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
