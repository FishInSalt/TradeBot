# Agent 体验优化设计

> 目标：解决 Agent 运行过程不可见和 System Prompt 过度约束两个体验问题，释放 Agent 自主决策能力。

## 审查员上下文

### 系统架构

TradeBot 是一个 LLM 驱动的加密货币交易系统。核心架构：

- **Agent**：基于 pydantic-ai 的 ReAct Agent，通过 tool calling 与系统交互
- **Exchange**：抽象层支持模拟交易所（SimulatedExchange）和真实交易所（OKXExchange）
- **Scheduler**：事件驱动调度器，定时唤醒 Agent 或在成交/告警时立即唤醒
- **交易标的**：USDT 永续合约（单向持仓模式）

### Agent Cycle 工作流

每次唤醒后，Agent 进入一个 ReAct 循环：

```
唤醒(定时/成交/告警) → Agent 自主决定调用哪些工具 → 收集信息 → 分析判断 → 执行/观望 → 休眠
```

核心入口：`src/cli/app.py` 的 `run_agent_cycle()` 调用 `agent.run(prompt)` 执行一个完整 cycle。

### 当前展示管道（三通道现状）

| 通道 | 写入机制 | 当前实际记录内容 |
|------|---------|----------------|
| **系统日志** (`logs/system.log`) | root logger, DEBUG 级别文件 handler | 极少：cycle token 统计、budget 重置、失败 warning。**Agent 思考和 tool 返回值完全没有记录** |
| **会话日志** (`logs/session_{id}.log`) | SessionConsole.print() 镜像 | 与终端完全相同：启动配置 + Agent 最终输出文字 |
| **终端** | SessionConsole.print() + Rich 格式 | 启动配置 + `Agent:\n{result.output}`（仅最终总结） |

关键问题：**三个通道都没有记录 Agent 的 tool 调用过程和思考内容。** pydantic-ai 的 message history 被完全丢弃。

### pydantic-ai 消息 API（A2 依赖的技术基础）

pydantic-ai >= 1.0 的 `agent.run()` 返回 `AgentRunResult`，提供：

- `result.output` — Agent 最终文本输出（当前唯一使用的字段）
- `result.new_messages()` — 本次 cycle 的完整消息列表 `list[ModelMessage]`
- `result.usage()` — token 用量统计

`ModelMessage` 是 `ModelRequest` 和 `ModelResponse` 的联合类型：
- `ModelResponse.parts` 包含 `TextPart`（Agent 文本）和 `ToolCallPart`（tool 调用名 + 参数）
- `ModelRequest.parts` 包含 `ToolReturnPart`（tool 返回值）和 `UserPromptPart`（用户提示）

通过遍历 `new_messages()` 可以提取完整的 tool 调用链：哪些 tool 被调用、传了什么参数、返回了什么。

### 当前 System Prompt（A1 要重写的内容）

`src/agent/persona.py` 的 `generate_system_prompt` 当前输出如下结构：

```
You are a professional cryptocurrency trader AI assistant.

## Trading Personality
- Risk Tolerance: {config} - {one-line description}
- Trading Style: {config} - {one-line description}
- Max Position Size: {N}% / Preferred Leverage: {N}x / Stop Loss: {N}% / Take Profit: {N}%

## Hard Rules (Soft Operating Constraints)
You MUST follow these constraints on every trade:
- Leverage MUST NOT exceed {N}x
- Single position MUST NOT exceed {N}%
- NEVER go all-in
- EVERY trade MUST have a stop loss
- Position sizing must be conservative

## Decision Workflow
On scheduled trigger: Step 1-4 fixed workflow (gather → analyze → decide → reason)
On fill event: Step 1-4 fixed workflow (review → set SL/TP → record → check)

## Limit Orders / Memory / Price Alerts / Wake Interval
(各功能使用说明)
```

问题：MUST/NEVER 硬规则 + 固定 Step 1-4 工作流 + 浅层人格描述，把 Agent 变成指令执行器。

### 为什么要做这两个改动

| 问题 | 现象 | 根因 |
|------|------|------|
| Agent 运行是黑箱 | 终端只显示最终总结，用户不知道 Agent 查了什么数据、做了什么分析、为什么不操作 | result.new_messages() 被丢弃，只用了 result.output |
| Agent 缺乏自主性 | 表现像"按规则执行的机器人"而非"有经验的交易员" | prompt 硬编码 MUST/NEVER 规则 + 固定工作流 |

---

## 背景

PR #1-#9 已完成核心功能（19 个 agent tools、Sim/OKX 双引擎、CLI 向导、Session 管理、352 测试通过）。在模拟环境运行 Agent 后发现上述两个体验问题。

### 当前工具列表（19 个）

**感知工具（8 个）**：
| 工具 | 用途 |
|------|------|
| `get_market_data(symbol, timeframe, candle_count)` | 市场数据 + 技术指标 + K 线 |
| `get_position(symbol)` | 当前持仓 + 风险上下文 |
| `get_account_balance()` | 账户余额 + 收益率 |
| `get_open_orders()` | 挂单列表 + 距当前价距离 |
| `get_trade_journal()` | 交易流水 + 绩效摘要 |
| `get_memories()` | 长期记忆 |
| `get_active_alerts()` | 告警配置（波动 + 价位） |
| `get_performance()` | 详细交易绩效统计 |

**执行工具（10 个）**：
| 工具 | 用途 |
|------|------|
| `open_position(side, position_pct, leverage, reasoning)` | 市价开仓 |
| `close_position(reasoning)` | 市价平仓 |
| `place_limit_order(side, price, position_pct, leverage, reasoning)` | 限价开仓 |
| `set_stop_loss(price, reasoning)` | 设止损 |
| `set_take_profit(price, reasoning)` | 设止盈 |
| `adjust_leverage(leverage, reasoning)` | 调杠杆 |
| `cancel_order(order_id, reasoning)` | 取消挂单 |
| `set_price_alert(threshold_pct, window_minutes, reasoning)` | 调波动告警参数 |
| `add_price_level_alert(price, direction, reasoning)` | 设价位告警 |
| `set_next_wake(minutes, reasoning)` | 设下次唤醒时间 |

**记忆工具（1 个）**：
| 工具 | 用途 |
|------|------|
| `save_memory(category, content, importance)` | 存长期记忆 |

---

## 改动范围

- A2：Agent 过程可见性 — 展示层改动，不影响 Agent 行为
- A1+P0：System Prompt 重设计 + 多时间框架引导 — 仅改 persona.py 的 prompt 文本
- 两个需求代码上互相独立

---

## 一、A2 — Agent 推理和执行过程可见性

### 问题

用户在终端看不到 Agent 的思考和操作过程：

- `src/cli/app.py:136` 使用 `agent.run()` 非流式调用
- `app.py:166` 只输出 `result.output`（最终总结文字），pydantic-ai result 中包含的完整 message history（tool calls、tool returns、中间推理文本）完全没有利用
- 终端日志默认 WARNING+（`src/cli/logging_config.py:55`），tool 调用的 info 级日志不可见
- Tool 实现（`tools_perception.py`、`tools_execution.py`）几乎无日志 — 仅 2 条 warning 级别的失败日志

### 方案选择

**选定方案：非流式 + 结构化后处理**

cycle 完成后遍历 `result.new_messages()`，提取 `ToolCallPart` 和 `ToolReturnPart`，在 display 层为每类 tool 写解析函数提取关键指标为一行摘要。

不使用流式输出（`agent.run_stream()`）— 每个 cycle 几秒到十几秒即完成，流式的体验增益不明显，非流式实现更简单。

**备选方案（未选）**：
- 原始截断：直接截取 tool return 前 N 字符 — 可读性差，信息密度低
- Tool 层自报告：tool 返回值增加 summary 前缀 — 改变 Agent 输入，引入风险

### 三通道输出设计

| 通道 | 内容 | 用途 |
|------|------|------|
| **终端**（SessionConsole） | Cycle 头 + tool 调用摘要 + Agent 最终文本 + token 统计 | 用户扫一眼了解"发生了什么" |
| **会话日志**（session_{id}.log） | 与终端相同（SessionConsole.print 镜像） | 持久化用户可见输出 |
| **系统日志**（system.log） | INFO：tool 名 + 摘要（与终端一致）；DEBUG：完整 tool 参数 + 完整返回值 | 调试和 prompt 调优 |

### 终端输出格式

```
── Cycle a3f2 (scheduled) ──────────────────
⚙ get_market_data      BTC $84,200 | RSI 62.3 | ATR 1.2%
⚙ get_position         Long 0.5 BTC @ $83,100 | PnL +1.32%
⚙ get_open_orders      2 orders (SL $81,500 / TP $86,000)
✎ save_memory [lesson]  BTC divergence at RSI overbought confirmed
                        as reliable exit signal (importance: 0.8)

Agent:
市场延续上升趋势，持仓浮盈健康，止损止盈位合理。
维持当前仓位不变，下次检查 30 分钟后。

tokens: 1,842 | budget: 48,158 remaining
────────────────────────────────────────────
```

格式规则：
- 感知/执行工具成功时使用 `⚙` 图标，错误/拒绝时使用 `✗` 图标（如 "Trade rejected by human approval"、"Position too small"、"A market order is already pending" 等非正常路径），记忆工具使用 `✎` 图标 — 视觉区分"成功操作"、"失败/拒绝"和"反思"
- `save_memory` 显示完整 content（不截断）— 它代表 Agent 的认知产出，对用户有观察价值。**注意：`save_memory` 的 `ToolReturnPart` 返回值已在 `tools_memory.py:17` 截断到 80 字符，因此完整 content 必须从 `ToolCallPart.args` 中提取（`content` 参数）。这是所有 tool 中唯一从 args 而非 return 提取摘要数据的例外。**
- 其他 tool 显示结构化一行摘要（从 `ToolReturnPart` 提取）
- 摘要中的 `$` 价格前缀是 display 层添加的装饰，tool 返回字符串中不含 `$` 符号
- Agent 最终文本继续使用 `result.output`（而非从消息历史中提取 TextPart），消息遍历仅用于提取 tool 调用信息。原因：ReAct 循环中 ModelResponse 可能包含多个 TextPart（tool call 前的中间推理 + 最终总结），使用 `result.output` 避免区分中间/最终文本的复杂性
- Cycle 头包含 cycle_id（前 4 字符，仅终端展示用途，完整 8 字符 ID 已存入 DecisionLog 数据库）和触发类型（scheduled / conditional / alert）
- 尾部显示 token 用量和剩余预算

### Tool 摘要解析

每个 tool 需要一个专用摘要解析函数。解析器从 tool 返回字符串中提取关键指标。

**感知工具：**

| 工具 | 摘要格式 | 提取逻辑 |
|------|---------|---------|
| get_market_data | `{symbol} ${price} \| RSI {rsi} \| ATR {atr}%` | 解析 Ticker 段 "Price:" 取价格；解析 Technical Indicators 段取 `RSI(14): XX.XX`；解析 Market Context 段取 `ATR(14): XX.XX (XX.XX% of price)` — RSI 和 ATR 在不同段落 |
| get_position | `{side} {contracts} @ ${entry} \| PnL {pnl}%` 或 `No open position` | 解析 side + contracts + entry_price；PnL 百分比在括号内 `(+X.XX% of initial capital)` |
| get_account_balance | `${total} ({ret}%)` | 解析 "Total:" 和 "Return:" 行 |
| get_open_orders | `{count} orders (types)` 或 `No pending orders` | 统计订单数，汇总类型（SL/TP/LIMIT） |
| get_trade_journal | `{total} trades \| Win {rate}% \| PnL {pnl}` | 解析 Performance Summary 段 |
| get_memories | `{count} memories` 或 `No memories` | 统计记忆条目数 |
| get_active_alerts | `Vol: {threshold}%/{window}min \| {count} price alerts` | 解析两个段落 |
| get_performance | `Return {ret}% \| {trades} trades \| Win {rate}%` | 解析 return、total trades、win rate |

**执行工具：**

| 工具 | 摘要格式 | 提取逻辑 |
|------|---------|---------|
| open_position | `{side} {qty} @ ~${price}, {lev}x` | 解析 "Order submitted:" 行 |
| close_position | `Close {count} position(s)` | 解析 "Orders submitted:" 行 |
| set_stop_loss | `SL @ ${price} ({dist}%)` | 解析 "Stop loss set at X.XX (X.XX% from current X.XX) \| Order: X" |
| set_take_profit | `TP @ ${price} ({dist}%)` | 解析 "Take profit set at X.XX (X.XX% from current X.XX) \| Order: X" |
| adjust_leverage | `{lev}x for {symbol}` | 解析 "Leverage adjusted to Nx for symbol" |
| place_limit_order | `Limit {side} {qty} @ ${price}, {lev}x` | 解析 "Limit order placed:" 行 |
| cancel_order | `Cancelled {type} {side} {amount} @ ${price}` | 解析 "Order cancelled: {type} {side} {amount}{price} \| ID: X" |
| set_price_alert | `threshold={pct}%, window={min}min` | 解析 "Price alert updated:" 行 |
| add_price_level_alert | `{direction} ${price}` | 解析 "Price level alert set:" 行 |
| set_next_wake | `{min}min` | 解析 "Next wake set to X min" |

> 注：以上摘要格式为方向性指导，实际解析器必须对照 tool 源码（`tools_perception.py` / `tools_execution.py`）的返回字符串编写。

**记忆工具：**

| 工具 | 摘要格式 | 提取逻辑 |
|------|---------|---------|
| save_memory | `[{category}] {完整 content} (importance: {score})` | **从 `ToolCallPart.args` 提取**（非 ToolReturnPart，因返回值已截断到 80 字符） |

**兜底机制**：任何无法解析的返回值（格式变化、新增 tool、错误响应），显示返回值前 80 字符。

### 消息遍历逻辑

pydantic-ai 的 `result.new_messages()` 返回 `list[ModelMessage]`（`ModelRequest` 和 `ModelResponse` 的联合类型）。遍历方式：

```python
from pydantic_ai.messages import (
    ModelRequest, ModelResponse,
    ToolCallPart, ToolReturnPart,
)

# 消息遍历仅提取 tool 调用信息，Agent 最终文本使用 result.output
for msg in result.new_messages():
    if isinstance(msg, ModelResponse):
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                # 记录：tool_name, args（用于 DEBUG 日志 + save_memory 完整内容提取）
    elif isinstance(msg, ModelRequest):
        for part in msg.parts:
            if isinstance(part, ToolReturnPart):
                # 记录：tool_name, content（用于摘要 + DEBUG 日志）
```

通过 `tool_call_id` 将 `ToolCallPart` 与对应的 `ToolReturnPart` 配对（优先使用，pydantic-ai 提供此字段）。顺序匹配仅作为 fallback — 并行 tool call 场景下顺序可能不可靠。

### 涉及文件

1. **`src/cli/display.py`** — 扩展现有文件（已有 `format_metrics` / `display_metrics`），新增 `format_cycle_output()` 和各 tool 摘要解析函数
2. **`src/cli/app.py`** — 修改 `run_agent_cycle`：提取 messages → 调用 display 格式化 → 写 INFO/DEBUG 日志 → 通过 console.print 输出
3. **测试** — 每个摘要解析函数的单元测试（输入样例返回字符串，验证提取结果）

---

## 二、A1+P0 — System Prompt 重设计 + 多时间框架引导

### 问题

`src/agent/persona.py` 的 `generate_system_prompt` 存在三个结构性问题：

1. **硬编码绝对约束**（39-44 行）：`MUST`、`NEVER`、`MUST NOT` 把 Agent 变成指令执行器
2. **固定决策工作流**（48-65 行）：Step 1-4 线性流程阻止 Agent 根据市况灵活调整
3. **人格描述过浅**（8-18 行定义浅层描述字典，注入 prompt 第 28-35 行）：trading style 和 risk tolerance 各只有一句话描述，对 LLM 信息量接近零

### 方案选择

**选定方案：三层架构重写**

彻底重写 prompt，建立三层结构：身份与工具 → 交易员思维框架 → 策略偏好注入点。

**备选方案（未选）**：
- 最小改动（软化 MUST/NEVER 措辞）— 治标不治本，固定工作流和浅层描述问题未解决

### 三层 Prompt 架构

```
┌─────────────────────────────────┐
│  Layer 1: 身份与工具            │  ← 你是谁，你有什么工具
├─────────────────────────────────┤
│  Layer 2: 交易员思维框架        │  ← 怎么思考（通用，MVP 核心）
├─────────────────────────────────┤
│  Layer 3: 策略偏好（注入点）    │  ← 按什么风格交易（当前硬编码默认值，未来用户自定义）
└─────────────────────────────────┘
```

### Layer 1: 身份与工具

简短声明身份，不规定行为。Tool schema 由 pydantic-ai 自动注入，此层只补充 schema 无法传达的使用注意事项：

- **市场上下文**：你交易的是 USDT 永续合约（无到期日）。采用单向持仓模式 — 同一标的不能同时持有多空仓位，需先平仓再反向开仓。持仓期间杠杆不可更改。（未来支持多市场类型时，此段按市场类型切换）
- **Fill 时序**：开仓后等 fill 通知再设止损/止盈，不要在同一个 cycle 中尝试
- **多时间框架**（P0）：可以用不同 timeframe 参数调用 get_market_data（如 "1h" 看大方向，"5m" 找入场点），用多个时间框架建立信心
- **记忆**：用 save_memory 记录交易复盘、市场规律、教训；回忆记忆以避免重复犯错
- **动态唤醒**：用 set_next_wake 根据市况和持仓状态调整检查频率
- **限价单**：用 place_limit_order 在关键价位挂单入场，不局限于市价单
- **价位告警**：用 add_price_level_alert 在分析识别出的关键支撑/阻力位设一次性告警

### Layer 2: 交易员思维框架（MVP 核心）

不写固定工作流，而是给出**思维维度**，引导 Agent 自主组合运用：

- **市场结构**：趋势还是震荡？处于什么阶段？关键支撑阻力在哪？不同时间框架是否一致？
- **信号与确认**：技术指标是否共振？价格行为是否确认信号？成交量是否配合？
- **风险回报**：这笔交易的 R:R 是多少？止损位在逻辑上合理的位置（结构性，非任意百分比）？潜在收益是否值得承担风险？
- **仓位管理**：当前承担多少风险？是否有加仓/减仓的依据？随着交易发展是否应该移动止损？
- **自我复盘**：之前类似情况的结果如何？记忆中是否有相关教训？无论是否交易，这个 cycle 能学到什么？

此层明确避免：
- 规定固定的步骤顺序
- 使用 MUST/NEVER/ALWAYS 等命令式措辞
- 指定具体的指标阈值或数值

### Layer 3: 策略偏好（注入点）

MVP 实现：基于 `PersonaConfig.trading_style` + `PersonaConfig.risk_tolerance` 生成有实质内容的策略描述 — 不是规则，而是倾向和偏好。代码中硬编码默认内容。

PersonaConfig 的数值参数（`max_position_pct`、`preferred_leverage`、`stop_loss_pct`、`take_profit_pct`）在 MVP 阶段**不注入 prompt**。原因：即使表述为"默认偏好"，具体数字仍会锚定 LLM 的决策范围，与"让 Agent 充分试错、观察决策边界"的目标矛盾。这些字段保留在 PersonaConfig 代码中（不删除），未来产品阶段（切实盘 + P3 硬风控）再决定是否重新引入。

**各 trading_style 的策略内容：**

- **trend_following**：识别并跟随已确立的趋势。等待趋势确认（均线排列、更高的高点/低点）后入场。随趋势发展移动止损。保持耐心 — 避免逆势交易。在趋势结构被破坏时离场，而非到达任意目标时。
- **swing**：在已确立的区间内或趋势回调中捕捉价格波段。通过支撑阻力和价格行为识别波段转折点。在价值区域入场，不在过度延伸的位置追价。目标设在区间对侧边界或前一个波段高/低点。
- **breakout**：关注整理形态和关键水平突破。在有成交量确认的突破后入场。假突破常见 — 严格管理风险。一旦动量确认突破方向，积极移动止损。

**各 risk_tolerance 的行为修饰：**

- **conservative**：优先保护资本。偏好高确定性、有明确失效条件的机会。较小仓位，较紧止损。可以错过机会。
- **moderate**：平衡机会和风险。标准仓位大小。愿意承受适度回撤换取合理收益。
- **aggressive**：高信心时愿意加大仓位。可以接受较宽止损和较大回撤。寻找非对称风险回报机会。

**未来扩展（不在本次迭代范围）**：

Layer 3 设计为注入点，未来迭代将扩展为：
1. P5a：用户在指定目录下保存策略偏好 skill 文件，系统加载并注入到 Layer 3
2. P5b：Skill 验证机制（格式、内容）
3. P5c：Wizard 集成策略偏好选择引导

### PersonaConfig 字段用法变化

| 字段 | 之前（硬规则） | 之后（MVP） |
|------|--------------|------------|
| trading_style | 一句话描述 | 完整方法论段落（注入 prompt） |
| risk_tolerance | 一句话描述 | 行为修饰段落（注入 prompt） |
| max_position_pct | "MUST NOT exceed X%" | 不注入 prompt，保留代码默认值 |
| preferred_leverage | "MUST NOT exceed Nx" | 不注入 prompt，保留代码默认值 |
| stop_loss_pct | "EVERY trade MUST have stop loss" | 不注入 prompt，保留代码默认值 |
| take_profit_pct | 隐含在规则中 | 不注入 prompt，保留代码默认值 |

### Wizard 配置简化

四个数值参数既然不注入 prompt，wizard 中对应的配置问题也应去除，避免用户配置了却不生效的困惑：

- **保留**：`risk_tolerance`、`trading_style` 的交互式选择
- **注释掉**：`max_position_pct`、`preferred_leverage`、`stop_loss_pct`、`take_profit_pct` 的 prompt 询问，加注释说明后续产品阶段可能重新启用
- **配置摘要简化**：`_show_summary()` 当前有两行表格行 — Persona 行（`moderate / trend_following`）和 Risk Params 行（`pos 30% / 3x / SL 3% / TP 6%`）。删除 Risk Params 行，保留 Persona 行

### 涉及文件

1. **`src/agent/persona.py`** — 重写 `generate_system_prompt`，建立三层结构
2. **`src/cli/wizard.py`** — 注释掉四个数值参数的配置问题，简化配置摘要
3. **测试** — 更新 persona 测试验证新 prompt 结构；更新 wizard 测试

---

## 实施顺序

1. **A2 先做** — 过程可见性是调优 A1 的基础设施
2. **A1+P0 后做** — Prompt 重设计，可通过 A2 直接观察 Agent 行为变化

两者代码独立（不同文件、无共享接口），可在同一分支或独立分支开发。

---

## 测试策略

### A2 测试
- 每个 tool 摘要解析函数的单元测试：输入样例返回字符串，验证提取的摘要
- `format_cycle_output` 单元测试：输入 mock message 列表，验证格式化输出结构
- 兜底测试：畸形/非预期返回值产生截断 fallback 而非崩溃

### A1 测试
- 验证 `generate_system_prompt` 包含三层结构
- 验证 PersonaConfig 值以偏好形式出现（非 MUST/NEVER 规则）
- 验证每种 trading_style 产生不同的方法论内容
- 验证每种 risk_tolerance 产生不同的修饰内容
- 验证包含多时间框架引导（P0）

### 冒烟测试
- A2 完成后：运行一个模拟 cycle，验证终端显示 tool 摘要 + Agent 文本 + token 统计
- A1 完成后：运行一个模拟 cycle，观察 Agent 行为 — 是否进行市场结构分析、使用多时间框架、做出自主决策

---

## Tool 层现有约束（排查结论：无需改动）

放开 prompt 自由度后，排查 tool 和 exchange 层是否存在阻塞 Agent 自主操作的人为限制：

| 场景 | 代码层行为 | 结论 |
|------|-----------|------|
| 加仓（同方向追加） | `_fill_market_open` 支持合仓，均价加权计算 | 正常工作 |
| 加仓时杠杆不同 | `set_leverage` 持仓时改杠杆 raise ValueError；撮合时杠杆不匹配的订单被静默取消 | 交易所物理限制（OKX 行为一致） |
| 反向开仓（持多开空） | limit 订单 raise ValueError；market 订单撮合时静默取消 | 单向持仓模式的物理限制 |
| 余额不足 | create_order raise ValueError | 合理约束 |
| 杠杆范围 | set_leverage 限制 1-125x | 与交易所一致 |

**结论**：所有约束均来自交易所真实行为，非人为限制。Agent 遇到这些限制后会收到明确的错误信息，可以据此调整决策。Tool 层不需要改动。

---

## 约束

- A2 是纯展示层改动 — 不影响 Agent 行为、tool 逻辑、数据流
- A1 仅改 `persona.py` — 不影响 tool 实现、config schema、CLI 逻辑
- 两个改动与现有 352 测试保持兼容（A2 新增展示代码，A1 仅改 prompt 文本）
- 不引入新依赖
- **最低依赖版本**：pydantic-ai >= 1.0（消息 API 依赖此版本，已在 pyproject.toml 声明）
- **语言规范**：系统代码全部使用英文，包括 prompt 文本、注释、变量名。本 spec 文档用中文描述设计意图，但实现时 prompt 必须用英文编写
- **已有日志兼容**：`app.py:164` 已有一条 cycle 级 INFO 日志 `Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)`，A2 新增的 tool 摘要 INFO 日志需与其格式风格保持一致
