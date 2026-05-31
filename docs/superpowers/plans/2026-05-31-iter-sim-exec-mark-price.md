# sim 执行保真 iter-2：mark price 真实化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `SimulatedExchange` 用真实 OKX mark price 驱动清算触发判定与 unrealized PnL，消除"sim 用 bid/ask → 比真实 OKX 更易爆仓 / uPnL 带 spread 噪声"的语义偏差。

**Architecture:** 新增并行 `watch_mark_price` WS loop 维护 `self._latest_mark_price`（独立 `_mark_error_count`）；内核 3 处消费点改读 mark（`get_mark_price` / `_calc_unrealized_pnl` / 清算触发判定）；触发基准改 mark、强平成交价保持盘口 bid/ask；stop/TP 触发、市价成交、价格 alert 不动。mark 不走 `_process_tick`（生产由 WS loop 更新、测试经共享 `_advance` helper 同步），不持久化、不碰 DB schema。

**Tech Stack:** Python 3.13 / asyncio / `ccxt.pro` (ccxtpro.okx, `watchMarkPrice`/`fetchMarkPrice` 实测 True) / pytest / unittest.mock (AsyncMock)。

参照 spec：`docs/superpowers/specs/2026-05-31-sim-exec-mark-price-design.md`。

---

## File Structure

| 文件 | 责任 | 改动 |
|---|---|---|
| `src/integrations/exchange/simulated.py` | sim 撮合内核 | `__init__` 加 mark 状态；`get_mark_price`/`_calc_unrealized_pnl`/清算触发改 mark；`start()` seed + `_mark_loop` + `close()` cancel；docstring |
| `src/agent/tools_perception.py` | 感知层渲染 | `:397-402` 注释更新（mark==last 不再恒成立） |
| `tests/_fixtures.py` | 共享测试基础设施 | `inject_mock_ccxt` 补 AsyncMock；`make_sim_exchange` 默认 mark；新增 `_advance` helper |
| `tests/test_simulated_exchange.py` | `_make_exchange` 体系测试 | `_make_exchange` 默认 mark；迁移 uPnL/清算测试走 `_advance` |
| `tests/test_simulated_cs_kernel.py` | cs 内核测试 | 迁移 uPnL/清算测试走 `_advance` |
| `tests/test_alert_lifecycle.py` | `make_sim_exchange` 体系 e2e | 迁移砸盘触发清算测试走 `_advance` |
| `tests/test_sim_mark_price.py` | **新建** iter-2 真断言集 | mark 数据源 / get_mark_price / uPnL / 清算 mark 触发 / seed / stale |

**关键约束：**
- mark 不入 DB（`sessions`/`SimBalance`），不碰 alembic。
- 生产 `_process_tick(ticker)` 签名**不变**（唯一生产调用点 `_matching_loop:1192` 读实例 mark）。
- 清算**触发判定**改 mark；强平**成交价**与 stop/TP 触发**不动**。

---

## Task 1: mark 状态 + 共享测试基础设施

铺垫层：加 `_latest_mark_price` 实例状态 + 测试 helper，**此时内核仍读 ticker**，现有测试应全过（仅新增状态，不改逻辑）。

**Files:**
- Modify: `src/integrations/exchange/simulated.py:87`（`__init__` 状态）
- Modify: `tests/_fixtures.py`（`inject_mock_ccxt` AsyncMock + `make_sim_exchange` 默认 mark + 新增 `_advance`）
- Modify: `tests/test_simulated_exchange.py:7`（`_make_exchange` def，内部 `_latest_ticker` 块@21）默认 mark
- Modify: `tests/conftest.py:147`（第三构造路径 `_make` fixture，自建 via `inject_mock_ccxt`）默认 mark
- Test: `tests/test_sim_mark_price.py`（新建）

> **三个构造路径都要设默认 mark**（`__init__` 默认 `None` → 清算 `mark<=liq` 抛 `TypeError`、`get_mark_price` 抛 `RuntimeError`）：① `_make_exchange`（`test_simulated_exchange.py`）② `make_sim_exchange`（`_fixtures.py`，**仅** `test_alert_lifecycle.py` import）③ `conftest.py:_make`（自建 `TradingDeps` fixture，agent 工具测试经此调 `get_position`→`get_mark_price`）。`test_derivatives_data.py` 用本地 `_make_sim_exchange`、只测 funding/LSR 不碰 mark 消费点 → **已核不受影响、不改**。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_sim_mark_price.py`：

```python
import pytest
from tests._fixtures import make_sim_exchange, make_ticker, _advance

pytestmark = pytest.mark.asyncio


async def test_make_sim_exchange_has_default_mark():
    ex = make_sim_exchange()
    assert ex._latest_mark_price == ex._latest_ticker.last  # default mark = last seed


async def test_advance_syncs_mark_then_processes_tick():
    ex = make_sim_exchange()
    await _advance(ex, make_ticker(last=60000.0), mark=59000.0)
    assert ex._latest_mark_price == 59000.0      # mark synced
    assert ex._latest_ticker.last == 60000.0     # ticker advanced


async def test_advance_without_mark_keeps_existing():
    ex = make_sim_exchange()
    ex._latest_mark_price = 51000.0
    await _advance(ex, make_ticker(last=60000.0))  # mark omitted
    assert ex._latest_mark_price == 51000.0       # unchanged


async def test_inject_mock_ccxt_mark_sources_are_awaitable():
    ex = make_sim_exchange()
    assert (await ex._ccxt.fetch_mark_price("BTC/USDT:USDT")) is not None
    assert (await ex._ccxt.watch_mark_price("BTC/USDT:USDT")) is not None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `pytest tests/test_sim_mark_price.py -v`
Expected: FAIL — `ImportError: cannot import name '_advance'` / `AttributeError: _latest_mark_price`。

- [ ] **Step 3: 加 `__init__` 状态**

`src/integrations/exchange/simulated.py:87`，在 `self._error_count = 0` 后插入：

```python
        self._error_count = 0
        self._latest_mark_price: float | None = None   # real OKX mark (watch_mark_price); None until seeded
        self._mark_error_count = 0                      # independent from _error_count (do NOT share)
```

- [ ] **Step 4: 改 `tests/_fixtures.py`**

a) 第 6 行 import 补 `AsyncMock`：

```python
from unittest.mock import AsyncMock, MagicMock
```

b) `inject_mock_ccxt`（`:25` 区域）在 `exchange._ccxt.market = ...` 后补 async mark 源：

```python
    exchange._ccxt = MagicMock()
    exchange._ccxt.amount_to_precision = MagicMock(side_effect=_trunc3)
    exchange._ccxt.market = MagicMock(return_value={"contractSize": contract_size})
    # mark sources are async (ccxtpro) — MagicMock children aren't awaitable; use AsyncMock
    exchange._ccxt.fetch_mark_price = AsyncMock(return_value={"markPrice": 50000.0})
    exchange._ccxt.watch_mark_price = AsyncMock(return_value={"markPrice": 50000.0})
    exchange._contract_size = contract_size
    return exchange
```

c) `make_sim_exchange`（`:115` 区域）在 `ex._latest_ticker = make_ticker(...)` 后补默认 mark：

```python
    ex._latest_ticker = make_ticker(symbol=symbol)
    ex._latest_mark_price = ex._latest_ticker.last   # default mark = last seed (None would TypeError in liq check)
    ex._running = True
```

d) 文件末尾新增共享 `_advance` helper：

```python
async def _advance(ex, ticker, mark=None):
    """Advance sim price for tests: optionally sync mark, then process the tick.

    mark 真实化后 mark 不走 _process_tick（只更新 _latest_ticker）。凡"推进价格 →
    查 uPnL / 触发清算"的测试须经此 helper 同步 mark。mark=None 时保留现有
    _latest_mark_price。接受任意 Ticker，_tick / make_ticker 两套体系通用。
    """
    if mark is not None:
        ex._latest_mark_price = mark
    await ex._process_tick(ticker)
```

- [ ] **Step 5: 改 `_make_exchange`（test_simulated_exchange.py）+ `conftest._make`（第三路径）默认 mark**

在 `exchange._latest_ticker = Ticker(...)` 块后插入：

```python
    exchange._latest_ticker = Ticker(
        symbol=symbol, last=95000.0, bid=94990.0, ask=95010.0,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534400000,
    )
    exchange._latest_mark_price = 95000.0   # default mark = last seed
    exchange._running = True
```

并 `tests/conftest.py:147`（第三路径 `_make` fixture）同理，在 `exchange._latest_ticker = make_ticker(symbol=symbol)` 后插入：

```python
    exchange._latest_ticker = make_ticker(symbol=symbol)
    exchange._latest_mark_price = exchange._latest_ticker.last   # third path: default mark = last seed
    exchange._latest_price = exchange._latest_ticker.last
```

- [ ] **Step 6: 运行新测试 + 全量回归**

Run: `pytest tests/test_sim_mark_price.py -v`
Expected: PASS（4 passed）。

Run: `pytest tests/test_simulated_exchange.py tests/test_simulated_cs_kernel.py tests/test_alert_lifecycle.py -q`
Expected: PASS（内核未改逻辑，仅新增状态，全过）。

- [ ] **Step 7: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/_fixtures.py tests/test_simulated_exchange.py tests/conftest.py tests/test_sim_mark_price.py
git commit -m "$(printf 'iter-sim-exec-mark-price: mark state + shared test infra\n\n_latest_mark_price/_mark_error_count state; _fixtures AsyncMock mark\nsources + default mark + shared _advance helper.\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 2: `get_mark_price` 返真实 mark

**Files:**
- Modify: `src/integrations/exchange/simulated.py:136-148`
- Test: `tests/test_sim_mark_price.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sim_mark_price.py`：

```python
async def test_get_mark_price_returns_real_mark_not_last():
    ex = make_sim_exchange()
    await _advance(ex, make_ticker(last=60000.0), mark=59000.0)
    assert await ex.get_mark_price("BTC/USDT:USDT") == 59000.0  # mark, not last 60000


async def test_get_mark_price_raises_before_seed():
    ex = make_sim_exchange()
    ex._latest_mark_price = None
    with pytest.raises(RuntimeError):
        await ex.get_mark_price("BTC/USDT:USDT")
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_sim_mark_price.py::test_get_mark_price_returns_real_mark_not_last -v`
Expected: FAIL — 返回 60000.0 (last) 而非 59000.0。

- [ ] **Step 3: 改 `get_mark_price`**

替换 `simulated.py:136-148` 整个方法：

```python
    async def get_mark_price(self, symbol: str) -> float:
        """Return the real OKX mark price (parallel watch_mark_price stream /
        fetch_mark_price seed). Distinct from ticker.last — under live OKX mark
        and last differ by a small basis (<0.05% normal); mark is the basis for
        liquidation trigger and unrealized PnL.
        """
        self._validate_symbol(symbol)
        if self._latest_mark_price is None:
            raise RuntimeError("No mark price available yet")
        return self._latest_mark_price
```

- [ ] **Step 4: 运行，确认通过**

Run: `pytest tests/test_sim_mark_price.py -v`
Expected: PASS（6 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_sim_mark_price.py
git commit -m "$(printf 'iter-sim-exec-mark-price: get_mark_price returns real mark\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 3: `_calc_unrealized_pnl` 用 mark + 迁移 uPnL 测试

**Files:**
- Modify: `src/integrations/exchange/simulated.py:114-120`
- Modify: `tests/test_simulated_exchange.py:47`（`test_fetch_balance_with_unrealized_pnl`）
- Modify: `tests/test_simulated_cs_kernel.py:13`（`test_unrealized_pnl_scales_with_cs`）
- Test: `tests/test_sim_mark_price.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sim_mark_price.py`：

```python
async def test_unrealized_pnl_uses_mark_not_bid_ask():
    ex = make_sim_exchange()            # contract_size=1.0
    ex._leverage["BTC/USDT:USDT"] = 5
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.01)
    await _advance(ex, make_ticker(last=50000.0, bid=50000.0, ask=50000.0), mark=50000.0)   # fill @ 50000
    # ticker bid up to 51990 but mark only 51000 — uPnL must read mark
    await _advance(ex, make_ticker(last=52000.0, bid=51990.0, ask=52010.0), mark=51000.0)
    pos = (await ex.fetch_positions("BTC/USDT:USDT"))[0]
    assert pos.unrealized_pnl == pytest.approx((51000 - 50000) * 0.01)  # 10.0, not bid-based 19.9


async def test_unrealized_pnl_short_uses_mark():
    ex = make_sim_exchange()
    ex._leverage["BTC/USDT:USDT"] = 5
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.01)
    await _advance(ex, make_ticker(last=50000.0, bid=50000.0, ask=50000.0), mark=50000.0)   # fill @ 50000
    await _advance(ex, make_ticker(last=48000.0, bid=47990.0, ask=48010.0), mark=49000.0)
    pos = (await ex.fetch_positions("BTC/USDT:USDT"))[0]
    assert pos.unrealized_pnl == pytest.approx((50000 - 49000) * 0.01)  # mark-based, symmetric
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_sim_mark_price.py::test_unrealized_pnl_uses_mark_not_bid_ask -v`
Expected: FAIL — uPnL = 19.9 (bid-based) 而非 10.0。

- [ ] **Step 3: 改 `_calc_unrealized_pnl`**

替换 `simulated.py:114-120`：

```python
    def _calc_unrealized_pnl(self, pos: _Position) -> float:
        mark = self._latest_mark_price
        if mark is None:                 # guard 判据从 _latest_ticker → _latest_mark_price
            return 0.0
        if pos.side == "long":
            return (mark - pos.entry_price) * self._base_qty(pos.contracts)
        else:
            return (pos.entry_price - mark) * self._base_qty(pos.contracts)
```

- [ ] **Step 4: 迁移受影响 uPnL 测试**

a) `tests/test_simulated_exchange.py:47` `test_fetch_balance_with_unrealized_pnl`：`_make_exchange` 默认 mark=95000（vs 原 bid 94990），entry 94000、contracts 0.001 → uPnL=(95000−94000)×0.001=1.0。更新断言：

```python
    balance = await ex.fetch_balance()
    assert balance.total_usdt == pytest.approx(101.0)   # was 100.99 (bid-based); now mark 95000
    assert balance.free_usdt == pytest.approx(71.0)     # was 70.99
    assert balance.used_usdt == 30.0
```

b) `tests/test_simulated_cs_kernel.py:13` `test_unrealized_pnl_scales_with_cs`：改 `:24/:26` 两处 `_process_tick` 走 `_advance` 同步 mark。文件顶部 import 补 `_advance`：

```python
from tests.test_simulated_exchange import _make_exchange, _tick
from tests._fixtures import _advance
```

测试体 `:22-26` 改为：

```python
    await ex.create_order("BTC/USDT:USDT", "buy", "market", amount=10)
    await _advance(ex, _tick(last=100_000.0, bid=100_000.0, ask=100_000.0), mark=100_000.0)  # fill
    await _advance(ex, _tick(last=101_000.0, bid=101_000.0, ask=101_000.0), mark=101_000.0)  # move
```

（`assert pos.unrealized_pnl ≈ 100.0` 不变——mark 同步到 101000。）

- [ ] **Step 5: 运行受影响测试 + 全量 uPnL**

Run: `pytest tests/test_sim_mark_price.py tests/test_simulated_cs_kernel.py::test_unrealized_pnl_scales_with_cs "tests/test_simulated_exchange.py::test_fetch_balance_with_unrealized_pnl" -v`
Expected: PASS。

Run: `pytest tests/test_simulated_exchange.py -k "balance or pnl or unrealized" -q`
Expected: PASS（逐个确认其余 uPnL-based 断言：`test_fetch_balance_free_clamps_to_zero:59` 等 — mark=95000 vs bid 94990 数值微变，若断言失败按 mark 值更新）。

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py tests/test_simulated_cs_kernel.py tests/test_sim_mark_price.py
git commit -m "$(printf 'iter-sim-exec-mark-price: unrealized PnL uses mark (guard + migrate)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 4: 清算触发改 mark（触发 mark + 成交盘口）+ 迁移清算测试

**Files:**
- Modify: `src/integrations/exchange/simulated.py:659-679`（`_process_tick` 清算块）
- Modify: `tests/test_simulated_exchange.py`（`test_liquidation_triggers_before_stop:310` / `test_liquidation_short:505` / `test_e2e_open_then_immediate_liquidation:1178`）
- Modify: `tests/test_simulated_cs_kernel.py:136`（`test_liquidation_via_process_tick_cs_not_one`）
- Modify: `tests/test_alert_lifecycle.py:496`（`test_sim_liquidation_triggers_alert_clear`）
- Test: `tests/test_sim_mark_price.py`

> **关键区分**：`test_force_liquidate_*`（`:609`/`:1584`）直接调 `_force_liquidate(pos, symbol, price)`，**不经触发判定**，签名不变 → **不受影响、不迁移**。只迁移经 `_process_tick` 触发清算的测试。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sim_mark_price.py`（顶部 import 补 `FillEvent` 收集用 — 直接收集 fill 经 `_fill_callback`）：

```python
async def test_liquidation_triggers_on_mark_not_bid():
    ex = make_sim_exchange(initial_balance=1000.0)
    ex._leverage["BTC/USDT:USDT"] = 100
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.01)
    await _advance(ex, make_ticker(last=50000.0, bid=50000.0, ask=50000.0), mark=50000.0)  # fill @ 50000
    liq = (await ex.fetch_positions("BTC/USDT:USDT"))[0].liquidation_price
    # bid dips below liq but mark stays above → survive (mark-driven, not bid)
    await _advance(ex, make_ticker(last=liq - 10, bid=liq - 10, ask=liq - 10), mark=liq + 50)
    assert "BTC/USDT:USDT" in ex._positions
    # mark dips below liq → liquidated
    await _advance(ex, make_ticker(last=liq + 5, bid=liq - 20, ask=liq + 5), mark=liq - 1)
    assert "BTC/USDT:USDT" not in ex._positions


async def test_liquidation_fill_price_is_bid_not_mark():
    fills = []
    async def collect(f):
        fills.append(f)
    ex = make_sim_exchange(initial_balance=1000.0)
    ex._fill_callback = collect
    ex._leverage["BTC/USDT:USDT"] = 100
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.01)
    await _advance(ex, make_ticker(last=50000.0, bid=50000.0, ask=50000.0), mark=50000.0)
    liq = (await ex.fetch_positions("BTC/USDT:USDT"))[0].liquidation_price
    await _advance(ex, make_ticker(last=liq + 5, bid=liq - 20, ask=liq + 5), mark=liq - 1)
    liq_fill = [f for f in fills if f.trigger_reason == "liquidation"][0]
    assert liq_fill.fill_price == liq - 20   # 盘口 bid, NOT mark (liq-1)
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_sim_mark_price.py::test_liquidation_triggers_on_mark_not_bid -v`
Expected: FAIL — 当前用 bid 触发：Case A (bid=liq-10 ≤ liq) 会误清算 → `in ex._positions` 失败。

- [ ] **Step 3: 改 `_process_tick` 清算块**

替换 `simulated.py:659-679`（清算判定段）。改 `ticker.bid/ask` 判定为 `mark`，成交价仍传 `ticker.bid/ask`：

```python
            # 1. Liquidation check (must be before conditional orders)
            #    Trigger basis = mark (real OKX); fill price = order-book bid/ask (市价吃盘口)
            mark = self._latest_mark_price
            for symbol, pos in list(self._positions.items()):
                liq = self._calc_liquidation_price(pos)
                if pos.side == "long" and mark <= liq:
                    fill = self._force_liquidate(pos, symbol, ticker.bid)
                    triggered.append(fill)
                    new_orders.append((Order(
                        id=fill.order_id, symbol=symbol,
                        side="sell", order_type="liquidation",
                        amount=fill.amount, price=fill.fill_price,
                        status="closed", fee=fill.fee,
                    ), fill.position_side))
                elif pos.side == "short" and mark >= liq:
                    fill = self._force_liquidate(pos, symbol, ticker.ask)
                    triggered.append(fill)
                    new_orders.append((Order(
                        id=fill.order_id, symbol=symbol,
                        side="buy", order_type="liquidation",
                        amount=fill.amount, price=fill.fill_price,
                        status="closed", fee=fill.fee,
                    ), fill.position_side))
```

- [ ] **Step 4: 运行新测试，确认通过**

Run: `pytest tests/test_sim_mark_price.py -v`
Expected: PASS。

- [ ] **Step 5: 迁移现有清算测试（经 `_process_tick` 触发的）**

**迁移规则**：把"推价到清算区触发清算"的 `await ex._process_tick(_tick(... bid=<砸盘价>))` 改为 `await _advance(ex, _tick(... bid=<砸盘价>), mark=<砸盘价>)`（mark 同步到砸盘价以触发；**保留原 tick 的全部 args 含 timestamp**）。各文件顶部 import 补 `_advance`（`test_simulated_exchange.py` 从 `tests._fixtures` import；`test_alert_lifecycle.py` 已 import `make_sim_exchange` 处同行追加 `_advance`）。

逐个测试：
- `test_simulated_exchange.py::test_liquidation_triggers_before_stop:310` — 触发清算那一 tick 改走 `_advance(.., mark=<砸盘价>)`。
- `test_simulated_exchange.py::test_liquidation_short:505` — 同（short：mark 升破 liq）。
- `test_simulated_exchange.py::test_e2e_open_then_immediate_liquidation:1178` — 同。
- `test_simulated_cs_kernel.py::test_liquidation_via_process_tick_cs_not_one:136`（`:163` `_process_tick(_tick(bid=89000))`）→ `_advance(ex, _tick(last=89_000.0, bid=89_000.0, ask=89_000.0), mark=89_000.0)`。
- `test_alert_lifecycle.py::test_sim_liquidation_triggers_alert_clear:496`（砸盘 `_process_tick(make_ticker(last=40000.0, timestamp=...))`）→ `_advance(sim, make_ticker(last=40000.0, timestamp=1700000001000), mark=40000.0)`（**保留原 timestamp，勿丢 args**）。

代表性完整 diff（cs_kernel `:163`）：

```python
    # 旧：
    # await ex._process_tick(_tick(last=89_000.0, bid=89_000.0, ask=89_000.0))
    # 新：
    await _advance(ex, _tick(last=89_000.0, bid=89_000.0, ask=89_000.0), mark=89_000.0)
```

> 非触发清算的 tick（开仓 fill、价未到清算区）保持原 `_process_tick` 不变——只迁移"意图触发清算"那一 tick。

- [ ] **Step 6: 运行迁移测试 + 三文件回归**

Run: `pytest "tests/test_simulated_exchange.py::test_liquidation_triggers_before_stop" "tests/test_simulated_exchange.py::test_liquidation_short" "tests/test_simulated_exchange.py::test_e2e_open_then_immediate_liquidation" "tests/test_simulated_cs_kernel.py::test_liquidation_via_process_tick_cs_not_one" "tests/test_alert_lifecycle.py::test_sim_liquidation_triggers_alert_clear" -v`
Expected: PASS。

Run: `pytest tests/test_simulated_exchange.py tests/test_simulated_cs_kernel.py tests/test_alert_lifecycle.py -q`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py tests/test_simulated_cs_kernel.py tests/test_alert_lifecycle.py tests/test_sim_mark_price.py
git commit -m "$(printf 'iter-sim-exec-mark-price: liquidation trigger uses mark (fill stays bid/ask)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 5: mark 数据源（seed + `_mark_loop` + close + 独立 error count）

**Files:**
- Modify: `src/integrations/exchange/simulated.py`（`start()` seed @1154+、新增 `_seed_mark_price`/`_mark_loop`、`close()` @1203）
- Test: `tests/test_sim_mark_price.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_sim_mark_price.py`（顶部补 `from unittest.mock import AsyncMock`）：

```python
async def test_seed_mark_price_extracts_value():
    ex = make_sim_exchange()
    ex._ccxt.fetch_mark_price = AsyncMock(return_value={"markPrice": 67000.0})
    assert await ex._seed_mark_price() == 67000.0


async def test_seed_mark_price_parses_real_string_markpx():
    # mock fidelity (spec §5 / project_iter2_mock_fidelity_lesson): real OKX
    # fetch_mark_price → parse_ticker → markPrice = safe_string(info,'markPx'),
    # 即字符串而非 float。float() 必须能解析。'markPrice' 键已核 ccxt 4.5.47 okx.parse_ticker。
    ex = make_sim_exchange()
    ex._ccxt.fetch_mark_price = AsyncMock(return_value={"markPrice": "66500.5"})
    assert await ex._seed_mark_price() == 66500.5


async def test_seed_mark_price_fail_fast_after_retries(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())  # skip real 1s+2s backoff
    ex = make_sim_exchange()
    ex._ccxt.fetch_mark_price = AsyncMock(side_effect=RuntimeError("net"))
    with pytest.raises(RuntimeError):
        await ex._seed_mark_price()


async def test_mark_loop_updates_then_keeps_stale_on_error():
    ex = make_sim_exchange()
    ex._latest_mark_price = 50000.0
    # first push 51000, then raise (stale keeps 51000), then cancel to exit
    ex._ccxt.watch_mark_price = AsyncMock(
        side_effect=[{"markPrice": 51000.0}, RuntimeError("ws"), __import__("asyncio").CancelledError()]
    )
    await ex._mark_loop()
    assert ex._latest_mark_price == 51000.0   # updated, then kept stale through error
    assert ex._mark_error_count >= 1          # independent counter incremented


async def test_mark_error_count_independent_of_ticker():
    ex = make_sim_exchange()
    ex._error_count = 7
    ex._ccxt.watch_mark_price = AsyncMock(side_effect=[{"markPrice": 52000.0}, __import__("asyncio").CancelledError()])
    await ex._mark_loop()
    assert ex._error_count == 7   # mark loop must NOT touch _error_count
```

- [ ] **Step 2: 运行，确认失败**

Run: `pytest tests/test_sim_mark_price.py -k "seed_mark or mark_loop or mark_error" -v`
Expected: FAIL — `AttributeError: _seed_mark_price` / `_mark_loop`。

- [ ] **Step 3: 新增 `_seed_mark_price` + `_mark_loop`，接入 `start()` / `close()`**

a) 新增方法（放在 `_matching_loop` 之后，`simulated.py:1201` 后）：

```python
    async def _seed_mark_price(self) -> float:
        """Seed mark via fetch_mark_price; 3-attempt backoff; fail-fast on all.

        MUST be called after _init_contract_size() (load_markets) so ccxt can
        resolve instId. Mirrors seed_ticker retry semantics.
        """
        for attempt in range(3):
            try:
                raw = await self._ccxt.fetch_mark_price(self._symbol)
                return float(raw["markPrice"])
            except Exception as e:
                if attempt < 2:
                    delay = 2 ** attempt
                    logger.warning(f"fetch_mark_price attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    raise RuntimeError(f"Failed to seed mark price after 3 attempts: {e}") from e

    async def _mark_loop(self) -> None:
        """Parallel WS loop maintaining _latest_mark_price. Independent
        _mark_error_count (NOT shared with _matching_loop._error_count).
        """
        while self._running:
            try:
                raw = await self._ccxt.watch_mark_price(self._symbol)
                self._latest_mark_price = float(raw["markPrice"])
            except asyncio.CancelledError:
                break
            except Exception:
                self._mark_error_count += 1
                logger.error("Mark loop error (count=%d)", self._mark_error_count, exc_info=True)
                if self._mark_error_count >= 3:
                    await asyncio.sleep(min(5 * self._mark_error_count, 60))
            else:
                self._mark_error_count = 0
```

b) `start()` 接入：在 `_init_contract_size()`（`:1153`）之后、seed_ticker 块旁 seed mark，并在 `_matching_task` 创建处并列起 `_mark_task`。`simulated.py:1176-1179` 区域改为：

```python
        self._latest_price = self._latest_ticker.last
        self._latest_mark_price = await self._seed_mark_price()   # after _init_contract_size (markets loaded)

        self._running = True
        self._matching_task = asyncio.create_task(self._matching_loop())
        self._mark_task = asyncio.create_task(self._mark_loop())
```

c) `close()`（`:1203-1213`）在 cancel `_matching_task` 旁 cancel `_mark_task`：

```python
    async def close(self) -> None:
        self._running = False
        for attr in ("_matching_task", "_mark_task"):
            task = getattr(self, attr, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if hasattr(self, "_ccxt"):
            await self._ccxt.close()
        logger.info("SimulatedExchange closed")
```

- [ ] **Step 4: 运行新测试，确认通过**

Run: `pytest tests/test_sim_mark_price.py -k "seed_mark or mark_loop or mark_error" -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_sim_mark_price.py
git commit -m "$(printf 'iter-sim-exec-mark-price: mark data source (seed + _mark_loop + close)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 6: 感知层注释更新 + 集成审查

mark 真实化后感知层 drift 后缀从"恒抑制"变"偶发显示"——渲染逻辑不改，但注释变误导。

**Files:**
- Modify: `src/agent/tools_perception.py:397-402`
- Test（审查，不新增功能）: `tests/test_iter_tool_opt_mark_vs_last.py` / `tests/test_iter_tool_opt_getpos_mark_suppress.py`

- [ ] **Step 1: 更新 `tools_perception.py:397-402` 注释**

替换该注释块（保留 `if drift_str in (...)` 逻辑不动）：

```python
            # Suppress the (Last:..., drift ...) suffix when the displayed drift
            # rounds to zero. Under sim, mark now comes from the real OKX
            # watch_mark_price stream (iter-2) — mark≈last (<0.05% normal) so the
            # suffix is usually but no longer always suppressed; it surfaces when
            # real drift rounds to ≥0.01%. Format-string comparison (not
            # abs(drift_pct) < epsilon) so the suppression boundary tracks the
            # .2f display precision exactly.
```

- [ ] **Step 2: 审查渲染层测试不依赖 sim mark==last**

Run: `pytest tests/test_iter_tool_opt_mark_vs_last.py tests/test_iter_tool_opt_getpos_mark_suppress.py -v`
Expected: PASS（这些注入任意 mark/last 测渲染，不依赖 sim 内部 mark==last）。若任一断言隐含 sim 产出 mark==last → 改为显式注入 mark/last。

- [ ] **Step 3: Commit**

```bash
git add src/agent/tools_perception.py
git commit -m "$(printf 'iter-sim-exec-mark-price: update mark==last comment (now real drift)\n\nCo-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>')"
```

---

## Task 7: 全量 pytest gate（go/no-go）

资金安全路径回归面大，全量绿是合并前 gate。

**Files:** 无（验证 + 收口）

- [ ] **Step 1: 全量 pytest**

Run: `pytest -q`
Expected: PASS — 本 iter 净增 `test_sim_mark_price.py` 16 新测试（含 Task 5 计数器 recovery-reset 覆盖）；rebase onto PR #67 后基线推进，实测 2087 passed + 5 skip。无 fail。

- [ ] **Step 2: 若有 fail，逐个核因**

按失败定位：① uPnL/清算断言数值漂移 → 该测试未走 `_advance` 同步 mark，补迁移；② `AttributeError: _latest_mark_price` → 某构造路径绕过两个 helper，直接补默认 mark；③ `await` Mock 报错 → 该测试自建 mock `_ccxt` 未给 AsyncMock mark 源。修复后重跑 `pytest -q`。

- [ ] **Step 3: 确认无 DB/alembic 改动**

Run: `git diff --stat main -- src/storage/ alembic/`
Expected: 空（mark 不持久化，无 schema 变更）。

- [ ] **Step 4: 终态确认**

Run: `git log --oneline main..HEAD`
Expected: spec commit（`bce98c6`）+ Task 1-6 共 6 个 impl commit。

---

## Self-Review 覆盖检查（plan 作者已核）

- spec §3.1 数据源 → Task 1（状态）+ Task 5（seed/loop/close）✓
- spec §3.2 内核 3 处 → Task 2（get_mark_price）+ Task 3（uPnL）+ Task 4（清算触发）✓
- spec §3.3 guard 判据 → Task 3 Step 3 ✓
- spec D2 触发 mark+成交盘口 → Task 4（fill 传 ticker.bid/ask）+ `test_liquidation_fill_price_is_bid_not_mark` ✓
- spec D3 seed fail-fast → Task 5 `test_seed_mark_price_fail_fast_after_retries` ✓
- spec D5 helper 沉 _fixtures 共享层 → Task 1 `_advance` ✓
- spec §2.3 注释更新 → Task 6 ✓
- spec §5 三体系迁移（_make_exchange / make_sim_exchange / cs_kernel）→ Task 3/4 迁移步 ✓
- spec §5 三构造路径默认 mark（含 `conftest._make` 第三路径；`test_derivatives_data` 本地不受影响）→ Task 1 ✓
- spec §5 mock fidelity 真实结构（字符串 markPx）→ Task 5 `test_seed_mark_price_parses_real_string_markpx` ✓
- spec §5 AsyncMock → Task 1 Step 4 ✓
- spec §7 独立 _mark_error_count → Task 5 `test_mark_error_count_independent_of_ticker` ✓
- spec §6 stop/TP 不动 → 全 plan 未触碰 `_should_trigger` ✓
