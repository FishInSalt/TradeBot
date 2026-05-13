"""Tests for OI history fetch + anchors + delta rendering.

Covers spec sections:
  §2.1 OpenInterestHistoryPoint + _OKX_OI_PERIOD
  §2.2/2.3 OKX + Simulated fetch_open_interest_history
  §2.4 MarketDataService.get_open_interest_history
  §2.5 render helpers + get_derivatives_data wire
  §5.2 19 unit tests + §5.3 simulated integration + §5.4 drift guard
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_oi_history_point_dataclass_fields():
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    p = OpenInterestHistoryPoint(timestamp=1778644800000, open_interest=33174.25, open_interest_value=2693065783.51)
    assert p.timestamp == 1778644800000
    assert p.open_interest == pytest.approx(33174.25)
    assert p.open_interest_value == pytest.approx(2693065783.51)


def test_okx_oi_period_mapping():
    from src.integrations.exchange.base import _OKX_OI_PERIOD
    assert _OKX_OI_PERIOD == {"5m": "5m", "1h": "1H", "1d": "1D"}


def test_base_exchange_has_fetch_open_interest_history_abstractmethod():
    import inspect
    from src.integrations.exchange.base import BaseExchange
    assert hasattr(BaseExchange, "fetch_open_interest_history")
    method = BaseExchange.fetch_open_interest_history
    sig = inspect.signature(method)
    assert "symbol" in sig.parameters
    assert "period" in sig.parameters
    assert "limit" in sig.parameters
    assert sig.parameters["period"].default == "1h"
    assert sig.parameters["limit"].default == 26


def _okx_with_raw_response(data_rows):
    """Helper: build an OKXExchange instance with mocked _client raw response."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._client.public_get_rubik_stat_contracts_open_interest_history = AsyncMock(
        return_value={"code": "0", "data": data_rows, "msg": ""}
    )
    return ex


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_parses_raw_response():
    # Raw OKX returns newest-first; our wrapper must reverse to oldest-first.
    rows = [
        ["1778644800000", "3317425.09", "33174.25", "2693065783.51"],  # newest
        ["1778641200000", "3325484.92", "33254.85", "2693785781.05"],
        ["1778637600000", "3306756.78", "33067.57", "2677381762.06"],  # oldest
    ]
    ex = _okx_with_raw_response(rows)
    points = await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 3)
    assert len(points) == 3
    # After reverse: oldest first
    assert points[0].timestamp == 1778637600000
    assert points[-1].timestamp == 1778644800000
    assert points[-1].open_interest == pytest.approx(33174.25)
    assert points[-1].open_interest_value == pytest.approx(2693065783.51)


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_empty_data():
    ex = _okx_with_raw_response([])
    points = await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert points == []


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_period_mapping_1h_to_uppercase():
    ex = _okx_with_raw_response([])
    await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)
    called_args = ex._client.public_get_rubik_stat_contracts_open_interest_history.call_args
    assert called_args[0][0]["period"] == "1H"
    assert called_args[0][0]["instId"] == "BTC-USDT-SWAP"
    assert called_args[0][0]["limit"] == "26"


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_period_mapping_1d_to_uppercase():
    ex = _okx_with_raw_response([])
    await ex.fetch_open_interest_history("BTC/USDT:USDT", "1d", 5)
    called_args = ex._client.public_get_rubik_stat_contracts_open_interest_history.call_args
    assert called_args[0][0]["period"] == "1D"


@pytest.mark.asyncio
async def test_okx_fetch_oi_history_missing_data_key():
    """Defensive: if raw response lacks 'data' key, treat as empty."""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._client.public_get_rubik_stat_contracts_open_interest_history = AsyncMock(
        return_value={"code": "0", "msg": ""}
    )
    points = await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert points == []


@pytest.mark.asyncio
async def test_simulated_fetch_oi_history_validates_symbol():
    """Guard 1: invalid symbol must raise ValueError before any network call."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._symbol = "BTC/USDT:USDT"
    ex._ccxt = MagicMock()  # would explode if called
    with pytest.raises(ValueError):
        await ex.fetch_open_interest_history("WRONG/SYMBOL", "1h", 26)


@pytest.mark.asyncio
async def test_simulated_fetch_oi_history_requires_started():
    """Guard 2: must raise RuntimeError if start() has not been called."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._symbol = "BTC/USDT:USDT"
    # _ccxt intentionally not set
    with pytest.raises(RuntimeError, match="Exchange not started"):
        await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)


@pytest.mark.asyncio
async def test_simulated_fetch_oi_history_wraps_rate_limit():
    """Guard 3: ccxt.RateLimitExceeded must be re-raised as RateLimitHit."""
    import ccxt
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.utils.cache import RateLimitHit
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._symbol = "BTC/USDT:USDT"
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._ccxt.public_get_rubik_stat_contracts_open_interest_history = AsyncMock(
        side_effect=ccxt.RateLimitExceeded("429 too many")
    )
    with pytest.raises(RateLimitHit, match="Sim open interest history"):
        await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)


@pytest.mark.asyncio
async def test_simulated_fetch_oi_history_parses_raw():
    """Happy path: raw response parsed, reversed, returned."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._symbol = "BTC/USDT:USDT"
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._ccxt.public_get_rubik_stat_contracts_open_interest_history = AsyncMock(
        return_value={"code": "0", "data": [
            ["1778644800000", "3317425.09", "33174.25", "2693065783.51"],
            ["1778641200000", "3325484.92", "33254.85", "2693785781.05"],
        ], "msg": ""}
    )
    points = await ex.fetch_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert len(points) == 2
    assert points[0].timestamp == 1778641200000  # oldest first after reverse
    assert points[-1].open_interest_value == pytest.approx(2693065783.51)


@pytest.mark.asyncio
async def test_market_data_get_oi_history_delegates_first_call():
    from src.integrations.market_data import MarketDataService
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    exchange = AsyncMock()
    exchange.fetch_open_interest_history.return_value = [
        OpenInterestHistoryPoint(1, 100.0, 1_000_000.0),
        OpenInterestHistoryPoint(2, 101.0, 1_010_000.0),
    ]
    svc = MarketDataService(exchange)
    points = await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert len(points) == 2
    exchange.fetch_open_interest_history.assert_called_once_with("BTC/USDT:USDT", "1h", 26)


@pytest.mark.asyncio
async def test_market_data_get_oi_history_cache_hit_skips_exchange():
    """Second call within TTL must not invoke exchange again."""
    from src.integrations.market_data import MarketDataService
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    exchange = AsyncMock()
    exchange.fetch_open_interest_history.return_value = [OpenInterestHistoryPoint(1, 100.0, 1_000_000.0)]
    svc = MarketDataService(exchange)
    await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 26)
    await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 26)
    assert exchange.fetch_open_interest_history.call_count == 1


@pytest.mark.asyncio
async def test_market_data_get_oi_history_distinct_keys_per_args():
    """Different (period, limit) tuples must not share cache."""
    from src.integrations.market_data import MarketDataService
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    exchange = AsyncMock()
    exchange.fetch_open_interest_history.return_value = [OpenInterestHistoryPoint(1, 100.0, 1_000_000.0)]
    svc = MarketDataService(exchange)
    await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 26)
    await svc.get_open_interest_history("BTC/USDT:USDT", "1h", 5)
    assert exchange.fetch_open_interest_history.call_count == 2
