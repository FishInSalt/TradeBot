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
    # Previous/Forecast line should appear below the event per spec §3.2
    assert "Previous: N/A | Forecast: N/A" in result
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


async def test_critical_alerts_services_unavailable():
    """NewsService returns None → render 'temporarily unavailable' per section
    so the Agent distinguishes a quiet window from an outage (spec §3.5)."""
    from src.agent.tools_perception import get_critical_alerts

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = None
    news_svc.get_macro_events.return_value = None

    deps = _make_deps(news=news_svc)
    result = await get_critical_alerts(deps)

    assert "Exchange announcements service temporarily unavailable" in result
    assert "Macro events service temporarily unavailable" in result
    # Footer still rendered so the Agent still sees the calendar-scope reminder
    assert "macro calendar covers current week only" in result


async def test_critical_alerts_mixed_unavailable_and_empty():
    """One section unavailable, the other simply empty — distinct rendering."""
    from src.agent.tools_perception import get_critical_alerts

    news_svc = AsyncMock()
    news_svc.get_announcements.return_value = None
    news_svc.get_macro_events.return_value = []

    deps = _make_deps(news=news_svc)
    result = await get_critical_alerts(deps)

    assert "Exchange announcements service temporarily unavailable" in result
    assert "No upcoming macro events" in result


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
