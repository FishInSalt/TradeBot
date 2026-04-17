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
| `src/integrations/news/cryptopanic.py` | Create | CryptoPanic API client — news headlines |
| `src/integrations/news/fear_greed.py` | Create | Alternative.me FGI client |
| `src/integrations/news/calendar.py` | Create | ForexFactory macro calendar client |
| `src/integrations/news/okx_announcements.py` | Create | OKX `/support/announcements` client |
| `src/integrations/news/okx_status.py` | Create | OKX `/system/status` client |
| `src/integrations/news/service.py` | Create | `NewsService` — aggregation, caching, quota protection |
| `src/integrations/exchange/base.py` | Modify | Add `FundingRate`, `OpenInterest`, `LongShortRatio` dataclasses + 3 abstract methods |
| `src/integrations/exchange/okx.py` | Modify | Implement 3 derivatives methods with `@_retry()` |
| `src/integrations/exchange/simulated.py` | Modify | Implement 3 derivatives methods via `self._ccxt` |
| `src/integrations/market_data.py` | Modify | Add `get_funding_rate()`, `get_open_interest()`, `get_long_short_ratio()` with TTLCache |
| `src/config.py` | Modify | Add `NewsConfig` + wire into `Settings` |
| `src/agent/tools_perception.py` | Modify | Add `get_market_news()`, `get_critical_alerts()`, `get_derivatives_data()` |
| `src/agent/trader.py` | Modify | Add `news` field to `TradingDeps`, register 3 new tool wrappers |
| `src/agent/persona.py` | Modify | Add 3-tool guidance to Layer 1 |
| `src/cli/app.py` | Modify | Initialize `NewsService`, inject into deps, close on shutdown |
| `src/cli/wizard.py` | Modify | Add Step 6: CryptoPanic API key configuration |
| `tests/test_cache.py` | Create | TTLCache unit tests |
| `tests/test_news_clients.py` | Create | All 5 client unit tests |
| `tests/test_news_service.py` | Create | NewsService integration tests |
| `tests/test_derivatives_data.py` | Create | Derivatives types + exchange + MarketDataService tests |
| `tests/test_news_tools.py` | Create | Tool implementation tests |
| `tests/test_config.py` | Modify | Add `NewsConfig` tests |
| `tests/test_tools.py` | Modify | Add `news` field to `MockDeps` |

---

## Pre-work Verification (run before Task 1)

External APIs can drift from what the spec describes. Run these curl checks before implementation to confirm response shapes are still valid — adjust parsers in Task 2/3 if fields have changed.

- [ ] **P1: Verify httpx is already a project dependency**

`httpx>=0.27` is already declared in `pyproject.toml` (verified on 2026-04-17). No `uv add` needed. If a future refactor removes it, run:
```bash
grep '^"httpx' pyproject.toml  # must return a match
```

- [ ] **P2: Verify CryptoPanic v1 API schema**

CryptoPanic migrated some users to a "Developer API v2" in late 2024; the free `/api/v1/posts/` path may now return different field names. Run with a real `auth_token`:
```bash
curl -s "https://cryptopanic.com/api/v1/posts/?auth_token=$CRYPTOPANIC_API_KEY&limit=1&filter=rising" | jq '.results[0] | {title, published_at, url, source_title: .source.title, currencies}'
```
Expected shape per spec §2.1:
- `title` (str)
- `published_at` (ISO8601 str with `Z`)
- `url` (str)
- `source.title` (str) — original media name
- `currencies` (list of `{code, title}` or `null`)

If any field moved (e.g. under `metadata.` or renamed), update `CryptoPanicClient._parse` in Task 2 Step 3 accordingly. If the endpoint returns 404/410, CryptoPanic may have retired v1 for new keys — pivot to v2 or degrade the feature.

- [ ] **P3: Verify ForexFactory calendar feed**

Non-official feed, no SLA. Run:
```bash
curl -s "https://nfs.faireconomy.media/ff_calendar_thisweek.json" | jq '.[0] | {title, country, date, impact, forecast, previous}'
```
Expected: `title`, `country` (e.g. "USD"), `date` (ISO8601), `impact` (High/Medium/Low), `forecast`, `previous`. If the feed is 404/unreachable, accept graceful degradation as designed (spec §3.4), but note that all calendar tests in Task 3 will be mock-only — **no integration smoke test** is possible.

- [ ] **P4: Verify OKX public endpoints**

Both endpoints are public (no auth). Confirm they still exist:
```bash
curl -s "https://www.okx.com/api/v5/support/announcements?annType=announcements-delistings" | jq '.data[0] | {title, url, annType, pTime}'
curl -s "https://www.okx.com/api/v5/system/status?state=scheduled" | jq '.data[0] | {title, state, begin, end}'
```
Expected fields listed in spec §2.4. If `code` is not `"0"` or the schema changed, update clients in Task 3.

**Proceed to Task 1 only after all four checks pass (or you've noted and handled the drift).**

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
        source="cryptopanic",
        category="news",
        importance="medium",
        title="Test headline",
    )
    assert event.source == "cryptopanic"
    assert event.symbols == []
    assert event.content == ""
    assert event.url == ""


def test_information_event_with_all_fields():
    from datetime import datetime, timezone
    from src.integrations.news.models import InformationEvent

    event = InformationEvent(
        timestamp=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc),
        source="cryptopanic",
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

    r1 = await cache.get_or_fetch("key", 0.01, fetch)  # 10ms TTL
    assert r1 == "data_1"

    await asyncio.sleep(0.02)
    r2 = await cache.get_or_fetch("key", 0.01, fetch)
    assert r2 == "data_2"


async def test_cache_429_extends_ttl_with_stale():
    from src.utils.cache import TTLCache, RateLimitHit

    cache = TTLCache()
    await cache.get_or_fetch("key", 0.01, AsyncMock(return_value="good"))

    await asyncio.sleep(0.02)  # let it expire

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
    await cache.get_or_fetch("key", 0.01, AsyncMock(return_value="good"))
    await asyncio.sleep(0.02)

    async def fetch_429():
        raise RateLimitHit("429")

    await cache.get_or_fetch("key", 0.01, fetch_429)

    # Now cache should be valid (30min TTL) — new fetch should not be called
    spy = AsyncMock(return_value="new")
    result = await cache.get_or_fetch("key", 0.01, spy)
    assert result == "good"
    spy.assert_not_called()


async def test_get_stale_returns_expired_data():
    from src.utils.cache import TTLCache

    cache = TTLCache()
    await cache.get_or_fetch("key", 0.01, AsyncMock(return_value="data"))
    await asyncio.sleep(0.02)
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
      - cryptopanic   → original media name (e.g. "CoinDesk")
      - alternative_me → classification string (e.g. "Extreme Fear")
      - forexfactory  → "Previous: X | Forecast: Y" for macro events
      - okx_announcement / okx_status → unused (empty string)

    Each tool section formats events from a single source, so the per-source
    convention is safe in practice. If a new tool ever renders mixed sources,
    add a dedicated field rather than overloading `content` further.
    """

    timestamp: datetime
    source: str  # "cryptopanic" / "alternative_me" / "okx_announcement" / "okx_status" / "forexfactory"
    category: str  # "news" / "fgi" / "announcement" / "maintenance" / "macro_event"
    importance: Literal["low", "medium", "high"]
    title: str
    content: str = ""
    url: str = ""
    symbols: list[str] = field(default_factory=list)


def extract_base_currency(symbol: str) -> str:
    """Extract base currency from a trading pair symbol.

    BTC/USDT:USDT → BTC, ETH/USDT:USDT → ETH.
    """
    return symbol.split("/")[0]
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

### Task 2: News Data Clients — CryptoPanic + Fear & Greed

**Files:**
- Create: `src/integrations/news/cryptopanic.py`, `src/integrations/news/fear_greed.py`
- Test: `tests/test_news_clients.py`

- [ ] **Step 1: Write tests for CryptoPanic client**

```python
# tests/test_news_clients.py
"""Tests for individual news/alert API clients."""
from datetime import datetime, timezone

import httpx
import pytest

from src.utils.cache import RateLimitHit


# ===== CryptoPanic =====

CRYPTOPANIC_RESPONSE = {
    "results": [
        {
            "title": "Bitcoin Breaks $90K as Institutional Inflows Surge",
            "published_at": "2026-04-16T14:30:00Z",
            "source": {"title": "CoinTelegraph"},
            "currencies": [
                {"code": "BTC", "title": "Bitcoin"},
                {"code": "ETH", "title": "Ethereum"},
            ],
            "url": "https://example.com/btc-90k",
        },
        {
            "title": "EU Passes Crypto Regulation",
            "published_at": "2026-04-16T12:00:00Z",
            "source": {"title": "CoinDesk"},
            "currencies": None,
            "url": "https://example.com/eu-crypto",
        },
    ],
}


async def test_cryptopanic_parse_response():
    from src.integrations.news.cryptopanic import CryptoPanicClient

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=CRYPTOPANIC_RESPONSE))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CryptoPanicClient(http, "test_key")
        events = await client.fetch_posts()

    assert len(events) == 2
    assert events[0].title == "Bitcoin Breaks $90K as Institutional Inflows Surge"
    assert events[0].source == "cryptopanic"
    assert events[0].category == "news"
    assert events[0].symbols == ["BTC", "ETH"]
    assert events[0].content == "CoinTelegraph"  # original source name
    assert events[0].url == "https://example.com/btc-90k"
    # No currencies → empty list
    assert events[1].symbols == []


async def test_cryptopanic_filter_param():
    from src.integrations.news.cryptopanic import CryptoPanicClient

    captured_params: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = CryptoPanicClient(http, "my_key")
        await client.fetch_posts(news_filter="bullish")

    assert captured_params["auth_token"] == "my_key"
    assert captured_params["filter"] == "bullish"
    assert captured_params["limit"] == "20"


async def test_cryptopanic_no_filter_omits_param():
    from src.integrations.news.cryptopanic import CryptoPanicClient

    captured_params: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.update(dict(request.url.params))
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = CryptoPanicClient(http, "key")
        await client.fetch_posts(news_filter=None)

    assert "filter" not in captured_params


async def test_cryptopanic_429_raises_rate_limit():
    from src.integrations.news.cryptopanic import CryptoPanicClient

    transport = httpx.MockTransport(lambda req: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as http:
        client = CryptoPanicClient(http, "key")
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

Run: `pytest tests/test_news_clients.py -v -k "cryptopanic or fgi"`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement CryptoPanic client**

```python
# src/integrations/news/cryptopanic.py
from __future__ import annotations

import httpx

from src.integrations.news.models import InformationEvent
from src.utils.cache import RateLimitHit

_CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"


class CryptoPanicClient:
    """CryptoPanic API client — crypto news headlines with sentiment filtering."""

    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def fetch_posts(self, news_filter: str | None = None) -> list[InformationEvent]:
        params: dict[str, str | int] = {"auth_token": self._api_key, "limit": 20}
        if news_filter is not None:
            params["filter"] = news_filter

        resp = await self._http.get(_CRYPTOPANIC_URL, params=params)
        if resp.status_code == 429:
            raise RateLimitHit("CryptoPanic rate limited")
        resp.raise_for_status()

        return self._parse(resp.json())

    @staticmethod
    def _parse(data: dict) -> list[InformationEvent]:
        from datetime import datetime, timezone

        events: list[InformationEvent] = []
        for post in data.get("results", []):
            raw_currencies = post.get("currencies") or []
            symbols = [c["code"] for c in raw_currencies if "code" in c]
            source_name = (post.get("source") or {}).get("title", "")
            pub = post.get("published_at", "")
            try:
                ts = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ts = datetime.now(timezone.utc)
            events.append(
                InformationEvent(
                    timestamp=ts,
                    source="cryptopanic",
                    category="news",
                    importance="medium",
                    title=post.get("title", ""),
                    content=source_name,
                    url=post.get("url", ""),
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

_FGI_URL = "https://api.alternative.me/fng/"


class FearGreedClient:
    """Alternative.me Fear & Greed Index client."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch(self) -> InformationEvent | None:
        from datetime import datetime, timezone

        resp = await self._http.get(_FGI_URL)
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

Run: `pytest tests/test_news_clients.py -v -k "cryptopanic or fgi"`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/integrations/news/cryptopanic.py src/integrations/news/fear_greed.py \
       tests/test_news_clients.py
git commit -m "feat(N2): add CryptoPanic and Fear & Greed Index clients"
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
}
OKX_TRADING_UPDATES_RESPONSE = {
    "code": "0",
    "data": [
        {
            "title": "Contract parameter change — ETH/USDT",
            "url": "https://www.okx.com/ann/200",
            "annType": "trading-updates-us-aus",
            "pTime": "1713300000000",
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

logger = logging.getLogger(__name__)

_FOREXFACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


class ForexFactoryClient:
    """ForexFactory economic calendar client (via faireconomy.media feed)."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def fetch_events(self) -> list[InformationEvent]:
        from datetime import datetime, timezone

        resp = await self._http.get(_FOREXFACTORY_URL)
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
    """OKX /support/announcements client — delistings + trading rule changes."""

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

            for item in resp.json().get("data", []):
                p_time = int(item.get("pTime", 0))
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

### Task 4: NewsService — Aggregation + Caching + Quota Protection

**Files:**
- Create: `src/integrations/news/service.py`
- Test: `tests/test_news_service.py`

- [ ] **Step 1: Write tests for NewsService core behavior**

```python
# tests/test_news_service.py
"""Tests for NewsService — aggregation, caching, quota, degradation."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.news.models import InformationEvent
from src.utils.cache import TTLCache


def _make_news_event(title="Test", source="cryptopanic", category="news",
                     symbols=None, hours_ago=0):
    return InformationEvent(
        timestamp=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        source=source,
        category=category,
        importance="medium",
        title=title,
        symbols=symbols or [],
    )


def _make_service(api_key="test_key"):
    """Build a NewsService with mocked HTTP clients and injected mock httpx."""
    from src.integrations.news.service import NewsService

    # Inject a mock http client — prevents a real httpx.AsyncClient from being
    # created and leaked as an orphan (which would trigger ResourceWarning).
    svc = NewsService(api_key=api_key, http=AsyncMock())
    if svc._cryptopanic is not None:
        svc._cryptopanic = AsyncMock()
    svc._fgi = AsyncMock()
    svc._calendar = AsyncMock()
    svc._announcements = AsyncMock()
    svc._status = AsyncMock()
    return svc


# --- get_news: basic ---

async def test_get_news_splits_by_symbol():
    svc = _make_service()
    svc._cryptopanic.fetch_posts.return_value = [
        _make_news_event("BTC up", symbols=["BTC"]),
        _make_news_event("BTC mining", symbols=["BTC"]),
        _make_news_event("ETH news", symbols=["ETH"]),
        _make_news_event("General 1"),
        _make_news_event("General 2"),
        _make_news_event("General 3"),
    ]
    sym, gen = await svc.get_news("BTC/USDT:USDT")
    assert len(sym) == 2  # only 2 BTC articles
    assert len(gen) == 3  # general fills remaining (total target=10, but only 4 general available)
    assert all("BTC" in e.symbols for e in sym)


async def test_get_news_fills_from_general_when_symbol_short():
    svc = _make_service()
    # Only 1 BTC article, 8 general
    posts = [_make_news_event("BTC", symbols=["BTC"])]
    posts += [_make_news_event(f"Gen {i}") for i in range(8)]
    svc._cryptopanic.fetch_posts.return_value = posts
    sym, gen = await svc.get_news("BTC/USDT:USDT", max_per_group=5)
    assert len(sym) == 1
    assert len(gen) == 8  # fill up to 9 total (10 - 1)


async def test_get_news_passes_filter():
    svc = _make_service()
    svc._cryptopanic.fetch_posts.return_value = []
    await svc.get_news("BTC/USDT:USDT", news_filter="bullish")
    svc._cryptopanic.fetch_posts.assert_called_once_with("bullish")


# --- get_news: no API key ---

async def test_get_news_no_api_key():
    svc = _make_service(api_key=None)
    sym, gen = await svc.get_news("BTC/USDT:USDT")
    assert sym == []
    assert gen == []


# --- get_news: caching ---

async def test_get_news_cache_hit():
    svc = _make_service()
    svc._cryptopanic.fetch_posts.return_value = [_make_news_event("Cached")]
    await svc.get_news("BTC/USDT:USDT")
    await svc.get_news("BTC/USDT:USDT")
    # fetch_posts called only once (second call is cache hit)
    assert svc._cryptopanic.fetch_posts.call_count == 1


async def test_get_news_different_filters_separate_cache():
    svc = _make_service()
    svc._cryptopanic.fetch_posts.return_value = []
    await svc.get_news("BTC/USDT:USDT", news_filter="bullish")
    await svc.get_news("BTC/USDT:USDT", news_filter="bearish")
    assert svc._cryptopanic.fetch_posts.call_count == 2


# --- get_news: quota ---

async def test_daily_quota_cap():
    svc = _make_service()
    svc._cryptopanic.fetch_posts.return_value = [_make_news_event("News")]
    svc._cryptopanic_daily_calls = svc._cryptopanic_daily_quota  # at limit
    svc._cryptopanic_daily_reset_date = datetime.now(timezone.utc).date()  # prevent reset

    # Should return stale cache if available, else empty
    sym, gen = await svc.get_news("BTC/USDT:USDT")
    assert sym == [] and gen == []
    svc._cryptopanic.fetch_posts.assert_not_called()


async def test_daily_quota_uses_stale_cache():
    svc = _make_service()
    svc._cryptopanic.fetch_posts.return_value = [_make_news_event("Old", symbols=["BTC"])]
    await svc.get_news("BTC/USDT:USDT")  # populate cache

    # Manually expire the cache entry (direct _store access is the cleanest way
    # to simulate time passing without actual sleeps or TTL monkey-patching).
    for key, (data, _created_at, ttl) in list(svc._cache._store.items()):
        svc._cache._store[key] = (data, 0.0, ttl)  # created_at=0 → always expired

    svc._cryptopanic_daily_calls = svc._cryptopanic_daily_quota  # at limit
    svc._cryptopanic_daily_reset_date = datetime.now(timezone.utc).date()

    sym, gen = await svc.get_news("BTC/USDT:USDT")
    assert len(sym) + len(gen) > 0  # stale cache used


async def test_daily_quota_resets_on_new_day():
    svc = _make_service()
    svc._cryptopanic_daily_calls = svc._cryptopanic_daily_quota  # at limit
    svc._cryptopanic_daily_reset_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    svc._cryptopanic.fetch_posts.return_value = []
    await svc.get_news("BTC/USDT:USDT")
    # Counter should have reset and fetch should have been called
    assert svc._cryptopanic.fetch_posts.call_count == 1


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

    svc = NewsService(api_key=None)  # no injection → creates its own
    try:
        mock_http = AsyncMock()
        # Replace with mock to verify close is called on owned client
        await svc._http.aclose()  # close the real one to avoid leak
        svc._http = mock_http
        svc._owns_http = True
        await svc.close()
        mock_http.aclose.assert_called_once()
    finally:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_news_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.integrations.news.service'`

- [ ] **Step 3: Implement NewsService**

```python
# src/integrations/news/service.py
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from src.integrations.news.calendar import ForexFactoryClient
from src.integrations.news.cryptopanic import CryptoPanicClient
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

_DEFAULT_DAILY_QUOTA = 180  # CryptoPanic free tier: 200/day, 10% safety margin


class NewsService:
    """Aggregates all news/alert data sources with caching and quota protection."""

    def __init__(
        self,
        api_key: str | None = None,
        http: httpx.AsyncClient | None = None,
        daily_quota: int = _DEFAULT_DAILY_QUOTA,
    ) -> None:
        # Accept injected http client for testability; default to real one otherwise.
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None  # only close http if we created it
        self._cache = TTLCache()
        self._api_key = api_key

        # Clients
        self._cryptopanic: CryptoPanicClient | None = (
            CryptoPanicClient(self._http, api_key) if api_key else None
        )
        self._fgi = FearGreedClient(self._http)
        self._calendar = ForexFactoryClient(self._http)
        self._announcements = OKXAnnouncementsClient(self._http)
        self._status = OKXStatusClient(self._http)

        # CryptoPanic daily quota tracking
        self._cryptopanic_daily_quota = daily_quota
        self._cryptopanic_daily_calls = 0
        self._cryptopanic_daily_reset_date: date | None = None

    @property
    def has_cryptopanic(self) -> bool:
        """True when a CryptoPanic API key is configured and the client is live."""
        return self._cryptopanic is not None

    def _check_quota_reset(self) -> None:
        today = datetime.now(timezone.utc).date()
        if self._cryptopanic_daily_reset_date != today:
            self._cryptopanic_daily_calls = 0
            self._cryptopanic_daily_reset_date = today

    async def get_news(
        self,
        symbol: str,
        news_filter: str | None = None,
        max_per_group: int = 5,
    ) -> tuple[list[InformationEvent], list[InformationEvent]]:
        """Fetch news headlines, split into (symbol_news, general_news).

        Returns two lists: symbol-specific headlines and general crypto news.
        If symbol_news < max_per_group, general_news gets extra slots (total = max_per_group * 2).
        """
        if self._cryptopanic is None:
            return [], []

        self._check_quota_reset()
        cache_key = f"news:{news_filter}"

        # Quota check — before any API call
        if self._cryptopanic_daily_calls >= self._cryptopanic_daily_quota:
            stale = self._cache.get_stale(cache_key)
            if stale is not None:
                logger.warning("CryptoPanic daily quota reached, using stale cache")
                return self._split_news(stale, symbol, max_per_group)
            logger.warning("CryptoPanic daily quota reached, no cache available")
            return [], []

        async def _fetch() -> list[InformationEvent]:
            result = await self._cryptopanic.fetch_posts(news_filter)
            self._cryptopanic_daily_calls += 1  # count after success (failed calls don't consume quota)
            return result

        try:
            all_posts = await self._cache.get_or_fetch(cache_key, _NEWS_TTL, _fetch)
        except RateLimitHit:
            # TTLCache already tried stale cache and didn't have any
            logger.warning("CryptoPanic 429 with no cache, degrading")
            return [], []
        except Exception:
            logger.warning("CryptoPanic fetch failed", exc_info=True)
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
git commit -m "feat(N2): add NewsService with caching, quota protection, and degradation"
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
    open_interest: float  # contracts or base currency units
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

Then add three methods to `OKXExchange`, before the `close()` method:

```python
    @_retry()
    async def fetch_funding_rate(self, symbol: str) -> FundingRate:
        data = await self._client.fetch_funding_rate(symbol)
        return FundingRate(
            symbol=data["symbol"],
            rate=float(data["fundingRate"]),
            next_funding_time=int(data.get("fundingTimestamp") or 0),
            timestamp=int(data.get("timestamp") or 0),
        )

    @_retry()
    async def fetch_open_interest(self, symbol: str) -> OpenInterest:
        data = await self._client.fetch_open_interest(symbol)
        return OpenInterest(
            symbol=data["symbol"],
            open_interest=float(data.get("openInterestAmount") or 0),
            open_interest_value=float(data.get("openInterestValue") or 0),
            timestamp=int(data.get("timestamp") or 0),
        )

    @_retry()
    async def fetch_long_short_ratio(self, symbol: str) -> LongShortRatio:
        history = await self._client.fetch_long_short_ratio_history(symbol, "5m", limit=1)
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

Then add three methods to `SimulatedExchange`, before the `start()` method:

```python
    async def fetch_funding_rate(self, symbol: str) -> FundingRate:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        data = await self._ccxt.fetch_funding_rate(symbol)
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
        data = await self._ccxt.fetch_open_interest(symbol)
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
        history = await self._ccxt.fetch_long_short_ratio_history(symbol, "5m", limit=1)
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
    assert config.cryptopanic_api_key == ""
    assert config.cryptopanic_daily_quota == 180


def test_news_config_custom_quota(tmp_path: Path):
    """Paid-tier users can raise the quota."""
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
news:
  cryptopanic_daily_quota: 500
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.news.cryptopanic_daily_quota == 500


def test_settings_with_news(tmp_path: Path):
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("""
exchange:
  name: okx
news:
  enabled: true
  cryptopanic_api_key: my_key
""")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.news.enabled is True
    assert settings.news.cryptopanic_api_key == "my_key"


def test_settings_without_news(tmp_path: Path):
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("exchange:\n  name: okx\n")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={})
    assert settings.news.enabled is True
    assert settings.news.cryptopanic_api_key == ""


def test_settings_news_env_override(tmp_path: Path):
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text("exchange:\n  name: okx\n")
    from src.config import load_settings
    settings = load_settings(settings_file, env_overrides={"CRYPTOPANIC_API_KEY": "env_key"})
    assert settings.news.cryptopanic_api_key == "env_key"
```

- [ ] **Step 2: Run config tests to verify new ones fail**

Run: `pytest tests/test_config.py -v -k "news"`
Expected: FAIL — `ImportError: cannot import name 'NewsConfig'`

- [ ] **Step 3: Implement NewsConfig**

In `src/config.py`, add `NewsConfig` class (after `AlertsConfig`):

```python
class NewsConfig(BaseModel):
    enabled: bool = True
    cryptopanic_api_key: str = ""
    # Free tier: ~200/day. Default 180 = 10% safety margin.
    # Raise this if you have a paid CryptoPanic plan.
    cryptopanic_daily_quota: int = 180
```

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

In `load_settings()`, add env override for CryptoPanic API key, after the exchange env overrides:

```python
    # News config: env override for API key
    news = data.get("news", {})
    cp_key = env_overrides.get("CRYPTOPANIC_API_KEY", "")
    if cp_key:
        news["cryptopanic_api_key"] = cp_key
    data["news"] = news
```

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


def _event(title="News", source="cryptopanic", symbols=None, hours_ago=0,
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


async def test_market_news_no_api_key():
    from src.agent.tools_perception import get_market_news

    news_svc = AsyncMock()
    news_svc.get_news.return_value = ([], [])
    news_svc.get_fear_greed_index.return_value = _event(
        "50 / 100 — Neutral", source="alternative_me", category="fgi",
    )
    news_svc.has_cryptopanic = False

    deps = _make_deps(news=news_svc)
    result = await get_market_news(deps)
    assert "Fear & Greed Index" in result
    assert "unavailable" in result.lower() and "cryptopanic" in result.lower()


async def test_market_news_passes_filter():
    from src.agent.tools_perception import get_market_news

    news_svc = AsyncMock()
    news_svc.get_news.return_value = ([], [])
    news_svc.get_fear_greed_index.return_value = None
    news_svc.has_cryptopanic = True

    deps = _make_deps(news=news_svc)
    await get_market_news(deps, news_filter="bullish")
    news_svc.get_news.assert_called_once_with("BTC/USDT:USDT", "bullish")


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


async def test_critical_alerts_empty():
    from src.agent.tools_perception import get_critical_alerts

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = []
    news_svc.get_macro_events.return_value = []

    deps = _make_deps(news=news_svc)
    result = await get_critical_alerts(deps)

    assert "No exchange announcements" in result
    assert "No upcoming macro events" in result


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

    market_data = AsyncMock()
    market_data.get_funding_rate.return_value = FundingRate(
        symbol="BTC/USDT:USDT", rate=0.000125,
        next_funding_time=int((datetime.now(timezone.utc) + timedelta(hours=3, minutes=42)).timestamp() * 1000),
        timestamp=0,
    )
    market_data.get_open_interest.return_value = OpenInterest(
        symbol="BTC/USDT:USDT", open_interest=12345.0,
        open_interest_value=4_820_000_000.0, timestamp=0,
    )
    market_data.get_long_short_ratio.return_value = LongShortRatio(
        symbol="BTC/USDT:USDT", long_short_ratio=1.35,
        long_ratio=0.574, short_ratio=0.426, timestamp=0,
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

Then append the three tool functions at the end of the file:

```python
async def get_market_news(
    deps: TradingDeps,
    news_filter: Literal["rising", "bullish", "bearish", "important"] | None = None,
) -> str:
    """Get crypto news headlines + Fear & Greed Index."""
    if deps.news is None:
        return "News service not configured."

    from src.integrations.news.models import extract_base_currency

    base = extract_base_currency(deps.symbol)

    # Fetch news + FGI
    symbol_news, general_news = await deps.news.get_news(deps.symbol, news_filter)
    fgi = await deps.news.get_fear_greed_index()

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

    # News sections
    has_news = bool(symbol_news or general_news)
    if has_news:
        if symbol_news:
            lines: list[str] = []
            for e in symbol_news:
                ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
                currencies = ", ".join(e.symbols) if e.symbols else "—"
                source_name = e.content if e.content else e.source
                lines.append(f"[{ts}] {e.title}\n  Source: {source_name} | Currencies: {currencies}")
            sections.append(
                f"=== Symbol News ({base}, {len(symbol_news)}) ===\n"
                + "\n\n".join(lines)
            )

        if general_news:
            lines = []
            for e in general_news:
                ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
                currencies = ", ".join(e.symbols) if e.symbols else "—"
                source_name = e.content if e.content else e.source
                lines.append(f"[{ts}] {e.title}\n  Source: {source_name} | Currencies: {currencies}")
            sections.append(
                f"=== General Crypto News ({len(general_news)}) ===\n"
                + "\n\n".join(lines)
            )
    else:
        if deps.news.has_cryptopanic:
            sections.append("=== News ===\nNo recent headlines.")
        else:
            sections.append("=== News ===\nCryptoPanic API key not configured — headlines unavailable.")

    return "\n\n".join(sections)


async def get_critical_alerts(
    deps: TradingDeps,
    lookback_hours: int = 24,
    lookahead_hours: int = 12,
) -> str:
    """Get critical alerts: exchange announcements + upcoming macro events."""
    if deps.news is None:
        return "News service not configured."

    announcements = await deps.news.get_announcements(lookback_hours)
    macro_events = await deps.news.get_macro_events(lookahead_hours)

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

    return "\n\n".join(sections)


async def get_derivatives_data(
    deps: TradingDeps,
    symbol: str | None = None,
) -> str:
    """Get derivatives market data: funding rate, open interest, long/short ratio."""
    from datetime import datetime, timezone

    symbol = symbol or deps.symbol
    sections = [f"=== Derivatives Data ({symbol}) ==="]
    errors: list[str] = []

    # Funding rate
    try:
        funding = await deps.market_data.get_funding_rate(symbol)
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
    except Exception:
        errors.append("Funding rate temporarily unavailable")

    # Open interest
    try:
        oi = await deps.market_data.get_open_interest(symbol)
        if oi.open_interest_value >= 1e9:
            oi_str = f"${oi.open_interest_value / 1e9:.2f}B"
        elif oi.open_interest_value >= 1e6:
            oi_str = f"${oi.open_interest_value / 1e6:.2f}M"
        else:
            oi_str = f"${oi.open_interest_value:,.0f}"
        sections.append(f"Open Interest: {oi_str}")
    except Exception:
        errors.append("Open interest temporarily unavailable")

    # Long/short ratio
    try:
        lsr = await deps.market_data.get_long_short_ratio(symbol)
        sections.append(
            f"Long/Short Ratio: {lsr.long_short_ratio:.2f} "
            f"({lsr.long_ratio:.1%} long / {lsr.short_ratio:.1%} short)"
        )
    except Exception:
        errors.append("Long/short ratio temporarily unavailable")

    sections.extend(errors)
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
        news_filter: Literal["rising", "bullish", "bearish", "important"] | None = None,
    ) -> str:
        """Get recent crypto news headlines and market sentiment.
        news_filter: 'rising' (trending), 'bullish', 'bearish', 'important'. Default: no filter (latest).
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
- **Market news**: Use get_market_news to check news headlines and Fear & Greed sentiment. Usually call without news_filter — the default gives latest headlines sufficient for most decisions. Only use news_filter when you need a specific lens: 'rising' (trending), 'bullish', 'bearish', 'important'. The Fear & Greed Index ranges from 0 (maximum fear) to 100 (maximum greed).
- **Critical alerts**: Use get_critical_alerts before placing trades to check for exchange announcements (delistings, contract maintenance, parameter changes) and upcoming macro events (FOMC, CPI, NFP with impact level). Often returns empty when no relevant events are scheduled. Note: the macro calendar covers the current week only — Friday evening/weekend may miss next week's early events.
- **Derivatives data**: Use get_derivatives_data for funding rate, open interest, and long/short ratio. Funding rate: positive means longs pay shorts, negative means shorts pay longs (settles every 8h). Open interest is total outstanding contracts. Long/short ratio shows account position distribution.
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

### Task 9: App Integration + Wizard

**Files:**
- Modify: `src/cli/app.py` (initialize NewsService, inject into deps, close on shutdown)
- Modify: `src/cli/wizard.py` (add Step 6 for CryptoPanic API key)

- [ ] **Step 1: Integrate NewsService in app.py**

In `src/cli/app.py`, in the `build_services()` function, after `MetricsService` initialization (around line 280), add:

```python
    # News service
    news_service = None
    if settings.news.enabled:
        from src.integrations.news.service import NewsService
        # Priority (per spec §10.2): env CRYPTOPANIC_API_KEY > .credentials file > none
        # settings.news.cryptopanic_api_key already holds the env value (or empty) from load_settings.
        cp_key = settings.news.cryptopanic_api_key or getattr(result, 'cryptopanic_api_key', '') or None
        news_service = NewsService(
            api_key=cp_key,
            daily_quota=settings.news.cryptopanic_daily_quota,
        )
        if cp_key:
            sc.print("News: ON (CryptoPanic + FGI + alerts)")
        else:
            sc.print("News: ON (FGI + alerts only — no CryptoPanic API key)")
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

Update `build_services` return to include news_service so it can be closed:

Change the return statement to:

```python
    return exchange, deps, agent, budget, news_service
```

In the `run()` function, update the unpacking:

```python
    exchange, deps, agent, budget, news_service = build_services(
        result, engine, session_id, sc, settings,
    )
```

In the shutdown section (after `await exchange.close()`), add:

```python
    if news_service is not None:
        await news_service.close()
```

- [ ] **Step 2: Add Wizard Step 6 for CryptoPanic API key**

In `src/cli/wizard.py`, add a new step function:

```python
async def _validate_cryptopanic_key(key: str, console: Console) -> bool:
    """Test CryptoPanic API key. Returns True if valid or temporarily unavailable."""
    import httpx

    console.print("  Testing API key...")
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                resp = await http.get(
                    "https://cryptopanic.com/api/v1/posts/",
                    params={"auth_token": key, "limit": 1},
                )
            if resp.status_code == 200:
                console.print("  [green]OK[/]")
                return True
            if resp.status_code == 429:
                console.print("  [yellow]Rate limited — key accepted (quota temporarily exhausted)[/]")
                return True
            if resp.status_code in (401, 403):
                console.print(f"  [red]Invalid key (HTTP {resp.status_code})[/]")
                return False
            console.print(f"  [yellow]Unexpected HTTP {resp.status_code}, treating as valid[/]")
            return True
        except (httpx.TimeoutException, httpx.ConnectError):
            if attempt == 0:
                console.print("  [yellow]Timeout, retrying...[/]")
            else:
                console.print("  [yellow]Timeout — key accepted (network issue)[/]")
                return True
    return True  # unreachable, but satisfy type checker


async def _step_news(config_dir: Path, console: Console) -> dict:
    """Step 6: News configuration (CryptoPanic API key).

    Priority (per spec §10.2): env CRYPTOPANIC_API_KEY > .credentials > none.
    Reuses the .credentials file (same as OKX credentials) for persistence.
    Loaded key is validated before reuse; saved after successful new entry.
    Invalid saved keys are cleared to avoid re-prompting the same failure.
    """
    import os

    console.print("\n[bold]Step 6: News (optional)[/]")

    # Env var takes priority — skip interactive; let load_settings/app wire it.
    if os.getenv("CRYPTOPANIC_API_KEY", "").strip():
        console.print("  [dim]Using CRYPTOPANIC_API_KEY from environment[/]")
        return {"cryptopanic_api_key": ""}

    # Try saved credentials
    saved = _load_credentials(config_dir)
    saved_entry = saved.get("cryptopanic", {})
    saved_key = saved_entry.get("api_key", "")
    if saved_key:
        console.print("  [dim]Saved CryptoPanic credentials found[/]")
        if Confirm.ask("  Use saved CryptoPanic API key?", default=True, console=console):
            if await _validate_cryptopanic_key(saved_key, console):
                return {"cryptopanic_api_key": saved_key}
            # Invalid — clear it so we don't re-prompt the same broken key next startup
            _save_credentials(config_dir, "cryptopanic", {"api_key": ""})
            console.print("  [yellow]Saved key invalid, removed from .credentials[/]")

    console.print("  CryptoPanic provides crypto news headlines with sentiment.")
    console.print("  Get a free API key at: https://cryptopanic.com/developers/api/")
    has_key = Confirm.ask("  Configure CryptoPanic API key?", default=False, console=console)
    if has_key:
        while True:
            key = Prompt.ask("  API key", password=True, console=console)
            if await _validate_cryptopanic_key(key, console):
                _save_credentials(config_dir, "cryptopanic", {"api_key": key})
                console.print("  [green]Saved to config/.credentials[/]")
                return {"cryptopanic_api_key": key}
            if not Confirm.ask("  Try another key?", default=True, console=console):
                break
    console.print("  [dim]Skipped — Fear & Greed Index and alerts still available[/]")
    return {"cryptopanic_api_key": ""}
```

Note: `_load_credentials()` and `_save_credentials()` already exist in `wizard.py` (used for OKX credentials) and work with arbitrary service names as dict keys — no changes to those helpers needed.

Add `cryptopanic_api_key` field to `WizardResult`:

```python
@dataclass
class WizardResult:
    # Exchange
    exchange_type: str
    fee_rate: float | None
    initial_balance: float
    api_credentials: dict | None
    # Trading pair
    symbol: str
    timeframe: str
    # Model
    model_config: ModelConfig
    model: Any
    # Risk & scheduling
    scheduler_interval_min: int
    approval_enabled: bool
    alert_enabled: bool
    alert_window_min: int | None
    alert_threshold_pct: float | None
    token_budget: int
    # Persona
    persona: PersonaConfig
    # News
    cryptopanic_api_key: str = ""
    # Session
    session_name: str = ""
```

In `run_wizard()`, insert the new step call **after `persona_data = _step_persona(...)` and before the `_show_summary` block** (i.e., as the last step inside the `while True:` loop before the summary):

```python
            persona_data = _step_persona(trader_defaults, console)
            news_data = await _step_news(config_dir, console)  # NEW — inserts as Step 6
```

And include it in the `data` merge (replace the existing line that builds `data`):

```python
            data = {**exchange_data, **trading_data, **model_data, **risk_data, **persona_data, **news_data}
```

Note: `run_wizard()` is already declared `async def` (because `_step_model` is async), so `await _step_news(...)` works without changing the function signature.

Also add it to `_show_summary`:

```python
    cp_key = data.get("cryptopanic_api_key", "")
    news_str = "ON (CryptoPanic + FGI)" if cp_key else "ON (FGI only)"
    table.add_row("News", news_str)
```

- [ ] **Step 3: Run full test suite to verify nothing breaks**

Run: `pytest --tb=short -q`
Expected: all tests pass

- [ ] **Step 4: Update .env.example**

Append to `.env.example` (optional non-interactive override; wizard persists to `config/.credentials` by default):

```
# Optional: CryptoPanic API key (overrides config/.credentials if both set)
CRYPTOPANIC_API_KEY=your_cryptopanic_key_here
```

- [ ] **Step 5: Commit**

```bash
git add src/cli/app.py src/cli/wizard.py .env.example
git commit -m "feat(N2): integrate NewsService in app startup and add wizard news step"
```

- [ ] **Step 6: Run full test suite one final time**

Run: `pytest --tb=short -q`
Expected: all tests pass (original 417 + new tests)

- [ ] **Step 7: Final commit (if any remaining changes)**

```bash
git status
# If clean, skip. Otherwise:
git add -A
git commit -m "feat(N2): final integration cleanup"
```
