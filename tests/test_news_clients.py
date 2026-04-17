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
    # Empty previous/forecast must render as "N/A" per spec §3.2 output sample
    assert events[0].content == "Previous: N/A | Forecast: N/A"
    assert events[1].title == "US Initial Jobless Claims"
    assert events[1].importance == "medium"
    assert events[1].content == "Previous: 215K | Forecast: 220K"


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
# Pre-work P4b left the schema unconfirmed. Cover both shapes so whichever
# one the live probe reveals, OKXStatusClient still parses correctly.

_OKX_STATUS_ITEM = {
    "title": "System upgrade",
    "state": "scheduled",
    "begin": "1713308400000",
    "end": "1713315600000",
    "maintType": "0",
    "serviceType": "1",
    "system": "trading",
}

OKX_STATUS_RESPONSE_FLAT = {"code": "0", "data": [_OKX_STATUS_ITEM]}
OKX_STATUS_RESPONSE_NESTED = {"code": "0", "data": [{"details": [_OKX_STATUS_ITEM]}]}


@pytest.mark.parametrize(
    "payload",
    [OKX_STATUS_RESPONSE_FLAT, OKX_STATUS_RESPONSE_NESTED],
    ids=["flat", "nested"],
)
async def test_okx_status_parse(payload):
    from src.integrations.news.okx_status import OKXStatusClient

    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as http:
        client = OKXStatusClient(http)
        events = await client.fetch()

    # Both schemas should yield at least one event per state fetched
    # (each call pulls scheduled + ongoing; ongoing returns the same payload).
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
