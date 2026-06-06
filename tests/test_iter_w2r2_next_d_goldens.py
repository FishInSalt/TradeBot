"""Golden mockup tests for iter w2r2-next-d (HTF / GMD / MTS).

Each test feeds a deterministic OHLCV fixture into the tool and
asserts substring presence (not full-line snapshot equality) for the
key output sections. This gives drift detection without making tests
brittle to whitespace / column-width changes.
"""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.fixtures.multi_tf_ohlcv import (
    df_4h_250bars, df_1d_250bars, df_5m_130bars, df_1h_250bars,
    df_5m_anomaly, fake_ticker_81870,
)


def _build_deps(ticker, ohlcv_by_tf, symbol="BTC/USDT:USDT"):
    """Construct a minimal TradingDeps double with market_data / technical."""
    from src.services.technical import TechnicalAnalysisService
    deps = MagicMock()
    deps.symbol = symbol
    deps.timeframe = "5m"
    deps.technical = TechnicalAnalysisService()
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)

    async def _ohlcv(sym, tf, limit):
        if tf not in ohlcv_by_tf:
            raise RuntimeError(f"no fixture for {tf}")
        return ohlcv_by_tf[tf]

    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv)
    return deps


class TestHTFGolden:
    @pytest.mark.asyncio
    async def test_htf_list_form_default_two_tfs_header(
        self, fake_ticker_81870, df_4h_250bars, df_1d_250bars,
    ):
        from src.agent.tools_perception import get_higher_timeframe_view
        deps = _build_deps(
            fake_ticker_81870,
            {"4h": df_4h_250bars, "1d": df_1d_250bars},
        )
        out = await get_higher_timeframe_view(deps, timeframes=["4h", "1d"])
        assert "=== Higher Timeframe View (BTC/USDT:USDT @" in out
        assert "UTC) ===" in out
        assert "Last: 81870.50" in out

    @pytest.mark.asyncio
    async def test_htf_per_tf_section_header_marks_last_closed_candle(
        self, fake_ticker_81870, df_4h_250bars,
    ):
        from src.agent.tools_perception import get_higher_timeframe_view
        deps = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})
        out = await get_higher_timeframe_view(deps, timeframes=["4h"])
        assert "[4h] (last closed candle: open" in out
        assert " UTC)" in out

    @pytest.mark.asyncio
    async def test_htf_ma_lines_include_slope_and_price_vs_ma(
        self, fake_ticker_81870, df_4h_250bars,
    ):
        from src.agent.tools_perception import get_higher_timeframe_view
        deps = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})
        out = await get_higher_timeframe_view(deps, timeframes=["4h"])
        assert "MA50:" in out
        assert "MA100:" in out
        assert "MA200:" in out
        assert "price vs MA:" in out
        assert "MA slope vs 10 bars ago:" in out

    @pytest.mark.asyncio
    async def test_htf_ma_stack_line(self, fake_ticker_81870, df_4h_250bars):
        from src.agent.tools_perception import get_higher_timeframe_view
        deps = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})
        out = await get_higher_timeframe_view(deps, timeframes=["4h"])
        assert "MA stack:" in out

    @pytest.mark.asyncio
    async def test_htf_100_period_range_includes_bars_ago_and_full_date(
        self, fake_ticker_81870, df_4h_250bars,
    ):
        from src.agent.tools_perception import get_higher_timeframe_view
        deps = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})
        out = await get_higher_timeframe_view(deps, timeframes=["4h"])
        assert "100-period High:" in out
        assert "100-period Low:" in out
        assert "bars ago, candle open" in out
        assert "Range pos (within 100-period):" in out
        assert "0%=Low" in out and "100%=High" in out

    @pytest.mark.asyncio
    async def test_htf_volume_regime_line(
        self, fake_ticker_81870, df_4h_250bars,
    ):
        from src.agent.tools_perception import get_higher_timeframe_view
        deps = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})
        out = await get_higher_timeframe_view(deps, timeframes=["4h"])
        assert "Last bar vol (base):" in out
        assert "SMA(20) avg)" in out

    @pytest.mark.asyncio
    async def test_htf_atr_regime_line(
        self, fake_ticker_81870, df_4h_250bars,
    ):
        from src.agent.tools_perception import get_higher_timeframe_view
        deps = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})
        out = await get_higher_timeframe_view(deps, timeframes=["4h"])
        assert "ATR(14):" in out
        assert "% of price;" in out
        assert "× vs 20-period ATR(14) avg)" in out

    @pytest.mark.asyncio
    async def test_htf_no_thousand_separator(
        self, fake_ticker_81870, df_4h_250bars,
    ):
        from src.agent.tools_perception import get_higher_timeframe_view
        deps = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})
        out = await get_higher_timeframe_view(deps, timeframes=["4h"])
        # No "X,YYY" thousand-separator commas anywhere in numeric output:
        import re
        assert not re.search(r"\d,\d{3}", out), out

    @pytest.mark.asyncio
    async def test_htf_no_current_price_label(
        self, fake_ticker_81870, df_4h_250bars,
    ):
        from src.agent.tools_perception import get_higher_timeframe_view
        deps = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})
        out = await get_higher_timeframe_view(deps, timeframes=["4h"])
        assert "Current Price:" not in out
        assert "Current price:" not in out

    @pytest.mark.asyncio
    async def test_htf_per_tf_degradation_only_failing_tf_marked(
        self, fake_ticker_81870, df_4h_250bars,
    ):
        from src.agent.tools_perception import get_higher_timeframe_view
        # 4h ok; 1d fetch raises
        from unittest.mock import AsyncMock, MagicMock
        deps = MagicMock()
        deps.symbol = "BTC/USDT:USDT"
        deps.timeframe = "5m"
        from src.services.technical import TechnicalAnalysisService
        deps.technical = TechnicalAnalysisService()
        deps.market_data = MagicMock()
        deps.market_data.get_ticker = AsyncMock(return_value=fake_ticker_81870)

        async def _ohlcv(sym, tf, limit):
            if tf == "4h":
                return df_4h_250bars
            raise RuntimeError("1d fetch failed")

        deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv)
        out = await get_higher_timeframe_view(deps, timeframes=["4h", "1d"])
        assert "[4h]" in out and "MA50:" in out  # 4h section is intact
        assert "[1d]" in out  # 1d section header present
        assert "Temporarily unavailable" in out or "temporarily unavailable" in out

    @pytest.mark.asyncio
    async def test_htf_1M_period_choice_marked_in_header(self, fake_ticker_81870):
        """G5: 1M uses (12, 24, 60) periods; section header marks it explicitly."""
        from src.agent.tools_perception import get_higher_timeframe_view
        from tests.fixtures.multi_tf_ohlcv import _build
        df_1m_month = _build(start_ms=1_500_000_000_000, tf="1M",
                             closes=[40000.0 + i * 500.0 for i in range(80)])
        deps = _build_deps(fake_ticker_81870, {"1M": df_1m_month})
        out = await get_higher_timeframe_view(deps, timeframes=["1M"])
        assert "MA12:" in out
        assert "MA24:" in out
        assert "MA60:" in out
        assert "1y/2y/5y monthly" in out


class TestGMDGolden:
    @pytest.mark.asyncio
    async def test_gmd_default_candle_count_is_30(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """B1: GMD default candle_count is 30 (was 50)."""
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "last 30" in out

    @pytest.mark.asyncio
    async def test_gmd_ticker_header_uses_last_and_timestamp(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """N13: Last: replaces Price:; header gets @ T UTC stamp."""
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Ticker (BTC/USDT:USDT @" in out and "UTC) ===" in out
        assert "Last: 81870.50" in out
        assert "Bid: 81870.40" in out
        assert "Ask: 81870.60" in out
        assert "Price:" not in out
        assert "24h base vol:" in out  # was Volume:

    @pytest.mark.asyncio
    async def test_gmd_ohlcv_table_has_markers_column(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """B3: Markers column added; closed-only display."""
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "Markers" in out
        assert "oldest-first by row" in out

    @pytest.mark.asyncio
    async def test_gmd_anomaly_markers_fire_on_high_vol_and_range(
        self, fake_ticker_81870, df_5m_anomaly,
    ):
        """B3: vol↑ marker fires when bar volume > 2× SMA(20); range↑ when
        (high-low) > 2× ATR(14)."""
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_anomaly})
        out = await get_market_data(deps)
        assert "vol↑" in out
        assert "range↑" in out

    @pytest.mark.asyncio
    async def test_gmd_period_summary_section(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """B4: Period summary section after OHLCV table; 2 fields (Avg vol +
        Net Δclose) post iter-tool-opt-gmd-polish issue 4 deletion of
        Avg range (~3% adoption)."""
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Period summary (last 5 closed candles vs prior 5 closed candles) ===" in out
        assert "Avg vol:" in out
        assert "Net Δclose:" in out
        # Avg range deleted per iter-tool-opt-gmd-polish issue 4 (~3% adoption)
        assert "Avg range" not in out

    @pytest.mark.asyncio
    async def test_gmd_closed_only_indicator_inputs(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """A4: Indicator inputs must be _closed_bars(df), not full df.

        df_5m_130bars uses a cyclic close (i % 10) that divides MA(20), so
        closed-only and full-df rolling means coincide on the unmodified
        fixture. To make the test discriminating, mutate the in-progress
        candle's close to an extreme value before invoking — that shifts
        the full-df rolling mean measurably while leaving the closed-only
        rolling mean unchanged. The rendered MA(20) must match the
        closed-only value.
        """
        from src.agent.tools_perception import get_market_data
        from src.utils.ohlcv_utils import _closed_bars
        df_mut = df_5m_130bars.copy()
        df_mut.loc[df_mut.index[-1], "close"] = 99999.0
        deps = _build_deps(fake_ticker_81870, {"5m": df_mut})
        out = await get_market_data(deps)
        df_closed = _closed_bars(df_mut)
        expected_closed = float(df_closed["close"].rolling(20).mean().iloc[-1])
        expected_full = float(df_mut["close"].rolling(20).mean().iloc[-1])
        assert expected_closed != expected_full, (
            "fixture mutation failed to discriminate closed-only vs full-df"
        )
        assert f"MA(20): {expected_closed:.2f}" in out, out
        assert f"MA(20): {expected_full:.2f}" not in out, out

    @pytest.mark.asyncio
    async def test_gmd_technical_indicators_header_has_closed_anchor(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题3: Technical Indicators 表头报最近收盘 bar 时点（5m → HH:MM）。"""
        import re
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert re.search(
            r"=== Technical Indicators \(5m, values as of last closed \d{2}:\d{2}\) ===",
            out,
        ), f"Technical Indicators header missing closed-bar anchor; out={out[:400]}"


class TestMTSGolden:
    @pytest.mark.asyncio
    async def test_mts_header_uses_last_with_ticker_timestamp(
        self, fake_ticker_81870, df_5m_130bars, df_1h_250bars,
        df_4h_250bars, df_1d_250bars,
    ):
        from src.agent.tools_perception import get_multi_timeframe_snapshot
        deps = _build_deps(
            fake_ticker_81870,
            {"5m": df_5m_130bars, "1h": df_1h_250bars,
             "4h": df_4h_250bars, "1d": df_1d_250bars},
        )
        out = await get_multi_timeframe_snapshot(deps)
        assert "=== Multi-TF Snapshot (BTC/USDT:USDT) ===" in out
        assert "Last (ticker @" in out and "UTC):" in out
        assert "81870.50" in out
        # Old "Current price:" label is gone
        assert "Current price:" not in out

    @pytest.mark.asyncio
    async def test_mts_ma_fast_vs_slow_direction_summary(
        self, fake_ticker_81870, df_5m_130bars, df_1h_250bars,
        df_4h_250bars, df_1d_250bars,
    ):
        from src.agent.tools_perception import get_multi_timeframe_snapshot
        deps = _build_deps(
            fake_ticker_81870,
            {"5m": df_5m_130bars, "1h": df_1h_250bars,
             "4h": df_4h_250bars, "1d": df_1d_250bars},
        )
        out = await get_multi_timeframe_snapshot(deps)
        assert "MA fast-vs-slow per tf:" in out

    @pytest.mark.asyncio
    async def test_mts_per_tf_row_columns(
        self, fake_ticker_81870, df_5m_130bars, df_1h_250bars,
        df_4h_250bars, df_1d_250bars,
    ):
        from src.agent.tools_perception import get_multi_timeframe_snapshot
        deps = _build_deps(
            fake_ticker_81870,
            {"5m": df_5m_130bars, "1h": df_1h_250bars,
             "4h": df_4h_250bars, "1d": df_1d_250bars},
        )
        out = await get_multi_timeframe_snapshot(deps)
        # Mom column with primary-MA reference label
        assert "(vs MA" in out
        # Structure column with raw MA values + operator
        assert "MA20:" in out or "MA50:" in out
        # ATR column with ratio
        assert "ATR " in out and "20p avg" in out and "×)" in out
        # Range pos
        assert "Range pos " in out

    @pytest.mark.asyncio
    async def test_mts_last_3_closes_line(
        self, fake_ticker_81870, df_5m_130bars, df_1h_250bars,
        df_4h_250bars, df_1d_250bars,
    ):
        from src.agent.tools_perception import get_multi_timeframe_snapshot
        deps = _build_deps(
            fake_ticker_81870,
            {"5m": df_5m_130bars, "1h": df_1h_250bars,
             "4h": df_4h_250bars, "1d": df_1d_250bars},
        )
        out = await get_multi_timeframe_snapshot(deps)
        # Each tf row has a "Last 3 closes (closed @ T UTC): a→b→c" line
        assert "Last 3 closes (closed @" in out
        assert "→" in out

    @pytest.mark.asyncio
    async def test_mts_columns_header_present(
        self, fake_ticker_81870, df_5m_130bars, df_1h_250bars,
        df_4h_250bars, df_1d_250bars,
    ):
        from src.agent.tools_perception import get_multi_timeframe_snapshot
        deps = _build_deps(
            fake_ticker_81870,
            {"5m": df_5m_130bars, "1h": df_1h_250bars,
             "4h": df_4h_250bars, "1d": df_1d_250bars},
        )
        out = await get_multi_timeframe_snapshot(deps)
        assert "Columns: Momentum" in out and "Structure" in out
        assert "Range pos" in out

    @pytest.mark.asyncio
    async def test_mts_range_pos_no_clamping_when_breakout(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """§3.2 Range pos out-of-bounds: rendered as fact without clamping."""
        from src.agent.tools_perception import get_multi_timeframe_snapshot
        from types import SimpleNamespace
        # Ticker price above the closed-bar 20-bar high → Range pos > 100%
        hi_ticker = SimpleNamespace(
            last=99999.0, bid=99998.0, ask=99999.5,
            high=99999.0, low=80000.0, base_volume=10.0,
        )
        deps = _build_deps(hi_ticker, {"5m": df_5m_130bars})
        out = await get_multi_timeframe_snapshot(deps, tfs=["5m"])
        import re
        # Range pos value > 100% in some form (e.g. "Range pos 14523%" or similar)
        m = re.search(r"Range pos (\-?\d+)%", out)
        assert m is not None
        assert int(m.group(1)) > 100, out


class TestPivotsLabel:
    @pytest.mark.asyncio
    async def test_pivots_header_uses_last_no_thousand_separator(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """N13 expansion: get_price_pivots header label `Last: V`
        (no thousand-separator). Section dividers `Levels Above/Below
        Current Price` are retained as prose."""
        from src.agent.tools_perception import get_price_pivots
        from unittest.mock import AsyncMock, MagicMock
        deps = MagicMock()
        deps.symbol = "BTC/USDT:USDT"
        deps.timeframe = "5m"
        deps.market_data = MagicMock()
        deps.market_data.get_ticker = AsyncMock(return_value=fake_ticker_81870)

        # Distinguish per-tf returns: feed the 5m main fixture only on 5m;
        # return an empty DataFrame for daily/weekly/monthly aux fetches so
        # _get_prior_period_hl degrades gracefully without conflating 5m
        # data into longer-tf computations. The test asserts only the
        # header line, but a tf-aware mock keeps the rest of the output
        # mathematically sensible too.
        import pandas as pd

        async def _ohlcv(sym, tf, limit):
            if tf == "5m":
                return df_5m_130bars
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv)
        out = await get_price_pivots(deps)
        assert "Last: 81870.50" in out
        assert "Current Price:" not in out
        # Section dividers retained
        assert "=== Levels Above Current Price ===" in out
        assert "=== Levels Below Current Price ===" in out
        # No thousand separator on the header price
        import re
        assert not re.search(r"Last: \d{1,3},\d{3}", out)


class TestPositionLiquidationDedup:
    @pytest.mark.asyncio
    async def test_position_section_no_liquidation_line(self):
        """F-P2: Liquidation: line removed from Position section; the
        richer Risk Exposure section's Liquidation: line is kept."""
        from src.agent.tools_perception import get_position
        from unittest.mock import AsyncMock, MagicMock
        from types import SimpleNamespace

        p = SimpleNamespace(
            side="long", contracts=0.01, entry_price=80000.0,
            leverage=10, liquidation_price=72000.0,
            unrealized_pnl=18.7, created_at=None,
        )
        deps = MagicMock()
        deps.symbol = "BTC/USDT:USDT"
        deps.timeframe = "5m"
        deps.initial_balance = 10000.0
        deps.fee_rate = 0.0005
        deps.exchange = MagicMock()
        deps.exchange.fetch_positions = AsyncMock(return_value=[p])
        deps.exchange.fetch_balance = AsyncMock(
            return_value=SimpleNamespace(total_usdt=10000.0, used_usdt=80.0)
        )
        deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
        deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
        deps.exchange.get_mark_price = AsyncMock(return_value=81870.50)
        deps.market_data = MagicMock()
        deps.market_data.get_ticker = AsyncMock(
            return_value=SimpleNamespace(last=81870.50, bid=81870.4, ask=81870.6,
                                         high=82000, low=80000, base_volume=10.0)
        )
        deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=None)

        out = await get_position(deps)
        position_section = out.split("=== PnL ===")[0]
        # Position section MUST NOT contain Liquidation:
        assert "Liquidation:" not in position_section, position_section
        # Risk Exposure section MUST still have it (full form):
        assert "Liquidation: 72000" in out
        assert "ATR(1h)" in out or "% away" in out
