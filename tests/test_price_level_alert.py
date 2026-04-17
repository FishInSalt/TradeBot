# tests/test_price_level_alert.py
from __future__ import annotations

import pytest
from src.integrations.exchange.base import BaseExchange, PriceLevelAlertInfo


# --- Helper: concrete BaseExchange subclass for testing ---

def _make_exchange():
    """Create a minimal concrete BaseExchange for testing price level methods."""

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
        async def fetch_funding_rate(self, symbol): ...
        async def fetch_open_interest(self, symbol): ...
        async def fetch_long_short_ratio(self, symbol): ...

    return _TestExchange()


# --- Tests ---

def test_price_level_alert_info_fields():
    info = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=58000.0,
        direction="below", current_price=57900.0,
        reasoning="Key support level", timestamp=1712534400000,
    )
    assert info.direction == "below"
    assert info.target_price == 58000.0


def test_add_price_level_alert():
    exchange = _make_exchange()
    alert_id = exchange.add_price_level_alert(58000.0, "below", "BTC/USDT:USDT", "support")
    assert isinstance(alert_id, str)
    assert len(exchange._price_level_alerts) == 1


def test_check_price_levels_triggers_below():
    exchange = _make_exchange()
    exchange.add_price_level_alert(58000.0, "below", "BTC/USDT:USDT", "support")
    triggered = exchange._check_price_levels(57900.0, 1000)
    assert len(triggered) == 1
    assert triggered[0].direction == "below"
    assert triggered[0].current_price == 57900.0
    # One-shot: removed from list
    assert len(exchange._price_level_alerts) == 0


def test_check_price_levels_triggers_above():
    exchange = _make_exchange()
    exchange.add_price_level_alert(62000.0, "above", "BTC/USDT:USDT", "resistance")
    triggered = exchange._check_price_levels(62100.0, 1000)
    assert len(triggered) == 1
    assert triggered[0].direction == "above"


def test_check_price_levels_no_trigger():
    exchange = _make_exchange()
    exchange.add_price_level_alert(58000.0, "below", "BTC/USDT:USDT", "support")
    triggered = exchange._check_price_levels(59000.0, 1000)
    assert len(triggered) == 0
    assert len(exchange._price_level_alerts) == 1


def test_check_price_levels_multiple_alerts():
    exchange = _make_exchange()
    exchange.add_price_level_alert(58000.0, "below", "BTC/USDT:USDT", "support")
    exchange.add_price_level_alert(55000.0, "below", "BTC/USDT:USDT", "deep support")
    exchange.add_price_level_alert(62000.0, "above", "BTC/USDT:USDT", "resistance")
    # Price at 57000 triggers first below but not second or above
    triggered = exchange._check_price_levels(57000.0, 1000)
    assert len(triggered) == 1
    assert len(exchange._price_level_alerts) == 2


def test_remove_price_level_alert():
    exchange = _make_exchange()
    alert_id = exchange.add_price_level_alert(58000.0, "below", "BTC/USDT:USDT", "support")
    assert exchange.remove_price_level_alert(alert_id) is True
    assert len(exchange._price_level_alerts) == 0
    assert exchange.remove_price_level_alert("nonexistent") is False


def test_add_price_level_alert_limit():
    exchange = _make_exchange()
    for i in range(20):
        result = exchange.add_price_level_alert(50000.0 + i * 100, "above", "BTC/USDT:USDT", f"level {i}")
        assert isinstance(result, str)
    # 21st should return None
    result = exchange.add_price_level_alert(99999.0, "above", "BTC/USDT:USDT", "too many")
    assert result is None


def test_add_price_level_alert_immediate_warning():
    exchange = _make_exchange()
    exchange._latest_price = 57000.0
    alert_id = exchange.add_price_level_alert(58000.0, "below", "BTC/USDT:USDT", "support")
    # Alert is added (not blocked), but caller should check _latest_price
    assert alert_id is not None
    assert len(exchange._price_level_alerts) == 1
