"""Task 4: open_position + place_limit_order produce contracts (张数) not base amount.

cs=0.01 (BTC mini): raw_quantity = usdt_amount×leverage / (price×cs)
  = (10000×0.10 × 10) / (100000 × 0.01) = 10000/1000 = 10 contracts.
Old behaviour (no ÷cs): 10000/100000 = 0.1  ← base amount, wrong.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.exchange.base import Order
from src.agent.tools_execution import open_position, place_limit_order


def _deps(cs: float):
    """Minimal deps that let open_position / place_limit_order reach create_order."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.fee_rate = 0.0005
    deps.approval_enabled = False
    deps.approval_gate = None
    deps.db_engine = None  # _record_action no-op

    balance = MagicMock()
    balance.free_usdt = 10000.0

    deps.exchange = MagicMock()
    deps.exchange.fetch_balance = AsyncMock(return_value=balance)
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.set_leverage = AsyncMock()
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    deps.exchange.get_contract_size = AsyncMock(return_value=cs)
    deps.exchange.amount_to_precision = MagicMock(side_effect=lambda s, a: a)  # identity
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="o1",
        symbol="BTC/USDT:USDT",
        side="buy",
        order_type="market",
        amount=10.0,  # expected contracts
        price=None,
        status="open",
        fee=None,
        is_algo=False,
        trigger_price=None,
    ))

    ticker = MagicMock()
    ticker.last = 100000.0
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=ticker)

    return deps


@pytest.mark.asyncio
async def test_open_position_quantity_is_contracts_not_base():
    """open_position must pass contracts (张数) to create_order.

    cs=0.01, 10% of 10000 free USDT × 10x leverage → notional 10000 USDT.
    contracts = 10000 / (100000 × 0.01) = 10.
    Old (broken) path: 10000 / 100000 = 0.1  (base amount, 100× too small).
    """
    deps = _deps(0.01)
    await open_position(deps, side="long", position_pct=10.0, leverage=10, reasoning="r")
    amount = deps.exchange.create_order.call_args.kwargs["amount"]
    assert abs(amount - 10.0) < 1e-9, (
        f"expected 10 contracts, got {amount} — "
        "raw_quantity must be divided by contract_size"
    )


@pytest.mark.asyncio
async def test_place_limit_order_quantity_is_contracts():
    """place_limit_order must pass contracts (张数) to create_order.

    Same arithmetic as open_position: cs=0.01, price=100000, 10% × 10x → 10 contracts.
    """
    deps = _deps(0.01)
    await place_limit_order(
        deps, side="long", price=100000.0, position_pct=10.0, leverage=10, reasoning="r"
    )
    amount = deps.exchange.create_order.call_args.kwargs["amount"]
    assert abs(amount - 10.0) < 1e-9, (
        f"expected 10 contracts, got {amount} — "
        "raw_quantity must be divided by contract_size"
    )


@pytest.mark.asyncio
async def test_open_position_cs1_unchanged():
    """cs=1.0 (legacy/default): behaviour is numerically identical to old path."""
    deps = _deps(1.0)
    # 10% of 10000 × 10x / (100000 × 1.0) = 0.1 contracts = 0.1 BTC (unchanged)
    deps.exchange.create_order.return_value = Order(
        id="o2", symbol="BTC/USDT:USDT", side="buy", order_type="market",
        amount=0.1, price=None, status="open", fee=None, is_algo=False, trigger_price=None,
    )
    await open_position(deps, side="long", position_pct=10.0, leverage=10, reasoning="r")
    amount = deps.exchange.create_order.call_args.kwargs["amount"]
    assert abs(amount - 0.1) < 1e-9


@pytest.mark.asyncio
async def test_place_limit_order_cs1_unchanged():
    """cs=1.0: place_limit_order passes same quantity as before."""
    deps = _deps(1.0)
    deps.exchange.create_order.return_value = Order(
        id="o3", symbol="BTC/USDT:USDT", side="buy", order_type="limit",
        amount=0.1, price=100000.0, status="open", fee=None, is_algo=False, trigger_price=None,
    )
    await place_limit_order(
        deps, side="long", price=100000.0, position_pct=10.0, leverage=10, reasoning="r"
    )
    amount = deps.exchange.create_order.call_args.kwargs["amount"]
    assert abs(amount - 0.1) < 1e-9
