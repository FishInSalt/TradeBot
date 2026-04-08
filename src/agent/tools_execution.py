from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps


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

    await deps.exchange.set_leverage(deps.symbol, leverage)
    order_side = "buy" if side == "long" else "sell"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=order_side, order_type="market", amount=quantity
    )
    return (
        f"Position opened:\n"
        f"  Side: {side} | Quantity: {quantity:.6f} | Leverage: {leverage}x\n"
        f"  Entry: ~{ticker.last:.2f} | Order: {order.id} ({order.status})"
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
