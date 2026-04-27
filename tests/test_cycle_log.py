"""Iter 1 (T1-1a) — cycle log cache 仪表化 tests.

观察期 §B3 Step 1：cycle log 输出 cache_hit / cache_miss / hit_rate
让 W1 cache hit rate baseline 可量化、零除安全。
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import models

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel

models.ALLOW_MODEL_REQUESTS = False


async def _make_deps_and_engine(session_id: str = "sess-iter1"):
    """Minimal TradingDeps + engine + session row。复用自 test_usage_limits 模式。"""
    from src.agent.trader import TradingDeps

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="iter1"))
        await db.commit()

    deps = TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=AsyncMock(),
        exchange=AsyncMock(),
        technical=MagicMock(),
        memory=AsyncMock(format_for_prompt=AsyncMock(return_value="No memories.")),
        session_id=session_id,
        db_engine=engine,
    )
    return deps, engine


def _make_agent_with_usage(usage_obj):
    """Mock agent.run 返回 result，result.usage() → usage_obj。"""
    async def mock_run(prompt, **kwargs):
        result = MagicMock()
        result.usage = lambda: usage_obj
        result.new_messages = lambda: []
        result.output = "test output"
        return result

    mock_agent = MagicMock()
    mock_agent.run = mock_run
    mock_agent.model = "test-model"
    return mock_agent


async def test_cycle_log_includes_cache_fields_when_present(caplog):
    """T1: usage.details 含 DeepSeek cache 字段时，cycle log 输出 cache_hit/miss/rate。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine("sess-cache-1")
    budget = TokenBudget(daily_max=500_000)

    usage = MagicMock(
        total_tokens=10_500,
        details={
            "reasoning_tokens": 500,
            "prompt_cache_hit_tokens": 8_000,
            "prompt_cache_miss_tokens": 2_000,
        },
    )
    agent = _make_agent_with_usage(usage)

    with caplog.at_level(logging.INFO, logger="src.cli.app"):
        await run_agent_cycle(
            agent=agent, deps=deps, trigger_type="scheduled",
            budget=budget, engine=engine,
        )

    cycle_log = next(
        (r.message for r in caplog.records if "tokens:" in r.message),
        None,
    )
    assert cycle_log is not None, "未找到 cycle tokens 行"
    assert "cache_hit=8000" in cycle_log, f"缺 cache_hit 字段: {cycle_log}"
    assert "cache_miss=2000" in cycle_log, f"缺 cache_miss 字段: {cycle_log}"
    assert "rate=80.0%" in cycle_log, f"hit_rate 计算错（应 80.0%）: {cycle_log}"
    assert "reasoning=500" in cycle_log, f"reasoning_tokens 不应丢: {cycle_log}"
    assert "total=10500" in cycle_log


async def test_cycle_log_hit_rate_zero_when_no_cache_data(caplog):
    """T2: 非 DeepSeek model（details 无 cache 字段），输出全 0 不报错。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine("sess-cache-2")
    budget = TokenBudget(daily_max=500_000)

    usage = MagicMock(
        total_tokens=5_000,
        details={"reasoning_tokens": 0},  # 无 prompt_cache_* 字段
    )
    agent = _make_agent_with_usage(usage)

    with caplog.at_level(logging.INFO, logger="src.cli.app"):
        await run_agent_cycle(
            agent=agent, deps=deps, trigger_type="scheduled",
            budget=budget, engine=engine,
        )

    cycle_log = next(
        (r.message for r in caplog.records if "tokens:" in r.message),
        None,
    )
    assert cycle_log is not None
    assert "cache_hit=0" in cycle_log
    assert "cache_miss=0" in cycle_log
    assert "rate=0.0%" in cycle_log


async def test_cycle_log_hit_rate_zero_division_safe(caplog):
    """T3: details=None（usage 无 details）— 除零保护，不抛异常，rate=0.0%。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine("sess-cache-3")
    budget = TokenBudget(daily_max=500_000)

    usage = MagicMock(total_tokens=1_000, details=None)
    agent = _make_agent_with_usage(usage)

    with caplog.at_level(logging.INFO, logger="src.cli.app"):
        result = await run_agent_cycle(
            agent=agent, deps=deps, trigger_type="scheduled",
            budget=budget, engine=engine,
        )

    assert result is not None, "正常 cycle 应返回 result（除零保护不影响主路径）"

    cycle_log = next(
        (r.message for r in caplog.records if "tokens:" in r.message),
        None,
    )
    assert cycle_log is not None
    assert "cache_hit=0" in cycle_log
    assert "rate=0.0%" in cycle_log
