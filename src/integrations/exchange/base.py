from __future__ import annotations
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


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


@dataclass
class Balance:
    total_usdt: float
    free_usdt: float
    used_usdt: float


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
    def __init__(self):
        self._price_level_alerts: list[dict] = []
        self._latest_price: float | None = None
        self._alert_service: Any | None = None

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Ticker: ...
    @abstractmethod
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]: ...
    @abstractmethod
    async def create_order(self, symbol: str, side: str, order_type: str, amount: float, price: float | None = None) -> Order: ...
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
    async def cancel_order(self, order_id: str, symbol: str) -> None: ...

    async def start(self) -> None:
        """启动 WebSocket 等后台任务。默认空实现。"""
        pass

    def on_fill(self, callback: Callable[['FillEvent'], Awaitable[None]]) -> None:
        """注册 fill 回调。默认空实现。"""
        pass

    def on_alert(self, callback: Callable[[Any], Awaitable[None]]) -> None:
        """注册价格异动回调。默认空实现。"""
        pass

    def set_alert_service(self, service: Any) -> None:
        """Inject PriceAlertService instance."""
        self._alert_service = service

    def update_alert_params(self, threshold_pct: float, window_minutes: int) -> None:
        """Update price alert parameters. Delegates to alert service if set."""
        if self._alert_service:
            self._alert_service.update_params(threshold_pct, window_minutes)

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
        })
        return alert_id

    def remove_price_level_alert(self, alert_id: str) -> bool:
        for i, a in enumerate(self._price_level_alerts):
            if a["id"] == alert_id:
                self._price_level_alerts.pop(i)
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
                ))
            else:
                remaining.append(alert)
        self._price_level_alerts = remaining
        return triggered

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


@dataclass
class PriceLevelAlertInfo:
    symbol: str
    target_price: float
    direction: str          # "above" / "below"
    current_price: float
    reasoning: str
    timestamp: int
