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
