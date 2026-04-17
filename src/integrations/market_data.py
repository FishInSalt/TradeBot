from __future__ import annotations
import pandas as pd
from src.integrations.exchange.base import BaseExchange, FundingRate, LongShortRatio, OpenInterest, Ticker
from src.utils.cache import TTLCache

_DERIVATIVES_TTL = 180.0  # 3 minutes


class MarketDataService:
    def __init__(self, exchange: BaseExchange):
        self._exchange = exchange
        self._derivatives_cache = TTLCache()

    async def get_current_price(self, symbol: str) -> float:
        ticker = await self._exchange.fetch_ticker(symbol)
        return ticker.last

    async def get_ticker(self, symbol: str) -> Ticker:
        return await self._exchange.fetch_ticker(symbol)

    async def get_ohlcv_dataframe(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        candles = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        return pd.DataFrame([
            {"timestamp": c.timestamp, "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume}
            for c in candles
        ])

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        return await self._derivatives_cache.get_or_fetch(
            f"funding:{symbol}", _DERIVATIVES_TTL,
            lambda: self._exchange.fetch_funding_rate(symbol),
        )

    async def get_open_interest(self, symbol: str) -> OpenInterest:
        return await self._derivatives_cache.get_or_fetch(
            f"oi:{symbol}", _DERIVATIVES_TTL,
            lambda: self._exchange.fetch_open_interest(symbol),
        )

    async def get_long_short_ratio(self, symbol: str) -> LongShortRatio:
        return await self._derivatives_cache.get_or_fetch(
            f"lsr:{symbol}", _DERIVATIVES_TTL,
            lambda: self._exchange.fetch_long_short_ratio(symbol),
        )
