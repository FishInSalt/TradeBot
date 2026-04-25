# Iter 3 — `get_price_pivots` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `get_price_pivots` perception tool that lists structural support/resistance — Williams fractal swing pivots from the last 100 main-TF bars + prior daily/weekly/monthly H/L — grouped above/below current price, fact-only.

**Architecture:** Pure helpers + tool in `src/agent/tools_perception.py` (algorithm `_compute_swing_pivots`, prior-period helper `_get_prior_period_hl`, renderer `_render_pivot_rows`, `_bars_ago_fmt`); `@agent.tool` thin wrapper in `src/agent/trader.py` (matches existing 18 perception tools, e.g. `trader.py:202-252`). 4-way concurrent OHLCV fetch via `asyncio.gather`; ticker fetch is the **only** condition that short-circuits the whole tool to a single `temporarily unavailable` line. Per-source three-state degradation contract (fact / `insufficient data` / `temporarily unavailable`).

**Tech Stack:** Python 3.x, pandas (DataFrame), numpy (loop), pytest + pytest-asyncio + pytest-mock, pydantic-ai (`@agent.tool` decorator).

**Spec:** `docs/superpowers/specs/2026-04-25-iter3-price-pivots-design.md` (commit `325291f` on branch `iter3-price-pivots-spec`). Each task references the relevant spec section. Read `memory/project_iter3_review_digest.md` for ~25 already-rejected scope-out issues + 7 fact calibrations before reopening any design point.

**Test count:** 786 → ≈818 (+32: algorithm 10 / render 13 incl. `_bars_ago_fmt` 3 / degradation 6 / fact-only 1 / persona drift 2). Spec §6 #8 anchors +29; the 3 `_bars_ago_fmt` unit tests are minor-helper coverage above the +29 contract — `_get_prior_period_hl`'s three-state behavior is covered indirectly via render-layer fixtures (per spec §5.2 "mock `_get_prior_period_hl` directly into `_render_pivot_rows`"), so it gets no dedicated unit-test block.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `src/agent/tools_perception.py` | Modify (+200-250) | Pure impl: `_compute_swing_pivots`, `_get_prior_period_hl`, `_render_pivot_rows`, `_bars_ago_fmt` helpers + `async def get_price_pivots(deps)`. **No decorator** (matches existing pattern; this file does not import `agent`). |
| `src/agent/trader.py` | Modify (+15-18) | `@agent.tool` thin wrapper at end of perception block; `REGISTERED_TOOL_NAMES` adds `"get_price_pivots"` to perception group. |
| `src/agent/persona.py` | Modify (+1-2) | Layer 1 `_build_layer1` appends 1 bullet (24 → 25). |
| `tests/test_price_pivots.py` | **Create** (+450-550) | 29 tests: 10 algorithm / 13 render (incl. 3 bars_ago) / 6 degradation. |
| `tests/test_fact_only_wordlist.py` | Modify (+30-50) | Add `PIVOTS_BANNED_WORDS` (per-tool local wordlist — new pattern, see Task 6); add `timeframe: str = "5m"` to `MockDeps`; add `test_get_price_pivots_fact_only_5_scenarios`. |
| `tests/test_persona.py` | Modify (+22-30) | Add `test_layer1_bullet_count_25` + `test_layer1_includes_get_price_pivots`. |
| `tests/test_trader_agent.py` | Modify (+1-2) | `len == 29` → `== 30`; comment `(18+10+1)` → `(19+10+1)`. |

## Locked Design Decisions (do not reopen)

- **Algorithm**: Williams Fractal N=5, **strict inequality** (`>`, not `≥`). Plateau ties produce no pivot. Explicit numpy loop (rolling-based naïve form would falsely tag flat plateaus). Confirmed pivots only — last N=5 bars excluded. Min `bars_ago = 5`.
- **Window**: Fixed 100 bars main TF + `1d`/`1w`/`1M` each `limit=2`, take `iloc[-2]`.
- **Concurrency**: 4-way `asyncio.gather` for OHLCV; ticker fetched serially before gather (need baseline price for distance %).
- **Baseline price**: `ticker.last` (real-time, do not substitute `df.close.iloc[-1]`).
- **Output grouping**: `Levels Above` / `Levels Below`, sorted by `abs(distance%)` ascending within each.
- **Row formats**: Swing → `Swing High: 66,890.00 (+0.55%, 23 bars ago)`; Prior → `Prior Daily H: 67,234.00 (+1.07%)` (no `bars ago`).
- **Three-state contract**: `fact` / `insufficient data` / `temporarily unavailable` — same as N3 spec §3.5.
- **Whole-tool short-circuit**: ticker exception **only**. Any OHLCV exception/empty degrades that section, framework still renders.
- **swing_status states**: `Swing pivots: temporarily unavailable` / `Swing pivots: insufficient data (need 11+ bars, got N)` / `(Window: N bars, less than 100)` / `(Window: N bars, less than 100 — no swing pivots found)` / `(No swing pivots in 100-bar window)` / `None` (full-load).
- **Architecture layer**: All helpers at `tools_perception.py` module-level (no new `services/pivots.py`); `@agent.tool` thin wrapper in `trader.py` — physically required because `tools_perception.py` does not import the `agent` object.
- **Fact-only wordlist**: Per-tool local `PIVOTS_BANNED_WORDS` (new pattern — existing 4 per-tool fact-only tests all use the global `FACT_ONLY_BANNED_WORDS_RE`). Reason: structural/evaluative words (`strong`/`weak`/`important`/`key`/`major`/`minor`/`critical`/`crucial`/`significant`) have not been audited against the 19 existing tools' outputs. Promotion to global is a separate observation-period PR.
- **MockDeps extension**: `tests/test_fact_only_wordlist.py:36-41` adds `timeframe: str = "5m"`. Required because `get_price_pivots` reads `deps.timeframe` on entry; existing 4 tests don't read it.
- **REGISTERED_TOOL_NAMES drift**: `tests/test_trader_agent.py:84` hard-codes `len == 29`; must update to `== 30` (comment `(18+10+1)` → `(19+10+1)`). This is **not** zero-effort auto-coverage.
- **Persona drift guard**: Add `assert "## How to Think" in prompt` before splitting on that header — protects against silent false-pass if Layer 2 header is renamed.
- **`_get_prior_period_hl` no dedicated unit tests**: Spec §5.2 specifies that the helper's three-state output (`ok` / `insufficient` / `unavailable`) is covered by the render-layer tests through fixture injection. The helper is implemented in Task 2 alongside the renderer. Adding standalone unit tests would over-spec relative to spec §6 #8.

---

## Task 1: Williams Fractal algorithm — `_compute_swing_pivots`

**Spec ref:** §4.1, §5.1.

**Files:**
- Create: `tests/test_price_pivots.py`
- Modify: `src/agent/tools_perception.py` (insert helpers near end of file, before `get_price_pivots` itself which is added in Task 3)

- [ ] **Step 1.1: Create the test file with 10 failing algorithm tests**

Write `tests/test_price_pivots.py`:

```python
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
```

- [ ] **Step 1.2: Run tests, verify pytest collection fails**

Run: `pytest tests/test_price_pivots.py -v`
Expected: pytest **collection** fails with `ImportError: cannot import name '_compute_swing_pivots' from 'src.agent.tools_perception'` — module-level import in the new test file cannot resolve until Step 1.3 lands. pytest will display each test name once with the same ImportError; this is a single root cause, not 10 distinct failures.

- [ ] **Step 1.3: Implement `_compute_swing_pivots` in `tools_perception.py`**

Append at module level near end of file (right before any future `get_price_pivots`). `tools_perception.py:1` already has `from __future__ import annotations`, so type hints can use `pd.DataFrame` directly without quoting.

```python
# === Iter 3 — get_price_pivots helpers ===

def _compute_swing_pivots(
    df: pd.DataFrame, n: int = 5
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (highs, lows) where each entry is (bars_ago, price).

    Williams fractal with strict inequality: center bar's high must be strictly
    greater than all 2n surrounding bars' highs (and similarly low strictly less).
    Equality at any neighbor disqualifies the pivot — prevents flat-plateau false
    signals. Confirmed pivots only — last n bars excluded due to incomplete
    right window, so min returned bars_ago = n.
    """
    if len(df) < 2 * n + 1:
        return [], []

    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    last_idx = len(df) - 1
    confirm_end = last_idx - n

    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(n, confirm_end + 1):
        center_h = h[i]
        center_l = l[i]
        is_high = all(center_h > h[i + d] for d in range(-n, n + 1) if d != 0)
        is_low = all(center_l < l[i + d] for d in range(-n, n + 1) if d != 0)
        if is_high:
            highs.append((last_idx - i, float(center_h)))
        if is_low:
            lows.append((last_idx - i, float(center_l)))
    return highs, lows
```

(`pandas as pd` is already imported at the top of `tools_perception.py` — confirm with grep before writing if uncertain.)

- [ ] **Step 1.4: Run tests, verify all 10 pass**

Run: `pytest tests/test_price_pivots.py -v`
Expected: 10 passed.

- [ ] **Step 1.5: Commit**

```bash
git add tests/test_price_pivots.py src/agent/tools_perception.py
git commit -m "feat(iter3): Williams fractal _compute_swing_pivots helper + algorithm tests"
```

---

## Task 2: Renderer + prior-period helper — `_render_pivot_rows`, `_bars_ago_fmt`, `_get_prior_period_hl`

**Spec ref:** §4.2, §4.3, §5.2.

This task implements **two** helpers together: the renderer (which the spec describes in §4.3) and the prior-period H/L extractor (§4.2). Per spec §5.2, the prior-period helper's three states (`ok` / `insufficient` / `unavailable`) are exercised through fixture injection in the render-layer tests below — no dedicated unit-test block.

**Files:**
- Modify: `tests/test_price_pivots.py` (append)
- Modify: `src/agent/tools_perception.py` (append helpers)

- [ ] **Step 2.1: Append failing render-layer tests**

Add to `tests/test_price_pivots.py`:

```python
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
```

- [ ] **Step 2.2: Run tests, verify pytest collection still fails**

Run: `pytest tests/test_price_pivots.py -v`
Expected: pytest collection fails with `ImportError: cannot import name '_render_pivot_rows'` (or `_bars_ago_fmt`) — same module-import failure mode as Step 1.2, until Step 2.3 lands.

- [ ] **Step 2.3: Implement `_get_prior_period_hl` + `_bars_ago_fmt` + `_render_pivot_rows`**

Append in `tools_perception.py` after `_compute_swing_pivots`. Order matters only for readability — `_render_pivot_rows` calls `_bars_ago_fmt`; `_get_prior_period_hl` is independent and used by `get_price_pivots` in Task 3.

```python
def _get_prior_period_hl(
    df_or_err: pd.DataFrame | Exception | None,
) -> tuple[str, float | None, float | None]:
    """Return (status, high, low). status one of 'ok' / 'insufficient' / 'unavailable'.

    Period label ('Daily' / 'Weekly' / 'Monthly') is bound by the caller in
    `_render_pivot_rows` when iterating the three period results — not needed here.
    """
    if isinstance(df_or_err, Exception):
        return "unavailable", None, None
    df = df_or_err
    if df is None or df.empty or len(df) < 2:
        return "insufficient", None, None
    prior = df.iloc[-2]
    return "ok", float(prior["high"]), float(prior["low"])


def _bars_ago_fmt(n: int) -> str:
    """0 → 'now' (defensive — confirmed pivots have min ago=N=5);
    1 → '1 bar ago'; N≥2 → 'N bars ago'."""
    if n == 0:
        return "now"
    if n == 1:
        return "1 bar ago"
    return f"{n} bars ago"


def _render_pivot_rows(
    swing_highs: list[tuple[int, float]],
    swing_lows: list[tuple[int, float]],
    prior_d: tuple[str, float | None, float | None],
    prior_w: tuple[str, float | None, float | None],
    prior_m: tuple[str, float | None, float | None],
    current_price: float,
) -> tuple[list[str], list[str], list[str]]:
    """Return (above_rows, below_rows, footer_lines).

    above/below already sorted by abs(distance%) ascending; footer collects
    insufficient/unavailable notices for priors that don't fit either group.
    Caller (`get_price_pivots`) handles swing_status separately.
    """
    above: list[tuple[float, str]] = []
    below: list[tuple[float, str]] = []
    footer: list[str] = []

    for kind, items in (("Swing High", swing_highs), ("Swing Low", swing_lows)):
        for ago, price in items:
            dist_pct = (price - current_price) / current_price * 100
            line = f"{kind}: {price:,.2f} ({dist_pct:+.2f}%, {_bars_ago_fmt(ago)})"
            target = above if price > current_price else below
            target.append((abs(dist_pct), line))

    for label, (status, h, l_) in [
        ("Daily", prior_d), ("Weekly", prior_w), ("Monthly", prior_m),
    ]:
        if status == "ok":
            for kind, value in [("H", h), ("L", l_)]:
                dist_pct = (value - current_price) / current_price * 100
                line = f"Prior {label} {kind}: {value:,.2f} ({dist_pct:+.2f}%)"
                target = above if value > current_price else below
                target.append((abs(dist_pct), line))
        else:
            note = "insufficient data" if status == "insufficient" else "temporarily unavailable"
            footer.append(f"Prior {label} H/L: {note}")

    above.sort(key=lambda x: x[0])
    below.sort(key=lambda x: x[0])
    return [line for _, line in above], [line for _, line in below], footer
```

- [ ] **Step 2.4: Run tests, verify pass**

Run: `pytest tests/test_price_pivots.py -v`
Expected: 23 passed (10 from Task 1 + 13 new — 3 bars_ago_fmt + 10 render).

- [ ] **Step 2.5: Commit**

```bash
git add tests/test_price_pivots.py src/agent/tools_perception.py
git commit -m "feat(iter3): _render_pivot_rows + _bars_ago_fmt + _get_prior_period_hl helpers"
```

---

## Task 3: Tool body — `get_price_pivots` + 6 degradation tests

**Spec ref:** §3.2, §3.3, §4.4, §5.3, §5.5.

**Files:**
- Modify: `tests/test_price_pivots.py` (append degradation tests)
- Modify: `src/agent/tools_perception.py` (add tool function)

- [ ] **Step 3.1: Append 6 failing degradation/integration tests**

Add to `tests/test_price_pivots.py`:

```python
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

from src.agent.tools_perception import get_price_pivots
from src.integrations.exchange.base import Ticker


@dataclass
class _PivotsDeps:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "5m"
    market_data: AsyncMock = field(default_factory=AsyncMock)


def _ticker(price: float = 66523.40) -> Ticker:
    return Ticker(symbol="BTC/USDT:USDT", last=price, bid=price - 0.5, ask=price + 0.5, timestamp=0)


def _df_n_bars(n: int, *, base: float = 66000.0, with_pivots: bool = False) -> pd.DataFrame:
    """Build n-bar OHLCV df. with_pivots=True inserts one swing high + low for testing."""
    highs = [base + i * 0.1 for i in range(n)]
    lows = [base - 100.0 + i * 0.1 for i in range(n)]
    if with_pivots and n >= 30:
        highs[15] = base + 1000.0  # swing high
        lows[20] = base - 1000.0   # swing low
    return _df(highs, lows)


def _ohlcv_side_effect(by_tf: dict):
    """Build a side_effect function for get_ohlcv_dataframe(symbol, timeframe, limit=...)."""
    async def _impl(symbol, timeframe, limit=None):
        result = by_tf.get(timeframe)
        if isinstance(result, Exception):
            raise result
        return result
    return _impl


@pytest.mark.asyncio
async def test_pivots_ticker_failure_short_circuits():
    """ticker fetch raises → whole tool returns single-line unavailable; OHLCV not called."""
    deps = _PivotsDeps()
    deps.market_data.get_ticker = AsyncMock(side_effect=Exception("ticker down"))
    deps.market_data.get_ohlcv_dataframe = AsyncMock()

    out = await get_price_pivots(deps)

    assert out == "Price pivots (BTC/USDT:USDT, main TF: 5m): temporarily unavailable"
    deps.market_data.get_ohlcv_dataframe.assert_not_called()


@pytest.mark.asyncio
async def test_pivots_main_tf_error_three_priors_ok():
    """main TF raises; 3 priors ok → swing_status footer 'temporarily unavailable'; above/below contain prior rows."""
    deps = _PivotsDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_ticker())
    daily = _df([67234.0, 67100.0], [65500.0, 65400.0])
    weekly = _df([68500.0, 68400.0], [64200.0, 64100.0])
    monthly = _df([71200.0, 71100.0], [60800.0, 60700.0])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv_side_effect({
        "5m": Exception("main tf down"),
        "1d": daily, "1w": weekly, "1M": monthly,
    }))

    out = await get_price_pivots(deps)

    assert "Swing pivots: temporarily unavailable" in out
    assert "Prior Daily H: 67,234.00" in out
    assert "Prior Weekly L: 64,200.00" in out
    assert "(none)" not in out  # priors fill above/below


@pytest.mark.asyncio
async def test_pivots_short_window_with_insufficient_priors():
    """50 bar main TF (with pivots) + all priors len<2 → window-note + 3 insufficient footers."""
    deps = _PivotsDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_ticker())
    main_df = _df_n_bars(50, with_pivots=True)
    short_df = _df([100.0], [99.0])  # len 1 → insufficient
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv_side_effect({
        "5m": main_df, "1d": short_df, "1w": short_df, "1M": short_df,
    }))

    out = await get_price_pivots(deps)

    assert "(Window: 50 bars, less than 100)" in out
    # Has at least one swing row (with_pivots=True)
    assert "Swing High" in out or "Swing Low" in out
    # Three priors → all insufficient footer lines
    assert out.count("insufficient data") == 3
    assert "Prior Daily H/L: insufficient data" in out
    assert "Prior Weekly H/L: insufficient data" in out
    assert "Prior Monthly H/L: insufficient data" in out


@pytest.mark.asyncio
async def test_pivots_short_window_with_prior_exceptions():
    """50 bar main TF + 3 priors raise → window-note + 3 unavailable footers (separate from #3 path)."""
    deps = _PivotsDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_ticker())
    main_df = _df_n_bars(50, with_pivots=True)
    err = RuntimeError("api glitch")
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv_side_effect({
        "5m": main_df, "1d": err, "1w": err, "1M": err,
    }))

    out = await get_price_pivots(deps)

    assert "(Window: 50 bars, less than 100)" in out
    assert out.count("temporarily unavailable") == 3
    assert "Prior Daily H/L: temporarily unavailable" in out


@pytest.mark.asyncio
async def test_pivots_main_tf_empty_with_prior_exceptions():
    """main TF df.empty + 3 priors raise → swing insufficient + above/below '(none)' + 3 unavailable footers."""
    deps = _PivotsDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_ticker())
    empty_df = _df([], [])
    err = RuntimeError("down")
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv_side_effect({
        "5m": empty_df, "1d": err, "1w": err, "1M": err,
    }))

    out = await get_price_pivots(deps)

    assert "Swing pivots: insufficient data (need 11+ bars, got 0)" in out
    # above/below sections still rendered, but content is (none)
    assert out.count("(none)") == 2
    assert out.count("Prior Daily H/L: temporarily unavailable") == 1
    assert out.count("Prior Weekly H/L: temporarily unavailable") == 1
    assert out.count("Prior Monthly H/L: temporarily unavailable") == 1


@pytest.mark.asyncio
async def test_pivots_full_main_tf_one_prior_failure_spacing():
    """100 bar main TF + 2 priors ok + 1 prior fails → swing_status None,
    spacing branch in §4.4 ('if prior_footer and not swing_status:' inserts blank line) verified."""
    deps = _PivotsDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_ticker())
    main_df = _df_n_bars(100, with_pivots=True)
    daily = _df([67234.0, 67100.0], [65500.0, 65400.0])
    monthly = _df([71200.0, 71100.0], [60800.0, 60700.0])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv_side_effect({
        "5m": main_df,
        "1d": daily,
        "1w": Exception("weekly down"),
        "1M": monthly,
    }))

    out = await get_price_pivots(deps)

    # Swing rows present (no insufficient/unavailable swing_status line)
    assert "Swing pivots:" not in out
    # Daily + Monthly priors in above/below
    assert "Prior Daily H" in out
    assert "Prior Monthly H" in out
    # Only weekly in footer
    assert "Prior Weekly H/L: temporarily unavailable" in out
    assert "Prior Daily H/L: temporarily unavailable" not in out
    # Spacing: blank line precedes the weekly footer (footer not glued to below rows)
    lines = out.split("\n")
    weekly_idx = next(i for i, l in enumerate(lines) if "Prior Weekly H/L: temporarily unavailable" in l)
    assert lines[weekly_idx - 1] == "", \
        f"Expected blank line before weekly footer, got {lines[weekly_idx - 1]!r}"
```

- [ ] **Step 3.2: Run tests, verify pytest collection fails**

Run: `pytest tests/test_price_pivots.py -v`
Expected: pytest collection fails with `ImportError: cannot import name 'get_price_pivots' from 'src.agent.tools_perception'` — same module-import failure mode as prior tasks.

- [ ] **Step 3.3: Implement `get_price_pivots` in `tools_perception.py`**

Append after the helpers (still module-level, no decorator):

```python
async def get_price_pivots(deps: TradingDeps) -> str:
    """Show structural support/resistance: last 100 main-TF swing pivots
    (Williams fractal N=5) + prior daily/weekly/monthly H/L. Fact-only.

    Returns:
        Levels grouped by 'above current price' / 'below current price';
        within each group, sorted by absolute distance ascending. Swing
        rows include 'N bars ago'; prior rows label the period.

    Degradation: per-source three-state (fact / insufficient data /
        temporarily unavailable). Ticker failure → whole tool unavailable
        (no baseline price); main-TF failure → swing section degrades only;
        per-prior failure → only that row degrades.
    """
    import asyncio  # local import — matches existing convention (e.g. tools_perception.py:1320)

    symbol = deps.symbol
    main_tf = deps.timeframe

    try:
        ticker = await deps.market_data.get_ticker(symbol)
        current_price = ticker.last
    except Exception:
        logger.exception("get_price_pivots ticker fetch failed for %s", symbol)
        return f"Price pivots ({symbol}, main TF: {main_tf}): temporarily unavailable"

    async def _fetch(tf: str, limit: int):
        try:
            return await deps.market_data.get_ohlcv_dataframe(symbol, tf, limit=limit)
        except Exception as e:
            return e

    main_df_or_err, daily_or_err, weekly_or_err, monthly_or_err = await asyncio.gather(
        _fetch(main_tf, 100),
        _fetch("1d", 2),
        _fetch("1w", 2),
        _fetch("1M", 2),
    )

    swing_status: str | None = None
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    if isinstance(main_df_or_err, Exception):
        swing_status = "Swing pivots: temporarily unavailable"
    elif main_df_or_err is None or main_df_or_err.empty or len(main_df_or_err) < 11:
        got_bars = 0 if (main_df_or_err is None or main_df_or_err.empty) else len(main_df_or_err)
        swing_status = f"Swing pivots: insufficient data (need 11+ bars, got {got_bars})"
    else:
        bar_count = len(main_df_or_err)
        swing_highs, swing_lows = _compute_swing_pivots(main_df_or_err, n=5)
        no_pivot = not swing_highs and not swing_lows
        if no_pivot and bar_count >= 100:
            swing_status = "(No swing pivots in 100-bar window)"
        elif no_pivot and bar_count < 100:
            swing_status = f"(Window: {bar_count} bars, less than 100 — no swing pivots found)"
        elif bar_count < 100:
            swing_status = f"(Window: {bar_count} bars, less than 100)"
        # else: 100 bars + ≥1 pivot → swing_status stays None

    prior_d = _get_prior_period_hl(daily_or_err)
    prior_w = _get_prior_period_hl(weekly_or_err)
    prior_m = _get_prior_period_hl(monthly_or_err)

    above_rows, below_rows, prior_footer = _render_pivot_rows(
        swing_highs, swing_lows, prior_d, prior_w, prior_m, current_price,
    )

    sections: list[str] = [
        f"=== Price Pivots ({symbol}, main TF: {main_tf}) ===",
        f"Current Price: {current_price:,.2f}",
        "",
        "=== Levels Above Current Price ===",
        *(above_rows or ["(none)"]),
        "",
        "=== Levels Below Current Price ===",
        *(below_rows or ["(none)"]),
    ]
    if swing_status:
        sections.append("")
        sections.append(swing_status)
    if prior_footer:
        if not swing_status:
            sections.append("")
        sections.extend(prior_footer)
    return "\n".join(sections)
```

(Verify `logger` and `TradingDeps` are already imported at top of `tools_perception.py` — they are, used by all 18 existing perception tools.)

- [ ] **Step 3.4: Run tests, verify pass**

Run: `pytest tests/test_price_pivots.py -v`
Expected: 29 passed (10 + 13 + 6).

- [ ] **Step 3.5: Commit**

```bash
git add tests/test_price_pivots.py src/agent/tools_perception.py
git commit -m "feat(iter3): get_price_pivots tool body + degradation tests"
```

---

## Task 4: Wire `@agent.tool` thin wrapper + REGISTERED_TOOL_NAMES drift

**Spec ref:** §1.3, §2.1, §4.6.

**Files:**
- Modify: `src/agent/trader.py:202-252` (insert wrapper after `get_multi_timeframe_snapshot` at line 252; before `# === Execution Tools ===` at line 254)
- Modify: `src/agent/trader.py:371-390` (add `"get_price_pivots"` to perception block of `REGISTERED_TOOL_NAMES`)
- Modify: `tests/test_trader_agent.py:84-86` (29 → 30; comment 18+10+1 → 19+10+1)

- [ ] **Step 4.1: Update drift test first (test will fail until impl is done)**

In `tests/test_trader_agent.py:84-86`, change:

```python
    assert len(REGISTERED_TOOL_NAMES) == 29, (
        f"Expected 29 tools (18+10+1), got {len(REGISTERED_TOOL_NAMES)}"
    )
```

to:

```python
    assert len(REGISTERED_TOOL_NAMES) == 30, (
        f"Expected 30 tools (19+10+1), got {len(REGISTERED_TOOL_NAMES)}"
    )
```

- [ ] **Step 4.2: Run drift test, verify it fails**

Run: `pytest tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools -v`
Expected: FAIL on the `len(REGISTERED_TOOL_NAMES) == 30` assertion (still 29 entries until Step 4.4). The earlier `actual == declared` set-equality assertion still passes at this point because the agent and the constant are both 29.

- [ ] **Step 4.3: Add `@agent.tool` thin wrapper in `trader.py`**

Insert after line 252 (the `get_multi_timeframe_snapshot` wrapper), before `# === Execution Tools ===` at line 254:

```python
    @agent.tool
    async def get_price_pivots(ctx: RunContext[TradingDeps]) -> str:
        """Show structural support/resistance: last 100 main-TF swing pivots
        (Williams fractal N=5) + prior daily/weekly/monthly H/L. Fact-only.
        Returns levels grouped by above/below current price, sorted by
        absolute distance. Swing rows annotate 'N bars ago'; prior rows
        label the period (Daily / Weekly / Monthly). See tool implementation
        for full degradation semantics.
        """
        from src.agent.tools_perception import get_price_pivots as _impl

        return await _impl(ctx.deps)
```

- [ ] **Step 4.4: Add `"get_price_pivots"` to `REGISTERED_TOOL_NAMES`**

In the perception block (currently ending at line 390 with `"get_multi_timeframe_snapshot"`), update the comment and append the new entry. Find:

```python
    # --- 感知 (18) ---
    "get_market_data",
    ...
    "get_multi_timeframe_snapshot",
```

Change comment to `# --- 感知 (19) ---` and append `"get_price_pivots",` after `"get_multi_timeframe_snapshot",`:

```python
    # --- 感知 (19) ---
    "get_market_data",
    ...
    "get_multi_timeframe_snapshot",
    "get_price_pivots",
```

- [ ] **Step 4.5: Run drift test, verify pass**

Run: `pytest tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools -v`
Expected: PASS.

- [ ] **Step 4.6: Run the full trader test module + a smoke run for the new tool**

Run: `pytest tests/test_trader_agent.py tests/test_price_pivots.py -v`
Expected: all green.

- [ ] **Step 4.7: Commit**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "feat(iter3): @agent.tool wrapper for get_price_pivots + REGISTERED_TOOL_NAMES drift sync"
```

---

## Task 5: Persona Layer 1 +1 bullet (24 → 25) + drift tests

**Spec ref:** §1.3, §4.5, §5.6.

**Files:**
- Modify: `tests/test_persona.py` (append 2 tests)
- Modify: `src/agent/persona.py:49` (append bullet inside `_build_layer1` triple-string before closing `"""`)

- [ ] **Step 5.1: Append the 2 failing persona drift tests**

Add to `tests/test_persona.py`:

```python
def test_layer1_bullet_count_25():
    """Layer 1 bullet count drift guard (Iter 3: 24 → 25). Bullets are markdown
    rows starting with '\\n- **' — matches `_build_layer1`'s tools-section format.
    """
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig()
    prompt = generate_system_prompt(config)
    # Guard: Layer 2 header — protects against silent false-pass if persona.py renames it
    assert "## How to Think" in prompt, \
        "Layer 2 header changed; update split key in this test"
    layer1 = prompt.split("## How to Think")[0]
    bullet_count = layer1.count("\n- **")
    assert bullet_count == 25, f"Expected 25 Layer 1 bullets, got {bullet_count}"


def test_layer1_includes_get_price_pivots():
    """Iter 3 Layer 1 bullet describes get_price_pivots with key terminology
    (fractal / structural / above / below)."""
    from src.agent.persona import generate_system_prompt
    config = PersonaConfig()
    prompt = generate_system_prompt(config)
    assert "get_price_pivots" in prompt
    assert "## How to Think" in prompt, \
        "Layer 2 header changed; update split key in this test"
    layer1 = prompt.split("## How to Think")[0]
    for keyword in ("fractal", "structural", "above", "below"):
        assert keyword in layer1.lower(), \
            f"Layer 1 bullet missing keyword '{keyword}'"
```

- [ ] **Step 5.2: Run tests, verify they fail**

Run: `pytest tests/test_persona.py::test_layer1_bullet_count_25 tests/test_persona.py::test_layer1_includes_get_price_pivots -v`
Expected: FAIL — bullet count 24 (not 25); `get_price_pivots` not in prompt.

- [ ] **Step 5.3: Append the new bullet to `_build_layer1` in `persona.py`**

In `src/agent/persona.py`, find the OCO atomicity bullet (currently at line 49, the last bullet before the closing `"""`):

```python
- **OCO atomicity on OKX**: stop and take_profit orders that share an algoId (rendered as `[OCO]` in get_open_orders) are atomic — cancelling or triggering one leg removes both. If you intend to replace only one leg, re-create the other leg immediately after."""
```

Replace with (append new bullet before the closing `"""`):

```python
- **OCO atomicity on OKX**: stop and take_profit orders that share an algoId (rendered as `[OCO]` in get_open_orders) are atomic — cancelling or triggering one leg removes both. If you intend to replace only one leg, re-create the other leg immediately after.
- **Price pivots**: Use get_price_pivots to scan structural levels — swing highs/lows from the last 100 main-TF bars (Williams fractal N=5) plus prior daily/weekly/monthly H/L. Levels are grouped above/below current price with distance % and bars-ago. Useful for placing SL/TP at structural levels rather than arbitrary percentages."""
```

(Keywords required by Step 5.1: `fractal`, `structural`, `above`, `below` — all present.)

- [ ] **Step 5.4: Run the new persona tests + the full persona module**

Run: `pytest tests/test_persona.py -v`
Expected: all green (existing tests untouched + 2 new pass).

- [ ] **Step 5.5: Commit**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "feat(iter3): persona Layer 1 bullet for get_price_pivots + drift tests"
```

---

## Task 6: Fact-only regression (1 test, 5 scenarios) + MockDeps `timeframe` field

**Spec ref:** §3.4, §5.4.

**Files:**
- Modify: `tests/test_fact_only_wordlist.py` (add `timeframe` field to `MockDeps`; add `PIVOTS_BANNED_WORDS` per-tool wordlist; add `test_get_price_pivots_fact_only_5_scenarios`)

- [ ] **Step 6.1: Extend `MockDeps` with `timeframe` field**

In `tests/test_fact_only_wordlist.py`, find `MockDeps` (line 35-41):

```python
@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    initial_balance: float = 10000.0
    exchange: AsyncMock = field(default_factory=AsyncMock)
    market_data: AsyncMock = field(default_factory=AsyncMock)
    technical: AsyncMock = field(default_factory=AsyncMock)
```

Append one line for `timeframe`:

```python
@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    initial_balance: float = 10000.0
    exchange: AsyncMock = field(default_factory=AsyncMock)
    market_data: AsyncMock = field(default_factory=AsyncMock)
    technical: AsyncMock = field(default_factory=AsyncMock)
    timeframe: str = "5m"  # Iter 3: get_price_pivots reads deps.timeframe
```

(Existing 4 fact-only tests don't read `timeframe` → unaffected.)

- [ ] **Step 6.2: Append the failing fact-only test**

Append at the end of `tests/test_fact_only_wordlist.py`:

```python
import pandas as pd

PIVOTS_BANNED_WORDS = (
    # Strength
    "strong", "weak", "strongly", "weakly",
    # Importance
    "important", "unimportant", "key", "major", "minor",
    "critical", "crucial", "significant", "insignificant",
    # Sentiment (inherited from global, listed here so this test does not
    # depend on the global wordlist — see plan §5.4 wordlist scope decision)
    "bullish", "bearish",
    # Iter 3 §1.2 non-goals — guard against future regressions producing them
    "broken", "breached",
)
PIVOTS_BANNED_RE = re.compile(
    r"\b(" + "|".join(PIVOTS_BANNED_WORDS) + r")\b", re.IGNORECASE,
)


def _pivots_df(highs, lows):
    n = len(highs)
    return pd.DataFrame({
        "open": highs, "high": highs, "low": lows, "close": highs,
        "volume": [1.0] * n,
    })


def _pivots_ticker():
    return Ticker(symbol="BTC/USDT:USDT", last=66523.40, bid=66523.0, ask=66524.0, timestamp=0)


def _pivots_ohlcv_side_effect(by_tf):
    async def _impl(symbol, timeframe, limit=None):
        result = by_tf.get(timeframe)
        if isinstance(result, Exception):
            raise result
        return result
    return _impl


def _build_normal_deps() -> MockDeps:
    """100 bar main TF with explicit pivots + 3 priors ok."""
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    highs = [66000.0 + i * 0.1 for i in range(100)]
    lows = [65900.0 + i * 0.1 for i in range(100)]
    highs[15] = 67500.0  # swing high
    lows[20] = 64500.0   # swing low
    main_df = _pivots_df(highs, lows)
    daily = _pivots_df([67234.0, 67100.0], [65500.0, 65400.0])
    weekly = _pivots_df([68500.0, 68400.0], [64200.0, 64100.0])
    monthly = _pivots_df([71200.0, 71100.0], [60800.0, 60700.0])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": main_df, "1d": daily, "1w": weekly, "1M": monthly,
    }))
    return deps


def _build_monotonic_uptrend_deps() -> MockDeps:
    """100 bar strictly increasing → no swing pivots; 3 priors ok."""
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    main_df = _pivots_df([66000.0 + i for i in range(100)], [65900.0 + i for i in range(100)])
    daily = _pivots_df([67234.0, 67100.0], [65500.0, 65400.0])
    weekly = _pivots_df([68500.0, 68400.0], [64200.0, 64100.0])
    monthly = _pivots_df([71200.0, 71100.0], [60800.0, 60700.0])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": main_df, "1d": daily, "1w": weekly, "1M": monthly,
    }))
    return deps


def _build_50bar_with_insufficient_prior_deps() -> MockDeps:
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    highs = [66000.0 + i * 0.1 for i in range(50)]
    lows = [65900.0 + i * 0.1 for i in range(50)]
    highs[15] = 67500.0
    lows[20] = 64500.0
    main_df = _pivots_df(highs, lows)
    short_df = _pivots_df([100.0], [99.0])  # len 1 → insufficient
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": main_df, "1d": short_df, "1w": short_df, "1M": short_df,
    }))
    return deps


def _build_main_tf_error_with_prior_ok_deps() -> MockDeps:
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    daily = _pivots_df([67234.0, 67100.0], [65500.0, 65400.0])
    weekly = _pivots_df([68500.0, 68400.0], [64200.0, 64100.0])
    monthly = _pivots_df([71200.0, 71100.0], [60800.0, 60700.0])
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": Exception("main tf down"),
        "1d": daily, "1w": weekly, "1M": monthly,
    }))
    return deps


def _build_main_tf_empty_with_prior_error_deps() -> MockDeps:
    deps = MockDeps()
    deps.market_data.get_ticker = AsyncMock(return_value=_pivots_ticker())
    err = RuntimeError("api down")
    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_pivots_ohlcv_side_effect({
        "5m": _pivots_df([], []),
        "1d": err, "1w": err, "1M": err,
    }))
    return deps


@pytest.mark.asyncio
async def test_get_price_pivots_fact_only_5_scenarios():
    """Normal / swing-empty / short-window / main-TF-error / all-prior-error
    — none of the 5 scenarios may emit any PIVOTS_BANNED_WORDS."""
    from src.agent.tools_perception import get_price_pivots

    scenarios = [
        ("normal_full", _build_normal_deps()),
        ("swing_empty", _build_monotonic_uptrend_deps()),
        ("short_window", _build_50bar_with_insufficient_prior_deps()),
        ("main_tf_error", _build_main_tf_error_with_prior_ok_deps()),
        ("all_prior_error", _build_main_tf_empty_with_prior_error_deps()),
    ]
    for name, deps in scenarios:
        output = await get_price_pivots(deps)
        matches = PIVOTS_BANNED_RE.findall(output)
        assert not matches, f"Banned words in scenario '{name}': {matches}\n--- output ---\n{output}"
```

- [ ] **Step 6.3: Run the fact-only test**

Run: `pytest tests/test_fact_only_wordlist.py::test_get_price_pivots_fact_only_5_scenarios -v`
Expected: PASS (output text in all 5 scenarios contains no banned words by design — current implementation only emits structural labels like `Swing High` / `Prior Daily H` / `(none)` / `temporarily unavailable`).

- [ ] **Step 6.4: Run all fact-only tests to confirm `MockDeps.timeframe` did not break existing 4 tests**

Run: `pytest tests/test_fact_only_wordlist.py -v`
Expected: 5 tests pass (4 existing + 1 new).

- [ ] **Step 6.5: Commit**

```bash
git add tests/test_fact_only_wordlist.py
git commit -m "test(iter3): get_price_pivots fact-only regression (5 scenarios) + MockDeps timeframe field"
```

---

## Task 7: Full suite verification + token-budget spot check

**Spec ref:** §6 acceptance items 8 + 9.

**Files:** none modified — verification only.

- [ ] **Step 7.1: Run full test suite**

Run: `pytest -x -q`
Expected: ≈818 passed, 1 skipped, 0 failed (786 baseline + 32 new). Spec §6 #8 anchors +29; the +3 over the spec contract is the three `_bars_ago_fmt` micro-helper tests added in Task 2.

If a non-pivots test breaks: investigate (likely a `MockDeps` consumer found `timeframe` unexpectedly — Task 6's `MockDeps` change is local to `tests/test_fact_only_wordlist.py`, so cross-file impact should be zero; root-cause before adjusting).

- [ ] **Step 7.2: Token-budget spot check (acceptance #9 — `≤ 800 tokens` full-load)**

Run an ad-hoc python snippet to estimate token count from the `_build_normal_deps()` fixture:

```bash
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from tests.test_fact_only_wordlist import _build_normal_deps
from src.agent.tools_perception import get_price_pivots

async def main():
    deps = _build_normal_deps()
    out = await get_price_pivots(deps)
    print(out)
    print('---')
    print('chars:', len(out))
    print('approx tokens (chars/4):', len(out) // 4)

asyncio.run(main())
"
```

Expected: chars/4 estimate ≈ 600-800. If meaningfully over 800, log the exact count in the commit body — observation period decides whether a cap is needed (spec §1.2 + §8.1 explicitly defer this).

- [ ] **Step 7.3: Persona prompt sanity check**

Run: `python -c "from src.agent.persona import generate_system_prompt; from src.config import PersonaConfig; p = generate_system_prompt(PersonaConfig()); print(p[:200]); print('...'); print('LAYER1 BULLETS:', p.split(chr(10)+chr(35)+chr(35)+' How to Think')[0].count(chr(10)+'- **'))"`
Expected: prints `LAYER1 BULLETS: 25`.

- [ ] **Step 7.4: Verify branch state and final commit graph**

Run: `git log --oneline iter3-price-pivots-spec ^main`
Expected: 7 commits (1 spec already on branch + 6 impl commits from Tasks 1-6).

- [ ] **Step 7.5: No commit needed — task is verification only.**

---

## Self-Review

**1. Spec coverage:**

| Spec section | Implemented in |
|--------------|----------------|
| §1.1 #1 (`@agent.tool` no params) | Task 4 |
| §1.1 #2 (above/below grouping + sort) | Task 2 (renderer) |
| §1.1 #3 (swing has bars ago, prior doesn't) | Task 2 |
| §1.1 #4 (per-source independent degradation) | Task 3 |
| §1.1 #5 (Layer 1 +1 bullet + REGISTERED) | Tasks 4, 5 |
| §1.1 #6 (test counts: 10/10/6/1/2) | Tasks 1, 2, 3, 5, 6 |
| §2.2 all edge cases (5 scenarios) | Tasks 3 + 6 |
| §3.2 4-way concurrent fetch + ticker serial | Task 3 |
| §3.3 three-state contract per layer | Tasks 3, 6 |
| §3.4 fact-only with PIVOTS_BANNED_WORDS | Task 6 |
| §4.1 strict-inequality fractal | Task 1 |
| §4.2 prior period helper | Task 2 (impl alongside renderer; tests indirect via §5.2) |
| §4.3 renderer + bars_ago_fmt | Task 2 |
| §4.4 main body assembly + spacing | Task 3 |
| §4.5 persona Layer 1 bullet | Task 5 |
| §4.6 REGISTERED_TOOL_NAMES drift sync | Task 4 |
| §5.1 algorithm tests (10) | Task 1 |
| §5.2 render tests (10 + 3 bars_ago) — covers prior helper indirectly | Task 2 |
| §5.3 degradation tests (6) | Task 3 |
| §5.4 fact-only test (1 × 5 scenarios) + MockDeps | Task 6 |
| §5.6 persona drift (2 tests) | Task 5 |
| §6 acceptance #8 (no regression) | Task 7 |
| §6 acceptance #9 (token cap) | Task 7.2 |
| §6 acceptance #10 (strict ineq) | Task 1 (tests 2, 3) |

No gaps.

**2. Placeholder scan:** No `TBD`, `TODO`, `implement later`, `add appropriate error handling`, or "similar to Task N". All code blocks are complete.

**3. Type / signature consistency:**
- `_compute_swing_pivots(df, n=5) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]` — same signature in Task 1 impl, Task 1 tests, Task 3 main body call. ✅
- `_get_prior_period_hl(df_or_err) -> tuple[str, float | None, float | None]` — consistent in Task 2 impl, Task 2 render tests' fixture tuple shape (`("ok"|"insufficient"|"unavailable", h_or_None, l_or_None)`), and Task 3 main body. ✅
- `_render_pivot_rows(swing_highs, swing_lows, prior_d, prior_w, prior_m, current_price) -> tuple[list[str], list[str], list[str]]` — same in Task 2 impl, Task 2 tests, Task 3 body. ✅
- `_bars_ago_fmt(n: int) -> str` — same Task 2 impl + tests. ✅
- Tool: `async def get_price_pivots(deps: TradingDeps) -> str` (impl) + `async def get_price_pivots(ctx: RunContext[TradingDeps]) -> str` (decorator wrapper) — matches existing 18 tools' two-file pattern. ✅
- `swing_status` states wording: literal strings in Task 3 impl (`"Swing pivots: temporarily unavailable"` / `"Swing pivots: insufficient data (need 11+ bars, got {got_bars})"` / `"(Window: {bar_count} bars, less than 100)"` / `"(Window: {bar_count} bars, less than 100 — no swing pivots found)"` / `"(No swing pivots in 100-bar window)"`) match Task 3 test assertions verbatim. ✅
- `MockDeps.timeframe: str = "5m"` matches `_PivotsDeps.timeframe: str = "5m"` in `tests/test_price_pivots.py`. ✅
- `REGISTERED_TOOL_NAMES` count `30` and comment `(19+10+1)` match Task 4 drift test update. ✅

---

**End of plan.**
