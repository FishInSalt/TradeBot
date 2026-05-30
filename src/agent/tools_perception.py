from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps
    from src.integrations.exchange.base import OpenInterestHistoryPoint, TakerFlowBar, Trade

logger = logging.getLogger(__name__)


# === Iter 2 toolkit constants ===
# get_order_book
ORDER_BOOK_CONCENTRATION_MULTIPLIER = 3.0
ORDER_BOOK_MAX_CONCENTRATED_LEVELS = 10
ORDER_BOOK_DEPTH_DEFAULT = 15

# get_recent_trades
RECENT_TRADES_SLICE_SIZE = 100         # trades per count-bucket
RECENT_TRADES_N_SLICES = 5             # max slices -> 5×100 = 500 (OKX /market/trades cap)
RECENT_TRADES_MAX_FETCH = RECENT_TRADES_SLICE_SIZE * RECENT_TRADES_N_SLICES  # 500

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
    """Single-timeframe market data implementation.

    Renders: Ticker (last + bid/ask + 24h H/L + base volume); Technical Indicators
    (RSI / MACD / BB / ATR via TechnicalAnalysisService); Market Context (ATR % of
    price + last-bar vol/SMA(20) ratio); Recent Candles OHLCV table with per-bar
    RVol(×SMA20) column + vol↑/range↑ markers + in-progress candle hint in the
    section header; Period summary (Avg vol, Net Δclose) across last 5 vs prior 5.

    All indicators / OHLCV rows / period summary are computed on closed bars
    (via `_closed_bars(df)`); the in-progress candle is excluded from data but
    its expected open/close timestamps appear in the section header.

    NOTE: This impl docstring is dev-facing only. LLM-facing description comes
    from `src.agent.tools_descriptions.GET_MARKET_DATA_DESCRIPTION` via the
    `@tool(description=...)` override at `src.agent.trader.py:124`. Args
    documentation for the LLM lives in the ctx-receiver docstring at the same
    site (parsed by griffe into parameters_json_schema).

    Args:
        deps: TradingDeps with .exchange / .market_data / .technical wired
        symbol: trading symbol (defaults to deps.symbol)
        timeframe: CCXT timeframe (defaults to deps.timeframe)
        candle_count: number of closed candles in OHLCV table; clamped to [10, 80]
    """
    import pandas as pd
    from datetime import datetime, timezone
    from src.utils.ohlcv_utils import (
        _live_price, _closed_bars, _atr_series,
        _to_pd_timestamp_utc, _fmt_candle_time, TF_OFFSETS,
    )

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
    # tools. Numerator = df_closed.iloc[-1] (most-recent closed bar).
    if len(df_closed) >= 20:
        vol_now = float(df_closed["volume"].iloc[-1])
        vol_avg = float(df_closed["volume"].iloc[-20:].mean())
        ratio = vol_now / vol_avg if vol_avg > 0 else 0.0
        ctx_lines.append(f"Last bar vol: {vol_now:.1f} ({ratio:.2f}× SMA(20) avg)")
    else:
        ctx_lines.append("Last bar vol: N/A")

    sections.append("=== Market Context ===\n" + "\n".join(ctx_lines))

    # === Recent Candles (OHLCV with markers + RVol column) ===
    vol_sma = df_closed["volume"].rolling(20).mean()
    atr_series = _atr_series(df_closed, period=14) if len(df_closed) >= 15 else None
    candle_lines: list[str] = [
        f"{'Time (open UTC)':<16} {'Open':>10} {'High':>10} {'Low':>10} "
        f"{'Close':>10} {'Vol':>10}  {'RVol(×SMA20)':>12}  Markers"
    ]
    for idx in display_df.index:
        row = df_closed.loc[idx]
        ts_val = row["timestamp"]
        # Use shared helper for both pd.Timestamp coercion and tf-aware formatting
        dt = _to_pd_timestamp_utc(ts_val)
        time_str = _fmt_candle_time(dt, timeframe)

        markers: list[str] = []
        vol_sma_at = vol_sma.loc[idx] if idx in vol_sma.index else None
        # Compute RVol; degraded fallback `—` when SMA(20) not yet ready
        if vol_sma_at is not None and not pd.isna(vol_sma_at) and float(vol_sma_at) > 0:
            rvol = float(row["volume"]) / float(vol_sma_at)
            rvol_str = f"{rvol:.2f}×"
            if float(row["volume"]) > 2 * float(vol_sma_at):
                markers.append("vol↑")
        else:
            rvol_str = "—"
        atr_at = None
        if atr_series is not None and idx in atr_series.index:
            atr_at = atr_series.loc[idx]
        if atr_at is not None and not pd.isna(atr_at) and float(atr_at) > 0:
            if (float(row["high"]) - float(row["low"])) > 2 * float(atr_at):
                markers.append("range↑")
        marker_str = " ".join(markers)

        candle_lines.append(
            f"{time_str:<16} {row['open']:>10.2f} {row['high']:>10.2f} "
            f"{row['low']:>10.2f} {row['close']:>10.2f} {row['volume']:>10.1f}  "
            f"{rvol_str:>12}  {marker_str}".rstrip()
        )
    # In-progress candle hint: extrapolate next bar's open/close from the
    # most-recent closed bar + tf offset. Unknown tf → empty suffix (degraded).
    in_progress_suffix = ""
    if not display_df.empty:
        offset = TF_OFFSETS.get(timeframe)
        if offset is not None:
            last_closed_dt = _to_pd_timestamp_utc(display_df["timestamp"].iloc[-1])
            in_progress_open = last_closed_dt + offset
            in_progress_close = in_progress_open + offset
            in_progress_suffix = (
                f"; in-progress {_fmt_candle_time(in_progress_open, timeframe)} "
                f"still open, closes at {_fmt_candle_time(in_progress_close, timeframe)}"
            )

    sections.append(
        f"=== Recent Candles ({timeframe}, last {display_count}, "
        f"oldest-first by row{in_progress_suffix}) ===\n"
        + "\n".join(candle_lines)
    )

    # === Period summary ===
    if len(df_closed) >= 10:
        last_5 = df_closed.iloc[-5:]
        prior_5 = df_closed.iloc[-10:-5]
        avg_vol_last = float(last_5["volume"].mean())
        avg_vol_prior = float(prior_5["volume"].mean())
        vol_ratio = avg_vol_last / avg_vol_prior if avg_vol_prior > 0 else 0.0
        net_delta_last = float(df_closed["close"].iloc[-1] - df_closed["close"].iloc[-5])
        net_delta_prior = float(df_closed["close"].iloc[-6] - df_closed["close"].iloc[-10])
        summary = (
            "=== Period summary (last 5 closed candles vs prior 5 closed candles) ===\n"
            f"Avg vol:     last 5 {avg_vol_last:.1f} / prior 5 {avg_vol_prior:.1f} ({vol_ratio:.2f}×)\n"
            f"Net Δclose:  last 5 {net_delta_last:+.1f} USDT / prior 5 {net_delta_prior:+.1f} USDT"
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
    from datetime import datetime, timezone

    from src.utils.ohlcv_utils import _closed_bars

    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    symbol = symbol or deps.symbol

    # Phase 1: positions only — early return if empty
    positions = await deps.exchange.fetch_positions(symbol)
    if not positions:
        return f"=== Position ({symbol} @ {fetch_ts} UTC) ===\nNo open positions."

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

    async def _safe_mark_price():
        try:
            return await deps.exchange.get_mark_price(symbol)
        except Exception:
            logger.exception("get_position: mark price fetch failed")
            return 0.0

    def _render_position_core() -> list[str]:
        """Render Position + PnL sections (Phase-1 fields only).

        Returns a list of 2 fully-formed sections (each "=== Header ===\\n<body>"
        joined string) so callers can append further sections via "\\n\\n".join.
        Shared between happy path and hard-failure degradation branch so PnL +
        Duration are preserved when ticker/balance/orders/contract_size fail
        (would otherwise be lost even though `p.created_at` is fully available).
        """
        pos_lines = [f"=== Position ({symbol} @ {fetch_ts} UTC) ===",
                     f"Side: {p.side.capitalize()} | Contracts: {p.contracts} | Entry: {p.entry_price:,.2f}",
                     f"Leverage: {p.leverage}x"]
        # F-P2: Liquidation lives in Risk Exposure section (richer form with
        # `(P% away = Q× ATR(1h))`); deduplicated from Position section.
        pos_lines.append(f"Unrealized: {p.unrealized_pnl:+.2f} USDT (gross)")

        pnl_lines = ["=== PnL ==="]
        if deps.initial_balance > 0:
            pnl_pct_of_capital = (p.unrealized_pnl / deps.initial_balance) * 100
            pnl_lines.append(
                f"PnL: {p.unrealized_pnl:+.2f} USDT gross ({pnl_pct_of_capital:+.2f}% of initial capital)"
            )
        else:
            pnl_lines.append(f"PnL: {p.unrealized_pnl:+.2f} USDT gross")
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

    # Phase 2a: core render (Position + PnL Phase-1 fields).
    # Computed before the main IO gather so PnL + Duration survive
    # ticker/balance/orders failure in the degradation branch below.
    sections = _render_position_core()

    try:
        ticker, balance, ohlcv_df, open_orders, contract_size, mark_price = await asyncio.gather(
            deps.market_data.get_ticker(symbol),
            deps.exchange.fetch_balance(),
            _safe_ohlcv(),
            deps.exchange.fetch_open_orders(symbol),
            deps.exchange.get_contract_size(symbol),
            _safe_mark_price(),
            return_exceptions=False,
        )
    except Exception as e:
        logger.exception("get_position: one of ticker/balance/orders/contract_size failed")
        sections.append(f"=== Risk Exposure ===\n(unavailable: {e.__class__.__name__})")
        sections.append(f"=== Exit Orders ===\n(unavailable: {e.__class__.__name__})")
        return "\n\n".join(sections)

    # ATR(1h) — closed-bars-only per algorithm-lock invariant (R2-Next-D §6.4):
    # atr_14 here must match GMD/HTF/MTS atr_14 on the same TF.
    atr_1h = None
    if ohlcv_df is not None and not ohlcv_df.empty:
        df_closed = _closed_bars(ohlcv_df)
        if not df_closed.empty:
            indicators = deps.technical.compute_indicators(df_closed)
            atr_1h = indicators.get("atr_14")
    current_price = ticker.last

    # === Fee & Breakeven ===
    # Depends on p.entry_price + deps.fee_rate + contract_size (USDT-denominated
    # notional uses contract_size factor — see Risk Exposure below for the
    # established convention). Rendered post-gather since contract_size and
    # current_price are needed; ticker.last failure already short-circuits to
    # the degradation branch above.
    entry_fee = p.entry_price * p.contracts * contract_size * deps.fee_rate
    if p.side == "long":
        breakeven = p.entry_price * (1 + 2 * deps.fee_rate)
        sign_str = "+"
        side_label = "long"
    else:
        breakeven = p.entry_price * (1 - 2 * deps.fee_rate)
        sign_str = "−"  # Unicode minus U+2212, matches test assertion exactly
        side_label = "short"

    fb_lines = ["=== Fee & Breakeven ==="]
    fb_lines.append(
        f"Entry fee paid: ~-{entry_fee:.2f} USDT "
        f"(= entry × contracts × contract_size × rate)"
    )
    if current_price > 0:
        if p.side == "long":
            _distance_pts = current_price - breakeven
        else:
            _distance_pts = breakeven - current_price
        fb_lines.append(
            f"Breakeven: {breakeven:,.2f} "
            f"[current {current_price:,.2f}, {_distance_pts:+.0f} pts]"
        )
    else:
        fb_lines.append(f"Breakeven: {breakeven:,.2f}")
    fb_lines.append(
        f"  = {p.entry_price:,.2f} × (1 {sign_str} 2 × fee_rate) "
        f"[{side_label} round-trip taker]"
    )
    sections.append("\n".join(fb_lines))

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
    # Risk Exposure: Mark + Liquidation
    if mark_price > 0:
        if current_price > 0:
            drift_pct = (current_price - mark_price) / mark_price * 100
            drift_str = f"{drift_pct:+.2f}%"
            # Suppress the (Last:..., drift ...) suffix when the displayed
            # drift rounds to zero — under sim, mark==last by construction
            # (simulated.py:130-142), so the suffix is dead 100% of the time
            # there. Format-string comparison (not abs(drift_pct) < epsilon)
            # so the suppression boundary tracks the .2f display precision
            # exactly: any value that would render +0.00%/-0.00% is dropped.
            if drift_str in ("+0.00%", "-0.00%"):
                risk_lines.append(f"Mark: {mark_price:.2f}")
            else:
                risk_lines.append(
                    f"Mark: {mark_price:.2f} (Last: {current_price:.2f}, drift {drift_str})"
                )
        else:
            risk_lines.append(f"Mark: {mark_price:.2f} (Last: unavailable)")

        if p.liquidation_price is not None:
            liq_dist_pct = abs(mark_price - p.liquidation_price) / mark_price * 100
            if atr_pct_1h is not None and atr_pct_1h > 0:
                atr_mult = liq_dist_pct / atr_pct_1h
                risk_lines.append(
                    f"Liquidation: {p.liquidation_price:.2f} ({liq_dist_pct:.2f}% away = {atr_mult:.1f}× ATR(1h))"
                )
            else:
                risk_lines.append(
                    f"Liquidation: {p.liquidation_price:.2f} ({liq_dist_pct:.2f}% away)"
                )
    else:
        # mark fetch failed → omit Mark line, Liquidation falls back without distance
        if p.liquidation_price is not None:
            risk_lines.append(
                f"Liquidation: {p.liquidation_price:.2f} (distance unavailable: mark fetch failed)"
            )
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
    trigger_ref = deps.exchange.algo_trigger_reference

    # Exit Orders distance is intentionally last-anchored (current_price = ticker.last),
    # matching OKX's algo trigger reference. The Risk Exposure Mark line above is for
    # the Liquidation row only — different anchor, different physical purpose.
    def _fmt_exit(o, kind: str) -> str:
        dist_entry_pct = (o.price - p.entry_price) / p.entry_price * 100
        dist_curr_pct = (o.price - current_price) / current_price * 100 if current_price > 0 else 0.0
        direction_entry = "above" if dist_entry_pct > 0 else "below"
        direction_curr = "above" if dist_curr_pct > 0 else "below"
        suffix = ""
        if atr_pct_1h is not None and atr_pct_1h > 0:
            atr_mult = abs(dist_curr_pct) / atr_pct_1h
            suffix = f" = {atr_mult:.1f}× ATR(1h)"
        return (
            f"  {kind}: {o.price:.2f} "
            f"({abs(dist_entry_pct):.1f}% {direction_entry} entry, "
            f"{abs(dist_curr_pct):.1f}% {direction_curr} {trigger_ref} price{suffix})  "
            f"[{o.amount} contracts]"
        )

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
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    balance = await deps.exchange.fetch_balance()
    ret_usdt = balance.total_usdt - deps.initial_balance
    ret_pct = (ret_usdt / deps.initial_balance) * 100 if deps.initial_balance > 0 else 0.0
    return (
        f"=== Account Balance (@ {fetch_ts} UTC) ===\n"
        f"Total: {balance.total_usdt:.2f} USDT (initial: {deps.initial_balance:.2f})\n"
        f"Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized)\n"
        f"Free: {balance.free_usdt:.2f} USDT\n"
        f"Used: {balance.used_usdt:.2f} USDT"
    )


async def get_memories(deps: TradingDeps) -> str:
    """Get long-term memories (lessons, patterns, trade reviews)."""
    return await deps.memory.format_for_prompt()


def _render_single_order(o, current: float, trigger_ref: str) -> str:
    """Render a single (non-OCO) order line.

    `trigger_ref` is the exchange's algo trigger reference word (default
    "last" for OKX); used in the distance-label suffix.

    Preserves the current > 0 branch: no crash on abnormal ticker. Label /
    distance / ID suffix format matches the pre-iter-tool-opt-mark-vs-last
    rendering except for the trailing "{trigger_ref} price" swap.
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
            pts = o.price - current
            price_str = f"@ {o.price:.2f} ({dist:+.2f}% / {pts:+.1f} pts from {trigger_ref} price)"
        else:
            price_str = f"@ {o.price:.2f} (ticker unavailable, distance N/A)"
    return f"  {label} {o.side} {o.amount} {price_str} | ID: {o.id}"


async def get_open_orders(deps: TradingDeps) -> str:
    """Get all pending orders with distance from last price."""
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return f"=== Pending Orders (@ {fetch_ts} UTC) ===\nNo pending orders."

    ticker = await deps.market_data.get_ticker(deps.symbol)
    current = ticker.last
    trigger_ref = deps.exchange.algo_trigger_reference

    # Group by id: OCO's two same-id legs share id + is_algo=True
    by_id: dict[str, list] = {}
    for o in orders:
        by_id.setdefault(o.id, []).append(o)

    lines = [f"=== Pending Orders (@ {fetch_ts} UTC) ==="]
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
                f" ({(sl.price - current) / current * 100:+.2f}%"
                f" / {sl.price - current:+.1f} pts from {trigger_ref} price)"
                if current > 0 else " (ticker unavailable)"
            )
            tp_dist = (
                f" ({(tp.price - current) / current * 100:+.2f}%"
                f" / {tp.price - current:+.1f} pts from {trigger_ref} price)"
                if current > 0 else " (ticker unavailable)"
            )
            lines.append(
                f"  [OCO] {sl.side} {sl.amount} "
                f"stop {sl.price:.2f}{sl_dist} / tp {tp.price:.2f}{tp_dist} "
                f"| algoId: {order_id} (cancel removes both legs)"
            )
        else:
            for o in group:
                lines.append(_render_single_order(o, current, trigger_ref))
    return "\n".join(lines)


async def get_trade_journal(deps: TradingDeps, limit: int = 20) -> str:
    """Get trade journal — decision timeline with quick stats summary."""
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if deps.db_engine is None:
        return (
            f"=== Trade Journal (@ {fetch_ts} UTC) ===\n"
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
            f"=== Trade Journal (@ {fetch_ts} UTC) ===\n"
            "No trade journal entries yet."
        )

    sections: list[str] = []

    # Performance Summary (from MetricsService)
    if deps.metrics is not None:
        metrics = await deps.metrics.compute()
        if metrics.total_trades > 0:
            # All metrics gross-based (PR #57 review I-2 + spec §3 single-source);
            # full gross/net dual view is in get_performance.
            summary_lines = [
                f"Total Trades: {metrics.total_trades} | Win: {metrics.winning_trades} "
                f"({metrics.win_rate:.1%}, gross) | Loss: {metrics.losing_trades}",
                f"Avg Win: {metrics.avg_win:+.2f} USDT gross | Avg Loss: {metrics.avg_loss:.2f} USDT gross",
                f"Profit Factor: {'N/A (no losses)' if metrics.profit_factor is None else f'{metrics.profit_factor:.2f}'} (gross)",
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

    sections.append(f"=== Trade Journal (@ {fetch_ts} UTC) ===\n" + "\n".join(lines))
    return "\n\n".join(sections)


async def get_active_alerts(deps: TradingDeps) -> str:
    """Get current alert configuration: price volatility alert params and price level alerts."""
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    sections: list[str] = []

    # Volatility alert settings
    params = deps.exchange.get_alert_params()
    if params is not None:
        threshold, window = params
        sections.append(f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\n{threshold}% in {window}min window")
    else:
        sections.append(f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\nNot set")

    # Price level alerts
    alerts = deps.exchange.get_price_level_alerts()
    count = len(alerts)
    lines = [f"=== Price Level Alerts ({count}/20) (@ {fetch_ts} UTC) ==="]
    if alerts:
        now = time.time()  # single baseline for all rows
        for i, a in enumerate(alerts, 1):
            age = _fmt_age_humanized(now - a["created_at"])
            lines.append(
                f'  #{i} (id={a["id"]}) {a["direction"]} {a["price"]:.2f} '
                f'— "{a["reasoning"]}" ({age})'
            )
    else:
        lines.append("  No active alerts.")
    sections.append("\n".join(lines))

    return "\n\n".join(sections)


async def get_performance(deps: TradingDeps) -> str:
    """Get detailed trading performance statistics."""
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    balance = await deps.exchange.fetch_balance()
    ret_usdt = balance.total_usdt - deps.initial_balance
    ret_pct = (ret_usdt / deps.initial_balance) * 100 if deps.initial_balance > 0 else 0.0

    if deps.metrics is None:
        # L3 by-design empty state (NOT an error): no metrics service available.
        # Trading Performance section still renders balance fields; Trade Stats
        # section emitted as placeholder so the schema is consistent.
        perf_section = (
            f"=== Trading Performance (@ {fetch_ts} UTC) ===\n"
            f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
            f"Current Balance: {balance.total_usdt:.2f} USDT\n"
            f"Total Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized, net)"
        )
        stats_section = (
            "=== Trade Stats ===\n"
            "No metrics service available."
        )
        return f"{perf_section}\n\n{stats_section}"

    metrics = await deps.metrics.compute()

    # Scope `(all fills)` 显式提示 total_fees 含未平仓 open lot 的 entry fee
    # → 当 session 末仍有持仓时 gross − net ≠ total_fees (差额 = 未平仓 lot 的
    # open_fee_share). agent 算术自检 `gross − fees ≈ net` 时必读 scope
    # 才不致 mismatch (PR #57 review R5-I-2).
    fees_line = (
        f"Total Fees: -{metrics.total_fees:.2f} USDT (all fills)"
        if metrics.total_fees > 0
        else "Total Fees: 0.00 USDT (all fills)"
    )

    if metrics.total_trades == 0:
        if metrics.legacy_close_skipped > 0 or metrics.legacy_open_skipped > 0:
            stats_body = (
                "Stats unavailable: all close fills are pre-net-metrics-iter legacy data "
                "(forensic analysis via scripts/_sim_metrics.py from sim_orders table)."
            )
        elif metrics.invariant_violations > 0:
            stats_body = (
                f"Stats unavailable: data invariant violations "
                f"({metrics.invariant_violations} close fills had no preceding open lot "
                f"or corrupt amount/price). Investigate trade_actions integrity."
            )
        else:
            stats_body = "No completed trades yet."
        perf_section = (
            f"=== Trading Performance (@ {fetch_ts} UTC) ===\n"
            f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
            f"Current Balance: {balance.total_usdt:.2f} USDT\n"
            f"Total Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized, net)\n"
            f"{fees_line}"
        )
        stats_section = f"=== Trade Stats ===\n{stats_body}"
        return f"{perf_section}\n\n{stats_section}"

    def _fmt_pf(pf: float | None) -> str:
        return "N/A (no losses)" if pf is None else f"{pf:.2f}"

    perf_section = (
        f"=== Trading Performance (@ {fetch_ts} UTC) ===\n"
        f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
        f"Current Balance: {balance.total_usdt:.2f} USDT\n"
        f"Total Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized, net)\n"
        f"Realized PnL: {metrics.total_pnl:+.2f} USDT gross / {metrics.net_pnl:+.2f} USDT net "
        f"(fees {-metrics.total_fees:+.2f} USDT)\n"
        f"{fees_line}"
    )

    # Note: 与 spec §8.1 example schema 一致。`fees` 注解 = session-wide total_fees
    # (含未平仓 open lot 的 entry fee)。当 session 末仍有持仓时，gross − net ≠ total_fees
    # (差额 = 未平仓 lot 的 open_fee_share)；此为已知 minor UX 不一致，未平仓退出后即收敛。

    stats_lines = []
    # Condition keyed on close-skipped (not OR open) — open-only skip doesn't
    # change trade count, message would degrade to "m/m (0 skipped)" noise
    # (PR #57 review I-4).
    if metrics.legacy_close_skipped > 0:
        m = metrics.total_trades
        n = m + metrics.legacy_close_skipped
        stats_lines.append(
            f"Note: net stats based on {m}/{n} trades "
            f"({metrics.legacy_close_skipped} legacy rows skipped — pre-net-metrics-iter data)."
        )
    if metrics.missing_close_entry_price_count > 0:
        stats_lines.append(
            f"Note: {metrics.missing_close_entry_price_count} close fills had cache-miss entry_price "
            f"(FIFO unaffected; audit trail incomplete for those trades)."
        )
    # spec §6.9 contract — surface invariant_violations alongside populated stats
    # (not just empty-state). Without this, agent sees clean totals but `gross − fees ≈ net`
    # self-check silently fails because total_fees includes the excluded fills' fees
    # (PR #57 review R4-I-2).
    if metrics.invariant_violations > 0:
        stats_lines.append(
            f"Note: invariant violations: {metrics.invariant_violations} fill(s) "
            f"had no preceding open lot or corrupt amount/price "
            f"(excluded from FIFO; investigate trade_actions integrity)."
        )

    stats_lines.append(f"Total Trades: {metrics.total_trades}")
    # Break-even (pnl == 0) trades 不计入 W/L (per spec §3 / R2-I-1 align with scripts);
    # 当 BE > 0 时附 `/{n}B` 段，agent 可验证 W+L+B = total partition completeness
    # (PR #57 review R2-I-3).
    gross_be_part = f"/{metrics.break_even_trades}B" if metrics.break_even_trades > 0 else ""
    net_be_part = f"/{metrics.net_break_even_trades}B" if metrics.net_break_even_trades > 0 else ""
    stats_lines.append(
        f"Win Rate: {metrics.win_rate:.0%} gross ({metrics.winning_trades}W/{metrics.losing_trades}L{gross_be_part}) "
        f"/ {metrics.net_win_rate:.0%} net ({metrics.net_winning_trades}W/{metrics.net_losing_trades}L{net_be_part})"
    )
    stats_lines.append(
        f"Profit Factor: {_fmt_pf(metrics.profit_factor)} gross / {_fmt_pf(metrics.net_profit_factor)} net"
    )
    stats_lines.append(
        f"Avg Win:  {metrics.avg_win:+.2f} USDT gross / {metrics.avg_win_net:+.2f} USDT net"
    )
    stats_lines.append(
        f"Avg Loss: {metrics.avg_loss:.2f} USDT gross / {metrics.avg_loss_net:.2f} USDT net"
    )
    stats_lines.append(
        f"Best Trade: {metrics.best_trade:+.2f} USDT gross / {metrics.best_trade_net:+.2f} USDT net"
    )
    stats_lines.append(
        f"Worst Trade: {metrics.worst_trade:.2f} USDT gross / {metrics.worst_trade_net:.2f} USDT net"
    )
    mdd_str = f"-{metrics.max_drawdown_pct:.1f}%" if metrics.max_drawdown_pct > 0 else "0.0%"
    stats_lines.append(f"Max Drawdown: {mdd_str} (net equity)")

    stats_section = "=== Trade Stats ===\n" + "\n".join(stats_lines)

    out = f"{perf_section}\n\n{stats_section}"

    # OKX session footnote (spec §6.4). Cache-miss line conditional on actual
    # caveat being emitted above — otherwise points to nothing (PR #57 review I-3).
    from src.integrations.exchange.okx import OKXExchange
    if isinstance(deps.exchange, OKXExchange):
        out += (
            "\n\nNote: OKX net metrics use exchange-echoed fees (accurate); "
            "minor ε from lot amount precision possible."
        )
        if metrics.missing_close_entry_price_count > 0:
            out += (
                "\n      Cache-miss close fills are included (FIFO uses lot.entry_px from open); "
                "audit-trail flagged in caveat above."
            )

    return out


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
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    if deps.news is None:
        return (
            f"=== News (@ {fetch_ts} UTC) ===\n"
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
            f"=== Fear & Greed Index (@ {fetch_ts} UTC) ===\n"
            f"Value: {fgi.title}\n"
            f"(Updated: {date_str})"
        )
    else:
        sections.append(f"=== Fear & Greed Index (@ {fetch_ts} UTC) ===\nFGI service temporarily unavailable.")

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
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if deps.news is None:
        return (
            f"=== Exchange Announcements (past {lookback_hours}h @ {fetch_ts} UTC) ===\n"
            "Error: News service not configured."
        )

    exc_class_name: str | None = None
    try:
        announcements = await deps.news.get_announcements(lookback_hours)
    except Exception as e:
        announcements = None
        exc_class_name = e.__class__.__name__

    if announcements is None:
        suffix = f" ({exc_class_name})" if exc_class_name else ""
        return (
            f"=== Exchange Announcements (past {lookback_hours}h @ {fetch_ts} UTC) ===\n"
            f"Error: Exchange announcements service temporarily unavailable{suffix}."
        )
    if announcements:
        lines = [e.timestamp.strftime("[%Y-%m-%d %H:%M] ") + e.title for e in announcements]
        return (
            f"=== Exchange Announcements (past {lookback_hours}h @ {fetch_ts} UTC) ===\n"
            + "\n".join(lines)
        )
    return (
        f"=== Exchange Announcements (past {lookback_hours}h @ {fetch_ts} UTC) ===\n"
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
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if deps.news is None:
        return (
            f"=== Upcoming Macro Events (next {lookahead_hours}h @ {fetch_ts} UTC) ===\n"
            "Error: News service not configured."
        )

    try:
        macro_events = await deps.news.get_macro_events(lookahead_hours)
    except Exception:
        macro_events = None

    sections: list[str] = []

    if macro_events is None:
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h @ {fetch_ts} UTC) ===\n"
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
            f"=== Upcoming Macro Events (next {lookahead_hours}h @ {fetch_ts} UTC) ===\n"
            + "\n".join(lines)
        )
    else:
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h @ {fetch_ts} UTC) ===\n"
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


def _format_oi_usd(v: float) -> str:
    """Format OI USD value with auto-scale unit (B / M / raw)."""
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


def _fmt_money(v: float) -> str:
    """Signed USD with auto $K/$M scale (ASCII sign, for test stability)."""
    a = abs(v)
    sign = "-" if v < 0 else "+"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.1f}K"
    return f"{sign}${a:.0f}"


def _derive_oi_anchors(
    points: list[OpenInterestHistoryPoint],
    *,
    now_ms: int,
    period_ms: int = 3600 * 1000,
) -> tuple[OpenInterestHistoryPoint | None, str, bool]:
    """Resolve closed-only OI anchors and render delta fragments.

    Detects in-progress final bucket via `newest.timestamp + period_ms > now_ms`
    (OKX rubik returns the partial current 1H bucket as the newest row; verified
    by .working/tool-optimization/probe_okx_oi_phase.py). When in-progress, all
    anchor indices shift forward by 1 so deltas remain closed-on-closed
    (G-calc-rigor-audit §G-6).

    Returns (current_closed_bucket, anchors_fragments_str, was_shifted):
      - current_closed_bucket: None if `points` lacks enough history; else the
        most-recent closed bucket (points[-2] when in-progress, points[-1]
        when newest is already closed).
      - anchors_fragments_str: "1h ago $X.XXB, +Y.Y%; 24h ago $X.XXB, -Y.Y%"
        (partial degradation: insufficient/zero anchors skipped silently).
      - was_shifted: True iff newest was in-progress (caller renders header
        disclosure).
    """
    if not points:
        return None, "", False
    newest = points[-1]
    is_in_progress = newest.timestamp + period_ms > now_ms
    base_offset = 2 if is_in_progress else 1
    if len(points) < base_offset:
        return None, "", is_in_progress
    current_closed = points[-base_offset]

    fragments: list[str] = []
    for label, hour_offset in [("1h ago", 1), ("24h ago", 24)]:
        idx_from_end = base_offset + hour_offset
        if len(points) < idx_from_end:
            continue
        anchor = points[-idx_from_end]
        if anchor.open_interest_value <= 0:
            continue
        delta_pct = (
            current_closed.open_interest_value / anchor.open_interest_value - 1
        ) * 100
        fragments.append(
            f"{label} {_format_oi_usd(anchor.open_interest_value)}, {delta_pct:+.1f}%"
        )
    return current_closed, "; ".join(fragments), is_in_progress


# --- taker_flow (get_taker_flow) constants + helpers (spec §3.1-3.3) ---
_TAKER_FLOW_PERIOD_MS = {
    "5m": 5 * 60_000, "1h": 60 * 60_000, "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000, "1w": 7 * 24 * 60 * 60_000,
}
# context-anchor up-tier on the 5m->1h->4h->1d->1w ladder (§3.3). Keys are also
# the exact set of valid *tool* periods ({5m,1h,4h,1d}); 1w is anchor-only.
_TAKER_FLOW_ANCHOR = {"5m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}
_TAKER_FLOW_RVOL_BARS = 20  # fixed baseline window (closed bars), decoupled from limit


def _pick_usd_scale(values: list[float]) -> tuple[str, float]:
    """One $K/$M scale for a column, chosen from peak abs magnitude (§3.2)."""
    peak = max((abs(v) for v in values), default=0.0)
    return ("$M", 1e6) if peak >= 1e6 else ("$K", 1e3)


def _fmt_scaled(v: float, divisor: float) -> str:
    return f"{v / divisor:+.1f}"


def _fmt_hhmm(ts_ms: int) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%H:%M")


def _render_taker_flow(
    bars: list["TakerFlowBar"],
    period: str,
    limit: int,
    *,
    now_ms: int,
    symbol: str,
    fetch_ts: str,
    closes: dict[int, float] | None = None,
    close_note: str | None = None,
    anchor: tuple[str, "TakerFlowBar"] | None = None,
) -> str:
    """Render the taker-flow report. Pure + deterministic given now_ms.

    bars: ascending; bars[-1] is the in-progress current bucket (kept + labeled).
    closes: bar-open-ts -> close px (OHLCV join). close_note: when set, omit the
    Close column and emit this note instead (1d day-boundary, or OHLCV failure).
    anchor: (uptier_label, uptier_in_progress_bar) or None.
    """
    period_ms = _TAKER_FLOW_PERIOD_MS[period]
    period_min = period_ms / 60_000
    newest = bars[-1]
    is_in_progress = newest.ts + period_ms > now_ms
    elapsed_min = max(0.0, (now_ms - newest.ts) / 60_000)

    def _total(b): return b.sell_usd + b.buy_usd
    def _net(b): return b.buy_usd - b.sell_usd
    def _buy_pct(b):
        t = _total(b)
        return (b.buy_usd / t * 100) if t > 0 else 0.0

    display = bars[-limit:]                              # oldest..newest displayed
    closed = bars[:-1] if is_in_progress else bars
    baseline = closed[-_TAKER_FLOW_RVOL_BARS:]
    baseline_avg = (
        sum(_total(b) for b in baseline) / len(baseline)
        if len(baseline) >= _TAKER_FLOW_RVOL_BARS else None
    )

    # CVD cumulative over displayed window, from oldest displayed bar upward
    cvd_running, cvd_by_ts = 0.0, {}
    for b in display:                                    # ascending
        cvd_running += _net(b)
        cvd_by_ts[b.ts] = cvd_running

    scale_label, divisor = _pick_usd_scale([_net(b) for b in display] + list(cvd_by_ts.values()))

    lines = [f"=== Taker Flow ({symbol} · {period} bars · @{fetch_ts} UTC) ===", ""]

    now_rvol = (_total(newest) / baseline_avg) if baseline_avg else None
    rvol_now = f"{now_rvol:.1f}× (vs {_TAKER_FLOW_RVOL_BARS}-bar avg)" if now_rvol is not None else "—"
    formed = (f"current {period}, {elapsed_min:.1f}/{period_min:g}min formed"
              if is_in_progress else f"current {period}, closed")
    lines.append(
        f"Now ({formed}):  {_buy_pct(newest):.0f}% taker buy · "
        f"net {_fmt_scaled(_net(newest), divisor)}{scale_label} · vol {rvol_now}"
    )
    net_sell_n = sum(1 for b in display if _net(b) < 0)
    lines.append(
        f"Window ({len(display)} bars = {len(display) * period_min:g}min):  "
        f"CVD {_fmt_scaled(cvd_by_ts[display[-1].ts], divisor)}{scale_label} · "
        f"{net_sell_n}/{len(display)} bars net-sell"
    )
    lines.append("")

    # Close column: omitted if close_note set; else joined by ts; collapse w/ safety net
    show_close = close_note is None and closes is not None
    rendered_closes = {}
    if show_close:
        rendered_closes = {b.ts: closes.get(b.ts) for b in display}
        if all(v is None for v in rendered_closes.values()):
            show_close = False
            close_note = "Close: n/a — no OHLCV bar matched (timestamp join empty)"

    hdr = f"  Time     Buy%   Net({scale_label})   RVol(×20-bar)   CVD({scale_label})"
    if show_close:
        hdr += "   Close"
    lines.append("Per-bar (bar open UTC, newest first; row 1 = current in-progress):")
    lines.append(hdr)
    for b in reversed(display):                          # newest-first
        star = "*" if (is_in_progress and b is newest) else " "
        rvol = (_total(b) / baseline_avg) if baseline_avg else None
        rvol_s = f"{rvol:.1f}×" if rvol is not None else "—"
        row = (f"  {_fmt_hhmm(b.ts)}{star}  {_buy_pct(b):>3.0f}%  "
               f"{_fmt_scaled(_net(b), divisor):>7}  {rvol_s:>5}  "
               f"{_fmt_scaled(cvd_by_ts[b.ts], divisor):>8}")
        if show_close:
            c = rendered_closes.get(b.ts)
            row += f"  {c:.0f}" if c is not None else "  —"
        lines.append(row)
    if is_in_progress:
        lines.append(f"  [* row 1 = current bar still forming ({elapsed_min:.1f}/{period_min:g}min)]")
    if close_note is not None:
        lines.append(close_note)

    if anchor is not None:
        up_label, up_bar = anchor
        up_ms = _TAKER_FLOW_PERIOD_MS[up_label]
        up_in_prog = up_bar.ts + up_ms > now_ms
        up_elapsed = max(0.0, (now_ms - up_bar.ts) / 60_000)
        up_formed = (f"current {up_label}, {up_elapsed:.0f}min formed"
                     if up_in_prog else f"current {up_label}, closed")
        up_total = up_bar.sell_usd + up_bar.buy_usd
        up_buy = (up_bar.buy_usd / up_total * 100) if up_total > 0 else 0.0
        up_net = up_bar.buy_usd - up_bar.sell_usd
        up_scale, up_div = _pick_usd_scale([up_net])
        lines.append("")
        lines.append(
            f"{up_label}-scale anchor ({up_formed}):  "
            f"{up_buy:.0f}% buy · net {_fmt_scaled(up_net, up_div)}{up_scale}"
        )
    return "\n".join(lines)


async def get_taker_flow(deps: TradingDeps, period: str = "5m", limit: int = 6) -> str:
    """Minute-level taker buy/sell flow over `limit` `period`-bars (impl).

    LLM-visible docstring lives on the trader.py @tool wrapper.
    """
    import time
    from datetime import datetime, timezone

    symbol = deps.symbol
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    # Fact-only explicit reject (no clamp, no Literal narrowing — soft-constraint §1/§2)
    if period not in _TAKER_FLOW_ANCHOR:  # valid tool periods == {5m,1h,4h,1d}
        return f"Invalid period '{period}'. period must be one of: 5m, 1h, 4h, 1d"
    if not (1 <= limit <= 36):
        return f"Invalid limit {limit}. limit must be in [1, 36]"

    header = f"=== Taker Flow ({symbol} · {period} bars · @{fetch_ts} UTC) ==="
    n = max(limit + 1, 21)  # fetch enough for fixed-20 RVol baseline + in-progress

    # Main rubik series — hard dependency.
    try:
        bars = await deps.market_data.get_taker_flow(symbol, period, n)
    except Exception as e:
        logger.exception("get_taker_flow main fetch failed for %s", symbol)
        return f"{header}\nTaker flow temporarily unavailable ({e.__class__.__name__})."
    if not bars:
        return f"{header}\nNo taker-volume data available."

    now_ms = int(time.time() * 1000)

    # OHLCV Close join — soft. 1d: day-boundary mismatch (probe E 0/10) -> omit column.
    closes: dict[int, float] | None = None
    close_note: str | None = None
    if period == "1d":
        close_note = ("Close: n/a — 1d rubik/OHLCV day-boundary mismatch "
                      "(16:00 vs 00:00 UTC)")
    else:
        try:
            df = await deps.market_data.get_ohlcv_dataframe(symbol, period, limit=n)
            closes = {int(r.timestamp): float(r.close) for r in df.itertuples()}
        except Exception:
            logger.exception("get_taker_flow OHLCV join failed for %s", symbol)
            close_note = "Close: n/a — OHLCV temporarily unavailable"

    # Context anchor (up-tier in-progress bar) — soft; drop the line on failure/empty.
    anchor = None
    up_label = _TAKER_FLOW_ANCHOR[period]
    try:
        up_bars = await deps.market_data.get_taker_flow(symbol, up_label, 1)
        if up_bars:
            anchor = (up_label, up_bars[-1])
    except Exception:
        logger.exception("get_taker_flow anchor fetch failed for %s", symbol)

    return _render_taker_flow(
        bars, period, limit, now_ms=now_ms, symbol=symbol, fetch_ts=fetch_ts,
        closes=closes, close_note=close_note, anchor=anchor,
    )


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
    funding, oi_hist, lsr = await asyncio.gather(
        deps.market_data.get_funding_rate(symbol),
        deps.market_data.get_open_interest_history(symbol, "1h", 26),
        deps.market_data.get_long_short_ratio(symbol),
        return_exceptions=True,
    )

    # All-3-failed L2: emit single Error section.
    if (
        isinstance(funding, Exception)
        and isinstance(oi_hist, Exception)
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

    # Open interest history (closed-only anchors per G-calc-rigor-audit §G-6).
    if isinstance(oi_hist, Exception) or not oi_hist:
        field_lines.append("Open Interest: (unavailable)")
    else:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        current, anchors, was_shifted = _derive_oi_anchors(oi_hist, now_ms=now_ms)
        if current is None:
            field_lines.append("Open Interest: (unavailable)")
        else:
            oi_str = _format_oi_usd(current.open_interest_value)
            # OKX rubik `ts` is bucket open time; show close time explicitly so
            # narrative cannot read HH:MM as a snapshot timestamp (review f/u §2).
            bucket_close_dt = datetime.fromtimestamp(
                (current.timestamp + 3600 * 1000) / 1000, tz=timezone.utc,
            )
            bucket_close_label = bucket_close_dt.strftime("%H:%M UTC")
            ref = f"last 1H bucket closed at {bucket_close_label}"
            suffix_parts = [ref] if was_shifted else []
            if anchors:
                suffix_parts.append(anchors)
            if suffix_parts:
                field_lines.append(
                    f"Open Interest: {oi_str} ({'; '.join(suffix_parts)})"
                )
            else:
                field_lines.append(f"Open Interest: {oi_str}")
            if current.timestamp:
                timestamps_ms.append(current.timestamp)

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
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if deps.macro is None:
        return (
            f"=== Macro Context (@ {fetch_ts} UTC) ===\n"
            "Error: Macro service not configured."
        )

    try:
        snap = await deps.macro.get_snapshot()
    except Exception:
        logger.warning("Macro snapshot fetch failed", exc_info=True)
        return (
            f"=== Macro Context (@ {fetch_ts} UTC) ===\n"
            "Error: Temporarily unavailable."
        )

    sections: list[str] = []
    any_available = False

    # Crypto Market — first section in happy path; gets the inline fetch timestamp.
    cg_fields = (snap.btc_dominance, snap.eth_dominance,
                 snap.total_mcap_usd, snap.mcap_change_24h_pct)
    if all(v is None for v in cg_fields):
        sections.append(f"=== Crypto Market (@ {fetch_ts} UTC) ===\nTemporarily unavailable.")
    else:
        any_available = True
        btc = f"{snap.btc_dominance:.2f}%" if snap.btc_dominance is not None else "N/A"
        eth = f"{snap.eth_dominance:.2f}%" if snap.eth_dominance is not None else "N/A"
        mcap = _fmt_big_usd(snap.total_mcap_usd) if snap.total_mcap_usd else "N/A"
        chg = f"{snap.mcap_change_24h_pct:+.2f}%" if snap.mcap_change_24h_pct is not None else "N/A"
        sections.append(
            f"=== Crypto Market (@ {fetch_ts} UTC) ===\n"
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
            f"=== Macro Context (@ {fetch_ts} UTC) ===\n"
            "Error: All sources temporarily unavailable."
        )

    return "\n\n".join(sections)


async def get_etf_flows(deps: TradingDeps, days: int = 7) -> str:
    """US BTC + ETH spot ETF daily net flows + cumulative AUM.

    Emits a trailing footer reminding the Agent that today's value may be
    revised T+1 — this is an operational fact (spec §3.6) needed in-context
    to avoid misreading same-day values.
    """
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    if deps.crypto_etf is None:
        return (
            f"=== BTC Spot ETF Flows (US @ {fetch_ts} UTC) ===\n"
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

    def _render_section(label: str, flows, header_suffix: str = "") -> str:
        # Three-state rendering per spec §3.5:
        #   None → outage ("temporarily unavailable")
        #   []   → data-gap ("insufficient data" — window too short)
        #   list → normal
        # header_suffix is appended inside the "(US ...)" parenthetical — used
        # by the BTC (first) section to carry the inline fetch timestamp;
        # ETH section passes "" so its header stays plain.
        if flows is None:
            return f"=== {label} Spot ETF Flows (US{header_suffix}) ===\nTemporarily unavailable."
        if not flows:
            return (
                f"=== {label} Spot ETF Flows (US{header_suffix}) ===\n"
                f"Insufficient data in requested window."
            )
        lines = [f"=== {label} Spot ETF Flows (US{header_suffix}) ==="]
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
        _render_section("BTC", btc, header_suffix=f" @ {fetch_ts} UTC"),
        _render_section("ETH", eth),
    ]

    if btc is None and eth is None:
        return (
            f"=== BTC Spot ETF Flows (US @ {fetch_ts} UTC) ===\n"
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


def _fmt_ob_notional(usd: float) -> str:
    """Order book 规模量 USD notional 自适应 $K/$M（逐值）。"""
    # >= 999_950 rounds to $1.00M at M-precision (.2f); below it the K branch's .1f
    # would surface a $1000.0K seam. Keeps the K/M boundary continuous.
    if abs(usd) >= 999_950:
        return f"${usd/1e6:.2f}M"
    if abs(usd) >= 1e3:
        return f"${usd/1e3:.1f}K"
    return f"${usd:.0f}"


async def get_order_book(deps: TradingDeps, depth: int = ORDER_BOOK_DEPTH_DEFAULT) -> str:
    """Order book snapshot: best bid/ask, depth, bid/ask share, concentrated levels.

    Args:
        depth: Levels per side to fetch. Default 15.

    Returns:
        str: Multi-line fact-only text. Sizes are USD notional; distances are
        price points + bp. See spec docs/superpowers/specs/2026-05-30-order-book-depth-redesign-design.md.
    """
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    symbol = deps.symbol
    try:
        ob = await deps.market_data.get_order_book(symbol, depth=depth)
    except Exception as e:
        logger.exception("get_order_book failed for %s", symbol)
        return (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Error: Temporarily unavailable ({e.__class__.__name__})."
        )

    actual = min(len(ob.bids), len(ob.asks))
    if not ob.bids or not ob.asks or actual < depth:
        return (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Error: Insufficient data (requested depth {depth}, got {actual})."
        )

    best_bid = ob.bids[0]
    best_ask = ob.asks[0]
    mid = (best_bid.price + best_ask.price) / 2
    spread = best_ask.price - best_bid.price
    spread_bp = spread / mid * 10000

    # notional = amount(base) × price; bid share uses base-amount ratio (unit-invariant)
    bid_notional = sum(l.amount * l.price for l in ob.bids[:depth])
    ask_notional = sum(l.amount * l.price for l in ob.asks[:depth])
    total_bid = sum(l.amount for l in ob.bids[:depth])
    total_ask = sum(l.amount for l in ob.asks[:depth])
    total_sum = total_bid + total_ask
    if total_sum == 0:
        return (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Error: Insufficient data (requested depth {depth}, got {actual})."
        )

    bid_lo = ob.bids[depth - 1].price
    ask_hi = ob.asks[depth - 1].price
    bid_span = best_bid.price - bid_lo
    ask_span = ask_hi - best_ask.price
    bid_span_bp = bid_span / best_bid.price * 10000
    ask_span_bp = ask_span / best_ask.price * 10000

    # Bid share three-state, fact-only (no "balanced" label)
    if total_bid == 0 and total_ask > 0:
        share_line = f"Bid share: 0% (asks only, no bids in top {depth})"
    elif total_ask == 0 and total_bid > 0:
        share_line = f"Bid share: 100% (bids only, no asks in top {depth})"
    else:
        bid_share = total_bid / total_sum * 100
        bid_ratio = total_bid / total_ask
        share_line = f"Bid share: {bid_share:.1f}% (bid : ask = {bid_ratio:.2f} : 1, by size)"

    sections = [
        (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Best bid: {best_bid.price:.2f} × {_fmt_ob_notional(best_bid.amount * best_bid.price)}  |  "
            f"Best ask: {best_ask.price:.2f} × {_fmt_ob_notional(best_ask.amount * best_ask.price)}\n"
            f"Spread: {spread:.2f} pts ({spread_bp:.2f} bp)"
        ),
        (
            f"=== Depth (top {depth} each side) ===\n"
            f"  Bids: {_fmt_ob_notional(bid_notional)} over {best_bid.price:.2f} - {bid_lo:.2f}  "
            f"(span {bid_span:.2f} pts / {bid_span_bp:.1f} bp)\n"
            f"  Asks: {_fmt_ob_notional(ask_notional)} over {best_ask.price:.2f} - {ask_hi:.2f}  "
            f"(span {ask_span:.2f} pts / {ask_span_bp:.1f} bp)\n"
            f"  {share_line}"
        ),
    ]

    # Concentrated levels: threshold = 3× per-side median of top-N (张数维度; the
    # median spans the full top-N incl best). The scan below excludes best[0] —
    # it is already shown in the Best line, not because the median drops it.
    import statistics
    bid_median = statistics.median([l.amount for l in ob.bids[:depth]])
    ask_median = statistics.median([l.amount for l in ob.asks[:depth]])
    threshold_bid = bid_median * ORDER_BOOK_CONCENTRATION_MULTIPLIER
    threshold_ask = ask_median * ORDER_BOOK_CONCENTRATION_MULTIPLIER

    concentrated = []
    for l in ob.bids[1:depth]:  # exclude best bid (already in Best line)
        if l.amount > threshold_bid:
            concentrated.append(("Bid", l.price, l.amount))
    for l in ob.asks[1:depth]:  # exclude best ask
        if l.amount > threshold_ask:
            concentrated.append(("Ask", l.price, l.amount))

    if concentrated:
        concentrated.sort(key=lambda c: c[2], reverse=True)
        concentrated = concentrated[:ORDER_BOOK_MAX_CONCENTRATED_LEVELS]
        bids_conc = sorted([c for c in concentrated if c[0] == "Bid"], key=lambda c: -c[1])
        asks_conc = sorted([c for c in concentrated if c[0] == "Ask"], key=lambda c: c[1])
        conc_header = (
            f"=== Concentrated Levels (beyond best bid/ask, "
            f"size > {ORDER_BOOK_CONCENTRATION_MULTIPLIER:.0f}× median of top {depth}) ==="
        )
        conc_rows = [
            f"  {side}  {price:.2f}  {_fmt_ob_notional(amount * price)}"
            for side, price, amount in bids_conc + asks_conc
        ]
        sections.append(conc_header + "\n" + "\n".join(conc_rows))

    return "\n\n".join(sections)


async def get_recent_trades(deps: TradingDeps) -> str:
    """Seconds-level tick micro-view over the last ~500 trades (impl).

    Count-buckets (5×100), newest-first. USD notional = amount(base) × price
    (amount is base-currency after the fetch_trades adapter normalization, §4.2).
    LLM-visible docstring lives on the trader.py @tool wrapper.
    """
    import statistics
    from datetime import datetime, timezone

    symbol = deps.symbol
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    try:
        trades = await deps.market_data.get_recent_trades(symbol, limit=RECENT_TRADES_MAX_FETCH)
    except Exception as e:
        logger.exception("get_recent_trades failed for %s", symbol)
        return f"=== Recent Trades ({symbol} · @{fetch_ts} UTC) ===\nRecent trades temporarily unavailable ({e.__class__.__name__})."
    if not trades:
        return f"=== Recent Trades ({symbol} · @{fetch_ts} UTC) ===\nNo recent trades."

    trades = sorted(trades, key=lambda t: t.timestamp)  # ascending (defensive)
    n = len(trades)
    span_s = (trades[-1].timestamp - trades[0].timestamp) / 1000 or 1e-9
    usd = [t.amount * t.price for t in trades]
    total_usd = sum(usd) or 1e-9
    buy_usd = sum(u for u, t in zip(usd, trades) if t.side == "buy")
    buy_cnt = sum(1 for t in trades if t.side == "buy")
    net_usd = buy_usd - (total_usd - buy_usd)

    lines = [f"=== Recent Trades ({symbol} · last {n} · {span_s:.1f}s · @{fetch_ts} UTC) ===", ""]
    lines.append(
        f"Taker buy:  {buy_cnt / n * 100:.0f}% by count · {buy_usd / total_usd * 100:.0f}% by volume"
        f"      Net: {_fmt_money(net_usd)} · {n / span_s:.1f} tr/s"
    )
    li = max(range(n), key=lambda i: usd[i])
    lines.append(
        f"Largest single:  {_fmt_money(usd[li]).lstrip('+')} {trades[li].side.upper()}"
        f"  (= {usd[li] / total_usd * 100:.1f}% of window vol)"
    )
    srt = sorted(usd)
    med = statistics.median(srt)
    p95 = srt[min(int(0.95 * n), n - 1)]
    lines.append(
        f"Size (USD notional):  med {_fmt_money(med).lstrip('+')} · "
        f"mean {_fmt_money(total_usd / n).lstrip('+')} · p95 {_fmt_money(p95).lstrip('+')}"
    )

    # Count-buckets, newest-first. Fewer than one full slice (<100) -> no table.
    if n >= RECENT_TRADES_SLICE_SIZE:
        slices = []
        hi = n
        while hi > 0 and len(slices) < RECENT_TRADES_N_SLICES:
            lo = max(0, hi - RECENT_TRADES_SLICE_SIZE)
            slices.append(trades[lo:hi])  # ascending chunk; slices[0]=newest
            hi = lo
        lines.append("")
        lines.append("Per 100-trade slice (newest first):")
        lines.append("  Slice    Span   Buy%(cnt)  Buy%(vol)    Net($)    MaxTrade")
        for si, chunk in enumerate(slices):
            cu = [t.amount * t.price for t in chunk]
            ctot = sum(cu) or 1e-9
            cbuy_usd = sum(u for u, t in zip(cu, chunk) if t.side == "buy")
            cbuy_cnt = sum(1 for t in chunk if t.side == "buy")
            cspan = (chunk[-1].timestamp - chunk[0].timestamp) / 1000
            cnet = cbuy_usd - (ctot - cbuy_usd)
            mi = max(range(len(chunk)), key=lambda k: cu[k])
            mside = "B" if chunk[mi].side == "buy" else "S"
            label = f"{si + 1}"
            if si == 0:
                label += " (new)"
            elif si == len(slices) - 1:
                label += " (old)"
            cnt_note = "" if len(chunk) == RECENT_TRADES_SLICE_SIZE else f" [{len(chunk)} tr]"
            lines.append(
                f"  {label:<8} {cspan:>4.1f}s   {cbuy_cnt / len(chunk) * 100:>4.0f}%      "
                f"{cbuy_usd / ctot * 100:>4.0f}%   {_fmt_money(cnet):>8}   "
                f"{_fmt_money(cu[mi]).lstrip('+')} {mside}{cnt_note}"
            )
    return "\n".join(lines)


async def get_multi_timeframe_snapshot(deps: TradingDeps, tfs: list[str] | None = None) -> str:
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
    import asyncio
    import pandas as pd
    from datetime import datetime, timezone
    from src.utils.ohlcv_utils import _live_price, _closed_bars, _atr_series

    symbol = deps.symbol
    if tfs is None:
        tfs = ["5m", "1h", "4h", "1d"]

    try:
        ticker = await deps.market_data.get_ticker(symbol)
        live_price = _live_price(ticker)
    except Exception:
        logger.exception("get_multi_timeframe_snapshot ticker fetch failed for %s", symbol)
        return f"=== Multi-TF Snapshot ({symbol}) ===\nError: Temporarily unavailable."

    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    async def _fetch_one(tf: str) -> tuple[str, pd.DataFrame | Exception]:
        try:
            df = await deps.market_data.get_ohlcv_dataframe(
                symbol, tf, limit=MULTI_TF_OHLCV_LIMIT.get(tf, 250),
            )
            return tf, df
        except Exception as e:
            return tf, e

    results = await asyncio.gather(*[_fetch_one(tf) for tf in tfs])

    if all(isinstance(r[1], Exception) for r in results):
        return (
            f"=== Multi-TF Snapshot ({symbol}) ===\n"
            f"Error: Temporarily unavailable (all timeframes failed)."
        )

    # First pass: compute MA fast-vs-slow direction tags per tf.
    direction_tags: list[str] = []
    rows: list[str] = []

    # Fixed seconds per tf, used to derive the "close @ T UTC" timestamp on
    # the Last 3 closes line. For 1M the fixed 30-day step is an approximation
    # (real months range 28-31 days) — when df has more than one closed bar
    # available, the implementation below prefers `df['timestamp'].iloc[-1]`
    # (the in-progress candle's open = the just-closed candle's close moment)
    # over this constant, which is exact for all tfs at the cost of one
    # row's data availability. The constant remains the fallback when the
    # next-bar timestamp is absent.
    _TF_SECONDS = {
        "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
        "4h": 14400, "1d": 86400, "1w": 7 * 86400, "1M": 30 * 86400,
    }

    for tf, df_or_err in results:
        primary_n = MULTI_TF_PRIMARY_MA.get(tf, 50)
        fast_n, slow_n = MULTI_TF_STRUCTURE_MAS.get(tf, (50, 200))
        if isinstance(df_or_err, Exception):
            rows.append(f"[{tf}]  temporarily unavailable")
            continue
        df = df_or_err
        df_closed = _closed_bars(df)
        if df_closed.empty or len(df_closed) < max(slow_n, 20) + 1:
            rows.append(f"[{tf}]  insufficient data (need {slow_n + 1} candles, got {len(df_closed)})")
            continue

        close = df_closed["close"]
        ma_fast = float(close.rolling(fast_n).mean().iloc[-1])
        ma_slow = float(close.rolling(slow_n).mean().iloc[-1])
        primary_ma = float(close.rolling(primary_n).mean().iloc[-1])

        # Cross-tf direction tag is a 2-way side proxy per spec §3 example
        # (5m below | 1h above | ...). Spec does not surface a third "flat"
        # state here; entanglement (< 0.1%) is rendered via "≈" in the
        # per-tf Structure column below, not in this summary line.
        direction_tags.append(f"{tf} {'above' if ma_fast > ma_slow else 'below'}")

        mom_pct = (live_price - primary_ma) / primary_ma * 100.0 if primary_ma > 0 else 0.0
        diff_pct = abs(ma_fast - ma_slow) / ma_slow * 100.0 if ma_slow > 0 else 0.0
        if diff_pct < 0.1:
            op = "≈"
        elif ma_fast > ma_slow:
            op = ">"
        else:
            op = "<"
        struct_str = f"MA{fast_n}: {ma_fast:.2f} {op} MA{slow_n}: {ma_slow:.2f}"
        # 1w/1M use (20, 50) instead of native (50, 200) due to weekly/monthly
        # history shortage in the MTS 20-bar window context — mark as degraded
        # so the agent reads them as fact-with-caveat, not as native structure
        # (spec §5.3; preserved from baseline tools_perception.py:1506-1509).
        if tf in ("1w", "1M"):
            struct_str += " (short-structure)"

        # ATR%, ratio
        atr_str = "ATR N/A"
        if len(df_closed) >= 35:
            atr_series = _atr_series(df_closed, period=14)
            atr_now = float(atr_series.iloc[-1])
            atr_avg = float(atr_series.rolling(20).mean().iloc[-1])
            atr_pct = atr_now / live_price * 100.0
            atr_ratio = atr_now / atr_avg if atr_avg > 0 else 0.0
            atr_str = f"ATR {atr_pct:.2f}% (20p avg {atr_avg / live_price * 100:.2f}%, {atr_ratio:.2f}×)"

        # Range pos (no clamping, per §3.2)
        last_20 = df_closed.iloc[-MULTI_TF_RANGE_PERIODS:]
        hi = float(last_20["high"].max())
        lo = float(last_20["low"].min())
        range_pct = (live_price - lo) / (hi - lo) * 100.0 if hi != lo else 0.0

        # Last 3 closes line — "closed @ T UTC" anchor. Prefer the in-progress
        # candle's timestamp (df.iloc[-1]['timestamp']) which equals the
        # just-closed candle's official close moment exactly; fall back to
        # last_closed_ts + _TF_SECONDS only if df has no in-progress bar at all.
        # Exact for 1M (no 30-day approximation drift).
        if len(df) > len(df_closed):
            close_dt = datetime.fromtimestamp(
                int(df["timestamp"].iloc[-1]) / 1000, tz=timezone.utc
            )
        else:
            last_closed_ts_ms = int(df_closed["timestamp"].iloc[-1])
            close_moment_s = last_closed_ts_ms / 1000 + _TF_SECONDS.get(tf, 0)
            close_dt = datetime.fromtimestamp(close_moment_s, tz=timezone.utc)
        closes_3 = df_closed["close"].iloc[-3:].tolist()
        last3_str = "→".join(f"{c:.2f}" for c in closes_3)

        row1 = (
            f"[{tf}]  Mom {mom_pct:+.1f}% (vs MA{primary_n}) | {struct_str} | "
            f"{atr_str} | Range pos {range_pct:.0f}%"
        )
        row2 = f"      Last 3 closes (closed @ {close_dt.strftime('%Y-%m-%d %H:%M')} UTC): {last3_str}"
        rows.append(row1)
        rows.append(row2)
        rows.append("")

    tags_str = " | ".join(direction_tags) if direction_tags else "(no data)"
    header_lines = [
        f"=== Multi-TF Snapshot ({symbol}) ===",
        f"Last (ticker @ {fetch_ts} UTC): {live_price:.2f}",
        f"MA fast-vs-slow per tf: {tags_str}",
        "Columns: Momentum (live ticker vs primary MA, %) | Structure (fast MA value vs slow MA value, with comparison) | Volatility (ATR % of price; ratio vs 20-period ATR avg) | Range pos (live ticker price within 20-bar closed-bar high-low; 0%=Low, 100%=High) | Last 3 closed candle closes",
        "",
    ]
    return "\n".join(header_lines + rows).rstrip()


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

    Caller contract: `df` must be closed-bars-only (no in-progress final bar) —
    bars_ago=0 anchors at the most-recent closed bar. Otherwise neighbors of
    near-end candidates leak in-progress high/low into the pivot test (see
    G-calc-rigor-audit §G-2).
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


def _fmt_age_humanized(seconds: float) -> str:
    """Render a wall-clock duration as a humanized 'X ago' suffix.

    Thresholds:
      < 60s    → 'just now'
      < 60min  → 'Nm ago'         (e.g. '5m ago')
      < 24h    → 'Hh Mm ago'      (e.g. '2h 15m ago')
      >= 24h   → 'Dd Hh ago'      (e.g. '1d 4h ago')

    seconds is non-negative; negative input (clock skew) clamps to 0.
    """
    s = max(0, int(seconds))
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        h, rem = divmod(s, 3600)
        return f"{h}h {rem // 60}m ago"
    d, rem = divmod(s, 86400)
    return f"{d}d {rem // 3600}h ago"


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
    from datetime import datetime, timezone

    from src.utils.ohlcv_utils import _closed_bars

    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

    symbol = deps.symbol
    main_tf = deps.timeframe

    try:
        ticker = await deps.market_data.get_ticker(symbol)
        current_price = ticker.last
    except Exception:
        logger.exception("get_price_pivots ticker fetch failed for %s", symbol)
        return (
            f"=== Price Pivots ({symbol}, main TF: {main_tf} @ {fetch_ts} UTC) ===\n"
            f"Error: Temporarily unavailable."
        )

    async def _fetch(tf: str, limit: int):
        try:
            return await deps.market_data.get_ohlcv_dataframe(symbol, tf, limit=limit)
        except Exception as e:
            return e

    # Main-TF: fetch 101 so that after stripping the in-progress final bar via
    # _closed_bars (G-calc-rigor-audit §G-2), the swing-pivot window is exactly
    # 100 closed bars. Prior-period TFs (daily/weekly/monthly) intentionally
    # take iloc[-2] downstream — that is already the closed prior period.
    main_df_or_err, daily_or_err, weekly_or_err, monthly_or_err = await asyncio.gather(
        _fetch(main_tf, 101),
        _fetch("1d", 2),
        _fetch("1w", 2),
        _fetch("1M", 2),
    )

    swing_status: str | None = None
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    if isinstance(main_df_or_err, Exception):
        swing_status = "Swing pivots: temporarily unavailable"
    elif main_df_or_err is None or main_df_or_err.empty:
        swing_status = "Swing pivots: insufficient data (need 11+ bars, got 0)"
    else:
        main_df_closed = _closed_bars(main_df_or_err)
        bar_count = len(main_df_closed)
        if bar_count < 11:
            swing_status = f"Swing pivots: insufficient data (need 11+ bars, got {bar_count})"
        else:
            swing_highs, swing_lows = _compute_swing_pivots(main_df_closed, n=5)
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
        f"=== Price Pivots ({symbol}, main TF: {main_tf} @ {fetch_ts} UTC) ===",
        f"Last: {current_price:.2f}",
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
