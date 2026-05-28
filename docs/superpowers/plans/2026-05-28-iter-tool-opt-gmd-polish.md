# iter-tool-opt-gmd-polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish `get_market_data` per `docs/superpowers/specs/2026-05-28-iter-tool-opt-gmd-polish-design.md` — 6 issues (1 P1 + 1 P2 + 4 P3) covering RVol column, in-progress candle hint, dead field deletions, and docstring rewrite.

**Architecture:** All changes in `src/agent/tools_perception.py` (get_market_data renderer + helpers) + `src/agent/tools_descriptions.py` (path B description) + `src/agent/trader.py` (path C inner docstring) + `src/utils/ohlcv_utils.py` (shared helpers). No service-layer changes, no schema/DB/migration.

**Tech Stack:** Python 3.13 / pydantic-ai 1.78 / pandas / pytest-asyncio / griffe (docstring parsing for path A; bypassed for path B via `@tool(description=DESC_X)`).

---

## Context for the implementer

### Project conventions (must know before touching code)

- **Path A / Path B / Path C docstring channels** (per memory `project_griffe_example_stripped`):
  - **Path A** = function docstring in `tools_perception.py`. Goes through griffe → `tool.tool_def.description` for LLM. Block-style admonitions (`Example:` + indented block) **get stripped by griffe**. Inline same-line text survives.
  - **Path B** = constant in `tools_descriptions.py` passed via `@tool(description=DESC_X)`. **Bypasses griffe entirely** — block-style admonitions survive verbatim to LLM. This is the **main LLM channel** for tools that have a path B (per PR #59 design).
  - **Path C** = inner docstring at `trader.py:124-140`. Dev-facing only; overridden by `@tool(description=...)`.
  - **CRITICAL**: When updating docstrings, keep **path A inline-style** (block would be stripped) and **path B block-style** (survives, no need to inline). Do NOT inline path B.
- **`_create_dual_mode_tool`** at `trader.py:58` provides `@tool` (no override) and `@tool(description=DESC)` (override) modes. `Args:` section always parsed from docstring via griffe even when description is overridden (drift guard: `require_parameter_descriptions=True`).
- **OHLCV closed-bar semantics**: `_closed_bars(df)` at `src/utils/ohlcv_utils.py:31` strips the in-progress (last) bar. All GMD indicators / OHLCV table rows / period summary work on `df_closed` only.
- **Display window**: `display_count = candle_count` when `available_closed >= candle_count + 50`, else fallback `max(10, available_closed - 50)` (line 109-112). RVol fallback `—` covers the degraded path edge case where SMA(20) hasn't started.
- **Timestamp dispatch quirk**: `display_df["timestamp"]` may be `int` ms-epoch OR `datetime`. Existing code at `tools_perception.py:164-168` already isinstance-dispatches. New helper `_to_pd_timestamp_utc` must mirror this dispatch.

### Test infrastructure

- Test fixtures: `tests/fixtures/multi_tf_ohlcv.py` provides `df_5m_130bars`, `df_5m_anomaly`, `df_4h_250bars`, `df_1d_250bars`, `df_1h_250bars`, `fake_ticker_81870`.
- `tests/fixtures/multi_tf_ohlcv.py:_build()` builds OHLCV with deterministic closes + integer ms timestamps. `df_5m_130bars` has 129 closed + 1 in-progress.
- GMD golden test class: `TestGMDGolden` in `tests/test_iter_w2r2_next_d_goldens.py` (line 178). New tests can go in this class or a new dedicated file.
- `_build_deps()` at `tests/test_iter_w2r2_next_d_goldens.py:18` builds a minimal `TradingDeps` mock with real `TechnicalAnalysisService`.

### Affected existing tests (will need inline fix)

- `tests/test_iter_w2r2_next_d_goldens.py:241-251` `test_gmd_period_summary_section` asserts `"Avg range (H-L):"` — Task 5 deletes this, update the test.
- `tests/test_trader_agent.py:369` `test_get_market_data_description_carries_example_output` asserts Ticker / Recent Candles / Period summary / vol↑ / range↑ — these all remain after path B rewrite, should still pass. **No action required**, but verify it passes after Task 6.
- `tests/test_tool_enhancement.py:641` `test_get_market_data_candle_count_clamp` — behavior unchanged, should still pass. **No action required**.
- Other GMD tests should still pass; verify in Task 7.

### Run tests

```bash
cd /Users/z/Z/TradeBot
uv run pytest tests/test_iter_w2r2_next_d_goldens.py -v   # GMD golden
uv run pytest tests/test_trader_agent.py::test_get_market_data_description_carries_example_output -v
uv run pytest tests/ -v                                    # full suite (~1859 tests)
```

---

## File Structure

| File | Responsibility | Lines changed |
|---|---|---|
| `src/utils/ohlcv_utils.py` | Add `TF_OFFSETS` constant, `_to_pd_timestamp_utc()`, `_fmt_candle_time()` helpers | ~25 |
| `src/agent/tools_perception.py:51-219` | `get_market_data` renderer: add RVol column / in-progress hint / delete N-candle row / delete Avg range; replace inline strftime with `_fmt_candle_time`; update path A docstring (inline-style) | ~40 |
| `src/agent/tools_descriptions.py:48-69` | Rewrite `GET_MARKET_DATA_DESCRIPTION` — keep block-style; update content (RVol column / in-progress hint / removed fields / clamp docstring) | ~25 |
| `src/agent/trader.py:124-140` | Inner docstring (path C) — sync content with path A | ~5 |
| `tests/test_iter_w2r2_next_d_goldens.py` | Add new GMD golden assertions in `TestGMDGolden` class; update `test_gmd_period_summary_section` | ~30 |
| `tests/test_iter_tool_opt_gmd_polish.py` (**new**) | New test file for issues that need finer fixtures (in-progress time arithmetic across tfs, RVol marker consistency, unsupported tf fallback, path B verify) | ~80 |

Estimated src change: **75 lines** (under mini-iter direct-merge cap 100 lines, per `feedback_docs_only_direct_merge`).

---

## Task 1: Foundation — TF_OFFSETS, `_to_pd_timestamp_utc`, `_fmt_candle_time` helpers

**Files:**
- Modify: `src/utils/ohlcv_utils.py` (add at end of module)
- Test: `tests/test_iter_tool_opt_gmd_polish.py` (new file)

These are shared helpers used by Task 2 (RVol column) and Task 3 (in-progress hint). TDD: write helper tests first.

- [ ] **Step 1: Create new test file with failing tests for helpers**

Create `tests/test_iter_tool_opt_gmd_polish.py`:

```python
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
        assert ts.hour == 12


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
```

- [ ] **Step 2: Run tests to verify they all fail**

```bash
cd /Users/z/Z/TradeBot
uv run pytest tests/test_iter_tool_opt_gmd_polish.py -v
```

Expected: all tests FAIL with `ImportError: cannot import name '_to_pd_timestamp_utc' from 'src.utils.ohlcv_utils'` (and similar for TF_OFFSETS / _fmt_candle_time).

- [ ] **Step 3: Implement helpers in `src/utils/ohlcv_utils.py`**

Append to `src/utils/ohlcv_utils.py` (after existing `_atr_series`):

```python
import pandas as pd
from pandas.tseries.offsets import DateOffset


# === iter-tool-opt-gmd-polish: shared helpers ===

TF_OFFSETS: dict[str, pd.Timedelta | DateOffset] = {
    # Intraday minute
    "1m":  pd.Timedelta(minutes=1),
    "3m":  pd.Timedelta(minutes=3),
    "5m":  pd.Timedelta(minutes=5),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    # Hour
    "1h":  pd.Timedelta(hours=1),
    "2h":  pd.Timedelta(hours=2),
    "4h":  pd.Timedelta(hours=4),
    "6h":  pd.Timedelta(hours=6),
    "8h":  pd.Timedelta(hours=8),
    "12h": pd.Timedelta(hours=12),
    # Day / week
    "1d":  pd.Timedelta(days=1),
    "3d":  pd.Timedelta(days=3),
    "1w":  pd.Timedelta(weeks=1),
    # Month (calendar-aware; 28-31 days not fixed)
    "1M":  DateOffset(months=1),
}


def _to_pd_timestamp_utc(ts_val: Any) -> pd.Timestamp:
    """Coerce OHLCV timestamp to tz-aware pd.Timestamp UTC.

    Mirrors the isinstance dispatch at tools_perception.py:164-168 — OHLCV
    timestamp column may be int/float ms-epoch OR datetime depending on the
    exchange adapter. Both produce equivalent UTC pd.Timestamp here.
    """
    if isinstance(ts_val, (int, float)):
        return pd.Timestamp(ts_val, unit="ms", tz="UTC")
    ts = pd.Timestamp(ts_val)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


def _fmt_candle_time(dt: pd.Timestamp, tf: str) -> str:
    """Format a candle's open-time per tf granularity.

    Unified dispatch shared by OHLCV table row rendering AND in-progress
    candle hint rendering (both consumers in tools_perception.get_market_data).

    Unknown tf falls back to `%Y-%m-%d` (matches existing default fallback at
    tools_perception.py:175). Does NOT raise — preserves backward-compat.
    """
    tf_lower = tf.lower()
    if tf_lower in ("1m", "3m", "5m", "15m", "30m"):
        return dt.strftime("%H:%M")
    if tf_lower in ("1h", "2h", "4h", "6h", "8h", "12h"):
        return dt.strftime("%m-%d %H:%M")
    if tf_lower in ("1d", "3d", "1w"):
        return dt.strftime("%Y-%m-%d")
    if tf_lower == "1m" or tf == "1M":  # note: 1M (month) is case-sensitive
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y-%m-%d")  # degraded fallback
```

**Note** on the `1M` branch: the OKX/CCXT timeframe `"1M"` is **case-sensitive** (uppercase M = month, lowercase m = minute). Use `tf == "1M"` not `tf_lower`. The previous branch already covered `"1m"` via `tf_lower`, so the `"1M"` check is unambiguous below it.

Fix the branch order to avoid the ambiguity above. Use this version instead:

```python
def _fmt_candle_time(dt: pd.Timestamp, tf: str) -> str:
    if tf == "1M":  # month — case-sensitive uppercase
        return dt.strftime("%Y-%m")
    tf_lower = tf.lower()
    if tf_lower in ("1m", "3m", "5m", "15m", "30m"):
        return dt.strftime("%H:%M")
    if tf_lower in ("1h", "2h", "4h", "6h", "8h", "12h"):
        return dt.strftime("%m-%d %H:%M")
    if tf_lower in ("1d", "3d", "1w"):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")  # degraded fallback
```

Also add at the top of `ohlcv_utils.py` (after existing imports):
```python
from typing import Any
```
if `Any` is not already imported.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py -v
```

Expected: all 14 helper tests PASS.

- [ ] **Step 5: Run full GMD suite to confirm no regressions**

```bash
uv run pytest tests/test_iter_w2r2_next_d_goldens.py tests/test_tool_enhancement.py tests/test_tools.py tests/test_trader_agent.py -v
```

Expected: all pre-existing tests PASS (helpers are not yet wired into get_market_data).

- [ ] **Step 6: Commit**

```bash
git add src/utils/ohlcv_utils.py tests/test_iter_tool_opt_gmd_polish.py
git commit -m "$(cat <<'EOF'
iter-tool-opt-gmd-polish (1/7): TF_OFFSETS + helpers

Add shared helpers used by RVol column rendering (Task 2) and in-progress
candle hint (Task 3):

- TF_OFFSETS: CCXT 15-tf duration dict (1m/3m/5m/15m/30m/1h/2h/4h/6h/
  8h/12h/1d/3d/1w/1M); 1M uses pd.DateOffset for calendar-aware month
- _to_pd_timestamp_utc: coerce int ms-epoch OR datetime → tz-aware
  pd.Timestamp UTC, mirrors tools_perception.py:164-168 dispatch
- _fmt_candle_time: unified strftime per tf, replaces inline 3-branch
  dispatch at tools_perception.py:169-175; degraded fallback for unknown
  tf returns ISO date (no raise, backward-compat preserved)

No behavior change yet; helpers wired in subsequent tasks.

Spec: docs/superpowers/specs/2026-05-28-iter-tool-opt-gmd-polish-design.md §2.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 议题 1 — `RVol(×SMA20)` column in OHLCV table

**Files:**
- Modify: `src/agent/tools_perception.py:157-198` (OHLCV table rendering)
- Test: `tests/test_iter_tool_opt_gmd_polish.py` (append)

- [ ] **Step 1: Write failing tests for RVol column**

Append to `tests/test_iter_tool_opt_gmd_polish.py`:

```python
# === Task 2: RVol column ===

import pytest
from unittest.mock import AsyncMock, MagicMock

from tests.fixtures.multi_tf_ohlcv import (
    df_5m_130bars, df_5m_anomaly, fake_ticker_81870,
)


def _build_gmd_deps(ticker, ohlcv_by_tf, symbol="BTC/USDT:USDT", tf="5m"):
    """Local copy of _build_deps from test_iter_w2r2_next_d_goldens to avoid
    cross-file imports."""
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
        # Extract OHLCV section
        section = out.split("=== Recent Candles")[1].split("===")[0]
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
        """Issue 1: when bar volume = 6× SMA(20) baseline, RVol shows ~6.00×
        AND vol↑ marker present. Tests common case (not FP-boundary).

        df_5m_anomaly: bar 127 volume = 600 vs SMA baseline 100 → RVol ≈ 6.00×.
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestRVolColumn -v
```

Expected: all 5 tests FAIL (RVol column not yet implemented).

- [ ] **Step 3: Implement RVol column in `src/agent/tools_perception.py`**

Modify `src/agent/tools_perception.py:157-198` (OHLCV table generation block). Replace the existing header line and per-row formatting:

```python
    # === Recent Candles (OHLCV with markers + RVol column) ===
    vol_sma = df_closed["volume"].rolling(20).mean()
    atr_series = _atr_series(df_closed, period=14) if len(df_closed) >= 15 else None
    candle_lines: list[str] = [
        f"{'Time (open UTC)':<16} {'Open':>10} {'High':>10} {'Low':>10} "
        f"{'Close':>10} {'Vol':>10}  {'RVol(×SMA20)':>12}  Markers"
    ]
    for idx in display_df.index:
        row = df_closed.loc[idx]
        ts_val = row["timestamp"]
        # Use shared helper for both pd.Timestamp coercion and tf-aware formatting
        dt = _to_pd_timestamp_utc(ts_val)
        time_str = _fmt_candle_time(dt, timeframe)

        markers: list[str] = []
        vol_sma_at = vol_sma.loc[idx] if idx in vol_sma.index else None
        # Compute RVol; degraded fallback `—` when SMA(20) not yet ready
        if vol_sma_at is not None and not pd.isna(vol_sma_at) and float(vol_sma_at) > 0:
            rvol = float(row["volume"]) / float(vol_sma_at)
            rvol_str = f"{rvol:.2f}×"
            if float(row["volume"]) > 2 * float(vol_sma_at):
                markers.append("vol↑")
        else:
            rvol_str = "—"
        atr_at = None
        if atr_series is not None and idx in atr_series.index:
            atr_at = atr_series.loc[idx]
        if atr_at is not None and not pd.isna(atr_at) and float(atr_at) > 0:
            if (float(row["high"]) - float(row["low"])) > 2 * float(atr_at):
                markers.append("range↑")
        marker_str = " ".join(markers)

        candle_lines.append(
            f"{time_str:<16} {row['open']:>10.2f} {row['high']:>10.2f} "
            f"{row['low']:>10.2f} {row['close']:>10.2f} {row['volume']:>10.1f}  "
            f"{rvol_str:>12}  {marker_str}".rstrip()
        )
```

Also import the helpers — at the top of `get_market_data` function body (currently has `from src.utils.ohlcv_utils import _live_price, _closed_bars, _atr_series`), add the new helpers:

```python
    from src.utils.ohlcv_utils import (
        _live_price, _closed_bars, _atr_series,
        _to_pd_timestamp_utc, _fmt_candle_time,
    )
```

Remove the now-unused `from datetime import datetime, timezone` import inside this function ONLY if it has no other use. **Check**: it's also used at line 98 (`fetch_ts = datetime.now(...)`) → keep the import.

Delete the now-dead inline dispatch lines 165-175 (replaced by helper calls above) — see the diff in Step 3 already replaces them.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestRVolColumn -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Run full GMD suite to catch regressions**

```bash
uv run pytest tests/test_iter_w2r2_next_d_goldens.py::TestGMDGolden tests/test_tool_enhancement.py -v -k "get_market_data"
```

Expected: all pre-existing tests PASS (RVol is additive — doesn't break existing column / marker assertions).

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_tool_opt_gmd_polish.py
git commit -m "$(cat <<'EOF'
iter-tool-opt-gmd-polish (2/7): RVol(×SMA20) column in OHLCV table

Issue 1 (P1) — close 29.6% systematic agent hand-compute of vol/SMA(20)
ratio per principle 5 (interface loop closure).

Add per-bar RVol column to OHLCV table:
- Header: `RVol(×SMA20)` right-aligned width 12, between Vol and Markers
- Values: `<X.XX>×` format matching agent reasoning idiom ("1.56× SMA avg")
- Degraded fallback `—` when SMA(20) not yet ready (degraded display window)
- `vol↑` marker preserved as visual-scan cue (RVol provides magnitude)

Also replace inline 3-branch tf strftime dispatch (line 169-175) with
shared `_fmt_candle_time` helper (Task 1) — unifies OHLCV row rendering
with in-progress hint formatting (Task 3 will consume same helper).

Spec: §2.1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 议题 2 — in-progress candle hint in OHLCV header

**Files:**
- Modify: `src/agent/tools_perception.py:194-197` (Recent Candles section header)
- Test: `tests/test_iter_tool_opt_gmd_polish.py` (append)

- [ ] **Step 1: Write failing tests for in-progress hint**

Append to `tests/test_iter_tool_opt_gmd_polish.py`:

```python
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
```

**Note**: `df_5m_130bars` fixture has 130 rows = 129 closed + 1 in-progress (per fixture docstring). `_closed_bars(df)` at `ohlcv_utils.py:31` drops the last row, so `df_closed.iloc[-1]` is the last *closed* bar (open at row 128) and we compute in-progress hint relative to that. The 130th row of the input df is the *in-progress* candle's open time (but not used directly for hint — we add tf offset to the last *closed* candle's open time).

Verify this assumption: read `tests/fixtures/multi_tf_ohlcv.py:_build()` to confirm row N has `timestamp = start_ms + N * tf_ms`. If row 128 has `timestamp = start + 128 * tf_ms`, then expected in-progress open = `start + 129 * tf_ms` (= row 129's timestamp = the in-progress candle's actual open time, which is exactly `row[-1].timestamp` in the un-closed df). So `last_closed_dt + tf_offset == df["timestamp"].iloc[-1]` (the in-progress bar's stamp). Tests rely on this equality.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestInProgressHint -v
```

Expected: all 6 tests FAIL (in-progress hint not yet implemented).

- [ ] **Step 3: Implement in-progress hint in `src/agent/tools_perception.py`**

Locate the `Recent Candles` section header at line 194-197 (currently):

```python
    sections.append(
        f"=== Recent Candles ({timeframe}, last {display_count}, oldest-first by row) ===\n"
        + "\n".join(candle_lines)
    )
```

Replace with:

```python
    # Build in-progress candle hint header suffix (issue 2: agent time-window
    # disambiguation; degraded fallback for unknown tf per spec §2.2)
    in_progress_suffix = ""
    if not display_df.empty:
        from src.utils.ohlcv_utils import TF_OFFSETS
        offset = TF_OFFSETS.get(timeframe)
        if offset is not None:
            last_closed_dt = _to_pd_timestamp_utc(display_df["timestamp"].iloc[-1])
            in_progress_open = last_closed_dt + offset
            in_progress_close = in_progress_open + offset
            in_progress_suffix = (
                f"; in-progress {_fmt_candle_time(in_progress_open, timeframe)} "
                f"still open, closes at {_fmt_candle_time(in_progress_close, timeframe)}"
            )

    sections.append(
        f"=== Recent Candles ({timeframe}, last {display_count}, "
        f"oldest-first by row{in_progress_suffix}) ===\n"
        + "\n".join(candle_lines)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestInProgressHint -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run full GMD suite to check existing test compatibility**

```bash
uv run pytest tests/test_iter_w2r2_next_d_goldens.py::TestGMDGolden -v
```

Expected: pre-existing `test_gmd_ohlcv_table_has_markers_column` still passes (it asserts `"oldest-first by row"` substring, which remains).

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_tool_opt_gmd_polish.py
git commit -m "$(cat <<'EOF'
iter-tool-opt-gmd-polish (3/7): in-progress candle hint in OHLCV header

Issue 2 (P2) — disambiguate candle time-window for agent (cycle 2c09
outlier wasted 3 GMD calls + 30s + ~3K tokens finding the in-progress
candle).

OHLCV Recent Candles header now includes:
  "; in-progress <HH:MM> still open, closes at <HH:MM>"
computed as last_closed_open + TF_OFFSETS[tf] and + 2× offset.

Time format unified across OHLCV row + in-progress hint via
_fmt_candle_time helper. 1M uses pd.DateOffset (calendar-aware).
Unknown tf → degraded fallback (no hint, no crash) per spec §2.2.

Spec: §2.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 议题 3 — Delete `<N>-candle High-Low` row from Market Context

**Files:**
- Modify: `src/agent/tools_perception.py:148-153` (delete N-candle H-L block)
- Test: `tests/test_iter_tool_opt_gmd_polish.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/test_iter_tool_opt_gmd_polish.py`:

```python
# === Task 4: delete N-candle High-Low row ===

class TestDeletedNCandleHL:
    @pytest.mark.asyncio
    async def test_no_n_candle_high_low_row(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 3: Market Context section no longer contains `<N>-candle High-Low`
        row. 1.1% adoption in audit; 24h H/L (ticker section, 54.4% adoption)
        is the surviving anchor."""
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestDeletedNCandleHL -v
```

Expected: FAIL — `N-candle High-Low:` still in output.

- [ ] **Step 3: Delete the N-candle High-Low block**

In `src/agent/tools_perception.py:148-153`, remove this block:

```python
    if not display_df.empty:
        ctx_lines.append(
            f"{display_count}-candle High-Low: {display_df['low'].min():.0f} — {display_df['high'].max():.0f}"
        )
    else:
        ctx_lines.append("Range: N/A")
```

Market Context section now has only `ATR(14)` + `Last bar vol` lines.

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestDeletedNCandleHL tests/test_iter_w2r2_next_d_goldens.py::TestGMDGolden -v
```

Expected: new test PASS; existing GMD tests still PASS (none asserts the deleted row).

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_tool_opt_gmd_polish.py
git commit -m "$(cat <<'EOF'
iter-tool-opt-gmd-polish (4/7): delete N-candle High-Low row

Issue 3 (P3) — 1.1% adoption (3/270 GMD reasoning blocks); agent mental
model prefers time-anchored swing high/low (25.2%) or 24h H/L (54.4%)
over abstract N-candle numeric range. Per spec §1.5 redundancy argument:
24h H/L already carries the same anchor role; deletion is path pruning,
not signal loss.

Spec: §2.3

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 议题 4 — Delete `Avg range` from Period summary

**Files:**
- Modify: `src/agent/tools_perception.py:200-217` (Period summary block)
- Test: `tests/test_iter_tool_opt_gmd_polish.py` (append)
- Update: `tests/test_iter_w2r2_next_d_goldens.py:241-251` `test_gmd_period_summary_section`

- [ ] **Step 1: Write failing tests for Avg range deletion**

Append to `tests/test_iter_tool_opt_gmd_polish.py`:

```python
# === Task 5: delete Avg range from Period summary ===

class TestPeriodSummaryNoAvgRange:
    @pytest.mark.asyncio
    async def test_period_summary_no_avg_range(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """Issue 4: Period summary section no longer contains Avg range row.
        ~3% adoption (1.5% verbatim + 1.9% concept) — dead metric."""
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
        """Issue 4: Period summary retains Avg vol (~10-15% adoption) and
        Net Δclose (~20-25% adoption)."""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Period summary" in out
        assert "Avg vol:" in out, f"Avg vol should remain; out={out[:1200]}"
        assert "Net Δclose:" in out, f"Net Δclose should remain; out={out[:1200]}"
```

- [ ] **Step 2: Update existing test that will break**

In `tests/test_iter_w2r2_next_d_goldens.py:241-251`, modify `test_gmd_period_summary_section`:

```python
    @pytest.mark.asyncio
    async def test_gmd_period_summary_section(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """B4: Period summary section after OHLCV table; 2 fields (Avg vol +
        Net Δclose) post iter-tool-opt-gmd-polish issue 4 deletion of
        Avg range (~3% adoption)."""
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Period summary (last 5 closed candles vs prior 5 closed candles) ===" in out
        assert "Avg vol:" in out
        assert "Net Δclose:" in out
        # Avg range deleted per iter-tool-opt-gmd-polish issue 4 (~3% adoption)
        assert "Avg range" not in out
```

- [ ] **Step 3: Run tests to verify the new ones fail and the updated existing one fails too (until impl)**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestPeriodSummaryNoAvgRange tests/test_iter_w2r2_next_d_goldens.py::TestGMDGolden::test_gmd_period_summary_section -v
```

Expected: all 3 tests FAIL (Avg range still in output).

- [ ] **Step 4: Implement deletion in `src/agent/tools_perception.py:200-217`**

Replace the Period summary block:

```python
    # === Period summary ===
    if len(df_closed) >= 10:
        last_5 = df_closed.iloc[-5:]
        prior_5 = df_closed.iloc[-10:-5]
        avg_vol_last = float(last_5["volume"].mean())
        avg_vol_prior = float(prior_5["volume"].mean())
        vol_ratio = avg_vol_last / avg_vol_prior if avg_vol_prior > 0 else 0.0
        net_delta_last = float(df_closed["close"].iloc[-1] - df_closed["close"].iloc[-5])
        net_delta_prior = float(df_closed["close"].iloc[-6] - df_closed["close"].iloc[-10])
        summary = (
            "=== Period summary (last 5 closed candles vs prior 5 closed candles) ===\n"
            f"Avg vol:     last 5 {avg_vol_last:.1f} / prior 5 {avg_vol_prior:.1f} ({vol_ratio:.2f}×)\n"
            f"Net Δclose:  last 5 {net_delta_last:+.1f} USDT / prior 5 {net_delta_prior:+.1f} USDT"
        )
        sections.append(summary)
```

Removed: `avg_rng_last`, `avg_rng_prior`, `rng_ratio` computations + the `Avg range (H-L):` line. Tightened the label column from 20 chars to 13 (`"Avg vol:     "` / `"Net Δclose:  "`) since the 3rd-line label `Avg range (H-L):` was the longest.

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestPeriodSummaryNoAvgRange tests/test_iter_w2r2_next_d_goldens.py::TestGMDGolden -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_tool_opt_gmd_polish.py tests/test_iter_w2r2_next_d_goldens.py
git commit -m "$(cat <<'EOF'
iter-tool-opt-gmd-polish (5/7): delete Avg range from Period summary

Issue 4 (P3) — Avg range (H-L) only ~3% adoption (1.5% verbatim + 1.9%
concept) per brainstorm field-level evidence; Avg vol kept (~10-15%
adoption) and Net Δclose kept (~20-25%). Period summary now 2 metrics
instead of 3.

Also update test_gmd_period_summary_section in existing golden file
to drop Avg range assertion and add a `not in` guard against future
re-introduction.

Spec: §2.4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 议题 5+6 — Docstring rewrite (path A inline + path B block-style + path C)

**Files:**
- Modify: `src/agent/tools_perception.py:51-87` (path A docstring + Args section)
- Modify: `src/agent/tools_descriptions.py:48-69` (path B `GET_MARKET_DATA_DESCRIPTION`)
- Modify: `src/agent/trader.py:124-140` (path C inner docstring)
- Test: `tests/test_iter_tool_opt_gmd_polish.py` (append)

**Critical**: Path A keeps **inline style** (block-style would be stripped by griffe). Path B keeps **block style** (bypasses griffe, block survives to LLM). See spec §2.5 path A vs B table.

- [ ] **Step 1: Write failing test for path B content**

Append to `tests/test_iter_tool_opt_gmd_polish.py`:

```python
# === Task 6: docstring rewrite ===

class TestDocstringRewrite:
    def test_path_b_description_contains_new_content(self):
        """Issue 5+6: path B GET_MARKET_DATA_DESCRIPTION reflects new OHLCV
        table format (RVol column + in-progress hint), drops 'volume ratio'
        fact-only drift, adds candle_count clamp explicit text. Block-style
        Example call/output preserved (bypasses griffe per PR #59)."""
        from src.agent.trader import create_trader_agent
        from src.config import PersonaConfig

        agent = create_trader_agent(model="test", persona_config=PersonaConfig())
        tool = agent._function_toolset.tools["get_market_data"]
        desc = tool.tool_def.description

        # Block-style sections still present (path B bypasses griffe)
        assert "=== Ticker" in desc, "Ticker section header missing from path B"
        assert "=== Recent Candles" in desc, "Recent Candles header missing"
        assert "=== Period summary" in desc, "Period summary header missing"

        # New content from this iter:
        assert "RVol(×SMA20)" in desc or "RVol" in desc, \
            f"RVol column documentation missing in path B: {desc!r}"
        assert "in-progress" in desc, \
            f"in-progress hint documentation missing in path B: {desc!r}"

        # Markers semantics preserved:
        assert "vol↑" in desc, "vol↑ marker semantics missing"
        assert "range↑" in desc, "range↑ marker semantics missing"

        # Fact-only fix: 'volume ratio' no longer claimed in Technical Indicators
        # (Avg range still appears nowhere; vol ratio fact: appears as `RVol`)
        # We don't assert absence of the literal word "volume ratio" since the
        # Last bar vol line still uses ratio concept; instead assert the new
        # RVol semantics and the deletion of Avg range from Period summary docs.
        assert "Avg range" not in desc, \
            f"Avg range should be removed from Period summary docs: {desc!r}"

        # candle_count clamp explicit (issue 6):
        assert "Clamped to [10, 80]" in desc or "clamped" in desc.lower(), \
            f"candle_count clamp explicit text missing: {desc!r}"
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestDocstringRewrite -v
```

Expected: FAIL (path B not yet rewritten).

- [ ] **Step 3: Rewrite path B `GET_MARKET_DATA_DESCRIPTION` in `src/agent/tools_descriptions.py:48-69`**

Replace the constant with block-style (preserve `Example call:` / `Example output:` admonitions — path B bypasses griffe so block survives to LLM):

```python
GET_MARKET_DATA_DESCRIPTION = """Single-timeframe market data: ticker (last + bid/ask + 24h H/L + base volume), technical indicators (RSI / MACD / BB / ATR), market context (ATR percent of price + last-bar volume with SMA(20) ratio), the most recent N closed candles in OHLCV table form with per-bar volume ratio (RVol = vol / SMA(20)) and anomaly markers, and a period summary comparing the last 5 vs prior 5 closed candles (avg volume, net Δclose).

All indicators are computed on the closed-bar series only (excluding the in-progress candle). The OHLCV table also shows closed bars only and is sorted oldest-first by row; the section header reports the in-progress candle's open and expected close timestamps.

OHLCV columns: Time (open UTC) | Open | High | Low | Close | Vol | RVol(×SMA20) | Markers.
- RVol = bar volume / SMA(20) of bar volumes (`2.95×` means the bar's volume is 2.95× the 20-bar average). Rendered for every closed bar; `—` when SMA(20) has not yet started (degraded display window).
- Markers (upside-only thresholds): `vol↑` for bar volume > 2× SMA(20) of bar volumes; `range↑` for bar range (high - low) > 2× ATR(14); empty for neither threshold tripped. Markers remain alongside RVol — RVol provides the magnitude, markers provide a visual scan cue.

Example call:
    get_market_data(timeframe="5m", candle_count=30)

Example output:
    === Ticker (BTC/USDT:USDT @ 14:23:08 UTC) ===
    Last: 81870.50 | Bid: 81870.40 | Ask: 81870.60
    24h High: 82400.10 | 24h Low: 80120.00 | 24h base vol: 12345.67

    === Technical Indicators (5m) ===
    RSI(14): 58.20
    ...

    === Market Context ===
    ATR(14): 245.30 (0.30% of price, 5m candles)
    Last bar vol: 178.6 (1.35× SMA(20) avg)

    === Recent Candles (5m, last 30, oldest-first by row; in-progress 14:25 still open, closes at 14:30) ===
    Time (open UTC)        Open       High        Low      Close        Vol  RVol(×SMA20)  Markers
    14:20              81865.00   81910.00   81860.00   81895.00      178.6         1.35×
    14:15              81830.00   81870.00   81825.00   81865.00      400.0         3.02×  vol↑
    ...

    === Period summary (last 5 closed candles vs prior 5 closed candles) ===
    Avg vol:     last 5 178.6 / prior 5 132.4 (1.35×)
    Net Δclose:  last 5 -25.0 USDT / prior 5 +120.0 USDT
"""
```

- [ ] **Step 4: Rewrite path A docstring + Args in `src/agent/tools_perception.py:51-87`**

Keep **inline style** for the main description (block would be stripped by griffe). Args section stays google-style (parsed via griffe into `parameters_json_schema`).

```python
async def get_market_data(
    deps: TradingDeps,
    symbol: str | None = None,
    timeframe: str | None = None,
    candle_count: int = 30,
) -> str:
    """Single-timeframe market data: ticker, technical indicators (RSI / MACD / BB / ATR), market context (ATR with percent of price, last-bar volume with SMA(20) ratio), the most recent N closed candles in OHLCV table form with per-bar volume ratio (RVol = vol / SMA(20)) and anomaly markers, and a period summary comparing the last 5 vs prior 5 closed candles (avg volume, net Δclose).

    All indicators are computed on the closed-bar series only (excluding the in-progress candle). The OHLCV table also shows closed bars only and is sorted oldest-first by row; the section header reports the in-progress candle's open and expected close timestamps.

    OHLCV columns: Time (open UTC), Open, High, Low, Close, Vol, RVol(×SMA20), Markers. RVol = bar volume / SMA(20) of bar volumes (`2.95×` means 2.95× the 20-bar average); rendered for every closed bar with `—` when SMA(20) has not yet started. Markers (upside-only thresholds): `vol↑` for bar volume > 2× SMA(20); `range↑` for bar range > 2× ATR(14); empty for neither. Markers remain alongside RVol — RVol provides the magnitude, markers provide a visual scan cue. Time column shows candle open in UTC.

    Args:
        symbol: Trading symbol. Defaults to session symbol.
        timeframe: CCXT timeframe ("1m", "5m", "1h", etc.). Defaults to session primary timeframe.
        candle_count: Number of closed candles in the OHLCV table. Default 30. Clamped to [10, 80]: values below 10 are raised to 10 (minimum useful window for indicators); values above 80 are capped to 80 (exchange API single-call limit).
    """
```

Removed: `Example call:` / `Example output:` / explicit `Markers in ...:` block — these would be stripped by griffe. The path B description carries the full block-style example to the LLM; path A serves as dev-facing fallback.

- [ ] **Step 5: Update path C inner docstring in `src/agent/trader.py:124-140`**

The inner docstring is dev-facing (overridden by `description=GET_MARKET_DATA_DESCRIPTION`). Keep it concise but accurate:

```python
    @tool(description=GET_MARKET_DATA_DESCRIPTION)
    async def get_market_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
        timeframe: str | None = None,
        candle_count: int = 30,
    ) -> str:
        """Get single-timeframe market data with indicators + OHLCV (RVol column +
        in-progress hint). LLM-visible description: src.agent.tools_descriptions.GET_MARKET_DATA_DESCRIPTION.

        Args:
            symbol: Trading symbol. Defaults to session symbol.
            timeframe: CCXT timeframe ("1m", "5m", "1h", etc.). Defaults to session primary timeframe.
            candle_count: Number of closed candles in the OHLCV table. Default 30. Clamped to [10, 80].
        """
        from src.agent.tools_perception import get_market_data as _impl

        return await _impl(ctx.deps, symbol, timeframe, candle_count)
```

- [ ] **Step 6: Run all docstring-related tests**

```bash
uv run pytest tests/test_iter_tool_opt_gmd_polish.py::TestDocstringRewrite \
              tests/test_trader_agent.py::test_get_market_data_description_carries_example_output \
              tests/test_trader_agent.py::test_dual_mode_tool_wrapper \
              tests/test_trader_agent.py::test_set_next_wake_description_carries_examples_block -v
```

Expected: all PASS. The existing `test_get_market_data_description_carries_example_output` asserts Ticker / Recent Candles / Period summary / vol↑ / range↑ — all still present in the new path B.

- [ ] **Step 7: Commit**

```bash
git add src/agent/tools_perception.py src/agent/tools_descriptions.py src/agent/trader.py tests/test_iter_tool_opt_gmd_polish.py
git commit -m "$(cat <<'EOF'
iter-tool-opt-gmd-polish (6/7): docstring rewrite (path A + B + C)

Issues 5+6 (P3) — sync all 3 docstring channels to reflect new OHLCV
table format (RVol column + in-progress hint), drop "volume ratio" drift
from Technical Indicators description (fact-only: per technical.py:25-28,
volume ratio is intentionally NOT in indicators output; was a historical
docstring leak), and make candle_count clamp explicit.

Channel responsibilities (per spec §2.5 + memory project_griffe_example_stripped):
- Path A (tools_perception.py docstring): inline style — block-style
  admonitions get stripped by griffe. Args section stays google for
  parameters_json_schema parsing.
- Path B (tools_descriptions.py GET_MARKET_DATA_DESCRIPTION): block style
  preserved — passed verbatim via @tool(description=), bypasses griffe.
  Carries full Example call/output to LLM.
- Path C (trader.py inner docstring): dev-facing only, overridden by
  description=. Simplified to summary + Args.

Spec: §2.5

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: AC sweep + full-suite verification + ready-to-merge

**Files:** None modified (verification only)

- [ ] **Step 1: Run full test suite to detect any regression**

```bash
uv run pytest tests/ -v 2>&1 | tail -40
```

Expected: all tests PASS. Pre-iter baseline was 1859 tests; this iter adds ~14 new tests and modifies 1 (`test_gmd_period_summary_section`), so expected count ≈ 1873.

If any test fails, diagnose and fix inline. **Do not skip / xfail**.

- [ ] **Step 2: Hand-inspect one GMD sample to verify visual correctness**

Run a real GMD render against the sim #12 paused session (or any test fixture):

```bash
uv run python -c "
import asyncio, pandas as pd
from unittest.mock import AsyncMock, MagicMock
from tests.fixtures.multi_tf_ohlcv import df_5m_anomaly, fake_ticker_81870

async def main():
    from src.agent.tools_perception import get_market_data
    from src.services.technical import TechnicalAnalysisService
    deps = MagicMock()
    deps.symbol = 'BTC/USDT:USDT'
    deps.timeframe = '5m'
    deps.technical = TechnicalAnalysisService()
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=fake_ticker_81870())
    deps.market_data.get_ohlcv_dataframe = AsyncMock(return_value=df_5m_anomaly())
    out = await get_market_data(deps)
    print(out)

asyncio.run(main())
" 2>&1 | head -60
```

Verify visually:
- ✓ `RVol(×SMA20)` column header present in Recent Candles table
- ✓ Each row has `<X.XX>×` value (or `—` for SMA-not-ready)
- ✓ Recent Candles header includes `in-progress <time> still open, closes at <time>`
- ✓ No `<N>-candle High-Low:` row in Market Context section
- ✓ Period summary has 2 lines: `Avg vol:` and `Net Δclose:` (no `Avg range`)
- ✓ Markers column still shows `vol↑` / `range↑` where appropriate

- [ ] **Step 3: Check spec ACs**

Cross-reference each AC in `docs/superpowers/specs/2026-05-28-iter-tool-opt-gmd-polish-design.md` §6:

- [ ] AC1: `pytest tests/` passes (Step 1)
- [ ] AC2: snapshot tests in `test_iter_tool_opt_gmd_polish.py` cover all visual changes (Tasks 2-5 tests)
- [ ] AC3: path A inline + path B block-style synced (Task 6 + test_path_b_description_contains_new_content)
- [ ] AC4: RVol > 2 ↔ vol↑ marker consistency (Task 2 tests)
- [ ] AC5: in-progress time arithmetic across tfs (Task 3 tests including monthly)
- [ ] AC6: candle_count clamp explicit in path A + B (Task 6 test)
- [ ] AC7: sim smoke 1 cycle no crash — Step 2 hand-inspect is the proxy

- [ ] **Step 4: Verify total source change <100 lines (mini-iter safeguard)**

```bash
git diff --stat be123a4..HEAD -- src/
```

Expected: combined src changes (tools_perception.py + tools_descriptions.py + trader.py + ohlcv_utils.py) ≈ 75-85 lines. If >100, decision: mini-iter direct-merge path no longer applies, prepare standard PR per spec §4 safeguard.

- [ ] **Step 5: Final commit (if any cleanup needed) or proceed to merge**

If all checks pass, the feature branch `iter-tool-opt-gmd-polish` is ready for either:
- **Mini-iter direct-merge** (≤100 lines src, simple issues): `git checkout main && git merge --no-ff iter-tool-opt-gmd-polish`
- **Standard PR**: `gh pr create` (if exceeded mini-iter cap or user prefers review)

**Do not merge / PR without explicit user instruction.** Report status and wait for user direction.

---

## Self-review checklist (before declaring plan done)

- [x] **Spec coverage**: each spec §1.2 issue (1-6) has a dedicated task (Tasks 2-6); spec §1.2 issue 7a/7b are out-of-scope per spec §2.6 / wontfix-by-cost — no task needed
- [x] **No placeholders**: every step has exact code / commands / expected output; no "TBD" or "implement later"
- [x] **Type consistency**: helper signatures (`_to_pd_timestamp_utc(Any) -> pd.Timestamp`; `_fmt_candle_time(pd.Timestamp, str) -> str`; `TF_OFFSETS: dict[str, pd.Timedelta | DateOffset]`) match across Tasks 1-3
- [x] **Existing test impact**: Task 5 inline-updates `test_gmd_period_summary_section`; other existing tests verified to still pass
- [x] **Path A / B distinction**: Tasks 6 + tests assert correct direction (A inline, B block-style); Task 6 commit message documents the channel responsibility
- [x] **Iteration ordering**: Task 1 (helpers) before Task 2 (RVol uses `_fmt_candle_time`) and Task 3 (in-progress uses `_to_pd_timestamp_utc` + `TF_OFFSETS` + `_fmt_candle_time`); Tasks 4-6 independent of helpers
