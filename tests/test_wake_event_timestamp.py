"""Wake-event timestamp & relative age (spec 2026-06-08).

Two pure helpers in src/cli/app.py:
  - _format_event_age(now, then) -> str | None : age ladder (future→None, <2s→"just now",
    else delegate to _format_relative_time)
  - _wake_time_suffix(verb, event_ts_ms, now) -> str : int-ms→UTC + assembled suffix
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


# ---------------------------------------------------------------- _format_event_age

def test_format_event_age_future_returns_none():
    from src.cli.app import _format_event_age
    now = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
    then = now + timedelta(seconds=5)   # event timestamp ahead of now (clock skew)
    assert _format_event_age(now, then) is None


def test_format_event_age_under_2s_just_now():
    from src.cli.app import _format_event_age
    now = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
    then = now - timedelta(seconds=1)
    assert _format_event_age(now, then) == "just now"


def test_format_event_age_exactly_2s_delegates_to_seconds():
    from src.cli.app import _format_event_age
    now = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
    then = now - timedelta(seconds=2)
    assert _format_event_age(now, then) == "2 sec ago"


def test_format_event_age_sub_minute_delegates():
    from src.cli.app import _format_event_age
    now = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
    then = now - timedelta(seconds=42)
    assert _format_event_age(now, then) == "42 sec ago"


def test_format_event_age_minutes_delegates():
    from src.cli.app import _format_event_age
    now = datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc)
    then = now - timedelta(minutes=4, seconds=37)
    assert _format_event_age(now, then) == "4 min ago"


# ---------------------------------------------------------------- _wake_time_suffix

def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_wake_time_suffix_with_age():
    from src.cli.app import _wake_time_suffix
    then = datetime(2026, 6, 1, 14, 34, 0, tzinfo=timezone.utc)
    now = then + timedelta(minutes=4)
    assert _wake_time_suffix("filled", _ms(then), now) == " — filled 2026-06-01 14:34 UTC (4 min ago)"


def test_wake_time_suffix_just_now():
    from src.cli.app import _wake_time_suffix
    then = datetime(2026, 6, 1, 14, 38, 22, tzinfo=timezone.utc)
    now = then  # scheduled: trigger time ≡ cycle_started_at
    assert _wake_time_suffix("fired", _ms(then), now) == " — fired 2026-06-01 14:38 UTC (just now)"


def test_wake_time_suffix_future_drops_parenthetical():
    from src.cli.app import _wake_time_suffix
    then = datetime(2026, 6, 1, 14, 38, 0, tzinfo=timezone.utc)
    now = then - timedelta(seconds=10)   # event ts ahead of now (skew)
    assert _wake_time_suffix("fired", _ms(then), now) == " — fired 2026-06-01 14:38 UTC"


def test_wake_time_suffix_verb_is_used():
    from src.cli.app import _wake_time_suffix
    then = datetime(2026, 6, 1, 14, 34, 0, tzinfo=timezone.utc)
    now = then + timedelta(minutes=4)
    assert _wake_time_suffix("fired", _ms(then), now).startswith(" — fired ")


# ---------------------------------------------------- _format_price_level_alert_trigger

def test_price_level_alert_trigger_appends_fired_suffix():
    from src.cli.app import _format_price_level_alert_trigger
    from src.integrations.exchange.base import PriceLevelAlertInfo

    then = datetime(2026, 6, 1, 14, 34, 0, tzinfo=timezone.utc)
    now = then + timedelta(minutes=4)
    context = PriceLevelAlertInfo(
        alert_id="a1b2", symbol="BTC/USDT:USDT", current_price=67193.70,
        target_price=67200.0, direction="below", reasoning="breakdown",
        timestamp=_ms(then),
    )
    out = _format_price_level_alert_trigger(context, now)
    assert "PRICE LEVEL:" in out
    assert out.endswith(" — fired 2026-06-01 14:34 UTC (4 min ago)")


# ---------------------------------------------------- branch integration (run_agent_cycle)

async def _run(trigger_type, context=None):
    from src.cli.app import TokenBudget, run_agent_cycle
    from tests.test_agent_cycle_injection import (
        _make_deps_engine_with_capture_mocks, _make_capturing_agent,
    )
    deps, engine = await _make_deps_engine_with_capture_mocks(f"sess-wts-{trigger_type}")
    agent, captured = _make_capturing_agent()
    await run_agent_cycle(
        agent=agent, deps=deps, events=[(trigger_type, context)],
        budget=TokenBudget(daily_max=500_000), engine=engine,
    )
    return captured["prompt"]


async def test_scheduled_prompt_has_fired_just_now():
    prompt = await _run("scheduled")
    assert " — fired " in prompt
    assert "(just now)" in prompt


async def test_fill_prompt_has_filled_suffix_and_no_double_triggered():
    from tests._fixtures import make_fill_event
    ts = int((datetime.now(timezone.utc) - timedelta(minutes=4, seconds=30)).timestamp() * 1000)
    context = make_fill_event(trigger_reason="limit", pnl=None, timestamp=ts)
    prompt = await _run("conditional", context)
    assert "limit triggered" in prompt            # event line intact
    assert " — filled " in prompt                 # suffix uses 'filled'
    assert "(4 min ago)" in prompt
    assert " — triggered " not in prompt          # suffix must NOT echo 'triggered'


async def test_percentage_alert_prompt_has_fired_suffix():
    from src.services.price_alert import AlertInfo
    ts = int((datetime.now(timezone.utc) - timedelta(minutes=4, seconds=30)).timestamp() * 1000)
    context = AlertInfo(
        symbol="BTC/USDT:USDT", current_price=67658.60, reference_price=68002.90,
        change_pct=-0.5, window_minutes=15, timestamp=ts,
    )
    prompt = await _run("alert", context)
    assert "PRICE ALERT:" in prompt
    assert " — fired " in prompt
    assert "(4 min ago)" in prompt


# ---------------------------------------------------- _wake_header_line

def test_wake_header_line_single_scheduled_has_suffix():
    from datetime import datetime, timezone
    from src.cli.app import _wake_header_line
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    line = _wake_header_line([("scheduled", None)], now)
    assert line == "You have been woken up by a scheduled trigger — fired 2026-06-01 14:38 UTC (just now)"


def test_wake_header_line_single_conditional_no_suffix():
    from datetime import datetime, timezone
    from src.cli.app import _wake_header_line
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    assert _wake_header_line([("conditional", object())], now) == "You have been woken up by a conditional trigger"


def test_wake_header_line_multi_breakdown():
    from datetime import datetime, timezone
    from src.cli.app import _wake_header_line
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    events = [("conditional", object()), ("alert", object()), ("alert", object())]
    line = _wake_header_line(events, now)
    assert line == "You have been woken up by 3 triggers (1 fill, 2 alerts) since the last cycle"


async def test_render_event_block_percentage_alert():
    from datetime import datetime, timezone
    from src.cli.app import _render_event_block
    from src.services.price_alert import AlertInfo
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    alert = AlertInfo(
        symbol="BTC/USDT:USDT", current_price=79170.0, reference_price=78000.0,
        change_pct=1.5, window_minutes=15, timestamp=int(now.timestamp() * 1000),
    )
    block = await _render_event_block(deps=None, trigger_type="alert", context=alert, cycle_started_at=now)
    assert block.startswith("\n\nPRICE ALERT: BTC/USDT:USDT surged 1.5% in 15min (78000.00 → 79170.00)")
    assert "fired 2026-06-01 14:38 UTC" in block


async def test_render_event_block_scheduled_empty():
    from datetime import datetime, timezone
    from src.cli.app import _render_event_block
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    assert await _render_event_block(deps=None, trigger_type="scheduled", context=None, cycle_started_at=now) == ""


async def test_render_event_block_open_fill_fee_only():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from src.cli.app import _render_event_block
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    ctx = SimpleNamespace(
        trigger_reason="market", symbol="BTC/USDT:USDT", amount=1.0, fill_price=80000.0,
        pnl=None, fee=1.0, is_full_close=False, entry_price=None,
        timestamp=int(now.timestamp() * 1000),
    )
    block = await _render_event_block(deps=None, trigger_type="conditional", context=ctx, cycle_started_at=now)
    assert block.startswith("\n\nIMPORTANT EVENT: market triggered — BTC/USDT:USDT 1.0 @ 80000.0")
    assert ", Fee: -1.00 USDT" in block
    assert "filled 2026-06-01 14:38 UTC" in block


async def test_render_event_block_full_close_round_trip_net():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from src.cli.app import _render_event_block
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)

    async def _get_cs(symbol):
        return 0.01

    deps = SimpleNamespace(exchange=SimpleNamespace(get_contract_size=_get_cs), fee_rate=0.0005)
    ctx = SimpleNamespace(
        trigger_reason="tp", symbol="BTC/USDT:USDT", amount=1.0, fill_price=81000.0,
        pnl=10.0, fee=0.405, is_full_close=True, entry_price=80000.0,
        timestamp=int(now.timestamp() * 1000),
    )
    block = await _render_event_block(deps=deps, trigger_type="conditional", context=ctx, cycle_started_at=now)
    assert "PnL: +10.00 USDT (gross)" in block
    assert "(this fill, equiv-round-trip)" in block
    # round_trip_net = -(80000.0 * 1.0 * 0.01 * 0.0005) + 10.0 - 0.405 = -0.4 + 10.0 - 0.405 = 9.195 → +9.20
    assert "+9.20 USDT (this fill, equiv-round-trip)" in block


# ============================================================================
# batch event drain (spec 2026-06-08 §1+§2) — N==1 byte-identity + N>1 multi
# ============================================================================

_FROZEN_NOW = datetime(2026, 6, 1, 14, 38, 0, tzinfo=timezone.utc)


class _FrozenDatetime(datetime):
    """datetime subclass whose .now() is pinned to _FROZEN_NOW so the dynamic
    wake-time suffix renders deterministically for full-string byte-identity."""

    @classmethod
    def now(cls, tz=None):
        return _FROZEN_NOW if tz is None else _FROZEN_NOW.astimezone(tz)


async def _run_capture(events, monkeypatch, session_id):
    """Run a frozen-clock cycle and return the exact prompt passed to agent.run."""
    from src.cli import app as app_mod
    from src.cli.app import TokenBudget, run_agent_cycle
    from tests.test_agent_cycle_injection import (
        _make_deps_engine_with_capture_mocks, _make_capturing_agent,
    )
    monkeypatch.setattr(app_mod, "datetime", _FrozenDatetime)
    deps, engine = await _make_deps_engine_with_capture_mocks(session_id)
    agent, captured = _make_capturing_agent()
    result = await run_agent_cycle(
        agent=agent, deps=deps, events=events,
        budget=TokenBudget(daily_max=500_000), engine=engine,
    )
    return captured["prompt"], deps, engine, result


def _expected_single(suffix_body: str, symbol="BTC/USDT:USDT", timeframe="15m") -> str:
    """The exact N==1 base prompt (header + pair + assess), no event block."""
    return (
        f"You have been woken up by a {suffix_body}.\n"
        f"Trading pair: {symbol} | Timeframe: {timeframe}\n"
        "Assess the situation and decide what to do."
    )


async def test_n1_scheduled_prompt_byte_identical(monkeypatch):
    """N==1 scheduled: full-string equality locks byte-identity to the prior
    single-event scheduled prompt (header fire-time suffix + 'just now')."""
    prompt, _, _, _ = await _run_capture(
        [("scheduled", None)], monkeypatch, "sess-bi-sched",
    )
    expected = _expected_single(
        "scheduled trigger — fired 2026-06-01 14:38 UTC (just now)"
    )
    assert prompt == expected


async def test_n1_conditional_fill_prompt_byte_identical(monkeypatch):
    """N==1 conditional open fill: full-string equality (header has no suffix,
    event block carries the 'filled' age suffix)."""
    from tests._fixtures import make_fill_event
    ts = int((_FROZEN_NOW - timedelta(minutes=4, seconds=30)).timestamp() * 1000)
    fill = make_fill_event(
        trigger_reason="limit", symbol="BTC/USDT:USDT", amount=0.01,
        fill_price=50000.0, fee=0.5, pnl=None, timestamp=ts,
    )
    prompt, _, _, _ = await _run_capture(
        [("conditional", fill)], monkeypatch, "sess-bi-fill",
    )
    expected = _expected_single("conditional trigger") + (
        "\n\nIMPORTANT EVENT: limit triggered — BTC/USDT:USDT 0.01 @ 50000.0"
        ", Fee: -0.50 USDT"
        " — filled 2026-06-01 14:33 UTC (4 min ago)"
    )
    assert prompt == expected


async def test_n1_price_level_alert_prompt_byte_identical(monkeypatch):
    """N==1 price-level alert: full-string equality."""
    from src.integrations.exchange.base import PriceLevelAlertInfo
    ts = int((_FROZEN_NOW - timedelta(minutes=4, seconds=30)).timestamp() * 1000)
    ctx = PriceLevelAlertInfo(
        alert_id="a1b2", symbol="BTC/USDT:USDT", current_price=67193.70,
        target_price=67200.0, direction="below", reasoning="breakdown",
        timestamp=ts,
    )
    prompt, _, _, _ = await _run_capture(
        [("alert", ctx)], monkeypatch, "sess-bi-pla",
    )
    expected = _expected_single("alert trigger") + (
        "\n\nPRICE LEVEL: BTC/USDT:USDT reached 67193.70 "
        "(alert id=a1b2 below 67200.00 — breakdown)"
        " — fired 2026-06-01 14:33 UTC (4 min ago)"
    )
    assert prompt == expected


async def test_n_gt_1_multi_event_integration(monkeypatch):
    """N>1: multi-trigger header, fill block before alert block (heap priority),
    persisted triggered_by == dominant 'conditional', trigger_context is a
    2-element JSON array."""
    import json
    from tests._fixtures import make_fill_event
    from src.services.price_alert import AlertInfo
    from src.storage.database import get_session
    from src.storage.models import AgentCycle
    from sqlalchemy import select

    fill_ts = int((_FROZEN_NOW - timedelta(minutes=2)).timestamp() * 1000)
    alert_ts = int((_FROZEN_NOW - timedelta(minutes=6)).timestamp() * 1000)
    fill = make_fill_event(
        trigger_reason="market", symbol="BTC/USDT:USDT", amount=0.01,
        fill_price=50000.0, fee=0.5, pnl=None, timestamp=fill_ts,
    )
    alert = AlertInfo(
        symbol="BTC/USDT:USDT", current_price=67658.60, reference_price=68002.90,
        change_pct=-0.5, window_minutes=15, timestamp=alert_ts,
    )
    prompt, deps, engine, _ = await _run_capture(
        [("conditional", fill), ("alert", alert)], monkeypatch, "sess-multi",
    )

    # Multi-trigger header
    assert prompt.startswith(
        "You have been woken up by 2 triggers (1 fill, 1 alert) since the last cycle.\n"
    )
    # fill block appears before alert block
    fill_pos = prompt.index("IMPORTANT EVENT: market triggered")
    alert_pos = prompt.index("PRICE ALERT: BTC/USDT:USDT dropped")
    assert fill_pos < alert_pos
    # each block carries its own age suffix
    assert "filled 2026-06-01 14:36 UTC (2 min ago)" in prompt
    assert "fired 2026-06-01 14:32 UTC (6 min ago)" in prompt

    # Persisted dominant type + JSON-array trigger_context (2 elements)
    async with get_session(engine) as db:
        rows = (await db.execute(
            select(AgentCycle).where(AgentCycle.session_id == "sess-multi")
        )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.triggered_by == "conditional"
    parsed = json.loads(row.trigger_context)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
