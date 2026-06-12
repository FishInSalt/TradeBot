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
