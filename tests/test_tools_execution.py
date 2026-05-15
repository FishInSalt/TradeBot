"""Tests for Task 10: register_close_order_entry wired in close-direction tools."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.exchange.base import Position, Order


def _make_deps(*, position_side="long", entry_price=80000.0, contracts=0.1,
               order_id="oid1"):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.fee_rate = 0.0005
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC/USDT:USDT",
            side=position_side,
            contracts=contracts,
            entry_price=entry_price,
            unrealized_pnl=10.0,
            leverage=10,
            liquidation_price=72000.0,
            created_at=None,
        ),
    ])
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    ticker = MagicMock()
    ticker.bid = 80100.0
    ticker.ask = 80110.0
    ticker.last = 80105.0
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id=order_id,
        symbol="BTC/USDT:USDT",
        side="sell" if position_side == "long" else "buy",
        order_type="market",
        amount=contracts,
        price=None,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))
    deps.exchange.register_close_order_entry = MagicMock()
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.cancel_order = AsyncMock()
    deps.exchange.algo_trigger_reference = "last"
    deps.approval_gate = None
    deps.approval_enabled = False
    deps.db_engine = None  # _record_action no-op
    return deps


@pytest.mark.asyncio
async def test_close_position_calls_register_close_order_entry():
    """close_position registers entry per submitted close order."""
    from src.agent.tools_execution import close_position

    deps = _make_deps(order_id="oid1", entry_price=80000.0)
    await close_position(deps, reasoning="test")

    deps.exchange.register_close_order_entry.assert_called_once_with("oid1", 80000.0)


@pytest.mark.asyncio
async def test_set_stop_loss_calls_register_close_order_entry():
    """set_stop_loss registers entry after creating stop order."""
    from src.agent.tools_execution import set_stop_loss

    deps = _make_deps(order_id="sl1", entry_price=80000.0)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="sl1",
        symbol="BTC/USDT:USDT",
        side="sell",
        order_type="stop",
        amount=0.1,
        price=78000.0,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))

    await set_stop_loss(deps, price=78000.0, reasoning="trailing stop")

    deps.exchange.register_close_order_entry.assert_called_once_with("sl1", 80000.0)


@pytest.mark.asyncio
async def test_set_take_profit_calls_register_close_order_entry():
    """set_take_profit registers entry after creating take-profit order."""
    from src.agent.tools_execution import set_take_profit

    deps = _make_deps(order_id="tp1", entry_price=80000.0)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="tp1",
        symbol="BTC/USDT:USDT",
        side="sell",
        order_type="take_profit",
        amount=0.1,
        price=85000.0,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))

    await set_take_profit(deps, price=85000.0, reasoning="target reached")

    deps.exchange.register_close_order_entry.assert_called_once_with("tp1", 80000.0)
