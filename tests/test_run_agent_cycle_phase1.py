"""AC-2: 三路径 8 字段填值符合 spec §5.5.1/§5.5.2 规则。"""
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from sqlalchemy import select

from src.cli.app import run_agent_cycle, TokenBudget
from src.storage.models import AgentCycle


@pytest.mark.asyncio
async def test_happy_path_fills_all_8_fields(make_usage, deps_factory, db_engine, db_session):
    """T12.1 (AC-2 happy): 全 8 字段非 NULL 且符合 §5.5.1 公式。"""
    usage = make_usage(
        input_tokens=1500, output_tokens=300,
        cache_read_tokens=1050, cache_write_tokens=10,
    )
    mock_result = MagicMock()
    mock_result.usage.return_value = usage
    mock_result.output = "(1) Stance: long. (2) Active: ..."
    mock_result.new_messages.return_value = []

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(return_value=mock_result)
    mock_agent.model = MagicMock(model_name="test-model")

    deps = deps_factory()
    budget = TokenBudget(daily_max=100000)
    await run_agent_cycle(
        mock_agent, deps, "scheduled", budget, db_engine,
    )

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1)
    )).scalar_one()

    assert row.execution_status == "ok"
    assert row.wall_time_ms is not None and row.wall_time_ms >= 0  # >=0: forensic 亚毫秒可能为 0
    assert row.llm_call_ms is not None and row.llm_call_ms >= 0
    assert row.input_tokens == 1500
    assert row.output_tokens == 300
    assert row.cache_read_tokens == 1050
    assert row.cache_write_tokens == 10
    assert row.reasoning_tokens == 0
    assert row.cache_hit_rate == pytest.approx(70.0)   # 1050/1500*100


@pytest.mark.asyncio
async def test_usage_limit_exceeded_only_wall_time_filled(deps_factory, db_engine, db_session):
    """T12.2 (AC-2 forensic): UsageLimitExceeded 路径仅 wall_time_ms 填，其余 NULL。"""
    from pydantic_ai.exceptions import UsageLimitExceeded

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=UsageLimitExceeded("test limit"))
    mock_agent.model = MagicMock(model_name="test-model")

    deps = deps_factory()
    budget = TokenBudget(daily_max=100000)
    await run_agent_cycle(
        mock_agent, deps, "scheduled", budget, db_engine,
    )

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1)
    )).scalar_one()

    assert row.execution_status == "usage_limit_exceeded"
    assert row.wall_time_ms is not None and row.wall_time_ms >= 0  # >=0: forensic 亚毫秒可能为 0
    assert row.llm_call_ms is None
    assert row.input_tokens is None
    assert row.output_tokens is None
    assert row.cache_read_tokens is None
    assert row.cache_write_tokens is None
    assert row.reasoning_tokens is None
    assert row.cache_hit_rate is None
    assert row.tokens_consumed == 0


@pytest.mark.asyncio
async def test_retry_exhausted_only_wall_time_filled(deps_factory, db_engine, db_session):
    """T12.3 (AC-2 forensic): retry_exhausted 路径仅 wall_time_ms 填，其余 NULL。"""
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=RuntimeError("network down"))
    mock_agent.model = MagicMock(model_name="test-model")

    deps = deps_factory()
    budget = TokenBudget(daily_max=100000)
    with patch("asyncio.sleep", new=AsyncMock()):  # skip backoff for fast test
        await run_agent_cycle(
            mock_agent, deps, "scheduled", budget, db_engine,
        )

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1)
    )).scalar_one()

    assert row.execution_status == "retry_exhausted"
    assert row.wall_time_ms is not None and row.wall_time_ms >= 0  # >=0: forensic 亚毫秒可能为 0
    assert row.llm_call_ms is None
    for col in ("input_tokens", "output_tokens", "cache_read_tokens",
                "cache_write_tokens", "reasoning_tokens", "cache_hit_rate"):
        assert getattr(row, col) is None, f"{col} expected None"
