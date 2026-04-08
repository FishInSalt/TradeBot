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
    session_id: str = "default"
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
    async def get_trade_history(ctx: RunContext[TradingDeps]) -> str:
        """Get trade history and memories."""
        from src.agent.tools_perception import get_trade_history as _impl

        return await _impl(ctx.deps)

    # === Execution Tools ===

    @agent.tool
    async def open_position(
        ctx: RunContext[TradingDeps],
        side: str,
        position_pct: float,
        leverage: int,
        stop_loss_price: float | None = None,
        take_profit_price: float | None = None,
    ) -> str:
        """Open a new position. side='long' or 'short'. position_pct=% of free balance."""
        from src.agent.tools_execution import open_position as _impl

        return await _impl(
            ctx.deps, side, position_pct, leverage, stop_loss_price, take_profit_price
        )

    @agent.tool
    async def close_position(ctx: RunContext[TradingDeps]) -> str:
        """Close all open positions."""
        from src.agent.tools_execution import close_position as _impl

        return await _impl(ctx.deps)

    @agent.tool
    async def set_stop_loss(ctx: RunContext[TradingDeps], price: float) -> str:
        """Set stop loss on current position."""
        from src.agent.tools_execution import set_stop_loss as _impl

        return await _impl(ctx.deps, price)

    @agent.tool
    async def set_take_profit(ctx: RunContext[TradingDeps], price: float) -> str:
        """Set take profit on current position."""
        from src.agent.tools_execution import set_take_profit as _impl

        return await _impl(ctx.deps, price)

    @agent.tool
    async def adjust_leverage(ctx: RunContext[TradingDeps], leverage: int) -> str:
        """Adjust leverage."""
        from src.agent.tools_execution import adjust_leverage as _impl

        return await _impl(ctx.deps, leverage)

    return agent
