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
