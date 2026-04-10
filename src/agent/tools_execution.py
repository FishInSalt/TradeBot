from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)


async def _record_action(deps: TradingDeps, action: str, order_id: str | None = None,
                          side: str | None = None, price: float | None = None,
                          pnl: float | None = None, reasoning: str | None = None) -> None:
    """写入一条 TradeAction 记录。写入失败不影响 tool 返回（容错）。"""
    if deps.db_engine is None:
        return
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    try:
        async with get_session(deps.db_engine) as session:
            session.add(TradeAction(
                session_id=deps.session_id,
                action=action,
                order_id=order_id,
                symbol=deps.symbol,
                side=side,
                price=price,
                pnl=pnl,
                reasoning=reasoning,
            ))
            await session.commit()
    except Exception:
        logger.warning("Failed to record TradeAction", exc_info=True)


async def _check_approval(deps: TradingDeps, action: str, action_desc: str,
                           position_pct: float, leverage: int) -> bool:
    """Check human approval. action_desc is a formatted description (NOT agent reasoning)."""
    if not deps.approval_enabled or deps.approval_gate is None:
        return True
    gate = deps.approval_gate
    if hasattr(gate, 'check'):
        return await gate.check(action, action_desc, position_pct, leverage)
    return True


async def open_position(
    deps: TradingDeps,
    side: str,
    position_pct: float,
    leverage: int,
    reasoning: str,
) -> str:
    """Open a new position. side='long' or 'short'. position_pct=% of free balance."""
    balance = await deps.exchange.fetch_balance()
    ticker = await deps.market_data.get_ticker(deps.symbol)
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    raw_quantity = (usdt_amount * leverage) / ticker.last
    quantity = deps.exchange.amount_to_precision(deps.symbol, raw_quantity)
    if quantity <= 0:
        return f"Position too small: {raw_quantity:.8f} rounds to 0 after precision adjustment."

    # Human approval gate
    action_desc = f"Open {side} {position_pct}% at ~{ticker.last:.2f}, {leverage}x leverage"
    approved = await _check_approval(deps, f"open_{side}", action_desc, position_pct, leverage)
    if not approved:
        return "Trade rejected by human approval."

    await deps.exchange.set_leverage(deps.symbol, leverage)
    order_side = "buy" if side == "long" else "sell"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=order_side, order_type="market", amount=quantity
    )

    await _record_action(
        deps, action="open_position", order_id=order.id,
        side=side, reasoning=reasoning,
    )

    return (
        f"Order submitted: {side} {quantity:.6f} @ ~{ticker.last:.2f}, {leverage}x | ID: {order.id}\n"
        f"You will be notified when filled."
    )


async def close_position(deps: TradingDeps, reasoning: str) -> str:
    """Close all open positions."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No positions to close."

    total_pnl = sum(p.unrealized_pnl for p in positions)
    action_desc = f"Close {len(positions)} position(s), PnL: {total_pnl:.2f}"
    approved = await _check_approval(deps, "close", action_desc, 0, 0)
    if not approved:
        return "Close rejected by human approval."

    order_ids = []
    for p in positions:
        order_side = "sell" if p.side == "long" else "buy"
        order = await deps.exchange.create_order(
            symbol=deps.symbol, side=order_side, order_type="market", amount=p.contracts
        )
        order_ids.append(order.id)
        await _record_action(
            deps, action="close_position", order_id=order.id,
            side=p.side, reasoning=reasoning,
        )

    return f"Orders submitted: close {len(positions)} position(s) | IDs: {', '.join(order_ids)}\nYou will be notified when filled."


async def set_stop_loss(deps: TradingDeps, price: float, reasoning: str) -> str:
    """Set stop loss on current position. Auto-cancels existing stop orders."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No open position to set stop loss on."
    p = positions[0]

    # Cancel existing stop orders
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "stop":
            await deps.exchange.cancel_order(o.id, deps.symbol)

    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="stop", amount=p.contracts, price=price
    )

    await _record_action(
        deps, action="set_stop_loss", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    return f"Stop loss set at {price:.2f} | Order: {order.id}"


async def set_take_profit(deps: TradingDeps, price: float, reasoning: str) -> str:
    """Set take profit on current position. Auto-cancels existing take profit orders."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No open position to set take profit on."
    p = positions[0]

    # Cancel existing take profit orders
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "take_profit":
            await deps.exchange.cancel_order(o.id, deps.symbol)

    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="take_profit", amount=p.contracts, price=price
    )

    await _record_action(
        deps, action="set_take_profit", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    return f"Take profit set at {price:.2f} | Order: {order.id}"


async def adjust_leverage(deps: TradingDeps, leverage: int, reasoning: str) -> str:
    """Adjust leverage for the trading symbol."""
    await deps.exchange.set_leverage(deps.symbol, leverage)
    await _record_action(
        deps, action="adjust_leverage",
        reasoning=reasoning,
    )
    return f"Leverage adjusted to {leverage}x for {deps.symbol}"
