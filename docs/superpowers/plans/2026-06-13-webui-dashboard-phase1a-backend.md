# WebUI Phase 1a — 后端只读 JSON API 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个只读的 FastAPI JSON API（`src/webui/`），把 agent 决策过程与交易表现从 SQLite 暴露给前端，零改 schema、零动现有 `src/` 模块、不向 agent 发指令。

**Architecture:** `src/webui/db.py` 建 `mode=ro` 只读 async engine（spike 实测可读 live WAL 库）；`src/webui/queries.py` 纯只读查询函数（核心可测单元）；`src/webui/schemas.py` pydantic 契约；`src/webui/app.py` 薄 HTTP 层。复用现有 `src/storage/models`、`src/storage/views`、`src/services/metrics`。

**Tech Stack:** Python 3.12+、FastAPI、uvicorn、SQLAlchemy 2.0 async + aiosqlite、pydantic v2、pytest + httpx(TestClient)。

设计依据：`docs/superpowers/specs/2026-06-12-webui-dashboard-phase1-design.md`（本计划锚点）+ `2026-06-12-webui-target-architecture.md`。

> **范围注（v1 精简）**：状态卡**只显示 `Session.status` + `last_active_at` 原始戳**，不重构"下次唤醒"、不派生精确 liveness（spec §5.2——复杂度不抵价值）。本计划据此不含唤醒重构逻辑。

---

## 数据事实速查（实施时直接引用，避免再翻代码）

- **DB URL**：`sqlite+aiosqlite:///data/tradebot.db`（`src/config.py:59`）。只读形式见 Task 2。
- **`agent_cycles` 列**：`id`(int PK)、`session_id`、`cycle_id`(str "6923")、`triggered_by`、`trigger_context`(JSON text)、`state_snapshot`(JSON text)、`decision`(text)、`execution_status`、`reasoning`(text)、`model_id`、`tokens_consumed`、`wall_time_ms`、`llm_call_ms`、`input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_write_tokens`/`reasoning_tokens`、`cache_hit_rate`、`user_prompt_snapshot`、`injected_events`、`created_at`。
- **`tool_calls` 列**：`id`、`session_id`、`cycle_id`、`tool_name`、`status`("ok"/"error"/"biz_error")、`duration_ms`、`error_type`、`created_at`、`args`(JSON text，**已 strip `reasoning`**，**无 output 列**)。
- **`sessions` 列**：`id`、`name`、`symbol`、`initial_balance`、`status`("active"/"paused")、`created_at`、`updated_at`、`exchange_type`、`timeframe`、`scheduler_interval_min`、`fee_rate`、`contract_size`、`token_budget`、`last_active_at`。
- **`sim_positions`**：`session_id`、`symbol`、`side`、`contracts`、`entry_price`、`leverage`、`updated_at`。
- **`sim_orders`**：`session_id`、`order_id`、`symbol`、`side`、`position_side`、`order_type`、`amount`、`trigger_price`、`status`("open"/"closed"/"cancelled")、`filled_price`、`fee`、`filled_at`、`created_at`、`leverage`。
- **`trade_actions`**：`id`、`session_id`、`cycle_id`、`action`、`side`、`price`、`pnl`、`fee`、`amount`、`entry_price`、`trigger_reason`、`created_at`。
- **`v_alert_lifecycle` 列**：`session_id`、`alert_id`、`registered_at`、`target_price`、`register_reasoning`、`triggered_at`、`triggered_price`、`cancelled_at`、`cancel_reasoning`、`final_status`("active"/"triggered"/"cancelled")。注册 action=`add_price_level_alert`（`views.py:104`）。
- **`state_snapshot` JSON**：`{position: {symbol,side,contracts,entry_price,unrealized_pnl,leverage,liquidation_price,pnl_pct_of_notional}|null, balance: {total_usdt,free_usdt,used_usdt}|null, market:{...}|null, pending_orders:[...], active_alerts:[...], _errors:[...], _cycle_id}`。**balance 可能为 None**（best-effort 失败）。
- **MetricsService**：`MetricsService(engine, session_id, initial_balance).compute(current_position="none")` → `PerformanceMetrics`（字段：`total_return_pct`、`net_pnl`、`net_win_rate`、`max_drawdown_pct`、`net_profit_factor`、`total_trades`、`net_winning_trades`、`net_losing_trades`、`total_fees`、`current_position` 等）。`__init__` 默认 `initial_balance=10000.0` ≠ `Session.initial_balance` 默认 100.0 → **必须显式传会话真实值**。
- **测试 fixture**（`tests/conftest.py`）：`engine`（内存 init_db）；`db_engine`(tmp_path 文件 init_db)。两者都跑 init_db Path 3（create_all + views + stamp head）。

---

## File Structure

- Create `src/webui/__init__.py` — 空包标记
- Create `src/webui/db.py` — 只读 engine 工厂（`make_readonly_engine` / `readonly_session`）
- Create `src/webui/schemas.py` — pydantic 响应模型（API 契约）
- Create `src/webui/queries.py` — 只读查询函数（核心逻辑）
- Create `src/webui/app.py` — FastAPI app + 路由 + 静态挂载
- Create `src/webui/__main__.py` — `python -m src.webui` uvicorn 启动
- Create `tests/test_webui_db.py` / `test_webui_queries.py` / `test_webui_api.py`
- Modify `pyproject.toml` — 加 `[webui]` 可选依赖 extra

---

## Task 0: 依赖 + 包骨架

**Files:**
- Modify: `pyproject.toml`（`[project.optional-dependencies]`，line ~21）
- Create: `src/webui/__init__.py`

- [ ] **Step 1: 加 webui extra + TestClient 依赖**

在 `pyproject.toml` 的 `[project.optional-dependencies]` 下加：

```toml
webui = ["fastapi>=0.110", "uvicorn[standard]>=0.27"]
```

（`httpx`（FastAPI `TestClient` 依赖）已在核心 `dependencies` 中，无需另加。）

- [ ] **Step 2: 装依赖**

Run: `uv pip install -e ".[webui,dev]"`
Expected: fastapi / uvicorn / httpx 安装成功。

- [ ] **Step 3: 建空包**

Create `src/webui/__init__.py`（一行 docstring `"""TradeBot WebUI — 只读观察台后端 (Phase 1a)。"""`）。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/webui/__init__.py
git commit -m "chore(webui): 加 webui 可选依赖 extra + 包骨架"
```

---

## Task 1: schemas.py — pydantic API 契约

**Files:**
- Create: `src/webui/schemas.py`
- Test: `tests/test_webui_api.py`(末尾 schema smoke，先建文件)

- [ ] **Step 1: 写契约模型**

Create `src/webui/schemas.py`：

```python
"""WebUI JSON API 响应契约（pydantic v2）。前端 types.ts 由本模块的 OpenAPI 自动生成。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, BaseModel


def _ensure_utc(v: datetime) -> datetime:
    """所有出站 datetime 归一化为 aware UTC。ORM 在 SQLite 读回 naive（无 tz）、pydantic
    序列化 naive 无 `Z`、aware 有 `Z`；混用会让前端 `new Date()` 对无 Z 串按本地时区解析、
    +0800 用户错位 8h。统一补 UTC → 全部带 Z、前端全按 UTC 解析。"""
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]


class SessionSummary(BaseModel):
    id: str
    name: str
    symbol: str
    status: str               # active / paused（原始字段，非 liveness 断言）
    created_at: UtcDatetime
    last_active_at: UtcDatetime | None
    cycle_count: int
    total_return_pct: float


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


class CycleRow(BaseModel):
    id: int                   # int PK — 详情跳转/游标用这个
    cycle_label: str          # agent_cycles.cycle_id 字符串，仅显示
    triggered_by: str
    created_at: UtcDatetime
    decision_head: str | None # decision 首段（截断）
    tokens_consumed: int
    wall_time_ms: int | None
    execution_status: str


class ToolCallRow(BaseModel):
    tool_name: str
    status: str
    duration_ms: int
    error_type: str | None
    args: dict | str | None   # 解析后的 JSON；截断 outlier 行解析失败时回退原始 str


class CycleDetail(BaseModel):
    id: int
    cycle_label: str
    triggered_by: str
    created_at: UtcDatetime
    reasoning: str | None
    decision: str | None
    trigger_context: dict | None
    state_snapshot: dict | None
    injected_events: dict | list | None
    tool_calls: list[ToolCallRow]
    tokens_consumed: int
    input_tokens: int | None
    output_tokens: int | None
    cache_hit_rate: float | None
    wall_time_ms: int | None
    llm_call_ms: int | None
    model_id: str | None


class EquityPoint(BaseModel):
    at: UtcDatetime
    equity: float             # 账户盯市净值 state_snapshot.balance.total_usdt


class TradeRow(BaseModel):
    at: UtcDatetime
    action: str
    side: str | None
    price: float | None
    amount: float | None
    pnl: float | None
    fee: float | None


class Performance(BaseModel):
    initial_balance: float
    current_position: str
    total_return_pct: float
    net_pnl: float
    net_win_rate: float
    max_drawdown_pct: float
    net_profit_factor: float | None
    total_trades: int
    net_winning_trades: int
    net_losing_trades: int
    total_fees: float
    equity_curve: list[EquityPoint]    # 盯市，每 cycle
    trades: list[TradeRow]


class PositionInfo(BaseModel):
    symbol: str
    side: str
    contracts: float
    entry_price: float
    leverage: int


class OrderInfo(BaseModel):
    order_id: str
    side: str
    order_type: str
    amount: float
    trigger_price: float | None


class AlertInfo(BaseModel):
    alert_id: str
    target_price: float | None
    registered_at: UtcDatetime
    register_reasoning: str | None


class LiveStatus(BaseModel):
    status: str                       # 会话状态字段（active/paused），非"运行中"断言
    last_active_at: UtcDatetime | None   # 原始戳——让陈旧的 active 自证（spec §5.2）
    position: PositionInfo | None
    open_orders: list[OrderInfo]
    active_alerts: list[AlertInfo]
```

- [ ] **Step 2: schema 序列化 smoke 测试**

Create `tests/test_webui_api.py`（先放一个 import smoke，后续 Task 8 扩展）：

```python
def test_schemas_importable():
    from src.webui import schemas
    s = schemas.LiveStatus(status="active", last_active_at=None, position=None,
                           open_orders=[], active_alerts=[])
    assert s.model_dump()["status"] == "active"
```

- [ ] **Step 3: Run**

Run: `pytest tests/test_webui_api.py::test_schemas_importable -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/webui/schemas.py tests/test_webui_api.py
git commit -m "feat(webui): JSON API pydantic 契约 schemas"
```

---

## Task 2: db.py — 只读 engine 工厂

**Files:**
- Create: `src/webui/db.py`
- Test: `tests/test_webui_db.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_webui_db.py`：

```python
import pytest
from sqlalchemy import text

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel
from src.webui.db import make_readonly_engine


@pytest.mark.asyncio
async def test_readonly_engine_reads_committed_data(tmp_path):
    db_file = tmp_path / "t.db"
    wengine = await init_db(f"sqlite+aiosqlite:///{db_file}")
    async with get_session(wengine) as s:
        s.add(SessionModel(id="sess-1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0))
        await s.commit()

    ro = make_readonly_engine(str(db_file))
    async with ro.connect() as conn:
        row = (await conn.execute(text("SELECT name FROM sessions WHERE id='sess-1'"))).first()
        assert row[0] == "n1"


@pytest.mark.asyncio
async def test_readonly_engine_rejects_write(tmp_path):
    db_file = tmp_path / "t2.db"
    await init_db(f"sqlite+aiosqlite:///{db_file}")
    ro = make_readonly_engine(str(db_file))
    with pytest.raises(Exception):
        async with ro.connect() as conn:
            await conn.execute(text("INSERT INTO sessions(id,name,symbol,initial_balance,status) "
                                    "VALUES('x','x','BTC',1,'active')"))
            await conn.commit()
```

- [ ] **Step 2: Run（应失败 — 模块不存在）**

Run: `pytest tests/test_webui_db.py -v`
Expected: FAIL（`ModuleNotFoundError: src.webui.db`）

- [ ] **Step 3: 实现 db.py**

Create `src/webui/db.py`：

```python
"""只读 SQLite 连接（mode=ro）。spike 实测可读 live WAL 库的未 checkpoint 帧；
禁用 immutable（会返回陈旧数据）。见 spec §3。"""
from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_readonly_engine(db_path: str) -> AsyncEngine:
    """指向 db_path 的只读 async engine。mode=ro + busy_timeout + query_only。

    db_path: SQLite 文件绝对/相对路径（非 URL）。不调 init_db、不跑 migration。
    """
    abspath = os.path.abspath(db_path)
    url = f"sqlite+aiosqlite:///file:{abspath}?mode=ro"
    engine = create_async_engine(url, echo=False, connect_args={"uri": True})

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA busy_timeout=3000")
        cur.execute("PRAGMA query_only=ON")
        cur.close()

    return engine


@asynccontextmanager
async def readonly_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
```

- [ ] **Step 4: Run（应通过）**

Run: `pytest tests/test_webui_db.py -v`
Expected: PASS（读到 "n1"；写被拒）

- [ ] **Step 5: Commit**

```bash
git add src/webui/db.py tests/test_webui_db.py
git commit -m "feat(webui): mode=ro 只读 engine 工厂（busy_timeout + query_only）"
```

---

## Task 3: queries.get_cycles — 决策时间线 feed

**Files:**
- Create: `src/webui/queries.py`
- Test: `tests/test_webui_queries.py`

> 所有 queries 函数签名 `async def fn(engine: AsyncEngine, ...)`，用普通 `get_session` 读（单元测试传内存 seeded engine；生产由 app 传只读 engine）。

- [ ] **Step 1: 共享测试 helper + 失败测试**

Create `tests/test_webui_queries.py`：

```python
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
                     decision="line1\nline2", created_at=None, **kw):
    async with get_session(engine) as s:
        c = AgentCycle(session_id=sid, cycle_id=cycle_id, triggered_by=triggered_by,
                       decision=decision, tokens_consumed=kw.get("tokens", 100),
                       wall_time_ms=kw.get("wall", 5000), execution_status="ok",
                       created_at=created_at or datetime.now(UTC),
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
    assert rows[0].decision_head and "line1" in rows[0].decision_head
```

- [ ] **Step 2: Run（失败 — queries 不存在）**

Run: `pytest tests/test_webui_queries.py::test_get_cycles_orders_desc_and_paginates -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 get_cycles**

Create `src/webui/queries.py`（首函数）：

```python
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
```

- [ ] **Step 4: Run（通过）**

Run: `pytest tests/test_webui_queries.py::test_get_cycles_orders_desc_and_paginates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): get_cycles 决策时间线 feed（id DESC keyset + before/after）"
```

---

## Task 4: queries.get_cycle_detail — 单 cycle 完整细节

**Files:**
- Modify: `src/webui/queries.py`
- Test: `tests/test_webui_queries.py`

- [ ] **Step 1: 失败测试**

追加到 `tests/test_webui_queries.py`：

```python
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
```

- [ ] **Step 2: Run（失败）**

Run: `pytest tests/test_webui_queries.py -k get_cycle_detail -v`
Expected: FAIL

- [ ] **Step 3: 实现**

追加到 `src/webui/queries.py`：

```python
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
```

- [ ] **Step 4: Run（通过）**

Run: `pytest tests/test_webui_queries.py -k get_cycle_detail -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): get_cycle_detail（cycle + tool_calls 1:N 子列表 + JSON 解析）"
```

---

## Task 5: queries.get_live_status — 实时状态卡（status + last_active_at + 持仓/挂单/告警）

**Files:**
- Modify: `src/webui/queries.py`
- Test: `tests/test_webui_queries.py`

> v1 精简：只返回 `status` + `last_active_at` 原始值 + 持仓/挂单/活跃告警，**不重构唤醒/不派生 liveness**（spec §5.2）。

- [ ] **Step 1: 失败测试**

追加：

```python
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
```

- [ ] **Step 2: Run（失败）**

Run: `pytest tests/test_webui_queries.py -k get_live_status -v`
Expected: FAIL

- [ ] **Step 3: 实现**

追加到 `src/webui/queries.py`：

```python
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
```

- [ ] **Step 4: Run（通过）**

Run: `pytest tests/test_webui_queries.py -k get_live_status -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): get_live_status（持仓/挂单/活跃告警 + status/last_active_at）"
```

---

## Task 6: queries.get_performance — 表现概览（指标 + 净值曲线 + 成交）

**Files:**
- Modify: `src/webui/queries.py`
- Test: `tests/test_webui_queries.py`

- [ ] **Step 1: 失败测试**

追加：

```python
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
```

- [ ] **Step 2: Run（失败）**

Run: `pytest tests/test_webui_queries.py -k get_performance -v`
Expected: FAIL

- [ ] **Step 3: 实现**

追加到 `src/webui/queries.py`：

```python
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
```

- [ ] **Step 4: Run（通过）**

Run: `pytest tests/test_webui_queries.py -k get_performance -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): get_performance（MetricsService + 盯市净值曲线跳 None + 成交列表）"
```

---

## Task 7: queries.list_sessions / get_session_detail — 会话列表 + 元信息

**Files:**
- Modify: `src/webui/queries.py`
- Test: `tests/test_webui_queries.py`

- [ ] **Step 1: 失败测试**

追加：

```python
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
```

- [ ] **Step 2: Run（失败）**

Run: `pytest tests/test_webui_queries.py -k "list_sessions or session_detail" -v`
Expected: FAIL

- [ ] **Step 3: 实现**

追加到 `src/webui/queries.py`：

```python
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
```

> 注：`list_sessions` 对每会话调 `MetricsService.compute()` 是 N+1（spec §4 已点明，localhost 小 N 可接受，不在 v1 优化）。

- [ ] **Step 4: Run（通过）**

Run: `pytest tests/test_webui_queries.py -k "list_sessions or session_detail" -v`
Expected: PASS

- [ ] **Step 5: 全 queries 回归 + Commit**

Run: `pytest tests/test_webui_queries.py -v`
Expected: 全 PASS

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): list_sessions + get_session_detail（含 N+1 注记）"
```

---

## Task 8: app.py + __main__.py — FastAPI 路由 + 启动

**Files:**
- Create: `src/webui/app.py`、`src/webui/__main__.py`
- Test: `tests/test_webui_api.py`

- [ ] **Step 1: 失败测试（TestClient 打全部端点）**

追加到 `tests/test_webui_api.py`：

```python
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from src.storage.database import get_session
from src.storage.models import Session as SessionModel, AgentCycle

UTC = timezone.utc


@pytest.fixture
async def seeded(engine):
    la = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id="s1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, last_active_at=la))
        s.add(AgentCycle(session_id="s1", cycle_id="c1", triggered_by="scheduled",
                         decision="d1", tokens_consumed=100, execution_status="ok",
                         state_snapshot='{"balance":{"total_usdt":10000.0}}', created_at=la))
        await s.commit()
    return engine


def _client(engine):
    from src.webui.app import create_app, get_engine
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    return TestClient(app)


@pytest.mark.asyncio
async def test_api_endpoints(seeded):
    c = _client(seeded)
    assert c.get("/api/sessions").status_code == 200
    assert c.get("/api/sessions").json()[0]["id"] == "s1"
    assert c.get("/api/sessions/s1").json()["scheduler_interval_min"] == 15
    cyc = c.get("/api/sessions/s1/cycles").json()
    assert cyc[0]["cycle_label"] == "c1"
    pk = cyc[0]["id"]
    assert c.get(f"/api/cycles/{pk}").json()["decision"] == "d1"
    assert c.get("/api/sessions/s1/performance").json()["initial_balance"] == 10000.0
    live = c.get("/api/sessions/s1/live").json()
    assert live["status"] == "active"
    assert live["last_active_at"].endswith("Z")          # 出站时间戳带 Z（UTC 归一化）
    assert c.get("/api/cycles/999999").status_code == 404
    assert c.get("/api/sessions/nope/performance").status_code == 404   # 缺失会话统一 404
    assert c.get("/api/sessions/nope/live").status_code == 404
```

- [ ] **Step 2: Run（失败）**

Run: `pytest tests/test_webui_api.py::test_api_endpoints -v`
Expected: FAIL（`src.webui.app` 不存在）

- [ ] **Step 3: 实现 app.py**

Create `src/webui/app.py`：

```python
"""FastAPI 只读观察台。薄 HTTP 层：解析参数 → 调 queries → 返回 schemas。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine

from src.webui import queries, schemas
from src.webui.db import make_readonly_engine

_DEFAULT_DB = "data/tradebot.db"


def get_engine(request: Request) -> AsyncEngine:    # 测试用 dependency_overrides[get_engine] 覆盖
    return request.app.state.engine


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="TradeBot WebUI", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.engine = make_readonly_engine(db_path or os.environ.get("TRADEBOT_DB", _DEFAULT_DB))

    @app.get("/api/sessions", response_model=list[schemas.SessionSummary])
    async def _sessions(eng: AsyncEngine = Depends(get_engine)):
        return await queries.list_sessions(eng)

    @app.get("/api/sessions/{sid}", response_model=schemas.SessionDetail)
    async def _session(sid: str, eng: AsyncEngine = Depends(get_engine)):
        d = await queries.get_session_detail(eng, sid)
        if d is None:
            raise HTTPException(404, "session not found")
        return d

    @app.get("/api/sessions/{sid}/cycles", response_model=list[schemas.CycleRow])
    async def _cycles(sid: str, limit: int = 50, before_id: int | None = None,
                      after_id: int | None = None, eng: AsyncEngine = Depends(get_engine)):
        return await queries.get_cycles(eng, sid, limit=min(limit, 200),
                                        before_id=before_id, after_id=after_id)

    @app.get("/api/cycles/{pk}", response_model=schemas.CycleDetail)
    async def _cycle(pk: int, eng: AsyncEngine = Depends(get_engine)):
        d = await queries.get_cycle_detail(eng, pk)
        if d is None:
            raise HTTPException(404, "cycle not found")
        return d

    @app.get("/api/sessions/{sid}/performance", response_model=schemas.Performance)
    async def _perf(sid: str, eng: AsyncEngine = Depends(get_engine)):
        p = await queries.get_performance(eng, sid)
        if p is None:
            raise HTTPException(404, "session not found")
        return p

    @app.get("/api/sessions/{sid}/live", response_model=schemas.LiveStatus)
    async def _live(sid: str, eng: AsyncEngine = Depends(get_engine)):
        ls = await queries.get_live_status(eng, sid)
        if ls is None:
            raise HTTPException(404, "session not found")
        return ls

    # 前端静态资源（Phase 1b 产出 frontend/dist）；存在才挂，避免开发期报错
    dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")

    return app


app = create_app()
```

> 注：生产 engine 存 `app.state.engine`，`get_engine(request)` 从中读；测试 `_client` 用 `app.dependency_overrides[get_engine] = lambda: engine` 整体替换该依赖（覆盖时无需 request 形参）。

- [ ] **Step 4: 实现 __main__.py**

Create `src/webui/__main__.py`：

```python
"""python -m src.webui — 本机启动观察台。"""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("src.webui.app:app", host="127.0.0.1",
                port=int(os.environ.get("TRADEBOT_WEBUI_PORT", "8000")), reload=False)
```

- [ ] **Step 5: Run（通过）**

Run: `pytest tests/test_webui_api.py -v`
Expected: PASS（全部端点 + 404）

- [ ] **Step 6: 冒烟启动（手动验收，可选）**

Run: `TRADEBOT_DB=data/tradebot.db .venv/bin/python -m src.webui` 然后浏览器开 `http://127.0.0.1:8000/api/docs` 看 OpenAPI，`curl localhost:8000/api/sessions` 应返回真实 sim 会话列表。Ctrl+C 停。

- [ ] **Step 7: Commit**

```bash
git add src/webui/app.py src/webui/__main__.py tests/test_webui_api.py
git commit -m "feat(webui): FastAPI 只读端点 + python -m src.webui 启动 + 静态挂载"
```

---

## Task 9: 全量回归 + README

**Files:**
- Create: `src/webui/README.md`

- [ ] **Step 1: 全量测试不回归**

Run: `pytest -q`
Expected: 既有 2300+ 测试 + 新 webui 测试全 PASS（webui 纯新增，不应动既有）。

- [ ] **Step 2: 写 README**

Create `src/webui/README.md`：开发（`uv pip install -e ".[webui]"` + `python -m src.webui`）、端点清单、只读约束（mode=ro，不调 init_db）、`TRADEBOT_DB`/`TRADEBOT_WEBUI_PORT` 环境变量、状态卡只显示 status+last_active_at（不重构 liveness，spec §5.2）、Phase 1b 前端待接（types 由 `/api/openapi.json` 经 openapi-typescript 生成）。

- [ ] **Step 3: Commit**

```bash
git add src/webui/README.md
git commit -m "docs(webui): Phase 1a 后端 README"
```

---

## 完成定义（Phase 1a）

- `pytest tests/test_webui_*.py -v` 全绿；`pytest -q` 无既有回归。
- `python -m src.webui` 能启动，`/api/docs` 出 OpenAPI，6 个端点对真实 `data/tradebot.db` 返回正确数据。
- 零改 schema、零动现有 `src/` 模块（仅新增 `src/webui/` + `pyproject.toml` 一行 extra）。

## Phase 1b（前端 Vue SPA）— 后续独立计划

1a 落地、API 经 `/api/openapi.json` 可用后，另起 `2026-XX-XX-webui-dashboard-phase1b-frontend.md`：`frontend/` Vite+Vue3+TS 脚手架、`openapi-typescript` 生成 `types.ts`、SessionList/SessionDetail/DecisionTimeline/PerformanceOverview/LiveStatusCard 组件、`usePolling` live 轮询（5s，`status=='active'` 才轮询，用 `after_id` 增量）、lightweight-charts 净值曲线、`vite build → frontend/dist` 由 app.py 挂载。状态卡只显示 status+last_active_at（spec §5.2）。
