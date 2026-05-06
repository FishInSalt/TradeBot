# Iter W2 R2-8b — Cycle Summary Injection (N10 MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把每 cycle 的 `agent_cycles.decision` 字段从"自由 closing 文本"重塑为 trader-native 5 字段结构化 cycle closing summary（生产侧），并通过 `cli/app.py` user message prefix 注入 N=3 most recent prior summaries（消费侧），建立 cross-cycle judgment continuity（N10 议题 MVP）。

**Architecture:** 双侧机制 — 生产侧在 `persona.py _build_layer1()` 末段加独立 section `## Cycle Closing Summary`（5 字段引导 + 三层 cap 600/800/1200），引导 agent 在 `result.output` (落 `agent_cycles.decision`) 末段写结构化 summary；消费侧在 `cli/app.py run_agent_cycle()` 入口加 fetch + render + inject 三层 helpers，从同 session 取最近 N=3 个 `execution_status='ok'` cycles 的 `decision`，按 trigger context → recent summaries → memory 顺序拼到 user message。Schema 不动（R2-7 `agent_cycles.decision: Text | None` 已铺好）。任何错误（DB 故障 / render 失败 / format 异常）都不阻塞 cycle — `_build_recent_summaries_block` outer wrap 是 fail-isolated boundary。

**Tech Stack:** Python 3.12 / SQLAlchemy 2.x async / pydantic-ai / pytest-asyncio / pytest caplog / aiosqlite (`:memory:` for tests)

**Spec:** `docs/superpowers/specs/2026-05-06-iter-w2r2-8b-cycle-summary-injection-design.md` (commit `28ade52`, 1043 行)

**Branch:** `feature/iter-w2r2-8b-cycle-summary-injection` (已 checkout, spec 已 commit)

---

## File Structure

| 文件 | 操作 | 责任 |
|---|---|---|
| `src/cli/app.py` | Modify | 注入点：5 helpers + `CycleSummary` dataclass + wiring（在 `run_agent_cycle` 内 trigger-context 之后、memory 之前）|
| `src/agent/persona.py` | Modify | 生产侧：`_build_layer1()` 末段加 `## Cycle Closing Summary` section + RuntimeConfig docstring 命名修正 |
| `tests/test_cycle_summary_injection.py` | Create | L1 + L2 unit tests（fetch query + render block，15 tests） |
| `tests/test_agent_cycle_injection.py` | Create | L4 integration tests（`run_agent_cycle` 端到端注入位置 / 边界 / fail-isolated，5 tests）|
| `tests/test_persona.py` | Modify | L3 drift guards（新增 6 tests 锁 section header / 5 字段 anchor / cap 数字 / critical events / anti-instruction guard / no future-self mention）|

预期净增：~130 行 source + ~390 行 test（净 +520 行）；**35 net new tests**（spec §5.2 enumerated 26 + plan T1 加 9 helper-level 边界测试，决议见 Self-Review §5）；测试 1174 → ~1209 (+35)。

**Helper 概念关系（cli/app.py 内部）：**

```
_build_recent_summaries_block(engine, session_id, n=3)   ← outer wrap (fail-isolated boundary, used by run_agent_cycle)
    │
    ├── _fetch_recent_summaries(engine, session_id, n)   ← DB query (try/except for layered defense)
    │       returns list[CycleSummary]
    │
    └── _render_recent_summaries(summaries, now)         ← format header + N blocks
            │
            ├── _truncate_decision(text, hard_cap, soft_cap)   ← cap with INFO/WARNING drift logs
            │
            └── _format_relative_time(now, then)               ← "8 min ago" + naive-datetime safe
```

T1 自底向上从最叶子的两个独立 helper 开始；T6 是 final smoke + AC verification。

---

## 实施顺序总览

| Task | 主题 | 关键产出 | 估算 |
|---|---|---|---|
| T1 | `_format_relative_time` + `_truncate_decision` 独立 helpers | 2 helper + 7 unit tests (T2.2-T2.5 cap + T2.7 naive datetime) | ~30 src + ~80 test |
| T2 | `CycleSummary` dataclass + `_fetch_recent_summaries` query helper | 1 dataclass + 1 helper + 8 unit tests (T1.1-T1.8) | ~40 src + ~100 test |
| T3 | `_render_recent_summaries` render helper | 1 helper + 4 unit tests (T2.1, T2.6 + 2 helper refactor pieces) | ~20 src + ~80 test |
| T4 | `_build_recent_summaries_block` outer wrap + `cli/app.py` 注入点 wiring + L4 integration | 1 helper + 注入点 patch + 5 integration tests (T4.1-T4.5) | ~10 src + ~120 test |
| T5 | `persona.py` 新 section + RuntimeConfig docstring update + drift guards | 1 section + docstring patch + 6 drift guards (T3.1-T3.6) | ~30 src + ~30 test |
| T6 | Final verification (full suite + manual smoke AC30) | AC self-check + smoke session | manual |

每个 task 独立 commit，subagent-driven mode 每 task 三段（implementer → spec-reviewer → code-reviewer）。

---

## Task 1: `_format_relative_time` + `_truncate_decision` Helpers

**Files:**
- Modify: `src/cli/app.py` (add helpers near top, after `_extract_thinking_text` definition around line 65)
- Create: `tests/test_cycle_summary_injection.py`

**Helper signatures:**
- `_format_relative_time(now: datetime, then: datetime) -> str` — 返回 "N sec ago" / "N min ago" / "N hour(s) ago" / "N day(s) ago"；若 `then.tzinfo is None` 内部 normalize 为 UTC（防 SQLite 行为，详见 spec §4.2.2）
- `_truncate_decision(text: str, hard_cap: int = 1200, soft_cap: int = 800) -> str` — 长度 ≤ soft_cap 直接返回；soft_cap < n ≤ hard_cap 保留全文 + INFO log；n > hard_cap 截断 + ` ... [truncated]` 后缀 + WARNING log

### - [ ] Step 1.1: Write failing tests for `_format_relative_time`

Create `tests/test_cycle_summary_injection.py` with the test scaffold and 4 relative-time tests:

```python
"""R2-8b cycle summary injection — L1 (fetch) + L2 (render) unit tests.

Helpers under test live in `src/cli/app.py`:
  - _format_relative_time(now, then) -> "N min ago" etc.
  - _truncate_decision(text, hard_cap, soft_cap) -> str (with drift logs)
  - _fetch_recent_summaries(engine, session_id, n) -> list[CycleSummary]
  - _render_recent_summaries(summaries, now) -> str
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest


# ─────────────────────────── L2 helpers ───────────────────────────

def test_format_relative_time_seconds():
    """T2.7-pre: < 60 sec returns 'N sec ago'."""
    from src.cli.app import _format_relative_time

    now = datetime(2026, 5, 6, 12, 0, 30, tzinfo=timezone.utc)
    then = datetime(2026, 5, 6, 12, 0, 5, tzinfo=timezone.utc)
    assert _format_relative_time(now, then) == "25 sec ago"


def test_format_relative_time_minutes():
    """< 60 min returns 'N min ago'."""
    from src.cli.app import _format_relative_time

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    then = now - timedelta(minutes=8)
    assert _format_relative_time(now, then) == "8 min ago"


def test_format_relative_time_hours_singular_and_plural():
    """1 hour / 2+ hours pluralization."""
    from src.cli.app import _format_relative_time

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    assert _format_relative_time(now, now - timedelta(hours=1, minutes=30)) == "1 hour ago"
    assert _format_relative_time(now, now - timedelta(hours=5)) == "5 hours ago"


def test_format_relative_time_days_singular_and_plural():
    """1 day / 2+ days pluralization."""
    from src.cli.app import _format_relative_time

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    assert _format_relative_time(now, now - timedelta(days=1, hours=2)) == "1 day ago"
    assert _format_relative_time(now, now - timedelta(days=4)) == "4 days ago"


def test_format_relative_time_handles_naive_datetime_from_sqlite():
    """T2.7 (review F1): SQLite returns naive datetime even when schema is
    DateTime(timezone=True). _format_relative_time must normalize internally
    to avoid TypeError: can't subtract offset-naive and offset-aware datetimes.
    """
    from src.cli.app import _format_relative_time

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    naive_then = datetime(2026, 5, 6, 11, 52, 0)  # tzinfo=None ← SQLite shape
    # Should not raise; should return "8 min ago"
    assert _format_relative_time(now, naive_then) == "8 min ago"
```

### - [ ] Step 1.2: Run the relative-time tests to verify they fail

Run: `pytest tests/test_cycle_summary_injection.py -v -k "format_relative_time"`
Expected: 5 FAIL with `ImportError: cannot import name '_format_relative_time' from 'src.cli.app'`

### - [ ] Step 1.3: Implement `_format_relative_time`

Add to `src/cli/app.py` (insert after the `_extract_thinking_text` function around line 65):

```python
def _format_relative_time(now: datetime, then: datetime) -> str:
    """Format a delta as '8 min ago' / '2 hours ago' / '1 day ago'.

    SQLite returns naive datetime even when schema is DateTime(timezone=True);
    normalize to UTC-aware before subtraction (same pattern as
    session_manager.py:294-295).
    """
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = now - then
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs} sec ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days > 1 else ''} ago"
```

### - [ ] Step 1.4: Run the relative-time tests to verify they pass

Run: `pytest tests/test_cycle_summary_injection.py -v -k "format_relative_time"`
Expected: 5 PASS

### - [ ] Step 1.5: Write failing tests for `_truncate_decision`

Append to `tests/test_cycle_summary_injection.py`:

```python
def test_truncate_decision_below_soft_cap_returns_unchanged():
    """T2.4-pre: text ≤ soft_cap (800) returns unchanged, no log."""
    from src.cli.app import _truncate_decision

    text = "x" * 500
    assert _truncate_decision(text) == text


def test_truncate_decision_in_soft_to_hard_band_keeps_full_with_info_log(caplog):
    """soft_cap < n ≤ hard_cap: keep full text + INFO drift log."""
    from src.cli.app import _truncate_decision

    text = "x" * 1000  # in (800, 1200] band
    with caplog.at_level(logging.INFO, logger="src.cli.app"):
        result = _truncate_decision(text)
    assert result == text  # full preserved
    assert any(
        "exceeded soft cap 800" in r.message and r.levelno == logging.INFO
        for r in caplog.records
    ), f"Expected INFO drift log; got: {[r.message for r in caplog.records]}"


def test_truncate_decision_above_hard_cap_truncates_with_marker_and_warning(caplog):
    """T2.3 + T2.5: n > hard_cap → text[:1200] + ' ... [truncated]' + WARNING."""
    from src.cli.app import _truncate_decision

    text = "x" * 1500
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        result = _truncate_decision(text)
    assert result.endswith(" ... [truncated]")
    assert result.startswith("x" * 1200)  # exactly hard_cap chars before marker
    assert any(
        "exceeded hard cap 1200" in r.message and r.levelno == logging.WARNING
        for r in caplog.records
    )


def test_truncate_decision_does_not_truncate_at_exactly_hard_cap():
    """Boundary: n == hard_cap → no truncation."""
    from src.cli.app import _truncate_decision

    text = "x" * 1200
    result = _truncate_decision(text)
    assert result == text
    assert not result.endswith("[truncated]")
```

### - [ ] Step 1.6: Run truncate tests to verify they fail

Run: `pytest tests/test_cycle_summary_injection.py -v -k "truncate_decision"`
Expected: 4 FAIL with `ImportError: cannot import name '_truncate_decision'`

### - [ ] Step 1.7: Implement `_truncate_decision`

Add to `src/cli/app.py` (immediately after `_format_relative_time`):

```python
def _truncate_decision(
    text: str, hard_cap: int = 1200, soft_cap: int = 800,
) -> str:
    """Hard-truncate at hard_cap; INFO log at soft_cap; WARNING log at hard_cap.

    Caps exposed to agent via persona.py `## Cycle Closing Summary` section
    (D-Q-A: fact-only philosophy — agent knows the limit and self-controls).
    """
    n = len(text)
    if n > hard_cap:
        logger.warning(
            "Cycle decision exceeded hard cap %d (got %d), truncating",
            hard_cap, n,
        )
        return text[:hard_cap] + " ... [truncated]"
    if n > soft_cap:
        logger.info(
            "Cycle decision exceeded soft cap %d (got %d), keeping full",
            soft_cap, n,
        )
    return text
```

### - [ ] Step 1.8: Run truncate tests to verify they pass

Run: `pytest tests/test_cycle_summary_injection.py -v -k "truncate_decision"`
Expected: 4 PASS

### - [ ] Step 1.9: Run the full new test file to verify all 9 helpers tests pass

Run: `pytest tests/test_cycle_summary_injection.py -v`
Expected: 9 PASS, 0 FAIL

### - [ ] Step 1.10: Run a broader regression check to ensure no leakage

Run: `pytest tests/test_cycle_log.py tests/test_usage_limits.py tests/test_persona.py -v`
Expected: ALL PASS (existing tests unaffected by the two new helpers)

### - [ ] Step 1.11: Commit

```bash
git add src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-8b): T1 _format_relative_time + _truncate_decision helpers

- _format_relative_time(now, then): "N sec/min/hour(s)/day(s) ago" with
  naive-datetime normalization (SQLite returns tzinfo=None even when schema
  is DateTime(timezone=True); same pattern as session_manager.py:294-295).
- _truncate_decision(text, hard_cap=1200, soft_cap=800): hard truncate at
  1200 + " ... [truncated]" marker + WARNING log; INFO drift log for the
  (800, 1200] soft band; no-op below 800. Caps exposed to agent via
  persona.py section in T5 (D-Q-A fact-only).
- 9 unit tests cover sec/min/hour-singular/hour-plural/day-singular/day-plural
  /naive-datetime + below-soft / soft-to-hard-with-info / above-hard / exact-cap.

Spec §4.2.2 / T2.3-T2.5 + T2.7 (review F1).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `CycleSummary` Dataclass + `_fetch_recent_summaries` Query

**Files:**
- Modify: `src/cli/app.py` (add `CycleSummary` dataclass + `_fetch_recent_summaries` after T1 helpers)
- Modify: `tests/test_cycle_summary_injection.py` (append L1 query tests)

**Signatures:**
- `@dataclass(frozen=True) class CycleSummary` with fields `id: int`, `cycle_id: str`, `triggered_by: str`, `decision: str`, `created_at: datetime`
- `async def _fetch_recent_summaries(engine, session_id: str, n: int = 3) -> list[CycleSummary]`

### - [ ] Step 2.1: Write failing tests for `_fetch_recent_summaries`

Append to `tests/test_cycle_summary_injection.py`:

```python
# ─────────────────────────── L1 fetch tests ───────────────────────────

async def _make_engine_with_session(session_id: str = "sess-r2-8b"):
    """Engine + session row (no exchange/market_data — fetch helper does
    not touch them)."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="r2-8b"))
        await db.commit()
    return engine


async def _add_cycle(
    engine, session_id, cycle_id, *,
    decision="x", execution_status="ok",
    triggered_by="scheduled", created_at=None,
):
    """Insert one AgentCycle row; created_at defaults to utcnow()."""
    from datetime import datetime, timezone
    from src.storage.database import get_session
    from src.storage.models import AgentCycle

    async with get_session(engine) as db:
        db.add(AgentCycle(
            session_id=session_id,
            cycle_id=cycle_id,
            triggered_by=triggered_by,
            decision=decision,
            execution_status=execution_status,
            created_at=created_at or datetime.now(timezone.utc),
        ))
        await db.commit()


async def test_fetch_returns_n_most_recent_ok_cycles():
    """T1.1: happy path — N=3 from a session with 4 cycles, returns the
    3 most recent in created_at DESC order."""
    from datetime import datetime, timezone, timedelta
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-1")
    base = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    for i, cid in enumerate(["aa11", "bb22", "cc33", "dd44"]):
        await _add_cycle(
            engine, "sess-t1-1", cid,
            decision=f"summary-{cid}",
            created_at=base + timedelta(minutes=i),
        )

    rows = await _fetch_recent_summaries(engine, "sess-t1-1", n=3)
    assert [r.cycle_id for r in rows] == ["dd44", "cc33", "bb22"]
    assert all(r.decision.startswith("summary-") for r in rows)


async def test_fetch_returns_empty_for_first_cycle_in_session():
    """T1.2: session with 0 prior cycles → empty list."""
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-2")
    assert await _fetch_recent_summaries(engine, "sess-t1-2", n=3) == []


async def test_fetch_returns_partial_when_session_has_fewer_than_n():
    """T1.3: session with 2 cycles, n=3 → list of 2 (not padded)."""
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-3")
    await _add_cycle(engine, "sess-t1-3", "aa11", decision="s1")
    await _add_cycle(engine, "sess-t1-3", "bb22", decision="s2")

    rows = await _fetch_recent_summaries(engine, "sess-t1-3", n=3)
    assert len(rows) == 2


async def test_fetch_excludes_forensic_cycles():
    """T1.4: cycles with execution_status != 'ok' (forensic) are skipped;
    fetch returns the adjacent ok cycles."""
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-4")
    await _add_cycle(engine, "sess-t1-4", "aa11", decision="ok-1", execution_status="ok")
    # decision=None for forensic per cli/app.py:223,266
    await _add_cycle(
        engine, "sess-t1-4", "bb22", decision=None,
        execution_status="usage_limit_exceeded",
    )
    await _add_cycle(engine, "sess-t1-4", "cc33", decision="ok-2", execution_status="ok")
    await _add_cycle(
        engine, "sess-t1-4", "dd44", decision=None,
        execution_status="retry_exhausted",
    )

    rows = await _fetch_recent_summaries(engine, "sess-t1-4", n=3)
    assert {r.cycle_id for r in rows} == {"aa11", "cc33"}


async def test_fetch_respects_session_boundary():
    """T1.5: cycles in other sessions must not leak in (D-U1-a session-bound)."""
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-5a")
    # Add a separate session row for "sess-t1-5b"
    from src.storage.database import get_session
    from src.storage.models import Session as SessionModel

    async with get_session(engine) as db:
        db.add(SessionModel(id="sess-t1-5b", name="other"))
        await db.commit()

    await _add_cycle(engine, "sess-t1-5a", "aa11", decision="mine")
    await _add_cycle(engine, "sess-t1-5b", "bb22", decision="theirs")

    rows = await _fetch_recent_summaries(engine, "sess-t1-5a", n=3)
    assert [r.cycle_id for r in rows] == ["aa11"]


async def test_fetch_orders_descending_by_created_at_then_id():
    """T1.6 (review F4): same created_at tie-broken by id DESC for stability."""
    from datetime import datetime, timezone
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-6")
    same_ts = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    # Insert 3 with identical created_at; sqlite assigns auto-increment id 1..3
    await _add_cycle(engine, "sess-t1-6", "aa11", decision="a", created_at=same_ts)
    await _add_cycle(engine, "sess-t1-6", "bb22", decision="b", created_at=same_ts)
    await _add_cycle(engine, "sess-t1-6", "cc33", decision="c", created_at=same_ts)

    rows = await _fetch_recent_summaries(engine, "sess-t1-6", n=3)
    # id DESC tie-breaker → cc33 (id=3) first, then bb22 (id=2), aa11 (id=1)
    assert [r.cycle_id for r in rows] == ["cc33", "bb22", "aa11"]


async def test_fetch_returns_empty_on_db_error(caplog, monkeypatch):
    """T1.7: any exception in fetch → log WARNING + return [] (D-U4-a)."""
    from src.cli.app import _fetch_recent_summaries
    import src.cli.app as app_mod

    class BoomEngine:
        pass

    # Force an exception via a get_session monkey-patch that raises
    def _boom(*a, **kw):
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(app_mod, "get_session", _boom)

    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        rows = await _fetch_recent_summaries(BoomEngine(), "any-sess", n=3)
    assert rows == []
    assert any(
        "Failed to fetch prior cycle summaries" in r.message
        and r.levelno == logging.WARNING
        for r in caplog.records
    )


async def test_fetch_excludes_cycles_with_null_decision():
    """T1.8 (review F2): a cycle with execution_status='ok' but decision=None
    should be physically filtered by `WHERE decision IS NOT NULL`. This is
    a defensive guard — the ok-path always writes decision=result.output, but
    if a future code path produces an ok cycle with NULL decision, the render
    block must not crash on `decision or ""` truncation downstream.
    """
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-8")
    await _add_cycle(engine, "sess-t1-8", "aa11", decision="real-summary")
    await _add_cycle(engine, "sess-t1-8", "bb22", decision=None)  # defensive case

    rows = await _fetch_recent_summaries(engine, "sess-t1-8", n=3)
    assert [r.cycle_id for r in rows] == ["aa11"]
```

### - [ ] Step 2.2: Run query tests to verify they fail

Run: `pytest tests/test_cycle_summary_injection.py -v -k "fetch_"`
Expected: 8 FAIL with `ImportError: cannot import name '_fetch_recent_summaries' / 'CycleSummary' from 'src.cli.app'`

### - [ ] Step 2.3: Implement `CycleSummary` dataclass + `_fetch_recent_summaries`

Add the imports needed (verify `select` is imported; if not, add to the existing sqlalchemy import line):

In `src/cli/app.py` line 11 — replace
```python
from sqlalchemy import update as sql_update
```
with
```python
from sqlalchemy import select, update as sql_update
```

Then change the dataclass import — line 1 already has `from __future__ import annotations`; ensure the `dataclasses` import is present near the top. Add this if missing (after line 10):

```python
from dataclasses import dataclass
```

Now add the dataclass and helper after `_truncate_decision` (the helper added in T1):

```python
@dataclass(frozen=True)
class CycleSummary:
    """Snapshot of an AgentCycle row used for cross-cycle context injection.

    `id` is included as a tie-breaker for same-timestamp ordering stability
    (review F4): fast in-memory tests / rapid sequential inserts can produce
    multiple rows with identical created_at, and SQLite ORDER BY only on
    created_at would be non-deterministic.
    """
    id: int
    cycle_id: str
    triggered_by: str
    decision: str
    created_at: datetime


async def _fetch_recent_summaries(
    engine, session_id: str, n: int = 3,
) -> list[CycleSummary]:
    """Fetch the N most recent ok cycles (with non-NULL decision) for a session.

    Filters:
      - session_id matches (D-U1-a: session-bound, no cross-session leak)
      - execution_status='ok' (forensic cycles have decision=NULL anyway, but
        explicit filter makes intent clear)
      - decision IS NOT NULL (review F2 defensive: physically eliminate any
        future code path that lands ok+NULL into the injection list)

    Returns [] on:
      - First cycle in session (no prior rows)
      - Forensic-only history (all cycles non-ok)
      - DB error (any exception logged at WARNING + empty list — D-U4-a
        fail-isolated; cycle must continue)

    Ordering: created_at DESC, id DESC (review F4 tie-breaker for stability).
    Caller (`_render_recent_summaries`) re-sorts ASC for chronological reading.
    """
    try:
        async with get_session(engine) as session:
            result = await session.execute(
                select(
                    AgentCycle.id,
                    AgentCycle.cycle_id,
                    AgentCycle.triggered_by,
                    AgentCycle.decision,
                    AgentCycle.created_at,
                )
                .where(
                    AgentCycle.session_id == session_id,
                    AgentCycle.execution_status == "ok",
                    AgentCycle.decision.is_not(None),
                )
                .order_by(
                    AgentCycle.created_at.desc(),
                    AgentCycle.id.desc(),
                )
                .limit(n)
            )
            rows = result.all()
        return [
            CycleSummary(
                id=r.id,
                cycle_id=r.cycle_id,
                triggered_by=r.triggered_by,
                decision=r.decision or "",
                created_at=r.created_at,
            )
            for r in rows
        ]
    except Exception as e:
        logger.warning(
            "Failed to fetch prior cycle summaries for injection: %s", e,
            exc_info=True,
        )
        return []
```

### - [ ] Step 2.4: Run query tests to verify they pass

Run: `pytest tests/test_cycle_summary_injection.py -v -k "fetch_"`
Expected: 8 PASS

### - [ ] Step 2.5: Run the full file + a regression sanity check

Run: `pytest tests/test_cycle_summary_injection.py tests/test_cycle_log.py tests/test_usage_limits.py -v`
Expected: ALL PASS (T1 helper tests + new T2 fetch tests + existing run_agent_cycle tests untouched)

### - [ ] Step 2.6: Commit

```bash
git add src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-8b): T2 CycleSummary dataclass + _fetch_recent_summaries

- @dataclass(frozen=True) CycleSummary { id, cycle_id, triggered_by,
  decision, created_at } — id field is the tie-breaker for same-timestamp
  ordering (review F4).
- _fetch_recent_summaries(engine, session_id, n=3): WHERE session_id =?
  AND execution_status='ok' AND decision IS NOT NULL (review F2 defense);
  ORDER BY created_at DESC, id DESC LIMIT n; try/except returns [] on any
  exception (D-U4-a fail-isolated, layered with T4 outer wrap).
- 8 unit tests: happy path / first-cycle-empty / partial < n /
  forensic-skip / cross-session boundary / id tie-breaker (F4) / DB
  error / NULL decision filter (F2).

Spec §4.2.1 / T1.1-T1.8.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `_render_recent_summaries` Render Helper

**Files:**
- Modify: `src/cli/app.py` (add render helper after `_fetch_recent_summaries`)
- Modify: `tests/test_cycle_summary_injection.py` (append render-block tests)

**Signature:**
- `def _render_recent_summaries(summaries: list[CycleSummary], now: datetime) -> str` — returns `""` for empty input; otherwise sorts ASC by `(created_at, id)` and emits a header + N blocks separated by blank lines.

### - [ ] Step 3.1: Write failing tests for `_render_recent_summaries`

Append to `tests/test_cycle_summary_injection.py`:

```python
# ─────────────────────────── L2 render tests ───────────────────────────

def _make_summary(cycle_id, triggered_by, decision, created_at, sid=1):
    """Test-only CycleSummary builder."""
    from src.cli.app import CycleSummary
    return CycleSummary(
        id=sid, cycle_id=cycle_id, triggered_by=triggered_by,
        decision=decision, created_at=created_at,
    )


def test_render_returns_empty_string_for_empty_list():
    """Empty input → empty string (caller skips header append)."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    assert _render_recent_summaries([], now) == ""


def test_render_includes_header_and_one_block():
    """Single summary → header + one block formatted per spec §3.6."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary(
        "a3f2c1d8b", "scheduled", "Stance: Holding long, thesis intact.",
        datetime(2026, 5, 6, 11, 52, 0, tzinfo=timezone.utc),
    )

    out = _render_recent_summaries([s], now)
    assert out.startswith(
        "Your prior cycle summaries (most recent N=3, from this session):"
    )
    assert "[cycle a3f2c1d8 · scheduled · 2026-05-06 11:52 UTC (8 min ago)]" in out
    assert "Stance: Holding long, thesis intact." in out


def test_render_truncates_cycle_id_to_8_chars():
    """T2.1: cycle_id is sliced to [:8] in the block header (full UUIDs are long)."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary(
        "a3f2c1d8b9c0d1e2", "alert", "body",
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    out = _render_recent_summaries([s], now)
    assert "[cycle a3f2c1d8 ·" in out
    assert "a3f2c1d8b9" not in out  # only first 8


def test_render_uses_absolute_and_relative_time():
    """T2.2: header line is '<UTC> (<ago>)' format."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary(
        "abcdef01", "scheduled", "body",
        datetime(2026, 5, 6, 11, 0, 0, tzinfo=timezone.utc),
    )
    out = _render_recent_summaries([s], now)
    assert "2026-05-06 11:00 UTC (1 hour ago)" in out


def test_render_truncates_decision_above_hard_cap_via_truncate_decision(caplog):
    """T2.3 + T2.5: decisions > 1200 chars are hard-truncated in the block."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    huge = "y" * 1500
    s = _make_summary(
        "abcdef01", "scheduled", huge,
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        out = _render_recent_summaries([s], now)
    assert " ... [truncated]" in out
    # WARNING raised by _truncate_decision should be visible
    assert any("exceeded hard cap" in r.message for r in caplog.records)


def test_render_keeps_full_decision_below_cap():
    """T2.4: ≤ 1200 chars, no truncation marker."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    body = "z" * 800
    s = _make_summary(
        "abcdef01", "scheduled", body,
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    out = _render_recent_summaries([s], now)
    assert "[truncated]" not in out
    assert body in out


def test_render_orders_chronologically_oldest_first():
    """T2.6: input may arrive DESC; render must emit ASC for natural reading.
    Tie-breaker: same created_at → id ASC after the (created_at, id) sort."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s1 = _make_summary(
        "newest11", "alert", "n",
        datetime(2026, 5, 6, 11, 58, 0, tzinfo=timezone.utc),
    )
    s2 = _make_summary(
        "middle22", "scheduled", "m",
        datetime(2026, 5, 6, 11, 50, 0, tzinfo=timezone.utc),
    )
    s3 = _make_summary(
        "oldest33", "conditional", "o",
        datetime(2026, 5, 6, 11, 45, 0, tzinfo=timezone.utc),
    )
    # Pass DESC (as fetch returns) → render should reorder ASC
    out = _render_recent_summaries([s1, s2, s3], now)

    pos_old = out.index("oldest33")
    pos_mid = out.index("middle22")
    pos_new = out.index("newest11")
    assert pos_old < pos_mid < pos_new
```

### - [ ] Step 3.2: Run render tests to verify they fail

Run: `pytest tests/test_cycle_summary_injection.py -v -k "test_render"`
Expected: 7 FAIL with `ImportError: cannot import name '_render_recent_summaries' from 'src.cli.app'`

### - [ ] Step 3.3: Implement `_render_recent_summaries`

Add to `src/cli/app.py` after `_fetch_recent_summaries`:

```python
def _render_recent_summaries(
    summaries: list[CycleSummary], now: datetime,
) -> str:
    """Render summaries as a user-message-ready prefix block.

    Returns "" if list is empty (caller skips header append on first cycle).
    Sorts by (created_at, id) ASC so the reader sees oldest → newest naturally
    (review F4: id tie-breaker keeps same-timestamp ordering stable).
    Each block is `[cycle <8char> · <trigger> · <UTC> (<ago>)]\\n<body>` joined
    by blank lines under one header.
    """
    if not summaries:
        return ""

    blocks = []
    for s in sorted(summaries, key=lambda x: (x.created_at, x.id)):
        cycle_id_short = s.cycle_id[:8]
        utc_str = s.created_at.strftime("%Y-%m-%d %H:%M UTC")
        ago = _format_relative_time(now, s.created_at)
        body = _truncate_decision(s.decision)
        blocks.append(
            f"[cycle {cycle_id_short} · {s.triggered_by} · {utc_str} ({ago})]\n{body}"
        )

    header = "Your prior cycle summaries (most recent N=3, from this session):"
    return f"{header}\n\n" + "\n\n".join(blocks)
```

### - [ ] Step 3.4: Run render tests to verify they pass

Run: `pytest tests/test_cycle_summary_injection.py -v -k "test_render"`
Expected: 7 PASS

### - [ ] Step 3.5: Run the full new file (now 24 tests)

Run: `pytest tests/test_cycle_summary_injection.py -v`
Expected: 24 PASS, 0 FAIL (9 helpers + 8 fetch + 7 render)

### - [ ] Step 3.6: Commit

```bash
git add src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-8b): T3 _render_recent_summaries render helper

- _render_recent_summaries(summaries, now) returns "" on empty (caller
  skips header append → first cycle silent skip per D-U3-a); else emits
  one header line + N blocks `[cycle <8> · <trigger> · <UTC> (<ago>)]\\n
  <body>` joined by blank lines.
- Sort ASC by (created_at, id) so reader sees oldest → newest naturally
  (review F4 tie-breaker keeps same-timestamp ordering stable).
- Body goes through _truncate_decision (T1) so > 1200 chars get the
   ... [truncated] marker + WARNING drift log.
- 7 unit tests cover empty/header+block/cycle_id [:8]/abs+rel time/
  hard-cap truncation/no-truncate-below-cap/chronological order.

Spec §4.2.2 / T2.1-T2.6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `_build_recent_summaries_block` Outer Wrap + Injection Wiring + L4 Integration

**Files:**
- Modify: `src/cli/app.py` (add outer wrap + patch `run_agent_cycle` injection point)
- Create: `tests/test_agent_cycle_injection.py`

**Signature:**
- `async def _build_recent_summaries_block(engine, session_id: str, n: int = 3) -> str` — fail-isolated boundary covering fetch + render + format; returns `""` on empty list OR any exception (logged at WARNING).

**Wiring:** in `run_agent_cycle`, after the `elif trigger_type == "alert"` block (currently ending ~line 191), before `memory_context = await deps.memory.format_for_prompt()` (currently line 193), insert the await + conditional append.

### - [ ] Step 4.1: Write failing integration tests

Create `tests/test_agent_cycle_injection.py`:

```python
"""R2-8b L4 integration tests — run_agent_cycle prompt injection.

End-to-end assertions: capture the prompt passed to agent.run via a
mock_run side-effect, then assert that:
  - First cycle in a session: NO 'Your prior cycle summaries' header.
  - 2+ cycle: header + N=min(3, available) blocks present.
  - Injection appears AFTER trigger context, BEFORE memory_context.
  - DB or render error: cycle still completes; no header in prompt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import models

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, AgentCycle

models.ALLOW_MODEL_REQUESTS = False


async def _make_deps_engine_with_capture_mocks(session_id: str = "sess-r28b"):
    """Same shape as test_usage_limits.py / test_cycle_log.py helper —
    real Balance/Ticker fixtures so _capture_state_snapshot succeeds."""
    from src.agent.trader import TradingDeps
    from src.integrations.exchange.base import Balance, Ticker

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="r2-8b"))
        await db.commit()

    exchange = MagicMock()
    exchange.fetch_positions = AsyncMock(return_value=[])
    exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=10000.0, used_usdt=0.0,
    ))
    exchange.fetch_open_orders = AsyncMock(return_value=[])
    exchange.get_price_level_alerts = MagicMock(return_value=[])

    market_data = MagicMock()
    market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=75000.0, bid=74999.0, ask=75001.0,
        high=75500.0, low=74500.0, base_volume=1000.0, timestamp=1746098096000,
    ))

    deps = TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=market_data,
        exchange=exchange,
        technical=MagicMock(),
        memory=AsyncMock(format_for_prompt=AsyncMock(return_value="No relevant memories.")),
        session_id=session_id,
        db_engine=engine,
    )
    return deps, engine


def _make_capturing_agent():
    """Mock agent.run that captures the prompt argument for assertion."""
    captured = {}

    async def mock_run(prompt, **kwargs):
        captured["prompt"] = prompt
        result = MagicMock()
        result.usage = lambda: MagicMock(total_tokens=100, details=None)
        result.new_messages = lambda: []
        result.output = "auto-generated cycle summary"
        return result

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"
    return agent, captured


async def _seed_prior_cycles(engine, session_id, *, count, base_offset_min=10):
    """Insert `count` prior cycles spaced 1 minute apart, ending base_offset_min
    minutes before now. All execution_status='ok' with non-empty decision."""
    base = datetime.now(timezone.utc) - timedelta(minutes=base_offset_min + count)
    async with get_session(engine) as db:
        for i in range(count):
            db.add(AgentCycle(
                session_id=session_id,
                cycle_id=f"prio{i:04d}",
                triggered_by="scheduled",
                decision=f"Prior summary #{i} body.",
                execution_status="ok",
                created_at=base + timedelta(minutes=i),
            ))
        await db.commit()


async def test_first_cycle_does_not_inject_prior_summaries():
    """T4.1: session with no prior cycles → no header in prompt."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-1")
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    assert "Your prior cycle summaries" not in prompt


async def test_subsequent_cycle_injects_prior_summaries_with_header():
    """T4.2: session with 2 prior ok cycles → header + 2 blocks present."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-2")
    await _seed_prior_cycles(engine, "sess-t4-2", count=2)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    assert "Your prior cycle summaries (most recent N=3, from this session):" in prompt
    assert "Prior summary #0 body." in prompt
    assert "Prior summary #1 body." in prompt


async def test_injection_appears_before_memory_context():
    """T4.3: order in prompt is trigger intro → recent summaries → memory."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-3")
    deps.memory = AsyncMock(
        format_for_prompt=AsyncMock(return_value="lesson-X-marker"),
    )
    await _seed_prior_cycles(engine, "sess-t4-3", count=1)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    pos_recent = prompt.index("Your prior cycle summaries")
    pos_memory = prompt.index("Your memories:")
    pos_intro = prompt.index("Assess the situation")
    assert pos_intro < pos_recent < pos_memory, (
        f"Order broken: intro={pos_intro} recent={pos_recent} memory={pos_memory}\n"
        f"prompt:\n{prompt}"
    )


async def test_injection_caps_at_n_3_after_4_cycles():
    """T4.4: with 4 prior cycles, only the most recent 3 appear."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-4")
    await _seed_prior_cycles(engine, "sess-t4-4", count=4)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    # Newest 3 should appear; oldest (#0) should NOT
    assert "Prior summary #1 body." in prompt
    assert "Prior summary #2 body." in prompt
    assert "Prior summary #3 body." in prompt
    assert "Prior summary #0 body." not in prompt


async def test_any_injection_error_does_not_abort_cycle(caplog, monkeypatch):
    """T4.5 (review F3): exception in fetch OR render OR format must be caught
    by the outer wrap; cycle proceeds; no 'Your prior cycle summaries' header
    in the prompt; WARNING logged.
    """
    from src.cli.app import TokenBudget, run_agent_cycle
    import src.cli.app as app_mod

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-5")
    await _seed_prior_cycles(engine, "sess-t4-5", count=1)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    # Force an exception inside the render path (post-fetch) to verify the
    # outer wrap catches it (the inner fetch try/except already covers DB).
    def _boom(summaries, now):
        raise RuntimeError("simulated render failure")

    monkeypatch.setattr(app_mod, "_render_recent_summaries", _boom)

    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        result = await run_agent_cycle(
            agent=agent, deps=deps, trigger_type="scheduled",
            budget=budget, engine=engine,
        )

    assert result is not None, "cycle must complete despite injection error"
    assert "Your prior cycle summaries" not in captured["prompt"]
    assert any(
        "Failed to build recent summaries block" in r.message
        and r.levelno == logging.WARNING
        for r in caplog.records
    )
```

### - [ ] Step 4.2: Run integration tests to verify the wiring-dependent ones fail

Run: `pytest tests/test_agent_cycle_injection.py -v -k "subsequent_cycle_injects_prior_summaries_with_header or injection_appears_before_memory_context or any_injection_error_does_not_abort_cycle"`

Expected: **3 FAIL** (`test_subsequent_cycle_injects_prior_summaries_with_header`, `test_injection_appears_before_memory_context`, `test_any_injection_error_does_not_abort_cycle`) — these three assert the **presence** of the injected header / its **position** relative to memory_context / the outer-wrap **error logging**, all of which require the T4.4 wiring + T4.3 outer wrap to exist. Failure mode: `AssertionError` on `"Your prior cycle summaries" in prompt` / `prompt.index(...)` raising `ValueError` / no WARNING record matching `"Failed to build recent summaries block"`.

`test_first_cycle_does_not_inject_prior_summaries` and `test_injection_caps_at_n_3_after_4_cycles` are **NOT** part of this red gate — they assert absence (`not in prompt`) and would trivially pass with no wiring, which is a known TDD blind spot for "should-be-empty" assertions. They become meaningful **after** the wiring lands (Step 4.5): the first-cycle test rules out the wiring spuriously injecting on empty fetches, and the cap test rules out the wiring leaking >3 entries. Their value is regression coverage, not red-gate signal.

Do not proceed to Step 4.3 unless the 3 wiring-dependent tests fail with the expected error shape.

### - [ ] Step 4.3: Implement `_build_recent_summaries_block` outer wrap

Add to `src/cli/app.py` after `_render_recent_summaries`:

```python
async def _build_recent_summaries_block(
    engine, session_id: str, n: int = 3,
) -> str:
    """Fetch + render summaries with a fail-isolated boundary.

    Returns "" on:
      - empty fetch (first cycle / forensic-only history / NULL decision filter)
      - any exception during fetch OR render OR format (logged at WARNING)

    Review F3: this outer wrap covers the entire injection pipeline, not just
    the DB query layer. _fetch_recent_summaries keeps its own try/except as
    layered defense. Rationale: a render/format exception would otherwise
    bubble before agent.run() and abort the cycle — violating the R2-8b
    "any error never blocks a cycle" promise.
    """
    try:
        summaries = await _fetch_recent_summaries(engine, session_id, n)
        if not summaries:
            return ""
        return _render_recent_summaries(
            summaries, now=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.warning(
            "Failed to build recent summaries block for injection: %s", e,
            exc_info=True,
        )
        return ""
```

### - [ ] Step 4.4: Patch the injection point in `run_agent_cycle`

In `src/cli/app.py`, the injection point sits between the volatility-alert branch (last line of the `elif trigger_type == "alert"` block) and `memory_context = await deps.memory.format_for_prompt()`. Apply this Edit verbatim — it has a unique anchor (the `f"in {context.window_minutes}min..."` line followed by the closing `)` and the `memory_context` line, none of which appear elsewhere in the file).

Use the `Edit` tool with these exact strings:

**old_string:**
```python
            prompt += (
                f"\n\nPRICE ALERT: {context.symbol} {direction} {abs(context.change_pct):.1f}% "
                f"in {context.window_minutes}min ({context.reference_price:.2f} → {context.current_price:.2f})"
            )

    memory_context = await deps.memory.format_for_prompt()
```

**new_string:**
```python
            prompt += (
                f"\n\nPRICE ALERT: {context.symbol} {direction} {abs(context.change_pct):.1f}% "
                f"in {context.window_minutes}min ({context.reference_price:.2f} → {context.current_price:.2f})"
            )

    # R2-8b: inject most recent N=3 cycle summaries from this session
    # (D-D-E injection position: trigger context → recent → memory).
    # _build_recent_summaries_block is fail-isolated (review F3) — any
    # error returns "" and lets the cycle proceed.
    recent_block = await _build_recent_summaries_block(
        engine, deps.session_id, n=3,
    )
    if recent_block:
        prompt += f"\n\n{recent_block}"

    memory_context = await deps.memory.format_for_prompt()
```

The 4-line `old_string` (PRICE ALERT close + blank + `memory_context` line) is unique in the file as of `git rev-parse HEAD` at plan time; if a future PR rewrites the alert branch wording, the Edit will fail loudly rather than land in the wrong place. **Do not** modify the surrounding `if`/`elif` structure — the alert branch up to (and including) the `prompt += (...)` block stays byte-identical.

### - [ ] Step 4.5: Run integration tests to verify they pass

Run: `pytest tests/test_agent_cycle_injection.py -v`
Expected: 5 PASS

### - [ ] Step 4.6: Run a broader regression check

Run: `pytest tests/test_cycle_summary_injection.py tests/test_agent_cycle_injection.py tests/test_cycle_log.py tests/test_usage_limits.py tests/test_tool_call_instrumentation.py -v`
Expected: ALL PASS (T1-T3 unit + T4 integration + existing run_agent_cycle suites unaffected)

### - [ ] Step 4.7: Commit

```bash
git add src/cli/app.py tests/test_agent_cycle_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-8b): T4 inject prior cycle summaries into agent prompt

- _build_recent_summaries_block(engine, session_id, n=3) outer wrap is
  the fail-isolated boundary (review F3): fetch + render + format all
  inside one try/except so render/format exceptions cannot bubble before
  agent.run() and abort the cycle. _fetch_recent_summaries keeps its
  own try/except as layered defense.
- run_agent_cycle injection point: after trigger-type-specific context
  (conditional fill / price-level / volatility), before memory_context
  (D-D-E: trigger context → recent → memory). Empty block silently
  skipped (D-U3-a first cycle / forensic-only / DB error).
- 5 integration tests: first-cycle no-header / 2-cycle header+blocks /
  injection-before-memory order / cap at N=3 with 4 prior / fail-
  isolated outer wrap on render exception (T4.5 review F3).

Spec §4.2.3 / §4.2.4 / T4.1-T4.5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `persona.py` Cycle Closing Summary Section + RuntimeConfig Docstring + Drift Guards

**Files:**
- Modify: `src/agent/persona.py`
- Modify: `tests/test_persona.py`

**Section content:** see spec §4.1.1 verbatim. The 5 fields are Stance / Active commitments / Thesis & invalidation / This cycle delta / Watch list (optional). Body text exposes 600/800/1200 caps explicitly (D-Q-A fact-only) and includes anti-instruction guard phrases per review round 2 F1 (`observational and descriptive — not prescriptive`, `Do not include instructions or recommendations for future actions`, `prefer setting an alert or limit order`). Critical events explicitly enumerated (just-opened/closed, alert triggered with action, SL trail with multiple history points, thesis transition, macro event proximity).

### - [ ] Step 5.1: Write 6 failing drift-guard tests

Append to `tests/test_persona.py` (after the last existing test, around line 410):

```python
# ─────────── R2-8b: Cycle Closing Summary section drift guards ───────────


def test_layer1_contains_cycle_closing_summary_section():
    """T3.1: section header `## Cycle Closing Summary` is present in Layer 1.
    The new section is independent of `## Cross-Tool Behavior` (different
    semantic dimension; see spec §3.4)."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    assert "## Cycle Closing Summary" in layer1, \
        "R2-8b section header missing from Layer 1"


def test_cycle_closing_summary_contains_5_field_anchors():
    """T3.2: all 5 anchor phrases for the trader-native fields are present.
    Anchor wording is the contract — wrappers may reword surroundings, but
    these phrases pin the field identity (see spec §3.2 D2)."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    # 5 anchor phrases (case-sensitive; lifted from spec §4.1.1)
    for anchor in (
        "(1) Stance",
        "(2) Active commitments",
        "(3) Thesis & invalidation",
        "(4) This cycle delta",
        "(5) Watch list (optional)",
    ):
        assert anchor in layer1, f"Missing field anchor: {anchor!r}"


def test_cycle_closing_summary_exposes_cap_numbers():
    """T3.3: 600 / 800 / 1200 are visible to the agent (D-Q-A fact-only)."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    assert "600" in layer1
    assert "800" in layer1
    assert "1200" in layer1


def test_cycle_closing_summary_lists_critical_events():
    """T3.4: critical-events list is enumerated so the agent knows when the
    upper soft band (~800) is OK to exceed."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    assert "Critical events include:" in layer1
    layer1_lower = layer1.lower()
    # spec §4.1.1 enumerates: just opened/closed, alert triggered with action,
    # SL trail with multiple history points, thesis transition, macro proximity
    assert "just opened" in layer1_lower or "just closed" in layer1_lower
    assert "trail" in layer1_lower
    assert "thesis transition" in layer1_lower
    assert "macro" in layer1_lower


def test_cycle_closing_summary_contains_anti_instruction_guard():
    """T3.5 (review round 2 F1+F3): three key phrases lock the
    observational-not-prescriptive frame in place. Removing any is a drift
    that would re-open the perform-for-audience risk (§3.5)."""
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1 = _build_layer1(RuntimeConfig())
    assert "observational and descriptive — not prescriptive" in layer1
    assert "Do not include instructions or recommendations for future actions" in layer1
    assert "prefer setting an alert or limit order" in layer1


def test_cycle_closing_summary_does_not_mention_future_self_or_past_self():
    """T3.6 (review round 2 F1): the section must NOT reveal the audience.
    Past wording like "your future self will see this" was deliberately
    deleted to defuse perform-for-audience confirmation bias. This drift
    guard locks against a future PR re-introducing audience-revealing
    framing.
    """
    from src.agent.persona import _build_layer1, RuntimeConfig

    layer1_lower = _build_layer1(RuntimeConfig()).lower()
    assert "future self" not in layer1_lower
    assert "past self" not in layer1_lower
```

### - [ ] Step 5.2: Run drift guards to verify they fail

Run: `pytest tests/test_persona.py -v -k "cycle_closing_summary or layer1_contains_cycle_closing_summary"`
Expected: 6 FAIL because the section is not yet in `_build_layer1`.

### - [ ] Step 5.3: Append the `## Cycle Closing Summary` section to `_build_layer1`

The existing `_build_layer1` is a single f-string. Append the new section by **only extending the string tail** (do NOT re-paste the function body — that would risk silently mutating the 6 existing bullets and tripping `test_layer1_cross_tool_bullet_count` / `test_layer1_no_tool_invocation_descriptions`).

Use the `Edit` tool with these exact strings. The anchor is the closing `"""` that terminates the existing f-string, immediately preceded by the unique `regardless of this setting.` clause from the Wake interval bullet.

**old_string:**
```
- **Wake interval control**: `set_next_wake(minutes)` requests the next scheduler wake-up when no external trigger fires. Valid range 1-{runtime.wake_max_minutes} min for this session. Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting."""
```

**new_string:**
```
- **Wake interval control**: `set_next_wake(minutes)` requests the next scheduler wake-up when no external trigger fires. Valid range 1-{runtime.wake_max_minutes} min for this session. Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting.

## Cycle Closing Summary

Your final response must be a concise cycle summary covering five elements (do not produce an analysis followed by a summary — the summary IS the final response):

(1) Stance — current state in one phrase. Examples: "Holding long, thesis intact" / "Watching for breakout" / "Pending limit order" / "Just closed long, cooling off".

(2) Active commitments — current positions, pending orders, and active alerts:
    - If holding position: position details + entry baseline (R:R / risk % / TP target) + current SL and any trail history (critical for trail decisions across cycles)
    - If pending orders: levels + cancellation criteria
    - If active alerts: levels + each one's signal intent
    - If none of the above: "No position. No pending orders. [Vol alert details if relevant]."

(3) Thesis & invalidation — why your current stance, and the specific conditions under which your thesis would become invalid. Include conviction level (low / moderate / high) when it affects risk or sizing decisions.

(4) This cycle delta — what changed this cycle: actions taken AND actions deliberately not taken (with reasons). Be specific about levels and timing.

(5) Watch list (optional) — non-action observations needing attention: pattern formation, divergence, macro events in the queue, regime shifts, lessons from this cycle. Skip if no relevant observations beyond fields 1-4.

Aim for ~600 chars (up to ~800 for critical events; the system hard-truncates beyond ~1200). Critical events include: just opened or closed position, alert triggered with action taken, SL trail with multiple history points, thesis transition (conviction level change), or macro event proximity.

The summary should be observational and descriptive — not prescriptive. Do not include instructions or recommendations for future actions; for price-conditional plans, prefer setting an alert or limit order rather than writing it as text intent. Do not re-paste market data or full thinking — those will be fresh-fetched."""
```

The `old_string` ends with `setting."""` — that exact 11-char tail is unique to the closing of `_build_layer1`'s f-string in the entire file (the other f-strings end with different content). Because we only extend that tail, the 6 existing bullets and their `**` markup stay byte-identical, so `test_layer1_cross_tool_bullet_count == 6` continues to hold.

Sub-bullet sanity (preempts a Step 5.5 worry): the new section's "Active commitments" sub-list uses leading-4-space indentation `    - If holding position:` etc., which does NOT match the regex `\n- **` that `test_layer1_cross_tool_bullet_count` uses (that pattern requires `\n` + `- ` at the line start + `**`). The new sub-bullets have indent and no `**`, so they are invisible to that drift guard.

### - [ ] Step 5.4: Run drift guards to verify they pass

Run: `pytest tests/test_persona.py -v -k "cycle_closing_summary or layer1_contains_cycle_closing_summary"`
Expected: 6 PASS

### - [ ] Step 5.5: Run the full persona test file to verify no regression

Run: `pytest tests/test_persona.py -v`
Expected: ALL PASS. **Note:** `test_layer1_cross_tool_bullet_count` (existing line ~248-261) asserts `bullet_count == 6` based on `'\n- **'` matches. The new section uses different markup (parenthesized field labels `(1) Stance …`, no `- **` bullets), so the existing test should remain green. If it fails, double-check that the new section does not introduce any `\n- **` patterns by accident — only the sub-bullets under "Active commitments" use leading whitespace + dash + space + plain text (`    - If holding position:`), which does NOT match `\n- **`.

If the existing `test_prompt_minimum_length` (`> 500 chars`) needs adjusting — verify it still fits; the new section adds ~1500 chars so length grows, never shrinks. No edit needed.

### - [ ] Step 5.6: Update RuntimeConfig docstring (命名修正)

Spec §4.1.3: rewrite the 3-line docstring fragment in `persona.py:16-18`. Edit the class docstring inside `RuntimeConfig`:

Replace
```python
    Per-cycle dynamic context (e.g., previous-cycle reasoning, current
    position) is NOT here — that channel is reserved for separate
    mechanisms (R2-8 N10 reasoning injection).
```
with
```python
    Per-cycle dynamic context (e.g., prior cycle summaries, current
    position) is NOT here — that channel is reserved for separate
    mechanisms (R2-8b cross-cycle continuity / decision injection).
```

Run a quick sanity check that no test asserts on the old wording:
```bash
grep -rn "R2-8 N10 reasoning injection" tests/ src/
```
Expected: zero matches (the docstring is the sole occurrence).

### - [ ] Step 5.7: Run the full persona test file once more to verify the docstring change is harmless

Run: `pytest tests/test_persona.py -v`
Expected: ALL PASS.

### - [ ] Step 5.8: Commit

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-8b): T5 persona.py Cycle Closing Summary section + drift guards

- _build_layer1: append independent `## Cycle Closing Summary` section after
  `## Cross-Tool Behavior` (D6 — different semantic dimension; cross-tool
  is operational, summary is output-format / cross-cycle contract).
- 5 trader-native fields (Stance / Active commitments / Thesis & invalidation /
  This cycle delta / Watch list optional) — D2 trader-native framing.
- Three caps (600/800/1200) exposed to agent (D-Q-A fact-only); critical-
  events list anchors when soft band can be exceeded.
- Anti-instruction guard (review round 2 F1+F3): "observational and
  descriptive — not prescriptive" / "Do not include instructions or
  recommendations for future actions" / "prefer setting an alert or limit
  order" — pure output-format framing, no audience reveal.
- RuntimeConfig docstring update: "R2-8 N10 reasoning injection" →
  "R2-8b cross-cycle continuity / decision injection" (issue ID + field
  semantic precision).
- 6 drift guards (T3.1-T3.6): section header / 5 anchors / cap numbers /
  critical-events list / anti-instruction phrases / no future-self /
  past-self mention.

Spec §4.1.1 / §4.1.3 / T3.1-T3.6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Final Verification + AC Self-Check + Manual Smoke

**Files:** none modified.

This task is a verification gate, not an implementation step. The goal is to confirm:
1. Full pytest suite is green (~1174 baseline + **35** new = ~1209 tests; or +26 if Self-Review §5 cuts were applied — implementer should follow whichever count was decided at impl time).
2. Each Acceptance Criterion (spec §9.1 / §9.2 / §9.3) maps to a passing test or a manual verification.
3. Smoke session (≥ 4 cycles, real LLM) shows the prompt actually contains the injected block in cycle 4 (AC30, manual — same model as R2-8a).

### - [ ] Step 6.1: Run full pytest suite

Run: `pytest -q`
Expected: ALL PASS, count grows by **+35** vs the pre-T1 baseline (1174 → ~1209), per Self-Review §5 default decision. If reviewer-driven cuts reduced the count to 26 at any earlier task, expected becomes +26 (1174 → ~1200) — implementer carries through whichever number was set in T1-T3 commits.

If new failures appear in unrelated files, investigate (do NOT mark this task complete with red tests).

### - [ ] Step 6.2: AC self-check — map each AC# to verification artifact

For each AC in spec §9.1 / §9.2 / §9.3, write down which test or which manual step proves it. Suggested format (paste into the PR description, not a file):

| AC# | Description | Verification |
|---|---|---|
| AC1 | section header present | `test_layer1_contains_cycle_closing_summary_section` |
| AC2 | 5 field anchors | `test_cycle_closing_summary_contains_5_field_anchors` |
| AC3 | cap numbers + critical events | `test_cycle_closing_summary_exposes_cap_numbers` + `test_cycle_closing_summary_lists_critical_events` |
| AC4 | first cycle no header | `test_first_cycle_does_not_inject_prior_summaries` |
| AC5 | 2+ cycle injects N=min(3, available) | `test_subsequent_cycle_injects_prior_summaries_with_header` + `test_injection_caps_at_n_3_after_4_cycles` |
| AC6 | forensic excluded | `test_fetch_excludes_forensic_cycles` |
| AC7 | cross-session boundary | `test_fetch_respects_session_boundary` |
| AC8 | DB failure does not abort | `test_fetch_returns_empty_on_db_error` + `test_any_injection_error_does_not_abort_cycle` |
| AC9 | order: trigger → recent → memory | `test_injection_appears_before_memory_context` |
| AC10 | hard truncation + WARNING | `test_truncate_decision_above_hard_cap_truncates_with_marker_and_warning` + `test_render_truncates_decision_above_hard_cap_via_truncate_decision` |
| AC11 | soft band INFO log + full text | `test_truncate_decision_in_soft_to_hard_band_keeps_full_with_info_log` |
| AC30 | manual smoke ≥ 4 cycles, cycle 4 prompt contains injected block | manual (Step 6.3) |

Any AC without a mapped verification is a plan failure — add a test before merging.

### - [ ] Step 6.3: Manual smoke (AC30) — user runs

The user runs a sim ≥ 4 cycles (same model and approach as R2-8a smoke; `cli/app.py run` with `--debug` against `BTC sim #N` simulated exchange) and shares two artifacts:

1. The session log fragment for cycle 4 (or the first cycle that should see N=3 priors).
2. A confirmation that the user-facing prompt at that cycle contained:
   - Header line `Your prior cycle summaries (most recent N=3, from this session):`
   - 3 blocks formatted `[cycle <8char> · <trigger> · <UTC> (<ago>)]\n<body>`
   - Block bodies were the actual decisions from cycles 1/2/3 (truncated if > 1200).

> **Why manual:** AC30 verifies that real-LLM output actually fits the prompt template — unit tests cover the Python wiring, not the LLM-produced summary structure. Per project memory `feedback_long_walltime_experiments`, do not run this in `run_in_background`.

If the manual smoke surfaces issues (e.g., LLM not following the 5-field structure, prompt mis-rendering), file follow-up issues but do NOT silently re-write code without a new spec round.

### - [ ] Step 6.4: Verify no AGENTS.md / CLAUDE.md drift

Run: `git diff main...HEAD -- '*.md' | head -200`
Expected: only `docs/superpowers/specs/2026-05-06-iter-w2r2-8b-cycle-summary-injection-design.md` and `docs/superpowers/plans/2026-05-06-iter-w2r2-8b-cycle-summary-injection.md` show; no accidental edits to top-level docs.

### - [ ] Step 6.5: Final summary commit (only if any housekeeping changes)

If Steps 6.1-6.4 surfaced no drift and no test additions, this task ends with **no new commit** — simply mark T6 complete in the plan tracking and proceed to PR.

If a small follow-up edit (e.g., docstring typo discovered in self-check) was applied, commit it as:

```bash
git add <changed files>
git commit -m "$(cat <<'EOF'
chore(iter-w2r2-8b): T6 final verification follow-ups

[describe the small fix(es) found by AC self-check]

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

### 1. Spec coverage check

| Spec section | Implementation locus | Plan task |
|---|---|---|
| §3.1 D1 (path A: prompt prefix) | `cli/app.py` injection point | T4 |
| §3.1 D2 (5 trader-native fields) | `persona.py` section content | T5 |
| §3.1 D3 (decision field, not reasoning) | `_fetch_recent_summaries` SELECT clause | T2 |
| §3.1 D4 (N=3) | `_build_recent_summaries_block(..., n=3)` default | T4 |
| §3.1 D5 (caps 600/800/1200, exposed) | `_truncate_decision` + persona section "Aim for ~600 chars (up to ~800…)" | T1 + T5 |
| §3.1 D6 (independent section) | `_build_layer1` ends with new section | T5 |
| §3.1 D7 (intro → trigger → recent → memory) | injection order | T4 (test T4.3) |
| §4.1.1 (verbatim section text) | `_build_layer1` f-string | T5 |
| §4.1.3 (RuntimeConfig docstring) | Step 5.6 | T5 |
| §4.2.1 (query helper) | `_fetch_recent_summaries` | T2 |
| §4.2.2 (render helper) | `_render_recent_summaries` + `_truncate_decision` + `_format_relative_time` | T1 + T3 |
| §4.2.3 (outer wrap, review F3) | `_build_recent_summaries_block` | T4 |
| §4.2.4 (injection point) | `run_agent_cycle` patch | T4 |
| §4.3 (边界条件矩阵 11 行) | T1.x / T2.x / T4.x tests | T1-T4 |
| §6.1 (错误处理矩阵) | layered defense — fetch try/except + outer wrap try/except + integration test T4.5 | T2 + T4 |
| §7.2-§7.5 (26 tests T1.1-T4.5) | enumerated in this plan | T1-T5 |
| §9.1-§9.3 (AC) | mapped in §6.2 | T6 |

No spec section is left unmapped. The injection-disclaimer (§8.9) is intentionally **not** implemented — explicit OOS.

### 2. Placeholder scan

I searched the plan for: TBD, TODO, "implement later", "fill in", "Add appropriate", "as needed", "and so on", "etc. (in code blocks)", "Similar to Task". No matches in tasks T1-T5 (T6 by design has manual steps that are explicitly described, not placeholders).

Step 4.4 was rewritten to give exact `Edit` `old_string`/`new_string` strings (4-line anchor: PRICE ALERT close + blank + `memory_context` line) — no surrounding-only references that an engineer would have to reconstruct. The alert branch is byte-identical in `old_string` and `new_string`; only the gap between PRICE ALERT and `memory_context` grows.

### 3. Type / signature consistency

- `_format_relative_time(now: datetime, then: datetime) -> str` — used by `_render_recent_summaries` only; signatures match across T1 / T3 / T5 / T6 references.
- `_truncate_decision(text: str, hard_cap: int = 1200, soft_cap: int = 800) -> str` — defaults baked in match `persona.py` exposed numbers ("up to ~800 … beyond ~1200").
- `CycleSummary` — `id: int` field is added in T2; T3 sort uses `(x.created_at, x.id)`; T1.6 tie-breaker test depends on `id` being part of ORDER BY at the SQL level (set in T2). Consistent.
- `_fetch_recent_summaries(engine, session_id: str, n: int = 3) -> list[CycleSummary]` — signature consistent across T2 implementation, T3 render input, T4 outer wrap call, and T4 monkeypatch in T4.5.
- `_render_recent_summaries(summaries: list[CycleSummary], now: datetime) -> str` — same shape used in T3 tests, T4 outer wrap, and T4.5 monkeypatch (`_boom(summaries, now)` matches positional call in `_build_recent_summaries_block`).
- `_build_recent_summaries_block(engine, session_id: str, n: int = 3) -> str` — single caller (`run_agent_cycle`), consistent.

No drift between task signatures.

### 4. Imports check

T2 introduces `select` from sqlalchemy and the `dataclass` decorator. Step 2.3 explicitly handles both:
- `from sqlalchemy import select, update as sql_update` (replaces line 11).
- `from dataclasses import dataclass` (added near top).

`datetime` and `timezone` are already imported in `cli/app.py:8`. `AgentCycle` is already imported in `cli/app.py:36`. `get_session` is already imported in `cli/app.py:35`. `logger` is already defined at `cli/app.py:40`. No additional imports needed.

For tests, `tests/test_cycle_summary_injection.py` is a new file that imports its own `datetime`, `timedelta`, `timezone`, `logging`, `pytest`. `tests/test_agent_cycle_injection.py` mirrors `tests/test_cycle_log.py` import patterns. Both are self-contained.

### 5. Test count check + decision

Spec §5.2 enumerates **26 net new tests** (T1.1-T1.8 + T2.1-T2.7 + T3.1-T3.6 + T4.1-T4.5 = 8+7+6+5).

Counting in this plan:
- T1 helpers: 5 (relative-time: sec / min / hour-sg / hour-pl / day-sg / day-pl folded as 4 — actually 5 distinct cases) + 4 (truncate) = 9
- T2 fetch: 8 (T1.1-T1.8)
- T3 render: 7 (empty + header+block + cycle_id-truncate + abs+rel time + truncate-via-helper + below-cap + chronological)
- T4 integration: 5 (T4.1-T4.5)
- T5 persona drift: 6 (T3.1-T3.6)

**Total: 9 + 8 + 7 + 5 + 6 = 35 tests**, which is **9 more than the spec's enumeration of 26**.

**Decision (default — implementer should follow this unless spec reviewer overrides):** **keep all 35**. Rationale:

1. Spec §5.2 figure of 26 is **enumerated**, not capped. Section §7.1 says `**26 测试** enumerated (T1.1-T1.8 + T2.1-T2.7 + T3.1-T3.6 + T4.1-T4.5 = 8+7+6+5)` — this counts the labeled identifiers, not "the maximum acceptable number".
2. The 9 extras live in **T1 only**, where the spec underspecifies the helper-level coverage (spec lumps relative-time + truncate cap edges into the L2 render group via T2.2 / T2.3 / T2.4 / T2.5 / T2.7 — 5 tests for what's actually 7 distinct cases at the helper layer plus 2 cases at the render layer).
3. Each extra test maps to a **concrete edge case**, listed below, none redundant with another:
   - `test_format_relative_time_seconds` — < 60s branch (spec only mocks "8 min ago")
   - `test_format_relative_time_minutes` — minutes branch (overlaps but documents)
   - `test_format_relative_time_hours_singular_and_plural` — `1 hour` vs `2 hours` plural rule
   - `test_format_relative_time_days_singular_and_plural` — `1 day` vs `2 days` plural rule
   - `test_truncate_decision_below_soft_cap_returns_unchanged` — happy path (otherwise only truncate paths covered)
   - `test_truncate_decision_does_not_truncate_at_exactly_hard_cap` — `n == hard_cap` boundary (off-by-one guard)
   - `test_render_returns_empty_string_for_empty_list` — silent-skip contract (D-U3-a anchor)
   - `test_render_includes_header_and_one_block` — minimal positive shape (separate from truncate / order assertions)
   - `test_render_keeps_full_decision_below_cap` — render path's "no marker" complement to the > hard-cap test

4. Layered defense pattern: `_truncate_decision` is unit-tested in T1 **AND** integration-tested through `_render_recent_summaries` in T3. That double-coverage is intentional — the unit test catches helper bugs, the render test catches wiring drift.

**If the spec reviewer rejects the 35-count and demands ≤ 26**, the prioritized cut list (drop these first; each line keeps a behavior verified by a sibling test):

1. `test_format_relative_time_seconds` (covered by general delta math)
2. `test_format_relative_time_minutes` (overlaps the integration path in T2.2 via render)
3. `test_truncate_decision_below_soft_cap_returns_unchanged` (covered by render's `keeps_full_decision_below_cap`)
4. `test_truncate_decision_does_not_truncate_at_exactly_hard_cap` (boundary value, defensible to drop)
5. `test_render_returns_empty_string_for_empty_list` (effectively re-asserted by T4.1 first-cycle no-header)
6. `test_render_includes_header_and_one_block` (overlaps T2.1 / T2.2 / T4.2)
7. `test_render_keeps_full_decision_below_cap` (overlaps T2.4 from the original spec list)
8. `test_format_relative_time_hours_singular_and_plural` (one of two pluralization cases)
9. `test_format_relative_time_days_singular_and_plural` (one of two pluralization cases)

Cutting all 9 → 26 tests, matches spec §5.2 enumeration. Implementer should NOT make this cut unilaterally — only if a reviewer explicitly demands it.

### 6. Risk re-check

After Fix 2 (exact `Edit` strings) Step 4.4 is no longer a free-form patch: the 4-line `old_string` (PRICE ALERT close paren + blank + `memory_context = await deps.memory.format_for_prompt()`) is unique to this spot in `cli/app.py` (verified at `git rev-parse HEAD` plan-time). If a future merge rewrites the alert wording, the `Edit` will fail loudly with "string not found" rather than land in the wrong place — preferable to a silent mis-paste.

The `for attempt in range(3):` retry loop sits **after** the injection site, so a wrong-location patch would either fail-loud (no anchor match) or insert above the retry loop, where missing `engine` / `deps` references would surface in T4 integration tests immediately. Risk is bounded.

After Fix 3 (Step 5.3 append-only Edit) the persona.py change is similarly anchored on the unique 11-char tail `setting."""` — the 6 existing bullets cannot be silently mutated.

**Query performance trade-off (explicit acceptance)**: `_fetch_recent_summaries` does `WHERE session_id = ? AND execution_status = 'ok' AND decision IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 3`. The `agent_cycles` table has indices `ix_agent_cycles_session_id_cycle_id` (composite) and the auto-index from `session_id: ... index=True`, but **no index matching `(session_id, created_at DESC)`** — so SQLite filters by session_id (index) then does an in-memory B-tree sort over the matching rows. W2 expected volume is ≤ 12 cycles/h × 48h = ~576 rows per session (1000-row upper bound), where the temp sort costs sub-millisecond; cycle wall time is dominated by LLM latency anyway. **No new index added** (YAGNI per observation-period soft-constraint philosophy). If W2 data later shows fetch latency creeping above the noise floor, add `ix_agent_cycles_session_id_created_at_desc` as a follow-up — not in this PR.

### 7. What's NOT covered by this plan (explicit OOS, mirrors spec §8)

- ❌ Cross-session restore-history filter (§8.3 — restore is intentional접 continuation).
- ❌ Confirmation-bias behavioral verification (§8.4 — observation-period work).
- ❌ Token cost A/B (§8.5 — smoke side-product).
- ❌ sim #4 痛점 baseline comparison (§8.6 — observation period).
- ❌ Metric counter for fetch failures (§8.7 — overkill).
- ❌ Thinking field injection (§8.8 — decision-only).
- ❌ Injection-side disclaimer (§8.9 — fact-only philosophy).
- ❌ "Assess the situation" repositioning (§8.10 — baseline issue).
- ❌ Sub-bullet enforcement under each field (§8.11 — agent self-adapts).

These are documented in the spec; this plan does not introduce code for any of them.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-06-iter-w2r2-8b-cycle-summary-injection.md`.**

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task (T1 → T2 → T3 → T4 → T5 → T6), review between tasks, fast iteration; matches the R2-7 / R2-8a / R2-8c flow.
2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

**Which approach?**
