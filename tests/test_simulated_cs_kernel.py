import pytest
from tests.test_simulated_exchange import _make_exchange, _tick
from src.integrations.exchange.simulated import _Position


def _ex_cs(cs, balance=100_000.0):
    ex = _make_exchange(initial_balance=balance)
    ex._contract_size = cs
    return ex


@pytest.mark.asyncio
async def test_unrealized_pnl_scales_with_cs():
    """BTC cs=0.01: 10 contracts = 0.1 base.
    Entry 100000, mark moves to 101000 → pnl = (101000-100000) × (10×0.01) = 100.
    """
    ex = _ex_cs(0.01)
    # Override initial ticker to entry price
    ex._latest_ticker = _tick(last=100_000.0, bid=100_000.0, ask=100_000.0)
    ex._leverage["BTC/USDT:USDT"] = 10

    await ex.create_order("BTC/USDT:USDT", "buy", "market", amount=10)
    # Fill at 100000
    await ex._process_tick(_tick(last=100_000.0, bid=100_000.0, ask=100_000.0))
    # Move price to 101000
    await ex._process_tick(_tick(last=101_000.0, bid=101_000.0, ask=101_000.0))

    pos = (await ex.fetch_positions("BTC/USDT:USDT"))[0]
    assert pos.contracts == 10                           # stored in contracts (張數)
    assert abs(pos.unrealized_pnl - 100.0) < 1e-6       # 1000 × (10×0.01)


@pytest.mark.asyncio
async def test_close_pnl_and_fee_scale_with_cs():
    """Directly construct a position and invoke _close_position_core to verify
    that pnl and fee scale with cs=0.01 (not cs=1).
    """
    ex = _ex_cs(0.01)
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=10, entry_price=100_000.0, leverage=10,
    )
    ex._used_usdt = 10_000.0
    ex._latest_ticker = _tick(last=101_000.0, bid=101_000.0, ask=101_000.0)

    pnl, fee, released = ex._close_position_core(
        "BTC/USDT:USDT", "long", 10, 101_000.0,
    )

    # pnl = (101000 - 100000) × (10 × 0.01) = 100.0
    assert abs(pnl - 100.0) < 1e-6
    # fee = 101000 × (10 × 0.01) × 0.0005 = 5.05
    expected_fee = 101_000.0 * (10 * 0.01) * 0.0005
    assert abs(fee - expected_fee) < 1e-6
    # released_margin = 100000 × (10 × 0.01) / 10 = 100.0
    expected_margin = 100_000.0 * (10 * 0.01) / 10
    assert abs(released - expected_margin) < 1e-6


@pytest.mark.asyncio
async def test_close_pnl_short_scales_with_cs():
    """Short position: pnl = (entry - fill) × base_qty."""
    ex = _ex_cs(0.01)
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="short", contracts=10, entry_price=100_000.0, leverage=10,
    )
    ex._used_usdt = 10_000.0
    ex._latest_ticker = _tick(last=99_000.0, bid=99_000.0, ask=99_000.0)

    pnl, fee, released = ex._close_position_core(
        "BTC/USDT:USDT", "short", 10, 99_000.0,
    )

    # pnl = (100000 - 99000) × (10 × 0.01) = 100.0
    assert abs(pnl - 100.0) < 1e-6


@pytest.mark.asyncio
async def test_cs_1_unchanged():
    """cs=1.0 (default) must give same results as before (regression)."""
    ex = _ex_cs(1.0)
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=1, entry_price=100_000.0, leverage=10,
    )
    ex._used_usdt = 10_000.0
    ex._latest_ticker = _tick(last=101_000.0, bid=101_000.0, ask=101_000.0)

    pnl, fee, released = ex._close_position_core(
        "BTC/USDT:USDT", "long", 1, 101_000.0,
    )

    # pnl = (101000 - 100000) × 1 × 1.0 = 1000
    assert abs(pnl - 1000.0) < 1e-6
    # fee = 101000 × 1 × 1.0 × 0.0005 = 50.5
    assert abs(fee - 50.5) < 1e-6
    # released = 100000 × 1 × 1.0 / 10 = 10000
    assert abs(released - 10_000.0) < 1e-6
