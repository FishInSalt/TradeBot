import pytest


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


async def test_create_trade_record(db_session):
    from src.storage.models import TradeRecord
    trade = TradeRecord(
        session_id="test-session",
        symbol="BTC/USDT:USDT", side="long", entry_price=65000.0,
        quantity=0.01, leverage=3, status="open",
        decision_reason="Bullish MA crossover",
    )
    db_session.add(trade)
    await db_session.commit()
    await db_session.refresh(trade)
    assert trade.id is not None
    assert trade.session_id == "test-session"
    assert trade.created_at is not None


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
