from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)


async def get_market_data(
    deps: TradingDeps,
    symbol: str | None = None,
    timeframe: str | None = None,
    candle_count: int = 50,
) -> str:
    """Get market data: ticker, indicators, market context, and recent candles.

    candle_count=20 for quick check or secondary timeframes, 50 for detailed analysis.
    Default 50. Values above 50 may be capped by exchange API limits.
    Total output ~1000-1200 tokens (K-line table ~750-800 + indicators + context).
    """
    symbol = symbol or deps.symbol
    timeframe = timeframe or deps.timeframe
    candle_count = max(10, min(candle_count, 80))

    ticker = await deps.market_data.get_ticker(symbol)
    fetch_limit = max(candle_count + 50, 100)
    df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=fetch_limit)
    indicators = deps.technical.compute_indicators(df)
    indicators_text = deps.technical.format_for_llm(
        indicators, current_price=ticker.last, timeframe=timeframe,
    )

    # Determine display count
    available = len(df)
    if available >= candle_count + 50:
        display_count = candle_count
    else:
        display_count = max(10, available - 50)
    display_df = df.tail(display_count)

    sections: list[str] = []

    # === Ticker ===
    sections.append(
        f"=== Ticker ({symbol}) ===\n"
        f"Price: {ticker.last:.2f} | Bid: {ticker.bid:.2f} | Ask: {ticker.ask:.2f}\n"
        f"24h High: {ticker.high:.2f} | Low: {ticker.low:.2f} | Volume: {ticker.base_volume:.2f}"
    )

    # === Technical Indicators ===
    sections.append(
        f"=== Technical Indicators ({timeframe}) ===\n{indicators_text}"
    )

    # === Market Context ===
    ctx_lines = []
    atr = indicators.get("atr_14")
    if atr is not None and ticker.last > 0:
        pct = atr / ticker.last * 100
        if timeframe == "5m":
            if pct < 0.1:
                atr_label = f"{pct:.2f}% of price — low volatility"
            elif pct <= 0.3:
                atr_label = f"{pct:.2f}% of price — moderate"
            else:
                atr_label = f"{pct:.2f}% of price — high volatility"
        else:
            atr_label = f"{pct:.2f}% of price, {timeframe} candles"
        ctx_lines.append(f"ATR(14): {atr:.2f} ({atr_label})")
    else:
        ctx_lines.append("ATR(14): N/A")

    vr = indicators.get("volume_ratio")
    if vr is not None:
        raw_vol = df["volume"].iloc[-2] if len(df) >= 2 else df["volume"].iloc[-1]
        if vr < 0.7:
            vr_label = "low"
        elif vr <= 1.3:
            vr_label = "normal"
        else:
            vr_label = "above normal"
        ctx_lines.append(f"Volume: {raw_vol:.1f} ({vr:.2f}x avg — {vr_label})")
    else:
        ctx_lines.append("Volume: N/A")

    if not display_df.empty:
        candle_high = display_df["high"].max()
        candle_low = display_df["low"].min()
        ctx_lines.append(f"{display_count}-candle Range: {candle_low:.0f} — {candle_high:.0f}")
    else:
        ctx_lines.append("Range: N/A")
    sections.append("=== Market Context ===\n" + "\n".join(ctx_lines))

    # === Recent Candles ===
    from datetime import datetime, timezone
    tf_short = timeframe.lower()
    candle_lines = [f"{'Time':<14} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10} {'Vol':>10}"]
    for _, row in display_df.iterrows():
        ts = row["timestamp"]
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        else:
            dt = ts
        if tf_short in ("1m", "5m", "15m"):
            time_str = dt.strftime("%H:%M")
        elif tf_short in ("1h", "4h"):
            time_str = dt.strftime("%m-%d %H:%M")
        else:
            time_str = dt.strftime("%Y-%m-%d")
        candle_lines.append(
            f"{time_str:<14} {row['open']:>10.2f} {row['high']:>10.2f} "
            f"{row['low']:>10.2f} {row['close']:>10.2f} {row['volume']:>10.1f}"
        )
    sections.append(
        f"=== Recent Candles ({timeframe}, last {display_count}) ===\n"
        + "\n".join(candle_lines)
    )

    return "\n\n".join(sections)


async def get_position(deps: TradingDeps, symbol: str | None = None) -> str:
    """Get current open position with risk context."""
    symbol = symbol or deps.symbol
    positions = await deps.exchange.fetch_positions(symbol)
    if not positions:
        return "No open positions."

    p = positions[0]
    lines = ["Current Position:"]
    lines.append(f"  {p.side.upper()} {p.contracts} contracts @ {p.entry_price:.2f} | {p.leverage}x leverage")

    # PnL as % of initial capital
    if deps.initial_balance > 0:
        pnl_pct = (p.unrealized_pnl / deps.initial_balance) * 100
        lines.append(f"  PnL: {p.unrealized_pnl:.2f} USDT ({pnl_pct:+.2f}% of initial capital)")
    else:
        lines.append(f"  PnL: {p.unrealized_pnl:.2f} USDT")

    # Liquidation distance
    if p.liquidation_price:
        ticker = await deps.market_data.get_ticker(symbol)
        if ticker.last > 0:
            liq_dist = abs(ticker.last - p.liquidation_price) / ticker.last * 100
            lines.append(f"  Liquidation: {p.liquidation_price:.2f} ({liq_dist:.1f}% away)")
        else:
            lines.append(f"  Liquidation: {p.liquidation_price:.2f}")

    # Duration
    if p.created_at is not None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        delta = now - p.created_at
        total_minutes = int(delta.total_seconds() / 60)
        if total_minutes < 60:
            dur_str = f"{total_minutes} min"
        elif total_minutes < 1440:
            dur_str = f"{total_minutes // 60}h {total_minutes % 60}m"
        else:
            dur_str = f"{total_minutes // 1440}d {(total_minutes % 1440) // 60}h"
        lines.append(f"  Duration: {dur_str}")
    else:
        lines.append("  Duration: N/A")

    return "\n".join(lines)


async def get_account_balance(deps: TradingDeps) -> str:
    """Get account balance with return on initial capital."""
    balance = await deps.exchange.fetch_balance()
    ret_usdt = balance.total_usdt - deps.initial_balance
    ret_pct = (ret_usdt / deps.initial_balance) * 100 if deps.initial_balance > 0 else 0.0
    return (
        f"Account Balance:\n"
        f"  Total: {balance.total_usdt:.2f} USDT (initial: {deps.initial_balance:.2f})\n"
        f"  Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized)\n"
        f"  Free: {balance.free_usdt:.2f} USDT\n"
        f"  Used: {balance.used_usdt:.2f} USDT"
    )


async def get_memories(deps: TradingDeps) -> str:
    """Get long-term memories (lessons, patterns, trade reviews)."""
    return await deps.memory.format_for_prompt()


async def get_open_orders(deps: TradingDeps) -> str:
    """Get all pending orders with distance from current price."""
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return "No pending orders."

    ticker = await deps.market_data.get_ticker(deps.symbol)
    current = ticker.last

    lines = ["Pending Orders:"]
    for o in orders:
        if o.order_type == "market" or o.price is None:
            label = "[PENDING]" if o.order_type == "market" else f"[{o.order_type.upper()}]"
            price_str = "market price"
        else:
            if o.order_type == "limit":
                label = "[LIMIT]"
            else:
                label = f"[{o.order_type.upper()}]"
            if current > 0:
                dist = (o.price - current) / current * 100
                price_str = f"@ {o.price:.2f} ({dist:+.2f}% from current)"
            else:
                price_str = f"@ {o.price:.2f}"
        lines.append(f"  {label} {o.side} {o.amount} {price_str} | ID: {o.id}")
    return "\n".join(lines)


async def get_trade_journal(deps: TradingDeps, limit: int = 20) -> str:
    """Get trade journal — decision timeline with quick stats summary.
    Use for reviewing recent decisions and their outcomes."""
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

    sections: list[str] = []

    # Performance Summary (from MetricsService)
    if deps.metrics is not None:
        metrics = await deps.metrics.compute()
        if metrics.total_trades > 0:
            summary_lines = [
                f"Total Trades: {metrics.total_trades} | Win: {metrics.winning_trades} "
                f"({metrics.win_rate:.1%}) | Loss: {metrics.losing_trades}",
                f"Avg Win: {metrics.avg_win:+.2f} USDT | Avg Loss: {metrics.avg_loss:.2f} USDT",
                f"Profit Factor: {'N/A (no losses)' if metrics.profit_factor == float('inf') else f'{metrics.profit_factor:.2f}'}",
            ]
            if metrics.recent_summary:
                summary_lines.append(f"Recent: {metrics.recent_summary}")
            sections.append("=== Performance Summary ===\n" + "\n".join(summary_lines))

    # Trade Journal
    order_details = {}
    order_ids = list({a.order_id for a in actions if a.order_id})
    for oid in order_ids:
        try:
            order = await deps.exchange.fetch_order(oid, deps.symbol)
            order_details[oid] = order
        except Exception:
            logger.warning("Failed to fetch order %s", oid, exc_info=True)

    lines = []
    for a in reversed(actions):  # chronological order
        ts = a.created_at.strftime("%m-%d %H:%M")
        line = f"[{ts}] {a.action}"
        if a.side:
            line += f" ({a.side})"
        if a.order_id and a.order_id in order_details:
            od = order_details[a.order_id]
            if od.price:
                line += f" @ {od.price:.2f}"
            if od.fee is not None:
                line += f", fee={od.fee:.4f}"
            line += f" [{od.status}]"
        if a.pnl is not None:
            line += f", pnl={a.pnl:.2f}"
        if a.reasoning:
            line += f"\n  Reasoning: {a.reasoning}"
        lines.append(line)

    sections.append("=== Trade Journal ===\n" + "\n".join(lines))
    return "\n\n".join(sections)


async def get_active_alerts(deps: TradingDeps) -> str:
    """Get current alert configuration: volatility alert params and active price level alerts."""
    sections: list[str] = []

    # Volatility alert settings
    params = deps.exchange.get_alert_params()
    if params is not None:
        threshold, window = params
        sections.append(f"=== Price Alert Settings ===\nVolatility alert: {threshold}% in {window}min window")
    else:
        sections.append("=== Price Alert Settings ===\nVolatility alert: OFF")

    # Price level alerts
    alerts = deps.exchange.get_price_level_alerts()
    count = len(alerts)
    lines = [f"=== Active Price Level Alerts ({count}/20) ==="]
    if alerts:
        for i, a in enumerate(alerts, 1):
            lines.append(f'  #{i} {a["direction"]} {a["price"]:.2f} — "{a["reasoning"]}"')
    else:
        lines.append("  No active alerts.")
    sections.append("\n".join(lines))

    return "\n\n".join(sections)


async def get_performance(deps: TradingDeps) -> str:
    """Get detailed trading performance statistics.
    Use for reviewing overall results and evaluating strategy effectiveness."""
    balance = await deps.exchange.fetch_balance()
    ret_usdt = balance.total_usdt - deps.initial_balance
    ret_pct = (ret_usdt / deps.initial_balance) * 100 if deps.initial_balance > 0 else 0.0

    if deps.metrics is None:
        return (
            f"=== Trading Performance ===\n"
            f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
            f"Current Balance: {balance.total_usdt:.2f} USDT\n"
            f"Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT)\n\n"
            f"No metrics service available."
        )

    metrics = await deps.metrics.compute()

    if metrics.total_trades == 0:
        return (
            f"=== Trading Performance ===\n"
            f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
            f"Current Balance: {balance.total_usdt:.2f} USDT\n"
            f"Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT)\n\n"
            f"No completed trades yet."
        )

    fees_line = f"Total Fees: -{metrics.total_fees:.2f} USDT\n\n" if metrics.total_fees > 0 else "Total Fees: 0.00 USDT\n\n"

    return (
        f"=== Trading Performance ===\n"
        f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
        f"Current Balance: {balance.total_usdt:.2f} USDT\n"
        f"Total Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized)\n"
        f"Realized PnL: {metrics.total_pnl:+.2f} USDT (gross, before fees)\n"
        f"{fees_line}"
        f"Total Trades: {metrics.total_trades} | Win: {metrics.winning_trades} "
        f"({metrics.win_rate:.1%}) | Loss: {metrics.losing_trades}\n"
        f"Avg Win: {metrics.avg_win:+.2f} USDT | Avg Loss: {metrics.avg_loss:.2f} USDT\n"
        f"Profit Factor: {'N/A (no losses)' if metrics.profit_factor == float('inf') else f'{metrics.profit_factor:.2f}'}\n"
        f"Max Drawdown: {f'-{metrics.max_drawdown_pct:.1f}' if metrics.max_drawdown_pct > 0 else '0.0'}%\n"
        f"Best Trade: {metrics.best_trade:+.2f} USDT | Worst Trade: {metrics.worst_trade:.2f} USDT"
    )


# Display-layer filter for CoinDesk CATEGORY_DATA — strips thematic tags
# (MARKET / CRYPTOCURRENCY / ...) from the "Currencies" line rendered to
# the Agent. Tags stay in InformationEvent.symbols for matching logic;
# this only affects display.
_NON_CURRENCY_CATEGORIES = frozenset({
    "ALTCOIN", "BUSINESS", "CRYPTOCURRENCY", "EXCHANGE", "FIAT",
    "MACROECONOMICS", "MARKET", "REGULATION", "TECHNOLOGY", "TRADING",
})


def _fmt_currencies(syms: list[str]) -> str:
    filtered = [s for s in syms if s not in _NON_CURRENCY_CATEGORIES]
    return ", ".join(filtered) if filtered else "—"


async def get_market_news(
    deps: TradingDeps,
    news_filter: Literal["positive", "negative", "neutral"] | None = None,
) -> str:
    """Get crypto news headlines + Fear & Greed Index."""
    import asyncio

    if deps.news is None:
        return "News service not configured."

    from src.integrations.news.models import extract_base_currency

    base = extract_base_currency(deps.symbol)

    # Fetch news + FGI concurrently (independent upstreams, independent caches).
    news_result, fgi_result = await asyncio.gather(
        deps.news.get_news(deps.symbol, news_filter),
        deps.news.get_fear_greed_index(),
        return_exceptions=True,
    )
    # NewsService.get_news contract: tuple[list, list] on success (possibly
    # empty), None when CoinDesk errored with no stale cache. gather adds a
    # third state (Exception) as belt-and-suspenders against contract drift.
    if isinstance(news_result, Exception) or news_result is None:
        news_down = True
        symbol_news, general_news = [], []
    else:
        news_down = False
        symbol_news, general_news = news_result
    fgi = None if isinstance(fgi_result, Exception) else fgi_result

    sections: list[str] = []

    # FGI section
    if fgi is not None:
        date_str = fgi.timestamp.strftime("%Y-%m-%d")
        sections.append(
            f"=== Fear & Greed Index ===\n"
            f"Value: {fgi.title}\n"
            f"(Updated: {date_str})"
        )
    else:
        sections.append("=== Fear & Greed Index ===\nFGI service temporarily unavailable.")

    has_news = bool(symbol_news or general_news)
    if has_news:
        if symbol_news:
            lines: list[str] = []
            for e in symbol_news:
                ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
                source_name = e.content if e.content else e.source
                lines.append(f"[{ts}] {e.title}\n  Source: {source_name} | Currencies: {_fmt_currencies(e.symbols)}")
            sections.append(
                f"=== Symbol News ({base}, {len(symbol_news)}) ===\n"
                + "\n\n".join(lines)
            )

        if general_news:
            lines = []
            for e in general_news:
                ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
                source_name = e.content if e.content else e.source
                lines.append(f"[{ts}] {e.title}\n  Source: {source_name} | Currencies: {_fmt_currencies(e.symbols)}")
            sections.append(
                f"=== General Crypto News ({len(general_news)}) ===\n"
                + "\n\n".join(lines)
            )
    elif news_down:
        sections.append("=== News ===\nNews service temporarily unavailable.")
    else:
        sections.append("=== News ===\nNo recent headlines.")

    return "\n\n".join(sections)


async def get_critical_alerts(
    deps: TradingDeps,
    lookback_hours: int = 24,
    lookahead_hours: int = 12,
) -> str:
    """Get critical alerts: exchange announcements + upcoming macro events."""
    import asyncio

    if deps.news is None:
        return "News service not configured."

    # Parallelize announcements + macro events to minimize wall-clock latency.
    # Each call has independent upstream sources and caches, so gather is safe.
    announcements, macro_events = await asyncio.gather(
        deps.news.get_announcements(lookback_hours),
        deps.news.get_macro_events(lookahead_hours),
        return_exceptions=True,
    )
    # NewsService contract: list for success (may be empty); None when every
    # upstream source errored, so the Agent can distinguish "quiet window" from
    # "services unavailable" (spec §3.5). gather(return_exceptions=True) is a
    # belt-and-suspenders guard in case the service contract ever changes.
    if isinstance(announcements, Exception):
        announcements = None
    if isinstance(macro_events, Exception):
        macro_events = None

    sections: list[str] = []

    # Announcements
    if announcements is None:
        sections.append(
            f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
            "Exchange announcements service temporarily unavailable."
        )
    elif announcements:
        lines = [e.timestamp.strftime("[%Y-%m-%d %H:%M] ") + e.title for e in announcements]
        sections.append(
            f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
            + "\n".join(lines)
        )
    else:
        sections.append(
            f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
            "No exchange announcements."
        )

    # Macro events
    if macro_events is None:
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
            "Macro events service temporarily unavailable."
        )
    elif macro_events:
        lines = []
        for e in macro_events:
            ts = e.timestamp.strftime("%Y-%m-%d %H:%M")
            impact = e.importance.capitalize()
            line = f"[{ts}] {e.title} — Impact: {impact}"
            if e.content:
                line += f"\n  {e.content}"
            lines.append(line)
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
            + "\n".join(lines)
        )
    else:
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
            "No upcoming macro events."
        )

    # Footer: calendar scope reminder (spec §3.2). Skip when macro source is
    # fully unavailable — the caveat is meaningless without a result to qualify.
    if macro_events is not None:
        sections.append(
            "Note: macro calendar covers current week only; "
            "Friday evening / weekend calls may miss next week's early events."
        )

    return "\n\n".join(sections)


async def get_derivatives_data(
    deps: TradingDeps,
    symbol: str | None = None,
) -> str:
    """Get derivatives market data: funding rate, open interest, long/short ratio."""
    import asyncio
    from datetime import datetime, timezone

    symbol = symbol or deps.symbol
    sections = [f"=== Derivatives Data ({symbol}) ==="]
    errors: list[str] = []
    timestamps_ms: list[int] = []

    # Fetch all three concurrently — each has independent cache + upstream.
    # gather(return_exceptions=True) gives us per-method success/failure.
    funding, oi, lsr = await asyncio.gather(
        deps.market_data.get_funding_rate(symbol),
        deps.market_data.get_open_interest(symbol),
        deps.market_data.get_long_short_ratio(symbol),
        return_exceptions=True,
    )

    # Funding rate
    if isinstance(funding, Exception):
        errors.append("Funding rate temporarily unavailable")
    else:
        direction = "longs pay shorts" if funding.rate >= 0 else "shorts pay longs"
        sign = "Positive rate" if funding.rate >= 0 else "Negative rate"
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        remaining_ms = max(0, funding.next_funding_time - now_ms)
        hours = remaining_ms // (3600 * 1000)
        minutes = (remaining_ms % (3600 * 1000)) // (60 * 1000)
        sections.append(
            f"Funding Rate: {funding.rate:+.4%} (next settlement in {hours}h {minutes}m)\n"
            f"  {sign} — {direction}"
        )
        if funding.timestamp:
            timestamps_ms.append(funding.timestamp)

    # Open interest
    if isinstance(oi, Exception):
        errors.append("Open interest temporarily unavailable")
    else:
        if oi.open_interest_value >= 1e9:
            oi_str = f"${oi.open_interest_value / 1e9:.2f}B"
        elif oi.open_interest_value >= 1e6:
            oi_str = f"${oi.open_interest_value / 1e6:.2f}M"
        else:
            oi_str = f"${oi.open_interest_value:,.0f}"
        sections.append(f"Open Interest: {oi_str}")
        if oi.timestamp:
            timestamps_ms.append(oi.timestamp)

    # Long/short ratio
    if isinstance(lsr, Exception):
        errors.append("Long/short ratio temporarily unavailable")
    else:
        sections.append(
            f"Long/Short Ratio: {lsr.long_short_ratio:.2f} "
            f"({lsr.long_ratio:.1%} long / {lsr.short_ratio:.1%} short)"
        )
        if lsr.timestamp:
            timestamps_ms.append(lsr.timestamp)

    sections.extend(errors)

    # Show oldest timestamp — lets the Agent detect stale-cache fallback data.
    if timestamps_ms:
        oldest_dt = datetime.fromtimestamp(min(timestamps_ms) / 1000, tz=timezone.utc)
        sections.append(f"Data as of: {oldest_dt.strftime('%Y-%m-%d %H:%M')} UTC")

    return "\n".join(sections)
