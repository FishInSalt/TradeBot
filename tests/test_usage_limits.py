"""Iter 5 §3.1 — UsageLimits + UsageLimitExceeded forensic path tests."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import models
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, DecisionLog

models.ALLOW_MODEL_REQUESTS = False


async def _make_deps_and_engine(session_id: str = "sess-iter5"):
    """Build minimal TradingDeps + real engine + session row (FK target)."""
    from src.agent.trader import TradingDeps

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="iter5"))
        await db.commit()

    deps = TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=AsyncMock(),
        exchange=AsyncMock(),
        technical=MagicMock(),
        memory=AsyncMock(format_for_prompt=AsyncMock(return_value="No relevant memories.")),
        session_id=session_id,
        db_engine=engine,
    )
    return deps, engine


async def test_usage_limits_passed_to_agent_run(monkeypatch):
    """T1: run_agent_cycle 调用 agent.run 时 kwargs 含 usage_limits 且 == USAGE_LIMITS_PER_CYCLE。"""
    from src.cli.app import USAGE_LIMITS_PER_CYCLE, TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine()
    budget = TokenBudget(daily_max=500_000)

    captured_kwargs = {}

    async def mock_run(prompt, **kwargs):
        captured_kwargs.update(kwargs)
        result = MagicMock()
        result.usage = lambda: MagicMock(total_tokens=100, details=None)
        result.new_messages = lambda: []
        result.output = "test output"
        return result

    mock_agent = MagicMock()
    mock_agent.run = mock_run
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent,
        deps=deps,
        trigger_type="scheduled",
        budget=budget,
        engine=engine,
    )

    assert "usage_limits" in captured_kwargs, (
        f"agent.run 未收到 usage_limits 参数, captured: {list(captured_kwargs.keys())}"
    )
    assert captured_kwargs["usage_limits"] is USAGE_LIMITS_PER_CYCLE, (
        f"usage_limits 不是 USAGE_LIMITS_PER_CYCLE 常量"
    )


async def test_usage_limit_exceeded_writes_forensic_decision_log():
    """T2: UsageLimitExceeded 触发时写 decision_logs 1 行 + 函数返回 None。"""
    from pydantic_ai.exceptions import UsageLimitExceeded

    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine(session_id="sess-t2")
    budget = TokenBudget(daily_max=500_000)

    async def boom(prompt, **kwargs):
        raise UsageLimitExceeded("test reason")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    result = await run_agent_cycle(
        agent=mock_agent,
        deps=deps,
        trigger_type="scheduled",
        budget=budget,
        engine=engine,
    )

    assert result is None, "病理 cycle 应返回 None"

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(DecisionLog).where(DecisionLog.decision == "usage_limit_exceeded")
        )).scalars().all()

    assert len(rows) == 1, f"应写 1 行 decision='usage_limit_exceeded'，实际 {len(rows)} 行"
    row = rows[0]
    assert row.session_id == "sess-t2"
    assert "test reason" in row.reasoning
    assert row.tokens_used == 0  # spec §3.1 #3 设计取舍


async def test_usage_limit_exceeded_does_not_retry():
    """T3: UsageLimitExceeded 不进 range(3) 重试，agent.run 仅被调 1 次。"""
    from pydantic_ai.exceptions import UsageLimitExceeded

    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine(session_id="sess-t3")
    budget = TokenBudget(daily_max=500_000)

    call_count = {"n": 0}

    async def boom(prompt, **kwargs):
        call_count["n"] += 1
        raise UsageLimitExceeded("test")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    assert call_count["n"] == 1, (
        f"agent.run 应仅被调 1 次（不重试），实际 {call_count['n']} 次"
    )


async def test_generic_exception_still_retries_3_times(monkeypatch):
    """T4: 通用 Exception 不被 UsageLimitExceeded 路径误捕，仍走 3 次重试。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine(session_id="sess-t4")
    budget = TokenBudget(daily_max=500_000)

    # 跳过实际 sleep 加速测试
    async def fast_sleep(_):
        pass
    monkeypatch.setattr("asyncio.sleep", fast_sleep)

    call_count = {"n": 0}

    async def flaky(prompt, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError("transient network error")
        result = MagicMock()
        result.usage = lambda: MagicMock(total_tokens=100, details=None)
        result.new_messages = lambda: []
        result.output = "recovered"
        return result

    mock_agent = MagicMock()
    mock_agent.run = flaky
    mock_agent.model = "test-model"

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    assert call_count["n"] == 3, f"应重试 3 次，实际 {call_count['n']}"
    assert result is not None, "第 3 次成功应返回 result"


async def test_t9_success_path_writes_status_ok_and_long_reasoning():
    """T9: 成功路径写 decision=派生 / status='ok' / reasoning truncated to 4000."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine(session_id="sess-t9")
    budget = TokenBudget(daily_max=500_000)

    # 喂 5000-char 长输出验证 cap 4000
    long_output = "x" * 5000

    async def mock_run(prompt, **kwargs):
        result = MagicMock()
        result.usage = lambda: MagicMock(total_tokens=100, details=None)
        result.new_messages = lambda: []
        result.output = long_output
        return result

    mock_agent = MagicMock()
    mock_agent.run = mock_run
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(DecisionLog).where(DecisionLog.session_id == "sess-t9")
        )).scalars().all()

    assert len(rows) == 1, f"应写 1 行 DecisionLog，实际 {len(rows)}"
    row = rows[0]
    assert row.status == "ok", f"成功路径 status 应 'ok'，实际 {row.status!r}"
    assert row.decision == "hold", \
        f"无 trade_actions 该 cycle 派生应 'hold'，实际 {row.decision!r}"
    assert len(row.reasoning) == 4000, \
        f"reasoning 应截断到 4000 chars，实际 {len(row.reasoning)}"


def test_usage_limit_total_tokens_capped_at_200k():
    """T5: USAGE_LIMITS_PER_CYCLE.total_tokens_limit == 200_000.

    pre-next-observation §T1-1c (W2 prep Iter 5) drift guard：W1 实测
    max 141k tokens/cycle，200k 留 ~40% buffer 同时收紧病理 cycle 爆裂上限
    （从 Iter 5 §3.1 引入时的 300k 兜底降下来）。
    """
    from src.cli.app import USAGE_LIMITS_PER_CYCLE
    assert USAGE_LIMITS_PER_CYCLE.total_tokens_limit == 200_000, (
        f"期望 200_000，实际 {USAGE_LIMITS_PER_CYCLE.total_tokens_limit}；"
        "如需调整请同步更新 pre-next-observation §T1-1c 与本测试"
    )
