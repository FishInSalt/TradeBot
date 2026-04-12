import pytest
import sqlalchemy


@pytest.fixture
async def db_session(tmp_path):
    from src.storage.database import init_db, get_session
    from src.storage.models import Session
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with get_session(engine) as session:
        # Create a session record for FK references
        s = Session(id="test-session", name="test")
        session.add(s)
        await session.commit()
        yield session


async def test_create_trade_action(db_session):
    from src.storage.models import TradeAction
    action = TradeAction(
        session_id="test-session",
        action="open_position",
        order_id="uuid-order-1",
        symbol="BTC/USDT:USDT",
        side="long",
        trigger_reason=None,
        price=None,
        pnl=None,
        reasoning="RSI oversold + golden cross",
    )
    db_session.add(action)
    await db_session.commit()
    await db_session.refresh(action)
    assert action.id is not None
    assert action.reasoning == "RSI oversold + golden cross"
    assert action.created_at is not None


async def test_create_trade_action_with_pnl(db_session):
    from src.storage.models import TradeAction
    action = TradeAction(
        session_id="test-session",
        action="order_filled",
        order_id="uuid-order-2",
        symbol="BTC/USDT:USDT",
        side="long",
        trigger_reason="stop",
        price=None,
        pnl=-1.35,
        reasoning="(exchange: stop order filled @ 60200)",
    )
    db_session.add(action)
    await db_session.commit()
    await db_session.refresh(action)
    assert action.pnl == -1.35
    assert action.trigger_reason == "stop"


async def test_create_decision_log(db_session):
    from src.storage.models import DecisionLog
    log = DecisionLog(
        session_id="test-session",
        cycle_id="c1", trigger_type="scheduled", decision="open_long",
        reasoning="RSI oversold", model_used="claude-opus", tokens_used=1500,
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    assert log.tokens_used == 1500
    assert log.session_id == "test-session"


async def test_create_memory_entry(db_session):
    from src.storage.models import MemoryEntry
    m = MemoryEntry(
        session_id="test-session",
        memory_type="long_term", category="trade_review",
        content="BTC bounced at 60k", relevance_score=0.85,
    )
    db_session.add(m)
    await db_session.commit()
    await db_session.refresh(m)
    assert m.relevance_score == 0.85
    assert m.session_id == "test-session"


async def test_create_session(db_session):
    from src.storage.models import Session
    from sqlalchemy import select
    result = await db_session.execute(select(Session).where(Session.name == "test"))
    s = result.scalar_one()
    assert s.id == "test-session"
    assert s.status == "active"


async def test_sim_tables_exist():
    """Verify sim_balances, sim_positions, sim_orders tables are created."""
    from sqlalchemy import inspect
    from src.storage.database import init_db
    from src.storage.models import SimBalance, SimPosition, SimOrder

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as conn:
        table_names = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "sim_balances" in table_names
    assert "sim_positions" in table_names
    assert "sim_orders" in table_names
    await engine.dispose()


async def test_sim_balance_session_id_is_pk():
    from src.storage.models import SimBalance
    from src.storage.database import init_db, get_session

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    # Create session FK target
    from src.storage.models import Session
    async with get_session(engine) as session:
        session.add(Session(id="s1", name="s1-test", initial_balance=100.0))
        await session.commit()

    async with get_session(engine) as session:
        session.add(SimBalance(session_id="s1", free_usdt=100.0, used_usdt=0.0))
        await session.commit()

        session.add(SimBalance(session_id="s1", free_usdt=200.0, used_usdt=0.0))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await session.commit()
    await engine.dispose()


async def test_sim_position_unique_constraint():
    from src.storage.models import SimPosition, Session
    from src.storage.database import init_db, get_session

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as session:
        session.add(Session(id="s1", name="s1-test", initial_balance=100.0))
        await session.commit()

    async with get_session(engine) as session:
        session.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="long",
                                contracts=0.001, entry_price=95000.0, leverage=3))
        await session.commit()

        session.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="long",
                                contracts=0.002, entry_price=96000.0, leverage=3))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await session.commit()
    await engine.dispose()


async def test_sim_order_fields():
    from src.storage.models import SimOrder, Session
    from src.storage.database import init_db, get_session

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as session:
        session.add(Session(id="s1", name="s1-test", initial_balance=100.0))
        await session.commit()

    async with get_session(engine) as session:
        order = SimOrder(
            session_id="s1", order_id="uuid-1", symbol="BTC/USDT:USDT",
            side="buy", position_side="long", order_type="market",
            amount=0.001, status="closed", filled_price=95010.0, fee=0.0475,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        assert order.filled_price == 95010.0
        assert order.fee == 0.0475
        assert order.trigger_price is None
    await engine.dispose()


async def test_session_new_fields_have_defaults():
    """New R2 fields have ORM defaults — Session(id=..., name=...) still works."""
    from src.storage.models import Session
    from src.storage.database import init_db, get_session

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as session:
        s = Session(id="r2-test", name="r2-defaults")
        session.add(s)
        await session.commit()
        await session.refresh(s)

        assert s.exchange_type == "simulated"
        assert s.timeframe == "15m"
        assert s.scheduler_interval_min == 15
        assert s.approval_enabled is True
        assert s.alert_config is None
        assert s.fee_rate is None
        assert s.token_budget == 500000
        assert s.last_active_at is None
    await engine.dispose()
