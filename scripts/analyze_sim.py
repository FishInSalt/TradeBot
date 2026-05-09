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
from datetime import datetime, timezone  # noqa: E402

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from scripts._sim_metrics import (  # noqa: E402
    R2_7_MERGED_AT, METRIC_GROUPS,
    assert_not_legacy, assert_schema_migrated,
    collect_roundtrips, render_caveats_per_side,
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
        await assert_schema_migrated(engine)
        session = await _resolve_session(engine, args.session)
        if session is None:
            print(f"Session '{args.session}' not found in {args.db}.", file=sys.stderr)
            sys.exit(1)
        assert_not_legacy(session)

        markdown = await render_analysis(engine, session)
        if args.out:
            Path(args.out).write_text(markdown)
        else:
            print(markdown)
    finally:
        await engine.dispose()


def _fmt_count(v): return "—" if v is None else f"{int(v):,}"
def _fmt_pct(v):   return "—" if v is None else f"{v*100:.1f}%"
def _fmt_pnl(v):   return "—" if v is None else f"{v:+.2f} USDT"
def _fmt_ms(v):    return "—" if v is None else f"{int(v)} ms"
def _fmt_dur(v):   return "—" if v is None else f"{v:.1f}"


def _two_col(title: str, rows: list[tuple[str, str]]) -> str:
    lines = [f"## {title}", "", "| Metric | Value |", "|---|---|"]
    for name, value in rows:
        lines.append(f"| {name} | {value} |")
    return "\n".join(lines)


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
    async with engine.connect() as conn:
        cycles_rows = (await conn.execute(text("""
            SELECT cycle_id, decision, execution_status FROM agent_cycles
            WHERE session_id = :sid ORDER BY id
        """), {"sid": session.id})).all()
    cycles = list(cycles_rows)  # rows have .cycle_id / .decision / .execution_status
    rows = [
        ("total_cycles", _fmt_count(await total_cycles(engine, session.id))),
        ("ok_count", _fmt_count(counts["ok"])),
        ("forensic_count", _fmt_count(counts["forensic"])),
    ]
    for k, v in trig.items():
        rows.append((f"triggered_by[{k}]", _fmt_count(v)))
    for k, v in dt.items():
        rows.append((f"decision_type[{k}]", _fmt_count(v)))
    rows.append(("five_field_complete_rate", _fmt_pct(await five_field_complete_rate(engine, session.id))))
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


async def render_analysis(engine, session) -> str:
    rts, caveats = await collect_roundtrips(engine, session.id)
    parts = [_render_header(session)]
    parts.append(await _render_pnl(engine, session, rts))
    parts.append(await _render_behavior(engine, session))
    parts.append(await _render_cost(engine, session))
    parts.append(await _render_caveats(engine, session, rts, caveats))
    return "\n\n".join(parts) + "\n"


def main():
    p = argparse.ArgumentParser(description="Single-sim full-stack metrics → markdown")
    p.add_argument("--session", required=True, help="Session UUID or sessions.name")
    p.add_argument("--db", default="data/tradebot.db", help="DB path")
    p.add_argument("--out", default=None, help="Output file (default: stdout)")
    args = p.parse_args()
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
