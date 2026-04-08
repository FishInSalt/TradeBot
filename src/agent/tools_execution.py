from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)


async def _record_trade_open(deps: TradingDeps, **kwargs) -> int | None:
    """Persist a new TradeRecord and return its id. Returns None on failure."""
    if deps.db_engine is None:
        return None
    from src.storage.database import get_session
    from src.storage.models import TradeRecord

    try:
        async with get_session(deps.db_engine) as session:
            record = TradeRecord(session_id=deps.session_id, **kwargs)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record.id
    except Exception:
        logger.warning("Failed to persist trade record to database", exc_info=True)
        return None


async def _update_trade_closed(deps: TradingDeps, symbol: str, side: str, pnl: float) -> None:
    """Find the matching open TradeRecord and update it to closed."""
    if deps.db_engine is None:
        return
    from sqlalchemy import select
    from src.storage.database import get_session
    from src.storage.models import TradeRecord

    try:
        async with get_session(deps.db_engine) as session:
            stmt = (
                select(TradeRecord)
                .where(TradeRecord.session_id == deps.session_id)
                .where(TradeRecord.symbol == symbol)
                .where(TradeRecord.side == side)
                .where(TradeRecord.status == "open")
                .order_by(TradeRecord.created_at.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            record = result.scalar_one_or_none()
            if record:
                record.status = "closed"
                record.pnl = pnl
                record.closed_at = datetime.now(timezone.utc)
                await session.commit()
            else:
                logger.warning(f"No open trade record found for {symbol} {side} to close")
    except Exception:
        logger.warning("Failed to update trade record in database", exc_info=True)


async def _check_approval(deps: TradingDeps, action: str, reasoning: str,
                           position_pct: float, leverage: int,
                           stop_loss: float | None = None,
                           take_profit: float | None = None) -> bool:
    """Check human approval if gate is enabled. Returns True if approved."""
    if not deps.approval_enabled or deps.approval_gate is None:
        return True
    gate = deps.approval_gate
    if hasattr(gate, 'check'):
        return await gate.check(action, reasoning, position_pct, leverage, stop_loss, take_profit)
    return True


async def open_position(
    deps: TradingDeps,
    side: str,
    position_pct: float,
    leverage: int,
    stop_loss_price: float | None = None,
    take_profit_price: float | None = None,
) -> str:
    """Open a new position. side='long' or 'short'. position_pct=% of free balance."""
    balance = await deps.exchange.fetch_balance()
    ticker = await deps.market_data.get_ticker(deps.symbol)
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    quantity = (usdt_amount * leverage) / ticker.last

    # Human approval gate
    reasoning = f"Open {side} {position_pct}% at ~{ticker.last:.2f}, {leverage}x leverage"
    approved = await _check_approval(
        deps, f"open_{side}", reasoning, position_pct, leverage,
        stop_loss_price, take_profit_price
    )
    if not approved:
        return "Trade rejected by human approval."

    await deps.exchange.set_leverage(deps.symbol, leverage)
    order_side = "buy" if side == "long" else "sell"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=order_side, order_type="market", amount=quantity
    )

    # Set stop loss and take profit if provided
    sl_msg = ""
    tp_msg = ""
    if stop_loss_price is not None:
        try:
            sl_side = "sell" if side == "long" else "buy"
            await deps.exchange.create_order(
                symbol=deps.symbol, side=sl_side, order_type="stop",
                amount=quantity, price=stop_loss_price
            )
            sl_msg = f"\n  Stop Loss: {stop_loss_price:.2f}"
        except Exception:
            logger.warning(f"Failed to set stop loss at {stop_loss_price}")
            sl_msg = f"\n  Stop Loss: FAILED to set at {stop_loss_price:.2f}"

    if take_profit_price is not None:
        try:
            tp_side = "sell" if side == "long" else "buy"
            await deps.exchange.create_order(
                symbol=deps.symbol, side=tp_side, order_type="take_profit",
                amount=quantity, price=take_profit_price
            )
            tp_msg = f"\n  Take Profit: {take_profit_price:.2f}"
        except Exception:
            logger.warning(f"Failed to set take profit at {take_profit_price}")
            tp_msg = f"\n  Take Profit: FAILED to set at {take_profit_price:.2f}"

    # Record trade in database (non-fatal if fails)
    await _record_trade_open(
        deps,
        symbol=deps.symbol, side=side, entry_price=ticker.last,
        quantity=quantity, leverage=leverage, status="open",
        stop_loss=stop_loss_price, take_profit=take_profit_price,
        decision_reason=reasoning,
    )

    return (
        f"Position opened:\n"
        f"  Side: {side} | Quantity: {quantity:.6f} | Leverage: {leverage}x\n"
        f"  Entry: ~{ticker.last:.2f} | Order: {order.id} ({order.status})"
        f"{sl_msg}{tp_msg}"
    )


async def close_position(deps: TradingDeps) -> str:
    """Close all open positions."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No positions to close."

    # Human approval gate for closing
    total_pnl = sum(p.unrealized_pnl for p in positions)
    reasoning = f"Close {len(positions)} position(s), total PnL: {total_pnl:.2f} USDT"
    approved = await _check_approval(deps, "close", reasoning, 0, 0)
    if not approved:
        return "Close rejected by human approval."

    results = []
    for p in positions:
        order_side = "sell" if p.side == "long" else "buy"
        order = await deps.exchange.create_order(
            symbol=deps.symbol, side=order_side, order_type="market", amount=p.contracts
        )

        # Update the matching open record to closed
        await _update_trade_closed(deps, deps.symbol, p.side, p.unrealized_pnl)

        results.append(
            f"Closed {p.side} {p.contracts} @ PnL: {p.unrealized_pnl:.2f} | Order: {order.id}"
        )
    return "Positions closed:\n" + "\n".join(results)


async def set_stop_loss(deps: TradingDeps, price: float) -> str:
    """Set stop loss on current position."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No open position to set stop loss on."
    p = positions[0]
    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="stop", amount=p.contracts, price=price
    )
    return f"Stop loss set at {price:.2f} | Order: {order.id}"


async def set_take_profit(deps: TradingDeps, price: float) -> str:
    """Set take profit on current position."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No open position to set take profit on."
    p = positions[0]
    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="take_profit", amount=p.contracts, price=price
    )
    return f"Take profit set at {price:.2f} | Order: {order.id}"


async def adjust_leverage(deps: TradingDeps, leverage: int) -> str:
    """Adjust leverage for the trading symbol."""
    await deps.exchange.set_leverage(deps.symbol, leverage)
    return f"Leverage adjusted to {leverage}x for {deps.symbol}"
