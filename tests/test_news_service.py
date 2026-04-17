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
    # gen_count budget = max_per_group * 2 - sym_count = 10 - 1 = 9 slots,
    # only 8 general posts available → gen=8. Total = 1 + 8 = 9.
    assert len(gen) == 8


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


async def test_get_announcements_filters_announcements_by_lookback():
    """okx_announcements events are filtered by publish-time lookback; older
    than `lookback_hours` are dropped."""
    svc = _make_service()
    svc._announcements.fetch.return_value = [
        _make_news_event("Recent", source="okx_announcement", hours_ago=1),
        _make_news_event("Old", source="okx_announcement", hours_ago=48),
    ]
    svc._status.fetch.return_value = []
    result = await svc.get_announcements(lookback_hours=24)
    assert len(result) == 1
    assert result[0].title == "Recent"


async def test_get_announcements_status_bypasses_lookback():
    """okx_status events are NOT filtered by lookback — the OKX API already
    scopes results via state=scheduled|ongoing, and timestamps may be future
    (scheduled) or recent past (ongoing). Both must reach the caller."""
    svc = _make_service()
    svc._announcements.fetch.return_value = []
    # One far-future scheduled maintenance + one ancient ongoing anomaly.
    svc._status.fetch.return_value = [
        _make_news_event("Future scheduled", source="okx_status",
                         category="maintenance", hours_ago=-48),  # 48h in future
        _make_news_event("Very old", source="okx_status",
                         category="maintenance", hours_ago=240),  # 10d in past
    ]
    result = await svc.get_announcements(lookback_hours=24)
    assert len(result) == 2
    titles = {e.title for e in result}
    assert titles == {"Future scheduled", "Very old"}


async def test_get_announcements_mixed_per_source_filtering():
    """Mixed inputs: okx_ann filtered by lookback; okx_status passed through."""
    svc = _make_service()
    svc._announcements.fetch.return_value = [
        _make_news_event("Recent ann", source="okx_announcement", hours_ago=1),
        _make_news_event("Old ann", source="okx_announcement", hours_ago=48),
    ]
    svc._status.fetch.return_value = [
        _make_news_event("Future maintenance", source="okx_status",
                         category="maintenance", hours_ago=-6),
    ]
    result = await svc.get_announcements(lookback_hours=24)
    titles = {e.title for e in result}
    # "Old ann" dropped (outside lookback); the future maintenance survives.
    assert titles == {"Recent ann", "Future maintenance"}


async def test_get_announcements_partial_failure():
    svc = _make_service()
    svc._announcements.fetch.side_effect = Exception("down")
    svc._status.fetch.return_value = [
        _make_news_event("Status OK", source="okx_status"),
    ]
    result = await svc.get_announcements(lookback_hours=24)
    assert len(result) == 1


async def test_get_announcements_all_sources_down_returns_none():
    """Both OKX sources errored → signal unavailability via None (spec §3.5)."""
    svc = _make_service()
    svc._announcements.fetch.side_effect = Exception("down")
    svc._status.fetch.side_effect = Exception("down too")
    result = await svc.get_announcements(lookback_hours=24)
    assert result is None


async def test_get_announcements_empty_is_not_none():
    """Both OKX sources return empty lists → empty list, NOT None (quiet window)."""
    svc = _make_service()
    svc._announcements.fetch.return_value = []
    svc._status.fetch.return_value = []
    result = await svc.get_announcements(lookback_hours=24)
    assert result == []


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


async def test_get_macro_events_failure_returns_none():
    """ForexFactory feed down → signal unavailability via None (spec §3.5)."""
    svc = _make_service()
    svc._calendar.fetch_events.side_effect = Exception("feed down")
    result = await svc.get_macro_events(lookahead_hours=12)
    assert result is None


async def test_get_macro_events_empty_is_not_none():
    """Feed reachable but no events in window → empty list, NOT None."""
    svc = _make_service()
    svc._calendar.fetch_events.return_value = []
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
