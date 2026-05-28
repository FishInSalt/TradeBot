"""Tests for iter-tool-opt-gmd-polish — shared helpers (Task 1) +
issue-specific assertions (Tasks 2-6).

Helpers tested here:
- _to_pd_timestamp_utc (Task 1)
- _fmt_candle_time (Task 1)
- TF_OFFSETS dict (Task 1)
"""
from __future__ import annotations
from datetime import datetime, timezone

import pandas as pd
import pytest


# === Task 1: helpers ===

class TestToPdTimestampUtc:
    def test_int_ms_epoch(self):
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        ts = _to_pd_timestamp_utc(1_700_000_000_000)
        assert ts.tz is not None
        assert ts.tz.utcoffset(None).total_seconds() == 0  # UTC
        assert ts.year == 2023 and ts.month == 11

    def test_float_ms_epoch(self):
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        ts = _to_pd_timestamp_utc(1_700_000_000_000.0)
        assert ts.tz is not None

    def test_naive_datetime_gets_localized_utc(self):
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        naive = datetime(2026, 5, 28, 12, 0, 0)
        ts = _to_pd_timestamp_utc(naive)
        assert ts.tz is not None
        assert ts.tz.utcoffset(None).total_seconds() == 0

    def test_aware_datetime_passthrough(self):
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        aware = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        ts = _to_pd_timestamp_utc(aware)
        assert ts.tz is not None
        assert ts.tz.utcoffset(None).total_seconds() == 0
        assert ts.hour == 12

    def test_aware_non_utc_gets_converted(self):
        """Non-UTC aware datetime must be CONVERTED (not just preserved)
        so the resulting wall-clock time reflects UTC, not the source tz."""
        from datetime import timedelta
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc
        # UTC+8 noon = UTC 04:00
        aware_utc8 = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        ts = _to_pd_timestamp_utc(aware_utc8)
        assert ts.tz.utcoffset(None).total_seconds() == 0
        assert ts.hour == 4


class TestTfOffsets:
    @pytest.mark.parametrize("tf,expected_seconds", [
        ("1m", 60), ("3m", 180), ("5m", 300), ("15m", 900), ("30m", 1800),
        ("1h", 3600), ("2h", 7200), ("4h", 14400), ("6h", 21600),
        ("8h", 28800), ("12h", 43200),
        ("1d", 86400), ("3d", 259200), ("1w", 604800),
    ])
    def test_timedelta_tfs(self, tf, expected_seconds):
        from src.utils.ohlcv_utils import TF_OFFSETS
        assert TF_OFFSETS[tf].total_seconds() == expected_seconds

    def test_1M_is_dateoffset(self):
        from src.utils.ohlcv_utils import TF_OFFSETS
        from pandas.tseries.offsets import DateOffset
        assert isinstance(TF_OFFSETS["1M"], DateOffset)

    def test_1M_advances_calendar_aware(self):
        """1M must respect calendar month length (28-31 days), not be a fixed
        30-day delta."""
        from src.utils.ohlcv_utils import TF_OFFSETS
        jan = pd.Timestamp("2026-01-31", tz="UTC")
        feb = jan + TF_OFFSETS["1M"]
        # Feb has 28 days in 2026 → Jan 31 + 1M = Feb 28 (pandas DateOffset behavior)
        assert feb.month == 2

    def test_unknown_tf_absent(self):
        from src.utils.ohlcv_utils import TF_OFFSETS
        assert "7m" not in TF_OFFSETS
        assert "2d" not in TF_OFFSETS


class TestFmtCandleTime:
    @pytest.mark.parametrize("tf,expected", [
        ("1m", "12:34"), ("3m", "12:34"), ("5m", "12:34"),
        ("15m", "12:34"), ("30m", "12:34"),
    ])
    def test_intraday_minute(self, tf, expected):
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-28 12:34:00", tz="UTC")
        assert _fmt_candle_time(dt, tf) == expected

    @pytest.mark.parametrize("tf", ["1h", "2h", "4h", "6h", "8h", "12h"])
    def test_hour_tfs(self, tf):
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-28 12:00:00", tz="UTC")
        assert _fmt_candle_time(dt, tf) == "05-28 12:00"

    @pytest.mark.parametrize("tf", ["1d", "3d", "1w"])
    def test_day_week_tfs(self, tf):
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-28", tz="UTC")
        assert _fmt_candle_time(dt, tf) == "2026-05-28"

    def test_1M_month_format(self):
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-01", tz="UTC")
        assert _fmt_candle_time(dt, "1M") == "2026-05"

    def test_unknown_tf_degraded_fallback(self):
        """Unknown tf returns ISO date — degraded fallback, no raise."""
        from src.utils.ohlcv_utils import _fmt_candle_time
        dt = pd.Timestamp("2026-05-28 12:34:00", tz="UTC")
        result = _fmt_candle_time(dt, "7m")  # synthetic unknown
        assert result == "2026-05-28"  # falls back to %Y-%m-%d


# === Task 2: RVol column ===

from unittest.mock import AsyncMock, MagicMock

from tests.fixtures.multi_tf_ohlcv import (
    df_5m_130bars, df_5m_anomaly,
    df_4h_250bars, df_1d_250bars,  # used by TestInProgressHint (Task 3)
    fake_ticker_81870,
)


def _build_gmd_deps(ticker, ohlcv_by_tf, symbol="BTC/USDT:USDT", tf="5m"):
    """Local copy of _build_deps from test_iter_w2r2_next_d_goldens.

    Intentional copy (not import) to avoid coupling this iter's tests to a
    sibling test file's internal helper. If `_build_deps` proves stable across
    iter boundaries, a future refactor can promote it to
    `tests/fixtures/multi_tf_ohlcv.py` co-located with the fixtures it consumes.
    """
    from src.services.technical import TechnicalAnalysisService
    deps = MagicMock()
    deps.symbol = symbol
    deps.timeframe = tf
    deps.technical = TechnicalAnalysisService()
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)

    async def _ohlcv(sym, t, limit):
        if t not in ohlcv_by_tf:
            raise RuntimeError(f"no fixture for {t}")
        return ohlcv_by_tf[t]

    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv)
    return deps


class TestRVolColumn:
    @pytest.mark.asyncio
    async def test_rvol_column_header_present(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 1: OHLCV table header has RVol(×SMA20) column."""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "RVol(×SMA20)" in out, f"RVol column header missing: {out[:600]}"

    @pytest.mark.asyncio
    async def test_rvol_values_have_x_suffix(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 1: each RVol value renders with × suffix (e.g. `1.00×`)."""
        import re
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        # Extract OHLCV section: split at next section header `=== Period`
        # NOT just `===` (the Recent Candles header has its own closing `===`
        # that would truncate the section to just the header tail).
        section = out.split("=== Recent Candles")[1].split("=== Period")[0]
        # At least one row should have a `N.NN×` value
        assert re.search(r"\d+\.\d{2}×", section), \
            f"No RVol value with × suffix found in OHLCV section: {section[:600]}"

    @pytest.mark.asyncio
    async def test_rvol_numeric_matches_vol_over_sma20(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 1: RVol value == bar.volume / SMA(20) of last 20 closed bars
        ending at that bar.

        df_5m_130bars has constant volume=100, so every bar's vol / SMA(20) = 1.0.
        """
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        section = out.split("=== Recent Candles")[1].split("=== Period")[0]
        # Every visible RVol value (vol / 100 = 1.0) should render as `1.00×`
        # — assert at least one such value appears (full match across all
        # rows is brittle to column-alignment whitespace; presence is enough).
        assert "1.00×" in section, \
            f"Expected RVol 1.00× (vol/SMA=1.0 for constant-vol fixture); section={section[:600]}"

    @pytest.mark.asyncio
    async def test_rvol_marker_consistency_high_volume(
        self, fake_ticker_81870, df_5m_anomaly,
    ):
        """Issue 1: when bar volume = 6× the baseline (input ratio), RVol on
        rendered table shows ≈ 4.8× AND vol↑ marker present. Tests common
        case (not FP-boundary).

        df_5m_anomaly: bar 127 volume = 600 vs baseline 100. The rendered
        RVol uses `rolling(20).mean()` AT bar 127, which **includes** bar
        127's anomalous volume in the SMA window: SMA = (19×100 + 600)/20 =
        125 → RVol = 600/125 = 4.8× (matches df_4h_recent_vol_spike fixture
        docstring math). The 6× input ratio gets attenuated by the SMA
        self-inclusion to ~4.8× — this is by design (RVol shows the bar's
        volume relative to its own 20-bar context, not a forward-looking
        baseline).
        """
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_anomaly})
        out = await get_market_data(deps)
        # Common case: very-high RVol bar
        # Find a row with a RVol ratio > 4 — should be the anomaly bar
        import re
        # Match `<digit(s)>.<digit><digit>×` and look for high values
        rvol_matches = re.findall(r"(\d+)\.(\d{2})×", out)
        high_rvols = [float(f"{a}.{b}") for a, b in rvol_matches if int(a) >= 4]
        assert high_rvols, \
            f"Expected at least one RVol ≥ 4.00× from anomaly fixture; out: {out[:600]}"
        assert "vol↑" in out, \
            f"vol↑ marker should accompany high RVol; out: {out[:600]}"

    @pytest.mark.asyncio
    async def test_rvol_marker_consistency_low_volume(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 1: when RVol << 2, no vol↑ marker.

        df_5m_130bars: constant volume → RVol ≈ 1.00×, no vol↑.
        """
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        # In a constant-volume fixture, no bar should trigger vol↑
        section = out.split("=== Recent Candles")[1].split("=== Period")[0]
        assert "vol↑" not in section, \
            f"vol↑ should not fire on constant-volume fixture; section: {section[:600]}"


# === Task 3: in-progress candle hint ===

class TestInProgressHint:
    @pytest.mark.asyncio
    async def test_in_progress_indicator_5m(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 2: OHLCV header contains 'in-progress HH:MM still open, closes at HH:MM'
        for intraday 5m tf."""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        # df_5m_130bars uses start_ms=1_700_000_000_000 (= 2023-11-14 22:13:20 UTC)
        # so the in-progress bar = closed[129] open
        # We assert presence of marker + closes-at clause, not exact times
        # (start_ms makes exact-time assertion brittle if fixture changes).
        assert "in-progress " in out
        assert "still open, closes at " in out

    @pytest.mark.asyncio
    async def test_in_progress_indicator_4h_format(
        self, fake_ticker_81870, df_4h_250bars,
    ):
        """Issue 2: 4h tf uses MM-DD HH:MM format in in-progress hint."""
        import re
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(
            fake_ticker_81870, {"4h": df_4h_250bars}, tf="4h",
        )
        out = await get_market_data(deps, timeframe="4h")
        # Header should contain `in-progress <MM-DD HH:MM> still open, closes at <MM-DD HH:MM>`
        m = re.search(
            r"in-progress (\d{2}-\d{2} \d{2}:\d{2}) still open, closes at (\d{2}-\d{2} \d{2}:\d{2})",
            out,
        )
        assert m, f"4h in-progress hint missing or wrong format; out={out[:1200]}"

    @pytest.mark.asyncio
    async def test_in_progress_indicator_1d_format(
        self, fake_ticker_81870, df_1d_250bars,
    ):
        """Issue 2: 1d tf uses YYYY-MM-DD format."""
        import re
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(
            fake_ticker_81870, {"1d": df_1d_250bars}, tf="1d",
        )
        out = await get_market_data(deps, timeframe="1d")
        m = re.search(
            r"in-progress (\d{4}-\d{2}-\d{2}) still open, closes at (\d{4}-\d{2}-\d{2})",
            out,
        )
        assert m, f"1d in-progress hint missing or wrong format; out={out[:1200]}"

    @pytest.mark.asyncio
    async def test_in_progress_time_arithmetic_intraday(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 2: in-progress_open == last_closed_open + tf_offset.
        in-progress_close == in-progress_open + tf_offset.

        Use a custom 5m fixture with predictable last-closed timestamp.
        """
        import re, pandas as pd
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc, TF_OFFSETS
        from src.agent.tools_perception import get_market_data

        # Manually take df_5m_130bars and compute expected times
        df = df_5m_130bars
        # df has 129 closed + 1 in-progress; _closed_bars drops the last bar,
        # so last closed = df.iloc[-2] (index 128).
        last_closed_ts_raw = df["timestamp"].iloc[-2]
        last_closed_dt = _to_pd_timestamp_utc(last_closed_ts_raw)
        expected_open = last_closed_dt + TF_OFFSETS["5m"]
        expected_close = expected_open + TF_OFFSETS["5m"]

        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df})
        out = await get_market_data(deps)
        assert expected_open.strftime("%H:%M") in out, \
            f"Expected in-progress open {expected_open.strftime('%H:%M')} in out; out={out[:1200]}"
        assert expected_close.strftime("%H:%M") in out, \
            f"Expected in-progress close {expected_close.strftime('%H:%M')} in out; out={out[:1200]}"

    @pytest.mark.asyncio
    async def test_in_progress_time_arithmetic_monthly(
        self, fake_ticker_81870,
    ):
        """Issue 2: 1M tf must use pd.DateOffset (calendar-aware), not Timedelta
        (months are 28-31 days, not fixed)."""
        import pandas as pd
        from tests.fixtures.multi_tf_ohlcv import _build
        from src.agent.tools_perception import get_market_data

        # Build a 1M fixture: 80 closed bars + 1 in-progress, starting 2020-01-01
        # so last closed is around 2026-08-01 → in-progress=2026-09-01, closes=2026-10-01
        # (or wherever the timeline lands — assert relative arithmetic, not absolutes)
        closes = [50000.0 + i * 100 for i in range(81)]
        df_1M = _build(
            start_ms=int(pd.Timestamp("2020-01-01", tz="UTC").value / 1e6),
            tf="1M", closes=closes,
        )

        from src.utils.ohlcv_utils import _to_pd_timestamp_utc, TF_OFFSETS
        last_closed_dt = _to_pd_timestamp_utc(df_1M["timestamp"].iloc[-2])
        expected_open = last_closed_dt + TF_OFFSETS["1M"]
        expected_close = expected_open + TF_OFFSETS["1M"]

        deps = _build_gmd_deps(fake_ticker_81870, {"1M": df_1M}, tf="1M")
        out = await get_market_data(deps, timeframe="1M")

        # 1M format = %Y-%m
        assert expected_open.strftime("%Y-%m") in out, \
            f"Expected monthly in-progress open {expected_open.strftime('%Y-%m')}; out={out[:1200]}"
        assert expected_close.strftime("%Y-%m") in out, \
            f"Expected monthly in-progress close {expected_close.strftime('%Y-%m')}; out={out[:1200]}"

    @pytest.mark.asyncio
    async def test_unsupported_tf_degraded_fallback(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 2: unknown tf → degraded fallback (no in-progress hint),
        no raise. Backward-compat with existing default-fallback at line 175."""
        from src.agent.tools_perception import get_market_data
        # Synthesize a fixture with non-CCXT tf label "7m"
        df = df_5m_130bars
        deps = _build_gmd_deps(fake_ticker_81870, {"7m": df}, tf="7m")
        # Should NOT raise
        out = await get_market_data(deps, timeframe="7m")
        # In-progress hint should be absent (or replaced by base header)
        assert "in-progress" not in out, \
            f"Unknown tf should skip in-progress hint; out={out[:1200]}"
        # Recent Candles header should still appear (degraded fallback, not crash)
        assert "=== Recent Candles" in out


# === Task 4: delete N-candle High-Low row ===

class TestDeletedNCandleHL:
    @pytest.mark.asyncio
    async def test_no_n_candle_high_low_row(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Market Context no longer contains `<N>-candle High-Low` row.
        Audit: 1.1% adoption; 24h H/L (ticker section, 54.4% adoption) is the
        surviving anchor."""
        import re
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        # Old format: `30-candle High-Low: 76430 — 77594`
        assert not re.search(r"\d+-candle High-Low:", out), \
            f"N-candle High-Low row should be deleted; out={out[:1200]}"
        # 24h H/L (from ticker) should still be present
        assert "24h High:" in out and "24h Low:" in out, \
            f"24h H/L should still be in ticker section as surviving anchor"
        # Market Context section should still exist (ATR / Last bar vol remain)
        assert "=== Market Context ===" in out
        assert "ATR(14):" in out


# === Task 5: delete Avg range from Period summary ===

class TestPeriodSummaryNoAvgRange:
    @pytest.mark.asyncio
    async def test_period_summary_no_avg_range(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Period summary section no longer contains Avg range row.
        Audit: ~3% adoption (1.5% verbatim + 1.9% concept) — dead metric."""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        # Old: `Avg range (H-L):    last 5 N / prior 5 M (R×)`
        assert "Avg range" not in out, \
            f"Avg range should be deleted from Period summary; out={out[:1200]}"

    @pytest.mark.asyncio
    async def test_period_summary_keeps_avg_vol_and_net_delta(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Period summary retains Avg vol (~10-15% adoption) and Net Δclose
        (~20-25% adoption)."""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Period summary" in out
        assert "Avg vol:" in out, f"Avg vol should remain; out={out[:1200]}"
        assert "Net Δclose:" in out, f"Net Δclose should remain; out={out[:1200]}"


# === Task 6: docstring updates across 3 channels ===

class TestDocstringRewrite:
    def test_ch_desc_description_contains_new_content(self):
        """CH-DESC (tools_descriptions.py:GET_MARKET_DATA_DESCRIPTION
        override → tool_def.description) reflects new OHLCV table format
        (RVol column + in-progress hint), drops 'volume ratio' fact-drift.
        Block-style Example call/output preserved (bypasses griffe per
        @tool(description=...) override; verified by test_dual_mode_tool_wrapper)."""
        from src.agent.trader import create_trader_agent
        from src.config import PersonaConfig

        agent = create_trader_agent(model="test", persona_config=PersonaConfig())
        tool = agent._function_toolset.tools["get_market_data"]
        desc = tool.tool_def.description

        # Block-style sections still present (CH-DESC bypasses griffe)
        assert "=== Ticker" in desc, "Ticker section header missing from CH-DESC"
        assert "=== Recent Candles" in desc, "Recent Candles header missing"
        assert "=== Period summary" in desc, "Period summary header missing"

        # New content from this iter:
        assert "RVol(×SMA20)" in desc or "RVol" in desc, \
            f"RVol column documentation missing in CH-DESC: {desc!r}"
        assert "in-progress" in desc, \
            f"in-progress hint documentation missing in CH-DESC: {desc!r}"

        # Markers semantics preserved:
        assert "vol↑" in desc, "vol↑ marker semantics missing"
        assert "range↑" in desc, "range↑ marker semantics missing"

        # Avg range deletion reflected:
        assert "Avg range" not in desc, \
            f"Avg range should be removed from Period summary docs: {desc!r}"

    def test_candle_count_clamp_text_in_params_schema(self):
        """Clamp explicit text reaches LLM via CH-ARGS channel (trader.py:124-140
        inner ctx-receiver docstring's Args block → parameters_json_schema),
        NOT via CH-DESC. Per spec §2.5 / test_dual_mode_tool_wrapper: griffe
        parses the decorated function's own Args block — which for
        get_market_data lives at trader.py:124-140 inner ctx-receiver, NOT at
        tools_perception.py:51 impl."""
        from src.agent.trader import create_trader_agent
        from src.config import PersonaConfig

        agent = create_trader_agent(model="test", persona_config=PersonaConfig())
        tool = agent._function_toolset.tools["get_market_data"]
        schema = tool.tool_def.parameters_json_schema
        candle_count_desc = schema["properties"]["candle_count"]["description"]

        # Clamp explicit:
        assert "Clamped to [10, 80]" in candle_count_desc, \
            f"candle_count clamp explicit text missing in params schema: {candle_count_desc!r}"
        # Reasoning behind floor / cap:
        assert "minimum useful window" in candle_count_desc or "below 10" in candle_count_desc, \
            f"floor=10 reasoning missing: {candle_count_desc!r}"
        assert "exchange API" in candle_count_desc or "above 80" in candle_count_desc, \
            f"cap=80 reasoning missing: {candle_count_desc!r}"
