# WebUI 观察台打磨 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 spec `2026-06-15-webui-observation-polish-design.md` 的 11 项 UI 打磨 + 全局 UTC + 详情区结构重排，只动观测可读性/语义准确性，不碰 agent 决策路径。

**Architecture:** 后端先行——`schemas.py`/`queries.py` 派生 `seq` 与注入事件富化（`triggered_ago` + `kind_label`，逐条 try/except 降级、`injection_moment` 补 aware UTC 防 P0 500），`cycle_capture.py` 多采波动告警单例；重生成 OpenAPI + 前端类型后，前端 `time.ts` UTC 格式器替换 4 处 `fmtLocal`，再逐组件落 header（序号/区间/去重）、状态面板（三标签格/UTC/波动告警/重命名/重排/默认折叠）、思考块（整块折叠/💭）、注入卡（人读摘要/age/原始 JSON 折叠，抽 `InjectionCard.vue`）。

**Tech Stack:** 后端 FastAPI + SQLAlchemy（async）+ pydantic v2 + pytest；前端 Vue 3 SPA + naive-ui 2.38.1（pin，勿 npm update）+ TypeScript + vitest；类型由后端 OpenAPI 经 `openapi-typescript` 生成。

**分支：** `iter-webui-observation-polish`（spec 已 commit `d43eac5`）。每个 task 自成 commit，后端测试用 pytest、前端用 `npm test`（vitest，无 typecheck），最终 task 跑 `vue-tsc` build + Playwright 走查。

---

## File Structure

**后端（先行，每 task 独立保持全绿）**
- `src/webui/schemas.py` — `CycleRow` / `CycleDetail` 各加 `seq: int`。
- `src/webui/queries.py` — `get_cycles` 窗口函数派生 `seq`；`get_cycle_detail` 标量 COUNT 派生 `seq` + 注入事件富化（新 helper `_enrich_injected_events` / `_injection_age` / `_injection_kind_label`，复用 `_classify_fill` 与 `event_render._format_event_age`）。
- `src/services/cycle_capture.py` — `_capture_state_snapshot` 加第 6 段采集波动告警单例 + 默认键。
- `tests/test_webui_queries.py` / `tests/test_cycle_capture.py` — 对应测试（注入富化测试必经真实 SQLite 往返）。
- `frontend/openapi.json` + `frontend/src/api/types.ts` — `seq` 进 schema 后重生成。

**前端（后端类型就绪后）**
- `frontend/src/utils/time.ts` — 加 `fmtUtc` / `fmtUtcTime` / `fmtUtcEpoch`，退役 `fmtLocal`。
- `frontend/src/components/CycleRowHeader.vue` — 序号 `#N` + 起止区间 + token/耗时只此一处。
- `frontend/src/components/LiveStatusCard.vue` / `TradesTable.vue` — `fmtLocal` → `fmtUtc`。
- `frontend/src/components/CycleDetailPanel.vue` — chips 去重、重命名「唤醒时状态」、重排、唤醒上下文默认折叠、余额三标签格、现价 UTC、波动告警两类展示。
- `frontend/src/components/ReactTimeline.vue` — 思考块整块折叠 + 💭；注入卡改用新组件。
- `frontend/src/components/InjectionCard.vue` —（新建）注入事件人读摘要卡，anchored / orphan 共用。
- `frontend/src/views/DashboardView.vue` — 会话头一次性标注「时间均为 UTC」。
- 对应 `frontend/test/*.spec.ts`。

---

## Task 1: 后端 `seq` —— `CycleRow` + `get_cycles`（窗口函数）

**Files:**
- Modify: `src/webui/schemas.py:59-68`（`CycleRow` 加 `seq`）
- Modify: `src/webui/queries.py:19-58`（`get_cycles` 窗口函数派生 `seq`）
- Test: `tests/test_webui_queries.py`

- [ ] **Step 1: 写失败测试（seq 会话内绝对、翻页不重启）**

追加到 `tests/test_webui_queries.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_webui_queries.py::test_get_cycles_seq_is_session_absolute_and_stable -q`
Expected: FAIL —— `CycleRow` 无 `seq` 字段（pydantic ValidationError）或 AttributeError。

- [ ] **Step 3: `schemas.py` 给 `CycleRow` 加 `seq`**

把 `src/webui/schemas.py:59-61`：

```python
class CycleRow(BaseModel):
    id: int                   # int PK — 详情跳转/游标用这个
    cycle_label: str          # agent_cycles.cycle_id 字符串，仅显示
```

改为：

```python
class CycleRow(BaseModel):
    id: int                   # int PK — 详情跳转/游标用这个
    seq: int                  # 会话内 1-based 绝对序号（窗口函数派生，翻页不重启）
    cycle_label: str          # agent_cycles.cycle_id 字符串，仅显示
```

- [ ] **Step 4: `queries.py` 加 `aliased` 导入**

把 `src/webui/queries.py:8`：

```python
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncEngine
```

改为：

```python
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import aliased
```

- [ ] **Step 5: 重写 `get_cycles`（窗口函数 seq 在游标过滤之前）**

把 `src/webui/queries.py:19-58` 整个 `get_cycles` 函数替换为：

```python
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
```

- [ ] **Step 6: 跑新测试 + 既有 get_cycles 测试，确认全绿**

Run: `python -m pytest tests/test_webui_queries.py -q`
Expected: PASS（新 seq 测试 + 既有 `test_get_cycles_orders_desc_and_paginates` / `test_get_cycles_after_id_no_gap_when_new_exceeds_limit` / `test_get_cycles_key_events_*` 不回归）。

- [ ] **Step 7: Commit**

```bash
git add src/webui/schemas.py src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): cycle 会话内绝对序号 seq（CycleRow + get_cycles 窗口函数）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 后端 `seq` —— `CycleDetail` + `get_cycle_detail`（标量 COUNT）

**Files:**
- Modify: `src/webui/schemas.py:81-83`（`CycleDetail` 加 `seq`）
- Modify: `src/webui/queries.py:162-190`（`get_cycle_detail` 计 `seq`）
- Test: `tests/test_webui_queries.py`

- [ ] **Step 1: 写失败测试（detail.seq = 会话内位置）**

追加到 `tests/test_webui_queries.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_webui_queries.py::test_get_cycle_detail_seq_matches_session_position -q`
Expected: FAIL —— `CycleDetail` 无 `seq`。

- [ ] **Step 3: `schemas.py` 给 `CycleDetail` 加 `seq`**

把 `src/webui/schemas.py:81-83`：

```python
class CycleDetail(BaseModel):
    id: int
    cycle_label: str
```

改为：

```python
class CycleDetail(BaseModel):
    id: int
    seq: int                  # 会话内 1-based 绝对序号（与 CycleRow.seq 同口径）
    cycle_label: str
```

- [ ] **Step 4: `get_cycle_detail` 计算并下发 `seq`**

把 `src/webui/queries.py:162-174`（函数开头到 `return schemas.CycleDetail(` 之前）：

```python
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
```

改为（在 `async with` 内加标量 COUNT，构造里加 `seq=seq`）：

```python
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
```

- [ ] **Step 5: 跑测试 + 既有 detail 测试，确认全绿**

Run: `python -m pytest tests/test_webui_queries.py -q`
Expected: PASS（新 seq 测试 + 既有 `test_get_cycle_detail_*` / `test_get_cycle_detail_returns_react_fields` 不回归）。

- [ ] **Step 6: Commit**

```bash
git add src/webui/schemas.py src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): CycleDetail 加 seq（get_cycle_detail 标量 COUNT 派生）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 后端注入事件富化 —— `triggered_ago` + `kind_label`（P0 tz + 逐条降级）

**Files:**
- Modify: `src/webui/queries.py`（新增 3 个 helper + `get_cycle_detail` 改 `injected_events`）
- Modify: `tests/test_webui_queries.py`（`_add_cycle` 加 `injected_events` 参数 + 4 个富化测试）

- [ ] **Step 1: `queries.py` 加 datetime + `_format_event_age` 导入**

把 `src/webui/queries.py:6`：

```python
import json
```

改为：

```python
import json
from datetime import datetime, timezone, timedelta
```

并在 `src/webui/queries.py:16`（`from src.webui import schemas` 之后）追加：

```python
from src.services.event_render import _format_event_age
```

- [ ] **Step 2: 写失败测试 —— 先给 `_add_cycle` 加 `injected_events` 参数**

把 `tests/test_webui_queries.py:20-35` 的 `_add_cycle` 整体替换为（新增 `injected_events=None` 形参 + 落库）：

```python
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
```

- [ ] **Step 3: 写失败测试 —— 4 个富化场景（经真实 SQLite 往返）**

追加到 `tests/test_webui_queries.py`：

```python
def _ms(dt):
    return int(dt.timestamp() * 1000)


@pytest.mark.asyncio
async def test_get_cycle_detail_enriches_injected_events(engine):
    """注入事件富化：triggered_ago（英文 ladder）+ kind_label（复用 _classify_fill）。
    必经真实 SQLite 往返——created_at 读回为 naive，验证 P0 tz 补全；
    手构造 aware fixture 会让 tz-naive→500 假绿（cf. feedback_views_need_rebuild_migration）。"""
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
```

- [ ] **Step 4: 跑测试确认失败**

Run: `python -m pytest tests/test_webui_queries.py -k injected -q`
Expected: FAIL —— `injected_events` 尚未富化（无 `kind_label` / `triggered_ago` 键）。

- [ ] **Step 5: `queries.py` 新增 3 个富化 helper**

在 `src/webui/queries.py` 的 `_classify_fill`（结束于 `:105`）之后插入：

```python
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


def _injection_age(base_aware: datetime, rec: dict) -> str | None:
    """注入事件 age（英文 ladder，复用 event_render._format_event_age）。
    base_aware = aware UTC 的 cycle 开始时刻；injection_moment = base + offset_ms。
    event None / 缺 timestamp → None；未来时点（skew）→ None（_format_event_age 既有语义）。"""
    event = rec.get("event")
    if not isinstance(event, dict) or event.get("timestamp") is None:
        return None
    injection_moment = base_aware + timedelta(milliseconds=rec.get("offset_ms") or 0)
    event_ts = datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)   # aware
    return _format_event_age(injection_moment, event_ts)


def _enrich_injected_events(raw, created_at, wall_time_ms):
    """富化 injected_events 供 WebUI 注入卡：每条加 triggered_ago + kind_label。
    仅处理 list[dict-with-'event'] 形态；其他形态（dict/str/None）原样返回。

    逐条 try/except 降级裸 event —— get_cycle_detail 无外层 try/except，任一事件富化
    异常绝不冒泡成 500（spec §6 / P0）。

    ⚠ P0：c.created_at 经 ORM 从 SQLite 读回是 naive（_ensure_utc docstring 所述）；
    不补 aware UTC 则 _format_event_age 首行 `then > now`（aware event_ts vs naive now）
    抛 TypeError → 500，且只在有注入事件的 cycle 触发（已实证复现）。补 tz 与 _ensure_utc 同模式。
    created_at 是 cycle 结束时刻；开始 ≈ created_at − wall_time_ms（数据核实）。"""
    if not isinstance(raw, list):
        return raw
    base = created_at if wall_time_ms is None else created_at - timedelta(milliseconds=wall_time_ms)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    out = []
    for rec in raw:
        if not isinstance(rec, dict) or "event" not in rec:
            out.append(rec)
            continue
        try:
            enriched = dict(rec)
            enriched["triggered_ago"] = _injection_age(base, rec)
            enriched["kind_label"] = _injection_kind_label(rec.get("event"))
            out.append(enriched)
        except Exception:
            out.append(rec)        # 降级裸 event（不附富化字段）
    return out
```

- [ ] **Step 6: `get_cycle_detail` 改 `injected_events` 走富化**

把 `src/webui/queries.py:178`：

```python
        injected_events=_loads(c.injected_events),
```

改为：

```python
        injected_events=_enrich_injected_events(_loads(c.injected_events), c.created_at, c.wall_time_ms),
```

- [ ] **Step 7: 跑富化测试 + 全 webui 测试，确认全绿**

Run: `python -m pytest tests/test_webui_queries.py -q`
Expected: PASS（4 个富化测试 + 既有测试不回归）。

- [ ] **Step 8: Commit**

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): 注入事件富化 triggered_ago + kind_label（P0 tz 补全 + 逐条降级）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 后端 B3 —— 快照采集波动告警单例

**Files:**
- Modify: `src/services/cycle_capture.py:115-123`（默认键）+ `:215` 后（第 6 段采集）
- Modify: `tests/test_cycle_capture.py`（fixture 加 `get_alert_params` + 3 个新测试 + 2 个既有测试更新）

- [ ] **Step 1: 写失败测试（3 个新场景）**

追加到 `tests/test_cycle_capture.py`（`# === T-SS:` 区块内、`test_state_snapshot_always_returns_dict_never_none` 之后）：

```python
async def test_state_snapshot_captures_volatility_alert(deps_with_position):
    """T-SS-12 (B3): 设了波动告警 → snapshot.volatility_alert = {threshold_pct, window_minutes}。"""
    deps_with_position.exchange.get_alert_params = MagicMock(return_value=(1.5, 15))
    snap = await _capture_state_snapshot("c-vol-1", deps_with_position)
    assert snap["volatility_alert"] == {"threshold_pct": 1.5, "window_minutes": 15}


async def test_state_snapshot_volatility_alert_none_when_disabled(deps_with_position):
    """T-SS-13 (B3): 未设波动告警（get_alert_params 返 None）→ volatility_alert = None。"""
    deps_with_position.exchange.get_alert_params = MagicMock(return_value=None)
    snap = await _capture_state_snapshot("c-vol-2", deps_with_position)
    assert snap["volatility_alert"] is None


async def test_state_snapshot_volatility_alert_error_isolated(deps_with_position):
    """T-SS-14 (B3): getter 抛异常 → volatility_alert 留 None + _errors 标记、不抛。"""
    deps_with_position.exchange.get_alert_params = MagicMock(side_effect=RuntimeError("boom"))
    snap = await _capture_state_snapshot("c-vol-3", deps_with_position)
    assert snap["volatility_alert"] is None
    assert any("volatility_alert_read_failed" in e for e in snap["_errors"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_cycle_capture.py -k volatility -q`
Expected: FAIL —— `snapshot` 无 `volatility_alert` 键（KeyError）。

- [ ] **Step 3: `cycle_capture.py` 加默认键**

把 `src/services/cycle_capture.py:115-123` 的 snapshot 初始化：

```python
    snapshot: dict = {
        "position": None,
        "balance": None,
        "market": None,
        "pending_orders": [],
        "active_alerts": [],
        "_errors": [],
        "_cycle_id": cycle_id,
    }
```

改为（加 `"volatility_alert": None`）：

```python
    snapshot: dict = {
        "position": None,
        "balance": None,
        "market": None,
        "pending_orders": [],
        "active_alerts": [],
        "volatility_alert": None,
        "_errors": [],
        "_cycle_id": cycle_id,
    }
```

- [ ] **Step 4: `cycle_capture.py` 加第 6 段采集**

在 `src/services/cycle_capture.py:215`（第 5 段 active alerts 的 except 块结尾、`return snapshot` 之前）插入：

```python

    # 6. volatility alert (singleton) — get_alert_params 返回 (threshold_pct, window_minutes)|None
    try:
        vol = deps.exchange.get_alert_params()
        snapshot["volatility_alert"] = (
            {"threshold_pct": vol[0], "window_minutes": vol[1]} if vol else None
        )
    except Exception as e:
        msg = f"volatility_alert_read_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)
```

- [ ] **Step 5: 更新既有 fixture，防 MagicMock 泄漏进 volatility_alert**

`deps.exchange` 是裸 MagicMock 时，`get_alert_params()` 返回 truthy 子 mock → `volatility_alert` 变成 `{"threshold_pct": <mock>, ...}` → `json.dumps` 抛 TypeError。给两个 fixture 显式设 None（真实默认：未配置波动告警）。

把 `tests/test_cycle_capture.py:49`：

```python
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
```

改为（`deps_with_position` 内）：

```python
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.exchange.get_alert_params = MagicMock(return_value=None)
```

把 `tests/test_cycle_capture.py:71`：

```python
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
```

改为（`deps_flat` 内）：

```python
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.exchange.get_alert_params = MagicMock(return_value=None)
```

- [ ] **Step 6: 更新 `test_state_snapshot_always_returns_dict_never_none`（json.dumps 路径）**

该测试用裸 inline mock 且 `json.dumps(snap)`；须让 `get_alert_params` 也抛（与"全失败"主题一致），否则返回的子 mock 进 volatility_alert 破坏 dumps。

把 `tests/test_cycle_capture.py:239`：

```python
    deps.exchange.get_price_level_alerts = MagicMock(side_effect=Exception("xxx"))
```

改为：

```python
    deps.exchange.get_price_level_alerts = MagicMock(side_effect=Exception("xxx"))
    deps.exchange.get_alert_params = MagicMock(side_effect=Exception("xxx"))
```

- [ ] **Step 7: 更新 `test_state_snapshot_all_failed`（5 → 6 项，且全失真全失败）**

新增第 6 个 best-effort fetch，"全失败"应为 6 项错误。

把 `tests/test_cycle_capture.py:162-180` 的测试体：

```python
async def test_state_snapshot_all_failed():
    """T-SS-7: 全部 fetch 失败 → 所有字段 None + _errors 5 项 + 不抛异常。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(side_effect=RuntimeError())
    deps.exchange.fetch_balance = AsyncMock(side_effect=RuntimeError())
    deps.exchange.fetch_open_orders = AsyncMock(side_effect=RuntimeError())
    deps.exchange.get_price_level_alerts = MagicMock(side_effect=RuntimeError())
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(side_effect=RuntimeError())

    snap = await _capture_state_snapshot("cyc-007", deps)
    assert snap["position"] is None
    assert snap["balance"] is None
    assert snap["market"] is None
    assert snap["pending_orders"] == []
    assert snap["active_alerts"] == []
    assert len(snap["_errors"]) == 5
```

改为（加 `get_alert_params` 抛 + volatility_alert 断言 + 6 项）：

```python
async def test_state_snapshot_all_failed():
    """T-SS-7: 全部 6 个 best-effort fetch 失败 → 所有字段 None + _errors 6 项 + 不抛异常。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(side_effect=RuntimeError())
    deps.exchange.fetch_balance = AsyncMock(side_effect=RuntimeError())
    deps.exchange.fetch_open_orders = AsyncMock(side_effect=RuntimeError())
    deps.exchange.get_price_level_alerts = MagicMock(side_effect=RuntimeError())
    deps.exchange.get_alert_params = MagicMock(side_effect=RuntimeError())
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(side_effect=RuntimeError())

    snap = await _capture_state_snapshot("cyc-007", deps)
    assert snap["position"] is None
    assert snap["balance"] is None
    assert snap["market"] is None
    assert snap["pending_orders"] == []
    assert snap["active_alerts"] == []
    assert snap["volatility_alert"] is None
    assert len(snap["_errors"]) == 6
```

- [ ] **Step 8: 跑全 cycle_capture 测试，确认全绿**

Run: `python -m pytest tests/test_cycle_capture.py -q`
Expected: PASS（3 新测试 + 既有 11 项 T-SS 不回归，含 `json_round_trip` / `always_returns_dict`）。

- [ ] **Step 9: Commit**

```bash
git add src/services/cycle_capture.py tests/test_cycle_capture.py
git commit -m "feat(perception): 快照采集波动告警单例（B3，best-effort + 默认键）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 重生成 `openapi.json` + 前端类型

**Files:**
- Regenerate: `frontend/openapi.json`、`frontend/src/api/types.ts`

- [ ] **Step 1: 从仓库根 dump 后端 OpenAPI（紧凑单行，与现有格式一致）**

Run（仓库根）：

```bash
python -c "import json; from src.webui.app import create_app; print(json.dumps(create_app().openapi(), ensure_ascii=False))" > frontend/openapi.json
```

Expected: `frontend/openapi.json` 更新。

- [ ] **Step 2: 校验 `seq` 进了 schema**

Run: `grep -o '"seq"' frontend/openapi.json | head -2`
Expected: 至少 2 处（CycleRow + CycleDetail 各一）。

- [ ] **Step 3: 再生成 `types.ts`**

Run：

```bash
cd frontend && npm run gen:types
```

Expected: `frontend/src/api/types.ts` 更新（`CycleRow` / `CycleDetail` 含 `seq: number`）。

- [ ] **Step 4: 校验前端类型含 seq + 前端测试不回归**

Run: `grep -n "seq" frontend/src/api/types.ts | head` —— Expected: 出现 `seq?: ` 或 `seq: number`。
Run: `cd frontend && npm test` —— Expected: 全绿（现有断言不依赖 seq，casts 为 any）。

- [ ] **Step 5: Commit**

```bash
git add frontend/openapi.json frontend/src/api/types.ts
git commit -m "chore(webui): 重生成 openapi.json + 前端类型（CycleRow/CycleDetail.seq）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 前端 `time.ts` —— UTC 格式器（保留 `fmtLocal`）

**Files:**
- Modify: `frontend/src/utils/time.ts`
- Test: `frontend/test/time.spec.ts`

- [ ] **Step 1: 写失败测试（fmtUtc / fmtUtcTime / fmtUtcEpoch 对已知 UTC instant）**

把 `frontend/test/time.spec.ts:2`：

```typescript
import { parseUtc, epochSec, fmtLocal } from "@/utils/time";
```

改为：

```typescript
import { parseUtc, epochSec, fmtLocal, fmtUtc, fmtUtcTime, fmtUtcEpoch } from "@/utils/time";
```

并在 `describe("time utils", ...)` 内（`fmtLocal` 用例之后、`});` 之前）追加：

```typescript
  it("fmtUtc 输出 YYYY-MM-DD HH:MM:SS（UTC，不随本地时区漂移）", () => {
    expect(fmtUtc("2026-06-12T10:00:00Z")).toBe("2026-06-12 10:00:00");
  });

  it("fmtUtc 去微秒 + 去 +00:00", () => {
    expect(fmtUtc("2026-06-14T14:52:08.590628+00:00")).toBe("2026-06-14 14:52:08");
  });

  it("fmtUtc 对 null 返回占位", () => {
    expect(fmtUtc(null)).toBe("—");
  });

  it("fmtUtcTime 输出 HH:MM:SS（UTC）", () => {
    expect(fmtUtcTime("2026-06-12T10:00:00Z")).toBe("10:00:00");
  });

  it("fmtUtcEpoch 把 epoch-ms 按 UTC 渲成 HH:MM:SS", () => {
    // 1781258400000 = 2026-06-12T10:00:00Z
    expect(fmtUtcEpoch(1781258400000)).toBe("10:00:00");
    expect(fmtUtcEpoch(null)).toBe("—");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/time.spec.ts`
Expected: FAIL —— `fmtUtc` / `fmtUtcTime` / `fmtUtcEpoch` 未导出。

- [ ] **Step 3: 实现 UTC 格式器（保留 fmtLocal，Task 8 再退役）**

把 `frontend/src/utils/time.ts` 整个文件替换为：

```typescript
/** 后端出站时间戳均带 Z（UTC）。看板统一按 UTC 展示——与 DB / sim 分析口径一致，零时区心算。 */
export function parseUtc(iso: string): Date {
  return new Date(iso);
}

export function epochSec(iso: string): number {
  return Math.floor(parseUtc(iso).getTime() / 1000);
}

function pad2(n: number): string {
  return n < 10 ? "0" + n : String(n);
}

/** ISO → "YYYY-MM-DD HH:MM:SS"（UTC，去微秒/去 +00:00）。用 getUTC* 拼装，不经 toLocaleString（locale 会引入本地时区）。 */
export function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = parseUtc(iso);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ` +
    `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;
}

/** ISO → "HH:MM:SS"（UTC，给区间结束/紧凑场景）。 */
export function fmtUtcTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = parseUtc(iso);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;
}

/** epoch ms → "HH:MM:SS"（UTC，给注入事件 event.timestamp 这类 epoch-ms 源）。 */
export function fmtUtcEpoch(ms: number | null | undefined): string {
  if (ms == null) return "—";
  const d = new Date(ms);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;
}

export function fmtLocal(iso: string | null | undefined): string {
  if (!iso) return "—";
  return parseUtc(iso).toLocaleString();
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/time.spec.ts`
Expected: PASS（新 UTC 用例 + 既有 parseUtc/epochSec/fmtLocal 用例全绿）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/time.ts frontend/test/time.spec.ts
git commit -m "feat(webui): time.ts 加 UTC 格式器 fmtUtc/fmtUtcTime/fmtUtcEpoch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `CycleRowHeader` —— 序号 #N + 起止区间 + token/耗时只此一处（C1/C2/C3）

**Files:**
- Modify: `frontend/src/components/CycleRowHeader.vue`
- Test: `frontend/test/CycleRowHeader.spec.ts`

- [ ] **Step 1: 写失败测试（序号 + 区间 + null wall 退化）**

把 `frontend/test/CycleRowHeader.spec.ts:5-12` 的 `cycle()` fixture：

```typescript
function cycle(overrides = {}) {
  return {
    id: 1, cycle_label: "c1", triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z",
    tokens_consumed: 80733, wall_time_ms: 49770, execution_status: "ok",
    position: null, key_events: [],
    ...overrides,
  };
}
```

改为（加 `seq: 7`）：

```typescript
function cycle(overrides = {}) {
  return {
    id: 1, seq: 7, cycle_label: "c1", triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z",
    tokens_consumed: 80733, wall_time_ms: 49770, execution_status: "ok",
    position: null, key_events: [],
    ...overrides,
  };
}
```

并追加测试用例（`describe` 内）：

```typescript
  it("§C1 行首显示会话内序号 #N", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle() as any } });
    expect(w.text()).toContain("#7");
  });

  it("§C2 时间为起→止区间（created_at 是结束，开始 = created_at − wall），UTC", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle() as any } });
    const txt = w.text();
    // created_at=10:00:00Z, wall=49770ms → 开始 09:59:10（UTC）
    expect(txt).toContain("2026-06-12 09:59:10");
    expect(txt).toContain("→");
    expect(txt).toContain("10:00:00");           // 结束时分秒
  });

  it("§C2 wall_time_ms=null（forensic）→ 只渲结束单点、无 →", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle({ wall_time_ms: null }) as any } });
    const txt = w.text();
    expect(txt).toContain("2026-06-12 10:00:00");
    expect(txt).not.toContain("→");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/CycleRowHeader.spec.ts`
Expected: FAIL —— 无 `#7`、无区间渲染。

- [ ] **Step 3: 改 `CycleRowHeader.vue` script（import + startAt computed）**

把 `frontend/src/components/CycleRowHeader.vue:5`：

```typescript
import { fmtLocal } from "@/utils/time";
```

改为：

```typescript
import { fmtUtc, fmtUtcTime } from "@/utils/time";
```

并在 `headText` computed（结束于 `:16`）之后插入：

```typescript

// C2: created_at 是 cycle 结束时刻；开始 ≈ created_at − wall_time_ms（数据核实）。
// wall_time_ms=null（forensic）→ 无法推开始，startAt=null（模板只渲结束单点）。
const startAt = computed(() => {
  const w = props.cycle.wall_time_ms;
  if (w == null) return null;
  return new Date(new Date(props.cycle.created_at).getTime() - w).toISOString();
});
```

- [ ] **Step 4: 改 `CycleRowHeader.vue` 模板（序号片 + 区间）**

把 `frontend/src/components/CycleRowHeader.vue:30`：

```html
    <span class="time">{{ fmtLocal(cycle.created_at) }}</span>
```

改为：

```html
    <span class="seq">#{{ cycle.seq }}</span>
    <span class="time">
      <template v-if="startAt">{{ fmtUtc(startAt) }} → {{ fmtUtcTime(cycle.created_at) }}</template>
      <template v-else>{{ fmtUtc(cycle.created_at) }}</template>
    </span>
```

- [ ] **Step 5: 加 `.seq` 样式**

把 `frontend/src/components/CycleRowHeader.vue:52`：

```css
.time { opacity: 0.7; white-space: nowrap; }
```

改为：

```css
.seq { color: var(--ob-text-muted); background: var(--ob-block-bg); border-radius: 4px; padding: 0 5px; font-size: 11px; white-space: nowrap; }
.time { opacity: 0.7; white-space: nowrap; }
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/CycleRowHeader.spec.ts`
Expected: PASS（序号/区间/null-wall 退化 + 既有"遥测用 format util" `80,733 tok` / `49.8s` 不回归）。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/CycleRowHeader.vue frontend/test/CycleRowHeader.spec.ts
git commit -m "feat(webui): cycle header 序号 #N + 起止区间（C1/C2，UTC）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 迁移剩余 `fmtLocal` 调用点并退役 `fmtLocal`（LiveStatusCard + TradesTable）

**Files:**
- Modify: `frontend/src/components/LiveStatusCard.vue:5,16`
- Modify: `frontend/src/components/TradesTable.vue:6,11`
- Modify: `frontend/src/utils/time.ts`（删 `fmtLocal`）
- Test: `frontend/test/LiveStatusCard.spec.ts` / `TradesTable.spec.ts` / `time.spec.ts`

- [ ] **Step 1: 写失败测试（两组件 UTC 输出 + fmtLocal 已删）**

`frontend/test/LiveStatusCard.spec.ts` 的 `describe` 内追加：

```typescript
  it("§全局 last_active_at 按 UTC 展示", async () => {
    const { wrapper, store } = mountCard();
    store.live = { status: "active", last_active_at: "2026-06-12T10:00:00Z", position: null, open_orders: [], active_alerts: [] } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("2026-06-12 10:00:00");
  });
```

`frontend/test/TradesTable.spec.ts` 的 `describe` 内追加：

```typescript
  it("§全局 成交时间按 UTC 展示", () => {
    const w = mount(TradesTable, {
      props: { trades: [{ at: "2026-06-12T10:00:00Z", action: "open", side: "long", price: 63000, amount: 1, pnl: 50, fee: 1 }] },
    });
    expect(w.text()).toContain("2026-06-12 10:00:00");
  });
```

`frontend/test/time.spec.ts`：删除 `fmtLocal` 的两个用例（`:14-21` 的 `"fmtLocal 对 null 返回占位"` 与 `"fmtLocal 对有效串返回非空字符串"`），并把 import 行 `:2` 改回不含 fmtLocal：

```typescript
import { parseUtc, epochSec, fmtUtc, fmtUtcTime, fmtUtcEpoch } from "@/utils/time";
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/LiveStatusCard.spec.ts test/TradesTable.spec.ts`
Expected: FAIL —— 当前 `fmtLocal` 输出本地格式（非 `2026-06-12 10:00:00`）。

- [ ] **Step 3: `LiveStatusCard.vue` 切 fmtUtc**

把 `frontend/src/components/LiveStatusCard.vue:5`：

```typescript
import { fmtLocal } from "@/utils/time";
```

改为：

```typescript
import { fmtUtc } from "@/utils/time";
```

把 `:16`：

```html
      <span class="muted">@ {{ fmtLocal(live.last_active_at) }}</span>
```

改为：

```html
      <span class="muted">@ {{ fmtUtc(live.last_active_at) }}</span>
```

- [ ] **Step 4: `TradesTable.vue` 切 fmtUtc**

把 `frontend/src/components/TradesTable.vue:6`：

```typescript
import { fmtLocal } from "@/utils/time";
```

改为：

```typescript
import { fmtUtc } from "@/utils/time";
```

把 `:11`：

```typescript
  { title: "时间", key: "at", render: (r) => fmtLocal(r.at) },
```

改为：

```typescript
  { title: "时间", key: "at", render: (r) => fmtUtc(r.at) },
```

- [ ] **Step 5: 确认无残留 fmtLocal 调用点，再删 fmtLocal**

Run: `grep -rn "fmtLocal" frontend/src`
Expected: 仅 `frontend/src/utils/time.ts` 的定义行（无其他消费者）。

删 `frontend/src/utils/time.ts` 末尾的 `fmtLocal`：

```typescript

export function fmtLocal(iso: string | null | undefined): string {
  if (!iso) return "—";
  return parseUtc(iso).toLocaleString();
}
```

整段移除。

- [ ] **Step 6: 跑相关测试 + 全量前端测试，确认全绿**

Run: `cd frontend && npm test`
Expected: PASS —— 两组件 UTC 断言通过，`time.spec` 无 fmtLocal 引用，无 `fmtLocal` import 报错。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/LiveStatusCard.vue frontend/src/components/TradesTable.vue frontend/src/utils/time.ts frontend/test/LiveStatusCard.spec.ts frontend/test/TradesTable.spec.ts frontend/test/time.spec.ts
git commit -m "feat(webui): LiveStatusCard/TradesTable 时间切 UTC + 退役 fmtLocal

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: `CycleDetailPanel` 结构 —— chips 去重 + 重命名 + 重排 + 唤醒上下文默认折叠（C3/重排/A3）

**Files:**
- Modify: `frontend/src/components/CycleDetailPanel.vue`
- Test: `frontend/test/CycleDetailPanel.spec.ts`

- [ ] **Step 1: 写/改失败测试**

`frontend/test/CycleDetailPanel.spec.ts` 改动：

(a) 把 `:41-44` 的 `"渲染唤醒上下文原文"` 用例改为 A3 默认折叠语义：

```typescript
  it("§A3 唤醒上下文默认折叠，点击展开原文", async () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("唤醒上下文");                 // 标题在
    expect(w.text()).not.toContain("Woke by scheduled tick"); // 默认折叠：原文不渲染
    await w.find(".context-toggle").trigger("click");
    expect(w.text()).toContain("Woke by scheduled tick");      // 展开后可见
  });
```

(b) 把 `:114-118` 的 `"§议题5 chips token 千分位 + 耗时 s"` 用例替换为 C3 去重语义：

```typescript
  it("§C3 chips 去掉 tokens/wall 重复片（只留 header），保留拆解", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ tokens_consumed: 80733, wall_time_ms: 49770 }) as any } });
    const txt = w.text();
    expect(txt).not.toContain("tokens 80,733");   // 去掉总 tokens 片
    expect(txt).not.toContain("wall ");            // 去掉 wall 片
    expect(txt).toContain("输入");                 // 保留输入/输出拆解
    expect(txt).toContain("llm");                  // 保留 llm
  });
```

(c) 把 `:87-95` 的 `"§⑤⑥ 状态快照默认展开 + 置顶 + 格式化"` 用例改为新标题 + 新顺序：

```typescript
  it("§重排/重命名 唤醒时状态默认展开 + 置顶（先于唤醒上下文与时间线）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    const txt = w.text();
    expect(txt).toContain("唤醒时状态");
    expect(txt).not.toContain("本轮开始时的状态");
    expect(txt).toContain("17.99");          // 默认展开即可见（持仓 contracts）
    expect(txt).not.toContain("_cycle_id");
    expect(txt.indexOf("唤醒时状态")).toBeLessThan(txt.indexOf("唤醒上下文"));
    expect(txt.indexOf("唤醒时状态")).toBeLessThan(txt.indexOf("推理与行动过程"));
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts`
Expected: FAIL（A3 默认折叠未实现、chips 仍含 tokens/wall、标题仍是「本轮开始时的状态」、顺序未重排）。

- [ ] **Step 3: `contextOpen` 默认折叠（A3）**

把 `frontend/src/components/CycleDetailPanel.vue:17`：

```typescript
const contextOpen = ref(true);
```

改为：

```typescript
const contextOpen = ref(false);   // A3：唤醒上下文默认折叠，按需展开
```

- [ ] **Step 4: chips 删 tokens + wall 片（C3）**

把 `frontend/src/components/CycleDetailPanel.vue:54-62`：

```html
    <n-space class="chips" :size="6">
      <n-tag size="small">tokens {{ fmtTokens(detail.tokens_consumed) }}</n-tag>
      <n-tag v-if="detail.input_tokens != null" size="small">输入 {{ fmtTokens(detail.input_tokens) }} / 输出 {{ fmtTokens(detail.output_tokens) }} tok</n-tag>
      <n-tag v-if="detail.cache_hit_rate != null" size="small">cache {{ detail.cache_hit_rate.toFixed(0) }}%</n-tag>
      <n-tag v-if="detail.wall_time_ms != null" size="small">wall {{ fmtDuration(detail.wall_time_ms) }}</n-tag>
      <n-tag v-if="detail.llm_call_ms != null" size="small">llm {{ fmtDuration(detail.llm_call_ms) }}</n-tag>
      <n-tag size="small" :type="detail.execution_status === 'ok' ? 'default' : 'error'">{{ detail.execution_status }}</n-tag>
      <n-tag v-if="detail.model_id" size="small">{{ detail.model_id }}</n-tag>
    </n-space>
```

改为（去掉 tokens 总片 + wall 片；token/耗时只在 header）：

```html
    <n-space class="chips" :size="6">
      <n-tag v-if="detail.input_tokens != null" size="small">输入 {{ fmtTokens(detail.input_tokens) }} / 输出 {{ fmtTokens(detail.output_tokens) }} tok</n-tag>
      <n-tag v-if="detail.cache_hit_rate != null" size="small">cache {{ detail.cache_hit_rate.toFixed(0) }}%</n-tag>
      <n-tag v-if="detail.llm_call_ms != null" size="small">llm {{ fmtDuration(detail.llm_call_ms) }}</n-tag>
      <n-tag size="small" :type="detail.execution_status === 'ok' ? 'default' : 'error'">{{ detail.execution_status }}</n-tag>
      <n-tag v-if="detail.model_id" size="small">{{ detail.model_id }}</n-tag>
    </n-space>
```

- [ ] **Step 5: 重排 + 重命名 + context-toggle class**

把 `frontend/src/components/CycleDetailPanel.vue:64-102`（唤醒上下文 section §2 + 状态快照 section §3，按当前「上下文在前、快照在后」顺序）整段：

```html
    <!-- 2. 唤醒上下文（原文版，可折叠；null 不渲染） -->
    <section v-if="detail.user_prompt_snapshot" class="ob-card">
      <h4 class="clickable" @click="contextOpen = !contextOpen">唤醒上下文 {{ contextOpen ? "▾" : "▸" }}</h4>
      <pre v-if="contextOpen" class="context">{{ detail.user_prompt_snapshot }}</pre>
    </section>

    <!-- 3. 状态快照（默认展开，置顶；state_snapshot 是本轮开始态） -->
    <section v-if="snapshot" class="ob-card">
      <h4 class="snapshot-toggle clickable" @click="snapshotOpen = !snapshotOpen">
        本轮开始时的状态 {{ snapshotOpen ? "▾" : "▸" }}
      </h4>
      <div v-if="snapshotOpen" class="snapshot">
        <template v-if="snapshot.position">
          <span class="snap-k">持仓</span>
          <span>
            <span class="dir" :class="snapshot.position.side">{{ snapshot.position.side === 'long' ? '多' : '空' }}</span>
            {{ fmtNum(snapshot.position.contracts) }} 张 · 入场 {{ fmtNum(snapshot.position.entry_price) }} · 杠杆 {{ snapshot.position.leverage }}× · 浮盈
            <span :class="pnlClass(snapshot.position.unrealized_pnl)">{{ fmtSigned(snapshot.position.unrealized_pnl) }} USDT</span>
          </span>
        </template>
        <template v-else><span class="snap-k">持仓</span><span class="muted">空仓</span></template>
        <template v-if="snapshot.balance">
          <span class="snap-k">余额</span>
          <span>总 {{ fmtNum(snapshot.balance.total_usdt) }} · 可用 {{ fmtNum(snapshot.balance.free_usdt) }} · 占用 {{ fmtNum(snapshot.balance.used_usdt) }} USDT</span>
        </template>
        <template v-if="snapshot.market">
          <span class="snap-k">现价</span>
          <span>{{ fmtNum(snapshot.market.ticker_last) }} <span class="muted">@ {{ snapshot.market.fetched_at }}</span></span>
        </template>
        <template v-if="snapshot.pending_orders && snapshot.pending_orders.length">
          <span class="snap-k">挂单</span>
          <span><span v-for="(o, i) in snapshot.pending_orders" :key="i" class="snap-item">{{ o.order_type }} {{ o.side }} @{{ fmtNum(o.trigger_price ?? o.price) }} ×{{ o.amount }}</span></span>
        </template>
        <template v-if="snapshot.active_alerts && snapshot.active_alerts.length">
          <span class="snap-k">告警</span>
          <span><span v-for="(a, i) in snapshot.active_alerts" :key="i" class="snap-item">{{ a.direction }} @{{ fmtNum(a.price) }}<span v-if="a.reasoning" class="muted"> · {{ a.reasoning }}</span></span></span>
        </template>
      </div>
    </section>
```

替换为（**快照在前、上下文在后**；快照标题改「唤醒时状态」；上下文 h4 加 `context-toggle` class；快照内部结构本任务保持不变，B1/B2/B3 在 Task 10 改）：

```html
    <!-- 2. 唤醒时状态（默认展开，置顶；state_snapshot 是唤醒瞬间态） -->
    <section v-if="snapshot" class="ob-card">
      <h4 class="snapshot-toggle clickable" @click="snapshotOpen = !snapshotOpen">
        唤醒时状态 {{ snapshotOpen ? "▾" : "▸" }}
      </h4>
      <div v-if="snapshotOpen" class="snapshot">
        <template v-if="snapshot.position">
          <span class="snap-k">持仓</span>
          <span>
            <span class="dir" :class="snapshot.position.side">{{ snapshot.position.side === 'long' ? '多' : '空' }}</span>
            {{ fmtNum(snapshot.position.contracts) }} 张 · 入场 {{ fmtNum(snapshot.position.entry_price) }} · 杠杆 {{ snapshot.position.leverage }}× · 浮盈
            <span :class="pnlClass(snapshot.position.unrealized_pnl)">{{ fmtSigned(snapshot.position.unrealized_pnl) }} USDT</span>
          </span>
        </template>
        <template v-else><span class="snap-k">持仓</span><span class="muted">空仓</span></template>
        <template v-if="snapshot.balance">
          <span class="snap-k">余额</span>
          <span>总 {{ fmtNum(snapshot.balance.total_usdt) }} · 可用 {{ fmtNum(snapshot.balance.free_usdt) }} · 占用 {{ fmtNum(snapshot.balance.used_usdt) }} USDT</span>
        </template>
        <template v-if="snapshot.market">
          <span class="snap-k">现价</span>
          <span>{{ fmtNum(snapshot.market.ticker_last) }} <span class="muted">@ {{ snapshot.market.fetched_at }}</span></span>
        </template>
        <template v-if="snapshot.pending_orders && snapshot.pending_orders.length">
          <span class="snap-k">挂单</span>
          <span><span v-for="(o, i) in snapshot.pending_orders" :key="i" class="snap-item">{{ o.order_type }} {{ o.side }} @{{ fmtNum(o.trigger_price ?? o.price) }} ×{{ o.amount }}</span></span>
        </template>
        <template v-if="snapshot.active_alerts && snapshot.active_alerts.length">
          <span class="snap-k">告警</span>
          <span><span v-for="(a, i) in snapshot.active_alerts" :key="i" class="snap-item">{{ a.direction }} @{{ fmtNum(a.price) }}<span v-if="a.reasoning" class="muted"> · {{ a.reasoning }}</span></span></span>
        </template>
      </div>
    </section>

    <!-- 3. 唤醒上下文（原文版，默认折叠 A3；null 不渲染） -->
    <section v-if="detail.user_prompt_snapshot" class="ob-card">
      <h4 class="context-toggle clickable" @click="contextOpen = !contextOpen">唤醒上下文 {{ contextOpen ? "▾" : "▸" }}</h4>
      <pre v-if="contextOpen" class="context">{{ detail.user_prompt_snapshot }}</pre>
    </section>
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts`
Expected: PASS —— A3 折叠 + chips 去重 + 重命名 + 重排；既有 `"chips 含 llm 与 execution_status"` / `"§① chip 输入/输出"` / `"cache 命中率"` / `"§⑥ 快照格式化真实行为"` 不回归。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/CycleDetailPanel.vue frontend/test/CycleDetailPanel.spec.ts
git commit -m "feat(webui): 详情区 chips 去重 + 唤醒时状态置顶/重命名 + 上下文默认折叠（C3/重排/A3）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: `CycleDetailPanel` 快照内容 —— 余额三标签格 + 现价 UTC + 波动告警（B1/B2/B3）

**Files:**
- Modify: `frontend/src/components/CycleDetailPanel.vue`
- Test: `frontend/test/CycleDetailPanel.spec.ts`

- [ ] **Step 1: 写失败测试（B1 / B2 / B3）**

`frontend/test/CycleDetailPanel.spec.ts` 的 `describe` 内追加：

```typescript
  it("§B1 余额三标签格（总额/可用/占用 + USDT 收尾）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    const txt = w.text();
    expect(txt).toContain("总额");
    expect(txt).toContain("可用");
    expect(txt).toContain("占用");
    expect(txt).toContain("10,000");          // total_usdt 千分位
    expect(txt).toContain("USDT");
    expect(txt).not.toContain("总 10,000");   // 旧平铺文案移除
  });

  it("§B2 现价时间 UTC 格式化（非裸 ISO）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    const txt = w.text();
    expect(txt).toContain("2026-06-12 10:00:00");        // fetched_at 2026-06-12T10:00:00Z
    expect(txt).not.toContain("2026-06-12T10:00:00Z");   // 不再裸 ISO
  });

  it("§B3 快照渲染波动告警（价格/波动两子标签）", () => {
    const base = detail();
    const w = mount(CycleDetailPanel, { props: { detail: { ...base,
      state_snapshot: { ...base.state_snapshot, volatility_alert: { threshold_pct: 1.5, window_minutes: 15 } } } as any } });
    const txt = w.text();
    expect(txt).toContain("价格");            // 价格告警子标签（fixture active_alerts 非空）
    expect(txt).toContain("波动");            // 波动子标签
    expect(txt).toContain("±1.5% / 15min");   // 波动阈值/窗口
  });

  it("§B3 历史快照缺 volatility_alert 键 → 不渲波动子段", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });   // fixture 无 volatility_alert
    expect(w.text()).not.toContain("波动");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts`
Expected: FAIL（无三标签格、现价仍裸 ISO、无波动告警渲染）。

- [ ] **Step 3: script 加 fmtUtc 导入**

把 `frontend/src/components/CycleDetailPanel.vue:8`：

```typescript
import { fmtTokens, fmtDuration, fmtNum, fmtSigned } from "@/utils/format";
```

改为（加 fmtUtc，from time）：

```typescript
import { fmtTokens, fmtDuration, fmtNum, fmtSigned } from "@/utils/format";
import { fmtUtc } from "@/utils/time";
```

- [ ] **Step 4: B1 余额三标签格**

把 Task 9 改后的余额 `<template>`（快照 section 内）：

```html
        <template v-if="snapshot.balance">
          <span class="snap-k">余额</span>
          <span>总 {{ fmtNum(snapshot.balance.total_usdt) }} · 可用 {{ fmtNum(snapshot.balance.free_usdt) }} · 占用 {{ fmtNum(snapshot.balance.used_usdt) }} USDT</span>
        </template>
```

改为：

```html
        <template v-if="snapshot.balance">
          <span class="snap-k">余额</span>
          <span class="bal">
            <span class="seg"><span class="sl">总额</span><span class="sv">{{ fmtNum(snapshot.balance.total_usdt) }}</span></span>
            <span class="seg"><span class="sl">可用</span><span class="sv">{{ fmtNum(snapshot.balance.free_usdt) }}</span></span>
            <span class="seg"><span class="sl">占用</span><span class="sv">{{ fmtNum(snapshot.balance.used_usdt) }}</span></span>
            <span class="unit">USDT</span>
          </span>
        </template>
```

- [ ] **Step 5: B2 现价时间 UTC**

把：

```html
          <span>{{ fmtNum(snapshot.market.ticker_last) }} <span class="muted">@ {{ snapshot.market.fetched_at }}</span></span>
```

改为：

```html
          <span>{{ fmtNum(snapshot.market.ticker_last) }} <span class="muted">@ {{ fmtUtc(snapshot.market.fetched_at) }}</span></span>
```

- [ ] **Step 6: B3 告警拆「价格 / 波动」两类**

把：

```html
        <template v-if="snapshot.active_alerts && snapshot.active_alerts.length">
          <span class="snap-k">告警</span>
          <span><span v-for="(a, i) in snapshot.active_alerts" :key="i" class="snap-item">{{ a.direction }} @{{ fmtNum(a.price) }}<span v-if="a.reasoning" class="muted"> · {{ a.reasoning }}</span></span></span>
        </template>
```

改为：

```html
        <template v-if="(snapshot.active_alerts && snapshot.active_alerts.length) || snapshot.volatility_alert">
          <span class="snap-k">告警</span>
          <span>
            <span v-if="snapshot.active_alerts && snapshot.active_alerts.length" class="alert-grp">
              <span class="muted alert-lbl">价格</span>
              <span v-for="(a, i) in snapshot.active_alerts" :key="i" class="snap-item">{{ a.direction }} @{{ fmtNum(a.price) }}<span v-if="a.reasoning" class="muted"> · {{ a.reasoning }}</span></span>
            </span>
            <span v-if="snapshot.volatility_alert" class="alert-grp">
              <span class="muted alert-lbl">波动</span>
              <span class="snap-item">±{{ fmtNum(snapshot.volatility_alert.threshold_pct) }}% / {{ snapshot.volatility_alert.window_minutes }}min</span>
            </span>
          </span>
        </template>
```

- [ ] **Step 7: 加 B1/B3 样式**

把 `frontend/src/components/CycleDetailPanel.vue:149`：

```css
.snap-item { display: inline-block; margin-right: 10px; }
```

改为：

```css
.snap-item { display: inline-block; margin-right: 10px; }
.bal { display: inline-flex; gap: 18px; align-items: baseline; flex-wrap: wrap; }
.seg { display: inline-flex; gap: 5px; align-items: baseline; }
.seg .sl { color: var(--ob-text-muted); }
.seg .sv { font-variant-numeric: tabular-nums; }
.unit { color: var(--ob-text-muted); }
.alert-grp { display: inline-flex; gap: 6px; align-items: baseline; margin-right: 14px; }
.alert-lbl { font-size: 11px; }
```

- [ ] **Step 8: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts`
Expected: PASS（B1/B2/B3 + 既有不回归）。

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/CycleDetailPanel.vue frontend/test/CycleDetailPanel.spec.ts
git commit -m "feat(webui): 快照余额三标签格 + 现价 UTC + 波动告警展示（B1/B2/B3）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: `ReactTimeline` 思考块 —— 整块折叠默认收起 + 💭「思考」（A1/A2）

**Files:**
- Modify: `frontend/src/components/ReactTimeline.vue`
- Test: `frontend/test/ReactTimeline.spec.ts`

- [ ] **Step 1: 改/写失败测试（多行默认折叠 / 短句豁免 / 💭+标签）**

`frontend/test/ReactTimeline.spec.ts` 改动：

(a) 把 `:106-115` 的 `"§议题2 超长 thinking 默认折叠 + 可展开全文"` 替换为（多行、CSS 折叠下用 `pre v-if` 验证后续行不渲染）：

```typescript
  it("§A1 多行 thinking 默认折叠（只显首行预览）+ 点击展开全文", async () => {
    const full = "第一行预览\n第二行隐藏内容\n第三行也隐藏";
    const p = { ...baseProps(), steps: [{ thinking: full, tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("第一行预览");           // 首行预览可见
    expect(w.text()).not.toContain("第二行隐藏内容");    // 折叠态：后续行不渲染（pre v-if）
    await w.find(".thinking-head").trigger("click");
    expect(w.text()).toContain("第二行隐藏内容");         // 展开后全文
  });
```

(b) 把 `:117-122` 的 `"§议题2 短 thinking 不折叠（无展开按钮）"` 替换为：

```typescript
  it("§A1 短单行 thinking 不折叠（无折叠 affordance、常显全文）", () => {
    const p = { ...baseProps(), steps: [{ thinking: "短推理", tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("短推理");
    expect(w.find(".thinking-head").exists()).toBe(false);   // 无折叠头
  });

  it("§A2 思考块用 💭 图标 + 「思考」标签", () => {
    const p = { ...baseProps(), steps: [{ thinking: "短推理", tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("💭");
    expect(w.text()).toContain("思考");
    expect(w.text()).not.toContain("🧠");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts`
Expected: FAIL（仍是 🧠 + 600 字两段式 + `.thinking-toggle`）。

- [ ] **Step 3: 改 script 常量 + 函数**

把 `frontend/src/components/ReactTimeline.vue:61-76`：

```typescript
// 思考块折叠态（议题 2）
const THINKING_FOLD_CHARS = 600;   // 超此字符数默认折叠
const THINKING_HEAD_CHARS = 360;   // 折叠态预览长度（≈前 6 行）
const openThinking = ref<Set<number>>(new Set());
function thinkingFolds(text: string) {
  return text.length > THINKING_FOLD_CHARS;
}
function thinkingShown(text: string, si: number) {
  if (!thinkingFolds(text) || openThinking.value.has(si)) return text;
  return text.slice(0, THINKING_HEAD_CHARS) + "…";
}
function toggleThinking(si: number) {
  const s = new Set(openThinking.value);
  s.has(si) ? s.delete(si) : s.add(si);
  openThinking.value = s;
}
```

替换为（A1：整块折叠、默认空集=折叠、单行短句豁免）：

```typescript
// 思考块整块折叠（A1）：默认折叠（openThinking 初始空集），按需整块展开。
// needsFold：有换行 或 超单行容量 → 给折叠 affordance 并默认折叠；只有真·单行短句豁免常显。
const THINKING_INLINE_MAX = 100;   // 单行容量小值（非旧的 600）
const openThinking = ref<Set<number>>(new Set());
function needsFold(text: string) {
  return text.includes("\n") || text.length > THINKING_INLINE_MAX;
}
function previewLine(text: string) {
  return text.split("\n")[0];      // 折叠态预览取首行（CSS 再做 ellipsis）
}
function toggleThinking(si: number) {
  const s = new Set(openThinking.value);
  s.has(si) ? s.delete(si) : s.add(si);
  openThinking.value = s;
}
```

- [ ] **Step 4: 改思考块模板**

把 `frontend/src/components/ReactTimeline.vue:105-114`：

```html
      <!-- 思考块（超长默认折叠，议题 2） -->
      <div v-if="step.thinking" class="thinking">
        <span class="step-icon">🧠</span>
        <div class="thinking-body">
          <pre class="thinking-text">{{ thinkingShown(step.thinking, si) }}</pre>
          <span v-if="thinkingFolds(step.thinking)" class="thinking-toggle clickable" @click="toggleThinking(si)">
            {{ openThinking.has(si) ? "收起 ▴" : "展开全文 ▾" }}
          </span>
        </div>
      </div>
```

替换为：

```html
      <!-- 思考块（💭 思考，整块折叠默认收起 A1/A2；单行短句豁免常显） -->
      <div v-if="step.thinking" class="thinking">
        <span class="step-icon">💭</span>
        <div class="thinking-body">
          <template v-if="needsFold(step.thinking)">
            <div class="thinking-head clickable" @click="toggleThinking(si)">
              <span class="tk-lbl">思考</span>
              <span class="tk-caret">{{ openThinking.has(si) ? "▾" : "▸" }}</span>
              <span v-if="!openThinking.has(si)" class="tk-preview">{{ previewLine(step.thinking) }}</span>
            </div>
            <pre v-if="openThinking.has(si)" class="thinking-text">{{ step.thinking }}</pre>
          </template>
          <template v-else>
            <span class="tk-lbl">思考</span> <span class="tk-inline">{{ step.thinking }}</span>
          </template>
        </div>
      </div>
```

- [ ] **Step 5: 改思考块样式（替换 .thinking-toggle，加 head/preview/inline）**

把 `frontend/src/components/ReactTimeline.vue:182-184`：

```css
.thinking-body { flex: 1; }
.thinking-toggle { font-size: 11px; color: var(--ob-text-muted); }
.args-compact { font-size: 12px; word-break: break-word; }
```

替换为：

```css
.thinking-body { flex: 1; min-width: 0; }
.thinking-head { display: flex; align-items: baseline; gap: 6px; font-size: 12px; }
.tk-lbl { color: var(--ob-text-muted); font-weight: 600; }
.tk-caret { color: var(--ob-text-muted); }
.tk-preview { color: var(--ob-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
.tk-inline { font-size: 12px; }
.args-compact { font-size: 12px; word-break: break-word; }
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts`
Expected: PASS（A1 多行折叠 / 短句豁免 / A2 💭+标签 + 既有工具卡/锚定/orphan 测试不回归——baseProps 的短 thinking「评估趋势」「决定开多」走 inline 常显）。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ReactTimeline.vue frontend/test/ReactTimeline.spec.ts
git commit -m "feat(webui): 思考块整块折叠默认收起 + 💭「思考」标签（A1/A2）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: 注入卡 —— 新建 `InjectionCard.vue` 人读摘要 + 接入 `ReactTimeline`（D）

**Files:**
- Create: `frontend/src/components/InjectionCard.vue`
- Modify: `frontend/src/components/ReactTimeline.vue`（InjectedEvent 接口 + 接入组件）
- Test (create): `frontend/test/InjectionCard.spec.ts`
- Test: `frontend/test/ReactTimeline.spec.ts`（去 offset 显示断言）

- [ ] **Step 1: 写 InjectionCard 失败测试**

新建 `frontend/test/InjectionCard.spec.ts`：

```typescript
import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import InjectionCard from "@/components/InjectionCard.vue";

// 2026-06-14T14:51:08Z 的 epoch ms（月份 5 = June）
const TS = Date.UTC(2026, 5, 14, 14, 51, 8);

describe("InjectionCard", () => {
  it("波动告警摘要：基名 + 窗口 + 带号百分比 + 参考价→现价", () => {
    const inj = { kind_label: "波动告警触发", triggered_ago: "1 min ago", offset_ms: 40312,
      event: { type: "percentage_alert", symbol: "BTC/USDT:USDT", window_minutes: 15,
        change_pct: 0.4076887, reference_price: 63823.2, current_price: 64083.4, timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    const txt = w.text();
    expect(txt).toContain("波动告警触发");        // 后端 kind_label 作标题
    expect(txt).toContain("BTC");                 // 基名（去 /USDT:USDT）
    expect(txt).toContain("15min 窗口");
    expect(txt).toContain("+0.41%");
    expect(txt).toContain("63,823.2");
    expect(txt).toContain("64,083.4");
    expect(txt).toContain("1 min ago");           // age 片
    expect(txt).toContain("触发于 14:51:08");      // UTC 时分秒
    expect(txt).not.toContain("40312");           // 去掉 offset_ms 显示
  });

  it("成交摘要：方向 + 张数@价 + 盈亏（红绿）+ 手续费", () => {
    const inj = { kind_label: "止损平仓", triggered_ago: "1 min ago", offset_ms: 25900,
      event: { type: "fill", position_side: "short", amount: 46.37, fill_price: 64280,
        pnl: -103.59, fee: 29.81, timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    const txt = w.text();
    expect(txt).toContain("止损平仓");
    expect(txt).toContain("空");
    expect(txt).toContain("46.37 张");
    expect(txt).toContain("@64,280");
    expect(txt).toContain("盈亏");
    expect(txt).toContain("−103.59");             // U+2212 负号
    expect(txt).toContain("手续费 29.81 USDT");
    expect(w.find(".neg").exists()).toBe(true);   // 负盈亏红字
  });

  it("成交摘要：pnl 缺省（开仓 fill）不渲盈亏段", () => {
    const inj = { kind_label: "限价开多",
      event: { type: "fill", position_side: "long", amount: 1, fill_price: 63000, pnl: null, fee: 0.5, timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    expect(w.text()).not.toContain("盈亏");
  });

  it("价格告警摘要：方向 @目标价（现价）+ reasoning 次行", () => {
    const inj = { kind_label: "价格告警触发", triggered_ago: "45 sec ago",
      event: { type: "price_level_alert", direction: "above", target_price: 63668,
        current_price: 63669, reasoning: "20:00 candle high", timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    const txt = w.text();
    expect(txt).toContain("上破");
    expect(txt).toContain("@63,668");
    expect(txt).toContain("现价 63,669");
    expect(txt).toContain("20:00 candle high");
  });

  it("triggered_ago=null 不渲 age 片", () => {
    const inj = { kind_label: "波动告警触发", triggered_ago: null,
      event: { type: "percentage_alert", symbol: "BTC/USDT:USDT", window_minutes: 15,
        change_pct: 0.4, reference_price: 1, current_price: 2, timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    expect(w.find(".inj-age").exists()).toBe(false);
  });

  it("原始 JSON 默认折叠，点击展开 JsonBlock", async () => {
    const inj = { kind_label: "止损平仓",
      event: { type: "fill", position_side: "short", amount: 1, fill_price: 64280, pnl: -10, fee: 1,
        order_id: "ord-zzz", timestamp: TS } };
    const w = mount(InjectionCard, { props: { inj: inj as any } });
    expect(w.text()).not.toContain("ord-zzz");          // 折叠态不渲原文
    await w.find(".inj-raw-toggle").trigger("click");
    expect(w.text()).toContain("ord-zzz");              // 展开后 JsonBlock 含 order_id
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/InjectionCard.spec.ts`
Expected: FAIL —— `InjectionCard.vue` 不存在。

- [ ] **Step 3: 新建 `frontend/src/components/InjectionCard.vue`**

```html
<script setup lang="ts">
import { ref } from "vue";
import JsonBlock from "@/components/JsonBlock.vue";
import { fmtNum, fmtSigned } from "@/utils/format";
import { fmtUtcEpoch } from "@/utils/time";

interface InjectedEvent {
  event: unknown;
  after_tool?: string;
  offset_ms?: number | null;
  after_tool_call_id?: string | null;
  triggered_ago?: string | null;
  kind_label?: string;
}
const props = defineProps<{ inj: InjectedEvent }>();
const rawOpen = ref(false);

// 第二套更轻的展示渲染器：仅格式化，不做事件分类（开/平及类型已由后端 kind_label 决定）。
function e(): any { return (props.inj.event ?? {}) as any; }
function baseName(sym: string | undefined): string { return sym ? sym.split("/")[0] : ""; }
function sideLabel(s: string | undefined): string { return s === "long" ? "多" : s === "short" ? "空" : (s ?? "?"); }
function dirLabel(d: string | undefined): string { return d === "above" ? "上破" : d === "below" ? "下破" : (d ?? ""); }
function pnlClass(n: number | null | undefined): string { return n == null ? "" : n < 0 ? "neg" : "pos"; }
</script>

<template>
  <div class="injection-card">
    <div class="inj-head">
      <span class="step-icon">⚡</span>
      <span class="inj-title">{{ inj.kind_label || "触发事件注入" }}</span>
      <span v-if="inj.triggered_ago" class="inj-age">{{ inj.triggered_ago }}</span>
    </div>
    <div class="inj-sum">
      <template v-if="e().type === 'percentage_alert'">
        {{ baseName(e().symbol) }} {{ e().window_minutes }}min 窗口
        <span :class="pnlClass(e().change_pct)">{{ fmtSigned(e().change_pct) }}%</span>
        · {{ fmtNum(e().reference_price) }} → {{ fmtNum(e().current_price) }}
      </template>
      <template v-else-if="e().type === 'fill'">
        {{ sideLabel(e().position_side) }} {{ fmtNum(e().amount) }} 张 @{{ fmtNum(e().fill_price) }}
        <template v-if="e().pnl != null"> · 盈亏 <span :class="pnlClass(e().pnl)">{{ fmtSigned(e().pnl) }}</span></template>
        <template v-if="e().fee != null"> · 手续费 {{ fmtNum(e().fee) }} USDT</template>
      </template>
      <template v-else-if="e().type === 'price_level_alert'">
        {{ dirLabel(e().direction) }} @{{ fmtNum(e().target_price) }}（现价 {{ fmtNum(e().current_price) }}）
        <div v-if="e().reasoning" class="inj-reason">{{ e().reasoning }}</div>
      </template>
    </div>
    <div v-if="e().timestamp != null" class="inj-meta">触发于 {{ fmtUtcEpoch(e().timestamp) }}</div>
    <div class="inj-raw-toggle clickable" @click="rawOpen = !rawOpen">原始 JSON {{ rawOpen ? "▾" : "▸" }}</div>
    <JsonBlock v-if="rawOpen" :value="inj.event" />
  </div>
</template>

<style scoped>
/* warn-soft 琥珀底上 muted 仅 ~4.34 → 文字用 --ob-warn 达 AA（沿用 ReactTimeline 既有处方）。 */
.injection-card { margin: 6px 0 6px 18px; padding: 6px 9px; background: var(--ob-warn-soft); border-radius: 4px; font-size: 12px; color: var(--ob-text); }
.inj-head { display: flex; align-items: center; gap: 7px; }
.inj-title { font-weight: 600; }
.inj-age { margin-left: auto; font-size: 11px; color: var(--ob-warn); border: 1px solid var(--ob-warn); border-radius: 4px; padding: 0 6px; }
.inj-sum { margin: 4px 0 0 22px; }
.inj-reason { color: var(--ob-text-muted); font-style: italic; margin-top: 2px; }
.inj-meta { margin: 3px 0 0 22px; font-size: 11px; color: var(--ob-warn); }
.inj-raw-toggle { margin: 4px 0 0 22px; font-size: 11px; color: var(--ob-warn); cursor: pointer; user-select: none; }
.step-icon { flex: 0 0 auto; }
.neg { color: var(--ob-neg); font-weight: 600; }
.pos { color: var(--ob-pos); font-weight: 600; }
.clickable { cursor: pointer; user-select: none; }
</style>
```

- [ ] **Step 4: 跑 InjectionCard 测试确认通过**

Run: `cd frontend && npx vitest run test/InjectionCard.spec.ts`
Expected: PASS。

- [ ] **Step 5: `ReactTimeline` 接口加 triggered_ago + kind_label，import 组件**

把 `frontend/src/components/ReactTimeline.vue:5-6`：

```typescript
import JsonBlock from "@/components/JsonBlock.vue";
import { fmtArgs, fmtDuration, clipArgs } from "@/utils/format";
```

改为（注入卡的 JsonBlock 移入 InjectionCard，这里改导入 InjectionCard）：

```typescript
import JsonBlock from "@/components/JsonBlock.vue";
import InjectionCard from "@/components/InjectionCard.vue";
import { fmtArgs, fmtDuration, clipArgs } from "@/utils/format";
```

把 `frontend/src/components/ReactTimeline.vue:10-15`：

```typescript
interface InjectedEvent {
  event: unknown;
  after_tool?: string;
  offset_ms?: number | null;
  after_tool_call_id?: string | null;
}
```

改为：

```typescript
interface InjectedEvent {
  event: unknown;
  after_tool?: string;
  offset_ms?: number | null;
  after_tool_call_id?: string | null;
  triggered_ago?: string | null;
  kind_label?: string;
}
```

（`JsonBlock` 仍被工具卡 `:133` 使用，import 保留。）

- [ ] **Step 6: 接入 anchored 注入卡（替换裸 JSON）**

把 `frontend/src/components/ReactTimeline.vue:139-145`：

```html
        <!-- 该工具后锚定的注入事件（批量并排） -->
        <div v-for="(inj, ii) in injectionsFor(t, si, ti)" :key="`inj-${si}-${ti}-${ii}`" class="injection-card">
          <span class="step-icon">⚡</span>
          <span class="inj-title">触发事件注入</span>
          <span v-if="inj.offset_ms != null" class="muted">+{{ inj.offset_ms }}ms</span>
          <JsonBlock :value="inj.event" />
        </div>
```

替换为：

```html
        <!-- 该工具后锚定的注入事件（批量并排，人读摘要卡） -->
        <InjectionCard v-for="(inj, ii) in injectionsFor(t, si, ti)" :key="`inj-${si}-${ti}-${ii}`" :inj="inj" />
```

- [ ] **Step 7: 接入 orphan 注入组（保留「未能锚定」标注）**

把 `frontend/src/components/ReactTimeline.vue:149-157`：

```html
    <!-- §10：未能按 id/名锚定的注入 → 时间线末尾归组 -->
    <div v-if="orphanInjections.length" class="orphan-injections">
      <div v-for="(inj, oi) in orphanInjections" :key="`orphan-inj-${oi}`" class="injection-card">
        <span class="step-icon">⚡</span>
        <span class="inj-title">触发事件注入（未能锚定）</span>
        <span v-if="inj.offset_ms != null" class="muted">+{{ inj.offset_ms }}ms</span>
        <JsonBlock :value="inj.event" />
      </div>
    </div>
```

替换为：

```html
    <!-- §10：未能按 id/名锚定的注入 → 时间线末尾归组 -->
    <div v-if="orphanInjections.length" class="orphan-injections">
      <div class="orphan-label muted">未能锚定的注入事件</div>
      <InjectionCard v-for="(inj, oi) in orphanInjections" :key="`orphan-inj-${oi}`" :inj="inj" />
    </div>
```

- [ ] **Step 8: 清理 ReactTimeline 里已迁出的注入卡样式**

把 `frontend/src/components/ReactTimeline.vue:172-178`：

```css
.injection-card { display: flex; align-items: center; gap: 6px; margin: 6px 0 6px 18px; padding: 5px 8px; background: var(--ob-warn-soft); border-radius: 4px; font-size: 12px; }
.inj-title { font-weight: 600; }
.step-icon { flex: 0 0 auto; }
.muted { color: var(--ob-text-muted); }
/* 注入卡 warn-soft 琥珀底上 muted(#6b7280) 仅 4.34 → 用更深 warn 达 AA（review）。
   scoped (0,2,0) 按特异性胜 .muted (0,1,0)，全局 muted/工具耗时仍白卡 4.83 不受影响。 */
.injection-card .muted { color: var(--ob-warn); }
```

替换为（注入卡样式移到 InjectionCard.vue；这里只保留 `.step-icon` / `.muted` 给工具卡与 orphan-label，加 orphan-label）：

```css
.step-icon { flex: 0 0 auto; }
.muted { color: var(--ob-text-muted); }
.orphan-label { font-size: 11px; margin: 8px 0 2px 18px; }
```

- [ ] **Step 9: ReactTimeline 注入测试加「不渲 offset」断言**

`frontend/test/ReactTimeline.spec.ts` 的 `"注入卡按 after_tool_call_id 锚在对应工具后"`（`:53`）末尾追加一行：

```typescript
    expect(txt).not.toContain("+1200ms");   // D：去掉 offset_ms 显示
```

（该用例 fixture `offset_ms: 1200`；既有「触发事件注入」断言仍成立——InjectedEvent 无 kind_label 时 InjectionCard 标题回退「触发事件注入」。）

- [ ] **Step 10: 跑全量前端测试确认通过**

Run: `cd frontend && npm test`
Expected: PASS —— InjectionCard 新测试 + ReactTimeline 既有锚定/orphan/batch 测试（标题回退「触发事件注入」、orphan「未能锚定」来自 orphan-label）+ 不渲 offset。

- [ ] **Step 11: Commit**

```bash
git add frontend/src/components/InjectionCard.vue frontend/src/components/ReactTimeline.vue frontend/test/InjectionCard.spec.ts frontend/test/ReactTimeline.spec.ts
git commit -m "feat(webui): 注入卡人读摘要 + age + 原始 JSON 折叠（D，抽 InjectionCard）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: `DashboardView` 会话头一次性标注「时间均为 UTC」

**Files:**
- Modify: `frontend/src/views/DashboardView.vue`
- Test: `frontend/test/DashboardView.spec.ts`

- [ ] **Step 1: 写失败测试**

`frontend/test/DashboardView.spec.ts` 的 `"会话头合并为单卡"`（`:52`）用例末尾、`expect(card.text()).not.toContain("提醒");` 之后追加：

```typescript
    expect(card.text()).toContain("时间均为 UTC");   // 全局时区标注（去歧义、不逐戳加噪）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/DashboardView.spec.ts`
Expected: FAIL —— 无「时间均为 UTC」。

- [ ] **Step 3: 会话头加 muted 小字标注**

把 `frontend/src/views/DashboardView.vue:35-39`：

```html
    <div class="session-header ob-card">
      <SessionMeta />
      <div class="header-divider" />
      <LiveStatusCard />
    </div>
```

改为：

```html
    <div class="session-header ob-card">
      <SessionMeta />
      <div class="header-divider" />
      <LiveStatusCard />
      <div class="tz-note">时间均为 UTC</div>
    </div>
```

- [ ] **Step 4: 加样式**

把 `frontend/src/views/DashboardView.vue:48`：

```css
.header-divider { height: 1px; background: var(--ob-border); margin: 8px 0; }
```

改为：

```css
.header-divider { height: 1px; background: var(--ob-border); margin: 8px 0; }
.tz-note { margin-top: 6px; font-size: 11px; color: var(--ob-text-muted); }
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/DashboardView.spec.ts`
Expected: PASS（新标注 + 既有 3 用例不回归）。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/DashboardView.vue frontend/test/DashboardView.spec.ts
git commit -m "feat(webui): 会话头一次性标注「时间均为 UTC」

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: 全量回归 + build + Playwright 三路径走查

**Files:**
- 无源码改动（验证 task）；如 build 暴露类型问题，回到对应 task 修。

- [ ] **Step 1: 后端全量 pytest**

Run: `python -m pytest tests/test_webui_queries.py tests/test_cycle_capture.py tests/test_webui_api.py tests/test_event_render.py -q`
Expected: PASS（无回归）。建议再跑全量 `python -m pytest -q` 确认无跨模块影响。

- [ ] **Step 2: 前端全量 vitest**

Run: `cd frontend && npm test`
Expected: PASS（全部 spec）。

- [ ] **Step 3: 前端 build（vue-tsc 类型校验 + 产 dist）**

Run: `cd frontend && npm run build`
Expected: 无 TS 错误（`CycleRow.seq` / `CycleDetail.seq` 已在 types.ts；组件引用类型自洽），产出 `frontend/dist`。

- [ ] **Step 4: 重启 webui 进程（dist + 后端不热更）**

webui 进程与 dist 须重启 + rebuild 才反映改动（不影响采集进程/数据）。本地：

```bash
TRADEBOT_DB=data/tradebot.db python -m src.webui
```

（用户自行在浏览器开 `http://127.0.0.1:8000`；长驻进程不用 run_in_background。）

- [ ] **Step 5: Playwright 三路径走查（console 0 error）**

用 Playwright MCP 打开 sim #21 会话，逐一核验：
1. **header**：cycle 行显示 `#N` 序号 + `起 → 止` UTC 区间；末尾 token/耗时只一处；forensic 行（wall=null）只显单点无 `→`。
2. **默认折叠态**：唤醒时状态展开可见（余额三标签格 + 现价 `YYYY-MM-DD HH:MM:SS` UTC）；唤醒上下文折叠 `▸`；思考块折叠 `💭 思考 ▸` + 首行预览（多行）/ 短句常显；点击各自展开正常。
3. **注入卡**：有注入事件的 cycle（D 目标）——人读摘要（波动/成交/价格三类）+ 右上 age `X ago` + `触发于 HH:MM:SS UTC` + `原始 JSON ▸` 可展开；无裸 JSON dump、无 `+Xms`；**注入 cycle detail 接口不 500**（P0 验证点）。

Expected: 三路径均符合，浏览器 console 0 error。

- [ ] **Step 6:（如有需要）收尾**

全绿后本迭代实现完成，进入 `superpowers:finishing-a-development-branch` 选择合并/PR。
