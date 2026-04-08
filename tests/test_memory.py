import pytest


@pytest.fixture
async def memory(tmp_path):
    from src.storage.database import init_db, get_session
    from src.storage.models import Session
    from src.agent.memory import MemoryService
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with get_session(engine) as session:
        session.add(Session(id="test-session", name="test"))
        await session.commit()
    return MemoryService(engine, session_id="test-session")


async def test_save_and_get_long_term(memory):
    await memory.save_long_term("trade_review", "BTC bounced off 60k support", 0.9)
    memories = await memory.get_relevant_memories("trade_review", limit=5)
    assert len(memories) == 1
    assert "60k support" in memories[0].content


async def test_top_n_retrieval(memory):
    for i in range(15):
        await memory.save_long_term("lesson", f"Lesson {i}", relevance_score=i / 15)
    memories = await memory.get_relevant_memories(limit=10)
    assert len(memories) == 10
    assert memories[0].relevance_score >= memories[-1].relevance_score


async def test_short_term_save_and_clear(memory):
    await memory.save_short_term("current analysis: bullish")
    context = await memory.get_short_term_context()
    assert len(context) == 1
    await memory.clear_short_term()
    context = await memory.get_short_term_context()
    assert len(context) == 0


async def test_format_for_prompt(memory):
    await memory.save_long_term("lesson", "Avoid chasing pumps", 0.8)
    text = await memory.format_for_prompt()
    assert "Avoid chasing pumps" in text


async def test_session_isolation(tmp_path):
    """Memories from different sessions don't mix."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session
    from src.agent.memory import MemoryService
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/iso.db")
    async with get_session(engine) as session:
        session.add(Session(id="s1", name="session1"))
        session.add(Session(id="s2", name="session2"))
        await session.commit()
    mem1 = MemoryService(engine, session_id="s1")
    mem2 = MemoryService(engine, session_id="s2")
    await mem1.save_long_term("lesson", "S1 memory", 0.9)
    await mem2.save_long_term("lesson", "S2 memory", 0.9)
    assert len(await mem1.get_relevant_memories()) == 1
    assert "S1 memory" in (await mem1.get_relevant_memories())[0].content
    assert len(await mem2.get_relevant_memories()) == 1
    assert "S2 memory" in (await mem2.get_relevant_memories())[0].content
