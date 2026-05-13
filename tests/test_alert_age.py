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
