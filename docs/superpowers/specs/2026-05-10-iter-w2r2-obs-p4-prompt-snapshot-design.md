# Iter W2R2 — Observability P4 (Prompt Snapshot)

> **Status**: spec v1, 2026-05-10
> **Branch**: `feature/iter-w2r2-obs-p4-prompt-snapshot`
> **Source brainstorm**: 本会话 dialogue（痛点 → 4 支柱 → A1/A2/A3 取舍 → A2 + system_prompt session 级简化）
> **Pair-doc**: `.working/observability-gaps-from-sim8.md` §P4 / `.working/observability-solutions-from-sim8.md` §P4 / `.working/sim8-w2-inventory.md` P2-9
> **Phase 1+2 prerequisites**: PR #42 (`6250e51`) + PR #43 (`1d9a236`) + PR #44 (`5821fe2`) 全 landed
> **Memory anchors**: `project_observability_roadmap_from_sim8` / `project_phase2_w3_followups` / `project_w2_ops_backlog` / `feedback_observation_period_soft_constraint`

---

## §1 Background & Scope

### §1.1 痛点定位

W2 sim #8 raw-data 重挖（详见 `.working/sim8-w2-rerun-findings.md`）暴露的核心 forensic 痛点：**agent 每 cycle 实际收到的完整 prompt 发送后即抛，DB 不留副本**。后续调查只能停留在「机理推测」层，无法做「可证伪假设」级别的 root cause。

具体不可调查的事实（已实证）：

| ID | 不可查事实 | 实证来源 |
|---|---|---|
| W1 | F1 cluster D7 — 4000-char `CYCLE_DECISION_HARD_CAP` 触发 65 次 priors 截断，截掉了什么内容 | rerun-findings D7 |
| W2 | R2-8b N=3 priors 实际注入的内容 + render 形态 | rerun-findings cluster #2 |
| W3 | memory top 10 自动注入的是过程记录还是 long-term lesson | rerun-findings cluster #2 v1 reframe |
| W4 | Cluster #1 末段塌陷 transition cycle 是不是 priors 内容塑造的 | Cluster #1 机理推测层 |
| W5 | N4 6 工具 0 调用 — 是 prompt 不够 hint 吗 | inventory P2-16 |
| W6 | persona 改版前后 cycle 行为对比 | observability-gaps §P4 |
| W7 | Cluster #2 v0 错误诊断 5 分钟 grep 成本（若 prompt 在 DB 则一秒） | rerun-findings 元教训 |

**痛点性质**：分析者痛点（非 agent 行为痛点）。sim 跑时 agent 不受影响；sim 跑完后所有 forensic 撞这个天花板，导致 sim #8 5 cluster 中 3 个停留在「机理推测」层。

### §1.2 痛点边界（明确 P4 不解决的）

- LLM internal CoT（Anthropic / DeepSeek 不暴露 thinking 推理链）
- 模型从同一 prompt 推出不同结论的"心理机制"
- model 端 KV cache 等隐式状态
- Layer 2 conversation history（多轮 tool call 后增长的 message tree）— 已由现有 `tool_calls` 表 args+status 部分覆盖；属于 P3 (tool_response payload) 议题，与本期解耦

P4 解锁的是「机理推测 → 可证伪假设」的飞跃，不是完整 LLM 黑盒打开。

### §1.3 LLM 互动 3 层与 capture 选择

```
Session 启动一次性绑定：
  Layer 0  system_prompt           (session-fixed)        ← P4 capture
                  ↓
Cycle 内（src/cli/app.py:466-505 拼接）：
  Layer 1  user_prompt             (per-cycle dynamic)    ← P4 capture
            ├─ trigger 段
            ├─ priors block (R2-8b N=3)
            └─ memory_context (top 10 lessons)
                  ↓
agent.run(user_prompt) — 多轮 tool 互动：
  Layer 2  conversation history    (multi-round)          ← OOS（→ P3 议题）
```

P4 capture **Layer 0 + Layer 1**。Layer 2 由 `tool_calls` 表 + 未来 P3 解决。

### §1.4 三方案取舍记录（A1/A2/A3）

brainstorm 阶段评估的三个方案：

| 方案 | 行数 | 痛点解锁 | 决议 |
|---|---|---|---|
| A1 Minimum | ~80 | 5/7 (W1-W4, W7) | 否决 — 见下方对称论证 |
| **A2 Standard** | ~120-150 | **7/7** | **采纳** |
| A3 Comprehensive | ~180-220 | 7/7 + W1 强化 | 否决 — 见下方对称论证 |

**A1/A3 反推路径对称分析**：

A1 不存 `system_prompt`，但反推可行：`sessions.persona_config` 已是 JSON snapshot + `RuntimeConfig.wake_max_minutes` 由公式 `min(max(4 * scheduler_interval_min, 60), 180)` 重算（src/cli/app.py:783）+ `generate_system_prompt(persona, runtime)` 是纯函数（src/agent/persona.py:56）。

A3 不存 `priors_block_full`，但反推可行：前 N 轮 `agent_cycles.decision` 已存 + `_build_recent_summaries_block` 是 deterministic function（R2-8b PR #38 引入，commit `28f7265`；R2-Next-A PR #40 增量演进 length feedback）。

**两方案的反推稳定性风险等级相当**——都依赖未来 render / generate 函数源码不大改。

**A2 真正赢 A1 / A3 的论据**（非"反推不存在"）：

| 维度 | A1 (system_prompt 反推) | A2 (capture) | A3 (priors_block_full 反推) |
|---|---|---|---|
| forensic 使用频率 | W5/W6 调查频繁（N4 prompt hint / persona 改版对比）| — | W1 truncation 调查触发型（sim #8 单一证据）|
| 分析者体验 | 必须 import + 调用 `generate_system_prompt(PersonaConfig.model_validate(json.loads(persona_config)), RuntimeConfig(wake_max_minutes=...))` —— 含 dict→PersonaConfig 重水合 + RuntimeConfig 重算 | 直接 `SELECT system_prompt FROM sessions` | 同 A1 复杂度（需 import `_build_recent_summaries_block`）|
| capture 边际成本 | — | ~15-20 KB / session × 几十 sessions = ~1 MB | 不增量字段则 0；增字段则改 R2-8b 产品代码（风险中）|
| 改产品代码 | 否 | 否（只 +1 字段 +1 capture call）| 是（改 `_build_recent_summaries_block` 返回 tuple 或加独立 capture）|

**A2 决议依据**：高频 forensic 使用（W5/W6）+ 直接 SELECT 体验 + 极低 capture 成本，三者叠加值得 capture system_prompt；A3 低频 forensic 使用（W1）+ 同等反推复杂度 + 改产品代码风险，不值得 capture priors_block_full。

**反推 caveat（A1 + A3 共有）**：依赖 render / generate 函数稳定。若将来大改，老 cycle 反推会失真。当前 W3 调查窗口内（数月级）成立。若将来大改，可作触发型 follow-up。

### §1.5 Scope 边界

| 维度 | In Scope | Out of Scope |
|---|---|---|
| Schema 变动 | `sessions.system_prompt` + `agent_cycles.user_prompt_snapshot` 各 1 字段 | 独立表 / 拆字段 / persona_versions 表 |
| Alembic migration | 1 个（同 PR） | — |
| `src/storage/models.py` | `Session` + `AgentCycle` 各 +1 字段 | — |
| `src/cli/app.py` | session 启动 capture 1 处（在 `run()` async 函数 line ~935 build_services 调用之后） + cycle 3 个 INSERT 路径加字段 | retry loop 改造 / prompt 构造代码改造 / build_services 签名改造 |
| `src/agent/` | 不动（trader.py / persona.py 签名保留） | — |
| `src/services/cycle_capture.py` | 不动（trigger_context / state_snapshot 既有 capture 保留） | — |
| Truncation 双存档 | OOS（pre-truncation 反推路径已存在） | → 触发型 follow-up |
| Retention 策略 | sim 永久（数据量级 ~3-4 MB / sim 无压力） | live 模式滚动策略 + sensitive data scan → 实盘前评估 |
| `_build_recent_summaries_block` 改造 | 不动 | — |

**纯增量 PR**：1 alembic + 2 source 文件改动（models.py + app.py）+ 测试。

---

## §2 Architecture

### §2.1 数据流

```
   ┌──────────────────────────────────────────────────┐
   │  Session 启动（src/cli/app.py 内 async run() 函数 │
   │  line ~935 build_services 调用之后；run() 是     │
   │  async，capture 路径需要 await — 详 §4.1）        │
   │   build_services(...) → exchange/deps/agent/...  │
   │      ↓                                            │
   │   ★ P4 capture（新增）：                          │
   │     system_prompt_text =                         │
   │         generate_system_prompt(persona, runtime) │
   │     UPDATE sessions SET system_prompt = ...      │
   │     WHERE id = session_id                        │
   │     (try/except catch-all → fail-isolated)       │
   │     (resume 路径同样覆写 — 详 §3.1)               │
   └──────────────────────────────────────────────────┘
                       │
                       ↓
   ┌──────────────────────────────────────────────────┐
   │  Cycle handler (run_agent_cycle, async)          │
   │   1. _capture_trigger_context() (既有)            │
   │   2. _capture_state_snapshot() (既有)             │
   │   3. 拼 prompt = trigger + priors + memory        │
   │      ↓                                            │
   │   4. ★ P4 capture（新增）：                       │
   │      user_prompt_snapshot_var = prompt           │
   │      (字符串引用赋值 — 不可能 raise)              │
   │   5. agent.run(prompt) — retry loop ×1-3         │
   │      ⚠ INVARIANT: prompt 在 retry loop 内不可变   │
   │        (line 518: 同一 prompt 三次)               │
   │        — 见 §9 AC-10 / §11 风险表                 │
   │   6. cycle finalize:                             │
   │      AgentCycle insert (3 路径之一):              │
   │        ─ happy / usage_limit /                   │
   │          retry_exhausted                         │
   │      → 3 路径全部带 user_prompt_snapshot=...      │
   └──────────────────────────────────────────────────┘
```

### §2.2 3 个 INSERT 路径

| 路径 | 行（约） | execution_status | user_prompt_snapshot |
|---|---|---|---|
| Happy | ~683 | `ok` | ✅ 必填 |
| usage_limit | ~531 | `usage_limit_exceeded` | ✅ 必填（forensic 价值高）|
| retry_exhausted | ~582 | `retry_exhausted` | ✅ 必填（**forensic 价值最高** — sim #8 实测 0.56% 频率；retry loop `except Exception` 在 attempt==2 时 fallthrough 此路径，已涵盖未预期 LLM 异常 catch-all）|

统一原则：**3 路径无 special case**。

### §2.3 与现有 capture 的关系

P4 与 `_capture_trigger_context` / `_capture_state_snapshot` 互补不冲突：

| Capture | 内容 | 形态 |
|---|---|---|
| `trigger_context` (既有) | trigger_type + 结构化 context（alert info / fill notification）| JSON 元数据 |
| `state_snapshot` (既有) | position / balance / open orders 等市场+账户快照 | JSON 元数据 |
| **`user_prompt_snapshot`** (P4) | LLM 实际看到的 user prompt 渲染文本 | 全文字符串 |

trigger_context 与 user_prompt_snapshot **不冗余**：前者是程序化查询的结构化字段（`SELECT ... WHERE json_extract(trigger_context, '$.type') = 'alert'`），后者是 LLM 看到的渲染文本（`SELECT ... WHERE user_prompt_snapshot LIKE '%PRICE LEVEL%'`）。

---

## §3 Schema 改动

### §3.1 `sessions` 表加字段

```python
class Session(Base):
    # ... 现有字段（id, name, symbol, persona_config, model_config, ...）
    system_prompt: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # P4: rendered system prompt at session creation; session-fixed
```

**字段语义**：
- 内容 = `generate_system_prompt(persona_config, runtime)` 返回字符串（Layer 0 完整文本）
- 写入时机 = session 启动时 UPDATE（**含 resume 路径** — 详下）
- nullable = True（historical sessions 必为 NULL；capture 失败也落 NULL）

**Resume 路径语义**（明确决议）：

每次 session 启动（含 resume）都用最新 `persona_config` + `runtime_config` 重算并**覆写** `sessions.system_prompt`。这与现有 `session_manager.py:170-176` resume 路径行为对齐 —— 该路径已在 resume 时持久化新 `model_config`，P4 system_prompt 走同款语义。

| Resume 行为 | 选项 | 决议 |
|---|---|---|
| 每次启动覆写 | ✅ 采纳 | 与 model_config resume 行为对齐；总是反映"当前 session anchor" |
| 仅 IS NULL 才写 | ❌ 否决 | resume 后若 persona/runtime 改变，DB 不反映 → 误导分析者 |
| 加 `system_prompt_history` 表 | ❌ 否决 | 当前 persona 在观察期处于 placeholder 状态（5 数值字段都 placeholder，per `project_persona_dead_config_decision`），跨 resume 改版需求实际为 0；W2-W3 阶段不上 ROI 不足 |

**Forensic 损失说明 + 风险有限性**：

覆写后 `sessions.system_prompt` 仅反映"最近一次 session 启动时的"，不能跨多次 resume 回查历史。严格说 cycle 级 `user_prompt_snapshot` 只 capture Layer 1（user prompt），**不含 Layer 0 (system_prompt)**——所以这一损失**不能**由 cycle 级直接兜底。但风险有限：

1. **单 session 内 persona/runtime 通常不变** → resume 覆写不丢真实信息（覆盖前后值相同）
2. **跨 resume 真要查历史 system_prompt** → 走 git history 反推（`generate_system_prompt` 函数版本 + persona_config 历史 = 重算）。当前 persona 在观察期 placeholder 状态，git history 重算成本可接受
3. **W6 persona 改版逐次历史** → 若未来真有需求（持续观察期外 / persona 5 数值字段进入 implementation），再加 `system_prompt_history` 表作触发型 follow-up

**为什么不用 hash + persona_versions 表去重**：单 session 内（含 resume 跨多次启动期间）system_prompt 通常不变；sessions 表是天然 anchor。跨 session 比较 = `SELECT system_prompt FROM sessions WHERE id IN (a, b)` 已够。占用 ~15-20 KB / session × 几十 sessions ≈ 1 MB（永久），无压力。

### §3.2 `agent_cycles` 表加字段

```python
class AgentCycle(Base):
    # ... 现有字段（cycle_id, triggered_by, trigger_context, state_snapshot,
    #                reasoning, decision, ..., Phase 1 9 列）
    user_prompt_snapshot: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # P4: full user prompt sent to agent.run(); per-cycle
```

**字段语义**：
- 内容 = `cli/app.py:466-505` 拼接好的 `prompt` 变量全文（Layer 1 完整字符串）
- 写入时机 = cycle finalize 时（3 个 INSERT 路径之一）
- nullable = True（historical / capture 失败）

**字段命名取舍**：

| 备选 | 决议 |
|---|---|
| **`user_prompt_snapshot`** | ✅ 采纳 — 明确 user 而非 system；snapshot 暗示 capture 性质 |
| `prompt_text` | ❌ 太泛，与 system_prompt 混淆 |
| `full_prompt` | ❌ 不准（cycle 内 LLM 实际看到 = system + user + tool history）|

### §3.3 Alembic Migration

**单文件**（同 PR）：

```python
def upgrade():
    op.add_column('sessions',
        sa.Column('system_prompt', sa.Text(), nullable=True))
    op.add_column('agent_cycles',
        sa.Column('user_prompt_snapshot', sa.Text(), nullable=True))

def downgrade():
    op.drop_column('agent_cycles', 'user_prompt_snapshot')
    op.drop_column('sessions', 'system_prompt')
```

**downgrade 安全性**：两字段都 nullable，downgrade 直接 `drop_column` 不爆 IntegrityError。对照 R2-7 alembic（`alembic/versions/eeeee565cb36_r2_7_agent_cycle_schema_reframe.py`）：upgrade 把 `decision` 改 `nullable=True`，downgrade 收紧回 `nullable=False`，靠**注释**提示先 `DELETE FROM agent_cycles WHERE execution_status='usage_limit_exceeded' AND decision IS NULL`（非 RuntimeError 守卫）；不清理则触发 SQLite IntegrityError。**P4 不踩此雷**：两字段始终 nullable，无 forensic NULL 阻塞 downgrade。

### §3.4 现有字段不动

- `agent_cycles.trigger_context` — JSON 元数据，与 user_prompt_snapshot 互补
- `agent_cycles.state_snapshot` — JSON 市场快照
- `sessions.persona_config` / `model_config` — 已有 JSON snapshot，不替代

---

## §4 Capture 时机与位置

### §4.1 Session 级 capture

**位置**：`src/cli/app.py` 内 `async def run()` 函数 line ~935（`build_services` 调用之后，进入 Phase 6 main loop 之前）。

**为什么不放 `build_services` 内部**：`build_services` 是 sync `def`（line 740），不能 `await`。capture 路径需要 async DB session，必须在 async caller 层执行。备选方案"改 build_services 为 async"会蔓延影响其多个 caller，得不偿失。

**实现伪码**（在 `run()` 内，`build_services(...)` 调用后）：

```python
# 现有 (line ~933-935)
exchange, deps, agent, budget, stats = build_services(
    result, engine, session_id, sc, settings,
)

# ★ P4 加（新增 ~12 行）：
try:
    # max_wake 公式与 build_services 内部 line 783 一致；P4 + build_services 共用。
    # 实施时把公式抽 helper（如 build_runtime_config）让两处共享，避免漂移
    # （详 implementation plan）。
    max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
    runtime_config = RuntimeConfig(wake_max_minutes=max_wake)
    system_prompt_text = generate_system_prompt(
        result.persona, runtime_config
    )
    async with get_session(engine) as s:
        await s.execute(
            sql_update(Session)  # 沿用既有 import 别名（line 13）
            .where(Session.id == session_id)
            .values(system_prompt=system_prompt_text)
        )
        await s.commit()
except Exception as e:
    logger.warning(f"P4 system_prompt capture failed: {e!r}")
    # session 继续启动；sessions.system_prompt 留 NULL
```

**设计选择**：

1. **直接调 `generate_system_prompt`**（纯函数 ~ms 级），不动 `create_trader_agent` 内部签名。代价：同一函数调两次（trader.py 内部第 58 行 + 此处）— 合理：函数 deterministic + cheap + 不引入 trader.py / build_services 改动 + 不破坏现有 caller。

2. **沿用 `sql_update` import 别名**（src/cli/app.py:13 已 `from sqlalchemy import select, update as sql_update`，line 963/1046 都用 `sql_update`），保持代码风格一致。

3. **重算 `runtime_config`**：与 `build_services` 内 line 783-784 同款公式。implementation 阶段建议把公式抽 helper（如 `build_runtime_config(scheduler_interval_min)`）让两处共用避免漂移。

**Resume 路径**：本 capture 在每次 `run()` 启动时执行（含 resume），`UPDATE sessions` 总是覆写——见 §3.1 决议表。

**备选方案（不推荐）**：
- 把 `build_services` 改 `async def` —— 蔓延影响其调用者
- 让 `create_trader_agent` 返回 `(agent, system_prompt)` tuple —— 破坏现有 caller + 测试

### §4.2 Cycle 级 capture

**位置**：`src/cli/app.py:466-505` prompt 拼接段后、retry loop 之前。

**实现伪码**：

```python
# 现有 (line 466-505 拼接段不动)
prompt = "You have been woken up by a {trigger_type} trigger.\n..."
if trigger_type == "conditional":
    prompt += msg
elif trigger_type == "alert":
    prompt += alert_msg

recent_block = await _build_recent_summaries_block(engine, deps.session_id, n=3)
if recent_block:
    prompt += f"\n\n{recent_block}"

memory_context = await deps.memory.format_for_prompt()
if memory_context != "No relevant memories.":
    prompt += f"\n\nYour memories:\n{memory_context}"

# ★ P4 加（单行）：
user_prompt_snapshot_var = prompt

# 现有 retry loop 不动
for attempt in range(3):
    result = await agent.run(prompt, ...)
```

**3 个 INSERT 路径**（`line ~531/582/683`）每个加 `user_prompt_snapshot=user_prompt_snapshot_var`。

### §4.3 与现有 capture 的时序

```
cycle handler 时间线：
  retry loop 之前：
    ① _capture_trigger_context()           → trigger_context_var
    ② _capture_state_snapshot()            → state_snapshot_var
    ③ 拼 prompt
    ④ user_prompt_snapshot_var = prompt    ← 新增（第 4 步）
  retry loop:
    agent.run(prompt) ×1-3
  finalize:
    AgentCycle insert 含全部 3 个 capture 字段（trigger_context / state_snapshot / user_prompt_snapshot）
```

**第 4 步**纯字符串引用赋值，无 IO，无新失败面。

---

## §5 错误处理 / Fail-Isolation

### §5.1 哲学对齐

P4 是 **fact-provider 不是 guard**（per `feedback_observation_period_soft_constraint`）：失败时落 NULL + log，**绝不**阻 cycle / session 主流程。

### §5.2 Session 级失败面

| 失败源 | 概率 | 处理 |
|---|---|---|
| `generate_system_prompt` raise | 极低（既然 trader.py 内部第 58 行同样调用已成功） | catch + warning + 继续 |
| `sessions` row UPDATE 失败 | 低（DB 故障极少） | catch + warning + 继续 |

`except Exception` catch-all + `logger.warning`，session 启动**不受影响**。

### §5.3 Cycle 级失败面

| 失败源 | 概率 | 处理 |
|---|---|---|
| `user_prompt_snapshot_var = prompt` | 0（字符串引用复制）| 无需 try/except |
| INSERT 失败 | 已有 handler | 整 cycle 写失败由现有 catch 处理 |

**P4 cycle 级新失败面 = 0**。prompt 拼接子函数（`_build_recent_summaries_block` / `format_for_prompt`）已有自己的 fail-isolation — 失败返回空串，prompt 仍然有效字符串。

### §5.4 NULL 行解读

| 字段 NULL | 含义 | 辨析路径 |
|---|---|---|
| `sessions.system_prompt` | (a) historical session（PR landed 前）/ (b) capture 失败（§5.2 catch-all 路径）| (b) 可通过 session log 反查 `"P4 system_prompt capture failed"` warning，与 (a) historical 区分 |
| `agent_cycles.user_prompt_snapshot` | historical cycle（PR landed 前）— **唯一来源** | — |

`sessions.system_prompt` 双源、`agent_cycles.user_prompt_snapshot` **单源**：cycle 级新失败面 = 0（§5.3），INSERT 失败 = 整 row 不写入而非"row 在但字段 NULL"。

**默认按 "capture 不可用"统一处理**；需细分 sessions 字段两源时走上述 log 反查路径。如未来想 schema 层显式细分（如 `state_captured_at` 时间戳），作 follow-up（与 `project_w2_ops_backlog` S3 同源）。

### §5.5 与 R2-7 forensic 路径的关系

R2-7 (PR #35) 把 `agent_cycles.decision` 改 `nullable=True`（upgrade 路径），让 `usage_limit_exceeded` / `retry_exhausted` 路径可写 forensic NULL。alembic downgrade 收紧回 `nullable=False`，靠**注释**提示先清理 forensic NULL 行（非 RuntimeError 守卫）；不清理则触发 SQLite IntegrityError。

**P4 不踩此雷**：两字段始终 `Text NULL`（upgrade / downgrade 不变），downgrade 直接 `drop_column` 不爆 IntegrityError，无需任何清理 / 守卫。

---

## §6 测试策略

### §6.1 测试覆盖矩阵

| 测试类型 | 测试用例 | 验证点 |
|---|---|---|
| Session 正向 | `test_session_create_captures_system_prompt` | sessions.system_prompt 内容 == `generate_system_prompt(persona, runtime)` |
| Session 反向 | `test_session_capture_failure_does_not_block_startup` | mock `get_session` raise → session 启动正常 + system_prompt=NULL + warning logged |
| Cycle happy | `test_cycle_captures_user_prompt_snapshot_happy` | 一个 cycle → user_prompt_snapshot 含 trigger 关键短语（无条件，如 "You have been woken up"）+ 若 `_fetch_recent_summaries` 非空则含 priors block 标记 + 若 memory 非 "No relevant memories." 则含 "Your memories:"。第一 cycle priors 段必空（`src/cli/app.py:355-356` 实测 _fetch 返回空 → `_build_recent_summaries_block` 返回 ""），用既有 cycle fixture 触发 priors 段才能验 |
| Cycle usage_limit | `test_cycle_captures_user_prompt_snapshot_usage_limit` | mock `agent.run` raise UsageLimitExceeded → cycle row 写入且 user_prompt_snapshot 非 NULL |
| Cycle retry_exhausted | `test_cycle_captures_user_prompt_snapshot_retry_exhausted` | mock `agent.run` 3 次 raise（任意 Exception，含未预期）→ 同上（forensic 路径关键 + 涵盖异常 catch-all）|
| Drift guard A (P4 字段) | `test_all_agentcycle_inserts_include_user_prompt_snapshot` | 静态 `ast.parse` 扫 `src/cli/app.py` 所有 `AgentCycle(` 调用，断言每个含 `user_prompt_snapshot` keyword |
| Drift guard B (retry-loop invariant) | `test_retry_loop_does_not_reassign_prompt` | 静态 `ast.parse` 扫 retry loop（`for attempt in range(3):`）body，断言不含 `prompt = ...` 形式赋值（hardens AC-10 — §6.2 详）|
| Drift guard C (max_wake helper) | `test_p4_runtime_config_matches_build_services` | runtime 测试：调 P4 capture 路径 + build_services 同输入下 `wake_max_minutes` 一致（防两处公式漂移）|
| Alembic 双向 | `test_alembic_p4_upgrade_downgrade` | upgrade → 字段存在 + nullable；downgrade → 字段不存在；幂等 |
| Integration smoke | 现有 sim integration test 增强 | end-to-end 一个 cycle 后 query DB 验证两字段非 NULL |

### §6.2 Drift Guard 形态

P4 引入 3 个 drift guard，全部用 `ast.parse` 静态扫 `src/cli/app.py`，同文件内（`tests/test_drift_p4_capture_paths.py`）：

**Guard A：所有 AgentCycle INSERT 路径含 user_prompt_snapshot**

**实现路径**：`ast.parse` 找 `AgentCycle(...)` 调用节点，遍历 keyword 参数。**不用 regex** —— `AgentCycle(...)` 内部嵌套调用（如 `json.dumps(trigger_context_var)` / `int((datetime.now(...) - cycle_started_at).total_seconds() * 1000)`）让 paren-balance regex 不可靠：非贪婪 `[^)]+?\)` 在第一个 `)` 处停，深嵌套 regex 也难以处理两层以上嵌套。

```python
import ast
from pathlib import Path

def test_all_agentcycle_inserts_include_user_prompt_snapshot():
    """3 个 INSERT 路径全覆盖 P4 字段 — 防止新加路径漏字段。"""
    src = Path("src/cli/app.py").read_text()
    tree = ast.parse(src)

    insert_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "AgentCycle"
    ]

    assert len(insert_calls) >= 3, (
        f"Expected ≥3 AgentCycle inserts, found {len(insert_calls)}"
    )

    for call in insert_calls:
        keyword_names = {kw.arg for kw in call.keywords}
        assert "user_prompt_snapshot" in keyword_names, (
            f"AgentCycle insert at line {call.lineno} missing "
            f"user_prompt_snapshot keyword — P4 capture incomplete. "
            f"Existing keywords: {sorted(keyword_names)}"
        )
```

**Guard B：retry loop 内 prompt 变量不可变（硬化 AC-10）**

**实现路径**：`ast.parse` 找 `for attempt in range(3):` 循环节点，遍历 body 找 `Assign` 节点（target 含 `prompt`）→ 立即报错。

```python
def test_retry_loop_does_not_reassign_prompt():
    """AC-10 invariant: retry loop 内不可重新赋值 prompt 变量
    (若未来引入 ModelRetry 类机制要改 prompt，必须同步重写 P4 capture 路径)."""
    src = Path("src/cli/app.py").read_text()
    tree = ast.parse(src)

    retry_loops = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Name)
        and node.iter.func.id == "range"
        and isinstance(node.target, ast.Name)
        and node.target.id == "attempt"
    ]

    assert len(retry_loops) >= 1, "Expected retry loop 'for attempt in range(...)' not found"

    for loop in retry_loops:
        for stmt in ast.walk(loop):
            # 三类赋值都要捕获：常规 (=) / 增强 (+=) / 类型注解 (: T =)
            targets: list[ast.expr] = []
            if isinstance(stmt, ast.Assign):
                targets.extend(stmt.targets)
            elif isinstance(stmt, ast.AugAssign):
                targets.append(stmt.target)
            elif isinstance(stmt, ast.AnnAssign):
                targets.append(stmt.target)
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "prompt":
                    raise AssertionError(
                        f"retry loop body re-assigns 'prompt' at line {stmt.lineno} "
                        f"({type(stmt).__name__}) — violates AC-10 invariant. "
                        f"P4 user_prompt_snapshot will diverge from actually-sent prompt; "
                        f"P4 capture path must be rewritten (per-attempt capture / "
                        f"attempt-level field) before this change ships."
                    )
```

**Guard C：max_wake 公式 P4 capture vs build_services 一致**

**实现路径**：runtime 调用比对，不是静态扫。两路径同输入产同输出，防 helper 漂移。

```python
def test_p4_runtime_config_matches_build_services():
    """max_wake helper 抽离后，P4 capture path 与 build_services 内部用相同公式."""
    from src.cli.app import build_services, _compute_max_wake  # helper 实施细节
    # 各 scheduler_interval_min 取样：5/15/30/60
    for interval in (5, 15, 30, 60):
        helper_value = _compute_max_wake(interval)
        # build_services 内部展开计算（fixture 模拟 result.scheduler_interval_min）
        expected = min(max(4 * interval, 60), 180)
        assert helper_value == expected, (
            f"max_wake drift at scheduler_interval_min={interval}: "
            f"helper returned {helper_value}, formula expected {expected}"
        )
```

**为什么 ast/runtime 三件套稳健**：
- Guard A 跨任意嵌套深度准确定位 `AgentCycle(...)` 调用，直接拿 `keyword.arg` 列表无需文本 substring 匹配
- Guard B 把 AC-10 文字 invariant 升级为 CI 可执行守卫——未来引入 ModelRetry 等 prompt 改写机制立即触发，比 spec 文字提醒可靠
- Guard C 防 max_wake 公式两处漂移（即使抽 helper，helper 签名变更或漏抽都会被捕获）

**为什么需要**：cycle handler 3 个 INSERT 路径分散在 ~150 行内，未来新加 4th path（如 R2-x 引入新 status）极易漏带字段；retry loop body 任何 `prompt = ...` 改写会让 snapshot 失真；max_wake 两处公式独立维护必漂移。三个 guard 各覆盖一类风险面。

### §6.3 测试规模估算

| 文件 | 新增测试数 | 行数（约） |
|---|---|---|
| `tests/test_p4_session_capture.py` | 2 | 60 |
| `tests/test_p4_cycle_capture.py` | 3 | 90 |
| `tests/test_drift_p4_capture_paths.py` | 3（Guard A/B/C） | 80 |
| `tests/test_alembic_p4.py` | 1 | 40 |
| 现有 sim integration 增强 | 1-2 | 30 |
| **小计** | **~10-11** | **~300** |

### §6.4 OOS 测试（明确不做）

- Performance 测试（capture 延迟）— 字符串赋值 + 1 次 `generate_system_prompt` 调用 ms 级，profile 显示瓶颈再加
- Retention 测试 — retention 留 follow-up（W3+ 后 sim 数量增加才有真触发）
- 反推路径测试（`_build_recent_summaries_block` + decision 反推 priors）— 那是反推工具的事，不是 P4 capture 的事

---

## §7 实施清单 + 估算

### §7.1 文件改动清单

| 文件 | 类型 | 改动 | 行数（约） |
|---|---|---|---|
| `alembic/versions/<new>.py` | 新增 | upgrade/downgrade 各 add/drop 2 column | 30 |
| `src/storage/models.py` | 改 | `Session` + `AgentCycle` 各加 1 字段 | 6 |
| `src/cli/app.py` | 改 | session 启动 capture 1 段 + max_wake helper `_compute_max_wake` 抽离 + 3 个 INSERT 路径加字段 | 35 |
| `tests/test_p4_session_capture.py` | 新增 | 2 测试 | 60 |
| `tests/test_p4_cycle_capture.py` | 新增 | 3 测试 | 90 |
| `tests/test_drift_p4_capture_paths.py` | 新增 | 3 测试（Guard A/B/C，全 ast/runtime 方案）| 80 |
| `tests/test_alembic_p4.py` | 新增 | 1 测试 | 40 |
| 现有 sim integration test | 改 | 增强 1-2 个断言 | 30 |
| **合计** | — | — | **~371** |

加上 PR description / commit message 等非代码工作 → **总 PR 体量预估 ~430 行**。

### §7.2 实施顺序

1. **Schema + alembic**（task A）：models.py 加字段 + alembic migration + `test_alembic_p4`
2. **Session 级 capture**（task B）：app.py session 启动加 capture + `test_p4_session_capture`
3. **Cycle 级 capture**（task C）：app.py 3 个 INSERT 路径加字段 + `test_p4_cycle_capture`
4. **Drift guards**（task D）：`tests/test_drift_p4_capture_paths.py` 文件 — 含 Guard A `test_all_agentcycle_inserts_include_user_prompt_snapshot` + Guard B `test_retry_loop_does_not_reassign_prompt` + Guard C `test_p4_runtime_config_matches_build_services`
5. **Integration smoke**（task E）：现有 sim integration test 增强
6. **PR review + merge**

执行步骤分解 + 风险点详见 implementation plan（writing-plans skill 后续产出）。

---

## §8 与既有议题对照

| 议题 | 关系 | 处理 |
|---|---|---|
| `project_observability_roadmap_from_sim8` Phase 3 P3+P4+P10 | 本期仅做 P4，P3/P10 按 roadmap 等触发型启动 | 不冲突 |
| `project_phase2_w3_followups` F1-F6 | OOS — 与 P4 解耦 | 不冲突 |
| `project_w2_ops_backlog` S1-S5 | S3 (state_captured_at) 与 P4 NULL 行细分相关 | follow-up 候选（§5.4）|
| `project_iter4_sql_caveats` | W2 SQL 边界 — P4 user_prompt_snapshot 是文本，不引入 SQL caveat | 不冲突 |
| `project_n10_recent_decisions_context_injection` (R2-8b) | P4 是 R2-8b 的"调试镜像"— 注入了什么内容，可查回 | P4 解锁 R2-8b 验证型调查 |
| `project_pydantic_ai_compliance` P1+P2 logfire | logfire 已决（PR #42 spec §4）不引入；P4 走纯 DB 路径 | 与 logfire 无关 |
| `feedback_observation_period_soft_constraint` | P4 是 fact-provider 不是 guard | §5 fail-isolated 设计对齐 |
| `feedback_brainstorm_decision_location` | brainstorm 产出落 spec 文档不动代码 | 本 spec 即此文档 |
| `feedback_plan_doc_commit_first` | spec 作独立 commit 先于代码 | 本 spec 单独 commit；后续 plan + impl 在同 branch |

---

## §9 Acceptance Criteria

PR merge 前必须全部满足：

**AC-1** Schema：`sessions.system_prompt` + `agent_cycles.user_prompt_snapshot` 两字段都已加 + alembic upgrade/downgrade 双向通过。

**AC-2** Session 级 capture：sim 启动后 `SELECT system_prompt FROM sessions WHERE id = ?` 返回非 NULL 字符串，内容等于 `generate_system_prompt(persona, runtime)` 返回值。

**AC-3** Cycle 级 capture happy path：sim 跑完一个 ok cycle 后 `SELECT user_prompt_snapshot FROM agent_cycles WHERE cycle_id = ?` 返回非 NULL，含 trigger 关键短语（如 "You have been woken up"）+ 若有 priors 含 priors block markers + 若有 memory 含 "Your memories:"。

**AC-4** Cycle 级 capture forensic path：mock `agent.run` raise（UsageLimitExceeded / 3× 任意 Exception 含未预期类型）后，对应 row `user_prompt_snapshot` 仍非 NULL，**且与 happy path 同输入下 snapshot 内容完全一致**（验证 forensic 路径不丢任何拼接段）。

**AC-5** Fail-isolation：mock session capture 写入失败（DB exception）→ session 仍启动成功 + `sessions.system_prompt = NULL` + `logger.warning` 触发。

**AC-6** Drift guard A（P4 字段）：`test_all_agentcycle_inserts_include_user_prompt_snapshot` 通过。当前 3 路径全覆盖。

**AC-6b** Drift guard B（retry-loop invariant）：`test_retry_loop_does_not_reassign_prompt` 通过 — ast 扫 `for attempt in range(3):` body 不含 `prompt = ...` 赋值（CI 硬化 AC-10）。

**AC-6c** Drift guard C（max_wake helper）：`test_p4_runtime_config_matches_build_services` 通过 — runtime 比对两路径 `wake_max_minutes` 一致。

**AC-7** 总测试通过：1471 + ~10 = ~1481 tests 全 pass + ≤ 5 skip。

**AC-8** 不动既有契约：`create_trader_agent` 签名不变；`generate_system_prompt` 签名不变；`_build_recent_summaries_block` 不变；`_capture_trigger_context` / `_capture_state_snapshot` 不变。

**AC-9** Code review：至少 1 轮 self-review + 至少 1 轮人审通过。

**AC-10** Retry-loop prompt invariant（**关键 invariant**）：retry loop 内 `prompt` 字符串变量不可变（src/cli/app.py:518 当前同一 `prompt` 三次传入 `agent.run`）—— `user_prompt_snapshot_var` 在 retry loop 之前赋值后，保证与 retry 任意 attempt 实际发送的 prompt 完全一致。**若未来引入 ModelRetry / prompt 改写机制**（per `project_iter5_observation_candidates §B`）破坏此 invariant，**P4 capture 必须同步重写**（在每次 attempt 前重新 capture / 或拆 attempt-level 字段），且本 spec 必须更新。

---

## §10 Out of Scope（明确不做）

- **Truncation 双存档**（pre-truncation priors_block_full）— 反推路径已存在；render 大改时再做
- **Tool response payload (P3)** — 独立议题，按 roadmap 等触发
- **Retry forensic (P10)** — 独立议题，按 roadmap 等触发
- **Persona versioning 表 / `system_prompt_history`** — 单 session 内（含 resume 跨多次启动期间）persona 通常不变；sessions 表是天然 anchor。若未来 W6 跨 resume 历史调查真有需求，作触发型 follow-up
- **Live 模式 retention 策略** — sim 阶段无紧迫性；实盘前评估
- **实盘前 retention 评估必含 sensitive data scan** — 当前 prompt 拼接段（src/cli/app.py:466-505）确认无 secret 注入（仅 trigger / priors / memory）。但实盘 retention 评估须含 secret-scan check（防未来某 iter 在 prompt 拼接段引入 API key / 用户私钥 / 账户敏感字段）
- **`state_captured_at` 时间戳字段** — follow-up 候选（与 S3 同源）
- **Performance 优化** — capture 是字符串赋值 + 1 次 DB UPDATE，量级足够
- **`get_session` / `format_for_prompt` 改造** — P4 只消费现有输出
- **logfire 引入** — 已决（PR #42 spec §4）
- **`max_wake` 公式抽 helper（`_compute_max_wake`）纳入本 PR**（与 §4.1 设计选择 #3 + §11 风险表 + §6.2 Guard C 三处一致）。仅"独立 commit 还是合并 commit"细节由 implementation plan 决定

---

## §11 风险与缓解

| 风险 | 概率 | 缓解 |
|---|---|---|
| Session 级 capture 失败影响 session 启动 | 极低 | catch-all + warning（§5.2）|
| Cycle 级新失败面 | 0 | 字符串赋值不 raise（§5.3）|
| 3 个 INSERT 路径漏带字段 | 中 | drift guard（§6.2，ast.parse 方案稳健跨嵌套）|
| Alembic downgrade 数据丢失 | 中 | nullable 字段，downgrade 直接 drop column；不引入 forensic NULL 阻塞 |
| W3 sim 期间字段填错 | 极低 | AC-3/AC-4 测试覆盖 + integration smoke |
| `generate_system_prompt` 重复调用引入新失败模式 | 极低 | 既然 trader.py 内部已成功调过，第二次调用 deterministic |
| Resume 路径覆写丢失历史 system_prompt | 中 | §3.1 风险有限性三点：① 单 session 内 persona/runtime 通常不变（覆盖前后值相同，无真信息丢失）；② 跨 resume 真要查历史走 git history（`generate_system_prompt` 函数版本 + persona_config 历史）反推；③ W6 跨 resume 改版需求作触发型 follow-up（当前观察期 persona placeholder 状态，需求实际为 0） |
| ModelRetry 等 prompt 改写机制破坏 retry-loop prompt invariant | 中（未来）| AC-10 固化 invariant；触发时强制更新 spec + capture 路径 |
| `max_wake` 公式两处漂移 | 低 | 本 PR 阶段抽 `_compute_max_wake` helper（per §10 OOS 已决"纳入本 PR"）+ Guard C runtime 比对（§6.2）双重防护 |
| 实盘 prompt 引入 secret 后 retention 不脱敏 | 低（当前 sim 阶段）| §10 OOS 显式标注实盘 retention 评估须含 sensitive data scan |

---

## §12 维护

- 本 spec 是 brainstorm-stage 产物，定稿后基本不再改
- 实施过程发现 spec 偏差 → 更新对应章节（不留 changelog）
- 与 Phase 2 spec / Phase 1 spec 互引：本期是 Phase 1 + 2 之后的独立子项，不并入 Phase 3 完整 scope
- W3 跑完后回头评估 P3 / P10 触发条件 — 若触发则启动各自独立 spec，**不再绑定打包 ③**
