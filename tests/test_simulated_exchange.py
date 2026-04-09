import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.exchange.base import Ticker
from src.integrations.exchange.simulated import FillEvent


def _make_exchange(initial_balance=100.0, fee_rate=0.0005, symbol="BTC/USDT:USDT"):
    """Helper: create a SimulatedExchange without async start()."""
    from src.integrations.exchange.simulated import SimulatedExchange

    config = MagicMock()
    config.fee_rate = fee_rate
    config.precision = {"BTC/USDT:USDT": 3, "ETH/USDT:USDT": 2}

    exchange = SimulatedExchange(config=config, db_engine=None, session_id="test-session", symbol=symbol)
    exchange._free_usdt = initial_balance
    exchange._used_usdt = 0.0
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
