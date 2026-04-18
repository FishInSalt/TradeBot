"""Tests for macro data model dataclasses."""


def test_fred_observation_fields():
    from src.integrations.macro.models import FREDObservation
    obs = FREDObservation(series_id="VIXCLS", date="2026-04-16", value=17.94)
    assert obs.series_id == "VIXCLS"
    assert obs.date == "2026-04-16"
    assert obs.value == 17.94


def test_fred_observation_is_frozen():
    import dataclasses
    import pytest
    from src.integrations.macro.models import FREDObservation
    obs = FREDObservation(series_id="VIXCLS", date="2026-04-16", value=17.94)
    with pytest.raises(dataclasses.FrozenInstanceError):
        obs.value = 99.0


def test_equity_quote_is_frozen():
    import dataclasses
    import pytest
    from src.integrations.macro.models import EquityQuote
    q = EquityQuote(symbol="SPY", price=710.14, change_pct=1.21, latest_trading_day="2026-04-17")
    with pytest.raises(dataclasses.FrozenInstanceError):
        q.price = 0.0


def test_macro_snapshot_is_frozen():
    import dataclasses
    import pytest
    from src.integrations.macro.models import MacroSnapshot
    snap = MacroSnapshot(
        btc_dominance=None, eth_dominance=None,
        total_mcap_usd=None, mcap_change_24h_pct=None,
        usd_index_broad_tw=None, vix=None, treasury_10y=None,
        spread_10y_2y=None, inflation_10y=None,
        spy=None, qqq=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.btc_dominance = 1.0


def test_equity_quote_fields():
    from src.integrations.macro.models import EquityQuote
    q = EquityQuote(
        symbol="SPY", price=710.14, change_pct=1.21,
        latest_trading_day="2026-04-17",
    )
    assert q.symbol == "SPY"
    assert q.price == 710.14
    assert q.change_pct == 1.21
    assert q.latest_trading_day == "2026-04-17"


def test_macro_snapshot_all_none_allowed():
    """All sub-source fields must accept None (sub-source independence)."""
    from src.integrations.macro.models import MacroSnapshot
    snap = MacroSnapshot(
        btc_dominance=None, eth_dominance=None,
        total_mcap_usd=None, mcap_change_24h_pct=None,
        usd_index_broad_tw=None, vix=None, treasury_10y=None,
        spread_10y_2y=None, inflation_10y=None,
        spy=None, qqq=None,
    )
    assert snap.btc_dominance is None
    assert snap.spy is None


def test_macro_snapshot_full_values():
    from src.integrations.macro.models import (
        MacroSnapshot, FREDObservation, EquityQuote,
    )
    snap = MacroSnapshot(
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
    assert snap.btc_dominance == 57.31
    assert snap.eth_dominance == 10.79
    assert snap.total_mcap_usd == 2.69e12
    assert snap.mcap_change_24h_pct == 2.58
    assert snap.usd_index_broad_tw == FREDObservation("DTWEXBGS", "2026-04-10", 118.86)
    assert snap.vix == FREDObservation("VIXCLS", "2026-04-16", 17.94)
    assert snap.treasury_10y == FREDObservation("DGS10", "2026-04-16", 4.32)
    assert snap.spread_10y_2y == FREDObservation("T10Y2Y", "2026-04-16", 0.06)
    assert snap.inflation_10y == FREDObservation("T10YIE", "2026-04-16", 2.43)
    assert snap.spy == EquityQuote("SPY", 710.14, 1.21, "2026-04-17")
    assert snap.qqq == EquityQuote("QQQ", 648.85, 1.31, "2026-04-17")
