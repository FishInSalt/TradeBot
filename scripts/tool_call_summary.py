#!/usr/bin/env python3
"""Tool-call metrics observation script.

Usage (run from repo root):
  uv run python scripts/tool_call_summary.py [--session NAME] [--since 1d|7d|all] [--tool NAME]

Reads tool_calls table + MetricsService.get_tool_call_summary + zero-call工具 padding.
Mirrors src/cli/app.py:379-386 sqlite relative-path normalization.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure src/ is importable when running as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from src.agent.trader import REGISTERED_TOOL_NAMES  # noqa: E402
from src.config import load_settings  # noqa: E402 (matches src/cli/app.py:24)
from src.services.metrics import MetricsService, ToolCallStats  # noqa: E402
from src.storage.database import init_db, get_session  # noqa: E402
from src.storage.models import Session as SessionModel, ToolCall  # noqa: E402


def parse_since(s: str | None) -> timedelta | None:
    if s is None or s == "all":
        return None
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    raise ValueError(f"Unrecognized --since value: {s!r} (use '1d', '7d', 'all', etc.)")


def resolve_db_url(settings_path: Path) -> str:
    """Mirror src/cli/app.py:379-386 sqlite relative-path → absolute normalization."""
    settings = load_settings(settings_path)
    db_url = settings.database.url
    if db_url.startswith("sqlite") and ":///" in db_url and not db_url.startswith("sqlite+aiosqlite:////"):
        relative_path = db_url.split(":///", 1)[1]
        absolute_path = _REPO_ROOT / relative_path
        db_url = f"sqlite+aiosqlite:///{absolute_path}"
    return db_url


def fmt_ago(dt: datetime) -> str:
    """Human-readable 'X ago' string."""
    now = datetime.now(timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


async def resolve_session_id(engine, name_or_uuid: str) -> str:
    """Look up session_id by friendly name or accept UUID verbatim."""
    async with get_session(engine) as db:
        # Try as name
        result = await db.execute(
            select(SessionModel).where(SessionModel.name == name_or_uuid)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row.id
        # Try as id
        result = await db.execute(
            select(SessionModel).where(SessionModel.id == name_or_uuid)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row.id
    raise SystemExit(f"Session not found: {name_or_uuid}")


async def count_cycles(engine, session_id: str | None, since: timedelta | None) -> int:
    """SELECT COUNT(DISTINCT cycle_id) — spec §4.3 says header 'N cycles' is separate query."""
    from sqlalchemy import func
    stmt = select(func.count(func.distinct(ToolCall.cycle_id)))
    if session_id is not None:
        stmt = stmt.where(ToolCall.session_id == session_id)
    if since is not None:
        cutoff = datetime.now(timezone.utc) - since
        stmt = stmt.where(ToolCall.created_at > cutoff)
    async with get_session(engine) as db:
        return (await db.execute(stmt)).scalar() or 0


def print_table(summary: dict[str, ToolCallStats], header: str) -> None:
    """Pretty-print aligned table; pad zero-call rows from REGISTERED_TOOL_NAMES."""
    print(header)
    print()
    print(f"{'Tool':<30}  {'Calls':>5}  {'Err%':>5}  {'p50':>6}  {'p95':>6}  {'Last called':<15}  Notes")
    print("-" * 30 + "  " + "-" * 5 + "  " + "-" * 5 + "  " + "-" * 6 + "  " + "-" * 6 + "  " + "-" * 15 + "  -----")

    for name in REGISTERED_TOOL_NAMES:
        stats = summary.get(name)
        if stats is None:
            print(f"{name:<30}  {'0':>5}  {'─':>5}  {'─':>6}  {'─':>6}  {'never':<15}")
            continue
        err_pct = f"{stats.error_rate * 100:.1f}%"
        last = fmt_ago(stats.last_called_at)
        notes = ""
        if stats.error_breakdown:
            parts = [f"{k}×{v}" for k, v in stats.error_breakdown.items()]
            notes = "[" + ", ".join(parts) + "]"
        print(
            f"{name:<30}  {stats.count:>5}  {err_pct:>5}  "
            f"{stats.p50_duration_ms:>4}ms  {stats.p95_duration_ms:>4}ms  "
            f"{last:<15}  {notes}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None, help="session name or UUID (omit = all)")
    parser.add_argument("--since", default="1d", help="time window (1d, 7d, all; default 1d)")
    parser.add_argument("--tool", default=None, help="filter to one tool")
    parser.add_argument(
        "--settings",
        type=Path,
        default=_REPO_ROOT / "config" / "settings.yaml",
        help="path to settings.yaml (default: config/settings.yaml in repo root)",
    )
    args = parser.parse_args()

    db_url = resolve_db_url(args.settings)
    engine = await init_db(db_url)

    session_id = None
    if args.session is not None:
        session_id = await resolve_session_id(engine, args.session)

    since = parse_since(args.since)

    ms = MetricsService(engine, session_id=session_id or "")
    summary = await ms.get_tool_call_summary(
        session_id=session_id,
        since=since,
        tool_name=args.tool,
    )

    cycles = await count_cycles(engine, session_id, since)

    session_label = args.session or "(all sessions)"
    since_label = args.since if args.since != "all" else "all history"
    header = f"Session: {session_label}  |  {since_label}  |  {cycles} cycles"
    print_table(summary, header)


if __name__ == "__main__":
    asyncio.run(main())
