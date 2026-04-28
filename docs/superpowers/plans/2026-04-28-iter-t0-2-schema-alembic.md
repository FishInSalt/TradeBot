# Iter 3 — Schema Evolution + Alembic Introduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce Alembic + 5 schema changes (tool_calls.args / trade_actions.cycle_id / decision_logs.{status,decision,market_summary}) + 109-row backfill in single migration; 0 existing tests modified.

**Architecture:** Three-state `init_db` sentinel (W1 pre-Alembic legacy DB walks `stamp base + upgrade head`); Cookbook "Sharing a Connection" + `engine.connect()` (no nested transactions); naming convention A.2 (decision_logs constraint align as batch_alter byproduct); `server_default="ok"` retained (avoids batch reconcile NULL violation).

**Tech Stack:** Python 3.12+ / SQLAlchemy 2.0 async / Alembic 1.13+ / pydantic-ai 1.78+ / SQLite (WAL mode).

**Spec:** `docs/superpowers/specs/2026-04-28-iter-t0-2-schema-alembic-design.md` (committed `0bd77c9`)
**Brainstorm:** `.working/pre-next-observation-todos.md` §B1/B2/T0-2

---

## Pre-flight Checklist (read before Task 1)

- ✅ Branch: `feature/iter-t0-2-schema-alembic` (already on)
- ✅ Spec committed: `0bd77c9`
- ⚠️ **Backup W1 DB before Task 5+** (`cp data/tradebot.db data/tradebot.db.iter3-backup`) — Task 10 will sandbox-validate against a copy, but having a hand-backup is cheap insurance.

---

## File Structure

```
NEW:
  alembic/                                              # Alembic migration framework
  ├── env.py                                            # Async template + connection injection + path normalization
  ├── script.py.mako                                    # Default alembic template (auto-generated, do not modify)
  └── versions/
      └── <rev>_initial_iter3_schema_evolution.py       # First migration

  alembic.ini                                           # Alembic config (script_location, file_template, prepend_sys_path)

  tests/test_alembic_migration.py                       # 4 migration tests
  tests/test_tool_call_recorder_args.py                 # 4 args field tests (or extend existing test_tool_call_recorder.py)
  tests/test_record_action_cycle_id.py                  # 2 cycle_id write tests

MODIFY:
  pyproject.toml                                        # Add alembic>=1.13.0 to [project.dependencies]
  src/storage/models.py                                 # Add NAMING_CONVENTION + 5 schema changes + 3 index renames
  src/storage/database.py                               # init_db three-state改造 + 6 helpers
  src/services/tool_call_recorder.py                    # Add args field write (line 91-98 block)
  src/agent/tools_execution.py                          # _record_action add cycle_id (line 27-37 block, 11 callers unchanged)
```

**File responsibilities:**
- `alembic/env.py`: Bridges `init_db` sync_conn injection vs CLI direct invocation; resolves async URL → sync; path-normalizes to repo_root.
- `alembic/versions/<rev>_*.py`: Self-contained migration (schema changes + index renames + backfill); `NAMING_CONVENTION` imported from `models.py`.
- `src/storage/database.py`: Three-state sentinel chooses migration path; six helpers (sentinel checks + alembic command wrappers).
- Tests are file-isolated to keep responsibilities clear; fixtures use `tmp_path` SQLite (not `:memory:`) for migration tests so alembic commands have a real file.

---

## Task 1: Add Alembic Dependency

**Files:**
- Modify: `pyproject.toml` line 6-18 ([project.dependencies] block)

- [ ] **Step 1: Add alembic to main dependencies**

Edit `pyproject.toml`, add `"alembic>=1.13.0",` to `[project.dependencies]` (NOT `[project.optional-dependencies].dev` — `init_db` calls `alembic.command.upgrade` in production runtime path).

After edit, the dependencies block should look like:

```toml
dependencies = [
    "pydantic-ai>=1.78,<2",
    "pydantic>=2.0",
    "ccxt>=4.0",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",
    "alembic>=1.13.0",
    "pandas>=2.0",
    "pandas-ta>=0.3",
    "rich>=13.0",
    "httpx>=0.27",
    "python-dotenv>=1.0",
    "pyyaml>=6.0",
]
```

- [ ] **Step 2: Sync dependencies**

Run: `uv sync`
Expected: Alembic 1.13+ installed; lockfile updated; no errors.

- [ ] **Step 3: Verify alembic importable**

Run: `uv run python -c "from alembic import command; from alembic.config import Config; print('alembic ok:', command.__name__)"`
Expected output: `alembic ok: alembic.command`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "$(cat <<'EOF'
feat(iter-t0-2): add alembic>=1.13.0 to main dependencies

Required for init_db production-path alembic.command.upgrade calls
introduced in subsequent tasks. Listed in [project.dependencies] not
[project.optional-dependencies].dev because production runtime needs it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: NAMING_CONVENTION + Base Metadata

**Files:**
- Modify: `src/storage/models.py` line 1-11 (imports + Base class)

- [ ] **Step 1: Add MetaData import + NAMING_CONVENTION constant**

Edit `src/storage/models.py`. Replace imports + Base:

```python
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Text, DateTime, ForeignKey, MetaData, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# Spec §3.3: Alembic naming convention. Permanent constant — never changes.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

- [ ] **Step 2: Run existing tests to confirm no regression**

Run: `uv run pytest -x -q 2>&1 | tail -20`
Expected: All existing tests still pass (898 tests). `index=True` auto-products names match convention exactly (no rename needed).

- [ ] **Step 3: Commit**

```bash
git add src/storage/models.py
git commit -m "$(cat <<'EOF'
feat(iter-t0-2): add NAMING_CONVENTION to Base metadata (A.2 decision)

Alembic-recommended naming convention. Existing 4 index=True products
(ix_<table>_session_id) match convention exactly — 0 rename needed.
3 manual __table_args__ Index() names will be renamed in Task 3.

Spec §3.3 / §G10.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: models.py Schema Changes (5 fields + 3 index renames)

**Files:**
- Modify: `src/storage/models.py` (TradeAction line 47-63 / DecisionLog line 66-80 / SimOrder line 127-148 / ToolCall line 151-170)

- [ ] **Step 1: Modify TradeAction — add cycle_id**

Find line 47-63 (TradeAction class). After `created_at` field, add `cycle_id`:

```python
class TradeAction(Base):
    """Agent 的交易操作日志 — append-only 事件模型。"""

    __tablename__ = "trade_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    action: Mapped[str] = mapped_column(String(30))
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trigger_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    cycle_id: Mapped[str | None] = mapped_column(String(50), nullable=True)        # Iter 3: §G3 — cycle correlation; nullable per §4.5 (历史数据约束)
```

- [ ] **Step 2: Modify DecisionLog — 3 changes (decision String length / status field / market_summary DEPRECATED comment / new index)**

Replace DecisionLog (line 66-80):

```python
class DecisionLog(Base):
    """One agent decision cycle — records what the agent decided and why."""

    __tablename__ = "decision_logs"
    __table_args__ = (
        Index("ix_decision_logs_session_id_cycle_id", "session_id", "cycle_id"),   # Iter 3: §G7 (T3-1 merged)
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(String(50))                              # Unique ID for this decision cycle
    trigger_type: Mapped[str] = mapped_column(String(20))                          # scheduled / conditional / alert
    market_summary: Mapped[str | None] = mapped_column(Text, nullable=True)        # DEPRECATED — see brainstorm §B2 (Python 源码注释，非 SQLAlchemy comment= 参数：SQLite 不支持 column COMMENT 子句，且 comment= 会引入 alembic check noise)
    decision: Mapped[str] = mapped_column(String(20))                              # String(50)→String(20) (spec §B1)
    status: Mapped[str] = mapped_column(String(30), default="ok", server_default="ok")  # 新增 (B1 双字段方案；String(30) per brainstorm 校准；server_default="ok" 与 DB schema 一致避免 alembic check noise，详见 §4.2 Step 4 "为什么保留 server_default")
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)             # Agent's reasoning (truncated to 500 chars)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)     # LLM model ID used for this cycle
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)                   # Total tokens consumed in this cycle
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 3: Modify SimOrder — index rename**

Find line 127-148 (SimOrder class). Replace `__table_args__`:

```python
class SimOrder(Base):
    """Simulated order — one row per submitted order in the simulated exchange."""

    __tablename__ = "sim_orders"
    __table_args__ = (Index("ix_sim_orders_session_id_status", "session_id", "status"),)   # rename: was ix_sim_orders_session_status

    # ... (rest of class unchanged)
```

- [ ] **Step 4: Modify ToolCall — add args field + 2 index renames**

Find line 151-170 (ToolCall class). Replace `__table_args__` and add `args` field:

```python
class ToolCall(Base):
    """每次 agent tool 调用一行（观察期埋点）。Append-only，无 UPDATE/DELETE 接口。"""

    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_session_id_tool_name_created_at", "session_id", "tool_name", "created_at"),   # rename: was ix_tool_calls_session_tool_time
        Index("ix_tool_calls_cycle_id", "cycle_id"),                                                        # rename: was ix_tool_calls_cycle
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"))
    cycle_id: Mapped[str] = mapped_column(String(50), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10))  # "ok" / "error"
    duration_ms: Mapped[int] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    args: Mapped[str | None] = mapped_column(Text, nullable=True)                  # Iter 3: §G2 — JSON dict of tool args, 4000 char cap, reasoning key stripped
```

- [ ] **Step 5: Run existing tests — expect failures because schema is now NEW but DB layer untouched**

Run: `uv run pytest -x -q tests/test_storage.py tests/test_metrics.py 2>&1 | tail -20`

Expected: Tests pass for `:memory:` and `tmp_path` fixtures (each fresh fixture gets new schema via `Base.metadata.create_all`). The full suite may have failures because some tests insert without `cycle_id` to TradeAction, but that's expected — model now allows nullable cycle_id, so writes with cycle_id=None are fine.

If any test fails referencing `decision_logs.market_summary` or status field absence, those are pre-existing tests that need no fix (they don't write to those new fields).

- [ ] **Step 6: Commit**

```bash
git add src/storage/models.py
git commit -m "$(cat <<'EOF'
feat(iter-t0-2): models.py 5 schema changes + 3 index renames

- TradeAction.cycle_id: String(50) nullable (§G3)
- DecisionLog.status: String(30) NOT NULL default 'ok' + server_default (§G4 / §B1 双字段)
- DecisionLog.decision: String(50)→String(20) (§G5 / §B1)
- DecisionLog.market_summary: DEPRECATED inline comment (§G6 / §B2; not dropped this iter)
- DecisionLog new index ix_decision_logs_session_id_cycle_id (§G7 / T3-1 merged)
- ToolCall.args: Text nullable, 4000 char cap (§G2)
- 3 hand-written index renames to align NAMING_CONVENTION

Spec §4.1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

> ⚠️ **"Half-changed" state warning** (Tasks 3-5 in-progress): After this commit, `models.py` has new `status` column (NOT NULL) but `data/tradebot.db` does NOT yet (Task 6 changes init_db; Task 5 writes the migration). **DO NOT run `python main.py` against the W1 DB** during Tasks 3-5 — ORM writes will hit `OperationalError: no such column: status`. If you need to test interactively before Task 6 completes, use a fresh sandbox DB (`TRADEBOT_DB_URL=sqlite+aiosqlite:///$(mktemp -u --suffix=.db)`) which goes through `Base.metadata.create_all` (path 3) with the new schema directly.

---

## Task 4: Alembic Infrastructure (alembic.ini + env.py)

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako` (default template)
- Create: `alembic/versions/.gitkeep` (empty placeholder; first migration in Task 5)

- [ ] **Step 1: Create alembic.ini at repo root**

Create file `alembic.ini` with:

```ini
[alembic]
script_location = alembic
file_template = %%(rev)s_%%(slug)s
sqlalchemy.url =
prepend_sys_path = %(here)s

# Logging configuration (default alembic template)
[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

Key fields:
- `script_location = alembic` — relative to alembic.ini (repo root)
- `sqlalchemy.url =` (empty) — env.py injects via `cfg.attributes["connection"]` (production) or `_resolved_sync_url()` (CLI)
- `prepend_sys_path = %(here)s` — `%(here)s` resolves to alembic.ini's directory (repo root), avoids cwd dependency

- [ ] **Step 2: Create alembic/env.py**

Create `alembic/env.py`:

```python
"""Alembic environment — async engine + connection injection + path normalization.

Spec §4.3 / §3.2.
"""
from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool
from alembic import context

from src.storage.models import Base   # 必需：autogenerate / alembic check 依赖

# === 顶层 module setup ===
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 关键：必须显式赋值，autogenerate / alembic check 全靠这一行
target_metadata = Base.metadata


def _resolved_sync_url() -> str:
    """CLI 直调路径：从 env var 或 settings 派生 sync URL，路径锚定到 repo root（与 app.py:434-438 normalization 一致）"""
    from src.config import load_settings
    # 优先 env var（CI override / ad-hoc 测试路径；实测 load_settings 不处理 database.url env_overrides 故必走此路径）
    env_url = os.getenv("TRADEBOT_DB_URL")
    if env_url:
        async_url = env_url
    else:
        # env_overrides={} 跳过 dotenv 读取（alembic 上下文不需要 OKX_* env vars）
        async_url = load_settings(env_overrides={}).database.url
    # 同步化 (sqlite+aiosqlite → sqlite)
    sync_url = async_url.replace("sqlite+aiosqlite:", "sqlite:")
    # Path normalization: 相对路径 → 绝对路径（锚定 repo root，alembic/env.py 在 repo_root/alembic/env.py）
    if sync_url.startswith("sqlite:///") and not sync_url.startswith("sqlite:////"):
        relative_path = sync_url[len("sqlite:///"):]
        if not Path(relative_path).is_absolute():
            repo_root = Path(__file__).resolve().parents[1]   # alembic/env.py → parents[1] = repo root
            sync_url = f"sqlite:///{(repo_root / relative_path).as_posix()}"
    return sync_url


def run_migrations_online() -> None:
    # 优先读 init_db 注入的 connection；CLI 直调 (alembic upgrade head) 时为 None
    connectable = config.attributes.get("connection", None)
    if connectable is None:
        # CLI 直调路径：自建 engine + connection（path normalization 见 _resolved_sync_url）
        sync_engine = create_engine(_resolved_sync_url(), poolclass=pool.NullPool)
        with sync_engine.connect() as conn:
            do_run_migrations(conn)
    else:
        # init_db 注入路径：复用外层 sync_conn
        do_run_migrations(connectable)


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # render_as_batch=True 让 SQLite 自动用 batch_alter（add column 等不需，alter type 必需）
        render_as_batch=True,
    )
    # alembic 自管事务边界（与 init_db 外层 engine.connect() 配合）
    with context.begin_transaction():
        context.run_migrations()


# === 入口 ===
if context.is_offline_mode():
    # offline 模式（生成 SQL 脚本不实际运行）— 简化版，本项目 production 不用 offline
    raise NotImplementedError("Offline mode not supported; use online migrations")
else:
    run_migrations_online()
```

- [ ] **Step 3: Create alembic/script.py.mako (default template)**

Create `alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Create empty versions/ directory**

```bash
mkdir -p alembic/versions
touch alembic/versions/.gitkeep
```

- [ ] **Step 5: Verify alembic env.py loads (dry-run on a temp DB)**

> ⚠️ **Use a temp DB, not W1 production**: without `TRADEBOT_DB_URL`, env.py CLI fallback would resolve to `data/tradebot.db` via `load_settings()`. `alembic current` is read-only (won't pollute W1) but should not silently target production for a dry-run.

Run:

```bash
TRADEBOT_DB_URL="sqlite+aiosqlite:///$(mktemp -u --suffix=.db)" uv run alembic current 2>&1 | head -5
```

Expected: No error; output may say "INFO  [alembic.runtime.migration] Context impl SQLiteImpl." then either empty or a revision line. Critically: no `ImportError` / `ModuleNotFoundError` / `KeyError`.

If `ModuleNotFoundError: src` appears: `prepend_sys_path = %(here)s` not picked up; verify alembic.ini is in repo root and you're running from repo root.

- [ ] **Step 6: Commit**

```bash
git add alembic.ini alembic/env.py alembic/script.py.mako alembic/versions/.gitkeep
git commit -m "$(cat <<'EOF'
feat(iter-t0-2): Alembic infrastructure (alembic.ini + async env.py)

- alembic.ini: script_location, file_template (%(rev)s_%(slug)s),
  sqlalchemy.url left empty (env.py injects), prepend_sys_path %(here)s
- env.py: async-style with connection injection (Cookbook "Sharing a
  Connection") + _resolved_sync_url with TRADEBOT_DB_URL env override
  fallback + path normalization to repo_root
- target_metadata = Base.metadata (required for autogenerate/alembic check)
- render_as_batch=True for SQLite ALTER COLUMN limitation

Spec §3.2 / §4.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: First Migration (initial_iter3_schema_evolution)

**Files:**
- Create: `alembic/versions/<rev>_initial_iter3_schema_evolution.py`

Note: alembic generates `<rev>` as a 12-char hex; the slug from `--message` is appended.

- [ ] **Step 1: Generate migration scaffold**

Run: `uv run alembic revision -m "initial_iter3_schema_evolution"`

Expected: New file at `alembic/versions/<rev>_initial_iter3_schema_evolution.py` with empty `upgrade()` / `downgrade()`. Note the generated `<rev>` for git add later.

- [ ] **Step 2: Implement upgrade() and downgrade() bodies per spec §4.2**

> ⚠️ **DO NOT overwrite the entire file with `Write`**. Alembic's `revision`, `down_revision`, `branch_labels`, `depends_on` values were auto-generated in Step 1 and are unique per migration — overwriting them with `<auto>` placeholders breaks the revision chain. Use `Edit` to modify ONLY:
> 1. **Add to header imports** (preserve revision identifiers below): `from src.storage.models import NAMING_CONVENTION`
> 2. **Replace `upgrade()` body** (the `pass` placeholder)
> 3. **Replace `downgrade()` body** (the `pass` placeholder)
>
> Section breakdown:

**Section A — Header (PRESERVE alembic-generated values; only ADD the import line)**

The auto-scaffolded file already contains a header like below. Keep `revision` / `down_revision` / etc. AS-IS. Only ADD `from src.storage.models import NAMING_CONVENTION` to the imports:

```python
"""initial_iter3_schema_evolution

Revision ID: <ALEMBIC-GENERATED-DO-NOT-MODIFY>
Revises:
Create Date: <ALEMBIC-GENERATED-DO-NOT-MODIFY>

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from src.storage.models import NAMING_CONVENTION   # ← ADD THIS LINE

# revision identifiers, used by Alembic.
revision: str = "<ALEMBIC-GENERATED-DO-NOT-MODIFY>"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
```

**Section B — Replace `upgrade()` body** (the `pass` placeholder generated in Step 1):

```python
def upgrade() -> None:
    # Step 1: 索引 rename（廉价 drop+create，不动数据）
    op.drop_index("ix_sim_orders_session_status", table_name="sim_orders")
    op.create_index("ix_sim_orders_session_id_status", "sim_orders", ["session_id", "status"])
    op.drop_index("ix_tool_calls_session_tool_time", table_name="tool_calls")
    op.create_index(
        "ix_tool_calls_session_id_tool_name_created_at",
        "tool_calls",
        ["session_id", "tool_name", "created_at"],
    )
    op.drop_index("ix_tool_calls_cycle", table_name="tool_calls")
    op.create_index("ix_tool_calls_cycle_id", "tool_calls", ["cycle_id"])

    # Step 2: tool_calls.args 直接 ADD COLUMN（无重建）
    op.add_column("tool_calls", sa.Column("args", sa.Text(), nullable=True))

    # Step 3: trade_actions.cycle_id 直接 ADD COLUMN
    op.add_column("trade_actions", sa.Column("cycle_id", sa.String(50), nullable=True))

    # Step 4: decision_logs batch_alter（3 个 ops + 约束名对齐 A.2）
    # 合并语义: Step 4a-4c 在 batch_alter 上下文内合并为单次 SQLite 表重建
    # （CREATE _new + INSERT SELECT + DROP old + RENAME），不是 3 次 109 行数据 copy。
    # 由 alembic batch 退出时合并 ops 行为（recreate-on-exit）保证。
    with op.batch_alter_table(
        "decision_logs",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        # 4a: decision String(50)→String(20)
        batch_op.alter_column("decision", type_=sa.String(20), existing_nullable=False)
        # 4b: 新增 status，server_default='ok' 让 INSERT _new SELECT old 满足 NOT NULL
        # 关键设计：server_default **保留**（不在 batch 内 alter 移除），原因详见 spec §4.2 "为什么保留 server_default"
        batch_op.add_column(
            sa.Column("status", sa.String(30), nullable=False, server_default="ok")
        )
        # 4c: 新增 ix_decision_logs_session_id_cycle_id（spec §T3-1 合并）
        batch_op.create_index(
            "ix_decision_logs_session_id_cycle_id",
            ["session_id", "cycle_id"],
        )

    # Step 5: 历史数据 backfill（109 行 decision 标 legacy）
    # 5a: 防御性 catch-net — pathological 行（decision='usage_limit_exceeded'）先抓 status 再标 legacy
    # 实测 W1 DB 0 行匹配（spec §1.2 注脚），此 UPDATE 在 W1 现状下空跑；保留是防御性零成本
    # 注意顺序：必须先抓 status 再无差别 backfill decision，否则 5b 跑完无 'usage_limit_exceeded' 行可抓
    op.execute(
        "UPDATE decision_logs SET status = 'usage_limit_exceeded' "
        "WHERE decision = 'usage_limit_exceeded'"
    )
    # 5b: 全部 decision 标 legacy（109 行，含 5a 已抓 status 的行）
    op.execute("UPDATE decision_logs SET decision = 'legacy'")
    # status 字段对未匹配 5a 的行已被 Step 4b 的 server_default 自动填充为 'ok'
```

**Section C — Replace `downgrade()` body** (the `pass` placeholder):

```python
def downgrade() -> None:
    # Step 5 逆向：decision='legacy' 不还原（破坏性 backfill 无可恢复源；B1 决议 109 行不可信）

    # Step 4 逆向：decision_logs batch_alter 全部回退
    with op.batch_alter_table("decision_logs", naming_convention=NAMING_CONVENTION) as batch_op:
        batch_op.drop_index("ix_decision_logs_session_id_cycle_id")
        batch_op.drop_column("status")
        batch_op.alter_column("decision", type_=sa.String(50), existing_nullable=False)

    # Step 3 逆向
    op.drop_column("trade_actions", "cycle_id")

    # Step 2 逆向
    op.drop_column("tool_calls", "args")

    # Step 1 逆向：索引名复原（drop convention 名 → 重建旧手写名）
    op.drop_index("ix_tool_calls_cycle_id", table_name="tool_calls")
    op.create_index("ix_tool_calls_cycle", "tool_calls", ["cycle_id"])
    op.drop_index("ix_tool_calls_session_id_tool_name_created_at", table_name="tool_calls")
    op.create_index(
        "ix_tool_calls_session_tool_time",
        "tool_calls",
        ["session_id", "tool_name", "created_at"],
    )
    op.drop_index("ix_sim_orders_session_id_status", table_name="sim_orders")
    op.create_index("ix_sim_orders_session_status", "sim_orders", ["session_id", "status"])
```

> **Reminder**: Section A header values (`revision`, `down_revision`, `branch_labels`, `depends_on`) were generated by `alembic revision` in Step 1 — **DO NOT modify them**. The `<ALEMBIC-GENERATED-DO-NOT-MODIFY>` placeholders shown above are illustrative; the real file already has correct values.

- [ ] **Step 3: Commit (no smoke run here — see note below)**

> ⚠️ **No smoke validation at this point**. The migration's first op is `op.drop_index("ix_sim_orders_session_status", ...)` which assumes W1 business tables exist. Running `alembic upgrade head` against an empty temp DB would hit `OperationalError: no such index` (same ALTER-not-CREATE root cause as Task 7/Task 10 §critical insight).
>
> Migration smoke validation is **deferred to Task 7** (`test_init_db_path_3_for_empty_db` covers DDL via init_db path 3; `test_upgrade_from_w1_like_data` covers actual `command.upgrade(cfg, "head")` with a hand-written W1 fixture). Task 5 commits "untested-but-syntax-valid" migration; Task 6 adds init_db three-state; Task 7 runs the migration tests end-to-end. If a syntax error slipped into the migration body, Task 6's `_alembic_config` import or Task 7's tests will surface it within ~2 tasks.

```bash
git add alembic/versions/*.py
git commit -m "$(cat <<'EOF'
feat(iter-t0-2): first migration — initial_iter3_schema_evolution

Single migration containing all 5 schema changes + 3 index renames + 109-row
backfill (W1 data marked decision='legacy'; usage_limit_exceeded catch-net).

decision_logs batch_alter combines 3 ops into single SQLite table recreate
(decision String(50)→String(20) + add status NOT NULL server_default 'ok'
+ new ix_decision_logs_session_id_cycle_id). server_default RETAINED to
avoid alembic batch reconcile NULL violation.

Spec §4.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: database.py Three-State init_db Refactor

**Files:**
- Modify: `src/storage/database.py` (entire file replace)

- [ ] **Step 1: Replace src/storage/database.py with three-state version**

Replace entire file content:

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.storage.models import Base

if TYPE_CHECKING:
    from alembic.config import Config

_session_factories: dict[int, async_sessionmaker[AsyncSession]] = {}


async def init_db(url: str) -> AsyncEngine:
    engine = create_async_engine(url, echo=False)
    # engine.connect() 不开外层事务，让 alembic 自管事务边界（Cookbook "Sharing a Connection"）
    async with engine.connect() as conn:
        has_alembic = await conn.run_sync(_has_alembic_version_table)
        if has_alembic:
            # 路径 1: 已 in-Alembic 链 → alembic upgrade head（no-op 若已 head）
            await conn.run_sync(_alembic_upgrade_head)
        elif await conn.run_sync(_has_business_tables):
            # 路径 2: pre-Alembic legacy DB（W1 当前状态）→ stamp base + upgrade head
            # stamp base 标记到 migration 链起点之前，让 legacy DB 真正经历首个 migration
            await conn.run_sync(_alembic_stamp_base)
            await conn.run_sync(_alembic_upgrade_head)
        else:
            # 路径 3: 空库 / 测试 fixture → create_all + stamp head（快路径，跳过 migration 链）
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_alembic_stamp_head)
    # WAL pragma 仍在外层（与原行为一致）
    async with engine.connect() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.commit()
    _session_factories[id(engine)] = async_sessionmaker(engine, expire_on_commit=False)
    return engine


@asynccontextmanager
async def get_session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = _session_factories.get(id(engine))
    if factory is None:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        _session_factories[id(engine)] = factory
    async with factory() as session:
        yield session


# === Alembic helpers (sync, called via conn.run_sync) ===


def _has_alembic_version_table(sync_conn) -> bool:
    """检测 alembic_version 表是否存在（sentinel #1: 已 in-Alembic 链）

    SQLite-specific: sqlite_master 是 SQLite 系统表；本 Iter 仅支持 SQLite。
    """
    result = sync_conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
    )
    return result.scalar() is not None


def _has_business_tables(sync_conn) -> bool:
    """检测核心业务表是否存在（sentinel #2: pre-Alembic legacy DB vs 空库）

    用 sessions 作 sentinel（最早创建的核心表，所有 W1 DB 都有此表）。
    SQLite-specific: sqlite_master。
    """
    result = sync_conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
    )
    return result.scalar() is not None


def _alembic_config(sync_conn) -> "Config":
    """构造 Alembic Config，路径锚定到 repo root（避免 cwd 依赖）"""
    from alembic.config import Config
    # database.py 在 src/storage/，parents[2] = repo root
    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.attributes["connection"] = sync_conn   # connection 注入
    return cfg


def _alembic_upgrade_head(sync_conn) -> None:
    """复用外层 conn 跑 upgrade head（路径 1 / 2 共用）"""
    from alembic import command
    command.upgrade(_alembic_config(sync_conn), "head")


def _alembic_stamp_head(sync_conn) -> None:
    """空库 create_all 后标记为 head（路径 3 快路径终点）"""
    from alembic import command
    command.stamp(_alembic_config(sync_conn), "head")


def _alembic_stamp_base(sync_conn) -> None:
    """pre-Alembic legacy DB 标记到 migration 链起点之前（路径 2 起点）

    后续 _alembic_upgrade_head 会从 base 跑全部 migration，包括 batch_alter 重建 decision_logs。
    """
    from alembic import command
    command.stamp(_alembic_config(sync_conn), "base")
```

- [ ] **Step 2: Run existing fixture-based tests — confirm path 3 (create_all + stamp head) works**

Run: `uv run pytest -x -q tests/test_storage.py 2>&1 | tail -20`

Expected: All tests in test_storage.py pass. Each fixture creates a fresh `:memory:` or `tmp_path` DB → no `alembic_version` → no business tables → path 3.

If a test fails with `OperationalError: no such table: alembic_version`: stamp head not committing. Check that `_alembic_stamp_head` is being called.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -x -q 2>&1 | tail -10`
Expected: 898 + new tests pass. (Path 3 covers all fixtures; legacy path 1/2 covered in Task 7 migration tests.)

- [ ] **Step 4: Commit**

```bash
git add src/storage/database.py
git commit -m "$(cat <<'EOF'
feat(iter-t0-2): init_db three-state sentinel + Alembic helpers

Spec §3.1 / §4.3. Three branches:
1. alembic_version exists → upgrade head (in-Alembic chain)
2. business tables exist → stamp base + upgrade head (pre-Alembic legacy /
   W1 current state — let legacy DB experience migration)
3. empty DB / test fixture → create_all + stamp head (fast path)

engine.connect() (no engine.begin()) avoids nested transaction with alembic.
Connection injection via cfg.attributes["connection"] (Cookbook "Sharing a
Connection"). Config path anchored to repo_root (avoids cwd dependency).

6 helpers: _has_alembic_version_table / _has_business_tables /
_alembic_config / _alembic_upgrade_head / _alembic_stamp_head /
_alembic_stamp_base.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Migration Tests (4 cases)

**Files:**
- Create: `tests/test_alembic_migration.py`

- [ ] **Step 1: Create migration test scaffold**

> ⚠️ **Critical design insight (per spec §5.2 corrected)**: Migration `upgrade()` Step 1 first line is `op.drop_index("ix_sim_orders_session_status", ...)` which **assumes W1 business tables already exist**. Empty DB direct `alembic upgrade head` hits `OperationalError: no such index`. Empty DB production path is **init_db path 3** (`create_all + stamp head`), NOT migration upgrade. Migration tests using `command.upgrade` must first hand-write FULL W1 schema (including `sim_orders` + `ix_sim_orders_session_status`).

Create `tests/test_alembic_migration.py`:

```python
"""Iter 3 migration tests — covers three-state sentinel + batch_alter + backfill.

Spec §5.2.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config


@pytest.fixture
def alembic_cfg_factory(monkeypatch):
    """Factory that builds Alembic Config + sets TRADEBOT_DB_URL via monkeypatch (auto-cleanup).

    Test isolation: monkeypatch reverts env var after each test; safe under pytest-xdist concurrency.
    """
    def _factory(db_path: Path) -> Config:
        monkeypatch.setenv("TRADEBOT_DB_URL", f"sqlite+aiosqlite:///{db_path}")
        repo_root = Path(__file__).resolve().parents[1]
        return Config(str(repo_root / "alembic.ini"))
    return _factory


def _create_pre_alembic_schema(db_path: Path) -> None:
    """Hand-write FULL W1 schema for migration testing (path 2 fixture).

    MUST include all tables/indexes that migration upgrade() references:
    - sim_orders + ix_sim_orders_session_status (Step 1 drops this index)
    - tool_calls + ix_tool_calls_session_tool_time + ix_tool_calls_cycle (Step 1 drops these)
    - decision_logs + ix_decision_logs_session_id (Step 4 batch_alter rebuilds this table)
    - trade_actions (Step 3 add column)
    - sessions (FK target for all above)

    Schema matches spec §4.1 BEFORE Iter 3 changes.
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE sessions (
            id VARCHAR(36) NOT NULL PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            symbol VARCHAR(50) NOT NULL,
            persona_config TEXT,
            model_config TEXT,
            initial_balance FLOAT NOT NULL,
            status VARCHAR(20) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            exchange_type VARCHAR(20) NOT NULL,
            timeframe VARCHAR(10) NOT NULL,
            scheduler_interval_min INTEGER NOT NULL,
            approval_enabled BOOLEAN NOT NULL,
            alert_config TEXT,
            fee_rate FLOAT,
            token_budget INTEGER NOT NULL,
            last_active_at DATETIME
        );
        CREATE TABLE decision_logs (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            cycle_id VARCHAR(50) NOT NULL,
            trigger_type VARCHAR(20) NOT NULL,
            market_summary TEXT,
            decision VARCHAR(50) NOT NULL,
            reasoning TEXT,
            model_used VARCHAR(100),
            tokens_used INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_decision_logs_session_id ON decision_logs (session_id);
        CREATE TABLE trade_actions (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            action VARCHAR(30) NOT NULL,
            order_id VARCHAR(36),
            symbol VARCHAR(50) NOT NULL,
            side VARCHAR(10),
            trigger_reason VARCHAR(20),
            price FLOAT,
            pnl FLOAT,
            reasoning TEXT,
            fee FLOAT,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_trade_actions_session_id ON trade_actions (session_id);
        CREATE TABLE tool_calls (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            cycle_id VARCHAR(50) NOT NULL,
            tool_name VARCHAR(60) NOT NULL,
            status VARCHAR(10) NOT NULL,
            duration_ms INTEGER NOT NULL,
            error_type VARCHAR(100),
            created_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_tool_calls_session_tool_time ON tool_calls (session_id, tool_name, created_at);
        CREATE INDEX ix_tool_calls_cycle ON tool_calls (cycle_id);
        CREATE TABLE sim_orders (
            id INTEGER NOT NULL PRIMARY KEY,
            session_id VARCHAR(36) NOT NULL,
            order_id VARCHAR(36) NOT NULL UNIQUE,
            symbol VARCHAR(50) NOT NULL,
            side VARCHAR(10) NOT NULL,
            position_side VARCHAR(10) NOT NULL,
            order_type VARCHAR(20) NOT NULL,
            amount FLOAT NOT NULL,
            trigger_price FLOAT,
            status VARCHAR(20) NOT NULL,
            filled_price FLOAT,
            fee FLOAT,
            filled_at DATETIME,
            created_at DATETIME NOT NULL,
            frozen_margin FLOAT NOT NULL DEFAULT 0.0,
            leverage INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(session_id) REFERENCES sessions (id)
        );
        CREATE INDEX ix_sim_orders_session_status ON sim_orders (session_id, status);
    """)
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_init_db_path_3_for_empty_db(tmp_path: Path) -> None:
    """Path 3 (empty DB → create_all + stamp head): NO migration upgrade run.

    Critical: migration upgrade() in empty DB hits "no such index" — first migration is
    ALTER not CREATE. Empty DB production path is init_db path 3.

    Asserts:
    1. Schema bootstrapped via Base.metadata.create_all (args / cycle_id / status / new index)
    2. alembic_version table stamped to head (sentinel #1 for next init_db call)
    """
    from src.storage.database import init_db

    db_path = tmp_path / "empty.db"
    await init_db(f"sqlite+aiosqlite:///{db_path}")

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Schema completeness
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(decision_logs)")}
    assert "status" in cols, "status column missing"
    assert cols["status"][3] == 1, "status should be NOT NULL"
    assert cols["decision"][2] == "VARCHAR(20)", f"decision should be VARCHAR(20), got {cols['decision'][2]}"
    assert "args" in {r[1] for r in cur.execute("PRAGMA table_info(tool_calls)")}
    assert "cycle_id" in {r[1] for r in cur.execute("PRAGMA table_info(trade_actions)")}
    indexes = {r[1] for r in cur.execute("SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='decision_logs'")}
    assert "ix_decision_logs_session_id_cycle_id" in indexes

    # 2. alembic_version stamped to head
    av = list(cur.execute("SELECT version_num FROM alembic_version"))
    assert len(av) == 1, f"alembic_version should have exactly 1 row, got {len(av)}"
    conn.close()


def test_upgrade_from_w1_like_data(tmp_path: Path, alembic_cfg_factory) -> None:
    """Path 2: pre-Alembic legacy DB with mock rows → batch_alter + backfill.

    Fixture builds FULL W1 schema (incl sim_orders) so migration Step 1 drop_index has target.
    Mock data: 4 rows decision='completed' + 1 row decision='usage_limit_exceeded'.
    Asserts:
    1. Migration does not raise (covers batch_alter merge semantics + INSERT SELECT NOT NULL path)
    2. All 5 rows have decision='legacy'
    3. 4 rows status='ok' (from server_default) + 1 row status='usage_limit_exceeded' (catch-net)
    """
    db_path = tmp_path / "w1_like.db"
    _create_pre_alembic_schema(db_path)

    # Insert 5 rows (4 completed + 1 usage_limit_exceeded)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO sessions (id, name, symbol, initial_balance, status, created_at, updated_at,
                              exchange_type, timeframe, scheduler_interval_min, approval_enabled,
                              token_budget)
        VALUES ('s1', 'test', 'BTC/USDT:USDT', 100.0, 'active',
                '2026-04-27T00:00:00+00:00', '2026-04-27T00:00:00+00:00',
                'simulated', '15m', 15, 1, 500000)
    """)
    for i, dec in enumerate(["completed", "completed", "completed", "completed", "usage_limit_exceeded"]):
        cur.execute("""
            INSERT INTO decision_logs (session_id, cycle_id, trigger_type, decision, tokens_used, created_at)
            VALUES ('s1', ?, 'scheduled', ?, 0, '2026-04-27T00:00:00+00:00')
        """, (f"cyc-{i}", dec))
    conn.commit()
    conn.close()

    # Run migration (must not raise)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")

    # Verify backfill
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    decisions = dict(cur.execute("SELECT decision, COUNT(*) FROM decision_logs GROUP BY decision"))
    assert decisions == {"legacy": 5}, f"expected all 5 rows decision='legacy', got {decisions}"
    statuses = dict(cur.execute("SELECT status, COUNT(*) FROM decision_logs GROUP BY status"))
    assert statuses == {"ok": 4, "usage_limit_exceeded": 1}, f"expected 4 ok + 1 usage_limit_exceeded, got {statuses}"
    conn.close()


def test_downgrade_then_upgrade(tmp_path: Path, alembic_cfg_factory) -> None:
    """From W1-like fixture: upgrade head → downgrade -1 → upgrade head reentrant + idempotent."""
    db_path = tmp_path / "reentrant.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")

    # Verify final state has all new fields
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    assert "status" in {r[1] for r in cur.execute("PRAGMA table_info(decision_logs)")}
    assert "args" in {r[1] for r in cur.execute("PRAGMA table_info(tool_calls)")}
    conn.close()


def test_upgrade_when_already_head(tmp_path: Path, alembic_cfg_factory) -> None:
    """Production critical path: alembic upgrade head when already at head is no-op + no error.

    Three-state sentinel #1 (alembic_version exists) runs upgrade head every init_db call.
    """
    db_path = tmp_path / "already_head.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)

    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # Second call should be no-op

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = list(cur.execute("SELECT version_num FROM alembic_version"))
    assert len(rows) == 1
    conn.close()
```

- [ ] **Step 2: Run migration tests**

Run: `uv run pytest tests/test_alembic_migration.py -v 2>&1 | tail -30`
Expected: 4 tests pass.

If `test_upgrade_from_w1_like_data` fails with NOT NULL violation: server_default not applied during INSERT SELECT — verify migration Task 5 Step 4b kept `server_default="ok"`.

If `test_downgrade_then_upgrade` fails: likely missing index or column in downgrade(); check spec §4.2 downgrade body.

- [ ] **Step 3: Commit**

```bash
git add tests/test_alembic_migration.py
git commit -m "$(cat <<'EOF'
test(iter-t0-2): 4 migration tests covering three-state sentinel

- test_init_db_path_3_for_empty_db: path 3 via init_db API; schema bootstrap
  + alembic_version stamped to head (NO migration upgrade — first migration
  is ALTER, would hit "no such index" on empty DB)
- test_upgrade_from_w1_like_data: path 2 (legacy DB) with FULL W1 fixture
  (sessions + sim_orders + ix_sim_orders_session_status + ...), 4+1 mock
  rows; asserts migration does not raise + backfill correctness
  (covers batch_alter merge + INSERT SELECT NOT NULL path)
- test_downgrade_then_upgrade: reentrant from W1-like fixture
- test_upgrade_when_already_head: production no-op idempotency from W1 fixture

Uses pytest monkeypatch.setenv via alembic_cfg_factory fixture for test
isolation (auto-cleanup, safe under pytest-xdist).

Spec §5.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: tool_call_recorder args Write (TDD)

**Files:**
- Create: `tests/test_tool_call_recorder_args.py`
- Modify: `src/services/tool_call_recorder.py` line 91-98 (session.add(ToolCall) block)

- [ ] **Step 1: Write 4 failing tests**

> ⚠️ **Mock compatibility risk** (implementation note): The helper below uses `MagicMock()` for `args: ValidatedToolArgs` parameter. pydantic-ai v1.78+ `wrap_tool_execute` may internally call `args.model_dump()` / `args.values` (auto-stubbed by MagicMock — likely OK). However, if pydantic-ai uses `isinstance(args, ValidatedToolArgs)` strict check, MagicMock will fail it. **If tests raise `TypeError: expected ValidatedToolArgs`, switch to a real `ValidatedToolArgs` instance** (import: `from pydantic_ai.capabilities import ValidatedToolArgs`; constructor signature documented in pydantic-ai `messages.py`). Implementer verifies by running tests in Step 2.

Create `tests/test_tool_call_recorder_args.py`:

```python
"""Iter 3 tool_call_recorder.args field write tests.

Spec §5.3.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.storage.database import init_db, get_session
from src.storage.models import ToolCall


@pytest.fixture
async def engine(tmp_path: Path):
    return await init_db(f"sqlite+aiosqlite:///{tmp_path}/recorder.db")


async def _run_recorder(engine, deps, call_args: Any) -> str | None:
    """Helper: invoke ToolCallRecorder with mock call.args, return DB-stored args field."""
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    call = MagicMock()
    call.tool_name = "test_tool"
    call.args = call_args
    call.args_as_dict = MagicMock(return_value=dict(call_args) if isinstance(call_args, dict) else json.loads(call_args) if isinstance(call_args, str) else {})

    tool_def = MagicMock()
    handler = AsyncMock(return_value="ok")

    ctx = MagicMock()
    ctx.deps = deps

    await recorder.wrap_tool_execute(
        ctx, call=call, tool_def=tool_def, args=MagicMock(), handler=handler,
    )

    async with get_session(engine) as session:
        from sqlalchemy import select
        result = await session.execute(select(ToolCall.args).order_by(ToolCall.id.desc()).limit(1))
        return result.scalar()


@pytest.fixture
def deps(engine):
    """Mock TradingDeps minimum surface (session_id / cycle_id / db_engine)."""
    d = MagicMock()
    d.session_id = "test-session"
    d.cycle_id = "test-cycle"
    d.db_engine = engine
    return d


@pytest.mark.asyncio
async def test_args_serialized_to_json_dict(engine, deps):
    deps.db_engine = engine
    args = await _run_recorder(engine, deps, {"side": "long", "pct": 30})
    assert args is not None
    parsed = json.loads(args)
    assert parsed == {"side": "long", "pct": 30}


@pytest.mark.asyncio
async def test_args_strips_reasoning_key(engine, deps):
    deps.db_engine = engine
    args = await _run_recorder(engine, deps, {"side": "long", "reasoning": "long text..."})
    assert args is not None
    parsed = json.loads(args)
    assert "reasoning" not in parsed
    assert parsed == {"side": "long"}


@pytest.mark.asyncio
async def test_args_truncated_at_4000(engine, deps):
    deps.db_engine = engine
    big = {"data": "x" * 5000}
    args = await _run_recorder(engine, deps, big)
    assert args is not None
    assert len(args) <= 4000


@pytest.mark.asyncio
async def test_args_none_when_empty(engine, deps):
    deps.db_engine = engine
    args = await _run_recorder(engine, deps, {})
    assert args is None
```

- [ ] **Step 2: Run tests — expect failures**

Run: `uv run pytest tests/test_tool_call_recorder_args.py -v 2>&1 | tail -15`
Expected: All 4 tests fail (likely all `args` returns None or column doesn't exist).

- [ ] **Step 3: Implement args write in tool_call_recorder.py**

Edit `src/services/tool_call_recorder.py`. **Replace the entire inner `try:` block inside the `finally:` clause (lines ~79-108)**, NOT just the `session.add` call. The block starts at `try:` (after `if not skip_record:`) and ends at the `except Exception as rec_err:` handler closing.

After the change, the inner try block looks like:

```python
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
                # 用 pydantic-ai 内置 helper 处理 str|dict|None 三态 + INVALID_JSON_KEY 兜底
                args_dict = call.args_as_dict()
                args_dict.pop("reasoning", None)   # strip 与 trade_actions.reasoning 重复存储
                args_serialized = json.dumps(args_dict, ensure_ascii=False) if args_dict else None
                if args_serialized and len(args_serialized) > 4000:
                    args_serialized = args_serialized[:4000]    # char-level 截断，与 reasoning 一致

                insert_start = time.monotonic()
                async with get_session(ctx.deps.db_engine) as session:
                    session.add(ToolCall(
                        session_id=ctx.deps.session_id,
                        cycle_id=ctx.deps.cycle_id,
                        tool_name=call.tool_name,
                        status=status,
                        duration_ms=duration_ms,
                        error_type=error_type,
                        args=args_serialized,            # ← 新增 (Iter 3 §G2)
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

Add `import json` at top of file (next to `import time`):

```python
import json
import logging
import time
```

- [ ] **Step 4: Run tests — expect pass**

Run: `uv run pytest tests/test_tool_call_recorder_args.py -v 2>&1 | tail -15`
Expected: All 4 tests pass.

- [ ] **Step 5: Run full suite — confirm no regression in existing recorder tests**

Run: `uv run pytest tests/test_tool_call_recorder.py tests/test_tool_call_instrumentation.py -v 2>&1 | tail -20`
Expected: All existing recorder/instrumentation tests still pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_tool_call_recorder_args.py src/services/tool_call_recorder.py
git commit -m "$(cat <<'EOF'
feat(iter-t0-2): tool_call_recorder writes args field with strip+truncate

- Use pydantic-ai call.args_as_dict() (handles str|dict|None three-state +
  INVALID_JSON_KEY fallback). Critical: do NOT use isinstance(call.args,
  dict) — provider/streaming responses may give raw JSON string.
- Strip 'reasoning' key (avoid trade_actions.reasoning duplicate storage)
- 4000 char cap (consistent with reasoning truncation; partial JSON
  tolerated by analysis side)
- Same args_as_dict() pattern as cli/app.py:215 display path

4 tests cover: serialize JSON / strip reasoning / truncate / None when empty.

Spec §4.4 / §5.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: tools_execution _record_action cycle_id (TDD)

**Files:**
- Create: `tests/test_record_action_cycle_id.py`
- Modify: `src/agent/tools_execution.py` line 16-39 (_record_action body)

- [ ] **Step 1: Write 2 failing tests**

Create `tests/test_record_action_cycle_id.py`:

```python
"""Iter 3 _record_action cycle_id write tests.

Spec §5.3.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from src.agent.tools_execution import _record_action
from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, TradeAction


@pytest.fixture
async def engine(tmp_path: Path):
    eng = await init_db(f"sqlite+aiosqlite:///{tmp_path}/record.db")
    # Need a session row (FK requirement)
    async with get_session(eng) as session:
        session.add(SessionModel(
            id="test-session", name="test", symbol="BTC/USDT:USDT",
            initial_balance=100.0, status="active",
            exchange_type="simulated", timeframe="15m",
            scheduler_interval_min=15, approval_enabled=True,
            token_budget=500000,
        ))
        await session.commit()
    return eng


def _make_deps(engine, cycle_id: str | None) -> MagicMock:
    deps = MagicMock()
    deps.session_id = "test-session"
    deps.symbol = "BTC/USDT:USDT"
    deps.db_engine = engine
    deps.cycle_id = cycle_id
    return deps


@pytest.mark.asyncio
async def test_record_action_writes_cycle_id(engine):
    deps = _make_deps(engine, "abc-123")
    await _record_action(deps, action="open_position", side="long", reasoning="r1")

    async with get_session(engine) as session:
        result = await session.execute(select(TradeAction.cycle_id).order_by(TradeAction.id.desc()).limit(1))
        cycle_id = result.scalar()
    assert cycle_id == "abc-123"


@pytest.mark.asyncio
async def test_record_action_writes_null_when_no_cycle_id(engine):
    """Tolerance path: deps.cycle_id is None → cycle_id NULL (schema nullable, legal)."""
    deps = _make_deps(engine, None)
    await _record_action(deps, action="open_position", side="long", reasoning="r1")

    async with get_session(engine) as session:
        result = await session.execute(select(TradeAction.cycle_id).order_by(TradeAction.id.desc()).limit(1))
        cycle_id = result.scalar()
    assert cycle_id is None
```

- [ ] **Step 2: Run tests — expect failures**

Run: `uv run pytest tests/test_record_action_cycle_id.py -v 2>&1 | tail -15`
Expected: Both tests fail. cycle_id column exists (Task 3) but `_record_action` doesn't write to it yet.

- [ ] **Step 3: Modify _record_action to write cycle_id from deps.cycle_id**

Edit `src/agent/tools_execution.py` line 16-39. Replace `_record_action`:

```python
async def _record_action(deps: TradingDeps, action: str, order_id: str | None = None,
                          side: str | None = None, price: float | None = None,
                          pnl: float | None = None, reasoning: str | None = None) -> None:
    """写入一条 TradeAction 记录。写入失败不影响 tool 返回（容错）。"""
    if deps.db_engine is None:
        return
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    try:
        async with get_session(deps.db_engine) as session:
            session.add(TradeAction(
                session_id=deps.session_id,
                cycle_id=deps.cycle_id,        # ← 新增（从 deps 取，11 个 callers 0 改动）
                action=action,
                order_id=order_id,
                symbol=deps.symbol,
                side=side,
                price=price,
                pnl=pnl,
                reasoning=reasoning,
            ))
            await session.commit()
    except Exception:
        logger.warning("Failed to record TradeAction", exc_info=True)
```

> **Note**: 11 callers (`tools_execution.py:85/121/147/177/192/218/243/270/289/337/366`) keep their existing arguments unchanged — `cycle_id` is sourced from `deps.cycle_id` inside the function body. This is the explicit design choice per spec §4.5.

- [ ] **Step 4: Run tests — expect pass**

Run: `uv run pytest tests/test_record_action_cycle_id.py -v 2>&1 | tail -10`
Expected: Both tests pass.

- [ ] **Step 5: Run full test suite to confirm no regression**

Run: `uv run pytest -x -q 2>&1 | tail -10`
Expected: All 898+ tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_record_action_cycle_id.py src/agent/tools_execution.py
git commit -m "$(cat <<'EOF'
feat(iter-t0-2): _record_action writes cycle_id from deps.cycle_id

- 11 _record_action callers (tools_execution.py:85/121/147/177/192/218/
  243/270/289/337/366) unchanged — cycle_id sourced from deps.cycle_id
  inside function body
- Tolerance: deps.cycle_id=None writes NULL (trade_actions.cycle_id is
  nullable per spec §4.5; historical data constraint)
- Test coverage: cycle_id write + NULL tolerance path

Spec §4.5 / §5.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Pre-merge Validation (Sandbox + Full Regression)

**Files:** No code changes; validation tasks against W1 DB sandbox copy.

- [ ] **Step 1: Backup W1 DB and prepare sandbox copy**

```bash
mkdir -p data/sandbox
cp data/tradebot.db data/sandbox/iter3-test.db
cp data/tradebot.db data/tradebot.db.iter3-backup       # extra safety
ls -la data/sandbox/iter3-test.db data/tradebot.db.iter3-backup
```

- [ ] **Step 2: Apply migration to sandbox DB**

```bash
TRADEBOT_DB_URL="sqlite+aiosqlite:///data/sandbox/iter3-test.db" uv run alembic upgrade head 2>&1 | tail -10
```

Expected: alembic logs show migration applied; no exceptions.

- [ ] **Step 3: Verify W1 backfill (5 SQL checks per spec §6.3)**

```bash
sqlite3 data/sandbox/iter3-test.db <<'EOF'
.echo on

-- 1. tool_calls.args column exists (NULL for historical, JSON dict for new writes; here: NULL)
SELECT COUNT(*) FROM pragma_table_info('tool_calls') WHERE name = 'args';
-- Expected: 1

-- 2. trade_actions.cycle_id column exists
SELECT COUNT(*) FROM pragma_table_info('trade_actions') WHERE name = 'cycle_id';
-- Expected: 1

-- 3. status backfill: 109 rows 'ok'
SELECT status, COUNT(*) FROM decision_logs GROUP BY status;
-- Expected: 109 rows 'ok' (W1 had 0 usage_limit_exceeded rows)

-- 4. decision backfill: 109 rows 'legacy'
SELECT decision, COUNT(*) FROM decision_logs GROUP BY decision;
-- Expected: 109 rows 'legacy'

-- 5. New composite index used
ANALYZE decision_logs;
EXPLAIN QUERY PLAN
SELECT * FROM decision_logs WHERE session_id='nonexistent' AND cycle_id='nonexistent';
-- Expected: USING INDEX ix_decision_logs_session_id_cycle_id (or session_id prefix index)
EOF
```

If row counts differ from 109: cross-check against `git log` between when spec was written and now (additional cycles may have run). Per spec §1.1 row-count footnote, 109 is approximate; the assertion is **all rows uniformly 'ok' / 'legacy'**, not the exact count.

- [ ] **Step 4: Run alembic check on a fresh sandbox (dual-rail invariant guard)**

> ⚠️ **Critical**: Do NOT directly `alembic upgrade head` on an empty DB — first migration is ALTER (drops `ix_sim_orders_session_status` etc), would hit `no such index`. Correct sandbox is `init_db` path 3 (`create_all + stamp head`) → then `alembic check` compares ORM metadata vs DB head schema (= dual-rail invariant: `create_all` product == migration head product).

```bash
TMP_DIR=$(mktemp -d)
TMP_DB="$TMP_DIR/ci_alembic_check.db"

# Init via path 3 (create_all + stamp head); avoids ALTER-on-empty bug
TRADEBOT_DB_URL="sqlite+aiosqlite:///$TMP_DB" uv run python -c "
import asyncio
from src.storage.database import init_db
asyncio.run(init_db('sqlite+aiosqlite:///$TMP_DB'))
"

# Audit ORM metadata vs DB head schema
TRADEBOT_DB_URL="sqlite+aiosqlite:///$TMP_DB" uv run alembic check 2>&1 | tail -10

rm -rf "$TMP_DIR"
```

Expected: "No new upgrade operations detected." (or similar). Diff non-empty would indicate metadata vs DB schema drift (= dual-rail divergence between create_all and migration head products).

If false alarms about anonymous constraints (per spec §5.4 contingency): add `include_object` filter to env.py per spec.

> Note: This is **separate** from the W1 sandbox in Step 1-3 (which was for backfill verification). Step 4 uses fresh empty sandbox to audit dual-rail invariant.

- [ ] **Step 5: Run full pytest suite**

```bash
uv run pytest -q 2>&1 | tail -10
```

Expected: All 898 + ~10 new tests pass (4 migration + 4 args + 2 cycle_id).

- [ ] **Step 6: Run 1-cycle smoke (optional, only if you're confident)**

> ⚠️ **Skip this step if you don't want to spend 5-15 minutes on a full LLM cycle.** Sandbox-only run; do NOT run against W1 DB.

```bash
TRADEBOT_DB_URL="sqlite+aiosqlite:///data/sandbox/iter3-test.db" uv run python main.py --model deepseek-v4-pro
# Stop after first cycle completes (Ctrl+C)
```

After 1 cycle, verify:

```bash
sqlite3 data/sandbox/iter3-test.db <<'EOF'
SELECT args FROM tool_calls WHERE args IS NOT NULL LIMIT 3;
-- Expected: JSON dict strings without 'reasoning' key

SELECT cycle_id, action FROM trade_actions WHERE cycle_id IS NOT NULL LIMIT 3;
-- Expected: cycle_id matches a decision_logs.cycle_id from same cycle
EOF
```

- [ ] **Step 7: Cleanup sandbox + final commit (if any tweaks needed)**

```bash
rm -rf data/sandbox
# data/tradebot.db.iter3-backup kept for paranoia rollback (delete after PR merge)
git status     # expect clean if no tweaks were needed in earlier tasks
```

If any spec deviations were discovered during validation, commit them now with a descriptive message.

---

## Self-Review Checklist (run before PR)

Before opening PR / handing off, manually verify:

- [ ] `pyproject.toml` has `alembic>=1.13.0` in `[project.dependencies]` (NOT `[project.optional-dependencies].dev`)
- [ ] `models.py` has `NAMING_CONVENTION` + `Base.metadata = MetaData(naming_convention=NAMING_CONVENTION)`
- [ ] `models.py` 5 schema changes match spec §4.1 (a)-(e) + 3 index renames
- [ ] `alembic/env.py` has `target_metadata = Base.metadata` at module top (autogenerate / alembic check requirement)
- [ ] `alembic/env.py` `_resolved_sync_url` reads `TRADEBOT_DB_URL` env var first (CI override path)
- [ ] Migration file imports `from src.storage.models import NAMING_CONVENTION` and uses it in `batch_alter_table(naming_convention=...)`
- [ ] Migration `upgrade()` Step 5a (catch-net UPDATE WHERE decision='usage_limit_exceeded') runs BEFORE Step 5b (UPDATE all to 'legacy')
- [ ] Migration `upgrade()` does NOT contain `batch_op.alter_column("status", server_default=None)` (server_default retained, batch reconcile bug avoided)
- [ ] `database.py` uses `engine.connect()` not `engine.begin()` for the alembic call block
- [ ] `database.py` has 6 helpers: `_has_alembic_version_table` / `_has_business_tables` / `_alembic_config` / `_alembic_upgrade_head` / `_alembic_stamp_head` / `_alembic_stamp_base`
- [ ] `tool_call_recorder.py` uses `call.args_as_dict()` (NOT `isinstance(call.args, dict)`)
- [ ] `tools_execution.py` `_record_action` reads `cycle_id` from `deps.cycle_id` (11 callers unchanged)
- [ ] All tests pass (full pytest)
- [ ] `alembic check` clean on sandbox

---

## Iter 4 Hand-off

After Iter 3 ships, **Iter 4 (T0-1 PR-B) starts immediately** with backfill SQL as first step. Per spec §8.1:

```sql
-- Iter 4 first step: backfill window-period mismatch rows
UPDATE decision_logs
   SET status = 'usage_limit_exceeded', decision = 'legacy'
 WHERE decision = 'usage_limit_exceeded'
   AND status = 'ok'
   AND datetime(created_at) > datetime('<iter3_merge_iso8601>');
```

`<iter3_merge_iso8601>` = ISO8601 with offset of Iter 3 PR merge time (look up `git log` for the merge commit, format as e.g. `2026-04-29T12:34:56+00:00`). Use `datetime()` function for SQLite comparison reliability.

---

**End of plan. Total: 10 tasks, ~50 steps, 6-8 hours estimated implementation time including TDD cycles.**
