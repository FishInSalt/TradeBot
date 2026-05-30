"""Tests for get_taker_flow: rubik taker-volume fetch + minute-level flow rendering.

Covers spec docs/superpowers/specs/2026-05-30-order-flow-tools-redesign-design.md
§2 (rubik source), §3.1-3.3 (taker_flow design), §3.5 (errors), §4.1 (architecture),
§5 ①②③⑤⑥ (tests).
"""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_taker_flow_bar_dataclass_fields():
    from src.integrations.exchange.base import TakerFlowBar
    b = TakerFlowBar(ts=1778644800000, sell_usd=5_800_000.0, buy_usd=4_200_000.0)
    assert b.ts == 1778644800000
    assert b.sell_usd == pytest.approx(5_800_000.0)
    assert b.buy_usd == pytest.approx(4_200_000.0)


def test_taker_volume_period_map_is_complete():
    """§3.1/§3.3/③: distinct from _OKX_OI_PERIOD; covers tool periods {5m,1h,4h,1d}
    PLUS the 1w anchor up-tier. Reusing _OKX_OI_PERIOD would KeyError on 4h/1w."""
    from src.integrations.exchange.base import _TAKER_VOLUME_PERIOD, _OKX_OI_PERIOD
    assert _TAKER_VOLUME_PERIOD == {"5m": "5m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    assert _TAKER_VOLUME_PERIOD is not _OKX_OI_PERIOD
    for p in ("5m", "1h", "4h", "1d", "1w"):
        assert p in _TAKER_VOLUME_PERIOD


def _sim_with_rubik(data_rows):
    """SimulatedExchange with mocked _ccxt rubik response. `.market` is SYNC
    (ccxt market() is synchronous) -> MagicMock; the rubik endpoint is async."""
    from src.integrations.exchange.simulated import SimulatedExchange
    ex = SimulatedExchange.__new__(SimulatedExchange)
    ex._ccxt = MagicMock()
    ex._ccxt.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._ccxt.public_get_rubik_stat_taker_volume_contract = AsyncMock(
        return_value={"code": "0", "data": data_rows, "msg": ""}
    )
    ex._validate_symbol = lambda s: None  # bypass symbol guard for unit isolation
    return ex


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_parses_and_ascends():
    # Raw OKX rubik is newest-first: [ts, sellVol, buyVol] (col1=sell, col2=buy).
    # Newest row (in-progress current bucket) must survive AND end up LAST after
    # the ascending sort (no drop/shift at fetch layer).
    rows = [
        ["1778644800000", "5800000", "4200000"],  # newest = in-progress
        ["1778644500000", "9000000", "8000000"],
        ["1778644200000", "1000000", "9000000"],  # oldest
    ]
    ex = _sim_with_rubik(rows)
    bars = await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 3)
    assert len(bars) == 3
    assert bars[0].ts == 1778644200000          # oldest first
    assert bars[-1].ts == 1778644800000         # in-progress newest kept, last
    # Column order [ts, sell, buy] (regression guard against direction flip):
    assert bars[-1].sell_usd == pytest.approx(5800000.0)
    assert bars[-1].buy_usd == pytest.approx(4200000.0)


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_passes_unit_period_instid_limit():
    ex = _sim_with_rubik([["1778644800000", "1", "2"]])
    await ex.fetch_taker_flow("BTC/USDT:USDT", "4h", 21)
    ex._ccxt.public_get_rubik_stat_taker_volume_contract.assert_awaited_once_with(
        {"instId": "BTC-USDT-SWAP", "period": "4H", "unit": "2", "limit": "21"}
    )


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_empty():
    ex = _sim_with_rubik([])
    assert await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 6) == []


@pytest.mark.asyncio
async def test_sim_fetch_taker_flow_rate_limit_raises():
    import ccxt.async_support as ccxt
    from src.utils.cache import RateLimitHit
    ex = _sim_with_rubik([])
    ex._ccxt.public_get_rubik_stat_taker_volume_contract = AsyncMock(
        side_effect=ccxt.RateLimitExceeded("429")
    )
    with pytest.raises(RateLimitHit):
        await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 6)


def _okx_with_rubik(data_rows):
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange.__new__(OKXExchange)
    ex._client = MagicMock()
    ex._client.market.return_value = {"id": "BTC-USDT-SWAP"}
    ex._client.public_get_rubik_stat_taker_volume_contract = AsyncMock(
        return_value={"code": "0", "data": data_rows, "msg": ""}
    )
    return ex


@pytest.mark.asyncio
async def test_okx_fetch_taker_flow_parses_and_ascends():
    rows = [
        ["1778644800000", "5800000", "4200000"],  # newest = in-progress
        ["1778644500000", "9000000", "8000000"],  # oldest
    ]
    ex = _okx_with_rubik(rows)
    bars = await ex.fetch_taker_flow("BTC/USDT:USDT", "1h", 2)
    assert [b.ts for b in bars] == [1778644500000, 1778644800000]
    assert bars[-1].sell_usd == pytest.approx(5800000.0)
    assert bars[-1].buy_usd == pytest.approx(4200000.0)
    ex._client.public_get_rubik_stat_taker_volume_contract.assert_awaited_once_with(
        {"instId": "BTC-USDT-SWAP", "period": "1H", "unit": "2", "limit": "2"}
    )


@pytest.mark.asyncio
async def test_okx_fetch_taker_flow_empty():
    ex = _okx_with_rubik([])
    assert await ex.fetch_taker_flow("BTC/USDT:USDT", "5m", 6) == []
