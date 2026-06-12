import pytest
from datetime import datetime, timezone, timedelta

from src.storage.database import get_session
from src.storage.models import Session as SessionModel, AgentCycle, ToolCall

UTC = timezone.utc


async def _seed_session(engine, sid="s1", interval=15, last_active=None, status="active"):
    async with get_session(engine) as s:
        s.add(SessionModel(id=sid, name=sid, symbol="BTC/USDT:USDT",
                           initial_balance=10000.0, status=status,
                           scheduler_interval_min=interval, last_active_at=last_active))
        await s.commit()


async def _add_cycle(engine, sid="s1", cycle_id="aaaa", triggered_by="scheduled",
                     decision="line1\nline2", created_at=None, **kw):
    async with get_session(engine) as s:
        c = AgentCycle(session_id=sid, cycle_id=cycle_id, triggered_by=triggered_by,
                       decision=decision, tokens_consumed=kw.get("tokens", 100),
                       wall_time_ms=kw.get("wall", 5000), execution_status="ok",
                       created_at=created_at or datetime.now(UTC),
                       state_snapshot=kw.get("snapshot"))
        s.add(c)
        await s.commit()
        return c.id


@pytest.mark.asyncio
async def test_get_cycles_orders_desc_and_paginates(engine):
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    ids = []
    for i in range(5):
        ids.append(await _add_cycle(engine, cycle_id=f"c{i}", created_at=base + timedelta(minutes=i)))
    from src.webui.queries import get_cycles
    rows = await get_cycles(engine, "s1", limit=2)
    assert [r.id for r in rows] == [ids[4], ids[3]]          # 最新在前
    older = await get_cycles(engine, "s1", limit=2, before_id=ids[3])
    assert [r.id for r in older] == [ids[2], ids[1]]
    newer = await get_cycles(engine, "s1", after_id=ids[3])
    assert [r.id for r in newer] == [ids[4]]
    assert rows[0].decision_head and "line1" in rows[0].decision_head
