# WebUI Cycle ReAct 时间线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 cycle 内的 ReAct 交错过程（唤醒上下文 → 思考 → 调工具 → 看结果 →〔中途注入〕→ 再思考 → 决策）持久化并在 WebUI 按真实时序回放，对标 CLI session log。

**Architecture:** 方案 A「骨架 + 指针」——新列 `agent_cycles.react_steps`（按 ModelResponse 顺序的骨架：每步 thinking + 该步发起的 tool_call_id 列表）+ `tool_calls.tool_call_id`（链接键）。工具遥测仍只在 `tool_calls` 一份；WebUI 按骨架顺序用 tool_call_id JOIN 取 args/result。中途注入靠 `injected_events[].after_tool_call_id` 锚定。`format_cycle_output`（CLI 渲染）与 `build_react_steps`（落库）共消费一个遍历提取器，杜绝双遍历漂移。

**Tech Stack:** Python 3.12 / SQLAlchemy 2 (async) / Alembic / pydantic v2 / pydantic-ai / FastAPI / Vue 3 SPA (naive-ui) / vitest / pytest

设计依据：`docs/superpowers/specs/2026-06-14-webui-cycle-react-timeline-design.md`（§ 引用均指该 spec）。

---

## 文件结构

后端：
- `src/storage/models.py` — `AgentCycle.react_steps`(Text null) + `ToolCall.tool_call_id`(String null)
- `alembic/versions/<gen>_react_steps_tool_call_id.py`（新建）— 2 ADD COLUMN + downgrade 重建 view
- `src/services/tool_call_recorder.py` — insert 增 `tool_call_id`
- `src/services/midcycle_injector.py` — 取证 record 增 `after_tool_call_id`
- `src/cli/display.py` — 新增 `_walk_react_responses`(共享遍历) + `_first_thinking_content` + `build_react_steps`；重构 `_extract_reasoning_per_response` 与 `format_cycle_output` 主循环消费共享遍历（行为保持）
- `src/cli/app.py` — 新增 `_safe_build_react_steps`；happy-path insert 增 `react_steps`；两条 forensic insert 显式 `react_steps=None`
- `src/webui/schemas.py` — `CycleDetail` 增 `react_steps`/`user_prompt_snapshot`/`execution_status`；`ToolCallRow` 增 `tool_call_id`
- `src/webui/queries.py` — `get_cycle_detail` 传新字段

前端：
- `frontend/openapi.json` + `frontend/src/api/types.ts`（由后端 schema 重生成）
- `frontend/src/api/client.ts` — 导出新增 `ReactStep` 便捷类型（若需要）
- `frontend/src/components/ReactTimeline.vue`（新建）— 时间线主体
- `frontend/src/components/CycleDetailPanel.vue` — 重构为 chips + Context + 时间线 + 决策 + 扁平回退

测试：
- `tests/test_tool_call_recorder_tool_call_id.py`（新建）
- `tests/test_midcycle_injector.py`（追加 after_tool_call_id 用例）
- `tests/test_display_cycle.py`（追加 build_react_steps + 共享遍历一致性 + render fidelity 复跑）
- `tests/test_alembic_react_timeline.py`（新建，仿 net_pnl_metrics）
- `tests/test_webui_queries.py` / `tests/test_webui_api.py`（追加新字段断言）
- `frontend/test/ReactTimeline.spec.ts`（新建）
- `frontend/test/CycleDetailPanel.spec.ts`（重写）

---

## Task 1: DB 列 + Alembic 迁移

**Files:**
- Modify: `src/storage/models.py:90-127`（AgentCycle）、`src/storage/models.py:198-220`（ToolCall）
- Create: `alembic/versions/<gen>_react_steps_tool_call_id.py`
- Test: `tests/test_alembic_react_timeline.py`

- [ ] **Step 1: 给 models 加两列**

`src/storage/models.py` —— `AgentCycle` 类，在 `# === END Phase 1 ===`（:126）之后、`created_at`（:127）之前插入（react_steps 不属 Phase 1 块，放注释外更干净）：

```python
    # webui-cycle-react-timeline §4.1: ReAct 叙事骨架（JSON 数组 as Text；NULL = legacy/forensic 无骨架）。
    # 每元素对应一个 ModelResponse: {"thinking": str|None, "tools": [{"tool_call_id", "tool_name"}, ...]}。
    # 工具遥测仍只在 tool_calls 一份，骨架按 tool_call_id JOIN 取 args/result（§3）。
    react_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
```

`ToolCall` 类，在 `result`（:220）之后插入：

```python
    # webui-cycle-react-timeline §4.2: pydantic-ai ToolCallPart.tool_call_id；react_steps 指针落点。
    # nullable: 历史行为 NULL（不回填，§7）。同名工具一轮多调靠此唯一区分（§10）。
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

- [ ] **Step 2: 生成迁移骨架**

```bash
alembic revision -m "react_steps and tool_call_id"
```
Expected: 生成 `alembic/versions/<hex>_react_steps_and_tool_call_id.py`，其 `down_revision` 被 alembic 自动填为当前 head。

- [ ] **Step 3: 验证 down_revision == head（防 multiple heads）**

```bash
alembic heads
grep "down_revision" alembic/versions/*react_steps_and_tool_call_id*.py
```
Expected: `alembic heads` 输出 `b43e33764d90 (head)`；迁移文件 `down_revision` 行为 `'b43e33764d90'`。若不一致，手改为 `'b43e33764d90'`（memory `feedback_spec_anchors_worktree_head` 第 3 条教训）。

- [ ] **Step 4: 写迁移 upgrade/downgrade 主体**

替换生成文件的 `upgrade()`/`downgrade()` 两个函数，并在文件顶部 import 区（`import sqlalchemy as sa` 之后）加入 view 常量 import。**保留** alembic 自动生成的 `revision`/`down_revision`/`branch_labels`/`depends_on` 四行不动：

```python
from src.storage.views import ALL_VIEW_NAMES, ALL_VIEW_SQLS


def upgrade() -> None:
    # plain ADD COLUMN：SQLite 原生、不重建表、不触碰 view，无需 drop view（§7）。
    op.add_column("agent_cycles", sa.Column("react_steps", sa.Text(), nullable=True))
    op.add_column("tool_calls", sa.Column("tool_call_id", sa.String(length=100), nullable=True))


def downgrade() -> None:
    # agent_cycles 与 tool_calls 均被 v_cycle_metrics 引用；batch drop_column 的 temp-table
    # rename 会重解析全部 view、rename 瞬间表不存在即炸。故先 DROP VIEW，drop 两列后用单源
    # ALL_VIEW_SQLS 重建（沿用 8c48305247c3 既有写法，§7）。
    for view in reversed(ALL_VIEW_NAMES):
        op.execute(f"DROP VIEW IF EXISTS {view}")
    with op.batch_alter_table("agent_cycles", schema=None) as b:
        b.drop_column("react_steps")
    with op.batch_alter_table("tool_calls", schema=None) as b:
        b.drop_column("tool_call_id")
    for sql in ALL_VIEW_SQLS:
        op.execute(sql)
```

- [ ] **Step 5: 写迁移测试**

Create `tests/test_alembic_react_timeline.py`（仿 `tests/test_alembic_net_pnl_metrics.py`）：

```python
"""webui-cycle-react-timeline migration test (仿 test_alembic_net_pnl_metrics.py)."""
from __future__ import annotations

import os
import subprocess
import sqlite3

import pytest

PRE_ITER_REV = "b43e33764d90"   # alembic head before this iter（实查，见 plan Task 1 Step 3）


@pytest.fixture
async def head_db(tmp_path):
    from src.storage.database import init_db
    db_path = tmp_path / "react_timeline.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    return str(db_path), env


@pytest.fixture
async def pre_iter_head_db(tmp_path):
    from src.storage.database import init_db
    db_path = tmp_path / "pre_iter_react.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    subprocess.run(["alembic", "downgrade", PRE_ITER_REV], check=True, env=env, capture_output=True)
    return str(db_path), env


async def test_head_has_react_steps_and_tool_call_id(head_db):
    db, _ = head_db
    conn = sqlite3.connect(db)
    ac_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    tc_cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    assert "react_steps" in ac_cols, f"react_steps missing; have {sorted(ac_cols)}"
    assert "tool_call_id" in tc_cols, f"tool_call_id missing; have {sorted(tc_cols)}"


async def test_upgrade_preserves_legacy_null(pre_iter_head_db):
    db, env = pre_iter_head_db
    conn = sqlite3.connect(db)
    ac_before = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    assert "react_steps" not in ac_before
    # legacy session + cycle at pre-iter schema
    conn.execute("""
        INSERT INTO sessions
        (id, name, symbol, initial_balance, status, created_at, updated_at,
         exchange_type, timeframe, scheduler_interval_min, approval_enabled, token_budget)
        VALUES ('legacy-test', 'legacy', 'BTC/USDT:USDT', 10000.0, 'active',
                '2026-01-01T00:00:00', '2026-01-01T00:00:00',
                'simulated', '15m', 15, 1, 500000)
    """)
    # raw sqlite3 INSERT 不套 ORM default：agent_cycles 的 NOT NULL 且无 server_default 列
    # 必须显式给值——tokens_consumed(models.py:110, default=0 仅 Python 端) 是其一，漏给会
    # IntegrityError。execution_status 有 server_default="ok" 故可省，此处仍显式给。
    conn.execute(
        "INSERT INTO agent_cycles (session_id, cycle_id, triggered_by, tokens_consumed, execution_status, created_at) "
        "VALUES ('legacy-test', 'c1', 'scheduled', 0, 'ok', '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env, capture_output=True)

    conn = sqlite3.connect(db)
    ac_after = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    assert "react_steps" in ac_after
    legacy = conn.execute(
        "SELECT react_steps FROM agent_cycles WHERE session_id='legacy-test'"
    ).fetchone()
    assert legacy == (None,), f"legacy row should preserve NULL (no backfill); got {legacy}"


async def test_downgrade_drops_columns_and_restores_view(head_db):
    db, env = head_db
    subprocess.run(["alembic", "downgrade", PRE_ITER_REV], check=True, env=env, capture_output=True)
    conn = sqlite3.connect(db)
    ac_cols = {row[1] for row in conn.execute("PRAGMA table_info(agent_cycles)")}
    tc_cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_calls)")}
    assert "react_steps" not in ac_cols, f"downgrade should drop react_steps; cols: {sorted(ac_cols)}"
    assert "tool_call_id" not in tc_cols, f"downgrade should drop tool_call_id; cols: {sorted(tc_cols)}"
    # v_cycle_metrics 必须在 downgrade 后仍可查（view 已重建）
    cnt = conn.execute("SELECT count(*) FROM v_cycle_metrics").fetchone()[0]
    assert cnt == 0  # 空库可查即证明 view 重建成功
```

- [ ] **Step 6: 跑测试**

Run: `pytest tests/test_alembic_react_timeline.py -v`
Expected: 3 passed（head 有两列、upgrade 不回填、downgrade 删列且 view 重建）。

- [ ] **Step 7: Commit**

```bash
git add src/storage/models.py alembic/versions/*react_steps_and_tool_call_id*.py tests/test_alembic_react_timeline.py
git commit -m "feat(storage): react_steps + tool_call_id 列 + 迁移"
```

---

## Task 2: recorder 写入 tool_call_id

**Files:**
- Modify: `src/services/tool_call_recorder.py:158-167`（`ToolCall(...)` insert）
- Test: `tests/test_tool_call_recorder_tool_call_id.py`（新建）

- [ ] **Step 1: 写失败测试**

Create `tests/test_tool_call_recorder_tool_call_id.py`（仿 `tests/test_tool_call_recorder_result.py`）：

```python
"""tool_call_recorder.tool_call_id field write test (仿 test_tool_call_recorder_result.py)."""
from __future__ import annotations

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import ToolCall


@pytest.fixture
async def engine(tmp_path: Path):
    eng = await init_db(f"sqlite+aiosqlite:///{tmp_path}/recorder_tcid.db")
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


@pytest.mark.asyncio
async def test_tool_call_id_recorded(engine, deps):
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    call = MagicMock()
    call.tool_name = "get_market_data"
    call.tool_call_id = "call_abc123"
    call.args_as_dict = MagicMock(return_value={})
    ctx = MagicMock()
    ctx.deps = deps
    with contextlib.suppress(Exception):
        await recorder.wrap_tool_execute(
            ctx, call=call, tool_def=MagicMock(), args=MagicMock(),
            handler=AsyncMock(return_value="ok"),
        )
    async with get_session(engine) as session:
        row = (await session.execute(
            select(ToolCall.tool_call_id).order_by(ToolCall.id.desc()).limit(1)
        )).first()
    assert row is not None, "no row written"
    assert row.tool_call_id == "call_abc123"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_tool_call_recorder_tool_call_id.py -v`
Expected: FAIL —— `row.tool_call_id` 为 None（recorder 尚未写该字段）。

- [ ] **Step 3: 给 insert 加字段**

`src/services/tool_call_recorder.py` —— `ToolCall(...)`（:158-167），在 `result=result_serialized,`（:166）之后加一行：

```python
                            result=result_serialized,        # ← 新增：工具返回值（观察期）
                            tool_call_id=call.tool_call_id,  # ← webui-react-timeline §5.1: react_steps 指针落点
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_tool_call_recorder_tool_call_id.py tests/test_tool_call_recorder_result.py tests/test_tool_call_recorder_args.py -v`
Expected: 全 PASS（新用例 + 既有 result/args 用例不回归）。

- [ ] **Step 5: Commit**

```bash
git add src/services/tool_call_recorder.py tests/test_tool_call_recorder_tool_call_id.py
git commit -m "feat(recorder): tool_calls 写入 tool_call_id"
```

---

## Task 3: injector 记录 after_tool_call_id

**Files:**
- Modify: `src/services/midcycle_injector.py:103-111`（取证 record dict）
- Test: `tests/test_midcycle_injector.py`（追加）

- [ ] **Step 1: 看现有 injector 测试，确认 fixture 风格**

Run: `grep -n "injected_events_log\|after_tool\|drain_pending_events_fn\|def test" tests/test_midcycle_injector.py | head -30`
Expected: 看到现有用例如何构造 `deps`（含 `drain_pending_events_fn` / `requeue_events_fn` / `injected_events_log` / `cycle_started_at`）与断言 `after_tool` 的方式，照其风格追加。

- [ ] **Step 2: 写失败测试**

在 `tests/test_midcycle_injector.py` 末尾追加（若现有用例已构造好 `deps` fixture / helper，复用之；下方为自包含版本，按文件实际 helper 调整 import）：

```python
@pytest.mark.asyncio
async def test_injected_record_has_after_tool_call_id(monkeypatch):
    """注入取证记录含 after_tool_call_id（= 注入发生时那次 call.tool_call_id，§5.2）。"""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    from src.services.midcycle_injector import MidCycleEventInjector
    import src.services.midcycle_injector as mod

    # 桩掉事件渲染/capture，聚焦 after_tool_call_id 字段
    async def _fake_render(deps, events, now):
        return "\n\n=== NEW EVENTS TRIGGERED (1 fill) ===\nblock"
    monkeypatch.setattr(mod, "_render_injection_block", _fake_render)
    monkeypatch.setattr(mod, "_capture_trigger_context", lambda cid, tt, ctx: {"type": tt})

    log: list = []
    deps = MagicMock()
    deps.drain_pending_events_fn = MagicMock(return_value=[("fill", {"px": 1})])
    deps.requeue_events_fn = MagicMock()
    deps.injected_events_log = log
    deps.cycle_id = "c1"
    deps.cycle_started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    call = MagicMock()
    call.tool_name = "get_position"
    call.tool_call_id = "call_xyz789"
    ctx = MagicMock()
    ctx.deps = deps

    async def handler(args):
        return "position: flat"

    injector = MidCycleEventInjector()
    out = await injector.wrap_tool_execute(
        ctx, call=call, tool_def=MagicMock(), args=MagicMock(), handler=handler,
    )
    assert out.endswith("block")            # 注入块已追加
    assert len(log) == 1
    assert log[0]["after_tool"] == "get_position"
    assert log[0]["after_tool_call_id"] == "call_xyz789"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_midcycle_injector.py::test_injected_record_has_after_tool_call_id -v`
Expected: FAIL —— `KeyError: 'after_tool_call_id'`（record dict 尚无该键）。

- [ ] **Step 4: 给 record dict 加字段**

`src/services/midcycle_injector.py` —— `records` 列表推导（:103-111），在 `"after_tool": call.tool_name,`（:107）之后加一行：

```python
                    "after_tool": call.tool_name,
                    "after_tool_call_id": call.tool_call_id,  # webui-react-timeline §5.2: 时间线锚点
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_midcycle_injector.py tests/test_midcycle_forensics.py -v`
Expected: 全 PASS（新用例 + 既有 injector/forensics 用例不回归）。

> 注：落库序列化无需改——app.py:742-745 只剥 `raw`、保留其余键，新键 `after_tool_call_id` 自动落库（§4.3）。

- [ ] **Step 6: Commit**

```bash
git add src/services/midcycle_injector.py tests/test_midcycle_injector.py
git commit -m "feat(injector): 取证记录增 after_tool_call_id"
```

---

## Task 4: 共享遍历提取器 + format_cycle_output 重构（行为保持）

**Files:**
- Modify: `src/cli/display.py:775-803`（`_extract_reasoning_per_response`）、`src/cli/display.py:1459-1476`（主循环）
- Test: `tests/test_display_cycle.py`（既有 render fidelity + drift guard 复跑）

- [ ] **Step 1: 加共享遍历提取器与 first-thinking helper**

`src/cli/display.py` —— 在 `_extract_reasoning_per_response`（:775）**之前**插入两个新函数：

```python
def _walk_react_responses(messages: list) -> list[tuple[list, list]]:
    """共享遍历（spec §5.3）：按序返回每个 ModelResponse 的 (ThinkingParts, ToolCallParts)。

    format_cycle_output 渲染与 build_react_steps 落库**同消费此函数**，杜绝双遍历漂移
    （pydantic-ai 升级改 parts 结构时，web 回放序与 CLI session log 交错序不会分裂）。
    两个消费者仅在 thinking 聚合上分叉：CLI 取首个 part、web 拼接全部。
    """
    out: list[tuple[list, list]] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            thinking_parts = [p for p in msg.parts if isinstance(p, ThinkingPart)]
            tool_calls = [p for p in msg.parts if isinstance(p, ToolCallPart)]
            out.append((thinking_parts, tool_calls))
    return out


def _first_thinking_content(thinking_parts: list) -> str | None:
    """渲染层 thinking SoT：每 Response 仅取首个 ThinkingPart content（与 smoke baseline 一致）。

    >1 ThinkingPart → drift warning（spec §4.2.4 / R2-8c）；renderer 仍只取 parts[0]。
    """
    if not thinking_parts:
        return None
    if len(thinking_parts) > 1:
        logger.warning(
            "ModelResponse has %d ThinkingParts (smoke baseline = 1); "
            "renderer takes only parts[0] — see spec §4.2.4 / R2-8c",
            len(thinking_parts),
        )
    return thinking_parts[0].content
```

- [ ] **Step 2: 重写 `_extract_reasoning_per_response` 复用共享遍历**

`src/cli/display.py:775-803` —— 把 `out: list[...] = []` 到 `return out` 之间的实现体逐字替换为薄包装：

```python
    return [_first_thinking_content(tp) for tp, _ in _walk_react_responses(messages)]
```

（函数签名不动；行为不变：仍是每 Response 首个 ThinkingPart，>1 仍 warn。）

同时更新该函数 docstring 末尾的 "Placement note" 行——重构后 `format_cycle_output` 改为直接调 `_first_thinking_content`，本函数唯一调用方变成 T-DG-1 drift guard 测试。把：

```
    Placement note: spec §5.3 列在 app.py，本 plan 改放 display.py（消费者所在层）
    避免 display→app 循环 import；helper 唯一使用方是 format_cycle_output。
```

改为：

```
    Placement note: 重构后（webui-react-timeline §5.3）format_cycle_output 直接消费
    _walk_react_responses + _first_thinking_content；本函数唯一调用方为 T-DG-1 drift guard
    测试（与 app._extract_thinking_text 比对 smoke baseline 等价）。
```

- [ ] **Step 3: 重构 format_cycle_output 主循环消费共享遍历**

`src/cli/display.py:1459-1476` —— 把：

```python
    # === ②③ 时序段 ===
    response_msgs = [m for m in ctx.messages if isinstance(m, ModelResponse)]

    # spec §4.2.3: 渲染层 thinking 提取 SoT 由 _extract_reasoning_per_response 集中
    reasoning_per_response = _extract_reasoning_per_response(ctx.messages)

    for i, mr in enumerate(response_msgs):
        thinking = reasoning_per_response[i]
        tool_calls = [p for p in mr.parts if isinstance(p, ToolCallPart)]

        if thinking:
            lines.append(_render_reasoning(thinking))

        if tool_calls:
            lines.append(_render_action(
                tool_calls, tool_returns_lookup, ctx.cycle_id,
                retry_lookup=retry_lookup,
            ))
```

替换为（消费共享遍历，行为逐字等价——同序、同 thinking 取首、同 tool 列表）：

```python
    # === ②③ 时序段 ===
    # spec §5.3: 与 build_react_steps 共消费 _walk_react_responses，防双遍历漂移
    for thinking_parts, tool_calls in _walk_react_responses(ctx.messages):
        thinking = _first_thinking_content(thinking_parts)

        if thinking:
            lines.append(_render_reasoning(thinking))

        if tool_calls:
            lines.append(_render_action(
                tool_calls, tool_returns_lookup, ctx.cycle_id,
                retry_lookup=retry_lookup,
            ))
```

- [ ] **Step 4: 跑既有渲染保真 + drift guard 测试确认行为不变**

Run: `pytest tests/test_display_cycle.py tests/test_cycle_capture.py -v`
Expected: 全 PASS。重点：`format_cycle_output` 既有 render fidelity 用例（test_display_cycle.py:445+）+ drift guard T-DG-1（test_display_cycle.py:1230，比对 `_extract_thinking_text` 与 `_extract_reasoning_per_response`）全绿——证明重构未改渲染行为。

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py
git commit -m "refactor(display): 抽共享遍历提取器，format_cycle_output 主循环复用"
```

---

## Task 5: build_react_steps 骨架构建

**Files:**
- Modify: `src/cli/display.py`（新增 `build_react_steps`，置于 `_walk_react_responses` 之后）
- Test: `tests/test_display_cycle.py`（追加 build_react_steps + 一致性用例）

- [ ] **Step 1: 写失败测试**

在 `tests/test_display_cycle.py` 末尾追加：

```python
# === webui-cycle-react-timeline: build_react_steps ===

def test_build_react_steps_interleave_order():
    """多 response 交错：每步 thinking + 该步发起的 tool（保留发起顺序）。"""
    from src.cli.display import build_react_steps
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["think A", "think B", "final think"],
        tool_call_segments=[
            [("get_market_data", {"sym": "BTC"}, "px 63000"),
             ("get_position", {}, "flat")],
            [("open_position", {"side": "long"}, "ok")],
            [],   # 末轮纯决策
        ],
        final_text="(1) Stance: long",
    )
    steps = build_react_steps(msgs)
    assert [s["thinking"] for s in steps] == ["think A", "think B", "final think"]
    assert [t["tool_name"] for t in steps[0]["tools"]] == ["get_market_data", "get_position"]
    assert [t["tool_name"] for t in steps[1]["tools"]] == ["open_position"]
    assert steps[2]["tools"] == []            # 末轮无 tools，只留 thinking
    # tool_call_id 非空（指针落点）
    assert all(t["tool_call_id"] for s in steps for t in s["tools"])


def test_build_react_steps_skips_empty_response():
    """末轮纯 TextPart（无 thinking 无 tools）→ 跳过，不产生空元素（§4.1）。"""
    from src.cli.display import build_react_steps
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["only think", None],
        tool_call_segments=[[("get_position", {}, "flat")], []],
        final_text="decision only",
    )
    steps = build_react_steps(msgs)
    assert len(steps) == 1                     # 第二个 response 空 → 跳过
    assert steps[0]["thinking"] == "only think"


def test_build_react_steps_joins_multi_thinking_parts():
    """单 response 多 ThinkingPart → '\\n\\n' 拼接（与 _extract_thinking_text 同口径）。"""
    from pydantic_ai.messages import ModelResponse, ThinkingPart, TextPart
    from src.cli.display import build_react_steps
    msgs = [ModelResponse(parts=[
        ThinkingPart(content="part1"),
        ThinkingPart(content="part2"),
        TextPart(content="done"),
    ])]
    steps = build_react_steps(msgs)
    # 该 response 无 tools 但有 thinking → 不跳过
    assert len(steps) == 1
    assert steps[0]["thinking"] == "part1\n\npart2"


def test_build_react_steps_empty_messages():
    from src.cli.display import build_react_steps
    assert build_react_steps([]) == []


def test_react_steps_order_matches_render_order():
    """一致性（§11）：build_react_steps 工具顺序 == format_cycle_output 渲染顺序。"""
    from src.cli.display import build_react_steps, format_cycle_output, CycleRenderContext
    from src.cli.session_state import SessionStats  # 既有 dataclass，无参可构造（test_display_cycle.py:558）
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    from datetime import datetime, timezone
    msgs = build_cycle_messages(
        thinking_segments=["t1", "t2"],
        tool_call_segments=[
            [("get_market_data", {}, "px"), ("get_position", {}, "flat")],
            [("open_position", {}, "ok")],
        ],
        final_text="(1) Stance: long",
    )
    steps = build_react_steps(msgs)
    names_from_steps = [t["tool_name"] for s in steps for t in s["tools"]]
    assert names_from_steps == ["get_market_data", "get_position", "open_position"]

    ctx = CycleRenderContext(
        cycle_id="c1", trigger_type="scheduled", trigger_context=None, state_snapshot=None,
        messages=msgs, final_text="(1) Stance: long", cycle_tokens=100,
        stats=SessionStats(), cache_hit_rate=50.0,
        cycle_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        forensic_reason=None,
    )
    text = format_cycle_output(ctx)
    # 渲染文本中工具名出现顺序须与骨架一致（锁双遍历不漂移）
    i0, i1, i2 = text.index("get_market_data"), text.index("get_position"), text.index("open_position")
    assert i0 < i1 < i2
```

> 注：`SessionStats()` 无参可构造（见 test_display_cycle.py:558 既有 render 用例）；`CycleRenderContext` 字段对照 display.py:757-769 dataclass 签名。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_display_cycle.py -k build_react_steps -v`
Expected: FAIL —— `ImportError: cannot import name 'build_react_steps'`。

- [ ] **Step 3: 实现 build_react_steps**

`src/cli/display.py` —— 在 `_first_thinking_content` 之后插入：

```python
def build_react_steps(messages: list) -> list[dict]:
    """从 cycle 收尾的 result.new_messages() 重建 ReAct 骨架（spec §4.1）。

    与 format_cycle_output 共消费 _walk_react_responses（防双遍历漂移，§5.3）；仅 thinking
    聚合分叉：此处拼接该 response 全部 ThinkingPart（CLI 取首个）。

    返回按 ModelResponse 顺序的数组，每元素:
        {"thinking": str|None, "tools": [{"tool_call_id", "tool_name"}, ...]}
    - thinking: 该 response 全部 ThinkingPart content '\\n\\n' 拼接（无 → None），与
      app._extract_thinking_text 同口径。
    - tools: 该 response ToolCallParts，保留发起顺序；带 tool_name（§10 orphan 兜底所需）。
    - 既无 thinking 又无 tools 的空 response 跳过（末轮纯决策 TextPart 不进骨架；decision 列单源）。
    """
    steps: list[dict] = []
    for thinking_parts, tool_calls in _walk_react_responses(messages):
        thinking = "\n\n".join(p.content for p in thinking_parts) if thinking_parts else None
        tools = [{"tool_call_id": p.tool_call_id, "tool_name": p.tool_name} for p in tool_calls]
        if thinking is None and not tools:
            continue
        steps.append({"thinking": thinking, "tools": tools})
    return steps
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_display_cycle.py -k "build_react_steps or render_order" -v`
Expected: 全 PASS（交错顺序、空 response 跳过、多 part 拼接、空 messages、render 顺序一致）。

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(display): build_react_steps 重建 ReAct 骨架"
```

---

## Task 6: app.py 收尾落库 react_steps

**Files:**
- Modify: `src/cli/app.py:32-35`（display import 块）、`src/cli/app.py:72`（新增 `_safe_build_react_steps`）、`src/cli/app.py:716-746`（happy-path insert）、`src/cli/app.py:557-579` / `611-634`（两条 forensic insert）
- Test: `tests/test_cycle_capture.py`（追加 `_safe_build_react_steps` 单测）

- [ ] **Step 1: 写失败测试**

在 `tests/test_cycle_capture.py` 末尾追加：

```python
# === webui-cycle-react-timeline: _safe_build_react_steps ===

def test_safe_build_react_steps_serializes():
    from src.cli.app import _safe_build_react_steps
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    import json
    msgs = build_cycle_messages(
        thinking_segments=["t1"],
        tool_call_segments=[[("get_position", {}, "flat")]],
        final_text="decision",
    )
    raw = _safe_build_react_steps(msgs)
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed[0]["tools"][0]["tool_name"] == "get_position"


def test_safe_build_react_steps_none_on_empty():
    from src.cli.app import _safe_build_react_steps
    assert _safe_build_react_steps([]) is None     # 空骨架 → None（不存 "[]"）


def test_safe_build_react_steps_isolates_exception(monkeypatch):
    """build 抛异常 → None（fail-isolated，绝不阻断 AgentCycle 写入，§5.3）。"""
    import src.cli.app as app_mod
    from src.cli.app import _safe_build_react_steps

    def boom(messages):
        raise RuntimeError("parts schema changed")
    monkeypatch.setattr(app_mod, "build_react_steps", boom)
    assert _safe_build_react_steps(["anything"]) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_cycle_capture.py -k safe_build_react_steps -v`
Expected: FAIL —— `ImportError: cannot import name '_safe_build_react_steps'`。

- [ ] **Step 3: 加 import + `_safe_build_react_steps`**

`src/cli/app.py:32-35`（display import 块）—— 首个 names 行增 `build_react_steps`：

```python
from src.cli.display import (
    display_metrics, format_cycle_output, build_react_steps,
```

`src/cli/app.py` —— 在 `_extract_thinking_text`（:72）之后插入：

```python
def _safe_build_react_steps(messages) -> str | None:
    """收尾构建 ReAct 骨架并序列化为 JSON（spec §5.3）。

    fail-isolated：构建 + 序列化任一步异常 → None + logger.warning，绝不阻断关键的
    AgentCycle 写入（与现有 render 失败降级同策略）。空骨架 → None（不存 "[]"）。
    """
    try:
        steps = build_react_steps(messages)
        return json.dumps(steps, ensure_ascii=False) if steps else None
    except Exception:
        logger.warning("build_react_steps failed; react_steps=None", exc_info=True)
        return None
```

- [ ] **Step 4: happy-path insert 加 react_steps**

`src/cli/app.py:742-745` —— 在 `injected_events=...` 之后（`AgentCycle(...)` 闭合 `)` 之前）加一行：

```python
                injected_events=json.dumps(
                    [{k: v for k, v in rec.items() if k != "raw"}
                     for rec in deps.injected_events_log]
                ) if deps.injected_events_log else None,   # raw 回滚句柄落库剥离（spec §6）
                react_steps=_safe_build_react_steps(result.new_messages()),  # webui-react-timeline §5.3
```

> 注：`result.new_messages()` 在 happy path 本已被调用多次（:685 工具日志 / :714 thinking / :759 render ctx），本次新增第 4 次与现状一致（pydantic-ai 返回本 run 消息切片，非昂贵重建）。统一 hoist 为单一局部变量属独立 micro-refactor，不在本 iter scope，故不改。

- [ ] **Step 5: 两条 forensic insert 显式 react_steps=None**

`src/cli/app.py` —— `usage_limit_exceeded` 路径的 `AgentCycle(...)`（:557-579）与 `retry_exhausted` 路径的 `AgentCycle(...)`（:611-634），各在 `injected_events=None,` 行之后加：

```python
                    injected_events=None,   # 回滚后落 NULL（spec §2/§6）
                    react_steps=None,       # webui-react-timeline §5.3: forensic 无骨架
```

（两处缩进按各自 insert 上下文对齐——usage 路径较外、retry 路径多一层。）

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_cycle_capture.py -k safe_build_react_steps -v && pytest tests/test_cycle_capture.py tests/test_midcycle_forensics.py -v`
Expected: 新 3 用例 PASS；cycle capture / forensics 既有用例不回归。

- [ ] **Step 7: Commit**

```bash
git add src/cli/app.py tests/test_cycle_capture.py
git commit -m "feat(app): cycle 收尾落库 react_steps（fail-isolated）"
```

---

## Task 7: WebUI schema 增字段

**Files:**
- Modify: `src/webui/schemas.py:55-81`（`ToolCallRow` + `CycleDetail`）
- Test: `tests/test_webui_api.py`（schema 序列化断言，或并入 Task 8 query 测试）

- [ ] **Step 1: 给 ToolCallRow 加 tool_call_id**

`src/webui/schemas.py` —— `ToolCallRow`（:55-61），在 `result` 字段之后加：

```python
    tool_call_id: str | None = None  # react_steps 指针 JOIN 键（§8）；历史行为 None
```

- [ ] **Step 2: 给 CycleDetail 加三字段**

`src/webui/schemas.py` —— `CycleDetail`（:64-81），在 `model_id: str | None`（:81）之后加：

```python
    react_steps: list | dict | str | None = None   # ReAct 骨架（_loads 解析；放宽形态同 trigger_context，防损坏行整类 500）
    user_prompt_snapshot: str | None = None         # 唤醒上下文原文（§8 暴露 #1）
    execution_status: str = "ok"                    # forensic 兜底视图据此说明"为何无时间线"
```

- [ ] **Step 3: 跑 schema import smoke**

Run: `python -c "from src.webui import schemas; print(schemas.CycleDetail.model_fields.keys()); print(schemas.ToolCallRow.model_fields.keys())"`
Expected: `CycleDetail` 含 `react_steps` / `user_prompt_snapshot` / `execution_status`；`ToolCallRow` 含 `tool_call_id`。

- [ ] **Step 4: Commit**

```bash
git add src/webui/schemas.py
git commit -m "feat(webui): CycleDetail/ToolCallRow 增 react_steps 等字段"
```

---

## Task 8: WebUI query 传新字段

**Files:**
- Modify: `src/webui/queries.py:78-91`（`get_cycle_detail`）
- Test: `tests/test_webui_queries.py`（追加）

- [ ] **Step 1: 看现有 query 测试 fixture 风格**

Run: `grep -n "get_cycle_detail\|def test\|AgentCycle\|ToolCall\|init_db\|seed" tests/test_webui_queries.py | head -30`
Expected: 看到现有用例如何 seed `AgentCycle` + `ToolCall` 行并调 `get_cycle_detail`，照其风格写新断言。

- [ ] **Step 2: 写失败测试**

在 `tests/test_webui_queries.py` 末尾追加（seed 部分按文件既有 helper 调整；下方示意自洽路径）：

```python
@pytest.mark.asyncio
async def test_get_cycle_detail_returns_react_fields(tmp_path):
    """get_cycle_detail 透传 react_steps / user_prompt_snapshot / execution_status / tool_call_id。"""
    import json
    from src.storage.database import init_db, get_session
    from src.storage.models import Session as SessionModel, AgentCycle, ToolCall
    from src.webui import queries

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/q.db")
    try:
        async with get_session(engine) as s:
            s.add(SessionModel(
                id="sess1", name="t", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                status="active", exchange_type="simulated", timeframe="15m",
                scheduler_interval_min=15, approval_enabled=True, token_budget=500000,
            ))
            s.add(AgentCycle(
                session_id="sess1", cycle_id="c1", triggered_by="scheduled",
                execution_status="ok", decision="(1) Stance: hold",
                user_prompt_snapshot="Woke by scheduled tick",
                react_steps=json.dumps([{"thinking": "t1", "tools": [
                    {"tool_call_id": "call_1", "tool_name": "get_position"}]}]),
            ))
            s.add(ToolCall(
                session_id="sess1", cycle_id="c1", tool_name="get_position",
                status="ok", duration_ms=12, tool_call_id="call_1", result="flat",
            ))
            await s.commit()
            pk = (await s.execute(
                __import__("sqlalchemy").select(AgentCycle.id).where(AgentCycle.cycle_id == "c1")
            )).scalar_one()

        detail = await queries.get_cycle_detail(engine, pk)
        assert detail.execution_status == "ok"
        assert detail.user_prompt_snapshot == "Woke by scheduled tick"
        assert detail.react_steps[0]["tools"][0]["tool_call_id"] == "call_1"
        assert detail.tool_calls[0].tool_call_id == "call_1"
    finally:
        await engine.dispose()
```

> 注：`SessionModel` import 名以 `src/webui/queries.py` 顶部既有 import（`Session as SessionModel`）为准；NOT NULL 字段按 models.py 实际签名补全。

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/test_webui_queries.py::test_get_cycle_detail_returns_react_fields -v`
Expected: FAIL —— `detail.react_steps` 为 None / `AttributeError`（query 尚未传字段）。

- [ ] **Step 4: query 传新字段**

`src/webui/queries.py:78-91` —— `get_cycle_detail` 的 `schemas.CycleDetail(...)` 构造：
  1. `ToolCallRow(...)`（:84-86）末尾加 `tool_call_id=t.tool_call_id`：

```python
            schemas.ToolCallRow(tool_name=t.tool_name, status=t.status, duration_ms=t.duration_ms,
                                error_type=t.error_type, args=_loads(t.args),
                                result=t.result, tool_call_id=t.tool_call_id) for t in tcs
```

  2. `CycleDetail(...)` 末尾（`model_id=c.model_id,` 之后）加三行：

```python
        model_id=c.model_id,
        react_steps=_loads(c.react_steps),
        user_prompt_snapshot=c.user_prompt_snapshot,
        execution_status=c.execution_status,
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_webui_queries.py tests/test_webui_api.py -v`
Expected: 全 PASS（新用例 + 既有 webui 用例不回归）。

- [ ] **Step 6: Commit**

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): get_cycle_detail 透传 react_steps 等字段"
```

---

## Task 9: 重生成 openapi.json + types.ts

**Files:**
- Regenerate: `frontend/openapi.json`、`frontend/src/api/types.ts`

- [ ] **Step 1: 重生成 openapi.json**

Run（项目根目录）：
```bash
python -c "import json; from src.webui.app import create_app; open('frontend/openapi.json','w').write(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2))"
```
Expected: `frontend/openapi.json` 更新。

- [ ] **Step 2: 验证新字段进 schema**

Run: `grep -E "react_steps|user_prompt_snapshot|execution_status|tool_call_id" frontend/openapi.json`
Expected: 命中 `react_steps` / `user_prompt_snapshot` / `execution_status`（CycleDetail）+ `tool_call_id`（ToolCallRow）。

- [ ] **Step 3: 重生成 types.ts**

Run（frontend 目录）：
```bash
cd frontend && npm run gen:types && cd ..
```
Expected: `frontend/src/api/types.ts` 更新；`grep -E "react_steps|tool_call_id" frontend/src/api/types.ts` 命中。

- [ ] **Step 4: Commit**

```bash
git add frontend/openapi.json frontend/src/api/types.ts
git commit -m "chore(webui): 重生成 openapi.json + 前端类型"
```

---

## Task 10: ReactTimeline.vue 时间线组件

**Files:**
- Create: `frontend/src/components/ReactTimeline.vue`
- Test: `frontend/test/ReactTimeline.spec.ts`

时间线契约（§9 / §6 / §10）：
- props: `steps`（react_steps）、`toolCalls`（ToolCallRow[]）、`injectedEvents`（注入记录数组或 null）。
- 由 `toolCalls` 建 `{tool_call_id → row}` map；由 `injectedEvents` 建 `{after_tool_call_id → 记录[]}` map。
- 逐 step：thinking 块（null 跳过）→ 逐 tool 卡（按 tool_call_id 查 row；缺 → orphan 因因中性标注）→ 该 tool 后紧跟其锚定的注入卡（批量并排）。

- [ ] **Step 1: 写失败测试**

Create `frontend/test/ReactTimeline.spec.ts`：

```typescript
import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import ReactTimeline from "@/components/ReactTimeline.vue";

const baseProps = () => ({
  steps: [
    { thinking: "评估趋势", tools: [
      { tool_call_id: "call_1", tool_name: "get_market_data" },
      { tool_call_id: "call_2", tool_name: "get_position" },
    ] },
    { thinking: "决定开多", tools: [
      { tool_call_id: "call_3", tool_name: "open_position" },
    ] },
  ],
  toolCalls: [
    { tool_name: "get_market_data", status: "ok", duration_ms: 30, error_type: null, args: { sym: "BTC" }, result: "px 63000", tool_call_id: "call_1" },
    { tool_name: "get_position", status: "ok", duration_ms: 12, error_type: null, args: {}, result: "flat", tool_call_id: "call_2" },
    { tool_name: "open_position", status: "ok", duration_ms: 80, error_type: null, args: { side: "long" }, result: "ok", tool_call_id: "call_3" },
  ],
  injectedEvents: null as any,
});

describe("ReactTimeline", () => {
  it("按 steps 顺序渲染 thinking 与工具名", () => {
    const w = mount(ReactTimeline, { props: baseProps() as any });
    const txt = w.text();
    expect(txt).toContain("评估趋势");
    expect(txt).toContain("决定开多");
    // 工具名按骨架顺序出现
    const i0 = txt.indexOf("get_market_data");
    const i1 = txt.indexOf("get_position");
    const i2 = txt.indexOf("open_position");
    expect(i0).toBeGreaterThanOrEqual(0);
    expect(i0).toBeLessThan(i1);
    expect(i1).toBeLessThan(i2);
  });

  it("工具卡按 tool_call_id 解析出 args/result（展开后）", async () => {
    const w = mount(ReactTimeline, { props: baseProps() as any });
    // 点开第一张工具卡
    await w.findAll(".tool-card .tool-head")[0].trigger("click");
    expect(w.text()).toContain("px 63000");
  });

  it("orphan tool_call_id（无对应 toolCall 行）→ 渲 tool_name + 因因中性标注", () => {
    const p = baseProps();
    p.steps[1].tools[0].tool_call_id = "call_missing";   // 骨架引用但 toolCalls 无此行
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("open_position");
    expect(w.text()).toContain("无遥测记录");
  });

  it("注入卡按 after_tool_call_id 锚在对应工具后", () => {
    const p = baseProps();
    p.injectedEvents = [
      { event: { type: "fill", side: "long" }, after_tool: "get_position", offset_ms: 1200, after_tool_call_id: "call_2" },
    ];
    const w = mount(ReactTimeline, { props: p as any });
    const txt = w.text();
    expect(txt).toContain("触发事件注入");
    // 注入卡出现在 get_position 之后、open_position 之前
    const iInj = txt.indexOf("触发事件注入");
    const iNext = txt.indexOf("open_position");
    expect(iInj).toBeGreaterThan(txt.indexOf("get_position"));
    expect(iInj).toBeLessThan(iNext);
  });

  it("批量注入（共享 after_tool_call_id）并排多张", () => {
    const p = baseProps();
    p.injectedEvents = [
      { event: { type: "fill" }, after_tool: "get_position", offset_ms: 1, after_tool_call_id: "call_2" },
      { event: { type: "price_level_alert" }, after_tool: "get_position", offset_ms: 2, after_tool_call_id: "call_2" },
    ];
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.findAll(".injection-card").length).toBe(2);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts; cd ..`
Expected: FAIL —— 组件不存在（解析失败 / mount 报错）。

- [ ] **Step 3: 实现 ReactTimeline.vue**

Create `frontend/src/components/ReactTimeline.vue`：

```vue
<script setup lang="ts">
import { computed, ref } from "vue";
import { NTag } from "naive-ui";
import type { ToolCallRow } from "@/api/client";
import JsonBlock from "@/components/JsonBlock.vue";

interface ReactTool { tool_call_id: string | null; tool_name: string }
interface ReactStep { thinking: string | null; tools: ReactTool[] }
interface InjectedEvent {
  event: unknown;
  after_tool?: string;
  offset_ms?: number | null;
  after_tool_call_id?: string | null;
}

const props = defineProps<{
  steps: ReactStep[];
  toolCalls: ToolCallRow[];
  injectedEvents: InjectedEvent[] | null;
}>();

const toolMap = computed(() => {
  const m = new Map<string, ToolCallRow>();
  for (const t of props.toolCalls) if (t.tool_call_id) m.set(t.tool_call_id, t);
  return m;
});

const injectionsByToolId = computed(() => {
  const m = new Map<string, InjectedEvent[]>();
  for (const e of props.injectedEvents ?? []) {
    const k = e.after_tool_call_id;
    if (!k) continue;
    (m.get(k) ?? m.set(k, []).get(k)!).push(e);
  }
  return m;
});

// 每张工具卡的展开态：key = tool_call_id（无 id 用合成 key）
const openCards = ref<Set<string>>(new Set());
function cardKey(t: ReactTool, si: number, ti: number) {
  return t.tool_call_id ?? `orphan-${si}-${ti}`;
}
function toggle(key: string) {
  const s = new Set(openCards.value);
  s.has(key) ? s.delete(key) : s.add(key);
  openCards.value = s;
}

function rowFor(t: ReactTool): ToolCallRow | undefined {
  return t.tool_call_id ? toolMap.value.get(t.tool_call_id) : undefined;
}
function injectionsFor(t: ReactTool): InjectedEvent[] {
  return t.tool_call_id ? injectionsByToolId.value.get(t.tool_call_id) ?? [] : [];
}
function statusType(s: string) {
  return s === "ok" ? "success" : s === "biz_error" ? "warning" : "error";
}
</script>

<template>
  <div class="react-timeline">
    <div v-for="(step, si) in steps" :key="si" class="react-step">
      <!-- 思考块 -->
      <div v-if="step.thinking" class="thinking">
        <span class="step-icon">🧠</span>
        <pre class="thinking-text">{{ step.thinking }}</pre>
      </div>

      <!-- 工具卡 + 锚定注入卡 -->
      <template v-for="(t, ti) in step.tools" :key="cardKey(t, si, ti)">
        <div class="tool-card">
          <div class="tool-head clickable" @click="toggle(cardKey(t, si, ti))">
            <span class="step-icon">⚙</span>
            <span class="tool-name">{{ t.tool_name }}</span>
            <template v-if="rowFor(t)">
              <n-tag size="tiny" :type="statusType(rowFor(t)!.status)">
                {{ rowFor(t)!.error_type ? `${rowFor(t)!.status} · ${rowFor(t)!.error_type}` : rowFor(t)!.status }}
              </n-tag>
              <span class="muted">{{ rowFor(t)!.duration_ms }}ms</span>
            </template>
            <span v-else class="muted orphan">无遥测记录（被拒或记录失败）</span>
          </div>
          <div v-if="rowFor(t) && openCards.has(cardKey(t, si, ti))" class="tool-body">
            <div class="kv"><span class="k">入参</span><JsonBlock :value="rowFor(t)!.args" /></div>
            <div class="kv"><span class="k">结果</span>
              <JsonBlock v-if="rowFor(t)!.result != null" :value="rowFor(t)!.result" />
              <span v-else class="seam">结果未捕获</span>
            </div>
          </div>
        </div>

        <!-- 该工具后锚定的注入事件（批量并排） -->
        <div v-for="(inj, ii) in injectionsFor(t)" :key="`inj-${si}-${ti}-${ii}`" class="injection-card">
          <span class="step-icon">⚡</span>
          <span class="inj-title">触发事件注入</span>
          <span v-if="inj.offset_ms != null" class="muted">+{{ inj.offset_ms }}ms</span>
          <JsonBlock :value="inj.event" />
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.react-step { border-left: 2px solid rgba(96, 165, 250, 0.3); padding-left: 10px; margin-bottom: 14px; }
.thinking { display: flex; gap: 6px; margin-bottom: 8px; }
.thinking-text { white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 12px; line-height: 1.5; background: rgba(0,0,0,0.18); padding: 6px 8px; border-radius: 4px; flex: 1; }
.tool-card { margin: 6px 0; background: rgba(255,255,255,0.03); border-radius: 4px; }
.tool-head { display: flex; align-items: center; gap: 6px; padding: 5px 8px; cursor: pointer; user-select: none; font-size: 12px; }
.tool-name { font-weight: 600; }
.tool-body { padding: 4px 8px 8px 26px; }
.kv { display: flex; gap: 8px; margin-top: 4px; font-size: 12px; }
.kv .k { opacity: 0.6; min-width: 32px; }
.injection-card { display: flex; align-items: center; gap: 6px; margin: 6px 0 6px 18px; padding: 5px 8px; background: rgba(250, 204, 21, 0.1); border-radius: 4px; font-size: 12px; }
.inj-title { font-weight: 600; }
.step-icon { flex: 0 0 auto; }
.muted { opacity: 0.55; }
.orphan { font-style: italic; }
.seam { font-size: 12px; opacity: 0.5; font-style: italic; }
.clickable { cursor: pointer; }
</style>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts; cd ..`
Expected: 5 passed（顺序、args/result 解析、orphan 标注、注入锚定、批量并排）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ReactTimeline.vue frontend/test/ReactTimeline.spec.ts
git commit -m "feat(webui): ReactTimeline 时间线组件"
```

---

## Task 11: CycleDetailPanel.vue 重构

**Files:**
- Modify: `frontend/src/components/CycleDetailPanel.vue`（整体重写）
- Test: `frontend/test/CycleDetailPanel.spec.ts`（重写）

布局（§9）：chips（含新增 llm / execution_status）→ Context（user_prompt_snapshot 原文，可折叠，null 跳过）→ ReactTimeline（react_steps 非空）或扁平回退（null）→ 决策。

- [ ] **Step 1: 重写测试**

替换 `frontend/test/CycleDetailPanel.spec.ts` 全文：

```typescript
import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CycleDetailPanel from "@/components/CycleDetailPanel.vue";

function detail(overrides = {}) {
  return {
    id: 5, cycle_label: "c5", triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z",
    reasoning: "thinking text", decision: "(1) Stance: hold",
    trigger_context: [{ type: "scheduled_tick" }],
    state_snapshot: { balance: { total_usdt: 10000 } },
    injected_events: null,
    react_steps: [
      { thinking: "评估趋势", tools: [{ tool_call_id: "call_1", tool_name: "get_position" }] },
    ],
    user_prompt_snapshot: "Woke by scheduled tick at 10:00",
    execution_status: "ok",
    tool_calls: [
      { tool_name: "get_position", status: "ok", duration_ms: 12, error_type: null, args: { symbol: "BTC" }, result: "flat", tool_call_id: "call_1" },
    ],
    tokens_consumed: 9000, input_tokens: 8000, output_tokens: 1000, cache_hit_rate: 92.76,
    wall_time_ms: 5000, llm_call_ms: 4000, model_id: "claude",
    ...overrides,
  };
}

describe("CycleDetailPanel", () => {
  it("渲染 ReAct 时间线（thinking + 工具名）与决策", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("评估趋势");
    expect(w.text()).toContain("get_position");
    expect(w.text()).toContain("(1) Stance: hold");
  });

  it("渲染唤醒上下文原文（user_prompt_snapshot）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("Woke by scheduled tick at 10:00");
  });

  it("user_prompt_snapshot 为 null（legacy）时不渲染 Context 块", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ user_prompt_snapshot: null }) as any } });
    expect(w.text()).not.toContain("唤醒上下文");
  });

  it("react_steps 为 null（legacy/forensic）→ 回退扁平视图 + 说明", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ react_steps: null }) as any } });
    expect(w.text()).toContain("无交错时间线");
    expect(w.text()).toContain("thinking text");   // 回退渲 reasoning 整块
  });

  it("回退分支：react_steps=null 但 injected_events 非空时仍渲染注入事件（防丢失）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ react_steps: null, injected_events: [{ event: { type: "fill" } }] }) as any } });
    expect(w.text()).toContain("中途注入事件");
  });

  it("chips 含 llm 与 execution_status", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("llm");
    expect(w.text()).toContain("ok");
  });

  it("cache 命中率按 0-100 口径直接显示，不再 ×100", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ cache_hit_rate: 92.76 }) as any } });
    expect(w.text()).toContain("cache 93%");
    expect(w.text()).not.toContain("9276");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts; cd ..`
Expected: FAIL（旧组件无时间线 / Context / 回退说明 / llm chip）。

- [ ] **Step 3: 重写 CycleDetailPanel.vue**

替换 `frontend/src/components/CycleDetailPanel.vue` 全文：

```vue
<script setup lang="ts">
import { computed, ref, h } from "vue";
import { NDataTable, NTag, NSpace } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { CycleDetail, ToolCallRow } from "@/api/client";
import JsonBlock from "@/components/JsonBlock.vue";
import ReactTimeline from "@/components/ReactTimeline.vue";

const props = defineProps<{ detail: CycleDetail }>();

const hasTimeline = computed(() => Array.isArray(props.detail.react_steps) && props.detail.react_steps.length > 0);
const hasInjected = computed(() => {
  const e = props.detail.injected_events;
  return Array.isArray(e) ? e.length > 0 : e != null;
});
const contextOpen = ref(true);

// 与 ReactTimeline.statusType 同口径（biz_error→warning），避免同数据两视图配色不一致
function statusType(s: string) {
  return s === "ok" ? "success" : s === "biz_error" ? "warning" : "error";
}

// 回退扁平视图：仅 react_steps 缺失（legacy/forensic）时用
const toolsOpen = ref(false);
const slowest = computed(() => {
  const ds = props.detail.tool_calls.map((t) => t.duration_ms ?? 0);
  return ds.length ? Math.max(...ds) : 0;
});
const toolColumns: DataTableColumns<ToolCallRow> = [
  { title: "工具", key: "tool_name" },
  {
    title: "状态", key: "status",
    render: (r) => h(NTag, { size: "small", type: statusType(r.status) },
      { default: () => (r.error_type ? `${r.status} · ${r.error_type}` : r.status) }),
  },
  { title: "耗时(ms)", key: "duration_ms" },
  { title: "入参", key: "args", render: (r) => h(JsonBlock, { value: r.args }) },
  { title: "结果", key: "result",
    render: (r) => (r.result == null ? h("span", { class: "seam" }, "结果未捕获") : h(JsonBlock, { value: r.result })) },
];
</script>

<template>
  <div class="cycle-detail">
    <!-- 1. 头部遥测 chips -->
    <n-space class="chips" :size="6">
      <n-tag size="small">tokens {{ detail.tokens_consumed }}</n-tag>
      <n-tag v-if="detail.input_tokens != null" size="small">in {{ detail.input_tokens }} / out {{ detail.output_tokens }}</n-tag>
      <n-tag v-if="detail.cache_hit_rate != null" size="small">cache {{ detail.cache_hit_rate.toFixed(0) }}%</n-tag>
      <n-tag v-if="detail.wall_time_ms != null" size="small">wall {{ detail.wall_time_ms }}ms</n-tag>
      <n-tag v-if="detail.llm_call_ms != null" size="small">llm {{ detail.llm_call_ms }}ms</n-tag>
      <n-tag size="small" :type="detail.execution_status === 'ok' ? 'default' : 'error'">{{ detail.execution_status }}</n-tag>
      <n-tag v-if="detail.model_id" size="small">{{ detail.model_id }}</n-tag>
    </n-space>

    <!-- 2. 唤醒上下文（原文版，可折叠；null 不渲染） -->
    <section v-if="detail.user_prompt_snapshot">
      <h4 class="clickable" @click="contextOpen = !contextOpen">唤醒上下文 {{ contextOpen ? "▾" : "▸" }}</h4>
      <pre v-if="contextOpen" class="context">{{ detail.user_prompt_snapshot }}</pre>
    </section>

    <!-- 3. ReAct 时间线（主角）或扁平回退 -->
    <section>
      <h4>ReAct 过程</h4>
      <ReactTimeline
        v-if="hasTimeline"
        :steps="(detail.react_steps as any)"
        :tool-calls="detail.tool_calls"
        :injected-events="(detail.injected_events as any) ?? null"
      />
      <div v-else class="flat-fallback">
        <p class="seam">该 cycle 无交错时间线（历史 / 取证记录）。下方为扁平视图。</p>
        <h5 class="tools-toggle clickable" @click="toolsOpen = !toolsOpen">
          工具调用（{{ detail.tool_calls.length }} 个 · 最慢 {{ slowest }}ms）{{ toolsOpen ? "▾" : "▸" }}
        </h5>
        <n-data-table v-if="toolsOpen" :columns="toolColumns" :data="detail.tool_calls" size="small" :bordered="false" />
        <!-- 注入事件：legacy cycle 可能 react_steps=null 而 injected_events 非空（注入 iter 晚于无骨架行），
             回退分支须渲染，否则其注入在 WebUI 彻底丢失（恢复旧 CycleDetailPanel 行为） -->
        <div v-if="hasInjected" class="inj-fallback">
          <h5>中途注入事件</h5>
          <JsonBlock :value="detail.injected_events" />
        </div>
        <pre class="reasoning">{{ detail.reasoning || "—" }}</pre>
      </div>
    </section>

    <!-- 4. 决策 -->
    <section><h4>决策</h4><pre class="decision">{{ detail.decision || "—" }}</pre></section>
  </div>
</template>

<style scoped>
.cycle-detail { padding: 8px 4px; }
.chips { margin-bottom: 10px; }
section { margin-bottom: 12px; }
h4 { margin: 0 0 4px; font-size: 13px; opacity: 0.85; }
h5 { margin: 6px 0 4px; font-size: 12px; opacity: 0.8; }
.clickable { cursor: pointer; user-select: none; }
.context { white-space: pre-wrap; word-break: break-word; background: rgba(0,0,0,0.22); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; max-height: 240px; overflow-y: auto; }
.reasoning { max-height: 360px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; background: rgba(0, 0, 0, 0.25); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 8px 0 0; }
.decision { white-space: pre-wrap; word-break: break-word; background: rgba(96, 165, 250, 0.08); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; }
:deep(.seam) { font-size: 12px; opacity: 0.5; font-style: italic; }
.seam { font-size: 12px; opacity: 0.5; font-style: italic; }
.inj-fallback { margin-top: 8px; }
</style>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts; cd ..`
Expected: 7 passed（时间线、Context 原文、Context null 跳过、react_steps null 回退、回退分支注入不丢失、llm/execution_status chips、cache 口径）。

- [ ] **Step 5: 跑前端全量 + build**

Run: `cd frontend && npx vitest run && npm run build; cd ..`
Expected: 全部测试 PASS；`npm run build` 成功（类型无误、naive-ui pin 2.38.1 不动）。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/CycleDetailPanel.vue frontend/test/CycleDetailPanel.spec.ts
git commit -m "feat(webui): CycleDetailPanel 重构为 ReAct 时间线布局"
```

---

## 最终验证

- [ ] **后端全量**

Run: `pytest -q`
Expected: 全绿（基线 ~2341 passed + 本 iter 新增用例）。

- [ ] **前端全量 + build**

Run: `cd frontend && npx vitest run && npm run build; cd ..`
Expected: 全绿 + build 成功。

- [ ] **迁移往返手验**

Run: `python -c "import asyncio; from src.storage.database import init_db; asyncio.run(init_db('sqlite+aiosqlite:///data/_smoke.db'))" && rm -f data/_smoke.db`
Expected: fresh DB 建库无错（含两新列）。

完成后用 superpowers:finishing-a-development-branch 收尾。

---

## Self-Review

**1. Spec coverage（逐 § 对照）：**
- §3 方案 A（骨架 + 指针，工具遥测单源）→ Task 1（列）+ Task 5（骨架）+ Task 10（JOIN 渲染）✓
- §4.1 react_steps 结构 → Task 1 + Task 5 ✓
- §4.2 tool_call_id → Task 1 + Task 2 ✓
- §4.3 after_tool_call_id（无迁移，自动落库）→ Task 3（record dict）+ §4.3 序列化无需改（app.py:742-745 只剥 raw）✓
- §5.1 recorder → Task 2 ✓
- §5.2 injector → Task 3 ✓
- §5.3 收尾构建 + fail-isolated + 两条 forensic None + 共享遍历提取器 → Task 4（共享遍历）+ Task 5（build）+ Task 6（_safe_build + forensic None）✓
- §6 注入整合（干净 result + after_tool_call_id 锚定 + 批量并排）→ Task 3 + Task 10 ✓
- §7 迁移（down_revision=b43e33764d90 实查、upgrade ADD COLUMN、downgrade 重建 view、不回填）→ Task 1 ✓
- §8 schema/query（CycleDetail 三字段 + ToolCallRow tool_call_id）→ Task 7 + Task 8 ✓
- §9 前端（chips+Context+时间线+决策）→ Task 10 + Task 11 ✓
- §10 边界（react_steps null 回退、orphan 因因中性、批量注入、截断 result、同名多调）→ Task 10/11 测试覆盖 ✓
- §11 测试策略 → 各 Task 测试步骤 + 一致性用例（Task 5 Step 1 `test_react_steps_order_matches_render_order`）✓
- §12 非目标（决策字段化 / Context 精炼 / 拒绝原因 / 回填 / thinking 单源 / 实时流）→ 计划未触及，符合 ✓

**2. Placeholder scan：** 无 TBD/TODO；每代码步均给完整代码块。两处「按文件实际签名补全」注解（`SessionStats` 构造、webui query seed 的 NOT NULL 字段）是对接既有 fixture 的必要校准提示，非占位——已指明对照来源（既有 render 用例 / models.py）。

**3. Type consistency：**
- `react_steps` 元素结构 `{thinking, tools:[{tool_call_id, tool_name}]}` 在 Task 5（build）、Task 8（query 透传）、Task 10/11（前端 ReactStep/ReactTool 接口）一致。
- `after_tool_call_id` 在 Task 3（写）、Task 10（前端 InjectedEvent 接口读）一致。
- `_safe_build_react_steps` / `build_react_steps` / `_walk_react_responses` / `_first_thinking_content` 命名贯穿 Task 4/5/6 一致。
- `execution_status` / `user_prompt_snapshot` / `tool_call_id` 在 schema（Task 7）、query（Task 8）、前端（Task 11）一致。
