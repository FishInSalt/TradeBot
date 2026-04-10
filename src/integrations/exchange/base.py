from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


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


class BaseExchange(ABC):
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

    def drain_pending_fills(self) -> list['FillEvent']:
        """Return and clear queued FillEvents. Default: empty (OKX etc. need not override)."""
        return []


@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: str
    position_side: str
    trigger_reason: str    # market / stop / take_profit / liquidation
    fill_price: float
    amount: float
    fee: float
    pnl: float | None      # 已实现盈亏（开仓时 None）
    timestamp: int
