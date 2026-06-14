"""tool_call_recorder.tool_call_id field write test (仿 test_tool_call_recorder_result.py)."""
from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import ToolCall


@pytest.fixture
async def engine(tmp_path: Path):
    eng = await init_db(f"sqlite+aiosqlite:///{tmp_path}/recorder_tcid.db")
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def deps(engine):
    d = MagicMock()
    d.session_id = "test-session"
    d.cycle_id = "test-cycle"
    d.db_engine = engine
    return d


@pytest.mark.asyncio
async def test_tool_call_id_recorded(engine, deps):
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    call = MagicMock()
    call.tool_name = "get_market_data"
    call.tool_call_id = "call_abc123"
    call.args_as_dict = MagicMock(return_value={})
    ctx = MagicMock()
    ctx.deps = deps
    with contextlib.suppress(Exception):
        await recorder.wrap_tool_execute(
            ctx, call=call, tool_def=MagicMock(), args=MagicMock(),
            handler=AsyncMock(return_value="ok"),
        )
    async with get_session(engine) as session:
        row = (await session.execute(
            select(ToolCall.tool_call_id).order_by(ToolCall.id.desc()).limit(1)
        )).first()
    assert row is not None, "no row written"
    assert row.tool_call_id == "call_abc123"
