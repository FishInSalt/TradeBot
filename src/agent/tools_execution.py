from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from src.services.tool_call_recorder import note_biz_error

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)

# NOTE: Return string prefixes are used by src/cli/display.py (_EXECUTION_SUCCESS_PREFIXES)
# to detect success vs business rejection. If you change a return string's prefix,
# update _EXECUTION_SUCCESS_PREFIXES in display.py accordingly.


def _order_id(result) -> str:
    """Extract the order id from create_order's heterogeneous return.

    Sim market orders now settle synchronously and return a FillEvent
    (.order_id); limit/stop/take_profit (and the deferred OKX async path) still
    return an Order (.id). Full sync-receipt rendering is handled in later
    iter-sync-market-fill tasks; this shim only normalizes the id so existing
    recording / receipt paths keep working. Explicit isinstance dispatch (not
    getattr) avoids MagicMock auto-attr false positives in test mocks.
    """
    from src.integrations.exchange.base import FillEvent
    return result.order_id if isinstance(result, FillEvent) else result.id


async def _record_action(deps: TradingDeps, action: str, *,
                          order_id: str | None = None,
                          alert_id: str | None = None,
                          side: str | None = None, price: float | None = None,
                          pnl: float | None = None, reasoning: str | None = None,
                          fee: float | None = None, amount: float | None = None,
                          entry_price: float | None = None,
                          trigger_reason: str | None = None) -> None:
    """写入一条 TradeAction 记录。写入失败不影响 tool 返回（容错）。

    `*` 之后全 kwarg-only — 防 future positional caller 把例如 side="long"
    误写入 alert_id 列（PR #42 review v4 I-5 修订）。

    fee/amount/entry_price/trigger_reason 供 order_filled 行使用（同步市价路径，
    per spec §5.1）；非 fill 行留 None。
    """
    if deps.db_engine is None:
        return
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    try:
        async with get_session(deps.db_engine) as session:
            session.add(TradeAction(
                session_id=deps.session_id,
                cycle_id=deps.cycle_id,
                action=action,
                order_id=order_id,
                alert_id=alert_id,
                symbol=deps.symbol,
                side=side,
                price=price,
                pnl=pnl,
                reasoning=reasoning,
                fee=fee,
                amount=amount,
                entry_price=entry_price,
                trigger_reason=trigger_reason,
            ))
            await session.commit()
    except Exception:
        logger.warning("Failed to record TradeAction", exc_info=True)


async def _record_order_filled(deps: TradingDeps, fill) -> None:
    """从同步 FillEvent 记一条 order_filled TradeAction（sim 同步市价路径）。

    字段集与 app._record_action_from_fill 对齐，使 metrics.total_fees /
    models amount-invariant / trigger_reason 分类在同步路径下仍成立（spec §5.1）。
    """
    await _record_action(
        deps, action="order_filled", order_id=fill.order_id,
        side=fill.position_side, price=fill.fill_price, pnl=fill.pnl,
        fee=fill.fee, amount=fill.amount, entry_price=fill.entry_price,
        trigger_reason=fill.trigger_reason,
        reasoning=f"(exchange: {fill.trigger_reason} order filled @ {fill.fill_price:.2f})",
    )


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
    side: Literal["long", "short"],
    position_pct: float,
    leverage: int,
    reasoning: str,
) -> str:
    """Open a new position. side='long' or 'short'. position_pct=% of free balance."""
    balance = await deps.exchange.fetch_balance()
    ticker = await deps.market_data.get_ticker(deps.symbol)
    contract_size = await deps.exchange.get_contract_size(deps.symbol)
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    raw_quantity = (usdt_amount * leverage) / (ticker.last * contract_size)
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
    order_id = _order_id(order)

    await _record_action(
        deps, action="open_position", order_id=order_id,
        side=side, reasoning=reasoning,
    )

    notional = ticker.last * quantity * contract_size
    est_entry_fee = notional * deps.fee_rate
    return (
        f"Order submitted: {side} {quantity:.6f} @ ~{ticker.last:.2f}, {leverage}x | ID: {order_id}\n"
        f"Est. entry fee: ~-{est_entry_fee:.2f} USDT "
        f"(notional ~{notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
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

    # Fee + net PnL estimation BEFORE approval gate (so approval message shows both views).
    # contract_size factor is required for USDT-denominated notional — matches
    # get_position Risk Exposure convention (tools_perception.py: notional = contracts × price × contract_size).
    ticker = await deps.market_data.get_ticker(deps.symbol)
    contract_size = await deps.exchange.get_contract_size(deps.symbol)
    total_unrealized = sum(p.unrealized_pnl for p in positions)
    total_contracts = sum(p.contracts for p in positions)
    total_entry_fee = sum(p.entry_price * p.contracts * contract_size * deps.fee_rate for p in positions)
    # Use bid/ask matching actual market close fill price (sim _fill_market_close convention)
    est_fill_price = ticker.bid if positions[0].side == "long" else ticker.ask
    est_exit_notional = est_fill_price * total_contracts * contract_size
    est_exit_fee = est_exit_notional * deps.fee_rate
    est_net_pnl = -total_entry_fee + total_unrealized - est_exit_fee

    action_desc = (
        f"Close {len(positions)} position(s), "
        f"PnL: {total_unrealized:+.2f} gross / {est_net_pnl:+.2f} net (round-trip)"
    )
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
        order_id = _order_id(order)
        deps.exchange.register_close_order_entry(order_id, p.entry_price)
        order_ids.append(order_id)
        await _record_action(
            deps, action="close_position", order_id=order_id,
            side=p.side, reasoning=reasoning,
        )

    return (
        f"Orders submitted: close {len(positions)} position(s) | IDs: {', '.join(order_ids)}\n"
        f"Est. exit fee: ~-{est_exit_fee:.2f} USDT "
        f"(notional ~{est_exit_notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
        f"Est. net PnL: ~{est_net_pnl:+.2f} USDT "
        f"(round-trip = entry fee ~-{total_entry_fee:.2f} "
        f"+ unrealized {total_unrealized:+.2f} "
        f"+ est. exit fee ~-{est_exit_fee:.2f})\n"
        f"You will be notified when filled."
    )


async def set_stop_loss(deps: TradingDeps, price: float, reasoning: str) -> str:
    """Set stop loss on the current position. Auto-cancels existing stop orders."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No open position to set stop loss on."
    p = positions[0]

    # Cancel existing stop orders + capture prev SL trigger_price for return
    # (per spec §3.4: use trigger_price not price — base.py:54 R2-7 §4.7
    # algo class contract; price is overloaded for limit-as-stop futures).
    # Multiple stops is a rare case; take the last (matches cancel sequence).
    prev_sl: float | None = None
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "stop":
            prev_sl = o.trigger_price
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)

    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="stop", amount=p.contracts, price=price,
    )
    deps.exchange.register_close_order_entry(order.id, p.entry_price)

    await _record_action(
        deps, action="set_stop_loss", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    ticker = await deps.market_data.get_ticker(deps.symbol)
    trigger_ref = deps.exchange.algo_trigger_reference
    # Shape: "old → new" prefix (update path) or single-value (first-set).
    price_str = f"{prev_sl:.2f} → {price:.2f}" if prev_sl is not None else f"{price:.2f}"
    if ticker.last > 0:
        dist_pct = (price - ticker.last) / ticker.last * 100
        return (
            f"Stop loss set at {price_str} "
            f"({dist_pct:+.2f}% from {trigger_ref} price {ticker.last:.2f}) | Order: {order.id}"
        )
    return f"Stop loss set at {price_str} | Order: {order.id}"


async def set_take_profit(deps: TradingDeps, price: float, reasoning: str) -> str:
    """Set take profit on the current position. Auto-cancels existing take_profit orders."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No open position to set take profit on."
    p = positions[0]

    # Cancel existing take profit orders + capture prev TP trigger_price
    prev_tp: float | None = None
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "take_profit":
            prev_tp = o.trigger_price
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)

    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="take_profit", amount=p.contracts, price=price,
    )
    deps.exchange.register_close_order_entry(order.id, p.entry_price)

    await _record_action(
        deps, action="set_take_profit", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    ticker = await deps.market_data.get_ticker(deps.symbol)
    trigger_ref = deps.exchange.algo_trigger_reference
    price_str = f"{prev_tp:.2f} → {price:.2f}" if prev_tp is not None else f"{price:.2f}"
    if ticker.last > 0:
        dist_pct = (price - ticker.last) / ticker.last * 100
        return (
            f"Take profit set at {price_str} "
            f"({dist_pct:+.2f}% from {trigger_ref} price {ticker.last:.2f}) | Order: {order.id}"
        )
    return f"Take profit set at {price_str} | Order: {order.id}"


async def adjust_leverage(deps: TradingDeps, leverage: int, reasoning: str) -> str:
    """Adjust leverage for the trading symbol.

    Rejects with current leverage in the message when a position is held
    (wrapper docstring promises this constraint; impl enforces it — was
    phantom guard prior to iter-tool-opt-adjust-leverage-guard).
    """
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if positions:
        return (
            f"Cannot adjust leverage while holding a position "
            f"(current: {positions[0].leverage}x). Close position first, then adjust."
        )
    await deps.exchange.set_leverage(deps.symbol, leverage)
    await _record_action(
        deps, action="adjust_leverage",
        reasoning=reasoning,
    )
    return f"Leverage adjusted to {leverage}x for {deps.symbol}"


async def set_price_volatility_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    reasoning: str,
) -> str:
    """Set the price volatility alert (singleton). Creates if none is
    configured; otherwise replaces the existing one — replacing resets the
    rolling tick window. threshold_pct: 0.1-50, window_minutes: 1-240."""
    # Parameter validation
    if not (0.1 <= threshold_pct <= 50.0):
        note_biz_error("invalid_threshold_range")
        return f"Invalid threshold_pct: must be 0.1-50.0, got {threshold_pct}"
    if not (1 <= window_minutes <= 240):
        return f"Invalid window_minutes: must be 1-240, got {window_minutes}"

    # Capture pre-state for the success message
    prev = deps.exchange.get_alert_params()

    deps.exchange.set_volatility_alert(threshold_pct, window_minutes, deps.symbol)

    await _record_action(
        deps, action="set_price_volatility_alert",
        reasoning=f"threshold={threshold_pct}%, window={window_minutes}min | {reasoning}",
    )

    if prev is None:
        return (
            f"Price volatility alert set: threshold={threshold_pct}%, "
            f"window={window_minutes}min"
        )
    prev_t, prev_w = prev
    return (
        f"Price volatility alert replaced: threshold={threshold_pct}%, "
        f"window={window_minutes}min "
        f"(was {prev_t}%/{prev_w}min, rolling window reset)"
    )


async def cancel_price_volatility_alert(
    deps: TradingDeps,
    reasoning: str,
) -> str:
    """Cancel the active price volatility alert. Idempotent: if no alert is
    set, returns ok with a note (no mutation, no audit row).

    Args:
        reasoning: brief description of your decision logic.
    """
    prev = deps.exchange.get_alert_params()
    if prev is None:
        # State-not-found → idempotent ok with note. Matches
        # cancel_price_level_alert protocol (R2-Next-E PR #47).
        return "No volatility alert active to cancel."

    prev_t, prev_w = prev
    deps.exchange.cancel_volatility_alert()
    await _record_action(
        deps, action="cancel_price_volatility_alert",
        reasoning=reasoning,
    )
    return f"Price volatility alert cancelled (was {prev_t}%/{prev_w}min)"


async def add_price_level_alert(
    deps: TradingDeps,
    price: float,
    direction: str,
    reasoning: str,
) -> str:
    """Set a one-shot price level alert at a specific price level.

    The alert is evaluated on every market tick. It fires once the
    condition holds, then is removed. If the condition already holds
    at creation time, the alert fires on the next tick (the success
    return string includes a "fires on next tick" suffix in that case).

    Max 20 active alerts per session; all alerts for a symbol are
    auto-cleared when the position on that symbol fully closes.

    Args:
        price: trigger price level.
        direction: 'above' (fires when price >= level) or
                   'below' (fires when price <= level).
        reasoning: brief rationale for the alert (audit-only).
    """
    if direction not in ("above", "below"):
        return f"Invalid direction: must be 'above' or 'below', got '{direction}'"

    alert_id = deps.exchange.add_price_level_alert(price, direction, deps.symbol, reasoning)
    if alert_id is None:
        return "Price level alert limit reached (max 20). Remove or wait for existing alerts to trigger."

    await _record_action(
        deps, action="add_price_level_alert",
        alert_id=alert_id,
        price=price,
        reasoning=f"{direction} {price} | {reasoning}",
    )

    base = f"Price level alert set: {direction} {price:.2f} (id={alert_id})"

    # Already-met case → fires on next tick (success-path suffix, not a warning)
    latest = deps.exchange._latest_price
    if latest is not None and (
        (direction == "above" and latest >= price)
        or (direction == "below" and latest <= price)
    ):
        return (
            f"{base} — fires on next tick "
            f"(current {latest:.2f} already {direction} {price:.2f})"
        )

    return base


def _lookup_alert(exchange, alert_id: str) -> dict | None:
    """Peek at the alert dict by id without mutating the alert list.

    Used by cancel (to capture reasoning before remove) and update (to
    capture direction + old_price for the success return string before
    the in-place mutation). Returns the full alert dict matching the id,
    or None if no match.
    """
    for alert in exchange.get_price_level_alerts():
        if alert["id"] == alert_id:
            return alert
    return None


async def cancel_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    reasoning: str,
) -> str:
    """Cancel a previously-set price level alert by its ID.

    Idempotent: if the alert is no longer active (already triggered or
    removed via close-fill auto-clear), returns ok with an
    'Alert {id} no longer active (already triggered or removed)' note
    rather than emitting a business error. Format-invalid IDs and
    unexpected internal exceptions still reject explicitly.

    Args:
        alert_id: 8-char hex id returned by add_price_level_alert.
        reasoning: brief rationale for the cancel (audit-only).
    """
    # 协议层：8-char hex 格式校验（uuid.uuid4()[:8] 生成，[0-9a-f]{8}）
    if not re.fullmatch(r"[0-9a-f]{8}", alert_id):
        note_biz_error("invalid_alert_id_format")
        return (
            f"Invalid alert_id format: {alert_id!r}. Expected 8-char hex "
            f"(e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids."
        )

    # Peek before mutate — captures reasoning for F-A3 success-string suffix.
    alert = _lookup_alert(deps.exchange, alert_id)
    if alert is None:
        # 状态不存在 → idempotent ok with note (spec §3.2, §3.4).
        # Covers both root causes: auto-trigger removal during cascade AND
        # _clear_stale_alerts_for_full_close on position close (PR #27).
        return (
            f"Alert {alert_id} no longer active "
            f"(already triggered or removed)"
        )

    ok = deps.exchange.remove_price_level_alert(alert_id)
    if not ok:
        # Defensive: lookup and remove are both sync, in-cycle; remove failing
        # after a successful lookup would indicate a real invariant violation.
        raise RuntimeError(
            f"remove_price_level_alert returned False for id={alert_id} "
            f"that was just present in lookup — invariant violated"
        )

    await _record_action(
        deps, action="cancel_price_level_alert",
        alert_id=alert_id,
        reasoning=reasoning,
    )
    return (
        f'Price level alert cancelled (id={alert_id}) — '
        f'"{alert["reasoning"]}"'
    )


async def update_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    new_price: float,
    reasoning: str,
) -> str:
    """Update an existing price level alert in place: change its trigger price
    and reasoning. The direction (above/below) cannot change — to flip
    direction, cancel and add a new alert. The alert's id stays the same.

    Args:
        alert_id: 8-char hex id of the existing alert (see get_active_alerts).
        new_price: new trigger price.
        reasoning: new rationale text; overwrites the alert's stored reasoning.
    """
    # Step 1: format validation
    if not re.fullmatch(r"[0-9a-f]{8}", alert_id):
        note_biz_error("invalid_alert_id_format")
        return (
            f"Invalid alert_id format: {alert_id!r}. Expected 8-char hex "
            f"(e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids."
        )

    # Step 2: lookup — capture direction + old_price for the success return.
    # (new_reasoning is the caller's arg; old_reasoning is not needed.)
    alert = _lookup_alert(deps.exchange, alert_id)
    if alert is None:
        note_biz_error("alert_not_found")
        return (
            f"Alert {alert_id} not found. "
            f"To create a new alert, use add_price_level_alert."
        )
    direction = alert["direction"]
    old_price = alert["price"]

    # Step 3: in-place update via the new BaseExchange method
    ok = deps.exchange.update_price_level_alert(alert_id, new_price, reasoning)
    if not ok:
        # Defensive: lookup just succeeded; in-place update should not fail.
        raise RuntimeError(
            f"update_price_level_alert returned False for id={alert_id} "
            f"that was just present in lookup — invariant violated"
        )

    # Step 4: audit row — single alert_id; reasoning records the move
    await _record_action(
        deps, action="update_price_level_alert",
        alert_id=alert_id,
        reasoning=f"price {old_price:.2f} → {new_price:.2f} | {reasoning}",
    )

    # Step 5: success return — state-only (reasoning normalized to head args per spec §3.6)
    return (
        f"Price level alert updated (id={alert_id}): "
        f"{direction} {old_price:.2f} → {new_price:.2f}"
    )


async def set_next_wake(
    deps: TradingDeps,
    minutes: int,
    reasoning: str,
) -> str:
    """See trader.py wrapper docstring."""
    if deps.set_next_wake_fn is None:
        return "Dynamic wake not available"

    if minutes < deps.wake_min_minutes:
        return (
            f"Cannot set wake to {minutes} min: "
            f"below wake_min={deps.wake_min_minutes} min."
        )
    if minutes > deps.wake_max_minutes:
        return (
            f"Cannot set wake to {minutes} min: "
            f"exceeds wake_max={deps.wake_max_minutes} min for this session."
        )

    deps.set_next_wake_fn(minutes)
    await _record_action(
        deps, action="set_next_wake",
        reasoning=f"interval={minutes}min | {reasoning}",
    )
    return f"Next wake set to {minutes} min"


async def set_next_wake_at(
    deps: TradingDeps,
    target_time: str,
    reasoning: str,
) -> str:
    """See trader.py wrapper docstring."""
    if deps.set_next_wake_fn is None:
        return "Dynamic wake not available"

    # 1. Format validation — strict HH:MM (00:00 - 23:59)
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", target_time)
    if not match:
        return (
            f"Invalid target_time format: {target_time!r}. "
            f"Expected 'HH:MM' UTC with 2-digit hour and minute "
            f"(e.g., '10:37' or '03:05')."
        )
    h, m = int(match[1]), int(match[2])

    # 2. Future inference — today HH:MM if still ahead, else tomorrow HH:MM
    now_utc = datetime.now(timezone.utc)
    candidate = now_utc.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now_utc:
        candidate += timedelta(days=1)

    # 3. Delta + bound validation — ceil to avoid waking before target moment
    delta_seconds = (candidate - now_utc).total_seconds()
    delta_minutes = math.ceil(delta_seconds / 60)
    candidate_label = candidate.strftime("%Y-%m-%d %H:%M")

    if delta_minutes < deps.wake_min_minutes:
        return (
            f"Cannot wake at {target_time} UTC: nearest future "
            f"{candidate_label} UTC (in {delta_minutes} min) "
            f"below wake_min={deps.wake_min_minutes} min."
        )
    if delta_minutes > deps.wake_max_minutes:
        return (
            f"Cannot wake at {target_time} UTC: nearest future "
            f"{candidate_label} UTC (in {delta_minutes} min) "
            f"exceeds wake_max={deps.wake_max_minutes} min for this session."
        )

    # 4. Success
    deps.set_next_wake_fn(delta_minutes)
    await _record_action(
        deps, action="set_next_wake_at",
        reasoning=(
            f"target={target_time} UTC resolves_to={candidate_label} UTC "
            f"interval={delta_minutes}min | {reasoning}"
        ),
    )
    return f"Next wake set for {candidate_label} UTC (in {delta_minutes} min)"


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
    contract_size = await deps.exchange.get_contract_size(deps.symbol)
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    raw_quantity = (usdt_amount * actual_leverage) / (price * contract_size)
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

    # Limit-as-close hook: if a position exists and this limit is the reverse
    # direction, the fill will be a close — register the position's entry_price
    # so OKX _parse_fill_event can attach it to the FillEvent (sim path captures
    # at fill time, no-op there).
    if positions and positions[0].side != side:
        deps.exchange.register_close_order_entry(order.id, positions[0].entry_price)

    await _record_action(
        deps, action="place_limit_order", order_id=order.id,
        side=side, price=price, reasoning=reasoning,
    )

    leverage_suffix = ""
    if positions and leverage != actual_leverage:
        leverage_suffix = f" (matched existing position; requested {leverage}x ignored)"
    notional = price * quantity * contract_size
    est_entry_fee = notional * deps.fee_rate
    return (
        f"Limit order placed: {side} {quantity:.6f} @ {price:.2f}, "
        f"{actual_leverage}x{leverage_suffix} | ID: {order.id}\n"
        f"Est. entry fee if filled: ~-{est_entry_fee:.2f} USDT "
        f"(notional ~{notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
        "Note: This tool only submits the order — it does not mean the order has been filled."
    )


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
        # 状态不存在 → idempotent ok with note (principle 6, aligned with
        # cancel_price_level_alert R2-Next-E PR #47). Order may have filled
        # or been cancelled by another path between the agent's perception
        # and this call.
        return (
            f"Order {order_id} no longer active "
            f"(already filled or cancelled)"
        )

    if target.order_type == "market":
        return "Cannot cancel market orders"

    await deps.exchange.cancel_order(order_id, deps.symbol, is_algo=target.is_algo)

    await _record_action(
        deps, action="cancel_order", order_id=order_id,
        side=target.side, price=target.price, reasoning=reasoning,
    )

    price_str = f" @ {target.price:.2f}" if target.price is not None else ""
    return f"Order cancelled: {target.order_type} {target.side} {target.amount:.6f}{price_str} | ID: {order_id}"
