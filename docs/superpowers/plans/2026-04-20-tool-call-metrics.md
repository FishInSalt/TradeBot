# Tool Call Metrics Enabler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship observation-period tool-call metrics infrastructure (B-tier: tool_name + session_id + cycle_id + status + duration_ms + error_type) without modifying any of the 26 existing `@agent.tool` registrations.

**Architecture:** Inject a `ToolCallRecorder` pydantic_ai `AbstractCapability` at agent creation; capability wraps every tool execution, reads `ctx.deps.db_engine` / `ctx.deps.cycle_id` / `ctx.deps.session_id` (all populated by the scheduler before `agent.run()`), writes one row per call to a new `tool_calls` table. Read side: `MetricsService.get_tool_call_summary()` for aggregation + thin `scripts/tool_call_summary.py` for CLI-style queries.

**Tech Stack:** Python 3.12+ / pydantic-ai 1.78.0 / SQLAlchemy 2.0 async + SQLite(WAL) / pytest + pytest-asyncio / pytest-mock.

**Spec reference:** `docs/superpowers/specs/2026-04-20-tool-call-metrics-design.md` (780 lines, commit `acbbbb3`, 9 independent review rounds passed).

---

## Task 0: Pre-flight Validation

**Purpose:** Before writing any code, verify two spec assumptions empirically in the real project environment. Both failures here would invalidate the design.

**Files:** None (verification only).

### Step 0.1: Verify recorder import strategy works in real project

- [ ] **Sub-step 0.1.1: Create ad-hoc test file**

Create `/tmp/iter1_import_smoke.py`:

```python
"""Minimal reproducer mirroring the final recorder structure in real project ctx.

Validates: TYPE_CHECKING + string forward-ref in recorder + top-level import
from trader does NOT circular-import under `from __future__ import annotations`.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/Users/z/Z/TradeBot")

from src.agent.trader import create_trader_agent, TradingDeps  # noqa: E402
from src.config import PersonaConfig  # noqa: E402

agent = create_trader_agent(model="test", persona_config=PersonaConfig())
print(f"agent type: {type(agent).__name__}")
print(f"tools count: {len(agent._function_toolset.tools)}")
```

- [ ] **Sub-step 0.1.2: Run pre-implementation smoke**

Run: `cd /Users/z/Z/TradeBot && uv run python /tmp/iter1_import_smoke.py`
Expected: `agent type: Agent`, `tools count: 26`
Purpose: baseline — confirms the current codebase imports cleanly before any changes.

- [ ] **Sub-step 0.1.3: Delete scratch file**

Run: `rm /tmp/iter1_import_smoke.py`

### Step 0.2: Grep for public alternatives to `_function_toolset.tools`

- [ ] **Sub-step 0.2.1: Search for public tool accessors in pydantic_ai**

Run:
```bash
grep -rn "def tools\b\|@property\s*$\|def get_tools\b" \
  /Users/z/Z/TradeBot/.venv/lib/python3.13/site-packages/pydantic_ai/agent/ \
  | grep -v "^Binary\|test_\|\.pyc" | head -30
```

- [ ] **Sub-step 0.2.2: Document finding inline**

Open `docs/superpowers/specs/2026-04-20-tool-call-metrics-design.md` §4.3 and append one sentence at the end of the 漂移防护 paragraph:

- If a public accessor exists: note the public path and switch the drift test to use it (update Task 5.3 in this plan accordingly).
- If none found: add `"plan-stage grep (YYYY-MM-DD) confirmed no public accessor; `_function_toolset.tools` remains the only path, fragility accepted."`

Expected outcome: **Most likely no public accessor** exists in 1.78 (last review agreed). This step is defensive — if one appears, we catch it before writing brittle code.

- [ ] **Sub-step 0.2.3: Commit pre-flight findings (if spec was edited)**

```bash
git add docs/superpowers/specs/2026-04-20-tool-call-metrics-design.md
git commit -m "docs: record plan-stage pre-flight findings for tool-call metrics"
```

If no spec edits were needed, skip this commit.

---

## Task 1: Add `ToolCall` Model to Storage

**Files:**
- Modify: `src/storage/models.py` (append new class at end)
- Test: `tests/test_storage.py` (extend existing or new test cases)

### Step 1.1: Write failing test for ToolCall model creation

- [ ] **Sub-step 1.1.1: Add test**

Open `tests/test_storage.py` and append:

```python
async def test_tool_call_model_create():
    """Verify ToolCall model can be inserted and queried with required fields."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from datetime import datetime, timezone

    engine = await init_db("sqlite+aiosqlite:///:memory:")

    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="test-session"))
        await db.commit()

    async with get_session(engine) as db:
        db.add(ToolCall(
            session_id="s1",
            cycle_id="cyc12345",
            tool_name="get_market_data",
            status="ok",
            duration_ms=250,
            error_type=None,
        ))
        await db.commit()

    from sqlalchemy import select
    async with get_session(engine) as db:
        result = await db.execute(select(ToolCall))
        rows = result.scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.session_id == "s1"
        assert row.cycle_id == "cyc12345"
        assert row.tool_name == "get_market_data"
        assert row.status == "ok"
        assert row.duration_ms == 250
        assert row.error_type is None
        assert isinstance(row.created_at, datetime)
        assert row.created_at.tzinfo is not None


async def test_tool_call_cycle_id_not_null():
    """cycle_id is NOT NULL — insert without it should fail."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from sqlalchemy.exc import IntegrityError

    engine = await init_db("sqlite+aiosqlite:///:memory:")

    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="t"))
        await db.commit()

    async with get_session(engine) as db:
        db.add(ToolCall(
            session_id="s1",
            cycle_id=None,  # type: ignore[arg-type]
            tool_name="x",
            status="ok",
            duration_ms=1,
        ))
        with pytest.raises(IntegrityError):
            await db.commit()
```

- [ ] **Sub-step 1.1.2: Run test, verify it fails**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_storage.py::test_tool_call_model_create -v`
Expected: FAIL with `ImportError: cannot import name 'ToolCall' from 'src.storage.models'`

### Step 1.2: Implement ToolCall model

- [ ] **Sub-step 1.2.1: Add ToolCall class**

Open `src/storage/models.py` and append at the end of the file:

```python
class ToolCall(Base):
    """每次 agent tool 调用一行（观察期埋点）。Append-only，无 UPDATE/DELETE 接口。"""

    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_session_tool_time", "session_id", "tool_name", "created_at"),
        Index("ix_tool_calls_cycle", "cycle_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"))
    # cycle_id: 应用层软关联 DecisionLog.cycle_id（不声明 DB FK —— 时序不允许）
    # NOT NULL: 运行时所有 tool 调用都在 run_agent_cycle 内，cycle_id 必有值
    cycle_id: Mapped[str] = mapped_column(String(50), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10))  # "ok" / "error"
    duration_ms: Mapped[int] = mapped_column(Integer)
    # error_type 存异常类名（非 message / traceback），避免敏感数据泄露
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Sub-step 1.2.2: Run tests, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_storage.py::test_tool_call_model_create tests/test_storage.py::test_tool_call_cycle_id_not_null -v`
Expected: PASS (both)

- [ ] **Sub-step 1.2.3: Full regression**

Run: `cd /Users/z/Z/TradeBot && uv run pytest -x`
Expected: 666 passed (664 + 2 new). No failures.

### Step 1.3: Commit

- [ ] **Sub-step 1.3.1: Commit**

```bash
git add src/storage/models.py tests/test_storage.py
git commit -m "feat(storage): add tool_calls table for observation-period metrics"
```

---

## Task 2: Tighten `TradingDeps.db_engine` + Add `cycle_id` Field

**Files:**
- Modify: `src/agent/trader.py:1-37` (imports + TradingDeps dataclass)
- Test: `tests/test_trader_agent.py` (existing `test_trading_deps_creation` will exercise new field)

### Step 2.1: Verify current regression baseline

- [ ] **Sub-step 2.1.1: Baseline run**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_trader_agent.py -v`
Expected: all current tests PASS (2 tests: `test_create_trader_agent`, `test_trader_agent_has_all_tools`, plus `test_trading_deps_creation` if present).

### Step 2.2: Tighten db_engine type

- [ ] **Sub-step 2.2.1: Add AsyncEngine import**

Open `src/agent/trader.py`, find the top imports block. Add after line 7 (`from pydantic_ai import Agent, RunContext`):

```python
from sqlalchemy.ext.asyncio import AsyncEngine
```

- [ ] **Sub-step 2.2.2: Update db_engine type annotation**

In `src/agent/trader.py` line 26, change:

```python
    db_engine: object | None = None  # AsyncEngine, typed as object to avoid circular import
```

to:

```python
    db_engine: AsyncEngine | None = None
```

The obsolete "circular import" comment is removed rather than reworded — the historical debt explanation belongs in git history, not inline (spec §3.2 and the tradingdeps-typing-cleanup follow-up memory carry the context).

- [ ] **Sub-step 2.2.3: Add cycle_id field**

In `src/agent/trader.py`, find the TradingDeps dataclass definition (ends at line 37 with `onchain: object | None = None`). Add **at the very end** (after `onchain`):

```python
    cycle_id: str | None = None  # Mutated by run_agent_cycle before agent.run(); see §3.3 of spec
```

- [ ] **Sub-step 2.2.4: Run regression**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_trader_agent.py -v`
Expected: all 2-3 existing tests PASS. New field has default `None`, does not break existing kwargs-based constructions.

- [ ] **Sub-step 2.2.5: Full regression**

Run: `cd /Users/z/Z/TradeBot && uv run pytest -x`
Expected: 666 passed.

### Step 2.3: Commit

- [ ] **Sub-step 2.3.1: Commit**

```bash
git add src/agent/trader.py
git commit -m "refactor(agent): tighten TradingDeps.db_engine type, add cycle_id field

- db_engine: object | None -> AsyncEngine | None (from __future__ defers eval)
- cycle_id: str | None (mutated per cycle by scheduler)

Zero behavior change; preserves all 3 existing TradingDeps(...) kwargs call
sites."
```

---

## Task 3: Implement `ToolCallRecorder` Capability (TDD)

**Files:**
- Create: `src/services/tool_call_recorder.py`
- Create: `tests/test_tool_call_recorder.py`

### Step 3.1: Scaffold test file with fixtures

- [ ] **Sub-step 3.1.1: Create test file with shared fixtures**

Create `tests/test_tool_call_recorder.py`:

```python
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
```

- [ ] **Sub-step 3.1.2: Verify fixtures load without error**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_recorder.py -v --collect-only`
Expected: No tests collected yet, no collection errors.

### Step 3.2: Test 1 — records successful tool call

- [ ] **Sub-step 3.2.1: Add failing test**

Append to `tests/test_tool_call_recorder.py`:

```python
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
```

- [ ] **Sub-step 3.2.2: Run, verify fail**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_recorder.py::test_records_successful_tool_call -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.tool_call_recorder'`

### Step 3.3: Implement recorder skeleton

- [ ] **Sub-step 3.3.1: Create recorder file**

Create `src/services/tool_call_recorder.py`:

```python
"""Tool-call metrics recorder — pydantic_ai capability for observation-period埋点.

See docs/superpowers/specs/2026-04-20-tool-call-metrics-design.md §3.1 for design.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
)
from pydantic_ai.exceptions import (
    ApprovalRequired,
    CallDeferred,
    ModelRetry,
    SkipToolExecution,
    ToolRetryError,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from src.storage.database import get_session
from src.storage.models import ToolCall

if TYPE_CHECKING:
    # 避免 trader.py ↔ tool_call_recorder.py 循环 import
    # (create_trader_agent() 内部函数级懒加载本模块，见 trader.py)
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)

# pydantic_ai 控制流信号 — retry / approval / deferral，不是真错，也不是 ok。
# 直通不记 metrics 行，否则未来启用 approval / retry flow 时产生假阳性 error。
_CONTROL_FLOW_EXCEPTIONS = (
    ApprovalRequired,
    CallDeferred,
    ModelRetry,
    SkipToolExecution,
    ToolRetryError,
)


@dataclass
class ToolCallRecorder(AbstractCapability["TradingDeps"]):  # 字符串前向引用
    """从 ctx.deps.db_engine 读 engine; recorder 本身无字段。

    依赖 pydantic_ai 契约 (v1.78 已验证): capability 收到的 ctx.deps 即
    agent.run(deps=...) 传入的对象。集成测试隐式验证。
    """

    async def wrap_tool_execute(
        self,
        ctx: RunContext[TradingDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        start = time.monotonic()
        status, error_type = "ok", None
        skip_record = False
        try:
            return await handler(args)
        except _CONTROL_FLOW_EXCEPTIONS:
            skip_record = True  # 控制流信号直通
            raise
        except Exception as e:
            status, error_type = "error", type(e).__name__
            raise
        finally:
            if not skip_record:
                try:
                    duration_ms = int((time.monotonic() - start) * 1000)
                    if ctx.deps.cycle_id is None:
                        raise RuntimeError(
                            "cycle_id must be set on TradingDeps before tool call"
                        )
                    if ctx.deps.db_engine is None:
                        raise RuntimeError(
                            "db_engine must be set on TradingDeps"
                        )
                    insert_start = time.monotonic()
                    async with get_session(ctx.deps.db_engine) as session:
                        session.add(ToolCall(
                            session_id=ctx.deps.session_id,
                            cycle_id=ctx.deps.cycle_id,
                            tool_name=call.tool_name,
                            status=status,
                            duration_ms=duration_ms,
                            error_type=error_type,
                        ))
                        await session.commit()
                    insert_ms = (time.monotonic() - insert_start) * 1000
                    logger.debug(
                        "tool_call_insert_ms=%.1f tool=%s", insert_ms, call.tool_name
                    )
                except Exception as rec_err:
                    logger.error(
                        "tool_call_recorder failed for %s: %s",
                        call.tool_name, rec_err,
                    )
```

- [ ] **Sub-step 3.3.2: Run test 1, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_recorder.py::test_records_successful_tool_call -v`
Expected: PASS

### Step 3.4: Test 2 — records failed tool call (exception re-raised)

- [ ] **Sub-step 3.4.1: Add test**

Append to `tests/test_tool_call_recorder.py`:

```python
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
```

- [ ] **Sub-step 3.4.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_recorder.py::test_records_failed_tool_call -v`
Expected: PASS (recorder already handles this case per Step 3.3 implementation)

### Step 3.5: Test 3 — control-flow exception does not write row

- [ ] **Sub-step 3.5.1: Add test**

Append to `tests/test_tool_call_recorder.py`:

```python
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
```

- [ ] **Sub-step 3.5.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_recorder.py::test_control_flow_exception_not_recorded -v`
Expected: PASS

### Step 3.6: Test 4 — recorder does not break tool on DB failure

- [ ] **Sub-step 3.6.1: Add test**

Append to `tests/test_tool_call_recorder.py`:

```python
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
```

- [ ] **Sub-step 3.6.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_recorder.py::test_recorder_does_not_break_tool_on_db_failure -v`
Expected: PASS

### Step 3.7: Test 5 — RuntimeError on missing cycle_id / db_engine

- [ ] **Sub-step 3.7.1: Add test**

Append to `tests/test_tool_call_recorder.py`:

```python
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
```

- [ ] **Sub-step 3.7.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_recorder.py::test_recorder_raises_runtime_error_when_cycle_id_missing tests/test_tool_call_recorder.py::test_recorder_raises_runtime_error_when_db_engine_missing -v`
Expected: PASS (both)

### Step 3.8: Test 6 — duration_ms is monotonic and non-negative

- [ ] **Sub-step 3.8.1: Add test**

Append to `tests/test_tool_call_recorder.py`:

```python
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
```

- [ ] **Sub-step 3.8.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_recorder.py::test_duration_ms_monotonic -v`
Expected: PASS

### Step 3.9: Full recorder test run + regression

- [ ] **Sub-step 3.9.1: Run all recorder tests**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_recorder.py -v`
Expected: 7 PASS:
- `test_records_successful_tool_call`
- `test_records_failed_tool_call`
- `test_control_flow_exception_not_recorded`
- `test_recorder_does_not_break_tool_on_db_failure`
- `test_recorder_raises_runtime_error_when_cycle_id_missing`
- `test_recorder_raises_runtime_error_when_db_engine_missing`
- `test_duration_ms_monotonic`

- [ ] **Sub-step 3.9.2: Full regression**

Run: `cd /Users/z/Z/TradeBot && uv run pytest -x`
Expected: 673 passed (664 + 2 from Task 1 + 7 from Task 3).

### Step 3.10: Commit

- [ ] **Sub-step 3.10.1: Commit**

```bash
git add src/services/tool_call_recorder.py tests/test_tool_call_recorder.py
git commit -m "feat(services): add ToolCallRecorder capability with 7 unit tests

- Wraps wrap_tool_execute; records (session_id, cycle_id, tool_name,
  status, duration_ms, error_type) per tool call
- Whitelists 5 pydantic_ai control-flow exceptions (ModelRetry etc.)
  to avoid false-positive error classification
- Uses explicit raise RuntimeError (not assert — survives python -O)
- Outer except catches all metrics-side failures → log.error without
  blocking tool return to agent
- Extra logger.debug insert_ms timer for write-latency observability"
```

---

## Task 4: Integrate Recorder into `create_trader_agent`

**Files:**
- Modify: `src/agent/trader.py:40-300` (create_trader_agent function body)

### Step 4.1: Add capability wiring (function-level lazy import)

- [ ] **Sub-step 4.1.1: Update create_trader_agent**

Open `src/agent/trader.py` line 40-44. Change:

```python
def create_trader_agent(
    model: str, persona_config: PersonaConfig
) -> Agent[TradingDeps, str]:
    system_prompt = generate_system_prompt(persona_config)
    agent = Agent(model, deps_type=TradingDeps, output_type=str, instructions=system_prompt)
```

to:

```python
def create_trader_agent(
    model: str, persona_config: PersonaConfig
) -> Agent[TradingDeps, str]:
    # 函数级懒加载 — 与现有 26 个 tool 的懒加载风格一致（技术上非必需：
    # recorder 侧 TYPE_CHECKING + 字符串前向引用已足以破环）
    from src.services.tool_call_recorder import ToolCallRecorder

    system_prompt = generate_system_prompt(persona_config)
    agent = Agent(
        model,
        deps_type=TradingDeps,
        output_type=str,
        instructions=system_prompt,
        capabilities=[ToolCallRecorder()],
    )
```

### Step 4.2: Verify existing tests still pass

- [ ] **Sub-step 4.2.1: Test trader agent**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_trader_agent.py -v`
Expected: all existing tests PASS (no recorder-specific assertion yet; those come in Task 8 integration test).

- [ ] **Sub-step 4.2.2: Full regression**

Run: `cd /Users/z/Z/TradeBot && uv run pytest -x`
Expected: 673 passed.

### Step 4.3: Commit

- [ ] **Sub-step 4.3.1: Commit**

```bash
git add src/agent/trader.py
git commit -m "feat(agent): wire ToolCallRecorder capability into create_trader_agent

Signature unchanged; recorder reads ctx.deps.db_engine at runtime
(populated by scheduler before agent.run()). Function-level lazy
import matches existing 26-tool pattern."
```

---

## Task 5: `REGISTERED_TOOL_NAMES` Constant + Drift Test

**Files:**
- Modify: `src/agent/trader.py` (append constant at end of module)
- Modify: `tests/test_trader_agent.py` (add drift test)

### Step 5.1: Add module-level constant

- [ ] **Sub-step 5.1.1: Append REGISTERED_TOOL_NAMES**

Open `src/agent/trader.py`. After the end of `create_trader_agent` function (after `return agent`), append:

```python


# REGISTERED_TOOL_NAMES: 与 `@agent.tool` 装饰顺序保持一致（感知 → 执行 → memory）。
# 供 scheduler 日志、scripts/tool_call_summary.py 脚本、漂移防护测试统一引用。
# 漂移防护：tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools
# 用 agent._function_toolset.tools 对照本常量。加新 tool 必须同时更新此列表。
REGISTERED_TOOL_NAMES: list[str] = [
    # --- 感知 (15) ---
    "get_market_data",
    "get_position",
    "get_account_balance",
    "get_open_orders",
    "get_trade_journal",
    "get_memories",
    "get_active_alerts",
    "get_performance",
    "get_market_news",
    "get_critical_alerts",
    "get_derivatives_data",
    "get_higher_timeframe_view",
    "get_macro_context",
    "get_etf_flows",
    "get_stablecoin_supply",
    # --- 执行 (10) ---
    "open_position",
    "close_position",
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "set_price_alert",
    "cancel_order",
    "add_price_level_alert",
    "set_next_wake",
    "place_limit_order",
    # --- memory (1) ---
    "save_memory",
]
```

### Step 5.2: Write drift test

- [ ] **Sub-step 5.2.1: Add failing test**

Open `tests/test_trader_agent.py`. Append:

```python
def test_registered_tool_names_matches_agent_tools():
    """Drift防护: REGISTERED_TOOL_NAMES 与 create_trader_agent 实际注册的
    tool 一一对应。加 tool 忘更新常量会导致 scripts/tool_call_summary.py
    从'零调用'表静默丢工具 → 本测试立即暴露。"""
    from src.agent.trader import REGISTERED_TOOL_NAMES, create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    actual = set(agent._function_toolset.tools)
    declared = set(REGISTERED_TOOL_NAMES)

    assert actual == declared, (
        f"Drift detected:\n"
        f"  In agent but not in REGISTERED_TOOL_NAMES: {actual - declared}\n"
        f"  In REGISTERED_TOOL_NAMES but not in agent: {declared - actual}"
    )
    assert len(REGISTERED_TOOL_NAMES) == 26, (
        f"Expected 26 tools (15+10+1), got {len(REGISTERED_TOOL_NAMES)}"
    )
    # 无重复
    assert len(REGISTERED_TOOL_NAMES) == len(set(REGISTERED_TOOL_NAMES)), \
        "REGISTERED_TOOL_NAMES contains duplicates"
```

- [ ] **Sub-step 5.2.2: Run test, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools -v`
Expected: PASS (tool list + actual registrations should align).

- [ ] **Sub-step 5.2.3: Full regression**

Run: `cd /Users/z/Z/TradeBot && uv run pytest -x`
Expected: 674 passed.

### Step 5.3: Commit

- [ ] **Sub-step 5.3.1: Commit**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "feat(agent): export REGISTERED_TOOL_NAMES + drift防护 test

Constant list consumed by scripts/tool_call_summary.py to display
'zero-call' tools; drift test ensures adding new @agent.tool without
updating the constant fails CI rather than silently dropping tools."
```

---

## Task 6: Mutate `deps.cycle_id` Before `agent.run()`

**Files:**
- Modify: `src/cli/app.py:100-105` (run_agent_cycle beginning)

### Step 6.1: Add mutation

- [ ] **Sub-step 6.1.1: Locate cycle_id generation**

Open `src/cli/app.py` at line 102:

```python
    cycle_id = str(uuid.uuid4())[:8]
    prompt = (
        f"You have been woken up by a {trigger_type} trigger.\n"
        ...
```

- [ ] **Sub-step 6.1.2: Insert deps.cycle_id assignment**

Change line 102-103 from:

```python
    cycle_id = str(uuid.uuid4())[:8]
    prompt = (
```

to:

```python
    cycle_id = str(uuid.uuid4())[:8]
    deps.cycle_id = cycle_id   # propagate to ToolCallRecorder via ctx.deps (§3.4 of spec)
    prompt = (
```

### Step 6.2: Verify regression

- [ ] **Sub-step 6.2.1: Run app lifecycle tests**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_app_lifecycle_n3.py -v`
Expected: PASS (no behavior change — field previously None now gets set but unused prior to integration test).

- [ ] **Sub-step 6.2.2: Full regression**

Run: `cd /Users/z/Z/TradeBot && uv run pytest -x`
Expected: 674 passed.

### Step 6.3: Commit

- [ ] **Sub-step 6.3.1: Commit**

```bash
git add src/cli/app.py
git commit -m "feat(cli): propagate cycle_id to TradingDeps before agent.run()

ToolCallRecorder reads ctx.deps.cycle_id to tag each tool_calls row.
Scheduler串行 cycle invariant (scheduler.py single-coroutine await
pattern) ensures no race. See spec §3.4."
```

---

## Task 7: Extend `MetricsService` with `get_tool_call_summary` (TDD)

**Files:**
- Modify: `src/services/metrics.py` (append `ToolCallStats` + method)
- Modify: `tests/test_metrics.py` (append 6 tests)

### Step 7.1: Baseline run

- [ ] **Sub-step 7.1.1: Run existing metrics tests**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_metrics.py -v`
Expected: all current tests PASS.

### Step 7.2: Test 1 — empty returns empty dict

- [ ] **Sub-step 7.2.1: Add test**

Open `tests/test_metrics.py`. Append at end:

```python
# --- Tool-call summary tests ---

async def test_tool_call_summary_empty():
    """No tool_calls rows → empty dict."""
    from datetime import timedelta
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()

    ms = MetricsService(engine, session_id="s1")
    summary = await ms.get_tool_call_summary()
    assert summary == {}
```

- [ ] **Sub-step 7.2.2: Run, verify fail**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_metrics.py::test_tool_call_summary_empty -v`
Expected: FAIL — `AttributeError: 'MetricsService' object has no attribute 'get_tool_call_summary'`

### Step 7.3: Implement minimal `get_tool_call_summary` (surgical edits)

**Why surgical edits instead of replacing the file**: the existing `compute()` method must not be disturbed; replacing the whole file risks clobbering concurrent hotfixes on main. Use 3 targeted `Edit` calls below.

- [ ] **Sub-step 7.3.1: Extend imports**

Use `Edit` on `src/services/metrics.py` to replace the import block (lines 1-10):

**old_string**:
```python
# src/services/metrics.py
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.models import TradeAction
```

**new_string**:
```python
# src/services/metrics.py
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.models import ToolCall, TradeAction
```

- [ ] **Sub-step 7.3.2: Append `ToolCallStats` dataclass after `PerformanceMetrics`**

Use `Edit` on `src/services/metrics.py`. Find the end of `PerformanceMetrics` class definition (ends around line 29 with `total_fees: float = 0.0`), and insert `ToolCallStats` between `PerformanceMetrics` and `class MetricsService:`:

**old_string**:
```python
    recent_summary: str = ""
    total_fees: float = 0.0


class MetricsService:
```

**new_string**:
```python
    recent_summary: str = ""
    total_fees: float = 0.0


@dataclass
class ToolCallStats:
    count: int                            # count >= 1 (zero-call tools not in dict)
    ok_count: int
    error_count: int
    error_rate: float                     # 0..1 ratio; script layer multiplies by 100 for %
    p50_duration_ms: int
    p95_duration_ms: int
    error_breakdown: dict[str, int]       # {"TimeoutError": 3, ...}
    last_called_at: datetime              # MAX(created_at); always has value for tools in dict


class MetricsService:
```

- [ ] **Sub-step 7.3.3: Append `get_tool_call_summary` method to `MetricsService`**

Find the end of the file (last line of `compute()` return statement). Use `Edit` to append new method after the existing `compute()` method:

**old_string** (last few lines of `compute()`):
```python
            recent_summary=recent_summary,
            total_fees=total_fees,
        )
```

**new_string**:
```python
            recent_summary=recent_summary,
            total_fees=total_fees,
        )

    async def get_tool_call_summary(
        self,
        session_id: str | None = None,
        since: timedelta | None = None,
        tool_name: str | None = None,
    ) -> dict[str, ToolCallStats]:
        """聚合 tool_calls 按 tool_name。零调用工具不入 dict。

        Args:
            session_id: None = 跨所有 session 聚合；否则限定该 session
            since: None = 全部历史；否则限定 created_at > now - since
            tool_name: None = 所有工具；否则只返回该工具

        Returns:
            {tool_name: ToolCallStats}; ToolCallStats.count >= 1 by contract.
        """
        stmt = select(ToolCall)
        if session_id is not None:
            stmt = stmt.where(ToolCall.session_id == session_id)
        if since is not None:
            cutoff = datetime.now(timezone.utc) - since
            stmt = stmt.where(ToolCall.created_at > cutoff)
        if tool_name is not None:
            stmt = stmt.where(ToolCall.tool_name == tool_name)

        async with get_session(self._engine) as db:
            rows = (await db.execute(stmt)).scalars().all()

        # Group in-memory by tool_name
        by_tool: dict[str, list[ToolCall]] = {}
        for row in rows:
            by_tool.setdefault(row.tool_name, []).append(row)

        result: dict[str, ToolCallStats] = {}
        for name, tool_rows in by_tool.items():
            count = len(tool_rows)
            ok_count = sum(1 for r in tool_rows if r.status == "ok")
            error_count = count - ok_count
            durations = [r.duration_ms for r in tool_rows]
            # Python 3.13: quantiles handles N=1 (returns repeated single value).
            # `method='inclusive'` keeps p50/p95 bounded by sample max (see spec §4.2).
            q = statistics.quantiles(durations, n=100, method="inclusive")
            p50 = int(q[49])      # index 49 = 50th percentile; int() truncates per spec §4.2
            p95 = int(q[94])      # index 94 = 95th percentile
            error_breakdown: dict[str, int] = {}
            for r in tool_rows:
                if r.error_type is not None:
                    error_breakdown[r.error_type] = error_breakdown.get(r.error_type, 0) + 1
            last_called = max(r.created_at for r in tool_rows)

            result[name] = ToolCallStats(
                count=count,
                ok_count=ok_count,
                error_count=error_count,
                error_rate=error_count / count,
                p50_duration_ms=p50,
                p95_duration_ms=p95,
                error_breakdown=error_breakdown,
                last_called_at=last_called,
            )

        return result
```

- [ ] **Sub-step 7.3.4: Run empty test, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_metrics.py::test_tool_call_summary_empty -v`
Expected: PASS

### Step 7.4: Test 2 — aggregation correct (includes last_called_at)

- [ ] **Sub-step 7.4.1: Add test**

Append to `tests/test_metrics.py`:

```python
async def test_tool_call_summary_aggregation():
    """Multi-tool multi-call: counts, error rate, last_called_at all correct."""
    from datetime import datetime, timezone, timedelta
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()

    t0 = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)
    rows = [
        # 3 successful get_market_data calls
        ToolCall(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                 status="ok", duration_ms=100, created_at=t0),
        ToolCall(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                 status="ok", duration_ms=200, created_at=t0 + timedelta(seconds=1)),
        ToolCall(session_id="s1", cycle_id="c2", tool_name="get_market_data",
                 status="ok", duration_ms=300, created_at=t0 + timedelta(seconds=2)),
        # 1 failed get_position
        ToolCall(session_id="s1", cycle_id="c1", tool_name="get_position",
                 status="error", duration_ms=50, error_type="TimeoutError",
                 created_at=t0 + timedelta(seconds=3)),
    ]
    async with get_session(engine) as db:
        db.add_all(rows)
        await db.commit()

    ms = MetricsService(engine, session_id="s1")
    summary = await ms.get_tool_call_summary(session_id="s1")

    assert set(summary.keys()) == {"get_market_data", "get_position"}

    mkt = summary["get_market_data"]
    assert mkt.count == 3
    assert mkt.ok_count == 3
    assert mkt.error_count == 0
    assert mkt.error_rate == 0.0
    assert mkt.last_called_at == t0 + timedelta(seconds=2)
    assert mkt.error_breakdown == {}

    pos = summary["get_position"]
    assert pos.count == 1
    assert pos.error_count == 1
    assert pos.error_rate == 1.0
    assert pos.error_breakdown == {"TimeoutError": 1}
    assert pos.last_called_at == t0 + timedelta(seconds=3)
```

- [ ] **Sub-step 7.4.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_metrics.py::test_tool_call_summary_aggregation -v`
Expected: PASS

### Step 7.5: Test 3 — session_id filter

- [ ] **Sub-step 7.5.1: Add test**

Append to `tests/test_metrics.py`:

```python
async def test_tool_call_summary_filter_session():
    """session_id=None aggregates across sessions; specifying one filters correctly."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add_all([
            SessionModel(id="s1", name="n1"),
            SessionModel(id="s2", name="n2"),
        ])
        await db.commit()
        db.add_all([
            ToolCall(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                     status="ok", duration_ms=10),
            ToolCall(session_id="s2", cycle_id="c2", tool_name="get_market_data",
                     status="ok", duration_ms=20),
        ])
        await db.commit()

    ms = MetricsService(engine, session_id="s1")  # instance session_id unused here

    all_sessions = await ms.get_tool_call_summary()  # None → cross-session
    assert all_sessions["get_market_data"].count == 2

    only_s1 = await ms.get_tool_call_summary(session_id="s1")
    assert only_s1["get_market_data"].count == 1
```

- [ ] **Sub-step 7.5.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_metrics.py::test_tool_call_summary_filter_session -v`
Expected: PASS

### Step 7.6: Test 4 — since window filter

- [ ] **Sub-step 7.6.1: Add test**

Append to `tests/test_metrics.py`:

```python
async def test_tool_call_summary_filter_since():
    """since=timedelta limits to rows with created_at > now - since."""
    from datetime import datetime, timezone, timedelta
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()

    now = datetime.now(timezone.utc)
    async with get_session(engine) as db:
        db.add_all([
            ToolCall(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                     status="ok", duration_ms=10,
                     created_at=now - timedelta(days=2)),
            ToolCall(session_id="s1", cycle_id="c2", tool_name="get_market_data",
                     status="ok", duration_ms=10,
                     created_at=now - timedelta(minutes=5)),
        ])
        await db.commit()

    ms = MetricsService(engine, session_id="s1")

    last_hour = await ms.get_tool_call_summary(session_id="s1", since=timedelta(hours=1))
    assert last_hour["get_market_data"].count == 1

    last_week = await ms.get_tool_call_summary(session_id="s1", since=timedelta(days=7))
    assert last_week["get_market_data"].count == 2
```

- [ ] **Sub-step 7.6.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_metrics.py::test_tool_call_summary_filter_since -v`
Expected: PASS

### Step 7.7: Test 5 — error_breakdown by error_type

- [ ] **Sub-step 7.7.1: Add test**

Append to `tests/test_metrics.py`:

```python
async def test_tool_call_summary_error_breakdown():
    """error_breakdown counts by error_type."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()
        db.add_all([
            ToolCall(session_id="s1", cycle_id="c", tool_name="get_x",
                     status="error", duration_ms=5, error_type="TimeoutError"),
            ToolCall(session_id="s1", cycle_id="c", tool_name="get_x",
                     status="error", duration_ms=5, error_type="TimeoutError"),
            ToolCall(session_id="s1", cycle_id="c", tool_name="get_x",
                     status="error", duration_ms=5, error_type="HTTPStatusError"),
            ToolCall(session_id="s1", cycle_id="c", tool_name="get_x",
                     status="ok", duration_ms=5),
        ])
        await db.commit()

    ms = MetricsService(engine, session_id="s1")
    summary = await ms.get_tool_call_summary(session_id="s1")

    stats = summary["get_x"]
    assert stats.count == 4
    assert stats.error_count == 3
    assert stats.error_rate == 0.75
    assert stats.error_breakdown == {"TimeoutError": 2, "HTTPStatusError": 1}
```

- [ ] **Sub-step 7.7.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_metrics.py::test_tool_call_summary_error_breakdown -v`
Expected: PASS

### Step 7.8: Test 6 — percentiles (inclusive method, N=1 and N>=2)

- [ ] **Sub-step 7.8.1: Add test**

Append to `tests/test_metrics.py`:

```python
async def test_tool_call_summary_percentiles_inclusive():
    """p50/p95 use method='inclusive', bounded by sample max; N=1 and N>=2."""
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, ToolCall
    from src.services.metrics import MetricsService

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="s1", name="n"))
        await db.commit()
        # tool_a: N=1 (single call duration=500)
        db.add(ToolCall(session_id="s1", cycle_id="c", tool_name="tool_a",
                        status="ok", duration_ms=500))
        # tool_b: N=10, durations 10..100 ms
        for d in range(10, 101, 10):  # 10,20,...,100
            db.add(ToolCall(session_id="s1", cycle_id="c", tool_name="tool_b",
                            status="ok", duration_ms=d))
        await db.commit()

    ms = MetricsService(engine, session_id="s1")
    summary = await ms.get_tool_call_summary(session_id="s1")

    # N=1: p50=p95=single value
    assert summary["tool_a"].p50_duration_ms == 500
    assert summary["tool_a"].p95_duration_ms == 500

    # N=10 inclusive: p95 bounded by sample max (100)
    assert summary["tool_b"].p95_duration_ms <= 100
    assert summary["tool_b"].p50_duration_ms <= 100
    # Sanity: p50 somewhere middle, p95 near high end
    assert 40 <= summary["tool_b"].p50_duration_ms <= 60
    assert 80 <= summary["tool_b"].p95_duration_ms <= 100
```

- [ ] **Sub-step 7.8.2: Run, verify pass**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_metrics.py::test_tool_call_summary_percentiles_inclusive -v`
Expected: PASS

### Step 7.9: Full regression

- [ ] **Sub-step 7.9.1: All metrics tests**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_metrics.py -v`
Expected: all existing tests + 6 new PASS.

- [ ] **Sub-step 7.9.2: Full suite**

Run: `cd /Users/z/Z/TradeBot && uv run pytest -x`
Expected: 680 passed (674 + 6 new).

### Step 7.10: Commit

- [ ] **Sub-step 7.10.1: Commit**

```bash
git add src/services/metrics.py tests/test_metrics.py
git commit -m "feat(services): MetricsService.get_tool_call_summary + 6 tests

Aggregates tool_calls by tool_name with count/error_rate/p50/p95/
error_breakdown/last_called_at. Accepts session_id=None for cross-
session comparison (e.g. BTC vs ETH session patterns).

Uses statistics.quantiles(method='inclusive') to keep p50/p95 bounded
by sample max (default exclusive extrapolates for small N, producing
p95 > max which confuses observation-period readers)."
```

---

## Task 8: End-to-end Integration Test

**Files:**
- Create: `tests/test_tool_call_instrumentation.py`

### Step 8.1: Write integration test with spy

- [ ] **Sub-step 8.1.1: Create test file**

Create `tests/test_tool_call_instrumentation.py`:

```python
"""End-to-end: agent.run() with real ToolCallRecorder writes rows to DB.

Also explicitly asserts wrap_tool_execute was actually invoked (guards against
stub/mock setups that don't exercise the capability chain — false-green risk).
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock

from pydantic_ai import Agent, models
from pydantic_ai.models.test import TestModel
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, ToolCall

# pydantic_ai safeguard: prevent real model calls in tests
models.ALLOW_MODEL_REQUESTS = False


async def test_agent_run_writes_tool_call_rows(monkeypatch):
    """Real agent.run() with TestModel stub → ToolCallRecorder wraps + writes rows.

    **Test scope**: verifies the wire-up, NOT tool happy-path semantics.
    Real tool handlers (tools_perception.get_market_data etc.) require full
    service mocks (market_data + technical + memory + DB). We stub the model
    to emit tool calls; if the handler errors due to incomplete mocks, recorder
    writes status=error and re-raises — capability is still exercised, which
    is what this test guards.

    Consequently assertions are status-agnostic: the test does NOT require
    status=ok — only that rows are written with correct session/cycle/name
    and spy confirms wrap_tool_execute was invoked.
    """
    from src.agent.trader import TradingDeps, create_trader_agent
    from src.services.tool_call_recorder import ToolCallRecorder
    from src.config import PersonaConfig

    # 1. Setup DB + session row (FK target)
    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="sess-e2e", name="integration"))
        await db.commit()

    # 2. Build agent with string model (matches create_trader_agent(model: str) signature)
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())

    # 3. Spy on ToolCallRecorder.wrap_tool_execute to count invocations
    call_count = {"n": 0}
    original = ToolCallRecorder.wrap_tool_execute

    async def spy(self, ctx, **kwargs):
        call_count["n"] += 1
        return await original(self, ctx, **kwargs)

    monkeypatch.setattr(ToolCallRecorder, "wrap_tool_execute", spy)

    # 4. Build minimal TradingDeps with mocks + real engine
    deps = TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=AsyncMock(),
        exchange=AsyncMock(),
        technical=MagicMock(),
        memory=AsyncMock(),
        session_id="sess-e2e",
        db_engine=engine,
        cycle_id="cyc-e2e",  # normally set by run_agent_cycle; here we pre-set
    )

    # 5. Run the agent, overriding model at call time via kwarg
    #    (pydantic_ai Agent.run() accepts model= override without changing create_trader_agent signature)
    try:
        await agent.run(
            "scan market and check position",
            deps=deps,
            model=TestModel(call_tools=["get_market_data", "get_position"]),
        )
    except Exception:
        # Tool handlers may error due to incomplete mocks — that's fine;
        # recorder still writes status=error row and spy still counts the wrap.
        pass

    # 6. Assert spy saw >= 1 invocation (guards false-green wire-up)
    assert call_count["n"] >= 1, \
        "wrap_tool_execute must be invoked — otherwise capability isn't wired correctly"

    # 7. Assert DB rows correspond to tool calls (status-agnostic)
    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()

    assert len(rows) >= 1, "at least one tool_calls row must be written"
    for r in rows:
        assert r.session_id == "sess-e2e"
        assert r.cycle_id == "cyc-e2e"
        assert r.tool_name in {"get_market_data", "get_position"}
        assert r.status in {"ok", "error"}  # either is valid — see docstring
        assert r.duration_ms >= 0
```

- [ ] **Sub-step 8.1.2: Run integration test**

Run: `cd /Users/z/Z/TradeBot && uv run pytest tests/test_tool_call_instrumentation.py -v`
Expected: PASS — spy count >= 1, at least one tool_calls row with correct session/cycle IDs.

Note: if this test surfaces issues with `TestModel(call_tools=[...])` API changes, adjust the stub parameters. Fallback path: use `FunctionModel` with explicit tool call emission — plan-stage engineer judgement.

- [ ] **Sub-step 8.1.3: Full regression**

Run: `cd /Users/z/Z/TradeBot && uv run pytest -x`
Expected: 681 passed (680 + 1 integration).

### Step 8.2: Commit

- [ ] **Sub-step 8.2.1: Commit**

```bash
git add tests/test_tool_call_instrumentation.py
git commit -m "test(integration): end-to-end ToolCallRecorder + agent.run()

Verifies recorder is actually invoked (spy count >= 1) and writes
rows with correct session_id/cycle_id/tool_name. Protects against
false-green setups where capability appears wired but never executes."
```

---

## Task 9: Thin Query Script

**Files:**
- Create: `scripts/tool_call_summary.py`

### Step 9.1: Create scripts/ directory

- [ ] **Sub-step 9.1.1: Make dir**

Run: `mkdir -p /Users/z/Z/TradeBot/scripts`

### Step 9.2: Write the script

- [ ] **Sub-step 9.2.1: Create script**

Create `scripts/tool_call_summary.py`:

```python
#!/usr/bin/env python3
"""Tool-call metrics observation script.

Usage (run from repo root):
  uv run python scripts/tool_call_summary.py [--session NAME] [--since 1d|7d|all] [--tool NAME]

Reads tool_calls table + MetricsService.get_tool_call_summary + zero-call工具 padding.
Mirrors src/cli/app.py:379-386 sqlite relative-path normalization.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure src/ is importable when running as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from src.agent.trader import REGISTERED_TOOL_NAMES  # noqa: E402
from src.config import load_settings  # noqa: E402 (matches src/cli/app.py:24)
from src.services.metrics import MetricsService, ToolCallStats  # noqa: E402
from src.storage.database import init_db, get_session  # noqa: E402
from src.storage.models import Session as SessionModel, ToolCall  # noqa: E402


def parse_since(s: str | None) -> timedelta | None:
    if s is None or s == "all":
        return None
    if s.endswith("d"):
        return timedelta(days=int(s[:-1]))
    if s.endswith("h"):
        return timedelta(hours=int(s[:-1]))
    raise ValueError(f"Unrecognized --since value: {s!r} (use '1d', '7d', 'all', etc.)")


def resolve_db_url(settings_path: Path) -> str:
    """Mirror src/cli/app.py:379-386 sqlite relative-path → absolute normalization."""
    settings = load_settings(settings_path)
    db_url = settings.database.url
    if db_url.startswith("sqlite") and ":///" in db_url and not db_url.startswith("sqlite+aiosqlite:////"):
        relative_path = db_url.split(":///", 1)[1]
        absolute_path = _REPO_ROOT / relative_path
        db_url = f"sqlite+aiosqlite:///{absolute_path}"
    return db_url


def fmt_ago(dt: datetime) -> str:
    """Human-readable 'X ago' string."""
    now = datetime.now(timezone.utc)
    delta = now - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


async def resolve_session_id(engine, name_or_uuid: str) -> str:
    """Look up session_id by friendly name or accept UUID verbatim."""
    async with get_session(engine) as db:
        # Try as name
        result = await db.execute(
            select(SessionModel).where(SessionModel.name == name_or_uuid)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row.id
        # Try as id
        result = await db.execute(
            select(SessionModel).where(SessionModel.id == name_or_uuid)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row.id
    raise SystemExit(f"Session not found: {name_or_uuid}")


async def count_cycles(engine, session_id: str | None, since: timedelta | None) -> int:
    """SELECT COUNT(DISTINCT cycle_id) — spec §4.3 says header 'N cycles' is separate query."""
    from sqlalchemy import func
    stmt = select(func.count(func.distinct(ToolCall.cycle_id)))
    if session_id is not None:
        stmt = stmt.where(ToolCall.session_id == session_id)
    if since is not None:
        cutoff = datetime.now(timezone.utc) - since
        stmt = stmt.where(ToolCall.created_at > cutoff)
    async with get_session(engine) as db:
        return (await db.execute(stmt)).scalar() or 0


def print_table(summary: dict[str, ToolCallStats], header: str) -> None:
    """Pretty-print aligned table; pad zero-call rows from REGISTERED_TOOL_NAMES."""
    print(header)
    print()
    print(f"{'Tool':<30}  {'Calls':>5}  {'Err%':>5}  {'p50':>6}  {'p95':>6}  {'Last called':<15}  Notes")
    print("-" * 30 + "  " + "-" * 5 + "  " + "-" * 5 + "  " + "-" * 6 + "  " + "-" * 6 + "  " + "-" * 15 + "  -----")

    for name in REGISTERED_TOOL_NAMES:
        stats = summary.get(name)
        if stats is None:
            print(f"{name:<30}  {'0':>5}  {'─':>5}  {'─':>6}  {'─':>6}  {'never':<15}")
            continue
        err_pct = f"{stats.error_rate * 100:.1f}%"
        last = fmt_ago(stats.last_called_at)
        notes = ""
        if stats.error_breakdown:
            parts = [f"{k}×{v}" for k, v in stats.error_breakdown.items()]
            notes = "[" + ", ".join(parts) + "]"
        print(
            f"{name:<30}  {stats.count:>5}  {err_pct:>5}  "
            f"{stats.p50_duration_ms:>4}ms  {stats.p95_duration_ms:>4}ms  "
            f"{last:<15}  {notes}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=None, help="session name or UUID (omit = all)")
    parser.add_argument("--since", default="1d", help="time window (1d, 7d, all; default 1d)")
    parser.add_argument("--tool", default=None, help="filter to one tool")
    parser.add_argument(
        "--settings",
        type=Path,
        default=_REPO_ROOT / "config" / "settings.yaml",
        help="path to settings.yaml (default: config/settings.yaml in repo root)",
    )
    args = parser.parse_args()

    db_url = resolve_db_url(args.settings)
    engine = await init_db(db_url)

    session_id = None
    if args.session is not None:
        session_id = await resolve_session_id(engine, args.session)

    since = parse_since(args.since)

    ms = MetricsService(engine, session_id=session_id or "")
    summary = await ms.get_tool_call_summary(
        session_id=session_id,
        since=since,
        tool_name=args.tool,
    )

    cycles = await count_cycles(engine, session_id, since)

    session_label = args.session or "(all sessions)"
    since_label = args.since if args.since != "all" else "all history"
    header = f"Session: {session_label}  |  {since_label}  |  {cycles} cycles"
    print_table(summary, header)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Sub-step 9.2.2: Make executable**

Run: `chmod +x /Users/z/Z/TradeBot/scripts/tool_call_summary.py`

### Step 9.3: Smoke test the script

- [ ] **Sub-step 9.3.1: --help smoke**

Run: `cd /Users/z/Z/TradeBot && uv run python scripts/tool_call_summary.py --help`
Expected: argparse help text printed, exit 0.

- [ ] **Sub-step 9.3.2: End-to-end sim smoke (if current DB has data)**

Run: `cd /Users/z/Z/TradeBot && uv run python scripts/tool_call_summary.py --since all 2>&1 | head -40`
Expected: either a pretty-printed table (if existing sim session has cycle data) OR an empty report with all tools shown as "0 / never". Either outcome confirms the pipeline.

Note: `resolve_db_url()` calls `load_settings(args.settings)`. If `config/settings.yaml` is missing or malformed, `load_settings` will raise — let that error surface to the user; don't silently swallow. Run the script from repo root for the default settings path to work.

### Step 9.4: Commit

- [ ] **Sub-step 9.4.1: Commit**

```bash
git add scripts/tool_call_summary.py
git commit -m "feat(scripts): add tool_call_summary.py CLI query utility

Reads MetricsService.get_tool_call_summary + separately queries
COUNT(DISTINCT cycle_id) for header 'N cycles'. Pads zero-call
tools from REGISTERED_TOOL_NAMES constant. Thin wrapper — no
business logic; MetricsService tests cover all aggregation."
```

---

## Task 10: Final Acceptance Checks + Tag

**Files:** None (verification only).

### Step 10.1: Full test suite

- [ ] **Sub-step 10.1.1: Run everything**

Run: `cd /Users/z/Z/TradeBot && uv run pytest -v 2>&1 | tail -20`
Expected: 681 passed (664 baseline + 17 new — see breakdown below).

Breakdown of new tests (total 17, matches spec §5.1 after sync commit):
- Task 1 (storage): 2
- Task 3 (recorder unit): 7
- Task 5 (drift): 1
- Task 7 (MetricsService): 6
- Task 8 (integration): 1

If counts diverge, compare `pytest -v` output against the breakdown above to identify which Task's tests drifted.

### Step 10.2: End-to-end sim session smoke (OPTIONAL)

**Status**: optional — Task 8 integration test already covers agent.run() → row-write at the unit level. Run this only if you have a working sim session harness set up.

- [ ] **Sub-step 10.2.1: Inspect existing data (if any prior sim session ran)**

Locate the project sqlite DB (per `src/cli/app.py:379-386` normalization): typically `data/tradebot.db` or similar under repo root. Verify schema + count rows if present:

```bash
sqlite3 "$(find data -name '*.db' | head -1)" '.schema tool_calls'
sqlite3 "$(find data -name '*.db' | head -1)" 'SELECT COUNT(*) FROM tool_calls'
```

Expected: `.schema` shows the `tool_calls` table with declared columns + indexes; row count is whatever (0 OK pre-first-cycle).

- [ ] **Sub-step 10.2.2: If sim session harness is available, run 1 cycle**

Consult the project README for the actual entrypoint (not hardcoded here to avoid drift). After one cycle completes:

```bash
sqlite3 "$(find data -name '*.db' | head -1)" \
  "SELECT tool_name, status, duration_ms FROM tool_calls ORDER BY id DESC LIMIT 10"
```

Expected: non-empty rows with the tools the agent called this cycle.

If the harness is not set up in this working copy, **skip** — Task 8 integration test is the primary contract test. Document as "deferred to first real run" in PR description.

### Step 10.3: Verify acceptance criteria from spec §6

- [ ] **Sub-step 10.3.1: Checklist**

Open `docs/superpowers/specs/2026-04-20-tool-call-metrics-design.md` §6 and walk the 9 criteria:

1. Schema: `tool_calls` table + indexes present (verify via `sqlite3 .schema tool_calls`)
2. Write path: recorder wired into agent (Task 4)
3. cycle_id 完整性: NOT NULL constraint + scheduler mutate (Task 6 + Task 1 schema)
4. Control flow classification: tests pass (Task 3.5)
5. metrics 失败不影响 agent: test_recorder_does_not_break_tool_on_db_failure passes (Task 3.6)
6. Read path: test_tool_call_summary_* all pass (Task 7)
7. 薄脚本: `scripts/tool_call_summary.py --help` works (Task 9.3)
8. Regression: 664 → 681 passed (Step 10.1) — matches spec §6 AC #8 post-sync
9. B 档原则: `grep -E "args|result_preview|traceback" src/storage/models.py` in the ToolCall section finds nothing

All 9 must pass before marking Task 10 done.

### Step 10.4: Summarize branch

- [ ] **Sub-step 10.4.1: Print final commit log**

Run: `git log --oneline iter1/tool-call-metrics ^main`
Expected: ~9-10 commits (1 spec + 1 plan + 1 spec-sync + 7-8 feat/test commits).

No git tag is created here — tagging conventions are set at merge time (post-PR), not per-feature-branch.

### Step 10.5: Open PR

- [ ] **Sub-step 10.5.1: Push branch**

Run: `git push -u origin iter1/tool-call-metrics`

- [ ] **Sub-step 10.5.2: Open PR (manual or via gh CLI)**

Suggested body:

```
Iteration 1 of 4 pre-observation plan: tool-call metrics enabler.

Ships B-tier observation infrastructure:
- tool_calls table (per-call row with session_id + cycle_id + status + duration_ms + error_type)
- ToolCallRecorder pydantic_ai capability (zero-modification wrap over 26 @agent.tool)
- MetricsService.get_tool_call_summary (aggregation API)
- scripts/tool_call_summary.py (thin CLI)
- 17 new tests (664 → 681)

Design spec: docs/superpowers/specs/2026-04-20-tool-call-metrics-design.md (9 review rounds).

Follow-ups noted:
- project_tradingdeps_typing_cleanup: tighten other 6 `object | None` fields post-observation
- spec §7 retry semantics: add `attempt` column if observation-period aggregates show retry pollution
- spec §7 index: add decision_logs.cycle_id index if cycle-level JOIN becomes frequent
```

---

## Notes for the Executor

- **TDD nonnegotiable**: every new function has a failing test **before** implementation. Tasks 1, 3, 7 enforce this explicitly; don't bypass.
- **Commit per sub-task**: each Task N ends with a commit. Don't batch — per-Task granularity is the review checkpoint.
- **If a test fails unexpectedly**: read the failure carefully. The recorder's "metrics failure does not block tool" contract (Task 3.6) means lots of bugs surface as "tool works but DB row missing" rather than crashes — inspect DB rows aggressively during debugging.
- **Do not modify** `src/scheduler/scheduler.py` or any of the 26 `@agent.tool` bodies. Spec §0.3 is hard constraint.
- **pydantic_ai version**: locked at 1.78.0 by uv.lock. If tests fail with unexpected API errors, check the installed version matches and don't upgrade without spec amendment.
- **On spec conflicts**: if implementation reveals a spec ambiguity, stop and flag — do not silently deviate. Spec §6 acceptance criteria are contract.
