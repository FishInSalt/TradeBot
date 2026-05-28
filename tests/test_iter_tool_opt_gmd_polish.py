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

import pytest
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
