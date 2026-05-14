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
        async def fetch_open_interest_history(self, symbol, period="1h", limit=26): ...
        async def fetch_long_short_ratio(self, symbol): ...

        async def fetch_order_book(self, symbol, depth=20):
            from src.integrations.exchange.base import OrderBook, OrderBookLevel
            return OrderBook(symbol=symbol, bids=[OrderBookLevel(100.0, 1.0)], asks=[OrderBookLevel(101.0, 1.0)], timestamp=0)

        async def fetch_trades(self, symbol, limit=500):
            return []

        async def get_contract_size(self, symbol):
            return 1.0

        async def get_mark_price(self, symbol):
            return 0.0

    return _TestExchange()


# --- Tests ---

def test_price_level_alert_info_fields():
    info = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=58000.0,
        direction="below", current_price=57900.0,
        reasoning="Key support level", timestamp=1712534400000,
        alert_id="testid01",
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


@pytest.mark.asyncio
async def test_get_active_alerts_displays_real_uuid():
    """R2-2 T1a: get_active_alerts 输出必须主显真实 uuid 且与位置索引同行，
    保证 agent 跨 cycle 能复制 id=<uuid> 真值给 cancel_price_level_alert。

    sim #4 根因：原输出仅有位置索引 `#N`，跨 cycle agent 无法获取 uuid → 100% cancel 失败。
    """
    from unittest.mock import MagicMock
    from src.agent.tools_perception import get_active_alerts

    exchange = _make_exchange()
    aid_a = exchange.add_price_level_alert(58000.0, "below", "BTC/USDT:USDT", "support")
    aid_b = exchange.add_price_level_alert(62000.0, "above", "BTC/USDT:USDT", "resistance")
    assert aid_a is not None and aid_b is not None

    deps = MagicMock()
    deps.exchange = exchange

    result = await get_active_alerts(deps)

    # 主显真实 uuid（agent 跨 cycle 复制 id 用）
    assert f"id={aid_a}" in result, f"uuid {aid_a!r} not in output: {result!r}"
    assert f"id={aid_b}" in result, f"uuid {aid_b!r} not in output: {result!r}"
    # 保留位置索引（向后兼容现有显示习惯）
    assert "#1" in result and "#2" in result
    # 锁定显示格式：#N 与 id= 必须同行（防未来 reformatter 把 id 拆下一行 / 删位置索引）
    assert any(f"#1 (id={aid_a})" in line for line in result.splitlines()), \
        f"expected '#1 (id={aid_a})' on a single line, got: {result!r}"
    assert any(f"#2 (id={aid_b})" in line for line in result.splitlines()), \
        f"expected '#2 (id={aid_b})' on a single line, got: {result!r}"
