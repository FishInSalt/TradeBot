"""Tests for OKXExchange._close_order_entry_cache and register_close_order_entry."""
import time
from unittest.mock import patch

import pytest


def test_okx_close_order_entry_cache_initialized_empty():
    """OKXExchange.__init__ creates empty _close_order_entry_cache dict."""
    with patch("src.integrations.exchange.okx.ccxt"):
        from src.integrations.exchange.okx import OKXExchange
        ex = OKXExchange(api_key="x", secret="x", password="x",
                         symbol="BTC/USDT:USDT", sandbox=True)
        assert ex._close_order_entry_cache == {}


def test_okx_register_close_order_entry_writes_to_cache():
    """register_close_order_entry stores (entry_price, monotonic_ts) tuple."""
    with patch("src.integrations.exchange.okx.ccxt"):
        from src.integrations.exchange.okx import OKXExchange
        ex = OKXExchange(api_key="x", secret="x", password="x",
                         symbol="BTC/USDT:USDT", sandbox=True)
        t_before = time.monotonic()
        ex.register_close_order_entry("order123", 80000.0)
        t_after = time.monotonic()

        assert "order123" in ex._close_order_entry_cache
        entry, ts = ex._close_order_entry_cache["order123"]
        assert entry == 80000.0
        assert t_before <= ts <= t_after


@pytest.mark.asyncio
async def test_parse_fill_event_pops_entry_price_from_cache():
    """OKX close fill event: entry_price filled from cache by order_id."""
    from src.integrations.exchange.okx import OKXExchange
    with patch("src.integrations.exchange.okx.ccxt"):
        ex = OKXExchange(api_key="x", secret="x", password="x",
                         symbol="BTC/USDT:USDT", sandbox=True)
        ex.register_close_order_entry("oid1", 80000.0)

        # synthesize close fill order_data (CCXT shape)
        order_data = {
            "id": "oid1", "symbol": "BTC/USDT:USDT", "side": "sell", "type": "market",
            "average": 80100.0, "filled": 0.1,
            "fee": {"cost": 4.005},
            "info": {"pnl": "10.0", "reduceOnly": "true"},
            "timestamp": 1234567890,
        }
        fill = await ex._parse_fill_event(order_data)
        assert fill.entry_price == 80000.0
        assert "oid1" not in ex._close_order_entry_cache  # popped


@pytest.mark.asyncio
async def test_parse_fill_event_cache_miss_yields_none_entry_price():
    """OKX close fill: cache miss → entry_price=None (graceful degrade)."""
    from src.integrations.exchange.okx import OKXExchange
    with patch("src.integrations.exchange.okx.ccxt"):
        ex = OKXExchange(api_key="x", secret="x", password="x",
                         symbol="BTC/USDT:USDT", sandbox=True)
        # no register call — cache empty
        order_data = {
            "id": "oid_unknown", "symbol": "BTC/USDT:USDT", "side": "sell", "type": "market",
            "average": 80100.0, "filled": 0.1,
            "fee": {"cost": 4.005},
            "info": {"pnl": "10.0", "reduceOnly": "true"},
            "timestamp": 1234567890,
        }
        fill = await ex._parse_fill_event(order_data)
        assert fill.entry_price is None
