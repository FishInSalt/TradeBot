from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)


async def _record_trade(deps: TradingDeps, **kwargs) -> None:
    """Persist a TradeRecord to the database if db_engine is available."""
    if deps.db_engine is None:
        return
    from src.storage.database import get_session
    from src.storage.models import TradeRecord

    async with get_session(deps.db_engine) as session:
        session.add(TradeRecord(**kwargs))
        await session.commit()


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

    # Record trade in database
    await _record_trade(
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
    results = []
    for p in positions:
        order_side = "sell" if p.side == "long" else "buy"
        order = await deps.exchange.create_order(
            symbol=deps.symbol, side=order_side, order_type="market", amount=p.contracts
        )

        # Record closed trade
        await _record_trade(
            deps,
            symbol=deps.symbol, side=p.side, entry_price=p.entry_price,
            quantity=p.contracts, leverage=p.leverage, status="closed",
            pnl=p.unrealized_pnl,
            decision_reason=f"Position closed via close_position tool",
        )

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
