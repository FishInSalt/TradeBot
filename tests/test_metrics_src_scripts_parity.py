"""src/services/metrics FIFO ↔ scripts/_sim_metrics.collect_roundtrips parity (spec §6.10)."""
from __future__ import annotations

import math
import pytest
from sqlalchemy import text


async def _setup_synthetic_sim_session(engine, sid: str, fee_rate: float, fills: list[tuple]):
    """Double-write sim_orders + trade_actions from single fill specs.

    Each fill spec: (event_type, side, price, amount, [trigger_reason])
      event_type ∈ {"open", "close", "liq"}
      side ∈ {"long", "short"}

    Fee = price × amount × fee_rate exactly (no stale_close_amount path).
    """
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO sessions "
            "(id, name, symbol, initial_balance, status, created_at, updated_at, "
            " exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            " token_budget, fee_rate) "
            "VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
            "        '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
            "        'simulated', '15m', 15, 1, 500000, :fr)"
        ), {"sid": sid, "fr": fee_rate})

        for idx, spec in enumerate(fills):
            etype, side, price, amount = spec[:4]
            trigger = "liquidation" if etype == "liq" else "market"
            is_close = etype in ("close", "liq")
            ord_id = f"o-{idx}"
            order_side = "sell" if (side == "long" and is_close) or (side == "short" and not is_close) else "buy"
            order_type = "liquidation" if etype == "liq" else "market"
            fee = price * amount * fee_rate
            ts = f"2026-01-01 00:00:{idx:02d}"

            # sim_orders row (consumed by scripts FIFO) — enumerate all NOT NULL cols
            # (Python defaults bypass raw SQL: status / frozen_margin / leverage / created_at)
            await conn.execute(text(
                "INSERT INTO sim_orders "
                "(session_id, order_id, symbol, side, position_side, order_type, "
                " amount, filled_price, fee, status, frozen_margin, leverage, "
                " filled_at, created_at) "
                "VALUES (:sid, :oid, 'BTC/USDT:USDT', :side, :ps, :ot, "
                "        :amt, :px, :fee, 'filled', 0.0, 10, :ts, :ts)"
            ), {"sid": sid, "oid": ord_id, "side": order_side, "ps": side, "ot": order_type,
                "amt": amount, "px": price, "fee": fee, "ts": ts})

            # trade_actions row (consumed by src FIFO)
            pnl = None
            entry_price = None
            if is_close:
                if etype == "liq":
                    pnl = -200.0  # arbitrary; liquidation branch reverses
                else:
                    sign = 1 if side == "long" else -1
                    open_idx = next(i for i, s in enumerate(fills[:idx])
                                    if s[0] == "open" and s[1] == side)
                    entry_price = fills[open_idx][2]
                    pnl = (price - entry_price) * amount * sign

            await conn.execute(text(
                "INSERT INTO trade_actions "
                "(session_id, action, symbol, side, trigger_reason, price, "
                " pnl, fee, amount, entry_price, order_id, created_at) "
                "VALUES (:sid, 'order_filled', 'BTC/USDT:USDT', :side, :trig, :px, "
                "        :pnl, :fee, :amt, :ep, :oid, :ts)"
            ), {"sid": sid, "side": side, "trig": trigger, "px": price,
                "pnl": pnl, "fee": fee, "amt": amount, "ep": entry_price,
                "oid": ord_id, "ts": ts})


@pytest.mark.asyncio
async def test_src_scripts_fifo_parity_simple(engine):
    """Single open + single close: src ↔ scripts byte-equal."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    from scripts._sim_metrics import collect_roundtrips

    sid = "parity-simple"
    fee_rate = 0.0005
    await _setup_synthetic_sim_session(engine, sid, fee_rate, fills=[
        ("open", "long", 50000.0, 0.1),
        ("close", "long", 51000.0, 0.1),
    ])

    src_rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid)
    script_rts, caveats = await collect_roundtrips(engine, sid)
    assert caveats["stale_close_amount_count"] == 0
    assert len(src_rts) == len(script_rts) == 1
    assert math.isclose(src_rts[0].pnl_gross, script_rts[0].pnl_gross, abs_tol=1e-9)
    assert math.isclose(src_rts[0].pnl_net, script_rts[0].pnl_net, abs_tol=1e-9)
    assert math.isclose(src_rts[0].fee_open_share, script_rts[0].fee_open_share, abs_tol=1e-9)
    assert math.isclose(src_rts[0].fee_close_share, script_rts[0].fee_close_share, abs_tol=1e-9)


@pytest.mark.asyncio
async def test_src_scripts_fifo_parity_partial_close(engine):
    """Partial close 2 times: src ↔ scripts byte-equal."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    from scripts._sim_metrics import collect_roundtrips

    sid = "parity-partial"
    fee_rate = 0.0005
    await _setup_synthetic_sim_session(engine, sid, fee_rate, fills=[
        ("open", "long", 50000.0, 0.1),
        ("close", "long", 51000.0, 0.05),
        ("close", "long", 49500.0, 0.05),
    ])

    src_rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid)
    script_rts, caveats = await collect_roundtrips(engine, sid)
    assert caveats["stale_close_amount_count"] == 0
    assert len(src_rts) == len(script_rts) == 2
    for s, t in zip(src_rts, script_rts):
        assert math.isclose(s.pnl_gross, t.pnl_gross, abs_tol=1e-9)
        assert math.isclose(s.pnl_net, t.pnl_net, abs_tol=1e-9)
        assert math.isclose(s.fee_open_share, t.fee_open_share, abs_tol=1e-9)
        assert math.isclose(s.fee_close_share, t.fee_close_share, abs_tol=1e-9)
