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
