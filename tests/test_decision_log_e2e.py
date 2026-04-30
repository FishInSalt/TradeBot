"""R2-4 §7.1 整合 e2e 测试 — sim #4 fdf20e56 场景回归。"""
from __future__ import annotations

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, TradeAction


async def test_decision_log_writes_adjust_protect_for_post_fill_protection():
    """sim #4 fdf20e56 端到端: cycle 含 set_stop_loss + set_take_profit + add_price_level_alert
    → DecisionLog.decision = 'adjust_protect'。

    R2-4 spec §1.1 / §5.4 矩阵第一行回归 — 核心保护事件浮现，不再被「续约 alert」语义掩盖。
    """
    from src.cli.app import _derive_decision_from_actions

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id="sess-fdf20e56", name="sim4-replay"))
        await db.commit()

    cycle_id = "fdf20e56"
    actions = [
        ("set_stop_loss", None),
        ("set_take_profit", None),
        ("add_price_level_alert", None),
        ("set_next_wake", None),
    ]
    async with get_session(engine) as db:
        for action, side in actions:
            db.add(TradeAction(
                session_id="sess-fdf20e56",
                cycle_id=cycle_id,
                action=action,
                symbol="BTC/USDT:USDT",
                side=side,
            ))
        await db.commit()

    async with get_session(engine) as session:
        decision = await _derive_decision_from_actions(
            session, "sess-fdf20e56", cycle_id
        )

    assert decision == "adjust_protect", (
        f"sim #4 fdf20e56 (post-fill 首挂 SL/TP + 续约 alert) "
        f"应派生 'adjust_protect'（PROTECT > ALERT）；实际 {decision!r}。"
        f" 这是 R2-4 spec §1.1 P0-3 阻塞场景的核心回归。"
    )
