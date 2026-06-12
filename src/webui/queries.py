"""WebUI 只读查询。纯函数：输入 engine + 参数，输出 schemas 模型。不写库。

模型/服务 import 一次性预置于此（Task 3 仅用 AgentCycle，其余 Task 4-7 才用上）——
仓库无 ruff/pre-commit F401 gate，逐 Task commit 期的"暂未用 import"不阻塞。
出站 datetime 的 UTC 归一化在 schemas 层（`UtcDatetime`），queries 不处理时区。"""
from __future__ import annotations

import json

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.storage.database import get_session
from src.storage.models import (
    AgentCycle, ToolCall, Session as SessionModel, SimPosition, SimOrder, TradeAction,
)
from src.services.metrics import MetricsService
from src.webui import schemas

_DECISION_HEAD_CHARS = 280


def _head(text_val: str | None) -> str | None:
    if not text_val:
        return None
    first = text_val.strip().split("\n", 1)[0]
    return first[:_DECISION_HEAD_CHARS]


async def get_cycles(
    engine: AsyncEngine, session_id: str, *,
    limit: int = 50, before_id: int | None = None, after_id: int | None = None,
) -> list[schemas.CycleRow]:
    stmt = select(AgentCycle).where(AgentCycle.session_id == session_id)
    if before_id is not None:
        stmt = stmt.where(AgentCycle.id < before_id)
    if after_id is not None:
        stmt = stmt.where(AgentCycle.id > after_id)
    stmt = stmt.order_by(AgentCycle.id.desc()).limit(limit)
    async with get_session(engine) as s:
        rows = list((await s.execute(stmt)).scalars().all())
    return [
        schemas.CycleRow(
            id=c.id, cycle_label=c.cycle_id, triggered_by=c.triggered_by,
            created_at=c.created_at, decision_head=_head(c.decision),
            tokens_consumed=c.tokens_consumed, wall_time_ms=c.wall_time_ms,
            execution_status=c.execution_status,
        ) for c in rows
    ]


def _loads(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw          # 截断的 outlier 行：回退原始字符串（spec 契约）


async def get_cycle_detail(engine: AsyncEngine, cycle_pk: int) -> schemas.CycleDetail | None:
    async with get_session(engine) as s:
        c = (await s.execute(
            select(AgentCycle).where(AgentCycle.id == cycle_pk)
        )).scalar_one_or_none()
        if c is None:
            return None
        tcs = list((await s.execute(
            select(ToolCall)
            .where(ToolCall.cycle_id == c.cycle_id, ToolCall.session_id == c.session_id)
            .order_by(ToolCall.id.asc())
        )).scalars().all())
    return schemas.CycleDetail(
        id=c.id, cycle_label=c.cycle_id, triggered_by=c.triggered_by, created_at=c.created_at,
        reasoning=c.reasoning, decision=c.decision,
        trigger_context=_loads(c.trigger_context), state_snapshot=_loads(c.state_snapshot),
        injected_events=_loads(c.injected_events),
        tool_calls=[
            schemas.ToolCallRow(tool_name=t.tool_name, status=t.status, duration_ms=t.duration_ms,
                                error_type=t.error_type, args=_loads(t.args)) for t in tcs
        ],
        tokens_consumed=c.tokens_consumed, input_tokens=c.input_tokens, output_tokens=c.output_tokens,
        cache_hit_rate=c.cache_hit_rate, wall_time_ms=c.wall_time_ms, llm_call_ms=c.llm_call_ms,
        model_id=c.model_id,
    )


async def get_live_status(engine: AsyncEngine, session_id: str) -> schemas.LiveStatus | None:
    async with get_session(engine) as s:
        sess = (await s.execute(
            select(SessionModel.status, SessionModel.last_active_at)
            .where(SessionModel.id == session_id)
        )).first()
        if sess is None:
            return None
        pos = (await s.execute(
            select(SimPosition).where(SimPosition.session_id == session_id)
        )).scalars().first()
        orders = list((await s.execute(
            select(SimOrder).where(SimOrder.session_id == session_id, SimOrder.status == "open")
            .order_by(SimOrder.created_at.asc())
        )).scalars().all())
        alerts = list((await s.execute(
            text("SELECT alert_id, target_price, registered_at, register_reasoning "
                 "FROM v_alert_lifecycle WHERE session_id=:sid AND final_status='active' "
                 "ORDER BY registered_at ASC"),
            {"sid": session_id},
        )).mappings().all())
    return schemas.LiveStatus(
        status=sess.status,
        last_active_at=sess.last_active_at,
        position=(schemas.PositionInfo(symbol=pos.symbol, side=pos.side, contracts=pos.contracts,
                                       entry_price=pos.entry_price, leverage=pos.leverage)
                  if pos else None),
        open_orders=[schemas.OrderInfo(order_id=o.order_id, side=o.side, order_type=o.order_type,
                                       amount=o.amount, trigger_price=o.trigger_price) for o in orders],
        active_alerts=[schemas.AlertInfo(alert_id=a["alert_id"], target_price=a["target_price"],
                                         registered_at=a["registered_at"],
                                         register_reasoning=a["register_reasoning"]) for a in alerts],
    )


def _current_position_label(pos: SimPosition | None) -> str:
    return pos.side if pos and pos.contracts else "none"


async def get_performance(engine: AsyncEngine, session_id: str) -> schemas.Performance | None:
    async with get_session(engine) as s:
        sess = (await s.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )).scalar_one_or_none()
        if sess is None:
            return None
        pos = (await s.execute(
            select(SimPosition).where(SimPosition.session_id == session_id)
        )).scalars().first()
        eq_rows = list((await s.execute(
            text("SELECT created_at AS at, "
                 "json_extract(state_snapshot,'$.balance.total_usdt') AS eq "
                 "FROM agent_cycles WHERE session_id=:sid ORDER BY id ASC"),
            {"sid": session_id},
        )).mappings().all())
        trades = list((await s.execute(
            select(TradeAction).where(TradeAction.session_id == session_id)
            .where(TradeAction.action == "order_filled")
            .order_by(TradeAction.id.asc())
        )).scalars().all())

    cur = _current_position_label(pos)
    m = await MetricsService(engine, session_id, sess.initial_balance).compute(current_position=cur)
    equity_curve = [
        schemas.EquityPoint(at=r["at"], equity=float(r["eq"]))
        for r in eq_rows if r["eq"] is not None
    ]
    return schemas.Performance(
        initial_balance=sess.initial_balance, current_position=cur,
        total_return_pct=m.total_return_pct, net_pnl=m.net_pnl, net_win_rate=m.net_win_rate,
        max_drawdown_pct=m.max_drawdown_pct, net_profit_factor=m.net_profit_factor,
        total_trades=m.total_trades, net_winning_trades=m.net_winning_trades,
        net_losing_trades=m.net_losing_trades, total_fees=m.total_fees,
        equity_curve=equity_curve,
        trades=[schemas.TradeRow(at=t.created_at, action=t.action, side=t.side, price=t.price,
                                 amount=t.amount, pnl=t.pnl, fee=t.fee) for t in trades],
    )


async def get_session_detail(engine: AsyncEngine, session_id: str) -> schemas.SessionDetail | None:
    async with get_session(engine) as s:
        sess = (await s.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )).scalar_one_or_none()
    if sess is None:
        return None
    return schemas.SessionDetail(
        id=sess.id, name=sess.name, symbol=sess.symbol, status=sess.status,
        timeframe=sess.timeframe, scheduler_interval_min=sess.scheduler_interval_min,
        initial_balance=sess.initial_balance, token_budget=sess.token_budget,
        created_at=sess.created_at, last_active_at=sess.last_active_at,
    )


async def list_sessions(engine: AsyncEngine) -> list[schemas.SessionSummary]:
    async with get_session(engine) as s:
        sessions = list((await s.execute(
            select(SessionModel).order_by(SessionModel.last_active_at.desc().nulls_last())
        )).scalars().all())
        counts = dict((await s.execute(
            select(AgentCycle.session_id, func.count()).group_by(AgentCycle.session_id)
        )).all())
    out = []
    for sess in sessions:
        m = await MetricsService(engine, sess.id, sess.initial_balance).compute()
        out.append(schemas.SessionSummary(
            id=sess.id, name=sess.name, symbol=sess.symbol, status=sess.status,
            created_at=sess.created_at, last_active_at=sess.last_active_at,
            cycle_count=counts.get(sess.id, 0), total_return_pct=m.total_return_pct,
        ))
    return out
