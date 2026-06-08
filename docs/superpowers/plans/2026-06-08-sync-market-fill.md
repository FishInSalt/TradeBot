# 市价单同步成交 + 开仓/SL/TP 动作拆开 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** sim 市价单（开仓 + 平仓）从「异步 → 下一 cycle 处理」改为 `create_order` 内同步结算并返回 `FillEvent`，让 agent 在同一个 warm cycle 内完成「进场 → 设 SL/TP」整笔决策。

**Architecture:** sim 层 `create_order` 的 market 分支直接结算（取 `_latest_ticker` 价 → 占用/释放保证金 → 建/平仓 → 返回 `FillEvent`），删除市价单的 pending 排队 / `_process_tick` 撮合 / FillEvent→conditional 触发。工具层按 `isinstance(result, FillEvent)` 分派同步回执 vs 旧异步回执（OKX deferred 仍返 `Order`）。limit/stop/take_profit 的异步 pending 机制**完全不变**。persona + 两个 wrapper docstring 同步改写（LLM 可见通道与新流程对齐）。

**Tech Stack:** Python 3 / asyncio / SQLAlchemy (async) / pytest / pydantic-ai (griffe docstring → tool_def.description)。

**Spec:** `docs/superpowers/specs/2026-06-08-sync-market-fill-design.md`（方案 A，已两轮审查 + 第三轮 5 议题落地）。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `src/integrations/exchange/simulated.py` | sim 撮合/结算 | `create_order` market 分支同步化；`_fill_market_open`/`_fill_market_close` 改同步签名 + 直接占用 + raise；删 `_execute_market_fill` + `_process_tick` 市价撮合分支；full-close 显式撤 OCO + 清告警 |
| `src/integrations/exchange/base.py` | `BaseExchange` 接口 | `create_order` 返回类型注解 → `Order \| FillEvent` + docstring |
| `src/agent/tools_execution.py` | exchange 无关工具实现 | `_record_action` 加 4 字段 + 新 `_record_order_filled`；`open_position`/`close_position` 按返回类型分派 |
| `src/agent/trader.py` | @tool wrapper（LLM docstring 通道） | `open_position`/`close_position` docstring 改写为同步语义 |
| `src/agent/persona.py` | system prompt（layer1 Cross-Tool Behavior） | Fill timing / Open fill / Close fill 三条改写 |
| `src/cli/display.py` | 执行类回执渲染 | `_EXECUTION_SUCCESS_PREFIXES` open/close → tuple（加 `Filled:`/`Closed`）；`_summarize_open_position`/`_summarize_close_position` 加同步前缀分支 |
| `tests/test_simulated_exchange.py` 等 | 测试 | 迁移异步市价测试 → 同步；新增同步/G1/reject/ledger/flip 测试 |

**不动**（spec §5.5 / §6.2）：`_pending_orders` / `_process_tick`（limit/stop/tp/liquidation 分支）/ `_matching_loop` / `_dispatch_fill_event` / 冻结保证金 / `_close_position_core` / `okx.py` 实盘路径。

---

## Task 1: Sim — 市价单同步结算（核心）

把 sim 市价路径从异步两段式改为 `create_order` 内同步完成。这是一次「改 `create_order` 返回类型」的原子变更——会立即打破 `test_simulated_exchange.py` 里所有「create_order(market) → process_tick → 断言」的异步测试，故本 task 在同一 commit 内**同时**完成 impl + 迁移受影响测试 + 新增同步测试，确保提交时该文件全绿。

**Files:**
- Modify: `src/integrations/exchange/simulated.py`（`create_order` 229-269 / `_fill_market_open` 312-376 / `_fill_market_close` 378-413 / 删 `_execute_market_fill` 415-420 / `_process_tick` 市价分支 649-657）
- Test: `tests/test_simulated_exchange.py`

- [ ] **Step 1: 写新同步行为测试（先失败）**

在 `tests/test_simulated_exchange.py` 顶部已 `from src.integrations.exchange.base import FillEvent`。追加：

```python
async def test_market_buy_opens_long_sync():
    """市价买单同步成交：create_order 直接返回 FillEvent，仓位即刻存在，无 pending/frozen。"""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert isinstance(fill, FillEvent)
    assert fill.fill_price == 95010.0          # ask（buy 吃 ask）
    assert fill.pnl is None                      # 开仓
    assert fill.is_full_close is False
    assert fill.trigger_reason == "market"
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1 and positions[0].side == "long"
    assert positions[0].contracts == 0.001
    balance = await ex.fetch_balance()
    margin = 95010.0 * 0.001 / 3
    assert balance.used_usdt == pytest.approx(margin)
    assert ex._pending_orders == []              # 不进 pending 队列
    assert ex._frozen_usdt == 0.0                # 无冻结


async def test_market_sell_opens_short_sync():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 2
    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert isinstance(fill, FillEvent)
    assert fill.fill_price == 94990.0          # bid（sell 吃 bid）
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert positions[0].side == "short"


async def test_market_close_sync_returns_realized_pnl():
    """市价平仓同步：返回 FillEvent 带 pnl + entry_price + is_full_close。"""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)   # 开 long @ ask 95010
    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)  # 平 @ bid 94990
    assert isinstance(fill, FillEvent)
    assert fill.fill_price == 94990.0
    assert fill.pnl is not None
    assert fill.entry_price == 95010.0          # 平仓前 weighted entry
    assert fill.is_full_close is True
    assert await ex.fetch_positions("BTC/USDT:USDT") == []
    assert ex._pending_orders == []


async def test_market_open_insufficient_balance_rejects():
    """余额不足 → explicit reject，状态不变。"""
    ex = _make_exchange(initial_balance=1.0)
    ex._leverage["BTC/USDT:USDT"] = 1
    with pytest.raises(ValueError, match="Insufficient balance"):
        await ex.create_order("BTC/USDT:USDT", "buy", "market", 1.0)
    assert await ex.fetch_positions("BTC/USDT:USDT") == []
    assert ex._free_usdt == 1.0


async def test_fill_market_open_reverse_conflict_raises():
    """防御性 guard：对冲突仓位直接调 _fill_market_open → explicit reject（不 silent None）。"""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="short", contracts=0.001, entry_price=95000.0, leverage=3)
    with pytest.raises(ValueError, match="existing short position"):
        ex._fill_market_open("oid", "BTC/USDT:USDT", "buy", 0.001, 3, ex._latest_ticker)


async def test_sync_full_close_cancels_orphans_and_clears_alerts():
    """G1：同步全平 → 撤孤儿 SL/TP（_pending_orders）+ 清 price-level 告警，两套机制都生效。"""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)            # 开 long
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)  # 挂 stop
    ex.add_price_level_alert(99000.0, "above", "BTC/USDT:USDT", "resistance")
    assert any(o.order_type == "stop" for o in ex._pending_orders)
    assert len(ex._price_level_alerts) == 1

    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)    # 全平
    assert fill.is_full_close is True
    assert not any(o.order_type == "stop" for o in ex._pending_orders)         # 孤儿单已撤
    assert ex._price_level_alerts == []                                        # 告警已清


async def test_sync_flip_does_not_mistrigger_old_orphan():
    """G1+flip 回归：平 long → 同步反向开 short 后，旧 long-stop 不得残留误平新仓。"""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)            # 开 long
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)  # long-stop
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)           # 全平（撤孤儿）
    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)    # 同 flip：反向开 short
    assert fill.position_side == "short"
    # 旧 long-stop 不应残留
    assert not any(o.order_type == "stop" and o.position_side == "long"
                   for o in ex._pending_orders)
```

- [ ] **Step 2: 运行新测试，确认失败**

Run: `pytest tests/test_simulated_exchange.py -k "sync or reverse_conflict or flip or insufficient_balance" -v`
Expected: FAIL（现 `create_order(market)` 返回 `Order(status="open")` 而非 `FillEvent`）

- [ ] **Step 3: 改 `create_order` 的 market 分支为同步结算**

把 `simulated.py` 当前 market 分支（229-269，`if order_type == "market": ...` 整块到 `return Order(...)`）替换为：

```python
            if order_type == "market":
                if self._latest_ticker is None:
                    raise RuntimeError("No ticker data available")
                ticker = self._latest_ticker
                is_close = self._is_close_order(symbol, side)
                order_id = str(uuid.uuid4())

                if is_close:
                    fill = self._fill_market_close(order_id, symbol, side, amount, ticker)
                    # G1：full close → 撤孤儿 SL/TP（内存）+ 清 price-level 告警。
                    # 同步路径绕开 _process_tick，这两件善后都不会自愈（spec §6.1 G1）。
                    if fill.is_full_close:
                        self._cancel_orphaned_orders()
                        self._clear_stale_alerts_for_full_close(fill)
                else:
                    leverage = self._leverage.get(symbol, 1)
                    fill = self._fill_market_open(order_id, symbol, side, amount, leverage, ticker)

                # G3：直接写一行 closed SimOrder（市价单不再有 open 中间态）。
                # _persist_state 同时 upsert 余额/仓位 + DB 撤孤儿（step 3b）。
                if self._db_engine:
                    closed_order = Order(
                        id=order_id, symbol=symbol, side=side, order_type="market",
                        amount=fill.amount, price=fill.fill_price,
                        status="closed", fee=fill.fee,
                    )
                    await self._persist_state(new_orders=[(closed_order, fill.position_side)])
                return fill
```

- [ ] **Step 4: 改 `_fill_market_open` 为同步签名 + 直接占用 + raise**

把 `_fill_market_open`（312-376）整体替换为：

```python
    def _fill_market_open(
        self, order_id: str, symbol: str, side: str,
        amount: float, leverage: int, ticker: Ticker,
    ) -> FillEvent:
        """Synchronously settle a market open. Direct margin occupation (no
        prior freeze). Raises ValueError on reverse/leverage conflict or
        insufficient balance (explicit reject, per tool-design principle 1)."""
        pos = self._positions.get(symbol)
        position_side = "long" if side == "buy" else "short"
        if pos is not None and pos.side != position_side:
            raise ValueError(
                f"Cannot open {position_side}: existing {pos.side} position. Close it first."
            )
        if pos is not None and pos.leverage != leverage:
            raise ValueError(
                f"Leverage mismatch: order {leverage}x vs position {pos.leverage}x. "
                f"Close position first."
            )
        fill_price = ticker.ask if side == "buy" else ticker.bid
        actual_margin = (fill_price * self._base_qty(amount)) / leverage
        actual_fee = fill_price * self._base_qty(amount) * self._fee_rate
        actual_cost = actual_margin + actual_fee
        if self._free_usdt < actual_cost:
            raise ValueError(
                f"Insufficient balance: need {actual_cost:.2f}, have {self._free_usdt:.2f}"
            )
        # Direct occupation (sync: estimate == actual, no *1.002 buffer needed)
        self._used_usdt += actual_margin
        self._free_usdt -= actual_cost
        self._free_usdt = round(self._free_usdt, 8)
        self._used_usdt = round(self._used_usdt, 8)

        if pos is not None and pos.side == position_side:
            new_contracts = pos.contracts + amount
            pos.entry_price = (pos.entry_price * pos.contracts + fill_price * amount) / new_contracts
            pos.contracts = new_contracts
            pos.updated_at = datetime.now(timezone.utc)
        else:
            self._positions[symbol] = _Position(
                side=position_side, contracts=amount,
                entry_price=fill_price, leverage=leverage,
            )
        self._leverage[symbol] = leverage

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.info(f"Market open filled (sync): {side} {amount} {symbol} @ {fill_price:.2f}")
        return FillEvent(
            order_id=order_id, symbol=symbol, side=side,
            position_side=position_side, trigger_reason="market",
            fill_price=fill_price, amount=amount, fee=actual_fee,
            pnl=None, timestamp=now_ms, is_full_close=False,
        )
```

- [ ] **Step 5: 改 `_fill_market_close` 为同步签名 + raise**

把 `_fill_market_close`（378-413）整体替换为：

```python
    def _fill_market_close(
        self, order_id: str, symbol: str, side: str, amount: float, ticker: Ticker,
    ) -> FillEvent:
        """Synchronously settle a market close (reuses _close_position_core).
        Raises ValueError if no position (sync: caller checked is_close, so this
        is a defensive backstop)."""
        pos = self._positions.get(symbol)
        if pos is None:
            raise ValueError(f"No {symbol} position to close")
        actual_amount = min(amount, pos.contracts)
        fill_price = ticker.bid if pos.side == "long" else ticker.ask
        position_side = pos.side
        captured_entry = pos.entry_price  # capture BEFORE _close_position_core may pop pos
        pnl, fee, _ = self._close_position_core(
            symbol, pos.side, actual_amount, fill_price, pnl_cap=True,
        )
        is_full_close = symbol not in self._positions
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.info(
            f"Market close filled (sync): {side} {actual_amount} {symbol} @ {fill_price:.2f}, "
            f"pnl={pnl:.4f}, fee={fee:.4f}"
        )
        return FillEvent(
            order_id=order_id, symbol=symbol, side=side,
            position_side=position_side, trigger_reason="market",
            fill_price=fill_price, amount=actual_amount, fee=fee,
            pnl=pnl, timestamp=now_ms,
            is_full_close=is_full_close, entry_price=captured_entry,
        )
```

- [ ] **Step 6: 删 `_execute_market_fill` + `_process_tick` 市价撮合分支**

删除 `_execute_market_fill`（415-420 整个方法）。

在 `_process_tick` 删除 step 0（649-657）整块：

```python
            # 0. Match pending market orders (new — before liquidation)
            market_orders = [o for o in self._pending_orders if o.order_type == "market"]
            for order in market_orders:
                fill = self._execute_market_fill(order, ticker)
                if fill is None:
                    cancelled_order_ids.append(order.id)
                    continue
                filled_order_ids.append(order.id)
                triggered.append(fill)
```

（liquidation / conditional / limit 分支 step 1-5 保留不动。）

- [ ] **Step 7: 给 `has_pending_market_order` 加死分支注释（G5，不删）**

在 `has_pending_market_order`（838）docstring 末尾补一句：

```python
    def has_pending_market_order(self, symbol: str, side: str | None = None) -> bool:
        """Check for pending market orders matching symbol and optional side.

        NOTE: sim market orders settle synchronously in create_order and never
        enter _pending_orders, so this is always False for sim market (dead
        branch). Kept for the OKX async path (deferred) which may still pend.
        """
```

- [ ] **Step 8: 运行新测试，确认通过**

Run: `pytest tests/test_simulated_exchange.py -k "sync or reverse_conflict or flip or insufficient_balance" -v`
Expected: PASS

- [ ] **Step 9: 迁移受影响的异步市价测试（转换规则 + 全绿）**

转换规则（canonical）——凡「`create_order(market)` 后再 `_process_tick` 才成交」的断言改为「`create_order(market)` 直接得 `FillEvent`」：

```python
# 旧（异步）：
order = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
assert order.status == "open"
await ex._process_tick(_tick())          # ← 删除
positions = await ex.fetch_positions("BTC/USDT:USDT")

# 新（同步）：
fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
assert isinstance(fill, FillEvent)
assert fill.fill_price == 95010.0        # ask（不再是 process_tick 的 _tick() 价）
positions = await ex.fetch_positions("BTC/USDT:USDT")
```

要点：
- 成交价从 `_tick()` 传入价改为 `_make_exchange` 种子 ticker 的 ask/bid（buy→ask 95010 / sell→bid 94990）。
- 删除 `assert order.status == "open"` / `assert order.price is None` / 市价单 frozen 断言（同步无冻结）。
- 直接调旧 `_fill_market_open(order: _PendingOrder, ...)`/`_fill_market_close`/`_execute_market_fill` 的单测：删除或改为新签名（见 Step 1 的 `test_fill_market_open_reverse_conflict_raises`）。
- 断言「市价单进 `_pending_orders`」/「市价单 process_tick 撮合」的测试：删除。
- limit/stop/take_profit/liquidation 的 process_tick 测试**不动**（异步保留）。

Run（迭代修复至全绿）: `pytest tests/test_simulated_exchange.py -v`
Expected: PASS（全绿）

- [ ] **Step 10: 全套发现式迁移受影响的市价测试**

不写死文件清单（易漏）——跑**全套**，按 Step 9 规则修每个因同步化失败的市价测试，并把所有改动的测试文件纳入本 commit（守"每 commit 全绿"）。

Run: `pytest -q 2>&1 | tail -40`

已知必受影响（实测：直接或经 `_advance`→`_process_tick` 让市价单「在 advance tick 价成交」）：
- `tests/test_sim_mark_price.py`（4 个市价开仓经 `_advance(@价)` 成交 → 同步化后改在 **seed ticker 价**即时成交，entry 价错位）。修法 = 在 `create_order(market)` **之前**把 `ex._latest_ticker` 预置到目标 entry 价（如 `_tick(last=50000, bid=50000, ask=50000)`）+ `ex._latest_mark_price`，再用 `_advance` 只推进 mark 查 uPnL/清算。
- `tests/test_simulated_cs_kernel.py`（`test_unrealized_pnl_scales_with_cs` 已先预置 ticker→entry，大概率仍绿；其余直接注入 `_positions` 调 `_close_position_core`，不受影响——若仍红按同规则修）。

已知**不受影响**（实测，无需改）：`test_fact_only_wordlist` / `test_tool_enhancement` / `test_tools`（用 mock/fake exchange，非真实 `SimulatedExchange`）；`test_v_order_lifecycle` / `test_storage`（`create_order` 调用数=0，走 `_sim_fixtures` 直插 DB 行）。

Run（迭代修复至全绿）: `pytest -q`
Expected: PASS（全绿）

- [ ] **Step 11: Commit**

```bash
# git add 所有本 task 触碰的文件（用 `git status` 核对，勿漏 Step 10 迁移的）。
# 至少含：
git add src/integrations/exchange/simulated.py \
        tests/test_simulated_exchange.py tests/test_sim_mark_price.py \
        tests/test_alert_lifecycle.py tests/test_order_sizing_cs.py tests/test_exchange.py
# test_simulated_cs_kernel.py 若 Step 10 实测需改则一并 add；test_v_order_lifecycle/test_storage 实测不改。
git commit -m "feat(sim): market orders settle synchronously in create_order

create_order market 分支同步结算并返回 FillEvent（开/平仓即时完成、撤孤儿单+清告警），
删除市价单 pending 排队 / _process_tick 撮合 / _execute_market_fill。limit/stop/tp 异步不变。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Sim — G3 账本持久化测试（带真实 DB）

验证同步市价开/平仓后 `sim_orders` 立即有 closed 行（喂 `fetch_closed_orders` + `_sim_metrics`），不经 `_process_tick`。

**Files:**
- Test: `tests/test_simulated_exchange.py`

- [ ] **Step 1: 写账本测试（先失败/或直接验证）**

```python
async def test_sync_market_writes_closed_sim_order(db_engine):
    """G3：同步市价开/平仓后 sim_orders 各有一行 status='closed'，filled_price/fee/filled_at 齐全。"""
    from sqlalchemy import select
    from src.storage.database import get_session
    from src.storage.models import SimOrder
    from src.integrations.exchange.base import FillEvent, Ticker
    from src.integrations.exchange.simulated import SimulatedExchange
    from tests._sim_fixtures import make_session
    from unittest.mock import MagicMock

    sid = await make_session(db_engine, initial_balance=100.0, fee_rate=0.0005)
    config = MagicMock(); config.fee_rate = 0.0005
    ex = SimulatedExchange(config=config, db_engine=db_engine,
                           session_id=sid, symbol="BTC/USDT:USDT")
    ex._free_usdt = 100.0; ex._used_usdt = 0.0; ex._frozen_usdt = 0.0
    ex._positions = {}; ex._pending_orders = []; ex._leverage = {"BTC/USDT:USDT": 3}
    ex._latest_ticker = Ticker(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0,
                               ask=95010.0, high=96000.0, low=94000.0,
                               base_volume=1000.0, timestamp=1712534400000)
    ex._latest_mark_price = 95000.0

    open_fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    close_fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)

    async with get_session(db_engine) as s:
        rows = (await s.execute(
            select(SimOrder).where(SimOrder.session_id == sid)
                            .where(SimOrder.order_type == "market")
        )).scalars().all()
    by_id = {r.order_id: r for r in rows}
    for fill in (open_fill, close_fill):
        row = by_id[fill.order_id]
        assert row.status == "closed"
        assert row.filled_price == fill.fill_price
        assert row.fee == fill.fee
        assert row.filled_at is not None

    closed = await ex.fetch_closed_orders("BTC/USDT:USDT")
    assert len([o for o in closed if o.order_type == "market"]) == 2
```

- [ ] **Step 2: 运行**

Run: `pytest tests/test_simulated_exchange.py::test_sync_market_writes_closed_sim_order -v`
Expected: PASS（Task 1 的 `_persist_state(new_orders=...)` 已写 closed 行）。若 `make_session` 不接受 `initial_balance`/`fee_rate` kwarg，按 `tests/_sim_fixtures.py:42` 实际签名调整（已支持）。

- [ ] **Step 3: Commit**

```bash
git add tests/test_simulated_exchange.py
git commit -m "test(sim): G3 ledger — sync market writes closed sim_orders row

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: BaseExchange — `create_order` 返回类型 `Order | FillEvent`

接口注解明示市价分支返回 `FillEvent`、limit/stop/tp 返回 `Order` 的异构（spec §5.1）。

**Files:**
- Modify: `src/integrations/exchange/base.py`（`create_order` 抽象方法签名 + docstring）
- Modify: `src/integrations/exchange/simulated.py`（`create_order` 返回注解）

- [ ] **Step 1: 改 base.py 抽象签名（保持单行 `...` 体）**

现 `BaseExchange.create_order`（`base.py:133-142`）是 `@abstractmethod ... -> Order: ...` 单行体，与同区**所有**抽象方法（`fetch_balance`/`fetch_positions`/… 均 `... : ...`）风格一致。**只改返回注解**，异构语义用方法上方 `#` 注释表达（不引入 docstring+`raise NotImplementedError`，否则破坏该区一致风格）：

```python
    # create_order return type is heterogeneous by order_type:
    #   market → settles synchronously (sim) → FillEvent (actual fill_price/fee/
    #            pnl/entry_price); callers dispatch on isinstance(result, FillEvent).
    #   limit / stop / take_profit → Order (status='open'); fills later (async),
    #            notifies via the fill callback.
    # (OKX live path, deferred, still returns Order for market — CLAUDE.md Tier 3;
    #  the FillEvent branch is sim-only for now.)
    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> "Order | FillEvent": ...
```

- [ ] **Step 2: 改 simulated.py `create_order` 返回注解**

`simulated.py:216` 把 `) -> Order:` 改为 `) -> "Order | FillEvent":`（`FillEvent` 已 import）。

- [ ] **Step 3: 运行 import/类型相关测试**

Run: `pytest tests/test_exchange.py tests/test_simulated_exchange.py -q`
Expected: PASS（纯注解，无行为变化）

- [ ] **Step 4: Commit**

```bash
git add src/integrations/exchange/base.py src/integrations/exchange/simulated.py
git commit -m "refactor(exchange): create_order return type Order | FillEvent

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Tool 层 — `_record_action` 加 4 字段 + `_record_order_filled`

工具层记 `order_filled` 需 `fee/amount/entry_price/trigger_reason`（spec §5.1：`_record_action` 现缺，漏则 `metrics.total_fees` 静默漏计 + 违 `amount` 不变量）。

**Files:**
- Modify: `src/agent/tools_execution.py`（`_record_action` 21-52 + 新增 `_record_order_filled`）
- Test: `tests/test_tools_execution.py`

- [ ] **Step 1: 写测试（先失败）**

在 `tests/test_tools_execution.py` 追加：

```python
async def test_record_order_filled_writes_all_fields(db_engine):
    """_record_order_filled 把 FillEvent 的 fee/amount/entry_price/trigger_reason 全写入。"""
    from sqlalchemy import select
    from src.storage.database import get_session
    from src.storage.models import TradeAction
    from src.integrations.exchange.base import FillEvent
    from src.agent.tools_execution import _record_order_filled
    from tests._sim_fixtures import make_session
    from unittest.mock import MagicMock

    sid = await make_session(db_engine)
    deps = MagicMock()
    deps.db_engine = db_engine
    deps.session_id = sid
    deps.cycle_id = "cyc1"
    deps.symbol = "BTC/USDT:USDT"
    fill = FillEvent(
        order_id="oid1", symbol="BTC/USDT:USDT", side="sell", position_side="long",
        trigger_reason="market", fill_price=82000.0, amount=0.1, fee=4.1,
        pnl=200.0, timestamp=1712534400000, is_full_close=True, entry_price=80000.0,
    )
    await _record_order_filled(deps, fill)

    async with get_session(db_engine) as s:
        row = (await s.execute(
            select(TradeAction).where(TradeAction.order_id == "oid1")
                               .where(TradeAction.action == "order_filled")
        )).scalar_one()
    assert row.fee == 4.1
    assert row.amount == 0.1
    assert row.entry_price == 80000.0
    assert row.trigger_reason == "market"
    assert row.pnl == 200.0
    assert row.cycle_id == "cyc1"
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_tools_execution.py::test_record_order_filled_writes_all_fields -v`
Expected: FAIL（`_record_order_filled` 不存在 / `_record_action` 无 fee 等参数）

- [ ] **Step 3: 扩展 `_record_action` 签名 + TradeAction 字段**

把 `_record_action`（21-52）的签名与 `TradeAction(...)` 构造改为：

```python
async def _record_action(deps: TradingDeps, action: str, *,
                          order_id: str | None = None,
                          alert_id: str | None = None,
                          side: str | None = None, price: float | None = None,
                          pnl: float | None = None, reasoning: str | None = None,
                          fee: float | None = None, amount: float | None = None,
                          entry_price: float | None = None,
                          trigger_reason: str | None = None) -> None:
    """写入一条 TradeAction 记录。写入失败不影响 tool 返回（容错）。

    `*` 之后全 kwarg-only。fee/amount/entry_price/trigger_reason 供 order_filled
    行使用（同步市价路径，per spec §5.1）；非 fill 行留 None。
    """
    if deps.db_engine is None:
        return
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    try:
        async with get_session(deps.db_engine) as session:
            session.add(TradeAction(
                session_id=deps.session_id,
                cycle_id=deps.cycle_id,
                action=action,
                order_id=order_id,
                alert_id=alert_id,
                symbol=deps.symbol,
                side=side,
                price=price,
                pnl=pnl,
                reasoning=reasoning,
                fee=fee,
                amount=amount,
                entry_price=entry_price,
                trigger_reason=trigger_reason,
            ))
            await session.commit()
    except Exception:
        logger.warning("Failed to record TradeAction", exc_info=True)
```

- [ ] **Step 4: 新增 `_record_order_filled` helper**

紧跟 `_record_action` 之后插入：

```python
async def _record_order_filled(deps: TradingDeps, fill) -> None:
    """从同步 FillEvent 记一条 order_filled TradeAction（sim 同步市价路径）。

    字段集与 app._record_action_from_fill 对齐，使 metrics.total_fees /
    models amount-invariant / trigger_reason 分类在同步路径下仍成立（spec §5.1）。
    """
    await _record_action(
        deps, action="order_filled", order_id=fill.order_id,
        side=fill.position_side, price=fill.fill_price, pnl=fill.pnl,
        fee=fill.fee, amount=fill.amount, entry_price=fill.entry_price,
        trigger_reason=fill.trigger_reason,
        reasoning=f"(exchange: {fill.trigger_reason} order filled @ {fill.fill_price:.2f})",
    )
```

- [ ] **Step 5: 运行，确认通过**

Run: `pytest tests/test_tools_execution.py::test_record_order_filled_writes_all_fields -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_execution.py tests/test_tools_execution.py
git commit -m "feat(tools): _record_action +fee/amount/entry_price/trigger_reason + _record_order_filled

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Tool 层 — `open_position` 同步回执分派

**Files:**
- Modify: `src/agent/tools_execution.py`（顶部 import + `open_position` 93-111）
- Test: `tests/test_tools_execution.py`

- [ ] **Step 1: 写测试（先失败）**

```python
async def test_open_position_sync_fill_receipt():
    """create_order 返 FillEvent → open_position 返同步回执（含 fill_price/fee + UNPROTECTED 提示）。"""
    from src.integrations.exchange.base import FillEvent
    deps = _make_open_deps(order_id="op1")   # 见 test 文件既有 open deps 工厂（220 行附近）
    deps.exchange.create_order = AsyncMock(return_value=FillEvent(
        order_id="op1", symbol="BTC/USDT:USDT", side="buy", position_side="long",
        trigger_reason="market", fill_price=80050.0, amount=0.1, fee=4.0,
        pnl=None, timestamp=1712534400000, is_full_close=False,
    ))
    deps.db_engine = None   # 跳过 DB 记账，只测回执
    from src.agent.tools_execution import open_position
    out = await open_position(deps, "long", 50.0, 3, reasoning="breakout")
    assert out.startswith("Filled:")
    assert "80050.00" in out
    assert "UNPROTECTED" in out
    assert "op1" in out


async def test_open_position_async_order_receipt_unchanged():
    """create_order 返 Order（OKX 路径）→ 维持旧异步回执。"""
    deps = _make_open_deps(order_id="op2")   # 既有工厂默认 create_order 返 Order
    deps.db_engine = None
    from src.agent.tools_execution import open_position
    out = await open_position(deps, "long", 50.0, 3, reasoning="breakout")
    assert "You will be notified when filled." in out
```

（`_make_open_deps` = 该文件 220 行附近既有的 open_position deps 工厂；若名称不同按实际命名调用。默认 `create_order` 返 `Order`。）

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_tools_execution.py -k "open_position_sync or async_order_receipt" -v`
Expected: FAIL（现 `open_position` 对 FillEvent 取 `.id` 会 AttributeError）

- [ ] **Step 3: 顶部加 FillEvent import**

`tools_execution.py` 第 9 行 `from src.services.tool_call_recorder import note_biz_error` 之后加：

```python
from src.integrations.exchange.base import FillEvent
```

- [ ] **Step 4: 改 `open_position` 分派**

把 `open_position` 的 93-111（`await deps.exchange.set_leverage(...)` 到末尾 `return (...)`）替换为：

```python
    await deps.exchange.set_leverage(deps.symbol, leverage)
    order_side = "buy" if side == "long" else "sell"
    result = await deps.exchange.create_order(
        symbol=deps.symbol, side=order_side, order_type="market", amount=quantity
    )

    if isinstance(result, FillEvent):
        # 同步成交（sim）：记 intent + order_filled，返真实回执 + UNPROTECTED 提示。
        await _record_action(
            deps, action="open_position", order_id=result.order_id,
            side=side, reasoning=reasoning,
        )
        await _record_order_filled(deps, result)
        fill_notional = result.fill_price * result.amount * contract_size
        return (
            f"Filled: {side} {result.amount:.6f} @ {result.fill_price:.2f}, {leverage}x "
            f"| ID: {result.order_id}\n"
            f"Entry fee: -{result.fee:.2f} USDT (notional {fill_notional:,.2f})\n"
            f"Position OPEN — UNPROTECTED. Set stop loss and take profit now."
        )

    # 异步（OKX，deferred）：维持 submit-and-notify。
    await _record_action(
        deps, action="open_position", order_id=result.id,
        side=side, reasoning=reasoning,
    )
    notional = ticker.last * quantity * contract_size
    est_entry_fee = notional * deps.fee_rate
    return (
        f"Order submitted: {side} {quantity:.6f} @ ~{ticker.last:.2f}, {leverage}x | ID: {result.id}\n"
        f"Est. entry fee: ~-{est_entry_fee:.2f} USDT "
        f"(notional ~{notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
        f"You will be notified when filled."
    )
```

- [ ] **Step 5: 运行，确认通过**

Run: `pytest tests/test_tools_execution.py -k "open_position" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_execution.py tests/test_tools_execution.py
git commit -m "feat(tools): open_position dispatches sync FillEvent vs async Order receipt

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Tool 层 — `close_position` 同步回执分派（realized PnL + round-trip）

**Files:**
- Modify: `src/agent/tools_execution.py`（`close_position` 146-170）
- Test: `tests/test_tools_execution.py`

- [ ] **Step 1: 写测试（先失败）**

```python
async def test_close_position_sync_realized_pnl_receipt():
    """create_order 返 FillEvent → close_position 返 realized PnL（gross + round-trip net）。"""
    from src.integrations.exchange.base import FillEvent
    deps = _make_deps(order_id="c1", entry_price=80000.0)   # 既有通用 deps 工厂（_make_deps :11；亦用于 close 测试）
    deps.db_engine = None
    # fetch_positions 返一个 long 0.1 @ 80000；create_order 同步平 @ 82000
    deps.exchange.create_order = AsyncMock(return_value=FillEvent(
        order_id="c1", symbol="BTC/USDT:USDT", side="sell", position_side="long",
        trigger_reason="market", fill_price=82000.0, amount=0.1, fee=4.1,
        pnl=200.0, timestamp=1712534400000, is_full_close=True, entry_price=80000.0,
    ))
    from src.agent.tools_execution import close_position
    out = await close_position(deps, reasoning="TP hit")
    assert out.startswith("Closed")
    assert "Realized PnL" in out
    assert "+200.00" in out          # gross
    # round-trip net = -entry_fee(80000*0.1*1.0*0.0005=4.0) + 200.0 - 4.1 = 191.90
    assert "+191.90" in out
    # 同步路径不调 register_close_order_entry
    deps.exchange.register_close_order_entry.assert_not_called()
```

（`_make_deps` 默认 `fetch_positions` 返 long 0.1 @ entry_price，`get_contract_size`=1.0，`fee_rate`=0.0005——见文件 12-50 行；如默认值不同按实际调整断言数值。）

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_tools_execution.py::test_close_position_sync_realized_pnl_receipt -v`
Expected: FAIL

- [ ] **Step 3: 改 `close_position` 分派**

把 `close_position` 的 146-170（`order_ids = []` 到末尾 `return (...)`）替换为：

```python
    order_ids = []
    sync_fills = []
    for p in positions:
        order_side = "sell" if p.side == "long" else "buy"
        result = await deps.exchange.create_order(
            symbol=deps.symbol, side=order_side, order_type="market",
            amount=p.contracts,
            params={"reduceOnly": True},  # OKX echoes info.reduceOnly=true in fill event
        )
        if isinstance(result, FillEvent):
            # 同步平仓：sim 在 _fill_market_close 已直接 capture entry，无需 register（G9）。
            await _record_action(
                deps, action="close_position", order_id=result.order_id,
                side=p.side, reasoning=reasoning,
            )
            await _record_order_filled(deps, result)
            order_ids.append(result.order_id)
            sync_fills.append(result)
        else:
            deps.exchange.register_close_order_entry(result.id, p.entry_price)
            await _record_action(
                deps, action="close_position", order_id=result.id,
                side=p.side, reasoning=reasoning,
            )
            order_ids.append(result.id)

    if sync_fills:
        # 同步：realized PnL 即时已知。round-trip net per fill =
        # -entry_fee + realized_pnl - exit_fee（entry_fee 带 contract_size 因子，
        # 与 app.py IMPORTANT EVENT 渲染 + close 估算约定一致）。
        total_realized = sum(f.pnl for f in sync_fills if f.pnl is not None)
        total_exit_fee = sum(f.fee for f in sync_fills)
        total_entry_fee_actual = sum(
            (f.entry_price or 0.0) * f.amount * contract_size * deps.fee_rate
            for f in sync_fills
        )
        round_trip_net = -total_entry_fee_actual + total_realized - total_exit_fee
        return (
            f"Closed {len(sync_fills)} position(s) | IDs: {', '.join(order_ids)}\n"
            f"Realized PnL: {total_realized:+.2f} USDT (gross) / "
            f"{round_trip_net:+.2f} USDT (round-trip net)\n"
            f"Exit fee: -{total_exit_fee:.2f} USDT"
        )

    # 异步（OKX，deferred）：维持 submit-and-notify。
    return (
        f"Orders submitted: close {len(positions)} position(s) | IDs: {', '.join(order_ids)}\n"
        f"Est. exit fee: ~-{est_exit_fee:.2f} USDT "
        f"(notional ~{est_exit_notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
        f"Est. net PnL: ~{est_net_pnl:+.2f} USDT "
        f"(round-trip = entry fee ~-{total_entry_fee:.2f} "
        f"+ unrealized {total_unrealized:+.2f} "
        f"+ est. exit fee ~-{est_exit_fee:.2f})\n"
        f"You will be notified when filled."
    )
```

（`est_exit_fee` / `est_exit_notional` / `est_net_pnl` / `total_entry_fee` / `total_unrealized` 仍由 127-136 的估算块产出，供异步分支用；`contract_size` 在 128 行已取。）

- [ ] **Step 4: 运行，确认通过**

Run: `pytest tests/test_tools_execution.py -k "close_position" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_execution.py tests/test_tools_execution.py
git commit -m "feat(tools): close_position sync FillEvent → realized PnL + round-trip net

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: display.py — 同步回执前缀 + summary 解析（will-break 修复）

Task 5/6 把同步回执前缀改成 `Filled:` / `Closed`。`display.py` 的 `_EXECUTION_SUCCESS_PREFIXES`（`is_tool_error` 用于「成功 vs 业务拒绝」判定）+ `_summarize_open_position`/`_summarize_close_position` 两个正则仍只认旧前缀——不同步登记，成功的同步开/平仓会被**渲成 error + session-log 摘要退化**（spec §5.1 下游耦合 + `tools_execution.py:16-18` 注释警告）。现有 display 测试只覆盖旧异步前缀（保留→仍绿），全量回归抓不到，故必须显式修。

> 排序说明：本 task 紧跟 Task 5/6（确立新前缀）之后、persona/trader（Task 8/9）之前。Task 5/6 不破坏既有 display 测试（它们传**硬编码字符串**给 `is_tool_error`/summary，非真实工具输出），故 Task 5→6→7 全程绿。

**Files:**
- Modify: `src/cli/display.py`（`_EXECUTION_SUCCESS_PREFIXES` 306-308 / `_summarize_open_position` 174-178 / `_summarize_close_position` 181-187）
- Test: `tests/test_display_cycle.py`

- [ ] **Step 1: 写测试（先失败）**

在 `tests/test_display_cycle.py` 追加：

```python
def test_is_tool_error_sync_open_fill_not_error():
    """同步开仓回执 'Filled: ...' 判为成功（非业务拒绝）；旧异步前缀仍成功。"""
    from src.cli.display import is_tool_error
    assert not is_tool_error(
        "open_position", "Filled: long 0.050000 @ 84200.50, 3x | ID: op1",
        outcome="success")
    assert not is_tool_error(
        "open_position", "Order submitted: long 0.05 @ ~84200, 3x", outcome="success")


def test_is_tool_error_sync_close_not_error():
    from src.cli.display import is_tool_error
    assert not is_tool_error(
        "close_position", "Closed 1 position(s) | IDs: c1", outcome="success")
    assert not is_tool_error(
        "close_position", "Orders submitted: close 1 position(s) | IDs: x",
        outcome="success")


def test_summarize_sync_open_fill():
    from src.cli.display import _summarize_open_position
    out = _summarize_open_position("Filled: long 0.050000 @ 84200.00, 3x | ID: op1")
    assert "long" in out and "84,200" in out and "3x" in out


def test_summarize_sync_close():
    from src.cli.display import _summarize_close_position
    out = _summarize_close_position("Closed 2 position(s) | IDs: a, b")
    assert "2" in out
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_display_cycle.py -k "sync_open_fill or sync_close or summarize_sync" -v`
Expected: FAIL（旧前缀/正则不匹配 `Filled:` / `Closed`）

- [ ] **Step 3: `_EXECUTION_SUCCESS_PREFIXES` open/close 改 tuple**

把 `display.py:306-308`：

```python
_EXECUTION_SUCCESS_PREFIXES = {
    "open_position": "Order submitted:",
    "close_position": "Orders submitted:",
```

改为（旧前缀保留给 OKX-deferred 异步路径，新前缀加给同步路径）：

```python
_EXECUTION_SUCCESS_PREFIXES = {
    "open_position": ("Order submitted:", "Filled:"),
    "close_position": ("Orders submitted:", "Closed"),
```

（`is_tool_error` 337-352 已支持 tuple 多前缀，无需改。）

- [ ] **Step 4: 两个 summary 正则加同步分支**

`_summarize_open_position`（175）正则改为兼容两前缀：

```python
def _summarize_open_position(content: str) -> str:
    m = re.search(r"(?:Order submitted|Filled):\s*(\w+)\s+([\d.]+)\s*@\s*~?([\d.]+),\s*(\d+)x", content)
    if m:
        return f"{m.group(1)} {m.group(2)} @ ~${float(m.group(3)):,.0f}, {m.group(4)}x"
    return _fallback_summary(content)
```

`_summarize_close_position`（184）正则改为兼容 `close` / `Closed`：

```python
def _summarize_close_position(content: str) -> str:
    # "No positions to close." is a business rejection — is_tool_error catches it
    # before this parser runs, so no need to handle it here.
    m = re.search(r"[Cc]lose(?:d)?\s+(\d+)\s+position", content)
    if m:
        return f"Close {m.group(1)} position(s)"
    return _fallback_summary(content)
```

- [ ] **Step 5: 运行，确认通过 + 既有 display 测试不回归**

Run: `pytest tests/test_display_cycle.py -k "sync_open_fill or sync_close or summarize_sync" -v`
Expected: PASS

Run: `pytest tests/test_display_cycle.py -q`
Expected: PASS（`test_is_tool_error_execution_success` 等旧前缀断言仍绿）

- [ ] **Step 6: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "fix(display): recognize sync market receipts (Filled:/Closed) as success

回执前缀从 Order submitted:/Orders submitted: 扩到同步路径 Filled:/Closed —
_EXECUTION_SUCCESS_PREFIXES 改 tuple + 两 summary 正则加分支，
防成功的同步开/平仓被 is_tool_error 误判为业务拒绝 + 摘要退化。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: persona.py — Cross-Tool Behavior 改写 + drift-guard

`persona.py:105-107` 现指令与新流程**正面冲突**（"do not attempt in the same cycle"），且 persona 权重 > 工具回执，必改（spec §10）。

**Files:**
- Modify: `src/agent/persona.py`（`_build_layer1` 内 105-107）
- Test: `tests/test_persona.py`

- [ ] **Step 1: 写 drift-guard 测试（先失败）**

在 `tests/test_persona.py` 追加：

```python
def test_persona_market_sync_replaces_separate_trigger():
    """市价同步语义改写：不再含 'do not attempt in the same cycle' 类冲突措辞。"""
    from src.agent.persona import generate_system_prompt
    from src.config import PersonaConfig
    prompt = generate_system_prompt(PersonaConfig())
    low = prompt.lower()
    # 新措辞present
    assert "synchronous" in low or "fills synchronously" in low or "same cycle" in low
    assert "unprotected" in low or "set stop loss and take profit" in low
    # 旧冲突措辞absent
    assert "do not attempt in the same cycle" not in low
    assert "separate trigger" not in low
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_persona.py::test_persona_market_sync_replaces_separate_trigger -v`
Expected: FAIL

- [ ] **Step 3: 改写 persona 105-107 三条**

把 `persona.py` 的 105-107 三个 bullet 替换为：

```python
- **Fill timing**: Market orders (open_position / close_position) fill synchronously — the tool call returns the actual fill (price, fee, and realized PnL on close) in the same cycle. After opening, set stop loss and take profit immediately in the SAME cycle: the position already exists. Limit orders fill later — you will be notified when they fill.
- **Open fill response**: When woken by a limit-order fill (conditional trigger) that opened a position, set your stop loss and take profit. (Market opens no longer wake you — set SL/TP right after the synchronous open, using the thesis you just formed.)
- **Close fill response**: When woken by a fill that closed a position via a stop-loss or take-profit trigger, review the trade outcome: what worked, what didn't, what you'd do differently. A manual market close returns its outcome synchronously — reflect in the same cycle.
```

- [ ] **Step 4: 运行 drift-guard + 既有 persona 测试**

Run: `pytest tests/test_persona.py -v`
Expected: PASS。若既有 `test_prompt_contains_layer1_identity` 断言旧关键词（如 "fill" 仍在），保持绿；如断言到被删的旧措辞，按新文本更新。

- [ ] **Step 5: Commit**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "feat(persona): rewrite fill-timing for sync market (same-cycle SL/TP)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: trader.py — open/close docstring 改写 + drift-guard

LLM 实见的是 `trader.py` @tool wrapper docstring（griffe → `tool_def.description`，per memory `project_tool_docstring_llm_channel`）。

**Files:**
- Modify: `src/agent/trader.py`（`open_position` 498-512 / `close_position` 519-531 docstring）
- Test: `tests/test_trader_agent.py`

- [ ] **Step 1: 写 drift-guard 测试（先失败）**

在 `tests/test_trader_agent.py` 追加：

```python
def _tool_desc(name: str) -> str:
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    return agent._function_toolset.tools[name].tool_def.description


def test_open_position_docstring_sync_semantics():
    desc = _tool_desc("open_position").lower()
    assert "synchronous" in desc or "fills synchronously" in desc
    assert "same cycle" in desc
    assert "separate trigger" not in desc
    assert "not in the same cycle" not in desc


def test_close_position_docstring_sync_semantics():
    desc = _tool_desc("close_position").lower()
    assert "synchronous" in desc or "realized pnl" in desc
    assert "separate trigger" not in desc
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_trader_agent.py -k "docstring_sync_semantics" -v`
Expected: FAIL

- [ ] **Step 3: 改写 open_position docstring（498-512）**

```python
        """Open a new market-order position.

        The market order fills synchronously: this call returns the actual fill
        (price and fee) and the position exists immediately. Set stop loss and
        take profit in the SAME cycle right after — the position is UNPROTECTED
        until you do.

        Entry incurs taker fee = notional × fee_rate; the return reports the actual fee.

        Args:
            side: 'long' or 'short'.
            position_pct: percent of free balance to allocate (0-100).
            leverage: leverage multiplier (cannot be changed while holding position).
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 4: 改写 close_position docstring（519-531）**

```python
        """Close all open positions via market order.

        The close fills synchronously: this call returns the realized PnL
        (gross and round-trip net) in the same cycle. Reflect on the outcome
        right here — no separate fill notification follows.

        Close incurs taker fee on exit (included in the round-trip net).

        Args:
            reasoning: brief description of your decision logic (e.g., 'TP target hit', 'thesis invalidated').
        """
```

- [ ] **Step 5: 运行，确认通过**

Run: `pytest tests/test_trader_agent.py -k "docstring_sync_semantics" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "feat(trader): rewrite open/close docstrings for sync market semantics

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Integration — order_id 端到端链路 + 全量回归

**Files:**
- Test: `tests/test_simulated_exchange.py`（端到端链路）

- [ ] **Step 1: 写 order_id 链路测试（§9 🟡-3）**

```python
async def test_order_id_chain_open_to_view(db_engine):
    """SimOrder.order_id == FillEvent.order_id == order_filled.order_id，
    且 v_order_lifecycle.originated_cycle_id 能解析到发起 cycle。"""
    from sqlalchemy import text
    from src.integrations.exchange.base import Ticker
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.storage.database import get_session
    from src.storage.models import TradeAction
    from tests._sim_fixtures import make_session, make_cycle
    from unittest.mock import MagicMock

    sid = await make_session(db_engine, initial_balance=100.0, fee_rate=0.0005)
    await make_cycle(db_engine, sid, "cyc1")
    config = MagicMock(); config.fee_rate = 0.0005
    ex = SimulatedExchange(config=config, db_engine=db_engine, session_id=sid,
                           symbol="BTC/USDT:USDT")
    ex._free_usdt = 100.0; ex._used_usdt = 0.0; ex._frozen_usdt = 0.0
    ex._positions = {}; ex._pending_orders = []; ex._leverage = {"BTC/USDT:USDT": 3}
    ex._latest_ticker = Ticker(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0,
                               ask=95010.0, high=96000.0, low=94000.0,
                               base_volume=1000.0, timestamp=1712534400000)
    ex._latest_mark_price = 95000.0

    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    # 模拟工具层记 intent（open_position）+ order_filled，cycle_id=cyc1
    async with get_session(db_engine) as s:
        s.add(TradeAction(session_id=sid, cycle_id="cyc1", action="open_position",
                          order_id=fill.order_id, symbol="BTC/USDT:USDT", side="long"))
        s.add(TradeAction(session_id=sid, cycle_id="cyc1", action="order_filled",
                          order_id=fill.order_id, symbol="BTC/USDT:USDT", side="long",
                          price=fill.fill_price, fee=fill.fee, amount=fill.amount,
                          trigger_reason="market"))
        await s.commit()

    async with db_engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT originated_cycle_id FROM v_order_lifecycle WHERE order_id = :oid"),
            {"oid": fill.order_id})).first()
    assert row is not None
    assert row.originated_cycle_id == "cyc1"
```

- [ ] **Step 2: 运行新链路测试**

Run: `pytest tests/test_simulated_exchange.py::test_order_id_chain_open_to_view -v`
Expected: PASS

- [ ] **Step 3: 全量回归**

Run: `pytest -q`
Expected: PASS（全绿）。重点关注：
- limit/stop/take_profit/liquidation 的 process_tick + conditional 触发测试**仍绿**（未误伤共享机器）。
- `test_sim_metrics*` / `test_v_order_lifecycle` / `test_metrics` 绿（order_filled 字段 + cycle_id 变化无破坏）。
- `test_fact_only_wordlist` 绿（新回执文本不引入禁用评价词）。

修复任何遗漏的失败（多为 Task 1 未覆盖到的散落市价测试，按 Task 1 Step 9 规则处理）。

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: order_id end-to-end chain + full regression green (sync market fill)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Spec Coverage 自查

| Spec 段 | 覆盖 task |
|---|---|
| §5.1 同步结算 + 返回 FillEvent | Task 1 (Step 3-5) |
| §5.1 返回类型异构 Order\|FillEvent | Task 3 |
| §5.1 order_filled 工具层记账（字段缺口 + 双写） | Task 4 + Task 5/6 |
| §5.1 round-trip contract_size 因子 | Task 6 (Step 3) |
| §5.1 G3 SimOrder 账本 | Task 1 (Step 3) + Task 2 |
| §5.2 删 freeze/*1.002/pending/process_tick 市价分支/dispatch | Task 1 (Step 3-6) |
| §5.2 G5 has_pending_market_order 死分支注释 | Task 1 (Step 7) |
| §5.3 取价用 _latest_ticker | Task 1 (Step 4-5) |
| §6.1 G1 两套机制（撤 OCO + 清告警） | Task 1 (Step 3, test Step 1) |
| §6.3 flip 不误平 | Task 1 (test `test_sync_flip...`) |
| §7.3 reverse/leverage explicit reject | Task 1 (Step 4, test reverse_conflict) |
| §7.4-1 启动恢复（裸仓由 scheduler 首跑 cycle 覆盖，无专门对账） | —（无需 task，spec 已据此重写） |
| §5.1 回执前缀变更波及 display 渲染（will-break 下游耦合） | Task 7 |
| §9 order_id 端到端链路 | Task 10 |
| §9 回归（limit/stop/tp 仍绿） | Task 10 (Step 3) |
| §10 persona + 两 docstring 改写 + drift-guard | Task 8 + Task 9 |
| §6.1 G9 register_close_order_entry sim 冗余 | Task 6 (Step 3，同步路径不调) |

**Open decisions（§11，已定）**：①取价 `_latest_ticker`（Task 1）；②市价平仓纳入本 iter（Task 1/6）；③**不做专门启动对账**——裸仓由 `scheduler.start():58` 保证首跑 cycle 覆盖（spec §7.4-1/§11③ 已据此重写，原 reconcile task 已删），软告警 §7.4-2 仍候选。
