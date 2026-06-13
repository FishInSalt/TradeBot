import json

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
                     decision="line1\nline2", created_at=None, trigger_context=None, **kw):
    # live capture 把 trigger_context 落库为 JSON list（多触发堆）——稳态主流形态，
    # 默认即用 list，避免 fixture 恒 NULL 漏掉 list→CycleDetail 的真实路径（PR#75 500 教训）。
    if trigger_context is None:
        trigger_context = [{"type": "scheduled_tick"}]
    async with get_session(engine) as s:
        c = AgentCycle(session_id=sid, cycle_id=cycle_id, triggered_by=triggered_by,
                       decision=decision, tokens_consumed=kw.get("tokens", 100),
                       wall_time_ms=kw.get("wall", 5000), execution_status="ok",
                       created_at=created_at or datetime.now(UTC),
                       trigger_context=json.dumps(trigger_context),
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


@pytest.mark.asyncio
async def test_get_cycles_after_id_no_gap_when_new_exceeds_limit(engine):
    """after_id 增量轮询：新增数 > limit 时取紧邻游标的一批（不跳过中间 cycle）。"""
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    ids = [await _add_cycle(engine, cycle_id=f"g{i}", created_at=base + timedelta(minutes=i))
           for i in range(5)]
    from src.webui.queries import get_cycles
    # 游标 ids[0] 之上有 4 条新增（ids[1..4]），limit=2：应取紧邻的 ids[1]/ids[2]，
    # 而非最新的 ids[3]/ids[4]（DESC 旧实现会跳过 ids[1]/ids[2] → 空洞）。
    rows = await get_cycles(engine, "s1", limit=2, after_id=ids[0])
    assert [r.id for r in rows] == [ids[2], ids[1]]     # DESC 输出，但取的是紧邻游标那批
    assert ids[1] in [r.id for r in rows]               # 紧邻游标的 cycle 不被跳过


@pytest.mark.asyncio
async def test_get_cycle_detail_joins_tool_calls_as_children(engine):
    await _seed_session(engine)
    pk = await _add_cycle(engine, cycle_id="d1",
                          snapshot='{"balance":{"total_usdt":10050.0},"position":null}')
    async with get_session(engine) as s:
        for i, name in enumerate(["get_position", "get_market_data"]):
            s.add(ToolCall(session_id="s1", cycle_id="d1", tool_name=name, status="ok",
                           duration_ms=10 + i, args='{"symbol":"BTC/USDT:USDT"}'))
        await s.commit()
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert d.cycle_label == "d1"
    assert [t.tool_name for t in d.tool_calls] == ["get_position", "get_market_data"]
    assert d.tool_calls[0].args == {"symbol": "BTC/USDT:USDT"}
    assert d.state_snapshot["balance"]["total_usdt"] == 10050.0


@pytest.mark.asyncio
async def test_get_cycle_detail_missing_returns_none(engine):
    await _seed_session(engine)
    from src.webui.queries import get_cycle_detail
    assert await get_cycle_detail(engine, 99999) is None


@pytest.mark.asyncio
async def test_get_cycle_detail_accepts_list_trigger_context(engine):
    """live capture 把 trigger_context 写成 JSON list（多触发堆）；CycleDetail 须接受 list 不 500（PR#75 回归）。"""
    await _seed_session(engine)
    pk = await _add_cycle(engine, cycle_id="lc1",
                          trigger_context=[{"type": "scheduled_tick"},
                                           {"type": "alert", "alert_id": "a1"}])
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert isinstance(d.trigger_context, list)
    assert d.trigger_context[0]["type"] == "scheduled_tick"
    assert d.trigger_context[1]["alert_id"] == "a1"


@pytest.mark.asyncio
async def test_tool_call_result_is_reserved_none(engine):
    """result 是预留字段：DB tool_calls 无 result 列，产出的工具行 result 恒 None（seam，待后端补全）。"""
    await _seed_session(engine)
    pk = await _add_cycle(engine, cycle_id="tr1")
    async with get_session(engine) as s:
        s.add(ToolCall(session_id="s1", cycle_id="tr1", tool_name="get_position",
                       status="ok", duration_ms=5, args=None))
        await s.commit()
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert d.tool_calls[0].result is None


@pytest.mark.asyncio
async def test_get_live_status_assembles(engine):
    la = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _seed_session(engine, interval=15, last_active=la, status="active")
    from src.storage.models import SimPosition, SimOrder, TradeAction
    async with get_session(engine) as s:
        s.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="long",
                          contracts=1.0, entry_price=63000.0, leverage=5))
        s.add(SimOrder(session_id="s1", order_id="o1", symbol="BTC/USDT:USDT", side="sell",
                       position_side="long", order_type="stop", amount=1.0, trigger_price=62000.0,
                       status="open", leverage=5))
        # v_alert_lifecycle registers CTE 认 action='add_price_level_alert'（views.py:104）
        s.add(TradeAction(session_id="s1", action="add_price_level_alert", alert_id="a1",
                          symbol="BTC/USDT:USDT", price=64000.0, reasoning="breakout"))
        await s.commit()
    from src.webui.queries import get_live_status
    ls = await get_live_status(engine, "s1")
    assert ls.status == "active"
    assert ls.last_active_at == la
    assert ls.position.side == "long" and ls.position.contracts == 1.0
    assert [o.order_id for o in ls.open_orders] == ["o1"]
    assert any(a.alert_id == "a1" for a in ls.active_alerts)


@pytest.mark.asyncio
async def test_get_performance_equity_skips_none_balance(engine):
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _add_cycle(engine, cycle_id="c1", created_at=base,
                     snapshot='{"balance":{"total_usdt":10000.0}}')
    await _add_cycle(engine, cycle_id="c2", created_at=base + timedelta(minutes=15),
                     snapshot='{"balance":null}')          # 失败点 → 跳过
    await _add_cycle(engine, cycle_id="c3", created_at=base + timedelta(minutes=30),
                     snapshot='{"balance":{"total_usdt":10120.0}}')
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")
    assert perf.initial_balance == 10000.0
    assert [round(p.equity, 1) for p in perf.equity_curve] == [10000.0, 10120.0]   # null 被跳


@pytest.mark.asyncio
async def test_list_sessions_summary(engine):
    la = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _seed_session(engine, sid="s1", interval=15, last_active=la)
    await _add_cycle(engine, sid="s1", cycle_id="c1", created_at=la)
    await _add_cycle(engine, sid="s1", cycle_id="c2", created_at=la + timedelta(minutes=5))
    from src.webui.queries import list_sessions, get_session_detail
    rows = await list_sessions(engine)
    assert len(rows) == 1
    assert rows[0].cycle_count == 2
    assert rows[0].status == "active"
    detail = await get_session_detail(engine, "s1")
    assert detail.scheduler_interval_min == 15
    assert await get_session_detail(engine, "nope") is None
