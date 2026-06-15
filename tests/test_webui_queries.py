import json

import pytest
from datetime import datetime, timezone, timedelta

from src.storage.database import get_session
from src.storage.models import Session as SessionModel, AgentCycle, ToolCall

UTC = timezone.utc


async def _seed_session(engine, sid="s1", interval=15, last_active=None, status="active"):
    async with get_session(engine) as s:
        s.add(SessionModel(id=sid, name=sid, symbol="BTC/USDT:USDT",
                           initial_balance=10000.0, status=status,
                           scheduler_interval_min=interval, last_active_at=last_active))
        await s.commit()


async def _add_cycle(engine, sid="s1", cycle_id="aaaa", triggered_by="scheduled",
                     decision="line1\nline2", created_at=None, trigger_context=None,
                     injected_events=None, **kw):
    # live capture 把 trigger_context 落库为 JSON list（多触发堆）——稳态主流形态，
    # 默认即用 list，避免 fixture 恒 NULL 漏掉 list→CycleDetail 的真实路径（PR#75 500 教训）。
    if trigger_context is None:
        trigger_context = [{"type": "scheduled_tick"}]
    async with get_session(engine) as s:
        c = AgentCycle(session_id=sid, cycle_id=cycle_id, triggered_by=triggered_by,
                       decision=decision, tokens_consumed=kw.get("tokens", 100),
                       wall_time_ms=kw.get("wall", 5000), execution_status="ok",
                       created_at=created_at or datetime.now(UTC),
                       trigger_context=json.dumps(trigger_context),
                       injected_events=(json.dumps(injected_events)
                                        if injected_events is not None else None),
                       state_snapshot=kw.get("snapshot"))
        s.add(c)
        await s.commit()
        return c.id


@pytest.mark.asyncio
async def test_get_cycles_orders_desc_and_paginates(engine):
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    ids = []
    for i in range(5):
        ids.append(await _add_cycle(engine, cycle_id=f"c{i}", created_at=base + timedelta(minutes=i)))
    from src.webui.queries import get_cycles
    rows = await get_cycles(engine, "s1", limit=2)
    assert [r.id for r in rows] == [ids[4], ids[3]]          # 最新在前
    older = await get_cycles(engine, "s1", limit=2, before_id=ids[3])
    assert [r.id for r in older] == [ids[2], ids[1]]
    newer = await get_cycles(engine, "s1", after_id=ids[3])
    assert [r.id for r in newer] == [ids[4]]
    assert rows[0].position is None and rows[0].key_events == []


@pytest.mark.asyncio
async def test_get_cycles_gap_since_prev_normal_and_first_null(engine):
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _add_cycle(engine, cycle_id="c0", created_at=base, wall=5000)
    await _add_cycle(engine, cycle_id="c1", created_at=base + timedelta(minutes=10), wall=5000)
    from src.webui.queries import get_cycles
    rows = {r.cycle_label: r for r in await get_cycles(engine, "s1")}
    assert rows["c0"].gap_since_prev_ms is None            # 首轮无上轮
    assert rows["c1"].gap_since_prev_ms == 595000          # 10min 间隔 − 5s wall = 595000ms


@pytest.mark.asyncio
async def test_get_cycles_gap_since_prev_null_when_wall_missing(engine):
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _add_cycle(engine, cycle_id="c0", created_at=base, wall=5000)
    await _add_cycle(engine, cycle_id="c1", created_at=base + timedelta(minutes=10), wall=None)
    from src.webui.queries import get_cycles
    rows = {r.cycle_label: r for r in await get_cycles(engine, "s1")}
    assert rows["c1"].gap_since_prev_ms is None            # wall=None 推不出本轮 start


@pytest.mark.asyncio
async def test_get_cycles_gap_since_prev_clamps_negative_to_zero(engine):
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _add_cycle(engine, cycle_id="c0", created_at=base, wall=5000)
    await _add_cycle(engine, cycle_id="c1", created_at=base + timedelta(seconds=1), wall=5000)
    from src.webui.queries import get_cycles
    rows = {r.cycle_label: r for r in await get_cycles(engine, "s1")}
    assert rows["c1"].gap_since_prev_ms == 0               # (1s − 5s wall) 负 → 归 0


@pytest.mark.asyncio
async def test_get_cycles_after_id_no_gap_when_new_exceeds_limit(engine):
    """after_id 增量轮询：新增数 > limit 时取紧邻游标的一批（不跳过中间 cycle）。"""
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    ids = [await _add_cycle(engine, cycle_id=f"g{i}", created_at=base + timedelta(minutes=i))
           for i in range(5)]
    from src.webui.queries import get_cycles
    # 游标 ids[0] 之上有 4 条新增（ids[1..4]），limit=2：应取紧邻的 ids[1]/ids[2]，
    # 而非最新的 ids[3]/ids[4]（DESC 旧实现会跳过 ids[1]/ids[2] → 空洞）。
    rows = await get_cycles(engine, "s1", limit=2, after_id=ids[0])
    assert [r.id for r in rows] == [ids[2], ids[1]]     # DESC 输出，但取的是紧邻游标那批
    assert ids[1] in [r.id for r in rows]               # 紧邻游标的 cycle 不被跳过


@pytest.mark.asyncio
async def test_get_cycle_detail_joins_tool_calls_as_children(engine):
    await _seed_session(engine)
    pk = await _add_cycle(engine, cycle_id="d1",
                          snapshot='{"balance":{"total_usdt":10050.0},"position":null}')
    async with get_session(engine) as s:
        for i, name in enumerate(["get_position", "get_market_data"]):
            s.add(ToolCall(session_id="s1", cycle_id="d1", tool_name=name, status="ok",
                           duration_ms=10 + i, args='{"symbol":"BTC/USDT:USDT"}'))
        await s.commit()
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert d.cycle_label == "d1"
    assert [t.tool_name for t in d.tool_calls] == ["get_position", "get_market_data"]
    assert d.tool_calls[0].args == {"symbol": "BTC/USDT:USDT"}
    assert d.state_snapshot["balance"]["total_usdt"] == 10050.0


@pytest.mark.asyncio
async def test_get_cycle_detail_missing_returns_none(engine):
    await _seed_session(engine)
    from src.webui.queries import get_cycle_detail
    assert await get_cycle_detail(engine, 99999) is None


@pytest.mark.asyncio
async def test_get_cycle_detail_accepts_list_trigger_context(engine):
    """live capture 把 trigger_context 写成 JSON list（多触发堆）；CycleDetail 须接受 list 不 500（PR#75 回归）。"""
    await _seed_session(engine)
    pk = await _add_cycle(engine, cycle_id="lc1",
                          trigger_context=[{"type": "scheduled_tick"},
                                           {"type": "alert", "alert_id": "a1"}])
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert isinstance(d.trigger_context, list)
    assert d.trigger_context[0]["type"] == "scheduled_tick"
    assert d.trigger_context[1]["alert_id"] == "a1"


@pytest.mark.asyncio
async def test_tool_call_result_none_when_not_captured(engine):
    """未捕获 result 的行（NULL）→ ToolCallRow.result is None。"""
    await _seed_session(engine)
    pk = await _add_cycle(engine, cycle_id="tr1")
    async with get_session(engine) as s:
        s.add(ToolCall(session_id="s1", cycle_id="tr1", tool_name="get_position",
                       status="ok", duration_ms=5, args=None, result=None))
        await s.commit()
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert d.tool_calls[0].result is None


@pytest.mark.asyncio
async def test_tool_call_result_passthrough_raw_str(engine):
    """捕获的 result（文本表格，非 JSON）→ 直传 raw str（不走 _loads）。"""
    await _seed_session(engine)
    pk = await _add_cycle(engine, cycle_id="tr2")
    async with get_session(engine) as s:
        s.add(ToolCall(session_id="s1", cycle_id="tr2", tool_name="get_market_data",
                       status="ok", duration_ms=8, args=None,
                       result="=== Ticker ===\nlast 63000"))
        await s.commit()
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert d.tool_calls[0].result == "=== Ticker ===\nlast 63000"   # raw str，原样


@pytest.mark.asyncio
async def test_get_live_status_assembles(engine):
    la = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _seed_session(engine, interval=15, last_active=la, status="active")
    from src.storage.models import SimPosition, SimOrder, TradeAction
    async with get_session(engine) as s:
        s.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="long",
                          contracts=1.0, entry_price=63000.0, leverage=5))
        s.add(SimOrder(session_id="s1", order_id="o1", symbol="BTC/USDT:USDT", side="sell",
                       position_side="long", order_type="stop", amount=1.0, trigger_price=62000.0,
                       status="open", leverage=5))
        # v_alert_lifecycle registers CTE 认 action='add_price_level_alert'（views.py:104）
        s.add(TradeAction(session_id="s1", action="add_price_level_alert", alert_id="a1",
                          symbol="BTC/USDT:USDT", price=64000.0, reasoning="breakout"))
        await s.commit()
    from src.webui.queries import get_live_status
    ls = await get_live_status(engine, "s1")
    assert ls.status == "active"
    assert ls.last_active_at == la
    assert ls.position.side == "long" and ls.position.contracts == 1.0
    assert [o.order_id for o in ls.open_orders] == ["o1"]
    assert any(a.alert_id == "a1" for a in ls.active_alerts)


@pytest.mark.asyncio
async def test_get_live_status_tokens_consumed_total_sums_cycles(engine):
    await _seed_session(engine)
    await _add_cycle(engine, cycle_id="c0", tokens=1200)
    await _add_cycle(engine, cycle_id="c1", tokens=3400)
    from src.webui.queries import get_live_status
    ls = await get_live_status(engine, "s1")
    assert ls.tokens_consumed_total == 4600


@pytest.mark.asyncio
async def test_get_live_status_tokens_consumed_total_zero_when_no_cycles(engine):
    # 全新会话（0 cycle）：SQLite SUM 空集返 NULL，须 coalesce 到 0，否则 int 字段 pydantic 校验失败
    await _seed_session(engine)
    from src.webui.queries import get_live_status
    ls = await get_live_status(engine, "s1")
    assert ls.tokens_consumed_total == 0


@pytest.mark.asyncio
async def test_get_performance_equity_skips_none_balance(engine):
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _add_cycle(engine, cycle_id="c1", created_at=base,
                     snapshot='{"balance":{"total_usdt":10000.0}}')
    await _add_cycle(engine, cycle_id="c2", created_at=base + timedelta(minutes=15),
                     snapshot='{"balance":null}')          # 失败点 → 跳过
    await _add_cycle(engine, cycle_id="c3", created_at=base + timedelta(minutes=30),
                     snapshot='{"balance":{"total_usdt":10120.0}}')
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")
    assert perf.initial_balance == 10000.0
    assert [round(p.equity, 1) for p in perf.equity_curve] == [10000.0, 10120.0]   # null 被跳


@pytest.mark.asyncio
async def test_get_cycle_detail_returns_react_fields(engine):
    """get_cycle_detail 透传 react_steps / user_prompt_snapshot / execution_status / tool_call_id。"""
    import sqlalchemy
    await _seed_session(engine)
    react = json.dumps([{"thinking": "t1", "tools": [
        {"tool_call_id": "call_1", "tool_name": "get_position"}]}])
    async with get_session(engine) as s:
        c = AgentCycle(
            session_id="s1", cycle_id="react1", triggered_by="scheduled",
            execution_status="ok", decision="(1) Stance: hold",
            user_prompt_snapshot="Woke by scheduled tick",
            react_steps=react,
        )
        s.add(c)
        await s.commit()
        pk = (await s.execute(
            sqlalchemy.select(AgentCycle.id).where(AgentCycle.cycle_id == "react1")
        )).scalar_one()
        s.add(ToolCall(
            session_id="s1", cycle_id="react1", tool_name="get_position",
            status="ok", duration_ms=12, tool_call_id="call_1", result="flat",
        ))
        await s.commit()

    from src.webui.queries import get_cycle_detail
    detail = await get_cycle_detail(engine, pk)
    assert detail.execution_status == "ok"
    assert detail.user_prompt_snapshot == "Woke by scheduled tick"
    assert detail.react_steps[0]["tools"][0]["tool_call_id"] == "call_1"
    assert detail.tool_calls[0].tool_call_id == "call_1"


@pytest.mark.asyncio
async def test_list_sessions_summary(engine):
    la = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _seed_session(engine, sid="s1", interval=15, last_active=la)
    await _add_cycle(engine, sid="s1", cycle_id="c1", created_at=la)
    await _add_cycle(engine, sid="s1", cycle_id="c2", created_at=la + timedelta(minutes=5))
    from src.webui.queries import list_sessions, get_session_detail
    rows = await list_sessions(engine)
    assert len(rows) == 1
    assert rows[0].cycle_count == 2
    assert rows[0].status == "active"
    detail = await get_session_detail(engine, "s1")
    assert detail.scheduler_interval_min == 15
    assert await get_session_detail(engine, "nope") is None


@pytest.mark.asyncio
async def test_list_sessions_exposes_net_return_pct(engine):
    """SessionSummary 暴露 net_return_pct（净收益率，已扣手续费）——与毛 total_return_pct 区分。
    左侧会话列表用净口径横向对比，避免毛收益率高估（手续费占比可观）。"""
    from src.storage.models import TradeAction
    await _seed_session(engine)   # initial_balance=10000.0
    async with get_session(engine) as s:
        s.add(TradeAction(session_id="s1", action="order_filled", symbol="BTC/USDT:USDT",
                          side="long", price=65000.0, amount=0.1, fee=5.0, pnl=None,
                          entry_price=None, trigger_reason="market"))
        s.add(TradeAction(session_id="s1", action="order_filled", symbol="BTC/USDT:USDT",
                          side="long", price=66000.0, amount=0.1, fee=5.0, pnl=100.0,
                          entry_price=65000.0, trigger_reason="take_profit"))
        await s.commit()
    from src.webui.queries import list_sessions
    rows = await list_sessions(engine)
    # gross =(66000−65000)*0.1*1*cs(1.0) = +100 → 1.0%；net = 100 − 5 − 5 = 90 → 0.9%
    assert round(rows[0].total_return_pct, 4) == 1.0
    assert round(rows[0].net_return_pct, 4) == 0.9


@pytest.mark.asyncio
async def test_get_session_detail_exposes_system_prompt(engine):
    """SessionDetail 暴露 Session.system_prompt（会话固定 persona）。"""
    async with get_session(engine) as s:
        s.add(SessionModel(id="sp1", name="sp1", symbol="BTC/USDT:USDT",
                           initial_balance=10000.0, status="active",
                           scheduler_interval_min=15,
                           system_prompt="You are a disciplined futures trader."))
        await s.commit()
    from src.webui.queries import get_session_detail
    d = await get_session_detail(engine, "sp1")
    assert d.system_prompt == "You are a disciplined futures trader."


def _fill(reason, *, pnl=None, full=False, side="long"):
    return {"type": "fill", "trigger_reason": reason, "position_side": side,
            "pnl": pnl, "is_full_close": full, "side": "buy", "amount": 1.0,
            "fill_price": 63000.0, "fee": 0.1, "order_id": "o1", "timestamp": 0}


def test_classify_fill_branches():
    from src.webui.queries import _classify_fill
    # 限价开仓（pnl is None）
    e = _classify_fill(_fill("limit", pnl=None, side="long"))
    assert (e.kind, e.label, e.direction) == ("fill_open", "限价开多", "long")
    e = _classify_fill(_fill("limit", pnl=None, side="short"))
    assert e.label == "限价开空"
    # 止损 / 止盈 / 强平 / 限价平（pnl≠None 且 full close）
    assert _classify_fill(_fill("stop", pnl=-50.0, full=True)).label == "止损平仓"
    assert _classify_fill(_fill("take_profit", pnl=80.0, full=True)).label == "止盈平仓"
    assert _classify_fill(_fill("liquidation", pnl=-200.0, full=True)).label == "强平"
    assert _classify_fill(_fill("limit", pnl=30.0, full=True)).label == "限价平仓"
    for r in ("stop", "take_profit", "liquidation", "limit"):
        assert _classify_fill(_fill(r, pnl=1.0, full=True)).kind == "fill_close"
    # 部分平（pnl≠None 非 full close）
    e = _classify_fill(_fill("stop", pnl=10.0, full=False))
    assert (e.kind, e.label) == ("fill_partial", "部分平仓")
    # market 回声 → 跳过（去重）
    assert _classify_fill(_fill("market", pnl=None)) is None
    assert _classify_fill(_fill("market", pnl=50.0, full=True)) is None


def test_classify_action_branches():
    from src.webui.queries import _classify_action
    # open_position：前 flat → open；前同向 → add；前反向 → flip
    e = _classify_action("open_position", {"side": "long"}, None)
    assert (e.kind, e.label, e.direction) == ("open", "开多", "long")
    assert _classify_action("open_position", {"side": "short"}, None).label == "开空"
    assert _classify_action("open_position", {"side": "long"}, "long").kind == "add"
    e = _classify_action("open_position", {"side": "short"}, "long")
    assert (e.kind, e.label) == ("flip", "反手→空")
    # close_position：无 side，方向取 prev
    e = _classify_action("close_position", {}, "long")
    assert (e.kind, e.label, e.direction) == ("close", "平多", "long")
    assert _classify_action("close_position", {}, "short").label == "平空"
    assert _classify_action("close_position", {}, None).label == "平仓"   # prev 缺失兜底
    # place_limit_order
    e = _classify_action("place_limit_order", {"side": "long"}, None)
    assert (e.kind, e.label, e.direction) == ("limit_order", "挂限价单·多", "long")
    # 非交易工具 → None
    assert _classify_action("get_market_data", {}, None) is None
    # args 非 dict（截断回退 str）→ 不抛、side 缺失走 '?'
    assert _classify_action("open_position", "broken", None).kind == "open"


def test_derive_position_and_normalize():
    from src.webui.queries import _derive_position, _normalize_to_list, _safe
    # 有持仓
    snap = {"position": {"side": "short", "contracts": 17.99, "entry_price": 63896.0}}
    p = _derive_position(snap)
    assert (p.side, p.contracts, p.entry_price) == ("short", 17.99, 63896.0)
    # flat：position=None / contracts=0 → None
    assert _derive_position({"position": None}) is None
    assert _derive_position({"position": {"side": "long", "contracts": 0}}) is None
    # 异常形态（snapshot 是 list/str/None）→ None，不抛
    assert _derive_position(["x"]) is None
    assert _derive_position("broken") is None
    assert _derive_position(None) is None
    # 形态归一：list[dict] 直用 / dict 包单元素 / 其他 → []
    assert _normalize_to_list([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert _normalize_to_list({"a": 1}) == [{"a": 1}]
    assert _normalize_to_list("broken") == []
    assert _normalize_to_list(None) == []
    assert _normalize_to_list([1, {"a": 1}]) == [{"a": 1}]   # 非 dict 元素剔除
    # _safe：异常 → None
    assert _safe(lambda: 1 / 0) is None
    assert _safe(lambda: 42) == 42


async def _add_tool(engine, cycle_id, tool_name, args, sid="s1"):
    async with get_session(engine) as s:
        s.add(ToolCall(session_id=sid, cycle_id=cycle_id, tool_name=tool_name,
                       status="ok", duration_ms=10,
                       args=json.dumps(args) if args is not None else None))
        await s.commit()


@pytest.mark.asyncio
async def test_get_cycles_key_events_active_actions(engine):
    await _seed_session(engine)
    # 开多（前 flat）
    await _add_cycle(engine, cycle_id="open1", snapshot='{"position":null}')
    await _add_tool(engine, "open1", "open_position", {"side": "long"})
    # 加仓（前同向 long）
    await _add_cycle(engine, cycle_id="add1",
                     snapshot='{"position":{"side":"long","contracts":2.0,"entry_price":63000.0}}')
    await _add_tool(engine, "add1", "open_position", {"side": "long"})
    # 反手（前反向 long → 开 short）
    await _add_cycle(engine, cycle_id="flip1",
                     snapshot='{"position":{"side":"long","contracts":2.0,"entry_price":63000.0}}')
    await _add_tool(engine, "flip1", "open_position", {"side": "short"})
    # 主动平仓 + 挂限价单（同轮两动作，按执行序）
    await _add_cycle(engine, cycle_id="mix1",
                     snapshot='{"position":{"side":"long","contracts":2.0,"entry_price":63000.0}}')
    await _add_tool(engine, "mix1", "close_position", None)
    await _add_tool(engine, "mix1", "place_limit_order", {"side": "short"})
    from src.webui.queries import get_cycles
    by = {r.cycle_label: r for r in await get_cycles(engine, "s1")}
    assert [e.kind for e in by["open1"].key_events] == ["open"]
    assert by["open1"].position is None
    assert [e.kind for e in by["add1"].key_events] == ["add"]
    assert by["add1"].position.side == "long" and by["add1"].position.contracts == 2.0
    assert [e.kind for e in by["flip1"].key_events] == ["flip"]
    assert [e.kind for e in by["mix1"].key_events] == ["close", "limit_order"]


@pytest.mark.asyncio
async def test_get_cycles_key_events_passive_fills_and_dedup(engine):
    await _seed_session(engine)
    # 止损平仓 fill
    await _add_cycle(engine, cycle_id="stop1", trigger_context=[_fill("stop", pnl=-50.0, full=True)])
    # market 回声 → 去重（不计入）
    await _add_cycle(engine, cycle_id="mkt1", trigger_context=[_fill("market", pnl=20.0, full=True)])
    # 同轮双事件：止损全平 fill（被动）→ snapshot 已空仓（fill 撮合早于 snapshot，cli/app.py:508→514，
    # 已用 cycle 1147 DB 实证）→ 主动反向 open 从空仓新开 = open（非 flip）
    await _add_cycle(engine, cycle_id="dual1",
                     trigger_context=[_fill("stop", pnl=-30.0, full=True, side="long")],
                     snapshot='{"position":null}')
    await _add_tool(engine, "dual1", "open_position", {"side": "short"})
    from src.webui.queries import get_cycles
    by = {r.cycle_label: r for r in await get_cycles(engine, "s1")}
    assert [e.kind for e in by["stop1"].key_events] == ["fill_close"]
    assert by["stop1"].key_events[0].label == "止损平仓"
    assert by["mkt1"].key_events == []                          # market 回声去重
    # 同轮：被动 fill 在前、主动动作在后；全平后 snapshot 空仓 → prev_side=None → open（非 flip）
    assert [e.kind for e in by["dual1"].key_events] == ["fill_close", "open"]
    assert by["dual1"].key_events[1].label == "开空"


@pytest.mark.asyncio
async def test_get_cycles_seq_is_session_absolute_and_stable(engine):
    """seq = 会话内 1-based 绝对序号（按 id 升序）；desc 列表与 before/after 翻页都不重启。"""
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    ids = [await _add_cycle(engine, cycle_id=f"sq{i}", created_at=base + timedelta(minutes=i))
           for i in range(5)]                       # seq 应为 1..5
    from src.webui.queries import get_cycles
    rows = await get_cycles(engine, "s1")            # 全量 desc：最新在前，seq 递减
    assert [(r.id, r.seq) for r in rows] == [(ids[4], 5), (ids[3], 4), (ids[2], 3), (ids[1], 2), (ids[0], 1)]
    older = await get_cycles(engine, "s1", limit=2, before_id=ids[3])   # 更旧方向
    assert [(r.id, r.seq) for r in older] == [(ids[2], 3), (ids[1], 2)]
    newer = await get_cycles(engine, "s1", limit=2, after_id=ids[0])    # 更新方向（紧邻游标那批）
    assert [(r.id, r.seq) for r in newer] == [(ids[2], 3), (ids[1], 2)]


@pytest.mark.asyncio
async def test_get_cycles_derivation_fail_isolated(engine):
    """派生真异常被 _safe 兜住、不阻断 feed（区别于"被类型守卫提前挡成 None"——那不验证 _safe）：
    - position.contracts 非数 → PositionBrief(contracts: float) 构造 ValidationError；
    - fill.position_side 为 dict → KeyEvent(direction: str|None) 构造 ValidationError。
    两路异常各被 _safe 吞为 None。"""
    await _seed_session(engine)
    await _add_cycle(
        engine, cycle_id="bad1",
        trigger_context=[{"type": "fill", "trigger_reason": "stop",
                          "position_side": {"bad": 1}, "pnl": -1.0, "is_full_close": True}],
        snapshot='{"position":{"side":"long","contracts":"not_a_number"}}')
    from src.webui.queries import get_cycles
    rows = await get_cycles(engine, "s1")            # 不抛（feed 不阻断）
    r = next(x for x in rows if x.cycle_label == "bad1")
    assert r.position is None and r.key_events == []  # position 与 fill 两路异常各被 _safe 兜住


@pytest.mark.asyncio
async def test_get_cycle_detail_seq_matches_session_position(engine):
    """get_cycle_detail.seq = 会话内 1-based 位置（与 get_cycles 同口径）。"""
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    ids = [await _add_cycle(engine, cycle_id=f"ds{i}", created_at=base + timedelta(minutes=i))
           for i in range(3)]
    from src.webui.queries import get_cycle_detail
    assert (await get_cycle_detail(engine, ids[0])).seq == 1
    assert (await get_cycle_detail(engine, ids[2])).seq == 3


def _ms(dt):
    return int(dt.timestamp() * 1000)


@pytest.mark.asyncio
async def test_get_cycle_detail_enriches_injected_events(engine):
    """注入事件富化：triggered_ago（英文 ladder）+ kind_label（复用 _classify_fill）。
    必经真实 SQLite 往返——created_at 读回为 naive，验证 P0 tz 补全；
    手构造 aware fixture 会让 tz-naive→500 假绿。"""
    await _seed_session(engine)
    created = datetime(2026, 6, 12, 10, 5, 0, tzinfo=UTC)        # cycle 结束时刻
    wall = 60_000                                               # 1min → 开始 = 10:04:00
    base_start = created - timedelta(milliseconds=wall)
    # 注入于开始后 30s = 10:04:30；事件戳 = 注入时刻 − 90s → age "1 min ago"
    event_ts_ms = _ms(base_start + timedelta(seconds=30) - timedelta(seconds=90))
    injected = [{
        "event": {"type": "fill", "trigger_reason": "stop", "position_side": "long",
                  "pnl": -50.0, "is_full_close": True, "timestamp": event_ts_ms},
        "after_tool": "get_position", "after_tool_call_id": "call_2", "offset_ms": 30_000,
    }]
    pk = await _add_cycle(engine, cycle_id="inj1", created_at=created, wall=wall,
                          injected_events=injected)
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    rec = d.injected_events[0]
    assert rec["kind_label"] == "止损平仓"           # _classify_fill: stop + full close
    assert rec["triggered_ago"] == "1 min ago"
    assert rec["offset_ms"] == 30_000                # 原字段透传不变


@pytest.mark.asyncio
async def test_injected_events_triggered_ago_none_guards(engine):
    """event None / 缺 timestamp / 未来时点 → triggered_ago=None，且不抛。"""
    await _seed_session(engine)
    created = datetime(2026, 6, 12, 10, 5, 0, tzinfo=UTC)
    future_ms = _ms(created + timedelta(hours=1))
    injected = [
        {"event": None, "after_tool": "t", "after_tool_call_id": None, "offset_ms": 0},
        {"event": {"type": "fill"}, "after_tool": "t", "after_tool_call_id": None, "offset_ms": 0},
        {"event": {"type": "percentage_alert", "timestamp": future_ms},
         "after_tool": "t", "after_tool_call_id": None, "offset_ms": 0},
    ]
    pk = await _add_cycle(engine, cycle_id="inj2", created_at=created, wall=60_000,
                          injected_events=injected)
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert d.injected_events[0]["triggered_ago"] is None     # event None
    assert d.injected_events[0]["kind_label"] == "事件"       # event None → 泛标题（不抛）
    assert d.injected_events[1]["triggered_ago"] is None     # 缺 timestamp
    assert d.injected_events[2]["triggered_ago"] is None     # 未来时点
    assert d.injected_events[2]["kind_label"] == "波动告警触发"


@pytest.mark.asyncio
async def test_injected_event_enrichment_degrades_per_event_no_500(engine):
    """单条富化抛错 → 该条降级裸 event、其余正常、整体不 500（get_cycle_detail 无外层 try/except）。"""
    await _seed_session(engine)
    created = datetime(2026, 6, 12, 10, 5, 0, tzinfo=UTC)
    good_ts = _ms(created - timedelta(seconds=90))
    injected = [
        {"event": {"type": "fill", "trigger_reason": "take_profit", "position_side": "short",
                   "pnl": 80.0, "is_full_close": True, "timestamp": good_ts},
         "after_tool": "t", "after_tool_call_id": None, "offset_ms": 0},
        {"event": {"type": "percentage_alert", "timestamp": "not-a-number"},   # 富化中抛 TypeError
         "after_tool": "t", "after_tool_call_id": None, "offset_ms": 0},
    ]
    pk = await _add_cycle(engine, cycle_id="inj3", created_at=created, wall=0,
                          injected_events=injected)
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)            # 不抛
    assert d.injected_events[0]["kind_label"] == "止盈平仓"      # 正常富化
    assert "kind_label" not in d.injected_events[1]            # 降级为裸 event


@pytest.mark.asyncio
async def test_injected_events_non_list_passthrough(engine):
    """injected_events 非 list 形态（dict / str / None）原样返回，不富化。"""
    await _seed_session(engine)
    pk = await _add_cycle(engine, cycle_id="inj4")     # 无 injected_events → 列 NULL → None
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert d.injected_events is None


@pytest.mark.asyncio
async def test_injection_age_just_triggered_within_cycle_is_just_now(engine):
    """刚触发事件：event_ts 微落 injection_moment 估算之"未来"但仍在 cycle 窗口内
    （≤ created_at）→ 因果上必先于其触发的注入、是估算噪声 → "just now"（非 None）。
    复现 sim#21 实测：price_level_alert event_ts 比 (created_at−wall)+offset 晚 ~0.2s。"""
    await _seed_session(engine)
    created = datetime(2026, 6, 12, 10, 5, 0, tzinfo=UTC)       # cycle 结束
    wall = 60_000                                              # 开始 = 10:04:00
    base_start = created - timedelta(milliseconds=wall)
    # 注入于开始后 30s（injection_moment 估算=10:04:30）；事件戳 = 估算+0.3s（落"未来"但 ≤ 结束）
    event_ts_ms = _ms(base_start + timedelta(seconds=30, milliseconds=300))
    injected = [{
        "event": {"type": "price_level_alert", "direction": "above", "target_price": 65600.0,
                  "current_price": 65694.2, "timestamp": event_ts_ms},
        "after_tool": "add_price_level_alert", "after_tool_call_id": "call_9", "offset_ms": 30_000,
    }]
    pk = await _add_cycle(engine, cycle_id="agenow", created_at=created, wall=wall,
                          injected_events=injected)
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert d.injected_events[0]["triggered_ago"] == "just now"   # 修前是 None（无 age 片）


@pytest.mark.asyncio
async def test_injection_age_event_after_cycle_end_stays_none(engine):
    """event_ts 真晚于 cycle 结束（created_at）→ 异常/损坏戳，保留 None（仅显 UTC），不误标 just now。"""
    await _seed_session(engine)
    created = datetime(2026, 6, 12, 10, 5, 0, tzinfo=UTC)
    after_end_ms = _ms(created + timedelta(seconds=10))         # 晚于 cycle 结束
    injected = [{
        "event": {"type": "price_level_alert", "direction": "above", "target_price": 1.0,
                  "current_price": 2.0, "timestamp": after_end_ms},
        "after_tool": "t", "after_tool_call_id": None, "offset_ms": 0,
    }]
    pk = await _add_cycle(engine, cycle_id="ageanom", created_at=created, wall=60_000,
                          injected_events=injected)
    from src.webui.queries import get_cycle_detail
    d = await get_cycle_detail(engine, pk)
    assert d.injected_events[0]["triggered_ago"] is None


@pytest.mark.asyncio
async def test_get_performance_exposes_trigger_reason_and_total_pnl(engine):
    """get_performance 暴露 close fill 的 trigger_reason + total_pnl（毛额）。
    注：symbol 必填（TradeAction.symbol NOT NULL，models.py:78）；total_pnl 由 MetricsService
    按价格重算 gross =(close.price − open.entry_px)*amount*sign*contract_size（metrics.py:192-196），
    非记录的 pnl 字段——故断言用重算值。"""
    from src.storage.models import TradeAction
    await _seed_session(engine)
    async with get_session(engine) as s:
        s.add(TradeAction(session_id="s1", action="order_filled", symbol="BTC/USDT:USDT",
                          side="long", price=65000.0, amount=0.1, fee=3.0, pnl=None,
                          entry_price=None, trigger_reason="market"))
        s.add(TradeAction(session_id="s1", action="order_filled", symbol="BTC/USDT:USDT",
                          side="long", price=64900.0, amount=0.1, fee=3.0, pnl=-10.0,
                          entry_price=65000.0, trigger_reason="stop"))
        await s.commit()
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")
    assert perf.trades[1].trigger_reason == "stop"          # id ASC：开仓在前、平仓在后
    # gross =(64900−65000)*0.1*sign(long=+1)*cs(1.0, session 无 contract_size→fallback) = -10.0
    assert round(perf.total_pnl, 2) == -10.0


@pytest.mark.asyncio
async def test_open_position_sim_authoritative_with_snapshot_unrealized(engine):
    """(a) SimPosition 有仓 + snapshot 同向 → side/数量/入场价取 SimPosition、未实现借 snapshot。"""
    from src.storage.models import SimPosition
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="short",
                          contracts=10.82, entry_price=65542.1, leverage=10))
        await s.commit()
    await _add_cycle(engine, cycle_id="cz", created_at=base,
                     snapshot=json.dumps({"balance": {"total_usdt": 9440.89},
                       "position": {"side": "short", "contracts": 10.82, "entry_price": 65542.1,
                                    "unrealized_pnl": -13.97, "pnl_pct_of_notional": -0.2}}))
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")
    assert perf.open_position is not None
    assert perf.open_position.side == "short"
    assert round(perf.open_position.contracts, 2) == 10.82
    assert round(perf.open_position.unrealized_pnl, 2) == -13.97
    assert round(perf.open_position.pnl_pct_of_notional, 2) == -0.2


@pytest.mark.asyncio
async def test_open_position_sim_authoritative_snapshot_mismatch_unrealized_none(engine):
    """(b) SimPosition 有仓 + snapshot flat/异向（漏显反例）→ side/数量/入场价仍取 SimPosition、未实现 None。"""
    from src.storage.models import SimPosition
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="short",
                          contracts=0.265, entry_price=65000.0, leverage=10))
        await s.commit()
    await _add_cycle(engine, cycle_id="cf", created_at=base,
                     snapshot=json.dumps({"balance": {"total_usdt": 9900.0}}))   # snapshot 无 position
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")
    assert perf.open_position is not None
    assert perf.open_position.side == "short"
    assert round(perf.open_position.contracts, 3) == 0.265
    assert perf.open_position.unrealized_pnl is None
    assert perf.open_position.pnl_pct_of_notional is None


@pytest.mark.asyncio
async def test_open_position_none_when_sim_flat_despite_snapshot(engine):
    """(c) SimPosition 平/空 + snapshot 有仓（幻影反例）→ open_position is None（权威）。"""
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _add_cycle(engine, cycle_id="cp", created_at=base,
                     snapshot=json.dumps({"balance": {"total_usdt": 10000.0},
                       "position": {"side": "long", "contracts": 0.121, "entry_price": 60000.0,
                                    "unrealized_pnl": -8.16, "pnl_pct_of_notional": -0.1}}))
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")     # 无 SimPosition
    assert perf.open_position is None


def test_close_label_drift_guard_ts_matches_classify_fill():
    """CLOSE_LABEL（trades.ts）平仓词汇必须与 _classify_fill 逐字同源（spec §C drift-guard）。
    _classify_fill 是单一权威来源：改它的标签 → 本测试读 TS 源校验同字面，强制 TS 同步。"""
    from pathlib import Path
    from src.webui.queries import _classify_fill
    expected = set()
    for reason in ["stop", "take_profit", "liquidation", "limit", "other"]:
        ev = _classify_fill({"trigger_reason": reason, "position_side": "long",
                             "pnl": 1.0, "is_full_close": True})
        expected.add(ev.label)        # other → 泛标签「平仓」
    repo_root = Path(__file__).resolve().parents[1]   # tests/ 的上一级 = 仓库根（不依赖 pytest CWD）
    ts = (repo_root / "frontend/src/utils/trades.ts").read_text(encoding="utf-8")
    # 用带引号字面校验（如 '"止损平仓"'），比裸 substring 更严：避免「平仓」是「止损平仓」子串的松配。
    missing = [label for label in expected if f'"{label}"' not in ts]
    assert not missing, f"CLOSE_LABEL drift: {missing} 不在 trades.ts（与 _classify_fill 漂移）"
