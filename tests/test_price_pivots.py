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


from src.agent.tools_perception import _render_pivot_rows, _bars_ago_fmt


def test_bars_ago_fmt_zero():
    assert _bars_ago_fmt(0) == "now"


def test_bars_ago_fmt_one():
    assert _bars_ago_fmt(1) == "1 bar ago"


def test_bars_ago_fmt_many():
    assert _bars_ago_fmt(23) == "23 bars ago"


# --- _render_pivot_rows ---


def test_render_full_load():
    """2 swing high + 2 swing low + 3 prior all ok → 5 above rows + 5 below rows + footer empty."""
    swing_highs = [(23, 66890.0), (47, 67120.5)]
    swing_lows = [(8, 66102.0), (19, 65800.0)]
    prior_d = ("ok", 67234.0, 65500.0)
    prior_w = ("ok", 68500.0, 64200.0)
    prior_m = ("ok", 71200.0, 60800.0)
    above, below, footer = _render_pivot_rows(swing_highs, swing_lows, prior_d, prior_w, prior_m, current_price=66523.40)
    assert len(above) == 5
    assert len(below) == 5
    assert footer == []


def test_render_swing_high_below_current_price():
    """A swing high entry whose price < current_price routes to below group (business fact, not contradictory)."""
    swing_highs = [(10, 65000.0)]  # below 66523
    swing_lows = []
    null_prior = ("insufficient", None, None)
    above, below, footer = _render_pivot_rows(swing_highs, [], null_prior, null_prior, null_prior, current_price=66523.40)
    assert above == []
    assert any("Swing High" in line and "65,000.00" in line for line in below)


def test_render_above_sorted_ascending_distance():
    """above rows ordered by abs(distance%) ascending."""
    swing_highs = [(84, 68750.0), (23, 66890.0), (47, 67120.5)]  # +3.35%, +0.55%, +0.90%
    swing_lows = []
    prior_d = ("ok", 67234.0, 65500.0)  # +1.07% above, -1.54% below
    prior_w = ("ok", 68500.0, 64200.0)  # +2.97% above, -3.49% below
    prior_m = ("ok", 71200.0, 60800.0)  # +7.03% above, -8.60% below
    above, below, footer = _render_pivot_rows(swing_highs, swing_lows, prior_d, prior_w, prior_m, current_price=66523.40)
    # Expected order: +0.55, +0.90, +1.07, +2.97, +3.35, +7.03
    assert "66,890.00" in above[0]
    assert "67,120.50" in above[1]
    assert "67,234.00" in above[2]
    assert "68,500.00" in above[3]
    assert "68,750.00" in above[4]
    assert "71,200.00" in above[5]


def test_render_signs_correct():
    """above rows show + sign, below rows show - sign."""
    swing_highs = [(10, 67000.0)]
    swing_lows = [(8, 66000.0)]
    null_prior = ("insufficient", None, None)
    above, below, _ = _render_pivot_rows(swing_highs, swing_lows, null_prior, null_prior, null_prior, current_price=66500.0)
    assert "(+" in above[0]
    assert "(-" in below[0]


def test_render_swing_row_has_bars_ago():
    swing_highs = [(23, 66890.0)]
    null_prior = ("insufficient", None, None)
    above, _, _ = _render_pivot_rows(swing_highs, [], null_prior, null_prior, null_prior, current_price=66523.40)
    assert above == ["Swing High: 66,890.00 (+0.55%, 23 bars ago)"]


def test_render_prior_row_no_bars_ago():
    prior_d = ("ok", 67234.0, 65500.0)
    null_prior = ("insufficient", None, None)
    above, _, _ = _render_pivot_rows([], [], prior_d, null_prior, null_prior, current_price=66523.40)
    assert any(line == "Prior Daily H: 67,234.00 (+1.07%)" for line in above)
    for line in above:
        assert "bars ago" not in line


def test_render_above_empty_returns_empty_list():
    """When nothing routes to above, _render_pivot_rows returns empty list (caller substitutes '(none)')."""
    swing_lows = [(8, 65000.0)]
    null_prior = ("insufficient", None, None)
    above, below, _ = _render_pivot_rows([], swing_lows, null_prior, null_prior, null_prior, current_price=66500.0)
    assert above == []
    assert len(below) == 1


def test_render_below_empty_returns_empty_list():
    swing_highs = [(8, 67000.0)]
    null_prior = ("insufficient", None, None)
    above, below, _ = _render_pivot_rows(swing_highs, [], null_prior, null_prior, null_prior, current_price=66500.0)
    assert below == []
    assert len(above) == 1


def test_render_prior_insufficient_in_footer():
    """Single prior insufficient → footer line, not in above/below.
    (Indirectly verifies _get_prior_period_hl's 'insufficient' status via fixture injection — see spec §5.2.)"""
    prior_d = ("ok", 67234.0, 65500.0)
    prior_w = ("insufficient", None, None)
    prior_m = ("ok", 71200.0, 60800.0)
    above, below, footer = _render_pivot_rows([], [], prior_d, prior_w, prior_m, current_price=66523.40)
    assert footer == ["Prior Weekly H/L: insufficient data"]
    # Ensure weekly H/L not in above/below
    for line in above + below:
        assert "Weekly" not in line


def test_render_prior_unavailable_in_footer():
    """(Indirectly verifies _get_prior_period_hl's 'unavailable' status via fixture injection — see spec §5.2.)"""
    prior_d = ("ok", 67234.0, 65500.0)
    prior_w = ("ok", 68500.0, 64200.0)
    prior_m = ("unavailable", None, None)
    above, below, footer = _render_pivot_rows([], [], prior_d, prior_w, prior_m, current_price=66523.40)
    assert footer == ["Prior Monthly H/L: temporarily unavailable"]
