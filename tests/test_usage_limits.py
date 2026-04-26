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
        result.usage = lambda: MagicMock(total_tokens=100)
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
