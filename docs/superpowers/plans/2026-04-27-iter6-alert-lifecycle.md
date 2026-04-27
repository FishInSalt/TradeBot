# Iter 6 Alert Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** P0-5 stale alert clearance via base-layer `_dispatch_fill_event` hook + new `cancel_price_level_alert` agent tool, aligning sim/okx behavior on close fill alert hygiene.

**Architecture:** FillEvent gains explicit `is_full_close: bool` field; sim infers dynamically via `_close_position_core` post-state; OKX uses three-source fusion (reduceOnly | trigger_reason | posSide+side). Base layer `_dispatch_fill_event` orchestrates SRP-split clear + invoke methods; both sub-classes route through it. Plan-stage hard gate (Task 0) validates OKX `info.reduceOnly` assumption before any code change.

**Tech Stack:** Python 3.13 / pydantic-ai 1.78 / asyncio / pytest / dataclasses / OKX swap demo / SQLAlchemy (TradeAction table).

**Source spec:** `docs/superpowers/specs/2026-04-27-iter6-alert-lifecycle-design.md`

**Branch:** `feature/iter-t2-1-alert-lifecycle` (already created; spec commit `c4c5c11` landed)

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `tests/_fixtures.py` | `make_fill_event(*, is_full_close=False, **overrides) -> FillEvent` factory; default in factory layer (NOT dataclass) so callers must explicitly opt into close semantics |
| `tests/test_alert_lifecycle.py` | All 25 new Iter 6 tests (unit 11 + integration 14) |
| `tests/fixtures/okx_watch_orders_market_close.json` | Task 0 real demo capture |
| `tests/fixtures/okx_watch_orders_sl_fill.json` | Task 0 real demo capture |
| `tests/fixtures/okx_watch_orders_tp_fill.json` | Task 0 real demo capture |
| `tests/fixtures/okx_watch_orders_liquidation.json` | Task 0 hand-constructed (demo unreachable, §6.4 future real-fixture candidate) |

### Modified files

| Path | Change |
|---|---|
| `src/integrations/exchange/base.py` | Add `import logging` / module logger / `__init__._fill_callback` field / `on_fill` non-empty impl / FillEvent +is_full_close field / `clear_level_alerts_by_symbol` helper / `_dispatch_fill_event` + 3-method SRP split |
| `src/integrations/exchange/simulated.py` | Delete `_fill_callback` field (line 79) / delete `on_fill` override (line 776-777) / fill 5 FillEvent constructors with explicit `is_full_close` (3 dynamic, 2 static) / replace call site (line 673-675) |
| `src/integrations/exchange/okx.py` | Delete `_fill_callback` field (line 121) / delete `on_fill` override (line 141-142) / add `_infer_is_full_close` helper / wire into `_parse_fill_event` (line 322) / replace call site (line 260-265) |
| `src/agent/tools_execution.py` | Add `cancel_price_level_alert(deps, alert_id, reasoning)` _impl |
| `src/agent/trader.py` | Add `@tool` wrapper for cancel_price_level_alert / REGISTERED_TOOL_NAMES 31→32 / count comment (10)→(11) / insert after `add_price_level_alert` |
| `src/cli/display.py` | Register `_EXECUTION_SUCCESS_PREFIXES["cancel_price_level_alert"] = "Price level alert cancelled"` |
| `tests/test_exchange.py` (line 204, 212) | Migrate 2 existing FillEvent constructors to `make_fill_event(...)` factory |
| `tests/test_trader_agent.py` (line 85-86) | Drift guard hardcode sync: `== 31 → == 32`, `(20+10+1) → (20+11+1)` |

---

## Task 0: OKX Demo Reality Check (🛑 HARD GATE)

**This task gates all subsequent tasks.** Do NOT proceed to Task 1 until §4.3 OKX three-source fusion design is validated OR a remediation path (A or B) is committed to.

**Files:**
- Run: `scripts/iter2b_smoke_test.py` pattern (manual test, not committed code)
- Create: `tests/fixtures/okx_watch_orders_market_close.json`
- Create: `tests/fixtures/okx_watch_orders_sl_fill.json`
- Create: `tests/fixtures/okx_watch_orders_tp_fill.json`
- Create: `tests/fixtures/okx_watch_orders_liquidation.json` (hand-constructed)

**Wall-time ceiling:** ~4-5h (scenarios 1-3 parallel after open). Scenario 2/3 each 4h hard timeout.

- [ ] **Step 1: Verify OKX demo credentials available**

Run: `grep -E "OKX_DEMO_(API_KEY|API_SECRET|API_PASSPHRASE)" .env 2>/dev/null && echo OK`
Expected: All three env vars present (Iter 2b setup, see memory `project_iter2b_okx_algo_normalization`).

If missing: Set up demo account credentials per `.env.example` `OKX_DEMO_*` keys before continuing.

- [ ] **Step 2: Patch debug capture hook into okx.py**

Add a temporary print/log into `okx.py:_watch_orders_loop` line 247-265 to dump every `order_data["info"]` raw JSON when `status == "closed"`:
```python
if status == "closed":
    print(f"[TASK0_CAPTURE] {order_data.get('id')}: {json.dumps(order_data, default=str)}")
```

This captures fill events from all three sub-experiments below. Revert this hook in Step 10 (final cleanup).

**Critical sequencing note**: OKX automatically cancels associated algo orders (SL/TP) when a position closes (reduce-only orders + net_mode → position size 0 → algo auto-cancel). Therefore we cannot reuse one position for all three captures — sub-experiments must each open their own fresh position.

- [ ] **Step 3: Sub-experiment 1A — market close fill capture (immediate)**

1. Open BTC-USDT-SWAP long at current market price, ~0.01 BTC notional (via OKX demo UI or `scripts/iter2b_smoke_test.py` adapted).
2. Immediately call `close_position(reasoning="Task 0 1A capture")` (or equivalent demo UI close).
3. Capture the printed `[TASK0_CAPTURE]` line — this is scenario 1 (market close).

Wall-time: ~1-2 minutes. Save to `tests/fixtures/okx_watch_orders_market_close.json` in Step 6.

- [ ] **Step 4: Sub-experiment 1B — SL trigger fill capture (4h timeout)**

Open a **fresh** long position (~0.01 BTC). On this NEW position:
1. Set ONLY a stop-loss at `current_price * 0.999` (i.e., -0.1% trigger distance).
2. Wait for price to drop ≥ 0.1% to trigger SL fill.
3. Capture the printed `[TASK0_CAPTURE]` line for the SL fill.

**4h hard timeout**: If SL doesn't trigger within 4h (price drift never crosses -0.1%), proceed to Step 8 with only scenarios 1A + 1C — flag scenario 1B as missing in the outcome decision.

Save to `tests/fixtures/okx_watch_orders_sl_fill.json` in Step 6.

- [ ] **Step 5: Sub-experiment 1C — TP trigger fill capture (4h timeout)**

Open a **fresh** long position (~0.01 BTC) on a separate account/symbol if you want to parallelize with 1B; otherwise sequential after 1B completes. On this position:
1. Set ONLY a take-profit at `current_price * 1.001` (i.e., +0.1% trigger distance).
2. Wait for price to rise ≥ 0.1% to trigger TP fill.
3. Capture the printed `[TASK0_CAPTURE]` line.

**4h hard timeout**: same handling as Step 4.

**Parallelization**: If using a single demo account with one BTC-USDT-SWAP symbol (net mode merges positions), 1B and 1C must run sequentially. If using two symbols (BTC + ETH) or two accounts, 1B and 1C can run in parallel — total wall-time still ≤ 4h.

Save to `tests/fixtures/okx_watch_orders_tp_fill.json` in Step 6.

- [ ] **Step 6: Save real captures to fixture files**

Format the captured JSON into:
- `tests/fixtures/okx_watch_orders_market_close.json` (scenario 1)
- `tests/fixtures/okx_watch_orders_sl_fill.json` (scenario 2)
- `tests/fixtures/okx_watch_orders_tp_fill.json` (scenario 3)

Each file: top-level JSON object matching CCXT unified order_data structure.

- [ ] **Step 7: Hand-construct liquidation fixture**

Based on scenarios 1-3 + OKX API docs (https://www.okx.com/docs-v5/en/#websocket-api-private-channel-order-channel), construct `tests/fixtures/okx_watch_orders_liquidation.json`. Mark in spec §6.4 as "synthetic, replace with real capture if observation/prod liquidation occurs".

Required fields: `id`, `symbol`, `side`, `type` (likely "market"), `status` ("closed"), `filled`, `average`, `info.reduceOnly` (likely True per OKX behavior), `info.posSide` ("net"), `info.pnl`.

- [ ] **Step 8: Decision — does spec §4.3 design hold?**

Inspect captured fixtures. Three outcomes:

**Outcome A (✅ design holds):** All 3 real captures (+ synthetic liquidation) have `info.reduceOnly == True` (or `"true"` string). → Proceed to Task 1 with current spec §4.3 unchanged.

**Outcome B (⚠️ algo OK but market_close missing reduceOnly):** SL/TP captures have reduceOnly, market_close doesn't. → Implement **Remediation A**:
1. Add `params: dict | None = None` kwarg to `BaseExchange.create_order` abstract signature
2. Sim override transparently ignores params
3. OKX override merges params into internal params dict (after `tdMode: "isolated"`)
4. `tools_execution.py:close_position` line 115-117 passes `params={"reduceOnly": True}`
5. All test mocks of `create_order` updated to accept new kwarg
6. Add 4 unit tests covering signature change

Estimated +0.5 day on top of existing plan.

**Outcome C (❌ all scenarios missing reduceOnly):** Implement **Remediation B**:
1. Subscribe to OKX positions WS channel
2. Maintain `self._cached_positions: dict[symbol, Position]`
3. `_parse_fill_event` compares fill amount vs cached size for precise full-close
4. Add 6+ unit tests covering cache sync + race conditions

Estimated +1-1.5 day on top of existing plan.

- [x] **Step 9: ✅ COMPLETED — Task 0 outcome captured (2026-04-28)**

Outcome: **B + 信号 4** (see spec §4.3.1.1).

Commit:
```bash
git add tests/fixtures/okx_watch_orders_*.json scripts/iter6_task0_capture.py scripts/iter6_diag_ticker.py docs/superpowers/specs/2026-04-27-iter6-alert-lifecycle-design.md
git commit -m "$(cat <<'EOF'
docs(iter6): Task 0 OKX reality check — Outcome B + 信号 4

Captured 4 real watch_orders fill events from OKX demo:
- 1A market close (no params): all signals miss → needs Remediation A
- 1B SL trigger: ordType=limit, reduceOnly=false, but algoId non-empty
- 1C TP trigger: same as 1B
- 1D market close with params={"reduceOnly": True}: reduceOnly=true echoed

Decisions landed in spec §4.3.1.1:
- Add signal 4 (info.algoId non-empty) to _infer_is_full_close
- Implement Remediation A in new Task 5b: extend BaseExchange.create_order
  with params kwarg, sim/okx override, close_position passes
  params={"reduceOnly": True}

Liquidation fixture remains synthetic (demo unreachable, §6.4 W3+
candidate). Helper scripts (iter6_task0_capture.py, iter6_diag_ticker.py)
committed for reproducibility (e.g. partial close tool 落地后 revisit).

Tangential finding: OKX demo ticker.last vs mark_price drifts ~1.67%;
algo trigger validation uses mark price (not last as 51280 message says).
Captured as memory `project_okx_demo_mark_vs_last_drift`. Helper script
uses mark price for trigger computation.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [x] **Step 10: ✅ N/A — no source modification**

Original plan assumed direct `print` patch in okx.py (which would need revert). Helper script `iter6_task0_capture.py` uses `monkey-patch` instead, so no source code modification was made. `git diff src/integrations/exchange/okx.py` returns nothing throughout Task 0.

---

## Task 1: Base Infrastructure (logging + _fill_callback uplift)

**Files:**
- Modify: `src/integrations/exchange/base.py:1-7` (imports), `:88-91` (__init__), `:136-138` (on_fill)
- Modify: `src/integrations/exchange/simulated.py:79` (delete field), `:776-777` (delete override)
- Modify: `src/integrations/exchange/okx.py:121` (delete field), `:141-142` (delete override)
- Test: `tests/test_alert_lifecycle.py` (new file, will accumulate tests across tasks)

These are dispatch dependencies (see spec §3.2 "前置改动声明"), not independent DRY refactor.

- [ ] **Step 1: Add import logging + logger to base.py**

Edit `src/integrations/exchange/base.py:1-8`:

```python
from __future__ import annotations
import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Add _fill_callback field to BaseExchange.__init__**

Edit `src/integrations/exchange/base.py:88-91`:

```python
class BaseExchange(ABC):
    def __init__(self):
        self._price_level_alerts: list[dict] = []
        self._latest_price: float | None = None
        self._alert_service: Any | None = None
        self._fill_callback: Callable[['FillEvent'], Awaitable[None]] | None = None
```

- [ ] **Step 3: Replace on_fill empty pass with non-empty impl**

Edit `src/integrations/exchange/base.py:136-138`:

```python
def on_fill(self, callback: Callable[['FillEvent'], Awaitable[None]]) -> None:
    """注册 fill 回调。"""
    self._fill_callback = callback
```

- [ ] **Step 4: Delete _fill_callback field from SimExchange**

Edit `src/integrations/exchange/simulated.py:79` — delete the line:

```python
self._fill_callback: Callable[[FillEvent], Awaitable[None]] | None = None
```

- [ ] **Step 5: Delete on_fill override from SimExchange**

Find and delete the entire override at `simulated.py:776-777` (and surrounding decorator/docstring if any). The base class impl now handles it.

- [ ] **Step 6: Delete _fill_callback field from OKXExchange**

Edit `src/integrations/exchange/okx.py:121` — delete the line setting `self._fill_callback = None`.

- [ ] **Step 7: Delete on_fill override from OKXExchange**

Find and delete the entire override at `okx.py:141-142`.

- [ ] **Step 8: Run test suite — verify no regression from uplift**

Run: `uv run pytest tests/ -x -q`
Expected: 857 passed, 1 skipped (pre-Iter 6 baseline). All on_fill registrations from `cli/app.py:520` should still work because base class now provides the impl.

If FAIL: investigate which test calls `on_fill` and verify base class non-empty impl is being used.

- [ ] **Step 9: Commit**

```bash
git add src/integrations/exchange/base.py src/integrations/exchange/simulated.py src/integrations/exchange/okx.py
git commit -m "$(cat <<'EOF'
refactor(exchange): uplift _fill_callback / on_fill to BaseExchange

Pre-requisite for Iter 6 _dispatch_fill_event base-layer dispatch.
Cannot reference self._fill_callback from base methods if field
lives in subclasses. Also adds module logger to base.py.

- base.py: import logging, module logger, _fill_callback field, on_fill non-empty impl
- simulated.py: delete _fill_callback field + on_fill override
- okx.py: delete _fill_callback field + on_fill override

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: FillEvent +is_full_close + Factory (TDD red)

**Files:**
- Modify: `src/integrations/exchange/base.py:204-214` (FillEvent dataclass)
- Create: `tests/_fixtures.py`

This task INTENTIONALLY breaks all 2 existing FillEvent constructors. Task 3 is the green phase.

- [ ] **Step 1: Add is_full_close field to FillEvent (no default)**

Edit `src/integrations/exchange/base.py:203-214` (FillEvent dataclass starts at `@dataclass` decorator line 203):

```python
@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: str
    position_side: str
    trigger_reason: str    # market / limit / stop / take_profit / liquidation
    fill_price: float
    amount: float
    fee: float
    pnl: float | None      # 已实现盈亏（开仓时 None）
    timestamp: int
    is_full_close: bool    # NEW — True iff fill 把该 symbol 持仓清零（仅触发 alert 清理）
```

- [ ] **Step 2: Run test suite — verify expected breakage**

Run: `uv run pytest tests/ -x -q 2>&1 | head -20`
Expected: FAIL with `TypeError: __init__() missing 1 required positional argument: 'is_full_close'` from `tests/test_exchange.py:204` and `:212`.

This confirms TDD red phase: dataclass enforces explicit is_full_close at all 2 existing FillEvent construction sites + 5 production sim sites + 1 production okx site.

- [ ] **Step 3: Create tests/_fixtures.py with factory helpers**

Create `tests/_fixtures.py` with three helpers (FillEvent + SimExchange + Ticker + OKXExchange):

```python
"""Shared test fixtures for Iter 6+ tests."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from src.integrations.exchange.base import FillEvent, Ticker


def make_fill_event(
    *,
    order_id: str = "test-order-1",
    symbol: str = "BTC/USDT:USDT",
    side: str = "buy",
    position_side: str = "long",
    trigger_reason: str = "market",
    fill_price: float = 50000.0,
    amount: float = 0.01,
    fee: float = 0.5,
    pnl: float | None = None,
    timestamp: int = 1700000000000,
    is_full_close: bool = False,
) -> FillEvent:
    """Construct a FillEvent for tests with sensible defaults.

    Defaults to is_full_close=False (open semantics). Tests covering
    full-close scenarios MUST explicitly pass is_full_close=True.
    See spec §4.1 — default in factory not dataclass to force explicit
    intent at call site (silent corruption protection).
    """
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
    )


def make_ticker(
    *,
    symbol: str = "BTC/USDT:USDT",
    last: float = 50000.0,
    bid: float | None = None,
    ask: float | None = None,
    timestamp: int = 1700000000000,
) -> Ticker:
    """Construct a Ticker for sim _process_tick calls."""
    if bid is None:
        bid = last - 5.0
    if ask is None:
        ask = last + 5.0
    return Ticker(
        symbol=symbol, last=last, bid=bid, ask=ask,
        high=last * 1.02, low=last * 0.98,
        base_volume=1000.0, timestamp=timestamp,
    )


def make_sim_exchange(
    initial_balance: float = 10000.0,
    fee_rate: float = 0.0005,
    symbol: str = "BTC/USDT:USDT",
):
    """Construct a SimulatedExchange for tests without async start().

    Mirrors tests/test_simulated_exchange.py:_make_exchange pattern. Pre-populates
    state that start() would normally set, plus a default _latest_ticker so
    create_order paths can run without first injecting a tick.
    """
    from src.integrations.exchange.simulated import SimulatedExchange

    config = MagicMock()
    config.fee_rate = fee_rate
    config.precision = {"BTC/USDT:USDT": 3, "ETH/USDT:USDT": 2}

    ex = SimulatedExchange(
        config=config, db_engine=None, session_id="test-session", symbol=symbol,
    )
    ex._free_usdt = initial_balance
    ex._used_usdt = 0.0
    ex._frozen_usdt = 0.0
    ex._positions = {}
    ex._pending_orders = []
    ex._leverage = {}
    ex._latest_ticker = make_ticker(symbol=symbol)
    ex._running = True
    return ex


def make_okx_exchange():
    """Construct OKXExchange via __new__ (skip ccxt client init for unit tests).

    Manually sets state that BaseExchange.__init__ + OKXExchange.__init__
    would normally set. Use only for pure-function tests on _infer_is_full_close
    or _dispatch_fill_event that don't touch network / ccxt client.
    """
    from src.integrations.exchange.okx import OKXExchange

    ex = OKXExchange.__new__(OKXExchange)
    # BaseExchange.__init__ state (set after Task 1 uplift)
    ex._price_level_alerts = []
    ex._latest_price = None
    ex._alert_service = None
    ex._fill_callback = None
    # OKXExchange-specific minimal state for tests
    ex._symbol = "BTC/USDT:USDT"
    ex._sandbox = True
    ex._running = False
    ex._ws_connected = False
    ex._pnl_fetch_timeout = 1.0
    ex._seen_order_ids = {}
    ex._seen_order_ids_max = 10000
    return ex
```

- [ ] **Step 4: Verify factory imports correctly**

Run: `uv run python -c "from tests._fixtures import make_fill_event; print(make_fill_event())"`
Expected: Prints `FillEvent(order_id='test-order-1', ..., is_full_close=False)`.

- [ ] **Step 5: Commit (TDD red checkpoint)**

```bash
git add src/integrations/exchange/base.py tests/_fixtures.py
git commit -m "$(cat <<'EOF'
feat(FillEvent): add is_full_close field + make_fill_event factory

is_full_close: True iff fill closes the symbol's position to 0
contracts. Triggers alert lifecycle clearance in _dispatch_fill_event
(introduced in later task). No default value on dataclass — forces
all callers to explicitly choose semantics, preventing silent
corruption when partial close tools land in the future.

make_fill_event factory in tests/_fixtures.py provides a default
is_full_close=False for tests; close-scenario tests must opt in.

This commit is TDD red — 2 existing FillEvent constructors in
tests/test_exchange.py + 6 production sites now fail. Subsequent
tasks turn them green.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Migrate Existing Fixtures to Factory (TDD green for tests)

**Files:**
- Modify: `tests/test_exchange.py:204` (FillEvent constructor)
- Modify: `tests/test_exchange.py:212` (FillEvent constructor)

Production sites still red after this task; Tasks 4-5 turn them green.

- [ ] **Step 1: Read context around tests/test_exchange.py:204**

Run: `sed -n '195,225p' tests/test_exchange.py`
Note current call form (kwargs) and what fields each constructor uses.

- [ ] **Step 2: Migrate test_exchange.py:204 to factory**

Replace the FillEvent(...) constructor at line 204 with `make_fill_event(...)`. Pass only the kwargs that differ from factory defaults; preserve all explicit values from the original.

For example, if original was:
```python
event = FillEvent(
    order_id="test-1", symbol="BTC/USDT", side="buy",
    position_side="long", trigger_reason="market",
    fill_price=50000.0, amount=0.1, fee=2.0, pnl=None, timestamp=1700,
)
```

Becomes:
```python
event = make_fill_event(
    order_id="test-1", symbol="BTC/USDT", amount=0.1, fee=2.0, timestamp=1700,
)
```

(Open semantics, pnl=None, side=buy, position_side=long, trigger_reason=market, fill_price=50000.0 all match factory defaults — drop or keep at discretion.)

Add `from tests._fixtures import make_fill_event` at top of file.

- [ ] **Step 3: Migrate test_exchange.py:212 to factory**

Same pattern. Note that line 212 is `event_with_pnl = FillEvent(...)` per audit — likely close semantics (pnl != None). Pass `is_full_close=True` if test asserts post-close state, OR keep `is_full_close=False` if test doesn't care about that field.

Read the test body to determine: if test asserts `event.is_full_close`, set explicitly; otherwise factory default OK.

- [ ] **Step 4: Run test_exchange.py to verify migration**

Run: `uv run pytest tests/test_exchange.py -x -q`
Expected: All test_exchange.py tests pass (factory migration complete for these 2 sites).

- [ ] **Step 5: Run full test suite — verify production-side red persists**

Run: `uv run pytest tests/ -x -q 2>&1 | head -30`
Expected: Failures now in production code paths (sim/okx FillEvent constructors). Tests in test_exchange.py pass.

- [ ] **Step 6: Commit (TDD green for test fixtures)**

```bash
git add tests/test_exchange.py
git commit -m "$(cat <<'EOF'
test(exchange): migrate 2 FillEvent constructors to make_fill_event factory

Both constructors at test_exchange.py:204 + :212 now use the factory
introduced in the previous commit. Production-side FillEvent sites
(sim 5 + okx 1) still red — Tasks 4-5 fix those.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Sim FillEvent Sites + Partial Close Contract Test

**Files:**
- Modify: `src/integrations/exchange/simulated.py:335` (`_fill_market_open`)
- Modify: `src/integrations/exchange/simulated.py:367` (`_fill_market_close`)
- Modify: `src/integrations/exchange/simulated.py:502` (`_execute_fill` conditional)
- Modify: `src/integrations/exchange/simulated.py:561` (`_execute_limit_fill`)
- Modify: `src/integrations/exchange/simulated.py:576` (`_force_liquidate`)
- Test: `tests/test_alert_lifecycle.py` (new)

Three sites use dynamic judgment (`order.symbol not in self._positions`); two are static (`False` for opens).

- [ ] **Step 1: Set is_full_close=False at simulated.py:335 (_fill_market_open)**

Edit `src/integrations/exchange/simulated.py:335-340`:

```python
return FillEvent(
    order_id=order.id, symbol=order.symbol, side=order.side,
    position_side=position_side, trigger_reason="market",
    fill_price=fill_price, amount=order.amount, fee=actual_fee,
    pnl=None, timestamp=now_ms,
    is_full_close=False,  # market open
)
```

- [ ] **Step 2: Set dynamic is_full_close at simulated.py:367 (_fill_market_close)**

Edit the function around line 354-372 — the FillEvent constructor needs to come AFTER `_close_position_core` so dict-membership query reflects post-close state:

```python
def _fill_market_close(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
    pos = self._positions.get(order.symbol)
    if pos is None:
        logger.warning(f"Market close {order.id} cancelled: position already closed")
        self._frozen_usdt -= order.frozen_margin
        self._free_usdt += order.frozen_margin
        return None

    actual_amount = min(order.amount, pos.contracts)
    fill_price = ticker.bid if pos.side == "long" else ticker.ask
    position_side = pos.side
    pnl, fee, _ = self._close_position_core(
        order.symbol, pos.side, actual_amount, fill_price, pnl_cap=True,
    )

    self._frozen_usdt -= order.frozen_margin
    self._free_usdt += order.frozen_margin

    is_full_close = order.symbol not in self._positions  # NEW: post-_close_position_core dict state

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
    )
```

- [ ] **Step 3: Set dynamic is_full_close at simulated.py:502 (_execute_fill conditional)**

Find function around line 494-508, modify FillEvent constructor at line 502 to include dynamic check. Note: line 498 calls `_close_position_core` so the dict membership check at FillEvent construction is post-close state.

```python
def _execute_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent:
    pos = self._positions[order.symbol]
    actual_amount = min(order.amount, pos.contracts)
    fill_price = ticker.bid if pos.side == "long" else ticker.ask
    pnl, fee, _ = self._close_position_core(
        order.symbol, pos.side, actual_amount, fill_price,
    )
    is_full_close = order.symbol not in self._positions  # NEW
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return FillEvent(
        order_id=order.id, symbol=order.symbol, side=order.side,
        position_side=order.position_side, trigger_reason=order.order_type,
        fill_price=fill_price, amount=actual_amount, fee=fee,
        pnl=pnl,
        timestamp=now_ms,
        is_full_close=is_full_close,
    )
```

- [ ] **Step 4: Set is_full_close=False at simulated.py:561 (_execute_limit_fill)**

Edit FillEvent constructor at line 561-566:

```python
return FillEvent(
    order_id=order.id, symbol=order.symbol, side=order.side,
    position_side=position_side, trigger_reason="limit",
    fill_price=fill_price, amount=order.amount, fee=actual_fee,
    pnl=None, timestamp=now_ms,
    is_full_close=False,  # limit open (no limit-close tool exists)
)
```

- [ ] **Step 5: Set dynamic is_full_close at simulated.py:576 (_force_liquidate)**

Edit FillEvent constructor at line 576-583, but compute `is_full_close` AFTER `_close_position_core` call at line 570. Per spec §4.2 boundary check: liquidation always passes `pos.contracts` so dict deletion is guaranteed → dynamic check returns True. Use dynamic form for consistency.

```python
def _force_liquidate(self, pos: _Position, symbol: str, price: float) -> FillEvent:
    contracts = pos.contracts
    pnl, fee, _ = self._close_position_core(
        symbol, pos.side, contracts, price, pnl_cap=True,
    )
    is_full_close = symbol not in self._positions  # NEW (always True for liquidation)
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
    )
```

- [ ] **Step 6: Run sim-related tests — verify production sim sites green**

Run: `uv run pytest tests/test_exchange.py tests/test_simulated_exchange.py -x -q 2>&1 | tail -10`
Expected: Tests pass (sim FillEvent sites no longer red).

If a test fixture explicitly asserts `pnl is None / not None` to determine close, it should still work since pnl values unchanged.

- [ ] **Step 7: Create tests/test_alert_lifecycle.py with partial close contract test**

Create `tests/test_alert_lifecycle.py` (file-top imports include all needed mock helpers):

```python
"""Iter 6 alert lifecycle tests: cancel tool + close path batch clearance."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._fixtures import (
    make_fill_event,
    make_okx_exchange,
    make_sim_exchange,
    make_ticker,
)


# ============ Sim partial close contract protection ============

@pytest.mark.asyncio
async def test_sim_partial_close_does_not_clear_alert():
    """Contract guarantee: future partial close tool must not silent-clear alerts.

    Manually constructs partial close (amount < pos.contracts) and verifies
    is_full_close=False so _dispatch_fill_event won't clear alerts.
    See spec §3.4 + §6.3.
    """
    sim = make_sim_exchange(initial_balance=10000.0)

    # Open position via create_order + _process_tick (market order needs tick to fill)
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))

    # Verify position created
    assert "BTC/USDT:USDT" in sim._positions
    pos = sim._positions["BTC/USDT:USDT"]
    initial_contracts = pos.contracts
    assert initial_contracts > 0

    # Add a price-level alert
    alert_id = sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert alert_id is not None
    assert len(sim.get_price_level_alerts()) == 1

    # Manually invoke _close_position_core with partial amount (50% of position)
    partial_amount = initial_contracts * 0.5
    sim._close_position_core(
        "BTC/USDT:USDT", pos.side, partial_amount, 50000.0, pnl_cap=False,
    )

    # Verify position still exists (partial close)
    assert "BTC/USDT:USDT" in sim._positions
    assert sim._positions["BTC/USDT:USDT"].contracts == pytest.approx(initial_contracts * 0.5)

    # is_full_close would be False (since symbol still in dict) —
    # which means _dispatch_fill_event would NOT clear alerts.
    is_full_close = "BTC/USDT:USDT" not in sim._positions
    assert is_full_close is False

    # Alerts must remain
    assert len(sim.get_price_level_alerts()) == 1
```

- [ ] **Step 8: Run partial close contract test**

Run: `uv run pytest tests/test_alert_lifecycle.py::test_sim_partial_close_does_not_clear_alert -v`
Expected: PASS.

- [ ] **Step 9: Run full test suite — verify Sim production sites all green**

Run: `uv run pytest tests/ -x -q 2>&1 | tail -10`
Expected: Sim FillEvent paths all green; only OKX FillEvent path remains red (Task 5).

- [ ] **Step 10: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_alert_lifecycle.py
git commit -m "$(cat <<'EOF'
feat(sim): fill is_full_close at 5 FillEvent constructors

3 dynamic via post-_close_position_core dict membership check
(market_close / conditional / liquidation), 2 static False (opens).
Dynamic form makes future partial close tools automatically correct
without spec changes — see spec §3.4 partial close protection.

Adds tests/test_alert_lifecycle.py with first contract test:
test_sim_partial_close_does_not_clear_alert validates partial close
won't silent-clear alerts even after this PR's auto-clearance lands.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: OKX _infer_is_full_close + Tests

**Files:**
- Modify: `src/integrations/exchange/okx.py:322` (`_parse_fill_event`)
- Modify: `src/integrations/exchange/okx.py` (add `_infer_is_full_close` private method)
- Test: `tests/test_alert_lifecycle.py` (append OKX tests)
- Task 5b (NEW, separate task) implements Remediation A — see below

**Task 0 outcome (closed 2026-04-28)**: Outcome B + 信号 4 → use 4-source fusion below + Task 5b implements Remediation A. No conditional branches.

- [ ] **Step 1: Add _infer_is_full_close helper to OKXExchange (4-source fusion)**

Insert after `_parse_fill_event` (around line 386) in `src/integrations/exchange/okx.py`:

```python
def _infer_is_full_close(self, info: dict, side: str, trigger_reason: str) -> bool:
    """OKX 平仓判定：四源融合，任一命中即认 close.

    NOTE: 当前项目 convention 下 ALL CLOSE FILLS ARE FULL CLOSE
    (close_position / set_stop_loss / set_take_profit 都传 amount=pos.contracts)。
    所以本判定实质是 "is close direction", 等价于 is_full_close.

    若未来加 partial close 工具 (reduce_position(percent) 等), 此判定会
    static-false-positive partial close, 届时需改为基于 fetch_positions /
    in-memory position cache 的精确判定 (见 spec §6.3).
    """
    # 信号 1: reduceOnly 显式 (OKX 强信号).
    # Task 0 实测: market close 路径下, 仅当 caller 显式传 params={"reduceOnly": True}
    # 时 OKX 才回填 'true' (Task 5b 实施 Remediation A).
    if info.get("reduceOnly") in (True, "true"):
        return True
    # 信号 2: trigger_reason 派生 close 类型.
    # 注意 "liquidation" 当前不可达 — _TRIGGER_REASON_MAP (okx.py:36-42) 没有该 key.
    # Task 0 实测: algo (SL/TP) 触发后 OKX 推送 fill event 的 ordType="limit"
    # → trigger_reason="unknown" → 信号 2 漏. algo 路径靠新增的信号 4 (algoId) 兜底.
    if trigger_reason in ("stop", "take_profit", "liquidation"):
        return True
    # 信号 3: posSide + side 反向 (hedge mode 强信号).
    # 项目强制 net_mode (okx.py:183), posSide 永远是 "net", 此分支当前不命中.
    pos_side = info.get("posSide")
    if pos_side == "long" and side == "sell":
        return True
    if pos_side == "short" and side == "buy":
        return True
    # 信号 4 (Task 0 实测后新增): info.algoId 非空 → algo-triggered fill.
    # algo 单 (SL/TP/conditional/OCO) 本质都是 reduce-only 语义, 触发后的 fill event
    # 一定带 algoId. Task 0 1B/1C 实测确认: SL/TP 触发的 fill event 中 info.algoId
    # 均非空; 普通用户下单 (1A/1D) algoId 为空. OKX 显式标识, 比信号 1/2/3 都强.
    algo_id = info.get("algoId")
    if algo_id and algo_id != "":
        return True
    return False
```

- [ ] **Step 2: Wire _infer_is_full_close into _parse_fill_event**

Edit `src/integrations/exchange/okx.py:375-386` — modify the `return FillEvent(...)`:

```python
    timestamp = order_data.get("timestamp", 0) or 0

    is_full_close = self._infer_is_full_close(info, side, trigger_reason)

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
    )
```

- [ ] **Step 3: Run test suite — verify all production FillEvent sites green**

Run: `uv run pytest tests/ -x -q 2>&1 | tail -10`
Expected: 858+ passed (857 baseline + new partial close test from Task 4).

- [ ] **Step 4: Add OKX _infer_is_full_close unit tests to test_alert_lifecycle.py**

Append to `tests/test_alert_lifecycle.py` (imports already at file top from Task 4 Step 7):

```python
# ============ OKX _infer_is_full_close three-source fusion ============

def test_okx_parse_fill_event_is_full_close_reduce_only():
    """Signal 1: info.reduceOnly=True → is_full_close=True."""
    okx = make_okx_exchange()
    info = {"reduceOnly": True, "posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "market") is True


def test_okx_parse_fill_event_is_full_close_reduce_only_string():
    """Signal 1: info.reduceOnly='true' string variant."""
    okx = make_okx_exchange()
    info = {"reduceOnly": "true", "posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "market") is True


def test_okx_parse_fill_event_is_full_close_trigger_reason_stop():
    """Signal 2: trigger_reason='stop' → is_full_close=True."""
    okx = make_okx_exchange()
    info = {"posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "stop") is True


def test_okx_parse_fill_event_is_full_close_trigger_reason_tp():
    """Signal 2: trigger_reason='take_profit' → is_full_close=True."""
    okx = make_okx_exchange()
    info = {"posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "take_profit") is True


def test_okx_parse_fill_event_is_full_close_trigger_reason_liq():
    """Signal 2: trigger_reason='liquidation' → is_full_close=True
    (defensive: _TRIGGER_REASON_MAP currently doesn't produce this)."""
    okx = make_okx_exchange()
    info = {"posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "liquidation") is True


@pytest.mark.skip(reason="hedge mode path, unreachable in net mode (okx.py:183)")
def test_okx_parse_fill_event_is_full_close_pos_side_long_sell():
    """Signal 3: posSide='long' + side='sell' → is_full_close=True.
    Currently unreachable: project forces net_mode (okx.py:183) so posSide='net'.
    Remove skip when hedge mode support is added.
    """
    okx = make_okx_exchange()
    info = {"posSide": "long"}
    assert okx._infer_is_full_close(info, "sell", "market") is True


@pytest.mark.skip(reason="hedge mode path, unreachable in net mode (okx.py:183)")
def test_okx_parse_fill_event_is_full_close_pos_side_short_buy():
    """Signal 3: posSide='short' + side='buy' → is_full_close=True.
    Currently unreachable: project forces net_mode."""
    okx = make_okx_exchange()
    info = {"posSide": "short"}
    assert okx._infer_is_full_close(info, "buy", "market") is True


def test_okx_parse_fill_event_is_full_close_net_mode_with_reduce_only():
    """net mode boundary: posSide='net' + reduceOnly=True → is_full_close=True.
    Validates signal 1 still works when signal 3 is unreachable."""
    okx = make_okx_exchange()
    info = {"reduceOnly": True, "posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "market") is True


def test_okx_parse_fill_event_open_no_close_signals():
    """Open fill: no reduceOnly, no close-trigger, posSide='net', no algoId → is_full_close=False."""
    okx = make_okx_exchange()
    info = {"posSide": "net", "reduceOnly": False, "algoId": ""}
    assert okx._infer_is_full_close(info, "buy", "market") is False


def test_okx_parse_fill_event_is_full_close_algo_id_non_empty():
    """Signal 4 (NEW): info.algoId non-empty → is_full_close=True.

    Task 0 实测 1B/1C: SL/TP triggered fills have algoId non-empty even though
    ordType='limit' and reduceOnly='false'. algoId is the OKX-explicit close
    signal for algo paths (SL/TP/conditional/OCO).
    """
    okx = make_okx_exchange()
    # Mimics 1B/1C real fixture: ordType=limit (signal 2 miss), reduceOnly=false
    # (signal 1 miss), posSide=net (signal 3 miss), but algoId non-empty
    info = {
        "posSide": "net",
        "reduceOnly": "false",
        "ordType": "limit",
        "algoId": "3516926949270786048",  # real value from 1C fixture
        "algoClOrdId": "6b9ad766b55dBCDE5cd2873d775bb62b",
    }
    assert okx._infer_is_full_close(info, "sell", "unknown") is True


def test_okx_parse_fill_event_open_with_empty_algo_id_string():
    """Signal 4 boundary: algoId="" (empty string, not non-empty) → False.

    Defends against treating "" as truthy by accident.
    """
    okx = make_okx_exchange()
    info = {"posSide": "net", "reduceOnly": False, "algoId": ""}
    assert okx._infer_is_full_close(info, "buy", "market") is False
```

- [ ] **Step 5: Run new OKX tests**

Run: `uv run pytest tests/test_alert_lifecycle.py -k "okx_parse_fill_event" -v`
Expected: 9 passed, 2 skipped (7 prior signal 1/2 tests + 2 new signal 4 tests; signal 3 hedge mode tests intentionally skipped).

- [ ] **Step 6: Add OKX integration test using Task 0 fixture**

Append to `tests/test_alert_lifecycle.py` (initially marked skip; Task 6 Step 5 removes skip):

```python
# ============ OKX _watch_orders_loop integration test ============

@pytest.mark.skip(reason="depends on Task 6 _dispatch_fill_event impl")
@pytest.mark.asyncio
async def test_okx_dispatch_fill_event_clears_via_loop():
    """Integration: _watch_orders_loop receives close fill push, _parse_fill_event
    constructs is_full_close=True, _dispatch_fill_event clears stale alert.

    Uses Task 0 captured fixture for market close scenario.
    """
    okx = make_okx_exchange()

    # Add a stale alert
    okx._price_level_alerts.append({
        "id": "test-alert-1",
        "symbol": "BTC/USDT:USDT",
        "price": 51000.0,
        "direction": "above",
        "reasoning": "stale",
    })

    # Load market close fixture
    fixture_path = Path("tests/fixtures/okx_watch_orders_market_close.json")
    with fixture_path.open() as f:
        order_data = json.load(f)

    # Mock _fetch_order_with_algo_fallback to avoid REST call
    okx._fetch_order_with_algo_fallback = AsyncMock(
        return_value={"info": {"pnl": "1.0"}}
    )

    # Parse fill event
    fill = await okx._parse_fill_event(order_data)

    # Verify is_full_close=True per signal 1 (reduceOnly) or 2 (trigger_reason)
    assert fill.is_full_close is True
    assert fill.symbol == "BTC/USDT:USDT"

    # Dispatch and verify alert cleared (no callback registered)
    await okx._dispatch_fill_event(fill)

    assert len(okx._price_level_alerts) == 0
```

- [ ] **Step 7: Run full test suite to verify zero regression**

Run: `uv run pytest tests/ -q 2>&1 | tail -5`
Expected: 866+ passed (857 baseline + ~9 new tests, depending on how many skip).

- [ ] **Step 8: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_alert_lifecycle.py
git commit -m "$(cat <<'EOF'
feat(okx): _infer_is_full_close three-source fusion + 7 unit tests

Wires is_full_close into _parse_fill_event via three-source fusion:
1. info.reduceOnly explicit (OKX strong signal)
2. trigger_reason in {stop, take_profit, liquidation} (defensive)
3. posSide + side reverse (hedge mode, currently unreachable)

Currently equivalent to "is close direction" per project convention
(all close fills are full close). Future partial close tools require
remediation per spec §6.3.

Tests: 7 unit (signal 1/2 coverage + open negative + net mode boundary),
2 skipped (signal 3 hedge mode unreachable). 1 integration test for
_watch_orders_loop pending Task 6 dispatch impl.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [x] **Step 9: ✅ Replaced by Task 5b (Remediation A) — see below**

Task 0 outcome B confirmed via 1A/1D实测 → Remediation A is required (not conditional). Lifted to standalone Task 5b for clarity.

---

## Task 5b: Remediation A — `BaseExchange.create_order` +params kwarg + close_position 显式 reduceOnly

**Files:**
- Modify: `src/integrations/exchange/base.py:98` (abstract `create_order` signature)
- Modify: `src/integrations/exchange/simulated.py` (override `create_order`)
- Modify: `src/integrations/exchange/okx.py:516-538` (override `create_order` merge params)
- Modify: `src/agent/tools_execution.py:115-117` (`close_position` passes `params={"reduceOnly": True}`)
- Modify: existing tests/mocks of `create_order` (sync to new kwarg)
- Test: `tests/test_alert_lifecycle.py` (5 new tests)

**Background**: Task 0 实测 (1A) shows OKX market close fill event has `info.reduceOnly='false'` when caller doesn't pass `reduceOnly` param. Task 0 1D 实测 confirms OKX echoes `reduceOnly='true'` when caller passes `params={"reduceOnly": True}`. Therefore market close path needs explicit `reduceOnly=True` to make signal 1 fire in `_infer_is_full_close`.

- [ ] **Step 1: Extend `BaseExchange.create_order` abstract signature**

Edit `src/integrations/exchange/base.py:98`:

```python
@abstractmethod
async def create_order(
    self,
    symbol: str,
    side: str,
    order_type: str,
    amount: float,
    price: float | None = None,
    params: dict | None = None,  # NEW
) -> Order: ...
```

- [ ] **Step 2: Sim override — accept and ignore params**

Edit `SimulatedExchange.create_order` (find the existing definition around simulated.py line 130-180; signature already takes price kwarg, just add params):

```python
async def create_order(
    self,
    symbol: str,
    side: str,
    order_type: str,
    amount: float,
    price: float | None = None,
    params: dict | None = None,  # NEW: accept but ignore
) -> Order:
    # Sim doesn't need reduceOnly (its _is_close_order_static + position state
    # logic handles full-close inference natively). Just accept the kwarg
    # for API compatibility with OKX.
    # ... existing body unchanged ...
```

- [ ] **Step 3: OKX override — merge caller params into internal dict**

Edit `src/integrations/exchange/okx.py:516-538` `create_order`:

```python
@_retry()
async def create_order(  # type: ignore[override]
    self,
    symbol: str,
    side: str,
    order_type: str,
    amount: float,
    price: float | None = None,
    params: dict | None = None,  # NEW
) -> Order:
    merged_params: dict[str, Any] = {"tdMode": "isolated"}
    if params:
        merged_params.update(params)  # caller wins on conflict
    is_algo = order_type in ("stop", "take_profit")
    if is_algo and price is not None:
        if order_type == "stop":
            merged_params["stopLossPrice"] = price
        else:  # take_profit
            merged_params["takeProfitPrice"] = price

    data = await self._client.create_order(
        symbol, order_type, side, amount, price, params=merged_params,
    )
    # ... rest of existing logic unchanged (is_algo manual construction, etc.) ...
```

- [ ] **Step 4: `tools_execution.py:close_position` passes `reduceOnly=True`**

Edit `src/agent/tools_execution.py:115-117` (inside `close_position` function):

```python
order = await deps.exchange.create_order(
    symbol=deps.symbol, side=order_side, order_type="market",
    amount=p.contracts,
    params={"reduceOnly": True},  # NEW: ensures OKX echoes info.reduceOnly=true in fill event
)
```

- [ ] **Step 5: Sync existing test mocks**

Run: `grep -rn "create_order" tests/ | grep -i "mock\|MagicMock\|AsyncMock"`

For each mock that asserts on `create_order` call args (e.g., uses `call_args` / `call_args_list` / `assert_called_with`), update to accept the new `params` kwarg. Examples:

- If mock just does `AsyncMock()` with no arg verification → no change needed
- If mock does `mock.create_order.assert_called_once_with(symbol="X", side="sell", ...)` → may need `params=ANY` from `unittest.mock`

Check at minimum: `tests/test_simulated_exchange.py`, `tests/test_exchange.py`, `tests/test_tools_execution.py` (or wherever close_position tests live).

- [ ] **Step 6: Add 5 new unit tests**

Append to `tests/test_alert_lifecycle.py`:

```python
# ============ Task 5b: Remediation A — params kwarg + reduceOnly propagation ============

@pytest.mark.asyncio
async def test_sim_create_order_accepts_params_kwarg():
    """Sim accepts params kwarg without crashing (transparent ignore)."""
    sim = make_sim_exchange()
    order = await sim.create_order(
        "BTC/USDT:USDT", "buy", "market", 0.01,
        params={"reduceOnly": True, "anything": "else"},
    )
    assert order is not None  # didn't crash on kwarg


@pytest.mark.asyncio
async def test_okx_create_order_merges_caller_params():
    """OKX override merges caller params into internal {tdMode: isolated} dict."""
    from unittest.mock import AsyncMock
    okx = make_okx_exchange()
    okx._client = AsyncMock()
    okx._client.create_order = AsyncMock(return_value={
        "id": "test-1", "symbol": "BTC/USDT:USDT", "side": "sell",
        "type": "market", "amount": 0.01, "price": None, "status": "open",
        "info": {"sz": "0.01"},
    })
    await okx.create_order(
        "BTC/USDT:USDT", "sell", "market", 0.01,
        params={"reduceOnly": True},
    )
    # Verify _client.create_order called with merged params
    call_kwargs = okx._client.create_order.call_args.kwargs
    assert call_kwargs["params"]["tdMode"] == "isolated"
    assert call_kwargs["params"]["reduceOnly"] is True


@pytest.mark.asyncio
async def test_okx_create_order_no_caller_params_uses_defaults():
    """OKX override with params=None → just {tdMode: isolated} (no reduceOnly)."""
    from unittest.mock import AsyncMock
    okx = make_okx_exchange()
    okx._client = AsyncMock()
    okx._client.create_order = AsyncMock(return_value={
        "id": "test-1", "symbol": "BTC/USDT:USDT", "side": "buy",
        "type": "market", "amount": 0.01, "price": None, "status": "open",
        "info": {"sz": "0.01"},
    })
    await okx.create_order("BTC/USDT:USDT", "buy", "market", 0.01)
    call_kwargs = okx._client.create_order.call_args.kwargs
    assert call_kwargs["params"] == {"tdMode": "isolated"}
    assert "reduceOnly" not in call_kwargs["params"]


@pytest.mark.asyncio
async def test_close_position_passes_reduce_only():
    """tools_execution.py:close_position passes params={'reduceOnly': True}
    to exchange.create_order. This is the Remediation A actuation point."""
    from unittest.mock import AsyncMock, MagicMock
    from src.agent.tools_execution import close_position
    from src.integrations.exchange.base import Position

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.db_engine = None
    deps.session_id = "test-session"
    deps.exchange = AsyncMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.01,
                 entry_price=50000.0, unrealized_pnl=0.0, leverage=10,
                 liquidation_price=45000.0),
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    deps.exchange.create_order = AsyncMock(return_value=MagicMock(id="order-1"))
    # Bypass _check_approval (returns True if no human gate)
    from unittest.mock import patch
    with patch("src.agent.tools_execution._check_approval",
               new=AsyncMock(return_value=True)):
        result = await close_position(deps, reasoning="test close")

    # Assert reduceOnly was passed
    call_kwargs = deps.exchange.create_order.call_args.kwargs
    assert call_kwargs.get("params") == {"reduceOnly": True}, \
        f"close_position must pass params={{'reduceOnly': True}}, got {call_kwargs.get('params')}"


@pytest.mark.asyncio
async def test_okx_fill_event_reduce_only_true_with_remediation_a():
    """End-to-end: OKX _infer_is_full_close returns True when fill event has
    info.reduceOnly='true' (the result of Remediation A). Validates 1D fixture."""
    okx = make_okx_exchange()
    # Mimics 1D fixture: market close with reduceOnly=true echoed back
    info = {
        "posSide": "net",
        "reduceOnly": "true",  # OKX echoed because caller passed it
        "ordType": "market",
        "algoId": "",  # market path, no algoId
    }
    assert okx._infer_is_full_close(info, "sell", "market") is True
```

- [ ] **Step 7: Run new tests + full regression**

Run: `uv run pytest tests/test_alert_lifecycle.py -v 2>&1 | tail -10`
Expected: 5 new Task 5b tests pass.

Run: `uv run pytest tests/ -q 2>&1 | tail -5`
Expected: All previously passing tests still pass (mock signature sync caught any breakage).

- [ ] **Step 8: Commit**

```bash
git add src/integrations/exchange/base.py src/integrations/exchange/simulated.py src/integrations/exchange/okx.py src/agent/tools_execution.py tests/
git commit -m "$(cat <<'EOF'
feat(exchange): Remediation A — extend create_order with params + close_position reduceOnly

Per Task 0 1A实测 (no params → reduceOnly=false in fill event) and
1D 实测 (with params={"reduceOnly": True} → reduceOnly=true echoed),
market close path requires explicit reduceOnly to make _infer_is_full_close
signal 1 fire.

- BaseExchange.create_order abstract signature: +params: dict | None = None
- SimulatedExchange.create_order: accept and ignore params (sim doesn't need)
- OKXExchange.create_order: merge caller params into internal {tdMode: isolated}
- tools_execution.py:close_position: passes params={"reduceOnly": True}
- 5 new unit tests covering signature compatibility + propagation + e2e

Combined with signal 4 (algoId) added in Task 5, Iter 6 P0-5 alert clearance
now closes both algo path (signal 4) and market close path (signal 1 via
Remediation A) on OKX.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Base Layer dispatch + clear helper + SRP-Split (Unit Tests)

**Files:**
- Modify: `src/integrations/exchange/base.py` (after `_check_price_levels` at line 200)
- Test: `tests/test_alert_lifecycle.py`

- [ ] **Step 1: Add clear_level_alerts_by_symbol helper to base.py**

Insert in `BaseExchange` class after `_check_price_levels` (around line 201, before `@dataclass class FillEvent` at line 204):

```python
    def clear_level_alerts_by_symbol(self, symbol: str) -> int:
        """Remove all price level alerts matching symbol. Returns count cleared.

        Used by _clear_stale_alerts_for_full_close on close fills. Also exposed
        as a standalone method for tests / future use.
        """
        before = len(self._price_level_alerts)
        self._price_level_alerts = [
            a for a in self._price_level_alerts if a["symbol"] != symbol
        ]
        return before - len(self._price_level_alerts)
```

- [ ] **Step 2: Add _dispatch_fill_event entry + 2 SRP units**

Insert after `clear_level_alerts_by_symbol`:

```python
    async def _dispatch_fill_event(self, fill: 'FillEvent') -> None:
        """Entry point for fill event dispatch.

        Subclasses MUST route all FillEvent through this method, not call
        self._fill_callback directly. Internal split into two SRP units:
        alert hygiene (clear) and callback fan-out (invoke).

        Order semantics: clear-before-callback. The callback observes the
        final post-hygiene state (alert list already filtered). If a future
        callback needs to capture stale-alert context for diagnostic logging,
        either reorder the dispatch or add a pre-clear hook.
        """
        self._clear_stale_alerts_for_full_close(fill)
        await self._invoke_fill_callback(fill)

    def _clear_stale_alerts_for_full_close(self, fill: 'FillEvent') -> None:
        """SRP unit 1: alert hygiene. Clear all level alerts for fill.symbol
        if and only if the fill closes the position fully (is_full_close).
        """
        if not fill.is_full_close:
            return
        cleared = self.clear_level_alerts_by_symbol(fill.symbol)
        if cleared > 0:
            logger.info(
                "Cleared %d stale price-level alert(s) on full close fill: "
                "symbol=%s order_id=%s",
                cleared, fill.symbol, fill.order_id,
            )

    async def _invoke_fill_callback(self, fill: 'FillEvent') -> None:
        """SRP unit 2: callback fan-out with failure isolation.

        Callback exceptions are logged, not propagated, so one fill's
        callback failure does not block subsequent fill processing.
        """
        if self._fill_callback is None:
            return
        try:
            await self._fill_callback(fill)
        except Exception:
            logger.exception("Fill callback failed for order %s", fill.order_id)
```

- [ ] **Step 3: Add unit tests for clear_level_alerts_by_symbol**

Append to `tests/test_alert_lifecycle.py`:

```python
# ============ clear_level_alerts_by_symbol helper ============

def test_clear_level_alerts_by_symbol_filters_correct_symbol():
    """Multi-symbol mix: clears only target symbol, returns count cleared."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "BTC/USDT:USDT", "price": 50000.0, "direction": "above"},
        {"id": "a2", "symbol": "ETH/USDT:USDT", "price": 3000.0, "direction": "above"},
        {"id": "a3", "symbol": "BTC/USDT:USDT", "price": 51000.0, "direction": "above"},
    ]
    cleared = sim.clear_level_alerts_by_symbol("BTC/USDT:USDT")
    assert cleared == 2
    assert len(sim._price_level_alerts) == 1
    assert sim._price_level_alerts[0]["symbol"] == "ETH/USDT:USDT"


def test_clear_level_alerts_by_symbol_returns_zero_when_empty():
    """Symbol with no alerts → returns 0, list unchanged."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "ETH/USDT:USDT", "price": 3000.0, "direction": "above"},
    ]
    cleared = sim.clear_level_alerts_by_symbol("BTC/USDT:USDT")
    assert cleared == 0
    assert len(sim._price_level_alerts) == 1
```

- [ ] **Step 4: Add unit tests for _dispatch_fill_event SRP-split**

Append to `tests/test_alert_lifecycle.py`:

```python
# ============ _dispatch_fill_event SRP units ============

@pytest.mark.asyncio
async def test_dispatch_fill_event_clears_on_full_close():
    """is_full_close=True → alert cleared + callback invoked."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "BTC/USDT:USDT", "price": 51000.0, "direction": "above"},
    ]
    callback_called = []

    async def cb(fill):
        callback_called.append(fill)
    sim._fill_callback = cb

    fill = make_fill_event(symbol="BTC/USDT:USDT", is_full_close=True)
    await sim._dispatch_fill_event(fill)

    assert len(sim._price_level_alerts) == 0
    assert len(callback_called) == 1


@pytest.mark.asyncio
async def test_dispatch_fill_event_skips_clear_when_not_full_close():
    """is_full_close=False → alert preserved + callback invoked."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "BTC/USDT:USDT", "price": 51000.0, "direction": "above"},
    ]
    callback_called = []

    async def cb(fill):
        callback_called.append(fill)
    sim._fill_callback = cb

    fill = make_fill_event(symbol="BTC/USDT:USDT", is_full_close=False)
    await sim._dispatch_fill_event(fill)

    assert len(sim._price_level_alerts) == 1  # preserved
    assert len(callback_called) == 1


@pytest.mark.asyncio
async def test_dispatch_fill_event_callback_failure_isolated(caplog):
    """Callback raises → logger.exception called, exception NOT propagated."""
    sim = make_sim_exchange()

    async def failing_cb(fill):
        raise RuntimeError("simulated failure")
    sim._fill_callback = failing_cb

    fill = make_fill_event(is_full_close=False)
    # Must NOT raise
    await sim._dispatch_fill_event(fill)

    assert any("Fill callback failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_dispatch_fill_event_no_callback_registered():
    """No callback registered → only clears alert, no error."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "BTC/USDT:USDT", "price": 51000.0, "direction": "above"},
    ]
    sim._fill_callback = None

    fill = make_fill_event(symbol="BTC/USDT:USDT", is_full_close=True)
    # Must NOT raise
    await sim._dispatch_fill_event(fill)

    assert len(sim._price_level_alerts) == 0  # cleared
```

- [ ] **Step 5: Remove skip from OKX integration test (Task 5 Step 6)**

Edit `tests/test_alert_lifecycle.py` — remove the `@pytest.mark.skip(reason="depends on Task 6 _dispatch_fill_event impl")` decorator from `test_okx_dispatch_fill_event_clears_via_loop`.

- [ ] **Step 6: Run all alert_lifecycle tests**

Run: `uv run pytest tests/test_alert_lifecycle.py -v 2>&1 | tail -30`
Expected: All non-skipped tests pass; 2 hedge mode tests still skipped.

- [ ] **Step 7: Run full test suite — verify zero regression**

Run: `uv run pytest tests/ -q 2>&1 | tail -5`
Expected: 870+ passed.

- [ ] **Step 8: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_alert_lifecycle.py
git commit -m "$(cat <<'EOF'
feat(base): _dispatch_fill_event with SRP-split + clear helper

Adds three new BaseExchange methods following SRP:
- _dispatch_fill_event: orchestration entry (one sync + one async line)
- _clear_stale_alerts_for_full_close: SRP-1 alert hygiene
- _invoke_fill_callback: SRP-2 callback fan-out + failure isolation

Plus clear_level_alerts_by_symbol helper for batch alert clearance.

Order semantics: clear-before-callback (callback sees post-hygiene state).
Failure isolation: callback exceptions logged not propagated, base-layer
contract guarantee for future callback registrants (current cli/app.py
handler also has its own try/except — base layer is defense in depth).

Tests: 6 new unit (clear ×2 + dispatch SRP units ×4) + un-skip OKX
integration test from Task 5.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Sim/OKX Call Site Replacement + Integration Tests

**Files:**
- Modify: `src/integrations/exchange/simulated.py:673-675` (call site)
- Modify: `src/integrations/exchange/okx.py:260-265` (call site)
- Test: `tests/test_alert_lifecycle.py` (append integration tests)

- [ ] **Step 1: Replace Sim call site at simulated.py:673-675**

Edit:
```python
# before
for fill in triggered:
    if self._fill_callback:
        await self._fill_callback(fill)

# after
for fill in triggered:
    await self._dispatch_fill_event(fill)
```

- [ ] **Step 2: Replace OKX call site at okx.py:260-265**

Edit:
```python
# before
fill_event = await self._parse_fill_event(order_data)
if self._fill_callback:
    try:
        await self._fill_callback(fill_event)
    except Exception:
        logger.exception("Fill callback failed for order %s", order_data.get("id"))

# after
fill_event = await self._parse_fill_event(order_data)
await self._dispatch_fill_event(fill_event)
```

- [ ] **Step 3: Run full test suite — verify call site swap doesn't regress**

Run: `uv run pytest tests/ -q 2>&1 | tail -5`
Expected: All previously passing tests still pass (call site swap is behavior-preserving for already-tested paths).

- [ ] **Step 4: Add Sim end-to-end integration tests**

Append to `tests/test_alert_lifecycle.py`:

```python
# ============ Sim end-to-end close fill → alert clearance ============

@pytest.mark.asyncio
async def test_sim_market_close_triggers_alert_clear():
    """Open + add alert + market close → alert auto-cleared."""
    sim = make_sim_exchange()

    # Open position via create_order + _process_tick
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))
    assert "BTC/USDT:USDT" in sim._positions

    # Add alert
    alert_id = sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert alert_id is not None
    assert len(sim.get_price_level_alerts()) == 1

    # Market close
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="sell", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0, timestamp=1700000001000))

    # Alert cleared via _dispatch_fill_event
    assert "BTC/USDT:USDT" not in sim._positions
    assert len(sim.get_price_level_alerts()) == 0


@pytest.mark.asyncio
async def test_sim_conditional_fill_triggers_alert_clear():
    """Open + add alert + SL trigger → alert auto-cleared."""
    sim = make_sim_exchange()

    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))
    assert "BTC/USDT:USDT" in sim._positions

    # Set SL via conditional (stop) order — sim forces full position size
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="sell", order_type="stop", amount=0.01, price=49000.0,
    )

    # Add alert
    sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert len(sim.get_price_level_alerts()) == 1

    # Trigger SL via price drop (below 49000 trigger)
    await sim._process_tick(make_ticker(last=48900.0, timestamp=1700000001000))

    assert "BTC/USDT:USDT" not in sim._positions
    assert len(sim.get_price_level_alerts()) == 0


@pytest.mark.asyncio
async def test_sim_liquidation_triggers_alert_clear():
    """Open + add alert + liquidation → alert auto-cleared."""
    sim = make_sim_exchange(initial_balance=100.0)  # small balance to enable liquidation
    await sim.set_leverage("BTC/USDT:USDT", 100)

    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))
    assert "BTC/USDT:USDT" in sim._positions

    sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert len(sim.get_price_level_alerts()) == 1

    # Crash price to trigger liquidation (100x leverage → ~1% drop kills it)
    await sim._process_tick(make_ticker(last=40000.0, timestamp=1700000001000))

    assert "BTC/USDT:USDT" not in sim._positions
    assert len(sim.get_price_level_alerts()) == 0


@pytest.mark.asyncio
async def test_sim_open_fill_does_not_clear_alert():
    """Open fill (is_full_close=False) → alert preserved.

    Open fills don't create stale alerts; the alerts at structural levels
    just placed BEFORE opening should remain valid post-open.
    """
    sim = make_sim_exchange()

    # Add alert FIRST (before opening)
    sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert len(sim.get_price_level_alerts()) == 1

    # Open fill via create_order + _process_tick
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))
    assert "BTC/USDT:USDT" in sim._positions

    # Alert preserved
    assert len(sim.get_price_level_alerts()) == 1
```

NOTE: All sim integration tests use `make_sim_exchange()` + `make_ticker()` helpers from `tests/_fixtures.py` (Task 2 Step 3). The sim ticker-loop pump method is `_process_tick` (verified at simulated.py:585) — pumping a market open or close requires both `create_order` + `_process_tick` since orders match against the next tick.

- [ ] **Step 5: Run integration tests**

Run: `uv run pytest tests/test_alert_lifecycle.py -v -k "sim_" 2>&1 | tail -20`
Expected: All sim integration tests pass.

Method name `_process_tick` is verified at simulated.py:585 (see Task 7 Step 4 NOTE). If a test still fails, the cause is order-state setup (e.g., position not yet created when partial close test starts), not the tick-loop method name.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -q 2>&1 | tail -5`
Expected: 874+ passed (4 new sim integration tests).

- [ ] **Step 7: Commit**

```bash
git add src/integrations/exchange/simulated.py src/integrations/exchange/okx.py tests/test_alert_lifecycle.py
git commit -m "$(cat <<'EOF'
feat(exchange): route sim/okx fill events through _dispatch_fill_event

Replaces direct _fill_callback invocation in both exchanges with
base-layer _dispatch_fill_event (which orchestrates clear + invoke
SRP units). Both call sites now identical.

Adds 4 sim end-to-end integration tests covering:
- Market close triggers alert clearance (P0-5 main path)
- Conditional SL fill triggers alert clearance
- Liquidation triggers alert clearance
- Open fill preserves alerts (no stale semantics)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: cancel_price_level_alert Agent Tool + display.py + Drift Guard Sync

**Files:**
- Modify: `src/agent/tools_execution.py` (add `cancel_price_level_alert` _impl)
- Modify: `src/agent/trader.py:519-540` (add `@tool` wrapper after `add_price_level_alert`)
- Modify: `src/agent/trader.py:614-650` (REGISTERED_TOOL_NAMES 31→32, count comment 10→11, insert after line 645)
- Modify: `src/cli/display.py:251-262` (register success prefix)
- Modify: `tests/test_trader_agent.py:85-86` (drift guard hardcode sync)
- Test: `tests/test_alert_lifecycle.py` (append cancel tool + is_tool_error tests)

- [ ] **Step 1: Add cancel_price_level_alert _impl to tools_execution.py**

Insert after `add_price_level_alert` function (around line 257-258 in tools_execution.py):

```python
async def cancel_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    reasoning: str,
) -> str:
    """Remove a price level alert by ID."""
    ok = deps.exchange.remove_price_level_alert(alert_id)
    if ok:
        await _record_action(
            deps, action="cancel_price_level_alert",
            reasoning=f"id={alert_id} | {reasoning}",
        )
        return f"Price level alert cancelled (id={alert_id})"
    return f"Alert {alert_id} not found (already triggered or never existed)"
```

- [ ] **Step 2: Add @tool wrapper in trader.py**

Insert in `create_trader_agent` function, immediately after the `add_price_level_alert` `@tool` block (after line 539):

```python
    @tool
    async def cancel_price_level_alert(
        ctx: RunContext[TradingDeps],
        alert_id: str,
        reasoning: str,
    ) -> str:
        """Cancel a previously-set price level alert by its ID.

        Use this when an alert is no longer relevant — for example, if the
        structural level it watched has been invalidated by a regime change
        or if the position context that motivated it has shifted in a way
        that the auto-clearing on close fill does not cover.

        Note: alerts at SL/TP levels are auto-cleared when a position closes;
        you usually do not need to call this for that case.

        Args:
            alert_id: the alert ID returned by add_price_level_alert.
            reasoning: brief description of why this alert is being cancelled.
        """
        from src.agent.tools_execution import cancel_price_level_alert as _impl

        return await _impl(ctx.deps, alert_id, reasoning=reasoning)
```

- [ ] **Step 3: Update REGISTERED_TOOL_NAMES**

Edit `src/agent/trader.py:614-650`:
- Line 637 comment: `# --- 执行 (10) ---` → `# --- 执行 (11) ---`
- After line 645 `"add_price_level_alert",` insert new line `"cancel_price_level_alert",`

The list now contains 32 entries (20 perception + 11 execution + 1 memory).

- [ ] **Step 4: Update drift guard hardcode in test_trader_agent.py**

Edit `tests/test_trader_agent.py:85-86`:

```python
# before
assert len(REGISTERED_TOOL_NAMES) == 31, (
    f"Expected 31 tools (20+10+1), got {len(REGISTERED_TOOL_NAMES)}"

# after
assert len(REGISTERED_TOOL_NAMES) == 32, (
    f"Expected 32 tools (20+11+1), got {len(REGISTERED_TOOL_NAMES)}"
```

- [ ] **Step 5: Register success prefix in display.py**

Edit `src/cli/display.py:251-262` — add new entry to `_EXECUTION_SUCCESS_PREFIXES`:

```python
_EXECUTION_SUCCESS_PREFIXES = {
    "open_position": "Order submitted:",
    "close_position": "Orders submitted:",
    "set_stop_loss": "Stop loss set at",
    "set_take_profit": "Take profit set at",
    "adjust_leverage": "Leverage adjusted to",
    "place_limit_order": "Limit order placed:",
    "cancel_order": "Order cancelled:",
    "set_price_alert": "Price alert updated:",
    "add_price_level_alert": ("Price level alert set:", "Alert set"),
    "cancel_price_level_alert": "Price level alert cancelled",  # NEW
    "set_next_wake": "Next wake set to",
}
```

NOTE: `_EXECUTION_PARSERS` is NOT extended — cancel tool args (alert_id + reasoning) are simple, default UI rendering is sufficient.

**is_tool_error mechanism (no "selective skip"):** display.py:282-288 uses `_EXECUTION_SUCCESS_PREFIXES` as a positive list — registered prefix matches success, anything else is auto-classified as business rejection. Our `"Price level alert cancelled"` registration matches the success path; the not-found return `"Alert {id} not found ..."` doesn't start with any registered prefix → `is_tool_error` returns True automatically. No "intentional non-registration" needed; it's how the lookup works.

- [ ] **Step 6: Run drift guard test — verify 31→32 sync caught**

Run: `uv run pytest tests/test_trader_agent.py -v 2>&1 | tail -10`
Expected: All trader agent tests pass, including drift guard with 32 tools.

If drift guard fails: verify both REGISTERED_TOOL_NAMES list and the @tool wrapper exist + names match exactly.

- [ ] **Step 7: Add cancel tool unit tests + is_tool_error tests**

Append to `tests/test_alert_lifecycle.py`:

```python
# ============ cancel_price_level_alert tool ============

@pytest.mark.asyncio
async def test_cancel_price_level_alert_tool_success():
    """Successful cancel: returns success message + records action."""
    from src.agent.tools_execution import cancel_price_level_alert

    sim = make_sim_exchange()
    alert_id = sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert alert_id is not None

    # Build minimal TradingDeps mock
    # NOTE: db_engine=None is safe — _record_action source-verified to early-return
    # at tools_execution.py:19 (`if deps.db_engine is None: return`). No DB I/O occurs.
    deps = MagicMock()
    deps.exchange = sim
    deps.db_engine = None
    deps.session_id = "test-session"

    result = await cancel_price_level_alert(deps, alert_id, "no longer needed")

    assert result == f"Price level alert cancelled (id={alert_id})"
    assert len(sim.get_price_level_alerts()) == 0


@pytest.mark.asyncio
async def test_cancel_price_level_alert_tool_not_found():
    """Non-existent alert_id: returns not-found message."""
    from src.agent.tools_execution import cancel_price_level_alert

    sim = make_sim_exchange()
    deps = MagicMock()
    deps.exchange = sim
    deps.db_engine = None
    deps.session_id = "test-session"

    result = await cancel_price_level_alert(deps, "nonexistent-id", "test")

    assert "not found" in result
    assert "nonexistent-id" in result


# ============ display.py is_tool_error coverage ============

def test_is_tool_error_cancel_alert_success_returns_false():
    """Success message with prefix → is_tool_error returns False."""
    from src.cli.display import is_tool_error
    
    result = is_tool_error(
        tool_name="cancel_price_level_alert",
        content="Price level alert cancelled (id=abc12345)",
        outcome="success",
    )
    assert result is False


def test_is_tool_error_cancel_alert_not_found_returns_true():
    """Not-found message doesn't match prefix → is_tool_error returns True (business rejection)."""
    from src.cli.display import is_tool_error
    
    result = is_tool_error(
        tool_name="cancel_price_level_alert",
        content="Alert nonexistent-id not found (already triggered or never existed)",
        outcome="success",
    )
    assert result is True
```

- [ ] **Step 8: Run new tool tests**

Run: `uv run pytest tests/test_alert_lifecycle.py -v -k "cancel or is_tool_error" 2>&1 | tail -15`
Expected: 4 passed.

- [ ] **Step 9: Run full test suite**

Run: `uv run pytest tests/ -q 2>&1 | tail -5`
Expected: 887 passed + 3 skipped (857 baseline + ~32 new tests, where 2 hedge mode tests skip).

If failures: investigate per-test, but most likely culprits are (a) drift guard not synced, (b) display.py prefix typo mismatch with tool return string, (c) `_record_action` mock not handling new args.

- [ ] **Step 10: Commit**

```bash
git add src/agent/tools_execution.py src/agent/trader.py src/cli/display.py tests/test_trader_agent.py tests/test_alert_lifecycle.py
git commit -m "$(cat <<'EOF'
feat(agent): add cancel_price_level_alert tool + display registration

New tool lets agent proactively dismiss alerts that are no longer
relevant. Auto-clearance on close fill (Tasks 6-7) handles the common
case; this tool covers regime-change / structural-shift dismissal.

- tools_execution.py: cancel_price_level_alert _impl, add/cancel symmetric
  with add_price_level_alert (failure path doesn't record, see spec §4.6)
- trader.py: @tool wrapper + REGISTERED_TOOL_NAMES 31→32 + count (10→11)
- display.py: _EXECUTION_SUCCESS_PREFIXES entry; not-found return
  doesn't start with the registered success prefix → is_tool_error
  positive-list lookup auto-classifies it as business rejection
  (same mechanism as close_position "No positions to close" — display.py:163)
- test_trader_agent.py:85-86: drift guard hardcode 31→32 + (20+10+1)→(20+11+1)
- test_alert_lifecycle.py: 2 cancel tool tests + 2 is_tool_error tests

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Final Verification + Iter 5 Compliance Check

**Files:** none (verification only)

- [ ] **Step 1: Verify Iter 5 framework compliance — Args present**

Run: `uv run python -c "from src.agent.trader import create_trader_agent; from src.agent.trader import TradingDeps; print('OK')"`
Expected: Prints "OK" without `UserError` from pydantic-ai about missing Args.

If FAIL with "Missing parameter description": the @tool wrapper docstring is missing Args section for one of the parameters. Verify both `alert_id` and `reasoning` have entries under `Args:`.

- [ ] **Step 2: Verify drift guard catches missing-from-list scenario**

Temporarily comment out the new line `"cancel_price_level_alert",` in `REGISTERED_TOOL_NAMES`. Run drift guard:

Run: `uv run pytest tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools -v`
Expected: FAIL with "In agent but not in REGISTERED_TOOL_NAMES: {'cancel_price_level_alert'}".

Restore the commented line. Re-run to confirm green.

- [ ] **Step 3: Run full test suite final check**

Run: `uv run pytest tests/ -q 2>&1 | tail -10`
Expected: 887 passed + 3 skipped (or higher if Task 0 chose Outcome B/C remediation tests).

- [ ] **Step 4: Verify acceptance criteria checklist**

Manually walk spec §7 acceptance criteria 1-11:

1. ✅ set alert → cancel via tool → no trigger? (test `test_cancel_price_level_alert_tool_success`)
2. ✅ Sim 3 close paths clear alerts? (tests `test_sim_market_close/conditional_fill/liquidation_triggers_alert_clear`)
3. ✅ OKX `_watch_orders_loop` mock close fill clears? (test `test_okx_dispatch_fill_event_clears_via_loop`)
4. ✅ Sim open fill preserves alert? (test `test_sim_open_fill_does_not_clear_alert`)
5. ✅ Sim partial close preserves alert? (test `test_sim_partial_close_does_not_clear_alert`)
6. ✅ Callback failure isolated? (test `test_dispatch_fill_event_callback_failure_isolated`)
7. ✅ Drift guard 31→32 + comment sync? (test `test_registered_tool_names_matches_agent_tools` + Task 8 hardcode update)
8. ✅ Total 887 passed + 3 skipped? (Step 3 result; +30 pass / +2 hedge skip vs 857+1 baseline)
9. ✅ Iter 5 compliance: Args present? (Step 1 result)
10. ✅ Task 0 OKX implementation completed (real fixtures or remediation)? (Task 0 outcome)
11. ✅ display.py prefix + is_tool_error covered? (tests `test_is_tool_error_cancel_alert_success/not_found`)

If any fail, return to corresponding task and fix.

- [ ] **Step 5: Verify spec section §3.1 ASCII diagram still matches code**

Run: `grep -n "_dispatch_fill_event\|_clear_stale_alerts_for_full_close\|_invoke_fill_callback\|_fill_callback" src/integrations/exchange/base.py`
Expected: All 4 method/field names present in base.py (matching spec §3.1 Layer 1 diagram + §4.4 method definitions).

- [ ] **Step 6: Push branch + open PR (no commit, just branch push)**

Run:
```bash
git push -u origin feature/iter-t2-1-alert-lifecycle
```

Then user opens PR via GitHub UI or `gh pr create`. PR description should reference:
- Spec: `docs/superpowers/specs/2026-04-27-iter6-alert-lifecycle-design.md`
- Plan: `docs/superpowers/plans/2026-04-27-iter6-alert-lifecycle.md`
- Closes P0-5 + partial closure of P0-6 (combined with Iter 7)

Memory updates (`MEMORY.md` + relevant project memories) and `pre-next-observation-todos.md` checklist tick happen post-merge in a separate session, per workflow established in Iter 1/5/7/8.

---

## Self-Review Checklist (executed by author before handoff)

**1. Spec coverage:**
- §1.1 P0-5 scope → Tasks 4-7 (sim) + Task 5 (okx) + Task 8 (cancel tool) ✓
- §1.3 Iter 6+7 联合 P0-6 → Tasks 4+7 reduce alert volume, Iter 7 already merged for ordering ✓
- §3.1 architecture diagram → Task 1 (uplift) + Task 6 (dispatch) + Task 4 (sim is_full_close) + Task 5 (okx is_full_close) ✓
- §3.2 SRP method split → Task 6 Step 2 ✓
- §3.3 two alert systems → no code change, design only (verified via §3.3 referenced as out-of-scope for clearing) ✓
- §3.4 partial close protection → Task 4 Step 7 (contract test) + spec §6.3 candidate ✓
- §4.1 FillEvent +is_full_close → Task 2 Step 1 ✓
- §4.2 Sim 5 sites → Task 4 Steps 1-5 ✓
- §4.3 OKX four-source fusion → Task 5 Step 1 (含信号 4 algoId) ✓
- §4.3.1 Task 0 hard gate → Task 0 (closed 2026-04-28, outcome B) ✓
- §4.3.1.1 Task 0 实测结果 + Remediation A → Task 5b ✓
- §4.4 base dispatch + 3 methods → Task 6 Steps 1-2 ✓
- §4.5 call site replacement → Task 7 Steps 1-2 ✓
- §4.6 cancel tool + display.py + record signature → Task 8 Steps 1-5 ✓
- §4.7 REGISTERED_TOOL_NAMES + drift guard sync → Task 8 Steps 3-4 ✓
- §5.4 fixture migration → Tasks 2-3 ✓
- §6 long-term candidates → no code change (memory candidates) ✓
- §7 acceptance 1-11 → Task 9 Step 4 ✓

**2. Placeholder scan:** no TBD / TODO / "implement later" / "similar to Task N" found.

**3. Type consistency:**
- `is_full_close` named consistently across all tasks ✓
- `_dispatch_fill_event` / `_clear_stale_alerts_for_full_close` / `_invoke_fill_callback` matched to spec §4.4 ✓
- `make_fill_event` factory signature matches usage in all tests ✓
- REGISTERED_TOOL_NAMES count 31→32 + comment (10)→(11) consistent across spec + plan ✓
- `BaseExchange.create_order` `params` kwarg signature consistent in Task 5b across base/sim/okx/close_position + 5 unit tests ✓
- `_infer_is_full_close` 4-source fusion (Task 5 Step 1) matches signal definitions in spec §4.3 ✓

**No issues found in self-review.**
