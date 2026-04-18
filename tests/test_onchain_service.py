"""Tests for OnchainService — stablecoin aggregation."""
from unittest.mock import AsyncMock

import pytest


def _make_service():
    from src.integrations.onchain.service import OnchainService
    svc = OnchainService(http=AsyncMock())
    svc._client = AsyncMock()
    return svc


def _asset(symbol: str, circulating: float, prev_week: float):
    return {
        "symbol": symbol,
        "circulating": {"peggedUSD": circulating},
        "circulatingPrevWeek": {"peggedUSD": prev_week},
    }


async def test_snapshot_usdt_and_usdc():
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 186.62e9, 184.29e9),
        _asset("USDC", 42.18e9, 41.67e9),
        _asset("DAI", 5.3e9, 5.25e9),  # ignored
    ]
    result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert "USDT" in by_sym
    assert "USDC" in by_sym
    assert by_sym["USDT"].circulating_usd == pytest.approx(186.62e9)
    assert by_sym["USDT"].change_7d_usd == pytest.approx(2.33e9, abs=1e7)
    assert by_sym["USDT"].change_7d_pct == pytest.approx(1.2644, abs=0.01)


async def test_total_sums_usdt_usdc_only():
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
        _asset("USDC", 50e9, 49e9),
        _asset("DAI", 5e9, 5e9),  # excluded from total
    ]
    result = await svc.get_stablecoin_snapshot()
    total = result["total"]
    assert total.total_circulating_usd == pytest.approx(150e9)
    assert total.total_change_7d_usd == pytest.approx(3e9)
    assert total.total_change_7d_pct == pytest.approx(3e9 / 147e9 * 100, abs=0.05)


async def test_missing_symbol_skipped():
    """If DefiLlama omits USDC, we still return USDT."""
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
    ]
    result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert "USDT" in by_sym
    assert "USDC" not in by_sym


async def test_fetch_failure_returns_none():
    svc = _make_service()
    svc._client.fetch_stablecoins.side_effect = RuntimeError("down")
    result = await svc.get_stablecoin_snapshot()
    assert result is None


async def test_cache_hit_skips_upstream():
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
    ]
    await svc.get_stablecoin_snapshot()
    await svc.get_stablecoin_snapshot()
    svc._client.fetch_stablecoins.assert_awaited_once()


async def test_close_closes_http_when_owned():
    from src.integrations.onchain.service import OnchainService
    svc = OnchainService()  # http=None → owned
    svc._http = AsyncMock()
    svc._owns_http = True
    await svc.close()
    svc._http.aclose.assert_awaited_once()
