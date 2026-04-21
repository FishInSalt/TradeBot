import pytest
from unittest.mock import AsyncMock
from src.integrations.exchange.base import Candle, OrderBook, OrderBookLevel, Ticker, Trade


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


@pytest.mark.asyncio
async def test_market_data_get_order_book_delegates_to_exchange():
    """MarketDataService.get_order_book is a thin wrapper with no caching."""
    from src.integrations.market_data import MarketDataService
    exchange = AsyncMock()
    exchange.fetch_order_book.return_value = OrderBook(
        symbol="BTC/USDT:USDT",
        bids=[OrderBookLevel(100.0, 1.0)],
        asks=[OrderBookLevel(101.0, 1.0)],
        timestamp=123,
    )
    svc = MarketDataService(exchange)
    ob = await svc.get_order_book("BTC/USDT:USDT", depth=5)
    assert ob.symbol == "BTC/USDT:USDT"
    exchange.fetch_order_book.assert_called_once_with("BTC/USDT:USDT", depth=5)


@pytest.mark.asyncio
async def test_market_data_get_recent_trades_delegates():
    """MarketDataService.get_recent_trades is a thin wrapper."""
    from src.integrations.market_data import MarketDataService
    exchange = AsyncMock()
    exchange.fetch_trades.return_value = [Trade(timestamp=1, side="buy", price=100.0, amount=0.01, trade_id="x")]
    svc = MarketDataService(exchange)
    trades = await svc.get_recent_trades("BTC/USDT:USDT", limit=500)
    assert len(trades) == 1
    exchange.fetch_trades.assert_called_once_with("BTC/USDT:USDT", limit=500)
