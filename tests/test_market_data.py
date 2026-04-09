import pytest
from unittest.mock import AsyncMock
from src.integrations.exchange.base import Candle, Ticker


@pytest.fixture
def mock_exchange():
    exchange = AsyncMock()
    exchange.fetch_ticker.return_value = Ticker(
        symbol="BTC/USDT:USDT", last=65000.0, bid=64999.0,
        ask=65001.0, high=66000.0, low=64000.0,
        base_volume=12345.6, timestamp=1712534400000,
    )
    exchange.fetch_ohlcv.return_value = [
        Candle(1712534400000, 64000.0, 65500.0, 63800.0, 65000.0, 1000.0),
        Candle(1712535300000, 65000.0, 65800.0, 64900.0, 65500.0, 800.0),
    ]
    return exchange


async def test_get_current_price(mock_exchange):
    from src.integrations.market_data import MarketDataService
    service = MarketDataService(mock_exchange)
    price = await service.get_current_price("BTC/USDT:USDT")
    assert price == 65000.0


async def test_get_ohlcv_dataframe(mock_exchange):
    from src.integrations.market_data import MarketDataService
    service = MarketDataService(mock_exchange)
    df = await service.get_ohlcv_dataframe("BTC/USDT:USDT", "15m", limit=2)
    assert len(df) == 2
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert df.iloc[0]["close"] == 65000.0
