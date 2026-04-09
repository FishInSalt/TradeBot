import pytest
from unittest.mock import AsyncMock


def test_order_has_fee_field():
    from src.integrations.exchange.base import Order
    order = Order(
        id="o1", symbol="BTC/USDT:USDT", side="buy", order_type="market",
        amount=0.01, price=65000.0, status="closed", fee=1.5,
    )
    assert order.fee == 1.5


def test_order_fee_defaults_to_none():
    from src.integrations.exchange.base import Order
    order = Order(
        id="o1", symbol="BTC/USDT:USDT", side="buy", order_type="market",
        amount=0.01, price=65000.0, status="closed",
    )
    assert order.fee is None


def test_base_exchange_requires_new_methods():
    from src.integrations.exchange.base import BaseExchange, Order, Balance, Position, Ticker, Candle

    class IncompleteExchange(BaseExchange):
        async def fetch_ticker(self, symbol: str) -> Ticker: ...
        async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]: ...
        async def create_order(self, symbol: str, side: str, order_type: str, amount: float, price: float | None = None) -> Order: ...
        async def fetch_balance(self) -> Balance: ...
        async def fetch_positions(self, symbol: str) -> list[Position]: ...
        async def set_leverage(self, symbol: str, leverage: int) -> None: ...
        def amount_to_precision(self, symbol: str, amount: float) -> float: ...
        async def close(self) -> None: ...
        # fetch_order, fetch_open_orders, fetch_closed_orders intentionally omitted

    with pytest.raises(TypeError):
        IncompleteExchange()


def test_base_exchange_is_abstract():
    from src.integrations.exchange.base import BaseExchange
    with pytest.raises(TypeError):
        BaseExchange()


async def test_okx_fetch_ticker():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.fetch_ticker.return_value = {
        "symbol": "BTC/USDT:USDT", "last": 65000.0, "bid": 64999.0, "ask": 65001.0,
        "high": 66000.0, "low": 64000.0, "baseVolume": 12345.6, "timestamp": 1712534400000,
    }
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    ticker = await exchange.fetch_ticker("BTC/USDT:USDT")
    assert ticker.last == 65000.0
    assert ticker.bid == 64999.0


async def test_okx_fetch_ohlcv():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.fetch_ohlcv.return_value = [
        [1712534400000, 64000.0, 65500.0, 63800.0, 65000.0, 1000.0],
        [1712535300000, 65000.0, 65800.0, 64900.0, 65500.0, 800.0],
    ]
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    candles = await exchange.fetch_ohlcv("BTC/USDT:USDT", "15m", limit=2)
    assert len(candles) == 2
    assert candles[0].close == 65000.0


async def test_okx_create_order():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.create_order.return_value = {
        "id": "order_123", "symbol": "BTC/USDT:USDT",
        "side": "buy", "type": "market", "amount": 0.01,
        "price": 65000.0, "status": "closed",
    }
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    order = await exchange.create_order(symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01)
    assert order.id == "order_123"
    assert order.status == "closed"


async def test_okx_fetch_balance():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.fetch_balance.return_value = {
        "total": {"USDT": 10000.0}, "free": {"USDT": 8000.0}, "used": {"USDT": 2000.0},
    }
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    balance = await exchange.fetch_balance()
    assert balance.total_usdt == 10000.0
    assert balance.free_usdt == 8000.0


async def test_okx_fetch_positions():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.fetch_positions.return_value = [
        {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.01,
         "entryPrice": 65000.0, "unrealizedPnl": 50.0, "leverage": 3, "liquidationPrice": 55000.0}
    ]
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    positions = await exchange.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].entry_price == 65000.0
