"""Tests for OI history fetch + anchors + delta rendering.

Covers spec sections:
  §2.1 OpenInterestHistoryPoint + _OKX_OI_PERIOD
  §2.2/2.3 OKX + Simulated fetch_open_interest_history
  §2.4 MarketDataService.get_open_interest_history
  §2.5 render helpers + get_derivatives_data wire
  §5.2 19 unit tests + §5.3 simulated integration + §5.4 drift guard
"""
import time
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
    _, result, _ = _derive_oi_anchors(points, now_ms=int(time.time() * 1000))
    assert "1h ago $2.93B, -0.3%" in result
    assert "24h ago $2.91B, +0.3%" in result
    assert "; " in result


def test_oi_render_positive_deltas():
    from src.agent.tools_perception import _derive_oi_anchors
    vals = [2_500_000_000.0] * 26
    vals[-1] = 2_920_000_000.0
    points = _make_points(vals)
    _, result, _ = _derive_oi_anchors(points, now_ms=int(time.time() * 1000))
    assert "24h ago $2.50B, +16.8%" in result


def test_oi_render_zero_delta_when_anchors_equal_current():
    from src.agent.tools_perception import _derive_oi_anchors
    vals = [2_920_000_000.0] * 26
    points = _make_points(vals)
    _, result, _ = _derive_oi_anchors(points, now_ms=int(time.time() * 1000))
    assert "+0.0%" in result


def test_oi_render_exactly_25_records():
    """24h-anchor minimum boundary: len(points)=25, points[-25]=points[0] available."""
    from src.agent.tools_perception import _derive_oi_anchors
    # len = 1 + 22 + 2 = 25; vals[-25]=vals[0]=$2.91B; vals[-2]=$2.93B; vals[-1]=$2.92B (current)
    vals = [2_910_000_000.0] + [2_900_000_000.0] * 22 + [2_930_000_000.0, 2_920_000_000.0]
    assert len(vals) == 25  # tripwire — guard the 24h-anchor index math
    points = _make_points(vals)
    _, result, _ = _derive_oi_anchors(points, now_ms=int(time.time() * 1000))
    assert "1h ago" in result
    assert "24h ago $2.91B" in result


def test_oi_render_exactly_2_records():
    """1h-anchor minimum boundary: only 1h shown, no 24h."""
    from src.agent.tools_perception import _derive_oi_anchors
    points = _make_points([2_930_000_000.0, 2_920_000_000.0])
    _, result, _ = _derive_oi_anchors(points, now_ms=int(time.time() * 1000))
    assert "1h ago $2.93B" in result
    assert "24h ago" not in result


def test_oi_render_1_record():
    """Below 1h anchor boundary: empty string."""
    from src.agent.tools_perception import _derive_oi_anchors
    points = _make_points([2_920_000_000.0])
    _, result, _ = _derive_oi_anchors(points, now_ms=int(time.time() * 1000))
    assert result == ""


def test_oi_render_anchor_zero_skipped():
    """Defensive: anchor with open_interest_value <= 0 must be skipped (div-by-zero)."""
    from src.agent.tools_perception import _derive_oi_anchors
    # len = 1 + 22 + 2 = 25; vals[-25]=vals[0]=0 (24h-ago zero) → skip 24h fragment
    vals = [0.0] + [2_900_000_000.0] * 22 + [2_930_000_000.0, 2_920_000_000.0]
    assert len(vals) == 25 and vals[-25] == 0.0  # tripwire — guard zero placement
    points = _make_points(vals)
    _, result, _ = _derive_oi_anchors(points, now_ms=int(time.time() * 1000))
    assert "1h ago" in result
    assert "24h ago" not in result


# ---------------------------------------------------------------------------
# Task 7: get_derivatives_data wired to OI history — failure path tests
# ---------------------------------------------------------------------------


def _async_mock(value):
    """Build AsyncMock: raise if value is Exception, else return value."""
    if isinstance(value, Exception):
        return AsyncMock(side_effect=value)
    return AsyncMock(return_value=value)


def _mock_deps_for_derivs(oi_hist_value, funding_value=None, lsr_value=None):
    """Build a minimal TradingDeps mock for get_derivatives_data tests.

    Each *_value: either the success payload (e.g., list[OpenInterestHistoryPoint],
    FundingRate, LongShortRatio) OR an Exception instance (raised by the AsyncMock).
    funding_value / lsr_value default to a sane stub if None.
    """
    from src.integrations.exchange.base import FundingRate, LongShortRatio
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()

    if funding_value is None:
        funding_value = FundingRate(
            symbol="BTC/USDT:USDT", rate=0.000014,
            next_funding_time=1778660000000, timestamp=1778645000000,
        )
    if lsr_value is None:
        lsr_value = LongShortRatio(
            symbol="BTC/USDT:USDT", long_short_ratio=0.66,
            long_ratio=0.399, short_ratio=0.601, timestamp=1778645000000,
        )
    deps.market_data.get_funding_rate = _async_mock(funding_value)
    deps.market_data.get_open_interest_history = _async_mock(oi_hist_value)
    deps.market_data.get_long_short_ratio = _async_mock(lsr_value)
    return deps


@pytest.mark.asyncio
async def test_derivs_oi_history_happy_full_anchors():
    from src.agent.tools_perception import get_derivatives_data
    vals = [2_900_000_000.0] * 26
    vals[-25] = 2_910_000_000.0
    vals[-2] = 2_930_000_000.0
    vals[-1] = 2_920_000_000.0
    deps = _mock_deps_for_derivs(_make_points(vals))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: $2.92B (1h ago $2.93B" in out
    assert "24h ago $2.91B" in out
    assert "Funding Rate:" in out
    assert "Long/Short Ratio:" in out


@pytest.mark.asyncio
async def test_derivs_oi_history_rate_limit():
    from src.agent.tools_perception import get_derivatives_data
    from src.utils.cache import RateLimitHit
    deps = _mock_deps_for_derivs(RateLimitHit("429"))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: (unavailable)" in out
    assert "Funding Rate:" in out  # other fields still rendered
    assert "Long/Short Ratio:" in out


@pytest.mark.asyncio
async def test_derivs_oi_history_empty_list():
    from src.agent.tools_perception import get_derivatives_data
    deps = _mock_deps_for_derivs([])
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: (unavailable)" in out


@pytest.mark.asyncio
async def test_derivs_oi_history_one_record_no_anchor():
    from src.agent.tools_perception import get_derivatives_data
    deps = _mock_deps_for_derivs(_make_points([2_920_000_000.0]))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: $2.92B\n" in out  # single-point form (no anchor paren)
    assert "1h ago" not in out
    assert "24h ago" not in out


@pytest.mark.asyncio
async def test_derivs_oi_history_two_records_1h_only():
    from src.agent.tools_perception import get_derivatives_data
    deps = _mock_deps_for_derivs(_make_points([2_930_000_000.0, 2_920_000_000.0]))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: $2.92B (1h ago $2.93B" in out
    assert "24h ago" not in out


@pytest.mark.asyncio
async def test_derivs_oi_history_anchor_zero_skipped():
    """points[-25].open_interest_value=0 — 24h anchor skipped, 1h preserved."""
    from src.agent.tools_perception import get_derivatives_data
    # len = 25; vals[-25]=vals[0]=0 (24h-ago zero)
    vals = [0.0] + [2_900_000_000.0] * 22 + [2_930_000_000.0, 2_920_000_000.0]
    deps = _mock_deps_for_derivs(_make_points(vals))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "1h ago $2.93B" in out
    assert "24h ago" not in out


@pytest.mark.asyncio
async def test_derivs_all_three_sources_fail_single_error_line():
    """R2-8c L2 全失败 fallback: single Error: line."""
    from src.agent.tools_perception import get_derivatives_data
    from src.utils.cache import RateLimitHit
    deps = _mock_deps_for_derivs(
        oi_hist_value=RateLimitHit("oi"),
        funding_value=RateLimitHit("funding"),
        lsr_value=RateLimitHit("lsr"),
    )
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Error: Temporarily unavailable" in out
    assert "Open Interest:" not in out  # per-field lines suppressed by L2


@pytest.mark.asyncio
async def test_derivs_oi_history_fail_others_ok():
    """OI fails alone → only OI line gets (unavailable); other two intact."""
    from src.agent.tools_perception import get_derivatives_data
    from src.utils.cache import RateLimitHit
    deps = _mock_deps_for_derivs(RateLimitHit("oi only"))
    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    assert "Open Interest: (unavailable)" in out
    assert "Funding Rate:" in out
    assert "longs pay shorts" in out or "shorts pay longs" in out
    assert "Long/Short Ratio:" in out
