"""Iter 3 tool_call_recorder.args field write tests.

Spec §5.3.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.database import init_db, get_session
from src.storage.models import ToolCall


@pytest.fixture
async def engine(tmp_path: Path):
    eng = await init_db(f"sqlite+aiosqlite:///{tmp_path}/recorder.db")
    try:
        yield eng
    finally:
        await eng.dispose()    # closes pool connections; _session_factories isolation is implicit via unique id(engine) per tmp_path


async def _run_recorder(engine, deps, call_args: Any) -> str | None:
    """Helper: invoke ToolCallRecorder with mock call.args, return DB-stored args field."""
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    call = MagicMock()
    call.tool_name = "test_tool"
    call.tool_call_id = None
    call.args = call_args
    call.args_as_dict = MagicMock(return_value=dict(call_args) if isinstance(call_args, dict) else json.loads(call_args) if isinstance(call_args, str) else {})

    tool_def = MagicMock()
    handler = AsyncMock(return_value="ok")

    ctx = MagicMock()
    ctx.deps = deps

    await recorder.wrap_tool_execute(
        ctx, call=call, tool_def=tool_def, args=MagicMock(), handler=handler,
    )

    async with get_session(engine) as session:
        from sqlalchemy import select
        result = await session.execute(select(ToolCall.args).order_by(ToolCall.id.desc()).limit(1))
        return result.scalar()


@pytest.fixture
async def deps(engine):
    """Mock TradingDeps minimum surface (session_id / cycle_id / db_engine).

    async def: pytest-asyncio auto mode + sync fixture depending on async fixture
    can yield un-awaited coroutine; async def ensures engine is properly resolved.
    """
    d = MagicMock()
    d.session_id = "test-session"
    d.cycle_id = "test-cycle"
    d.db_engine = engine
    return d


@pytest.mark.asyncio
async def test_args_serialized_to_json_dict(engine, deps):
    deps.db_engine = engine
    args = await _run_recorder(engine, deps, {"side": "long", "pct": 30})
    assert args is not None
    parsed = json.loads(args)
    assert parsed == {"side": "long", "pct": 30}


@pytest.mark.asyncio
async def test_args_strips_reasoning_key(engine, deps):
    deps.db_engine = engine
    args = await _run_recorder(engine, deps, {"side": "long", "reasoning": "long text..."})
    assert args is not None
    parsed = json.loads(args)
    assert "reasoning" not in parsed
    assert parsed == {"side": "long"}


@pytest.mark.asyncio
async def test_args_truncated_at_4000(engine, deps):
    deps.db_engine = engine
    big = {"data": "x" * 5000}
    args = await _run_recorder(engine, deps, big)
    assert args is not None
    assert len(args) <= 4000


@pytest.mark.asyncio
async def test_args_none_when_empty_dict(engine, deps):
    deps.db_engine = engine
    args = await _run_recorder(engine, deps, {})
    assert args is None


@pytest.mark.asyncio
async def test_args_none_when_call_args_is_none(engine, deps):
    """Direct None case (vs empty dict {}); ensures args_as_dict() three-state coverage."""
    deps.db_engine = engine
    args = await _run_recorder(engine, deps, None)
    assert args is None


@pytest.mark.asyncio
async def test_args_does_not_mutate_caller_dict(engine, deps):
    """Regression lock for commit e31e6b7: pydantic-ai args_as_dict() returns
    self.args by reference for dict inputs (messages.py:1660). Recorder must
    dict()-copy before pop("reasoning"); without the copy, the live
    ToolCallPart.args dict is mutated in place — a latent defect for any
    future post-run message inspection.

    Note: cannot reuse _run_recorder helper because its `dict(call_args)`
    return_value already makes a defensive copy, masking regressions of the
    recorder's own `dict(call.args_as_dict())` wrapper. This test bypasses the
    helper to mock args_as_dict returning the SAME reference (real pydantic-ai
    behavior on dict input).
    """
    from src.services.tool_call_recorder import ToolCallRecorder

    deps.db_engine = engine
    original_args = {"side": "long", "reasoning": "must persist on caller"}

    recorder = ToolCallRecorder()
    call = MagicMock()
    call.tool_name = "test_tool"
    call.tool_call_id = None
    call.args = original_args
    call.args_as_dict = MagicMock(return_value=original_args)   # same ref, NOT a copy

    tool_def = MagicMock()
    handler = AsyncMock(return_value="ok")
    ctx = MagicMock()
    ctx.deps = deps

    await recorder.wrap_tool_execute(
        ctx, call=call, tool_def=tool_def, args=MagicMock(), handler=handler,
    )

    # Caller's dict must be unchanged — recorder must not mutate ToolCallPart.args.
    assert "reasoning" in original_args, "recorder mutated caller's dict (dict() wrapper regression)"
    assert original_args == {"side": "long", "reasoning": "must persist on caller"}
