"""Iter tool-opt-mdd-formula-align drift guards (G-calc-rigor-audit §G-3).

Cross-source drift guard: runtime `MetricsService.compute().max_drawdown_pct`
and offline `scripts/_sim_metrics.max_drawdown_pct(...)` must compute the
same equity-peak-based drawdown when fed identical inputs.

Runtime returns percentage (0..100); sim returns ratio (0..1). The guard
compares `runtime_pct ≈ sim_ratio * 100.0`.
"""
from __future__ import annotations

import json

import pytest

from src.storage.database import get_session, init_db
from src.storage.models import AgentCycle, Session, TradeAction


@pytest.fixture
async def db_with_session(tmp_path):
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/mdd_align.db")
    async with get_session(engine) as db:
        db.add(Session(id="sid", name="mdd-align", initial_balance=10000.0))
        await db.commit()
    yield engine
    await engine.dispose()


async def _seed_fills_and_cycles(engine, pnls: list[float], initial_balance: float = 10000.0) -> None:
    eq = initial_balance
    async with get_session(engine) as db:
        for i, pnl in enumerate(pnls):
            db.add(TradeAction(
                session_id="sid", action="order_filled",
                order_id=f"o-{i}", symbol="BTC/USDT:USDT", side="long",
                trigger_reason="market", pnl=pnl, fee=0.0,
                reasoning=f"(exchange: market filled #{i})",
            ))
            eq += pnl
            db.add(AgentCycle(
                session_id="sid", cycle_id=f"c-{i}",
                triggered_by="scheduled",
                state_snapshot=json.dumps({"balance": {"total_usdt": eq}}),
            ))
        await db.commit()


@pytest.mark.parametrize("pnls", [
    [100.0, -50.0, -30.0, 200.0],         # mild drawdown then recovery
    [500.0, -200.0, -100.0, -100.0],      # bigger drawdown from peak
    [-50.0, -50.0, 200.0, 50.0],          # initial drawdown before peak
    [10.0, 20.0, 30.0, 40.0],             # monotonic up — zero drawdown
])
async def test_runtime_mdd_matches_sim_mdd_on_same_inputs(db_with_session, pnls):
    """Runtime MDD (% form) and sim MDD (ratio form) must match on identical
    PnL/equity sequences. Driven across 4 representative drawdown shapes.
    """
    from scripts._sim_metrics import max_drawdown_pct as sim_mdd
    from src.services.metrics import MetricsService

    await _seed_fills_and_cycles(db_with_session, pnls)

    runtime_metrics = await MetricsService(
        engine=db_with_session, session_id="sid", initial_balance=10000.0,
    ).compute()
    sim_ratio = await sim_mdd(db_with_session, "sid")

    assert sim_ratio is not None
    assert runtime_metrics.max_drawdown_pct == pytest.approx(
        sim_ratio * 100.0, abs=1e-9,
    ), (
        f"pnls={pnls}: runtime_pct={runtime_metrics.max_drawdown_pct} "
        f"vs sim_ratio*100={sim_ratio * 100.0}"
    )
