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
            "CATEGORY_DATA": [],
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
    assert e0.content == "CoinTelegraph"
    assert e0.url == "https://example.com/btc-90k"
    assert e0.timestamp == datetime.fromtimestamp(1776398458, tz=timezone.utc)
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
