"""Phase 2 cross-sim analytics core: FIFO lot pairing + metric functions
+ METRIC_GROUPS inventory + caveats helpers (per-side + diff-only).

Spec: docs/superpowers/specs/2026-05-09-iter-w2r2-obs-phase2-design.md
Caveats §4.4 / SQL §3.5 / R2-7 cutoff §6.4 must be honored.
"""
from __future__ import annotations

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
