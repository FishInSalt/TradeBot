from __future__ import annotations

import asyncio
import logging
from functools import wraps
from typing import Callable, TypeVar

import ccxt.async_support as ccxt

from src.integrations.exchange.base import Balance, BaseExchange, Candle, Order, Position, Ticker

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _retry(max_retries: int = 3, base_delay: float = 1.0):
    """Exponential backoff retry decorator for async exchange methods."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, asyncio.TimeoutError) as e:
                    last_error = e
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"{func.__name__} attempt {attempt + 1}/{max_retries} failed: {e}, retrying in {delay}s")
                    await asyncio.sleep(delay)
            raise last_error
        return wrapper
    return decorator


class OKXExchange(BaseExchange):
    def __init__(self, api_key: str, secret: str, password: str):
        self._client = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": password,
            "options": {"defaultType": "swap"},
            "timeout": 30000,
        })
        logger.info("OKX exchange initialized (real account)")

    @_retry()
    async def fetch_ticker(self, symbol: str) -> Ticker:
        data = await self._client.fetch_ticker(symbol)
        return Ticker(
            symbol=data["symbol"], last=float(data["last"]),
            bid=float(data["bid"]), ask=float(data["ask"]),
            high=float(data["high"]), low=float(data["low"]),
            base_volume=float(data["baseVolume"]), timestamp=data["timestamp"],
        )

    @_retry()
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        data = await self._client.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [
            Candle(timestamp=int(r[0]), open=float(r[1]), high=float(r[2]),
                   low=float(r[3]), close=float(r[4]), volume=float(r[5]))
            for r in data
        ]

    @_retry()
    async def create_order(self, symbol: str, side: str, order_type: str, amount: float, price: float | None = None) -> Order:
        data = await self._client.create_order(symbol, order_type, side, amount, price)
        return Order(
            id=data["id"], symbol=data["symbol"], side=data["side"],
            order_type=data["type"], amount=float(data["amount"]),
            price=float(data["price"]) if data.get("price") else None,
            status=data["status"],
        )

    @_retry()
    async def fetch_balance(self) -> Balance:
        data = await self._client.fetch_balance()
        return Balance(
            total_usdt=float(data["total"].get("USDT", 0)),
            free_usdt=float(data["free"].get("USDT", 0)),
            used_usdt=float(data["used"].get("USDT", 0)),
        )

    @_retry()
    async def fetch_positions(self, symbol: str) -> list[Position]:
        data = await self._client.fetch_positions([symbol])
        return [
            Position(
                symbol=p["symbol"], side=p["side"],
                contracts=float(p["contracts"]),
                entry_price=float(p["entryPrice"]),
                unrealized_pnl=float(p["unrealizedPnl"]),
                leverage=int(p["leverage"]),
                liquidation_price=float(p["liquidationPrice"]) if p.get("liquidationPrice") else None,
            )
            for p in data if float(p["contracts"]) > 0
        ]

    @_retry()
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        await self._client.set_leverage(leverage, symbol)

    async def close(self) -> None:
        await self._client.close()
