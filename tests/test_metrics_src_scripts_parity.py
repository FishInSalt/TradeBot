"""src/services/metrics FIFO ↔ scripts/_sim_metrics.collect_roundtrips parity (spec §6.10).

Parity scope (compared on math-consistent synthetic fixtures only):
  - **numeric**: pnl_gross, pnl_net, fee_open_share, fee_close_share
  - **identity**: side, entry_px, exit_px, amount (PR #57 review R4-I-3 — defends
    against off-by-one open lot lookup or side-sign swap that could numerically
    coincide on synthetic fixtures)

Intentionally excluded from parity (scripts-only fields not represented in src):
  - open_at / close_at / duration_seconds (scripts derives from sim_orders timestamps;
    src doesn't surface)
  - leverage (scripts inherits from open order; src treats as informational only)
  - open_cycle_id / close_cycle_id (scripts joins to agent_cycles; src doesn't)
  - exit_type / is_liquidation modeling differences

Intentionally excluded from fixtures (wontfix per spec §6.10):
  - stale_close_amount_count divergence: scripts derives close amount from fee/fee_rate
    when amount stale; src trusts FillEvent.amount (actual filled, not order.amount)
  - MDD: src = realized-only equity (Σ net_pnls); scripts = broker total (state_snapshot)
"""
from __future__ import annotations

import math
import pytest
from sqlalchemy import text


async def _setup_synthetic_sim_session(
    engine,
    sid: str,
    fee_rate: float,
    fills: list[tuple],
    contract_size: float = 1.0,
):
    """Double-write sim_orders + trade_actions from single fill specs.

    Each fill spec: (event_type, side, price, amount, [trigger_reason])
      event_type ∈ {"open", "close", "liq"}
      side ∈ {"long", "short"}

    Fee = price × amount × contract_size × fee_rate exactly (no stale_close_amount path).
    contract_size defaults to 1.0 so existing callers (cs=1) see identical math.
    """
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO sessions "
            "(id, name, symbol, initial_balance, status, created_at, updated_at, "
            " exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            " token_budget, fee_rate, contract_size) "
            "VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
            "        '2026-01-01 00:00:00', '2026-01-01 00:00:00', "
            "        'simulated', '15m', 15, 1, 500000, :fr, :cs)"
        ), {"sid": sid, "fr": fee_rate, "cs": contract_size})

        for idx, spec in enumerate(fills):
            etype, side, price, amount = spec[:4]
            trigger = "liquidation" if etype == "liq" else "market"
            is_close = etype in ("close", "liq")
            ord_id = f"o-{idx}"
            order_side = "sell" if (side == "long" and is_close) or (side == "short" and not is_close) else "buy"
            order_type = "liquidation" if etype == "liq" else "market"
            fee = price * amount * contract_size * fee_rate
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
                    pnl = (price - entry_price) * amount * sign * contract_size

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
    _assert_roundtrip_parity(src_rts[0], script_rts[0])


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
        _assert_roundtrip_parity(s, t)


def _assert_roundtrip_parity(src_rt, script_rt) -> None:
    """Assert src ↔ scripts Roundtrip parity on common fields.

    Numeric (math.isclose abs_tol=1e-9): pnl_gross, pnl_net, fee_open_share,
    fee_close_share.

    Identity (==): side, entry_px, exit_px, amount — defends against off-by-one
    open lot lookup or side-sign swap that could numerically coincide on
    synthetic fixtures (PR #57 review R4-I-3).
    """
    # Numeric
    assert math.isclose(src_rt.pnl_gross, script_rt.pnl_gross, abs_tol=1e-9), (
        f"pnl_gross drift: src={src_rt.pnl_gross} vs scripts={script_rt.pnl_gross}"
    )
    assert math.isclose(src_rt.pnl_net, script_rt.pnl_net, abs_tol=1e-9)
    assert math.isclose(src_rt.fee_open_share, script_rt.fee_open_share, abs_tol=1e-9)
    assert math.isclose(src_rt.fee_close_share, script_rt.fee_close_share, abs_tol=1e-9)
    # Identity
    assert src_rt.side == script_rt.side, (
        f"side drift: src={src_rt.side!r} vs scripts={script_rt.side!r}"
    )
    assert math.isclose(src_rt.entry_px, script_rt.entry_px, abs_tol=1e-9), (
        f"entry_px drift: src={src_rt.entry_px} vs scripts={script_rt.entry_px} "
        f"(likely off-by-one open lot lookup)"
    )
    assert math.isclose(src_rt.exit_px, script_rt.exit_px, abs_tol=1e-9)
    assert math.isclose(src_rt.amount, script_rt.amount, abs_tol=1e-9)


@pytest.mark.asyncio
async def test_src_scripts_fifo_parity_cs_nonunit(engine):
    """cs=0.01: src (explicit cs) ↔ scripts (reads DB cs) 仍 byte-equal。

    10 张 long open@100000 close@101000, cs=0.01:
      gross = (101000-100000) × 10 × 0.01 = 100.0 USDT
      fee_open  = 100000 × 10 × 0.01 × 0.0005 = 5.0
      fee_close = 101000 × 10 × 0.01 × 0.0005 = 5.05
      pnl_net   = 100.0 - 10.05 = 89.95
    """
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    from scripts._sim_metrics import collect_roundtrips

    sid = "parity-cs"
    await _setup_synthetic_sim_session(
        engine, sid, fee_rate=0.0005, contract_size=0.01,
        fills=[
            ("open", "long", 100_000.0, 10.0),
            ("close", "long", 101_000.0, 10.0),
        ],
    )

    src_rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid, 0.01)
    script_rts, caveats = await collect_roundtrips(engine, sid)

    assert caveats["stale_close_amount_count"] == 0
    assert len(src_rts) == len(script_rts) == 1
    _assert_roundtrip_parity(src_rts[0], script_rts[0])

    # Verify cs is truly multiplied in (not just coincident): gross ≠ cs=1 result
    assert abs(src_rts[0].pnl_gross - 100.0) < 1e-6, (
        f"Expected gross=100.0 (×cs=0.01), got {src_rts[0].pnl_gross}"
    )


@pytest.mark.asyncio
async def test_src_scripts_fifo_parity_cs_nonunit_liquidation(engine):
    """cs=0.01 + liquidation: src ↔ scripts per-unit 口径一致（都不乘 cs）。

    Both sides read the stored arbitrary pnl=-200.0 and derive liq_pnl_per_unit
    = pnl / amount (per-张, money-denominated — no ×cs).  Parity confirms both
    FIFO implementations treat the stored value identically under cs≠1.
    """
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    from scripts._sim_metrics import collect_roundtrips

    sid = "parity-cs-liq"
    await _setup_synthetic_sim_session(engine, sid, 0.0005, contract_size=0.01, fills=[
        ("open", "long", 100_000.0, 10.0),
        ("liq", "long", 95_000.0, 10.0),
    ])
    src_rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid, 0.01)
    script_rts, caveats = await collect_roundtrips(engine, sid)

    assert len(src_rts) == len(script_rts) == 1
    assert caveats["liquidation_count"] == 1
    _assert_roundtrip_parity(src_rts[0], script_rts[0])
