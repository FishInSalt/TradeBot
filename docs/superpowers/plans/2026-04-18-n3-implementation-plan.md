# N3: Macro Context & Institutional Flows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four perception tools (`get_higher_timeframe_view`, `get_macro_context`, `get_etf_flows`, `get_stablecoin_supply`) so the trading agent can see long-period K-line structure, cross-market macro conditions, US spot ETF flows, and stablecoin supply trends.

**Architecture:** Bottom-up build. Three new integration packages (macro / crypto_etf / onchain) each follow the N2 `news/` pattern: data model → independent HTTP clients → service aggregator with TTLCache. Shared infrastructure (`TTLCache`, `RateLimitHit`) is reused from `src/utils/cache.py`. Macro aggregator has sub-source independence (CG / FRED / AV each degrade independently). ETF service uses a cum-delta algorithm to compute daily flows from SoSoValue's multi-row responses. `get_higher_timeframe_view` calls existing `MarketDataService.get_ohlcv_dataframe()` directly — no new exchange code.

**Tech Stack:** Python 3.12, httpx (async HTTP), pandas (already in deps, used for rolling MAs), pytest + pytest-asyncio, dataclasses, `zoneinfo` (stdlib, Python 3.9+).

**Spec:** `docs/superpowers/specs/2026-04-18-n3-macro-context-design.md`

---

## Design Deviations from Spec

This plan is faithful to the spec except for the following explicit deviations. Each deviation is called out here so reviewers don't have to reconstruct intent from the task list.

| Area | Spec position | Plan position | Rationale |
|---|---|---|---|
| Alpha Vantage daily-call counter metric (spec §12.1) | "可作为 N3 实施附带项" (optional attachment, ~10 lines + 1 test) — purpose: verify the "实际不会超 25/day" assumption during observation | Deferred to a follow-up PR; not added as a plan task | "可作为" is permissive, not mandatory. Value of the metric is observation-time validation; can be added in a focused 30-minute PR once a real observation window exists. Avoids bundling speculative telemetry into an already large feature PR. |
| `CryptoEtfService.get_etf_flows` insufficient-data return value (spec §5.3 vs §3.5) | Spec §5.3 pseudocode returns `None` for insufficient data; spec §3.5 three-state contract distinguishes `[]` (data-gap, "No data") from `None` (outage, "temporarily unavailable") | Return `[]` for insufficient data, `None` only for source outage | §5.3 pseudocode is illustrative and predates the §3.5 contract articulation in the same spec. Returning `[]` is faithful to §3.5's three-state semantics: the upstream call succeeded but the requested window lacks history — this is a distinct condition from "SoSoValue is down" and deserves distinct tool-layer rendering ("Insufficient data in requested window" vs "Temporarily unavailable"). The agent can pick different recovery strategies for the two. |

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/integrations/macro/__init__.py` | Create | Package init |
| `src/integrations/macro/models.py` | Create | `FREDObservation`, `EquityQuote`, `MacroSnapshot` dataclasses |
| `src/integrations/macro/fred.py` | Create | FRED API client — `fetch_latest(series_id)` |
| `src/integrations/macro/cg_global.py` | Create | CoinGecko `/global` client (Demo key in header) |
| `src/integrations/macro/alpha_vantage.py` | Create | Alpha Vantage client with `Information` soft-error detection + 1 req/sec throttle + time-of-day TTL helper |
| `src/integrations/macro/service.py` | Create | `MacroService` — aggregation, per-source caching, sub-source independence |
| `src/integrations/crypto_etf/__init__.py` | Create | Package init |
| `src/integrations/crypto_etf/models.py` | Create | `ETFFlowEntry` dataclass |
| `src/integrations/crypto_etf/sosovalue.py` | Create | SoSoValue API client |
| `src/integrations/crypto_etf/service.py` | Create | `CryptoEtfService` with cum-delta algorithm |
| `src/integrations/onchain/__init__.py` | Create | Package init |
| `src/integrations/onchain/models.py` | Create | `StablecoinSnapshot`, `StablecoinTotal` dataclasses |
| `src/integrations/onchain/defillama.py` | Create | DefiLlama stablecoins client |
| `src/integrations/onchain/service.py` | Create | `OnchainService` |
| `src/config.py` | Modify | Add `MacroConfig`, `CryptoEtfConfig`, `OnchainConfig` + env overrides |
| `src/agent/tools_perception.py` | Modify | Add 4 new tool implementations |
| `src/agent/trader.py` | Modify | Add 3 fields to `TradingDeps`, register 4 new tool wrappers |
| `src/agent/persona.py` | Modify | Append 4 bullets to Layer 1 |
| `src/cli/app.py` | Modify | Initialize 3 new services, inject into deps, close on shutdown |
| `tests/test_macro_models.py` | Create | Dataclass tests |
| `tests/test_macro_clients.py` | Create | FRED / CG / AV client unit tests |
| `tests/test_macro_service.py` | Create | MacroService aggregation + degradation tests |
| `tests/test_av_time_of_day_cache.py` | Create | Time-of-day TTL tests |
| `tests/test_crypto_etf_client.py` | Create | SoSoValue client unit tests |
| `tests/test_crypto_etf_service.py` | Create | Cum-delta algorithm + service tests |
| `tests/test_onchain_client.py` | Create | DefiLlama client tests |
| `tests/test_onchain_service.py` | Create | OnchainService tests |
| `tests/test_perception_tools_n3.py` | Create | 4 new tool implementation tests |
| `tests/test_config.py` | Modify | Add N3 config tests |
| `tests/test_tools.py` | Modify | Add `macro`, `crypto_etf`, `onchain` fields to `MockDeps` |
| `tests/test_news_tools.py` | Modify | Add `macro`, `crypto_etf`, `onchain` fields to `MockDeps` |

---

## Task 0: Pre-work Verification (run BEFORE Task 1)

Spec §5.8 requires all 5 data sources be smoke-tested end-to-end before implementation begins. This avoids N2-style rework when an assumed field turns out missing.

**Files:**
- No code changes. Smoke-test only. Populate `.env` with 4 new API keys.

- [ ] **Step 1: Register free API keys and add to `.env`**

Register at each URL and add the resulting key to `.env`:

| Source | Registration URL | Env var |
|---|---|---|
| FRED | https://fredaccount.stlouisfed.org/apikeys | `FRED_API_KEY` |
| Alpha Vantage | https://www.alphavantage.co/support/#api-key | `ALPHA_VANTAGE_API_KEY` |
| CoinGecko Demo | https://www.coingecko.com/en/api/pricing → "Create Demo Account" | `COINGECKO_DEMO_API_KEY` |
| SoSoValue | https://sosovalue.com/developer | `SOSOVALUE_API_KEY` |

- [ ] **Step 2: Run smoke tests for all 5 sources**

```bash
set -a; source .env; set +a

# --- FRED 5 series ---
for s in DTWEXBGS VIXCLS DGS10 T10Y2Y T10YIE; do
  echo "=== FRED $s ==="
  curl -s "https://api.stlouisfed.org/fred/series/observations?series_id=$s&api_key=$FRED_API_KEY&file_type=json&limit=1&sort_order=desc"
  echo ""
done

# --- Alpha Vantage SPY + QQQ (>=1s gap) ---
curl -s "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=SPY&apikey=$ALPHA_VANTAGE_API_KEY"
sleep 2
curl -s "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=QQQ&apikey=$ALPHA_VANTAGE_API_KEY"

# --- CoinGecko /global (Demo key required) ---
curl -s -H "x-cg-demo-api-key: $COINGECKO_DEMO_API_KEY" \
  "https://api.coingecko.com/api/v3/global" \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['data']; print(f'BTC.D={d[\"market_cap_percentage\"][\"btc\"]:.2f}%, total_mcap=\${d[\"total_market_cap\"][\"usd\"]/1e12:.2f}T')"

# --- SoSoValue BTC ETF (verify multi-row pattern) ---
curl -s -H "x-soso-api-key: $SOSOVALUE_API_KEY" \
  "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history?symbol=BTC&country_code=US" \
  | python3 -c "
import json, sys
from collections import Counter
d = json.load(sys.stdin)['data']
c = Counter(r['date'] for r in d)
multi = [k for k, v in c.items() if v > 1]
print(f'Total rows: {len(d)}, distinct dates: {len(c)}, multi-row dates: {len(multi)}')
if multi:
    rows = [r for r in d if r['date']==multi[0]]
    cum = {r['cum_net_inflow'] for r in rows}
    assets = {r['total_net_assets'] for r in rows}
    print(f'  cum_net_inflow values for {multi[0]}: {cum}')
    print(f'  total_net_assets values for {multi[0]}: {assets}')
"

# --- SoSoValue ETH ETF (spec §5.8 Step 2: first implementer step) ---
curl -s -H "x-soso-api-key: $SOSOVALUE_API_KEY" \
  "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history?symbol=ETH&country_code=US" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)['data']
print(f'ETH total rows: {len(d)}')
print(f'ETH first row keys: {sorted(d[0].keys())}' if d else 'ETH response empty')
"

# --- DefiLlama stablecoins ---
curl -s "https://stablecoins.llama.fi/stablecoins" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)['peggedAssets']
for sym in ['USDT', 'USDC']:
    a = next((x for x in d if x['symbol']==sym), None)
    if a:
        print(f'{sym}={a[\"circulating\"][\"peggedUSD\"]/1e9:.2f}B, prevWeek={a[\"circulatingPrevWeek\"][\"peggedUSD\"]/1e9:.2f}B')
    else:
        print(f'{sym}: NOT FOUND')
"

# --- ccxt OKX monthly candles (1M timeframe is unverified in spec §2.6) ---
# get_higher_timeframe_view supports timeframe in {4h, 1d, 1w, 1M}. Spec §2.6
# smoke-tested 1D+limit=300 only; 1M is a gap. 1M candles need >=200 months
# of history for MA200, which BTC has had since 2010 — so volume should be
# fine, but the ccxt→OKX param mapping (e.g. whether OKX accepts "1M" vs
# "1Mon" vs needs ccxt's timeframe dict) is worth a one-call sanity check.
python3 - <<'PY'
import asyncio
import ccxt.async_support as ccxt

async def main():
    ex = ccxt.okx()
    try:
        for tf in ("4h", "1d", "1w", "1M"):
            candles = await ex.fetch_ohlcv("BTC/USDT:USDT", tf, limit=5)
            print(f"{tf}: {len(candles)} candles, last close={candles[-1][4] if candles else 'N/A'}")
    finally:
        await ex.close()

asyncio.run(main())
PY
```

- [ ] **Step 3: Verify checklist**

All must pass. If ANY fails, STOP and investigate root cause before proceeding.

- [ ] FRED: all 5 series return `observations` arrays (not 401)
- [ ] Alpha Vantage: SPY/QQQ return `Global Quote` object (not `Information`)
- [ ] CoinGecko: returns BTC.D percentage (Demo key header works)
- [ ] SoSoValue BTC: multi-row dates ≥ 1; cum_net_inflow and total_net_assets are identical across rows of the same date (confirms §5.3 dedup strategy)
- [ ] SoSoValue ETH: endpoint responds with same field names as BTC
- [ ] DefiLlama: USDT AND USDC both present with `circulating.peggedUSD` and `circulatingPrevWeek.peggedUSD` populated
- [ ] ccxt OKX: all 4 timeframes (`4h`, `1d`, `1w`, `1M`) return non-empty candle arrays with a numeric `last close`. If `1M` raises `NotSupported` or returns `[]`, the HTF tool's `1M` branch will need a timeframe-mapping fallback — investigate before Task 11.

No commit in this task — no code changes.

---

## Task 1: Macro Data Models

**Files:**
- Create: `src/integrations/macro/__init__.py` (empty)
- Create: `src/integrations/macro/models.py`
- Test: `tests/test_macro_models.py`

- [ ] **Step 1: Create empty package init**

```python
# src/integrations/macro/__init__.py
```

(Empty file. Package marker.)

- [ ] **Step 2: Write failing tests for models**

```python
# tests/test_macro_models.py
"""Tests for macro data model dataclasses."""


def test_fred_observation_fields():
    from src.integrations.macro.models import FREDObservation
    obs = FREDObservation(series_id="VIXCLS", date="2026-04-16", value=17.94)
    assert obs.series_id == "VIXCLS"
    assert obs.date == "2026-04-16"
    assert obs.value == 17.94


def test_fred_observation_is_frozen():
    import dataclasses
    import pytest
    from src.integrations.macro.models import FREDObservation
    obs = FREDObservation(series_id="VIXCLS", date="2026-04-16", value=17.94)
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.value = 99.0


def test_equity_quote_fields():
    from src.integrations.macro.models import EquityQuote
    q = EquityQuote(
        symbol="SPY", price=710.14, change_pct=1.21,
        latest_trading_day="2026-04-17",
    )
    assert q.symbol == "SPY"
    assert q.price == 710.14
    assert q.change_pct == 1.21
    assert q.latest_trading_day == "2026-04-17"


def test_macro_snapshot_all_none_allowed():
    """All sub-source fields must accept None (sub-source independence)."""
    from src.integrations.macro.models import MacroSnapshot
    snap = MacroSnapshot(
        btc_dominance=None, eth_dominance=None,
        total_mcap_usd=None, mcap_change_24h_pct=None,
        usd_index_broad_tw=None, vix=None, treasury_10y=None,
        spread_10y_2y=None, inflation_10y=None,
        spy=None, qqq=None,
    )
    assert snap.btc_dominance is None
    assert snap.spy is None


def test_macro_snapshot_full_values():
    from src.integrations.macro.models import (
        MacroSnapshot, FREDObservation, EquityQuote,
    )
    snap = MacroSnapshot(
        btc_dominance=57.31, eth_dominance=10.79,
        total_mcap_usd=2.69e12, mcap_change_24h_pct=2.58,
        usd_index_broad_tw=FREDObservation("DTWEXBGS", "2026-04-10", 118.86),
        vix=FREDObservation("VIXCLS", "2026-04-16", 17.94),
        treasury_10y=FREDObservation("DGS10", "2026-04-16", 4.32),
        spread_10y_2y=FREDObservation("T10Y2Y", "2026-04-16", 0.06),
        inflation_10y=FREDObservation("T10YIE", "2026-04-16", 2.43),
        spy=EquityQuote("SPY", 710.14, 1.21, "2026-04-17"),
        qqq=EquityQuote("QQQ", 648.85, 1.31, "2026-04-17"),
    )
    assert snap.btc_dominance == 57.31
    assert snap.vix.value == 17.94
    assert snap.spy.price == 710.14
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_macro_models.py -v`
Expected: FAIL with `ImportError: No module named 'src.integrations.macro.models'` or similar.

- [ ] **Step 4: Implement models**

```python
# src/integrations/macro/models.py
"""Data models for macro context (CG /global + FRED + Alpha Vantage)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FREDObservation:
    """A single FRED series observation.

    `date` is an ISO "YYYY-MM-DD" string. FRED series have daily granularity
    but different report delays (DTWEXBGS often lags ~1 week; VIX/DGS10 daily).
    The `date` field lets the Agent see each value's actual observation date.
    """
    series_id: str
    date: str
    value: float


@dataclass(frozen=True)
class EquityQuote:
    """Alpha Vantage GLOBAL_QUOTE response — SPY / QQQ."""
    symbol: str
    price: float
    change_pct: float          # 24h %, e.g. +1.21
    latest_trading_day: str    # ISO "YYYY-MM-DD"


@dataclass(frozen=True)
class MacroSnapshot:
    """Aggregate of CG /global + FRED + Alpha Vantage.

    Every field is Optional so sub-source failures degrade independently
    (spec §3.2). The FRED USD-index field is named `usd_index_broad_tw`
    rather than `dxy` because the series is DTWEXBGS (Fed Broad TW index,
    26 currencies, basis 2006=100), NOT the ICE DXY (6 currencies, basis
    1973=100). See spec §2.2 for the rationale.
    """
    # CoinGecko /global
    btc_dominance: float | None
    eth_dominance: float | None
    total_mcap_usd: float | None
    mcap_change_24h_pct: float | None

    # FRED
    usd_index_broad_tw: FREDObservation | None
    vix: FREDObservation | None
    treasury_10y: FREDObservation | None
    spread_10y_2y: FREDObservation | None
    inflation_10y: FREDObservation | None

    # Alpha Vantage
    spy: EquityQuote | None
    qqq: EquityQuote | None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_macro_models.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/integrations/macro/__init__.py src/integrations/macro/models.py \
       tests/test_macro_models.py
git commit -m "feat(N3): add macro data models (FREDObservation, EquityQuote, MacroSnapshot)"
```

---

## Task 2: FRED Client

**Files:**
- Create: `src/integrations/macro/fred.py`
- Test: `tests/test_macro_clients.py` (FRED section)

- [ ] **Step 1: Write failing tests for FRED client**

```python
# tests/test_macro_clients.py
"""Tests for macro API clients (FRED, CoinGecko, Alpha Vantage)."""
import httpx
import pytest

from src.utils.cache import RateLimitHit


# ===== FRED =====

FRED_RESPONSE_VIX = {
    "realtime_start": "2026-04-18",
    "realtime_end": "2026-04-18",
    "observation_start": "1600-01-01",
    "observation_end": "9999-12-31",
    "units": "lin",
    "output_type": 1,
    "file_type": "json",
    "order_by": "observation_date",
    "sort_order": "desc",
    "count": 1,
    "offset": 0,
    "limit": 3,
    "observations": [
        {"realtime_start": "2026-04-18", "realtime_end": "2026-04-18",
         "date": "2026-04-16", "value": "17.94"},
    ],
}

FRED_RESPONSE_WITH_NA = {
    "observations": [
        {"date": "2026-04-17", "value": "."},  # FRED uses "." for missing
        {"date": "2026-04-16", "value": "17.94"},
    ],
}


async def test_fred_parse_latest():
    from src.integrations.macro.fred import FREDClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=FRED_RESPONSE_VIX)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="test-key")
        obs = await client.fetch_latest("VIXCLS")
    assert obs.series_id == "VIXCLS"
    assert obs.date == "2026-04-16"
    assert obs.value == 17.94


async def test_fred_passes_api_key_and_params():
    """FRED expects api_key, series_id, file_type, limit, sort_order in query."""
    from src.integrations.macro.fred import FREDClient
    captured_params: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_params.update(dict(req.url.params))
        return httpx.Response(200, json=FRED_RESPONSE_VIX)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="my-fred-key")
        await client.fetch_latest("VIXCLS")
    assert captured_params["series_id"] == "VIXCLS"
    assert captured_params["api_key"] == "my-fred-key"
    assert captured_params["file_type"] == "json"
    assert captured_params["limit"] == "3"
    assert captured_params["sort_order"] == "desc"


async def test_fred_skips_na_value_returns_next():
    """FRED returns '.' for missing readings; skip to next real observation."""
    from src.integrations.macro.fred import FREDClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=FRED_RESPONSE_WITH_NA)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="k")
        obs = await client.fetch_latest("VIXCLS")
    assert obs.date == "2026-04-16"
    assert obs.value == 17.94


async def test_fred_all_na_returns_none():
    """If every observation is missing, return None (treated as degraded)."""
    from src.integrations.macro.fred import FREDClient
    body = {"observations": [{"date": "2026-04-17", "value": "."}]}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="k")
        obs = await client.fetch_latest("VIXCLS")
    assert obs is None


async def test_fred_empty_observations_returns_none():
    from src.integrations.macro.fred import FREDClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"observations": []})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="k")
        obs = await client.fetch_latest("VIXCLS")
    assert obs is None


async def test_fred_429_raises_rate_limit():
    from src.integrations.macro.fred import FREDClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = FREDClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_latest("VIXCLS")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_macro_clients.py -v`
Expected: FAIL with `ImportError: No module named 'src.integrations.macro.fred'`.

- [ ] **Step 3: Implement FRED client**

```python
# src/integrations/macro/fred.py
"""FRED (Federal Reserve Economic Data) API client."""
from __future__ import annotations

import httpx

from src.integrations.macro.models import FREDObservation
from src.utils.cache import RateLimitHit

_FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


class FREDClient:
    """Fetches the latest non-missing observation for a single FRED series.

    FRED returns "." for missing readings (e.g. holidays). We scan up to
    `limit=3` rows to find the first real value, so that DTWEXBGS's ~1-week
    report delay (spec §2.2) still yields a usable observation.
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def fetch_latest(self, series_id: str) -> FREDObservation | None:
        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "limit": 3,
            "sort_order": "desc",
        }
        resp = await self._http.get(_FRED_URL, params=params)
        if resp.status_code == 429:
            raise RateLimitHit(f"FRED rate limited for {series_id}")
        resp.raise_for_status()

        observations = resp.json().get("observations", [])
        for obs in observations:
            raw = obs.get("value")
            if raw in (None, "", "."):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            date = obs.get("date", "")
            if not date:
                continue
            return FREDObservation(series_id=series_id, date=date, value=value)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_macro_clients.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/macro/fred.py tests/test_macro_clients.py
git commit -m "feat(N3): add FRED client with NA-value handling"
```

---

## Task 3: CoinGecko Global Client

**Files:**
- Create: `src/integrations/macro/cg_global.py`
- Modify: `tests/test_macro_clients.py` (append CoinGecko section)

- [ ] **Step 1: Append failing tests for CoinGecko client**

Append at the bottom of `tests/test_macro_clients.py`:

```python
# ===== CoinGecko /global =====

CG_GLOBAL_RESPONSE = {
    "data": {
        "active_cryptocurrencies": 18000,
        "markets": 1100,
        "total_market_cap": {"usd": 2_692_000_000_000.0},
        "total_volume": {"usd": 80_000_000_000.0},
        "market_cap_percentage": {"btc": 57.31, "eth": 10.79, "usdt": 3.5},
        "market_cap_change_percentage_24h_usd": 2.58,
        "updated_at": 1776499200,
    },
}


async def test_cg_global_parse_response():
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=CG_GLOBAL_RESPONSE)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="demo-key")
        data = await client.fetch_global()
    assert data["btc_dominance"] == 57.31
    assert data["eth_dominance"] == 10.79
    assert data["total_mcap_usd"] == 2_692_000_000_000.0
    assert data["mcap_change_24h_pct"] == 2.58


async def test_cg_global_sends_demo_key_header():
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    captured_headers: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(req.headers))
        return httpx.Response(200, json=CG_GLOBAL_RESPONSE)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="my-demo-key")
        await client.fetch_global()
    assert captured_headers.get("x-cg-demo-api-key") == "my-demo-key"


async def test_cg_global_429_raises_rate_limit():
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_global()


async def test_cg_global_missing_fields_return_nones():
    """If CG adjusts its response schema, degrade by returning None per-field."""
    from src.integrations.macro.cg_global import CoinGeckoGlobalClient
    body = {"data": {"market_cap_percentage": {}, "total_market_cap": {}}}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinGeckoGlobalClient(http, api_key="k")
        data = await client.fetch_global()
    assert data["btc_dominance"] is None
    assert data["eth_dominance"] is None
    assert data["total_mcap_usd"] is None
    assert data["mcap_change_24h_pct"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_macro_clients.py -v -k "cg_global"`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement CoinGecko client**

```python
# src/integrations/macro/cg_global.py
"""CoinGecko /global API client — crypto market overview."""
from __future__ import annotations

import httpx

from src.utils.cache import RateLimitHit

_CG_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


class CoinGeckoGlobalClient:
    """Fetches crypto market totals: BTC/ETH dominance, total mcap, 24h change.

    Requires a Demo-tier API key (30 req/min) passed in the
    `x-cg-demo-api-key` header. Spec §2.1 explains why keyless access is
    insufficient (5-15 req/min unstable cap).

    Returns a dict of primitives rather than a dataclass so MacroService can
    assemble the final MacroSnapshot without a per-source boilerplate type.
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def fetch_global(self) -> dict[str, float | None]:
        headers = {"x-cg-demo-api-key": self._api_key}
        resp = await self._http.get(_CG_GLOBAL_URL, headers=headers)
        if resp.status_code == 429:
            raise RateLimitHit("CoinGecko rate limited")
        resp.raise_for_status()

        data = resp.json().get("data", {})
        mcap_pct = data.get("market_cap_percentage", {}) or {}
        total_mcap = data.get("total_market_cap", {}) or {}

        # Each field may be missing if CG adjusts schema. None-per-field
        # keeps the downstream degradation path per-field rather than
        # per-source (spec §3.2 sub-source independence).
        return {
            "btc_dominance": mcap_pct.get("btc"),
            "eth_dominance": mcap_pct.get("eth"),
            "total_mcap_usd": total_mcap.get("usd"),
            "mcap_change_24h_pct": data.get("market_cap_change_percentage_24h_usd"),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_macro_clients.py -v -k "cg_global"`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/macro/cg_global.py tests/test_macro_clients.py
git commit -m "feat(N3): add CoinGecko /global client with Demo key header"
```

---

## Task 4: Alpha Vantage Client + Time-of-Day TTL

**Files:**
- Create: `src/integrations/macro/alpha_vantage.py`
- Modify: `tests/test_macro_clients.py` (append AV client section)
- Test: `tests/test_av_time_of_day_cache.py`

- [ ] **Step 1: Append failing tests for AV client to `test_macro_clients.py`**

```python
# ===== Alpha Vantage =====

AV_RESPONSE_SPY = {
    "Global Quote": {
        "01. symbol": "SPY",
        "05. price": "710.1400",
        "06. volume": "50000000",
        "07. latest trading day": "2026-04-17",
        "09. change": "8.49",
        "10. change percent": "1.21%",
    },
}

AV_RESPONSE_RATE_LIMIT = {
    "Information": (
        "Thank you for using Alpha Vantage! "
        "Our standard API rate limit is 25 requests per day."
    ),
}

AV_RESPONSE_NOTE_LIMIT = {
    "Note": "Thank you for using Alpha Vantage! 5 calls/min exceeded.",
}


async def test_av_parse_quote():
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        quote = await client.fetch_quote("SPY")
    assert quote.symbol == "SPY"
    assert quote.price == 710.14
    assert quote.change_pct == 1.21
    assert quote.latest_trading_day == "2026-04-17"


async def test_av_information_field_raises_rate_limit():
    """AV returns HTTP 200 + body containing 'Information' on rate limit.
    Client must raise RateLimitHit, not return a malformed Quote."""
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_RATE_LIMIT)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        with pytest.raises(RateLimitHit, match="rate limit"):
            await client.fetch_quote("SPY")


async def test_av_note_field_also_raises_rate_limit():
    """Older AV error responses use 'Note' key instead of 'Information'."""
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_NOTE_LIMIT)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_quote("SPY")


async def test_av_unexpected_shape_raises_value_error():
    """If the response has neither Global Quote nor Information/Note, flag it
    as a hard error so it shows up as 'temporarily unavailable' rather than
    silently degrading."""
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"unexpected": "shape"})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        with pytest.raises(ValueError):
            await client.fetch_quote("SPY")


async def test_av_429_also_raises_rate_limit():
    from src.integrations.macro.alpha_vantage import AlphaVantageClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = AlphaVantageClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_quote("SPY")


async def test_av_throttles_consecutive_calls(monkeypatch):
    """AV enforces 1 req/sec hard limit. Client must call asyncio.sleep for
    at least _MIN_INTERVAL (~1.1s) on the second consecutive call.

    We patch asyncio.sleep to record call durations without actually waiting,
    so CI does not burn ~1.1s per invocation."""
    import src.integrations.macro.alpha_vantage as av_module

    sleep_durations: list[float] = []

    async def fake_sleep(duration: float) -> None:
        sleep_durations.append(duration)

    monkeypatch.setattr(av_module.asyncio, "sleep", fake_sleep)

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=AV_RESPONSE_SPY)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = av_module.AlphaVantageClient(http, api_key="k")
        await client.fetch_quote("SPY")  # first call: no sleep, _last_fetch_at=0
        await client.fetch_quote("SPY")  # second call: must sleep ~_MIN_INTERVAL

    # First call should NOT have slept (elapsed since monotonic()=0 is huge).
    # Second call MUST have slept very close to _MIN_INTERVAL.
    assert len(sleep_durations) == 1, (
        f"expected exactly one throttle sleep, got {sleep_durations}"
    )
    # Generous band for monotonic-clock jitter between the two time.monotonic()
    # reads bracketing the http.get() call (which runs in the same test event
    # loop; mock transport is near-instant).
    assert 0.9 <= sleep_durations[0] <= 1.2, (
        f"throttle sleep duration {sleep_durations[0]} outside expected band"
    )
```

- [ ] **Step 2: Write failing tests for time-of-day TTL helper**

```python
# tests/test_av_time_of_day_cache.py
"""Tests for Alpha Vantage time-of-day TTL helper (spec §5.2)."""
from datetime import datetime
from zoneinfo import ZoneInfo


def _patch_now(monkeypatch, et_dt: datetime) -> None:
    """Replace datetime.now inside alpha_vantage module with a fixed ET time."""
    import src.integrations.macro.alpha_vantage as mod

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return et_dt.astimezone(tz) if tz else et_dt

    monkeypatch.setattr(mod, "datetime", FakeDateTime)


def test_ttl_weekend_saturday(monkeypatch):
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    # 2026-04-18 is Saturday
    _patch_now(monkeypatch, datetime(2026, 4, 18, 14, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 12 * 3600.0


def test_ttl_weekend_sunday(monkeypatch):
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 19, 10, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 12 * 3600.0


def test_ttl_weekday_market_hours(monkeypatch):
    """Weekday 9:30-16:00 ET → 30min TTL."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    # Monday 10:00 AM ET
    _patch_now(monkeypatch, datetime(2026, 4, 20, 10, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 30 * 60.0


def test_ttl_weekday_market_open_edge(monkeypatch):
    """9:30 AM inclusive → market hours."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 9, 30, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 30 * 60.0


def test_ttl_weekday_just_before_open(monkeypatch):
    """9:29 AM → pre-market 4h TTL."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 9, 29, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 4 * 3600.0


def test_ttl_weekday_market_close_edge(monkeypatch):
    """16:00 exclusive → after-market 4h TTL."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 16, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 4 * 3600.0


def test_ttl_weekday_just_before_close(monkeypatch):
    """15:59 → still market hours."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 15, 59, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 30 * 60.0


def test_ttl_weekday_after_hours(monkeypatch):
    """Weekday 20:00 ET → 4h TTL."""
    from src.integrations.macro.alpha_vantage import alpha_vantage_ttl_seconds
    _patch_now(monkeypatch, datetime(2026, 4, 20, 20, 0, tzinfo=ZoneInfo("America/New_York")))
    assert alpha_vantage_ttl_seconds() == 4 * 3600.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_av_time_of_day_cache.py tests/test_macro_clients.py -v -k "av_"`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Implement Alpha Vantage client + TTL helper**

```python
# src/integrations/macro/alpha_vantage.py
"""Alpha Vantage client — US equity quotes (SPY, QQQ).

Handles two AV quirks:
1. Rate-limit responses are HTTP 200 + body containing 'Information' / 'Note'.
2. AV enforces a 1 req/sec hard limit — a per-client throttle avoids soft
   limiting on burst calls.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from src.integrations.macro.models import EquityQuote
from src.utils.cache import RateLimitHit

_AV_URL = "https://www.alphavantage.co/query"
_NY = ZoneInfo("America/New_York")


def alpha_vantage_ttl_seconds() -> float:
    """Time-of-day aware cache TTL for Alpha Vantage SPY/QQQ (spec §5.2).

    - Sat/Sun: 12h (data static, conserve 25/day budget)
    - Weekday 9:30-16:00 ET: 30min (catch intraday moves)
    - Weekday pre/after market: 4h

    NYSE holidays not handled — weekday holidays use the short TTL based on
    time-of-day, wasting a few API calls on static data. Acceptable for now;
    if observed budget pressure becomes an issue, add a holiday calendar.
    """
    now_et = datetime.now(_NY)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return 12 * 3600.0
    hour_min = now_et.hour + now_et.minute / 60.0
    if 9.5 <= hour_min < 16.0:
        return 30 * 60.0
    return 4 * 3600.0


class AlphaVantageClient:
    """Fetches GLOBAL_QUOTE (SPY, QQQ).

    Throttles outgoing HTTP to >= _MIN_INTERVAL seconds apart; cache hits
    in MacroService never reach this method so they don't incur the sleep.
    MacroService._fetch_av_all currently calls SPY then QQQ serially, so
    this throttle is partly redundant — kept as defensive measure in case a
    future refactor parallelizes per-symbol fetches.
    """

    _MIN_INTERVAL = 1.1  # 1 req/sec hard limit + 100ms safety margin

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key
        self._last_fetch_at: float = 0.0

    async def fetch_quote(self, symbol: str) -> EquityQuote:
        elapsed = time.monotonic() - self._last_fetch_at
        if elapsed < self._MIN_INTERVAL:
            await asyncio.sleep(self._MIN_INTERVAL - elapsed)

        try:
            resp = await self._http.get(
                _AV_URL,
                params={
                    "function": "GLOBAL_QUOTE",
                    "symbol": symbol,
                    "apikey": self._api_key,
                },
            )
        finally:
            # Advance the clock even on failure so subsequent retries also
            # respect the hard limit.
            self._last_fetch_at = time.monotonic()

        if resp.status_code == 429:
            raise RateLimitHit(f"Alpha Vantage hard 429 for {symbol}")
        resp.raise_for_status()

        data = resp.json()
        # AV signals soft rate limit via HTTP 200 body. Both 'Information'
        # (new) and 'Note' (legacy) are observed — check both.
        soft_msg = data.get("Information") or data.get("Note")
        if soft_msg:
            raise RateLimitHit(f"Alpha Vantage soft rate limit: {soft_msg}")

        quote = data.get("Global Quote")
        if not quote:
            raise ValueError(
                f"Alpha Vantage returned unexpected shape: {list(data.keys())}"
            )

        return EquityQuote(
            symbol=quote.get("01. symbol", symbol),
            price=float(quote["05. price"]),
            change_pct=float(quote["10. change percent"].rstrip("%")),
            latest_trading_day=quote.get("07. latest trading day", ""),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_av_time_of_day_cache.py tests/test_macro_clients.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/integrations/macro/alpha_vantage.py \
       tests/test_macro_clients.py tests/test_av_time_of_day_cache.py
git commit -m "feat(N3): add Alpha Vantage client with soft rate-limit detection and time-of-day TTL"
```

---

## Task 5: MacroService (aggregation + sub-source independence)

**Files:**
- Create: `src/integrations/macro/service.py`
- Test: `tests/test_macro_service.py`

- [ ] **Step 1: Write failing tests for MacroService**

```python
# tests/test_macro_service.py
"""Tests for MacroService — aggregation, caching, sub-source independence."""
from unittest.mock import AsyncMock

import pytest

from src.integrations.macro.models import (
    EquityQuote, FREDObservation, MacroSnapshot,
)
from src.utils.cache import RateLimitHit


def _make_service():
    """Build MacroService with all clients mocked."""
    from src.integrations.macro.service import MacroService
    svc = MacroService(
        fred_key="fk", av_key="ak", cg_key="ck", http=AsyncMock(),
    )
    svc._cg = AsyncMock()
    svc._fred = AsyncMock()
    svc._av = AsyncMock()
    return svc


async def test_all_sources_succeed():
    svc = _make_service()
    svc._cg.fetch_global.return_value = {
        "btc_dominance": 57.31, "eth_dominance": 10.79,
        "total_mcap_usd": 2.69e12, "mcap_change_24h_pct": 2.58,
    }
    svc._fred.fetch_latest.side_effect = [
        FREDObservation("DTWEXBGS", "2026-04-10", 118.86),
        FREDObservation("VIXCLS", "2026-04-16", 17.94),
        FREDObservation("DGS10", "2026-04-16", 4.32),
        FREDObservation("T10Y2Y", "2026-04-16", 0.06),
        FREDObservation("T10YIE", "2026-04-16", 2.43),
    ]
    svc._av.fetch_quote.side_effect = [
        EquityQuote("SPY", 710.14, 1.21, "2026-04-17"),
        EquityQuote("QQQ", 648.85, 1.31, "2026-04-17"),
    ]
    snap = await svc.get_snapshot()
    assert isinstance(snap, MacroSnapshot)
    assert snap.btc_dominance == 57.31
    assert snap.vix.value == 17.94
    assert snap.spy.price == 710.14


async def test_cg_failure_does_not_affect_others():
    """CG source fails → cg fields are None; FRED + AV still populated."""
    svc = _make_service()
    svc._cg.fetch_global.side_effect = RuntimeError("network down")
    svc._fred.fetch_latest.return_value = FREDObservation("VIXCLS", "2026-04-16", 17.94)
    svc._av.fetch_quote.return_value = EquityQuote("SPY", 710.14, 1.21, "2026-04-17")
    snap = await svc.get_snapshot()
    assert snap.btc_dominance is None
    assert snap.eth_dominance is None
    assert snap.vix is not None
    assert snap.spy is not None


async def test_fred_partial_failure_per_series():
    """One FRED series failing leaves others intact."""
    svc = _make_service()
    svc._cg.fetch_global.return_value = {
        "btc_dominance": None, "eth_dominance": None,
        "total_mcap_usd": None, "mcap_change_24h_pct": None,
    }

    def fake_fred(series_id):
        if series_id == "VIXCLS":
            raise RuntimeError("VIX server down")
        return FREDObservation(series_id, "2026-04-16", 1.0)

    svc._fred.fetch_latest.side_effect = fake_fred
    svc._av.fetch_quote.return_value = EquityQuote("SPY", 710.14, 1.21, "2026-04-17")

    snap = await svc.get_snapshot()
    assert snap.vix is None
    assert snap.treasury_10y is not None
    assert snap.usd_index_broad_tw is not None


async def test_av_rate_limit_returns_none_for_that_symbol():
    svc = _make_service()
    svc._cg.fetch_global.return_value = {
        "btc_dominance": None, "eth_dominance": None,
        "total_mcap_usd": None, "mcap_change_24h_pct": None,
    }
    svc._fred.fetch_latest.return_value = FREDObservation("VIXCLS", "2026-04-16", 17.94)

    def fake_av(sym):
        if sym == "SPY":
            raise RateLimitHit("25/day exceeded")
        return EquityQuote("QQQ", 648.85, 1.31, "2026-04-17")

    svc._av.fetch_quote.side_effect = fake_av
    snap = await svc.get_snapshot()
    assert snap.spy is None
    assert snap.qqq is not None


async def test_cache_hit_skips_upstream_call():
    svc = _make_service()
    svc._cg.fetch_global.return_value = {
        "btc_dominance": 57.31, "eth_dominance": 10.79,
        "total_mcap_usd": 2.69e12, "mcap_change_24h_pct": 2.58,
    }
    svc._fred.fetch_latest.return_value = FREDObservation("X", "2026-04-16", 1.0)
    svc._av.fetch_quote.return_value = EquityQuote("SPY", 1.0, 0.1, "2026-04-17")

    await svc.get_snapshot()
    cg_calls_first = svc._cg.fetch_global.call_count
    await svc.get_snapshot()
    # Second call within TTL → cache hit, no new upstream call.
    assert svc._cg.fetch_global.call_count == cg_calls_first


async def test_close_closes_http_when_owned():
    from src.integrations.macro.service import MacroService
    svc = MacroService(fred_key="k", av_key="k", cg_key="k")  # http=None → owned
    svc._http = AsyncMock()
    svc._owns_http = True
    await svc.close()
    svc._http.aclose.assert_awaited_once()


async def test_close_does_not_close_injected_http():
    svc = _make_service()
    # _make_service passes http=AsyncMock() → not owned
    svc._http = AsyncMock()
    svc._owns_http = False
    await svc.close()
    svc._http.aclose.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_macro_service.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement MacroService**

```python
# src/integrations/macro/service.py
"""MacroService — aggregates CoinGecko /global + FRED + Alpha Vantage."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.integrations.macro.alpha_vantage import (
    AlphaVantageClient, alpha_vantage_ttl_seconds,
)
from src.integrations.macro.cg_global import CoinGeckoGlobalClient
from src.integrations.macro.fred import FREDClient
from src.integrations.macro.models import (
    EquityQuote, FREDObservation, MacroSnapshot,
)
from src.utils.cache import RateLimitHit, TTLCache

logger = logging.getLogger(__name__)

# Cache TTLs (seconds), spec §2.1 / §2.2
_CG_TTL = 900.0       # 15 min — crypto market 24/7
_FRED_TTL = 21600.0   # 6 h — daily-granularity series

_FRED_SERIES = ("DTWEXBGS", "VIXCLS", "DGS10", "T10Y2Y", "T10YIE")


class MacroService:
    """Aggregates 3 sub-sources with per-source caching and independent
    degradation (spec §3.2). Each sub-source failure yields None for the
    corresponding MacroSnapshot field(s); the other fields are unaffected.
    """

    def __init__(
        self,
        fred_key: str,
        av_key: str,
        cg_key: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        # http=None  → we create and own the client; close() will aclose it.
        # http=client → caller injected it (typical for tests), caller owns
        #   lifecycle. Mirrors NewsService's convention. See spec §3.5 and
        #   src/integrations/news/service.py:36 for the canonical pattern.
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._cache = TTLCache()

        self._cg = CoinGeckoGlobalClient(self._http, cg_key)
        self._fred = FREDClient(self._http, fred_key)
        self._av = AlphaVantageClient(self._http, av_key)

    async def get_snapshot(self) -> MacroSnapshot:
        cg_data, fred_data, av_data = await asyncio.gather(
            self._fetch_cg(),
            self._fetch_fred_all(),
            self._fetch_av_all(),
        )
        return MacroSnapshot(
            btc_dominance=cg_data.get("btc_dominance"),
            eth_dominance=cg_data.get("eth_dominance"),
            total_mcap_usd=cg_data.get("total_mcap_usd"),
            mcap_change_24h_pct=cg_data.get("mcap_change_24h_pct"),
            usd_index_broad_tw=fred_data.get("DTWEXBGS"),
            vix=fred_data.get("VIXCLS"),
            treasury_10y=fred_data.get("DGS10"),
            spread_10y_2y=fred_data.get("T10Y2Y"),
            inflation_10y=fred_data.get("T10YIE"),
            spy=av_data.get("SPY"),
            qqq=av_data.get("QQQ"),
        )

    async def _fetch_cg(self) -> dict[str, Any]:
        """CG source; returns dict-of-None on any failure."""
        empty = {
            "btc_dominance": None, "eth_dominance": None,
            "total_mcap_usd": None, "mcap_change_24h_pct": None,
        }
        try:
            return await self._cache.get_or_fetch(
                "cg:global", _CG_TTL, self._cg.fetch_global,
            )
        except RateLimitHit:
            logger.warning("CoinGecko /global rate limited, no stale cache")
            return empty
        except Exception:
            logger.warning("CoinGecko /global fetch failed", exc_info=True)
            return empty

    async def _fetch_fred_all(self) -> dict[str, FREDObservation | None]:
        """5 FRED series in parallel — per-series degradation."""
        results = await asyncio.gather(
            *[
                self._fetch_fred_one(s)
                for s in _FRED_SERIES
            ]
        )
        return dict(zip(_FRED_SERIES, results))

    async def _fetch_fred_one(self, series_id: str) -> FREDObservation | None:
        try:
            return await self._cache.get_or_fetch(
                f"fred:{series_id}", _FRED_TTL,
                lambda sid=series_id: self._fred.fetch_latest(sid),
            )
        except RateLimitHit:
            logger.warning("FRED rate limited for %s, no stale cache", series_id)
            return None
        except Exception:
            logger.warning("FRED fetch failed for %s", series_id, exc_info=True)
            return None

    async def _fetch_av_all(self) -> dict[str, EquityQuote | None]:
        """SPY + QQQ serially (1 req/sec limit). Per-symbol TTL picked at
        call time — weekend/after-hours caches live longer (spec §5.2)."""
        result: dict[str, EquityQuote | None] = {}
        for sym in ("SPY", "QQQ"):
            ttl = alpha_vantage_ttl_seconds()
            try:
                result[sym] = await self._cache.get_or_fetch(
                    f"av:{sym}", ttl,
                    lambda s=sym: self._av.fetch_quote(s),
                )
            except RateLimitHit:
                logger.warning("AV soft rate-limited for %s, no stale cache", sym)
                result[sym] = None
            except Exception:
                logger.warning("AV fetch failed for %s", sym, exc_info=True)
                result[sym] = None
        return result

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_macro_service.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/macro/service.py tests/test_macro_service.py
git commit -m "feat(N3): add MacroService with sub-source independence"
```

---

## Task 6: Crypto ETF Models + SoSoValue Client

**Files:**
- Create: `src/integrations/crypto_etf/__init__.py` (empty)
- Create: `src/integrations/crypto_etf/models.py`
- Create: `src/integrations/crypto_etf/sosovalue.py`
- Test: `tests/test_crypto_etf_client.py`

- [ ] **Step 1: Create package init and write failing tests**

```python
# src/integrations/crypto_etf/__init__.py
```

```python
# tests/test_crypto_etf_client.py
"""Tests for SoSoValue ETF client."""
import httpx
import pytest

from src.utils.cache import RateLimitHit


# Response captured from real API (spec §2.4 smoke test).
SOSOVALUE_RESPONSE_BTC = {
    "code": 0,
    "data": [
        # 2026-04-17 has 3 rows with identical cum_net_inflow but different
        # total_net_inflow — the exact multi-row pattern cum_delta handles.
        {"date": "2026-04-17", "cum_net_inflow": 57_739_993_739.43,
         "total_net_inflow": 663_911_366.47,
         "total_net_assets": 101_450_000_000.0,
         "total_value_traded": 2_500_000_000.0},
        {"date": "2026-04-17", "cum_net_inflow": 57_739_993_739.43,
         "total_net_inflow": 996_375_546.47,
         "total_net_assets": 101_450_000_000.0,
         "total_value_traded": 2_600_000_000.0},
        {"date": "2026-04-17", "cum_net_inflow": 57_739_993_739.43,
         "total_net_inflow": 1_617_957_506.54,
         "total_net_assets": 101_450_000_000.0,
         "total_value_traded": 2_700_000_000.0},
        {"date": "2026-04-16", "cum_net_inflow": 57_076_082_372.97,
         "total_net_inflow": 26_051_070.56,
         "total_net_assets": 97_900_000_000.0,
         "total_value_traded": 2_000_000_000.0},
        {"date": "2026-04-15", "cum_net_inflow": 57_050_031_302.41,
         "total_net_inflow": 186_031_234.0,
         "total_net_assets": 97_500_000_000.0,
         "total_value_traded": 1_800_000_000.0},
    ],
}


async def test_sosovalue_returns_raw_rows():
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=SOSOVALUE_RESPONSE_BTC)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="k")
        rows = await client.fetch_summary_history("BTC")
    assert len(rows) == 5
    assert rows[0]["date"] == "2026-04-17"
    assert rows[0]["cum_net_inflow"] == 57_739_993_739.43


async def test_sosovalue_sends_required_header_and_query():
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    captured_headers: dict = {}
    captured_params: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured_headers.update(dict(req.headers))
        captured_params.update(dict(req.url.params))
        return httpx.Response(200, json=SOSOVALUE_RESPONSE_BTC)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="my-soso-key")
        await client.fetch_summary_history("BTC")
    # Lowercase hyphenated — spec §2.4 "strict case sensitivity"
    assert captured_headers.get("x-soso-api-key") == "my-soso-key"
    assert captured_params["symbol"] == "BTC"
    assert captured_params["country_code"] == "US"


async def test_sosovalue_401_raises():
    """401 is an auth problem, not a rate limit — surface as hard error.
    raise_for_status raises httpx.HTTPStatusError; the service layer catches
    it as a generic Exception and degrades to None."""
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    transport = httpx.MockTransport(lambda req: httpx.Response(401))
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="bad-key")
        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_summary_history("BTC")


async def test_sosovalue_429_raises_rate_limit():
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="k")
        with pytest.raises(RateLimitHit):
            await client.fetch_summary_history("BTC")


async def test_sosovalue_empty_data_returns_empty_list():
    from src.integrations.crypto_etf.sosovalue import SoSoValueClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"code": 0, "data": []})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = SoSoValueClient(http, api_key="k")
        rows = await client.fetch_summary_history("BTC")
    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crypto_etf_client.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement models + client**

```python
# src/integrations/crypto_etf/models.py
"""Data models for crypto spot ETF flow tracking."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ETFFlowEntry:
    """Single-day ETF flow entry.

    `net_inflow_usd` is computed from cum_net_inflow deltas (spec §5.3),
    NOT directly from SoSoValue's `total_net_inflow` field — the latter
    can differ across multi-row same-date responses.
    """
    date: str                # ISO "YYYY-MM-DD"
    net_inflow_usd: float    # signed; negative on net outflow days
    cumulative_usd: float    # cum_net_inflow at end of `date`
    aum_usd: float           # total_net_assets at end of `date`
```

```python
# src/integrations/crypto_etf/sosovalue.py
"""SoSoValue ETF summary-history API client."""
from __future__ import annotations

import httpx

from src.utils.cache import RateLimitHit

_SOSOVALUE_URL = "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history"


class SoSoValueClient:
    """Fetches per-day ETF summary rows from SoSoValue.

    Returns raw rows (list[dict]); the service layer handles the multi-row
    dedup + cum-delta computation (spec §5.3).

    Auth header is `x-soso-api-key` (lowercase, hyphenated). Other common
    spellings (X-API-KEY, Bearer) return 401 — confirmed in spec §2.4.
    """

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def fetch_summary_history(self, symbol: str) -> list[dict]:
        headers = {"x-soso-api-key": self._api_key}
        params = {"symbol": symbol, "country_code": "US"}
        resp = await self._http.get(_SOSOVALUE_URL, params=params, headers=headers)
        if resp.status_code == 429:
            raise RateLimitHit(f"SoSoValue rate limited for {symbol}")
        resp.raise_for_status()
        return resp.json().get("data", []) or []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crypto_etf_client.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/crypto_etf/__init__.py \
       src/integrations/crypto_etf/models.py \
       src/integrations/crypto_etf/sosovalue.py \
       tests/test_crypto_etf_client.py
git commit -m "feat(N3): add SoSoValue ETF client"
```

---

## Task 7: CryptoEtfService (cum-delta algorithm)

**Files:**
- Create: `src/integrations/crypto_etf/service.py`
- Test: `tests/test_crypto_etf_service.py`

This is the most algorithmically involved part of N3 — cum-delta from potentially duplicated, unordered SoSoValue rows (spec §5.3).

- [ ] **Step 1: Write failing tests — cum-delta edge cases**

```python
# tests/test_crypto_etf_service.py
"""Tests for CryptoEtfService — cum-delta algorithm (spec §5.3)."""
from unittest.mock import AsyncMock

import pytest

from src.integrations.crypto_etf.models import ETFFlowEntry


def _make_service():
    from src.integrations.crypto_etf.service import CryptoEtfService
    svc = CryptoEtfService(api_key="k", http=AsyncMock())
    svc._client = AsyncMock()
    return svc


def _row(date: str, cum: float, aum: float = 1e11, net_in: float = 0.0):
    return {
        "date": date,
        "cum_net_inflow": cum,
        "total_net_inflow": net_in,
        "total_net_assets": aum,
    }


async def test_cum_delta_simple_case():
    """Two distinct days → one flow entry."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=1100.0, aum=2e11),
        _row("2026-04-16", cum=1000.0, aum=1.9e11),
    ]
    flows = await svc.get_etf_flows("BTC", days=1)
    assert len(flows) == 1
    assert flows[0].date == "2026-04-17"
    assert flows[0].net_inflow_usd == pytest.approx(100.0)
    assert flows[0].cumulative_usd == 1100.0
    assert flows[0].aum_usd == 2e11


async def test_cum_delta_handles_multirow_same_date():
    """Multi-row dates dedup to first row; cum delta uses identical cum values.

    This reproduces real SoSoValue response (spec §2.4 smoke test):
    2026-04-17 has 3 rows, all with cum=57_739_993_739.43.
    2026-04-16 has 1 row with cum=57_076_082_372.97.
    Expected daily flow = 663_911_366.46.
    """
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=57_739_993_739.43,
             aum=101_450_000_000.0, net_in=663_911_366.47),
        _row("2026-04-17", cum=57_739_993_739.43,
             aum=101_450_000_000.0, net_in=996_375_546.47),
        _row("2026-04-17", cum=57_739_993_739.43,
             aum=101_450_000_000.0, net_in=1_617_957_506.54),
        _row("2026-04-16", cum=57_076_082_372.97,
             aum=97_900_000_000.0, net_in=26_051_070.56),
    ]
    flows = await svc.get_etf_flows("BTC", days=1)
    assert len(flows) == 1
    assert flows[0].date == "2026-04-17"
    assert flows[0].net_inflow_usd == pytest.approx(663_911_366.46, abs=1.0)
    assert flows[0].cumulative_usd == 57_739_993_739.43
    assert flows[0].aum_usd == 101_450_000_000.0


async def test_cum_delta_handles_negative_flow():
    """Outflow day: today.cum < yesterday.cum."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-13", cum=900.0),
        _row("2026-04-12", cum=1100.0),
    ]
    flows = await svc.get_etf_flows("BTC", days=1)
    assert flows[0].net_inflow_usd == pytest.approx(-200.0)


async def test_cum_delta_unordered_input_still_sorts_desc():
    """Even if API returns rows in ascending or shuffled order, service sorts."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-15", cum=1000.0),
        _row("2026-04-17", cum=1200.0),
        _row("2026-04-16", cum=1100.0),
    ]
    flows = await svc.get_etf_flows("BTC", days=2)
    assert [f.date for f in flows] == ["2026-04-17", "2026-04-16"]
    assert flows[0].net_inflow_usd == pytest.approx(100.0)
    assert flows[1].net_inflow_usd == pytest.approx(100.0)


async def test_clamp_days_above_max():
    """days > 14 is clamped to 14."""
    svc = _make_service()
    # Provide 20 distinct days to exercise upper clamp.
    rows = [_row(f"2026-04-{d:02d}", cum=1000.0 + d) for d in range(1, 21)]
    svc._client.fetch_summary_history.return_value = rows
    flows = await svc.get_etf_flows("BTC", days=30)
    assert len(flows) == 14


async def test_clamp_days_below_min():
    """days < 1 is clamped to 1."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=1100.0),
        _row("2026-04-16", cum=1000.0),
    ]
    flows = await svc.get_etf_flows("BTC", days=0)
    assert len(flows) == 1


async def test_insufficient_data_returns_empty_list():
    """Need days+1 distinct dates; fewer → empty list (spec §3.5: three-state
    contract — [] signals data-gap, distinct from None which signals outage)."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=1000.0),
    ]
    flows = await svc.get_etf_flows("BTC", days=1)
    assert flows == []


async def test_fetch_failure_returns_none():
    """Source outage → None (spec §3.5)."""
    svc = _make_service()
    svc._client.fetch_summary_history.side_effect = RuntimeError("network down")
    flows = await svc.get_etf_flows("BTC", days=7)
    assert flows is None


async def test_rate_limit_with_no_stale_returns_none():
    """RateLimitHit without stale cache → None (service outage branch)."""
    from src.utils.cache import RateLimitHit
    svc = _make_service()
    svc._client.fetch_summary_history.side_effect = RateLimitHit("429")
    flows = await svc.get_etf_flows("BTC", days=7)
    assert flows is None


async def test_cache_hit_skips_upstream_call():
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=1100.0),
        _row("2026-04-16", cum=1000.0),
    ]
    await svc.get_etf_flows("BTC", days=1)
    first_calls = svc._client.fetch_summary_history.call_count
    await svc.get_etf_flows("BTC", days=1)
    assert svc._client.fetch_summary_history.call_count == first_calls


async def test_cache_key_scoped_by_symbol():
    """BTC and ETH must not share a cache slot."""
    svc = _make_service()
    calls: list[str] = []

    async def fake_fetch(sym):
        calls.append(sym)
        return [
            _row("2026-04-17", cum=1100.0),
            _row("2026-04-16", cum=1000.0),
        ]

    svc._client.fetch_summary_history.side_effect = fake_fetch
    await svc.get_etf_flows("BTC", days=1)
    await svc.get_etf_flows("ETH", days=1)
    assert calls == ["BTC", "ETH"]


async def test_close_closes_http_when_owned():
    from src.integrations.crypto_etf.service import CryptoEtfService
    svc = CryptoEtfService(api_key="k")  # http=None → owned
    svc._http = AsyncMock()
    svc._owns_http = True
    await svc.close()
    svc._http.aclose.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_crypto_etf_service.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement CryptoEtfService**

```python
# src/integrations/crypto_etf/service.py
"""CryptoEtfService — fetches + computes daily ETF flows via cum-delta."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from src.integrations.crypto_etf.models import ETFFlowEntry
from src.integrations.crypto_etf.sosovalue import SoSoValueClient
from src.utils.cache import RateLimitHit, TTLCache

logger = logging.getLogger(__name__)

_ETF_TTL = 14400.0  # 4 hours (spec §2.4)


class CryptoEtfService:
    """Aggregates SoSoValue ETF flow data with TTLCache + cum-delta algorithm.

    Supports BTC + ETH. Exposes `get_etf_flows(symbol, days)` returning a
    list of N daily ETFFlowEntry rows (most recent first), or None on outage.
    """

    def __init__(
        self,
        api_key: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._cache = TTLCache()
        self._client = SoSoValueClient(self._http, api_key)

    async def get_etf_flows(
        self, symbol: str, days: int = 7,
    ) -> list[ETFFlowEntry] | None:
        """Compute daily flows from cumulative inflows (multi-row safe).

        SoSoValue returns multiple rows per date (Friday + most recent
        unsettled day). Smoke-test verified cum_net_inflow and
        total_net_assets are cross-row identical; only total_net_inflow
        differs. Computing delta of cum_net_inflow gives the canonical
        daily flow without depending on row ordering.
        """
        days = max(1, min(days, 14))  # clamp (spec §3.3)

        try:
            raw: list[dict] = await self._cache.get_or_fetch(
                f"etf:{symbol}", _ETF_TTL,
                lambda s=symbol: self._client.fetch_summary_history(s),
            )
        except RateLimitHit:
            logger.warning("SoSoValue rate limited for %s, no stale cache", symbol)
            return None
        except Exception:
            logger.warning("SoSoValue fetch failed for %s", symbol, exc_info=True)
            return None

        # Dedup by date — first occurrence wins. cum_net_inflow AND
        # total_net_assets are cross-row identical for same-date rows
        # (smoke-test verified), so first occurrence reflects the canonical
        # EOD values.
        seen: dict[str, dict] = {}
        for r in raw:
            date = r.get("date")
            if not date:
                continue
            seen.setdefault(date, r)

        # Descending order by date (most recent first).
        sorted_desc = sorted(seen.values(), key=lambda x: x["date"], reverse=True)

        # Need days+1 distinct dates to compute `days` deltas. This is a
        # data-gap, NOT an outage — the upstream call succeeded; the window
        # just doesn't have enough history yet. Per spec §3.5 three-state
        # contract, return [] (empty container = data-gap) rather than None
        # (reserved for source unavailability), so the tool layer can
        # render a distinct message to the agent.
        if len(sorted_desc) < days + 1:
            logger.info(
                "Insufficient data for %s ETF flows: %d rows, need %d",
                symbol, len(sorted_desc), days + 1,
            )
            return []

        flows: list[ETFFlowEntry] = []
        for i in range(days):
            today = sorted_desc[i]
            yest = sorted_desc[i + 1]
            flows.append(ETFFlowEntry(
                date=today["date"],
                net_inflow_usd=float(today["cum_net_inflow"])
                               - float(yest["cum_net_inflow"]),
                cumulative_usd=float(today["cum_net_inflow"]),
                aum_usd=float(today["total_net_assets"]),
            ))
        return flows

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_crypto_etf_service.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/crypto_etf/service.py tests/test_crypto_etf_service.py
git commit -m "feat(N3): add CryptoEtfService with cum-delta algorithm"
```

---

## Task 8: Onchain Models + DefiLlama Client

**Files:**
- Create: `src/integrations/onchain/__init__.py` (empty)
- Create: `src/integrations/onchain/models.py`
- Create: `src/integrations/onchain/defillama.py`
- Test: `tests/test_onchain_client.py`

- [ ] **Step 1: Create package init and write failing tests**

```python
# src/integrations/onchain/__init__.py
```

```python
# tests/test_onchain_client.py
"""Tests for DefiLlama stablecoins client."""
import httpx
import pytest

from src.utils.cache import RateLimitHit


DEFILLAMA_RESPONSE = {
    "peggedAssets": [
        {
            "id": "1",
            "symbol": "USDT",
            "name": "Tether",
            "circulating": {"peggedUSD": 186_620_000_000.0},
            "circulatingPrevDay": {"peggedUSD": 186_500_000_000.0},
            "circulatingPrevWeek": {"peggedUSD": 184_290_000_000.0},
        },
        {
            "id": "2",
            "symbol": "USDC",
            "name": "USD Coin",
            "circulating": {"peggedUSD": 42_180_000_000.0},
            "circulatingPrevDay": {"peggedUSD": 42_100_000_000.0},
            "circulatingPrevWeek": {"peggedUSD": 41_670_000_000.0},
        },
        {
            "id": "3",
            "symbol": "DAI",
            "name": "Dai",
            "circulating": {"peggedUSD": 5_300_000_000.0},
            "circulatingPrevDay": {"peggedUSD": 5_300_000_000.0},
            "circulatingPrevWeek": {"peggedUSD": 5_250_000_000.0},
        },
    ],
}


async def test_defillama_parse_all_assets():
    from src.integrations.onchain.defillama import DefiLlamaClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=DEFILLAMA_RESPONSE)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = DefiLlamaClient(http)
        assets = await client.fetch_stablecoins()
    assert len(assets) == 3
    by_sym = {a["symbol"]: a for a in assets}
    assert by_sym["USDT"]["circulating"]["peggedUSD"] == 186_620_000_000.0
    assert by_sym["USDC"]["circulatingPrevWeek"]["peggedUSD"] == 41_670_000_000.0


async def test_defillama_429_raises_rate_limit():
    from src.integrations.onchain.defillama import DefiLlamaClient
    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = DefiLlamaClient(http)
        with pytest.raises(RateLimitHit):
            await client.fetch_stablecoins()


async def test_defillama_empty_peggedassets_returns_empty_list():
    from src.integrations.onchain.defillama import DefiLlamaClient
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"peggedAssets": []})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = DefiLlamaClient(http)
        assets = await client.fetch_stablecoins()
    assert assets == []


# Model tests
def test_stablecoin_snapshot_fields():
    from src.integrations.onchain.models import StablecoinSnapshot
    s = StablecoinSnapshot(
        symbol="USDT",
        circulating_usd=186.62e9,
        change_7d_usd=2.33e9,
        change_7d_pct=1.27,
    )
    assert s.symbol == "USDT"
    assert s.change_7d_pct == 1.27


def test_stablecoin_total_fields():
    from src.integrations.onchain.models import StablecoinTotal
    t = StablecoinTotal(
        total_circulating_usd=319.61e9,
        total_change_7d_usd=3.85e9,
        total_change_7d_pct=1.22,
    )
    assert t.total_circulating_usd == 319.61e9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_onchain_client.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement models + client**

```python
# src/integrations/onchain/models.py
"""Data models for stablecoin supply data."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StablecoinSnapshot:
    """Single-stablecoin supply snapshot with 7d change."""
    symbol: str                    # "USDT" / "USDC"
    circulating_usd: float
    change_7d_usd: float
    change_7d_pct: float


@dataclass(frozen=True)
class StablecoinTotal:
    """Aggregate total across tracked stablecoins."""
    total_circulating_usd: float
    total_change_7d_usd: float
    total_change_7d_pct: float
```

```python
# src/integrations/onchain/defillama.py
"""DefiLlama stablecoins API client."""
from __future__ import annotations

import httpx

from src.utils.cache import RateLimitHit

_DEFILLAMA_URL = "https://stablecoins.llama.fi/stablecoins"


class DefiLlamaClient:
    """Fetches stablecoin supply snapshot from DefiLlama.

    Returns the raw `peggedAssets` list so the service layer can filter to
    the specific symbols it cares about. Response is ~250KB covering every
    tracked stablecoin across every chain; we only keep the top-level
    `circulating` / `circulatingPrevWeek` values (no auth, no rate limit).
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_stablecoins(self) -> list[dict]:
        resp = await self._http.get(_DEFILLAMA_URL)
        if resp.status_code == 429:
            raise RateLimitHit("DefiLlama rate limited")
        resp.raise_for_status()
        return resp.json().get("peggedAssets", []) or []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_onchain_client.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/onchain/__init__.py \
       src/integrations/onchain/models.py \
       src/integrations/onchain/defillama.py \
       tests/test_onchain_client.py
git commit -m "feat(N3): add DefiLlama stablecoins client"
```

---

## Task 9: OnchainService (USDT + USDC aggregation)

**Files:**
- Create: `src/integrations/onchain/service.py`
- Test: `tests/test_onchain_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_onchain_service.py
"""Tests for OnchainService — stablecoin aggregation."""
from unittest.mock import AsyncMock

import pytest


def _make_service():
    from src.integrations.onchain.service import OnchainService
    svc = OnchainService(http=AsyncMock())
    svc._client = AsyncMock()
    return svc


def _asset(symbol: str, circulating: float, prev_week: float):
    return {
        "symbol": symbol,
        "circulating": {"peggedUSD": circulating},
        "circulatingPrevWeek": {"peggedUSD": prev_week},
    }


async def test_snapshot_usdt_and_usdc():
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 186.62e9, 184.29e9),
        _asset("USDC", 42.18e9, 41.67e9),
        _asset("DAI", 5.3e9, 5.25e9),  # ignored
    ]
    result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert "USDT" in by_sym
    assert "USDC" in by_sym
    assert by_sym["USDT"].circulating_usd == pytest.approx(186.62e9)
    assert by_sym["USDT"].change_7d_usd == pytest.approx(2.33e9, abs=1e7)
    assert by_sym["USDT"].change_7d_pct == pytest.approx(1.2644, abs=0.01)


async def test_total_sums_usdt_usdc_only():
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
        _asset("USDC", 50e9, 49e9),
        _asset("DAI", 5e9, 5e9),  # excluded from total
    ]
    result = await svc.get_stablecoin_snapshot()
    total = result["total"]
    assert total.total_circulating_usd == pytest.approx(150e9)
    assert total.total_change_7d_usd == pytest.approx(3e9)
    assert total.total_change_7d_pct == pytest.approx(3e9 / 147e9 * 100, abs=0.05)


async def test_missing_symbol_skipped():
    """If DefiLlama omits USDC, we still return USDT."""
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
    ]
    result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert "USDT" in by_sym
    assert "USDC" not in by_sym


async def test_fetch_failure_returns_none():
    svc = _make_service()
    svc._client.fetch_stablecoins.side_effect = RuntimeError("down")
    result = await svc.get_stablecoin_snapshot()
    assert result is None


async def test_cache_hit_skips_upstream():
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
    ]
    await svc.get_stablecoin_snapshot()
    await svc.get_stablecoin_snapshot()
    svc._client.fetch_stablecoins.assert_awaited_once()


async def test_close_closes_http_when_owned():
    from src.integrations.onchain.service import OnchainService
    svc = OnchainService()  # http=None → owned
    svc._http = AsyncMock()
    svc._owns_http = True
    await svc.close()
    svc._http.aclose.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_onchain_service.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement OnchainService**

```python
# src/integrations/onchain/service.py
"""OnchainService — aggregates DefiLlama stablecoin supply."""
from __future__ import annotations

import logging
from typing import TypedDict

import httpx

from src.integrations.onchain.defillama import DefiLlamaClient
from src.integrations.onchain.models import StablecoinSnapshot, StablecoinTotal
from src.utils.cache import RateLimitHit, TTLCache

logger = logging.getLogger(__name__)

_STABLECOIN_TTL = 21600.0  # 6 hours (spec §2.5)
_TRACKED_SYMBOLS = ("USDT", "USDC")


class StablecoinResult(TypedDict):
    coins: list[StablecoinSnapshot]
    total: StablecoinTotal


class OnchainService:
    """Fetches stablecoin snapshot: per-symbol + aggregate."""

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._cache = TTLCache()
        self._client = DefiLlamaClient(self._http)

    async def get_stablecoin_snapshot(self) -> StablecoinResult | None:
        try:
            raw = await self._cache.get_or_fetch(
                "defillama:stablecoins", _STABLECOIN_TTL,
                self._client.fetch_stablecoins,
            )
        except RateLimitHit:
            logger.warning("DefiLlama rate limited, no stale cache")
            return None
        except Exception:
            logger.warning("DefiLlama fetch failed", exc_info=True)
            return None

        by_sym = {a.get("symbol"): a for a in raw if a.get("symbol")}

        coins: list[StablecoinSnapshot] = []
        total_circ = 0.0
        total_prev = 0.0
        for sym in _TRACKED_SYMBOLS:
            asset = by_sym.get(sym)
            if asset is None:
                continue
            circulating = float(
                (asset.get("circulating") or {}).get("peggedUSD", 0.0)
            )
            prev_week = float(
                (asset.get("circulatingPrevWeek") or {}).get("peggedUSD", 0.0)
            )
            delta = circulating - prev_week
            pct = (delta / prev_week * 100.0) if prev_week > 0 else 0.0
            coins.append(StablecoinSnapshot(
                symbol=sym,
                circulating_usd=circulating,
                change_7d_usd=delta,
                change_7d_pct=pct,
            ))
            total_circ += circulating
            total_prev += prev_week

        total_delta = total_circ - total_prev
        total_pct = (total_delta / total_prev * 100.0) if total_prev > 0 else 0.0
        total = StablecoinTotal(
            total_circulating_usd=total_circ,
            total_change_7d_usd=total_delta,
            total_change_7d_pct=total_pct,
        )

        return {"coins": coins, "total": total}

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_onchain_service.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/integrations/onchain/service.py tests/test_onchain_service.py
git commit -m "feat(N3): add OnchainService with USDT/USDC snapshot"
```

---

## Task 10: Config — MacroConfig, CryptoEtfConfig, OnchainConfig

**Files:**
- Modify: `src/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_config.py`:

```python
# --- N3 config ---

def test_macro_config_defaults():
    from src.config import MacroConfig
    cfg = MacroConfig()
    assert cfg.enabled is True
    assert cfg.fred_api_key == ""
    assert cfg.alpha_vantage_api_key == ""
    assert cfg.coingecko_demo_api_key == ""


def test_crypto_etf_config_defaults():
    from src.config import CryptoEtfConfig
    cfg = CryptoEtfConfig()
    assert cfg.enabled is True
    assert cfg.sosovalue_api_key == ""


def test_onchain_config_defaults():
    from src.config import OnchainConfig
    cfg = OnchainConfig()
    assert cfg.enabled is True


def test_settings_includes_n3_configs():
    from src.config import Settings
    s = Settings()
    assert s.macro.enabled is True
    assert s.crypto_etf.enabled is True
    assert s.onchain.enabled is True


def test_load_settings_env_overrides_n3_keys(tmp_path):
    """4 new env vars should populate config when the YAML leaves them blank."""
    from src.config import load_settings
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text("trading:\n  symbol: 'BTC/USDT:USDT'\n")
    env = {
        "FRED_API_KEY": "fred-test",
        "ALPHA_VANTAGE_API_KEY": "av-test",
        "COINGECKO_DEMO_API_KEY": "cg-test",
        "SOSOVALUE_API_KEY": "soso-test",
    }
    settings = load_settings(path=yaml_path, env_overrides=env)
    assert settings.macro.fred_api_key == "fred-test"
    assert settings.macro.alpha_vantage_api_key == "av-test"
    assert settings.macro.coingecko_demo_api_key == "cg-test"
    assert settings.crypto_etf.sosovalue_api_key == "soso-test"


def test_load_settings_yaml_overrides_n3_keys_wins(tmp_path):
    """YAML values take precedence over env vars."""
    from src.config import load_settings
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "macro:\n"
        "  fred_api_key: 'yaml-fred'\n"
    )
    env = {"FRED_API_KEY": "env-fred"}
    settings = load_settings(path=yaml_path, env_overrides=env)
    assert settings.macro.fred_api_key == "yaml-fred"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v -k "macro_config or crypto_etf_config or onchain_config or n3"`
Expected: FAIL with `ImportError` / AttributeError.

- [ ] **Step 3: Implement config classes**

In `src/config.py`, add three new config classes after `NewsConfig`:

```python
class MacroConfig(BaseModel):
    enabled: bool = True
    fred_api_key: str = ""              # env FRED_API_KEY
    alpha_vantage_api_key: str = ""     # env ALPHA_VANTAGE_API_KEY
    coingecko_demo_api_key: str = ""    # env COINGECKO_DEMO_API_KEY


class CryptoEtfConfig(BaseModel):
    enabled: bool = True
    sosovalue_api_key: str = ""         # env SOSOVALUE_API_KEY


class OnchainConfig(BaseModel):
    enabled: bool = True
```

In the `Settings` class, add three new fields:

```python
class Settings(BaseModel):
    exchange: ExchangeConfig = ExchangeConfig()
    trading: TradingConfig = TradingConfig()
    models: ModelsConfig | None = None
    scheduler: SchedulerConfig = SchedulerConfig()
    llm_budget: LLMBudgetConfig = LLMBudgetConfig()
    database: DatabaseConfig = DatabaseConfig()
    approval: ApprovalConfig = ApprovalConfig()
    alerts: AlertsConfig = AlertsConfig()
    news: NewsConfig = NewsConfig()
    macro: MacroConfig = MacroConfig()
    crypto_etf: CryptoEtfConfig = CryptoEtfConfig()
    onchain: OnchainConfig = OnchainConfig()
```

In `load_settings`, replace the function body with:

```python
def load_settings(
    path: Path = Path("config/settings.yaml"),
    env_overrides: dict[str, str] | None = None,
) -> Settings:
    if env_overrides is None:
        load_dotenv()
        env_overrides = dict(os.environ)

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    exchange = data.get("exchange", {})
    exchange.setdefault("api_key", env_overrides.get("OKX_API_KEY", ""))
    exchange.setdefault("secret", env_overrides.get("OKX_SECRET", ""))
    exchange.setdefault("password", env_overrides.get("OKX_PASSWORD", ""))
    data["exchange"] = exchange

    # N3: macro + crypto_etf env overrides (YAML values take precedence)
    macro = data.get("macro", {})
    macro.setdefault("fred_api_key", env_overrides.get("FRED_API_KEY", ""))
    macro.setdefault("alpha_vantage_api_key",
                     env_overrides.get("ALPHA_VANTAGE_API_KEY", ""))
    macro.setdefault("coingecko_demo_api_key",
                     env_overrides.get("COINGECKO_DEMO_API_KEY", ""))
    data["macro"] = macro

    crypto_etf = data.get("crypto_etf", {})
    crypto_etf.setdefault("sosovalue_api_key",
                          env_overrides.get("SOSOVALUE_API_KEY", ""))
    data["crypto_etf"] = crypto_etf

    return Settings(**data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: all PASS (existing + N3 new tests).

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat(N3): add MacroConfig, CryptoEtfConfig, OnchainConfig with env overrides"
```

---

## Task 11: Tool Implementation — `get_higher_timeframe_view`

**Files:**
- Modify: `src/agent/tools_perception.py` (append function)
- Test: `tests/test_perception_tools_n3.py` (create, HTF section)

- [ ] **Step 1: Write failing tests for `get_higher_timeframe_view`**

```python
# tests/test_perception_tools_n3.py
"""Tests for the 4 N3 perception tools."""
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from src.integrations.crypto_etf.models import ETFFlowEntry
from src.integrations.macro.models import (
    EquityQuote, FREDObservation, MacroSnapshot,
)
from src.integrations.onchain.models import StablecoinSnapshot, StablecoinTotal


@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    market_data: object = None
    exchange: object = None
    technical: object = None
    memory: object = None
    session_id: str = "test"
    db_engine: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None
    initial_balance: float = 10000.0
    metrics: object = None
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None


def _make_deps(**overrides) -> MockDeps:
    return MockDeps(**overrides)


# ===== get_higher_timeframe_view =====

def _make_ohlcv_df(n_rows: int, last_close: float = 75_234.50) -> pd.DataFrame:
    """Build a synthetic OHLCV dataframe of n_rows.

    Prices ascend linearly so MAs are deterministic; highs add +500 and
    lows subtract -500 for a stable range.

    NOTE: this shape is intentionally extreme — 100-period high always falls
    in the last row, so range position is ~92%. Tests below assert on string
    presence only, not numeric correctness of range position. If a future
    test asserts on the range-position number, replace this helper with a
    fixture that produces a less degenerate shape.
    """
    base = last_close - (n_rows - 1) * 50
    rows = []
    for i in range(n_rows):
        close = base + i * 50
        rows.append({
            "timestamp": 1_776_000_000 + i * 86_400_000,
            "open": close - 10, "high": close + 500, "low": close - 500,
            "close": close, "volume": 1000.0,
        })
    return pd.DataFrame(rows)


async def test_htf_view_format_1d():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "Higher Timeframe View (1d" in result
    assert "BTC/USDT:USDT" in result
    assert "MA50:" in result
    assert "MA100:" in result
    assert "MA200:" in result
    assert "100-period High" in result
    assert "100-period Low" in result
    assert "Current price within range" in result
    assert "20-period High" in result
    assert "20-period Low" in result
    assert "20-period range width" in result
    # Period-unit label: 1d → "days"
    assert "days ago" in result


async def test_htf_view_period_label_for_4h():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="4h")
    assert "4h-bars ago" in result


async def test_htf_view_period_label_for_1w():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="1w")
    assert "weeks ago" in result


async def test_htf_view_period_label_for_1m():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="1M")
    assert "months ago" in result


async def test_htf_view_passes_symbol_and_limit_to_market_data():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    await get_higher_timeframe_view(deps, timeframe="1d")
    market_data.get_ohlcv_dataframe.assert_awaited_once_with(
        "BTC/USDT:USDT", "1d", limit=250,
    )


async def test_htf_view_has_no_subjective_labels():
    """Spec §3.1: no 'uptrend / strong / upper third' labels — fact-only."""
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    lower = result.lower()
    for label in ("uptrend", "downtrend", "strong", "weak",
                  "bullish", "bearish", "upper third", "lower third",
                  "signals", "precedes", "follows"):
        assert label not in lower, f"found subjective label '{label}'"


async def test_htf_view_upstream_failure_degrades():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.side_effect = RuntimeError("OKX down")
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "temporarily unavailable" in result.lower()


async def test_htf_view_insufficient_data_for_ma200():
    """If fewer than 200 candles are returned, MA200 degrades but others work."""
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(150)
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "MA50:" in result
    assert "MA100:" in result
    # MA200 should appear but flagged as insufficient.
    assert "MA200" in result
    assert "insufficient data" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_perception_tools_n3.py -v -k "htf_view"`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `get_higher_timeframe_view`**

Append to `src/agent/tools_perception.py`:

```python
# Unit label for "N periods ago" rendered below range highs/lows.
_UNIT_LABEL = {"4h": "4h-bars", "1d": "days", "1w": "weeks", "1M": "months"}


async def get_higher_timeframe_view(
    deps: TradingDeps,
    timeframe: Literal["4h", "1d", "1w", "1M"],
) -> str:
    """Show long-period MAs and range position for a higher timeframe.

    Output is fact-only per spec §3.1: MA distances as percentages, range
    position as 0-100%, no labels like 'uptrend' / 'strong' / 'upper third'.
    ~250 tokens total.
    """
    symbol = deps.symbol

    try:
        df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=250)
    except Exception:
        logger.warning("HTF fetch failed for %s %s", symbol, timeframe, exc_info=True)
        return "Higher timeframe view: temporarily unavailable"

    if df.empty:
        return "Higher timeframe view: temporarily unavailable"

    last_close = float(df["close"].iloc[-1])

    sections: list[str] = [
        f"=== Higher Timeframe View ({timeframe}, {symbol}) ===",
        f"Current Price: {last_close:,.2f}",
        "",
        "=== MA Distances ===",
    ]

    def _ma(period: int) -> float | None:
        if len(df) < period:
            return None
        return float(df["close"].rolling(period).mean().iloc[-1])

    for period in (50, 100, 200):
        ma = _ma(period)
        if ma is None:
            sections.append(f"MA{period}: insufficient data (need {period} candles)")
            continue
        dist_pct = (last_close - ma) / ma * 100.0
        sections.append(
            f"MA{period}: {ma:,.2f} (price {dist_pct:+.1f}%)"
        )

    unit = _UNIT_LABEL[timeframe]

    # Range: last 100 periods. Reset index to 0-based integers so .idxmax()
    # returns a position, not a timestamp — defensive if market_data ever
    # switches to a timestamp index.
    if len(df) >= 100:
        last_100 = df.iloc[-100:].reset_index(drop=True)
        hi100_idx = int(last_100["high"].idxmax())
        lo100_idx = int(last_100["low"].idxmin())
        hi100 = float(last_100["high"].max())
        lo100 = float(last_100["low"].min())
        hi_ago = 99 - hi100_idx
        lo_ago = 99 - lo100_idx
        rng_pos = 0.0 if hi100 == lo100 else (last_close - lo100) / (hi100 - lo100) * 100.0
        sections.extend([
            "",
            "=== Range Position ===",
            f"100-period High: {hi100:,.2f} ({hi_ago} {unit} ago)",
            f"100-period Low:  {lo100:,.2f} ({lo_ago} {unit} ago)",
            f"Current price within range: {rng_pos:.1f}%",
        ])

    # 20-period band.
    if len(df) >= 20:
        last_20 = df.iloc[-20:]
        hi20 = float(last_20["high"].max())
        lo20 = float(last_20["low"].min())
        width_pct = 0.0 if lo20 == 0 else (hi20 - lo20) / lo20 * 100.0
        sections.extend([
            "",
            f"20-period High: {hi20:,.2f}",
            f"20-period Low:  {lo20:,.2f}",
            f"20-period range width: {width_pct:.1f}%",
        ])

    return "\n".join(sections)
```

Also ensure the `Literal` import is already present at the top of `tools_perception.py` (it already is — line 4).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_perception_tools_n3.py -v -k "htf_view"`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_perception_tools_n3.py
git commit -m "feat(N3): add get_higher_timeframe_view tool"
```

---

## Task 12: Tool Implementation — `get_macro_context`, `get_etf_flows`, `get_stablecoin_supply`

**Files:**
- Modify: `src/agent/tools_perception.py` (append 3 functions)
- Modify: `tests/test_perception_tools_n3.py` (append sections)

- [ ] **Step 1: Append failing tests**

```python
# ===== get_macro_context =====

def _full_snapshot() -> MacroSnapshot:
    return MacroSnapshot(
        btc_dominance=57.31, eth_dominance=10.79,
        total_mcap_usd=2.69e12, mcap_change_24h_pct=2.58,
        usd_index_broad_tw=FREDObservation("DTWEXBGS", "2026-04-10", 118.86),
        vix=FREDObservation("VIXCLS", "2026-04-16", 17.94),
        treasury_10y=FREDObservation("DGS10", "2026-04-16", 4.32),
        spread_10y_2y=FREDObservation("T10Y2Y", "2026-04-16", 0.06),
        inflation_10y=FREDObservation("T10YIE", "2026-04-16", 2.43),
        spy=EquityQuote("SPY", 710.14, 1.21, "2026-04-17"),
        qqq=EquityQuote("QQQ", 648.85, 1.31, "2026-04-17"),
    )


async def test_macro_no_service():
    from src.agent.tools_perception import get_macro_context
    deps = _make_deps(macro=None)
    result = await get_macro_context(deps)
    assert "not configured" in result.lower()


async def test_macro_full_snapshot_rendering():
    from src.agent.tools_perception import get_macro_context
    macro_svc = AsyncMock()
    macro_svc.get_snapshot.return_value = _full_snapshot()

    deps = _make_deps(macro=macro_svc)
    result = await get_macro_context(deps)

    assert "=== Crypto Market ===" in result
    assert "BTC.D: 57.31%" in result
    assert "ETH.D: 10.79%" in result
    assert "$2.69T" in result
    assert "+2.58%" in result
    assert "=== US Macro (FRED) ===" in result
    assert "USD Index (Broad TW): 118.86" in result
    assert "(as of 2026-04-10)" in result
    assert "VIX: 17.94" in result
    assert "10Y Treasury: 4.32%" in result
    assert "2s10s Spread: +0.06%" in result
    assert "10Y Inflation Expectation: 2.43%" in result
    assert "=== US Equities (Alpha Vantage) ===" in result
    assert "SPY: $710.14" in result
    assert "QQQ: $648.85" in result


async def test_macro_cg_section_unavailable_when_all_cg_fields_none():
    from src.agent.tools_perception import get_macro_context
    snap = _full_snapshot()
    snap_dict = snap.__dict__.copy()
    snap_dict.update(dict(
        btc_dominance=None, eth_dominance=None,
        total_mcap_usd=None, mcap_change_24h_pct=None,
    ))
    new_snap = MacroSnapshot(**snap_dict)

    macro_svc = AsyncMock()
    macro_svc.get_snapshot.return_value = new_snap
    deps = _make_deps(macro=macro_svc)
    result = await get_macro_context(deps)

    assert "=== Crypto Market ===" in result
    assert "temporarily unavailable" in result.lower()
    # But FRED + AV should still render
    assert "VIX: 17.94" in result
    assert "SPY: $710.14" in result


async def test_macro_all_sections_unavailable():
    from src.agent.tools_perception import get_macro_context
    snap = MacroSnapshot(
        btc_dominance=None, eth_dominance=None,
        total_mcap_usd=None, mcap_change_24h_pct=None,
        usd_index_broad_tw=None, vix=None, treasury_10y=None,
        spread_10y_2y=None, inflation_10y=None,
        spy=None, qqq=None,
    )
    macro_svc = AsyncMock()
    macro_svc.get_snapshot.return_value = snap
    deps = _make_deps(macro=macro_svc)
    result = await get_macro_context(deps)

    assert "all sources temporarily unavailable" in result.lower()


async def test_macro_has_no_subjective_labels():
    from src.agent.tools_perception import get_macro_context
    macro_svc = AsyncMock()
    macro_svc.get_snapshot.return_value = _full_snapshot()
    deps = _make_deps(macro=macro_svc)
    result = await get_macro_context(deps)

    lower = result.lower()
    for label in ("bullish", "bearish", "strong dollar", "slightly positive",
                  "risk-on", "risk-off", "moderate"):
        assert label not in lower, f"found subjective label '{label}'"


# ===== get_etf_flows =====

def _flows(days: int) -> list[ETFFlowEntry]:
    base_cum = 57_000_000_000.0
    return [
        ETFFlowEntry(
            date=f"2026-04-{17-i:02d}",
            net_inflow_usd=(i + 1) * 100_000_000.0 * ((-1) ** i),
            cumulative_usd=base_cum + (days - i) * 100_000_000.0,
            aum_usd=1.0e11,
        )
        for i in range(days)
    ]


async def test_etf_no_service():
    from src.agent.tools_perception import get_etf_flows
    deps = _make_deps(crypto_etf=None)
    result = await get_etf_flows(deps)
    assert "not configured" in result.lower()


async def test_etf_btc_and_eth_format():
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        return _flows(days)

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)

    assert "=== BTC Spot ETF Flows (US) ===" in result
    assert "=== ETH Spot ETF Flows (US) ===" in result
    assert "2026-04-17:" in result
    assert "7-day net:" in result
    assert "Note:" in result
    # Footer should include the T+1 caveat (spec §3.3)
    assert "may be revised t+1" in result.lower()


async def test_etf_btc_fails_eth_succeeds():
    """Sub-source independence: one symbol failing does not kill the other."""
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        if symbol == "BTC":
            return None
        return _flows(days)

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)

    assert "BTC Spot ETF" in result
    assert "temporarily unavailable" in result.lower()
    assert "ETH Spot ETF" in result
    assert "2026-04-17" in result


async def test_etf_both_fail():
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()
    svc.get_etf_flows.return_value = None
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)

    assert "temporarily unavailable" in result.lower()


async def test_etf_insufficient_data_renders_distinct_from_outage():
    """Service returns [] (data-gap) vs None (outage). Tool output must
    distinguish so the agent doesn't read a data-gap as a service failure
    (and vice versa). Spec §3.5 three-state contract."""
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        return [] if symbol == "BTC" else _flows(days)

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)
    lower = result.lower()

    # BTC section reports insufficient data, NOT "temporarily unavailable"
    btc_section = result.split("=== ETH")[0]
    assert "insufficient data" in btc_section.lower()
    assert "temporarily unavailable" not in btc_section.lower()
    # ETH section still rendered normally
    assert "7-day net:" in result


async def test_etf_has_no_subjective_labels():
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        return _flows(days)

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)

    lower = result.lower()
    for label in ("bullish", "bearish", "dry powder", "capital entering",
                  "institutional buying", "accumulation"):
        assert label not in lower, f"found subjective label '{label}'"


# ===== get_stablecoin_supply =====

async def test_stablecoin_no_service():
    from src.agent.tools_perception import get_stablecoin_supply
    deps = _make_deps(onchain=None)
    result = await get_stablecoin_supply(deps)
    assert "not configured" in result.lower()


async def test_stablecoin_full_format():
    from src.agent.tools_perception import get_stablecoin_supply
    svc = AsyncMock()
    svc.get_stablecoin_snapshot.return_value = {
        "coins": [
            StablecoinSnapshot("USDT", 186.62e9, 2.33e9, 1.27),
            StablecoinSnapshot("USDC", 42.18e9, 0.51e9, 1.22),
        ],
        "total": StablecoinTotal(228.80e9, 2.84e9, 1.26),
    }
    deps = _make_deps(onchain=svc)
    result = await get_stablecoin_supply(deps)

    assert "=== Stablecoin Supply ===" in result
    assert "USDT: $186.62B" in result
    assert "+$2.33B" in result
    assert "+1.27%" in result
    assert "USDC: $42.18B" in result
    assert "Total Stablecoin Mcap" in result


async def test_stablecoin_service_failure():
    from src.agent.tools_perception import get_stablecoin_supply
    svc = AsyncMock()
    svc.get_stablecoin_snapshot.return_value = None
    deps = _make_deps(onchain=svc)
    result = await get_stablecoin_supply(deps)
    assert "temporarily unavailable" in result.lower()


async def test_stablecoin_has_no_subjective_labels():
    from src.agent.tools_perception import get_stablecoin_supply
    svc = AsyncMock()
    svc.get_stablecoin_snapshot.return_value = {
        "coins": [StablecoinSnapshot("USDT", 186.62e9, 2.33e9, 1.27)],
        "total": StablecoinTotal(186.62e9, 2.33e9, 1.27),
    }
    deps = _make_deps(onchain=svc)
    result = await get_stablecoin_supply(deps)
    lower = result.lower()
    for label in ("dry powder", "capital entering", "sidelined",
                  "bullish", "bearish"):
        assert label not in lower, f"found subjective label '{label}'"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_perception_tools_n3.py -v`
Expected: FAIL with `ImportError` for the 3 new tool functions.

- [ ] **Step 3: Implement the 3 tools**

Append to `src/agent/tools_perception.py`:

```python
def _fmt_signed_dollars(v: float) -> str:
    """Format a signed dollar amount in $M or $B (spec §3.3 output format)."""
    abs_v = abs(v)
    sign = "+" if v >= 0 else "-"
    if abs_v >= 1e9:
        return f"{sign}${abs_v / 1e9:,.2f}B"
    if abs_v >= 1e6:
        return f"{sign}${abs_v / 1e6:,.2f}M"
    return f"{sign}${abs_v:,.0f}"


def _fmt_big_usd(v: float) -> str:
    """Positive-only T/B/M formatter for cumulative AUM, totals."""
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


async def get_macro_context(deps: TradingDeps) -> str:
    """Cross-market macro snapshot: crypto totals + FRED + US equities.

    Output is fact-only (spec §3.2): no 'strong dollar' / 'risk-on' labels.
    FRED values include `as of YYYY-MM-DD` so the Agent sees each reading's
    real observation date (DTWEXBGS has ~1-week report delay).
    """
    if deps.macro is None:
        return "Macro service not configured."

    try:
        snap = await deps.macro.get_snapshot()
    except Exception:
        logger.warning("Macro snapshot fetch failed", exc_info=True)
        return "Macro context: temporarily unavailable"

    sections: list[str] = []
    any_available = False

    # Crypto Market
    cg_fields = (snap.btc_dominance, snap.eth_dominance,
                 snap.total_mcap_usd, snap.mcap_change_24h_pct)
    if all(v is None for v in cg_fields):
        sections.append("=== Crypto Market ===\nTemporarily unavailable.")
    else:
        any_available = True
        btc = f"{snap.btc_dominance:.2f}%" if snap.btc_dominance is not None else "N/A"
        eth = f"{snap.eth_dominance:.2f}%" if snap.eth_dominance is not None else "N/A"
        mcap = _fmt_big_usd(snap.total_mcap_usd) if snap.total_mcap_usd else "N/A"
        chg = f"{snap.mcap_change_24h_pct:+.2f}%" if snap.mcap_change_24h_pct is not None else "N/A"
        sections.append(
            "=== Crypto Market ===\n"
            f"BTC.D: {btc} | ETH.D: {eth} | Total Mcap: {mcap} (24h: {chg})"
        )

    # US Macro (FRED)
    fred_fields = (snap.usd_index_broad_tw, snap.vix, snap.treasury_10y,
                   snap.spread_10y_2y, snap.inflation_10y)
    if all(v is None for v in fred_fields):
        sections.append("=== US Macro (FRED) ===\nTemporarily unavailable.")
    else:
        any_available = True
        lines = ["=== US Macro (FRED) ==="]
        if snap.usd_index_broad_tw is not None:
            o = snap.usd_index_broad_tw
            lines.append(f"USD Index (Broad TW): {o.value:.2f} (as of {o.date})")
        if snap.vix is not None:
            o = snap.vix
            lines.append(f"VIX: {o.value:.2f} (as of {o.date})")
        if snap.treasury_10y is not None:
            o = snap.treasury_10y
            lines.append(f"10Y Treasury: {o.value:.2f}% (as of {o.date})")
        if snap.spread_10y_2y is not None:
            o = snap.spread_10y_2y
            lines.append(f"2s10s Spread: {o.value:+.2f}% (as of {o.date})")
        if snap.inflation_10y is not None:
            o = snap.inflation_10y
            lines.append(f"10Y Inflation Expectation: {o.value:.2f}% (as of {o.date})")
        sections.append("\n".join(lines))

    # US Equities (Alpha Vantage)
    if snap.spy is None and snap.qqq is None:
        sections.append("=== US Equities (Alpha Vantage) ===\nTemporarily unavailable.")
    else:
        any_available = True
        lines = ["=== US Equities (Alpha Vantage) ==="]
        if snap.spy is not None:
            lines.append(
                f"SPY: ${snap.spy.price:,.2f} (24h: {snap.spy.change_pct:+.2f}%)"
            )
        if snap.qqq is not None:
            lines.append(
                f"QQQ: ${snap.qqq.price:,.2f} (24h: {snap.qqq.change_pct:+.2f}%)"
            )
        sections.append("\n".join(lines))

    if not any_available:
        return "Macro context: all sources temporarily unavailable"

    return "\n\n".join(sections)


async def get_etf_flows(deps: TradingDeps, days: int = 7) -> str:
    """US BTC + ETH spot ETF daily net flows + cumulative AUM.

    Emits a trailing footer reminding the Agent that today's value may be
    revised T+1 — this is an operational fact (spec §3.6) needed in-context
    to avoid misreading same-day values.
    """
    if deps.crypto_etf is None:
        return "ETF flows service not configured."

    import asyncio

    btc_result, eth_result = await asyncio.gather(
        deps.crypto_etf.get_etf_flows("BTC", days),
        deps.crypto_etf.get_etf_flows("ETH", days),
        return_exceptions=True,
    )
    btc = None if isinstance(btc_result, Exception) else btc_result
    eth = None if isinstance(eth_result, Exception) else eth_result

    def _render_section(label: str, flows) -> str:
        # Three-state rendering per spec §3.5:
        #   None → outage ("temporarily unavailable")
        #   []   → data-gap ("insufficient data" — window too short)
        #   list → normal
        if flows is None:
            return f"=== {label} Spot ETF Flows (US) ===\nTemporarily unavailable."
        if not flows:
            return (
                f"=== {label} Spot ETF Flows (US) ===\n"
                f"Insufficient data in requested window."
            )
        lines = [f"=== {label} Spot ETF Flows (US) ==="]
        net_total = 0.0
        for i, entry in enumerate(flows):
            suffix = f"  (cum: {_fmt_big_usd(entry.cumulative_usd)})" if i == 0 else ""
            lines.append(
                f"{entry.date}: {_fmt_signed_dollars(entry.net_inflow_usd)}{suffix}"
            )
            net_total += entry.net_inflow_usd
        lines.append(f"{len(flows)}-day net: {_fmt_signed_dollars(net_total)}")
        return "\n".join(lines)

    sections = [
        _render_section("BTC", btc),
        _render_section("ETH", eth),
    ]

    if btc is None and eth is None:
        return "ETF flows: temporarily unavailable"

    # Footer: operational facts the Agent needs in-context (spec §3.6).
    # The trading-day count mirrors the `days` parameter — spec §3.3 shows
    # "7" in the example because default days=7; the f-string keeps this
    # accurate when the agent requests a different window.
    sections.append(
        f"Note: Past {days} trading days (weekends/holidays excluded).\n"
        "Note: Issuer-reported; today's value may be revised T+1."
    )

    return "\n\n".join(sections)


async def get_stablecoin_supply(deps: TradingDeps) -> str:
    """USDT + USDC total supply + 7-day change.

    Output is fact-only (spec §3.4): no 'dry powder' / 'capital entering'.
    """
    if deps.onchain is None:
        return "Onchain service not configured."

    try:
        result = await deps.onchain.get_stablecoin_snapshot()
    except Exception:
        logger.warning("Stablecoin snapshot fetch failed", exc_info=True)
        return "Stablecoin supply: temporarily unavailable"

    if result is None:
        return "Stablecoin supply: temporarily unavailable"

    lines = ["=== Stablecoin Supply ==="]
    for coin in result["coins"]:
        lines.append(
            f"{coin.symbol}: {_fmt_big_usd(coin.circulating_usd)} "
            f"(7d: {_fmt_signed_dollars(coin.change_7d_usd)}, "
            f"{coin.change_7d_pct:+.2f}%)"
        )
    total = result["total"]
    lines.append(
        f"Total Stablecoin Mcap: {_fmt_big_usd(total.total_circulating_usd)} "
        f"(7d: {_fmt_signed_dollars(total.total_change_7d_usd)}, "
        f"{total.total_change_7d_pct:+.2f}%)"
    )

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_perception_tools_n3.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_perception_tools_n3.py
git commit -m "feat(N3): add get_macro_context, get_etf_flows, get_stablecoin_supply tools"
```

---

## Task 13: Tool Registration + TradingDeps + Layer 1 Prompt

**Files:**
- Modify: `src/agent/trader.py` (3 new TradingDeps fields + 4 new tool wrappers)
- Modify: `src/agent/persona.py` (4 new bullets in `_build_layer1()`)
- Modify: `tests/test_tools.py` (add 3 fields to MockDeps)
- Modify: `tests/test_news_tools.py` (add 3 fields to MockDeps)
- Modify: `tests/test_trader_agent.py` (extend `test_trader_agent_has_all_tools` with 4 new tool names)

- [ ] **Step 1: Extend `TradingDeps` in `src/agent/trader.py`**

Add three new fields to `TradingDeps` immediately after `news`:

```python
@dataclass
class TradingDeps:
    symbol: str
    timeframe: str
    market_data: MarketDataService
    exchange: BaseExchange
    technical: TechnicalAnalysisService
    memory: MemoryService
    session_id: str  # UUID from sessions table, must be explicitly set
    db_engine: object | None = None  # AsyncEngine, typed as object to avoid circular import
    approval_gate: object | None = None  # ApprovalGate instance
    approval_enabled: bool = True
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: Callable[[int], None] | None = None
    initial_balance: float = 10000.0
    metrics: object | None = None  # MetricsService, typed as object to avoid circular import
    news: object | None = None  # NewsService, typed as object to avoid circular import
    macro: object | None = None  # MacroService; typed as object to avoid circular import
    crypto_etf: object | None = None  # CryptoEtfService; typed as object to avoid circular import
    onchain: object | None = None  # OnchainService; typed as object to avoid circular import
```

- [ ] **Step 2: Register the 4 new tools**

Inside `create_trader_agent()`, add 4 tool registrations after the existing `get_derivatives_data` tool (line ~147) and before the `# === Execution Tools ===` marker:

```python
    @agent.tool
    async def get_higher_timeframe_view(
        ctx: RunContext[TradingDeps],
        timeframe: Literal["4h", "1d", "1w", "1M"],
    ) -> str:
        """Get long-period structure: MA50/100/200 distances and range position.
        timeframe: '4h' bridges LTF and 1d; '1d'/'1w'/'1M' for swing/position context.
        Output ~250 tokens. No default — explicitly pick the timeframe you need."""
        from src.agent.tools_perception import get_higher_timeframe_view as _impl

        return await _impl(ctx.deps, timeframe)

    @agent.tool
    async def get_macro_context(ctx: RunContext[TradingDeps]) -> str:
        """Get cross-market macro snapshot: BTC/ETH dominance, Total Crypto Mcap, USD
        Trade-Weighted Index (FRED DTWEXBGS; NOT ICE DXY), VIX, 10Y Treasury, 2s10s spread,
        10Y inflation expectation, and SPY/QQQ. Output ~200 tokens."""
        from src.agent.tools_perception import get_macro_context as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_etf_flows(ctx: RunContext[TradingDeps], days: int = 7) -> str:
        """Get US BTC + ETH spot ETF daily net flows + cumulative AUM for the past `days`
        trading days (1-14, default 7). Today's value may be revised T+1.
        Output ~300 tokens."""
        from src.agent.tools_perception import get_etf_flows as _impl

        return await _impl(ctx.deps, days)

    @agent.tool
    async def get_stablecoin_supply(ctx: RunContext[TradingDeps]) -> str:
        """Get USDT + USDC current total supply and 7-day change.
        Data sourced from DefiLlama (on-chain circulating supply). Output ~80 tokens."""
        from src.agent.tools_perception import get_stablecoin_supply as _impl

        return await _impl(ctx.deps)
```

- [ ] **Step 3: Update Layer 1 system prompt**

In `src/agent/persona.py` `_build_layer1()`, the last bullet `- **Derivatives structure**: ...` ends on **line 40** immediately before the closing `"""` of the function's single return-string literal. Insert 4 new bullets on new lines between that final bullet and the `"""` — i.e., the new content stays **inside** the string literal. Do NOT add content after the closing `"""` or after `return`.

Shape before the edit (current):

```python
    - **Derivatives structure**: ... long/short ratio is the ratio of long vs short account positions."""
```

Shape after the edit:

```python
    - **Derivatives structure**: ... long/short ratio is the ratio of long vs short account positions.
    - **Higher timeframe view**: ...
    - **Macro context**: ...
    - **ETF flows**: ...
    - **Stablecoin supply**: ..."""
```

Content of the 4 new bullets:

```
- **Higher timeframe view**: Use get_higher_timeframe_view with timeframe="4h"/"1d"/"1w"/"1M" to see long-period moving averages (MA50/100/200), price position within the recent 100-period range, and structural highs/lows over a longer window than your default trading timeframe.
- **Macro context**: Use get_macro_context for cross-market data — BTC/ETH dominance, Total Crypto Market Cap (CoinGecko), USD Trade-Weighted Index (FRED DTWEXBGS — note: this is the Fed's broad TW index across 26 currencies, NOT the ICE DXY across 6 currencies; absolute values differ but directional movement is highly correlated), VIX, 10Y Treasury yield, 2s10s spread, 10Y inflation expectation (FRED), and SPY/QQQ closing quotes (Alpha Vantage). FRED data has daily granularity; SPY/QQQ are equity ETFs with NYSE trading-hour quotes.
- **ETF flows**: Use get_etf_flows for daily net flow data of US-traded BTC and ETH spot ETFs over the past 7 days, plus cumulative AUM. Today's value may be revised T+1.
- **Stablecoin supply**: Use get_stablecoin_supply for current USDT/USDC total supply and 7-day changes, sourced from on-chain data via DefiLlama.
```

- [ ] **Step 4: Update MockDeps in existing test files**

In `tests/test_tools.py`, find the `MockDeps` dataclass and append 3 new fields after `news`:

```python
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None
```

In `tests/test_news_tools.py`, find the `MockDeps` dataclass and append the same 3 fields. Both MockDeps are local test helpers; they must stay in sync with the real `TradingDeps` signature.

Then extend `tests/test_trader_agent.py::test_trader_agent_has_all_tools` to assert that the 4 new tool names are registered. Add after the existing `assert "set_next_wake" in tool_names` line:

```python
    # N3 perception tools
    assert "get_higher_timeframe_view" in tool_names
    assert "get_macro_context" in tool_names
    assert "get_etf_flows" in tool_names
    assert "get_stablecoin_supply" in tool_names
```

N2 did not add its 3 tools to this assertion list (confirmed by inspecting the current file). That was a silent regression gap; N3 fixes it for its 4 new tools without re-auditing N2's gap (out of scope here — a dedicated cleanup PR can backfill if anyone cares).

**Optional low-cost backfill**: if the subagent executing this task notices the 3 missing N2 tool assertions (`get_market_news`, `get_critical_alerts`, `get_derivatives_data`), adding them is 3 lines with zero risk and can go in the same commit. Skip if any test surprises arise — the backfill is a nice-to-have, not load-bearing for N3.

**A third MockDeps exists at `tests/test_tool_enhancement.py:247`** — intentionally left unchanged. That helper was already out of sync with N2 (missing `news`) because its tests do not exercise any deps field beyond `market_data`, `exchange`, `technical`, `memory`. N3 tools are not reachable through the code paths it tests, so adding fields there would be dead defensive work. If a future refactor makes that file touch an N3 tool, the MockDeps definition will be caught at that time.

- [ ] **Step 5: Run the whole test suite**

Run: `pytest --tb=short -q`
Expected: all tests pass (no regressions, all new tests pass).

- [ ] **Step 6: Commit**

```bash
git add src/agent/trader.py src/agent/persona.py \
       tests/test_tools.py tests/test_news_tools.py tests/test_trader_agent.py
git commit -m "feat(N3): register 4 macro tools, extend TradingDeps, update Layer 1 prompt"
```

---

## Task 14: App Integration — `build_services` + Shutdown Ordering

**Files:**
- Modify: `src/cli/app.py`

Spec §5.7 defines the exact insertion pattern: services are built after `news_service`, injected into `TradingDeps`, and closed in the `finally` block in a specific order. The shutdown order is load-bearing because it mirrors fix commit `9a81663` (`fix(N2): critical — ForexFactory UTC normalization + app shutdown cleanup`), which established the contract that each owned httpx client is closed inside the main finally block.

- [ ] **Step 1: Build 3 new services in `build_services`**

In `src/cli/app.py`, locate the news-service block at line ~285 and add 3 new service constructions immediately after it. Replace the block:

```python
    # News service — all upstream sources are keyless (CoinDesk, FGI, ForexFactory, OKX).
    news_service = None
    if settings.news.enabled:
        from src.integrations.news.service import NewsService
        news_service = NewsService()
        sc.print("News: ON (CoinDesk News + FGI + alerts)")
    else:
        sc.print("News: OFF")
```

With:

```python
    # News service — all upstream sources are keyless (CoinDesk, FGI, ForexFactory, OKX).
    news_service = None
    if settings.news.enabled:
        from src.integrations.news.service import NewsService
        news_service = NewsService()
        sc.print("News: ON (CoinDesk News + FGI + alerts)")
    else:
        sc.print("News: OFF")

    # N3: Macro service — CoinGecko /global + FRED + Alpha Vantage.
    macro_service = None
    if settings.macro.enabled:
        from src.integrations.macro.service import MacroService
        macro_service = MacroService(
            fred_key=settings.macro.fred_api_key,
            av_key=settings.macro.alpha_vantage_api_key,
            cg_key=settings.macro.coingecko_demo_api_key,
        )
        sc.print("Macro: ON (FRED + Alpha Vantage + CoinGecko)")
    else:
        sc.print("Macro: OFF")

    # N3: Crypto ETF service — SoSoValue.
    crypto_etf_service = None
    if settings.crypto_etf.enabled:
        from src.integrations.crypto_etf.service import CryptoEtfService
        crypto_etf_service = CryptoEtfService(
            api_key=settings.crypto_etf.sosovalue_api_key,
        )
        sc.print("Crypto ETF: ON (SoSoValue)")
    else:
        sc.print("Crypto ETF: OFF")

    # N3: Onchain service — DefiLlama stablecoins.
    onchain_service = None
    if settings.onchain.enabled:
        from src.integrations.onchain.service import OnchainService
        onchain_service = OnchainService()
        sc.print("Onchain: ON (DefiLlama stablecoins)")
    else:
        sc.print("Onchain: OFF")
```

- [ ] **Step 2: Inject into `TradingDeps`**

Replace the existing `deps = TradingDeps(...)` constructor call (~line 294) with:

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
    )
```

- [ ] **Step 3: Add shutdown close calls**

Locate the `finally:` block at line ~461 that closes `exchange` and `deps.news`. Extend it with three new close calls after the news close. Replace the block:

```python
    finally:
        try:
            await exchange.close()
        except Exception:
            logger.warning("Failed to close exchange", exc_info=True)
        if deps.news is not None:
            try:
                await deps.news.close()
            except Exception:
                logger.warning("Failed to close news service", exc_info=True)
```

With:

```python
    finally:
        try:
            await exchange.close()
        except Exception:
            logger.warning("Failed to close exchange", exc_info=True)
        if deps.news is not None:
            try:
                await deps.news.close()
            except Exception:
                logger.warning("Failed to close news service", exc_info=True)
        if deps.macro is not None:
            try:
                await deps.macro.close()
            except Exception:
                logger.warning("Failed to close macro service", exc_info=True)
        if deps.crypto_etf is not None:
            try:
                await deps.crypto_etf.close()
            except Exception:
                logger.warning("Failed to close crypto_etf service", exc_info=True)
        if deps.onchain is not None:
            try:
                await deps.onchain.close()
            except Exception:
                logger.warning("Failed to close onchain service", exc_info=True)
```

Order rationale (spec §5.7): `exchange → news → macro → crypto_etf → onchain`. All 3 new closes are inside the `finally` block (before the session status update, which happens outside), mirroring the news close pattern established by fix commit `9a81663` (`fix(N2): critical — ForexFactory UTC normalization + app shutdown cleanup`). Putting them outside the `finally` would skip cleanup on exception paths — exactly the failure mode 9a81663 addressed.

- [ ] **Step 4: Run the whole test suite**

Run: `pytest --tb=short -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/cli/app.py
git commit -m "feat(N3): wire MacroService, CryptoEtfService, OnchainService into app lifecycle"
```

---

## Task 14.5: Integration Tests — Wiring + Lifecycle

**Files:**
- Create: `tests/test_n3_wiring.py` — asserts `build_services` instantiates / skips the 3 services per settings and injects them into `TradingDeps`
- Create: `tests/test_app_lifecycle_n3.py` — asserts shutdown close calls are resilient (don't raise on exception) and happen in the spec-mandated order

Spec §8.3 explicitly requires these two files. They validate the §5.7 integration contract at the wiring level rather than reaching into private fields across unit tests.

- [ ] **Step 1: Write `tests/test_n3_wiring.py`**

```python
"""Integration tests: build_services wires N3 services per settings (spec §8.3).

These tests call build_services with a REAL WizardResult (not MagicMock) so
they stay correct under a future refactor that adds isinstance checks or
dataclass-level validation. The `model` field is set to "test" and
ALLOW_MODEL_REQUESTS is disabled so pydantic-ai does not attempt to resolve
or construct an AnthropicModel for the agent constructed inside
build_services (see tests/test_trader_agent.py:4 for the same guard).
"""
from unittest.mock import MagicMock

import pytest
from pydantic_ai import models

from src.cli.wizard import WizardResult
from src.config import (
    AlertsConfig, ApprovalConfig, CryptoEtfConfig, DatabaseConfig,
    ExchangeConfig, LLMBudgetConfig, MacroConfig, ModelRouting, ModelsConfig,
    NewsConfig, OnchainConfig, PersonaConfig, SchedulerConfig, Settings,
    TradingConfig,
)
from src.services.model_manager import ModelConfig

models.ALLOW_MODEL_REQUESTS = False


def _make_settings(
    macro_enabled: bool = True,
    etf_enabled: bool = True,
    onchain_enabled: bool = True,
    news_enabled: bool = False,
) -> Settings:
    return Settings(
        exchange=ExchangeConfig(),
        trading=TradingConfig(),
        models=ModelsConfig(routing=ModelRouting()),
        scheduler=SchedulerConfig(),
        llm_budget=LLMBudgetConfig(),
        database=DatabaseConfig(),
        approval=ApprovalConfig(),
        alerts=AlertsConfig(enabled=False),
        news=NewsConfig(enabled=news_enabled),
        macro=MacroConfig(
            enabled=macro_enabled, fred_api_key="k",
            alpha_vantage_api_key="k", coingecko_demo_api_key="k",
        ),
        crypto_etf=CryptoEtfConfig(enabled=etf_enabled, sosovalue_api_key="k"),
        onchain=OnchainConfig(enabled=onchain_enabled),
    )


def _make_result() -> WizardResult:
    """Minimal real WizardResult — all fields explicit so future dataclass
    validation (e.g., __post_init__ checks) surfaces here rather than
    silently passing via MagicMock attribute access."""
    return WizardResult(
        exchange_type="simulated",
        fee_rate=0.001,
        initial_balance=10_000.0,
        api_credentials=None,
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        # ModelConfig is a dataclass with 5 str fields; values don't matter
        # here because build_services only reads result.model (the sentinel
        # below) when constructing the agent.
        model_config=ModelConfig(
            id="test", provider="test", model="test", api_key="", base_url=None,
        ),
        # "test" sentinel lets pydantic-ai build a harness agent without
        # touching Anthropic SDK construction (see ALLOW_MODEL_REQUESTS=False
        # above and tests/test_trader_agent.py:11).
        model="test",
        scheduler_interval_min=15,
        approval_enabled=False,
        alert_enabled=False,
        alert_window_min=60,
        alert_threshold_pct=5.0,
        token_budget=1_000_000,
        persona=PersonaConfig(),
        session_name="test-session",
    )


async def test_build_services_all_n3_enabled():
    from src.cli.app import build_services
    from src.integrations.crypto_etf.service import CryptoEtfService
    from src.integrations.macro.service import MacroService
    from src.integrations.onchain.service import OnchainService

    settings = _make_settings(True, True, True)
    result = _make_result()
    exchange, deps, agent, budget = build_services(
        result, MagicMock(), "sid", MagicMock(), settings,
    )
    try:
        assert isinstance(deps.macro, MacroService)
        assert isinstance(deps.crypto_etf, CryptoEtfService)
        assert isinstance(deps.onchain, OnchainService)
    finally:
        # build_services constructs owned httpx clients. Close them so the
        # test does not leak file descriptors between runs.
        await deps.macro.close()
        await deps.crypto_etf.close()
        await deps.onchain.close()


async def test_build_services_macro_disabled():
    from src.cli.app import build_services

    settings = _make_settings(macro_enabled=False)
    exchange, deps, agent, budget = build_services(
        _make_result(), MagicMock(), "sid", MagicMock(), settings,
    )
    try:
        assert deps.macro is None
        # Siblings still on — independent toggles
        assert deps.crypto_etf is not None
        assert deps.onchain is not None
    finally:
        await deps.crypto_etf.close()
        await deps.onchain.close()


async def test_build_services_all_n3_disabled():
    from src.cli.app import build_services

    settings = _make_settings(False, False, False)
    exchange, deps, agent, budget = build_services(
        _make_result(), MagicMock(), "sid", MagicMock(), settings,
    )
    assert deps.macro is None
    assert deps.crypto_etf is None
    assert deps.onchain is None


async def test_build_services_crypto_etf_disabled_leaves_others_on():
    from src.cli.app import build_services

    settings = _make_settings(macro_enabled=True, etf_enabled=False,
                              onchain_enabled=True)
    exchange, deps, agent, budget = build_services(
        _make_result(), MagicMock(), "sid", MagicMock(), settings,
    )
    try:
        assert deps.crypto_etf is None
        assert deps.macro is not None
        assert deps.onchain is not None
    finally:
        await deps.macro.close()
        await deps.onchain.close()


async def test_build_services_onchain_disabled_leaves_others_on():
    from src.cli.app import build_services

    settings = _make_settings(macro_enabled=True, etf_enabled=True,
                              onchain_enabled=False)
    exchange, deps, agent, budget = build_services(
        _make_result(), MagicMock(), "sid", MagicMock(), settings,
    )
    try:
        assert deps.onchain is None
        assert deps.macro is not None
        assert deps.crypto_etf is not None
    finally:
        await deps.macro.close()
        await deps.crypto_etf.close()
```

- [ ] **Step 2: Write `tests/test_app_lifecycle_n3.py`**

```python
"""Source-level regression guard for N3 service shutdown (spec §8.3 + §5.7).

Rationale: a replay-style unit test of the finally block tests a copy of
the logic, not src/cli/app.py itself — it gives zero regression protection
if someone later deletes `await deps.macro.close()` from the real file.
Instead, we assert directly on the source of src/cli/app.py:
  1. All 5 expected close() calls are present.
  2. They appear in the spec §5.7-mandated order.
  3. Each N3 close is preceded by a `try:` (so it is exception-wrapped).

This is brittle to source reformatting (e.g., using a class method instead
of a bare expression), but that brittleness is intentional — any refactor
of the shutdown block is exactly the change that should force this test to
be re-read. If the shutdown moves to a helper function, update both
app.py and this test together.
"""
from pathlib import Path

import pytest


_APP_PATH = Path(__file__).resolve().parent.parent / "src" / "cli" / "app.py"


def _source() -> str:
    return _APP_PATH.read_text()


def _find_line(source: str, needle: str) -> int:
    for i, line in enumerate(source.splitlines()):
        if needle in line:
            return i
    raise AssertionError(f"not found in src/cli/app.py: {needle!r}")


EXPECTED_CLOSE_ORDER = (
    "exchange.close()",
    "deps.news.close()",
    "deps.macro.close()",
    "deps.crypto_etf.close()",
    "deps.onchain.close()",
)


def test_all_expected_close_calls_present():
    source = _source()
    for call in EXPECTED_CLOSE_ORDER:
        assert call in source, f"Missing shutdown call: {call}"


def test_close_calls_in_spec_mandated_order():
    """Spec §5.7: exchange → news → macro → crypto_etf → onchain."""
    source = _source()
    line_numbers = [_find_line(source, call) for call in EXPECTED_CLOSE_ORDER]
    assert line_numbers == sorted(line_numbers), (
        f"Close calls out of order. Got line numbers {line_numbers} for "
        f"{EXPECTED_CLOSE_ORDER}"
    )


@pytest.mark.parametrize("close_call", [
    "deps.macro.close()",
    "deps.crypto_etf.close()",
    "deps.onchain.close()",
])
def test_n3_close_is_wrapped_in_try_except(close_call: str):
    """Each N3 close must live inside a try/except so a failing close
    does not abort cleanup of siblings (spec §5.7 'per-service try/except').

    Heuristic: within 5 lines before the close call, there should be a
    `try:` on its own line. This matches the N2 pattern for deps.news.close().
    """
    source = _source()
    lines = source.splitlines()
    line_no = _find_line(source, close_call)
    window = lines[max(0, line_no - 5):line_no]
    assert any(L.strip() == "try:" for L in window), (
        f"{close_call} is not preceded by a `try:` within 5 lines — "
        f"window was:\n" + "\n".join(window)
    )


def test_n3_close_calls_inside_finally_block():
    """The N3 closes must be inside the outer finally block (not after it,
    where they would not run on the exception path).

    Heuristic: the last `finally:` occurring BEFORE `deps.macro.close()`
    must also occur BEFORE `deps.onchain.close()`, and there must be no
    intervening `scheduler_task =` line (which signals re-entry into the
    try body above).
    """
    source = _source()
    macro_line = _find_line(source, "deps.macro.close()")
    onchain_line = _find_line(source, "deps.onchain.close()")
    lines = source.splitlines()
    finally_candidates = [
        i for i, L in enumerate(lines)
        if L.strip() == "finally:" and i < macro_line
    ]
    assert finally_candidates, "No `finally:` found before N3 closes"
    last_finally = max(finally_candidates)
    between = "\n".join(lines[last_finally:onchain_line])
    assert "scheduler_task =" not in between, (
        "N3 closes do not appear to live inside the expected finally block"
    )
```

**Why source-level instead of replay**: a replay test duplicates the finally-block logic into the test file and then asserts on its own behavior. That gives zero regression coverage for `src/cli/app.py` — if someone later deletes `await deps.macro.close()` from app.py, the replay test still passes. The source-grep approach above asserts directly on the real file and will fail the moment the finally block is altered in a way that breaks the spec §5.7 contract.

**Caveats of source-level testing** (documented so the subagent does not try to "improve" them away):
- Brittle to harmless refactors (e.g., extracting shutdown into a helper function). That brittleness is intentional — any shutdown refactor is exactly the change that should force a human to re-read this test.
- Does not verify runtime behavior. Per-service `close()` resilience is already covered by `test_close_closes_http_when_owned` in each service's unit-test file. This file is specifically about the app.py-level contract, not service-level internals.

- [ ] **Step 3: Run the new test files**

Run: `pytest tests/test_n3_wiring.py tests/test_app_lifecycle_n3.py -v`
Expected: all PASS.

- [ ] **Step 4: Run the full test suite**

Run: `pytest --tb=short -q`
Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add tests/test_n3_wiring.py tests/test_app_lifecycle_n3.py
git commit -m "feat(N3): add wiring and lifecycle integration tests"
```

---

## Task 15: Final Verification & Test Suite Run

- [ ] **Step 1: Run the full test suite one more time**

Run: `pytest --tb=short -v`
Expected: all tests pass. N3 should add ~100 new tests (spec §8.4):
- `test_macro_models.py`: ~5
- `test_macro_clients.py`: ~15 (6 FRED + 4 CG + 6 AV)
- `test_av_time_of_day_cache.py`: ~8
- `test_macro_service.py`: ~7
- `test_crypto_etf_client.py`: ~5
- `test_crypto_etf_service.py`: ~12 (incl. rate-limit and insufficient-data branches)
- `test_onchain_client.py`: ~5
- `test_onchain_service.py`: ~6
- `test_perception_tools_n3.py`: ~26 (HTF 8 + macro 5 + etf 6 + stablecoin 4 + no-label greps 3)
- `test_config.py`: +6
- `test_n3_wiring.py`: ~5
- `test_app_lifecycle_n3.py`: ~6 (1 presence + 1 order + 3 parametrized try-wrap + 1 finally-scope)
- `test_trader_agent.py`: +4 (tool-registration assertions)

- [ ] **Step 2: Check git log**

Run: `git log --oneline -n 20`
Expected: 15 commits (Tasks 1, 2, …, 14, 14.5) all prefixed `feat(N3):`.

- [ ] **Step 3: If any final cleanup is needed, commit**

```bash
git status
# If clean, skip. Otherwise:
git add -A
git commit -m "feat(N3): final integration cleanup"
```

---

## Post-implementation notes (for the reviewer)

- **AV daily-call counter metric (spec §12.1)**: deferred to a follow-up PR — see "Design Deviations from Spec" at the top of this plan for the rationale.
- **Spec §9.3 PR split decision**: the user decides whether to ship N3 as one PR or split into (A) macro+HTF and (B) ETF+stablecoin. The plan commits are already organized so either split is viable: Tasks 1-5 + 11 + relevant slices of 13-14 = PR-A; Tasks 6-10 + 12 + relevant slices of 13-14 = PR-B. Do NOT pre-split without explicit user instruction.
- **Spec §12.2 DST edge case**: `alpha_vantage_ttl_seconds` uses `ZoneInfo("America/New_York")` which auto-handles DST. No special handling needed.
