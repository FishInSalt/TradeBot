"""WebUI 只读查询。纯函数：输入 engine + 参数，输出 schemas 模型。不写库。

出站 datetime 的 UTC 归一化在 schemas 层（`UtcDatetime`），queries 不处理时区。"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import aliased

from src.storage.database import get_session
from src.storage.models import (
    AgentCycle, ToolCall, Session as SessionModel, SimPosition, SimOrder, TradeAction,
)
from src.services.metrics import MetricsService
from src.webui import schemas
from src.services.event_render import _format_event_age


async def get_cycles(
    engine: AsyncEngine, session_id: str, *,
    limit: int = 50, before_id: int | None = None, after_id: int | None = None,
) -> list[schemas.CycleRow]:
    # seq = 会话内 1-based 绝对序号：row_number 须在【游标过滤之前】对全量 session 子集开窗
    # （子查询），外层再套游标 + 方向排序 + limit；否则 after_id 翻页会从游标处重启序号。
    inner = (
        select(AgentCycle, func.row_number().over(order_by=AgentCycle.id.asc()).label("seq"))
        .where(AgentCycle.session_id == session_id)
        .subquery()
    )
    ac = aliased(AgentCycle, inner)
    stmt = select(ac, inner.c.seq)
    if before_id is not None:
        stmt = stmt.where(inner.c.id < before_id)
    if after_id is not None:
        stmt = stmt.where(inner.c.id > after_id)
    # after_id（取更新方向）须取紧邻游标的 n 条（ASC）再 reverse，否则 DESC+LIMIT 会返回
    # 游标之上「最新」的 n 条、新增数 > limit 时静默跳过紧邻那批 → 时间线空洞。
    if after_id is not None:
        stmt = stmt.order_by(inner.c.id.asc()).limit(limit)
    else:
        stmt = stmt.order_by(inner.c.id.desc()).limit(limit)
    async with get_session(engine) as s:
        result = list((await s.execute(stmt)).all())     # [(AgentCycle, seq), ...]
        # 批量 join tool_calls（一次查整批 cycle，feed limit≤200）；按 cycle_id 分组、保留执行序
        cycle_ids = [c.cycle_id for c, _ in result]
        tool_rows = []
        if cycle_ids:
            tool_rows = list((await s.execute(
                select(ToolCall.cycle_id, ToolCall.tool_name, ToolCall.args)
                .where(ToolCall.session_id == session_id, ToolCall.cycle_id.in_(cycle_ids))
                .order_by(ToolCall.id.asc())
            )).all())
    if after_id is not None:
        result.reverse()          # 统一为 id DESC 输出（最新在前），rows/seq 同步 reverse
    tools_by_cycle: dict[str, list[tuple[str, object]]] = {}
    for cid, tname, targs in tool_rows:
        tools_by_cycle.setdefault(cid, []).append((tname, _loads(targs)))
    return [
        schemas.CycleRow(
            id=c.id, seq=seq, cycle_label=c.cycle_id, triggered_by=c.triggered_by,
            created_at=c.created_at, tokens_consumed=c.tokens_consumed,
            wall_time_ms=c.wall_time_ms, execution_status=c.execution_status,
            position=_safe(lambda c=c: _derive_position(_loads(c.state_snapshot))),
            key_events=_derive_key_events(c, tools_by_cycle.get(c.cycle_id, [])),
        ) for c, seq in result
    ]


def _derive_key_events(c, tools: list[tuple[str, object]]) -> list[schemas.KeyEvent]:
    """组装本轮 key_events：被动 fill（trigger_context，在前）+ 主动动作（tool_calls，在后）。
    每事件 _safe 包裹——单事件异常跳过、不阻断 feed。"""
    prev_side = None
    pos = _safe(lambda: _derive_position(_loads(c.state_snapshot)))
    if pos is not None:
        prev_side = pos.side
    events: list[schemas.KeyEvent] = []
    for item in _normalize_to_list(_loads(c.trigger_context)):
        if item.get("type") == "fill":
            ev = _safe(lambda item=item: _classify_fill(item))
            if ev is not None:
                events.append(ev)
    for tname, targs in tools:
        ev = _safe(lambda tname=tname, targs=targs: _classify_action(tname, targs, prev_side))
        if ev is not None:
            events.append(ev)
    return events


def _loads(raw: str | None):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw          # 截断的 outlier 行：回退原始字符串（spec 契约）


def _classify_fill(fill: dict) -> schemas.KeyEvent | None:
    """trigger_context 里单个 fill dict → KeyEvent。
    market 回声 = 历史会话旧派发产物（spec §2），跳过去重 → None。
    pnl is None → 开仓型；pnl≠None 且 is_full_close → 全平；否则部分平。"""
    reason = fill.get("trigger_reason")
    if reason == "market":
        return None
    side = fill.get("position_side")
    d = "多" if side == "long" else "空" if side == "short" else "?"
    if fill.get("pnl") is None:
        return schemas.KeyEvent(kind="fill_open", label=f"限价开{d}", direction=side)
    if fill.get("is_full_close"):
        label = {"stop": "止损平仓", "take_profit": "止盈平仓",
                 "liquidation": "强平", "limit": "限价平仓"}.get(reason, "平仓")
        return schemas.KeyEvent(kind="fill_close", label=label, direction=side)
    return schemas.KeyEvent(kind="fill_partial", label="部分平仓", direction=side)


def _injection_kind_label(event) -> str:
    """注入事件人读标题。fill 复用 _classify_fill（单一权威来源、消除前端 fill 词汇漂移）；
    _classify_fill 对 trigger_reason=='market' 回 None → 泛标题「成交」（禁直接 .label，
    否则 None.label AttributeError）。其余静态标题在此一处定义；未知类型 → 泛标题「事件」。"""
    if not isinstance(event, dict):
        return "事件"
    etype = event.get("type")
    if etype == "fill":
        kl = _classify_fill(event)
        return kl.label if kl else "成交"
    if etype == "percentage_alert":
        return "波动告警触发"
    if etype == "price_level_alert":
        return "价格告警触发"
    return "事件"


def _injection_age(base_aware: datetime, end_aware: datetime, rec: dict) -> str | None:
    """注入事件 age（英文 ladder，复用 event_render._format_event_age）。
    base_aware = aware UTC 的 cycle 开始时刻；injection_moment = base + offset_ms；
    end_aware = aware UTC 的 cycle 结束时刻（created_at）。
    event None / 缺 timestamp → None。

    刚触发事件修正：injection_moment=(created_at−wall)+offset 是估算，事件因果上必先于
    其触发的注入，但估算噪声可让 event_ts 微落"未来"（实测 price_level_alert ~0.2s）→
    _format_event_age 的 `then>now` 会吞成 None（右上无 age 片）。若 event_ts 仍落在
    cycle 窗口内（≤ 结束），视为估算噪声 → "just now"；仅真晚于 cycle 结束（异常/损坏戳）
    才保留 None（仅显 UTC）。原则性边界（cycle 窗口），非魔数 tolerance。"""
    event = rec.get("event")
    if not isinstance(event, dict) or event.get("timestamp") is None:
        return None
    injection_moment = base_aware + timedelta(milliseconds=rec.get("offset_ms") or 0)
    event_ts = datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)   # aware
    age = _format_event_age(injection_moment, event_ts)
    if age is None and event_ts <= end_aware:
        return "just now"
    return age


def _enrich_injected_events(raw, created_at, wall_time_ms):
    """富化 injected_events 供 WebUI 注入卡：每条加 triggered_ago + kind_label。
    仅处理 list[dict-with-'event'] 形态；其他形态（dict/str/None）原样返回。

    逐条 try/except 降级裸 event —— get_cycle_detail 无外层 try/except，任一事件富化
    异常绝不冒泡成 500（spec §6 / P0）。

    ⚠ P0：c.created_at 经 ORM 从 SQLite 读回是 naive；不补 aware UTC 则 _format_event_age
    首行 `then > now`（aware event_ts vs naive now）抛 TypeError → 500，且只在有注入事件的
    cycle 触发。补 tz 与 _ensure_utc 同模式。created_at 是 cycle 结束时刻；开始 ≈ created_at − wall_time_ms。"""
    if not isinstance(raw, list):
        return raw
    end_aware = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=timezone.utc)
    base = end_aware if wall_time_ms is None else end_aware - timedelta(milliseconds=wall_time_ms)
    out = []
    for rec in raw:
        if not isinstance(rec, dict) or "event" not in rec:
            out.append(rec)
            continue
        try:
            enriched = dict(rec)
            enriched["triggered_ago"] = _injection_age(base, end_aware, rec)
            enriched["kind_label"] = _injection_kind_label(rec.get("event"))
            out.append(enriched)
        except Exception:
            out.append(rec)        # 降级裸 event（不附富化字段）
    return out


def _classify_action(tool_name: str, args, prev_side: str | None) -> schemas.KeyEvent | None:
    """本轮单个 tool_call → KeyEvent。prev_side = 操作前持仓方向（state_snapshot.position）。
    非交易工具 → None。args 非 dict（截断回退）→ 当空 dict 处理。"""
    a = args if isinstance(args, dict) else {}
    if tool_name == "open_position":
        side = a.get("side")
        d = "多" if side == "long" else "空" if side == "short" else "?"
        if prev_side is None:
            return schemas.KeyEvent(kind="open", label=f"开{d}", direction=side)
        if prev_side == side:
            return schemas.KeyEvent(kind="add", label="加仓", direction=side)
        return schemas.KeyEvent(kind="flip", label=f"反手→{d}", direction=side)
    if tool_name == "close_position":
        d = "多" if prev_side == "long" else "空" if prev_side == "short" else ""
        return schemas.KeyEvent(kind="close", label=f"平{d}" if d else "平仓", direction=prev_side)
    if tool_name == "place_limit_order":
        side = a.get("side")
        d = "多" if side == "long" else "空" if side == "short" else "?"
        return schemas.KeyEvent(kind="limit_order", label=f"挂限价单·{d}", direction=side)
    return None


def _derive_position(snapshot) -> schemas.PositionBrief | None:
    """state_snapshot.position → PositionBrief。flat（position=None / contracts=0）
    或异常形态（snapshot 非 dict）→ None。"""
    if not isinstance(snapshot, dict):
        return None
    pos = snapshot.get("position")
    if not isinstance(pos, dict):
        return None
    side, contracts = pos.get("side"), pos.get("contracts")
    if not side or not contracts:
        return None
    return schemas.PositionBrief(side=side, contracts=contracts, entry_price=pos.get("entry_price"))


def _normalize_to_list(raw) -> list:
    """trigger_context 形态归一（schemas.py:72 已放宽为 dict|list|str|None）：
    list → 仅保留 dict 元素；dict → 单元素 list；其他 → []。"""
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _safe(fn):
    """派生 fail-isolate：单事件解析异常 → None（沿用 #78 _safe_* 风格），不阻断 feed。"""
    try:
        return fn()
    except Exception:
        return None


async def get_cycle_detail(engine: AsyncEngine, cycle_pk: int) -> schemas.CycleDetail | None:
    async with get_session(engine) as s:
        c = (await s.execute(
            select(AgentCycle).where(AgentCycle.id == cycle_pk)
        )).scalar_one_or_none()
        if c is None:
            return None
        seq = (await s.execute(
            select(func.count()).select_from(AgentCycle)
            .where(AgentCycle.session_id == c.session_id, AgentCycle.id <= c.id)
        )).scalar_one()
        tcs = list((await s.execute(
            select(ToolCall)
            .where(ToolCall.cycle_id == c.cycle_id, ToolCall.session_id == c.session_id)
            .order_by(ToolCall.id.asc())
        )).scalars().all())
    return schemas.CycleDetail(
        id=c.id, seq=seq, cycle_label=c.cycle_id, triggered_by=c.triggered_by, created_at=c.created_at,
        reasoning=c.reasoning, decision=c.decision,
        trigger_context=_loads(c.trigger_context), state_snapshot=_loads(c.state_snapshot),
        injected_events=_enrich_injected_events(_loads(c.injected_events), c.created_at, c.wall_time_ms),
        tool_calls=[
            schemas.ToolCallRow(tool_name=t.tool_name, status=t.status, duration_ms=t.duration_ms,
                                error_type=t.error_type, args=_loads(t.args),
                                result=t.result, tool_call_id=t.tool_call_id) for t in tcs    # raw str 直传，不走 _loads（截断行永非合法 JSON）
        ],
        tokens_consumed=c.tokens_consumed, input_tokens=c.input_tokens, output_tokens=c.output_tokens,
        cache_hit_rate=c.cache_hit_rate, wall_time_ms=c.wall_time_ms, llm_call_ms=c.llm_call_ms,
        model_id=c.model_id,
        react_steps=_loads(c.react_steps),
        user_prompt_snapshot=c.user_prompt_snapshot,
        execution_status=c.execution_status,
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
        system_prompt=sess.system_prompt,
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
