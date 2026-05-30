# Order-flow Tools Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `get_taker_flow` (rubik minute-level taker buy/sell flow) and refactor `get_recent_trades` from time-buckets into fixed count-buckets (plus a 张→base amount-unit fix), giving the agent two complementary order-flow views — seconds-level micro (A-class entry timing) and minute-level trend (B-class flow).

**Architecture:** New `TakerFlowBar` dataclass + `fetch_taker_flow` across `BaseExchange`/`Simulated`/`OKX` → uncached `MarketDataService.get_taker_flow` → `tools_perception.get_taker_flow` (a pure `_render_taker_flow` helper that takes an injected `now_ms`, plus OHLCV-join and a context-anchor up-tier fetch) → `trader.py` `@tool` wrapper registered in `REGISTERED_TOOL_NAMES`. For `get_recent_trades`: the 张→base unit fix lives in the `fetch_trades` adapter (reads real market `contractSize`, decoupled from the load-bearing execution-layer `get_contract_size`); the tool itself is rewritten to render fixed 5×100 count-buckets.

**Tech Stack:** Python 3.11+, pydantic-ai `@tool`, ccxt(pro) OKX rubik endpoints (`public_get_rubik_stat_taker_volume_contract`), pandas (OHLCV join), pytest + pytest-asyncio, `unittest.mock` (AsyncMock for async endpoints, MagicMock for the synchronous ccxt `.market()`).

**Spec:** `docs/superpowers/specs/2026-05-30-order-flow-tools-redesign-design.md` (commit `c1f16ab`).

---

## File Structure

**Group A — `get_taker_flow` (new tool):**

- `src/integrations/exchange/base.py` — add `TakerFlowBar` dataclass, `_TAKER_VOLUME_PERIOD` map (5 entries incl `1w` anchor), and `fetch_taker_flow` `@abstractmethod`.
- `src/integrations/exchange/simulated.py` — `fetch_taker_flow` impl (real `_ccxt` rubik call).
- `src/integrations/exchange/okx.py` — `fetch_taker_flow` impl (`@_retry()`, mirrors OI method).
- `src/integrations/market_data.py` — `get_taker_flow` passthrough (NOT cached).
- `src/agent/tools_perception.py` — `_TAKER_FLOW_PERIOD_MS` / `_TAKER_FLOW_ANCHOR` constants, `_pick_usd_scale` / `_fmt_scaled` / `_fmt_hhmm` helpers, pure `_render_taker_flow`, async `get_taker_flow` tool.
- `src/agent/trader.py` — `get_taker_flow` `@tool` wrapper + add to `REGISTERED_TOOL_NAMES` (perception 19→20).
- Tests: `tests/test_taker_flow.py` (new), edits to the 11 complete `BaseExchange` stubs in `tests/test_exchange.py` / `tests/test_price_level_alert.py` / `tests/test_tool_enhancement.py`, and `tests/test_trader_agent.py:82` count assertion.

**Group B — `get_recent_trades` refactor:**

- `src/integrations/exchange/simulated.py` / `okx.py` — `fetch_trades` 张→base normalization.
- `src/agent/tools_perception.py` — new count-bucket constants + `_fmt_money` helper + rewritten `get_recent_trades`.
- `src/agent/trader.py` — `get_recent_trades` wrapper (drop `window_seconds`, new docstring).
- Tests: `tests/test_recent_trades_buckets.py` (new), rewrite of `tests/test_toolkit_iter2.py` recent-trades tests, plus a sweep of remaining `get_recent_trades` references.

**Decomposition rationale:** `fetch_taker_flow` is implemented in `Simulated`/`OKX` as a concrete method FIRST (Tasks A2/A3), then promoted to `@abstractmethod` in `base.py` together with the 11 stub edits in a single commit (Task A4). This keeps every intermediate commit green — the abstract promotion never lands before its implementations exist. The pure `_render_taker_flow` (Task A6) takes an injected `now_ms` so all formatting/math is deterministic and unit-testable without mocking the clock; the async tool (Task A7) only owns fetch orchestration + partial-failure degradation.

---

## Group A: `get_taker_flow`

### Task A1: `TakerFlowBar` model + `_TAKER_VOLUME_PERIOD` map

**Files:**
- Modify: `src/integrations/exchange/base.py` (model near `OpenInterestHistoryPoint:383`; map near `_OKX_OI_PERIOD:18`)
- Test: `tests/test_taker_flow.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_taker_flow.py`:

```python
"""Tests for get_taker_flow: rubik taker-volume fetch + minute-level flow rendering.

Covers spec docs/superpowers/specs/2026-05-30-order-flow-tools-redesign-design.md
§2 (rubik source), §3.1-3.3 (taker_flow design), §3.5 (errors), §4.1 (architecture),
§5 ①②③⑤⑥ (tests).
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_taker_flow_bar_dataclass_fields():
    from src.integrations.exchange.base import TakerFlowBar
    b = TakerFlowBar(ts=1778644800000, sell_usd=5_800_000.0, buy_usd=4_200_000.0)
    assert b.ts == 1778644800000
    assert b.sell_usd == pytest.approx(5_800_000.0)
    assert b.buy_usd == pytest.approx(4_200_000.0)


def test_taker_volume_period_map_is_complete():
    """§3.1/§3.3/③: distinct from _OKX_OI_PERIOD; covers tool periods {5m,1h,4h,1d}
    PLUS the 1w anchor up-tier. Reusing _OKX_OI_PERIOD would KeyError on 4h/1w."""
    from src.integrations.exchange.base import _TAKER_VOLUME_PERIOD, _OKX_OI_PERIOD
    assert _TAKER_VOLUME_PERIOD == {"5m": "5m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    assert _TAKER_VOLUME_PERIOD is not _OKX_OI_PERIOD
    for p in ("5m", "1h", "4h", "1d", "1w"):
        assert p in _TAKER_VOLUME_PERIOD
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_taker_flow.py::test_taker_flow_bar_dataclass_fields tests/test_taker_flow.py::test_taker_volume_period_map_is_complete -v`
Expected: FAIL with `ImportError: cannot import name 'TakerFlowBar'` / `'_TAKER_VOLUME_PERIOD'`.

- [ ] **Step 3: Add the map next to `_OKX_OI_PERIOD`**

In `src/integrations/exchange/base.py`, immediately after the `_OKX_OI_PERIOD = {...}` line (currently line 18):

```python
# taker-volume rubik endpoint period map. DELIBERATELY distinct from
# _OKX_OI_PERIOD: the legal period set differs (taker flow exposes 4h + 1w; OI
# does not), so reusing _OKX_OI_PERIOD would KeyError on 4h/1w. 1w is included
# only as the 1d-period anchor up-tier (§3.3), not as a standalone tool period.
_TAKER_VOLUME_PERIOD = {"5m": "5m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
```

- [ ] **Step 4: Add the `TakerFlowBar` dataclass**

In `src/integrations/exchange/base.py`, after the `OpenInterestHistoryPoint` dataclass (ends line 393), add:

```python
@dataclass
class TakerFlowBar:
    """One taker-volume bucket from OKX rubik taker-volume-contract (unit=2, USD).

    `ts` is the bucket OPEN time (ms); intervals equal the requested period. The
    newest bar returned by the endpoint is the in-progress CURRENT bucket — the
    fetch layer returns it raw (no detection, no formed% — this dataclass carries
    no formed field); the tool layer detects in-progress via
    `ts + period_ms > now_ms` and labels formed% (§3.2/§4.1).
    """
    ts: int
    sell_usd: float
    buy_usd: float
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_taker_flow.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_taker_flow.py
git commit -m "feat(taker-flow): add TakerFlowBar model + _TAKER_VOLUME_PERIOD map"
```

---

### Task A2: `SimulatedExchange.fetch_taker_flow`

**Files:**
- Modify: `src/integrations/exchange/simulated.py` (base import block 14-29; new method after `fetch_open_interest_history` ends at 1063)
- Test: `tests/test_taker_flow.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_taker_flow.py`:

```python
def _sim_with_rubik(data_rows):
    """SimulatedExchange with mocked _ccxt rubik response. `.market` is SYNC
    (ccxt market() is synchronous) → MagicMock; the rubik endpoint is async."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._ccxt.public_get_rubik_stat_taker_volume_contract = AsyncMock(
        return_value={"code": "0", "data": data_rows, "msg": ""}
    )
    ex._validate_symbol = lambda s: None  # bypass symbol guard for unit isolation
    return ex


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_parses_and_ascends():
    # Raw OKX rubik is newest-first: [ts, sellVol, buyVol] (col1=sell, col2=buy).
    # Newest row (in-progress current bucket) must survive AND end up LAST after
    # the ascending sort (no drop/shift at fetch layer).
    rows = [
        ["1778644800000", "5800000", "4200000"],  # newest = in-progress
        ["1778644500000", "9000000", "8000000"],
        ["1778644200000", "1000000", "9000000"],  # oldest
    ]
    ex = _sim_with_rubik(rows)
    bars = await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 3)
    assert len(bars) == 3
    assert bars[0].ts == 1778644200000          # oldest first
    assert bars[-1].ts == 1778644800000         # in-progress newest kept, last
    # Column order [ts, sell, buy] (regression guard against direction flip):
    assert bars[-1].sell_usd == pytest.approx(5800000.0)
    assert bars[-1].buy_usd == pytest.approx(4200000.0)


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_passes_unit_period_instid_limit():
    ex = _sim_with_rubik([["1778644800000", "1", "2"]])
    await ex.fetch_taker_flow("BTC/USDT:USDT", "4h", 21)
    ex._ccxt.public_get_rubik_stat_taker_volume_contract.assert_awaited_once_with(
        {"instId": "BTC-USDT-SWAP", "period": "4H", "unit": "2", "limit": "21"}
    )


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_empty():
    ex = _sim_with_rubik([])
    assert await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 6) == []


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_rate_limit_raises():
    import ccxt.async_support as ccxt
    from src.utils.cache import RateLimitHit
    ex = _sim_with_rubik([])
    ex._ccxt.public_get_rubik_stat_taker_volume_contract = AsyncMock(
        side_effect=ccxt.RateLimitExceeded("429")
    )
    with pytest.raises(RateLimitHit):
        await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 6)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_taker_flow.py -k sim_fetch_taker_flow -v`
Expected: FAIL with `AttributeError: 'SimulatedExchange' object has no attribute 'fetch_taker_flow'`.

- [ ] **Step 3: Extend the base import in `simulated.py`**

In `src/integrations/exchange/simulated.py`, add `TakerFlowBar` and `_TAKER_VOLUME_PERIOD` to the existing `from src.integrations.exchange.base import (...)` block (lines 14-29) — insert alphabetically/with the existing constant:

```python
    Ticker,
    Trade,
    TakerFlowBar,
    _OKX_OI_PERIOD,
    _TAKER_VOLUME_PERIOD,
)
```

- [ ] **Step 4: Implement `fetch_taker_flow`**

In `src/integrations/exchange/simulated.py`, immediately after `fetch_open_interest_history` (ends line 1063, before `fetch_long_short_ratio` at 1065):

```python
    async def fetch_taker_flow(
        self,
        symbol: str,
        period: Literal["5m", "1h", "4h", "1d", "1w"] = "5m",
        limit: int = 6,
    ) -> list[TakerFlowBar]:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            raw = await self._ccxt.public_get_rubik_stat_taker_volume_contract({
                "instId": self._ccxt.market(symbol)["id"],
                "period": _TAKER_VOLUME_PERIOD[period],
                "unit": "2",  # USD notional — unit-clear, cross-symbol comparable
                "limit": str(limit),
            })
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim taker flow: {e}") from e
        rows = raw.get("data") or []
        bars = [
            TakerFlowBar(ts=int(r[0]), sell_usd=float(r[1]), buy_usd=float(r[2]))
            for r in rows
        ]
        bars.reverse()  # OKX newest-first -> oldest-first (in-progress bar last)
        return bars
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_taker_flow.py -k sim_fetch_taker_flow -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_taker_flow.py
git commit -m "feat(taker-flow): SimulatedExchange.fetch_taker_flow via real _ccxt rubik"
```

---

### Task A3: `OKXExchange.fetch_taker_flow`

**Files:**
- Modify: `src/integrations/exchange/okx.py` (base import block 13-28; new method after `fetch_open_interest_history` ends at 848)
- Test: `tests/test_taker_flow.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_taker_flow.py`:

```python
def _okx_with_rubik(data_rows):
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._client.public_get_rubik_stat_taker_volume_contract = AsyncMock(
        return_value={"code": "0", "data": data_rows, "msg": ""}
    )
    return ex


@pytest.mark.asyncio
async def test_okx_fetch_taker_flow_parses_and_ascends():
    rows = [
        ["1778644800000", "5800000", "4200000"],  # newest = in-progress
        ["1778644500000", "9000000", "8000000"],  # oldest
    ]
    ex = _okx_with_rubik(rows)
    bars = await ex.fetch_taker_flow("BTC/USDT:USDT", "1h", 2)
    assert [b.ts for b in bars] == [1778644500000, 1778644800000]
    assert bars[-1].sell_usd == pytest.approx(5800000.0)
    assert bars[-1].buy_usd == pytest.approx(4200000.0)
    ex._client.public_get_rubik_stat_taker_volume_contract.assert_awaited_once_with(
        {"instId": "BTC-USDT-SWAP", "period": "1H", "unit": "2", "limit": "2"}
    )


@pytest.mark.asyncio
async def test_okx_fetch_taker_flow_empty():
    ex = _okx_with_rubik([])
    assert await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 6) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_taker_flow.py -k okx_fetch_taker_flow -v`
Expected: FAIL with `AttributeError: ... has no attribute 'fetch_taker_flow'`.

- [ ] **Step 3: Extend the base import in `okx.py`**

In `src/integrations/exchange/okx.py`, add `TakerFlowBar` and `_TAKER_VOLUME_PERIOD` to the `from src.integrations.exchange.base import (...)` block (lines 13-28), same style as Task A2 Step 3.

- [ ] **Step 4: Implement `fetch_taker_flow`**

In `src/integrations/exchange/okx.py`, immediately after `fetch_open_interest_history` (ends line 848, before `fetch_long_short_ratio` at 850):

```python
    @_retry()
    async def fetch_taker_flow(
        self,
        symbol: str,
        period: Literal["5m", "1h", "4h", "1d", "1w"] = "5m",
        limit: int = 6,
    ) -> list[TakerFlowBar]:
        inst_id = self._client.market(symbol)["id"]  # BTC/USDT:USDT -> BTC-USDT-SWAP
        try:
            raw = await self._client.public_get_rubik_stat_taker_volume_contract({
                "instId": inst_id,
                "period": _TAKER_VOLUME_PERIOD[period],
                "unit": "2",
                "limit": str(limit),
            })
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"OKX taker flow: {e}") from e
        rows = raw.get("data") or []
        bars = [
            TakerFlowBar(ts=int(r[0]), sell_usd=float(r[1]), buy_usd=float(r[2]))
            for r in rows
        ]
        bars.reverse()  # OKX newest-first -> oldest-first (in-progress bar last)
        return bars
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_taker_flow.py -k okx_fetch_taker_flow -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_taker_flow.py
git commit -m "feat(taker-flow): OKXExchange.fetch_taker_flow (mirrors OI rubik method)"
```

---

### Task A4: Promote `fetch_taker_flow` to `@abstractmethod` + sync 11 stubs

This is the single "contract promotion" commit. `Simulated` and `OKX` already implement the method (A2/A3), so the only breakage from adding the abstractmethod is the 11 complete `BaseExchange` test stubs — fixed in the same commit, keeping the suite green. `IncompleteExchange` (`test_exchange.py:28`) is the NEGATIVE abstractness test and is intentionally left incomplete (do NOT add the method there).

**Files:**
- Modify: `src/integrations/exchange/base.py` (add abstractmethod after `fetch_open_interest_history` block ends at 164)
- Modify: `tests/test_exchange.py` (4 complete stubs: `DummyExchange` at 236, 274, 313; `_Stub` at 351)
- Modify: `tests/test_price_level_alert.py` (`_TestExchange` at 13)
- Modify: `tests/test_tool_enhancement.py` (`_TestExchange` at 42, 95, 130, 171, 205, 235)
- Test: `tests/test_taker_flow.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_taker_flow.py`:

```python
def test_base_exchange_has_fetch_taker_flow_abstractmethod():
    import inspect
    from src.integrations.exchange.base import BaseExchange
    assert "fetch_taker_flow" in BaseExchange.__abstractmethods__
    sig = inspect.signature(BaseExchange.fetch_taker_flow)
    assert sig.parameters["period"].default == "5m"
    assert sig.parameters["limit"].default == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_taker_flow.py::test_base_exchange_has_fetch_taker_flow_abstractmethod -v`
Expected: FAIL — `fetch_taker_flow` not in `__abstractmethods__`.

- [ ] **Step 3: Add the abstractmethod to `base.py`**

In `src/integrations/exchange/base.py`, after the `fetch_open_interest_history` abstractmethod block (ends line 164, before `fetch_long_short_ratio` at 165):

```python
    @abstractmethod
    async def fetch_taker_flow(
        self,
        symbol: str,
        period: Literal["5m", "1h", "4h", "1d", "1w"] = "5m",
        limit: int = 6,
    ) -> list["TakerFlowBar"]:
        """Taker buy/sell volume bars (USD notional) from rubik taker-volume.

        Returns oldest-first; the LAST bar is the in-progress current bucket
        (returned raw, no detection/labeling — that is the tool layer's job).
        """
        ...
```

- [ ] **Step 4: Run the full exchange + tool stub suites to confirm the breakage**

Run: `pytest tests/test_exchange.py tests/test_price_level_alert.py tests/test_tool_enhancement.py -x -q`
Expected: FAIL — `TypeError: Can't instantiate abstract class ... with abstract method fetch_taker_flow` for the 11 complete stubs. (`IncompleteExchange` already raises TypeError by design — unaffected.)

- [ ] **Step 5: Add a one-line stub to each of the 11 complete stubs**

To each of the 11 complete stub classes, add this line alongside the other abstract-method stubs (place it right after the existing `async def fetch_open_interest_history(...)` line in each class):

```python
        async def fetch_taker_flow(self, symbol, period="5m", limit=6): return []
```

Exact insertion sites:
- `tests/test_exchange.py`: after lines 250, 288, 327 (`DummyExchange` ×3) and after 365 (`_Stub`). NOTE the `_Stub` variant uses parameter name `s` for symbol elsewhere — match its local style: `async def fetch_taker_flow(self, s, period="5m", limit=6): return []`.
- `tests/test_price_level_alert.py`: in `_TestExchange` (class at 13), after its `fetch_open_interest_history` line.
- `tests/test_tool_enhancement.py`: in each `_TestExchange` (classes at 42, 95, 130, 171, 205, 235), after each one's `fetch_open_interest_history` line (e.g. after line 56 for the first).

Do NOT touch `IncompleteExchange` (`test_exchange.py:28`).

- [ ] **Step 6: Run the suites to verify green**

Run: `pytest tests/test_exchange.py tests/test_price_level_alert.py tests/test_tool_enhancement.py tests/test_taker_flow.py::test_base_exchange_has_fetch_taker_flow_abstractmethod -q`
Expected: PASS (all stubs instantiate; abstractmethod test passes; `IncompleteExchange` still raises TypeError as asserted by its own test).

- [ ] **Step 7: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_exchange.py tests/test_price_level_alert.py tests/test_tool_enhancement.py tests/test_taker_flow.py
git commit -m "feat(taker-flow): promote fetch_taker_flow to abstractmethod + sync test stubs"
```

---

### Task A5: `MarketDataService.get_taker_flow` (NOT cached)

**Files:**
- Modify: `src/integrations/market_data.py` (import line 4; new method after `get_recent_trades:34`)
- Test: `tests/test_taker_flow.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_taker_flow.py`:

```python
@pytest.mark.asyncio
async def test_market_data_get_taker_flow_passthrough_uncached():
    from src.integrations.market_data import MarketDataService
    from src.integrations.exchange.base import TakerFlowBar
    exchange = AsyncMock()
    exchange.fetch_taker_flow.return_value = [TakerFlowBar(ts=1, sell_usd=2.0, buy_usd=3.0)]
    svc = MarketDataService(exchange)
    out1 = await svc.get_taker_flow("BTC/USDT:USDT", "5m", 21)
    out2 = await svc.get_taker_flow("BTC/USDT:USDT", "5m", 21)
    assert out1[0].buy_usd == pytest.approx(3.0)
    # NOT cached: two calls -> two underlying fetches (unlike get_open_interest_history)
    assert exchange.fetch_taker_flow.await_count == 2
    exchange.fetch_taker_flow.assert_awaited_with("BTC/USDT:USDT", "5m", 21)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_taker_flow.py::test_market_data_get_taker_flow_passthrough_uncached -v`
Expected: FAIL — `AttributeError: 'MarketDataService' object has no attribute 'get_taker_flow'`.

- [ ] **Step 3: Add `TakerFlowBar` to the import + implement the method**

In `src/integrations/market_data.py`, add `TakerFlowBar` to the base import (line 4). Then after `get_recent_trades` (ends line 34):

```python
    async def get_taker_flow(self, symbol: str, period: str = "5m", limit: int = 6) -> list[TakerFlowBar]:
        # NOT cached (contrast get_open_interest_history's 180s TTL): taker_flow's
        # value is the live in-progress bucket; caching would stale formed% AND
        # desync from the uncached OHLCV join used for the Close column (§3.2/§4.1).
        return await self._exchange.fetch_taker_flow(symbol, period, limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_taker_flow.py::test_market_data_get_taker_flow_passthrough_uncached -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/market_data.py tests/test_taker_flow.py
git commit -m "feat(taker-flow): MarketDataService.get_taker_flow (uncached passthrough)"
```

---

### Task A6: Pure `_render_taker_flow` helper + render tests

The renderer is a PURE function taking injected `now_ms` (mirrors `_derive_oi_anchors`), so in-progress detection, formed%, CVD, RVol, Close-join, 1d-degrade, and the anchor line are all deterministically testable without a clock or network. The async tool (A7) only feeds it data.

**Files:**
- Modify: `src/agent/tools_perception.py` (add constants + helpers near `_derive_oi_anchors:1049`; the `Trade`/`OpenInterestHistoryPoint` imports already exist at the top of the module)
- Test: `tests/test_taker_flow.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_taker_flow.py`. These build 21+ bars so the fixed-20 RVol baseline is satisfiable, and pass an explicit `now_ms` so the newest bar is in-progress (its open is < period_ms before now).

```python
def _bars(n, period_ms, *, base_open, sell=1_000_000.0, buy=1_000_000.0):
    """n ascending TakerFlowBar; bar i opens at base_open + i*period_ms.
    Caller sets base_open so the last bar is in-progress relative to now_ms."""
    from src.integrations.exchange.base import TakerFlowBar
    return [TakerFlowBar(ts=base_open + i * period_ms, sell_usd=sell, buy_usd=buy)
            for i in range(n)]


def test_render_taker_flow_now_line_and_in_progress():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    # last bar opens 2min before now -> in-progress, 2.0/5min formed
    bars = _bars(21, period_ms, base_open=now - 120_000 - 20 * period_ms)
    # make the newest bar buy-heavy so buy% is checkable
    bars[-1].buy_usd, bars[-1].sell_usd = 700_000.0, 300_000.0
    out = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="BTC-USDT-SWAP", fetch_ts="04:34")
    assert "=== Taker Flow (BTC-USDT-SWAP · 5m bars · @04:34 UTC) ===" in out
    assert "current 5m, 2.0/5min formed" in out
    assert "70% taker buy" in out                 # newest bar buy%
    assert "row 1 = current in-progress" in out
    assert "still forming (2.0/5min)" in out      # per-bar footnote


def test_render_taker_flow_window_cvd_and_net_sell_count():
    from src.agent.tools_perception import _render_taker_flow
    from src.integrations.exchange.base import TakerFlowBar
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    # displayed window = last 3 bars; make 1 of them net-sell
    bars[-3].buy_usd, bars[-3].sell_usd = 1_000_000.0, 1_000_000.0   # net 0
    bars[-2].buy_usd, bars[-2].sell_usd = 2_000_000.0, 1_000_000.0   # +1M
    bars[-1].buy_usd, bars[-1].sell_usd = 500_000.0, 1_500_000.0     # -1M (net-sell)
    out = _render_taker_flow(bars, "5m", 3, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "Window (3 bars = 15min):" in out
    assert "1/3 bars net-sell" in out
    # CVD over window (oldest->newest cumulative): 0, +1M, then 0 => window CVD ~ 0.0
    assert "CVD +0.0$M" in out or "CVD -0.0$M" in out


def test_render_taker_flow_rvol_fixed_20_baseline_and_limit_1_no_degeneracy():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    # 20 closed bars each total=2M (sell+buy=1M+1M); in-progress newest total=4M
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    bars[-1].buy_usd, bars[-1].sell_usd = 2_000_000.0, 2_000_000.0   # total 4M
    out = _render_taker_flow(bars, "5m", 1, now_ms=now, symbol="X", fetch_ts="00:00")
    # newest total 4M / 20-bar avg 2M = 2.0x ; limit=1 still computes (no "—")
    assert "2.0× (vs 20-bar avg)" in out
    assert "RVol(×20-bar)" in out


def test_render_taker_flow_rvol_degrades_below_20_closed():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(6, period_ms, base_open=now - 60_000 - 5 * period_ms)  # only 5 closed
    out = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "vol —" in out or "—" in out  # RVol falls back when <20 closed bars


def test_render_taker_flow_close_column_joins_by_ts_and_dashes_missing():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    # provide close for the last 2 displayed bars, omit one -> "—"
    closes = {bars[-1].ts: 73531.0, bars[-2].ts: 73553.0}  # bars[-3] missing
    out = _render_taker_flow(bars, "5m", 3, now_ms=now, symbol="X", fetch_ts="00:00", closes=closes)
    assert "Close" in out
    assert "73531" in out and "73553" in out
    # the unmatched displayed bar shows — in the Close column
    assert out.count("—") >= 1


def test_render_taker_flow_close_all_missing_safety_net_collapses_column():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    out = _render_taker_flow(bars, "5m", 3, now_ms=now, symbol="X", fetch_ts="00:00", closes={})
    # every displayed bar unmatched -> omit column + single explicit note (not per-row —)
    assert "no OHLCV bar matched" in out


def test_render_taker_flow_close_note_omits_column_for_1d():
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 86_400_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 3_600_000 - 20 * period_ms)
    note = "Close: n/a — 1d rubik/OHLCV day-boundary mismatch (16:00 vs 00:00 UTC)"
    out = _render_taker_flow(bars, "1d", 3, now_ms=now, symbol="X", fetch_ts="00:00", close_note=note)
    assert note in out
    assert "Close" not in out.split("Per-bar")[1].splitlines()[1]  # header has no Close col


def test_render_taker_flow_anchor_line_when_provided_and_absent_when_none():
    from src.agent.tools_perception import _render_taker_flow
    from src.integrations.exchange.base import TakerFlowBar
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    anchor_bar = TakerFlowBar(ts=now - 34 * 60_000, sell_usd=4_700_000.0, buy_usd=5_300_000.0)
    out = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00",
                             anchor=("1h", anchor_bar))
    assert "1h-scale anchor (current 1h, 34min formed):" in out
    assert "53% buy" in out  # 5.3M / (5.3M+4.7M) = 53.0% exactly (off the .5 round-half-even boundary)
    out2 = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "anchor" not in out2.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_taker_flow.py -k render_taker_flow -v`
Expected: FAIL — `ImportError: cannot import name '_render_taker_flow'`.

- [ ] **Step 3: Add constants + format helpers + the renderer**

In `src/agent/tools_perception.py`, near `_derive_oi_anchors` (after `_format_oi_usd:1046`), add:

```python
# --- taker_flow (get_taker_flow) constants + helpers (spec §3.1-3.3) ---
_TAKER_FLOW_PERIOD_MS = {
    "5m": 5 * 60_000, "1h": 60 * 60_000, "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000, "1w": 7 * 24 * 60 * 60_000,
}
# context-anchor up-tier on the 5m->1h->4h->1d->1w ladder (§3.3). Keys are also
# the exact set of valid *tool* periods ({5m,1h,4h,1d}); 1w is anchor-only.
_TAKER_FLOW_ANCHOR = {"5m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}
_TAKER_FLOW_RVOL_BARS = 20  # fixed baseline window (closed bars), decoupled from limit


def _pick_usd_scale(values: list[float]) -> tuple[str, float]:
    """One $K/$M scale for a column, chosen from peak abs magnitude (§3.2)."""
    peak = max((abs(v) for v in values), default=0.0)
    return ("$M", 1e6) if peak >= 1e6 else ("$K", 1e3)


def _fmt_scaled(v: float, divisor: float) -> str:
    return f"{v / divisor:+.1f}"


def _fmt_hhmm(ts_ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%H:%M")


def _render_taker_flow(
    bars: list["TakerFlowBar"],
    period: str,
    limit: int,
    *,
    now_ms: int,
    symbol: str,
    fetch_ts: str,
    closes: dict[int, float] | None = None,
    close_note: str | None = None,
    anchor: tuple[str, "TakerFlowBar"] | None = None,
) -> str:
    """Render the taker-flow report. Pure + deterministic given now_ms.

    bars: ascending; bars[-1] is the in-progress current bucket (kept + labeled).
    closes: bar-open-ts -> close px (OHLCV join). close_note: when set, omit the
    Close column and emit this note instead (1d day-boundary, or OHLCV failure).
    anchor: (uptier_label, uptier_in_progress_bar) or None.
    """
    period_ms = _TAKER_FLOW_PERIOD_MS[period]
    period_min = period_ms / 60_000
    newest = bars[-1]
    is_in_progress = newest.ts + period_ms > now_ms
    elapsed_min = max(0.0, (now_ms - newest.ts) / 60_000)

    def _total(b): return b.sell_usd + b.buy_usd
    def _net(b): return b.buy_usd - b.sell_usd
    def _buy_pct(b):
        t = _total(b)
        return (b.buy_usd / t * 100) if t > 0 else 0.0

    display = bars[-limit:]                              # oldest..newest displayed
    closed = bars[:-1] if is_in_progress else bars
    baseline = closed[-_TAKER_FLOW_RVOL_BARS:]
    baseline_avg = (
        sum(_total(b) for b in baseline) / len(baseline)
        if len(baseline) >= _TAKER_FLOW_RVOL_BARS else None
    )

    # CVD cumulative over displayed window, from oldest displayed bar upward
    cvd_running, cvd_by_ts = 0.0, {}
    for b in display:                                    # ascending
        cvd_running += _net(b)
        cvd_by_ts[b.ts] = cvd_running

    scale_label, divisor = _pick_usd_scale([_net(b) for b in display] + list(cvd_by_ts.values()))

    lines = [f"=== Taker Flow ({symbol} · {period} bars · @{fetch_ts} UTC) ===", ""]

    now_rvol = (_total(newest) / baseline_avg) if baseline_avg else None
    rvol_now = f"{now_rvol:.1f}× (vs {_TAKER_FLOW_RVOL_BARS}-bar avg)" if now_rvol is not None else "—"
    formed = (f"current {period}, {elapsed_min:.1f}/{period_min:g}min formed"
              if is_in_progress else f"current {period}, closed")
    lines.append(
        f"Now ({formed}):  {_buy_pct(newest):.0f}% taker buy · "
        f"net {_fmt_scaled(_net(newest), divisor)}{scale_label} · vol {rvol_now}"
    )
    net_sell_n = sum(1 for b in display if _net(b) < 0)
    lines.append(
        f"Window ({len(display)} bars = {len(display) * period_min:g}min):  "
        f"CVD {_fmt_scaled(cvd_by_ts[display[-1].ts], divisor)}{scale_label} · "
        f"{net_sell_n}/{len(display)} bars net-sell"
    )
    lines.append("")

    # Close column: omitted if close_note set; else joined by ts; collapse w/ safety net
    show_close = close_note is None and closes is not None
    rendered_closes = {}
    if show_close:
        rendered_closes = {b.ts: closes.get(b.ts) for b in display}
        if all(v is None for v in rendered_closes.values()):
            show_close = False
            close_note = "Close: n/a — no OHLCV bar matched (timestamp join empty)"

    hdr = f"  Time     Buy%   Net({scale_label})   RVol(×20-bar)   CVD({scale_label})"
    if show_close:
        hdr += "   Close"
    lines.append("Per-bar (bar open UTC, newest first; row 1 = current in-progress):")
    lines.append(hdr)
    for b in reversed(display):                          # newest-first
        star = "*" if (is_in_progress and b is newest) else " "
        rvol = (_total(b) / baseline_avg) if baseline_avg else None
        rvol_s = f"{rvol:.1f}×" if rvol is not None else "—"
        row = (f"  {_fmt_hhmm(b.ts)}{star}  {_buy_pct(b):>3.0f}%  "
               f"{_fmt_scaled(_net(b), divisor):>7}  {rvol_s:>5}  "
               f"{_fmt_scaled(cvd_by_ts[b.ts], divisor):>8}")
        if show_close:
            c = rendered_closes.get(b.ts)
            row += f"  {c:.0f}" if c is not None else "  —"
        lines.append(row)
    if is_in_progress:
        lines.append(f"  [* row 1 = current bar still forming ({elapsed_min:.1f}/{period_min:g}min)]")
    if close_note is not None:
        lines.append(close_note)

    if anchor is not None:
        up_label, up_bar = anchor
        up_ms = _TAKER_FLOW_PERIOD_MS[up_label]
        up_in_prog = up_bar.ts + up_ms > now_ms
        up_elapsed = max(0.0, (now_ms - up_bar.ts) / 60_000)
        up_formed = (f"current {up_label}, {up_elapsed:.0f}min formed"
                     if up_in_prog else f"current {up_label}, closed")
        up_total = up_bar.sell_usd + up_bar.buy_usd
        up_buy = (up_bar.buy_usd / up_total * 100) if up_total > 0 else 0.0
        up_net = up_bar.buy_usd - up_bar.sell_usd
        up_scale, up_div = _pick_usd_scale([up_net])
        lines.append("")
        lines.append(
            f"{up_label}-scale anchor ({up_formed}):  "
            f"{up_buy:.0f}% buy · net {_fmt_scaled(up_net, up_div)}{up_scale}"
        )
    return "\n".join(lines)
```

NOTE: the renderer/tool only use `TakerFlowBar` in string-quoted hints (`list["TakerFlowBar"]`) and never construct it or `isinstance`-check it at runtime, and `tools_perception.py` already has `from __future__ import annotations`. So add `TakerFlowBar` to the existing `if TYPE_CHECKING:` block (lines 7-9, alongside `OpenInterestHistoryPoint, Trade`) — a runtime import is unnecessary. Tests import `TakerFlowBar` directly from `base`, so they are unaffected.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_taker_flow.py -k render_taker_flow -v`
Expected: PASS (8 tests). Adjust the `>3.0f`/`>7`/`>8` column widths only if an assertion on a substring (e.g. `"70% taker buy"`) is affected — assertions are substring-based, not full-line, so widths are free to tune.

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_taker_flow.py
git commit -m "feat(taker-flow): pure _render_taker_flow renderer (formed%/CVD/RVol/Close/anchor)"
```

---

### Task A7: async `get_taker_flow` tool (orchestration + reject + partial-failure)

**Files:**
- Modify: `src/agent/tools_perception.py` (add async tool after `_render_taker_flow`)
- Test: `tests/test_taker_flow.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_taker_flow.py`:

```python
import time as _time
from unittest.mock import AsyncMock, MagicMock
import pandas as pd


def _deps_with_taker(bars_by_period, *, ohlcv=None, ohlcv_exc=None, main_exc=None):
    """TradingDeps double: market_data.get_taker_flow keyed by period;
    get_ohlcv_dataframe returns `ohlcv` df (or raises ohlcv_exc)."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    async def _gtf(symbol, period, limit):
        if main_exc is not None and period in bars_by_period and limit > 1:
            raise main_exc
        return bars_by_period.get(period, [])
    deps.market_data.get_taker_flow = AsyncMock(side_effect=_gtf)
    if ohlcv_exc is not None:
        deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=ohlcv_exc)
    else:
        deps.market_data.get_ohlcv_dataframe = AsyncMock(
            return_value=ohlcv if ohlcv is not None else pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]))
    return deps


def _live_bars(n, period_ms):
    from src.integrations.exchange.base import TakerFlowBar
    now = int(_time.time() * 1000)
    base = now - 60_000 - (n - 1) * period_ms  # last bar in-progress
    return [TakerFlowBar(ts=base + i * period_ms, sell_usd=1e6, buy_usd=1e6) for i in range(n)]


@pytest.mark.asyncio
async def test_get_taker_flow_rejects_bad_period():
    from src.agent.tools_perception import get_taker_flow
    out = await get_taker_flow(_deps_with_taker({}), period="15m")
    assert "period must be one of: 5m, 1h, 4h, 1d" in out


@pytest.mark.asyncio
async def test_get_taker_flow_rejects_out_of_range_limit():
    from src.agent.tools_perception import get_taker_flow
    deps = _deps_with_taker({})
    assert "limit must be in [1, 36]" in await get_taker_flow(deps, "5m", 0)
    assert "limit must be in [1, 36]" in await get_taker_flow(deps, "5m", 37)


@pytest.mark.asyncio
async def test_get_taker_flow_main_failure_unavailable():
    from src.agent.tools_perception import get_taker_flow
    deps = _deps_with_taker({"5m": _live_bars(21, 300_000)}, main_exc=RuntimeError("boom"))
    out = await get_taker_flow(deps, "5m", 6)
    assert "Taker flow temporarily unavailable" in out


@pytest.mark.asyncio
async def test_get_taker_flow_empty():
    from src.agent.tools_perception import get_taker_flow
    out = await get_taker_flow(_deps_with_taker({"5m": []}), "5m", 6)
    assert "No taker-volume data available." in out


@pytest.mark.asyncio
async def test_get_taker_flow_ohlcv_failure_degrades_close_but_renders_flow():
    from src.agent.tools_perception import get_taker_flow
    deps = _deps_with_taker({"5m": _live_bars(21, 300_000), "1h": _live_bars(2, 3_600_000)},
                            ohlcv_exc=RuntimeError("ohlcv down"))
    out = await get_taker_flow(deps, "5m", 6)
    assert "Close: n/a — OHLCV temporarily unavailable" in out
    assert "Per-bar" in out  # flow rows still render


@pytest.mark.asyncio
async def test_get_taker_flow_1d_omits_close_column():
    from src.agent.tools_perception import get_taker_flow
    deps = _deps_with_taker({"1d": _live_bars(21, 86_400_000), "1w": _live_bars(2, 604_800_000)})
    out = await get_taker_flow(deps, "1d", 6)
    assert "day-boundary mismatch" in out


@pytest.mark.asyncio
async def test_get_taker_flow_anchor_failure_drops_anchor_line():
    from src.agent.tools_perception import get_taker_flow
    async def _gtf(symbol, period, limit):
        if period == "1h":
            raise RuntimeError("anchor down")
        return _live_bars(21, 300_000)
    deps = _deps_with_taker({"5m": _live_bars(21, 300_000)})
    deps.market_data.get_taker_flow = AsyncMock(side_effect=_gtf)
    out = await get_taker_flow(deps, "5m", 6)
    assert "Per-bar" in out          # main series renders
    assert "anchor" not in out.lower()  # anchor line dropped silently


@pytest.mark.asyncio
async def test_get_taker_flow_happy_path_includes_close_and_anchor():
    from src.agent.tools_perception import get_taker_flow
    main = _live_bars(21, 300_000)
    anchor = _live_bars(2, 3_600_000)
    ohlcv = pd.DataFrame([{"timestamp": b.ts, "open": 1, "high": 1, "low": 1,
                           "close": 73000 + i, "volume": 1} for i, b in enumerate(main)])
    deps = _deps_with_taker({"5m": main, "1h": anchor}, ohlcv=ohlcv)
    out = await get_taker_flow(deps, "5m", 6)
    assert "Close" in out
    assert "1h-scale anchor" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_taker_flow.py -k "get_taker_flow" -v`
Expected: FAIL — `ImportError: cannot import name 'get_taker_flow'`.

- [ ] **Step 3: Implement the async tool**

In `src/agent/tools_perception.py`, after `_render_taker_flow`:

```python
async def get_taker_flow(deps: TradingDeps, period: str = "5m", limit: int = 6) -> str:
    """Minute-level taker buy/sell flow over `limit` `period`-bars (impl).

    LLM-visible docstring lives on the trader.py @tool wrapper.
    """
    import time
    from datetime import datetime, timezone

    symbol = deps.symbol
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    # Fact-only explicit reject (no clamp, no Literal narrowing — soft-constraint §1/§2)
    if period not in _TAKER_FLOW_ANCHOR:  # valid tool periods == {5m,1h,4h,1d}
        return f"Invalid period '{period}'. period must be one of: 5m, 1h, 4h, 1d"
    if not (1 <= limit <= 36):
        return f"Invalid limit {limit}. limit must be in [1, 36]"

    header = f"=== Taker Flow ({symbol} · {period} bars · @{fetch_ts} UTC) ==="
    n = max(limit + 1, 21)  # fetch enough for fixed-20 RVol baseline + in-progress

    # Main rubik series — hard dependency.
    try:
        bars = await deps.market_data.get_taker_flow(symbol, period, n)
    except Exception as e:
        logger.exception("get_taker_flow main fetch failed for %s", symbol)
        return f"{header}\nTaker flow temporarily unavailable ({e.__class__.__name__})."
    if not bars:
        return f"{header}\nNo taker-volume data available."

    now_ms = int(time.time() * 1000)

    # OHLCV Close join — soft. 1d: day-boundary mismatch (probe E 0/10) -> omit column.
    closes: dict[int, float] | None = None
    close_note: str | None = None
    if period == "1d":
        close_note = ("Close: n/a — 1d rubik/OHLCV day-boundary mismatch "
                      "(16:00 vs 00:00 UTC)")
    else:
        try:
            df = await deps.market_data.get_ohlcv_dataframe(symbol, period, limit=n)
            closes = {int(r.timestamp): float(r.close) for r in df.itertuples()}
        except Exception:
            logger.exception("get_taker_flow OHLCV join failed for %s", symbol)
            close_note = "Close: n/a — OHLCV temporarily unavailable"

    # Context anchor (up-tier in-progress bar) — soft; drop the line on failure/empty.
    anchor = None
    up_label = _TAKER_FLOW_ANCHOR[period]
    try:
        up_bars = await deps.market_data.get_taker_flow(symbol, up_label, 1)
        if up_bars:
            anchor = (up_label, up_bars[-1])
    except Exception:
        logger.exception("get_taker_flow anchor fetch failed for %s", symbol)

    return _render_taker_flow(
        bars, period, limit, now_ms=now_ms, symbol=symbol, fetch_ts=fetch_ts,
        closes=closes, close_note=close_note, anchor=anchor,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_taker_flow.py -k "get_taker_flow" -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the whole taker_flow file**

Run: `pytest tests/test_taker_flow.py -q`
Expected: PASS (all tests so far).

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/test_taker_flow.py
git commit -m "feat(taker-flow): async get_taker_flow tool (reject + OHLCV join + anchor + partial-failure)"
```

---

### Task A8: `trader.py` wrapper + register + drift-guard count

**Files:**
- Modify: `src/agent/trader.py` (wrapper after `get_recent_trades` wrapper ends at 402; `REGISTERED_TOOL_NAMES` 719-755)
- Modify: `tests/test_trader_agent.py:82` (count 33→34, `(19+14)`→`(20+14)`)
- Test: `tests/test_trader_agent.py`

- [ ] **Step 1: Update the drift-guard count assertion first (failing test)**

In `tests/test_trader_agent.py`, change lines 82-84:

```python
    assert len(REGISTERED_TOOL_NAMES) == 34, (
        f"Expected 34 tools (20+14), got {len(REGISTERED_TOOL_NAMES)}"
    )
```

- [ ] **Step 2: Run the drift-guard to verify it fails**

Run: `pytest tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools -v`
Expected: FAIL — agent has 33 tools / REGISTERED has 33; count assertion expects 34, AND the set-equality assertion will also flag once we add the name. (Right now it fails on the count line.)

- [ ] **Step 3: Add the `@tool` wrapper**

In `src/agent/trader.py`, immediately after the `get_recent_trades` wrapper (ends line 402, before the `get_multi_timeframe_snapshot` wrapper at 405). The LLM-visible channel is THIS docstring (per `project_tool_docstring_llm_channel`); griffe strips block-style admonitions but passes the `Returns:` block raw (per `project_griffe_example_section_stripped`), so the call→output example lives INSIDE `Returns:`:

```python
    @tool
    async def get_taker_flow(ctx: RunContext[TradingDeps], period: str = "5m", limit: int = 6) -> str:
        """Minute-level taker buy/sell flow: who is hitting the book over recent bars.

        Server-aggregated taker volume (USD) per bar — the minute-to-hours trend
        companion to get_recent_trades (which is a ~40s tick micro-view). Row 1 is
        the current in-progress bar (labeled with how far it has formed); CVD is
        cumulative net taker volume across the shown window only, so do NOT compare
        CVD across separate calls (the window's oldest bar — its zero point — rolls
        forward each call). RVol is the bar's taker total vs a fixed 20-closed-bar
        average. A same-period 1h/4h/1d context-anchor line shows the larger bar's
        current direction. period one of 5m/1h/4h/1d; limit 1..36 bars.

        Args:
            period: bar size, one of "5m", "1h", "4h", "1d" (default "5m").
            limit: number of bars to show, 1..36 (default 6).

        Returns:
            A taker-flow report. Example for get_taker_flow("5m", 6):
            === Taker Flow (BTC-USDT-SWAP · 5m bars · @04:34 UTC) ===
            Now (current 5m, 4.0/5min formed):  41% taker buy · net -5.8$M · vol 0.3× (vs 20-bar avg)
            Window (6 bars = 30min):  CVD +109.8$M · 2/6 bars net-sell
            Per-bar (bar open UTC, newest first; row 1 = current in-progress):
              Time     Buy%   Net($M)   RVol(×20-bar)   CVD($M)   Close
              04:30*    41%     -5.8    0.3×   +109.8    73531
              ... (older bars) ...
              [* row 1 = current bar still forming (4.0/5min)]
            1h-scale anchor (current 1h, 34min formed):  53% buy · net +62$M
        """
        from src.agent.tools_perception import get_taker_flow as _impl

        return await _impl(ctx.deps, period=period, limit=limit)
```

- [ ] **Step 4: Register the tool name**

In `src/agent/trader.py`, in `REGISTERED_TOOL_NAMES`: change the perception comment `# --- 感知 (19) ---` (line 720) to `# --- 感知 (20) ---`, and insert `"get_taker_flow",` immediately after `"get_recent_trades",` (line 737) to match decoration order.

- [ ] **Step 5: Run the drift-guard + full trader tests to verify pass**

Run: `pytest tests/test_trader_agent.py -v`
Expected: PASS — set-equality holds (agent now has `get_taker_flow`, REGISTERED has it), count == 34, no duplicates.

- [ ] **Step 6: Commit**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "feat(taker-flow): register get_taker_flow @tool wrapper (perception 19->20)"
```

---

## Group B: `get_recent_trades` refactor

### Task B1: `fetch_trades` 张→base normalization (sim + okx)

The unit fix lives in the `fetch_trades` ADAPTER, reading the real *market* `contractSize` — NOT the execution-layer `get_contract_size` (sim returns 1.0 there, load-bearing, untouched this iter). After this, `Trade.amount` truly equals its `# base-currency` annotation. `fetch_trades`' only consumer is `get_recent_trades` (matching never reads it), so the semantic change is safe (§4.2). No existing adapter test asserts a raw `amount` value (verified: `test_market_data.py:59` mocks the exchange; `test_sim_microstructure_real_data.py` recent-trades tests mock at the tool layer), so this task is additive.

**Files:**
- Modify: `src/integrations/exchange/simulated.py` (`fetch_trades` 1206-1224)
- Modify: `src/integrations/exchange/okx.py` (`fetch_trades` 893-908)
- Test: `tests/test_recent_trades_buckets.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recent_trades_buckets.py`:

```python
"""Tests for the get_recent_trades count-bucket refactor + fetch_trades 张->base
unit normalization. Spec docs/superpowers/specs/2026-05-30-order-flow-tools-redesign-design.md
§3.4 (count buckets), §4.2 (Option B adapter), §5 ④⑤.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_sim_fetch_trades_normalizes_contracts_to_base():
    """§4.2/④: raw ccxt amount is OKX contracts (张); multiply by real market
    contractSize so Trade.amount is base-currency. Mock-fidelity (⑤): include
    info.sz + contractSize != 1 (BTC swap 0.01)."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP", "contractSize": 0.01}
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1, "side": "buy", "price": 70000.0, "amount": 5.0,
         "id": "a", "info": {"sz": "5"}},   # 5 张 * 0.01 = 0.05 base
        {"timestamp": 2, "side": "sell", "price": 70010.0, "amount": 2.0,
         "id": "b", "info": {"sz": "2"}},
    ])
    ex._validate_symbol = lambda s: None
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert trades[0].amount == pytest.approx(0.05)
    assert trades[1].amount == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_sim_fetch_trades_contractsize_missing_defaults_to_one():
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "X"}  # no contractSize key
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1, "side": "buy", "price": 100.0, "amount": 3.0, "id": "a"}])
    ex._validate_symbol = lambda s: None
    trades = await ex.fetch_trades("X/USDT:USDT", limit=500)
    assert trades[0].amount == pytest.approx(3.0)  # cs defaults to 1.0


@pytest.mark.asyncio
async def test_okx_fetch_trades_normalizes_contracts_to_base():
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.markets = {"ETH/USDT:USDT": {"contractSize": 0.1}}
    ex._client.market.return_value = {"contractSize": 0.1}
    ex._client.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1, "side": "buy", "price": 3000.0, "amount": 4.0, "id": "a",
         "info": {"sz": "4"}}])  # 4 张 * 0.1 = 0.4 base
    trades = await ex.fetch_trades("ETH/USDT:USDT", limit=500)
    assert trades[0].amount == pytest.approx(0.4)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_recent_trades_buckets.py -k fetch_trades -v`
Expected: FAIL — amounts are 5.0/2.0/4.0 (raw 张), not 0.05/0.02/0.4.

- [ ] **Step 3: Normalize in `simulated.py` `fetch_trades`**

In `src/integrations/exchange/simulated.py`, edit `fetch_trades` (1206-1224). After the `data = await self._ccxt.fetch_trades(...)` block and before the loop, read the real market contractSize once; multiply each amount:

```python
        # 张->base normalization (Option B, §4.2): raw ccxt amount is OKX
        # contracts; multiply by the REAL market contractSize so Trade.amount is
        # base-currency (as its model annotation claims). NOT get_contract_size
        # (sim returns 1.0 there — execution-layer, load-bearing, untouched).
        cs = float((self._ccxt.market(symbol) or {}).get("contractSize") or 1.0)
        trades: list[Trade] = []
        for r in data:
            ts, side, px, amt = r.get("timestamp"), r.get("side"), r.get("price"), r.get("amount")
            if ts is None or side is None or px is None or amt is None:
                continue  # None-safe: CCXT safe_* may return None on malformed rows
            tid = r.get("id")
            trades.append(Trade(timestamp=int(ts), side=str(side), price=float(px),
                                amount=float(amt) * cs, trade_id=str(tid) if tid is not None else None))
        trades.sort(key=lambda t: t.timestamp)
        return trades
```

- [ ] **Step 4: Normalize in `okx.py` `fetch_trades`**

In `src/integrations/exchange/okx.py`, edit `fetch_trades` (893-908) to load markets if needed and multiply:

```python
    @_retry(max_retries=2, base_delay=0.5)
    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
        if not self._client.markets:
            await self._client.load_markets()
        cs = float((self._client.markets.get(symbol) or {}).get("contractSize") or 1.0)
        data = await self._client.fetch_trades(symbol, limit=limit)
        trades: list[Trade] = []
        for raw in data:
            raw_id = raw.get("id")
            trades.append(Trade(
                timestamp=int(raw["timestamp"]),
                side=str(raw["side"]),
                price=float(raw["price"]),
                amount=float(raw["amount"]) * cs,  # 张->base (Option B, §4.2)
                trade_id=str(raw_id) if raw_id is not None else None,
            ))
        trades.sort(key=lambda t: t.timestamp)
        return trades
```

- [ ] **Step 5: Run tests to verify they pass + run the existing fetch_trades suites**

Run: `pytest tests/test_recent_trades_buckets.py -k fetch_trades tests/test_exchange.py tests/test_sim_microstructure_real_data.py tests/test_market_data.py -q`
Expected: PASS. (If any pre-existing test asserts a raw `amount` from the real adapter — none found at plan time — update its expectation to the ×contractSize value.)

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/simulated.py src/integrations/exchange/okx.py tests/test_recent_trades_buckets.py
git commit -m "fix(recent-trades): normalize fetch_trades 张->base via real market contractSize (Option B)"
```

---

### Task B2: `get_recent_trades` count-bucket refactor

**Files:**
- Modify: `src/agent/tools_perception.py` (constants 22-24; `_fmt_money` helper near `_format_oi_usd`; rewrite `get_recent_trades` 1809-1900)
- Modify: `tests/test_toolkit_iter2.py` (rewrite recent-trades tests 154-243)
- Test: `tests/test_recent_trades_buckets.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_recent_trades_buckets.py`:

```python
def _mk_trades(specs):
    """specs: list of (ts_ms, side, price, base_amount)."""
    from src.integrations.exchange.base import Trade
    return [Trade(timestamp=ts, side=s, price=p, amount=a, trade_id=str(i))
            for i, (ts, s, p, a) in enumerate(specs)]


def _deps(trades):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data.get_recent_trades = AsyncMock(return_value=trades)
    return deps


@pytest.mark.asyncio
async def test_recent_trades_count_buckets_5x100():
    from src.agent.tools_perception import get_recent_trades
    # 500 trades, alternating side, 1s apart
    specs = [(1_000_000 + i * 1000, "buy" if i % 2 == 0 else "sell", 70000.0, 0.01)
             for i in range(500)]
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "last 500 ·" in out
    assert "Per 100-trade slice (newest first):" in out
    assert "1 (new)" in out and "5 (old)" in out
    assert "by count" in out and "by volume" in out


@pytest.mark.asyncio
async def test_recent_trades_usd_is_amount_times_price():
    from src.agent.tools_perception import get_recent_trades
    # one 1.0-base buy at 70000 -> $70.0K notional largest single
    specs = [(1_000_000 + i * 1000, "buy", 70000.0, 0.001) for i in range(99)]
    specs.append((1_000_000 + 99_000, "sell", 70000.0, 1.0))  # 1.0 base * 70000 = $70K
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "$70.0K SELL" in out  # USD notional = amount(base) * price


@pytest.mark.asyncio
async def test_recent_trades_count_vs_volume_buy_pct_divergence():
    from src.agent.tools_perception import get_recent_trades
    # many tiny buys (count-heavy buy) + few huge sells (volume-heavy sell)
    specs = [(1_000_000 + i * 1000, "buy", 70000.0, 0.0001) for i in range(90)]
    specs += [(1_000_000 + (90 + i) * 1000, "sell", 70000.0, 1.0) for i in range(10)]
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    # by count: 90% buy ; by volume: sells dominate -> low buy%
    assert "90% by count" in out
    assert "by volume" in out  # exact vol% checked loosely; divergence present


@pytest.mark.asyncio
async def test_recent_trades_under_100_single_aggregate_no_table():
    from src.agent.tools_perception import get_recent_trades
    specs = [(1_000_000 + i * 1000, "buy", 70000.0, 0.01) for i in range(40)]
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "last 40 ·" in out
    assert "Per 100-trade slice" not in out  # too few for a slice table


@pytest.mark.asyncio
async def test_recent_trades_partial_fewer_slices_with_real_counts():
    from src.agent.tools_perception import get_recent_trades
    specs = [(1_000_000 + i * 1000, "buy", 70000.0, 0.01) for i in range(250)]
    out = await get_recent_trades(_deps(_mk_trades(specs)))
    assert "last 250 ·" in out
    # 250 -> 3 slices (100,100,50); oldest slice shows its real count
    assert "[50 tr]" in out


@pytest.mark.asyncio
async def test_recent_trades_empty_and_failure():
    from src.agent.tools_perception import get_recent_trades
    out_empty = await get_recent_trades(_deps([]))
    assert "No recent trades." in out_empty
    deps = MagicMock(); deps.symbol = "BTC/USDT:USDT"
    deps.market_data.get_recent_trades = AsyncMock(side_effect=Exception("timeout"))
    out_fail = await get_recent_trades(deps)
    assert "Recent trades temporarily unavailable" in out_fail
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_recent_trades_buckets.py -k recent_trades -v`
Expected: FAIL — old time-bucket output / `window_seconds` signature mismatch.

- [ ] **Step 3: Replace the constants**

In `src/agent/tools_perception.py`, replace lines 22-24:

```python
RECENT_TRADES_SLICE_SIZE = 100         # trades per count-bucket
RECENT_TRADES_N_SLICES = 5             # max slices -> 5×100 = 500 (OKX /market/trades cap)
RECENT_TRADES_MAX_FETCH = RECENT_TRADES_SLICE_SIZE * RECENT_TRADES_N_SLICES  # 500
```

- [ ] **Step 4: Add the `_fmt_money` helper**

In `src/agent/tools_perception.py`, near `_format_oi_usd` (1040-1046), add:

```python
def _fmt_money(v: float) -> str:
    """Signed USD with auto $K/$M scale (ASCII sign, for test stability)."""
    a = abs(v)
    sign = "-" if v < 0 else "+"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.1f}K"
    return f"{sign}${a:.0f}"
```

- [ ] **Step 5: Rewrite `get_recent_trades`**

Replace the entire `get_recent_trades` function (1809-1900) with:

```python
async def get_recent_trades(deps: TradingDeps) -> str:
    """Seconds-level tick micro-view over the last ~500 trades (impl).

    Count-buckets (5×100), newest-first. USD notional = amount(base) × price
    (amount is base-currency after the fetch_trades adapter normalization, §4.2).
    LLM-visible docstring lives on the trader.py @tool wrapper.
    """
    import statistics
    from datetime import datetime, timezone

    symbol = deps.symbol
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    try:
        trades = await deps.market_data.get_recent_trades(symbol, limit=RECENT_TRADES_MAX_FETCH)
    except Exception as e:
        logger.exception("get_recent_trades failed for %s", symbol)
        return f"=== Recent Trades ({symbol} · @{fetch_ts} UTC) ===\nRecent trades temporarily unavailable ({e.__class__.__name__})."
    if not trades:
        return f"=== Recent Trades ({symbol} · @{fetch_ts} UTC) ===\nNo recent trades."

    trades = sorted(trades, key=lambda t: t.timestamp)  # ascending (defensive)
    n = len(trades)
    span_s = (trades[-1].timestamp - trades[0].timestamp) / 1000 or 1e-9
    usd = [t.amount * t.price for t in trades]
    total_usd = sum(usd) or 1e-9
    buy_usd = sum(u for u, t in zip(usd, trades) if t.side == "buy")
    buy_cnt = sum(1 for t in trades if t.side == "buy")
    net_usd = buy_usd - (total_usd - buy_usd)

    lines = [f"=== Recent Trades ({symbol} · last {n} · {span_s:.1f}s · @{fetch_ts} UTC) ===", ""]
    lines.append(
        f"Taker buy:  {buy_cnt / n * 100:.0f}% by count · {buy_usd / total_usd * 100:.0f}% by volume"
        f"      Net: {_fmt_money(net_usd)} · {n / span_s:.1f} tr/s"
    )
    li = max(range(n), key=lambda i: usd[i])
    lines.append(
        f"Largest single:  {_fmt_money(usd[li]).lstrip('+')} {trades[li].side.upper()}"
        f"  (= {usd[li] / total_usd * 100:.1f}% of window vol)"
    )
    srt = sorted(usd)
    med = statistics.median(srt)
    p95 = srt[min(int(0.95 * n), n - 1)]
    lines.append(
        f"Size (USD notional):  med {_fmt_money(med).lstrip('+')} · "
        f"mean {_fmt_money(total_usd / n).lstrip('+')} · p95 {_fmt_money(p95).lstrip('+')}"
    )

    # Count-buckets, newest-first. Fewer than one full slice (<100) -> no table.
    if n >= RECENT_TRADES_SLICE_SIZE:
        slices = []
        hi = n
        while hi > 0 and len(slices) < RECENT_TRADES_N_SLICES:
            lo = max(0, hi - RECENT_TRADES_SLICE_SIZE)
            slices.append(trades[lo:hi])  # ascending chunk; slices[0]=newest
            hi = lo
        lines.append("")
        lines.append("Per 100-trade slice (newest first):")
        lines.append("  Slice    Span   Buy%(cnt)  Buy%(vol)    Net($)    MaxTrade")
        for si, chunk in enumerate(slices):
            cu = [t.amount * t.price for t in chunk]
            ctot = sum(cu) or 1e-9
            cbuy_usd = sum(u for u, t in zip(cu, chunk) if t.side == "buy")
            cbuy_cnt = sum(1 for t in chunk if t.side == "buy")
            cspan = (chunk[-1].timestamp - chunk[0].timestamp) / 1000
            cnet = cbuy_usd - (ctot - cbuy_usd)
            mi = max(range(len(chunk)), key=lambda k: cu[k])
            mside = "B" if chunk[mi].side == "buy" else "S"
            label = f"{si + 1}"
            if si == 0:
                label += " (new)"
            elif si == len(slices) - 1:
                label += " (old)"
            cnt_note = "" if len(chunk) == RECENT_TRADES_SLICE_SIZE else f" [{len(chunk)} tr]"
            lines.append(
                f"  {label:<8} {cspan:>4.1f}s   {cbuy_cnt / len(chunk) * 100:>4.0f}%      "
                f"{cbuy_usd / ctot * 100:>4.0f}%   {_fmt_money(cnet):>8}   "
                f"{_fmt_money(cu[mi]).lstrip('+')} {mside}{cnt_note}"
            )
    return "\n".join(lines)
```

- [ ] **Step 6: Rewrite the old recent-trades tests in `test_toolkit_iter2.py`**

The 6 tests at `tests/test_toolkit_iter2.py:154-243` (`test_recent_trades_typical`, `_empty_cold_market`, `_service_failure`, `_partial_coverage_double_condition`, `_all_taker_sell`, `_all_taker_buy`) assert the OLD time-bucket format + `window_seconds` signature. Replace them with the new behaviors (the canonical versions now live in `tests/test_recent_trades_buckets.py`). Delete the 6 obsolete tests and the `RECENT_TRADES_MAX_FETCH` import usage tied to `window_seconds`, replacing with two thin smoke checks that exercise the new format here too:

```python
@pytest.mark.asyncio
async def test_recent_trades_typical_new_format():
    from unittest.mock import AsyncMock, MagicMock
    from src.integrations.exchange.base import Trade
    from src.agent.tools_perception import get_recent_trades
    deps = MagicMock(); deps.symbol = "BTC/USDT:USDT"
    trades = [Trade(timestamp=1_000_000 + i * 1000, side="buy" if i % 2 else "sell",
                    price=64000.0, amount=0.01, trade_id=str(i)) for i in range(120)]
    deps.market_data.get_recent_trades = AsyncMock(return_value=trades)
    out = await get_recent_trades(deps)
    assert "Per 100-trade slice (newest first):" in out
    assert "by count" in out


@pytest.mark.asyncio
async def test_recent_trades_empty_new_format():
    from unittest.mock import AsyncMock, MagicMock
    from src.agent.tools_perception import get_recent_trades
    deps = MagicMock(); deps.symbol = "BTC/USDT:USDT"
    deps.market_data.get_recent_trades = AsyncMock(return_value=[])
    assert "No recent trades." in await get_recent_trades(deps)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_recent_trades_buckets.py tests/test_toolkit_iter2.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/agent/tools_perception.py tests/test_recent_trades_buckets.py tests/test_toolkit_iter2.py
git commit -m "feat(recent-trades): refactor get_recent_trades to fixed 5x100 count-buckets"
```

---

### Task B3: `get_recent_trades` wrapper + sweep remaining references

**Files:**
- Modify: `src/agent/trader.py` (`get_recent_trades` wrapper 390-402)
- Modify (as the sweep finds): `tests/test_iter_tool_opt_error_metadata.py`, `tests/test_display_cycle.py`, `tests/test_fact_only_wordlist.py`, `tests/test_market_data.py`, `tests/test_trader_agent.py`

- [ ] **Step 1: Update the wrapper (drop `window_seconds`, new docstring)**

In `src/agent/trader.py`, replace the `get_recent_trades` wrapper (390-402):

```python
    @tool
    async def get_recent_trades(ctx: RunContext[TradingDeps]) -> str:
        """Seconds-level tick micro-view: who is taking liquidity right now.

        The last ~500 trades grouped into 5 count-buckets of 100 (newest first),
        with the window's true time span in the header (typically tens of seconds).
        Use it for entry timing — is buy or sell pressure hitting the book this
        instant. For the minute-to-hours flow trend, use get_taker_flow instead.

        Returns:
            A trades micro-report. Example for get_recent_trades():
            === Recent Trades (BTC-USDT-SWAP · last 500 · 40.9s · @04:34 UTC) ===
            Taker buy:  40% by count · 49% by volume      Net: -$34.8K · 12.2 tr/s
            Largest single:  $168K SELL  (= 12.7% of window vol)
            Size (USD notional):  med $59 · mean $2.6K · p95 $9.7K
            Per 100-trade slice (newest first):
              Slice    Span   Buy%(cnt)  Buy%(vol)    Net($)    MaxTrade
              1 (new)  8.1s     44%        58%       +$12.1K    $168K S
              ... (older slices) ...
        """
        from src.agent.tools_perception import get_recent_trades as _impl

        return await _impl(ctx.deps)
```

- [ ] **Step 2: Sweep for stale `window_seconds` / old-format references**

Run: `grep -rn "get_recent_trades\|window_seconds\|RECENT_TRADES_WINDOW_DEFAULT\|RECENT_TRADES_BUCKET_COUNT" tests/ src/`
For each hit that (a) calls `get_recent_trades(..., window_seconds=...)`, or (b) asserts the old `"t-Nmin"` / `"buy ... / sell ..."` / `"Avg size"` time-bucket strings, or (c) imports the removed constants — update the call to drop `window_seconds` and update the assertion to the new format substrings (`"Per 100-trade slice"`, `"by count"`, `"by volume"`, `"Largest single"`). Likely files: `test_iter_tool_opt_error_metadata.py` (error-path string may have changed to `"Recent trades temporarily unavailable"`), `test_display_cycle.py`, `test_fact_only_wordlist.py`. `test_market_data.py:59-63` only checks `get_recent_trades` passthrough at the service layer (`limit=500`) and is unaffected by the tool refactor — leave it.

- [ ] **Step 3: Run the full test suite**

Run: `pytest -q`
Expected: PASS (whole suite). Fix any remaining stale assertions surfaced by the run.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(recent-trades): update wrapper (drop window_seconds) + sweep stale test refs"
```

---

## Self-Review

After all tasks, run this checklist (inline — fix issues as found):

**1. Spec coverage:**
- §2 rubik source → A1 (map), A2/A3 (fetch, unit=2, instId, ascending, in-progress kept), ⑤ fixtures.
- §3.1 signature/params/RVol-baseline-fixed-20/fetch n=max(limit+1,21) → A6 (`_TAKER_FLOW_RVOL_BARS`, `n` in A7), A8 (wrapper signature).
- §3.2 output (Now/Window/Per-bar/RVol(×20-bar)/CVD cross-call warning/Close join/1d整列降级/safety net/anchor/no-data-lag header) → A6 renderer + A7 + A8 docstring.
- §3.3 anchor ladder 5m→1h→4h→1d→1w, in-progress + formed% → `_TAKER_FLOW_ANCHOR`, A6 anchor block, A7 anchor fetch.
- §3.4 count-buckets (header span, count+vol buy%, Largest single, Size med/mean/p95, USD=amount×price, <500 degrade, <100 single aggregate) → B2.
- §3.5 errors (explicit reject no-Literal-no-clamp, partial-failure hierarchy, empty) → A7 + B2.
- §4.1 architecture (TakerFlowBar, fetch raw-no-detect, no-cache market_data, tool-layer detection, REGISTERED 19→20, 11-complete-stub blast radius — `IncompleteExchange` excluded; spec §4.1's "12" counts all 12 BaseExchange subclasses, but only 11 break) → A1-A8.
- §4.2 Option B adapter + 量纲护栏 + constants → B1, B2.
- §5 ①②③④⑤⑥ → A1(③), A2/A3(①⑤), A4(⑥ abstract+stubs), A6/A7(②), B1(④-adapter,⑤), B2(④-tool), A8(⑥ drift count).

**2. Placeholder scan:** No "TBD"/"handle errors"/"similar to". The B3 sweep step gives an exact grep command + concrete update rule (not a placeholder — it is a discovery step with a defined action). The awkward walrus line in A6 Step 1 has an explicit "use the clean call instead" note.

**3. Type/name consistency:** `TakerFlowBar(ts/sell_usd/buy_usd)`, `_TAKER_VOLUME_PERIOD` (exchange layer), `_TAKER_FLOW_PERIOD_MS` / `_TAKER_FLOW_ANCHOR` / `_TAKER_FLOW_RVOL_BARS` (tool layer), `fetch_taker_flow(symbol, period, limit)`, `get_taker_flow(deps, period, limit)`, `_render_taker_flow(bars, period, limit, *, now_ms, symbol, fetch_ts, closes, close_note, anchor)`, `_fmt_money`/`_fmt_scaled`/`_pick_usd_scale`/`_fmt_hhmm` — used consistently across A6/A7/A8. `RECENT_TRADES_SLICE_SIZE`/`RECENT_TRADES_N_SLICES`/`RECENT_TRADES_MAX_FETCH` consistent across B1/B2.

**Implementation-time probes (noted in spec §3.2):** before relying on the 1h Close column, confirm 1h rubik↔OHLCV ts alignment; and check whether an HKT-aligned OHLCV `bar` variant could rescue the 1d Close column (currently整列降级). Neither blocks this plan.
