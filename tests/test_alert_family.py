"""Iter w2r2-next-e Alert family treatment tests.

See: docs/superpowers/specs/2026-05-12-iter-w2r2-next-e-alert-family-design.md
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from src.storage.database import get_session
from src.storage.models import ToolCall
from tests.test_tool_call_recorder import make_call, make_ctx, make_deps


# ============ Task 1: _lookup_alert helper ============

def test_lookup_alert_returns_dict_when_present():
    """_lookup_alert returns the full alert dict when id matches."""
    from src.agent.tools_execution import _lookup_alert

    exchange = MagicMock()
    exchange.get_price_level_alerts.return_value = [
        {"id": "a3f2b8c1", "price": 82100.0, "direction": "above",
         "symbol": "BTC/USDT:USDT", "reasoning": "4h high"},
        {"id": "d7c2e9f4", "price": 81720.0, "direction": "below",
         "symbol": "BTC/USDT:USDT", "reasoning": "1h low"},
    ]

    result = _lookup_alert(exchange, "a3f2b8c1")
    assert result == {
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h high",
    }


def test_lookup_alert_returns_none_when_absent():
    """_lookup_alert returns None when id not in the list."""
    from src.agent.tools_execution import _lookup_alert

    exchange = MagicMock()
    exchange.get_price_level_alerts.return_value = [
        {"id": "a3f2b8c1", "price": 82100.0, "direction": "above",
         "symbol": "BTC/USDT:USDT", "reasoning": "4h high"},
    ]

    result = _lookup_alert(exchange, "ffffffff")
    assert result is None
