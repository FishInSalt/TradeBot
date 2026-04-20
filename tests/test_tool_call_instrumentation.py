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
