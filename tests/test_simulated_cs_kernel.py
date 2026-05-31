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
    ex._used_usdt = 100.0  # true margin: 100000 × (10 × 0.01) / 10 = 100.0
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
    ex._used_usdt = 100.0  # true margin: 100000 × (10 × 0.01) / 10 = 100.0
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


@pytest.mark.asyncio
async def test_pnl_cap_clamp_guard_cs_not_one():
    """Guard: pnl_cap clamp path is cs-scaled at cs=0.01.

    cs=0.01, long 10 contracts @ entry=100000, lev=50, close @ 90000 (−10% × 50×):
      base_qty        = 10 × 0.01 = 0.1
      released_margin = 100000 × 0.1 / 50 = 200.0
      fee             = 90000 × 0.1 × 0.0005 = 4.5
      raw pnl         = (90000 − 100000) × 0.1 = −1000  (exceeds margin)
      clamped pnl     = max(−1000, −(200 − 4.5)) = −195.5

    If the cap branch leaked a bare 張數 (forgot ×cs), released_margin would be
    200/0.01 = 20000 and fee 450, giving clamped pnl = −19550 — 100× off.
    The exact assertion catches that regression.
    """
    ex = _ex_cs(0.01)
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=10, entry_price=100_000.0, leverage=50,
    )
    # true margin: 100000 × (10 × 0.01) / 50 = 200.0
    ex._used_usdt = 200.0
    ex._free_usdt = 100_000.0 - 200.0
    ex._latest_ticker = _tick(last=90_000.0, bid=90_000.0, ask=90_000.0)

    pnl, fee, released = ex._close_position_core(
        "BTC/USDT:USDT", "long", 10, 90_000.0, pnl_cap=True,
    )

    # released_margin = 100000 × 0.1 / 50 = 200.0
    assert abs(released - 200.0) < 1e-6
    # fee = 90000 × 0.1 × 0.0005 = 4.5
    assert abs(fee - 4.5) < 1e-6
    # pnl clamped to −(200.0 − 4.5) = −195.5 (not raw −1000)
    assert abs(pnl - (-195.5)) < 1e-6


@pytest.mark.asyncio
async def test_liquidation_via_process_tick_cs_not_one():
    """Guard: end-to-end liquidation triggered by _process_tick is cs-scaled at cs=0.01.

    cs=0.01, long 10 contracts @ entry=100000, lev=10:
      liq_price ≈ 100000 × (1 − 1/10) / (1 − 0.0005) ≈ 90045
      base_qty        = 10 × 0.01 = 0.1
      released_margin = 100000 × 0.1 / 10 = 1000.0
      At bid=89000 (below liq_price):
        fee_liq = 89000 × 0.1 × 0.0005 = 4.45
        raw pnl = (89000 − 100000) × 0.1 = −1100  (exceeds margin)
        clamped pnl = max(−1100, −(1000 − 4.45)) = −995.55

    If ×cs were missing in the cap path: released_margin = 100000, pnl_capped ≈ −99955.5 — 100× off.
    The exact free_usdt assertion catches that regression.
    """
    initial_balance = 10_000.0
    ex = _ex_cs(0.01, balance=initial_balance)

    # Directly inject position; set _used_usdt to true cs-scaled margin
    # released_margin = 100000 × (10 × 0.01) / 10 = 1000.0
    ex._positions["BTC/USDT:USDT"] = _Position(
        side="long", contracts=10, entry_price=100_000.0, leverage=10,
    )
    ex._used_usdt = 1_000.0
    ex._free_usdt = initial_balance - 1_000.0  # = 9000.0

    # Tick with bid=89000 — well below liq_price ≈ 90045
    await ex._process_tick(_tick(last=89_000.0, bid=89_000.0, ask=89_000.0))

    # Position must be gone (liquidated)
    positions = await ex.fetch_positions("BTC/USDT:USDT")
    assert positions == [], "position should be liquidated"

    # pnl_cap clamps loss to -(released_margin - fee) = -(1000 - 4.45) = -995.55
    # free_usdt after = free_before + released + capped_pnl - fee
    #                 = 9000 + 1000 + (-995.55) - 4.45 = 9000.0
    # (entire margin is lost; pnl_cap ensures free_usdt doesn't go below 9000)
    balance = await ex.fetch_balance()
    assert abs(balance.free_usdt - 9_000.0) < 1e-4
    assert abs(balance.used_usdt) < 1e-6  # margin fully released
