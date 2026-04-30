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
from sqlalchemy import select
from src.storage.database import get_session
from src.storage.models import ToolCall
from tests.test_tool_call_recorder import make_deps, make_ctx, make_call


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


# ============ OKX dispatch-post-parse integration test ============

@pytest.mark.asyncio
async def test_okx_dispatch_fill_event_clears_post_parse():
    """Integration: _parse_fill_event on a close fill produces is_full_close=True,
    then _dispatch_fill_event clears stale alert. Exercises the post-parse half
    of the _watch_orders_loop dispatch path (parse → dispatch); the loop iteration
    itself is not exercised here.

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


# ============ Sim end-to-end close fill → alert clearance ============

@pytest.mark.asyncio
async def test_sim_market_close_triggers_alert_clear():
    """Open + add alert + market close → alert auto-cleared."""
    sim = make_sim_exchange()

    # Open position via create_order + _process_tick
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))
    assert "BTC/USDT:USDT" in sim._positions

    # Add alert
    alert_id = sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert alert_id is not None
    assert len(sim.get_price_level_alerts()) == 1

    # Market close
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="sell", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0, timestamp=1700000001000))

    # Alert cleared via _dispatch_fill_event
    assert "BTC/USDT:USDT" not in sim._positions
    assert len(sim.get_price_level_alerts()) == 0


@pytest.mark.asyncio
async def test_sim_conditional_fill_triggers_alert_clear():
    """Open + add alert + SL trigger → alert auto-cleared."""
    sim = make_sim_exchange()

    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))
    assert "BTC/USDT:USDT" in sim._positions

    # Set SL via conditional (stop) order — sim forces full position size
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="sell", order_type="stop", amount=0.01, price=49000.0,
    )

    # Add alert
    sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert len(sim.get_price_level_alerts()) == 1

    # Trigger SL via price drop (below 49000 trigger)
    await sim._process_tick(make_ticker(last=48900.0, timestamp=1700000001000))

    assert "BTC/USDT:USDT" not in sim._positions
    assert len(sim.get_price_level_alerts()) == 0


@pytest.mark.asyncio
async def test_sim_liquidation_triggers_alert_clear():
    """Open + add alert + liquidation → alert auto-cleared."""
    sim = make_sim_exchange(initial_balance=100.0)  # small balance to enable liquidation
    await sim.set_leverage("BTC/USDT:USDT", 100)

    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))
    assert "BTC/USDT:USDT" in sim._positions

    sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert len(sim.get_price_level_alerts()) == 1

    # Crash price to trigger liquidation (100x leverage → ~1% drop kills it)
    await sim._process_tick(make_ticker(last=40000.0, timestamp=1700000001000))

    assert "BTC/USDT:USDT" not in sim._positions
    assert len(sim.get_price_level_alerts()) == 0


@pytest.mark.asyncio
async def test_sim_open_fill_does_not_clear_alert():
    """Open fill (is_full_close=False) → alert preserved.

    Open fills don't create stale alerts; the alerts at structural levels
    just placed BEFORE opening should remain valid post-open.
    """
    sim = make_sim_exchange()

    # Add alert FIRST (before opening)
    sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert len(sim.get_price_level_alerts()) == 1

    # Open fill via create_order + _process_tick
    await sim.create_order(
        symbol="BTC/USDT:USDT", side="buy", order_type="market", amount=0.01,
    )
    await sim._process_tick(make_ticker(last=50000.0))
    assert "BTC/USDT:USDT" in sim._positions

    # Alert preserved
    assert len(sim.get_price_level_alerts()) == 1


# ============ Order semantics: callback observes post-clear state ============

@pytest.mark.asyncio
async def test_dispatch_fill_event_callback_observes_post_clear_state():
    """Order semantics: callback runs AFTER alert hygiene.

    Verifies the clear-before-callback contract documented in
    BaseExchange._dispatch_fill_event docstring. Callback inspects the
    alerts list at invocation time; should observe post-hygiene state
    (filtered list).
    """
    sim = make_sim_exchange()
    sim._price_level_alerts = [
        {"id": "a1", "symbol": "BTC/USDT:USDT", "price": 51000.0, "direction": "above"},
    ]
    captured_alerts = []

    async def cb(fill):
        captured_alerts.append(list(sim._price_level_alerts))
    sim._fill_callback = cb

    fill = make_fill_event(symbol="BTC/USDT:USDT", is_full_close=True)
    await sim._dispatch_fill_event(fill)

    # Callback should have seen post-hygiene (empty) alert list
    assert len(captured_alerts) == 1
    assert captured_alerts[0] == []


# ============ cancel_price_level_alert tool ============

@pytest.mark.asyncio
async def test_cancel_price_level_alert_tool_success():
    """Successful cancel: returns success message + records action."""
    from src.agent.tools_execution import cancel_price_level_alert

    sim = make_sim_exchange()
    alert_id = sim.add_price_level_alert(
        price=51000.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="test",
    )
    assert alert_id is not None

    # Build minimal TradingDeps mock
    # NOTE: db_engine=None is safe — _record_action source-verified to early-return
    # at tools_execution.py:19 (`if deps.db_engine is None: return`). No DB I/O occurs.
    deps = MagicMock()
    deps.exchange = sim
    deps.db_engine = None
    deps.session_id = "test-session"

    result = await cancel_price_level_alert(deps, alert_id, "no longer needed")

    assert result == f"Price level alert cancelled (id={alert_id})"
    assert len(sim.get_price_level_alerts()) == 0


@pytest.mark.asyncio
async def test_cancel_price_level_alert_tool_invalid_format():
    """R2-2 T2: 协议错（agent 传非 8-char hex）→ format 错误信息引导查看 get_active_alerts。

    sim #4 实证：agent 100% 传 `#1` / `"1"` / `"11"` 等 enumerate 索引误读，
    永远匹配不到 uuid。原统一错误信息把"格式错"和"已触发"合并 → agent 诊断错方向。
    """
    from src.agent.tools_execution import cancel_price_level_alert

    sim = make_sim_exchange()
    deps = MagicMock()
    deps.exchange = sim
    deps.db_engine = None
    deps.session_id = "test-session"

    # 三种典型协议错输入（含 # / 含 dash / 长度错）
    for bad_id in ["#1", "nonexistent-id", "1"]:
        result = await cancel_price_level_alert(deps, bad_id, "test")
        assert "Invalid alert_id format" in result, f"协议错信息缺失 for {bad_id!r}: {result!r}"
        assert "8-char hex" in result, f"格式提示缺失 for {bad_id!r}: {result!r}"
        assert "get_active_alerts" in result, f"id 来源引导缺失 for {bad_id!r}: {result!r}"
        assert repr(bad_id) in result or bad_id in result, f"用户输入回显缺失 for {bad_id!r}: {result!r}"


@pytest.mark.asyncio
async def test_cancel_price_level_alert_tool_state_not_found():
    """R2-2 T3: 状态错（合法 8-char hex 但 sim 中不存在）→ already triggered or expired。"""
    from src.agent.tools_execution import cancel_price_level_alert

    sim = make_sim_exchange()
    deps = MagicMock()
    deps.exchange = sim
    deps.db_engine = None
    deps.session_id = "test-session"

    # 合法 8-char hex 格式（防真碰撞，不与任何活跃 uuid 重合 — sim 中无 alerts）
    fake_id = "deadbeef"
    result = await cancel_price_level_alert(deps, fake_id, "test")

    assert "already triggered or expired" in result, f"状态错信息缺失: {result!r}"
    assert fake_id in result, f"alert_id 回显缺失: {result!r}"
    # 状态错不应混入"格式错"提示
    assert "Invalid alert_id format" not in result
    assert "8-char hex" not in result


# ============ display.py is_tool_error coverage ============

def test_is_tool_error_cancel_alert_success_returns_false():
    """Success message with prefix → is_tool_error returns False."""
    from src.cli.display import is_tool_error

    result = is_tool_error(
        tool_name="cancel_price_level_alert",
        content="Price level alert cancelled (id=abc12345)",
        outcome="success",
    )
    assert result is False


def test_is_tool_error_cancel_alert_invalid_format_returns_true():
    """R2-2 T4: 协议错信息不命中 success prefix → is_tool_error=True (business rejection)。"""
    from src.cli.display import is_tool_error

    result = is_tool_error(
        tool_name="cancel_price_level_alert",
        content="Invalid alert_id format: '#1'. Expected 8-char hex (e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids.",
        outcome="success",
    )
    assert result is True


def test_is_tool_error_cancel_alert_state_not_found_returns_true():
    """R2-2 T5: 状态错（已触发/过期）信息不命中 success prefix → is_tool_error=True。"""
    from src.cli.display import is_tool_error

    result = is_tool_error(
        tool_name="cancel_price_level_alert",
        content="Alert deadbeef already triggered or expired",
        outcome="success",
    )
    assert result is True


def test_registered_tool_names_includes_cancel_alert():
    """Explicit assertion that cancel_price_level_alert is in REGISTERED_TOOL_NAMES.

    Redundant with test_trader_agent.py drift guard (which catches missing
    registration via agent introspection), but provides explicit naming so
    future readers can grep "includes_cancel_alert" to find this contract.
    """
    from src.agent.trader import REGISTERED_TOOL_NAMES

    assert "cancel_price_level_alert" in REGISTERED_TOOL_NAMES
    # Also verify position adjacent to add_price_level_alert (add/cancel pairing per §4.7)
    add_idx = REGISTERED_TOOL_NAMES.index("add_price_level_alert")
    cancel_idx = REGISTERED_TOOL_NAMES.index("cancel_price_level_alert")
    assert cancel_idx == add_idx + 1, \
        f"cancel_price_level_alert should be immediately after add_price_level_alert"


# ============ R2-4 T3: end-to-end biz_error instrumentation ============


@pytest.mark.asyncio
async def test_set_price_alert_invalid_threshold_records_biz_error(engine, session_with_row):
    """端到端: set_price_alert 传 0.05 越界 → tool_calls 行 status='biz_error'."""
    from src.agent.tools_execution import set_price_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_alert_params.return_value = (1.0, 60)  # alerts enabled

    async def handler(args):
        return await set_price_alert(deps, threshold_pct=0.05, window_minutes=60, reasoning="t")

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("set_price_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Invalid threshold_pct" in result
    assert "0.05" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_threshold_range"


@pytest.mark.asyncio
async def test_cancel_price_level_alert_invalid_format_records_biz_error(engine, session_with_row):
    """端到端: cancel_price_level_alert 传 '#1' (非 8-char hex) → biz_error 'invalid_alert_id_format'."""
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        return await cancel_price_level_alert(deps, alert_id="#1", reasoning="t")

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Invalid alert_id format" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_alert_id_format"


@pytest.mark.asyncio
async def test_cancel_price_level_alert_not_found_records_biz_error(engine, session_with_row):
    """端到端: cancel_price_level_alert 传合法 hex 但 alert 不存在 → biz_error 'alert_not_found'."""
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.remove_price_level_alert.return_value = False  # 不存在

    async def handler(args):
        return await cancel_price_level_alert(deps, alert_id="a3f2b8c1", reasoning="t")

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "already triggered or expired" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "alert_not_found"
