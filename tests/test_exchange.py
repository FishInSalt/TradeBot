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
        # Intentionally omitted: fetch_order, fetch_open_orders, fetch_closed_orders,
        # fetch_order_book, fetch_trades, get_contract_size — this test verifies
        # abstract-method enforcement, so IncompleteExchange must remain incomplete.

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


async def test_okx_fetch_order():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.fetch_order.return_value = {
        "id": "order_123", "symbol": "BTC/USDT:USDT",
        "side": "buy", "type": "market", "amount": 0.01,
        "price": 65000.0, "status": "closed", "fee": {"cost": 0.325},
    }
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    order = await exchange.fetch_order("order_123")
    assert order.id == "order_123"
    assert order.fee == 0.325


async def test_okx_fetch_open_orders():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    # Iter 2b T3: fetch_open_orders 现在走 3-way asyncio.gather (plain +
    # conditional algo + oco algo). Plain path返回一单, algo path 返回空.
    plain_order = {
        "id": "o1", "symbol": "BTC/USDT:USDT", "side": "sell", "type": "stop",
        "amount": 0.01, "price": 93000.0, "status": "open",
    }

    async def fake_fetch(symbol, params=None):
        params = params or {}
        if not params.get("stop"):
            return [plain_order]
        return []

    mock_ccxt.fetch_open_orders = AsyncMock(side_effect=fake_fetch)
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    orders = await exchange.fetch_open_orders("BTC/USDT:USDT")
    assert len(orders) == 1
    assert orders[0].status == "open"


async def test_okx_fetch_closed_orders():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.fetch_orders.return_value = [
        {"id": "o1", "symbol": "BTC/USDT:USDT", "side": "buy", "type": "market",
         "amount": 0.01, "price": 95000.0, "status": "closed", "fee": {"cost": 0.475}},
    ]
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    orders = await exchange.fetch_closed_orders("BTC/USDT:USDT", limit=10)
    assert len(orders) == 1
    assert orders[0].fee == 0.475


async def test_okx_create_order_parses_fee():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.create_order.return_value = {
        "id": "order_456", "symbol": "BTC/USDT:USDT",
        "side": "buy", "type": "market", "amount": 0.01,
        "price": 65000.0, "status": "closed", "fee": {"cost": 0.325},
    }
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    order = await exchange.create_order(symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01)
    assert order.fee == 0.325


async def test_okx_create_order_fee_none_when_missing():
    from src.integrations.exchange.okx import OKXExchange
    mock_ccxt = AsyncMock()
    mock_ccxt.create_order.return_value = {
        "id": "order_789", "symbol": "BTC/USDT:USDT",
        "side": "buy", "type": "market", "amount": 0.01,
        "price": 65000.0, "status": "closed",
    }
    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_ccxt
    order = await exchange.create_order(symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01)
    assert order.fee is None


def test_fill_event_from_base():
    """FillEvent should be importable from base.py with pnl field."""
    from src.integrations.exchange.base import FillEvent
    event = FillEvent(
        order_id="o1", symbol="BTC/USDT:USDT", side="buy",
        position_side="long", trigger_reason="market",
        fill_price=60200.0, amount=0.001, fee=0.03,
        pnl=None, timestamp=1712534400000,
    )
    assert event.pnl is None

    event_with_pnl = FillEvent(
        order_id="o2", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", trigger_reason="stop",
        fill_price=58394.0, amount=0.001, fee=0.03,
        pnl=-1.35, timestamp=1712534401000,
    )
    assert event_with_pnl.pnl == -1.35


async def test_okx_cancel_order(monkeypatch):
    from src.integrations.exchange.okx import OKXExchange
    from unittest.mock import AsyncMock, MagicMock
    import ccxt.async_support as ccxt

    mock_client = MagicMock(spec=ccxt.okx)
    mock_client.cancel_order = AsyncMock(return_value={"id": "o1", "status": "cancelled"})

    exchange = OKXExchange.__new__(OKXExchange)
    exchange._client = mock_client

    await exchange.cancel_order("o1", "BTC/USDT:USDT")
    mock_client.cancel_order.assert_called_once_with("o1", "BTC/USDT:USDT")


async def test_base_exchange_start_default_noop():
    """BaseExchange.start() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest(self, symbol): ...
        async def fetch_long_short_ratio(self, symbol): ...

        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0

    ex = DummyExchange()
    await ex.start()  # 不应抛异常


def test_base_exchange_on_fill_default_noop():
    """BaseExchange.on_fill() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest(self, symbol): ...
        async def fetch_long_short_ratio(self, symbol): ...

        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0

    ex = DummyExchange()
    callback = AsyncMock()
    ex.on_fill(callback)  # 不应抛异常


def test_base_exchange_on_alert_default_noop():
    """BaseExchange.on_alert() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest(self, symbol): ...
        async def fetch_long_short_ratio(self, symbol): ...

        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0

    ex = DummyExchange()
    callback = AsyncMock()
    ex.on_alert(callback)  # 不应抛异常


def test_base_exchange_set_alert_service_default_noop():
    """BaseExchange.set_alert_service() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest(self, symbol): ...
        async def fetch_long_short_ratio(self, symbol): ...

        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0

    ex = DummyExchange()
    ex.set_alert_service(object())  # 不应抛异常


def test_base_exchange_update_alert_params_default_noop():
    """BaseExchange.update_alert_params() 默认空实现不应抛异常。"""
    from src.integrations.exchange.base import BaseExchange

    class DummyExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest(self, symbol): ...
        async def fetch_long_short_ratio(self, symbol): ...

        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0

    ex = DummyExchange()
    ex.update_alert_params(3.0, 5)  # 不应抛异常


async def test_base_exchange_has_pending_market_order_default():
    """BaseExchange.has_pending_market_order returns False by default."""
    from src.integrations.exchange.base import BaseExchange
    class _Stub(BaseExchange):
        async def fetch_ticker(self, s): ...
        async def fetch_ohlcv(self, s, t, limit=100): ...
        async def create_order(self, s, side, ot, amt, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, s): ...
        async def set_leverage(self, s, l): ...
        def amount_to_precision(self, s, a): ...
        async def close(self): ...
        async def fetch_order(self, oid, s=None): ...
        async def fetch_open_orders(self, s): ...
        async def fetch_closed_orders(self, s, limit=20): ...
        async def cancel_order(self, oid, s): ...
        async def fetch_funding_rate(self, s): ...
        async def fetch_open_interest(self, s): ...
        async def fetch_long_short_ratio(self, s): ...

        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0
    stub = _Stub()
    assert stub.has_pending_market_order("BTC/USDT:USDT") is False
    assert stub.has_pending_market_order("BTC/USDT:USDT", side="buy") is False
