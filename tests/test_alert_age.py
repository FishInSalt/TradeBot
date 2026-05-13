"""Iter tool-opt-alert-age tests.

Spec: docs/superpowers/specs/2026-05-14-iter-tool-opt-alert-age-design.md

Time mocking pattern: tests patch `time.time` via monkeypatch on the per-module
reference (`time` is a module singleton; patch is test-scoped with auto-teardown).
For BaseExchange tests:
    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: <value>)
For get_active_alerts rendering tests:
    monkeypatch.setattr("src.agent.tools_perception.time.time", lambda: <value>)
"""
from __future__ import annotations

import re
import pytest
from unittest.mock import MagicMock


# ============ Task 1: AL-1 — created_at on add ============

def test_add_price_level_alert_stores_created_at(monkeypatch):
    """Spec §5.1.1 + AC-1: add_price_level_alert writes a created_at: float
    field on the alert dict, equal to time.time() at the call site.
    """
    from src.integrations.exchange.simulated import SimulatedExchange

    # Patch time.time at the point where it's imported in base.py
    mock_time = 1700000000.0
    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: mock_time)

    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}

    ex = SimulatedExchange(
        config=config, db_engine=None, session_id="test-session", symbol="BTC/USDT:USDT"
    )

    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h structural high",
    )

    assert alert_id is not None
    alerts = ex.get_price_level_alerts()
    assert len(alerts) == 1
    a = alerts[0]
    assert a["id"] == alert_id
    assert a["price"] == 82_100.0
    assert a["direction"] == "above"
    assert a["symbol"] == "BTC/USDT:USDT"
    assert a["reasoning"] == "4h structural high"
    # AL-1 the new field:
    assert "created_at" in a
    assert a["created_at"] == 1700000000.0


# ============ Task 2: BaseExchange.update_price_level_alert ============

def test_update_price_level_alert_is_in_place(monkeypatch):
    """Spec §5.1.2 + AC-2: update is in-place — id is preserved across update."""
    from src.integrations.exchange.simulated import SimulatedExchange

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}
    ex = SimulatedExchange(config=config, db_engine=None, session_id="test-session", symbol="BTC/USDT:USDT")
    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )

    ok = ex.update_price_level_alert(alert_id, 82_500.0, "tighten level")
    assert ok is True

    alerts = ex.get_price_level_alerts()
    assert len(alerts) == 1
    assert alerts[0]["id"] == alert_id  # id stable


def test_update_price_level_alert_overwrites_price_and_reasoning(monkeypatch):
    """Spec §4.2 + AC-2: update writes new price and new reasoning in place."""
    from src.integrations.exchange.simulated import SimulatedExchange

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}
    ex = SimulatedExchange(config=config, db_engine=None, session_id="test-session", symbol="BTC/USDT:USDT")
    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )

    ex.update_price_level_alert(alert_id, 82_500.0, "tighten after breakout")

    a = ex.get_price_level_alerts()[0]
    assert a["price"] == 82_500.0
    assert a["reasoning"] == "tighten after breakout"


def test_update_price_level_alert_keeps_direction_and_symbol(monkeypatch):
    """Spec §4.2 + AC-2: direction and symbol survive update unchanged."""
    from src.integrations.exchange.simulated import SimulatedExchange

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}
    ex = SimulatedExchange(config=config, db_engine=None, session_id="test-session", symbol="BTC/USDT:USDT")
    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )

    # new_price crosses the original level (would-trigger-immediately territory),
    # but direction must not auto-flip.
    ex.update_price_level_alert(alert_id, 81_900.0, "lower level")

    a = ex.get_price_level_alerts()[0]
    assert a["direction"] == "above"  # preserved
    assert a["symbol"] == "BTC/USDT:USDT"  # preserved


def test_update_price_level_alert_resets_created_at(monkeypatch):
    """Spec §4.2 + AC-2: created_at is rewritten to time.time() on update."""
    from src.integrations.exchange.simulated import SimulatedExchange

    # First add at t=1700000000
    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}
    ex = SimulatedExchange(config=config, db_engine=None, session_id="test-session", symbol="BTC/USDT:USDT")
    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )
    assert ex.get_price_level_alerts()[0]["created_at"] == 1700000000.0

    # Then update at t=1700005000 (5000s later)
    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700005000.0)
    ex.update_price_level_alert(alert_id, 82_500.0, "trail")

    assert ex.get_price_level_alerts()[0]["created_at"] == 1700005000.0


def test_update_price_level_alert_not_found_returns_false(monkeypatch):
    """Spec §5.1.2 + AC-3: unknown alert_id returns False; list unchanged."""
    from src.integrations.exchange.simulated import SimulatedExchange

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {"BTC/USDT:USDT": 3}
    ex = SimulatedExchange(config=config, db_engine=None, session_id="test-session", symbol="BTC/USDT:USDT")
    ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )
    before = list(ex.get_price_level_alerts())

    ok = ex.update_price_level_alert("deadbeef", 82_500.0, "trail")
    assert ok is False

    after = ex.get_price_level_alerts()
    assert after == before  # unchanged


# ============ Task 3: update_price_level_alert tool layer ============


@pytest.mark.asyncio
async def test_update_tool_return_string_shape(engine, session_with_row):
    """Spec §5.2 + AC-4: tool layer success returns the new single-direction shape:
    'Price level alert updated (id={alert_id}): {direction} {old_price} → {new_price} — "{new_reasoning}"'
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder
    from tests.test_tool_call_recorder import make_call, make_ctx, make_deps

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h structural high",
        "created_at": 1700000000.0,
    }]
    deps.exchange.update_price_level_alert.return_value = True

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="a3f2b8c1", new_price=82500.0,
            reasoning="trail up after breakout",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    # Shape: 'Price level alert updated (id=AAAA): above 82100.00 → 82500.00 — "..."'
    pattern = re.compile(
        r'^Price level alert updated \(id=[0-9a-f]{8}\): '
        r'(above|below) [\d.]+ → [\d.]+ '
        r'— ".+"$',
        re.DOTALL,
    )
    assert pattern.match(result), f"unexpected shape: {result!r}"

    # Anchored content: single id, preserved direction, new reasoning carried.
    assert "id=a3f2b8c1" in result
    assert "above 82100.00 → 82500.00" in result
    assert '— "trail up after breakout"' in result

    # New shape must NOT contain double direction or id transition.
    assert "→ above" not in result
    assert "id=a3f2b8c1 → id=" not in result

    # Exchange method called once with the new in-place signature.
    deps.exchange.update_price_level_alert.assert_called_once_with(
        "a3f2b8c1", 82500.0, "trail up after breakout",
    )


@pytest.mark.asyncio
async def test_update_tool_emits_biz_error_alert_not_found(engine, session_with_row):
    """Spec §5.2 + AC-5: tool layer on not-found emits biz_error alert_not_found
    and returns directive text. Behavior preserved from R2-Next-E."""
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder
    from src.storage.database import get_session
    from src.storage.models import ToolCall
    from sqlalchemy import select
    from tests.test_tool_call_recorder import make_call, make_ctx, make_deps

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = []  # absent

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="a3f2b8c1", new_price=82500.0,
            reasoning="trail",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Alert a3f2b8c1 not found" in result
    assert "add_price_level_alert" in result  # directive

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "alert_not_found"

    # No mutation
    deps.exchange.update_price_level_alert.assert_not_called()
