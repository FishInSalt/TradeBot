"""P4 cycle-level capture — agent_cycles.user_prompt_snapshot for all 3 INSERT paths.

Tests:
  1. Happy path: user_prompt_snapshot equals the prompt passed to agent.run.
  2. usage_limit_exceeded: forensic row has user_prompt_snapshot non-NULL +
     identical content to happy.
  3. retry_exhausted: 3 raised exceptions → forensic row has user_prompt_snapshot
     non-NULL + identical content (covers any-Exception catch-all).

Reuses _make_deps_engine_with_capture_mocks helper pattern (test_usage_limits.py).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import models
from pydantic_ai.usage import UsageLimitExceeded
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, AgentCycle

models.ALLOW_MODEL_REQUESTS = False


def _mock_usage_legacy(total_tokens: int = 100):
    u = MagicMock()
    u.total_tokens = total_tokens
    u.input_tokens = total_tokens
    u.output_tokens = 0
    u.cache_read_tokens = 0
    u.cache_write_tokens = 0
    u.details = None
    return u


async def _make_deps_engine_with_capture_mocks(session_id: str = "sess-p4c"):
    """Same shape as test_usage_limits / test_agent_cycle_injection helpers."""
    from src.agent.trader import TradingDeps
    from src.integrations.exchange.base import Balance, Ticker

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="p4-cycle"))
        await db.commit()

    exchange = MagicMock()
    exchange.fetch_positions = AsyncMock(return_value=[])
    exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=10000.0, used_usdt=0.0,
    ))
    exchange.fetch_open_orders = AsyncMock(return_value=[])
    exchange.get_price_level_alerts = MagicMock(return_value=[])

    market_data = MagicMock()
    market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=75000.0, bid=74999.0, ask=75001.0,
        high=75500.0, low=74500.0, base_volume=1000.0, timestamp=1746098096000,
    ))

    deps = TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=market_data,
        exchange=exchange,
        technical=MagicMock(),
        memory=AsyncMock(format_for_prompt=AsyncMock(return_value="No relevant memories.")),
        session_id=session_id,
        db_engine=engine,
    )
    return deps, engine


def test_capture_trigger_contexts_maps_batch():
    from src.services.cycle_capture import _capture_trigger_contexts
    from src.integrations.exchange.base import PriceLevelAlertInfo

    alert = PriceLevelAlertInfo(
        alert_id="a1", symbol="BTC/USDT:USDT", current_price=80050.0,
        target_price=80000.0, direction="above", reasoning="r", timestamp=1_700_000_000_000,
    )
    out = _capture_trigger_contexts("cyc1", [("scheduled", None), ("alert", alert)])
    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0] == {"type": "scheduled_tick"}
    assert out[1]["type"] == "price_level_alert"
    assert out[1]["alert_id"] == "a1"


def test_capture_trigger_contexts_all_fail_yields_none_slots():
    from src.services.cycle_capture import _capture_trigger_contexts

    # context that raises on attribute access → per-event None, count preserved
    class Bad:
        def __getattr__(self, name):
            raise RuntimeError("boom")

    out = _capture_trigger_contexts("cyc1", [("conditional", Bad()), ("conditional", Bad())])
    assert out == [None, None]



async def test_cycle_captures_user_prompt_snapshot_happy():
    """AC-3 happy path: user_prompt_snapshot non-NULL + contains trigger phrase."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-h")
    budget = TokenBudget(daily_max=500_000)

    captured = {}

    async def mock_run(prompt, **kwargs):
        captured["prompt"] = prompt
        result = MagicMock()
        result.usage = lambda: _mock_usage_legacy(100)
        result.new_messages = lambda: []
        result.output = "decision text"
        return result

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"

    await run_agent_cycle(
        agent, deps, [("scheduled", None)], budget, engine,
        model="test-model",
    )

    async with get_session(engine) as db:
        cycle = (await db.execute(
            select(AgentCycle).where(AgentCycle.session_id == "sess-h")
        )).scalar_one()

    assert cycle.user_prompt_snapshot is not None, "user_prompt_snapshot should be populated"
    assert cycle.user_prompt_snapshot == captured["prompt"], (
        "user_prompt_snapshot must match the prompt passed to agent.run"
    )
    assert "You have been woken up" in cycle.user_prompt_snapshot, (
        "trigger phrase must be present"
    )


async def test_cycle_captures_user_prompt_snapshot_usage_limit():
    """AC-4 usage_limit_exceeded forensic path: user_prompt_snapshot still set
    + identical to happy-path content under same input."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-ul")
    budget = TokenBudget(daily_max=500_000)

    captured = {}

    async def mock_run(prompt, **kwargs):
        captured["prompt"] = prompt
        # Raise UsageLimitExceeded — exits via line ~525 except branch
        raise UsageLimitExceeded("simulated token cap")

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"

    await run_agent_cycle(
        agent, deps, [("scheduled", None)], budget, engine,
        model="test-model",
    )

    async with get_session(engine) as db:
        cycle = (await db.execute(
            select(AgentCycle).where(AgentCycle.session_id == "sess-ul")
        )).scalar_one()

    assert cycle.execution_status == "usage_limit_exceeded"
    assert cycle.user_prompt_snapshot is not None, "forensic row missing user_prompt_snapshot"
    assert cycle.user_prompt_snapshot == captured["prompt"], (
        "forensic capture must match the prompt sent to agent.run "
        "(identical to happy path under same input)"
    )


async def test_cycle_captures_user_prompt_snapshot_retry_exhausted(monkeypatch):
    """AC-4 retry_exhausted forensic path: 3 raised exceptions → forensic row
    has user_prompt_snapshot identical to what would have been sent on attempt 1.

    Note: takes `monkeypatch` fixture so asyncio.sleep can be patched safely
    (no module-attribute racing under pytest-xdist parallel workers).
    """
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-re")
    budget = TokenBudget(daily_max=500_000)

    captured = {}

    async def mock_run(prompt, **kwargs):
        if "prompt" not in captured:
            captured["prompt"] = prompt
        # Raise on every attempt — except Exception path falls through 3× to
        # retry_exhausted INSERT at line ~582
        raise RuntimeError("simulated unexpected LLM failure")

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"

    # Use monkeypatch.setattr (fixture-scoped, auto-teardown, safe under pytest-xdist
    # parallel workers) instead of raw module-attribute assignment.
    import asyncio
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))

    await run_agent_cycle(
        agent, deps, [("scheduled", None)], budget, engine,
        model="test-model",
    )

    async with get_session(engine) as db:
        cycle = (await db.execute(
            select(AgentCycle).where(AgentCycle.session_id == "sess-re")
        )).scalar_one()

    assert cycle.execution_status == "retry_exhausted"
    assert cycle.user_prompt_snapshot is not None, "retry_exhausted row missing user_prompt_snapshot"
    assert cycle.user_prompt_snapshot == captured["prompt"], (
        "forensic capture must match prompt from attempt 1 "
        "(retry-loop prompt invariant — see AC-10)"
    )


async def test_cycle_console_renders_context_section_happy():
    """app.py success 构造点透传 user_prompt_snapshot → console 渲出 ▾ Context + Woke by。

    drift-guard：若 app.py 漏传 user_prompt_snapshot，字段默认 None → 无 Context 段 → 红。
    """
    import io
    from rich.console import Console
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.integrations.exchange.base import PriceLevelAlertInfo

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-ctx-happy")

    async def mock_run(prompt, **kwargs):
        result = MagicMock()
        result.usage = lambda: _mock_usage_legacy(1000)   # 全 token 属性为 int，避 commit 崩
        result.new_messages = lambda: []
        result.output = "**(1) Stance** — flat.\n**(4) Thesis & invalidation** — bearish."
        return result

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"

    buf = io.StringIO()
    console = Console(file=buf, width=120, no_color=True)
    alert = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=73384.0, direction="above",
        current_price=73384.0, reasoning="MA20 reclaim", timestamp=1746098096000,
        alert_id="934cfd12",
    )
    await run_agent_cycle(
        agent, deps, [("alert", alert)], TokenBudget(daily_max=1_000_000), engine,
        console=console, model="test-model",
    )
    out = buf.getvalue()
    assert "▾ Context (carried into this cycle)" in out
    assert "Woke by — PRICE LEVEL ALERT:" in out
    assert "alert id=934cfd12" in out


async def test_cycle_console_renders_context_on_forensic():
    """usage_limit forensic 短路路径透传 user_prompt_snapshot → console 渲出 Woke by。"""
    import io
    from rich.console import Console
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.integrations.exchange.base import PriceLevelAlertInfo

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-ctx-forensic")

    async def mock_run(prompt, **kwargs):
        raise UsageLimitExceeded("simulated token cap")

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"

    buf = io.StringIO()
    console = Console(file=buf, width=120, no_color=True)
    alert = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=73384.0, direction="above",
        current_price=73384.0, reasoning="MA20 reclaim", timestamp=1746098096000,
        alert_id="934cfd12",
    )
    await run_agent_cycle(
        agent, deps, [("alert", alert)], TokenBudget(daily_max=1_000_000), engine,
        console=console, model="test-model",
    )
    out = buf.getvalue()
    assert "▾ Context (carried into this cycle)" in out
    assert "Woke by — PRICE LEVEL ALERT:" in out
