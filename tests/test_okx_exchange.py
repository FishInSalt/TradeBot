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


@pytest.mark.asyncio
async def test_parse_fill_event_open_forces_pnl_none():
    """OKX V5 fillPnl='0' on opens — _parse_fill_event must override to pnl=None
    so downstream FIFO (src/services/metrics._collect_roundtrips_from_trade_actions)
    correctly treats it as an open (pnl IS NULL discriminator) instead of a
    breakeven close that would cascade to invariant_violations.

    Regression guard for PR #57 mini-fix I-1.
    """
    from src.integrations.exchange.okx import OKXExchange
    with patch("src.integrations.exchange.okx.ccxt"):
        ex = OKXExchange(api_key="x", secret="x", password="x",
                         symbol="BTC/USDT:USDT", sandbox=True)
        # synthesize an OPEN fill order_data — no reduceOnly / no algoId /
        # trigger_reason="market" / net_mode posSide → _infer_is_full_close=False
        order_data = {
            "id": "oid_open", "symbol": "BTC/USDT:USDT", "side": "buy", "type": "market",
            "average": 80000.0, "filled": 0.1,
            "fee": {"cost": 4.0},
            "info": {"pnl": "0", "posSide": "net"},  # fillPnl="0" per OKX V5 docs on opens
            "timestamp": 1234567890,
        }
        fill = await ex._parse_fill_event(order_data)
        assert fill.is_full_close is False
        assert fill.pnl is None, (
            f"open fill must have pnl=None (not 0.0) so FIFO treats it as open; got {fill.pnl}"
        )


@pytest.mark.asyncio
async def test_parse_fill_event_close_preserves_pnl():
    """OKX close fill: pnl from fillPnl preserved (positive guard companion to
    test_parse_fill_event_open_forces_pnl_none — confirms override only applies
    on opens, closes keep their pnl).
    """
    from src.integrations.exchange.okx import OKXExchange
    with patch("src.integrations.exchange.okx.ccxt"):
        ex = OKXExchange(api_key="x", secret="x", password="x",
                         symbol="BTC/USDT:USDT", sandbox=True)
        ex.register_close_order_entry("oid_close", 80000.0)
        order_data = {
            "id": "oid_close", "symbol": "BTC/USDT:USDT", "side": "sell", "type": "market",
            "average": 80100.0, "filled": 0.1,
            "fee": {"cost": 4.005},
            "info": {"pnl": "10.0", "reduceOnly": "true"},
            "timestamp": 1234567890,
        }
        fill = await ex._parse_fill_event(order_data)
        assert fill.is_full_close is True
        assert fill.pnl == 10.0


@pytest.mark.asyncio
async def test_okx_cancel_order_pops_close_entry_cache(monkeypatch):
    """cancel_order removes the order_id from _close_order_entry_cache."""
    from src.integrations.exchange.okx import OKXExchange
    with patch("src.integrations.exchange.okx.ccxt"):
        ex = OKXExchange(api_key="x", secret="x", password="x",
                         symbol="BTC/USDT:USDT", sandbox=True)
        ex.register_close_order_entry("oid1", 80000.0)

        async def fake_cancel(*a, **kw):
            return None
        monkeypatch.setattr(ex._client, "cancel_order", fake_cancel)

        await ex.cancel_order("oid1", "BTC/USDT:USDT", is_algo=False)
        assert "oid1" not in ex._close_order_entry_cache


def test_okx_close_entry_cache_ttl_sweep_drops_stale():
    """_sweep_close_entry_cache_ttl drops entries older than TTL_HOURS."""
    import time
    from src.integrations.exchange.okx import OKXExchange, _CLOSE_ENTRY_CACHE_TTL_SECONDS
    with patch("src.integrations.exchange.okx.ccxt"):
        ex = OKXExchange(api_key="x", secret="x", password="x",
                         symbol="BTC/USDT:USDT", sandbox=True)
        # inject stale entry
        ex._close_order_entry_cache["stale_oid"] = (80000.0, time.monotonic() - _CLOSE_ENTRY_CACHE_TTL_SECONDS - 1)
        ex._close_order_entry_cache["fresh_oid"] = (80000.0, time.monotonic())

        ex._sweep_close_entry_cache_ttl()
        assert "stale_oid" not in ex._close_order_entry_cache
        assert "fresh_oid" in ex._close_order_entry_cache
