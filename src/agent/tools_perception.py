from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps


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


async def get_trade_history(deps: TradingDeps) -> str:
    """Get trade history and memories."""
    return await deps.memory.format_for_prompt()
