"""R2-8b L4 integration tests — run_agent_cycle prompt injection.

End-to-end assertions: capture the prompt passed to agent.run via a
mock_run side-effect, then assert that:
  - First cycle in a session: NO 'Your prior cycle summaries' header.
  - 2+ cycle: header + N=min(3, available) blocks present.
  - Injection appears AFTER trigger context (memory_context removed in iter-w2r3-memory-disable).
  - DB or render error: cycle still completes; no header in prompt.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import models

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, AgentCycle

models.ALLOW_MODEL_REQUESTS = False


async def _make_deps_engine_with_capture_mocks(session_id: str = "sess-r28b"):
    """Same shape as test_usage_limits.py / test_cycle_log.py helper —
    real Balance/Ticker fixtures so _capture_state_snapshot succeeds."""
    from src.agent.trader import TradingDeps
    from src.integrations.exchange.base import Balance, Ticker

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="r2-8b"))
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


def _make_capturing_agent():
    """Mock agent.run that captures the prompt argument for assertion."""
    captured = {}

    async def mock_run(prompt, **kwargs):
        captured["prompt"] = prompt
        result = MagicMock()
        # T23: inline MagicMock 替换为完整 attrs (Phase 1 cli/app.py 写 8 字段)
        _u = MagicMock()
        _u.total_tokens = 100
        _u.input_tokens = 100
        _u.output_tokens = 0
        _u.cache_read_tokens = 0
        _u.cache_write_tokens = 0
        _u.details = None
        result.usage = lambda: _u
        result.new_messages = lambda: []
        result.output = "auto-generated cycle summary"
        return result

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"
    return agent, captured


async def _seed_prior_cycles(engine, session_id, *, count, base_offset_min=10):
    """Insert `count` prior cycles spaced 1 minute apart, ending base_offset_min
    minutes before now. All execution_status='ok' with non-empty decision."""
    base = datetime.now(timezone.utc) - timedelta(minutes=base_offset_min + count)
    async with get_session(engine) as db:
        for i in range(count):
            db.add(AgentCycle(
                session_id=session_id,
                cycle_id=f"prio{i:04d}",
                triggered_by="scheduled",
                decision=f"Prior summary #{i} body.",
                execution_status="ok",
                created_at=base + timedelta(minutes=i),
            ))
        await db.commit()


async def test_first_cycle_does_not_inject_prior_summaries():
    """T4.1: session with no prior cycles → no header in prompt."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-1")
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    assert "Your prior cycle summaries" not in prompt


async def test_subsequent_cycle_injects_prior_summaries_with_header():
    """T4.2: session with 2 prior ok cycles → header + 2 blocks present."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-2")
    await _seed_prior_cycles(engine, "sess-t4-2", count=2)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    assert "Your prior cycle summaries (most recent N=3, from this session):" in prompt
    assert "Prior summary #0 body." in prompt
    assert "Prior summary #1 body." in prompt

    # P4 integration smoke: user_prompt_snapshot in DB must equal the prompt
    # captured by mock_run. Validates the full wiring trigger → priors block →
    # memory → user_prompt_snapshot_var → AgentCycle INSERT → DB.
    #
    # Filter excludes seeded prior cycles (cycle_id pattern "prio0000".."prio000N"
    # per _seed_prior_cycles in test_agent_cycle_injection.py:96) so scalar_one()
    # picks exactly the new cycle written by run_agent_cycle.
    #
    # NOTE: target test is structured as
    #   `deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-X")`
    # so there is no `session_id` local variable in the test scope — use
    # `deps.session_id` (which holds the same string the helper was called with).
    from src.storage.models import AgentCycle as _AC  # local alias to avoid top-level disturbance
    from sqlalchemy import select as _select
    async with get_session(engine) as db:
        cycle = (await db.execute(
            _select(_AC)
            .where(_AC.session_id == deps.session_id)
            .where(~_AC.cycle_id.like("prio%"))
        )).scalar_one()
    assert cycle.user_prompt_snapshot == captured["prompt"], (
        "P4: user_prompt_snapshot in DB must equal prompt sent to agent.run "
        f"(diff length: snapshot={len(cycle.user_prompt_snapshot or '')} "
        f"vs captured={len(captured['prompt'])})"
    )


async def test_injection_appears_after_trigger_no_memory():
    """T4.3 (iter-w2r3-memory-disable): order in prompt is trigger intro → recent summaries; memory injection removed."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-3")
    # deps.memory remains an AsyncMock per fixture; format_for_prompt should not be called
    await _seed_prior_cycles(engine, "sess-t4-3", count=1)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    pos_recent = prompt.index("Your prior cycle summaries")
    pos_intro = prompt.index("Assess the situation")
    assert pos_intro < pos_recent, (
        f"Order broken: intro={pos_intro} recent={pos_recent}\nprompt:\n{prompt}"
    )
    assert "Your memories:" not in prompt, (
        f"memory injection regression: 'Your memories:' in prompt\n{prompt[:500]}"
    )


async def test_injection_caps_at_n_3_after_4_cycles():
    """T4.4: with 4 prior cycles, only the most recent 3 appear."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-4")
    await _seed_prior_cycles(engine, "sess-t4-4", count=4)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    # Newest 3 should appear; oldest (#0) should NOT
    assert "Prior summary #1 body." in prompt
    assert "Prior summary #2 body." in prompt
    assert "Prior summary #3 body." in prompt
    assert "Prior summary #0 body." not in prompt


async def test_any_injection_error_does_not_abort_cycle(caplog, monkeypatch):
    """T4.5 (review F3): exception in fetch OR render OR format must be caught
    by the outer wrap; cycle proceeds; no 'Your prior cycle summaries' header
    in the prompt; WARNING logged.
    """
    from src.cli.app import TokenBudget, run_agent_cycle
    import src.cli.app as app_mod

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-5")
    await _seed_prior_cycles(engine, "sess-t4-5", count=1)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    # Force an exception inside the render path (post-fetch) to verify the
    # outer wrap catches it (the inner fetch try/except already covers DB).
    def _boom(summaries, now):
        raise RuntimeError("simulated render failure")

    monkeypatch.setattr(app_mod, "_render_recent_summaries", _boom)

    with caplog.at_level(logging.WARNING, logger="src.cli.app"):
        result = await run_agent_cycle(
            agent=agent, deps=deps, trigger_type="scheduled",
            budget=budget, engine=engine,
        )

    assert result is not None, "cycle must complete despite injection error"
    assert "Your prior cycle summaries" not in captured["prompt"]
    assert any(
        "Failed to build recent summaries block" in r.message
        and r.levelno == logging.WARNING
        for r in caplog.records
    )


async def test_subsequent_cycle_with_alert_trigger_injects_after_price_alert():
    """T4.6 (PR #38 review follow-up): the volatility-alert branch (the
    `else` arm at cli/app.py inside the `elif trigger_type == "alert"`
    block) also gets injection. Spec §3.6 mockup uses alert as canonical
    case; T4.1-T4.5 only exercised scheduled trigger. This locks the
    byte-identical alert branch + injection wiring against future regression.
    """
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.services.price_alert import AlertInfo

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-6")
    await _seed_prior_cycles(engine, "sess-t4-6", count=2)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    # Real AlertInfo instance (mock-fidelity lesson: critical paths use
    # real fixtures, not MagicMock — _capture_trigger_context isinstance
    # check would silently return None on a MagicMock and skip the
    # percentage_alert capture path).
    alert_ctx = AlertInfo(
        symbol="BTC/USDT:USDT",
        current_price=74587.5,
        reference_price=76500.0,
        change_pct=-2.5,
        window_minutes=15,
        timestamp=1746098096000,
    )

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="alert", context=alert_ctx,
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    # Volatility alert text rendered (the `else` sub-branch)
    assert "PRICE ALERT: BTC/USDT:USDT dropped 2.5%" in prompt
    # Recent block injected after the alert content
    assert "Your prior cycle summaries (most recent N=3, from this session):" in prompt
    pos_alert = prompt.index("PRICE ALERT")
    pos_recent = prompt.index("Your prior cycle summaries")
    assert pos_alert < pos_recent, (
        f"Order broken: alert={pos_alert} recent={pos_recent}\nprompt:\n{prompt}"
    )
