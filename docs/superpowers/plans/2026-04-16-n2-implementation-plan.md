# N2: Market Intelligence Tools — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three perception tools (`get_market_news`, `get_critical_alerts`, `get_derivatives_data`) so the trading agent can see news, exchange announcements, macro events, and derivatives market structure.

**Architecture:** Bottom-up build: shared cache utility → 5 independent HTTP API clients → NewsService aggregator → BaseExchange derivatives abstraction → exchange implementations → MarketDataService cached methods → tool functions → tool registration + system prompt → app wiring + wizard. All news/alert data flows through NewsService (httpx); all derivatives data flows through BaseExchange→MarketDataService (ccxt). Both paths use a shared TTLCache with 429-aware stale-cache fallback.

**Tech Stack:** Python 3.12, httpx (async HTTP), ccxt (exchange API), pytest + pytest-asyncio, dataclasses

**Spec:** `docs/superpowers/specs/2026-04-16-n2-market-news-design.md`

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/utils/__init__.py` | Create | Package init |
| `src/utils/cache.py` | Create | `TTLCache` + `RateLimitHit` — shared TTL cache with 429 stale-fallback |
| `src/integrations/news/__init__.py` | Create | Package init |
| `src/integrations/news/models.py` | Create | `InformationEvent` dataclass + `extract_base_currency()` |
| `src/integrations/news/coindesk.py` | Create | CoinDesk Data News API client — news headlines with sentiment |
| `src/integrations/news/fear_greed.py` | Create | Alternative.me FGI client |
| `src/integrations/news/calendar.py` | Create | ForexFactory macro calendar client |
| `src/integrations/news/okx_announcements.py` | Create | OKX `/support/announcements` client |
| `src/integrations/news/okx_status.py` | Create | OKX `/system/status` client |
| `src/integrations/news/service.py` | Create | `NewsService` — aggregation + caching (no quota/keys; all sources are keyless) |
| `src/integrations/exchange/base.py` | Modify | Add `FundingRate`, `OpenInterest`, `LongShortRatio` dataclasses + 3 abstract methods |
| `src/integrations/exchange/okx.py` | Modify | Implement 3 derivatives methods with `@_retry()` |
| `src/integrations/exchange/simulated.py` | Modify | Implement 3 derivatives methods via `self._ccxt` |
| `src/integrations/market_data.py` | Modify | Add `get_funding_rate()`, `get_open_interest()`, `get_long_short_ratio()` with TTLCache |
| `src/config.py` | Modify | Add `NewsConfig` + wire into `Settings` |
| `src/agent/tools_perception.py` | Modify | Add `get_market_news()`, `get_critical_alerts()`, `get_derivatives_data()` |
| `src/agent/trader.py` | Modify | Add `news` field to `TradingDeps`, register 3 new tool wrappers |
| `src/agent/persona.py` | Modify | Add 3-tool guidance to Layer 1 |
| `src/cli/app.py` | Modify | Initialize `NewsService`, inject into deps, close on shutdown |
| `src/cli/wizard.py` | (no change) | All news sources are keyless — no new wizard step |
| `tests/test_cache.py` | Create | TTLCache unit tests |
| `tests/test_news_clients.py` | Create | All 5 client unit tests |
| `tests/test_news_service.py` | Create | NewsService integration tests |
| `tests/test_derivatives_data.py` | Create | Derivatives types + exchange + MarketDataService tests |
| `tests/test_news_tools.py` | Create | Tool implementation tests |
| `tests/test_config.py` | Modify | Add `NewsConfig` tests |
| `tests/test_tools.py` | Modify | Add `news` field to `MockDeps` |

---

## Pre-work Verification (run before Task 1)

Pre-work was completed on 2026-04-17. Findings are recorded here so the implementer can validate assumptions without re-running everything. Re-run the curls if any significant time has passed.

- [x] **P1: httpx is in pyproject.toml** — `httpx>=0.27` already declared (line 15). No `uv add` needed.

- [x] **P2: CoinDesk Data News API verified**
   ```bash
   curl -s "https://data-api.coindesk.com/news/v1/article/list?lang=EN&limit=2" | jq '.Data[0] | {TITLE, PUBLISHED_ON, URL, source: .SOURCE_DATA.NAME, categories: [.CATEGORY_DATA[].NAME], SENTIMENT}'
   ```
   **Confirmed fields**: `TITLE`, `PUBLISHED_ON` (Unix 秒), `URL`, `SOURCE_DATA.NAME`, `CATEGORY_DATA[].NAME`, `SENTIMENT` ∈ {"POSITIVE", "NEGATIVE", "NEUTRAL"}.
   **Confirmed params**: `lang=EN`, `limit=N`, `categories=BTC`, `sentiment=POSITIVE|NEGATIVE|NEUTRAL`.
   **No key needed, no observed rate limit** (10 consecutive 200s, no X-RateLimit-* headers).
   **`SCORE` is always 0** (don't rely on it for sort; `news_filter` omits `trending`).

- [x] **P3: ForexFactory calendar feed verified**
   ```bash
   curl -s "https://nfs.faireconomy.media/ff_calendar_thisweek.json" | jq '.[0] | {title, country, date, impact, forecast, previous}'
   ```
   All spec fields present (`title`, `country`, `date`, `impact`, `forecast`, `previous`); this week had 108 entries, 8 USD High/Medium after local filter.

- [x] **P4a: OKX `/support/announcements` verified (nested schema confirmed)**
   ```bash
   curl -s "https://www.okx.com/api/v5/support/announcements?annType=announcements-delistings" | jq '.data[0].details[0] | {annType, title, url, pTime, businessPTime}'
   ```
   **Schema**: items live under `response.data[0].details[*]`, **not** `response.data[*]`. Field names inside each item match spec (`annType`, `title`, `url`, `pTime`, `businessPTime`). Task 3's `OKXAnnouncementsClient.fetch()` must parse this nesting — mock fixtures and the `_parse()` code below both use `data[0].details[*]`.

- [ ] **P4b: OKX `/system/status` schema validation (BLOCKER — must run before Task 3 Step 5)**

   Initial probe returned empty (no live scheduled maintenance), so nesting is unconfirmed:
   ```bash
   curl -s "https://www.okx.com/api/v5/system/status?state=scheduled" | jq '.data[0] // "no scheduled maintenance"'
   ```

   Re-run with `state=completed` before implementing `OKXStatusClient` — completed maintenance is always archived:
   ```bash
   curl -s "https://www.okx.com/api/v5/system/status?state=completed" | jq '.data[0] | to_entries | map({key, type: (.value | type)})'
   ```
   - If the response is **flat** `data[*]` with fields `title / state / begin / end / maintType / serviceType / system`: the plan's `OKXStatusClient.fetch()` below works as-is.
   - If the response is **nested** `data[0].details[*]` like `/support/announcements`: apply the same nested-parse pattern to `OKXStatusClient.fetch()` (extract `data[0].get("details", [])` before iterating) and update the `OKX_STATUS_RESPONSE` test fixture to match.

- [ ] **P5: ccxt OKX derivatives methods (MUST run before Task 5)**

   ccxt is Python-only, not curl-verifiable. The `has` table is ccxt's declared capability — not a runtime guarantee, so also make an actual call. Run before Task 5:

   ```bash
   python - <<'PY'
   import asyncio
   import ccxt.async_support as ccxt

   async def main():
       ex = ccxt.okx()
       try:
           # 1. declarative capability
           has = {k: ex.has.get(k) for k in [
               'fetchFundingRate', 'fetchOpenInterest',
               'fetchLongShortRatio', 'fetchLongShortRatioHistory',
           ]}
           print("has:", has)

           # 2. runtime probes — each should return data, not raise NotSupported
           fr = await ex.fetch_funding_rate('BTC/USDT:USDT')
           print("funding_rate OK:", fr.get('fundingRate'))

           oi = await ex.fetch_open_interest('BTC/USDT:USDT')
           print("open_interest OK:", oi.get('openInterestValue'))

           lsr = await ex.fetch_long_short_ratio_history('BTC/USDT:USDT', '5m', limit=1)
           print("lsr_history OK:", (lsr[0] if lsr else 'empty'))
       finally:
           await ex.close()

   asyncio.run(main())
   PY
   ```

   **Expected `has` table**: `{'fetchFundingRate': True, 'fetchOpenInterest': True, 'fetchLongShortRatio': False, 'fetchLongShortRatioHistory': True}`.
   **Expected runtime**: all three calls return data without `ccxt.NotSupported` / `ccxt.ExchangeError`.
   - If `fetchLongShortRatio` capability becomes `True` AND the simpler call works: Task 5 can simplify — call `fetch_long_short_ratio(symbol)` directly.
   - If `fetch_long_short_ratio_history` raises `NotSupported` at runtime (regardless of `has`): blocker — plan needs revision (fallback to OKX REST `/api/v5/rubik/stat/contracts/long-short-account-ratio-contract`).

**P1–P4 done; P5 deferred to Task 5 prep (requires Python env).** Proceed to Task 1.

---

### Task 1: Infrastructure — Data Model + Cache Utility

**Files:**
- Create: `src/integrations/news/__init__.py`, `src/integrations/news/models.py`
- Create: `src/utils/__init__.py`, `src/utils/cache.py`
- Test: `tests/test_cache.py` (includes InformationEvent + extract_base_currency + TTLCache tests)

- [ ] **Step 1: Write tests for InformationEvent and extract_base_currency**

```python
# tests/test_cache.py
"""Tests for TTLCache and InformationEvent model."""
import asyncio

import pytest
from unittest.mock import AsyncMock


# --- InformationEvent + extract_base_currency ---

def test_information_event_creation():
    from datetime import datetime, timezone
    from src.integrations.news.models import InformationEvent

    event = InformationEvent(
        timestamp=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        source="coindesk",
        category="news",
        importance="medium",
        title="Test headline",
    )
    assert event.source == "coindesk"
    assert event.symbols == []
    assert event.content == ""
    assert event.url == ""


def test_information_event_with_all_fields():
    from datetime import datetime, timezone
    from src.integrations.news.models import InformationEvent

    event = InformationEvent(
        timestamp=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        source="coindesk",
        category="news",
        importance="high",
        title="BTC Rally",
        content="CoinDesk",
        url="https://example.com",
        symbols=["BTC", "ETH"],
    )
    assert event.symbols == ["BTC", "ETH"]
    assert event.content == "CoinDesk"


def test_extract_base_currency_btc():
    from src.integrations.news.models import extract_base_currency
    assert extract_base_currency("BTC/USDT:USDT") == "BTC"


def test_extract_base_currency_eth():
    from src.integrations.news.models import extract_base_currency
    assert extract_base_currency("ETH/USDT:USDT") == "ETH"


def test_extract_base_currency_sol():
    from src.integrations.news.models import extract_base_currency
    assert extract_base_currency("SOL/USDT:USDT") == "SOL"


def test_extract_base_currency_strips_1000_prefix():
    """OKX uses 1000PEPE / 1000SHIB multiplier contracts — strip so CoinDesk
    CATEGORY_DATA match still works (CoinDesk tags use PEPE, not 1000PEPE)."""
    from src.integrations.news.models import extract_base_currency
    assert extract_base_currency("1000PEPE/USDT:USDT") == "PEPE"
    assert extract_base_currency("1000SHIB/USDT:USDT") == "SHIB"


def test_extract_base_currency_strips_k_prefix():
    from src.integrations.news.models import extract_base_currency
    assert extract_base_currency("kSHIB/USDT:USDT") == "SHIB"
    assert extract_base_currency("kBONK/USDT:USDT") == "BONK"


def test_extract_base_currency_does_not_strip_false_positives():
    """Don't strip when the remainder isn't all-letters."""
    from src.integrations.news.models import extract_base_currency
    # "1000" alone (no letters) — leave as-is
    assert extract_base_currency("1000/USDT:USDT") == "1000"
    # "k" alone — leave as-is
    assert extract_base_currency("k/USDT:USDT") == "k"


# --- TTLCache ---

async def test_cache_stores_and_returns():
    from src.utils.cache import TTLCache

    cache = TTLCache()
    fetch = AsyncMock(return_value="data")
    result = await cache.get_or_fetch("key", 60.0, fetch)
    assert result == "data"
    fetch.assert_called_once()

    result2 = await cache.get_or_fetch("key", 60.0, fetch)
    assert result2 == "data"
    assert fetch.call_count == 1  # still 1 — cache hit


async def test_cache_different_keys_independent():
    from src.utils.cache import TTLCache

    cache = TTLCache()
    fetch_a = AsyncMock(return_value="a")
    fetch_b = AsyncMock(return_value="b")
    assert await cache.get_or_fetch("k1", 60.0, fetch_a) == "a"
    assert await cache.get_or_fetch("k2", 60.0, fetch_b) == "b"
    fetch_a.assert_called_once()
    fetch_b.assert_called_once()


async def test_cache_expires():
    from src.utils.cache import TTLCache

    cache = TTLCache()
    call_count = 0

    async def fetch():
        nonlocal call_count
        call_count += 1
        return f"data_{call_count}"

    # TTL and sleep kept small but generous enough to avoid flakes on busy CI.
    r1 = await cache.get_or_fetch("key", 0.05, fetch)  # 50ms TTL
    assert r1 == "data_1"

    await asyncio.sleep(0.1)  # well past the 50ms TTL
    r2 = await cache.get_or_fetch("key", 0.05, fetch)
    assert r2 == "data_2"


async def test_cache_429_extends_ttl_with_stale():
    from src.utils.cache import TTLCache, RateLimitHit

    cache = TTLCache()
    await cache.get_or_fetch("key", 0.05, AsyncMock(return_value="good"))

    await asyncio.sleep(0.1)  # let it expire (TTL 50ms)

    async def fetch_429():
        raise RateLimitHit("429")

    result = await cache.get_or_fetch("key", 0.01, fetch_429)
    assert result == "good"  # stale cache returned


async def test_cache_429_no_stale_raises():
    from src.utils.cache import TTLCache, RateLimitHit

    cache = TTLCache()

    async def fetch_429():
        raise RateLimitHit("429")

    with pytest.raises(RateLimitHit):
        await cache.get_or_fetch("key", 60.0, fetch_429)


async def test_cache_429_extended_ttl_persists():
    """After 429 extends TTL to 30min, cache should stay valid."""
    from src.utils.cache import TTLCache, RateLimitHit

    cache = TTLCache()
    await cache.get_or_fetch("key", 0.05, AsyncMock(return_value="good"))
    await asyncio.sleep(0.1)  # expire the 50ms TTL

    async def fetch_429():
        raise RateLimitHit("429")

    await cache.get_or_fetch("key", 0.05, fetch_429)

    # Now cache should be valid (30min TTL) — new fetch should not be called
    spy = AsyncMock(return_value="new")
    result = await cache.get_or_fetch("key", 0.05, spy)
    assert result == "good"
    spy.assert_not_called()


async def test_get_stale_returns_expired_data():
    from src.utils.cache import TTLCache

    cache = TTLCache()
    await cache.get_or_fetch("key", 0.05, AsyncMock(return_value="data"))
    await asyncio.sleep(0.1)
    assert cache.get_stale("key") == "data"


async def test_get_stale_returns_none_if_missing():
    from src.utils.cache import TTLCache

    cache = TTLCache()
    assert cache.get_stale("nonexistent") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.integrations.news'`

- [ ] **Step 3: Implement InformationEvent model + extract_base_currency**

```python
# src/integrations/news/__init__.py
```

```python
# src/integrations/news/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class InformationEvent:
    """Unified data model for all market intelligence events.

    The `content` field is source-specific free-form metadata used by the
    formatter for that section. Conventions:
      - coindesk       → original media name (e.g. "CoinTelegraph")
      - alternative_me → classification string (e.g. "Extreme Fear")
      - forexfactory   → "Previous: X | Forecast: Y" for macro events
      - okx_announcement / okx_status → unused (empty string)

    Each tool section formats events from a single source, so the per-source
    convention is safe in practice. If a new tool ever renders mixed sources,
    add a dedicated field rather than overloading `content` further.
    """

    timestamp: datetime
    source: str  # "coindesk" / "alternative_me" / "okx_announcement" / "okx_status" / "forexfactory"
    category: str  # "news" / "fgi" / "announcement" / "maintenance" / "macro_event"
    importance: Literal["low", "medium", "high"]
    title: str
    content: str = ""
    url: str = ""
    symbols: list[str] = field(default_factory=list)


def extract_base_currency(symbol: str) -> str:
    """Extract base currency for matching against CoinDesk CATEGORY_DATA.

    Strips OKX multiplier prefixes (1000PEPE → PEPE, kSHIB → SHIB) so those
    symbols aren't silently excluded from symbol-specific news.

    BTC/USDT:USDT      → BTC
    ETH/USDT:USDT      → ETH
    1000PEPE/USDT:USDT → PEPE
    kSHIB/USDT:USDT    → SHIB
    """
    base = symbol.split("/")[0]
    for prefix in ("1000", "k"):
        if base.startswith(prefix):
            remainder = base[len(prefix):]
            if remainder and remainder.isalpha():
                return remainder
    return base
```

- [ ] **Step 4: Implement TTLCache**

```python
# src/utils/__init__.py
```

```python
# src/utils/cache.py
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class RateLimitHit(Exception):
    """Raised when a fetch encounters HTTP 429 or equivalent rate limit."""


class TTLCache:
    """In-memory TTL cache with rate-limit-aware stale fallback.

    On RateLimitHit: if stale data exists, extend TTL to 30 min and return it.
    On other errors: let them propagate — caller decides how to degrade.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float, float]] = {}  # key → (data, created_at, ttl)

    async def get_or_fetch(
        self,
        key: str,
        default_ttl: float,
        fetch_fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        entry = self._store.get(key)
        if entry is not None:
            data, created_at, ttl = entry
            if time.monotonic() - created_at <= ttl:
                return data

        try:
            data = await fetch_fn()
        except RateLimitHit:
            if entry is not None:
                stale_data = entry[0]
                self._store[key] = (stale_data, time.monotonic(), 1800.0)
                logger.warning("Rate limited on key=%s, extending TTL to 30min", key)
                return stale_data
            raise

        self._store[key] = (data, time.monotonic(), default_ttl)
        return data

    def get_stale(self, key: str) -> Any | None:
        """Return cached data ignoring TTL, or None if key was never stored."""
        entry = self._store.get(key)
        return entry[0] if entry is not None else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cache.py -v`
Expected: all PASS

- [ ] **Step 6: Run full test suite to verify no regressions**

Run: `pytest --tb=short -q`
Expected: 417+ tests pass, 0 failures

- [ ] **Step 7: Commit**

```bash
git add src/utils/__init__.py src/utils/cache.py \
       src/integrations/news/__init__.py src/integrations/news/models.py \
       tests/test_cache.py
git commit -m "feat(N2): add InformationEvent model, TTLCache, and extract_base_currency"
```

---

### Task 2: News Data Clients — CoinDesk + Fear & Greed

**Files:**
- Create: `src/integrations/news/coindesk.py`, `src/integrations/news/fear_greed.py`
- Test: `tests/test_news_clients.py`

- [ ] **Step 1: Write tests for CoinDesk News client**

```python
# tests/test_news_clients.py
"""Tests for individual news/alert API clients."""
from datetime import datetime, timezone

import httpx
import pytest

from src.utils.cache import RateLimitHit


# ===== CoinDesk News =====

COINDESK_RESPONSE = {
    "Data": [
        {
            "TYPE": "122",
            "ID": 60622154,
            "GUID": "https://example.com/guid-1",
            "PUBLISHED_ON": 1776398458,
            "TITLE": "Bitcoin Breaks $90K as Institutional Inflows Surge",
            "URL": "https://example.com/btc-90k",
            "BODY": "Full article body...",
            "KEYWORDS": "Bitcoin|btc|ETF",
            "LANG": "EN",
            "SENTIMENT": "POSITIVE",
            "SCORE": 0,
            "SOURCE_DATA": {"NAME": "CoinTelegraph", "URL": "https://cointelegraph.com/", "LANG": "EN"},
            "CATEGORY_DATA": [
                {"TYPE": "122", "ID": 14, "NAME": "BTC", "CATEGORY": "BTC"},
                {"TYPE": "122", "ID": 24, "NAME": "ETH", "CATEGORY": "ETH"},
                {"TYPE": "122", "ID": 37, "NAME": "MARKET", "CATEGORY": "MARKET"},
            ],
        },
        {
            "TYPE": "122",
            "ID": 60622155,
            "GUID": "https://example.com/guid-2",
            "PUBLISHED_ON": 1776397000,
            "TITLE": "EU Passes Crypto Regulation",
            "URL": "https://example.com/eu-crypto",
            "BODY": "Details of the regulation...",
            "KEYWORDS": "Europe|regulation",
            "LANG": "EN",
            "SENTIMENT": "NEUTRAL",
            "SCORE": 0,
            "SOURCE_DATA": {"NAME": "CoinDesk", "URL": "https://www.coindesk.com/", "LANG": "EN"},
            "CATEGORY_DATA": [],  # no specific crypto categories
        },
    ],
    "Err": {},
}


async def test_coindesk_parse_response():
    from src.integrations.news.coindesk import CoinDeskNewsClient

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=COINDESK_RESPONSE))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinDeskNewsClient(http)
        events = await client.fetch_posts()

    assert len(events) == 2
    e0 = events[0]
    assert e0.title == "Bitcoin Breaks $90K as Institutional Inflows Surge"
    assert e0.source == "coindesk"
    assert e0.category == "news"
    assert e0.symbols == ["BTC", "ETH", "MARKET"]
    assert e0.content == "CoinTelegraph"  # original media name from SOURCE_DATA.NAME
    assert e0.url == "https://example.com/btc-90k"
    # Timestamp parsed from Unix seconds
    assert e0.timestamp == datetime.fromtimestamp(1776398458, tz=timezone.utc)
    # No CATEGORY_DATA → empty symbols
    assert events[1].symbols == []


async def test_coindesk_sentiment_param():
    from src.integrations.news.coindesk import CoinDeskNewsClient

    captured_params: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(200, json={"Data": [], "Err": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinDeskNewsClient(http)
        await client.fetch_posts(news_filter="positive")

    assert captured_params["lang"] == "EN"
    assert captured_params["limit"] == "20"
    assert captured_params["sentiment"] == "POSITIVE"


async def test_coindesk_no_filter_omits_sentiment():
    from src.integrations.news.coindesk import CoinDeskNewsClient

    captured_params: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(200, json={"Data": [], "Err": {}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinDeskNewsClient(http)
        await client.fetch_posts(news_filter=None)

    assert "sentiment" not in captured_params


async def test_coindesk_429_raises_rate_limit():
    from src.integrations.news.coindesk import CoinDeskNewsClient

    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CoinDeskNewsClient(http)
        with pytest.raises(RateLimitHit):
            await client.fetch_posts()


# ===== Fear & Greed Index =====

FGI_RESPONSE = {
    "data": [
        {
            "value": "23",
            "value_classification": "Extreme Fear",
            "timestamp": "1713225600",
        }
    ],
}


async def test_fgi_parse_response():
    from src.integrations.news.fear_greed import FearGreedClient

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=FGI_RESPONSE))
    async with httpx.AsyncClient(transport=transport) as http:
        client = FearGreedClient(http)
        event = await client.fetch()

    assert event is not None
    assert event.source == "alternative_me"
    assert event.category == "fgi"
    assert event.title == "23 / 100 — Extreme Fear"
    assert event.content == "Extreme Fear"
    assert event.importance == "low"


async def test_fgi_empty_data_returns_none():
    from src.integrations.news.fear_greed import FearGreedClient

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": []}))
    async with httpx.AsyncClient(transport=transport) as http:
        client = FearGreedClient(http)
        assert await client.fetch() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_news_clients.py -v -k "coindesk or fgi"`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement CoinDesk News client**

```python
# src/integrations/news/coindesk.py
from __future__ import annotations

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

_COINDESK_URL = "https://data-api.coindesk.com/news/v1/article/list"

# Map user-facing filter → CoinDesk sentiment values
_SENTIMENT_MAP = {
    "positive": "POSITIVE",
    "negative": "NEGATIVE",
    "neutral": "NEUTRAL",
}


class CoinDeskNewsClient:
    """CoinDesk Data News API client — crypto news headlines with sentiment.

    No auth required. Response shape:
      { "Data": [ {TITLE, PUBLISHED_ON, URL, SOURCE_DATA.NAME, CATEGORY_DATA[], SENTIMENT, ...}, ... ], "Err": {} }
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_posts(self, news_filter: str | None = None) -> list[InformationEvent]:
        params: dict[str, str | int] = {"lang": "EN", "limit": 20}
        if news_filter is not None:
            mapped = _SENTIMENT_MAP.get(news_filter)
            if mapped is not None:
                params["sentiment"] = mapped

        resp = await self._http.get(_COINDESK_URL, params=params)
        if resp.status_code == 429:
            raise RateLimitHit("CoinDesk rate limited")
        resp.raise_for_status()

        return self._parse(resp.json())

    @staticmethod
    def _parse(data: dict) -> list[InformationEvent]:
        from datetime import datetime, timezone

        events: list[InformationEvent] = []
        for article in data.get("Data", []):
            raw_cats = article.get("CATEGORY_DATA") or []
            symbols = [c.get("NAME", "") for c in raw_cats if c.get("NAME")]
            source_name = (article.get("SOURCE_DATA") or {}).get("NAME", "")

            pub_raw = article.get("PUBLISHED_ON")
            try:
                ts = datetime.fromtimestamp(int(pub_raw), tz=timezone.utc)
            except (TypeError, ValueError):
                ts = datetime.now(timezone.utc)

            events.append(
                InformationEvent(
                    timestamp=ts,
                    source="coindesk",
                    category="news",
                    importance="medium",
                    title=article.get("TITLE", ""),
                    content=source_name,
                    url=article.get("URL", ""),
                    symbols=symbols,
                )
            )
        return events
```

- [ ] **Step 4: Implement Fear & Greed client**

```python
# src/integrations/news/fear_greed.py
from __future__ import annotations

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

_FGI_URL = "https://api.alternative.me/fng/"


class FearGreedClient:
    """Alternative.me Fear & Greed Index client."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self) -> InformationEvent | None:
        from datetime import datetime, timezone

        resp = await self._http.get(_FGI_URL)
        if resp.status_code == 429:
            raise RateLimitHit("FGI rate limited")
        resp.raise_for_status()
        items = resp.json().get("data", [])
        if not items:
            return None

        item = items[0]
        value = item["value"]
        classification = item["value_classification"]
        raw_ts = item.get("timestamp")
        ts = (
            datetime.fromtimestamp(int(raw_ts), tz=timezone.utc)
            if raw_ts
            else datetime.now(timezone.utc)
        )
        return InformationEvent(
            timestamp=ts,
            source="alternative_me",
            category="fgi",
            importance="low",
            title=f"{value} / 100 — {classification}",
            content=classification,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_news_clients.py -v -k "coindesk or fgi"`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/integrations/news/coindesk.py src/integrations/news/fear_greed.py \
       tests/test_news_clients.py
git commit -m "feat(N2): add CoinDesk News and Fear & Greed Index clients"
```

---

### Task 3: Alert Data Clients — ForexFactory + OKX Announcements + OKX Status

**Files:**
- Create: `src/integrations/news/calendar.py`, `src/integrations/news/okx_announcements.py`, `src/integrations/news/okx_status.py`
- Test: `tests/test_news_clients.py` (append)

- [ ] **Step 1: Write tests for ForexFactory, OKX Announcements, and OKX Status clients**

Append to `tests/test_news_clients.py`:

```python
# ===== ForexFactory Calendar =====

FOREXFACTORY_RESPONSE = [
    {
        "title": "FOMC Meeting Minutes",
        "country": "USD",
        "date": "2026-04-16T18:00:00-04:00",
        "impact": "High",
        "forecast": "",
        "previous": "",
    },
    {
        "title": "US Initial Jobless Claims",
        "country": "USD",
        "date": "2026-04-16T20:30:00-04:00",
        "impact": "Medium",
        "forecast": "220K",
        "previous": "215K",
    },
    {
        "title": "EU Consumer Confidence",
        "country": "EUR",
        "date": "2026-04-16T15:00:00-04:00",
        "impact": "Medium",
        "forecast": "-8.0",
        "previous": "-7.5",
    },
    {
        "title": "US Treasury Auction",
        "country": "USD",
        "date": "2026-04-16T13:00:00-04:00",
        "impact": "Low",
        "forecast": "",
        "previous": "",
    },
]


async def test_calendar_parse_response():
    from src.integrations.news.calendar import ForexFactoryClient

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=FOREXFACTORY_RESPONSE))
    async with httpx.AsyncClient(transport=transport) as http:
        client = ForexFactoryClient(http)
        events = await client.fetch_events()

    # Should filter: only USD + High/Medium → 2 events (skip EUR, skip Low)
    assert len(events) == 2
    assert events[0].title == "FOMC Meeting Minutes"
    assert events[0].source == "forexfactory"
    assert events[0].category == "macro_event"
    assert events[0].importance == "high"
    assert events[1].title == "US Initial Jobless Claims"
    assert events[1].importance == "medium"
    assert "Previous: 215K" in events[1].content
    assert "Forecast: 220K" in events[1].content


async def test_calendar_empty_response():
    from src.integrations.news.calendar import ForexFactoryClient

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=[]))
    async with httpx.AsyncClient(transport=transport) as http:
        client = ForexFactoryClient(http)
        events = await client.fetch_events()
    assert events == []


# ===== OKX Announcements =====

OKX_DELISTINGS_RESPONSE = {
    "code": "0",
    "data": [
        {
            "details": [
                {
                    "title": "Delisting of XYZ/USDT perpetual",
                    "url": "https://www.okx.com/ann/123",
                    "annType": "announcements-delistings",
                    "pTime": "1713265200000",
                },
                {
                    "title": "Old announcement",
                    "url": "https://www.okx.com/ann/100",
                    "annType": "announcements-delistings",
                    "pTime": "1612000000000",  # very old
                },
            ],
        },
    ],
}
OKX_TRADING_UPDATES_RESPONSE = {
    "code": "0",
    "data": [
        {
            "details": [
                {
                    "title": "Contract parameter change — ETH/USDT",
                    "url": "https://www.okx.com/ann/200",
                    "annType": "trading-updates-us-aus",
                    "pTime": "1713300000000",
                },
            ],
        },
    ],
}


async def test_okx_announcements_parse():
    from src.integrations.news.okx_announcements import OKXAnnouncementsClient

    # Client fetches once per annType; respond per annType with distinct payloads.
    def handler(request: httpx.Request) -> httpx.Response:
        ann_type = dict(request.url.params).get("annType", "")
        if ann_type == "announcements-delistings":
            return httpx.Response(200, json=OKX_DELISTINGS_RESPONSE)
        if ann_type == "trading-updates-us-aus":
            return httpx.Response(200, json=OKX_TRADING_UPDATES_RESPONSE)
        return httpx.Response(200, json={"code": "0", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = OKXAnnouncementsClient(http)
        events = await client.fetch()

    # 2 delistings + 1 trading update = 3 events (no time filtering — that's NewsService's job)
    assert len(events) == 3
    titles = {e.title for e in events}
    assert "Delisting of XYZ/USDT perpetual" in titles
    assert "Contract parameter change — ETH/USDT" in titles
    assert all(e.source == "okx_announcement" for e in events)
    assert all(e.category == "announcement" for e in events)
    assert all(e.importance == "high" for e in events)


async def test_okx_announcements_queries_correct_types():
    from src.integrations.news.okx_announcements import OKXAnnouncementsClient

    captured_types: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_types.append(dict(request.url.params).get("annType", ""))
        return httpx.Response(200, json={"code": "0", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = OKXAnnouncementsClient(http)
        await client.fetch()

    assert "announcements-delistings" in captured_types
    assert "trading-updates-us-aus" in captured_types


async def test_okx_announcements_429():
    from src.integrations.news.okx_announcements import OKXAnnouncementsClient

    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = OKXAnnouncementsClient(http)
        with pytest.raises(RateLimitHit):
            await client.fetch()


# ===== OKX System Status =====

OKX_STATUS_RESPONSE = {
    "code": "0",
    "data": [
        {
            "title": "System upgrade",
            "state": "scheduled",
            "begin": "1713308400000",
            "end": "1713315600000",
            "maintType": "0",
            "serviceType": "1",
            "system": "trading",
        },
    ],
}


async def test_okx_status_parse():
    from src.integrations.news.okx_status import OKXStatusClient

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=OKX_STATUS_RESPONSE))
    async with httpx.AsyncClient(transport=transport) as http:
        client = OKXStatusClient(http)
        events = await client.fetch()

    assert len(events) >= 1
    assert events[0].source == "okx_status"
    assert events[0].category == "maintenance"
    assert events[0].importance == "high"
    assert "System upgrade" in events[0].title
    assert "UTC" in events[0].title


async def test_okx_status_queries_both_states():
    from src.integrations.news.okx_status import OKXStatusClient

    captured_states: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_states.append(dict(request.url.params).get("state", ""))
        return httpx.Response(200, json={"code": "0", "data": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = OKXStatusClient(http)
        await client.fetch()

    assert "scheduled" in captured_states
    assert "ongoing" in captured_states


async def test_okx_status_429():
    from src.integrations.news.okx_status import OKXStatusClient

    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = OKXStatusClient(http)
        with pytest.raises(RateLimitHit):
            await client.fetch()
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `pytest tests/test_news_clients.py -v -k "calendar or okx_announcements or okx_status"`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement ForexFactory calendar client**

```python
# src/integrations/news/calendar.py
from __future__ import annotations

import logging

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

logger = logging.getLogger(__name__)

_FOREXFACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


class ForexFactoryClient:
    """ForexFactory economic calendar client (via faireconomy.media feed)."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_events(self) -> list[InformationEvent]:
        from datetime import datetime, timezone

        resp = await self._http.get(_FOREXFACTORY_URL)
        if resp.status_code == 429:
            raise RateLimitHit("ForexFactory rate limited")
        resp.raise_for_status()

        events: list[InformationEvent] = []
        for item in resp.json():
            if item.get("country") != "USD":
                continue
            impact = item.get("impact", "")
            if impact not in ("High", "Medium"):
                continue

            date_str = item.get("date", "")
            try:
                ts = datetime.fromisoformat(date_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                continue

            content_parts: list[str] = []
            if item.get("previous"):
                content_parts.append(f"Previous: {item['previous']}")
            if item.get("forecast"):
                content_parts.append(f"Forecast: {item['forecast']}")

            events.append(
                InformationEvent(
                    timestamp=ts,
                    source="forexfactory",
                    category="macro_event",
                    importance="high" if impact == "High" else "medium",
                    title=item.get("title", ""),
                    content=" | ".join(content_parts),
                )
            )
        return events
```

- [ ] **Step 4: Implement OKX announcements client**

```python
# src/integrations/news/okx_announcements.py
from __future__ import annotations

import logging

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

logger = logging.getLogger(__name__)

_OKX_ANNOUNCEMENTS_URL = "https://www.okx.com/api/v5/support/announcements"
_ANN_TYPES = ("announcements-delistings", "trading-updates-us-aus")


class OKXAnnouncementsClient:
    """OKX /support/announcements client — delistings + trading rule changes.

    Response schema (verified in Pre-work P4a): items are nested under
    `data[0].details[*]`, not `data[*]`. The flat layer is per-page metadata;
    the actual announcement items live in the `details` array.
    """

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self) -> list[InformationEvent]:
        from datetime import datetime, timezone

        events: list[InformationEvent] = []
        for ann_type in _ANN_TYPES:
            resp = await self._http.get(_OKX_ANNOUNCEMENTS_URL, params={"annType": ann_type})
            if resp.status_code == 429:
                raise RateLimitHit("OKX announcements rate limited")
            resp.raise_for_status()

            data_arr = resp.json().get("data") or []
            if not data_arr or not isinstance(data_arr[0], dict):
                continue
            details = data_arr[0].get("details") or []

            for item in details:
                p_time = int(item.get("pTime") or 0)
                events.append(
                    InformationEvent(
                        timestamp=datetime.fromtimestamp(p_time / 1000, tz=timezone.utc),
                        source="okx_announcement",
                        category="announcement",
                        importance="high",
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                    )
                )
        return events
```

- [ ] **Step 5: Implement OKX status client**

```python
# src/integrations/news/okx_status.py
from __future__ import annotations

import logging

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

logger = logging.getLogger(__name__)

_OKX_STATUS_URL = "https://www.okx.com/api/v5/system/status"


class OKXStatusClient:
    """OKX /system/status client — scheduled maintenance + ongoing incidents."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self) -> list[InformationEvent]:
        from datetime import datetime, timezone

        # Use fetch time as the observation timestamp so these pass
        # NewsService's lookback filter (past N hours = recently observed).
        # The actual maintenance begin/end goes into the title for display.
        now = datetime.now(timezone.utc)

        events: list[InformationEvent] = []
        for state in ("scheduled", "ongoing"):
            resp = await self._http.get(_OKX_STATUS_URL, params={"state": state})
            if resp.status_code == 429:
                raise RateLimitHit("OKX status rate limited")
            resp.raise_for_status()

            for item in resp.json().get("data", []):
                begin_ms = int(item.get("begin", 0))
                end_ms = int(item.get("end", 0))
                begin_dt = datetime.fromtimestamp(begin_ms / 1000, tz=timezone.utc)
                end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
                title_raw = item.get("title", "")
                title = (
                    f"{title_raw} "
                    f"{begin_dt.strftime('%Y-%m-%d %H:%M')}-"
                    f"{end_dt.strftime('%H:%M')} UTC"
                )
                events.append(
                    InformationEvent(
                        timestamp=now,
                        source="okx_status",
                        category="maintenance",
                        importance="high",
                        title=title,
                    )
                )
        return events
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_news_clients.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/integrations/news/calendar.py src/integrations/news/okx_announcements.py \
       src/integrations/news/okx_status.py tests/test_news_clients.py
git commit -m "feat(N2): add ForexFactory, OKX announcements, and OKX status clients"
```

---

### Task 4: NewsService — Aggregation + Caching

**Files:**
- Create: `src/integrations/news/service.py`
- Test: `tests/test_news_service.py`

- [ ] **Step 1: Write tests for NewsService core behavior**

```python
# tests/test_news_service.py
"""Tests for NewsService — aggregation, caching, degradation."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.integrations.news.models import InformationEvent
from src.utils.cache import TTLCache


def _make_news_event(title="Test", source="coindesk", category="news",
                     symbols=None, hours_ago=0):
    return InformationEvent(
        timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        source=source,
        category=category,
        importance="medium",
        title=title,
        symbols=symbols or [],
    )


def _make_service():
    """Build a NewsService with all data-source clients mocked."""
    from src.integrations.news.service import NewsService

    # Inject a mock http client to avoid a real httpx.AsyncClient being orphaned.
    svc = NewsService(http=AsyncMock())
    svc._news = AsyncMock()
    svc._fgi = AsyncMock()
    svc._calendar = AsyncMock()
    svc._announcements = AsyncMock()
    svc._status = AsyncMock()
    return svc


# --- get_news: basic ---

async def test_get_news_splits_by_symbol():
    svc = _make_service()
    svc._news.fetch_posts.return_value = [
        _make_news_event("BTC up", symbols=["BTC"]),
        _make_news_event("BTC mining", symbols=["BTC"]),
        _make_news_event("ETH news", symbols=["ETH"]),
        _make_news_event("General 1"),
        _make_news_event("General 2"),
        _make_news_event("General 3"),
    ]
    sym, gen = await svc.get_news("BTC/USDT:USDT")
    assert len(sym) == 2  # only 2 BTC articles
    # general_news has 4 items (ETH + 3x General). gen_count request = 10 - 2 = 8,
    # so all 4 available general articles are selected (total = 6, below target 10).
    assert len(gen) == 4
    assert all("BTC" in e.symbols for e in sym)


async def test_get_news_fills_from_general_when_symbol_short():
    svc = _make_service()
    # Only 1 BTC article, 8 general
    posts = [_make_news_event("BTC", symbols=["BTC"])]
    posts += [_make_news_event(f"Gen {i}") for i in range(8)]
    svc._news.fetch_posts.return_value = posts
    sym, gen = await svc.get_news("BTC/USDT:USDT", max_per_group=5)
    assert len(sym) == 1
    assert len(gen) == 8  # fill up to 9 total (10 - 1)


async def test_get_news_passes_filter():
    svc = _make_service()
    svc._news.fetch_posts.return_value = []
    await svc.get_news("BTC/USDT:USDT", news_filter="positive")
    svc._news.fetch_posts.assert_called_once_with("positive")


# --- get_news: error paths ---

async def test_get_news_api_failure_returns_empty():
    svc = _make_service()
    svc._news.fetch_posts.side_effect = Exception("API down")
    sym, gen = await svc.get_news("BTC/USDT:USDT")
    assert sym == [] and gen == []


# --- get_news: caching ---

async def test_get_news_cache_hit():
    svc = _make_service()
    svc._news.fetch_posts.return_value = [_make_news_event("Cached")]
    await svc.get_news("BTC/USDT:USDT")
    await svc.get_news("BTC/USDT:USDT")
    # fetch_posts called only once (second call is cache hit)
    assert svc._news.fetch_posts.call_count == 1


async def test_get_news_different_filters_separate_cache():
    svc = _make_service()
    svc._news.fetch_posts.return_value = []
    await svc.get_news("BTC/USDT:USDT", news_filter="positive")
    await svc.get_news("BTC/USDT:USDT", news_filter="negative")
    assert svc._news.fetch_posts.call_count == 2


# --- get_news: rate limit handled by TTLCache ---

async def test_get_news_429_uses_stale_cache():
    """TTLCache should return stale data on RateLimitHit."""
    from src.utils.cache import RateLimitHit

    svc = _make_service()
    svc._news.fetch_posts.return_value = [_make_news_event("Old", symbols=["BTC"])]
    await svc.get_news("BTC/USDT:USDT")  # populate cache

    # Expire the cache entry
    for key, (data, _created_at, ttl) in list(svc._cache._store.items()):
        svc._cache._store[key] = (data, 0.0, ttl)

    # Next fetch raises 429 → TTLCache returns stale
    svc._news.fetch_posts.side_effect = RateLimitHit("429")
    sym, gen = await svc.get_news("BTC/USDT:USDT")
    assert len(sym) + len(gen) > 0  # stale cache used


async def test_get_news_429_no_cache_degrades():
    """First call is 429 with no cached data → returns empty."""
    from src.utils.cache import RateLimitHit

    svc = _make_service()
    svc._news.fetch_posts.side_effect = RateLimitHit("429")
    sym, gen = await svc.get_news("BTC/USDT:USDT")
    assert sym == [] and gen == []


# --- get_fear_greed_index ---

async def test_get_fgi():
    svc = _make_service()
    fgi_event = _make_news_event("23 / 100 — Extreme Fear", source="alternative_me", category="fgi")
    svc._fgi.fetch.return_value = fgi_event
    result = await svc.get_fear_greed_index()
    assert result is not None
    assert result.source == "alternative_me"


async def test_get_fgi_failure_returns_none():
    svc = _make_service()
    svc._fgi.fetch.side_effect = Exception("API down")
    result = await svc.get_fear_greed_index()
    assert result is None


async def test_get_fgi_cached():
    svc = _make_service()
    svc._fgi.fetch.return_value = _make_news_event("FGI", source="alternative_me")
    await svc.get_fear_greed_index()
    await svc.get_fear_greed_index()
    assert svc._fgi.fetch.call_count == 1


# --- get_announcements ---

async def test_get_announcements_combines_both_sources():
    svc = _make_service()
    svc._announcements.fetch.return_value = [
        _make_news_event("Delisting", source="okx_announcement", category="announcement"),
    ]
    svc._status.fetch.return_value = [
        _make_news_event("Maintenance", source="okx_status", category="maintenance"),
    ]
    result = await svc.get_announcements(lookback_hours=24)
    assert len(result) == 2


async def test_get_announcements_filters_by_lookback():
    svc = _make_service()
    svc._announcements.fetch.return_value = [
        _make_news_event("Recent", source="okx_announcement", hours_ago=1),
        _make_news_event("Old", source="okx_announcement", hours_ago=48),
    ]
    svc._status.fetch.return_value = []
    result = await svc.get_announcements(lookback_hours=24)
    assert len(result) == 1
    assert result[0].title == "Recent"


async def test_get_announcements_partial_failure():
    svc = _make_service()
    svc._announcements.fetch.side_effect = Exception("down")
    svc._status.fetch.return_value = [
        _make_news_event("Status OK", source="okx_status"),
    ]
    result = await svc.get_announcements(lookback_hours=24)
    assert len(result) == 1


# --- get_macro_events ---

async def test_get_macro_events_filters_by_lookahead():
    svc = _make_service()
    now = datetime.now(timezone.utc)
    svc._calendar.fetch_events.return_value = [
        InformationEvent(
            timestamp=now + timedelta(hours=2),
            source="forexfactory", category="macro_event",
            importance="high", title="FOMC Soon",
        ),
        InformationEvent(
            timestamp=now + timedelta(hours=24),
            source="forexfactory", category="macro_event",
            importance="high", title="FOMC Later",
        ),
        InformationEvent(
            timestamp=now - timedelta(hours=2),
            source="forexfactory", category="macro_event",
            importance="high", title="Already Passed",
        ),
    ]
    result = await svc.get_macro_events(lookahead_hours=12)
    assert len(result) == 1
    assert result[0].title == "FOMC Soon"


async def test_get_macro_events_failure_returns_empty():
    svc = _make_service()
    svc._calendar.fetch_events.side_effect = Exception("feed down")
    result = await svc.get_macro_events(lookahead_hours=12)
    assert result == []


# --- close ---

async def test_close_injected_http_not_closed():
    """Injected http client is owned by the caller — NewsService must NOT close it."""
    svc = _make_service()  # injects AsyncMock as http
    await svc.close()
    svc._http.aclose.assert_not_called()


async def test_close_owned_http_closes():
    """When NewsService creates its own http client, close() should close it."""
    from src.integrations.news.service import NewsService

    svc = NewsService()  # no injection → creates its own httpx.AsyncClient
    # Close the real client first to avoid a resource leak, then swap in a mock
    # so we can verify close() dispatches aclose() on the owned client.
    await svc._http.aclose()
    mock_http = AsyncMock()
    svc._http = mock_http
    assert svc._owns_http is True  # should already be True from __init__
    await svc.close()
    mock_http.aclose.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_news_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.integrations.news.service'`

- [ ] **Step 3: Implement NewsService**

```python
# src/integrations/news/service.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from src.integrations.news.calendar import ForexFactoryClient
from src.integrations.news.coindesk import CoinDeskNewsClient
from src.integrations.news.fear_greed import FearGreedClient
from src.integrations.news.models import InformationEvent, extract_base_currency
from src.integrations.news.okx_announcements import OKXAnnouncementsClient
from src.integrations.news.okx_status import OKXStatusClient
from src.utils.cache import RateLimitHit, TTLCache

logger = logging.getLogger(__name__)

# Cache TTLs (seconds)
_NEWS_TTL = 900.0  # 15 min
_FGI_TTL = 21600.0  # 6 hours
_CALENDAR_TTL = 21600.0  # 6 hours
_OKX_TTL = 600.0  # 10 min


class NewsService:
    """Aggregates all news/alert data sources with caching.

    All upstream sources are keyless (CoinDesk News, FGI, ForexFactory, OKX).
    No quota tracking — if a source returns HTTP 429, TTLCache serves stale
    data if present; otherwise the get_* method returns an empty result.
    """

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        # Accept injected http client for testability; default to real one otherwise.
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None  # only close http if we created it
        self._cache = TTLCache()

        # Clients (all keyless)
        self._news = CoinDeskNewsClient(self._http)
        self._fgi = FearGreedClient(self._http)
        self._calendar = ForexFactoryClient(self._http)
        self._announcements = OKXAnnouncementsClient(self._http)
        self._status = OKXStatusClient(self._http)

    async def get_news(
        self,
        symbol: str,
        news_filter: str | None = None,
        max_per_group: int = 5,
    ) -> tuple[list[InformationEvent], list[InformationEvent]]:
        """Fetch news headlines, split into (symbol_news, general_news).

        Returns two lists: symbol-specific headlines and general crypto news.
        If symbol_news < max_per_group, general_news gets extra slots (total = max_per_group * 2).
        On any upstream error returns ([], []).
        """
        cache_key = f"news:{news_filter}"

        try:
            all_posts = await self._cache.get_or_fetch(
                cache_key, _NEWS_TTL,
                lambda: self._news.fetch_posts(news_filter),
            )
        except RateLimitHit:
            # TTLCache already tried stale cache and didn't have any
            logger.warning("CoinDesk 429 with no cache, degrading")
            return [], []
        except Exception:
            logger.warning("CoinDesk fetch failed", exc_info=True)
            return [], []

        return self._split_news(all_posts, symbol, max_per_group)

    @staticmethod
    def _split_news(
        posts: list[InformationEvent],
        symbol: str,
        max_per_group: int,
    ) -> tuple[list[InformationEvent], list[InformationEvent]]:
        base = extract_base_currency(symbol)
        symbol_news = [p for p in posts if base in p.symbols]
        general_news = [p for p in posts if base not in p.symbols]

        sym_count = min(len(symbol_news), max_per_group)
        sym_selected = symbol_news[:sym_count]
        gen_count = max_per_group * 2 - sym_count
        gen_selected = general_news[:gen_count]

        return sym_selected, gen_selected

    async def get_fear_greed_index(self) -> InformationEvent | None:
        try:
            return await self._cache.get_or_fetch("fgi", _FGI_TTL, self._fgi.fetch)
        except Exception:
            logger.warning("FGI fetch failed", exc_info=True)
            return None

    async def get_macro_events(self, lookahead_hours: int) -> list[InformationEvent]:
        try:
            all_events: list[InformationEvent] = await self._cache.get_or_fetch(
                "macro_calendar", _CALENDAR_TTL, self._calendar.fetch_events
            )
        except Exception:
            logger.warning("ForexFactory fetch failed", exc_info=True)
            return []

        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=lookahead_hours)
        return [e for e in all_events if now <= e.timestamp <= cutoff]

    async def get_announcements(self, lookback_hours: int) -> list[InformationEvent]:
        results: list[InformationEvent] = []
        for cache_key, fetch_fn in [
            ("okx_ann", self._announcements.fetch),
            ("okx_status", self._status.fetch),
        ]:
            try:
                events = await self._cache.get_or_fetch(cache_key, _OKX_TTL, fetch_fn)
                results.extend(events)
            except RateLimitHit:
                logger.warning("OKX rate limited for %s", cache_key)
            except Exception:
                logger.warning("OKX fetch failed for %s", cache_key, exc_info=True)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        return [e for e in results if e.timestamp >= cutoff]

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_news_service.py -v`
Expected: all PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest --tb=short -q`
Expected: 417+ tests pass, 0 failures

- [ ] **Step 6: Commit**

```bash
git add src/integrations/news/service.py tests/test_news_service.py
git commit -m "feat(N2): add NewsService with caching and graceful degradation"
```

---

### Task 5: Derivatives — Types + BaseExchange + Exchange Implementations

**Files:**
- Modify: `src/integrations/exchange/base.py` (add dataclasses + abstract methods)
- Modify: `src/integrations/exchange/okx.py` (implement 3 methods)
- Modify: `src/integrations/exchange/simulated.py` (implement 3 methods)
- Test: `tests/test_derivatives_data.py`

- [ ] **Step 1: Write tests for derivatives dataclasses + exchange implementations**

```python
# tests/test_derivatives_data.py
"""Tests for derivatives data types, exchange implementations, and MarketDataService caching."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.integrations.exchange.base import FundingRate, OpenInterest, LongShortRatio


# --- Dataclass tests ---

def test_funding_rate_fields():
    fr = FundingRate(
        symbol="BTC/USDT:USDT", rate=0.000125,
        next_funding_time=1713265200000, timestamp=1713261600000,
    )
    assert fr.rate == 0.000125
    assert fr.symbol == "BTC/USDT:USDT"


def test_open_interest_fields():
    oi = OpenInterest(
        symbol="BTC/USDT:USDT", open_interest=12345.0,
        open_interest_value=4_820_000_000.0, timestamp=1713261600000,
    )
    assert oi.open_interest_value == 4_820_000_000.0


def test_long_short_ratio_fields():
    lsr = LongShortRatio(
        symbol="BTC/USDT:USDT", long_short_ratio=1.35,
        long_ratio=0.574, short_ratio=0.426, timestamp=1713261600000,
    )
    assert lsr.long_short_ratio == 1.35
    assert lsr.long_ratio == pytest.approx(0.574, abs=0.001)
    assert lsr.short_ratio == pytest.approx(0.426, abs=0.001)


# --- OKXExchange derivatives tests ---

async def test_okx_fetch_funding_rate():
    from src.integrations.exchange.okx import OKXExchange

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = AsyncMock()
    exchange._client.fetch_funding_rate.return_value = {
        "symbol": "BTC/USDT:USDT",
        "fundingRate": 0.000125,
        "fundingTimestamp": 1713265200000,
        "timestamp": 1713261600000,
    }
    result = await exchange.fetch_funding_rate("BTC/USDT:USDT")
    assert isinstance(result, FundingRate)
    assert result.rate == 0.000125
    assert result.next_funding_time == 1713265200000


async def test_okx_fetch_open_interest():
    from src.integrations.exchange.okx import OKXExchange

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = AsyncMock()
    exchange._client.fetch_open_interest.return_value = {
        "symbol": "BTC/USDT:USDT",
        "openInterestAmount": 12345.0,
        "openInterestValue": 4_820_000_000.0,
        "timestamp": 1713261600000,
    }
    result = await exchange.fetch_open_interest("BTC/USDT:USDT")
    assert isinstance(result, OpenInterest)
    assert result.open_interest_value == 4_820_000_000.0


async def test_okx_fetch_long_short_ratio():
    from src.integrations.exchange.okx import OKXExchange

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = AsyncMock()
    exchange._client.fetch_long_short_ratio_history.return_value = [
        {"symbol": "BTC/USDT:USDT", "longShortRatio": 1.35, "timestamp": 1713261600000},
    ]
    result = await exchange.fetch_long_short_ratio("BTC/USDT:USDT")
    assert isinstance(result, LongShortRatio)
    assert result.long_short_ratio == 1.35
    assert result.long_ratio == pytest.approx(1.35 / 2.35, abs=0.001)
    assert result.short_ratio == pytest.approx(1.0 / 2.35, abs=0.001)


async def test_okx_long_short_ratio_empty_raises():
    from src.integrations.exchange.okx import OKXExchange

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = AsyncMock()
    exchange._client.fetch_long_short_ratio_history.return_value = []
    with pytest.raises(ValueError, match="No long/short ratio data"):
        await exchange.fetch_long_short_ratio("BTC/USDT:USDT")


# --- SimulatedExchange derivatives tests ---

def _make_sim_exchange(symbol="BTC/USDT:USDT"):
    """Minimal SimulatedExchange for derivatives testing."""
    from src.integrations.exchange.simulated import SimulatedExchange

    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}
    exchange = SimulatedExchange(config=config, db_engine=None, session_id="test", symbol=symbol)
    exchange._ccxt = AsyncMock()
    return exchange


async def test_sim_fetch_funding_rate():
    ex = _make_sim_exchange()
    ex._ccxt.fetch_funding_rate.return_value = {
        "symbol": "BTC/USDT:USDT",
        "fundingRate": -0.0003,
        "fundingTimestamp": 1713265200000,
        "timestamp": 1713261600000,
    }
    result = await ex.fetch_funding_rate("BTC/USDT:USDT")
    assert isinstance(result, FundingRate)
    assert result.rate == -0.0003


async def test_sim_fetch_open_interest():
    ex = _make_sim_exchange()
    ex._ccxt.fetch_open_interest.return_value = {
        "symbol": "BTC/USDT:USDT",
        "openInterestAmount": 9000.0,
        "openInterestValue": 855_000_000.0,
        "timestamp": 1713261600000,
    }
    result = await ex.fetch_open_interest("BTC/USDT:USDT")
    assert isinstance(result, OpenInterest)
    assert result.open_interest_value == 855_000_000.0


async def test_sim_fetch_long_short_ratio():
    ex = _make_sim_exchange()
    ex._ccxt.fetch_long_short_ratio_history.return_value = [
        {"symbol": "BTC/USDT:USDT", "longShortRatio": 0.94, "timestamp": 1713261600000},
    ]
    result = await ex.fetch_long_short_ratio("BTC/USDT:USDT")
    assert isinstance(result, LongShortRatio)
    assert result.long_short_ratio == 0.94
    assert result.long_ratio == pytest.approx(0.94 / 1.94, abs=0.001)


async def test_sim_fetch_funding_rate_no_ccxt():
    """Should raise if exchange not started (no _ccxt)."""
    from src.integrations.exchange.simulated import SimulatedExchange

    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {}
    ex = SimulatedExchange(config=config, db_engine=None, session_id="test", symbol="BTC/USDT:USDT")
    # Don't set _ccxt — simulates not calling start()
    with pytest.raises(RuntimeError, match="not started"):
        await ex.fetch_funding_rate("BTC/USDT:USDT")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_derivatives_data.py -v`
Expected: FAIL — `ImportError: cannot import name 'FundingRate' from 'src.integrations.exchange.base'`

- [ ] **Step 3: Add derivatives dataclasses + abstract methods to BaseExchange**

Add to `src/integrations/exchange/base.py`, after the `PriceLevelAlertInfo` dataclass at the end of the file:

```python
@dataclass
class FundingRate:
    symbol: str
    rate: float  # current funding rate (e.g., 0.000125 = 0.0125%)
    next_funding_time: int  # next settlement timestamp (ms)
    timestamp: int


@dataclass
class OpenInterest:
    symbol: str
    open_interest: float  # base-currency amount (per ccxt unified `openInterestAmount`)
    open_interest_value: float  # USD value
    timestamp: int


@dataclass
class LongShortRatio:
    symbol: str
    long_short_ratio: float  # raw ratio (e.g., 1.35)
    long_ratio: float  # derived: ratio / (1 + ratio)
    short_ratio: float  # derived: 1 / (1 + ratio)
    timestamp: int
```

Add three abstract methods to the `BaseExchange` class, after `cancel_order`:

```python
    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> 'FundingRate': ...
    @abstractmethod
    async def fetch_open_interest(self, symbol: str) -> 'OpenInterest': ...
    @abstractmethod
    async def fetch_long_short_ratio(self, symbol: str) -> 'LongShortRatio': ...
```

- [ ] **Step 4: Implement derivatives in OKXExchange**

Add to `src/integrations/exchange/okx.py`. First, update the import from `base.py`:

```python
from src.integrations.exchange.base import (
    Balance,
    BaseExchange,
    Candle,
    FillEvent,
    FundingRate,
    LongShortRatio,
    OpenInterest,
    Order,
    Position,
    Ticker,
)
```

Also add import for `RateLimitHit` at the top:

```python
from src.utils.cache import RateLimitHit
```

Then add three methods to `OKXExchange`, before the `close()` method. Each wraps the ccxt call in try/except to convert `ccxt.RateLimitExceeded` → `RateLimitHit` (so `TTLCache` in `MarketDataService` can do stale-fallback uniformly with news sources):

```python
    @_retry()
    async def fetch_funding_rate(self, symbol: str) -> FundingRate:
        try:
            data = await self._client.fetch_funding_rate(symbol)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"OKX funding rate: {e}") from e
        return FundingRate(
            symbol=data["symbol"],
            rate=float(data["fundingRate"]),
            next_funding_time=int(data.get("fundingTimestamp") or 0),
            timestamp=int(data.get("timestamp") or 0),
        )

    @_retry()
    async def fetch_open_interest(self, symbol: str) -> OpenInterest:
        try:
            data = await self._client.fetch_open_interest(symbol)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"OKX open interest: {e}") from e
        return OpenInterest(
            symbol=data["symbol"],
            open_interest=float(data.get("openInterestAmount") or 0),
            open_interest_value=float(data.get("openInterestValue") or 0),
            timestamp=int(data.get("timestamp") or 0),
        )

    @_retry()
    async def fetch_long_short_ratio(self, symbol: str) -> LongShortRatio:
        try:
            history = await self._client.fetch_long_short_ratio_history(symbol, "5m", limit=1)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"OKX long/short ratio: {e}") from e
        if not history:
            raise ValueError(f"No long/short ratio data for {symbol}")
        entry = history[0]
        ratio = float(entry["longShortRatio"])
        return LongShortRatio(
            symbol=symbol,
            long_short_ratio=ratio,
            long_ratio=ratio / (1 + ratio),
            short_ratio=1.0 / (1 + ratio),
            timestamp=int(entry.get("timestamp") or 0),
        )
```

Note: `@_retry()` does not catch `RateLimitExceeded` (only `NetworkError`, `ExchangeNotAvailable`, `TimeoutError`), so the `RateLimitHit` propagates through to `MarketDataService.get_*`, where `TTLCache.get_or_fetch` catches it for stale-cache fallback.

- [ ] **Step 5: Implement derivatives in SimulatedExchange**

Add to `src/integrations/exchange/simulated.py`. First, update the import:

```python
from src.integrations.exchange.base import (
    Balance,
    BaseExchange,
    Candle,
    FillEvent,
    FundingRate,
    LongShortRatio,
    OpenInterest,
    Order,
    Position,
    Ticker,
)
```

Also add a **new** top-level `ccxt` import and `RateLimitHit`. Note: `src/integrations/exchange/simulated.py` currently does **not** import `ccxt` at module level — it only does a dynamic `import ccxt.pro as ccxtpro` inside `start()`. We need `ccxt.RateLimitExceeded` available in the method bodies below, so add:

```python
import ccxt.async_support as ccxt  # new top-level import (for RateLimitExceeded)
from src.utils.cache import RateLimitHit
```

Then add three methods to `SimulatedExchange`, before the `start()` method. Each wraps the ccxt.pro call and converts `ccxt.RateLimitExceeded` → `RateLimitHit`:

```python
    async def fetch_funding_rate(self, symbol: str) -> FundingRate:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            data = await self._ccxt.fetch_funding_rate(symbol)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim funding rate: {e}") from e
        return FundingRate(
            symbol=data["symbol"],
            rate=float(data["fundingRate"]),
            next_funding_time=int(data.get("fundingTimestamp") or 0),
            timestamp=int(data.get("timestamp") or 0),
        )

    async def fetch_open_interest(self, symbol: str) -> OpenInterest:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            data = await self._ccxt.fetch_open_interest(symbol)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim open interest: {e}") from e
        return OpenInterest(
            symbol=data["symbol"],
            open_interest=float(data.get("openInterestAmount") or 0),
            open_interest_value=float(data.get("openInterestValue") or 0),
            timestamp=int(data.get("timestamp") or 0),
        )

    async def fetch_long_short_ratio(self, symbol: str) -> LongShortRatio:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            history = await self._ccxt.fetch_long_short_ratio_history(symbol, "5m", limit=1)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim long/short ratio: {e}") from e
        if not history:
            raise ValueError(f"No long/short ratio data for {symbol}")
        entry = history[0]
        ratio = float(entry["longShortRatio"])
        return LongShortRatio(
            symbol=symbol,
            long_short_ratio=ratio,
            long_ratio=ratio / (1 + ratio),
            short_ratio=1.0 / (1 + ratio),
            timestamp=int(entry.get("timestamp") or 0),
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_derivatives_data.py -v`
Expected: all PASS

- [ ] **Step 7: Run full test suite**

Run: `pytest --tb=short -q`
Expected: all tests pass (0 failures)

- [ ] **Step 8: Commit**

```bash
git add src/integrations/exchange/base.py src/integrations/exchange/okx.py \
       src/integrations/exchange/simulated.py tests/test_derivatives_data.py
git commit -m "feat(N2): add derivatives types and exchange implementations (funding, OI, LSR)"
```

---

### Task 6: MarketDataService — Derivatives with Cache

**Files:**
- Modify: `src/integrations/market_data.py`
- Test: `tests/test_derivatives_data.py` (append)

- [ ] **Step 1: Write tests for MarketDataService derivatives methods**

Append to `tests/test_derivatives_data.py`:

```python
# --- MarketDataService cached derivatives ---

async def test_market_data_get_funding_rate():
    from src.integrations.market_data import MarketDataService

    exchange = AsyncMock()
    exchange.fetch_funding_rate.return_value = FundingRate(
        symbol="BTC/USDT:USDT", rate=0.0001,
        next_funding_time=1713265200000, timestamp=1713261600000,
    )
    svc = MarketDataService(exchange)
    result = await svc.get_funding_rate("BTC/USDT:USDT")
    assert result.rate == 0.0001
    exchange.fetch_funding_rate.assert_called_once_with("BTC/USDT:USDT")


async def test_market_data_get_open_interest():
    from src.integrations.market_data import MarketDataService

    exchange = AsyncMock()
    exchange.fetch_open_interest.return_value = OpenInterest(
        symbol="BTC/USDT:USDT", open_interest=12345.0,
        open_interest_value=4_820_000_000.0, timestamp=1713261600000,
    )
    svc = MarketDataService(exchange)
    result = await svc.get_open_interest("BTC/USDT:USDT")
    assert result.open_interest_value == 4_820_000_000.0


async def test_market_data_get_long_short_ratio():
    from src.integrations.market_data import MarketDataService

    exchange = AsyncMock()
    exchange.fetch_long_short_ratio.return_value = LongShortRatio(
        symbol="BTC/USDT:USDT", long_short_ratio=1.35,
        long_ratio=0.574, short_ratio=0.426, timestamp=1713261600000,
    )
    svc = MarketDataService(exchange)
    result = await svc.get_long_short_ratio("BTC/USDT:USDT")
    assert result.long_short_ratio == 1.35


async def test_market_data_derivatives_cache_hit():
    from src.integrations.market_data import MarketDataService

    exchange = AsyncMock()
    exchange.fetch_funding_rate.return_value = FundingRate(
        symbol="BTC/USDT:USDT", rate=0.0001,
        next_funding_time=1713265200000, timestamp=1713261600000,
    )
    svc = MarketDataService(exchange)
    await svc.get_funding_rate("BTC/USDT:USDT")
    await svc.get_funding_rate("BTC/USDT:USDT")
    assert exchange.fetch_funding_rate.call_count == 1  # cache hit


async def test_market_data_derivatives_cache_by_symbol():
    from src.integrations.market_data import MarketDataService

    exchange = AsyncMock()
    exchange.fetch_funding_rate.side_effect = [
        FundingRate("BTC/USDT:USDT", 0.0001, 0, 0),
        FundingRate("ETH/USDT:USDT", 0.0002, 0, 0),
    ]
    svc = MarketDataService(exchange)
    btc = await svc.get_funding_rate("BTC/USDT:USDT")
    eth = await svc.get_funding_rate("ETH/USDT:USDT")
    assert btc.rate == 0.0001
    assert eth.rate == 0.0002
    assert exchange.fetch_funding_rate.call_count == 2  # independent cache keys
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `pytest tests/test_derivatives_data.py -v -k "market_data"`
Expected: FAIL — `MarketDataService has no attribute 'get_funding_rate'`

- [ ] **Step 3: Implement MarketDataService derivatives methods**

Edit `src/integrations/market_data.py` incrementally (do NOT replace the whole file):

**1) Add imports** — after the existing `from src.integrations.exchange.base import BaseExchange, Ticker` line:

```python
from src.integrations.exchange.base import BaseExchange, FundingRate, LongShortRatio, OpenInterest, Ticker
from src.utils.cache import TTLCache

_DERIVATIVES_TTL = 180.0  # 3 minutes
```

**2) Add cache field to `__init__`** — after `self._exchange = exchange`:

```python
        self._derivatives_cache = TTLCache()
```

**3) Append three new methods** at the end of the `MarketDataService` class:

```python
    async def get_funding_rate(self, symbol: str) -> FundingRate:
        return await self._derivatives_cache.get_or_fetch(
            f"funding:{symbol}", _DERIVATIVES_TTL,
            lambda: self._exchange.fetch_funding_rate(symbol),
        )

    async def get_open_interest(self, symbol: str) -> OpenInterest:
        return await self._derivatives_cache.get_or_fetch(
            f"oi:{symbol}", _DERIVATIVES_TTL,
            lambda: self._exchange.fetch_open_interest(symbol),
        )

    async def get_long_short_ratio(self, symbol: str) -> LongShortRatio:
        return await self._derivatives_cache.get_or_fetch(
            f"lsr:{symbol}", _DERIVATIVES_TTL,
            lambda: self._exchange.fetch_long_short_ratio(symbol),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_derivatives_data.py -v`
Expected: all PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest --tb=short -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/integrations/market_data.py tests/test_derivatives_data.py
git commit -m "feat(N2): add cached derivatives methods to MarketDataService"
```

---

### Task 7: Config + Tool Implementations

**Files:**
- Modify: `src/config.py` (add `NewsConfig`)
- Modify: `src/agent/tools_perception.py` (add 3 tool functions)
- Modify: `tests/test_config.py` (add NewsConfig tests)
- Create: `tests/test_news_tools.py`

- [ ] **Step 1: Write tests for NewsConfig**

Append to `tests/test_config.py`:

```python
def test_news_config_defaults():
    from src.config import NewsConfig
    config = NewsConfig()
    assert config.enabled is True


def test_settings_with_news(tmp_path: Path):
    """news.enabled=false disables NewsService initialization."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
news:
  enabled: false
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.news.enabled is False


def test_settings_without_news(tmp_path: Path):
    """news section is optional and defaults to enabled."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("exchange:\n  name: okx\n")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.news.enabled is True
```

- [ ] **Step 2: Run config tests to verify new ones fail**

Run: `pytest tests/test_config.py -v -k "news"`
Expected: FAIL — `ImportError: cannot import name 'NewsConfig'`

- [ ] **Step 3: Implement NewsConfig**

In `src/config.py`, add `NewsConfig` class (after `AlertsConfig`):

```python
class NewsConfig(BaseModel):
    enabled: bool = True
```

All news data sources (CoinDesk News / FGI / ForexFactory / OKX announcements) are keyless. `NewsConfig` is just a feature toggle — when `enabled=False`, `NewsService` is not initialized and the three news-related tools return "News service not configured".

Add field to `Settings` class:

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
```

`load_settings()` needs no change related to news config.

- [ ] **Step 4: Run config tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: all PASS

- [ ] **Step 5: Write tests for tool implementations**

```python
# tests/test_news_tools.py
"""Tests for get_market_news, get_critical_alerts, get_derivatives_data tools."""
import pytest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.integrations.exchange.base import FundingRate, LongShortRatio, OpenInterest, Ticker
from src.integrations.news.models import InformationEvent


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


def _make_deps(**overrides):
    return MockDeps(**overrides)


def _event(title="News", source="coindesk", symbols=None, hours_ago=0,
           category="news", content="", importance="medium"):
    return InformationEvent(
        timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        source=source, category=category, importance=importance,
        title=title, content=content, symbols=symbols or [],
    )


# ===== get_market_news =====

async def test_market_news_no_service():
    from src.agent.tools_perception import get_market_news
    deps = _make_deps(news=None)
    result = await get_market_news(deps)
    assert "not configured" in result.lower()


async def test_market_news_format():
    from src.agent.tools_perception import get_market_news

    news_svc = AsyncMock()
    news_svc.get_news.return_value = (
        [_event("BTC Rally", symbols=["BTC"], content="CoinDesk")],
        [_event("EU Regulation", content="Reuters")],
    )
    fgi = _event("23 / 100 — Extreme Fear", source="alternative_me",
                 category="fgi", content="Extreme Fear")
    news_svc.get_fear_greed_index.return_value = fgi

    deps = _make_deps(news=news_svc)
    result = await get_market_news(deps)

    assert "Fear & Greed Index" in result
    assert "23 / 100" in result
    assert "Symbol News" in result
    assert "BTC Rally" in result
    assert "CoinDesk" in result
    assert "General Crypto News" in result
    assert "EU Regulation" in result


async def test_market_news_empty_results():
    """News service is configured but all upstream returned empty — show graceful message."""
    from src.agent.tools_perception import get_market_news

    news_svc = AsyncMock()
    news_svc.get_news.return_value = ([], [])
    news_svc.get_fear_greed_index.return_value = _event(
        "50 / 100 — Neutral", source="alternative_me", category="fgi",
    )

    deps = _make_deps(news=news_svc)
    result = await get_market_news(deps)
    assert "Fear & Greed Index" in result
    assert "No recent headlines" in result or "temporarily unavailable" in result.lower()


async def test_market_news_passes_filter():
    from src.agent.tools_perception import get_market_news

    news_svc = AsyncMock()
    news_svc.get_news.return_value = ([], [])
    news_svc.get_fear_greed_index.return_value = None

    deps = _make_deps(news=news_svc)
    await get_market_news(deps, news_filter="positive")
    news_svc.get_news.assert_called_once_with("BTC/USDT:USDT", "positive")


async def test_market_news_filters_non_currency_tags():
    """CoinDesk CATEGORY_DATA mixes tickers and thematic tags;
    the display layer should show only currency tickers."""
    from src.agent.tools_perception import get_market_news

    news_svc = AsyncMock()
    # symbols contains both real tickers and thematic tags — formatter
    # must strip the thematic ones so the "Currencies" line stays clean.
    noisy_event = _event(
        "BTC Rally",
        symbols=["BTC", "ETH", "MARKET", "MACROECONOMICS", "CRYPTOCURRENCY"],
        content="CoinDesk",
    )
    news_svc.get_news.return_value = ([noisy_event], [])
    news_svc.get_fear_greed_index.return_value = None

    deps = _make_deps(news=news_svc)
    result = await get_market_news(deps)

    # Tickers appear
    assert "Currencies: BTC, ETH" in result
    # Thematic tags do NOT leak into Currencies line
    assert "MARKET" not in result
    assert "MACROECONOMICS" not in result
    assert "CRYPTOCURRENCY" not in result


async def test_market_news_all_non_currency_tags_shows_dash():
    """When every tag is a thematic label, render em-dash."""
    from src.agent.tools_perception import get_market_news

    news_svc = AsyncMock()
    only_themes = _event(
        "General regulation news",
        symbols=["MARKET", "REGULATION", "MACROECONOMICS"],
        content="Reuters",
    )
    news_svc.get_news.return_value = ([], [only_themes])
    news_svc.get_fear_greed_index.return_value = None

    deps = _make_deps(news=news_svc)
    result = await get_market_news(deps)
    assert "Currencies: —" in result


# ===== get_critical_alerts =====

async def test_critical_alerts_no_service():
    from src.agent.tools_perception import get_critical_alerts
    deps = _make_deps(news=None)
    result = await get_critical_alerts(deps)
    assert "not configured" in result.lower()


async def test_critical_alerts_format():
    from src.agent.tools_perception import get_critical_alerts

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = [
        _event("Delisting XYZ", source="okx_announcement", category="announcement"),
    ]
    news_svc.get_macro_events.return_value = [
        _event("FOMC Meeting", source="forexfactory", category="macro_event",
               importance="high", content="Previous: N/A | Forecast: N/A"),
    ]

    deps = _make_deps(news=news_svc)
    result = await get_critical_alerts(deps)

    assert "Exchange Announcements" in result
    assert "Delisting XYZ" in result
    assert "Upcoming Macro Events" in result
    assert "FOMC Meeting" in result
    assert "Impact: High" in result
    # Calendar scope reminder should always be present (spec §3.2)
    assert "macro calendar covers current week only" in result


async def test_critical_alerts_empty():
    from src.agent.tools_perception import get_critical_alerts

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = []
    news_svc.get_macro_events.return_value = []

    deps = _make_deps(news=news_svc)
    result = await get_critical_alerts(deps)

    assert "No exchange announcements" in result
    assert "No upcoming macro events" in result
    # Footer still there even when both sections are empty
    assert "macro calendar covers current week only" in result


async def test_critical_alerts_passes_params():
    from src.agent.tools_perception import get_critical_alerts

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = []
    news_svc.get_macro_events.return_value = []

    deps = _make_deps(news=news_svc)
    await get_critical_alerts(deps, lookback_hours=48, lookahead_hours=24)
    news_svc.get_announcements.assert_called_once_with(48)
    news_svc.get_macro_events.assert_called_once_with(24)


# ===== get_derivatives_data =====

async def test_derivatives_data_format():
    from src.agent.tools_perception import get_derivatives_data

    ts_ms = int(datetime(2026, 4, 16, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)
    market_data = AsyncMock()
    market_data.get_funding_rate.return_value = FundingRate(
        symbol="BTC/USDT:USDT", rate=0.000125,
        next_funding_time=int((datetime.now(timezone.utc) + timedelta(hours=3, minutes=42)).timestamp() * 1000),
        timestamp=ts_ms,
    )
    market_data.get_open_interest.return_value = OpenInterest(
        symbol="BTC/USDT:USDT", open_interest=12345.0,
        open_interest_value=4_820_000_000.0, timestamp=ts_ms,
    )
    market_data.get_long_short_ratio.return_value = LongShortRatio(
        symbol="BTC/USDT:USDT", long_short_ratio=1.35,
        long_ratio=0.574, short_ratio=0.426, timestamp=ts_ms,
    )

    deps = _make_deps(market_data=market_data)
    result = await get_derivatives_data(deps)

    assert "Derivatives Data" in result
    assert "Funding Rate" in result
    assert "+0.0125%" in result
    assert "longs pay shorts" in result
    assert "Open Interest" in result
    assert "$4.82B" in result
    assert "Long/Short Ratio" in result
    assert "1.35" in result
    assert "57.4%" in result
    # Data freshness indicator present (spec §3.3)
    assert "Data as of: 2026-04-16 14:30 UTC" in result


async def test_derivatives_data_negative_funding():
    from src.agent.tools_perception import get_derivatives_data

    market_data = AsyncMock()
    market_data.get_funding_rate.return_value = FundingRate(
        symbol="BTC/USDT:USDT", rate=-0.0003,
        next_funding_time=int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp() * 1000),
        timestamp=0,
    )
    market_data.get_open_interest.return_value = OpenInterest(
        symbol="BTC/USDT:USDT", open_interest=0, open_interest_value=500_000_000.0, timestamp=0,
    )
    market_data.get_long_short_ratio.return_value = LongShortRatio(
        symbol="BTC/USDT:USDT", long_short_ratio=0.8,
        long_ratio=0.444, short_ratio=0.556, timestamp=0,
    )

    deps = _make_deps(market_data=market_data)
    result = await get_derivatives_data(deps)
    assert "shorts pay longs" in result
    assert "-0.0300%" in result


async def test_derivatives_data_partial_failure():
    from src.agent.tools_perception import get_derivatives_data

    market_data = AsyncMock()
    market_data.get_funding_rate.side_effect = Exception("API down")
    market_data.get_open_interest.return_value = OpenInterest(
        symbol="BTC/USDT:USDT", open_interest=0, open_interest_value=1_000_000_000.0, timestamp=0,
    )
    market_data.get_long_short_ratio.side_effect = Exception("timeout")

    deps = _make_deps(market_data=market_data)
    result = await get_derivatives_data(deps)

    assert "Open Interest" in result
    assert "$1.00B" in result
    assert "unavailable" in result.lower()  # degradation messages


async def test_derivatives_data_custom_symbol():
    from src.agent.tools_perception import get_derivatives_data

    market_data = AsyncMock()
    market_data.get_funding_rate.return_value = FundingRate("ETH/USDT:USDT", 0.0001, 0, 0)
    market_data.get_open_interest.return_value = OpenInterest("ETH/USDT:USDT", 0, 100_000_000.0, 0)
    market_data.get_long_short_ratio.return_value = LongShortRatio("ETH/USDT:USDT", 1.0, 0.5, 0.5, 0)

    deps = _make_deps(market_data=market_data)
    result = await get_derivatives_data(deps, symbol="ETH/USDT:USDT")
    assert "ETH/USDT:USDT" in result
```

- [ ] **Step 6: Run tool tests to verify they fail**

Run: `pytest tests/test_news_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_market_news'`

- [ ] **Step 7: Implement tool functions in tools_perception.py**

The file already has `from typing import TYPE_CHECKING` at the top. Update that line to include `Literal`:

```python
# Change from:
from typing import TYPE_CHECKING
# To:
from typing import TYPE_CHECKING, Literal
```

Then append the three tool functions at the end of the file. The
`_NON_CURRENCY_CATEGORIES` frozenset is a module-level constant so it is
built once rather than on every tool invocation:

```python
# Display-layer filter for CoinDesk CATEGORY_DATA — strips thematic tags
# (MARKET / CRYPTOCURRENCY / ...) from the "Currencies" line rendered to
# the Agent. Tags stay in InformationEvent.symbols for matching logic;
# this only affects display.
_NON_CURRENCY_CATEGORIES = frozenset({
    "ALTCOIN", "BUSINESS", "CRYPTOCURRENCY", "EXCHANGE", "FIAT",
    "MACROECONOMICS", "MARKET", "REGULATION", "TECHNOLOGY", "TRADING",
})


def _fmt_currencies(syms: list[str]) -> str:
    filtered = [s for s in syms if s not in _NON_CURRENCY_CATEGORIES]
    return ", ".join(filtered) if filtered else "—"


async def get_market_news(
    deps: TradingDeps,
    news_filter: Literal["positive", "negative", "neutral"] | None = None,
) -> str:
    """Get crypto news headlines + Fear & Greed Index."""
    import asyncio

    if deps.news is None:
        return "News service not configured."

    from src.integrations.news.models import extract_base_currency

    base = extract_base_currency(deps.symbol)

    # Fetch news + FGI concurrently (independent upstreams, independent caches).
    news_result, fgi_result = await asyncio.gather(
        deps.news.get_news(deps.symbol, news_filter),
        deps.news.get_fear_greed_index(),
        return_exceptions=True,
    )
    if isinstance(news_result, Exception):
        symbol_news, general_news = [], []
    else:
        symbol_news, general_news = news_result
    fgi = None if isinstance(fgi_result, Exception) else fgi_result

    sections: list[str] = []

    # FGI section
    if fgi is not None:
        date_str = fgi.timestamp.strftime("%Y-%m-%d")
        sections.append(
            f"=== Fear & Greed Index ===\n"
            f"Value: {fgi.title}\n"
            f"(Updated: {date_str})"
        )
    else:
        sections.append("=== Fear & Greed Index ===\nFGI service temporarily unavailable.")

    has_news = bool(symbol_news or general_news)
    if has_news:
        if symbol_news:
            lines: list[str] = []
            for e in symbol_news:
                ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
                source_name = e.content if e.content else e.source
                lines.append(f"[{ts}] {e.title}\n  Source: {source_name} | Currencies: {_fmt_currencies(e.symbols)}")
            sections.append(
                f"=== Symbol News ({base}, {len(symbol_news)}) ===\n"
                + "\n\n".join(lines)
            )

        if general_news:
            lines = []
            for e in general_news:
                ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
                source_name = e.content if e.content else e.source
                lines.append(f"[{ts}] {e.title}\n  Source: {source_name} | Currencies: {_fmt_currencies(e.symbols)}")
            sections.append(
                f"=== General Crypto News ({len(general_news)}) ===\n"
                + "\n\n".join(lines)
            )
    else:
        sections.append("=== News ===\nNo recent headlines (or news service temporarily unavailable).")

    return "\n\n".join(sections)


async def get_critical_alerts(
    deps: TradingDeps,
    lookback_hours: int = 24,
    lookahead_hours: int = 12,
) -> str:
    """Get critical alerts: exchange announcements + upcoming macro events."""
    import asyncio

    if deps.news is None:
        return "News service not configured."

    # Parallelize announcements + macro events to minimize wall-clock latency.
    # Each call has independent upstream sources and caches, so gather is safe.
    announcements, macro_events = await asyncio.gather(
        deps.news.get_announcements(lookback_hours),
        deps.news.get_macro_events(lookahead_hours),
        return_exceptions=True,
    )
    # NewsService methods already swallow per-source errors and return [],
    # but gather isolates cross-method failures if the Service contract changes.
    if isinstance(announcements, Exception):
        announcements = []
    if isinstance(macro_events, Exception):
        macro_events = []

    sections: list[str] = []

    # Announcements
    if announcements:
        lines = [e.timestamp.strftime("[%Y-%m-%d %H:%M] ") + e.title for e in announcements]
        sections.append(
            f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
            + "\n".join(lines)
        )
    else:
        sections.append(
            f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
            "No exchange announcements."
        )

    # Macro events
    if macro_events:
        lines = []
        for e in macro_events:
            ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
            impact = e.importance.capitalize()
            line = f"[{ts}] {e.title} — Impact: {impact}"
            if e.content:
                line += f"\n  {e.content}"
            lines.append(line)
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
            + "\n".join(lines)
        )
    else:
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
            "No upcoming macro events."
        )

    # Footer: calendar scope reminder (spec §3.2). Kept unconditional so the
    # Agent doesn't need weekday-awareness to understand the limitation.
    sections.append(
        "Note: macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )

    return "\n\n".join(sections)


async def get_derivatives_data(
    deps: TradingDeps,
    symbol: str | None = None,
) -> str:
    """Get derivatives market data: funding rate, open interest, long/short ratio."""
    import asyncio
    from datetime import datetime, timezone

    symbol = symbol or deps.symbol
    sections = [f"=== Derivatives Data ({symbol}) ==="]
    errors: list[str] = []
    timestamps_ms: list[int] = []

    # Fetch all three concurrently — each has independent cache + upstream.
    # gather(return_exceptions=True) gives us per-method success/failure.
    funding, oi, lsr = await asyncio.gather(
        deps.market_data.get_funding_rate(symbol),
        deps.market_data.get_open_interest(symbol),
        deps.market_data.get_long_short_ratio(symbol),
        return_exceptions=True,
    )

    # Funding rate
    if isinstance(funding, Exception):
        errors.append("Funding rate temporarily unavailable")
    else:
        direction = "longs pay shorts" if funding.rate >= 0 else "shorts pay longs"
        sign = "Positive rate" if funding.rate >= 0 else "Negative rate"
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        remaining_ms = max(0, funding.next_funding_time - now_ms)
        hours = remaining_ms // (3600 * 1000)
        minutes = (remaining_ms % (3600 * 1000)) // (60 * 1000)
        sections.append(
            f"Funding Rate: {funding.rate:+.4%} (next settlement in {hours}h {minutes}m)\n"
            f"  {sign} — {direction}"
        )
        if funding.timestamp:
            timestamps_ms.append(funding.timestamp)

    # Open interest
    if isinstance(oi, Exception):
        errors.append("Open interest temporarily unavailable")
    else:
        if oi.open_interest_value >= 1e9:
            oi_str = f"${oi.open_interest_value / 1e9:.2f}B"
        elif oi.open_interest_value >= 1e6:
            oi_str = f"${oi.open_interest_value / 1e6:.2f}M"
        else:
            oi_str = f"${oi.open_interest_value:,.0f}"
        sections.append(f"Open Interest: {oi_str}")
        if oi.timestamp:
            timestamps_ms.append(oi.timestamp)

    # Long/short ratio
    if isinstance(lsr, Exception):
        errors.append("Long/short ratio temporarily unavailable")
    else:
        sections.append(
            f"Long/Short Ratio: {lsr.long_short_ratio:.2f} "
            f"({lsr.long_ratio:.1%} long / {lsr.short_ratio:.1%} short)"
        )
        if lsr.timestamp:
            timestamps_ms.append(lsr.timestamp)

    sections.extend(errors)

    # Show oldest timestamp — lets the Agent detect stale-cache fallback data.
    if timestamps_ms:
        oldest_dt = datetime.fromtimestamp(min(timestamps_ms) / 1000, tz=timezone.utc)
        sections.append(f"Data as of: {oldest_dt.strftime('%Y-%m-%d %H:%M')} UTC")

    return "\n".join(sections)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_news_tools.py tests/test_config.py -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add src/config.py src/agent/tools_perception.py \
       tests/test_config.py tests/test_news_tools.py
git commit -m "feat(N2): add NewsConfig and tool implementations (news, alerts, derivatives)"
```

---

### Task 8: Tool Registration + TradingDeps + System Prompt

**Files:**
- Modify: `src/agent/trader.py` (add `news` to TradingDeps, register 3 tools)
- Modify: `src/agent/persona.py` (add tool guidance to Layer 1)
- Modify: `tests/test_tools.py` (add `news` to MockDeps)

- [ ] **Step 1: Update TradingDeps and register tools in trader.py**

In `src/agent/trader.py`, add `from typing import Literal` to the imports, then add the `news` field to `TradingDeps`:

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
```

In `create_trader_agent()`, add three new tool registrations after `get_performance` and before the Execution Tools section:

```python
    @agent.tool
    async def get_market_news(
        ctx: RunContext[TradingDeps],
        news_filter: Literal["positive", "negative", "neutral"] | None = None,
    ) -> str:
        """Get recent crypto news headlines and market sentiment.
        news_filter: 'positive', 'negative', 'neutral'. Default: no filter (latest mix).
        Returns 10 headlines (5 symbol-specific + 5 general crypto) + Fear & Greed Index.
        Output ~500-700 tokens."""
        from src.agent.tools_perception import get_market_news as _impl

        return await _impl(ctx.deps, news_filter)

    @agent.tool
    async def get_critical_alerts(
        ctx: RunContext[TradingDeps],
        lookback_hours: int = 24,
        lookahead_hours: int = 12,
    ) -> str:
        """Get critical alerts: exchange announcements and upcoming macro events.
        lookback_hours: how far back to check announcements (default 24h).
        lookahead_hours: how far ahead to check macro events (default 12h).
        Output ~100-400 tokens (often empty when no relevant events are scheduled)."""
        from src.agent.tools_perception import get_critical_alerts as _impl

        return await _impl(ctx.deps, lookback_hours, lookahead_hours)

    @agent.tool
    async def get_derivatives_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
    ) -> str:
        """Get derivatives market data: funding rate, open interest, long/short ratio.
        When symbol is None, uses the currently traded pair.
        Output ~150-250 tokens."""
        from src.agent.tools_perception import get_derivatives_data as _impl

        return await _impl(ctx.deps, symbol)
```

- [ ] **Step 2: Update MockDeps in test_tools.py**

Add `news: object = None` to the `MockDeps` dataclass in `tests/test_tools.py`:

```python
@dataclass
class MockDeps:
    symbol: str
    timeframe: str
    market_data: AsyncMock
    exchange: AsyncMock
    technical: MagicMock
    memory: AsyncMock
    session_id: str = "test-session"
    db_engine: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None
    initial_balance: float = 10000.0
    metrics: object = None
    news: object = None
```

- [ ] **Step 3: Update Layer 1 system prompt in persona.py**

In `src/agent/persona.py`, append to the end of `_build_layer1()` return string, before the closing `"""`, add:

```
- **Market news**: Use get_market_news to check crypto news headlines + Fear & Greed Index (0 = max fear, 100 = max greed). Returns 10 headlines (5 symbol-specific + 5 general). Usually call without news_filter; use 'positive' / 'negative' / 'neutral' when you want a specific sentiment lens.
- **Critical alerts**: Use get_critical_alerts before trading to scan exchange announcements (maintenance, delistings, parameter changes) over the past lookback_hours and upcoming macro events (FOMC, CPI, NFP with impact level) within the next lookahead_hours. Often empty when nothing is scheduled. Macro calendar covers the current week only — Friday evening / weekend calls may miss next week's early events.
- **Derivatives structure**: Use get_derivatives_data for funding rate, open interest, and long/short ratio. Positive funding rate means longs pay shorts, negative means shorts pay longs (settles every 8h). Open interest is total outstanding contracts. Long/short ratio is the ratio of long vs short account positions.
```

- [ ] **Step 4: Run all tests to verify nothing breaks**

Run: `pytest --tb=short -q`
Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add src/agent/trader.py src/agent/persona.py tests/test_tools.py
git commit -m "feat(N2): register market intelligence tools and update system prompt"
```

---

### Task 9: App Integration

**Files:**
- Modify: `src/cli/app.py` (initialize NewsService, inject into deps, close on shutdown)
- Wizard: **no change** — all news sources are keyless, no Step 6 needed.

- [ ] **Step 1: Integrate NewsService in app.py**

In `src/cli/app.py`, in the `build_services()` function, after `MetricsService` initialization (around line 280), add:

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

In the `TradingDeps(...)` constructor call, add the `news` field:

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
    )
```

`build_services` return tuple stays unchanged — `deps.news` already holds the `NewsService` reference, consistent with how `deps.memory` / `deps.metrics` / `deps.technical` are kept (none of them are in the return tuple).

In the shutdown section, after `await exchange.close()`, add:

```python
    if deps.news is not None:
        await deps.news.close()
```

Order rationale: close `exchange` first so its WebSocket drains, then close `news` (which closes the HTTP client). Avoids pending HTTP requests during shutdown.

- [ ] **Step 2: Run full test suite to verify nothing breaks**

Run: `pytest --tb=short -q`
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add src/cli/app.py
git commit -m "feat(N2): wire NewsService into app startup and shutdown"
```

- [ ] **Step 4: Run full test suite one final time**

Run: `pytest --tb=short -q`
Expected: all tests pass (original 417 + new tests)

- [ ] **Step 5: Final commit (if any remaining changes)**

```bash
git status
# If clean, skip. Otherwise:
git add -A
git commit -m "feat(N2): final integration cleanup"
```
