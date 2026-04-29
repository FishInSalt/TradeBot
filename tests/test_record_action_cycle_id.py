"""Iter 3 _record_action cycle_id write tests.

Spec §5.3.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from src.agent.tools_execution import _record_action
from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, TradeAction


@pytest.fixture
async def engine(tmp_path: Path):
    eng = await init_db(f"sqlite+aiosqlite:///{tmp_path}/record.db")
    # Need a session row (FK requirement)
    async with get_session(eng) as session:
        session.add(SessionModel(
            id="test-session", name="test", symbol="BTC/USDT:USDT",
            initial_balance=100.0, status="active",
            exchange_type="simulated", timeframe="15m",
            scheduler_interval_min=15, approval_enabled=True,
            token_budget=500000,
        ))
        await session.commit()
    try:
        yield eng
    finally:
        await eng.dispose()    # cleanup _session_factories[id(engine)] avoid stale entries cross-test


def _make_deps(engine, cycle_id: str | None) -> MagicMock:
    deps = MagicMock()
    deps.session_id = "test-session"
    deps.symbol = "BTC/USDT:USDT"
    deps.db_engine = engine
    deps.cycle_id = cycle_id
    return deps


@pytest.mark.asyncio
async def test_record_action_writes_cycle_id(engine):
    deps = _make_deps(engine, "abc-123")
    await _record_action(deps, action="open_position", side="long", reasoning="r1")

    async with get_session(engine) as session:
        result = await session.execute(select(TradeAction.cycle_id).order_by(TradeAction.id.desc()).limit(1))
        cycle_id = result.scalar()
    assert cycle_id == "abc-123"


@pytest.mark.asyncio
async def test_record_action_writes_null_when_no_cycle_id(engine):
    """Tolerance path: deps.cycle_id is None → cycle_id NULL (schema nullable, legal)."""
    deps = _make_deps(engine, None)
    await _record_action(deps, action="open_position", side="long", reasoning="r1")

    async with get_session(engine) as session:
        result = await session.execute(select(TradeAction.cycle_id).order_by(TradeAction.id.desc()).limit(1))
        cycle_id = result.scalar()
    assert cycle_id is None
