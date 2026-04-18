# tests/test_crypto_etf_service.py
"""Tests for CryptoEtfService — cum-delta algorithm (spec §5.3)."""
from unittest.mock import AsyncMock

import pytest

from src.integrations.crypto_etf.models import ETFFlowEntry


def _make_service():
    from src.integrations.crypto_etf.service import CryptoEtfService
    svc = CryptoEtfService(api_key="k", http=AsyncMock())
    svc._client = AsyncMock()
    return svc


def _row(date: str, cum: float, aum: float = 1e11, net_in: float = 0.0):
    return {
        "date": date,
        "cum_net_inflow": cum,
        "total_net_inflow": net_in,
        "total_net_assets": aum,
    }


async def test_cum_delta_simple_case():
    """Two distinct days → one flow entry."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=1100.0, aum=2e11),
        _row("2026-04-16", cum=1000.0, aum=1.9e11),
    ]
    flows = await svc.get_etf_flows("BTC", days=1)
    assert len(flows) == 1
    assert flows[0].date == "2026-04-17"
    assert flows[0].net_inflow_usd == pytest.approx(100.0)
    assert flows[0].cumulative_usd == 1100.0
    assert flows[0].aum_usd == 2e11


async def test_cum_delta_handles_multirow_same_date():
    """Multi-row dates dedup to first row; cum delta uses identical cum values.

    This reproduces real SoSoValue response (spec §2.4 smoke test):
    2026-04-17 has 3 rows, all with cum=57_739_993_739.43.
    2026-04-16 has 1 row with cum=57_076_082_372.97.
    Expected daily flow = 663_911_366.46.
    """
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=57_739_993_739.43,
             aum=101_450_000_000.0, net_in=663_911_366.47),
        _row("2026-04-17", cum=57_739_993_739.43,
             aum=101_450_000_000.0, net_in=996_375_546.47),
        _row("2026-04-17", cum=57_739_993_739.43,
             aum=101_450_000_000.0, net_in=1_617_957_506.54),
        _row("2026-04-16", cum=57_076_082_372.97,
             aum=97_900_000_000.0, net_in=26_051_070.56),
    ]
    flows = await svc.get_etf_flows("BTC", days=1)
    assert len(flows) == 1
    assert flows[0].date == "2026-04-17"
    assert flows[0].net_inflow_usd == pytest.approx(663_911_366.46, abs=1.0)
    assert flows[0].cumulative_usd == 57_739_993_739.43
    assert flows[0].aum_usd == 101_450_000_000.0


async def test_cum_delta_handles_negative_flow():
    """Outflow day: today.cum < yesterday.cum."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-13", cum=900.0),
        _row("2026-04-12", cum=1100.0),
    ]
    flows = await svc.get_etf_flows("BTC", days=1)
    assert flows[0].net_inflow_usd == pytest.approx(-200.0)


async def test_cum_delta_unordered_input_still_sorts_desc():
    """Even if API returns rows in ascending or shuffled order, service sorts."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-15", cum=1000.0),
        _row("2026-04-17", cum=1200.0),
        _row("2026-04-16", cum=1100.0),
    ]
    flows = await svc.get_etf_flows("BTC", days=2)
    assert [f.date for f in flows] == ["2026-04-17", "2026-04-16"]
    assert flows[0].net_inflow_usd == pytest.approx(100.0)
    assert flows[1].net_inflow_usd == pytest.approx(100.0)


async def test_clamp_days_above_max():
    """days > 14 is clamped to 14."""
    svc = _make_service()
    # Provide 20 distinct days to exercise upper clamp.
    rows = [_row(f"2026-04-{d:02d}", cum=1000.0 + d) for d in range(1, 21)]
    svc._client.fetch_summary_history.return_value = rows
    flows = await svc.get_etf_flows("BTC", days=30)
    assert len(flows) == 14


async def test_clamp_days_below_min():
    """days < 1 is clamped to 1."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=1100.0),
        _row("2026-04-16", cum=1000.0),
    ]
    flows = await svc.get_etf_flows("BTC", days=0)
    assert len(flows) == 1


async def test_insufficient_data_returns_empty_list():
    """Need days+1 distinct dates; fewer → empty list (spec §3.5: three-state
    contract — [] signals data-gap, distinct from None which signals outage)."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=1000.0),
    ]
    flows = await svc.get_etf_flows("BTC", days=1)
    assert flows == []


async def test_fetch_failure_returns_none():
    """Source outage → None (spec §3.5)."""
    svc = _make_service()
    svc._client.fetch_summary_history.side_effect = RuntimeError("network down")
    flows = await svc.get_etf_flows("BTC", days=7)
    assert flows is None


async def test_empty_raw_response_treated_as_outage():
    """SoSoValue returning `data: []` is more likely schema drift / silent
    upstream failure than a genuinely empty history window. Return None
    (outage) rather than [] (data-gap) so the tool renders "temporarily
    unavailable" instead of the reassuring "insufficient data" message."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = []
    flows = await svc.get_etf_flows("BTC", days=7)
    assert flows is None


async def test_rate_limit_with_no_stale_returns_none():
    """RateLimitHit without stale cache → None (service outage branch)."""
    from src.utils.cache import RateLimitHit
    svc = _make_service()
    svc._client.fetch_summary_history.side_effect = RateLimitHit("429")
    flows = await svc.get_etf_flows("BTC", days=7)
    assert flows is None


async def test_cache_hit_skips_upstream_call():
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        _row("2026-04-17", cum=1100.0),
        _row("2026-04-16", cum=1000.0),
    ]
    await svc.get_etf_flows("BTC", days=1)
    first_calls = svc._client.fetch_summary_history.call_count
    await svc.get_etf_flows("BTC", days=1)
    assert svc._client.fetch_summary_history.call_count == first_calls


async def test_cache_key_scoped_by_symbol():
    """BTC and ETH must not share a cache slot."""
    svc = _make_service()
    calls: list[str] = []

    async def fake_fetch(sym):
        calls.append(sym)
        return [
            _row("2026-04-17", cum=1100.0),
            _row("2026-04-16", cum=1000.0),
        ]

    svc._client.fetch_summary_history.side_effect = fake_fetch
    await svc.get_etf_flows("BTC", days=1)
    await svc.get_etf_flows("ETH", days=1)
    assert calls == ["BTC", "ETH"]


async def test_malformed_numeric_field_returns_none():
    """A row with None for cum_net_inflow should degrade to None (outage),
    not propagate TypeError uncaught."""
    svc = _make_service()
    svc._client.fetch_summary_history.return_value = [
        {"date": "2026-04-17", "cum_net_inflow": None,
         "total_net_inflow": 0.0, "total_net_assets": 1e11},
        _row("2026-04-16", cum=1000.0),
    ]
    flows = await svc.get_etf_flows("BTC", days=1)
    assert flows is None


async def test_close_closes_http_when_owned():
    from src.integrations.crypto_etf.service import CryptoEtfService
    svc = CryptoEtfService(api_key="k")  # http=None → owned
    svc._http = AsyncMock()
    svc._owns_http = True
    await svc.close()
    svc._http.aclose.assert_awaited_once()
