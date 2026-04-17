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
