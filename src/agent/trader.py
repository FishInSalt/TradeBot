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
    async def get_exchange_announcements(
        ctx: RunContext[TradingDeps],
        lookback_hours: int = 24,
    ) -> str:
        """Get recent exchange announcements (maintenance, delistings, parameter changes).

        Call before trading or when investigating unexpected price moves. Output
        ~50-200 tokens (often empty when no recent announcements).

        Args:
            lookback_hours: how far back to scan for announcements (default 24h).
        """
        from src.agent.tools_perception import get_exchange_announcements as _impl

        return await _impl(ctx.deps, lookback_hours)

    @agent.tool
    async def get_macro_calendar(
        ctx: RunContext[TradingDeps],
        lookahead_hours: int = 12,
    ) -> str:
        """Get upcoming macro events (FOMC, CPI, NFP) with impact level.

        Call before trading or when assessing forward-looking risk. Macro calendar
        covers the current week only — Friday evening / weekend calls may miss
        next week's early events. Output ~50-250 tokens (often empty when no
        scheduled events in window).

        Args:
            lookahead_hours: how far ahead to scan for events (default 12h).
        """
        from src.agent.tools_perception import get_macro_calendar as _impl

        return await _impl(ctx.deps, lookahead_hours)

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

    @agent.tool
    async def get_order_book(ctx: RunContext[TradingDeps], depth: int = 20) -> str:
        """Return top-N order book depth with concentrated-level breakdown.

        Args:
            depth: Levels per side to fetch. Default 20.

        Returns:
            str: Multi-line fact-only text (best bid/ask + cumulative depth + bid share + concentrated levels).

        Degradation: "Order book ({symbol}): insufficient data (requested depth X, got Y)" if book is empty/short;
        "Order book ({symbol}): temporarily unavailable" on service failure.
        """
        from src.agent.tools_perception import get_order_book as _impl

        return await _impl(ctx.deps, depth=depth)

    @agent.tool
    async def get_recent_trades(ctx: RunContext[TradingDeps], window_seconds: int = 300) -> str:
        """Return taker-flow bias and rhythm over a recent time window via 5 time-buckets.

        Args:
            window_seconds: Observation window in seconds. Default 300 (5 min).

        Returns:
            str: 5-bucket breakdown + Total + trade count + avg size.

        Degradation: "Recent trades ({symbol}): no trades in last {window_seconds}s" if cold market;
        "Recent trades ({symbol}): temporarily unavailable" on service failure. Heavy windows
        may annotate Total with "partial coverage" footnote.
        """
        from src.agent.tools_perception import get_recent_trades as _impl

        return await _impl(ctx.deps, window_seconds=window_seconds)

    @agent.tool
    async def get_multi_timeframe_snapshot(ctx: RunContext[TradingDeps], tfs: list[str] | None = None) -> str:
        """Quick multi-timeframe scan: momentum | structure | volatility | range position.

        Args:
            tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"]. 1w/1M are supported but non-default.

        Returns:
            str: 4-column row per TF + Columns header.

        Degradation: per-TF "insufficient data (need N candles, got M)" or "temporarily unavailable";
        overall "Multi-TF snapshot ({symbol}): temporarily unavailable" only if ALL TFs fail or ticker fetch fails.
        """
        from src.agent.tools_perception import get_multi_timeframe_snapshot as _impl

        return await _impl(ctx.deps, tfs=tfs)

    @agent.tool
    async def get_price_pivots(ctx: RunContext[TradingDeps]) -> str:
        """Show structural support/resistance: last 100 main-TF swing pivots
        (Williams fractal N=5) + prior daily/weekly/monthly H/L. Fact-only.
        Returns levels grouped by above/below current price, sorted by
        absolute distance. Swing rows annotate 'N bars ago'; prior rows
        label the period (Daily / Weekly / Monthly). See tool implementation
        for full degradation semantics.
        """
        from src.agent.tools_perception import get_price_pivots as _impl

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


# REGISTERED_TOOL_NAMES: 与 `@agent.tool` 装饰顺序保持一致（感知 → 执行 → memory）。
# 供 scheduler 日志、scripts/tool_call_summary.py 脚本、漂移防护测试统一引用。
# 漂移防护：tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools
# 用 agent._function_toolset.tools 对照本常量。加新 tool 必须同时更新此列表。
REGISTERED_TOOL_NAMES: list[str] = [
    # --- 感知 (20) ---
    "get_market_data",
    "get_position",
    "get_account_balance",
    "get_open_orders",
    "get_trade_journal",
    "get_memories",
    "get_active_alerts",
    "get_performance",
    "get_market_news",
    "get_exchange_announcements",
    "get_macro_calendar",
    "get_derivatives_data",
    "get_higher_timeframe_view",
    "get_macro_context",
    "get_etf_flows",
    "get_stablecoin_supply",
    "get_order_book",
    "get_recent_trades",
    "get_multi_timeframe_snapshot",
    "get_price_pivots",
    # --- 执行 (10) ---
    "open_position",
    "close_position",
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "set_price_alert",
    "cancel_order",
    "add_price_level_alert",
    "set_next_wake",
    "place_limit_order",
    # --- memory (1) ---
    "save_memory",
]
