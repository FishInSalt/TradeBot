"""Task 24: fill notification rendering tests — event.entry_price plumbing.

Tests capture the prompt passed to agent.run via a mock side-effect and
assert the fill-notification fragment format for three cases:
  - Open fill (pnl=None): fee only
  - Full close with entry_price: fee + gross + equiv-round-trip net
  - Part close OR cache-miss full close: fee + gross only
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_deps_engine(session_id: str):
    """Minimal deps+engine for run_agent_cycle prompt-capture tests.

    Mirrors test_agent_cycle_injection._make_deps_engine_with_capture_mocks:
    real Balance/Ticker fixtures so _capture_state_snapshot succeeds.
    """
    from src.agent.trader import TradingDeps
    from src.integrations.exchange.base import Balance, Ticker

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="fill-notif-test"))
        await db.commit()

    exchange = MagicMock()
    exchange.fetch_positions = AsyncMock(return_value=[])
    exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=10000.0, used_usdt=0.0,
    ))
    exchange.fetch_open_orders = AsyncMock(return_value=[])
    exchange.get_price_level_alerts = MagicMock(return_value=[])
    exchange.get_contract_size = AsyncMock(return_value=1.0)

    market_data = MagicMock()
    market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=81000.0, bid=80999.0, ask=81001.0,
        high=82000.0, low=80000.0, base_volume=1000.0, timestamp=1746098096000,
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
        fee_rate=0.001,
    )
    return deps, engine


def _make_capturing_agent():
    """Mock agent.run that captures the prompt for assertion."""
    captured: dict = {}

    async def mock_run(prompt, **kwargs):
        captured["prompt"] = prompt
        result = MagicMock()
        _u = MagicMock()
        _u.total_tokens = 100
        _u.input_tokens = 100
        _u.output_tokens = 0
        _u.cache_read_tokens = 0
        _u.cache_write_tokens = 0
        _u.details = None
        result.usage = lambda: _u
        result.new_messages = lambda: []
        result.output = "cycle summary"
        return result

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"
    return agent, captured


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fill_notification_open_includes_fee():
    """Open fill (pnl=None) — message includes 'Fee: -29.97 USDT'."""
    from src.cli.app import TokenBudget, run_agent_cycle
    from tests._fixtures import make_fill_event

    deps, engine = await _make_deps_engine("sess-fill-open")
    agent, captured = _make_capturing_agent()

    fill = make_fill_event(
        trigger_reason="market",
        fill_price=81000.0,
        amount=0.369,
        fee=29.97,
        pnl=None,
        is_full_close=False,
        entry_price=None,
    )

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="conditional",
        budget=TokenBudget(daily_max=500_000),
        engine=engine, context=fill,
    )

    prompt = captured["prompt"]
    assert "Fee: -29.97 USDT" in prompt
    # Sanity: no round-trip line for open fill
    assert "round-trip" not in prompt
    assert "gross" not in prompt


@pytest.mark.asyncio
async def test_fill_notification_close_full_includes_round_trip_net_uses_entry_price_field():
    """Full close fill: round-trip net computed from event.entry_price (not back-derived).

    pnl=-56.29, fee=29.91, entry_price=81878.6, amount=0.366, deps.fee_rate=0.001
    → entry_fee_recompute = 81878.6 × 0.366 × 0.001 = 29.9676... ≈ 29.97
    → round_trip_net = -29.97 + (-56.29) - 29.91 = -116.17
    """
    from src.cli.app import TokenBudget, run_agent_cycle
    from tests._fixtures import make_fill_event

    deps, engine = await _make_deps_engine("sess-fill-close-full")
    agent, captured = _make_capturing_agent()

    fill = make_fill_event(
        trigger_reason="take_profit",
        fill_price=82300.0,
        amount=0.366,
        fee=29.91,
        pnl=-56.29,
        is_full_close=True,
        entry_price=81878.6,
    )

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="conditional",
        budget=TokenBudget(daily_max=500_000),
        engine=engine, context=fill,
    )

    prompt = captured["prompt"]
    # entry_fee_recompute = 81878.6 * 0.366 * 0.001 = 29.9676... → rounds to 29.97
    # round_trip_net = -29.97 + (-56.29) - 29.91 = -116.17
    assert "PnL: -56.29 USDT (gross) / -116.17 USDT (this fill, equiv-round-trip)" in prompt
    assert "Fee: -29.91 USDT" in prompt


@pytest.mark.asyncio
async def test_fill_notification_close_partial_omits_round_trip():
    """Part close (is_full_close=False) — Fee + gross only, no round-trip line."""
    from src.cli.app import TokenBudget, run_agent_cycle
    from tests._fixtures import make_fill_event

    deps, engine = await _make_deps_engine("sess-fill-partial")
    agent, captured = _make_capturing_agent()

    fill = make_fill_event(
        trigger_reason="stop",
        fill_price=79000.0,
        amount=0.5,
        fee=41.0,
        pnl=750.0,
        is_full_close=False,
        entry_price=80000.0,
    )

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="conditional",
        budget=TokenBudget(daily_max=500_000),
        engine=engine, context=fill,
    )

    prompt = captured["prompt"]
    assert "Fee: -41.00 USDT" in prompt
    assert "PnL: +750.00 USDT (gross)" in prompt
    assert "round-trip" not in prompt


@pytest.mark.asyncio
async def test_fill_notification_label_uses_this_fill_equiv_round_trip():
    """Label is '(this fill, equiv-round-trip)' — exact string match."""
    from src.cli.app import TokenBudget, run_agent_cycle
    from tests._fixtures import make_fill_event

    deps, engine = await _make_deps_engine("sess-fill-label")
    agent, captured = _make_capturing_agent()

    # Full close with entry_price → should produce the label
    fill = make_fill_event(
        trigger_reason="take_profit",
        fill_price=82000.0,
        amount=0.1,
        fee=8.2,
        pnl=100.0,
        is_full_close=True,
        entry_price=80000.0,
    )

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="conditional",
        budget=TokenBudget(daily_max=500_000),
        engine=engine, context=fill,
    )

    prompt = captured["prompt"]
    assert "(this fill, equiv-round-trip)" in prompt


@pytest.mark.asyncio
async def test_fill_notification_pnl_cap_scenario_uses_actual_entry_price():
    """Drift guard: pnl_cap fired in sim → entry_price field is pre-cap entry,
    NOT back-derived from clamped pnl. round_trip_net uses correct entry_fee.

    entry_price=80000.0, amount=0.1, fee_rate=0.001
    → entry_fee_recompute = 80000.0 × 0.1 × 0.001 = 8.0
    Clamped pnl=-500.0 (margin cap), fee=8.0
    → round_trip_net = -8.0 + (-500.0) - 8.0 = -516.0

    If code incorrectly back-derived entry from pnl the result would differ.
    """
    from src.cli.app import TokenBudget, run_agent_cycle
    from tests._fixtures import make_fill_event

    deps, engine = await _make_deps_engine("sess-fill-pnlcap")
    agent, captured = _make_capturing_agent()

    # Simulate pnl_cap clamped scenario: pnl is clamped but entry_price is original
    fill = make_fill_event(
        trigger_reason="liquidation",
        fill_price=72000.0,
        amount=0.1,
        fee=8.0,
        pnl=-500.0,   # pnl_cap clamped value (margin)
        is_full_close=True,
        entry_price=80000.0,   # pre-cap original entry — must be used for recompute
    )

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="conditional",
        budget=TokenBudget(daily_max=500_000),
        engine=engine, context=fill,
    )

    prompt = captured["prompt"]
    # entry_fee_recompute = 80000.0 * 0.1 * 0.001 = 8.0
    # round_trip_net = -8.0 + (-500.0) - 8.0 = -516.0
    assert "(this fill, equiv-round-trip)" in prompt
    assert "-516.00" in prompt
    assert "Fee: -8.00 USDT" in prompt


@pytest.mark.asyncio
async def test_fill_notification_full_close_cache_miss_emits_hint():
    """Cache miss on full close (OKX restart scenario): is_full_close=True +
    entry_price=None → render fee + gross + explicit hint, not silent degrade.

    fact-provider principle: agent should know WHY round-trip line is absent
    so it doesn't form wrong conclusions from the inconsistency vs sim path
    (which always carries entry_price).
    """
    from src.cli.app import TokenBudget, run_agent_cycle
    from tests._fixtures import make_fill_event

    deps, engine = await _make_deps_engine("sess-fill-cache-miss")
    agent, captured = _make_capturing_agent()

    fill = make_fill_event(
        trigger_reason="stop",
        fill_price=79000.0,
        amount=0.5,
        fee=41.0,
        pnl=-500.0,
        is_full_close=True,
        entry_price=None,  # cache miss
    )

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="conditional",
        budget=TokenBudget(daily_max=500_000),
        engine=engine, context=fill,
    )

    prompt = captured["prompt"]
    assert "Fee: -41.00 USDT" in prompt
    assert "PnL: -500.00 USDT (gross)" in prompt
    assert "[round-trip net unavailable: entry_price not cached]" in prompt
