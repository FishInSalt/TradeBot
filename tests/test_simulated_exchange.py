import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.exchange.base import Ticker
from src.integrations.exchange.base import FillEvent


def _make_exchange(initial_balance=100.0, fee_rate=0.0005, symbol="BTC/USDT:USDT"):
    """Helper: create a SimulatedExchange without async start()."""
    from src.integrations.exchange.simulated import SimulatedExchange

    config = MagicMock()
    config.fee_rate = fee_rate
    config.precision = {"BTC/USDT:USDT": 3, "ETH/USDT:USDT": 2}

    exchange = SimulatedExchange(config=config, db_engine=None, session_id="test-session", symbol=symbol)
    exchange._free_usdt = initial_balance
    exchange._used_usdt = 0.0
    exchange._frozen_usdt = 0.0
    exchange._positions = {}
    exchange._pending_orders = []
    exchange._leverage = {}
    exchange._latest_ticker = Ticker(
        symbol=symbol, last=95000.0, bid=94990.0, ask=95010.0,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534400000,
    )
    exchange._running = True
    return exchange


async def test_fetch_balance_initial():
    ex = _make_exchange(initial_balance=100.0)
    balance = await ex.fetch_balance()
    assert balance.free_usdt == 100.0
    assert balance.used_usdt == 0.0
    assert balance.total_usdt == 100.0


async def test_fetch_balance_with_unrealized_pnl():
    ex = _make_exchange(initial_balance=70.0)
    ex._used_usdt = 30.0
    ex._positions["BTC/USDT:USDT"] = MagicMock(
        side="long", contracts=0.001, entry_price=94000.0, leverage=3,
    )
    balance = await ex.fetch_balance()
    assert balance.total_usdt == pytest.approx(100.99)
    assert balance.free_usdt == pytest.approx(70.99)
    assert balance.used_usdt == 30.0


async def test_fetch_balance_free_clamps_to_zero():
    ex = _make_exchange(initial_balance=5.0)
    ex._used_usdt = 30.0
    ex._positions["BTC/USDT:USDT"] = MagicMock(
        side="long", contracts=0.001, entry_price=100000.0, leverage=3,
    )
    balance = await ex.fetch_balance()
    assert balance.free_usdt == 0.0


async def test_market_buy_opens_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    assert order.status == "closed"
    assert order.price == 95010.0
    assert order.fee == pytest.approx(95010.0 * 0.001 * 0.0005)
    assert order.amount == 0.001

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].contracts == 0.001

    balance = await ex.fetch_balance()
    margin = 95010.0 * 0.001 / 3
    assert balance.used_usdt == pytest.approx(margin)


async def test_market_sell_opens_short():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert order.price == 94990.0
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert positions[0].side == "short"


async def test_market_order_insufficient_balance():
    ex = _make_exchange(initial_balance=1.0)
    ex._leverage["BTC/USDT:USDT"] = 1
    with pytest.raises(ValueError, match="Insufficient balance"):
        await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)


async def test_market_order_wrong_symbol():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="Symbol mismatch"):
        await ex.create_order("ETH/USDT:USDT", "buy", "market", 0.001)


async def test_market_order_invalid_amount():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="amount must be > 0"):
        await ex.create_order("BTC/USDT:USDT", "buy", "market", 0)


async def test_market_order_unknown_type():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="Unknown order_type"):
        await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001)


async def test_market_close_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert order.status == "closed"
    assert order.price == 94990.0
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0
    balance = await ex.fetch_balance()
    assert balance.used_usdt == 0.0


async def test_market_close_clamps_amount():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.999)
    assert order.amount == 0.001


async def test_add_to_position():
    ex = _make_exchange(initial_balance=200.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=96000.0, bid=95990.0, ask=96010.0,
        high=97000.0, low=94000.0, base_volume=1000.0, timestamp=1712534500000,
    )
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert positions[0].contracts == 0.002
    expected_entry = (95010.0 * 0.001 + 96010.0 * 0.001) / 0.002
    assert positions[0].entry_price == pytest.approx(expected_entry)


async def test_add_position_leverage_mismatch():
    ex = _make_exchange(initial_balance=200.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    ex._leverage["BTC/USDT:USDT"] = 5
    with pytest.raises(ValueError, match="Leverage mismatch"):
        await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)


async def test_set_leverage():
    ex = _make_exchange()
    await ex.set_leverage("BTC/USDT:USDT", 5)
    assert ex._leverage["BTC/USDT:USDT"] == 5


async def test_set_leverage_rejects_float():
    ex = _make_exchange()
    with pytest.raises(TypeError):
        await ex.set_leverage("BTC/USDT:USDT", 2.5)


async def test_set_leverage_rejects_out_of_range():
    ex = _make_exchange()
    with pytest.raises(ValueError):
        await ex.set_leverage("BTC/USDT:USDT", 200)


async def test_set_leverage_rejects_with_position():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    with pytest.raises(ValueError, match="Cannot change leverage"):
        await ex.set_leverage("BTC/USDT:USDT", 5)


def test_amount_to_precision():
    ex = _make_exchange()
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.001567) == 0.001
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.0019999) == 0.001


def test_amount_to_precision_unknown_symbol():
    ex = _make_exchange()
    with pytest.raises(KeyError):
        ex.amount_to_precision("UNKNOWN/USDT:USDT", 1.0)


async def test_stop_order_creation():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)
    assert order.status == "open"
    assert order.price == 93000.0
    assert order.order_type == "stop"
    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    assert len(open_orders) == 1


async def test_stop_order_without_position():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="Cannot create conditional order without a position"):
        await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)


async def test_stop_order_without_price():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    with pytest.raises(ValueError, match="price is required"):
        await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001)


async def test_conditional_order_forces_full_amount():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.0005, price=93000.0)
    assert order.amount == 0.001  # forced to position.contracts


async def test_should_trigger_stop_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    tick = Ticker(
        symbol="BTC/USDT:USDT", last=92800.0, bid=92790.0, ask=92810.0,
        high=96000.0, low=92000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)

    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "stop"
    assert fill_events[0].fill_price == 92790.0  # bid for long close
    assert fill_events[0].position_side == "long"
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0
    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    assert len(open_orders) == 0


async def test_should_trigger_take_profit_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "take_profit", 0.001, price=97000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    tick = Ticker(
        symbol="BTC/USDT:USDT", last=97500.0, bid=97490.0, ask=97510.0,
        high=98000.0, low=94000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)
    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "take_profit"


async def test_liquidation_triggers_before_stop():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 10
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    tick = Ticker(
        symbol="BTC/USDT:USDT", last=80000.0, bid=79990.0, ask=80010.0,
        high=96000.0, low=79000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)
    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "liquidation"
    balance = await ex.fetch_balance()
    assert balance.free_usdt >= 0.0


async def test_should_trigger_stop_short():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "buy", "stop", 0.001, price=97000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    tick = Ticker(
        symbol="BTC/USDT:USDT", last=97200.0, bid=97190.0, ask=97210.0,
        high=98000.0, low=94000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)
    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "stop"
    assert fill_events[0].fill_price == 97210.0  # ask for short close
    assert fill_events[0].position_side == "short"


async def test_should_trigger_take_profit_short():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "buy", "take_profit", 0.001, price=93000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    tick = Ticker(
        symbol="BTC/USDT:USDT", last=92800.0, bid=92790.0, ask=92810.0,
        high=96000.0, low=92000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)
    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "take_profit"
    assert fill_events[0].position_side == "short"


async def test_no_trigger_when_price_above_stop():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    tick = Ticker(
        symbol="BTC/USDT:USDT", last=94000.0, bid=93990.0, ask=94010.0,
        high=96000.0, low=93000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)
    assert len(fill_events) == 0
    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    assert len(open_orders) == 1


async def test_persist_and_restore():
    """State should survive persist -> new instance -> restore."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session
    from src.integrations.exchange.simulated import SimulatedExchange

    engine = await init_db("sqlite+aiosqlite:///:memory:")

    async with get_session(engine) as sess:
        sess.add(Session(id="test-s", name="test", initial_balance=100.0))
        await sess.commit()

    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}

    ex1 = SimulatedExchange(config, engine, "test-s", "BTC/USDT:USDT")
    await ex1._init_state(initial_balance=100.0)
    ex1._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534400000,
    )
    ex1._leverage["BTC/USDT:USDT"] = 3
    await ex1.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex1._persist_state()

    ex2 = SimulatedExchange(config, engine, "test-s", "BTC/USDT:USDT")
    await ex2._restore_state()
    ex2._latest_ticker = ex1._latest_ticker

    balance = await ex2.fetch_balance()
    assert balance.used_usdt > 0

    positions = await ex2.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].side == "long"

    await engine.dispose()


async def test_fetch_closed_orders_from_db():
    """Market orders should be queryable via fetch_closed_orders."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session
    from src.integrations.exchange.simulated import SimulatedExchange

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as sess:
        sess.add(Session(id="test-s2", name="test2", initial_balance=100.0))
        await sess.commit()

    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}

    ex = SimulatedExchange(config, engine, "test-s2", "BTC/USDT:USDT")
    await ex._init_state(initial_balance=100.0)
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534400000,
    )
    ex._leverage["BTC/USDT:USDT"] = 3

    order = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    closed = await ex.fetch_closed_orders("BTC/USDT:USDT")
    assert len(closed) == 1
    assert closed[0].id == order.id
    assert closed[0].status == "closed"
    assert closed[0].fee is not None

    fetched = await ex.fetch_order(order.id)
    assert fetched.id == order.id
    assert fetched.price == order.price

    await engine.dispose()


async def test_partial_close_position():
    """Partial close should release proportional margin and keep remaining position."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.002)

    # Close half
    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert order.amount == 0.001

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].contracts == pytest.approx(0.001)

    # Conditional orders should NOT be cancelled (position still open)
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)
    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    assert len(open_orders) == 1


async def test_liquidation_short():
    """Short position should be liquidated when ask rises above liquidation price."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 10
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    # Price surges above liquidation
    tick = Ticker(
        symbol="BTC/USDT:USDT", last=120000.0, bid=119990.0, ask=120010.0,
        high=121000.0, low=94000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(tick)

    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "liquidation"
    assert fill_events[0].position_side == "short"
    balance = await ex.fetch_balance()
    assert balance.free_usdt >= 0.0


async def test_cancel_order():
    ex = _make_exchange(initial_balance=100.0)
    await ex.set_leverage("BTC/USDT:USDT", 3)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    sl_order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)
    assert len(await ex.fetch_open_orders("BTC/USDT:USDT")) == 1
    await ex.cancel_order(sl_order.id, "BTC/USDT:USDT")
    assert len(await ex.fetch_open_orders("BTC/USDT:USDT")) == 0


async def test_cancel_nonexistent_order():
    ex = _make_exchange()
    with pytest.raises(ValueError, match="not found"):
        await ex.cancel_order("nonexistent-id", "BTC/USDT:USDT")


async def test_fill_event_carries_pnl_on_stop():
    """When a stop order triggers, FillEvent should include pnl."""
    ex = _make_exchange(initial_balance=100.0)
    fills = []
    ex.on_fill(lambda event: fills.append(event) or asyncio.sleep(0))

    await ex.set_leverage("BTC/USDT:USDT", 3)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)

    drop_ticker = Ticker("BTC/USDT:USDT", 89000.0, 89000.0, 89010.0,
                         96000.0, 88000.0, 1000.0, 1712534500000)
    await ex._process_tick(drop_ticker)

    assert len(fills) == 1
    assert fills[0].trigger_reason == "stop"
    assert fills[0].pnl is not None
    assert fills[0].pnl < 0


async def test_market_order_queues_fill_event():
    """Market order should queue FillEvent, not call callback immediately."""
    ex = _make_exchange(initial_balance=100.0)
    callback_calls = []
    ex.on_fill(lambda event: callback_calls.append(event) or asyncio.sleep(0))

    await ex.set_leverage("BTC/USDT:USDT", 3)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    assert len(callback_calls) == 0
    fills = ex.drain_pending_fills()
    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"
    assert fills[0].pnl is None
    assert len(ex.drain_pending_fills()) == 0


async def test_market_close_fill_event_has_pnl():
    """Market close should produce FillEvent with pnl in pending queue."""
    ex = _make_exchange(initial_balance=100.0)
    await ex.set_leverage("BTC/USDT:USDT", 3)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    ex.drain_pending_fills()

    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    fills = ex.drain_pending_fills()
    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"
    assert fills[0].pnl is not None


async def test_force_liquidate_fill_event_has_pnl():
    """Liquidation FillEvent should include pnl."""
    ex = _make_exchange(initial_balance=100.0)
    fills = []
    ex.on_fill(lambda event: fills.append(event) or asyncio.sleep(0))

    await ex.set_leverage("BTC/USDT:USDT", 10)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    ex.drain_pending_fills()

    liq_ticker = Ticker("BTC/USDT:USDT", 80000.0, 80000.0, 80010.0,
                        96000.0, 79000.0, 1000.0, 1712534500000)
    await ex._process_tick(liq_ticker)

    assert len(fills) >= 1
    liq_fill = [f for f in fills if f.trigger_reason == "liquidation"]
    assert len(liq_fill) == 1
    assert liq_fill[0].pnl is not None


async def test_simulated_exchange_alert_service_integration():
    """SimulatedExchange 应在 _process_tick 中调用 PriceAlertService.check。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker
    from src.services.price_alert import AlertInfo
    from unittest.mock import MagicMock, AsyncMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
        precision={"BTC/USDT:USDT": 3},
    )
    exchange = SimulatedExchange(
        config=config, db_engine=None, session_id="test",
        symbol="BTC/USDT:USDT",
    )
    exchange._free_usdt = 1000.0
    exchange._running = True

    mock_alert = AlertInfo(
        symbol="BTC/USDT:USDT",
        current_price=57900.0,
        reference_price=60000.0,
        change_pct=-3.5,
        window_minutes=5,
        timestamp=1712534400000,
    )
    mock_service = MagicMock()
    mock_service.check.return_value = mock_alert
    exchange.set_alert_service(mock_service)

    alert_callback = AsyncMock()
    exchange.on_alert(alert_callback)

    ticker = Ticker(
        symbol="BTC/USDT:USDT", last=57900.0,
        bid=57899.0, ask=57901.0,
        high=60000.0, low=57800.0,
        base_volume=12345.0, timestamp=1712534400000,
    )
    await exchange._process_tick(ticker)

    mock_service.check.assert_called_once_with(57900.0, 1712534400000)
    alert_callback.assert_called_once()
    alert_info = alert_callback.call_args[0][0]
    assert alert_info.change_pct == -3.5


async def test_simulated_exchange_no_alert_when_service_returns_none():
    """PriceAlertService.check 返回 None 时不应调用 alert callback。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker
    from unittest.mock import MagicMock, AsyncMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
        precision={"BTC/USDT:USDT": 3},
    )
    exchange = SimulatedExchange(
        config=config, db_engine=None, session_id="test",
        symbol="BTC/USDT:USDT",
    )
    exchange._free_usdt = 1000.0
    exchange._running = True

    mock_service = MagicMock()
    mock_service.check.return_value = None
    exchange.set_alert_service(mock_service)

    alert_callback = AsyncMock()
    exchange.on_alert(alert_callback)

    ticker = Ticker(
        symbol="BTC/USDT:USDT", last=60000.0,
        bid=59999.0, ask=60001.0,
        high=60500.0, low=59500.0,
        base_volume=12345.0, timestamp=1712534400000,
    )
    await exchange._process_tick(ticker)

    mock_service.check.assert_called_once()
    alert_callback.assert_not_called()


async def test_simulated_exchange_update_alert_params():
    """update_alert_params 应委托给内部 PriceAlertService。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from unittest.mock import MagicMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
        precision={"BTC/USDT:USDT": 3},
    )
    exchange = SimulatedExchange(
        config=config, db_engine=None, session_id="test",
        symbol="BTC/USDT:USDT",
    )

    mock_service = MagicMock()
    exchange.set_alert_service(mock_service)
    exchange.update_alert_params(2.0, 10)
    mock_service.update_params.assert_called_once_with(2.0, 10)


async def test_simulated_exchange_alert_callback_outside_lock():
    """alert callback 应在锁外执行（与 fill callback 同模式）。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker
    from src.services.price_alert import AlertInfo
    from unittest.mock import MagicMock, AsyncMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
        precision={"BTC/USDT:USDT": 3},
    )
    exchange = SimulatedExchange(
        config=config, db_engine=None, session_id="test",
        symbol="BTC/USDT:USDT",
    )
    exchange._free_usdt = 1000.0
    exchange._running = True

    mock_alert = AlertInfo(
        symbol="BTC/USDT:USDT",
        current_price=57900.0,
        reference_price=60000.0,
        change_pct=-3.5,
        window_minutes=5,
        timestamp=1712534400000,
    )
    mock_service = MagicMock()
    mock_service.check.return_value = mock_alert
    exchange.set_alert_service(mock_service)

    lock_held_during_callback = False

    async def alert_callback(info):
        nonlocal lock_held_during_callback
        lock_held_during_callback = exchange._lock.locked()

    exchange.on_alert(alert_callback)

    ticker = Ticker(
        symbol="BTC/USDT:USDT", last=57900.0,
        bid=57899.0, ask=57901.0,
        high=60000.0, low=57800.0,
        base_volume=12345.0, timestamp=1712534400000,
    )
    await exchange._process_tick(ticker)

    assert lock_held_during_callback is False


async def test_sim_order_model_has_frozen_fields():
    """SimOrder model has frozen_margin and leverage columns."""
    from src.storage.models import SimOrder
    assert hasattr(SimOrder, "frozen_margin")
    assert hasattr(SimOrder, "leverage")


async def test_pending_order_has_frozen_fields():
    """_PendingOrder supports frozen_margin and leverage fields."""
    from src.integrations.exchange.simulated import _PendingOrder
    order = _PendingOrder(
        id="test", symbol="BTC/USDT:USDT", side="buy",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=100.0, leverage=3,
    )
    assert order.frozen_margin == 100.0
    assert order.leverage == 3


async def test_is_close_order_dynamic():
    """_is_close_order detects close vs open based on current position."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange()
    # No position → not a close
    assert ex._is_close_order("BTC/USDT:USDT", "sell") is False
    # Long position + sell → close
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95000.0, leverage=3,
    )
    assert ex._is_close_order("BTC/USDT:USDT", "sell") is True
    assert ex._is_close_order("BTC/USDT:USDT", "buy") is False
    # Short position + buy → close
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="short", contracts=0.001, entry_price=95000.0, leverage=3,
    )
    assert ex._is_close_order("BTC/USDT:USDT", "buy") is True
    assert ex._is_close_order("BTC/USDT:USDT", "sell") is False


async def test_is_close_order_static():
    """_is_close_order_static detects close direction from order fields only."""
    from src.integrations.exchange.simulated import SimulatedExchange, _PendingOrder
    # long position_side + sell → close
    o = _PendingOrder(id="1", symbol="X", side="sell", position_side="long",
                      order_type="market", amount=1, trigger_price=None)
    assert SimulatedExchange._is_close_order_static(o) is True
    # long position_side + buy → open (add-to)
    o2 = _PendingOrder(id="2", symbol="X", side="buy", position_side="long",
                       order_type="market", amount=1, trigger_price=None)
    assert SimulatedExchange._is_close_order_static(o2) is False
    # short position_side + buy → close
    o3 = _PendingOrder(id="3", symbol="X", side="buy", position_side="short",
                       order_type="market", amount=1, trigger_price=None)
    assert SimulatedExchange._is_close_order_static(o3) is True


async def test_market_order_returns_open_status():
    """create_order("market") now returns status="open", price=None, fee=None."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert order.status == "open"
    assert order.price is None
    assert order.fee is None


async def test_market_order_frozen_balance():
    """Market order freezes margin+fee from free_usdt."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    # Frozen should be (ask * amount / leverage + ask * amount * fee_rate) * 1.002
    ask = 95010.0
    margin = (ask * 0.001) / 3
    fee = ask * 0.001 * 0.0005
    frozen = (margin + fee) * 1.002
    assert ex._frozen_usdt == pytest.approx(frozen)
    assert ex._free_usdt == pytest.approx(100.0 - frozen)
    # No position yet
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0


async def test_market_order_fills_on_next_tick():
    """Market order fills on next _process_tick: position created, callback called."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert len(await ex.fetch_positions("BTC/USDT:USDT")) == 0

    # Tick with slightly different price
    tick = Ticker(symbol="BTC/USDT:USDT", last=95100.0, bid=95090.0, ask=95110.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].contracts == 0.001

    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"
    assert fills[0].fill_price == 95110.0  # ask price at tick time
    assert fills[0].pnl is None  # open order, no PnL
    assert ex._frozen_usdt == 0.0


async def test_market_close_fills_on_next_tick():
    """Close market order: position still exists after create_order, gone after tick."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=50.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    # Setup existing long position
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95010.0, leverage=3,
    )
    margin = 95010.0 * 0.001 / 3
    ex._used_usdt = margin
    ex._free_usdt = 50.0 - margin

    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert order.status == "open"
    # Position still exists
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1

    tick = Ticker(symbol="BTC/USDT:USDT", last=95100.0, bid=95090.0, ask=95110.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0
    assert len(fills) == 1
    assert fills[0].trigger_reason == "market"
    assert fills[0].pnl is not None


async def test_close_market_order_minimal_freeze():
    """Close order only freezes fee (not margin), allowing close even when free_usdt ≈ 0."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    margin = 95010.0 * 0.001 / 3
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95010.0, leverage=3,
    )
    ex._used_usdt = margin
    ex._free_usdt = 0.01  # Almost no free balance

    # Should NOT raise — close only freezes fee (min of estimated_fee, free_usdt)
    order = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert order.status == "open"
    assert ex._frozen_usdt == pytest.approx(0.01)  # min(fee, 0.01)


async def test_frozen_balance_diff_refund():
    """When tick price is lower than submit price, diff is refunded to free_usdt."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    free_after_freeze = ex._free_usdt

    # Tick with LOWER ask → actual cost < frozen → refund
    tick = Ticker(symbol="BTC/USDT:USDT", last=94900.0, bid=94890.0, ask=94900.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt > free_after_freeze  # got a refund


async def test_frozen_extreme_clamp():
    """Extreme price movement: free_usdt clamped to 0, shortfall absorbed as slippage cost.

    Math: initial=32, ask@submit=95010, leverage=3
      frozen = (95010*0.001/3 + 95010*0.001*0.0005) * 1.002 ≈ 31.78
      free_after_freeze = 32 - 31.78 = 0.22
    Tick ask=97000:
      actual_cost = 97000*0.001/3 + 97000*0.001*0.0005 ≈ 32.38
      diff = 31.78 - 32.38 = -0.60
      free = 0.22 + (-0.60) = -0.38 → clamped to 0 (shortfall = 0.38 lost as slippage)
    """
    ex = _make_exchange(initial_balance=32.0)  # tight: just barely covers frozen
    ex._leverage["BTC/USDT:USDT"] = 3
    total_before = 32.0
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert ex._free_usdt < 1.0  # confirm tight margin

    # Tick with MUCH HIGHER ask → actual cost > frozen → clamp
    tick = Ticker(symbol="BTC/USDT:USDT", last=97000.0, bid=96990.0, ask=97000.0,
                  high=97000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == 0.0  # clamped to zero
    # used_usdt = actual_margin only (no phantom residue in _used_usdt)
    actual_margin = (97000.0 * 0.001) / 3
    assert ex._used_usdt == pytest.approx(actual_margin)
    balance = await ex.fetch_balance()
    assert balance.total_usdt > total_before  # phantom value: total inflated, not shrunk


async def test_fill_market_close_position_gone():
    """If position was liquidated before close fill, close order is cancelled and margin unfrozen."""
    from src.integrations.exchange.simulated import _Position, _PendingOrder
    ex = _make_exchange(initial_balance=50.0)
    # Manually set up a pending close order but no position (simulating liquidation ate it)
    ex._pending_orders.append(_PendingOrder(
        id="close-1", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=0.05, leverage=3,
    ))
    ex._frozen_usdt = 0.05
    ex._free_usdt = 49.95

    tick = Ticker(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    # Close order should be cancelled, margin unfrozen
    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(50.0)
    assert len(ex._pending_orders) == 0


async def test_fill_market_close_clamps_amount():
    """Close fill amount is clamped to position contracts."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    margin = 95010.0 * 0.001 / 3
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95010.0, leverage=3,
    )
    ex._used_usdt = margin
    ex._free_usdt = 100.0 - margin

    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    # Submit close for MORE than position size
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.005)
    tick = Ticker(symbol="BTC/USDT:USDT", last=95100.0, bid=95090.0, ask=95110.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)

    assert len(fills) == 1
    assert fills[0].amount == 0.001  # clamped to actual position size


async def test_orphan_cleanup_preserves_market_open():
    """Stop loss closing position should NOT delete pending market/limit open orders."""
    from src.integrations.exchange.simulated import _PendingOrder
    ex = _make_exchange()
    # Pending market open order (no position needed — it creates one)
    ex._pending_orders.append(_PendingOrder(
        id="mkt-open", symbol="BTC/USDT:USDT", side="buy",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=32.0, leverage=3,
    ))
    # No position exists (was just closed by stop)
    ex._cancel_orphaned_orders()
    assert len(ex._pending_orders) == 1
    assert ex._pending_orders[0].id == "mkt-open"


async def test_orphan_cleanup_removes_market_close():
    """Liquidation should remove pending market close orders and unfreeze margin."""
    from src.integrations.exchange.simulated import _PendingOrder
    ex = _make_exchange(initial_balance=50.0)
    ex._frozen_usdt = 0.05
    ex._free_usdt = 49.95
    ex._pending_orders.append(_PendingOrder(
        id="mkt-close", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=0.05, leverage=3,
    ))
    # No position (liquidated)
    ex._cancel_orphaned_orders()
    assert len(ex._pending_orders) == 0
    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(50.0)


async def test_orphan_cleanup_unfreezes_margin():
    """Orphaned close order's frozen margin is correctly returned."""
    from src.integrations.exchange.simulated import _PendingOrder
    ex = _make_exchange(initial_balance=100.0)
    ex._frozen_usdt = 5.0
    ex._free_usdt = 95.0
    ex._pending_orders = [
        _PendingOrder(id="stop-1", symbol="BTC/USDT:USDT", side="sell",
                      position_side="long", order_type="stop",
                      amount=0.001, trigger_price=90000.0),
        _PendingOrder(id="mkt-close", symbol="BTC/USDT:USDT", side="sell",
                      position_side="long", order_type="market",
                      amount=0.001, trigger_price=None,
                      frozen_margin=5.0, leverage=3),
    ]
    # No position → both should be cleaned up, market close unfreezes
    ex._cancel_orphaned_orders()
    assert len(ex._pending_orders) == 0
    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(100.0)


async def test_fetch_balance_total_includes_frozen():
    """total_usdt = free + used + frozen + unrealized."""
    ex = _make_exchange(initial_balance=60.0)
    ex._used_usdt = 30.0
    ex._frozen_usdt = 10.0
    balance = await ex.fetch_balance()
    assert balance.total_usdt == pytest.approx(100.0)
    assert balance.free_usdt == pytest.approx(60.0)
    assert balance.used_usdt == 30.0
