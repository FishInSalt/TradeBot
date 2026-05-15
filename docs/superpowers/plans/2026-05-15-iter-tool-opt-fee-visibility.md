# iter-tool-opt-fee-visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 端到端注入 fee fact，让 agent 在四个 mental moment（决策前认知 / 持仓中锚点 / 执行 submit / fill notification）都看到 fee 数字 + breakeven 锚点。

**Architecture:** wizard 必填 `fee_rate` → `RuntimeConfig.taker_fee_rate` + `TradingDeps.fee_rate` → system prompt Market Context 段渲染数字；工具输出层重算 fee fact（`entry × contracts × rate`）。`FillEvent` 加 `entry_price` 字段，sim 三处 close path + OKX `_close_order_entry_cache` 在 fill 事件中填入，cli 渲染层无需反推、无需 isinstance(SimulatedExchange) 判断。`SimulatedExchange.__init__` + `build_services` + wizard sub-step 三层 fail-loud 防 NULL fee_rate 流到工具决策路径。

**Tech Stack:** Python 3.13 / pydantic-ai / SQLAlchemy / CCXT / pytest-asyncio / Rich CLI

**Spec:** `docs/superpowers/specs/2026-05-15-iter-tool-opt-fee-visibility-design.md`

---

## File Structure

| 文件 | 责任 | 操作 |
|---|---|---|
| `src/agent/persona.py` | `DEFAULT_TAKER_FEE_RATE` 常量 + `RuntimeConfig.taker_fee_rate` 字段 + `_build_layer1` Market Context 段重写 | Modify |
| `src/agent/trader.py` | `TradingDeps.fee_rate` 字段；wrapper docstring 追加 fee 提示行（5 个工具）| Modify |
| `src/agent/tools_perception.py` | `get_position` 加 Fee & Breakeven 段 + gross 标签；`get_performance` 加 gross-based 标签 | Modify |
| `src/agent/tools_execution.py` | `open_position` / `close_position` / `place_limit_order` submit 输出加 fee fact；`close_position` 加 round-trip net + approval message net 视角；close-direction 工具调用 `exchange.register_close_order_entry` | Modify |
| `src/integrations/exchange/base.py` | `FillEvent.entry_price` 字段；`BaseExchange.register_close_order_entry()` no-op 默认方法 | Modify |
| `src/integrations/exchange/simulated.py` | `__init__` raise on None fee_rate；三处 close path（`_fill_market_close` / `_execute_fill` / `_force_liquidate`）传 `entry_price` 到 `FillEvent` | Modify |
| `src/integrations/exchange/okx.py` | `_close_order_entry_cache` 内部 cache + `register_close_order_entry` 实现 + `cancel_order` 清 cache + TTL 清理 + `_parse_fill_event` pop cache 填入 entry_price | Modify |
| `src/cli/wizard.py` | `WizardResult.fee_rate` 类型 `float | None → float`；`_step_exchange` 文字调整 + OKX 分支必填 fee_rate；`_show_summary` OKX 分支也显示 fee | Modify |
| `src/cli/session_manager.py` | `_restore_session` 加 legacy NULL fee_rate sub-step | Modify |
| `src/cli/app.py` | `build_services` 顶部 raise on None fee_rate + drift guard + 注入 RuntimeConfig + TradingDeps；`run_agent_cycle` fill notification 渲染（line 472-479）使用 `event.entry_price` 计算 round-trip net | Modify |
| `tests/test_persona.py` | Layer1 Market Context 段渲染 fee_rate / 无 evaluation 词 drift guard | Modify |
| `tests/test_wizard.py` | simulated + OKX 必填 fee_rate | Modify |
| `tests/test_simulated_exchange.py` | `__init__` raise；三处 close path 都填入 entry_price；pnl_cap 隔离 drift guard | Modify |
| `tests/test_okx_exchange.py` | submit cache write；fill pop；cache miss 降级；TTL 清理；info.pnl gross 语义 fixture verify | Modify |
| `tests/test_get_position.py` | Fee & Breakeven 段 long/short；不含 rate 数字 drift guard；entry fee 公式 + 加仓/part close 场景；gross 标签 | Modify |
| `tests/test_get_performance.py` | docstring 强化 + gross-based 标签 | Modify |
| `tests/test_tools_execution.py` | open/close/limit submit fee fact；docstring 无 evaluation 词 drift guard | Modify |
| `tests/test_cli_app.py` | fill notification open/close 含 fee；round-trip 用 entry_price 不反推；label `(this fill, equiv-round-trip)`；pnl_cap 场景 entry_price 正确 | Modify |
| 测试 fixture 多文件 | 受 SimulatedExchange `__init__` raise 影响的 fixture 显式补 fee_rate | Modify (Task 0 lock 清单) |

---

## Task 0: Inventory & classify test fixtures

**Goal:** lock SimulatedExchange `__init__` fail-loud 改动的 fixture 影响范围。这是后续 Task 5 的输入。本 task 不改代码，只产出分类清单。

**Files:**
- Read-only: `tests/`
- Output: 临时文档 `.working/iter-tool-opt-fee-visibility-fixture-inventory.md`（不进 git）

- [ ] **Step 1: Run grep to enumerate all relevant fixture call sites**

```bash
grep -rn "SimulatedExchange(" tests/ > .working/iter-tool-opt-fee-visibility-fixture-inventory.md
echo "---" >> .working/iter-tool-opt-fee-visibility-fixture-inventory.md
grep -rn "ExchangeConfig(" tests/ >> .working/iter-tool-opt-fee-visibility-fixture-inventory.md
echo "---" >> .working/iter-tool-opt-fee-visibility-fixture-inventory.md
grep -rn "RuntimeConfig(" tests/ >> .working/iter-tool-opt-fee-visibility-fixture-inventory.md
echo "---" >> .working/iter-tool-opt-fee-visibility-fixture-inventory.md
grep -rn "TradingDeps(" tests/ >> .working/iter-tool-opt-fee-visibility-fixture-inventory.md
echo "---" >> .working/iter-tool-opt-fee-visibility-fixture-inventory.md
grep -rn "WizardResult(" tests/ >> .working/iter-tool-opt-fee-visibility-fixture-inventory.md
```

- [ ] **Step 2: Classify each call site into A/B/C buckets**

For each call site in the inventory file, manually annotate:
- **A**（无需改）: 已显式 `fee_rate=...` 设值（任何非 None 值）
- **B**（需改）: 漏设 `fee_rate` 字段，依赖现有 `0.0005` silent default。Task 5 后会 raise。必须显式补 `fee_rate=DEFAULT_TAKER_FEE_RATE` 或具体测试值
- **C**（保留 NULL）: 显式测 nullable 行为的（如 `test_storage.py` 验证 DB column nullable）— 不改

注意：`test_okx_algo_normalization.py:50 result.fee_rate = None` 属 B 类（必须改为非 None，因为 WizardResult.fee_rate 类型本身在 Task 13 收紧为 `float`）。

- [ ] **Step 3: Lock total counts and produce summary**

在 inventory 文件顶部写一行总结：`# Inventory: A=N1 / B=N2 / C=N3 (total N1+N2+N3)`. 这是 Task 5 fixture migration 的输入。

- [ ] **Step 4: No commit (这是 working doc, 不进 git)**

---

## Task 1: Add `DEFAULT_TAKER_FEE_RATE` constant + RuntimeConfig field

**Files:**
- Modify: `src/agent/persona.py:1-54`
- Test: `tests/test_persona.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_persona.py 顶部 import 加 DEFAULT_TAKER_FEE_RATE
from src.agent.persona import (
    CYCLE_DECISION_WORD_CAP,
    DEFAULT_TAKER_FEE_RATE,
    RuntimeConfig,
    generate_system_prompt,
)

def test_default_taker_fee_rate_is_okx_btc_perp_regular_tier():
    """DEFAULT_TAKER_FEE_RATE = 0.0005 (OKX BTC perp regular tier taker)."""
    assert DEFAULT_TAKER_FEE_RATE == 0.0005

def test_runtime_config_default_taker_fee_rate():
    """RuntimeConfig 默认 taker_fee_rate 与常量一致 (test/temp 用途)."""
    rc = RuntimeConfig()
    assert rc.taker_fee_rate == DEFAULT_TAKER_FEE_RATE

def test_runtime_config_explicit_taker_fee_rate():
    """RuntimeConfig 接受 explicit override."""
    rc = RuntimeConfig(taker_fee_rate=0.001)
    assert rc.taker_fee_rate == 0.001
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_persona.py::test_default_taker_fee_rate_is_okx_btc_perp_regular_tier tests/test_persona.py::test_runtime_config_default_taker_fee_rate tests/test_persona.py::test_runtime_config_explicit_taker_fee_rate -v
```
Expected: ImportError on `DEFAULT_TAKER_FEE_RATE`

- [ ] **Step 3: Add constant and RuntimeConfig field**

In `src/agent/persona.py` after `CYCLE_DECISION_CHAR_HARD_FLOOR = 8000` (around line 22) add:

```python
DEFAULT_TAKER_FEE_RATE = 0.0005
"""OKX BTC perp regular tier taker fee, as decimal. Used as wizard input
default + RuntimeConfig/TradingDeps test defaults. Production paths MUST
override via wizard-injected sessions.fee_rate."""
```

In `RuntimeConfig` (after `wake_max_minutes` field, before `def generate_system_prompt`) add:

```python
    taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE
    """Session-level taker fee rate (decimal storage format, e.g., 0.001 = 0.1%;
    wizard input is in percent and divides by 100 before storing).

    Injected from sessions.fee_rate via build_services. Default DEFAULT_TAKER_FEE_RATE
    is for tests / temp call sites only — production paths MUST set explicitly.
    If a production code path silently relies on the default, that is a bug —
    flag and route through cli wiring."""
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_persona.py -v
```
Expected: 3 new tests PASS, no existing test breaks.

- [ ] **Step 5: Commit**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "feat(fee-vis): add DEFAULT_TAKER_FEE_RATE + RuntimeConfig.taker_fee_rate"
```

---

## Task 2: Add `fee_rate` field to TradingDeps

**Files:**
- Modify: `src/agent/trader.py:24-46`
- Test: `tests/test_trader_agent.py` (existing) or `tests/test_persona.py` (small)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_persona.py`:

```python
def test_trading_deps_has_fee_rate_default():
    """TradingDeps default fee_rate matches DEFAULT_TAKER_FEE_RATE."""
    from src.agent.trader import TradingDeps
    from unittest.mock import MagicMock
    deps = TradingDeps(
        symbol="BTC/USDT:USDT", timeframe="5m",
        market_data=MagicMock(), exchange=MagicMock(),
        technical=MagicMock(), memory=MagicMock(),
        session_id="test",
    )
    assert deps.fee_rate == DEFAULT_TAKER_FEE_RATE
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_persona.py::test_trading_deps_has_fee_rate_default -v
```
Expected: AttributeError or AssertionError on `deps.fee_rate`.

- [ ] **Step 3: Add field**

In `src/agent/trader.py` `TradingDeps` dataclass add after `initial_balance: float = 10000.0`:

```python
    from src.agent.persona import DEFAULT_TAKER_FEE_RATE  # if not already imported at module top
    # (move import to module top — keep all dataclass field annotations clean)

    fee_rate: float = DEFAULT_TAKER_FEE_RATE
    """Session-level taker fee rate (decimal). Mirror of RuntimeConfig.taker_fee_rate;
    injected from sessions.fee_rate via build_services. Default for tests only."""
```

Module-top import (replace existing `from src.agent.persona import ...` line if present, else add):

```python
from src.agent.persona import DEFAULT_TAKER_FEE_RATE, RuntimeConfig, generate_system_prompt
```

- [ ] **Step 4: Run test to verify pass + full suite no regression**

```bash
pytest tests/test_persona.py tests/test_trader_agent.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/trader.py tests/test_persona.py
git commit -m "feat(fee-vis): add TradingDeps.fee_rate field"
```

---

## Task 3: Add `entry_price` field to FillEvent

**Files:**
- Modify: `src/integrations/exchange/base.py:324-336`
- Test: `tests/test_simulated_exchange.py` (use existing FillEvent import)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_simulated_exchange.py`:

```python
def test_fill_event_has_optional_entry_price_field():
    """FillEvent.entry_price defaults to None and accepts float."""
    from src.integrations.exchange.base import FillEvent

    ev = FillEvent(
        order_id="1", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", trigger_reason="market",
        fill_price=80000.0, amount=1.0, fee=40.0, pnl=100.0,
        timestamp=1, is_full_close=True,
    )
    assert ev.entry_price is None  # default

    ev2 = FillEvent(
        order_id="2", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", trigger_reason="market",
        fill_price=80000.0, amount=1.0, fee=40.0, pnl=100.0,
        timestamp=1, is_full_close=True, entry_price=79900.0,
    )
    assert ev2.entry_price == 79900.0
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_simulated_exchange.py::test_fill_event_has_optional_entry_price_field -v
```
Expected: TypeError on unexpected keyword `entry_price`.

- [ ] **Step 3: Add field to FillEvent**

In `src/integrations/exchange/base.py` `FillEvent` dataclass, after `is_full_close: bool` add:

```python
    entry_price: float | None = None
    """Position weighted-avg entry price at fill time (per contract).

    For close fills (pnl is not None): exchange-layer-filled actual position
    entry price (before any pnl_cap clamping in sim). Used by cli renderer
    to compute round-trip net without reverse-engineering from pnl.

    For open fills (pnl is None): always None — by design.
    Rationale: open fill 的 entry 信息已通过 fill_price 表达；entry_price 字段
    语义专用于 close fill 的 position weighted-avg entry。统一 open fill 永远
    None 避免半态字段导致后续误用。
    """
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_simulated_exchange.py -v
```
Expected: new test PASS, no existing FillEvent construction breaks (default value preserves backward compat).

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_simulated_exchange.py
git commit -m "feat(fee-vis): add FillEvent.entry_price optional field"
```

---

## Task 4: Add `BaseExchange.register_close_order_entry()` no-op default

**Files:**
- Modify: `src/integrations/exchange/base.py` (BaseExchange class)
- Test: `tests/test_simulated_exchange.py`

**Rationale:** spec §4.5b 描述 OKX 实现 `_close_order_entry_cache` + tools_execution.py 调用。为了保持工具层 exchange-agnostic（不做 isinstance 检查），加 BaseExchange no-op default method，OKX 重写实现 cache，sim 继承 no-op 默认。

- [ ] **Step 1: Write the failing test**

```python
def test_simulated_exchange_register_close_order_entry_is_noop():
    """SimulatedExchange inherits BaseExchange.register_close_order_entry no-op (no error)."""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import ExchangeConfig

    cfg = ExchangeConfig(name="simulated", fee_rate=0.0005)
    ex = SimulatedExchange(config=cfg, db_engine=None, session_id="t", symbol="BTC/USDT:USDT")
    # 不抛错，不返回值
    result = ex.register_close_order_entry("order123", 80000.0)
    assert result is None
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_simulated_exchange.py::test_simulated_exchange_register_close_order_entry_is_noop -v
```
Expected: AttributeError on `register_close_order_entry`.

- [ ] **Step 3: Add no-op method to BaseExchange**

In `src/integrations/exchange/base.py` `BaseExchange` class (any logical location, e.g., after `on_fill` around line 180), add:

```python
    def register_close_order_entry(self, order_id: str, entry_price: float) -> None:
        """Hook for exchange impls to record per-order entry_price for close fills.

        Default: no-op (sim path captures entry_price directly in fill event from
        in-memory _Position; OKX path overrides this method to populate
        _close_order_entry_cache, consumed by _parse_fill_event).

        Called by close-direction tools (close_position / set_stop_loss /
        set_take_profit) immediately after create_order returns.
        """
        return None
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_simulated_exchange.py::test_simulated_exchange_register_close_order_entry_is_noop -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_simulated_exchange.py
git commit -m "feat(fee-vis): add BaseExchange.register_close_order_entry no-op hook"
```

---

## Task 5: SimulatedExchange `__init__` fail-loud + fixture migration

**Files:**
- Modify: `src/integrations/exchange/simulated.py:66`
- Modify: 多个 fixture 文件（per Task 0 inventory B-类清单）
- Test: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write the failing test**

```python
def test_init_raises_when_fee_rate_is_none():
    """SimulatedExchange constructor raises on None fee_rate (silent fallback removed)."""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import ExchangeConfig
    import pytest

    cfg = ExchangeConfig(name="simulated", fee_rate=None)
    with pytest.raises(ValueError, match="fee_rate"):
        SimulatedExchange(
            config=cfg, db_engine=None,
            session_id="t", symbol="BTC/USDT:USDT",
        )
```

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_simulated_exchange.py::test_init_raises_when_fee_rate_is_none -v
```
Expected: did not raise (current code has silent 0.0005 fallback).

- [ ] **Step 3: Replace silent fallback with raise**

In `src/integrations/exchange/simulated.py:66` replace:

```python
self._fee_rate: float = config.fee_rate if config.fee_rate is not None else 0.0005
```

with:

```python
if config.fee_rate is None:
    raise ValueError(
        "SimulatedExchange requires fee_rate in config "
        "(wizard-enforced; legacy NULL session detected)"
    )
self._fee_rate: float = config.fee_rate
```

- [ ] **Step 4: Migrate B-class fixture files (per Task 0 inventory)**

For each B-class call site in `.working/iter-tool-opt-fee-visibility-fixture-inventory.md`, add explicit `fee_rate=DEFAULT_TAKER_FEE_RATE` (or specific test value).

Example pattern in `tests/conftest.py` (most-used fixture):

```python
# Before
config = ExchangeConfig(name="simulated", precision={"BTC/USDT:USDT": 3})

# After
from src.agent.persona import DEFAULT_TAKER_FEE_RATE  # add import at top
config = ExchangeConfig(
    name="simulated", fee_rate=DEFAULT_TAKER_FEE_RATE,
    precision={"BTC/USDT:USDT": 3},
)
```

For `tests/test_okx_algo_normalization.py:50` (`result.fee_rate = None`), change to a non-None value (e.g., `result.fee_rate = 0.0005`) — WizardResult type tightens in Task 13.

- [ ] **Step 5: Run full test suite to verify no regression**

```bash
pytest tests/ -x --timeout=60 2>&1 | tail -40
```
Expected: all PASS, including the new raise test. If failures appear, those are missed B-class fixtures — add to inventory and fix.

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/
git commit -m "feat(fee-vis): SimulatedExchange __init__ fail-loud on None fee_rate + fixture migration"
```

---

## Task 6: Sim `_fill_market_close` captures entry_price into FillEvent

**Files:**
- Modify: `src/integrations/exchange/simulated.py:366-399`
- Test: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_fill_market_close_includes_entry_price_in_event():
    """sim market close fill event carries position weighted-avg entry."""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import ExchangeConfig
    from src.agent.persona import DEFAULT_TAKER_FEE_RATE

    cfg = ExchangeConfig(name="simulated", fee_rate=DEFAULT_TAKER_FEE_RATE)
    ex = SimulatedExchange(config=cfg, db_engine=None, session_id="t", symbol="BTC/USDT:USDT")
    ex.set_initial_balance(10000.0)
    await ex.start()

    # Open long @ 80000
    await ex._update_ticker(_make_ticker("BTC/USDT:USDT", 80000.0))
    await ex.set_leverage("BTC/USDT:USDT", 10)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", amount=0.1)
    await ex._update_ticker(_make_ticker("BTC/USDT:USDT", 80100.0))
    # Capture fill events
    fills = []
    ex.on_fill(lambda ev: fills.append(ev) or asyncio.sleep(0))
    # ... (use existing test helper pattern; assert close fill carries entry_price)

    # Close
    fills.clear()
    await ex.create_order(
        "BTC/USDT:USDT", "sell", "market", amount=0.1,
        params={"reduceOnly": True},
    )
    await ex._update_ticker(_make_ticker("BTC/USDT:USDT", 80200.0))
    close_fill = next(f for f in fills if f.pnl is not None)
    assert close_fill.entry_price == 80000.0  # captured before pnl_cap
```

(adapt to existing test helpers in `tests/test_simulated_exchange.py` — use established fixture/ticker patterns)

- [ ] **Step 2: Run test to verify failure**

```bash
pytest tests/test_simulated_exchange.py::test_fill_market_close_includes_entry_price_in_event -v
```
Expected: assertion error — entry_price is None.

- [ ] **Step 3: Capture entry_price before _close_position_core**

In `src/integrations/exchange/simulated.py:366-399` `_fill_market_close`, modify the body to capture `pos.entry_price` before the close call:

```python
def _fill_market_close(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
    """Fill a pending market close order. Returns None if position already gone."""
    pos = self._positions.get(order.symbol)
    if pos is None:
        logger.warning(f"Market close {order.id} cancelled: position already closed")
        self._frozen_usdt -= order.frozen_margin
        self._free_usdt += order.frozen_margin
        return None

    actual_amount = min(order.amount, pos.contracts)
    fill_price = ticker.bid if pos.side == "long" else ticker.ask
    position_side = pos.side
    captured_entry = pos.entry_price  # capture BEFORE close (pos may be popped from self._positions in _close_position_core line 437-438)
    pnl, fee, _ = self._close_position_core(
        order.symbol, pos.side, actual_amount, fill_price, pnl_cap=True,
    )

    self._frozen_usdt -= order.frozen_margin
    self._free_usdt += order.frozen_margin

    is_full_close = order.symbol not in self._positions

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    logger.info(
        f"Market close filled: {order.side} {actual_amount} {order.symbol} @ {fill_price:.2f}, "
        f"pnl={pnl:.4f}, fee={fee:.4f}"
    )
    return FillEvent(
        order_id=order.id, symbol=order.symbol, side=order.side,
        position_side=position_side, trigger_reason="market",
        fill_price=fill_price, amount=actual_amount, fee=fee,
        pnl=pnl, timestamp=now_ms,
        is_full_close=is_full_close,
        entry_price=captured_entry,
    )
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_simulated_exchange.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(fee-vis): sim _fill_market_close captures entry_price into FillEvent"
```

---

## Task 7: Sim `_execute_fill` (SL/TP path) captures entry_price

**Files:**
- Modify: `src/integrations/exchange/simulated.py:522-538`
- Test: `tests/test_simulated_exchange.py`

**Rationale:** sim #8 实证 — 15 笔 close 中 8 stop + 2 take_profit + 5 market = SL/TP 触发占 67%（主路径）。漏接 `_execute_fill` 等于主路径 fee 闭环不工作。

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_execute_fill_includes_entry_price_for_stop_trigger():
    """sim SL trigger fill event carries position weighted-avg entry."""
    # ... build position, set stop loss, trigger via _update_ticker that crosses stop
    # assert sl_fill.entry_price == position.entry_price (pre-close value)

@pytest.mark.asyncio
async def test_execute_fill_includes_entry_price_for_take_profit_trigger():
    """sim TP trigger fill event carries position weighted-avg entry."""
    # ... mirror SL test
```

(use existing SL/TP fixtures in `tests/test_simulated_exchange.py` for setup boilerplate)

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_simulated_exchange.py::test_execute_fill_includes_entry_price_for_stop_trigger tests/test_simulated_exchange.py::test_execute_fill_includes_entry_price_for_take_profit_trigger -v
```
Expected: assertion failures — entry_price is None.

- [ ] **Step 3: Capture entry_price in `_execute_fill`**

In `src/integrations/exchange/simulated.py:522-538` `_execute_fill`, modify:

```python
def _execute_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent:
    pos = self._positions[order.symbol]
    actual_amount = min(order.amount, pos.contracts)
    fill_price = ticker.bid if pos.side == "long" else ticker.ask
    captured_entry = pos.entry_price  # capture BEFORE close (pos may be popped from self._positions in _close_position_core line 437-438)
    pnl, fee, _ = self._close_position_core(
        order.symbol, pos.side, actual_amount, fill_price,
    )
    is_full_close = order.symbol not in self._positions
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return FillEvent(
        order_id=order.id, symbol=order.symbol, side=order.side,
        position_side=order.position_side, trigger_reason=order.order_type,
        fill_price=fill_price, amount=actual_amount, fee=fee,
        pnl=pnl,
        timestamp=now_ms,
        is_full_close=is_full_close,
        entry_price=captured_entry,
    )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_simulated_exchange.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(fee-vis): sim _execute_fill captures entry_price (SL/TP path, 67% main flow)"
```

---

## Task 8: Sim `_force_liquidate` captures entry_price + pnl_cap drift guard

**Files:**
- Modify: `src/integrations/exchange/simulated.py:599-616`
- Test: `tests/test_simulated_exchange.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_force_liquidate_includes_entry_price():
    """sim liquidation fill event carries position entry."""
    # set up high-leverage position, force liquidation via extreme price tick
    # assert liq_fill.entry_price == original_entry

@pytest.mark.asyncio
async def test_fill_event_entry_price_captured_before_pnl_cap():
    """drift guard: entry_price reflects original entry even when pnl_cap fires.

    Construct: leverage 100x position, market drops below liq, _close_position_core
    pnl_cap clamps pnl to -margin. entry_price MUST still equal original entry
    (not back-derived from clamped pnl).
    """
    # ... assert entry_price == 80000 even when actual pnl saturated at -margin
```

- [ ] **Step 2: Run tests to verify failure**

Expected: entry_price is None.

- [ ] **Step 3: Capture entry_price in `_force_liquidate`**

In `src/integrations/exchange/simulated.py:599-616`:

```python
def _force_liquidate(self, pos: _Position, symbol: str, price: float) -> FillEvent:
    contracts = pos.contracts
    captured_entry = pos.entry_price  # capture BEFORE close (pos may be popped from self._positions in _close_position_core line 437-438)
    pnl, fee, _ = self._close_position_core(
        symbol, pos.side, contracts, price, pnl_cap=True,
    )
    is_full_close = symbol not in self._positions
    order_id = str(uuid.uuid4())
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    logger.warning(f"LIQUIDATION: {pos.side} {contracts} {symbol} @ {price:.2f}")
    return FillEvent(
        order_id=order_id, symbol=symbol,
        side="sell" if pos.side == "long" else "buy",
        position_side=pos.side, trigger_reason="liquidation",
        fill_price=price, amount=contracts, fee=fee,
        pnl=pnl,
        timestamp=now_ms,
        is_full_close=is_full_close,
        entry_price=captured_entry,
    )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_simulated_exchange.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_exchange.py
git commit -m "feat(fee-vis): sim _force_liquidate captures entry_price + pnl_cap drift guard"
```

---

## Task 9: OKX `_close_order_entry_cache` data structure + register hook

**Files:**
- Modify: `src/integrations/exchange/okx.py` (OKXExchange `__init__` + new method)
- Test: `tests/test_okx_exchange.py`

- [ ] **Step 1: Write the failing test**

```python
def test_okx_close_order_entry_cache_initialized_empty():
    """OKXExchange.__init__ creates empty _close_order_entry_cache dict."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="x", secret="x", password="x", symbol="BTC/USDT:USDT", sandbox=True)
    assert ex._close_order_entry_cache == {}

def test_okx_register_close_order_entry_writes_to_cache():
    """register_close_order_entry stores (entry_price, monotonic_ts) tuple."""
    import time
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="x", secret="x", password="x", symbol="BTC/USDT:USDT", sandbox=True)
    t_before = time.monotonic()
    ex.register_close_order_entry("order123", 80000.0)
    t_after = time.monotonic()

    assert "order123" in ex._close_order_entry_cache
    entry, ts = ex._close_order_entry_cache["order123"]
    assert entry == 80000.0
    assert t_before <= ts <= t_after
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_okx_exchange.py::test_okx_close_order_entry_cache_initialized_empty tests/test_okx_exchange.py::test_okx_register_close_order_entry_writes_to_cache -v
```
Expected: AttributeError on `_close_order_entry_cache`.

- [ ] **Step 3: Add cache + override register method in OKXExchange**

In `src/integrations/exchange/okx.py` `OKXExchange.__init__` add (alongside other instance fields):

```python
        self._close_order_entry_cache: dict[str, tuple[float, float]] = {}
        """Maps close-direction order_id → (position.entry_price, monotonic_ts).
        Populated by register_close_order_entry (called by tools_execution.py
        close paths after create_order). Consumed by _parse_fill_event.
        Cleaned on cancel_order or via TTL sweep."""
```

In `OKXExchange` class body, override:

```python
    def register_close_order_entry(self, order_id: str, entry_price: float) -> None:
        """Cache position entry_price for close-direction order at submit time.

        Allows _parse_fill_event to attach exchange-layer-known entry_price to
        the FillEvent without relying on order response fields (OKX V5 avgPx
        on close orders is exit price, not position entry)."""
        import time
        self._close_order_entry_cache[order_id] = (entry_price, time.monotonic())
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_okx_exchange.py -v -k "close_order_entry"
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_okx_exchange.py
git commit -m "feat(fee-vis): OKX _close_order_entry_cache + register hook"
```

---

## Task 10: tools_execution.py wires `register_close_order_entry` for close-direction tools

**Files:**
- Modify: `src/agent/tools_execution.py:109-201` (close_position / set_stop_loss / set_take_profit)
- Test: `tests/test_tools_execution.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_close_position_calls_register_close_order_entry():
    """close_position registers entry per submitted close order."""
    from unittest.mock import AsyncMock, MagicMock
    from src.agent.tools_execution import close_position
    from src.integrations.exchange.base import Position, Order

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.fee_rate = 0.0005
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.1,
                 entry_price=80000.0, leverage=10, unrealized_pnl=10.0,
                 liquidation_price=72000.0, created_at=None),
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    deps.market_data = MagicMock()
    ticker = MagicMock(); ticker.bid = 80100.0; ticker.ask = 80110.0; ticker.last = 80105.0
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="oid1", symbol="BTC/USDT:USDT", side="sell", order_type="market",
        amount=0.1, price=None, trigger_price=None, status="open", fee=None, is_algo=False,
    ))
    deps.exchange.register_close_order_entry = MagicMock()
    deps.approval_gate = None
    deps.approval_enabled = False
    deps.db_engine = None  # _record_action no-op

    await close_position(deps, reasoning="test")

    deps.exchange.register_close_order_entry.assert_called_once_with("oid1", 80000.0)
```

Mirror tests for `set_stop_loss` / `set_take_profit`.

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_tools_execution.py::test_close_position_calls_register_close_order_entry -v
```
Expected: AssertionError — register_close_order_entry not called.

- [ ] **Step 3: Wire register call in close_position / set_stop_loss / set_take_profit**

In `src/agent/tools_execution.py` `close_position` (line 109-139), inside the for-loop after `create_order`:

```python
    for p in positions:
        order_side = "sell" if p.side == "long" else "buy"
        order = await deps.exchange.create_order(
            symbol=deps.symbol, side=order_side, order_type="market",
            amount=p.contracts,
            params={"reduceOnly": True},
        )
        deps.exchange.register_close_order_entry(order.id, p.entry_price)
        order_ids.append(order.id)
        # ... existing _record_action
```

In `set_stop_loss` (line 142-170), after `create_order`:

```python
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="stop", amount=p.contracts, price=price
    )
    deps.exchange.register_close_order_entry(order.id, p.entry_price)
```

In `set_take_profit` (line 173-201), same pattern.

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_tools_execution.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_execution.py tests/test_tools_execution.py
git commit -m "feat(fee-vis): wire register_close_order_entry in close-direction tools"
```

---

## Task 11: OKX `_parse_fill_event` pops cache + info.pnl gross semantic comment

**Files:**
- Modify: `src/integrations/exchange/okx.py:315-382`
- Test: `tests/test_okx_exchange.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_parse_fill_event_pops_entry_price_from_cache():
    """OKX close fill event: entry_price filled from cache by order_id."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="x", secret="x", password="x", symbol="BTC/USDT:USDT", sandbox=True)
    ex.register_close_order_entry("oid1", 80000.0)

    # synthesize close fill order_data (CCXT shape)
    order_data = {
        "id": "oid1", "symbol": "BTC/USDT:USDT", "side": "sell", "type": "market",
        "average": 80100.0, "filled": 0.1,
        "fee": {"cost": 4.005},
        "info": {"pnl": "10.0", "reduceOnly": "true"},
        "timestamp": 1234567890,
    }
    fill = await ex._parse_fill_event(order_data)
    assert fill.entry_price == 80000.0
    assert "oid1" not in ex._close_order_entry_cache  # popped

@pytest.mark.asyncio
async def test_parse_fill_event_cache_miss_yields_none_entry_price():
    """OKX close fill: cache miss → entry_price=None (graceful degrade)."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="x", secret="x", password="x", symbol="BTC/USDT:USDT", sandbox=True)
    # no register call — cache empty

    order_data = {
        "id": "oid_unknown", "symbol": "BTC/USDT:USDT", "side": "sell", "type": "market",
        "average": 80100.0, "filled": 0.1,
        "fee": {"cost": 4.005},
        "info": {"pnl": "10.0", "reduceOnly": "true"},
        "timestamp": 1234567890,
    }
    fill = await ex._parse_fill_event(order_data)
    assert fill.entry_price is None
```

- [ ] **Step 2: Run tests to verify failure**

Expected: AttributeError or assertion failure.

- [ ] **Step 3: Update `_parse_fill_event` to pop cache (含 OKX V5 文档静态依据)**

In `src/integrations/exchange/okx.py:315-382`, after computing `is_full_close` and before constructing `FillEvent`, add（注释引用 OKX V5 公开文档原文，避免循环引用 spec §6.0 — spec 自己只承诺 fixture 静态 assert）:

```python
        # Pop entry_price from cache for close fills.
        #
        # OKX V5 fillPnl semantic (per OKX V5 公开文档摘录):
        #   "fillPnl: Last filled profit and loss, applicable to orders which
        #    have a trade and aim to close position. It always is 0 in other
        #    conditions"
        #   ref: https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-fills
        #
        # Semantic：fillPnl = gross realized P&L of this fill, **excludes**
        # taker/maker fee (fee accounted separately via OKX `fee` / `fillFee`
        # field, returned in CCXT-normalized form as fee_info.cost above).
        # Matches sim convention (`_close_position_core` returns gross pnl).
        #
        # Cache miss (cached is None) → entry_price=None → cli renderer
        # degrades to fee + gross view.
        cached = self._close_order_entry_cache.pop(order_id, None)
        entry_price = cached[0] if cached is not None else None

        return FillEvent(
            order_id=order_id,
            symbol=symbol,
            side=side,
            position_side=position_side,
            trigger_reason=trigger_reason,
            fill_price=fill_price,
            amount=amount,
            fee=fee,
            pnl=pnl,
            timestamp=timestamp,
            is_full_close=is_full_close,
            entry_price=entry_price,
        )
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_okx_exchange.py -v -k "parse_fill_event"
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_okx_exchange.py
git commit -m "feat(fee-vis): OKX _parse_fill_event pops cache + info.pnl gross comment"
```

---

## Task 12: OKX `cancel_order` + TTL sweep for cache cleanup

**Files:**
- Modify: `src/integrations/exchange/okx.py:711-720` (cancel_order) + new TTL helper
- Test: `tests/test_okx_exchange.py`

**Cache lifecycle 状态机** (per spec §4.5b):
| 事件 | 动作 |
|---|---|
| submit close order | `cache[order_id] = (entry_price, now)` |
| fill | `cache.pop(order_id)` |
| cancel order | `cache.pop(order_id, None)` |
| TTL ceiling 24h | sweep, drop stale |
| process restart | empty (in-memory) |

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_okx_cancel_order_pops_close_entry_cache(monkeypatch):
    """cancel_order removes the order_id from _close_order_entry_cache."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="x", secret="x", password="x", symbol="BTC/USDT:USDT", sandbox=True)
    ex.register_close_order_entry("oid1", 80000.0)

    # Stub _client.cancel_order
    async def fake_cancel(*a, **kw): return None
    monkeypatch.setattr(ex._client, "cancel_order", fake_cancel)

    await ex.cancel_order("oid1", "BTC/USDT:USDT", is_algo=False)
    assert "oid1" not in ex._close_order_entry_cache

def test_okx_close_entry_cache_ttl_sweep_drops_stale():
    """_sweep_close_entry_cache_ttl drops entries older than TTL_HOURS."""
    import time
    from src.integrations.exchange.okx import OKXExchange, _CLOSE_ENTRY_CACHE_TTL_SECONDS
    ex = OKXExchange(api_key="x", secret="x", password="x", symbol="BTC/USDT:USDT", sandbox=True)
    # inject stale entry
    ex._close_order_entry_cache["stale_oid"] = (80000.0, time.monotonic() - _CLOSE_ENTRY_CACHE_TTL_SECONDS - 1)
    ex._close_order_entry_cache["fresh_oid"] = (80000.0, time.monotonic())

    ex._sweep_close_entry_cache_ttl()
    assert "stale_oid" not in ex._close_order_entry_cache
    assert "fresh_oid" in ex._close_order_entry_cache
```

- [ ] **Step 2: Run tests to verify failure**

Expected: function not defined / assertion failures.

- [ ] **Step 3: Implement TTL sweep + integrate into cancel_order**

At module top of `src/integrations/exchange/okx.py` add:

```python
_CLOSE_ENTRY_CACHE_TTL_SECONDS = 24 * 3600  # 24h ceiling per spec §4.5b
```

Update `cancel_order` (line 711-720):

```python
    @_retry()
    async def cancel_order(  # type: ignore[override]
        self, order_id: str, symbol: str, is_algo: bool = False,
    ) -> None:
        if is_algo:
            await self._client.cancel_order(
                order_id, symbol,
                params={"stop": True, "trigger": True, "algoId": order_id},
            )
        else:
            await self._client.cancel_order(order_id, symbol)
        self._close_order_entry_cache.pop(order_id, None)
```

Add sweep helper as a method on OKXExchange:

```python
    def _sweep_close_entry_cache_ttl(self) -> None:
        """Drop _close_order_entry_cache entries older than TTL.

        Periodic-call hook — invoke from existing housekeeping loop or
        as a defensive sweep at fetch_open_orders boundaries (cheap).
        Keeps cache bounded across long SL/TP idle windows.
        """
        import time
        now = time.monotonic()
        stale = [oid for oid, (_, ts) in self._close_order_entry_cache.items()
                 if now - ts > _CLOSE_ENTRY_CACHE_TTL_SECONDS]
        for oid in stale:
            self._close_order_entry_cache.pop(oid, None)
```

Call sweep at start of `fetch_open_orders` (cheap, naturally periodic):

```python
    @_retry()
    async def fetch_open_orders(self, symbol: str) -> list[Order]:  # type: ignore[override]
        self._sweep_close_entry_cache_ttl()
        # ... existing impl
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_okx_exchange.py -v -k "cache"
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_okx_exchange.py
git commit -m "feat(fee-vis): OKX cancel_order + TTL sweep for close_entry_cache cleanup"
```

---

## Task 13: Wizard fee_rate full fill (OKX 分支 + sim 文字 + WizardResult 收紧 + summary 去 gate)

**Files:**
- Modify: `src/cli/wizard.py:21-42, 65-95, 128-133, 288-299`
- Test: `tests/test_wizard.py`

**Merge rationale**（修订自 audit Moderate #2）：拆 Task 13/14 会留 broken window — Task 13 移除 `_show_summary` 的 `if ex == "simulated":` gate 后到 Task 14 OKX 实填之间，OKX wizard 跑到 summary 一步会 `None * 100 → TypeError`. 合并为单 commit 消除中间态。

- [ ] **Step 1: Write the failing tests**

```python
def test_wizard_result_fee_rate_type_is_float_not_optional():
    """WizardResult.fee_rate annotation is `float`, not `float | None`."""
    from src.cli.wizard import WizardResult
    import typing
    hints = typing.get_type_hints(WizardResult)
    assert hints["fee_rate"] is float

def test_wizard_simulated_branch_prompt_says_per_side():
    """Simulated fee_rate prompt text says 'Fee rate (% per side)' (was 'Fee rate (%)').

    UX: clarifies entry fee + exit fee are both rate × notional.
    """
    # snapshot/spy on FloatPrompt.ask args (use monkeypatch on rich.prompt.FloatPrompt.ask)
    # assert called with prompt containing "per side"

def test_wizard_okx_branch_prompts_for_fee_rate(monkeypatch):
    """OKX path collects fee_rate (default 0.05% = OKX BTC perp regular tier taker)."""
    from src.cli.wizard import _step_exchange
    # ... mock Prompt/Confirm/FloatPrompt to drive OKX path
    # assert returned dict["fee_rate"] is non-None float
    # assert FloatPrompt.ask was called with prompt mentioning 'OKX live 用户请按 VIP tier 实填'

def test_wizard_summary_shows_fee_for_okx_path():
    """_show_summary appends fee% for OKX path (not gated by exchange_type=='simulated')."""
    from src.cli.wizard import _show_summary
    # synthesize data dict with exchange_type='okx', fee_rate=0.0005
    # capture console output, assert "fee: 0.05%" string present
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_wizard.py -v -k "fee_rate or per_side or okx_branch or summary"
```
Expected: failures.

- [ ] **Step 3: Implement all changes in wizard.py — single commit**

In `src/cli/wizard.py:25` change:

```python
    fee_rate: float | None          # simulated only
```

to:

```python
    fee_rate: float                 # both paths, wizard-enforced
```

In `_step_exchange` simulated branch (line 71-85), change FloatPrompt prompt text:

```python
fee_pct = FloatPrompt.ask("  Fee rate (% per side)", default=default_fee_pct, console=console)
```

In `_step_exchange` OKX branch (line 128-133), replace:

```python
    return {
        "exchange_type": "okx",
        "fee_rate": None,
        "initial_balance": balance,
        "api_credentials": api_credentials,
    }
```

with:

```python
    # OKX path fee_rate (iter-tool-opt-fee-visibility):
    # Currently user-input self-estimated (matches simulated path UX).
    # Future: fetch via OKX /api/v5/account/trade-fee endpoint to get
    # the user's actual taker rate by VIP tier; remove the manual input.
    # See spec §7 follow-up "iter-tool-opt-okx-fee-rate-auto-fetch".
    okx_fee_pct = FloatPrompt.ask(
        "  Fee rate (% per side) "
        "[default 0.05 = OKX BTC perp regular tier taker; "
        "OKX live 用户请按 VIP tier 实填]",
        default=0.05, console=console,
    )
    return {
        "exchange_type": "okx",
        "fee_rate": okx_fee_pct / 100,
        "initial_balance": balance,
        "api_credentials": api_credentials,
    }
```

In `_show_summary` (line 288-299), remove the `if ex == "simulated":` gate around fee display:

```python
    ex_label = data["exchange_type"]
    fee_pct = data["fee_rate"] * 100
    table.add_row("Exchange", f"{ex_label} (fee: {fee_pct:.2f}%)")
```

OKX 分支已实填非 None（同一 commit 内），summary 渲染安全。

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_wizard.py -v
```
Expected: 4 new tests PASS, no existing test breaks.

- [ ] **Step 5: Commit (single commit — full wizard fee_rate fill)**

```bash
git add src/cli/wizard.py tests/test_wizard.py
git commit -m "feat(fee-vis): wizard fee_rate full fill — OKX branch + sim 'per side' + WizardResult tighten + summary covers OKX"
```

---

## Task 14: (deprecated, merged into Task 13)

Plan 期 audit Moderate #2 暴露 Task 13/14 拆分会留 broken window（OKX summary 渲染 TypeError）；已合并为单 commit Task 13。本节保留为 placeholder 维持 task 编号引用稳定（Task 15+ 编号不变）。No-op — skip to Task 15.

---

## Task 15: session_manager `_restore_session` legacy NULL fee_rate sub-step

**Files:**
- Modify: `src/cli/session_manager.py:82-180`
- Test: `tests/test_session_manager.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_restore_session_prompts_for_fee_rate_on_legacy_null(monkeypatch):
    """_restore_session detects sessions.fee_rate IS NULL → prompts user → updates DB.

    Returned WizardResult.fee_rate is non-None.
    """
    # 1) seed DB with a session row, fee_rate=NULL
    # 2) monkeypatch FloatPrompt.ask to return 0.05
    # 3) call _restore_session
    # 4) assert WizardResult.fee_rate == 0.0005
    # 5) re-read DB row → fee_rate field updated
```

- [ ] **Step 2: Run test to verify failure**

Expected: WizardResult constructed with fee_rate=None, raises in build_services later (or AssertionError here).

- [ ] **Step 3: Add NULL detection sub-step**

In `src/cli/session_manager.py:82` `_restore_session`, after loading `s` (line 92-93 `async with get_session...` exits — `s` becomes detached) and before `WizardResult(...)` construction (around line 167), add:

```python
    # Legacy NULL fee_rate detection (iter-tool-opt-fee-visibility §4.5):
    # Pre-iter sessions may have NULL fee_rate. Prompt user to set it
    # before constructing WizardResult (which requires float).
    resolved_fee_rate = s.fee_rate
    if resolved_fee_rate is None:
        from rich.prompt import FloatPrompt
        console.print("[yellow]Legacy session has no fee_rate configured.[/]")
        fee_pct = FloatPrompt.ask(
            "  Set fee rate (% per side) for this session",
            default=0.05, console=console,
        )
        resolved_fee_rate = fee_pct / 100
        async with get_session(engine) as db_sess:
            await db_sess.execute(
                update(Session).where(Session.id == session_id).values(
                    fee_rate=resolved_fee_rate,
                )
            )
            await db_sess.commit()
```

然后在 `WizardResult(...)` 构造（line 167）把 `fee_rate=s.fee_rate` 改为 `fee_rate=resolved_fee_rate` — 用局部变量传，**不要**给 detached `s` 写 attribute（避免依赖 detached ORM mutation 语义）。

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_session_manager.py -v -k "fee_rate"
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli/session_manager.py tests/test_session_manager.py
git commit -m "feat(fee-vis): session resume sub-step prompts for fee_rate on legacy NULL"
```

---

## Task 16: build_services raise + drift guard + RuntimeConfig/TradingDeps wiring (含 P4 capture)

**Files:**
- Modify: `src/cli/app.py:787-914` (`build_services`)
- Modify: `src/cli/app.py:967-973` (`run()` Phase 5b — `_capture_session_system_prompt` call site)
- Test: `tests/test_drift_p4_capture_paths.py` (existing drift guard test file)

**Critical context — dual RuntimeConfig construction**：`_compute_max_wake` 的 docstring 明示 "Single source of truth shared by build_services and P4 session-level capture"；现存 `test_p4_runtime_config_matches_build_services` drift guard 证明这是已知 dual-source 风险。本 task 必须**同时**注入 fee_rate 到两个构造点，并扩展 drift guard 覆盖 `taker_fee_rate`。否则 `sessions.system_prompt` 字段渲染 Fee 段时 `runtime.taker_fee_rate` 会 fallback 到 default 0.0005，与 user 实输 fee_rate 脱钩，AC5（spec §8.1）假阳性。

- [ ] **Step 1: Write the failing tests**

```python
def test_build_services_raises_on_none_fee_rate():
    """build_services fails-loud when WizardResult.fee_rate is None.

    Defense in depth — wizard sub-step (Task 15) is primary recovery; this is
    bottom layer for manual SQL / restored backup / migration bug.
    """
    from src.cli.app import build_services
    # ... synthesize WizardResult with fee_rate=None (bypass type via dataclass.replace)
    with pytest.raises(ValueError, match="fee_rate"):
        build_services(...)

def test_build_services_drift_guard_runtime_vs_deps_fee_rate():
    """RuntimeConfig.taker_fee_rate must equal TradingDeps.fee_rate after build."""
    # build_services with fee_rate=0.001
    # assert returned deps.fee_rate == 0.001 AND build trace's runtime.taker_fee_rate == 0.001

def test_p4_runtime_config_matches_build_services_fee_rate():
    """drift guard extension: P4 capture-path RuntimeConfig must carry the same
    taker_fee_rate as build_services-internal RuntimeConfig.

    Construct via run()-side dual code paths (or extracted helper) and assert
    rc_capture.taker_fee_rate == rc_build.taker_fee_rate == result.fee_rate.
    """
    # Mirror existing test_p4_runtime_config_matches_build_services pattern;
    # extend assertion to taker_fee_rate field.
```

- [ ] **Step 2: Run tests to verify failure**

Expected: failures.

- [ ] **Step 3: Modify build_services**

In `src/cli/app.py:787` add at top of `build_services` body:

```python
def build_services(
    result: WizardResult,
    engine,
    session_id: str,
    sc: SessionConsole,
    settings: Settings,
):
    """Build exchange, deps, agent, budget from WizardResult."""
    if result.fee_rate is None:
        raise ValueError(
            "Session has no fee_rate configured. This usually means a legacy "
            "session was loaded but the resume flow's fee_rate sub-step did "
            "not run. To recover: (a) restart the CLI to trigger wizard resume "
            "flow; (b) or manually UPDATE sessions SET fee_rate=0.0005 WHERE "
            "id=<your_session_id> in DB and restart."
        )
    # ... existing exchange branch
```

In the `runtime_config` construction (around line 829) include `taker_fee_rate`:

```python
    runtime_config = RuntimeConfig(
        wake_max_minutes=max_wake,
        taker_fee_rate=result.fee_rate,
    )
```

In the `TradingDeps(...)` construction (line 885-904) add `fee_rate` while preserving **all 18 existing fields verbatim** (do not collapse under `...,`):

```python
    deps = TradingDeps(
        symbol=result.symbol,
        timeframe=result.timeframe,
        market_data=market_data,
        exchange=exchange,
        technical=technical,
        memory=memory,
        session_id=session_id,
        db_engine=engine,
        approval_gate=approval_gate,
        approval_enabled=result.approval_enabled,
        initial_balance=result.initial_balance,
        metrics=metrics_service,
        news=news_service,
        macro=macro_service,
        crypto_etf=crypto_etf_service,
        onchain=onchain_service,
        wake_min_minutes=1,
        wake_max_minutes=max_wake,
        fee_rate=result.fee_rate,  # iter-tool-opt-fee-visibility
    )
```

After existing `assert deps.wake_max_minutes == ...` drift guard (line 908-911) add:

```python
    assert deps.fee_rate == runtime_config.taker_fee_rate, (
        f"fee_rate drift: TradingDeps {deps.fee_rate} vs "
        f"RuntimeConfig {runtime_config.taker_fee_rate} must match"
    )
```

- [ ] **Step 4: Patch P4 capture path RuntimeConfig (Critical fix)**

In `src/cli/app.py:967-973` `run()` Phase 5b, also inject `taker_fee_rate`:

```python
    # ── Phase 5b: P4 system_prompt capture ──
    runtime_config_for_capture = RuntimeConfig(
        wake_max_minutes=_compute_max_wake(result.scheduler_interval_min),
        taker_fee_rate=result.fee_rate,
    )
    await _capture_session_system_prompt(
        engine, session_id, result.persona, runtime_config_for_capture,
    )
```

This closes the dual-source drift root cause (per `_compute_max_wake` docstring "Single source of truth shared by build_services and P4 session-level capture"). Without this fix, `sessions.system_prompt` field renders `Fee: taker 0.050%` (default) regardless of user's actual `result.fee_rate` — AC5 假阳性.

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/test_drift_p4_capture_paths.py -v
pytest tests/test_cli_app.py -v -k "build_services"
```
Expected: PASS, including the new `test_p4_runtime_config_matches_build_services_fee_rate`.

- [ ] **Step 6: Commit**

```bash
git add src/cli/app.py tests/test_drift_p4_capture_paths.py
git commit -m "feat(fee-vis): build_services raise + drift guard + inject RuntimeConfig/TradingDeps fee_rate (含 P4 capture)"
```

---

## Task 17: persona `_build_layer1` Market Context — Fee 双行 segment

**Files:**
- Modify: `src/agent/persona.py:78-114`
- Test: `tests/test_persona.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_layer1_market_context_renders_taker_fee_rate():
    """Market Context segment includes 'Fee: taker X.XXX% per side (set at session start).'"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    rc = RuntimeConfig(taker_fee_rate=0.001)
    text = _build_layer1(rc)
    assert "Fee: taker 0.100% per side (set at session start)." in text
    assert "Round-trip cost on a position = entry_fee + exit_fee" in text
    assert "≈ 2 × fee_rate × notional" in text

def test_market_context_segment_no_evaluation_words():
    """Market Context segment removes 'frequent small trades' / 'erode capital' nudges.

    drift guard scope: only the '## Market Context' segment of _build_layer1
    output. Layer 3 (personality / trading_style) MAY contain compliant
    evaluation descriptors (e.g., 'patient trader') and is not part of this guard.
    """
    from src.agent.persona import _build_layer1, RuntimeConfig
    text = _build_layer1(RuntimeConfig())

    # extract '## Market Context' segment up to next '##' header
    market_ctx_start = text.index("## Market Context")
    next_h2 = text.index("##", market_ctx_start + len("## Market Context"))
    market_ctx_segment = text[market_ctx_start:next_h2]

    forbidden = ["frequent small trades", "erode capital", "friction costs alone"]
    for word in forbidden:
        assert word not in market_ctx_segment, f"'{word}' still present in Market Context"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_persona.py -v -k "market_context"
```
Expected: failures.

- [ ] **Step 3: Rewrite Market Context segment**

In `src/agent/persona.py:78` `_build_layer1`, **保留完整 f-string body**（含 `{runtime.wake_max_minutes}` 插值在 `## Cross-Tool Behavior` 段 line 92 等其它已有 f-string 占位），仅替换 `## Market Context` 段那一句。具体改法：

定位 line 83（`Market Context` 段唯一的长句），把 `"You trade USDT-margined perpetual futures ... frequent small trades can erode capital through friction costs alone."` 替换为：

```
You trade USDT-margined perpetual futures (no expiry date). The exchange uses one-way position mode — you cannot hold long and short positions on the same symbol simultaneously. To reverse direction, close your current position first. Leverage cannot be changed while holding a position.

Fee: taker {fee_pct:.3f}% per side (set at session start).
Round-trip cost on a position = entry_fee + exit_fee ≈ 2 × fee_rate × notional.
```

并在函数顶部加 `fee_pct = runtime.taker_fee_rate * 100` 局部变量（紧接 `def _build_layer1` 第一行）。

⚠️ **不要**复制后面的 `## Cross-Tool Behavior` 段当字面文本——line 92 现存 `Allowed range: next 1-{runtime.wake_max_minutes} min` 是 f-string 插值，必须保持插值形态。简单做法：用 Edit tool 仅 surgical-replace line 83 + 在 def 顶部插入 fee_pct 变量；不要重写整个 function body。

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_persona.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "feat(fee-vis): persona Layer1 Market Context — Fee 双行 segment, drop nudge sentence"
```

---

## Task 18: get_position Fee & Breakeven section + gross labels

**Files:**
- Modify: `src/agent/tools_perception.py:222-340` (`get_position`)
- Modify: `src/agent/trader.py:128-142` (wrapper docstring)
- Test: `tests/test_get_position.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_renders_fee_breakeven_section_long():
    """Long position renders Fee & Breakeven section with formula and signed distance."""
    # construct deps with fee_rate=0.001, position long entry=80000 contracts=0.5
    # current_price=80200
    out = await get_position(deps, "BTC/USDT:USDT")
    assert "=== Fee & Breakeven ===" in out
    assert "Entry fee paid: ~-40.00 USDT (= entry × contracts × rate)" in out
    # breakeven = 80000 × (1 + 0.002) = 80160
    # distance = 80200 - 80160 = +40
    assert "Breakeven: 80,160.00" in out
    assert "[current 80,200.00, +40 pts]" in out
    assert "= 80,000.00 × (1 + 2 × fee_rate) [long round-trip taker]" in out

@pytest.mark.asyncio
async def test_renders_fee_breakeven_section_short():
    """Short position uses (1 − 2r) formula."""
    # short entry=80000 contracts=0.5, current_price=79800
    # breakeven = 80000 × (1 - 0.002) = 79840
    # distance = 79840 - 79800 = +40 (signed: negative when current is above breakeven)
    out = await get_position(deps, "BTC/USDT:USDT")
    assert "Breakeven: 79,840.00" in out
    assert "= 80,000.00 × (1 − 2 × fee_rate) [short round-trip taker]" in out

@pytest.mark.asyncio
async def test_fee_breakeven_section_does_not_render_fee_rate_number():
    """drift guard: rate digits only in system prompt (single-source principle).

    Fee & Breakeven section MUST NOT print fee_rate as a number — only entry_fee
    + breakeven + formula caption with `fee_rate` symbol.
    """
    out = await get_position(deps, "BTC/USDT:USDT")
    fb_start = out.index("=== Fee & Breakeven ===")
    fb_end = out.index("===", fb_start + len("=== Fee & Breakeven ==="))
    fb_segment = out[fb_start:fb_end]
    assert "0.001" not in fb_segment  # rate digit
    assert "0.05%" not in fb_segment
    assert "0.10%" not in fb_segment

@pytest.mark.asyncio
async def test_entry_fee_matches_recompute_formula():
    """Entry fee = entry_price × contracts × fee_rate (math identity)."""
    # entry=81878.6, contracts=0.366, fee_rate=0.001
    # expected = 29.96756 → format as -29.97
    assert "Entry fee paid: ~-29.97 USDT" in out

@pytest.mark.asyncio
async def test_position_section_includes_gross_label():
    """Position section: Unrealized line carries '(gross)' label."""
    assert re.search(r"Unrealized: [\+\-]\d+\.\d+ USDT \(gross\)", out)

@pytest.mark.asyncio
async def test_pnl_section_includes_gross_label():
    """PnL section: PnL line carries 'gross' label."""
    assert re.search(r"PnL: [\+\-]\d+\.\d+ USDT gross", out)
```

For `test_entry_fee_after_add_position_equals_cumulative_actual` and `test_entry_fee_after_part_close_equals_remaining_equiv_cost`, exercise sim_exchange end-to-end (open → add → fetch_position → render).

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_get_position.py -v
```
Expected: failures.

- [ ] **Step 3: Add Fee & Breakeven section + gross labels in get_position**

In `src/agent/tools_perception.py:282-311` `_render_position_core`, modify Unrealized + PnL lines:

```python
        pos_lines.append(f"Unrealized: {p.unrealized_pnl:+.2f} USDT (gross)")
        # ...
        pnl_lines.append(
            f"PnL: {p.unrealized_pnl:+.2f} USDT gross ({pnl_pct_inner:+.2f}% of initial capital)"
        )
        # else branch:
        pnl_lines.append(f"PnL: {p.unrealized_pnl:+.2f} USDT gross")
```

After `_render_position_core()` returns, before the `try:` block at line 313 (or right after `sections = _render_position_core()` at line 340) — actually, per spec §3.2 implementation hint, add Fee & Breakeven section **immediately after `_render_position_core` and BEFORE the try/except gather block** so it's not collateral damage of ticker/balance failure:

Move the Fee & Breakeven append to between line 311's return and line 313's try. Restructure as follows:

```python
    sections = _render_position_core()

    # Fee & Breakeven section (independent of ticker/balance/orders gather):
    # depends only on position.entry_price + deps.fee_rate. Distance bracket
    # uses ticker.last with its own try/except → fallback omits distance only,
    # not the section.
    entry_fee = p.entry_price * p.contracts * deps.fee_rate
    if p.side == "long":
        breakeven = p.entry_price * (1 + 2 * deps.fee_rate)
        sign_str = "+"
        side_label = "long"
    else:
        breakeven = p.entry_price * (1 - 2 * deps.fee_rate)
        sign_str = "−"
        side_label = "short"

    fb_lines = ["=== Fee & Breakeven ==="]
    fb_lines.append(f"Entry fee paid: ~-{entry_fee:.2f} USDT (= entry × contracts × rate)")
    # try fetch ticker just for distance (separate from main gather)
    try:
        distance_ticker = await deps.market_data.get_ticker(symbol)
        if distance_ticker.last > 0:
            if p.side == "long":
                distance_pts = distance_ticker.last - breakeven
            else:
                distance_pts = breakeven - distance_ticker.last
            fb_lines.append(
                f"Breakeven: {breakeven:,.2f} "
                f"[current {distance_ticker.last:,.2f}, {distance_pts:+.0f} pts]"
            )
        else:
            fb_lines.append(f"Breakeven: {breakeven:,.2f}")
    except Exception:
        fb_lines.append(f"Breakeven: {breakeven:,.2f}")
    fb_lines.append(
        f"  = {p.entry_price:,.2f} × (1 {sign_str} 2 × fee_rate) "
        f"[{side_label} round-trip taker]"
    )
    sections.append("\n".join(fb_lines))

    try:
        ticker, balance, ohlcv_df, open_orders, contract_size, mark_price = await asyncio.gather(
            ...
```

- [ ] **Step 4: Update wrapper docstring in trader.py**

In `src/agent/trader.py:128-142`, append fee/breakeven explanation to existing docstring:

```python
    @tool
    async def get_position(ctx: RunContext[TradingDeps], symbol: str | None = None) -> str:
        """Get current position details with risk exposure context.

        Includes Risk exposure (notional / margin / mark price / liquidation
        distance in ATR(1h) multiples — 1h is the fixed baseline regardless of
        session trading style) and Exit orders section (SL/TP distances from
        both entry and last price). Liquidation distance is computed against
        mark price.

        Output also includes Fee & Breakeven section: entry_fee paid (= entry × contracts × rate)
        and breakeven price = entry × (1 ± 2 × fee_rate). Use breakeven as the fee-aware decision
        anchor when judging whether to hold or close.

        Args:
            symbol: trading symbol (defaults to session symbol).
        """
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/test_get_position.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_get_position.py
git commit -m "feat(fee-vis): get_position adds Fee & Breakeven section + gross labels"
```

---

## Task 19: get_performance gross-based labels + wrapper docstring

**Files:**
- Modify: `src/agent/tools_perception.py:642-707` (output labels)
- Modify: `src/agent/trader.py:188-198` (wrapper docstring)
- Test: `tests/test_get_performance.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_trade_stats_includes_gross_based_label():
    """Trade Stats labels each metric as (gross-based) until net iter lands."""
    out = await get_performance(deps_with_metrics)
    assert "Win Rate" in out  # may use 'Win:' format
    assert "(gross-based)" in out
    # at minimum: profit_factor / max_drawdown / best/worst trade lines

def test_get_performance_wrapper_docstring_lists_fee_fields_and_gross_caveat():
    """Wrapper docstring lists Total Fees field and gross-based caveat."""
    from src.agent.trader import create_trader_agent
    # extract get_performance wrapper docstring via agent._function_toolset
    # assert "Total Fees" in docstring AND "gross-based" in docstring
```

- [ ] **Step 2: Run tests to verify failure**

Expected: failures.

- [ ] **Step 3: Update output labels in get_performance**

In `src/agent/tools_perception.py:697-705` replace `stats_section` template:

```python
    stats_section = (
        f"=== Trade Stats ===\n"
        f"Total Trades: {metrics.total_trades} | Win: {metrics.winning_trades} "
        f"({metrics.win_rate:.1%}, gross-based) | Loss: {metrics.losing_trades}\n"
        f"Avg Win: {metrics.avg_win:+.2f} USDT | Avg Loss: {metrics.avg_loss:.2f} USDT (gross-based)\n"
        f"Profit Factor: "
        f"{'N/A (no losses)' if metrics.profit_factor == float('inf') else f'{metrics.profit_factor:.2f} (gross-based)'}\n"
        f"Max Drawdown: {f'-{metrics.max_drawdown_pct:.1f}' if metrics.max_drawdown_pct > 0 else '0.0'}% (gross-based equity)\n"
        f"Best Trade: {metrics.best_trade:+.2f} USDT | Worst Trade: {metrics.worst_trade:.2f} USDT (gross-based)"
    )
```

- [ ] **Step 4: Update wrapper docstring in trader.py**

In `src/agent/trader.py:188-198` replace:

```python
    @tool
    async def get_performance(ctx: RunContext[TradingDeps]) -> str:
        """Show session trading performance — balance, return, cumulative fees, win rate, drawdown.

        Returns:
            str: Two sections.

            === Trading Performance === — Initial Balance, Current Balance,
            Total Return (% + USDT, incl. unrealized), Realized PnL (gross, before fees),
            Total Fees (cumulative across all fills).

            === Trade Stats === — Total Trades, Win Rate, Avg Win/Loss, Profit Factor,
            Max Drawdown (equity-peak-based), Best/Worst Trade. All gross-based until
            iter-tool-opt-net-pnl-metrics lands.

            Related: get_trade_journal (decision timeline).

        Degradation: 'No completed trades yet.' if zero trades.
        'No metrics service available.' if metrics service is missing.
        """
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/test_get_performance.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_get_performance.py
git commit -m "feat(fee-vis): get_performance gross-based labels + wrapper docstring rewrite"
```

---

## Task 20: open_position est. entry fee + wrapper docstring

**Files:**
- Modify: `src/agent/tools_execution.py:66-106`
- Modify: `src/agent/trader.py:428-451`
- Test: `tests/test_tools_execution.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_open_position_output_includes_est_entry_fee():
    """open_position return string includes Est. entry fee with notional × rate caption."""
    # mock deps with fee_rate=0.0005, balance free=1000, leverage=10, ticker.last=80000
    # expected quantity = (100*10)/80000 = 0.125
    # notional = 80000 * 0.125 = 10000; est_fee = 10000 * 0.0005 = 5.00
    out = await open_position(deps, side="long", position_pct=10, leverage=10, reasoning="t")
    assert "Est. entry fee: ~-5.00 USDT" in out
    assert "(notional ~10,000.00 × ~0.050%)" in out

def test_open_position_wrapper_docstring_mentions_fee():
    """Wrapper docstring preserves fill-timing sentence + appends fee mention."""
    # extract wrapper docstring; assert both:
    # "Position fills via market order; you will receive a fill notification"
    # AND "Entry incurs taker fee = notional × fee_rate. Fill notification reports actual fee."
```

- [ ] **Step 2: Run tests to verify failure**

Expected: failures.

- [ ] **Step 3: Modify open_position return + wrapper docstring**

In `src/agent/tools_execution.py:103-106` replace return:

```python
    notional = ticker.last * quantity
    est_entry_fee = notional * deps.fee_rate
    return (
        f"Order submitted: {side} {quantity:.6f} @ ~{ticker.last:.2f}, {leverage}x | ID: {order.id}\n"
        f"Est. entry fee: ~-{est_entry_fee:.2f} USDT "
        f"(notional ~{notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
        f"You will be notified when filled."
    )
```

In `src/agent/trader.py:428-451` append to docstring (before Args:):

```python
        """Open a new market-order position.

        Position fills via market order; you will receive a fill notification
        when execution completes (separate trigger, not in the same cycle).
        Stop loss and take profit place against an existing position, so they
        require the fill notification.

        Entry incurs taker fee = notional × fee_rate. Fill notification reports actual fee.

        Args:
            side: 'long' or 'short'.
            position_pct: percent of free balance to allocate (0-100).
            leverage: leverage multiplier (cannot be changed while holding position).
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_tools_execution.py -v -k "open_position"
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_execution.py src/agent/trader.py tests/test_tools_execution.py
git commit -m "feat(fee-vis): open_position submit output adds Est. entry fee"
```

---

## Task 21: close_position round-trip net + approval message net view + wrapper docstring

**Files:**
- Modify: `src/agent/tools_execution.py:109-139`
- Modify: `src/agent/trader.py:453-465`
- Test: `tests/test_tools_execution.py` + ripple to any test asserting old `Close N position(s), PnL: X.XX` substring

**Approval-message format ripple guard**（修订自 audit Moderate #3）：本 task 改 `_check_approval` 入参 action_desc 从 `"Close N position(s), PnL: X.XX"` 到 `"Close N position(s), PnL: X.XX gross / Y.YY net (round-trip)"`. 任何现有 test 用 substring 匹配旧格式都会断。Step 0 强制 grep 锁清单。

- [ ] **Step 0: Grep ripple targets — lock affected test list before code change**

```bash
grep -rn "Close.*position.*PnL" tests/
grep -rn "action_desc.*Close" tests/
grep -rn "approval.*Close" tests/
```

For each match, classify:
- A 类（不受影响）: 完全无 PnL substring 断言
- B 类（受影响）: 用 substring 断言旧格式 — 必须更新为新格式 `"PnL: X.XX gross / Y.YY net (round-trip)"`

Lock B-类清单后才进 Step 1，避免边改边 fix 反复 break-fix-break。

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_close_position_output_includes_round_trip_net_pnl():
    """close_position output: Est. exit fee + Est. net PnL (round-trip) breakdown."""
    # Position long entry=80000 contracts=0.5, ticker.bid=80100.0, fee_rate=0.0005
    # entry_fee = 80000*0.5*0.0005 = 20; exit_notional = 80100*0.5 = 40050; exit_fee = 20.025
    # unrealized = 50.0
    # net = -20 + 50 - 20.025 = +9.975 ≈ +9.98
    out = await close_position(deps, reasoning="t")
    assert "Est. exit fee: ~-20.03 USDT" in out
    assert "Est. net PnL: ~+9.98 USDT" in out
    assert "round-trip = entry fee ~-20.00 + unrealized +50.00 + est. exit fee ~-20.03" in out

@pytest.mark.asyncio
async def test_close_position_approval_message_includes_gross_and_net():
    """Approval gate message format: 'PnL: X gross / Y net (round-trip)'."""
    # spy on approval_gate.check call args; assert action_desc contains "gross" and "net (round-trip)"
```

- [ ] **Step 2: Run tests to verify failure**

Expected: failures.

- [ ] **Step 3: Restructure close_position to fetch ticker before approval**

Replace `src/agent/tools_execution.py:109-139` `close_position` body:

```python
async def close_position(deps: TradingDeps, reasoning: str) -> str:
    """Close all open positions."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No positions to close."

    order_side = "sell" if positions[0].side == "long" else "buy"
    if deps.exchange.has_pending_market_order(deps.symbol, side=order_side):
        return "A close order is already pending. Wait for fill confirmation."

    # Fee + net PnL estimation BEFORE approval gate (so approval message shows both views)
    ticker = await deps.market_data.get_ticker(deps.symbol)
    total_unrealized = sum(p.unrealized_pnl for p in positions)
    total_contracts = sum(p.contracts for p in positions)
    total_entry_fee = sum(p.entry_price * p.contracts * deps.fee_rate for p in positions)
    # Use bid/ask matching actual market close fill price (sim _fill_market_close convention)
    est_fill_price = ticker.bid if positions[0].side == "long" else ticker.ask
    est_exit_notional = est_fill_price * total_contracts
    est_exit_fee = est_exit_notional * deps.fee_rate
    est_net_pnl = -total_entry_fee + total_unrealized - est_exit_fee

    action_desc = (
        f"Close {len(positions)} position(s), "
        f"PnL: {total_unrealized:+.2f} gross / {est_net_pnl:+.2f} net (round-trip)"
    )
    approved = await _check_approval(deps, "close", action_desc, 0, 0)
    if not approved:
        return "Close rejected by human approval."

    order_ids = []
    for p in positions:
        order_side = "sell" if p.side == "long" else "buy"
        order = await deps.exchange.create_order(
            symbol=deps.symbol, side=order_side, order_type="market",
            amount=p.contracts,
            params={"reduceOnly": True},
        )
        deps.exchange.register_close_order_entry(order.id, p.entry_price)
        order_ids.append(order.id)
        await _record_action(
            deps, action="close_position", order_id=order.id,
            side=p.side, reasoning=reasoning,
        )

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

(`register_close_order_entry` call already added in Task 10 — keep idempotent.)

In `src/agent/trader.py:453-465` append to docstring:

```python
    @tool
    async def close_position(ctx: RunContext[TradingDeps], reasoning: str) -> str:
        """Close all open positions via market order.

        Position closure fills via market order; you will receive a fill
        notification when execution completes (separate trigger).

        Close incurs taker fee on exit. Submit output includes est. exit fee and est. round-trip net PnL.

        Args:
            reasoning: brief description of your decision logic (e.g., 'TP target hit', 'thesis invalidated').
        """
```

- [ ] **Step 4: Update Step 0 B-类受影响 tests for new action_desc format**

For each B-类 test from Step 0 inventory, update substring assertion from `"Close 1 position(s), PnL: -56.29"` style to `"Close 1 position(s), PnL: -56.29 gross / -86.20 net (round-trip)"` — verify with new fee_rate-aware fixture.

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/test_tools_execution.py -v -k "close_position"
pytest tests/ -v -k "approval and close"  # ripple verification
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_execution.py src/agent/trader.py tests/
git commit -m "feat(fee-vis): close_position adds round-trip net PnL + approval message net view + ripple test updates"
```

---

## Task 22: place_limit_order est. entry fee + wrapper docstring

**Files:**
- Modify: `src/agent/tools_execution.py:584-588`
- Modify: `src/agent/trader.py:715-735`
- Test: `tests/test_tools_execution.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_place_limit_order_output_includes_est_entry_fee_if_filled():
    """place_limit_order output: Est. entry fee if filled (uses limit price for notional)."""
    # price=80000, position_pct=10, leverage=10, balance.free=1000
    # quantity = (100*10)/80000 = 0.125; notional = 10000; est_fee = 5.0
    out = await place_limit_order(deps, side="long", price=80000, position_pct=10, leverage=10, reasoning="t")
    assert "Est. entry fee if filled: ~-5.00 USDT" in out
    assert "(notional ~10,000.00 × ~0.050%)" in out
    assert "Note: This tool only submits the order" in out  # preserved
```

- [ ] **Step 2: Run test to verify failure**

Expected: failure.

- [ ] **Step 3: Modify place_limit_order return + wrapper docstring**

In `src/agent/tools_execution.py:584-588` replace return:

```python
    notional = price * quantity
    est_entry_fee = notional * deps.fee_rate
    return (
        f"Limit order placed: {side} {quantity:.6f} @ {price:.2f}, "
        f"{actual_leverage}x{leverage_suffix} | ID: {order.id}\n"
        f"Est. entry fee if filled: ~-{est_entry_fee:.2f} USDT "
        f"(notional ~{notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
        "Note: This tool only submits the order — it does not mean the order has been filled."
    )
```

In `src/agent/trader.py:715-735` append to docstring (before Args:):

```python
    @tool
    async def place_limit_order(
        ctx: RunContext[TradingDeps],
        side: str,
        price: float,
        position_pct: float,
        leverage: int,
        reasoning: str,
    ) -> str:
        """Place a limit order at a specific price (e.g., buy at support level).

        Limit fill incurs maker or taker fee depending on fill condition.

        Args:
            side: 'long' or 'short'.
            price: limit price.
            position_pct: percent of free balance to allocate (0-100).
            leverage: leverage multiplier.
            reasoning: brief description of your decision logic.
        """
```

- [ ] **Step 4: Run test to verify pass**

```bash
pytest tests/test_tools_execution.py -v -k "place_limit_order"
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_execution.py src/agent/trader.py tests/test_tools_execution.py
git commit -m "feat(fee-vis): place_limit_order submit output adds Est. entry fee if filled"
```

---

## Task 23: Execution-tool docstring drift guard (no evaluation words)

**Files:**
- Test only: `tests/test_tools_execution.py`

**Guard-only task**：本 task 是 docstring drift guard，依赖 Task 20/21/22 已经把 wrapper docstring 改成无 evaluation 词。**不走 RED→GREEN 节奏** — 写完 test 直接 expected pass（Task 20/21/22 已先行实施）。如果 fail 说明前面 task 引入了禁止词，回头修 docstring。

- [ ] **Step 1: Write the test**

```python
def test_execution_tool_docstrings_no_evaluation_words():
    """Execution tools docstrings do not include evaluation/nudge words.

    F-dim drift guard. Approved fee-related vocabulary: 'taker fee', 'fee_rate',
    'notional', 'round-trip', 'gross', 'net'. Forbidden:
    - 'erode' / 'friction' / 'frequent small trades' (Layer 1 nudge family)
    - 'should' / 'must' / 'avoid' / 'careful' (evaluative directives)
    """
    # 提取 trader.py wrapper docstrings for open_position / close_position /
    # set_stop_loss / set_take_profit / place_limit_order / cancel_order
    # 断言 forbidden 词不在
```

- [ ] **Step 2: Run test to verify pass (or fix doc as needed)**

```bash
pytest tests/test_tools_execution.py::test_execution_tool_docstrings_no_evaluation_words -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_tools_execution.py
git commit -m "test(fee-vis): drift guard for execution-tool docstring evaluation words"
```

---

## Task 24: cli/app.py fill notification rendering uses event.entry_price

**Files:**
- Modify: `src/cli/app.py:472-479` (within `run_agent_cycle`)
- Test: `tests/test_cli_app.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_fill_notification_open_includes_fee():
    """Open fill (pnl=None) — message includes 'Fee: -X.XX USDT' (signed via -event.fee)."""
    # Build context FillEvent: pnl=None, fee=29.97
    # render via run_agent_cycle prompt construction (extract the msg branch)
    assert "Fee: -29.97 USDT" in prompt

@pytest.mark.asyncio
async def test_fill_notification_close_full_includes_round_trip_net_uses_entry_price_field():
    """Full close fill: round-trip net computed from event.entry_price (not back-derived).

    pnl=−56.29, fee=29.91, entry_price=81878.6, amount=0.366, deps.fee_rate=0.001
    → entry_fee_recompute = 81878.6 × 0.366 × 0.001 = 29.97
    → round_trip_net = -29.97 + (-56.29) - 29.91 = -116.17
    """
    assert "PnL: -56.29 (gross) / -116.17 (this fill, equiv-round-trip)" in prompt
    assert "Fee: -29.91 USDT" in prompt

@pytest.mark.asyncio
async def test_fill_notification_close_partial_omits_round_trip():
    """Part close (is_full_close=False) — Fee + gross only, no round-trip line."""
    # is_full_close=False, pnl=750.0, fee=41.0
    assert "Fee: -41.00 USDT" in prompt
    assert "PnL: +750.00 USDT (gross)" in prompt
    assert "round-trip" not in prompt

@pytest.mark.asyncio
async def test_fill_notification_label_uses_this_fill_equiv_round_trip():
    """Label is '(this fill, equiv-round-trip)' — not 'this close 总账'."""
    # full close render
    assert "(this fill, equiv-round-trip)" in prompt

@pytest.mark.asyncio
async def test_fill_notification_pnl_cap_scenario_uses_actual_entry_price():
    """drift guard: pnl_cap fired in sim → entry_price field still original entry,
    NOT back-derived from clamped pnl. Round-trip net uses correct entry_fee."""
    # Construct event with pnl=-margin (capped), entry_price=80000.0 (pre-cap)
    # Verify entry_fee_recompute uses 80000.0 not back-derived from clamped pnl
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_cli_app.py -v -k "fill_notification"
```
Expected: failures.

- [ ] **Step 3: Modify fill notification rendering**

In `src/cli/app.py:472-479` replace the conditional fill block:

```python
    if trigger_type == "conditional" and context is not None:
        msg = (
            f"\n\nIMPORTANT EVENT: {context.trigger_reason} triggered "
            f"— {context.symbol} {context.amount} @ {context.fill_price}"
        )
        if context.pnl is None:
            # Open fill — fee only
            msg += f", Fee: {-context.fee:+.2f} USDT"
        elif context.is_full_close and context.entry_price is not None:
            # Full close fill — fee + gross + equiv-round-trip net
            entry_fee_recompute = context.entry_price * context.amount * deps.fee_rate
            round_trip_net = -entry_fee_recompute + context.pnl - context.fee
            msg += (
                f", Fee: {-context.fee:+.2f} USDT, "
                f"PnL: {context.pnl:+.2f} (gross) / "
                f"{round_trip_net:+.2f} (this fill, equiv-round-trip)"
            )
        else:
            # Part close OR full close with no entry_price (OKX cache miss)
            msg += (
                f", Fee: {-context.fee:+.2f} USDT, "
                f"PnL: {context.pnl:+.2f} USDT (gross)"
            )
        prompt += msg
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/test_cli_app.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli/app.py tests/test_cli_app.py
git commit -m "feat(fee-vis): fill notification renders fee + round-trip net via event.entry_price"
```

---

## Task 25: Full suite verification + AC sign-off

**Files:**
- No code changes — verification only.

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ --timeout=60 2>&1 | tail -30
```
Expected: all PASS. Pre-iter baseline = 1694 passed (per memory `project_tradebot_status`); new tests sum to ~47（spec §6.1 32 + plan-期扩展 test：Task 1 +3 / Task 2 +1 / Task 4 +1 / Task 9 +2 / Task 10 +3 / Task 12 +2 / Task 13 +4 / Task 15 +1 / Task 16 +3 含新 P4 drift guard / 其余 task 散落）+ Task 0 inventory B-类 fixture migration（数量 plan 期 lock）。Final count 应在 **1694 + ~50 ± 容差**。

- [ ] **Step 2: Manual AC verification (per spec §8.1)**

Verify each implementation AC manually by reading test output / running spot-check commands:

- AC1: `get_position` output contains `=== Fee & Breakeven ===` — covered by `test_renders_fee_breakeven_section_long/short`
- AC2: fill notification renders fee — covered by `test_fill_notification_*`
- AC3: `close_position` submit contains `Est. round-trip net` — covered by `test_close_position_output_includes_round_trip_net_pnl`
- AC4: Wizard creates session with non-None fee_rate (sim + OKX) — covered by `test_wizard_*_fee_rate_input_*`
- AC5: System prompt P4 snapshot contains Fee 双行 — verify by querying a freshly-created session's `sessions.system_prompt` field after a brief run, OR by `_build_layer1` snapshot test (Task 17)
- AC6: full pytest passes — Step 1

- [ ] **Step 3: Inspect a fresh sim session's system prompt to confirm Fee segment lands in P4 capture (含具体百分比 drift guard)**

```bash
# After running a fresh session briefly (manual smoke) with wizard fee_rate input
# = 0.05% (default), query:
sqlite3 data/tradebot.db \
  "SELECT system_prompt FROM sessions ORDER BY rowid DESC LIMIT 1" \
  | grep "Fee: taker"
```

Expected output 必须**完全匹配** wizard 输入的具体百分比：

```
Fee: taker 0.050% per side (set at session start).
```

如果 user wizard 输入 `0.1%`（如 sim #8），则期望 `Fee: taker 0.100% per side (set at session start).`. **不要**用宽泛 `grep "Fee:"` 验证存在性 — 那会让 Critical #1（P4 capture 路径漏注 taker_fee_rate）假阳性通过. 必须验证数字与 wizard 实输一致.

补充：自动化覆盖见 Task 16 `test_p4_runtime_config_matches_build_services_fee_rate` drift guard test，本 step 是 manual smoke 双重确认.

(Skip this step in non-interactive runs; user will run smoke separately per memory `feedback_long_walltime_experiments`.)

- [ ] **Step 4: No commit (verification only)**

---

## Self-Review Checklist (executed at plan write time + audit-revised)

**1. Spec coverage:** ✓
- §3.1 system prompt → Task 17
- §3.2 get_position Fee & Breakeven → Task 18
- §3.3 get_performance labels + docstring → Task 19
- §3.4 open_position fee → Task 20
- §3.5 close_position round-trip + approval message → Task 21
- §3.6 place_limit_order fee → Task 22
- §3.7 fill notification → Task 24
- §4.1 RuntimeConfig + DEFAULT_TAKER_FEE_RATE → Task 1
- §4.2 TradingDeps → Task 2
- §4.3 build_services raise + drift guard → Task 16（含 P4 capture path 二次注入，audit Critical #1 修订）
- §4.4 fill notification → Task 24
- §4.5 wizard sim text + OKX branch + summary + WizardResult tighten → **Task 13 单 commit 合并**（audit Moderate #2 修订；Task 14 deprecated placeholder）
- §4.5 session resume sub-step → Task 15（用局部变量 `resolved_fee_rate`，避免 detached ORM mutation）
- §4.5b FillEvent.entry_price → Task 3; sim 3 paths → Tasks 6/7/8; OKX cache lifecycle → Tasks 9/10/11/12
- §4.6 SimulatedExchange __init__ raise + fixture migration → Tasks 0, 5
- §6.0 Pre-gate 1 (info.pnl gross fixture) → Task 11（含 OKX V5 公开文档原文摘录，audit Minor #10 修订）；Pre-gate 2 (cache lifecycle) → Tasks 9-12
- §6.1 ~47 new tests distributed across Tasks 1-24
- §6.2 fixture grep → Task 0

**2. Placeholder scan:** No "TBD" / "implement later" / unspecified test code. Each test has skeleton; complex setups reference existing test fixture patterns by name.

**3. Type consistency:**
- `register_close_order_entry(order_id: str, entry_price: float) -> None` — used identically in Tasks 4, 9, 10, 21
- `_close_order_entry_cache: dict[str, tuple[float, float]]` — consistent in Tasks 9, 11, 12
- `entry_price: float | None` — consistent across base.py + sim three paths + OKX
- `DEFAULT_TAKER_FEE_RATE = 0.0005` — used in Task 1 (persona) and Task 2 (TradingDeps default), referenced in Task 5 (fixtures), Task 13 (wizard default)
- Approval message format `"PnL: X gross / Y net (round-trip)"` — consistent between Task 21 close_position rewrite and Task 24 fill notification rendering

**4. Audit-driven revisions applied:**

| 风险等级 | 议题 | 修订 task | 修订内容 |
|---|---|---|---|
| 🔴 Critical | #1 P4 capture path 漏注 taker_fee_rate（AC5 假阳性）| Task 16 / Task 25 Step 3 | 加 Step 4 注入 `runtime_config_for_capture.taker_fee_rate` + 加 `test_p4_runtime_config_matches_build_services_fee_rate` drift guard + Task 25 grep 改为验证具体百分比数字 |
| 🟡 Moderate | #2 Task 13/14 拆分 broken window | Task 13 / Task 14 | 合并为单 commit Task 13；Task 14 deprecated placeholder |
| 🟡 Moderate | #3 close_position approval 格式 ripple 未列举 | Task 21 | 加 Step 0 grep 现有测试 + B-类清单更新流程 |
| 🟢 Minor | #4 Task 17 f-string 插值意外被改成字面 | Task 17 Step 3 | 明示"保留完整 f-string body 含 {runtime.wake_max_minutes} 插值，仅替换 line 83" |
| 🟢 Minor | #5 capture rationale 注释失准（pnl_cap 不影响 entry_price）| Task 6/7/8 Step 3 注释 | 改为 "(pos may be popped from self._positions in _close_position_core line 437-438)" |
| 🟢 Minor | #6 detached ORM attribute mutation | Task 15 Step 3 | 用局部变量 `resolved_fee_rate` 传 WizardResult，不写 detached `s.fee_rate` |
| 🟢 Minor | #7 Task 23 倒置 TDD | Task 23 顶部 note | 明示 "Guard-only task, 不走 RED→GREEN" |
| 🟢 Minor | #8 测试计数低估 | Task 25 Step 1 | 改 "1694 + ~50 ± 容差"（原 ~30 偏低）|
| 🟢 Minor | #10 Pre-gate 1 注释循环引用 spec | Task 11 Step 3 | 注释引用 OKX V5 公开文档原文摘录 + URL，避免循环引用 |
| 🟢 Minor | #11 Task 16 RuntimeConfig/TradingDeps 省略既有字段 | Task 16 Step 3 | 展开列 18 个 TradingDeps 字段全名（不用 `..., # existing fields` 写法）|
| 🔵 Info | #9 OKX REST-only fallback boundary | （未吸纳进 plan）| 属 spec §9 数据局限，本 iter 实施层不影响 sim/OKX 主路径；W3+ OKX live 上线前另立 caveat |

---

## Execution Notes

**Order rationale:**
- Phase 1 (Tasks 1-4): foundation types — no behavior changes, safe to land independently
- Phase 2 (Tasks 5-8): sim exchange — Task 5 raise depends on Task 0 inventory; Tasks 6-8 use FillEvent.entry_price (Task 3)
- Phase 3 (Tasks 9-12): OKX exchange — independent of sim Phase 2, can be parallelized after Task 4
- Phase 4 (Tasks 13-16): wizard / session manager / build_services — Task 16 build_services depends on RuntimeConfig.taker_fee_rate (Task 1) + TradingDeps.fee_rate (Task 2) + WizardResult.fee_rate float (Task 13) + session resume sub-step (Task 15)
- Phase 5 (Task 17): persona Layer 1 — depends on RuntimeConfig.taker_fee_rate (Task 1)
- Phase 6-7 (Tasks 18-23): tools output — depend on TradingDeps.fee_rate (Task 2) + register hook (Task 10)
- Phase 8 (Task 24): fill notification — depends on FillEvent.entry_price (Task 3) populated by sim (Tasks 6-8) and OKX (Tasks 9-12)
- Phase 9 (Task 25): final verification

**Key invariants to preserve while editing:**
- FillEvent.entry_price for open fills always None (per spec C4 + B-2)
- Sim path entry_price captured BEFORE _close_position_core (avoid pnl_cap mutation)
- Fee symbol invariant: FillEvent.fee always positive (cost); render with `f"{-event.fee:+.2f}"` to get sign correct
- Single-source: rate digits only in system prompt; status tools (get_position) carry no rate digits; derived tools (open/close/limit submit) carry rate digits as transparency caption
- close_position `register_close_order_entry` called per submitted order (one-way mode → 1 order, but loop tolerates >1)
