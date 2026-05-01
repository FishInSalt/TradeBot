"""R2-7 §6.1 + §6.2: cycle 决策时刻 capture helpers.

两 helper:
- _capture_trigger_context: trigger metadata DB 端镜像 (dataclass → JSON dict)
- _capture_state_snapshot: 决策时系统层面客观快照 (持仓 / 余额 / 现价 / pending / alerts)

Best-effort 容错: 异常 → 字段 None + _errors 标记 + log warning + cycle 继续.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.integrations.exchange.base import PriceLevelAlertInfo
from src.services.price_alert import AlertInfo

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)


def _capture_trigger_context(cycle_id: str, trigger_type: str, context) -> dict | None:
    """Capture trigger metadata for DB. Best-effort: any exception → None.

    Args:
        cycle_id: 当前 cycle_id (用于日志反查)
        trigger_type: scheduled / conditional / alert
        context: trigger 携带 metadata (FillEvent / PriceLevelAlertInfo / AlertInfo / None)
    """
    try:
        if trigger_type == "scheduled":
            return {"type": "scheduled_tick"}
        if trigger_type == "conditional" and context is not None:
            # FillEvent (base.py:270-282): 11 字段全保留 + type
            return {
                "type": "fill",
                "trigger_reason": context.trigger_reason,
                "symbol": context.symbol,
                "side": context.side,
                "position_side": context.position_side,
                "amount": context.amount,
                "fill_price": context.fill_price,
                "fee": context.fee,
                "pnl": context.pnl,
                "order_id": context.order_id,
                "timestamp": context.timestamp,
                "is_full_close": context.is_full_close,
            }
        if trigger_type == "alert" and context is not None:
            if isinstance(context, PriceLevelAlertInfo):
                # base.py:285-292: 6 字段 + type
                return {
                    "type": "price_level_alert",
                    "symbol": context.symbol,
                    "current_price": context.current_price,
                    "target_price": context.target_price,
                    "direction": context.direction,
                    "reasoning": context.reasoning,
                    "timestamp": context.timestamp,
                }
            if isinstance(context, AlertInfo):
                # src/services/price_alert.py:8-15: 6 字段 + type
                return {
                    "type": "percentage_alert",
                    "symbol": context.symbol,
                    "current_price": context.current_price,
                    "reference_price": context.reference_price,
                    "change_pct": context.change_pct,
                    "window_minutes": context.window_minutes,
                    "timestamp": context.timestamp,
                }
        return None
    except Exception as e:
        logger.warning(
            "trigger_context capture failed (cycle_id=%s, trigger_type=%s, context_type=%s): %s",
            cycle_id, trigger_type, type(context).__name__, e,
        )
        return None


async def _capture_state_snapshot(cycle_id: str, deps: TradingDeps) -> dict:
    """Capture system-side objective state at decision time. Best-effort per-field.

    **Contract**: 永不 raise，永不 return None — 即使所有 5 个 fetch 全失败也返回完整 dict
    (字段值为 None / [] + _errors 列出 5 个 fail 原因 + _cycle_id 仍填)。

    存储层契约 (cli/app.py 写入)：调用方对 state_snapshot 字段无条件做 json.dumps，
    DB state_snapshot 列实际**永非 NULL** (虽然 schema 声明 nullable=True)。schema
    nullable 是 R2-7 数据驱动 evolution 哲学的占位，未来若加 schema validation 可
    收紧 NOT NULL。当前消费者 (R2-8 display / W2 SQL pivot) 应假设非 NULL。

    Args:
        cycle_id: 当前 cycle_id (用于日志反查)
        deps: TradingDeps 含 exchange / market_data / symbol

    Returns:
        dict with keys: position / balance / market / pending_orders / active_alerts
        + _errors (list of `{name}_fetch_failed: {ExceptionType}`) + _cycle_id.
        Per-field exception → field = None / [] (绝不抛异常).
    """
    snapshot: dict = {
        "position": None,
        "balance": None,
        "market": None,
        "pending_orders": [],
        "active_alerts": [],
        "_errors": [],
        "_cycle_id": cycle_id,
    }

    # 1. position (best-effort) — Position dataclass (base.py:79-88)
    try:
        positions = await deps.exchange.fetch_positions(deps.symbol)
        if positions:
            p = positions[0]
            notional = (
                p.entry_price * p.contracts
                if p.entry_price > 0 and p.contracts > 0
                else 0.0
            )
            pnl_pct = (p.unrealized_pnl / notional * 100) if notional > 0 else None
            snapshot["position"] = {
                "symbol": p.symbol,
                "side": p.side,
                "contracts": p.contracts,
                "entry_price": p.entry_price,
                "unrealized_pnl": p.unrealized_pnl,
                "leverage": p.leverage,
                "liquidation_price": p.liquidation_price,
                "pnl_pct": pnl_pct,
            }
    except Exception as e:
        msg = f"position_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 2. balance (best-effort) — Balance dataclass (base.py:49-53)
    try:
        balance = await deps.exchange.fetch_balance()
        snapshot["balance"] = {
            "total_usdt": balance.total_usdt,
            "free_usdt": balance.free_usdt,
            "used_usdt": balance.used_usdt,
        }
    except Exception as e:
        msg = f"balance_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 3. market (best-effort) — Ticker dataclass (base.py:13-22)
    try:
        ticker = await deps.market_data.get_ticker(deps.symbol)
        snapshot["market"] = {
            "ticker_last": ticker.last,
            "ticker_timestamp": ticker.timestamp,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        msg = f"ticker_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 4. pending orders (best-effort) — Order dataclass + R2-7 §4.7 trigger_price
    try:
        orders = await deps.exchange.fetch_open_orders(deps.symbol)
        snapshot["pending_orders"] = [
            {
                "id": o.id,
                "order_type": o.order_type,
                "side": o.side,
                "price": o.price,
                "trigger_price": o.trigger_price,
                "amount": o.amount,
                "status": o.status,
                "is_algo": o.is_algo,
            }
            for o in orders
        ]
    except Exception as e:
        msg = f"open_orders_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 5. active alerts (no IO) — single-symbol filter (Issue 6: cycle 是单 symbol 上下文)
    try:
        all_alerts = deps.exchange.get_price_level_alerts()
        snapshot["active_alerts"] = [
            {
                "id": a["id"],
                "direction": a["direction"],
                "price": a["price"],
                "reasoning": a.get("reasoning", ""),
            }
            for a in all_alerts
            if a["symbol"] == deps.symbol
        ]
    except Exception as e:
        msg = f"alerts_read_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    return snapshot
