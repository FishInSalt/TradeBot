"""B4: _capture_state_snapshot notional ×cs via get_contract_size."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.integrations.exchange.base import Position, Balance, Ticker
from src.services.cycle_capture import _capture_state_snapshot


@pytest.mark.asyncio
async def test_pnl_pct_uses_real_cs():
    """pnl_pct_of_notional uses real contract size: notional = entry_price × contracts × cs."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=10, entry_price=100000.0,
        unrealized_pnl=100.0, leverage=10, liquidation_price=90000.0,
    )])
    deps.exchange.get_contract_size = AsyncMock(return_value=0.01)
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=10000.0, used_usdt=0.0))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=101000.0, bid=101000.0, ask=101000.0,
        high=102000.0, low=99000.0, base_volume=1000.0, timestamp=1746098096000))
    snap = await _capture_state_snapshot("c1", deps)
    # notional = 100000 × 10 × 0.01 = 10000; pnl_pct = 100/10000×100 = 1.0
    assert abs(snap["position"]["pnl_pct_of_notional"] - 1.0) < 1e-6
