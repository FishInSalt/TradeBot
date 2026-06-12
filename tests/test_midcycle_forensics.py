"""§6 取证：injected_events 列三写入点 + §2 被丢弃 run ⇒ 注入回滚。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.storage.models import AgentCycle


def _fake_record(raw=("conditional", "fill-sentinel")):
    return {"event": {"type": "fill"}, "raw": raw, "after_tool": "get_position", "offset_ms": 73000}


@pytest.mark.asyncio
async def test_injected_events_column_roundtrip(db_session):
    """新列可写可读 NULL / JSON 数组两态（spec §9 migration 验收）。"""
    db_session.add(AgentCycle(
        session_id="s-col", cycle_id="c1", triggered_by="scheduled",
        injected_events=json.dumps([{"event": {"type": "fill"}, "after_tool": "t", "offset_ms": 1}]),
    ))
    db_session.add(AgentCycle(session_id="s-col", cycle_id="c2", triggered_by="scheduled"))
    await db_session.commit()

    rows = (await db_session.execute(
        select(AgentCycle).where(AgentCycle.session_id == "s-col").order_by(AgentCycle.id)
    )).scalars().all()
    assert json.loads(rows[0].injected_events)[0]["after_tool"] == "t"
    assert rows[1].injected_events is None


def test_rollback_helper_requeues_and_clears():
    """_rollback_injected_events：requeue raw（同批序）+ 清空累积器。"""
    from src.cli.app import _rollback_injected_events
    from tests.test_midcycle_injector import make_deps

    requeued = []
    deps = make_deps()
    deps.requeue_events_fn = lambda evs: requeued.extend(evs)
    deps.injected_events_log.extend([
        _fake_record(("conditional", "f1")), _fake_record(("alert", "a1")),
    ])

    _rollback_injected_events(deps)
    assert requeued == [("conditional", "f1"), ("alert", "a1")]
    assert deps.injected_events_log == []


def test_rollback_helper_no_fn_just_clears():
    """requeue_events_fn 未接线（单测/旧路径）→ 只清空不炸。"""
    from src.cli.app import _rollback_injected_events
    from tests.test_midcycle_injector import make_deps

    deps = make_deps()
    deps.injected_events_log.append(_fake_record())
    _rollback_injected_events(deps)
    assert deps.injected_events_log == []


def _mock_agent_ok():
    """mock agent：run 正常返回。"""
    mock_result = MagicMock()
    usage = MagicMock()
    usage.total_tokens = 100
    usage.details = {}
    usage.cache_read_tokens = 0
    usage.cache_write_tokens = 0
    usage.input_tokens = 50
    usage.output_tokens = 50
    mock_result.usage.return_value = usage
    mock_result.output = "decision text"
    mock_result.new_messages.return_value = []
    agent = MagicMock()
    agent.run = AsyncMock(return_value=mock_result)
    agent.model = MagicMock(model_name="test-model")
    return agent


@pytest.mark.asyncio
async def test_success_path_serializes_records_stripping_raw(deps_factory, db_engine, db_session):
    """成功路径：injected_events 落 accumulator 序列化，raw 字段剥离（spec §6）。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    agent = _mock_agent_ok()
    ok_result = agent.run.return_value

    async def run_and_inject(*a, **kw):
        deps.injected_events_log.append(_fake_record())
        return ok_result

    agent.run = AsyncMock(side_effect=run_and_inject)

    await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    recs = json.loads(row.injected_events)
    assert recs[0]["after_tool"] == "get_position"
    assert "raw" not in recs[0]
    assert recs[0]["event"] == {"type": "fill"}


@pytest.mark.asyncio
async def test_usage_limit_rolls_back_and_nulls(deps_factory, db_engine, db_session):
    """usage_limit 终态：写库前 requeue + 清空 → injected_events 落 NULL（spec §2/§6）。"""
    from pydantic_ai.exceptions import UsageLimitExceeded

    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    requeued = []
    deps.requeue_events_fn = lambda evs: requeued.extend(evs)

    async def run_inject_then_blow(*a, **kw):
        deps.injected_events_log.append(_fake_record(("conditional", "f1")))
        raise UsageLimitExceeded("runaway")

    agent = _mock_agent_ok()
    agent.run = AsyncMock(side_effect=run_inject_then_blow)

    await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "usage_limit_exceeded"
    assert row.injected_events is None
    assert requeued == [("conditional", "f1")]
    assert deps.injected_events_log == []


@pytest.mark.asyncio
async def test_transient_retry_rolls_back_attempt1_injections(deps_factory, db_engine, db_session):
    """retry 交互：attempt 1 注入后抛瞬时异常 → 重试前 requeue + 清空；
    存活 attempt（未再注入）→ 最终行 injected_events NULL（spec §2 被丢弃 run 规则）。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    requeued = []
    deps.requeue_events_fn = lambda evs: requeued.extend(evs)

    agent = _mock_agent_ok()
    ok_result = agent.run.return_value
    calls = {"n": 0}

    async def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            deps.injected_events_log.append(_fake_record(("alert", "a1")))
            raise RuntimeError("transient")
        return ok_result

    agent.run = AsyncMock(side_effect=flaky)

    with patch("asyncio.sleep", new=AsyncMock()):
        await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "ok"
    assert row.injected_events is None, "attempt 1 的注入已回滚，存活 attempt 未注入 → NULL"
    assert requeued == [("alert", "a1")], "重试前必须 requeue（事件经下一 attempt/兜底重新送达）"


@pytest.mark.asyncio
async def test_retry_exhausted_rolls_back_and_nulls(deps_factory, db_engine, db_session):
    """retry_exhausted 终态：3 attempt 各自回滚，forensic 行 injected_events NULL。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    requeued = []
    deps.requeue_events_fn = lambda evs: requeued.extend(evs)

    agent = _mock_agent_ok()

    async def always_fail(*a, **kw):
        deps.injected_events_log.append(_fake_record(("conditional", "f1")))
        raise RuntimeError("network down")

    agent.run = AsyncMock(side_effect=always_fail)

    with patch("asyncio.sleep", new=AsyncMock()):
        await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "retry_exhausted"
    assert row.injected_events is None
    assert len(requeued) == 3, "每个被丢弃 attempt 的注入都回滚（3 attempt × 1 事件）"
