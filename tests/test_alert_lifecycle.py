"""Iter 6 alert lifecycle tests: cancel tool + close path batch clearance."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._fixtures import (
    make_fill_event,
    make_okx_exchange,
    make_sim_exchange,
    make_ticker,
)


# ============ Sim partial close contract protection ============

@pytest.mark.asyncio
async def test_sim_partial_close_does_not_clear_alert():
    """Contract guarantee: future partial close tool must not silent-clear alerts.

    Manually constructs partial close (amount < pos.contracts) and verifies
    is_full_close=False so _dispatch_fill_event won't clear alerts.
    See spec §3.4 + §6.3.
    """
    sim = make_sim_exchange(initial_balance=10000.0)

    # Open position via create_order + _process_tick (market order needs tick to fill)
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))

    # Verify position created
    assert "BTC/USDT:USDT" in sim._positions
    pos = sim._positions["BTC/USDT:USDT"]
    initial_contracts = pos.contracts
    assert initial_contracts > 0

    # Add a price-level alert
    alert_id = sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert alert_id is not None
    assert len(sim.get_price_level_alerts()) == 1

    # Manually invoke _close_position_core with partial amount (50% of position)
    partial_amount = initial_contracts * 0.5
    sim._close_position_core(
        "BTC/USDT:USDT", pos.side, partial_amount, 50000.0, pnl_cap=False,
    )

    # Verify position still exists (partial close)
    assert "BTC/USDT:USDT" in sim._positions
    assert sim._positions["BTC/USDT:USDT"].contracts == pytest.approx(initial_contracts * 0.5)

    # is_full_close would be False (since symbol still in dict) —
    # which means _dispatch_fill_event would NOT clear alerts.
    is_full_close = "BTC/USDT:USDT" not in sim._positions
    assert is_full_close is False

    # Alerts must remain
    assert len(sim.get_price_level_alerts()) == 1


# ============ OKX _infer_is_full_close three-source fusion ============

def test_okx_parse_fill_event_is_full_close_reduce_only():
    """Signal 1: info.reduceOnly=True → is_full_close=True."""
    okx = make_okx_exchange()
    info = {"reduceOnly": True, "posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "market") is True


def test_okx_parse_fill_event_is_full_close_reduce_only_string():
    """Signal 1: info.reduceOnly='true' string variant."""
    okx = make_okx_exchange()
    info = {"reduceOnly": "true", "posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "market") is True


def test_okx_parse_fill_event_is_full_close_trigger_reason_stop():
    """Signal 2: trigger_reason='stop' → is_full_close=True."""
    okx = make_okx_exchange()
    info = {"posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "stop") is True


def test_okx_parse_fill_event_is_full_close_trigger_reason_tp():
    """Signal 2: trigger_reason='take_profit' → is_full_close=True."""
    okx = make_okx_exchange()
    info = {"posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "take_profit") is True


def test_okx_parse_fill_event_is_full_close_trigger_reason_liq():
    """Signal 2: trigger_reason='liquidation' → is_full_close=True
    (defensive: _TRIGGER_REASON_MAP currently doesn't produce this)."""
    okx = make_okx_exchange()
    info = {"posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "liquidation") is True


@pytest.mark.skip(reason="hedge mode path, unreachable in net mode (okx.py:183)")
def test_okx_parse_fill_event_is_full_close_pos_side_long_sell():
    """Signal 3: posSide='long' + side='sell' → is_full_close=True.
    Currently unreachable: project forces net_mode (okx.py:183) so posSide='net'.
    Remove skip when hedge mode support is added.
    """
    okx = make_okx_exchange()
    info = {"posSide": "long"}
    assert okx._infer_is_full_close(info, "sell", "market") is True


@pytest.mark.skip(reason="hedge mode path, unreachable in net mode (okx.py:183)")
def test_okx_parse_fill_event_is_full_close_pos_side_short_buy():
    """Signal 3: posSide='short' + side='buy' → is_full_close=True.
    Currently unreachable: project forces net_mode."""
    okx = make_okx_exchange()
    info = {"posSide": "short"}
    assert okx._infer_is_full_close(info, "buy", "market") is True


def test_okx_parse_fill_event_is_full_close_net_mode_with_reduce_only():
    """net mode boundary: posSide='net' + reduceOnly=True → is_full_close=True.
    Validates signal 1 still works when signal 3 is unreachable."""
    okx = make_okx_exchange()
    info = {"reduceOnly": True, "posSide": "net"}
    assert okx._infer_is_full_close(info, "sell", "market") is True


def test_okx_parse_fill_event_open_no_close_signals():
    """Open fill: no reduceOnly, no close-trigger, posSide='net', no algoId → is_full_close=False."""
    okx = make_okx_exchange()
    info = {"posSide": "net", "reduceOnly": False, "algoId": ""}
    assert okx._infer_is_full_close(info, "buy", "market") is False


def test_okx_parse_fill_event_is_full_close_algo_id_non_empty():
    """Signal 4 (NEW): info.algoId non-empty → is_full_close=True.

    Task 0 实测 1B/1C: SL/TP triggered fills have algoId non-empty even though
    ordType='limit' and reduceOnly='false'. algoId is the OKX-explicit close
    signal for algo paths (SL/TP/conditional/OCO).
    """
    okx = make_okx_exchange()
    # Mimics 1B/1C real fixture: ordType=limit (signal 2 miss), reduceOnly=false
    # (signal 1 miss), posSide=net (signal 3 miss), but algoId non-empty
    info = {
        "posSide": "net",
        "reduceOnly": "false",
        "ordType": "limit",
        "algoId": "3516926949270786048",  # real value from 1C fixture
        "algoClOrdId": "6b9ad766b55dBCDE5cd2873d775bb62b",
    }
    assert okx._infer_is_full_close(info, "sell", "unknown") is True


def test_okx_parse_fill_event_open_with_empty_algo_id_string():
    """Signal 4 boundary: algoId="" (empty string, not non-empty) → False.

    Defends against treating "" as truthy by accident.
    """
    okx = make_okx_exchange()
    info = {"posSide": "net", "reduceOnly": False, "algoId": ""}
    assert okx._infer_is_full_close(info, "buy", "market") is False


# ============ OKX _watch_orders_loop integration test ============

@pytest.mark.skip(reason="depends on Task 6 _dispatch_fill_event impl")
@pytest.mark.asyncio
async def test_okx_dispatch_fill_event_clears_via_loop():
    """Integration: _watch_orders_loop receives close fill push, _parse_fill_event
    constructs is_full_close=True, _dispatch_fill_event clears stale alert.

    Uses 1D fixture (market close WITH params={"reduceOnly": True}, signal 1
    reduceOnly='true' echoed by OKX). NOT 1A — that fixture has reduceOnly=false
    and would fail the is_full_close=True assertion (per spec §4.3.1.1 outcome).
    1A path is covered by Task 4/7 sim end-to-end tests; this test verifies
    the OKX-specific dispatch path post-Remediation A.
    """
    okx = make_okx_exchange()

    # Add a stale alert
    okx._price_level_alerts.append({
        "id": "test-alert-1",
        "symbol": "BTC/USDT:USDT",
        "price": 51000.0,
        "direction": "above",
        "reasoning": "stale",
    })

    # Load 1D fixture (market close with reduceOnly=true echoed)
    fixture_path = Path("tests/fixtures/okx_watch_orders_market_close_reduce_only.json")
    with fixture_path.open() as f:
        order_data = json.load(f)

    # Mock _fetch_order_with_algo_fallback to avoid REST call
    okx._fetch_order_with_algo_fallback = AsyncMock(
        return_value={"info": {"pnl": "1.0"}}
    )

    # Parse fill event
    fill = await okx._parse_fill_event(order_data)

    # Verify is_full_close=True per signal 1 (reduceOnly='true' echoed by OKX)
    assert fill.is_full_close is True
    assert fill.symbol == "BTC/USDT:USDT"

    # Dispatch and verify alert cleared (no callback registered)
    await okx._dispatch_fill_event(fill)

    assert len(okx._price_level_alerts) == 0
