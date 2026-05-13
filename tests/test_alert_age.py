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
