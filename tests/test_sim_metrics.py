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


# === T3: collect_roundtrips lot-model edges ===


async def test_collect_roundtrips_same_side_addition_two_lots_one_close(db_engine):
    """lot1(long, 0.1) + lot2(long, 0.1) + close 0.2 → 2 roundtrips."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    base = R2_7_MERGED_AT + timedelta(days=2)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=80000, amount=0.1, filled_at=base)
    await make_open_lot(db_engine, sid, cycle_id="c2", entry_px=82000, amount=0.1,
                        filled_at=base + timedelta(minutes=5))
    await make_close_fill(db_engine, sid, cycle_id="c3", exit_px=85000, amount=0.2,
                          pnl_gross=800.0, filled_at=base + timedelta(minutes=10))
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 2
    assert rts[0].pnl_gross == pytest.approx(500.0)  # (85000-80000)*0.1
    assert rts[1].pnl_gross == pytest.approx(300.0)  # (85000-82000)*0.1
    assert caveats["unclosed_lot_count"] == {"long": 0, "short": 0}


async def test_collect_roundtrips_partial_close(db_engine):
    """open(0.2) + close(0.05) → 1 rt (amount=0.05); 1 unclosed lot remaining."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.2)
    await make_close_fill(db_engine, sid, cycle_id="c2", amount=0.05, pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].amount == pytest.approx(0.05)
    assert caveats["unclosed_lot_count"]["long"] == 1


async def test_collect_roundtrips_close_spans_multiple_lots(db_engine):
    """lot1(0.1) + lot2(0.1) + close(0.15) → lot1 fully + lot2 0.05 partial."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    base = R2_7_MERGED_AT + timedelta(days=2)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.1, filled_at=base)
    await make_open_lot(db_engine, sid, cycle_id="c2", amount=0.1,
                        filled_at=base + timedelta(minutes=5))
    await make_close_fill(db_engine, sid, cycle_id="c3", amount=0.15, pnl_gross=400.0,
                          filled_at=base + timedelta(minutes=10))
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 2
    assert rts[0].amount == pytest.approx(0.1)   # lot1 fully
    assert rts[1].amount == pytest.approx(0.05)  # lot2 partial
    assert caveats["unclosed_lot_count"]["long"] == 1


async def test_collect_roundtrips_fee_proportional_split(db_engine):
    """open.fee=0.50 (explicit), lot 50% consumed → fee_open_share=0.25."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    # explicit fee to make assertion straightforward
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.2, fee=0.5)
    # close 0.1 (50% of lot) → fee_close auto = 0.1 * 82000 * 0.0005 = 4.1
    await make_close_fill(db_engine, sid, cycle_id="c2", amount=0.1, pnl_gross=200.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].fee_open_share == pytest.approx(0.25)


async def test_collect_roundtrips_pnl_uses_lot_entry_not_weighted(db_engine):
    """Non-liquidation pnl_gross = (exit_px - lot.entry_px) * consumed,
    not trade_actions.pnl (sim weighted)."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    # lot1 entry 100, lot2 entry 200; close 1.0 at 150
    # FIFO lot1 consumed 1.0 → (150-100)*1 = +50
    # If wrongly used trade_actions.pnl=0 (sim weighted), test catches.
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=100, amount=1.0)
    await make_open_lot(db_engine, sid, cycle_id="c2", entry_px=200, amount=1.0)
    await make_close_fill(db_engine, sid, cycle_id="c3", exit_px=150, amount=1.0,
                          pnl_gross=0.0)  # sim weighted
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].pnl_gross == pytest.approx(50.0)


# === T4: collect_roundtrips liquidation + invariants ===


async def test_collect_roundtrips_liquidation_close_cycle_id_none(db_engine):
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.1)
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="liquidation",
                          amount=0.1, pnl_gross=-50.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].exit_type == "liquidation"
    assert rts[0].close_cycle_id is None
    assert caveats["liquidation_count"] == 1


async def test_collect_roundtrips_liquidation_uses_trade_actions_pnl(db_engine):
    """Sim caps liquidation loss; FIFO recompute would over-state.
    Verify roundtrip.pnl_gross = trade_actions.pnl proportional."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    # entry 80000, exit 40000, amount 0.1, lev 10 → recompute -4000;
    # sim pnl_cap stub = -800 (margin floor)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=80000, amount=0.1, leverage=10)
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="liquidation",
                          exit_px=40000, amount=0.1, pnl_gross=-800.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    # consumed 0.1 / actual 0.1 → full pnl_gross = -800
    assert rts[0].pnl_gross == pytest.approx(-800.0)


async def test_collect_roundtrips_liquidation_missing_trade_action_invariant(db_engine, capsys):
    """Liquidation fill without order_filled trade_action → invariant violation."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.1)
    # Manually insert sim_orders WITHOUT any trade_action row
    from sqlalchemy import insert
    from src.storage.models import SimOrder
    async with db_engine.begin() as conn:
        await conn.execute(insert(SimOrder).values(
            session_id=sid, order_id="liq-orphan", symbol="BTC/USDT:USDT",
            side="sell", position_side="long", order_type="liquidation",
            amount=0.1, status="filled", filled_price=40000, fee=2.0,
            filled_at=R2_7_MERGED_AT + timedelta(days=2, minutes=5),
        ))
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert caveats["invariant_violations"] >= 1
    assert any(rt.exit_type == "liquidation" and rt.pnl_gross == 0.0 for rt in rts)
    err = capsys.readouterr().err
    assert "missing trade_actions.pnl" in err


async def test_collect_roundtrips_non_liquidation_recomputes_pnl_from_lot(db_engine):
    """Non-liquidation must recompute from lot.entry_px (ignore trade_actions.pnl)."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=80000, amount=0.1)
    # set wrong trade_actions.pnl=999 → if read, test catches; expected PnL = 200
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="market",
                          exit_px=82000, amount=0.1, pnl_gross=999.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].pnl_gross == pytest.approx(200.0)


# === T5: collect_roundtrips stale amount + close-no-lot + diverge ===


async def test_collect_roundtrips_stale_sl_amount_derived_from_fee(db_engine):
    """SL order.amount=0.2 stale, position 0.05; fee derived 0.05; rt.amount=0.05."""
    sid = await make_session(db_engine, fee_rate=0.0005)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.05)
    # fee = 0.05 * 82000 * 0.0005 = 2.05; pass explicit stale amount=0.2 with that fee
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="stop",
                          amount=0.2, fee=2.05, pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert len(rts) == 1
    assert rts[0].amount == pytest.approx(0.05)
    assert caveats["stale_close_amount_count"] == 0  # derive succeeded


async def test_collect_roundtrips_stale_amount_fallback_to_order_amount(db_engine, capsys):
    """fee=0 → derivation fails → fallback sim_orders.amount + 2 caveats:
    stale_close_amount_count=1 (derive failed) AND invariant_violations=1
    (close_remaining=0.15 unmatched after consuming the only 0.05 lot).
    """
    sid = await make_session(db_engine, fee_rate=0.0005)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", amount=0.05)
    await make_close_fill(db_engine, sid, cycle_id="c2", exit_type="stop",
                          amount=0.2, fee=0.0, pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert caveats["stale_close_amount_count"] == 1
    assert caveats["invariant_violations"] == 1  # 0.15 unmatched after lot exhausted
    assert "no preceding open lot" in capsys.readouterr().err


async def test_collect_roundtrips_unclosed_lot(db_engine):
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_open_lot(db_engine, sid, cycle_id="c1")
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert rts == []
    assert caveats["unclosed_lot_count"] == {"long": 1, "short": 0}


async def test_collect_roundtrips_close_no_lot_warning(db_engine, capsys):
    """Close fill with no preceding lot → stderr warning + invariant_violations += 1."""
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")
    await make_close_fill(db_engine, sid, cycle_id="c1", exit_type="market",
                          amount=0.1, pnl_gross=100.0)
    rts, caveats = await collect_roundtrips(db_engine, sid)
    assert rts == []
    assert caveats["invariant_violations"] == 1
    err = capsys.readouterr().err
    assert "no preceding open lot" in err


async def test_collect_roundtrips_cycle_id_5_enum_join(db_engine):
    """open_cycle_id resolves via v_order_lifecycle (5-enum), not order_filled."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1")
    await make_close_fill(db_engine, sid, cycle_id="c2", pnl_gross=200.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].open_cycle_id == "c1"
    assert rts[0].close_cycle_id == "c2"


async def test_collect_roundtrips_duration_seconds(db_engine):
    sid = await make_session(db_engine)
    for c in ["c1", "c2"]:
        await make_cycle(db_engine, sid, c)
    base = R2_7_MERGED_AT + timedelta(days=2)
    await make_open_lot(db_engine, sid, cycle_id="c1", filled_at=base)
    await make_close_fill(db_engine, sid, cycle_id="c2",
                          filled_at=base + timedelta(minutes=15), pnl_gross=100.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert rts[0].duration_seconds == 15 * 60


async def test_collect_roundtrips_partial_close_lot_pnl_diverges_from_sim_weighted(db_engine):
    """Spec §4.4 item 8: lot1=100/1 + lot2=200/1 + close 0.5@150
    → FIFO lot pnl=+25, sim weighted=0; both legitimate."""
    sid = await make_session(db_engine)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=100, amount=1.0)
    await make_open_lot(db_engine, sid, cycle_id="c2", entry_px=200, amount=1.0)
    await make_close_fill(db_engine, sid, cycle_id="c3", exit_px=150, amount=0.5,
                          pnl_gross=0.0)  # sim weighted = 0
    rts, _ = await collect_roundtrips(db_engine, sid)
    # lot1 consumed 0.5 → (150-100)*0.5 = +25
    assert rts[0].pnl_gross == pytest.approx(25.0)


async def test_collect_roundtrips_full_close_lot_pnl_matches_sim_weighted(db_engine):
    """All lots fully closed → sum(FIFO lot pnl_gross) == sim realized."""
    sid = await make_session(db_engine, fee_rate=0.0005)
    for c in ["c1", "c2", "c3"]:
        await make_cycle(db_engine, sid, c)
    await make_open_lot(db_engine, sid, cycle_id="c1", entry_px=100, amount=1.0)
    await make_open_lot(db_engine, sid, cycle_id="c2", entry_px=200, amount=1.0)
    await make_close_fill(db_engine, sid, cycle_id="c3", exit_px=150, amount=2.0,
                          pnl_gross=0.0)
    rts, _ = await collect_roundtrips(db_engine, sid)
    assert sum(rt.pnl_gross for rt in rts) == pytest.approx(0.0, abs=0.01)
