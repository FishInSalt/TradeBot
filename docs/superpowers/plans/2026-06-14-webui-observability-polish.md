# WebUI 观察台 Phase 1 打磨 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 观察台 Phase 1 的 6 项 UX 改进，核心是让决策 feed「扫读即定位关键事件」——head/end 双段表达开始态持仓 + 本轮关键动作，并补齐会话级 system prompt、cycle 状态快照、推理折叠、数值友好化、标题通俗化。

**Architecture:** 零 DB 迁移——所有新字段从已落库列（`Session.system_prompt` / `AgentCycle.state_snapshot` / `AgentCycle.trigger_context` / `ToolCall`）在 WebUI 查询层派生。后端先定型 schema/query → 重生成 OpenAPI 类型 → 前端组件消费。派生 fail-isolated（单事件异常跳过、不阻断 feed）。

**Tech Stack:** Python / FastAPI / SQLAlchemy 2 async / pydantic v2（后端）；Vue 3 SPA + naive-ui（pin 2.38.1）+ Pinia + vitest（前端）；pytest（后端测试）。

**Spec:** `docs/superpowers/specs/2026-06-14-webui-observability-polish-design.md`

---

## File Structure

**后端：**
- `src/webui/schemas.py` — 加 `PositionBrief` / `KeyEvent`；`CycleRow` 删 `decision_head` + 加 `position` / `key_events`；`SessionDetail` 加 `system_prompt`。
- `src/webui/queries.py` — 新增纯函数 helper（`_classify_fill` / `_classify_action` / `_derive_position` / `_normalize_to_list` / `_safe`）；`get_cycles` 派生 `position` + `key_events`（批量 join `tool_calls`）；`get_session_detail` 加 `system_prompt`；删 dead code `_head` + `_DECISION_HEAD_CHARS`。
- `tests/test_webui_queries.py` — helper 单测 + `get_cycles` 派生逐分支集成测试；改既有 `test_get_cycles_orders_desc_and_paginates`（去 `decision_head` 断言）。
- `tests/test_webui_api.py` — `system_prompt` API 暴露断言。

**类型生成（中间产物）：**
- `frontend/openapi.json` — 从后端 dump 重生成。
- `frontend/src/api/types.ts` — `npm run gen:types` 重生成。

**前端：**
- `frontend/src/utils/format.ts`（新建）+ `frontend/test/format.spec.ts`（新建）— `fmtTokens` / `fmtDuration` / `fmtArgs`。
- `frontend/src/components/CycleRowHeader.vue` + `frontend/test/CycleRowHeader.spec.ts`（新建）— head/end 双段。
- `frontend/src/components/ReactTimeline.vue` + `frontend/test/ReactTimeline.spec.ts` — thinking 折叠 + args 紧凑 + duration 友好。
- `frontend/src/components/CycleDetailPanel.vue` + `frontend/test/CycleDetailPanel.spec.ts` — 状态快照详情区 + 标题改名 + chips 数值友好。
- `frontend/src/components/SessionMeta.vue` + `frontend/test/SessionMeta.spec.ts` — system prompt 折叠区。

**执行顺序注意（重要）：** Task 6（gen types）后到 Task 8（改 `CycleRowHeader`）之间，`CycleRowHeader.vue:13` 仍引用已删除的 `cycle.decision_head`，`vue-tsc --noEmit` 会暂时报错。**前端各 task 内只跑该组件的 vitest（运行时转译，不做全量类型检查），不跑 `npm run build`/`vue-tsc`**。全量 `vue-tsc` + `build` gate 统一放 Task 13。

---

## Task 1: 会话级 system_prompt 后端暴露（议题 1 后端）

**Files:**
- Modify: `src/webui/schemas.py:31-41`（`SessionDetail`）
- Modify: `src/webui/queries.py:176-188`（`get_session_detail`）
- Test: `tests/test_webui_queries.py`、`tests/test_webui_api.py`

- [ ] **Step 1: 写失败测试（query 层）**

在 `tests/test_webui_queries.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py::test_get_session_detail_exposes_system_prompt -q`
Expected: FAIL —`AttributeError: 'SessionDetail' object has no attribute 'system_prompt'`（或 pydantic 构造时无该字段）。

- [ ] **Step 3: schema 加字段**

`src/webui/schemas.py`，`SessionDetail` 末尾（`last_active_at` 后）加：

```python
class SessionDetail(BaseModel):
    id: str
    name: str
    symbol: str
    status: str
    timeframe: str
    scheduler_interval_min: int
    initial_balance: float
    token_budget: int
    created_at: UtcDatetime
    last_active_at: UtcDatetime | None
    system_prompt: str | None = None    # 会话固定 persona（建会话时渲染，models.py:54-55）
```

- [ ] **Step 4: query 透传**

`src/webui/queries.py`，`get_session_detail` 的 `return schemas.SessionDetail(...)` 加一行：

```python
    return schemas.SessionDetail(
        id=sess.id, name=sess.name, symbol=sess.symbol, status=sess.status,
        timeframe=sess.timeframe, scheduler_interval_min=sess.scheduler_interval_min,
        initial_balance=sess.initial_balance, token_budget=sess.token_budget,
        created_at=sess.created_at, last_active_at=sess.last_active_at,
        system_prompt=sess.system_prompt,
    )
```

- [ ] **Step 5: 加 API 层断言**

`tests/test_webui_api.py`，`test_api_endpoints` 内 `c.get("/api/sessions/s1")` 那行之后追加一行（fixture `seeded` 的 session 未设 system_prompt → None）：

```python
    assert c.get("/api/sessions/s1").json()["system_prompt"] is None
```

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py::test_get_session_detail_exposes_system_prompt tests/test_webui_api.py::test_api_endpoints -q`
Expected: PASS（2 passed）。

- [ ] **Step 7: Commit**

```bash
git add src/webui/schemas.py src/webui/queries.py tests/test_webui_queries.py tests/test_webui_api.py
git commit -m "feat(webui): SessionDetail 暴露 system_prompt（议题 1 后端）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: fill 分类纯函数 `_classify_fill` + `KeyEvent`/`PositionBrief` schema（议题 3 后端）

被动成交 fill → `KeyEvent`。数据源 = `trigger_context` 里 `type=="fill"` 的 dict（字段见 cycle_capture.py:37-50）。`pnl`+`is_full_close` 定开/平，`trigger_reason` 定原因，`position_side` 定方向。`market` 回声 → `None`（历史会话旧 echo 去重，spec §2/§3.3）。

**Files:**
- Modify: `src/webui/schemas.py`（新增 `PositionBrief` / `KeyEvent`）
- Modify: `src/webui/queries.py`（新增 `_classify_fill`）
- Test: `tests/test_webui_queries.py`

- [ ] **Step 1: 写失败测试（逐分支）**

`tests/test_webui_queries.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py::test_classify_fill_branches -q`
Expected: FAIL —`ImportError: cannot import name '_classify_fill'`。

- [ ] **Step 3: 加 schema**

`src/webui/schemas.py`，在 `CycleRow` 定义**之前**加两个模型：

```python
class PositionBrief(BaseModel):
    """feed head：本轮开始态持仓精简（state_snapshot.position）。flat → None（不构造本模型）。"""
    side: str                # 'long' | 'short'
    contracts: float
    entry_price: float | None


class KeyEvent(BaseModel):
    """feed end：本轮单个关键事件。被动 fill（fill_*）+ 主动动作（open/add/close/flip/limit_order）。"""
    kind: str                # open|add|close|flip|limit_order | fill_open|fill_close|fill_partial
    label: str               # 开多 / 加仓 / 平多 / 反手→空 / 挂限价单·多 / 限价开多 / 止损平仓 / 强平 …
    direction: str | None    # 'long'|'short'，前端据此着色
```

- [ ] **Step 4: 加 `_classify_fill`**

`src/webui/queries.py`，在 `_loads`（:57）之后加：

```python
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
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py::test_classify_fill_branches -q`
Expected: PASS（1 passed）。

- [ ] **Step 6: Commit**

```bash
git add src/webui/schemas.py src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): _classify_fill + KeyEvent/PositionBrief schema（议题 3 后端）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 主动动作分类纯函数 `_classify_action`（议题 3 后端）

本轮 `tool_calls` → `KeyEvent`。`open_position` 据「操作前持仓方向」（`prev_side` = `state_snapshot.position.side`）分 open/add/flip；`close_position` 无 side 参数（全平，方向取 prev）；`place_limit_order` 据 `args.side`。`open_position.side` 是 `Literal["long","short"]`（trader.py:506，类型强制）；`place_limit_order.side` 是裸 `str`、取值 `'long'`/`'short'` 靠 docstring 约定（:773/:787）——`_classify_action` 对异常值走 `"?"` 兜底。

**Files:**
- Modify: `src/webui/queries.py`（新增 `_classify_action`）
- Test: `tests/test_webui_queries.py`

- [ ] **Step 1: 写失败测试**

`tests/test_webui_queries.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py::test_classify_action_branches -q`
Expected: FAIL —`ImportError: cannot import name '_classify_action'`。

- [ ] **Step 3: 加 `_classify_action`**

`src/webui/queries.py`，紧接 `_classify_fill` 之后加：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py::test_classify_action_branches -q`
Expected: PASS（1 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): _classify_action 主动动作分类（议题 3 后端）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: position 派生 + 形态归一 + fail-isolate（议题 3 后端）

`state_snapshot.position` → `PositionBrief`（flat / 异常形态 → None）；`trigger_context` 形态归一为 `list[dict]`；`_safe` 包裹单事件派生防整 feed 阻断。

**Files:**
- Modify: `src/webui/queries.py`（新增 `_derive_position` / `_normalize_to_list` / `_safe`）
- Test: `tests/test_webui_queries.py`

- [ ] **Step 1: 写失败测试**

`tests/test_webui_queries.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py::test_derive_position_and_normalize -q`
Expected: FAIL —`ImportError: cannot import name '_derive_position'`。

- [ ] **Step 3: 加三个 helper**

`src/webui/queries.py`，紧接 `_classify_action` 之后加：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py::test_derive_position_and_normalize -q`
Expected: PASS（1 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): _derive_position + 形态归一 + _safe fail-isolate（议题 3 后端）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `get_cycles` 集成 + `CycleRow` 改造 + 清理 dead code（议题 3 后端核心）

`CycleRow` 删 `decision_head`、加 `position` + `key_events`；`get_cycles` 批量 join `tool_calls` 并组装：被动 fill（前）+ 主动动作（后）。删 dead code `_head` / `_DECISION_HEAD_CHARS`。

**Files:**
- Modify: `src/webui/schemas.py:44-53`（`CycleRow`）
- Modify: `src/webui/queries.py:18-25`（删 `_head`/`_DECISION_HEAD_CHARS`）、`:28-54`（`get_cycles`）
- Test: `tests/test_webui_queries.py:39-52`（改既有）+ 新增集成测试

- [ ] **Step 1: 改既有测试 + 写集成测试**

先改 `tests/test_webui_queries.py` 的 `test_get_cycles_orders_desc_and_paginates`（:52），把最后一行 `decision_head` 断言换成新字段（默认 `_add_cycle` 无 fill/无 tool/snapshot=None → 空）：

```python
    assert rows[0].position is None and rows[0].key_events == []
```

再在文件末尾追加集成测试（用模块内已有 `_seed_session` / `_add_cycle` / `_fill`，并补一个加 tool_call 的 helper）：

```python
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
    # 同轮：被动 fill 在前、主动动作在后；全平后 snapshot 空仓 → prev_side=None → open（非 flip，真实数据语义）
    assert [e.kind for e in by["dual1"].key_events] == ["fill_close", "open"]
    assert by["dual1"].key_events[1].label == "开空"


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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py -q -k "key_events or orders_desc or derivation_fail"`
Expected: FAIL —`get_cycles` 仍传 `decision_head`、`CycleRow` 无 `position`/`key_events`。

- [ ] **Step 3: 改 `CycleRow` schema**

`src/webui/schemas.py`，`CycleRow` 删 `decision_head`、加两字段：

```python
class CycleRow(BaseModel):
    id: int                   # int PK — 详情跳转/游标用这个
    cycle_label: str          # agent_cycles.cycle_id 字符串，仅显示
    triggered_by: str
    created_at: UtcDatetime
    tokens_consumed: int
    wall_time_ms: int | None
    execution_status: str
    position: PositionBrief | None    # head：本轮开始态持仓（state_snapshot.position）；flat → None
    key_events: list[KeyEvent]        # end：本轮关键动作（被动 fill 在前、主动在后）；无 → []
```

- [ ] **Step 4: 改 `get_cycles` + 删 dead code**

`src/webui/queries.py`：先删 `_DECISION_HEAD_CHARS`（:18）和 `_head`（:21-25）整段；并删 imports 行 `from src.storage.models import (...)` 里若仅 `_head` 用到的项（实际 `TradeAction` 等仍被其他 query 用，无需动 import）。然后把 `get_cycles` 整个函数体替换为：

```python
async def get_cycles(
    engine: AsyncEngine, session_id: str, *,
    limit: int = 50, before_id: int | None = None, after_id: int | None = None,
) -> list[schemas.CycleRow]:
    stmt = select(AgentCycle).where(AgentCycle.session_id == session_id)
    if before_id is not None:
        stmt = stmt.where(AgentCycle.id < before_id)
    if after_id is not None:
        stmt = stmt.where(AgentCycle.id > after_id)
    # after_id（取更新方向）须取紧邻游标的 n 条（ASC）再 reverse，否则 DESC+LIMIT 会返回
    # 游标之上「最新」的 n 条、新增数 > limit 时静默跳过紧邻那批 → 时间线空洞。
    if after_id is not None:
        stmt = stmt.order_by(AgentCycle.id.asc()).limit(limit)
    else:
        stmt = stmt.order_by(AgentCycle.id.desc()).limit(limit)
    async with get_session(engine) as s:
        rows = list((await s.execute(stmt)).scalars().all())
        # 批量 join tool_calls（一次查整批 cycle，feed limit≤200）；按 cycle_id 分组、保留执行序
        cycle_ids = [c.cycle_id for c in rows]
        tool_rows = []
        if cycle_ids:
            tool_rows = list((await s.execute(
                select(ToolCall.cycle_id, ToolCall.tool_name, ToolCall.args)
                .where(ToolCall.session_id == session_id, ToolCall.cycle_id.in_(cycle_ids))
                .order_by(ToolCall.id.asc())
            )).all())
    if after_id is not None:
        rows.reverse()          # 统一为 id DESC 输出（最新在前）
    tools_by_cycle: dict[str, list[tuple[str, object]]] = {}
    for cid, tname, targs in tool_rows:
        tools_by_cycle.setdefault(cid, []).append((tname, _loads(targs)))
    return [
        schemas.CycleRow(
            id=c.id, cycle_label=c.cycle_id, triggered_by=c.triggered_by,
            created_at=c.created_at, tokens_consumed=c.tokens_consumed,
            wall_time_ms=c.wall_time_ms, execution_status=c.execution_status,
            position=_safe(lambda c=c: _derive_position(_loads(c.state_snapshot))),
            key_events=_derive_key_events(c, tools_by_cycle.get(c.cycle_id, [])),
        ) for c in rows
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
```

> 注：`_derive_position(_loads(c.state_snapshot))` 在 `CycleRow.position` 和 `_derive_key_events` 内各算一次——派生廉价（纯 dict 读取），为可读性接受重复，不提前抽局部变量穿线。

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py -q`
Expected: PASS（全文件通过，含改后的 `test_get_cycles_orders_desc_and_paginates` + 3 个新集成测试）。

- [ ] **Step 6: 全 webui 后端测试回归**

Run: `.venv/bin/python -m pytest tests/test_webui_queries.py tests/test_webui_api.py -q`
Expected: PASS。确认无残留 `decision_head` 引用（`grep -rnw decision_head src/ tests/` 应为空；`-w` 词界避开 `decision_header` 子串假阳性）。

- [ ] **Step 7: Commit**

```bash
git add src/webui/schemas.py src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): get_cycles 派生 position + key_events，删 decision_head（议题 3 后端核心）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 重生成 OpenAPI 类型（中间产物）

后端 schema 定型后，从后端 dump OpenAPI → 重生成前端 `types.ts`。**本 task 不跑 `vue-tsc`/`build`**（`CycleRowHeader.vue` 仍用 `decision_head`，Task 8 才改）。

**Files:**
- Regenerate: `frontend/openapi.json`、`frontend/src/api/types.ts`

- [ ] **Step 1: dump 后端 OpenAPI（minified + trailing newline，沿用约定）**

Run（在仓库根）:
```bash
.venv/bin/python -c "import json; from src.webui.app import create_app; print(json.dumps(create_app().openapi()))" > frontend/openapi.json
```
（`json.dumps` 默认 `, `/`: ` 分隔符 + `print` 末尾换行，与现有 `frontend/openapi.json` 格式一致。）

- [ ] **Step 2: 生成前端类型**

Run: `cd frontend && npm run gen:types`
Expected: 无报错，`src/api/types.ts` 更新。

- [ ] **Step 3: 验证类型含新字段（不跑 build）**

Run: `cd frontend && grep -E "system_prompt|key_events|PositionBrief|KeyEvent" src/api/types.ts`
Expected: 命中 `system_prompt`、`key_events`、`PositionBrief`、`KeyEvent`。
Run: `cd frontend && grep -c "decision_head" src/api/types.ts`
Expected: `0`（已移除）。

- [ ] **Step 4: Commit**

```bash
git add frontend/openapi.json frontend/src/api/types.ts
git commit -m "chore(webui): 重生成 openapi.json + 前端类型（position/key_events/system_prompt）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 数值友好化 util（议题 5）

新建 `frontend/src/utils/format.ts`：`fmtTokens`（千分位）/ `fmtDuration`（ms→s）/ `fmtArgs`（紧凑 key=value）。纯函数，后续组件复用。

**Files:**
- Create: `frontend/src/utils/format.ts`
- Test: `frontend/test/format.spec.ts`（新建）

- [ ] **Step 1: 写失败测试**

新建 `frontend/test/format.spec.ts`：

```typescript
import { describe, it, expect } from "vitest";
import { fmtTokens, fmtDuration, fmtArgs } from "@/utils/format";

describe("fmtTokens", () => {
  it("千分位", () => {
    expect(fmtTokens(80733)).toBe("80,733");
    expect(fmtTokens(0)).toBe("0");
  });
  it("null/undefined → 占位", () => {
    expect(fmtTokens(null)).toBe("—");
    expect(fmtTokens(undefined)).toBe("—");
  });
});

describe("fmtDuration", () => {
  it("≥1000ms → s（1 位小数）", () => {
    expect(fmtDuration(49770)).toBe("49.8s");
    expect(fmtDuration(1000)).toBe("1.0s");
  });
  it("<1000ms → ms", () => {
    expect(fmtDuration(320)).toBe("320ms");
  });
  it("0 → <1ms；null → 占位", () => {
    expect(fmtDuration(0)).toBe("<1ms");
    expect(fmtDuration(null)).toBe("—");
  });
});

describe("fmtArgs", () => {
  it("dict → 紧凑 key=value 单行", () => {
    expect(fmtArgs({ timeframe: "1h", candle_count: 30 })).toBe("timeframe=1h, candle_count=30");
  });
  it("空/无参 → （无参）", () => {
    expect(fmtArgs({})).toBe("（无参）");
    expect(fmtArgs(null)).toBe("（无参）");
    expect(fmtArgs(undefined)).toBe("（无参）");
  });
  it("嵌套值 dict/list → 回退 JSON 串", () => {
    expect(fmtArgs({ levels: [1, 2] })).toBe("levels=[1,2]");
    expect(fmtArgs({ cfg: { a: 1 } })).toBe('cfg={"a":1}');
  });
  it("顶层非 dict（截断回退 str / list）→ JSON 串", () => {
    expect(fmtArgs("broken")).toBe('"broken"');
    expect(fmtArgs([1, 2])).toBe("[1,2]");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/format.spec.ts`
Expected: FAIL —`Failed to resolve import "@/utils/format"`。

- [ ] **Step 3: 实现 util**

新建 `frontend/src/utils/format.ts`：

```typescript
/** 数值与展示友好化（议题 5）。 */

export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US");
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms === 0) return "<1ms";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 工具入参紧凑展示：`timeframe=1h, candle_count=30`。嵌套值回退 JSON 串。
 *  空 / 无参 → `（无参）`；顶层非 dict（截断回退 str / list）→ JSON 串。 */
export function fmtArgs(args: unknown): string {
  if (args == null) return "（无参）";
  if (typeof args !== "object" || Array.isArray(args)) return JSON.stringify(args);
  const entries = Object.entries(args as Record<string, unknown>);
  if (!entries.length) return "（无参）";
  return entries
    .map(([k, v]) => `${k}=${typeof v === "object" && v !== null ? JSON.stringify(v) : v}`)
    .join(", ");
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/format.spec.ts`
Expected: PASS（全部通过）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/format.ts frontend/test/format.spec.ts
git commit -m "feat(webui): 数值友好化 util（fmtTokens/fmtDuration/fmtArgs，议题 5）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: feed 行 head/end 双段（议题 3 前端）

`CycleRowHeader.vue` 改：head（开始态持仓）+ end（key_events chips，按方向着色）+ key_events 非空整行左色条 + 遥测用 format util。

**Files:**
- Modify: `frontend/src/components/CycleRowHeader.vue`（全量重写 script+template+style）
- Test: `frontend/test/CycleRowHeader.spec.ts`（新建）

- [ ] **Step 1: 写失败测试**

新建 `frontend/test/CycleRowHeader.spec.ts`：

```typescript
import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CycleRowHeader from "@/components/CycleRowHeader.vue";

function cycle(overrides = {}) {
  return {
    id: 1, cycle_label: "c1", triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z",
    tokens_consumed: 80733, wall_time_ms: 49770, execution_status: "ok",
    position: null, key_events: [],
    ...overrides,
  };
}

describe("CycleRowHeader", () => {
  it("flat 开始态 + 无交易：head 空仓 + end （无交易）", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle() as any } });
    expect(w.text()).toContain("开始");
    expect(w.text()).toContain("空仓");
    expect(w.text()).toContain("（无交易）");
    expect(w.find(".keyrow").exists()).toBe(false);   // 噪声轮无色条
  });

  it("有开始态持仓：head 显示方向/张数/入场价", () => {
    const w = mount(CycleRowHeader, {
      props: { cycle: cycle({ position: { side: "short", contracts: 17.99, entry_price: 63896.0 } }) as any },
    });
    expect(w.text()).toContain("空");
    expect(w.text()).toContain("17.99");
    expect(w.text()).toContain("63896");
  });

  it("key_events 非空：每事件一枚 chip + 整行色条高亮", () => {
    const w = mount(CycleRowHeader, {
      props: { cycle: cycle({ key_events: [
        { kind: "fill_close", label: "止损平仓", direction: "long" },
        { kind: "flip", label: "反手→空", direction: "short" },
      ] }) as any },
    });
    expect(w.text()).toContain("止损平仓");
    expect(w.text()).toContain("反手→空");
    expect(w.find(".keyrow").exists()).toBe(true);    // 关键事件锚点色条
  });

  it("遥测用 format util（千分位 + s）", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle() as any } });
    expect(w.text()).toContain("80,733");
    expect(w.text()).toContain("49.8s");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/CycleRowHeader.spec.ts`
Expected: FAIL —组件仍渲染 `decision_head`，无 head/end 结构。

- [ ] **Step 3: 重写组件**

`frontend/src/components/CycleRowHeader.vue` 全量替换为：

```vue
<script setup lang="ts">
import { computed } from "vue";
import type { CycleRow } from "@/api/client";
import { NTag } from "naive-ui";
import { fmtLocal } from "@/utils/time";
import { fmtTokens, fmtDuration } from "@/utils/format";

const props = defineProps<{ cycle: CycleRow }>();

const headText = computed(() => {
  const p = props.cycle.position;
  if (!p) return "空仓";
  const d = p.side === "long" ? "多" : p.side === "short" ? "空" : p.side;
  const ep = p.entry_price != null ? ` @${Math.round(p.entry_price)}` : "";
  return `${d} ${p.contracts}张${ep}`;
});

// kind → chip 配色：开=绿 / 平=红 / 挂单=蓝 / 反手=黄（spec §3.4）
function chipType(kind: string): "success" | "error" | "info" | "warning" | "default" {
  if (kind === "open" || kind === "add" || kind === "fill_open") return "success";
  if (kind === "close" || kind === "fill_close" || kind === "fill_partial") return "error";
  if (kind === "limit_order") return "info";
  if (kind === "flip") return "warning";
  return "default";
}
</script>

<template>
  <div class="cycle-head" :class="{ keyrow: cycle.key_events.length > 0 }">
    <span class="time">{{ fmtLocal(cycle.created_at) }}</span>
    <n-tag size="small" :bordered="false">{{ cycle.triggered_by }}</n-tag>
    <span class="seg head-pos"><span class="seg-label">开始:</span> {{ headText }}</span>
    <span class="seg end-events">
      <span class="seg-label">本轮:</span>
      <template v-if="cycle.key_events.length">
        <n-tag v-for="(e, i) in cycle.key_events" :key="i" size="tiny" :type="chipType(e.kind)" :bordered="false">
          {{ e.label }}
        </n-tag>
      </template>
      <span v-else class="muted">（无交易）</span>
    </span>
    <n-tag size="small" :type="cycle.execution_status === 'ok' ? 'default' : 'error'" :bordered="false">
      {{ cycle.execution_status }}
    </n-tag>
    <span class="tele">{{ fmtTokens(cycle.tokens_consumed) }}tok · {{ fmtDuration(cycle.wall_time_ms) }}</span>
  </div>
</template>

<style scoped>
.cycle-head { display: flex; align-items: center; gap: 8px; width: 100%; font-size: 13px; padding-left: 6px; border-left: 3px solid transparent; }
.cycle-head.keyrow { border-left-color: #60a5fa; }   /* 关键事件锚点高亮 */
.time { opacity: 0.7; white-space: nowrap; }
.seg { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; overflow: hidden; }
.seg-label { opacity: 0.5; font-size: 11px; }
.head-pos { min-width: 120px; }
.end-events { flex: 1; flex-wrap: wrap; }
.tele { font-size: 11px; opacity: 0.5; white-space: nowrap; }
.muted { opacity: 0.45; }
</style>
```

- [ ] **Step 4: 同步既有 decision_head 消费者测试**

删 `decision_head` 后两个前端测试会破/残留，须同步（否则 Task 13 Step 4 的 grep gate 必非空，且 DecisionStream mount 新 CycleRowHeader 会因 undefined `key_events` 崩）：

`frontend/test/DecisionStream.spec.ts` 的 `cyc()` 工厂（:7-9）改——去 `decision_head`、加 `position`/`key_events`，并给 `triggered_by` 唯一值以保留顺序断言：

```typescript
function cyc(id: number) {
  return { id, cycle_label: `c${id}`, triggered_by: `t${id}`, created_at: "2026-06-12T10:00:00Z", tokens_consumed: 1, wall_time_ms: 1, execution_status: "ok", position: null, key_events: [] };
}
```

并把首个测试「按 store.cycles 顺序渲染每条 cycle 表头」（:24-29）的断言从 `decision_head` 文本（`head3`/`head1`）改为表头计数 + `triggered_by` 顺序：

```typescript
  it("按 store.cycles 顺序渲染每条 cycle 表头", async () => {
    const { wrapper } = mountStream();
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".cycle-head").length).toBe(3);   // 三条都渲染
    expect(wrapper.text().indexOf("t3")).toBeLessThan(wrapper.text().indexOf("t1"));  // store.cycles 顺序
  });
```

`frontend/test/store.spec.ts` 的 `cyc()` 工厂（:9-11）同步去 `decision_head`、加 `position`/`key_events`（纯 store 测试，断言全基于 `c.id`，不受影响）：

```typescript
function cyc(id: number) {
  return { id, cycle_label: `c${id}`, triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z", tokens_consumed: 1, wall_time_ms: 1, execution_status: "ok", position: null, key_events: [] };
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/CycleRowHeader.spec.ts test/DecisionStream.spec.ts test/store.spec.ts`
Expected: PASS（三个 spec 全通过；DecisionStream mount CycleRowHeader 不再因 undefined `key_events` 崩）。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/CycleRowHeader.vue frontend/test/CycleRowHeader.spec.ts frontend/test/DecisionStream.spec.ts frontend/test/store.spec.ts
git commit -m "feat(webui): feed 行 head/end 双段 + 关键事件色条；同步 decision_head 消费者测试（议题 3 前端）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: 推理块超长折叠（议题 2）

`ReactTimeline.vue` 🧠 thinking 块：字符数 > 阈值（默认 600）默认折叠，显示前若干行 + 「展开全文 ▾」；≤ 阈值全显示。逐块独立折叠态。

**Files:**
- Modify: `frontend/src/components/ReactTimeline.vue`
- Test: `frontend/test/ReactTimeline.spec.ts`

- [ ] **Step 1: 写失败测试**

`frontend/test/ReactTimeline.spec.ts`，在 `describe` 块内追加：

```typescript
  it("§议题2 超长 thinking 默认折叠 + 可展开全文", async () => {
    const long = "x".repeat(700);
    const p = { ...baseProps(), steps: [{ thinking: long, tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("展开全文");
    expect(w.text()).not.toContain(long);               // 折叠态不渲染全文
    await w.find(".thinking-toggle").trigger("click");
    expect(w.text()).toContain(long);                   // 展开后渲染全文
  });

  it("§议题2 短 thinking 不折叠（无展开按钮）", () => {
    const p = { ...baseProps(), steps: [{ thinking: "短推理", tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("短推理");
    expect(w.text()).not.toContain("展开全文");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts`
Expected: FAIL —无 `.thinking-toggle`、无「展开全文」。

- [ ] **Step 3: 实现折叠**

`frontend/src/components/ReactTimeline.vue`：

(a) `<script setup>` 顶部加常量 + 折叠态 + helper（紧接现有 `const openCards = ref...` 之后，约 :58）：

```typescript
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

(b) template 思考块（现 :84-87）替换为：

```vue
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

(c) `<style scoped>` 末尾加：

```css
.thinking-body { flex: 1; }
.thinking-toggle { font-size: 11px; opacity: 0.6; }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts`
Expected: PASS（含原有 + 2 个新测试）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReactTimeline.vue frontend/test/ReactTimeline.spec.ts
git commit -m "feat(webui): ReactTimeline 推理块超长折叠（议题 2）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: 工具卡 args 紧凑 + duration 友好（议题 5 应用到 ReactTimeline）

`ReactTimeline.vue` 工具卡入参用 `fmtArgs` 紧凑单行（替换 `JsonBlock`），duration 用 `fmtDuration`。

**Files:**
- Modify: `frontend/src/components/ReactTimeline.vue`
- Test: `frontend/test/ReactTimeline.spec.ts`

- [ ] **Step 1: 写失败测试**

`frontend/test/ReactTimeline.spec.ts`，在 `describe` 块内追加：

```typescript
  it("§议题5 工具卡 args 紧凑单行 + duration 友好", async () => {
    const p = {
      ...baseProps(),
      steps: [{ thinking: null, tools: [{ tool_call_id: "call_a", tool_name: "get_market_data" }] }],
      toolCalls: [{ tool_name: "get_market_data", status: "ok", duration_ms: 1500, error_type: null,
                    args: { timeframe: "1h", candle_count: 30 }, result: "ok", tool_call_id: "call_a" }],
    };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("1.5s");                          // duration 友好
    await w.findAll(".tool-card .tool-head")[0].trigger("click");
    expect(w.text()).toContain("timeframe=1h, candle_count=30"); // args 紧凑单行
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts -t "议题5 工具卡"`
Expected: FAIL —duration 显示 `1500ms`、args 走 JsonBlock 多行 JSON。

- [ ] **Step 3: 改 ReactTimeline**

`frontend/src/components/ReactTimeline.vue`：

(a) `<script setup>` import 加 `fmtArgs` / `fmtDuration`（JsonBlock 仍用于 result 与注入事件，保留）：

```typescript
import { fmtArgs, fmtDuration } from "@/utils/format";
```

(b) template 工具卡 duration（现 :99 `{{ rowFor(t)!.duration_ms }}ms`）改：

```vue
              <span class="muted">{{ fmtDuration(rowFor(t)!.duration_ms) }}</span>
```

(c) template 工具卡入参（现 :104）`<div class="kv"><span class="k">入参</span><JsonBlock :value="rowFor(t)!.args" /></div>` 改为紧凑单行：

```vue
            <div class="kv"><span class="k">入参</span><span class="args-compact">{{ fmtArgs(rowFor(t)!.args) }}</span></div>
```

(d) `<style scoped>` 末尾加：

```css
.args-compact { font-size: 12px; word-break: break-word; }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts`
Expected: PASS（全文件通过）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReactTimeline.vue frontend/test/ReactTimeline.spec.ts
git commit -m "feat(webui): 工具卡 args 紧凑 + duration 友好（议题 5）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: cycle 状态快照详情区 + 标题改名 + chips 数值友好（议题 4 + 6 + 5）

`CycleDetailPanel.vue`：新增「状态快照」折叠区（默认折叠，渲染 `state_snapshot` 完整内容）；「ReAct 过程」→「推理与行动过程」；chips 的 tokens/wall/llm 用 format util。

**Files:**
- Modify: `frontend/src/components/CycleDetailPanel.vue`
- Test: `frontend/test/CycleDetailPanel.spec.ts`

- [ ] **Step 1: 写失败测试**

`frontend/test/CycleDetailPanel.spec.ts`：先把 `detail()` 工厂的 `state_snapshot` 改为含完整结构（便于断言子块）：

```typescript
    state_snapshot: {
      position: { side: "short", contracts: 17.99, entry_price: 63896.0, unrealized_pnl: -12.5, leverage: 5 },
      balance: { total_usdt: 10000, free_usdt: 9000, used_usdt: 1000 },
      market: { ticker_last: 63900, fetched_at: "2026-06-12T10:00:00Z" },
      pending_orders: [{ id: "o1", order_type: "stop", side: "sell", trigger_price: 62000, amount: 1 }],
      active_alerts: [{ id: "a1", direction: "above", price: 64000, reasoning: "breakout" }],
      _errors: [], _cycle_id: "c5",
    },
```

再追加测试：

```typescript
  it("§议题6 标题改为「推理与行动过程」", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("推理与行动过程");
    expect(w.text()).not.toContain("ReAct 过程");
  });

  it("§议题4 状态快照详情区：展开后渲染持仓/余额/告警", async () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    const toggle = w.findAll(".snapshot-toggle")[0];
    await toggle.trigger("click");
    const txt = w.text();
    expect(txt).toContain("17.99");      // 持仓张数
    expect(txt).toContain("9000");       // 余额 free
    expect(txt).toContain("breakout");   // 告警 reasoning
    expect(txt).not.toContain("_cycle_id");   // 内部键不展示
  });

  it("§议题5 chips token 千分位 + 耗时 s", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ tokens_consumed: 80733, wall_time_ms: 49770 }) as any } });
    expect(w.text()).toContain("80,733");
    expect(w.text()).toContain("49.8s");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts`
Expected: FAIL —无「推理与行动过程」、无 `.snapshot-toggle`、chips 仍 `80733`/`49770ms`。

- [ ] **Step 3: 改组件**

`frontend/src/components/CycleDetailPanel.vue`：

(a) `<script setup>` import 加 format util + 折叠态 + snapshot 计算（紧接 `const toolsOpen = ref(false)` 附近）：

```typescript
import { fmtTokens, fmtDuration } from "@/utils/format";
```
```typescript
const snapshotOpen = ref(false);
// state_snapshot 可能是 dict|list|str（放宽形态）；仅 dict 渲染结构化详情，内部键剔除
const snapshot = computed(() => {
  const s = props.detail.state_snapshot;
  return s && typeof s === "object" && !Array.isArray(s) ? (s as Record<string, any>) : null;
});
```

(b) template chips（现 :47/:50/:51）改用 format util：

```vue
      <n-tag size="small">tokens {{ fmtTokens(detail.tokens_consumed) }}</n-tag>
      <n-tag v-if="detail.input_tokens != null" size="small">in {{ fmtTokens(detail.input_tokens) }} / out {{ fmtTokens(detail.output_tokens) }}</n-tag>
      <n-tag v-if="detail.cache_hit_rate != null" size="small">cache {{ detail.cache_hit_rate.toFixed(0) }}%</n-tag>
      <n-tag v-if="detail.wall_time_ms != null" size="small">wall {{ fmtDuration(detail.wall_time_ms) }}</n-tag>
      <n-tag v-if="detail.llm_call_ms != null" size="small">llm {{ fmtDuration(detail.llm_call_ms) }}</n-tag>
```

(c) template 标题（现 :64）`<h4>ReAct 过程</h4>` 改：

```vue
      <h4>推理与行动过程</h4>
```

(d) template 在「决策」section（现 :88）之前插入状态快照 section：

```vue
    <!-- 状态快照详情（默认折叠，议题 4）；state_snapshot 是本轮开始态 -->
    <section v-if="snapshot">
      <h4 class="snapshot-toggle clickable" @click="snapshotOpen = !snapshotOpen">
        状态快照（开始态）{{ snapshotOpen ? "▾" : "▸" }}
      </h4>
      <div v-if="snapshotOpen" class="snapshot">
        <div class="snap-block" v-if="snapshot.position">
          <span class="snap-k">持仓</span>
          <span>{{ snapshot.position.side }} · {{ snapshot.position.contracts }}张 · 入场 {{ snapshot.position.entry_price }} · 杠杆 {{ snapshot.position.leverage }}x · 浮盈 {{ snapshot.position.unrealized_pnl }}</span>
        </div>
        <div class="snap-block" v-else><span class="snap-k">持仓</span><span class="muted">空仓</span></div>
        <div class="snap-block" v-if="snapshot.balance">
          <span class="snap-k">余额</span>
          <span>total {{ snapshot.balance.total_usdt }} · free {{ snapshot.balance.free_usdt }} · used {{ snapshot.balance.used_usdt }}</span>
        </div>
        <div class="snap-block" v-if="snapshot.market">
          <span class="snap-k">现价</span>
          <span>{{ snapshot.market.ticker_last }} <span class="muted">@ {{ snapshot.market.fetched_at }}</span></span>
        </div>
        <div class="snap-block" v-if="snapshot.pending_orders && snapshot.pending_orders.length">
          <span class="snap-k">挂单</span>
          <span>
            <span v-for="(o, i) in snapshot.pending_orders" :key="i" class="snap-item">
              {{ o.order_type }} {{ o.side }} @{{ o.trigger_price ?? o.price }} ×{{ o.amount }}
            </span>
          </span>
        </div>
        <div class="snap-block" v-if="snapshot.active_alerts && snapshot.active_alerts.length">
          <span class="snap-k">告警</span>
          <span>
            <span v-for="(a, i) in snapshot.active_alerts" :key="i" class="snap-item">
              {{ a.direction }} @{{ a.price }}<span v-if="a.reasoning" class="muted"> · {{ a.reasoning }}</span>
            </span>
          </span>
        </div>
      </div>
    </section>
```

(e) `<style scoped>` 末尾加：

```css
.snapshot { font-size: 12px; }
.snap-block { display: flex; gap: 8px; margin: 4px 0; line-height: 1.5; }
.snap-k { opacity: 0.55; min-width: 36px; flex: 0 0 auto; }
.snap-item { display: inline-block; margin-right: 10px; }
.muted { opacity: 0.55; }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts`
Expected: PASS（含改后的既有测试 + 3 个新测试）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CycleDetailPanel.vue frontend/test/CycleDetailPanel.spec.ts
git commit -m "feat(webui): cycle 状态快照详情区 + 标题改名 + chips 数值友好（议题 4/6/5）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: 会话级 system prompt 折叠区（议题 1 前端）

`SessionMeta.vue` 在 NDescriptions 之后加折叠区「System Prompt（persona，会话固定）」，默认折叠；`system_prompt` 为空不渲染。

**Files:**
- Modify: `frontend/src/components/SessionMeta.vue`
- Test: `frontend/test/SessionMeta.spec.ts`

- [ ] **Step 1: 写失败测试**

`frontend/test/SessionMeta.spec.ts`，在 `describe` 块内追加：

```typescript
  it("§议题1 有 system_prompt：折叠区可展开看全文", async () => {
    const wrapper = mount(SessionMeta, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.detail = { id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1h",
      scheduler_interval_min: 15, initial_balance: 10000, token_budget: 200000,
      created_at: "2026-06-12T10:00:00Z", last_active_at: null,
      system_prompt: "You are a disciplined futures trader." } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("System Prompt");
    expect(wrapper.text()).not.toContain("disciplined");      // 默认折叠
    await wrapper.find(".sysprompt-toggle").trigger("click");
    expect(wrapper.text()).toContain("disciplined");          // 展开后全文
  });

  it("§议题1 无 system_prompt：不渲染折叠区", async () => {
    const wrapper = mount(SessionMeta, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.detail = { id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1h",
      scheduler_interval_min: 15, initial_balance: 10000, token_budget: 200000,
      created_at: "2026-06-12T10:00:00Z", last_active_at: null, system_prompt: null } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).not.toContain("System Prompt");
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/SessionMeta.spec.ts`
Expected: FAIL —无「System Prompt」、无 `.sysprompt-toggle`。

- [ ] **Step 3: 改组件**

`frontend/src/components/SessionMeta.vue` 全量替换为：

```vue
<script setup lang="ts">
import { computed, ref } from "vue";
import { NDescriptions, NDescriptionsItem } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";

const store = useSessionsStore();
const d = computed(() => store.detail);
const promptOpen = ref(false);
</script>

<template>
  <div v-if="d" class="session-meta-wrap">
    <n-descriptions :column="5" size="small" label-placement="left" class="session-meta" bordered>
      <n-descriptions-item label="Symbol">{{ d.symbol }}</n-descriptions-item>
      <n-descriptions-item label="周期">{{ d.timeframe }}</n-descriptions-item>
      <n-descriptions-item label="调度间隔">{{ d.scheduler_interval_min }}min</n-descriptions-item>
      <n-descriptions-item label="初始余额">{{ d.initial_balance }}</n-descriptions-item>
      <n-descriptions-item label="Token 预算">{{ d.token_budget }}</n-descriptions-item>
    </n-descriptions>
    <section v-if="d.system_prompt" class="sysprompt">
      <span class="sysprompt-toggle clickable" @click="promptOpen = !promptOpen">
        System Prompt（persona，会话固定）{{ promptOpen ? "▾" : "▸" }}
      </span>
      <pre v-if="promptOpen" class="sysprompt-text">{{ d.system_prompt }}</pre>
    </section>
  </div>
</template>

<style scoped>
.session-meta-wrap { padding: 6px 16px; }
.session-meta { margin-bottom: 6px; }
.sysprompt-toggle { cursor: pointer; user-select: none; font-size: 12px; opacity: 0.8; }
.clickable { cursor: pointer; user-select: none; }
.sysprompt-text { white-space: pre-wrap; word-break: break-word; background: rgba(0,0,0,0.22); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 6px 0 0; max-height: 320px; overflow-y: auto; }
</style>
```

> 注：既有测试「detail 为空时不渲染」断言 `.session-meta` 不存在——重写后 `v-if="d"` 包裹 `.session-meta-wrap`，`d` 为空时整块不渲染，`.session-meta` 同样不存在，该断言仍成立。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/SessionMeta.spec.ts`
Expected: PASS（含既有 2 个 + 新 2 个）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SessionMeta.vue frontend/test/SessionMeta.spec.ts
git commit -m "feat(webui): 会话级 system prompt 折叠区（议题 1 前端）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: 全量验证 gate + 完成

后端全量 pytest（#78 教训：per-file 跑会漏 drift-guard 簇）+ 前端全量类型检查 + build + vitest。

**Files:** 无（仅验证）

- [ ] **Step 1: 后端全量回归**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS（全绿；基线 2363+ 后端测试 + 本 iter 新增）。若有非本 iter 的既有失败，先定位是否本 iter 引入。

- [ ] **Step 2: 前端全量类型检查 + build**

Run: `cd frontend && npm run build`
Expected: `vue-tsc --noEmit` 0 error + `vite build` 成功（此刻 `CycleRowHeader` 已改、不再引用 `decision_head`）。

- [ ] **Step 3: 前端全量 vitest**

Run: `cd frontend && npm test`
Expected: PASS（全部 spec 通过，含本 iter 新增 format/CycleRowHeader 及改动的 ReactTimeline/CycleDetailPanel/SessionMeta）。

- [ ] **Step 4: 残留引用扫描**

Run: `grep -rnw "decision_head" src/ tests/ frontend/src/ frontend/test/`
Expected: 空（无残留）。`-w` 词界排除 `test_..._decision_header_...`（`decision_head`**er**）的子串假阳性。
Run: `grep -rn "ReAct 过程" frontend/src/`
Expected: 空（标题已改）。

- [ ] **Step 5: 完成开发分支**

REQUIRED SUB-SKILL: 用 superpowers:finishing-a-development-branch 收尾（验证测试 → 选 merge/PR/keep/discard）。

---

## Self-Review

**1. Spec coverage（6 议题逐项）：**
- 议题 1（system prompt）：Task 1（后端暴露）+ Task 12（前端折叠区）✓
- 议题 2（推理折叠）：Task 9 ✓
- 议题 3（head/end 双段）：Task 2-5（后端派生）+ Task 8（前端 feed 行）✓——含 spec §3.3 全分支（fill_open/止损/止盈/强平/限价平/部分平/market 去重/同轮双事件/open/add/flip/close/limit_order/噪声轮空）+ fail-isolate
- 议题 4（状态快照详情区）：Task 11 ✓
- 议题 5（数值友好化）：Task 7（util）+ Task 10（ReactTimeline args/duration）+ Task 11（chips）✓
- 议题 6（标题通俗化）：Task 11 ✓
- spec §9 后端汇总：Task 5 删 dead code `_head`/`_DECISION_HEAD_CHARS` + Task 6 重生成类型 ✓
- spec §11 非目标：均未触碰（无 agent loop / 无 DB 迁移 / 无绝对量价解析 / 无 TradeAction join / 无 cancel_order key_event / 无实时推送）✓

**2. Placeholder scan：** 无 TBD/TODO；每个 code step 含完整代码；每个 run step 含确切命令 + 预期输出。

**3. Type consistency：** 后端 `PositionBrief`(side/contracts/entry_price) + `KeyEvent`(kind/label/direction) 字段名贯穿 schema → OpenAPI → types.ts → 前端组件断言；`CycleRow.key_events: list[KeyEvent]` 与前端 `cycle.key_events`（数组，`.length` / chip 遍历）一致；helper 名（`_classify_fill`/`_classify_action`/`_derive_position`/`_normalize_to_list`/`_safe`/`_derive_key_events`）跨 task 一致；前端 util 名（`fmtTokens`/`fmtDuration`/`fmtArgs`）在 Task 7 定义、Task 8/10/11 消费一致。

**4. 执行顺序风险已标注：** Task 6→8 之间 `vue-tsc` 暂红——各前端 task 内只跑组件 vitest，全量 gate 集中 Task 13。
