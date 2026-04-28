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


# ============ Task 5b: Remediation A — params kwarg + reduceOnly propagation ============

@pytest.mark.asyncio
async def test_sim_create_order_accepts_params_kwarg():
    """Sim accepts params kwarg without crashing (transparent ignore)."""
    sim = make_sim_exchange()
    order = await sim.create_order(
        "BTC/USDT:USDT", "buy", "market", 0.01,
        params={"reduceOnly": True, "anything": "else"},
    )
    assert order is not None  # didn't crash on kwarg


@pytest.mark.asyncio
async def test_okx_create_order_merges_caller_params():
    """OKX override merges caller params into internal {tdMode: isolated} dict."""
    from unittest.mock import AsyncMock
    okx = make_okx_exchange()
    okx._client = AsyncMock()
    okx._client.create_order = AsyncMock(return_value={
        "id": "test-1", "symbol": "BTC/USDT:USDT", "side": "sell",
        "type": "market", "amount": 0.01, "price": None, "status": "open",
        "info": {"sz": "0.01"},
    })
    await okx.create_order(
        "BTC/USDT:USDT", "sell", "market", 0.01,
        params={"reduceOnly": True},
    )
    # Verify _client.create_order called with merged params
    call_kwargs = okx._client.create_order.call_args.kwargs
    assert call_kwargs["params"]["tdMode"] == "isolated"
    assert call_kwargs["params"]["reduceOnly"] is True


@pytest.mark.asyncio
async def test_okx_create_order_no_caller_params_uses_defaults():
    """OKX override with params=None → just {tdMode: isolated} (no reduceOnly)."""
    from unittest.mock import AsyncMock
    okx = make_okx_exchange()
    okx._client = AsyncMock()
    okx._client.create_order = AsyncMock(return_value={
        "id": "test-1", "symbol": "BTC/USDT:USDT", "side": "buy",
        "type": "market", "amount": 0.01, "price": None, "status": "open",
        "info": {"sz": "0.01"},
    })
    await okx.create_order("BTC/USDT:USDT", "buy", "market", 0.01)
    call_kwargs = okx._client.create_order.call_args.kwargs
    assert call_kwargs["params"] == {"tdMode": "isolated"}
    assert "reduceOnly" not in call_kwargs["params"]


@pytest.mark.asyncio
async def test_close_position_passes_reduce_only():
    """tools_execution.py:close_position passes params={'reduceOnly': True}
    to exchange.create_order. This is the Remediation A actuation point."""
    from unittest.mock import AsyncMock, MagicMock
    from src.agent.tools_execution import close_position
    from src.integrations.exchange.base import Position

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.db_engine = None
    deps.session_id = "test-session"
    deps.exchange = AsyncMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(symbol="BTC/USDT:USDT", side="long", contracts=0.01,
                 entry_price=50000.0, unrealized_pnl=0.0, leverage=10,
                 liquidation_price=45000.0),
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    deps.exchange.create_order = AsyncMock(return_value=MagicMock(id="order-1"))
    # Bypass _check_approval (returns True if no human gate)
    from unittest.mock import patch
    with patch("src.agent.tools_execution._check_approval",
               new=AsyncMock(return_value=True)):
        result = await close_position(deps, reasoning="test close")

    # Assert reduceOnly was passed
    call_kwargs = deps.exchange.create_order.call_args.kwargs
    assert call_kwargs.get("params") == {"reduceOnly": True}, \
        f"close_position must pass params={{'reduceOnly': True}}, got {call_kwargs.get('params')}"


@pytest.mark.asyncio
async def test_okx_fill_event_reduce_only_true_with_remediation_a():
    """End-to-end: OKX _infer_is_full_close returns True when fill event has
    info.reduceOnly='true' (the result of Remediation A). Validates 1D fixture."""
    okx = make_okx_exchange()
    # Mimics 1D fixture: market close with reduceOnly=true echoed back
    info = {
        "posSide": "net",
        "reduceOnly": "true",  # OKX echoed because caller passed it
        "ordType": "market",
        "algoId": "",  # market path, no algoId
    }
    assert okx._infer_is_full_close(info, "sell", "market") is True


# ============ clear_level_alerts_by_symbol helper ============

def test_clear_level_alerts_by_symbol_filters_correct_symbol():
    """Multi-symbol mix: clears only target symbol, returns count cleared."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "BTC/USDT:USDT", "price": 50000.0, "direction": "above"},
        {"id": "a2", "symbol": "ETH/USDT:USDT", "price": 3000.0, "direction": "above"},
        {"id": "a3", "symbol": "BTC/USDT:USDT", "price": 51000.0, "direction": "above"},
    ]
    cleared = sim.clear_level_alerts_by_symbol("BTC/USDT:USDT")
    assert cleared == 2
    assert len(sim._price_level_alerts) == 1
    assert sim._price_level_alerts[0]["symbol"] == "ETH/USDT:USDT"


def test_clear_level_alerts_by_symbol_returns_zero_when_empty():
    """Symbol with no alerts → returns 0, list unchanged."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "ETH/USDT:USDT", "price": 3000.0, "direction": "above"},
    ]
    cleared = sim.clear_level_alerts_by_symbol("BTC/USDT:USDT")
    assert cleared == 0
    assert len(sim._price_level_alerts) == 1


# ============ _dispatch_fill_event SRP units ============

@pytest.mark.asyncio
async def test_dispatch_fill_event_clears_on_full_close():
    """is_full_close=True → alert cleared + callback invoked."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "BTC/USDT:USDT", "price": 51000.0, "direction": "above"},
    ]
    callback_called = []

    async def cb(fill):
        callback_called.append(fill)
    sim._fill_callback = cb

    fill = make_fill_event(symbol="BTC/USDT:USDT", is_full_close=True)
    await sim._dispatch_fill_event(fill)

    assert len(sim._price_level_alerts) == 0
    assert len(callback_called) == 1


@pytest.mark.asyncio
async def test_dispatch_fill_event_skips_clear_when_not_full_close():
    """is_full_close=False → alert preserved + callback invoked."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "BTC/USDT:USDT", "price": 51000.0, "direction": "above"},
    ]
    callback_called = []

    async def cb(fill):
        callback_called.append(fill)
    sim._fill_callback = cb

    fill = make_fill_event(symbol="BTC/USDT:USDT", is_full_close=False)
    await sim._dispatch_fill_event(fill)

    assert len(sim._price_level_alerts) == 1  # preserved
    assert len(callback_called) == 1


@pytest.mark.asyncio
async def test_dispatch_fill_event_callback_failure_isolated(caplog):
    """Callback raises → logger.exception called, exception NOT propagated."""
    sim = make_sim_exchange()

    async def failing_cb(fill):
        raise RuntimeError("simulated failure")
    sim._fill_callback = failing_cb

    fill = make_fill_event(is_full_close=False)
    # Must NOT raise
    await sim._dispatch_fill_event(fill)

    assert any("Fill callback failed" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_dispatch_fill_event_no_callback_registered():
    """No callback registered → only clears alert, no error."""
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "BTC/USDT:USDT", "price": 51000.0, "direction": "above"},
    ]
    sim._fill_callback = None

    fill = make_fill_event(symbol="BTC/USDT:USDT", is_full_close=True)
    # Must NOT raise
    await sim._dispatch_fill_event(fill)

    assert len(sim._price_level_alerts) == 0  # cleared
