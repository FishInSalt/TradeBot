#!/usr/bin/env python3
"""Two-sim diff report (markdown + Δ/Δ%/flag).

Usage:
    python scripts/diff_sim.py --a <id_or_name> --b <id_or_name> [--db PATH] [--out FILE]

Spec: docs/superpowers/specs/2026-05-09-iter-w2r2-obs-phase2-design.md
"""
from __future__ import annotations

import sys
from pathlib import Path

# C-3
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import argparse  # noqa: E402
import asyncio  # noqa: E402

from sqlalchemy import select  # noqa: E402
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


# Threshold constants (spec §5.4)
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
      - PnL cross-zero (a<0<b 等)       → flag by |Δ| absolute (handled in sum_pnl branch)
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

    render_diff (T15) iterates a list of (label, kind) tuples — see ROW_KINDS
    below — and pulls metrics_a[label] / metrics_b[label] for the diff row.
    """
    from sqlalchemy import text
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
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT cycle_id, decision, execution_status FROM agent_cycles
            WHERE session_id = :sid ORDER BY id
        """), {"sid": sid})).all()
    out["retraction_rate"] = retraction_rate(list(rows))

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


# T13 stub: render_diff body lives in T15 (distributions + caveats).
# T13 ships a minimal diff that emits enough to satisfy the e2e tests in this
# task: header + a single PnL/Behavior section showing total_cycles, total_pnl_net,
# and roundtrip_count. T15 replaces with full section-rendering.
async def render_diff(engine, session_a, session_b) -> str:
    metrics_a, _, _ = await compute_metrics_for_session(engine, session_a)
    metrics_b, _, _ = await compute_metrics_for_session(engine, session_b)
    parts = [_render_diff_header(session_a, session_b)]
    # Minimal section to satisfy T13 e2e tests (full render in T15)
    minimal_labels = [
        "total_cycles",
        "total_pnl_net",
        "roundtrip_count",
    ]
    parts.append(_render_diff_section("PnL", minimal_labels, metrics_a, metrics_b))
    return "\n\n".join(parts) + "\n"


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
        assert_not_legacy(sa)
        assert_not_legacy(sb)
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
