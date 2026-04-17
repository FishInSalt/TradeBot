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


# --- RateLimitExceeded → RateLimitHit architectural regression tests ---
#
# ccxt.RateLimitExceeded is a subclass of ccxt.NetworkError. The @_retry()
# decorator on OKXExchange methods catches NetworkError and retries up to 3
# times. If the inner try/except ever stops converting RateLimitExceeded →
# RateLimitHit, 429s would be silently retried instead of propagating to
# TTLCache for stale-cache fallback — breaking spec §3.5 contract.
# These tests pin the invariant.


async def test_okx_funding_rate_converts_rate_limit_exceeded():
    import ccxt.async_support as ccxt
    from src.integrations.exchange.okx import OKXExchange
    from src.utils.cache import RateLimitHit

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = AsyncMock()
    exchange._client.fetch_funding_rate.side_effect = ccxt.RateLimitExceeded("429")
    with pytest.raises(RateLimitHit):
        await exchange.fetch_funding_rate("BTC/USDT:USDT")
    # _retry() must NOT have retried — one call, then surfaced RateLimitHit.
    assert exchange._client.fetch_funding_rate.call_count == 1


async def test_okx_open_interest_converts_rate_limit_exceeded():
    import ccxt.async_support as ccxt
    from src.integrations.exchange.okx import OKXExchange
    from src.utils.cache import RateLimitHit

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = AsyncMock()
    exchange._client.fetch_open_interest.side_effect = ccxt.RateLimitExceeded("429")
    with pytest.raises(RateLimitHit):
        await exchange.fetch_open_interest("BTC/USDT:USDT")
    assert exchange._client.fetch_open_interest.call_count == 1


async def test_okx_long_short_ratio_converts_rate_limit_exceeded():
    import ccxt.async_support as ccxt
    from src.integrations.exchange.okx import OKXExchange
    from src.utils.cache import RateLimitHit

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = AsyncMock()
    exchange._client.fetch_long_short_ratio_history.side_effect = ccxt.RateLimitExceeded("429")
    with pytest.raises(RateLimitHit):
        await exchange.fetch_long_short_ratio("BTC/USDT:USDT")
    assert exchange._client.fetch_long_short_ratio_history.call_count == 1


async def test_okx_long_short_ratio_converts_not_supported():
    """If a future ccxt upgrade withdraws capability, surface a precise
    NotImplementedError rather than leaking ccxt.NotSupported (which the
    tool layer would flatten into a generic "temporarily unavailable")."""
    import ccxt.async_support as ccxt
    from src.integrations.exchange.okx import OKXExchange

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = AsyncMock()
    exchange._client.fetch_long_short_ratio_history.side_effect = ccxt.NotSupported("gone")
    with pytest.raises(NotImplementedError, match="long/short ratio history"):
        await exchange.fetch_long_short_ratio("BTC/USDT:USDT")
    # _retry() catches NetworkError but NOT NotSupported, so one call only.
    assert exchange._client.fetch_long_short_ratio_history.call_count == 1


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


async def test_sim_long_short_ratio_converts_not_supported():
    """Mirrors the OKXExchange regression test — both exchange paths must
    convert ccxt.NotSupported to NotImplementedError so a future ccxt
    upgrade withdrawing the capability surfaces the same precise error
    regardless of simulated vs live path."""
    import ccxt.async_support as ccxt

    ex = _make_sim_exchange()
    ex._ccxt.fetch_long_short_ratio_history.side_effect = ccxt.NotSupported("gone")
    with pytest.raises(NotImplementedError, match="long/short ratio history"):
        await ex.fetch_long_short_ratio("BTC/USDT:USDT")
    assert ex._ccxt.fetch_long_short_ratio_history.call_count == 1


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
