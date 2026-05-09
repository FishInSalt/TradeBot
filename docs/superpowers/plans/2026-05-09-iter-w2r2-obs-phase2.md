# Iter W2R2 — Observability Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build cross-sim analytics CLI tools (`scripts/analyze_sim.py` + `scripts/diff_sim.py`) on top of Phase 1's derived layer (v_cycle_metrics / v_alert_lifecycle / v_order_lifecycle), producing 28 metric groups across PnL/Cost/Behavior dimensions plus a FIFO-lot trade roundtrip pairing engine.

**Architecture:** Pure-script delta on top of Phase 1. SQLAlchemy SELECT against existing tables/views → `_sim_metrics.py` helpers → `analyze_sim.py` (single-sim markdown) / `diff_sim.py` (two-sim diff with Δ/Δ%/flag). Zero schema / alembic / src/cli / src/storage / src/integrations changes. Python FIFO lot model handles partial close + same-side addition (not a SQL view; sim handles long+short mutex per `_Position` single-instance dict).

**Tech Stack:** Python 3.12 / SQLAlchemy 2.x async / argparse / stdlib only (no new deps; markdown via string concat). Tests: pytest + pytest-asyncio (`asyncio_mode = "auto"` already configured) + `db_engine` fixture from `tests/conftest.py:90`.

**Spec:** `docs/superpowers/specs/2026-05-09-iter-w2r2-obs-phase2-design.md` (1103 lines). Tasks below cite spec sections — re-read the cited section before each task.

**Branch:** `feature/iter-w2r2-obs-phase2` (already created; spec at `9fa252c`).

**Memory anchors (read before starting):**
- `project_r2_8b_legacy_decision_restore_boundary` — R2_7_MERGED_AT cutoff = 2026-05-02
- `project_iter4_sql_caveats` — hold 双义 / legacy / derive_error 三类边界
- `feedback_plan_doc_commit_first` — this plan doc as standalone commit before any code commit
- `feedback_no_pr_comment` — code-review skill 跑完直接对话报告，不发 GitHub
- `feedback_long_walltime_experiments` — manual smoke (sim #8 / sim #9) 由 user 跑

---

## File Structure

```
scripts/
├── __init__.py            NEW   empty   makes `scripts` a package for `from scripts.* import`
├── _sim_metrics.py        NEW   ~480-580 LoC  FIFO pairing (~80) + 10 PnL fns (~80) + 7 Cost fns (~50) + 13 Behavior fns (~120) + 2 caveats helpers (~80) + dataclasses/constants/whitelists (~70) + assert_not_legacy (~10) + imports/docstrings (~30)
├── analyze_sim.py         NEW   ~280-360 LoC  single-sim markdown (argparse + render)
└── diff_sim.py            NEW   ~140-200 LoC  two-sim diff markdown (Δ/Δ%/flag + caveats helpers reuse)

tests/
├── _sim_fixtures.py       NEW   ~80-120 LoC   fixtures with auto-computed fees
├── test_sim_metrics.py    NEW   ~360-480 LoC  ~42-46 tests on _sim_metrics
├── test_analyze_sim.py    NEW   ~180-230 LoC  ~12-15 tests end-to-end (subprocess)
├── test_diff_sim.py       NEW   ~140-200 LoC  ~16-19 tests on Δ/flag/distributions/caveats
└── test_drift_phase2_metrics.py  NEW  ~80-130 LoC  ~8-10 drift-guard tests
```

**Total new code:** ~1620-2080 LoC. **Net tests:** ~85-95 (1284 → ~1369-1379).
**Zero changes to:** `alembic/` `src/storage/` `src/cli/` `src/integrations/` `src/agent/` `main.py`.

---

## Cross-cutting Conventions (apply throughout)

These conventions are referenced repeatedly; honor them in every task.

### C-1: Test file `datetime` imports

Every new test file starts with module-top import:

```python
from datetime import datetime, timedelta, timezone
```

**Never** `from datetime import timedelta` inside a function — Python compile-time scope rule turns the entire function's `timedelta` references into `UnboundLocalError` if the import follows any earlier reference. Fix once at module top.

### C-2: Fixture fee auto-computation

`make_open_lot` and `make_close_fill` default `fee=None`. When None, compute `fee = amount * px * fee_rate` (matches sim's `actual_amount`-based fee logic in `simulated.py:401`). Stale SL/TP scenarios pass an **explicit** fee that contradicts `amount` to exercise the `_derive_close_amount` fallback path. The fixture's `fee_rate` parameter must match the session's `fee_rate` (default `0.0005` on both).

### C-3: Script `sys.path` boilerplate

Both `scripts/analyze_sim.py` and `scripts/diff_sim.py` start with the repo-root path insertion (mirrors `scripts/tool_call_summary.py:18-20`):

```python
from __future__ import annotations
import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# noqa: E402 on subsequent imports
from scripts._sim_metrics import ...  # noqa: E402
from src.storage.models import ...    # noqa: E402
```

This makes `python scripts/analyze_sim.py` work when subprocess `sys.path[0]` is `scripts/`. Tests that `subprocess.run([sys.executable, "scripts/analyze_sim.py", ...])` rely on this.

### C-4: METRIC_GROUPS single source

`scripts/_sim_metrics.py` exports a `METRIC_GROUPS` list of exactly 28 string keys. The renderer in `analyze_sim.py` hand-writes rows (one row per group, plus expansion for multi-row groups like `exit_type_distribution` / `per_field_hit_rate`). The drift guard asserts: (a) `len(METRIC_GROUPS) == 28`, (b) the 3-dimension partition (10 PnL / 8 Cost / 10 Behavior), and (c) every group's canonical render label appears in analyze stdout (via `test_analyze_emits_all_28_metric_groups`). This bidirectional contract — list ↔ renderer must stay synchronized — catches drift without forcing an over-engineered registry. Adding a metric requires updating the list AND the renderer.

```python
METRIC_GROUPS: list[str] = [
    # PnL (10)
    "win_rate", "total_pnl_net", "roundtrip_count",
    "avg_fifo_pnl_per_roundtrip",
    "avg_roundtrip_duration_min", "median_roundtrip_duration_min",
    "max_drawdown_pct",
    "exit_type_distribution", "largest_win_loss", "profit_factor",
    # Cost (8)
    "total_input_tokens", "total_output_tokens", "total_cache_read_tokens",
    "avg_cache_hit_rate",
    "tokens_per_cycle_percentile",     # rendered as p50 + p95 (2 rows, 1 group)
    "avg_wall_time_ms",
    "llm_tool_avg_pair",                # avg_llm_call_ms + avg_tool_total_ms (2 rows, 1 group)
    "per_tool_call_top10",
    # Behavior (10)
    "total_cycles", "ok_vs_forensic_count",
    "triggered_by_distribution",
    "decision_type_distribution",       # incl. hold (pure-observation) + hold (wake-only)
    "five_field_complete_rate",
    "per_field_hit_rate",               # 5 anchor sub-rates
    "decision_length_avg_p95",          # avg + p95 (2 rows, 1 group)
    "retraction_rate",
    "reasoning_avg_pair",               # avg_reasoning_tokens + avg_thinking_chars (2 rows, 1 group)
    "alert_lifecycle_summary",   # expands to alert_triggered_rate + alert_cancelled_rate
                                  # + alert_avg_cancel_attempt_count (3 sub-rows, 1 group)
]
assert len(METRIC_GROUPS) == 28, "METRIC_GROUPS must stay at 28 — update spec §3 if changing"
```

**Multi-row groups** (1 METRIC_GROUPS key → ≥2 render rows): `exit_type_distribution` (5 rows by enum), `tokens_per_cycle_percentile` (p50+p95), `llm_tool_avg_pair` (llm+tool), `triggered_by_distribution` / `decision_type_distribution` (N rows by key set), `per_field_hit_rate` (5 anchor rates), `decision_length_avg_p95` (avg+p95), `reasoning_avg_pair` (tokens+chars), `alert_lifecycle_summary` (triggered+cancelled+avg_cancel_attempt). analyze and diff render the SAME row labels for these expansions — single source.

### C-5: Caveats helpers (split by responsibility)

`scripts/_sim_metrics.py` exports two helpers covering all 10 caveat types from spec §6.3:
- `render_caveats_per_side(rts, caveats, *, prefix, ok_cycle_count, forensic_count, null_field_summary)` — 8 per-session templates (1-7 + 10).
- `render_caveats_diff_only(*, a_eq_b, cross_symbol)` — 2 diff-specific templates (8 + 9).

`analyze_sim.py` calls only `render_caveats_per_side(prefix="")`. `diff_sim.py` calls `render_caveats_per_side` twice with `prefix="[A] "` / `prefix="[B] "`, then appends `render_caveats_diff_only(...)`. Drift guard greps the source for all 10 prefix templates.

### C-6: Verification commands use `pipefail`

Long pipelines (`pytest ... | tail`) must run under `set -o pipefail` or simply not pipe (so pytest's exit code reaches the shell):

```bash
uv run pytest tests/test_sim_metrics.py -v  # raw, exit code surfaces
# OR
set -o pipefail; uv run pytest -v 2>&1 | tail -40
```

### C-7: TDD note for tests that exercise prior implementation

T3-T5 add test cases for capabilities already implemented in T2 (`collect_roundtrips` was written end-to-end). Step 1 reads "Write tests asserting <capability>"; the run step expects PASS. If any fails, the algorithm's edge case was missed — fix in `_sim_metrics.py` before continuing.

---

## Task Order Rationale

Bottom-up TDD: helpers → `collect_roundtrips` (FIFO core) → metric functions → `METRIC_GROUPS` + caveat helper → analyze script → diff script → drift guards → manual smoke.

Each `collect_roundtrips` task adds an orthogonal capability (basic / lot model / liquidation / stale+invariant), so tests are additive without rewriting prior cases.

---

### Task 0: Plan doc commit (this file)

**Files:** `docs/superpowers/plans/2026-05-09-iter-w2r2-obs-phase2.md` (this doc)

- [ ] **Step 1:** User reviews this plan in place (per `feedback_review_before_commit`)

- [ ] **Step 2:** Commit plan as standalone commit (per `feedback_plan_doc_commit_first`)

```bash
git add docs/superpowers/plans/2026-05-09-iter-w2r2-obs-phase2.md
git commit -m "$(cat <<'EOF'
docs(iter-w2r2-obs-phase2): implementation plan for cross-sim analytics

Plan derived from spec at 9fa252c. Bottom-up TDD: helpers → FIFO
collect_roundtrips → metric fns + METRIC_GROUPS + caveats helper →
analyze script → diff script → drift guards → manual smoke.
Zero schema / alembic / src changes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 1: `_sim_metrics.py` skeleton — helpers, dataclasses, constants, METRIC_GROUPS

**Spec:** §4.1 fact calibration / §4.2 helpers / §6.4 R2_7_MERGED_AT.

**Files:**
- Create: `scripts/__init__.py` (empty — makes `scripts` an importable package)
- Create: `scripts/_sim_metrics.py`
- Create: `tests/test_sim_metrics.py`
- Create: `tests/_sim_fixtures.py` (skeleton — populate as later tasks need it)

- [ ] **Step 1: Verify scripts/ has no existing tests assuming non-package**

```bash
rg "from scripts" tests/ src/
test ! -e scripts/__init__.py && echo "absent — safe to add"
```

Expected: empty rg + "absent". Existing scripts run as `python scripts/foo.py`; adding `__init__.py` does not break that, only adds the import-as-package path.

- [ ] **Step 2: Write failing tests for helpers + METRIC_GROUPS**

```python
# tests/test_sim_metrics.py
"""Tests for scripts/_sim_metrics.py — Phase 2 cross-sim analytics core."""
from datetime import datetime, timedelta, timezone

import pytest

from scripts._sim_metrics import (
    R2_7_MERGED_AT,
    METRIC_GROUPS,
    Roundtrip,
    _Lot,
    _compute_pnl,
    _derive_close_amount,
    _is_close_fill,
)


def test_is_close_fill_long_sell_returns_true():
    assert _is_close_fill("long", "sell") is True


def test_is_close_fill_short_buy_returns_true():
    assert _is_close_fill("short", "buy") is True


def test_is_close_fill_open_returns_false():
    assert _is_close_fill("long", "buy") is False
    assert _is_close_fill("short", "sell") is False


def test_compute_pnl_long_profit():
    assert _compute_pnl(100.0, 110.0, 1.0, "long") == pytest.approx(10.0)


def test_compute_pnl_short_profit():
    assert _compute_pnl(100.0, 90.0, 1.0, "short") == pytest.approx(10.0)


class _FillStub:
    def __init__(self, fee=None, filled_price=None, amount=None):
        self.fee = fee; self.filled_price = filled_price; self.amount = amount


def test_derive_close_amount_uses_fee_inverse():
    # fee = 80000 * 0.05 * 0.0005 = 2.0
    fill = _FillStub(fee=2.0, filled_price=80000.0, amount=0.05)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005)
    assert ok is True
    assert derived == pytest.approx(0.05)


def test_derive_close_amount_fallback_when_fee_missing():
    fill = _FillStub(fee=None, filled_price=80000.0, amount=0.2)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005)
    assert ok is False
    assert derived == 0.2


def test_derive_close_amount_fallback_when_fee_rate_missing():
    fill = _FillStub(fee=2.0, filled_price=80000.0, amount=0.05)
    derived, ok = _derive_close_amount(fill, fee_rate=None)
    assert ok is False
    assert derived == 0.05


def test_derive_close_amount_fallback_when_derived_exceeds_order_amount():
    # implies actual=0.5 but order_amount=0.05; reject as suspicious
    fill = _FillStub(fee=20.0, filled_price=80000.0, amount=0.05)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005)
    assert ok is False
    assert derived == 0.05


def test_r2_7_merged_at_constant():
    assert R2_7_MERGED_AT == datetime(2026, 5, 2, tzinfo=timezone.utc)


def test_metric_groups_count_28():
    """Single source of metric inventory; renderer + drift guard reuse this."""
    assert len(METRIC_GROUPS) == 28
    assert len(set(METRIC_GROUPS)) == 28  # no duplicates


async def test_phase1_views_runnable(db_engine):
    """Prerequisite sanity: Phase 1 views exist and SELECT * returns a row shape.

    Catches schema drift early — if v_cycle_metrics column was renamed or
    a view was dropped, this fails in T1 instead of mid-T2 algorithm.
    Cost: 30ms.
    """
    from sqlalchemy import text
    async with db_engine.connect() as conn:
        await conn.execute(text("SELECT * FROM v_cycle_metrics LIMIT 0"))
        await conn.execute(text("SELECT * FROM v_alert_lifecycle LIMIT 0"))
        await conn.execute(text("SELECT * FROM v_order_lifecycle LIMIT 0"))
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_sim_metrics.py -v
```

Expected: ImportError "No module named 'scripts._sim_metrics'".

- [ ] **Step 4: Create `scripts/__init__.py`**

```bash
touch scripts/__init__.py
```

- [ ] **Step 5: Implement skeleton in `scripts/_sim_metrics.py`**

```python
"""Phase 2 cross-sim analytics core: FIFO lot pairing + metric functions
+ METRIC_GROUPS inventory + caveats helpers (per-side + diff-only).

Spec: docs/superpowers/specs/2026-05-09-iter-w2r2-obs-phase2-design.md
Caveats §4.4 / SQL §3.5 / R2-7 cutoff §6.4 must be honored.
"""
from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

# `from sqlalchemy import text` is added in T2 when collect_roundtrips needs it;
# T1 skeleton does not require it yet. Same for `import json` / `import statistics`
# / `import re` — added in T6/T8 respectively as their functions arrive.


R2_7_MERGED_AT = datetime(2026, 5, 2, tzinfo=timezone.utc)


METRIC_GROUPS: list[str] = [
    # PnL (10)
    "win_rate", "total_pnl_net", "roundtrip_count",
    "avg_fifo_pnl_per_roundtrip",
    "avg_roundtrip_duration_min", "median_roundtrip_duration_min",
    "max_drawdown_pct",
    "exit_type_distribution", "largest_win_loss", "profit_factor",
    # Cost (8)
    "total_input_tokens", "total_output_tokens", "total_cache_read_tokens",
    "avg_cache_hit_rate",
    "tokens_per_cycle_percentile",
    "avg_wall_time_ms",
    "llm_tool_avg_pair",
    "per_tool_call_top10",
    # Behavior (10)
    "total_cycles", "ok_vs_forensic_count",
    "triggered_by_distribution",
    "decision_type_distribution",
    "five_field_complete_rate",
    "per_field_hit_rate",
    "decision_length_avg_p95",
    "retraction_rate",
    "reasoning_avg_pair",
    "alert_lifecycle_summary",
]
assert len(METRIC_GROUPS) == 28, \
    "METRIC_GROUPS must stay at 28 — update spec §3 if changing"


def _is_close_fill(position_side: str, side: str) -> bool:
    """Mirror simulated.py:94 _is_close_order_static."""
    return (
        (position_side == "long" and side == "sell")
        or (position_side == "short" and side == "buy")
    )


def _compute_pnl(entry_px: float, exit_px: float, amount: float, side: str) -> float:
    """Lot-level PnL (non-weighted). Mirrors simulated.py:403-406."""
    if side == "long":
        return (exit_px - entry_px) * amount
    return (entry_px - exit_px) * amount


def _derive_close_amount(fill, fee_rate: float | None) -> tuple[float, bool]:
    """Derive close fill actual_amount from fee (handles stale SL/TP amount).

    fee = filled_price × actual_amount × fee_rate
    → actual_amount = fee / (filled_price × fee_rate)

    Fallback when fee/fee_rate/filled_price missing OR derived > order_amount × 1.01:
    return (order_amount, False).
    """
    if fill.fee and fill.filled_price and fee_rate and fee_rate > 0:
        derived = fill.fee / (fill.filled_price * fee_rate)
        if derived <= fill.amount * 1.01:  # 1% float tolerance
            return derived, True
    return fill.amount, False


@dataclass
class _Lot:
    open_at: datetime
    open_cycle_id: str | None
    side: str
    entry_px: float
    original_amount: float
    remaining_amount: float
    leverage: int
    open_fee: float


@dataclass
class Roundtrip:
    open_at: datetime
    close_at: datetime
    open_cycle_id: str | None
    close_cycle_id: str | None
    side: str
    entry_px: float
    exit_px: float
    amount: float
    leverage: int
    pnl_gross: float
    fee_open_share: float
    fee_close_share: float
    fee_total: float
    pnl_net: float
    duration_seconds: int
    exit_type: str
```

- [ ] **Step 6: Skeleton `tests/_sim_fixtures.py`** (helpers expanded in T2)

```python
"""Test fixtures for Phase 2 cross-sim analytics. Underscore = internal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from scripts._sim_metrics import R2_7_MERGED_AT


def _safe_created_at(offset_days: int = 1) -> datetime:
    """Default session created_at: post-R2-7 cutoff to avoid legacy reject."""
    return R2_7_MERGED_AT + timedelta(days=offset_days)


def _resolve_db_path(engine) -> str:
    """Extract sqlite filesystem path from async engine URL (for subprocess tests)."""
    url = str(engine.url)
    return url.replace("sqlite+aiosqlite:///", "")


# Fixture builders populated by T2 (make_session / make_cycle / make_open_lot / make_close_fill).
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
uv run pytest tests/test_sim_metrics.py -v
```

Expected: 12 PASS (11 unit + 1 Phase 1 views runnable smoke).

- [ ] **Step 8: Commit**

```bash
git add scripts/__init__.py scripts/_sim_metrics.py tests/test_sim_metrics.py tests/_sim_fixtures.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T1 _sim_metrics helpers + dataclasses + METRIC_GROUPS

_is_close_fill / _compute_pnl / _derive_close_amount + _Lot + Roundtrip
+ R2_7_MERGED_AT + METRIC_GROUPS (28 keys, single source for renderer
and drift guard). 11 unit tests + 1 Phase 1 views runnable smoke.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `collect_roundtrips` happy paths + fixture builders

**Spec:** §4.2 algorithm / §4.5 testing matrix.

**Files:**
- Modify: `scripts/_sim_metrics.py` (add `collect_roundtrips`)
- Modify: `tests/test_sim_metrics.py`
- Modify: `tests/_sim_fixtures.py` (populate builders with **C-2 auto-fee**)

- [ ] **Step 1: Populate fixture builders with auto-fee semantics**

Append to `tests/_sim_fixtures.py`:

```python
from sqlalchemy import insert
from src.storage.models import (
    Session as SessionModel, AgentCycle, SimOrder, TradeAction,
)


async def make_session(
    engine, *, name="test_sim", symbol="BTC/USDT:USDT",
    created_at=None, fee_rate=0.0005, initial_balance=100.0,
) -> str:
    if created_at is None:
        created_at = _safe_created_at(1)
    session_id = str(uuid4())
    async with engine.begin() as conn:
        await conn.execute(insert(SessionModel).values(
            id=session_id, name=name, symbol=symbol,
            created_at=created_at, fee_rate=fee_rate,
            initial_balance=initial_balance,
        ))
    return session_id


async def make_cycle(
    engine, session_id, cycle_id, *, decision=None,
    execution_status="ok", triggered_by="scheduled",
    state_snapshot=None, reasoning=None,
    input_tokens=5000, output_tokens=500, cache_read_tokens=3500,
    wall_time_ms=1200, llm_call_ms=900, reasoning_tokens=0,
):
    async with engine.begin() as conn:
        await conn.execute(insert(AgentCycle).values(
            session_id=session_id, cycle_id=cycle_id,
            triggered_by=triggered_by, execution_status=execution_status,
            decision=decision, state_snapshot=state_snapshot, reasoning=reasoning,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens, wall_time_ms=wall_time_ms,
            llm_call_ms=llm_call_ms, reasoning_tokens=reasoning_tokens,
            tokens_consumed=input_tokens + output_tokens,
            cache_hit_rate=cache_read_tokens / input_tokens if input_tokens else None,
        ))


async def make_open_lot(
    engine, session_id, *, cycle_id, side="long",
    entry_px=80000.0, amount=0.1, leverage=1,
    fee=None, fee_rate=0.0005,
    filled_at=None, order_type="market",
) -> str:
    """Insert open fill (sim_orders + open_position trade_action).

    fee=None → auto-compute amount * entry_px * fee_rate (sim's actual_amount-based
    fee pattern, simulated.py:401). Pass explicit fee to override.
    """
    if fee is None:
        fee = amount * entry_px * fee_rate
    if filled_at is None:
        filled_at = _safe_created_at(2)
    fill_side = "buy" if side == "long" else "sell"
    order_id = str(uuid4())
    async with engine.begin() as conn:
        await conn.execute(insert(SimOrder).values(
            session_id=session_id, order_id=order_id,
            symbol="BTC/USDT:USDT", side=fill_side, position_side=side,
            order_type=order_type, amount=amount, status="filled",
            filled_price=entry_px, fee=fee, filled_at=filled_at,
            leverage=leverage,
        ))
        await conn.execute(insert(TradeAction).values(
            session_id=session_id, cycle_id=cycle_id,
            action="open_position", order_id=order_id,
            symbol="BTC/USDT:USDT", side=fill_side, price=entry_px,
        ))
    return order_id


async def make_close_fill(
    engine, session_id, *, cycle_id, side="long",
    exit_px=82000.0, amount=0.1,
    fee=None, fee_rate=0.0005,
    exit_type="market", pnl_gross=None, filled_at=None,
) -> str:
    """Insert close fill. fee=None → auto-compute (matches non-stale sim path).

    For stale SL/TP scenarios pass an explicit fee inconsistent with `amount`
    to drive the _derive_close_amount fallback.

    pnl_gross is the sim weighted-entry PnL written to trade_actions.pnl
    (drives P2 total_pnl_net + liquidation pnl_cap path; not used for FIFO
    lot attribution which recomputes from lot.entry_px for non-liquidation).

    5-enum action mapping:
      market/stop/take_profit → close_position
      limit                   → place_limit_order
      liquidation             → no 5-enum row (close_cycle_id stays None per §4.1)
    """
    if fee is None:
        fee = amount * exit_px * fee_rate
    if filled_at is None:
        filled_at = _safe_created_at(3)
    fill_side = "sell" if side == "long" else "buy"
    order_id = str(uuid4())
    if exit_type == "liquidation":
        action_5enum = None
    elif exit_type == "limit":
        action_5enum = "place_limit_order"
    else:
        action_5enum = "close_position"
    async with engine.begin() as conn:
        await conn.execute(insert(SimOrder).values(
            session_id=session_id, order_id=order_id,
            symbol="BTC/USDT:USDT", side=fill_side, position_side=side,
            order_type=exit_type, amount=amount, status="filled",
            filled_price=exit_px, fee=fee, filled_at=filled_at,
        ))
        if action_5enum is not None:
            await conn.execute(insert(TradeAction).values(
                session_id=session_id, cycle_id=cycle_id,
                action=action_5enum, order_id=order_id,
                symbol="BTC/USDT:USDT", side=fill_side, price=exit_px,
            ))
        # Always write order_filled with pnl (drives P2 + liquidation pnl_cap path)
        await conn.execute(insert(TradeAction).values(
            session_id=session_id, cycle_id=cycle_id,
            action="order_filled", order_id=order_id,
            symbol="BTC/USDT:USDT", side=fill_side, price=exit_px,
            pnl=pnl_gross, fee=fee, trigger_reason=exit_type,
        ))
    return order_id
```

- [ ] **Step 2: Write 6 failing tests for `collect_roundtrips` happy paths**

```python
# Append to tests/test_sim_metrics.py

from scripts._sim_metrics import collect_roundtrips
from tests._sim_fixtures import (
    make_session, make_cycle, make_open_lot, make_close_fill,
)


async def test_collect_roundtrips_empty_session(db_engine):
    sid = await make_session(db_engine)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert rts == []
    assert caveats["unclosed_lot_count"] == {"long": 0, "short": 0}
    assert caveats["invariant_violations"] == 0
    assert caveats["liquidation_count"] == 0
    assert caveats["stale_close_amount_count"] == 0


async def test_collect_roundtrips_single_market_close(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_cycle(db_engine, sid, "c2")
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=80000, amount=0.1)
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_px=82000, amount=0.1,
                          exit_type="market", pnl_gross=200.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].exit_type == "market"
    assert rts[0].amount == pytest.approx(0.1)
    # FIFO recompute (non-liquidation): (82000-80000)*0.1 = 200
    assert rts[0].pnl_gross == pytest.approx(200.0)
    assert caveats["stale_close_amount_count"] == 0


async def test_collect_roundtrips_sl_close(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_cycle(db_engine, sid, "c2")
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="stop", pnl_gross=200.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].exit_type == "stop"


async def test_collect_roundtrips_tp_close(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_cycle(db_engine, sid, "c2")
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="take_profit", pnl_gross=200.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].exit_type == "take_profit"


async def test_collect_roundtrips_two_long_sequential(db_engine):
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3", "c4"]:
        await make_cycle(db_engine, sid, c)
    base = R2_7_MERGED_AT + timedelta(days=2)
    await make_open_lot(db_engine, sid, cycle_id="c1", filled_at=base)
    await make_close_fill(db_engine, sid, cycle_id="c2",
                          filled_at=base + timedelta(minutes=10), pnl_gross=200.0)
    await make_open_lot(db_engine, sid, cycle_id="c3",
                        filled_at=base + timedelta(minutes=20))
    await make_close_fill(db_engine, sid, cycle_id="c4",
                          filled_at=base + timedelta(minutes=30), pnl_gross=300.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 2


async def test_collect_roundtrips_long_short_alternating(db_engine):
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3", "c4"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", side="long")
    await make_close_fill(db_engine, sid, cycle_id="c2", side="long", pnl_gross=100.0)
    await make_open_lot(db_engine, sid, cycle_id="c3", side="short")
    await make_close_fill(db_engine, sid, cycle_id="c4", side="short", pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 2
    assert {rt.side for rt in rts} == {"long", "short"}
    assert caveats["unclosed_lot_count"] == {"long": 0, "short": 0}
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_sim_metrics.py::test_collect_roundtrips_empty_session -v
```

Expected: ImportError "cannot import name 'collect_roundtrips'".

- [ ] **Step 4: Implement `collect_roundtrips` in `scripts/_sim_metrics.py`**

Reference spec §4.2 for full algorithm pseudo-code.

```python
from sqlalchemy import text


_FILLS_SQL = text("""
    SELECT so.id, so.order_id, so.side, so.position_side, so.order_type,
           so.amount, so.filled_price, so.fee, so.filled_at, so.leverage,
           vol.originated_cycle_id, ta_filled.pnl AS trade_action_pnl
    FROM sim_orders so
    LEFT JOIN v_order_lifecycle vol ON vol.order_id = so.order_id
    LEFT JOIN trade_actions ta_filled
      ON ta_filled.order_id = so.order_id
     AND ta_filled.session_id = :sid    -- defensive even though SimOrder.order_id is unique
     AND ta_filled.action = 'order_filled'
    WHERE so.session_id = :sid AND so.filled_at IS NOT NULL
    ORDER BY so.filled_at ASC, so.id ASC
""")


async def _fetch_fee_rate(engine, session_id: str) -> float | None:
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT fee_rate FROM sessions WHERE id = :sid"),
            {"sid": session_id},
        )).first()
    return row.fee_rate if row else None


async def collect_roundtrips(engine, session_id: str) -> tuple[list[Roundtrip], dict]:
    """FIFO lot pairing. See spec §4.2 for full algorithm.

    Returns (roundtrips, caveats):
      caveats keys: unclosed_lot_count {'long': int, 'short': int},
                    invariant_violations: int,
                    liquidation_count: int,
                    stale_close_amount_count: int
    """
    fee_rate = await _fetch_fee_rate(engine, session_id)
    async with engine.connect() as conn:
        result = await conn.execute(_FILLS_SQL, {"sid": session_id})
        fills = result.all()

    roundtrips: list[Roundtrip] = []
    open_lots: dict[str, deque[_Lot]] = {"long": deque(), "short": deque()}
    caveats = {
        "unclosed_lot_count": {"long": 0, "short": 0},
        "invariant_violations": 0,
        "liquidation_count": 0,
        "stale_close_amount_count": 0,
    }

    for fill in fills:
        if not _is_close_fill(fill.position_side, fill.side):
            open_lots[fill.position_side].append(_Lot(
                open_at=fill.filled_at,
                open_cycle_id=fill.originated_cycle_id,
                side=fill.position_side,
                entry_px=fill.filled_price,
                original_amount=fill.amount,
                remaining_amount=fill.amount,
                leverage=fill.leverage,
                open_fee=fill.fee or 0.0,
            ))
            continue

        # CLOSE — FIFO consume
        actual_amount, derived_ok = _derive_close_amount(fill, fee_rate)
        if not derived_ok:
            caveats["stale_close_amount_count"] += 1
        close_remaining = actual_amount
        close_fee_total = fill.fee or 0.0
        lot_queue = open_lots[fill.position_side]

        while close_remaining > 0:
            if not lot_queue:
                caveats["invariant_violations"] += 1
                print(
                    f"close fill {fill.order_id} has no preceding open lot",
                    file=sys.stderr,
                )
                break
            lot = lot_queue[0]
            consumed = min(lot.remaining_amount, close_remaining)

            if fill.order_type == "liquidation":
                if fill.trade_action_pnl is None:
                    caveats["invariant_violations"] += 1
                    print(
                        f"liquidation fill {fill.order_id} missing trade_actions.pnl row",
                        file=sys.stderr,
                    )
                    pnl_gross = 0.0
                else:
                    pnl_gross = fill.trade_action_pnl * (consumed / actual_amount)
            else:
                pnl_gross = _compute_pnl(lot.entry_px, fill.filled_price, consumed, lot.side)

            fee_open_share = lot.open_fee * (consumed / lot.original_amount)
            fee_close_share = close_fee_total * (consumed / actual_amount)
            fee_total = fee_open_share + fee_close_share

            roundtrips.append(Roundtrip(
                open_at=lot.open_at, close_at=fill.filled_at,
                open_cycle_id=lot.open_cycle_id,
                close_cycle_id=(fill.originated_cycle_id
                                if fill.order_type != "liquidation" else None),
                side=lot.side, entry_px=lot.entry_px, exit_px=fill.filled_price,
                amount=consumed, leverage=lot.leverage,
                pnl_gross=pnl_gross,
                fee_open_share=fee_open_share, fee_close_share=fee_close_share,
                fee_total=fee_total,
                pnl_net=pnl_gross - fee_total,
                duration_seconds=int((fill.filled_at - lot.open_at).total_seconds()),
                exit_type=fill.order_type,
            ))

            lot.remaining_amount -= consumed
            close_remaining -= consumed
            if lot.remaining_amount <= 1e-9:
                lot_queue.popleft()

        if fill.order_type == "liquidation":
            caveats["liquidation_count"] += 1

    caveats["unclosed_lot_count"] = {
        "long": len(open_lots["long"]),
        "short": len(open_lots["short"]),
    }
    return roundtrips, caveats
```

- [ ] **Step 5: Run tests, verify pass**

```bash
uv run pytest tests/test_sim_metrics.py -v -k "collect_roundtrips"
```

Expected: 6 PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/_sim_metrics.py tests/_sim_fixtures.py tests/test_sim_metrics.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T2 collect_roundtrips happy paths

FIFO lot pairing handles open/close + market/SL/TP exit_type +
sequential and long/short alternating sequences. Fixtures auto-compute
fee from amount*px*fee_rate (override for stale SL tests). 6 unit tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `collect_roundtrips` lot model — same-side addition / partial close / span lots

**Spec:** §4.1 (sim 行为校准 — 同向加仓 / 部分平仓物理支持) / §4.2 / §4.3 数据组装 / §4.5.

**Files:**
- Modify: `tests/test_sim_metrics.py`

**TDD note (per C-7):** `collect_roundtrips` already implements the lot model (T2). These tests assert behavior; expected PASS without source change. If any test fails, fix `_sim_metrics.py` and re-run.

- [ ] **Step 1: Write 5 tests for lot-model edges**

```python
async def test_collect_roundtrips_same_side_addition_two_lots_one_close(db_engine):
    """lot1(long, 0.1) + lot2(long, 0.1) + close 0.2 → 2 roundtrips."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    base = R2_7_MERGED_AT + timedelta(days=2)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=80000, amount=0.1, filled_at=base)
    await make_open_lot(db_engine, sid, cycle_id="c2", entry_px=82000, amount=0.1,
                        filled_at=base + timedelta(minutes=5))
    await make_close_fill(db_engine, sid, cycle_id="c3", exit_px=85000, amount=0.2,
                          pnl_gross=800.0, filled_at=base + timedelta(minutes=10))
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 2
    assert rts[0].pnl_gross == pytest.approx(500.0)  # (85000-80000)*0.1
    assert rts[1].pnl_gross == pytest.approx(300.0)  # (85000-82000)*0.1
    assert caveats["unclosed_lot_count"] == {"long": 0, "short": 0}


async def test_collect_roundtrips_partial_close(db_engine):
    """open(0.2) + close(0.05) → 1 rt (amount=0.05); 1 unclosed lot remaining."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.2)
    await make_close_fill(db_engine, sid, cycle_id="c2", amount=0.05, pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].amount == pytest.approx(0.05)
    assert caveats["unclosed_lot_count"]["long"] == 1


async def test_collect_roundtrips_close_spans_multiple_lots(db_engine):
    """lot1(0.1) + lot2(0.1) + close(0.15) → lot1 fully + lot2 0.05 partial."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    base = R2_7_MERGED_AT + timedelta(days=2)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.1, filled_at=base)
    await make_open_lot(db_engine, sid, cycle_id="c2", amount=0.1,
                        filled_at=base + timedelta(minutes=5))
    await make_close_fill(db_engine, sid, cycle_id="c3", amount=0.15, pnl_gross=400.0,
                          filled_at=base + timedelta(minutes=10))
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 2
    assert rts[0].amount == pytest.approx(0.1)   # lot1 fully
    assert rts[1].amount == pytest.approx(0.05)  # lot2 partial
    assert caveats["unclosed_lot_count"]["long"] == 1


async def test_collect_roundtrips_fee_proportional_split(db_engine):
    """open.fee=0.50 (explicit), lot 50% consumed → fee_open_share=0.25."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    # explicit fee to make assertion straightforward
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.2, fee=0.5)
    # close 0.1 (50% of lot) → fee_close auto = 0.1 * 82000 * 0.0005 = 4.1
    await make_close_fill(db_engine, sid, cycle_id="c2", amount=0.1, pnl_gross=200.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].fee_open_share == pytest.approx(0.25)


async def test_collect_roundtrips_pnl_uses_lot_entry_not_weighted(db_engine):
    """Non-liquidation pnl_gross = (exit_px - lot.entry_px) * consumed,
    not trade_actions.pnl (sim weighted)."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    # lot1 entry 100, lot2 entry 200; close 1.0 at 150
    # FIFO lot1 consumed 1.0 → (150-100)*1 = +50
    # If wrongly used trade_actions.pnl=0 (sim weighted), test catches.
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=100, amount=1.0)
    await make_open_lot(db_engine, sid, cycle_id="c2", entry_px=200, amount=1.0)
    await make_close_fill(db_engine, sid, cycle_id="c3", exit_px=150, amount=1.0,
                          pnl_gross=0.0)  # sim weighted
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].pnl_gross == pytest.approx(50.0)
```

- [ ] **Step 2: Run tests, expect 5 PASS**

```bash
uv run pytest tests/test_sim_metrics.py -v -k "same_side_addition or partial_close or close_spans or fee_proportional or uses_lot_entry"
```

If any fails, reread §4.2/§4.3 and fix `_sim_metrics.py` before continuing.

- [ ] **Step 3: Commit**

```bash
git add tests/test_sim_metrics.py
git commit -m "$(cat <<'EOF'
test(obs-phase2): T3 collect_roundtrips lot-model assertions

Same-side addition + partial close + close spanning multiple lots +
fee proportional split + lot.entry_px (not sim-weighted) PnL. 5 tests
exercising T2 implementation; no source change unless edge case missed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `collect_roundtrips` liquidation special-case + invariant violations

**Spec:** §4.1 liquidation 事实校准 / §4.4 项 4 (pnl_cap) / §4.4 项 3 (cycle_id N/A).

**Files:**
- Modify: `tests/test_sim_metrics.py`

**TDD note (per C-7):** liquidation paths already implemented in T2. Expected PASS.

- [ ] **Step 1: Write 4 tests**

```python
async def test_collect_roundtrips_liquidation_close_cycle_id_none(db_engine):
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.1)
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="liquidation",
                          amount=0.1, pnl_gross=-50.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].exit_type == "liquidation"
    assert rts[0].close_cycle_id is None
    assert caveats["liquidation_count"] == 1


async def test_collect_roundtrips_liquidation_uses_trade_actions_pnl(db_engine):
    """Sim caps liquidation loss; FIFO recompute would over-state.
    Verify roundtrip.pnl_gross = trade_actions.pnl proportional."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    # entry 80000, exit 40000, amount 0.1, lev 10 → recompute -4000;
    # sim pnl_cap stub = -800 (margin floor)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=80000, amount=0.1, leverage=10)
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="liquidation",
                          exit_px=40000, amount=0.1, pnl_gross=-800.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    # consumed 0.1 / actual 0.1 → full pnl_gross = -800
    assert rts[0].pnl_gross == pytest.approx(-800.0)


async def test_collect_roundtrips_liquidation_missing_trade_action_invariant(db_engine, capsys):
    """Liquidation fill without order_filled trade_action → invariant violation."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.1)
    # Manually insert sim_orders WITHOUT any trade_action row
    from sqlalchemy import insert
    from src.storage.models import SimOrder
    async with db_engine.begin() as conn:
        await conn.execute(insert(SimOrder).values(
            session_id=sid, order_id="liq-orphan", symbol="BTC/USDT:USDT",
            side="sell", position_side="long", order_type="liquidation",
            amount=0.1, status="filled", filled_price=40000, fee=2.0,
            filled_at=R2_7_MERGED_AT + timedelta(days=2, minutes=5),
        ))
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert caveats["invariant_violations"] >= 1
    assert any(rt.exit_type == "liquidation" and rt.pnl_gross == 0.0 for rt in rts)
    err = capsys.readouterr().err
    assert "missing trade_actions.pnl" in err


async def test_collect_roundtrips_non_liquidation_recomputes_pnl_from_lot(db_engine):
    """Non-liquidation must recompute from lot.entry_px (ignore trade_actions.pnl)."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=80000, amount=0.1)
    # set wrong trade_actions.pnl=999 → if read, test catches; expected PnL = 200
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="market",
                          exit_px=82000, amount=0.1, pnl_gross=999.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].pnl_gross == pytest.approx(200.0)
```

- [ ] **Step 2: Run, verify pass (4 PASS)**

```bash
uv run pytest tests/test_sim_metrics.py -v -k "liquidation or non_liquidation"
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_sim_metrics.py
git commit -m "$(cat <<'EOF'
test(obs-phase2): T4 collect_roundtrips liquidation + invariants

Liquidation close_cycle_id=None / pnl from trade_actions (pnl_cap) /
missing-trade_action invariant violation + stderr msg / non-liquidation
recompute from lot.entry_px. 4 tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `collect_roundtrips` stale amount + close-no-lot + diverge

**Spec:** §4.4 项 5 (stale amount fix) / 项 1 (unclosed) / 项 2 (close-no-lot warn) / 项 8 (diverge).

**Files:**
- Modify: `tests/test_sim_metrics.py`

**TDD note (per C-7):** all paths implemented in T2. Expected PASS.

- [ ] **Step 1: Write 8 tests**

```python
async def test_collect_roundtrips_stale_sl_amount_derived_from_fee(db_engine):
    """SL order.amount=0.2 stale, position 0.05; fee derived 0.05; rt.amount=0.05."""
    sid = await make_session(db_engine, fee_rate=0.0005)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.05)
    # fee = 0.05 * 82000 * 0.0005 = 2.05; pass explicit stale amount=0.2 with that fee
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="stop",
                          amount=0.2, fee=2.05, pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].amount == pytest.approx(0.05)
    assert caveats["stale_close_amount_count"] == 0  # derive succeeded


async def test_collect_roundtrips_stale_amount_fallback_to_order_amount(db_engine, capsys):
    """fee=0 → derivation fails → fallback sim_orders.amount + 2 caveats:
    stale_close_amount_count=1 (derive failed) AND invariant_violations=1
    (close_remaining=0.15 unmatched after consuming the only 0.05 lot).
    """
    sid = await make_session(db_engine, fee_rate=0.0005)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.05)
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="stop",
                          amount=0.2, fee=0.0, pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert caveats["stale_close_amount_count"] == 1
    assert caveats["invariant_violations"] == 1  # 0.15 unmatched after lot exhausted
    assert "no preceding open lot" in capsys.readouterr().err


async def test_collect_roundtrips_unclosed_lot(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_open_lot(db_engine, sid, cycle_id="c1")
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert rts == []
    assert caveats["unclosed_lot_count"] == {"long": 1, "short": 0}


async def test_collect_roundtrips_close_no_lot_warning(db_engine, capsys):
    """Close fill with no preceding lot → stderr warning + invariant_violations += 1."""
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_close_fill(db_engine, sid, cycle_id="c1", exit_type="market",
                          amount=0.1, pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert rts == []
    assert caveats["invariant_violations"] == 1
    err = capsys.readouterr().err
    assert "no preceding open lot" in err


async def test_collect_roundtrips_cycle_id_5_enum_join(db_engine):
    """open_cycle_id resolves via v_order_lifecycle (5-enum), not order_filled."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", pnl_gross=200.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].open_cycle_id == "c1"
    assert rts[0].close_cycle_id == "c2"


async def test_collect_roundtrips_duration_seconds(db_engine):
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    base = R2_7_MERGED_AT + timedelta(days=2)
    await make_open_lot(db_engine, sid, cycle_id="c1", filled_at=base)
    await make_close_fill(db_engine, sid, cycle_id="c2",
                          filled_at=base + timedelta(minutes=15), pnl_gross=100.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].duration_seconds == 15 * 60


async def test_collect_roundtrips_partial_close_lot_pnl_diverges_from_sim_weighted(db_engine):
    """Spec §4.4 item 8: lot1=100/1 + lot2=200/1 + close 0.5@150
    → FIFO lot pnl=+25, sim weighted=0; both legitimate."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=100, amount=1.0)
    await make_open_lot(db_engine, sid, cycle_id="c2", entry_px=200, amount=1.0)
    await make_close_fill(db_engine, sid, cycle_id="c3", exit_px=150, amount=0.5,
                          pnl_gross=0.0)  # sim weighted = 0
    rts, _ = await collect_roundtrips(db_engine, sid)
    # lot1 consumed 0.5 → (150-100)*0.5 = +25
    assert rts[0].pnl_gross == pytest.approx(25.0)


async def test_collect_roundtrips_full_close_lot_pnl_matches_sim_weighted(db_engine):
    """All lots fully closed → sum(FIFO lot pnl_gross) == sim realized."""
    sid = await make_session(db_engine, fee_rate=0.0005)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=100, amount=1.0)
    await make_open_lot(db_engine, sid, cycle_id="c2", entry_px=200, amount=1.0)
    await make_close_fill(db_engine, sid, cycle_id="c3", exit_px=150, amount=2.0,
                          pnl_gross=0.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert sum(rt.pnl_gross for rt in rts) == pytest.approx(0.0, abs=0.01)
```

- [ ] **Step 2: Run, verify all collect_roundtrips tests pass (~24 cumulative)**

```bash
uv run pytest tests/test_sim_metrics.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_sim_metrics.py
git commit -m "$(cat <<'EOF'
test(obs-phase2): T5 collect_roundtrips stale amount + close-no-lot + diverge

Stale SL fee-derive + 0-fee fallback caveat + unclosed lot + close-no-lot
stderr + cycle_id 5-enum JOIN + duration_seconds + FIFO vs sim-weighted
legitimate diverge + full-close mathematical identity. 8 tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: PnL metric functions (P1-P10)

**Spec:** §3.1 / §4.4 项 6 (P2 semantic clarity).

**Files:**
- Modify: `scripts/_sim_metrics.py` (add PnL functions)
- Modify: `tests/test_sim_metrics.py`

- [ ] **Step 1: Write failing tests**

```python
from scripts._sim_metrics import (
    win_rate, total_pnl_net, roundtrip_count,
    avg_fifo_pnl_per_roundtrip,
    avg_roundtrip_duration_min, median_roundtrip_duration_min,
    max_drawdown_pct, exit_type_distribution,
    largest_win_loss, profit_factor,
)


def _rt(pnl_net=10.0, duration=60, exit_type="market", side="long"):
    """Roundtrip stub for unit tests."""
    now = datetime(2026, 5, 5, tzinfo=timezone.utc)
    return Roundtrip(
        open_at=now, close_at=now, open_cycle_id=None, close_cycle_id=None,
        side=side, entry_px=0, exit_px=0, amount=0, leverage=1,
        pnl_gross=pnl_net, fee_open_share=0, fee_close_share=0, fee_total=0,
        pnl_net=pnl_net, duration_seconds=duration, exit_type=exit_type,
    )


def test_win_rate_basic():
    rts = [_rt(pnl_net=10), _rt(pnl_net=-5), _rt(pnl_net=20)]
    assert win_rate(rts) == pytest.approx(2 / 3)


def test_win_rate_all_wins_returns_100pct():
    assert win_rate([_rt(pnl_net=10), _rt(pnl_net=20)]) == pytest.approx(1.0)


def test_win_rate_zero_roundtrips_returns_none():
    assert win_rate([]) is None


def test_roundtrip_count():
    assert roundtrip_count([_rt(), _rt(), _rt()]) == 3
    assert roundtrip_count([]) == 0


def test_avg_fifo_pnl_per_roundtrip_uses_lot_mean():
    assert avg_fifo_pnl_per_roundtrip([_rt(pnl_net=10), _rt(pnl_net=-4)]) == pytest.approx(3.0)


def test_avg_fifo_pnl_per_roundtrip_zero_returns_none():
    assert avg_fifo_pnl_per_roundtrip([]) is None


def test_avg_roundtrip_duration_min():
    rts = [_rt(duration=120), _rt(duration=180)]  # 2 min, 3 min
    assert avg_roundtrip_duration_min(rts) == pytest.approx(2.5)


def test_median_roundtrip_duration_min():
    rts = [_rt(duration=60), _rt(duration=120), _rt(duration=300)]
    assert median_roundtrip_duration_min(rts) == pytest.approx(2.0)


def test_largest_win_loss():
    rts = [_rt(pnl_net=10), _rt(pnl_net=-50), _rt(pnl_net=80)]
    win, loss = largest_win_loss(rts)
    assert win == 80.0
    assert loss == -50.0


def test_largest_win_loss_no_roundtrips():
    win, loss = largest_win_loss([])
    assert win is None and loss is None


def test_profit_factor_basic():
    rts = [_rt(pnl_net=100), _rt(pnl_net=-50)]  # 100/50 = 2.0
    assert profit_factor(rts) == pytest.approx(2.0)


def test_profit_factor_all_wins_returns_none():
    assert profit_factor([_rt(pnl_net=10), _rt(pnl_net=20)]) is None


def test_profit_factor_all_losses_returns_none():
    assert profit_factor([_rt(pnl_net=-10), _rt(pnl_net=-20)]) is None


def test_profit_factor_zero_returns_none():
    assert profit_factor([]) is None


def test_exit_type_distribution_dict_format_5_keys():
    rts = [_rt(exit_type="market"), _rt(exit_type="market"), _rt(exit_type="stop"),
           _rt(exit_type="take_profit"), _rt(exit_type="liquidation")]
    dist = exit_type_distribution(rts)
    assert set(dist.keys()) == {"market", "stop", "take_profit", "limit", "liquidation"}
    assert dist["market"] == pytest.approx(2 / 5)
    assert dist["limit"] == 0


async def test_max_drawdown_pct_uses_total_usdt_not_free(db_engine):
    """state_snapshot.balance.total_usdt timeseries; sessions.initial_balance start."""
    import json
    sid = await make_session(db_engine, initial_balance=100.0)
    snap = lambda total: json.dumps({"balance": {"total_usdt": total, "free_usdt": 50.0}})
    await make_cycle(db_engine, sid, "c1", state_snapshot=snap(100.0))
    await make_cycle(db_engine, sid, "c2", state_snapshot=snap(120.0))  # peak
    await make_cycle(db_engine, sid, "c3", state_snapshot=snap(90.0))   # 25% dd
    dd = await max_drawdown_pct(db_engine, sid)
    assert dd == pytest.approx(0.25)


async def test_total_pnl_net_uses_sim_realized_minus_roundtrip_fees(db_engine):
    """P2 = sum(close trade_actions.pnl) - sum(roundtrip.fee_total).

    Use auto-fee (per C-2): open_fee = 0.1*80000*0.0005 = 4.0;
    close_fee = 0.1*82000*0.0005 = 4.1; rt.fee_total = 8.1.
    P2 = 200 (gross) - 8.1 = 191.9.
    """
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", pnl_gross=200.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    p2 = await total_pnl_net(db_engine, sid, rts)
    assert p2 == pytest.approx(191.9, abs=0.01)


async def test_total_pnl_net_excludes_unclosed_lot_open_fee(db_engine):
    """Lot1 fully paired (fee_total 8.1); lot2 still open (open_fee 4.0 NOT in P2).

    Auto-fee (per C-2): each open=4.0, close=4.1.
    P2 = 100 (gross from lot1 close) - 8.1 (rt.fee_total) = 91.9.
    Lot2's open_fee 4.0 stays attributed to it (待将来 close 才入对应 rt).
    """
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", pnl_gross=100.0)
    await make_open_lot(db_engine, sid, cycle_id="c3")  # still open
    rts, _ = await collect_roundtrips(db_engine, sid)
    p2 = await total_pnl_net(db_engine, sid, rts)
    assert p2 == pytest.approx(91.9, abs=0.01)
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement PnL functions in `scripts/_sim_metrics.py`**

```python
import json
import statistics


def win_rate(rts: list[Roundtrip]) -> float | None:
    if not rts:
        return None
    return sum(1 for rt in rts if rt.pnl_net > 0) / len(rts)


def roundtrip_count(rts: list[Roundtrip]) -> int:
    return len(rts)


def avg_fifo_pnl_per_roundtrip(rts: list[Roundtrip]) -> float | None:
    if not rts:
        return None
    return statistics.mean(rt.pnl_net for rt in rts)


def avg_roundtrip_duration_min(rts: list[Roundtrip]) -> float | None:
    if not rts:
        return None
    return statistics.mean(rt.duration_seconds / 60 for rt in rts)


def median_roundtrip_duration_min(rts: list[Roundtrip]) -> float | None:
    if not rts:
        return None
    return statistics.median(rt.duration_seconds / 60 for rt in rts)


def largest_win_loss(rts: list[Roundtrip]) -> tuple[float | None, float | None]:
    if not rts:
        return None, None
    pnls = [rt.pnl_net for rt in rts]
    return max(pnls), min(pnls)


def profit_factor(rts: list[Roundtrip]) -> float | None:
    if not rts:
        return None
    wins = sum(rt.pnl_net for rt in rts if rt.pnl_net > 0)
    losses = sum(rt.pnl_net for rt in rts if rt.pnl_net < 0)
    if wins == 0 or losses == 0:
        return None
    return wins / abs(losses)


def exit_type_distribution(rts: list[Roundtrip]) -> dict[str, float]:
    keys = ["market", "stop", "take_profit", "limit", "liquidation"]
    counts = {k: 0 for k in keys}
    for rt in rts:
        counts[rt.exit_type] = counts.get(rt.exit_type, 0) + 1
    total = len(rts) or 1
    return {k: counts.get(k, 0) / total for k in keys}


def _percentile(sorted_values: list[float], p: int) -> float | None:
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


async def max_drawdown_pct(engine, session_id: str) -> float | None:
    async with engine.connect() as conn:
        sess = (await conn.execute(text(
            "SELECT initial_balance FROM sessions WHERE id = :sid"
        ), {"sid": session_id})).first()
        if not sess:
            return None
        rows = (await conn.execute(text("""
            SELECT state_snapshot FROM agent_cycles
            WHERE session_id = :sid AND state_snapshot IS NOT NULL
            ORDER BY id ASC
        """), {"sid": session_id})).all()
    if not rows:
        return None
    totals = [sess.initial_balance]
    for r in rows:
        try:
            totals.append(json.loads(r.state_snapshot)["balance"]["total_usdt"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    if len(totals) < 2:
        return None
    peak = totals[0]
    max_dd = 0.0
    for v in totals:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return max_dd


async def total_pnl_net(engine, session_id: str, rts: list[Roundtrip]) -> float:
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT COALESCE(SUM(ta.pnl), 0) AS gross
            FROM trade_actions ta
            JOIN sim_orders so ON so.order_id = ta.order_id
            WHERE ta.session_id = :sid
              AND ta.action = 'order_filled'
              AND ((so.position_side = 'long' AND so.side = 'sell')
                OR (so.position_side = 'short' AND so.side = 'buy'))
        """), {"sid": session_id})).first()
    gross = row.gross or 0.0
    return gross - sum(rt.fee_total for rt in rts)
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/_sim_metrics.py tests/test_sim_metrics.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T6 PnL metric functions (P1-P10)

win_rate / total_pnl_net / roundtrip_count / avg_fifo_pnl_per_roundtrip /
avg+median duration / max_drawdown_pct (raw state_snapshot.total_usdt) /
exit_type_distribution (5-key zero-fill) / largest_win_loss / profit_factor.
P2 verified to exclude unclosed lot's open_fee. ~17 tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Cost metric functions (C1-C8)

**Spec:** §3.2.

**Files:**
- Modify: `scripts/_sim_metrics.py`
- Modify: `tests/test_sim_metrics.py`

- [ ] **Step 1: Failing tests**

```python
from scripts._sim_metrics import (
    cost_token_sums, avg_cache_hit_rate,
    tokens_per_cycle_percentile,
    avg_wall_time_ms, avg_llm_call_ms, avg_tool_total_ms,
    per_tool_call_top10,
)


async def test_cost_token_sums_from_view(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1", input_tokens=1000, output_tokens=200, cache_read_tokens=700)
    await make_cycle(db_engine, sid, "c2", input_tokens=2000, output_tokens=300, cache_read_tokens=1500)
    sums = await cost_token_sums(db_engine, sid)
    assert sums["total_input_tokens"] == 3000
    assert sums["total_output_tokens"] == 500
    assert sums["total_cache_read_tokens"] == 2200


async def test_avg_cache_hit_rate_weighted_by_input_tokens(db_engine):
    """(1000*0.7 + 2000*0.75) / 3000 = 2200/3000."""
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1", input_tokens=1000, cache_read_tokens=700)
    await make_cycle(db_engine, sid, "c2", input_tokens=2000, cache_read_tokens=1500)
    rate = await avg_cache_hit_rate(db_engine, sid)
    assert rate == pytest.approx(2200 / 3000)


async def test_avg_cache_hit_rate_all_zero_returns_none(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1", input_tokens=0, cache_read_tokens=0)
    assert await avg_cache_hit_rate(db_engine, sid) is None


async def test_tokens_per_cycle_percentile(db_engine):
    """For sorted [100..1000] (10 values, indices 0..9), linear interp:
       p50: k = 9*0.5 = 4.5 → 500 + (600-500)*0.5 = 550
       p95: k = 9*0.95 = 8.55 → 900 + (1000-900)*0.55 = 955
    Tight assertions catch both algorithm bugs AND fixture drift.
    """
    sid = await make_session(db_engine)
    for i, t in enumerate([100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]):
        await make_cycle(db_engine, sid, f"c{i}",
                         input_tokens=t, output_tokens=0, cache_read_tokens=0)
    p50 = await tokens_per_cycle_percentile(db_engine, sid, 50)
    p95 = await tokens_per_cycle_percentile(db_engine, sid, 95)
    assert p50 == pytest.approx(550)
    assert p95 == pytest.approx(955)


async def test_avg_wall_time_ms(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1", wall_time_ms=1000)
    await make_cycle(db_engine, sid, "c2", wall_time_ms=2000)
    assert await avg_wall_time_ms(db_engine, sid) == pytest.approx(1500)


async def test_per_tool_call_top10_aggregation(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    from sqlalchemy import insert
    from src.storage.models import ToolCall
    async with db_engine.begin() as conn:
        for tool in ["get_market_state"] * 5 + ["read_alerts"] * 3 + ["set_next_wake"] * 1:
            await conn.execute(insert(ToolCall).values(
                session_id=sid, cycle_id="c1", tool_name=tool,
                status="ok", duration_ms=100,
            ))
    top = await per_tool_call_top10(db_engine, sid)
    assert top[0] == ("get_market_state", 5)
    assert top[1] == ("read_alerts", 3)
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

```python
async def cost_token_sums(engine, session_id: str) -> dict[str, int]:
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) AS total_cache_read_tokens
            FROM v_cycle_metrics WHERE session_id = :sid
        """), {"sid": session_id})).first()
    return {
        "total_input_tokens": row.total_input_tokens,
        "total_output_tokens": row.total_output_tokens,
        "total_cache_read_tokens": row.total_cache_read_tokens,
    }


async def avg_cache_hit_rate(engine, session_id: str) -> float | None:
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT SUM(input_tokens) AS total_in, SUM(cache_read_tokens) AS total_cache
            FROM v_cycle_metrics WHERE session_id = :sid
        """), {"sid": session_id})).first()
    if not row.total_in:
        return None
    return row.total_cache / row.total_in


async def tokens_per_cycle_percentile(engine, session_id: str, p: int) -> float | None:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT tokens_consumed FROM v_cycle_metrics
            WHERE session_id = :sid AND tokens_consumed IS NOT NULL
            ORDER BY tokens_consumed
        """), {"sid": session_id})).all()
    return _percentile([r.tokens_consumed for r in rows], p)


_AVG_COLUMN_ALLOWED = frozenset({
    "wall_time_ms", "llm_call_ms", "tool_total_ms",
    "decision_length", "reasoning_tokens",
})


async def _avg_view_column(engine, session_id: str, col: str) -> float | None:
    """Internal: AVG over a whitelisted v_cycle_metrics column.

    SQL identifier must be interpolated (DB-API can't bind column names);
    the whitelist defends against accidental misuse from future contributors.
    """
    if col not in _AVG_COLUMN_ALLOWED:
        raise ValueError(f"_avg_view_column: column {col!r} not in whitelist")
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            f"SELECT AVG({col}) AS avg_val FROM v_cycle_metrics WHERE session_id = :sid"
        ), {"sid": session_id})).first()
    return row.avg_val


async def avg_wall_time_ms(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "wall_time_ms")


async def avg_llm_call_ms(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "llm_call_ms")


async def avg_tool_total_ms(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "tool_total_ms")


async def per_tool_call_top10(engine, session_id: str) -> list[tuple[str, int]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT tool_name, COUNT(*) AS cnt FROM tool_calls
            WHERE session_id = :sid
            GROUP BY tool_name ORDER BY cnt DESC LIMIT 10
        """), {"sid": session_id})).all()
    return [(r.tool_name, r.cnt) for r in rows]
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/_sim_metrics.py tests/test_sim_metrics.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T7 Cost metric functions (C1-C8)

Token sums + weighted cache_hit_rate + percentile (p50/p95) + avg
wall_time/llm/tool_total (C7 pair) + per_tool top-10. ~6 tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Behavior metric functions (B1-B10)

**Spec:** §3.3 / §3.5 caveat 1 (hold 双义) / §3.5 caveat 3 (anchor false-positive accepted).

**Files:**
- Modify: `scripts/_sim_metrics.py`
- Modify: `tests/test_sim_metrics.py`

- [ ] **Step 1: Failing tests**

```python
from scripts._sim_metrics import (
    total_cycles, ok_vs_forensic_count,
    triggered_by_distribution, decision_type_distribution,
    five_field_complete_rate, per_field_hit_rate,
    avg_decision_length_chars, decision_length_p95,
    retraction_rate, avg_reasoning_tokens, avg_thinking_chars,
    alert_lifecycle_summary, extract_stance,
)


async def test_total_cycles_count(db_engine):
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    assert await total_cycles(db_engine, sid) == 3


async def test_ok_vs_forensic_count(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1", execution_status="ok")
    await make_cycle(db_engine, sid, "c2", execution_status="ok")
    await make_cycle(db_engine, sid, "c3", execution_status="usage_limit_exceeded")
    counts = await ok_vs_forensic_count(db_engine, sid)
    assert counts["ok"] == 2
    assert counts["forensic"] == 1


async def test_decision_type_distribution_hold_double_meaning(db_engine):
    """Spec §3.5 caveat 1: hold (pure-observation) vs hold (wake-only)."""
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")  # no trade_action → pure-observation
    await make_cycle(db_engine, sid, "c2")
    from sqlalchemy import insert
    from src.storage.models import TradeAction
    async with db_engine.begin() as conn:
        # c2 only set_next_wake → wake-only
        await conn.execute(insert(TradeAction).values(
            session_id=sid, cycle_id="c2", action="set_next_wake",
            symbol="BTC/USDT:USDT",
        ))
    dist = await decision_type_distribution(db_engine, sid)
    assert dist.get("hold (pure-observation)") == 1
    assert dist.get("hold (wake-only)") == 1


async def test_decision_type_distribution_excludes_order_filled(db_engine):
    """make_close_fill writes both close_position + order_filled for the
    same cycle. Distribution must record close_position (decision intent),
    not order_filled (sim bookkeeping).
    """
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", pnl_gross=100.0)
    dist = await decision_type_distribution(db_engine, sid)
    assert dist.get("close_position") == 1
    assert dist.get("order_filled", 0) == 0  # bookkeeping filtered out
    assert dist.get("open_position") == 1


async def test_decision_type_distribution_priority_deterministic(db_engine):
    """Cycle with both close_position + place_limit_order → close_position
    wins by priority (deterministic; not PYTHONHASHSEED-dependent).
    """
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    from sqlalchemy import insert
    from src.storage.models import TradeAction
    async with db_engine.begin() as conn:
        for action in ["place_limit_order", "close_position"]:
            await conn.execute(insert(TradeAction).values(
                session_id=sid, cycle_id="c1", action=action,
                symbol="BTC/USDT:USDT",
            ))
    dist = await decision_type_distribution(db_engine, sid)
    assert dist.get("close_position") == 1
    # place_limit_order NOT double-counted (priority picked close_position first)
    assert dist.get("place_limit_order", 0) == 0


def test_extract_stance_basic():
    assert extract_stance("**(1) Stance**: bull\n...") == "bull"
    assert extract_stance("(1) Stance: BEAR\n") == "bear"
    assert extract_stance("nothing here") is None
    assert extract_stance(None) is None


def test_retraction_rate_cycle_to_cycle_stance_change():
    class _C:
        def __init__(self, cid, decision, status="ok"):
            self.cycle_id = cid; self.decision = decision; self.execution_status = status
    cycles = [
        _C("c1", "(1) Stance: bull"),
        _C("c2", "(1) Stance: bull"),
        _C("c3", "(1) Stance: bear"),       # retraction
        _C("c4", "(1) Stance: bear"),
        _C("c5", "(1) Stance: neutral"),    # retraction
    ]
    assert retraction_rate(cycles) == pytest.approx(2 / 4)


def test_retraction_rate_zero_pairs_returns_none():
    assert retraction_rate([]) is None


async def test_5field_complete_rate_uses_view_column(db_engine):
    sid = await make_session(db_engine)
    complete = ("(1) Stance: bull\n(2) Active commitments: x\n"
                "(3) This-cycle delta: x\n(4) Thesis invalidation: x\n(5) Watch list: x")
    incomplete = "(1) Stance: bull"
    await make_cycle(db_engine, sid, "c1", decision=complete)
    await make_cycle(db_engine, sid, "c2", decision=incomplete)
    rate = await five_field_complete_rate(db_engine, sid)
    assert rate == pytest.approx(0.5)


async def test_per_field_hit_rate_5_keys(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1", decision="(1) Stance: bull")
    rates = await per_field_hit_rate(db_engine, sid)
    assert set(rates.keys()) == {
        "has_stance", "has_active_commitments", "has_this_cycle_delta",
        "has_thesis_invalidation", "has_watch_list",
    }


async def test_alert_lifecycle_summary_from_view(db_engine):
    """Smoke: empty session → key set fixed (cancel_attempt_count column from view)."""
    sid = await make_session(db_engine)
    summary = await alert_lifecycle_summary(db_engine, sid)
    assert "triggered_rate" in summary
    assert "cancelled_rate" in summary
    assert "avg_cancel_attempt_count" in summary  # matches v_alert_lifecycle column
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement** (note: `cancel_attempt_count` matches view column at views.py:142)

```python
import re


STANCE_RE = re.compile(
    r"(?:^|\n)\s*(?:\*\*)?\(?1\)?\.?\s*(?:\*\*)?\s*[Ss]tance(?:\*\*)?\s*[:：]\s*"
    r"(?:\*\*)?(\w+)",
    re.MULTILINE,
)


def extract_stance(decision: str | None) -> str | None:
    if not decision:
        return None
    m = STANCE_RE.search(decision)
    return m.group(1).lower().strip() if m else None


def retraction_rate(cycles) -> float | None:
    """cycle N stance ≠ cycle N-1 stance ratio. None when 0 valid pairs.

    Caveat (spec §3.5 item 3): substring-LIKE not anchored; R2-Next-A
    priors-injection引述 may inflate count. Accepted first-cut precision.
    """
    valid = [(c.cycle_id, extract_stance(c.decision))
             for c in cycles if c.execution_status == "ok"]
    pairs = [(prev, curr) for prev, curr in zip(valid, valid[1:])
             if prev[1] is not None and curr[1] is not None]
    if not pairs:
        return None
    return sum(1 for prev, curr in pairs if prev[1] != curr[1]) / len(pairs)


async def total_cycles(engine, session_id: str) -> int:
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT COUNT(*) AS n FROM v_cycle_metrics WHERE session_id = :sid"
        ), {"sid": session_id})).first()
    return row.n


async def ok_vs_forensic_count(engine, session_id: str) -> dict[str, int]:
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT
              SUM(CASE WHEN is_ok_cycle = 1 THEN 1 ELSE 0 END) AS ok,
              SUM(CASE WHEN is_forensic_cycle = 1 THEN 1 ELSE 0 END) AS forensic
            FROM v_cycle_metrics WHERE session_id = :sid
        """), {"sid": session_id})).first()
    return {"ok": row.ok or 0, "forensic": row.forensic or 0}


async def triggered_by_distribution(engine, session_id: str) -> dict[str, int]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT triggered_by, COUNT(*) AS cnt FROM v_cycle_metrics
            WHERE session_id = :sid GROUP BY triggered_by
        """), {"sid": session_id})).all()
    return {r.triggered_by: r.cnt for r in rows}


DECISION_ACTION_PRIORITY: list[str] = [
    "open_position",
    "close_position",
    "place_limit_order",
    "set_stop_loss",
    "set_take_profit",
    "add_price_level_alert",
    "cancel_price_level_alert",
]


async def decision_type_distribution(engine, session_id: str) -> dict[str, int]:
    """§3.5 caveat 1: hold (pure-observation) vs hold (wake-only).

    Determinism (R3 fix): SQL filters out 'order_filled' (sim bookkeeping,
    not a decision) so multi-action cycles aren't polluted by fill events.
    Python uses DECISION_ACTION_PRIORITY (fixed order) to pick a primary
    action when a cycle has multiple decision actions — avoids set-iteration
    non-determinism (PYTHONHASHSEED-dependent).
    """
    async with engine.connect() as conn:
        active_rows = (await conn.execute(text("""
            SELECT cycle_id,
                   GROUP_CONCAT(DISTINCT action) AS actions,
                   COUNT(DISTINCT action) AS distinct_count
            FROM trade_actions
            WHERE session_id = :sid
              AND action != 'order_filled'   -- drop sim bookkeeping (not a decision)
            GROUP BY cycle_id
        """), {"sid": session_id})).all()
        all_rows = (await conn.execute(text(
            "SELECT cycle_id FROM agent_cycles WHERE session_id = :sid"
        ), {"sid": session_id})).all()
    active_ids = {r.cycle_id for r in active_rows}
    all_ids = {r.cycle_id for r in all_rows}
    pure_obs = all_ids - active_ids
    dist: dict[str, int] = {"hold (pure-observation)": len(pure_obs)} if pure_obs else {}
    for r in active_rows:
        # GROUP BY + WHERE filter ensures GROUP_CONCAT(DISTINCT action) is non-empty
        # for any row in active_rows; the `or ""` guard is defensive only — if
        # SQLite ever returned empty/NULL it would split to {""} which doesn't
        # match any priority-list entry and falls through to wake-only branch.
        actions = set((r.actions or "").split(","))
        if actions == {"set_next_wake"}:
            dist["hold (wake-only)"] = dist.get("hold (wake-only)", 0) + 1
            continue
        # Pick primary action by fixed priority (deterministic)
        for primary in DECISION_ACTION_PRIORITY:
            if primary in actions:
                dist[primary] = dist.get(primary, 0) + 1
                break
        else:
            # No priority-list action matched — only set_next_wake plus
            # something unknown; treat as wake-only (defensive; should not
            # happen for sessions written by current src/cli/app.py paths).
            dist["hold (wake-only)"] = dist.get("hold (wake-only)", 0) + 1
    return dist


async def five_field_complete_rate(engine, session_id: str) -> float | None:
    """Reads v_cycle_metrics.five_field_complete column.

    Pre-existing schema caveat (Phase 1 PR #42): despite the name,
    `five_field_complete` checks only 4 anchors (stance + active_commitments
    + this_cycle_delta + thesis_invalidation; **excludes** has_watch_list)
    — see views.py:74-76 `>= 4`. This metric is "first-4 fields complete
    rate" semantically. Renaming is W3 follow-up; Phase 2 sticks with
    column name to avoid view churn.
    """
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT AVG(CAST(five_field_complete AS REAL)) AS rate
            FROM v_cycle_metrics WHERE session_id = :sid
        """), {"sid": session_id})).first()
    return row.rate


async def per_field_hit_rate(engine, session_id: str) -> dict[str, float | None]:
    fields = ["has_stance", "has_active_commitments", "has_this_cycle_delta",
              "has_thesis_invalidation", "has_watch_list"]
    out: dict[str, float | None] = {}
    async with engine.connect() as conn:
        for f in fields:
            row = (await conn.execute(text(
                f"SELECT AVG(CAST({f} AS REAL)) AS rate FROM v_cycle_metrics WHERE session_id = :sid"
            ), {"sid": session_id})).first()
            out[f] = row.rate
    return out


async def avg_decision_length_chars(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "decision_length")


async def decision_length_p95(engine, session_id: str) -> float | None:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT decision_length FROM v_cycle_metrics
            WHERE session_id = :sid AND decision_length IS NOT NULL
            ORDER BY decision_length
        """), {"sid": session_id})).all()
    return _percentile([r.decision_length for r in rows], 95)


async def avg_reasoning_tokens(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "reasoning_tokens")


async def avg_thinking_chars(engine, session_id: str) -> float | None:
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT AVG(LENGTH(reasoning)) AS avg_chars FROM agent_cycles
            WHERE session_id = :sid AND reasoning IS NOT NULL
        """), {"sid": session_id})).first()
    return row.avg_chars


async def alert_lifecycle_summary(engine, session_id: str) -> dict:
    """Reads v_alert_lifecycle (column = cancel_attempt_count per views.py:142)."""
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT
              CAST(SUM(CASE WHEN triggered_at IS NOT NULL THEN 1 ELSE 0 END) AS REAL)
                / NULLIF(COUNT(*), 0) AS triggered_rate,
              CAST(SUM(CASE WHEN cancelled_at IS NOT NULL THEN 1 ELSE 0 END) AS REAL)
                / NULLIF(COUNT(*), 0) AS cancelled_rate,
              AVG(cancel_attempt_count) AS avg_cancel_attempt_count
            FROM v_alert_lifecycle WHERE session_id = :sid
        """), {"sid": session_id})).first()
    return {
        "triggered_rate": row.triggered_rate,
        "cancelled_rate": row.cancelled_rate,
        "avg_cancel_attempt_count": row.avg_cancel_attempt_count,
    }
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/_sim_metrics.py tests/test_sim_metrics.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T8 Behavior metric functions (B1-B10)

total_cycles + ok/forensic + triggered_by + decision_type (hold 双义 +
DECISION_ACTION_PRIORITY deterministic primary-action pick + SQL filter
on order_filled bookkeeping) + 5field_complete (caveat: column actually
checks 4 anchors, not 5; pre-existing schema) + per-field hit rate +
decision length avg/p95 + retraction_rate (regex stance, accepts §3.5
caveat 3 false positive) + reasoning tokens/chars + alert lifecycle
summary (v_alert_lifecycle column = cancel_attempt_count). ~11 tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: `assert_not_legacy` + naive datetime + caveats helpers (per-side + diff-only)

**Spec:** §6.4 / `memory r2_8b_legacy_decision_restore_boundary` / §6.3 (10 caveat types).

**Design note:** caveats split into two helpers with disjoint responsibilities:
- `render_caveats_per_side(rts, caveats, *, prefix, ok_cycle_count, forensic_count, null_field_summary)` — emits 8 per-session templates (1-7 + 10 from spec §6.3); used by analyze (`prefix=""`) and by diff once per side (`prefix="[A] "` / `"[B] "`).
- `render_caveats_diff_only(*, a_eq_b, cross_symbol)` — emits 2 diff-specific templates (8 + 9 from spec §6.3); only diff mode calls this.

This split avoids a flag-overloaded function where the diff-specific branch would otherwise risk re-emitting per-side messages with empty inputs.

**Files:**
- Modify: `scripts/_sim_metrics.py` (add `assert_not_legacy` + 2 caveat helpers)
- Modify: `tests/test_sim_metrics.py`

- [ ] **Step 1: Failing tests**

```python
from scripts._sim_metrics import (
    assert_not_legacy, render_caveats_per_side, render_caveats_diff_only,
)


def test_assert_not_legacy_post_cutoff_passes():
    class _S:
        name = "post"; created_at = R2_7_MERGED_AT + timedelta(days=1)
    assert_not_legacy(_S())


def test_assert_not_legacy_pre_cutoff_raises():
    class _S:
        name = "legacy"; created_at = R2_7_MERGED_AT - timedelta(days=1)
    with pytest.raises(SystemExit) as exc:
        assert_not_legacy(_S())
    assert "legacy sessions" in str(exc.value)


def test_assert_not_legacy_naive_datetime_normalized():
    """SQLite returns naive datetime; coerce to UTC, do not raise TypeError."""
    class _S:
        name = "naive_post"
        created_at = (R2_7_MERGED_AT + timedelta(days=1)).replace(tzinfo=None)
    assert_not_legacy(_S())


def test_assert_not_legacy_naive_pre_cutoff_raises():
    class _S:
        name = "naive_legacy"
        created_at = (R2_7_MERGED_AT - timedelta(days=1)).replace(tzinfo=None)
    with pytest.raises(SystemExit):
        assert_not_legacy(_S())


# Per-side caveats — 8 templates.

def _empty_caveats(*, unclosed=None, invariant=0, liquidation=0, stale=0):
    return {
        "unclosed_lot_count": unclosed or {"long": 0, "short": 0},
        "invariant_violations": invariant,
        "liquidation_count": liquidation,
        "stale_close_amount_count": stale,
    }


def test_render_caveats_per_side_zero_ok_cycles():
    out = render_caveats_per_side([], _empty_caveats(), prefix="",
                                  ok_cycle_count=0)
    assert "Session has 0 ok cycles" in out


def test_render_caveats_per_side_zero_roundtrips():
    out = render_caveats_per_side([], _empty_caveats(), prefix="",
                                  ok_cycle_count=10)
    assert "0 closed roundtrips" in out


def test_render_caveats_per_side_unclosed_lots():
    cv = _empty_caveats(unclosed={"long": 2, "short": 1})
    out = render_caveats_per_side([], cv, prefix="", ok_cycle_count=10)
    assert "3 unclosed lot(s)" in out
    assert "long: 2" in out and "short: 1" in out


def test_render_caveats_per_side_invariant():
    cv = _empty_caveats(invariant=2)
    out = render_caveats_per_side([_rt()], cv, prefix="", ok_cycle_count=10)
    assert "2 invariant violation(s)" in out


def test_render_caveats_per_side_liquidation():
    cv = _empty_caveats(liquidation=1)
    out = render_caveats_per_side([_rt()], cv, prefix="", ok_cycle_count=10)
    assert "1 liquidation event(s)" in out
    assert "pnl_cap" in out


def test_render_caveats_per_side_stale_close_amount():
    cv = _empty_caveats(stale=3)
    out = render_caveats_per_side([_rt()], cv, prefix="", ok_cycle_count=10)
    assert "3 stale close amount(s)" in out


def test_render_caveats_per_side_forensic():
    out = render_caveats_per_side([_rt()], _empty_caveats(), prefix="",
                                  ok_cycle_count=10, forensic_count=4)
    assert "4 forensic cycle(s)" in out


def test_render_caveats_per_side_null_pollution():
    out = render_caveats_per_side([_rt()], _empty_caveats(), prefix="",
                                  ok_cycle_count=10,
                                  null_field_summary=[("decision", 12)])
    assert "12 rows with NULL decision" in out


def test_render_caveats_per_side_prefix_decorates():
    """diff use case: prefix='[A] ' applied to all per-side messages."""
    cv = _empty_caveats(unclosed={"long": 1, "short": 0})
    out = render_caveats_per_side([], cv, prefix="[A] ", ok_cycle_count=10)
    assert "[A] 1 unclosed lot(s)" in out


# Diff-only caveats — 2 templates.

def test_render_caveats_diff_only_a_equals_b():
    out = render_caveats_diff_only(a_eq_b=True, cross_symbol=None)
    assert "WARNING: A and B refer to same session" in out


def test_render_caveats_diff_only_cross_symbol():
    out = render_caveats_diff_only(a_eq_b=False,
                                   cross_symbol=("BTC/USDT:USDT", "ETH/USDT:USDT"))
    assert "A=BTC/USDT:USDT, B=ETH/USDT:USDT" in out


def test_render_caveats_diff_only_neither():
    """Empty when no diff-specific condition fires."""
    out = render_caveats_diff_only(a_eq_b=False, cross_symbol=("BTC/USDT:USDT", "BTC/USDT:USDT"))
    assert out == ""


def test_render_caveats_diff_only_does_not_emit_per_side():
    """Sanity: diff-only never emits per-side template fragments."""
    out = render_caveats_diff_only(a_eq_b=True, cross_symbol=None)
    assert "0 closed roundtrips" not in out
    assert "unclosed lot" not in out
```

- [ ] **Step 2: Run, verify fail**

- [ ] **Step 3: Implement**

```python
def assert_not_legacy(session) -> None:
    created_at = session.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at < R2_7_MERGED_AT:
        raise SystemExit(
            f"Session '{session.name}' was created at {created_at.isoformat()} "
            f"(before R2-7 schema reframe at {R2_7_MERGED_AT.date()}); "
            f"legacy sessions are intentionally unsupported "
            f"(pre-R2-7 schema cutoff)."
        )


def render_caveats_per_side(
    rts, caveats, *, prefix: str,
    ok_cycle_count: int,
    forensic_count: int = 0,
    null_field_summary: list[tuple[str, int]] | None = None,
) -> str:
    """Emit 8 per-session caveat templates (spec §6.3 rows 1-7 + 10).

    Args:
      rts: roundtrips list (drives "0 closed roundtrips" branch).
      caveats: dict from collect_roundtrips (unclosed/invariant/liquidation/stale).
      prefix: '' for analyze single-sim; '[A] ' / '[B] ' for diff per-side.
      ok_cycle_count: drives "0 ok cycles" branch.
      forensic_count: drives "N forensic cycle(s)" branch.
      null_field_summary: list of (field, row_count) for >5% NULL fields.
    """
    null_field_summary = null_field_summary or []
    lines: list[str] = []

    if ok_cycle_count == 0:
        lines.append(f"- {prefix}Session has 0 ok cycles — all metrics N/A.")

    if not rts and ok_cycle_count > 0:
        lines.append(f"- {prefix}0 closed roundtrips — PnL metrics N/A.")

    unclosed = caveats.get("unclosed_lot_count", {"long": 0, "short": 0})
    n_unclosed = unclosed["long"] + unclosed["short"]
    if n_unclosed:
        lines.append(
            f"- {prefix}{n_unclosed} unclosed lot(s) at session end "
            f"(long: {unclosed['long']}, short: {unclosed['short']}) "
            f"excluded from roundtrip metrics."
        )

    if caveats.get("invariant_violations"):
        lines.append(
            f"- {prefix}{caveats['invariant_violations']} invariant violation(s) "
            f"detected — see stderr logs for details."
        )

    if caveats.get("liquidation_count"):
        lines.append(
            f"- {prefix}{caveats['liquidation_count']} liquidation event(s) — "
            f"close_cycle_id N/A (liquidation does not write 5-enum trade_action); "
            f"pnl read from trade_actions.pnl due to sim pnl_cap."
        )

    if caveats.get("stale_close_amount_count"):
        lines.append(
            f"- {prefix}{caveats['stale_close_amount_count']} stale close amount(s) — "
            f"actual_amount derivation failed (fee or fee_rate missing); "
            f"fell back to sim_orders.amount which may overstate close size."
        )

    if forensic_count:
        lines.append(
            f"- {prefix}{forensic_count} forensic cycle(s) "
            f"(execution_status != 'ok') — excluded from cycle averages."
        )

    for field, count in null_field_summary:
        lines.append(
            f"- {prefix}{count} rows with NULL {field} in agent_cycles — "
            f"affected metrics may be biased."
        )

    return "\n".join(lines)


def render_caveats_diff_only(
    *, a_eq_b: bool, cross_symbol: tuple[str, str] | None,
) -> str:
    """Emit 2 diff-specific caveat templates (spec §6.3 rows 8 + 9)."""
    lines: list[str] = []
    if a_eq_b:
        lines.append("- WARNING: A and B refer to same session — all deltas are zero.")
    if cross_symbol and cross_symbol[0] != cross_symbol[1]:
        lines.append(
            f"- WARNING: A={cross_symbol[0]}, B={cross_symbol[1]}; "
            f"PnL comparable in USDT but market context differs."
        )
    return "\n".join(lines)
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_sim_metrics.py -v -k "not_legacy or render_caveats"
```

- [ ] **Step 5: Commit**

```bash
git add scripts/_sim_metrics.py tests/test_sim_metrics.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T9 assert_not_legacy + caveats helpers (per-side + diff-only)

R2-7 cutoff fail-fast (per memory r2_8b_legacy_decision_restore_boundary)
+ naive datetime normalization. Two caveat helpers with disjoint
responsibilities: render_caveats_per_side (8 templates, prefix-aware) +
render_caveats_diff_only (2 templates). Analyze calls per_side once;
diff calls per_side twice ([A]/[B]) + diff_only once. ~16 tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: `analyze_sim.py` skeleton — argparse + session resolution + Fatal errors

**Spec:** §5.1 CLI / §6.1-§6.2 Fatal / §6.4 legacy fail-fast. **Convention:** C-3 sys.path boilerplate.

**Files:**
- Create: `scripts/analyze_sim.py`
- Create: `tests/test_analyze_sim.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_analyze_sim.py
"""End-to-end tests for scripts/analyze_sim.py via subprocess."""
from datetime import datetime, timedelta, timezone

import pytest
import subprocess
import sys

from tests._sim_fixtures import (
    make_session, make_cycle, make_open_lot, make_close_fill, _resolve_db_path,
)
from scripts._sim_metrics import R2_7_MERGED_AT


def _run_analyze(*args, db_path):
    cmd = [sys.executable, "scripts/analyze_sim.py", *args, "--db", str(db_path)]
    return subprocess.run(cmd, capture_output=True, text=True)


async def test_analyze_session_not_found_exit_1(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="real")
    r = _run_analyze("--session", "typo", db_path=db_path)
    assert r.returncode == 1
    assert "Session 'typo' not found" in r.stderr


async def test_analyze_db_file_missing_exit_1(tmp_path):
    r = _run_analyze("--session", "any", db_path=tmp_path / "nonexistent.db")
    assert r.returncode == 1
    assert "Database file not found" in r.stderr


async def test_analyze_session_by_name_resolves(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="my_friendly_name")
    await make_cycle(db_engine, await make_session_id(db_engine, "my_friendly_name"), "c1")
    r = _run_analyze("--session", "my_friendly_name", db_path=db_path)
    assert r.returncode == 0
    assert "my_friendly_name" in r.stdout


async def test_analyze_out_dir_missing_exit_1(db_engine, tmp_path):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    r = _run_analyze("--session", "test_sim",
                     "--out", str(tmp_path / "noexist" / "x.md"), db_path=db_path)
    assert r.returncode == 1
    assert "Output dir" in r.stderr


# Helper for resolving session_id by name:
async def make_session_id(engine, name) -> str:
    from sqlalchemy import text
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT id FROM sessions WHERE name = :n"),
                                  {"n": name})).first()
    return row.id if row else None
```

- [ ] **Step 2: Run, expect fail (script doesn't exist)**

- [ ] **Step 3: Implement `scripts/analyze_sim.py` skeleton with C-3 boilerplate**

```python
#!/usr/bin/env python3
"""Sim Analysis Report — single sim full-stack metrics → markdown.

Usage:
    python scripts/analyze_sim.py --session <id_or_name> [--db PATH] [--out FILE]

Spec: docs/superpowers/specs/2026-05-09-iter-w2r2-obs-phase2-design.md
"""
from __future__ import annotations

import sys
from pathlib import Path

# C-3: ensure repo root on sys.path so `from scripts.* / from src.*` works
# whether invoked as `python scripts/analyze_sim.py` (subprocess sys.path[0]
# = scripts/) or via pytest (CWD = repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import argparse  # noqa: E402
import asyncio  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from scripts._sim_metrics import (  # noqa: E402
    R2_7_MERGED_AT, METRIC_GROUPS,
    assert_not_legacy, collect_roundtrips, render_caveats_per_side,
)
from src.storage.models import Session as SessionModel  # noqa: E402


async def _resolve_session(engine, key: str):
    """UUID first; then sessions.name. Returns SessionModel or None."""
    async with engine.connect() as conn:
        row = (await conn.execute(
            select(SessionModel).where(SessionModel.id == key)
        )).first()
        if row:
            return row[0]
        row = (await conn.execute(
            select(SessionModel).where(SessionModel.name == key)
        )).first()
        return row[0] if row else None


async def amain(args):
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database file not found: {args.db}", file=sys.stderr)
        print("Use --db PATH to override (default: data/tradebot.db).",
              file=sys.stderr)
        sys.exit(1)

    if args.out:
        out_path = Path(args.out)
        if not out_path.parent.exists():
            print(f"Output dir {out_path.parent} does not exist.", file=sys.stderr)
            print("Create it first or use a different path.", file=sys.stderr)
            sys.exit(1)

    engine = create_async_engine(f"sqlite+aiosqlite:///{args.db}")
    try:
        session = await _resolve_session(engine, args.session)
        if session is None:
            print(f"Session '{args.session}' not found in {args.db}.", file=sys.stderr)
            print("Use --list-sessions to see candidates.", file=sys.stderr)
            sys.exit(1)
        assert_not_legacy(session)

        markdown = await render_analysis(engine, session)
        if args.out:
            Path(args.out).write_text(markdown)
        else:
            print(markdown)
    finally:
        await engine.dispose()


async def render_analysis(engine, session) -> str:
    """T11 fills out 3 sections + caveats. T10 stub: header only."""
    return f"# Sim Analysis Report\n\n- Session: {session.name}\n"


def main():
    p = argparse.ArgumentParser(description="Single-sim full-stack metrics → markdown")
    p.add_argument("--session", required=True, help="Session UUID or sessions.name")
    p.add_argument("--db", default="data/tradebot.db", help="DB path")
    p.add_argument("--out", default=None, help="Output file (default: stdout)")
    args = p.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run, verify Fatal-class tests pass**

```bash
uv run pytest tests/test_analyze_sim.py -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze_sim.py tests/test_analyze_sim.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T10 analyze_sim.py skeleton + Fatal-class errors

argparse + session UUID/name resolution + Fatal: session not found,
db file missing, out dir missing, legacy session. C-3 sys.path
boilerplate so subprocess invocation works. 4 e2e tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: `analyze_sim.py` markdown render — 3 sections + caveats via METRIC_GROUPS

**Spec:** §5.2 output / §5.5 precision / §5.6 sample / §6.3 caveats.

**Files:**
- Modify: `scripts/analyze_sim.py`
- Modify: `tests/test_analyze_sim.py`

- [ ] **Step 1: Failing tests**

```python
async def test_analyze_runs_on_minimal_session(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    await make_cycle(db_engine, sid, "c1")
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    assert r.returncode == 0
    for hdr in ["## PnL", "## Behavior", "## Cost", "## Caveats"]:
        assert hdr in r.stdout


async def test_analyze_renders_partial_close_correctly(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.2)
    await make_close_fill(db_engine, sid, cycle_id="c2", amount=0.05, pnl_gross=100.0)
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    assert r.returncode == 0
    assert "roundtrip_count" in r.stdout
    # 1 closed rt produced; lot still has remaining → caveat for unclosed
    assert "unclosed lot(s)" in r.stdout


async def test_analyze_renders_liquidation_in_exit_distribution(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="liquidation",
                          pnl_gross=-50.0)
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    assert "exit_type[liquidation]" in r.stdout
    assert "liquidation event(s)" in r.stdout


async def test_analyze_markdown_section_order_pnl_behavior_cost_caveats(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    await make_cycle(db_engine, sid, "c1")
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    out = r.stdout
    pnl = out.find("## PnL")
    beh = out.find("## Behavior")
    cost = out.find("## Cost")
    cav = out.find("## Caveats")
    assert 0 < pnl < beh < cost < cav


async def test_analyze_emits_all_28_metric_groups(db_engine):
    """Every key in METRIC_GROUPS shows up as a row label in stdout."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine)
    sid = await make_session_id(db_engine, "test_sim")
    decision = ("(1) Stance: bull\n(2) Active commitments: x\n"
                "(3) This-cycle delta: x\n(4) Thesis invalidation: x\n(5) Watch list: x")
    base = R2_7_MERGED_AT + timedelta(days=2)
    for i in range(10):
        await make_cycle(db_engine, sid, f"c{i}", decision=decision,
                         state_snapshot=f'{{"balance":{{"total_usdt":{100+i}}}}}')
    for i in range(3):
        oc, cc = f"c{2*i}", f"c{2*i+1}"
        await make_open_lot(db_engine, sid, cycle_id=oc,
                            filled_at=base + timedelta(minutes=i*20))
        await make_close_fill(db_engine, sid, cycle_id=cc, pnl_gross=10.0,
                              filled_at=base + timedelta(minutes=i*20+5))
    r = _run_analyze("--session", "test_sim", db_path=db_path)
    assert r.returncode == 0
    # Every METRIC_GROUPS key (or its split sub-rows) is present.
    # For pair groups (llm_tool_avg_pair / decision_length_avg_p95 /
    # reasoning_avg_pair / tokens_per_cycle_percentile), at least the canonical
    # sub-row name must appear. exit_type expands to 5 keys; per_field expands to 5.
    expected_substrings = [
        # PnL
        "win_rate", "total_pnl_net", "roundtrip_count",
        "avg_fifo_pnl_per_roundtrip", "avg_roundtrip_duration_min",
        "median_roundtrip_duration_min", "max_drawdown_pct",
        "exit_type[market]", "largest_win", "largest_loss", "profit_factor",
        # Cost
        "total_input_tokens", "total_output_tokens", "total_cache_read_tokens",
        "avg_cache_hit_rate", "tokens_per_cycle_p50", "tokens_per_cycle_p95",
        "avg_wall_time_ms", "avg_llm_call_ms", "avg_tool_total_ms",
        "per_tool_call_top10",
        # Behavior
        "total_cycles", "ok_count", "forensic_count",
        "triggered_by[", "decision_type[",
        "5field_complete_rate", "has_stance",
        "avg_decision_length_chars", "decision_length_p95",
        "retraction_rate", "avg_reasoning_tokens", "avg_thinking_chars",
        # alert_lifecycle_summary expands to 3 sub-rows (analyze + diff aligned)
        "alert_triggered_rate", "alert_cancelled_rate",
        "alert_avg_cancel_attempt_count",
    ]
    for s in expected_substrings:
        assert s in r.stdout, f"missing render: {s!r}"
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Implement section renderers** in `scripts/analyze_sim.py`

```python
from datetime import datetime, timezone

from scripts._sim_metrics import (
    win_rate, total_pnl_net, roundtrip_count,
    avg_fifo_pnl_per_roundtrip,
    avg_roundtrip_duration_min, median_roundtrip_duration_min,
    max_drawdown_pct, exit_type_distribution,
    largest_win_loss, profit_factor,
    cost_token_sums, avg_cache_hit_rate, tokens_per_cycle_percentile,
    avg_wall_time_ms, avg_llm_call_ms, avg_tool_total_ms,
    per_tool_call_top10,
    total_cycles, ok_vs_forensic_count, triggered_by_distribution,
    decision_type_distribution, five_field_complete_rate, per_field_hit_rate,
    avg_decision_length_chars, decision_length_p95,
    retraction_rate, avg_reasoning_tokens, avg_thinking_chars,
    alert_lifecycle_summary,
)


def _fmt_count(v): return "—" if v is None else f"{int(v):,}"
def _fmt_pct(v):   return "—" if v is None else f"{v*100:.1f}%"
def _fmt_pnl(v):   return "—" if v is None else f"{v:+.2f} USDT"
def _fmt_ms(v):    return "—" if v is None else f"{int(v)} ms"
def _fmt_dur(v):   return "—" if v is None else f"{v:.1f}"


async def render_analysis(engine, session) -> str:
    rts, caveats = await collect_roundtrips(engine, session.id)
    parts = [_render_header(session)]
    parts.append(await _render_pnl(engine, session, rts))
    parts.append(await _render_behavior(engine, session))
    parts.append(await _render_cost(engine, session))
    parts.append(await _render_caveats(engine, session, rts, caveats))
    return "\n\n".join(parts) + "\n"


def _render_header(session) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last = session.last_active_at or session.updated_at or session.created_at
    return (
        f"# Sim Analysis Report\n\n"
        f"- Session: {session.name} ({session.symbol}, "
        f"{session.created_at:%Y-%m-%d} → {last:%Y-%m-%d})\n"
        f"- Generated: {now}"
    )


async def _render_pnl(engine, session, rts) -> str:
    p2 = await total_pnl_net(engine, session.id, rts)
    dd = await max_drawdown_pct(engine, session.id)
    win, loss = largest_win_loss(rts)
    pf = profit_factor(rts)
    dist = exit_type_distribution(rts)
    rows = [
        ("total_pnl_net", _fmt_pnl(p2)),
        ("win_rate", _fmt_pct(win_rate(rts))),
        ("roundtrip_count", _fmt_count(roundtrip_count(rts))),
        ("avg_fifo_pnl_per_roundtrip", _fmt_pnl(avg_fifo_pnl_per_roundtrip(rts))),
        ("avg_roundtrip_duration_min", _fmt_dur(avg_roundtrip_duration_min(rts))),
        ("median_roundtrip_duration_min", _fmt_dur(median_roundtrip_duration_min(rts))),
        ("max_drawdown_pct", _fmt_pct(dd)),
        ("largest_win", _fmt_pnl(win)),
        ("largest_loss", _fmt_pnl(loss)),
        ("profit_factor", "—" if pf is None else f"{pf:.2f}"),
    ]
    for key in ["market", "stop", "take_profit", "limit", "liquidation"]:
        rows.append((f"exit_type[{key}]", _fmt_pct(dist[key])))
    return _two_col("PnL", rows)


async def _render_cost(engine, session) -> str:
    sums = await cost_token_sums(engine, session.id)
    rate = await avg_cache_hit_rate(engine, session.id)
    p50 = await tokens_per_cycle_percentile(engine, session.id, 50)
    p95 = await tokens_per_cycle_percentile(engine, session.id, 95)
    rows = [
        ("total_input_tokens", _fmt_count(sums["total_input_tokens"])),
        ("total_output_tokens", _fmt_count(sums["total_output_tokens"])),
        ("total_cache_read_tokens", _fmt_count(sums["total_cache_read_tokens"])),
        ("avg_cache_hit_rate", _fmt_pct(rate)),
        ("tokens_per_cycle_p50", _fmt_count(p50)),
        ("tokens_per_cycle_p95", _fmt_count(p95)),
        ("avg_wall_time_ms", _fmt_ms(await avg_wall_time_ms(engine, session.id))),
        ("avg_llm_call_ms", _fmt_ms(await avg_llm_call_ms(engine, session.id))),
        ("avg_tool_total_ms", _fmt_ms(await avg_tool_total_ms(engine, session.id))),
    ]
    top = await per_tool_call_top10(engine, session.id)
    if top:
        rows.append(("per_tool_call_top10",
                     ", ".join(f"{n}:{c}" for n, c in top)))
    else:
        rows.append(("per_tool_call_top10", "—"))
    return _two_col("Cost", rows)


async def _render_behavior(engine, session) -> str:
    counts = await ok_vs_forensic_count(engine, session.id)
    trig = await triggered_by_distribution(engine, session.id)
    dt = await decision_type_distribution(engine, session.id)
    pfh = await per_field_hit_rate(engine, session.id)
    summary = await alert_lifecycle_summary(engine, session.id)
    # retraction_rate needs full cycle list with decision
    from sqlalchemy import select
    from src.storage.models import AgentCycle
    async with engine.connect() as conn:
        cycles_rows = (await conn.execute(
            select(AgentCycle).where(AgentCycle.session_id == session.id)
                              .order_by(AgentCycle.id)
        )).all()
    cycles = [r[0] for r in cycles_rows]
    rows = [
        ("total_cycles", _fmt_count(await total_cycles(engine, session.id))),
        ("ok_count", _fmt_count(counts["ok"])),
        ("forensic_count", _fmt_count(counts["forensic"])),
    ]
    for k, v in trig.items():
        rows.append((f"triggered_by[{k}]", _fmt_count(v)))
    for k, v in dt.items():
        rows.append((f"decision_type[{k}]", _fmt_count(v)))
    rows.append(("5field_complete_rate", _fmt_pct(await five_field_complete_rate(engine, session.id))))
    for k, v in pfh.items():
        rows.append((k, _fmt_pct(v)))
    rows += [
        ("avg_decision_length_chars", _fmt_count(await avg_decision_length_chars(engine, session.id))),
        ("decision_length_p95", _fmt_count(await decision_length_p95(engine, session.id))),
        ("retraction_rate", _fmt_pct(retraction_rate(cycles))),
        ("avg_reasoning_tokens", _fmt_count(await avg_reasoning_tokens(engine, session.id))),
        ("avg_thinking_chars", _fmt_count(await avg_thinking_chars(engine, session.id))),
        # alert_lifecycle_summary expands to 3 sub-rows (matching diff representation;
        # METRIC_GROUPS still has 1 key but renders 3 — same pattern as
        # tokens_per_cycle_percentile (p50+p95) and reasoning_avg_pair).
        ("alert_triggered_rate", _fmt_pct(summary["triggered_rate"])),
        ("alert_cancelled_rate", _fmt_pct(summary["cancelled_rate"])),
        ("alert_avg_cancel_attempt_count", _fmt_dur(summary["avg_cancel_attempt_count"])),
    ]
    return _two_col("Behavior", rows)


async def _render_caveats(engine, session, rts, caveats) -> str:
    """Single-sim caveats — render_caveats_per_side with empty prefix."""
    counts = await ok_vs_forensic_count(engine, session.id)
    null_summary = await _detect_null_pollution(engine, session.id)
    body = render_caveats_per_side(
        rts, caveats, prefix="",
        ok_cycle_count=counts["ok"],
        forensic_count=counts["forensic"],
        null_field_summary=null_summary,
    )
    if not body.strip():
        body = "- (no caveats)"
    return f"## Caveats\n\n{body}"


_NULL_CHECK_FIELDS = ("decision", "reasoning", "state_snapshot")  # whitelist; loop only


async def _detect_null_pollution(engine, session_id: str) -> list[tuple[str, int]]:
    """Spec §6.3 last row: rows with NULL <field> >5% of agent_cycles.

    `field` is iterated from the hardcoded `_NULL_CHECK_FIELDS` tuple — never
    user input. SQL identifier interpolation is safe here.
    """
    async with engine.connect() as conn:
        total = (await conn.execute(text(
            "SELECT COUNT(*) AS n FROM agent_cycles WHERE session_id = :sid"
        ), {"sid": session_id})).first().n
        if not total:
            return []
        out: list[tuple[str, int]] = []
        for field in _NULL_CHECK_FIELDS:
            n = (await conn.execute(text(
                f"SELECT COUNT(*) AS n FROM agent_cycles "
                f"WHERE session_id = :sid AND {field} IS NULL"
            ), {"sid": session_id})).first().n
            if n / total > 0.05:
                out.append((field, n))
    return out


def _two_col(title: str, rows: list[tuple[str, str]]) -> str:
    lines = [f"## {title}", "", "| Metric | Value |", "|---|---|"]
    for name, value in rows:
        lines.append(f"| {name} | {value} |")
    return "\n".join(lines)
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_analyze_sim.py -v
```

- [ ] **Step 5: Commit**

```bash
git add scripts/analyze_sim.py tests/test_analyze_sim.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T11 analyze_sim.py 3-section markdown + caveats

PnL → Behavior → Cost → Caveats fixed order. Reuses render_caveats_per_side
helper (T9) — emits 8 of 10 spec §6.3 templates (per-session set, incl.
forensic + NULL pollution). ~5 e2e tests including 28-group inventory check.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: `analyze_sim.py` legacy session integration

**Spec:** §6.2 / §6.4.

**Files:**
- Modify: `tests/test_analyze_sim.py`

(Implementation already done in T9 + T10.)

- [ ] **Step 1: Tests**

```python
async def test_analyze_legacy_session_rejected(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="legacy_sim",
                       created_at=R2_7_MERGED_AT - timedelta(days=1))
    r = _run_analyze("--session", "legacy_sim", db_path=db_path)
    assert r.returncode == 1
    assert "legacy sessions" in r.stderr


async def test_analyze_post_cutoff_naive_datetime_works(db_engine):
    """SQLite returns naive datetime; tzinfo normalization in assert_not_legacy."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="post_naive")
    sid = await make_session_id(db_engine, "post_naive")
    await make_cycle(db_engine, sid, "c1")
    r = _run_analyze("--session", "post_naive", db_path=db_path)
    assert r.returncode == 0
```

- [ ] **Step 2: Run, expect pass**

- [ ] **Step 3: Commit**

```bash
git add tests/test_analyze_sim.py
git commit -m "$(cat <<'EOF'
test(obs-phase2): T12 analyze_sim.py legacy session integration

Pre-cutoff session → exit 1 + stderr; post-cutoff naive datetime
normalization. 2 tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: `diff_sim.py` — Δ/Δ%/flag for counts/sums/PnL absolute

**Spec:** §5.1 CLI / §5.3 algorithms / §5.4 thresholds / §5.5 missing values. **Convention:** C-3 sys.path.

**Files:**
- Create: `scripts/diff_sim.py`
- Create: `tests/test_diff_sim.py`

- [ ] **Step 1: Failing tests** (full assertions, no placeholders)

```python
# tests/test_diff_sim.py
"""End-to-end tests for scripts/diff_sim.py via subprocess."""
from datetime import datetime, timedelta, timezone

import pytest
import re
import subprocess
import sys

from tests._sim_fixtures import (
    make_session, make_cycle, make_open_lot, make_close_fill, _resolve_db_path,
)
from scripts._sim_metrics import R2_7_MERGED_AT


def _run_diff(*args, db_path):
    cmd = [sys.executable, "scripts/diff_sim.py", *args, "--db", str(db_path)]
    return subprocess.run(cmd, capture_output=True, text=True)


def _row_for(out: str, metric: str) -> str | None:
    """Find the row containing | <metric> |. Returns the line or None."""
    for line in out.splitlines():
        if line.startswith("|") and f"| {metric} " in line:
            return line
    return None


async def _seed_pnl_session(engine, name, total_pnl):
    """Single roundtrip session with controlled total_pnl_net.

    Auto-fee per C-2: open_fee = 0.1*80000*0.0005 = 4.0; close_fee = 0.1*82000*0.0005 = 4.1;
    rt.fee_total = 8.1. P2 = sim_realized_gross - rt.fee_total.
    Set pnl_gross = total_pnl + 8.1 → P2 == total_pnl exactly.

    Full close (lot 0.1 consumed entirely) → 1 rt, no unclosed lot.
    """
    sid = await make_session(engine, name=name)
    await make_cycle(engine, sid, "c1")
    await make_cycle(engine, sid, "c2")
    await make_open_lot(engine, sid, cycle_id="c1")
    await make_close_fill(engine, sid, cycle_id="c2",
                          pnl_gross=total_pnl + 8.1)
    return sid


async def test_diff_basic_two_sessions(db_engine):
    """A 2 cycles, B 3 cycles → total_cycles row Δ=+1, Δ%=+50%, flag=🔴."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="sim_a")
    await make_session(db_engine, name="sim_b")
    sid_a = (await _resolve_id(db_engine, "sim_a"))
    sid_b = (await _resolve_id(db_engine, "sim_b"))
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid_a, c)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid_b, c)
    r = _run_diff("--a", "sim_a", "--b", "sim_b", db_path=db_path)
    assert r.returncode == 0
    assert "| Sim A | Sim B | Δ | Δ% | Flag |" in r.stdout
    row = _row_for(r.stdout, "total_cycles")
    assert row is not None
    assert "+1" in row
    assert "+50.0%" in row
    assert "🔴" in row


async def test_diff_pnl_negative_to_positive_returns_na_pct(db_engine):
    """sim_a PnL≈-81, sim_b PnL≈+120 → Δ%='n/a', |Δ|=201 ≥ 200 → 🔴."""
    db_path = _resolve_db_path(db_engine)
    await _seed_pnl_session(db_engine, "neg", -81.0)
    await _seed_pnl_session(db_engine, "pos", 120.0)
    r = _run_diff("--a", "neg", "--b", "pos", db_path=db_path)
    assert r.returncode == 0
    row = _row_for(r.stdout, "total_pnl_net")
    assert "n/a" in row
    assert "🔴" in row


async def test_diff_zero_divisor_returns_na_pct(db_engine):
    """sim_a roundtrip_count=0, sim_b=2 → Δ=+2 Δ%='n/a'; non-PnL → ⚠️ (|Δ|>0)."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="empty")
    sid_e = await _resolve_id(db_engine, "empty")
    await make_cycle(db_engine, sid_e, "c1")
    await _seed_pnl_session(db_engine, "with_rts", 100.0)
    r = _run_diff("--a", "empty", "--b", "with_rts", db_path=db_path)
    row = _row_for(r.stdout, "roundtrip_count")
    assert "n/a" in row
    assert "⚠️" in row


async def test_diff_threshold_warn_at_10pct_inclusive(db_engine):
    """A 10 cycles, B 11 cycles → +10.0% inclusive → ⚠️."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="sim_10")
    await make_session(db_engine, name="sim_11")
    sid_a = await _resolve_id(db_engine, "sim_10")
    sid_b = await _resolve_id(db_engine, "sim_11")
    for i in range(10):
        await make_cycle(db_engine, sid_a, f"c{i}")
    for i in range(11):
        await make_cycle(db_engine, sid_b, f"c{i}")
    r = _run_diff("--a", "sim_10", "--b", "sim_11", db_path=db_path)
    row = _row_for(r.stdout, "total_cycles")
    assert "+10.0%" in row
    assert "⚠️" in row and "🔴" not in row


async def test_diff_threshold_crit_at_30pct_inclusive(db_engine):
    """A 10, B 13 → +30.0% inclusive → 🔴."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="sim_x10")
    await make_session(db_engine, name="sim_x13")
    sid_a = await _resolve_id(db_engine, "sim_x10")
    sid_b = await _resolve_id(db_engine, "sim_x13")
    for i in range(10):
        await make_cycle(db_engine, sid_a, f"c{i}")
    for i in range(13):
        await make_cycle(db_engine, sid_b, f"c{i}")
    r = _run_diff("--a", "sim_x10", "--b", "sim_x13", db_path=db_path)
    row = _row_for(r.stdout, "total_cycles")
    assert "+30.0%" in row
    assert "🔴" in row


async def test_diff_pnl_threshold_50_usdt_inclusive(db_engine):
    """sim_a PnL=0, sim_b PnL=+50 → |Δ|=50 inclusive → ⚠️."""
    db_path = _resolve_db_path(db_engine)
    await _seed_pnl_session(db_engine, "p0", 0.0)
    await _seed_pnl_session(db_engine, "p50", 50.0)
    r = _run_diff("--a", "p0", "--b", "p50", db_path=db_path)
    row = _row_for(r.stdout, "total_pnl_net")
    assert "⚠️" in row and "🔴" not in row


async def test_diff_pnl_threshold_200_usdt_inclusive(db_engine):
    """sim_a PnL=0, sim_b PnL=+200 → |Δ|=200 inclusive → 🔴."""
    db_path = _resolve_db_path(db_engine)
    await _seed_pnl_session(db_engine, "p0_2", 0.0)
    await _seed_pnl_session(db_engine, "p200", 200.0)
    r = _run_diff("--a", "p0_2", "--b", "p200", db_path=db_path)
    row = _row_for(r.stdout, "total_pnl_net")
    assert "🔴" in row


async def _resolve_id(engine, name) -> str:
    from sqlalchemy import text
    async with engine.connect() as conn:
        return (await conn.execute(
            text("SELECT id FROM sessions WHERE name = :n"), {"n": name}
        )).first().id
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Implement `scripts/diff_sim.py`** with C-3 boilerplate

```python
#!/usr/bin/env python3
"""Two-sim diff report (markdown + Δ/Δ%/flag)."""
from __future__ import annotations

import sys
from pathlib import Path

# C-3
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import argparse  # noqa: E402
import asyncio  # noqa: E402

from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from scripts._sim_metrics import (  # noqa: E402
    R2_7_MERGED_AT, METRIC_GROUPS,
    assert_not_legacy, collect_roundtrips,
    render_caveats_per_side, render_caveats_diff_only,
    win_rate, total_pnl_net, roundtrip_count,
    avg_fifo_pnl_per_roundtrip,
    avg_roundtrip_duration_min, median_roundtrip_duration_min,
    max_drawdown_pct, exit_type_distribution, largest_win_loss, profit_factor,
    cost_token_sums, avg_cache_hit_rate, tokens_per_cycle_percentile,
    avg_wall_time_ms, avg_llm_call_ms, avg_tool_total_ms,
    per_tool_call_top10,
    total_cycles, ok_vs_forensic_count, triggered_by_distribution,
    decision_type_distribution, five_field_complete_rate, per_field_hit_rate,
    avg_decision_length_chars, decision_length_p95,
    retraction_rate, avg_reasoning_tokens, avg_thinking_chars,
    alert_lifecycle_summary,
)
from scripts.analyze_sim import _resolve_session, _detect_null_pollution  # noqa: E402


# Threshold constants (spec §5.4) — write once per OOS-5
WARN_PCT = 10.0
CRIT_PCT = 30.0
WARN_PP = 5.0
CRIT_PP = 15.0
WARN_PNL_USDT = 50.0
CRIT_PNL_USDT = 200.0


def _flag_by_pct(pct: float | None) -> str:
    if pct is None:
        return "—"
    a = abs(pct)
    if a >= CRIT_PCT:
        return "🔴"
    if a >= WARN_PCT:
        return "⚠️"
    return "—"


def _flag_by_pnl_abs(delta: float | None) -> str:
    if delta is None:
        return "—"
    a = abs(delta)
    if a >= CRIT_PNL_USDT:
        return "🔴"
    if a >= WARN_PNL_USDT:
        return "⚠️"
    return "—"


def _flag_by_rate(delta_pp: float | None, delta_pct: float | None) -> str:
    """Rate flag: pp and % evaluated independently; OR — take more severe."""
    if delta_pp is None and delta_pct is None:
        return "—"
    pp = abs(delta_pp) if delta_pp is not None else 0.0
    pct = abs(delta_pct) if delta_pct is not None else 0.0
    if pp >= CRIT_PP or pct >= CRIT_PCT:
        return "🔴"
    if pp >= WARN_PP or pct >= WARN_PCT:
        return "⚠️"
    return "—"


def _delta(a, b):
    if a is None or b is None:
        return None
    return b - a


def _delta_pct(a, b):
    if a is None or b is None or a == 0:
        return None
    if (a < 0 < b) or (b < 0 < a):  # cross zero (PnL) — n/a
        return None
    return ((b - a) / a) * 100


def _compute_row_flag(a, b, kind: str) -> str:
    """Single dispatch for spec §5.4 thresholds + §5.5 missing-value rules.

    kind: 'count' | 'sum_token' | 'avg' | 'percentile'  → Δ% threshold
          'sum_pnl' | 'avg_pnl'                          → |Δ| absolute (50/200 USDT)
          'rate'                                          → pp/% OR semantics

    Spec §5.5 missing-value rules apply BEFORE §5.4 thresholds:
      - both None / empty             → "—"
      - one side None (signal lost or new) → "⚠️"
      - non-PnL divisor==0 with |Δ|>0  → "⚠️"  (Δ%='n/a' but value moved)
      - PnL cross-zero (a<0<b等)       → flag by |Δ| absolute (handled in sum_pnl branch)
    """
    if a is None and b is None:
        return "—"
    if a is None or b is None:
        return "⚠️"
    delta = b - a
    if kind in ("sum_pnl", "avg_pnl"):
        if kind == "avg_pnl":
            # Spec §5.3: prefer Δ%; fall back to PnL abs when Δ% n/a
            pct = _delta_pct(a, b)
            if pct is not None:
                return _flag_by_pct(pct)
        return _flag_by_pnl_abs(delta)
    # non-PnL
    if a == 0:
        return "⚠️" if abs(delta) > 0 else "—"
    if kind == "rate":
        return _flag_by_rate(delta, _delta_pct(a, b))
    return _flag_by_pct(_delta_pct(a, b))


async def compute_metrics_for_session(engine, session) -> tuple[dict, list, dict]:
    """Compute all per-row metrics for one sim → dict keyed by render-row label.

    Returns (metrics, rts, caveats):
      metrics: dict[str, value | None] — keys = expanded render-row labels
               (e.g. "exit_type[market]", "tokens_per_cycle_p50",
                "triggered_by[scheduled]", "decision_type[close_position]",
                "has_stance", "alert_triggered_rate", "alert_cancelled_rate",
                "alert_avg_cancel_attempt_count")
      rts: list[Roundtrip] — passed to render_caveats_per_side
      caveats: dict — same shape as collect_roundtrips returns

    render_diff iterates a list of (label, kind) tuples — see ROW_KINDS
    below — and pulls metrics_a[label] / metrics_b[label] for the diff row.
    """
    rts, caveats = await collect_roundtrips(engine, session.id)
    sid = session.id
    out: dict = {}

    # PnL — 10 groups
    out["win_rate"] = win_rate(rts)
    out["total_pnl_net"] = await total_pnl_net(engine, sid, rts)
    out["roundtrip_count"] = roundtrip_count(rts)
    out["avg_fifo_pnl_per_roundtrip"] = avg_fifo_pnl_per_roundtrip(rts)
    out["avg_roundtrip_duration_min"] = avg_roundtrip_duration_min(rts)
    out["median_roundtrip_duration_min"] = median_roundtrip_duration_min(rts)
    out["max_drawdown_pct"] = await max_drawdown_pct(engine, sid)
    win, loss = largest_win_loss(rts)
    out["largest_win"] = win
    out["largest_loss"] = loss
    out["profit_factor"] = profit_factor(rts)
    for k, v in exit_type_distribution(rts).items():
        out[f"exit_type[{k}]"] = v

    # Cost — 8 groups (with sub-rows)
    sums = await cost_token_sums(engine, sid)
    out["total_input_tokens"] = sums["total_input_tokens"]
    out["total_output_tokens"] = sums["total_output_tokens"]
    out["total_cache_read_tokens"] = sums["total_cache_read_tokens"]
    out["avg_cache_hit_rate"] = await avg_cache_hit_rate(engine, sid)
    out["tokens_per_cycle_p50"] = await tokens_per_cycle_percentile(engine, sid, 50)
    out["tokens_per_cycle_p95"] = await tokens_per_cycle_percentile(engine, sid, 95)
    out["avg_wall_time_ms"] = await avg_wall_time_ms(engine, sid)
    out["avg_llm_call_ms"] = await avg_llm_call_ms(engine, sid)
    out["avg_tool_total_ms"] = await avg_tool_total_ms(engine, sid)
    # per_tool_call_top10 is a list — diff-friendly representation:
    # store as dict[tool_name → count] so diff can do key-union
    out["per_tool_call_top10"] = dict(await per_tool_call_top10(engine, sid))

    # Behavior — 10 groups
    out["total_cycles"] = await total_cycles(engine, sid)
    counts = await ok_vs_forensic_count(engine, sid)
    out["ok_count"] = counts["ok"]
    out["forensic_count"] = counts["forensic"]
    for k, v in (await triggered_by_distribution(engine, sid)).items():
        out[f"triggered_by[{k}]"] = v
    for k, v in (await decision_type_distribution(engine, sid)).items():
        out[f"decision_type[{k}]"] = v
    out["five_field_complete_rate"] = await five_field_complete_rate(engine, sid)
    for k, v in (await per_field_hit_rate(engine, sid)).items():
        out[k] = v  # has_stance / has_active_commitments / ...
    out["avg_decision_length_chars"] = await avg_decision_length_chars(engine, sid)
    out["decision_length_p95"] = await decision_length_p95(engine, sid)

    # retraction_rate needs full cycle list with decision text
    from sqlalchemy import select
    from src.storage.models import AgentCycle
    async with engine.connect() as conn:
        rows = (await conn.execute(
            select(AgentCycle).where(AgentCycle.session_id == sid)
                              .order_by(AgentCycle.id)
        )).all()
    out["retraction_rate"] = retraction_rate([r[0] for r in rows])

    out["avg_reasoning_tokens"] = await avg_reasoning_tokens(engine, sid)
    out["avg_thinking_chars"] = await avg_thinking_chars(engine, sid)
    summary = await alert_lifecycle_summary(engine, sid)
    # alert_lifecycle_summary is composite — split into 3 sub-rows for diff
    out["alert_triggered_rate"] = summary["triggered_rate"]
    out["alert_cancelled_rate"] = summary["cancelled_rate"]
    out["alert_avg_cancel_attempt_count"] = summary["avg_cancel_attempt_count"]

    return out, rts, caveats


# ROW_KINDS drives render_diff dispatch (spec §5.3 algorithm-by-type table).
# kind values: 'count' (Δ%) / 'sum_token' (Δ%) / 'sum_pnl' (|Δ| absolute) /
#              'avg' (Δ%) / 'avg_pnl' (Δ%, fall back to |Δ|) /
#              'rate' (pp + % OR) / 'percentile' (Δ%)
ROW_KINDS: dict[str, str] = {
    # PnL
    "win_rate": "rate",
    "total_pnl_net": "sum_pnl",
    "roundtrip_count": "count",
    "avg_fifo_pnl_per_roundtrip": "avg_pnl",
    "avg_roundtrip_duration_min": "avg",
    "median_roundtrip_duration_min": "avg",
    "max_drawdown_pct": "rate",
    "largest_win": "sum_pnl",
    "largest_loss": "sum_pnl",
    "profit_factor": "avg",  # ratio — Δ% threshold; PnL absolute irrelevant
    # exit_type[*] — added dynamically (kind='rate' since values are 0..1 fractions)
    # Cost
    "total_input_tokens": "sum_token",
    "total_output_tokens": "sum_token",
    "total_cache_read_tokens": "sum_token",
    "avg_cache_hit_rate": "rate",
    "tokens_per_cycle_p50": "percentile",
    "tokens_per_cycle_p95": "percentile",
    "avg_wall_time_ms": "avg",
    "avg_llm_call_ms": "avg",
    "avg_tool_total_ms": "avg",
    "per_tool_call_top10": "count",  # dict expansion handled like distributions
    # Behavior
    "total_cycles": "count",
    "ok_count": "count",
    "forensic_count": "count",
    # triggered_by[*] / decision_type[*] — added dynamically (kind='count')
    "five_field_complete_rate": "rate",
    "has_stance": "rate",
    "has_active_commitments": "rate",
    "has_this_cycle_delta": "rate",
    "has_thesis_invalidation": "rate",
    "has_watch_list": "rate",
    "avg_decision_length_chars": "avg",
    "decision_length_p95": "percentile",
    "retraction_rate": "rate",
    "avg_reasoning_tokens": "avg",
    "avg_thinking_chars": "avg",
    "alert_triggered_rate": "rate",
    "alert_cancelled_rate": "rate",
    "alert_avg_cancel_attempt_count": "avg",
}


def _resolve_kind(label: str) -> str:
    """Static label → kind via ROW_KINDS; dynamic distribution labels by prefix."""
    if label in ROW_KINDS:
        return ROW_KINDS[label]
    # Dynamic expansions (key set unioned across A & B):
    if label.startswith("exit_type["):
        return "rate"           # values are 0..1 fractions
    if label.startswith(("triggered_by[", "decision_type[")):
        return "count"
    if label.startswith("per_tool_call_top10["):
        return "count"
    raise ValueError(f"unknown row label: {label!r}; add to ROW_KINDS or _resolve_kind dispatch")


def _render_diff_header(session_a, session_b) -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    last_a = session_a.last_active_at or session_a.updated_at or session_a.created_at
    last_b = session_b.last_active_at or session_b.updated_at or session_b.created_at
    return (
        f"# Sim Diff Report\n\n"
        f"- A: {session_a.name} ({session_a.symbol}, "
        f"{session_a.created_at:%Y-%m-%d} → {last_a:%Y-%m-%d})\n"
        f"- B: {session_b.name} ({session_b.symbol}, "
        f"{session_b.created_at:%Y-%m-%d} → {last_b:%Y-%m-%d})\n"
        f"- Generated: {now}"
    )


def _render_diff_section(title: str, labels: list[str],
                         metrics_a: dict, metrics_b: dict) -> str:
    """Build one diff section (PnL / Behavior / Cost) with fixed column header.

    labels: ordered render-row labels for this section (incl. dynamic
            distribution expansions handled by caller).
    """
    lines = [
        f"## {title}", "",
        "| Metric | Sim A | Sim B | Δ | Δ% | Flag |",
        "|---|---|---|---|---|---|",
    ]
    for label in labels:
        a = metrics_a.get(label)
        b = metrics_b.get(label)
        kind = _resolve_kind(label)
        delta = _delta(a, b)
        pct = _delta_pct(a, b)
        flag = _compute_row_flag(a, b, kind)
        lines.append(
            f"| {label} | {_fmt_value(a, kind)} | {_fmt_value(b, kind)} | "
            f"{_fmt_delta(delta, kind)} | {_fmt_pct_cell(pct)} | {flag} |"
        )
    return "\n".join(lines)


# Per-cell formatters — match analyze precision rules (spec §5.5).
def _fmt_value(v, kind):
    if v is None:
        return "—"
    if kind in ("sum_pnl", "avg_pnl"):
        return f"{v:+.2f} USDT"
    if kind == "rate":
        return f"{v*100:.1f}%"
    if kind == "avg":
        # latency / chars: integer; durations / ratios: 1 decimal
        return f"{v:.1f}" if abs(v) < 100 else f"{int(v):,}"
    return f"{int(v):,}"  # count / sum_token / percentile

def _fmt_delta(delta, kind):
    if delta is None:
        return "—"
    if kind in ("sum_pnl", "avg_pnl"):
        return f"{delta:+.2f} USDT"
    if kind == "rate":
        return f"{delta*100:+.1f}pp"
    return f"{delta:+,.1f}" if isinstance(delta, float) else f"{delta:+,}"

def _fmt_pct_cell(pct):
    if pct is None:
        return "n/a"
    return f"{pct:+.1f}%"


async def amain(args):
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database file not found: {args.db}", file=sys.stderr)
        sys.exit(1)
    if args.out and not Path(args.out).parent.exists():
        print(f"Output dir {Path(args.out).parent} does not exist.", file=sys.stderr)
        sys.exit(1)

    engine = create_async_engine(f"sqlite+aiosqlite:///{args.db}")
    try:
        sa = await _resolve_session(engine, args.a)
        sb = await _resolve_session(engine, args.b)
        if sa is None or sb is None:
            missing = args.a if sa is None else args.b
            print(f"Session '{missing}' not found in {args.db}.", file=sys.stderr)
            sys.exit(1)
        assert_not_legacy(sa); assert_not_legacy(sb)
        markdown = await render_diff(engine, sa, sb)
        if args.out:
            Path(args.out).write_text(markdown)
        else:
            print(markdown)
    finally:
        await engine.dispose()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--db", default="data/tradebot.db")
    p.add_argument("--out", default=None)
    args = p.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
```

`render_diff` body shown in T15 (Step 3) — composes header + 3 sections + caveats using helpers above.

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/diff_sim.py tests/test_diff_sim.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T13 diff_sim.py — Δ/Δ%/flag + missing-value dispatch

Counts/sums/percentiles use Δ% threshold; PnL uses |Δ| absolute (50/200
USDT, Δ% informational only). Cross-zero divisor → 'n/a'. _compute_row_flag
single dispatch implements spec §5.5 missing-value rules (None / signal
lost / zero divisor non-PnL → ⚠️). C-3 sys.path boilerplate. ~7 e2e tests
with concrete assertions on flag strings.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: `diff_sim.py` rate flag + missing-value dispatch

**Spec:** §5.4 Rate flag rule + 实例 / §5.5 missing-value rules.

**Files:**
- Modify: `tests/test_diff_sim.py`

(`_flag_by_rate` / `_flag_by_pct` / `_flag_by_pnl_abs` / `_compute_row_flag` already implemented in T13.)

- [ ] **Step 1: Tests**

```python
def test_flag_rate_91_to_92_no_flag():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(1.0, 1.1) == "—"


def test_flag_rate_91_to_96_warn_via_pp_only():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(5.0, 5.5) == "⚠️"


def test_flag_rate_5_to_10_crit_via_pct_promotes():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(5.0, 100.0) == "🔴"


def test_flag_rate_50_to_35_crit_inclusive():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(-15.0, -30.0) == "🔴"


def test_flag_rate_below_5pp_no_flag():
    from scripts.diff_sim import _flag_by_rate
    assert _flag_by_rate(3.0, 3.2) == "—"


def test_flag_pct_inclusive_at_10():
    from scripts.diff_sim import _flag_by_pct
    assert _flag_by_pct(10.0) == "⚠️"
    assert _flag_by_pct(9.9) == "—"


def test_flag_pct_inclusive_at_30():
    from scripts.diff_sim import _flag_by_pct
    assert _flag_by_pct(30.0) == "🔴"
    assert _flag_by_pct(29.9) == "⚠️"


def test_flag_pnl_abs_inclusive_at_50_200():
    from scripts.diff_sim import _flag_by_pnl_abs
    assert _flag_by_pnl_abs(50.0) == "⚠️"
    assert _flag_by_pnl_abs(49.9) == "—"
    assert _flag_by_pnl_abs(200.0) == "🔴"
    assert _flag_by_pnl_abs(199.9) == "⚠️"


# _compute_row_flag — spec §5.5 missing-value dispatch.

def test_compute_row_flag_both_none():
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(None, None, "count") == "—"


def test_compute_row_flag_a_has_b_none_signal_lost():
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(0.5, None, "rate") == "⚠️"


def test_compute_row_flag_a_none_b_has_signal_new():
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(None, 0.5, "rate") == "⚠️"


def test_compute_row_flag_zero_divisor_non_pnl_warn():
    """Spec §5.5: a=0 (non-PnL), |Δ|>0 → ⚠️ regardless of Δ%='n/a'."""
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(0, 2, "count") == "⚠️"
    assert _compute_row_flag(0, 0, "count") == "—"


def test_compute_row_flag_pnl_cross_zero_uses_abs():
    """Spec §5.4: PnL uses |Δ| absolute even when Δ% n/a (cross-zero)."""
    from scripts.diff_sim import _compute_row_flag
    # a=-81, b=120 → |Δ|=201 ≥ 200 → 🔴
    assert _compute_row_flag(-81.0, 120.0, "sum_pnl") == "🔴"
    # a=0, b=50 → |Δ|=50 inclusive → ⚠️
    assert _compute_row_flag(0.0, 50.0, "sum_pnl") == "⚠️"


def test_compute_row_flag_count_uses_pct():
    """Counts judged by Δ% threshold."""
    from scripts.diff_sim import _compute_row_flag
    assert _compute_row_flag(10, 11, "count") == "⚠️"   # +10%
    assert _compute_row_flag(10, 13, "count") == "🔴"   # +30%


def test_compute_row_flag_avg_pnl_prefers_pct_falls_back_to_abs():
    """Spec §5.3: avg_pnl prefers Δ%; falls back to PnL |Δ| when Δ% n/a."""
    from scripts.diff_sim import _compute_row_flag
    # divergent +30%: Δ% triggers 🔴 ahead of |Δ|
    assert _compute_row_flag(10.0, 13.0, "avg_pnl") == "🔴"
    # cross-zero: Δ% n/a, fall back to PnL abs (|Δ|=10 < 50 → —)
    assert _compute_row_flag(-5.0, 5.0, "avg_pnl") == "—"
    # cross-zero |Δ|=60 ≥ 50 → ⚠️
    assert _compute_row_flag(-30.0, 30.0, "avg_pnl") == "⚠️"
```

- [ ] **Step 2: Run, verify pass**

- [ ] **Step 3: Commit**

```bash
git add tests/test_diff_sim.py
git commit -m "$(cat <<'EOF'
test(obs-phase2): T14 rate flag + _compute_row_flag dispatch

§5.4 Rate flag rule (pp/% OR, boundary inclusive 5pp/15pp/10%/30%/50/200) +
§5.5 missing-value dispatch in _compute_row_flag (both None / signal
lost-or-new / zero divisor non-PnL ⚠️ / PnL cross-zero |Δ| absolute /
avg_pnl prefers Δ% falls back to abs). ~14 unit tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: `diff_sim.py` distributions + missing values + caveats reuse

**Spec:** §5.3 distribution / §5.5 missing value table / §6.3 a==b / cross-symbol.

**Files:**
- Modify: `scripts/diff_sim.py` (add render_diff body + caveat aggregation via shared helper)
- Modify: `tests/test_diff_sim.py`

- [ ] **Step 1: Failing tests** (concrete assertions)

```python
async def test_diff_distribution_expansion(db_engine):
    """exit_type with key only on one side → key union, missing → 0%."""
    db_path = _resolve_db_path(db_engine)
    # A: 1 market roundtrip
    await make_session(db_engine, name="exit_a")
    sid_a = await _resolve_id(db_engine, "exit_a")
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid_a, c)
    await make_open_lot(db_engine, sid_a, cycle_id="c1")
    await make_close_fill(db_engine, sid_a, cycle_id="c2", exit_type="market", pnl_gross=10.0)
    # B: 1 liquidation
    await make_session(db_engine, name="exit_b")
    sid_b = await _resolve_id(db_engine, "exit_b")
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid_b, c)
    await make_open_lot(db_engine, sid_b, cycle_id="c1")
    await make_close_fill(db_engine, sid_b, cycle_id="c2", exit_type="liquidation",
                          pnl_gross=-50.0)
    r = _run_diff("--a", "exit_a", "--b", "exit_b", db_path=db_path)
    assert r.returncode == 0
    assert "exit_type[market]" in r.stdout
    assert "exit_type[liquidation]" in r.stdout


async def test_diff_a_equals_b_warning(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="same")
    sid = await _resolve_id(db_engine, "same")
    await make_cycle(db_engine, sid, "c1")
    r = _run_diff("--a", "same", "--b", "same", db_path=db_path)
    assert "WARNING: A and B refer to same session" in r.stdout


async def test_diff_cross_symbol_warning(db_engine):
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="btc_sim", symbol="BTC/USDT:USDT")
    await make_session(db_engine, name="eth_sim", symbol="ETH/USDT:USDT")
    sid_a = await _resolve_id(db_engine, "btc_sim")
    sid_b = await _resolve_id(db_engine, "eth_sim")
    await make_cycle(db_engine, sid_a, "c1")
    await make_cycle(db_engine, sid_b, "c1")
    r = _run_diff("--a", "btc_sim", "--b", "eth_sim", db_path=db_path)
    assert "A=BTC/USDT:USDT, B=ETH/USDT:USDT" in r.stdout
    assert r.returncode == 0


async def test_diff_caveats_aggregated_per_side(db_engine):
    """A 1 unclosed lot, B 0 → caveats prefixed [A] / [B]."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="A_unclosed")
    sid_a = await _resolve_id(db_engine, "A_unclosed")
    await make_cycle(db_engine, sid_a, "c1")
    await make_open_lot(db_engine, sid_a, cycle_id="c1")  # no close → unclosed
    await make_session(db_engine, name="B_clean")
    sid_b = await _resolve_id(db_engine, "B_clean")
    await make_cycle(db_engine, sid_b, "c1")
    r = _run_diff("--a", "A_unclosed", "--b", "B_clean", db_path=db_path)
    assert "[A] 1 unclosed lot(s)" in r.stdout


async def test_diff_missing_value_a_has_b_none(db_engine):
    """A has retraction_rate (≥1 valid pair), B has 0 cycles → flag=⚠️."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="A_with")
    sid_a = await _resolve_id(db_engine, "A_with")
    await make_cycle(db_engine, sid_a, "c1", decision="(1) Stance: bull")
    await make_cycle(db_engine, sid_a, "c2", decision="(1) Stance: bear")
    await make_session(db_engine, name="B_empty")
    r = _run_diff("--a", "A_with", "--b", "B_empty", db_path=db_path)
    row = _row_for(r.stdout, "retraction_rate")
    assert "⚠️" in row


async def test_diff_missing_value_a_none_b_has(db_engine):
    """Symmetric: A 0 cycles / B has data → flag=⚠️."""
    db_path = _resolve_db_path(db_engine)
    await make_session(db_engine, name="A_empty2")
    await make_session(db_engine, name="B_with2")
    sid_b = await _resolve_id(db_engine, "B_with2")
    await make_cycle(db_engine, sid_b, "c1", decision="(1) Stance: bull")
    await make_cycle(db_engine, sid_b, "c2", decision="(1) Stance: bear")
    r = _run_diff("--a", "A_empty2", "--b", "B_with2", db_path=db_path)
    row = _row_for(r.stdout, "retraction_rate")
    assert "⚠️" in row
```

- [ ] **Step 2: Run, expect fail**

- [ ] **Step 3: Implement `render_diff` with distribution expansion + caveat helper reuse**

```python
# Section static label inventory (excludes dynamic distribution / per_tool keys).
PNL_LABELS = [
    "total_pnl_net", "win_rate", "roundtrip_count", "avg_fifo_pnl_per_roundtrip",
    "avg_roundtrip_duration_min", "median_roundtrip_duration_min", "max_drawdown_pct",
    "largest_win", "largest_loss", "profit_factor",
]
PNL_DIST_PREFIX = "exit_type["

COST_STATIC_LABELS = [
    "total_input_tokens", "total_output_tokens", "total_cache_read_tokens",
    "avg_cache_hit_rate", "tokens_per_cycle_p50", "tokens_per_cycle_p95",
    "avg_wall_time_ms", "avg_llm_call_ms", "avg_tool_total_ms",
]
COST_DIST_PREFIX = "per_tool_call_top10["

BEH_STATIC_LABELS = [
    "total_cycles", "ok_count", "forensic_count",
    # triggered_by[*] / decision_type[*] inserted dynamically before next group
    "five_field_complete_rate",
    "has_stance", "has_active_commitments", "has_this_cycle_delta",
    "has_thesis_invalidation", "has_watch_list",
    "avg_decision_length_chars", "decision_length_p95",
    "retraction_rate", "avg_reasoning_tokens", "avg_thinking_chars",
    "alert_triggered_rate", "alert_cancelled_rate", "alert_avg_cancel_attempt_count",
]


def _expand_dist_labels(metrics_a: dict, metrics_b: dict, prefix: str) -> list[str]:
    """Union of dynamic distribution keys; sorted for stable output."""
    keys = {k for k in metrics_a if k.startswith(prefix)}
    keys |= {k for k in metrics_b if k.startswith(prefix)}
    return sorted(keys)


def _flatten_dist_into_dict(metrics: dict, source_key: str, prefix: str) -> None:
    """metrics[source_key] = {k1: v1, ...} → metrics[f'{prefix}{k1}']=v1, ..."""
    payload = metrics.pop(source_key, None)
    if not payload:
        return
    for k, v in payload.items():
        metrics[f"{prefix}{k}"] = v


def _build_pnl_labels(metrics_a, metrics_b) -> list[str]:
    return PNL_LABELS + _expand_dist_labels(metrics_a, metrics_b, PNL_DIST_PREFIX)


def _build_cost_labels(metrics_a, metrics_b) -> list[str]:
    return COST_STATIC_LABELS + _expand_dist_labels(metrics_a, metrics_b, COST_DIST_PREFIX)


def _build_behavior_labels(metrics_a, metrics_b) -> list[str]:
    # Insert triggered_by[*] + decision_type[*] after forensic_count (index 2)
    trig = _expand_dist_labels(metrics_a, metrics_b, "triggered_by[")
    dt = _expand_dist_labels(metrics_a, metrics_b, "decision_type[")
    return BEH_STATIC_LABELS[:3] + trig + dt + BEH_STATIC_LABELS[3:]


async def render_diff(engine, session_a, session_b) -> str:
    metrics_a, rts_a, cv_a = await compute_metrics_for_session(engine, session_a)
    metrics_b, rts_b, cv_b = await compute_metrics_for_session(engine, session_b)
    # Flatten dict-valued entries (per_tool_call_top10) into label-prefixed keys
    _flatten_dist_into_dict(metrics_a, "per_tool_call_top10", "per_tool_call_top10[")
    _flatten_dist_into_dict(metrics_b, "per_tool_call_top10", "per_tool_call_top10[")

    parts = [_render_diff_header(session_a, session_b)]
    parts.append(_render_diff_section("PnL", _build_pnl_labels(metrics_a, metrics_b),
                                       metrics_a, metrics_b))
    parts.append(_render_diff_section("Behavior", _build_behavior_labels(metrics_a, metrics_b),
                                       metrics_a, metrics_b))
    parts.append(_render_diff_section("Cost", _build_cost_labels(metrics_a, metrics_b),
                                       metrics_a, metrics_b))

    # Caveats: per-side ×2 + diff-only ×1
    counts_a = await ok_vs_forensic_count(engine, session_a.id)
    counts_b = await ok_vs_forensic_count(engine, session_b.id)
    null_a = await _detect_null_pollution(engine, session_a.id)
    null_b = await _detect_null_pollution(engine, session_b.id)
    cav_lines = [
        render_caveats_per_side(
            rts_a, cv_a, prefix="[A] ",
            ok_cycle_count=counts_a["ok"], forensic_count=counts_a["forensic"],
            null_field_summary=null_a,
        ),
        render_caveats_per_side(
            rts_b, cv_b, prefix="[B] ",
            ok_cycle_count=counts_b["ok"], forensic_count=counts_b["forensic"],
            null_field_summary=null_b,
        ),
        render_caveats_diff_only(
            a_eq_b=(session_a.id == session_b.id),
            cross_symbol=(session_a.symbol, session_b.symbol),
        ),
    ]
    body = "\n".join(line for line in cav_lines if line.strip())
    if not body:
        body = "- (no caveats)"
    parts.append(f"## Caveats\n\n{body}")
    return "\n\n".join(parts) + "\n"
```

Sample diff output (header + first PnL row):

```markdown
# Sim Diff Report

- A: sim_7 (BTC/USDT:USDT, 2026-05-04 → 2026-05-05)
- B: sim_8 (BTC/USDT:USDT, 2026-05-06 → 2026-05-07)
- Generated: 2026-05-09 12:34 UTC

## PnL

| Metric | Sim A | Sim B | Δ | Δ% | Flag |
|---|---|---|---|---|---|
| total_pnl_net | -81.10 USDT | +120.50 USDT | +201.60 USDT | n/a | 🔴 |
| win_rate | 35.0% | 50.0% | +15.0pp | +42.9% | 🔴 |
| ...
```

- [ ] **Step 4: Run, verify pass**

- [ ] **Step 5: Commit**

```bash
git add scripts/diff_sim.py tests/test_diff_sim.py
git commit -m "$(cat <<'EOF'
feat(obs-phase2): T15 diff_sim.py distributions + missing values + caveats

Distribution dict expansion (key union, missing → 0). a==b warning;
cross-symbol caveat. render_caveats_per_side reused twice with [A]/[B]
prefix + render_caveats_diff_only once. Missing-value 4 cases per
spec §5.5. ~6 tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: Drift guards (METRIC_GROUPS-anchored, all 10 caveat templates)

**Spec:** §7.4.

**Files:**
- Create: `tests/test_drift_phase2_metrics.py`

- [ ] **Step 1: Drift-guard tests**

```python
"""Drift guards for Phase 2 cross-sim analytics."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import Integer

from scripts._sim_metrics import METRIC_GROUPS, R2_7_MERGED_AT, exit_type_distribution
from src.storage.models import SimOrder


def test_metric_groups_inventory_28():
    """Single source: METRIC_GROUPS list has exactly 28 group keys.

    Future additions intentionally break this test → reviewer must
    update spec §3 + METRIC_GROUPS together.
    """
    assert len(METRIC_GROUPS) == 28
    assert len(set(METRIC_GROUPS)) == 28


def test_metric_groups_split_into_3_dimensions():
    """10 PnL + 8 Cost + 10 Behavior verified by group key partition."""
    pnl_keys = {"win_rate", "total_pnl_net", "roundtrip_count",
                "avg_fifo_pnl_per_roundtrip",
                "avg_roundtrip_duration_min", "median_roundtrip_duration_min",
                "max_drawdown_pct", "exit_type_distribution",
                "largest_win_loss", "profit_factor"}
    cost_keys = {"total_input_tokens", "total_output_tokens",
                 "total_cache_read_tokens", "avg_cache_hit_rate",
                 "tokens_per_cycle_percentile", "avg_wall_time_ms",
                 "llm_tool_avg_pair", "per_tool_call_top10"}
    behavior_keys = {"total_cycles", "ok_vs_forensic_count",
                     "triggered_by_distribution", "decision_type_distribution",
                     "five_field_complete_rate", "per_field_hit_rate",
                     "decision_length_avg_p95", "retraction_rate",
                     "reasoning_avg_pair", "alert_lifecycle_summary"}
    assert len(pnl_keys) == 10
    assert len(cost_keys) == 8
    assert len(behavior_keys) == 10
    assert set(METRIC_GROUPS) == (pnl_keys | cost_keys | behavior_keys)


def test_caveat_templates_match_section_6_3():
    """Spec §6.3 lists 10 caveat templates. Verify all 10 substrings present
    in scripts/_sim_metrics.py source (covering both per-side + diff-only helpers)."""
    src = Path("scripts/_sim_metrics.py").read_text()
    expected_fragments = [
        "Session has 0 ok cycles",
        "0 closed roundtrips",
        "unclosed lot(s) at session end",
        "invariant violation(s)",
        "liquidation event(s)",
        "stale close amount(s)",
        "forensic cycle(s)",
        "rows with NULL",
        "WARNING: A and B refer to same session",
        "WARNING: A=",
    ]
    for frag in expected_fragments:
        assert frag in src, f"caveat template missing in caveat helpers: {frag!r}"
    assert len(expected_fragments) == 10  # spec §6.3 inventory


def test_section_order_pnl_behavior_cost_caveats():
    """Markdown output: ## PnL → ## Behavior → ## Cost → ## Caveats."""
    src = Path("scripts/analyze_sim.py").read_text()
    pnl = src.find("_render_pnl(")
    beh = src.find("_render_behavior(")
    cost = src.find("_render_cost(")
    cav = src.find("_render_caveats(")
    assert 0 < pnl < beh < cost < cav, \
        f"section render call order wrong: pnl={pnl} beh={beh} cost={cost} cav={cav}"


def test_exit_type_5_enum():
    dist = exit_type_distribution([])
    assert set(dist.keys()) == {"market", "stop", "take_profit", "limit", "liquidation"}


def test_r2_7_merged_at_constant_matches_pr35():
    assert R2_7_MERGED_AT == datetime(2026, 5, 2, tzinfo=timezone.utc)


def test_v_order_lifecycle_originated_cycle_id_column_present():
    content = Path("src/storage/views.py").read_text()
    assert "v_order_lifecycle" in content
    assert "originated_cycle_id" in content


def test_v_alert_lifecycle_cancel_attempt_count_column_present():
    """alert_lifecycle_summary reads cancel_attempt_count (not cancel_attempts);
    drift if view renames the column."""
    content = Path("src/storage/views.py").read_text()
    assert "cancel_attempt_count" in content


def test_simorder_id_is_int_pk():
    """§4.2 same-tick tiebreaker `ORDER BY filled_at, id` requires Integer PK."""
    assert isinstance(SimOrder.__table__.c.id.type, Integer)
```

- [ ] **Step 2: Run, expect pass**

```bash
uv run pytest tests/test_drift_phase2_metrics.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_drift_phase2_metrics.py
git commit -m "$(cat <<'EOF'
test(obs-phase2): T16 drift guards (METRIC_GROUPS-anchored)

len(METRIC_GROUPS)==28 + 3-dim partition (10/8/10) + 10 caveat
templates substring present + section render call order +
exit_type 5 enum + R2_7_MERGED_AT + v_order_lifecycle column +
v_alert_lifecycle.cancel_attempt_count + SimOrder.id Integer. 9 tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: Manual smoke (AC-4 / AC-5 / AC-11)

**Spec:** §9 AC-4, AC-5, AC-11. Per `feedback_long_walltime_experiments`, **user runs**.

- [ ] **Step 1: User runs analyze on sim_8**

```bash
uv run python scripts/analyze_sim.py --session sim_8 --db data/tradebot.db
```

Expected: exit 0, `## PnL` / `## Behavior` / `## Cost` / `## Caveats` headers, all METRIC_GROUPS rendered, real sim #8 caveats (unclosed lot / liquidation events).

- [ ] **Step 2: User runs diff sim_7 vs sim_8**

```bash
uv run python scripts/diff_sim.py --a sim_7 --b sim_8 --db data/tradebot.db
```

Expected: exit 0, `| Sim A | Sim B | Δ | Δ% | Flag |` columns, threshold-correct flags, cross-symbol warning if applicable.

- [ ] **Step 3: User reports anomalies if any**

If actual output diverges from expected (crash / missing sections / unexpected `—` rows), report back via plan execution.

- [ ] **Step 4: AC-11 — wait for W3 sim**

After user runs W3 sim, `diff_sim.py --a sim_8 --b sim_9` should produce a standard diff report within 10 minutes. Defer to W3 actual run.

(No commit for this task.)

---

### Task 18: Code review + finishing-a-development-branch

- [ ] **Step 1: Use `superpowers:requesting-code-review` skill**

Per `feedback_no_pr_comment`, do not post results to GitHub PR; report directly in conversation.

- [ ] **Step 2: Address review feedback**

Per `superpowers:receiving-code-review`: technical rigor over performative agreement. Verify each suggestion before applying.

- [ ] **Step 3: Verify all ACs**

```bash
set -o pipefail
uv run pytest -v
```

(Per C-6: do not pipe to `tail` without pipefail; pytest's exit code must surface.)

Expected count: ~1364-1374 passed (1284 prior + ~80-90 new) + 5 skip.

Verify spec §9 ACs:
- AC-1: pytest count
- AC-2: `git diff main -- alembic/ src/storage/` empty
- AC-3: `git diff main -- src/cli/ src/integrations/ src/agent/ main.py` empty
- AC-4 / AC-5 / AC-11: T17 manual smoke
- AC-6: `test_metric_groups_inventory_28`
- AC-7-10, 12-19: pytest hits

- [ ] **Step 4: Use `superpowers:finishing-a-development-branch` skill**

Skill presents merge / PR / cleanup options; user decides.

(No commit for this task — branch state ready for PR.)

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-iter-w2r2-obs-phase2.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch with checkpoints

Which approach?
