"""R2-8b cycle summary injection — L1 (fetch) + L2 (render) unit tests.

Helpers under test live in `src/cli/app.py`:
  - _format_relative_time(now, then) -> "N min ago" etc.
  - _count_words(text) -> int (whitespace-split, wc -w convention; T1)
  - _truncate_decision(text, hard_cap_words, hard_cap_chars) -> str (T2 D1; word-aware + silent secondary char floor)
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


def test_truncate_decision_below_word_cap_returns_unchanged():
    """T2.1 (R2-Next-A): word count ≤ cap (700) returns unchanged, no log."""
    from src.cli.app import _truncate_decision

    text = " ".join(["word"] * 500)  # 500 words, well under 700
    assert _truncate_decision(text) == text


def test_truncate_decision_above_word_cap_truncates_with_marker_and_warning(caplog):
    """T2.2 (R2-Next-A): word count > cap → text cut at word boundary +
    standalone-line marker + WARNING log."""
    from src.cli.app import _truncate_decision

    text = " ".join(["word"] * 800)  # 800 words, over 700 cap
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        result = _truncate_decision(text)
    # Marker on its own line, includes cap value
    assert result.endswith("\n... [truncated by system, cut at 700 words]")
    # Body before marker has exactly 700 words
    body = result.rsplit("\n... [truncated", 1)[0]
    assert len(body.split()) == 700
    # Word-aware boundary preserves token integrity (no mid-word cut)
    assert all(w == "word" for w in body.split())
    # WARNING log mentions word units
    assert any(
        "exceeded hard cap 700 words" in r.message and r.levelno == logging.WARNING
        for r in caplog.records
    )


def test_truncate_decision_does_not_truncate_at_exactly_word_cap():
    """T2.3 (R2-Next-A): boundary — exactly 700 words → no truncation."""
    from src.cli.app import _truncate_decision

    text = " ".join(["word"] * 700)
    result = _truncate_decision(text)
    assert result == text
    assert "[truncated" not in result


def test_truncate_marker_uses_constant_value():
    """T2.4 (R2-Next-A drift guard): marker text contains the literal
    `cut at {N} words` matching CYCLE_DECISION_WORD_CAP. Renaming or
    re-valuing the constant must update the marker — this test catches
    drift."""
    from src.cli.app import _truncate_decision
    from src.agent.persona import CYCLE_DECISION_WORD_CAP

    text = " ".join(["word"] * (CYCLE_DECISION_WORD_CAP + 50))
    result = _truncate_decision(text)
    assert f"cut at {CYCLE_DECISION_WORD_CAP} words" in result


def test_truncate_marker_on_standalone_newline():
    """T2.5 (R2-Next-A): marker is on its own line (preceded by `\\n`),
    not inline with truncated body. Visual standalone makes it obvious
    to the agent that content was cut here."""
    from src.cli.app import _truncate_decision

    text = " ".join(["word"] * 800)
    result = _truncate_decision(text)
    assert "\n... [truncated" in result, \
        "marker must be preceded by newline (standalone line)"


def test_truncate_word_boundary_does_not_split_token():
    """T2.6 (R2-Next-A): word-boundary slice preserves token integrity.
    With 800 long tokens, cap at 700, body must contain exactly 700
    intact tokens — never a partial word."""
    from src.cli.app import _truncate_decision

    long_word = "supercalifragilisticexpialidocious"  # 34 chars
    text = " ".join([long_word] * 800)
    result = _truncate_decision(text)
    body = result.rsplit("\n... [truncated", 1)[0]
    tokens = body.split()
    assert len(tokens) == 700
    assert all(t == long_word for t in tokens), \
        "all tokens must be intact (no mid-word slice)"


def test_truncate_pathological_single_token_falls_back_to_char_floor(caplog):
    """T2.7 (R2-Next-A P1 secondary): when the input is a single
    pathological token (no whitespace) far over the char floor, the
    word-cap path does NOT fire (1 word < 700) and the silent secondary
    char floor activates with legacy `[truncated]` marker.
    Tests the P1 belt-and-suspenders for `\\S+`-bypass cases."""
    from src.cli.app import _truncate_decision
    from src.agent.persona import CYCLE_DECISION_CHAR_HARD_FLOOR

    text = "x" * (CYCLE_DECISION_CHAR_HARD_FLOOR + 500)  # 8500 chars, 1 word
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        result = _truncate_decision(text)
    # Secondary path uses legacy marker (silent — not "truncated by system")
    assert result.endswith(" ... [truncated]")
    assert "by system" not in result, \
        "secondary char floor must NOT use agent-facing word-cap marker"
    # Body sliced at char floor exactly
    body = result[:-len(" ... [truncated]")]
    assert len(body) == CYCLE_DECISION_CHAR_HARD_FLOOR
    # WARNING log mentions char path + words=1 diagnostic
    assert any(
        "exceeded char floor" in r.message and "words=1" in r.message
        for r in caplog.records
    )


def test_truncate_word_path_takes_precedence_over_char_floor():
    """T2.8 (R2-Next-A P1 secondary): when input exceeds BOTH word cap
    AND char floor, word-cap path wins (it's checked first). Marker is
    word-cap form, not legacy form."""
    from src.cli.app import _truncate_decision

    # 800 words, each "word" is 12 chars + 1 space = 800*13 = 10400 chars
    # Both caps exceeded, but word path checked first
    text = " ".join(["wordwordword"] * 800)
    assert len(text) > 8000  # above char floor
    assert len(text.split()) == 800  # above word cap
    result = _truncate_decision(text)
    # Word-cap marker, NOT legacy marker
    assert "cut at 700 words" in result
    assert not result.endswith(" ... [truncated]"), \
        "word-cap path should win — not legacy marker"


# ─── R2-Next-A: _count_words helper (T1) ───

def test_count_words_empty():
    """T1.1 (R2-Next-A): empty string → 0."""
    from src.cli.app import _count_words
    assert _count_words("") == 0


def test_count_words_whitespace_only():
    """T1.2 (R2-Next-A): whitespace-only string → 0 (no \\S+ runs)."""
    from src.cli.app import _count_words
    assert _count_words("   \t\n  ") == 0


def test_count_words_single_token():
    """T1.3 (R2-Next-A): single token → 1, regardless of internal punct."""
    from src.cli.app import _count_words
    assert _count_words("hello") == 1
    assert _count_words("hello-world") == 1  # hyphen NOT split (matches wc -w)
    assert _count_words("81,985.40") == 1    # comma/dot NOT split
    assert _count_words("don't") == 1        # apostrophe NOT split


def test_count_words_mixed_whitespace():
    """T1.4 (R2-Next-A): tabs, newlines, multi-space all delimit tokens."""
    from src.cli.app import _count_words
    assert _count_words("a\tb\nc d") == 4
    assert _count_words("  hello   world  ") == 2


def test_count_words_markdown_delimiters_count_as_words():
    """T1.5 (R2-Next-A): markdown `|`, `---`, `—` count as words.
    Naturally penalizes table-format inflation in agent's word budget
    without forcing a format change. See spec §4.3 + §3 Q1 (38.4% of
    cycles use markdown table delimiters)."""
    from src.cli.app import _count_words
    assert _count_words("| - Position |") == 4    # |, -, Position, |
    assert _count_words("|---|---|") == 1          # one continuous run


def test_count_words_unicode_handling():
    """T1.6 (R2-Next-A spec §9 risk mitigation): Unicode boundaries —
    emoji and CJK. `\\S+` is Unicode-aware in Python re; emoji and
    Chinese chars without whitespace count as one token. Matches helper
    design — Unicode-dense content is penalized the same way markdown
    noise is (no special handling, deterministic by whitespace only)."""
    from src.cli.app import _count_words
    assert _count_words("hello 😀 world") == 3
    assert _count_words("中文") == 1                    # no whitespace = 1 word
    assert _count_words("中文 测试") == 2               # space-delimited = 2 words
    assert _count_words("hello 中文 😀") == 3


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
    """T3.1 (R2-Next-A): single summary → header + one block with
    word count in the per-prior header (5-field format)."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    body = "Stance: Holding long, thesis intact."
    s = _make_summary(
        "a3f2c1d8b", "scheduled", body,
        datetime(2026, 5, 6, 11, 52, 0, tzinfo=timezone.utc),
    )

    out = _render_recent_summaries([s], now)
    assert out.startswith(
        "Your prior cycle summaries (most recent N=3, from this session):"
    )
    # 5-field header: cycle · trigger · UTC (ago) · N words
    assert (
        "[cycle a3f2c1d8 · scheduled · 2026-05-06 11:52 UTC (8 min ago) "
        "· 5 words]" in out
    ), f"5-field header missing in output:\n{out}"
    assert body in out


def test_render_truncates_cycle_id_to_8_chars():
    """T3.2 (R2-Next-A): cycle_id sliced to [:8] in 5-field block header."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary(
        "a3f2c1d8b9c0d1e2", "alert", "body word",
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    out = _render_recent_summaries([s], now)
    assert "[cycle a3f2c1d8 ·" in out
    assert "a3f2c1d8b9" not in out  # only first 8
    # 5-field header still well-formed
    assert "· 2 words]" in out


def test_render_uses_absolute_and_relative_time():
    """T3.3 (R2-Next-A): header format `<UTC> (<ago>) · N words`."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary(
        "abcdef01", "scheduled", "body single",  # 2 words
        datetime(2026, 5, 6, 11, 0, 0, tzinfo=timezone.utc),
    )
    out = _render_recent_summaries([s], now)
    assert "2026-05-06 11:00 UTC (1 hour ago) · 2 words]" in out


def test_render_truncates_decision_above_word_cap_via_truncate_decision(caplog):
    """T2.9 (R2-Next-A): decisions > 700 words are word-truncated in
    the rendered block; marker on standalone line."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    huge = " ".join(["wordy"] * 800)  # 800 words
    s = _make_summary(
        "abcdef01", "scheduled", huge,
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        out = _render_recent_summaries([s], now)
    assert "\n... [truncated by system, cut at 700 words]" in out
    assert any("exceeded hard cap 700 words" in r.message for r in caplog.records)


def test_render_keeps_full_decision_below_cap():
    """T2.10 (R2-Next-A): under both word cap (1 word ≤ 700) and char
    floor (800 chars ≤ 8000), no truncation marker; body preserved."""
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


def test_header_shows_original_word_count_for_truncated_prior(caplog):
    """T3.4 (R2-Next-A D2): when a prior is over-cap, the header word
    count is the ORIGINAL count (pre-truncation), not the truncated
    body count. Agent compares header N vs cap to learn 'I exceeded
    the cap by X words'."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    huge = " ".join(["word"] * 879)  # 879 words, will be cut to 700
    s = _make_summary(
        "abcdef01", "scheduled", huge,
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        out = _render_recent_summaries([s], now)
    # Header shows ORIGINAL 879, not truncated 700
    assert "· 879 words]" in out
    # Body still has the word-cap marker
    assert "\n... [truncated by system, cut at 700 words]" in out


def test_header_word_count_matches_count_words_helper():
    """T3.5 (R2-Next-A D2 drift guard): header word count must equal
    `_count_words(s.decision)` exactly. Defends against future changes
    that compute count via a different convention."""
    from src.cli.app import _render_recent_summaries, _count_words

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    body = "| - Position | Entry: 81,985 | SL: 81,550 |"
    expected_count = _count_words(body)
    s = _make_summary(
        "abcdef01", "scheduled", body,
        datetime(2026, 5, 6, 11, 55, 0, tzinfo=timezone.utc),
    )
    out = _render_recent_summaries([s], now)
    assert f"· {expected_count} words]" in out


def test_header_word_count_present_for_each_of_three_priors():
    """T3.6 (R2-Next-A D2): in N=3 priors, every prior block has a
    word count in its 5-field header."""
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    summaries = [
        _make_summary(
            f"cycle{i:03d}", "scheduled", f"body {i} body",
            datetime(2026, 5, 6, 11, 50 + i, 0, tzinfo=timezone.utc),
            sid=i,
        )
        for i in range(3)
    ]
    out = _render_recent_summaries(summaries, now)
    # Each block has `· 3 words]` (each body has 3 tokens)
    assert out.count("· 3 words]") == 3
