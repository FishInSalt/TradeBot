from __future__ import annotations
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from src.services.price_alert import PriceAlertService

logger = logging.getLogger(__name__)

# OKX rubik stat endpoint requires uppercase '1H' / '1D'; project convention exposes
# lowercase across abstractions (matches fetch_ohlcv(timeframe='1h')). The mapping
# below is the only translation layer.
_OKX_OI_PERIOD = {"5m": "5m", "1h": "1H", "1d": "1D"}
# taker-volume rubik endpoint period map. DELIBERATELY distinct from
# _OKX_OI_PERIOD: the legal period set differs (taker flow exposes 15m + 4h + 1w;
# OI does not), so reusing _OKX_OI_PERIOD would KeyError on 15m/4h/1w. 1w is
# included only as the 1d-period anchor up-tier (§3.3), not a standalone tool period.
_TAKER_VOLUME_PERIOD = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}


@dataclass
class Ticker:
    symbol: str
    last: float
    bid: float
    ask: float
    high: float
    low: float
    base_volume: float
    timestamp: int


@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Order:
    id: str
    symbol: str
    side: str
    order_type: str
    amount: float
    price: float | None
    status: str
    fee: float | None = None
    is_algo: bool = False
    trigger_price: float | None = None   # R2-7 §4.7


@dataclass
class Balance:
    total_usdt: float
    free_usdt: float
    used_usdt: float


@dataclass
class OrderBookLevel:
    price: float
    amount: float  # base-currency


@dataclass
class OrderBook:
    symbol: str
    bids: list[OrderBookLevel]  # sorted by price DESC (best first)
    asks: list[OrderBookLevel]  # sorted by price ASC (best first)
    timestamp: int | None  # CCXT may return None in some exchanges/conditions


@dataclass
class Trade:
    timestamp: int  # ms
    side: str       # "buy" | "sell" (taker direction per CCXT unified spec)
    price: float
    amount: float   # base-currency
    trade_id: str | None


@dataclass
class Position:
    symbol: str
    side: str
    contracts: float
    entry_price: float
    unrealized_pnl: float
    leverage: int
    liquidation_price: float | None
    created_at: datetime | None = None


class BaseExchange(ABC):
    algo_trigger_reference: str = "last"
    """Word used in distance-label rendering at the five sites listed in
    docs/superpowers/specs/2026-05-14-iter-tool-opt-mark-vs-last-design.md §3.1.
    OKX algo orders default trigger reference is last (project does not set
    triggerPxType). Subclasses for exchanges whose default differs override
    this attribute (e.g., Bybit V5 requires explicit triggerBy; Hyperliquid
    uses mark or oracle uniformly).
    """

    def __init__(self):
        self._price_level_alerts: list[dict] = []
        self._latest_price: float | None = None
        self._alert_service: PriceAlertService | None = None
        self._fill_callback: Callable[['FillEvent'], Awaitable[None]] | None = None

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker: ...

    @abstractmethod
    async def get_mark_price(self, symbol: str) -> float:
        """Fetch mark price for the symbol. Used by get_position for
        liquidation-distance calculation. Raises on endpoint failure or empty
        response (no silent fallback).
        """
        ...

    @abstractmethod
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]: ...
    # create_order return type is heterogeneous by order_type:
    #   market → settles synchronously (sim) → FillEvent (actual fill_price/fee/
    #            pnl/entry_price); callers dispatch on isinstance(result, FillEvent).
    #   limit / stop / take_profit → Order (status='open'); fills later (async),
    #            notifies via the fill callback.
    # (OKX live path, deferred, still returns Order for market — CLAUDE.md Tier 3;
    #  the FillEvent branch is sim-only for now.)
    @abstractmethod
    async def create_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> Order | FillEvent: ...
    @abstractmethod
    async def fetch_balance(self) -> Balance: ...
    @abstractmethod
    async def fetch_positions(self, symbol: str) -> list[Position]: ...
    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> None: ...
    @abstractmethod
    def amount_to_precision(self, symbol: str, amount: float) -> float: ...
    @abstractmethod
    async def close(self) -> None: ...
    @abstractmethod
    async def fetch_order(self, order_id: str, symbol: str | None = None) -> Order: ...
    @abstractmethod
    async def fetch_open_orders(self, symbol: str) -> list[Order]: ...
    @abstractmethod
    async def fetch_closed_orders(self, symbol: str, limit: int = 20) -> list[Order]: ...
    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str, is_algo: bool = False) -> None: ...
    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> 'FundingRate': ...
    @abstractmethod
    async def fetch_open_interest_history(
        self,
        symbol: str,
        period: Literal["5m", "1h", "1d"] = "1h",
        limit: int = 26,
    ) -> list["OpenInterestHistoryPoint"]: ...
    @abstractmethod
    async def fetch_taker_flow(
        self,
        symbol: str,
        period: Literal["5m", "1h", "4h", "1d", "1w"] = "5m",
        limit: int = 6,
    ) -> list["TakerFlowBar"]:
        """Taker buy/sell volume bars (USD notional) from rubik taker-volume.

        Returns oldest-first; the LAST bar is the in-progress current bucket
        (returned raw, no detection/labeling — that is the tool layer's job).
        """
        ...
    @abstractmethod
    async def fetch_long_short_ratio(self, symbol: str) -> 'LongShortRatio': ...
    @abstractmethod
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook: ...
    @abstractmethod
    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]: ...
    @abstractmethod
    async def get_contract_size(self, symbol: str) -> float:
        """Contract multiplier (base currency per contract). OKX BTC/USDT:USDT swap = 0.01 BTC/contract.
        Both Sim and OKX cache the real market contractSize at start()."""
        ...

    async def start(self) -> None:
        """启动 WebSocket 等后台任务。默认空实现。"""
        pass

    def on_fill(self, callback: Callable[['FillEvent'], Awaitable[None]]) -> None:
        """注册 fill 回调。"""
        self._fill_callback = callback

    def register_close_order_entry(self, order_id: str, entry_price: float) -> None:
        """Hook for exchange impls to record per-order entry_price for close fills.

        Default: no-op (sim path captures entry_price directly in fill event from
        in-memory _Position; OKX path overrides this method to populate
        _close_order_entry_cache, consumed by _parse_fill_event).

        Called by close-direction tools (close_position / set_stop_loss /
        set_take_profit) immediately after create_order returns.
        """
        return None

    def on_alert(self, callback: Callable[[Any], Awaitable[None]]) -> None:
        """注册价格异动回调。默认空实现。"""
        pass

    def set_volatility_alert(self, threshold_pct: float,
                             window_minutes: int, symbol: str) -> None:
        """Lazy-create on first call, update_params on subsequent calls.
        Replacing parameters resets the rolling tick window (PriceAlertService
        update_params semantics)."""
        if self._alert_service is None:
            self._alert_service = PriceAlertService(symbol, window_minutes, threshold_pct)
        else:
            self._alert_service.update_params(threshold_pct, window_minutes)

    def cancel_volatility_alert(self) -> None:
        """Clear the singleton; subsequent ticks no longer evaluate volatility."""
        self._alert_service = None

    def get_alert_params(self) -> tuple[float, int] | None:
        """Return (threshold_pct, window_minutes) or None if alerts disabled."""
        if self._alert_service is not None:
            return self._alert_service.get_params()
        return None

    def get_price_level_alerts(self) -> list[dict]:
        """Return a copy of active price level alerts."""
        return list(self._price_level_alerts)

    def has_pending_market_order(self, symbol: str, side: str | None = None) -> bool:
        """Check for pending market orders. Default: False (real exchanges don't track client-side)."""
        return False

    def add_price_level_alert(self, price: float, direction: str,
                               symbol: str, reasoning: str) -> str | None:
        """Add a price level alert. Returns alert_id, or None if at limit (20)."""
        if len(self._price_level_alerts) >= 20:
            return None
        alert_id = str(uuid.uuid4())[:8]
        self._price_level_alerts.append({
            "id": alert_id, "price": price, "direction": direction,
            "symbol": symbol, "reasoning": reasoning,
            "created_at": time.time(),
        })
        return alert_id

    def remove_price_level_alert(self, alert_id: str) -> bool:
        for i, a in enumerate(self._price_level_alerts):
            if a["id"] == alert_id:
                self._price_level_alerts.pop(i)
                return True
        return False

    def update_price_level_alert(self, alert_id: str, new_price: float,
                                  new_reasoning: str) -> bool:
        """In-place update of an existing price level alert.

        Overwrites price, reasoning, and created_at on the matching alert dict;
        preserves id, direction, and symbol. Returns True if a matching alert
        was found and updated, False otherwise.
        """
        for alert in self._price_level_alerts:
            if alert["id"] == alert_id:
                alert["price"] = new_price
                alert["reasoning"] = new_reasoning
                alert["created_at"] = time.time()
                return True
        return False

    def _check_price_levels(self, current_price: float,
                             timestamp: int) -> list['PriceLevelAlertInfo']:
        triggered = []
        remaining = []
        for alert in self._price_level_alerts:
            if (alert["direction"] == "above" and current_price >= alert["price"]) or \
               (alert["direction"] == "below" and current_price <= alert["price"]):
                triggered.append(PriceLevelAlertInfo(
                    symbol=alert["symbol"], target_price=alert["price"],
                    direction=alert["direction"], current_price=current_price,
                    reasoning=alert["reasoning"], timestamp=timestamp,
                    alert_id=alert["id"],
                ))
            else:
                remaining.append(alert)
        self._price_level_alerts = remaining
        return triggered

    def clear_level_alerts_by_symbol(self, symbol: str) -> int:
        """Remove all price level alerts matching symbol. Returns count cleared.

        Used by _clear_stale_alerts_for_full_close on close fills. Also exposed
        as a standalone method for tests / future use.
        """
        before = len(self._price_level_alerts)
        self._price_level_alerts = [
            a for a in self._price_level_alerts if a["symbol"] != symbol
        ]
        return before - len(self._price_level_alerts)

    async def _dispatch_fill_event(self, fill: 'FillEvent') -> None:
        """Entry point for fill event dispatch.

        Subclasses MUST route all FillEvent through this method, not call
        self._fill_callback directly. Internal split into two SRP units:
        alert hygiene (clear) and callback fan-out (invoke).

        Order semantics: clear-before-callback. The callback observes the
        final post-hygiene state (alert list already filtered). If a future
        callback needs to capture stale-alert context for diagnostic logging,
        either reorder the dispatch or add a pre-clear hook.
        """
        self._clear_stale_alerts_for_full_close(fill)
        await self._invoke_fill_callback(fill)

    def _clear_stale_alerts_for_full_close(self, fill: 'FillEvent') -> None:
        """SRP unit 1: alert hygiene. Clear all level alerts for fill.symbol
        if and only if the fill closes the position fully (is_full_close).
        """
        if not fill.is_full_close:
            return
        cleared = self.clear_level_alerts_by_symbol(fill.symbol)
        if cleared > 0:
            logger.info(
                "Cleared %d stale price-level alert(s) on full close fill: "
                "symbol=%s order_id=%s",
                cleared, fill.symbol, fill.order_id,
            )

    async def _invoke_fill_callback(self, fill: 'FillEvent') -> None:
        """SRP unit 2: callback fan-out with failure isolation.

        Callback exceptions are logged, not propagated, so one fill's
        callback failure does not block subsequent fill processing.
        """
        if self._fill_callback is None:
            return
        try:
            await self._fill_callback(fill)
        except Exception:
            logger.exception("Fill callback failed for order %s", fill.order_id)

@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: str
    position_side: str
    trigger_reason: str    # market / limit / stop / take_profit / liquidation
    fill_price: float
    amount: float
    fee: float
    pnl: float | None      # 已实现盈亏（开仓时 None）
    timestamp: int
    is_full_close: bool    # True iff 该 fill 把 symbol 持仓清零（用于 alert 清理）
    entry_price: float | None = None
    """Position weighted-avg entry price at fill time (per contract).

    For close fills (pnl is not None): exchange-layer-filled actual position
    entry price (before any pnl_cap clamping in sim). Used by cli renderer
    to compute round-trip net without reverse-engineering from pnl.

    For open fills (pnl is None): always None — by design.
    Rationale: open fill 的 entry 信息已通过 fill_price 表达；entry_price 字段
    语义专用于 close fill 的 position weighted-avg entry。统一 open fill 永远
    None 避免半态字段导致后续误用。
    """


@dataclass
class PriceLevelAlertInfo:
    symbol: str
    target_price: float
    direction: str          # "above" / "below"
    current_price: float
    reasoning: str
    timestamp: int
    alert_id: str           # 8-char hex id (uuid4()[:8]); enables alert lifecycle join


@dataclass
class FundingRate:
    symbol: str
    rate: float  # current funding rate (e.g., 0.000125 = 0.0125%)
    next_funding_time: int  # next settlement timestamp (ms)
    timestamp: int


@dataclass
class OpenInterestHistoryPoint:
    """One historical OI snapshot at a given timestamp.

    open_interest_value is USD-denominated. No `symbol` field is carried —
    a list of history points always belongs to one symbol and the caller
    holds that context.
    """
    timestamp: int
    open_interest: float  # base-currency amount
    open_interest_value: float  # USD value


@dataclass
class TakerFlowBar:
    """One taker-volume bucket from OKX rubik taker-volume-contract (unit=2, USD).

    `ts` is the bucket OPEN time (ms); intervals equal the requested period. The
    newest bar returned by the endpoint is the in-progress CURRENT bucket — the
    fetch layer returns it raw (no detection, no formed% — this dataclass carries
    no formed field); the tool layer detects in-progress via
    `ts + period_ms > now_ms` and labels formed% (§3.2/§4.1).
    """
    ts: int
    sell_usd: float
    buy_usd: float


@dataclass
class LongShortRatio:
    symbol: str
    long_short_ratio: float  # raw ratio (e.g., 1.35)
    long_ratio: float  # derived: ratio / (1 + ratio)
    short_ratio: float  # derived: 1 / (1 + ratio)
    timestamp: int
