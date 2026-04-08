from __future__ import annotations
import pandas as pd
from src.integrations.exchange.base import BaseExchange, Ticker


class MarketDataService:
    def __init__(self, exchange: BaseExchange):
        self._exchange = exchange

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
