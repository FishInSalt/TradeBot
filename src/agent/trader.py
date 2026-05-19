from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic_ai import Agent, RunContext
from sqlalchemy.ext.asyncio import AsyncEngine

from src.agent.memory import MemoryService
from src.agent.persona import DEFAULT_TAKER_FEE_RATE, generate_system_prompt, RuntimeConfig
from src.agent.tools_descriptions import (
    GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION,
    GET_MARKET_DATA_DESCRIPTION,
    GET_MULTI_TIMEFRAME_SNAPSHOT_DESCRIPTION,
    SET_NEXT_WAKE_AT_DESCRIPTION,
    SET_NEXT_WAKE_DESCRIPTION,
)
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
    fee_rate: float = DEFAULT_TAKER_FEE_RATE
    """Session-level taker fee rate (decimal). Mirror of RuntimeConfig.taker_fee_rate;
    injected from sessions.fee_rate via build_services. Default for tests only."""
    metrics: MetricsService | None = None
    news: NewsService | None = None
    macro: MacroService | None = None
    crypto_etf: CryptoEtfService | None = None
    onchain: OnchainService | None = None
    cycle_id: str | None = None  # Mutated by run_agent_cycle before agent.run(); see §3.3 of spec


def _create_dual_mode_tool(agent):
    """Build the project's @tool decorator with two usage modes:

        @tool                       — default: griffe sniffs docstring main_desc + Args
        @tool(description=DESC_X)   — override: pass DESC_X verbatim to LLM,
                                       bypass griffe section-stripping

    Why dual-mode: pydantic-ai 1.78 / griffe strips google section headers
    (Examples:, Example call:, inline admonitions) from tool_def.description.
    Override path B carries multi-outcome Examples / multi-section Example
    output blocks intact. See spec §2.2 of
    docs/superpowers/specs/2026-05-19-iter-tool-opt-dead-example-promote-design.md.

    Backward-compat: 33 existing @tool sites use the no-arg form, unchanged.

    Iter 5 D preserved: docstring_format='google' + require_parameter_descriptions=True
    still enforced on both branches.
    """
    def tool(func=None, *, description=None):
        kwargs = {
            "docstring_format": "google",
            "require_parameter_descriptions": True,
        }
        if description is not None:
            kwargs["description"] = description
        if func is not None and callable(func):
            return agent.tool(**kwargs)(func)
        return lambda f: agent.tool(**kwargs)(f)
    return tool


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

    # Iter 5 D: 启用 google docstring 显式声明 + 强制 Args 完整性 (preserved).
    # iter-tool-opt-dead-example-promote (2026-05-19): dual-mode wrapper extracted
    # to module-level `_create_dual_mode_tool` — supports `@tool(description=DESC_X)`
    # path-B override that bypasses griffe section-stripping for tools with
    # multi-outcome Examples / multi-section Example output blocks.
    tool = _create_dual_mode_tool(agent)

    # === Perception Tools ===

    # LLM-visible description: src.agent.tools_descriptions.GET_MARKET_DATA_DESCRIPTION
    @tool(description=GET_MARKET_DATA_DESCRIPTION)
    async def get_market_data(
        ctx: RunContext[TradingDeps],
        symbol: str | None = None,
        timeframe: str | None = None,
        candle_count: int = 30,
    ) -> str:
        """Get single-timeframe market data with indicators + OHLCV.

        Args:
            symbol: Trading symbol. Defaults to session symbol.
            timeframe: CCXT timeframe ("1m", "5m", "1h", etc.). Defaults to session primary timeframe.
            candle_count: Number of closed candles in the OHLCV table. Default 30. Range 10-80 (capped by exchange API).
        """
        from src.agent.tools_perception import get_market_data as _impl

        return await _impl(ctx.deps, symbol, timeframe, candle_count)

    @tool
    async def get_position(ctx: RunContext[TradingDeps], symbol: str | None = None) -> str:
        """Get current position details with risk exposure context.

        Includes Risk exposure (notional / margin / mark price / liquidation
        distance in ATR(1h) multiples — 1h is the fixed baseline regardless of
        session trading style) and Exit orders section (SL/TP distances from
        both entry and last price). Liquidation distance is computed against
        mark price.

        Output also includes Fee & Breakeven section: entry_fee paid (= entry × contracts × rate)
        and breakeven price = entry × (1 ± 2 × fee_rate) — the fill price at which the
        position is exactly flat on a taker round-trip.

        Args:
            symbol: trading symbol (defaults to session symbol).
        """
        from src.agent.tools_perception import get_position as _impl

        return await _impl(ctx.deps, symbol)

    @tool
    async def get_account_balance(ctx: RunContext[TradingDeps]) -> str:
        """Get account balance with return on initial capital.

        Output reports total equity, free margin, used margin, and percentage
        return on initial capital.
        """
        from src.agent.tools_perception import get_account_balance as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_open_orders(ctx: RunContext[TradingDeps]) -> str:
        """Get all pending orders with distance from last price.

        Lists limit orders, stop loss, and take profit orders, each with their
        price level and distance from last price. OCO-paired orders (sharing
        an algoId on OKX) render with `[OCO]` tag.
        """
        from src.agent.tools_perception import get_open_orders as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_trade_journal(ctx: RunContext[TradingDeps]) -> str:
        """Get the trade journal — decision timeline with quick stats summary.

        Related: get_performance (quantitative view of the same period).
        """
        from src.agent.tools_perception import get_trade_journal as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_active_alerts(ctx: RunContext[TradingDeps]) -> str:
        """Get current alert configuration.

        Reports volatility alert parameters (threshold % + time window) and
        active price level alerts.
        """
        from src.agent.tools_perception import get_active_alerts as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_performance(ctx: RunContext[TradingDeps]) -> str:
        """Show session trading performance — balance, return, fees, win rate, drawdown (gross + net dual view).

        Degradation responses: `No completed trades yet.` when there are no completed trades. `Stats unavailable: all close fills are pre-net-metrics-iter legacy data (forensic analysis via scripts/_sim_metrics.py from sim_orders table).` when all close fills are legacy. `Stats unavailable: data invariant violations (N close fills had no preceding open lot or corrupt amount/price). Investigate trade_actions integrity.` when data invariant violations are detected with zero recoverable trades. `No metrics service available.` when the metrics service is unavailable.

        Returns:
            str: Two sections.

            === Trading Performance (@ HH:MM:SS UTC) === — Initial Balance, Current Balance,
            Total Return (% + USDT, incl. unrealized, net), Realized PnL (gross / net + fees),
            Total Fees (-X.XX USDT (all fills)). The `(all fills)` scope flags that
            total_fees includes unclosed open lots' entry fees, so arithmetic
            self-check `gross − fees ≈ net` only holds exactly when no positions
            are open at compute time.

            === Trade Stats === — Per-condition `Note: ...` caveat lines precede metric values, in this order when applicable:
            - `Note: net stats based on m/n trades (...)` when pre-iter legacy close
              fills are skipped (FIFO requires entry_price + amount).
            - `Note: N close fills had cache-miss entry_price (FIFO unaffected; audit
              trail incomplete for those trades).` when OKX cache-miss is present.
            - `Note: invariant violations: N fill(s) had no preceding open lot or
              corrupt amount/price (excluded from FIFO; investigate trade_actions
              integrity).` when data integrity violations are detected and at
              least one trade completed (the zero-trades case of the same
              condition surfaces as the `Stats unavailable: data invariant
              violations ...` degradation response above instead).

            Metric values follow: Total Trades, Win Rate (gross / net), Avg Win/Loss
            (gross / net), Profit Factor (gross / net), Max Drawdown (net equity),
            Best/Worst Trade (gross / net).

            Related: get_trade_journal (decision timeline).
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
        Default: latest mix (no sentiment filter).

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

        Often empty when no recent announcements.

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

        Macro calendar covers the current week only — Friday evening / weekend
        calls may miss next week's early events. Often empty when no scheduled
        events in window.

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
        """Get derivatives market data: funding rate, open interest (with 1h/24h
        anchors and percent change), and long/short ratio.

        Positive funding rate means longs pay shorts; negative means shorts pay
        longs (settlement interval varies by contract — see next settlement time
        in output). Open interest is total outstanding contracts in USD, rendered
        with anchor values from 1h ago and 24h ago and the percent change to
        the current value — e.g. the OI line renders as `Open Interest: $2.69B
        (1h ago $2.71B, -0.7%; 24h ago $2.45B, +9.8%)`. Anchor labels correspond
        to OKX 1H-bar boundaries and may differ from wall-clock 1h/24h offsets
        by 0-60 minutes when the latest bar is still in progress. Long/short
        ratio is the ratio of long vs short account positions.

        Args:
            symbol: trading symbol; None uses the currently traded pair.
        """
        from src.agent.tools_perception import get_derivatives_data as _impl

        return await _impl(ctx.deps, symbol)

    # LLM-visible description: src.agent.tools_descriptions.GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION
    @tool(description=GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION)
    async def get_higher_timeframe_view(
        ctx: RunContext[TradingDeps],
        timeframes: list[Literal["4h", "1d", "1w", "1M"]] | None = None,
    ) -> str:
        """Higher-timeframe structural view across MA / range / ATR / volume.

        Args:
            timeframes: List of CCXT timeframes from {"4h", "1d", "1w", "1M"}. Default ["4h", "1d"]. Each timeframe rendered as a separate section.
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
        equity ETFs with NYSE trading-hour quotes.
        """
        from src.agent.tools_perception import get_macro_context as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_etf_flows(ctx: RunContext[TradingDeps], days: int = 7) -> str:
        """Get US BTC + ETH spot ETF daily net flows + cumulative AUM.

        Today's value may be revised T+1.

        Args:
            days: lookback days (1-14, default 7).
        """
        from src.agent.tools_perception import get_etf_flows as _impl

        return await _impl(ctx.deps, days)

    @tool
    async def get_stablecoin_supply(ctx: RunContext[TradingDeps]) -> str:
        """Get USDT + USDC current total supply and 7-day changes.

        Data sourced from DefiLlama (on-chain circulating supply).
        """
        from src.agent.tools_perception import get_stablecoin_supply as _impl

        return await _impl(ctx.deps)

    @tool
    async def get_order_book(ctx: RunContext[TradingDeps], depth: int = 15) -> str:
        """Return top-N order book depth with concentrated-level breakdown.

        Reports best bid/ask, cumulative depth, bid/ask share, and concentrated levels (size > 3× same-side median). If the book is empty or shorter than requested depth, the response is `Order book ({symbol}): insufficient data (requested depth X, got Y)`. On service failure, the response is `Order book ({symbol}): temporarily unavailable`.

        Args:
            depth: levels per side to fetch (default 15).
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

    # LLM-visible description: src.agent.tools_descriptions.GET_MULTI_TIMEFRAME_SNAPSHOT_DESCRIPTION
    @tool(description=GET_MULTI_TIMEFRAME_SNAPSHOT_DESCRIPTION)
    async def get_multi_timeframe_snapshot(ctx: RunContext[TradingDeps], tfs: list[str] | None = None) -> str:
        """Multi-TF snapshot — single fanout across N timeframes.

        Args:
            tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"].
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
        side: Literal["long", "short"],
        position_pct: float,
        leverage: int,
        reasoning: str,
    ) -> str:
        """Open a new market-order position.

        Position fills via market order; you will receive a fill notification
        when execution completes (separate trigger, not in the same cycle).
        Stop loss and take profit place against an existing position, so they
        require the fill notification.

        Entry incurs taker fee = notional × fee_rate. Fill notification reports actual fee.

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

        Close incurs taker fee on exit. Submit output includes est. exit fee and est. round-trip net PnL.

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

        Args:
            leverage: new leverage multiplier.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import adjust_leverage as _impl

        return await _impl(ctx.deps, leverage, reasoning=reasoning)

    @tool
    async def set_price_volatility_alert(
        ctx: RunContext[TradingDeps],
        threshold_pct: float,
        window_minutes: int,
        reasoning: str,
    ) -> str:
        """Set the price volatility alert (singleton). Creates if none is
        configured; otherwise replaces the existing one — replacing resets the
        rolling tick window, so the next trigger requires re-accumulating ticks
        across the full window from scratch. Use cancel_price_volatility_alert
        to remove without setting a new one.

        Related: get_active_alerts (current volatility + price-level alert state).

        Args:
            threshold_pct: alert threshold percent (0.1-50).
            window_minutes: time window in minutes (1-240).
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_price_volatility_alert as _impl

        return await _impl(ctx.deps, threshold_pct, window_minutes, reasoning=reasoning)

    @tool
    async def cancel_price_volatility_alert(
        ctx: RunContext[TradingDeps],
        reasoning: str,
    ) -> str:
        """Cancel the active price volatility alert. Idempotent: if no alert is
        set, returns ok with a note. Use set_price_volatility_alert to configure
        a new one.

        Related: get_active_alerts (current volatility + price-level alert state).

        Args:
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import cancel_price_volatility_alert as _impl

        return await _impl(ctx.deps, reasoning=reasoning)

    @tool
    async def cancel_order(ctx: RunContext[TradingDeps], order_id: str, reasoning: str) -> str:
        """Cancel a pending order (limit, stop loss, or take profit).

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
        """Update an existing price level alert in place: change its trigger
        price and reasoning. The direction (above/below) cannot change —
        to flip direction, cancel and add a new alert. The alert's id stays
        the same. Trail use case: when price moves and you want the same
        alert at a new level, this preserves identity (id, direction) while
        refreshing the price and reasoning.

        Args:
            alert_id: 8-char hex id of the existing alert (see get_active_alerts).
            new_price: new trigger price.
            reasoning: new rationale text; overwrites the alert's stored reasoning.
        """
        from src.agent.tools_execution import update_price_level_alert as _impl

        return await _impl(ctx.deps, alert_id, new_price, reasoning=reasoning)

    # LLM-visible description: src.agent.tools_descriptions.SET_NEXT_WAKE_DESCRIPTION
    @tool(description=SET_NEXT_WAKE_DESCRIPTION)
    async def set_next_wake(
        ctx: RunContext[TradingDeps],
        minutes: int,
        reasoning: str,
    ) -> str:
        """Schedule the next scheduler wake-up (relative interval).

        Args:
            minutes: minutes from now until the next wake-up. Must fall within
                [wake_min_minutes, wake_max_minutes]; rejected otherwise.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_next_wake as _impl

        return await _impl(ctx.deps, minutes, reasoning=reasoning)

    # LLM-visible description: src.agent.tools_descriptions.SET_NEXT_WAKE_AT_DESCRIPTION
    @tool(description=SET_NEXT_WAKE_AT_DESCRIPTION)
    async def set_next_wake_at(
        ctx: RunContext[TradingDeps],
        target_time: str,
        reasoning: str,
    ) -> str:
        """Schedule the next scheduler wake-up (absolute UTC time).

        Args:
            target_time: future wake time in 'HH:MM' UTC format (e.g., '10:37').
                Resolves to the nearest future time matching HH:MM (today if
                HH:MM is still ahead in UTC; otherwise tomorrow). Must fall
                within [now+wake_min_minutes, now+wake_max_minutes]; rejected
                otherwise.
            reasoning: brief description of your decision logic.
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

        Limit fill incurs maker or taker fee depending on fill condition.

        Args:
            side: 'long' or 'short'.
            price: limit price.
            position_pct: percent of free balance to allocate (0-100).
            leverage: leverage multiplier.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import place_limit_order as _impl

        return await _impl(ctx.deps, side, price, position_pct, leverage, reasoning=reasoning)

    return agent


# REGISTERED_TOOL_NAMES: 与 `@agent.tool` 装饰顺序保持一致（感知 → 执行）。
# 供 scheduler 日志、scripts/tool_call_summary.py 脚本、漂移防护测试统一引用。
# 漂移防护：tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools
# 用 agent._function_toolset.tools 对照本常量。加新 tool 必须同时更新此列表。
REGISTERED_TOOL_NAMES: list[str] = [
    # --- 感知 (19) ---
    "get_market_data",
    "get_position",
    "get_account_balance",
    "get_open_orders",
    "get_trade_journal",
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
    # --- 执行 (14) ---
    "open_position",
    "close_position",
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "set_price_volatility_alert",
    "cancel_price_volatility_alert",
    "cancel_order",
    "add_price_level_alert",
    "cancel_price_level_alert",
    "update_price_level_alert",
    "set_next_wake",
    "set_next_wake_at",
    "place_limit_order",
]
