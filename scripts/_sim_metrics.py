"""Phase 2 cross-sim analytics core: FIFO lot pairing + metric functions
+ METRIC_GROUPS inventory + caveats helpers (per-side + diff-only).

Spec: docs/superpowers/specs/2026-05-09-iter-w2r2-obs-phase2-design.md
Caveats §4.4 / SQL §3.5 / R2-7 cutoff §6.4 must be honored.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text


def _parse_dt(value: datetime | str | None) -> datetime | None:
    """Coerce aiosqlite-returned datetime value to datetime.

    aiosqlite returns DATETIME columns from raw text() queries as strings of
    the form 'YYYY-MM-DD HH:MM:SS[.ffffff]' (no TZ suffix). Strings that
    include a TZ offset (e.g. '+00:00') are NOT supported and will raise
    ValueError — current callers never produce such values.

    Returns None if value is None.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    # aiosqlite returns 'YYYY-MM-DD HH:MM:SS.ffffff' (no TZ suffix)
    s = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {value!r}")


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


_FILLS_SQL = text("""
    SELECT so.id, so.order_id, so.side, so.position_side, so.order_type,
           so.amount, so.filled_price, so.fee, so.filled_at, so.leverage,
           vol.originated_cycle_id, ta_filled.pnl AS trade_action_pnl
    FROM sim_orders so
    LEFT JOIN v_order_lifecycle vol ON vol.order_id = so.order_id
    LEFT JOIN trade_actions ta_filled
      ON ta_filled.order_id = so.order_id
     AND ta_filled.session_id = :sid
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
                open_at=_parse_dt(fill.filled_at),
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
        close_at_dt = _parse_dt(fill.filled_at)  # hoisted: parse once per fill

        # Pre-compute liquidation per-unit pnl ONCE per fill — fixes invariant
        # counter overcounting when liquidation spans N lots.
        liq_pnl_per_unit: float | None = None
        if fill.order_type == "liquidation":
            if fill.trade_action_pnl is None:
                caveats["invariant_violations"] += 1
                print(
                    f"liquidation fill {fill.order_id} missing trade_actions.pnl row",
                    file=sys.stderr,
                )
                liq_pnl_per_unit = 0.0
            else:
                liq_pnl_per_unit = fill.trade_action_pnl / actual_amount

        while close_remaining > 1e-9:  # epsilon-tolerant (matches lot-pop tolerance)
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
                pnl_gross = liq_pnl_per_unit * consumed
            else:
                pnl_gross = _compute_pnl(lot.entry_px, fill.filled_price, consumed, lot.side)

            fee_open_share = lot.open_fee * (consumed / lot.original_amount)
            fee_close_share = close_fee_total * (consumed / actual_amount)
            fee_total = fee_open_share + fee_close_share

            roundtrips.append(Roundtrip(
                open_at=lot.open_at, close_at=close_at_dt,
                open_cycle_id=lot.open_cycle_id,
                close_cycle_id=(fill.originated_cycle_id
                                if fill.order_type != "liquidation" else None),
                side=lot.side, entry_px=lot.entry_px, exit_px=fill.filled_price,
                amount=consumed, leverage=lot.leverage,
                pnl_gross=pnl_gross,
                fee_open_share=fee_open_share, fee_close_share=fee_close_share,
                fee_total=fee_total,
                pnl_net=pnl_gross - fee_total,
                duration_seconds=int((close_at_dt - lot.open_at).total_seconds()),
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


# ── PnL metric functions (P1-P10) ─────────────────────────────────────────────


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
