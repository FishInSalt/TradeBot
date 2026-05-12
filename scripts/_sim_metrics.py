"""Phase 2 cross-sim analytics core: FIFO lot pairing + metric functions
+ METRIC_GROUPS inventory + caveats helpers (per-side + diff-only).

Spec: docs/superpowers/specs/2026-05-09-iter-w2r2-obs-phase2-design.md
Caveats §4.4 / SQL §3.5 / R2-7 cutoff §6.4 must be honored.
"""
from __future__ import annotations

import json
import re
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
        # Defensive guard: actual_amount<=0 (degenerate sim_orders.amount)
        # would crash on the division below; route through invariant pathway.
        liq_pnl_per_unit: float | None = None
        if fill.order_type == "liquidation":
            if fill.trade_action_pnl is None or actual_amount <= 0:
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


# ── Cost metric functions (C1-C8) ─────────────────────────────────────────────


async def cost_token_sums(engine, session_id: str) -> dict[str, int]:
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT COALESCE(SUM(input_tokens), 0) AS total_input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
                   COALESCE(SUM(cache_read_tokens), 0) AS total_cache_read_tokens
            FROM v_cycle_metrics WHERE session_id = :sid
        """), {"sid": session_id})).first()
    return {
        "total_input_tokens": row.total_input_tokens,
        "total_output_tokens": row.total_output_tokens,
        "total_cache_read_tokens": row.total_cache_read_tokens,
    }


async def avg_cache_hit_rate(engine, session_id: str) -> float | None:
    # Forensic filter (spec §6.3 forensic caveat): cycle averages exclude
    # is_ok_cycle = 0 to honor the "excluded from cycle averages" contract.
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT SUM(input_tokens) AS total_in, SUM(cache_read_tokens) AS total_cache
            FROM v_cycle_metrics WHERE session_id = :sid AND is_ok_cycle = 1
        """), {"sid": session_id})).first()
    if not row.total_in:
        return None
    return row.total_cache / row.total_in


async def tokens_per_cycle_percentile(engine, session_id: str, p: int) -> float | None:
    # Forensic filter: percentile is "typical cycle" — exclude forensic.
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT tokens_consumed FROM v_cycle_metrics
            WHERE session_id = :sid AND is_ok_cycle = 1
              AND tokens_consumed IS NOT NULL
            ORDER BY tokens_consumed
        """), {"sid": session_id})).all()
    return _percentile([r.tokens_consumed for r in rows], p)


_AVG_COLUMN_ALLOWED = frozenset({
    "wall_time_ms", "llm_call_ms", "tool_total_ms",
    "decision_length", "reasoning_tokens",
})


async def _avg_view_column(engine, session_id: str, col: str) -> float | None:
    """Internal: AVG over a whitelisted v_cycle_metrics column.

    SQL identifier must be interpolated (DB-API can't bind column names);
    the whitelist defends against accidental misuse from future contributors.

    Forensic filter (spec §6.3): all callers compute "cycle averages",
    contractually excluded from forensic per the Caveats section.
    """
    if col not in _AVG_COLUMN_ALLOWED:
        raise ValueError(f"_avg_view_column: column {col!r} not in whitelist")
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            f"SELECT AVG({col}) AS avg_val FROM v_cycle_metrics "
            f"WHERE session_id = :sid AND is_ok_cycle = 1"
        ), {"sid": session_id})).first()
    return row.avg_val


async def avg_wall_time_ms(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "wall_time_ms")


async def avg_llm_call_ms(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "llm_call_ms")


async def avg_tool_total_ms(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "tool_total_ms")


async def per_tool_call_top10(engine, session_id: str) -> list[tuple[str, int]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT tool_name, COUNT(*) AS cnt FROM tool_calls
            WHERE session_id = :sid
            GROUP BY tool_name ORDER BY cnt DESC LIMIT 10
        """), {"sid": session_id})).all()
    return [(r.tool_name, r.cnt) for r in rows]


# ── Behavior metric functions (B1-B10) ────────────────────────────────────────


STANCE_RE = re.compile(
    # Separator class includes ASCII colon, fullwidth colon, em-dash, en-dash —
    # W2 smoke (sim #7/#8) showed actual prompt uses em-dash ("(1) Stance — ...").
    r"(?:^|\n)\s*(?:\*\*)?\(?1\)?\.?\s*(?:\*\*)?\s*[Ss]tance(?:\*\*)?\s*[:：—–]\s*"
    r"(?:\*\*)?(\w+)",
    re.MULTILINE,
)


def extract_stance(decision: str | None) -> str | None:
    if not decision:
        return None
    m = STANCE_RE.search(decision)
    return m.group(1).lower().strip() if m else None


def retraction_rate(cycles) -> float | None:
    """cycle N stance ≠ cycle N-1 stance ratio. None when 0 valid pairs.

    Caveat (spec §3.5 item 3): substring-LIKE not anchored; R2-Next-A
    priors-injection引述 may inflate count. Accepted first-cut precision.
    """
    valid = [(c.cycle_id, extract_stance(c.decision))
             for c in cycles if c.execution_status == "ok"]
    pairs = [(prev, curr) for prev, curr in zip(valid, valid[1:])
             if prev[1] is not None and curr[1] is not None]
    if not pairs:
        return None
    return sum(1 for prev, curr in pairs if prev[1] != curr[1]) / len(pairs)


async def total_cycles(engine, session_id: str) -> int:
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT COUNT(*) AS n FROM v_cycle_metrics WHERE session_id = :sid"
        ), {"sid": session_id})).first()
    return row.n


async def ok_vs_forensic_count(engine, session_id: str) -> dict[str, int]:
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT
              SUM(CASE WHEN is_ok_cycle = 1 THEN 1 ELSE 0 END) AS ok,
              SUM(CASE WHEN is_forensic_cycle = 1 THEN 1 ELSE 0 END) AS forensic
            FROM v_cycle_metrics WHERE session_id = :sid
        """), {"sid": session_id})).first()
    return {"ok": row.ok or 0, "forensic": row.forensic or 0}


async def triggered_by_distribution(engine, session_id: str) -> dict[str, int]:
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT triggered_by, COUNT(*) AS cnt FROM v_cycle_metrics
            WHERE session_id = :sid GROUP BY triggered_by
        """), {"sid": session_id})).all()
    return {r.triggered_by: r.cnt for r in rows}


DECISION_ACTION_PRIORITY: list[str] = [
    "open_position",
    "close_position",
    "place_limit_order",
    "set_stop_loss",
    "set_take_profit",
    "add_price_level_alert",
    "cancel_price_level_alert",
]


async def decision_type_distribution(engine, session_id: str) -> dict[str, int]:
    """§3.5 caveat 1: hold (pure-observation) vs hold (wake-only).

    Determinism (R3 fix): SQL filters out 'order_filled' (sim bookkeeping,
    not a decision) so multi-action cycles aren't polluted by fill events.
    Python uses DECISION_ACTION_PRIORITY (fixed order) to pick a primary
    action when a cycle has multiple decision actions — avoids set-iteration
    non-determinism (PYTHONHASHSEED-dependent).
    """
    async with engine.connect() as conn:
        active_rows = (await conn.execute(text("""
            SELECT cycle_id,
                   GROUP_CONCAT(DISTINCT action) AS actions,
                   COUNT(DISTINCT action) AS distinct_count
            FROM trade_actions
            WHERE session_id = :sid
              AND action != 'order_filled'   -- drop sim bookkeeping (not a decision)
            GROUP BY cycle_id
        """), {"sid": session_id})).all()
        all_rows = (await conn.execute(text(
            "SELECT cycle_id FROM agent_cycles WHERE session_id = :sid"
        ), {"sid": session_id})).all()
    active_ids = {r.cycle_id for r in active_rows}
    all_ids = {r.cycle_id for r in all_rows}
    pure_obs = all_ids - active_ids
    dist: dict[str, int] = {"hold (pure-observation)": len(pure_obs)} if pure_obs else {}
    for r in active_rows:
        # GROUP BY + WHERE filter ensures GROUP_CONCAT(DISTINCT action) is non-empty
        # for any row in active_rows; the `or ""` guard is defensive only — if
        # SQLite ever returned empty/NULL it would split to {""} which doesn't
        # match any priority-list entry and falls through to wake-only branch.
        actions = set((r.actions or "").split(","))
        if actions <= {"set_next_wake", "set_next_wake_at"} and actions:
            dist["hold (wake-only)"] = dist.get("hold (wake-only)", 0) + 1
            continue
        # Pick primary action by fixed priority (deterministic)
        for primary in DECISION_ACTION_PRIORITY:
            if primary in actions:
                dist[primary] = dist.get(primary, 0) + 1
                break
        else:
            # No priority-list action matched — only set_next_wake plus
            # something unknown; treat as wake-only (defensive; should not
            # happen for sessions written by current src/cli/app.py paths).
            dist["hold (wake-only)"] = dist.get("hold (wake-only)", 0) + 1
    return dist


async def five_field_complete_rate(engine, session_id: str) -> float | None:
    """Reads v_cycle_metrics.five_field_complete column.

    Pre-existing schema caveat (Phase 1 PR #42): despite the name,
    `five_field_complete` checks only 4 anchors (stance + active_commitments
    + this_cycle_delta + thesis_invalidation; **excludes** has_watch_list)
    — see views.py:74-76 `>= 4`. This metric is "first-4 fields complete
    rate" semantically. Renaming is W3 follow-up; Phase 2 sticks with
    column name to avoid view churn.
    """
    # Forensic filter: 5-field rate is a decision-quality average.
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT AVG(CAST(five_field_complete AS REAL)) AS rate
            FROM v_cycle_metrics WHERE session_id = :sid AND is_ok_cycle = 1
        """), {"sid": session_id})).first()
    return row.rate


async def per_field_hit_rate(engine, session_id: str) -> dict[str, float | None]:
    # Forensic filter: per-anchor hit rates are decision-quality averages.
    fields = ["has_stance", "has_active_commitments", "has_this_cycle_delta",
              "has_thesis_invalidation", "has_watch_list"]
    out: dict[str, float | None] = {}
    async with engine.connect() as conn:
        for f in fields:
            row = (await conn.execute(text(
                f"SELECT AVG(CAST({f} AS REAL)) AS rate FROM v_cycle_metrics "
                f"WHERE session_id = :sid AND is_ok_cycle = 1"
            ), {"sid": session_id})).first()
            out[f] = row.rate
    return out


async def avg_decision_length_chars(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "decision_length")


async def decision_length_p95(engine, session_id: str) -> float | None:
    # Forensic filter: percentile is "typical cycle" — exclude forensic.
    async with engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT decision_length FROM v_cycle_metrics
            WHERE session_id = :sid AND is_ok_cycle = 1
              AND decision_length IS NOT NULL
            ORDER BY decision_length
        """), {"sid": session_id})).all()
    return _percentile([r.decision_length for r in rows], 95)


async def avg_reasoning_tokens(engine, session_id: str) -> float | None:
    return await _avg_view_column(engine, session_id, "reasoning_tokens")


async def avg_thinking_chars(engine, session_id: str) -> float | None:
    # Forensic filter: cycle-level average of reasoning length excludes
    # is_ok_cycle = 0. Direct agent_cycles query (LENGTH not in view), so
    # replicate the is_ok_cycle predicate inline (matches views.py:77-80).
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT AVG(LENGTH(reasoning)) AS avg_chars FROM agent_cycles
            WHERE session_id = :sid AND reasoning IS NOT NULL
              AND execution_status = 'ok'
              AND decision IS NOT NULL AND length(decision) > 0
        """), {"sid": session_id})).first()
    return row.avg_chars


async def alert_lifecycle_summary(engine, session_id: str) -> dict:
    """Reads v_alert_lifecycle (column = cancel_attempt_count per views.py:142)."""
    async with engine.connect() as conn:
        row = (await conn.execute(text("""
            SELECT
              CAST(SUM(CASE WHEN triggered_at IS NOT NULL THEN 1 ELSE 0 END) AS REAL)
                / NULLIF(COUNT(*), 0) AS triggered_rate,
              CAST(SUM(CASE WHEN cancelled_at IS NOT NULL THEN 1 ELSE 0 END) AS REAL)
                / NULLIF(COUNT(*), 0) AS cancelled_rate,
              AVG(cancel_attempt_count) AS avg_cancel_attempt_count
            FROM v_alert_lifecycle WHERE session_id = :sid
        """), {"sid": session_id})).first()
    return {
        "triggered_rate": row.triggered_rate,
        "cancelled_rate": row.cancelled_rate,
        "avg_cancel_attempt_count": row.avg_cancel_attempt_count,
    }


# ── Legacy session guard + caveats helpers ────────────────────────────────────


async def assert_schema_migrated(engine) -> None:
    """Spec §6.2 row 3: schema-missing fail-fast.

    Probes every hard dependency the scripts read at runtime:
      tables: sessions, agent_cycles, sim_orders, trade_actions, tool_calls
      views:  v_cycle_metrics, v_order_lifecycle, v_alert_lifecycle
    Partial migrations (e.g. tables present but a view missing) previously
    leaked SQLAlchemy tracebacks past this guard — covered by reviewer
    findings on PR #43.

    On 'no such table' or 'no such view' → SystemExit with spec-prescribed
    friendly message + alembic hint. Other OperationalErrors propagate
    per spec §6.2 last row.
    """
    from sqlalchemy.exc import OperationalError
    probes = (
        "sessions", "agent_cycles", "sim_orders", "trade_actions", "tool_calls",
        "v_cycle_metrics", "v_order_lifecycle", "v_alert_lifecycle",
    )
    try:
        async with engine.connect() as conn:
            for name in probes:
                await conn.execute(text(f"SELECT 1 FROM {name} LIMIT 0"))
    except OperationalError as e:
        msg = str(e).lower()
        if "no such table" in msg or "no such view" in msg:
            raise SystemExit(
                "agent_cycles / sim_orders / v_cycle_metrics not found in DB.\n"
                "Run: alembic upgrade head"
            )
        raise


def assert_not_legacy(session) -> None:
    created_at = session.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at < R2_7_MERGED_AT:
        raise SystemExit(
            f"Session '{session.name}' was created at {created_at.isoformat()} "
            f"(before R2-7 schema reframe at {R2_7_MERGED_AT.date()}); "
            f"legacy sessions are intentionally unsupported "
            f"(pre-R2-7 schema cutoff)."
        )


def render_caveats_per_side(
    rts, caveats, *, prefix: str,
    ok_cycle_count: int,
    forensic_count: int = 0,
    null_field_summary: list[tuple[str, int]] | None = None,
) -> str:
    """Emit 8 per-session caveat templates (spec §6.3 rows 1-7 + 10).

    Args:
      rts: roundtrips list (drives "0 closed roundtrips" branch).
      caveats: dict from collect_roundtrips (unclosed/invariant/liquidation/stale).
      prefix: '' for analyze single-sim; '[A] ' / '[B] ' for diff per-side.
      ok_cycle_count: drives "0 ok cycles" branch.
      forensic_count: drives "N forensic cycle(s)" branch.
      null_field_summary: list of (field, row_count) for >5% NULL fields.
    """
    null_field_summary = null_field_summary or []
    lines: list[str] = []

    if ok_cycle_count == 0:
        # Cycle averages / rates / decision-derived metrics now filter is_ok_cycle=1
        # (spec §6.3 forensic-exclusion contract), so they ARE N/A. Raw aggregations
        # (token sums, total cycle counts, distributions) still report all cycles.
        lines.append(
            f"- {prefix}Session has 0 ok cycles — cycle averages, rates, "
            f"and decision-derived metrics are N/A; raw sums and counts "
            f"still reported."
        )

    if not rts and ok_cycle_count > 0:
        lines.append(f"- {prefix}0 closed roundtrips — PnL metrics N/A.")

    unclosed = caveats.get("unclosed_lot_count", {"long": 0, "short": 0})
    n_unclosed = unclosed["long"] + unclosed["short"]
    if n_unclosed:
        lines.append(
            f"- {prefix}{n_unclosed} unclosed lot(s) at session end "
            f"(long: {unclosed['long']}, short: {unclosed['short']}) "
            f"excluded from roundtrip metrics."
        )

    if caveats.get("invariant_violations"):
        lines.append(
            f"- {prefix}{caveats['invariant_violations']} invariant violation(s) "
            f"detected — see stderr logs for details."
        )

    if caveats.get("liquidation_count"):
        lines.append(
            f"- {prefix}{caveats['liquidation_count']} liquidation event(s) — "
            f"close_cycle_id N/A (liquidation does not write 5-enum trade_action); "
            f"pnl read from trade_actions.pnl due to sim pnl_cap."
        )

    if caveats.get("stale_close_amount_count"):
        lines.append(
            f"- {prefix}{caveats['stale_close_amount_count']} stale close amount(s) — "
            f"actual_amount derivation failed (fee or fee_rate missing); "
            f"fell back to sim_orders.amount which may overstate close size."
        )

    if forensic_count:
        lines.append(
            f"- {prefix}{forensic_count} forensic cycle(s) "
            f"(execution_status != 'ok') — excluded from cycle averages."
        )

    for field, count in null_field_summary:
        lines.append(
            f"- {prefix}{count} rows with NULL {field} in agent_cycles — "
            f"affected metrics may be biased."
        )

    return "\n".join(lines)


def render_caveats_diff_only(
    *, a_eq_b: bool, cross_symbol: tuple[str, str] | None,
) -> str:
    """Emit 2 diff-specific caveat templates (spec §6.3 rows 8 + 9)."""
    lines: list[str] = []
    if a_eq_b:
        lines.append("- WARNING: A and B refer to same session — all deltas are zero.")
    if cross_symbol and cross_symbol[0] != cross_symbol[1]:
        lines.append(
            f"- WARNING: A={cross_symbol[0]}, B={cross_symbol[1]}; "
            f"PnL comparable in USDT but market context differs."
        )
    return "\n".join(lines)
