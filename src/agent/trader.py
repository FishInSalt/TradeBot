from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic_ai import Agent, RunContext
from sqlalchemy.ext.asyncio import AsyncEngine

from src.agent.memory import MemoryService
from src.agent.persona import generate_system_prompt, RuntimeConfig
from src.cli.approval import ApprovalGate
from src.config import PersonaConfig
from src.integrations.crypto_etf.service import CryptoEtfService
from src.integrations.exchange.base import BaseExchange
from src.integrations.macro.service import MacroService
from src.integrations.market_data import MarketDataService
from src.integrations.news.service import NewsService
from src.integrations.onchain.service import OnchainService
from src.services.metrics import MetricsService
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
    approval_gate: ApprovalGate | None = None
    approval_enabled: bool = True
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: Callable[[int], None] | None = None
    initial_balance: float = 10000.0
    metrics: MetricsService | None = None
    news: NewsService | None = None
    macro: MacroService | None = None
    crypto_etf: CryptoEtfService | None = None
    onchain: OnchainService | None = None
    cycle_id: str | None = None  # Mutated by run_agent_cycle before agent.run(); see §3.3 of spec


def create_trader_agent(
    model: str,
    persona_config: PersonaConfig,
    runtime: RuntimeConfig | None = None,
) -> Agent[TradingDeps, str]:
    # 函数级懒加载 — 与现有 26 个 tool 的懒加载风格一致（技术上非必需：
    # recorder 侧 TYPE_CHECKING + 字符串前向引用已足以破环）
    from src.services.tool_call_recorder import ToolCallRecorder
    from src.services.model_manager import get_optimal_settings

    system_prompt = generate_system_prompt(persona_config, runtime)
    # model-specific 配置由 model_manager.get_optimal_settings() 统一管理，
    # trader 不感知具体 provider/model 细节，仅按 name 查表。
    # model 入参可能是 KnownModelName 字符串 (tests) 或 pydantic-ai Model 对象 (prod);
    # .model_name 是 Model 基类公共属性，缺失即 AttributeError（fail-loud，避免 silent skip）。
    model_name = model if isinstance(model, str) else model.model_name
    agent = Agent(
        model,
        deps_type=TradingDeps,
        output_type=str,
        instructions=system_prompt,
        capabilities=[ToolCallRecorder()],
        model_settings=get_optimal_settings(model_name),
    )

    # Iter 5 D: 启用 google docstring 显式声明 + 强制 Args 完整性。
    # require_parameter_descriptions=True 在 tool 加载时校验，缺 Args 立即 startup fail。
    # 用 def 而非 functools.partial — partial 丢失 Agent.tool 的 overload 信息，
    # IDE static type checker 会把 @tool 标红；def 让 pyright 看到清晰的装饰器签名。
    def tool(func):
        return agent.tool(
            docstring_format="google",
            require_parameter_descriptions=True,
        )(func)

    # === Perception Tools ===

    @tool
    async def get_market_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
        timeframe: str | None = None,
        candle_count: int = 50,
    ) -> str:
        """Get market data: ticker, technical indicators, market context, and recent candles.

        Use multiple timeframes to build conviction before acting (e.g., "1h" for
        the bigger picture, "5m" for entry timing). Pass candle_count=20 for
        secondary timeframes to save tokens.

        Total output ~1000-1200 tokens (K-line table ~750-800 + indicators + context).

        Args:
            symbol: trading symbol; None defaults to session symbol.
            timeframe: candle timeframe (e.g., '5m', '1h', '4h', '1d'); None defaults to session timeframe.
            candle_count: number of candles to fetch (default 50). Use 20 for quick checks
                or secondary timeframes; 50 for detailed analysis. Values above 50 may be
                capped by exchange API limits.
        """
        from src.agent.tools_perception import get_market_data as _impl

        return await _impl(ctx.deps, symbol, timeframe, candle_count)

    @tool
    async def get_position(ctx: RunContext[TradingDeps], symbol: str | None = None) -> str:
        """Get current position details with risk exposure context.

        Includes Risk exposure (notional / margin / liquidation distance in
        ATR(1h) multiples — 1h is the fixed baseline regardless of session
        trading style) and Exit orders section (SL/TP distances from both
        entry and current). Useful both when opening and during ongoing
        position management.

        Args:
            symbol: trading symbol (defaults to session symbol).
        """
        from src.agent.tools_perception import get_position as _impl

        return await _impl(ctx.deps, symbol)

    @tool
    async def get_account_balance(ctx: RunContext[TradingDeps]) -> str:
        """Get account balance with return on initial capital.

        Output reports total equity, free margin, used margin, and percentage
        return on initial capital — useful for sizing decisions and risk checks.
        """
        from src.agent.tools_perception import get_account_balance as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_open_orders(ctx: RunContext[TradingDeps]) -> str:
        """Get all pending orders with distance from current price.

        Lists limit orders, stop loss, and take profit orders, each with their
        price level and distance from current. OCO-paired orders (sharing an
        algoId on OKX) render with `[OCO]` tag. Useful before placing new
        orders or when reviewing exposure.
        """
        from src.agent.tools_perception import get_open_orders as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_trade_journal(ctx: RunContext[TradingDeps]) -> str:
        """Get the trade journal — decision timeline with quick stats summary.

        Use for reviewing recent decisions and their outcomes — pair with
        get_performance for the quantitative view of the same period.
        """
        from src.agent.tools_perception import get_trade_journal as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_memories(ctx: RunContext[TradingDeps]) -> str:
        """Get long-term memories (lessons, patterns, trade reviews).

        Check past memories before making decisions to avoid repeating mistakes
        and apply pattern recognitions that proved correct previously.
        """
        from src.agent.tools_perception import get_memories as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_active_alerts(ctx: RunContext[TradingDeps]) -> str:
        """Get current alert configuration.

        Reports volatility alert parameters (threshold % + time window) and
        active price level alerts. Useful when reviewing or adjusting your
        alert setup.
        """
        from src.agent.tools_perception import get_active_alerts as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_performance(ctx: RunContext[TradingDeps]) -> str:
        """Get quantitative trading performance statistics.

        Reports return, win rate, drawdown, profit factor, and other
        quantitative metrics. Use for evaluating strategy effectiveness
        across the session — pair with get_trade_journal for decision
        pattern review.
        """
        from src.agent.tools_perception import get_performance as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_market_news(
        ctx: RunContext[TradingDeps],
        news_filter: Literal["positive", "negative", "neutral"] | None = None,
    ) -> str:
        """Get recent crypto news headlines + Fear & Greed Index (0 = max fear, 100 = max greed).

        Returns up to 10 headlines total (up to 5 symbol-specific, remainder
        general crypto); total may be fewer if upstream has limited recent posts.
        Usually call without news_filter; use 'positive' / 'negative' / 'neutral'
        when you want a specific sentiment lens. Output ~500-700 tokens.

        Args:
            news_filter: 'positive', 'negative', 'neutral', or None for latest mix.
        """
        from src.agent.tools_perception import get_market_news as _impl

        return await _impl(ctx.deps, news_filter)

    @tool
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

    @tool
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

    @tool
    async def get_derivatives_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
    ) -> str:
        """Get derivatives market data: funding rate, open interest, long/short ratio.

        Positive funding rate means longs pay shorts; negative means shorts pay
        longs (settlement interval varies by contract — see next settlement time
        in output). Open interest is total outstanding contracts. Long/short
        ratio is the ratio of long vs short account positions. Output ~150-250 tokens.

        Args:
            symbol: trading symbol; None uses the currently traded pair.
        """
        from src.agent.tools_perception import get_derivatives_data as _impl

        return await _impl(ctx.deps, symbol)

    @tool
    async def get_higher_timeframe_view(
        ctx: RunContext[TradingDeps],
        timeframe: Literal["4h", "1d", "1w", "1M"],
    ) -> str:
        """Get long-period structure: MA50/100/200 distances and range position.

        Reports moving averages (MA50/100/200), price position within the recent
        100-period range, and structural highs/lows over a longer window than
        your default trading timeframe. No default — explicitly pick the
        timeframe. Output ~250 tokens.

        Args:
            timeframe: '4h' bridges LTF and 1d; '1d'/'1w'/'1M' for swing/position context.
        """
        from src.agent.tools_perception import get_higher_timeframe_view as _impl

        return await _impl(ctx.deps, timeframe)

    @tool
    async def get_macro_context(ctx: RunContext[TradingDeps]) -> str:
        """Get cross-market macro snapshot.

        Includes BTC/ETH dominance, Total Crypto Mcap (CoinGecko), USD
        Trade-Weighted Index (FRED DTWEXBGS — note: the Fed's broad TW index
        across 26 currencies, NOT the ICE DXY across 6 currencies; absolute
        values differ and the two can diverge on single-currency moves, though
        they usually move in the same direction), VIX, 10Y Treasury yield,
        2s10s spread, 10Y inflation expectation (FRED), and SPY/QQQ closing
        quotes (Alpha Vantage). FRED data has daily granularity; SPY/QQQ are
        equity ETFs with NYSE trading-hour quotes. Output ~200 tokens.
        """
        from src.agent.tools_perception import get_macro_context as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_etf_flows(ctx: RunContext[TradingDeps], days: int = 7) -> str:
        """Get US BTC + ETH spot ETF daily net flows + cumulative AUM.

        Today's value may be revised T+1. Output ~300 tokens.

        Args:
            days: lookback days (1-14, default 7).
        """
        from src.agent.tools_perception import get_etf_flows as _impl

        return await _impl(ctx.deps, days)

    @tool
    async def get_stablecoin_supply(ctx: RunContext[TradingDeps]) -> str:
        """Get USDT + USDC current total supply and 7-day changes.

        Data sourced from DefiLlama (on-chain circulating supply). Output ~80 tokens.
        """
        from src.agent.tools_perception import get_stablecoin_supply as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_order_book(ctx: RunContext[TradingDeps], depth: int = 20) -> str:
        """Return top-N order book depth with concentrated-level breakdown.

        Reports best bid/ask, cumulative depth, bid/ask share, and concentrated
        levels (size > 3× same-side median). Use to evaluate liquidity, slippage
        risk, or concentrated levels near current price.

        Args:
            depth: levels per side to fetch (default 20).

        Degradation: "Order book ({symbol}): insufficient data (requested depth X, got Y)"
        if book is empty/short; "Order book ({symbol}): temporarily unavailable" on
        service failure.
        """
        from src.agent.tools_perception import get_order_book as _impl

        return await _impl(ctx.deps, depth=depth)

    @tool
    async def get_recent_trades(ctx: RunContext[TradingDeps], window_seconds: int = 300) -> str:
        """Read taker-flow bias and rhythm over recent minutes.

        Default 300s window across 5 × 60s buckets. Total + trade count + avg
        size shown below buckets.

        Args:
            window_seconds: total scan window (default 300s).
        """
        from src.agent.tools_perception import get_recent_trades as _impl

        return await _impl(ctx.deps, window_seconds=window_seconds)

    @tool
    async def get_multi_timeframe_snapshot(ctx: RunContext[TradingDeps], tfs: list[str] | None = None) -> str:
        """Scan multi-TF alignment in a single call (default 5m/1h/4h/1d).

        Useful for a once-per-cycle structural overview before committing to
        a direction. Reports 4 columns per TF: momentum / structure / volatility
        / range position.

        Args:
            tfs: list of timeframes; None uses default (5m/1h/4h/1d).
        """
        from src.agent.tools_perception import get_multi_timeframe_snapshot as _impl

        return await _impl(ctx.deps, tfs=tfs)

    @tool
    async def get_price_pivots(ctx: RunContext[TradingDeps]) -> str:
        """Scan structural price levels.

        Reports swing highs/lows from the last 100 main-TF bars (Williams fractal
        N=5) plus prior daily/weekly/monthly H/L. Levels are grouped above/below
        current price with distance % and bars-ago.
        """
        from src.agent.tools_perception import get_price_pivots as _impl

        return await _impl(ctx.deps)

    # === Execution Tools ===

    @tool
    async def open_position(
        ctx: RunContext[TradingDeps],
        side: str,
        position_pct: float,
        leverage: int,
        reasoning: str,
    ) -> str:
        """Open a new market-order position.

        Position fills via market order; you will receive a fill notification
        when execution completes. Set stop loss and take profit only after the
        fill notification arrives (separate trigger, not in the same cycle).

        Args:
            side: 'long' or 'short'.
            position_pct: percent of free balance to allocate (0-100).
            leverage: leverage multiplier (cannot be changed while holding position).
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import open_position as _impl

        return await _impl(ctx.deps, side, position_pct, leverage, reasoning=reasoning)

    @tool
    async def close_position(ctx: RunContext[TradingDeps], reasoning: str) -> str:
        """Close all open positions via market order.

        Position closure fills via market order; you will receive a fill
        notification when execution completes (separate trigger).

        Args:
            reasoning: brief description of your decision logic (e.g., 'TP target hit', 'thesis invalidated').
        """
        from src.agent.tools_execution import close_position as _impl

        return await _impl(ctx.deps, reasoning=reasoning)

    @tool
    async def set_stop_loss(ctx: RunContext[TradingDeps], price: float, reasoning: str) -> str:
        """Set stop loss on the current position.

        Auto-cancels any existing stop orders before placing the new one.
        On OKX, stop and take_profit orders sharing an algoId render as `[OCO]`
        in get_open_orders and are atomic — cancelling or triggering one leg
        removes both. To replace only one leg, re-create the other leg
        immediately after.

        Args:
            price: trigger price for the stop loss.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_stop_loss as _impl

        return await _impl(ctx.deps, price, reasoning=reasoning)

    @tool
    async def set_take_profit(ctx: RunContext[TradingDeps], price: float, reasoning: str) -> str:
        """Set take profit on the current position.

        Auto-cancels any existing take_profit orders before placing the new one.
        On OKX, stop and take_profit orders sharing an algoId render as `[OCO]`
        in get_open_orders and are atomic — cancelling or triggering one leg
        removes both. To replace only one leg, re-create the other leg
        immediately after.

        Args:
            price: trigger price for the take profit.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_take_profit as _impl

        return await _impl(ctx.deps, price, reasoning=reasoning)

    @tool
    async def adjust_leverage(ctx: RunContext[TradingDeps], leverage: int, reasoning: str) -> str:
        """Adjust leverage multiplier.

        Cannot be changed while holding a position — close first, then adjust.
        Higher leverage amplifies both gains and losses, including liquidation risk.

        Args:
            leverage: new leverage multiplier.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import adjust_leverage as _impl

        return await _impl(ctx.deps, leverage, reasoning=reasoning)

    @tool
    async def set_price_alert(
        ctx: RunContext[TradingDeps],
        threshold_pct: float,
        window_minutes: int,
        reasoning: str,
    ) -> str:
        """Adjust volatility alert sensitivity.

        Tighten in quiet markets to catch early moves; widen in volatile
        conditions to reduce noise. Pair with get_active_alerts to review
        current configuration.

        Args:
            threshold_pct: alert threshold percent (min 0.1, max 50).
            window_minutes: time window in minutes (min 1, max 240).
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_price_alert as _impl

        return await _impl(ctx.deps, threshold_pct, window_minutes, reasoning=reasoning)

    @tool
    async def cancel_order(ctx: RunContext[TradingDeps], order_id: str, reasoning: str) -> str:
        """Cancel a pending order (limit, stop loss, or take profit).

        Use to remove stale limit orders when the market has moved away from
        your intended entry. Leaving outdated orders risks an unintended fill
        at a price that no longer makes sense.

        Args:
            order_id: id of the order to cancel.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import cancel_order as _impl

        return await _impl(ctx.deps, order_id, reasoning=reasoning)

    @tool
    async def add_price_level_alert(
        ctx: RunContext[TradingDeps],
        price: float,
        direction: str,
        reasoning: str,
    ) -> str:
        """Set a one-shot alert at a specific price level.

        Useful for support/resistance levels you want to be notified about.
        Triggers once when reached, then auto-removes. You will be woken up
        when the level is hit.

        Args:
            price: alert price level.
            direction: 'above' (breakout) or 'below' (breakdown).
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import add_price_level_alert as _impl

        return await _impl(ctx.deps, price, direction, reasoning=reasoning)

    @tool
    async def cancel_price_level_alert(
        ctx: RunContext[TradingDeps],
        alert_id: str,
        reasoning: str,
    ) -> str:
        """Cancel a previously-set price level alert by its ID.

        Use this when an alert is no longer relevant — for example, if the
        structural level it watched has been invalidated by a regime change
        or if the position context that motivated it has shifted in a way
        that the auto-clearing on close fill does not cover.

        Note: alerts at SL/TP levels are auto-cleared when a position closes;
        you usually do not need to call this for that case.

        Args:
            alert_id: 8-char hex id returned by add_price_level_alert (also visible
                in get_active_alerts output as 'id=...'). Do not use the position
                index '#N' from get_active_alerts — that is for display only.
            reasoning: brief description of why this alert is being cancelled.
        """
        from src.agent.tools_execution import cancel_price_level_alert as _impl

        return await _impl(ctx.deps, alert_id, reasoning=reasoning)

    @tool
    async def set_next_wake(
        ctx: RunContext[TradingDeps],
        minutes: int,
        reasoning: str,
    ) -> str:
        """Set how soon you want to check the market again.

        One-shot: only affects the next wake, then reverts to the default
        interval. Shorten when you have an open position or expect volatility;
        lengthen when the market is quiet and you have no exposure.

        Args:
            minutes: minutes until next wake.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_next_wake as _impl

        return await _impl(ctx.deps, minutes, reasoning=reasoning)

    @tool
    async def place_limit_order(
        ctx: RunContext[TradingDeps],
        side: str,
        price: float,
        position_pct: float,
        leverage: int,
        reasoning: str,
    ) -> str:
        """Place a limit order at a specific price (e.g., buy at support level).

        Not every entry needs to be a market order — limit orders let you
        target specific levels without paying the spread.

        Args:
            side: 'long' or 'short'.
            price: limit price.
            position_pct: percent of free balance to allocate (0-100).
            leverage: leverage multiplier.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import place_limit_order as _impl

        return await _impl(ctx.deps, side, price, position_pct, leverage, reasoning=reasoning)

    # === Memory Tools ===

    @tool
    async def save_memory(
        ctx: RunContext[TradingDeps], category: str, content: str, importance: float = 0.5
    ) -> str:
        """Save a learning or observation to long-term memory.

        Save memories that your future self would find actionable — trade
        outcomes, pattern recognitions that proved correct or incorrect, and
        mistakes to avoid. Routine observations like "market is quiet" are
        not worth saving.

        Args:
            category: 'trade_review', 'market_pattern', or 'lesson'.
            content: the memory content to save.
            importance: weight 0-1 (default 0.5).
        """
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
    # --- 执行 (11) ---
    "open_position",
    "close_position",
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "set_price_alert",
    "cancel_order",
    "add_price_level_alert",
    "cancel_price_level_alert",
    "set_next_wake",
    "place_limit_order",
    # --- memory (1) ---
    "save_memory",
]
