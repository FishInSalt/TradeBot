from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext

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
    db_engine: object | None = None  # AsyncEngine, typed as object to avoid circular import
    approval_gate: object | None = None  # ApprovalGate instance
    approval_enabled: bool = True
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: Callable[[int], None] | None = None
    initial_balance: float = 10000.0
    metrics: object | None = None  # MetricsService, typed as object to avoid circular import


def create_trader_agent(
    model: str, persona_config: PersonaConfig
) -> Agent[TradingDeps, str]:
    system_prompt = generate_system_prompt(persona_config)
    agent = Agent(model, deps_type=TradingDeps, output_type=str, instructions=system_prompt)

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
        """Get account balance."""
        from src.agent.tools_perception import get_account_balance as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_open_orders(ctx: RunContext[TradingDeps]) -> str:
        """Get all pending orders (market awaiting fill, limit, stop loss, take profit)."""
        from src.agent.tools_perception import get_open_orders as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_trade_journal(ctx: RunContext[TradingDeps]) -> str:
        """Get trade journal — agent's decision timeline with fill details."""
        from src.agent.tools_perception import get_trade_journal as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_memories(ctx: RunContext[TradingDeps]) -> str:
        """Get long-term memories (lessons, patterns, trade reviews)."""
        from src.agent.tools_perception import get_memories as _impl

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
