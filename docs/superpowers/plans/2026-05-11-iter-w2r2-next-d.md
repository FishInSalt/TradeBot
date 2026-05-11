# Iter w2r2-next-d Multi-TF Path Reversal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Path-reverse the three multi-timeframe perception tools (MTS / GMD / HTF) so the tool surface aligns with the agent's "multi-TF alignment" mental model — MTS becomes the cycle-opening primary, GMD retreats to single-TF depth, HTF becomes a list-form long-term structural view.

**Architecture:** Single PR on branch `iter-w2r2-next-d/multi-tf` with **8 ordered commits**: (1) docs, (2) shared helpers in new `src/utils/ohlcv_utils.py` + BB labels in `services/technical.py`, (3) HTF list-form + N6 G1-G5, (4) GMD changes, (5) MTS upgrade, (6) `get_price_pivots` label unification, (7) `get_position` Liquidation dedup, (8) cross-tool drift-guard tests. Layer-1 in `persona.py` is **intentionally untouched** — cross-tool routing lives in wrapper docstrings via pydantic-ai's griffe sniff.

**Tech Stack:** Python 3.12 + `pydantic-ai` (Agent.tool google-format docstrings, griffe sniff) + `pandas` / `pandas_ta` (technical indicators) + `pytest-asyncio` (test runner).

**Spec reference:** `docs/superpowers/specs/2026-05-11-iter-w2r2-next-d-multi-tf-design.md`. Read the spec section noted at the top of each task before starting that task.

**Branch / baseline:** Working tree currently at `iter-w2r2-next-d/multi-tf` @ `06b1e6d` ("docs(iter-w2r2-next-d): multi-tf path-reversal spec + OHLCV semantics verification scripts"). Baseline tests: **1487 collected** (5 skip) as of 2026-05-11 per `CLAUDE.md`. Every commit MUST keep this total non-decreasing; new tests are additive.

---

## Conventions

- **TDD discipline (rigid):** Each behavior-bearing task is a Red-Green-Commit micro-loop — write a failing test first, run it to confirm RED, then implement minimal code to GREEN, then commit.
- **Commit messages:** Use the conventional-commits prefix from `git log` (`docs:` / `refactor:` / `feat:` / `chore:` / `test:`). Every commit message ends with the `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer (per CLAUDE.md `git commit` workflow). Per `feedback_no_pr_comment`, do NOT post to GitHub during plan execution.
- **Number formatting:** All numeric output across MTS / GMD / HTF / get_price_pivots `Last:` uses **no thousand separator** (`81870.50`, not `81,870.50`). Decimal precision 2dp for prices/MA, 2dp for ATR percentages and ratios, 1dp for volume (spec §6.5). The Position section keeps its existing `:,.2f` style — only the four perception headers unify.
- **Closed-only series convention:** Indicator inputs are `_closed_bars(df)` (= `df.iloc[:-1]`) at the call site, not globally inside `compute_indicators` (spec §6.4.1).
- **Live-price convention:** `ticker.last` via `_live_price(ticker)` is the canonical live-state source (spec §6.4); empirical anchor: `verify_ohlcv_semantics_v2.py` A1 sub-bps drift floor.
- **No Layer-1 changes:** Plan never edits `src/agent/persona.py` Layer-1 prompt (spec §6.1 — drift-guards `test_layer1_cross_tool_bullet_count` + `test_layer1_no_tool_invocation_descriptions`).
- **Test runner:** All `pytest` commands assume the project venv is active. Use `python -m pytest` if `pytest` is not on `PATH`. Verbose flag `-v` is recommended for the per-task runs below.
- **Reproducible OHLCV fixtures:** New shared fixture module `tests/fixtures/multi_tf_ohlcv.py` (created in Task 2) hosts the OHLCV DataFrames consumed by tasks 2/3/4/5/8. Keep all fixture price scales consistent (≈ 81000 USDT for BTC/USDT:USDT, matching `verify_ohlcv_semantics_v2.py` data).

---

## File Structure

### New files

- `src/utils/ohlcv_utils.py` — Shared OHLCV helpers consumed by MTS / GMD / HTF: `_live_price`, `_closed_bars`, `_atr_series`. Three primitives carrying one design decision each (canonical live-price source / closed-only strip / `mamode="rma"` algorithm lock). API frozen at Task 2 (commit 2); tasks 3-5 import without modification.
- `tests/test_ohlcv_utils.py` — Unit tests for the three helpers (closed-only stripping, live-price wrapper, ATR-series equivalence to `compute_indicators["atr_14"]`).
- `tests/fixtures/multi_tf_ohlcv.py` — Pytest fixtures for hand-crafted OHLCV DataFrames (BTC/USDT:USDT-scaled) shared by HTF/GMD/MTS golden-mockup tests and the cross-tool drift-guard tests. **Includes** `pytest.fixture` factories returning DataFrames with deterministic timestamps and prices.
- `tests/test_multi_tf_drift_guards.py` — The 6 cross-tool drift-guard invariants from spec §7.1.
- `tests/test_iter_w2r2_next_d_goldens.py` — Golden-mockup tests per tool (one section per tool: HTF, GMD, MTS).

### Modified files

- `src/services/technical.py` — `format_for_llm` BB label rewrite (F-O2) only. `compute_indicators` signature unchanged.
- `src/agent/tools_perception.py` — Three tool function bodies rewritten:
  - `get_market_data` (lines 39-136): default `candle_count` 50→30, B3 anomaly markers, B4 period summary, closed-only indicator inputs, header label rewrites.
  - `get_higher_timeframe_view` (lines 849-934): signature `timeframe` → `timeframes: list[Literal[...]]`; per-tf section layout with N6 G1-G5; closed-only inputs.
  - `get_multi_timeframe_snapshot` (lines 1423-1529): full output rewrite per §3.1 mockup.
  - `get_position` (line 191): drop `Liquidation:` line from Position section (F-P2).
  - `get_price_pivots` (line 1709): `Current Price: V` → `Last: V` (label + thousand-separator removal).
- `src/agent/trader.py` — Three wrapper @tool function docstrings rewritten (matching §3.3 / §4.5 / §5.7) plus the "Related perception tools" tails (§6.1). HTF wrapper signature changes to `timeframes: list[Literal[...]] = ["4h", "1d"]`; GMD wrapper default `candle_count: int = 30`.
- `tests/test_perception_tools_n3.py` — All 14 HTF call sites migrated `timeframe="..."` → `timeframes=["..."]`; output-assertion strings rewritten to match the new list-form layout.
- `tests/test_fact_only_wordlist.py` line 555 — HTF positional call migrated.
- `tests/test_display_cycle.py` lines 393 / 1306 / 1316 / 1589 / 1607 / 1629 / 1649 / 1659 / 1687 / 1700 / 1731 / 1733 / 1744 / 1746 / 1775 — `Price:` / `Current Price:` / `Current price:` literals updated to `Last:` (or `Last (ticker @ ...):`) per §6.3.
- `tests/test_toolkit_iter2.py` lines 306 / 372 — MTS `Current price:` assertion → `Last (ticker @ ...):` regex (use `re.search`, not `in`).

### Files explicitly NOT modified

- `src/agent/persona.py` — Layer-1 untouched (spec §6.1).
- `src/services/technical.py compute_indicators` — global behavior unchanged (spec §6.4.1).
- `src/agent/tools_perception.py get_position` Risk Exposure section's `Liquidation:` line at line 258/260 — only the duplicate at line 191 (Position section) is removed.

---

# Task 1 — Commit 1: spec + plan as docs commit

Spec ref: §8 commit plan. Per `feedback_plan_doc_commit_first`, the plan document is committed first as its own docs commit before any source change.

The spec was already landed via commit `06b1e6d`. This task adds this plan document as a separate docs commit.

**Files:**
- Create: `docs/superpowers/plans/2026-05-11-iter-w2r2-next-d.md` (this file — assumed already saved before Task 1 begins)

- [ ] **Step 1: Verify plan file exists and is staged-ready**

Run:

```bash
ls -la docs/superpowers/plans/2026-05-11-iter-w2r2-next-d.md
git status --short docs/superpowers/plans/
```

Expected: file exists; `git status` shows it as untracked or modified.

- [ ] **Step 2: Verify baseline test count**

Run:

```bash
python -m pytest --collect-only -q 2>&1 | tail -3
```

Expected: `1487 tests collected` (5 skip recorded in CLAUDE.md). Record the exact number — every subsequent task must keep this non-decreasing.

- [ ] **Step 3: Stage and commit the plan**

Run:

```bash
git add docs/superpowers/plans/2026-05-11-iter-w2r2-next-d.md
git commit -m "$(cat <<'EOF'
docs(iter-w2r2-next-d): implementation plan for multi-TF path reversal

Plan document covering the 8-commit PR sequence per spec §8, with
TDD task decomposition for the three-tool refactor (MTS / GMD / HTF),
shared helpers in src/utils/ohlcv_utils.py, and cross-tool drift-guard
tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Verify commit landed**

Run:

```bash
git log --oneline -1
```

Expected: most recent commit is the plan commit with message starting `docs(iter-w2r2-next-d): implementation plan for multi-TF path reversal`.

---

# Task 2 — Commit 2: shared helpers + BB labels

Spec ref: §6.3 F-O2; §6.4 helper module; §6.4.2 ATR series; §2.2.1 algorithm-lock invariant.

The new module `src/utils/ohlcv_utils.py` lands first so commits 3-5 can import it without redefining. F-O2 BB label rewrite ships in the same commit because it touches `services/technical.py`, which is the only file in this commit other than the new utils module.

**Files:**
- Create: `src/utils/ohlcv_utils.py`
- Create: `tests/test_ohlcv_utils.py`
- Verify exists (no creation needed): `tests/fixtures/__init__.py` (already present, empty)
- Create: `tests/fixtures/multi_tf_ohlcv.py`
- Modify: `src/services/technical.py` lines 95-116 (BB block in `format_for_llm`)
- Modify: `tests/test_display_cycle.py` line 393 only — BB label rewrite ripples into the existing snapshot that asserts `"BB: 81960 / 81727 / 81494 (position: 81% of band width)"` style; only the smallest visible change is touched in this commit, fuller display_cycle test sweep happens in Task 9 alongside the cross-tool unification.

## 2.1 Shared OHLCV fixtures

- [ ] **Step 1: Create the fixtures module**

`tests/fixtures/__init__.py` already exists (verify):

```bash
test -f tests/fixtures/__init__.py && echo OK || echo "MISSING — create empty file"
```

Expected: `OK`.

Then create `tests/fixtures/multi_tf_ohlcv.py` with hand-crafted DataFrames:

```python
"""Reproducible OHLCV fixtures for multi-TF tool tests.

Each builder returns a pandas DataFrame with columns
[timestamp, open, high, low, close, volume] where timestamp is
millisecond UTC. Prices scaled to BTC/USDT:USDT ≈ 81000 to match
the empirical anchor (verify_ohlcv_semantics_v2.py 2026-05-10).

The last row of every fixture represents an in-progress candle: tests
that call `_closed_bars(df)` MUST end up with one fewer row than the
raw fixture. Use this to verify closed-only stripping unambiguously.
"""
from __future__ import annotations
import pandas as pd
import pytest

_TF_MS = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000,
          "4h": 14_400_000, "1d": 86_400_000,
          "1w": 7 * 86_400_000, "1M": 30 * 86_400_000}


def _build(start_ms: int, tf: str, closes: list[float], base_vol: float = 100.0) -> pd.DataFrame:
    """Build a DataFrame where each candle has open=prev_close, close=closes[i],
    high=close+10, low=close-10, volume=base_vol. The final bar is treated as
    in-progress (timestamp = start_ms + (N-1)*tf_ms)."""
    step = _TF_MS[tf]
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        rows.append({
            "timestamp": start_ms + i * step,
            "open": prev,
            "high": max(prev, c) + 10.0,
            "low": min(prev, c) - 10.0,
            "close": c,
            "volume": base_vol,
        })
        prev = c
    return pd.DataFrame(rows)


@pytest.fixture
def df_4h_250bars() -> pd.DataFrame:
    """4h OHLCV with 250 closed bars + 1 in-progress (251 rows)."""
    # Monotonic upward drift from 75000 to 82000 across 251 bars
    closes = [75000.0 + i * (7000.0 / 250) for i in range(251)]
    return _build(start_ms=1_700_000_000_000, tf="4h", closes=closes)


@pytest.fixture
def df_1d_250bars() -> pd.DataFrame:
    """1d OHLCV with 250 closed bars + 1 in-progress (251 rows)."""
    closes = [55000.0 + i * (26000.0 / 250) for i in range(251)]
    return _build(start_ms=1_700_000_000_000, tf="1d", closes=closes)


@pytest.fixture
def df_5m_130bars() -> pd.DataFrame:
    """5m OHLCV with 129 closed bars + 1 in-progress (130 rows).

    130 rows is the minimum to satisfy GMD's default `candle_count=30`
    display window: `available_closed (129) >= candle_count + 50 (80)`
    must hold for `display_count = 30` to apply; otherwise GMD falls back
    to `max(10, available_closed - 50)` and the header reads "last N"
    with N < 30, breaking the golden test.
    """
    closes = [81000.0 + (i % 10) * 5.0 for i in range(130)]
    return _build(start_ms=1_700_000_000_000, tf="5m", closes=closes)


@pytest.fixture
def df_1h_250bars() -> pd.DataFrame:
    """1h OHLCV with 249 closed bars + 1 in-progress (250 rows)."""
    closes = [78000.0 + (i % 50) * 100.0 for i in range(250)]
    return _build(start_ms=1_700_000_000_000, tf="1h", closes=closes)


@pytest.fixture
def df_5m_anomaly() -> pd.DataFrame:
    """5m OHLCV with one bar volume = 5× SMA(20), one bar range = 4× ATR(14).
    Used to drive GMD vol↑ / range↑ marker tests in Task 4.
    Sized to 130 rows for the same GMD display-window reason as
    df_5m_130bars."""
    closes = [81000.0 + (i % 10) * 5.0 for i in range(130)]
    df = _build(start_ms=1_700_000_000_000, tf="5m", closes=closes)
    df.loc[127, "volume"] = 600.0  # 6× the 100.0 SMA baseline
    df.loc[128, "high"] = df.loc[128, "close"] + 200.0  # widens range
    df.loc[128, "low"] = df.loc[128, "close"] - 200.0
    return df


@pytest.fixture
def fake_ticker_81870():
    """Mock ticker.last = 81870.50, bid 81870.40, ask 81870.60."""
    from types import SimpleNamespace
    return SimpleNamespace(
        last=81870.50, bid=81870.40, ask=81870.60,
        high=82500.00, low=81000.00, base_volume=1234.56,
    )
```

- [ ] **Step 2: Verify fixtures import cleanly**

Run:

```bash
python -c "from tests.fixtures.multi_tf_ohlcv import df_4h_250bars, df_1d_250bars, df_5m_130bars, df_5m_anomaly, fake_ticker_81870; print('OK')"
```

Expected: `OK` printed; no `ImportError`.

## 2.2 `_live_price` and `_closed_bars` (TDD)

- [ ] **Step 3: Write failing tests for `_live_price` and `_closed_bars`**

Create `tests/test_ohlcv_utils.py`:

```python
"""Unit tests for src/utils/ohlcv_utils.py helpers.

Covers the §6.4 helper module: _live_price, _closed_bars, _atr_series.
The §6.4.2 invariant (ATR series last-value bit-equality with
compute_indicators) is covered here; the §2.2.1 end-to-end invariant
(MTS / HTF rendered overlap signals equal) is covered in
tests/test_multi_tf_drift_guards.py and operates on rendered output,
not helpers.
"""
from __future__ import annotations
import pytest
import pandas as pd
from types import SimpleNamespace

from tests.fixtures.multi_tf_ohlcv import (
    df_4h_250bars, df_1d_250bars, df_5m_130bars, df_5m_anomaly,
    fake_ticker_81870,
)


def test_live_price_returns_ticker_last_as_float(fake_ticker_81870):
    from src.utils.ohlcv_utils import _live_price
    assert _live_price(fake_ticker_81870) == 81870.50
    assert isinstance(_live_price(fake_ticker_81870), float)


def test_closed_bars_strips_last_row(df_4h_250bars):
    from src.utils.ohlcv_utils import _closed_bars
    closed = _closed_bars(df_4h_250bars)
    assert len(closed) == len(df_4h_250bars) - 1
    assert closed["timestamp"].iloc[-1] == df_4h_250bars["timestamp"].iloc[-2]


def test_closed_bars_returns_view_or_copy_not_mutating_input(df_4h_250bars):
    """Helper must not mutate the input frame."""
    from src.utils.ohlcv_utils import _closed_bars
    before_len = len(df_4h_250bars)
    _ = _closed_bars(df_4h_250bars)
    assert len(df_4h_250bars) == before_len
```

- [ ] **Step 4: Run tests to confirm they fail with ImportError**

Run:

```bash
python -m pytest tests/test_ohlcv_utils.py -v 2>&1 | head -30
```

Expected: 3 failures with `ModuleNotFoundError: No module named 'src.utils.ohlcv_utils'`.

- [ ] **Step 5: Create the helpers module**

Create `src/utils/ohlcv_utils.py`:

```python
"""Shared OHLCV helpers for multi-TF perception tools.

Spec ref: docs/superpowers/specs/2026-05-11-iter-w2r2-next-d-multi-tf-design.md §6.4.

These helpers exist to make the live-state vs closed-bar contract
explicit at every call site in MTS / GMD / HTF, and to lock the
algorithm primitives (pandas_ta.atr mamode='rma', closed-only strip,
ticker.last live-price source) that the §2.2.1 algorithm-lock
invariant rests on for signals MTS and HTF both surface at shared
timeframes (4h, 1d).
"""
from __future__ import annotations
from typing import Any
import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]


def _live_price(ticker: Any) -> float:
    """Canonical live current price.

    Empirically (verify_ohlcv_semantics_v2.py 2026-05-10) approximately
    equal to df['close'].iloc[-1] within a sub-bps drift floor (~0.01 bps
    observed in 31-sample window; not strictly equal due to sub-second
    trade flow between independent ticker and OHLCV API calls). Choose
    ticker.last as the canonical live-price source for code-semantic
    clarity ('this is the live decision-time price').
    """
    return float(ticker.last)


def _closed_bars(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV with the in-progress candle stripped.

    Empirically (verify_ohlcv_semantics_v2.py 2026-05-10): in a 31-sample,
    1m timeframe window with two candle rotations, the closed-only MA(5)
    showed 0.0000 drift while the full-df MA(5) drifted by 0.0200 in
    the same candle window. Stripping is required for temporally stable
    per-cycle facts.
    """
    return df.iloc[:-1]
```

- [ ] **Step 6: Run tests to confirm they pass**

Run:

```bash
python -m pytest tests/test_ohlcv_utils.py::test_live_price_returns_ticker_last_as_float tests/test_ohlcv_utils.py::test_closed_bars_strips_last_row tests/test_ohlcv_utils.py::test_closed_bars_returns_view_or_copy_not_mutating_input -v
```

Expected: 3 passed.

## 2.3 `_atr_series` with rma algorithm lock (TDD)

- [ ] **Step 7: Write failing test for `_atr_series` last-value equality**

Append to `tests/test_ohlcv_utils.py`:

```python
def test_atr_series_last_value_equals_compute_indicators_atr_14(df_4h_250bars):
    """§6.4.2 invariant: _atr_series(df_closed, 14).iloc[-1] must equal
    TechnicalAnalysisService.compute_indicators(df_closed)['atr_14'] bit-for-bit.
    Locks pandas_ta mamode='rma' against future library default drift."""
    from src.utils.ohlcv_utils import _closed_bars, _atr_series
    from src.services.technical import TechnicalAnalysisService
    df_closed = _closed_bars(df_4h_250bars)
    series = _atr_series(df_closed, period=14)
    scalar = TechnicalAnalysisService().compute_indicators(df_closed)["atr_14"]
    assert series.iloc[-1] == pytest.approx(scalar, rel=0, abs=0)  # bit-equal


def test_atr_series_returns_pandas_series(df_4h_250bars):
    from src.utils.ohlcv_utils import _closed_bars, _atr_series
    df_closed = _closed_bars(df_4h_250bars)
    assert isinstance(_atr_series(df_closed, 14), pd.Series)
```

- [ ] **Step 8: Confirm failures**

Run:

```bash
python -m pytest tests/test_ohlcv_utils.py::test_atr_series_last_value_equals_compute_indicators_atr_14 tests/test_ohlcv_utils.py::test_atr_series_returns_pandas_series -v 2>&1 | tail -15
```

Expected: 2 failures with `ImportError: cannot import name '_atr_series' ...` or `AttributeError`.

- [ ] **Step 9: Implement `_atr_series`**

Append to `src/utils/ohlcv_utils.py`:

```python
def _atr_series(df_closed: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(period) **series** computed with Wilder's smoothing (mamode='rma').

    services/technical.py compute_indicators calls pandas_ta.atr(...) which
    currently defaults to mamode='rma' in pandas_ta 0.x; this helper passes
    mamode='rma' explicitly so the algorithm is locked against future
    library default changes. Drift-guard test_atr_series_last_value_equals_compute_indicators_atr_14
    enforces bit-for-bit equality of the last value vs compute_indicators.
    """
    return ta.atr(  # type: ignore[no-any-return]
        df_closed["high"], df_closed["low"], df_closed["close"],
        length=period, mamode="rma",
    )
```

- [ ] **Step 10: Run tests to confirm pass**

Run:

```bash
python -m pytest tests/test_ohlcv_utils.py -v
```

Expected: 5 passed (3 earlier + 2 new).

## 2.4 F-O2 Bollinger labels in `services/technical.py`

- [ ] **Step 11: Write failing test for BB rewrite**

Add a new top-level test in `tests/test_ohlcv_utils.py` (kept in same file since it shares the fixtures; placement matches the commit's scope):

```python
def test_format_for_llm_bb_label_uses_full_words_and_explicit_periods():
    """F-O2: `BB(20,2): Upper X | Middle Y | Lower Z (position: P%, 0%=Lower / 100%=Upper)`."""
    from src.services.technical import TechnicalAnalysisService
    indicators = {
        "rsi_14": 50.0,
        "ma_20": 81700.0, "ma_50": 81800.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 81960.0, "bb_middle": 81727.0, "bb_lower": 81494.0,
        "atr_14": 122.5, "volume_ratio": 1.1,
    }
    out = TechnicalAnalysisService().format_for_llm(indicators, current_price=81870.50)
    assert "BB(20,2):" in out, out
    assert "Upper 81960.00" in out
    assert "Middle 81727.00" in out
    assert "Lower 81494.00" in out
    assert "0%=Lower" in out and "100%=Upper" in out
    assert "position:" in out
    # Old format must be gone:
    assert "BB: 81960" not in out
```

Run:

```bash
python -m pytest tests/test_ohlcv_utils.py::test_format_for_llm_bb_label_uses_full_words_and_explicit_periods -v 2>&1 | tail -10
```

Expected: FAIL — current output starts with `"BB: 81960 / 81727 / 81494 (...)"`.

- [ ] **Step 12: Rewrite the BB block in `services/technical.py`**

Open `src/services/technical.py` lines 95-116 and replace the BB block:

Old code (lines 95-116):

```python
        # Bollinger Bands — fact-only: position as % of band width inside band;
        # 'X% above/below upper/lower band' when price breaks out. Anchor inside
        # the band is band width; anchor outside is the band edge (asymmetric on
        # purpose — band is the reference frame, see spec §2.3 #2).
        bb_u = indicators.get("bb_upper")
        bb_m = indicators.get("bb_middle")
        bb_l = indicators.get("bb_lower")
        if all(v is not None for v in (bb_u, bb_m, bb_l)):
            if bb_u == bb_l:
                pos = "position: N/A"
            elif current_price < bb_l:
                pct_below = (bb_l - current_price) / bb_l * 100
                pos = f"{pct_below:.1f}% below lower band"
            elif current_price > bb_u:
                pct_above = (current_price - bb_u) / bb_u * 100
                pos = f"{pct_above:.1f}% above upper band"
            else:
                pct = (current_price - bb_l) / (bb_u - bb_l) * 100
                pos = f"position: {pct:.0f}% of band width"
            lines.append(f"BB: {bb_u:.0f} / {bb_m:.0f} / {bb_l:.0f} ({pos})")
        else:
            lines.append(f"BB: {_fmt(bb_u)} / {_fmt(bb_m)} / {_fmt(bb_l)}")
```

New code:

```python
        # Bollinger Bands (F-O2 per spec §6.3): full-word labels, explicit
        # (20, 2) periods, explicit 0%=Lower / 100%=Upper anchor.
        # Asymmetric anchor by design (spec §2.3 #2): inside the band, position
        # is rendered as % of band width (the band is the reference frame);
        # outside the band, position is rendered as % distance from the
        # broken band edge (the edge is the reference frame). The frame
        # changes with the regime, not the formula.
        bb_u = indicators.get("bb_upper")
        bb_m = indicators.get("bb_middle")
        bb_l = indicators.get("bb_lower")
        if all(v is not None for v in (bb_u, bb_m, bb_l)):
            if bb_u == bb_l:
                pos = "position: N/A"
            elif current_price < bb_l:
                pct_below = (bb_l - current_price) / bb_l * 100
                pos = f"position: {pct_below:.1f}% below Lower"
            elif current_price > bb_u:
                pct_above = (current_price - bb_u) / bb_u * 100
                pos = f"position: {pct_above:.1f}% above Upper"
            else:
                pct = (current_price - bb_l) / (bb_u - bb_l) * 100
                pos = f"position: {pct:.0f}%, 0%=Lower / 100%=Upper"
            lines.append(
                f"BB(20,2): Upper {bb_u:.2f} | Middle {bb_m:.2f} | Lower {bb_l:.2f} ({pos})"
            )
        else:
            lines.append(
                f"BB(20,2): Upper {_fmt(bb_u)} | Middle {_fmt(bb_m)} | Lower {_fmt(bb_l)}"
            )
```

- [ ] **Step 13: Run the BB test to confirm pass**

Run:

```bash
python -m pytest tests/test_ohlcv_utils.py::test_format_for_llm_bb_label_uses_full_words_and_explicit_periods -v
```

Expected: PASS.

- [ ] **Step 14: Locate existing BB-label assertions and update them in-place**

Some existing tests assert the **old** BB format (`"BB: ... / ..."`). Find them:

```bash
grep -rn 'BB: ' tests/ src/
```

Expected hits — for each one, update the assertion string to match the new `BB(20,2): Upper X | Middle Y | Lower Z (position: ...)` form. The asserted exact values stay the same; only the label wording changes.

If `grep` returns only the snapshot lines in `tests/test_display_cycle.py`, update each verbatim. Use the Edit tool — do NOT mass-rewrite via `sed`.

- [ ] **Step 15: Run the entire test suite to confirm no regressions**

Run:

```bash
python -m pytest -q 2>&1 | tail -10
```

Expected: all green; total collected ≥ 1487 + new tests added in this task (`test_ohlcv_utils.py` adds 6: 3 helper + 2 ATR + 1 BB = 1493+).

- [ ] **Step 16: Commit**

Run:

```bash
git add src/utils/ohlcv_utils.py tests/test_ohlcv_utils.py tests/fixtures/multi_tf_ohlcv.py src/services/technical.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
refactor(technical+utils): shared OHLCV helpers + F-O2 BB labels

Add src/utils/ohlcv_utils.py with three primitives (_live_price /
_closed_bars / _atr_series), each carrying one design decision:
canonical live-price source, closed-only strip, and pandas_ta.atr
mamode="rma" algorithm lock. These three back the §2.2.1
algorithm-lock invariant for MTS / HTF shared signals at 4h / 1d.
F-O2: BB label rewrites to "BB(20,2): Upper X | Middle Y | Lower Z
(position: P%, 0%=Lower / 100%=Upper)" — full words + explicit periods
+ explicit anchor; conforms to fact-only label discipline.

Spec §6.3 / §6.4 / §6.4.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 17: Verify commit**

Run:

```bash
git log --oneline -2
git diff HEAD~1 HEAD --stat
```

Expected: top commit is the helpers + BB rewrite; diff stat shows ~120-160 lines added across `ohlcv_utils.py` / `test_ohlcv_utils.py` / `multi_tf_ohlcv.py` plus ~20 modified lines in `technical.py`.

---

# Task 3 — Commit 3: HTF list-form + N6 G1-G5

Spec ref: §5 (full); §6.1 wrapper docstring tail; §7.1.1 test migration matrix.

HTF goes first (before GMD and MTS) so the most signal-edge tool ships its new contract first; subsequent tool changes can rely on the §2.2.1 algorithm-lock invariant.

**Files:**
- Modify: `src/agent/tools_perception.py` lines 836-934 (`_htf_ago_fmt` retained, `get_higher_timeframe_view` body rewritten)
- Modify: `src/agent/trader.py` lines 273-290 (wrapper signature + docstring + "Related" tail)
- Modify: `tests/test_perception_tools_n3.py` — 14 call sites + assertion strings (§7.1.1)
- Modify: `tests/test_fact_only_wordlist.py` line 555 — positional call migration
- Create: `tests/test_iter_w2r2_next_d_goldens.py` — first test class `TestHTFGolden`

## 3.1 Golden mockup test (TDD)

- [ ] **Step 1: Create the golden-mockup test module with HTF section**

Create `tests/test_iter_w2r2_next_d_goldens.py`:

```python
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
```

- [ ] **Step 2: Run HTF goldens to confirm failures**

Run:

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestHTFGolden -v 2>&1 | tail -25
```

Expected: 11 failures — most assertion strings absent because the current HTF output uses `Current Price:` / single-tf section / no MA100 / no slope / no MA stack / no 100-period bars-ago full date / etc.

## 3.2 Implement HTF list-form

- [ ] **Step 3: Add HTF MA-period table constant**

Open `src/agent/tools_perception.py` and locate the constants block (lines 25-36). Append after line 36 (after `MULTI_TF_OHLCV_LIMIT`):

```python
# get_higher_timeframe_view (Iter w2r2-next-d): per-tf MA periods.
# 4h/1d/1w use standard (50, 100, 200); 1M uses (12, 24, 60) = 1y/2y/5y
# monthly per crypto-industry convention (spec §5.4).
HTF_MA_PERIODS: dict[str, tuple[int, int, int]] = {
    "4h": (50, 100, 200),
    "1d": (50, 100, 200),
    "1w": (50, 100, 200),
    "1M": (12, 24, 60),
}
HTF_OHLCV_LIMIT = 250  # uniform; longest MA(200) + slope lookback 10 + buffer
```

- [ ] **Step 4: Rewrite `get_higher_timeframe_view` body**

Open `src/agent/tools_perception.py` lines 849-934. Replace the entire function (lines 849-934, keeping `_htf_ago_fmt` at 836-846 unchanged) with:

```python
async def get_higher_timeframe_view(
    deps: TradingDeps,
    timeframes: list[Literal["4h", "1d", "1w", "1M"]] | None = None,
) -> str:
    """Long-term structural view across one or more higher timeframes: ticker (authoritative live price), per-tf MA50/MA100/MA200 with raw value, price-vs-MA percentage, and MA slope (10-bar lookback); MA stack comparison; 100-period high and low with bars-ago and the candle open timestamp; range position within 100-period; 20-period high-low range width; last-bar volume vs 20-period SMA ratio (base volume); ATR(14) raw, percent of price, and ratio vs 20-period ATR average.

    All moving averages are simple moving averages (SMA) computed on the closed-bar series only (excluding the in-progress bar). The slope reference and all rolling averages use the closed-candle series. ATR(14) is computed via _atr_series (mamode='rma' algorithm lock per spec §6.4.2).

    MA stack comparison uses ">" / "<" / "≈" with 0.1% tolerance: when |MAa - MAb| / MAb < 0.001, the operator collapses to "≈" (e.g., "MA50 ≈ MA100 < MA200").

    Per-tf MA periods: 4h / 1d / 1w use (50, 100, 200) — standard moving-average periods. 1M uses (12, 24, 60), corresponding to 1-year / 2-year / 5-year monthly cycles, matching crypto-industry monthly chart conventions; the 1M section header marks the period choice explicitly.

    Args:
        timeframes: List of CCXT timeframes from {"4h", "1d", "1w", "1M"}. Default ["4h", "1d"]. Each timeframe rendered as a separate section.

    Example call:
        get_higher_timeframe_view(timeframes=["4h", "1d"])
    Example output:
        === Higher Timeframe View (BTC/USDT:USDT @ 14:23:08 UTC) ===
        Last: 81870.50

        [4h] (last closed candle: open 2026-05-11 08:00 UTC)
          MA50: 79200.00 (price vs MA: +3.4%; MA slope vs 10 bars ago: +0.8%)
          ...
          MA stack: MA50 > MA100 > MA200
          100-period High: 82800.00 (32 bars ago, candle open 2026-05-06 00:00 UTC)
          ...
          Last bar vol (base): 1521.6 (5.0× SMA(20) avg)
          ATR(14): 1572.30 (1.92% of price; 1.04× vs 20-period ATR(14) avg)
        ...

    Degradation: per-tf "insufficient data (need N candles)" if OHLCV history is shorter than the longest MA period; per-tf "Error: Temporarily unavailable" if the OHLCV fetch for that tf fails; overall returns header-only error if the ticker fetch fails.

    Related perception tools (factual capability surface, not a calling order):
        - get_multi_timeframe_snapshot: cross-timeframe alignment overview — authoritative ticker, per-tf MA fast-vs-slow direction count, per-tf momentum / structure with raw MA values / volatility ratio / range position / 3 closed candle closes.
        - get_market_data: single-timeframe depth output — full RSI / MACD / BB / Volume ratio indicators, market context, a 30-candle OHLCV table with anomaly markers, and a period summary (last 5 vs prior 5 closed candles).
    """
    import asyncio
    import pandas as pd
    from datetime import datetime, timezone

    from src.utils.ohlcv_utils import _live_price, _closed_bars, _atr_series

    symbol = deps.symbol
    if timeframes is None:
        timeframes = ["4h", "1d"]

    try:
        ticker = await deps.market_data.get_ticker(symbol)
        live_price = _live_price(ticker)
    except Exception:
        logger.warning("HTF ticker fetch failed for %s", symbol, exc_info=True)
        return f"=== Higher Timeframe View ({symbol}) ===\nError: Temporarily unavailable."

    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    async def _fetch_one(tf: str) -> tuple[str, pd.DataFrame | Exception]:
        try:
            df = await deps.market_data.get_ohlcv_dataframe(symbol, tf, limit=HTF_OHLCV_LIMIT)
            return tf, df
        except Exception as e:
            return tf, e

    results = await asyncio.gather(*[_fetch_one(tf) for tf in timeframes])

    sections: list[str] = [
        f"=== Higher Timeframe View ({symbol} @ {fetch_ts} UTC) ===",
        f"Last: {live_price:.2f}",
        "",
    ]

    for tf, df_or_err in results:
        ma_periods = HTF_MA_PERIODS.get(tf, (50, 100, 200))
        fast_n, mid_n, slow_n = ma_periods

        if isinstance(df_or_err, Exception):
            sections.append(f"[{tf}] Error: Temporarily unavailable.")
            sections.append("")
            continue

        df = df_or_err
        if df.empty or len(df) < slow_n + 1:
            sections.append(
                f"[{tf}] insufficient data (need {slow_n + 1} candles, got {len(df)})"
            )
            sections.append("")
            continue

        df_closed = _closed_bars(df)
        # Header — last closed candle timestamp
        last_ts_ms = int(df_closed["timestamp"].iloc[-1])
        last_dt = datetime.fromtimestamp(last_ts_ms / 1000, tz=timezone.utc)
        if tf == "1M":
            header = (
                f"[{tf}] (last closed candle: open {last_dt.strftime('%Y-%m-%d %H:%M')} UTC; "
                f"MA periods {fast_n}/{mid_n}/{slow_n} = 1y/2y/5y monthly — "
                f"adapted for crypto-industry monthly cycle conventions)"
            )
        else:
            header = f"[{tf}] (last closed candle: open {last_dt.strftime('%Y-%m-%d %H:%M')} UTC)"
        sections.append(header)

        # MA lines — fast / mid / slow with slope
        close = df_closed["close"]
        def _ma_line(n: int) -> str:
            if len(df_closed) < n + 10:
                return f"  MA{n}: insufficient data (need {n + 10} candles)"
            ma_now = float(close.rolling(n).mean().iloc[-1])
            ma_then = float(close.rolling(n).mean().iloc[-11])
            slope_pct = (ma_now - ma_then) / ma_then * 100.0 if ma_then > 0 else 0.0
            dist_pct = (live_price - ma_now) / ma_now * 100.0
            return (
                f"  MA{n}: {ma_now:.2f}  (price vs MA: {dist_pct:+.1f}%; "
                f"MA slope vs 10 bars ago: {slope_pct:+.1f}%)"
            )

        ma_fast_line = _ma_line(fast_n)
        ma_mid_line = _ma_line(mid_n)
        ma_slow_line = _ma_line(slow_n)
        sections.extend([ma_fast_line, ma_mid_line, ma_slow_line])

        # MA stack
        try:
            ma_vals = {
                fast_n: float(close.rolling(fast_n).mean().iloc[-1]),
                mid_n: float(close.rolling(mid_n).mean().iloc[-1]),
                slow_n: float(close.rolling(slow_n).mean().iloc[-1]),
            }
            ordered = sorted(ma_vals.items(), key=lambda kv: -kv[1])
            ops: list[str] = []
            for (na, va), (nb, vb) in zip(ordered, ordered[1:]):
                rel_diff = abs(va - vb) / vb if vb > 0 else 0.0
                ops.append("≈" if rel_diff < 0.001 else ">")
            stack_str = " ".join(
                [f"MA{ordered[0][0]}"]
                + [f"{ops[i]} MA{ordered[i + 1][0]}" for i in range(len(ops))]
            )
            sections.append(f"  MA stack: {stack_str}")
        except Exception:
            sections.append("  MA stack: insufficient data")

        # 100-period range
        if len(df_closed) >= 100:
            last_100 = df_closed.iloc[-100:].reset_index(drop=True)
            hi_idx = int(last_100["high"].idxmax())
            lo_idx = int(last_100["low"].idxmin())
            hi100 = float(last_100["high"].max())
            lo100 = float(last_100["low"].min())
            hi_ago = 99 - hi_idx
            lo_ago = 99 - lo_idx
            hi_ts = datetime.fromtimestamp(int(last_100["timestamp"].iloc[hi_idx]) / 1000, tz=timezone.utc)
            lo_ts = datetime.fromtimestamp(int(last_100["timestamp"].iloc[lo_idx]) / 1000, tz=timezone.utc)
            rng_pos = ((live_price - lo100) / (hi100 - lo100) * 100.0) if hi100 != lo100 else 0.0
            sections.extend([
                f"  100-period High: {hi100:.2f}  ({hi_ago} bars ago, candle open {hi_ts.strftime('%Y-%m-%d %H:%M')} UTC)",
                f"  100-period Low:  {lo100:.2f}  ({lo_ago} bars ago, candle open {lo_ts.strftime('%Y-%m-%d %H:%M')} UTC)",
                f"  Range pos (within 100-period): {rng_pos:.0f}%  (0%=Low, 100%=High)",
            ])

        # 20-period band
        if len(df_closed) >= 20:
            last_20 = df_closed.iloc[-20:]
            hi20 = float(last_20["high"].max())
            lo20 = float(last_20["low"].min())
            width_pct = (hi20 - lo20) / lo20 * 100.0 if lo20 > 0 else 0.0
            sections.append(
                f"  20-period High: {hi20:.2f} / Low: {lo20:.2f} / range width: {width_pct:.1f}% (= (High-Low)/Low)"
            )

        # Last bar vol regime
        if len(df_closed) >= 21:
            vol_now = float(df_closed["volume"].iloc[-1])
            vol_avg_20 = float(df_closed["volume"].iloc[-20:].mean())
            ratio = vol_now / vol_avg_20 if vol_avg_20 > 0 else 0.0
            sections.append(f"  Last bar vol (base): {vol_now:.1f}  ({ratio:.1f}× SMA(20) avg)")

        # ATR regime
        if len(df_closed) >= 35:  # 14 ATR window + 20 ATR-avg window + 1
            atr_series = _atr_series(df_closed, period=14)
            atr_now = float(atr_series.iloc[-1])
            atr_avg = float(atr_series.rolling(20).mean().iloc[-1])
            atr_pct = atr_now / live_price * 100.0 if live_price > 0 else 0.0
            atr_ratio = atr_now / atr_avg if atr_avg > 0 else 0.0
            sections.append(
                f"  ATR(14): {atr_now:.2f}  ({atr_pct:.2f}% of price; "
                f"{atr_ratio:.2f}× vs 20-period ATR(14) avg)"
            )

        sections.append("")

    return "\n".join(sections).rstrip()
```

- [ ] **Step 5: Run HTF goldens to confirm pass**

Run:

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestHTFGolden -v
```

Expected: 11 passed.

## 3.3 Wrapper docstring + signature in `trader.py`

- [ ] **Step 6: Replace HTF wrapper in `src/agent/trader.py`**

Open `src/agent/trader.py` lines 273-290 and replace the entire `@tool async def get_higher_timeframe_view` block with:

```python
    @tool
    async def get_higher_timeframe_view(
        ctx: RunContext[TradingDeps],
        timeframes: list[Literal["4h", "1d", "1w", "1M"]] | None = None,
    ) -> str:
        """Long-term structural view across one or more higher timeframes: ticker (authoritative live price), per-tf MA50/MA100/MA200 with raw value, price-vs-MA percentage, and MA slope (10-bar lookback); MA stack comparison; 100-period high and low with bars-ago and the candle open timestamp; range position within 100-period; 20-period high-low range width; last-bar volume vs 20-period SMA ratio (base volume); ATR(14) raw, percent of price, and ratio vs 20-period ATR average.

        All moving averages are simple moving averages (SMA) computed on the closed-bar series only (excluding the in-progress bar). The slope reference and all rolling averages use the closed-candle series.

        MA stack comparison uses ">" / "<" / "≈" with 0.1% tolerance: when |MAa - MAb| / MAb < 0.001, the operator collapses to "≈" (e.g., "MA50 ≈ MA100 < MA200").

        Per-tf MA periods: 4h / 1d / 1w use (50, 100, 200) — standard moving-average periods. 1M uses (12, 24, 60), corresponding to 1-year / 2-year / 5-year monthly cycles, matching crypto-industry monthly chart conventions; the 1M section header marks the period choice explicitly.

        Args:
            timeframes: List of CCXT timeframes from {"4h", "1d", "1w", "1M"}. Default ["4h", "1d"]. Each timeframe rendered as a separate section.

        Example call:
            get_higher_timeframe_view(timeframes=["4h", "1d"])
        Example output:
            === Higher Timeframe View (BTC/USDT:USDT @ 14:23:08 UTC) ===
            Last: 81870.50

            [4h] (last closed candle: open 2026-05-11 08:00 UTC)
              MA50: 79200.00 (price vs MA: +3.4%; MA slope vs 10 bars ago: +0.8%)
              ...
              MA stack: MA50 > MA100 > MA200
              100-period High: 82800.00 (32 bars ago, candle open 2026-05-06 00:00 UTC)
              ...
              Last bar vol (base): 1521.6 (5.0× SMA(20) avg)
              ATR(14): 1572.30 (1.92% of price; 1.04× vs 20-period ATR(14) avg)
            ...

        Degradation: per-tf "insufficient data (need N candles)" if OHLCV history is shorter than the longest MA period; per-tf "Error: Temporarily unavailable" if the OHLCV fetch for that tf fails; overall returns header-only error if the ticker fetch fails.

        Related perception tools (factual capability surface, not a calling order):
            - get_multi_timeframe_snapshot: cross-timeframe alignment overview — authoritative ticker, per-tf MA fast-vs-slow direction count, per-tf momentum / structure with raw MA values / volatility ratio / range position / 3 closed candle closes.
            - get_market_data: single-timeframe depth output — full RSI / MACD / BB / Volume ratio indicators, market context, a 30-candle OHLCV table with anomaly markers, and a period summary (last 5 vs prior 5 closed candles).
        """
        from src.agent.tools_perception import get_higher_timeframe_view as _impl

        return await _impl(ctx.deps, timeframes)
```

The impl-function docstring written in Step 4 already mirrors the wrapper docstring above (spec §6.2 two-layer convention). No additional mirror step is needed — both surfaces carry the same full text in a single edit.

## 3.4 Migrate existing HTF tests

- [ ] **Step 7: Update `tests/test_perception_tools_n3.py` call sites and assertions**

Read `tests/test_perception_tools_n3.py` to enumerate every HTF call — audited 2026-05-11 returns 12 `await get_higher_timeframe_view(...)` call sites at lines 82, 106, 117, 128, 139, 152, 167, 182, 201, 216, 250, 294. Verify with:

```bash
grep -n "await get_higher_timeframe_view" tests/test_perception_tools_n3.py
```

For **every** matched call, apply this transformation:

- `await get_higher_timeframe_view(deps, timeframe="1d")` → `await get_higher_timeframe_view(deps, timeframes=["1d"])`
- `await get_higher_timeframe_view(deps, timeframe="4h")` → `await get_higher_timeframe_view(deps, timeframes=["4h"])`
- `await get_higher_timeframe_view(deps, timeframe="1w")` → `await get_higher_timeframe_view(deps, timeframes=["1w"])`
- `await get_higher_timeframe_view(deps, timeframe="1M")` → `await get_higher_timeframe_view(deps, timeframes=["1M"])`

For every assertion string in this file that compares against the OLD layout — e.g., `assert "Current Price: " in result`, `assert "=== MA Distances ===" in result`, `assert "=== Range Position ===" in result`, `assert "Current price within range" in result` — rewrite to the new layout:

- `"Current Price:"` → `"Last:"`
- `"=== MA Distances ==="` → asserts removed; replace with `"MA50:"`, `"MA100:"`, `"MA200:"` substrings (or specific assertions per test)
- `"=== Range Position ==="` → `"100-period High:"` / `"Range pos (within 100-period):"`
- `"=== 20-period Band ==="` → `"20-period High:"` / `"range width:"`
- `"Current price within range"` → `"Range pos (within 100-period):"`

Edit each test in-place with the Edit tool. Goal: each test asserts the same semantic thing in the new layout. If a test specifically checks an MA value, the new layout still exposes it under `  MAn: V` so assertion shape just changes from `f"MA{period}: {val:,.2f}"` → `f"MA{period}: {val:.2f}"` (note: no thousand separator).

- [ ] **Step 8: Update `tests/test_fact_only_wordlist.py` line 555**

Edit:

```python
output = await get_higher_timeframe_view(deps, "4h")
```

to:

```python
output = await get_higher_timeframe_view(deps, ["4h"])
```

- [ ] **Step 9: Run migrated tests**

Run:

```bash
python -m pytest tests/test_perception_tools_n3.py tests/test_fact_only_wordlist.py::test_get_higher_timeframe_view_fact_only -v 2>&1 | tail -40
```

Expected: all tests previously calling HTF pass. If any output-assertion still fails, inspect the actual output via:

```bash
python -m pytest tests/test_perception_tools_n3.py::<failing_test_name> -v -s 2>&1 | tail -30
```

and adjust the assertion string (keep the test's intent; only update wording).

- [ ] **Step 10: Run full suite to check for ripple regressions**

Run:

```bash
python -m pytest -q 2>&1 | tail -10
```

Expected: all green; total count ≥ 1487 + Task 2 additions + Task 3 additions.

**Triage rule:** if any `tests/test_display_cycle.py` snapshot fails on HTF wording, defer the rewrite to Task 9 (display_cycle gets a full sweep there). Mark with `pytest.mark.xfail(reason="display_cycle HTF snapshot rewrite deferred to Task 9")` and proceed — Task 9 will remove the xfail.

- [ ] **Step 11: Commit**

Run:

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_iter_w2r2_next_d_goldens.py tests/test_perception_tools_n3.py tests/test_fact_only_wordlist.py
git commit -m "$(cat <<'EOF'
feat(htf): list-form signature + N6 G1-G5 enrichment

get_higher_timeframe_view now accepts timeframes: list[Literal[...]]
with default ["4h","1d"] (single-tf form removed — see §7.1.1 test
migration). Per-tf section adds MA100 + slope + MA stack + 100-period
bars-ago full date + volume regime + ATR regime; 1M timeframe uses
(12, 24, 60) periods per crypto-industry monthly convention.

All indicator inputs use _closed_bars (§6.4); Last: uses ticker.last
(§6.4 + A1 empirical). Wrapper docstring includes the "Related
perception tools" fact-only tail (§6.1).

Spec §5 / §6.1 / §6.4 / §7.1.1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 12: Verify commit**

Run:

```bash
git log --oneline -3
```

Expected: top three commits are docs(plan), refactor(technical+utils), feat(htf).

---

# Task 4 — Commit 4: GMD changes

Spec ref: §4 (full); §6.1 wrapper tail; §6.3 F-O3.

**Files:**
- Modify: `src/agent/tools_perception.py` lines 39-136 (`get_market_data` body rewritten)
- Modify: `src/agent/trader.py` lines 85-109 (wrapper signature + docstring + "Related" tail)
- Create: new test class `TestGMDGolden` in `tests/test_iter_w2r2_next_d_goldens.py`

## 4.1 Golden tests for GMD (TDD)

- [ ] **Step 1: Append GMD golden tests**

Append to `tests/test_iter_w2r2_next_d_goldens.py`:

```python
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
    async def test_gmd_market_context_uses_last_bar_vol_and_smaperiod(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """F-O3: Last bar vol: X (Y× SMA(20) avg)."""
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "Last bar vol:" in out
        assert "SMA(20) avg)" in out
        # Old label is gone:
        assert "Volume:" not in out.split("=== Market Context ===")[1].split("===")[0]

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
        """B4: Period summary section after OHLCV table; 3 fields."""
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Period summary (last 5 closed candles vs prior 5 closed candles) ===" in out
        assert "Avg vol:" in out
        assert "Avg range (H-L):" in out
        assert "Net Δclose:" in out

    @pytest.mark.asyncio
    async def test_gmd_closed_only_indicator_inputs(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """A4: Indicator inputs must be _closed_bars(df), not full df.
        Spot-check: with our deterministic fixture, MA20 must equal
        _closed_bars(df)['close'].rolling(20).mean().iloc[-1]."""
        from src.agent.tools_perception import get_market_data
        from src.utils.ohlcv_utils import _closed_bars
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        df_closed = _closed_bars(df_5m_130bars)
        expected_ma20 = float(df_closed["close"].rolling(20).mean().iloc[-1])
        # Output renders MA(20) at 2dp; verify presence with the exact rounded value
        assert f"MA(20): {expected_ma20:.2f}" in out, out
```

- [ ] **Step 2: Run goldens to confirm failures**

Run:

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestGMDGolden -v 2>&1 | tail -20
```

Expected: 7 failures.

## 4.2 Rewrite `get_market_data`

- [ ] **Step 3: Rewrite the function body**

Open `src/agent/tools_perception.py` lines 39-136. Replace with:

```python
async def get_market_data(
    deps: TradingDeps,
    symbol: str | None = None,
    timeframe: str | None = None,
    candle_count: int = 30,
) -> str:
    """Single-timeframe market data: ticker, technical indicators (RSI / MACD / BB / ATR / volume ratio), market context (ATR with percent of price, last-bar volume with average ratio, display-window range), the most recent N closed candles in OHLCV table form with anomaly markers, and a period summary comparing the last 5 vs prior 5 closed candles (avg volume, avg range, net Δclose).

    All indicators are computed on the closed-bar series only (excluding the in-progress candle). The OHLCV table also shows closed bars only and is sorted oldest-first by row.

    Markers in OHLCV table (upside-only thresholds):
        "vol↑"   — bar volume > 2× SMA(20) of bar volumes
        "range↑" — bar range (high - low) > 2× ATR(14)
        Empty    — neither threshold tripped.

    Time column shows candle open in UTC.

    Args:
        symbol: Trading symbol. Defaults to session symbol.
        timeframe: CCXT timeframe ("1m", "5m", "1h", etc.). Defaults to session primary timeframe.
        candle_count: Number of closed candles in the OHLCV table. Default 30. Range 10-80 (capped by exchange API).

    Example call:
        get_market_data(timeframe="5m", candle_count=30)
    Example output:
        === Ticker (BTC/USDT:USDT @ 14:23:08 UTC) ===
        Last: 81870.50 | Bid: 81870.40 | Ask: 81870.60
        ...
        === Recent Candles (5m, last 30, oldest-first by row) ===
        Time (open UTC)   Open ... Vol     Markers
        14:20         ...         245.3   vol↑
        ...
        === Period summary (last 5 closed candles vs prior 5 closed candles) ===
        Avg vol:            last 5 178.6 / prior 5 132.4 (1.35×)
        Avg range (H-L):    last 5 38.2 / prior 5 24.8 (1.54×)
        Net Δclose:         last 5 -25.0 USDT / prior 5 +120.0 USDT

    Related perception tools (factual capability surface, not a calling order):
        - get_multi_timeframe_snapshot: cross-timeframe alignment overview — authoritative ticker, per-tf MA fast-vs-slow direction count, per-tf momentum / structure with raw MA values / volatility ratio / range position / 3 closed candle closes.
        - get_higher_timeframe_view: long-term structural anchors output — raw MA50/100/200 values with slopes and MA stack, 100-period range with bars-ago, volume regime, ATR regime, across one or more higher timeframes.
    """
    import pandas as pd
    from datetime import datetime, timezone
    from src.utils.ohlcv_utils import _live_price, _closed_bars, _atr_series

    symbol = symbol or deps.symbol
    timeframe = timeframe or deps.timeframe
    candle_count = max(10, min(candle_count, 80))

    ticker = await deps.market_data.get_ticker(symbol)
    live_price = _live_price(ticker)
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    fetch_limit = max(candle_count + 50, 100)
    df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=fetch_limit)
    df_closed = _closed_bars(df)
    indicators = deps.technical.compute_indicators(df_closed)
    indicators_text = deps.technical.format_for_llm(
        indicators, current_price=live_price, timeframe=timeframe,
    )

    available_closed = len(df_closed)
    if available_closed >= candle_count + 50:
        display_count = candle_count
    else:
        display_count = max(10, available_closed - 50)
    display_df = df_closed.tail(display_count)

    sections: list[str] = []

    # === Ticker ===
    sections.append(
        f"=== Ticker ({symbol} @ {fetch_ts} UTC) ===\n"
        f"Last: {live_price:.2f} | Bid: {ticker.bid:.2f} | Ask: {ticker.ask:.2f}\n"
        f"24h High: {ticker.high:.2f} | 24h Low: {ticker.low:.2f} | 24h base vol: {ticker.base_volume:.2f}"
    )

    # === Technical Indicators ===
    sections.append(f"=== Technical Indicators ({timeframe}) ===\n{indicators_text}")

    # === Market Context ===
    ctx_lines: list[str] = []
    atr = indicators.get("atr_14")
    if atr is not None and live_price > 0:
        pct = atr / live_price * 100
        ctx_lines.append(f"ATR(14): {atr:.2f} ({pct:.2f}% of price, {timeframe} candles)")
    else:
        ctx_lines.append("ATR(14): N/A")

    # F-O3: Last bar vol with SMA(20) period explicit
    if len(df_closed) >= 21:
        vol_now = float(df_closed["volume"].iloc[-1])
        vol_avg = float(df_closed["volume"].iloc[-21:-1].mean())
        ratio = vol_now / vol_avg if vol_avg > 0 else 0.0
        ctx_lines.append(f"Last bar vol: {vol_now:.1f} ({ratio:.2f}× SMA(20) avg)")
    else:
        ctx_lines.append("Last bar vol: N/A")

    if not display_df.empty:
        ctx_lines.append(
            f"{display_count}-candle High-Low: {display_df['low'].min():.0f} — {display_df['high'].max():.0f}"
        )
    else:
        ctx_lines.append("Range: N/A")
    sections.append("=== Market Context ===\n" + "\n".join(ctx_lines))

    # === Recent Candles (OHLCV with markers) ===
    vol_sma = df_closed["volume"].rolling(20).mean()
    atr_series = _atr_series(df_closed, period=14) if len(df_closed) >= 15 else None
    candle_lines: list[str] = [
        f"{'Time (open UTC)':<16} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10} {'Vol':>10}  Markers"
    ]
    for idx in display_df.index:
        row = df_closed.loc[idx]
        ts_val = row["timestamp"]
        if isinstance(ts_val, (int, float)):
            dt = datetime.fromtimestamp(ts_val / 1000, tz=timezone.utc)
        else:
            dt = ts_val
        tf_short = timeframe.lower()
        if tf_short in ("1m", "5m", "15m"):
            time_str = dt.strftime("%H:%M")
        elif tf_short in ("1h", "4h"):
            time_str = dt.strftime("%m-%d %H:%M")
        else:
            time_str = dt.strftime("%Y-%m-%d")

        markers: list[str] = []
        vol_sma_at = vol_sma.loc[idx] if idx in vol_sma.index else None
        if vol_sma_at is not None and not pd.isna(vol_sma_at) and float(vol_sma_at) > 0:
            if float(row["volume"]) > 2 * float(vol_sma_at):
                markers.append("vol↑")
        atr_at = None
        if atr_series is not None and idx in atr_series.index:
            atr_at = atr_series.loc[idx]
        if atr_at is not None and not pd.isna(atr_at) and float(atr_at) > 0:
            if (float(row["high"]) - float(row["low"])) > 2 * float(atr_at):
                markers.append("range↑")
        marker_str = " ".join(markers)

        candle_lines.append(
            f"{time_str:<16} {row['open']:>10.2f} {row['high']:>10.2f} "
            f"{row['low']:>10.2f} {row['close']:>10.2f} {row['volume']:>10.1f}  {marker_str}".rstrip()
        )
    sections.append(
        f"=== Recent Candles ({timeframe}, last {display_count}, oldest-first by row) ===\n"
        + "\n".join(candle_lines)
    )

    # === Period summary ===
    if len(df_closed) >= 10:
        last_5 = df_closed.iloc[-5:]
        prior_5 = df_closed.iloc[-10:-5]
        avg_vol_last = float(last_5["volume"].mean())
        avg_vol_prior = float(prior_5["volume"].mean())
        vol_ratio = avg_vol_last / avg_vol_prior if avg_vol_prior > 0 else 0.0
        avg_rng_last = float((last_5["high"] - last_5["low"]).mean())
        avg_rng_prior = float((prior_5["high"] - prior_5["low"]).mean())
        rng_ratio = avg_rng_last / avg_rng_prior if avg_rng_prior > 0 else 0.0
        net_delta_last = float(df_closed["close"].iloc[-1] - df_closed["close"].iloc[-5])
        net_delta_prior = float(df_closed["close"].iloc[-6] - df_closed["close"].iloc[-10])
        summary = (
            "=== Period summary (last 5 closed candles vs prior 5 closed candles) ===\n"
            f"Avg vol:            last 5 {avg_vol_last:.1f} / prior 5 {avg_vol_prior:.1f} ({vol_ratio:.2f}×)\n"
            f"Avg range (H-L):    last 5 {avg_rng_last:.1f} / prior 5 {avg_rng_prior:.1f} ({rng_ratio:.2f}×)\n"
            f"Net Δclose:         last 5 {net_delta_last:+.1f} USDT / prior 5 {net_delta_prior:+.1f} USDT"
        )
        sections.append(summary)

    return "\n\n".join(sections)
```

Note: the body uses `pd.isna(...)` via the local `import pandas as pd` shown in the function body above. The file currently has no top-level pandas import — keeping it local mirrors how `get_multi_timeframe_snapshot` already imports pandas at the call site (line 1435 in the baseline). If a future task introduces top-level pandas import for cleanliness, GMD's local import becomes redundant but harmless.

## 4.3 Wrapper docstring + signature in `trader.py`

- [ ] **Step 4: Replace GMD wrapper in `src/agent/trader.py`**

Open `src/agent/trader.py` lines 85-109 and replace the entire `@tool async def get_market_data` block with the mirror of the impl-function docstring from Step 3, wrapped in the @tool form:

```python
    @tool
    async def get_market_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
        timeframe: str | None = None,
        candle_count: int = 30,
    ) -> str:
        """Single-timeframe market data: ticker, technical indicators (RSI / MACD / BB / ATR / volume ratio), market context (ATR with percent of price, last-bar volume with average ratio, display-window range), the most recent N closed candles in OHLCV table form with anomaly markers, and a period summary comparing the last 5 vs prior 5 closed candles (avg volume, avg range, net Δclose).

        All indicators are computed on the closed-bar series only (excluding the in-progress candle). The OHLCV table also shows closed bars only and is sorted oldest-first by row.

        Markers in OHLCV table (upside-only thresholds):
            "vol↑"   — bar volume > 2× SMA(20) of bar volumes
            "range↑" — bar range (high - low) > 2× ATR(14)
            Empty    — neither threshold tripped.

        Time column shows candle open in UTC.

        Args:
            symbol: Trading symbol. Defaults to session symbol.
            timeframe: CCXT timeframe ("1m", "5m", "1h", etc.). Defaults to session primary timeframe.
            candle_count: Number of closed candles in the OHLCV table. Default 30. Range 10-80 (capped by exchange API).

        Example call:
            get_market_data(timeframe="5m", candle_count=30)
        Example output:
            === Ticker (BTC/USDT:USDT @ 14:23:08 UTC) ===
            Last: 81870.50 | Bid: 81870.40 | Ask: 81870.60
            ...
            === Recent Candles (5m, last 30, oldest-first by row) ===
            Time (open UTC)   Open ... Vol     Markers
            14:20         ...         245.3   vol↑
            ...
            === Period summary (last 5 closed candles vs prior 5 closed candles) ===
            Avg vol:            last 5 178.6 / prior 5 132.4 (1.35×)
            Avg range (H-L):    last 5 38.2 / prior 5 24.8 (1.54×)
            Net Δclose:         last 5 -25.0 USDT / prior 5 +120.0 USDT

        Related perception tools (factual capability surface, not a calling order):
            - get_multi_timeframe_snapshot: cross-timeframe alignment overview — authoritative ticker, per-tf MA fast-vs-slow direction count, per-tf momentum / structure with raw MA values / volatility ratio / range position / 3 closed candle closes.
            - get_higher_timeframe_view: long-term structural anchors output — raw MA50/100/200 values with slopes and MA stack, 100-period range with bars-ago, volume regime, ATR regime, across one or more higher timeframes.
        """
        from src.agent.tools_perception import get_market_data as _impl

        return await _impl(ctx.deps, symbol, timeframe, candle_count)
```

The wrapper docstring is identical text to the impl-function docstring in Step 3 (spec §6.2 two-layer convention satisfied in one edit per surface).

## 4.4 Run goldens + suite

- [ ] **Step 5: Run GMD goldens**

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestGMDGolden -v
```

Expected: 7 passed.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest -q 2>&1 | tail -10
```

Expected: all green. If `tests/test_display_cycle.py` snapshot tests fail on the new GMD ticker label, mark them with `pytest.mark.xfail(reason="display_cycle GMD snapshot rewrite deferred to Task 9")` and proceed.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_iter_w2r2_next_d_goldens.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat(gmd): default candle_count 30 + B3/B4 + closed-only + N13/F-O3

get_market_data now defaults candle_count=30 (sim #8 modal cluster
68%); B3 anomaly markers vol↑ / range↑ in the OHLCV table; B4 period
summary section (last 5 vs prior 5); closed-only indicator and table
inputs (§6.4.1, A4 empirical); N13 ticker header Last: with timestamp;
F-O3 Last bar vol label with explicit SMA(20) period. Wrapper docstring
includes the "Related perception tools" fact-only tail (§6.1).

Spec §4 / §6.1 / §6.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Verify commit**

Run:

```bash
git log --oneline -4
```

Expected: top four commits include feat(gmd).

---

# Task 5 — Commit 5: MTS upgrade

Spec ref: §3 (full); §6.1 wrapper tail.

**Files:**
- Modify: `src/agent/tools_perception.py` lines 1423-1529 (`get_multi_timeframe_snapshot` body rewritten)
- Modify: `src/agent/trader.py` lines 365-378 (wrapper docstring + "Related" tail)
- Create: new test class `TestMTSGolden` in `tests/test_iter_w2r2_next_d_goldens.py`

## 5.1 Golden tests for MTS (TDD)

- [ ] **Step 1: Append MTS golden tests**

Append to `tests/test_iter_w2r2_next_d_goldens.py`:

```python
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
```

- [ ] **Step 2: Run goldens to confirm failures**

Run:

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestMTSGolden -v 2>&1 | tail -15
```

Expected: 6 failures.

## 5.2 Rewrite `get_multi_timeframe_snapshot`

- [ ] **Step 3: Rewrite the function body**

Open `src/agent/tools_perception.py` lines 1423-1529. Replace the entire body with:

```python
async def get_multi_timeframe_snapshot(deps: TradingDeps, tfs: list[str] | None = None) -> str:
    """Multi-timeframe snapshot: ticker (authoritative current price) plus a cross-tf MA fast-vs-slow direction line plus per-tf rows containing momentum (live ticker vs primary MA, %), fast-vs-slow MA structure (MA names with raw values and comparison operator; weekly/monthly tfs use degraded (20, 50) periods marked with " (short-structure)"), volatility (ATR % of price and its ratio vs 20-period ATR average), range position (live ticker price within the last 20 closed-bar high-low, 0% = low / 100% = high), and the most recent 3 closed candle closes with the close timestamp.

    All moving averages are simple moving averages (SMA) computed on the closed-bar series only (excluding the in-progress bar). Per-tf MA values are rendered inline in the Structure column; the Momentum column shows the percentage from live ticker to the primary MA on each tf. ATR(14) is computed via _atr_series (mamode='rma' algorithm lock per spec §6.4.2); shared 4h/1d signals also surfaced by HTF use the same SMA formula and the same _atr_series helper, so identical inputs produce identical values by construction (§2.2.1 algorithm-lock invariant; end-to-end verified by test_mts_htf_overlap_values_match).

    Args:
        tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"].

    Example call:
        get_multi_timeframe_snapshot()
    Example output:
        === Multi-TF Snapshot (BTC/USDT:USDT) ===
        Last (ticker @ 14:23:08 UTC): 81870.50
        MA fast-vs-slow per tf: 5m below | 1h above | 4h above | 1d below
        Columns: ...

        [5m]  Mom -0.3% (vs MA20) | MA20: 81960 < MA50: 82150 | ATR 0.15% (20p avg 0.18%, 0.83×) | Range pos 65%
              Last 3 closes (closed @ 2026-05-11 14:20 UTC): 81870→81848→81870
        ... (3 more tf rows)

    Degradation: per-TF "insufficient data" or "temporarily unavailable"; overall returns header-only error if all TFs fail or ticker fetch fails.

    Related perception tools (factual capability surface, not a calling order):
        - get_market_data: single-timeframe depth output — full RSI / MACD / BB / Volume ratio indicators, market context, a 30-candle OHLCV table with anomaly markers, and a period summary (last 5 vs prior 5 closed candles).
        - get_higher_timeframe_view: long-term structural anchors output — raw MA50/100/200 values with slopes and MA stack, 100-period range with bars-ago, volume regime, ATR regime, across one or more higher timeframes.
    """
    import asyncio
    import pandas as pd
    from datetime import datetime, timezone
    from src.utils.ohlcv_utils import _live_price, _closed_bars, _atr_series

    symbol = deps.symbol
    if tfs is None:
        tfs = ["5m", "1h", "4h", "1d"]

    try:
        ticker = await deps.market_data.get_ticker(symbol)
        live_price = _live_price(ticker)
    except Exception:
        logger.exception("get_multi_timeframe_snapshot ticker fetch failed for %s", symbol)
        return f"=== Multi-TF Snapshot ({symbol}) ===\nError: Temporarily unavailable."

    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    async def _fetch_one(tf: str) -> tuple[str, pd.DataFrame | Exception]:
        try:
            df = await deps.market_data.get_ohlcv_dataframe(
                symbol, tf, limit=MULTI_TF_OHLCV_LIMIT.get(tf, 250),
            )
            return tf, df
        except Exception as e:
            return tf, e

    results = await asyncio.gather(*[_fetch_one(tf) for tf in tfs])

    if all(isinstance(r[1], Exception) for r in results):
        return (
            f"=== Multi-TF Snapshot ({symbol}) ===\n"
            f"Error: Temporarily unavailable (all timeframes failed)."
        )

    # First pass: compute MA fast-vs-slow direction tags per tf.
    direction_tags: list[str] = []
    rows: list[str] = []

    # Fixed seconds per tf, used to derive the "close @ T UTC" timestamp on
    # the Last 3 closes line. For 1M the fixed 30-day step is an approximation
    # (real months range 28-31 days) — when df has more than one closed bar
    # available, the implementation below prefers `df['timestamp'].iloc[-1]`
    # (the in-progress candle's open = the just-closed candle's close moment)
    # over this constant, which is exact for all tfs at the cost of one
    # row's data availability. The constant remains the fallback when the
    # next-bar timestamp is absent.
    _TF_SECONDS = {
        "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
        "4h": 14400, "1d": 86400, "1w": 7 * 86400, "1M": 30 * 86400,
    }

    for tf, df_or_err in results:
        primary_n = MULTI_TF_PRIMARY_MA.get(tf, 50)
        fast_n, slow_n = MULTI_TF_STRUCTURE_MAS.get(tf, (50, 200))
        if isinstance(df_or_err, Exception):
            rows.append(f"[{tf}]  temporarily unavailable")
            continue
        df = df_or_err
        df_closed = _closed_bars(df)
        if df_closed.empty or len(df_closed) < max(slow_n, 20) + 1:
            rows.append(f"[{tf}]  insufficient data (need {slow_n + 1} candles, got {len(df_closed)})")
            continue

        close = df_closed["close"]
        ma_fast = float(close.rolling(fast_n).mean().iloc[-1])
        ma_slow = float(close.rolling(slow_n).mean().iloc[-1])
        primary_ma = float(close.rolling(primary_n).mean().iloc[-1])

        direction_tags.append(f"{tf} {'above' if ma_fast > ma_slow else 'below'}")

        mom_pct = (live_price - primary_ma) / primary_ma * 100.0 if primary_ma > 0 else 0.0
        diff_pct = abs(ma_fast - ma_slow) / ma_slow * 100.0 if ma_slow > 0 else 0.0
        if diff_pct < 0.1:
            op = "≈"
        elif ma_fast > ma_slow:
            op = ">"
        else:
            op = "<"
        struct_str = f"MA{fast_n}: {ma_fast:.2f} {op} MA{slow_n}: {ma_slow:.2f}"
        # 1w/1M use (20, 50) instead of native (50, 200) due to weekly/monthly
        # history shortage in the MTS 20-bar window context — mark as degraded
        # so the agent reads them as fact-with-caveat, not as native structure
        # (spec §5.3; preserved from baseline tools_perception.py:1506-1509).
        if tf in ("1w", "1M"):
            struct_str += " (short-structure)"

        # ATR%, ratio
        atr_str = "ATR N/A"
        if len(df_closed) >= 35:
            atr_series = _atr_series(df_closed, period=14)
            atr_now = float(atr_series.iloc[-1])
            atr_avg = float(atr_series.rolling(20).mean().iloc[-1])
            atr_pct = atr_now / live_price * 100.0
            atr_ratio = atr_now / atr_avg if atr_avg > 0 else 0.0
            atr_str = f"ATR {atr_pct:.2f}% (20p avg {atr_avg / live_price * 100:.2f}%, {atr_ratio:.2f}×)"

        # Range pos (no clamping, per §3.2)
        last_20 = df_closed.iloc[-MULTI_TF_RANGE_PERIODS:]
        hi = float(last_20["high"].max())
        lo = float(last_20["low"].min())
        range_pct = (live_price - lo) / (hi - lo) * 100.0 if hi != lo else 0.0

        # Last 3 closes line — "closed @ T UTC" anchor. Prefer the in-progress
        # candle's timestamp (df.iloc[-1]['timestamp']) which equals the
        # just-closed candle's official close moment exactly; fall back to
        # last_closed_ts + _TF_SECONDS only if df has no in-progress bar at all.
        # Exact for 1M (no 30-day approximation drift).
        if len(df) > len(df_closed):
            close_dt = datetime.fromtimestamp(
                int(df["timestamp"].iloc[-1]) / 1000, tz=timezone.utc
            )
        else:
            last_closed_ts_ms = int(df_closed["timestamp"].iloc[-1])
            close_moment_s = last_closed_ts_ms / 1000 + _TF_SECONDS.get(tf, 0)
            close_dt = datetime.fromtimestamp(close_moment_s, tz=timezone.utc)
        closes_3 = df_closed["close"].iloc[-3:].tolist()
        last3_str = "→".join(f"{c:.2f}" for c in closes_3)

        row1 = (
            f"[{tf}]  Mom {mom_pct:+.1f}% (vs MA{primary_n}) | {struct_str} | "
            f"{atr_str} | Range pos {range_pct:.0f}%"
        )
        row2 = f"      Last 3 closes (closed @ {close_dt.strftime('%Y-%m-%d %H:%M')} UTC): {last3_str}"
        rows.append(row1)
        rows.append(row2)
        rows.append("")

    header_lines = [
        f"=== Multi-TF Snapshot ({symbol}) ===",
        f"Last (ticker @ {fetch_ts} UTC): {live_price:.2f}",
        f"MA fast-vs-slow per tf: " + " | ".join(direction_tags) if direction_tags else "MA fast-vs-slow per tf: (no data)",
        "Columns: Momentum (live ticker vs primary MA, %) | Structure (fast MA value vs slow MA value, with comparison) | Volatility (ATR % of price; ratio vs 20-period ATR avg) | Range pos (live ticker price within 20-bar closed-bar high-low; 0%=Low, 100%=High) | Last 3 closed candle closes",
        "",
    ]
    return "\n".join(header_lines + rows).rstrip()
```

## 5.3 Wrapper docstring in `trader.py`

- [ ] **Step 4: Replace MTS wrapper docstring**

Open `src/agent/trader.py` lines 365-378 and replace the entire `@tool async def get_multi_timeframe_snapshot` block with the mirror of the impl-function docstring from Step 3, wrapped in the @tool form:

```python
    @tool
    async def get_multi_timeframe_snapshot(ctx: RunContext[TradingDeps], tfs: list[str] | None = None) -> str:
        """Multi-timeframe snapshot: ticker (authoritative current price) plus a cross-tf MA fast-vs-slow direction line plus per-tf rows containing momentum (live ticker vs primary MA, %), fast-vs-slow MA structure (MA names with raw values and comparison operator; weekly/monthly tfs use degraded (20, 50) periods marked with " (short-structure)"), volatility (ATR % of price and its ratio vs 20-period ATR average), range position (live ticker price within the last 20 closed-bar high-low, 0% = low / 100% = high), and the most recent 3 closed candle closes with the close timestamp.

        All moving averages are simple moving averages (SMA) computed on the closed-bar series only (excluding the in-progress bar). Per-tf MA values are rendered inline in the Structure column; the Momentum column shows the percentage from live ticker to the primary MA on each tf.

        Args:
            tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"].

        Example call:
            get_multi_timeframe_snapshot()
        Example output:
            === Multi-TF Snapshot (BTC/USDT:USDT) ===
            Last (ticker @ 14:23:08 UTC): 81870.50
            MA fast-vs-slow per tf: 5m below | 1h above | 4h above | 1d below
            Columns: ...

            [5m]  Mom -0.3% (vs MA20) | MA20: 81960 < MA50: 82150 | ATR 0.15% (20p avg 0.18%, 0.83×) | Range pos 65%
                  Last 3 closes (closed @ 2026-05-11 14:20 UTC): 81870→81848→81870
            ... (3 more tf rows)

        Degradation: per-TF "insufficient data" or "temporarily unavailable"; overall returns header-only error if all TFs fail or ticker fetch fails.

        Related perception tools (factual capability surface, not a calling order):
            - get_market_data: single-timeframe depth output — full RSI / MACD / BB / Volume ratio indicators, market context, a 30-candle OHLCV table with anomaly markers, and a period summary (last 5 vs prior 5 closed candles).
            - get_higher_timeframe_view: long-term structural anchors output — raw MA50/100/200 values with slopes and MA stack, 100-period range with bars-ago, volume regime, ATR regime, across one or more higher timeframes.
        """
        from src.agent.tools_perception import get_multi_timeframe_snapshot as _impl

        return await _impl(ctx.deps, tfs=tfs)
```

The wrapper docstring is identical text to the impl-function docstring in Step 3 (spec §6.2 two-layer convention satisfied in one edit per surface).

## 5.4 Run goldens + suite

- [ ] **Step 5: Run MTS goldens**

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestMTSGolden -v
```

Expected: 6 passed.

- [ ] **Step 6: Run full suite**

```bash
python -m pytest -q 2>&1 | tail -10
```

Expected: all green; if `tests/test_toolkit_iter2.py::test_*` assertions on `"Current price:"` fail, defer to Task 9 (test sweep) via `pytest.mark.xfail(reason="toolkit_iter2 MTS sweep deferred to Task 9")`.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_iter_w2r2_next_d_goldens.py tests/test_toolkit_iter2.py
git commit -m "$(cat <<'EOF'
feat(mts): cycle-opening primary — MA values column + Last 3 closes

get_multi_timeframe_snapshot rewritten as the cycle-opening primary:
authoritative ticker with fetch timestamp; MA fast-vs-slow per tf
count-based direction line; per-tf row with Momentum (live ticker vs
primary MA), Structure (raw fast/slow MA values + comparison),
Volatility (ATR% + 20-period ratio), Range pos (no clamping per §3.2),
and a "Last 3 closes (closed @ T UTC)" line per tf. Closed-only
indicator inputs; Last: from ticker.last (§6.4). Wrapper docstring
includes the "Related perception tools" fact-only tail (§6.1).

Spec §3 / §6.1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Verify commit**

Run:

```bash
git log --oneline -5
```

Expected: top 5 commits include feat(mts).

---

# Task 6 — Commit 6: `get_price_pivots` label unification

Spec ref: §6.3 N13 expansion.

Brings perception-tool header label unification to 4 of 4 tools (MTS / GMD / HTF / pivots). Section dividers `=== Levels Above Current Price ===` / `=== Levels Below Current Price ===` are **retained** as prose per spec §6.3.

**Files:**
- Modify: `src/agent/tools_perception.py` line 1709

## 6.1 Test (TDD)

- [ ] **Step 1: Write failing test**

Append to `tests/test_iter_w2r2_next_d_goldens.py`:

```python
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
        # exchange / other deps not needed for pivots happy path with mocked fetches
        out = await get_price_pivots(deps)
        assert "Last: 81870.50" in out
        assert "Current Price:" not in out
        # Section dividers retained
        assert "=== Levels Above Current Price ===" in out
        assert "=== Levels Below Current Price ===" in out
        # No thousand separator on the header price
        import re
        assert not re.search(r"Last: \d{1,3},\d{3}", out)
```

- [ ] **Step 2: Confirm failure**

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestPivotsLabel -v 2>&1 | tail -10
```

Expected: 1 failure on `Last: 81870.50` not in result (current header is `Current Price: 81,870.50`).

- [ ] **Step 3: Apply the label change**

Open `src/agent/tools_perception.py` line 1709 and change:

```python
        f"Current Price: {current_price:,.2f}",
```

to:

```python
        f"Last: {current_price:.2f}",
```

- [ ] **Step 4: Run test to confirm pass**

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestPivotsLabel -v
```

Expected: PASS.

- [ ] **Step 5: Full suite check (display_cycle pivots snapshot will need a follow-up in Task 9)**

```bash
python -m pytest -q 2>&1 | tail -10
```

If `tests/test_display_cycle.py` snapshot at lines 1731/1744/1775 fails on `Current Price:` substring, mark with `pytest.mark.xfail(reason="display_cycle pivots snapshot rewrite deferred to Task 9")`.

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_w2r2_next_d_goldens.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
chore(perception): get_price_pivots header Last: for cross-tool consistency

§6.3 expansion: brings the 4th perception-tool header (MTS / GMD / HTF /
pivots) to the unified Last: label. Section dividers
"=== Levels Above/Below Current Price ===" retained as prose dividers
per spec §6.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 7 — Commit 7: `get_position` Liquidation deduplication

Spec ref: §6.3 F-P2.

The Risk Exposure section's `Liquidation:` line at `tools_perception.py:258/260` is the richer form with `(P% away = Q× ATR(1h))`; the duplicate at line 191 in the Position section is removed.

**Files:**
- Modify: `src/agent/tools_perception.py` lines 190-191

## 7.1 Test (TDD)

- [ ] **Step 1: Write failing test**

Append to `tests/test_iter_w2r2_next_d_goldens.py`:

```python
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
        deps.exchange = MagicMock()
        deps.exchange.fetch_positions = AsyncMock(return_value=[p])
        deps.exchange.fetch_balance = AsyncMock(
            return_value=SimpleNamespace(total_usdt=10000.0, used_usdt=80.0)
        )
        deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
        deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
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
```

- [ ] **Step 2: Confirm failure**

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestPositionLiquidationDedup -v 2>&1 | tail -10
```

Expected: 1 failure on `"Liquidation:" not in position_section` because the current code emits it at line 191.

- [ ] **Step 3: Remove the line**

Open `src/agent/tools_perception.py` lines 190-191. Replace:

```python
        if p.liquidation_price is not None:
            pos_lines.append(f"Liquidation: {p.liquidation_price:,.2f}")
        pos_lines.append(f"Unrealized: {p.unrealized_pnl:+.2f} USDT")
```

with:

```python
        # F-P2: Liquidation lives in Risk Exposure section (richer form with
        # `(P% away = Q× ATR(1h))`); deduplicated from Position section.
        pos_lines.append(f"Unrealized: {p.unrealized_pnl:+.2f} USDT")
```

- [ ] **Step 4: Run test + full suite**

```bash
python -m pytest tests/test_iter_w2r2_next_d_goldens.py::TestPositionLiquidationDedup -v
python -m pytest -q 2>&1 | tail -10
```

Expected: golden passes. If any `test_position*` test asserts `Liquidation:` count in the full output, it should still pass (the Risk Exposure form is kept); if it asserts on the Position section specifically, update it inline to match the new layout.

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_w2r2_next_d_goldens.py
git commit -m "$(cat <<'EOF'
chore(get_position): F-P2 deduplicate Liquidation line

The Liquidation: line in the Position section is removed; the Risk
Exposure section retains the richer "Liquidation: V (P% away = Q×
ATR(1h))" form. Removes the redundant first surface so the agent
reads a single canonical liquidation fact.

Spec §6.3 F-P2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Task 8 — Commit 8: cross-tool drift-guard tests

Spec ref: §7.1 (all 6 invariants).

The 6 drift-guard tests lock in the §2.2.1 algorithm-lock invariant (end-to-end), the closed-only contract, and the cross-tool `Last:` derivation. They are the final commit so they exercise every tool change landed by commits 3-7.

**Files:**
- Create: `tests/test_multi_tf_drift_guards.py`

## 8.1 Implement the 6 drift-guard tests

- [ ] **Step 1: Create the test module**

Create `tests/test_multi_tf_drift_guards.py`:

```python
"""Cross-tool drift-guard tests for iter w2r2-next-d (spec §7.1).

Six invariants:
1. test_indicator_temporal_stability_within_candle
2. test_live_price_field_equals_ticker_last
3. test_three_tools_use_same_ticker_last_in_Last_label
4. test_no_in_progress_candle_in_indicator_inputs
5. test_mts_htf_overlap_values_match (§2.2.1)
6. test_atr_series_last_value_equals_compute_indicators_atr_14 (§6.4.2)

These tests purposely use mocked deps and hand-crafted OHLCV fixtures
so the invariants can be asserted bit-for-bit, without the runtime
variability of live OHLCV fetches.
"""
from __future__ import annotations
import re
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock

from tests.fixtures.multi_tf_ohlcv import (
    df_4h_250bars, df_1d_250bars, df_5m_130bars, df_1h_250bars,
    df_5m_anomaly, fake_ticker_81870, _build,
)


def _build_deps(ticker, ohlcv_by_tf, symbol="BTC/USDT:USDT"):
    from src.services.technical import TechnicalAnalysisService
    deps = MagicMock()
    deps.symbol = symbol
    deps.timeframe = "5m"
    deps.technical = TechnicalAnalysisService()
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)

    async def _ohlcv(sym, tf, limit):
        return ohlcv_by_tf[tf]

    deps.market_data.get_ohlcv_dataframe = AsyncMock(side_effect=_ohlcv)
    return deps


def test_indicator_temporal_stability_within_candle(df_4h_250bars):
    """A4: closed-only MA(5) is stable across in-progress mutations;
    full-df MA(5) drifts when the in-progress bar's close changes."""
    from src.utils.ohlcv_utils import _closed_bars
    df_a = df_4h_250bars.copy()
    df_b = df_4h_250bars.copy()
    # Mutate the in-progress bar only
    df_b.loc[df_b.index[-1], "close"] = float(df_b.loc[df_b.index[-1], "close"]) + 1000.0

    closed_a = _closed_bars(df_a)["close"].rolling(5).mean().iloc[-1]
    closed_b = _closed_bars(df_b)["close"].rolling(5).mean().iloc[-1]
    full_a = df_a["close"].rolling(5).mean().iloc[-1]
    full_b = df_b["close"].rolling(5).mean().iloc[-1]

    assert closed_a == pytest.approx(closed_b, rel=0, abs=0), "closed-only MA must be stable"
    assert full_a != full_b, "full-df MA must drift when in-progress close moves"


@pytest.mark.asyncio
async def test_live_price_field_equals_ticker_last(
    fake_ticker_81870, df_4h_250bars, df_1d_250bars,
):
    """Last: header in HTF (and others) derives from ticker.last."""
    from src.agent.tools_perception import get_higher_timeframe_view
    deps = _build_deps(
        fake_ticker_81870, {"4h": df_4h_250bars, "1d": df_1d_250bars},
    )
    out = await get_higher_timeframe_view(deps, timeframes=["4h"])
    assert "Last: 81870.50" in out


@pytest.mark.asyncio
async def test_three_tools_use_same_ticker_last_in_Last_label(
    fake_ticker_81870, df_4h_250bars, df_1d_250bars,
    df_5m_130bars, df_1h_250bars,
):
    """MTS / GMD / HTF all surface ticker.last in their Last: line."""
    from src.agent.tools_perception import (
        get_market_data, get_higher_timeframe_view, get_multi_timeframe_snapshot,
    )
    deps_mts = _build_deps(
        fake_ticker_81870,
        {"5m": df_5m_130bars, "1h": df_1h_250bars,
         "4h": df_4h_250bars, "1d": df_1d_250bars},
    )
    deps_gmd = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
    deps_htf = _build_deps(fake_ticker_81870, {"4h": df_4h_250bars})

    out_mts = await get_multi_timeframe_snapshot(deps_mts)
    out_gmd = await get_market_data(deps_gmd)
    out_htf = await get_higher_timeframe_view(deps_htf, timeframes=["4h"])

    # MTS header: "Last (ticker @ T UTC): 81870.50"
    assert re.search(r"Last \(ticker @ \d{2}:\d{2}:\d{2} UTC\): 81870\.50", out_mts)
    # GMD ticker header: "Last: 81870.50 | Bid: ..."
    assert "Last: 81870.50 |" in out_gmd
    # HTF header: "Last: 81870.50"
    assert "Last: 81870.50" in out_htf


@pytest.mark.asyncio
async def test_no_in_progress_candle_in_indicator_inputs(
    fake_ticker_81870, df_5m_130bars,
):
    """A4 enforcement: mutating the in-progress bar must NOT change
    rendered indicator values in GMD (MA20 / MA50 / RSI / etc.)."""
    from src.agent.tools_perception import get_market_data
    df_a = df_5m_130bars.copy()
    df_b = df_5m_130bars.copy()
    df_b.loc[df_b.index[-1], "close"] = 99999.0
    df_b.loc[df_b.index[-1], "volume"] = 99999.0

    deps_a = _build_deps(fake_ticker_81870, {"5m": df_a})
    deps_b = _build_deps(fake_ticker_81870, {"5m": df_b})

    out_a = await get_market_data(deps_a)
    out_b = await get_market_data(deps_b)

    # Extract the Technical Indicators section from both outputs
    def _indicators_block(out: str) -> str:
        return out.split("=== Technical Indicators")[1].split("===")[0]

    assert _indicators_block(out_a) == _indicators_block(out_b), (
        "Indicator block must be identical regardless of in-progress mutation"
    )


@pytest.mark.asyncio
async def test_mts_htf_overlap_values_match(
    fake_ticker_81870, df_4h_250bars, df_1d_250bars,
):
    """§2.2.1: at shared tfs (4h, 1d), MTS and HTF render the same
    MA50 / MA200 / ATR-ratio values, given identical fixture OHLCV.

    End-to-end test: invokes both tools through the same mocked
    MarketDataService, regex-extracts MA50 / MA200 / ATR-ratio from
    each rendered output, and asserts equality. This catches both
    compute drift (one side deviates from the shared SMA / _atr_series
    primitives) and render-side bugs (a wrong attribute surfaces in the
    MA50 slot). MA100 and slope are HTF-only and not part of the
    overlap; MTS does not surface them.
    """
    import re
    from src.agent.tools_perception import (
        get_multi_timeframe_snapshot, get_higher_timeframe_view,
    )

    # Single mock layer feeds both tools the SAME fixture OHLCV.
    deps = _build_deps(
        fake_ticker_81870,
        {"4h": df_4h_250bars, "1d": df_1d_250bars,
         # MTS asks for 5m/1h too on default tfs; supply benign fixtures.
         "5m": df_4h_250bars, "1h": df_4h_250bars},
    )

    out_mts = await get_multi_timeframe_snapshot(deps, tfs=["4h", "1d"])
    out_htf = await get_higher_timeframe_view(deps, timeframes=["4h", "1d"])

    def _extract_mts(out: str, tf: str) -> dict[str, float]:
        """Extract MA50, MA200, ATR-ratio from MTS row at given tf."""
        # MTS row: "[4h]  Mom ... | MA50: 79200.00 < MA200: 76200.00 | ATR X.XX% (20p avg Y.YY%, Z.ZZ×) | ..."
        section = re.search(rf"\[{tf}\][^\n]*", out)
        assert section, f"MTS missing [{tf}] row\n{out}"
        row = section.group(0)
        ma50 = float(re.search(r"MA50:\s*(\d+\.\d+)", row).group(1))
        ma200 = float(re.search(r"MA200:\s*(\d+\.\d+)", row).group(1))
        atr_ratio = float(re.search(r"(\d+\.\d+)×", row).group(1))
        return {"ma50": ma50, "ma200": ma200, "atr_ratio": atr_ratio}

    def _extract_htf(out: str, tf: str) -> dict[str, float]:
        """Extract MA50, MA200, ATR-ratio from HTF section at given tf."""
        # HTF section is multiline; capture from "[4h]" to next "[" or end.
        m = re.search(rf"\[{tf}\][^[]*", out, flags=re.DOTALL)
        assert m, f"HTF missing [{tf}] section\n{out}"
        sec = m.group(0)
        ma50 = float(re.search(r"MA50:\s*(\d+\.\d+)", sec).group(1))
        ma200 = float(re.search(r"MA200:\s*(\d+\.\d+)", sec).group(1))
        atr_ratio = float(re.search(r"(\d+\.\d+)× vs 20-period", sec).group(1))
        return {"ma50": ma50, "ma200": ma200, "atr_ratio": atr_ratio}

    for tf in ("4h", "1d"):
        mts_vals = _extract_mts(out_mts, tf)
        htf_vals = _extract_htf(out_htf, tf)
        assert mts_vals == htf_vals, (
            f"§2.2.1 invariant violated at {tf}: MTS={mts_vals} HTF={htf_vals}"
        )


def test_atr_series_last_value_equals_compute_indicators_atr_14(df_4h_250bars):
    """§6.4.2: _atr_series(df_closed, 14).iloc[-1] bit-equals
    compute_indicators(df_closed)['atr_14']. Already enforced in
    tests/test_ohlcv_utils.py; replicated here so the cross-tool
    drift-guard module is self-contained and reads as a complete spec
    §7.1 inventory."""
    from src.utils.ohlcv_utils import _closed_bars, _atr_series
    from src.services.technical import TechnicalAnalysisService

    df_closed = _closed_bars(df_4h_250bars)
    series_last = _atr_series(df_closed, period=14).iloc[-1]
    scalar = TechnicalAnalysisService().compute_indicators(df_closed)["atr_14"]
    assert series_last == pytest.approx(scalar, rel=0, abs=0)
```

- [ ] **Step 2: Run the drift-guards**

```bash
python -m pytest tests/test_multi_tf_drift_guards.py -v
```

Expected: 6 passed.

- [ ] **Step 3: Run full suite — final non-regression check**

```bash
python -m pytest -q 2>&1 | tail -10
```

Expected: all green. If any `tests/test_display_cycle.py` or `tests/test_toolkit_iter2.py` tests are still marked `xfail` from earlier tasks, address them now (Task 9 sweep is done inline here before the final commit).

## 8.2 Final display_cycle + toolkit_iter2 sweep

- [ ] **Step 4: Resolve any remaining xfail markers**

Run:

```bash
python -m pytest -q -rX 2>&1 | tail -20
```

For each `xfail`'d test:

1. Inspect the actual output the tool produces now (`python -m pytest <test> -v -s 2>&1 | tail -30`).
2. Rewrite the snapshot or assertion to match the new layout.
3. Remove the `xfail` marker.

**Before editing — enumerate every Price-label hit** so no site is missed (audit 2026-05-11 returned 36 hits across `test_display_cycle.py` plus 2 in `test_toolkit_iter2.py`):

```bash
grep -n 'Current Price\|Current price\|Price: \|"Price:' tests/test_display_cycle.py
grep -n '"Current price:"' tests/test_toolkit_iter2.py
```

Apply these transformations site-by-site, using the Edit tool — never mass-rewrite with sed (mass-rewriting catches the prose section dividers `=== Levels Above/Below Current Price ===` which must be **retained** per spec §6.3):

| Pattern in current file | Replacement |
|---|---|
| `"Price: <num>.<dec> | Bid: ..."` standalone | `"Last: <num>.<dec> | Bid: ..."` (GMD ticker rendered without the `@ T UTC` stamp on snapshots that did not capture the timestamp; for newly-touched snapshots, include `@ T UTC` via regex `re.search`, not string-equality) |
| `"Current Price: <num>,<num>.<dec>"` (with thousand-separator) | `"Last: <numeric without comma>"` (label change + thousand-separator removal per §6.5) |
| `"Current price: <num>.<dec>"` (lower-case, MTS-style) | regex `re.search(r"Last \(ticker @ \d{2}:\d{2}:\d{2} UTC\): <num>\.<dec>", out)` — switch from substring to regex because the timestamp is dynamic |
| `"=== Levels Above Current Price ==="` | **retain unchanged** (prose section divider per §6.3) |
| `"=== Levels Below Current Price ==="` | **retain unchanged** |
| `"Current price within range"` prose (test_perception_tools_n3.py line 91) | **retain unchanged** — natural-language reference, not a label |
| HTF snapshot blocks (the 4-section MA Distances / Range Position / 20-period Band layout) | the new HTF output is significantly different (per-tf section, MA stack, slope, etc.); rebuild each snapshot's `expected` literal by capturing the new output via `pytest -v -s` and asserting verbatim, then remove the `xfail` marker |

For each file edited, run the file's full test set after to confirm no other failures introduced:

```bash
python -m pytest tests/test_display_cycle.py -q
python -m pytest tests/test_toolkit_iter2.py -q
```

- [ ] **Step 5: Verify-before-completion — full suite final pass**

Run:

```bash
python -m pytest 2>&1 | tail -5
python -m pytest --collect-only -q 2>&1 | tail -3
```

Expected:
- All tests passing (`X passed, Y skipped` — Y stays ≥ 5).
- Total collected ≥ 1487 (baseline) + tests added (Task 2: 6, Task 3: 11, Task 4: 7, Task 5: 6, Task 6: 1, Task 7: 1, Task 8: 6 = +38 → 1525+).

If any test fails: do NOT commit. Diagnose, fix, then re-run.

## 8.3 Empirical re-verification (manual smoke)

- [ ] **Step 6: Per §10 verify the OHLCV semantics script still runs**

This step is **manual/optional in the local dev cycle** (it hits OKX live data and takes ≥ 90 seconds — long-walltime per `feedback_long_walltime_experiments` memory). Defer to the user. Note in the PR description that the script was run and A4 still passed (user will report manually after running):

```bash
python scripts/verify_ohlcv_semantics_v2.py
```

Expected output marker: `A4: PASS — closed-only stable, full-df drifts`.

- [ ] **Step 7: Final commit**

```bash
git add tests/test_multi_tf_drift_guards.py tests/test_display_cycle.py tests/test_toolkit_iter2.py
git commit -m "$(cat <<'EOF'
test: cross-tool drift-guards for multi-TF path reversal

Adds tests/test_multi_tf_drift_guards.py with the six invariants from
spec §7.1: (1) indicator temporal stability on closed-only inputs;
(2) Last: field derived from ticker.last; (3) MTS / GMD / HTF all
surface ticker.last in their Last: line; (4) in-progress bar
mutation does not change rendered indicator values; (5) §2.2.1
MTS / HTF shared-tf signals computed through identical helper path;
(6) §6.4.2 ATR series last value bit-equals compute_indicators atr_14.

Also completes the display_cycle / toolkit_iter2 test sweep:
"Price:" / "Current Price:" / "Current price:" literals updated to
the unified Last: / Last (ticker @ ...): forms per §6.3.

Spec §7.1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8: Verify final state**

```bash
git log --oneline -8
git status
```

Expected:
- 8 commits since `06b1e6d` (or 9 if we count the spec commit) — counting from this PR: `docs(plan)`, `refactor(technical+utils)`, `feat(htf)`, `feat(gmd)`, `feat(mts)`, `chore(perception)`, `chore(get_position)`, `test:`.
- `git status` clean (no unstaged changes).

---

# Task 9 — Wrap-up: PR readiness

This task gathers PR-creation metadata; no code changes.

- [ ] **Step 1: Confirm test counts and PR diff size**

```bash
python -m pytest --collect-only -q 2>&1 | tail -3
git diff --stat 06b1e6d..HEAD | tail -3
```

Record: total test count delta (+~38), total diff lines (target 2000-2400 per spec §8).

- [ ] **Step 2: Show user the commit log + invite review**

Per `feedback_review_before_commit` and `feedback_no_pr_comment`, do NOT open a PR or push autonomously. Show the user:

```bash
git log --oneline 06b1e6d..HEAD
```

and ask whether to push the branch and open a PR (or whether they want to review one more time first).

- [ ] **Step 3: When user approves, push and open PR**

Run (only after user approval):

```bash
git push -u origin iter-w2r2-next-d/multi-tf
gh pr create --title "feat(iter-w2r2-next-d): multi-TF path reversal — MTS / GMD / HTF realignment" --body "$(cat <<'EOF'
## Summary
- MTS upgrade: cycle-opening primary — adds MA-direction line, MA-values column, Last-3-closes line, ticker-as-of timestamp. Header `Last:` from `ticker.last` (spec §3).
- GMD retreat: single-TF depth — default `candle_count` 50→30, B3 anomaly markers, B4 period summary, closed-only indicator + table inputs, N13 / F-O3 labels (spec §4).
- HTF list-form + N6 G1-G5: `timeframes: list[Literal[...]]` signature; per-tf MA50/100/200 with slope + MA stack + 100-period range with full date stamp + volume regime + ATR regime; 1M uses (12, 24, 60) periods (spec §5).
- Cross-cutting: shared `src/utils/ohlcv_utils.py` (three primitives: _live_price / _closed_bars / _atr_series) provides the algorithm-lock primitives the §2.2.1 invariant rests on; F-O2 BB labels; F-P2 Position-section Liquidation dedup; N13 unification on `get_price_pivots`; wrapper-docstring "Related perception tools" tail on all three tools (spec §6.1 — Layer-1 intentionally untouched).
- 6 cross-tool drift-guards (spec §7.1) lock in the invariants.

Spec: `docs/superpowers/specs/2026-05-11-iter-w2r2-next-d-multi-tf-design.md`.
Plan: `docs/superpowers/plans/2026-05-11-iter-w2r2-next-d.md`.

## Test plan
- [x] `tests/test_ohlcv_utils.py` — helpers + F-O2 BB (Task 2)
- [x] `tests/test_iter_w2r2_next_d_goldens.py` — per-tool golden mockups (Tasks 3-7)
- [x] `tests/test_multi_tf_drift_guards.py` — 6 invariants (Task 8)
- [x] `tests/test_perception_tools_n3.py` — HTF list-form migration (Task 3)
- [x] `tests/test_fact_only_wordlist.py` line 555 — HTF positional migration (Task 3)
- [x] `tests/test_display_cycle.py` — Last: label sweep + HTF snapshot rewrite (Task 8)
- [x] `tests/test_toolkit_iter2.py` lines 306 / 372 — MTS Last (ticker @ ...) assertion (Task 8)
- [ ] Manual: `python scripts/verify_ohlcv_semantics_v2.py` — A4 closed-only stability still holds (user runs per `feedback_long_walltime_experiments`)
- [ ] W3+ post-release: validate MTS frequency ≥ 45% MVP / ≥ 60% stretch and GMD ×3 ≤ 50% MVP / ≤ 30% stretch per §7.2

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

PR URL will be returned by `gh` — share with user.

---

## End of Plan

This plan covers spec sections §0-§12. Layer-1 in `persona.py` is intentionally untouched (spec §6.1). Each commit ships green; no commit is allowed to land with a failing test or an active `xfail` marker except where explicitly deferred to Task 8 / Task 9. The 8-commit ordering matches spec §8 verbatim.
