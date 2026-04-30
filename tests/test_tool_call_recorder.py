"""Unit tests for ToolCallRecorder capability."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from unittest.mock import MagicMock, AsyncMock

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, ToolCall

# R2-4 polish §I3 — drift guards 锚 __file__ 而非 cwd（与 test_alembic_migration.py:23 一致）
_REPO_ROOT = Path(__file__).resolve().parents[1]


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
    call.args_as_dict = MagicMock(return_value={})
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


# ---------------------------------------------------------------------------
# R2-4 T2: ContextVar Hook + BIZ_ERROR_TYPES + Recorder改造
# spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §4
# ---------------------------------------------------------------------------


async def test_records_biz_error_when_note_biz_error_called(engine, session_with_row):
    """工具内 note_biz_error → tool_calls.status='biz_error', error_type=<type>。"""
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        note_biz_error("invalid_threshold_range")
        return "Invalid threshold_pct: must be 0.1-50.0, got 0.05"

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("set_price_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    # LLM 看到的字符串不变（fact 透明）
    assert result == "Invalid threshold_pct: must be 0.1-50.0, got 0.05"

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_threshold_range"


async def test_biz_error_does_not_leak_across_calls(engine, session_with_row):
    """call A note_biz_error 后，call B 不 note → call B 仍 status='ok' (ContextVar reset)。"""
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler_a(args):
        note_biz_error("invalid_threshold_range")
        return "fail string"

    async def handler_b(args):
        return "success string"

    await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("tool_a"),
        tool_def=MagicMock(),
        args={},
        handler=handler_a,
    )
    await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("tool_b"),
        tool_def=MagicMock(),
        args={},
        handler=handler_b,
    )

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall).order_by(ToolCall.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_threshold_range"
    assert rows[1].status == "ok", \
        f"ContextVar 应在 wrap_tool_execute 入口 reset；call B 不应继承 call A 的 biz_error"
    assert rows[1].error_type is None


async def test_exception_overrides_biz_error(engine, session_with_row):
    """工具同时 note_biz_error 又抛 ValueError → status='error', error_type='ValueError'（exception 优先）。"""
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        note_biz_error("invalid_threshold_range")
        raise ValueError("unexpected boom after note")

    with pytest.raises(ValueError, match="unexpected boom"):
        await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("buggy_tool"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error_type == "ValueError"


async def test_note_biz_error_unknown_type_logs_and_skips(engine, session_with_row, caplog):
    """fail-soft: 拼错 → logger.error 调用 + 不 set ContextVar；后续写 status='ok'（spec §4.2）。"""
    import logging
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        note_biz_error("typo_xxx")  # 不在 BIZ_ERROR_TYPES
        return "tool returned ok"

    with caplog.at_level(logging.ERROR, logger="src.services.tool_call_recorder"):
        result = await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("any_tool"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    assert result == "tool returned ok"
    assert any("typo_xxx" in rec.message for rec in caplog.records), \
        f"应 logger.error 含拼错的 type；实际 records: {[r.message for r in caplog.records]}"

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok", \
        "拼错应 fail-soft，ContextVar 不被 set，本次 tool call 仍记 'ok'"


async def test_control_flow_exception_skips_biz_error_recording(engine, session_with_row):
    """工具 note_biz_error + raise ApprovalRequired → 不写库（控制流路径优先 skip_record）。"""
    from pydantic_ai.exceptions import ApprovalRequired
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        note_biz_error("invalid_threshold_range")
        raise ApprovalRequired()  # pydantic_ai 1.78: __init__(self, metadata: dict|None=None)

    with pytest.raises(ApprovalRequired):
        await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("any_tool"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 0, "控制流异常应 skip_record，不写 tool_calls"


def test_biz_error_types_drift_guard():
    """BIZ_ERROR_TYPES 集合 vs `note_biz_error("...")` 字面引用一致。
    扫 src/agent/tools_execution.py 内所有 note_biz_error 调用，断言 string literal 全部 ∈ BIZ_ERROR_TYPES。
    """
    import re
    from src.services.tool_call_recorder import BIZ_ERROR_TYPES

    src = (_REPO_ROOT / "src/agent/tools_execution.py").read_text()
    pattern = re.compile(r'note_biz_error\(["\']([a-z_]+)["\']\)')
    cited = set(pattern.findall(src))

    drift = cited - BIZ_ERROR_TYPES
    assert not drift, \
        f"tools_execution.py 引用未注册的 biz error type: {drift}（请在 BIZ_ERROR_TYPES 注册或更正字面量）"

    # Sanity: R2-4 应 instrument ≥ 3 处（spec §4.3）
    assert len(cited) >= 3, \
        f"R2-4 应 instrument ≥3 处 note_biz_error；实测 {len(cited)} 处: {cited}"


def test_tool_calls_status_values_fit_column():
    """G7 (R2-4 spec §7.2): tool_calls.status 应用层 enum 取值 ⊆ String(20)。"""
    enum_values = {"ok", "biz_error", "error"}
    over_limit = [v for v in enum_values if len(v) > 20]
    assert not over_limit, f"status enum > 20 chars: {over_limit}"
