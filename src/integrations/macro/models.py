"""Data models for macro context (CG /global + FRED + Alpha Vantage)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FREDObservation:
    """A single FRED series observation.

    `date` is an ISO "YYYY-MM-DD" string. FRED series have daily granularity
    but different report delays (DTWEXBGS often lags ~1 week; VIX/DGS10 daily).
    The `date` field lets the Agent see each value's actual observation date.
    """
    series_id: str
    date: str
    value: float


@dataclass(frozen=True)
class EquityQuote:
    """Alpha Vantage GLOBAL_QUOTE response — SPY / QQQ."""
    symbol: str
    price: float
    change_pct: float          # 24h %, e.g. +1.21
    latest_trading_day: str    # ISO "YYYY-MM-DD"


@dataclass(frozen=True)
class MacroSnapshot:
    """Aggregate of CG /global + FRED + Alpha Vantage.

    Every field is Optional so sub-source failures degrade independently
    (spec §3.2). The FRED USD-index field is named `usd_index_broad_tw`
    rather than `dxy` because the series is DTWEXBGS (Fed Broad TW index,
    26 currencies, basis 2006=100), NOT the ICE DXY (6 currencies, basis
    1973=100). See spec §2.2 for the rationale.
    """
    # CoinGecko /global
    btc_dominance: float | None
    eth_dominance: float | None
    total_mcap_usd: float | None
    mcap_change_24h_pct: float | None

    # FRED
    usd_index_broad_tw: FREDObservation | None
    vix: FREDObservation | None
    treasury_10y: FREDObservation | None
    spread_10y_2y: FREDObservation | None
    inflation_10y: FREDObservation | None

    # Alpha Vantage
    spy: EquityQuote | None
    qqq: EquityQuote | None
