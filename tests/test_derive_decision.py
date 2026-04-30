"""Iter 4 §5.1 — _derive_decision_from_actions 单元测 + drift guard."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

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


async def test_t8_5_open_position_with_invalid_side_falls_through():
    """T8.5: open_position(side=None) + set_stop_loss 同 cycle → 'adjust'。

    spec §3.5: 派生函数对 side ∉ {'long', 'short'} 兜底 — skip 此 row 让 downstream 接管。
    实测 cycle = [open_position(side=None), set_stop_loss] 应返回 'adjust' 不是 'open_None'。
    """
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-85",
                         "open_position", side=None)
    await _insert_action(engine, "sess-derive-test", "cycle-85", "set_stop_loss")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-85"
        )
    assert result == "adjust", \
        f"side=None open_position 应被 skip 让 adjust 接管，实际 {result!r}"


async def test_t8_6_select_failure_falls_back_to_derive_error():
    """T8.6: SELECT 抛 SQLAlchemyError → fallback 'derive_error'（spec §3.2）。"""
    from src.cli.app import _derive_decision_from_actions

    # mock session.execute 抛 SQLAlchemyError
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=SQLAlchemyError("DB unreachable"))

    result = await _derive_decision_from_actions(
        mock_session, "sess-x", "cycle-x"
    )
    assert result == "derive_error", \
        f"DB 故障应 fallback 'derive_error'，实际 {result!r}"


_ACTION_LITERAL_RE = re.compile(
    r'_record_action\b[^)]*?\baction\s*=\s*["\']([a-z_]+)["\']',
    re.DOTALL,
)
_EXPECTED_RECORD_ACTION_SITES = 11  # spec §8.3 实测


def _grep_record_action_literals(path: str) -> set[str]:
    """单行正则扫 _record_action(...) 调用块的 action 字面量。
    Sanity check 站点数 == 11 防 regex false-empty。"""
    src = Path(path).read_text()
    matches = _ACTION_LITERAL_RE.findall(src)
    assert len(matches) == _EXPECTED_RECORD_ACTION_SITES, (
        f"扫描站点数 {len(matches)} ≠ 期望 {_EXPECTED_RECORD_ACTION_SITES}（spec §8.3）；"
        f"可能 regex 失效或站点被重命名/新增 — 实测命中: {matches}"
    )
    return set(matches)


def test_t11_adjust_actions_drift_guard():
    """T11: tools_execution.py 内所有 _record_action action 字面量
    必须落入 derive_decision_type 的分类，否则新增 action 漏分类会静默落 hold。

    spec §5.4 — 与 Iter 5 D' tests/test_trader_agent.py:69 同款纪律。
    """
    from src.cli.app import ADJUST_ACTIONS

    actual = _grep_record_action_literals("src/agent/tools_execution.py")
    expected = ADJUST_ACTIONS | {"open_position", "close_position", "set_next_wake"}
    drift = actual - expected
    assert not drift, \
        f"新增未分类的 action: {drift}（请更新 ADJUST_ACTIONS 或派生逻辑）"


def test_t12_derive_output_fits_decision_column():
    """T12: 派生函数输出 enum 字符串必须 ≤ DecisionLog.decision String(20)。

    spec §5.5 — 防未来加新 enum 超约束。
    legacy 不纳入此集合（historical-only，非派生函数运行时输出）。
    """
    enum_values = {"open_long", "open_short", "close", "adjust", "hold", "derive_error"}
    over_limit = [v for v in enum_values if len(v) > 20]
    assert not over_limit, f"派生输出 > 20 chars: {over_limit}"


async def test_t13_adjust_entry_order_derives_from_place_limit_order():
    """T13: cycle 仅含 place_limit_order → 'adjust_entry_order'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-13", "place_limit_order")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-13"
        )
    assert result == "adjust_entry_order"


async def test_t14_adjust_leverage_derives_from_adjust_leverage_action():
    """T14: cycle 仅含 adjust_leverage → 'adjust_leverage'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-14", "adjust_leverage")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-14"
        )
    assert result == "adjust_leverage"


async def test_t15_adjust_alert_derives_from_set_price_alert():
    """T15: cycle 仅含 set_price_alert → 'adjust_alert'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-15", "set_price_alert")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-15"
        )
    assert result == "adjust_alert"


async def test_t16_priority_protect_beats_alert_when_both_present():
    """T16: cycle 含 set_stop_loss + set_take_profit + add_price_level_alert (sim #4 fdf20e56 场景)
    → 'adjust_protect'（PROTECT 优先级高于 ALERT）。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-16", "set_stop_loss")
    await _insert_action(engine, "sess-derive-test", "cycle-16", "set_take_profit")
    await _insert_action(engine, "sess-derive-test", "cycle-16", "add_price_level_alert")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-16"
        )
    assert result == "adjust_protect", \
        f"sim #4 fdf20e56 场景应派生 'adjust_protect' (PROTECT > ALERT)，实际 {result!r}"


async def test_t17_priority_entry_order_beats_leverage_and_alert():
    """T17: cycle 含 place_limit_order + adjust_leverage + set_price_alert
    → 'adjust_entry_order'（ENTRY_ORDER 优先级高于 LEVERAGE/ALERT）。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-17", "place_limit_order")
    await _insert_action(engine, "sess-derive-test", "cycle-17", "adjust_leverage")
    await _insert_action(engine, "sess-derive-test", "cycle-17", "set_price_alert")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-17"
        )
    assert result == "adjust_entry_order"


async def test_t18_priority_leverage_beats_alert():
    """T18: cycle 含 adjust_leverage + set_price_alert → 'adjust_leverage'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-18", "adjust_leverage")
    await _insert_action(engine, "sess-derive-test", "cycle-18", "set_price_alert")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-18"
        )
    assert result == "adjust_leverage"
