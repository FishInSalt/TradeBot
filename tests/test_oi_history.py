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


# ---------------------------------------------------------------------------
# Task 6: _format_oi_usd + _derive_oi_anchors render helpers
# ---------------------------------------------------------------------------


def _make_points(values_usd):
    """Helper: build N points with monotonic timestamps and given USD values.
    Returns oldest-first to match exchange.fetch_open_interest_history convention."""
    from src.integrations.exchange.base import OpenInterestHistoryPoint
    return [
        OpenInterestHistoryPoint(timestamp=i, open_interest=v / 80000.0, open_interest_value=v)
        for i, v in enumerate(values_usd)
    ]


def test_format_oi_usd_billion_scale():
    from src.agent.tools_perception import _format_oi_usd
    assert _format_oi_usd(2_920_000_000.0) == "$2.92B"


def test_format_oi_usd_million_scale():
    from src.agent.tools_perception import _format_oi_usd
    assert _format_oi_usd(850_000_000.0) == "$850.00M"


def test_format_oi_usd_below_million():
    from src.agent.tools_perception import _format_oi_usd
    assert _format_oi_usd(123_456.0) == "$123,456"


def test_oi_render_happy_path_inline_26_records():
    """26 records: 1h anchor = points[-2], 24h anchor = points[-25].
    Current $2.92B; 1h-ago $2.93B (-0.34%); 24h-ago $2.91B (+0.34%)."""
    from src.agent.tools_perception import _derive_oi_anchors
    # Build 26 records, oldest first. Index 0..23 don't matter; -25=$2.91B; -2=$2.93B; -1=$2.92B.
    vals = [2_900_000_000.0] * 26
    vals[-25] = 2_910_000_000.0   # 24h ago
    vals[-2] = 2_930_000_000.0    # 1h ago
    vals[-1] = 2_920_000_000.0    # current
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "1h ago $2.93B, -0.3%" in result
    assert "24h ago $2.91B, +0.3%" in result
    assert "; " in result


def test_oi_render_positive_deltas():
    from src.agent.tools_perception import _derive_oi_anchors
    vals = [2_500_000_000.0] * 26
    vals[-1] = 2_920_000_000.0
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "24h ago $2.50B, +16.8%" in result


def test_oi_render_zero_delta_when_anchors_equal_current():
    from src.agent.tools_perception import _derive_oi_anchors
    vals = [2_920_000_000.0] * 26
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "+0.0%" in result


def test_oi_render_exactly_25_records():
    """24h-anchor minimum boundary: len(points)=25, points[-25]=points[0] available."""
    from src.agent.tools_perception import _derive_oi_anchors
    # len = 1 + 22 + 2 = 25; vals[-25]=vals[0]=$2.91B; vals[-2]=$2.93B; vals[-1]=$2.92B (current)
    vals = [2_910_000_000.0] + [2_900_000_000.0] * 22 + [2_930_000_000.0, 2_920_000_000.0]
    assert len(vals) == 25  # tripwire — guard the 24h-anchor index math
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "1h ago" in result
    assert "24h ago $2.91B" in result


def test_oi_render_exactly_2_records():
    """1h-anchor minimum boundary: only 1h shown, no 24h."""
    from src.agent.tools_perception import _derive_oi_anchors
    points = _make_points([2_930_000_000.0, 2_920_000_000.0])
    result = _derive_oi_anchors(points, points[-1])
    assert "1h ago $2.93B" in result
    assert "24h ago" not in result


def test_oi_render_1_record():
    """Below 1h anchor boundary: empty string."""
    from src.agent.tools_perception import _derive_oi_anchors
    points = _make_points([2_920_000_000.0])
    result = _derive_oi_anchors(points, points[-1])
    assert result == ""


def test_oi_render_anchor_zero_skipped():
    """Defensive: anchor with open_interest_value <= 0 must be skipped (div-by-zero)."""
    from src.agent.tools_perception import _derive_oi_anchors
    # len = 1 + 22 + 2 = 25; vals[-25]=vals[0]=0 (24h-ago zero) → skip 24h fragment
    vals = [0.0] + [2_900_000_000.0] * 22 + [2_930_000_000.0, 2_920_000_000.0]
    assert len(vals) == 25 and vals[-25] == 0.0  # tripwire — guard zero placement
    points = _make_points(vals)
    result = _derive_oi_anchors(points, points[-1])
    assert "1h ago" in result
    assert "24h ago" not in result
