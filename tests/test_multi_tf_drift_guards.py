"""Cross-tool drift-guard tests for iter w2r2-next-d (spec §7.1).

Seven invariants:
1. test_indicator_temporal_stability_within_candle
2. test_live_price_field_equals_ticker_last
3. test_three_tools_use_same_ticker_last_in_Last_label
4. test_no_in_progress_candle_in_indicator_inputs
5. test_mts_htf_overlap_values_match (§2.2.1)
6. test_atr_series_last_value_equals_compute_indicators_atr_14 (§6.4.2)
7. test_gmd_htf_last_bar_vol_ratio_match — GMD/HTF "Last bar vol: X
   (Y× SMA(20) avg)" use the same SMA(20) window formula (spec §5.5)

These tests purposely use mocked deps and hand-crafted OHLCV fixtures
so the invariants can be asserted bit-for-bit, without the runtime
variability of live OHLCV fetches.
"""
from __future__ import annotations
import re
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock

from tests.fixtures.multi_tf_ohlcv import (
    df_4h_250bars, df_1d_250bars, df_5m_130bars, df_1h_250bars,
    df_5m_anomaly, df_4h_recent_vol_spike, fake_ticker_81870, _build,
)


def _build_deps(ticker, ohlcv_by_tf, symbol="BTC/USDT:USDT"):
    from src.services.technical import TechnicalAnalysisService
    deps = MagicMock()
    deps.symbol = symbol
    deps.timeframe = "5m"
    deps.technical = TechnicalAnalysisService()
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)

    async def _ohlcv(sym, tf, limit):
        return ohlcv_by_tf[tf]

    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv)
    return deps


def test_indicator_temporal_stability_within_candle(df_4h_250bars):
    """A4: closed-only MA(5) is stable across in-progress mutations;
    full-df MA(5) drifts when the in-progress bar's close changes."""
    from src.utils.ohlcv_utils import _closed_bars
    df_a = df_4h_250bars.copy()
    df_b = df_4h_250bars.copy()
    # Mutate the in-progress bar only
    df_b.loc[df_b.index[-1], "close"] = float(df_b.loc[df_b.index[-1], "close"]) + 1000.0

    closed_a = _closed_bars(df_a)["close"].rolling(5).mean().iloc[-1]
    closed_b = _closed_bars(df_b)["close"].rolling(5).mean().iloc[-1]
    full_a = df_a["close"].rolling(5).mean().iloc[-1]
    full_b = df_b["close"].rolling(5).mean().iloc[-1]

    assert closed_a == pytest.approx(closed_b, rel=0, abs=0), "closed-only MA must be stable"
    assert full_a != full_b, "full-df MA must drift when in-progress close moves"


@pytest.mark.asyncio
async def test_live_price_field_equals_ticker_last(
    fake_ticker_81870, df_4h_250bars, df_1d_250bars,
):
    """Last: header in HTF (and others) derives from ticker.last."""
    from src.agent.tools_perception import get_higher_timeframe_view
    deps = _build_deps(
        fake_ticker_81870, {"4h": df_4h_250bars, "1d": df_1d_250bars},
    )
    out = await get_higher_timeframe_view(deps, timeframes=["4h"])
    assert "Last: 81870.50" in out


@pytest.mark.asyncio
async def test_three_tools_use_same_ticker_last_in_Last_label(
    fake_ticker_81870, df_4h_250bars, df_1d_250bars,
    df_5m_130bars, df_1h_250bars,
):
    """MTS / GMD / HTF all surface ticker.last in their Last: line."""
    from src.agent.tools_perception import (
        get_market_data, get_higher_timeframe_view, get_multi_timeframe_snapshot,
    )
    deps_mts = _build_deps(
        fake_ticker_81870,
        {"5m": df_5m_130bars, "1h": df_1h_250bars,
         "4h": df_4h_250bars, "1d": df_1d_250bars},
    )
    deps_gmd = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
    deps_htf = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})

    out_mts = await get_multi_timeframe_snapshot(deps_mts)
    out_gmd = await get_market_data(deps_gmd)
    out_htf = await get_higher_timeframe_view(deps_htf, timeframes=["4h"])

    # MTS header: "Last (ticker @ T UTC): 81870.50"
    assert re.search(r"Last \(ticker @ \d{2}:\d{2}:\d{2} UTC\): 81870\.50", out_mts)
    # GMD ticker header: "Last: 81870.50 | Bid: ..."
    assert "Last: 81870.50 |" in out_gmd
    # HTF header: "Last: 81870.50"
    assert "Last: 81870.50" in out_htf


@pytest.mark.asyncio
async def test_no_in_progress_candle_in_indicator_inputs(
    fake_ticker_81870, df_5m_130bars,
):
    """A4 enforcement: mutating the in-progress bar must NOT change
    rendered indicator values in GMD (MA20 / MA50 / RSI / etc.)."""
    from src.agent.tools_perception import get_market_data
    df_a = df_5m_130bars.copy()
    df_b = df_5m_130bars.copy()
    df_b.loc[df_b.index[-1], "close"] = 99999.0
    df_b.loc[df_b.index[-1], "volume"] = 99999.0

    deps_a = _build_deps(fake_ticker_81870, {"5m": df_a})
    deps_b = _build_deps(fake_ticker_81870, {"5m": df_b})

    out_a = await get_market_data(deps_a)
    out_b = await get_market_data(deps_b)

    # Extract the Technical Indicators section from both outputs
    def _indicators_block(out: str) -> str:
        return out.split("=== Technical Indicators")[1].split("===")[0]

    assert _indicators_block(out_a) == _indicators_block(out_b), (
        "Indicator block must be identical regardless of in-progress mutation"
    )


@pytest.mark.asyncio
async def test_mts_htf_overlap_values_match(
    fake_ticker_81870, df_4h_250bars, df_1d_250bars,
):
    """§2.2.1: at shared tfs (4h, 1d), MTS and HTF render the same
    MA50 / MA200 / ATR-ratio values, given identical fixture OHLCV.

    End-to-end test: invokes both tools through the same mocked
    MarketDataService, regex-extracts MA50 / MA200 / ATR-ratio from
    each rendered output, and asserts equality. This catches both
    compute drift (one side deviates from the shared SMA / _atr_series
    primitives) and render-side bugs (a wrong attribute surfaces in the
    MA50 slot). MA100 and slope are HTF-only and not part of the
    overlap; MTS does not surface them.
    """
    from src.agent.tools_perception import (
        get_multi_timeframe_snapshot, get_higher_timeframe_view,
    )

    # Single mock layer feeds both tools the SAME fixture OHLCV.
    deps = _build_deps(
        fake_ticker_81870,
        {"4h": df_4h_250bars, "1d": df_1d_250bars,
         # MTS asks for 5m/1h too on default tfs; supply benign fixtures.
         "5m": df_4h_250bars, "1h": df_4h_250bars},
    )

    out_mts = await get_multi_timeframe_snapshot(deps, tfs=["4h", "1d"])
    out_htf = await get_higher_timeframe_view(deps, timeframes=["4h", "1d"])

    def _extract_mts(out: str, tf: str) -> dict[str, float]:
        """Extract MA50, MA200, ATR-ratio from MTS row at given tf."""
        # MTS row: "[4h]  Mom ... | MA50: 79200.00 < MA200: 76200.00 | ATR X.XX% (20p avg Y.YY%, Z.ZZ×) | ..."
        section = re.search(rf"\[{tf}\][^\n]*", out)
        assert section, f"MTS missing [{tf}] row\n{out}"
        row = section.group(0)
        ma50 = float(re.search(r"MA50:\s*(\d+\.\d+)", row).group(1))
        ma200 = float(re.search(r"MA200:\s*(\d+\.\d+)", row).group(1))
        atr_ratio = float(re.search(r"(\d+\.\d+)×", row).group(1))
        return {"ma50": ma50, "ma200": ma200, "atr_ratio": atr_ratio}

    def _extract_htf(out: str, tf: str) -> dict[str, float]:
        """Extract MA50, MA200, ATR-ratio from HTF section at given tf."""
        # HTF section is multiline; capture from "[4h]" to next "[" or end.
        m = re.search(rf"\[{tf}\][^[]*", out, flags=re.DOTALL)
        assert m, f"HTF missing [{tf}] section\n{out}"
        sec = m.group(0)
        ma50 = float(re.search(r"MA50:\s*(\d+\.\d+)", sec).group(1))
        ma200 = float(re.search(r"MA200:\s*(\d+\.\d+)", sec).group(1))
        atr_ratio = float(re.search(r"(\d+\.\d+)× vs 20-period", sec).group(1))
        return {"ma50": ma50, "ma200": ma200, "atr_ratio": atr_ratio}

    for tf in ("4h", "1d"):
        mts_vals = _extract_mts(out_mts, tf)
        htf_vals = _extract_htf(out_htf, tf)
        assert mts_vals == htf_vals, (
            f"§2.2.1 invariant violated at {tf}: MTS={mts_vals} HTF={htf_vals}"
        )


def test_atr_series_last_value_equals_compute_indicators_atr_14(df_4h_250bars):
    """§6.4.2: _atr_series(df_closed, 14).iloc[-1] bit-equals
    compute_indicators(df_closed)['atr_14']. Already enforced in
    tests/test_ohlcv_utils.py; replicated here so the cross-tool
    drift-guard module is self-contained and reads as a complete spec
    §7.1 inventory."""
    from src.utils.ohlcv_utils import _closed_bars, _atr_series
    from src.services.technical import TechnicalAnalysisService

    df_closed = _closed_bars(df_4h_250bars)
    series_last = _atr_series(df_closed, period=14).iloc[-1]
    scalar = TechnicalAnalysisService().compute_indicators(df_closed)["atr_14"]
    assert series_last == pytest.approx(scalar, rel=0, abs=0)


@pytest.mark.asyncio
async def test_gmd_htf_last_bar_vol_ratio_match(
    fake_ticker_81870, df_4h_recent_vol_spike,
):
    """GMD and HTF both surface "Last bar vol: X (Y× SMA(20) avg)"; the
    SMA(20) window formula must be identical across the two tools
    (spec §5.5: `df.iloc[:-1]['volume'].rolling(20).mean().iloc[-1]`,
    equivalent to `df_closed['volume'].iloc[-20:].mean()`).

    End-to-end test: feed the same df_4h_recent_vol_spike fixture into
    both GMD (with timeframe="4h") and HTF (with timeframes=["4h"]),
    regex-extract the Y× SMA(20) avg ratio from each rendered output,
    and assert (1) the two tools render the same ratio at the same
    precision, and (2) that ratio matches the canonical formula.

    Discriminating power: df_4h_recent_vol_spike has a 600 volume at
    the last closed bar (rest = 100). The spec §5.5 window iloc[-20:]
    includes that bar → mean = 125 → ratio = 4.8. A regression to
    iloc[-21:-1] would exclude that bar → mean = 100 → ratio = 6.0.
    The assertion therefore distinguishes the two windows numerically,
    unlike a guard built on uniform-volume fixtures which would yield
    ratio = 1.0 for any window choice (tautology).
    """
    from src.agent.tools_perception import get_market_data, get_higher_timeframe_view
    from src.utils.ohlcv_utils import _closed_bars

    # Both tools see the SAME 4h fixture.
    deps_gmd = _build_deps(fake_ticker_81870, {"4h": df_4h_recent_vol_spike})
    deps_htf = _build_deps(fake_ticker_81870, {"4h": df_4h_recent_vol_spike})

    out_gmd = await get_market_data(deps_gmd, timeframe="4h")
    out_htf = await get_higher_timeframe_view(deps_htf, timeframes=["4h"])

    # GMD: "Last bar vol: X.X (Y.YY× SMA(20) avg)"  (2dp)
    gmd_match = re.search(r"Last bar vol:[^(]*\((\d+\.\d+)× SMA\(20\) avg\)", out_gmd)
    assert gmd_match, f"GMD missing Last bar vol line\n{out_gmd}"
    gmd_ratio = float(gmd_match.group(1))

    # HTF: "Last bar vol (base): X.X (Y.Y× SMA(20) avg)"  (1dp)
    htf_match = re.search(r"Last bar vol \(base\):[^(]*\((\d+\.\d+)× SMA\(20\) avg\)", out_htf)
    assert htf_match, f"HTF missing Last bar vol line\n{out_htf}"
    htf_ratio = float(htf_match.group(1))

    # Canonical formula on the same fixture (spec §5.5).
    df_closed = _closed_bars(df_4h_recent_vol_spike)
    expected = float(df_closed["volume"].iloc[-1]) / float(
        df_closed["volume"].iloc[-20:].mean()
    )
    # Sanity check the fixture: the spike must put the canonical ratio
    # at ≈ 4.8, distinguishable from the regression target (≈ 6.0).
    assert 4.5 < expected < 5.0, (
        f"Fixture lost its spike — expected canonical ratio ≈ 4.8, got {expected:.4f}. "
        f"Drift-guard would degrade to tautology."
    )

    # Cross-tool: GMD's 2dp render, rounded to HTF's 1dp, must equal HTF's 1dp render.
    assert round(gmd_ratio, 1) == htf_ratio, (
        f"§5.5 algorithm drift: GMD ratio {gmd_ratio:.2f} (→1dp {round(gmd_ratio, 1)}) "
        f"≠ HTF ratio {htf_ratio:.1f}"
    )
    # Both must equal the canonical formula at the rendered precision.
    assert htf_ratio == pytest.approx(round(expected, 1), abs=0.05), (
        f"§5.5 algorithm regression: HTF ratio {htf_ratio:.1f} ≠ canonical "
        f"{expected:.4f} (rendered at 1dp). Likely cause: window choice changed "
        f"from iloc[-20:] to iloc[-21:-1] or similar — see spec §5.5."
    )
