# Iter tool-opt-mark-vs-last Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `get_position` Liquidation distance off `ticker.last` onto mark price via a new `BaseExchange.get_mark_price()` abstract method; introduce a `BaseExchange.algo_trigger_reference: str = "last"` class attribute that drives the distance-label wording at five output sites — six emit points, OCO renders 2 — (`get_position` Exit Orders / `get_open_orders` single / `get_open_orders` OCO / `set_stop_loss` / `set_take_profit`); display a `Mark: X (Last: Y, drift ±Z%)` line in the Risk Exposure section; sync three trader-wrapper docstrings and校准 the iter6 demo-script docstring family.

**Architecture:** Mark fetch is the 6th member of `get_position`'s existing `asyncio.gather` 5-tuple, wrapped with a `_safe_mark_price` helper (parallel pattern to `_safe_ohlcv` at `tools_perception.py:259-264`). On mark fetch failure the helper returns `0.0`; the downstream `mark_price > 0` gate degrades the Mark line (omitted) and Liquidation line (fallback to "distance unavailable: mark fetch failed"). Exit Orders + Notional/Margin lines are anchored to `ticker.last` (matches OKX algo trigger reference per project default) and are unaffected by mark fetch failure. The five distance-label sites read `deps.exchange.algo_trigger_reference` at render time, ensuring single-source-of-truth for the trigger reference word.

**Tech Stack:** Python 3, pytest (existing), pydantic-ai (existing), CCXT 4.5.47 `okx` for the new mark-price endpoint call (`public_get_public_mark_price`), `asyncio.gather` for concurrent IO (already in get_position).

**Spec:** `docs/superpowers/specs/2026-05-14-iter-tool-opt-mark-vs-last-design.md`

**Branch:** `iter-tool-opt-mark-vs-last` (already created; spec commit `a6b57ba` is the first commit on the branch).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/integrations/exchange/base.py` | Modify | `BaseExchange` gains `algo_trigger_reference: str = "last"` class attribute + abstract `get_mark_price(symbol: str) -> float` method |
| `src/integrations/exchange/okx.py` | Modify | `OKXExchange.get_mark_price` impl using `public_get_public_mark_price` |
| `src/integrations/exchange/simulated.py` | Modify | `SimulatedExchange.get_mark_price` impl returning `await fetch_ticker(symbol).last` |
| `src/agent/tools_perception.py` | Modify | `_safe_mark_price` helper + 6-tuple gather + Mark line render + Liquidation recompute against mark + 2-decimal precision + Exit Orders label swap + `_render_single_order` `trigger_ref` parameter + OCO inline label swap |
| `src/agent/tools_execution.py` | Modify | `set_stop_loss` / `set_take_profit` success message label swap "from current" → "from last price" (via `algo_trigger_reference`) |
| `src/agent/trader.py` | Modify | 3 wrapper docstring sentence-edits at lines 133 / 156 / 159 + 1 added sentence on `get_position` for mark anchor |
| `scripts/iter6_task0_capture.py` | Modify | `_fetch_mark_price` docstring (line 203-216) + `_place_algo` docstring (line 222-223)校准 |
| `scripts/iter6_diag_ticker.py` | Modify | Module docstring item (c) at line 13 + drift formula at line 81 + print label at line 82 |
| `tests/test_iter_tool_opt_mark_vs_last.py` | Create | New test suite covering all 15 cases per spec §5.1 |
| `tests/test_tool_enhancement.py` | Modify (re-baseline) | ~10 `"from current"` → `"from last price"` substring updates + any Liquidation line precision adjustments |
| `tests/test_display_cycle.py` | Modify (re-baseline) | ~11 `"from current"` → `"from last price"` substring updates + Mark line presence + Liquidation precision adjustments |
| `tests/test_iter_tool_opt_error_metadata.py` | Modify (re-baseline) | ~4 `"from current"` → `"from last price"` substring updates (incl. OO-6 ticker-unavailable path) |

**Test pattern across new tests:** Mock `OKXExchange._client` (CCXT) and `SimulatedExchange._latest_ticker` via direct attribute set or MagicMock. Mark endpoint responses use full V5 envelope `{"code": "0", "msg": "", "data": [{"instId": "...", "instType": "SWAP", "markPx": "<value>", "ts": "<ms>"}]}` per `project_iter2_mock_fidelity_lesson`. Byte-equal for full lines with fixture-controlled values; substring for lines carrying variable IDs / amounts.

---

## Task 1: BaseExchange — `algo_trigger_reference` + abstract `get_mark_price`

**Files:**
- Modify: `src/integrations/exchange/base.py:97-105`
- Test: `tests/test_iter_tool_opt_mark_vs_last.py` (new file)

- [ ] **Step 1: Create `tests/test_iter_tool_opt_mark_vs_last.py` with first failing test**

```python
"""Iter tool-opt-mark-vs-last tests.

Spec: docs/superpowers/specs/2026-05-14-iter-tool-opt-mark-vs-last-design.md

Test pattern:
- OKX-side: mock `_client` (CCXT) with MagicMock; mark endpoint returns full V5
  envelope `{"code": "0", "msg": "", "data": [{"instId", "instType", "markPx",
  "ts"}]}` per project_iter2_mock_fidelity_lesson.
- Sim-side: direct attribute set on `_latest_ticker`.
- Byte-equal for full lines with fully fixture-controlled values; substring for
  lines carrying variable order IDs / amounts / contracts.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ============ Task 1: BaseExchange attribute + abstract method ============

def test_base_algo_trigger_reference_default_last():
    """Spec §3.1: BaseExchange.algo_trigger_reference is a class attribute
    defaulting to "last". OKXExchange and SimulatedExchange inherit unchanged.
    """
    from src.integrations.exchange.base import BaseExchange
    from src.integrations.exchange.okx import OKXExchange
    from src.integrations.exchange.simulated import SimulatedExchange

    assert BaseExchange.algo_trigger_reference == "last"
    assert OKXExchange.algo_trigger_reference == "last"
    assert SimulatedExchange.algo_trigger_reference == "last"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py::test_base_algo_trigger_reference_default_last -v`
Expected: FAIL with `AttributeError: type object 'BaseExchange' has no attribute 'algo_trigger_reference'`

- [ ] **Step 3: Add class attribute + abstract method to `src/integrations/exchange/base.py`**

In `class BaseExchange(ABC):` (line 97), after `__init__` and before `@abstractmethod async def fetch_ticker`, add:

```python
    algo_trigger_reference: str = "last"
    """Word used in distance-label rendering at the five sites listed in
    docs/superpowers/specs/2026-05-14-iter-tool-opt-mark-vs-last-design.md §3.1.
    OKX algo orders default trigger reference is last (project does not set
    triggerPxType). Override in subclasses for exchanges whose default differs
    (e.g., Bybit V5 — must be set explicitly; Hyperliquid — "mark" or "oracle").
    """
```

In the abstract method block, after the existing `@abstractmethod async def fetch_ticker(...)`, add:

```python
    @abstractmethod
    async def get_mark_price(self, symbol: str) -> float:
        """Fetch mark price for the symbol. Used by get_position for
        liquidation-distance calculation. Implementations should raise on
        endpoint failure / empty response (no silent fallback).
        """
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py::test_base_algo_trigger_reference_default_last -v`
Expected: PASS

- [ ] **Step 5: Verify import side-effect didn't break existing tests**

Run: `uv run pytest tests/ -x --timeout=30 -q 2>&1 | tail -20`
Expected: Either PASS or fail on the abstract-method check when instantiating concrete subclasses without `get_mark_price` (covered by Task 2-3).

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_iter_tool_opt_mark_vs_last.py
git commit -m "iter-tool-opt-mark-vs-last: add BaseExchange.algo_trigger_reference + abstract get_mark_price

Class attribute defaults to \"last\" — drives the trigger-reference word in
distance-label rendering at five output sites. Subclasses override for
exchanges whose default differs. Abstract method get_mark_price returns
mark price for liquidation-distance calculation."
```

---

## Task 2: SimulatedExchange.get_mark_price

**Files:**
- Modify: `src/integrations/exchange/simulated.py` (add method to class body)
- Test: `tests/test_iter_tool_opt_mark_vs_last.py` (append)

- [ ] **Step 1: Append failing test**

```python
# ============ Task 2: SimulatedExchange.get_mark_price ============

@pytest.mark.asyncio
async def test_sim_get_mark_price_returns_ticker_last():
    """Spec §3.1 SimulatedExchange row: get_mark_price returns the cached
    ticker.last. Sim has a single price source — mark = last. fetch_ticker is
    observation-only (no internal tick advance), so back-to-back invocation
    inside get_position's 6-tuple gather is safe.
    """
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker

    cfg = MagicMock(fee_rate=0.0005, precision={})
    ex = SimulatedExchange(config=cfg, db_engine=None, session_id="sid", symbol="BTC/USDT:USDT")
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_345.0, timestamp=1_715_040_000_000,
    )

    mark = await ex.get_mark_price("BTC/USDT:USDT")
    assert mark == 80_000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py::test_sim_get_mark_price_returns_ticker_last -v`
Expected: FAIL with `TypeError: Can't instantiate abstract class SimulatedExchange with abstract method get_mark_price` (since Task 1 added the abstract method but Sim hasn't implemented yet).

- [ ] **Step 3: Add `get_mark_price` to `SimulatedExchange`**

In `src/integrations/exchange/simulated.py`, immediately after `fetch_ticker` (around line 124), add:

```python
    async def get_mark_price(self, symbol: str) -> float:
        """Sim has a single price source — mark = last. Note: under the new
        get_position flow this is called inside a 6-tuple gather that already
        fetches ticker; fetch_ticker is observation-only (reads cached
        _latest_ticker) so back-to-back invocation is safe. If a future
        SimulatedExchange mutates state in fetch_ticker (e.g., synthetic tick
        advancement for replay scenarios), revisit and read self._latest_ticker
        directly.
        """
        self._validate_symbol(symbol)
        if self._latest_ticker is None:
            raise RuntimeError("No ticker data available yet")
        return self._latest_ticker.last
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py::test_sim_get_mark_price_returns_ticker_last -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_iter_tool_opt_mark_vs_last.py
git commit -m "iter-tool-opt-mark-vs-last: SimulatedExchange.get_mark_price

Returns cached ticker.last — Sim has a single price source. Observation-only
(no internal tick advance), safe for back-to-back invocation inside the
get_position 6-tuple gather."
```

---

## Task 3: OKXExchange.get_mark_price

**Files:**
- Modify: `src/integrations/exchange/okx.py` (add method after `fetch_ticker` at line 436)
- Test: `tests/test_iter_tool_opt_mark_vs_last.py` (append)

- [ ] **Step 1: Append three failing tests**

```python
# ============ Task 3: OKXExchange.get_mark_price ============

@pytest.mark.asyncio
async def test_okx_get_mark_price_fetches_endpoint():
    """Spec §3.1: OKXExchange.get_mark_price hits public_get_public_mark_price
    and parses markPx as float. Mock response uses full V5 envelope per
    project_iter2_mock_fidelity_lesson.
    """
    from src.integrations.exchange.okx import OKXExchange

    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.public_get_public_mark_price = AsyncMock(return_value={
        "code": "0", "msg": "",
        "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP",
                  "markPx": "81920.10", "ts": "1715040000000"}],
    })
    ex._client.market = MagicMock(return_value={"id": "BTC-USDT-SWAP"})

    mark = await ex.get_mark_price("BTC/USDT:USDT")
    assert mark == 81920.10
    assert isinstance(mark, float)


@pytest.mark.asyncio
async def test_okx_get_mark_price_raises_on_empty_data():
    """Spec §3.1: empty `data` array → RuntimeError (no silent fallback)."""
    from src.integrations.exchange.okx import OKXExchange

    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.public_get_public_mark_price = AsyncMock(return_value={
        "code": "0", "msg": "", "data": [],
    })
    ex._client.market = MagicMock(return_value={"id": "BTC-USDT-SWAP"})

    with pytest.raises(RuntimeError, match="mark price fetch returned empty"):
        await ex.get_mark_price("BTC/USDT:USDT")


@pytest.mark.asyncio
async def test_okx_get_mark_price_uses_inst_id_conversion():
    """Spec §3.1: instId is derived via self._client.market(symbol)["id"]
    (CCXT-unified symbol → OKX instId). For BTC/USDT:USDT this yields
    BTC-USDT-SWAP.
    """
    from src.integrations.exchange.okx import OKXExchange

    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.public_get_public_mark_price = AsyncMock(return_value={
        "code": "0", "msg": "",
        "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP",
                  "markPx": "81920.10", "ts": "1715040000000"}],
    })
    ex._client.market = MagicMock(return_value={"id": "BTC-USDT-SWAP"})

    await ex.get_mark_price("BTC/USDT:USDT")
    ex._client.public_get_public_mark_price.assert_awaited_once_with({
        "instType": "SWAP", "instId": "BTC-USDT-SWAP",
    })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py -k "okx_get_mark_price" -v`
Expected: 3 FAIL with `Can't instantiate abstract class OKXExchange` or `AttributeError: 'OKXExchange' object has no attribute 'get_mark_price'`.

- [ ] **Step 3: Add `get_mark_price` to `OKXExchange`**

In `src/integrations/exchange/okx.py`, immediately after `fetch_ticker` (around line 436), add:

```python
    @_retry()
    async def get_mark_price(self, symbol: str) -> float:
        """Fetch OKX mark price via public_get_public_mark_price endpoint.

        OKX uses mark price for perpetual liquidation calculation. Algo
        trigger reference defaults to last (project does not set
        triggerPxType), so callers wanting trigger-side distance should use
        fetch_ticker().last instead.
        """
        inst_id = self._client.market(symbol)["id"]
        raw = await self._client.public_get_public_mark_price({
            "instType": "SWAP", "instId": inst_id,
        })
        data = raw.get("data") or []
        if not data:
            raise RuntimeError(f"mark price fetch returned empty for {symbol}")
        return float(data[0]["markPx"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py -k "okx_get_mark_price" -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_iter_tool_opt_mark_vs_last.py
git commit -m "iter-tool-opt-mark-vs-last: OKXExchange.get_mark_price

Fetches mark via public_get_public_mark_price endpoint. instId derived via
self._client.market(symbol)['id']. Raises RuntimeError on empty data — no
silent fallback. Wrapped in existing @_retry() decorator for transient
network errors."
```

---

## Task 4: get_position — mark integration (helper + gather + Mark line + Liquidation recompute)

**Files:**
- Modify: `src/agent/tools_perception.py:259-348` (extend `_safe_*` helper section + gather + Risk Exposure section)
- Test: `tests/test_iter_tool_opt_mark_vs_last.py` (append)

This is the largest task — combines _safe_mark_price helper, gather extension, Mark line rendering, Liquidation distance recompute against mark, and 2-decimal precision upgrade. All four changes ship together because partial impl would emit dead variables or unused output.

- [ ] **Step 1: Append failing tests covering all four sub-changes**

```python
# ============ Task 4: get_position mark integration ============

@pytest.fixture
def mock_deps_for_position():
    """Build a minimal `deps` mock with all IO returning fixture values."""
    import pandas as pd
    from src.integrations.exchange.base import Ticker, Position, Balance

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.initial_balance = 10_000.0

    # Position: short with 0.5 contracts entry 80000, liq 51000
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=500.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10_500.0, free_usdt=8_000.0, used_usdt=2_500.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_contract_size = AsyncMock(return_value=0.01)
    deps.exchange.get_mark_price = AsyncMock(return_value=80_000.0)
    deps.exchange.algo_trigger_reference = "last"
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_048.0, bid=80_040.0, ask=80_056.0,
        high=82_000.0, low=79_000.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))
    # Empty OHLCV → no ATR suffix; cleaner assertions
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=pd.DataFrame())
    return deps


@pytest.mark.asyncio
async def test_get_position_mark_line_byte_equal(mock_deps_for_position):
    """Spec §3.1 POS-5 Mark line variant (i) happy path: byte-equal Mark line
    rendering with explicit drift formula (last - mark) / mark * 100.
    """
    from src.agent.tools_perception import get_position

    # Fixture math: mark=80000, last=80048 → drift = (80048-80000)/80000*100 = +0.06%
    out = await get_position(mock_deps_for_position)
    assert "Mark: 80000.00 (Last: 80048.00, drift +0.06%)" in out


@pytest.mark.asyncio
async def test_get_position_drift_positive_sign_demo_magnitude(mock_deps_for_position):
    """Spec §5.1: demo-magnitude fixture using memory `project_okx_demo_mark_vs_last_drift`
    values. Note: memory writes -1.67% under (mark-last)/last convention;
    spec §4.1 uses (last-mark)/mark which gives +1.7033% → rounded +1.70%.
    Same physical observation; sign flips AND magnitude shifts ~0.03pp because
    denominator changes from last to mark. Test docstring reproduces this note
    to prevent future contributors from "fixing" the convention discrepancy.
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Ticker

    mock_deps_for_position.exchange.get_mark_price = AsyncMock(return_value=76_680.30)
    mock_deps_for_position.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=77_986.30, bid=77_980.0, ask=77_990.0,
        high=78_500.0, low=77_000.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_position(mock_deps_for_position)
    assert "Mark: 76680.30 (Last: 77986.30, drift +1.70%)" in out


@pytest.mark.asyncio
async def test_get_position_drift_negative_sign(mock_deps_for_position):
    """Spec §5.1: synthetic negative-sign guard. mark > last → drift negative.
    No claim of matching demo direction.
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Ticker

    mock_deps_for_position.exchange.get_mark_price = AsyncMock(return_value=80_048.0)
    mock_deps_for_position.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_990.0, ask=80_010.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_position(mock_deps_for_position)
    assert "drift -0.06%" in out


@pytest.mark.asyncio
async def test_get_position_liquidation_distance_uses_mark(mock_deps_for_position):
    """Spec §5.1: distance anchor is mark, not last. Fixture: mark=80000,
    last=82000, liq=51000 → mark-anchored = (80000-51000)/80000 = 36.25%.
    Last-anchored would give (82000-51000)/82000 ≈ 37.80% — assertion
    verifies the mark-anchored value, so a regression to last-anchored fails.
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Ticker

    mock_deps_for_position.exchange.get_mark_price = AsyncMock(return_value=80_000.0)
    mock_deps_for_position.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=82_000.0, bid=81_990.0, ask=82_010.0,
        high=82_500.0, low=80_000.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_position(mock_deps_for_position)
    assert "Liquidation: 51000.00 (36.25% away)" in out


@pytest.mark.asyncio
async def test_get_position_mark_fetch_failure_isolated_to_liquidation(mock_deps_for_position):
    """Spec §5.1: mark fetch failure → Mark line omitted, Liquidation falls
    back to "(distance unavailable: mark fetch failed)", but Notional / Margin
    / Exit Orders all render normally (Exit Orders is anchored to ticker.last,
    independent of mark).
    """
    from src.agent.tools_perception import get_position

    mock_deps_for_position.exchange.get_mark_price = AsyncMock(
        side_effect=RuntimeError("mark price fetch returned empty for BTC/USDT:USDT"),
    )

    out = await get_position(mock_deps_for_position)
    # (a) Mark line omitted
    assert "Mark:" not in out
    # (b) Liquidation fallback
    assert "Liquidation: 51000.00 (distance unavailable: mark fetch failed)" in out
    # (c) Notional + Margin render normally
    assert "Notional value:" in out
    assert "Margin used:" in out
    # (d) Exit Orders section still present (empty in this fixture but rendered)
    assert "=== Exit Orders ===" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py -k "position" -v`
Expected: 5 FAIL — Mark line not emitted, Liquidation still uses last, etc.

- [ ] **Step 3: Add `_safe_mark_price` helper inside `get_position`**

In `src/agent/tools_perception.py`, after the existing `_safe_ohlcv` (around line 264), add:

```python
    async def _safe_mark_price():
        try:
            return await deps.exchange.get_mark_price(symbol)
        except Exception:
            logger.exception("get_position: mark price fetch failed")
            return 0.0
```

- [ ] **Step 4: Extend the gather to 6 members**

Replace the existing gather (line 307-314):

```python
    try:
        ticker, balance, ohlcv_df, open_orders, contract_size, mark_price = await asyncio.gather(
            deps.market_data.get_ticker(symbol),
            deps.exchange.fetch_balance(),
            _safe_ohlcv(),
            deps.exchange.fetch_open_orders(symbol),
            deps.exchange.get_contract_size(symbol),
            _safe_mark_price(),
            return_exceptions=False,
        )
    except Exception as e:
        logger.exception("get_position: one of ticker/balance/orders/contract_size failed")
        sections = _render_position_core()
        sections.append(f"=== Risk Exposure ===\n(unavailable: {e.__class__.__name__})")
        sections.append(f"=== Exit Orders ===\n(unavailable: {e.__class__.__name__})")
        return "\n\n".join(sections)
```

`_safe_mark_price` cannot raise (returns 0.0 on failure), so it stays inside the gather without affecting the outer try/except.

- [ ] **Step 5: Replace Liquidation block with Mark line + mark-anchored distance**

Replace the existing Liquidation block (line 342-348):

```python
    # Risk Exposure: Mark + Liquidation
    if mark_price > 0:
        if current_price > 0:
            drift_pct = (current_price - mark_price) / mark_price * 100
            risk_lines.append(
                f"Mark: {mark_price:.2f} (Last: {current_price:.2f}, drift {drift_pct:+.2f}%)"
            )
        else:
            risk_lines.append(f"Mark: {mark_price:.2f} (Last: unavailable)")

        if p.liquidation_price is not None:
            liq_dist_pct = abs(mark_price - p.liquidation_price) / mark_price * 100
            if atr_pct_1h is not None and atr_pct_1h > 0:
                atr_mult = liq_dist_pct / atr_pct_1h
                risk_lines.append(
                    f"Liquidation: {p.liquidation_price:.2f} ({liq_dist_pct:.2f}% away = {atr_mult:.1f}× ATR(1h))"
                )
            else:
                risk_lines.append(
                    f"Liquidation: {p.liquidation_price:.2f} ({liq_dist_pct:.2f}% away)"
                )
    else:
        # mark fetch failed → omit Mark line, Liquidation falls back without distance
        if p.liquidation_price is not None:
            risk_lines.append(
                f"Liquidation: {p.liquidation_price:.2f} (distance unavailable: mark fetch failed)"
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py -k "position" -v`
Expected: 5 PASS

- [ ] **Step 7: Run broader test suite to surface fixture re-baseline needs (deferred to Task 11)**

Run: `uv run pytest tests/test_tool_enhancement.py tests/test_display_cycle.py tests/test_iter_tool_opt_error_metadata.py -x --timeout=30 2>&1 | tail -30`
Expected: failures on existing tests due to new Mark line + `:.2f` precision; these are addressed in Task 11.

Do **not** fix the failing existing tests yet — the next tasks (5-7) introduce more label changes which all rebase together in Task 11.

- [ ] **Step 8: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_tool_opt_mark_vs_last.py
git commit -m "iter-tool-opt-mark-vs-last: get_position mark integration

Adds _safe_mark_price helper (parallel to _safe_ohlcv) as 6th gather member;
zero latency increment. Risk Exposure section gains Mark line showing mark /
last / drift with explicit (last - mark) / mark * 100 sign convention.
Liquidation distance recomputed against mark with 2-decimal precision; on
mark fetch failure (mark_price == 0.0) falls back to 'distance unavailable:
mark fetch failed' without silent anchor mix. Notional / Margin / Exit
Orders unaffected."
```

---

## Task 5: get_position — Exit Orders label swap

**Files:**
- Modify: `src/agent/tools_perception.py:369-378` (`_fmt_exit` function inside `get_position`)
- Test: `tests/test_iter_tool_opt_mark_vs_last.py` (append)

- [ ] **Step 1: Append failing test**

```python
# ============ Task 5: get_position Exit Orders label swap ============

@pytest.mark.asyncio
async def test_get_position_exit_orders_label_last_price(mock_deps_for_position):
    """Spec §3.1 POS-5 (Exit Orders): _fmt_exit swaps "current" → trigger_ref
    word (which is "last" for OKX default + Sim). Substring assertion because
    line contains variable order price / amount.
    """
    from src.agent.tools_perception import get_position
    from src.integrations.exchange.base import Order

    mock_deps_for_position.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="abc1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.5, price=78_000.0, status="open", is_algo=True,
              trigger_price=78_000.0),
        Order(id="abc2", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.5, price=82_000.0, status="open", is_algo=True,
              trigger_price=82_000.0),
    ])

    out = await get_position(mock_deps_for_position)
    # Substring guard: SL/TP exit lines mention "last price" not "current"
    assert "below last price" in out  # SL below current
    assert "above last price" in out  # TP above current
    assert "below current" not in out
    assert "above current" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py::test_get_position_exit_orders_label_last_price -v`
Expected: FAIL — output still says "below current" / "above current".

- [ ] **Step 3: Modify `_fmt_exit` to use `algo_trigger_reference`**

In `src/agent/tools_perception.py`, replace the `_fmt_exit` function (line 369-378) inside `get_position`:

```python
    trigger_ref = deps.exchange.algo_trigger_reference

    def _fmt_exit(o, kind: str) -> str:
        dist_entry_pct = (o.price - p.entry_price) / p.entry_price * 100
        dist_curr_pct = (o.price - current_price) / current_price * 100 if current_price > 0 else 0.0
        direction_entry = "above" if dist_entry_pct > 0 else "below"
        direction_curr = "above" if dist_curr_pct > 0 else "below"
        suffix = ""
        if atr_pct_1h is not None and atr_pct_1h > 0:
            atr_mult = abs(dist_curr_pct) / atr_pct_1h
            suffix = f" = {atr_mult:.1f}× ATR(1h)"
        return (
            f"  {kind}: {o.price:.2f} "
            f"({abs(dist_entry_pct):.1f}% {direction_entry} entry, "
            f"{abs(dist_curr_pct):.1f}% {direction_curr} {trigger_ref} price{suffix})  "
            f"[{o.amount} contracts]"
        )
```

The `trigger_ref` variable is read once before `_fmt_exit` is called.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py::test_get_position_exit_orders_label_last_price -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_tool_opt_mark_vs_last.py
git commit -m "iter-tool-opt-mark-vs-last: get_position Exit Orders label swap

_fmt_exit reads deps.exchange.algo_trigger_reference (single source of truth
for trigger-reference word; default 'last' for OKX). Distance anchor variable
(current_price = ticker.last) unchanged — matches OKX algo trigger default."
```

---

## Task 6: get_open_orders — `_render_single_order` + OCO inline label swap

**Files:**
- Modify: `src/agent/tools_perception.py:417-438` (`_render_single_order`) + `441-485` (`get_open_orders` body, OCO branch)
- Test: `tests/test_iter_tool_opt_mark_vs_last.py` (append)

- [ ] **Step 1: Append failing tests for both single + OCO paths**

```python
# ============ Task 6: get_open_orders single + OCO label swap ============

@pytest.mark.asyncio
async def test_get_open_orders_single_order_uses_last_price():
    """Spec §3.1 OO-7 non-OCO: _render_single_order takes a trigger_ref
    parameter; label uses "{trigger_ref} price" instead of "current".
    """
    from src.agent.tools_perception import get_open_orders
    from src.integrations.exchange.base import Order, Ticker

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange.algo_trigger_reference = "last"
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="ord-1", symbol="BTC/USDT:USDT", side="buy", order_type="limit",
              amount=0.5, price=79_000.0, status="open", is_algo=False,
              trigger_price=None),
    ])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_open_orders(deps)
    assert "from last price" in out
    assert "from current" not in out


@pytest.mark.asyncio
async def test_get_open_orders_oco_pair_uses_last_price():
    """Spec §3.1 OO-7 OCO: same-id stop + take_profit pair via inline render
    branch — both sl_dist and tp_dist suffixes use "{trigger_ref} price".
    """
    from src.agent.tools_perception import get_open_orders
    from src.integrations.exchange.base import Order, Ticker

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange.algo_trigger_reference = "last"
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="oco-1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.5, price=78_000.0, status="open", is_algo=True,
              trigger_price=78_000.0),
        Order(id="oco-1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.5, price=82_000.0, status="open", is_algo=True,
              trigger_price=82_000.0),
    ])
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await get_open_orders(deps)
    # Both legs use the trigger_ref label
    assert out.count("from last price") == 2
    assert "from current" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py -k "open_orders" -v`
Expected: 2 FAIL — outputs still say "from current".

- [ ] **Step 3: Modify `_render_single_order` to take `trigger_ref` parameter**

In `src/agent/tools_perception.py`, replace `_render_single_order` (line 417-438):

```python
def _render_single_order(o, current: float, trigger_ref: str) -> str:
    """Render a single (non-OCO) order line.

    `trigger_ref` is the exchange's algo trigger reference word (default
    "last" for OKX); used in the distance-label suffix.

    Preserves the current > 0 branch: no crash on abnormal ticker. Label /
    distance / ID suffix format matches the pre-iter-tool-opt-mark-vs-last
    rendering except for the trailing "{trigger_ref} price" swap.
    """
    if o.order_type == "market" or o.price is None:
        label = "[PENDING]" if o.order_type == "market" else f"[{o.order_type.upper()}]"
        price_str = "market price"
    else:
        if o.order_type == "limit":
            label = "[LIMIT]"
        else:
            label = f"[{o.order_type.upper()}]"
        if current > 0:
            dist = (o.price - current) / current * 100
            pts = o.price - current
            price_str = f"@ {o.price:.2f} ({dist:+.2f}% / {pts:+.1f} pts from {trigger_ref} price)"
        else:
            price_str = f"@ {o.price:.2f} (ticker unavailable, distance N/A)"
    return f"  {label} {o.side} {o.amount} {price_str} | ID: {o.id}"
```

- [ ] **Step 4: Modify `get_open_orders` body — OCO inline + single call site**

In `src/agent/tools_perception.py`, modify `get_open_orders` (around line 441-485). Add `trigger_ref` near line 450, update OCO inline f-strings, update `_render_single_order` call site:

```python
async def get_open_orders(deps: TradingDeps) -> str:
    """Get all pending orders with distance from last price."""
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return f"=== Pending Orders (@ {fetch_ts} UTC) ===\nNo pending orders."

    ticker = await deps.market_data.get_ticker(deps.symbol)
    current = ticker.last
    trigger_ref = deps.exchange.algo_trigger_reference

    # Group by id: OCO's two same-id legs share id + is_algo=True
    by_id: dict[str, list] = {}
    for o in orders:
        by_id.setdefault(o.id, []).append(o)

    lines = [f"=== Pending Orders (@ {fetch_ts} UTC) ==="]
    for order_id, group in by_id.items():
        is_oco = (
            len(group) == 2
            and {o.order_type for o in group} == {"stop", "take_profit"}
            and all(o.is_algo for o in group)
        )
        if is_oco:
            sl = next(o for o in group if o.order_type == "stop")
            tp = next(o for o in group if o.order_type == "take_profit")
            sl_dist = (
                f" ({(sl.price - current) / current * 100:+.2f}%"
                f" / {sl.price - current:+.1f} pts from {trigger_ref} price)"
                if current > 0 else " (ticker unavailable)"
            )
            tp_dist = (
                f" ({(tp.price - current) / current * 100:+.2f}%"
                f" / {tp.price - current:+.1f} pts from {trigger_ref} price)"
                if current > 0 else " (ticker unavailable)"
            )
            lines.append(
                f"  [OCO] {sl.side} {sl.amount} "
                f"stop {sl.price:.2f}{sl_dist} / tp {tp.price:.2f}{tp_dist} "
                f"| algoId: {order_id} (cancel removes both legs)"
            )
        else:
            for o in group:
                lines.append(_render_single_order(o, current, trigger_ref))
    return "\n".join(lines)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py -k "open_orders" -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_tool_opt_mark_vs_last.py
git commit -m "iter-tool-opt-mark-vs-last: get_open_orders single + OCO label swap

_render_single_order takes a required trigger_ref parameter (no default —
explicit per project style). OCO inline branch (sl_dist + tp_dist f-strings)
reads trigger_ref locally. Both paths render 'from {trigger_ref} price' —
OKX default 'last' under current setup; future Bybit/Hyperliquid wrappers
flip all sites via single attribute override."
```

---

## Task 7: set_stop_loss + set_take_profit — success message label swap

**Files:**
- Modify: `src/agent/tools_execution.py:165-169` (`set_stop_loss`) + `:195-199` (`set_take_profit`)
- Test: `tests/test_iter_tool_opt_mark_vs_last.py` (append)

- [ ] **Step 1: Append failing tests**

```python
# ============ Task 7: set_stop_loss + set_take_profit message swap ============

@pytest.mark.asyncio
async def test_set_stop_loss_message_uses_last_price():
    """Spec §3.1 SL-2: success message swaps 'from current' → 'from
    {trigger_ref} price' (default 'last').
    """
    from src.agent.tools_execution import set_stop_loss
    from src.integrations.exchange.base import Order, Position, Ticker

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange.algo_trigger_reference = "last"
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=500.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ])
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="sl-1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
        amount=0.5, price=78_000.0, status="open", is_algo=True,
    ))
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await set_stop_loss(deps, price=78_000.0, reasoning="below MA50")
    assert "from last price" in out
    assert "from current" not in out


@pytest.mark.asyncio
async def test_set_take_profit_message_uses_last_price():
    """Spec §3.1 TP-2: mirror of SL-2 for take_profit."""
    from src.agent.tools_execution import set_take_profit
    from src.integrations.exchange.base import Order, Position, Ticker

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange.algo_trigger_reference = "last"
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=500.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ])
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="tp-1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
        amount=0.5, price=82_000.0, status="open", is_algo=True,
    ))
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))

    out = await set_take_profit(deps, price=82_000.0, reasoning="resistance ceiling")
    assert "from last price" in out
    assert "from current" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py -k "set_stop_loss_message or set_take_profit_message" -v`
Expected: 2 FAIL — messages still say "from current".

- [ ] **Step 3: Modify `set_stop_loss` success message**

In `src/agent/tools_execution.py`, replace the success path of `set_stop_loss` (line 165-169):

```python
    ticker = await deps.market_data.get_ticker(deps.symbol)
    trigger_ref = deps.exchange.algo_trigger_reference
    if ticker.last > 0:
        dist_pct = (price - ticker.last) / ticker.last * 100
        return f"Stop loss set at {price:.2f} ({dist_pct:+.2f}% from {trigger_ref} price {ticker.last:.2f}) | Order: {order.id}"
    return f"Stop loss set at {price:.2f} | Order: {order.id}"
```

- [ ] **Step 4: Modify `set_take_profit` success message**

Replace the success path of `set_take_profit` (line 195-199):

```python
    ticker = await deps.market_data.get_ticker(deps.symbol)
    trigger_ref = deps.exchange.algo_trigger_reference
    if ticker.last > 0:
        dist_pct = (price - ticker.last) / ticker.last * 100
        return f"Take profit set at {price:.2f} ({dist_pct:+.2f}% from {trigger_ref} price {ticker.last:.2f}) | Order: {order.id}"
    return f"Take profit set at {price:.2f} | Order: {order.id}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py -k "set_stop_loss_message or set_take_profit_message" -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_execution.py tests/test_iter_tool_opt_mark_vs_last.py
git commit -m "iter-tool-opt-mark-vs-last: set_stop_loss + set_take_profit message swap

Success messages now read trigger_ref from deps.exchange.algo_trigger_reference
('last' for OKX default). Distance anchor (ticker.last) unchanged — matches
the OKX algo trigger reference."
```

---

## Task 8: algo_trigger_reference single-source-of-truth sentinel test

**Files:**
- Test: `tests/test_iter_tool_opt_mark_vs_last.py` (append; no source code changes)

This task adds the "sentinel" test that locks all five label sites to the `algo_trigger_reference` attribute. If a future contributor hardcodes `"last"` at any site, this test fails loudly.

- [ ] **Step 1: Append sentinel test**

```python
# ============ Task 8: algo_trigger_reference single-source-of-truth ============

@pytest.mark.asyncio
async def test_algo_trigger_reference_drives_label_text(monkeypatch):
    """Spec §5.1 sentinel: monkey-patch BaseExchange.algo_trigger_reference to
    "mark" and verify all FIVE label sites emit "from mark price" — confirms
    single-source-of-truth wiring. Failure of this test indicates a future
    contributor has hardcoded "last" at one or more sites.

    Sites under test (5 sites, 6 emit points — OCO renders 2):
      (a) get_position Exit Orders _fmt_exit
      (b) get_open_orders _render_single_order (non-OCO)
      (c) get_open_orders OCO inline branch (sl_dist + tp_dist = 2 emits)
      (d) set_stop_loss success message
      (e) set_take_profit success message
    """
    from src.integrations.exchange.base import BaseExchange, Order, Position, Ticker
    from src.agent.tools_perception import get_position, get_open_orders
    from src.agent.tools_execution import set_stop_loss, set_take_profit
    import pandas as pd

    monkeypatch.setattr(BaseExchange, "algo_trigger_reference", "mark")

    # Common deps fixture
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.initial_balance = 10_000.0
    deps.exchange.algo_trigger_reference = "mark"
    deps.exchange.fetch_balance = AsyncMock(return_value=MagicMock(
        total_usdt=10_500.0, free_usdt=8_000.0, used_usdt=2_500.0,
    ))
    deps.exchange.get_contract_size = AsyncMock(return_value=0.01)
    deps.exchange.get_mark_price = AsyncMock(return_value=80_000.0)
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=80_000.0, bid=79_995.0, ask=80_005.0,
        high=82_000.0, low=79_500.0, base_volume=12_000.0, timestamp=1_715_040_000_000,
    ))
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=pd.DataFrame())

    # (a) get_position Exit Orders + (b/c covered separately)
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.5,
                 entry_price=80_000.0, unrealized_pnl=0.0, leverage=10,
                 liquidation_price=51_000.0, created_at=None),
    ])
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="sl-1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.5, price=78_000.0, status="open", is_algo=True,
              trigger_price=78_000.0),
    ])
    out_pos = await get_position(deps)
    assert "below mark price" in out_pos or "above mark price" in out_pos

    # (b) get_open_orders non-OCO
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="ord-1", symbol="BTC/USDT:USDT", side="buy", order_type="limit",
              amount=0.5, price=79_000.0, status="open", is_algo=False,
              trigger_price=None),
    ])
    out_oo = await get_open_orders(deps)
    assert "from mark price" in out_oo

    # (c) get_open_orders OCO
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(id="oco-1", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
              amount=0.5, price=78_000.0, status="open", is_algo=True,
              trigger_price=78_000.0),
        Order(id="oco-1", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
              amount=0.5, price=82_000.0, status="open", is_algo=True,
              trigger_price=82_000.0),
    ])
    out_oco = await get_open_orders(deps)
    assert out_oco.count("from mark price") == 2

    # (d) set_stop_loss
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="sl-2", symbol="BTC/USDT:USDT", side="sell", order_type="stop",
        amount=0.5, price=78_000.0, status="open", is_algo=True,
    ))
    out_sl = await set_stop_loss(deps, price=78_000.0, reasoning="x")
    assert "from mark price" in out_sl

    # (e) set_take_profit
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="tp-2", symbol="BTC/USDT:USDT", side="sell", order_type="take_profit",
        amount=0.5, price=82_000.0, status="open", is_algo=True,
    ))
    out_tp = await set_take_profit(deps, price=82_000.0, reasoning="x")
    assert "from mark price" in out_tp
```

- [ ] **Step 2: Run test to verify it passes**

Tasks 5-7 already wired all sites via `deps.exchange.algo_trigger_reference`, so this test should pass without source changes.

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py::test_algo_trigger_reference_drives_label_text -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_iter_tool_opt_mark_vs_last.py
git commit -m "iter-tool-opt-mark-vs-last: sentinel test for single-source-of-truth wiring

Monkey-patches BaseExchange.algo_trigger_reference to 'mark' and verifies all
five label-rendering call sites emit 'from mark price'. Failure of this test
indicates a future contributor has hardcoded 'last' at one or more sites —
the abstraction's primary safeguard."
```

---

## Task 9: trader.py wrapper docstring sync (3 sentence edits + 1 added sentence)

**Files:**
- Modify: `src/agent/trader.py:129-138` (get_position) + `:154-161` (get_open_orders)

No TDD — pure docstring edit, no behavior change. Verification via grep.

- [ ] **Step 1: Modify `get_position` wrapper docstring (line 129-138)**

Replace:

```python
    @tool
    async def get_position(ctx: RunContext[TradingDeps], symbol: str | None = None) -> str:
        """Get current position details with risk exposure context.

        Includes Risk exposure (notional / margin / liquidation distance in
        ATR(1h) multiples — 1h is the fixed baseline regardless of session
        trading style) and Exit orders section (SL/TP distances from both
        entry and current).

        Args:
            symbol: trading symbol (defaults to session symbol).
        """
```

With:

```python
    @tool
    async def get_position(ctx: RunContext[TradingDeps], symbol: str | None = None) -> str:
        """Get current position details with risk exposure context.

        Includes Risk exposure (notional / margin / mark price / liquidation
        distance in ATR(1h) multiples — 1h is the fixed baseline regardless of
        session trading style) and Exit orders section (SL/TP distances from
        both entry and last price). Liquidation distance is computed against
        mark price.

        Args:
            symbol: trading symbol (defaults to session symbol).
        """
```

- [ ] **Step 2: Modify `get_open_orders` wrapper docstring (line 154-161)**

Replace:

```python
    @tool
    async def get_open_orders(ctx: RunContext[TradingDeps]) -> str:
        """Get all pending orders with distance from current price.

        Lists limit orders, stop loss, and take profit orders, each with their
        price level and distance from current. OCO-paired orders (sharing an
        algoId on OKX) render with `[OCO]` tag.
        """
```

With:

```python
    @tool
    async def get_open_orders(ctx: RunContext[TradingDeps]) -> str:
        """Get all pending orders with distance from last price.

        Lists limit orders, stop loss, and take profit orders, each with their
        price level and distance from last price. OCO-paired orders (sharing
        an algoId on OKX) render with `[OCO]` tag.
        """
```

- [ ] **Step 3: Verify with grep — no remaining "from current" / "and current"**

Run: `grep -n "from current\|and current" /Users/z/Z/TradeBot/src/agent/trader.py`
Expected: only line 399 (`(authoritative current price)` from `get_multi_tf_snapshot` docstring, unrelated) and line 479 / 497 (`Set X on the current position` — semantic "current position", not anchor word).

- [ ] **Step 4: Verify test suite still passes for these tools**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py -v -q`
Expected: all PASS (no behavior change in docstring)

- [ ] **Step 5: Commit**

```bash
git add src/agent/trader.py
git commit -m "iter-tool-opt-mark-vs-last: sync wrapper docstrings (trader.py)

3 in-place 'current' → 'last price' edits at lines 133 / 156 / 159 + 1 added
sentence on get_position for mark anchor disclosure. set_stop_loss /
set_take_profit docstrings have no 'current' wording so unchanged. Build-time
docstring describes current OKX-specific concrete reference; runtime output
text is algo_trigger_reference-driven."
```

---

## Task 10: iter6 script family校准 (4 logical sub-changes / 5 edit actions)

**Files:**
- Modify: `scripts/iter6_task0_capture.py:203-216` (sub-a) + `:222-223` (sub-b)
- Modify: `scripts/iter6_diag_ticker.py:13` (sub-c) + `:81` (sub-d1) + `:82` (sub-d2)

No TDD — script-only docstrings + diagnostic formula. Verification via grep.

- [ ] **Step 1: Replace `iter6_task0_capture.py:203-216` `_fetch_mark_price` docstring (sub-a)**

Replace:

```python
async def _fetch_mark_price(ex: OKXExchange) -> float:
    """Fetch OKX mark price via raw public endpoint.

    Demo ticker.last drifts up to 1.67% from mark price (verified via
    iter6_diag_ticker.py); OKX algo trigger validation uses mark price
    despite the 51280 error message saying "last price". Always compute
    triggers from mark price.
    """
```

With:

```python
async def _fetch_mark_price(ex: OKXExchange) -> float:
    """Fetch OKX mark price via raw public endpoint.

    OKX algo trigger validation uses last price (V5 docs + CCXT 4.5.47
    verified per memory project_okx_demo_mark_vs_last_drift, 校准 2026-04-28).
    Demo workaround: mark is 1.67% below last in demo env, so triggers
    computed from mark sit well below OKX's last-reference comparison and
    reliably bypass 51280 errors. In production (drift typically <0.05%),
    this workaround offers negligible buffer over last-anchored triggers.
    """
```

- [ ] **Step 2: Replace `iter6_task0_capture.py:222-223` `_place_algo` docstring (sub-b)**

Replace:

```python
    """Place algo order with single attempt at given buffer percentage.

    Trigger computed from mark price (NOT ticker.last) because OKX algo
    validation uses mark price internally. Buffer 0.6% chosen empirically.
    """
```

With:

```python
    """Place algo order with single attempt at given buffer percentage.

    Trigger computed from mark price (NOT ticker.last) as a demo workaround —
    see _fetch_mark_price docstring above for the 校准 rationale. Buffer 0.6%
    chosen empirically.
    """
```

- [ ] **Step 3: Replace `iter6_diag_ticker.py:13` module docstring item (c) (sub-c)**

In the module docstring at the top of the file, replace:

```
  (c) trigger validation uses mark price not last price
```

With:

```
  (c) trigger validation uses last price; demo mark/last drift offers buffer (see project memory okx-demo-mark-vs-last-drift)
```

- [ ] **Step 4: Replace `iter6_diag_ticker.py:81-82` drift formula + label (sub-d1 + d2)**

Replace:

```python
                    diff_pct = (float(mp.get('markPx', 0)) - ticker1.last) / ticker1.last * 100
                    print(f"  mark vs ticker.last drift: {diff_pct:+.4f}%")
```

With:

```python
                    diff_pct = (ticker1.last - float(mp.get('markPx', 0))) / float(mp.get('markPx', 0)) * 100
                    print(f"  last vs mark drift (last - mark / mark): {diff_pct:+.4f}%")
```

- [ ] **Step 5: Verify with grep — no remaining stale claims**

Run: `grep -nE "trigger validation uses mark|OKX algo validation uses mark|mark vs ticker\.last drift|markPx', 0\)\) - ticker1\.last" /Users/z/Z/TradeBot/scripts/iter6_task0_capture.py /Users/z/Z/TradeBot/scripts/iter6_diag_ticker.py`
Expected: zero matches.

- [ ] **Step 6: Commit**

```bash
git add scripts/iter6_task0_capture.py scripts/iter6_diag_ticker.py
git commit -m "iter-tool-opt-mark-vs-last: iter6 script family 校准

4 logical sub-changes / 5 edit actions, all tracing to memory校准 2026-04-28
(project_okx_demo_mark_vs_last_drift):
  (a) iter6_task0_capture.py:203-216 _fetch_mark_price docstring
  (b) iter6_task0_capture.py:222-223 _place_algo docstring
  (c) iter6_diag_ticker.py:13 module docstring item (c)
  (d1) iter6_diag_ticker.py:81 drift formula → (last - mark) / mark * 100
  (d2) iter6_diag_ticker.py:82 print label parity

Spec convention swap shifts physical magnitude ~0.03pp on top of sign flip
(denominator change from last to mark) — cached output snapshots referencing
the value need re-baselining if any exist."
```

---

## Task 11: Re-baseline existing test fixtures (~25 cases across 3 files)

**Files:**
- Modify: `tests/test_tool_enhancement.py` (~10 cases)
- Modify: `tests/test_display_cycle.py` (~11 cases)
- Modify: `tests/test_iter_tool_opt_error_metadata.py` (~4 cases)

- [ ] **Step 1: Surface all failing tests**

Run: `uv run pytest tests/test_tool_enhancement.py tests/test_display_cycle.py tests/test_iter_tool_opt_error_metadata.py --tb=line --timeout=30 2>&1 | grep -E "FAILED|PASSED" | sort | uniq -c | sort -rn`

Expected: ~25 FAILED entries across the three files. The failures come from:
- `"from current"` → `"from last price"` substring mismatch
- New Mark line inserted into Risk Exposure section (where Position-view goldens assert content order)
- Liquidation distance precision `{:.1f}%` → `{:.2f}%`
- Liquidation distance recomputed against mark (different numeric value when fixture has mark ≠ last)

Save the failure list to a scratch file for tracking:

```bash
uv run pytest tests/test_tool_enhancement.py tests/test_display_cycle.py tests/test_iter_tool_opt_error_metadata.py --tb=no --timeout=30 2>&1 | grep "FAILED" > /tmp/mark-vs-last-failures.txt
wc -l /tmp/mark-vs-last-failures.txt
```

- [ ] **Step 2: Fix `tests/test_tool_enhancement.py` — substring updates**

For each failing test in `tests/test_tool_enhancement.py`:
- Replace expected `"from current"` strings → `"from last price"` (preserves surrounding numeric values)
- If the test asserts byte-equal Liquidation line: update precision `{:.1f}` → `{:.2f}` AND replace last-anchored numeric expectation with mark-anchored (if fixture has mark ≠ last; for fixtures where mark == last, only the precision changes)
- If the test asserts the Risk Exposure section structurally (e.g., line-count or "Notional value:" before "Margin used:"): insert Mark line between "Margin used:" and "Liquidation:" if relevant

Run: `uv run pytest tests/test_tool_enhancement.py --tb=line --timeout=30 -q 2>&1 | tail -10`
Expected: all PASS for this file.

- [ ] **Step 3: Commit**

```bash
git add tests/test_tool_enhancement.py
git commit -m "iter-tool-opt-mark-vs-last: re-baseline test_tool_enhancement fixtures

~10 fixture updates: 'from current' → 'from last price' substring swaps +
Liquidation precision {:.1f} → {:.2f} + mark-anchored distance values where
fixtures distinguish mark from last + Mark line insertion in Risk Exposure
section goldens."
```

- [ ] **Step 4: Fix `tests/test_display_cycle.py` — same pattern**

Same procedure as Step 2, applied to `tests/test_display_cycle.py`.

Run: `uv run pytest tests/test_display_cycle.py --tb=line --timeout=30 -q 2>&1 | tail -10`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_display_cycle.py
git commit -m "iter-tool-opt-mark-vs-last: re-baseline test_display_cycle fixtures

~11 fixture updates following the same pattern as test_tool_enhancement
(substring swap + precision upgrade + Mark line insertion + mark-anchored
distance values)."
```

- [ ] **Step 6: Fix `tests/test_iter_tool_opt_error_metadata.py` — same pattern**

Same procedure. Note: this file includes OO-6 ticker-unavailable path tests — verify those still pass (in the fallback branch, `_render_single_order` returns `"@ {price} (ticker unavailable, distance N/A)"` which has no "current" / "last price" wording, so should be untouched).

Run: `uv run pytest tests/test_iter_tool_opt_error_metadata.py --tb=line --timeout=30 -q 2>&1 | tail -10`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/test_iter_tool_opt_error_metadata.py
git commit -m "iter-tool-opt-mark-vs-last: re-baseline test_iter_tool_opt_error_metadata fixtures

~4 fixture updates. OO-6 ticker-unavailable fallback path unchanged (fallback
string carries no 'current' / 'last price' wording — distance simply 'N/A')."
```

---

## Task 12: Full-suite verification + final cleanup

**Files:**
- None modified — verification only

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ --timeout=60 -q 2>&1 | tail -15`
Expected: all PASS. The pre-existing test count was 1660 (per memory `project_tradebot_status`); after this iter it should be 1675 (1660 + 15 new tests in `test_iter_tool_opt_mark_vs_last.py`).

If any failures surface:
- If it's a test asserting `"from current"` in a file not listed in Task 11: re-baseline that file analogously (substring swap, precision upgrade, mark-line/distance value updates as appropriate)
- If it's a tool-count drift (`REGISTERED_TOOL_NAMES`): verify still equals 34 per spec §3.2 — should not have changed since no tool was added or removed

- [ ] **Step 2: Verify no banned phrases / stale claims remain in source**

Run:
```bash
grep -nE "from current\b|OKX algo trigger validation uses mark|OKX algo validation uses mark|trigger validation uses mark" \
  /Users/z/Z/TradeBot/src/ /Users/z/Z/TradeBot/scripts/iter6_task0_capture.py /Users/z/Z/TradeBot/scripts/iter6_diag_ticker.py -r
```

Expected: zero matches. (The phrase `"current position"` in `set_stop_loss` / `set_take_profit` docstrings refers to position state, not the anchor word — unaffected.)

- [ ] **Step 3: Verify the five label sites all flow through `algo_trigger_reference`**

Run:
```bash
grep -nE "from last price|from \{trigger_ref\} price|from \{deps\.exchange\.algo_trigger_reference\} price" \
  /Users/z/Z/TradeBot/src/agent/tools_perception.py /Users/z/Z/TradeBot/src/agent/tools_execution.py
```

Expected: ≥4 matches (one per site: `_fmt_exit` Exit Orders, `_render_single_order` non-OCO, OCO inline sl_dist + tp_dist, `set_stop_loss`, `set_take_profit`).

- [ ] **Step 4: Run the sentinel test in isolation to confirm**

Run: `uv run pytest tests/test_iter_tool_opt_mark_vs_last.py::test_algo_trigger_reference_drives_label_text -v`
Expected: PASS.

- [ ] **Step 5: Final test-count + commit-list sanity**

Run:
```bash
git log --oneline iter-tool-opt-mark-vs-last ^main | head -20
```

Expected commit chain (top is HEAD):
1. (Task 11) re-baseline test_iter_tool_opt_error_metadata
2. (Task 11) re-baseline test_display_cycle
3. (Task 11) re-baseline test_tool_enhancement
4. (Task 10) iter6 script family校准
5. (Task 9) sync wrapper docstrings (trader.py)
6. (Task 8) sentinel test for single-source-of-truth wiring
7. (Task 7) set_stop_loss + set_take_profit message swap
8. (Task 6) get_open_orders single + OCO label swap
9. (Task 5) get_position Exit Orders label swap
10. (Task 4) get_position mark integration
11. (Task 3) OKXExchange.get_mark_price
12. (Task 2) SimulatedExchange.get_mark_price
13. (Task 1) BaseExchange attribute + abstract method
14. (Spec) design spec (4 review rounds resolved) — `a6b57ba`

13 impl commits + 1 spec commit = 14 commits total on the branch.

- [ ] **Step 6: No final commit needed — Task 12 is verification only**

If all checks pass, the iter is ready for code review via `superpowers:requesting-code-review` skill or `gh pr create` for the PR workflow.

---

## Out-of-scope (per spec §3.3 + §9)

Do **not** add these to this iter. Each has its own trigger condition in spec §9:

- F1: setting `triggerPxType=mark` on project's algo order submission path (write-path change)
- F2: index price as a third reference
- F3: LIMIT order distance anchor to bid/ask instead of last
- F4: drift / funding-rate cross-reference disclosure
- F5: multi-exchange `algo_trigger_reference` override fixtures (no Binance/Bybit integration yet)
- F6: mark price caching infrastructure
- F7: ATR-multiple anchor reconciliation
