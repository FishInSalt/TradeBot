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
