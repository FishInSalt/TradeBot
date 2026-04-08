from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

import ccxt.async_support as ccxt

from src.integrations.exchange.base import (
    Balance,
    BaseExchange,
    Candle,
    Order,
    Position,
    Ticker,
)

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def _retry(max_retries: int = 3, base_delay: float = 1.0):
    """Exponential backoff retry decorator for async exchange methods."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_error: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (
                    ccxt.NetworkError,
                    ccxt.ExchangeNotAvailable,
                    asyncio.TimeoutError,
                ) as e:
                    last_error = e
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"{func.__name__} attempt {attempt + 1}/{max_retries} "
                        f"failed: {e}, retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)
            raise last_error  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


class OKXExchange(BaseExchange):
    def __init__(self, api_key: str, secret: str, password: str):
        self._client = ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": password,
                "options": {"defaultType": "swap"},
                "timeout": 30000,
            }
        )
        logger.info("OKX exchange initialized (real account)")

    @_retry()
    async def fetch_ticker(self, symbol: str) -> Ticker:  # type: ignore[override]
        data = await self._client.fetch_ticker(symbol)
        return Ticker(
            symbol=data["symbol"],  # type: ignore[arg-type]
            last=float(data["last"]),  # type: ignore[arg-type]
            bid=float(data["bid"]),  # type: ignore[arg-type]
            ask=float(data["ask"]),  # type: ignore[arg-type]
            high=float(data["high"]),  # type: ignore[arg-type]
            low=float(data["low"]),  # type: ignore[arg-type]
            base_volume=float(data["baseVolume"]),  # type: ignore[arg-type]
            timestamp=data["timestamp"],  # type: ignore[arg-type]
        )

    @_retry()
    async def fetch_ohlcv(  # type: ignore[override]
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        data = await self._client.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [
            Candle(
                timestamp=int(r[0]),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in data
        ]

    @_retry()
    async def create_order(  # type: ignore[override]
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
    ) -> Order:
        data = await self._client.create_order(
            symbol, order_type, side, amount, price  # type: ignore[arg-type]
        )
        return Order(
            id=data["id"],  # type: ignore[arg-type]
            symbol=data["symbol"],  # type: ignore[arg-type]
            side=data["side"],  # type: ignore[arg-type]
            order_type=data["type"],  # type: ignore[arg-type]
            amount=float(data["amount"]),  # type: ignore[arg-type]
            price=float(data["price"]) if data.get("price") else None,  # type: ignore[arg-type]
            status=data["status"],  # type: ignore[arg-type]
        )

    @_retry()
    async def fetch_balance(self) -> Balance:  # type: ignore[override]
        data = await self._client.fetch_balance()
        return Balance(
            total_usdt=float(data["total"].get("USDT", 0)),
            free_usdt=float(data["free"].get("USDT", 0)),
            used_usdt=float(data["used"].get("USDT", 0)),
        )

    @_retry()
    async def fetch_positions(self, symbol: str) -> list[Position]:  # type: ignore[override]
        data = await self._client.fetch_positions([symbol])
        return [
            Position(
                symbol=p["symbol"],  # type: ignore[arg-type]
                side=p["side"],  # type: ignore[arg-type]
                contracts=float(p["contracts"]),  # type: ignore[arg-type]
                entry_price=float(p["entryPrice"]),  # type: ignore[arg-type]
                unrealized_pnl=float(p["unrealizedPnl"]),  # type: ignore[arg-type]
                leverage=int(p["leverage"]),  # type: ignore[arg-type]
                liquidation_price=(
                    float(p["liquidationPrice"])  # type: ignore[arg-type]
                    if p.get("liquidationPrice")
                    else None
                ),
            )
            for p in data
            if float(p["contracts"]) > 0  # type: ignore[arg-type]
        ]

    @_retry()
    async def set_leverage(self, symbol: str, leverage: int) -> None:  # type: ignore[override]
        await self._client.set_leverage(leverage, symbol)

    async def close(self) -> None:
        await self._client.close()
