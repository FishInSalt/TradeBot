# R2-Next-G Implementation Plan — `get_derivatives_data` OI history anchors + delta

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 24h/1h OI delta anchors to `get_derivatives_data` output, addressing principle 7 missing-window gap on the `Open Interest:` field and resolving sim #8 cross-cycle OI mental-math narrative (cycles `dc3d1b8a` need-expression + `e6929b2c` from-to delta).

**Architecture:** Add a new `fetch_open_interest_history` method to `BaseExchange` / `OKXExchange` / `SimulatedExchange` using the OKX raw rubik endpoint `publicGetRubikStatContractsOpenInterestHistory` (per-`instId`, matches single-point `fetchOpenInterest` USD value exactly). Plumb through `MarketDataService` with the existing `_derivatives_cache` + 180s TTL. Replace the OI render block in `get_derivatives_data` with a current + 1h-anchor + 24h-anchor + delta inline format. Delete the now-unused single-point OI methods + `OpenInterest` dataclass (grep verified — only `tools_perception.py:847` and `market_data.py:44` are production callers).

**Tech Stack:** pydantic-ai 0.x agent tools, ccxt-async / ccxtpro OKX driver, pytest-asyncio, `TTLCache` (`src/utils/cache.py`).

**Spec:** `docs/superpowers/specs/2026-05-13-iter-w2r2-next-g-oi-delta-design.md` (commit `4febeef`)

**Branch:** `iter-w2r2-next-g`

---

## Task 0: Read spec + verify branch state

**Files:** none — verification only

- [ ] **Step 1: Read spec end-to-end**

Run: `wc -l docs/superpowers/specs/2026-05-13-iter-w2r2-next-g-oi-delta-design.md`
Expected: ~620 lines.

Read the entire spec. Pay attention to:
- §2.1 `OpenInterestHistoryPoint` dataclass definition
- §2.2 OKX impl + `_OKX_OI_PERIOD` mapping
- §2.3 Simulated 3-guard pattern
- §2.5 render helpers + replacement block (lines 880-892 of `tools_perception.py`)
- §3.3 退化态 table (case A-D)
- §4.1 失败模式分类
- §5.2 21 unit tests
- §6.4 spec language constraints (do NOT promise improved decisions)

- [ ] **Step 2: Verify branch + clean tree**

Run:
```bash
git status && git branch --show-current
```
Expected:
```
nothing to commit, working tree clean
iter-w2r2-next-g
```

- [ ] **Step 3: Run baseline test suite — capture pass count**

Run: `pytest tests/ --tb=no -q 2>&1 | tail -5`
Expected: 1487+ passed (per memory `project_tradebot_status`). Note the exact count; the final task asserts no regression.

- [ ] **Step 4: Verify OKX raw endpoint accessibility (network smoke)**

Run:
```bash
python3 -c "
import asyncio, ccxt.async_support as ccxt
async def main():
    e = ccxt.okx()
    try:
        r = await e.public_get_rubik_stat_contracts_open_interest_history({
            'instId': 'BTC-USDT-SWAP', 'period': '1H', 'limit': '3'
        })
        print('OK', len(r.get('data', [])), 'records')
    finally:
        await e.close()
asyncio.run(main())
"
```
Expected: `OK 3 records`. If network unavailable: no formal record needed — the Task 11 Step 3 smoke and the Task 4 RateLimitExceeded test still cover their respective paths via mocks, and the manual sim is user-run only.

---

## Task 1: Add `OpenInterestHistoryPoint` dataclass + `_OKX_OI_PERIOD` constant

**Files:**
- Modify: `src/integrations/exchange/base.py` (add new dataclass near `OpenInterest` at L306; add `_OKX_OI_PERIOD` constant near top of module)
- Test: `tests/test_oi_history.py` (NEW — will be the home for all OI history tests across this PR)

- [ ] **Step 1: Write failing test**

Create `tests/test_oi_history.py`:

```python
"""Tests for OI history fetch + anchors + delta rendering.

Covers spec sections:
  §2.1 OpenInterestHistoryPoint + _OKX_OI_PERIOD
  §2.2/2.3 OKX + Simulated fetch_open_interest_history
  §2.4 MarketDataService.get_open_interest_history
  §2.5 render helpers + get_derivatives_data wire
  §5.2 19 unit tests + §5.3 simulated integration + §5.4 drift guard
"""
import pytest


def test_oi_history_point_dataclass_fields():
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    p = OpenInterestHistoryPoint(timestamp=1778644800000, open_interest=33174.25, open_interest_value=2693065783.51)
    assert p.timestamp == 1778644800000
    assert p.open_interest == pytest.approx(33174.25)
    assert p.open_interest_value == pytest.approx(2693065783.51)


def test_okx_oi_period_mapping():
    from src.integrations.exchange.base import _OKX_OI_PERIOD
    assert _OKX_OI_PERIOD == {"5m": "5m", "1h": "1H", "1d": "1D"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_oi_history.py -v`
Expected: 2 FAIL with `ImportError: cannot import name 'OpenInterestHistoryPoint'` / `_OKX_OI_PERIOD`.

- [ ] **Step 3: Implement dataclass + constant**

Modify `src/integrations/exchange/base.py`:

Near the top of the module (after existing imports), add:

```python
# OKX rubik stat endpoint requires uppercase '1H' / '1D'; project convention exposes
# lowercase across abstractions (matches fetch_ohlcv(timeframe='1h')). The mapping
# below is the only translation layer.
_OKX_OI_PERIOD = {"5m": "5m", "1h": "1H", "1d": "1D"}
```

Then, after the existing `OpenInterest` dataclass (around L312), add:

```python
@dataclass
class OpenInterestHistoryPoint:
    """One historical OI snapshot at a given timestamp.

    open_interest_value is USD-denominated and shares semantics with
    OpenInterest.open_interest_value (same single-contract scope, just a
    point in time).
    """
    timestamp: int
    open_interest: float  # base-currency amount
    open_interest_value: float  # USD value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_oi_history.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_oi_history.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-g): OpenInterestHistoryPoint dataclass + _OKX_OI_PERIOD

Adds the data-shape primitive consumed by fetch_open_interest_history
across the 3 exchange impls. Period mapping isolates OKX raw endpoint's
'1H'/'1D' casing from the project's lowercase abstractions.

_OKX_OI_PERIOD lives in base.py (not okx.py — resolves spec §8.2 OQ 3)
because simulated.py also depends on base and translates the same way;
hosting the mapping in base.py avoids a new sim→okx import edge.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `BaseExchange.fetch_open_interest_history` abstractmethod

**Files:**
- Modify: `src/integrations/exchange/base.py` (add abstractmethod near L133, alongside existing `fetch_open_interest`)
- Test: `tests/test_oi_history.py` (extend)

- [ ] **Step 1: Write failing test**

Append to `tests/test_oi_history.py`:

```python
def test_base_exchange_has_fetch_open_interest_history_abstractmethod():
    import inspect
    from src.integrations.exchange.base import BaseExchange
    assert hasattr(BaseExchange, "fetch_open_interest_history")
    method = BaseExchange.fetch_open_interest_history
    sig = inspect.signature(method)
    assert "symbol" in sig.parameters
    assert "period" in sig.parameters
    assert "limit" in sig.parameters
    assert sig.parameters["period"].default == "1h"
    assert sig.parameters["limit"].default == 26
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_oi_history.py::test_base_exchange_has_fetch_open_interest_history_abstractmethod -v`
Expected: FAIL with `AttributeError: type object 'BaseExchange' has no attribute 'fetch_open_interest_history'`.

- [ ] **Step 3: Add abstractmethod in base.py**

In `src/integrations/exchange/base.py`, locate the existing `fetch_open_interest` abstractmethod (L133). Add immediately after it:

```python
    @abstractmethod
    async def fetch_open_interest_history(
        self,
        symbol: str,
        period: Literal["5m", "1h", "1d"] = "1h",
        limit: int = 26,
    ) -> list["OpenInterestHistoryPoint"]: ...
```

If `Literal` isn't already imported in this file, add to the imports:

```python
from typing import Literal
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_oi_history.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_oi_history.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-g): BaseExchange.fetch_open_interest_history abstractmethod

ABC signature exposes lowercase period ('1h'/'1d') matching project
convention; per-impl translates to OKX-native uppercase via _OKX_OI_PERIOD.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Implement `OKXExchange.fetch_open_interest_history`

**Files:**
- Modify: `src/integrations/exchange/okx.py` (add method near existing `fetch_open_interest` at L726)
- Test: `tests/test_oi_history.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oi_history.py`:

```python
from unittest.mock import AsyncMock, MagicMock


def _okx_with_raw_response(data_rows):
    """Helper: build an OKXExchange instance with mocked _client raw response."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._client.public_get_rubik_stat_contracts_open_interest_history = AsyncMock(
        return_value={"code": "0", "data": data_rows, "msg": ""}
    )
    return ex


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_parses_raw_response():
    # Raw OKX returns newest-first; our wrapper must reverse to oldest-first.
    rows = [
        ["1778644800000", "3317425.09", "33174.25", "2693065783.51"],  # newest
        ["1778641200000", "3325484.92", "33254.85", "2693785781.05"],
        ["1778637600000", "3306756.78", "33067.57", "2677381762.06"],  # oldest
    ]
    ex = _okx_with_raw_response(rows)
    points = await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 3)
    assert len(points) == 3
    # After reverse: oldest first
    assert points[0].timestamp == 1778637600000
    assert points[-1].timestamp == 1778644800000
    assert points[-1].open_interest == pytest.approx(33174.25)
    assert points[-1].open_interest_value == pytest.approx(2693065783.51)


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_empty_data():
    ex = _okx_with_raw_response([])
    points = await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert points == []


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_period_mapping_1h_to_uppercase():
    ex = _okx_with_raw_response([])
    await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)
    called_args = ex._client.public_get_rubik_stat_contracts_open_interest_history.call_args
    assert called_args[0][0]["period"] == "1H"
    assert called_args[0][0]["instId"] == "BTC-USDT-SWAP"
    assert called_args[0][0]["limit"] == "26"


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_period_mapping_1d_to_uppercase():
    ex = _okx_with_raw_response([])
    await ex.fetch_open_interest_history("BTC/USDT:USDT", "1d", 5)
    called_args = ex._client.public_get_rubik_stat_contracts_open_interest_history.call_args
    assert called_args[0][0]["period"] == "1D"


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_missing_data_key():
    """Defensive: if raw response lacks 'data' key, treat as empty."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._client.public_get_rubik_stat_contracts_open_interest_history = AsyncMock(
        return_value={"code": "0", "msg": ""}
    )
    points = await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert points == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_oi_history.py -v`
Expected: 5 new tests FAIL with `AttributeError` / `TypeError: Can't instantiate abstract class OKXExchange with abstract method fetch_open_interest_history`.

- [ ] **Step 3: Implement OKXExchange.fetch_open_interest_history**

In `src/integrations/exchange/okx.py`, after the existing `fetch_open_interest` method (L737), add. Note the `@_retry()` decorator + inline try/except — this aligns with existing okx.py:712 / 725 / 738 derivatives-fetch pattern (see L703-710 comment explaining why RateLimitHit conversion must happen inside the decorated body to escape the retry decorator unmolested):

```python
    @_retry()
    async def fetch_open_interest_history(
        self,
        symbol: str,
        period: Literal["5m", "1h", "1d"] = "1h",
        limit: int = 26,
    ) -> list[OpenInterestHistoryPoint]:
        inst_id = self._client.market(symbol)["id"]  # BTC/USDT:USDT -> BTC-USDT-SWAP
        try:
            raw = await self._client.public_get_rubik_stat_contracts_open_interest_history({
                "instId": inst_id,
                "period": _OKX_OI_PERIOD[period],
                "limit": str(limit),
            })
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"OKX open interest history: {e}") from e
        rows = raw.get("data") or []
        # OKX rubik 4-col schema: [ts_ms, oi_contracts, oi_base, oi_usd].
        # r[1] (contract count) intentionally not consumed — agent uses USD anchor only.
        points = [
            OpenInterestHistoryPoint(
                timestamp=int(r[0]),
                open_interest=float(r[2]),        # oi_base (base-currency amount)
                open_interest_value=float(r[3]),  # oi_usd (USD value)
            )
            for r in rows
        ]
        points.reverse()  # OKX returns newest-first; flip to oldest-first
        return points
```

Update imports at the top of `okx.py`:

```python
from src.integrations.exchange.base import (
    ..., OpenInterestHistoryPoint, _OKX_OI_PERIOD,  # add these
)
from typing import Literal  # if not already imported
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_oi_history.py -v`
Expected: 8 PASS (3 prior + 5 new).

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_oi_history.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-g): OKXExchange.fetch_open_interest_history via raw rubik endpoint

Uses publicGetRubikStatContractsOpenInterestHistory (per-instId) to avoid
ccxt unified API's currency-aggregate semantics. r[1] (contract count)
discarded; agent consumes USD anchor only. Period mapping isolates OKX
'1H'/'1D' casing from project convention.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Implement `SimulatedExchange.fetch_open_interest_history`

**Files:**
- Modify: `src/integrations/exchange/simulated.py` (add method near existing `fetch_open_interest` at L1011)
- Test: `tests/test_oi_history.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oi_history.py`:

```python
@pytest.mark.asyncio
async def test_simulated_fetch_oi_history_validates_symbol():
    """Guard 1: invalid symbol must raise ValueError before any network call."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._symbol = "BTC/USDT:USDT"
    ex._ccxt = MagicMock()  # would explode if called
    with pytest.raises(ValueError):
        await ex.fetch_open_interest_history("WRONG/SYMBOL", "1h", 26)


@pytest.mark.asyncio
async def test_simulated_fetch_oi_history_requires_started():
    """Guard 2: must raise RuntimeError if start() has not been called."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._symbol = "BTC/USDT:USDT"
    # _ccxt intentionally not set
    with pytest.raises(RuntimeError, match="Exchange not started"):
        await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)


@pytest.mark.asyncio
async def test_simulated_fetch_oi_history_wraps_rate_limit():
    """Guard 3: ccxt.RateLimitExceeded must be re-raised as RateLimitHit."""
    import ccxt
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.utils.cache import RateLimitHit
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._symbol = "BTC/USDT:USDT"
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._ccxt.public_get_rubik_stat_contracts_open_interest_history = AsyncMock(
        side_effect=ccxt.RateLimitExceeded("429 too many")
    )
    with pytest.raises(RateLimitHit, match="Sim open interest history"):
        await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)


@pytest.mark.asyncio
async def test_simulated_fetch_oi_history_parses_raw():
    """Happy path: raw response parsed, reversed, returned."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._symbol = "BTC/USDT:USDT"
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._ccxt.public_get_rubik_stat_contracts_open_interest_history = AsyncMock(
        return_value={"code": "0", "data": [
            ["1778644800000", "3317425.09", "33174.25", "2693065783.51"],
            ["1778641200000", "3325484.92", "33254.85", "2693785781.05"],
        ], "msg": ""}
    )
    points = await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert len(points) == 2
    assert points[0].timestamp == 1778641200000  # oldest first after reverse
    assert points[-1].open_interest_value == pytest.approx(2693065783.51)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_oi_history.py -v`
Expected: 4 new tests FAIL.

- [ ] **Step 3: Implement SimulatedExchange.fetch_open_interest_history**

In `src/integrations/exchange/simulated.py`, after `fetch_open_interest` (L1024), add:

```python
    async def fetch_open_interest_history(
        self,
        symbol: str,
        period: Literal["5m", "1h", "1d"] = "1h",
        limit: int = 26,
    ) -> list[OpenInterestHistoryPoint]:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            raw = await self._ccxt.public_get_rubik_stat_contracts_open_interest_history({
                "instId": self._ccxt.market(symbol)["id"],
                "period": _OKX_OI_PERIOD[period],
                "limit": str(limit),
            })
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim open interest history: {e}") from e
        rows = raw.get("data") or []
        points = [
            OpenInterestHistoryPoint(
                timestamp=int(r[0]),
                open_interest=float(r[2]),
                open_interest_value=float(r[3]),
            )
            for r in rows
        ]
        points.reverse()
        return points
```

Update imports at the top of `simulated.py`:

```python
from src.integrations.exchange.base import (
    ..., OpenInterestHistoryPoint, _OKX_OI_PERIOD,  # add these
)
from typing import Literal  # if not already imported
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_oi_history.py -v`
Expected: 12 PASS (8 prior + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_oi_history.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-g): SimulatedExchange.fetch_open_interest_history with 3 guards

3 guards mirror existing simulated.fetch_open_interest pattern:
  1. _validate_symbol — symbol whitelist
  2. hasattr(self, '_ccxt') — start() precondition
  3. ccxt.RateLimitExceeded → RateLimitHit wrapping

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add `MarketDataService.get_open_interest_history`

**Files:**
- Modify: `src/integrations/market_data.py` (add method after existing `get_open_interest` at L41)
- Test: `tests/test_oi_history.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oi_history.py`:

```python
@pytest.mark.asyncio
async def test_market_data_get_oi_history_delegates_first_call():
    from src.integrations.market_data import MarketDataService
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    exchange = AsyncMock()
    exchange.fetch_open_interest_history.return_value = [
        OpenInterestHistoryPoint(1, 100.0, 1_000_000.0),
        OpenInterestHistoryPoint(2, 101.0, 1_010_000.0),
    ]
    svc = MarketDataService(exchange)
    points = await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert len(points) == 2
    exchange.fetch_open_interest_history.assert_called_once_with("BTC/USDT:USDT", "1h", 26)


@pytest.mark.asyncio
async def test_market_data_get_oi_history_cache_hit_skips_exchange():
    """Second call within TTL must not invoke exchange again."""
    from src.integrations.market_data import MarketDataService
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    exchange = AsyncMock()
    exchange.fetch_open_interest_history.return_value = [OpenInterestHistoryPoint(1, 100.0, 1_000_000.0)]
    svc = MarketDataService(exchange)
    await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 26)
    await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert exchange.fetch_open_interest_history.call_count == 1


@pytest.mark.asyncio
async def test_market_data_get_oi_history_distinct_keys_per_args():
    """Different (period, limit) tuples must not share cache."""
    from src.integrations.market_data import MarketDataService
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    exchange = AsyncMock()
    exchange.fetch_open_interest_history.return_value = [OpenInterestHistoryPoint(1, 100.0, 1_000_000.0)]
    svc = MarketDataService(exchange)
    await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 26)
    await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 5)
    assert exchange.fetch_open_interest_history.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_oi_history.py -v -k market_data`
Expected: 3 FAIL with `AttributeError: ... has no attribute 'get_open_interest_history'`.

- [ ] **Step 3: Implement get_open_interest_history**

In `src/integrations/market_data.py`, after `get_open_interest` (L41-45), add:

```python
    async def get_open_interest_history(
        self,
        symbol: str,
        period: Literal["5m", "1h", "1d"] = "1h",
        limit: int = 26,
    ) -> list[OpenInterestHistoryPoint]:
        return await self._derivatives_cache.get_or_fetch(
            f"oi_history:{symbol}:{period}:{limit}", _DERIVATIVES_TTL,
            lambda: self._exchange.fetch_open_interest_history(symbol, period, limit),
        )
```

Note `Literal["5m","1h","1d"]` keeps the service-layer signature in sync with `BaseExchange.fetch_open_interest_history` (Task 2). Mismatched relaxation to `str` would let upper callers pass invalid periods and only fail at the ABC boundary.

Update imports at top of `market_data.py`:

```python
from src.integrations.exchange.base import BaseExchange, FundingRate, LongShortRatio, OpenInterest, OpenInterestHistoryPoint, OrderBook, Ticker, Trade
from typing import Literal  # if not already imported
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_oi_history.py -v -k market_data`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/market_data.py tests/test_oi_history.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-g): MarketDataService.get_open_interest_history with 180s cache

Reuses existing _derivatives_cache + _DERIVATIVES_TTL. Cache key includes
full args (symbol/period/limit) so distinct windows don't collide.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Render helpers `_format_oi_usd` + `_derive_oi_anchors`

**Files:**
- Modify: `src/agent/tools_perception.py` (add module-scope helpers above `get_derivatives_data`)
- Test: `tests/test_oi_history.py` (extend with 8 render cases)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oi_history.py`:

```python
def _make_points(values_usd):
    """Helper: build N points with monotonic timestamps and given USD values.
    Returns oldest-first to match exchange.fetch_open_interest_history convention."""
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    return [
        OpenInterestHistoryPoint(timestamp=i, open_interest=v / 80000.0, open_interest_value=v)
        for i, v in enumerate(values_usd)
    ]


def test_format_oi_usd_billion_scale():
    from src.agent.tools_perception import _format_oi_usd
    assert _format_oi_usd(2_920_000_000.0) == "$2.92B"


def test_format_oi_usd_million_scale():
    from src.agent.tools_perception import _format_oi_usd
    assert _format_oi_usd(850_000_000.0) == "$850.00M"


def test_format_oi_usd_below_million():
    from src.agent.tools_perception import _format_oi_usd
    assert _format_oi_usd(123_456.0) == "$123,456"


def test_oi_render_happy_path_inline_26_records():
    """26 records: 1h anchor = points[-2], 24h anchor = points[-25].
    Current $2.92B; 1h-ago $2.93B (-0.34%); 24h-ago $2.91B (+0.34%)."""
    from src.agent.tools_perception import _derive_oi_anchors
    # Build 26 records, oldest first. Index 0..23 don't matter; -25=$2.91B; -2=$2.93B; -1=$2.92B.
    vals = [2_900_000_000.0] * 26
    vals[-25] = 2_910_000_000.0   # 24h ago
    vals[-2] = 2_930_000_000.0    # 1h ago
    vals[-1] = 2_920_000_000.0    # current
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "1h ago $2.93B, -0.3%" in result
    assert "24h ago $2.91B, +0.3%" in result
    assert "; " in result


def test_oi_render_positive_deltas():
    from src.agent.tools_perception import _derive_oi_anchors
    vals = [2_500_000_000.0] * 26
    vals[-1] = 2_920_000_000.0
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "24h ago $2.50B, +16.8%" in result


def test_oi_render_zero_delta_when_anchors_equal_current():
    from src.agent.tools_perception import _derive_oi_anchors
    vals = [2_920_000_000.0] * 26
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "+0.0%" in result


def test_oi_render_exactly_25_records():
    """24h-anchor minimum boundary: len(points)=25, points[-25]=points[0] available."""
    from src.agent.tools_perception import _derive_oi_anchors
    # len = 1 + 22 + 2 = 25; vals[-25]=vals[0]=$2.91B; vals[-2]=$2.93B; vals[-1]=$2.92B (current)
    vals = [2_910_000_000.0] + [2_900_000_000.0] * 22 + [2_930_000_000.0, 2_920_000_000.0]
    assert len(vals) == 25  # tripwire — guard the 24h-anchor index math
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "1h ago" in result
    assert "24h ago $2.91B" in result


def test_oi_render_exactly_2_records():
    """1h-anchor minimum boundary: only 1h shown, no 24h."""
    from src.agent.tools_perception import _derive_oi_anchors
    points = _make_points([2_930_000_000.0, 2_920_000_000.0])
    result = _derive_oi_anchors(points, points[-1])
    assert "1h ago $2.93B" in result
    assert "24h ago" not in result


def test_oi_render_1_record():
    """Below 1h anchor boundary: empty string."""
    from src.agent.tools_perception import _derive_oi_anchors
    points = _make_points([2_920_000_000.0])
    result = _derive_oi_anchors(points, points[-1])
    assert result == ""


def test_oi_render_anchor_zero_skipped():
    """Defensive: anchor with open_interest_value <= 0 must be skipped (div-by-zero)."""
    from src.agent.tools_perception import _derive_oi_anchors
    # len = 1 + 22 + 2 = 25; vals[-25]=vals[0]=0 (24h-ago zero) → skip 24h fragment
    vals = [0.0] + [2_900_000_000.0] * 22 + [2_930_000_000.0, 2_920_000_000.0]
    assert len(vals) == 25 and vals[-25] == 0.0  # tripwire — guard zero placement
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "1h ago" in result
    assert "24h ago" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_oi_history.py -v -k "format_oi_usd or oi_render"`
Expected: 11 FAIL with `ImportError: cannot import name '_format_oi_usd' / '_derive_oi_anchors'`.

- [ ] **Step 3: Implement helpers**

In `src/agent/tools_perception.py`, locate the existing `get_derivatives_data` (around L831). Add at module scope **before** the function:

```python
def _format_oi_usd(v: float) -> str:
    """Format OI USD value with auto-scale unit (B / M / raw)."""
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


def _derive_oi_anchors(
    points: list[OpenInterestHistoryPoint],
    current: OpenInterestHistoryPoint,
) -> str:
    """Render '1h ago $X.XXB, +Y.Y%; 24h ago $X.XXB, -Y.Y%' fragments.

    Anchor indices measured from end (points[-1] = current). Partial-history
    degrades gracefully: insufficient or zero-value anchors are skipped.
    """
    fragments: list[str] = []
    for label, idx_from_end in [("1h ago", 2), ("24h ago", 25)]:
        if len(points) < idx_from_end:
            continue
        anchor = points[-idx_from_end]
        if anchor.open_interest_value <= 0:
            continue
        delta_pct = (current.open_interest_value / anchor.open_interest_value - 1) * 100
        fragments.append(
            f"{label} {_format_oi_usd(anchor.open_interest_value)}, {delta_pct:+.1f}%"
        )
    return "; ".join(fragments)
```

Update imports at the top of `tools_perception.py` to include `OpenInterestHistoryPoint`:

```python
from src.integrations.exchange.base import (
    ..., OpenInterestHistoryPoint,  # add this
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_oi_history.py -v -k "format_oi_usd or oi_render"`
Expected: 11 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_oi_history.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-g): _format_oi_usd + _derive_oi_anchors render helpers

Module-scope helpers prepared for get_derivatives_data wire (next task).
Auto-scale USD formatting (B/M/raw) and graceful partial-history
degradation (insufficient or zero-value anchors are silently skipped per
principle 1 fact-only).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire `get_derivatives_data` + update trader.py docstring + per-field fallback tests

**Files:**
- Modify: `src/agent/tools_perception.py:831-913` (replace OI fetch + render block)
- Modify: `src/agent/trader.py:271-287` (docstring)
- Test: `tests/test_oi_history.py` (extend with 7 failure path cases)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_oi_history.py`:

```python
def _async_mock(value):
    """Build AsyncMock: raise if value is Exception, else return value."""
    if isinstance(value, Exception):
        return AsyncMock(side_effect=value)
    return AsyncMock(return_value=value)


def _mock_deps_for_derivs(oi_hist_value, funding_value=None, lsr_value=None):
    """Build a minimal TradingDeps mock for get_derivatives_data tests.

    Each *_value: either the success payload (e.g., list[OpenInterestHistoryPoint],
    FundingRate, LongShortRatio) OR an Exception instance (raised by the AsyncMock).
    funding_value / lsr_value default to a sane stub if None.
    """
    from src.integrations.exchange.base import FundingRate, LongShortRatio
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()

    if funding_value is None:
        funding_value = FundingRate(
            symbol="BTC/USDT:USDT", rate=0.000014,
            next_funding_time=1778660000000, timestamp=1778645000000,
        )
    if lsr_value is None:
        lsr_value = LongShortRatio(
            symbol="BTC/USDT:USDT", long_short_ratio=0.66,
            long_ratio=0.399, short_ratio=0.601, timestamp=1778645000000,
        )
    deps.market_data.get_funding_rate = _async_mock(funding_value)
    deps.market_data.get_open_interest_history = _async_mock(oi_hist_value)
    deps.market_data.get_long_short_ratio = _async_mock(lsr_value)
    return deps


@pytest.mark.asyncio
async def test_derivs_oi_history_happy_full_anchors():
    from src.agent.tools_perception import get_derivatives_data
    vals = [2_900_000_000.0] * 26
    vals[-25] = 2_910_000_000.0
    vals[-2] = 2_930_000_000.0
    vals[-1] = 2_920_000_000.0
    deps = _mock_deps_for_derivs(_make_points(vals))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: $2.92B (1h ago $2.93B" in out
    assert "24h ago $2.91B" in out
    assert "Funding Rate:" in out
    assert "Long/Short Ratio:" in out


@pytest.mark.asyncio
async def test_derivs_oi_history_rate_limit():
    from src.agent.tools_perception import get_derivatives_data
    from src.utils.cache import RateLimitHit
    deps = _mock_deps_for_derivs(RateLimitHit("429"))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: (unavailable)" in out
    assert "Funding Rate:" in out  # other fields still rendered
    assert "Long/Short Ratio:" in out


@pytest.mark.asyncio
async def test_derivs_oi_history_empty_list():
    from src.agent.tools_perception import get_derivatives_data
    deps = _mock_deps_for_derivs([])
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: (unavailable)" in out


@pytest.mark.asyncio
async def test_derivs_oi_history_one_record_no_anchor():
    from src.agent.tools_perception import get_derivatives_data
    deps = _mock_deps_for_derivs(_make_points([2_920_000_000.0]))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: $2.92B\n" in out  # single-point form (no anchor paren)
    assert "1h ago" not in out
    assert "24h ago" not in out


@pytest.mark.asyncio
async def test_derivs_oi_history_two_records_1h_only():
    from src.agent.tools_perception import get_derivatives_data
    deps = _mock_deps_for_derivs(_make_points([2_930_000_000.0, 2_920_000_000.0]))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: $2.92B (1h ago $2.93B" in out
    assert "24h ago" not in out


@pytest.mark.asyncio
async def test_derivs_oi_history_anchor_zero_skipped():
    """points[-25].open_interest_value=0 — 24h anchor skipped, 1h preserved."""
    from src.agent.tools_perception import get_derivatives_data
    # len = 25; vals[-25]=vals[0]=0 (24h-ago zero)
    vals = [0.0] + [2_900_000_000.0] * 22 + [2_930_000_000.0, 2_920_000_000.0]
    deps = _mock_deps_for_derivs(_make_points(vals))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "1h ago $2.93B" in out
    assert "24h ago" not in out


@pytest.mark.asyncio
async def test_derivs_all_three_sources_fail_single_error_line():
    """R2-8c L2 全失败 fallback: single Error: line."""
    from src.agent.tools_perception import get_derivatives_data
    from src.utils.cache import RateLimitHit
    deps = _mock_deps_for_derivs(
        oi_hist_value=RateLimitHit("oi"),
        funding_value=RateLimitHit("funding"),
        lsr_value=RateLimitHit("lsr"),
    )
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Error: Temporarily unavailable" in out
    assert "Open Interest:" not in out  # per-field lines suppressed by L2


@pytest.mark.asyncio
async def test_derivs_oi_history_fail_others_ok():
    """OI fails alone → only OI line gets (unavailable); other two intact."""
    from src.agent.tools_perception import get_derivatives_data
    from src.utils.cache import RateLimitHit
    deps = _mock_deps_for_derivs(RateLimitHit("oi only"))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: (unavailable)" in out
    assert "Funding Rate:" in out
    assert "longs pay shorts" in out or "shorts pay longs" in out
    assert "Long/Short Ratio:" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_oi_history.py -v -k derivs`
Expected: 8 FAIL — `AttributeError` on `get_open_interest_history` mock OR mismatched output content.

- [ ] **Step 3: Replace OI fetch + render in get_derivatives_data**

In `src/agent/tools_perception.py:831-913`, locate the `get_derivatives_data` function. Change the `asyncio.gather` call (around L845-850):

```python
    funding, oi_hist, lsr = await asyncio.gather(
        deps.market_data.get_funding_rate(symbol),
        deps.market_data.get_open_interest_history(symbol, "1h", 26),
        deps.market_data.get_long_short_ratio(symbol),
        return_exceptions=True,
    )
```

Change the all-3-failed guard (around L853-861) to test `oi_hist`:

```python
    if (
        isinstance(funding, Exception)
        and isinstance(oi_hist, Exception)
        and isinstance(lsr, Exception)
    ):
        return (
            f"=== Derivatives Data ({symbol}) ===\n"
            f"Error: Temporarily unavailable (all 3 data sources failed)."
        )
```

Replace the OI render block (L880-892) entirely:

```python
    # Open interest history (replaces single-point fetch — see spec §2.5).
    if isinstance(oi_hist, Exception) or not oi_hist:
        field_lines.append("Open Interest: (unavailable)")
    else:
        current = oi_hist[-1]  # newest, after .reverse() in fetch
        oi_str = _format_oi_usd(current.open_interest_value)
        anchors = _derive_oi_anchors(oi_hist, current)
        if anchors:
            field_lines.append(f"Open Interest: {oi_str} ({anchors})")
        else:
            field_lines.append(f"Open Interest: {oi_str}")
        if current.timestamp:
            timestamps_ms.append(current.timestamp)
```

- [ ] **Step 4: Update trader.py docstring**

In `src/agent/trader.py:271-287`, replace the `get_derivatives_data` @tool docstring with:

```python
        """Get derivatives market data: funding rate, open interest (with 1h/24h
        anchors and percent change), and long/short ratio.

        Positive funding rate means longs pay shorts; negative means shorts pay
        longs (settlement interval varies by contract — see next settlement time
        in output). Open interest is total outstanding contracts in USD, rendered
        with anchor values from 1h ago and 24h ago and the percent change to
        the current value. Anchor labels correspond to OKX 1H-bar boundaries
        and may differ from wall-clock 1h/24h offsets by 0-60 minutes when the
        latest bar is still in progress. Long/short ratio is the ratio of long
        vs short account positions. Output ~180-260 tokens.

        Args:
            symbol: trading symbol; None uses the currently traded pair.
        """
```

- [ ] **Step 5: Run new tests to verify they pass**

Run: `pytest tests/test_oi_history.py -v -k derivs`
Expected: 8 PASS.

- [ ] **Step 6: Run FULL OI history test file**

Run: `pytest tests/test_oi_history.py -v`
Expected: 30+ PASS (all so far).

- [ ] **Step 7: Commit**

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_oi_history.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-g): wire get_derivatives_data to OI history + anchor render

Replaces single-point get_open_interest fetch with get_open_interest_history,
renders 1h/24h anchor inline alongside current value. Per-field fallback,
all-3-fail L2 path, and trader.py docstring all aligned with spec §2.5-2.6
+ §4.1. Docstring acknowledges OKX 1H-bar boundary trust edge (0-60min).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Migrate existing test mocks (mass-update phase)

**Files (test mocks affected by API change)**:
- Modify: `tests/test_news_tools.py` (4 mock points)
- Modify: `tests/test_display_cycle.py` (1 side_effect)
- Modify: `tests/test_fact_only_wordlist.py` (1 AsyncMock)

These tests currently mock `market_data.get_open_interest`; after Task 7 they break because `get_derivatives_data` now calls `get_open_interest_history`.

- [ ] **Step 1: Verify these tests now fail**

Run: `pytest tests/test_news_tools.py tests/test_display_cycle.py tests/test_fact_only_wordlist.py -v 2>&1 | grep -E "FAIL|ERROR" | head`
Expected: failures relating to `get_open_interest` mock not being invoked OR `get_open_interest_history` not being mocked.

- [ ] **Step 2: Update tests/test_news_tools.py mocks**

Find the 4 sites:

```bash
grep -n "get_open_interest" tests/test_news_tools.py
```
Expected: 4 hits around L324 / L358 / L377 / L397.

For each, replace the `market_data.get_open_interest.return_value = OpenInterest(...)` style with:

```python
from src.integrations.exchange.base import OpenInterestHistoryPoint

# Was:
#   market_data.get_open_interest.return_value = OpenInterest(
#       "BTC/USDT:USDT", 12345.0, 100_000_000.0, 0
#   )
# Now:
market_data.get_open_interest_history.return_value = [
    OpenInterestHistoryPoint(timestamp=1778640000000, open_interest=12345.0, open_interest_value=100_000_000.0),
]
```

Apply the same shape transformation to all 4 sites. The test assertions for the rendered output may need updating if they assert a specific OI-line substring — adjust to match new format (`$100.00M` instead of `$100M` etc., depending on what those tests assert).

Also update the import line at the top of `test_news_tools.py`:

```python
from src.integrations.exchange.base import FundingRate, LongShortRatio, OpenInterestHistoryPoint, Ticker
```

(Remove `OpenInterest` from the import since these tests no longer use it.)

- [ ] **Step 3: Update tests/test_display_cycle.py mock**

Find the site:
```bash
grep -n "get_open_interest" tests/test_display_cycle.py
```
Expected: 1 hit at L2968.

Replace:
```python
# Was:
#   market_data.get_open_interest.side_effect = Exception()
# Now:
market_data.get_open_interest_history.side_effect = Exception()
```

- [ ] **Step 4: Update tests/test_fact_only_wordlist.py mock**

Find the site:
```bash
grep -n "get_open_interest" tests/test_fact_only_wordlist.py
```
Expected: 1 hit at L539.

Replace:
```python
# Was:
#   deps.market_data.get_open_interest = AsyncMock(side_effect=Exception("down"))
# Now:
deps.market_data.get_open_interest_history = AsyncMock(side_effect=Exception("down"))
```

- [ ] **Step 5: Run affected tests**

Run: `pytest tests/test_news_tools.py tests/test_display_cycle.py tests/test_fact_only_wordlist.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_news_tools.py tests/test_display_cycle.py tests/test_fact_only_wordlist.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-next-g): migrate downstream mocks to get_open_interest_history

3 test files mocked the now-replaced get_open_interest. Updated to mock
get_open_interest_history returning list[OpenInterestHistoryPoint] so
get_derivatives_data wiring tests pass.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Remove unused single-point OI (`get_open_interest` / `fetch_open_interest` / `OpenInterest` dataclass)

**Files:**
- Modify: `src/integrations/market_data.py` (delete `get_open_interest` L41-45)
- Modify: `src/integrations/exchange/base.py` (delete `fetch_open_interest` abstractmethod L133, delete `OpenInterest` dataclass L306)
- Modify: `src/integrations/exchange/okx.py` (delete `fetch_open_interest` L726-737)
- Modify: `src/integrations/exchange/simulated.py` (delete `fetch_open_interest` L1011-1024)
- Modify: `tests/test_derivatives_data.py` (delete obsolete tests, keep test file for remaining funding/lsr coverage)
- Modify: `tests/test_exchange.py` (6 abstract stub removals)
- Modify: `tests/test_price_level_alert.py` (1 abstract stub removal)
- Modify: `tests/test_tool_enhancement.py` (2 abstract stub removals)

Pre-flight: confirm no remaining production caller exists.

- [ ] **Step 1: Re-grep to confirm safety**

Run:
```bash
grep -rn "get_open_interest\b\|fetch_open_interest\b\|\bOpenInterest\b" src/ 2>/dev/null
```
Expected: only the 4 definition sites (`base.py:133, 306` / `market_data.py:41,44 + import` / `okx.py:726-737 + import` / `simulated.py:1011-1024 + import`). NO production caller in `tools_perception.py` or elsewhere. If a caller appears, STOP and reopen retention scope decision.

- [ ] **Step 2: Delete from src/integrations/market_data.py**

Delete the `get_open_interest` method (L41-45). Remove `OpenInterest` from the import line at top.

After:
```python
from src.integrations.exchange.base import BaseExchange, FundingRate, LongShortRatio, OpenInterestHistoryPoint, OrderBook, Ticker, Trade
```

- [ ] **Step 3: Delete from src/integrations/exchange/base.py**

Delete the `fetch_open_interest` abstractmethod (L133) and the `OpenInterest` dataclass (around L306-310 — include the `@dataclass` decorator line).

- [ ] **Step 4: Delete from src/integrations/exchange/okx.py**

Delete the `fetch_open_interest` method (L726-737). Remove `OpenInterest` from the import at the top.

- [ ] **Step 5: Delete from src/integrations/exchange/simulated.py**

Delete the `fetch_open_interest` method (L1011-1024). Remove `OpenInterest` from the import at the top.

- [ ] **Step 6: Update tests/test_exchange.py — remove 6 abstract stubs**

Run:
```bash
grep -n "fetch_open_interest" tests/test_exchange.py
```
Expected: 6 hits (L250, 285, 321, 357, 392, 426 per pre-flight grep).

For each, delete the line `async def fetch_open_interest(self, symbol): ...` (it was a no-op stub for the abstract method, now unneeded).

- [ ] **Step 7: Update tests/test_price_level_alert.py + test_tool_enhancement.py — abstract stubs**

```bash
grep -n "fetch_open_interest" tests/test_price_level_alert.py tests/test_tool_enhancement.py
```
Expected: 1 hit in `test_price_level_alert.py:27`; 2 hits in `test_tool_enhancement.py:56,104`.

Delete each `async def fetch_open_interest(self, symbol): ...` stub.

- [ ] **Step 8: Update tests/test_derivatives_data.py — remove obsolete tests**

Delete from `tests/test_derivatives_data.py`:
- The `OpenInterest` import on L5 (replace to `from src.integrations.exchange.base import FundingRate, LongShortRatio`)
- **5 test functions** (any test that references `OpenInterest` dataclass or `*_open_interest` method):
  1. `test_open_interest_fields` (L19-24) — dataclass smoke test, breaks on `OpenInterest` deletion
  2. `test_okx_fetch_open_interest` (L56-68) — directly tests deleted method
  3. `test_okx_open_interest_converts_rate_limit_exceeded` (L121-131) — directly tests deleted method's rate-limit wrap
  4. `test_sim_fetch_open_interest` (L190-199) — directly tests deleted method
  5. `test_market_data_get_open_interest` (L257-266) — directly tests deleted service method
- Any helper that constructs `OpenInterest(...)` only for those tests.

Verify after deletion:
```bash
grep -nE "OpenInterest\b|fetch_open_interest\b|get_open_interest\b" tests/test_derivatives_data.py
```
Expected: 0 hits.

Keep all funding rate + long/short ratio tests intact.

- [ ] **Step 9: Re-grep to verify clean**

Run:
```bash
grep -rn "get_open_interest\b\|fetch_open_interest\b\|\bOpenInterest\b" src/ tests/ 2>/dev/null
```
Expected: **0 hits** (or only string-literal mentions in comments/spec references). If anything remains, address.

- [ ] **Step 10: Run full test suite**

Run: `pytest tests/ --tb=short -q 2>&1 | tail -20`
Expected: all PASS; total count should be roughly (baseline from Task 0 Step 3) − (deleted obsolete tests) + (~30 new OI history tests). No failures, no errors.

- [ ] **Step 11: Commit**

```bash
git add src/integrations/market_data.py src/integrations/exchange/base.py src/integrations/exchange/okx.py src/integrations/exchange/simulated.py tests/test_derivatives_data.py tests/test_exchange.py tests/test_price_level_alert.py tests/test_tool_enhancement.py
git commit -m "$(cat <<'EOF'
refactor(iter-w2r2-next-g): remove unused single-point OI methods + dataclass

After Task 7 wire-up, get_open_interest / fetch_open_interest / OpenInterest
have zero production callers (verified by grep). Removing per CLAUDE.md
"if you are certain that something is unused, you can delete it completely"
and principle 3 (no dual signal source).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: T-DG-OI-1 drift guard

**Files:**
- Test: `tests/test_drift_oi_history.py` (NEW — matches project convention `test_drift_*.py` per-domain file)

- [ ] **Step 1: Write the drift guard test**

Create `tests/test_drift_oi_history.py`:

```python
"""Drift guard for OI history anchor render format (T-DG-OI-1).

Spec §5.4 — assert that a snapshot of get_derivatives_data output containing
a happy-path OI line includes the exact anchor substrings '(1h ago ' and
'24h ago '. Prevents accidental regression of the anchor inline format
during future R2-8c-style sectioning refactors.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_oi_anchor_format_drift_guard():
    from src.agent.tools_perception import get_derivatives_data
    from src.integrations.exchange.base import (
        FundingRate, LongShortRatio, OpenInterestHistoryPoint,
    )

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_funding_rate = AsyncMock(return_value=FundingRate(
        symbol="BTC/USDT:USDT", rate=0.000014,
        next_funding_time=1778660000000, timestamp=1778645000000,
    ))
    deps.market_data.get_long_short_ratio = AsyncMock(return_value=LongShortRatio(
        symbol="BTC/USDT:USDT", long_short_ratio=0.66,
        long_ratio=0.399, short_ratio=0.601, timestamp=1778645000000,
    ))
    # 26-record OI history with distinct anchors.
    vals = [2_900_000_000.0] * 26
    vals[-25] = 2_910_000_000.0
    vals[-2] = 2_930_000_000.0
    vals[-1] = 2_920_000_000.0
    points = [
        OpenInterestHistoryPoint(timestamp=1778640000000 + i * 3600000,
                                 open_interest=v / 80000.0, open_interest_value=v)
        for i, v in enumerate(vals)
    ]
    deps.market_data.get_open_interest_history = AsyncMock(return_value=points)

    out = await get_derivatives_data(deps, "BTC/USDT:USDT")

    # Drift guard: exact anchor substrings — refactors must not silently
    # drop the inline anchor format (e.g., move to sub-line, change "ago"
    # to "back", reorder windows, etc.).
    assert "(1h ago " in out, "1h-anchor inline format dropped — refactor regression?"
    assert "24h ago " in out, "24h-anchor inline format dropped — refactor regression?"
    # Both must appear in the SAME line as the current OI value (inline form).
    oi_line = [ln for ln in out.splitlines() if ln.startswith("Open Interest:")][0]
    assert "1h ago" in oi_line and "24h ago" in oi_line
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_drift_oi_history.py -v`
Expected: 1 PASS.

- [ ] **Step 3: Sanity check — simulate a regression**

Temporarily comment out the line in `tools_perception.py` that renders the anchor (`field_lines.append(f"Open Interest: {oi_str} ({anchors})")`) and replace with just `field_lines.append(f"Open Interest: {oi_str}")`. Re-run:

```bash
pytest tests/test_drift_oi_history.py -v
```
Expected: FAIL with assertion message about "1h-anchor inline format dropped".

Undo the temporary change.

Re-run to confirm green:
```bash
pytest tests/test_drift_oi_history.py -v
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_drift_oi_history.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-next-g): T-DG-OI-1 drift guard for OI anchor inline format

Prevents future sectioning refactors from silently dropping the
'(1h ago $X, ±Y%; 24h ago $X, ±Y%)' inline form. Asserts both anchor
substrings appear on the Open Interest line.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Final test suite + lint + smoke + PR-ready

- [ ] **Step 1: Full test suite**

Run: `pytest tests/ --tb=short -q 2>&1 | tail -10`
Expected: all PASS. Total count delta from baseline (Task 0 Step 3):
- `+34 new` (Task 1: 2 / Task 2: 1 / Task 3: 5 / Task 4: 4 / Task 5: 3 / Task 6: 10 / Task 7: 8 / Task 10: 1)
- `−5 deleted` (Task 9 Step 8 obsolete `test_derivatives_data.py` functions)
- **Net `≈ +29` from baseline.** A different number is the headline red flag — investigate before proceeding.

- [ ] **Step 2: Lint / mypy / ruff**

Run:
```bash
ruff check src/ tests/
mypy src/
```
Expected: clean (or only pre-existing project warnings — must match baseline state, no new errors introduced).

- [ ] **Step 3: Manual smoke — user-run simulated session, 10-20 min**

Per memory `feedback_long_walltime_experiments`, long-walltime (>10min) sims are user-run, not Claude-run-in-background. Provide the engineer / user this exact recipe and wait for their report:

```bash
# Interactive wizard — select: simulated exchange / BTC/USDT:USDT / default everything
python main.py
# Let it run ~15 min, then Ctrl+C (the app finishes the current cycle then exits cleanly).
```

After the run, verify from the resulting `logs/session_<sid>.log` (whichever new session got created):

```bash
SID=$(ls -t logs/session_*.log | head -1)
echo "Session log: $SID"

# Anchor format must appear in derivatives output (fixed-string -F avoids BRE escape quirks)
grep -cF "(1h ago "                       "$SID"   # expect ≥ 1
grep -cF "24h ago "                       "$SID"   # expect ≥ 1

# No crash spam
grep -cF "Open Interest: (unavailable)"   "$SID"   # 0-2 acceptable; ≥5 = OKX rate-limit or network issue
```

If the engineer's environment can't reach OKX (e.g., CI / offline), document so and rely on the test suite alone — Task 11 Step 1's 30+ unit tests cover all logic paths via mocks.

- [ ] **Step 4: Final commit / PR prep**

Verify clean state:
```bash
git status
git log --oneline iter-w2r2-next-g ^main | head -20
```

Expected log (newest first):
```
test(iter-w2r2-next-g): T-DG-OI-1 drift guard for OI anchor inline format
refactor(iter-w2r2-next-g): remove unused single-point OI methods + dataclass
test(iter-w2r2-next-g): migrate downstream mocks to get_open_interest_history
feat(iter-w2r2-next-g): wire get_derivatives_data to OI history + anchor render
feat(iter-w2r2-next-g): _format_oi_usd + _derive_oi_anchors render helpers
feat(iter-w2r2-next-g): MarketDataService.get_open_interest_history with 180s cache
feat(iter-w2r2-next-g): SimulatedExchange.fetch_open_interest_history with 3 guards
feat(iter-w2r2-next-g): OKXExchange.fetch_open_interest_history via raw rubik endpoint
feat(iter-w2r2-next-g): BaseExchange.fetch_open_interest_history abstractmethod
feat(iter-w2r2-next-g): OpenInterestHistoryPoint dataclass + _OKX_OI_PERIOD
docs(iter-w2r2-next-g): OI history anchors + delta design spec — 24h/1h delta via OKX raw rubik endpoint
```

If everything is green, the PR is ready. Open it with:

```bash
gh pr create --title "iter-w2r2-next-g: OI history anchors + 24h/1h delta on get_derivatives_data" --body "$(cat <<'EOF'
## Summary
- R2-Next-G (sim #8 W2 roadmap §6.1 Iter 4) — adds 24h/1h OI anchor values + percent delta to the `Open Interest:` field of `get_derivatives_data`, addressing principle 7 missing-window gap and the cross-cycle OI mental-math narrative observed in sim #8 (cycles `dc3d1b8a` need-expression + `e6929b2c` from-to delta).
- Uses OKX raw `publicGetRubikStatContractsOpenInterestHistory` (per-instId, matches single-point USD value) to avoid the ccxt unified API's currency-aggregate signal mixing.
- Deletes now-unused single-point `get_open_interest` / `fetch_open_interest` / `OpenInterest` dataclass (grep-verified zero callers).

## Test plan
- [ ] `pytest tests/` all green (incl. ~21 new OI history tests + drift guard)
- [ ] `ruff check` / `mypy` clean
- [ ] Simulated 10-20 min smoke shows new anchor format in agent reasoning
- [ ] W3 sim gate: ≥60% retain / 50-60% observe / 31-50% docstring-promo follow-up / <31% rollback (per spec §6.2)

Spec: `docs/superpowers/specs/2026-05-13-iter-w2r2-next-g-oi-delta-design.md`
Plan: `docs/superpowers/plans/2026-05-13-iter-w2r2-next-g.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Appendix: Spec Coverage Map

| Spec section | Task(s) |
|---|---|
| §1.1-1.5 background / motivation / API constraint | (informational — referenced throughout) |
| §2.1 `OpenInterestHistoryPoint` + `_OKX_OI_PERIOD` | Task 1 |
| §2.1 `BaseExchange.fetch_open_interest_history` ABC | Task 2 |
| §2.2 OKXExchange impl + raw endpoint | Task 3 |
| §2.3 SimulatedExchange impl + 3 guards | Task 4 |
| §2.4 `MarketDataService.get_open_interest_history` + cache | Task 5 |
| §2.4 `get_open_interest` retention via grep | Task 9 Step 1 |
| §2.5 render helpers `_format_oi_usd` / `_derive_oi_anchors` | Task 6 |
| §2.5 `get_derivatives_data` wire | Task 7 |
| §2.6 `trader.py` docstring (with trust-edge note) | Task 7 Step 4 |
| §3.2 happy-path inline output | Task 7 Step 1 (test_derivs_oi_history_happy_full_anchors) |
| §3.3 退化态 cases A-D | Task 7 Steps 1+5 (oi_history_rate_limit / empty_list / one_record / two_records / anchor_zero / all_three_fail / fail_others_ok) |
| §4 failure semantics | Task 7 + Task 4 (RateLimitHit guard) |
| §4.4 limit=26 buffer | Task 5 default + Task 3/4 wire |
| §5.2 render unit tests (8) | Task 6 |
| §5.2 service/fetch unit tests (4) | Tasks 3 + 5 |
| §5.2 failure path tests (7) | Task 7 |
| §5.3 simulated integration | (Manual smoke in Task 11; can be skipped in CI per spec) |
| §5.4 drift guard T-DG-OI-1 | Task 10 |
| §6.1 acceptance | Task 11 |
| §6.2 W3 gate | (PR description references; no code change) |
