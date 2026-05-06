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
