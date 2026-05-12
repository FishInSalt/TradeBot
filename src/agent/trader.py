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
        candle_count: int = 30,
    ) -> str:
        """Single-timeframe market data: ticker, technical indicators (RSI / MACD / BB / ATR / volume ratio), market context (ATR with percent of price, last-bar volume with average ratio, display-window range), the most recent N closed candles in OHLCV table form with anomaly markers, and a period summary comparing the last 5 vs prior 5 closed candles (avg volume, avg range, net Δclose).

        All indicators are computed on the closed-bar series only (excluding the in-progress candle). The OHLCV table also shows closed bars only and is sorted oldest-first by row.

        Markers in OHLCV table (upside-only thresholds):
            "vol↑"   — bar volume > 2× SMA(20) of bar volumes
            "range↑" — bar range (high - low) > 2× ATR(14)
            Empty    — neither threshold tripped.

        Time column shows candle open in UTC.

        Args:
            symbol: Trading symbol. Defaults to session symbol.
            timeframe: CCXT timeframe ("1m", "5m", "1h", etc.). Defaults to session primary timeframe.
            candle_count: Number of closed candles in the OHLCV table. Default 30. Range 10-80 (capped by exchange API).

        Example call:
            get_market_data(timeframe="5m", candle_count=30)
        Example output:
            === Ticker (BTC/USDT:USDT @ 14:23:08 UTC) ===
            Last: 81870.50 | Bid: 81870.40 | Ask: 81870.60
            ...
            === Recent Candles (5m, last 30, oldest-first by row) ===
            Time (open UTC)   Open ... Vol     Markers
            14:20         ...         245.3   vol↑
            ...
            === Period summary (last 5 closed candles vs prior 5 closed candles) ===
            Avg vol:            last 5 178.6 / prior 5 132.4 (1.35×)
            Avg range (H-L):    last 5 38.2 / prior 5 24.8 (1.54×)
            Net Δclose:         last 5 -25.0 USDT / prior 5 +120.0 USDT
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
        timeframes: list[Literal["4h", "1d", "1w", "1M"]] | None = None,
    ) -> str:
        """Long-term structural view across one or more higher timeframes: ticker (authoritative live price), per-tf MA50/MA100/MA200 with raw value, price-vs-MA percentage, and MA slope (10-bar lookback); MA stack comparison; 100-period high and low with bars-ago and the candle open timestamp; range position within 100-period; 20-period high-low range width; last-bar volume vs 20-period SMA ratio (base volume); ATR(14) raw, percent of price, and ratio vs 20-period ATR average.

        All moving averages are simple moving averages (SMA) computed on the closed-bar series only (excluding the in-progress bar). The slope reference and all rolling averages use the closed-candle series.

        MA stack comparison uses ">" / "<" / "≈" with 0.1% tolerance: when |MAa - MAb| / MAb < 0.001, the operator collapses to "≈" (e.g., "MA50 ≈ MA100 < MA200").

        Per-tf MA periods: 4h / 1d / 1w use (50, 100, 200) — standard moving-average periods. 1M uses (12, 24, 60), corresponding to 1-year / 2-year / 5-year monthly cycles, matching crypto-industry monthly chart conventions; the 1M section header marks the period choice explicitly.

        Args:
            timeframes: List of CCXT timeframes from {"4h", "1d", "1w", "1M"}. Default ["4h", "1d"]. Each timeframe rendered as a separate section.

        Example call:
            get_higher_timeframe_view(timeframes=["4h", "1d"])
        Example output:
            === Higher Timeframe View (BTC/USDT:USDT @ 14:23:08 UTC) ===
            Last: 81870.50

            [4h] (last closed candle: open 2026-05-11 08:00 UTC)
              MA50: 79200.00 (price vs MA: +3.4%; MA slope vs 10 bars ago: +0.8%)
              ...
              MA stack: MA50 > MA100 > MA200
              100-period High: 82800.00 (32 bars ago, candle open 2026-05-06 00:00 UTC)
              ...
              Last bar vol (base): 1521.6 (5.0× SMA(20) avg)
              ATR(14): 1572.30 (1.92% of price; 1.04× vs 20-period ATR(14) avg)
            ...

        Degradation: per-tf "insufficient data (need N candles)" if OHLCV history is shorter than the longest MA period; per-tf "Error: Temporarily unavailable" if the OHLCV fetch for that tf fails; overall returns header-only error if the ticker fetch fails.
        """
        from src.agent.tools_perception import get_higher_timeframe_view as _impl

        return await _impl(ctx.deps, timeframes)

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
        """Multi-timeframe snapshot: ticker (authoritative current price) plus a cross-tf MA fast-vs-slow direction line plus per-tf rows containing momentum (live ticker vs primary MA, %), fast-vs-slow MA structure (MA names with raw values and comparison operator; weekly/monthly tfs use degraded (20, 50) periods marked with " (short-structure)"), volatility (ATR % of price and its ratio vs 20-period ATR average), range position (live ticker price within the last 20 closed-bar high-low, 0% = low / 100% = high), and the most recent 3 closed candle closes with the close timestamp.

        All moving averages are simple moving averages (SMA) computed on the closed-bar series only (excluding the in-progress bar). Per-tf MA values are rendered inline in the Structure column; the Momentum column shows the percentage from live ticker to the primary MA on each tf. ATR(14) is computed via _atr_series (mamode='rma' algorithm lock per spec §6.4.2); shared 4h/1d signals also surfaced by HTF use the same SMA formula and the same _atr_series helper, so identical inputs produce identical values by construction (§2.2.1 algorithm-lock invariant; end-to-end verified by test_mts_htf_overlap_values_match).

        Args:
            tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"].

        Example call:
            get_multi_timeframe_snapshot()
        Example output:
            === Multi-TF Snapshot (BTC/USDT:USDT) ===
            Last (ticker @ 14:23:08 UTC): 81870.50
            MA fast-vs-slow per tf: 5m below | 1h above | 4h above | 1d below
            Columns: ...

            [5m]  Mom -0.3% (vs MA20) | MA20: 81960 < MA50: 82150 | ATR 0.15% (20p avg 0.18%, 0.83×) | Range pos 65%
                  Last 3 closes (closed @ 2026-05-11 14:20 UTC): 81870→81848→81870
            ... (3 more tf rows)

        Degradation: per-TF "insufficient data" or "temporarily unavailable"; overall returns header-only error if all TFs fail or ticker fetch fails.
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

        Idempotent: if the alert is no longer active (already triggered
        or auto-cleared by a position-close fill), returns ok with a
        'Note: Alert {id} no longer active' line rather than emitting a
        business error. Format-invalid IDs still reject explicitly.

        Note: alerts at SL/TP levels are auto-cleared when a position
        closes; you usually do not need to call this for that case.

        Args:
            alert_id: 8-char hex id returned by add_price_level_alert
                (also visible in get_active_alerts output as 'id=...').
                Do not use the position index '#N' from get_active_alerts —
                that is for display only.
            reasoning: brief description of why this alert is being
                cancelled.
        """
        from src.agent.tools_execution import cancel_price_level_alert as _impl

        return await _impl(ctx.deps, alert_id, reasoning=reasoning)

    @tool
    async def update_price_level_alert(
        ctx: RunContext[TradingDeps],
        alert_id: str,
        new_price: float,
        reasoning: str,
    ) -> str:
        """Replace a single existing price level alert with a new price.

        Atomic: cancels the old alert and creates a new one with new_price,
        preserving the original direction and reasoning text. The direction
        (above/below) cannot change — to change direction or reasoning
        materially, use cancel + add. Trail use case: when price moves and
        you want the same alert at a new level, this preserves identity
        continuity (the alert is still "the same thing at a new price").

        Args:
            alert_id: 8-char hex id of the existing alert (see get_active_alerts).
            new_price: new trigger price.
            reasoning: brief rationale for the move (audit-only).
        """
        from src.agent.tools_execution import update_price_level_alert as _impl

        return await _impl(ctx.deps, alert_id, new_price, reasoning=reasoning)

    @tool
    async def set_next_wake(
        ctx: RunContext[TradingDeps],
        minutes: int,
        reasoning: str,
    ) -> str:
        """Schedule the next scheduler wake-up after a relative minute interval.

        Args:
            minutes: minutes from now until the next wake-up. Must fall within
                [wake_min_minutes, wake_max_minutes]; rejected otherwise.
            reasoning: brief description of your decision logic.

        Returns a confirmation, or a reject message describing the violation.

        Examples:
            set_next_wake(15, "consolidation phase, check in 15 min")
            → "Next wake set to 15 min. Reason: ..."

            set_next_wake(90, "...")
            → "Cannot set wake to 90 min: exceeds wake_max=60 min for this session."

            set_next_wake(0, "...")
            → "Cannot set wake to 0 min: below wake_min=1 min."

        Alerts, fills, and conditional triggers always interrupt scheduled wake.
        """
        from src.agent.tools_execution import set_next_wake as _impl

        return await _impl(ctx.deps, minutes, reasoning=reasoning)

    @tool
    async def set_next_wake_at(
        ctx: RunContext[TradingDeps],
        target_time: str,
        reasoning: str,
    ) -> str:
        """Schedule the next scheduler wake-up at an absolute UTC time.

        Args:
            target_time: future wake time in 'HH:MM' UTC format (e.g., '10:37').
                Resolves to the nearest future time matching HH:MM (today if
                HH:MM is still ahead in UTC; otherwise tomorrow). Must fall
                within [now+wake_min_minutes, now+wake_max_minutes]; rejected
                otherwise.
            reasoning: brief description of your decision logic.

        Returns a confirmation containing the resolved date-time, or a reject
        message describing the violation.

        Examples:
            set_next_wake_at("10:37", "align with 1h candle close at 11:00 UTC")
            → "Next wake set for 2026-05-12 10:37 UTC (in 14 min). Reason: ..."

            set_next_wake_at("12:00", "...")
            → "Cannot wake at 12:00 UTC: nearest future 2026-05-12 12:00 UTC
               (in 97 min) exceeds wake_max=60 min for this session."

            set_next_wake_at("10:23", "...")  # now=10:23, resolves to tomorrow
            → "Cannot wake at 10:23 UTC: nearest future 2026-05-13 10:23 UTC
               (in 1440 min) exceeds wake_max=60 min for this session."

            set_next_wake_at("foo", "...")
            → "Invalid target_time format: 'foo'. Expected 'HH:MM' UTC
               with 2-digit hour and minute (e.g., '10:37' or '03:05')."

        Alerts, fills, and conditional triggers always interrupt scheduled wake.
        """
        from src.agent.tools_execution import set_next_wake_at as _impl

        return await _impl(ctx.deps, target_time, reasoning=reasoning)

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
    # --- 执行 (13) ---
    "open_position",
    "close_position",
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "set_price_alert",
    "cancel_order",
    "add_price_level_alert",
    "cancel_price_level_alert",
    "update_price_level_alert",
    "set_next_wake",
    "set_next_wake_at",
    "place_limit_order",
    # --- memory (1) ---
    "save_memory",
]
