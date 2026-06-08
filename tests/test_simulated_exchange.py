import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.exchange.base import Ticker
from src.integrations.exchange.base import FillEvent
from tests._fixtures import _advance


def _make_exchange(initial_balance=100.0, fee_rate=0.0005, symbol="BTC/USDT:USDT"):
    """Helper: create a SimulatedExchange without async start()."""
    from src.integrations.exchange.simulated import SimulatedExchange

    config = MagicMock()
    config.fee_rate = fee_rate

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
    exchange._latest_mark_price = exchange._latest_ticker.last   # default mark = last seed
    exchange._running = True
    from tests._fixtures import inject_mock_ccxt
    inject_mock_ccxt(exchange)
    return exchange


def _tick(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0):
    """Helper: create a Ticker for _process_tick calls in tests."""
    return Ticker(
        symbol=symbol, last=last, bid=bid, ask=ask,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000,
    )


# ---------------------------------------------------------------------------
# Sync market fill (iter-sync-market-fill Task 1): market create_order settles
# synchronously and returns a FillEvent (no pending queue / no _process_tick).
# ---------------------------------------------------------------------------


async def test_market_buy_opens_long_sync():
    """市价买单同步成交：create_order 直接返回 FillEvent，仓位即刻存在，无 pending/frozen。"""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert isinstance(fill, FillEvent)
    assert fill.fill_price == 95010.0          # ask（buy 吃 ask）
    assert fill.pnl is None                      # 开仓
    assert fill.is_full_close is False
    assert fill.trigger_reason == "market"
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1 and positions[0].side == "long"
    assert positions[0].contracts == 0.001
    balance = await ex.fetch_balance()
    margin = 95010.0 * 0.001 / 3
    assert balance.used_usdt == pytest.approx(margin)
    assert ex._pending_orders == []              # 不进 pending 队列
    assert ex._frozen_usdt == 0.0                # 无冻结


async def test_market_sell_opens_short_sync():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 2
    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert isinstance(fill, FillEvent)
    assert fill.fill_price == 94990.0          # bid（sell 吃 bid）
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert positions[0].side == "short"


async def test_market_close_sync_returns_realized_pnl():
    """市价平仓同步：返回 FillEvent 带 pnl + entry_price + is_full_close。"""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)   # 开 long @ ask 95010
    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)  # 平 @ bid 94990
    assert isinstance(fill, FillEvent)
    assert fill.fill_price == 94990.0
    assert fill.pnl is not None
    assert fill.entry_price == 95010.0          # 平仓前 weighted entry
    assert fill.is_full_close is True
    assert await ex.fetch_positions("BTC/USDT:USDT") == []
    assert ex._pending_orders == []


async def test_market_open_insufficient_balance_rejects():
    """余额不足 → explicit reject，状态不变。"""
    ex = _make_exchange(initial_balance=1.0)
    ex._leverage["BTC/USDT:USDT"] = 1
    with pytest.raises(ValueError, match="Insufficient balance"):
        await ex.create_order("BTC/USDT:USDT", "buy", "market", 1.0)
    assert await ex.fetch_positions("BTC/USDT:USDT") == []
    assert ex._free_usdt == 1.0


async def test_fill_market_open_reverse_conflict_raises():
    """防御性 guard：对冲突仓位直接调 _fill_market_open → explicit reject（不 silent None）。"""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="short", contracts=0.001, entry_price=95000.0, leverage=3)
    with pytest.raises(ValueError, match="existing short position"):
        ex._fill_market_open("oid", "BTC/USDT:USDT", "buy", 0.001, 3, ex._latest_ticker)


async def test_sync_full_close_cancels_orphans_and_clears_alerts():
    """G1：同步全平 → 撤孤儿 SL/TP（_pending_orders）+ 清 price-level 告警，两套机制都生效。"""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)            # 开 long
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)  # 挂 stop
    ex.add_price_level_alert(99000.0, "above", "BTC/USDT:USDT", "resistance")
    assert any(o.order_type == "stop" for o in ex._pending_orders)
    assert len(ex._price_level_alerts) == 1

    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)    # 全平
    assert fill.is_full_close is True
    assert not any(o.order_type == "stop" for o in ex._pending_orders)         # 孤儿单已撤
    assert ex._price_level_alerts == []                                        # 告警已清


async def test_sync_flip_does_not_mistrigger_old_orphan():
    """G1+flip 回归：平 long → 同步反向开 short 后，旧 long-stop 不得残留误平新仓。"""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)            # 开 long
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)  # long-stop
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)           # 全平（撤孤儿）
    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)    # 同 flip：反向开 short
    assert fill.position_side == "short"
    # 旧 long-stop 不应残留
    assert not any(o.order_type == "stop" and o.position_side == "long"
                   for o in ex._pending_orders)


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
    assert balance.total_usdt == pytest.approx(101.0)   # mark 95000 vs entry 94000: uPnL=1.0
    assert balance.free_usdt == pytest.approx(71.0)     # free 70 + uPnL 1.0
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
    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert isinstance(fill, FillEvent)
    assert fill.fill_price == 95010.0  # ask (sync fill at seed ticker)

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
    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert isinstance(fill, FillEvent)

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
        await ex.create_order("BTC/USDT:USDT", "buy", "foobar", 0.001)


async def test_market_close_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)  # sync open

    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)  # sync close
    assert isinstance(fill, FillEvent)
    assert fill.is_full_close is True

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0
    balance = await ex.fetch_balance()
    assert balance.used_usdt == 0.0


async def test_market_close_clamps_amount():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)  # sync open

    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.999)  # sync close — amount clamped

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0  # fully closed despite excess amount


async def test_add_to_position():
    ex = _make_exchange(initial_balance=200.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)  # fill @ ask 95010

    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=96000.0, bid=95990.0, ask=96010.0,
        high=97000.0, low=94000.0, base_volume=1000.0, timestamp=1712534500000,
    )
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)  # fill @ ask 96010

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert positions[0].contracts == 0.002
    expected_entry = (95010.0 * 0.001 + 96010.0 * 0.001) / 0.002
    assert positions[0].entry_price == pytest.approx(expected_entry)


async def test_add_position_leverage_mismatch():
    ex = _make_exchange(initial_balance=200.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)  # creates position at 3x

    ex._leverage["BTC/USDT:USDT"] = 5  # change setting
    # Sync: leverage mismatch is now an explicit reject (no silent cancel),
    # state unchanged.
    with pytest.raises(ValueError, match="Leverage mismatch"):
        await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert positions[0].contracts == 0.001  # unchanged — second order rejected
    assert ex._frozen_usdt == 0.0


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
    await ex._process_tick(_tick())  # fill → creates position
    with pytest.raises(ValueError, match="Cannot change leverage"):
        await ex.set_leverage("BTC/USDT:USDT", 5)


def test_amount_to_precision_truncates_via_ccxt():
    ex = _make_exchange()
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.001567) == 0.001
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.0019999) == 0.001   # truncate not round


async def test_stop_order_creation():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex._process_tick(_tick())  # fill → position exists
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
    await ex._process_tick(_tick())  # fill → position exists
    with pytest.raises(ValueError, match="price is required"):
        await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001)


async def test_conditional_order_forces_full_amount():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex._process_tick(_tick())  # fill → position exists
    order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.0005, price=93000.0)
    assert order.amount == 0.001  # forced to position.contracts


async def test_should_trigger_stop_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex._process_tick(_tick())  # fill open
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
    assert fill_events[0].fill_price == 92790.0
    assert fill_events[0].position_side == "long"
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0
    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    assert len(open_orders) == 0


async def test_should_trigger_take_profit_long():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex._process_tick(_tick())  # fill open
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
    await ex._process_tick(_tick())  # fill open
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)

    fill_events = []
    async def on_fill(event: FillEvent):
        fill_events.append(event)
    ex.on_fill(on_fill)

    tick = Ticker(
        symbol="BTC/USDT:USDT", last=80000.0, bid=79990.0, ask=80010.0,
        high=96000.0, low=79000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await _advance(ex, tick, mark=79990.0)  # mark = bid (crash side for long liq)
    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "liquidation"
    balance = await ex.fetch_balance()
    assert balance.free_usdt >= 0.0


async def test_should_trigger_stop_short():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    await ex._process_tick(_tick())  # fill open
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
    assert fill_events[0].fill_price == 97210.0
    assert fill_events[0].position_side == "short"


async def test_should_trigger_take_profit_short():
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    await ex._process_tick(_tick())  # fill open
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
    await ex._process_tick(_tick())  # fill open
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

    ex1 = SimulatedExchange(config, engine, "test-s", "BTC/USDT:USDT")
    await ex1._init_state(initial_balance=100.0)
    ex1._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534400000,
    )
    ex1._latest_mark_price = 95010.0  # seed mark (direct construction bypasses start()'s seed)
    ex1._leverage["BTC/USDT:USDT"] = 3
    await ex1.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex1._process_tick(Ticker(
        symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000,
    ))  # fill the market order
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
    """Market orders should be queryable via fetch_closed_orders after fill."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session
    from src.integrations.exchange.simulated import SimulatedExchange

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as sess:
        sess.add(Session(id="test-s2", name="test2", initial_balance=100.0))
        await sess.commit()

    config = MagicMock()
    config.fee_rate = 0.0005

    ex = SimulatedExchange(config, engine, "test-s2", "BTC/USDT:USDT")
    await ex._init_state(initial_balance=100.0)
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
        high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534400000,
    )
    ex._latest_mark_price = 95010.0  # seed mark (direct construction bypasses start()'s seed)
    ex._leverage["BTC/USDT:USDT"] = 3

    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    # Sync: market order is settled + persisted (status="closed") inside create_order.

    closed = await ex.fetch_closed_orders("BTC/USDT:USDT")
    assert len(closed) == 1
    assert closed[0].id == fill.order_id
    assert closed[0].status == "closed"
    assert closed[0].fee is not None

    fetched = await ex.fetch_order(fill.order_id)
    assert fetched.id == fill.order_id

    await engine.dispose()


async def test_partial_close_position():
    """Partial close should release proportional margin and keep remaining position."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.002)  # sync open

    # Close half
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)  # sync partial close

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
    await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)  # sync open @ bid 94990

    fill_events = []
    async def on_fill(event):
        fill_events.append(event)
    ex.on_fill(on_fill)

    # Tick: price surges above liquidation price → liquidation fill
    tick2 = Ticker(
        symbol="BTC/USDT:USDT", last=120000.0, bid=119990.0, ask=120010.0,
        high=121000.0, low=94000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await _advance(ex, tick2, mark=120010.0)  # mark = ask (crash side for short liq)

    assert len(fill_events) == 1
    assert fill_events[0].trigger_reason == "liquidation"
    assert fill_events[0].position_side == "short"
    balance = await ex.fetch_balance()
    assert balance.free_usdt >= 0.0


async def test_cancel_order():
    ex = _make_exchange(initial_balance=100.0)
    await ex.set_leverage("BTC/USDT:USDT", 3)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex._process_tick(_tick())  # fill → position exists
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
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex._process_tick(_tick())  # fill open
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)

    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    drop_ticker = Ticker("BTC/USDT:USDT", 89000.0, 89000.0, 89010.0,
                         96000.0, 88000.0, 1000.0, 1712534500000)
    await ex._process_tick(drop_ticker)

    assert len(fills) == 1
    assert fills[0].trigger_reason == "stop"
    assert fills[0].pnl is not None
    assert fills[0].pnl < 0


async def test_market_order_fill_callback():
    """Sync: market open returns FillEvent directly; no fill callback / no conditional trigger."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)
    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert isinstance(fill, FillEvent)
    assert fill.trigger_reason == "market"
    assert fill.side == "buy"
    assert len(fills) == 0  # sync path does not dispatch fill callback


async def test_market_close_fill_event_has_pnl():
    """Sync: market close returns FillEvent with pnl directly."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)  # sync open

    close_fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)  # sync close
    assert close_fill.trigger_reason == "market"
    assert close_fill.pnl is not None
    assert len(fills) == 0  # sync path does not dispatch fill callback


async def test_force_liquidate_fill_event_has_pnl():
    """Liquidation FillEvent should include pnl."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 10
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)  # sync open (no callback)
    assert len(fills) == 0

    # Tick: price crashes below liquidation
    liq_ticker = Ticker("BTC/USDT:USDT", 80000.0, 80000.0, 80010.0,
                        96000.0, 79000.0, 1000.0, 1712534500000)
    await _advance(ex, liq_ticker, mark=80000.0)  # mark = bid (crash side for long liq)

    liq_fills = [f for f in fills if f.trigger_reason == "liquidation"]
    assert len(liq_fills) == 1
    assert liq_fills[0].pnl is not None


async def test_simulated_exchange_alert_service_integration():
    """SimulatedExchange 应在 _process_tick 中调用 PriceAlertService.check。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker
    from src.services.price_alert import AlertInfo
    from unittest.mock import MagicMock, AsyncMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
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
    exchange._alert_service = mock_service

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
    )
    exchange = SimulatedExchange(
        config=config, db_engine=None, session_id="test",
        symbol="BTC/USDT:USDT",
    )
    exchange._free_usdt = 1000.0
    exchange._running = True

    mock_service = MagicMock()
    mock_service.check.return_value = None
    exchange._alert_service = mock_service

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


async def test_simulated_exchange_alert_callback_outside_lock():
    """alert callback 应在锁外执行（与 fill callback 同模式）。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.base import Ticker
    from src.services.price_alert import AlertInfo
    from unittest.mock import MagicMock, AsyncMock
    from src.config import ExchangeConfig

    config = ExchangeConfig(
        name="simulated", fee_rate=0.0005,
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
    exchange._alert_service = mock_service

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


async def test_pending_limit_order_fields():
    """Limit order creates _PendingOrder with correct frozen_margin/leverage/trigger_price."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)

    assert len(ex._pending_orders) == 1
    po = ex._pending_orders[0]
    assert po.order_type == "limit"
    assert po.frozen_margin > 0
    assert po.leverage == 3
    assert po.trigger_price == 90000.0


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


async def test_market_order_returns_fill_event():
    """Sync: create_order("market") returns a FillEvent (not an open Order)."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert isinstance(fill, FillEvent)
    assert fill.fill_price == 95010.0  # ask
    assert fill.fee is not None
    assert fill.pnl is None


async def test_market_order_direct_occupation():
    """Sync: market open directly occupies margin+fee (no freeze, no buffer)."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    ask = 95010.0
    margin = (ask * 0.001) / 3
    fee = ask * 0.001 * 0.0005
    assert ex._used_usdt == pytest.approx(margin)
    assert ex._free_usdt == pytest.approx(100.0 - margin - fee)
    assert ex._frozen_usdt == 0.0  # sync: no freeze
    # Position exists immediately
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1


async def test_market_open_creates_position_immediately():
    """Sync: position created in create_order, FillEvent at seed ticker ask."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3

    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].side == "long"
    assert positions[0].contracts == 0.001

    assert fill.trigger_reason == "market"
    assert fill.fill_price == 95010.0  # ask price at seed ticker
    assert fill.pnl is None  # open order, no PnL
    assert ex._frozen_usdt == 0.0


async def test_market_close_pops_position_immediately():
    """Sync close: position exists, then create_order(close) pops it immediately."""
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

    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert isinstance(fill, FillEvent)
    assert fill.trigger_reason == "market"
    assert fill.pnl is not None

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0


async def test_market_close_allowed_with_low_free_balance():
    """Sync close: does not require new margin, so it succeeds even when free_usdt ≈ 0."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    margin = 95010.0 * 0.001 / 3
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95010.0, leverage=3,
    )
    ex._used_usdt = margin
    ex._free_usdt = 0.01  # Almost no free balance

    # Should NOT raise — close releases margin, doesn't occupy new.
    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.001)
    assert fill.is_full_close is True
    assert await ex.fetch_positions("BTC/USDT:USDT") == []


async def test_fill_market_close_amount_clamped():
    """Sync close fill amount is clamped to position contracts."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    margin = 95010.0 * 0.001 / 3
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95010.0, leverage=3,
    )
    ex._used_usdt = margin
    ex._free_usdt = 100.0 - margin

    # Submit close for MORE than position size
    fill = await ex.create_order("BTC/USDT:USDT", "sell", "market", 0.005)
    assert fill.amount == 0.001  # clamped to actual position size


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


async def test_e2e_open_then_stop_same_cycle():
    """Core value scenario (sync): open_position fills immediately → position exists →
    set stop succeeds in the SAME cycle (no fill-notification round-trip needed)."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    # Agent cycle 1: open position — sync fill, position exists immediately
    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert isinstance(fill, FillEvent)
    assert fill.trigger_reason == "market"
    assert len(fills) == 0  # sync path does not dispatch a fill callback

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1

    # Same cycle: set stop now succeeds (position already exists)
    stop_order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)
    assert stop_order.status == "open"
    assert stop_order.order_type == "stop"


async def test_e2e_open_then_immediate_liquidation():
    """Sync market buy opens immediately (entry @ seed ask), then the next tick
    liquidates it. Open is sync (no callback); only the liquidation dispatches."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 10  # high leverage → tight liquidation price
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    # Sync open: entry @ seed ask 95010. With 10x leverage, liq ≈ entry * 0.9 ≈ 85509.
    fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert fill.trigger_reason == "market"
    assert len(fills) == 0  # sync open does not dispatch a callback

    # Tick where mark crashes below liquidation price.
    tick = Ticker(symbol="BTC/USDT:USDT", last=80000.0, bid=80000.0, ask=80010.0,
                  high=96000.0, low=79000.0, base_volume=1000.0, timestamp=1712534401000)
    await _advance(ex, tick, mark=80000.0)  # mark below liq

    # Only the liquidation dispatches a callback.
    assert len(fills) == 1
    assert fills[0].trigger_reason == "liquidation"
    # Position should be gone
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 0
    balance = await ex.fetch_balance()
    assert balance.free_usdt >= 0.0


async def test_has_pending_market_order():
    """has_pending_market_order returns True when pending market order exists."""
    from src.integrations.exchange.simulated import _PendingOrder
    ex = _make_exchange()
    assert ex.has_pending_market_order("BTC/USDT:USDT") is False

    ex._pending_orders.append(_PendingOrder(
        id="m1", symbol="BTC/USDT:USDT", side="buy",
        position_side="long", order_type="market",
        amount=0.001, trigger_price=None,
        frozen_margin=32.0, leverage=3,
    ))
    assert ex.has_pending_market_order("BTC/USDT:USDT") is True
    assert ex.has_pending_market_order("BTC/USDT:USDT", side="buy") is True
    assert ex.has_pending_market_order("BTC/USDT:USDT", side="sell") is False

    # Stop orders don't count
    ex._pending_orders = [_PendingOrder(
        id="s1", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", order_type="stop",
        amount=0.001, trigger_price=90000.0,
    )]
    assert ex.has_pending_market_order("BTC/USDT:USDT") is False


async def test_limit_order_creation():
    """create_order("limit") returns status="open", freezes margin."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)
    assert order.status == "open"
    assert order.order_type == "limit"
    assert order.price == 90000.0
    # Check frozen: (90000 * 0.001 / 3) + (90000 * 0.001 * 0.0005) = 30 + 0.045 = 30.045
    expected_frozen = (90000.0 * 0.001 / 3) + (90000.0 * 0.001 * 0.0005)
    assert ex._frozen_usdt == pytest.approx(expected_frozen)
    assert ex._free_usdt == pytest.approx(100.0 - expected_frozen)


async def test_limit_order_requires_price():
    """Limit order without price raises ValueError."""
    ex = _make_exchange()
    with pytest.raises(ValueError, match="price is required"):
        await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001)


async def test_limit_order_reverse_position_rejected():
    """Limit sell rejected when long position exists (one-way mode)."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange()
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95000.0, leverage=3,
    )
    with pytest.raises(ValueError, match="Cannot open short limit order"):
        await ex.create_order("BTC/USDT:USDT", "sell", "limit", 0.001, price=100000.0)


async def test_limit_order_leverage_matches_position():
    """Limit order uses position leverage when position exists."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 10  # different from position
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=95000.0, leverage=3,
    )
    ex._used_usdt = 95000.0 * 0.001 / 3
    ex._free_usdt = 100.0 - ex._used_usdt
    order = await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)
    # Should use position leverage (3), not _leverage setting (10)
    # frozen = (90000 * 0.001 / 3) + (90000 * 0.001 * 0.0005)
    expected_frozen = (90000.0 * 0.001 / 3) + (90000.0 * 0.001 * 0.0005)
    assert ex._frozen_usdt == pytest.approx(expected_frozen)


async def test_limit_order_fills_when_price_reached():
    """Buy limit triggers when ask <= limit price."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=94000.0)

    # Tick with ask above limit → no fill
    tick1 = Ticker(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
                   high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick1)
    assert len(fills) == 0

    # Tick with ask at limit → fill
    tick2 = Ticker(symbol="BTC/USDT:USDT", last=93900.0, bid=93890.0, ask=93900.0,
                   high=96000.0, low=93000.0, base_volume=1000.0, timestamp=1712534402000)
    await ex._process_tick(tick2)
    assert len(fills) == 1
    assert fills[0].trigger_reason == "limit"
    assert fills[0].fill_price == 94000.0  # fills at limit price, not market
    assert fills[0].pnl is None  # open order

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    assert positions[0].entry_price == 94000.0
    assert ex._frozen_usdt == 0.0


async def test_limit_order_not_filled_above_price():
    """Buy limit does NOT trigger when ask > limit price."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)

    tick = Ticker(symbol="BTC/USDT:USDT", last=95000.0, bid=94990.0, ask=95010.0,
                  high=96000.0, low=94000.0, base_volume=1000.0, timestamp=1712534401000)
    await ex._process_tick(tick)
    assert len(ex._pending_orders) == 1  # still pending
    assert ex._frozen_usdt > 0


async def test_limit_fill_cancelled_on_reverse_position():
    """Limit buy cancelled at fill time if short position now exists."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=94000.0)
    frozen = ex._frozen_usdt

    # Manually create a short position
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="short", contracts=0.001, entry_price=96000.0, leverage=3,
    )

    tick = Ticker(symbol="BTC/USDT:USDT", last=93900.0, bid=93890.0, ask=93900.0,
                  high=96000.0, low=93000.0, base_volume=1000.0, timestamp=1712534402000)
    await ex._process_tick(tick)

    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(100.0)


async def test_limit_fill_cancelled_on_leverage_mismatch():
    """Limit order cancelled at fill time if position leverage doesn't match."""
    from src.integrations.exchange.simulated import _Position
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=94000.0)

    # Create position with DIFFERENT leverage
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=0.001, entry_price=96000.0, leverage=5,
    )

    tick = Ticker(symbol="BTC/USDT:USDT", last=93900.0, bid=93890.0, ask=93900.0,
                  high=96000.0, low=93000.0, base_volume=1000.0, timestamp=1712534402000)
    await ex._process_tick(tick)

    assert ex._frozen_usdt == 0.0
    assert len(ex._pending_orders) == 0


async def test_limit_order_cancel_unfreezes():
    """Cancelling a limit order returns frozen margin to free_usdt."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    order = await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)
    frozen = ex._frozen_usdt
    assert frozen > 0

    await ex.cancel_order(order.id, "BTC/USDT:USDT")
    assert ex._frozen_usdt == 0.0
    assert ex._free_usdt == pytest.approx(100.0)
    assert len(ex._pending_orders) == 0


# ---------------------------------------------------------------------------
# R2-7 §4.7 Task 1: Simulated transparent passthrough of SimOrder.trigger_price
# ---------------------------------------------------------------------------


async def test_simulated_fetch_open_orders_propagates_trigger_price():
    """T-ORD-6 (R2-7 §4.7): SimulatedExchange.fetch_open_orders 返回的 stop/TP Order
    含 trigger_price (透传 _PendingOrder.trigger_price)；plain limit/market: None。
    """
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    # Open a position so conditional orders can attach
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex._process_tick(_tick())  # fill market open

    # Place a stop and a take_profit
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=93000.0)
    await ex.create_order("BTC/USDT:USDT", "sell", "take_profit", 0.001, price=97000.0)
    # Place a plain limit (open-direction → opposite side; use a fresh ex to avoid conflict)
    # Stay on this exchange: the limit must match position side, but we already long.
    # Skip limit here; T-ORD-6 focuses on stop/TP propagation. Validate plain limit
    # via a separate simple fixture below.

    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    sl_orders = [o for o in open_orders if o.order_type == "stop"]
    tp_orders = [o for o in open_orders if o.order_type == "take_profit"]
    assert len(sl_orders) == 1
    assert len(tp_orders) == 1
    assert sl_orders[0].trigger_price == pytest.approx(93000.0)
    assert tp_orders[0].trigger_price == pytest.approx(97000.0)


async def test_simulated_fetch_open_orders_limit_has_no_trigger_price():
    """T-ORD-6 (cont.): plain limit Order.trigger_price 应为 None (非 stop/TP 类)."""
    ex = _make_exchange(initial_balance=10000.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    # No position → limit buy is open-direction long, allowed
    await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.001, price=90000.0)
    open_orders = await ex.fetch_open_orders("BTC/USDT:USDT")
    limit_orders = [o for o in open_orders if o.order_type == "limit"]
    assert len(limit_orders) == 1
    assert limit_orders[0].trigger_price is None


def test_fill_event_has_optional_entry_price_field():
    """FillEvent.entry_price defaults to None and accepts float."""
    from src.integrations.exchange.base import FillEvent

    ev = FillEvent(
        order_id="1", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", trigger_reason="market",
        fill_price=80000.0, amount=1.0, fee=40.0, pnl=100.0,
        timestamp=1, is_full_close=True,
    )
    assert ev.entry_price is None  # default

    ev2 = FillEvent(
        order_id="2", symbol="BTC/USDT:USDT", side="sell",
        position_side="long", trigger_reason="market",
        fill_price=80000.0, amount=1.0, fee=40.0, pnl=100.0,
        timestamp=1, is_full_close=True, entry_price=79900.0,
    )
    assert ev2.entry_price == 79900.0


def test_simulated_exchange_register_close_order_entry_is_noop():
    """SimulatedExchange inherits BaseExchange.register_close_order_entry no-op (no error)."""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.config import ExchangeConfig

    cfg = ExchangeConfig(name="simulated", fee_rate=0.0005)
    ex = SimulatedExchange(config=cfg, db_engine=None, session_id="t", symbol="BTC/USDT:USDT")
    # 不抛错，不返回值
    result = ex.register_close_order_entry("order123", 80000.0)
    assert result is None


def test_init_raises_when_fee_rate_is_none():
    """SimulatedExchange constructor raises on None fee_rate (silent fallback removed)."""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.config import ExchangeConfig
    import pytest

    cfg = ExchangeConfig(name="simulated", fee_rate=None)
    with pytest.raises(ValueError, match="fee_rate"):
        SimulatedExchange(
            config=cfg, db_engine=None,
            session_id="t", symbol="BTC/USDT:USDT",
        )


async def test_fill_market_close_includes_entry_price_in_event():
    """sim market close fill event carries position weighted-avg entry."""
    ex = _make_exchange(initial_balance=10000.0, fee_rate=0.0005)
    ex._leverage["BTC/USDT:USDT"] = 10

    # Set ticker to 80000 for open fill
    ex._latest_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=80000.0, bid=79990.0, ask=80010.0,
        high=81000.0, low=79000.0, base_volume=1000.0, timestamp=1712534400000,
    )

    # Open long @ ~80010 (ask) — sync fill at seed ticker
    await ex.create_order("BTC/USDT:USDT", "buy", "market", amount=0.1)

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert len(positions) == 1
    entry_before_close = positions[0].entry_price  # should be 80010.0

    # Move ticker before closing so close fills at the new bid
    ex._latest_ticker = _tick(last=80200.0, bid=80190.0, ask=80210.0)

    # Close long — sync fill returns the close FillEvent directly
    close_fill = await ex.create_order(
        "BTC/USDT:USDT", "sell", "market", amount=0.1,
        params={"reduceOnly": True},
    )
    assert close_fill.pnl is not None
    assert close_fill.entry_price == entry_before_close  # captured before _close_position_core


@pytest.mark.asyncio
async def test_execute_fill_includes_entry_price_for_stop_trigger():
    """sim SL trigger fill event carries position weighted-avg entry."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    # Open long at ~95010 (ask price from _tick default)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex._process_tick(_tick())  # fill open; entry_price = 95010.0

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    expected_entry = positions[0].entry_price  # 95010.0

    # Place SL below current price
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 0.001, price=90000.0)

    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    # Tick that triggers the stop (bid crosses below 90000)
    drop_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=89500.0, bid=89490.0, ask=89510.0,
        high=96000.0, low=88000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(drop_ticker)

    assert len(fills) == 1
    assert fills[0].trigger_reason == "stop"
    assert fills[0].entry_price == expected_entry


@pytest.mark.asyncio
async def test_execute_fill_includes_entry_price_for_take_profit_trigger():
    """sim TP trigger fill event carries position weighted-avg entry."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 3
    # Open long at ~95010 (ask price from _tick default)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    await ex._process_tick(_tick())  # fill open; entry_price = 95010.0

    positions = await ex.fetch_positions("BTC/USDT:USDT")
    expected_entry = positions[0].entry_price  # 95010.0

    # Place TP above current price
    await ex.create_order("BTC/USDT:USDT", "sell", "take_profit", 0.001, price=97000.0)

    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    # Tick that triggers the take profit (bid crosses above 97000)
    up_ticker = Ticker(
        symbol="BTC/USDT:USDT", last=97500.0, bid=97490.0, ask=97510.0,
        high=98000.0, low=94000.0, base_volume=1000.0, timestamp=1712535000000,
    )
    await ex._process_tick(up_ticker)

    assert len(fills) == 1
    assert fills[0].trigger_reason == "take_profit"
    assert fills[0].entry_price == expected_entry


@pytest.mark.asyncio
async def test_force_liquidate_includes_entry_price():
    """sim liquidation fill event carries position entry."""
    ex = _make_exchange(initial_balance=100.0)
    ex._leverage["BTC/USDT:USDT"] = 10

    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    open_fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)  # sync open
    assert len(fills) == 0
    original_entry = open_fill.fill_price  # entry_price == fill_price for market open (95010)

    # Tick: price crashes below liquidation price
    liq_ticker = Ticker("BTC/USDT:USDT", 80000.0, 79990.0, 80010.0,
                        96000.0, 79000.0, 1000.0, 1712534500000)
    await _advance(ex, liq_ticker, mark=79990.0)  # mark = bid (crash side for long liq)

    liq_fills = [f for f in fills if f.trigger_reason == "liquidation"]
    assert len(liq_fills) == 1
    assert liq_fills[0].entry_price == pytest.approx(original_entry)


@pytest.mark.asyncio
async def test_fill_event_entry_price_captured_before_pnl_cap():
    """drift guard: entry_price reflects original entry even when pnl_cap fires.

    Construct: leverage 100x position, market drops below liq; _close_position_core
    pnl_cap clamps pnl to -margin. entry_price MUST still equal original entry
    (not back-derived from clamped pnl).
    """
    ex = _make_exchange(initial_balance=10000.0)
    ex._leverage["BTC/USDT:USDT"] = 100

    fills = []
    async def on_fill(event):
        fills.append(event)
    ex.on_fill(on_fill)

    # Place and fill a long at ~95010 (ask from seed ticker) — sync open
    open_fill = await ex.create_order("BTC/USDT:USDT", "buy", "market", 0.001)
    assert len(fills) == 0
    expected_entry = open_fill.fill_price  # e.g. 95010.0

    # Tick 2: deep crash — well below liquidation; pnl_cap will clamp pnl to -margin
    deep_crash_ticker = Ticker(
        "BTC/USDT:USDT", 50000.0, 49990.0, 50010.0,
        96000.0, 49000.0, 1000.0, 1712535000000,
    )
    await _advance(ex, deep_crash_ticker, mark=49990.0)  # mark = bid (crash side for long liq)

    liq_fills = [f for f in fills if f.trigger_reason == "liquidation"]
    assert len(liq_fills) == 1
    liq_fill = liq_fills[0]

    # pnl is clamped (won't exceed -margin); entry_price must still be the original
    assert liq_fill.pnl is not None
    assert liq_fill.entry_price == pytest.approx(expected_entry)


def test_sim_close_paths_capture_entry_before_close_position_core():
    """AST drift guard: all 3 sim close paths must read pos.entry_price BEFORE
    calling self._close_position_core (which may pop the position from
    self._positions, see _close_position_core implementation).

    Mirror of test_fill_event_entry_price_captured_before_pnl_cap but covers
    all 3 capture sites (_fill_market_close / _execute_fill / _force_liquidate)
    via AST scan rather than only _force_liquidate via integration test.
    """
    import ast
    import pathlib

    sim_py = pathlib.Path(__file__).resolve().parents[1] / "src" / "integrations" / "exchange" / "simulated.py"
    tree = ast.parse(sim_py.read_text())

    target_methods = {"_fill_market_close", "_execute_fill", "_force_liquidate"}
    found = {name: False for name in target_methods}

    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in target_methods):
            continue
        # Scan the function body for the relative ordering of:
        # (a) any assignment whose value reads ".entry_price" attribute
        # (b) any call to self._close_position_core
        capture_line = None
        close_call_line = None
        for stmt in ast.walk(node):
            if (isinstance(stmt, ast.Attribute) and stmt.attr == "entry_price"
                    and capture_line is None):
                # Only count entry_price reads on .entry_price (e.g. pos.entry_price)
                capture_line = stmt.lineno
            if (isinstance(stmt, ast.Call) and isinstance(stmt.func, ast.Attribute)
                    and stmt.func.attr == "_close_position_core"
                    and close_call_line is None):
                close_call_line = stmt.lineno
        assert capture_line is not None, (
            f"{node.name}: no .entry_price capture found"
        )
        assert close_call_line is not None, (
            f"{node.name}: no _close_position_core call found"
        )
        assert capture_line < close_call_line, (
            f"{node.name}: .entry_price captured at line {capture_line} but "
            f"_close_position_core called at line {close_call_line} — capture "
            f"must precede close (position may be popped from self._positions)"
        )
        found[node.name] = True

    missing = [name for name, ok in found.items() if not ok]
    assert not missing, f"sim close paths not scanned: {missing}"
