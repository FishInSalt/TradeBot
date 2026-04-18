"""Tests for the 4 N3 perception tools."""
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from src.integrations.crypto_etf.models import ETFFlowEntry
from src.integrations.macro.models import (
    EquityQuote, FREDObservation, MacroSnapshot,
)
from src.integrations.onchain.models import StablecoinSnapshot, StablecoinTotal


@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    market_data: object = None
    exchange: object = None
    technical: object = None
    memory: object = None
    session_id: str = "test"
    db_engine: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None
    initial_balance: float = 10000.0
    metrics: object = None
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None


def _make_deps(**overrides) -> MockDeps:
    return MockDeps(**overrides)


# ===== get_higher_timeframe_view =====

def _make_ohlcv_df(n_rows: int, last_close: float = 75_234.50) -> pd.DataFrame:
    """Build a synthetic OHLCV dataframe of n_rows.

    Prices ascend linearly so MAs are deterministic; highs add +500 and
    lows subtract -500 for a stable range.

    NOTE: this shape is intentionally extreme — 100-period high always falls
    in the last row, so range position is ~92%. Tests below assert on string
    presence only, not numeric correctness of range position. If a future
    test asserts on the range-position number, replace this helper with a
    fixture that produces a less degenerate shape.
    """
    base = last_close - (n_rows - 1) * 50
    rows = []
    for i in range(n_rows):
        close = base + i * 50
        rows.append({
            "timestamp": 1_776_000_000 + i * 86_400_000,
            "open": close - 10, "high": close + 500, "low": close - 500,
            "close": close, "volume": 1000.0,
        })
    return pd.DataFrame(rows)


async def test_htf_view_format_1d():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "Higher Timeframe View (1d" in result
    assert "BTC/USDT:USDT" in result
    assert "MA50:" in result
    assert "MA100:" in result
    assert "MA200:" in result
    assert "100-period High" in result
    assert "100-period Low" in result
    assert "Current price within range" in result
    assert "20-period High" in result
    assert "20-period Low" in result
    assert "20-period range width" in result
    # Period-unit label: 1d → "days"
    assert "days ago" in result


async def test_htf_view_period_label_for_4h():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="4h")
    assert "4h-bars ago" in result


async def test_htf_view_period_label_for_1w():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="1w")
    assert "weeks ago" in result


async def test_htf_view_period_label_for_1m():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    result = await get_higher_timeframe_view(deps, timeframe="1M")
    assert "months ago" in result


async def test_htf_view_passes_symbol_and_limit_to_market_data():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)

    await get_higher_timeframe_view(deps, timeframe="1d")
    market_data.get_ohlcv_dataframe.assert_awaited_once_with(
        "BTC/USDT:USDT", "1d", limit=250,
    )


async def test_htf_view_has_no_subjective_labels():
    """Spec §3.1: no 'uptrend / strong / upper third' labels — fact-only."""
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(250)
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    lower = result.lower()
    for label in ("uptrend", "downtrend", "strong", "weak",
                  "bullish", "bearish", "upper third", "lower third",
                  "signals", "precedes", "follows"):
        assert label not in lower, f"found subjective label '{label}'"


async def test_htf_view_upstream_failure_degrades():
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.side_effect = RuntimeError("OKX down")
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "temporarily unavailable" in result.lower()


async def test_htf_view_insufficient_data_for_ma200():
    """If fewer than 200 candles are returned, MA200 degrades but others work."""
    from src.agent.tools_perception import get_higher_timeframe_view

    market_data = AsyncMock()
    market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df(150)
    deps = _make_deps(market_data=market_data)
    result = await get_higher_timeframe_view(deps, timeframe="1d")

    assert "MA50:" in result
    assert "MA100:" in result
    # MA200 should appear but flagged as insufficient.
    assert "MA200" in result
    assert "insufficient data" in result.lower()


# ===== get_macro_context =====

def _full_snapshot() -> MacroSnapshot:
    return MacroSnapshot(
        btc_dominance=57.31, eth_dominance=10.79,
        total_mcap_usd=2.69e12, mcap_change_24h_pct=2.58,
        usd_index_broad_tw=FREDObservation("DTWEXBGS", "2026-04-10", 118.86),
        vix=FREDObservation("VIXCLS", "2026-04-16", 17.94),
        treasury_10y=FREDObservation("DGS10", "2026-04-16", 4.32),
        spread_10y_2y=FREDObservation("T10Y2Y", "2026-04-16", 0.06),
        inflation_10y=FREDObservation("T10YIE", "2026-04-16", 2.43),
        spy=EquityQuote("SPY", 710.14, 1.21, "2026-04-17"),
        qqq=EquityQuote("QQQ", 648.85, 1.31, "2026-04-17"),
    )


async def test_macro_no_service():
    from src.agent.tools_perception import get_macro_context
    deps = _make_deps(macro=None)
    result = await get_macro_context(deps)
    assert "not configured" in result.lower()


async def test_macro_full_snapshot_rendering():
    from src.agent.tools_perception import get_macro_context
    macro_svc = AsyncMock()
    macro_svc.get_snapshot.return_value = _full_snapshot()

    deps = _make_deps(macro=macro_svc)
    result = await get_macro_context(deps)

    assert "=== Crypto Market ===" in result
    assert "BTC.D: 57.31%" in result
    assert "ETH.D: 10.79%" in result
    assert "$2.69T" in result
    assert "+2.58%" in result
    assert "=== US Macro (FRED) ===" in result
    assert "USD Index (Broad TW): 118.86" in result
    assert "(as of 2026-04-10)" in result
    assert "VIX: 17.94" in result
    assert "10Y Treasury: 4.32%" in result
    assert "2s10s Spread: +0.06%" in result
    assert "10Y Inflation Expectation: 2.43%" in result
    assert "=== US Equities (Alpha Vantage) ===" in result
    assert "SPY: $710.14" in result
    assert "QQQ: $648.85" in result


async def test_macro_cg_section_unavailable_when_all_cg_fields_none():
    from src.agent.tools_perception import get_macro_context
    snap = _full_snapshot()
    snap_dict = snap.__dict__.copy()
    snap_dict.update(dict(
        btc_dominance=None, eth_dominance=None,
        total_mcap_usd=None, mcap_change_24h_pct=None,
    ))
    new_snap = MacroSnapshot(**snap_dict)

    macro_svc = AsyncMock()
    macro_svc.get_snapshot.return_value = new_snap
    deps = _make_deps(macro=macro_svc)
    result = await get_macro_context(deps)

    assert "=== Crypto Market ===" in result
    assert "temporarily unavailable" in result.lower()
    # But FRED + AV should still render
    assert "VIX: 17.94" in result
    assert "SPY: $710.14" in result


async def test_macro_all_sections_unavailable():
    from src.agent.tools_perception import get_macro_context
    snap = MacroSnapshot(
        btc_dominance=None, eth_dominance=None,
        total_mcap_usd=None, mcap_change_24h_pct=None,
        usd_index_broad_tw=None, vix=None, treasury_10y=None,
        spread_10y_2y=None, inflation_10y=None,
        spy=None, qqq=None,
    )
    macro_svc = AsyncMock()
    macro_svc.get_snapshot.return_value = snap
    deps = _make_deps(macro=macro_svc)
    result = await get_macro_context(deps)

    assert "all sources temporarily unavailable" in result.lower()


async def test_macro_has_no_subjective_labels():
    from src.agent.tools_perception import get_macro_context
    macro_svc = AsyncMock()
    macro_svc.get_snapshot.return_value = _full_snapshot()
    deps = _make_deps(macro=macro_svc)
    result = await get_macro_context(deps)

    lower = result.lower()
    for label in ("bullish", "bearish", "strong dollar", "slightly positive",
                  "risk-on", "risk-off", "moderate"):
        assert label not in lower, f"found subjective label '{label}'"


# ===== get_etf_flows =====

def _flows(days: int) -> list[ETFFlowEntry]:
    base_cum = 57_000_000_000.0
    return [
        ETFFlowEntry(
            date=f"2026-04-{17-i:02d}",
            net_inflow_usd=(i + 1) * 100_000_000.0 * ((-1) ** i),
            cumulative_usd=base_cum + (days - i) * 100_000_000.0,
            aum_usd=1.0e11,
        )
        for i in range(days)
    ]


async def test_etf_no_service():
    from src.agent.tools_perception import get_etf_flows
    deps = _make_deps(crypto_etf=None)
    result = await get_etf_flows(deps)
    assert "not configured" in result.lower()


async def test_etf_btc_and_eth_format():
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        return _flows(days)

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)

    assert "=== BTC Spot ETF Flows (US) ===" in result
    assert "=== ETH Spot ETF Flows (US) ===" in result
    assert "2026-04-17:" in result
    assert "7-day net:" in result
    assert "Note:" in result
    # Footer should include the T+1 caveat (spec §3.3)
    assert "may be revised t+1" in result.lower()


async def test_etf_btc_fails_eth_succeeds():
    """Sub-source independence: one symbol failing does not kill the other."""
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        if symbol == "BTC":
            return None
        return _flows(days)

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)

    assert "BTC Spot ETF" in result
    assert "temporarily unavailable" in result.lower()
    assert "ETH Spot ETF" in result
    assert "2026-04-17" in result


async def test_etf_both_fail():
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()
    svc.get_etf_flows.return_value = None
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)

    assert "temporarily unavailable" in result.lower()


async def test_etf_insufficient_data_renders_distinct_from_outage():
    """Service returns [] (data-gap) vs None (outage). Tool output must
    distinguish so the agent doesn't read a data-gap as a service failure
    (and vice versa). Spec §3.5 three-state contract."""
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        return [] if symbol == "BTC" else _flows(days)

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)
    lower = result.lower()

    # BTC section reports insufficient data, NOT "temporarily unavailable"
    btc_section = result.split("=== ETH")[0]
    assert "insufficient data" in btc_section.lower()
    assert "temporarily unavailable" not in btc_section.lower()
    # ETH section still rendered normally
    assert "7-day net:" in result


async def test_etf_renders_aum_on_first_row():
    """First-row suffix shows both cumulative inflow AND end-of-day AUM.
    `ETFFlowEntry.aum_usd` is set from SoSoValue's total_net_assets; the
    previous rendering stored but never surfaced it."""
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        return _flows(days)

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=3)

    # _flows() uses aum_usd=1.0e11 = $100B
    assert "AUM: $100.00B" in result
    # First-row suffix co-renders cum + AUM, so expect both tokens on the top
    # data line inside the BTC section
    btc_section = result.split("=== ETH")[0]
    first_data_line = [L for L in btc_section.splitlines() if L.startswith("2026-04-")][0]
    assert "cum:" in first_data_line
    assert "AUM:" in first_data_line


async def test_etf_footer_suppressed_on_mixed_outage_and_data_gap():
    """PR#14 review I3: if one side is None (outage) and the other is []
    (data-gap), neither rendered actual flow rows, so the T+1 revision
    caveat refers to nothing. Suppress the footer in that case."""
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        return None if symbol == "BTC" else []

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)
    lower = result.lower()

    # Both sub-sections render (outage + data-gap messages)
    assert "temporarily unavailable" in lower
    assert "insufficient data" in lower
    # But no T+1 footer — there is no today's value to warn about
    assert "may be revised t+1" not in lower
    assert "past 7 trading days" not in lower


async def test_etf_tool_clamps_days_in_footer():
    """Agent passes days=30 → service clamps to 14 → footer must say
    "Past 14 trading days", not "Past 30". Otherwise row count and footer
    contradict each other (14 rows rendered but footer claims 30).
    """
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        # Service clamps to 14; tool must match.
        return _flows(min(days, 14))

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=30)

    assert "Past 14 trading days" in result
    assert "Past 30 trading days" not in result
    assert "14-day net:" in result


async def test_etf_tool_clamps_days_below_min():
    """Agent passes days=0 → clamp to 1; footer reflects clamped value."""
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        return _flows(max(days, 1))

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=0)

    assert "Past 1 trading days" in result


async def test_etf_has_no_subjective_labels():
    from src.agent.tools_perception import get_etf_flows
    svc = AsyncMock()

    async def fake_flows(symbol, days):
        return _flows(days)

    svc.get_etf_flows.side_effect = fake_flows
    deps = _make_deps(crypto_etf=svc)
    result = await get_etf_flows(deps, days=7)

    lower = result.lower()
    for label in ("bullish", "bearish", "dry powder", "capital entering",
                  "institutional buying", "accumulation"):
        assert label not in lower, f"found subjective label '{label}'"


# ===== get_stablecoin_supply =====

async def test_stablecoin_no_service():
    from src.agent.tools_perception import get_stablecoin_supply
    deps = _make_deps(onchain=None)
    result = await get_stablecoin_supply(deps)
    assert "not configured" in result.lower()


async def test_stablecoin_full_format():
    from src.agent.tools_perception import get_stablecoin_supply
    svc = AsyncMock()
    svc.get_stablecoin_snapshot.return_value = {
        "coins": [
            StablecoinSnapshot("USDT", 186.62e9, 2.33e9, 1.27),
            StablecoinSnapshot("USDC", 42.18e9, 0.51e9, 1.22),
        ],
        "total": StablecoinTotal(228.80e9, 2.84e9, 1.26),
    }
    deps = _make_deps(onchain=svc)
    result = await get_stablecoin_supply(deps)

    assert "=== Stablecoin Supply ===" in result
    assert "USDT: $186.62B" in result
    assert "+$2.33B" in result
    assert "+1.27%" in result
    assert "USDC: $42.18B" in result
    assert "Total Stablecoin Mcap" in result


async def test_stablecoin_service_failure():
    from src.agent.tools_perception import get_stablecoin_supply
    svc = AsyncMock()
    svc.get_stablecoin_snapshot.return_value = None
    deps = _make_deps(onchain=svc)
    result = await get_stablecoin_supply(deps)
    assert "temporarily unavailable" in result.lower()


async def test_stablecoin_empty_coins_signals_data_unavailable():
    """Guard against upstream schema drift: if DefiLlama renames tracked
    symbols (USDT → USDT0 etc.), `coins` ends up empty and totals are 0.0.
    The tool must surface "data unavailable" rather than render $0.00."""
    from src.agent.tools_perception import get_stablecoin_supply
    svc = AsyncMock()
    svc.get_stablecoin_snapshot.return_value = {
        "coins": [],
        "total": StablecoinTotal(0.0, 0.0, 0.0),
    }
    deps = _make_deps(onchain=svc)
    result = await get_stablecoin_supply(deps)
    lower = result.lower()
    assert "data unavailable" in lower
    assert "$0.00" not in result


async def test_stablecoin_has_no_subjective_labels():
    from src.agent.tools_perception import get_stablecoin_supply
    svc = AsyncMock()
    svc.get_stablecoin_snapshot.return_value = {
        "coins": [StablecoinSnapshot("USDT", 186.62e9, 2.33e9, 1.27)],
        "total": StablecoinTotal(186.62e9, 2.33e9, 1.27),
    }
    deps = _make_deps(onchain=svc)
    result = await get_stablecoin_supply(deps)
    lower = result.lower()
    for label in ("dry powder", "capital entering", "sidelined",
                  "bullish", "bearish"):
        assert label not in lower, f"found subjective label '{label}'"
