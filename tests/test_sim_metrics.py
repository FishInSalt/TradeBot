"""Tests for scripts/_sim_metrics.py — Phase 2 cross-sim analytics core."""
from datetime import datetime, timedelta, timezone

import pytest

from scripts._sim_metrics import (
    R2_7_MERGED_AT,
    METRIC_GROUPS,
    Roundtrip,
    _Lot,
    _compute_pnl,
    _derive_close_amount,
    _is_close_fill,
    collect_roundtrips,
)
from tests._sim_fixtures import (
    make_session, make_cycle, make_open_lot, make_close_fill,
)


def test_is_close_fill_long_sell_returns_true():
    assert _is_close_fill("long", "sell") is True


def test_is_close_fill_short_buy_returns_true():
    assert _is_close_fill("short", "buy") is True


def test_is_close_fill_open_returns_false():
    assert _is_close_fill("long", "buy") is False
    assert _is_close_fill("short", "sell") is False


def test_compute_pnl_long_profit():
    assert _compute_pnl(100.0, 110.0, 1.0, "long") == pytest.approx(10.0)


def test_compute_pnl_short_profit():
    assert _compute_pnl(100.0, 90.0, 1.0, "short") == pytest.approx(10.0)


class _FillStub:
    def __init__(self, fee=None, filled_price=None, amount=None):
        self.fee = fee
        self.filled_price = filled_price
        self.amount = amount


def test_derive_close_amount_uses_fee_inverse():
    # fee = 80000 * 0.05 * 0.0005 = 2.0
    fill = _FillStub(fee=2.0, filled_price=80000.0, amount=0.05)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005)
    assert ok is True
    assert derived == pytest.approx(0.05)


def test_derive_close_amount_fallback_when_fee_missing():
    fill = _FillStub(fee=None, filled_price=80000.0, amount=0.2)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005)
    assert ok is False
    assert derived == 0.2


def test_derive_close_amount_fallback_when_fee_rate_missing():
    fill = _FillStub(fee=2.0, filled_price=80000.0, amount=0.05)
    derived, ok = _derive_close_amount(fill, fee_rate=None)
    assert ok is False
    assert derived == 0.05


def test_derive_close_amount_fallback_when_derived_exceeds_order_amount():
    # implies actual=0.5 but order_amount=0.05; reject as suspicious
    fill = _FillStub(fee=20.0, filled_price=80000.0, amount=0.05)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005)
    assert ok is False
    assert derived == 0.05


def test_r2_7_merged_at_constant():
    assert R2_7_MERGED_AT == datetime(2026, 5, 2, tzinfo=timezone.utc)


def test_metric_groups_count_28():
    """Single source of metric inventory; renderer + drift guard reuse this."""
    assert len(METRIC_GROUPS) == 28
    assert len(set(METRIC_GROUPS)) == 28  # no duplicates


async def test_phase1_views_runnable(db_engine):
    """Prerequisite sanity: Phase 1 views exist and SELECT * returns a row shape.

    Catches schema drift early — if v_cycle_metrics column was renamed or
    a view was dropped, this fails in T1 instead of mid-T2 algorithm.
    Cost: 30ms.
    """
    from sqlalchemy import text
    async with db_engine.connect() as conn:
        await conn.execute(text("SELECT * FROM v_cycle_metrics LIMIT 0"))
        await conn.execute(text("SELECT * FROM v_alert_lifecycle LIMIT 0"))
        await conn.execute(text("SELECT * FROM v_order_lifecycle LIMIT 0"))


# === T2: collect_roundtrips happy paths ===


async def test_collect_roundtrips_empty_session(db_engine):
    sid = await make_session(db_engine)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert rts == []
    assert caveats["unclosed_lot_count"] == {"long": 0, "short": 0}
    assert caveats["invariant_violations"] == 0
    assert caveats["liquidation_count"] == 0
    assert caveats["stale_close_amount_count"] == 0


async def test_collect_roundtrips_single_market_close(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_cycle(db_engine, sid, "c2")
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=80000, amount=0.1)
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_px=82000, amount=0.1,
                          exit_type="market", pnl_gross=200.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].exit_type == "market"
    assert rts[0].amount == pytest.approx(0.1)
    # FIFO recompute (non-liquidation): (82000-80000)*0.1 = 200
    assert rts[0].pnl_gross == pytest.approx(200.0)
    assert caveats["stale_close_amount_count"] == 0


async def test_collect_roundtrips_sl_close(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_cycle(db_engine, sid, "c2")
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="stop", pnl_gross=200.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].exit_type == "stop"


async def test_collect_roundtrips_tp_close(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_cycle(db_engine, sid, "c2")
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="take_profit", pnl_gross=200.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].exit_type == "take_profit"


async def test_collect_roundtrips_two_long_sequential(db_engine):
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3", "c4"]:
        await make_cycle(db_engine, sid, c)
    base = R2_7_MERGED_AT + timedelta(days=2)
    await make_open_lot(db_engine, sid, cycle_id="c1", filled_at=base)
    await make_close_fill(db_engine, sid, cycle_id="c2",
                          filled_at=base + timedelta(minutes=10), pnl_gross=200.0)
    await make_open_lot(db_engine, sid, cycle_id="c3",
                        filled_at=base + timedelta(minutes=20))
    await make_close_fill(db_engine, sid, cycle_id="c4",
                          filled_at=base + timedelta(minutes=30), pnl_gross=300.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 2


async def test_collect_roundtrips_long_short_alternating(db_engine):
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3", "c4"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", side="long")
    await make_close_fill(db_engine, sid, cycle_id="c2", side="long", pnl_gross=100.0)
    await make_open_lot(db_engine, sid, cycle_id="c3", side="short")
    await make_close_fill(db_engine, sid, cycle_id="c4", side="short", pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 2
    assert {rt.side for rt in rts} == {"long", "short"}
    assert caveats["unclosed_lot_count"] == {"long": 0, "short": 0}
