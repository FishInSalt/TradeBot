from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic_ai import Agent, RunContext
from sqlalchemy.ext.asyncio import AsyncEngine

from src.agent.memory import MemoryService
from src.agent.persona import generate_system_prompt
from src.config import PersonaConfig
from src.integrations.exchange.base import BaseExchange
from src.integrations.market_data import MarketDataService
from src.services.technical import TechnicalAnalysisService


@dataclass
class TradingDeps:
    symbol: str
    timeframe: str
    market_data: MarketDataService
    exchange: BaseExchange
    technical: TechnicalAnalysisService
    memory: MemoryService
    session_id: str  # UUID from sessions table, must be explicitly set
    db_engine: AsyncEngine | None = None
    approval_gate: object | None = None  # ApprovalGate instance
    approval_enabled: bool = True
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: Callable[[int], None] | None = None
    initial_balance: float = 10000.0
    metrics: object | None = None  # MetricsService, typed as object to avoid circular import
    news: object | None = None  # NewsService, typed as object to avoid circular import
    macro: object | None = None  # MacroService; typed as object to avoid circular import
    crypto_etf: object | None = None  # CryptoEtfService; typed as object to avoid circular import
    onchain: object | None = None  # OnchainService; typed as object to avoid circular import
    cycle_id: str | None = None  # Mutated by run_agent_cycle before agent.run(); see §3.3 of spec


def create_trader_agent(
    model: str, persona_config: PersonaConfig
) -> Agent[TradingDeps, str]:
    # 函数级懒加载 — 与现有 26 个 tool 的懒加载风格一致（技术上非必需：
    # recorder 侧 TYPE_CHECKING + 字符串前向引用已足以破环）
    from src.services.tool_call_recorder import ToolCallRecorder

    system_prompt = generate_system_prompt(persona_config)
    agent = Agent(
        model,
        deps_type=TradingDeps,
        output_type=str,
        instructions=system_prompt,
        capabilities=[ToolCallRecorder()],
    )

    # === Perception Tools ===

    @agent.tool
    async def get_market_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
        timeframe: str | None = None,
        candle_count: int = 50,
    ) -> str:
        """Get market data: ticker, technical indicators, market context, and recent candles.
        candle_count=20 for quick check or secondary timeframes, 50 for detailed analysis.
        Default 50. Values above 50 may be capped by exchange API limits.
        Total output ~1000-1200 tokens (K-line table ~750-800 + indicators + context).
        symbol and timeframe default to session config."""
        from src.agent.tools_perception import get_market_data as _impl

        return await _impl(ctx.deps, symbol, timeframe, candle_count)

    @agent.tool
    async def get_position(ctx: RunContext[TradingDeps], symbol: str | None = None) -> str:
        """Get current open position with risk context (PnL %, liquidation distance, duration)."""
        from src.agent.tools_perception import get_position as _impl

        return await _impl(ctx.deps, symbol)

    @agent.tool
    async def get_account_balance(ctx: RunContext[TradingDeps]) -> str:
        """Get account balance with return on initial capital."""
        from src.agent.tools_perception import get_account_balance as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_open_orders(ctx: RunContext[TradingDeps]) -> str:
        """Get all pending orders with distance from current price."""
        from src.agent.tools_perception import get_open_orders as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_trade_journal(ctx: RunContext[TradingDeps]) -> str:
        """Get trade journal — decision timeline with quick stats summary. Use for reviewing recent decisions and their outcomes."""
        from src.agent.tools_perception import get_trade_journal as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_memories(ctx: RunContext[TradingDeps]) -> str:
        """Get long-term memories (lessons, patterns, trade reviews)."""
        from src.agent.tools_perception import get_memories as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_active_alerts(ctx: RunContext[TradingDeps]) -> str:
        """Get current alert configuration: volatility alert params and active price level alerts."""
        from src.agent.tools_perception import get_active_alerts as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_performance(ctx: RunContext[TradingDeps]) -> str:
        """Get detailed trading performance statistics. Use for reviewing overall results and evaluating strategy effectiveness."""
        from src.agent.tools_perception import get_performance as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_market_news(
        ctx: RunContext[TradingDeps],
        news_filter: Literal["positive", "negative", "neutral"] | None = None,
    ) -> str:
        """Get recent crypto news headlines and market sentiment.
        news_filter: 'positive', 'negative', 'neutral'. Default: no filter (latest mix).
        Returns up to 10 headlines total (up to 5 symbol-specific, remainder general crypto); total may be fewer if upstream has limited recent posts. Plus Fear & Greed Index.
        Output ~500-700 tokens."""
        from src.agent.tools_perception import get_market_news as _impl

        return await _impl(ctx.deps, news_filter)

    @agent.tool
    async def get_critical_alerts(
        ctx: RunContext[TradingDeps],
        lookback_hours: int = 24,
        lookahead_hours: int = 12,
    ) -> str:
        """Get critical alerts: exchange announcements and upcoming macro events.
        lookback_hours: how far back to check announcements (default 24h).
        lookahead_hours: how far ahead to check macro events (default 12h).
        Output ~100-400 tokens (often empty when no relevant events are scheduled)."""
        from src.agent.tools_perception import get_critical_alerts as _impl

        return await _impl(ctx.deps, lookback_hours, lookahead_hours)

    @agent.tool
    async def get_derivatives_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
    ) -> str:
        """Get derivatives market data: funding rate, open interest, long/short ratio.
        When symbol is None, uses the currently traded pair.
        Output ~150-250 tokens."""
        from src.agent.tools_perception import get_derivatives_data as _impl

        return await _impl(ctx.deps, symbol)

    @agent.tool
    async def get_higher_timeframe_view(
        ctx: RunContext[TradingDeps],
        timeframe: Literal["4h", "1d", "1w", "1M"],
    ) -> str:
        """Get long-period structure: MA50/100/200 distances and range position.
        timeframe: '4h' bridges LTF and 1d; '1d'/'1w'/'1M' for swing/position context.
        Output ~250 tokens. No default — explicitly pick the timeframe you need."""
        from src.agent.tools_perception import get_higher_timeframe_view as _impl

        return await _impl(ctx.deps, timeframe)

    @agent.tool
    async def get_macro_context(ctx: RunContext[TradingDeps]) -> str:
        """Get cross-market macro snapshot: BTC/ETH dominance, Total Crypto Mcap, USD
        Trade-Weighted Index (FRED DTWEXBGS; NOT ICE DXY), VIX, 10Y Treasury, 2s10s spread,
        10Y inflation expectation, and SPY/QQQ. Output ~200 tokens."""
        from src.agent.tools_perception import get_macro_context as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_etf_flows(ctx: RunContext[TradingDeps], days: int = 7) -> str:
        """Get US BTC + ETH spot ETF daily net flows + cumulative AUM for the past `days`
        trading days (1-14, default 7). Today's value may be revised T+1.
        Output ~300 tokens."""
        from src.agent.tools_perception import get_etf_flows as _impl

        return await _impl(ctx.deps, days)

    @agent.tool
    async def get_stablecoin_supply(ctx: RunContext[TradingDeps]) -> str:
        """Get USDT + USDC current total supply and 7-day change.
        Data sourced from DefiLlama (on-chain circulating supply). Output ~80 tokens."""
        from src.agent.tools_perception import get_stablecoin_supply as _impl

        return await _impl(ctx.deps)

    # === Execution Tools ===

    @agent.tool
    async def open_position(
        ctx: RunContext[TradingDeps],
        side: str,
        position_pct: float,
        leverage: int,
        reasoning: str,
    ) -> str:
        """Open a new position. side='long' or 'short'. position_pct=% of free balance. Always provide reasoning."""
        from src.agent.tools_execution import open_position as _impl

        return await _impl(ctx.deps, side, position_pct, leverage, reasoning=reasoning)

    @agent.tool
    async def close_position(ctx: RunContext[TradingDeps], reasoning: str) -> str:
        """Close all open positions. Always provide reasoning."""
        from src.agent.tools_execution import close_position as _impl

        return await _impl(ctx.deps, reasoning=reasoning)

    @agent.tool
    async def set_stop_loss(ctx: RunContext[TradingDeps], price: float, reasoning: str) -> str:
        """Set stop loss on current position. Auto-cancels existing stop orders. Always provide reasoning."""
        from src.agent.tools_execution import set_stop_loss as _impl

        return await _impl(ctx.deps, price, reasoning=reasoning)

    @agent.tool
    async def set_take_profit(ctx: RunContext[TradingDeps], price: float, reasoning: str) -> str:
        """Set take profit on current position. Auto-cancels existing TP orders. Always provide reasoning."""
        from src.agent.tools_execution import set_take_profit as _impl

        return await _impl(ctx.deps, price, reasoning=reasoning)

    @agent.tool
    async def adjust_leverage(ctx: RunContext[TradingDeps], leverage: int, reasoning: str) -> str:
        """Adjust leverage. Always provide reasoning."""
        from src.agent.tools_execution import adjust_leverage as _impl

        return await _impl(ctx.deps, leverage, reasoning=reasoning)

    @agent.tool
    async def set_price_alert(
        ctx: RunContext[TradingDeps],
        threshold_pct: float,
        window_minutes: int,
        reasoning: str,
    ) -> str:
        """Adjust price alert parameters. threshold_pct: 0.5-50%, window_minutes: 1-240. Always provide reasoning."""
        from src.agent.tools_execution import set_price_alert as _impl

        return await _impl(ctx.deps, threshold_pct, window_minutes, reasoning=reasoning)

    @agent.tool
    async def cancel_order(ctx: RunContext[TradingDeps], order_id: str, reasoning: str) -> str:
        """Cancel a pending order (limit, stop loss, take profit). Always provide reasoning."""
        from src.agent.tools_execution import cancel_order as _impl

        return await _impl(ctx.deps, order_id, reasoning=reasoning)

    @agent.tool
    async def add_price_level_alert(
        ctx: RunContext[TradingDeps],
        price: float,
        direction: str,
        reasoning: str,
    ) -> str:
        """Set a one-shot price level alert. direction: 'above' (breakout) or 'below' (breakdown). Triggers once then auto-removes. Always provide reasoning."""
        from src.agent.tools_execution import add_price_level_alert as _impl

        return await _impl(ctx.deps, price, direction, reasoning=reasoning)

    @agent.tool
    async def set_next_wake(
        ctx: RunContext[TradingDeps],
        minutes: int,
        reasoning: str,
    ) -> str:
        """Set how soon you want to check the market again (minutes). One-shot: only affects the next wake, then reverts to default. Always provide reasoning."""
        from src.agent.tools_execution import set_next_wake as _impl

        return await _impl(ctx.deps, minutes, reasoning=reasoning)

    @agent.tool
    async def place_limit_order(
        ctx: RunContext[TradingDeps],
        side: str,
        price: float,
        position_pct: float,
        leverage: int,
        reasoning: str,
    ) -> str:
        """Place a limit order at a specific price (e.g., buy at support level). side='long' or 'short'. position_pct=% of free balance. Always provide reasoning."""
        from src.agent.tools_execution import place_limit_order as _impl

        return await _impl(ctx.deps, side, price, position_pct, leverage, reasoning=reasoning)

    # === Memory Tools ===

    @agent.tool
    async def save_memory(
        ctx: RunContext[TradingDeps], category: str, content: str, importance: float = 0.5
    ) -> str:
        """Save a learning or observation to long-term memory. category: trade_review/market_pattern/lesson. importance: 0-1."""
        from src.agent.tools_memory import save_memory as _impl

        return await _impl(ctx.deps, category, content, importance)

    return agent
