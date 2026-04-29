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


async def test_t1_open_long_derives():
    """T1: cycle 含 open_position(side='long') → 'open_long'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-1",
                         "open_position", side="long")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-1"
        )
    assert result == "open_long"


async def test_t2_open_short_derives():
    """T2: cycle 含 open_position(side='short') → 'open_short'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-2",
                         "open_position", side="short")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-2"
        )
    assert result == "open_short"


async def test_t3_close_derives():
    """T3: cycle 含 close_position（无 open）→ 'close'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-3",
                         "close_position", side="long")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-3"
        )
    assert result == "close"


async def test_t4_adjust_derives_from_set_stop_loss():
    """T4: cycle 仅含 set_stop_loss → 'adjust'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-4", "set_stop_loss")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-4"
        )
    assert result == "adjust"


async def test_t6_set_next_wake_only_returns_hold():
    """T6: cycle 仅含 set_next_wake → 'hold'（spec §C5 决议：set_next_wake 单独归 hold）。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-6", "set_next_wake")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-6"
        )
    assert result == "hold"


async def test_t7_priority_open_beats_adjust():
    """T7: cycle 含 open_position + set_stop_loss 同 cycle → 'open_long'（早期返回拦截）。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-7",
                         "open_position", side="long")
    await _insert_action(engine, "sess-derive-test", "cycle-7", "set_stop_loss")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-7"
        )
    assert result == "open_long"


async def test_t8_session_isolation():
    """T8: session_A cycle X 有 open；session_B 同 cycle_id 无 actions → 派生 session_B 返回 'hold'。

    cycle_id 实测是 UUID4 前 8 chars (spec §5.1 T8 实操含义)，
    单 session 内碰撞极低但跨 session 长尾可能重复 → 防 SELECT 漏 session_id WHERE 子句。
    """
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session(session_id="sess-A")
    # 加 sess-B 也作 FK target
    async with get_session(engine) as db:
        db.add(SessionModel(id="sess-B", name="other-session"))
        await db.commit()

    # session_A cycle X 有 open_position
    await _insert_action(engine, "sess-A", "cycle-shared",
                         "open_position", side="long")

    # 查 session_B 同 cycle_id → 应返回 hold（不互窜）
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-B", "cycle-shared"
        )
    assert result == "hold"
