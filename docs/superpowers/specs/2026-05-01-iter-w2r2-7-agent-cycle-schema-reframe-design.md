# Iter W2 Round 2-7 — Agent Cycle Schema Reframe (设计 spec)

**议题代号**: `iter-w2r2-7-agent-cycle-schema-reframe`
**Branch**: `feature/iter-w2r2-7-agent-cycle-schema-reframe`
**前置依赖**: R2-1 ~ R2-6 全部 landed (PR #30/#31/#32/#33/#34 + R2-6 chore)
**后续联动**: R2-8 (P1-7 展示 MVP A 路径 + N10 reasoning 注入), R2-9 (Iter 10 重跑 smoke 验证 W2 启动)
**N9 limit-order 派生盲区**: wontfix - by design（理由见 §5.2）

---

## §1. 议题背景与决议 framing

### 1.1 议题演进

原 R2-7 议题在 `.working/sim4-issues-inventory.md §P0-4 / N9` 定位为 **limit-order 派生盲区**：sim #4 实测中 agent 100% 走 `place_limit_order` 路径，被 R2-4 派生为 `adjust_entry_order` 而非 `open_<side>`，W2 SQL `WHERE decision='open_*'` 漏 100% 开仓 cycle。

brainstorm 演进过程（2026-05-01）层层上溯：

1. **派生函数视角**: 修 limit-order open heuristic 让派生函数加 `place_limit_order` 无 cancel → `open_<side>` 分支
2. **decision 字段语义**: decision 字段是机器派生 enum 还是 agent 主观输出？
3. **DecisionLog 表职责**: 表名暗示"决策日志"但承载混合元数据 + 决策 + 执行状态，循环依赖 + 命名歧义
4. **reasoning 字段语义**: reasoning 当前存 `result.output` (message) 不是 thinking content，与字段名背离
5. **state at decision**: 需要决策时刻系统客观状态作为"决策合理性"评估的 anchor

最终决议：**整套 schema reframe** — 表名重构 + 字段重命名 + 字段语义重构 + 删除 R2-4 派生路线 + 新增 state_snapshot。

### 1.2 决议 framing

**表的承载职责**: 一行 = 一个 cycle 的"前因后果"完整 case study。

5 维度叙事链：

```
前因 (triggered_by)
  → 触发上下文 (trigger_context)
  → 决策时现状 (state_snapshot)
  → agent 推理 (reasoning = thinking content)
  → agent 决策 (decision = message content)
```

每行可独立读，事后分析者无需 cross-reference 即可重建：怎么醒的 → 醒来 trigger 携带了什么 → 当时世界长啥样 → agent 怎么想 → agent 怎么决策。

### 1.3 R2-4 派生 enum 路线 deprecation

R2-4 PR #33 投入大量基础设施（派生函数 + 4 子集常量 + DERIVE_DECISION_VALUES SoT + AST drift guard + 25 测试）但实战使用 **0 次**（`docs/metrics/decision-enum-timeline.md` 自承"0 production reader, 唯一未来读者是观察期 SQL 分析者"）。R2-7 决议彻底废弃此路线，理由详见 §5。

### 1.4 W2 baseline 纪律对齐

R2-7 是**纯存储路径 + schema 改造**，不动 prompt / 不动 agent 行为 / 不动 LLM output_type。与 memory `project_w2_prep_progress` "不在观察期内做行为改造" 纪律一致。

---

## §2. 当前状态盘点

### 2.1 当前 schema 字段（src/storage/models.py:77-95）

| 字段 | 类型 | 实际承载 | 是"决策"吗 |
|---|---|---|---|
| id | int PK | 标识 | — |
| session_id | str(36) FK | 标识 | — |
| cycle_id | str(50) | 标识 | — |
| trigger_type | str(20) | cycle 触发原因 | ✗ 输入信号 |
| market_summary | Text \| None | DEPRECATED（从未真实写入）| ✗ 历史遗留 |
| decision | str(30) | R2-4 派生 enum 标签 | ✗ 机器派生 |
| status | str(30) default="ok" | 执行状态 | ✗ 框架结果 |
| reasoning | Text \| None | `result.output` 全文 cap 4000 | ✓ agent 输出 |
| model_used | str(100) \| None | LLM 模型 ID | ✗ 资源元数据 |
| tokens_used | int default=0 | LLM token 计数 | ✗ 资源元数据 |
| created_at | datetime | 时间 | — |

**结论**：表里大多数字段不是"决策"。`DecisionLog` 表名误导 + `decision` 字段同名循环依赖。

### 2.2 R2-4 派生 enum 实战使用 audit

实证调查（2026-05-01 grep 全 codebase）：

| 维度 | 数据 |
|---|---|
| `src/` 生产代码读 decision 字段 | **0 处** |
| `scripts/` 脚本读 decision 字段 | **0 处** |
| `tests/` 中 decision 字段引用 | 仅写入正确性断言（不是 pivot 分析）|
| W1 观察期实际 `decision` 字段 distinct count | **1（全 'completed' 硬编码）**|
| sim #4 期间 GROUP BY decision 实战 | **0 次**（仅暴露派生 bug 不做分布分析）|

**结论**：SQL pivot 能力（GROUP BY decision）= **prospective 价值**，**0 demonstrated value**。

### 2.3 reasoning 字段命名 vs 实际背离

`src/cli/app.py:366` `reasoning=result.output[:4000]` —— 存的是 LLM 最终对外文本（TextPart），**不是 thinking content**。

pydantic-ai 1.78 已原生支持 `ThinkingPart`（`messages.py:1513`），DeepSeek v4-pro 已 enable thinking（`model_manager.py:42`），但**当前 codebase 完全未消费 ThinkingPart** — `cli/app.py:318-352` 遍历 `result.new_messages()` 只 isinstance ToolCallPart/ToolReturnPart，thinking content 100% 丢失（仅 token 数被记入 `reasoning_tokens`）。

`reasoning` 字段命名与实际承载严重背离。

---

## §3. 设计哲学

### 3.1 五维度叙事链

```
┌──────────┐    ┌──────────────────┐    ┌────────────────┐    ┌─────────────┐    ┌─────────────┐
│ 前因      │ → │ 触发上下文        │ → │ 决策时现状      │ → │ agent 推理   │ → │ agent 决策   │
│ triggered_by│  │ trigger_context  │   │ state_snapshot │    │ reasoning   │    │ decision    │
└──────────┘    └──────────────────┘    └────────────────┘    └─────────────┘    └─────────────┘
```

每一段是事后复盘的一个独立 anchor：

| 段 | 答的问题 | 类型 | 来源 |
|---|---|---|---|
| 前因 | 怎么醒的 | 客观 | scheduler / fill / alert event |
| 触发上下文 | 醒来 trigger 携带了什么 | 客观 | trigger handler 已知 metadata |
| 决策时现状 | 醒来时世界长啥样 | 客观 | cycle handler 主动 fetch 系统状态 |
| agent 推理 | agent 怎么想 | 主观 | LLM ThinkingPart |
| agent 决策 | agent 决策什么 | 主观 | LLM TextPart (`result.output`) |

### 3.2 字段职责分簇

5 簇分类（混合表，不强行拆表）：

| 簇 | 字段 | 职责 |
|---|---|---|
| **Identity** | id, session_id, cycle_id, created_at | 标识 |
| **前因 + 触发上下文** | triggered_by, trigger_context | 决策的输入语境 |
| **决策时现状** | state_snapshot | 系统层面客观快照 |
| **决策产出** | reasoning, decision | agent 主观输出 |
| **执行元数据** | execution_status, model_id, tokens_consumed | 辅助 metadata |

### 3.3 客观 vs 主观分离

| 字段 | 客观/主观 | 说明 |
|---|---|---|
| trigger_context | 客观 | trigger handler 写入的 detail，不依赖 agent |
| state_snapshot | 客观 | cycle handler 主动 fetch，不依赖 agent 调工具 |
| reasoning | 主观 | agent thinking 过程 |
| decision | 主观 | agent 最终输出 |

**关键洞察**：客观快照与主观输出**职责互斥**，事后复盘可对照"agent 看到/想到了什么 vs 实际是什么"，发现 agent 盲区或扭曲信息。

### 3.4 与 R2-4 哲学的对照

| R2-4 timeline 哲学 | R2-7 决议 |
|---|---|
| decision 是"主导决策标签" enum 派生 | ✗ 废弃。decision 改保 message |
| 降维单值（拒绝多值）| ✗ 废弃。message 是自由文本，pivot 用 LIKE |
| 派生 stateless（trade_actions 留底可重派）| ✗ 派生路线整套删除 |
| 0 production reader / pivot-only | ✓ 保留（0 reader 仍然成立）|
| trade_actions 留底完整动作流水 | ✓ 保留（不动 trade_actions）|

---

## §4. Schema Reframe 详细

### 4.1 表名重命名

```
decision_logs → agent_cycles
```

类名：`DecisionLog → AgentCycle`

### 4.2 字段重命名 + 类型变更

| 旧字段 | 新字段 | 类型变更 | 内容变更 |
|---|---|---|---|
| `id` | 不变 | — | — |
| `session_id` | 不变 | — | — |
| `cycle_id` | 不变 | — | — |
| `trigger_type` | `triggered_by` | str(20) 不变 | 不变 (scheduled/conditional/alert) |
| `market_summary` | `trigger_context` | Text \| None 不变 | **语义重构**：触发瞬间客观快照（JSON）|
| `decision` | 保留名 | **String(30) → Text + nullable** | **语义重构**：从派生 enum 改保 `result.output` (message) |
| `status` | `execution_status` | str(30) 不变 | 不变 (ok / usage_limit_exceeded) |
| `reasoning` | 保留名 | Text \| None 不变 | **语义重构**：从 message 改保 thinking content |
| `model_used` | `model_id` | str(100) \| None 不变 | 不变 |
| `tokens_used` | `tokens_consumed` | int default=0 不变 | 不变 |
| `created_at` | 不变 | — | — |
| **新加** `state_snapshot` | (新) | Text \| None | 决策时系统客观快照（JSON）|

### 4.3 trigger_context JSON schema

trigger context 来源对照（field schema 严格对照 ground truth dataclass）：

| trigger 类型 | 数据源类 | trigger_context 内容 |
|---|---|---|
| `scheduled` | (无 context) | 1 字段：`type` |
| `conditional` | `FillEvent` (`base.py:269-281`, 11 字段) | **12 字段**（11 dataclass + 1 合成 `type`）：`type`/`trigger_reason`/`symbol`/`side`/`position_side`/`amount`/`fill_price`/`fee`/`pnl`/`order_id`/`timestamp`/`is_full_close` |
| `alert` (price level) | `PriceLevelAlertInfo` (`base.py:284-291`, 6 字段) | **7 字段**（6 dataclass + 1 合成 `type`）：`type`/`symbol`/`current_price`/`target_price`/`direction`/`reasoning`/`timestamp` |
| `alert` (percentage) | `AlertInfo` (`src/services/price_alert.py:9-15`, 6 字段) | **7 字段**（6 dataclass + 1 合成 `type`）：`type`/`symbol`/`current_price`/`reference_price`/`change_pct`/`window_minutes`/`timestamp` |

**字段保留原则（P1-1 / P1-2 决议）**: trigger context dataclass 的全部字段保留 — 这些都是 trigger handler 已生成的 metadata，序列化成本边际接近 0（每行 +50-200 bytes JSON）。alert 两路径都含 `timestamp` 用于 alert→cycle delay forensic 分析；fill 路径的 `fee`/`position_side`/`is_full_close` 是 PnL 分析 / 状态变化解读的关键。

**E4 校准记录**: 之前 spec 误用 `PercentageAlertInfo` + `previous_price`；ground truth 是 `AlertInfo` + `reference_price`，并含 `window_minutes` + `timestamp` 上下文字段。

写入时点：`run_agent_cycle` 内 trigger 解析阶段（与当前 prompt 拼接同步）。

**D2 设计意图**: prompt 拼接路径不动（`cli/app.py:225-238` 现有 prompt 文本拼接保留），`trigger_context` 是同源 detail 的 DB 端镜像，由独立 helper `_capture_trigger_context()` 维护。两条路径都从 FillEvent / AlertInfo / PriceLevelAlertInfo 同源读取，但服务不同的 consumer（agent prompt vs 事后分析者）。

### 4.4 state_snapshot JSON schema (detail 版)

字段全部对照 BaseExchange dataclass / 内存结构 ground truth（`src/integrations/exchange/base.py`）：

```json
{
  "position": {
    "symbol": "BTC/USDT:USDT",
    "side": "short",
    "contracts": 0.265,
    "entry_price": 75350.0,
    "unrealized_pnl": 12.34,
    "leverage": 5,
    "liquidation_price": 79500.0 | null,
    "pnl_pct": 0.062 | null
  } | null,
  "balance": {
    "total_usdt": 10134.5,
    "free_usdt": 10047.3,
    "used_usdt": 87.2
  },
  "market": {
    "ticker_last": 75123.5,
    "ticker_timestamp": 1746098096000,
    "fetched_at": "2026-05-01T12:34:56Z"
  } | null,
  "pending_orders": [
    {
      "id": "ord-abc",
      "order_type": "limit" | "stop_loss" | "take_profit",
      "side": "buy" | "sell",
      "price": 75550.0,
      "trigger_price": null,
      "amount": 0.013,
      "status": "open",
      "is_algo": false
    }
  ],
  "active_alerts": [
    {
      "id": "a3f2b8c1",
      "direction": "above" | "below",
      "price": 75600.0,
      "reasoning": "..."
    }
  ],
  "_errors": []
}
```

**字段 ground truth 对照（E1/E2/E3/E5 校准记录）**:

| snapshot 字段 | 来源 | 实际定义位置 |
|---|---|---|
| `position.symbol` ~ `liquidation_price` | `Position` dataclass | `base.py:78-87` (字段：symbol/side/contracts/entry_price/**unrealized_pnl**/leverage/liquidation_price/created_at) |
| `position.pnl_pct` | helper 计算（不在 dataclass）| 见 §6.2 计算公式 |
| `balance.total_usdt`/`free_usdt`/`used_usdt` | `Balance` dataclass | `base.py:48-52`（字段名是 `total_usdt`，不是 `equity_usdt`）|
| `market.ticker_last` | `Ticker.last` | `base.py:13-22` (含 @dataclass 装饰器, P2-6 校准) |
| `market.ticker_timestamp` | `Ticker.timestamp` (ms epoch, exchange 时钟) | `base.py:22` (Issue 3 加：用于市场数据 staleness 分析)|
| `market.fetched_at` | 本机 capture 时刻 (ISO8601 UTC) | helper 写入 `datetime.now(timezone.utc).isoformat()` (Issue 3 注：与 ticker_timestamp 不必相同 — 前者是 cycle handler 时间锚点，后者是 exchange tick 时刻，stale 数据时两者会有差) |
| `pending_orders.*` 全字段 | `Order` dataclass + R2-7 扩展 | `base.py:35-45` + §4.7 加 `trigger_price` 字段 |
| `active_alerts.*` | exchange 内存 `_price_level_alerts` list | `base.py:171` `get_price_level_alerts()` 返回 dict 列表，字段 `id/price/direction/symbol/reasoning`（**字段名是 `price`，不是 `target_price`**）|

**E1 注释**: `Position.unrealized_pnl` 已是 USDT 单位（不是百分比）。snapshot 直接存 `unrealized_pnl`；额外计算 `pnl_pct = unrealized_pnl / (entry_price * contracts) * 100` 作为衍生便利字段，ticker 缺失时 pnl_pct 仍可计算（不依赖 ticker.last）。

**E5 注释**: `get_price_level_alerts()` 无参数返回**所有 symbol** 的 alerts，helper 内自行 filter `if a["symbol"] == deps.symbol`。

字段语义边界（写入 spec 显式说明）：

> `state_snapshot` 承载系统层面客观事实（持仓 / 余额 / pending orders / active alerts / ticker.last 决策瞬间锚点），**不承载**计算/衍生数据（技术指标）或第三方数据源（资讯 / 宏观 / 链上）。后者是 perception 工具职责，agent 视角的 forensic 由独立议题"C 档字段"（tool_calls.args_json + return_content）解决，与本 spec 解耦。
>
> 该边界由 3 个理由支撑：
> 1. 职责分工避免 schema 重复；
> 2. 第三方 API 限流（AV/SoSoValue/DefiLlama/CoinGecko 等 quota 不允许每 cycle 全套拉取）；
> 3. W2 baseline 纪律——观察期需求驱动加 forensic capability，不防御性 over-engineering。

写入时点：`run_agent_cycle` 内 cycle_id 生成之后、`agent.run()` 之前。

### 4.5 字段 nullable 性变更

| 字段 | R2-4 nullable | R2-7 nullable | 理由 |
|---|---|---|---|
| `decision` | NOT NULL | **改为 nullable** | forensic 路径 (UsageLimitExceeded) 时 agent 无 message 输出 → NULL |
| `reasoning` | nullable 保留 | 保留 | 非 thinking model 时 NULL；forensic 路径 NULL |
| 其他 | 不变 | 不变 | — |

### 4.6 索引

保留 R2-4 的：`Index("ix_decision_logs_session_id_cycle_id", "session_id", "cycle_id")`。

R2-7 后改名为 `ix_agent_cycles_session_id_cycle_id`（rename 跟随表名）。

### 4.7 BaseExchange.Order dataclass 扩展（E3 (a) 决议）

**问题背景**: `state_snapshot.pending_orders` 需要记 SL/TP/conditional limit 类订单的触发阈值，但当前 `Order` dataclass（`base.py:35-45`）**无 `trigger_price` 字段** — 该字段仅存在于 SQLAlchemy `SimOrder` 模型 + Simulated 内部 dataclass，未透出到 BaseExchange API。

**决议（E3 (a)）**: 扩展 `BaseExchange.Order` 加 `trigger_price: float | None = None` 字段；OKX/Simulated 两端 `_parse_order` / 转换路径填充。

#### Schema 变更

```python
# src/integrations/exchange/base.py:35-45 (R2-7 之后)
@dataclass
class Order:
    id: str
    symbol: str
    side: str
    order_type: str
    amount: float
    price: float | None
    status: str
    fee: float | None = None
    is_algo: bool = False
    trigger_price: float | None = None   # ← R2-7 新加（默认 None 兼容现有 callsite）
```

#### 实现端填充

| 实现 | 位置 | 填充逻辑 |
|---|---|---|
| **OKX** `_parse_order` | `src/integrations/exchange/okx.py` (Iter 2b 已有 algo 归一化) | algo orders（SL/TP/OCO）从 `info.stopLossPrice` / `info.takeProfitPrice` 读；plain orders → None |
| **Simulated** | `src/integrations/exchange/simulated.py` `_make_*_order` 转换路径 | 直接读 `SimOrder.trigger_price` 字段（已存在）映射到 `Order.trigger_price` |

#### 与 Iter 2b 的连续性

Iter 2b 加 `is_algo: bool = False`（PR #23）建立了 BaseExchange.Order 演进先例 — 默认值兼容现有 callsite，新字段无需逐个调用点改。R2-7 的 `trigger_price` 同模式延续。

#### 影响面

| 文件 | 改动 |
|---|---|
| `src/integrations/exchange/base.py` | Order dataclass +1 字段 |
| `src/integrations/exchange/okx.py` | `_parse_order` 填充逻辑 |
| `src/integrations/exchange/simulated.py` | `_make_*_order` 转换 |
| `tests/test_exchange.py` 等 Order 构造 fixture | 加 trigger_price 默认 None（dataclass 默认值兼容，必要测试 fixture 可显式传）|
| `tests/test_okx_algo_normalization.py` | 加 trigger_price 填充验证（OCO / SL / TP 三种 algo type）|

预估改动：~30-50 行（+5 测试）。

---

## §5. R2-4 派生 enum 路线 Deprecation

### 5.1 删除清单

#### 整文件删除
- `tests/test_derive_decision.py`（27 测试 + AST drift guard）
- `tests/test_decision_log_e2e.py`（e2e 派生测试）
- `docs/metrics/decision-enum-timeline.md`

#### Source code 删除（src/cli/app.py）

| 行号 | 内容 |
|---|---|
| 51-76 | 5 ACTIONS frozenset 常量（PROTECT_ACTIONS / ENTRY_ORDER_ACTIONS / LEVERAGE_ACTIONS / ALERT_ACTIONS / ADJUST_ACTIONS）|
| 78-85 | `DERIVE_DECISION_VALUES` SoT |
| 88-149 | `_derive_decision_from_actions` async helper（60 行）|
| 84/116/118 | `derive_error` 字面量（随派生函数）|
| ~54 (注释行) | "trade_actions 留底，未来若数据反证可仅重派生历史 decision_logs.decision，无需 schema 演进" — 派生函数已删 + 表已 rename，整段注释失效（L 校准 v6） |

#### Stale 注释清理（顺手）

| 文件:行 | 现状 | R2-7 处理 |
|---|---|---|
| `tests/test_okx_websocket.py:208` | "FillEvent.order_id 必须用 algoId, 否则与 decision_logs.order_id (= algoId from..." — `decision_logs` 没有 order_id 字段（实际是 `trade_actions.order_id`），R2-7 后表名也改 | 更新为 `trade_actions.order_id`（修 stale 命名指代）|

### 5.2 N9 议题处置：wontfix - by design

`project_n9_derive_decision_limit_order_blindspot` 议题在 R2-7 后**不存在**：派生路线整套删除，不再有"派生 bug"。

记入 inventory：`.working/sim4-issues-inventory.md §P0-4` 标 `✅ wontfix - by design (R2-7, 2026-05-01)`。

### 5.3 R2-4 工作 sunk cost

PR #33 (squash `75bb11c`, 2026-04-30) 一周前 landed 的整套派生基础设施作废。诚实承认 sunk cost — 接受 R2-4 是"prospective over-engineering"的判断。

但 R2-4 中并非全部工作浪费，**保留部分**：
- Alembic migration 文件 `<rev>_r2_4_decision_subtypes_and_biz_error.py` — 历史 chain 不能删
- ContextVar `_biz_error_type` + `note_biz_error()` 侧通道（biz error metrics）— 与 R2-4 P0-1 解耦于 P0-3，**保留**
- `tool_calls.status` widen 10→20 — 保留（独立于 decision 字段）

**M4 解耦佐证**: `note_biz_error` 的写入终点是 `tool_call_recorder.py` 内 `wrap_tool_execute` 读 `_biz_error_type` ContextVar 后写 `tool_calls.status`（`src/services/tool_call_recorder.py:53/83/118`），与 `decision_logs` 写路径**完全无关**。所有当前消费者（`src/agent/tools_execution.py:216/275/288` 三处 `note_biz_error()` 调用）均位于 ToolCallRecorder 链路，删除 decision 派生函数不影响 biz error metrics。

### 5.4 历史 enum 值的兼容

历史 970 行 `decision_logs` 数据中 `decision` 字段是 enum 短串：
- W1 期：'completed'（硬编码）
- Iter 3 backfill：'legacy'
- Iter 4 起：'open_long' / 'open_short' / 'close' / 'adjust' / 'hold'
- Iter 4 故障 fallback：'derive_error'
- R2-4 起：'adjust_protect' / 'adjust_entry_order' / 'adjust_leverage' / 'adjust_alert'

R2-7 后 `decision` 类型改 Text + nullable，旧 enum 短串**仍可读**（Text 容纳）。但**新数据是 message 长文本**，与旧数据 SQL **不可比**：

```sql
WHERE decision LIKE 'open_%'
-- 旧数据: 命中 'open_long' / 'open_short' enum 短串
-- 新数据: 可能命中 message 中含 "I plan to open short..." 等自然语句
-- → 语义不同，统计无意义
```

此为**已知断层**，文档化为 `docs/metrics/agent-cycles-schema.md` timeline 段。

---

## §6. 写入路径改造

### 6.1 trigger_context 写入

新增 helper `_capture_trigger_context(cycle_id, trigger_type, context) -> dict | None`（建议位 `src/services/cycle_capture.py`），按 trigger 类型生成 JSON。**整个 helper 包裹 try/except**，与 `_capture_state_snapshot` 同纪律 — best-effort 容错，异常 → return None，cycle 继续：

```python
# src/services/cycle_capture.py
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.integrations.exchange.base import FillEvent, PriceLevelAlertInfo
from src.services.price_alert import AlertInfo

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)


def _capture_trigger_context(cycle_id: str, trigger_type: str, context) -> dict | None:
    """Capture trigger metadata for DB. Best-effort: any exception → None (cycle continues).

    Args:
        cycle_id: 当前 cycle_id (用于日志反查 — F 修复)
        trigger_type: scheduled / conditional / alert
        context: trigger 携带的 metadata (FillEvent / PriceLevelAlertInfo / AlertInfo / None)
    """
    try:
        if trigger_type == "scheduled":
            return {"type": "scheduled_tick"}
        if trigger_type == "conditional" and context is not None:
            # FillEvent (base.py:269-281): fee/position_side/timestamp/is_full_close 全字段保留 (P1-2)
            return {
                "type": "fill",
                "trigger_reason": context.trigger_reason,
                "symbol": context.symbol,
                "side": context.side,                  # ← P1-2: maker side (buy/sell)
                "position_side": context.position_side, # ← P1-2: 持仓方向 (long/short) — trigger_reason 不完全覆盖
                "amount": context.amount,
                "fill_price": context.fill_price,
                "fee": context.fee,                    # ← P1-2: 净 PnL 分析必要 (净 = pnl - fee)
                "pnl": context.pnl,
                "order_id": context.order_id,
                "timestamp": context.timestamp,        # ← P1-2: forensic delay 分析 (alert→cycle 间隔)
                "is_full_close": context.is_full_close, # ← P1-2: alert 清理逻辑解读关键 (Iter 6 4-source fusion 字段)
            }
        if trigger_type == "alert" and context is not None:
            # PriceLevelAlertInfo (base.py:284-291) — H 行号校准: 含 @dataclass 装饰器
            if isinstance(context, PriceLevelAlertInfo):
                return {
                    "type": "price_level_alert",
                    "symbol": context.symbol,
                    "current_price": context.current_price,
                    "target_price": context.target_price,
                    "direction": context.direction,
                    "reasoning": context.reasoning,
                    "timestamp": context.timestamp,    # ← P1-1: 与 percentage 路径对称，alert→cycle delay forensic
                }
            # AlertInfo (percentage alert, src/services/price_alert.py:9-15)
            if isinstance(context, AlertInfo):
                return {
                    "type": "percentage_alert",
                    "symbol": context.symbol,
                    "current_price": context.current_price,
                    "reference_price": context.reference_price,    # ← E4 校准: not "previous_price"
                    "change_pct": context.change_pct,
                    "window_minutes": context.window_minutes,
                    "timestamp": context.timestamp,
                }
        return None  # 已知 trigger_type 但 context 为 None / 类型不匹配
    except Exception as e:
        # Issue 2 容错: AttributeError / 未来 trigger 类型扩展导致属性缺失 → 不抛，DB 字段 NULL
        # F 修复: 日志附带 cycle_id 用于多 cycle 并发反查
        logger.warning(
            "trigger_context capture failed (cycle_id=%s, trigger_type=%s, context_type=%s): %s",
            cycle_id, trigger_type, type(context).__name__, e,
        )
        return None
```

**P1-1 / P1-2 字段全保留原则**: trigger context 的 forensic 价值在于"事后能完整复盘 trigger 时刻发生了什么"——所有 dataclass 字段都是 trigger handler 已生成的 metadata，序列化成本接近 0（每行 +50-200 bytes JSON），不应主动过滤。两路径补字段后对称：fill / alert 都含 `timestamp` 用于 delay forensic 分析。

**Issue 2 容错纪律**: 与 `_capture_state_snapshot` 一致 — helper 的异常**不应打断 cycle**。trigger_context 是 forensic 字段（DB 端镜像），缺失 → trigger_context=NULL + log warning，agent.run() 仍正常执行。容错触发场景：未来扩展 trigger 类型 / context 类型不符 / 属性 schema 漂移。

写入时点：cycle_id 生成后立即调用 + JSON 序列化存 `trigger_context`。

### 6.2 state_snapshot 写入

新增 helper `_capture_state_snapshot(cycle_id, deps) -> dict`（建议位于新文件 `src/services/cycle_capture.py`，与 `_capture_trigger_context` 同文件）：

```python
async def _capture_state_snapshot(cycle_id: str, deps: TradingDeps) -> dict:
    """Capture system-side objective state at decision time. Best-effort: per-field try/except.

    Args:
        cycle_id: 当前 cycle_id (用于 _errors 标记 + 日志反查 — F 修复)
        deps: TradingDeps with exchange / market_data
    """
    snapshot = {
        "position": None,
        "balance": None,
        "market": None,
        "pending_orders": [],
        "active_alerts": [],
        "_errors": [],
        "_cycle_id": cycle_id,         # F 修复: forensic 反查时 JSON 内自带 cycle_id (DB cycle_id 列冗余但便于 grep system.log)
    }

    # 1. position (best-effort) — fields per Position dataclass (base.py:78-87)
    try:
        positions = await deps.exchange.fetch_positions(deps.symbol)
        if positions:
            p = positions[0]
            # Compute pnl_pct from entry_price + unrealized_pnl + contracts (no ticker dep)
            notional = p.entry_price * p.contracts if p.entry_price > 0 and p.contracts > 0 else 0.0
            pnl_pct = (p.unrealized_pnl / notional * 100) if notional > 0 else None
            snapshot["position"] = {
                "symbol": p.symbol,
                "side": p.side,
                "contracts": p.contracts,
                "entry_price": p.entry_price,
                "unrealized_pnl": p.unrealized_pnl,    # ← E1 校准: 原 dataclass 字段, USDT 单位
                "leverage": p.leverage,
                "liquidation_price": p.liquidation_price,
                "pnl_pct": pnl_pct,                    # ← 衍生计算字段（None if entry_price 或 contracts 为 0）
            }
    except Exception as e:
        msg = f"position_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 2. balance (best-effort) — fields per Balance dataclass (base.py:48-52)
    try:
        balance = await deps.exchange.fetch_balance()
        snapshot["balance"] = {
            "total_usdt": balance.total_usdt,           # ← E2 校准: not "equity_usdt"
            "free_usdt": balance.free_usdt,
            "used_usdt": balance.used_usdt,
        }
    except Exception as e:
        msg = f"balance_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 3. market (best-effort) — fields per Ticker dataclass (base.py:13-22, 含 @dataclass)
    # Issue 3: 同时存 ticker_timestamp (exchange clock, ms epoch) + fetched_at (本机 capture 时刻)
    # 两者不必相同 — stale 数据 / 网络延迟时会有差，分析者可用差值判断 staleness
    try:
        ticker = await deps.market_data.get_ticker(deps.symbol)
        snapshot["market"] = {
            "ticker_last": ticker.last,
            "ticker_timestamp": ticker.timestamp,    # ← Issue 3 新增 (exchange 时钟 ms epoch)
            "fetched_at": datetime.now(timezone.utc).isoformat(),  # 本机 capture 时刻
        }
    except Exception as e:
        msg = f"ticker_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 4. pending orders (best-effort) — Order dataclass extended in §4.7 with trigger_price
    try:
        orders = await deps.exchange.fetch_open_orders(deps.symbol)
        snapshot["pending_orders"] = [
            {
                "id": o.id,
                "order_type": o.order_type,
                "side": o.side,
                "price": o.price,
                "trigger_price": o.trigger_price,    # ← R2-7 §4.7 新增字段
                "amount": o.amount,
                "status": o.status,
                "is_algo": o.is_algo,
            }
            for o in orders
        ]
    except Exception as e:
        msg = f"open_orders_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 5. active alerts (no IO) — get_price_level_alerts() returns list[dict] for ALL symbols
    # Issue 6: 单 symbol filter 意图 — cycle 是单 symbol 上下文 (deps.symbol fixed at session start),
    # 决策时其他 symbol 的 alert 与本 cycle 决策无关，filter 减小 JSON 体积 + 聚焦 forensic 信号。
    try:
        all_alerts = deps.exchange.get_price_level_alerts()      # ← E5 校准: method name + no params
        snapshot["active_alerts"] = [
            {
                "id": a["id"],
                "direction": a["direction"],
                "price": a["price"],                              # ← E5 校准: dict key is "price" not "target_price"
                "reasoning": a.get("reasoning", ""),
            }
            for a in all_alerts
            if a["symbol"] == deps.symbol                          # ← Issue 6: 单 symbol filter (见上方 comment)
        ]
    except Exception as e:
        msg = f"alerts_read_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    return snapshot
```

写入时点：cycle_id 生成后、agent.run() 之前。

**D1 设计意图**: snapshot capture **不依赖 agent 的 tool 调用顺序** — 即使 agent 不调 `get_position` / `get_account_balance` / `get_open_orders`，cycle handler 仍主动 fetch 写入 state_snapshot。这是发现"agent 漏看现状就决策"盲区的关键能力。事后查询：`tool_calls cycle_id JOIN` 可知 agent 调过哪些工具；`state_snapshot.position` 可知决策时实际持仓 — 两者对照发现遗漏。

**M6 IO 估算（OKX 实盘场景）**: 每 cycle state_snapshot capture 增加 **4 次 OKX REST call**：

| 操作 | 限流（OKX 默认）| state_snapshot 单 cycle 占用 |
|---|---|---|
| `fetch_positions` | 10 req/2s per UID | 1 |
| `fetch_balance` | 10 req/2s per UID | 1 |
| `fetch_ticker` | 20 req/2s per IP | 1（market_data 内 cache 大概率命中）|
| `fetch_open_orders` | 60 req/2s per UID | 1（注：OKX 实现是 3 路 gather plain+cond+oco，物理上 3 次 REST，本文统计逻辑次数）|

cycle 持续 30s-3min → 每 endpoint 实际调用率 0.05-0.4 req/s，远低于 5-30 req/s 限流阈值（**15-50× 余量**）。

存在的真实代价（非阻塞 W2，切实盘前 follow-up）：
- 与 agent 工具 fetch 重复 IO（agent 调 get_position 等会再 fetch 一次） → cycle-level cache 优化是 §12.4 follow-up 议题
- ccxt `RateLimitExceeded` 已被 `@_retry` 自动重试（src/integrations/exchange/okx.py:75）

**Simulated 模式**: position/balance/orders 全是 DB 读（无网络 IO）；ticker 是内存 dict（无 IO）；alerts 是内存列表（无 IO）→ 总计 0 网络 IO。W2 观察期主战场是 simulated 模式，**实盘 IO 风险 W2 不会触发**。

容错原则：单字段 fetch 失败 → null + `_errors` 标记；不阻塞 cycle，不引入 disable toggle，不引入连续失败检测（独立议题）。

### 6.3 reasoning 写入（thinking content）

修改 cli/app.py 内的 message 遍历逻辑（当前 line 318-352），增加 ThinkingPart 处理：

```python
thinking_parts: list[str] = []

for msg in result.new_messages():
    if isinstance(msg, ModelResponse):
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                ...
            elif isinstance(part, ThinkingPart):
                thinking_parts.append(part.content)
    elif isinstance(msg, ModelRequest):
        ...

# 写入 agent_cycles
reasoning_text = "\n\n".join(thinking_parts) if thinking_parts else None
```

非 thinking model（无 ThinkingPart）→ reasoning = NULL。

不 cap（Text 字段无长度限制；DeepSeek R1 thinking 单 cycle 5k-50k tokens ≈ 20k-200k chars 仍可接受）。

### 6.4 decision 写入（message content）

简化为：

```python
decision_text = result.output  # 不 cap, Text 字段
```

forensic 路径（UsageLimitExceeded）→ result 不存在 → decision = NULL（见 §6.5）。

### 6.5 forensic 路径（UsageLimitExceeded）写入

当前 `cli/app.py:264-284` 写：

```python
except UsageLimitExceeded as e:
    decision = await _derive_decision_from_actions(...)
    session.add(DecisionLog(
        ...
        decision=decision,
        status="usage_limit_exceeded",
        reasoning=str(e)[:4000],
        ...
    ))
```

R2-7 后改为（注：`state_snapshot` / `trigger_context` 已在 try 块**之前** capture，**此处直接复用变量**，不再重复调用 helper — 见 §6.7 写入顺序总览）：

```python
# trigger_context 与 state_snapshot 在 try 块之前已 capture（§6.7 写入顺序）
# state_snapshot_var = await _capture_state_snapshot(cycle_id, deps)              # ← 在 try 之前
# trigger_context_var = _capture_trigger_context(cycle_id, trigger_type, context) # ← 在 try 之前

try:
    result = await agent.run(...)  # 此处 raise UsageLimitExceeded
    ...
except UsageLimitExceeded as e:
    logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
    async with get_session(engine) as session:
        session.add(AgentCycle(
            session_id=deps.session_id,
            cycle_id=cycle_id,
            triggered_by=trigger_type,
            trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
            state_snapshot=json.dumps(state_snapshot_var),
            reasoning=None,                            # agent 没产出 thinking
            decision=None,                             # agent 没产出 message
            execution_status="usage_limit_exceeded",
            model_id=getattr(model, 'model_name', str(model)) if model else str(agent.model),
            tokens_consumed=0,
        ))
        await session.commit()
    return None
```

**Issue 1 校准**: 早期版本 spec 在 except 块内重复调用 `_capture_state_snapshot` / `_capture_trigger_context` —— 这与 §6.7 写入顺序总览 + AC4 + §9.5 "已在 agent.run() 之前 capture（不受 exception 影响）" 三处明确冲突，且会让实盘下多 4 次 OKX REST call（违反 §6.2 M6 IO 预算意图），且 capture 时刻已飘移（forensic 价值打折）。R2-7 v4 修复：明确 capture **在 try 块之前一次**，except 块直接复用变量。

异常详情**不存 DB 字段** → 已在 system.log（exception-level + stacktrace），事后查 log 即可。

decision/reasoning 字段保持语义纯净（要么是 agent 产出，要么 NULL）。

**M8 partial usage 注记**: `tokens_consumed=0` 沿用 Iter 5 假设（"UsageLimitExceeded 不携带 partial usage"，spec §3.1 #3）。impl 时若 pydantic-ai 1.78 的 `UsageLimitExceeded` 暴露 partial usage（例如 `e.usage.total_tokens`），应改为记入实际消耗值。pre-impl smoke 验证步骤（见 §10.5）会确认这个行为。

### 6.6 success 路径写入

```python
# trigger_context_var / state_snapshot_var 在 try 块之前已 capture（§6.7 写入顺序，与 forensic 路径同源）
# state_snapshot_var = await _capture_state_snapshot(cycle_id, deps)
# trigger_context_var = _capture_trigger_context(cycle_id, trigger_type, context)

result = await agent.run(...)
thinking_text = ...  # extract from ThinkingPart parts (见 §6.3)

async with get_session(engine) as session:
    session.add(AgentCycle(
        session_id=deps.session_id,
        cycle_id=cycle_id,
        triggered_by=trigger_type,
        trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
        state_snapshot=json.dumps(state_snapshot_var),
        reasoning=thinking_text,                   # nullable: NULL if no ThinkingPart
        decision=result.output,                    # nullable: NULL if forensic (但 success 路径 result 必有)
        execution_status="ok",
        model_id=getattr(model, 'model_name', str(model)) if model else str(agent.model),
        tokens_consumed=tokens,
    ))
    await session.commit()
```

### 6.7 写入顺序总览

```
run_agent_cycle 入口
├── cycle_id = uuid (line ~217)
├── trigger_context_var = _capture_trigger_context(cycle_id, trigger_type, context)  ← 新增 (一次)
├── state_snapshot_var = await _capture_state_snapshot(cycle_id, deps)                ← 新增 (一次)
├── prompt 拼接（保持现状，prompt 内仍含 trigger detail 给 agent）
├── try: agent.run()
│   └── extract: thinking_text from ThinkingPart, decision = result.output
│   └── INSERT AgentCycle (success path) — 复用 *_var
└── except UsageLimitExceeded:
    └── INSERT AgentCycle (forensic: reasoning=NULL, decision=NULL, status='usage_limit_exceeded') — 复用 *_var
```

**关键不变量**: 两次 capture 调用**只在 try 块之前发生一次**；success / forensic 两条路径都复用同一对 `*_var`。这保证：
1. 实盘下每 cycle 仅 +4 OKX REST call（§6.2 M6 IO 预算）
2. forensic 路径的 state_snapshot 是 trigger 时刻状态，不是 exception 时刻状态（forensic 价值最大）
3. AC4 "forensic 路径 state_snapshot 不为 NULL" 由 capture 在 try 之前完成保证（不被 exception 影响）

**P2-5 顺序无关注释**: 三步「`trigger_context_var` capture / `state_snapshot_var` capture / prompt 拼接」**互相相对顺序无关**（同源 dataclass 读取 / DB 端镜像 / agent prompt 文本，三条独立路径，D2 已说明）。spec 流程图按"先 capture 后 prompt"列只是叙事方便，impl 时实际顺序由 implementer 决定，唯一约束是**全部都在 try 块之前完成**（确保 forensic 路径能复用变量）。

---

## §7. Migration & 历史数据策略

### 7.1 Alembic migration 内容

新文件：`alembic/versions/<new_rev>_r2_7_agent_cycle_schema_reframe.py`

upgrade 操作（顺序）：

```python
def upgrade():
    # Step 1: rename table
    op.rename_table("decision_logs", "agent_cycles")

    # Step 2: batch_alter for column changes (SQLite 限制 → batch_alter)
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        # rename columns
        batch_op.alter_column("trigger_type", new_column_name="triggered_by")
        batch_op.alter_column("market_summary", new_column_name="trigger_context")
        batch_op.alter_column("status", new_column_name="execution_status")
        batch_op.alter_column("model_used", new_column_name="model_id")
        batch_op.alter_column("tokens_used", new_column_name="tokens_consumed")

        # type + nullable change for decision
        batch_op.alter_column(
            "decision",
            existing_type=sa.String(30),
            type_=sa.Text(),
            existing_nullable=False,
            nullable=True,
        )

        # add state_snapshot column
        batch_op.add_column(
            sa.Column("state_snapshot", sa.Text(), nullable=True)
        )

    # Step 3: rename index (SQLite 不支持 ALTER INDEX RENAME，用 drop + recreate)
    op.drop_index("ix_decision_logs_session_id_cycle_id", table_name="agent_cycles")
    op.create_index(
        "ix_agent_cycles_session_id_cycle_id",
        "agent_cycles",
        ["session_id", "cycle_id"],
    )
```

downgrade 操作（逆向，必须支持回滚）：

```python
def downgrade():
    # Step 3 逆向：drop + recreate index with old name
    op.drop_index("ix_agent_cycles_session_id_cycle_id", table_name="decision_logs")
    op.create_index(
        "ix_decision_logs_session_id_cycle_id",
        "decision_logs",
        ["session_id", "cycle_id"],
    )

    # Step 2 逆向：batch_alter 还原列
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.drop_column("state_snapshot")
        batch_op.alter_column(
            "decision",
            existing_type=sa.Text(),
            type_=sa.String(30),
            existing_nullable=True,
            nullable=False,  # WARNING: 若历史有 NULL 行（forensic R2-7 后写入的），逆向会失败
        )
        batch_op.alter_column("tokens_consumed", new_column_name="tokens_used")
        batch_op.alter_column("model_id", new_column_name="model_used")
        batch_op.alter_column("execution_status", new_column_name="status")
        batch_op.alter_column("trigger_context", new_column_name="market_summary")
        batch_op.alter_column("triggered_by", new_column_name="trigger_type")

    # Step 1 逆向：rename table back
    op.rename_table("agent_cycles", "decision_logs")
```

**逆向限制**：若 R2-7 后已有 forensic 路径写入（decision=NULL），逆向时 NOT NULL 约束会失败。

**Issue 4 escape hatch（紧急回滚 ops 路径）**: 若需 downgrade，先清理 NULL 行再 alembic downgrade：

```sql
-- Step 1: 删除 R2-7 后写入的 forensic NULL 行（这些行无法满足 R2-4 NOT NULL 约束）
DELETE FROM agent_cycles
WHERE execution_status = 'usage_limit_exceeded'
  AND decision IS NULL;

-- Step 2: alembic downgrade
-- $ alembic downgrade -1
```

数据损失说明：删除的 forensic 行是 R2-7 后的 UsageLimitExceeded cycle 记录（实操中应极少 — UsageLimitExceeded 在 W1 实测 0 次 / sim #4 0 次）。删除前可备份 SELECT 结果留底。

**实操中 R2-7 是单向演进，回滚预期极少**；此 escape hatch 是 ops 兜底路径。

### 7.2 历史数据策略

**不 backfill 任何字段**：
- 旧 970 行 decision 字段保留 enum 短串原值（'open_long' / 'adjust_*' / 'hold' / 'legacy' / 'completed' 等）
- 旧 reasoning 字段保留 message 内容（cap 4000）
- 旧 market_summary 字段（rename 为 trigger_context）—— 全 NULL（从未写入），rename 后仍 NULL
- 旧 state_snapshot 字段 = NULL（新加列）

**已知数据断层**（文档化为 `docs/metrics/agent-cycles-schema.md` timeline 段）：

| 时点 | decision 含义 | reasoning 含义 |
|---|---|---|
| 2026-04-08 ~ 2026-04-26 | 'completed' (硬编码) | message |
| 2026-04-26 (Iter 5) | 'usage_limit_exceeded' / 'completed' | message + str(e) |
| 2026-04-29 (Iter 4 PR #29) | enum 9 类（open_*/close/adjust/hold/derive_error/legacy）| message cap 4000 |
| 2026-04-30 (R2-4 PR #33) | enum 12 类（拆 4 子集 + 上述） | message cap 4000 |
| **2026-05-01 起 (R2-7)** | **message 自由文本** \| NULL | **thinking content** \| NULL |

**SQL 跨期分析建议**（写入 schema md）：

```sql
-- 旧数据 GROUP BY decision (W1 / sim #4 期)
SELECT decision, COUNT(*) FROM agent_cycles
WHERE created_at < '2026-05-01' GROUP BY decision;

-- 新数据 LIKE 检索 (W2 期)
SELECT cycle_id, decision FROM agent_cycles
WHERE created_at >= '2026-05-01' AND decision LIKE '%open%';
-- ⚠️ 自由文本，pivot 仅作粗筛，结果需人工 review
```

### 7.3 与 Alembic chain 完整性

R2-7 migration 在 alembic chain 中位于 R2-4 之后。实测 `alembic/versions/`：

```
379f62306805_initial_iter3_schema_evolution.py     ← Iter 3 PR #28
e7b2bd73c131_r2_4_decision_subtypes_and_biz_error_.py   ← R2-4 PR #33
<new_rev>_r2_7_agent_cycle_schema_reframe.py       ← R2-7 (this PR)
```

**M1 校准**: R2-3（system.log rotation）**没有产生 migration**（不动 DB）。chain 中只有 Iter 3 + R2-4 两个历史 migration，**绝不删除**——Alembic chain 完整性依赖。

---

## §8. Cycle 展示设计 (for P1-7 R2-8)

> **本节为 R2-7 后续 R2-8 议题（P1-7 展示 MVP A 路径 + N10 reasoning 注入）的设计接口契约**。R2-7 PR **不实施**本节内容；R2-7 PR 内 display.py **不改 param 名**（trigger_type / agent_output / tokens_used 保留），仅传入数据来源跟随 DB 字段（如 trigger_type 仍来自 run_agent_cycle 入参 string，不读 DB triggered_by 列）。R2-8 PR 实施本节全部展示设计 + 决定 param rename（D 校准 v7）。

### 8.1 cycle header（P1-7 7-1 + 7-4）

#### 当前 (sim #4 实测)
```
── Cycle 8988 (scheduled) ──
```

#### R2-8 期望
```
── Cycle 8988 [12:34:56] (scheduled, +75min after last) ──
   Position: short 0.265 BTC @75350 | PnL +0.05% (+12.3 USDT) | 5x lev
   Trigger: scheduled_tick
```

数据来源：
- 时间戳：`agent_cycles.created_at`（格式 HH:MM:SS）
- 距上 cycle 时长：上一 cycle.created_at 减本 cycle.created_at（Python timedelta）
- Position：`state_snapshot.position`（None 时显 "Flat"）
- Trigger 原因（7-5 显示）：`trigger_context.type` + 关键 detail

### 8.2 cycle 末小结 + 累计统计（P1-7 7-3）

#### 当前 (sim #4 实测)
```
⚙ get_market_data: ✓ ...
⚙ place_limit_order: ✗ Invalid threshold 0.3 (要求 ≥0.5)
⚙ set_stop_loss: ✓ ...
[Agent: I have placed a short...]
```

失败混在 ⚙ 流里无小结。

#### R2-8 期望
```
⚙ get_market_data: ✓ ...
⚙ place_limit_order: [bold red]✗ Invalid threshold 0.3 (要求 ≥0.5)[/]
⚙ set_stop_loss: ✓ ...
[Agent: I have placed a short...]
─ Cycle 8988 summary: 14 tools (✓ 13 / ✗ 1) | 12.3k tokens | session: 47 cycles, 158k tokens
```

数据来源：
- ✓/✗ count：从 cycle 内 tool_calls 表（cycle_id JOIN）+ status filter
- session 累计：聚合 `agent_cycles WHERE session_id=X` 的 tokens_consumed sum + cycle count

### 8.3 trigger context 渲染（P1-7 7-5）

按 `trigger_context.type` 分支渲染：

| type | 渲染示例 |
|---|---|
| `scheduled_tick` | `⏰ scheduled tick` |
| `fill` | `🔔 fill: short SL @75600 → -125 USDT (PnL -1.2%) [order ord-abc]` |
| `price_level_alert` | `⚠️ alert: BTC above 75600 (current 75623) — "FOMC reaction watch"` |
| `percentage_alert` | `📊 alert: BTC +2.3% in 1h (75123 → 76847)` |

数据来源：`agent_cycles.trigger_context` JSON 字段。

### 8.4 session 终结报告（P1-7 7-8）

#### R2-8 期望（shutdown 时打印 panel）
```
╔════════════════════ Session Summary ════════════════════╗
║ Session: BTC sim #5 (3fe27696...)                        ║
║ Started: 2026-05-02 18:14 UTC                            ║
║ Ended:   2026-05-04 06:23 UTC (36h 9min)                 ║
║                                                          ║
║ Cycles:        158                                       ║
║   - scheduled: 47                                        ║
║   - alert:     93                                        ║
║   - conditional: 18                                      ║
║                                                          ║
║ Tokens:        2,341k consumed (avg 14.8k/cycle)         ║
║ Errors:        3 cycles (usage_limit_exceeded)           ║
║                                                          ║
║ Trades:                                                  ║
║   - Open positions: 4 (3 short, 1 long)                  ║
║   - Closed: 4 (2 win / 2 loss)                           ║
║   - Net PnL: -47.50 USDT                                 ║
║   - Equity: 9947.50 USDT (-0.53% from start)             ║
╚══════════════════════════════════════════════════════════╝
```

数据来源 SQL（聚合查询）：

```sql
-- Cycles 总数 + trigger 分布
SELECT triggered_by, COUNT(*) FROM agent_cycles
WHERE session_id = ? GROUP BY triggered_by;

-- Tokens
SELECT SUM(tokens_consumed), COUNT(*), AVG(tokens_consumed) FROM agent_cycles
WHERE session_id = ?;

-- Errors
SELECT COUNT(*) FROM agent_cycles
WHERE session_id = ? AND execution_status != 'ok';

-- Trades 来自 trade_actions / sim_orders / sim_positions
-- (R2-7 不动这些表，复用现有 sim 数据查询)
```

### 8.5 字段消费契约（display.py 接口签名）

R2-8 期望 `display.py` 提供的渲染函数：

```python
# R2-8 期望签名（R2-7 不实施，仅 spec 接口契约 — D 校准 v6）：
# 注：R2-7 PR 内 display.py 现有 param 名 (trigger_type / agent_output / tokens_used) 全部保留，
# 仅传入数据来源跟随 DB 字段（如 trigger_type 仍来自 run_agent_cycle 入参 string，不读 DB triggered_by 列）
def format_cycle_output(
    cycle_id: str,
    triggered_by: str,                  # R2-8: was trigger_type (param rename 由 R2-8 决定)
    trigger_context_json: str | None,   # R2-8 NEW
    state_snapshot_json: str | None,    # R2-8 NEW
    cycle_started_at: datetime,         # R2-8 NEW (was implicit)
    last_cycle_at: datetime | None,     # R2-8 NEW (calculate gap)
    tool_calls: list[dict],
    agent_output: str,                  # R2-8: 渲染层标签保留 (P1-7 渲染 message 区域)
    agent_thinking: str | None,         # R2-8 NEW (来自 reasoning 字段 = thinking)
    tokens_consumed: int,               # R2-8: was tokens_used
    session_cumulative_tokens: int,     # R2-8 NEW (for footer)
    session_cumulative_cycles: int,     # R2-8 NEW
    budget_remaining: int,
) -> str:
    """Render single cycle output for session log."""

def format_session_summary(
    session_id: str,
    summary_data: dict,                 # 来自 SQL 聚合
) -> str:
    """Render session shutdown panel."""
```

### 8.6 与 N10 reasoning 注入的协同（R2-8 议题）

P1-7 7-6（reasoning 折叠 / 视觉锚点）+ N10（前 cycle reasoning 注入回 prompt）共享同一 reasoning 字段（thinking content）。R2-8 议题独立 spec，引用本 §8 接口契约。

注入策略候选（R2-8 决定）：
- 注入 thinking (reasoning)：信息密度高 + token 成本高
- 注入 message (decision)：已 compressed 结论 + token 成本低
- 注入 thinking + message 双轨：最完整 + token 成本最高

**R2-7 决议**：R2-7 不预设注入策略，留给 R2-8 N10 议题独立设计。R2-7 仅保证两个字段都被写入 → R2-8 灵活选择注入哪个。

---

## §9. 容错与失败处理

### 9.1 单字段瞬时失败（best-effort）

`_capture_state_snapshot` 内每个字段独立 try/except：失败 → null + `_errors` 数组追加 `{type}_fetch_failed: {ExceptionType}`。

`_capture_trigger_context` 整个函数 try/except 包裹（Issue 2）：失败 → return None → DB 字段写 NULL + log warning。

两 helper 共同纪律：**异常不打断 cycle**，agent.run() 仍正常执行。

### 9.2 state_snapshot / trigger_context JSON serialize 失败

**Issue C 校准 (v6 review)**: 早期版本 spec 写 "json.dumps 失败 → state_snapshot = None + log warning, cycle 继续" — 与 §9.5 "DB 写入失败可见性优先 / 不 swallow" 纪律不一致。修正为：

`json.dumps(state_snapshot_var)` / `json.dumps(trigger_context_var)` 失败按 §9.5 默认行为：**异常上抛到 cycle handler 外层**（main loop 捕获），不 swallow。

理由：
1. helper 内已确保所有字段 JSON-friendly（int/float/str/None/list/dict + ISO 字符串），json.dumps 失败说明 helper bug（写了 datetime / Decimal / 自定义对象等非可序列化值）— 是需要可见修复的 bug，不是 transient failure
2. 与 §9.5 哲学一致：DB 写入相关失败的可见性优先于 silent swallow
3. spec test 应捕获此类问题（T-SS-8 已含 `json.dumps` round-trip 验证）

实操：implementer 应在 helper 内（`_capture_*`）严格控制返回字段类型，spec test 守把住 round-trip。impl 时若意外触发，定位 helper 内的字段写法修复，不引入 silent swallow。

### 9.3 不引入 disable toggle

明确不加 setting `state_snapshot.enabled` 类 toggle。理由（详见 brainstorm 决议）：
1. disable toggle 是静默劣化温床（数据缺失但系统看似正常）
2. 让 ops 习惯性屏蔽问题而非诊断根因
3. 为不存在的场景准备复杂度（OKX 限流余量 15-50×，不可能需要长期关 state_snapshot）

### 9.4 不引入连续失败检测（独立议题）

"连续 N cycle state_snapshot 全失败 → 系统级停止" 机制不在 R2-7 范围。理由：
- 是横向 ops 议题（OKX API 全断、DB 写不进等也该触发）
- 不应为 state_snapshot 单独实现 mini 停止机制
- 留作未来统一 ops 健康监控议题

### 9.5 forensic 路径写入

UsageLimitExceeded 触发时：
- state_snapshot / trigger_context 已在 agent.run() 之前 capture（不受 exception 影响）
- decision/reasoning = NULL（agent 无产出）
- execution_status = "usage_limit_exceeded"

**P2-1 校准 — DB 写入异常处理**: forensic 路径的 `session.add` + `session.commit` **不额外加 try/except 包裹**，与当前 cli/app.py:269-283 现状一致；DB 写入失败时按 SQLAlchemy 默认行为（异常上抛到 cycle handler 外层，由 main loop 捕获）。

理由：
1. 与 R2-7 "纯存储路径 + schema 改造，不动 prompt / 不动 agent 行为" 纪律一致 — 不引入新的 error swallowing 行为
2. `_record_action` (tools_execution.py:42) 现有 best-effort `except Exception` swallow 模式是工具层职责（避免 metrics 阻塞 tool return），DB session 写入是 cycle handler 终点 — 写入失败本身就是需要可见的异常
3. forensic 路径 capture 早已完成（在 try 之前），写入失败的可见性优先于 swallow

cycle handler 外层的异常处理由 scheduler / main loop 现有逻辑负责，R2-7 不改这层。

---

## §10. 测试策略

### 10.1 删除测试（25-27 个）

| 文件 | 测试数 | 删除理由 |
|---|---|---|
| `tests/test_derive_decision.py` | 27 (t1-t12 全套 + AST drift guard) | 派生函数删除 |
| `tests/test_decision_log_e2e.py` | 1-2 | e2e 派生测试 |

### 10.2 新增测试（25-30 个）

#### Schema migration 测试 (`tests/test_alembic_migration.py` +)

| 测试 | 验证内容 |
|---|---|
| T-MIG-1 | upgrade 后表名 `agent_cycles` 存在；`decision_logs` 不存在 |
| T-MIG-2 | upgrade 后 5 列重命名生效（PRAGMA table_info）|
| T-MIG-3 | upgrade 后 decision 类型 = TEXT + nullable=1 |
| T-MIG-4 | upgrade 后 state_snapshot 列存在 + 类型 TEXT + nullable |
| T-MIG-5 | upgrade 后索引名 `ix_agent_cycles_session_id_cycle_id` |
| T-MIG-6 | 历史 decision_logs 表所有现有行 schema 兼容（旧 enum decision 短串可读，行数原样保留）|
| T-MIG-7 | downgrade 回滚成功（前提：无 forensic R2-7 行）|
| T-MIG-8 (M5) | upgrade 后 `execution_status` 列的 `server_default='ok'` 仍有效（PRAGMA table_info 查 dflt_value，验证 batch_alter rename 不丢 server_default）|

#### BaseExchange.Order 扩展测试 (E3 (a) — `tests/test_okx_algo_normalization.py` + `tests/test_exchange.py`)

| 测试 | 验证内容 |
|---|---|
| T-ORD-1 | `Order` dataclass 含 `trigger_price: float \| None = None` 字段（默认值兼容现有 callsite）|
| T-ORD-2 | OKX `_parse_order` 对 OCO（algoType=oco）order 同时填充 stopLossPrice + takeProfitPrice 中至少一个 |
| T-ORD-3 | OKX `_parse_order` 对 stop-loss algo（ordType=conditional + slTriggerPx）填充 trigger_price |
| T-ORD-4 | OKX `_parse_order` 对 take-profit algo（ordType=conditional + tpTriggerPx）填充 trigger_price |
| T-ORD-5 | OKX `_parse_order` 对 plain limit order（无 trigger）→ trigger_price=None |
| T-ORD-6 | Simulated `_make_*_order` 对 SL/TP `SimOrder.trigger_price` 映射到 `Order.trigger_price` |

#### state_snapshot 写入测试 (`tests/test_cycle_capture.py` 新文件)

| 测试 | 验证内容 |
|---|---|
| T-SS-1 | 无持仓 cycle → snapshot.position = None, balance/market 有值 |
| T-SS-2 | 有持仓 cycle → snapshot.position 完整 8 字段（symbol/side/contracts/entry_price/unrealized_pnl/leverage/liquidation_price/pnl_pct）|
| T-SS-3 | pending_orders detail 完整（含 SL/TP/limit 三类，每条 8 字段含 trigger_price）|
| T-SS-4 | active_alerts detail 完整（id/direction/price/reasoning），自行 filter symbol |
| T-SS-5 | ticker fetch 失败 → snapshot.market = None + _errors 含 "ticker_fetch_failed" |
| T-SS-6 | position fetch 失败 → snapshot.position = None + _errors 标记 |
| T-SS-7 | 全部 fetch 失败 → 所有字段 None + _errors 5 项 + cycle 不抛异常 |
| T-SS-8 | JSON serialize 后能被 json.loads 还原 |
| T-SS-9 | balance 字段名验证（`total_usdt` 而非 `equity_usdt`，E2 校准）|
| T-SS-10 | pnl_pct 计算正确性（entry_price * contracts > 0 时按公式；为 0 时 None）|

#### trigger_context 写入测试

| 测试 | 验证内容 |
|---|---|
| T-TC-1 | scheduled trigger → `{"type": "scheduled_tick"}` |
| T-TC-2 | conditional fill trigger → 含 trigger_reason / fill_price / pnl / order_id 等字段 |
| T-TC-3 | `PriceLevelAlertInfo` trigger → 含 target_price / direction / reasoning 等 |
| T-TC-4 | `AlertInfo` (percentage alert) trigger → 含 reference_price / change_pct / window_minutes / timestamp（E4 校准字段）|

#### ThinkingPart 提取测试

| 测试 | 验证内容 |
|---|---|
| T-TH-1 | thinking model（mock ThinkingPart）→ reasoning = 拼接后的 thinking text |
| T-TH-2 | 非 thinking model（无 ThinkingPart）→ reasoning = NULL |
| T-TH-3 | 多个 ThinkingPart → 用 `\n\n` 拼接 |
| T-TH-4 | thinking content 长度 > 4000 → 不截断（无 cap）|

#### success / forensic 路径写入测试 (`tests/test_usage_limits.py` 改造)

| 测试 | 验证内容 |
|---|---|
| T-WP-1 | success 路径：decision = result.output / reasoning = thinking / state_snapshot 不为 NULL |
| T-WP-2 | forensic 路径（UsageLimitExceeded）：decision=NULL / reasoning=NULL / status='usage_limit_exceeded' / state_snapshot 不为 NULL（capture 在 try 之前完成）|
| T-WP-3 | forensic 路径 trigger_context 仍写入 |

### 10.3 修改现有测试（5-6 个文件）

| 文件 | 修改内容 |
|---|---|
| `tests/test_storage.py` | DecisionLog → AgentCycle import + 字段名改 |
| `tests/test_usage_limits.py` | DecisionLog → AgentCycle + 字段名改 + 删除 `assert row.decision == "hold"` 类断言（改为新语义断言）|
| `tests/test_alembic_migration.py` | 保留 R2-4 段 + 新增 R2-7 段（表名 / 字段名 / decision Text / state_snapshot 列）|
| `tests/test_cycle_log.py` | trigger_type 参数仍是 run_agent_cycle 入参（不改），但 cycle log assertions 改 |
| `tests/test_display_cycle.py` | **R2-7 不改 display.py param 名**（D 校准 v6）— `format_cycle_output(trigger_type, agent_output, tokens_used, ...)` 三个 param 都是渲染层标签（与 `run_agent_cycle` 入参同源），不直接对应 DB 字段名。R2-7 仅改 DB 字段，不改 cycle handler / display 入参签名。这些测试**不需要改**（除非现有断言读 DB 字段名）。如果 R2-8 决定 rename param 与 DB 字段对齐，那是 R2-8 spec 范围 |

### 10.4 净测试数变化

```
删除: 25-27 (派生函数测试)
新增: 35 (实测：T-MIG 8 + T-ORD 6 + T-SS 10 + T-TC 4 + T-TH 4 + T-WP 3 = 35)
净变化: +8 ~ +10
```

预期 R2-7 后总测试数：970 → 978~980（含 E3 (a) 决议加的 Order 扩展测试）。

**P2-4 校准**: §10.2 逐项加合 = 35（精确）；浮动空间留作 plan 阶段微调（如某项拆分 / 合并），最终落点应在 33-37 区间。

### 10.5 Pre-impl smoke 验证步骤（M7 + M8）

R2-7 impl 第 1 步必须先做的外部依赖验证（在写 code 前 run）：

#### Pre-smoke 1: pydantic-ai DeepSeek ThinkingPart 实测
**目的**: 验证 pydantic-ai 1.78 + DeepSeek v4-pro 实际 message stream 含 ThinkingPart 实例（不只是 token usage 计数）。

**由谁跑（P2-3）**: **由用户跑**（涉及 DeepSeek 实盘 LLM call + API 计费 + 网络）。预计 ~10s 短跑，不属 memory `feedback_long_walltime_experiments` 的 ">10min" 范畴，但仍涉用户的 API key 与计费，由用户在 R2-7 implementer agent / Claude session 启动 implementation 之前手动跑一次，结果贴回会话记入 plan / PR description。

**做法**:
```python
# scripts/iter_w2r2_7_thinking_smoke.py（一次性 smoke，不入 CI）
import asyncio
from pydantic_ai import Agent, ModelResponse, ThinkingPart, TextPart
# ... setup DeepSeek v4-pro agent w/ thinking="high" ...
result = await agent.run("What is 2+2? Show your reasoning.")
for msg in result.new_messages():
    if isinstance(msg, ModelResponse):
        for part in msg.parts:
            print(type(part).__name__, getattr(part, "content", "")[:100])
```

**预期输出**: 至少出现一个 `ThinkingPart` 实例（content 非空）。

**若失败**: ground truth 与 spec §6.3 不一致 → 在写 §6.3 实施前先确认 pydantic-ai DeepSeek provider 实际行为，必要时调整提取逻辑（如：从 reasoning_content 字段提取而非 ThinkingPart）。

**Code 路径核对**: `pydantic_ai/models/openai.py:938-961` `_process_thinking` 处理 `reasoning_content` 字段并 emit `ThinkingPart` — 已验证支持，但仍需 smoke 跑一次确认完整路径。

#### Pre-smoke 2 (已 audit, 转参考记录): UsageLimitExceeded partial usage 行为

**结论（v3 reviewer audit, Issue 5）**: pydantic-ai 1.78 源码已确认 `UsageLimitExceeded` **不暴露 partial usage**。

**Audit 证据**:
- `pydantic_ai/exceptions.py:183`: `class UsageLimitExceeded(AgentRunError)` 的 `__init__(self, message: str)` 单参数构造（无 usage 字段）
- `pydantic_ai/usage.py:382/386/392/400/404/410/417`: 所有 7 处 raise 点均仅传 message string（E 校准 v6: 补 line 404）

**结论**: 沿用 `tokens_consumed=0`（§6.5 当前假设），无需 impl 时 smoke 实测。

**注**: 若未来 pydantic-ai 升级（>1.78）添加 partial usage 暴露，应重审此假设并改 §6.5。

---

## §11. Acceptance Criteria

| AC | 验证内容 |
|---|---|
| **AC1** | `agent_cycles` 表存在 + 含 12 列：5 rename (triggered_by/trigger_context/execution_status/model_id/tokens_consumed) + 1 decision (widen+nullable) + 1 state_snapshot (新加) + 1 reasoning (语义重构, 名不变) + 4 identity 不变 (id/session_id/cycle_id/created_at) + index rename |
| **AC2** | 历史 `decision_logs` 表所有现有行 schema-compatible 保留（迁移到 `agent_cycles`，旧字段名 → 新字段名，旧值不变；具体行数实施时实测，不预设硬编码阈值）|
| **AC3** | success 路径写入：state_snapshot detail 版（5 类含 list）+ trigger_context（3 trigger 类型）+ reasoning(thinking) + decision(message) |
| **AC4** | forensic 路径写入（UsageLimitExceeded）：reasoning=NULL / decision=NULL / status='usage_limit_exceeded' / state_snapshot 不为 NULL |
| **AC5** | thinking model（DeepSeek v4-pro）reasoning 字段 = ThinkingPart 拼接；非 thinking model reasoning = NULL |
| **AC6** | `_derive_decision_from_actions` 函数 + 5 ACTIONS 常量 + DERIVE_DECISION_VALUES + 25 测试 + decision-enum-timeline.md 完全删除 |
| **AC7** | `docs/metrics/agent-cycles-schema.md` 新建，含 schema 定义 + JSON content schema + 历史 enum timeline + §8 P1-7 展示设计接口契约 |
| **AC8** | display.py **不改 param 名**（trigger_type / agent_output / tokens_used 保留为渲染层标签）；渲染逻辑保持现状（P1-7 改造 + param rename 全留 R2-8 决定）|
| **AC9** | 现有测试（除被删 25-27 个）+ 31-36 新测试全部通过 |
| **AC10** | Alembic upgrade + downgrade 双向通过（前提：downgrade 在无 forensic R2-7 行的环境）|
| **AC11** | OKX 实盘模式下 state_snapshot 写入正常（手动 smoke test 验证 4 次逻辑 fetch — fetch_positions / fetch_balance / fetch_ticker / fetch_open_orders；与 §6.2 M6 IO 估算口径对齐：fetch_open_orders 内 3 路 gather 算 1 次逻辑 fetch）|
| **AC12** | 不引入 disable toggle，不引入连续失败检测机制 |
| **AC13** (E3) | `BaseExchange.Order` 含 `trigger_price: float \| None = None`；OKX `_parse_order` 对 SL/TP/OCO 填充；Simulated 转换路径填充 |
| **AC14** (M5) | upgrade 后 `execution_status` 列保留 `server_default='ok'`（PRAGMA dflt_value 验证）|
| **AC15** (M7+M8) | Pre-impl smoke 验证完成：ThinkingPart 在 DeepSeek 实际 emit + UsageLimitExceeded partial usage 行为已记录 |

---

## §12. Out-of-scope & Follow-up 议题

### 12.1 R2-7 不做（独立议题）

| 议题 | 归属 | 触发条件 |
|---|---|---|
| **C 档字段** (tool_calls.args_json + return_content) — agent 视角 forensic | observation-period-metrics-review-checklist 候选 | W2 期间发现"reasoning + state_snapshot 不足以评估决策质量" |
| **持续失败检测 + 系统级停止** — 统一 ops 健康监控 | 独立 ops 议题 | W2 期间出现连续 state_snapshot 失败 |
| **cycle-level cache 优化** — 消除重复 OKX API call | 切实盘前 follow-up | 切实盘后实测 IO 浪费严重 |
| **thesis 字段** — agent 自我声明决策依据 | R2-8 N10 议题 | 已规划 |
| **outcome 字段** — 决策事后 PnL outcome | 独立议题 | W2 数据驱动 |
| **persona snapshot** — 决策时生效的 persona/style | 独立议题 | 切换 persona / style 频繁时 |

### 12.2 N9 limit-order 派生盲区

**wontfix - by design (R2-7)**。理由见 §5.2。

### 12.3 P1-7 展示 MVP A 路径 + N10 reasoning 注入

不在 R2-7 实施。R2-8 议题独立 spec，引用本 §8 接口契约。

R2-8 命名建议：`iter-w2r2-8-cycle-display-mvp-and-reasoning-injection`。

### 12.4 OKX 实盘 state_snapshot 优化

切实盘前 follow-up：
- cycle-level cache（消除 agent 工具调用重复 IO）
- 配置化 ticker fetch fresh vs cached 的策略

不阻塞 W2 启动。

---

## §13. 改动量估算

| 项 | 量 |
|---|---|
| **整文件删除** | 3 个（test_derive_decision.py / test_decision_log_e2e.py / decision-enum-timeline.md）|
| **Src 修改文件** | 4 个（src/cli/app.py / src/storage/models.py / src/integrations/exchange/base.py [E3 Order +trigger_price] / src/integrations/exchange/okx.py [_parse_order]）|
| **Src 修改文件（间接）** | 1 个（src/integrations/exchange/simulated.py — `_make_*_order` 转换 trigger_price）|
| **新增 helper 文件** | 1 个（src/services/cycle_capture.py）|
| **Tests 修改文件** | 6-7 个（含 test_okx_algo_normalization.py 加 trigger_price 测试）|
| **Tests 删除测试数** | 25-27 |
| **Tests 新增测试数** | 31-36（含 +6 Order trigger_price 测试 + +1 server_default 测试 + +2 state_snapshot 校准测试）|
| **新 Alembic migration** | 1 个 |
| **新 docs/metrics 文件** | 1 个（agent-cycles-schema.md）|
| **新 smoke 脚本** | 1 个（scripts/iter_w2r2_7_thinking_smoke.py，pre-impl 一次性，不入 CI）|
| **总代码行数（净改动）** | **~450-620 行**（E3 (a) 决议加 ~30-50 行 BaseExchange.Order 扩展） |
| **PR commit 数估算** | 7-9 个（spec / pre-smoke / Order 扩字段 / migration / 删派生 / state_snapshot / trigger_context / ThinkingPart / 字段 rename / tests）|

---

## §14. Drift Guards

| Guard | 验证 | 触发场景 |
|---|---|---|
| **G1** AgentCycle field SoT | `tests/test_storage.py` 含 `EXPECTED_AGENT_CYCLE_FIELDS = {...}`，与 `inspect.getmembers(AgentCycle)` 实际字段比对 | 加新字段忘改 SoT 集合 → CI fail |
| **G2** state_snapshot JSON shape | `tests/test_cycle_capture.py::test_state_snapshot_json_shape` 用 jsonschema 或手工断言 5 类 keys 存在 | helper 函数漏字段 |
| **G3** trigger_context type 完整性 | `tests/test_cycle_capture.py::test_trigger_context_types_covered` 验证 4 种 trigger 类型分支都返回有效 dict | 新增 trigger 类型忘加分支 |
| **G4** ThinkingPart 提取 round-trip | mock thinking parts → cli/app.py 提取 → 验证内容拼接顺序 + 与原始一致 | pydantic-ai message 处理逻辑漏 ThinkingPart |
| **G5** 历史数据 schema 兼容 | `tests/test_alembic_migration.py::test_historical_decision_logs_compat` 跑 migration 后旧行可读 + 旧 enum 短串保留 | migration 误 backfill / 误 drop |
| **G6** Alembic chain 完整性 | 现有 `tests/test_alembic_migration.py` 跑完整 chain（base → R2-7）成功 | migration 顺序错乱 |
| **G7** 删除函数 + 常量无遗留引用 (M3 扩 grep) | 自动测试 `tests/test_drift_no_legacy_decision_refs.py` 跑：`grep -E "_derive_decision_from_actions\|PROTECT_ACTIONS\|ENTRY_ORDER_ACTIONS\|LEVERAGE_ACTIONS\|ALERT_ACTIONS\|ADJUST_ACTIONS\|DERIVE_DECISION_VALUES" src/ tests/` = 0 hit (排除已删除 test 文件)；**额外 PR self-check (K 加)**: implementer 在 PR description 中附 `grep -rn "DecisionLog\|decision_logs" src/ tests/ scripts/` 输出（预期：仅历史 alembic migration files + 删除清单覆盖项），与 G7 自动测试互验 | src/tests 中残留任一派生路线符号 |
| **G8** server_default 保留 (M5) | `tests/test_alembic_migration.py::test_execution_status_server_default_after_rename` PRAGMA dflt_value 验证 | batch_alter rename 丢 server_default |
| **G9** Order trigger_price (E3) | `tests/test_okx_algo_normalization.py::test_order_dataclass_has_trigger_price_field` + 各 algoType 填充验证 | 加新 algo type 忘填 trigger_price |

---

## §15. 议题归档（spec 落地后手动操作，不在代码 PR 内）

R2-7 PR merged 后，**手动**完成以下 memory / inventory 归档：

### 15.1 Memory 更新

| 文件 | 操作 |
|---|---|
| `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_n9_derive_decision_limit_order_blindspot.md` | 标 wontfix - by design (R2-7, 2026-05-01) |
| `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_w2_prep_progress.md` | R2-7 状态更新为 ✅ landed |
| `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_tradebot_status.md` | 加 PR # 行 |
| `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_iter4_sql_caveats.md` | derive_error fallback 失效，DB 健康度监控指标更新（improved: forensic 路径 status='usage_limit_exceeded' 仍可统计）|
| `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_observation_period_metrics_review_checklist.md` | C 档字段独立议题 + state_snapshot 已加，metrics 演进 candidate 调整 |

### 15.2 Inventory 更新

| 文件 | 操作 |
|---|---|
| `.working/sim4-issues-inventory.md §P0-4` | 标 ✅ wontfix - by design (R2-7, 2026-05-01) |
| `.working/all-pending-needs.md` Tier 1 | R2-7 升级为 schema reframe，状态 ✅ landed |
| `.working/all-pending-needs.md` Tier 2 | C 档字段从 metrics §7 #3 重命名为独立"agent 视角 forensic"议题 |

### 15.3 新 memory（R2-7 落地后）

新建 `project_agent_cycle_schema_reframe.md`（或 fold 入现有 memory）：
- R2-7 schema reframe 决议历史（5 维度叙事框架）
- 历史 enum timeline + W1/sim #4 与 W2 数据断层
- forensic 路径 (i) 决议（NULL + status 标识）
- C 档字段独立议题归属

---

## 附录 A. brainstorm 决议历史（精简）

| 决议点 | 选择 | 理由 |
|---|---|---|
| decision 字段语义 | message (D2) | 与"reasoning + decision 共同表达决策"叙事一致；R2-4 派生 enum 实战使用 0 |
| reasoning 字段语义 | thinking content | 命名 fidelity；填补当前 thinking 100% 丢失漏洞 |
| 表 framing | 5 维度叙事（前因/上下文/现状/推理/决策）| 用户自定义 framing，叙事完整 |
| 表名 | agent_cycles | 与表职责一致，避免 decision_logs.decision 循环依赖 |
| 重命名范围 | α 全套（表 + 5 字段）| 一次性扫除命名 debt |
| trigger_context 内容 | conditional/alert detail；scheduled "scheduled tick" 占位 | 客观快照；与 prompt 拼接 detail 同源 |
| state_snapshot 来源 | 系统拉取（system view）| 客观 ground truth，与 trigger_context 同设计哲学 |
| state_snapshot 范围 | 系统层面客观事实（5 类）| 不重叠 perception 工具职责；不触限流 quota |
| state_snapshot 粒度 | detail 版（pending/alerts list）| sim #4 实证 count 版有 25% gap |
| decision cap | 不 cap (Text) | message 通常 500-3000 chars，存全文有事后分析价值 |
| forensic 路径 decision/reasoning | NULL（仅 status 标识）| 字段语义纯净；异常详情已在 system.log |
| 失败处理 | best-effort 容错 + _errors 标记，无 disable toggle，无连续失败检测 | fail-loud > fail-silent，避免静默劣化 |
| R2-4 派生路线 | 整套删除 | 实战使用 0；prospective over-engineering |
| 议题边界 | R2-7 仅 schema；P1-7 展示 R2-8 独立 PR；spec §8 接口契约联动 | review focus 分离；spec 内聚避免设计漂移 |

---

**Spec 完成 - 等待用户 review。** Spec self-review 见下一节。

---

## §16. Spec self-review

### §16.1 v1 self-review (2026-05-01)

按 brainstorming skill 流程做 spec self-review，4 项检查：

### Placeholder scan
- ✅ 无 TBD / TODO / 未填字段
- ✅ 修复点 1: §7.1 Step 3 / downgrade 的 SQLite ALTER INDEX RENAME 不支持 → 改用 drop_index + create_index 包装

### Internal consistency
- ✅ §3.2 5 簇分类 vs §4 字段 mapping 一致
- ✅ §4.5 nullable 变更 vs §6.5 forensic 写 NULL 一致
- ✅ §5 删除清单 vs §10.1 删除测试 vs §13 改动量估算 一致
- ✅ §8 P1-7 接口契约 vs §12 P1-7 R2-8 议题归属 一致
- ✅ §6.7 写入顺序总览的 state_snapshot capture 在 try 块外 → forensic 路径同样可用 ✓

### Scope check
- ✅ 单 spec 范围合理（~700 行，与 Iter 4/5 spec 类似量级）
- ✅ impl 范围（~400-550 行，6-8 commits）属 mid-large iter，未超大
- ✅ 与 R2-8 (P1-7 + N10) 解耦清晰

### Ambiguity check
- ✅ §6.2 helper 代码已明确 fetch 失败 → field=None（非 keys 缺失）
- ✅ §4.4 JSON schema 用 `| null` 标注可空字段
- ✅ forensic 路径 state_snapshot capture 时点已显式（在 try 块外）
- ✅ 字段 nullable / NOT NULL 矩阵在 §4.5 单独列出
- ⚠️ 测试数浮动（v2: 31-36 新增 / 25-27 删除）实操允许浮动，接受

### v1 self-review 不足

v1 self-review 仅查内部 consistency，**没查外部接口对照** — 这是真实缺陷，导致 E1-E5 全部漏过。**必须加一项外部接口验证。**

### §16.2 v2 self-review (2026-05-01, 用户 review 后修订)

#### v2 触发原因

v1 self-review 发布给用户审阅后，用户做了详细 audit，发现 **5 个严重事实错误（E1-E5）**：spec 引用的 dataclass 字段 / 类名 / 方法名与实际 codebase ground truth 不符。这些错误在 v1 self-review 4 项检查（placeholder / consistency / scope / ambiguity）下都未被发现。

**根因**: v1 self-review 没有第 5 项 "外部接口对照" — 即每个引用的现有类/字段/方法必须 grep / Read 验证存在。

#### v2 修复范围

| 项 | 类别 | 修复内容 |
|---|---|---|
| **E1** | 字段不存在 | `Position.pnl_pct/pnl_usdt` → `unrealized_pnl`（改 §4.4 / §6.2 helper code），加 pnl_pct 衍生计算 |
| **E2** | 字段名错 | `Balance.equity_usdt` → `total_usdt`（改 §4.4 / §6.2）|
| **E3** | 字段不存在（用户决议 (a)）| 扩展 `BaseExchange.Order` 加 `trigger_price: float \| None = None`（新 §4.7 + 改 §4.4 / §6.2 / §10.2 / §13 / §14 G9）|
| **E4** | 类名 + 字段错 | `PercentageAlertInfo` → `AlertInfo`，`previous_price` → `reference_price`（改 §4.3 / §6.1）|
| **E5** | 方法名 + 字段错 | `get_active_alerts(symbol)` → `get_price_level_alerts()`，`target_price` → `price`（改 §4.4 / §6.2）|
| **M1** | 措辞错 | R2-3 没产生 alembic migration，spec §7.3 改写 |
| **M2** | 措辞错 | AC2 "970 行" → "所有现有行"（不引数字）|
| **M3** | drift guard 不全 | G7 grep 名单扩到 5 ACTIONS + DERIVE_DECISION_VALUES + 派生函数（改 §14 G7）|
| **M4** | 缺佐证 | §5.3 加 note_biz_error 解耦佐证（写入终点 tool_calls.status，与 decision_logs 无关）|
| **M5** | 测试缺失 | 加 T-MIG-8 验证 server_default 保留 + G8 drift guard |
| **M6** | 缺可见性 | §6.2 加 IO 估算（OKX +4 REST/cycle, 限流余量 15-50×）+ Simulated 模式 0 IO 对比 |
| **M7** | 风险缓释 | §10.5 加 pre-impl smoke 验证 ThinkingPart（虽然 pydantic-ai 1.78 代码已确认支持）|
| **M8** | 风险缓释 | §10.5 加 pre-impl smoke 验证 UsageLimitExceeded partial usage |
| **D1** | 设计意图缺明确 | §6.2 末尾加 "snapshot 不依赖 agent tool 调用顺序" |
| **D2** | 设计意图缺明确 | §4.3 末尾加 "prompt 与 trigger_context 同源 duplicate" 设计意图 |
| **D3** | self-review 流程缺项 | 新增第 5 项检查"外部接口对照"，本 v2 增补 |

#### v2 新检查项：外部接口对照

每个 spec 引用的现有 codebase 实体必须有 ground truth 验证：

| 引用类型 | 验证手段 | v2 实施 |
|---|---|---|
| dataclass 字段 | Read 该 dataclass 定义文件 | ✅ 已对照 base.py 实测：Position / Balance / Order / Ticker |
| 类名 | grep `class <Name>` | ✅ 已确认：AlertInfo (price_alert.py:9) / PriceLevelAlertInfo (现有 cli/app.py path) |
| 方法名 | grep `def <name>` | ✅ 已确认：`get_price_level_alerts` (base.py:171) / `fetch_balance` / `fetch_positions` 等 |
| dict key 字面量 | Read source 行确认 | ✅ 已确认：alert dict 字段 `id/price/direction/symbol/reasoning` |
| 行号引用 | Read 该行确认 | ✅ 已确认：cli/app.py / base.py / models.py 引用行号 |
| 历史 alembic chain | `ls alembic/versions/` | ✅ 已确认：仅 379f...initial + e7b2...r2_4，R2-3 无 migration (M1 校准依据) |

#### v2 review 状态

✅ 所有 5+8+3 = 16 项审查反馈已修复内嵌
✅ E1-E5 ground truth 对照完成
✅ self-review 流程已加第 5 项外部接口对照（防 future spec 漏过）
✅ Pre-impl smoke 步骤已写入 spec（§10.5）

### §16.3 v3 self-review (2026-05-01, v2 review 后修订)

#### v3 触发原因

v2 spec 用户审阅发现 2 个 minor：
- **N1**: §4.4 JSON example `pnl_pct: 0.0163` 与 `unrealized_pnl: 12.34` 不自洽
  - 公式: 12.34 / (75350 × 0.265) × 100 = 12.34 / 19967.75 × 100 ≈ 0.062
  - 修正: `pnl_pct: 0.0163` → `0.062`（百分比单位，与公式自洽）
- **N2**: §16 章节编号重复（v1 + v2 两个 `## §16`）
  - 修正: 改为 §16.1 (v1) / §16.2 (v2) / §16.3 (v3) 嵌套结构

#### v3 review 状态

✅ N1 数值不自洽修正
✅ N2 章节编号嵌套重组

### §16.4 v4 self-review (2026-05-01, v3 review 后修订)

#### v4 触发原因

v3 spec 第三轮审阅发现 6 个 Issue：
- **Issue 1 (🔴必修)**: §6.5 forensic 例码在 except 块内重复调用 `_capture_state_snapshot` / `_capture_trigger_context`，与 §6.7 写入顺序总览 + AC4 + §9.5 三处明确"capture 在 try 外一次"互相矛盾
- **Issue 2 (🟡)**: `_capture_trigger_context` helper 缺 try/except 包装，AttributeError 等异常会打断 cycle
- **Issue 3 (🟡)**: `market.fetched_at` 与 `Ticker.timestamp` 语义不区分，stale 数据分析无法做
- **Issue 4 (🟡)**: §7.1 downgrade NULL 行限制无 SQL escape hatch
- **Issue 5 (🟢)**: Pre-smoke 2 是 belt-and-suspenders（reviewer audit pydantic-ai 源码已确认不暴露 partial usage）
- **Issue 6 (🟢)**: §6.2 active_alerts 单 symbol filter 缺意图论证

#### v4 修复范围

| 项 | 修复内容 |
|---|---|
| **Issue 1** (🔴) | §6.5 forensic 例码删除 except 块内的 capture 调用 + 改为复用 `*_var`；§6.6 success 路径同步使用 `*_var` 命名；§6.7 写入顺序总览强化"两次 capture 只在 try 之前发生一次"不变量 + 列三条保证（IO 预算 / forensic 时刻 / AC4）|
| **Issue 2** (🟡) | §6.1 `_capture_trigger_context` 整个函数 try/except 包裹，异常 → return None + log warning + cycle 继续；§9.1 加纪律条目 |
| **Issue 3** (🟡) | §4.4 JSON schema 加 `ticker_timestamp: int` (exchange ms epoch) 字段 + 注释区分 vs `fetched_at`；§6.2 helper 同步写入 |
| **Issue 4** (🟡) | §7.1 downgrade 加 SQL escape hatch（DELETE forensic NULL 行 + alembic downgrade）+ 数据损失说明 |
| **Issue 5** (🟢) | §10.5 Pre-smoke 2 改为"已 audit, 转参考记录"段，含 reviewer 提供的 pydantic-ai 源码引用（exceptions.py:183 + usage.py 多个 raise 点）|
| **Issue 6** (🟢) | §6.2 active_alerts filter 加意图注释（cycle 是单 symbol 上下文，filter 减体积 + 聚焦）|

#### v4 review 状态

✅ Issue 1 (🔴) 内部一致性修复 — capture 调用一次纪律强化
✅ Issue 2/3/4 (🟡) 全部修复
✅ Issue 5/6 (🟢) 信息项 / 意图记录补齐
✅ §6 全段 success / forensic 路径变量命名一致 (`*_var`)

### §16.5 v5 self-review (2026-05-01, v4 review 后修订)

#### v5 触发原因

v4 spec 第四轮审阅发现 9 项实质 + minor 问题：

- **P1-1 (🔴)** PriceLevelAlertInfo trigger_context 漏 timestamp（与 percentage AlertInfo 路径不对称）
- **P1-2 (🔴)** FillEvent trigger_context 漏 fee/position_side/timestamp/is_full_close（forensic 价值高字段未保留）
- **P1-3 (🔴)** AC1 "12 列"措辞精度不足（5+1+1+4=11，reasoning 未归类）
- **P2-1 (🔴)** §9.5 forensic 写入 try/except 描述与 §6.5/6.6 例码不一致
- **P2-2 (🔴)** §4.4 JSON example liquidation_price 缺 `| null` 标注
- **P2-3 (🟡)** §10.5 Pre-smoke 1 由谁跑未指定
- **P2-4 (🟡)** §10.2 实测 35 项 vs §10.4 估算 31-36 微对不齐
- **P2-5 (🟡)** §6.7 prompt 拼接 vs capture 顺序未明确"相对顺序无关"
- **P2-6 (🟡)** §2.3 Ticker 行号 base.py:14-22 vs 实际 13 起（含 @dataclass）

#### v5 修复范围

| 项 | 修复内容 |
|---|---|
| **P1-1** | §6.1 helper 补 `timestamp` (PriceLevelAlertInfo 7 字段全保留, base.py:285-291)；§4.3 表更新 |
| **P1-2** | §6.1 helper 补 4 字段：`side`/`position_side`/`fee`/`timestamp`/`is_full_close`（FillEvent 11 字段全保留, base.py:269-281）；§4.3 表更新；加 "字段保留原则" 段说明 forensic 价值取舍 |
| **P1-3** | AC1 改写为 12 列精确分类（5 rename + 1 widen + 1 新加 + 1 语义重构 + 4 identity 不变）|
| **P2-1** | §9.5 改写：明确 DB 写入 **不额外加 try/except 包裹**，与现状一致；列出 3 条理由（不动行为纪律 / `_record_action` swallow 是工具层职责 / 写入失败可见性优先）|
| **P2-2** | §4.4 JSON example 加 `liquidation_price: 79500.0 \| null` + `pnl_pct: 0.062 \| null` 与顶层风格一致 |
| **P2-3** | §10.5 Pre-smoke 1 加"由用户跑"说明（API 计费 + 网络）|
| **P2-4** | §10.4 改写：精确 35 + 浮动空间 33-37；总测试数 970→978~980 |
| **P2-5** | §6.7 加"P2-5 顺序无关注释"段：3 步互相相对顺序无关，唯一约束在 try 之前 |
| **P2-6** | §4.4 / §6.2 Ticker 行号校准 14-22 → 13-22（含 @dataclass）|

#### v5 review 状态

✅ P1-1 / P1-2 / P1-3 (🔴 实质) 全部修复 — trigger_context 字段对称 + AC1 精确分类
✅ P2-1 / P2-2 (🔴 实质) 修复 — 写入纪律对齐 + JSON null 标注一致
✅ P2-3 / P2-4 / P2-5 / P2-6 (🟡 minor) 修复 — Pre-smoke 责任 / 测试数 / 顺序意图 / 行号校准
✅ trigger_context 字段保留原则成 spec 内决议（FillEvent 11 字段 / PriceLevelAlertInfo 7 字段 / AlertInfo 7 字段全保留）

### §16.6 v6 self-review (2026-05-01, v5 review 后修订)

#### v6 触发原因

v5 spec 第五轮审阅发现 12 项问题（A-D 必须 + E-H 应当 + I-L 可选）：

| 项 | 严重度 | 内容 |
|---|---|---|
| **A** | 🔴 | §4.3 fill 行 "11 字段" 与列出 12 keys 不一致（11 dataclass + 1 合成 type）|
| **B** | 🔴 | §16.5 字段计数描述不准（PriceLevelAlertInfo 实有 6 字段, 加 type = 7 keys）|
| **C** | 🔴 | §9.2 "json.dumps 失败 → None + cycle 继续" 与 §9.5 "DB 写入失败可见性优先" 哲学冲突 |
| **D** | 🔴 | §10.3 + §8.5 display.py param 名（agent_output / trigger_type / tokens_used）是否改未明牌 |
| **E** | 🟡 | §10.5 Pre-smoke 2 audit 漏 usage.py:404 raise 行 |
| **F** | 🟡 | helper 异常日志 / `_errors` 缺 cycle_id 上下文（多 cycle 并发反查困难）|
| **G** | 🟡 | helper 文件 `cycle_capture.py` import 拓扑没明牌 |
| **H** | 🟡 | PriceLevelAlertInfo 行号 §4.3 (284-291) vs §6.1 (285-291) 不一致 |
| **I** | 🟢 | `tests/test_okx_websocket.py:208` stale 注释（提 decision_logs.order_id 实际无此字段）|
| **J** | 🟢 | AC11 "fetch 4 个 OKX endpoint" 与 §6.2 M6 "fetch_open_orders 物理 3 次 REST" 口径冲突 |
| **K** | 🟢 | implementer PR self-check grep 没正式入 spec |
| **L** | 🟢 | cli/app.py:54 派生失效注释未列入删除清单 |

#### v6 修复范围

| 项 | 修复内容 |
|---|---|
| **A** (🔴) | §4.3 fill 行改 "12 字段（11 dataclass + 1 合成 type）"；同时 PriceLevelAlertInfo / AlertInfo 也改 "7 字段（6 dataclass + 1 合成 type）" 统一口径 |
| **B** (🔴) | §16.5 措辞已由 v6 表 §4.3 自然纠正（不重写 v5 历史，本 §16.6 记录解释）|
| **C** (🔴) | §9.2 改写：json.dumps 失败按 §9.5 默认行为（异常上抛，不 swallow）+ 列 3 条理由；T-SS-8 round-trip 测试守把 |
| **D** (🔴) | §10.3 test_display_cycle.py 项明牌 R2-7 不改 display.py param 名（trigger_type / agent_output / tokens_used 是渲染层标签，与 DB 字段名解耦）；§8.5 接口契约段加注释明确 R2-8 决定 param rename |
| **E** (🟡) | §10.5 Pre-smoke 2 audit 列表补 usage.py:404（共 7 处 raise）|
| **F** (🟡) | `_capture_trigger_context` / `_capture_state_snapshot` signature 加 `cycle_id: str` 入参；日志 / `_errors` / snapshot._cycle_id 字段都附带；§6.5/6.6/6.7 调用点同步加入参 |
| **G** (🟡) | §6.1 helper code 加 import 段（`from src.integrations.exchange.base import FillEvent, PriceLevelAlertInfo` / `from src.services.price_alert import AlertInfo` / `TYPE_CHECKING` 守 TradingDeps）|
| **H** (🟡) | §6.1 helper 注释 PriceLevelAlertInfo 行号统一为 base.py:284-291（含 @dataclass）|
| **I** (🟢) | §5.1 stale 注释清理段加 `tests/test_okx_websocket.py:208` 顺手更新条目 |
| **J** (🟢) | AC11 改 "4 次逻辑 fetch（与 §6.2 M6 对齐）" 明确口径 |
| **K** (🟢) | §14 G7 加 implementer PR self-check 步骤（`grep -rn "DecisionLog\|decision_logs" src/ tests/ scripts/` 输出附 PR description，与 G7 互验）|
| **L** (🟢) | §5.1 source code 删除清单加 cli/app.py:~54 派生失效注释 |

#### v6 review 状态

✅ A/B/C/D (🔴 必修) 全部修复 — 字段计数一致 + json.dumps 纪律对齐 + display.py param 明牌
✅ E/F/G/H (🟡 应修) 全部修复 — audit 完整 + 多 cycle 反查能力 + import 边界 + 行号统一
✅ I/J/K/L (🟢 可选) 全部修复 — stale 注释清理 + 口径统一 + PR self-check + 删除清单完整

### §16.7 v7 self-review (2026-05-01, v6 review 后修订)

#### v7 触发原因

v6 spec 第六轮审阅发现 D 修复**未彻底**：display.py param 名"不改"决议在 §10.3 + §8.5 明牌，但 spec 内 2 处残留旧措辞与决议矛盾，会导致 impl 阶段方向摆动。

| 残留 | 位置 | 旧措辞 | 风险 |
|---|---|---|---|
| **D-残留 1** | §8 章节抬头 | "display.py 在 R2-7 PR 中仅做'字段名 transition'（旧字段名读取 → 新字段名读取）" | implementer 读此处会以为要改 param |
| **D-残留 2** | AC8 验收标准 | "display.py 字段名 transition：trigger_type→triggered_by 等读取改名" | reviewer 按 AC8 验收会判定 implementer 没改而 reject |

后果：implementer 看 AC8 会改 display.py param → reviewer 看 §10.3 会指出不该改 → impl 阶段决策摆动 / 来回返工。

#### v7 修复范围

| 项 | 修复内容 |
|---|---|
| **D-残留 1** | §8 章节抬头改写：明牌"R2-7 PR 内 display.py **不改 param 名**（trigger_type / agent_output / tokens_used 保留），仅传入数据来源跟随 DB 字段。R2-8 PR 实施本节全部展示设计 + 决定 param rename" |
| **D-残留 2** | AC8 改写：明牌"display.py **不改 param 名**（trigger_type / agent_output / tokens_used 保留为渲染层标签）；渲染逻辑保持现状（P1-7 改造 + param rename 全留 R2-8 决定）" |

#### v7 review 状态

✅ D-残留 1 / D-残留 2 修复 — §8 抬头 / §10.3 / §8.5 / AC8 全 4 处对齐"display.py param 名 R2-7 不改"决议
✅ Spec 内部一致性：implementer 路径（AC8 + §8 抬头）与 reviewer 路径（§10.3 + §8.5）现在指向同一结论，无方向摆动风险

spec v7 准备好让用户做最终 review。
