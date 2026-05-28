"""Shared OHLCV helpers for multi-TF perception tools.

Spec ref: docs/superpowers/specs/2026-05-11-iter-w2r2-next-d-multi-tf-design.md §6.4.

These helpers exist to make the live-state vs closed-bar contract
explicit at every call site in MTS / GMD / HTF, and to lock the
algorithm primitives (pandas_ta.atr mamode='rma', closed-only strip,
ticker.last live-price source) that the §2.2.1 algorithm-lock
invariant rests on for signals MTS and HTF both surface at shared
timeframes (4h, 1d).
"""
from __future__ import annotations
from typing import Any
import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]
from pandas.tseries.offsets import DateOffset


def _live_price(ticker: Any) -> float:
    """Canonical live current price.

    Empirically (verify_ohlcv_semantics_v2.py 2026-05-10) approximately
    equal to df['close'].iloc[-1] within a sub-bps drift floor (~0.01 bps
    observed in 31-sample window; not strictly equal due to sub-second
    trade flow between independent ticker and OHLCV API calls). Choose
    ticker.last as the canonical live-price source for code-semantic
    clarity ('this is the live decision-time price').
    """
    return float(ticker.last)


def _closed_bars(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV with the in-progress candle stripped.

    Empirically (verify_ohlcv_semantics_v2.py 2026-05-10): in a 31-sample,
    1m timeframe window with two candle rotations, the closed-only MA(5)
    showed 0.0000 drift while the full-df MA(5) drifted by 0.0200 in
    the same candle window. Stripping is required for temporally stable
    per-cycle facts.
    """
    return df.iloc[:-1]


def _atr_series(df_closed: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR(period) **series** computed with Wilder's smoothing (mamode='rma').

    services/technical.py compute_indicators calls pandas_ta.atr(...) which
    currently defaults to mamode='rma' in pandas_ta 0.x; this helper passes
    mamode='rma' explicitly so the algorithm is locked against future
    library default changes. Drift-guard test_atr_series_last_value_equals_compute_indicators_atr_14
    enforces bit-for-bit equality of the last value vs compute_indicators.
    """
    return ta.atr(  # type: ignore[no-any-return]
        df_closed["high"], df_closed["low"], df_closed["close"],
        length=period, mamode="rma",
    )


# === iter-tool-opt-gmd-polish: shared helpers ===

TF_OFFSETS: dict[str, pd.Timedelta | DateOffset] = {
    # Intraday minute
    "1m":  pd.Timedelta(minutes=1),
    "3m":  pd.Timedelta(minutes=3),
    "5m":  pd.Timedelta(minutes=5),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    # Hour
    "1h":  pd.Timedelta(hours=1),
    "2h":  pd.Timedelta(hours=2),
    "4h":  pd.Timedelta(hours=4),
    "6h":  pd.Timedelta(hours=6),
    "8h":  pd.Timedelta(hours=8),
    "12h": pd.Timedelta(hours=12),
    # Day / week
    "1d":  pd.Timedelta(days=1),
    "3d":  pd.Timedelta(days=3),
    "1w":  pd.Timedelta(weeks=1),
    # Month (calendar-aware; 28-31 days not fixed)
    "1M":  DateOffset(months=1),
}


def _to_pd_timestamp_utc(ts_val: Any) -> pd.Timestamp:
    """Coerce OHLCV timestamp to tz-aware pd.Timestamp UTC.

    Mirrors the isinstance dispatch at tools_perception.py:164-168 — OHLCV
    timestamp column may be int/float ms-epoch OR datetime depending on the
    exchange adapter. Both produce equivalent UTC pd.Timestamp here.
    """
    if isinstance(ts_val, (int, float)):
        return pd.Timestamp(ts_val, unit="ms", tz="UTC")
    ts = pd.Timestamp(ts_val)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")


def _fmt_candle_time(dt: pd.Timestamp, tf: str) -> str:
    """Format a candle's open-time per tf granularity.

    Unified dispatch shared by OHLCV table row rendering AND in-progress
    candle hint rendering (both consumers in tools_perception.get_market_data).

    The OKX/CCXT timeframe `"1M"` is **case-sensitive** (uppercase M = month,
    lowercase m = minute) — `"1M"` is checked first BEFORE `tf.lower()` to
    avoid the lowered `"1m"` accidentally matching the month branch.

    Unknown tf falls back to `%Y-%m-%d` (matches existing default fallback at
    tools_perception.py:175). Does NOT raise — preserves backward-compat.
    """
    if tf == "1M":  # month — case-sensitive uppercase; must be checked before lower()
        return dt.strftime("%Y-%m")
    tf_lower = tf.lower()
    if tf_lower in ("1m", "3m", "5m", "15m", "30m"):
        return dt.strftime("%H:%M")
    if tf_lower in ("1h", "2h", "4h", "6h", "8h", "12h"):
        return dt.strftime("%m-%d %H:%M")
    if tf_lower in ("1d", "3d", "1w"):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")  # degraded fallback for unknown tf
