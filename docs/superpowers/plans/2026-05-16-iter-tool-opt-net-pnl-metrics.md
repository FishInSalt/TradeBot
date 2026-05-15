# Iter tool-opt-net-pnl-metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PnL metrics 净值化重构——`MetricsService.compute()` 改用 FIFO lot pairing（与 `scripts/_sim_metrics.collect_roundtrips` 同算法），提供 gross + net 并列双视角；max_drawdown 切 net equity；`pnl_pct` 双语义双命名。

**Architecture:** `trade_actions` 表加 2 列（`entry_price` + `amount`），由 `_record_action_from_fill` 写入；`compute()` 内部 FIFO 重建 lot queue；`get_performance` 输出 gross/net 并列；OKX cache miss `entry_price=NULL` 不阻塞算法。

**Tech Stack:** Python 3.13 / pytest / SQLAlchemy 2.x / alembic / pydantic-ai（既有）

**Spec reference:** `docs/superpowers/specs/2026-05-16-iter-tool-opt-net-pnl-metrics-design.md`

---

## Convention — fixture & test path

测试目录是**扁平结构**（`tests/test_*.py`，无子目录）。本 plan 引用既有 fixture：

- **`engine`** (`tests/conftest.py:26`) — in-memory SQLite + `init_db` (Path 3，含 `_apply_views`)；适合纯算法/CRUD 测试；速度最快。
- **`db_engine`** (`tests/conftest.py:91`) — tmp_path 真 DB + Phase 1 head + 3 views；适合需要持久化测试。
- **`db_engine_with_real_db`** (`tests/conftest.py:170`) — **不要用于本 iter migration test**。该 fixture 依赖 `data/tradebot.db`（gitignored），missing 即 `pytest.skip(...)` → CI 静默 skip。仅用于"production DB 历史兼容" 类测试。

**默认选 `engine`**（不需要持久化的算法/单元测试）。**Migration test (Task 1) 自建 head_db / pre_iter_head_db fixture**（仿 `tests/test_alembic_p4.py:22-48` pattern：init_db Path 3 bootstrap + subprocess alembic downgrade/upgrade，**不依赖外部 DB**）。

---

## File Structure

**Commit boundary 警告**：Task 1 (view DDL JSON path 改) 与 Task 2 (cycle_capture write key 改) **必须按顺序连续完成**；Task 1 落地后未做 Task 2 期间 live agent 跑 cycle 会让 `v_cycle_metrics.position_pnl_pct` 列对新行返回 NULL（view 读 `pnl_pct_of_notional` 但 cycle_capture 还在写 `pnl_pct`）。如该窗口很短（同一会话连续 commit + squash-merge to main）影响可忽略；若分会话执行，建议 Task 1 commit message 标注 "Run no live agent until Task 2 lands" 或合并 Task 1+2 为单一 atomic commit。

**Modify**:
- `src/storage/models.py` — TradeAction +2 字段
- `src/storage/views.py` — v_cycle_metrics JSON 路径 rename
- `src/services/metrics.py` — compute() FIFO 重构 + PerformanceMetrics +13 字段
- `src/services/cycle_capture.py` — JSON key rename
- `src/cli/app.py` — `_record_action_from_fill` 写新字段
- `src/cli/display.py` — read key rename + profit_factor None handling
- `src/agent/tools_perception.py` — get_performance 重写 + OKX import + profit_factor None
- `scripts/_sim_metrics.py` — 加 gross 视角 metric functions
- `scripts/analyze_sim.py` — 报表 column 加 gross
- `scripts/diff_sim.py` — diff 输出加 gross

**Create**:
- `alembic/versions/7d4f8e9a2b1c_iter_net_pnl_metrics.py` — migration
- `tests/test_alembic_net_pnl_metrics.py` — migration test
- `tests/test_metrics_fifo.py` — FIFO 算法测试
- `tests/test_metrics_src_scripts_parity.py` — drift guard

**Modify (test)**:
- `tests/test_metrics.py` — compute() integration + 移除 `profit_factor > 1.0` 老断言
- `tests/test_cycle_capture.py` — 既有 `p["pnl_pct"]` 断言改为 `pnl_pct_of_notional`（line 106 + 220 + 95 docstring）
- `tests/test_display_cycle.py` — read key 更新
- `tests/test_v_cycle_metrics.py` — view JSON 路径断言
- `tests/test_get_performance.py` — 6 处 `(gross-based)` 老断言移除 / 改为 `gross / net` 双视角断言
- `tests/test_cli_app.py` — `_record_action_from_fill` 新字段断言
- `tests/test_cli.py` — `profit_factor=1.8` 测试参数仍兼容
- `tests/test_drift_phase2_metrics.py` — schema 断言名单更新（如有）
- `tests/test_analyze_sim.py` — 报表 column 名单更新

---

## Task 1: Alembic Migration + Schema (atomic schema commit)

**Files:**
- Create: `alembic/versions/<NEW_REV_ID>_iter_net_pnl_metrics.py`（`<NEW_REV_ID>` 用 `alembic revision -m "iter net pnl metrics"` 生成的真 hash，避免编造 ID 撞库；下方占位的 `7d4f8e9a2b1c` 全部替换为实际生成值）
- Create: `tests/test_alembic_net_pnl_metrics.py`
- Modify: `src/storage/models.py:58-76` (TradeAction)
- Modify: `src/storage/views.py:62` (v_cycle_metrics JSON 路径)

- [ ] **Step 1: Write failing migration tests**

Create `tests/test_alembic_net_pnl_metrics.py` using `test_alembic_p4.py:22-48` fixture pattern (no external DB dependency):

```python
"""Iter net-pnl-metrics migration test (仿 test_alembic_p4.py pattern, no data/tradebot.db dep)."""
from __future__ import annotations

import os
import subprocess
import sqlite3

import pytest


PRE_ITER_REV = "4ee6c95d0430"   # alembic head before this iter (P4 prompt snapshot)


@pytest.fixture
async def head_db(tmp_path):
    """Bootstrap fresh DB at current head via init_db (Path 3 — auto-stamps head)."""
    from src.storage.database import init_db
    db_path = tmp_path / "net_pnl_metrics.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    return str(db_path), env


@pytest.fixture
async def pre_iter_head_db(tmp_path):
    """Bootstrap fresh DB then explicitly downgrade to pre-iter head — forward
    upgrade exercises this iter's migration.upgrade() path."""
    from src.storage.database import init_db
    db_path = tmp_path / "pre_iter_net_pnl.db"
    db_url_async = f"sqlite+aiosqlite:///{db_path}"
    engine = await init_db(db_url_async)
    await engine.dispose()
    env = {**os.environ, "TRADEBOT_DB_URL": db_url_async}
    subprocess.run(
        ["alembic", "downgrade", PRE_ITER_REV],
        check=True, env=env, capture_output=True,
    )
    return str(db_path), env


async def test_head_has_entry_price_amount_columns(head_db):
    """At current head: trade_actions 有 entry_price + amount 两列."""
    db, _ = head_db
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "entry_price" in cols, f"entry_price missing; have {sorted(cols)}"
    assert "amount" in cols, f"amount missing; have {sorted(cols)}"


async def test_head_view_uses_pnl_pct_of_notional_path(head_db):
    """v_cycle_metrics DDL 引用 $.position.pnl_pct_of_notional."""
    db, _ = head_db
    conn = sqlite3.connect(db)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_cycle_metrics'"
    ).fetchone()[0]
    assert "pnl_pct_of_notional" in sql, (
        f"view DDL should reference pnl_pct_of_notional; got:\n{sql}"
    )


async def test_upgrade_preserves_pre_iter_legacy_rows(pre_iter_head_db):
    """Real `alembic upgrade head`: pre-iter trade_actions 行 entry_price/amount=NULL after migration.

    Verifies upgrade() runs forward + does NOT backfill (spec §6.11 by design).
    """
    db, env = pre_iter_head_db

    # Pre-condition: at PRE_ITER_REV, trade_actions has no entry_price/amount cols
    conn = sqlite3.connect(db)
    cols_before = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "amount" not in cols_before, (
        f"Pre-condition: amount should not exist at pre-iter head; cols: {sorted(cols_before)}"
    )
    assert "entry_price" not in cols_before

    # Insert legacy row at pre-iter schema (no new columns).
    # Must enumerate all 12 NOT NULL Session cols (Python defaults bypass via raw SQL;
    # per tests/test_alembic_migration.py:262 pattern).
    conn.execute("""
        INSERT INTO sessions
        (id, name, symbol, initial_balance, status, created_at, updated_at,
         exchange_type, timeframe, scheduler_interval_min, approval_enabled, token_budget)
        VALUES ('legacy-test', 'legacy', 'BTC/USDT:USDT', 10000.0, 'active',
                '2026-01-01T00:00:00', '2026-01-01T00:00:00',
                'simulated', '15m', 15, 1, 500000)
    """)
    # trade_actions: session_id + action + symbol NOT NULL; created_at default _utcnow Python-side
    conn.execute(
        "INSERT INTO trade_actions (session_id, action, symbol, price, pnl, fee, created_at) "
        "VALUES ('legacy-test', 'order_filled', 'BTC/USDT:USDT', 50000.0, 10.0, 0.25, '2026-01-01T00:00:00')"
    )
    conn.commit()
    conn.close()

    # Forward upgrade — runs this iter's migration.upgrade()
    subprocess.run(["alembic", "upgrade", "head"], check=True, env=env, capture_output=True)

    # Post-condition: new columns exist, legacy row has NULL
    conn = sqlite3.connect(db)
    cols_after = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "amount" in cols_after, f"upgrade did not add amount; cols: {sorted(cols_after)}"
    assert "entry_price" in cols_after, f"upgrade did not add entry_price; cols: {sorted(cols_after)}"

    legacy = conn.execute(
        "SELECT entry_price, amount FROM trade_actions WHERE session_id='legacy-test'"
    ).fetchone()
    assert legacy == (None, None), (
        f"legacy row should preserve NULL after upgrade (no backfill); got {legacy}"
    )

    # v_cycle_metrics view must be present after upgrade (rebuilt by migration)
    view_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_cycle_metrics'"
    ).fetchone()[0]
    assert "pnl_pct_of_notional" in view_sql, "view should reference new JSON path after upgrade"


async def test_downgrade_drops_new_columns_and_restores_view(head_db):
    """alembic downgrade -1: 2 columns removed; view recreated with pre-iter JSON path."""
    db, env = head_db
    subprocess.run(["alembic", "downgrade", "-1"], check=True, env=env, capture_output=True)

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_actions)")}
    assert "amount" not in cols, f"downgrade should drop amount; cols: {sorted(cols)}"
    assert "entry_price" not in cols

    view_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND name='v_cycle_metrics'"
    ).fetchone()[0]
    assert "pnl_pct_of_notional" not in view_sql, (
        "downgrade should restore pre-iter view DDL (no pnl_pct_of_notional)"
    )
    assert "pnl_pct" in view_sql, "downgrade should restore '$.position.pnl_pct' JSON path"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_alembic_net_pnl_metrics.py -v`
Expected at this stage（pre-migration impl）：
- `test_head_has_entry_price_amount_columns` → **FAIL**（cols 不存在）
- `test_head_view_uses_pnl_pct_of_notional_path` → **FAIL**（view DDL 还是旧 path）
- `test_upgrade_preserves_pre_iter_legacy_rows` → **FAIL**（pre_iter_head_db fixture 的 `alembic downgrade` 命令找不到 PRE_ITER_REV 之前的 rev，或者 upgrade 还没新 migration 可跑）
- `test_downgrade_drops_new_columns_and_restores_view` → **PASS（trivially）**：当前 head 就是 PRE_ITER_REV，downgrade -1 退到 phase1 rev，新 cols 本就不存在；这是 fixture 时序的 false-pass，post-impl 才真正验证 downgrade 路径。可接受。

> **加固建议**：在 test_downgrade_drops_new_columns_and_restores_view docstring 内 inline 加一行：
> `"NOTE: Expected to false-pass before migration impl. Verify by `git status` shows new migration file before treating this test as meaningful."`

- [ ] **Step 3: Update TradeAction model**

Edit `src/storage/models.py`. Find `class TradeAction` (line 58) and add 2 fields after `fee` (line 75):

```python
class TradeAction(Base):
    """Agent 的交易操作日志 — append-only 事件模型.

    iter-tool-opt-net-pnl-metrics 字段范围说明:
    - amount: 所有 action='order_filled' 行（open + close）有值（per FillEvent.amount 必填）；
              非 fill 行（cancel / submit 等由 tools_execution._record_action 写）NULL by design.
    - entry_price: open fill 行永远 NULL（per FillEvent.entry_price 设计 "open fill 永远 None"，
                   base.py:349-360）；close fill 行通常有值，OKX cache miss 时可 NULL
                   （继承 fee_visibility iter limitation；详见 spec §6.5）；非 fill 行 NULL by design.
    """

    __tablename__ = "trade_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action: Mapped[str] = mapped_column(String(30))
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    alert_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trigger_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 4: Update views.py v_cycle_metrics DDL**

Edit `src/storage/views.py` line 62. Find:

```python
  CAST(json_extract(ac.state_snapshot, '$.position.pnl_pct')        AS REAL)    AS position_pnl_pct,
```

Replace with:

```python
  CAST(json_extract(ac.state_snapshot, '$.position.pnl_pct_of_notional')  AS REAL)    AS position_pnl_pct,
```

- [ ] **Step 5: Extract pre-iter views.py SQL for downgrade**

The migration `downgrade()` must reproduce the exact pre-iter `V_CYCLE_METRICS_SQL`. Extract it verbatim:

```bash
git show e7f7e78:src/storage/views.py > /tmp/views_pre_iter.py
# Inspect /tmp/views_pre_iter.py to copy V_CYCLE_METRICS_SQL string literal.
```

You'll use that whole `V_CYCLE_METRICS_SQL` string (~70 行 SQL，含 CTE
ac_with_anchors + 5 has_* anchor cols + tool_total_ms 子查询 +
cache_hit_rate_derived + pending_orders_count etc.)
verbatim in the migration's `downgrade()` step. **Do NOT abbreviate** — the
view has ~30 columns + CTE `ac_with_anchors` + 5 `has_*` anchor columns +
subqueries; missing any will break consumers.

- [ ] **Step 6: Create alembic migration file**

Create `alembic/versions/7d4f8e9a2b1c_iter_net_pnl_metrics.py`:

```python
"""iter-tool-opt-net-pnl-metrics: trade_actions amount + entry_price + view DDL update

Revision ID: 7d4f8e9a2b1c
Revises: 4ee6c95d0430
Create Date: 2026-05-16 12:00:00.000000

Adds two nullable columns to trade_actions (per spec §C0/§C1) and rebuilds
v_cycle_metrics view to read $.position.pnl_pct_of_notional (per spec §C0/§C6).

Legacy rows (pre-migration) keep entry_price/amount as NULL by design;
their v_cycle_metrics.position_pnl_pct column returns NULL after this
migration (per spec §6.11; not backfilled).
"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from src.storage.views import V_CYCLE_METRICS_SQL


revision: str = "7d4f8e9a2b1c"
down_revision: str | None = "4ee6c95d0430"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


# Pre-iter V_CYCLE_METRICS_SQL (copy verbatim from `git show e7f7e78:src/storage/views.py`).
# Used by downgrade() to recreate the view exactly as it was before this migration.
# WARNING: maintain in sync with that historical version; do NOT use abbreviated SQL.
_V_CYCLE_METRICS_SQL_PRE_ITER = """
<<< paste full V_CYCLE_METRICS_SQL string content from git show e7f7e78:src/storage/views.py here >>>
"""


def upgrade() -> None:
    with op.batch_alter_table("trade_actions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("amount", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("entry_price", sa.Float, nullable=True))

    op.execute("DROP VIEW IF EXISTS v_cycle_metrics")
    op.execute(V_CYCLE_METRICS_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_cycle_metrics")
    op.execute(_V_CYCLE_METRICS_SQL_PRE_ITER)

    with op.batch_alter_table("trade_actions", schema=None) as batch_op:
        batch_op.drop_column("entry_price")
        batch_op.drop_column("amount")
```

After writing, paste pre-iter SQL into the `_V_CYCLE_METRICS_SQL_PRE_ITER` triple-quoted string. Verify it's syntactically valid SQL (sqlite-compatible).

- [ ] **Step 7: Run migration tests to verify pass**

Run: `pytest tests/test_alembic_net_pnl_metrics.py -v`
Expected: PASS

- [ ] **Step 8: Run full test suite for regressions**

Run: `pytest -x -q 2>&1 | tail -30`
Expected: All tests pass.

If `tests/test_v_cycle_metrics.py` references the JSON path, update assertions there to `pnl_pct_of_notional`.

- [ ] **Step 9: Commit**

```bash
git add tests/test_alembic_net_pnl_metrics.py alembic/versions/7d4f8e9a2b1c_iter_net_pnl_metrics.py \
        src/storage/models.py src/storage/views.py tests/test_v_cycle_metrics.py
git commit -m "$(cat <<'EOF'
feat(iter-tool-opt-net-pnl-metrics): alembic migration + TradeAction schema

trade_actions +2 nullable columns (amount, entry_price) per spec §C0/§C1.
v_cycle_metrics view rebuilt to read $.position.pnl_pct_of_notional
(aligned with forthcoming cycle_capture JSON key rename).

Legacy rows preserved as NULL by design (spec §6.11; not backfilled).
Downgrade SQL inlines pre-iter V_CYCLE_METRICS_SQL verbatim from
git show e7f7e78:src/storage/views.py.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: cycle_capture + display.py JSON key rename (atomic data path)

**Files:**
- Modify: `src/services/cycle_capture.py:124,133` (write `pnl_pct_of_notional`)
- Modify: `src/cli/display.py:717` (read `pnl_pct_of_notional`)
- Modify: `tests/test_cycle_capture.py:106,220,95` (既有 `p["pnl_pct"]` 断言)
- Modify: `tests/test_display_cycle.py` (if references `pnl_pct` key)
- Modify: `tests/test_v_cycle_metrics.py:39` (if asserts on JSON path)

- [ ] **Step 1: Update existing test_cycle_capture.py assertions**

Edit `tests/test_cycle_capture.py`. Three references:

Line 95 docstring (`pnl_pct 衍生计算`) → `pnl_pct_of_notional 衍生计算`

Line 106 `assert p["pnl_pct"] == pytest.approx(0.0618, rel=1e-3)` → `assert p["pnl_pct_of_notional"] == pytest.approx(0.0618, rel=1e-3)`

Line 220 `assert snap["position"]["pnl_pct"] is None` → `assert snap["position"]["pnl_pct_of_notional"] is None`

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_cycle_capture.py -v -k "T-SS-2 or T-SS-10"`
Expected: FAIL (key still pnl_pct on the production side)

- [ ] **Step 3: Modify cycle_capture.py**

Edit `src/services/cycle_capture.py`. Find lines 124 + 133:

```python
            pnl_pct = (p.unrealized_pnl / notional * 100) if notional > 0 else None
            snapshot["position"] = {
                ...
                "pnl_pct": pnl_pct,
            }
```

Replace with:

```python
            pnl_pct_of_notional = (p.unrealized_pnl / notional * 100) if notional > 0 else None
            snapshot["position"] = {
                ...
                "pnl_pct_of_notional": pnl_pct_of_notional,
            }
```

- [ ] **Step 4: Modify display.py**

Edit `src/cli/display.py` line 717:

```python
            pnl_pct = pos.get("pnl_pct")
```

Replace with:

```python
            pnl_pct = pos.get("pnl_pct_of_notional")
```

- [ ] **Step 5: Update test_display_cycle.py if needed**

Run: `grep -n "pnl_pct" tests/test_display_cycle.py`

If matches, rename to `pnl_pct_of_notional` accordingly.

- [ ] **Step 6: Run tests to verify pass**

Run: `pytest tests/test_cycle_capture.py tests/test_display_cycle.py tests/test_v_cycle_metrics.py -v`
Expected: PASS

- [ ] **Step 7: Run full suite for regressions**

Run: `pytest -x -q 2>&1 | tail -30`
Expected: All pass.

- [ ] **Step 8: Commit**

```bash
git add src/services/cycle_capture.py src/cli/display.py \
        tests/test_cycle_capture.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat(iter-tool-opt-net-pnl-metrics): rename pnl_pct → pnl_pct_of_notional

cycle_capture writes new JSON key; display.py reads same; v_cycle_metrics
view (from Task 1) already aligned. Per spec §A3: explicit dual semantics
(pnl_pct_of_capital in tools / pnl_pct_of_notional in snapshot).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: _record_action_from_fill writes amount + entry_price

**Files:**
- Modify: `src/cli/app.py:418-433`
- Modify: `tests/test_cli_app.py` (add tests for new fields)

- [ ] **Step 1: Locate existing _record_action_from_fill tests**

Run: `grep -n "_record_action_from_fill\|record_action_from_fill" tests/test_cli_app.py`

Note the existing test setup pattern (fixtures, helper functions).

- [ ] **Step 2: Add failing tests**

Append to `tests/test_cli_app.py`:

```python
@pytest.mark.asyncio
async def test_record_action_open_fill_writes_amount_no_entry_price(engine, session_with_row):
    """spec §C2: open fill 行写 amount，entry_price=NULL（FillEvent 设计）."""
    from src.cli.app import _record_action_from_fill
    from src.integrations.exchange.base import FillEvent
    from src.storage.database import get_session
    from src.storage.models import TradeAction
    from sqlalchemy import select

    event = FillEvent(
        order_id="o-open", symbol="BTC/USDT:USDT",
        side="buy", position_side="long",
        trigger_reason="market", fill_price=50000.0, amount=0.1, fee=2.5,
        pnl=None, timestamp=1700_000_000_000, is_full_close=False,
        entry_price=None,  # open fill always None
    )
    await _record_action_from_fill(engine, session_with_row, event)

    async with get_session(engine) as session:
        row = (await session.execute(
            select(TradeAction).where(TradeAction.session_id == session_with_row)
            .where(TradeAction.order_id == "o-open")
        )).scalars().one()
    assert row.amount == 0.1
    assert row.entry_price is None
    assert row.pnl is None


@pytest.mark.asyncio
async def test_record_action_close_fill_writes_amount_and_entry_price(engine, session_with_row):
    """spec §C2: close fill 行写 amount + entry_price."""
    from src.cli.app import _record_action_from_fill
    from src.integrations.exchange.base import FillEvent
    from src.storage.database import get_session
    from src.storage.models import TradeAction
    from sqlalchemy import select

    event = FillEvent(
        order_id="o-close", symbol="BTC/USDT:USDT",
        side="sell", position_side="long",
        trigger_reason="market", fill_price=51000.0, amount=0.1, fee=2.55,
        pnl=100.0, timestamp=1700_000_001_000, is_full_close=True,
        entry_price=50000.0,
    )
    await _record_action_from_fill(engine, session_with_row, event)

    async with get_session(engine) as session:
        row = (await session.execute(
            select(TradeAction).where(TradeAction.session_id == session_with_row)
            .where(TradeAction.order_id == "o-close")
        )).scalars().one()
    assert row.amount == 0.1
    assert row.entry_price == 50000.0
    assert row.pnl == 100.0


@pytest.mark.asyncio
async def test_record_action_okx_cache_miss_close_entry_price_null(engine, session_with_row):
    """spec §6.5: OKX cache miss close fill — entry_price=None (algorithm continues)."""
    from src.cli.app import _record_action_from_fill
    from src.integrations.exchange.base import FillEvent
    from src.storage.database import get_session
    from src.storage.models import TradeAction
    from sqlalchemy import select

    event = FillEvent(
        order_id="o-miss", symbol="BTC/USDT:USDT",
        side="sell", position_side="long",
        trigger_reason="liquidation", fill_price=45000.0, amount=0.05, fee=1.125,
        pnl=-250.0, timestamp=1700_000_002_000, is_full_close=False,
        entry_price=None,  # cache miss
    )
    await _record_action_from_fill(engine, session_with_row, event)

    async with get_session(engine) as session:
        row = (await session.execute(
            select(TradeAction).where(TradeAction.session_id == session_with_row)
            .where(TradeAction.order_id == "o-miss")
        )).scalars().one()
    assert row.amount == 0.05
    assert row.entry_price is None
    assert row.pnl == -250.0
```

- [ ] **Step 3: Run tests to verify failure**

Run: `pytest tests/test_cli_app.py -v -k "test_record_action"`
Expected: FAIL（amount/entry_price 都 None — 字段未写入）

- [ ] **Step 4: Modify _record_action_from_fill**

Edit `src/cli/app.py` lines 418-433. Replace:

```python
async def _record_action_from_fill(engine, session_id, event: FillEvent):
    """将 FillEvent 记录为 TradeAction。

    iter-tool-opt-net-pnl-metrics: 同步写 amount + entry_price
    （per spec §C2 / §6.5 OKX cache miss 时 entry_price 可 NULL）.
    """
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id=session_id,
            action="order_filled",
            order_id=event.order_id,
            symbol=event.symbol,
            side=event.position_side,
            trigger_reason=event.trigger_reason,
            price=event.fill_price,
            pnl=event.pnl,
            fee=event.fee,
            amount=event.amount,
            entry_price=event.entry_price,
            reasoning=f"(exchange: {event.trigger_reason} order filled @ {event.fill_price:.2f})",
        ))
        await session.commit()
```

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/test_cli_app.py -v -k "test_record_action"`
Expected: PASS（3 个新测试）

- [ ] **Step 6: Run full suite for regressions**

Run: `pytest -x -q 2>&1 | tail -30`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/cli/app.py tests/test_cli_app.py
git commit -m "$(cat <<'EOF'
feat(iter-tool-opt-net-pnl-metrics): _record_action_from_fill writes amount + entry_price

Both fields persisted from FillEvent. amount always set on order_filled rows;
entry_price NULL on open fills (FillEvent design) and OKX cache miss closes
(spec §6.5). Unblocks FIFO algorithm in MetricsService.compute().

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: PerformanceMetrics dataclass + profit_factor None code updates

**Files:**
- Modify: `src/services/metrics.py:15-32` (PerformanceMetrics dataclass)
- Modify: `src/cli/display.py:31` (profit_factor None handling)
- Modify: `src/agent/tools_perception.py:610` (profit_factor None comparison)
- Modify: `src/agent/tools_perception.py:743` (profit_factor None comparison)
- Modify: `tests/test_metrics.py:51` (assertion update)

**Profit_factor 类型切换说明**：spec §2 决策 zero-denom 统一为 None；现有 src 实现用 `float("inf")`。本 task 切换需要同步更新 3 处下游消费者：

| 文件 | 行 | 当前 | 新 |
|---|---|---|---|
| `src/cli/display.py` | 31 | `metrics.profit_factor:.2f` | None-guarded format |
| `src/agent/tools_perception.py` | 610 | `metrics.profit_factor == float('inf')` | `is None` |
| `src/agent/tools_perception.py` | 743 | `metrics.profit_factor == float('inf')` | `is None`（与 line 610 同 commit 改；**不**延后到 Task 8 — 否则 Task 7 commit 后 compute() 返回 None，line 743 走 else 分支 `f'{None:.2f}'` 在 no-loss session 上 TypeError crash）|

- [ ] **Step 1: Write failing test for new dataclass fields**

Append to `tests/test_metrics.py`:

```python
def test_performance_metrics_has_net_fields():
    """spec §C3: PerformanceMetrics +7 net 字段 + 2 计数字段 + 4 caveat."""
    from src.services.metrics import PerformanceMetrics
    pm = PerformanceMetrics()
    # 7 net metric fields
    assert pm.net_pnl == 0.0
    assert pm.net_profit_factor is None
    assert pm.net_win_rate == 0.0
    assert pm.avg_win_net == 0.0
    assert pm.avg_loss_net == 0.0
    assert pm.best_trade_net == 0.0
    assert pm.worst_trade_net == 0.0
    # 2 count fields
    assert pm.net_winning_trades == 0
    assert pm.net_losing_trades == 0
    # 4 caveat counters
    assert pm.legacy_open_skipped == 0
    assert pm.legacy_close_skipped == 0
    assert pm.missing_close_entry_price_count == 0
    assert pm.invariant_violations == 0


def test_performance_metrics_profit_factor_default_none():
    """spec §2 zero-denom decision: PF default None (was 0.0/inf)."""
    from src.services.metrics import PerformanceMetrics
    pm = PerformanceMetrics()
    assert pm.profit_factor is None


# Issue 2 / 8 防御：Task 4 line 610/743 同步切 is None 已消除 Task 4↔Task 7
# transient crash 窗口；Task 7 加 test_compute_profit_factor_none_on_zero_losses
# 验证 metrics 层 emit None；Task 8 重写后 deps_with_one_winning_trade fixture
# 触发 get_performance 渲染 PF（虽 PF 非 None，但渲染路径已用 None-guard 写法
# 不会 crash）。无需新加 render-layer no-loss 集成测试。
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_metrics.py -v -k "performance_metrics_has_net_fields or profit_factor_default"`
Expected: FAIL

- [ ] **Step 3: Modify PerformanceMetrics dataclass**

Edit `src/services/metrics.py` lines 15-32. Replace with:

```python
@dataclass
class PerformanceMetrics:
    # Gross metrics (existing — per-lot-pair semantics shift per spec §0)
    total_return_pct: float = 0.0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float | None = None  # zero-denom → None per spec §2
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    current_position: str = "none"
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    recent_summary: str = ""
    total_fees: float = 0.0
    # Net metrics (iter-tool-opt-net-pnl-metrics — per spec §C3)
    net_pnl: float = 0.0
    net_profit_factor: float | None = None
    net_win_rate: float = 0.0
    avg_win_net: float = 0.0
    avg_loss_net: float = 0.0
    best_trade_net: float = 0.0
    worst_trade_net: float = 0.0
    net_winning_trades: int = 0
    net_losing_trades: int = 0
    # Caveats (per spec §6.2)
    legacy_open_skipped: int = 0
    legacy_close_skipped: int = 0
    missing_close_entry_price_count: int = 0
    invariant_violations: int = 0
```

- [ ] **Step 4: Update tests/test_metrics.py:51 老 PF 断言**

Edit `tests/test_metrics.py` line 51 `assert metrics.profit_factor > 1.0`：

需要根据 test 上下文判断。如果 fixture 产出 win+loss 都有 → PF 是浮点数，断言仍成立（只是改 type 不破语义）。如果 fixture 是 wins-only → PF=None，断言需改为 `is None`。

Read context around line 51 first:

```bash
sed -n '40,55p' tests/test_metrics.py
```

Update assertion if needed based on fixture context.

- [ ] **Step 5: Update src/cli/display.py:31 PF None handling**

Edit `src/cli/display.py` line 31. Find:

```python
        f"Profit Factor: {metrics.profit_factor:.2f}\n"
```

Replace with:

```python
        f"Profit Factor: {'N/A (no losses)' if metrics.profit_factor is None else f'{metrics.profit_factor:.2f}'}\n"
```

- [ ] **Step 6: Update src/agent/tools_perception.py:610 + line 743 PF None handling**

**两处 PF 比较必须同一 commit 修复**——避免 Task 7 commit 后、Task 8 commit 前的 transient crash 窗口期（line 743 在 no-loss session 上 TypeError）。

Edit `src/agent/tools_perception.py`:

**Line 610** (get_trade_journal output)：

```python
                f"Profit Factor: {'N/A (no losses)' if metrics.profit_factor == float('inf') else f'{metrics.profit_factor:.2f}'}",
```

Replace with:

```python
                f"Profit Factor: {'N/A (no losses)' if metrics.profit_factor is None else f'{metrics.profit_factor:.2f}'}",
```

**Line 743** (get_performance gross-based legacy text — 本行 Task 8 整体重写时会再次替换，但 None-guard 部分必须先到位)：

```python
        f"{'N/A (no losses)' if metrics.profit_factor == float('inf') else f'{metrics.profit_factor:.2f} (gross-based)'}\n"
```

Replace with:

```python
        f"{'N/A (no losses)' if metrics.profit_factor is None else f'{metrics.profit_factor:.2f} (gross-based)'}\n"
```

- [ ] **Step 7: Run dataclass tests + PF None handling tests**

Run: `pytest tests/test_metrics.py tests/test_cli.py -v -k "performance_metrics_has_net or profit_factor_default or profit_factor"`
Expected: PASS

- [ ] **Step 8: Run full suite for regressions**

Run: `pytest -x -q 2>&1 | tail -30`
Expected: All pass.

Note: any tests asserting `profit_factor == float('inf')` will FAIL — update to `is None`. Grep first:

```bash
grep -rn "profit_factor == float" tests/ --include="*.py"
```

- [ ] **Step 9: Commit**

```bash
git add src/services/metrics.py src/cli/display.py src/agent/tools_perception.py \
        tests/test_metrics.py
git commit -m "$(cat <<'EOF'
feat(iter-tool-opt-net-pnl-metrics): extend PerformanceMetrics + profit_factor → None convention

+9 net fields + 4 caveat counters per spec §C3.
profit_factor type: float → float | None (zero-denom 统一 None，aligned with scripts).
Updates display.py + tools_perception.py PF comparison/format sites.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: FIFO algorithm helper — `_collect_roundtrips_from_trade_actions`

**Files:**
- Modify: `src/services/metrics.py` (add `_Lot`, `_Roundtrip`, `_collect_roundtrips_from_trade_actions`)
- Create: `tests/test_metrics_fifo.py`

**Helper signature note**: fee_rate **不**作为参数传入 helper —— FIFO 用 `lot.open_fee`（来自 `trade_actions.fee`，CCXT/sim 实测值）和 `fill.fee` 直接计算分摊，无需 fee_rate。fee_rate fetch + warning 保留在 `compute()` 层（spec §6.1 informational 用途）。

- [ ] **Step 1: Write failing tests — single open + single close**

Create `tests/test_metrics_fifo.py`:

```python
"""FIFO lot pairing algorithm tests (spec §5.2)."""
from __future__ import annotations

import pytest
from sqlalchemy import text


# NOTE: Raw SQL helpers must enumerate ALL NOT NULL columns explicitly — Session has 8
# NOT NULL cols with Python-side default= that raw SQL bypasses (per
# tests/test_alembic_migration.py:262 precedent). created_at default same caveat for
# trade_actions. Reuse SQLAlchemy ORM session.add() instead if simpler.


async def _insert_session(conn, sid: str, fee_rate: float | None = 0.0005):
    """Raw SQL session insert — enumerates 12 NOT NULL cols + fee_rate."""
    fr_clause = "NULL" if fee_rate is None else str(fee_rate)
    await conn.execute(text(
        f"INSERT INTO sessions "
        f"(id, name, symbol, initial_balance, status, created_at, updated_at, "
        f" exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
        f" token_budget, fee_rate) "
        f"VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
        f"        '2026-01-01T00:00:00', '2026-01-01T00:00:00', "
        f"        'simulated', '15m', 15, 1, 500000, {fr_clause})"
    ), {"sid": sid})


async def _insert_fill(conn, sid: str, **kwargs):
    """Raw SQL trade_actions insert. Caller provides side / price / amount / fee / pnl / etc.

    Auto-applies created_at default (raw SQL doesn't trigger Python-side _utcnow).
    """
    defaults = {
        "session_id": sid, "action": "order_filled",
        "symbol": "BTC/USDT:USDT", "trigger_reason": "market",
        "created_at": "2026-01-01T00:00:00",
    }
    defaults.update(kwargs)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join(f":{k}" for k in defaults.keys())
    await conn.execute(text(f"INSERT INTO trade_actions ({cols}) VALUES ({placeholders})"), defaults)


@pytest.mark.asyncio
async def test_fifo_single_open_single_close(engine):
    """One open lot fully consumed by one close → 1 roundtrip."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions

    sid = "fifo-test-1"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.1,
                           fee=2.5, pnl=None, entry_price=None)
        await _insert_fill(conn, sid, side="long", price=51000.0, amount=0.1,
                           fee=2.55, pnl=100.0, entry_price=50000.0)

    rts, caveats = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 1
    assert rts[0].pnl_gross == pytest.approx(100.0)
    assert rts[0].fee_open_share == pytest.approx(2.5)
    assert rts[0].fee_close_share == pytest.approx(2.55)
    assert rts[0].pnl_net == pytest.approx(100.0 - 2.5 - 2.55)
    assert caveats == {"legacy_open_skipped": 0, "legacy_close_skipped": 0,
                       "missing_close_entry_price_count": 0, "invariant_violations": 0}


@pytest.mark.asyncio
async def test_fifo_partial_close_twice(engine):
    """One open, two partial closes → 2 roundtrips sharing open lot."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions

    sid = "fifo-test-2"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.1,
                           fee=2.5, pnl=None, entry_price=None)
        await _insert_fill(conn, sid, side="long", price=51000.0, amount=0.05,
                           fee=1.275, pnl=50.0, entry_price=50000.0)
        await _insert_fill(conn, sid, side="long", price=49500.0, amount=0.05,
                           fee=1.2375, pnl=-25.0, entry_price=50000.0)

    rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 2
    assert rts[0].fee_open_share == pytest.approx(1.25)   # 2.5 * 0.05/0.1
    assert rts[0].pnl_gross == pytest.approx(50.0)
    assert rts[1].fee_open_share == pytest.approx(1.25)
    assert rts[1].pnl_gross == pytest.approx(-25.0)


@pytest.mark.asyncio
async def test_fifo_multi_open_single_close(engine):
    """Two opens at different prices, single close consumes both → 2 roundtrips."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions

    sid = "fifo-test-3"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.1,
                           fee=2.5, pnl=None, entry_price=None)
        await _insert_fill(conn, sid, side="long", price=52000.0, amount=0.1,
                           fee=2.6, pnl=None, entry_price=None)
        await _insert_fill(conn, sid, side="long", price=53000.0, amount=0.2,
                           fee=5.3, pnl=400.0, entry_price=51000.0)

    rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 2
    assert rts[0].pnl_gross == pytest.approx(300.0)  # (53000-50000)*0.1
    assert rts[1].pnl_gross == pytest.approx(100.0)  # (53000-52000)*0.1
    assert rts[0].fee_close_share == pytest.approx(2.65)  # 5.3 * 0.1/0.2
    assert rts[1].fee_close_share == pytest.approx(2.65)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_metrics_fifo.py -v`
Expected: FAIL（ImportError 或 function not defined）

- [ ] **Step 3: Implement FIFO helper in metrics.py**

Add to `src/services/metrics.py` (after the existing imports and dataclasses, before `MetricsService`):

```python
from collections import deque
import logging

logger = logging.getLogger(__name__)


@dataclass
class _Lot:
    """In-memory FIFO lot (spec §5.2; mirrors scripts/_sim_metrics._Lot subset)."""
    side: str
    entry_px: float
    original_amount: float
    remaining_amount: float
    open_fee: float


@dataclass
class _Roundtrip:
    """Lot pair result (spec §5.2; mirrors scripts/_sim_metrics.Roundtrip subset)."""
    side: str
    entry_px: float
    exit_px: float
    amount: float
    pnl_gross: float
    fee_open_share: float
    fee_close_share: float
    pnl_net: float
    is_liquidation: bool


_EPS = 1e-9


async def _collect_roundtrips_from_trade_actions(
    engine: AsyncEngine,
    session_id: str,
) -> tuple[list[_Roundtrip], dict[str, int]]:
    """FIFO lot pairing from trade_actions (spec §5.2).

    Reads trade_actions for the session, reconstructs FIFO lot queue from
    open fills (pnl IS NULL), pairs against close fills (pnl IS NOT NULL).
    Uses lot.open_fee + close.fee directly (no fee_rate dependency).

    Returns (roundtrips, caveats). Caveats keys: legacy_open_skipped,
    legacy_close_skipped, missing_close_entry_price_count, invariant_violations.
    """
    async with get_session(engine) as session:
        result = await session.execute(
            select(TradeAction)
            .where(TradeAction.session_id == session_id)
            .where(TradeAction.action == "order_filled")
            .order_by(TradeAction.created_at, TradeAction.id)
        )
        fills = list(result.scalars().all())

    lots: dict[str, deque[_Lot]] = {"long": deque(), "short": deque()}
    roundtrips: list[_Roundtrip] = []
    caveats = {
        "legacy_open_skipped": 0,
        "legacy_close_skipped": 0,
        "missing_close_entry_price_count": 0,
        "invariant_violations": 0,
    }

    for fill in fills:
        # OPEN vs CLOSE discriminator (spec §5.2): pnl IS NULL → open
        if fill.pnl is None:
            if fill.amount is None:
                caveats["legacy_open_skipped"] += 1
                logger.warning("metrics FIFO: legacy open fill id=%s amount IS NULL, skipping", fill.id)
                continue
            if fill.amount <= 0 or fill.price <= 0:
                logger.error("metrics FIFO: open fill id=%s corrupt amount=%s or price=%s",
                             fill.id, fill.amount, fill.price)
                caveats["invariant_violations"] += 1
                continue
            lots[fill.side].append(_Lot(
                side=fill.side, entry_px=fill.price,
                original_amount=fill.amount, remaining_amount=fill.amount,
                open_fee=fill.fee or 0.0,
            ))
            continue

        # CLOSE fill
        if fill.amount is None:
            caveats["legacy_close_skipped"] += 1
            logger.warning("metrics FIFO: legacy close fill id=%s amount IS NULL, skipping", fill.id)
            continue
        if fill.amount <= 0:
            logger.error("metrics FIFO: close fill id=%s amount %s <= 0 (corrupt data), skipping", fill.id, fill.amount)
            caveats["invariant_violations"] += 1
            continue
        if fill.entry_price is None:
            caveats["missing_close_entry_price_count"] += 1
            # NOT skip — FIFO uses lot.entry_px from open fill (spec §6.2 b)

        is_liquidation = fill.trigger_reason == "liquidation"
        liq_pnl_per_unit: float | None = None
        if is_liquidation:
            if fill.pnl is None or fill.amount <= 0:
                caveats["invariant_violations"] += 1
                logger.error("metrics FIFO: liquidation id=%s missing pnl or zero amount", fill.id)
                liq_pnl_per_unit = 0.0
            else:
                liq_pnl_per_unit = fill.pnl / fill.amount

        close_remaining = fill.amount
        close_fee_total = fill.fee or 0.0
        while close_remaining > _EPS:
            if not lots[fill.side]:
                caveats["invariant_violations"] += 1
                logger.error(
                    "metrics FIFO: close fill id=%s no preceding open lot for side=%s",
                    fill.id, fill.side,
                )
                break
            lot = lots[fill.side][0]
            consumed = min(lot.remaining_amount, close_remaining)
            fee_open_share = lot.open_fee * (consumed / lot.original_amount)
            fee_close_share = close_fee_total * (consumed / fill.amount)
            sign = 1.0 if fill.side == "long" else -1.0
            if is_liquidation:
                pnl_gross = (liq_pnl_per_unit or 0.0) * consumed
            else:
                pnl_gross = (fill.price - lot.entry_px) * consumed * sign
            pnl_net = pnl_gross - fee_open_share - fee_close_share
            roundtrips.append(_Roundtrip(
                side=lot.side, entry_px=lot.entry_px, exit_px=fill.price,
                amount=consumed,
                pnl_gross=pnl_gross,
                fee_open_share=fee_open_share, fee_close_share=fee_close_share,
                pnl_net=pnl_net,
                is_liquidation=is_liquidation,
            ))
            lot.remaining_amount -= consumed
            close_remaining -= consumed
            if lot.remaining_amount <= _EPS:
                lots[fill.side].popleft()

    return roundtrips, caveats
```

**Imports placement**（src/services/metrics.py 顶部当前已 `import statistics` + `from sqlalchemy import select`，**缺** `from collections import deque` + `import logging`）：

1. 在 metrics.py 顶部 imports 区加：
   ```python
   from collections import deque
   import logging
   ```
2. 在 imports 之后、`@dataclass` 之前加模块级 logger：
   ```python
   logger = logging.getLogger(__name__)
   ```
3. Step 3 helper code block 中显示的 `import logging` / `logger = logging.getLogger(__name__)` / `from collections import deque` 是 illustrative；真正生效在模块顶部（Step 3 把它们重复写一份是文档冗余，impl 时只保留顶部一份）。

- [ ] **Step 4: Run FIFO tests to verify pass**

Run: `pytest tests/test_metrics_fifo.py -v`
Expected: All 3 PASS

- [ ] **Step 5: Run full suite for regressions**

Run: `pytest -x -q 2>&1 | tail -20`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/services/metrics.py tests/test_metrics_fifo.py
git commit -m "$(cat <<'EOF'
feat(iter-tool-opt-net-pnl-metrics): FIFO lot pairing helper

_collect_roundtrips_from_trade_actions reconstructs FIFO lot queue from
trade_actions and produces gross + net pnl per (lot, close) pair.

Mirrors scripts/_sim_metrics.collect_roundtrips; src-side data source is
trade_actions (post-Task 1 schema). Helper does NOT take fee_rate —
fee shares come from lot.open_fee + fill.fee directly.

Handles open/close legacy NULL amount (skip + caveat), OKX cache miss
(continue + informational caveat), liquidation (reverse from fill.pnl),
invariant violations (no preceding open lot, corrupt amount/price).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: FIFO edge cases (liquidation / cache miss / legacy / invariant / corrupt / short)

**Files:**
- Modify: `tests/test_metrics_fifo.py` (extend)

- [ ] **Step 1: Append edge-case tests**

```python
@pytest.mark.asyncio
async def test_fifo_liquidation_uses_reverse_pnl(engine):
    """spec §5.2 liquidation: pnl_gross = fill.pnl/amount × consumed (吸收 sim pnl_cap)."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    sid = "fifo-liq"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.1, fee=2.5, pnl=None)
        # liquidation at 45000; sim pnl_cap clamps pnl to -480 (not geometric -500)
        await _insert_fill(conn, sid, side="long", price=45000.0, amount=0.1,
                           fee=2.25, pnl=-480.0, entry_price=50000.0,
                           trigger_reason="liquidation")

    rts, caveats = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 1
    assert rts[0].pnl_gross == pytest.approx(-480.0)  # reverse, not geometric
    assert rts[0].is_liquidation is True
    assert caveats["invariant_violations"] == 0


@pytest.mark.asyncio
async def test_fifo_okx_cache_miss_continues_algorithm(engine):
    """spec §6.5: close fill entry_price=NULL — algorithm continues, caveat raised."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    sid = "fifo-okx-miss"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.1, fee=2.5, pnl=None)
        await _insert_fill(conn, sid, side="long", price=51000.0, amount=0.1,
                           fee=2.55, pnl=100.0, entry_price=None)  # ← None

    rts, caveats = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 1
    assert rts[0].pnl_gross == pytest.approx(100.0)  # uses lot.entry_px=50000
    assert caveats["missing_close_entry_price_count"] == 1


@pytest.mark.asyncio
async def test_fifo_okx_cache_miss_pnl_equivalent_to_hit(engine):
    """spec §7.1 核心 claim: cache miss vs cache hit pnl_net byte-equal."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions

    async def setup(sid: str, close_entry_price: float | None):
        async with engine.begin() as conn:
            await _insert_session(conn, sid)
            await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.1, fee=2.5, pnl=None)
            await _insert_fill(conn, sid, side="long", price=51000.0, amount=0.1,
                               fee=2.55, pnl=100.0, entry_price=close_entry_price)

    await setup("hit", 50000.0)
    await setup("miss", None)
    hit_rts, _ = await _collect_roundtrips_from_trade_actions(engine, "hit")
    miss_rts, _ = await _collect_roundtrips_from_trade_actions(engine, "miss")
    assert len(hit_rts) == len(miss_rts) == 1
    assert hit_rts[0].pnl_net == pytest.approx(miss_rts[0].pnl_net)
    assert hit_rts[0].pnl_gross == pytest.approx(miss_rts[0].pnl_gross)


@pytest.mark.asyncio
async def test_fifo_legacy_row_skipped(engine):
    """spec §6.2 (a): amount IS NULL → skip + caveat counter."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    sid = "fifo-legacy"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=None, fee=2.5, pnl=None)
        await _insert_fill(conn, sid, side="long", price=51000.0, amount=None,
                           fee=2.55, pnl=100.0, entry_price=None)

    rts, caveats = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 0
    assert caveats["legacy_open_skipped"] == 1
    assert caveats["legacy_close_skipped"] == 1


@pytest.mark.asyncio
async def test_fifo_invariant_close_without_open(engine):
    """spec §6.9: close fill without preceding open lot → invariant_violations."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    sid = "fifo-invariant"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="long", price=51000.0, amount=0.1,
                           fee=2.55, pnl=100.0, entry_price=50000.0)

    rts, caveats = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 0
    # close fill enters `while close_remaining > _EPS`, hits `if not lots[fill.side]` once → +1
    assert caveats["invariant_violations"] == 1


@pytest.mark.asyncio
async def test_fifo_corrupt_zero_amount(engine):
    """spec §6.3: amount=0 → invariant + skip."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    sid = "fifo-zero-amount"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        # corrupt open with amount=0
        await _insert_fill(conn, sid, side="long", price=50000.0, amount=0.0, fee=0.0, pnl=None)
        # close that would need that open
        await _insert_fill(conn, sid, side="long", price=51000.0, amount=0.1,
                           fee=2.55, pnl=100.0, entry_price=50000.0)

    rts, caveats = await _collect_roundtrips_from_trade_actions(engine, sid)
    # 2 violations: (1) open amount<=0 skipped + invariant; (2) close finds no open lot → invariant
    assert caveats["invariant_violations"] == 2


@pytest.mark.asyncio
async def test_fifo_short_position(engine):
    """Short side correctness: sign = -1."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    sid = "fifo-short"
    async with engine.begin() as conn:
        await _insert_session(conn, sid)
        await _insert_fill(conn, sid, side="short", price=50000.0, amount=0.1, fee=2.5, pnl=None)
        # short close at lower price (profit)
        await _insert_fill(conn, sid, side="short", price=49000.0, amount=0.1,
                           fee=2.45, pnl=100.0, entry_price=50000.0)

    rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid)
    assert len(rts) == 1
    assert rts[0].pnl_gross == pytest.approx(100.0)  # (50000-49000)*0.1 (after sign)
```

- [ ] **Step 2: Run tests to verify pass**

Run: `pytest tests/test_metrics_fifo.py -v`
Expected: All 10 PASS（3 from Task 5 + 7 here）

- [ ] **Step 3: Commit**

```bash
git add tests/test_metrics_fifo.py
git commit -m "test(iter-tool-opt-net-pnl-metrics): FIFO edge cases — liquidation, cache miss, legacy, invariant, corrupt, short

7 edge case tests per spec §5.2/§6.2/§6.3/§6.5/§6.9; cache miss equivalence
test validates FIFO algorithmic decoupling from close.entry_price."
```

---

## Task 7: MetricsService.compute() FIFO integration

**Files:**
- Modify: `src/services/metrics.py` (compute method body)
- Modify: `tests/test_metrics.py` (add integration tests)

- [ ] **Step 1: Write failing integration tests**

Append to `tests/test_metrics.py`:

```python
from sqlalchemy import text


async def _setup_compute_session(engine, sid: str, initial_balance: float = 10000.0,
                                  fee_rate: float | None = 0.0005, fills: list[dict] | None = None):
    """Test helper: insert sessions + trade_actions for compute() tests (raw SQL with all NOT NULL cols)."""
    fr_clause = "NULL" if fee_rate is None else str(fee_rate)
    async with engine.begin() as conn:
        await conn.execute(text(
            f"INSERT INTO sessions "
            f"(id, name, symbol, initial_balance, status, created_at, updated_at, "
            f" exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            f" token_budget, fee_rate) "
            f"VALUES (:sid, :sid, 'BTC/USDT:USDT', :bal, 'active', "
            f"        '2026-01-01T00:00:00', '2026-01-01T00:00:00', "
            f"        'simulated', '15m', 15, 1, 500000, {fr_clause})"
        ), {"sid": sid, "bal": initial_balance})
        for f in (fills or []):
            defaults = {"session_id": sid, "action": "order_filled",
                        "symbol": "BTC/USDT:USDT", "trigger_reason": "market",
                        "created_at": "2026-01-01T00:00:00"}
            defaults.update(f)
            cols = ", ".join(defaults.keys())
            placeholders = ", ".join(f":{k}" for k in defaults.keys())
            await conn.execute(text(f"INSERT INTO trade_actions ({cols}) VALUES ({placeholders})"), defaults)


@pytest.mark.asyncio
async def test_compute_uses_fifo_with_gross_and_net(engine):
    """spec §5.2: compute() returns gross + net metrics."""
    from src.services.metrics import MetricsService
    sid = "compute-fifo-1"
    await _setup_compute_session(engine, sid, fills=[
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 51000.0, "amount": 0.1, "fee": 2.55,
         "pnl": 100.0, "entry_price": 50000.0},
    ])
    svc = MetricsService(engine, sid, initial_balance=10000.0)
    m = await svc.compute()

    assert m.total_pnl == pytest.approx(100.0)
    assert m.total_trades == 1
    assert m.winning_trades == 1
    assert m.win_rate == pytest.approx(1.0)
    assert m.net_pnl == pytest.approx(94.95)  # 100 - 2.5 - 2.55
    assert m.net_winning_trades == 1
    assert m.net_win_rate == pytest.approx(1.0)
    assert m.total_fees == pytest.approx(5.05)


@pytest.mark.asyncio
async def test_compute_net_mdd_uses_net_equity(engine):
    """spec §A1: max_drawdown_pct uses net equity series."""
    from src.services.metrics import MetricsService
    sid = "mdd-net"
    # net pnl = -100 - 2.5 - 2.45 = -104.95
    # equity trough: 1000 + (-104.95) = 895.05
    # dd = (1000 - 895.05) / 1000 = 0.10495 → 10.495%
    await _setup_compute_session(engine, sid, initial_balance=1000.0, fills=[
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 49000.0, "amount": 0.1, "fee": 2.45,
         "pnl": -100.0, "entry_price": 50000.0},
    ])
    svc = MetricsService(engine, sid, initial_balance=1000.0)
    m = await svc.compute()
    assert m.max_drawdown_pct == pytest.approx(10.495, abs=0.01)


@pytest.mark.asyncio
async def test_compute_fee_rate_null_fallback_warns(engine, caplog):
    """spec §6.1: sessions.fee_rate IS NULL → log.warning (algorithm unaffected since FIFO uses lot.open_fee directly)."""
    from src.services.metrics import MetricsService
    sid = "fee-null"
    await _setup_compute_session(engine, sid, fee_rate=None, fills=[
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 51000.0, "amount": 0.1, "fee": 2.55,
         "pnl": 100.0, "entry_price": 50000.0},
    ])
    svc = MetricsService(engine, sid, initial_balance=10000.0)
    with caplog.at_level("WARNING"):
        m = await svc.compute()
    assert m.total_trades == 1
    # Stricter: only match our service's logger (avoids ORM / sqlalchemy noise)
    metrics_records = [r for r in caplog.records if r.name == "src.services.metrics"]
    assert any("fee_rate" in r.message.lower() for r in metrics_records), (
        f"Expected fee_rate warning from src.services.metrics; got: {[r.message for r in metrics_records]}"
    )


@pytest.mark.asyncio
async def test_compute_legacy_session_all_stats_unavailable(engine):
    """spec §6.2(c): all close fills legacy → all stats N/A, total_trades=0."""
    from src.services.metrics import MetricsService
    sid = "legacy-all"
    await _setup_compute_session(engine, sid, fills=[
        {"side": "long", "price": 50000.0, "amount": None, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 51000.0, "amount": None, "fee": 2.55,
         "pnl": 100.0, "entry_price": None},
    ])
    svc = MetricsService(engine, sid, initial_balance=10000.0)
    m = await svc.compute()
    assert m.total_trades == 0
    assert m.legacy_open_skipped == 1
    assert m.legacy_close_skipped == 1


@pytest.mark.asyncio
async def test_compute_profit_factor_none_on_zero_losses(engine):
    """spec §2 zero-denom: PF None when no losses."""
    from src.services.metrics import MetricsService
    sid = "pf-no-loss"
    await _setup_compute_session(engine, sid, fills=[
        {"side": "long", "price": 50000.0, "amount": 0.1, "fee": 2.5, "pnl": None},
        {"side": "long", "price": 51000.0, "amount": 0.1, "fee": 2.55,
         "pnl": 100.0, "entry_price": 50000.0},
    ])
    svc = MetricsService(engine, sid, initial_balance=10000.0)
    m = await svc.compute()
    assert m.profit_factor is None
    assert m.net_profit_factor is None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_metrics.py -v -k "compute_uses_fifo or compute_net_mdd or compute_fee_rate_null or compute_legacy or compute_profit_factor_none"`
Expected: FAIL

- [ ] **Step 3: Replace MetricsService.compute() body**

Edit `src/services/metrics.py`. Replace the `compute` method body:

```python
    async def compute(
        self,
        current_position: str = "none",
    ) -> PerformanceMetrics:
        from src.storage.models import Session as SessionModel

        # Fetch fee_rate from sessions (informational; FIFO uses lot.open_fee + fill.fee directly)
        async with get_session(self._engine) as session:
            row = (await session.execute(
                select(SessionModel.fee_rate).where(SessionModel.id == self._session_id)
            )).first()
        fee_rate = row.fee_rate if row else None
        if fee_rate is None:
            logger.warning(
                "metrics: sessions.fee_rate IS NULL for session %s (informational; "
                "FIFO algorithm uses recorded trade_actions.fee values)",
                self._session_id,
            )

        # Total fees (independent of FIFO roundtrips)
        async with get_session(self._engine) as session:
            result = await session.execute(
                select(TradeAction)
                .where(TradeAction.session_id == self._session_id)
                .where(TradeAction.action == "order_filled")
            )
            all_fills = list(result.scalars().all())
        total_fees = sum(f.fee for f in all_fills if f.fee is not None)

        # FIFO lot pairing
        rts, caveats = await _collect_roundtrips_from_trade_actions(self._engine, self._session_id)

        # All stats unavailable when no roundtrips (spec §6.2 c)
        if not rts:
            return PerformanceMetrics(
                current_position=current_position,
                total_fees=total_fees,
                legacy_open_skipped=caveats["legacy_open_skipped"],
                legacy_close_skipped=caveats["legacy_close_skipped"],
                missing_close_entry_price_count=caveats["missing_close_entry_price_count"],
                invariant_violations=caveats["invariant_violations"],
            )

        gross_pnls = [rt.pnl_gross for rt in rts]
        gross_wins = [p for p in gross_pnls if p > 0]
        gross_losses = [p for p in gross_pnls if p <= 0]
        gross_profit = sum(gross_wins)
        gross_loss_abs = abs(sum(gross_losses))

        net_pnls = [rt.pnl_net for rt in rts]
        net_wins = [p for p in net_pnls if p > 0]
        net_losses = [p for p in net_pnls if p <= 0]
        net_profit = sum(net_wins)
        net_loss_abs = abs(sum(net_losses))

        # MDD on net equity (spec §A1)
        equity = self._initial_balance
        peak = equity
        max_dd_ratio = 0.0
        for net in net_pnls:
            equity += net
            peak = max(peak, equity)
            if peak > 0:
                max_dd_ratio = max(max_dd_ratio, (peak - equity) / peak)

        # recent_summary 沿用 gross W/L 计数（spec 未明确切 net）；net 化作 W3 follow-up
        # candidate if fee 翻转 win→loss 频率高（参 spec §10 OOS）
        n = min(5, len(gross_pnls))
        recent_pnls = gross_pnls[-n:]
        recent_wins = sum(1 for p in recent_pnls if p > 0)
        recent_losses = n - recent_wins
        trade_word = "trade" if n == 1 else "trades"
        recent_summary = f"{recent_wins}W {recent_losses}L (last {n} {trade_word})"

        total_pnl = sum(gross_pnls)
        net_pnl = sum(net_pnls)

        return PerformanceMetrics(
            total_return_pct=(total_pnl / self._initial_balance) * 100 if self._initial_balance > 0 else 0.0,
            total_pnl=total_pnl,
            win_rate=len(gross_wins) / len(rts),
            max_drawdown_pct=max_dd_ratio * 100.0,
            profit_factor=(gross_profit / gross_loss_abs) if (gross_wins and gross_loss_abs > 0) else None,
            total_trades=len(rts),
            winning_trades=len(gross_wins),
            losing_trades=len(gross_losses),
            current_position=current_position,
            avg_win=gross_profit / len(gross_wins) if gross_wins else 0.0,
            avg_loss=-gross_loss_abs / len(gross_losses) if gross_losses else 0.0,
            best_trade=max(gross_pnls),
            worst_trade=min(gross_pnls),
            recent_summary=recent_summary,
            total_fees=total_fees,
            net_pnl=net_pnl,
            net_profit_factor=(net_profit / net_loss_abs) if (net_wins and net_loss_abs > 0) else None,
            net_win_rate=len(net_wins) / len(rts),
            avg_win_net=net_profit / len(net_wins) if net_wins else 0.0,
            avg_loss_net=-net_loss_abs / len(net_losses) if net_losses else 0.0,
            best_trade_net=max(net_pnls),
            worst_trade_net=min(net_pnls),
            net_winning_trades=len(net_wins),
            net_losing_trades=len(net_losses),
            legacy_open_skipped=caveats["legacy_open_skipped"],
            legacy_close_skipped=caveats["legacy_close_skipped"],
            missing_close_entry_price_count=caveats["missing_close_entry_price_count"],
            invariant_violations=caveats["invariant_violations"],
        )
```

**Note**: `Session` ORM 类名就是 `Session`（`src/storage/models.py:32`）；本 import 用 `as SessionModel` alias 避免与 `sqlalchemy.orm.Session` 名冲突。

- [ ] **Step 4: Run failing tests to verify pass**

Run: `pytest tests/test_metrics.py -v -k "compute_uses_fifo or compute_net_mdd or compute_fee_rate_null or compute_legacy or compute_profit_factor_none"`
Expected: PASS

- [ ] **Step 5: Rewrite `_add_fill` helper + update 4 affected tests in test_metrics.py**

既有 `tests/test_metrics.py:17-25` `_add_fill` 只插 close fills（pnl 必填，无配对 open，无 amount/entry_price）。FIFO 改造后每个 close 无对应 open lot → 全归 `invariant_violations`，4 个 test 跑 0 trades 全 break。

**重写 `_add_fill` helper**（替换 line 17-37 内容）：

```python
async def _add_paired_trade(engine, gross_pnl, fee_open=0.25, fee_close=0.25,
                              entry_price=50000.0, amount=0.1):
    """Add a paired open + close fill that produces a roundtrip with the given gross pnl.

    FIFO requires open + close pair; this helper preserves the original test contract
    (one call = one trade) by inserting both fills under unique order_ids.
    """
    async with get_session(engine) as session:
        oid_base = f"o-{gross_pnl:.4f}"
        # Open fill
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"{oid_base}-open", symbol="BTC/USDT:USDT", side="long",
            trigger_reason="market", price=entry_price, pnl=None, fee=fee_open,
            amount=amount, entry_price=None,
            reasoning="(exchange: market open filled)",
        ))
        await session.commit()
    # Close fill: derive exit price from gross_pnl (long: exit = entry + pnl/amount)
    exit_price = entry_price + gross_pnl / amount
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"{oid_base}-close", symbol="BTC/USDT:USDT", side="long",
            trigger_reason="market", price=exit_price, pnl=gross_pnl, fee=fee_close,
            amount=amount, entry_price=entry_price,
            reasoning="(exchange: market close filled)",
        ))
        await session.commit()


async def _add_open_fill(engine, fee=0.5, entry_price=50000.0, amount=0.1):
    """Open fill without paired close — for testing total_fees aggregation."""
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id="test-session", action="order_filled",
            order_id=f"o-open-{fee}", symbol="BTC/USDT:USDT", side="long",
            trigger_reason="market", price=entry_price, pnl=None, fee=fee,
            amount=amount, entry_price=None,
            reasoning="(exchange: market open filled)",
        ))
        await session.commit()
```

**4 affected test assertion updates**:

**Line 40 `test_compute_metrics`**: replace `_add_fill(...)` calls with `_add_paired_trade(...)`. Assertion updates:
```python
async def test_compute_metrics(metrics_db):
    from src.services.metrics import MetricsService
    await _add_paired_trade(metrics_db, 30.0, fee_open=0.25, fee_close=0.25)
    await _add_paired_trade(metrics_db, -15.0, fee_open=0.15, fee_close=0.15)
    await _add_paired_trade(metrics_db, 180.0, fee_open=0.4, fee_close=0.4)

    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    assert metrics.total_trades == 3
    assert metrics.win_rate == pytest.approx(2 / 3, abs=0.01)
    assert metrics.total_pnl == pytest.approx(195.0)
    assert metrics.profit_factor is not None
    assert metrics.profit_factor > 1.0
    assert metrics.avg_win == pytest.approx(105.0)
    assert metrics.avg_loss == pytest.approx(-15.0)
    assert metrics.best_trade == pytest.approx(180.0)
    assert metrics.worst_trade == pytest.approx(-15.0)
    # Each paired trade has fee_open + fee_close = total per-trade fee
    assert metrics.total_fees == pytest.approx(0.5 + 0.3 + 0.8)
    # Net = gross - fees per trade
    assert metrics.net_pnl == pytest.approx(195.0 - 1.6)
```

**Line 78 `test_compute_metrics_recent_summary`**: replace `_add_fill` calls with `_add_paired_trade`; assertions unchanged.

**Line 91 `test_compute_metrics_total_fees_includes_opens`**: this tests an open without paired close. Use `_add_open_fill` (which now writes amount). Assertion change:
```python
async def test_compute_metrics_total_fees_includes_opens(metrics_db):
    from src.services.metrics import MetricsService
    await _add_open_fill(metrics_db, fee=0.5)
    await _add_paired_trade(metrics_db, 30.0, fee_open=0.25, fee_close=0.25)
    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    # Solo open fill leaves a lot in queue but doesn't pair → total_trades only counts paired roundtrip
    assert metrics.total_trades == 1
    # total_fees aggregates all order_filled rows regardless of FIFO pairing
    assert metrics.total_fees == pytest.approx(0.5 + 0.25 + 0.25)
```

**Line 103 `test_compute_metrics_max_drawdown`**: replace `_add_fill` calls with `_add_paired_trade(..., fee_open=0.0, fee_close=0.0)`. Net == gross since fees=0; MDD calc identical (per spec §A1 net equity = gross equity when fees=0):
```python
async def test_compute_metrics_max_drawdown(metrics_db):
    from src.services.metrics import MetricsService
    await _add_paired_trade(metrics_db, 100.0, fee_open=0.0, fee_close=0.0)
    await _add_paired_trade(metrics_db, -50.0, fee_open=0.0, fee_close=0.0)
    await _add_paired_trade(metrics_db, -30.0, fee_open=0.0, fee_close=0.0)
    await _add_paired_trade(metrics_db, 200.0, fee_open=0.0, fee_close=0.0)
    service = MetricsService(engine=metrics_db, session_id="test-session", initial_balance=10000.0)
    metrics = await service.compute()
    # net = gross (fees=0); equity series [10000, 10100, 10050, 10020, 10220]
    # peak after step 1 = 10100, trough at step 3 = 10020 → dd = 80/10100
    assert metrics.max_drawdown_pct == pytest.approx(80 / 10100 * 100)
```

Run after edits: `pytest tests/test_metrics.py -v 2>&1 | tail -30`
Expected: 4 affected tests + 5 new compute() tests all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/services/metrics.py tests/test_metrics.py
git commit -m "$(cat <<'EOF'
feat(iter-tool-opt-net-pnl-metrics): MetricsService.compute() FIFO integration

Replaces per-close-fill aggregation with FIFO lot pairing; PerformanceMetrics
now carries gross + net dual fields plus caveat counters. MDD switched to
net equity (initial + Σ net_pnls). PF zero-denom returns None.

Pre-iter legacy sessions (all NULL amount) yield all-N/A stats (spec §6.2c);
forensic analysis via scripts/_sim_metrics from sim_orders still complete.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: get_performance output layer (gross/net 并列双视角)

**Files:**
- Modify: `src/agent/tools_perception.py:682-748` (get_performance implementation)
- Modify: `src/agent/tools_perception.py:291` (variable rename)
- Modify: `src/agent/trader.py:196-214` (pydantic-ai **wrapper docstring** — LLM-visible via griffe sniff，per memory `project_n7_layer1_organization`；自指描述 "All gross-based until iter-tool-opt-net-pnl-metrics lands" 必须重写)
- Modify: `tests/test_get_performance.py` (rewrite 6+ legacy tests)

**Breaking tests to update** (`tests/test_get_performance.py`):

| Old assertion line | Old check | New check |
|---|---|---|
| 83 | `assert "(gross-based)" in out` | `assert "gross" in out and "net" in out` |
| 92-94 | Trade Stats labels each metric as (gross-based) | replace with "Win Rate: X% gross / Y% net" pattern |
| 105-117 | win-rate `(gross-based)` regex | `assert re.search(r"\d+% gross.*\d+% net", out)` |
| 122-135 | Profit Factor `(gross-based)` | `assert "gross /" in pf_line and "net" in pf_line` |
| 144-156 | Max Drawdown `(gross-based equity)` | `assert "(net equity)" in mdd_line` |
| 165-177 | Best/Worst Trade `(gross-based)` | `assert "gross /" in bw_line and "net" in bw_line` |
| 189-201 | wrapper docstring `gross-based` substring | docstring 改为 "gross/net dual view; see spec §8 for caveats" 类似 |

- [ ] **Step 1a: Rewrite existing `_make_deps_with_metrics` fixture (lines 49-74)**

既有 fixture 只插 close fills（pnl 必填，无 amount/entry_price）→ FIFO 后全归 legacy_close_skipped → 0 trades → 输出 "Stats unavailable" → **6 个既有断言全 FAIL**。必须重写为 paired open+close pattern：

```python
async def _make_deps_with_metrics(tmp_path):
    """Build deps with MetricsService backed by a DB with two completed paired trades.

    iter-tool-opt-net-pnl-metrics: TradeAction 必须有 amount + entry_price (close fill)
    for FIFO to produce roundtrips.
    """
    from src.storage.database import init_db, get_session
    from src.storage.models import Session, TradeAction
    from src.services.metrics import MetricsService

    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/perf19.db")
    async with get_session(engine) as session:
        session.add(Session(id="s19", name="test-perf-19", initial_balance=10000.0, fee_rate=0.0005))
        # Trade 1: open @50000 → close @50450, gross=+45 (long 0.1)
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o1-open",
            symbol="BTC/USDT:USDT", side="long",
            price=50000.0, amount=0.1, fee=0.25, pnl=None, entry_price=None,
        ))
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o1-close",
            symbol="BTC/USDT:USDT", side="long",
            price=50450.0, amount=0.1, fee=0.25, pnl=45.0, entry_price=50000.0,
        ))
        # Trade 2: open @50000 → close @49780, gross=-22 (long 0.1)
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o2-open",
            symbol="BTC/USDT:USDT", side="long",
            price=50000.0, amount=0.1, fee=0.15, pnl=None, entry_price=None,
        ))
        session.add(TradeAction(
            session_id="s19", action="order_filled", order_id="o2-close",
            symbol="BTC/USDT:USDT", side="long",
            price=49780.0, amount=0.1, fee=0.15, pnl=-22.0, entry_price=50000.0,
        ))
        await session.commit()

    deps = _make_deps()
    deps.db_engine = engine
    deps.session_id = "s19"
    deps.initial_balance = 10000.0
    deps.fee_rate = 0.0005
    deps.metrics = MetricsService(engine=engine, session_id="s19", initial_balance=10000.0)
    deps.exchange.fetch_balance.return_value = Balance(10023.0, 9023.0, 1000.0)
    return deps, engine
```

After rewrite, FIFO produces 2 roundtrips (gross 45 + −22) with associated net (fee subtotals 0.5 + 0.3 = 0.8) → `metrics.total_trades == 2` → goes through normal Trade Stats render path.

- [ ] **Step 1b: Update existing test_get_performance.py assertions (6 lines)**

Specific assertion replacements:

| 既有 line | 旧断言 | 新断言 |
|---|---|---|
| 94 `test_trade_stats_includes_gross_based_label` | `assert "(gross-based)" in out` | `assert "gross" in out and "net" in out` |
| 116 `test_win_rate_line_has_gross_based_label` regex | `r"\d+\.?\d*%,\s*gross-based\)"` | `r"\d+%\s+gross.*\d+%\s+net"` |
| 134 `test_profit_factor_has_gross_based_label` PF line | `assert "(gross-based)" in line` | `assert "gross" in line and "net" in line` |
| 155 `test_max_drawdown_has_gross_based_label` MDD line | `assert "(gross-based equity)" in line` | `assert "(net equity)" in line` |
| 176 `test_best_worst_gross_based_label` line | `assert "(gross-based)" in line` | `assert "gross" in line and "net" in line` |
| 200 `test_get_performance_wrapper_docstring_lists_*_caveat` | `assert "gross-based" in desc` | `assert "gross" in desc and "net" in desc and "Total Fees" in desc and "(net equity)" in desc` |

Plus optionally: rename test function names containing `gross_based` to reflect new schema (cosmetic; not required for pass).

Add new positive assertions:

```python
@pytest.mark.asyncio
async def test_get_performance_dual_view_lines(deps_with_one_winning_trade):
    """spec §8.1: 输出 gross/net 双视角."""
    out = await get_performance(deps_with_one_winning_trade)
    # Win Rate line
    win_line = next(line for line in out.splitlines() if line.startswith("Win Rate"))
    assert "gross" in win_line and "net" in win_line, f"Win rate line missing dual view: {win_line!r}"
    # Profit Factor line
    pf_line = next(line for line in out.splitlines() if line.startswith("Profit Factor"))
    assert "gross" in pf_line and "net" in pf_line, f"PF line missing dual view: {pf_line!r}"
    # Max Drawdown line
    mdd_line = next(line for line in out.splitlines() if "Max Drawdown" in line)
    assert "net equity" in mdd_line, f"MDD line missing 'net equity': {mdd_line!r}"
```

`deps_with_one_winning_trade` fixture — 复用既有 `deps_factory` (conftest.py:130-160) 避免重新拼装 TradingDeps 全部字段：

```python
@pytest_asyncio.fixture
async def deps_with_one_winning_trade(db_engine, deps_factory):
    """deps_factory 产 TradingDeps + 注入 metrics + 1 winning paired trade。"""
    from sqlalchemy import text
    from src.services.metrics import MetricsService

    sid = "perf-test-1"
    async with db_engine.begin() as conn:
        # 防御性 idempotent — deps_factory 不写 sessions DB 行（只初始化 SimulatedExchange in-memory state），
        # 但 tmp_path 跨 test 复用时此 DELETE 防 FK 冲突
        await conn.execute(text("DELETE FROM sessions WHERE id = :sid"), {"sid": sid})
        await conn.execute(text(
            "INSERT INTO sessions "
            "(id, name, symbol, initial_balance, status, created_at, updated_at, "
            " exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            " token_budget, fee_rate) "
            "VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
            "        '2026-01-01T00:00:00', '2026-01-01T00:00:00', "
            "        'simulated', '15m', 15, 1, 500000, 0.0005)"
        ), {"sid": sid})
        # Paired open + close (one winning roundtrip)
        # created_at must be explicit — raw SQL bypasses Python-side _utcnow default
        # (TradeAction.created_at is NOT NULL; per spec §C0/§C1)
        # Distinct timestamps ensure FIFO ORDER BY created_at puts open before close.
        for fill in [
            {"session_id": sid, "action": "order_filled", "symbol": "BTC/USDT:USDT",
             "side": "long", "trigger_reason": "market", "price": 50000.0,
             "amount": 0.1, "fee": 2.5, "pnl": None, "entry_price": None,
             "order_id": "o-open", "created_at": "2026-01-01T00:00:00"},
            {"session_id": sid, "action": "order_filled", "symbol": "BTC/USDT:USDT",
             "side": "long", "trigger_reason": "market", "price": 51000.0,
             "amount": 0.1, "fee": 2.55, "pnl": 100.0, "entry_price": 50000.0,
             "order_id": "o-close", "created_at": "2026-01-01T00:00:01"},
        ]:
            cols = ", ".join(fill.keys())
            placeholders = ", ".join(f":{k}" for k in fill.keys())
            await conn.execute(text(f"INSERT INTO trade_actions ({cols}) VALUES ({placeholders})"), fill)

    deps = deps_factory(session_id=sid, initial_balance=10000.0)
    deps.metrics = MetricsService(db_engine, sid, initial_balance=10000.0)
    deps.fee_rate = 0.0005
    return deps
```

注：`deps_factory` 已涵盖 SimulatedExchange + initial_balance pre-population + 全部 TradingDeps 必填字段（参 `tests/conftest.py:115-163`）；本 fixture 仅插入 trade_actions 行 + 注入 metrics service + 显式 fee_rate。

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_get_performance.py -v`
Expected: FAIL (output still uses old format)

- [ ] **Step 3: Rewrite get_performance**

Edit `src/agent/tools_perception.py`. Find `async def get_performance` (~line 682). Replace from after the `metrics = await deps.metrics.compute()` line through the end (~line 748).

**Also fix L3 path schema** — `Return:` 标签与新 `Total Return: ... (incl. unrealized, net)` schema 不一致；在 `if deps.metrics is None:` 分支（~line 690-704）把 `Return:` 改为 `Total Return: ... (incl. unrealized, net)`，3 处 perf_section (L3 / total_trades==0 / 正常 path) 标签统一。

```python
    metrics = await deps.metrics.compute()

    fees_line = (
        f"Total Fees: -{metrics.total_fees:.2f} USDT"
        if metrics.total_fees > 0 else "Total Fees: 0.00 USDT"
    )

    if metrics.total_trades == 0:
        if metrics.legacy_close_skipped > 0 or metrics.legacy_open_skipped > 0:
            stats_body = (
                "Stats unavailable: all close fills are pre-net-metrics-iter legacy data "
                "(forensic analysis via scripts/_sim_metrics.py from sim_orders table)."
            )
        elif metrics.invariant_violations > 0:
            stats_body = (
                f"Stats unavailable: data invariant violations "
                f"({metrics.invariant_violations} close fills had no preceding open lot "
                f"or corrupt amount/price). Investigate trade_actions integrity."
            )
        else:
            stats_body = "No completed trades yet."
        perf_section = (
            f"=== Trading Performance (@ {fetch_ts} UTC) ===\n"
            f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
            f"Current Balance: {balance.total_usdt:.2f} USDT\n"
            f"Total Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized, net)\n"
            f"{fees_line}"
        )
        stats_section = f"=== Trade Stats ===\n{stats_body}"
        return f"{perf_section}\n\n{stats_section}"

    def _fmt_pf(pf: float | None) -> str:
        return "N/A (no losses)" if pf is None else f"{pf:.2f}"

    perf_section = (
        f"=== Trading Performance (@ {fetch_ts} UTC) ===\n"
        f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
        f"Current Balance: {balance.total_usdt:.2f} USDT\n"
        f"Total Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized, net)\n"
        f"Realized PnL: {metrics.total_pnl:+.2f} USDT gross / {metrics.net_pnl:+.2f} USDT net "
        f"(fees {-metrics.total_fees:+.2f} USDT)\n"
        f"{fees_line}"
    )

    # Note: 与 spec §8.1 example schema 一致。`fees` 注解 = session-wide total_fees
    # (含未平仓 open lot 的 entry fee)。当 session 末仍有持仓时，gross − net ≠ total_fees
    # (差额 = 未平仓 lot 的 open_fee_share)；此为已知 minor UX 不一致，未平仓退出后即收敛。

    stats_lines = []
    if metrics.legacy_open_skipped > 0 or metrics.legacy_close_skipped > 0:
        m = metrics.total_trades
        n = m + metrics.legacy_close_skipped
        stats_lines.append(
            f"Note: net stats based on {m}/{n} trades "
            f"({metrics.legacy_close_skipped} legacy rows skipped — pre-net-metrics-iter data)."
        )
    if metrics.missing_close_entry_price_count > 0:
        stats_lines.append(
            f"Note: {metrics.missing_close_entry_price_count} close fills had cache-miss entry_price "
            f"(FIFO unaffected; audit trail incomplete for those trades)."
        )

    stats_lines.append(f"Total Trades: {metrics.total_trades}")
    stats_lines.append(
        f"Win Rate: {metrics.win_rate:.0%} gross ({metrics.winning_trades}W/{metrics.losing_trades}L) "
        f"/ {metrics.net_win_rate:.0%} net ({metrics.net_winning_trades}W/{metrics.net_losing_trades}L)"
    )
    stats_lines.append(
        f"Profit Factor: {_fmt_pf(metrics.profit_factor)} gross / {_fmt_pf(metrics.net_profit_factor)} net"
    )
    stats_lines.append(
        f"Avg Win:  {metrics.avg_win:+.2f} USDT gross / {metrics.avg_win_net:+.2f} USDT net"
    )
    stats_lines.append(
        f"Avg Loss: {metrics.avg_loss:.2f} USDT gross / {metrics.avg_loss_net:.2f} USDT net"
    )
    stats_lines.append(
        f"Best Trade: {metrics.best_trade:+.2f} USDT gross / {metrics.best_trade_net:+.2f} USDT net"
    )
    stats_lines.append(
        f"Worst Trade: {metrics.worst_trade:.2f} USDT gross / {metrics.worst_trade_net:.2f} USDT net"
    )
    mdd_str = f"-{metrics.max_drawdown_pct:.1f}%" if metrics.max_drawdown_pct > 0 else "0.0%"
    stats_lines.append(f"Max Drawdown: {mdd_str} (net equity)")

    stats_section = "=== Trade Stats ===\n" + "\n".join(stats_lines)

    out = f"{perf_section}\n\n{stats_section}"

    # OKX session footnote (spec §6.4)
    from src.integrations.exchange.okx import OKXExchange
    if isinstance(deps.exchange, OKXExchange):
        out += (
            "\n\nNote: OKX net metrics use exchange-echoed fees (accurate); "
            "minor ε from lot amount precision possible.\n"
            "      Cache-miss close fills (if any) excluded from net stats; see caveat above."
        )

    return out
```

Also rename `pnl_pct_inner` → `pnl_pct_of_capital` at line 291:

```python
        if deps.initial_balance > 0:
            pnl_pct_of_capital = (p.unrealized_pnl / deps.initial_balance) * 100
            pnl_lines.append(
                f"PnL: {p.unrealized_pnl:+.2f} USDT gross ({pnl_pct_of_capital:+.2f}% of initial capital)"
            )
```

**Update pydantic-ai wrapper docstring** `src/agent/trader.py:196-214`（LLM 实际看的是这段；tools_perception.py:683 那段只是 internal 实现 docstring）：

Find:
```python
    @tool
    async def get_performance(ctx: RunContext[TradingDeps]) -> str:
        """Show session trading performance — balance, return, cumulative fees, win rate, drawdown.

        Returns:
            str: Two sections.

            === Trading Performance === — Initial Balance, Current Balance,
            Total Return (% + USDT, incl. unrealized), Realized PnL (gross, before fees),
            Total Fees (cumulative across all fills).

            === Trade Stats === — Total Trades, Win Rate, Avg Win/Loss, Profit Factor,
            Max Drawdown (equity-peak-based), Best/Worst Trade. All gross-based until
            iter-tool-opt-net-pnl-metrics lands.

            Related: get_trade_journal (decision timeline).

        Degradation: 'No completed trades yet.' if zero trades.
        'No metrics service available.' if metrics service is missing.
        """
```

Replace with:
```python
    @tool
    async def get_performance(ctx: RunContext[TradingDeps]) -> str:
        """Show session trading performance — balance, return, fees, win rate, drawdown (gross + net dual view).

        Returns:
            str: Two sections.

            === Trading Performance === — Initial Balance, Current Balance,
            Total Return (% + USDT, incl. unrealized, net), Realized PnL (gross / net + fees),
            Total Fees (cumulative across all fills).

            === Trade Stats === — Total Trades, Win Rate (gross / net), Avg Win/Loss
            (gross / net), Profit Factor (gross / net), Max Drawdown (net equity),
            Best/Worst Trade (gross / net). Caveats:
            - Pre-iter legacy close fills are skipped (FIFO requires entry_price + amount);
              when present, output adds "Note: net stats based on m/n trades" line.
            - OKX cache-miss close fills are included in algorithm (FIFO uses lot.entry_px
              from open) but flagged in caveat note.

            Related: get_trade_journal (decision timeline).

        Degradation: 'No completed trades yet.' if zero trades; 'Stats unavailable: ...'
        if all close fills are legacy; 'No metrics service available.' if metrics service missing.
        """
```

- [ ] **Step 4: Run get_performance tests**

Run: `pytest tests/test_get_performance.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite for regressions**

Run: `pytest -x -q 2>&1 | tail -30`

Grep for any remaining `(gross-based)` assertions and update if found:

```bash
grep -rn "(gross-based)" tests/ --include="*.py"
```

如有 hit，inline 更新断言为新 `gross / net` 双视角 schema。

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py src/agent/trader.py tests/test_get_performance.py
# tests/test_tool_enhancement.py — only add if grep in Step 5 found and updated assertions
git commit -m "$(cat <<'EOF'
feat(iter-tool-opt-net-pnl-metrics): get_performance dual gross/net view

Per spec §8.1: Win Rate / Profit Factor / Avg Win / Avg Loss / Best / Worst
all 并列双视角. MDD single net (spec §A1). OKX session footnote via
isinstance check. Legacy + cache-miss caveats output per §8.3.

pnl_pct_inner → pnl_pct_of_capital variable rename (output text already
explicit "% of initial capital"). Removes (gross-based) labels.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: scripts/_sim_metrics gross 视角 + analyze_sim/diff_sim 输出

**Files:**
- Modify: `scripts/_sim_metrics.py` (add gross metric functions)
- Modify: `scripts/analyze_sim.py:31-35,134-148` (imports + report columns)
- Modify: `scripts/diff_sim.py` (diff output)
- Modify: `tests/test_analyze_sim.py:170` (column list update)

**Naming asymmetry note**: 既有 scripts metric functions（`win_rate` / `profit_factor` / `largest_win_loss` / `avg_fifo_pnl_per_roundtrip`）已是 net (FIFO `pnl_net`-based)。本 iter 新加的 gross 视角加 `_gross` 后缀；既有 net 函数**不**加 `_net` 后缀（避免 break `analyze_sim.py:31-35` imports）。

**METRIC_GROUPS inventory test 影响**：`scripts/_sim_metrics.METRIC_GROUPS` 是 28 项 single source of truth（`tests/test_drift_phase2_metrics.py:18` 硬断言 `len == 28`，`:22-41` partition 10 PnL / 8 Cost / 10 Behavior）。本 iter 加 4 个 gross 变体后：

- **METRIC_GROUPS** 同步 +4 keys（`win_rate_gross` / `profit_factor_gross` / `avg_fifo_pnl_per_roundtrip_gross` / `largest_win_loss_gross`）
- inventory test 更新：`len == 32`；pnl_keys partition `10 → 14`
- 这是 spec §3 单一来源约定的正确响应（drift guard 强制 schema-aware change）

- [ ] **Step 1: Add gross metric functions + METRIC_GROUPS entries**

Edit `scripts/_sim_metrics.py`:

(a) Append 4 keys to `METRIC_GROUPS` list (find current list to locate insertion; new keys join PnL group):
```python
METRIC_GROUPS: list[str] = [
    # ... existing 28 keys ...
    "win_rate_gross", "profit_factor_gross",
    "avg_fifo_pnl_per_roundtrip_gross", "largest_win_loss_gross",
]
```

**同步更新模块级 assert** (`scripts/_sim_metrics.py:74-75`，**import-time 触发**，不改会 `AssertionError` 阻塞全测试套件):
```python
# scripts/_sim_metrics.py:74-75 — 改 28 → 32
assert len(METRIC_GROUPS) == 32, \
    "METRIC_GROUPS must stay at 32 — update spec §3 if changing"
```

(b) After `largest_win_loss` (~line 312), add:

```python
def win_rate_gross(rts: list[Roundtrip]) -> float | None:
    if not rts:
        return None
    return sum(1 for rt in rts if rt.pnl_gross > 0) / len(rts)


def profit_factor_gross(rts: list[Roundtrip]) -> float | None:
    if not rts:
        return None
    wins = sum(rt.pnl_gross for rt in rts if rt.pnl_gross > 0)
    losses = sum(rt.pnl_gross for rt in rts if rt.pnl_gross < 0)
    if wins == 0 or losses == 0:
        return None
    return wins / abs(losses)


def avg_fifo_pnl_per_roundtrip_gross(rts: list[Roundtrip]) -> float | None:
    if not rts:
        return None
    return statistics.mean(rt.pnl_gross for rt in rts)


def largest_win_loss_gross(rts: list[Roundtrip]) -> tuple[float | None, float | None]:
    if not rts:
        return None, None
    pnls = [rt.pnl_gross for rt in rts]
    return max(pnls), min(pnls)
```

(c) Update `tests/test_drift_phase2_metrics.py`：
- line 18 `assert len(METRIC_GROUPS) == 28` → `== 32`
- line 19 `assert len(set(METRIC_GROUPS)) == 28` → `== 32`
- line 24-28 `pnl_keys` set 加 4 keys
- line 38 `assert len(pnl_keys) == 10` → `== 14`

- [ ] **Step 1b: Add unit tests for 4 new gross functions**

Append to `tests/test_sim_metrics.py` (既有文件)：

```python
from scripts._sim_metrics import (
    win_rate_gross, profit_factor_gross,
    avg_fifo_pnl_per_roundtrip_gross, largest_win_loss_gross,
    Roundtrip,
)


def _rt(pnl_gross: float, pnl_net: float) -> Roundtrip:
    """Helper: build minimal Roundtrip for unit test (only gross/net fields matter)."""
    from datetime import datetime, timezone
    return Roundtrip(
        open_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        close_at=datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc),
        open_cycle_id=None, close_cycle_id=None,
        side="long", entry_px=50000.0, exit_px=51000.0,
        amount=0.1, leverage=10,
        pnl_gross=pnl_gross,
        fee_open_share=0.0, fee_close_share=0.0,
        fee_total=0.0, pnl_net=pnl_net,
        duration_seconds=60, exit_type="market",
    )


def test_win_rate_gross_empty():
    assert win_rate_gross([]) is None


def test_win_rate_gross_all_wins():
    rts = [_rt(10.0, 5.0), _rt(20.0, 15.0)]
    assert win_rate_gross(rts) == 1.0


def test_win_rate_gross_mixed():
    # 2 gross wins, 1 gross loss
    rts = [_rt(10.0, 5.0), _rt(-5.0, -10.0), _rt(20.0, 15.0)]
    assert win_rate_gross(rts) == pytest.approx(2 / 3, abs=0.01)


def test_profit_factor_gross_zero_losses_returns_none():
    rts = [_rt(10.0, 5.0), _rt(20.0, 15.0)]
    assert profit_factor_gross(rts) is None


def test_profit_factor_gross_zero_wins_returns_none():
    rts = [_rt(-10.0, -15.0)]
    assert profit_factor_gross(rts) is None


def test_profit_factor_gross_typical():
    rts = [_rt(30.0, 25.0), _rt(-10.0, -15.0)]
    assert profit_factor_gross(rts) == pytest.approx(3.0)


def test_avg_fifo_pnl_per_roundtrip_gross():
    rts = [_rt(10.0, 5.0), _rt(20.0, 15.0)]
    assert avg_fifo_pnl_per_roundtrip_gross(rts) == pytest.approx(15.0)


def test_largest_win_loss_gross():
    rts = [_rt(10.0, 5.0), _rt(-5.0, -10.0), _rt(30.0, 25.0)]
    assert largest_win_loss_gross(rts) == (30.0, -5.0)
```

- [ ] **Step 2: Update analyze_sim.py imports + report block**

Edit `scripts/analyze_sim.py`.

Imports (lines 31-35) — add gross functions:

```python
from scripts._sim_metrics import (
    win_rate, win_rate_gross,
    total_pnl_net, roundtrip_count,
    collect_roundtrips,
    max_drawdown_pct, exit_type_distribution,
    largest_win_loss, largest_win_loss_gross,
    profit_factor, profit_factor_gross,
    avg_fifo_pnl_per_roundtrip, avg_fifo_pnl_per_roundtrip_gross,
)
```

Report block (lines 134-148) — add gross adjacent rows:

```python
    dd = await max_drawdown_pct(engine, session.id)
    win, loss = largest_win_loss(rts)
    win_g, loss_g = largest_win_loss_gross(rts)
    pf = profit_factor(rts)
    pf_g = profit_factor_gross(rts)

    rows = [
        ("win_rate_net", _fmt_pct(win_rate(rts))),
        ("win_rate_gross", _fmt_pct(win_rate_gross(rts))),
        # ... preserve existing rows around here per current layout ...
        ("max_drawdown_pct", _fmt_pct(dd)),
        ("largest_win_net", _fmt_pnl(win)),
        ("largest_win_gross", _fmt_pnl(win_g)),
        ("largest_loss_net", _fmt_pnl(loss)),
        ("largest_loss_gross", _fmt_pnl(loss_g)),
        ("profit_factor_net", "—" if pf is None else f"{pf:.2f}"),
        ("profit_factor_gross", "—" if pf_g is None else f"{pf_g:.2f}"),
    ]
```

Exact integration with existing row list depends on current layout — preserve other existing rows; insert gross variants adjacent to each net counterpart.

- [ ] **Step 3: Update test_analyze_sim.py column list**

Edit `tests/test_analyze_sim.py:170`. The test asserts a list of column names. Add the new gross variants per Step 2.

- [ ] **Step 4: Update diff_sim.py**

Same pattern — find metric rendering block (likely uses similar imports + row pattern), insert gross counterparts. Look at git blame on analyze_sim.py for matching diff_sim.py structure.

- [ ] **Step 5: Smoke test scripts manually**

Run:
```bash
python scripts/analyze_sim.py --help
python -c "from scripts._sim_metrics import win_rate_gross, profit_factor_gross, avg_fifo_pnl_per_roundtrip_gross, largest_win_loss_gross"
```
Expected: no errors.

If a sim DB is available:
```bash
python scripts/analyze_sim.py <some-sid>
```
Expected: output shows both `*_net` and `*_gross` rows.

- [ ] **Step 6: Run scripts tests**

Run: `pytest tests/test_analyze_sim.py tests/test_sim_metrics.py tests/test_drift_phase2_metrics.py -v`
Expected: PASS（含 8 个新 gross 单元测试 + drift guard `len == 32` 断言更新）

- [ ] **Step 7: Commit**

```bash
git add scripts/_sim_metrics.py scripts/analyze_sim.py scripts/diff_sim.py \
        tests/test_analyze_sim.py
git commit -m "$(cat <<'EOF'
feat(iter-tool-opt-net-pnl-metrics): scripts gross 视角对齐 src 双视角

_sim_metrics adds win_rate_gross / profit_factor_gross / avg_gross /
largest_win_loss_gross. analyze_sim + diff_sim reports render net + gross
side-by-side (matches src get_performance output).

Existing net functions keep no-suffix names (avoids breaking imports
and tests/test_drift_phase2_metrics.py:28 schema assertions).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Drift guard — src ↔ scripts FIFO parity test

**Files:**
- Create: `tests/test_metrics_src_scripts_parity.py`

- [ ] **Step 1: Write parity test**

Create `tests/test_metrics_src_scripts_parity.py`:

```python
"""src/services/metrics FIFO ↔ scripts/_sim_metrics.collect_roundtrips parity (spec §6.10)."""
from __future__ import annotations

import math
import pytest
from sqlalchemy import text


async def _setup_synthetic_sim_session(engine, sid: str, fee_rate: float, fills: list[tuple]):
    """Double-write sim_orders + trade_actions from single fill specs.

    Each fill spec: (event_type, side, price, amount, [trigger_reason])
      event_type ∈ {"open", "close", "liq"}
      side ∈ {"long", "short"}

    Fee = price × amount × fee_rate exactly (no stale_close_amount path).
    """
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO sessions "
            "(id, name, symbol, initial_balance, status, created_at, updated_at, "
            " exchange_type, timeframe, scheduler_interval_min, approval_enabled, "
            " token_budget, fee_rate) "
            "VALUES (:sid, :sid, 'BTC/USDT:USDT', 10000.0, 'active', "
            "        '2026-01-01T00:00:00', '2026-01-01T00:00:00', "
            "        'simulated', '15m', 15, 1, 500000, :fr)"
        ), {"sid": sid, "fr": fee_rate})

        for idx, spec in enumerate(fills):
            etype, side, price, amount = spec[:4]
            trigger = "liquidation" if etype == "liq" else "market"
            is_close = etype in ("close", "liq")
            ord_id = f"o-{idx}"
            order_side = "sell" if (side == "long" and is_close) or (side == "short" and not is_close) else "buy"
            order_type = "liquidation" if etype == "liq" else "market"
            fee = price * amount * fee_rate
            ts = f"2026-01-01T00:00:{idx:02d}"

            # sim_orders row (consumed by scripts FIFO) — enumerate all NOT NULL cols
            # (Python defaults bypass raw SQL: status / frozen_margin / leverage / created_at)
            await conn.execute(text(
                "INSERT INTO sim_orders "
                "(session_id, order_id, symbol, side, position_side, order_type, "
                " amount, filled_price, fee, status, frozen_margin, leverage, "
                " filled_at, created_at) "
                "VALUES (:sid, :oid, 'BTC/USDT:USDT', :side, :ps, :ot, "
                "        :amt, :px, :fee, 'filled', 0.0, 10, :ts, :ts)"
            ), {"sid": sid, "oid": ord_id, "side": order_side, "ps": side, "ot": order_type,
                "amt": amount, "px": price, "fee": fee, "ts": ts})

            # trade_actions row (consumed by src FIFO)
            pnl = None
            entry_price = None
            if is_close:
                if etype == "liq":
                    pnl = -200.0  # arbitrary; liquidation branch reverses
                else:
                    sign = 1 if side == "long" else -1
                    open_idx = next(i for i, s in enumerate(fills[:idx])
                                    if s[0] == "open" and s[1] == side)
                    entry_price = fills[open_idx][2]
                    pnl = (price - entry_price) * amount * sign

            await conn.execute(text(
                "INSERT INTO trade_actions "
                "(session_id, action, symbol, side, trigger_reason, price, "
                " pnl, fee, amount, entry_price, order_id, created_at) "
                "VALUES (:sid, 'order_filled', 'BTC/USDT:USDT', :side, :trig, :px, "
                "        :pnl, :fee, :amt, :ep, :oid, :ts)"
            ), {"sid": sid, "side": side, "trig": trigger, "px": price,
                "pnl": pnl, "fee": fee, "amt": amount, "ep": entry_price,
                "oid": ord_id, "ts": ts})


@pytest.mark.asyncio
async def test_src_scripts_fifo_parity_simple(engine):
    """Single open + single close: src ↔ scripts byte-equal."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    from scripts._sim_metrics import collect_roundtrips

    sid = "parity-simple"
    fee_rate = 0.0005
    await _setup_synthetic_sim_session(engine, sid, fee_rate, fills=[
        ("open", "long", 50000.0, 0.1),
        ("close", "long", 51000.0, 0.1),
    ])

    src_rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid)
    script_rts, caveats = await collect_roundtrips(engine, sid)
    assert caveats["stale_close_amount_count"] == 0
    assert len(src_rts) == len(script_rts) == 1
    assert math.isclose(src_rts[0].pnl_gross, script_rts[0].pnl_gross, abs_tol=1e-9)
    assert math.isclose(src_rts[0].pnl_net, script_rts[0].pnl_net, abs_tol=1e-9)
    assert math.isclose(src_rts[0].fee_open_share, script_rts[0].fee_open_share, abs_tol=1e-9)
    assert math.isclose(src_rts[0].fee_close_share, script_rts[0].fee_close_share, abs_tol=1e-9)


@pytest.mark.asyncio
async def test_src_scripts_fifo_parity_partial_close(engine):
    """Partial close 2 times: src ↔ scripts byte-equal."""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    from scripts._sim_metrics import collect_roundtrips

    sid = "parity-partial"
    fee_rate = 0.0005
    await _setup_synthetic_sim_session(engine, sid, fee_rate, fills=[
        ("open", "long", 50000.0, 0.1),
        ("close", "long", 51000.0, 0.05),
        ("close", "long", 49500.0, 0.05),
    ])

    src_rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid)
    script_rts, caveats = await collect_roundtrips(engine, sid)
    assert caveats["stale_close_amount_count"] == 0
    assert len(src_rts) == len(script_rts) == 2
    for s, t in zip(src_rts, script_rts):
        assert math.isclose(s.pnl_gross, t.pnl_gross, abs_tol=1e-9)
        assert math.isclose(s.pnl_net, t.pnl_net, abs_tol=1e-9)
        assert math.isclose(s.fee_open_share, t.fee_open_share, abs_tol=1e-9)
        assert math.isclose(s.fee_close_share, t.fee_close_share, abs_tol=1e-9)
```

- [ ] **Step 2: Run parity tests**

Run: `pytest tests/test_metrics_src_scripts_parity.py -v`
Expected: PASS

If failures due to ordering / dataclass field name mismatches, adjust:
- scripts `Roundtrip` has more fields than src `_Roundtrip` — compare common fields only
- scripts `collect_roundtrips` returns `(rts, caveats)` with `stale_close_amount_count`; src returns different caveat keys

- [ ] **Step 3: Commit**

```bash
git add tests/test_metrics_src_scripts_parity.py
git commit -m "$(cat <<'EOF'
test(iter-tool-opt-net-pnl-metrics): drift guard — src ↔ scripts FIFO parity

Synthetic fixture double-writes sim_orders + trade_actions; verifies
src/services/metrics._collect_roundtrips_from_trade_actions and
scripts/_sim_metrics.collect_roundtrips produce byte-equal Roundtrips
on math-consistent fixtures (per spec §6.10).

MDD not in parity scope — src is realized-only equity, scripts uses
broker total (state_snapshot.balance.total_usdt).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: End-to-end smoke + breaking tests final sweep + verification

**Known breaking tests (must verify all updated by Tasks 2-8)**:

1. `tests/test_metrics.py:17-25` `_add_fill` helper + `tests/test_metrics.py:40,78,91,103` 4 affected tests — Task 7 Step 5 rewrites helper + updates assertions (per-lot-pair + paired open+close).
2. `tests/test_get_performance.py:83,92-94,105-117,122-135,144-156,165-177,189-201` — 6 tests `(gross-based)` substring — Task 8 verified.
3. `tests/test_cycle_capture.py:106,220,95` — `p["pnl_pct"]` references — Task 2 verified.
4. `tests/test_v_cycle_metrics.py:39` — view JSON path — Task 1 verified.
5. `tests/test_display_cycle.py` — read key — Task 2 verified (if reference).
6. `tests/test_cli_app.py` — Task 3 verified.
7. `tests/test_tool_enhancement.py` — Task 8 Step 5 grep `(gross-based)` 命中后再更新（**可能无命中**）
8. `src/cli/display.py:31` PF None handling — Task 4 verified.
9. `src/agent/tools_perception.py:610` PF None handling — Task 4 verified.
10. `tests/test_drift_phase2_metrics.py:28` schema name list — Task 9 verified (asymmetric naming preserved).
11. `tests/test_analyze_sim.py:170` column list — Task 9 verified.
12. `tests/test_drift_phase2_metrics.py:18,19,38` METRIC_GROUPS 长度 + PnL partition — Task 9 Step 1c 已显式更新。

- [ ] **Step 1: Final grep sweep — old keys/labels**

```bash
grep -rn "(gross-based)" tests/ src/ --include="*.py"
grep -rn "\"pnl_pct\"" tests/ src/ --include="*.py" | grep -v "pnl_pct_of_"
grep -rn "profit_factor == float" tests/ src/ --include="*.py"
grep -rn "profit_factor == 0" tests/ src/ --include="*.py"
```

Expected: zero matches (except in this plan itself).

If any remain, update inline + commit minimal fix.

- [ ] **Step 2: Full test suite**

Run: `pytest -q 2>&1 | tail -30`
Expected: all 1756+ existing tests + new iter tests (~33-37) pass.

If any failures, investigate; common causes:
- Old fixture still references `pnl_pct` JSON key → update
- Hardcoded PF format string assumes float (now Optional) → wrap None check
- Per-lot-pair semantic change breaks test fixtures with partial close → update expected values

- [ ] **Step 3: Smoke test live entrypoint**

```bash
python -c "from src.cli.app import run; from src.services.metrics import MetricsService, PerformanceMetrics; print('imports ok')"
```
Expected: prints "imports ok"

If CLI has dry-run/health-check entry, run it.

- [ ] **Step 4: Verify migration on dev DB (if available)**

```bash
# Backup any existing development DB
cp data/tradebot.db data/tradebot.db.backup-pre-iter 2>/dev/null
# Trigger fresh DB init (verifies migration runs clean on Path 3 + Path 1)
python -c "from src.storage.database import init_db; import asyncio; asyncio.run(init_db('sqlite+aiosqlite:////tmp/iter-smoke.db'))"
# Verify
sqlite3 /tmp/iter-smoke.db "PRAGMA table_info(trade_actions);" | grep -E "amount|entry_price"
sqlite3 /tmp/iter-smoke.db "SELECT sql FROM sqlite_master WHERE name='v_cycle_metrics'" | grep pnl_pct_of_notional
```
Expected:
- Two rows for `amount` and `entry_price` columns
- One row for view containing `pnl_pct_of_notional`

- [ ] **Step 5: Final commit (if any sweep/fixture fixes were needed)**

```bash
git add tests/  # whatever changed
git commit -m "$(cat <<'EOF'
test(iter-tool-opt-net-pnl-metrics): final fixture sweep + e2e smoke

Updates any remaining stale fixture references (pnl_pct / gross-based /
profit_factor inf). Iter ready for PR.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Push branch**

```bash
git push -u origin iter-tool-opt-net-pnl-metrics
```

Watch CI for green status; ready for PR.

---

## Self-Review Checklist

1. **Spec coverage**: 
   - §0 摘要 → covered by Task 1 + Task 3 + Task 7 + Task 8
   - §1 实证 → reference only (spec context)
   - §2 决策表 → Task 1 (schema) + Task 4 (PF None) + Task 5 (FIFO no fee_rate arg) + Task 7 (compute) + Task 8 (output) + Task 9 (scripts)
   - §3 Architecture → Task 5 + Task 7
   - §4 Components → Task 1-9 (each C# mapped)
   - §5 Data Flow → Task 1 + Task 3 + Task 5 + Task 7
   - §6 Error Handling → Task 5 (legacy/invariant), Task 6 (edge cases), Task 7 (fee_rate NULL), Task 8 (caveat output)
   - §7 Testing → distributed in Tasks 5-10
   - §8 Output → Task 8
   - §9 Surface Δ → Task 1 (migration delta documented)
   - §10 OOS → no task (documented in spec)
   - §11 Trigger → no task (closure check in spec)

2. **Placeholder scan**: 
   - All code blocks complete
   - One explicit `<<< paste verbatim ... >>>` in Task 1 Step 6 — engineer-driven copy required (cannot be inlined without 195-line embed); marked WARNING

3. **Type consistency**:
   - `_Lot` / `_Roundtrip` / `_collect_roundtrips_from_trade_actions` defined Task 5, consumed Task 7 + Task 10 ✓
   - `pnl_pct_of_notional` JSON key consistent across cycle_capture (Task 2), display (Task 2), views.py (Task 1) ✓
   - `PerformanceMetrics` field set stable Task 4 onward; consumed by `get_performance` Task 8 ✓
   - `profit_factor: float | None` ripples through display.py:31 (Task 4), tools_perception.py:610 (Task 4), tools_perception.py:743 (Task 8), test_metrics.py:51 (Task 4) ✓

4. **Breaking tests**: explicit enumeration in Task 11 cross-references back to fixing task ✓

5. **Parity scope caveats** (per spec §6.10):
   - MDD src ↔ scripts **not** in parity scope (src = realized-only equity; scripts = broker total including unrealized)
   - Drift guard `roundtrip` byte-equal only enforced on **math-consistent synthetic fixtures**; real sim data parity may diverge due to `_derive_close_amount` 1% tolerance path or `created_at` vs `filled_at` ordering nuance — not a bug, document as known limitation if encountered in W3 sim
   - Real-data parity follow-up（if W3 数据触发）属独立议题
