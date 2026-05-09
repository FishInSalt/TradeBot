"""Test fixtures for Phase 2 cross-sim analytics. Underscore = internal."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from scripts._sim_metrics import R2_7_MERGED_AT


def _safe_created_at(offset_days: int = 1) -> datetime:
    """Default session created_at: post-R2-7 cutoff to avoid legacy reject."""
    return R2_7_MERGED_AT + timedelta(days=offset_days)


def _resolve_db_path(engine) -> str:
    """Extract sqlite filesystem path from async engine URL (for subprocess tests)."""
    url = str(engine.url)
    return url.replace("sqlite+aiosqlite:///", "")


# Fixture builders populated by T2 (make_session / make_cycle / make_open_lot / make_close_fill).

from sqlalchemy import insert
from src.storage.models import (
    Session as SessionModel, AgentCycle, SimOrder, TradeAction,
)


async def make_session_id(engine, name: str) -> str:
    """Look up session UUID by name. Raises if not found."""
    from sqlalchemy import text
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT id FROM sessions WHERE name = :name"),
            {"name": name},
        )).first()
    if row is None:
        raise ValueError(f"Session '{name}' not found in DB")
    return row.id


async def make_session(
    engine, *, name="test_sim", symbol="BTC/USDT:USDT",
    created_at=None, fee_rate=0.0005, initial_balance=100.0,
) -> str:
    if created_at is None:
        created_at = _safe_created_at(1)
    session_id = str(uuid4())
    async with engine.begin() as conn:
        await conn.execute(insert(SessionModel).values(
            id=session_id, name=name, symbol=symbol,
            created_at=created_at, fee_rate=fee_rate,
            initial_balance=initial_balance,
        ))
    return session_id


async def make_cycle(
    engine, session_id, cycle_id, *, decision=None,
    execution_status="ok", triggered_by="scheduled",
    state_snapshot=None, reasoning=None,
    input_tokens=5000, output_tokens=500, cache_read_tokens=3500,
    wall_time_ms=1200, llm_call_ms=900, reasoning_tokens=0,
):
    async with engine.begin() as conn:
        await conn.execute(insert(AgentCycle).values(
            session_id=session_id, cycle_id=cycle_id,
            triggered_by=triggered_by, execution_status=execution_status,
            decision=decision, state_snapshot=state_snapshot, reasoning=reasoning,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens, wall_time_ms=wall_time_ms,
            llm_call_ms=llm_call_ms, reasoning_tokens=reasoning_tokens,
            tokens_consumed=input_tokens + output_tokens,
            cache_hit_rate=cache_read_tokens / input_tokens if input_tokens else None,
        ))


async def make_open_lot(
    engine, session_id, *, cycle_id, side="long",
    entry_px=80000.0, amount=0.1, leverage=1,
    fee=None, fee_rate=0.0005,
    filled_at=None, order_type="market",
) -> str:
    """Insert open fill (sim_orders + open_position trade_action).

    fee=None → auto-compute amount * entry_px * fee_rate (sim's actual_amount-based
    fee pattern, simulated.py:401). Pass explicit fee to override.
    """
    if fee is None:
        fee = amount * entry_px * fee_rate
    if filled_at is None:
        filled_at = _safe_created_at(2)
    fill_side = "buy" if side == "long" else "sell"
    order_id = str(uuid4())
    async with engine.begin() as conn:
        await conn.execute(insert(SimOrder).values(
            session_id=session_id, order_id=order_id,
            symbol="BTC/USDT:USDT", side=fill_side, position_side=side,
            order_type=order_type, amount=amount, status="filled",
            filled_price=entry_px, fee=fee, filled_at=filled_at,
            leverage=leverage,
        ))
        await conn.execute(insert(TradeAction).values(
            session_id=session_id, cycle_id=cycle_id,
            action="open_position", order_id=order_id,
            symbol="BTC/USDT:USDT", side=fill_side, price=entry_px,
        ))
    return order_id


async def make_close_fill(
    engine, session_id, *, cycle_id, side="long",
    exit_px=82000.0, amount=0.1,
    fee=None, fee_rate=0.0005,
    exit_type="market", pnl_gross=None, filled_at=None,
) -> str:
    """Insert close fill. fee=None → auto-compute (matches non-stale sim path).

    For stale SL/TP scenarios pass an explicit fee inconsistent with `amount`
    to drive the _derive_close_amount fallback.

    pnl_gross is the sim weighted-entry PnL written to trade_actions.pnl
    (drives P2 total_pnl_net + liquidation pnl_cap path; not used for FIFO
    lot attribution which recomputes from lot.entry_px for non-liquidation).

    5-enum action mapping:
      market/stop/take_profit → close_position
      limit                   → place_limit_order
      liquidation             → no 5-enum row (close_cycle_id stays None per §4.1)
    """
    if fee is None:
        fee = amount * exit_px * fee_rate
    if filled_at is None:
        filled_at = _safe_created_at(3)
    fill_side = "sell" if side == "long" else "buy"
    order_id = str(uuid4())
    if exit_type == "liquidation":
        action_5enum = None
    elif exit_type == "limit":
        action_5enum = "place_limit_order"
    else:
        action_5enum = "close_position"
    async with engine.begin() as conn:
        await conn.execute(insert(SimOrder).values(
            session_id=session_id, order_id=order_id,
            symbol="BTC/USDT:USDT", side=fill_side, position_side=side,
            order_type=exit_type, amount=amount, status="filled",
            filled_price=exit_px, fee=fee, filled_at=filled_at,
        ))
        if action_5enum is not None:
            await conn.execute(insert(TradeAction).values(
                session_id=session_id, cycle_id=cycle_id,
                action=action_5enum, order_id=order_id,
                symbol="BTC/USDT:USDT", side=fill_side, price=exit_px,
            ))
        # Always write order_filled with pnl (drives P2 + liquidation pnl_cap path)
        await conn.execute(insert(TradeAction).values(
            session_id=session_id, cycle_id=cycle_id,
            action="order_filled", order_id=order_id,
            symbol="BTC/USDT:USDT", side=fill_side, price=exit_px,
            pnl=pnl_gross, fee=fee, trigger_reason=exit_type,
        ))
    return order_id
