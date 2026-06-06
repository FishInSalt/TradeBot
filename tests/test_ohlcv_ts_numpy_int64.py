"""Regression tests for the in-progress candle timestamp bug.

Root cause: `_to_pd_timestamp_utc` gated the ms-epoch branch on
`isinstance(ts_val, (int, float))`. Under numpy 2.x, `numpy.int64` is NOT a
subclass of Python `int` (whereas `numpy.float64` IS a subclass of `float`),
so a value taken via column access `df["timestamp"].iloc[-1]` (dtype int64)
fell through to the no-`unit` fallback `pd.Timestamp(ts_val)`, which parses the
ms-epoch integer as NANOSECONDS — collapsing every timestamp to ~1970-01-01
00:xx. The get_market_data in-progress header then rendered a frozen, wrong
"in-progress 00:44 still open, closes at 00:59" (5m: "00:34/00:39").

The candle-row path `df.loc[idx]["timestamp"]` happened to be correct only
because a mixed-dtype row Series upcasts to float64 (a `float` subclass) —
which masked the helper's broken type contract.

These tests use INDEPENDENT expected values (hand-computed from a known
ms-epoch, never routed through the function under test) to avoid the
tautological self-consistency that let the original suite pass: the existing
test_in_progress_time_arithmetic_intraday computed its expectation by calling
the same buggy `_to_pd_timestamp_utc`, so both sides collapsed identically and
the assertion held.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from tests.fixtures.multi_tf_ohlcv import _build
from tests.test_iter_tool_opt_gmd_polish import _build_gmd_deps

# A known, 5m/15m-aligned ms-epoch: 2023-11-14 22:13:20 UTC.
_KNOWN_MS = 1_700_000_000_000
_KNOWN_UTC = "2023-11-14 22:13:20"


class TestToPdTimestampUtcNumpyInt64:
    """Direct unit test of the root-cause helper with a numpy.int64 input."""

    def test_numpy_int64_parsed_as_milliseconds_not_nanoseconds(self):
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc

        # Exactly the type produced by `df["timestamp"].iloc[-1]` on an int64 column.
        val = np.int64(_KNOWN_MS)
        assert not isinstance(val, int)  # documents the numpy 2.x footgun

        ts = _to_pd_timestamp_utc(val)
        # Independent expectation: must be 2023, NOT collapsed to 1970.
        assert ts.year == 2023, f"epoch parsed as ns → collapsed to {ts}"
        assert str(ts).startswith(_KNOWN_UTC)
        assert ts.tz is not None
        assert ts.utcoffset() == pd.Timedelta(0)  # UTC

    def test_numpy_int64_matches_python_int(self):
        """numpy.int64 and Python int of the same value must render identically."""
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc

        assert _to_pd_timestamp_utc(np.int64(_KNOWN_MS)) == _to_pd_timestamp_utc(_KNOWN_MS)

    def test_column_access_scalar_is_numpy_int64(self):
        """Document the exact provenance: an int64-column scalar IS numpy.int64."""
        df = pd.DataFrame({"timestamp": [_KNOWN_MS, _KNOWN_MS + 900_000]})
        scalar = df["timestamp"].iloc[-1]
        assert isinstance(scalar, np.integer)  # numpy scalar, not a Python int
        assert not isinstance(scalar, int)


class TestInProgressHeaderUsesRealClock:
    """End-to-end: the get_market_data in-progress header must reflect the real
    UTC wall-clock derived from the int64 timestamp column, not the frozen
    1970-collapsed value (5m → 00:34/00:39)."""

    @pytest.mark.asyncio
    async def test_5m_in_progress_header_not_collapsed_to_1970(self):
        # 130 rows of 5m bars anchored at a known ms-epoch. The new design renders
        # the in-progress bar (df.iloc[-1] = index 129) in its own section, so its
        # open-ts comes straight from that row → open = _KNOWN_MS + 129*300_000.
        step_ms = 300_000
        closes = [70000.0 + i for i in range(130)]
        df = _build(start_ms=_KNOWN_MS, tf="5m", closes=closes)
        assert df["timestamp"].dtype == np.int64  # the dtype that triggers the bug

        # 新设计: in-progress section 直接渲 df.iloc[-1]（= 被丢弃那根），open = 该行 timestamp
        ip_open = pd.Timestamp(_KNOWN_MS + 129 * step_ms, unit="ms", tz="UTC")  # df.iloc[-1]，独立换算
        ip_close = ip_open + pd.Timedelta(minutes=5)
        exp_open = ip_open.strftime("%H:%M")
        exp_close = ip_close.strftime("%H:%M")

        ticker = SimpleNamespace(
            last=70128.0, bid=70127.9, ask=70128.1,
            high=70200.0, low=69900.0, base_volume=1234.56,
        )
        deps = _build_gmd_deps(ticker, {"5m": df}, tf="5m")
        from src.agent.tools_perception import get_market_data
        out = await get_market_data(deps, timeframe="5m")

        assert f"{exp_open} open, closes {exp_close}" in out, (
            f"expected in-progress {exp_open}/{exp_close} in In-progress Candle header; out={out[:600]}"
        )
        # 回归 guard: 绝不塌缩到 1970（旧 bug 渲成 00:xx）。
        assert ip_open.year == 2023
        assert "1970" not in out
