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
