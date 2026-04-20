"""Unit tests for ToolCallRecorder capability."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from unittest.mock import MagicMock, AsyncMock

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, ToolCall


@pytest.fixture
async def engine() -> AsyncEngine:
    return await init_db("sqlite+aiosqlite:///:memory:")


@pytest.fixture
async def session_with_row(engine: AsyncEngine) -> str:
    """Insert a parent session row so ToolCall FK holds."""
    async with get_session(engine) as db:
        db.add(SessionModel(id="sess-test", name="unit-test"))
        await db.commit()
    return "sess-test"


def make_deps(engine: AsyncEngine, session_id: str, cycle_id: str | None = "cyc-test"):
    """Construct a minimal TradingDeps for recorder tests."""
    from src.agent.trader import TradingDeps
    return TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=MagicMock(),
        exchange=MagicMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
        session_id=session_id,
        db_engine=engine,
        cycle_id=cycle_id,
    )


def make_ctx(deps):
    """Fake pydantic_ai RunContext with .deps set."""
    ctx = MagicMock()
    ctx.deps = deps
    return ctx


def make_call(tool_name: str = "get_market_data"):
    """Fake pydantic_ai ToolCallPart."""
    call = MagicMock()
    call.tool_name = tool_name
    return call


async def test_records_successful_tool_call(engine, session_with_row):
    """Tool returns normally → one row with status=ok, duration_ms>=0, error_type=None."""
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        return "tool returned this"

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("get_market_data"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert result == "tool returned this"

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].tool_name == "get_market_data"
    assert rows[0].status == "ok"
    assert rows[0].error_type is None
    assert rows[0].duration_ms >= 0
    assert rows[0].session_id == "sess-test"
    assert rows[0].cycle_id == "cyc-test"


async def test_records_failed_tool_call(engine, session_with_row):
    """Tool raises → row with status=error + error_type; exception still propagates."""
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("get_position"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error_type == "ValueError"
    assert rows[0].tool_name == "get_position"


async def test_control_flow_exception_not_recorded(engine, session_with_row):
    """ModelRetry / ApprovalRequired etc. don't record metrics rows, but still raise."""
    from pydantic_ai.exceptions import ModelRetry
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        raise ModelRetry("agent should retry")

    with pytest.raises(ModelRetry):
        await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("open_position"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 0, "control-flow signals must not write metrics rows"


async def test_recorder_does_not_break_tool_on_db_failure(
    engine, session_with_row, caplog, monkeypatch
):
    """If DB commit fails, tool return value still propagates; log.error fires."""
    from src.services.tool_call_recorder import ToolCallRecorder
    from src.services import tool_call_recorder as rec_module
    import logging

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    # Monkey-patch get_session in the recorder module to raise on __aenter__
    class FailingCtxManager:
        async def __aenter__(self):
            raise RuntimeError("simulated DB unavailable")
        async def __aexit__(self, *args):
            return False

    def failing_get_session(_engine):
        return FailingCtxManager()

    monkeypatch.setattr(rec_module, "get_session", failing_get_session)

    async def handler(args):
        return "tool OK"

    with caplog.at_level(logging.ERROR):
        result = await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("get_market_data"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    assert result == "tool OK", "tool return must not be blocked by metrics failure"
    assert any(
        "tool_call_recorder failed" in rec.message
        for rec in caplog.records
    ), "log.error must fire on recorder failure"


async def test_recorder_raises_runtime_error_when_cycle_id_missing(
    engine, session_with_row, caplog
):
    """If deps.cycle_id is None, RuntimeError raised inside finally, caught + logged."""
    from src.services.tool_call_recorder import ToolCallRecorder
    import logging

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row, cycle_id=None)  # missing!

    async def handler(args):
        return "tool OK"

    with caplog.at_level(logging.ERROR):
        result = await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("get_market_data"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    assert result == "tool OK", "tool return must not be blocked"
    # Outer except catches RuntimeError and logs
    assert any("cycle_id must be set" in rec.message for rec in caplog.records)


async def test_recorder_raises_runtime_error_when_db_engine_missing(
    engine, session_with_row, caplog
):
    """If deps.db_engine is None, RuntimeError raised, caught + logged."""
    from src.services.tool_call_recorder import ToolCallRecorder
    import logging

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.db_engine = None  # simulate missing engine

    async def handler(args):
        return "tool OK"

    with caplog.at_level(logging.ERROR):
        result = await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("get_position"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    assert result == "tool OK"
    assert any("db_engine must be set" in rec.message for rec in caplog.records)


async def test_duration_ms_monotonic(engine, session_with_row):
    """duration_ms derived from time.monotonic(), >= 0, reasonable magnitude."""
    import asyncio
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        await asyncio.sleep(0.05)  # 50ms
        return "ok"

    await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("get_market_data"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert 40 <= rows[0].duration_ms <= 200, \
        f"duration_ms={rows[0].duration_ms} outside plausible band for 50ms sleep"
