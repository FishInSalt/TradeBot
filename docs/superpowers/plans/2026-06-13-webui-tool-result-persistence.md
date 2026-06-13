# 工具调用结果持久化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `tool_calls` 表加 `result` 列 + 在执行层捕获工具返回值，点亮 Phase 1b 已预埋的 `ToolCallRow.result` seam（纯后端，零改前端）。

**Architecture:** 三段式按依赖顺序——① schema（model 列 + Alembic 迁移，迁移用 SQLite view 重建 pattern）→ ② recorder 捕获（`wrap_tool_execute` finally 块序列化 result，30000 char cap + 截断标记）→ ③ WebUI query 接线（`get_cycle_detail` 直传 raw str）。设计依据见 `docs/superpowers/specs/2026-06-13-webui-tool-result-persistence-design.md`。

**Tech Stack:** SQLAlchemy 2.0 ORM + Alembic（SQLite，batch_alter_table）+ pydantic-ai capability（ToolCallRecorder）+ pytest/pytest-asyncio。

---

## File Structure

| 文件 | 职责 | 任务 |
|------|------|------|
| `src/storage/models.py` | `ToolCall` 加 `result` 列（Text, nullable） | T1 |
| `alembic/versions/<hash>_tool_call_result.py` | 新迁移：plain add_column upgrade + view-dance downgrade | T1 |
| `tests/test_alembic_roundtrip_phase1.py` | head 有 result 列 + downgrade 删列保 view 断言 | T1 |
| `src/services/tool_call_recorder.py` | `wrap_tool_execute` finally 捕获 result（截断 30000） | T2 |
| `tests/test_tool_call_recorder_result.py` | 捕获 / 截断 / error→NULL / biz_error→捕获（新文件） | T2 |
| `src/webui/queries.py` | `get_cycle_detail` 构造 `ToolCallRow` 补 `result=t.result` | T3 |
| `tests/test_webui_queries.py` | 更新预留测试 + 加 result 捕获正面用例 | T3 |
| `tests/test_webui_api.py` | cycle-detail 响应含 result 字段 | T3 |

依赖：T1 必须先（T2 的 init_db create_all 与 T3 的 `ToolCall(result=...)` 都要列存在）→ T2 → T3。

---

## Task 1: Schema — model 列 + Alembic 迁移

**Files:**
- Modify: `src/storage/models.py:218`（`ToolCall.args` 行之后加 `result`）
- Create: `alembic/versions/<hash>_tool_call_result.py`（用 `alembic revision` 生成）
- Test: `tests/test_alembic_roundtrip_phase1.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_alembic_roundtrip_phase1.py` 末尾追加：

```python
async def test_head_has_tool_calls_result(head_db):
    """init_db Path 3 后 tool_calls 含 result 列。"""
    db, _ = head_db
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    assert "result" in cols


async def test_downgrade_drops_result_keeps_views(head_db):
    """downgrade -1（head → 7244c7b7185d）删 result 列、且 3 个 view 仍在
    （downgrade 的 DROP VIEW → drop_column → 重建 view 舞蹈正确性）。"""
    db, env = head_db
    subprocess.run(["alembic", "downgrade", "-1"], check=True, env=env, capture_output=True)
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    assert "result" not in cols
    views = _query_views(db)
    assert EXPECTED_VIEWS.issubset(views), f"views not restored by downgrade: {EXPECTED_VIEWS - views}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_alembic_roundtrip_phase1.py::test_head_has_tool_calls_result -v`
Expected: FAIL — `assert "result" in cols`（列尚不存在）。

- [ ] **Step 3: 加 model 列**

`src/storage/models.py`，在 `ToolCall.args`（第 218 行）之后加一行：

```python
    args: Mapped[str | None] = mapped_column(Text, nullable=True)                  # Iter 3: §G2 — JSON dict of tool args, 4000 char cap, reasoning key stripped
    # 工具返回值（观察期可观测性）；str 文本（非 JSON），30000 char cap + 截断标记，见 tool_call_recorder
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: 生成并填写迁移**

Run: `alembic revision -m "tool_call_result"`
这会生成 `alembic/versions/<hash>_tool_call_result.py`，自动填 `down_revision = "7244c7b7185d"`（当前 head）。**生成后核对 `down_revision` 确为 `"7244c7b7185d"`**。

把该文件的 import 区与 upgrade/downgrade 替换为：

```python
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from src.storage.views import ALL_VIEW_NAMES, ALL_VIEW_SQLS


# revision / down_revision / branch_labels / depends_on 由 alembic revision 自动生成，勿改


def upgrade() -> None:
    # plain ADD COLUMN：SQLite 原生、不重建表、不触碰 view，无需 drop view
    op.add_column("tool_calls", sa.Column("result", sa.Text(), nullable=True))


def downgrade() -> None:
    # tool_calls 被 v_cycle_metrics / v_alert_lifecycle 引用；batch drop_column 的
    # temp-table rename 会重解析全部 view、rename 瞬间表不存在即炸。故先 DROP VIEW。
    # 重建用当前单源 ALL_VIEW_SQLS（无 view 引用 result，且 downgrade 落 7244c7b7185d
    # 处 injected_events 仍在 → 当前 SQL 全有效），不需 _PRE_ITER 冻结快照。
    for view in reversed(ALL_VIEW_NAMES):
        op.execute(f"DROP VIEW IF EXISTS {view}")
    with op.batch_alter_table("tool_calls", schema=None) as b:
        b.drop_column("result")
    for sql in ALL_VIEW_SQLS:
        op.execute(sql)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_alembic_roundtrip_phase1.py -v`
Expected: 全 PASS——新 2 条 + 既有 `test_upgrade_idempotent_after_downgrade`（直接跑新迁移 down+up）+ `test_downgrade_drops_phase1_columns` / `test_downgrade_drops_views`（`phase1_head_db` 经新迁移 downgrade 链）全绿。

- [ ] **Step 6: Commit**

```bash
git add src/storage/models.py alembic/versions/ tests/test_alembic_roundtrip_phase1.py
git commit -m "feat(storage): tool_calls.result 列 + Alembic 迁移（view 重建 pattern）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Recorder 捕获 result

**Files:**
- Modify: `src/services/tool_call_recorder.py`（`wrap_tool_execute`）
- Test: `tests/test_tool_call_recorder_result.py`（新建）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_tool_call_recorder_result.py`：

```python
"""tool_call_recorder.result field write tests.

Spec: docs/superpowers/specs/2026-06-13-webui-tool-result-persistence-design.md §捕获语义。
result 捕获与 args 捕获同构（见 test_tool_call_recorder_args.py）。
"""
from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import ToolCall

_TRUNC_MARK = "\n…[truncated]"


@pytest.fixture
async def engine(tmp_path: Path):
    eng = await init_db(f"sqlite+aiosqlite:///{tmp_path}/recorder_result.db")
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def deps(engine):
    d = MagicMock()
    d.session_id = "test-session"
    d.cycle_id = "test-cycle"
    d.db_engine = engine
    return d


async def _record_and_get(engine, deps, handler):
    """Run recorder with given handler; return last-row (result, status).

    Swallows handler exceptions (error path re-raises) so the recorded row
    can still be inspected.
    """
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    call = MagicMock()
    call.tool_name = "test_tool"
    call.args_as_dict = MagicMock(return_value={})
    ctx = MagicMock()
    ctx.deps = deps
    with contextlib.suppress(Exception):
        await recorder.wrap_tool_execute(
            ctx, call=call, tool_def=MagicMock(), args=MagicMock(), handler=handler,
        )
    async with get_session(engine) as session:
        row = (await session.execute(
            select(ToolCall.result, ToolCall.status).order_by(ToolCall.id.desc()).limit(1)
        )).first()
    return row


@pytest.mark.asyncio
async def test_result_captured(engine, deps):
    row = await _record_and_get(engine, deps, AsyncMock(return_value="=== Ticker ===\nlast 63000"))
    assert row.result == "=== Ticker ===\nlast 63000"
    assert row.status == "ok"


@pytest.mark.asyncio
async def test_result_truncated_at_30000(engine, deps):
    row = await _record_and_get(engine, deps, AsyncMock(return_value="x" * 40000))
    assert row.result.startswith("x" * 30000)
    assert row.result.endswith(_TRUNC_MARK)
    assert len(row.result) == 30000 + len(_TRUNC_MARK)


@pytest.mark.asyncio
async def test_result_null_on_exception(engine, deps):
    async def boom(args):
        raise ValueError("nope")
    row = await _record_and_get(engine, deps, boom)
    assert row.result is None
    assert row.status == "error"


@pytest.mark.asyncio
async def test_result_captured_on_biz_error(engine, deps):
    from src.services.tool_call_recorder import note_biz_error

    async def biz(args):
        note_biz_error("alert_not_found")     # 合法 BIZ_ERROR_TYPE（recorder.py:60）
        return "alert a1 not found"
    row = await _record_and_get(engine, deps, biz)
    assert row.result == "alert a1 not found"
    assert row.status == "biz_error"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_tool_call_recorder_result.py -v`
Expected: FAIL — `test_result_captured` 等 `row.result` 为 None（recorder 尚未捕获）。

- [ ] **Step 3: 实现捕获**

`src/services/tool_call_recorder.py`，改 `wrap_tool_execute`：

**(a)** try 前初始化 `result`（异常路径 result 不绑定，finally 需可达）。第 106-107 行：

```python
        status, error_type = "ok", None
        skip_record = False
        result = None   # try 前初始化：异常路径不绑定，finally 序列化需可达
```

**(b)** finally 块内，args 截断块之后（第 145 行 `args_serialized = args_serialized[:4000]` 之后）、`insert_start` 之前，加：

```python
                    # result 捕获（spec §捕获语义）：str 直存，30000 char cap + 截断标记。
                    # 不进 agent context、不耗 token；cap 仅防病态巨行。error 路径 result=None。
                    result_serialized = None
                    if result is not None:
                        result_serialized = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                        if len(result_serialized) > 30000:
                            result_serialized = result_serialized[:30000] + "\n…[truncated]"
```

**(c)** `ToolCall(...)` 构造器（第 149-157 行）的 `args=args_serialized,` 之后加：

```python
                            args=args_serialized,            # ← 新增 (Iter 3 §G2)
                            result=result_serialized,        # ← 新增：工具返回值（观察期）
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_tool_call_recorder_result.py tests/test_tool_call_recorder_args.py -v`
Expected: 全 PASS（新 result 测试 + 既有 args 测试不回归）。

- [ ] **Step 5: Commit**

```bash
git add src/services/tool_call_recorder.py tests/test_tool_call_recorder_result.py
git commit -m "feat(recorder): 捕获工具返回值到 tool_calls.result（30000 cap）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: WebUI query 接线

**Files:**
- Modify: `src/webui/queries.py:84`（`get_cycle_detail` 构造 `ToolCallRow`）
- Test: `tests/test_webui_queries.py`（更新 1 条 + 加 1 条）、`tests/test_webui_api.py`（加 1 条）

- [ ] **Step 1: 写/改失败测试**

**(a)** `tests/test_webui_queries.py`：把既有 `test_tool_call_result_is_reserved_none`（第 110 行）整体替换为以下两个测试（旧测试 docstring「DB 无 result 列」已过时；拆成「未捕获→None」+「捕获→直传」）：

```python
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
```

**(b)** `tests/test_webui_api.py` 末尾追加（cycle-detail 响应含 result 字段）：

```python
@pytest.mark.asyncio
async def test_api_cycle_detail_includes_tool_result(engine):
    from src.storage.models import ToolCall as TC
    la = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id="s1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, last_active_at=la))
        s.add(AgentCycle(session_id="s1", cycle_id="c1", triggered_by="scheduled",
                         decision="d1", tokens_consumed=100, execution_status="ok",
                         trigger_context=json.dumps([{"type": "scheduled_tick"}]),
                         state_snapshot='{"balance":{"total_usdt":10000.0}}', created_at=la))
        s.add(TC(session_id="s1", cycle_id="c1", tool_name="get_market_data",
                 status="ok", duration_ms=8, args=None, result="=== Ticker ===\nlast 63000"))
        await s.commit()
    c = _client(engine)
    cyc = c.get("/api/sessions/s1/cycles").json()
    cd = c.get(f"/api/cycles/{cyc[0]['id']}")
    assert cd.status_code == 200
    tcs = cd.json()["tool_calls"]
    assert tcs[0]["result"] == "=== Ticker ===\nlast 63000"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_webui_queries.py::test_tool_call_result_passthrough_raw_str tests/test_webui_api.py::test_api_cycle_detail_includes_tool_result -v`
Expected: FAIL — `result` 为 None（query 尚未传 `result=t.result`）。

- [ ] **Step 3: 实现 query 接线**

`src/webui/queries.py`，`get_cycle_detail` 的 `ToolCallRow` 构造（第 84-85 行），补 `result=t.result`：

```python
        tool_calls=[
            schemas.ToolCallRow(tool_name=t.tool_name, status=t.status, duration_ms=t.duration_ms,
                                error_type=t.error_type, args=_loads(t.args),
                                result=t.result) for t in tcs    # raw str 直传，不走 _loads（截断行永非合法 JSON）
        ],
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_webui_queries.py tests/test_webui_api.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Commit**

```bash
git add src/webui/queries.py tests/test_webui_queries.py tests/test_webui_api.py
git commit -m "feat(webui): get_cycle_detail 接线 tool_calls.result（点亮 seam）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 收尾验证（全部 task 后）

- [ ] 全量跑后端测试确认零回归：`pytest tests/ -q`
- [ ] 确认前端无需改动：`CycleDetailPanel.vue:35-36` 的 result 列在 `result != null` 时走 JsonBlock，已就绪（spec §目标）。
- [ ] 走 PR（spec §落地形态：三件套 + schema migration + 多文件）。
