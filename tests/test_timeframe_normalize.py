"""Tests for src.utils.timeframe — free-form timeframe canonicalization.

Root cause (2026-06-09 sim #17 / session 64b4ea1f): the session was created with
primary timeframe "1H" (uppercase) — a value the config comment listed as a
valid option — which ccxt's case-sensitive parse_timeframe rejects ("timeframe
unit H is not supported"), crashing 7 cycles. normalize_timeframe folds the
unambiguous uppercase unit letters (H/D/W → h/d/w) to the project's lowercase
ccxt convention while preserving the minute (lowercase m) vs month (uppercase M)
distinction, and raises ValueError for genuinely unsupported values.
"""
import pytest

from src.utils.timeframe import SUPPORTED_TIMEFRAMES, normalize_timeframe


class TestNormalizeUnambiguousCaseFold:
    """The actual footgun: uppercase hour/day/week units fold to lowercase."""

    @pytest.mark.parametrize("raw, expected", [
        ("1H", "1h"),    # the exact bug value
        ("4H", "4h"),
        ("2H", "2h"),
        ("12H", "12h"),
        ("1D", "1d"),
        ("3D", "3d"),
        ("1W", "1w"),
    ])
    def test_uppercase_hour_day_week_folds_to_lowercase(self, raw, expected):
        assert normalize_timeframe(raw) == expected


class TestAlreadyCanonical:
    @pytest.mark.parametrize("tf", ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"])
    def test_lowercase_passes_through_unchanged(self, tf):
        assert normalize_timeframe(tf) == tf

    def test_month_uppercase_M_is_preserved(self):
        # "1M" = one month (ccxt uppercase-M convention); must NOT be lowered to
        # the minute "1m" — these are different timeframes.
        assert normalize_timeframe("1M") == "1M"


class TestMinuteMonthAmbiguityIsRejectedNotGuessed:
    """"5M" could mean 5 minutes (agent intent) or 5 months (ccxt M). We refuse
    to guess — reject rather than silently fetch the wrong granularity."""

    @pytest.mark.parametrize("ambiguous", ["5M", "15M", "30M"])
    def test_minute_amount_with_uppercase_M_raises(self, ambiguous):
        with pytest.raises(ValueError):
            normalize_timeframe(ambiguous)


class TestUnsupportedRaises:
    @pytest.mark.parametrize("bad", ["", "  ", "banana", "1hr", "60m", "1y", "abc1h", "h1"])
    def test_unsupported_raises_valueerror(self, bad):
        with pytest.raises(ValueError):
            normalize_timeframe(bad)

    def test_error_message_names_the_offending_value(self):
        with pytest.raises(ValueError, match="1hr"):
            normalize_timeframe("1hr")


class TestNonStringInputRaisesValueError:
    """Contract: normalize_timeframe raises ValueError (not TypeError) for any
    bad input — a TypeError leaking from an unhashable arg would be the same
    class of raw-exception-escape this module exists to prevent."""

    @pytest.mark.parametrize("bad", [["1h"], {"1h"}, {"a": 1}, 5, None, 1.5])
    def test_non_string_raises_valueerror_not_typeerror(self, bad):
        with pytest.raises(ValueError):
            normalize_timeframe(bad)


class TestSupportedSetDriftGuard:
    """SUPPORTED_TIMEFRAMES must stay in lockstep with ohlcv_utils.TF_OFFSETS —
    they are two views of the same canonical set; drift would let one accept a
    timeframe the other cannot render/offset."""

    def test_matches_tf_offsets_keys(self):
        from src.utils.ohlcv_utils import TF_OFFSETS
        assert set(TF_OFFSETS.keys()) == set(SUPPORTED_TIMEFRAMES)

    def test_every_supported_timeframe_normalizes_to_itself(self):
        for tf in SUPPORTED_TIMEFRAMES:
            assert normalize_timeframe(tf) == tf
