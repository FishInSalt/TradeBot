from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)

# NOTE: Return string prefixes are used by src/cli/display.py (_EXECUTION_SUCCESS_PREFIXES)
# to detect success vs business rejection. If you change a return string's prefix,
# update _EXECUTION_SUCCESS_PREFIXES in display.py accordingly.


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
                cycle_id=deps.cycle_id,        # ← 新增（从 deps 取，11 个 callers 0 改动）
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

    # Duplicate order prevention
    if deps.exchange.has_pending_market_order(deps.symbol):
        return "A market order is already pending. Wait for fill confirmation before opening another position."

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

    order_side = "sell" if positions[0].side == "long" else "buy"
    if deps.exchange.has_pending_market_order(deps.symbol, side=order_side):
        return "A close order is already pending. Wait for fill confirmation."

    total_pnl = sum(p.unrealized_pnl for p in positions)
    action_desc = f"Close {len(positions)} position(s), PnL: {total_pnl:.2f}"
    approved = await _check_approval(deps, "close", action_desc, 0, 0)
    if not approved:
        return "Close rejected by human approval."

    order_ids = []
    for p in positions:
        order_side = "sell" if p.side == "long" else "buy"
        order = await deps.exchange.create_order(
            symbol=deps.symbol, side=order_side, order_type="market",
            amount=p.contracts,
            params={"reduceOnly": True},  # ensures OKX echoes info.reduceOnly=true in fill event
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
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)

    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="stop", amount=p.contracts, price=price
    )

    await _record_action(
        deps, action="set_stop_loss", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    ticker = await deps.market_data.get_ticker(deps.symbol)
    if ticker.last > 0:
        dist_pct = (price - ticker.last) / ticker.last * 100
        return f"Stop loss set at {price:.2f} ({dist_pct:+.2f}% from current {ticker.last:.2f}) | Order: {order.id}"
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
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)

    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="take_profit", amount=p.contracts, price=price
    )

    await _record_action(
        deps, action="set_take_profit", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    ticker = await deps.market_data.get_ticker(deps.symbol)
    if ticker.last > 0:
        dist_pct = (price - ticker.last) / ticker.last * 100
        return f"Take profit set at {price:.2f} ({dist_pct:+.2f}% from current {ticker.last:.2f}) | Order: {order.id}"
    return f"Take profit set at {price:.2f} | Order: {order.id}"


async def adjust_leverage(deps: TradingDeps, leverage: int, reasoning: str) -> str:
    """Adjust leverage for the trading symbol."""
    await deps.exchange.set_leverage(deps.symbol, leverage)
    await _record_action(
        deps, action="adjust_leverage",
        reasoning=reasoning,
    )
    return f"Leverage adjusted to {leverage}x for {deps.symbol}"


async def set_price_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    reasoning: str,
) -> str:
    """Adjust price alert parameters. threshold_pct: min 0.1, max 50, window_minutes: min 1, max 240."""
    # Check if alerts are enabled
    if deps.exchange.get_alert_params() is None:
        return "Alerts are disabled for this session. Enable alerts in wizard to use this feature."

    # Parameter validation
    if not (0.1 <= threshold_pct <= 50.0):
        return f"Invalid threshold_pct: must be 0.1-50.0, got {threshold_pct}"
    if not (1 <= window_minutes <= 240):
        return f"Invalid window_minutes: must be 1-240, got {window_minutes}"

    deps.exchange.update_alert_params(threshold_pct, window_minutes)

    await _record_action(
        deps, action="set_price_alert",
        reasoning=f"threshold={threshold_pct}%, window={window_minutes}min | {reasoning}",
    )

    return (
        f"Price alert updated: threshold={threshold_pct}%, "
        f"window={window_minutes}min"
    )


async def add_price_level_alert(
    deps: TradingDeps,
    price: float,
    direction: str,
    reasoning: str,
) -> str:
    """Set a one-shot price level alert. direction: 'above' or 'below'."""
    if direction not in ("above", "below"):
        return f"Invalid direction: must be 'above' or 'below', got '{direction}'"

    alert_id = deps.exchange.add_price_level_alert(price, direction, deps.symbol, reasoning)
    if alert_id is None:
        return "Price level alert limit reached (max 20). Remove or wait for existing alerts to trigger."

    await _record_action(
        deps, action="add_price_level_alert", price=price,
        reasoning=f"{direction} {price} | {reasoning}",
    )

    # Immediate trigger warning
    latest = deps.exchange._latest_price
    if latest is not None:
        if (direction == "above" and latest >= price) or \
           (direction == "below" and latest <= price):
            return (
                f"Alert set (id={alert_id}), but WARNING: current price ({latest:.2f}) "
                f"already {'above' if direction == 'above' else 'below'} {price:.2f}, "
                f"may trigger immediately"
            )

    return f"Price level alert set: {direction} {price:.2f} (id={alert_id})"


async def cancel_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    reasoning: str,
) -> str:
    """Remove a price level alert by ID."""
    ok = deps.exchange.remove_price_level_alert(alert_id)
    if ok:
        await _record_action(
            deps, action="cancel_price_level_alert",
            reasoning=f"id={alert_id} | {reasoning}",
        )
        return f"Price level alert cancelled (id={alert_id})"
    return f"Alert {alert_id} not found (already triggered or never existed)"


async def set_next_wake(
    deps: TradingDeps,
    minutes: int,
    reasoning: str,
) -> str:
    """Set the next wake interval (one-shot). Clamped to configured min/max."""
    if deps.set_next_wake_fn is None:
        return "Dynamic wake not available"
    clamped = max(deps.wake_min_minutes, min(minutes, deps.wake_max_minutes))
    deps.set_next_wake_fn(clamped)

    await _record_action(
        deps, action="set_next_wake",
        reasoning=f"interval={clamped}min | {reasoning}",
    )

    if clamped != minutes:
        return f"Next wake set to {clamped} min (clamped from {minutes}). Reason: {reasoning}"
    return f"Next wake set to {clamped} min. Reason: {reasoning}"


async def place_limit_order(
    deps: TradingDeps,
    side: str,
    price: float,
    position_pct: float,
    leverage: int,
    reasoning: str,
) -> str:
    """Place a limit order at a specific price."""
    if side not in ("long", "short"):
        return "side must be 'long' or 'short'"

    # Leverage: match position if exists, else use specified
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if positions:
        actual_leverage = positions[0].leverage
    else:
        await deps.exchange.set_leverage(deps.symbol, leverage)
        actual_leverage = leverage

    balance = await deps.exchange.fetch_balance()
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    raw_quantity = (usdt_amount * actual_leverage) / price
    quantity = deps.exchange.amount_to_precision(deps.symbol, raw_quantity)
    if quantity <= 0:
        return f"Position too small: {raw_quantity:.8f} rounds to 0 after precision adjustment."

    action_desc = f"Limit {side} {position_pct}% at {price:.2f}, {actual_leverage}x leverage"
    approved = await _check_approval(deps, f"limit_{side}", action_desc, position_pct, actual_leverage)
    if not approved:
        return "Limit order rejected by human approval."

    order_side = "buy" if side == "long" else "sell"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=order_side, order_type="limit",
        amount=quantity, price=price,
    )

    await _record_action(
        deps, action="place_limit_order", order_id=order.id,
        side=side, price=price, reasoning=reasoning,
    )

    return f"Limit order placed: {side} {quantity:.6f} @ {price:.2f}, {actual_leverage}x | ID: {order.id}"


async def cancel_order(
    deps: TradingDeps,
    order_id: str,
    reasoning: str,
) -> str:
    """Cancel a pending order (limit, stop, take_profit)."""
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    target = None
    for o in open_orders:
        if o.id == order_id:
            target = o
            break

    if target is None:
        return f"Order not found or already filled: {order_id}"

    if target.order_type == "market":
        return "Cannot cancel market orders"

    await deps.exchange.cancel_order(order_id, deps.symbol, is_algo=target.is_algo)

    await _record_action(
        deps, action="cancel_order", order_id=order_id,
        side=target.side, price=target.price, reasoning=reasoning,
    )

    price_str = f" @ {target.price:.2f}" if target.price is not None else ""
    return f"Order cancelled: {target.order_type} {target.side} {target.amount:.6f}{price_str} | ID: {order_id}"
