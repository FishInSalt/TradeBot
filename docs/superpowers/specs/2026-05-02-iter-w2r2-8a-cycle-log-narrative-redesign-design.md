# Iter W2 R2-8a — Cycle Log Narrative Architecture Redesign

**Date**: 2026-05-02
**Status**: Spec (brainstorm done, plan/impl pending)
**Branch**: `feature/iter-w2r2-8a-cycle-log-narrative-redesign`
**议题序**: W2 prep round 2 — R2-7 ✅ → **R2-8a** → R2-8c → R2-8b → R2-9 → 启 W2

---

## 0. TL;DR

把 cycle log 从"工具流水账 + agent 最终输出"重设计为"还原 think → act → think → act → decision 完整 cognition flow"，对齐 R2-7 五维度叙事 schema。**渲染层为主 + retry-exhausted 可观测性补写**：渲染层是核心（display.py 重构 + 新增 SessionStats class）；持久化层仅 retry-exhausted 路径补写 forensic AgentCycle 行（`execution_status="retry_exhausted"`），与 R2-7 forensic 同型；不动 schema / 不动 prompt / 不动 agent 主循环。

| 改动维度 | 范围 |
|---|---|
| 主入口 | `src/cli/display.py` `format_cycle_output()` 时序遍历重构 |
| 数据消费 | 直接遍历 `result.new_messages()`（in-memory），不依赖 DB 重组 |
| Schema | 不动 — R2-7 已铺好 5 维度叙事字段（`triggered_by` / `trigger_context` / `state_snapshot` / `reasoning` / `decision`）|
| 改动量 | ~540-590 行（含测试 ~225-265；spec 五轮自审 +10 行 — projected stats footer + escape 全 body + run_agent_cycle stats=None 默认 + AC30 拆 a/b + 影响清单补 docs/model 注释 + 4+11 测试文件签名同步）|
| 关联 | 拆出 R2-8c（tool 输出优化）独立议题，本 spec 不含 |

## 1. 议题源由

### 1.1 上溯链：从 P1-7 inventory MVP 升级为完整 reframe

`.working/sim4-issues-inventory.md` §P1-7 把 session log 列为"展示难用伞议题（9 子问题），W2 启动前必修 MVP（A 路径 7-1/7-3/7-4/7-8）"，估 ~150 行 patch。

R2-8a brainstorm（2026-05-02）期间用户提出**根本性 reframe**：

> 当前 session log 中记录一个 cycle 的幅度大概也就半页报告的幅度，导致很多决策的前因后果信息都无法展示。是否能够在每个 cycle 还原 agent think 和 action，以及最后 decision 的过程？

这一 reframe 把议题从"补字段 patch"升级为"重设计 cycle log 形态使之成为 trader 决策报告"，与 R2-7 五维度叙事 schema 完全对齐——R2-7 已经在 DB 层把 thinking content 与 message text 分列（`reasoning` 列 = thinking, `decision` 列 = message text），但 P1-7 patch 路线**完全没消费 thinking 数据源**。

### 1.2 R2-8 拆分（避免 scope creep）

R2-8a brainstorm 末段浮现"工具输出优化"二级议题（D5 8 工具 L1 multi-line / D11 mixed C / D4 长尾 fallback 升级），但：
- 8 工具 L1 mockup 全是构想（基于 source 推断"应该长什么样"），未经 reasoning 实际引用频率验证
- 字段优先级、价值密度评估、L0/L1/L2 边界都依赖真实数据驱动
- 8 工具 verbatim parser 设计是独立议题级工作量

决议拆 R2-8c（tool 输出优化）为独立议题，R2-8a landed 后启动完整 brainstorm→spec→plan→impl。详见 `memory/project_r2_8c_tool_output_optimization.md`。

### 1.3 议题与 W2 启动关系

R2-9（W2 启动验证 smoke）需 R2-8a + R2-8c + R2-8b 全部 landed 后才启。R2-8a 是 reframe 框架；R2-8c 是 reframe 完整度补全；R2-8b 是 reflection MVP（独立议题）。

## 2. 当前状态分析

### 2.1 当前渲染逻辑（`src/cli/display.py:326-370`）

```python
def format_cycle_output(
    cycle_id: str, trigger_type: str,
    tool_calls: list[dict],     # ← 已扁平化 list，丢时序
    agent_output: str,          # ← TextPart message
    tokens_used: int,
    budget_remaining: int,
) -> str:
    lines = []
    lines.append(f"[dim]── Cycle {short_id} ({trigger_type}) {'─' * 30}[/]")
    for tc in tool_calls:
        icon, summary = resolve_tool_display(name, content, outcome, args)
        lines.append(f"{icon} {name:<22} {summary}")
    lines.append(f"\n[bold cyan]Agent:[/]\n{agent_output}")
    lines.append(f"\n[dim]tokens: {tokens_used:,} | budget: {budget_remaining:,} remaining[/]")
    lines.append(f"[dim]{'─' * 44}[/]")
    return "\n".join(lines)
```

### 2.2 sim #4 实测痛点（5 类）

| 类 | 问题 | sim #4 行号实证 |
|---|---|---|
| **A 时间维度** | cycle header 无时间戳、无间隔、无持续时长、footer 无结束时间 | L20 `── Cycle 85ff (scheduled) ──`；L65→L66 `Cycle 85ff` 完 → `Cycle 0935` 紧贴（实际 75min 间隔不可见）|
| **B 状态信息** | header 不显示 PnL/持仓/balance，跨 cycle 演变靠人脑串 | L435 `Cycle fdf2 (conditional)` 不显示已持有 short，需扫到 L436 ⚙ get_position 才看到 |
| **G trigger 上下文** | `(alert)` 不分哪个 alert / `(conditional)` 不显示 fill 内容 / `(scheduled)` 不分 30min 兜底 vs set_next_wake | L105 `Cycle 2e4f (alert)` 必须 query DB 才知是 76,100 上破还是 0.5%/5min vol；L435 `Cycle fdf2 (conditional)` fill 详情仅在 prompt 里不在 log |
| **E token 经济** | footer 仅单 cycle，无 session 累计 / cache hit / I/O 拆分 | 每 cycle footer 仅 `tokens: 46,488 \| budget: 9,953,512 remaining`；W2 24h 跑下来用户感知不到累计速度 |
| **核心：cognition flow 缺失** | thinking content **完全不在 session log**，agent reasoning 与 tool 调用扁平堆放无时序 | sim #4 L21-37 一整段 ⚙ tool 行紧贴 Agent: 段，无 reasoning 中间过程 |

### 2.3 R2-7 schema 已铺好的数据基础设施

| R2-7 字段 | R2-8a 消费方式 | 现状 |
|---|---|---|
| `agent_cycles.triggered_by` | Header `Trigger` 行类型显示 | ✅ 已写入（`scheduled` / `conditional` / `alert`）|
| `agent_cycles.trigger_context` (JSON) | Header `Trigger` 行详情 | ✅ 已写入（cycle_capture.py `_capture_trigger_context`）|
| `agent_cycles.state_snapshot` (JSON) | Header `State` 行（position/balance/market）| ✅ 已写入（`_capture_state_snapshot` 含 position/balance/market/pending_orders/active_alerts）|
| `agent_cycles.reasoning` (thinking content) | `▾ Reasoning` 段（拼接版 from `_extract_thinking_text`）| ✅ 已写入；R2-8a **不消费** DB 字段，从 in-memory `result.new_messages()` 重新提取以保留时序 |
| `agent_cycles.decision` (message text) | `▾ Decision` 段 | ✅ 已写入（= `result.output`）|
| `agent_cycles.created_at` | Header 时间戳 | ✅ 已存在 |

**关键发现**: R2-7 把多轮 ThinkingPart 用 `\n\n` 拼成单 `reasoning` 字段，**时序信息在 DB 已丢失**。R2-8a 的时序渲染**必须从 in-memory `result.new_messages()` 重新提取**（cycle 完成时数据齐全）。事后从 DB 重组时序非本议题范围。

## 3. Design Overview

### 3.1 5 维度叙事架构

每 cycle 渲染为 5 段，对应 R2-7 schema 一一映射：

```
═══════════════════════════════════════════════════════════════════════════
  ① Header  (cycle_id + 时间 + Trigger + State)
═══════════════════════════════════════════════════════════════════════════

② Reasoning #1  (thinking from ModelResponse[0])
② Action #1     (tool calls from ModelResponse[0])
② Reasoning #2  (thinking from ModelResponse[1])
② Action #2     (tool calls from ModelResponse[1])
...
② Reasoning #N  (thinking from final ModelResponse)
③ Decision      (TextPart from final ModelResponse)

═══════════════════════════════════════════════════════════════════════════
  ④ Footer  (Tokens + Cache + Duration + Ended)
═══════════════════════════════════════════════════════════════════════════
```

`②` 段按 `ModelResponse` 分组**时序交织**。每个 ModelResponse 内：ThinkingPart → ToolCallPart 顺序（已 verified）；最终 ModelResponse 内：ThinkingPart → TextPart 顺序（已 verified）。

### 3.2 完整 mockup（基于 sim #4 cycle 9f57 重 reframe；illustrative，非 byte-equal verbatim copy）

```
═══════════════════════════════════════════════════════════════════════════
  Cycle 9f57  •  18:14:23 UTC  •  +12 min from prev
───────────────────────────────────────────────────────────────────────────
  Trigger    ALERT — vol -1.6%/10min fired (BTC 76,225 → 75,448)
  State      Short 0.265 @ $75,350 (5x) | PnL +0.10% | Balance $9,990
═══════════════════════════════════════════════════════════════════════════

▾ Reasoning (892 chars total)
  I was woken by a 0.5%/3min vol alert — BTC just dropped 1.6%... [+92 chars]

▾ Action (3 tools)
  ⚙ get_market_data            BTC $75,212 | RSI 33.55 | ATR 0.29%
  ⚙ get_position               Short 0.265 @ $75,350 | PnL +0.10%
  ⚙ get_open_orders            1 orders (LMT $75,550)

▾ Reasoning (1247 chars total)
  Position fine — limit short still pending at 75550... [+447 chars]

▾ Action (4 tools)
  ⚙ get_derivatives_data       === Derivatives Data (BTC/USDT:USDT) === Funding...
  ⚙ get_recent_trades          === Recent Trades (BTC/USDT:USDT, last 300s, ...
  ⚙ get_higher_timeframe_view  === Higher Timeframe View (4h, BTC/USDT:USDT) ...
  ⚙ get_multi_timeframe_snapshot  === Multi-TF Snapshot (BTC/USDT:USDT) === ...

▾ Reasoning (1567 chars total)
  Liquidation cascade not panic — volume 6x avg... [+767 chars]

▾ Action (3 tools)
  ⚙ get_market_news            === Fear & Greed Index === Value: 26 / 100 ...
  ⚙ get_price_pivots           === Price Pivots (BTC/USDT:USDT, main TF: 1m) ...
  ⚙ get_macro_context          === Crypto Market === BTC.D: 58.00% | ETH.D: ...

▾ Reasoning (445 chars total)
  Got enough info. Decision: keep 75550 limit, set invalidation alert at
  75625 (reclaim of prior daily low = thesis weakened), 10min wake.

▾ Action (3 tools)
  ⚙ add_price_level_alert      below $74,890
  ⚙ add_price_level_alert      above $75,625
  ⚙ set_next_wake              10min

▾ Decision
  ## Situation Assessment: BTC Flash Crash

  **What happened**: BTC dropped ~1.6% in 10 minutes — from 76225 down
  to 74893 — on the heaviest 5m volume of the day (6x average)...
  [完整 markdown 内嵌]

───────────────────────────────────────────────────────────────────────────
  Tokens   41,947 cycle  |  Session 376k (avg 47k/cycle, 8 cycles)
  Cache    93.2% hit rate
  Duration 4.2s  |  Ended 18:14:27 UTC
═══════════════════════════════════════════════════════════════════════════
```

**Mockup 视觉注**：
- `▾ Action (4 tools)` 段内 tool 行 summary **保持当前形态**（前面 6 个工具走 fallback 80 chars 截断）—— 工具输出优化是 R2-8c 议题
- `{tool_name:<22}` padding 在长工具名（如 `get_multi_timeframe_snapshot` 28 chars）下会 spillover 破坏严格对齐 —— 维持 `display.py:361` 当前行为，dynamic 对齐归 R2-8c 议题

### 3.2.1 SCHEDULED 形态短 mockup（baseline 可视化）

`scheduled_tick` trigger 类型 metadata 为空，Header `Trigger` 行单词稀薄。sim #4 SCHEDULED cycle 占 9/15 多数，渲染样貌对比 ALERT 形态显著简短：

```
═══════════════════════════════════════════════════════════════════════════
  Cycle 8988  •  18:23:45 UTC  •  +5 min from prev
───────────────────────────────────────────────────────────────────────────
  Trigger    SCHEDULED
  State      Short 0.265 @ $75,350 (5x) | PnL +0.01% | Balance $9,981
═══════════════════════════════════════════════════════════════════════════

▾ Reasoning (84 chars total)
  Position is at breakeven, consolidation coiling. Hold short, 5min wake.

▾ Action (3 tools)
  ⚙ get_position               Short 0.265 @ $75,350 | PnL +0.01%
  ⚙ get_market_data            BTC $75,347 | RSI 55.00 | ATR 0.12%
  ⚙ set_next_wake              5min

▾ Decision
  Standing by. The short is at breakeven, consolidation is coiling tighter,
  and I'll be back in 5 minutes to see which way it resolves.

───────────────────────────────────────────────────────────────────────────
  Tokens   12,234 cycle  |  Session 388k (avg 43k/cycle, 9 cycles)
  Cache    94.1% hit rate
  Duration 2.8s  |  Ended 18:23:48 UTC
═══════════════════════════════════════════════════════════════════════════
```

注意 SCHEDULED `Trigger` 行**仅类型不带详情**（`scheduled_tick` 无 metadata）。30min 默认 vs `set_next_wake` 子类区分由 Header `+5 min from prev` 间接暴露——5min 间隔说明是显式 `set_next_wake(5min)` 不是 30min 兜底。subtype capture 增强属 R2-8c / W2 后期议题（§8.9）。

### 3.3 12 项 brainstorm 决议（final）

| # | 决议 | 状态 |
|---|---|---|
| **D1** | 时序架构（按 `ModelResponse` 分组遍历 `result.new_messages()`）| ✅ in-scope |
| **D2** | thinking 全部截 800 chars + `... [+N chars]` 标记（task-agnostic）| ✅ in-scope |
| **D3** | thinking 段头 `▾ Reasoning ({total} chars total)`，**不编号** | ✅ in-scope |
| ~~D4~~ | ~~长尾 fallback 升级 80→200~~ | ❌ → R2-8c |
| ~~D5~~ | ~~8 工具 L1 multi-line parser~~ | ❌ → R2-8c |
| **D6** | tool calls 按 ModelResponse 分组合并（`▾ Action (N tools)`，不细化 #1.1）| ✅ in-scope |
| **D7** | terminal 与 file 双 sink 共用 markup 字符串（color 在 file 端 stripped via `no_color=True`，行宽 wrapping 各自处理 — file `width=120` vs terminal 动态宽度）| ✅ in-scope |
| **D8** | forensic 路径仅渲染 Header + Footer + Decision 占位（partial messages 因 `result=None` 不可访问，agent.iter() 重构 → **N12a** candidate）| ✅ in-scope（spec self-review 校准）|
| **D9** | Session 末 panel 不做（事后 SQL 查替代）| ✅ |
| **D10** | 时序信息仅 in-memory `result.new_messages()` 提取，不动 R2-7 schema | ✅ in-scope |
| ~~D11~~ | ~~mixed C 形态~~ | ❌ → R2-8c |
| **D12** | session log rotation 不加 | ✅ |
| **D13** | retry-exhausted 路径（generic Exception 3 次失败）渲染 Header + Footer + `[cycle aborted — N attempts failed: <error class>]`，避免 W2 长 session 黑洞 | ✅ in-scope |
| **D14** | 新建 `SessionStats` class（与 TokenBudget 解耦），持 cycle_count / last_cycle_ended_at / total_tokens / avg；不随 daily reset 归零 | ✅ in-scope |
| **D15** | Footer 文案用 `Session` 替代模糊的 `Cumulative`，明示 session-级语义 | ✅ in-scope |
| **D16** | retry-exhausted 路径写 forensic AgentCycle 行（`execution_status="retry_exhausted"` sibling of R2-7 `usage_limit_exceeded`），避免 W2 SQL 黑洞 | ✅ in-scope |

**注（D6 trade-off 透明）**：当前不做 `▾ Action #1.1` deep-link 编号；未来若用户需要"cycle 内第 N 个 tool 失败"快速定位，可作为 R2-8c 议题或新议题加。

## 4. 段级设计契约

### 4.1 Cycle Header

#### 4.1.1 渲染契约（verbatim）

```
═══════════════════════════════════════════════════════════════════════════
  Cycle {short_id}  •  {start_ts_utc}  •  +{delta_min} min from prev
───────────────────────────────────────────────────────────────────────────
  Trigger    {type_upper} — {trigger_context_detail}
  State      {position_summary} | PnL {pnl_pct:+.2f}% | Balance ${balance:,.0f}
═══════════════════════════════════════════════════════════════════════════
```

#### 4.1.2 字段映射

| 字段 | 数据源 | 渲染规则 |
|---|---|---|
| `short_id` | `cycle_id[:4]`（保持当前行为）| 4 chars |
| `start_ts_utc` | `ctx.cycle_started_at`（**函数入口时刻**，含 capture IO；不取 DB `agent_cycles.created_at` —— 后者是 cycle **完成时**写入，比函数入口晚 1-3s + LLM call duration） | format `HH:MM:SS UTC` |
| `delta_min` | `(ctx.cycle_started_at - ctx.stats.last_cycle_ended_at).total_seconds() / 60` | int min；首 cycle 显示 `(first cycle)` |
| `type_upper` | `triggered_by.upper()` | `SCHEDULED` / `CONDITIONAL` / `ALERT` |
| `trigger_context_detail` | `trigger_context` JSON 解析 | 见 §4.1.3 |
| `position_summary` | `state_snapshot.position` JSON | 见 §4.1.4 |
| `pnl_pct` | `state_snapshot.position.pnl_pct` | 无仓位时整段为 `FLAT`，PnL 段省略 |
| `balance` | `state_snapshot.balance.total_usdt` | 无 balance 时省略 `Balance` 字段 |

#### 4.1.3 Trigger 详情渲染契约

`trigger_context` 是 JSON dict，结构来自 `src/services/cycle_capture.py:_capture_trigger_context`（line 24-80）。**字段集合 verbatim**：

| `trigger_context.type` | 实际字段集合（cycle_capture.py verbatim） | 渲染样例 |
|---|---|---|
| `scheduled_tick` | `{"type": "scheduled_tick"}`（仅类型，无 metadata）| **verbatim**: `Trigger    SCHEDULED`（无 em-dash 后缀，无尾随空白）—— 30min 默认 vs set_next_wake 子类区分由 Header `+X min from prev` + Footer `Duration` 间接暴露，scheduled subtype capture 增强属 W2 后期议题 |
| `fill` (conditional) | `{type, trigger_reason, symbol, side, position_side, amount, fill_price, fee, pnl, order_id, timestamp, is_full_close}` | `CONDITIONAL — {trigger_reason} {position_side} {symbol_short} {amount} @ ${fill_price:,.0f}, PnL {pnl:+.2f} USDT` 例：`CONDITIONAL — TP_FILL short BTC 0.265 @ $73,800, PnL +185.00 USDT` |
| `price_level_alert` (alert) | `{type, symbol, current_price, target_price, direction, reasoning, timestamp}` | `ALERT — {symbol_short} reached {current_price:,.0f} ({direction} ${target_price:,.0f} alert)` 例：`ALERT — BTC reached 75,448 (below $75,500 alert)` |
| `percentage_alert` (alert) | `{type, symbol, current_price, reference_price, change_pct, window_minutes, timestamp}` | `ALERT — vol {change_pct:+.1f}%/{window_minutes}min fired ({symbol_short} {reference_price:,.0f} → {current_price:,.0f})` 例：`ALERT — vol -1.6%/10min fired (BTC 76,225 → 75,448)` |

**Fallback**: 字段缺失 / 未知 type → 仅渲染 `{TYPE_UPPER}` 不带详情，warning log 记 `cycle_id` + dict keys。
**注 1**: `trigger_context.type` 字段值是 R2-7 spec 定义的稳定 contract；新增类型时 R2-8a 显式 fallback 到基础类型行为不破坏渲染。
**注 2**: R2-8a display 层入参是 in-memory dict（不是 DB JSON string），**不存在 JSON 解析失败边界**——该边界仅在 `cycle_capture._capture_trigger_context` 写入路径存在（已有 try/except → return None）。

#### 4.1.4 State 渲染契约

`state_snapshot` 是 JSON dict 含 `position` / `balance` / `market` / `pending_orders` / `active_alerts` / `_errors` / `_cycle_id`。R2-8a 仅消费 `position` + `balance`。

`state_snapshot.position` 字段集合 verbatim（来自 `cycle_capture.py:124-133`）：
`{symbol, side, contracts, entry_price, unrealized_pnl, leverage, liquidation_price, pnl_pct}` —— 8 个字段。R2-8a Header 仅读 `side` / `contracts` / `entry_price` / `leverage` / `pnl_pct` 5 字段；`unrealized_pnl` / `liquidation_price` / `symbol` 在 R2-8a 不消费（保留为 R2-8c / 未来议题数据源）。

| 仓位状态 | position JSON | 渲染 |
|---|---|---|
| 持仓 | 8 字段集 | `Short 0.265 @ $75,350 (5x) \| PnL +0.10% \| Balance $9,990` |
| 无仓位 | `null` | `FLAT \| Balance $10,000` |
| balance 缺失 | `balance = null` | `Short 0.265 @ $75,350 (5x) \| PnL +0.10%`（省略 Balance 段）|
| **state_snapshot 完全 None**（防御）| `null` | `[snapshot unavailable]` 占位 |
| **`_errors` 非空**（部分 fetch 失败）| 用现有非 null 字段 | 缺失字段省略；warning log 记 `_errors` 列表 |

### 4.2 Reasoning 段

#### 4.2.1 渲染契约

```
▾ Reasoning ({total} chars total)
  {body_with_2_space_indent}
  {if truncated: " ... [+{remaining} chars]" 末尾追加}
```

#### 4.2.2 截断算法

```python
from rich.markup import escape

def render_reasoning(thinking_text: str, max_chars: int = 800) -> str:
    total = len(thinking_text)
    if total <= max_chars:
        body = thinking_text
        suffix = ""
    else:
        body = thinking_text[:max_chars]
        remaining = total - max_chars
        suffix = f" ... [+{remaining} chars]"

    # P1 escape — body 是 LLM thinking content，可能含 [red] / [bold] 等字面值
    # （agent 在讨论 Rich 自身或代码示例时会输出），escape 后让 markup 字符 literal 显示
    indented = "\n".join(f"  {escape(line)}" for line in body.splitlines())
    return f"▾ Reasoning ({total} chars total)\n{indented}{suffix}"
```

**契约**:
- 硬截到 800 chars，不回退到段落边界（决议明确：与 D2 标记结合不会产生歧义）
- `... [+N chars]` 仅在截断时出现（≤ 800 字符无标记）
- 段头总长度 hint 不论是否截断都显示
- **body 必须 `rich.markup.escape()`** — thinking content 是 LLM 输出，attack surface 与 §4.4 Decision 段同型

#### 4.2.3 时序提取契约（与 `_extract_thinking_text` 行为分离）

**关键**：当前 `cli/app.py:51-64` 已有 `_extract_thinking_text` helper（R2-7 PR #35 落地，DB `agent_cycles.reasoning` 列写入路径）—— **全收集 ThinkingPart**（不限 1 个 per Response）。R2-8a 新增 `_extract_reasoning_per_response` 用于 session log 时序渲染——**仅取每 Response 首个 ThinkingPart**（与 pre-impl smoke baseline 一致）。

**两 helper 行为分离不追求单源真相**——理由：

| Helper | 行为 | 用途 | future-drift 行为 |
|---|---|---|---|
| `_extract_thinking_text` (R2-7 不动) | 全收集 ThinkingPart `\n\n`.join | DB `agent_cycles.reasoning` 列写入 | 多 ThinkingPart per Response 时仍全保留（无丢失）|
| `_extract_reasoning_per_response` (R2-8a 新增) | 每 Response 仅取 `parts[0]` | session log 时序渲染 | 限缩为首个 ThinkingPart（drift guard T-DG-1 兜底）|

```python
# R2-7 helper 不动（line 51-64 verbatim）
def _extract_thinking_text(messages) -> str | None:
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ThinkingPart):
                    parts.append(part.content)
    return "\n\n".join(parts) if parts else None


# R2-8a 新增 helper（独立）
def _extract_reasoning_per_response(messages: list) -> list[str | None]:
    """每个 ModelResponse 仅取首个 ThinkingPart 的 content（与 pre-impl smoke baseline 一致）。
    返回 list 长度 = ModelResponse 数；None = 该 Response 无 ThinkingPart。
    与 _extract_thinking_text 行为分离：渲染层接受'每 Response 首 ThinkingPart'限缩，DB 写入保持全收集。"""
    out: list[str | None] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            thinking_parts = [p for p in msg.parts if isinstance(p, ThinkingPart)]
            if thinking_parts:
                out.append(thinking_parts[0].content)
            else:
                out.append(None)
    return out
```

**为什么不共用单源真相**（避免 silent behavior change）：
- 若用 `_extract_thinking_text = "\n\n".join(_extract_reasoning_per_response(...))` 单源，**未来 pydantic-ai 引入"多 ThinkingPart per Response"时 DB 行为退化**（仅保留每 Response 首个，丢内容）
- AC21 声称"DB 写入行为不动"——单源真相违反此 contract
- 两 helper 各 ~10 行不算 DRY 违反；drift guard T-DG-1 验证两 helper 在 smoke baseline 下行为一致，pydantic-ai schema drift 时**两 helper 各自独立处理**

**Pre-impl smoke verified**（`.working/verify_message_structure.py`，2026-05-02）：
- 3 个 ModelResponse 都恰好 1 个 ThinkingPart
- ThinkingPart 在 `parts[0]`（先于 ToolCallPart）
- 跨 ModelResponse 顺序 = LLM 生成时序

#### 4.2.4 边界情况

| 情况 | 处置 |
|---|---|
| 非 thinking model（无 ThinkingPart）| 整段不渲染（直接 `▾ Action` 段） |
| ModelResponse 仅含 TextPart 无 ThinkingPart（仅最终）| 当前 ModelResponse 段省略 `▾ Reasoning`，进 `▾ Decision` |
| ThinkingPart `content == ""` | 段省略（视同非 thinking model 局部行为）|
| Pydantic-AI future schema 引入多 ThinkingPart 单 ModelResponse | 当前实施仅取 `thinking_parts[0]`；引入第二 ThinkingPart 时改为 `\n\n`.join — drift guard 测试断言"smoke 中观察到的 1 ThinkingPart per ModelResponse"作为 R2-8a baseline |

### 4.3 Action 段

#### 4.3.1 渲染契约

```
▾ Action ({n_tools} tools)
  ⚙ {tool_name:<22} {summary}
  ⚙ {tool_name:<22} {summary}
  ...
```

`{summary}` 由 `summarize_tool()` / `_fallback_summary()` 产生（**保持当前实施**，R2-8a 不动 parser）。

**ret / args fallback micro-spec**（避免 plan/impl 漏 case）：
```python
from rich.markup import escape

for part in tool_calls:
    # args 容错（同 cli/app.py:243-251 当前行为）
    try:
        args = part.args_as_dict()
    except Exception:
        args = None

    # ret lookup 失败兜底
    ret = tool_returns_lookup.get(part.tool_call_id)
    if ret is None:
        # 关联失败（极少 — pydantic-ai 应对齐 tool_call_id）
        logger.warning("tool_call_id mismatch for %s in cycle %s", part.tool_name, cycle_id)
        line = f"⚙ {part.tool_name:<22} [no return captured]"
    else:
        icon, summary = resolve_tool_display(part.tool_name, ret.content, ret.outcome, args)
        # P1 escape — summary 可能含 [bold] 等字面值（如 get_market_news headline 文本）
        line = f"{icon} {part.tool_name:<22} {escape(summary)}"
```

**Escape 范围**: tool summary 由 `summarize_tool` / `_fallback_summary` 产生——content 是 tool 返回（可能含 LLM 不可预知的字符如新闻条目 / 公告 / Markdown 片段），有 markup attack 面。**仅 body 部分 escape，框架 markup（icon / column padding）保留 Rich 渲染**。

**Multi-line summary 行为（R2-8c forward-compat 注）**：当前 `summarize_tool` 全部返回 single-line；未来 R2-8c 议题若改为 multi-line（含 `\n`），渲染层需要后续行 indent 与首行 22 字符 offset 对齐。R2-8a 不预留专门 hook，但 `_render_action` 在 `summary` 含 `\n` 时按 `\n` split 后逐行 prefix `  ` (2 spaces) + 不重复 icon 即可——**此 multi-line 处理 R2-8a 不实施**，留 R2-8c 议题接管。

#### 4.3.2 时序提取契约

```python
def extract_actions_per_response(messages: list) -> list[list[ToolCallPart]]:
    out = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            tool_calls = [p for p in msg.parts if isinstance(p, ToolCallPart)]
            out.append(tool_calls)
    return out
```

#### 4.3.3 失败 tool 行渲染

`✗` 与 `⚙` 区分维持 `is_tool_error` 现有逻辑（`display.py:274-289`）。失败行排版样式不变（与 `⚙` 同列对齐）。**视觉醒目化属 C1+C2 削除项，R2-8a out-of-scope**。

#### 4.3.4 边界情况

| 情况 | 处置 |
|---|---|
| ModelResponse 内 0 ToolCallPart（仅 ThinkingPart 或仅 TextPart）| `▾ Action` 段省略 |
| ToolReturnPart 与 ToolCallPart 关联失败 | 保持当前 `_call_args_by_id` 错配警告（`cli/app.py:258`）|
| 单 ModelResponse 含 1 ToolCallPart | 渲染 `▾ Action (1 tool)` —— 单复数形态 |

### 4.4 Decision 段

#### 4.4.1 渲染契约

```
▾ Decision
  {escape(result.output) — 完整 markdown 内嵌（2-space indent 一致），Rich markup 字符 literal 化}
```

**Rich markup escape（attack surface 防护）**：
- `console.print()` 默认按 Rich markup 解析字符串。LLM Decision 文本若含 `[red]` / `[bold]` / `[/]` 等字面值（agent 在讨论 Rich 自身 / 代码示例 / 嵌套 markdown 时可能输出），Rich 会解释为 markup → 渲染错乱或 `MarkupError`
- R2-8a "完整 markdown 内嵌"放大此风险面（vs 当前 short agent_output 风险低）
- **强制 escape**: `_render_decision` 内调 `rich.markup.escape(ctx.final_text)` 让 markup 字符 literal 显示

```python
from rich.markup import escape

def _render_decision(text: str) -> str:
    indented = "\n".join(f"  {line}" for line in escape(text).splitlines())
    return f"\n▾ Decision\n{indented}"
```

#### 4.4.2 数据源

`ctx.final_text` (= 最终 ModelResponse 内的 TextPart.content)，从 `result.output` 取。`format_cycle_output(ctx)` 入参由 CycleRenderContext 装载。

#### 4.4.3 边界情况

| 情况 | 处置 |
|---|---|
| 最终 ModelResponse 仅 ThinkingPart 无 TextPart（理论极少）| `▾ Decision` 段渲染 `[no decision text]` 占位 |
| forensic 路径（usage_limit_exceeded）| `▾ Decision` 渲染 `[no decision — usage limit exceeded; partial messages unavailable]`（**`{n} tool calls` 版本删除** — `result=None` 不可数 tool calls；详见 §6.4 contract）|
| retry-exhausted 路径（3 attempts failed）| `▾ Decision` 渲染 `[cycle aborted — 3 attempts failed: <error class>: <msg>]`（详见 §6.5 contract）|
| `result.output == ""` | `▾ Decision` 渲染 `[empty decision text]` 占位 |

### 4.5 Cycle Footer

#### 4.5.1 渲染契约

```
───────────────────────────────────────────────────────────────────────────
  Tokens   {cycle_tokens:,} cycle  |  Session {session_total_k}k (avg {session_avg_k}k/cycle, {session_cycle_count} cycles)
  Cache    {hit_rate:.1f}% hit rate
  Duration {duration_sec:.1f}s  |  Ended {end_ts_utc}
═══════════════════════════════════════════════════════════════════════════
```

**Forensic / retry-exhausted 路径变体**：保持三行布局，仅 `Cache` 行内容替换为 `N/A (forensic)` 或 `N/A (aborted)`。

#### 4.5.2 字段映射

| 字段 | 数据源 | 当前是否已计算 |
|---|---|---|
| `cycle_tokens` | `usage.total_tokens`（已传入）| ✅ |
| `session_total_k` | **projected**: `(stats.total_tokens + ctx.cycle_tokens) / 1000` (rounded) | 新增 SessionStats — 含当前 cycle |
| `session_avg_k` | **projected**: `((stats.total_tokens + ctx.cycle_tokens) / (stats.cycle_count + 1)) / 1000` (rounded) | 新增 SessionStats — 含当前 cycle |
| `session_cycle_count` | **projected**: `stats.cycle_count + 1` | 新增 SessionStats — 含当前 cycle |
| `hit_rate` | `ctx.cache_hit_rate`（caller 传入，None → "N/A (forensic)" / "N/A (aborted)" 分支；footer 不重算）| ✅ 已计算 (`app.py:231`) — caller 直接传入 |
| `duration_sec` | `cycle_ended_at - cycle_started_at`（in `run_agent_cycle` capture）| 新增 capture |
| `end_ts_utc` | `cycle_ended_at.strftime("%H:%M:%S UTC")` | 新增 capture |

#### 4.5.3 SessionStats（新 class，与 TokenBudget 解耦）

**问题**：把 `cycle_count` / `last_cycle_ended_at` 塞进 TokenBudget 是 layering anti-pattern——TokenBudget 是 daily token 预算簿（`_used` 每日 reset），而 cycle 时序 metric 应是 session 语义（不跨日重置）。两者放一起会出现 latent bug：跨夜 day 2 第 1 cycle Header 显示 `(first cycle of day)` 抹除了真实跨夜 wake interval（用户看不到"距前 cycle 540 min"这个有意义信号）。

**设计**：新建 `SessionStats` class，session-级 lifecycle（不随 daily reset 归零）。TokenBudget 维持原职责（daily token 预算簿）不动。

```python
class SessionStats:
    """Session-level cycle tracking — independent of daily token budget reset.

    Lifecycle: 1 instance per session, lives from session start to shutdown.
    NOT reset on daily token budget reset (跨夜 wake interval 仍可见).
    """
    def __init__(self):
        self._cycle_count = 0
        self._total_tokens = 0
        self._last_cycle_ended_at: datetime | None = None

    def record_cycle(self, cycle_tokens: int, cycle_ended_at: datetime) -> None:
        """Called once per cycle, after format_cycle_output renders.
        forensic / retry-exhausted cycles 也调用此（cycle_tokens=0），
        消耗 trigger 容量但无 token 产出。"""
        self._cycle_count += 1
        self._total_tokens += cycle_tokens
        self._last_cycle_ended_at = cycle_ended_at

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def avg_tokens_per_cycle(self) -> int:
        if self._cycle_count == 0:
            return 0
        return self._total_tokens // self._cycle_count

    @property
    def last_cycle_ended_at(self) -> datetime | None:
        return self._last_cycle_ended_at
```

**TokenBudget 不动**——保持当前 `_used` / `_check_reset` / `daily_max` / `remaining` / `exhausted` 职责纯净。

**Footer 文案明示语义**：用 `Session` 标识（不用 `Cumulative` 模糊词），强调 session 累计而非 daily 累计：
```
Tokens   {cycle_tokens:,} cycle  |  Session {total_tokens/1000:.0f}k (avg {avg/1000:.0f}k/cycle, {cycle_count} cycles)
```

**lifecycle 边界**：
- forensic / retry-exhausted cycle 调 `record_cycle(0, end_ts)` —— 计入 cycle_count 但 total_tokens += 0
- daily token budget reset 时 SessionStats 不动（last_cycle_ended_at 跨夜可见 → "+540 min from prev"）
- session 结束时 SessionStats instance 释放，新 session 重新创建

**Footer projected stats（P1 off-by-one 解决）**：

`format_cycle_output(ctx)` 在 `stats.record_cycle()` **之前**调用——若 footer 直接读 `stats.cycle_count` / `stats.total_tokens` 会少算当前 cycle，footer 显示 N-1 cycles 而非 N（与 mockup §3.2 "8 cycles 含当前" 直觉冲突）。

**修法**：footer 渲染层用 **projected stats**（render 时显式 + 1 / + cycle_tokens），不改 lifecycle 顺序：

```python
def _render_footer(ctx: CycleRenderContext) -> str:
    # Projected — 含当前 cycle (stats.record_cycle 在 render 后才调)
    proj_total = ctx.stats.total_tokens + ctx.cycle_tokens
    proj_count = ctx.stats.cycle_count + 1
    proj_avg = proj_total // proj_count if proj_count > 0 else 0
    ...
```

**为什么不改 lifecycle 顺序（先 record 后 render）**：
- record 会更新 `_last_cycle_ended_at` 到 current cycle —— 如果 render 时取 `stats.last_cycle_ended_at` 计算 "+X min from prev"，会变成自指（current vs current = 0 min）
- projected stats 仅 footer 显示层 + 1/+cycle_tokens，不破坏 stats 内部不变量

**Caveat — forensic cycle_tokens=0 是统计假象（W2 排查 baseline）**：

R2-7 spec §3.1 #3 决议 `UsageLimitExceeded` 路径不携带 partial usage（pydantic-ai API 限制），R2-7 forensic 写入 `tokens_consumed=0`。但**物理含义不是 0**——`USAGE_LIMITS_PER_CYCLE` (`app.py:47`) 设 200k token，forensic = LLM 实际消耗已达 200k 上限抛出，**实际消耗近 200k**。

R2-8a SessionStats `total_tokens += 0` 沿用 R2-7 决议（不改 token 统计语义）。但需要 W2 排查时记忆此 caveat：

| 场景 | 表现 | 真实情况 |
|---|---|---|
| daily budget 烧光（TokenBudget._used = 10M） + SessionStats avg 偏低 | `Session avg 30k/cycle` 但 daily budget 已耗尽 | 隐含多个 forensic cycle 各消耗 ~200k 但 total_tokens 计 0 |
| forensic vs retry-exhausted 表面同型 | 两者 cycle_tokens=0 | forensic = 实际消耗 ~200k；retry-exhausted = LLM 未调成功 ~0 token |

**处置**：本 spec 仅记 caveat，**不**改 token 统计语义。upper-bound 估算（forensic 计 USAGE_LIMITS upper bound）属 R2-7 决议演进议题，归 `project_w2_ops_backlog`（与 S1-S5 同档 W2 ops candidate）。

## 5. 实施策略

### 5.1 数据流

```
run_agent_cycle(...)                         # cli/app.py
  └─ result = await agent.run(...)
  └─ tool_calls + thinking_text 解析（已存在）
  └─ AgentCycle DB 写入（已存在，不动）
  └─ console.print(format_cycle_output(...))  # ← 入参签名变更

# 入参封装为 CycleRenderContext dataclass（避免 12 入参 smell）：
@dataclass(frozen=True)
class CycleRenderContext:
    cycle_id: str
    trigger_type: str               # "scheduled" / "conditional" / "alert"
    trigger_context: dict | None    # in-memory dict from _capture_trigger_context（不是 JSON string）
    state_snapshot: dict | None     # in-memory dict from _capture_state_snapshot（永非 None per R2-7 contract，但加防御）
    messages: list | None           # result.new_messages() — None for forensic / retry-exhausted
    final_text: str | None          # result.output (= final TextPart.content) — None for forensic / retry-exhausted
    cycle_tokens: int               # usage.total_tokens (forensic / retry-exhausted = 0)
    stats: SessionStats             # session-level cycle tracker（含 last_cycle_ended_at 取数路径）
    cache_hit_rate: float | None    # 已计算好（caller 传入），None 触发 footer "N/A"; forensic / retry-exhausted = None
    cycle_started_at: datetime      # **run_agent_cycle 函数入口时刻**（含 trigger/state capture IO 的 1-3s）— 实墙时间直觉
    cycle_ended_at: datetime        # cycle 完成（含 DB 写入）后时刻
    forensic_reason: str | None     # None=正常路径; "usage_limit_exceeded"=forensic; "aborted: <error class>: <msg[:200]>"=retry-exhausted


def format_cycle_output(ctx: CycleRenderContext) -> str:
    ...

# 入参变化：
#   format_cycle_output(cycle_id, trigger_type, tool_calls, agent_output, tokens, budget_remaining)  # 旧 6 参
#       → format_cycle_output(ctx: CycleRenderContext)                                                # 新 1 参
#
# CycleRenderContext 由 run_agent_cycle 装填一次（line 132+），3 路径（正常 / forensic / retry-exhausted）共用同一 dataclass。
```

**关键字段定义（避免 plan/impl 歧义）**：
- `cycle_started_at`：**`run_agent_cycle` 函数入口时刻** — 含 trigger/state capture 的 5 次 IO（fetch_positions/balance/ticker/orders + alerts ~1-3s）。Footer `Duration` 反映用户感知的实墙时间，不仅是 LLM call duration。
- `cache_hit_rate`：**caller 算好传入**（已有 `app.py:231` 的 `hit_rate` 计算），footer 不重算。`None` 自然分支 forensic / retry-exhausted（渲染 `Cache N/A (forensic)` / `Cache N/A (aborted)`）。
- `prev_cycle_ended_at`：**不作 ctx 字段** — 从 `ctx.stats.last_cycle_ended_at` 读取（避免 caller 重复传）。

### 5.2 时序遍历核心算法

```python
def format_cycle_output(ctx: CycleRenderContext) -> str:
    lines = []

    # === Header ===
    lines.append(_render_header(ctx))   # 内部读 ctx.cycle_id / trigger_type / trigger_context / state_snapshot / cycle_started_at / stats.last_cycle_ended_at

    # === Forensic / retry-exhausted 路径短路 ===
    if ctx.messages is None:
        # ctx.forensic_reason ∈ {"usage_limit_exceeded", "aborted: <error class>: <msg[:200]>"}
        # 构造 raw placeholder（不 pre-escape err_part）—— 然后整体 escape 一次
        # 双 escape 会让反斜杠可见（"RuntimeError: \[red]boom\[/]" 不是 user-friendly 字面值）
        if ctx.forensic_reason.startswith("aborted"):
            err_part = ctx.forensic_reason[len('aborted: '):]   # raw, 不 escape
            placeholder = f"[cycle aborted — 3 attempts failed: {err_part}]"
        else:  # usage_limit_exceeded
            placeholder = "[no decision — usage limit exceeded; partial messages unavailable]"
        # 仅一次 escape（外层方括号 + 内层动态 markup 字面值统一处理）
        # 终端显示自然字面值 "RuntimeError: [red]boom[/]" 不带反斜杠
        lines.append(f"\n▾ Decision\n  {escape(placeholder)}")
        lines.append(_render_footer(ctx))   # ctx.cache_hit_rate is None → footer 自动渲染 "N/A (forensic)" / "N/A (aborted)"（按 forensic_reason 分支）
        return "\n".join(lines)

    # === 正常路径：时序段 ===

    # === Build tool_call_id → ToolReturnPart map ===
    # _render_action 内 resolve_tool_display 需要 ToolReturnPart.content + outcome
    # （icon 经 is_tool_error / summary 经 summarize_tool 计算）。ToolReturnPart 在
    # ModelRequest.parts 里，与 ToolCallPart 通过 tool_call_id 关联。
    tool_returns_lookup: dict[str, ToolReturnPart] = {}
    for msg in ctx.messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_returns_lookup[part.tool_call_id] = part

    # === ②③ 时序段 ===
    response_msgs = [m for m in ctx.messages if isinstance(m, ModelResponse)]
    n = len(response_msgs)

    for i, mr in enumerate(response_msgs):
        thinking = next(
            (p.content for p in mr.parts if isinstance(p, ThinkingPart)),
            None,
        )
        tool_calls = [p for p in mr.parts if isinstance(p, ToolCallPart)]
        text_parts = [p for p in mr.parts if isinstance(p, TextPart)]

        if thinking:
            lines.append(_render_reasoning(thinking))

        if tool_calls:
            # _render_action 内对每个 ToolCallPart：见 §4.3 ret/args fallback micro-spec
            lines.append(_render_action(tool_calls, tool_returns_lookup))

        if text_parts and i == n - 1:
            # 最终 TextPart = Decision (Rich markup escape — see §4.4)
            lines.append(_render_decision(text_parts[0].content))

    # === Footer ===
    lines.append(_render_footer(ctx))

    return "\n".join(lines)
```

### 5.3 文件影响清单

| 文件 | 改动 |
|---|---|
| `src/cli/display.py` | `format_cycle_output(ctx: CycleRenderContext)` 签名重构；新增 `CycleRenderContext` dataclass + `_render_header` / `_render_reasoning` / `_render_action` / `_render_decision` / `_render_footer` private helper；保持 `summarize_tool` / `_fallback_summary` / `is_tool_error` 等当前 parser 不动；`_render_decision` 调 `rich.markup.escape()` 防 markup attack（详见 §4.4）|
| `src/cli/session_state.py` (**新文件**) | 新建 `SessionStats` class（详见 §4.5.3）。**独立文件原因**：app.py 已 633 行偏大，session-级 lifecycle 工具类与 cycle 主循环逻辑职责分离更清晰。`TokenBudget` 暂留 app.py 不强制重构（cleanup follow-up 留 W2 后期 mini-PR）|
| `src/cli/app.py` (TokenBudget) | **不动**（保持 daily token 预算簿职责纯净；cumulative/avg/cycle_count 由 SessionStats 接管）|
| `src/cli/app.py` (`run_agent_cycle`) | 装填 `CycleRenderContext`；收集 `cycle_started_at`（**函数入口时刻**，含 capture IO）/ `cycle_ended_at`（3 路径都 capture）；3 路径（正常 / forensic / retry-exhausted）都调 `format_cycle_output(ctx)` + `stats.record_cycle()`；retry-exhausted 路径补写 forensic AgentCycle (`execution_status="retry_exhausted"`)；新增 `_extract_reasoning_per_response` helper（独立于 `_extract_thinking_text`，详见 §4.2.3）；`_extract_thinking_text` 行为不动 |
| `src/cli/app.py` (`build_services`) | return signature 从 4-tuple `(exchange, deps, agent, budget)` 扩展为 **5-tuple** `(exchange, deps, agent, budget, stats)`；创建 `SessionStats` instance（与 budget 同 lifecycle）；import from `src/cli/session_state.py` |
| `src/cli/app.py` (`main` 调用方) | 解构 build_services 5-tuple → 注入 stats 到 `run_agent_cycle()` |
| `tests/test_display_cycle.py` | 现有 `format_cycle_output` 测试扩展 — 时序渲染、截断、边界情况、forensic/retry-exhausted 路径测试矩阵（详见 §7）|
| `tests/test_cycle_log.py` | 现有 `run_agent_cycle` + `TokenBudget` 测试更新签名（含 SessionStats 注入 + 3 路径覆盖）|
| `tests/test_usage_limits.py` (8 处) | 现有 `run_agent_cycle(...)` 调用——若 `stats=None` 默认值（见下方注），无需改 |
| `tests/test_wizard.py` (3 处 line 481/526/552) | `build_services` 4-tuple 解构 → 5-tuple |
| `tests/test_n3_wiring.py` (5 处 line 92/111/128/141/158) | 同上 4-tuple → 5-tuple |
| `tests/test_okx_algo_normalization.py` (line 67-69) | **不属 5-tuple 同步** — 仅调用 `build_services(...)` raise 测试不解构 return；但 `build_services` 内部新增 `SessionStats()` 构造可能影响 patch 链覆盖，plan 阶段验证 patch 是否需扩展 |
| `docs/metrics/agent-cycles-schema.md` | `execution_status` 列描述加 `retry_exhausted` enum 值 sibling of `usage_limit_exceeded` |
| `src/storage/models.py:94` 注释 | `# ok / usage_limit_exceeded` 改为 `# ok / usage_limit_exceeded / retry_exhausted` |
| `tests/test_session_state.py` (**新文件**) | SessionStats class 单测（cycle_count / total_tokens / avg / last_cycle_ended_at lifecycle）|
| `tests/conftest.py` 或 `tests/fixtures/cycle_fixtures.py` (**新 builder**) | `build_cycle_messages(thinking_segments, tool_call_segments, final_text)` fixture builder — in-memory 构造 `list[ModelResponse / ModelRequest]`，**不**持久化二进制文件（pickle 有 untrusted exec 风险；现有 fixture format 是 plain JSON 不能直接 dump pydantic-ai dataclass）；字段值参考 `.working/verify_message_structure.py` 实跑 capture 的结构（如 thinking 长度分布 / tool_name 集合）|

**注 1**: 之前 spec 列入 `tests/test_cli.py` 是错的——该文件无 `format_cycle_output` / `run_agent_cycle` / `TokenBudget` 引用（grep 确认 0 命中）。

**注 2 — `run_agent_cycle(stats=None)` 默认值**：避免破坏 `tests/test_usage_limits.py` (8 处) 既有签名。`stats=None` 时函数内部用 `_DummySessionStats()`（cycle_count/total_tokens 永 0，`record_cycle` no-op，`last_cycle_ended_at` 永 None）。仅集成测试 / `cli/app.py main` / `tests/test_cycle_log.py` 需要显式传 `stats`。Spec impl 时建议 `_DummySessionStats` 是 module-level singleton，避免每 cycle 实例化。

### 5.4 SessionStats 持久化（替代之前 TokenBudget 扩展方案）

**生命周期**：与 session（cli session）同 lifespan，由 `cli/app.py` `build_services()` 创建 1 个 instance 注入 `run_agent_cycle()`。

```
session start (build_services)
  └─ stats = SessionStats()
  └─ each cycle (run_agent_cycle):
      └─ cycle_started_at = datetime.now(UTC)         # 函数入口时刻（含 capture IO）
      └─ trigger_context_var = _capture_trigger_context(...)
      └─ state_snapshot_var = await _capture_state_snapshot(...)
      └─ ... (LLM call / forensic / retry-exhausted 三路径之一，决定 messages / final_text / cycle_tokens / forensic_reason)
      └─ cycle_ended_at = datetime.now(UTC)
      └─ ctx = CycleRenderContext(
             cycle_id=cycle_id, trigger_type=trigger_type,
             trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
             messages=messages, final_text=final_text,
             cycle_tokens=cycle_tokens, stats=stats,
             cache_hit_rate=hit_rate, cycle_started_at=cycle_started_at,
             cycle_ended_at=cycle_ended_at, forensic_reason=forensic_reason,
         )
      └─ console.print(format_cycle_output(ctx))      # 1 入参，无散列入参
      └─ stats.record_cycle(cycle_tokens, cycle_ended_at)
session end (shutdown)
  └─ stats instance 释放
```

**首 cycle 行为**：`stats.last_cycle_ended_at is None` → Header 渲染 `(first cycle)`。

**跨日行为**：SessionStats **不随** TokenBudget 的 daily reset 归零；day 2 第 1 cycle Header 仍显示 `+540 min from prev`（用户看到跨夜 wake interval）—— 这是设计意图（与 P1-4 跨日 cycle_count 语义同型解决）。

**forensic / retry-exhausted cycle**：仍调 `stats.record_cycle(0, end_ts)` —— `cycle_count` 计入但 `total_tokens` 不增（avg 反映出 trigger 容量浪费）。

## 6. 边界情况完整清单

### 6.1 Trigger context 边界

| 情况 | 处置 | 测试 ID |
|---|---|---|
| `trigger_context = None`（forensic 路径 / 早期未 capture）| Header `Trigger` 行仅 `{TYPE_UPPER}` 不带详情 | T-EH-1 |
| `trigger_context.type` 未知（schema drift / 新增类型）| 渲染 `{TYPE_UPPER}` 不带详情 + warning log | T-EH-2 |
| `trigger_context.fill_price` 缺失（conditional fill 无价格）| 显示 `CONDITIONAL — {trigger_reason}` 不带价格 | T-EH-3 |

**注**: 之前列入的"trigger_context JSON 解析失败"边界已删除——R2-8a display 层入参是 in-memory dict（来自 `_capture_trigger_context` 直接返回），**不**消费 DB JSON string。JSON 解析失败的边界仅在 cycle_capture 写入路径存在（已有 try/except → return None，与 T-EH-1 同型）。

### 6.2 State snapshot 边界

| 情况 | 处置 | 测试 ID |
|---|---|---|
| `state_snapshot = None`（防御性 — R2-7 contract 永非 None）| Header `State` 行 `[snapshot unavailable]` | T-ES-1 |
| `state_snapshot.position = None` 无仓位 | `FLAT` | T-ES-2 |
| `state_snapshot.balance = None`（fetch 失败）| 省略 `Balance` 字段 + warning log `_errors` | T-ES-3 |
| `state_snapshot._errors` 非空 | 用现有非 null 字段；warning log `_errors` | T-ES-4 |
| `state_snapshot.position.pnl_pct = None`（计算失败 / 0 notional）| 省略 PnL 段 | T-ES-5 |

### 6.3 Reasoning / thinking 边界

| 情况 | 处置 | 测试 ID |
|---|---|---|
| 非 thinking model（无 ThinkingPart）| 整段不渲染 | T-RE-1 |
| ThinkingPart `content == ""` | 段省略（与 T-RE-1 同型）| T-RE-2 |
| thinking < 800 chars | 完整渲染，无截断标记 | T-RE-3 |
| thinking ≥ 800 chars | 硬截 800 + `... [+N chars]` | T-RE-4 |
| thinking 含特殊 markdown / `\n` | 完整保留（带 2-space indent）| T-RE-5 |
| 单 ModelResponse 多 ThinkingPart（drift guard）| 仅取 `parts[0]`，记 warning + 测试断言"smoke baseline" | T-RE-6 |

### 6.4 Forensic 路径（usage_limit_exceeded）

R2-7 spec §3.1 #3 已确认 `UsageLimitExceeded` 不携带 partial usage，R2-7 forensic 写入 `reasoning=NULL` + `decision=NULL`。

**关键约束（spec self-review 校准）**：`cli/app.py:181` `result = None` 初始化；`UsageLimitExceeded` 在 `await agent.run(...)` 内部抛出时，`result` 变量**保持 `None` 永远不被赋值**。pydantic-ai 的 `agent.run()` 在 raise 时不暴露内部累计的 messages —— 访问 partial messages 必须切到 `agent.iter()` 流式 API（cycle 主循环非平凡重构，工作量超出 R2-8a scope）。

**因此**：forensic 路径**无 partial messages 可渲染** —— 仅渲染 Header + Footer + Decision 占位（不渲染 partial Reasoning / Action 段）。`agent.iter()` 重构留作 **N12a** candidate（独立于 N12b schema 升级议题）。

R2-8a forensic 渲染契约：

```
═══════════════════════════════════════════════════════════════════════════
  Cycle 9f57  •  18:14:23 UTC  •  +12 min from prev
───────────────────────────────────────────────────────────────────────────
  Trigger    ALERT — vol -1.6%/10min fired (BTC 76,225 → 75,448)
  State      Short 0.265 @ $75,350 (5x) | PnL +0.10% | Balance $9,990
═══════════════════════════════════════════════════════════════════════════

▾ Decision
  [no decision — usage limit exceeded; partial messages unavailable]

───────────────────────────────────────────────────────────────────────────
  Tokens   0 cycle  |  Session 379k (avg 47k/cycle, 8 cycles)
  Cache    N/A (forensic)
  Duration {sec}s  |  Ended {end_ts}
═══════════════════════════════════════════════════════════════════════════
```

**实施关键**:
- forensic 路径调用方（`cli/app.py:194-209`）当前 `return None`
- R2-8a 改为：在 except 块内**显式 capture `cycle_ended_at = datetime.now(UTC)`**（DB 写入 + format_cycle_output + stats.record_cycle 都需要），然后：
  1. 写 forensic AgentCycle（已有 R2-7 行为不动）
  2. 装填 `CycleRenderContext(messages=None, final_text=None, forensic_reason="usage_limit_exceeded", cycle_ended_at=cycle_ended_at, cache_hit_rate=None, ...)` 调 `format_cycle_output(ctx)`
  3. 调 `stats.record_cycle(cycle_tokens=0, cycle_ended_at)` 持久化
  4. `return None`（保持 retry loop 行为）
- forensic cycle 计入 SessionStats `cycle_count` 但 `cycle_tokens=0`（消耗实墙时间 + trigger 容量，但无 token）—— 详见 §4.5.3 SessionStats lifecycle

| 情况 | 处置 | 测试 ID |
|---|---|---|
| forensic 路径（usage_limit_exceeded）| 渲染 Header + Footer + `[no decision — usage limit exceeded; partial messages unavailable]` | T-FO-1 |
| forensic + SessionStats 累计 | `forensic_cycle += 1`，`total_tokens += 0` → avg 拉低（统计行为符合直觉 — forensic 消耗 trigger 容量但无产出）| T-FO-2 |

### 6.5 Retry-exhausted 路径（generic Exception 3 次重试都失败）

`cli/app.py:210-217` generic Exception 路径：3 次重试都失败 → `return None`。当前 cycle 在 session log 上**完全消失**，W2 24h 长 session 这是"不可见黑洞"——用户排查 trigger 后丢失的 cycle 找不到。

R2-8a retry-exhausted 渲染契约：

```
═══════════════════════════════════════════════════════════════════════════
  Cycle 9f57  •  18:14:23 UTC  •  +12 min from prev
───────────────────────────────────────────────────────────────────────────
  Trigger    ALERT — vol -1.6%/10min fired (BTC 76,225 → 75,448)
  State      Short 0.265 @ $75,350 (5x) | PnL +0.10% | Balance $9,990
═══════════════════════════════════════════════════════════════════════════

▾ Decision
  [cycle aborted — 3 attempts failed: <last error class>: <last error message[:200]>]

───────────────────────────────────────────────────────────────────────────
  Tokens   0 cycle  |  Session 379k (avg 47k/cycle, 8 cycles)
  Cache    N/A (aborted)
  Duration {sec}s  |  Ended {end_ts}
═══════════════════════════════════════════════════════════════════════════
```

**实施关键**:
- `cli/app.py:215-217` `else: return None` 改为：先 capture `cycle_ended_at = datetime.now(UTC)`，**再写 forensic AgentCycle 行**（与 R2-7 usage_limit_exceeded 同型），然后调 `format_cycle_output(ctx)` 渲染 session log
- retry-exhausted forensic AgentCycle 写入字段：
  ```python
  AgentCycle(
      session_id=deps.session_id, cycle_id=cycle_id,
      triggered_by=trigger_type,
      trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
      state_snapshot=json.dumps(state_snapshot_var),
      reasoning=None, decision=None,
      execution_status="retry_exhausted",   # ← 新 enum 值，sibling of R2-7 "usage_limit_exceeded"
      model_id=model_id_var, tokens_consumed=0,
  )
  ```
- retry-exhausted cycle 计入 SessionStats（同 forensic — 消耗 trigger 容量）

**为什么写 DB（与 forensic 同型）**：
- R2-7 已建立"forensic cycle 也写 AgentCycle 行"模式（usage_limit_exceeded）
- W2 观察期 SQL 分析基于 `agent_cycles` 聚合：`SELECT * FROM agent_cycles WHERE execution_status != 'ok'` 自然涵盖两类 forensic
- 不写 DB 会让 W2 SQL 分析黑洞 retry-exhausted 失败模式（与 P0-1 P1-7 议题"避免黑洞"诉求矛盾）
- `agent_cycles.execution_status` 是 `String(30)` 不是 strict enum，新增 `retry_exhausted` 值 backward-compatible 不需 Alembic migration

| 情况 | 处置 | 测试 ID |
|---|---|---|
| 3 次重试都失败 | 渲染 Header + Footer + `[cycle aborted — 3 attempts failed: ConnectionError: ...]` | T-EX-1 |
| Error message 超 200 chars | 截断到 200 + `...` | T-EX-2 |
| retry-exhausted + SessionStats 累计 | 同 forensic（`cycle_count += 1`, `total_tokens += 0`）| T-EX-3 |

### 6.6 时序边界

| 情况 | 处置 | 测试 ID |
|---|---|---|
| 首 cycle（无 prev_cycle_ended_at）| Header `+0 min from prev` 改为 `(first cycle)` | T-TS-1 |
| 跨日（prev_cycle_ended_at 在前一天 — SessionStats **不重置** last_cycle_ended_at）| 正常显示 `+540 min from prev`（用户能看到跨夜 wake interval）| T-TS-2 |
| Cycle 持续超长（如 60s+ LLM 卡）| `Duration 67.3s` 正常显示 | T-TS-3 |

### 6.7 Tool calls 边界

| 情况 | 处置 | 测试 ID |
|---|---|---|
| ModelResponse 内 0 ToolCallPart（仅 ThinkingPart 或仅 TextPart）| `▾ Action` 段省略 | T-TC-1 |
| 整 cycle 0 tool call（极少 — agent 直接 thinking → text）| 仅 1 个 `▾ Reasoning` + `▾ Decision`，无任何 `▾ Action` | T-TC-2 |
| 单 ModelResponse 含 1 ToolCallPart | `▾ Action (1 tool)` 单数 | T-TC-3 |
| `tool_returns_lookup.get(part.tool_call_id)` is None（关联失败）| 渲染 `⚙ {tool_name:<22} [no return captured]` + warning log | T-TC-4 |
| `part.args_as_dict()` raise Exception | try/except → `args=None`，与当前 cli/app.py:243-251 行为一致 | T-TC-5 |

## 7. 测试矩阵

### 7.1 Helper 单元测试（11 cases）

| ID | 目标 | 输入 | 期望 |
|---|---|---|---|
| T-RH-1 | `_render_header` 完整字段 | mock 完整 trigger_context + state_snapshot | 符合 §4.1.1 verbatim |
| T-RH-2 | `_render_header` first cycle | `prev_cycle_ended_at=None` | 含 `(first cycle)` |
| T-RH-3 | `_render_header` trigger_context=None | None | 仅 `{TYPE_UPPER}` 不带详情 |
| T-RR-1 | `_render_reasoning` < 800 | 412 chars | 无 `... [+N chars]` |
| T-RR-2 | `_render_reasoning` = 800 | 800 chars | 无标记 |
| T-RR-3 | `_render_reasoning` > 800 | 1547 chars | `... [+747 chars]` 标记 |
| T-RR-4 | `_render_reasoning` 含 `\n` | 多行 thinking | 2-space indent 每行 |
| T-RA-1 | `_render_action` 多 tools | 3 ToolCallPart | `▾ Action (3 tools)` |
| T-RA-2 | `_render_action` 单 tool | 1 ToolCallPart | `▾ Action (1 tool)` 单数 |
| T-RD-1 | `_render_decision` 完整 markdown | 长 markdown text | 2-space indent 完整保留 |
| T-RF-1 | `_render_footer` 完整 | mock budget + cache | 符合 §4.5.1 verbatim |

### 7.2 集成测试（11 cases）

| ID | 目标 | 输入 | 期望 |
|---|---|---|---|
| T-INT-1a | 5 段架构**结构断言**（含 builder 构造的真型 fixture）| `cycle_fixtures.build_cycle_messages(...)` 输出（结构参数参考 `.working/verify_message_structure.py` 实测：3 ModelResponse / 1 ThinkingPart per Response / ThinkingPart 在 `parts[0]`）| 断言 ① Header 行结构 / ② `▾ Reasoning` 与 `▾ Action` 交织 / ③ `▾ Decision` 段后跟 ④ Footer / 不做 byte-verbatim 比对 |
| T-INT-1b | 完整 cycle verbatim（hand-crafted fixture）| 精心构造的 messages 列表（thinking 长度可控）| mockup §3.2 verbatim 比对 |
| T-INT-2 | 非 thinking model | 无 ThinkingPart messages | 跳过 `▾ Reasoning`，`▾ Action` 紧接 Header |
| T-INT-3 | 0 tool call cycle | 仅 1 ModelResponse with TextPart | 仅 1 Reasoning + Decision |
| T-INT-4 | forensic usage_limit_exceeded | `messages=None` + `forensic_reason="usage_limit_exceeded"` | 渲染 Header + Footer + `[no decision — usage limit exceeded; partial messages unavailable]` + `Cache N/A (forensic)` |
| T-INT-5 | retry-exhausted 路径 | `messages=None` + `forensic_reason="aborted: ConnectionError: timeout"` | 渲染 Header + Footer + `[cycle aborted — 3 attempts failed: ConnectionError: timeout]` + `Cache N/A (aborted)` |
| T-INT-5b | retry-exhausted error message **含 markup 字面值** | `forensic_reason="aborted: RuntimeError: [red]boom[/]"` | placeholder 仅一次 escape（不 pre-escape err_part 避免双重转义），Rich 渲染后**终端显示自然字面值** `[cycle aborted — 3 attempts failed: RuntimeError: [red]boom[/]]`（无可见反斜杠）+ 不抛 MarkupError |
| T-INT-6 | SessionStats session 累计 | 5 cycles 累加（含 1 forensic）| footer `Session {sum}k (avg {sum/5}k/cycle, 5 cycles)`（forensic 也计入 cycle_count，total_tokens 不增）|
| T-INT-7 | Cache hit rate 计算 | cache_hit=92, cache_miss=8 | footer `Cache 92.0% hit rate` |
| T-INT-8 | SessionStats 跨 cycle delta | day 1 last_cycle_ended_at = T1, day 2 第 1 cycle now=T1+12min | Header `+12 min from prev`（不归零）|
| T-INT-9 | 跨日 wake interval（P1-4 解决验证）| day 1 last_cycle_ended_at = 23:55, day 2 03:55 | Header `+240 min from prev`（不变 `(first cycle of day)`）|
| T-INT-10 | trigger_context.type 未知（schema drift）| dict `{"type": "unknown_future_type"}` | fallback header `Trigger    {TYPE_UPPER}` 不带详情 + warning log |

### 7.3 Drift guards（3 cases）

| ID | 断言 | 失败信号 |
|---|---|---|
| T-DG-1 | smoke baseline 下两 helper 行为等价：`_extract_thinking_text(messages)` ≡ `"\n\n".join(t for t in _extract_reasoning_per_response(messages) if t)` | 多 ThinkingPart per Response 时两 helper 输出会分歧（DB 全保留 vs 渲染只取首个），drift guard fail 提示 R2-8c / N12 议题接管 |
| T-DG-2 | ThinkingPart 在 ToolCallPart / TextPart 之前 | smoke baseline；逆序时仍按 type 分类不依赖 index |
| T-DG-3 | `state_snapshot` 字段集合 = `{position, balance, market, pending_orders, active_alerts, _errors, _cycle_id}` | R2-7 contract；新增字段触发 warning 提醒 R2-8a 是否需要消费 |

### 7.4 Mock fidelity 要求

参考 `memory project_iter2_mock_fidelity_lesson`：critical 路径 mock 应至少一条真实 fixture。

R2-8a critical 路径：
- `result.new_messages()` mock 由 `tests/fixtures/cycle_fixtures.py` 的 `build_cycle_messages(thinking_segments, tool_call_segments, final_text)` builder in-memory 构造（不持久化二进制文件 — pickle 有 untrusted exec 风险，pydantic-ai message classes 不支持 model_dump_json）。**结构参数**参考 `.working/verify_message_structure.py` 实测：每 ModelResponse 1 ThinkingPart / ThinkingPart 在 `parts[0]` / 跨 Response 时序 = LLM 生成时序
- `state_snapshot` mock 参考 `_capture_state_snapshot` 实际返回 8 字段集合（position/balance/market/pending_orders/active_alerts/_errors/_cycle_id + position 内 8 子字段）

**实施约束**: T-INT-1a 使用 builder 构造（避免硬编码假设漂移）；T-INT-1b verbatim 比对 mockup §3.2 用 hand-crafted dict（精心控制 thinking 长度等字段以匹配 mockup）。

### 7.5 测试总数估算

| 类 | 数量 |
|---|---|
| Helper 单测 | 11 |
| 集成 | 11（拆 T-INT-1 + 加 retry-exhausted + 加跨日 wake interval；T-INT-1a/1b/2~10 = 11）|
| Drift guard | 3（T-DG-1 smoke baseline 下两 helper 行为一致（每 Response 1 ThinkingPart 时 `_extract_thinking_text` 输出 ≡ `"\n\n".join(_extract_reasoning_per_response)` 非 None 项）/ T-DG-2 ThinkingPart 时序 / T-DG-3 state_snapshot 字段集合）|
| 边界细化（§6 各表 T-EH/T-ES/T-RE/T-FO/T-EX/T-TS/T-TC = ~22 cases，与 §7.1/7.2 部分重叠）| ~10-15 净新增 |
| SessionStats 单测（test_session_state.py）| 5-7 cases |
| **合计** | **~40-47 tests** |

## 8. Out-of-scope（显式不做）

### 8.1 R2-8c — Tool 输出展示优化（独立议题）

R2-8a brainstorm 决议的 D4 / D5 / D11 已转 R2-8c 议题：
- 长尾工具 fallback 升级（80→200/300）
- 8 工具 L1 multi-line parser
- mixed C 形态决议
- L0/L1/L2 各档边界

R2-8a landed 后立即启动 R2-8c brainstorm。详见 `memory project_r2_8c_tool_output_optimization`。

### 8.2 R2-8b — 前 cycle reasoning 注入（N10 MVP）

R2-8c landed 后启动。议题独立——是 reflection tools MVP，与 cycle log 形态正交（仅 prompt 端注入；session log 渲染层无关）。详见 `memory project_n10_recent_decisions_context_injection`。

### 8.3 Session 末 panel（D9 削除）

事后 SQL 5 分钟可查（`agent_cycles` + `tool_calls` 聚合），shutdown hook 与 graceful shutdown 设计交互复杂度不值得 R2-8a scope。

### 8.4 失败行视觉醒目化（C1+C2 削除）

R2-1+R2-2 已修业务失败根因（biz_error 5 分类 + uuid 暴露），sim #4 28 次 biz_error 在 W2 baseline 应大幅减少。`✗ vs ⚙` icon 区分已存在。视觉醒目化（颜色 / 字体加粗 / 失败累计计数）属 W2 数据驱动议题，不进 R2-8a。

### 8.5 R2-7 schema 升级保留时序结构（**N12b** candidate）

当前 `agent_cycles.reasoning` 拼接版（`\n\n`.join thinking parts）适合 N10 注入；事后 DB 重组时序非本议题范围。如未来需要事后时序分析（如基于 DB 跑 reflection 分析脚本），可议题级 schema 升级（`reasoning` 改为 JSON list 持时序）。**N12b 关注数据持久化层升级，独立于 N12a（agent.iter() 重构）**。

### 8.6 Terminal/file 形态分裂（N11 candidate）

当前 SessionConsole 双 sink 共用 markup（D7 决议）。如 W2 实测 terminal 实时跟随过载（cycle log 1-2 页太密集），可议题级分裂——terminal 精简版 + file 完整版。R2-8a 不做。

### 8.7 Session log rotation（D12）

session log 当前无 rotation handler，单 session 文件量级 W2 24h ~1MB / 48h ~2MB / 1week ~7MB（per-cycle ~3KB），远未达 system.log 100MB rotation 阈值。如未来长 session（1 month+）单文件超 100MB 再补。

### 8.8 Inventory 7-9（三源整合 web UI / TUI）

session log + system.log + DB 三源整合属大改造（textual 多 pane / web UI），独立 design spec，R2-8a 不动。

### 8.9 fill `is_full_close` 标记未消费

`cycle_capture._capture_trigger_context` 写入的 conditional fill `trigger_context` 含 `is_full_close: bool` 字段（cycle_capture.py:49），R2-8a Header `Trigger` 行渲染 conditional fill 时**不消费**该字段。未来可加 `[FULL CLOSE]` / `[PARTIAL]` 标记区分——归 R2-8c 议题（与 8 工具 L1 parser 一并讨论）。

### 8.10 SCHEDULED trigger subtype capture 增强

sim #4 痛点 G `(scheduled)` 单分支低信息——无法区分"30min 默认兜底"vs"上 cycle `set_next_wake` 显式安排"。R2-8a 仅复刻 trigger_type 文案为 `Trigger    SCHEDULED`，subtype 不可见。

**论据不进 R2-8a scope**：
- 增强需要改 `cycle_capture._capture_trigger_context` 的 `scheduled` 分支（加 `subtype` 字段），动 R2-7 schema 内 trigger_context JSON 内容（drift guard 触发）
- subtype 数据源在 scheduler 内部（`heapq` priority + `_PRIORITY_MAP`），需 cycle 主循环传递 wake event 类型
- 实墙时间 + Header `+X min from prev` 已**间接暴露** wake 路径（30min vs 自定义 X min 在间隔上区分）

**留作议题**：W2 后期或 N# candidate（按 sim #4 痛点 G 实证驱动）。可与 `project_w2_ops_backlog` S 系列同档候选。

## 9. Pre-impl smoke 验证 ✅

`.working/verify_message_structure.py` 实跑（2026-05-02），验证 4 个结构性假设：

| 假设 | 实测 | 状态 |
|---|---|---|
| ThinkingPart 在 ToolCallPart 之前 | 3 ModelResponse 都在 `parts[0]` | ✅ |
| 每 ModelResponse 至多 1 ThinkingPart | 3/3 都恰好 1 个 | ✅ |
| 跨 ModelResponse 顺序 = LLM 生成时序 | 完美时序 (think_1 → act → think_2 → act → think_3 → message) | ✅ |
| 最终 ModelResponse 含 ThinkingPart + TextPart | ModelResponse[2] = `[ThinkingPart, TextPart]` | ✅ |

**额外数据点**:
- DeepSeek v4-pro `thinking="high" + extra_body={"thinking": {"type": "enabled"}}` 真实生成 reasoning_tokens=631
- 3 段 thinking 长度: 187 / 2095 / 84 chars（task-specific 分布，不可 generalize 到真实 trader cycle）
- prompt cache hit rate 68.9%

实施时无需重跑 pre-impl smoke。但 R2-9 smoke 应顺带 capture 真实 trader cycle 的 thinking 长度分布数据，喂入 R2-8c brainstorm + **N12c** candidate（thinking 截断长度 data-driven 调整）。

## 10. Acceptance Criteria

R2-8a impl 完成的判定条件：

### 10.1 行为正确性

- [ ] AC1: `format_cycle_output()` 5 段架构按 §3.1 顺序渲染
- [ ] AC2: 时序遍历按 ModelResponse 分组，think→act 交织正确
- [ ] AC3: thinking 段截断算法符合 §4.2.2 契约（800 硬截 + `... [+N chars]` 标记）
- [ ] AC4: Header trigger 详情按 §4.1.3 trigger_type 分支正确渲染（含 `Trigger    SCHEDULED` verbatim）
- [ ] AC5: Header state 按 §4.1.4 仓位/无仓位/balance 缺失正确渲染
- [ ] AC6: Footer 含 cycle_tokens / session_total / session_avg / session_cycle_count / hit_rate / duration / end_ts 全字段
- [ ] AC7: forensic 路径渲染 Header + Footer + `[no decision — usage limit exceeded; partial messages unavailable]` + `Cache N/A (forensic)`（**不**渲染 partial Reasoning/Action — `result=None` 不可访问，agent.iter() 重构留 N12a candidate）
- [ ] AC8: 非 thinking model `▾ Reasoning` 段全部省略
- [ ] AC9: 0 tool call cycle 仅渲染 Reasoning + Decision，无 `▾ Action`
- [ ] AC10: 首 cycle Header `(first cycle)` 不显示 `+X min from prev`
- [ ] AC11: retry-exhausted 路径渲染 Header + Footer + `[cycle aborted — 3 attempts failed: <error class>: <msg>]` + `Cache N/A (aborted)`（避免 W2 黑洞）
- [ ] AC12: retry-exhausted 路径**写 forensic AgentCycle 行**（`execution_status="retry_exhausted"` sibling of R2-7 `usage_limit_exceeded`），与 R2-7 同型；W2 SQL `WHERE execution_status != 'ok'` 涵盖两类 forensic
- [ ] AC13: SessionStats 跨日不重置（day 2 第 1 cycle 显示 `+540 min from prev`，不显示 `(first cycle of day)`）
- [ ] AC14: SessionStats forensic / retry-exhausted cycle 计入 `cycle_count` 但 `total_tokens += 0`

### 10.2 测试覆盖

- [ ] AC15: §7 测试矩阵 ~40-47 cases 全部 PASS
- [ ] AC16: T-INT-1a（结构断言）使用 `tests/fixtures/cycle_fixtures.py` 的 `build_cycle_messages()` builder（结构参数参考 `.working/verify_message_structure.py` 实测：3 ModelResponse / 1 ThinkingPart per Response / ThinkingPart 在 `parts[0]`）
- [ ] AC17: T-INT-1b（verbatim）使用 hand-crafted fixture（与 mockup §3.2 byte-equal）
- [ ] AC18: 现有 `tests/test_display_cycle.py` + `tests/test_cycle_log.py` regression 全 PASS
- [ ] AC19: 总测试数 988 → ~1028-1035（净 +40-47）
- [ ] AC20: 全 test suite < 3s（不引入 slow tests）

### 10.3 兼容性

- [ ] AC21: R2-7 schema 不动（`agent_cycles` 字段无变更）
- [ ] AC22: `_extract_thinking_text` 行为完全不动（全收集 ThinkingPart `\n\n`.join — DB `agent_cycles.reasoning` 列写入路径不变；N10 仍用拼接版）；R2-8a 新增 `_extract_reasoning_per_response` 独立 helper 服务渲染层（每 Response 仅取首个 ThinkingPart，与 smoke baseline 一致；详见 §4.2.3）|
- [ ] AC23: `summarize_tool` / `_fallback_summary` / `is_tool_error` 等 parser 不动（R2-8c 议题）
- [ ] AC24: Alembic migration 0 个（不动 schema）
- [ ] AC25: SessionConsole 双 sink 行为不动（D7 共用 markup 字符串：color 在 file 端 stripped via `no_color=True`，行宽 wrapping 各自处理 — file `width=120` vs terminal 动态宽度）
- [ ] AC26: TokenBudget 接口不动（保持 daily token 预算簿职责纯净；session 级 metric 全部由新 SessionStats 持有）

### 10.4 PR 形态

- [ ] AC27: 单 PR landed（不拆 sub-PR）
- [ ] AC28: 净改动 ~510-550 行（含测试 ~210-250；二轮自审 P0/P1/P2/P3 修订后再增）
- [ ] AC29: 文档先 commit（spec + plan）后 impl，符合 `feedback_plan_doc_commit_first`
- [ ] AC30a: PR 后跑短 smoke（**真实 sim 1 cycle**）验证正常路径 5 段架构渲染（Header / Reasoning / Action 时序交织 / Decision / Footer）
- [ ] AC30b: 单测 / monkeypatch smoke 验证 forensic 路径（mock `UsageLimitExceeded`）+ retry-exhausted 路径（mock 3 次 generic Exception）—— 这两类无法在正常 sim 中可控复现，必须用 mock
- [ ] AC31: brainstorm 16 项决议清单 cross-ref 在 PR description（含 D1-D16 全表，反映 spec 三轮自审校准后的 D7/D8/D13/D14/D15/D16）

## 11. 关联文档 + 议题

### 11.1 上游议题
- `.working/sim4-issues-inventory.md §P1-7` — 原 inventory 描述（R2-8a brainstorm 已升级议题范围）
- `memory project_w2_prep_progress` — W2 prep round 2 进度，R2-8 拆分记录
- `memory project_n10_recent_decisions_context_injection` — N10 议题（R2-8b 后续）
- `memory project_agent_reflection_tools_candidate` — reflection tools 上位议题

### 11.2 下游议题
- `memory project_r2_8c_tool_output_optimization` — R2-8a 拆出的 tool 输出议题（**R2-8a landed 后立即启动**）
- N11 candidate（terminal/file 分裂）
- **N12a** candidate（agent.iter() 重构 — 解 forensic partial messages 不可访问）
- **N12b** candidate（R2-7 schema 升级保留时序 — DB reasoning JSON list）
- **N12c** candidate（thinking 截断长度 data-driven 调整 — 基于 R2-9 / W2 实测分布）

### 11.3 R2-7 spec cross-ref 校准

R2-7 spec line 6 写 "后续联动: R2-8 (P1-7 展示 MVP A 路径 + N10 reasoning 注入)"——与现 R2-8a/R2-8c/R2-8b 拆分不一致，但 R2-7 已 landed 不动；新会话起手通过本 spec + W2 prep memory 看到准确拆分。

## 12. Self-review

### 12.-5 第七轮自审（2026-05-02 user-reviewed round 6 — escape 双重转义修正）

| 优先级 | 项 | 处置 |
|---|---|---|
| **P2** | retry-exhausted escape 过度（双 escape 导致显示反斜杠 `\[red]boom\[/]`，不是自然字面值）| §5.2 改：err_part 不 pre-escape，仅最外层 `escape(placeholder)` 一次；T-INT-5b 期望同步为"显示自然字面值无反斜杠" |

### 12.-4 第六轮自审（2026-05-02 user-reviewed round 5 — minor 收敛）

第六轮审查仅 2 项 minor finding，user 主动确认 P1/P2 关键问题已修复无新阻塞：

| 优先级 | 项 | 处置 |
|---|---|---|
| **P2 #1** | retry-exhausted placeholder 动态 error class/message 漏 escape（攻击面：`RuntimeError("[red]boom[/]")` 类异常）| §5.2 短路路径加 `escape(err_part)` + 整体 `escape(placeholder)`（外层方括号也需 escape 防 console.print 解析）+ 加 T-INT-5b 测试 case |
| **P3 #2** | 影响清单 grep 不准 | §5.3 改 `tests/test_n3_wiring.py` 5 处解构点（92/111/128/141/158）；`tests/test_okx_algo_normalization.py` 不属 5-tuple 同步（不解构 return），改为 plan 阶段验证 patch 链覆盖 |

**收敛信号**: 6 轮自审 + user review，spec 已无 critical / 阻塞 finding。下一步可 commit + writing-plans。

### 12.-3 第五轮自审（2026-05-02 user-reviewed round 4 — critical bugs + framing）

第五轮自审针对 user 第四份审查报告 6 项发现，处置如下：

| 优先级 | 项 | 处置 |
|---|---|---|
| **P1 #1** | SessionStats footer **off-by-one critical bug**（lifecycle 先 print 后 record，footer stats 不含当前 cycle）| §4.5.2 字段映射改 **projected stats**（render 时 `stats.total_tokens + ctx.cycle_tokens` / `stats.cycle_count + 1`）+ §4.5.3 加 lifecycle 注 + 不改 lifecycle 顺序（避免 last_cycle_ended_at 自指）|
| **P1 #2** | Markup escape 仅覆盖 Decision，Reasoning / Action body 漏 | §4.2.2 + §4.3 也加 `rich.markup.escape()` — 所有 user/LLM/external data body escape，仅保留框架 markup |
| **P2 #3** | "纯渲染层重构" framing 与 retry-exhausted DB 写入矛盾 | TL;DR 改"渲染层为主 + retry-exhausted 可观测性补写"+ §5.3 影响清单加 `docs/metrics/agent-cycles-schema.md` + `src/storage/models.py:94` 注释 |
| **P2 #4** | 影响清单漏 4+11 测试文件 | §5.3 加 `tests/test_wizard.py` (3 处 build_services 4-tuple 解构) + `tests/test_n3_wiring.py` + `tests/test_okx_algo_normalization.py` + `tests/test_usage_limits.py` (8 处 run_agent_cycle 调用) + 加 **注 2** 给 `run_agent_cycle(stats=None)` 默认值（DummySessionStats no-op） |
| **P3 #5** | §4.4.3 forensic 文案残留 `{n} tool calls` 版本（result=None 不可数）| §4.4.3 删除该版本，统一用 §6.4 "partial messages unavailable" + 加 retry-exhausted 占位行 |
| **P3 #6** | AC30 单 sim cycle 同时验证正常+forensic 不可行 | §10.4 拆 AC30a（真实 sim 1 cycle 验正常）+ AC30b（单测/monkeypatch 验 forensic + retry-exhausted）|

### 12.-2 第四轮自审（2026-05-02 user-reviewed round 3 — 实质 gap + ambiguity）

第四轮自审针对 user 第三份审查报告 11 项发现，处置如下：

| 优先级 | 项 | 处置 |
|---|---|---|
| **G1** | retry-exhausted 路径未声明 DB 写入策略 → W2 SQL 黑洞 | §6.5 加 forensic AgentCycle 写入 spec（`execution_status="retry_exhausted"` sibling of R2-7）+ §3.3 D16 决议 + AC12 |
| **G2** | `_extract_thinking_text` refactor silent behavior change | §4.2.3 恢复两 helper 独立（B 方案：避免 silent 退化）+ T-DG-1 改为 smoke baseline 行为等价断言 + AC22 措辞回归"helper 行为完全不动" |
| **A1** | start_ts_utc 数据源 §4.1.2 vs §5.1 diverge | §4.1.2 改用 `ctx.cycle_started_at`（函数入口时刻）；DB `created_at` 仅事后 SQL |
| **A2** | SessionStats 实例化位置 | §5.3 显式 `build_services` return 5-tuple + main() 解构 |
| **A3** | forensic cycle_ended_at capture 时机 | §6.4 加 except 块内 capture micro-spec |
| **M1** | 长工具名 padding spillover | §3.2 加注（维持 display.py:361 当前行为，dynamic 对齐归 R2-8c）|
| **M2** | footer 算术不自洽 (47×8≠379) | §3.2 改 `Session 376k` 自洽 |
| **M3** | AC18 基线 988 vs memory 970 | spec 数字正确（实测 988），不修；memory follow-up 单独更新 |
| **M4** | SCHEDULED mockup 缺位 | §3.2.1 加 SCHEDULED 短 mockup 让 baseline 可视化 |
| **P3-1** | fill `is_full_close` 字段未消费 | §8.9 加 R2-8c reference |
| **P3-2** | forensic upper-bound estimate W2 ops backlog S6 | spec §4.5.3 已记 caveat；plan 阶段 add memory `project_w2_ops_backlog` S6 |

### 12.-1 第三轮自审（2026-05-02 user-reviewed round 2）

第三轮自审针对 user 第二份审查报告 15 项发现：

| 优先级 | 项 | 处置 |
|---|---|---|
| **P1-1** | T-EH-2 phantom JSON 解析失败（display 入参是 dict 不是 JSON string）| §6.1 删 T-EH-2 + 重编号；§4.1.3 fallback 描述 + 注 2 校准；§7.2 T-INT-10 改"unknown type schema drift" |
| **P1-2** | Fixture 位置 `.working/` 在 .gitignore 不进 git | §5.3 加 `tests/conftest.py` / `cycle_fixtures.py` 新 builder（in-memory，避免 pickle untrusted exec 风险）|
| **P1-3** | cycle_started_at 起点不明 | §5.1 verbatim 定义"`run_agent_cycle` 函数入口时刻（含 capture IO）"——实墙时间直觉 |
| **P1-4** | forensic cycle_tokens=0 是统计假象（实际 ~200k）| §4.5.3 加 caveat 注（不改 token 统计语义）+ cross-ref `project_w2_ops_backlog` |
| **P1-5** | cache 入参冗余 (hit/miss tokens vs hit_rate) | §5.1 改 `cache_hit_rate: float \| None` 单参，None 自然分支 forensic/aborted |
| **P1-6** | `_extract_thinking_text` vs `_extract_reasoning_per_response` drift latent bug | §4.2.3 改单源真相：`_extract_thinking_text = "\n\n".join(_extract_reasoning_per_response(...))` |
| **P1-7** | Decision 段 Rich markup attack surface | §4.4 加 `rich.markup.escape()` 强制转义 |
| **P2-8** | §7.5 数字算错（10 集成 ≠ 11） | §7.2 标题改 11 cases；§7.5 总数 ~40-47；AC18 同步 |
| **P2-9** | mockup "重写" 措辞误导 | §3.2 改 "基于 sim #4 cycle 9f57 重 reframe；illustrative" |
| **P2-10** | SCHEDULED subtype 不可见性应入 Out-of-scope | §8.9 显式新增 sub-section |
| **P2-11** | N12 candidate 是两件事（agent.iter vs schema） | 拆 N12a / N12b / N12c（含 thinking 截断 data-driven）|
| **P2-12** | D7 "完全统一" 与 width=120 file Console 不严格 | §3.3 D7 措辞改"共用 markup 字符串（color stripped, 行宽 wrapping 各自处理）" |
| **P3-13** | SessionStats 应独立文件 | §5.3 加 `src/cli/session_state.py` 新文件 |
| **P3-14** | CycleRenderContext 应直接定调 | §5.1 直接定调 dataclass，2 入参 (`format_cycle_output(ctx)`) |
| **P3-15** | Test fixture format 未指定 | §5.3 选定 in-memory builder helper（避免 pickle 安全风险 + 不需 custom JSON encoder）|

### 12.0 第二轮自审（2026-05-02 user-reviewed）

第二轮 fresh-eyes 自审 + user 审查报告处理，发现并修复 16 项偏差：

| 优先级 | 项 | 处置 |
|---|---|---|
| **P0-1** | forensic 路径假设 partial messages 可访问（错——`result=None` 在 raise 时不可访问）| §6.4 重写：仅 Header + Footer + 占位 Decision；agent.iter() 重构 → N12 candidate |
| **P0-2** | retry-exhausted 路径黑洞（W2 不可见 cycle）| §6.5 加新边界 + D13 决议 + AC11 |
| **P1-3** | TokenBudget squat-tenant + cross-day latent bug | §4.5.3 重写：新建 SessionStats class（D14）独立 lifecycle |
| **P1-4** | cycle_count 跨日歧义 | §4.5.1 footer `Cumulative` → `Session`（D15）+ §6.6 跨日不重置 |
| **P1-5** | T-INT-1 mock vs fixture 矛盾 | §7.2 拆为 T-INT-1a 结构（真 fixture）+ T-INT-1b verbatim（hand-crafted）|
| **P1-6** | scheduled_tick 渲染样例缺位 | §4.1.3 加 `Trigger    SCHEDULED` verbatim |
| **P1-7** | forensic Footer 样式不一致 | §6.4 恢复三行 + `Cache N/A (forensic)` |
| **P2-8** | _render_action ret=None fallback 未明确 | §4.3 加 micro-spec：`[no return captured]` |
| **P2-9** | args_as_dict() 异常容错未抄 | §4.3 算法块显式 try/except |
| **P2-10** | Header 时间戳跨日歧义 | minor，留 plan 阶段决议（每日首 cycle 全日期 vs MM-DD HH:MM:SS）|
| **P2-11** | forensic cycle 是否计入 cycle_count | §4.5.3 明示"forensic 计入 cycle_count，total_tokens 不增" |
| **P2-13** | tests/test_cli.py 影响清单错（grep 0 命中）| §5.3 改 `tests/test_cycle_log.py` |
| **P3-14** | 入参膨胀到 12 | §5.1 加 note：plan 阶段考虑 CycleRenderContext dataclass |
| **P3-15** | D6 旁注 trade-off 透明 | §3.3 加 deep-link 编号议题注 |
| **P3-16** | R2-8c multi-line summary 行为未明 | §4.3 加 multi-line forward-compat 注 |
| 加 | self-review 也漏的 footer cumulative 字段缺失（§6.4 修后已含）| 已含 |

### 12.1 Placeholder scan (final)
- [x] 所有 §X.Y 段编号完整、无 TBD/TODO 占位
- [x] §4 段级契约 verbatim 完整（含 §4.1.3 scheduled_tick verbatim）
- [x] §6 边界情况覆盖正常路径 / forensic / retry-exhausted / 非 thinking model / 0-N tool / state_snapshot 部分缺失
- [x] §7 测试矩阵给出每 case 的 ID + 目标 + 输入 + 期望

### 12.2 Internal consistency (final)
- [x] §3.3 决议清单 D8 / D13 / D14 / D15 与 §6.4 / §6.5 / §4.5.3 / §4.5.1 渲染契约一一一致
- [x] §4.2.2 截断算法与 §3.3 D2 决议一致
- [x] §4.5.3 SessionStats 独立 class 与 §5.4 持久化策略一致
- [x] §5.1 数据流签名扩展与 §5.3 文件影响清单互相 cross-ref（test_cycle_log.py + SessionStats class）
- [x] §6.4 forensic Header + Footer 占位与 §10.1 AC7 一致
- [x] §6.5 retry-exhausted 与 §10.1 AC11 一致
- [x] §3.2 mockup footer `Session 379k` 与 §4.5.1 contract 一致

### 12.3 Scope check (final)
- [x] R2-8a 范围限于渲染层（不动 schema / prompt / agent 主循环）
- [x] §8 Out-of-scope 11 项显式列出，与 brainstorm 决议清单的 ❌ 项一一对应
- [x] 改动量预估（~510-550 含测试 ~210-250）反映二轮自审 P0/P1/P2/P3 修订增量
- [x] 单 PR landed 原则（无 sub-PR 拆分）
- [x] agent.iter() 重构显式归 N12a candidate（不进 R2-8a）；schema 升级归 N12b；thinking 截断 data-driven 归 N12c

### 12.4 Ambiguity check (final)

- [x] §4.1.3 trigger 详情分支：4 种枚举完整 + scheduled_tick verbatim
- [x] §4.5.1 footer 字段完整 + forensic/retry-exhausted footer 三行布局保持
- [x] §6.4 forensic 路径：仅 Header + Footer + Decision 占位（不渲染 partial）
- [x] §6.5 retry-exhausted 路径：error class + msg[:200] 截断格式明确
- [x] §4.5.3 SessionStats lifecycle：跨日不重置 / forensic 计入 cycle_count / total_tokens 不增
- [x] §4.3 _render_action：ret=None / args 异常 / multi-line summary 三类 fallback 显式

### 12.5 关键 design decision rationale (updated)

- **D2 thinking 截 800 task-agnostic**：smoke 实测 thinking 长度分布是 task-specific artifact，不可 generalize；A2 全部 800 是 task-agnostic baseline，R2-9 / W2 数据驱动后再调整（**N12c** candidate）
- **D8 forensic 不渲染 partial**：`agent.run()` 在 `UsageLimitExceeded` raise 时 `result=None` partial messages 不可访问；agent.iter() 重构超出 R2-8a scope（cycle 主循环改造，违反"不动 agent 行为"R2-8a 哲学），归 **N12a** candidate
- **D9 不做 Session 末 panel**：事后 SQL 5 分钟可查 + shutdown hook 与 graceful shutdown 设计交互复杂度
- **D10 时序仅 in-memory**：R2-7 schema 已用 `\n\n`.join 拼接 thinking，时序信息 DB 已丢失；事后 DB 重组时序非本议题范围（**N12b** candidate）
- **D12 不加 session log rotation**：W2 24h-1week 单文件 1-7MB，远低于 system.log 100MB rotation 阈值
- **D13 retry-exhausted 渲染 Header + Footer + 占位**：避免 W2 长 session"不可见黑洞"（cycle 在 session log 凭空消失，用户 24h 后排查找不到）
- **D14 SessionStats 独立 class**：解决 TokenBudget squat-tenant + 跨日 latent bug；TokenBudget 维持 daily token 预算簿职责纯净
- **D15 Footer `Session` 替代 `Cumulative`**：`Cumulative` 模糊（daily 还是 session？），`Session` 明示语义

---

**Spec status**: brainstorm done (2026-05-02), ready for plan.
**Next step**: writing-plans skill → `2026-05-02-iter-w2r2-8a-cycle-log-narrative-redesign.md` plan
