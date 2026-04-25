"""Iter 3 — get_price_pivots tests (algorithm / render / degradation layers)."""
from __future__ import annotations

import pandas as pd
import pytest

from src.agent.tools_perception import _compute_swing_pivots


def _df(highs: list[float], lows: list[float]) -> pd.DataFrame:
    """Build minimal OHLCV df from highs+lows; open/close/volume filled with placeholders."""
    n = len(highs)
    assert len(lows) == n
    return pd.DataFrame({
        "open": highs,
        "high": highs,
        "low": lows,
        "close": highs,
        "volume": [1.0] * n,
    })


# --- Algorithm: _compute_swing_pivots (Williams Fractal N=5, strict inequality) ---


def test_basic_fractal_swing_high():
    """30 bars, bar[10] high strictly greater than left 5 + right 5 → 1 swing high."""
    highs = [100.0 + i * 0.1 for i in range(30)]
    highs[10] = 105.0  # spike above neighbors
    lows = [99.0 + i * 0.1 for i in range(30)]
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    # bars_ago for index 10 in a 30-bar df: last_idx=29, ago=29-10=19
    assert h == [(19, 105.0)]
    assert l == []


def test_strict_inequality_plateau_no_pivot():
    """bar[10].high == bar[11].high → not a pivot (rolling-eq form would falsely tag this)."""
    highs = [100.0] * 30
    highs[10] = 105.0
    highs[11] = 105.0  # plateau
    lows = [99.0] * 30
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    assert h == []


def test_strict_inequality_single_side_tie():
    """bar[10].high > bar[9] but == bar[11] → not a pivot (strict on every neighbor)."""
    highs = [100.0] * 30
    highs[10] = 105.0
    highs[11] = 105.0  # right-side tie
    # left side strictly less
    for i in range(5, 10):
        highs[i] = 100.0 + (i - 5) * 0.5
    lows = [99.0] * 30
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    assert h == []


def test_multi_pivot_two_highs_one_low():
    """30 bars: bar[8] high spike, bar[20] high spike, bar[14] low spike."""
    highs = [100.0 + i * 0.05 for i in range(30)]
    lows = [99.0 + i * 0.05 for i in range(30)]
    highs[8] = 110.0
    highs[20] = 115.0
    lows[14] = 90.0
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    last_idx = 29
    # Order is loop order (i ascending → bars_ago descending)
    assert h == [(last_idx - 8, 110.0), (last_idx - 20, 115.0)]
    assert l == [(last_idx - 14, 90.0)]


def test_monotonic_uptrend_no_pivots():
    """100 bar strictly increasing highs → no swing high or low (each high beats prior; right window invalidates)."""
    highs = [100.0 + i for i in range(100)]
    lows = [99.0 + i for i in range(100)]
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    assert h == []
    assert l == []


def test_monotonic_downtrend_no_pivots():
    highs = [200.0 - i for i in range(100)]
    lows = [199.0 - i for i in range(100)]
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    assert h == []
    assert l == []


def test_unconfirmed_recent_pivot_excluded():
    """bar[95] is local max but right window incomplete (only 4 bars to the right) → excluded.
    confirm_end = last_idx - n = 99 - 5 = 94; loop range(5, 95) skips index 95."""
    highs = [100.0] * 100
    highs[95] = 999.0  # would be a pivot if right window were complete
    lows = [99.0] * 100
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    assert h == []  # not returned — unconfirmed


def test_insufficient_data_returns_empty():
    """len < 2N+1 = 11 → ([], []), no exception."""
    highs = [100.0] * 10
    lows = [99.0] * 10
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    assert h == []
    assert l == []


def test_boundary_minimum_length():
    """len == 11 (= 2N+1) → only bar[5] eligible (loop range(5, 6))."""
    highs = [100.0] * 11
    highs[5] = 110.0
    lows = [99.0] * 11
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    last_idx = 10
    assert h == [(last_idx - 5, 110.0)]
    assert l == []


def test_dual_pivot_high_and_low_same_bar():
    """30 bars, bar[15] is both swing high and swing low (expansion bar — high beats neighbors AND low undercuts neighbors)."""
    highs = [100.0] * 30
    lows = [99.0] * 30
    highs[15] = 110.0
    lows[15] = 90.0
    df = _df(highs, lows)
    h, l = _compute_swing_pivots(df, n=5)
    last_idx = 29
    assert h == [(last_idx - 15, 110.0)]
    assert l == [(last_idx - 15, 90.0)]
