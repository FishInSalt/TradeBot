from __future__ import annotations

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


def create_trader_agent(
    model: str, persona_config: PersonaConfig
) -> Agent[TradingDeps, str]:
    system_prompt = generate_system_prompt(persona_config)
    agent = Agent(model, deps_type=TradingDeps, output_type=str, instructions=system_prompt)

    # === Perception Tools ===

    @agent.tool
    async def get_market_data(
        ctx: RunContext[TradingDeps], symbol: str, timeframe: str
    ) -> str:
        """Get current market data with technical indicators."""
        from src.agent.tools_perception import get_market_data as _impl

        return await _impl(ctx.deps, symbol, timeframe)

    @agent.tool
    async def get_position(ctx: RunContext[TradingDeps], symbol: str) -> str:
        """Get current open positions."""
        from src.agent.tools_perception import get_position as _impl

        return await _impl(ctx.deps, symbol)

    @agent.tool
    async def get_account_balance(ctx: RunContext[TradingDeps]) -> str:
        """Get account balance."""
        from src.agent.tools_perception import get_account_balance as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def get_open_orders(ctx: RunContext[TradingDeps]) -> str:
        """Get pending conditional orders (stop loss, take profit)."""
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
        cooldown_minutes: int,
        reasoning: str,
    ) -> str:
        """Adjust price alert parameters. threshold_pct: 0.5-50%, window_minutes: 1-60, cooldown_minutes: 1-120. Always provide reasoning."""
        from src.agent.tools_execution import set_price_alert as _impl

        return await _impl(ctx.deps, threshold_pct, window_minutes, cooldown_minutes, reasoning=reasoning)

    # === Memory Tools ===

    @agent.tool
    async def save_memory(
        ctx: RunContext[TradingDeps], category: str, content: str, importance: float = 0.5
    ) -> str:
        """Save a learning or observation to long-term memory. category: trade_review/market_pattern/lesson. importance: 0-1."""
        from src.agent.tools_memory import save_memory as _impl

        return await _impl(ctx.deps, category, content, importance)

    return agent
