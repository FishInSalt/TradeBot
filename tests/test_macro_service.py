# tests/test_macro_service.py
"""Tests for MacroService — aggregation, caching, sub-source independence."""
from unittest.mock import AsyncMock

import pytest

from src.integrations.macro.models import (
    EquityQuote, FREDObservation, MacroSnapshot,
)
from src.utils.cache import RateLimitHit


def _make_service():
    """Build MacroService with all clients mocked."""
    from src.integrations.macro.service import MacroService
    svc = MacroService(
        fred_key="fk", av_key="ak", cg_key="ck", http=AsyncMock(),
    )
    svc._cg = AsyncMock()
    svc._fred = AsyncMock()
    svc._av = AsyncMock()
    return svc


async def test_all_sources_succeed():
    svc = _make_service()
    svc._cg.fetch_global.return_value = {
        "btc_dominance": 57.31, "eth_dominance": 10.79,
        "total_mcap_usd": 2.69e12, "mcap_change_24h_pct": 2.58,
    }
    svc._fred.fetch_latest.side_effect = [
        FREDObservation("DTWEXBGS", "2026-04-10", 118.86),
        FREDObservation("VIXCLS", "2026-04-16", 17.94),
        FREDObservation("DGS10", "2026-04-16", 4.32),
        FREDObservation("T10Y2Y", "2026-04-16", 0.06),
        FREDObservation("T10YIE", "2026-04-16", 2.43),
    ]
    svc._av.fetch_quote.side_effect = [
        EquityQuote("SPY", 710.14, 1.21, "2026-04-17"),
        EquityQuote("QQQ", 648.85, 1.31, "2026-04-17"),
    ]
    snap = await svc.get_snapshot()
    assert isinstance(snap, MacroSnapshot)
    # CG
    assert snap.btc_dominance == 57.31
    assert snap.eth_dominance == 10.79
    # FRED — verify the field-to-series-id mapping in get_snapshot() for all 5
    assert snap.usd_index_broad_tw.series_id == "DTWEXBGS"
    assert snap.vix.series_id == "VIXCLS"
    assert snap.vix.value == 17.94
    assert snap.treasury_10y.series_id == "DGS10"
    assert snap.spread_10y_2y.series_id == "T10Y2Y"
    assert snap.inflation_10y.series_id == "T10YIE"
    # AV
    assert snap.spy.symbol == "SPY"
    assert snap.spy.price == 710.14
    assert snap.qqq.symbol == "QQQ"


async def test_cg_failure_does_not_affect_others():
    """CG source fails → cg fields are None; FRED + AV still populated."""
    svc = _make_service()
    svc._cg.fetch_global.side_effect = RuntimeError("network down")
    svc._fred.fetch_latest.return_value = FREDObservation("VIXCLS", "2026-04-16", 17.94)
    svc._av.fetch_quote.return_value = EquityQuote("SPY", 710.14, 1.21, "2026-04-17")
    snap = await svc.get_snapshot()
    assert snap.btc_dominance is None
    assert snap.eth_dominance is None
    assert snap.vix is not None
    assert snap.spy is not None


async def test_fred_partial_failure_per_series():
    """One FRED series failing leaves others intact."""
    svc = _make_service()
    svc._cg.fetch_global.return_value = {
        "btc_dominance": None, "eth_dominance": None,
        "total_mcap_usd": None, "mcap_change_24h_pct": None,
    }

    def fake_fred(series_id):
        if series_id == "VIXCLS":
            raise RuntimeError("VIX server down")
        return FREDObservation(series_id, "2026-04-16", 1.0)

    svc._fred.fetch_latest.side_effect = fake_fred
    svc._av.fetch_quote.return_value = EquityQuote("SPY", 710.14, 1.21, "2026-04-17")

    snap = await svc.get_snapshot()
    assert snap.vix is None
    assert snap.treasury_10y is not None
    assert snap.usd_index_broad_tw is not None


async def test_av_rate_limit_returns_none_for_that_symbol():
    svc = _make_service()
    svc._cg.fetch_global.return_value = {
        "btc_dominance": None, "eth_dominance": None,
        "total_mcap_usd": None, "mcap_change_24h_pct": None,
    }
    svc._fred.fetch_latest.return_value = FREDObservation("VIXCLS", "2026-04-16", 17.94)

    def fake_av(sym):
        if sym == "SPY":
            raise RateLimitHit("25/day exceeded")
        return EquityQuote("QQQ", 648.85, 1.31, "2026-04-17")

    svc._av.fetch_quote.side_effect = fake_av
    snap = await svc.get_snapshot()
    assert snap.spy is None
    assert snap.qqq is not None


async def test_cache_hit_skips_upstream_call():
    svc = _make_service()
    svc._cg.fetch_global.return_value = {
        "btc_dominance": 57.31, "eth_dominance": 10.79,
        "total_mcap_usd": 2.69e12, "mcap_change_24h_pct": 2.58,
    }
    svc._fred.fetch_latest.return_value = FREDObservation("X", "2026-04-16", 1.0)
    svc._av.fetch_quote.return_value = EquityQuote("SPY", 1.0, 0.1, "2026-04-17")

    await svc.get_snapshot()
    cg_calls_first = svc._cg.fetch_global.call_count
    await svc.get_snapshot()
    # Second call within TTL → cache hit, no new upstream call.
    assert svc._cg.fetch_global.call_count == cg_calls_first


async def test_close_closes_http_when_owned():
    from src.integrations.macro.service import MacroService
    svc = MacroService(fred_key="k", av_key="k", cg_key="k")  # http=None → owned
    svc._http = AsyncMock()
    svc._owns_http = True
    await svc.close()
    svc._http.aclose.assert_awaited_once()


async def test_close_does_not_close_injected_http():
    svc = _make_service()
    # _make_service passes http=AsyncMock() → not owned
    svc._http = AsyncMock()
    svc._owns_http = False
    await svc.close()
    svc._http.aclose.assert_not_called()
