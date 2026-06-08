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
        agent=agent, deps=deps, trigger_type=trigger_type,
        budget=TokenBudget(daily_max=500_000), engine=engine, context=context,
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
