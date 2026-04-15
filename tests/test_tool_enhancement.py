# tests/test_tool_enhancement.py
"""Tests for tool enhancement (spec: 2026-04-14-tool-enhancement-design)."""
import pytest
from datetime import datetime, timezone
from src.integrations.exchange.base import Ticker


# --- Task 1: Foundation ---

def test_position_created_at_default():
    from src.integrations.exchange.base import Position
    p = Position("BTC/USDT:USDT", "long", 0.01, 65000.0, 10.0, 3, 55000.0)
    assert p.created_at is None  # default None


def test_position_created_at_set():
    from src.integrations.exchange.base import Position
    ts = datetime(2026, 4, 14, tzinfo=timezone.utc)
    p = Position("BTC/USDT:USDT", "long", 0.01, 65000.0, 10.0, 3, 55000.0, created_at=ts)
    assert p.created_at == ts


def test_price_alert_service_get_params():
    from src.services.price_alert import PriceAlertService
    svc = PriceAlertService("BTC/USDT:USDT", window_minutes=30, threshold_pct=3.0)
    assert svc.get_params() == (3.0, 30)


def test_price_alert_service_get_params_after_update():
    from src.services.price_alert import PriceAlertService
    svc = PriceAlertService("BTC/USDT:USDT", window_minutes=30, threshold_pct=3.0)
    svc.update_params(5.0, 60)
    assert svc.get_params() == (5.0, 60)


def test_base_exchange_alert_consolidation():
    """BaseExchange stores alert_service and delegates to it."""
    from unittest.mock import MagicMock
    from src.integrations.exchange.base import BaseExchange

    # Create a concrete subclass for testing
    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...

    ex = _TestExchange()

    # No alert service → get_alert_params returns None
    assert ex.get_alert_params() is None

    # Set alert service
    mock_svc = MagicMock()
    mock_svc.get_params.return_value = (5.0, 60)
    ex.set_alert_service(mock_svc)

    # get_alert_params delegates
    assert ex.get_alert_params() == (5.0, 60)

    # update_alert_params delegates
    ex.update_alert_params(3.0, 30)
    mock_svc.update_params.assert_called_once_with(3.0, 30)


def test_base_exchange_get_price_level_alerts():
    from src.integrations.exchange.base import BaseExchange

    class _TestExchange(BaseExchange):
        async def fetch_ticker(self, symbol): ...
        async def fetch_ohlcv(self, symbol, timeframe, limit=100): ...
        async def create_order(self, symbol, side, order_type, amount, price=None): ...
        async def fetch_balance(self): ...
        async def fetch_positions(self, symbol): ...
        async def set_leverage(self, symbol, leverage): ...
        def amount_to_precision(self, symbol, amount): ...
        async def close(self): ...
        async def fetch_order(self, order_id, symbol=None): ...
        async def fetch_open_orders(self, symbol): ...
        async def fetch_closed_orders(self, symbol, limit=20): ...
        async def cancel_order(self, order_id, symbol): ...

    ex = _TestExchange()
    assert ex.get_price_level_alerts() == []

    ex.add_price_level_alert(75000.0, "above", "BTC/USDT:USDT", "resistance")
    alerts = ex.get_price_level_alerts()
    assert len(alerts) == 1
    assert alerts[0]["price"] == 75000.0

    # Verify it's a copy (mutating returned list doesn't affect internal state)
    alerts.pop()
    assert len(ex.get_price_level_alerts()) == 1
