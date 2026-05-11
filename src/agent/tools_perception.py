from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps
    from src.integrations.exchange.base import Trade

logger = logging.getLogger(__name__)


# === Iter 2 toolkit constants ===
# get_order_book
ORDER_BOOK_CONCENTRATION_MULTIPLIER = 3.0
ORDER_BOOK_MAX_CONCENTRATED_LEVELS = 10
ORDER_BOOK_DEPTH_DEFAULT = 20
ORDER_BOOK_BALANCED_THRESHOLD_PCT = 5.0

# get_recent_trades
RECENT_TRADES_WINDOW_DEFAULT = 300
RECENT_TRADES_BUCKET_COUNT = 5
RECENT_TRADES_MAX_FETCH = 500  # OKX /market/trades single-call limit

# get_multi_timeframe_snapshot
MULTI_TF_PRIMARY_MA = {"5m": 20, "1h": 50, "4h": 50, "1d": 50, "1w": 50, "1M": 50}
MULTI_TF_STRUCTURE_MAS = {
    "5m": (20, 50),
    "1h": (50, 200),
    "4h": (50, 200),
    "1d": (50, 200),
    "1w": (20, 50),
    "1M": (20, 50),
}
MULTI_TF_RANGE_PERIODS = 20
MULTI_TF_OHLCV_LIMIT = {"5m": 80, "1h": 250, "4h": 250, "1d": 250, "1w": 60, "1M": 60}

# get_higher_timeframe_view (Iter w2r2-next-d): per-tf MA periods.
# 4h/1d/1w use standard (50, 100, 200); 1M uses (12, 24, 60) = 1y/2y/5y
# monthly per crypto-industry convention (spec §5.4).
HTF_MA_PERIODS: dict[str, tuple[int, int, int]] = {
    "4h": (50, 100, 200),
    "1d": (50, 100, 200),
    "1w": (50, 100, 200),
    "1M": (12, 24, 60),
}
HTF_OHLCV_LIMIT = 250  # uniform; longest MA(200) + slope lookback 10 + buffer


async def get_market_data(
    deps: TradingDeps,
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

    Related perception tools (factual capability surface, not a calling order):
        - get_multi_timeframe_snapshot: cross-timeframe alignment overview — authoritative ticker, per-tf MA fast-vs-slow direction count, per-tf momentum / structure with raw MA values / volatility ratio / range position / 3 closed candle closes.
        - get_higher_timeframe_view: long-term structural anchors output — raw MA50/100/200 values with slopes and MA stack, 100-period range with bars-ago, volume regime, ATR regime, across one or more higher timeframes.
    """
    import pandas as pd
    from datetime import datetime, timezone
    from src.utils.ohlcv_utils import _live_price, _closed_bars, _atr_series

    symbol = symbol or deps.symbol
    timeframe = timeframe or deps.timeframe
    candle_count = max(10, min(candle_count, 80))

    ticker = await deps.market_data.get_ticker(symbol)
    live_price = _live_price(ticker)
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    fetch_limit = max(candle_count + 50, 100)
    df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=fetch_limit)
    df_closed = _closed_bars(df)
    indicators = deps.technical.compute_indicators(df_closed)
    indicators_text = deps.technical.format_for_llm(
        indicators, current_price=live_price, timeframe=timeframe,
    )

    available_closed = len(df_closed)
    if available_closed >= candle_count + 50:
        display_count = candle_count
    else:
        display_count = max(10, available_closed - 50)
    display_df = df_closed.tail(display_count)

    sections: list[str] = []

    # === Ticker ===
    sections.append(
        f"=== Ticker ({symbol} @ {fetch_ts} UTC) ===\n"
        f"Last: {live_price:.2f} | Bid: {ticker.bid:.2f} | Ask: {ticker.ask:.2f}\n"
        f"24h High: {ticker.high:.2f} | 24h Low: {ticker.low:.2f} | 24h base vol: {ticker.base_volume:.2f}"
    )

    # === Technical Indicators ===
    sections.append(f"=== Technical Indicators ({timeframe}) ===\n{indicators_text}")

    # === Market Context ===
    ctx_lines: list[str] = []
    atr = indicators.get("atr_14")
    if atr is not None and live_price > 0:
        pct = atr / live_price * 100
        ctx_lines.append(f"ATR(14): {atr:.2f} ({pct:.2f}% of price, {timeframe} candles)")
    else:
        ctx_lines.append("ATR(14): N/A")

    # F-O3: Last bar vol with SMA(20) period explicit.
    # Window: "last 20 closed bars including the latest" — identical to HTF
    # (spec §5.5), so the same market state renders the same ratio in both
    # tools. Note: the bar in the numerator (df_closed.iloc[-1]) is the most
    # recent closed bar, which differs from TechnicalAnalysisService
    # compute_indicators.volume_ratio (uses iloc[-2]); only the SMA window
    # matches across tools, not the numerator-bar selection.
    if len(df_closed) >= 20:
        vol_now = float(df_closed["volume"].iloc[-1])
        vol_avg = float(df_closed["volume"].iloc[-20:].mean())
        ratio = vol_now / vol_avg if vol_avg > 0 else 0.0
        ctx_lines.append(f"Last bar vol: {vol_now:.1f} ({ratio:.2f}× SMA(20) avg)")
    else:
        ctx_lines.append("Last bar vol: N/A")

    if not display_df.empty:
        ctx_lines.append(
            f"{display_count}-candle High-Low: {display_df['low'].min():.0f} — {display_df['high'].max():.0f}"
        )
    else:
        ctx_lines.append("Range: N/A")
    sections.append("=== Market Context ===\n" + "\n".join(ctx_lines))

    # === Recent Candles (OHLCV with markers) ===
    vol_sma = df_closed["volume"].rolling(20).mean()
    atr_series = _atr_series(df_closed, period=14) if len(df_closed) >= 15 else None
    candle_lines: list[str] = [
        f"{'Time (open UTC)':<16} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10} {'Vol':>10}  Markers"
    ]
    for idx in display_df.index:
        row = df_closed.loc[idx]
        ts_val = row["timestamp"]
        if isinstance(ts_val, (int, float)):
            dt = datetime.fromtimestamp(ts_val / 1000, tz=timezone.utc)
        else:
            dt = ts_val
        tf_short = timeframe.lower()
        if tf_short in ("1m", "5m", "15m"):
            time_str = dt.strftime("%H:%M")
        elif tf_short in ("1h", "4h"):
            time_str = dt.strftime("%m-%d %H:%M")
        else:
            time_str = dt.strftime("%Y-%m-%d")

        markers: list[str] = []
        vol_sma_at = vol_sma.loc[idx] if idx in vol_sma.index else None
        if vol_sma_at is not None and not pd.isna(vol_sma_at) and float(vol_sma_at) > 0:
            if float(row["volume"]) > 2 * float(vol_sma_at):
                markers.append("vol↑")
        atr_at = None
        if atr_series is not None and idx in atr_series.index:
            atr_at = atr_series.loc[idx]
        if atr_at is not None and not pd.isna(atr_at) and float(atr_at) > 0:
            if (float(row["high"]) - float(row["low"])) > 2 * float(atr_at):
                markers.append("range↑")
        marker_str = " ".join(markers)

        candle_lines.append(
            f"{time_str:<16} {row['open']:>10.2f} {row['high']:>10.2f} "
            f"{row['low']:>10.2f} {row['close']:>10.2f} {row['volume']:>10.1f}  {marker_str}".rstrip()
        )
    sections.append(
        f"=== Recent Candles ({timeframe}, last {display_count}, oldest-first by row) ===\n"
        + "\n".join(candle_lines)
    )

    # === Period summary ===
    if len(df_closed) >= 10:
        last_5 = df_closed.iloc[-5:]
        prior_5 = df_closed.iloc[-10:-5]
        avg_vol_last = float(last_5["volume"].mean())
        avg_vol_prior = float(prior_5["volume"].mean())
        vol_ratio = avg_vol_last / avg_vol_prior if avg_vol_prior > 0 else 0.0
        avg_rng_last = float((last_5["high"] - last_5["low"]).mean())
        avg_rng_prior = float((prior_5["high"] - prior_5["low"]).mean())
        rng_ratio = avg_rng_last / avg_rng_prior if avg_rng_prior > 0 else 0.0
        net_delta_last = float(df_closed["close"].iloc[-1] - df_closed["close"].iloc[-5])
        net_delta_prior = float(df_closed["close"].iloc[-6] - df_closed["close"].iloc[-10])
        summary = (
            "=== Period summary (last 5 closed candles vs prior 5 closed candles) ===\n"
            f"Avg vol:            last 5 {avg_vol_last:.1f} / prior 5 {avg_vol_prior:.1f} ({vol_ratio:.2f}×)\n"
            f"Avg range (H-L):    last 5 {avg_rng_last:.1f} / prior 5 {avg_rng_prior:.1f} ({rng_ratio:.2f}×)\n"
            f"Net Δclose:         last 5 {net_delta_last:+.1f} USDT / prior 5 {net_delta_prior:+.1f} USDT"
        )
        sections.append(summary)

    return "\n\n".join(sections)


async def get_position(deps: TradingDeps, symbol: str | None = None) -> str:
    """Show current position with risk exposure and SL/TP distances.

    Args:
        symbol: Optional override of deps.symbol.

    Returns:
        str: Multi-section position view (position line + PnL + Duration + Risk exposure + Exit orders). See spec §2.4.

    Degradation: 'No open positions.' if empty. ATR(1h) unavailable → ATR-multiple suffixes omitted (other sections intact).
    """
    import asyncio
    symbol = symbol or deps.symbol

    # Phase 1: positions only — early return if empty
    positions = await deps.exchange.fetch_positions(symbol)
    if not positions:
        return "=== Position ===\nNo open positions."

    p = positions[0]

    # Phase 2: gather remaining IO in parallel. OHLCV has per-call soft-fail (ATR suffix omission
    # is spec §2.4 three-state). Ticker / balance / orders / contract_size failures are hard —
    # wrap the whole gather in a try/except that degrades the enhanced sections, keeping the
    # original position+PnL+Duration lines intact.
    #
    # NOTE: spec §3.3 suggests `return_exceptions=True`. We use `False + outer try/except` instead
    # for these reasons: (1) simpler to reason about — any hard failure collapses to a single
    # degradation path rather than 5 per-IO isinstance checks; (2) Risk exposure and Exit orders
    # both need coherent ticker + balance + contract_size; partial success gives misleading
    # numbers (e.g. "Notional X USDT" without a valid ticker → stale). The spec's preference
    # is a recommendation, not a hard constraint; the audit flagged this as P3 (non-critical).
    async def _safe_ohlcv():
        try:
            return await deps.market_data.get_ohlcv_dataframe(symbol, "1h", limit=50)
        except Exception:
            logger.exception("get_position: 1h OHLCV fetch failed")
            return None

    def _render_position_core() -> list[str]:
        """Render Position + PnL sections (Phase-1 fields only).

        Returns a list of 2 fully-formed sections (each "=== Header ===\\n<body>"
        joined string) so callers can append further sections via "\\n\\n".join.
        Shared between happy path and hard-failure degradation branch so PnL +
        Duration are preserved when ticker/balance/orders/contract_size fail
        (would otherwise be lost even though `p.created_at` is fully available).
        """
        pos_lines = [f"=== Position ({symbol}) ===",
                     f"Side: {p.side.capitalize()} | Contracts: {p.contracts} | Entry: {p.entry_price:,.2f}",
                     f"Leverage: {p.leverage}x"]
        if p.liquidation_price is not None:
            pos_lines.append(f"Liquidation: {p.liquidation_price:,.2f}")
        pos_lines.append(f"Unrealized: {p.unrealized_pnl:+.2f} USDT")

        pnl_lines = ["=== PnL ==="]
        if deps.initial_balance > 0:
            pnl_pct_inner = (p.unrealized_pnl / deps.initial_balance) * 100
            pnl_lines.append(
                f"PnL: {p.unrealized_pnl:+.2f} USDT ({pnl_pct_inner:+.2f}% of initial capital)"
            )
        else:
            pnl_lines.append(f"PnL: {p.unrealized_pnl:+.2f} USDT")
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
            pnl_lines.append(f"Duration: {dur_str}")
        else:
            pnl_lines.append("Duration: N/A")
        return ["\n".join(pos_lines), "\n".join(pnl_lines)]

    try:
        ticker, balance, ohlcv_df, open_orders, contract_size = await asyncio.gather(
            deps.market_data.get_ticker(symbol),
            deps.exchange.fetch_balance(),
            _safe_ohlcv(),
            deps.exchange.fetch_open_orders(symbol),
            deps.exchange.get_contract_size(symbol),
            return_exceptions=False,
        )
    except Exception:
        logger.exception("get_position: one of ticker/balance/orders/contract_size failed")
        sections = _render_position_core()
        sections.append("=== Risk Exposure ===\n(unavailable)")
        sections.append("=== Exit Orders ===\n(unavailable)")
        return "\n\n".join(sections)

    # ATR(1h) — may be None if OHLCV failed
    atr_1h = None
    if ohlcv_df is not None and not ohlcv_df.empty:
        indicators = deps.technical.compute_indicators(ohlcv_df)
        atr_1h = indicators.get("atr_14")
    current_price = ticker.last

    sections = _render_position_core()

    # === Risk Exposure ===
    notional = p.contracts * p.entry_price * contract_size
    equity = balance.total_usdt
    exp_pct = notional / equity * 100 if equity > 0 else 0.0
    margin_used = balance.used_usdt
    margin_pct = margin_used / equity * 100 if equity > 0 else 0.0
    atr_pct_1h = atr_1h / current_price * 100 if atr_1h is not None and current_price > 0 else None

    risk_lines = ["=== Risk Exposure ==="]
    risk_lines.append(f"Notional value: {notional:.2f} USDT ({exp_pct:.1f}% of equity {equity:.2f})")
    risk_lines.append(f"Margin used: {margin_used:.2f} USDT ({margin_pct:.1f}% of equity, from balance.used_usdt)")
    if p.liquidation_price is not None and current_price > 0:
        liq_dist_pct = abs(current_price - p.liquidation_price) / current_price * 100
        if atr_pct_1h is not None and atr_pct_1h > 0:
            atr_mult = liq_dist_pct / atr_pct_1h
            risk_lines.append(f"Liquidation: {p.liquidation_price:.2f} ({liq_dist_pct:.1f}% away = {atr_mult:.1f}× ATR(1h))")
        else:
            risk_lines.append(f"Liquidation: {p.liquidation_price:.2f} ({liq_dist_pct:.1f}% away)")
    sections.append("\n".join(risk_lines))

    # === Exit Orders ===
    # Filter out None-price orders defensively. Iter 2b OKX algo-order normalization
    # is expected to always populate `price` from slTriggerPx/tpTriggerPx, but guarding
    # here keeps _fmt_exit free of None-handling branches and crashes explicitly if the
    # normalization contract is ever violated upstream (rather than silently rendering
    # "(None% above entry)" or similar garbage).
    sl_orders = sorted(
        [o for o in open_orders
         if o.order_type == "stop" and o.symbol == symbol and o.price is not None],
        key=lambda o: o.price,
    )
    tp_orders = sorted(
        [o for o in open_orders
         if o.order_type == "take_profit" and o.symbol == symbol and o.price is not None],
        key=lambda o: o.price,
    )
    exit_lines = ["=== Exit Orders ==="]

    def _fmt_exit(o, kind: str) -> str:
        dist_entry_pct = (o.price - p.entry_price) / p.entry_price * 100
        dist_curr_pct = (o.price - current_price) / current_price * 100 if current_price > 0 else 0.0
        direction_entry = "above" if dist_entry_pct > 0 else "below"
        direction_curr = "above" if dist_curr_pct > 0 else "below"
        suffix = ""
        if atr_pct_1h is not None and atr_pct_1h > 0:
            atr_mult = abs(dist_curr_pct) / atr_pct_1h
            suffix = f" = {atr_mult:.1f}× ATR(1h)"
        return f"  {kind}: {o.price:.2f} ({abs(dist_entry_pct):.1f}% {direction_entry} entry, {abs(dist_curr_pct):.1f}% {direction_curr} current{suffix})  [{o.amount} contracts]"

    if sl_orders:
        for o in sl_orders:
            exit_lines.append(_fmt_exit(o, "Stop loss"))
    else:
        exit_lines.append("  Stop loss: not set")

    if tp_orders:
        for o in tp_orders:
            exit_lines.append(_fmt_exit(o, "Take profit"))
    else:
        exit_lines.append("  Take profit: not set")
    sections.append("\n".join(exit_lines))

    return "\n\n".join(sections)


async def get_account_balance(deps: TradingDeps) -> str:
    """Get account balance with return on initial capital."""
    balance = await deps.exchange.fetch_balance()
    ret_usdt = balance.total_usdt - deps.initial_balance
    ret_pct = (ret_usdt / deps.initial_balance) * 100 if deps.initial_balance > 0 else 0.0
    return (
        f"=== Account Balance ===\n"
        f"Total: {balance.total_usdt:.2f} USDT (initial: {deps.initial_balance:.2f})\n"
        f"Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized)\n"
        f"Free: {balance.free_usdt:.2f} USDT\n"
        f"Used: {balance.used_usdt:.2f} USDT"
    )


async def get_memories(deps: TradingDeps) -> str:
    """Get long-term memories (lessons, patterns, trade reviews)."""
    return await deps.memory.format_for_prompt()


def _render_single_order(o, current: float) -> str:
    """Render a single (non-OCO) order line — preserves pre-Iter-2b rendering exactly.

    Preserves the current > 0 branch: no crash on abnormal ticker. Label/distance/ID
    suffix format matches original tools_perception.py exactly to satisfy spec §6
    "zero byte-level regression".
    """
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
    return f"  {label} {o.side} {o.amount} {price_str} | ID: {o.id}"


async def get_open_orders(deps: TradingDeps) -> str:
    """Get all pending orders with distance from current price."""
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return "=== Pending Orders ===\nNo pending orders."

    ticker = await deps.market_data.get_ticker(deps.symbol)
    current = ticker.last

    # Group by id: OCO's two same-id legs share id + is_algo=True
    by_id: dict[str, list] = {}
    for o in orders:
        by_id.setdefault(o.id, []).append(o)

    lines = ["=== Pending Orders ==="]
    for order_id, group in by_id.items():
        is_oco = (
            len(group) == 2
            and {o.order_type for o in group} == {"stop", "take_profit"}
            and all(o.is_algo for o in group)
        )
        if is_oco:
            sl = next(o for o in group if o.order_type == "stop")
            tp = next(o for o in group if o.order_type == "take_profit")
            sl_dist = (
                f" ({(sl.price - current) / current * 100:+.2f}% from current)"
                if current > 0 else ""
            )
            tp_dist = (
                f" ({(tp.price - current) / current * 100:+.2f}% from current)"
                if current > 0 else ""
            )
            lines.append(
                f"  [OCO] {sl.side} {sl.amount} "
                f"stop {sl.price:.2f}{sl_dist} / tp {tp.price:.2f}{tp_dist} "
                f"| algoId: {order_id} (cancel removes both legs)"
            )
        else:
            for o in group:
                lines.append(_render_single_order(o, current))
    return "\n".join(lines)


async def get_trade_journal(deps: TradingDeps, limit: int = 20) -> str:
    """Get trade journal — decision timeline with quick stats summary.
    Use for reviewing recent decisions and their outcomes."""
    if deps.db_engine is None:
        return (
            "=== Trade Journal ===\n"
            "No trade journal entries yet."
        )
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
        return (
            "=== Trade Journal ===\n"
            "No trade journal entries yet."
        )

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
            lines.append(f'  #{i} (id={a["id"]}) {a["direction"]} {a["price"]:.2f} — "{a["reasoning"]}"')
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
        # L3 by-design empty state (NOT an error): no metrics service available.
        # Trading Performance section still renders balance fields; Trade Stats
        # section emitted as placeholder so the schema is consistent.
        perf_section = (
            f"=== Trading Performance ===\n"
            f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
            f"Current Balance: {balance.total_usdt:.2f} USDT\n"
            f"Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT)"
        )
        stats_section = (
            "=== Trade Stats ===\n"
            "No metrics service available."
        )
        return f"{perf_section}\n\n{stats_section}"

    metrics = await deps.metrics.compute()

    if metrics.total_trades == 0:
        # L3 by-design empty state (NOT an error): no completed trades yet.
        perf_section = (
            f"=== Trading Performance ===\n"
            f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
            f"Current Balance: {balance.total_usdt:.2f} USDT\n"
            f"Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT)"
        )
        stats_section = (
            "=== Trade Stats ===\n"
            "No completed trades yet."
        )
        return f"{perf_section}\n\n{stats_section}"

    fees_line = (
        f"Total Fees: -{metrics.total_fees:.2f} USDT"
        if metrics.total_fees > 0
        else "Total Fees: 0.00 USDT"
    )

    perf_section = (
        f"=== Trading Performance ===\n"
        f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
        f"Current Balance: {balance.total_usdt:.2f} USDT\n"
        f"Total Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized)\n"
        f"Realized PnL: {metrics.total_pnl:+.2f} USDT (gross, before fees)\n"
        f"{fees_line}"
    )

    stats_section = (
        f"=== Trade Stats ===\n"
        f"Total Trades: {metrics.total_trades} | Win: {metrics.winning_trades} "
        f"({metrics.win_rate:.1%}) | Loss: {metrics.losing_trades}\n"
        f"Avg Win: {metrics.avg_win:+.2f} USDT | Avg Loss: {metrics.avg_loss:.2f} USDT\n"
        f"Profit Factor: {'N/A (no losses)' if metrics.profit_factor == float('inf') else f'{metrics.profit_factor:.2f}'}\n"
        f"Max Drawdown: {f'-{metrics.max_drawdown_pct:.1f}' if metrics.max_drawdown_pct > 0 else '0.0'}%\n"
        f"Best Trade: {metrics.best_trade:+.2f} USDT | Worst Trade: {metrics.worst_trade:.2f} USDT"
    )

    return f"{perf_section}\n\n{stats_section}"


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
        return (
            "=== News ===\n"
            "Error: News service not configured."
        )

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


async def get_exchange_announcements(
    deps: TradingDeps,
    lookback_hours: int = 24,
) -> str:
    """Get recent exchange announcements (maintenance, delistings, parameter changes)."""
    if deps.news is None:
        return (
            "=== Exchange Announcements ===\n"
            "Error: News service not configured."
        )

    try:
        announcements = await deps.news.get_announcements(lookback_hours)
    except Exception:
        announcements = None

    if announcements is None:
        return (
            f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
            "Error: Exchange announcements service temporarily unavailable."
        )
    if announcements:
        lines = [e.timestamp.strftime("[%Y-%m-%d %H:%M] ") + e.title for e in announcements]
        return (
            f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
            + "\n".join(lines)
        )
    return (
        f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
        "No exchange announcements."
    )


async def get_macro_calendar(
    deps: TradingDeps,
    lookahead_hours: int = 12,
) -> str:
    """Get upcoming macro events (FOMC, CPI, NFP) with impact level.

    Footer rule: shown when macro_events is a list (incl. []) so the scope
    caveat qualifies a real result; suppressed when macro_events is None
    (no result to qualify, per spec §3.4).
    """
    if deps.news is None:
        return (
            "=== Upcoming Macro Events ===\n"
            "Error: News service not configured."
        )

    try:
        macro_events = await deps.news.get_macro_events(lookahead_hours)
    except Exception:
        macro_events = None

    sections: list[str] = []

    if macro_events is None:
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
            "Error: Temporarily unavailable."
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

    # Footer: shown when macro_events is a list; suppressed when None.
    if macro_events is not None:
        sections.append(
            "=== Note ===\n"
            "Macro calendar covers current week only; "
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
    field_lines: list[str] = []
    timestamps_ms: list[int] = []

    # Fetch all three concurrently — each has independent cache + upstream.
    # gather(return_exceptions=True) gives us per-method success/failure.
    funding, oi, lsr = await asyncio.gather(
        deps.market_data.get_funding_rate(symbol),
        deps.market_data.get_open_interest(symbol),
        deps.market_data.get_long_short_ratio(symbol),
        return_exceptions=True,
    )

    # All-3-failed L2: emit single Error section.
    if (
        isinstance(funding, Exception)
        and isinstance(oi, Exception)
        and isinstance(lsr, Exception)
    ):
        return (
            f"=== Derivatives Data ({symbol}) ===\n"
            f"Error: Temporarily unavailable (all 3 data sources failed)."
        )

    # Funding rate (per-field L3 fallback for partial failure)
    if isinstance(funding, Exception):
        field_lines.append("Funding Rate: (unavailable)")
    else:
        direction = "longs pay shorts" if funding.rate >= 0 else "shorts pay longs"
        sign = "Positive rate" if funding.rate >= 0 else "Negative rate"
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        remaining_ms = max(0, funding.next_funding_time - now_ms)
        hours = remaining_ms // (3600 * 1000)
        minutes = (remaining_ms % (3600 * 1000)) // (60 * 1000)
        field_lines.append(
            f"Funding Rate: {funding.rate:+.4%} (next settlement in {hours}h {minutes}m)"
        )
        field_lines.append(f"  {sign} — {direction}")
        if funding.timestamp:
            timestamps_ms.append(funding.timestamp)

    # Open interest
    if isinstance(oi, Exception):
        field_lines.append("Open Interest: (unavailable)")
    else:
        if oi.open_interest_value >= 1e9:
            oi_str = f"${oi.open_interest_value / 1e9:.2f}B"
        elif oi.open_interest_value >= 1e6:
            oi_str = f"${oi.open_interest_value / 1e6:.2f}M"
        else:
            oi_str = f"${oi.open_interest_value:,.0f}"
        field_lines.append(f"Open Interest: {oi_str}")
        if oi.timestamp:
            timestamps_ms.append(oi.timestamp)

    # Long/short ratio
    if isinstance(lsr, Exception):
        field_lines.append("Long/Short Ratio: (unavailable)")
    else:
        field_lines.append(
            f"Long/Short Ratio: {lsr.long_short_ratio:.2f} "
            f"({lsr.long_ratio:.1%} long / {lsr.short_ratio:.1%} short)"
        )
        if lsr.timestamp:
            timestamps_ms.append(lsr.timestamp)

    # Show the oldest upstream timestamp across the 3 fetches — this is the
    # lower bound of data age (i.e. "at least one slice is this old"), giving
    # the Agent a worst-case freshness signal. It reflects upstream response
    # age, not whether TTLCache served an extended stale-fallback entry.
    if timestamps_ms:
        oldest_dt = datetime.fromtimestamp(min(timestamps_ms) / 1000, tz=timezone.utc)
        field_lines.append(f"Data as of: {oldest_dt.strftime('%Y-%m-%d %H:%M')} UTC")

    return f"=== Derivatives Data ({symbol}) ===\n" + "\n".join(field_lines)


# Unit labels for "N periods ago" rendered below range highs/lows.
_UNIT_LABEL = {"4h": "4h-bars", "1d": "days", "1w": "weeks", "1M": "months"}
_UNIT_LABEL_SINGULAR = {"4h": "4h-bar", "1d": "day", "1w": "week", "1M": "month"}


def _htf_ago_fmt(n: int, timeframe: Literal["4h", "1d", "1w", "1M"]) -> str:
    """Render the 'N periods ago' suffix with proper latest/singular/plural
    grammar (spec §3.5 M1). 0 periods ago renders as 'latest' (the max/min
    landed on the most recent bar); 1 period uses the singular label; N>=2
    uses the plural label. Placed at module scope alongside _UNIT_LABEL*
    for consistency with other HTF helpers."""
    if n == 0:
        return "latest"
    if n == 1:
        return f"1 {_UNIT_LABEL_SINGULAR[timeframe]} ago"
    return f"{n} {_UNIT_LABEL[timeframe]} ago"


async def get_higher_timeframe_view(
    deps: TradingDeps,
    timeframes: list[Literal["4h", "1d", "1w", "1M"]] | None = None,
) -> str:
    """Long-term structural view across one or more higher timeframes: ticker (authoritative live price), per-tf MA50/MA100/MA200 with raw value, price-vs-MA percentage, and MA slope (10-bar lookback); MA stack comparison; 100-period high and low with bars-ago and the candle open timestamp; range position within 100-period; 20-period high-low range width; last-bar volume vs 20-period SMA ratio (base volume); ATR(14) raw, percent of price, and ratio vs 20-period ATR average.

    All moving averages are simple moving averages (SMA) computed on the closed-bar series only (excluding the in-progress bar). The slope reference and all rolling averages use the closed-candle series. ATR(14) is computed via _atr_series (mamode='rma' algorithm lock per spec §6.4.2).

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

    Related perception tools (factual capability surface, not a calling order):
        - get_multi_timeframe_snapshot: cross-timeframe alignment overview — authoritative ticker, per-tf MA fast-vs-slow direction count, per-tf momentum / structure with raw MA values / volatility ratio / range position / 3 closed candle closes.
        - get_market_data: single-timeframe depth output — full RSI / MACD / BB / Volume ratio indicators, market context, a 30-candle OHLCV table with anomaly markers, and a period summary (last 5 vs prior 5 closed candles).
    """
    import asyncio
    import pandas as pd
    from datetime import datetime, timezone

    from src.utils.ohlcv_utils import _live_price, _closed_bars, _atr_series

    symbol = deps.symbol
    if timeframes is None:
        timeframes = ["4h", "1d"]

    try:
        ticker = await deps.market_data.get_ticker(symbol)
        live_price = _live_price(ticker)
    except Exception:
        logger.warning("HTF ticker fetch failed for %s", symbol, exc_info=True)
        return f"=== Higher Timeframe View ({symbol}) ===\nError: Temporarily unavailable."

    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    async def _fetch_one(tf: str) -> tuple[str, pd.DataFrame | Exception]:
        try:
            df = await deps.market_data.get_ohlcv_dataframe(symbol, tf, limit=HTF_OHLCV_LIMIT)
            return tf, df
        except Exception as e:
            return tf, e

    results = await asyncio.gather(*[_fetch_one(tf) for tf in timeframes])

    sections: list[str] = [
        f"=== Higher Timeframe View ({symbol} @ {fetch_ts} UTC) ===",
        f"Last: {live_price:.2f}",
        "",
    ]

    for tf, df_or_err in results:
        ma_periods = HTF_MA_PERIODS.get(tf, (50, 100, 200))
        fast_n, mid_n, slow_n = ma_periods

        if isinstance(df_or_err, Exception):
            sections.append(f"[{tf}] Error: Temporarily unavailable.")
            sections.append("")
            continue

        df = df_or_err
        if df.empty or len(df) < slow_n + 1:
            sections.append(
                f"[{tf}] insufficient data (need {slow_n + 1} candles, got {len(df)})"
            )
            sections.append("")
            continue

        df_closed = _closed_bars(df)
        # Header — last closed candle timestamp
        last_ts_ms = int(df_closed["timestamp"].iloc[-1])
        last_dt = datetime.fromtimestamp(last_ts_ms / 1000, tz=timezone.utc)
        if tf == "1M":
            header = (
                f"[{tf}] (last closed candle: open {last_dt.strftime('%Y-%m-%d %H:%M')} UTC; "
                f"MA periods {fast_n}/{mid_n}/{slow_n} = 1y/2y/5y monthly — "
                f"adapted for crypto-industry monthly cycle conventions)"
            )
        else:
            header = f"[{tf}] (last closed candle: open {last_dt.strftime('%Y-%m-%d %H:%M')} UTC)"
        sections.append(header)

        # MA lines — fast / mid / slow with slope
        close = df_closed["close"]
        def _ma_line(n: int) -> str:
            if len(df_closed) < n + 10:
                return f"  MA{n}: insufficient data (need {n + 10} candles)"
            ma_now = float(close.rolling(n).mean().iloc[-1])
            ma_then = float(close.rolling(n).mean().iloc[-11])
            slope_pct = (ma_now - ma_then) / ma_then * 100.0 if ma_then > 0 else 0.0
            dist_pct = (live_price - ma_now) / ma_now * 100.0
            return (
                f"  MA{n}: {ma_now:.2f}  (price vs MA: {dist_pct:+.1f}%; "
                f"MA slope vs 10 bars ago: {slope_pct:+.1f}%)"
            )

        ma_fast_line = _ma_line(fast_n)
        ma_mid_line = _ma_line(mid_n)
        ma_slow_line = _ma_line(slow_n)
        sections.extend([ma_fast_line, ma_mid_line, ma_slow_line])

        # MA stack
        try:
            ma_vals = {
                fast_n: float(close.rolling(fast_n).mean().iloc[-1]),
                mid_n: float(close.rolling(mid_n).mean().iloc[-1]),
                slow_n: float(close.rolling(slow_n).mean().iloc[-1]),
            }
            ordered = sorted(ma_vals.items(), key=lambda kv: -kv[1])
            ops: list[str] = []
            for (_, va), (_, vb) in zip(ordered, ordered[1:]):
                # 0.1% tolerance per spec §5.3: MAs within 0.1% collapse to "≈".
                rel_diff = abs(va - vb) / vb if vb > 0 else 0.0
                ops.append("≈" if rel_diff < 0.001 else ">")
            stack_str = " ".join(
                [f"MA{ordered[0][0]}"]
                + [f"{ops[i]} MA{ordered[i + 1][0]}" for i in range(len(ops))]
            )
            sections.append(f"  MA stack: {stack_str}")
        except Exception:
            sections.append("  MA stack: insufficient data")

        # 100-period range
        if len(df_closed) >= 100:
            last_100 = df_closed.iloc[-100:].reset_index(drop=True)
            hi_idx = int(last_100["high"].idxmax())
            lo_idx = int(last_100["low"].idxmin())
            hi100 = float(last_100["high"].max())
            lo100 = float(last_100["low"].min())
            hi_ago = 99 - hi_idx
            lo_ago = 99 - lo_idx
            hi_ts = datetime.fromtimestamp(int(last_100["timestamp"].iloc[hi_idx]) / 1000, tz=timezone.utc)
            lo_ts = datetime.fromtimestamp(int(last_100["timestamp"].iloc[lo_idx]) / 1000, tz=timezone.utc)
            rng_pos = ((live_price - lo100) / (hi100 - lo100) * 100.0) if hi100 != lo100 else 0.0
            sections.extend([
                f"  100-period High: {hi100:.2f}  ({hi_ago} bars ago, candle open {hi_ts.strftime('%Y-%m-%d %H:%M')} UTC)",
                f"  100-period Low:  {lo100:.2f}  ({lo_ago} bars ago, candle open {lo_ts.strftime('%Y-%m-%d %H:%M')} UTC)",
                f"  Range pos (within 100-period): {rng_pos:.0f}%  (0%=Low, 100%=High)",
            ])

        # 20-period band
        if len(df_closed) >= 20:
            last_20 = df_closed.iloc[-20:]
            hi20 = float(last_20["high"].max())
            lo20 = float(last_20["low"].min())
            width_pct = (hi20 - lo20) / lo20 * 100.0 if lo20 > 0 else 0.0
            sections.append(
                f"  20-period High: {hi20:.2f} / Low: {lo20:.2f} / range width: {width_pct:.1f}% (= (High-Low)/Low)"
            )

        # Last bar vol regime
        if len(df_closed) >= 21:
            vol_now = float(df_closed["volume"].iloc[-1])
            vol_avg_20 = float(df_closed["volume"].iloc[-20:].mean())
            ratio = vol_now / vol_avg_20 if vol_avg_20 > 0 else 0.0
            sections.append(f"  Last bar vol (base): {vol_now:.1f}  ({ratio:.1f}× SMA(20) avg)")

        # ATR regime
        if len(df_closed) >= 35:  # 14 ATR window + 20 ATR-avg window + 1
            atr_series = _atr_series(df_closed, period=14)
            atr_now = float(atr_series.iloc[-1])
            atr_avg = float(atr_series.rolling(20).mean().iloc[-1])
            atr_pct = atr_now / live_price * 100.0 if live_price > 0 else 0.0
            atr_ratio = atr_now / atr_avg if atr_avg > 0 else 0.0
            sections.append(
                f"  ATR(14): {atr_now:.2f}  ({atr_pct:.2f}% of price; "
                f"{atr_ratio:.2f}× vs 20-period ATR(14) avg)"
            )

        sections.append("")

    return "\n".join(sections).rstrip()


def _fmt_signed_dollars(v: float) -> str:
    """Format a signed dollar amount in $M or $B (spec §3.3 output format)."""
    abs_v = abs(v)
    sign = "+" if v >= 0 else "-"
    if abs_v >= 1e9:
        return f"{sign}${abs_v / 1e9:,.2f}B"
    if abs_v >= 1e6:
        return f"{sign}${abs_v / 1e6:,.2f}M"
    return f"{sign}${abs_v:,.0f}"


def _fmt_big_usd(v: float) -> str:
    """Positive-only T/B/M formatter for cumulative AUM, totals."""
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


def _fmt_pct(v: float | None) -> str:
    """Render a 7-day percentage change, tolerating None.

    Returns 'N/A (no prior-week data)' when pct is None (OnchainService sets
    change_7d_pct / total_change_7d_pct to None when prev_week == 0; §3.5 M3).
    Sibling to _fmt_big_usd / _fmt_signed_dollars — module-level helper,
    not inner-defined in get_stablecoin_supply.
    """
    if v is None:
        return "N/A (no prior-week data)"
    return f"{v:+.2f}%"


async def get_macro_context(deps: TradingDeps) -> str:
    """Cross-market macro snapshot: crypto totals + FRED + US equities.

    Output is fact-only (spec §3.2): no 'strong dollar' / 'risk-on' labels.
    FRED values include `as of YYYY-MM-DD` so the Agent sees each reading's
    real observation date (DTWEXBGS has ~1-week report delay).
    """
    if deps.macro is None:
        return (
            "=== Macro Context ===\n"
            "Error: Macro service not configured."
        )

    try:
        snap = await deps.macro.get_snapshot()
    except Exception:
        logger.warning("Macro snapshot fetch failed", exc_info=True)
        return (
            "=== Macro Context ===\n"
            "Error: Temporarily unavailable."
        )

    sections: list[str] = []
    any_available = False

    # Crypto Market
    cg_fields = (snap.btc_dominance, snap.eth_dominance,
                 snap.total_mcap_usd, snap.mcap_change_24h_pct)
    if all(v is None for v in cg_fields):
        sections.append("=== Crypto Market ===\nTemporarily unavailable.")
    else:
        any_available = True
        btc = f"{snap.btc_dominance:.2f}%" if snap.btc_dominance is not None else "N/A"
        eth = f"{snap.eth_dominance:.2f}%" if snap.eth_dominance is not None else "N/A"
        mcap = _fmt_big_usd(snap.total_mcap_usd) if snap.total_mcap_usd else "N/A"
        chg = f"{snap.mcap_change_24h_pct:+.2f}%" if snap.mcap_change_24h_pct is not None else "N/A"
        sections.append(
            "=== Crypto Market ===\n"
            f"BTC.D: {btc} | ETH.D: {eth} | Total Mcap: {mcap} (24h: {chg})"
        )

    # US Macro (FRED)
    fred_fields = (snap.usd_index_broad_tw, snap.vix, snap.treasury_10y,
                   snap.spread_10y_2y, snap.inflation_10y)
    if all(v is None for v in fred_fields):
        sections.append("=== US Macro (FRED) ===\nTemporarily unavailable.")
    else:
        any_available = True
        lines = ["=== US Macro (FRED) ==="]
        if snap.usd_index_broad_tw is not None:
            o = snap.usd_index_broad_tw
            lines.append(f"USD Index (Broad TW): {o.value:.2f} (as of {o.date})")
        if snap.vix is not None:
            o = snap.vix
            lines.append(f"VIX: {o.value:.2f} (as of {o.date})")
        if snap.treasury_10y is not None:
            o = snap.treasury_10y
            lines.append(f"10Y Treasury: {o.value:.2f}% (as of {o.date})")
        if snap.spread_10y_2y is not None:
            o = snap.spread_10y_2y
            lines.append(f"2s10s Spread: {o.value:+.2f}% (as of {o.date})")
        if snap.inflation_10y is not None:
            o = snap.inflation_10y
            lines.append(f"10Y Inflation Expectation: {o.value:.2f}% (as of {o.date})")
        sections.append("\n".join(lines))

    # US Equities (Alpha Vantage)
    # AV's change percent is close-to-previous-close for the latest trading
    # day (NOT a rolling 24h window — weekends/holidays would render Friday
    # vs Thursday). Drop the misleading "24h:" label and include the actual
    # trading day via `as of`, matching the FRED section's freshness anchor.
    if snap.spy is None and snap.qqq is None:
        sections.append("=== US Equities (Alpha Vantage) ===\nTemporarily unavailable.")
    else:
        any_available = True
        lines = ["=== US Equities (Alpha Vantage) ==="]
        if snap.spy is not None:
            lines.append(
                f"SPY: ${snap.spy.price:,.2f} "
                f"({snap.spy.change_pct:+.2f}%, as of {snap.spy.latest_trading_day})"
            )
        if snap.qqq is not None:
            lines.append(
                f"QQQ: ${snap.qqq.price:,.2f} "
                f"({snap.qqq.change_pct:+.2f}%, as of {snap.qqq.latest_trading_day})"
            )
        sections.append("\n".join(lines))

    if not any_available:
        return (
            "=== Macro Context ===\n"
            "Error: All sources temporarily unavailable."
        )

    return "\n\n".join(sections)


async def get_etf_flows(deps: TradingDeps, days: int = 7) -> str:
    """US BTC + ETH spot ETF daily net flows + cumulative AUM.

    Emits a trailing footer reminding the Agent that today's value may be
    revised T+1 — this is an operational fact (spec §3.6) needed in-context
    to avoid misreading same-day values.
    """
    if deps.crypto_etf is None:
        return (
            "=== BTC Spot ETF Flows (US) ===\n"
            "Error: ETF flows service not configured."
        )

    # `days` parameter is clamped in CryptoEtfService.get_etf_flows
    # (src/integrations/crypto_etf/service.py:47) — single source of truth.
    # The footer below derives the rendered day-count from the service's
    # actual result lengths, NOT the user-supplied `days`, so over-range
    # requests (e.g., days=30) render "Past 14 trading days" consistent
    # with the clamped value rather than the misleading "Past 30".

    import asyncio

    btc_result, eth_result = await asyncio.gather(
        deps.crypto_etf.get_etf_flows("BTC", days),
        deps.crypto_etf.get_etf_flows("ETH", days),
        return_exceptions=True,
    )
    btc = None if isinstance(btc_result, Exception) else btc_result
    eth = None if isinstance(eth_result, Exception) else eth_result

    def _render_section(label: str, flows) -> str:
        # Three-state rendering per spec §3.5:
        #   None → outage ("temporarily unavailable")
        #   []   → data-gap ("insufficient data" — window too short)
        #   list → normal
        if flows is None:
            return f"=== {label} Spot ETF Flows (US) ===\nTemporarily unavailable."
        if not flows:
            return (
                f"=== {label} Spot ETF Flows (US) ===\n"
                f"Insufficient data in requested window."
            )
        lines = [f"=== {label} Spot ETF Flows (US) ==="]
        net_total = 0.0
        for i, entry in enumerate(flows):
            # First row also shows cumulative net-inflow and end-of-day AUM
            # (total net assets). Both are fields on ETFFlowEntry sourced from
            # SoSoValue — AUM lets the agent gauge fund size alongside flow.
            suffix = (
                f"  (cum: {_fmt_big_usd(entry.cumulative_usd)}, "
                f"AUM: {_fmt_big_usd(entry.aum_usd)})"
            ) if i == 0 else ""
            lines.append(
                f"{entry.date}: {_fmt_signed_dollars(entry.net_inflow_usd)}{suffix}"
            )
            net_total += entry.net_inflow_usd
        lines.append(f"{len(flows)}-day net: {_fmt_signed_dollars(net_total)}")
        return "\n".join(lines)

    sections = [
        _render_section("BTC", btc),
        _render_section("ETH", eth),
    ]

    if btc is None and eth is None:
        return (
            "=== BTC Spot ETF Flows (US) ===\n"
            "Error: Temporarily unavailable."
        )

    # Footer: operational facts the Agent needs in-context (spec §3.6).
    # The trading-day count is derived from the service's actual result
    # length — under the M2 single-clamp regime (§3.5), the clamp expression
    # lives only in CryptoEtfService.get_etf_flows:47 and the tool layer
    # reads the clamped outcome back from the result to keep the clamp
    # logic in one place (DRY). When btc and eth are both non-empty,
    # invariant len(btc) == len(eth) holds (same clamp + same parallel
    # fetch path in CryptoEtfService); pick whichever is non-empty to read
    # the rendered day count. Footer is emitted only when at least one
    # side rendered flow rows — a mix of outage (None) + data-gap ([]) has
    # no "today's value" for the T+1 caveat to refer to, so suppressing
    # the footer avoids misleading noise.
    if btc or eth:
        days_rendered = len(next((f for f in (btc, eth) if f), []))
        sections.append(
            "=== Note ===\n"
            f"Past {days_rendered} trading days (weekends/holidays excluded). "
            "Issuer-reported; today's value may be revised T+1."
        )

    return "\n\n".join(sections)


async def get_stablecoin_supply(deps: TradingDeps) -> str:
    """USDT + USDC total supply + 7-day change.

    Output is fact-only (spec §3.4): no 'dry powder' / 'capital entering'.
    """
    if deps.onchain is None:
        return (
            "=== Stablecoin Supply ===\n"
            "Error: Onchain service not configured."
        )

    try:
        result = await deps.onchain.get_stablecoin_snapshot()
    except Exception:
        logger.warning("Stablecoin snapshot fetch failed", exc_info=True)
        return (
            "=== Stablecoin Supply ===\n"
            "Error: Temporarily unavailable."
        )

    if result is None:
        return (
            "=== Stablecoin Supply ===\n"
            "Error: Temporarily unavailable."
        )

    if not result["coins"]:
        # Guard against upstream schema drift (e.g. DefiLlama renaming USDT →
        # USDT0): neither tracked symbol matched, so totals would render as
        # $0.00 — misleading. Signal "data unavailable" instead.
        return (
            "=== Stablecoin Supply ===\n"
            "Error: Data unavailable (no tracked symbols found in response)."
        )

    lines = ["=== Stablecoin Supply ==="]
    for coin in result["coins"]:
        lines.append(
            f"{coin.symbol}: {_fmt_big_usd(coin.circulating_usd)} "
            f"(7d: {_fmt_signed_dollars(coin.change_7d_usd)}, "
            f"{_fmt_pct(coin.change_7d_pct)})"
        )
    total = result["total"]
    lines.append(
        f"Total Stablecoin Mcap: {_fmt_big_usd(total.total_circulating_usd)} "
        f"(7d: {_fmt_signed_dollars(total.total_change_7d_usd)}, "
        f"{_fmt_pct(total.total_change_7d_pct)})"
    )

    return "\n".join(lines)


async def get_order_book(deps: TradingDeps, depth: int = ORDER_BOOK_DEPTH_DEFAULT) -> str:
    """Return top-N order book depth with concentrated-level breakdown.

    Args:
        depth: Levels per side to fetch. Default 20.

    Returns:
        str: Multi-line fact-only text (best bid/ask + cumulative depth + bid share + concentrated levels). See spec §2.1.

    Degradation: Returns "Order book ({symbol}): insufficient data (requested depth X, got Y)" if book is empty/short;
    "Order book ({symbol}): temporarily unavailable" on service failure.
    """
    symbol = deps.symbol
    # Extract base currency for unit labels (e.g. "BTC" from "BTC/USDT:USDT");
    # avoids hardcoded "BTC" when system later supports ETH/USDT:USDT etc.
    base_currency = symbol.split("/")[0]
    try:
        ob = await deps.market_data.get_order_book(symbol, depth=depth)
    except Exception:
        logger.exception("get_order_book failed for %s", symbol)
        return (
            f"=== Order Book ({symbol}) ===\n"
            "Error: Temporarily unavailable."
        )

    actual = min(len(ob.bids), len(ob.asks))
    if not ob.bids or not ob.asks or actual < depth:
        return (
            f"=== Order Book ({symbol}) ===\n"
            f"Error: Insufficient data (requested depth {depth}, got {actual})."
        )

    best_bid = ob.bids[0]
    best_ask = ob.asks[0]
    mid = (best_bid.price + best_ask.price) / 2
    spread = best_ask.price - best_bid.price
    spread_pct = spread / mid * 100

    total_bid = sum(l.amount for l in ob.bids[:depth])
    total_ask = sum(l.amount for l in ob.asks[:depth])
    total_sum = total_bid + total_ask
    # Spec §2.1 — all-zero amounts across both sides: degrade to insufficient data
    # (real OKX / Sim cannot produce this, but spec mandates explicit guard)
    if total_sum == 0:
        return (
            f"=== Order Book ({symbol}) ===\n"
            f"Error: Insufficient data (requested depth {depth}, got {actual})."
        )
    bid_deep_pct = (ob.bids[0].price - ob.bids[depth - 1].price) / ob.bids[0].price * 100
    ask_deep_pct = (ob.asks[depth - 1].price - ob.asks[0].price) / ob.asks[0].price * 100

    # Bid share three-state
    if total_bid == 0 and total_ask > 0:
        share_line = "Bid share: 0% (asks only, no bids in top {})".format(depth)
    elif total_ask == 0 and total_bid > 0:
        share_line = "Bid share: 100% (bids only, no asks in top {})".format(depth)
    else:
        bid_share = total_bid / total_sum * 100
        if abs(bid_share - 50) < ORDER_BOOK_BALANCED_THRESHOLD_PCT:
            # Spec §2.1 — fixed '~50%' label when within balanced threshold, not actual value.
            # Actual value on a balanced output creates a conflicting signal
            # ("Bid share: ~47% (balanced)" mixes precise percentage with the approximation marker).
            share_line = "Bid share: ~50% (balanced)"
        else:
            bid_ratio = total_bid / total_ask if total_ask > 0 else float("inf")
            share_line = f"Bid share: {bid_share:.1f}% (bid : ask = {bid_ratio:.2f} : 1)"

    sections = [
        (
            f"=== Order Book ({symbol}) ===\n"
            f"Best bid: {best_bid.price:.2f} × {best_bid.amount:.4f} {base_currency}  |  Best ask: {best_ask.price:.2f} × {best_ask.amount:.4f} {base_currency}\n"
            f"Spread: {spread:.2f} ({spread_pct:.3f}%)"
        ),
        (
            f"=== Depth (top {depth} each side) ===\n"
            f"  Bids cumulative: {total_bid:.4f} {base_currency} over {best_bid.price:.2f} - {ob.bids[depth-1].price:.2f} ({bid_deep_pct:.2f}% deep)\n"
            f"  Asks cumulative: {total_ask:.4f} {base_currency} over {best_ask.price:.2f} - {ob.asks[depth-1].price:.2f} ({ask_deep_pct:.2f}% deep)\n"
            f"  {share_line}"
        ),
    ]

    # Concentrated levels (per-side median)
    import statistics
    bid_amounts = [l.amount for l in ob.bids[:depth]]
    ask_amounts = [l.amount for l in ob.asks[:depth]]
    bid_median = statistics.median(bid_amounts)
    ask_median = statistics.median(ask_amounts)
    threshold_bid = bid_median * ORDER_BOOK_CONCENTRATION_MULTIPLIER
    threshold_ask = ask_median * ORDER_BOOK_CONCENTRATION_MULTIPLIER

    concentrated = []
    for l in ob.bids[:depth]:
        if l.amount > threshold_bid:
            concentrated.append(("Bid", l.price, l.amount, (mid - l.price) / mid * 100, True))
    for l in ob.asks[:depth]:
        if l.amount > threshold_ask:
            concentrated.append(("Ask", l.price, l.amount, (l.price - mid) / mid * 100, False))

    if concentrated:
        # Sort top-10 by amount desc, then restore display order (bids-then-asks, nearest-to-mid first)
        concentrated.sort(key=lambda c: c[2], reverse=True)
        concentrated = concentrated[:ORDER_BOOK_MAX_CONCENTRATED_LEVELS]
        bids_conc = sorted([c for c in concentrated if c[0] == "Bid"], key=lambda c: -c[1])  # price desc
        asks_conc = sorted([c for c in concentrated if c[0] == "Ask"], key=lambda c: c[1])   # price asc
        conc_header = (
            f"=== Concentrated Levels "
            f"(size > {ORDER_BOOK_CONCENTRATION_MULTIPLIER:.0f}× median of top {depth}) ==="
        )
        conc_rows = []
        for side, price, amount, dist_pct, is_bid in bids_conc + asks_conc:
            direction = "below mid" if is_bid else "above mid"
            conc_rows.append(f"  {side}  {price:.2f}  {amount:.4f} {base_currency}  ({dist_pct:.2f}% {direction})")
        sections.append(conc_header + "\n" + "\n".join(conc_rows))

    return "\n\n".join(sections)


async def get_recent_trades(deps: TradingDeps, window_seconds: int = RECENT_TRADES_WINDOW_DEFAULT) -> str:
    """Return taker-flow bias and rhythm over a recent time window via 5 time-buckets.

    Args:
        window_seconds: Observation window in seconds. Default 300 (5 min).

    Returns:
        str: 5-bucket breakdown + Total + trade count + avg size. See spec §2.2.

    Degradation: "no trades in last {window_seconds}s" if cold market; "temporarily unavailable" on service failure.
    """
    import time
    symbol = deps.symbol
    base_currency = symbol.split("/")[0]
    try:
        trades = await deps.market_data.get_recent_trades(symbol, limit=RECENT_TRADES_MAX_FETCH)
    except Exception:
        logger.exception("get_recent_trades failed for %s", symbol)
        return (
            f"=== Recent Trades ({symbol}) ===\n"
            f"Error: Temporarily unavailable."
        )

    if not trades:
        return (
            f"=== Recent Trades ({symbol}, last {window_seconds}s) ===\n"
            f"No trades in last {window_seconds}s."
        )

    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    bucket_duration_ms = window_ms // RECENT_TRADES_BUCKET_COUNT

    # Allocate trades to buckets (0 = oldest, 4 = newest); drop over-window
    buckets: list[list[Trade]] = [[] for _ in range(RECENT_TRADES_BUCKET_COUNT)]
    in_window: list[Trade] = []
    for t in trades:
        age_ms = now_ms - t.timestamp
        # Skip out-of-window trades:
        # - age_ms >= window_ms: too old (strict >= prevents bucket_idx = -1 on boundary)
        # - age_ms < 0: future-timestamped (server clock ahead of local clock).
        #   Python floor division gives `-5000 // 60_000 == -1`, which would compute
        #   bucket_idx = 5 — out of bounds on a 5-element list (IndexError). NTP-level
        #   clock skew of a few hundred ms is common in practice; skip rather than
        #   silently clamp so genuine clock-sync failures stay visible.
        if age_ms >= window_ms or age_ms < 0:
            continue
        bucket_idx = RECENT_TRADES_BUCKET_COUNT - 1 - (age_ms // bucket_duration_ms)
        buckets[bucket_idx].append(t)
        in_window.append(t)

    if not in_window:
        return (
            f"=== Recent Trades ({symbol}, last {window_seconds}s) ===\n"
            f"No trades in last {window_seconds}s."
        )

    lines = [f"=== Recent Trades ({symbol}, last {window_seconds}s, {RECENT_TRADES_BUCKET_COUNT} × {bucket_duration_ms // 1000}s buckets) ==="]
    total_buy = 0.0
    total_sell = 0.0
    for i, bucket in enumerate(buckets):
        buy_vol = sum(t.amount for t in bucket if t.side == "buy")
        sell_vol = sum(t.amount for t in bucket if t.side == "sell")
        net = buy_vol - sell_vol
        total_buy += buy_vol
        total_sell += sell_vol
        # Label: for standard 300s/5-bucket → t-5min to t-1min; otherwise bucket {i+1}/N ({start_s}-{end_s}s ago)
        if window_seconds == 300:
            label = f"t-{RECENT_TRADES_BUCKET_COUNT - i}min"
        else:
            start_s = (RECENT_TRADES_BUCKET_COUNT - i - 1) * (bucket_duration_ms // 1000)
            end_s = (RECENT_TRADES_BUCKET_COUNT - i) * (bucket_duration_ms // 1000)
            label = f"bucket {i+1}/{RECENT_TRADES_BUCKET_COUNT} ({start_s}-{end_s}s ago)"
        lines.append(f"  {label}  buy {buy_vol:.4f} / sell {sell_vol:.4f}  (net {net:+.4f})")

    total_vol = total_buy + total_sell
    buy_pct = total_buy / total_vol * 100 if total_vol > 0 else 0.0
    net_total = total_buy - total_sell
    total_label = f"Total: buy {total_buy:.4f} / sell {total_sell:.4f} (net {net_total:+.4f}, {buy_pct:.0f}% taker buy)"

    # Partial coverage double-condition
    fetch_ratio = len(trades) / RECENT_TRADES_MAX_FETCH
    oldest_age_ms = max(now_ms - t.timestamp for t in in_window)
    oldest_age_ratio = oldest_age_ms / window_ms
    if fetch_ratio >= 0.95 and oldest_age_ratio < 0.95:
        total_label = f"Total: buy {total_buy:.4f} / sell {total_sell:.4f} (net {net_total:+.4f}*, {buy_pct:.0f}% taker buy) [* partial coverage: {len(trades)} trades at limit, oldest age {oldest_age_ms//1000}s ({oldest_age_ratio:.0%} of window), window not fully covered]"

    lines.append(total_label)
    lines.append(f"Trade count: {len(in_window)} | Avg size: {total_vol / len(in_window):.4f} {base_currency}")
    return "\n".join(lines)


async def get_multi_timeframe_snapshot(deps: TradingDeps, tfs: list[str] | None = None) -> str:
    """Quick multi-timeframe scan: momentum | structure | volatility | range position.

    Args:
        tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"].

    Returns:
        str: 4-column row per TF + Columns header. See spec §2.3.

    Degradation: per-TF "insufficient data" or "temporarily unavailable"; overall unavailable only if ALL TFs fail.
    """
    import asyncio
    import pandas as pd
    symbol = deps.symbol
    if tfs is None:
        tfs = ["5m", "1h", "4h", "1d"]

    # Fetch current price (from ticker, not per-TF close).
    # Uses MarketDataService layer for consistency with other perception tools
    # (get_market_data / get_position). market_data.get_ticker is a no-cache
    # passthrough wrapper so the behavior is functionally identical to calling
    # exchange.fetch_ticker directly.
    try:
        ticker = await deps.market_data.get_ticker(symbol)
        current_price = ticker.last
    except Exception:
        logger.exception("get_multi_timeframe_snapshot ticker fetch failed for %s", symbol)
        return (
            f"=== Multi-TF Snapshot ({symbol}) ===\n"
            f"Error: Temporarily unavailable."
        )

    async def _fetch_one(tf: str) -> tuple[str, pd.DataFrame | Exception]:
        try:
            df = await deps.market_data.get_ohlcv_dataframe(symbol, tf, limit=MULTI_TF_OHLCV_LIMIT.get(tf, 250))
            return tf, df
        except Exception as e:
            return tf, e

    results = await asyncio.gather(*[_fetch_one(tf) for tf in tfs], return_exceptions=False)

    # All failed?
    if all(isinstance(r[1], Exception) for r in results):
        return (
            f"=== Multi-TF Snapshot ({symbol}) ===\n"
            f"Error: Temporarily unavailable (all timeframes failed)."
        )

    rows: list[str] = []
    for tf, df_or_err in results:
        primary_ma_n = MULTI_TF_PRIMARY_MA.get(tf, 50)
        fast, slow = MULTI_TF_STRUCTURE_MAS.get(tf, (50, 200))
        if isinstance(df_or_err, Exception):
            rows.append(f"{tf}: temporarily unavailable")
            continue
        df = df_or_err
        if df.empty or len(df) < slow:
            rows.append(f"{tf}: insufficient data (need {slow} candles, got {len(df)})")
            continue
        indicators = deps.technical.compute_indicators(df)
        atr = indicators.get("atr_14")
        close = float(df["close"].iloc[-1])

        # Momentum: live ticker price vs primary MA.
        # Intentional: uses `current_price` (live ticker) NOT `df["close"].iloc[-1]`
        # so the row answers "where is RIGHT NOW relative to this TF's MA?".
        # Per-TF close lags by up to one candle period (e.g. 1d close = previous
        # day's close), which would understate fast-moving intraday moves on
        # higher TFs. Do not "fix" this to df.close.iloc[-1].
        primary_ma_val = float(df["close"].rolling(primary_ma_n).mean().iloc[-1])
        mom_pct = (current_price - primary_ma_val) / primary_ma_val * 100
        mom_str = f"{mom_pct:+.1f}% vs MA{primary_ma_n}"

        # Structure: MA(fast) vs MA(slow)
        ma_fast = float(df["close"].rolling(fast).mean().iloc[-1])
        ma_slow = float(df["close"].rolling(slow).mean().iloc[-1])
        diff_pct = abs(ma_fast - ma_slow) / ma_slow * 100
        if diff_pct < 0.1:
            struct_str = f"MA{fast} at MA{slow}"
        elif ma_fast > ma_slow:
            struct_str = f"MA{fast} above MA{slow}"
        else:
            struct_str = f"MA{fast} below MA{slow}"
        # (short-structure) marker ONLY for 1w/1M — these are degraded from (50, 200) due to history shortage.
        # 5m's (MA20, MA50) is its native structure, not a degradation → no marker (spec §2.3 example).
        if tf in ("1w", "1M"):
            struct_str += " (short-structure)"

        # Volatility
        atr_pct = (atr / close * 100) if atr is not None else None
        atr_str = f"ATR {atr_pct:.2f}%" if atr_pct is not None else "ATR N/A"

        # Range position: last 20-bar high/low
        last_20 = df.iloc[-MULTI_TF_RANGE_PERIODS:]
        hi = float(last_20["high"].max())
        lo = float(last_20["low"].min())
        range_pct = 0.0 if hi == lo else (close - lo) / (hi - lo) * 100

        rows.append(f"{tf}:  {mom_str:<16} | {struct_str:<40} | {atr_str:<12} | range pos {range_pct:.0f}%")

    header = [
        f"=== Multi-TF Snapshot ({symbol}) ===",
        f"Current price: {current_price:.2f}",
        "Columns: Momentum (price vs primary MA) | Structure (MA alignment) | Volatility (ATR as % of price) | Range pos (position within 20-bar high-low, 0%=low / 100%=high)",
        "",
    ]
    return "\n".join(header + rows)


# === Iter 3 — get_price_pivots helpers ===

def _compute_swing_pivots(
    df: pd.DataFrame, n: int = 5
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (highs, lows) where each entry is (bars_ago, price).

    Williams fractal with strict inequality: center bar's high must be strictly
    greater than all 2n surrounding bars' highs (and similarly low strictly less).
    Equality at any neighbor disqualifies the pivot — prevents flat-plateau false
    signals. Confirmed pivots only — last n bars excluded due to incomplete
    right window, so min returned bars_ago = n.
    """
    if len(df) < 2 * n + 1:
        return [], []

    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    last_idx = len(df) - 1
    confirm_end = last_idx - n

    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(n, confirm_end + 1):
        center_h = h[i]
        center_l = l[i]
        is_high = all(center_h > h[i + d] for d in range(-n, n + 1) if d != 0)
        is_low = all(center_l < l[i + d] for d in range(-n, n + 1) if d != 0)
        if is_high:
            highs.append((last_idx - i, float(center_h)))
        if is_low:
            lows.append((last_idx - i, float(center_l)))
    return highs, lows


def _get_prior_period_hl(
    df_or_err: pd.DataFrame | Exception | None,
) -> tuple[str, float | None, float | None]:
    """Return (status, high, low). status one of 'ok' / 'insufficient' / 'unavailable'.

    Period label ('Daily' / 'Weekly' / 'Monthly') is bound by the caller in
    `_render_pivot_rows` when iterating the three period results — not needed here.
    """
    if isinstance(df_or_err, Exception):
        return "unavailable", None, None
    df = df_or_err
    if df is None or df.empty or len(df) < 2:
        return "insufficient", None, None
    prior = df.iloc[-2]
    return "ok", float(prior["high"]), float(prior["low"])


def _bars_ago_fmt(n: int) -> str:
    """0 → 'now' (defensive — confirmed pivots have min ago=N=5);
    1 → '1 bar ago'; N≥2 → 'N bars ago'."""
    if n == 0:
        return "now"
    if n == 1:
        return "1 bar ago"
    return f"{n} bars ago"


def _render_pivot_rows(
    swing_highs: list[tuple[int, float]],
    swing_lows: list[tuple[int, float]],
    prior_d: tuple[str, float | None, float | None],
    prior_w: tuple[str, float | None, float | None],
    prior_m: tuple[str, float | None, float | None],
    current_price: float,
) -> tuple[list[str], list[str], list[str]]:
    """Return (above_rows, below_rows, footer_lines).

    above/below already sorted by abs(distance%) ascending; footer collects
    insufficient/unavailable notices for priors that don't fit either group.
    Caller (`get_price_pivots`) handles swing_status separately.
    """
    above: list[tuple[float, str]] = []
    below: list[tuple[float, str]] = []
    footer: list[str] = []

    for kind, items in (("Swing High", swing_highs), ("Swing Low", swing_lows)):
        for ago, price in items:
            dist_pct = (price - current_price) / current_price * 100
            line = f"{kind}: {price:,.2f} ({dist_pct:+.2f}%, {_bars_ago_fmt(ago)})"
            target = above if price > current_price else below
            target.append((abs(dist_pct), line))

    for label, (status, h, l_) in [
        ("Daily", prior_d), ("Weekly", prior_w), ("Monthly", prior_m),
    ]:
        if status == "ok":
            for kind, value in [("H", h), ("L", l_)]:
                dist_pct = (value - current_price) / current_price * 100
                line = f"Prior {label} {kind}: {value:,.2f} ({dist_pct:+.2f}%)"
                target = above if value > current_price else below
                target.append((abs(dist_pct), line))
        else:
            note = "insufficient data" if status == "insufficient" else "temporarily unavailable"
            footer.append(f"Prior {label} H/L: {note}")

    above.sort(key=lambda x: x[0])
    below.sort(key=lambda x: x[0])
    return [line for _, line in above], [line for _, line in below], footer


async def get_price_pivots(deps: TradingDeps) -> str:
    """Show structural support/resistance: last 100 main-TF swing pivots
    (Williams fractal N=5) + prior daily/weekly/monthly H/L. Fact-only.

    Returns:
        Levels grouped by 'above current price' / 'below current price';
        within each group, sorted by absolute distance ascending. Swing
        rows include 'N bars ago'; prior rows label the period.

    Degradation: per-source three-state (fact / insufficient data /
        temporarily unavailable). Ticker failure → whole tool unavailable
        (no baseline price); main-TF failure → swing section degrades only;
        per-prior failure → only that row degrades.
    """
    import asyncio  # local import — matches existing convention (e.g. tools_perception.py:1320)

    symbol = deps.symbol
    main_tf = deps.timeframe

    try:
        ticker = await deps.market_data.get_ticker(symbol)
        current_price = ticker.last
    except Exception:
        logger.exception("get_price_pivots ticker fetch failed for %s", symbol)
        return (
            f"=== Price Pivots ({symbol}, main TF: {main_tf}) ===\n"
            f"Error: Temporarily unavailable."
        )

    async def _fetch(tf: str, limit: int):
        try:
            return await deps.market_data.get_ohlcv_dataframe(symbol, tf, limit=limit)
        except Exception as e:
            return e

    main_df_or_err, daily_or_err, weekly_or_err, monthly_or_err = await asyncio.gather(
        _fetch(main_tf, 100),
        _fetch("1d", 2),
        _fetch("1w", 2),
        _fetch("1M", 2),
    )

    swing_status: str | None = None
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    if isinstance(main_df_or_err, Exception):
        swing_status = "Swing pivots: temporarily unavailable"
    elif main_df_or_err is None or main_df_or_err.empty or len(main_df_or_err) < 11:
        got_bars = 0 if (main_df_or_err is None or main_df_or_err.empty) else len(main_df_or_err)
        swing_status = f"Swing pivots: insufficient data (need 11+ bars, got {got_bars})"
    else:
        bar_count = len(main_df_or_err)
        swing_highs, swing_lows = _compute_swing_pivots(main_df_or_err, n=5)
        no_pivot = not swing_highs and not swing_lows
        if no_pivot and bar_count >= 100:
            swing_status = "(No swing pivots in 100-bar window)"
        elif no_pivot and bar_count < 100:
            swing_status = f"(Window: {bar_count} bars, less than 100 — no swing pivots found)"
        elif bar_count < 100:
            swing_status = f"(Window: {bar_count} bars, less than 100)"
        # else: 100 bars + ≥1 pivot → swing_status stays None

    prior_d = _get_prior_period_hl(daily_or_err)
    prior_w = _get_prior_period_hl(weekly_or_err)
    prior_m = _get_prior_period_hl(monthly_or_err)

    above_rows, below_rows, prior_footer = _render_pivot_rows(
        swing_highs, swing_lows, prior_d, prior_w, prior_m, current_price,
    )

    sections: list[str] = [
        f"=== Price Pivots ({symbol}, main TF: {main_tf}) ===",
        f"Current Price: {current_price:,.2f}",
        "",
        "=== Levels Above Current Price ===",
        *(above_rows or ["(none)"]),
        "",
        "=== Levels Below Current Price ===",
        *(below_rows or ["(none)"]),
    ]
    if swing_status:
        sections.append("")
        sections.append("=== Swing Status ===")
        sections.append(swing_status)
    if prior_footer:
        sections.append("")
        sections.append("=== Prior Period H/L ===")
        sections.extend(prior_footer)
    return "\n".join(sections)
