"""tool_call_recorder.result field write tests.

Spec: docs/superpowers/specs/2026-06-13-webui-tool-result-persistence-design.md §捕获语义。
result 捕获与 args 捕获同构（见 test_tool_call_recorder_args.py）。
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import ToolCall

_TRUNC_MARK = "\n…[truncated]"


@pytest.fixture
async def engine(tmp_path: Path):
    eng = await init_db(f"sqlite+aiosqlite:///{tmp_path}/recorder_result.db")
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


async def _record_and_get(engine, deps, handler):
    """Run recorder with given handler; return last-row (result, status).

    Swallows handler exceptions (error path re-raises) so the recorded row
    can still be inspected.
    """
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    call = MagicMock()
    call.tool_name = "test_tool"
    call.tool_call_id = None
    call.args_as_dict = MagicMock(return_value={})
    ctx = MagicMock()
    ctx.deps = deps
    with contextlib.suppress(Exception):
        await recorder.wrap_tool_execute(
            ctx, call=call, tool_def=MagicMock(), args=MagicMock(), handler=handler,
        )
    async with get_session(engine) as session:
        row = (await session.execute(
            select(ToolCall.result, ToolCall.status).order_by(ToolCall.id.desc()).limit(1)
        )).first()
    assert row is not None, "no row written — recorder may have silently failed"
    return row


@pytest.mark.asyncio
async def test_result_captured(engine, deps):
    row = await _record_and_get(engine, deps, AsyncMock(return_value="=== Ticker ===\nlast 63000"))
    assert row.result == "=== Ticker ===\nlast 63000"
    assert row.status == "ok"


@pytest.mark.asyncio
async def test_result_truncated_at_30000(engine, deps):
    row = await _record_and_get(engine, deps, AsyncMock(return_value="x" * 40000))
    assert row.result.startswith("x" * 30000)
    assert row.result.endswith(_TRUNC_MARK)
    assert len(row.result) == 30000 + len(_TRUNC_MARK)


@pytest.mark.asyncio
async def test_result_null_on_exception(engine, deps):
    async def boom(args):
        raise ValueError("nope")
    row = await _record_and_get(engine, deps, boom)
    assert row.result is None
    assert row.status == "error"


@pytest.mark.asyncio
async def test_result_null_when_handler_returns_none(engine, deps):
    """handler 合法返回 None → `if result is not None` guard 落 NULL，status 仍 ok。"""
    row = await _record_and_get(engine, deps, AsyncMock(return_value=None))
    assert row.result is None
    assert row.status == "ok"


@pytest.mark.asyncio
async def test_result_captured_on_biz_error(engine, deps):
    from src.services.tool_call_recorder import note_biz_error

    async def biz(args):
        note_biz_error("alert_not_found")     # 合法 BIZ_ERROR_TYPE（recorder.py:60）
        return "alert a1 not found"
    row = await _record_and_get(engine, deps, biz)
    assert row.result == "alert a1 not found"
    assert row.status == "biz_error"
