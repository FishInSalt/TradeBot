from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)


async def get_market_data(deps: TradingDeps, symbol: str, timeframe: str) -> str:
    """Get current market data with technical indicators."""
    ticker = await deps.market_data.get_ticker(symbol)
    df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=100)
    indicators = deps.technical.compute_indicators(df)
    indicators_text = deps.technical.format_for_llm(indicators, current_price=ticker.last)
    return (
        f"Symbol: {symbol}\n"
        f"Price: {ticker.last:.2f} | Bid: {ticker.bid:.2f} | Ask: {ticker.ask:.2f}\n"
        f"24h High: {ticker.high:.2f} | Low: {ticker.low:.2f} | Volume: {ticker.base_volume:.2f}\n\n"
        f"Technical Indicators ({timeframe}):\n{indicators_text}"
    )


async def get_position(deps: TradingDeps, symbol: str) -> str:
    """Get current open positions."""
    positions = await deps.exchange.fetch_positions(symbol)
    if not positions:
        return "No open positions."
    lines = ["Current Positions:"]
    for p in positions:
        lines.append(
            f"  {p.side.upper()} {p.contracts} contracts @ {p.entry_price:.2f} "
            f"| Leverage: {p.leverage}x | PnL: {p.unrealized_pnl:.2f} USDT"
            f"{'| Liq: ' + f'{p.liquidation_price:.2f}' if p.liquidation_price else ''}"
        )
    return "\n".join(lines)


async def get_account_balance(deps: TradingDeps) -> str:
    """Get account balance."""
    balance = await deps.exchange.fetch_balance()
    return (
        f"Account Balance:\n"
        f"  Total: {balance.total_usdt:.2f} USDT\n"
        f"  Free: {balance.free_usdt:.2f} USDT\n"
        f"  Used: {balance.used_usdt:.2f} USDT"
    )


async def get_memories(deps: TradingDeps) -> str:
    """Get long-term memories (lessons, patterns, trade reviews)."""
    return await deps.memory.format_for_prompt()


async def get_open_orders(deps: TradingDeps) -> str:
    """Get pending conditional orders (stop loss, take profit)."""
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return "No pending orders."
    lines = ["Pending Orders:"]
    for o in orders:
        lines.append(f"  {o.order_type.upper()} {o.side} {o.amount} @ {o.price:.2f} | ID: {o.id}")
    return "\n".join(lines)


async def get_trade_journal(deps: TradingDeps, limit: int = 20) -> str:
    """Get trade journal — agent's decision timeline with fill details."""
    if deps.db_engine is None:
        return "No trade journal entries yet."
    from sqlalchemy import select, desc
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    async with get_session(deps.db_engine) as session:
        result = await session.execute(
            select(TradeAction)
            .where(TradeAction.session_id == deps.session_id)
            .order_by(desc(TradeAction.created_at))
            .limit(limit)
        )
        actions = list(result.scalars().all())

    if not actions:
        return "No trade journal entries yet."

    order_details = {}
    order_ids = list({a.order_id for a in actions if a.order_id})
    for oid in order_ids:
        try:
            order = await deps.exchange.fetch_order(oid, deps.symbol)
            order_details[oid] = order
        except Exception:
            logger.warning("Failed to fetch order %s", oid, exc_info=True)

    lines = ["=== Trade Journal ==="]
    for a in reversed(actions):  # chronological order
        ts = a.created_at.strftime("%m-%d %H:%M")
        line = f"[{ts}] {a.action}"
        if a.side:
            line += f" ({a.side})"
        if a.order_id and a.order_id in order_details:
            od = order_details[a.order_id]
            if od.price:
                line += f" @ {od.price:.2f}"
            if od.fee:
                line += f", fee={od.fee:.4f}"
            line += f" [{od.status}]"
        if a.reasoning:
            line += f"\n  Reasoning: {a.reasoning}"
        lines.append(line)
    return "\n".join(lines)
