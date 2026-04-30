# R2-4 Implementation Plan — biz error metrics + decision subtype derivation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** R2-4 落地 sim4-issues §P0-1（业务失败 metrics 可见）+ §P0-3（decision 'adjust' 拆 4 子类）双议题，在 W2 真观察期启动前消除 baseline 阻塞。

**Architecture:** ContextVar side-channel（P0-1，零行为改造）+ 静态 action 类别拆分（P0-3，stateless 派生）+ 单 Alembic migration（容量扩容，无 backfill）。Spec 见 `docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md`。

**Tech Stack:** Python 3.11+ / pydantic-ai 1.78+ (capability `wrap_tool_execute` 接口) / SQLAlchemy 2.0 async / Alembic batch_alter_table / pytest async / SQLite (test) + PostgreSQL (prod)

---

## File Structure

### 创建
| 路径 | 责任 |
|---|---|
| `alembic/versions/<rev>_r2_4_decision_subtypes_and_biz_error.py` | Alembic migration：tool_calls.status 10→20 + decision_logs.decision 20→30，仅扩容无 backfill |
| `docs/metrics/decision-enum-timeline.md` | enum 演进时间线 + SQL 兼容性指南 + 字段职责分工锚点 |

### 修改
| 路径 | 改动 |
|---|---|
| `src/services/tool_call_recorder.py` | 新增 `_biz_error_type` ContextVar + `note_biz_error()` + `BIZ_ERROR_TYPES` frozenset；`wrap_tool_execute` 改造（status 优先级 + ContextVar reset） |
| `src/agent/tools_execution.py:214` | set_price_alert 阈值越界路径加 `note_biz_error("invalid_threshold_range")` |
| `src/agent/tools_execution.py:272-275` | cancel_price_level_alert 协议错路径加 `note_biz_error("invalid_alert_id_format")` |
| `src/agent/tools_execution.py:284` | cancel_price_level_alert 状态错路径加 `note_biz_error("alert_not_found")` |
| `src/cli/app.py:53-62` | 拆 ADJUST_ACTIONS 为 PROTECT/ENTRY_ORDER/LEVERAGE/ALERT 4 子集 + ADJUST_ACTIONS 改为 union |
| `src/cli/app.py:65-106` | `_derive_decision_from_actions` 改派生 4 个 adjust_* 子类（保留 logger.warning 兜底） |
| `src/storage/models.py:90` | DecisionLog.decision String(20) → String(30) |
| `src/storage/models.py:181` | ToolCall.status String(10) → String(20) |

### 测试新增 / 修改
| 路径 | 改动 |
|---|---|
| `tests/test_tool_call_recorder.py` | +6 项（biz_error / leak / exception 优先级 / fail-soft / drift guard / control flow skip）|
| `tests/test_alert_lifecycle.py` 或 `tests/test_tools.py` | +3 项（set_price_alert / cancel format / cancel not found 端到端）|
| `tests/test_derive_decision.py` | t4 rename / t7 调整 / t11 拆 4 子集 / t12 String(30) / +6 新增 |
| `tests/test_alembic_migration.py` | +4 项（tool_calls.status 容量 / decision_logs.decision 容量 / 历史 adjust 保留 / 索引保留） |
| `tests/test_decision_log_e2e.py` 或并入 test_app.py | +1 项（adjust_protect 端到端 — sim #4 `fdf20e56` 场景）|

---

## Pre-flight Checks

- [ ] **P1: 确认 baseline + 分支**

```bash
git status
git log --oneline -3
git branch --show-current
pytest --collect-only 2>&1 | tail -3
```

Expected:
```
On branch feature/iter-w2r2-4-biz-error-and-decision-subtypes
ad7a825 docs(iter-w2r2-4): add biz error metrics + decision subtype derivation design spec
940 tests collected
```

- [ ] **P2: 跑全量 baseline 通过**

Run: `pytest -x -q 2>&1 | tail -5`
Expected: `937 passed, 3 skipped` (no fails)

- [ ] **P3: 确认 PR 号交叉（spec §8.5 #6）**

```bash
git log --oneline | grep -E "Iter [3-5]|Merge pull request" | head -10
```

Confirm: Iter 3 = PR #28, Iter 4 = PR #29 with git evidence (用于 §5.6 文档落地时 PR # 校验)。

- [ ] **P4: T0' commit plan 文档（feedback_plan_doc_commit_first 纪律）**

> Plan 文档作为独立 commit 先于代码变更 commit。spec (T0) 已 landed at `ad7a825`；plan (T0') 必须在 T1 之前 commit。

```bash
git status   # 确认 plan 文件 untracked
git add docs/superpowers/plans/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes.md
git commit -m "$(cat <<'EOF'
docs(iter-w2r2-4): add biz error metrics + decision subtype derivation implementation plan

R2-4 7-task TDD plan 实施 sim4-issues §P0-1 + §P0-3 修复:
- T1: Alembic migration (widen status & decision columns)
- T2: P0-1 ContextVar hook + BIZ_ERROR_TYPES + recorder 改造
- T3: P0-1 instrument set_price_alert + cancel_price_level_alert
- T4: P0-3 split ADJUST_ACTIONS into 4 subsets + derive priority
- T5: P0-3 update existing tests + add G7 status drift guard
- T6: docs/metrics/decision-enum-timeline.md
- T7: integration regression — sim #4 fdf20e56 scenario

Plan review processed (2 rounds): 7 项 R1 (3 硬错 / 2 中度 / 2 轻度) + 3 项 R2 (轻度修复时引入)。
详见 plan §"Plan Review Processing Log"。

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: T0' commit hash 落定 (8e... or similar)，`git log --oneline -3` 显示 plan commit 在 spec commit 之上。

---

## Task 1: Alembic Migration — widen tool_calls.status & decision_logs.decision

**Files:**
- Create: `alembic/versions/<rev>_r2_4_decision_subtypes_and_biz_error.py`（rev 由 `alembic revision` 生成）
- Modify: `src/storage/models.py:90` (decision String(20)→(30))
- Modify: `src/storage/models.py:181` (status String(10)→(20))
- Modify: `tests/test_alembic_migration.py:193` (现有断言 `VARCHAR(20)` → `VARCHAR(30)` — 因为 T1 改 ORM 后 init_db path 3 走 Base.metadata.create_all 自然产出 VARCHAR(30)，旧断言会 fail)
- Test: `tests/test_alembic_migration.py` (新增 4 项)

> **Pre-condition fixture**：`alembic_cfg_factory` 已存在于 `tests/test_alembic_migration.py:16`（无下划线前缀，作为 pytest fixture 参数注入）。新增测试签名格式：`def test_xxx(tmp_path: Path, alembic_cfg_factory):` + body 内 `cfg = alembic_cfg_factory(db_path)`。

> **决策**：先改 model 与 migration（schema 演进），再修测试。逻辑顺序：DB 容量 → ORM model → 派生函数能写入新 enum 值。

- [ ] **Step 1.1: 写 4 项失败测试**

读现有 `tests/test_alembic_migration.py`（沿用 Iter 3 测试模式）。在文件末尾追加（注意：`alembic_cfg_factory` 是 pytest fixture 作为参数注入；用 `==` 精确断言匹配现有惯例 line 193；**`_create_pre_alembic_schema(db_path)` 必须在 `command.upgrade` 前调用**——Iter 3 migration 是 ALTER 不是 CREATE，空 DB 会 `OperationalError: no such index ix_sim_orders_session_status`，沿用 line 215-216 W1-like 测试模式）：

```python
def test_r2_4_upgrade_widens_tool_calls_status(tmp_path: Path, alembic_cfg_factory):
    """R2-4: tool_calls.status String(10) → String(20)。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)  # Iter 3 migration 是 ALTER 不是 CREATE，必须先建 W1 schema
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(tool_calls)")}
    assert "status" in cols
    assert cols["status"][2] == "VARCHAR(20)", \
        f"tool_calls.status 期望 VARCHAR(20)，实际 {cols['status'][2]}"


def test_r2_4_upgrade_widens_decision_logs_decision(tmp_path: Path, alembic_cfg_factory):
    """R2-4: decision_logs.decision String(20) → String(30)。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(decision_logs)")}
    assert "decision" in cols
    assert cols["decision"][2] == "VARCHAR(30)", \
        f"decision_logs.decision 期望 VARCHAR(30)，实际 {cols['decision'][2]}"


def test_r2_4_upgrade_preserves_historical_adjust_rows(tmp_path: Path, alembic_cfg_factory):
    """R2-4 不动 'adjust' 历史行（A 方案，spec §5.5）。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    # 跑到 Iter 3 head（不含 R2-4）
    command.upgrade(cfg, "379f62306805")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # 模拟 Iter 4 之后写入的 adjust 行
    cur.execute("INSERT INTO sessions (id, name) VALUES ('sess-x', 'pre-r2-4')")
    cur.execute(
        "INSERT INTO decision_logs "
        "(session_id, cycle_id, trigger_type, decision, tokens_used, created_at) "
        "VALUES ('sess-x', 'cyc-x', 'scheduled', 'adjust', 0, datetime('now'))"
    )
    conn.commit()
    conn.close()

    # 跑 R2-4 upgrade
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = list(cur.execute("SELECT decision FROM decision_logs WHERE cycle_id = 'cyc-x'"))
    assert len(rows) == 1, f"期望 1 行，实际 {len(rows)}"
    assert rows[0][0] == "adjust", \
        f"R2-4 不应 backfill 历史 'adjust' → 实际 {rows[0][0]!r}"


def test_r2_4_upgrade_preserves_existing_indexes(tmp_path: Path, alembic_cfg_factory):
    """R2-4 不动 Iter 3 已建索引。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    indexes = {
        r[1] for r in cur.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='decision_logs'"
        )
    }
    assert "ix_decision_logs_session_id_cycle_id" in indexes, \
        f"Iter 3 索引应保留，实际 indexes={indexes}"
```

**额外：修 line 193 既有断言**（T1 改 ORM 后 path 3 自然产出 VARCHAR(30)，旧断言会 fail）：

```python
# 原 (line 193):
# assert cols["decision"][2] == "VARCHAR(20)", f"decision should be VARCHAR(20), got {cols['decision'][2]}"
assert cols["decision"][2] == "VARCHAR(30)", f"decision should be VARCHAR(30), got {cols['decision'][2]}"
```

- [ ] **Step 1.2: 跑测试看红**

Run: `pytest tests/test_alembic_migration.py::test_r2_4_upgrade_widens_tool_calls_status tests/test_alembic_migration.py::test_r2_4_upgrade_widens_decision_logs_decision tests/test_alembic_migration.py::test_r2_4_upgrade_preserves_historical_adjust_rows tests/test_alembic_migration.py::test_r2_4_upgrade_preserves_existing_indexes -v`

Expected: 全部 FAIL（migration 还未创建；容量断言不通过）。

- [ ] **Step 1.3: 改 ORM model 容量**

Modify `src/storage/models.py:90`:
```python
# 原: decision: Mapped[str] = mapped_column(String(20))
decision: Mapped[str] = mapped_column(String(30))                              # String(20)→String(30) (R2-4 spec §5.2)
```

Modify `src/storage/models.py:181`:
```python
# 原: status: Mapped[str] = mapped_column(String(10))
status: Mapped[str] = mapped_column(String(20))                                 # String(10)→String(20) (R2-4 spec §4.1)
```

- [ ] **Step 1.4: 生成 Alembic migration 文件**

Run: `alembic revision -m "r2_4 decision subtypes and biz error metrics"`
Expected: 生成 `alembic/versions/<rev>_r2_4_decision_subtypes_and_biz_error.py`

替换文件全部内容为：

```python
"""r2_4 decision subtypes and biz error metrics

Revision ID: <auto-generated by alembic>
Revises: 379f62306805
Create Date: 2026-04-30 ...

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md

Schema-only migration. No data backfill — historical 'adjust' rows
preserved verbatim per A-strategy decision (see spec §5.5).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '<auto>'  # 保留 alembic 生成的 hex
down_revision: Union[str, Sequence[str], None] = '379f62306805'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # P0-1: tool_calls.status 容量扩容（与 decision_logs.status 是不同列，本 R2-4 不动后者）
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=10),
            type_=sa.String(length=20),
            existing_nullable=False,
        )

    # P0-3: decision_logs.decision 容量扩容（与 decision_logs.status 不同列）
    with op.batch_alter_table("decision_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "decision",
            existing_type=sa.String(length=20),
            type_=sa.String(length=30),
            existing_nullable=False,
        )


def downgrade() -> None:
    # 反向：仅给开发期 rollback；生产 W2 不做 downgrade（spec §6.4 风险表）
    # CAVEAT: 若 DB 中已有新 enum 值（如 'adjust_entry_order' 18 char）
    # SQLite batch_alter_table 模式下 String 长度收紧不强制截断；PostgreSQL 会拒绝。
    with op.batch_alter_table("decision_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "decision",
            existing_type=sa.String(length=30),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=20),
            type_=sa.String(length=10),
            existing_nullable=False,
        )
```

> **CAVEAT**: 保留 alembic 自动生成的 `revision: str = '<hex>'` 不要替换为 `'<auto>'` 字面量。该 hex 是 migration 的 PK，被 alembic 用作 head 标识。

- [ ] **Step 1.5: 跑测试看绿**

Run: `pytest tests/test_alembic_migration.py -v 2>&1 | tail -20`
Expected: R2-4 4 项 PASS + 既有所有 PASS。

- [ ] **Step 1.6: 跑全量 regression**

Run: `pytest -x -q 2>&1 | tail -5`
Expected: 既有 940 collected → 944 collected (+4 R2-4 migration test)，全 pass。

- [ ] **Step 1.7: T1 commit**

```bash
git add src/storage/models.py alembic/versions/*r2_4*.py tests/test_alembic_migration.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-4): T1 alembic migration — widen status & decision columns

R2-4 spec §6 schema-only migration:
- tool_calls.status: String(10) → String(20) (容纳 'biz_error' enum + 余量)
- decision_logs.decision: String(20) → String(30) (容纳 adjust_entry_order 18 char + 余量)
- 仅扩容（widen），无新列、无 backfill、无索引变化
- batch_alter_table 沿用 Iter 3 模式，down_revision=379f62306805

Tests: 940 → 944 collected (+4 migration verify: 容量 ×2 + 历史 adjust 不动 + 索引保留)。

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §6
EOF
)"
```

---

## Task 2: P0-1 ContextVar Hook + BIZ_ERROR_TYPES + Recorder

**Files:**
- Modify: `src/services/tool_call_recorder.py` (加 ContextVar / note_biz_error / BIZ_ERROR_TYPES + 改造 wrap_tool_execute)
- Test: `tests/test_tool_call_recorder.py` (+6)

> **决策**：先建立 P0-1 基础设施（recorder 改造 + 模块级常量），下个 task 再 instrument 工具。这样基础设施可独立测试，工具变动也独立 review。

- [ ] **Step 2.1: 写 6 项失败测试（追加至 tests/test_tool_call_recorder.py 末尾）**

```python
async def test_records_biz_error_when_note_biz_error_called(engine, session_with_row):
    """工具内 note_biz_error → tool_calls.status='biz_error', error_type=<type>。"""
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        note_biz_error("invalid_threshold_range")
        return "Invalid threshold_pct: must be 0.1-50.0, got 0.05"

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("set_price_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    # LLM 看到的字符串不变（fact 透明）
    assert result == "Invalid threshold_pct: must be 0.1-50.0, got 0.05"

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_threshold_range"


async def test_biz_error_does_not_leak_across_calls(engine, session_with_row):
    """call A note_biz_error 后，call B 不 note → call B 仍 status='ok' (ContextVar reset)。"""
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler_a(args):
        note_biz_error("invalid_threshold_range")
        return "fail string"

    async def handler_b(args):
        return "success string"

    await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("tool_a"),
        tool_def=MagicMock(),
        args={},
        handler=handler_a,
    )
    await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("tool_b"),
        tool_def=MagicMock(),
        args={},
        handler=handler_b,
    )

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall).order_by(ToolCall.id))).scalars().all()
    assert len(rows) == 2
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_threshold_range"
    assert rows[1].status == "ok", \
        f"ContextVar 应在 wrap_tool_execute 入口 reset；call B 不应继承 call A 的 biz_error"
    assert rows[1].error_type is None


async def test_exception_overrides_biz_error(engine, session_with_row):
    """工具同时 note_biz_error 又抛 ValueError → status='error', error_type='ValueError'（exception 优先）。"""
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        note_biz_error("invalid_threshold_range")
        raise ValueError("unexpected boom after note")

    with pytest.raises(ValueError, match="unexpected boom"):
        await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("buggy_tool"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert rows[0].error_type == "ValueError"


async def test_note_biz_error_unknown_type_logs_and_skips(engine, session_with_row, caplog):
    """fail-soft: 拼错 → logger.error 调用 + 不 set ContextVar；后续写 status='ok'（spec §4.2）。"""
    import logging
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        note_biz_error("typo_xxx")  # 不在 BIZ_ERROR_TYPES
        return "tool returned ok"

    with caplog.at_level(logging.ERROR, logger="src.services.tool_call_recorder"):
        result = await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("any_tool"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    assert result == "tool returned ok"
    assert any("typo_xxx" in rec.message for rec in caplog.records), \
        f"应 logger.error 含拼错的 type；实际 records: {[r.message for r in caplog.records]}"

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok", \
        "拼错应 fail-soft，ContextVar 不被 set，本次 tool call 仍记 'ok'"


def test_biz_error_types_drift_guard():
    """BIZ_ERROR_TYPES 集合 vs `note_biz_error("...")` 字面引用一致。
    扫 src/agent/tools_execution.py 内所有 note_biz_error 调用，断言 string literal 全部 ∈ BIZ_ERROR_TYPES。
    """
    import re
    from pathlib import Path
    from src.services.tool_call_recorder import BIZ_ERROR_TYPES

    src = Path("src/agent/tools_execution.py").read_text()
    # 扫 note_biz_error("xxx") 或 note_biz_error('xxx')
    pattern = re.compile(r'note_biz_error\(["\']([a-z_]+)["\']\)')
    cited = set(pattern.findall(src))

    drift = cited - BIZ_ERROR_TYPES
    assert not drift, \
        f"tools_execution.py 引用未注册的 biz error type: {drift}（请在 BIZ_ERROR_TYPES 注册或更正字面量）"

    # Sanity: R2-4 应 instrument ≥ 3 处（spec §4.3）
    assert len(cited) >= 3, \
        f"R2-4 应 instrument ≥3 处 note_biz_error；实测 {len(cited)} 处: {cited}"


async def test_control_flow_exception_skips_biz_error_recording(engine, session_with_row):
    """工具 note_biz_error + raise ApprovalRequired → 不写库（控制流路径优先 skip_record）。"""
    from pydantic_ai.exceptions import ApprovalRequired
    from src.services.tool_call_recorder import ToolCallRecorder, note_biz_error

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        note_biz_error("invalid_threshold_range")
        raise ApprovalRequired()  # pydantic_ai 1.78: __init__(self, metadata: dict|None=None)

    with pytest.raises(ApprovalRequired):
        await recorder.wrap_tool_execute(
            make_ctx(deps),
            call=make_call("any_tool"),
            tool_def=MagicMock(),
            args={},
            handler=handler,
        )

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 0, "控制流异常应 skip_record，不写 tool_calls"
```

> **签名核查（已 verify）**: pydantic_ai 1.78 `ApprovalRequired.__init__(self, metadata: dict[str, Any] | None = None)` — 不接 tool_call_id / tool_name kwarg。沿用既有 `tests/test_tool_call_recorder.py::test_control_flow_exception_not_recorded` 写法（无 args 调用）。

- [ ] **Step 2.2: 跑测试看红**

Run: `pytest tests/test_tool_call_recorder.py -v 2>&1 | tail -30`
Expected: 6 项新增全部 FAIL（`note_biz_error`、`BIZ_ERROR_TYPES` 还未存在 → ImportError 或 status 断言失败）。

- [ ] **Step 2.3: 实现 ContextVar + 上报函数 + 改造 wrap_tool_execute**

Modify `src/services/tool_call_recorder.py`：

在 imports 段加：
```python
from contextvars import ContextVar
```

在 `_CONTROL_FLOW_EXCEPTIONS` 定义之后（约 L48 后）加模块级：
```python
# R2-4 §4.2 — biz_error side-channel
# 工具内 note_biz_error("xxx") 上报；wrap_tool_execute 在 handler 返回后读
# LLM 看到的工具返回字符串不变（fact 透明，零行为改造）
_biz_error_type: ContextVar[str | None] = ContextVar(
    "tool_call_biz_error_type", default=None
)

BIZ_ERROR_TYPES: frozenset[str] = frozenset({
    "invalid_threshold_range",        # set_price_alert 阈值越界
    "invalid_alert_id_format",        # cancel_price_level_alert 协议错（非 8-char hex）
    "alert_not_found",                # cancel_price_level_alert 状态错（已触发/不存在）
})


def note_biz_error(error_type: str) -> None:
    """工具内调用以标记本次 tool call 为业务失败。

    LLM 看到的返回字符串不变（fact 透明）；ToolCallRecorder.wrap_tool_execute
    在 handler 返回后读取此 ContextVar，写入 tool_calls.status='biz_error',
    error_type=<type>。

    拼错保护策略：fail-soft（运行期 logger.error + 跳过 ContextVar set）。
    drift guard 测试期 strict 检查（test_biz_error_types_drift_guard）。

    CAVEAT: 必须在工具协程主体内调用，不要在 asyncio.gather 子 task 内调
    （Python ContextVar 子 task 修改不会回流父 frame）。
    """
    if error_type not in BIZ_ERROR_TYPES:
        logger.error(
            "note_biz_error called with unknown type: %r — drift guard expected to catch this",
            error_type,
        )
        return
    _biz_error_type.set(error_type)
```

修改 `wrap_tool_execute` 方法（约 L58-L122），核心改造：在 try 入口 reset ContextVar，handler 成功返回后读 ContextVar 决定 biz_error。完整修改后的方法：

```python
async def wrap_tool_execute(
    self,
    ctx: RunContext[TradingDeps],
    *,
    call: ToolCallPart,
    tool_def: ToolDefinition,
    args: ValidatedToolArgs,
    handler: WrapToolExecuteHandler,
) -> Any:
    start = time.monotonic()
    # R2-4 §4.2 — reset per-call (隔离嵌套 / 异步任务 / 跨调用泄漏)
    token = _biz_error_type.set(None)
    status, error_type = "ok", None
    skip_record = False
    try:
        result = await handler(args)
    except _CONTROL_FLOW_EXCEPTIONS:
        skip_record = True  # 控制流信号直通
        raise
    except Exception as e:
        status, error_type = "error", type(e).__name__
        raise
    else:
        # handler 成功返回 — 检查是否被 note_biz_error 标记
        biz = _biz_error_type.get()
        if biz is not None:
            status, error_type = "biz_error", biz
        return result
    finally:
        _biz_error_type.reset(token)
        if not skip_record:
            try:
                duration_ms = int((time.monotonic() - start) * 1000)
                if ctx.deps.cycle_id is None:
                    raise RuntimeError(
                        "cycle_id must be set on TradingDeps before tool call"
                    )
                if ctx.deps.db_engine is None:
                    raise RuntimeError(
                        "db_engine must be set on TradingDeps"
                    )
                # 序列化 args，strip reasoning（spec §T0-2 (b)）
                args_dict = dict(call.args_as_dict())
                args_dict.pop("reasoning", None)
                args_serialized = json.dumps(args_dict, ensure_ascii=False) if args_dict else None
                if args_serialized and len(args_serialized) > 4000:
                    args_serialized = args_serialized[:4000]

                insert_start = time.monotonic()
                async with get_session(ctx.deps.db_engine) as session:
                    session.add(ToolCall(
                        session_id=ctx.deps.session_id,
                        cycle_id=ctx.deps.cycle_id,
                        tool_name=call.tool_name,
                        status=status,
                        duration_ms=duration_ms,
                        error_type=error_type,
                        args=args_serialized,
                    ))
                    await session.commit()
                insert_ms = (time.monotonic() - insert_start) * 1000
                logger.debug(
                    "tool_call_insert_ms=%.1f tool=%s", insert_ms, call.tool_name
                )
            except Exception as rec_err:
                logger.error(
                    "tool_call_recorder failed for %s: %s",
                    call.tool_name, rec_err,
                )
```

> **CAVEAT**: 关键逻辑顺序：
> 1. `try` 入口 `token = _biz_error_type.set(None)` reset
> 2. `except _CONTROL_FLOW_EXCEPTIONS` 仍 set `skip_record=True` + raise（不动）
> 3. `except Exception` 仍记 'error' + raise（exception 优先级高于 biz_error）
> 4. `else` 分支 — handler 返回时读 ContextVar
> 5. `finally` reset(token) + 写库（与现有 finally 同结构）

- [ ] **Step 2.4: 跑测试看绿**

Run: `pytest tests/test_tool_call_recorder.py -v 2>&1 | tail -30`
Expected: 6 新增 + 既有全 PASS。

- [ ] **Step 2.5: 跑全量 regression**

Run: `pytest -x -q 2>&1 | tail -5`
Expected: 944 → 950 collected (+6)，全 pass。

- [ ] **Step 2.6: T2 commit**

```bash
git add src/services/tool_call_recorder.py tests/test_tool_call_recorder.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-4): T2 ContextVar hook + BIZ_ERROR_TYPES + recorder改造

R2-4 spec §4.2 — P0-1 业务失败 metrics 可见基础设施：
- 加 _biz_error_type: ContextVar[str|None] (per-call 隔离)
- 加 BIZ_ERROR_TYPES frozenset 3 类常量
- 加 note_biz_error() 模块级函数 (fail-soft 拼错保护)
- 改 wrap_tool_execute: try入口 reset / else分支读 ContextVar / exception 优先级 > biz_error
- LLM 看到的工具返回字符串完全不变（零行为改造）

Tests: 944 → 950 collected (+6)。

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §4
EOF
)"
```

---

## Task 3: P0-1 Instrument set_price_alert + cancel_price_level_alert

**Files:**
- Modify: `src/agent/tools_execution.py:214` (set_price_alert 阈值越界路径)
- Modify: `src/agent/tools_execution.py:272-275` (cancel_price_level_alert 协议错路径)
- Modify: `src/agent/tools_execution.py:284` (cancel_price_level_alert 状态错路径)
- Modify: `tests/conftest.py` (上提 engine + session_with_row fixture，让 alert_lifecycle 测试可复用)
- Test: `tests/test_alert_lifecycle.py` (+3 端到端) + `tests/test_tool_call_recorder.py` (本地 fixture 引用 conftest 版本)

> **决策**：基础设施 (T2) 落地后，3 处 instrument 加 1 行 + 端到端验证。

> **Pre-condition** (verify 后落入 plan)：
> - `tests/conftest.py` 当前只含 `settings` / `trader_config` fixture
> - `tests/test_alert_lifecycle.py` 既有模式是 `MagicMock + db_engine=None` (让 `_record_action` L19 早 return)，**不能直接复用**到 R2-4 测试，因为 ToolCallRecorder 要写 ToolCall 必须有真 engine
> - 所以本 task 必须先把 engine + session_with_row fixture 上提到 `tests/conftest.py`，让 alert_lifecycle 的 R2-4 测试和 tool_call_recorder 测试都能复用

- [ ] **Step 3.0: 上提 fixture 到 tests/conftest.py**

Modify `tests/conftest.py`，在文件末尾追加（沿用项目惯例：`pyproject.toml:36 asyncio_mode = "auto"`，全 tests/ 0 处用 `pytest_asyncio.fixture` — 用 `@pytest.fixture` 包 async fixture 在 auto 模式下自动工作）：

```python
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture
async def engine() -> AsyncEngine:
    """In-memory SQLite engine + schema (R2-4 共享 fixture，原在 test_tool_call_recorder.py)."""
    from src.storage.database import init_db
    return await init_db("sqlite+aiosqlite:///:memory:")


@pytest.fixture
async def session_with_row(engine: AsyncEngine) -> str:
    """Insert parent session row so child rows' FK holds (R2-4 共享 fixture)."""
    from src.storage.database import get_session
    from src.storage.models import Session as SessionModel
    async with get_session(engine) as db:
        db.add(SessionModel(id="sess-test", name="unit-test"))
        await db.commit()
    return "sess-test"
```

> **注意**：`tests/conftest.py:1` 已 `import pytest`，无需重复 import。

**删除 `tests/test_tool_call_recorder.py:13-24` 双源 fixture**（conftest 版本会自动注入）：

```python
# 删除这一段（原 line 13-24 的 fixture）:
# @pytest.fixture
# async def engine() -> AsyncEngine:
#     return await init_db("sqlite+aiosqlite:///:memory:")
#
# @pytest.fixture
# async def session_with_row(engine: AsyncEngine) -> str:
#     async with get_session(engine) as db:
#         db.add(SessionModel(id="sess-test", name="unit-test"))
#         await db.commit()
#     return "sess-test"
```

> **CAVEAT**：删除后 `tests/test_tool_call_recorder.py` 顶部 `from src.storage.database import init_db, get_session` 与 `from src.storage.models import Session as SessionModel, ToolCall` 仍被其他 fixture / 测试用，**保留**这些 import 不动。

> **跨模块 fixture override 风险（已 verify 低风险）**：另两文件已有同名 `engine` fixture：
> - `tests/test_record_action_cycle_id.py:18-19` `engine(tmp_path: Path)` (签名不同)
> - `tests/test_tool_call_recorder_args.py:18-19` `engine(tmp_path: Path)` (签名不同)
>
> pytest fixture 解析规则：**module-level fixture 优先于 conftest fixture**（局部覆盖全局）。两文件签名不同（带 `tmp_path` 参数）会保持 module-local 行为，**不受 conftest 上提影响**。
>
> Step 3.0 落地后**必须单独验证**：
> ```bash
> pytest tests/test_record_action_cycle_id.py tests/test_tool_call_recorder_args.py -v 2>&1 | tail -10
> ```
> Expected: 全 PASS，无 fixture 解析错误。如 fail，说明 override 行为有差异，需调试或 rename conftest fixture。

- [ ] **Step 3.1a: 加 imports 到 tests/test_alert_lifecycle.py**

`tests/test_alert_lifecycle.py` 现有 imports（line 1-14）：
```python
from unittest.mock import AsyncMock, MagicMock
import pytest
from tests._fixtures import (
    make_fill_event, make_okx_exchange, make_sim_exchange, make_ticker,
)
```

R2-4 端到端测试需补 imports（追加到现有 imports 之后）：
```python
from sqlalchemy import select
from src.storage.database import get_session
from src.storage.models import ToolCall
from tests.test_tool_call_recorder import make_deps, make_ctx, make_call
```

> **Why each**: `select` + `ToolCall` 用于查 tool_calls 行验证 status；`get_session` 提供 async session；`make_deps/make_ctx/make_call` 是 helper 函数（不是 fixture，可跨 module import）。

- [ ] **Step 3.1b: 写 3 项端到端失败测试**

先 grep 现有 test 文件找最贴近的 test 文件：
```bash
grep -l "set_price_alert\|cancel_price_level_alert" tests/*.py
```

期望命中 `tests/test_alert_lifecycle.py`（R2-2 PR #31 引入 cancel state machine 测试）。在该文件末尾追加（fixture 自动从 `tests/conftest.py` 注入；3 项均加 `@pytest.mark.asyncio` 与现有 file 惯例对齐 — line 20/174/223/234/255 全部显式标注）：

```python
@pytest.mark.asyncio
async def test_set_price_alert_invalid_threshold_records_biz_error(engine, session_with_row):
    """端到端: set_price_alert 传 0.05 越界 → tool_calls 行 status='biz_error'."""
    from src.agent.tools_execution import set_price_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_alert_params.return_value = (1.0, 60)  # alerts enabled

    async def handler(args):
        return await set_price_alert(deps, threshold_pct=0.05, window_minutes=60, reasoning="t")

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("set_price_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    # LLM 看到的 fact 字符串不变
    assert "Invalid threshold_pct" in result
    assert "0.05" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_threshold_range"


@pytest.mark.asyncio
async def test_cancel_price_level_alert_invalid_format_records_biz_error(engine, session_with_row):
    """端到端: cancel_price_level_alert 传 '#1' (非 8-char hex) → biz_error 'invalid_alert_id_format'."""
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        return await cancel_price_level_alert(deps, alert_id="#1", reasoning="t")

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Invalid alert_id format" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_alert_id_format"


@pytest.mark.asyncio
async def test_cancel_price_level_alert_not_found_records_biz_error(engine, session_with_row):
    """端到端: cancel_price_level_alert 传合法 hex 但 alert 不存在 → biz_error 'alert_not_found'."""
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.remove_price_level_alert.return_value = False  # 不存在

    async def handler(args):
        return await cancel_price_level_alert(deps, alert_id="a3f2b8c1", reasoning="t")

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "already triggered or expired" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "alert_not_found"
```

> **CAVEAT**：
> - `engine` / `session_with_row` 是 fixture（Step 3.0 已上提到 `tests/conftest.py`，自动 inject）
> - `make_deps` / `make_ctx` / `make_call` 是辅助函数（不是 fixture），从 `tests/test_tool_call_recorder.py` 顶层 import：`from tests.test_tool_call_recorder import make_deps, make_ctx, make_call`
> - `MagicMock` / `select` / `ToolCall` 等同时 import

- [ ] **Step 3.2: 跑测试看红**

Run: `pytest tests/test_alert_lifecycle.py -v -k "biz_error" 2>&1 | tail -20`
Expected: 3 项 FAIL（工具未 instrument，rows[0].status='ok'）。

- [ ] **Step 3.3: 实现 3 处 instrument**

Modify `src/agent/tools_execution.py` 头部 imports（如 note_biz_error 还未 import）：
```python
from src.services.tool_call_recorder import note_biz_error
```

Modify L213-214：
```python
    # 原:
    # if not (0.1 <= threshold_pct <= 50.0):
    #     return f"Invalid threshold_pct: must be 0.1-50.0, got {threshold_pct}"
    if not (0.1 <= threshold_pct <= 50.0):
        note_biz_error("invalid_threshold_range")
        return f"Invalid threshold_pct: must be 0.1-50.0, got {threshold_pct}"
```

Modify L271-275：
```python
    # 原:
    # if not re.fullmatch(r"[0-9a-f]{8}", alert_id):
    #     return (
    #         f"Invalid alert_id format: {alert_id!r}. Expected 8-char hex "
    #         f"(e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids."
    #     )
    if not re.fullmatch(r"[0-9a-f]{8}", alert_id):
        note_biz_error("invalid_alert_id_format")
        return (
            f"Invalid alert_id format: {alert_id!r}. Expected 8-char hex "
            f"(e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids."
        )
```

Modify L283-284：
```python
    # 原: return f"Alert {alert_id} already triggered or expired"
    note_biz_error("alert_not_found")
    return f"Alert {alert_id} already triggered or expired"
```

> **CAVEAT**：每处都是 `note_biz_error("...")` 单行加在 `return` 之前；LLM 看到的 return 字符串保持原样。

- [ ] **Step 3.4: 跑测试看绿**

Run: `pytest tests/test_alert_lifecycle.py -v -k "biz_error" 2>&1 | tail -20`
Expected: 3 PASS。

- [ ] **Step 3.5: 跑 drift guard 验证**

Run: `pytest tests/test_tool_call_recorder.py::test_biz_error_types_drift_guard -v`
Expected: PASS（3 处 note_biz_error 字面量 ⊆ BIZ_ERROR_TYPES）。

- [ ] **Step 3.6: 跑全量 regression**

Run: `pytest -x -q 2>&1 | tail -5`
Expected: 950 → 953 collected (+3)，全 pass。

- [ ] **Step 3.7: T3 commit**

```bash
git add src/agent/tools_execution.py tests/test_alert_lifecycle.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-4): T3 instrument set_price_alert + cancel_price_level_alert

R2-4 spec §4.3 — P0-1 工具 instrument:
- set_price_alert L214 阈值越界 → note_biz_error("invalid_threshold_range")
- cancel_price_level_alert L272 协议错 → note_biz_error("invalid_alert_id_format")
- cancel_price_level_alert L284 状态错 → note_biz_error("alert_not_found")

工具返回字符串完全不变（fact 透明）；status='biz_error' 通过 ContextVar 上报。
sim #4 实证驱动 minimal set，非穷举（spec §4.4.3 显式声明 19 处中仅 3 处 instrument）。

Tests: 950 → 953 collected (+3 端到端)。

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §4.3
EOF
)"
```

---

## Task 4: P0-3 Split ADJUST_ACTIONS into 4 Subsets + Derive Priority

**Files:**
- Modify: `src/cli/app.py:50-62` (拆 ADJUST_ACTIONS)
- Modify: `src/cli/app.py:65-106` (改造 _derive_decision_from_actions)
- Test: `tests/test_derive_decision.py` (+6 新增；既有 t11/t12/t4/t7 留 T5 处理)

> **决策**：先拆常量 + 改派生函数（T4），再修既有测试（T5）。这样 T4 仅含逻辑变更，T5 仅含测试调整，commit 边界清晰。

- [ ] **Step 4.1: 写 6 项新增失败测试（追加至 tests/test_derive_decision.py 末尾）**

```python
async def test_t13_adjust_entry_order_derives_from_place_limit_order():
    """T13: cycle 仅含 place_limit_order → 'adjust_entry_order'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-13", "place_limit_order")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-13"
        )
    assert result == "adjust_entry_order"


async def test_t14_adjust_leverage_derives_from_adjust_leverage_action():
    """T14: cycle 仅含 adjust_leverage → 'adjust_leverage'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-14", "adjust_leverage")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-14"
        )
    assert result == "adjust_leverage"


async def test_t15_adjust_alert_derives_from_set_price_alert():
    """T15: cycle 仅含 set_price_alert → 'adjust_alert'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-15", "set_price_alert")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-15"
        )
    assert result == "adjust_alert"


async def test_t16_priority_protect_beats_alert_when_both_present():
    """T16: cycle 含 set_stop_loss + set_take_profit + add_price_level_alert (sim #4 fdf20e56 场景)
    → 'adjust_protect'（PROTECT 优先级高于 ALERT）。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-16", "set_stop_loss")
    await _insert_action(engine, "sess-derive-test", "cycle-16", "set_take_profit")
    await _insert_action(engine, "sess-derive-test", "cycle-16", "add_price_level_alert")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-16"
        )
    assert result == "adjust_protect", \
        f"sim #4 fdf20e56 场景应派生 'adjust_protect' (PROTECT > ALERT)，实际 {result!r}"


async def test_t17_priority_entry_order_beats_leverage_and_alert():
    """T17: cycle 含 place_limit_order + adjust_leverage + set_price_alert
    → 'adjust_entry_order'（ENTRY_ORDER 优先级高于 LEVERAGE/ALERT）。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-17", "place_limit_order")
    await _insert_action(engine, "sess-derive-test", "cycle-17", "adjust_leverage")
    await _insert_action(engine, "sess-derive-test", "cycle-17", "set_price_alert")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-17"
        )
    assert result == "adjust_entry_order"


async def test_t18_priority_leverage_beats_alert():
    """T18: cycle 含 adjust_leverage + set_price_alert → 'adjust_leverage'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-18", "adjust_leverage")
    await _insert_action(engine, "sess-derive-test", "cycle-18", "set_price_alert")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-18"
        )
    assert result == "adjust_leverage"
```

- [ ] **Step 4.2: 跑测试看红**

Run: `pytest tests/test_derive_decision.py -v -k "t13 or t14 or t15 or t16 or t17 or t18" 2>&1 | tail -15`
Expected: 6 项 FAIL（派生仍输出 'adjust' 单值）。

- [ ] **Step 4.3: 拆 ADJUST_ACTIONS 为 4 子集 + 改造派生函数**

Modify `src/cli/app.py:50-62`，**替换** ADJUST_ACTIONS 单一定义为：

```python
# Iter 4 §3.2 + R2-4 spec §5.3 — DecisionLog 派生类型分类常量
# R2-4 拆 ADJUST_ACTIONS 为 4 子集 (sim4-issues §P0-3)
# 派生优先级（业务直觉默认）: protect > entry_order > leverage > alert
# trade_actions 留底，未来若数据反证可仅重派生历史 decision_logs.decision，无需 schema 演进
PROTECT_ACTIONS = frozenset({
    "set_stop_loss",
    "set_take_profit",
})
ENTRY_ORDER_ACTIONS = frozenset({
    "place_limit_order",
    "cancel_order",
})
LEVERAGE_ACTIONS = frozenset({
    "adjust_leverage",
})
ALERT_ACTIONS = frozenset({
    "set_price_alert",
    "add_price_level_alert",
    "cancel_price_level_alert",
})

# 兜底 union — 用于 drift guard 测试 (T5 t11) / 任何"任意 adjust"判断
# set_next_wake 单独归 hold（spec §C5）；open_position / close_position 单独分类
ADJUST_ACTIONS = (
    PROTECT_ACTIONS | ENTRY_ORDER_ACTIONS | LEVERAGE_ACTIONS | ALERT_ACTIONS
)
```

Modify `src/cli/app.py:65-106` 派生函数为：

```python
async def _derive_decision_from_actions(
    session: AsyncSession,
    session_id: str,
    cycle_id: str,
) -> str:
    """从 trade_actions 反查 cycle 内 actions，按优先级派生 decision 类型。

    优先级（高 → 低）:
        open_long > open_short > close
        > adjust_protect > adjust_entry_order > adjust_leverage > adjust_alert
        > hold

    返回 9 类 enum: open_long / open_short / close /
    adjust_protect / adjust_entry_order / adjust_leverage / adjust_alert /
    hold / derive_error

    R2-4 spec §5.3 — 拆 'adjust' 单值为 4 子类（sim4-issues §P0-3）。
    DB 故障 fallback: derive_error（独立 enum，spec §8.1）。
    """
    try:
        rows = (await session.execute(
            select(TradeAction).where(
                TradeAction.session_id == session_id,
                TradeAction.cycle_id == cycle_id,
            ).order_by(TradeAction.id)  # first-match 语义稳定
        )).scalars().all()
    except (SQLAlchemyError, OSError):
        logger.exception(
            f"derive_decision SELECT failed for cycle {cycle_id}; falling back to 'derive_error'"
        )
        return "derive_error"

    actions = {a.action for a in rows}

    # 1. 开仓（最高优先级）
    for a in rows:
        if a.action == "open_position":
            if a.side not in ("long", "short"):
                logger.warning(
                    f"open_position with unexpected side={a.side!r} "
                    f"in cycle {cycle_id}; skipping this row, downstream "
                    f"classification (close/adjust/hold) takes over"
                )
                continue  # 跳过此 row，循环继续
            return f"open_{a.side}"  # open_long / open_short

    # 2. 平仓
    if "close_position" in actions:
        return "close"

    # 3. adjust 子类（按事件重要性优先级）
    if actions & PROTECT_ACTIONS:
        return "adjust_protect"
    if actions & ENTRY_ORDER_ACTIONS:
        return "adjust_entry_order"
    if actions & LEVERAGE_ACTIONS:
        return "adjust_leverage"
    if actions & ALERT_ACTIONS:
        return "adjust_alert"

    # 4. hold（无任何 ADJUST_ACTIONS，含 cycle 仅有 set_next_wake 的情况）
    return "hold"
```

> **CAVEAT**：
> 1. 保留 `logger.warning(f"open_position with unexpected side=...")` 兜底诊断日志（spec §5.3 caveat）
> 2. 派生函数仍 stateless（仅 trade_actions JOIN）
> 3. `actions = {a.action for a in rows}` 提到循环外，避免在 4 个 set 交集判断中重复构造

- [ ] **Step 4.4: 跑测试看绿**

Run: `pytest tests/test_derive_decision.py -v -k "t13 or t14 or t15 or t16 or t17 or t18" 2>&1 | tail -15`
Expected: 6 PASS。

- [ ] **Step 4.5: 跑既有派生测试**

Run: `pytest tests/test_derive_decision.py -v 2>&1 | tail -25`
Expected: 现有 t1-t3, t5, t6, t7, t8, t8.6, t11, t12 PASS；**t4 / t8.5 FAIL**。

**Fail 原因细节（重新校准）**：
- **t4 真 FAIL**：原断言 `result == "adjust"`，T4 后 set_stop_loss → `'adjust_protect'`
- **t8.5 真 FAIL**：原断言 `result == "adjust"`，T4 后 open_position(side=None) skip + set_stop_loss → `'adjust_protect'`
- **t7 不 FAIL**：原断言 `'open_long'`，T4 后 open_position 仍最高优先级 → 仍 `'open_long'`（T5 仅调整 docstring）
- **t11 不 FAIL**：扫 `_record_action_literals` 与 `ADJUST_ACTIONS | {open_position, close_position, set_next_wake}` 比对，T4 后 ADJUST_ACTIONS = 4 子集 union 仍含 8 个 action，集合相等不漂（T5 主动扩 G2/G3/G4 子集 drift guard 加强覆盖）
- **t12 不 FAIL**：原 enum_values 集合硬编码 6 项均 ≤ 20 char，T4 后未自动跟进新 4 个 adjust_* 子类 → **不会 fail，但覆盖不全**（T5 主动扩集合 + 长度断言改 30）

> **不在此 commit 修复 t4/t8.5**：保持 commit 边界清晰，T4 = 派生逻辑变更，T5 = 测试更新（含 t4/t8.5 真 fail 修 + t7 docstring 调整 + t11/t12 主动加强覆盖）。

- [ ] **Step 4.6: T4 commit**

```bash
git add src/cli/app.py tests/test_derive_decision.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-4): T4 split ADJUST_ACTIONS into 4 subsets + derive priority

R2-4 spec §5.3 — P0-3 'adjust' 拆 4 子类:
- 拆 ADJUST_ACTIONS 为 PROTECT/ENTRY_ORDER/LEVERAGE/ALERT 4 子集
- ADJUST_ACTIONS 改为 4 子集 union（兜底引用保留）
- 派生函数扩 4 个 adjust_* 子类:
  * adjust_protect (set_stop_loss/set_take_profit)
  * adjust_entry_order (place_limit_order/cancel_order)
  * adjust_leverage (adjust_leverage)
  * adjust_alert (set_price_alert/add_price_level_alert/cancel_price_level_alert)
- 派生优先级 protect > entry_order > leverage > alert (业务直觉默认值)
- 派生函数仍 stateless（仅 trade_actions JOIN）
- 保留 logger.warning('open_position with unexpected side=...') 兜底诊断日志

新增 6 测试覆盖 4 子类派生分支 + 3 优先级矩阵（sim #4 fdf20e56 场景回归）。

CAVEAT: t4/t8.5 在此 commit 后真 FAIL（'adjust'→'adjust_protect' 派生变更）；
t7/t11/t12 PASS 但覆盖不全 — 由 T5 commit 统一处理（fail 修复 + 主动加强覆盖）
（commit 边界清晰：T4 = 派生逻辑，T5 = 既有测试调整）。

Tests: 953 → 959 collected (+6 派生分支)。

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §5.3
EOF
)"
```

---

## Task 5: P0-3 Update Existing Tests (t4 rename / t7 / t11 split / t12 capacity)

**Files:**
- Modify: `tests/test_derive_decision.py` (t4 rename + 断言 / t7 调整 / t11 拆 4 / t12 String(30))

> **决策**：T4 commit 后既有 t4/t7/t11/t12 FAIL，T5 修复使全量绿。

- [ ] **Step 5.1: 先确认 fail 状态**

Run: `pytest tests/test_derive_decision.py -v -k "t4 or t8_5" 2>&1 | tail -10`
Expected: **t4 / t8.5 真 FAIL**（原断言 'adjust' 但派生输出 'adjust_protect'）。

`pytest tests/test_derive_decision.py -v -k "t7 or t11 or t12" 2>&1 | tail -10`
Expected: **t7 / t11 / t12 仍 PASS**（T4 改动未触发它们 fail；T5 主动加强覆盖而非修 fail）。

- [ ] **Step 5.2: 改 t4 — rename + 断言**

Modify `tests/test_derive_decision.py:91-101`：

```python
# 原:
# async def test_t4_adjust_derives_from_set_stop_loss():
#     """T4: cycle 仅含 set_stop_loss → 'adjust'。"""
#     ...
#     assert result == "adjust"

async def test_t4_adjust_protect_derives_from_set_stop_loss():
    """T4 (R2-4 rename): cycle 仅含 set_stop_loss → 'adjust_protect'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-4", "set_stop_loss")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-4"
        )
    assert result == "adjust_protect"
```

- [ ] **Step 5.3: 改 t7 — 调整断言**

Modify `tests/test_derive_decision.py:117-129`，将 `set_stop_loss` 共存场景断言更精准（PROTECT 优先级测试 t16 已 cover；t7 仅验"open 优先于任意 adjust"）：

```python
async def test_t7_priority_open_beats_adjust():
    """T7: cycle 含 open_position + set_stop_loss 同 cycle → 'open_long'（早期返回拦截）。

    R2-4 调整: set_stop_loss 单独本应派生 'adjust_protect'，但 open_position 优先级更高。
    """
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-7",
                         "open_position", side="long")
    await _insert_action(engine, "sess-derive-test", "cycle-7", "set_stop_loss")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-7"
        )
    assert result == "open_long", \
        f"open_position 应优先于任意 adjust_*，实际 {result!r}"
```

- [ ] **Step 5.4: 改 t8.5 — 调整断言**

Modify `tests/test_derive_decision.py:158-175`：原断言 `result == "adjust"`，R2-4 后 set_stop_loss 应派生 `adjust_protect`：

```python
async def test_t8_5_open_position_with_invalid_side_falls_through():
    """T8.5: open_position(side=None) + set_stop_loss 同 cycle → 'adjust_protect'。

    spec §3.5: 派生函数对 side ∉ {'long', 'short'} 兜底 — skip 此 row 让 downstream 接管。
    实测 cycle = [open_position(side=None), set_stop_loss] 应返回 'adjust_protect' 不是 'open_None'。
    R2-4 调整: 'adjust' → 'adjust_protect'（PROTECT 子集）。
    """
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-85",
                         "open_position", side=None)
    await _insert_action(engine, "sess-derive-test", "cycle-85", "set_stop_loss")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-85"
        )
    assert result == "adjust_protect", \
        f"side=None open_position 应被 skip 让 adjust_protect 接管，实际 {result!r}"
```

- [ ] **Step 5.5: 改 t11 — 拆 4 子集 drift guard**

Modify `tests/test_derive_decision.py:212-224` 替换为 4 个子集 drift guard + 1 个 union 兜底：

```python
def test_t11_adjust_actions_drift_guard():
    """T11 (R2-4 改造): tools_execution.py 内所有 _record_action action 字面量
    必须落入 ADJUST_ACTIONS union 或单独分类（open_position / close_position / set_next_wake）。

    R2-4 spec §7.2: 此测试是 ADJUST_ACTIONS union 兜底——
    G5 (ALERT_ACTIONS) 子集漂移由 union 间接覆盖（union 含 ALERT_ACTIONS 全部元素，
    新增/重命名 ALERT 类 action 会被 actual - expected drift 抓到）。
    G2/G3/G4 (PROTECT/ENTRY_ORDER/LEVERAGE) 由独立 t11_protect/t11_entry_order/t11_leverage 各自精确断言。
    """
    from src.cli.app import ADJUST_ACTIONS

    actual = _grep_record_action_literals("src/agent/tools_execution.py")
    expected = ADJUST_ACTIONS | {"open_position", "close_position", "set_next_wake"}
    drift = actual - expected
    assert not drift, \
        f"新增未分类的 action: {drift}（请更新 ADJUST_ACTIONS 子集或派生逻辑）"


def test_t11_protect_actions_drift_guard():
    """T11 G2 (R2-4): PROTECT_ACTIONS 子集 vs trade_actions 字面 action 名一致性。

    扫 tools_execution.py 内被分到 PROTECT_ACTIONS 的 action 名（手动列表，不靠 grep）。
    防止 trade_actions 写入侧 / 派生侧字面量漂移。
    """
    from src.cli.app import PROTECT_ACTIONS

    expected_protect = {"set_stop_loss", "set_take_profit"}
    assert PROTECT_ACTIONS == expected_protect, \
        f"PROTECT_ACTIONS 漂移: actual={PROTECT_ACTIONS}, expected={expected_protect}"


def test_t11_entry_order_actions_drift_guard():
    """T11 G3 (R2-4): ENTRY_ORDER_ACTIONS 子集 drift guard。"""
    from src.cli.app import ENTRY_ORDER_ACTIONS

    expected = {"place_limit_order", "cancel_order"}
    assert ENTRY_ORDER_ACTIONS == expected, \
        f"ENTRY_ORDER_ACTIONS 漂移: actual={ENTRY_ORDER_ACTIONS}, expected={expected}"


def test_t11_leverage_actions_drift_guard():
    """T11 G4 (R2-4): LEVERAGE_ACTIONS 子集 drift guard。"""
    from src.cli.app import LEVERAGE_ACTIONS

    expected = {"adjust_leverage"}
    assert LEVERAGE_ACTIONS == expected, \
        f"LEVERAGE_ACTIONS 漂移: actual={LEVERAGE_ACTIONS}, expected={expected}"
```

> **决策**：t11 原 1 项保留为 union 兜底；新增 t11_protect / t11_entry_order / t11_leverage 3 项（spec §7.2 G2/G3/G4）。注：ALERT_ACTIONS 子集已由 grep `_grep_record_action_literals` 通过 union 间接验证（任何 ALERT_ACTION 漂移会被 union drift guard 抓到）；如需单独 G5 测试，可在此模式扩展 `test_t11_alert_actions_drift_guard`，但 spec §7.2 表 G5 标注为 "extend"（用 union 兜底涵盖），不另写。

- [ ] **Step 5.6: 改 t12 — String(30) 容量**

Modify `tests/test_derive_decision.py:227-235`：

```python
def test_t12_derive_output_fits_decision_column():
    """T12 (R2-4 调整): 派生函数输出 enum 字符串必须 ≤ DecisionLog.decision String(30)。

    R2-4 spec §5.2 容量 String(20) → String(30)。
    legacy / adjust 不纳入此集合（不再写入）；historical-only。
    """
    enum_values = {
        "open_long", "open_short", "close",
        "adjust_protect", "adjust_entry_order", "adjust_leverage", "adjust_alert",
        "hold", "derive_error",
    }
    over_limit = [v for v in enum_values if len(v) > 30]
    assert not over_limit, f"派生输出 > 30 chars: {over_limit}"
```

- [ ] **Step 5.7: 加 status enum 取值 drift guard (G7)**

在 `tests/test_tool_call_recorder.py` 末尾追加（spec §7.2 G7）：

```python
def test_tool_calls_status_values_fit_column():
    """G7 (R2-4 spec §7.2): tool_calls.status 应用层 enum 取值 ⊆ String(20)。"""
    enum_values = {"ok", "biz_error", "error"}
    over_limit = [v for v in enum_values if len(v) > 20]
    assert not over_limit, f"status enum > 20 chars: {over_limit}"
```

- [ ] **Step 5.8: 跑测试看绿**

Run: `pytest tests/test_derive_decision.py tests/test_tool_call_recorder.py -v 2>&1 | tail -30`
Expected: 全 PASS（含 t4 rename / t7 / t8.5 / t11 拆 4 子集 / t12 String(30) / G7）。

- [ ] **Step 5.9: 跑全量 regression**

Run: `pytest -x -q 2>&1 | tail -5`
Expected: 959 → 963 collected (+4)，全 pass。

> **T5 净增 +4 算式**：t11 拆 4 子集（原 1 项保留 + 新增 3 项 = 净 +3）+ G7 status drift guard 新增 +1 + t12 改造 in-place 不增 collected +0 + t4/t7/t8.5 rename/调整 +0 = **+4**。
>
> **累计净增 +24（不是 +25）**：T1 +4 / T2 +6 / T3 +3 / T4 +6 / T5 +4 / T6 +0 / T7 +1 = **+24**。
>
> spec §7.3 估算 940 → ~965 (+25) 在 ±2 误差范围内（spec 表述 "drift guard +5 含 G6 t12 容量精确化 +1" 实际 G6 是 in-place 改造不增 collected）。最终 baseline 940 → ~964 collected。以 `pytest --collect-only` 实测数字为准。

- [ ] **Step 5.10: T5 commit**

```bash
git add tests/test_derive_decision.py tests/test_tool_call_recorder.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-4): T5 update existing derive_decision tests + add status drift guard

R2-4 spec §7.1 / §7.2:
- t4 rename → test_t4_adjust_protect_derives_from_set_stop_loss + 断言 'adjust_protect'
- t7 调整断言 — open 优先于任意 adjust_* (set_stop_loss 单独本应派生 adjust_protect)
- t8.5 调整断言 'adjust' → 'adjust_protect'
- t11 拆 4 子集 drift guard (G2/G3/G4 spec §7.2):
  * 原 t11 保留为 ADJUST_ACTIONS union 兜底
  * 新增 t11_protect / t11_entry_order / t11_leverage 子集断言
  * t11_alert (G5) 由 union 兜底间接覆盖
- t12 容量断言 String(20) → String(30) + enum 集合更新为 9 个新值
- 新增 G7: test_tool_calls_status_values_fit_column (status enum ⊆ String(20))

Tests: 959 → 963 collected (+4 净增: t11 拆 +3 + G7 +1 + t12 改造 +0 + 其余 rename 不增 collected)。

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §7
EOF
)"
```

---

## Task 6: Add decision-enum-timeline Documentation

**Files:**
- Create: `docs/metrics/decision-enum-timeline.md`

> **决策**：A 方案不动历史数据，靠文档承载 enum 演进 audit + SQL 兼容性指南 + 字段职责分工锚点。

- [ ] **Step 6.1: 创建文档目录（如不存在）**

Run: `ls docs/metrics 2>&1` （若 No such file，下一步 Write 会自动创建）

- [ ] **Step 6.2: 写文档**

Create `docs/metrics/decision-enum-timeline.md` 完整内容：

```markdown
# Decision Enum 演进时间线

本文档承载 `decision_logs.decision` 字段的 enum 取值演进 audit 与 SQL 兼容性指南。

## 当前可见取值（截至 R2-4，2026-04-30）

| Enum 值 | 引入时间 | 引入 PR | 仍在写入？ |
|---|---|---|---|
| `legacy` | Iter 3 | PR #28 | 否（仅历史 backfill）|
| `open_long` / `open_short` / `close` / `hold` | Iter 4 | PR #29 | 是 |
| `derive_error` | Iter 4 | PR #29 | 是（DB 故障 fallback）|
| `adjust` | Iter 4 | PR #29 | **否（R2-4 起停写）** |
| `adjust_protect` / `adjust_entry_order` / `adjust_leverage` / `adjust_alert` | R2-4 | PR #TBD | 是 |

> **PR # 占位**：R2-4 PR 编号在落 PR 时填实。

## 字段职责分工（设计锚点）

`decision_logs.decision` 与 `trade_actions` 表的职责分工：

| 表 | 职责 | 信息粒度 |
|---|---|---|
| `trade_actions` | fact-of-record（动作流水）| 每 action 一行，全保留 |
| `decision_logs.decision` | **降维标签**（cycle 主导决策）| 每 cycle 一个 enum 值 |

`decision_logs.decision` 字段当前 0 生产读取路径（grep 全 src 树），唯一未来读者是观察期 SQL 分析者（人工临时查询），目标是「按主导决策类型快速 pivot」。

让 decision 字段保留多值（数组 / 主从 / 位掩码）= **打破 decision_logs 的"降维"职责** = 与 trade_actions 表语义重复 = schema 设计退步。

## 单值 decision 与 trade_actions 下钻

decision_logs.decision 是「cycle 主导决策标签」，按优先级（protect > entry_order > leverage > alert）取最高一类。多类 adjust 共存时低优先级类别**不在此字段反映**，但 trade_actions 表保留 cycle 内全部动作。

### 何时 GROUP BY decision（粗粒度）
- cycle 模式分布、主导决策频率分析

### 何时 JOIN trade_actions（细粒度）
- 想看「cycle 内同时有 PROTECT 和 ALERT 的占比」
- 想看「首挂 SL/TP vs trailing」（结合 cycle 时序，stateful 分析）

例：cycle 内 PROTECT + ALERT 共存频率
```sql
SELECT COUNT(DISTINCT cycle_id) FROM trade_actions
WHERE cycle_id IN (
    SELECT cycle_id FROM trade_actions
    WHERE action IN ('set_stop_loss','set_take_profit')
  ) AND action IN ('set_price_alert','add_price_level_alert','cancel_price_level_alert');
```

## SQL 兼容性提示

跨观察期分析时（W2 vs W1/sim #4）若按 decision 细分：
- W1 / sim #4 旧数据中 adjust_* 表示为 `'adjust'`
- 新观察期数据使用 4 个 `adjust_*` 子类
- 兼容查询: `decision LIKE 'adjust%' OR decision = 'adjust'`

## R2-4 决策语境（参考）

详见：
- `.working/sim4-issues-inventory.md §P0-3`
- `docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §5`

不做 backfill 的理由（A 方案）:
1. W2 主分析路径不影响（新 session_id 物理隔离）
2. 跨 session tax 中等不阻塞（一个 OR 条件）
3. 派生函数 stateless，trade_actions 留底完整 → 任何时候可重派生
4. 不动旧数据是项目一贯做法（Iter 4 引入 derive_error 时未 retroactive update 旧 'legacy' 行）
5. 机器可读 audit 零成本保留（DB 行原值是历史 metrics 输出 ground truth）

## 派生优先级排序的设计假设

`protect > entry_order > leverage > alert` 是基于业务直觉的默认排序。sim #4 实证只直接验证了 protect + alert 共存场景（`fdf20e56`），其他组合（如 entry_order + leverage 共存）频率未知。

此排序是 placeholder default。**trade_actions 永远留底完整动作流水**——若 W2 数据反证某种排序不合实际，后续 PR 仅需重派生历史 `decision_logs.decision`（无需 schema 演进）。
```

- [ ] **Step 6.3: 验证文档可读**

Run: `cat docs/metrics/decision-enum-timeline.md | head -20`
Expected: 输出标题 + 表格头。

- [ ] **Step 6.4: T6 commit**

```bash
git add docs/metrics/decision-enum-timeline.md
git commit -m "$(cat <<'EOF'
docs(iter-w2r2-4): T6 add decision-enum-timeline.md

R2-4 spec §5.6 — A 方案历史数据策略 (不 backfill) 的文档承载:
- enum 取值演进时间表（Iter 3 / Iter 4 / R2-4）
- 字段职责分工锚点 (decision_logs.decision 降维标签 vs trade_actions fact)
- 单值 decision + trade_actions 下钻使用指南
- SQL 兼容性提示 (LIKE 'adjust%' OR = 'adjust')
- R2-4 不 backfill 理由 5 条 (W2 主分析不影响 / Iter 4 先例 / 派生 stateless 等)
- 派生优先级 placeholder default 设计假设声明

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §5.6
EOF
)"
```

---

## Task 7: Integration Regression — sim #4 fdf20e56 Scenario

**Files:**
- Test: `tests/test_decision_log_e2e.py` (new) 或并入既有 `tests/test_app.py` (+1)

> **决策**：端到端验证整个 R2-4 链路 — 模拟 sim #4 `fdf20e56` cycle 场景（开仓后首次挂 SL/TP + 续约 alert）走 derive_decision 派生为 `adjust_protect`。

- [ ] **Step 7.1: 选择测试位置**

Run: `ls tests/test_decision_log* tests/test_app*.py 2>&1`

如 `tests/test_decision_log_e2e.py` 不存在，新建；否则追加。本 plan 默认新建 `tests/test_decision_log_e2e.py`。

- [ ] **Step 7.2: 写 1 项 e2e 失败测试**

Create `tests/test_decision_log_e2e.py`:

```python
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
```

- [ ] **Step 7.3: 跑测试看绿（已被 T4 实现承接）**

Run: `pytest tests/test_decision_log_e2e.py -v 2>&1 | tail -10`
Expected: PASS（T4 派生函数已实现 PROTECT > ALERT 优先级）。

> **解释**：此 e2e 测试模拟 sim #4 实际场景，T4 实现完成后应直接 PASS（不需要新代码）。如果 FAIL，说明 T4 派生逻辑有 bug，需回 T4 修复后再验证。

- [ ] **Step 7.4: 跑全量 regression**

Run: `pytest -x -q 2>&1 | tail -5`
Expected: 963 → 964 collected (+1)，全 pass。

- [ ] **Step 7.5: 最终全量 baseline 验证**

Run: `pytest -q 2>&1 | tail -10`
Expected:
```
... 961 passed, 3 skipped ...
```
（baseline 940 → R2-4 后 ~964 collected = 961 pass + 3 skip；T1 +4 / T2 +6 / T3 +3 / T4 +6 / T5 +4 / T6 +0 / T7 +1 = +24 净增。spec §7.3 估算 +25 在 ±2 误差范围内）

- [ ] **Step 7.6: T7 commit**

```bash
git add tests/test_decision_log_e2e.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-4): T7 integration regression — sim #4 fdf20e56 scenario

R2-4 spec §7.1 / §5.4 矩阵第一行回归:
- sim #4 cycle fdf20e56 (含 set_stop_loss + set_take_profit + add_alert + set_next_wake)
- R2-4 派生 'adjust_protect' (PROTECT 优先于 ALERT)
- 核心保护事件浮现，不再被「续约 alert」语义掩盖

W2 阻塞场景 P0-3 端到端验证通过。

Tests: 963 → 964 collected (+1 e2e)。

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md §1.1 §5.4
EOF
)"
```

---

## Final Verification

- [ ] **F1: 全部 commit 一览**

Run: `git log --oneline main..HEAD`
Expected:
```
<hash> test(iter-w2r2-4): T7 integration regression — sim #4 fdf20e56 scenario
<hash> docs(iter-w2r2-4): T6 add decision-enum-timeline.md
<hash> test(iter-w2r2-4): T5 update existing derive_decision tests + add status drift guard
<hash> feat(iter-w2r2-4): T4 split ADJUST_ACTIONS into 4 subsets + derive priority
<hash> feat(iter-w2r2-4): T3 instrument set_price_alert + cancel_price_level_alert
<hash> feat(iter-w2r2-4): T2 ContextVar hook + BIZ_ERROR_TYPES + recorder改造
<hash> feat(iter-w2r2-4): T1 alembic migration — widen status & decision columns
<hash> docs(iter-w2r2-4): add biz error metrics + decision subtype derivation implementation plan   ← T0' (Pre-flight P4)
ad7a825 docs(iter-w2r2-4): add biz error metrics + decision subtype derivation design spec   ← T0
```
（共 7 feat/test/docs task commit + T0' plan commit + T0 spec commit = 9 commit）

- [ ] **F2: 全量 pytest 通过**

Run: `pytest -q 2>&1 | tail -5`
Expected: 961 passed, 3 skipped (~964 collected total, +24 净增；spec §7.3 估算 +25 在 ±2 误差范围内)

- [ ] **F3: drift guard 全 pass**

Run: `pytest tests/test_derive_decision.py::test_t11_adjust_actions_drift_guard tests/test_derive_decision.py::test_t11_protect_actions_drift_guard tests/test_derive_decision.py::test_t11_entry_order_actions_drift_guard tests/test_derive_decision.py::test_t11_leverage_actions_drift_guard tests/test_derive_decision.py::test_t12_derive_output_fits_decision_column tests/test_tool_call_recorder.py::test_biz_error_types_drift_guard tests/test_tool_call_recorder.py::test_tool_calls_status_values_fit_column -v`
Expected: 7 PASS（spec §7.2 G1-G7）

- [ ] **F4: 准备 PR push**

Run: `git push -u origin feature/iter-w2r2-4-biz-error-and-decision-subtypes`
Expected: branch pushed.

- [ ] **F5: 创建 PR**

Run（参考 spec §8.6 PR 模板）:
```bash
gh pr create --title "feat(iter-w2r2-4): biz error metrics + decision subtype derivation" --body "$(cat <<'EOF'
## Summary

R2-4 (W2 prep round 2 第四项)，sim4-issues §P0-1 + §P0-3 修复，必须打包以共享 Alembic migration。

### P0-1 业务失败 metrics 可见
- 加 ContextVar `_biz_error_type` + `note_biz_error()` + `BIZ_ERROR_TYPES` frozenset
- ToolCallRecorder.wrap_tool_execute 改造：handler 返回后读 ContextVar，写 status='biz_error'
- 工具内 instrument: invalid_threshold_range / invalid_alert_id_format / alert_not_found
- LLM 看到的字符串完全不变（fact 透明，零行为改造）
- 拼错 fail-soft（运行期 logger.error + drift guard 测试期 strict）

### P0-3 decision 'adjust' 拆 4 子类
- ADJUST_ACTIONS 拆 PROTECT/ENTRY_ORDER/LEVERAGE/ALERT 4 子集
- 派生优先级 protect > entry_order > leverage > alert
- 派生函数仍 stateless（仅 trade_actions JOIN）
- 历史 'adjust' 行不动 (A 方案)，靠 docs/metrics/decision-enum-timeline.md 文档化

### 不在 scope（已澄清）
- 不 backfill 历史 decision='adjust'
- 不加 metrics §7 #1/#2/#3（触发条件未达成）
- 不改 LLM 看到的工具返回字符串
- 不引入 DB CHECK 约束
- 不一次穷举 19 处字符串失败路径（仅 instrument sim #4 实证 3 处，spec §4.4.3 显式声明盲点）

baseline 940 → target ~964 collected (+24，spec §7.3 估算 +25 在 ±2 误差范围内)。

## Test plan
- [ ] migration upgrade 后 PRAGMA 容量正确（tool_calls.status / decision_logs.decision）
- [ ] migration 不动历史 decision='adjust' 行
- [ ] biz_error 路径全 covered（recorder + 工具端到端 3 处）
- [ ] decision 派生 4 子类全 covered + 优先级矩阵 covered
- [ ] drift guard G1-G7 全 pass
- [ ] sim #4 fdf20e56 e2e scenario passes
- [ ] 全量 pytest 通过

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Plan Review Processing Log

### Round 3 (6 项)

- 🔴 P0 #1 4 项 migration 测试缺 `_create_pre_alembic_schema(db_path)` 调用（Iter 3 migration 是 ALTER 不是 CREATE，空 DB 会 `OperationalError: no such index`）：accept (verified `_create_pre_alembic_schema` 真存在 line 28，path 2 / W1-like 测试都先调用)，4 项测试 + Step 1.1 头部 caveat 全部加 `_create_pre_alembic_schema(db_path)`
- 🟠 P1 #2 Step 3.1 imports 不显式：accept，拆为 Step 3.1a (imports) + Step 3.1b (测试)，明确列出 4 项必须新增的 import line
- 🟠 P1 #3 Final F1 commit 计数遗漏 T0'：accept，Pre-flight 加 P4 commit T0' 步骤 + F1 commit 一览 8 → 9 commit
- 🟡 P2 #4 跨模块 fixture override 风险未提示：accept (verified `test_record_action_cycle_id.py:18-19` 与 `test_tool_call_recorder_args.py:18-19` 同名 fixture 但签名带 tmp_path 不同)，Step 3.0 加 verified 注释 + 单文件回归验证 step
- 🟡 P2 #5 t11 drift guard docstring 语义错（subset 测试不验 ALERT 漂移）：accept，docstring 改为 "G5 (ALERT_ACTIONS) 由 union 间接覆盖"
- 🟡 P2 #6 test_alert_lifecycle 新增缺 @pytest.mark.asyncio：accept (verified file line 20/174/223/234/255 全部显式标注)，3 项新测试加标记

### Round 2 (3 项 — 修复时新引入的轻度问题)

- 🟡 N1 Step 4.6 commit message body 残留旧叙事："t4/t7/t11/t12 既有测试在此 commit 后 FAIL"：accept，同步为"t4/t8.5 在此 commit 后真 FAIL；t7/t11/t12 PASS 但覆盖不全"
- 🟡 N2 Step 3.0 用 `@pytest_asyncio.fixture` 与项目惯例不符：accept (verified: pyproject.toml:36 `asyncio_mode = "auto"` + 全 tests/ 0 处 pytest_asyncio.fixture)，改为 `@pytest.fixture` + 删 `import pytest_asyncio` + 加注释引用 conftest.py:1 已 import pytest
- 🟡 N3 PR 描述 +25 与 plan 实际 +24 不一致：accept，PR 描述 line 1676 改为 `~964 collected (+24, spec §7.3 估算 +25 在 ±2 误差范围内)`

### Round 1 (7 项)

- 🔴 错 1: `tests/test_alembic_migration.py:193` 现有断言 `VARCHAR(20)` → 必须改 `VARCHAR(30)`：accept，T1 File Structure + Step 1.1 末尾加 line 193 修改
- 🔴 错 2: fixture 名 `_alembic_cfg_factory` → 实际 `alembic_cfg_factory` (无下划线，pytest fixture 注入)：accept，4 个新测试签名 + body 全部改正 + 加 Pre-condition 提示
- 🔴 错 3: `ApprovalRequired(tool_call_id=, tool_name=)` 签名错 → pydantic_ai 1.78 实际 `__init__(self, metadata: dict|None = None)`：accept，改为无 args 调用 + caveat 替换为 verified 注释
- 🟠 问题 4: `tests/conftest.py` 缺 `engine` / `session_with_row` fixture，跨 module fixture import 不可行：accept，T3 加 Step 3.0 上提 fixture 到 conftest.py + 删除 test_tool_call_recorder.py 双源
- 🟠 问题 5: T4 后 fail 叙事不准（t11/t12 不会 fail，t7 也不 fail）：accept，Step 4.5 / 5.1 / 5.10 commit message 全部校准为 t4/t8.5 真 fail；t7/t11/t12 不 fail（T5 主动加强覆盖而非修 fail）
- 🟡 问题 6: VARCHAR 断言风格：accept，沿用现有 `==` 精确断言（line 193 惯例）
- 🟡 问题 7: T5 +5 → +4 算术修正：accept，累计 +25 → +24，最终 baseline 940 → ~964（不是 965）。spec §7.3 估算 +25 在 ±2 误差范围内保留作为参考

## Self-Review Checklist (Author run before handoff)

- [x] **Spec coverage 检查**：
  - §1.1 阻塞动机 ✅ T4/T7 e2e 验证
  - §2.1 in scope (P0-1 / P0-3 / Alembic / 文档) ✅ T1/T2/T3/T4/T5/T6
  - §2.2 out of scope ✅ 在 PR 描述中显式 reject
  - §3 决策汇总 12 项 ✅ T1-T7 全覆盖
  - §4.1-4.5 P0-1 设计 ✅ T1 (容量) / T2 (基础设施) / T3 (instrument) / T5 (G7 drift guard)
  - §5.1-5.7 P0-3 设计 ✅ T1 (容量) / T4 (派生) / T5 (既有测试) / T6 (文档锚点)
  - §6 Alembic migration ✅ T1
  - §7 测试策略 ✅ T1/T2/T3/T4/T5/T7 测试矩阵全覆盖
  - §8 实施序 ✅ 本 plan T1-T7
  - §11 自检 + Round 1-5 review 处理记录 ✅ spec 已 landed

- [x] **Placeholder scan**：无 TBD / TODO / "implement later" / "fill in details"。所有代码块完整。

- [x] **Type consistency 检查**：
  - `wrap_tool_execute` 签名（`ctx`, `*`, `call`, `tool_def`, `args`, `handler`）一致
  - `note_biz_error(error_type: str) -> None` 一致
  - `BIZ_ERROR_TYPES: frozenset[str]` 一致
  - PROTECT_ACTIONS / ENTRY_ORDER_ACTIONS / LEVERAGE_ACTIONS / ALERT_ACTIONS 类型 frozenset 一致
  - decision enum 取值在 t12 / e2e / spec 三处一致

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
