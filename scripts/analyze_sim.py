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
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import sessionmaker
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == key))
        obj = result.scalars().first()
        if obj:
            return obj
        result = await db.execute(select(SessionModel).where(SessionModel.name == key))
        return result.scalars().first()


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
