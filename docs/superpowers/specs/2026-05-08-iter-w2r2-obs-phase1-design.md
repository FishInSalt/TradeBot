# Phase 1 Observability — DB-Native Cycle Metrics + Lifecycle Views

**Date**: 2026-05-08
**Iter id**: w2r2-obs-phase1
**Status**: design (brainstorm-approved, awaiting user spec review)
**Predecessors**: R2-7 (PR #35, agent_cycle reframe) / R2-Next-A (PR #40, F1 5-field landed) / R2-Next-B (PR #41, decision forensic)
**Brainstorm sources**:
- `.working/observability-gaps-from-sim8.md` — 痛点定义 P1-P10 + 5 维度
- `.working/observability-solutions-from-sim8.md` — 逐项解决方案 + 4 类打包
- `.working/logfire-evaluation.md` — Logfire fork 已决（reject Phase 1）

---

## 1. Problem statement

W2 观察期 sim #8 (178 cycles / 19.2h / 14.36M tokens) 的 4 维度 raw-data 重挖暴露**根本瓶颈不是 agent 行为难懂，而是派生指标没沉淀**：每个新视角（cluster #1-#4）都要从 raw 重算 + 重新校准 query 边界。结果：

- 分析慢（每 cluster ~30 min query 时间）
- 易错（cluster #2 v0 错误诊断正是缺乏系统化框架）
- 不可复用（sim #9 要重做一遍，无增量复利）

`observability-gaps-from-sim8.md` 把 10 个痛点（P1-P10）归纳为 5 维度（A-F）。本 iter 实施 **Phase 1 = "派生层基础设施"**（gaps doc §3 第 1 优先级），覆盖 P1+P2+P5+P6 4 个痛点。

## 2. Background

### 2.1 现有数据基础设施

| 项 | 状态 |
|---|---|
| SQLite 版本 | 3.50.4（window function / json_extract 全支持）|
| alembic | 已用 batch_alter_table 模式（SQLite 多列 ALTER 限制）|
| `agent_cycles` schema | R2-7 reframe 后 12 字段（含 id/session_id/created_at 系统列；domain 字段 9：cycle_id/triggered_by/trigger_context/state_snapshot/decision/execution_status/reasoning/model_id/tokens_consumed）+ 1 复合 index `(session_id, cycle_id)` |
| `state_snapshot` JSON | 已含 position / balance / market.fetched_at / pending_orders / active_alerts（cycle_capture.py:103-204）|
| `decision` 字段 | R2-7 后 Text/nullable；含 R2-Next-A 5-field 结构（57.1% 直起 / 4.5% H2 / 38.4% table delimiter 前缀）|
| `tool_calls` | append-only 已含 cycle_id / args (JSON) / status / duration_ms |
| `trade_actions` | append-only 已含 cycle_id / order_id；**无 alert_id 列**（实施障碍，§5.1 解决）|

### 2.2 Token 拆分数据已计算但未落 DB

`src/cli/app.py:599-617` 已从 `result.usage().details` 提取并 `logger.info` 输出但**没落 DB**：

```python
reasoning_tokens = details.get("reasoning_tokens", 0)
cache_hit       = details.get("prompt_cache_hit_tokens", 0)
cache_miss      = details.get("prompt_cache_miss_tokens", 0)
hit_rate        = (cache_hit / input_total * 100) if input_total > 0 else 0.0
```

**P2 实施成本**比 brainstorm doc 原估小：仅"alembic add_column ×6 + INSERT 改写"，零 LLM 调用 schema 影响。

### 2.3 R2-Next-A 5-field SQL multi-LIKE 已 100% 实证

`docs/superpowers/specs/2026-05-07-iter-w2r2-next-a-f1-feedback-loop-design.md` §5.5 实证 4-variant LIKE union pattern 在 sim #8 178 cycles **171/171 命中**（vs narrow LIKE 58.5%）。本 iter `v_cycle_metrics` 5-field anchor 直接复用此 pattern。

### 2.4 已决前置（不再讨论）

- **Logfire reject**：见 `.working/logfire-evaluation.md`。核心理由 = 数据所有权 mismatch（默认上传 Pydantic 云端，90 天 retention，与本地真相源哲学不匹配）。Phase 1 走纯 DB 路径。
- **物化表 vs SQL view 选 view**：基于 R2-Next-A §5.5 100% LIKE 实证，view 不再有"复杂 anchor 抽取无法表达"这条 con；加上 P1+P2 已把 hot 字段（timing/tokens）落 agent_cycles 列，view 直接拿；sim 量级（< 5k cycle/sim）性能完全够。
- **S3 state_captured_at 根因方案推迟到 R2-Next-J**（cycle state machine refactor）：本期走 view JSON 派生（`json_extract(state_snapshot, '$.market.fetched_at')`）作 proxy。

## 3. Decisions matrix

brainstorm 阶段 6 个决策点收敛结果：

| # | 决策点 | 选择 | Rationale |
|---|---|---|---|
| 1 | 打包形态 | A: 单 spec + 单 PR | P5 字段依赖 P1+P2 列；与 R2-Next-A/B 单 iter 多议题模式一致；规模估 impl ~600 + test ~500 + SQL ~150 + alembic ~80 ≈ **1300 行**（在 R2-Next-A PR #40 / R2-Next-B PR #41 同量级）|
| 2 | P5 物理形态 | Y: SQL view 单一来源 | 反对 view 的 con（"anchor 抽取不行"）已被 R2-Next-A §5.5 否定；零 drift / 零物化开销 / 实施最简（**前提：plan task 0 实测 5-field 联合命中率 baseline；若联合 baseline < 90% 需重审 view 形态**——R2-Next-A §5.5 单字段 100% 不保证 4 字段联合 100%）|
| 3 | P1 timing 粒度 | β: 2 phase | `wall_time_ms` + `llm_call_ms`；agent.run 内部边界由 pydantic-ai 控制无法精切；`tool_total_ms` 由 `tool_calls.duration_ms` SUM 派生 |
| 4 | P6 view 范围 | δ: alert + order（2 view）| `v_trade_roundtrip` 与 Phase 2 P8 cross-sim diff 共享配对算法，推迟到 P8 共建避免重做 |
| 5 | S3 state_captured_at | α: view JSON 派生 | 用 `state_snapshot.market.fetched_at` ISO 字符串作 proxy；S3 native 列推迟到 R2-Next-J |
| 6 | alert_id 落 DB（实施障碍）| X: Phase 1 顺手加列 + 配套扩展 | trade_actions 加 alert_id 列 + PriceLevelAlertInfo 加 alert_id 字段 + 全调用链打通；~25 行；解锁 v_alert_lifecycle |

## 4. Goals and non-goals

### 4.1 Goals

- 落地 P1+P2 主表扩展：agent_cycles 加 8 列（2 timing + 6 token 拆分）
- 落地 P5 派生层：`v_cycle_metrics` view（38 列）
- 落地 P6 lifecycle 派生层：`v_alert_lifecycle` + `v_order_lifecycle` view
- 解锁 alert_id 数据通路：trade_actions 加 alert_id 列 + PriceLevelAlertInfo 加 alert_id 字段
- 历史数据兼容：sim #1-#8 数据接受 NULL，view 不破坏
- **Live mode (OKXExchange) 兼容**：`v_cycle_metrics` + `v_alert_lifecycle` 字段填值与 exchange 类型无关（agent_cycles / trade_actions 写入路径同源）；仅 `v_order_lifecycle` 因 `FROM sim_orders` 不覆盖 live（已在 §4.2 OOS 列出）
  - **W3 live 实启 sanity check**（不阻塞 plan）：OKXExchange:300 调 `self._check_price_levels` 已 verify（共享基类 client-side 路径）；W3 live 启动后 spot-check 1-2 个 alert lifecycle 数据完整性（确认 register/trigger/cancel 三态在 v_alert_lifecycle 都有行）。前置闭合 95% 可信度，runtime ticker poll 频率 / 服务端 alert 是否抢先触发等边界情况由 W3 实测兜底

### 4.2 Non-goals

| 议题 | 推迟到 | 理由 |
|---|---|---|
| P3 tool_call_responses 存档 | Phase 3 (forensic snapshot) | 触发型；存储/retention 设计独立 |
| P4 cycle_prompt_snapshots | Phase 3 | 同上 |
| P7 sim_market_snapshot | Phase 2 (cross-sim) | 与 P8 同 PR |
| P8 CLI diff 工具 | Phase 2 | - |
| P9 fact provenance | 不主动 | 等 P3+P4 上线后再评 |
| P10 cycle_retry_attempts | Phase 3 / R2-Next-J | 与 cycle state machine 强同源 |
| `v_trade_roundtrip` view | Phase 2 P8 共建 | 配对算法与 cross-sim diff 共享 |
| `replace_chain_root`（trailing-stop 群）| 独立 follow-up | 复杂度高，单独 brainstorm |
| **Live-trade (OKX) order lifecycle** | live 启动后单独评估 | `v_order_lifecycle` 仅 `FROM sim_orders`；OKXExchange 不写 sim_orders 表，live 模式下此 view 数据为空。W3+ 进 OKX live 时另起 view 或 schema 改动 |
| **`v_alert_lifecycle` 不投影 direction** | 独立 follow-up | direction 信息当前仅在 `trade_actions.reasoning` 文本（add 路径 prefix `{direction} {price} \|`）+ `trigger_context.direction` JSON（仅 triggered cycle）；reasoning text LIKE 抽脆弱；需要时独立 PR 加 `trade_actions.direction` 列（对称 alert_id 改动模式）|
| **S3 state_captured_at native 列** | **R2-Next-J** | 根因方案 = cycle state machine refactor |
| `attempt` 列（observation_period_checklist #1）| Phase 3 / R2-Next-J | retry 语义重塑联动 |
| C 档 `result_preview`（observation_period_checklist #3）| **被 Phase 3 P3 自动闭合** | 同源议题 |
| logfire instrument | Phase 1.5+ 触发再评 | 数据所有权 mismatch reject |

## 5. Design

### 5.1 Schema 扩展

#### 5.1.1 `agent_cycles` 新增 8 列（全 nullable）

```python
class AgentCycle(Base):
    # ... 现有 9 字段不动 ...

    # P1 — timing
    wall_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_call_ms:  Mapped[int | None] = mapped_column(Integer, nullable=True)

    # P2 — token 拆分（cli/app.py:605-611 已计算）
    input_tokens:       Mapped[int | None]   = mapped_column(Integer, nullable=True)
    output_tokens:      Mapped[int | None]   = mapped_column(Integer, nullable=True)
    cache_read_tokens:  Mapped[int | None]   = mapped_column(Integer, nullable=True)
    cache_write_tokens: Mapped[int | None]   = mapped_column(Integer, nullable=True)
    reasoning_tokens:   Mapped[int | None]   = mapped_column(Integer, nullable=True)
    cache_hit_rate:     Mapped[float | None] = mapped_column(Float,   nullable=True)
```

#### 5.1.2 `trade_actions` 新增 alert_id 列

```python
class TradeAction(Base):
    # ... 现有字段不动 ...
    alert_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
```

#### 5.1.3 alembic migration

```python
# alembic/versions/<rev>_phase1_observability.py
revision = "<new_rev>"
down_revision = "eeeee565cb36"  # R2-7 reframe

def upgrade() -> None:
    # P1+P2: agent_cycles 加 8 列
    with op.batch_alter_table("agent_cycles") as batch_op:
        batch_op.add_column(sa.Column("wall_time_ms",       sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("llm_call_ms",        sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("input_tokens",       sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("output_tokens",      sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("cache_read_tokens",  sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("cache_write_tokens", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("reasoning_tokens",   sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("cache_hit_rate",     sa.Float,   nullable=True))

    # X 配套: trade_actions 加 alert_id
    with op.batch_alter_table("trade_actions") as batch_op:
        batch_op.add_column(sa.Column("alert_id", sa.String(50), nullable=True))

    # P5+P6: 创建 3 个 view
    op.execute(_V_CYCLE_METRICS_SQL)
    op.execute(_V_ALERT_LIFECYCLE_SQL)
    op.execute(_V_ORDER_LIFECYCLE_SQL)

def downgrade() -> None:
    # Drop views first (column dependency)
    op.execute("DROP VIEW IF EXISTS v_order_lifecycle")
    op.execute("DROP VIEW IF EXISTS v_alert_lifecycle")
    op.execute("DROP VIEW IF EXISTS v_cycle_metrics")

    with op.batch_alter_table("trade_actions") as batch_op:
        batch_op.drop_column("alert_id")

    with op.batch_alter_table("agent_cycles") as batch_op:
        for col in ("cache_hit_rate", "reasoning_tokens", "cache_write_tokens",
                    "cache_read_tokens", "output_tokens", "input_tokens",
                    "llm_call_ms", "wall_time_ms"):
            batch_op.drop_column(col)
```

**w2_ops_backlog S1 部分吸收**：downgrade 函数顶部注释提示"如有依赖新列的 view 或 query 需先清理"，与 R2-7 escape-hatch 注释模式一致。

### 5.2 `v_cycle_metrics` view 字段集（38 列）

字段分组：

| 组 | 字段 | 来源 |
|---|---|---|
| Identity (6) | session_id, cycle_id, triggered_by, execution_status, created_at, model_id | `agent_cycles` 直接 |
| Timing (3) | wall_time_ms, llm_call_ms, **tool_total_ms** | P1 列 + 派生 SUM(tool_calls.duration_ms) |
| Tokens (8) | tokens_consumed, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens, cache_hit_rate (legacy DeepSeek-only), **cache_hit_rate_derived** (portable) | P2 列 + legacy + view 派生 |
| State snapshot (8) | position_size, position_side, position_leverage, position_unrealized_pnl, position_pnl_pct, balance_free_usdt, ticker_last, state_captured_at | `json_extract(state_snapshot, ...)` |
| State counts (3) | pending_orders_count, active_alerts_count, snapshot_errors_count | `json_array_length(...)` |
| State derived (2) | has_position, **decision_length** | `position IS NOT NULL` / `length(decision)` |
| 5-field anchors (5) | has_stance, has_active_commitments, has_this_cycle_delta, has_thesis_invalidation, has_watch_list | 4-variant LIKE pattern (R2-Next-A §5.5) |
| 5-field derived (1) | five_field_complete | sum(4 mandatory) ≥ 4 |
| Cycle status flags (2) | is_ok_cycle, is_forensic_cycle | `execution_status` 派生 |

**总列数**：6+3+8+8+3+2+5+1+2 = **38 列**。

#### 5.2.1 5-field anchor 检测模板

```sql
CASE
  WHEN ac.decision LIKE '%(N) Keyword%'      OR ac.decision LIKE '%(N) **Keyword%'
    OR ac.decision LIKE '%**(N) Keyword%'    OR ac.decision LIKE '%**(N)** Keyword%'
  THEN 1 ELSE 0
END AS has_<field>
```

| anchor | N | Keyword |
|---|---|---|
| has_stance | 1 | `Stance` |
| has_active_commitments | 2 | `Active` |
| has_this_cycle_delta | 3 | `This cycle` |
| has_thesis_invalidation | 4 | `Thesis` |
| has_watch_list | 5 | `Watch` |

`five_field_complete = (has_stance + has_active_commitments + has_this_cycle_delta + has_thesis_invalidation) >= 4`（4 mandatory；watch_list optional 不计）。

#### 5.2.2 view SQL 完整定义

```sql
CREATE VIEW v_cycle_metrics AS
WITH ac_with_anchors AS (
  -- 5-field anchor 检测：每个 anchor 4-variant LIKE union (R2-Next-A §5.5)
  -- 抽到 CTE 一次性派生，避免下游 five_field_complete 重复 LIKE
  SELECT
    ac.*,
    CASE WHEN ac.decision LIKE '%(1) Stance%' OR ac.decision LIKE '%(1) **Stance%'
           OR ac.decision LIKE '%**(1) Stance%' OR ac.decision LIKE '%**(1)** Stance%'
         THEN 1 ELSE 0 END AS has_stance,
    CASE WHEN ac.decision LIKE '%(2) Active%' OR ac.decision LIKE '%(2) **Active%'
           OR ac.decision LIKE '%**(2) Active%' OR ac.decision LIKE '%**(2)** Active%'
         THEN 1 ELSE 0 END AS has_active_commitments,
    CASE WHEN ac.decision LIKE '%(3) This cycle%' OR ac.decision LIKE '%(3) **This cycle%'
           OR ac.decision LIKE '%**(3) This cycle%' OR ac.decision LIKE '%**(3)** This cycle%'
         THEN 1 ELSE 0 END AS has_this_cycle_delta,
    CASE WHEN ac.decision LIKE '%(4) Thesis%' OR ac.decision LIKE '%(4) **Thesis%'
           OR ac.decision LIKE '%**(4) Thesis%' OR ac.decision LIKE '%**(4)** Thesis%'
         THEN 1 ELSE 0 END AS has_thesis_invalidation,
    CASE WHEN ac.decision LIKE '%(5) Watch%' OR ac.decision LIKE '%(5) **Watch%'
           OR ac.decision LIKE '%**(5) Watch%' OR ac.decision LIKE '%**(5)** Watch%'
         THEN 1 ELSE 0 END AS has_watch_list
  FROM agent_cycles ac
)
SELECT
  -- Identity
  ac.session_id, ac.cycle_id, ac.triggered_by, ac.execution_status,
  ac.created_at, ac.model_id,

  -- Timing
  ac.wall_time_ms, ac.llm_call_ms,
  -- tool_total_ms 派生：correlated subquery 累加同 cycle 内所有 tool_calls.duration_ms
  -- (sim 量级 178 cycle × ~10 tool_calls/cycle 性能足够)
  (SELECT SUM(tc.duration_ms) FROM tool_calls tc
   WHERE tc.session_id=ac.session_id AND tc.cycle_id=ac.cycle_id) AS tool_total_ms,

  -- Tokens
  ac.tokens_consumed, ac.input_tokens, ac.output_tokens,
  ac.cache_read_tokens, ac.cache_write_tokens,
  ac.reasoning_tokens,
  ac.cache_hit_rate,                          -- legacy: DeepSeek-only (vendor key 计算)；切非-DeepSeek provider 恒 0
  -- Portable cache hit rate (provider-agnostic via standard usage attributes)
  -- 推荐分析端用此列；cache_hit_rate 仅作 logger 兼容路径
  CASE WHEN ac.input_tokens IS NOT NULL AND ac.input_tokens > 0
       THEN ac.cache_read_tokens * 100.0 / ac.input_tokens
       ELSE NULL END AS cache_hit_rate_derived,

  -- State snapshot via JSON
  CAST(json_extract(ac.state_snapshot, '$.position.contracts')      AS REAL)    AS position_size,
       json_extract(ac.state_snapshot, '$.position.side')                       AS position_side,
  CAST(json_extract(ac.state_snapshot, '$.position.leverage')       AS INTEGER) AS position_leverage,
  CAST(json_extract(ac.state_snapshot, '$.position.unrealized_pnl') AS REAL)    AS position_unrealized_pnl,
  CAST(json_extract(ac.state_snapshot, '$.position.pnl_pct')        AS REAL)    AS position_pnl_pct,
  CAST(json_extract(ac.state_snapshot, '$.balance.free_usdt')       AS REAL)    AS balance_free_usdt,
  CAST(json_extract(ac.state_snapshot, '$.market.ticker_last')      AS REAL)    AS ticker_last,
       json_extract(ac.state_snapshot, '$.market.fetched_at')                   AS state_captured_at,

  -- State counts
  json_array_length(json_extract(ac.state_snapshot, '$.pending_orders')) AS pending_orders_count,
  json_array_length(json_extract(ac.state_snapshot, '$.active_alerts'))  AS active_alerts_count,
  json_array_length(json_extract(ac.state_snapshot, '$._errors'))        AS snapshot_errors_count,

  -- State derived
  CASE WHEN json_extract(ac.state_snapshot, '$.position') IS NOT NULL
       THEN 1 ELSE 0 END AS has_position,
  length(ac.decision) AS decision_length,    -- forensic / outlier 筛子（30% 用例）

  -- 5-field anchors (从 CTE 直接传)
  ac.has_stance, ac.has_active_commitments, ac.has_this_cycle_delta,
  ac.has_thesis_invalidation, ac.has_watch_list,

  -- 5-field complete (4 mandatory；DRY: 用 CTE 派生的 has_* 列加和而非重复 LIKE)
  CASE WHEN (ac.has_stance + ac.has_active_commitments
           + ac.has_this_cycle_delta + ac.has_thesis_invalidation) >= 4
       THEN 1 ELSE 0 END AS five_field_complete,

  -- Cycle status flags
  -- is_ok_cycle: empty-string decision 不算 ok（避免 R2-7 后 result.output="" 边界）
  CASE WHEN ac.execution_status='ok'
        AND ac.decision IS NOT NULL
        AND length(ac.decision) > 0
       THEN 1 ELSE 0 END AS is_ok_cycle,
  CASE WHEN ac.execution_status IN ('retry_exhausted','usage_limit_exceeded')
       THEN 1 ELSE 0 END AS is_forensic_cycle

FROM ac_with_anchors ac;
```

#### 5.2.3 分析端使用建议（命名困惑澄清）

`v_cycle_metrics` 含两个 cache hit rate 列，**分析者必读**：

| 列 | 来源 | 推荐使用 |
|---|---|---|
| `cache_hit_rate` (legacy) | `agent_cycles.cache_hit_rate` 列 — DeepSeek vendor key `prompt_cache_hit_tokens / (cache_hit + cache_miss) * 100`；切非-DeepSeek provider 恒 0 | ⚠️ **仅作 cli/app.py:613-616 logger.info 输出 + 现有 sim log 解析脚本兼容**，新分析脚本不用 |
| `cache_hit_rate_derived` (portable) | view 内派生 — `cache_read_tokens * 100.0 / NULLIF(input_tokens, 0)`；用 pydantic-ai 标准属性，所有 provider 通用 | ✅ **新分析脚本统一用此列** |

切到 OpenAI o-series / Anthropic 等时会出现 `cache_read_tokens > 0 AND cache_hit_rate = 0.0` 的"看似 bug 实非 bug"行——这是 legacy 列的 DeepSeek-only 局限，不是数据缺陷；用 `cache_hit_rate_derived` 即可正常分析。

#### 5.2.4 W2 SQL caveats 吸收（`iter4_sql_caveats` 三类）

| caveat | 本 view 处理 |
|---|---|
| **hold 双义**（decision='hold' 0-action vs set_next_wake-only）| view **不投影 decision text** 给"action 类别"列；要分析"是否真有交易动作"用 `LEFT JOIN trade_actions` 或本期 `v_alert_lifecycle` / `v_order_lifecycle`（hold 双义在 R2-7 后已淡化，decision 是 Text 不再是 enum）|
| **legacy vs derive_error**（Iter 3 alembic backfill vs runtime fallback）| R2-7 后 decision 是 Text，新 cycle 不再有这俩 token；历史 sim 仍存。view 不过滤；分析者按 `created_at >= '<R2-7 cutoff>'` 自行隔离 |
| **derive_error 作 DB 健康指标** | `snapshot_errors_count` 列直接暴露 state_snapshot capture 失败数（>0 即有问题）|

### 5.3 `v_alert_lifecycle` view

#### 5.3.1 lifecycle 事件源

| 事件 | 数据源 | alert_id 取法 |
|---|---|---|
| **registered** | `trade_actions WHERE action='add_price_level_alert'` | 新加 `alert_id` 列 |
| **triggered** (auto) | `agent_cycles WHERE triggered_by='alert'` | `json_extract(trigger_context, '$.alert_id')`（PriceLevelAlertInfo 加 alert_id 字段后镜像）|
| **cancelled** | `trade_actions WHERE action='cancel_price_level_alert'` | 新加 `alert_id` 列 |
| **cancel attempts** | `tool_calls WHERE tool_name='cancel_price_level_alert'` | `json_extract(args, '$.alert_id')` |

#### 5.3.2 view SQL

```sql
CREATE VIEW v_alert_lifecycle AS
WITH registers AS (
  SELECT session_id, alert_id,
         created_at AS registered_at,
         price AS target_price,
         reasoning AS register_reasoning
  FROM trade_actions
  WHERE action='add_price_level_alert' AND alert_id IS NOT NULL
),
triggers AS (
  SELECT session_id,
         json_extract(trigger_context, '$.alert_id') AS alert_id,
         created_at AS triggered_at,
         CAST(json_extract(trigger_context, '$.current_price') AS REAL) AS triggered_price
  FROM agent_cycles
  WHERE triggered_by='alert'
    AND json_extract(trigger_context, '$.type')='price_level_alert'
    AND json_extract(trigger_context, '$.alert_id') IS NOT NULL
),
cancels AS (
  SELECT session_id, alert_id,
         created_at AS cancelled_at,
         reasoning AS cancel_reasoning
  FROM trade_actions
  WHERE action='cancel_price_level_alert' AND alert_id IS NOT NULL
),
cancel_attempts AS (
  SELECT session_id,
         json_extract(args, '$.alert_id') AS alert_id,
         COUNT(*) AS attempt_count,
         SUM(CASE WHEN status='biz_error' THEN 1 ELSE 0 END) AS attempt_failures
  FROM tool_calls
  WHERE tool_name='cancel_price_level_alert'
  GROUP BY session_id, json_extract(args, '$.alert_id')
)
SELECT
  r.session_id,
  r.alert_id,
  r.registered_at,
  r.target_price,
  r.register_reasoning,
  t.triggered_at,
  t.triggered_price,
  c.cancelled_at,
  c.cancel_reasoning,
  COALESCE(ca.attempt_count, 0)    AS cancel_attempt_count,
  COALESCE(ca.attempt_failures, 0) AS cancel_attempt_failures,
  CASE
    WHEN t.triggered_at IS NOT NULL THEN 'triggered'
    WHEN c.cancelled_at IS NOT NULL THEN 'cancelled'
    ELSE 'active'
  END AS final_status
FROM registers r
LEFT JOIN triggers       t  ON t.session_id=r.session_id  AND t.alert_id=r.alert_id
LEFT JOIN cancels        c  ON c.session_id=r.session_id  AND c.alert_id=r.alert_id
LEFT JOIN cancel_attempts ca ON ca.session_id=r.session_id AND ca.alert_id=r.alert_id;
```

**关键设计点**：

- `final_status` 三态枚举（active/triggered/cancelled）— 直击 cluster #3 alert 状态漂移
- `cancel_attempt_count ≥ 2` → "重复 cancel" 信号（R2-Next-E #3 idempotent 议题数据源）
- `cancel_attempt_failures > 0` → cancel 40% 失败率议题数据源
- **历史数据天然丢失（不是缺陷）**：sim #1-#8 数据在三个事件源都缺 alert_id —
  - registers / cancels CTE：`trade_actions.alert_id IS NULL`（X 方案前列不存在），被 `WHERE alert_id IS NOT NULL` 过滤
  - triggers CTE：`trigger_context` JSON 在 sim #1-#8 全无 `alert_id` key（PriceLevelAlertInfo 加 alert_id 是本期改动），被 `json_extract(...) IS NOT NULL` 过滤
  - cancel_attempts CTE：`tool_calls.args` JSON 含 alert_id（与 alert_id 列改动无关），不受影响
  - **后果**：sim #1-#8 历史 alert lifecycle 数据**完全无法重建**；v_alert_lifecycle 仅服务 sim #9+；分析者按 `created_at >= '<本期 land 时间>'` 过滤

### 5.4 `v_order_lifecycle` view

```sql
CREATE VIEW v_order_lifecycle AS
SELECT
  so.session_id,
  so.order_id, so.symbol, so.side, so.position_side,
  so.order_type, so.amount,
  so.trigger_price, so.filled_price, so.fee, so.leverage, so.frozen_margin,
  so.created_at, so.filled_at, so.status,

  -- Lifetime duration
  CASE
    WHEN so.filled_at IS NOT NULL
    THEN CAST((julianday(so.filled_at) - julianday(so.created_at)) * 86400 AS INTEGER)
  END AS lifetime_seconds,

  -- Algo trigger drift (trigger vs fill) — 仅对 algo 单（stop / take_profit）有意义
  -- limit 单的 fill_price = trigger_price 是结构性恒等（simulated.py:53/547），drift = 0 是
  -- 信号噪音；用 order_type filter 把 limit 单 drift 设 NULL 让分析端语义清晰
  -- signed (无 ABS): 正值 = filled > trigger；正负号未做 side-aware 归一，分析者自行判方向
  CASE
    WHEN so.order_type IN ('stop','take_profit')
     AND so.trigger_price IS NOT NULL AND so.filled_price IS NOT NULL
    THEN (so.filled_price - so.trigger_price) / so.trigger_price * 100.0
    ELSE NULL
  END AS trigger_drift_pct,

  -- 注：原 lifecycle_state 列已删除（与 so.status 1:1 自映射，零信息密度）；
  -- 消费方直接读 so.status 即可

  -- Cycle correlation via trade_actions bridge — 取最早创建 cycle（按 created_at LIMIT 1）
  -- 同一 order_id 可能在多个 cycle 留 trade_actions（如 cycle A 创建 / cycle B cancel），
  -- originated 仅指**创建时所属 cycle**（origin），cancel/replace 等后续 action 不影响 origin 归属
  (SELECT ta.cycle_id
   FROM trade_actions ta
   WHERE ta.order_id=so.order_id
     AND ta.action IN ('open_position','close_position','place_limit_order',
                       'set_stop_loss','set_take_profit')
   ORDER BY ta.created_at LIMIT 1) AS originated_cycle_id

FROM sim_orders so;
```

**关键设计点**：

- `lifetime_seconds` 派生（julianday SQLite 标准）
- `trigger_drift_pct` **signed** 衡量 algo order 触发漂移（与 `okx_demo_mark_vs_last_drift` memory 议题相关；保留正负号让分析者判方向，未做 side-aware 归一）
- `originated_cycle_id` correlated subquery 关联回 agent_cycles 通过 trade_actions 桥接

### 5.5 `src/cli/app.py` 写入路径改动

#### 5.5.1 happy path（line 651-668）

```python
# Existing token extraction (line 599-617) — 双轨：旧变量保留给 logger 兼容，新变量给 DB 写入
usage = result.usage()
tokens = usage.total_tokens if usage else 0
details = (usage.details or {}) if usage else {}

# === 旧变量名保留（cli/app.py:608-616 logger.info 输出 + sim log 解析脚本兼容）===
# reasoning_tokens 走 details vendor key — pydantic-ai 当前未暴露 reasoning_tokens
# 标准属性（grep .venv/lib/python3.13/site-packages/pydantic_ai/usage.py:20-243 确认）；
# DeepSeek o-series thinking 模型在 details dict 提供此 key
reasoning_tokens = details.get("reasoning_tokens", 0)
cache_hit   = details.get("prompt_cache_hit_tokens", 0)    # DeepSeek-specific
cache_miss  = details.get("prompt_cache_miss_tokens", 0)   # DeepSeek-specific
input_total = cache_hit + cache_miss
hit_rate = (cache_hit / input_total * 100) if input_total > 0 else 0.0  # 保留 0.0 兼容 line 614 `{hit_rate:.1f}` 格式化

# === 新变量 — pydantic-ai 标准属性 (usage.py:20-35) 给 DB 写入（更 portable + AC-11 验证一致）===
cache_read  = usage.cache_read_tokens  if usage else 0
cache_write = usage.cache_write_tokens if usage else 0
input_tok   = usage.input_tokens       if usage else 0
output_tok  = usage.output_tokens      if usage else 0

session.add(
    AgentCycle(
        # ... existing fields
        tokens_consumed=tokens,
        # === Phase 1 新加 ===
        wall_time_ms=int((datetime.now(timezone.utc) - cycle_started_at).total_seconds() * 1000),
        llm_call_ms=llm_call_ms,    # 新变量, 见 §5.5.3
        input_tokens=input_tok,
        output_tokens=output_tok,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning_tokens,
        cache_hit_rate=hit_rate,
    )
)
```

**Note 1（语义对齐验证 = AC-11）**：双轨设计——旧变量名 (`cache_hit` / `cache_miss` / `input_total` / `hit_rate`) 保留给 cli/app.py:613-616 logger.info 输出 + 现有 sim log 解析脚本兼容；新变量名 (`cache_read` / `cache_write` / `input_tok` / `output_tok`) 走 pydantic-ai 标准属性给 DB 写入。AC-11 验证两轨语义一致：(a) `usage.cache_read_tokens ≈ cache_hit`（5% 误差内）；(b) `usage.input_tokens ≈ cache_hit + cache_miss`（5% 误差内）。不一致按 standard 属性为准 + plan 中记录差异。

**T0 实测结论 (2026-05-08, deepseek-v4-pro)**: 长 system_prompt (~425 input tokens) 触发 DeepSeek KV cache 后两轨完全对齐——`usage.cache_read_tokens=384` vs `details['prompt_cache_hit_tokens']=384`（相对误差 **0.0%**）；`usage.input_tokens=425` vs `(cache_hit + cache_miss)=425`（相对误差 **0.0%**）。两轨语义对齐 ✓ → 续 plan。注：短 prompt（<256 tokens）DeepSeek 不触发 cache；长 prefix prompt 才能验证 (a)。

**前置验证选项（不强制）**：archived sim DB 不含 raw `result.usage()` 对象（仅 `tokens_consumed` 总数），无法反推；如 spec land 前可抽空在 `cli/app.py:600` 加 `logger.debug(f'usage: {vars(usage)} | details: {details}')` 跑一个 dev cycle 抓 raw 比对，结论前置写入本 Note；否则按 plan task 0 处理（plan 阶段加临时 logger.debug → 跑 cycle → 比对 → 删除 logger）。

**Note 2（wall_time_ms vs Footer Duration 语义差）**：本字段在 `AgentCycle(...)` constructor 内 capture，发生在 `await session.commit()` **之前**；现有 Footer Duration 取 `cycle_ended_at` 在 commit **之后**（cli/app.py:540-542 注释明确写 "实墙时间含 DB 写入"）。两者差 ~5-50ms（DB write 时间）。**分析者比对 wall_time_ms 与 session log Footer Duration 时需注意此 5-50ms 漂移**，不视为 bug。如未来需要二者完全对齐需走 R2-Next-J cycle state machine refactor。

#### 5.5.2 forensic 路径（usage_limit_exceeded line 526-538 / retry_exhausted line 568-581）

**两路径分别 INSERT，模板见下；两处均按本模板 8 字段填值**（`wall_time_ms` 仍计算保留"卡多久"信号；其余 7 字段全 NULL）：

```python
session.add(AgentCycle(
    # ... existing fields
    tokens_consumed=0,
    # === Phase 1 新加（forensic）===
    # 表达：wall_time_ms 在 forensic 路径仍计算（cycle_started_at 总有），其余字段
    # 通过 except 块顶部 `llm_call_ms = None` 预设传入；其它 *_tokens / hit_rate 在
    # except 内不计算（用 None 字面量直接传）保留 NULL 语义
    wall_time_ms=int((datetime.now(timezone.utc) - cycle_started_at).total_seconds() * 1000),
    llm_call_ms=llm_call_ms,        # except 块顶预设的 None
    input_tokens=None,
    output_tokens=None,
    cache_read_tokens=None,
    cache_write_tokens=None,
    reasoning_tokens=None,
    cache_hit_rate=None,
))
```

`wall_time_ms` 在 forensic 路径仍填，给"卡多久"诊断保留信号。其余字段 NULL 与现有 `tokens_consumed=0` / `reasoning=None` / `decision=None` 模式一致。

#### 5.5.3 llm_call_ms 计时点（line 513 retry loop）

```python
for attempt in range(3):
    try:
        llm_start = datetime.now(timezone.utc)        # 新
        result = await agent.run(
            prompt,
            usage_limits=USAGE_LIMITS_PER_CYCLE,
            **run_kwargs,
        )
        llm_end = datetime.now(timezone.utc)          # 新
        llm_call_ms = int((llm_end - llm_start).total_seconds() * 1000)  # 新
        break
    except UsageLimitExceeded as e:
        llm_call_ms = None                            # forensic NULL
        # ... existing forensic write
    except Exception as e:
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)
        else:
            llm_call_ms = None                        # forensic NULL
            # ... existing forensic write
```

**精度边界**：retry attempt 1/2 失败时间不计入（只记最后成功 attempt）。这与现有 `tokens_consumed` 只记最后 attempt 的语义一致。

**三个 timing 字段偏序关系**（避免分析者误读）：

```
wall_time_ms ≥ llm_call_ms ≥ tool_total_ms
[cycle 全程]   [agent.run() 期间]  [SUM tool_calls.duration_ms 子集]
```

| 区段 | 涵盖 |
|---|---|
| `wall_time_ms` | cycle_started_at → AgentCycle constructor 内 capture（含 state_snapshot capture IO + prompt 构建 + agent.run + DB 写前部分；不含 commit / format_cycle_output 渲染——见 §5.5.1 Note 2）|
| `llm_call_ms` | llm_start → llm_end，即 `await agent.run(...)` 期间（含 LLM round-trip 多轮 + tool dispatching overhead + 每个 tool 实际 duration）|
| `tool_total_ms` | view 派生 = `SUM(tool_calls.duration_ms) WHERE cycle_id`（**是 llm_call_ms 的子集**，因 tool 在 LLM 阻塞期内执行）|

**重要**：`wall_time_ms - llm_call_ms - tool_total_ms` **不等价于"非 LLM 开销"**，因 tool_total_ms 已被 llm_call_ms 包含。正确的"非 LLM 开销"= `wall_time_ms - llm_call_ms`（含 state_snapshot capture + prompt 构建 + DB 写前部分）。

**retry-with-success 污染**：当 attempt #1/#2 失败 + #3 成功时：
- `llm_call_ms` 仅记 attempt #3 时长（精度边界已声明）
- `wall_time_ms` 包含 **attempt #1/#2 fail 时间 + asyncio backoff sleep**（exponential backoff：1+2=3 秒）
- 因此 "非 LLM 开销 = wall - llm" 在此场景被高估 **3+ 秒**

精确隔离需配合每 attempt 时间记录（`attempt` 列议题，留 P10 / R2-Next-J 处理；本期 view 接受此污染）。分析时建议 `JOIN agent_cycles WHERE execution_status='ok'` 后用 `tool_calls` 表 attempts 数辅助识别 retry-with-success cycle。

### 5.6 `src/agent/tools_execution.py` 改动

#### 5.6.1 `_record_action` 签名扩展

```python
async def _record_action(
    deps: TradingDeps, action: str,
    order_id: str | None = None,
    alert_id: str | None = None,        # 新
    side: str | None = None,
    price: float | None = None,
    pnl: float | None = None,
    reasoning: str | None = None,
) -> None:
    # ... existing body
    session.add(TradeAction(
        # ...
        order_id=order_id,
        alert_id=alert_id,              # 新
        # ...
    ))
```

**11 个 callers 改动范围**：本文件中 `_record_action` 共 11 个调用点。其中：
- **2 个改动**：`add_price_level_alert` (line 244-264) + `cancel_price_level_alert` (line 267-289)，新传 `alert_id=alert_id` 关键字
- **9 个零改动**：`open_position` / `close_position` / `place_limit_order` / `cancel_order` / `set_stop_loss` / `set_take_profit` / `adjust_leverage` 等。因 `alert_id: str | None = None` 默认值生效，新增字段对它们透明（仅写 NULL 到 trade_actions.alert_id）

**AC-3 验证范围限于 add/cancel 两个 caller**；9 个零改动 caller 不需新断言（默认值行为由 dataclass 标准语义保证）。

#### 5.6.2 两个 callers 传 alert_id

```python
# add_price_level_alert (line 244-264)
alert_id = deps.exchange.add_price_level_alert(price, direction, deps.symbol, reasoning)
# ...
await _record_action(
    deps, action="add_price_level_alert",
    alert_id=alert_id,                              # 新
    price=price,
    reasoning=f"{direction} {price} | {reasoning}", # 保留 — direction 信息在 trade_actions
                                                    # 没专列（side 列已被 long/short 占用）
)

# cancel_price_level_alert (line 267-289)
ok = deps.exchange.remove_price_level_alert(alert_id)
if ok:
    await _record_action(
        deps, action="cancel_price_level_alert",
        alert_id=alert_id,                          # 新
        reasoning=reasoning,                        # 删 `id={alert_id} | ` prefix（alert_id 已专列；
                                                    # direction 在 cancel 上下文不需要）
    )
```

**reasoning 字段处理对称性说明**：
- **add 路径**：保留 `{direction} {price} | ` prefix —— direction 不进 trade_actions 专列（side 列已被 long/short 占用，verify: tools_execution.py:64 docstring "side='long' or 'short'" + line 91/127/153/183/353/386 实测全 long/short），prefix 是 direction 信息唯一来源
- **cancel 路径**：删 `id={alert_id} | ` prefix —— alert_id 已落 trade_actions.alert_id 专列，prefix 冗余

**Land note（外部消费风险）**：内部代码（src/ + tests/）grep 验证 0 消费者依赖 `reasoning LIKE 'id=%'`；但 W2 用户外部 SQL pivot 脚本（`.working/sim8-w2-*.md` 等分析中曾用）若有 `WHERE reasoning LIKE 'id=%'` 抽 alert_id 模式，本期 land 后须切到 `trade_actions.alert_id` 列查询。

后续 `v_alert_lifecycle` 想拿 direction 可从 register 行 `reasoning LIKE 'above%' OR 'below%'` 抽（或更简：从 trigger_context.direction JSON 取，但仅 triggered cycle 有）。**本 view 不投影 direction**；W3 数据需要时独立 follow-up 加列。

### 5.7 `src/integrations/exchange/base.py` + `cycle_capture.py` 改动

```python
# base.py:286 — PriceLevelAlertInfo dataclass 加字段
@dataclass
class PriceLevelAlertInfo:
    symbol: str
    target_price: float
    direction: str
    current_price: float
    reasoning: str
    timestamp: int
    alert_id: str            # 新（**放最末**，无默认值）

# base.py:206 — _check_price_levels 实例化处传值
triggered.append(PriceLevelAlertInfo(
    symbol=alert["symbol"], target_price=alert["price"],
    direction=alert["direction"], current_price=current_price,
    reasoning=alert["reasoning"], timestamp=timestamp,
    alert_id=alert["id"],           # 新
))

# cycle_capture.py:54 — _capture_trigger_context 镜像 alert_id 到 JSON
return {
    "type": "price_level_alert",
    "alert_id": context.alert_id,    # 新
    "symbol": context.symbol,
    # ... existing fields
}
```

**alert_id 字段位置选择**：放 dataclass **最末**（与 `timestamp` 同列 metadata 风格），保 7 字段全无默认值（一致性）。当前代码库 0 positional 构造点（base.py:206 + tests/test_cycle_capture.py:286 + tests/test_price_level_alert.py:46 全部 keyword arg），加字段对现有 keyword caller 无影响；放最末更符合 future positional caller 的"append-only"直觉，降低后续扩展破坏面。

### 5.8 错误处理哲学

| 写入路径 | 失败哲学 | 代码体现 |
|---|---|---|
| AgentCycle INSERT (3 paths) | **不 fail-isolate** — DB 写失败应让 cycle 失败（保 cycle 完整性）| 现有 `await session.commit()` 无 try/except 包裹，沿用 |
| `_capture_state_snapshot` | **fail-isolated**（永不 raise，永返完整 dict）| 已有，不动 |
| `_capture_trigger_context` | **fail-isolated**（best-effort，失败 return None）| 已有；alert_id 字段缺失走现 except → trigger_context=None |
| `_record_action` (TradeAction INSERT) | **fail-isolated**（失败 logger.warning，不影响 tool return）| 现有 try/except，不动 |

## 6. Acceptance Criteria

| AC | 内容 | 验证方式 |
|---|---|---|
| AC-1 | alembic upgrade + downgrade roundtrip pass（8 列 add/drop + alert_id add/drop + 3 view create/drop）| `pytest tests/test_alembic_roundtrip.py`（新增）|
| AC-2 | 3 INSERT 路径 8 字段填值符合 §5.5.1/§5.5.2 规则 — happy 全填、forensic 仅 wall_time_ms 填其余 NULL | unit test mock `result.usage()` 三种路径分别断言 |
| AC-3 | `trade_actions.alert_id` 在 add + cancel 两个 callers 都正确写入；reasoning 不再 prefix `id={alert_id}` | unit test 跑 add/cancel 各一次 SELECT alert_id |
| AC-4 | PriceLevelAlertInfo 7 字段（alert_id 字段存在，无默认值，由 `_check_price_levels` 源头实例化时写入；位置在 dataclass 最末）+ auto-trigger 实例化 + trigger_context JSON 含 alert_id key | unit test `_check_price_levels` + `_capture_trigger_context` |
| AC-5 | `v_cycle_metrics` 字段 SELECT 与 §5.2 字段表一致；fixture 数据 SELECT 一行抽样匹配预期 | integration test 写 fixture cycle + SELECT * 断言 |
| AC-6 | `v_alert_lifecycle` 配对正确（同 alert_id 三态在一行）；`final_status` 枚举覆盖 100% input | integration test 写 register + trigger + cancel fixture，SELECT 验证 |
| AC-7 | `v_order_lifecycle` lifetime_seconds / trigger_drift_pct / originated_cycle_id 派生正确 | integration test 写 sim_orders fixture + 验证 |
| AC-8 | 历史 sim 数据兼容性 — sim #1-#8 数据被 view 接受（NULL 列不破坏 SELECT；alert_id IS NULL 行被自动过滤）| 跑现有 sim DB 文件 SELECT * FROM v_cycle_metrics 不 raise |
| AC-9 | 5-field anchor drift-guard — sim #8 archive DB 上 `SELECT AVG(five_field_complete) FROM v_cycle_metrics WHERE is_ok_cycle=1` ≥ **<阈值，plan task 0 实测 baseline 后填>**（R2-Next-A §5.5 baseline 171/171=100% 是**单字段** `(4) Thesis` 4-variant LIKE 命中率，**不是** 4 字段联合 `five_field_complete` 命中率；plan 阶段先跑一遍 sim #8 联合命中率取 baseline，再据此 set buffer，原则：阈值 = round_down(baseline - 5pp)；W3 上线后掉落 > 5pp 同此 buffer 触发暂停发布——见 §9 risks 第 1 行）<br>**baseline 回填位置**：测出后填入 `tests/test_5field_anchor_drift_guard.py` 顶部常量 `_BASELINE_HIT_RATE` + `_DRIFT_THRESHOLD = _BASELINE_HIT_RATE - 0.05`；本 spec AC-9 / §7.1 不回填具体数字（spec 是 plan 前置文档保持稳定）| offline test on archived sim #8 DB |
| AC-10 | view 性能 — sim #8 178 行级 `SELECT * FROM v_cycle_metrics` 单次 < 100ms（offline benchmark on archived sim #8 DB；非 CI strict gate；1500/100k 行级前瞻讨论见 §8.3）| offline benchmark script `scripts/benchmark_view_phase1.py`（新增）|
| **AC-11** | **plan task 0 验证** — pydantic-ai 标准属性 vs DeepSeek vendor key 语义对齐（双轨设计前提，见 §5.5.1 Note 1）：(a) `usage.cache_read_tokens ≈ details['prompt_cache_hit_tokens']`；(b) `usage.input_tokens ≈ details['prompt_cache_hit_tokens'] + details['prompt_cache_miss_tokens']`（验证 hit_rate 分母对齐）。**触发阈值**：(a)/(b) **任一相对误差 ≤ 5% → 续 plan**（在 plan 中记录实测差异）；**> 5% → 暂停 plan 回 spec 评估** hit_rate 公式重写 / vendor 兼容性方案（如启用 `cache_hit_rate_derived` 作主指标 + `cache_hit_rate` 列 deprecated 标注） | runtime sample 一个 cycle 双取值比对 |
| **AC-12** | **forensic enum drift-guard** — `tests/test_forensic_enum_completeness.py` 跑 `SELECT DISTINCT execution_status FROM agent_cycles WHERE execution_status NOT IN ('ok','retry_exhausted','usage_limit_exceeded')` 应返回空集；非空 → fail 提示新 enum 需同步 `v_cycle_metrics.is_forensic_cycle` CASE 列举（与 §9 风险表第 2 行同源）| sim DB 上 SQL 断言 |

### 6.5 阈值语义辨析（避免 90% / 5pp 区间困惑）

spec 提及三个数字阈值，**语义独立不可混淆**：

| 阈值 | 适用场景 | 触发动作 |
|---|---|---|
| **90%**（决策回滚阈值）| plan task 0 一次性 baseline 判定（§3 决策矩阵 #2）| baseline < 90% → **重审 view 形态**（决策点 #2 是否仍 viable / 是否应改物化表）|
| **5pp**（drift guard buffer）| baseline 实测后线性派生（§6 AC-9 / §9 风险表）| `drift_threshold = baseline - 5pp`；W3 上线后掉落超过此线 → 暂停发布 + 重审 LIKE pattern |
| **baseline 实测值**（plan task 0 输出）| 90%-100% 任意值 | 决定 drift_threshold 起点，**不影响** view 形态决策（除非 < 90%） |

**示例**：
- baseline 实测 92% → drift_threshold = 87%；view 形态保留；W3 监控 < 87% 触发警报
- baseline 实测 88% → 触发**view 重审**（< 90% 决策回滚阈值）；不再讨论 drift_threshold

## 7. Test plan

### 7.1 新增测试文件

| 文件 | 覆盖 |
|---|---|
| `tests/test_alembic_roundtrip_phase1.py` | AC-1：upgrade + downgrade 完整 roundtrip + view drop 顺序 |
| `tests/test_run_agent_cycle_phase1.py` | AC-2：happy / usage_limit_exceeded / retry_exhausted 三路径填值断言 |
| `tests/test_record_action_alert_id.py` | AC-3：add + cancel 两 callers 正确传递 alert_id |
| `tests/test_price_level_alert_info_alert_id.py` | AC-4：dataclass 7 字段 + auto-trigger + trigger_context 镜像 |
| `tests/test_v_cycle_metrics.py` | AC-5：fixture cycle + SELECT * 字段断言 |
| `tests/test_v_alert_lifecycle.py` | AC-6：register/trigger/cancel 三态 + final_status 枚举 |
| `tests/test_v_order_lifecycle.py` | AC-7：lifetime_seconds / trigger_drift_pct / originated_cycle_id |
| `tests/test_view_historical_compat.py` | AC-8：在 NULL columns 上 SELECT 不 raise |
| `tests/test_5field_anchor_drift_guard.py` | AC-9：sim #8 archive DB 实测 `AVG(five_field_complete) WHERE is_ok_cycle=1 AND session_id='8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3'` ≥ `<plan task 0 baseline - 5pp>`（具体阈值由 plan 回填，见 §6 AC-9 baseline 回填位置）。**CI 行为**：`pytest.skip(reason="sim DB not present")` 当 `--sim-db <path>` 未提供时；CI 默认不跑（sim DB 不进 git）。**手动运行**：W3 上线前 `pytest tests/test_5field_anchor_drift_guard.py --sim-db data/tradebot.db --session-id 8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`（仓库唯一 DB；session_id 是 sim8 178 cycles，verify by sqlite3 query）。**不预设 sim_archives/ 目录契约**——如未来需要归档其他 sim，独立 follow-up 议题 |
| `tests/test_view_performance.py` | AC-10：sim #8 178 行级单次 query < 100ms |
| `tests/test_forensic_enum_completeness.py` | AC-12：`SELECT DISTINCT execution_status FROM agent_cycles WHERE execution_status NOT IN ('ok','retry_exhausted','usage_limit_exceeded')` 应空集；非空 → fail 提示新 enum 需同步 `v_cycle_metrics.is_forensic_cycle` CASE 列举 |

### 7.2 已有测试影响

| 测试文件 | 影响 |
|---|---|
| `tests/test_run_agent_cycle.py` 系列 | 8 新字段值断言扩展；fixture 需 mock `result.usage().details` 含 cache/response keys |
| `tests/test_tool_recorder.py` / `test_trade_action.py` | 加 alert_id 列断言 |
| `tests/test_simulated_exchange.py` `_check_price_levels` | PriceLevelAlertInfo 6→7 字段断言扩 |
| `tests/test_cycle_capture.py` | trigger_context JSON 含 alert_id key 断言 |

预估测试改动总量：~30 测试触及，~500 行 test code 新增/修改。

**plan 实施策略（fixture 集中管理）**：pydantic-ai usage 替换涉及很多既有 mock_usage fixture 都需加 `cache_read_tokens / cache_write_tokens / input_tokens / output_tokens` 属性。**plan 阶段优先做 1 个集中 fixture factory** 集中管理，避免散射成本：

```python
# tests/conftest.py 或 tests/fixtures/usage.py
@pytest.fixture
def make_usage():
    """Factory for pydantic-ai RunUsage mock with Phase 1 standard attrs.
    Default values reflect a typical DeepSeek cycle; override per test as needed.
    """
    def _make(
        input_tokens: int = 1000, output_tokens: int = 200,
        cache_read_tokens: int = 700, cache_write_tokens: int = 0,
        details: dict | None = None,
    ):
        # ... return RunUsage-like mock object
    return _make
```

30+ 既有测试改成 `usage = make_usage(input_tokens=..., cache_read_tokens=...)` 而非各自构造，集中统一。

## 8. Migration / rollback

### 8.1 上线步骤

1. alembic upgrade head — 加 9 列 + 3 view（新 sim 数据立即受益）
2. 现有 sim DB 文件 — 9 个新列（agent_cycles 8 + trade_actions.alert_id 1）NULL，view 自动兼容（AC-8）
3. 新 sim #9+ — 全字段填，view 完整覆盖

### 8.2 回滚

`alembic downgrade <prev>` —— drop 3 view + 9 列。无业务影响（view 只读派生层）。

### 8.3 Live 量级前瞻

sim #8 178 行级实测 < 100ms（AC-10 验证）；1500 行（W3+ 多 sim 累积估算）+ live 100k+ cycle 量级若慢，单 PR 加 generated column index（SQLite 支持），无需重设计。

## 9. Risks + mitigation

| 风险 | 缓解 |
|---|---|
| 5-field anchor LIKE pattern 漂移（persona 未来改文案）| AC-9 drift-guard test（**offline gate**，非 CI 强制 — sim DB 不进 git）；W3 上线前在最近 archive sim DB 上跑 `AVG(five_field_complete) WHERE is_ok_cycle=1` 对比 plan baseline，掉落 > 5pp 触发暂停发布 + 重审 LIKE pattern（5pp buffer 与 AC-9 阈值同源）|
| 新增 `execution_status` forensic enum（如 R2-Next-J 加 'partial_completion' / 'crashed'）后 view 漏判 | `is_forensic_cycle` 用 enum 列举（`IN ('retry_exhausted','usage_limit_exceeded')`），新增 forensic 状态时**必须同步改 v_cycle_metrics 列定义** + alembic migration drop+recreate view；spec checklist 加入 follow-up（spec §10 Cross-issue linkage 已含 R2-Next-J 关联，不重复）|
| 历史 sim NULL 比例高破坏 view | view 设计 `COALESCE` / `IS NOT NULL` 显式兼容（§5.3.2 / §5.4）；spec 注释 |
| pydantic-ai `cache_read_tokens` 标准属性 vs DeepSeek `prompt_cache_hit_tokens` 语义对齐未验证 | plan 阶段第一个抽样 cycle 跑完比对二者一致性（§5.5.1 Note）；不一致则取 `usage.cache_read_tokens` 为准 + plan 中记录差异 |
| 8 列加到 agent_cycles 破坏 W2 SQL pivot scripts | 全 nullable + 现有脚本不读这些列；R2-7 escape-hatch 模式同源 |
| alert_id 列加在 trade_actions 后历史行 NULL → v_alert_lifecycle 配对失败 | view `WHERE alert_id IS NOT NULL` 隐式过滤；spec 注释 |
| view-on-view 性能（多 json_extract + LIKE）| sim 量级 < 100ms（AC-10）；live 量级触发时单 PR 加 generated column index |
| 测试 fixture 重写工作量低估 | plan 阶段拆分独立 task，含 §7.2 已有测试影响清单 |
| W3 sim 上线后 view 暴露新 bug | view 是只读派生，可快速 DROP 修 SQL 重建（无业务影响）|

## 10. Cross-issue linkage

| 议题 | 关系 | 处理 |
|---|---|---|
| `observation_period_metrics_review_checklist` #1 attempt 列 | 与 P10 同源 | 留 Phase 3/J，本 spec §4.2 OOS |
| 同上 #2 cycle_id 索引 | **R2-7 已 supersede** | 已闭合 |
| 同上 #3 result_preview | 与 P3 重叠 | 留 Phase 3，本 spec §4.2 OOS |
| `w2_ops_backlog` S1 alembic NULL 检测 | 本期 downgrade 注释吸收 | 部分闭合（§5.1.3）|
| `w2_ops_backlog` S3 state_captured_at | 本期 view JSON 派生 + 根因留 R2-Next-J | 部分闭合（§2.4 / §5.2）|
| `iter4_sql_caveats` 三类边界 | view 注释吸收 | 闭合（§5.2.4）|
| `pydantic_ai_compliance` P1 logfire | 本议题是其触发条件 | 已决 reject Phase 1（§2.4）|
| `n10_recent_decisions_context_injection`（R2-8b）| P4 是其调试镜像 | 留 Phase 3 |
| `agent_reflection_tools_candidate` | W3+ 议题 | 不影响本期 |
| `okx_demo_mark_vs_last_drift` | `v_order_lifecycle.trigger_drift_pct` 与之相关 | 本期 view 暴露数据，分析独立 |
| `state_snapshot._cycle_id` JSON 字段冗余 | view column-level 后无消费者 | follow-up backlog（独立 mini-PR 删 `cycle_capture.py:110` 写入 + spec 配套清理；不在本期 scope）|

## 11. References

- `.working/observability-gaps-from-sim8.md` — 痛点定义 P1-P10 + 5 维度归纳
- `.working/observability-solutions-from-sim8.md` — 逐项解决方案 + 4 类打包
- `.working/logfire-evaluation.md` — Logfire fork 决策（reject Phase 1）
- `.working/sim8-w2-rerun-findings.md` — sim #8 raw-data 4 维度重挖（cluster #1-#4 议题源）
- `docs/superpowers/specs/2026-05-07-iter-w2r2-next-a-f1-feedback-loop-design.md` §5.5 — 5-field 4-variant LIKE 100% 实证
- `src/cli/app.py:435-687` — run_agent_cycle 主循环
- `src/services/cycle_capture.py:24-204` — capture helpers
- `src/storage/models.py:57-189` — TradeAction / AgentCycle / ToolCall schema
- `src/integrations/exchange/base.py:199-292` — `_check_price_levels` + PriceLevelAlertInfo
- `alembic/versions/eeeee565cb36_r2_7_agent_cycle_schema_reframe.py` — batch_alter_table 模式参考
