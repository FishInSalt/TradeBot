# Iter tool-opt-fee-visibility — fee 感知端到端贯通

**Date**: 2026-05-15
**Iteration**: iter-tool-opt-fee-visibility
**Type**: Design spec (system prompt + tool outputs + fill notification + wizard)
**Source brainstorm**: 2026-05-15 session 基于 sim #8 W2 实证 + 用户三维框架（决策前认知 / 执行时显示 / 持仓中锚点）
**Upstream**: `.working/sim8-w2-fee-and-manual-close.md`（Fee Awareness Gap 议题立项基础）
**Related principles**: 1（fact-provider 不是 guard）/ 2（工具服务 agent 心智路径）/ 3（信号唯一权威来源）/ 4（信号补齐优先于新工具）/ 5（接口闭环常用 pattern）/ 7（标签 / 单位清晰）/ 8（信任 agent + 工具优先 — fact 注入不算 prompt nudge）

---

## 0. One-minute summary

sim #8 178 cycles 实证：**手续费占总亏损 77.4%**（30 笔订单总 fee 277.55 USDT，gross PnL -81.10 USDT，净亏 -358.65 USDT）。agent 决策中 fee/friction 提及仅 9/178 (5%)，`get_performance` 调用仅 2/178 (1.1%)；fee 既非 agent 决策因子，也非 agent 心智锚点。

本 iter 通过**端到端 fee fact 注入**让 agent 在四个 mental moment 都看到 fee fact：

| 时刻 | 工具 / 路径 | 暴露 fact |
|---|---|---|
| **决策前认知**（每 cycle）| system prompt Market Context | Fee 双行 segment（taker rate + round-trip cost 公式） |
| **决策前认知**（持仓中）| `get_position` Fee & Breakeven 段 | entry fee paid + breakeven 价格（含公式 caption；rate 数字仅由 system prompt 单源，按原则 3） |
| **执行 submit**（open / close）| `open_position` / `close_position` / `place_limit_order` 输出 | Est. entry/exit fee + Est. round-trip net（close） |
| **执行 fill**（fill notification）| `cli/app.py:472-479` | actual fee + actual round-trip net (full close) |

数据源统一为 `sessions.fee_rate`（wizard 必填，包括 simulated 和 OKX 两条路径；user 责任），通过 RuntimeConfig + TradingDeps 注入。**取消** 之前考虑的 `BaseExchange.get_taker_fee_rate()` 抽象方法（架构简化）。常量 `DEFAULT_TAKER_FEE_RATE = 0.0005`（OKX BTC perp regular tier taker）作为 wizard default 提示值 + RuntimeConfig/TradingDeps test 默认值，**生产路径强制 wizard 注入**。

**Fee 符号 invariant**（全 spec 适用）：`FillEvent.fee` 字段值始终为 **正数**，代表已付 cost；渲染时统一用 `f"Fee: {-event.fee:+.2f}"` 让符号自动正确（应对未来 OKX maker rebate 场景）。sim 路径 `actual_fee = fill_price × amount × fee_rate` 总为正 ✅；OKX CCXT 实盘 maker rebate 场景理论可能负值，本 iter 不处理（与 OKX maker/taker mix 实盘准备期议题同期）。

**OKX 路径 user-input vs CCXT echo 语义裂痕（已知风险，影响范围已收窄）**：本 iter OKX 分支让 user 手填 fee_rate（默认 0.0005），用于 system prompt + 工具输出层的 estimated fact（如 `Est. entry fee`）。OKX 实账户**实际 fill fee** 由 CCXT response 真实回填（含 VIP tier / maker rebate），可能与 user 手填值不一致。影响范围（修订自 review 新一轮 #1 — 避免过度承诺）：
- ✅ Fill notification 中 **exit fee** 由 `context.fee`（CCXT 真实回填）准确显示
- ✅ FillEvent.entry_price 字段消除 pnl 反推 bug（与 user-input fee_rate 无关）
- ⚠️ Fill notification 中 **round-trip net 的 entry_fee 分量**仍按 `entry_price × amount × deps.fee_rate` 重算（sim 路径 fee_rate 恒定数学恒等于 actual；OKX VIP tier 偏差时 entry_fee 估算偏移，round-trip net 同等失真）
- ⚠️ 工具 submit 输出层的 `Est. entry fee` / `Est. exit fee` 是基于 user-input 的 ballpark
- ✅ Agent 应以 fill notification 中的 `context.fee`（actual exit fee）+ `get_performance.Total Fees`（累计 actual fees）为真值；round-trip net 在 OKX 仅作 ballpark
- W3+ 通过 §7 follow-up `iter-tool-opt-okx-fee-rate-auto-fetch` 收敛 user-input → actual VIP tier rate；彻底消除 entry_fee 偏差需更进一步加 `FillEvent.entry_fee_total` 字段（独立议题，不在本 iter）

**FillEvent 字段扩展（本 iter）**：base.py `FillEvent` 加 `entry_price: float | None`（修订自 review #1+#3）。sim 在 `_fill_market_close` cap 之前 capture `pos.entry_price`，OKX 从 response `avgPx` 取。cli 渲染层直接消费，无需反推、无需感知 exchange 类型。详 §3.7。

不在 scope：metrics.py 算法层（`profit_factor` / `max_drawdown` / `win_rate` 用 gross）的 net 重构 — 独立 follow-up iter `iter-tool-opt-net-pnl-metrics`。本 iter 仅在 `get_performance` 输出层加 `(gross-based)` 标签明示当前数字性质。

surface delta: 工具数量 **不变**（无新工具）；持久层 schema **不变**（sessions.fee_rate 已 nullable=True，wizard 必填执行在应用层）；BaseExchange 接口**不变**。

---

## 1. Empirical foundations

### 1.1 Source data

- sim #8: 178 cycles / 19.2h / 1818 tool calls (DB `data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`)
- `.working/sim8-w2-fee-and-manual-close.md`（Fee Awareness Gap 议题立项分析）
- 三维框架来源：用户 2026-05-15 session 「决策前认知 / 执行时显示 / 持仓中锚点」

### 1.2 Per-issue datum table

| Issue | Datum | Source |
|---|---|---|
| Fee 失血占比 | 30 笔 order_filled 总 fee = 277.55 USDT（按 sim #8 fee_rate=0.001）；gross PnL = -81.10；净亏 -358.65 = -3.59% on 10k；**fee 占总亏损 77.4%**。**敏感性 caveat（review #14）**：fee 占比对 fee_rate 数值敏感（0.001 → 77.4%；0.0005 → ~50%），但比例量级稳定 — "fee 是核心失血源"结论不依赖具体 rate 值 | trade_actions SUM(fee) / SUM(pnl) |
| Agent fee blindness | narrative grep "fee/friction" = 9/178 (5%)；多数为事后追述非决策前考虑 | reasoning LIKE '%fee%' |
| 高频高曝光工具不显示 fee | `get_position` 174/178 (98%) calls，但当前输出层完全不显示 fee | tool_calls + tools_perception.py:222-340 |
| 决策辅助工具调用率极低 | `get_performance` 2/178 (1.1%) calls；唯一显示 `Total Fees` 的工具几乎无人查 | tool_calls SUM(get_performance) |
| Fill notification fee fact 丢失 | trigger_context JSON 含 fee 字段（DB 已存），但 `cli/app.py:472-479` user_prompt 渲染**不**包含 fee | app.py 渲染层 + trigger_context DB row |
| Submit-time fee blind | `open_position` (11 calls) / `close_position` (6) / `place_limit_order` (9) submit 输出**均不显示** fee 预估 | tools_execution.py:66-139, 538-588 |
| Breakeven 概念缺失 | narrative grep "breakeven" = 0/178；agent 用 "Unrealized +X" gross 视角思考盈亏 | reasoning LIKE '%breakeven%' |
| sessions.fee_rate 实际填充 | 全部 8 个 session 都已设 fee_rate（NULL=0）；wizard 必填后亦不会产生 NULL | sessions WHERE fee_rate IS NULL |
| 数学恒等验证 | sim #8 Trade #1: entry 81878.6 × 0.366 × 0.001 = 29.96756 vs actual recorded 29.9676（差 0.0004 浮点）| trade_actions.fee 反查 |

> **footnote (sim #8 fee_rate)**：sim #8 session 用户输入 `fee_rate=0.001`（不是 wizard default 0.0005），所以验证表用 0.001 反算。本 iter 引入的 `DEFAULT_TAKER_FEE_RATE = 0.0005`（OKX BTC perp regular tier）是更通用的 default 提示值，user 仍可在 wizard 中输入自己的实际费率（如 sim 测试用 0.001 / OKX VIP tier 实际 rate）。default 切换不影响已设 fee_rate 的 session 数据。

### 1.3 Implication

实证表明：

- **gross PnL 不再是决策核心** — fee 占总亏损 77.4%，agent 看到 "Unrealized +183" 误判持仓盈利，实际净 +120（扣 round-trip fee 60）。Breakeven 是 fee-aware 决策锚点。
- **agent 不缺 profit 意识** — narrative "profit/earn" 提及 82/178 (46%)；缺的是 fee fact，不是 motivation。这强力反驳"加目标 prompt 即可"路径。
- **prompt 文字层无效** — `persona.py:83` 已有 "frequent small trades can erode capital through friction costs alone"，5% grep 证明抽象文字无效。需要**具体数字 + agent 心智锚点**（breakeven）。
- **工具层 fact 缺失是根因** — agent 5 次中 4 次 fee blindness 来自工具输出不暴露 fee；按原则 8 反思顺序：能力 ✅ 描述 ❌ 默认值 N/A 接口 ❌，应工具侧修复，不依赖 prompt nudge。

数学性质：`entry_price × contracts × fee_rate = Σ(individual fill fees)` 在 sim 中**恒等成立**（weighted entry 设计自然累计），消除"复用 DB actual fee vs 重算估算"的精度顾虑。OKX 实盘 < 0.01% 偏差。

---

## 2. Architecture and scope

### 2.1 数据流

```
[wizard]                       [DB]                              [app build_services]
  fee_rate input        ──>    sessions.fee_rate         ──>    inject to:
  (mandatory in iter)                                              ├─ RuntimeConfig.taker_fee_rate
                                                                   └─ TradingDeps.fee_rate
                                                                          │
              ┌──────────────────────────────────────────────────────────┘
              ▼
   [System prompt Market Context]    │   [tools 计算 fee fact]
   Fee: taker 0.100% per side        │   entry_fee_paid = entry × contracts × fee_rate
   Round-trip cost = 2 × rate × ntn  │   est_exit_fee = mark × contracts × fee_rate
              │                       │   breakeven = entry × (1 ± 2 × fee_rate)
              │                       │
              ▼                       ▼
   每 cycle agent 看到的 base fact ├─ get_position Fee & Breakeven 段
                                  ├─ open_position / close_position / place_limit_order submit
                                  └─ fill notification (含 actual round-trip net，用 FillEvent.entry_price 直接计算)
```

### 2.2 设计决策

| 决策 | 选项 | 选择 | 理由 |
|---|---|---|---|
| fee_rate 数据源 | A. BaseExchange.get_taker_fee_rate() 抽象 / B. session config 注入 | **B** | 简化架构（取消抽象接口）+ user 责任明确 + sim/OKX 统一来源 + 符合原则 3 |
| Entry fee 关联 | A. DB time-window match / B. 重算 (entry × contracts × rate) / C. 扩展 Position dataclass | **B** | 数学恒等 + 0 DB query + 加仓/part close 场景自然处理 + 跨 sim/OKX 一致 |
| Breakeven 公式精度 | 近似 `× (1 ± 2r)` / 精确 `(... ) / (1 ∓ r)` | **近似** | fee_rate² 项 ≈ 1e-7 可忽略 + agent 可手算验证 + caption 透明 |
| close_position submit Est. net PnL scope | round-trip / close-only | **round-trip** | agent 决策时关心本笔交易总账 + 与 breakeven 视角一致 |
| fill notification close round-trip net | 包含 / 不含 | **包含**（仅 is_full_close=True） | 闭环完整（est. submit vs actual fill 对账）；part close 跳过避免歧义 |
| sessions.fee_rate NULL fallback | wizard 必填 / DB NOT NULL migration / 仅 wizard 默认值 | **wizard 必填** | 现实 0 NULL（n=8）；CLAUDE.md 禁止"scenarios that can't happen"；migration 风险 |
| BaseExchange 接口扩展 | 加 get_taker_fee_rate() / 不加 | **不加** | session config 注入替代；减少抽象层负担 |
| metrics.py 算法重构 (profit_factor / max_drawdown 用 net) | 含 / 独立 follow-up | **独立 follow-up iter** | 影响范围大（算法 + 输出 + 测试），scope creep 风险；本 iter 仅加 `(gross-based)` 标签 |

### 2.3 不在 scope（明示）

- **metrics.py 计算改 net** — profit_factor / max_drawdown / win_rate / avg / best/worst 算法修订 → follow-up iter `iter-tool-opt-net-pnl-metrics`
- **funding fee 模拟** — sim 不模拟 funding settlement；OKX 实盘 funding 是独立账目；W3+ 实盘准备期评估
- **pnl_pct 分母 convention 统一** — `get_position` 用 initial_balance / `cycle_capture.py` 用 notional；独立 convention 决策（与 net pnl follow-up 一起评估）
- **liquidation 简化公式** — sim 简化公式 vs OKX maintenance margin model；G-calc 系列议题
- **Manual Close Panic（W2 sim #8 第二议题）** — 等 W3 baseline 数据再立项（见 `.working/sim8-w2-fee-and-manual-close.md` §2）
- **OKX maker/taker mix 实际 fee** — 实盘准备期议题，与 `project_okx_demo_mark_vs_last_drift` memory 同期

### 2.4 surface delta

| 维度 | 变化 |
|---|---|
| 工具数量 | 33 → **33**（无变化） |
| BaseExchange 接口 | **无变化**（撤回 get_taker_fee_rate） |
| `FillEvent` dataclass | **+1 字段**（`entry_price: float \| None`；非 breaking，添加可选字段；修订自 review #1+#3） |
| DB schema | **无变化**（sessions.fee_rate 已存在 nullable=True） |
| Alembic migration | **无需** |
| RuntimeConfig | +1 字段（`taker_fee_rate`） |
| TradingDeps | +1 字段（`fee_rate`） |
| 测试新增 | 32 个（功能 + drift guard + FillEvent.entry_price 三路径 + pnl_cap 隔离 + OKX cache lifecycle）+ fixture 扫描（plan 期精确确认，估算上限 30 个，实际更少） |

---

## 3. Tool surface contracts

### 3.1 System prompt — Market Context 段重写

**当前** (`persona.py:_build_layer1`):
```
You trade USDT-margined perpetual futures (no expiry date). The exchange uses one-way 
position mode — you cannot hold long and short positions on the same symbol simultaneously. 
To reverse direction, close your current position first. Leverage cannot be changed while 
holding a position. Every trade incurs fees on both entry and exit — frequent small 
trades can erode capital through friction costs alone.
```

**修订后**:
```
You trade USDT-margined perpetual futures (no expiry date). The exchange uses one-way 
position mode — you cannot hold long and short positions on the same symbol simultaneously. 
To reverse direction, close your current position first. Leverage cannot be changed while 
holding a position.

Fee: taker {fee_rate_pct:.3f}% per side (set at session start).
Round-trip cost on a position = entry_fee + exit_fee ≈ 2 × fee_rate × notional.
```

**改动 rationale**:
- 删除 `Every trade incurs fees on both entry and exit — frequent small trades can erode capital through friction costs alone.` 句：
  - "frequent small trades can erode capital" — 评价 / nudge，违反原则 1
  - 5% grep 证明该抽象文字无效
- 加 Fee 双行 segment（独立 segment 风格，user 在 brainstorm § Prompt 措辞选择中确认）：
  - 含**具体数字** `{fee_rate_pct:.3f}%` — 注入式 RuntimeConfig fact（与 `wake_max_minutes` 同类，不是 nudge）
  - 含 round-trip 公式（agent 心智锚点）
  - `(set at session start)` 明示数据来源（系统层 fact，是 fee_rate 的**唯一**渲染位置；工具输出层按原则 3 不重复 rate 数字）；修订自 review #11，旧措辞 "(session config)" 在 agent 视角下可能歧义
  - Round-trip 公式中 `≈` 是 **first-order 近似事实标注**（fee_rate² 项 ~1e-7 可忽略），非 evaluation / nudge — 修订自 review S-3，spec rationale 层说明（不注入 system prompt 文本避免污染 agent 视野）

### 3.2 `get_position` 输出加 Fee & Breakeven 段

> **设计决策（修订自 review B-5 — 防 future audit 重开议题）**: rate 数字在 **status 类工具**（get_position 等状态查看）按单源原则只在 system prompt 渲染；在 **derived 类工具**（execution submit 输出）作为 transparency caption 显示一次性 computation 的 input（`notional × taker rate`）。这是有意的语义分层（status fact 视角 vs derived fact 视角），不是原则 3 单源违反。

**当前 sectioning**（R2-8c PR #37 pattern）:
1. `=== Position (... @ HH:MM:SS UTC) ===`
2. `=== PnL ===`
3. `=== Risk Exposure ===`
4. `=== Exit Orders ===`

**修订后** sectioning（5 段，header 保持现状不简化；修订自 review C1）:
1. `=== Position ({symbol} @ HH:MM:SS UTC) ===` — header 保持，Unrealized 行加 `(gross)` 标签
2. `=== PnL ===` — PnL 行加 `gross` 标签
3. `=== Fee & Breakeven ===` **新增**
4. `=== Risk Exposure ===`
5. `=== Exit Orders ===`

**新增 Fee & Breakeven 段 layout**（按原则 3 "信号唯一权威来源" — fee rate 已由 system prompt 锚定，本段**不重复 rate 数字**，仅展示 derived facts）:
```
=== Fee & Breakeven ===
Entry fee paid: ~-{entry_fee:.2f} USDT (= entry × contracts × rate)
Breakeven: {breakeven:,.2f} [current {current_price:,.2f}, {distance_pts:+.0f} pts]
  = {entry_price:,.2f} × (1 {sign} 2 × fee_rate) [{side} round-trip taker]
```

格式说明: `distance_pts` 用 **signed only** 形式（修订自 review #8，去掉重复的 "above/below" direction word — agent 看符号判断方向，与现有 `set_stop_loss` `({dist_pct:+.2f}%)` 风格一致）。

**Fee rate 单源原则**：rate 数字仅在 system prompt Market Context 段出现一次（每 cycle agent 已看到），`get_position` 不再渲染 rate 数字，避免 agent 跨层对账（"两处数字一致吗？"是认知噪声）。Breakeven 公式 caption 中 `(1 ± 2 × fee_rate)` 用符号变量而非具体数字 — agent 看 system prompt 数字 + 工具 derived fact 即可完整 reasoning。

**字段数据源**:
- `entry_fee = position.entry_price × position.contracts × deps.fee_rate`（重算，数学恒等于 actual cumulative entry fee paid）
- `breakeven`:
  - Long: `position.entry_price × (1 + 2 × deps.fee_rate)`
  - Short: `position.entry_price × (1 − 2 × deps.fee_rate)`
- `current_price = ticker.last`
- `distance_pts`（signed only，修订自 review #8）:
  - Long: `current_price - breakeven`（负数 = 当前在 breakeven 下方）
  - Short: `breakeven - current_price`（负数 = 当前在 breakeven 上方）

**跨层 caveat 已通过"单源化"消除**（修订自 review #6 + 新一轮 C3 决策）:
- System prompt: `Fee: taker {x}% per side (set at session start)` — **唯一 rate 数字源**（status fact 视角）
- `get_position` Fee & Breakeven 段（status fact 视角）: **不重复 rate 数字**，仅 entry_fee + breakeven derived facts
- `open_position` / `close_position` / `place_limit_order` submit 输出（**derived fact** 视角）: caption 中**保留**`(notional ~X × ~Y%)` rate 数字，理由 — 这是单次操作 derived computation 的 transparency caption（agent 看一笔 entry fee 怎么算的），不是 status fact 重复；与 status 类工具（rate 仅 system prompt 一处）的语义区分清晰
- Agent 心智路径：status 类工具看 entry_fee 数字（不看 rate）；derived 类工具看一次性 computation caption（含 rate 是必要透明性）

**Position / PnL 段 gross 标签修订**:
```
=== Position ===
Side: Long | Contracts: 0.366 | Entry: 81,878.60
Leverage: 15x
Unrealized: -56.29 USDT (gross)              ← 加 (gross) 标签

=== PnL ===
PnL: -56.29 USDT gross (-0.56% of initial capital)    ← 加 gross 标签
Duration: 31 min
```

**降级路径**（partial degradation pattern，与 R2-8c 一致）:
- `deps.fee_rate` 不可得 → system bug（build_services 注入失败），fail-fast；不在工具层 handle
- `current_price` 不可得 → Breakeven 行省略距离括号，公式 caption 保留
- `position.entry_price` 不可得 → Fee & Breakeven 整段省略（position 异常已在前置 logic fail）

**实施位置明示**（修订自 review S4）: Fee & Breakeven 段在 `_render_position_core` 之后立即拼接（仅依赖 `position.entry_price` + `deps.fee_rate`），**独立于现有 try/except 块**（`tools_perception.py:313-328` 包裹 ticker/balance/orders/contract_size/mark_price gather）。distance 括号部分用 `ticker.last` 时单独 try/except fallback to `None` → 省略距离括号，不连累整段。这样 ticker fail 时 Fee & Breakeven 段不被整体降级，与上述"current_price 不可得仅省略距离"描述一致。

**docstring 修订位置说明**（修订自 review #12）: 本节及 §3.4-3.6 描述的 docstring 修订指 **wrapper docstring**（`src/agent/trader.py:436-720` 区段的 `@agent.tool` wrapper 函数），不是 `tools_perception.py` / `tools_execution.py` 的 impl docstring。按 PR #25 (Iter 4 N7) DRY 反转后，wrapper docstring 由 pydantic-ai/griffe 自动 sniff 进 LLM 工具描述；impl docstring 不参与 LLM 视野。修订时**保留** wrapper 现有 fill-timing / cross-tool behavior 句（如 "Position fills via market order; you will receive a fill notification..."），仅在 docstring 末尾追加 fee 提示行。

### 3.3 `get_performance` 输出标签强化

**修订自 review C2 — docstring 修订位置明示**: 改 **wrapper docstring**（`trader.py:189-196` 区段的 `@agent.tool` wrapper 函数），不是 impl docstring（按 PR #25 Iter 4 N7 DRY 反转后，wrapper docstring 由 pydantic-ai/griffe sniff 进 LLM 视野；impl docstring 不参与）。

**当前 docstring**:
```python
"""Get detailed trading performance statistics."""
```

**修订 docstring**（fact-only call→output 风格，对齐 `get_position` pattern）:
```python
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

**输出层 Trade Stats 段标签修订**:
```
=== Trade Stats ===
Total Trades: 15 | Win: 6 (40.0%, gross-based) | Loss: 9
Avg Win: +27.32 USDT | Avg Loss: -27.20 USDT (gross-based)
Profit Factor: 1.34 (gross-based)
Max Drawdown: -3.5% (gross-based equity)
Best Trade: +124.75 USDT | Worst Trade: -59.24 USDT (gross-based)
```

**rationale**:
- 不改算法（metrics.compute() 仍输出 gross），仅在输出层加 `(gross-based)` 标签
- 让 agent 看到 PF 1.34 / drawdown -3.5% 等数字时立即知道这是 gross-based
- 配合 Fee fact 字段（`Total Fees: -277.55 USDT`），agent 可自己判断 net 性质
- 算法重构留 follow-up iter `iter-tool-opt-net-pnl-metrics` 处理

### 3.4 `open_position` submit 输出加 fee fact

**docstring 修订**（wrapper docstring trader.py:436-448，**保留现有 fill-timing 句 + Args: 段**，仅末尾追加 fee 提示；修订自 review B4 体例与 §3.5 一致）:
```python
"""Open a new position via market order. ... existing wrapper content
including "Position fills via market order; you will receive a fill
notification..." and Args: section preserved ...

Entry incurs taker fee = notional × fee_rate. Fill notification reports actual fee.
"""
```

**Return 修订**:
```python
notional = ticker.last * quantity
est_entry_fee = notional * deps.fee_rate
return (
    f"Order submitted: {side} {quantity:.6f} @ ~{ticker.last:.2f}, {leverage}x | ID: {order.id}\n"
    f"Est. entry fee: ~-{est_entry_fee:.2f} USDT (notional ~{notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
    f"You will be notified when filled."
)
```

### 3.5 `close_position` submit 输出加 round-trip net

**docstring 修订**（wrapper docstring，保留现有 fill-timing 句，仅末尾追加 fee 提示）:
```python
"""Close all open positions via market order. ... existing wrapper content ...

Close incurs taker fee on exit. Submit output includes est. exit fee and est. round-trip net PnL.
"""
```

**Return 修订**:
```python
# 修订自 review B-3 + N3: ticker fetch + fee 计算放在 _check_approval **之前**
# 以便 approval message 含 gross + net 双视角
positions = await deps.exchange.fetch_positions(deps.symbol)
if not positions:
    return "No positions to close."
order_side = "sell" if positions[0].side == "long" else "buy"
if deps.exchange.has_pending_market_order(deps.symbol, side=order_side):
    return "A close order is already pending. Wait for fill confirmation."

# fee 估算（用于 approval message + return message 共享）
ticker = await deps.market_data.get_ticker(deps.symbol)
total_unrealized = sum(p.unrealized_pnl for p in positions)
total_contracts = sum(p.contracts for p in positions)
total_entry_fee = sum(p.entry_price * p.contracts * deps.fee_rate for p in positions)
# Use bid/ask matching actual market close fill price (sim _fill_market_close convention)
est_fill_price = ticker.bid if positions[0].side == "long" else ticker.ask
est_exit_notional = est_fill_price * total_contracts
est_exit_fee = est_exit_notional * deps.fee_rate
est_net_pnl = -total_entry_fee + total_unrealized - est_exit_fee

# Approval message 同步 gross + net 视角（修订自 review B-3 — 选项 C 跨层一致）
action_desc = (
    f"Close {len(positions)} position(s), "
    f"PnL: {total_unrealized:+.2f} gross / {est_net_pnl:+.2f} net (round-trip)"
)
approved = await _check_approval(deps, "close", action_desc, 0, 0)
if not approved:
    return "Close rejected by human approval."

# create_order loop
order_ids = []
for p in positions:
    order_side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=order_side, order_type="market",
        amount=p.contracts, params={"reduceOnly": True},
    )
    order_ids.append(order.id)
    await _record_action(
        deps, action="close_position", order_id=order.id,
        side=p.side, reasoning=reasoning,
    )

return (
    f"Orders submitted: close {len(positions)} position(s) | IDs: {', '.join(order_ids)}\n"
    f"Est. exit fee: ~-{est_exit_fee:.2f} USDT (notional ~{est_exit_notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
    f"Est. net PnL: ~{est_net_pnl:+.2f} USDT (round-trip = entry fee ~-{total_entry_fee:.2f} + unrealized {total_unrealized:+.2f} + est. exit fee ~-{est_exit_fee:.2f})\n"
    f"You will be notified when filled."
)
```

**Approval gate 视角策略**（修订自 review B-3 选项 C）:
- approval message: `PnL: -56.29 gross / -86.20 net (round-trip)` — gross + net 双视角
- agent return message: 含 `Est. net PnL` 同 net 数字（视角一致）
- fill notification: `PnL: -56.29 (gross) / -116.17 (this fill, equiv-round-trip)` — 三层 gross + net 跨层完全一致

**ticker fetch 顺序**: 修订自 review B-3 + N3 — fetch 放在 `_check_approval` **之前**（之前 spec 文字描述与代码示例不一致；现 lock 为"approval 之前 ticker fetch"以支持 approval message 含 net 数字）。

**Approval-gate IO ordering tradeoff**（ultrareview R2 Imp #4）: ticker + get_contract_size 在 approval 之前 fetch 是 design-intent，**不**是 bug。Rationale: approval message 需含 `PnL: gross / net (round-trip)` 双视角（Task 21 audit Moderate #3 修订要求），net 估算依赖 ticker.bid/ask + contract_size。代价: approval 拒绝时 2 个额外 API call 浪费（OKX live cached metadata + cached ticker ~微秒级，rate-limit 几乎无影响）。优先级低于"approval 知情决策"的价值，维持现状。

注：one-way mode 下 `len(positions) == 1`，sum loop 单元素，保留 generic 写法。

**关于估算 fill 价口径**（修订自 review #5 — 旧 rationale "fill 价 ≈ ticker.last" 不准；实际 sim `_fill_market_close` 用 `ticker.bid if long else ticker.ask`）:
- close_position 是 **market order**，实际 fill 价：long close 用 `ticker.bid`（卖单 hit bid），short close 用 `ticker.ask`（买单 hit ask）— 而非 ticker.last
- est. exit fee 估算 "if I market-close now" 的 fee，**推荐使用 `ticker.bid/ask` 与实际 fill 价口径一致**（spread ~0.5-2 USDT 在 BTC 主流时段，预估 fee 偏差 ~0.001 USDT 量级可忽略，但 rationale 应准确）
- 这与 SL/TP（algo trigger，触发用 mark price）是不同口径 — 跨工具看似不一致但语义正确。memory `project_okx_demo_mark_vs_last_drift` 涉及 algo trigger 字段，不影响本路径

**OKX unrealized_pnl 跨锚漂移 caveat**（修订自 review B5）: OKX 实盘的 `position.unrealized_pnl` 由 CCXT 标准化（OKX API 用 mark price 计算），sim 路径用 `ticker.bid/ask` 计算（`simulated.py:_calc_unrealized_pnl`）。close_position 的 est_net_pnl 公式将 `total_unrealized`（mark-based on OKX）与 `est_exit_fee`（用 ticker.bid/ask 算 notional）混用 — OKX 实盘存在 mark vs bid/ask 漂移（per `project_okx_demo_mark_vs_last_drift` memory，demo 实测 1.67%）。本 iter 接受这个跨锚漂移（est_net_pnl 在 OKX 是 ballpark，不是精确值）；与 §0 "OKX 路径 round-trip net 仅作 ballpark" 一致；agent 应以 fill notification actual fee + get_performance Total Fees 为真值。

**Submit 端 vs Fill 端 fee 估算 scope caveat**（修订自 review N-4）: submit 端 `total_entry_fee = sum(p.entry_price × p.contracts × fee_rate)` 是当前持仓**全部 contracts 在单次 fill 假设**下的聚合估算；fill 端 `entry_fee_recompute = context.entry_price × context.amount × fee_rate` 是该 fill 对应 contracts 的单 fill 重算。sim 路径单 fill close 下两值数学恒等；OKX 大单分批 fill 时 submit 是 aggregate / fill 是 per-fill，agent 累积多 fill 视角对齐需查 `get_performance` Total Fees。

### 3.6 `place_limit_order` submit 输出加 fee fact

**docstring 修订**:
```python
"""Place a limit order at a specific price.

Limit fill incurs maker or taker fee depending on fill condition.
"""
```

**Return 修订**（用 limit price 算 notional，因 limit fill 价 = limit price；明示 "if filled"）:
```python
notional = price * quantity
est_entry_fee = notional * deps.fee_rate
return (
    f"Limit order placed: {side} {quantity:.6f} @ {price:.2f}, "
    f"{actual_leverage}x{leverage_suffix} | ID: {order.id}\n"
    f"Est. entry fee if filled: ~-{est_entry_fee:.2f} USDT (notional ~{notional:,.2f} × ~{deps.fee_rate*100:.3f}%)\n"
    "Note: This tool only submits the order — it does not mean the order has been filled."
)
```

### 3.7 fill notification 渲染（`cli/app.py:472-479`）

渲染统一用 `f"Fee: {-event.fee:+.2f}"` 让符号自动正确（与 §0 invariant 一致）。

**Open fill**（`event.pnl is None`）:
```
IMPORTANT EVENT: market triggered — BTC/USDT:USDT 0.366 @ 81878.6, Fee: -29.97 USDT
```

**Close fill (full close)**（`event.pnl is not None && event.is_full_close`）:

**修订自 review #1 + #2 + #3**：消除反推公式（pnl_cap 触发场景反推 entry_price 系统性偏移）+ 消除 exchange 类型区分。改用 **FillEvent 直接携带 `entry_price` 字段**，sim/OKX 在 fill 事件中由 exchange 层填入，cli 渲染层无需反推、无需 isinstance 判断：

```python
# cli/app.py:472-479 close fill (is_full_close=True) 渲染
if context.is_full_close and context.entry_price is not None:
    # entry_fee 重算（sim 路径数学恒等于 actual；OKX 路径在 VIP tier 偏差时为 ballpark）
    entry_fee_recompute = context.entry_price * context.amount * deps.fee_rate
    round_trip_net = -entry_fee_recompute + context.pnl - context.fee
    msg += (
        f", Fee: {-context.fee:+.2f} USDT, "
        f"PnL: {context.pnl:+.2f} (gross) / {round_trip_net:+.2f} (this fill, equiv-round-trip)"
    )
else:
    # part close 或 entry_price 缺失 — 仅显示 fee + gross
    msg += f", Fee: {-context.fee:+.2f} USDT, PnL: {context.pnl:+.2f} USDT (gross)"
```

变量名 `entry_fee_recompute` 明示语义（修订自 review 新一轮 #1）— 仅 sim 路径数学恒等于 actual；OKX 路径在 user-input fee_rate ≠ VIP tier actual rate 时是 ballpark。彻底消除 entry_fee 偏差需独立 `FillEvent.entry_fee_total` 字段（不在本 iter）。

`event.entry_price` 数据源（按 exchange 实现层填入，与原则 3 一致 — entry_price 由产生事件的 exchange 层定义）:
- **sim 路径**（`simulated.py:_fill_market_close` 等三处 close fill path）: 在 `pnl_cap` 应用**之前** capture `pos.entry_price`（weighted avg per-contract），传入 FillEvent。不影响 pnl 计算逻辑。
- **OKX 路径**（`okx.py:_parse_fill_event`）: 从 OKX response 取 `avgPx` 或 `info.avgPx` 字段（OKX V5 close fill 含此字段）；缺失时 entry_price=None（cli 渲染层降级到 fee + gross 视图）。

**关键优势**:
1. **消除 pnl_cap 反推 bug**: 即使 sim `_close_position_core` 在亏损接近爆仓时 cap pnl，entry_price 由 exchange 直接传入未受影响
2. **消除 exchange 类型区分机制**: cli 层无需 isinstance(SimulatedExchange)，统一逻辑
3. **sim/OKX 渲染一致**: 两路径都暴露 round-trip net（前提 entry_price 字段有值）
4. **降级路径清晰**: entry_price 缺失（OKX 早期实现 / part close）→ 自然 fallback 到 fee + gross 视图

**OKX `info.pnl` 语义 verify**（修订自 review #2 + #9，从 W3+ defer 改为本 iter implementation prerequisite）: 由于 entry_price 不再依赖 pnl 反推，OKX `info.pnl` 字段 gross/net 语义**只影响**显示标签（`(gross)` vs `(net)`），不影响 round-trip net 计算正确性。本 iter implementation 期需对 OKX V5 fixture 跑一笔 close 定标 `info.pnl` 字段语义（OKX 文档应是 gross，与 sim 一致），结果落 spec / code comment（不另立 follow-up）。

渲染（用 `f"{-context.fee:+.2f}"` 让符号自动正确，应对未来 OKX maker rebate 负值 fee 场景）:
```
IMPORTANT EVENT: market triggered — BTC/USDT:USDT 0.366 @ 81724.8, Fee: -29.91 USDT, PnL: -56.29 (gross) / -116.17 (this fill, equiv-round-trip)
```

label `(this fill, equiv-round-trip)` 明示 scope 是**本 fill** 对应的等效 round-trip（不是"本次 close 操作"也不是"多腿交易总账"）—— 修订自 review #3。OKX 大单分批 fill 场景下，单次 close 可能产生多个 fill 事件，最后一个 fill 触发 is_full_close=True 但 round_trip_net 只反映该 fill 对应的 contracts；agent 累计多次 fill 的 round-trip net 或查 `get_performance` 取 session-cumulative 视角。

**Close fill (partial)**（`event.is_full_close == False`）:
```
IMPORTANT EVENT: stop triggered — BTC/USDT:USDT 0.5 @ 82000.0, Fee: -41.00 USDT, PnL: +750.00 USDT (gross)
```

(实施时用 `f"Fee: {-event.fee:+.2f}"` 符号自动正确，与 §0 invariant 一致)

part close 不显示 round-trip net — round-trip 语义在剩仓存在时不清晰；agent 通过 next-cycle `get_position` 查剩余持仓的 round-trip 视角，通过 `get_performance` 查 session cumulative。

---

## 4. Backend wiring

### 4.1 `src/agent/persona.py`

**模块顶部加常量**（修订自 review #11，消除 0.0005 magic number 重复）:
```python
DEFAULT_TAKER_FEE_RATE = 0.0005
"""OKX BTC perp regular tier taker fee, as decimal. Used as wizard input
default + RuntimeConfig/TradingDeps test defaults. Production paths MUST
override via wizard-injected sessions.fee_rate."""
```

**RuntimeConfig 加字段**:
```python
@dataclass(frozen=True)
class RuntimeConfig:
    wake_max_minutes: int = 60
    """..."""
    taker_fee_rate: float = DEFAULT_TAKER_FEE_RATE
    """Session-level taker fee rate (decimal storage format, e.g., 0.001 = 0.1%; wizard input is in percent and divides by 100 before storing).
    
    Injected from sessions.fee_rate via build_services. Default DEFAULT_TAKER_FEE_RATE
    is for tests / temp call sites only — production paths MUST set explicitly. 
    If a production code path silently relies on the default, that is a bug — 
    flag and route through cli wiring."""
```

**`_build_layer1` 加 Fee 双行 segment**:
```python
def _build_layer1(runtime: RuntimeConfig) -> str:
    fee_pct = runtime.taker_fee_rate * 100
    return f"""You are a cryptocurrency trader operating autonomously. ...

## Market Context

You trade USDT-margined perpetual futures (no expiry date). The exchange uses one-way 
position mode — you cannot hold long and short positions on the same symbol simultaneously. 
To reverse direction, close your current position first. Leverage cannot be changed while 
holding a position.

Fee: taker {fee_pct:.3f}% per side (set at session start).
Round-trip cost on a position = entry_fee + exit_fee ≈ 2 × fee_rate × notional.

## Cross-Tool Behavior
..."""
```

### 4.2 `src/agent/trader.py`

**TradingDeps 加字段**:
```python
from src.agent.persona import DEFAULT_TAKER_FEE_RATE

@dataclass
class TradingDeps:
    ...existing fields...
    fee_rate: float = DEFAULT_TAKER_FEE_RATE
    """Session-level taker fee rate (decimal). Mirror of RuntimeConfig.taker_fee_rate;
    injected from sessions.fee_rate via build_services. Default for tests only."""
```

### 4.3 `src/cli/app.py` `build_services`

修订自 review #2（spec 之前误写 await db.execute；`build_services` 是 sync 函数且 fee_rate 已在 WizardResult 注入，无需查 DB）。

**注入逻辑**（sync, 直接用 `result.fee_rate`；raise 放在 `build_services` **函数顶部**，exchange 实例化之前，保证两条 exchange path fail 在同一位置 — 修订自 review #10）:
```python
# build_services is sync; WizardResult.fee_rate is wizard-enforced non-None
# (both simulated and OKX paths after §4.5 修订 + resume sub-step).
# Use raise (not assert) — assert is stripped by Python -O optimization;
# this is a production invariant.
def build_services(result: WizardResult, ...):
    # Top-of-function invariant check (修订自 review #10)
    if result.fee_rate is None:
        raise ValueError(
            "Session has no fee_rate configured. This usually means a legacy "
            "session was loaded but the resume flow's fee_rate sub-step did "
            "not run. To recover: (a) restart the CLI to trigger wizard resume "
            "flow; (b) or manually UPDATE sessions SET fee_rate=0.0005 WHERE "
            "id=<your_session_id> in DB and restart."
        )

    runtime_config = RuntimeConfig(
        wake_max_minutes=max_wake,
        taker_fee_rate=result.fee_rate,
    )
    deps = TradingDeps(
        ...,
        fee_rate=result.fee_rate,
    )

    # Drift guard (与 wake_max_minutes 同 pattern，app.py:908-911 参考；修订自 review E2)
    assert deps.fee_rate == runtime_config.taker_fee_rate, (
        f"fee_rate drift: TradingDeps {deps.fee_rate} vs "
        f"RuntimeConfig {runtime_config.taker_fee_rate} must match"
    )
    # ... exchange 实例化 etc
```

修订自 review S2 + M1 + M10: 改 `raise ValueError` 而非 `assert`（生产 invariant 不依赖 assert，Python -O 优化会剥离）；与 §4.6 SimulatedExchange raise 风格一致；放函数顶部统一两条 exchange path；error message 给 user recovery path 指引（review #4 哑错改进）。

不依赖 DB query — `result.fee_rate` 在 wizard 创建 session 或 `_restore_session` 重建 `WizardResult` 时已 hydrated（`src/cli/session_manager.py:169 fee_rate=s.fee_rate`）。Legacy session NULL fee_rate 走 §4.5 resume sub-step；§4.3 ValueError 是兜底保护。

### 4.4 `src/cli/app.py:472-479` fill notification 渲染

完整修订见 §3.7。需要访问 `deps.fee_rate`（`run_agent_cycle(agent, deps, ...)` 上下文 ready）。

### 4.5 `src/cli/wizard.py` fee_rate 必填（含 OKX 分支）

修订自 review #1（spec 之前未覆盖 OKX 分支；`wizard.py:130` 硬写 `"fee_rate": None`，会让 build_services 检测失败）。

**输入约定保留 percent UX**（修订自 review S1 — spec 之前误写 "decimal, e.g., 0.001"，与现有 `wizard.py:74 FloatPrompt.ask("Fee rate (%)")` 冲突；照原措辞落地会让 user 输 `0.05` 被存为 5% taker，fee 暴涨 100×）:

- **Prompt 文字**: `Fee rate (% per side)` — 保留 percent 输入，user 输 `0.05` 表 0.05%
- **存储格式**: `fee_pct / 100 → decimal`（保留 `wizard.py:82` 现行逻辑）
- **default 显示**: percent 形式（`0.05` for 0.05%，不是 `0.0005`）
- **OKX 分支同口径**: 同样接受 percent 输入

**两条 wizard 路径都改为必填 fee_rate**:

**Simulated 分支**（line 66-95 周围，当前已 fee_rate input，仅文字微调）:
```
Fee rate (% per side) [default 0.05]:
```

**OKX 分支**（line 128-133 周围，新增 fee_rate input，与 simulated 同口径）:
```
Fee rate (% per side) [default 0.05 = OKX BTC perp regular tier taker; OKX live 用户请按 VIP tier 实填]:
```

caveat 文字加入"OKX live 用户请按 VIP tier 实填"（修订自 review #12 — demo 账户的 VIP tier 通常不与 live 一致；user 按自己实际账户填值）。

代码 comment 标注 future enhancement：
```python
# OKX path fee_rate (iter-tool-opt-fee-visibility):
# Currently user-input self-estimated (matches simulated path UX).
# Future: fetch via OKX /api/v5/account/trade-fee endpoint to get
# the user's actual taker rate by VIP tier; remove the manual input.
# See spec §7 follow-up "iter-tool-opt-okx-fee-rate-auto-fetch".
```

**WizardResult.fee_rate 类型收紧**（line 25）:
```python
# 当前: fee_rate: float | None  # simulated only
# 改为: fee_rate: float          # both paths, wizard-enforced
```

**_show_summary 显示口径同步**：percent 形式（`fee: 0.05%`）与输入一致；存储读取时乘以 100 转回 percent 显示。修订自 review #6 — 当前 `wizard.py:295-296` 的 `if ex == "simulated":` 条件需**去掉**（OKX 路径也有 fee_rate，应一并显示）。

**Legacy NULL fee_rate session resume sub-step**（修订自 review #4 — 避免哑错 + 提供 recovery path）:

`session_manager._restore_session` 加 detection + sub-step:
```python
# Pseudo-code, plan 期 finalize
session_row = ...load from DB...
if session_row.fee_rate is None:
    console.print("[yellow]Legacy session has no fee_rate configured.[/]")
    fee_pct = FloatPrompt.ask(
        "  Set fee rate (% per side) for this session",
        default=0.05, console=console,
    )
    new_fee_rate = fee_pct / 100
    # UPDATE sessions SET fee_rate = new_fee_rate WHERE id = ...
    session_row.fee_rate = new_fee_rate
return WizardResult(..., fee_rate=session_row.fee_rate)
```

约 10 行，complementary 到 §4.3 build_services raise（兜底保护，不应该触发；若触发说明 sub-step 未运行 — 例如 user 直接 CLI launch 跳过 resume flow，那时 ValueError 消息引导用户走 wizard）。

`src/cli/session_manager.py:169 fee_rate=s.fee_rate` 路径不变（DB 列保持 nullable=True 向后兼容 legacy session 读取，但**新建 session 写入 + resume sub-step 双重保证非 None**）。

### 4.5b `src/integrations/exchange/base.py` FillEvent 加字段

修订自 review #1 + #3（消除反推 entry_price bug + 消除 cli 层 exchange 类型区分）:

```python
@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: str
    position_side: str
    trigger_reason: str
    fill_price: float
    amount: float
    fee: float
    pnl: float | None
    timestamp: int
    is_full_close: bool
    entry_price: float | None = None
    """Position weighted-avg entry price at fill time (per contract).
    
    For close fills (pnl is not None): exchange-layer-filled actual position
    entry price (before any pnl_cap clamping in sim). Used by cli renderer
    to compute round-trip net without reverse-engineering from pnl.
    
    For open fills (pnl is None): always None — by design (修订自 review C4 + B-2).
    Rationale: open fill 的 entry 信息已通过 fill_price 表达（open fill 时
    fill_price = 该次 fill 的 entry price）；entry_price 字段语义专用于 close
    fill 的 position weighted-avg entry。统一 open fill 永远 None 避免半态字段
    导致后续误用（如 reviewer 误以为 open fill 也可读 entry_price）。
    """
```

**sim 实现**（`simulated.py:_fill_market_close` + `_execute_fill`（SL/TP close path）+ `_force_liquidate` 三处 close fill path；修订自 review A2 — `_execute_limit_fill` 是 limit-**open** 不是 close path）:
- 三处都在 `_close_position_core` 调用**之前** capture `pos.entry_price`（weighted avg per-contract）— 修订自 review N-3，pnl_cap 行为：`_fill_market_close` (line 379) `pnl_cap=True` / `_force_liquidate` (line 602) `pnl_cap=True` / `_execute_fill` (line 526) **默认 False 不传**
- 传入 FillEvent: `entry_price=pos.entry_price`
- 不影响 pnl / fee 计算逻辑

**B2 重要性明示**（修订自 review B2）: sim #8 实证 — 15 笔 close 中 8 stop + 2 take_profit + 5 market = **SL/TP 触发占 67%**（主路径）。漏接 `_execute_fill` 等于主路径 fee 闭环不工作。三处必须同步注入。

**OKX 实现**（修订自 review B1 — 撤回错误的 "OKX order response avgPx" 假设）:

OKX V5 order response 的 `avgPx` 字段是**该订单的成交均价**（对 close 订单 = exit price），不是 position entry。要拿 position-level entry 需走另一条数据通道。

**推荐方案 A: Submit-time capture + order_id → entry_price internal mapping**（与 sim `_PendingOrder` cache 概念一致，限定 close path）:

1. **OKX 实现层加 `_close_order_entry_cache: dict[order_id, tuple[float, float]]`**（值为 `(entry_price, captured_at_ts)`；仅 close path 用，submit close 时 capture；多种 event 类型 pop）
2. `close_position` / `set_stop_loss` / `set_take_profit` 在 `create_order` **submit 之前**:
   - **已有** `fetch_positions` 调用（修订自 review B-1 — 撤回错误的"强制增加 API call"假设；`tools_execution.py:111` close_position / `:144` set_stop_loss / `:175` set_take_profit 三处都已有 fetch_positions）
   - 复用现有 fetch_positions 结果 → 取 `position.entry_price`
   - submit `create_order` 返回 `order_id`
   - `okx_exchange._close_order_entry_cache[order_id] = (position.entry_price, time.monotonic())`
3. `okx.py:_parse_fill_event` 处理 fill 时:
   - `event.is_full_close` or `pnl is not None` → 从 `_close_order_entry_cache.pop(order_id, None)` 取 entry_price
   - 填入 FillEvent.entry_price
4. **Cache lifecycle 完整状态机**（修订自 review B-1 — SL/TP 长挂单场景的 cache 管理）:

| 事件 | 动作 | 触发位置 |
|---|---|---|
| submit close-direction order | `cache[order_id] = (entry_price, now)` | `create_order` 之前 |
| fill (close fill) | `entry_price, _ = cache.pop(order_id, (None, 0))` | `_parse_fill_event` |
| cancel order（agent 主动取消 SL/TP）| `cache.pop(order_id, None)` | `cancel_order` 处 |
| TTL ceiling（防长挂单泄漏）| 周期性扫描 `cache`，drop `now - captured_at > TTL_HOURS` 条目；建议 TTL=24h（SL/TP 多数 < 24h，超过的 reset 是合理操作）| 后台 cleanup task / 每 1h 触发 |
| process restart | cache 内存丢失（不持久化）→ 已挂 SL/TP fill 时 cache miss → 降级到 fee + gross 视图（不影响功能正确性，仅 round-trip net 不显示） | 启动时空 dict |

**降级路径**: 任何 cache miss（pop 返回 None / restart 后空 cache / TTL 已过期）→ `entry_price=None` → cli 渲染降级到 fee + gross 视图。

**SL/TP 触发占 67% 实证**（sim #8）下，cache lifecycle 正确性是 OKX 主路径 fee 闭环的关键 — TTL + cancel pop + restart drop 三个状态都需 plan 期 explicit 测试。

**备选方案**（plan 期评估，本 spec 推荐 A）:
- B. 内存维护 long-lived position cache（`fetch_positions` 时刷新）— 复杂度高
- C. fill 后 `/api/v5/account/positions-history` 查询 — 额外 API call + 延迟
- D. OKX 路径不显示 round-trip net — scope 退化

**Plan 期 §6.0 Pre-gate 2 任务调整**: 验证 OKX `fetch_positions().entry_price` 字段（OKX V5 `posAvgPx`）准确性 + cache lifecycle 正确性（submit → fill → pop），**不是**验证 "OKX order response avgPx 含 entry"（这是错的假设）。

非 breaking change — `entry_price: float | None = None` 作为 default-None 可选字段添加，不影响现有 FillEvent 调用。

### 4.6 `src/integrations/exchange/simulated.py:66` 清理 silent fallback

**当前**:
```python
self._fee_rate: float = config.fee_rate if config.fee_rate is not None else 0.0005
```

**修订**（**有意 breaking change**，与 wizard 必填配合 fail-loud 优于 silent fallback）:
```python
if config.fee_rate is None:
    raise ValueError(
        "SimulatedExchange requires fee_rate in config "
        "(wizard-enforced; legacy NULL session detected)"
    )
self._fee_rate: float = config.fee_rate
```

让 NULL 显式失败，与 wizard 必填配合。

**Breaking change 影响**（明示 review #13）:
- 现 DB 8 个 session 全部已设 fee_rate（实测 NULL=0），生产路径不受影响
- 测试 fixture 受影响：预估 ~30 个 fixture 漏设 fee_rate（需扫描并显式 set `fee_rate=DEFAULT_TAKER_FEE_RATE` 或具体值）
- 实施期工作量：fixture 迁移作为本 iter implementation 一部分（不另立项）
- 方向正确性：与 wizard 必填 + RuntimeConfig 注入设计配套，silent default 与 fail-loud 二选一，本 iter 选 fail-loud（行业实践 best practice）

**DB read 路径 NULL fail-loud robustness**（补充 — review S2 提到"dev/test/历史快照都可能产生 NULL row"）:
- DB 列保持 nullable=True 向后兼容（不动 schema / 不做 alembic migration）
- 写路径（wizard 必填）保证新建 session 非 NULL ✅
- 读路径三层保护：(a) `session_manager._restore_session` 检测 NULL → wizard sub-step 补填（本 iter §4.5 已实现）；(b) `build_services` raise ValueError 兜底（§4.3）；(c) `SimulatedExchange.__init__` raise ValueError 兜底（本节）
- 三层 fail-loud 保证：手动 SQL / 旧 backup 恢复 / migration bug 产生的 NULL row 在 startup 即 fail，不会 silent 流到工具决策路径

**三层接受工程化代价的 rationale**（修订自 review E1 + S-1 — vs CLAUDE.md "scenarios that can't happen" + principle 8 工具/构造层简化哲学的紧张关系；本 iter 接受 trade-off 由 fee 占 77.4% 失血实证驱动 + robust 实施层要求）:
- (a) wizard sub-step 是 user-facing recovery path（避免哑错），UX 必要
- (b) build_services raise 是 cli wiring 层 invariant 检查（fail-fast）
- (c) SimulatedExchange.__init__ raise 是构造层 invariant（fail-loud 优于 silent 0.0005 default）
- 三层职责不同：(a) recovery / (b) wiring invariant / (c) construction invariant；不是同一 invariant 的冗余检查
- 接受工程化代价（~30 行代码）换取 三个独立失败模式（startup with NULL DB / startup without wizard / SimConfig 漏字段）的 fail-loud 覆盖；本 iter 数据 + 实证驱动议题（fee 占 77.4% 失血）需 robust 实施层

**测试 fixture 扫描清单**: 见 §6.2（实际类名是 `ExchangeConfig` 不是 `SimConfig`，详细 grep 清单参见 §6.2）。

---

## 5. 加仓 / part close 场景处理

### 5.1 加仓场景

设：t1 entry 1 BTC @ 80000 fee 80 → t2 add 1 BTC @ 81000 fee 81 → weighted entry 80500, contracts 2

| touchpoint | 行为 | 健全性 |
|---|---|---|
| t1 fill notification | `Fee: -80` | ✅ |
| t2 add fill notification | `Fee: -81`（open fill, pnl=None） | ✅（每次加仓单独显示） |
| t2 后 `get_position` Entry fee paid | `-161 (= 80500 × 2 × 0.001)` | ✅ 数学恒等于 cumulative actual 161 |
| t2 后 `get_position` Breakeven | `80500 × 1.002 = 80661`（剩余仓位 breakeven） | ✅ |

数学性质保证：`weighted_entry × total_contracts × fee_rate = Σ individual fill fees`（详 §9 数据局限：恒等性仅在 fee_rate 恒定的 sim 路径成立；OKX maker/taker mix 时偏差需实证）。

### 5.2 Part close 场景（前瞻防御）

**当前 agent 工具集不能触发 part close**（close_position / set_stop_loss / set_take_profit 全部用 `amount=p.contracts`）。OKX 实盘极端 slippage 下理论可能 partial fill，但 sim #8 实证 = 0 笔。

**本节是前瞻防御性设计（非实证驱动）**（明示 review #14）:
- 本 iter 加 part close 渲染分支的 if/else 约 1 行开销，远低于"前瞻防御"通常成本
- 防御目的：OKX 实盘极端市价 slippage / 未来加入 part-close 工具时无需重新设计 fill notification
- 与 CLAUDE.md "Don't add features beyond what the task requires" 的紧张关系：本 iter 渲染分支是单 if 判断，非整条代码路径；可接受

**多 fill close 链 scope**（修订自 review #3）:
- OKX 大单分批 fill 时，单次 close 可能产生多个 fill 事件
- 第 1 fill: is_full_close=False（仓位还未清零）→ 走 part close 分支
- 第 n fill: is_full_close=True（最后清零）→ 走 round-trip net 分支，用 `event.entry_price`（exchange 层填入的 weighted avg）+ event.amount + event.fee 计算 round_trip_net，**只反映该 fill 对应的 contracts**
- label 用 `(this fill, equiv-round-trip)` 明示 scope（不是"this close 总账"）
- agent 累计多 fill round-trip net 或查 `get_performance` 取 session cumulative

设：t3 part close 1 BTC @ 82000（remaining 1 BTC）

| touchpoint | 行为 | 健全性 |
|---|---|---|
| t3 fill notification | `Fee: -82, PnL: +1500 (gross)`（不显示 round-trip） | ✅ part close 不显示 round-trip 避免歧义 |
| t3 后 `get_position` Entry fee paid | `-80.5 (= 80500 × 1 × 0.001)` — **剩余仓位等效 entry cost** | ⚠️ 与 historical cumulative paid 161 不同；caption 明示 derivation 缓解 |
| t3 后 `get_position` Breakeven | `80500 × 1.002 = 80661`（剩余仓位 breakeven） | ✅ |
| t3 后 `close_position` submit Est. round-trip net | 基于剩余 1 BTC | ✅ 一致 |

**caption `(= entry × contracts × rate)`** 让 agent 看到 derivation，避免误读为 historical lookup。

**三视角分工**:
- per-fill historical: fill notification 累计 actual fee
- current position equivalent: `get_position` Fee & Breakeven 段
- session cumulative: `get_performance` Total Fees

---

## 6. Test plan

### 6.0 Implementation prerequisite（修订自 review #2 + #9）

本 iter implementation **不另立 follow-up，必须本期完成**两个 pre-implementation gate：

**Pre-gate 1 降级为 implementation 期 fixture test**（修订自 review N-5）: 既然 §3.7 已用 `FillEvent.entry_price` 替代反推，OKX `info.pnl` 字段 gross/net 语义只影响 fill notification 标签准确性（不影响 round-trip net 计算正确性），**不需要 OKX demo round trip 作 mandatory pre-implementation gate**。
- 改为: implementation 期通过 fixture test 覆盖 OKX response 解析（CCXT 标准化字段 + raw info.pnl 双源验证）
- OKX V5 文档 `fillPnl` 定义应是 gross realized P&L（与 sim 一致）；fixture test 中静态 assert 即可
- 结果落地: 在 `src/integrations/exchange/okx.py:_parse_fill_event` 的 pnl 解析处加 code comment 锁定语义

**Pre-gate 2: OKX position-level entry_price 数据通道 verify**（修订自 review B1 — 撤回错误的 "order response avgPx = entry" 假设）
- 方法: demo round trip 中跑 `fetch_positions()` 取 `info.posAvgPx`（OKX V5 position-level 字段），验证字段存在 + 数值 = 实际加权 entry
- 预期: OKX V5 `positions` endpoint 含 `posAvgPx` = position weighted avg entry price
- Cache lifecycle 验证: submit close 时 capture → fill callback 中 pop → cleanup 正确
- 结果落地: `okx.py` 加 `_close_order_entry_cache`; submit close path 在 close_position / set_stop_loss / set_take_profit 处 capture position.entry_price → cache by order_id; `_parse_fill_event` pop cache 填入 entry_price；缺失场景（cache miss）entry_price=None

### 6.1 新增测试（32 个）

**System prompt + Wizard**:
- `test_persona.py::test_layer1_market_context_renders_taker_fee_rate` — 验证 fee 双行 segment 含数字
- `test_persona.py::test_market_context_segment_no_evaluation_words` — drift guard，**scope 限定 `_build_layer1` 输出的 "## Market Context" 段**（修订自 review #8；排除 Layer 3 personality / trading_style 段，那些段含合规 evaluation 描述如 "patient trader"）：删除 "frequent small trades / can erode capital" 等 nudge 词
- `test_wizard.py::test_wizard_requires_fee_rate_input_simulated` — simulated 分支必填
- `test_wizard.py::test_wizard_requires_fee_rate_input_okx` — OKX 分支必填（review #1）

**Simulated Exchange**:
- `test_simulated_exchange.py::test_init_raises_when_fee_rate_is_none` — silent fallback 清理验证

**get_position Fee & Breakeven**:
- `test_get_position.py::test_renders_fee_breakeven_section_long` — Long 公式 + 字段格式（**不含 rate 数字行**，原则 3 单源）
- `test_get_position.py::test_renders_fee_breakeven_section_short` — Short side-aware 公式 `(1 − 2r)`
- `test_get_position.py::test_fee_breakeven_section_does_not_render_fee_rate_number` — drift guard，防 future 加回 rate 行（review #6）
- `test_get_position.py::test_entry_fee_matches_recompute_formula` — 数学恒等 drift guard
- `test_get_position.py::test_entry_fee_after_add_position_equals_cumulative_actual` — 加仓场景
- `test_get_position.py::test_entry_fee_after_part_close_equals_remaining_equiv_cost` — part close 场景
- `test_get_position.py::test_position_section_includes_gross_label` — Unrealized 加 (gross) 标签
- `test_get_position.py::test_pnl_section_includes_gross_label` — PnL 加 gross 标签

**get_performance**:
- `test_get_performance.py::test_docstring_lists_fee_fields_and_gross_caveat` — docstring 强化验证
- `test_get_performance.py::test_trade_stats_includes_gross_based_label` — `(gross-based)` 标签

**Execution tools**:
- `test_tools_execution.py::test_open_position_output_includes_est_entry_fee`
- `test_tools_execution.py::test_close_position_output_includes_round_trip_net_pnl`
- `test_tools_execution.py::test_place_limit_order_output_includes_est_entry_fee_if_filled`
- `test_tools_execution.py::test_execution_tool_docstrings_no_evaluation_words` — F 维度 drift guard

**Fill notification**:
- `test_cli_app.py::test_fill_notification_open_includes_fee`
- `test_cli_app.py::test_fill_notification_close_full_includes_round_trip_net_uses_entry_price_field` — 验证 cli 用 event.entry_price 不反推
- `test_cli_app.py::test_fill_notification_close_partial_omits_round_trip`
- `test_cli_app.py::test_fill_notification_label_uses_this_fill_equiv_round_trip`
- `test_cli_app.py::test_fill_notification_pnl_cap_scenario_uses_actual_entry_price` — drift guard，验证 sim pnl_cap 触发场景下 entry_price 仍正确（防回归到反推路径）

**FillEvent entry_price 字段** (新增 §4.5b):
- `test_simulated_exchange.py::test_fill_market_close_includes_entry_price_in_event` — sim market close 填入 verify
- `test_simulated_exchange.py::test_execute_fill_includes_entry_price_for_stop_trigger` — sim SL 触发 fill (修订自 review B2) 
- `test_simulated_exchange.py::test_execute_fill_includes_entry_price_for_take_profit_trigger` — sim TP 触发 fill (修订自 review B2)
- `test_simulated_exchange.py::test_force_liquidate_includes_entry_price` — sim 强平 fill
- `test_simulated_exchange.py::test_fill_event_entry_price_captured_before_pnl_cap` — pnl_cap 场景隔离
- `test_okx_exchange.py::test_close_order_submit_caches_entry_price_by_order_id` — OKX submit cache verify (修订自 review B1)
- `test_okx_exchange.py::test_parse_fill_event_pops_entry_price_from_cache` — OKX fill 时取 cache verify (修订自 review B1)
- `test_okx_exchange.py::test_parse_fill_event_cache_miss_yields_none_entry_price` — OKX 降级路径

### 6.2 测试 fixture 扫描清单

修订自 review M4: 实际类名是 `ExchangeConfig`（不是 `SimConfig`，spec 笔误）。app.py:799-800 使用 `ExchangeConfig(name="simulated", fee_rate=result.fee_rate, ...)` 间接路径创建 `SimulatedExchange`，所以 grep 必须包含 `ExchangeConfig(`。

**Plan sub-task 0**（修订自 review D1）: plan 期第一个 sub-task 是**精确 grep fixture 总数 + 分类**（fixture 已显式 set fee_rate 的 / 需补 fee_rate 的 / 测 nullable 行为不应改的），lock impl 工作量。grep 命令：

```
grep -rn "SimulatedExchange(" tests/
grep -rn "ExchangeConfig(" tests/
grep -rn "RuntimeConfig(" tests/
grep -rn "TradingDeps(" tests/
grep -rn "WizardResult(" tests/
```

对每个调用点分类：
- **A 类（无需改）**: 已显式设 fee_rate（如 `_sim_fixtures.py:44` / `conftest.py:132` / `test_simulated_exchange.py:7`）
- **B 类（需改）**: 漏设 fee_rate 的 fixture（必须新增 `fee_rate=DEFAULT_TAKER_FEE_RATE`）
- **C 类（保留 NULL）**: 显式测 DB column nullable 性质的（如 `test_storage.py:230 assert s.fee_rate is None`） + OKX path 旧约定（`test_okx_algo_normalization.py:50 result.fee_rate = None` 必须改）

预估影响上限 30 个测试文件（plan sub-task 0 精确确认实际数；估算偏高）。

### 6.3 验证策略

- 全套测试通过（包括现有 ~1694 个测试 + 新增 32 个 + fixture 迁移 plan 期精确确认数）
- sim #8 重跑：观察 agent narrative 是否出现 fee/breakeven 提及（target ≥ 50%）
- W3 baseline session：跨 session 验证 agent fee-aware 决策模式

---

## 7. Follow-up candidates

| ID | 议题 | 触发条件 | scope |
|---|---|---|---|
| **iter-tool-opt-net-pnl-metrics** | metrics.py 加 net_profit_factor / net_max_drawdown / net_win_rate 等；输出 gross + net 双视角；max_drawdown 切换 net equity-based 为主 | W3+ baseline 数据回来后立项；与 G-calc audit 类似的"计算严谨性 sprint" | 算法 + 测试 + 输出，影响范围大 |
| **iter-tool-opt-okx-fee-rate-auto-fetch** | OKX wizard fee_rate 从 user 手填改为 `/api/v5/account/trade-fee` API 自动获取（按 user 的 VIP tier 实际费率） | 实盘准备期；与 `project_okx_demo_mark_vs_last_drift` memory 同期 | wizard + okx.py + auth + 错误处理，影响范围中等 |
| pnl_pct 分母 convention 统一 | `get_position` 用 initial_balance / `cycle_capture` 用 notional 跨工具不一致 | 与 net_pnl_metrics 同期 | convention 决策 |
| funding fee 模拟 | sim 不模拟 funding settlement；OKX 实盘 funding 单独账目；agent 看不到 cumulative funding cost | W3+ + 实盘准备期联合评估 | sim 算法扩展 + RuntimeConfig 新增 funding 字段（与 taker_fee_rate 同 pattern 独立字段，避免混入 fee 段）+ system prompt 加独立 §Funding 段（不混入 §Fee 段）+ 工具输出层 |
| OKX 实盘 maker/taker mix | OKX 实盘 fee = limit maker / market taker 差异；当前用 taker rate 估算偏保守 | 实盘准备期；与 `project_okx_demo_mark_vs_last_drift` memory 同期 | sim/OKX 一致性 |
| Manual Close Panic（W2 议题 2）| sim #8 manual close -129.30 vs stop trigger +30.63；需多 session 验证规律 | W3 baseline 1-2 session 再现后立项 | close_position 输出 + alternative-action |
| **OKX `_close_order_entry_cache` 持久化** | 当前 in-memory only，进程重启丢全部 entries；OKX 重启后 pre-existing SL/TP 触发 → cli 退化"fee + gross"（agent 见到 `[round-trip net unavailable: entry_price not cached]` hint）。Design-intent graceful degradation，**不**算 regression. | 实盘准备期 + 用户进程重启频率高时；候选实现 `trade_actions.entry_price_snapshot` 列 + 重启时 rebuild cache | DB schema + 启动期 rebuild 逻辑 |
| **`place_limit_order` limit-as-close hook** | ✅ 已在本 iter 落地（ultrareview R2 Imp #2）：existing position 反向 limit 注册 entry_price；scale-in / fresh open 不注册 | 本 iter 内闭环 | n/a |

---

## 8. 验收锚点

### 8.1 实施期 AC（hard pass-gate）

| AC | 方法 |
|---|---|
| `get_position` 输出含 Fee & Breakeven 段 | 输出快照 + sectioning drift guard 通过 |
| 30 笔 order_filled fill notification 渲染含 fee | sim 重跑 + trigger_context 渲染验证 |
| close_position submit 输出含 Est. round-trip net | 工具调用快照 |
| Wizard 创建 session 必填 fee_rate（simulated + OKX 两条路径） | wizard 流程测试 |
| System prompt P4 snapshot 含 Fee 双行 segment | sessions.system_prompt 字段 grep |
| 全套测试通过（1694 现有 + 32 新增 + fixture 迁移 plan 期精确确认数） | pytest |

### 8.2 W3+ sim 行为 AC（按 R2-Next-G 四档模板）

修订自 review M3 + D2: 按 memory `project_r2_next_g_followups` W3 hard gate 模式分四档，fee 提及率与 breakeven 提及率分开锚定（fee 提及 ≠ fee-aware 决策，breakeven 才是 §1.3 强调的心智锚点）。

**Baseline rationale**（修订自 review D2）:
- **Fee 提及率 baseline = 5%**（sim #8 narrative grep 实测）
- **60% retain = 12× baseline**（量级提升，与 R2-Next-G 模板的"baseline × 10-15× = retain"对齐；表示 fee fact 已成为决策 mental loop 的常驻字段）
- **31% docstring-promo = 6× baseline**（中等量级，仍有提升空间）
- **Breakeven 提及率 baseline = 0%**（cold-start，新引入心智锚点）
- **40% retain = cold-start anchor 进入主路径阈值**（参考 R2-Next-G "新 fact 引入" 类议题的 W3 anchor 阈值）

**Fee 提及率**（baseline sim #8 = 5%）:

| 区间 | 判定 |
|---|---|
| ≥ 60% | **retain** — fee 已成为 agent 决策因子，方案 effective |
| 50-60% | **observe** — 进一步 session 数据评估 |
| 31-50% | **docstring-promo** — 工具 docstring 强化 / 加 fact 标签 |
| < 31% | **rollback / 重审** — 工具层 fact 注入未生效，需 root cause |

**Breakeven 提及率**（baseline sim #8 = 0%，cold-start 新引入心智锚点）:

| 区间 | 判定 |
|---|---|
| ≥ 20% | **retain** — breakeven 心智锚点 effective（首个观察期 cold-start 合理门槛）|
| 10-20% | **observe** |
| 5-10% | **docstring-promo** — Fee & Breakeven 段公式 caption 强化 |
| < 5% | **rollback / 重审** — 心智锚点未建立 |

**Breakeven 阈值 rationale**（修订自 review N-6 — 移除模糊"R2-Next-G 参考"，调低到 cold-start 合理范围）: 
- 首个观察期 cold-start 新引入心智锚点，从 0% 到 20% retain 已是显著建立（不要求与 fee 提及率同 60% 量级）
- 与 §1.3 "fee/breakeven 是不同视角的 mental anchor" 一致：fee 提及是 awareness 验证（高门槛），breakeven 是 anchor 建立验证（低门槛入门档）
- W3+ retain 后 W4+ 可重审 raise 阈值

### 8.3 边界行为 AC（part close + 多 fill close 场景）

修订自 review M2 + N5: sim #8 part close = 0 笔 + 多 fill close = 0 笔，本 iter 是前瞻防御设计；W3+ 实证后回审：

| AC | 触发动作 |
|---|---|
| Part close 场景出现时，agent narrative 不把"剩余等效 entry fee" / "historical cumulative" / "this fill 等效" 三套语义混读为同一概念 | 混淆率 >10% 触发 follow-up "part close 渲染语义收紧" |
| 多 fill close 场景 agent 累加 round-trip net（不误读为多笔独立交易） | 混淆率 >15% 触发 follow-up "多 fill 累加渲染" 或退化为 "partial 全部不显示 round-trip" |
| OKX 路径 fill notification actual fee vs system prompt user-input fee_rate 不一致时，agent 以 fill notification + get_performance.Total Fees 为真值 | W3+ OKX 实盘 session 触发后实证；若 agent 用 estimated fee 做精确决策 → 触发 `iter-tool-opt-okx-fee-rate-auto-fetch` 加速实施 |

---

## 9. 数据局限

- sim #8 单 session / single symbol（BTC/USDT:USDT）— 跨 symbol / 跨 persona 盲区
- W3 baseline 数据未跑，本 iter 是 W2 实证驱动的前瞻设计；W3 数据回来后可能微调
- 数学恒等性质（`entry × contracts × rate = Σ fills fee`）仅在 sim 单 fee_rate 场景下成立；OKX 实盘 maker/taker mix 时偏差需实证
- Manual Close Panic 等姊妹议题尚未立项，本 iter 仅解决 Fee Awareness Gap 一项
