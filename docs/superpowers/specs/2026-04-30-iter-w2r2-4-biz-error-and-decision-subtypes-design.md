# R2-4 设计 spec — biz error metrics + decision subtype derivation

**Iter**: W2 prep round 2 第四项（R2-4）
**生成于**: 2026-04-30
**议题来源**: `.working/sim4-issues-inventory.md` §P0-1 + §P0-3
**Spec status**: brainstorm 完成（6 段全 approved），待 user review，再转 writing-plans

---

## 1. 背景与动机

### 1.1 sim #4 实证暴露的两个 W2 阻塞 P0

**P0-1 — 业务失败被 metrics 完全吞掉（10.7% 失败率）**

sim #4 中 `tool_calls.status` 全部 `'ok'` (262/262)，但 session log 显示 28+ 业务失败：
- 4 次 `set_price_alert` 阈值越界（sim #4 时字符串：`Invalid threshold_pct: must be 0.5-50.0, got 0.3`）
- 24+ 次 `cancel_price_level_alert` lookup miss（sim #4 时字符串：`Alert #N not found`）

> **引述时点说明**：以上字符串是 sim #4（2026-04-30）跑跑时的现场记录。R2-1 (PR #30) 已把阈值收紧到 0.1-50.0，R2-2 (PR #31) 已把 cancel 错误信息改写为 `Alert {alert_id} already triggered or expired`。**失败路径本身仍存在**（阈值越界路径在 0.05 这种更小的值上仍触发；cancel lookup miss 路径仍触发），R2-4 instrument 仍有效；但 R2-1 收紧后 invalid_threshold_range 在 W2 期的预期触发频率显著下降（详见 §4.4 路径覆盖盲点声明）。

实际业务失败率 = 28/262 = **10.7%**（Iter 10 报告漏检）。

**根因**：`ToolCallRecorder` 只记 exception，业务级 validation 失败（参数下限 / lookup miss）的实现路径是**返回字符串**而非 raise，被当成 ok 写入。

**W2 阻塞影响**：W2 SQL 分析 error 模式时会 100% 漏掉这两类问题，命中 memory `project_observation_period_metrics_review_checklist` 的 "C 档 ≥3 例触发条件"——sim #4 单次就 28 例。

**P0-3 — derive_decision 'adjust' 三义混合**

cycle `fdf20e56`（含 `set_stop_loss + set_take_profit + add_alert + set_next_wake`）被派生为 `decision='adjust'`，但实际是**开仓后首次挂保护单**——核心交易事件。

派生逻辑无法区分 3 类语义：
1. 未开仓状态下的 `set_price_alert + add_alert`（探索类）
2. 开仓后的 `set_stop_loss + set_take_profit`（保护类 = "lock-in"）
3. 持仓中的 SL trailing（管理类）

**W2 阻塞影响**：W2 SQL 按 decision 分析时无法区分 3 种语义。

**R2-4 解决度（明示边界）**：
- ✅ 「探索类（无持仓 alert）」vs「保护类（持仓 SL/TP）」vs「挂单类」vs「杠杆类」—— 拆 4 种 `adjust_*` 子类后清晰区分（4 种 adjust 子类）
- ⚠️ 「保护首挂 vs SL trailing」仍同 enum `adjust_protect` —— **stateful 派生（看持仓 delta + 历史 SL）超出 R2-4 scope，留 W2 数据驱动议题**（详见 §5.4 末尾脚注）
- ⚠️ sim4-issues §10.7 表格列出的两例派生问题：
  - `fdf20e56`（语义模糊）→ R2-4 解决（派生 `adjust_protect`，浮现核心保护事件）
  - `cc53`（"真开仓 cycle 被归 adjust"）→ R2-4 部分进步（派生 `adjust_entry_order` ≠ 'adjust'），**完整正确（→ `open_short`）依赖 R2-7 N9 修法**（详见 §2.2 N9 责任分配）

### 1.2 R2-4 打包理由

P0-1 与 P0-3 都需 schema 演进（`tool_calls.status` 容量 + `decision_logs.decision` 容量），合并为 1 个 PR + 1 个 Alembic migration，避免二次 migration。

---

## 2. Scope（已澄清固化）

### 2.1 In scope

- **P0-1 修法**：业务失败可见机制 + tool_calls 容量扩容
- **P0-3 修法**：'adjust' 拆 4 子类 + decision_logs 容量扩容
- **Alembic migration**：仅 schema 演进，无数据 backfill
- **enum 演进时间线文档**：`docs/metrics/decision-enum-timeline.md`

### 2.2 Out of scope（明示）

- ❌ metrics §7 #1 `attempt` 列（触发条件「p95 抖动无解释 + retry 实际发生」未达成）
- ❌ metrics §7 #2 `ix_decision_logs_session_cycle` 索引（触发条件「cycle-level 关联查询 ≥ 3 次」未达成）
- ❌ metrics §7 #3 `result_preview` 字段（触发条件「决策归因仅靠 tool_name+duration 不够 ≥ 3 次」未达成）
- ❌ N9 limit-order 派生为 open_long/open_short（R2-7 处理）—— sim #4 cc53 cycle 的"真开仓被归 adjust"语义错由此覆盖，R2-4 仅把 cc53 从 `'adjust'` 推进到 `'adjust_entry_order'` 作为部分进步
- ❌ Backfill 历史 `decision='adjust'`（A 方案：不动历史数据，文档承载 audit）
- ❌ 改 `set_price_alert` / `cancel_price_level_alert` 工具返回字符串（R2-1/R2-2 已定，不再动）
- ❌ DB CHECK 约束（保持应用层 enum，与 memory `feedback_observation_period_soft_constraint` 精神对齐）
- ❌ P0-3 stateful 派生（"首挂 vs trailing" 区分）—— 留 W2 数据驱动后议题

### 2.3 Scope 决策依据

memory `feedback_observation_period_soft_constraint` 精神：fact 触发后才演进 schema。§7 三项触发条件均未达成，提前演进违反纪律。Iter 3 已建 Alembic 基线，二次 migration 真实成本不高，不构成 bundling 理由。

---

## 3. 设计决策汇总

| 决策点 | 选定方案 |
|---|---|
| **scope** | A — 仅 P0-1 + P0-3 |
| **P0-1 识别机制** | (a) ContextVar hook，工具内 `note_biz_error(error_type)` 上报 |
| **P0-3 拆分维度** | (A) 静态 action 类别拆分，派生仍 stateless |
| **新 decision enum** | `adjust_protect` / `adjust_entry_order` / `adjust_leverage` / `adjust_alert` |
| **派生优先级** | open_* > close > protect > entry_order > leverage > alert > hold |
| **历史数据策略** | A 不动 + enum 演进时间线文档 |
| **`tool_calls.status` 容量** | String(10) → String(20)（注：与 `decision_logs.status` 是不同列，本 R2-4 不动后者）|
| **`tool_calls.status` enum 取值** | `ok` / `biz_error` / `error` |
| **error_type 取值** | 常量集合 `BIZ_ERROR_TYPES` + drift guard |
| **decision 容量** | String(20) → String(30) |
| **派生兜底** | `hold` 仅在 0 ADJUST_ACTIONS |
| **drift guard** | test_t11 拆 4 子集 / test_t12 反映 String(30) / t4 rename → adjust_protect + 3 新子类派生分支 + 3 优先级用例 |

---

## 4. P0-1 设计 — 业务失败 metrics 可见

> **命名澄清**：本节涉及的 `status` 字段全部指 `tool_calls.status`（ToolCall 表）。`decision_logs.status` 是 Iter 3 引入的另一独立列（String(30), 取值 `ok` / `usage_limit_exceeded`），**R2-4 不动**。

### 4.1 数据模型改动

```python
# src/storage/models.py
class ToolCall(Base):
    status: Mapped[str] = mapped_column(String(20))  # tool_calls.status: 10 → 20
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 不变
```

`tool_calls.status` enum 取值（应用层约束，不进 DB CHECK）：
- `"ok"` — 工具成功（既无 exception 也未 note_biz_error）
- `"biz_error"` — 业务级失败（工具内 `note_biz_error()` 上报）
- `"error"` — Python exception（既有路径，不动）

### 4.2 ToolCallRecorder 改造

**真实方法名 / 签名**（`src/services/tool_call_recorder.py:58`，源码已确认）:
- 类名：`ToolCallRecorder(AbstractCapability["TradingDeps"])`
- 改造方法：**`async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler) -> Any`**（pydantic-ai capability 接口约定）
- 现有职责：args JSON 序列化（`call.args_as_dict()` + reasoning strip + 4000 char cap）、cycle_id / db_engine null check、外层 `try/except → logger.error` 兜底

> **伪代码 caveat**：以下伪代码描述设计意图（ContextVar reset / status 优先级 / fail-soft）。实际签名 / 序列化 / 截断 / null check / 兜底 logger 由 plan 阶段对齐源码精确化，不在 spec 重复。

**新增模块级 ContextVar + 上报函数**：

```python
_biz_error_type: ContextVar[str | None] = ContextVar(
    "tool_call_biz_error_type", default=None
)

BIZ_ERROR_TYPES: frozenset[str] = frozenset({
    "invalid_threshold_range",        # set_price_alert 阈值越界
    "invalid_alert_id_format",        # cancel_price_level_alert 协议错（非 8-char hex）
    "alert_not_found",                # cancel_price_level_alert 状态错（已触发/不存在）
})

def note_biz_error(error_type: str) -> None:
    """在工具内调用以标记本次 tool call 为业务失败。

    LLM 看到的返回字符串不变（fact 透明）；ToolCallRecorder 在 handler
    返回后读取此 ContextVar，写入 tool_calls.status='biz_error',
    error_type=<type>。

    拼错保护策略：fail-soft（运行期不抛）。
    - 拼错 → logger.error("note_biz_error called with unknown type: %r", error_type)
    - ContextVar 不被 set，本次 tool call 仍记 status='ok'
    - drift guard 测试期 strict 检查（test_biz_error_types_drift_guard）
    - 设计意图：开发期 drift 应该测试期暴露；运行期 hard-fail 风险
      高于 silent-skip-with-log（拼错会让 wrap_tool_execute 的
      `except Exception` 把它转成 status='error', error_type='ValueError'，
      然后 raise 让 pydantic-ai 收到异常 → agent 看不到工具本应返回的字符串
      → agent 行为被污染）
    """
    if error_type not in BIZ_ERROR_TYPES:
        logger.error("note_biz_error called with unknown type: %r", error_type)
        return
    _biz_error_type.set(error_type)
```

**`wrap_tool_execute` 改造（核心逻辑）**：

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
    token = _biz_error_type.set(None)  # reset per-call (隔离嵌套 / 异步任务)
    status, error_type = "ok", None
    skip_record = False
    try:
        result = await handler(args)
    except _CONTROL_FLOW_EXCEPTIONS:
        skip_record = True
        raise
    except Exception as e:
        status, error_type = "error", type(e).__name__
        raise
    else:
        biz = _biz_error_type.get()
        if biz is not None:
            status, error_type = "biz_error", biz
    finally:
        _biz_error_type.reset(token)
        if not skip_record:
            # ... 既有 args 序列化 / cycle_id check / session.add / logger.error 兜底 ...
            session.add(ToolCall(status=status, error_type=error_type, ...))
    return result
```

**关键点**：
- `token = ContextVar.set(None) ... reset(token)` 保证嵌套调用 / 异步任务隔离（pydantic-ai 是 async）
- `note_biz_error("xxx")` 拼错时 **fail-soft**（logger.error + 不 set ContextVar）→ 运行期不污染 agent；drift guard 测试期 strict 检查 → 开发期暴露漏洞
- exception 优先级 > biz_error — 工具同时 note 又抛异常时记 'error'（exception 是更严重信号）

### 4.3 工具内 instrumentation 范围

仅 instrument sim #4 暴露的已知失败路径（YAGNI；未来新增按需扩 BIZ_ERROR_TYPES）：

| 工具 | 文件:行（约）| 失败路径 | 上报 |
|---|---|---|---|
| `set_price_alert` | `src/agent/tools_execution.py` ~L207 | 阈值越界（R2-1 收紧后 0.1-50） | `note_biz_error("invalid_threshold_range")` |
| `cancel_price_level_alert` | `src/agent/tools_execution.py` ~L268 | id 非 8-char hex（R2-2 协议错路径）| `note_biz_error("invalid_alert_id_format")` |
| `cancel_price_level_alert` | `src/agent/tools_execution.py` ~L275 | id 合法但 alert 不存在（R2-2 状态错）| `note_biz_error("alert_not_found")` |

R2-1 / R2-2 已经把这两个工具的失败路径"分类清晰"，R2-4 在此基础上加 metrics 上报，与之前修法天然契合。

### 4.4 不在 scope 的工具 + 路径覆盖盲点声明

#### 4.4.1 已确认无字符串返回业务失败路径的工具
- `get_market_data` / `get_higher_timeframe_view` 等 perception 工具：当前无业务级 validation 失败。**不动**。

#### 4.4.2 异常驱动路径（已被 status='error' 承接）
- `place_limit_order` / `set_stop_loss` / `set_take_profit` 等执行类：exchange API 错误抛 exception，已被 `status='error'` 路径承接。**不动**。

#### 4.4.3 ⚠️ 路径覆盖盲点（明示）

**`tools_execution.py` 实际有 19 处字符串返回业务失败路径**（grep 实测 + multi-line return 手核，2026-04-30 main 含 R2-1/R2-2/R2-3）：

| 行号 | 工具 | 字符串失败路径 |
|---|---|---|
| L73 | open_position | "A market order is already pending..." |
| L79 | open_position | "Trade rejected by human approval." |
| L102 | close_position | "No positions to close." |
| L106 | close_position | "A close order is already pending..." |
| L112 | close_position | "Close rejected by human approval." |
| L135 | set_stop_loss | "No open position to set stop loss on." |
| L165 | set_take_profit | "No open position to set take profit on." |
| L210 | set_price_alert | "Alerts are disabled for this session..." |
| **L214** | **set_price_alert** | **"Invalid threshold_pct: ..." ✅ R2-4 instrument (`invalid_threshold_range`)** |
| L216 | set_price_alert | "Invalid window_minutes: ..." |
| L239 | add_price_level_alert | "Invalid direction: ..." |
| L243 | add_price_level_alert | "Price level alert limit reached (max 20)..." |
| **L272** | **cancel_price_level_alert** | **"Invalid alert_id format: ..." ✅ R2-4 instrument (`invalid_alert_id_format`)** — multi-line return |
| **L284** | **cancel_price_level_alert** | **"Alert ... already triggered or expired" ✅ R2-4 instrument (`alert_not_found`)** |
| L294 | set_next_wake | "Dynamic wake not available" |
| L318 | place_limit_order | "side must be 'long' or 'short'"（pydantic Literal 通常已上层拦截，防御性兜底）|
| L338 | place_limit_order | "Limit order rejected by human approval." |
| L368 | cancel_order | "Order not found or already filled: ..." |
| L371 | cancel_order | "Cannot cancel market orders" |

R2-4 仅 instrument 其中 **3 个路径**（sim #4 实证暴露的高频项 + R2-2 协议错路径），**不是穷举**——表中 ✅ 标注的 3 行与 §4.3 instrument 表一一对应。

**对 W2 SQL 分析的真实影响**：R2-4 落地后 `tool_calls.status='biz_error'` 仅捕获其中 3 类。**其余 16 路径（19 - 3）仍以 status='ok' 写入**。读 W2 SQL 错误模式分析时，"status='ok'" 不等同于"工具完全成功"——必须配合 trade_actions 是否写入、agent 后续行为等综合判断。

**为什么不一次到位 instrument 全部？**
1. `feedback_observation_period_soft_constraint` 精神：fact 触发后才演进。本次 instrument 是 **sim #4 实证驱动 minimal set**，未实证发生过的路径暂不 instrument 避免设计假设
2. PR 体量控制：16 项 instrument = 16 个 BIZ_ERROR_TYPES 常量 + 16 个端到端测试，PR 复杂度非线性上升

**Drift guard G1 的实际能力边界**：扫"已写出的 `note_biz_error("...")` 调用"⊆ BIZ_ERROR_TYPES。**无法**强制"漏写 note_biz_error 的字符串返回路径"——这是机制本质局限，不是测试缺陷。

**未来新增工具 / 路径**：通过 BIZ_ERROR_TYPES + drift guard 仅约束**已注册**的拼错；漏写 note_biz_error 调用的新增字符串返回路径仍依赖人工 review + W2 数据驱动后扩。

#### 4.4.4 R2-4 follow-up 议题（落 memory）

- **候选议题：广义 biz_error 路径覆盖**：W2 期间收集到非 R2-4 instrument 的高频 biz error 实证后，独立 PR 扩 BIZ_ERROR_TYPES + instrument 对应路径
- 触发条件：W2 24-48h 跑完后，session log grep 字符串返回失败统计 ≥ 5 例 / 同类
- 落地形态：sweep PR，每类 1 个 BIZ_ERROR_TYPES 常量 + 1 处 note_biz_error + 1 个端到端测试

### 4.5 风险与缓解

| 风险 | 缓解 |
|---|---|
| 工具开发者忘记 note_biz_error（漏写）| drift guard 测试 + memory feedback 记录纪律；§4.4.3 明示当前 16 路径未 instrument 是"实证驱动"非穷举 |
| 工具开发者**拼错** error_type 字符串 | **fail-soft**：运行期 logger.error + 跳过 ContextVar set（status 仍 'ok'）；drift guard 测试期 strict 抓 — 开发期失败优于运行期污染 agent |
| ContextVar 跨任务泄漏 | `set` / `reset(token)` 严格配对；`wrap_tool_execute` 入口 reset |
| **ContextVar `asyncio.gather` 子 task 隔离** | Python ContextVar 行为：`asyncio.create_task()` 默认 copy 父 context，但子 task 内 `set` 不会回流父 task。**当前所有工具都是直接调用 `note_biz_error`，无 spawn 子 task，无问题**。未来工具开发者若引入 `asyncio.gather` 模式，需在子 task 内"汇总到父 task"自行处理。**plan 阶段在 `note_biz_error` docstring 加 caveat 提示**：「必须在工具协程主体内调用，不要在 asyncio.gather 子 task 内调」 |
| BIZ_ERROR_TYPES 集合演化 | 集合本身有 drift guard 测试（扫工具源码内 `note_biz_error("...")` 字面引用 ⊆ 集合）|
| 现有测试 mock ToolCallRecorder | 已有 `test_records_failed_tool_call` 等不冲突 |

---

## 5. P0-3 设计 — decision 'adjust' 拆分

### 5.1 字段职责分工（理论锚点）

`decision_logs.decision` 与 `trade_actions` 表的职责分工：

| 表 | 职责 | 信息粒度 |
|---|---|---|
| `trade_actions` | fact-of-record（动作流水）| 每 action 一行，全保留 |
| `decision_logs.decision` | **降维标签**（cycle 主导决策）| 每 cycle 一个 enum 值 |

`decision_logs.decision` 字段当前 0 生产读取路径（grep 全 src 树），唯一未来读者是观察期 SQL 分析者（人工临时查询），目标是「按主导决策类型快速 pivot」。

让 decision 字段保留多值（数组 / 主从 / 位掩码）= **打破 decision_logs 的"降维"职责** = 与 trade_actions 表语义重复 = schema 设计退步。

「不丢信息」需求 100% 由 trade_actions 满足 → cycle_id JOIN 永远是兜底路径。trade_actions 的 fact-of-record 地位决定了 decision 字段单值的合理性。

### 5.2 数据模型改动

```python
# src/storage/models.py
class DecisionLog(Base):
    decision: Mapped[str] = mapped_column(String(30))  # 20 → 30
```

`decision` enum 取值汇总：

| 类型 | 值 | 来源 / 时间窗口 |
|---|---|---|
| 开仓 | `open_long` / `open_short` | Iter 4 引入 |
| 平仓 | `close` | Iter 4 引入 |
| **保护类调整** | `adjust_protect` | **R2-4 新增** |
| **挂单类调整** | `adjust_entry_order` | **R2-4 新增** |
| **杠杆调整** | `adjust_leverage` | **R2-4 新增** |
| **告警类调整** | `adjust_alert` | **R2-4 新增** |
| 持仓 / 无变化 | `hold` | Iter 4 引入 |
| Fallback | `derive_error` | Iter 4 引入（DB 故障）|
| Legacy | `legacy` | Iter 3 之前历史数据 |
| **遗留 adjust** | `adjust` | Iter 4 ~ R2-4 之间不再写入；DB 中保留（A 方案）|

最长 `adjust_entry_order`（18 char），`String(30)` 余量 12。

### 5.3 派生函数改造（`src/cli/app.py`）

> **伪代码 caveat**（与 §4.2 对称）：以下伪代码描述设计意图（拆 4 子集 / 优先级 / stateless）。实际实现保留现有 `app.py:94-98` 的 `logger.warning('open_position with unexpected side=...')` 兜底诊断日志（plan 阶段对齐 — 不要因为伪代码省略就丢掉运维信号）。

```python
# 顶层常量（替换原 ADJUST_ACTIONS 单一定义）
PROTECT_ACTIONS     = frozenset({"set_stop_loss", "set_take_profit"})
ENTRY_ORDER_ACTIONS = frozenset({"place_limit_order", "cancel_order"})
LEVERAGE_ACTIONS    = frozenset({"adjust_leverage"})
ALERT_ACTIONS       = frozenset({
    "set_price_alert",
    "add_price_level_alert",
    "cancel_price_level_alert",
})

# 兜底 union — 用于 drift guard / 任何"任意 adjust"判断
ADJUST_ACTIONS = (
    PROTECT_ACTIONS | ENTRY_ORDER_ACTIONS | LEVERAGE_ACTIONS | ALERT_ACTIONS
)

# 派生函数
async def _derive_decision_from_actions(
    session: AsyncSession,
    session_id: str,
    cycle_id: str,
) -> str:
    """派生顺序（高 → 低）:
    open_long > open_short > close
    > adjust_protect > adjust_entry_order > adjust_leverage > adjust_alert
    > hold

    返回 9 类 enum: open_long / open_short / close / adjust_protect /
    adjust_entry_order / adjust_leverage / adjust_alert / hold / derive_error
    """
    try:
        rows = (await session.execute(
            select(TradeAction).where(
                TradeAction.session_id == session_id,
                TradeAction.cycle_id == cycle_id,
            ).order_by(TradeAction.id)
        )).scalars().all()
    except (SQLAlchemyError, OSError):
        return "derive_error"

    actions = {a.action for a in rows}

    # 1. 开仓（最高优先级）
    for a in rows:
        if a.action == "open_position":
            if a.side not in ("long", "short"):
                continue
            return f"open_{a.side}"

    # 2. 平仓
    if "close_position" in actions:
        return "close"

    # 3. adjust 子类（按事件重要性排序）
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

**关键点**：
- 派生仍 **stateless**（仅 `trade_actions` JOIN，无跨表）
- 单 cycle 内多类 action 并存时取最高优先级（事件重要性主导）
- `set_next_wake` 不在任何 \*_ACTIONS 集合 → 自然落 hold（与现状 §C5 决议一致）
- `ADJUST_ACTIONS` 兜底 union 保留 — 为 drift guard 测试提供"任意 adjust"概念

**关于优先级排序的设计假设**：
`protect > entry_order > leverage > alert` 是基于业务直觉的默认排序——sim #4 实证只直接验证了 protect + alert 共存场景（`fdf20e56`），其他组合（如 entry_order + leverage 共存）频率未知。此排序是 placeholder default，**trade_actions 永远留底完整动作流水**——若 W2 数据反证某种排序不合实际，后续 PR 仅需重派生历史 `decision_logs.decision`（无需 schema 演进）。这与 memory `feedback_observation_period_soft_constraint` 精神契合：fact 不动，metric 派生可演进。

### 5.4 派生优先级矩阵（验证 sim #4 实证）

| cycle 内容 | 旧派生 | 新派生 | 备注 |
|---|---|---|---|
| set_stop_loss + set_take_profit + add_alert（sim #4 `fdf20e56`）| `adjust` | **`adjust_protect`** | ✅ 核心交易事件浮现 |
| 仅 add_price_level_alert（探索期）| `adjust` | `adjust_alert` | 与 protect 拉开语义距离 |
| place_limit_order + add_alert（挂单+监控）| `adjust` | `adjust_entry_order` | entry_order > alert |
| 仅 set_stop_loss（trailing）| `adjust` | `adjust_protect` | 与首挂同 enum，靠 SQL window 区分（首挂 vs trailing 留 W2 后议题） |
| 仅 set_next_wake | `hold` | `hold` | 不变 |
| 0 actions | `hold` | `hold` | 不变 |

### 5.5 历史数据策略（A：不动）

R2-4 落地后 DB 中已有的 `decision='adjust'` 行（Iter 4 PR #29 之后 W1 + sim #1-#4 累计）保留原值不动。

**理由**：
1. **W2 主分析路径不受影响**：W2 是新 session，SQL 自然带 `WHERE session_id = '<w2_id>'`，旧 session 的 'adjust' 物理隔离
2. **跨 session 对比 tax 中等不阻塞**：跨观察期对比时加 `decision LIKE 'adjust%' OR decision = 'adjust'` 一个 OR 条件
3. **不动旧数据是项目一贯做法**：Iter 3 PR #28 的 backfill 109 行为 'legacy' 是**首次 schema 演进**的破坏性 backfill（必要：旧数据无 enum 字段需补值），方向与 R2-4 相反不构成先例。Iter 4 引入 `'derive_error'` 是 runtime DB 故障 fallback enum，与历史 'legacy' 是不同维度——本来就不该 backfill，类比强度弱。**R2-4 不 backfill 'adjust' 行的真正论据是 §5.5 第 1/2/4/5 条**（W2 主分析路径不影响 + 跨 session tax 中等 + 机器可读 audit 零成本 + 派生 stateless 永远可重派生），第 3 条仅作辅助。
4. **机器可读 audit 零成本保留**：DB 行原值是历史 metrics 输出的 ground truth，不被 backfill 抹除
5. **派生函数 stateless**：trade_actions 留底完整，任何时候可重派生（不需要 DB 行也能恢复历史）

### 5.6 enum 演进时间线文档

新增 `docs/metrics/decision-enum-timeline.md`，承载 audit + SQL 兼容性指导责任：

```markdown
# Decision Enum 演进时间线

## 当前可见取值（截至 R2-4，2026-04-30）

| Enum 值 | 引入时间 | 引入 PR | 仍在写入？ |
|---|---|---|---|
| legacy             | Iter 3      | PR #28 | 否（仅历史 backfill） |
| open_long / open_short / close / hold | Iter 4 | PR #29 | 是 |
| derive_error       | Iter 4      | PR #29 | 是（DB 故障 fallback） |
| adjust             | Iter 4      | PR #29 | 否（R2-4 起停写） |
| adjust_protect / adjust_entry_order / adjust_leverage / adjust_alert | R2-4 | PR #TBD | 是 |

## 单值 decision 与 trade_actions 下钻

decision_logs.decision 是「cycle 主导决策标签」，按优先级（protect >
entry_order > leverage > alert）取最高一类。多类 adjust 共存时低优先级
类别**不在此字段反映**，但 trade_actions 表保留 cycle 内全部动作。

### 何时 GROUP BY decision（粗粒度）
- cycle 模式分布、主导决策频率分析

### 何时 JOIN trade_actions（细粒度）
- 想看「cycle 内同时有 PROTECT 和 ALERT 的占比」
- 想看「首挂 SL/TP vs trailing」（结合 cycle 时序）

例：cycle 内 PROTECT + ALERT 共存频率
SELECT COUNT(DISTINCT cycle_id) FROM trade_actions
WHERE cycle_id IN (
    SELECT cycle_id FROM trade_actions
    WHERE action IN ('set_stop_loss','set_take_profit')
  ) AND action IN ('set_price_alert','add_price_level_alert','cancel_price_level_alert');

## SQL 兼容性提示

跨观察期分析时（W2 vs W1/sim #4）若按 decision 细分：
- W1 / sim #4 旧数据中 adjust_* 表示为 'adjust'
- 新观察期数据使用 4 个 adjust_* 子类
- 兼容查询：`decision LIKE 'adjust%' OR decision = 'adjust'`
```

### 5.7 风险与缓解

| 风险 | 缓解 |
|---|---|
| 旧 SQL 查询中 `decision = 'adjust'` 失效 | enum 演进时间线文档显式标注；旧数据保留 'adjust' 取值不破坏旧查询匹配旧数据 |
| `adjust_entry_order` 与 R2-7 (N9) 冲突 | 已澄清：接受未来二次调整 |
| String(30) 容量在 PostgreSQL 部署上行为差异 | SQLite 测试 + Alembic 双 dialect 兼容（沿用 Iter 3 模式）|
| ADJUST_ACTIONS 改动破坏其他引用 | grep 全量引用：仅 derive_decision 一处 + 测试，无外部依赖 |

---

## 6. Alembic Migration 设计

### 6.1 文件命名

```
alembic/versions/<rev>_r2_4_decision_subtypes_and_biz_error.py
```

- **revision**：自动生成 hex
- **down_revision**：`379f62306805`（Iter 3 唯一现有 migration）
- 沿用 Iter 3 命名风格 + spec/plan 文档同 slug

### 6.2 改动内容（仅 schema 演进，无数据 backfill）

```python
"""r2_4 decision subtypes and biz error metrics.

Revision ID: <auto>
Revises: 379f62306805
Create Date: 2026-04-30 ...

R2-4 spec: docs/superpowers/specs/2026-04-30-iter-w2r2-4-biz-error-and-decision-subtypes-design.md

Schema-only migration. No data backfill — historical 'adjust' rows
preserved verbatim per A-strategy decision (see spec §5.5).
"""

def upgrade() -> None:
    # P0-1: tool_calls.status 容量扩容（注：本列与 decision_logs.status 是不同列，本 R2-4 不动后者）
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(length=10),
            type_=sa.String(length=20),
            existing_nullable=False,
        )
    # P0-3: decision_logs.decision 容量扩容（注：是 decision 列，与 decision_logs.status 不同）
    with op.batch_alter_table("decision_logs", schema=None) as batch_op:
        batch_op.alter_column(
            "decision",
            existing_type=sa.String(length=20),
            type_=sa.String(length=30),
            existing_nullable=False,
        )

def downgrade() -> None:
    # 反向：仅给开发期 rollback；生产 W2 不做 downgrade
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

**关键点**：
- `batch_alter_table` 沿用 Iter 3 模式 — SQLite 通过 table 重建实现 ALTER COLUMN，PostgreSQL 走原生 ALTER
- 仅扩容（widen），无新列、无新表、无索引变化、无 backfill
- `existing_*` 参数完整声明 — 沿用 Iter 3 习惯，避免 alembic 检测漂移

### 6.3 不在 migration 中的事项

- **数据 backfill**：A 方案不动历史 `decision='adjust'` 数据，migration 不写任何 UPDATE
- **DB CHECK 约束**：`decision_logs.decision` 与 `tool_calls.status` 取值仍是应用层 enum，不在 DB 层强制
- **索引改动**：无（既有 `ix_decision_logs_session_id_cycle_id` 在 Iter 3 已建）

### 6.4 Migration 风险与缓解

| 风险 | 缓解 |
|---|---|
| upgrade 跑到一半失败 | batch_alter_table 是事务内操作，失败回滚（Iter 3 模式）|
| 现有 sim/W1 数据库未跑过 migration | `init_db` 自动调 `alembic upgrade head`（PR #28 已建）|
| downgrade 在已有新 enum 值时失败 | spec 显式声明：W2 启动后不做 downgrade，rollback 用 DB 备份 |
| Iter 3 caveat #4 `engine.begin()` vs `engine.connect()` | 不需重新处理 — Iter 3 已修，R2-4 沿用 |
| Iter 3 caveat #5 fileConfig caplog | 不需重新处理 — Iter 3 已修，R2-4 沿用 |
| **PostgreSQL alter_column 行为差异** | **当前 W2 仍 SQLite，R2-4 沿用 Iter 3 SQLite-only 测试**。生产切 PG 时 `alter_column(String(N→M))` 行为需重测（PG batch_alter_table 走原生 ALTER COLUMN，String(10)→(20) 是 widen 一般无问题，但需事后验证）。低优先级，不阻塞 W2 启动。|

---

## 7. 测试策略

### 7.1 测试矩阵汇总

#### `tests/test_tool_call_recorder.py` — P0-1 路径

新增：

| 测试 | 验证内容 |
|---|---|
| `test_records_biz_error_when_note_biz_error_called` | 工具内 `note_biz_error("invalid_threshold_range")` → status='biz_error', error_type='invalid_threshold_range' |
| `test_biz_error_does_not_leak_across_calls` | 调用 A 中 note → 调用 B 不带 note → B status='ok' (ContextVar reset 验证) |
| `test_exception_overrides_biz_error` | 工具同时 note 又抛 ValueError → status='error', error_type='ValueError' |
| `test_note_biz_error_unknown_type_logs_and_skips` | `note_biz_error("typo_xxx")` 不抛异常；logger.error 被调用（含拼错的 type）；后续 `wrap_tool_execute` 写 `tool_calls.status='ok'`（ContextVar 未被 set） — 验证 fail-soft 行为完整（与 §4.2 设计一致）|
| `test_biz_error_types_drift_guard` | 扫 `src/agent/tools_execution.py` 内所有 `note_biz_error("...")` 调用，断言其 string literal 全部 ∈ `BIZ_ERROR_TYPES` |
| `test_control_flow_exception_skips_biz_error_recording` | 工具 note + raise ApprovalRequired → 不写库 |

保留不动：
- `test_records_successful_tool_call`
- `test_records_failed_tool_call`
- `test_control_flow_exception_not_recorded`
- `test_args_*` 系列

#### `tests/test_alert_lifecycle.py` / `tests/test_tools.py` — 工具 instrumentation

新增：
- `test_set_price_alert_invalid_threshold_records_biz_error` — 端到端：传 0.05 → 工具返回字符串 + tool_calls 行 status='biz_error'
- `test_cancel_price_level_alert_invalid_format_records_biz_error` — 传 "#1" → biz_error 'invalid_alert_id_format'
- `test_cancel_price_level_alert_not_found_records_biz_error` — 传合法 hex 但不存在 → biz_error 'alert_not_found'

#### `tests/test_derive_decision.py` — P0-3 路径

| 现有 test | 影响 |
|---|---|
| t1-t3 (`open_long` / `open_short` / `close`) | 不变 |
| **t4** (`test_t4_adjust_derives_from_set_stop_loss`) | **重命名为 `test_t4_adjust_protect_derives_from_set_stop_loss`** + 断言 `'adjust_protect'` |
| t5-t6 (zero / set_next_wake → hold) | 不变 |
| t7 (priority open beats adjust) | 调整断言 `'adjust'` → `'adjust_protect'`（代表）|
| t8 (session isolation) | 不变 |
| t8.5-t8.6 (invalid side / select failure) | 不变 |
| **t11** (drift guard) | **拆 4 个子集 drift guard**（每个 \*_ACTIONS 与代码内字面引用一致） |
| **t12** (decision column fits) | **更新为 String(30) 长度断言** |

新增：
- `test_adjust_entry_order_derives_from_place_limit_order`
- `test_adjust_leverage_derives_from_adjust_leverage_action`
- `test_adjust_alert_derives_from_set_price_alert`
- `test_priority_protect_beats_alert_when_both_present` — sim #4 `fdf20e56` 场景回归
- `test_priority_entry_order_beats_leverage_and_alert`
- `test_priority_leverage_beats_alert`

#### `tests/test_alembic_migration.py` — Migration 验证

新增：
- `test_r2_4_upgrade_widens_tool_calls_status` — `PRAGMA table_info(tool_calls)` 确认 status 列 type='VARCHAR(20)'
- `test_r2_4_upgrade_widens_decision_logs_decision` — `PRAGMA table_info(decision_logs)` 确认 decision 列 type='VARCHAR(30)'
- `test_r2_4_upgrade_preserves_historical_adjust_rows` — upgrade 前 INSERT decision='adjust' 行 → upgrade 后 SELECT 仍为 'adjust'
- `test_r2_4_upgrade_preserves_existing_indexes` — upgrade 后 `ix_decision_logs_session_id_cycle_id` 仍存在

#### 整合测试

新增：
- `test_decision_log_writes_adjust_protect_for_post_fill_protection` — 模拟 sim #4 `fdf20e56` 端到端：cycle 含 set_stop_loss + set_take_profit + add_alert → DecisionLog.decision='adjust_protect'

### 7.2 Drift Guard 完整清单

| # | Drift Guard | 防止 | 测试归属 |
|---|---|---|---|
| G1 | `BIZ_ERROR_TYPES` vs `note_biz_error("…")` 字面引用 | 拼错 / 漏注册新类型 | new — `tests/test_tool_call_recorder.py::test_biz_error_types_drift_guard`（属 §7.3 P0-1 +9 计数）|
| G2 | `PROTECT_ACTIONS` vs trade_actions 字面 action 名 | trade_actions 写入 / 派生 mismatched | new — t11 拆 4 子集第 1 项（属 §7.3 drift guard +5 中的 +3 拆出）|
| G3 | `ENTRY_ORDER_ACTIONS` 同上 | 同上 | new — t11 拆 4 子集第 2 项（同上）|
| G4 | `LEVERAGE_ACTIONS` 同上 | 同上 | new — t11 拆 4 子集第 3 项（同上）|
| G5 | `ALERT_ACTIONS` 同上 | 同上 | extend — 原 t11 (`test_t11_adjust_actions_drift_guard`) 改造保留为 ADJUST_ACTIONS union 兜底 + 新增上述 G2-G4 拆分（净增 +3） |
| G6 | `decision` 派生输出 ⊆ String(30) | enum 容量 drift | extend — t12 (`test_t12_derive_output_fits_decision_column`) 更新断言长度 String(20)→String(30)（无 collected 增量）|
| G7 | `tool_calls.status` 取值 ⊆ {ok, biz_error, error} | status 容量 drift（间接验 String(20) 够用）| new — `tests/test_tool_call_recorder.py::test_tool_calls_status_values_fit_column`（属 §7.3 drift guard +5 的最后 1 项）|

**归属汇总**：
- G1（BIZ_ERROR_TYPES）算在 §7.3 P0-1 +9 计数里（与 6 项 recorder 测试归一类）
- G2-G5（4 个 ACTIONS）算在 §7.3 drift guard +5 中的 +3 净增（t11 原 1 项保留为 union 兜底，新增 3 个子集 drift）
- G6（容量）= t12 改造，无 collected 增量
- G7（status enum 取值）算在 §7.3 drift guard +5 的最后 1 项 + 间接验 G6 同思路（status 间接验，即 +5 = 3 + 2 = 拆 ACTIONS 3 + status 1 + status drift 兼 t12 1）

**精确数字（统一表述）**：drift guard +5 = G2/G3/G4 拆出 ACTIONS 子集 +3 / G7 status enum 取值 +1 / G6 t12 容量精确化 +1。（§7.3 算式 3+1+1=5 与此处一致）

### 7.3 测试规模预估

- baseline (当前 main，R2-3 已 landed) = **940 collected (937 pass + 3 skip)** — `pytest --collect-only` 实测确认
- biz_error 路径：+6 (recorder, 含 G1 BIZ_ERROR_TYPES drift) + 3 (alert lifecycle / tools) = +9
- decision 派生：+6（新增 3 子类 + 3 优先级用例）
- drift guard：+5（G2-G5 拆出 4 ACTIONS 子集 +3 / G7 status enum 取值 +1 / G6 t12 容量精确化 +1）
- migration 验证：+4（upgrade 容量 + 历史数据 + 索引保留）
- 整合 e2e：+1（adjust_protect 端到端）
- 现有更新：~3 项 rename / 调整断言（不增加 collected 数量）
- **总计**：940 → ~965 collected (+25 净增)

### 7.4 不做的测试（YAGNI）

- 不验证 PostgreSQL dialect 行为（沿用 Iter 3 sqlite-only 测试惯例）
- 不验证 Alembic upgrade/downgrade 双向（沿用 Iter 3 单向 upgrade 验证）
- 不写 sim #4 数据回放测试（数据驱动验证由 R2-9 smoke 接管）
- 不验证旧 `decision='adjust'` 行被读取时的兼容性（保留即可，无外部依赖路径会因新 enum 出错）

---

## 8. 实施序与 PR 流程

### 8.1 Branch & PR

- **branch**：`feature/iter-w2r2-4-biz-error-and-decision-subtypes`
- **PR title**：`feat(iter-w2r2-4): biz error metrics + decision subtype derivation`

### 8.2 Commit 拆分（按 memory `feedback_plan_doc_commit_first`）

```
T0  docs(iter-w2r2-4): add design spec
T0' docs(iter-w2r2-4): add implementation plan          ← writing-plans 阶段产出
T1  feat(iter-w2r2-4): alembic migration — widen status & decision columns
T2  feat(iter-w2r2-4): P0-1 ContextVar hook + BIZ_ERROR_TYPES + Recorder
T3  feat(iter-w2r2-4): P0-1 instrument set_price_alert + cancel_price_level_alert
T4  feat(iter-w2r2-4): P0-3 split ADJUST_ACTIONS into 4 subsets + derive priority
T5  feat(iter-w2r2-4): P0-3 update existing tests (t4 rename / t7 / t11 split / t12 capacity)
T6  docs(iter-w2r2-4): add decision-enum-timeline.md
T7  test(iter-w2r2-4): integration regression — sim #4 fdf20e56 scenario
```

**拆分原则**：
- T0 / T0' 文档先于代码（memory `feedback_plan_doc_commit_first`）
- T1 schema 先于派生函数 / Recorder（依赖顺序）
- P0-1 (T2-T3) 与 P0-3 (T4-T5) 之间无依赖，但都依赖 T1
- T6 文档与代码独立，最后落
- T7 整合验证 sim #4 实证场景

### 8.3 与既定纪律的对齐

| 纪律来源 | 对齐方式 |
|---|---|
| `feedback_git_branch` | feature 分支不直 main |
| `feedback_plan_doc_commit_first` | T0 / T0' spec/plan 文档独立 commit 早于代码 |
| `feedback_review_before_commit` | spec 写完用户审阅 / plan 写完用户审阅 |
| `feedback_brainstorm_decision_location` | brainstorm 产出落 `.working/` + `docs/superpowers/specs/`，不动 source code |
| `feedback_observation_period_soft_constraint` | 不加 schema CHECK 约束、不改 LLM 看到的字符串、note_biz_error 是隐式 side-channel 不影响 fact 流 |

### 8.4 Baseline regression check

每个 T 完成后跑一次 `pytest`，目标：
- baseline (当前 main，R2-3 已 landed) = **940 collected, 937 pass + 3 skip**（`pytest --collect-only` 实测）
- target (R2-4 后) = ~965 collected (+25 净增), 962 pass + 3 skip

逐 commit 验证 + 末段全量回归。同 R2-1/R2-2/R2-3 模式。

### 8.5 Implementation 守则（writing-plans 阶段细化）

1. **TDD rigid**：每个 T 都先写测试看红，再实现看绿
2. **不动 LLM 看到的工具返回字符串**：sim4-issues §P1-2 / §P1-1 已经确定的字符串保持原样
3. **不加 DB CHECK 约束**：应用层 enum 即可
4. **drift guard 与代码同 commit 落地**
5. **每个 commit message 引用 spec 章节**：方便 review 时回溯设计来源
6. **PR 号交叉验证**：plan 阶段以 `git log --oneline | grep -E "(Iter [3-5]|PR #)"` 交叉确认 §5.6 enum 演进表中 "Iter 3 = PR #28 / Iter 4 = PR #29" 引用准确（spec 当前基于 memory `project_pre_observation_iterations`，plan 阶段以 git 真实历史为准）

### 8.6 R2-9 验证信号

R2-4 落地后的下次 smoke test (R2-9 重跑) 应观察：
- `tool_calls.status` 中出现 `'biz_error'` 行 — 验证机制工作
- `decision_logs.decision` 中出现 `'adjust_protect'` / 其他 subtype 行 — 验证派生工作
- 无任何 `String(20)` / `String(30)` 截断错误日志

R2-4 自身**不需要**重跑 smoke — R2-9 是 W2 启动前总验证。

---

## 9. 关联 memory / 文档

- `.working/sim4-issues-inventory.md §P0-1` / `§P0-3` — 议题来源 + 实证数据
- `.working/all-pending-needs.md` — Tier 1 / Tier 2 全景（确认 §7 #1/#2/#3 触发条件）
- `project_observation_period_metrics_review_checklist` — §7 三项候选议题清单
- `feedback_observation_period_soft_constraint` — fact 不动 agent 行为；schema 演进数据驱动
- `feedback_plan_doc_commit_first` — plan/spec 文档独立 commit 先于代码
- `feedback_review_before_commit` — 重要产出物先用户审阅
- `feedback_brainstorm_decision_location` — brainstorm 产出落文档不动 source
- `project_w2_prep_progress` — round 2 R2-x 执行序
- Iter 3 (PR #28) — Alembic 基线 + caveat #4/#5
- Iter 4 (PR #29) — derive_decision 落地 + 'legacy' 不 backfill 先例

---

## 10. Open Questions（无）

brainstorm 6 段全 approved，无待决议题。

---

## 11. Spec 自检结果

- ✅ Placeholder：无 TBD / TODO 阻塞项（`<rev>` 由 alembic 自动生成；§5.6 enum 演进表格中 "PR #TBD" 是合法的"未来 PR 编号占位"，落 PR 时填实）
- ✅ 内部一致性：派生优先级 / enum 取值 / 容量扩容三处描述一致；测试规模 +25 与 §7.3 各项相加一致 (9+6+5+4+1=25)
- ✅ Scope：单 PR 可实施，commit 数 8 项可控
- ✅ Ambiguity：派生函数行为通过示例矩阵（§5.4）显式化
- ✅ 命名澄清：所有 `status` 引用全量限定为 `tool_calls.status` 或 `decision_logs.status`，与 Iter 3 引入的 `decision_logs.status: String(30)` 列明示区分
- ✅ R2-4 解决度边界：§1.1 末尾明示「4 种 adjust 子类区分 ✅ / 首挂 vs trailing 区分 ⚠️ 留 W2 数据驱动」；cc53 责任分配 §1.1 + §2.2 显式声明 → R2-7 N9 修法
- ✅ 优先级排序设计假设承认：§5.3 末尾标注「placeholder default + W2 数据驱动可演进」
- ✅ note_biz_error 拼错保护：fail-soft 运行期 logger.error + drift guard 测试期 strict（§4.2 / §4.5）

### 11.1 接受 review report 的处理记录

#### Round 1 (10 项)

- 🔴 #1 baseline 940：reject（事实）+ §7.3 / §8.4 表述精准化为「当前 main (R2-3 已 landed)」
- 🔴 #2 拼错副作用：accept，§4.2 改 fail-soft + drift guard 期 strict / §4.5 风险表加行
- 🔴 #3 P0-3 解决度：accept，§1.1 末尾加边界声明
- 🔴 #4 cc53 缺失：accept（部分）— §1.1 / §2.2 加责任分配
- 🟠 #5 status 命名：accept，全量限定 `tool_calls.status`
- 🟠 #6 Iter 4 先例措辞：accept，§5.5 ③ 改写
- 🟠 #7 优先级数据支撑：accept，§5.3 加 placeholder caveat
- 🟡 #8 drift guard 归属：accept，§7.2 表加 G1-G7 编号 + 归属 + 数字汇总
- 🟡 #9 PR 号交叉：partial reject（memory 已确认）+ §8.5 加 plan 阶段 git log 交叉验证条目
- 🟡 #10 方法名 paraphrase：accept，§4.2 改 `wrap_tool_execute` + 真实签名 + 伪代码 caveat

#### Round 2 (1 项)

- 🔴 fail-soft 设计与 §7.1 测试矩阵冲突：accept，`test_note_biz_error_unknown_type_raises` → `test_note_biz_error_unknown_type_logs_and_skips` + 验证内容改为 fail-soft 行为完整

#### Round 3 (8 项)

- ⚠️ 引述时点过期（threshold + alert not found）：accept，§1.1 加注「sim #4 现场字符串；R2-1/R2-2 已收紧但路径仍存在」
- 🔴 B1 BIZ_ERROR_TYPES 仅覆盖 3 路径 vs 实际 17+ 字符串返回失败路径：accept (a) 路径，§4.4 重写为 4.4.1/4.4.2/4.4.3/4.4.4 — 明示 R2-4 是 sim #4 实证驱动 minimal set 非穷举 + drift guard G1 能力边界 + W2 follow-up 候选议题
- 🟠 O1 §5.3 派生伪代码缺 caveat：accept，§5.3 加伪代码 caveat（保留 logger.warning + continue 兜底）
- 🟠 O2 ContextVar asyncio.gather 子 task 隔离：accept，§4.5 风险表加行 + plan 阶段 docstring caveat 指引
- 🟡 Y1 §7.2 / §7.3 计数措辞不一：accept，§7.2 末尾统一为 3+1+1
- 🟡 Y2 PostgreSQL migration 风险未明示：accept，§6.4 风险表加 PG alter_column 重测行
- 🟡 Y3 Iter 4 'derive_error' 类比强度弱：accept（部分），§5.5 ③ 改写为"不动旧数据是项目一贯做法"，明示 Iter 4 类比是辅助，主要论据是 §5.5 第 1/2/4/5 条
- ✅ 一、事实校验全部正确 — 9 项核查表无问题

#### Round 4 (1 项)

- 🟡 §4.4.3 表格遗漏 L318：accept，补 L318 `place_limit_order side 校验` + 表格行数从 17 → 18 + 重复段落清理 + 后续计数 14+ → 15 (= 18 - 3)

#### Round 5 (2 项)

- ❌ §4.4.3 表格漏 L272 `invalid_alert_id_format` (multi-line return)：accept，补 L272 ✅ 标注（与 §4.3 一致 3 个 instrument 路径），表头 18 → 19，删除矛盾的"R2-2 协议错在 §4.3 列出"补救注释，连锁 15→16 / 14+→16
- ❌ §4.5 风险表残留旧数字 14+：accept，更新为 16
