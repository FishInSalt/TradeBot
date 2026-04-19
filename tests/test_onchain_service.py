"""Tests for OnchainService — stablecoin aggregation."""
import logging
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


async def test_multi_row_same_symbol_first_occurrence_wins_with_warning(caplog):
    """Multiple rows for the same symbol: first occurrence kept, drift WARN logged."""
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
        _asset("USDT", 50e9, 48e9),  # second-occurrence duplicate
    ]
    caplog.clear()
    with caplog.at_level(logging.WARNING,
                        logger="src.integrations.onchain.service"):
        result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert by_sym["USDT"].circulating_usd == pytest.approx(100e9), (
        "first occurrence must win (not overwritten by second)"
    )
    drift_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "schema drift" in r.getMessage().lower()
    ]
    assert len(drift_warnings) == 1, (
        f"expected exactly 1 schema-drift warning, got {len(drift_warnings)}"
    )
    assert "USDT" in drift_warnings[0].getMessage()


async def test_symbol_normalization_whitespace_and_case():
    """Symbol lookup is tolerant of 'USDT ' / ' usdt' / 'Usdt' variants."""
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset(" usdt", 100e9, 98e9),  # lowercase + leading whitespace
        _asset("USDC ", 50e9, 49e9),   # trailing whitespace
    ]
    result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert "USDT" in by_sym
    assert "USDC" in by_sym
    assert by_sym["USDT"].circulating_usd == pytest.approx(100e9)
    assert by_sym["USDC"].circulating_usd == pytest.approx(50e9)


async def test_unknown_symbol_does_not_trigger_drift_warning(caplog):
    """Untracked symbols (e.g., DAI) silently skip — no drift WARN noise."""
    svc = _make_service()
    svc._client.fetch_stablecoins.return_value = [
        _asset("USDT", 100e9, 98e9),
        _asset("DAI", 5e9, 5e9),
        _asset("DAI", 3e9, 3e9),  # duplicate of an untracked symbol
    ]
    caplog.clear()
    with caplog.at_level(logging.WARNING,
                        logger="src.integrations.onchain.service"):
        result = await svc.get_stablecoin_snapshot()
    by_sym = {s.symbol: s for s in result["coins"]}
    assert "USDT" in by_sym
    assert "DAI" not in by_sym
    drift_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "schema drift" in r.getMessage().lower()
    ]
    assert len(drift_warnings) == 0, (
        f"expected zero drift warnings (DAI is untracked), got {len(drift_warnings)}"
    )
