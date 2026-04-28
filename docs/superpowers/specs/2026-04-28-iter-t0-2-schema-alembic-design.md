# Iter 3 — Alembic 引入 + Schema 演进 (T0-2 PR-S)

**Date**: 2026-04-28
**Branch**: `feature/iter-t0-2-schema-alembic`
**Source todo**: `.working/pre-next-observation-todos.md` §T0-2 PR-S
**Brainstorm 决议**: §B1 (decision/status 双字段) / §B2 (market_summary deprecated) / §T0-2 (a)~(e)
**预估工作量**: 1.5 ~ 2 天（Alembic 首次引入 + 多字段 + 历史数据 backfill + 写入路径 + 测试）

---

## 1. 背景与动机

### 1.1 W1 观察期暴露的数据采集底座 gap

W1 观察期（13.6h，**105 cycles W1 末时点**）8 笔深挖 N20-N25 暴露：决策归因仅靠 `tool_name + duration` 不足以解释决策路径。具体诉求：

> **行数注脚**：spec 全文用 **109 行** 表述（merge 时实测 `data/tradebot.db.decision_logs` = 109）。差额 4 行是 W1 截止后到 Iter 3 merge 之间的辅助调试 cycle（W1 baseline smoke / pre-observation iteration 验证产物）。验证 SQL（§6.3）按 109 检查；若实施时 cwd DB 行数与 109 不符，先 `SELECT COUNT(*) FROM decision_logs` 确认 baseline 再判断是否多出新 cycle，**不要硬编码 109 为 hard fail 阈值**。



| Gap | 现状 | 影响 |
|---|---|---|
| `tool_calls` 无 `args` 字段 | 仅记 tool 名 + 状态 + 耗时 | 看不见 set_price_alert 阈值 / cancel_order ID / timeframe 选择 / leverage 数值 |
| `trade_actions` 无 `cycle_id` 字段 | append-only 无 cycle 关联 | decision↔行为无法 cycle 级 join，每次分析都要从 51MB system.log grep 复现 |
| `decision_logs` 字段语义混乱 | `decision` 100% 硬编码 `'completed'`；pre-observation Iter 5（pydantic-ai compliance, PR #26）又加 `'usage_limit_exceeded'` 错误状态 | "决策类型" vs "执行状态"两个维度纠缠在一起，无法 2D 交叉分析 |
| `decision_logs.market_summary` 100% NULL | 字段从生命周期开始就是 "intended but unimplemented" | 占位字段，应 deprecated 而非继续保留意图 |
| 项目无 Alembic | 仅 `Base.metadata.create_all()` | 任何 schema 演化对已有数据 DB 无升级路径，下次更难做 |

### 1.2 已完成的 brainstorm 决议（来自 `.working/pre-next-observation-todos.md`）

- **§B1**: DecisionLog 双字段方案（保留 `decision` 表决策类型 + 新增 `status` 表执行状态）；109 行历史 `decision='completed'` 不准 → backfill 为 `'legacy'`。**已实测 W1 DB 0 行 `decision='usage_limit_exceeded'`**（usage_limit_exceeded 路径在 W1 期间未触发），无脑 `UPDATE ... SET decision='legacy'` 不丢任何执行状态信息
- **§B2**: `market_summary` deprecated；不实施写入；schema 字段保留 nullable；C 档触发时一并 drop（不在本 Iter）
- **§T0-2 (a)**: 单 migration 包含**全部** schema 改动 + 索引 + 历史 backfill（不拆 PR）

### 1.3 与下一 Iter 的关系

- **Iter 4 (T0-1 PR-B)** 依赖本 Iter merge 后开工：DecisionLog 写入路径补全（reasoning 4000 cap / decision 派生 / status 写入 / market_summary 不传）需要本 Iter 的 `status` 字段先就位
- **C 档触发时 drop market_summary**：复用本 Iter 的 Alembic 基础设施（独立小 PR，不在本 Iter scope）

---

## 2. 设计目标

### 2.1 In-scope

- **G1**: 首次引入 Alembic（async env.py + naming convention + autogenerate ready）
- **G2**: `tool_calls` 加 `args: Text | None`（4000 char cap、JSON dict、strip `reasoning` key）
- **G3**: `trade_actions` 加 `cycle_id: String(50) | None`
- **G4**: `decision_logs` 加 `status: String(30)` default `'ok'`（per brainstorm 校准）。**校准理由**: `pre-next-observation-todos.md` §B1 字面 String(20)，但实测 `len("usage_limit_exceeded") == 20`——**当前唯一已知错误码正好顶满 String(20) 上限（0 buffer 边界，非溢出）**。校准至 String(30) 给未来类似长度错误码留余地：`error` (5) / `request_limit_exceeded` (22) / `tool_call_limit_exceeded` (24) 等
- **G5**: `decision_logs.decision: String(50) → String(20)`
- **G6**: `decision_logs.market_summary` 加 inline `#` DEPRECATED 注释（不 drop column）
- **G7**: 新增 `ix_decision_logs_session_id_cycle_id` 索引（spec §T3-1 合并到本 Iter）
- **G8**: 历史 109 行 backfill：`status='ok'`（server_default 自动）+ `decision='legacy'`
- **G9**: 写入路径：`tool_calls.args` 在 `ToolCallRecorder` 写入 + `trade_actions.cycle_id` 在 `_record_action` 写入
- **G10**: 配 SQLAlchemy naming convention，`decision_logs` 重建表时附带约束名对齐（A.2 决议）

### 2.2 Out-of-scope（明牌不做）

- ❌ DecisionLog 写入路径补全（reasoning 4000 cap / decision 派生）— 留 Iter 4 (T0-1 PR-B)
- ❌ `market_summary` drop column — C 档触发时一并 drop
- ❌ `tool_calls.result_preview` 字段（spec §T0-2 (b) 未列）
- ❌ 历史 `decision` 通过 `trade_actions` 时间窗口反推（B1 选项 (I)）— 已选 (II) `legacy`
- ❌ 其他 6 表（sessions / sim_balances / sim_orders / sim_positions / trade_actions / tool_calls）的匿名 PK/FK/UQ 约束对齐 — 留"动到那张表时"渐进偿债（A.2 决议）
- ❌ 引入 pre-commit hook 跑 `alembic check` — CI 已守门，本地避免噪声

---

## 3. 架构

### 3.1 Alembic 与 `init_db()` 共存（**三态判定**）

**关键实测**: W1 production DB 当前**没有 alembic_version 表**（项目从未引入 Alembic）。如果用二态判定 "alembic_version 存在 → upgrade / 不存在 → create_all + stamp"，W1 DB 命中第二分支：`Base.metadata.create_all(checkfirst=True)` 见已存在表跳过（不 ALTER 添加新列）+ stamp head 错误标记到 head → Iter 3 ship 后写入路径立即撞 "no such column"。**这是 spec merge 即 break production 的硬漏洞**，必须用三态判定避开。

`src/storage/database.py` 改造 `init_db()`：

```
init_db(url) called
       ↓
检测 alembic_version 表是否存在？
       ├── 存在 → 已 in-Alembic 链 → alembic upgrade head（已有 W1 数据 + 已 stamp 过）
       └── 不存在 → 进一步检测业务表（如 sessions）是否存在？
                ├── 存在 → pre-Alembic legacy DB（W1 当前状态）→ alembic stamp base + upgrade head
                │              （让 legacy DB 真正经历 migration 重建表，是 W1 升级路径）
                └── 不存在 → 空库 / 测试 fixture → Base.metadata.create_all + alembic stamp head（快路径）
```

**关键实施细节**:
- 业务表检测用 `sessions` 作 sentinel（最早创建的核心表）；任何 W1 DB 都有此表
- pre-Alembic legacy 路径走 `alembic stamp base`（标记到 migration 链起点之前）+ `alembic upgrade head`，让 W1 DB 真正经历首个 migration（含 batch_alter 重建 decision_logs / 索引 rename / backfill）
- W1 DB 升级是 "首次实际 migration 触发"——必须**通过本路径**而非 stamp head 跳过

**SQLite-specific 声明**: 本 Iter 仅支持 SQLite。`sqlite_master` 系统表是 SQLite 私有 schema 接口；`batch_alter_table` 重建语义针对 SQLite ALTER COLUMN 限制设计。未来若引入其他 DB driver（PostgreSQL / MySQL），sentinel 与 migration 重建逻辑需重新设计（PG 用 `information_schema.tables`，MySQL 同款）。

**测试 fixture 路径不变**：实测测试 fixture **混用** `sqlite+aiosqlite:///:memory:`（**19 处**）+ `sqlite+aiosqlite:///{tmp_path}/<name>.db`（**13 处**），共 32 处 init_db 调用。两种 fixture 都走第三分支（无业务表 → create_all + stamp head 快路径）。

**`init_db` 三态改造理由**：
1. **fixture 与 migration 链解耦**（核心理由）：测试 fixture 不走 migration，避免 migration 错误回退污染 unit test 信号
2. 测试库速度：`create_all` 直建 head 比 alembic upgrade 快（~50-200ms × 32 fixtures = 1.6-6.4s 节省）
3. Production W1 DB 走 migration 链保数据安全（含 backfill）
4. CI `alembic check` 守门 `create_all` 产物 ≡ migration head 产物的一致性

### 3.2 文件布局

```
新增:
  alembic/
  ├── env.py                                                    # async template (Q3 决议)
  ├── script.py.mako                                            # alembic 默认，不改
  └── versions/
      └── <rev>_initial_iter3_schema_evolution.py               # 首个 migration
  alembic.ini                                                   # 关键字段见下方示意

  tests/test_alembic_migration.py                               # 新增 migration 测试
  tests/test_tool_call_recorder_args.py                         # 新增 args 写入测试（如复用现有 test 文件可省）
  tests/test_record_action_cycle_id.py                          # 新增 cycle_id 写入测试

改动:
  src/storage/models.py                                         # 加 NAMING_CONVENTION + 5 项 schema 改
  src/storage/database.py                                       # init_db 三态判定
  src/services/tool_call_recorder.py                            # args 写入
  src/agent/tools_execution.py                                  # _record_action 加 cycle_id 写入

依赖:
  pyproject.toml                                                # 加 alembic>=1.13.0 到 [project.dependencies]（main 依赖，非 dev；理由：init_db production 路径调 alembic.command.upgrade，运行时必装）
```

**`alembic.ini` 关键字段示意**（默认模板基础上的项目特化）:

```ini
[alembic]
script_location = alembic                # 相对仓库根
file_template = %%(rev)s_%%(slug)s       # rev 12-char hash + slug 简短英文，per Q4 决议
sqlalchemy.url =                         # 留空 — 由 env.py 注入（init_db 路径走 connection 注入；CLI 直调路径由 env.py 从 src.config.load_settings() 派生）
prepend_sys_path = %(here)s              # 锚定 alembic.ini 所在目录（repo root），避免 cwd 依赖

[loggers]
keys = root,sqlalchemy,alembic

# ... 其余 loggers/handlers/formatters 用 alembic init 默认模板，不改
```

**为什么 `sqlalchemy.url` 留空**: production 路径 `init_db` 通过 `cfg.attributes["connection"]` 注入；CLI 直调（开发者手动 `alembic upgrade`）由 env.py 从 `src.config.load_settings().database.url` 派生 + path normalization（详见 §4.3 `_resolved_sync_url`）。**单一真相源 = settings**，不在 alembic.ini 硬编码。

### 3.3 NAMING_CONVENTION 装配

放 `src/storage/models.py` 顶层（与现有 `_utcnow` / `_uuid` helper 同位置；不抽独立文件，YAGNI）：

```python
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

**对现有约束的影响**：
- ✅ 4 个 `index=True` 自动产物（如 `ix_decision_logs_session_id`）— 与 convention 推算名 100% 一致，0 改动
- ⚠️ 3 个手写 `__table_args__ Index(...)` — 与 convention 不同，本 Iter rename 对齐
- ⏸️ 7 匿名 PK / 6 匿名 FK / 4 匿名 UQ — A.2 决议保留现状，仅 `decision_logs` 因 String 改重建时附带对齐

---

## 4. 详细改动

### 4.1 `src/storage/models.py`

**(a) 顶层加 `NAMING_CONVENTION` + 改 `Base`**（见 §3.3）

**(b) `ToolCall` (line 151-170)**

```python
class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_session_id_tool_name_created_at", "session_id", "tool_name", "created_at"),  # rename
        Index("ix_tool_calls_cycle_id", "cycle_id"),                                                       # rename
    )
    # ... 现有字段不变 ...
    args: Mapped[str | None] = mapped_column(Text, nullable=True)                  # 新增
```

**(c) `TradeAction` (line 47-63)**

```python
class TradeAction(Base):
    __tablename__ = "trade_actions"
    # ... 现有字段不变 ...
    cycle_id: Mapped[str | None] = mapped_column(String(50), nullable=True)        # 新增
```

`session_id index=True` 自动产物 `ix_trade_actions_session_id` 已合规，不动。

**(d) `DecisionLog` (line 66-80)**

```python
class DecisionLog(Base):
    __tablename__ = "decision_logs"
    __table_args__ = (
        Index("ix_decision_logs_session_id_cycle_id", "session_id", "cycle_id"),   # 新增（spec §T3-1 合并）
    )
    # ... 大部分字段不变 ...
    market_summary: Mapped[str | None] = mapped_column(Text, nullable=True)        # DEPRECATED — see brainstorm §B2 (Python 源码注释，非 SQLAlchemy comment= 参数：SQLite 不支持 column COMMENT 子句，且 comment= 会引入 alembic check noise)
    decision: Mapped[str] = mapped_column(String(20))                              # String(50)→String(20) (spec §B1)
    status: Mapped[str] = mapped_column(String(30), default="ok", server_default="ok")  # 新增 (B1 双字段方案；String(30) per brainstorm 校准；server_default="ok" 与 DB schema 一致避免 alembic check noise，详见 §4.2 Step 4 "为什么保留 server_default")
    # ...
```

**(e) `SimOrder` (line 127-148)**

```python
class SimOrder(Base):
    __tablename__ = "sim_orders"
    __table_args__ = (
        Index("ix_sim_orders_session_id_status", "session_id", "status"),          # rename
    )
```

### 4.2 首次 Migration: `<rev>_initial_iter3_schema_evolution.py`

**Upgrade 顺序**:

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
        # 关键设计：server_default **保留**（不在 batch 内 alter 移除），原因详见下方 "为什么保留 server_default"
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

**为什么保留 `server_default="ok"`（不在 batch 内 alter 移除）**:

1. **避免 alembic batch 合并语义 bug**：alembic batch_alter 的 reconcile 行为是把所有 ops 合并到 final schema，单次 CREATE _new + INSERT SELECT + DROP + RENAME。若在 batch 内 add_column(server_default='ok') + alter_column(server_default=None)，最终 schema 的 status 列 = `NOT NULL 无 server_default` → INSERT _new SELECT FROM old 时旧表无 status 列、新列依赖 schema default 取值、default 已被 alter 移除 → **NOT NULL 撞 NULL 抛错**。spec 早期版本此处有 bug，本轮审查指出。

2. **schema 语义合理**：现有 24 处 `default=` 是 ORM-time 业务字段填值（每条 INSERT 由 Python 对象构造）；新加 `status` 是 schema-level 默认（DB 自填 'ok' 让 ADD COLUMN 兼容 NOT NULL backfill 路径）。**语义层级不同**，引入 1 处 `server_default` 是 schema 设计需要而非破坏惯例。

3. **alembic check 无 noise**：model 同步声明 `server_default="ok"`（见 §4.1 (d)），metadata 与 DB 状态一致 → autogenerate 不报 diff。

4. **bonus 收益**：未来若有 ad-hoc `op.execute("INSERT INTO decision_logs ...")` 不带 status 字段，DB 自动填 'ok' 不撞 NOT NULL（虽然实测当前 0 occurrence，零成本保险）。

> ⚠️ **Implementation 阶段必须实跑 `alembic upgrade head` against W1 副本验证**，确认 batch_alter 行为符合预期 + INSERT SELECT 不撞 NULL。`tests/test_alembic_migration.py::test_upgrade_from_w1_like_data` 必须覆盖此路径（见 §5.2，断言 migration 不抛异常 + 数据完整）。

**Downgrade 完整逆向**:

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

**downgrade 不可还原项明牌**:
- `decision='legacy'` 旧值不复 — backfill 是不可逆操作（109 行原 `'completed'` 信息已丢，但 B1 决议视为不可信，无业务价值）
- `decision_logs` 表 PK / FK 自增 id：SQLite batch_alter 重建表时通过 `INSERT INTO _new SELECT * FROM old` 显式带 id 列，**id 序列保留**（不重置）— 但若 user 在 upgrade 后插入了新行、再 downgrade 再 upgrade，新插入行的 id 仍单调递增（Alembic batch_alter 行为契约）

### 4.3 `src/storage/database.py` — `init_db()` 三态改造

**两个关键设计**：

**(1) connection 注入约定**：`alembic.command.upgrade(cfg, "head")` 默认行为是 `engine_from_config` 自建 Engine + Connection，**不会复用** init_db 外层的 connection。结果：同一 SQLite 文件被两个 connection 并发写，WAL 模式下 transaction 边界不清晰。Alembic cookbook "Sharing a Connection" 模式：调用方把 connection 注入 `cfg.attributes["connection"]`，env.py 优先读这个 attribute、缺失才自建。

**(2) 用 `engine.begin()` 不是 `engine.connect()`** （**Round 13 关键校准**）：Alembic `MigrationContext.begin_transaction()` 实测源码行为（`alembic/runtime/migration.py`）：

```python
def begin_transaction(self, _per_migration: bool = False):
    if self._in_external_transaction:
        return nullcontext()    # 检测到外层已 in transaction → 完全不开 transaction、不 commit、不 rollback
    ...
```

→ shared connection 模式下 alembic **完全依赖外层管理 transaction**：
- ✅ 外层 `engine.begin()`：BEGIN → 内层 alembic 检测 in_external_transaction → nullcontext → ops 在外层 transaction 内执行 → 外层退出 COMMIT
- ❌ 外层 `engine.connect()`（**spec round 1-12 错误设计**）：无 BEGIN → SELECT 触发 SQLAlchemy 2.0 auto-begin → alembic 检测 in_transaction → nullcontext 不 commit → `async with engine.connect()` 退出无显式 commit → **ROLLBACK 所有 schema 创建 + alembic_version stamp** → 全套 32 处 fixture 立刻撞 `OperationalError: no such table`

第二轮某审查者基于对 cookbook 的误读建议 `engine.connect()`，spec 接受后 11 轮审查未发现，第十三轮通过 alembic 源码 + 官方默认 env.py 模板核实纠正（默认模板用 `connectable.connect()` 是 alembic 自建 engine 场景，内层 `context.begin_transaction()` 真正开 transaction；本项目是 share connection 场景，外层必须 begin）。

```python
async def init_db(url: str) -> AsyncEngine:
    engine = create_async_engine(url, echo=False)
    # engine.begin() 开外层 transaction；alembic 内层 context.begin_transaction()
    # 检测 _in_external_transaction → nullcontext → 共享外层 transaction → 外层退出 COMMIT
    async with engine.begin() as conn:
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
    from pathlib import Path
    from alembic.config import Config
    # database.py 在 src/storage/，parents[2] = repo root
    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(repo_root / "alembic.ini")
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

**`alembic/env.py` 完整设计**:

```python
# alembic/env.py

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
    import os
    from src.config import load_settings
    # 优先 env var（CI override / ad-hoc 测试路径；实测 load_settings 不处理 database.url env_overrides 故必走此路径）
    env_url = os.getenv("TRADEBOT_DB_URL")
    if env_url:
        async_url = env_url
    else:
        # env_overrides={} 跳过 dotenv 读取（alembic 上下文不需要 OKX_* env vars）
        # path 锚定到 repo_root（避免 alembic CLI 从非 repo_root 启动时 cwd-relative path 失败）
        repo_root = Path(__file__).resolve().parents[1]
        async_url = load_settings(
            path=repo_root / "config" / "settings.yaml",
            env_overrides={},
        ).database.url
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
        with sync_engine.begin() as conn:    # 外层开 transaction，alembic 内层 nullcontext 共享 (Round 13)
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
    # alembic 检测外层 transaction (init_db 的 engine.begin() 或 CLI 自建 engine.begin()) → nullcontext → 共享
    with context.begin_transaction():
        context.run_migrations()


# === 入口 ===
if context.is_offline_mode():
    # offline 模式（生成 SQL 脚本不实际运行）— 简化版，本项目 production 不用 offline
    raise NotImplementedError("Offline mode not supported; use online migrations")
else:
    run_migrations_online()
```

**关键设计要点**:
- 顶层 `target_metadata = Base.metadata` — autogenerate / `alembic check` 必需
- `_resolved_sync_url` helper 与 `app.py:434-438` 同款 path normalization 逻辑
- `load_settings(env_overrides={})` 跳过 dotenv 读取（alembic 不需要 OKX_* env vars）
- `_alembic_config` 用 `Path(__file__).resolve().parents[2]` 锚定 alembic.ini，避免 cwd 依赖

### 4.4 `src/services/tool_call_recorder.py` — args 写入

**改动位置**：line 91-98 `session.add(ToolCall(...))` 块。

**关键：用 pydantic-ai 内置 `call.args_as_dict()`，不要 `isinstance(call.args, dict)`**。理由：
- pydantic-ai `ToolCallPart.args` 实际类型是 `str | dict[str, Any] | None`（messages.py:1609）— 部分 provider / 流式响应下是 raw JSON 字符串
- `isinstance(call.args, dict)` 在 str 形态下走 false 分支 → 整体写 NULL，**args 数据丢失**
- `args_as_dict()`（messages.py:1644）已处理 str/dict/None 三态 + INVALID_JSON_KEY 兜底
- 与 `src/cli/app.py:215` 现有抽取风格一致（display 路径已用同款 helper）

新增逻辑（在 `session.add` 之前）：

```python
import json
# 用 pydantic-ai 内置 helper 处理 str|dict|None 三态 + INVALID_JSON_KEY 兜底
args_dict = call.args_as_dict()
args_dict.pop("reasoning", None)   # strip 与 trade_actions.reasoning 重复存储
args_serialized = json.dumps(args_dict, ensure_ascii=False) if args_dict else None
if args_serialized and len(args_serialized) > 4000:
    args_serialized = args_serialized[:4000]    # char-level 截断，与 reasoning 一致
```

`session.add` 扩展：

```python
session.add(ToolCall(
    session_id=ctx.deps.session_id,
    cycle_id=ctx.deps.cycle_id,
    tool_name=call.tool_name,
    status=status,
    duration_ms=duration_ms,
    error_type=error_type,
    args=args_serialized,            # 新增
))
```

**截断后允许 invalid JSON**：99% 工具 args < 4000 chars（实测平均几百），cap 仅做 outlier 防御；分析侧容忍 partial JSON（与 reasoning 切尾一致）。

**Implementation hint — strip reasoning key 是否彻底**：本项目工具签名统一用 `reasoning` 字段名（与 `_record_action(reasoning=...)` 同款）；implementation 阶段建议先 `grep -rnE "reasoning|reason_text|reason\s*[:=]" src/agent/tools_*.py` 确认无 `reasoning_text` / `reason` 等命名变体，避免漏 strip。当前 grep 应返回统一 `reasoning` 一种。

**Implementation 自检清单（每次工具签名扩展时回归）**:
- [ ] 新工具 `reasoning` 字段名是否与 `_record_action(reasoning=...)` 同款（不是 `reason` / `reason_text`）？
- [ ] 若引入新命名变体，需更新 recorder 内 `args_dict.pop("reasoning", None)` → 多 key 协同 strip
- [ ] 修改后回归 `tests/test_tool_call_recorder_args.py::test_args_strips_reasoning_key` 覆盖新 key

**与 `cli/app.py:215` 现有路径关系**: `src/cli/app.py:215` 已用 `part.args_as_dict()` 抽 args 给 display + system.log。本 Iter recorder 路径独立做 DB 持久化，二者从同一 message stream 各自抽取——**不复用是为了 capability 自包含**（`ToolCallRecorder` 本身无外部依赖，便于测试与 disable；display 与 recorder 是 producer/consumer 解耦的两个 pipeline）。

### 4.5 `src/agent/tools_execution.py` — `_record_action` 加 `cycle_id`

**改法 (ii)**：函数体内从 `deps.cycle_id` 取，11 个 callers 0 改动。

```python
async def _record_action(
    deps: TradingDeps,
    action: str,
    order_id: str | None = None,
    side: str | None = None,
    price: float | None = None,
    pnl: float | None = None,
    reasoning: str | None = None,
) -> None:
    if deps.db_engine is None:
        return
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    try:
        async with get_session(deps.db_engine) as session:
            session.add(TradeAction(
                session_id=deps.session_id,
                cycle_id=deps.cycle_id,        # 新增（从 deps 取）
                action=action,
                # ... 其他字段不变 ...
            ))
            await session.commit()
    except Exception:
        logger.warning("Failed to record TradeAction", exc_info=True)
```

**容错**: `deps.cycle_id is None` 时写 NULL（trade_actions.cycle_id schema 是 nullable，合法）。

**为什么 nullable（不对称契约）**:
- `trade_actions` 现存所有历史行的 cycle_id 都是 NULL（新字段，无可靠字段反推 backfill）
- 与 `ToolCallRecorder` 的 NOT NULL 强约束（运行时所有 tool 调用都在 cycle 上下文内，参考 tool_call_recorder.py:81-84）**不对称**
- 此不对称的**唯一真正理由**是历史数据约束：现存 trade_actions 行无 cycle_id（新字段，无可靠 backfill 字段，基于 created_at 时间窗口反推 decision_logs.cycle_id 不可靠）
- **运行时 cycle_id 实际必有值**（与 ToolCallRecorder 同款契约）：实测 11 处 `_record_action` 调用全部在 `tools_execution.py` agent 工具内，agent.run 上下文 → `deps.cycle_id` 必有值（与 `trader.py:45` 注释一致）。`_dispatch_fill_event` 路径（base.py:227）仅调 `_clear_stale_alerts_for_full_close` + `_invoke_fill_callback`，**不调 `_record_action`**——故"异步 fill 写入路径"假设场景不存在
- 未来收紧 NOT NULL 的前置条件：所有现存历史行批量回填后即可考虑 migration 收紧；运行时层面无 cycle_id=None 的真实路径阻塞

11 个 callers 在 `tools_execution.py:85/121/147/177/192/218/243/270/289/337/366` — **全部沿用现有参数，0 改动**。

---

## 5. 测试策略

### 5.1 现有 fixture 路径不变

测试 DB fixture 全部走 `init_db(url)`（如 `tests/test_storage.py:9`）。`init_db` 三态判定后测试自动走第三分支（无业务表 → `create_all + stamp head` 快路径），**0 个测试需改写**。

### 5.2 新增 Migration 测试: `tests/test_alembic_migration.py`

> ⚠️ **关键认知（设计前提）**: 本 Iter 首个 migration 是 **ALTER pre-existing schema**（drop_index / add_column / batch_alter），**不是 CREATE FROM BASE**。空库直接跑 `alembic upgrade head` 第一行 `op.drop_index("ix_sim_orders_session_status", ...)` 立即撞 `OperationalError: no such index`。空库的 production 路径是 **init_db path 3**（`create_all + stamp head`），**不经过 migration 链**。Migration 链的设计前提是已有完整 W1 schema（path 2 pre-Alembic legacy 或 path 1 已 in-Alembic 后续升级）。

| 测试 | 验证 |
|---|---|
| `test_init_db_path_3_for_empty_db` | 空库走 `init_db` path 3 (create_all + stamp head)，验证：(1) schema 完整（含 args / cycle_id / status / `ix_decision_logs_session_id_cycle_id`）；(2) `alembic_version` 表已 stamp 到 head revision。**关键**：不调 `alembic upgrade head`（首个 migration 是 ALTER 操作，空库会撞 `no such index`） |
| `test_upgrade_from_w1_like_data` | **Pre-Alembic schema 来源**：测试内 hand-write **完整 W1 业务表 schema**（sessions + sim_orders + ix_sim_orders_session_status + decision_logs + trade_actions + tool_calls + 旧手写索引），让 migration upgrade Step 1 的 drop_index/alter 操作有目标。Mock 数据混合两种：4 行 `decision='completed'` + 1 行 `decision='usage_limit_exceeded'`，**直接跑 `command.upgrade(cfg, "head")`**（非 init_db，因测试目标是 migration 行为），断言：(1) **migration 不抛异常**（覆盖 batch_alter 合并语义 + INSERT SELECT 不撞 NOT NULL 路径）；(2) 5 行全部 `decision='legacy'`；(3) 4 行 `status='ok'`（来自 server_default）+ **1 行 `status='usage_limit_exceeded'`（验证 §4.2 Step 5a catch-net 写对）**|
| `test_downgrade_then_upgrade` | 从 W1-like fixture 起步 → upgrade → downgrade -1 → upgrade head 可重入，验证幂等 |
| `test_upgrade_when_already_head` | 已在 head 状态（从 W1-like fixture upgrade 后）再次 `alembic upgrade head` 不报错、状态不变（**production 关键路径**：三态判定下 sentinel #1 分支每次 `init_db` 都跑 upgrade head，幂等性必须可验证）|

> Migration test 用 `alembic.command` Python API 调用（不走 subprocess），与现有 in-process 测试风格一致。`test_init_db_path_3_for_empty_db` 用 `init_db()` API（path 3）。

### 5.3 写入路径测试

**`tests/test_tool_call_recorder_args.py`**（或扩展现有 recorder 测试文件）

| 测试 | 验证 |
|---|---|
| `test_args_serialized_to_json_dict` | `call.args = {"side": "long", "pct": 30}` → DB args = `'{"side":"long","pct":30}'` |
| `test_args_strips_reasoning_key` | `call.args = {"side": "long", "reasoning": "long..."}` → DB args 不含 `"reasoning"` |
| `test_args_truncated_at_4000` | 超长 args dict → DB args ≤ 4000 chars |
| `test_args_none_when_empty_dict` | `call.args = {}` → DB args = NULL |
| `test_args_none_when_call_args_is_none` | `call.args = None` → DB args = NULL（与上 case 显式分离，覆盖 args_as_dict() str/dict/None 三态中的 None 入参分支）|

**`tests/test_record_action_cycle_id.py`**

| 测试 | 验证 |
|---|---|
| `test_record_action_writes_cycle_id` | `deps.cycle_id = "abc-123"` → TradeAction.cycle_id == "abc-123" |
| `test_record_action_writes_null_when_no_cycle_id` | `deps.cycle_id = None` → TradeAction.cycle_id IS NULL（容错）|

### 5.4 CI 一致性守门

CI 加一步（pytest 后）。**关键**: (1) 不能跑在本地 W1 DB 上（污染风险）；(2) **不能直接 `alembic upgrade head`**（首个 migration 是 ALTER 操作，空库会撞 `no such index`，与 §5.2 关键认知一致）。**正确策略**: 临时空 DB → 走 `init_db` (path 3 = create_all + stamp head) 建 schema → `alembic check` 比对 ORM metadata vs DB head schema 一致性：

```bash
# CI workflow（pytest 后）
TMP_DIR=$(mktemp -d)
TMP_DB="$TMP_DIR/ci_alembic_check.db"

# 走 init_db path 3 (create_all + stamp head)，避免 alembic upgrade 空库撞 no such index
TRADEBOT_DB_URL="sqlite+aiosqlite:///$TMP_DB" uv run python -c "
import asyncio
from src.storage.database import init_db
asyncio.run(init_db('sqlite+aiosqlite:///$TMP_DB'))
"

# 检测 ORM metadata 与 DB schema (= migration head 等价 schema) 一致性
TRADEBOT_DB_URL="sqlite+aiosqlite:///$TMP_DB" uv run alembic check   # diff 非空则 fail

rm -rf "$TMP_DIR"
```

**为什么这是正确审计**: `init_db` path 3 调 `Base.metadata.create_all` 把 ORM models 描述的 schema 物化到 DB；`alembic stamp head` 标记该状态等价于 migration head。`alembic check` 比较 ORM metadata vs DB schema → 如果一致，证明 "走 migration 链产物" 与 "走 create_all 产物" 等价（双轨 invariant 守护）。

**前置实现已定型**：实测 `src/config.py:114-153` `load_settings` 仅注入 `OKX_*` / `FRED_API_KEY` 等业务 env vars，**不处理 `database.url`**——所以 spec env.py 的 `_resolved_sync_url()` 必须在最外层加 env var fallback（不能走 load_settings env_overrides 路径）。

**单一权威实现见 §4.3**（含 env var fallback + async→sync + path normalization 完整逻辑）。`TRADEBOT_DB_URL` env override 由 §4.3 `_resolved_sync_url` 第一行处理。

**为什么不直接跑 W1 DB**：W1 DB 已 stamp head，alembic check 期望空 diff；但万一 spec 实施有未发现漂移，污染 W1 实测数据风险高。临时空 DB 跑 upgrade head + check 是干净 sandbox。

**False alarm 预案**: 若实测发现匿名约束触发 noise diff，env.py 加 `include_object` 过滤：

```python
def include_object(obj, name, type_, reflected, compare_to):
    if type_ in ("foreign_key_constraint", "unique_constraint", "primary_key_constraint"):
        if name is None or name.startswith("sqlite_autoindex_"):
            return False
    return True

context.configure(..., include_object=include_object)
```

第一次跑 alembic check 时实测后决定是否启用此过滤。

### 5.5 Regression

```bash
uv run pytest                  # 898 + ~11 新 tests
uv run alembic check           # 一致性守门
```

新增 tests 预估 ~11 个（migration 4 + recorder 5 + record_action 2），+0.5-1s 总耗时。

---

## 6. 错误处理 / 回滚 / 验证

### 6.1 失败模式

| 场景 | 处理 |
|---|---|
| `ImportError: No module named 'alembic'`（user 拉新代码后未 `uv sync`）| **运行 `uv sync` 同步依赖**（pyproject.toml 已声明 alembic>=1.13.0；错误信息明示）|
| Migration 中途失败（INSERT / SQL 错 / WAL lock / `decision_logs` batch_alter 重建任一步如 CREATE _new / INSERT SELECT old / RENAME）| Alembic 自动 rollback；DB 回到 pre-migration；alembic_version 不前进 |
| Backfill SQL 失败 | 同 transaction 回滚 |
| `init_db` 走 upgrade 失败 | raise，main.py fail-fast 启动失败（避免半坏 schema 进运行）|
| `args` JSON 序列化抛 TypeError | recorder 已有 try/except；rec_err logged，tool 返回不受影响（line 104-108 现有容错）|
| `deps.cycle_id is None` | trade_actions.cycle_id schema nullable，写 NULL 合法 |

### 6.2 回滚路径

**完全回滚（紧急）**:
```bash
uv run alembic downgrade -1
```
⚠️ `decision='legacy'` backfill 不还原（B1 决议：109 行不可信，无可恢复源）。

**半回滚（保留 schema 但停写新字段）**: 改 recorder 不写 `args`、`_record_action` 不写 `cycle_id`；DB 字段保留 NULL，读路径不受影响。

**整库重建（最后手段）**: 删 `data/tradebot.db*`（**通配 `*` 覆盖主文件 + `-shm` + `-wal` SHM/WAL 产物**，避免 SQLite WAL mode 残留导致重启异常）→ 重启 → `init_db` 走 create_all + stamp 路径（三态 sentinel #3）；W1 数据全丢。

### 6.3 完成判据（spec §T0-2 验证 SQL）

跑 1-2 cycle 后 SQL 验证：

```sql
-- 1. tool_calls.args 写入正确（JSON dict 形态，无 reasoning key）
SELECT args FROM tool_calls WHERE args IS NOT NULL LIMIT 5;

-- 2. trade_actions.cycle_id 与 decision_logs.cycle_id 匹配
SELECT cycle_id, action FROM trade_actions WHERE cycle_id IS NOT NULL LIMIT 5;

-- 3. status backfill 完整: 109 行 'ok'
SELECT status, COUNT(*) FROM decision_logs GROUP BY status;

-- 4. decision backfill 完整: 109 行 'legacy'
SELECT decision, COUNT(*) FROM decision_logs GROUP BY decision;

-- 5. 新索引被使用（注：表小 109 行 + ix_decision_logs_session_id 单列索引也匹配 prefix；
--    SQLite 优化器基于统计选索引，复合索引未必被挑中。建议先 ANALYZE 再 EXPLAIN 减少统计噪声）
ANALYZE decision_logs;
EXPLAIN QUERY PLAN
SELECT * FROM decision_logs WHERE session_id='...' AND cycle_id='...';
-- 期望: 使用某个 session_id 相关索引（ix_decision_logs_session_id_cycle_id 复合索引最优；
--       若优化器选 ix_decision_logs_session_id 单列前缀匹配也是合理结果）
-- 不期望: SCAN decision_logs（全表扫描，意味着新索引未生效）
```

### 6.4 Pre-merge 检查清单

**关键执行边界**：Step 2 / 3 必须用 **sandbox DB**（避免污染 W1 实测数据）；Step 4 在 W1 DB **只读跑** 验证 SQL；Step 5 在 sandbox DB 跑，避免 1 cycle 写入污染 W1。

| # | 检查 | DB 边界 | 命令 |
|---|---|---|---|
| 1 | 全套 tests 通过 | fixture 自管 | `uv run pytest` |
| 2 | Alembic 一致性 | **sandbox**（mktemp tmp DB + TRADEBOT_DB_URL 注入）| 见 §5.4 CI 守门 workflow |
| 3 | Migration 可重入 | **sandbox**（同上）| `TRADEBOT_DB_URL="sqlite+aiosqlite:///$TMP_DB" uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` |
| 4 | W1 数据 backfill 验证 | **W1 DB 只读**（仅 SELECT）| 本地 W1 DB 跑 §6.3 5 项验证 SQL |
| 5 | 1 cycle smoke | **sandbox**（mktemp tmp DB 复制 W1 数据 / 或 W1 DB 备份后跑）| `TRADEBOT_DB_URL=... uv run python main.py` 跑 1 cycle，验证 args / cycle_id 写入 |

---

## 7. 风险与未决项

### 7.1 已知风险

| 风险 | 概率 | 缓解 |
|---|---|---|
| `alembic check` 对 6 表匿名约束 false alarm | 中 | env.py `include_object` 过滤（§5.4 已备方案）|
| `decision_logs` batch_alter 重建 109 行数据 copy 失败 | 低 | Alembic transaction 自动 rollback；109 行体量小（< 5ms）|
| `args` 截断破坏 JSON 影响分析 | 低 | 99% args < 4000 chars；分析侧容忍 partial JSON |
| W1 DB 与 spec 假设 schema 不符 | 低 | 已实测确认（§3.1 sentinel 检测）|
| `pyproject.toml` 加 `alembic>=1.13.0` 引入额外依赖 | 极低 | Alembic 是 SQLAlchemy 生态标准依赖 |
| 已 in-Alembic 库 `alembic_version` 表被手动 DROP（清理脚本误删 / 半失败 migration / 手动 hack） | 极低 | **三态判定下命中 sentinel #2**（业务表存在）→ 走 stamp base + 重跑 migration 链。**本 Iter（首个 migration）安全**——本 Iter ship 后 W1 DB 是 pre-Iter3 schema，重跑首个 migration = 设计的正常路径；**未来 Iter N+1 之后**该场景会撞 "column already exists"（DB schema 已在 N+1 形态，重跑首个 migration 撞已存在的列）。届时此场景需手动 `alembic stamp <correct_rev>` 修复至已知 rev，或备份后重建 |
| **Iter 3 → Iter 4 之间数据不一致窗口** | 中（按计划紧密衔接则低） | 本 Iter merge 后 schema 已就位（`status` 默认 `'ok'`），但 Iter 4 (T0-1 PR-B) merge 前 `app.py:170` pathological 路径仍写 `decision='usage_limit_exceeded'`。任何 usage_limit 触发会产生 `decision='usage_limit_exceeded' AND status='ok'` 的语义冲突行（decision 字面是状态、status 字面是 ok）。**预期态明牌**：Iter 4 merge 后向前回填（或分析时按"语义冲突 = pre-Iter4 行"识别）；推荐 Iter 3→4 紧密衔接（< 1 day）减少不一致行数 |

### 7.2 未决项（待 implementation 阶段决策）

- **`alembic check` `include_object` 过滤是否启用**：第一次跑实测后决定（§5.4）
- **migration test fixture 共享**：暂按 per-test 起 SQLite，若实测慢再考虑 module-scope fixture
- **alembic.ini 是否纳入 git**：纳入（项目级配置；密钥不在此文件）

---

## 8. Iter 4 后置依赖

本 Iter merge 后 Iter 4 (T0-1 PR-B) 立即可开工。Iter 4 范围（不在本 Iter）:

### 8.1 Iter 4 **第一步**（承诺）：窗口期 mismatch 行 backfill

Iter 3 merge 后到 Iter 4 merge 前的窗口内，`app.py:170` pathological 路径仍写 `decision='usage_limit_exceeded' AND status='ok'`（语义冲突）。Iter 4 第一步 SQL 回填这些行：

```sql
-- 识别窗口期产生的 mismatch 行
-- 时区格式: ISO8601 带时区，如 '2026-04-29T12:34:56+00:00'
-- decision_logs.created_at 是 DateTime(timezone=True)，SQLite 存储为 ISO8601 字符串
SELECT id, created_at, decision, status FROM decision_logs
WHERE decision = 'usage_limit_exceeded'
  AND status = 'ok'
  AND datetime(created_at) > datetime('<iter3_merge_iso8601>');
-- 期望：行数 = 窗口期 usage_limit 触发次数（紧密衔接下应 0 / 个位数）

-- 回填（与 Iter 3 §4.2 Step 5a 同语义）
UPDATE decision_logs
   SET status = 'usage_limit_exceeded', decision = 'legacy'
 WHERE decision = 'usage_limit_exceeded'
   AND status = 'ok'
   AND datetime(created_at) > datetime('<iter3_merge_iso8601>');
```

`<iter3_merge_iso8601>` = Iter 3 PR merge 时刻 ISO8601 with offset（如 `2026-04-29T12:34:56+00:00`）。**用 `datetime()` 函数包装比较**避免 SQLite 字符串比较语义模糊。Iter 4 spec 内固化此 timestamp。

### 8.2 Iter 4 后续步骤（spec § 占位，详细设计在 Iter 4 spec）

- `src/cli/app.py:253` `decision="completed"` → `_derive_decision_from_actions(...)` + `status="ok"`（行号校准：spec 编写时 `pre-next-observation-todos.md` 写 :243，merge 时实测 :253，应以 Iter 4 实施时实测为准）
- `src/cli/app.py:170` `decision="usage_limit_exceeded"` → `decision="..."` + `status="usage_limit_exceeded"`（消除新写入冲突源头）
- `tests/test_usage_limits.py:103` `DecisionLog.decision == "usage_limit_exceeded"` → `DecisionLog.status == "..."`
- `reasoning[:500]` → `reasoning[:4000]`
- `market_summary` 不传（B2 决议）

---

**文档用途**: Iter 3 implementation 起手读本 spec + `.working/pre-next-observation-todos.md` §B1/B2/T0-2 即可入手。
