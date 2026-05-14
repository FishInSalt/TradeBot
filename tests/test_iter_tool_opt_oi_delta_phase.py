"""Iter tool-opt-oi-delta-phase drift guards (G-calc-rigor-audit §G-6).

OKX rubik OI history endpoint returns the in-progress 1H bucket as the
newest row (verified by .working/tool-optimization/probe_okx_oi_phase.py:
51.44 min ago < 60 min → bucket still open). Pre-fix, the renderer used
this in-progress bucket as `current` and computed deltas vs the prior
closed bucket — producing partial-vs-full comparisons (e.g., 35-min
partial OI vs 1h-ago full bucket as "1h ago, X%"), inflating phase
drift in volatile periods.

Post-fix `_derive_oi_anchors`:
- newest.timestamp + period > now → is_in_progress = True
- All anchor indices shift forward by 1 (current = points[-2],
  1h-ago = points[-3], 24h-ago = points[-26])
- Caller renders "(as of last closed 1H bucket HH:MM UTC; …)" disclosure

This file's 4 tests cover:
1. In-progress shift correctness (current = closed prior bucket)
2. Closed-newest fallback parity (current = points[-1], no disclosure)
3. Empty `points` returns (None, "", False)
4. Insufficient history when in-progress (returns None)
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.tools_perception import _derive_oi_anchors, get_derivatives_data
from src.integrations.exchange.base import (
    FundingRate, LongShortRatio, OpenInterestHistoryPoint,
)


PERIOD_MS = 3600 * 1000


def _make_hourly_points(values_usd: list[float], *, newest_ts_ms: int):
    """Build N points hourly-spaced, oldest first, newest at `newest_ts_ms`."""
    n = len(values_usd)
    base_ts = newest_ts_ms - (n - 1) * PERIOD_MS
    return [
        OpenInterestHistoryPoint(
            timestamp=base_ts + i * PERIOD_MS,
            open_interest=v / 80000.0,
            open_interest_value=v,
        )
        for i, v in enumerate(values_usd)
    ]


def test_in_progress_newest_shifts_anchors_forward_by_one():
    """Newest bucket in-progress: current shifts to points[-2], 1h-ago to
    points[-3], 24h-ago to points[-26]. 26 records suffice (need 26 for
    24h-ago after shift)."""
    vals = [2_900_000_000.0] * 26
    vals[-26] = 2_910_000_000.0   # 24h ago closed
    vals[-3] = 2_930_000_000.0    # 1h ago closed
    vals[-2] = 2_920_000_000.0    # current closed
    vals[-1] = 9_999_999_999.0    # in-progress (must be ignored)
    newest_ts = 1_800_000_000_000  # arbitrary anchor
    points = _make_hourly_points(vals, newest_ts_ms=newest_ts)
    # now_ms inside the in-progress bucket window
    now_ms = newest_ts + 30 * 60_000  # 30 min into the 1H bucket

    current, anchors, was_shifted = _derive_oi_anchors(points, now_ms=now_ms)

    assert was_shifted is True
    assert current is not None
    assert current.open_interest_value == 2_920_000_000.0
    # Deltas: 2920/2930 - 1 ≈ -0.34%; 2920/2910 - 1 ≈ +0.34%
    assert "1h ago $2.93B, -0.3%" in anchors
    assert "24h ago $2.91B, +0.3%" in anchors


def test_closed_newest_keeps_original_indices_no_disclosure():
    """Newest bucket already closed (now_ms > newest.ts + period): current =
    points[-1], anchors at [-2]/[-25] — backward-compatible behavior. The
    happy path of pre-fix code is preserved."""
    vals = [2_900_000_000.0] * 26
    vals[-25] = 2_910_000_000.0
    vals[-2] = 2_930_000_000.0
    vals[-1] = 2_920_000_000.0  # treated as closed current
    newest_ts = 1_800_000_000_000
    points = _make_hourly_points(vals, newest_ts_ms=newest_ts)
    now_ms = newest_ts + PERIOD_MS + 60_000  # bucket already closed + 1 min

    current, anchors, was_shifted = _derive_oi_anchors(points, now_ms=now_ms)

    assert was_shifted is False
    assert current is not None
    assert current.open_interest_value == 2_920_000_000.0
    assert "1h ago $2.93B, -0.3%" in anchors
    assert "24h ago $2.91B, +0.3%" in anchors


def test_empty_points_returns_none_tuple():
    current, anchors, was_shifted = _derive_oi_anchors([], now_ms=int(time.time() * 1000))
    assert current is None
    assert anchors == ""
    assert was_shifted is False


def test_single_in_progress_point_returns_none_current():
    """Length 1 with in-progress newest: no closed bucket available → current=None."""
    newest_ts = 1_800_000_000_000
    points = _make_hourly_points([2_920_000_000.0], newest_ts_ms=newest_ts)
    now_ms = newest_ts + 30 * 60_000  # in-progress

    current, anchors, was_shifted = _derive_oi_anchors(points, now_ms=now_ms)

    assert current is None
    assert anchors == ""
    assert was_shifted is True


@pytest.mark.asyncio
async def test_derivs_oi_in_progress_renders_disclosure_label():
    """End-to-end via get_derivatives_data: in-progress newest bucket → OI
    line includes 'as of last closed 1H bucket HH:MM UTC' disclosure."""
    vals = [2_900_000_000.0] * 26
    vals[-26] = 2_910_000_000.0
    vals[-3] = 2_930_000_000.0
    vals[-2] = 2_920_000_000.0
    vals[-1] = 9_999_999_999.0
    # Anchor the newest bucket to "30 min ago" wall-clock so the wrapper's
    # internal datetime.now(timezone.utc) marks it as in-progress.
    now_wall_ms = int(time.time() * 1000)
    newest_ts = now_wall_ms - 30 * 60_000
    points = _make_hourly_points(vals, newest_ts_ms=newest_ts)

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_funding_rate = AsyncMock(return_value=FundingRate(
        symbol="BTC/USDT:USDT", rate=0.000014,
        next_funding_time=now_wall_ms + 3600_000, timestamp=now_wall_ms,
    ))
    deps.market_data.get_long_short_ratio = AsyncMock(return_value=LongShortRatio(
        symbol="BTC/USDT:USDT", long_short_ratio=0.66,
        long_ratio=0.399, short_ratio=0.601, timestamp=now_wall_ms,
    ))
    deps.market_data.get_open_interest_history = AsyncMock(return_value=points)

    out = await get_derivatives_data(deps, "BTC/USDT:USDT")
    oi_line = next(ln for ln in out.splitlines() if ln.startswith("Open Interest:"))

    assert "$2.92B" in oi_line  # current is points[-2], not points[-1]
    assert "as of last closed 1H bucket" in oi_line
    assert "1h ago $2.93B" in oi_line
    assert "24h ago $2.91B" in oi_line
    # Make sure the in-progress bucket value ($9.99B) is NOT rendered as current.
    assert "$10.00B" not in oi_line and "9.99" not in oi_line
