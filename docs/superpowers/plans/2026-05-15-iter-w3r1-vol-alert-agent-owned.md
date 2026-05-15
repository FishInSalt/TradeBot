# Iter w3r1-vol-alert-agent-owned Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transfer ownership of the price volatility alert from the wizard (one-time human decision persisted to `Session.alert_config`) to the agent, modeling it as a singleton with `None` (unset) ↔ `Set` two-state and adding an idempotent `cancel_price_volatility_alert` tool.

**Architecture:** `BaseExchange._alert_service: PriceAlertService | None` becomes the single fact-only signal. Lazy: agent's first `set_price_volatility_alert` lazy-constructs the service; `cancel_price_volatility_alert` returns the slot to `None`. Wizard / `AlertsConfig` / `Session.alert_config` / `WizardResult.alert_*` are all deleted; obsolete DB column is left as a dead column (project has no Alembic precedent for column drops).

**Tech Stack:** Python 3.13, pydantic-ai, SQLAlchemy 2.x async + aiosqlite, pytest-asyncio, Rich CLI, ad-hoc PRAGMA-based session-table migrations (not Alembic for `Session` columns).

**Spec:** `docs/superpowers/specs/2026-05-15-iter-w3r1-vol-alert-agent-owned-design.md`

**Branch:** `iter-w3r1-vol-alert-agent-owned` (already created; spec already committed as `daec446`)

**Baseline:** 1694 tests passing as of `540a697`. Expected post-iter: **1693-1700** — precise count is 9 added (Task 1: 4 base lazy/cancel; Task 2: 1 net set + 2 cancel + 2 get_active_alerts) minus 8 deleted (1 set-disabled + 1 step_risk_alerts_off + 1 restore_null + 5 Task 7 Cat A) = net **+1** → ~1695. Band 1693-1700 allows -1 ~ +6 tolerance. Spec §6.4 (commit `daec446`) cited 1697-1702 as an early estimate; this plan has the more precise count. The spec is being updated in lockstep to 1693-1700.

---

## Sequencing rationale

Tasks are sequenced for **per-commit runnability** by adding new APIs alongside old ones first, then migrating callers, then deleting the old APIs:

1. **Task 1** — Add new base API (`set_volatility_alert` / `cancel_volatility_alert`) **alongside** the old `set_alert_service` / `update_alert_params`. Both work. New unit tests added.
2. **Task 2** — Migrate the agent tool layer + perception display + trader registration to the new API. Old base API still present but unused by tool layer.
3. **Task 3** — Delete the `cli/app.py build_services` alert init block (which was the last caller of the old base API).
4. **Task 4** — Delete the old `set_alert_service` / `update_alert_params` from base; update drift guards.
5. **Task 5** — Delete wizard alert step + `WizardResult` fields + `AlertsConfig`; update wizard / config tests.
6. **Task 6** — Delete `Session.alert_config` model field + session_manager read/write paths + ad-hoc migration entry. Storage / session_manager / alembic_migration tests updated.
7. **Task 7** — Sweep remaining integration / scenario tests; run repo-wide grep guard; full pytest verification.

Tasks 5 and 6 may be reordered if convenient, but must come after Tasks 1-4. Task 7 is last.

---

## File Structure

**Production files modified:**

- `src/integrations/exchange/base.py` — replace alert API (Tasks 1, 4)
- `src/agent/tools_execution.py` — rewire set, add cancel (Task 2)
- `src/agent/tools_perception.py` — `get_active_alerts` text (Task 2)
- `src/agent/trader.py` — wrapper docstrings + register cancel + tool list (Task 2)
- `src/cli/app.py` — delete alert init block (Task 3)
- `src/cli/wizard.py` — delete alert step / `WizardResult` fields / summary row (Task 5)
- `src/config.py` — delete `AlertsConfig` / `Settings.alerts` (Task 5)
- `src/cli/session_manager.py` — delete `alert_config` paths + migration entry (Task 6)
- `src/storage/models.py` — delete `Session.alert_config` mapped column (Task 6)
- `config/settings.yaml` — delete `alerts:` section if present (Task 5)

**Test files modified or added:**

- `tests/test_tool_enhancement.py` — base drift guards, mock target switch, delete disabled-branch test (Tasks 1, 2, 4)
- `tests/test_tools.py` — set / cancel / get_active_alerts test additions and modifications (Task 2)
- `tests/test_wizard.py` — drop alert fields (Task 5)
- `tests/test_n3_wiring.py` — drop AlertsConfig import + WizardResult fields (Task 5)
- `tests/test_session_manager.py` — drop alert_config paths (Task 6)
- `tests/test_storage.py` — drop alert_config assertion (Task 6)
- `tests/test_alembic_migration.py` — drop alert_config TEXT (Task 6)
- `tests/test_okx_algo_normalization.py` — drop `alert_enabled` field (Task 5 — uses WizardResult)
- `tests/test_simulated_exchange.py` / `test_okx_websocket.py` / `test_alert_lifecycle.py` / `test_trader_agent.py` / `test_session_state.py` / `test_tool_call_recorder.py` / `test_display_cycle.py` / `test_exchange.py` / `test_fact_only_wordlist.py` — case-by-case audit for `set_alert_service` / `PriceAlertService` direct injection or wizard fixtures (Task 7)

---

## Task 1: Base API — add `set_volatility_alert` and `cancel_volatility_alert` (lazy)

**Spec ref:** OWN-1 (§2.3), §3.1 / §3.2 backend behavior, §4.1

**Files:**

- Modify: `src/integrations/exchange/base.py:107-111` (constructor — type tightening, do not delete `_alert_service` slot itself), `src/integrations/exchange/base.py:186-199` (add new methods alongside old ones)
- Test: `tests/test_tool_enhancement.py` (add new base-layer tests)

### Step 1.1 — Write failing tests for the new base API

- [ ] **Step 1.1: Write failing tests for the new base methods**

Add the following tests to `tests/test_tool_enhancement.py`. Place them immediately after `test_base_exchange_alert_consolidation` (currently around L36-87) so all alert-related base tests cluster together.

```python
def test_base_set_volatility_alert_lazy_creates_when_none():
    """First call constructs PriceAlertService with the passed args."""
    from src.integrations.exchange.base import BaseExchange
    from src.services.price_alert import PriceAlertService

    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None, params=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...
        async def fetch_order_book(self, symbol, depth=20): ...
        async def fetch_trades(self, symbol, limit=500): return []
        async def get_contract_size(self, symbol): return 1.0
        async def get_mark_price(self, symbol): return 0.0

    ex = _TestExchange()
    assert ex._alert_service is None
    assert ex.get_alert_params() is None

    ex.set_volatility_alert(threshold_pct=2.0, window_minutes=30, symbol="BTC/USDT:USDT")

    assert isinstance(ex._alert_service, PriceAlertService)
    assert ex.get_alert_params() == (2.0, 30)


def test_base_set_volatility_alert_updates_when_exists():
    """Second call invokes update_params on the same instance and clears _ticks."""
    from src.integrations.exchange.base import BaseExchange

    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None, params=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...
        async def fetch_order_book(self, symbol, depth=20): ...
        async def fetch_trades(self, symbol, limit=500): return []
        async def get_contract_size(self, symbol): return 1.0
        async def get_mark_price(self, symbol): return 0.0

    ex = _TestExchange()
    ex.set_volatility_alert(threshold_pct=5.0, window_minutes=60, symbol="BTC/USDT:USDT")
    first_instance = ex._alert_service

    # Feed a tick to populate the rolling window
    ex._alert_service.check(50000.0, 1700000000000)
    assert len(ex._alert_service._ticks) == 1

    # Second call must update in place AND clear ticks
    ex.set_volatility_alert(threshold_pct=2.0, window_minutes=30, symbol="BTC/USDT:USDT")

    assert ex._alert_service is first_instance  # same instance
    assert ex.get_alert_params() == (2.0, 30)
    assert len(ex._alert_service._ticks) == 0   # window reset


def test_base_cancel_volatility_alert_clears_to_none():
    """cancel_volatility_alert sets _alert_service back to None."""
    from src.integrations.exchange.base import BaseExchange

    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None, params=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...
        async def fetch_order_book(self, symbol, depth=20): ...
        async def fetch_trades(self, symbol, limit=500): return []
        async def get_contract_size(self, symbol): return 1.0
        async def get_mark_price(self, symbol): return 0.0

    ex = _TestExchange()
    ex.set_volatility_alert(threshold_pct=3.0, window_minutes=15, symbol="BTC/USDT:USDT")
    assert ex._alert_service is not None

    ex.cancel_volatility_alert()
    assert ex._alert_service is None
    assert ex.get_alert_params() is None


def test_base_cancel_volatility_alert_idempotent_when_already_none():
    """cancel_volatility_alert is a no-op when _alert_service is already None."""
    from src.integrations.exchange.base import BaseExchange

    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None, params=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...  # noqa: ARG002
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...
        async def fetch_order_book(self, symbol, depth=20): ...
        async def fetch_trades(self, symbol, limit=500): return []
        async def get_contract_size(self, symbol): return 1.0
        async def get_mark_price(self, symbol): return 0.0

    ex = _TestExchange()
    assert ex._alert_service is None
    ex.cancel_volatility_alert()  # must not raise
    assert ex._alert_service is None
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `pytest tests/test_tool_enhancement.py::test_base_set_volatility_alert_lazy_creates_when_none tests/test_tool_enhancement.py::test_base_set_volatility_alert_updates_when_exists tests/test_tool_enhancement.py::test_base_cancel_volatility_alert_clears_to_none tests/test_tool_enhancement.py::test_base_cancel_volatility_alert_idempotent_when_already_none -v`

Expected: 4 FAILs, all with `AttributeError: 'BaseExchange' object has no attribute 'set_volatility_alert'` or similar.

- [ ] **Step 1.3: Implement the new base methods**

Edit `src/integrations/exchange/base.py`. Add the import at the top with the other imports (around L1-9, after the `from typing import ...` line):

```python
from src.services.price_alert import PriceAlertService
```

Tighten the `_alert_service` type annotation in `__init__` (currently L110):

```python
self._alert_service: PriceAlertService | None = None
```

Insert the two new methods **immediately before** `get_alert_params` (currently L195-199 — leave `set_alert_service` / `update_alert_params` in place for now; Task 4 deletes them). Inserting before `get_alert_params` matches spec §4.1 example ordering, so after Task 4 the final order will be `set_volatility_alert` → `cancel_volatility_alert` → `get_alert_params`:

```python
def set_volatility_alert(self, threshold_pct: float,
                         window_minutes: int, symbol: str) -> None:
    """Lazy-create on first call, update_params on subsequent calls.
    Replacing parameters resets the rolling tick window (PriceAlertService
    update_params semantics)."""
    if self._alert_service is None:
        self._alert_service = PriceAlertService(symbol, window_minutes, threshold_pct)
    else:
        self._alert_service.update_params(threshold_pct, window_minutes)

def cancel_volatility_alert(self) -> None:
    """Clear the singleton; subsequent ticks no longer evaluate volatility."""
    self._alert_service = None
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `pytest tests/test_tool_enhancement.py::test_base_set_volatility_alert_lazy_creates_when_none tests/test_tool_enhancement.py::test_base_set_volatility_alert_updates_when_exists tests/test_tool_enhancement.py::test_base_cancel_volatility_alert_clears_to_none tests/test_tool_enhancement.py::test_base_cancel_volatility_alert_idempotent_when_already_none -v`

Expected: 4 PASSes.

- [ ] **Step 1.5: Run the full test_tool_enhancement.py and verify nothing broke**

Run: `pytest tests/test_tool_enhancement.py -v`

Expected: All previous tests still pass (in particular `test_base_exchange_alert_consolidation`, `test_simulated_exchange_inherits_alert_methods`, `test_okx_exchange_inherits_alert_methods` — these still target the old API which is still present).

- [ ] **Step 1.6: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_tool_enhancement.py
git commit -m "$(cat <<'EOF'
iter-w3r1-vol-alert-agent-owned: add base set_volatility_alert + cancel_volatility_alert (lazy)

OWN-1 (Task 1 of 7). New BaseExchange.set_volatility_alert(threshold_pct,
window_minutes, symbol) lazy-constructs PriceAlertService on first call,
delegates to update_params on subsequent calls (which clears the rolling
tick window per existing PriceAlertService semantics). New
cancel_volatility_alert() drops the slot back to None.

Old set_alert_service / update_alert_params methods still present and
unchanged — Task 4 deletes them after the tool layer (Task 2) and
build_services (Task 3) migrate to the new API.

_alert_service annotation tightened from Any | None to
PriceAlertService | None.

Spec: docs/superpowers/specs/2026-05-15-iter-w3r1-vol-alert-agent-owned-design.md §4.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Tool layer — migrate `set`, add `cancel`, fix `get_active_alerts` text, register in trader

**Spec ref:** OWN-2 / OWN-3 / OWN-4 / OWN-5 (§2.3), §3.1 / §3.2 / §3.3 / §3.4

**Files:**

- Modify: `src/agent/tools_execution.py:225-253` (set), append new function (cancel)
- Modify: `src/agent/tools_perception.py:621` (text)
- Modify: `src/agent/trader.py:518-535` (set wrapper), append cancel wrapper, modify `REGISTERED_TOOL_NAMES:743-757`
- Test: `tests/test_tools.py` (set behavior changes + new tests), `tests/test_tool_enhancement.py:729-768` (delete disabled test, fix mock target on the rest)

### Step 2.1 — Modify the existing set tests in test_tools.py

- [ ] **Step 2.1: Update existing set tests in test_tools.py**

Currently `tests/test_tools.py:352-390` contains 4 cases that mock `deps.exchange.update_alert_params`. The new code calls `deps.exchange.set_volatility_alert(threshold, window, symbol)`. Modify each test:

Replace the block from L352-390 with:

```python
async def test_set_price_volatility_alert_creates_when_none(deps):
    """First call: success string says 'set:', not 'replaced:'."""
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, 2.0, 30, reasoning="initial")
    assert "set:" in result
    assert "replaced" not in result
    assert "threshold=2.0%" in result
    assert "window=30min" in result
    deps.exchange.set_volatility_alert.assert_called_once_with(2.0, 30, deps.symbol)


async def test_set_price_volatility_alert_replaces_when_exists(deps):
    """Replace path: success string contains 'replaced:', 'was X/Y', 'rolling window reset'."""
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, 2.0, 30, reasoning="tighten")
    assert "replaced:" in result
    assert "was 5.0%/60min" in result
    assert "rolling window reset" in result
    deps.exchange.set_volatility_alert.assert_called_once_with(2.0, 30, deps.symbol)


async def test_set_price_volatility_alert_threshold_too_low(deps):
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, 0.05, 5, reasoning="test")
    assert "Invalid threshold_pct" in result
    deps.exchange.set_volatility_alert.assert_not_called()


async def test_set_price_volatility_alert_threshold_too_high(deps):
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, 55.0, 5, reasoning="test")
    assert "Invalid threshold_pct" in result
    deps.exchange.set_volatility_alert.assert_not_called()


async def test_set_price_volatility_alert_window_out_of_range(deps):
    from src.agent.tools_execution import set_price_volatility_alert
    deps.exchange.set_volatility_alert = MagicMock()

    result = await set_price_volatility_alert(deps, 3.0, 0, reasoning="test")
    assert "Invalid window_minutes" in result
    deps.exchange.set_volatility_alert.assert_not_called()

    result = await set_price_volatility_alert(deps, 3.0, 250, reasoning="test")
    assert "Invalid window_minutes" in result
    deps.exchange.set_volatility_alert.assert_not_called()
```

- [ ] **Step 2.2: Add the cancel tool tests in test_tools.py**

Append the following tests immediately after the set tests block:

```python
async def test_cancel_price_volatility_alert_when_active(deps):
    """Active path: clears slot, returns 'was X/Y' confirmation, records action."""
    from src.agent.tools_execution import cancel_price_volatility_alert
    deps.exchange.get_alert_params = MagicMock(return_value=(2.0, 30))
    deps.exchange.cancel_volatility_alert = MagicMock()
    result = await cancel_price_volatility_alert(deps, reasoning="market calmed")
    assert "Price volatility alert cancelled" in result
    assert "was 2.0%/30min" in result
    deps.exchange.cancel_volatility_alert.assert_called_once_with()


async def test_cancel_price_volatility_alert_when_none_idempotent(deps):
    """Already-unset path: ok with note, no mutation, no audit row."""
    from src.agent.tools_execution import cancel_price_volatility_alert
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.exchange.cancel_volatility_alert = MagicMock()
    result = await cancel_price_volatility_alert(deps, reasoning="cleanup")
    assert "No volatility alert active to cancel" in result
    deps.exchange.cancel_volatility_alert.assert_not_called()
```

Note: `_record_action` writes to `deps.db_engine` if non-None; the existing `deps` fixture in `test_tools.py` has `db_engine=None`, so `_record_action` no-ops automatically. We do not need to assert audit-row absence directly — the `cancel_volatility_alert.assert_not_called()` confirms the short-circuit.

- [ ] **Step 2.3: Add get_active_alerts text tests in test_tools.py**

Append these tests immediately after the cancel tests:

```python
async def test_get_active_alerts_volatility_section_when_unset(deps):
    """Unset path: section says 'Not set', NOT 'OFF'."""
    from src.agent.tools_perception import get_active_alerts
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    result = await get_active_alerts(deps)
    assert "Not set" in result
    assert "\nOFF" not in result  # `\n` anchor avoids matching "OFF" inside other words


async def test_get_active_alerts_volatility_section_when_set(deps):
    """Set path: section shows '{threshold}% in {window}min window'."""
    from src.agent.tools_perception import get_active_alerts
    deps.exchange.get_alert_params = MagicMock(return_value=(2.0, 30))
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    result = await get_active_alerts(deps)
    assert "2.0% in 30min window" in result
```

- [ ] **Step 2.4: Run new tests to verify they fail**

Run: `pytest tests/test_tools.py::test_set_price_volatility_alert_creates_when_none tests/test_tools.py::test_set_price_volatility_alert_replaces_when_exists tests/test_tools.py::test_cancel_price_volatility_alert_when_active tests/test_tools.py::test_cancel_price_volatility_alert_when_none_idempotent tests/test_tools.py::test_get_active_alerts_volatility_section_when_unset -v`

Expected: 5 FAILs (set tests fail because `set_volatility_alert` is not yet called by tool layer; cancel tests fail because the function does not exist; `get_active_alerts` text test fails because the string is still `OFF`).

- [ ] **Step 2.5: Implement set_price_volatility_alert migration**

Edit `src/agent/tools_execution.py:225-253`. Replace the entire `set_price_volatility_alert` function with:

```python
async def set_price_volatility_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    reasoning: str,
) -> str:
    """Set the price volatility alert (singleton). Creates if none is
    configured; otherwise replaces the existing one — replacing resets the
    rolling tick window. threshold_pct: 0.1-50, window_minutes: 1-240."""
    # Parameter validation
    if not (0.1 <= threshold_pct <= 50.0):
        note_biz_error("invalid_threshold_range")
        return f"Invalid threshold_pct: must be 0.1-50.0, got {threshold_pct}"
    if not (1 <= window_minutes <= 240):
        return f"Invalid window_minutes: must be 1-240, got {window_minutes}"

    # Capture pre-state for the success message
    prev = deps.exchange.get_alert_params()

    deps.exchange.set_volatility_alert(threshold_pct, window_minutes, deps.symbol)

    await _record_action(
        deps, action="set_price_volatility_alert",
        reasoning=f"threshold={threshold_pct}%, window={window_minutes}min | {reasoning}",
    )

    if prev is None:
        return (
            f"Price volatility alert set: threshold={threshold_pct}%, "
            f"window={window_minutes}min"
        )
    prev_t, prev_w = prev
    return (
        f"Price volatility alert replaced: threshold={threshold_pct}%, "
        f"window={window_minutes}min "
        f"(was {prev_t}%/{prev_w}min, rolling window reset)"
    )
```

- [ ] **Step 2.6: Add the cancel_price_volatility_alert function**

Append the following function immediately after the rewritten `set_price_volatility_alert` in `src/agent/tools_execution.py`:

```python
async def cancel_price_volatility_alert(
    deps: TradingDeps,
    reasoning: str,
) -> str:
    """Cancel the active price volatility alert. Idempotent: if no alert is
    set, returns ok with a note (no mutation, no audit row).

    Args:
        reasoning: brief description of your decision logic.
    """
    prev = deps.exchange.get_alert_params()
    if prev is None:
        # State-not-found → idempotent ok with note. Matches
        # cancel_price_level_alert protocol (R2-Next-E PR #47).
        return "No volatility alert active to cancel."

    prev_t, prev_w = prev
    deps.exchange.cancel_volatility_alert()
    await _record_action(
        deps, action="cancel_price_volatility_alert",
        reasoning=reasoning,
    )
    return f"Price volatility alert cancelled (was {prev_t}%/{prev_w}min)"
```

- [ ] **Step 2.7: Update get_active_alerts text in tools_perception.py**

Edit `src/agent/tools_perception.py:621`. Change:

```python
sections.append(f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\nOFF")
```

to:

```python
sections.append(f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\nNot set")
```

- [ ] **Step 2.8: Update set_price_volatility_alert wrapper in trader.py**

Edit `src/agent/trader.py:517-535`. Replace the wrapper function with:

```python
@tool
async def set_price_volatility_alert(
    ctx: RunContext[TradingDeps],
    threshold_pct: float,
    window_minutes: int,
    reasoning: str,
) -> str:
    """Set the price volatility alert (singleton). Creates if none is
    configured; otherwise replaces the existing one — replacing resets the
    rolling tick window, so the next trigger requires re-accumulating ticks
    across the full window from scratch. Use cancel_price_volatility_alert
    to remove without setting a new one.

    Related: get_active_alerts (current volatility + price-level alert state).

    Args:
        threshold_pct: alert threshold percent (0.1-50).
        window_minutes: time window in minutes (1-240).
        reasoning: brief description of your decision logic.
    """
    from src.agent.tools_execution import set_price_volatility_alert as _impl

    return await _impl(ctx.deps, threshold_pct, window_minutes, reasoning=reasoning)
```

- [ ] **Step 2.9: Add cancel_price_volatility_alert wrapper in trader.py**

Insert the following wrapper immediately after the `set_price_volatility_alert` wrapper (around L535) and before the next `@tool` (`cancel_order` at L537):

```python
@tool
async def cancel_price_volatility_alert(
    ctx: RunContext[TradingDeps],
    reasoning: str,
) -> str:
    """Cancel the active price volatility alert. Idempotent: if no alert is
    set, returns ok with a note. Use set_price_volatility_alert to configure
    a new one.

    Related: get_active_alerts (current volatility + price-level alert state).

    Args:
        reasoning: brief description of your decision logic.
    """
    from src.agent.tools_execution import cancel_price_volatility_alert as _impl

    return await _impl(ctx.deps, reasoning=reasoning)
```

- [ ] **Step 2.10: Update REGISTERED_TOOL_NAMES**

Edit `src/agent/trader.py:743-757`. The execution section header currently says `--- 执行 (13) ---`. Change it to `--- 执行 (14) ---` and insert `"cancel_price_volatility_alert"` immediately after `"set_price_volatility_alert"`:

```python
    # --- 执行 (14) ---
    "open_position",
    "close_position",
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "set_price_volatility_alert",
    "cancel_price_volatility_alert",
    "cancel_order",
    "add_price_level_alert",
    "cancel_price_level_alert",
    "update_price_level_alert",
    "set_next_wake",
    "set_next_wake_at",
    "place_limit_order",
]
```

- [ ] **Step 2.11: Delete the disabled-branch test in test_tool_enhancement.py**

Delete `tests/test_tool_enhancement.py:729-737` (`test_set_price_volatility_alert_disabled` — the entire test function). This test asserts the dead-end string `"Alerts are disabled..."` which no longer exists.

- [ ] **Step 2.12: Update mock target in remaining test_tool_enhancement.py set tests**

In `tests/test_tool_enhancement.py:739-768`, three tests still mock `deps.exchange.update_alert_params`:

- `test_set_price_volatility_alert_enabled` (L739-747)
- `test_set_price_volatility_alert_accepts_threshold_0_1` (L750-758)
- `test_set_price_volatility_alert_rejects_threshold_below_0_1` (L761-768)

For each test, apply two changes:

1. Change `deps.exchange.update_alert_params = MagicMock()` to `deps.exchange.set_volatility_alert = MagicMock()`.
2. Configure `deps.exchange.get_alert_params = MagicMock(return_value=...)` based on which path the test exercises:
   - **Create path** (test asserts `"set:"` in the success string, or asserts that an alert is created from scratch): `return_value=None`
   - **Replace path** (test asserts `"replaced:"` or that existing params are overwritten): `return_value=(prev_t, prev_w)` for whatever prior state the test wants to assert was replaced
   - **Reject path** (test asserts validation error before any mutation): `get_alert_params` is not called by the rejecting code path, so the mock setup is irrelevant; either omit the line or leave it set to anything

For each test, apply the corrected pattern. Example for `test_set_price_volatility_alert_enabled` (replace path — pre-existing 5.0/60 being tightened to 3.0/30):

```python
async def test_set_price_volatility_alert_enabled():
    from src.agent.tools_execution import set_price_volatility_alert

    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    deps.exchange.set_volatility_alert = MagicMock()

    result = await set_price_volatility_alert(deps, 3.0, 30, reasoning="tighter alert")
    assert "replaced:" in result.lower() or "3.0%" in result
```

For `test_set_price_volatility_alert_accepts_threshold_0_1`:

```python
async def test_set_price_volatility_alert_accepts_threshold_0_1():
    """R2-1 T4: tool layer accepts threshold_pct=0.1 (new lower bound)."""
    from src.agent.tools_execution import set_price_volatility_alert
    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=None)
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, threshold_pct=0.1, window_minutes=15, reasoning="test")
    assert "Price volatility alert set" in result
    assert "threshold=0.1%" in result  # `%` 锁尾防 0.15 子串误命中 (spec P2-1)
```

For `test_set_price_volatility_alert_rejects_threshold_below_0_1`:

```python
async def test_set_price_volatility_alert_rejects_threshold_below_0_1():
    """R2-1 T5: tool layer rejects threshold_pct=0.05 with new error message."""
    from src.agent.tools_execution import set_price_volatility_alert
    deps = _make_deps()
    deps.exchange.set_volatility_alert = MagicMock()
    result = await set_price_volatility_alert(deps, threshold_pct=0.05, window_minutes=15, reasoning="test")
    assert "Invalid threshold_pct: must be 0.1-50.0" in result
```

- [ ] **Step 2.13: Run all modified and new tests to verify they pass**

Run: `pytest tests/test_tools.py -k volatility -v && pytest tests/test_tool_enhancement.py -k volatility -v && pytest tests/test_tools.py::test_get_active_alerts_volatility_section_when_unset tests/test_tools.py::test_get_active_alerts_volatility_section_when_set -v`

Expected: All PASS. The deleted `test_set_price_volatility_alert_disabled` is gone — no `selected 0` warning expected since other `volatility` tests still match.

- [ ] **Step 2.14: Run trader registration drift guard**

The existing test `tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools` will detect the +1 tool — verify it passes:

Run: `pytest tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools -v`

Expected: PASS. If FAIL, the issue is likely in `REGISTERED_TOOL_NAMES` order — verify the new entry matches the `@agent.tool` registration order in `trader.py`.

- [ ] **Step 2.15: Commit**

```bash
git add src/agent/tools_execution.py src/agent/tools_perception.py src/agent/trader.py tests/test_tools.py tests/test_tool_enhancement.py
git commit -m "$(cat <<'EOF'
iter-w3r1-vol-alert-agent-owned: tool layer migration to new base API + cancel + 'Not set' text

OWN-2 / OWN-3 / OWN-4 / OWN-5 (Task 2 of 7).

set_price_volatility_alert: deletes the dead-end 'Alerts are disabled in
wizard' branch; routes to base.set_volatility_alert; success string
distinguishes 'set:' (create) vs 'replaced: ... (was X/Y, rolling window
reset)' so the agent knows the side effect on the rolling window.

cancel_price_volatility_alert: new tool. Idempotent ok-with-note when
already unset (no mutation, no audit row), matching cancel_price_level_alert
protocol from R2-Next-E PR #47.

get_active_alerts: 'OFF' → 'Not set' on the unset path, aligning with the
price-level 'No active alerts.' empty-container text.

REGISTERED_TOOL_NAMES: 32 → 33 (cancel_price_volatility_alert appended
after set_price_volatility_alert in execution section).

Spec: docs/superpowers/specs/2026-05-15-iter-w3r1-vol-alert-agent-owned-design.md §3, §4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `cli/app.py` — delete the alert init block

**Spec ref:** OWN-9 (§2.3), §4.3, §5.1 (banner row clean-up)

**Files:**

- Modify: `src/cli/app.py:915-928` (delete entire if/else block + import)

### Step 3.1 — Delete the init block

- [ ] **Step 3.1: Identify and delete the alert init block**

Edit `src/cli/app.py:915-928`. The current block is:

```python
    # Alert service
    if result.alert_enabled:
        alert_service = PriceAlertService(
            symbol=result.symbol,
            window_minutes=result.alert_window_min,
            threshold_pct=result.alert_threshold_pct,
        )
        exchange.set_alert_service(alert_service)
        sc.print(
            f"Alerts: ON ({result.alert_window_min}min / "
            f"{result.alert_threshold_pct}%)"
        )
    else:
        sc.print("Alerts: OFF")
```

Delete this entire block (the `# Alert service` comment line through the closing `sc.print("Alerts: OFF")`). Do not replace it with anything — `_alert_service` is naturally `None` from `BaseExchange.__init__`.

- [ ] **Step 3.2: Delete the unused PriceAlertService import**

`src/cli/app.py` uses a function-local import for `PriceAlertService` inside the `build_services` function body (around L795, near the top of the function). Find and remove the line:

```python
from src.services.price_alert import PriceAlertService
```

Note: this is **not** at the top of the file — `build_services` follows a project convention of importing some modules locally to avoid eager initialization. The deleted line is the function-body import, not a top-of-file import.

- [ ] **Step 3.3: Run pytest on tests that exercise build_services**

Run: `pytest tests/test_okx_algo_normalization.py -v`

Expected: PASS — but if the test fixture sets `result.alert_enabled = False` (per grep at L50), the line setting that field is now writing to a no-longer-existing dataclass field. This is fine in Task 3 because `WizardResult.alert_enabled` is still defined (Task 5 deletes it). The `build_services` code path no longer reads it.

If you see new failures, they likely indicate a test that was relying on `Alerts: ON/OFF` banner output via stdout capture — search and remove such assertions.

- [ ] **Step 3.4: Commit**

```bash
git add src/cli/app.py
git commit -m "$(cat <<'EOF'
iter-w3r1-vol-alert-agent-owned: drop build_services alert init block

OWN-9 (Task 3 of 7). The if-result.alert_enabled block at app.py:915-928
no longer has any reason to run — the agent now controls volatility-alert
state lazily via base.set_volatility_alert. Drop the entire block plus
the now-unused PriceAlertService import.

Banner lines 'Alerts: ON (...)' / 'Alerts: OFF' are removed: there is no
fact to display at startup (no on/off, no preconfigured params).

Spec: docs/superpowers/specs/2026-05-15-iter-w3r1-vol-alert-agent-owned-design.md §4.3, §5.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Delete old base API + drift guards

**Spec ref:** OWN-1 (§2.3), §4.1

**Files:**

- Modify: `src/integrations/exchange/base.py:186-193` (delete `set_alert_service` + `update_alert_params`)
- Modify: `tests/test_tool_enhancement.py:36-87` (`test_base_exchange_alert_consolidation` — rewrite to use new API), `tests/test_tool_enhancement.py:163-177` (rewrite drift guards)

### Step 4.1 — Update tests first (TDD)

- [ ] **Step 4.1: Rewrite `test_base_exchange_alert_consolidation` to use new API**

In `tests/test_tool_enhancement.py:36-87`, replace the body of `test_base_exchange_alert_consolidation` (the part after the `_TestExchange` class definition, around L72-87) with:

```python
    ex = _TestExchange()

    # No alert service → get_alert_params returns None
    assert ex.get_alert_params() is None

    # set_volatility_alert lazy-creates → get_alert_params returns the configured tuple
    ex.set_volatility_alert(threshold_pct=5.0, window_minutes=60, symbol="BTC/USDT:USDT")
    assert ex.get_alert_params() == (5.0, 60)

    # Second set updates in place
    ex.set_volatility_alert(threshold_pct=3.0, window_minutes=30, symbol="BTC/USDT:USDT")
    assert ex.get_alert_params() == (3.0, 30)

    # cancel returns to None
    ex.cancel_volatility_alert()
    assert ex.get_alert_params() is None
```

- [ ] **Step 4.2: Rewrite the SimulatedExchange / OKXExchange drift guards**

In `tests/test_tool_enhancement.py:163-177`, replace the two existing tests with:

```python
def test_simulated_exchange_inherits_volatility_alert_methods():
    """SimulatedExchange should NOT override set_volatility_alert / cancel_volatility_alert."""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import BaseExchange
    assert SimulatedExchange.set_volatility_alert is BaseExchange.set_volatility_alert
    assert SimulatedExchange.cancel_volatility_alert is BaseExchange.cancel_volatility_alert


def test_okx_exchange_inherits_volatility_alert_methods():
    """OKXExchange should NOT override set_volatility_alert / cancel_volatility_alert."""
    from src.integrations.exchange.okx import OKXExchange
    from src.integrations.exchange.base import BaseExchange
    assert OKXExchange.set_volatility_alert is BaseExchange.set_volatility_alert
    assert OKXExchange.cancel_volatility_alert is BaseExchange.cancel_volatility_alert
```

- [ ] **Step 4.3: Delete the old base methods**

Edit `src/integrations/exchange/base.py:186-199`. Delete these two methods entirely:

```python
def set_alert_service(self, service: Any) -> None:
    """Inject PriceAlertService instance."""
    self._alert_service = service

def update_alert_params(self, threshold_pct: float, window_minutes: int) -> None:
    """Update price alert parameters. Delegates to alert service if set."""
    if self._alert_service:
        self._alert_service.update_params(threshold_pct, window_minutes)
```

`get_alert_params` (the next method) stays. `set_volatility_alert` / `cancel_volatility_alert` (added in Task 1) stay.

- [ ] **Step 4.4: Run drift guards and consolidation test**

Run: `pytest tests/test_tool_enhancement.py::test_base_exchange_alert_consolidation tests/test_tool_enhancement.py::test_simulated_exchange_inherits_volatility_alert_methods tests/test_tool_enhancement.py::test_okx_exchange_inherits_volatility_alert_methods -v`

Expected: 3 PASSes.

- [ ] **Step 4.5: Run a `src/`-only grep to confirm zero remaining callers in production code**

Run: `grep -rn "set_alert_service\|update_alert_params" src/`

Expected: zero hits in `src/`. If hits remain, you missed a caller in earlier tasks — fix in this commit.

**Do NOT grep `tests/` here.** Five integration tests in `tests/test_okx_websocket.py`, `tests/test_simulated_exchange.py`, and `tests/test_exchange.py` still reference the old API (their purpose was to validate the old API behavior). Those tests are deleted/migrated in Task 7 per the categorized cleanup list. Repo-wide grep including `tests/` is run in Step 7.3.

- [ ] **Step 4.6: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_tool_enhancement.py
git commit -m "$(cat <<'EOF'
iter-w3r1-vol-alert-agent-owned: delete old base set_alert_service / update_alert_params API

OWN-1 cleanup (Task 4 of 7). After Task 2 (tool layer) and Task 3
(cli/app build_services) migrated to set_volatility_alert /
cancel_volatility_alert, the old set_alert_service / update_alert_params
methods on BaseExchange have no callers. Delete them.

Drift-guard tests rewritten to assert subclasses inherit the new methods
unchanged. test_base_exchange_alert_consolidation rewritten to exercise
the new lazy-create + cancel flow.

Spec: docs/superpowers/specs/2026-05-15-iter-w3r1-vol-alert-agent-owned-design.md §4.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wizard / config / session_manager alert-path cleanup

**Spec ref:** OWN-6, OWN-7, OWN-8 partial — wizard side and session_manager `WizardResult` plumbing (§2.3, §5.1, §5.2). The `Session.alert_config` model field + migration entry are deferred to Task 6.

**Atomic boundary rationale:** `WizardResult.alert_*` field deletion is tightly coupled to every producer (`wizard._step_risk_scheduling`, `session_manager` read path) and every consumer (`session_manager` write path, `cli/app.py` — already done in Task 3). All four must move in one commit, otherwise dataclass construction raises `TypeError: unexpected keyword argument`. The `Session.alert_config` model field is **independent** of `WizardResult.alert_*` and can land in a separate commit (Task 6) — at the end of Task 5, the column will exist on the model but no code reads or writes it.

**Files:**

- Modify: `src/cli/wizard.py:37-39` (`WizardResult` fields), `src/cli/wizard.py:250-260` (`_step_risk_scheduling`), `src/cli/wizard.py:321-327` (`_show_summary`)
- Modify: `src/config.py:59-62` (`AlertsConfig`), `src/config.py:93` (`Settings.alerts`)
- Modify: `src/cli/session_manager.py:137-145` (alert read block), `:189-191` (WizardResult kwargs), `:215-222` (alert write block), `:239` (`alert_config=alert_config` Session kwarg)
- Modify: `config/settings.yaml` AND `config/settings_sim.yaml` (delete `alerts:` section + comment header from each)
- Modify: `tests/test_wizard.py` (~14 line refs)
- Modify: `tests/test_n3_wiring.py:17, 41, 75-77`
- Modify: `tests/test_okx_algo_normalization.py:50` (drop `result.alert_enabled = False`)
- Modify: `tests/test_session_manager.py` (drop the `WizardResult` alert kwargs in fixtures + drop `alert_enabled` / `alert_window_min` asserts; the alert_config-specific test_restore_session_null_alert_config and storage assertions stay until Task 6 since the model field is still present)

### Step 5.1 — Update wizard tests to drop alert fields

- [ ] **Step 5.1: Update test_wizard.py to drop all alert references**

`tests/test_wizard.py` mixes three categories of alert references: dataclass field references (kwargs / asserts / dict literals), patched-mock side_effect / return_value sequences that interleave alert prompts with adjacent prompts, and one test (`test_step_risk_alerts_off`) whose entire purpose is the deleted OFF path.

**TDD red/green status preview** — by the end of this Step, the test suite will be RED (this is intentional; do not panic):

- **Cat 1 / Cat 2 / Cat 4 / Cat 5** edits leave tests RED until Steps 5.5-5.12 land the matching production deletions (see Step 5.4 for the precise expected failure modes — `TypeError` for missing dataclass fields, `StopIteration` for trimmed mock side_effects).
- **Cat 3** is a structural delete (`test_step_risk_alerts_off`); it does not affect any other test's color.

The red bar turns green at Step 5.14.

**Category 1 — field references (kwargs, asserts, dict literals):**

- L35-37: delete the three keyword args from the `WizardResult(...)` constructor.
- L251-253: delete the three asserts (`alert_enabled is True`, `alert_window_min == 5`, `alert_threshold_pct == 3.0`).
- L329, L344, L581-582, L621-622, L662-663: delete each `"alert_enabled": ..., "alert_window_min": ..., "alert_threshold_pct": ...` triple from dict literals.
- L465: delete the three keyword args.
- L478: delete `patch("src.services.price_alert.PriceAlertService"):` from the `with` statement (rebalance indentation).
- L500: delete the three keyword args.

**Category 2 — `_step_risk_scheduling` mock sequences in `test_step_risk_sim_defaults` (around L243-256):**

The test currently patches:

```python
@patch("src.cli.wizard.IntPrompt.ask", side_effect=[15, 5, 500000])    # interval, alert_window, budget
@patch("src.cli.wizard.FloatPrompt.ask", return_value=3.0)              # alert_threshold (only call)
@patch("src.cli.wizard.Confirm.ask", side_effect=[False, True])         # approval OFF, alerts ON
def test_step_risk_sim_defaults(mock_confirm, mock_float, mock_int):
    ...
    assert result["alert_enabled"] is True
    assert result["alert_window_min"] == 5
    assert result["alert_threshold_pct"] == 3.0
```

After Steps 5.6-5.7 delete the alert prompts in `_step_risk_scheduling`, the function only calls IntPrompt twice (interval, budget), Confirm once (approval), and FloatPrompt zero times. Update the test to:

```python
@patch("src.cli.wizard.IntPrompt.ask", side_effect=[15, 500000])       # interval, budget
@patch("src.cli.wizard.Confirm.ask", side_effect=[False])              # approval OFF
def test_step_risk_sim_defaults(mock_confirm, mock_int):
    from src.cli.wizard import _step_risk_scheduling
    result = _step_risk_scheduling(Settings(), "simulated", Console())
    assert result["scheduler_interval_min"] == 15
    assert result["approval_enabled"] is False
    assert result["token_budget"] == 500000
```

The `FloatPrompt.ask` decorator is removed entirely (no calls remain).

**Category 3 — `test_step_risk_alerts_off` (L257-264) full deletion:**

This test's entire purpose is the alerts-OFF wizard branch, which no longer exists. Delete the entire function (decorators + body + signature). Do not "collapse" — there is nothing meaningful to collapse to.

**Category 4 — `test_run_wizard_full_flow` mock sequences (around L361-380):**

The test currently patches:

```python
patch("src.cli.wizard.FloatPrompt.ask", side_effect=[
    0.05, 100.0,        # Step 1: fee_rate, balance
    3.0,                 # Step 4: threshold
])
patch("src.cli.wizard.IntPrompt.ask", side_effect=[
    1,                   # Step 3: select model #1
    15,                  # Step 4: interval
    5,                   # Step 4: alert window
    500000,              # Step 4: budget
])
patch("src.cli.wizard.Confirm.ask", side_effect=[
    False,               # Step 4: approval OFF (sim default)
    True,                # Step 4: alerts ON
    True,                # Summary: confirm
])
```

After alert prompts are deleted, remove the alert-corresponding entries from each `side_effect` list:

```python
patch("src.cli.wizard.FloatPrompt.ask", side_effect=[
    0.05, 100.0,        # Step 1: fee_rate, balance
    # threshold entry removed
])
patch("src.cli.wizard.IntPrompt.ask", side_effect=[
    1,                   # Step 3: select model #1
    15,                  # Step 4: interval
    # alert window entry removed
    500000,              # Step 4: budget
])
patch("src.cli.wizard.Confirm.ask", side_effect=[
    False,               # Step 4: approval OFF (sim default)
    # alerts ON entry removed
    True,                # Summary: confirm
])
```

If `FloatPrompt.ask` `side_effect` list ends up with a trailing comma after `100.0`, Python is fine with that. If you prefer, write `side_effect=[0.05, 100.0]` on one line.

**Category 5 — `test_run_wizard_reject_then_confirm` mock sequences (around L405-425):**

This test runs the wizard twice (reject then confirm), so the side_effect lists carry **two rounds** of values. Apply the Category 4 pattern to **both rounds**:

```python
patch("src.cli.wizard.FloatPrompt.ask", side_effect=[
    # Round 1
    0.05, 100.0,    # threshold removed
    # Round 2
    0.05, 200.0,    # threshold removed
])
patch("src.cli.wizard.IntPrompt.ask", side_effect=[
    # Round 1
    1, 15, 500000,  # alert window removed
    # Round 2
    1, 15, 500000,  # alert window removed
])
patch("src.cli.wizard.Confirm.ask", side_effect=[
    # Round 1
    False, False,   # approval OFF, summary REJECT (alerts ON entry removed)
    # Round 2
    False, True,    # approval OFF, summary CONFIRM (alerts ON entry removed)
])
```

**Verification:** without the Category 2/4/5 mock sequence updates, `pytest tests/test_wizard.py` will raise `StopIteration` from a `MagicMock` whose `side_effect` iterator is exhausted, or `RuntimeError` propagated from inside Rich prompts.

- [ ] **Step 5.2: Update test_n3_wiring.py**

In `tests/test_n3_wiring.py`:

- L17: remove `AlertsConfig` from the import list.
- L41: delete `alerts=AlertsConfig(enabled=False),` from the `Settings(...)` constructor.
- L75-77: delete `alert_enabled=False, alert_window_min=60, alert_threshold_pct=5.0,` from the `WizardResult(...)` constructor.

- [ ] **Step 5.3: Update test_okx_algo_normalization.py**

In `tests/test_okx_algo_normalization.py:50`, delete:

```python
result.alert_enabled = False
```

This line was setting a field on a `WizardResult` mock; the field is being deleted in this task.

- [ ] **Step 5.4: Run wizard / n3 / okx_algo tests — expect RED bar (TDD red phase)**

Run: `pytest tests/test_wizard.py tests/test_n3_wiring.py tests/test_okx_algo_normalization.py -v 2>&1 | head -80`

Expected: **multiple FAILures** — this is the red bar. Specific failure modes:

- `TypeError: WizardResult() missing 3 required positional arguments: 'alert_enabled', 'alert_window_min', 'alert_threshold_pct'` — Step 5.1 Cat 1 deleted the kwargs but Step 5.5 has not yet deleted the dataclass fields, so construction now fails.
- `StopIteration` from a `MagicMock` whose `side_effect` iterator is exhausted — Step 5.1 Cat 2/4/5 trimmed the side_effect lists but `_step_risk_scheduling` still calls the alert prompts (Steps 5.6-5.7 delete them).

These FAILs are expected — they will turn GREEN after Steps 5.5-5.12 land the matching production-code deletions. If you see a FAILure pattern that does NOT match the two above (e.g., AssertionError about an alert value still being set), it indicates an incomplete test edit in Step 5.1 — go back and fix.

- [ ] **Step 5.5: Delete WizardResult fields**

Edit `src/cli/wizard.py:37-39`. Delete:

```python
    alert_enabled: bool
    alert_window_min: int | None
    alert_threshold_pct: float | None
```

- [ ] **Step 5.6: Delete the alert prompts in _step_risk_scheduling**

Edit `src/cli/wizard.py:250-260`. The current section is:

```python
    alert_enabled = Confirm.ask("  Price alerts", default=defaults.alerts.enabled, console=console)

    alert_window = None
    alert_threshold = None
    if alert_enabled:
        alert_window = IntPrompt.ask(
            "    Window (min)", default=defaults.alerts.window_minutes, console=console,
        )
        alert_threshold = FloatPrompt.ask(
            "    Threshold (%)", default=defaults.alerts.threshold_pct, console=console,
        )
```

Delete this entire block. Then in the returned dict (L264-271), delete:

```python
        "alert_enabled": alert_enabled,
        "alert_window_min": alert_window,
        "alert_threshold_pct": alert_threshold,
```

The returned dict now has only `scheduler_interval_min`, `approval_enabled`, `token_budget`.

- [ ] **Step 5.7: Delete the Alerts row in _show_summary**

Edit `src/cli/wizard.py:321-327`. Delete:

```python
    if data["alert_enabled"]:
        alert_str = (
            f"ON ({data['alert_window_min']}min / {data['alert_threshold_pct']}%)"
        )
    else:
        alert_str = "OFF"
    table.add_row("Alerts", alert_str)
```

- [ ] **Step 5.8: Delete AlertsConfig and Settings.alerts**

Edit `src/config.py`:

- L59-62: delete the `AlertsConfig` class definition entirely.
- L93: delete the line `alerts: AlertsConfig = AlertsConfig()`.

- [ ] **Step 5.9: Clean settings.yaml AND settings_sim.yaml**

Both `config/settings.yaml` and `config/settings_sim.yaml` carry the `alerts:` section + comment header (verified L34-38 in `settings.yaml`, L36-40 in `settings_sim.yaml`). Both must be cleaned in this step — leaving stale `alerts:` in either file misleads readers about whether the config knob is still live.

Run: `grep -n "^alerts:\|=== Price Alert Configuration ===" config/settings.yaml config/settings_sim.yaml`

For each file where the section exists, delete **both**:

1. The section comment header `# === Price Alert Configuration ===`
2. The `alerts:` block (the `alerts:` line plus its indented child lines `enabled:`, `window_minutes:`, `threshold_pct:`)

The comment header is left orphaned without (2), so both must be removed together per file. If `grep` returns no output for a file, no change to that file is needed.

- [ ] **Step 5.10: Delete the alert read block in session_manager restore path**

Edit `src/cli/session_manager.py:137-145`. Delete:

```python
    # Alert config
    alert_enabled = False
    alert_window = None
    alert_threshold = None
    if s.alert_config:
        alert_data = json.loads(s.alert_config)
        alert_enabled = alert_data.get("enabled", False)
        alert_window = alert_data.get("window")
        alert_threshold = alert_data.get("threshold")
```

The `Session.alert_config` field on the model is still present (Task 6 deletes it), but reading it here is no longer needed because `WizardResult` no longer carries the three alert fields.

- [ ] **Step 5.11: Delete the WizardResult alert kwargs in session_manager**

Edit `src/cli/session_manager.py:189-191`. The `return WizardResult(...)` block currently passes:

```python
        alert_enabled=alert_enabled,
        alert_window_min=alert_window,
        alert_threshold_pct=alert_threshold,
```

Delete these three keyword arguments. Without this step, the dataclass construction will raise `TypeError: unexpected keyword argument 'alert_enabled'` because Step 5.5 already deleted the fields.

- [ ] **Step 5.12: Delete the alert write block in session_manager create path**

Edit `src/cli/session_manager.py:215-222`. Delete the entire block:

```python
        # Alert config JSON
        alert_config = None
        if result.alert_enabled:
            alert_config = json.dumps({
                "enabled": True,
                "window": result.alert_window_min,
                "threshold": result.alert_threshold_pct,
            })
```

Then at L239, delete `alert_config=alert_config,` from the `Session(...)` constructor kwargs (the `alert_config` local variable is now gone, so a kwarg referencing it would `NameError`).

The `Session.alert_config` mapped column is still present on the model (Task 6 deletes it). After this step, instances will be created with the column defaulting to `NULL` — which is also its post-Task-6 behavior on existing DBs.

- [ ] **Step 5.13: Update test_session_manager fixtures for WizardResult kwargs**

In `tests/test_session_manager.py`, remove `alert_enabled=...,  alert_window_min=...,  alert_threshold_pct=...,` from each `WizardResult(...)` constructor at L255, L297, L356, L452, plus L100-101 asserts (`assert result.alert_enabled is True / .alert_window_min == 5`).

Do **not** in this step touch:

- L33 migration columns list (touched in Task 6)
- L73, L126 `Session(alert_config=...)` constructor calls (touched in Task 6)
- L110-144 `test_restore_session_null_alert_config` (deleted in Task 6)
- L275 `json.loads(s.alert_config)` block (touched in Task 6)

The fixtures' `WizardResult` constructions must drop the alert kwargs in Task 5; the `Session.alert_config` references stay until Task 6.

- [ ] **Step 5.14: Run wizard / n3 / okx_algo / session_manager tests**

Run: `pytest tests/test_wizard.py tests/test_n3_wiring.py tests/test_okx_algo_normalization.py tests/test_session_manager.py -v 2>&1 | tail -30`

Expected: All PASS.

If `tests/test_wizard.py` has parameterized fixtures based on `alert_enabled`, you may need to rebalance the test count. The expected delta is removing 1-3 cases (the OFF parameterization). `test_session_manager.py::test_restore_session_null_alert_config` will likely still pass because `Session.alert_config` is still on the model — Task 6 deletes that test.

- [ ] **Step 5.15: Commit**

```bash
git add src/cli/wizard.py src/config.py src/cli/session_manager.py config/settings.yaml config/settings_sim.yaml tests/test_wizard.py tests/test_n3_wiring.py tests/test_okx_algo_normalization.py tests/test_session_manager.py
git commit -m "$(cat <<'EOF'
iter-w3r1-vol-alert-agent-owned: delete wizard alert step + WizardResult.alert_* + AlertsConfig + session_manager paths

OWN-6, OWN-7, OWN-8 partial (Task 5 of 7). The wizard no longer asks for
volatility alert config (no enable confirm, no window prompt, no threshold
prompt). The 'Alerts' row in the wizard summary table is removed.
WizardResult sheds the three alert fields. AlertsConfig and Settings.alerts
are deleted. config/settings.yaml alerts: section removed if it was present.

session_manager: alert read block (parsing s.alert_config JSON), alert write
block (building alert_config JSON from result.alert_*), alert_config Session
kwarg, and WizardResult alert kwargs all deleted in this commit because
they are tightly coupled to the WizardResult.alert_* field deletion. Without
co-deletion, the dataclass construction would raise TypeError.

Session.alert_config column on the model and the ('alert_config', 'TEXT')
ad-hoc migration entry are NOT yet touched — Task 6 handles them. After
this commit, the column exists but no code reads or writes it; new sessions
will have the column NULL.

Tests updated: alert_enabled / alert_window_min / alert_threshold_pct
references removed across test_wizard.py, test_n3_wiring.py,
test_okx_algo_normalization.py, test_session_manager.py (WizardResult
fixtures + asserts only — alert_config-specific assertions deferred to
Task 6).

Spec: docs/superpowers/specs/2026-05-15-iter-w3r1-vol-alert-agent-owned-design.md §5.1, §5.2, §5.3 partial

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `Session.alert_config` — delete model field + ad-hoc migration entry

**Spec ref:** OWN-10 + OWN-8 remainder (§2.3), §5.3

**Atomic boundary rationale:** After Task 5, no code reads or writes `Session.alert_config`. This task deletes the model field, the ad-hoc PRAGMA migration entry, and the test assertions that target the field. The column on existing SQLite databases is left as a dead column per spec §5.3 (no Alembic migration).

**Files:**

- Modify: `src/storage/models.py:51` (delete `alert_config` mapped column)
- Modify: `src/cli/session_manager.py:30` (delete migration list entry — only remaining session_manager touch from this iter)
- Modify: `tests/test_session_manager.py:33` (migration columns assertion), `:73, 126` (Session fixture kwargs), `:110-144` (delete `test_restore_session_null_alert_config`), `:275` (delete `json.loads(s.alert_config)` block)
- Modify: `tests/test_storage.py:230` (drop `assert s.alert_config is None`)
- Modify: `tests/test_alembic_migration.py:63` (drop `alert_config TEXT`)

### Step 6.1 — Update tests first

- [ ] **Step 6.1: Update test_storage.py**

In `tests/test_storage.py:230`, delete the line:

```python
    assert s.alert_config is None
```

If this line is the only assertion in the test, delete the whole test. Otherwise just remove this line.

- [ ] **Step 6.2: Update test_alembic_migration.py**

In `tests/test_alembic_migration.py:63`, the expected schema includes:

```python
            alert_config TEXT,
```

Delete this line from the expected schema string. Verify the surrounding `CREATE TABLE` statement is still well-formed (no trailing comma issue — if `alert_config TEXT,` is the last column line before the closing `);`, the previous line's trailing comma must also be removed).

- [ ] **Step 6.3: Update test_session_manager.py**

In `tests/test_session_manager.py`:

- L33: in the migration columns list, remove `"alert_config"` (the migration check assertion).
- L73, L126: remove `alert_config=alert_cfg,` (or `alert_config=None,`) from the `Session(...)` constructor used in fixtures.
- L110-144: delete the entire test `test_restore_session_null_alert_config` (the `Session.alert_config` field no longer exists).
- L275-277: remove ONLY these three lines:

```python
    alert = json.loads(s.alert_config)
    assert alert["enabled"] is True
    assert alert["window"] == 10
```

**DO NOT delete `assert s.status == "active"` (around L278)** — this assertion is unrelated to alert_config and must stay. The three alert lines are visually contiguous with the s.status assert; a careless "delete the block" would over-delete. Verify after edit by greppling: `grep -n "assert s.status" tests/test_session_manager.py` should still show the L278 assertion intact.

(L100-101 asserts and L255 / L297 / L356 / L452 `WizardResult` kwargs were already handled in Task 5.)

- [ ] **Step 6.4: Run pre-implementation tests**

Run: `pytest tests/test_session_manager.py tests/test_storage.py tests/test_alembic_migration.py -v 2>&1 | tail -20`

Expected: tests still pass — production still has `Session.alert_config`, and the test edits have only removed assertions, not added new ones. The point is to baseline.

- [ ] **Step 6.5: Delete the `alert_config` column from the model**

Edit `src/storage/models.py:51`. Delete:

```python
    alert_config: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON: {enabled, window, threshold}
```

- [ ] **Step 6.6: Delete the migration entry**

Edit `src/cli/session_manager.py:30`. In the `migrations` list inside `_migrate_session_table`, delete:

```python
        ("alert_config", "TEXT"),
```

- [ ] **Step 6.7: Run all DB / session tests to verify**

Run: `pytest tests/test_session_manager.py tests/test_storage.py tests/test_alembic_migration.py -v`

Expected: All PASS.

If `tests/test_session_manager.py::test_restore_session_null_alert_config` was not deleted in Step 6.3, it will now fail with `AttributeError: 'Session' object has no attribute 'alert_config'`. Delete it.

- [ ] **Step 6.8: Commit**

```bash
git add src/storage/models.py src/cli/session_manager.py tests/test_session_manager.py tests/test_storage.py tests/test_alembic_migration.py
git commit -m "$(cat <<'EOF'
iter-w3r1-vol-alert-agent-owned: drop Session.alert_config field + ad-hoc migration entry

OWN-10 + OWN-8 remainder (Task 6 of 7). Session.alert_config mapped column
deleted from the SQLAlchemy model. The ('alert_config', 'TEXT') entry
deleted from session_manager._migrate_session_table's ad-hoc PRAGMA
migration list. (Other session_manager alert paths were already deleted
in Task 5 alongside WizardResult.alert_* removal — they were tightly
coupled.)

DB column on existing SQLite databases is left as a dead column — no
Alembic migration. Rationale: alert_config was never alembic-managed
(ad-hoc PRAGMA mechanism), no precedent for column drops in this project,
and SQLite < 3.35 lacks ALTER TABLE DROP COLUMN. Dead column on existing
DBs is invisible and ~zero storage (NULL).

Tests updated: test_session_manager (migration cols + Session fixture
kwargs + delete null-config restore test + delete alert_config JSON
parse), test_storage (drop alert_config assertion),
test_alembic_migration (drop alert_config TEXT from expected schema).

Spec: docs/superpowers/specs/2026-05-15-iter-w3r1-vol-alert-agent-owned-design.md §5.3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Integration / scenario test sweep + repo grep guard + full pytest

**Spec ref:** §6.4 (verification strategy)

**Files:**

- Audit and fix as needed: `tests/test_simulated_exchange.py`, `tests/test_okx_websocket.py`, `tests/test_alert_lifecycle.py`, `tests/test_trader_agent.py`, `tests/test_session_state.py`, `tests/test_tool_call_recorder.py`, `tests/test_display_cycle.py`, `tests/test_exchange.py`, `tests/test_fact_only_wordlist.py`

### Step 7.1 — Find remaining references

- [ ] **Step 7.1: Run the verification grep**

Run:

```bash
grep -rn "alert_config\|alert_enabled\|alert_window\|alert_threshold\|set_alert_service\|update_alert_params\|AlertsConfig" tests/ src/
```

Expected hits: zero in `src/`. In `tests/`, remaining hits should only be in test files inside `tests/` that were not addressed in Tasks 5/6 — these are integration tests that may inject `PriceAlertService` directly.

- [ ] **Step 7.2: Categorized cleanup of remaining alert-API references in integration tests**

Remaining references in `tests/` after Tasks 1-6 fall into three categories. Handle each category differently:

**Category A — Tests whose purpose is the deleted old API (DELETE these tests entirely):**

The following 5 tests exist solely to validate `set_alert_service` / `update_alert_params` behavior. The new model (lazy create via `set_volatility_alert`, no separate `set_alert_service` injection step) makes their purpose obsolete. The new API is already covered by Task 1's tests (`test_base_set_volatility_alert_lazy_creates_when_none` etc.) and Task 4's drift guards.

- `tests/test_okx_websocket.py:758` — `test_okx_set_alert_service` (asserts old injection semantics)
- `tests/test_okx_websocket.py:772` — `test_okx_update_alert_params_delegates` (asserts old delegation; new `set_volatility_alert` internally encapsulates this)
- `tests/test_simulated_exchange.py:726` — `test_simulated_exchange_update_alert_params` (same as above for SimulatedExchange)
- `tests/test_exchange.py:348` — `test_base_exchange_set_alert_service_default_noop` (asserts old default no-op; method no longer exists)
- `tests/test_exchange.py:386` — `test_base_exchange_update_alert_params_default_noop` (same as above)

For each, delete the entire function (decorators + body + signature). If the file uses `pytest.mark.asyncio` decorators or shared fixtures, delete only the test function, not the shared setup.

**Category B — Tests whose purpose is the tick→callback path (MIGRATE injection mechanism, preserve mock semantics):**

The following 7 tests inject a `MagicMock()` (not a real `PriceAlertService`) and control behavior via `mock_service.check.return_value = mock_alert` or `.side_effect = [...]`:

- `tests/test_simulated_exchange.py:641` — `test_simulated_exchange_alert_service_integration`
- `tests/test_simulated_exchange.py:689` — `test_simulated_exchange_no_alert_when_service_returns_none`
- `tests/test_simulated_exchange.py:747` — `test_simulated_exchange_alert_callback_outside_lock`
- `tests/test_okx_websocket.py:610` — `test_watch_ticker_loop_skips_none_timestamp`
- `tests/test_okx_websocket.py:665` — `test_watch_ticker_loop_skips_none_bid`
- `tests/test_okx_websocket.py:787` — `test_watch_ticker_loop_triggers_alert`
- `tests/test_okx_websocket.py:853` — `test_watch_ticker_loop_no_alert_when_service_returns_none`

**Naïve substitution does NOT work.** Calling `exchange.set_volatility_alert(2.0, 5, "BTC/USDT:USDT")` would lazy-construct a real `PriceAlertService` whose `.check()` runs the real rolling-window logic; the test's `mock_service.check.return_value = mock_alert` would have no effect because `_alert_service` is the real instance, not the mock. Tests would silently pass with the wrong semantics.

**Correct migration pattern** — bypass `set_volatility_alert` and write the mock directly into the slot (which is what the deleted `set_alert_service` was doing internally — see `base.py:186-188` pre-Task-4: `def set_alert_service(self, service): self._alert_service = service`):

```python
# Before (Task 4 deleted this method)
exchange.set_alert_service(mock_service)

# After (direct slot assignment — preserves mock control)
exchange._alert_service = mock_service
```

Apply this pattern to each of the 7 tests above. Do **not** delete these tests — they verify the tick→callback path which is unchanged in this iter. Do **not** remove the `from src.services.price_alert import PriceAlertService` / `AlertInfo` imports — `AlertInfo` is still used to construct `mock_alert` literals.

**Drift-guard caveat**: writing to the private attribute `_alert_service` is intentional in tests (it was the same effective operation the old public method performed). If a future test-style policy bans private-attribute writes in tests, replace with a `pytest.MonkeyPatch.setattr(exchange, "_alert_service", mock_service)` or `unittest.mock.patch.object(exchange, "_alert_service", mock_service)` — both have the same semantics for this slot.

**Category C — `PriceAlertService` direct unit tests (LEAVE UNCHANGED):**

`tests/test_price_alert.py` exercises the `PriceAlertService` class directly (`check`, `update_params`, `get_params`). These methods are untouched by this iter. Leave the file unchanged.

**Category D — `tests/test_fact_only_wordlist.py` audit:**

If the wordlist contains entries referring to the deleted dead-end string (`"Alerts are disabled"`, `"Enable alerts in wizard"`) or the deleted `OFF` text, those entries are now obsolete. Remove them. If the wordlist is unrelated, leave it.

**Discovery commands:**

```bash
# Category A + B candidates
grep -rn "set_alert_service\|update_alert_params" tests/

# Category B injection-only candidates (likely intersects with above)
grep -rn "PriceAlertService" tests/test_simulated_exchange.py tests/test_okx_websocket.py tests/test_alert_lifecycle.py tests/test_trader_agent.py tests/test_session_state.py tests/test_tool_call_recorder.py tests/test_display_cycle.py tests/test_exchange.py

# Category D audit
grep -n "alerts? (are )?disabled\|Enable alerts\|alert.*OFF" tests/test_fact_only_wordlist.py
```

- [ ] **Step 7.3: Re-run the verification grep**

Run:

```bash
grep -rn "alert_config\|alert_enabled\|alert_window\|alert_threshold\|set_alert_service\|update_alert_params\|AlertsConfig" tests/ src/
```

Expected: zero hits. If hits remain, fix them.

Run also:

```bash
grep -rn "Alerts are disabled" src/ tests/
```

Expected: zero hits (the dead-end string is gone everywhere).

- [ ] **Step 7.4: Run the full pytest suite**

Run: `pytest -q 2>&1 | tail -30`

Expected: **1693-1700** passed (baseline 1694; precise net +1 from 9 added — 4 Task 1 + 1 net Task 2 set + 2 Task 2 cancel + 2 Task 2 get_active_alerts — minus 8 deleted — 1 disabled + 1 alerts_off + 1 restore_null + 5 Task 7 Cat A). Acceptance: count lands in this band; any unexpected failures investigated, not suppressed.

If failures occur:
- Failures in tests not previously touched in this iter → audit whether the test depended on `WizardResult.alert_*` or the old wizard banner; fix per Step 7.2 pattern.
- Failures in `test_trader_agent.py::test_registered_tool_names_matches_agent_tools` → re-check Step 2.10 ordering.

- [ ] **Step 7.5: Manual smoke (optional but recommended for first sim run)**

Per spec §6.4, run a fresh sim session and verify:

1. Wizard does not prompt `Price alerts`; no `Alerts` row in summary; no `Alerts: ON / OFF` banner in startup output.
2. Agent calls `set_price_volatility_alert(2.0, 30, "test")` → `get_active_alerts` shows `2.0% in 30min window`; success string contains `"set:"`.
3. Agent calls `set_price_volatility_alert(1.5, 60, "tighter")` → success string contains `"replaced:"` and `"rolling window reset"`.
4. Agent calls `cancel_price_volatility_alert("done")` → `get_active_alerts` shows `Not set`; success string contains `"was 1.5%/60min"`.
5. Agent calls `cancel_price_volatility_alert("again")` → `"No volatility alert active to cancel."`; no exception.
6. Restart the session → `get_active_alerts` shows `Not set` (no carry-over).

The first 1-2 are the only ones reachable without a live agent; per memory `feedback_long_walltime_experiments`, defer the 3-6 sim verification to the user.

- [ ] **Step 7.6: Final commit (sweep + verification)**

```bash
git add tests/
git commit -m "$(cat <<'EOF'
iter-w3r1-vol-alert-agent-owned: integration-test sweep + grep verification

Task 7 of 7. Audit-and-fix sweep across remaining integration tests that
referenced PriceAlertService directly or used set_alert_service for
injection: test_simulated_exchange, test_okx_websocket, test_alert_lifecycle,
test_trader_agent, test_session_state, test_tool_call_recorder,
test_display_cycle, test_exchange, test_fact_only_wordlist.

All references migrated to exchange.set_volatility_alert(threshold,
window, symbol). Repo-wide grep for {alert_config, alert_enabled,
alert_window, alert_threshold, set_alert_service, update_alert_params,
AlertsConfig, "Alerts are disabled"} returns zero hits in src/ and tests/.

Full pytest suite: 1693-1700 passed (precise net +1 from 9 added minus
8 deleted per categorized cleanup; band allows -1 ~ +6 tolerance).

Spec: docs/superpowers/specs/2026-05-15-iter-w3r1-vol-alert-agent-owned-design.md §6.4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If Step 7.2 found no hits to fix, no `git add` is needed for this step — skip the commit and just verify the grep + pytest.

---

## Acceptance criteria (full plan)

Before declaring this iter complete and opening a PR:

1. ✅ `grep -rn "alert_config|alert_enabled|alert_window|alert_threshold|set_alert_service|update_alert_params|AlertsConfig" tests/ src/` returns zero hits.
2. ✅ `grep -rn "Alerts are disabled" src/ tests/` returns zero hits.
3. ✅ `pytest -q` reports 1693-1700 passed; zero failed.
4. ✅ `git log iter-w3r1-vol-alert-agent-owned ^main --oneline` shows 8 commits (1 spec + 7 task commits, possibly skipping commit 7 if no fixes needed).
5. ✅ `REGISTERED_TOOL_NAMES` list contains exactly 33 entries; `cancel_price_volatility_alert` is positioned immediately after `set_price_volatility_alert` in the execution section.
6. ✅ `BaseExchange` no longer has `set_alert_service` or `update_alert_params`; has `set_volatility_alert` and `cancel_volatility_alert`.
7. ✅ `WizardResult` no longer has `alert_enabled / alert_window_min / alert_threshold_pct`.
8. ✅ `Session` model no longer has `alert_config`.
9. ✅ `AlertsConfig` does not exist in `src/config.py`.
10. ✅ A fresh sim session wizard run does not prompt for "Price alerts" and does not show an "Alerts" row in the summary.
