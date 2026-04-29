"""Iter 4 §5.1 — _derive_decision_from_actions 单元测 + drift guard."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, TradeAction


async def _make_engine_with_session(session_id: str = "sess-derive-test"):
    """In-memory SQLite + 1 个 SessionModel (FK target)。"""
    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="derive-test"))
        await db.commit()
    return engine


async def _insert_action(engine, session_id: str, cycle_id: str,
                         action: str, side: str | None = None):
    """插一行 TradeAction 到测试 DB。"""
    async with get_session(engine) as db:
        db.add(TradeAction(
            session_id=session_id,
            cycle_id=cycle_id,
            action=action,
            symbol="BTC/USDT:USDT",
            side=side,
        ))
        await db.commit()


async def test_t5_zero_actions_returns_hold():
    """T5: cycle 0 actions → 'hold'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-empty"
        )
    assert result == "hold"
