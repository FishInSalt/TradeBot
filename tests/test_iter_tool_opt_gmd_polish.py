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
