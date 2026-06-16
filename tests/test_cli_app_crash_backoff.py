"""半 A — 跨 cycle 崩溃退避重唤（spec §1）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.storage.models import AgentCycle


def test_trading_deps_has_scheduler_interval_min_field():
    """TradingDeps 暴露 scheduler_interval_min（退避封顶来源），默认 15，可覆写。"""
    from src.agent.trader import TradingDeps

    deps = TradingDeps(
        symbol="BTC/USDT:USDT", timeframe="15m",
        market_data=MagicMock(), exchange=MagicMock(), technical=MagicMock(),
        memory=MagicMock(), session_id="s",
    )
    assert deps.scheduler_interval_min == 15

    deps2 = TradingDeps(
        symbol="BTC/USDT:USDT", timeframe="15m",
        market_data=MagicMock(), exchange=MagicMock(), technical=MagicMock(),
        memory=MagicMock(), session_id="s", scheduler_interval_min=30,
    )
    assert deps2.scheduler_interval_min == 30


@pytest.mark.parametrize("n, fallback, expected", [
    # fallback=1 → floor=min(2,1)=1 → 恒 1（no-op，本就每分钟巡检）
    (1, 1, 1), (5, 1, 1),
    # fallback=60 → 2,4,8,16,32,60(封顶),60…
    (1, 60, 2), (2, 60, 4), (3, 60, 8), (4, 60, 16),
    (5, 60, 32), (6, 60, 60), (7, 60, 60),
    # fallback=180 → 2,4,…,128,180(封顶)
    (1, 180, 2), (7, 180, 128), (8, 180, 180), (12, 180, 180),
])
def test_backoff_min_curve(n, fallback, expected):
    from src.cli.app import backoff_min
    assert backoff_min(n, fallback) == expected


async def _add_cycle(db_session, session_id, status):
    db_session.add(AgentCycle(
        session_id=session_id, cycle_id="c", triggered_by="scheduled",
        execution_status=status,
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_count_consecutive_retry_exhausted_stops_at_first_non_re(db_engine, db_session):
    """末尾连续 retry_exhausted 计数，遇首个非 RE（含中间夹 ok）即止。"""
    from src.cli.app import _count_consecutive_retry_exhausted

    # 插入顺序 = id 升序；newest-first 看尾部：RE, RE, ok(止)
    for st in ["ok", "ok", "retry_exhausted", "ok", "retry_exhausted", "retry_exhausted"]:
        await _add_cycle(db_session, "sess-A", st)

    n = await _count_consecutive_retry_exhausted(db_engine, "sess-A")
    assert n == 2


@pytest.mark.asyncio
async def test_count_consecutive_single_crash(db_engine, db_session):
    """会话首个 cycle 即崩 → n=1。"""
    from src.cli.app import _count_consecutive_retry_exhausted
    await _add_cycle(db_session, "sess-B", "retry_exhausted")
    assert await _count_consecutive_retry_exhausted(db_engine, "sess-B") == 1


@pytest.mark.asyncio
async def test_count_consecutive_is_session_scoped(db_engine, db_session):
    """计数只看本会话；别的会话的 RE 不串味。"""
    from src.cli.app import _count_consecutive_retry_exhausted
    await _add_cycle(db_session, "sess-C", "retry_exhausted")
    await _add_cycle(db_session, "sess-D", "retry_exhausted")
    await _add_cycle(db_session, "sess-D", "retry_exhausted")
    assert await _count_consecutive_retry_exhausted(db_engine, "sess-C") == 1
    assert await _count_consecutive_retry_exhausted(db_engine, "sess-D") == 2


@pytest.mark.asyncio
async def test_count_consecutive_capped(db_engine, db_session):
    """连崩超过 fetch cap → 返回 cap（曲线已饱和，超出部分无意义）。"""
    from src.cli.app import _count_consecutive_retry_exhausted, _CRASH_STREAK_FETCH_CAP
    for _ in range(_CRASH_STREAK_FETCH_CAP + 5):
        await _add_cycle(db_session, "sess-E", "retry_exhausted")
    assert await _count_consecutive_retry_exhausted(db_engine, "sess-E") == _CRASH_STREAK_FETCH_CAP


# --- _schedule_crash_backoff 单元 ---

@pytest.mark.asyncio
async def test_schedule_crash_backoff_none_fn_no_raise(db_engine, db_session):
    """set_next_wake_fn=None（非交互/单测路径）→ 跳过不抛。"""
    from src.cli.app import _schedule_crash_backoff
    deps = MagicMock()
    deps.set_next_wake_fn = None
    await _schedule_crash_backoff(db_engine, deps, "RequestTimeout")  # 不抛即通过


@pytest.mark.asyncio
async def test_schedule_crash_backoff_normal_value(db_engine, db_session):
    """已有 1 条 RE 行 → n=1 → backoff_min(1, 60)=2；context 带 err_class。"""
    from src.cli.app import _schedule_crash_backoff
    await _add_cycle(db_session, "sess-F", "retry_exhausted")
    calls = []
    deps = MagicMock()
    deps.session_id = "sess-F"
    deps.scheduler_interval_min = 60
    deps.set_next_wake_fn = lambda minutes, ctx: calls.append((minutes, ctx))

    await _schedule_crash_backoff(db_engine, deps, "RequestTimeout")
    assert len(calls) == 1
    minutes, ctx = calls[0]
    assert minutes == 2
    assert ctx.startswith("crash-backoff:")
    assert "RequestTimeout" in ctx


@pytest.mark.asyncio
async def test_schedule_crash_backoff_count_query_failure_uses_floor(db_engine, monkeypatch):
    """计数查询自身失败 → fail-isolated 回退 n=1（floor），不二次击穿崩溃路径。"""
    from src.cli import app as app_mod
    calls = []
    deps = MagicMock()
    deps.session_id = "sess-G"
    deps.scheduler_interval_min = 60
    deps.set_next_wake_fn = lambda minutes, ctx: calls.append((minutes, ctx))

    async def _boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(app_mod, "_count_consecutive_retry_exhausted", _boom)

    await app_mod._schedule_crash_backoff(db_engine, deps, "RequestTimeout")
    assert calls == [(2, "crash-backoff: RequestTimeout")]   # 回退 n=1 → backoff_min(1, 60)=2=floor


# --- 端到端：run_agent_cycle 崩溃路径 ---

def _mock_agent():
    agent = MagicMock()
    return agent


@pytest.mark.asyncio
async def test_retry_exhausted_schedules_backoff(deps_factory, db_engine, db_session):
    """3 attempt 全崩 → 写 retry_exhausted 行 + 调 set_next_wake_fn（值=曲线、context=crash-backoff）。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    deps.scheduler_interval_min = 60
    calls = []
    deps.set_next_wake_fn = lambda minutes, ctx: calls.append((minutes, ctx))

    # 预置 1 条同会话 RE 行：本 cycle 再崩 → 连崩 count=2（≠ fail-isolation floor n=1）。
    # 使期望退避值 4≠2，证明 helper 在 live 路径读到的是真实 DB count，而非回退 floor。
    await _add_cycle(db_session, deps.session_id, "retry_exhausted")

    agent = _mock_agent()
    agent.run = AsyncMock(side_effect=RuntimeError("network down"))

    with patch("asyncio.sleep", new=AsyncMock()):
        await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "retry_exhausted"
    # 预置 1 + 本 cycle 1 = n=2 → backoff_min(2, 60)=4（≠ floor 2，证真实 count 在 live 路径驱动）
    assert len(calls) == 1
    minutes, ctx = calls[0]
    assert minutes == 4
    assert ctx.startswith("crash-backoff:")
    assert "RuntimeError" in ctx


@pytest.mark.asyncio
async def test_usage_limit_does_not_schedule_backoff(deps_factory, db_engine, db_session):
    """usage_limit_exceeded 是病理死循环 → 不退避重唤（spec §1 排除）。"""
    from src.cli.app import TokenBudget, run_agent_cycle
    from pydantic_ai.exceptions import UsageLimitExceeded

    deps = deps_factory()
    deps.scheduler_interval_min = 60
    calls = []
    deps.set_next_wake_fn = lambda minutes, ctx: calls.append((minutes, ctx))

    agent = _mock_agent()
    agent.run = AsyncMock(side_effect=UsageLimitExceeded("runaway"))

    with patch("asyncio.sleep", new=AsyncMock()):
        await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "usage_limit_exceeded"
    assert calls == [], "usage_limit 不应触发退避重唤"
