"""FIFO lot pairing algorithm tests (spec §5.2)."""
from __future__ import annotations

import pytest
from sqlalchemy import text


# NOTE: Raw SQL helpers must enumerate ALL NOT NULL columns explicitly — Session has 8
# NOT NULL cols with Python-side default= that raw SQL bypasses (per
# tests/test_alembic_migration.py:262 precedent). created_at default same caveat for
# trade_actions. Reuse SQLAlchemy ORM session.add() instead if simpler.


async def _insert_session(conn, sid: str, fee_rate: float | None = 0.0005):
    """Raw SQL session insert — enumerates 12 NOT NULL cols + fee_rate."""
    fr_clause = "NULL" if fee_rate is None else str(fee_rate)
    await conn.execute(text(
        f"INSERT INTO sessions "
        f"(id, name, symbol, initial_balance, status, created_at, updated_at, "
        f" exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
        f" token_budget, fee_rate) "
        f"VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
        f"        '2026-01-01T00:00:00', '2026-01-01T00:00:00', "
        f"        'simulated', '15m', 15, 1, 500000, {fr_clause})"
    ), {"sid": sid})


async def _insert_fill(conn, sid: str, **kwargs):
    """Raw SQL trade_actions insert. Caller provides side / price / amount / fee / pnl / etc.

    Auto-applies created_at default (raw SQL doesn't trigger Python-side _utcnow).
    """
    defaults = {
        "session_id": sid, "action": "order_filled",
        "symbol": "BTC/USDT:USDT", "trigger_reason": "market",
        "created_at": "2026-01-01T00:00:00",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(f":{k}" for k in defaults.keys())
    await conn.execute(text(f"INSERT INTO trade_actions ({cols}) VALUES ({placeholders})"), defaults)


@pytest.mark.asyncio
async def test_fifo_single_open_single_close(engine):
    """One open lot fully consumed by one close → 1 roundtrip."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions

    sid = "fifo-test-1"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.1,
                           fee=2.5, pnl=None, entry_price=None)
        await _insert_fill(conn, sid, side="long", price=51000.0, amount=0.1,
                           fee=2.55, pnl=100.0, entry_price=50000.0)

    rts, caveats = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 1
    assert rts[0].pnl_gross == pytest.approx(100.0)
    assert rts[0].fee_open_share == pytest.approx(2.5)
    assert rts[0].fee_close_share == pytest.approx(2.55)
    assert rts[0].pnl_net == pytest.approx(100.0 - 2.5 - 2.55)
    assert caveats == {"legacy_open_skipped": 0, "legacy_close_skipped": 0,
                       "missing_close_entry_price_count": 0, "invariant_violations": 0}


@pytest.mark.asyncio
async def test_fifo_partial_close_twice(engine):
    """One open, two partial closes → 2 roundtrips sharing open lot."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions

    sid = "fifo-test-2"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.1,
                           fee=2.5, pnl=None, entry_price=None)
        await _insert_fill(conn, sid, side="long", price=51000.0, amount=0.05,
                           fee=1.275, pnl=50.0, entry_price=50000.0)
        await _insert_fill(conn, sid, side="long", price=49500.0, amount=0.05,
                           fee=1.2375, pnl=-25.0, entry_price=50000.0)

    rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 2
    assert rts[0].fee_open_share == pytest.approx(1.25)   # 2.5 * 0.05/0.1
    assert rts[0].pnl_gross == pytest.approx(50.0)
    assert rts[1].fee_open_share == pytest.approx(1.25)
    assert rts[1].pnl_gross == pytest.approx(-25.0)


@pytest.mark.asyncio
async def test_fifo_multi_open_single_close(engine):
    """Two opens at different prices, single close consumes both → 2 roundtrips."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions

    sid = "fifo-test-3"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.1,
                           fee=2.5, pnl=None, entry_price=None)
        await _insert_fill(conn, sid, side="long", price=52000.0, amount=0.1,
                           fee=2.6, pnl=None, entry_price=None)
        await _insert_fill(conn, sid, side="long", price=53000.0, amount=0.2,
                           fee=5.3, pnl=400.0, entry_price=51000.0)

    rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 2
    assert rts[0].pnl_gross == pytest.approx(300.0)  # (53000-50000)*0.1
    assert rts[1].pnl_gross == pytest.approx(100.0)  # (53000-52000)*0.1
    assert rts[0].fee_close_share == pytest.approx(2.65)  # 5.3 * 0.1/0.2
    assert rts[1].fee_close_share == pytest.approx(2.65)
