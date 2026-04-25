# Iter 4 — Prompt 优化（observation-period 前最后一发 prompt 工作）

**Spec date:** 2026-04-25
**Branch:** `iter4-prompt-optimization-spec`
**Predecessor:** Iter 3 (PR #24, squash `fd5968d`)
**Successor:** Iter 5 — pydantic-ai 框架合规

## 0. 背景

### 0.0 Project Overview（不熟项目的审查员先看这段）

**项目**：TradeBot — 加密货币自主交易 agent，基于 LLM 决策做开仓 / 平仓 / SL/TP 设置等交易动作。

**技术栈**：
- **Agent 框架**：[pydantic-ai](https://ai.pydantic.dev/)（Python LLM agent framework）— pyproject.toml 声明 `>=1.0`（未固定上界），实测运行时 1.78.0；版本固定推 Iter 5.3
- **LLM**：Anthropic Claude（通过 pydantic-ai provider 注入，`src/services/model_manager.py` 集中管理）
- **交易所**：OKX（实盘）+ SimulatedExchange（模拟）— 当前阶段在 SimulatedExchange 跑，进观察期收集 baseline
- **数据源**：CoinDesk News / Fear & Greed Index / FRED / CoinGecko / Alpha Vantage / SoSoValue / DefiLlama / ForexFactory
- **DB**：SQLAlchemy 2 + AsyncEngine（`sessions` / `trades` / `decision_logs` / `tool_calls` 表）

**当前架构**：
- 单 Agent 工厂 `create_trader_agent()`（`src/agent/trader.py`），`deps_type=TradingDeps`，`output_type=str`
- 已使用官方推荐的 `instructions=` 而非 `system_prompt=`
- 30 个 `@agent.tool` 工具（19 感知 + 10 执行 + 1 memory）
- Persona prompt 三层结构（`src/agent/persona.py`）：
  - **Layer 1** 角色定位 + Market Context + Tool Usage Notes（25 bullets，本 iter 主要审计对象）
  - **Layer 2** 思考框架（`## How to Think` — 5 个开放问题段）
  - **Layer 3** 个性 + 交易风格注入（personality / trading_style）
- 观察基础设施已就位（B 档 `tool_calls` metrics，Iter 1 PR #21 落地）

**当前阶段**：观察期前的 prompt 优化最终轮（pre-observation iteration 4 of 6）。

**审查员前置假设**：理解 LLM agent + tool calling 的基本概念；了解 prompt engineering 中"system prompt vs tool description"的作用差异。

### 0.1 项目快照

24 PR 已合并、818 tests + 1 skip 全绿。Agent 当前 30 个 `@agent.tool`（19 感知 + 10 执行 + 1 memory）。Persona Layer 1 25 bullets。

### 0.2 所处位置

进观察期前的 iteration 计划修订后为 6 步：

| Iter | 状态 | 内容 |
|------|------|------|
| 1 | ✅ PR #21 | tool-call metrics enabler |
| 2 | ✅ PR #22 | toolkit expansion (3 perception tools + get_position 增强) |
| 2b | ✅ PR #23 | OKX live hardening |
| 3 | ✅ PR #24 | 结构感知工具 `get_price_pivots` |
| **4** | **本 iter** | **prompt 优化（5 子项）** |
| 5 | pending | pydantic-ai 框架合规（UsageLimits / ModelRetry / 配置）|

**为什么从 5 步扩到 6 步**：原计划 Iter 4 = N7 Layer 1 重组（单议题）。Brainstorm 阶段重审时识别出三个根本变化：

1. pydantic-ai 框架合规议题（2026-04-22 识别）原计划没纳入，且与 prompt 优化技术维度不同（运行时 guard vs 提示词内容）— 拆出 Iter 5
2. pydantic-ai 框架机制层明示 docstring 自动作 tool description 传 LLM，N7 原方向（Layer 1 分组 / 抽工具索引）违反 DRY — 反转为"删除工具介绍 bullets，依赖 docstring"
3. 用户澄清：tools 输出 prompt 应 fact-only；system prompt 应避免硬性规定 / 流程 — 两层 prompt 不同标准

#### 0.2.1 关键论证强度声明（审查员注意）

本 spec 的核心反转（Layer 1 删除工具描述 bullets）的论证强度是 **工程原则 + 框架机制层支持，不是框架硬性禁令**：

| 论据 | 强度 |
|---|---|
| pydantic-ai 框架推荐？ | ❌ 框架不强制（内容层无约束 — pydantic-ai docs/tools.md 仅说 "docstring is passed to LLM as description"，未禁止 system prompt 重复）|
| pydantic-ai 框架机制？ | ✅ docstring 自动作为 `ToolDefinition.description` 传 LLM；`Args:` 段自动 parse 为 parameter schema |
| 业界 LLM tool calling 最佳实践 | ✅ 主流方向（含使用引导 — Anthropic / OpenAI 均推荐 tool description 含使用场景）|
| 工程内聚原则 | ✅ 单工具描述放该工具 docstring |
| DRY 原则 | ✅ 避免 system prompt 重复 |

**论证不成立的场景**：如审查员认为"system prompt 列工具有官方未否定的额外价值"（如跨工具'何时用 X 不用 Y'引导、调用阶段引导），这是**合法挑战**。本 spec §2.1 保留 5 条跨工具行为规范 bullets 即针对此风险的缓解；进一步退路是 Layer 1 加'工具索引段'（一行枚举所有工具名），但本 iter 不采取。

**未来不可逆性缓解**：4.1 docstring + 4.2 Layer 1 在同 PR 内但 commit 分开，万一观察期发现 LLM 工具发现率显著下降，可单独 revert Layer 1 改动不动 docstring（详见 §6.1）。

### 0.3 核心目标

让 agent 像真实交易员那样自主做交易决策。两层 prompt 用不同审计标准：

| Prompt 层 | 标准 | 目的 |
|---|---|---|
| **Tools 输出**（运行时返回字符串）| **Fact-only** — 纯事实数值 / 数据，无评价词、无方向 hint | 让 agent 看客观信息自己判断含义 |
| **System prompt**（persona.py + tool docstring）| **避免硬性规定 / 流程** — 不剥夺 agent 最佳决策空间 | 让 agent 自主推理 |

#### 0.3.1 边界举例（澄清两层标准的判定边界）

**System prompt 保留**：
- ✅ 开放问题：`"Where is the logical stop loss?"`（让 agent 决定）
- ✅ 行为引导：`"Use multiple timeframes to build conviction before acting"`（agent 仍自主选择）
- ✅ 个性定义：`"Missing opportunity does not bother you — taking a bad trade does"`（Layer 3 风格本质，不审）
- ✅ 技术约束：`"Set stop loss only after fill confirmation — order not yet filled cannot have SL/TP"`（不是决策暗示，是技术事实）

**System prompt 删除/软化**：
- ❌ 硬性单向规定：`"...at a structural level, not an arbitrary percentage"`（强制结构位、排除百分比 → 软化为开放问题）
- ❌ 硬性流程：`"Always check the chart before placing an order"`（剥夺 agent 决策）
- ❌ 单向决策暗示（Layer 3 之外的"X is primary"型暗示）

**Tool docstring**（4.1 范围）：
- ✅ 调用时机引导：`"Call before trading to scan exchange announcements"`（引导调用时机）
- ❌ 硬性规定：`"Always call this tool every cycle"`（剥夺 agent 调度自由）

**Tool 运行时输出**（N5 PR #18 已做，4.5 复查范围）：
- ✅ 事实：`"RSI(14) = 65.3"`
- ❌ 评价：`"RSI(14) = 65.3 (overbought)"`（这是 N5 已清理的）

### 0.4 硬约束

1. **DRY 不重复**：docstring 是工具描述的唯一权威来源，Layer 1 不再列工具描述
2. **内聚边界**：单工具调用时机 → docstring；跨工具协作模式 → Layer 1
3. **避免硬性规定**：system prompt 不写"必须 / 应该 / 不允许"等单向决策（个性 / 风格段除外）
4. **不动技术约束**：persona.py Market Context 段（USDT-margined / one-way mode / fees）保留，是事实约束不是决策暗示
5. **不动 Layer 3**：personality + trading_style 段不审 — 用户主动选择的"风格"内硬性规则是风格语义本身，且尾注已有"adapt..."豁免
6. **单 PR 单 spec**：5 子项一次落地，不拆 4a/4b

### 0.5 术语表

| 术语 | 定义 |
|------|------|
| **Layer 1** | persona.py `_build_layer1()` 返回字符串 — 角色定位 + Market Context + 跨工具行为 |
| **Layer 2** | persona.py `_build_layer2()` — `## How to Think` 思考框架 |
| **Layer 3** | persona.py `_build_layer3(config)` — personality + trading_style 注入 |
| **Tool docstring** | `@agent.tool` 装饰器函数体内的 docstring（trader.py 内）— pydantic-ai 抽这层作 description 传 LLM |
| **跨工具协作模式** | 涉及 ≥ 2 个工具的时序 / 触发响应 / 状态依赖（如 fill timing 涉及 open + SL/TP）|
| **单工具调用时机** | 仅涉及 1 个工具的"何时调用 / 参数推荐 / 输出解读"（如"call before trading"）|
| **硬性规定** | 在所有场景下都强制某动作 / 排除某动作的单向指令 |

## 1. 目标与非目标

### 1.1 目标

1. **4.1** 31 个 `@agent.tool` 函数 docstring 全部统一 Google format（first paragraph + `Args:` 段）；含调用时机引导，避免硬性规定
2. **4.2** Persona Layer 1 从 25 bullets 瘦身到 5 bullets — 仅保留跨工具行为规范段
3. **4.3** Layer 2 删除单向决策暗示（具体改写见 §3.3）；Layer 3 不审
4. **4.4** `get_critical_alerts` 拆分为 `get_exchange_announcements` + `get_macro_calendar`，工具数 30 → 31
5. **4.5** 31 个工具运行时输出全部通过全局 `FACT_ONLY_BANNED_WORDS_RE` 检测；补完 ~27 工具的全局 wordlist fact-only 测试（31 总工具 - 4 已覆盖）
6. **测试规模**：818 → ~840-860 (+22-42)，零 regression

### 1.2 非目标

- ❌ `docstring_format='google'` Agent 配置启用 → Iter 5.3
- ❌ `require_parameter_descriptions=True` Agent 配置启用 → Iter 5.3
- ❌ UsageLimits / ModelRetry / pydantic-ai 版本固定 → Iter 5
- ❌ `prepare=` / `prepare_tools=` 动态工具评估 → 观察期数据驱动
- ❌ Iter 3 spec §7 follow-up（volume profile / touch count / 等 7 项）→ 观察期
- ❌ Layer 3 personality / trading_style 段审计 — 用户主动选择风格的硬性规则是风格本身
- ❌ Tool 实现 docstring（tools_perception.py / tools_execution.py / tools_memory.py 内的实现 docstring）改动 — pydantic-ai 不读这层；保持现状或顺手对齐

### 1.3 改动文件汇总

| 文件 | 改动 | 估算行数 |
|------|------|---------|
| `src/agent/persona.py` | Layer 1 重构（25 → 5 bullets，标题 `## Tool Usage Notes` → `## Cross-Tool Behavior`）；L27 软化；L65 删 1 句单向决策；Layer 3 不动 | -20 / +5 |
| `src/agent/trader.py` | 31 个 `@agent.tool` 装饰器函数 docstring 重写；REGISTERED_TOOL_NAMES 30 → 31；拆 `get_critical_alerts` | +150 / -50 |
| `src/agent/tools_perception.py` | 拆 `get_critical_alerts` 为两个独立函数 | +30 / -50 |
| `tests/test_persona.py` | bullet drift 测试更新（25 → 5）；删 `test_layer1_includes_get_price_pivots`；新增 `test_layer1_no_tool_invocation_descriptions` | +30 / -30 |
| `tests/test_trader_agent.py:84` | drift 硬编码 `len == 30` → `== 31` | +1 / -1 |
| `tests/test_news_tools.py` | 拆 `get_critical_alerts` 7 个测试函数（`test_critical_alerts_*` at L174-281）为 `test_exchange_announcements_*` + `test_macro_calendar_*` 两套；测试名同步 | +50 / -30 |
| `tests/test_fact_only_wordlist.py` | 新增 ~27 工具的 fact-only 测试（全局 wordlist；执行工具批量参数化）| +200 |

## 2. 25 Bullet 个案分类（4.2 核心数据）

### 2.1 保留 Layer 1（5 条 — 跨工具协作 / 触发响应）

| 行 | bullet | 类型 | 处理 |
|---|---|---|---|
| L26 | Fill timing | 跨工具时序（market order → SL/TP）| 保留 — 技术约束（未 fill 无法设 SL/TP）|
| L27 | Open fill response | fill 触发响应 | **保留 + 软化**（见 §3.2） |
| L28 | Close fill response | fill 触发响应 + memory | 保留 — 行为引导，无硬性 |
| L34 | Alert response | alert 触发响应（多 alert 类型）| 保留 — 全是开放问题 |
| L49 | OCO atomicity on OKX | 跨工具协作（OKX 特有语义）| 保留 — 开放选择措辞 |

### 2.2 移入单工具 docstring（20 条 bullet → 对应工具 docstring；其中 4 条 bullet 拆双 docstring：L30 / L35 / L37 / L39）

| 行 | bullet | 目标 docstring | 备注 |
|---|---|---|---|
| L29 | Multi-timeframe analysis | `get_market_data` | 含 `candle_count=20` 参数推荐 |
| L30 | Memory | `save_memory` + `get_memories` | 拆"what to save"→ save_memory；"check past lessons"→ get_memories |
| L31 | Dynamic wake interval | `set_next_wake` | 含调整时机引导 |
| L32 | Limit orders | `place_limit_order` | "Not every entry needs to be market order" → docstring 中作 alternative 描述 |
| L33 | Price level alerts | `add_price_level_alert` | — |
| L35 | Volatility alerts | `set_price_alert` + `get_active_alerts` | 拆"调整 sensitivity"→ set_price_alert；"review configuration"→ get_active_alerts |
| L36 | Order management | `cancel_order` | — |
| L37 | Self-assessment | `get_performance` + `get_trade_journal` | 拆量化评估 vs 决策模式回顾 |
| L38 | Market news | `get_market_news` | — |
| L39 | Critical alerts | **拆为** `get_exchange_announcements` + `get_macro_calendar` | 4.4 拆分后双 docstring |
| L40 | Derivatives structure | `get_derivatives_data` | — |
| L41 | Higher timeframe view | `get_higher_timeframe_view` | — |
| L42 | Macro context | `get_macro_context` | DTWEXBGS vs DXY 区分等长说明都进 docstring |
| L43 | ETF flows | `get_etf_flows` | — |
| L44 | Stablecoin supply | `get_stablecoin_supply` | — |
| L45 | Order book | `get_order_book` | — |
| L46 | Recent trades | `get_recent_trades` | — |
| L47 | Multi-timeframe snapshot | `get_multi_timeframe_snapshot` | "once per cycle" → docstring 调用频率引导 |
| L48 | Position risk context | `get_position` | — |
| L50 | Price pivots | `get_price_pivots` | **软化**（见 §3.2）|

### 2.3 7 个老工具 docstring 补完

之前未在 Layer 1 介绍，现在更不补 Layer 1，但 docstring 必须充实：

`get_account_balance` / `get_open_orders` / `open_position` / `close_position` / `set_stop_loss` / `set_take_profit` / `adjust_leverage`

每个 docstring 含：功能描述 + 参数说明（`Args:` 段）+ 必要的调用时机引导。

**覆盖率闭合验证**：§2.2（24 工具，含 N8 拆分双 docstring）+ §2.3（7 老工具）= 31 工具，与 Acceptance #4（31 工具全 Google format）+ §3.5 fact-only 27 待补工具（31 - 4 已覆盖）一致。

## 3. 子项详细范围

### 3.1 Tool docstring audit + 补完（4.1）

**Docstring 模板**（Google format，与 pydantic-ai griffe sniff 兼容）：

```python
@agent.tool
async def example_tool(
    ctx: RunContext[TradingDeps],
    param: int = 10,
) -> str:
    """<功能描述 + 调用时机引导>。

    Args:
        param: <描述>
    """
```

**Audit checklist**（每个 docstring）：

- [ ] First paragraph 完整描述工具功能
- [ ] 含调用时机引导（如 "call before trading" / "after fill" / 与其他工具关系）
- [ ] `Args:` 段每参数有描述
- [ ] 措辞避免**调用时机 / 调用频率**类硬性规定（如 "Always call this tool every cycle"、"You must call X before Y"）
- [ ] **参数使用指令豁免**：所有 10 个执行工具 docstring 当前均含 `Always provide reasoning.`（trader.py 实地核对：`open_position` L270 / `close_position` L283 / `set_stop_loss` L290 / `set_take_profit` L297 / `adjust_leverage` L304 / `set_price_alert` L311 / `cancel_order` L323 / `add_price_level_alert` L330 / `set_next_wake` L342 / `place_limit_order` L353）。这是描述 `reasoning` 参数用途的指令，**不属"硬性规定"范畴**。统一处理：迁移到 Google format 的 `Args:` 段（如 `reasoning: brief description of your decision logic`），不删除语义
- [ ] 输出 token 估算保留（如现有 "Output ~500-700 tokens"）
- [ ] 内容来源：如果该工具有 §2.2 移入条目，bullet 内容已并入

**当前 docstring 格式现状样例**（4.1 audit 时统一改 Google format）：

```python
# 样例 1：单行 docstring，无 Args 段（trader.py:84，get_account_balance）
async def get_account_balance(ctx: RunContext[TradingDeps]) -> str:
    """Get account balance with return on initial capital."""
    ...

# 样例 2：plain 格式，参数描述内联（trader.py:144-147，get_critical_alerts）
async def get_critical_alerts(...) -> str:
    """Get critical alerts: exchange announcements and upcoming macro events.
    lookback_hours: how far back to check announcements (default 24h).
    lookahead_hours: how far ahead to check macro events (default 12h).
    Output ~100-400 tokens (often empty when no relevant events are scheduled)."""

# 样例 3：含参数描述但无标准 Args 段（trader.py:128-133，get_market_news）
async def get_market_news(...) -> str:
    """Get recent crypto news headlines and market sentiment.
    news_filter: 'positive', 'negative', 'neutral'. Default: no filter (latest mix).
    Returns up to 10 headlines total ...
    Output ~500-700 tokens."""
```

4.1 audit 统一目标：所有 31 个 `@agent.tool` 函数 docstring 改成 Google format（first paragraph + 标准 `Args:` 段），见 §3.1 上方模板。

### 3.2 软化措辞具体改写

**L27 Open fill response 软化**：

当前（persona.py:27）：
> "When woken by an order fill notification (conditional trigger) that opened a position, check the chart to identify structural support/resistance levels, then set stop loss and take profit at those levels. Do not skip market data — you need it to place stops at meaningful prices, not arbitrary ones."

软化后：
> "When woken by an order fill notification (conditional trigger) that opened a position, identify your stop loss and take profit levels and set them. Use market data to inform these levels."

变化：
- ✂ 删 "check the chart to identify structural support/resistance levels"（连带删除"check the chart"动作指令 — 让 agent 自主决定如何获取价格信息，不预设"必须看 chart"）
- ✂ 删 "structural support/resistance levels"（硬性规定结构位）
- ✂ 删 "Do not skip market data" 改 "Use market data"（软化禁令）
- ✂ 删 "place stops at meaningful prices, not arbitrary ones"（单向决策暗示）

**L50 Price pivots docstring 软化**（移入 `get_price_pivots` docstring 时）：

当前（persona.py:50）末尾：
> "Useful for placing SL/TP at structural levels rather than arbitrary percentages."

处理：直接删除该句。前文已描述工具功能（"swing highs/lows... grouped above/below current price with distance % and bars-ago"），不需要追加用途暗示。

### 3.3 Layer 2 单向决策审计（4.3）

**唯一删除点**（persona.py:65 Risk-Reward 段，仅删一句子句）：

当前 Risk-Reward 整段：
```
**Risk-Reward**
What is the risk-to-reward ratio of this potential trade? Where is the logical stop loss — at a structural level, not an arbitrary percentage? Is the potential reward worth the risk? Would a better entry improve the ratio?
```

修改后：
```
**Risk-Reward**
What is the risk-to-reward ratio of this potential trade? Where is the logical stop loss? Is the potential reward worth the risk? Would a better entry improve the ratio?
```

变化：
- ✂ 仅删除 "— at a structural level, not an arbitrary percentage" 子句（在所有场景下强制结构位、排除百分比 — 违反"避免剥夺 agent 决策"标准）
- ✓ 保留：4 个开放问题（risk-reward / stop loss / reward worth risk / better entry）— 段落语义完整
- ✓ 思考框架仍引导 agent 思考 SL 位置，但不预设答案

**保留**：Layer 2 其他所有开放问题（Market Structure / Signal & Confirmation / Position Management / Self-Review 4 段共 ~12 个开放问题）— 思考框架本质就是开放问题。

**Layer 3 不审**：personality（conservative / moderate / aggressive）+ trading_style（trend_following / swing / breakout）段全部不动。

### 3.4 N8 工具拆分（4.4）

**拆分前**（trader.py:138-150）：

```python
@agent.tool
async def get_critical_alerts(
    ctx: RunContext[TradingDeps],
    lookback_hours: int = 24,
    lookahead_hours: int = 12,
) -> str:
    """Get critical alerts: exchange announcements and upcoming macro events. ..."""
```

**拆分后**（两个独立工具）：

```python
@agent.tool
async def get_exchange_announcements(
    ctx: RunContext[TradingDeps],
    lookback_hours: int = 24,
) -> str:
    """Get recent exchange announcements (maintenance, delistings, parameter changes).

    Call before trading or when investigating unexpected price moves. Output ~50-200 tokens
    (often empty when no recent announcements).

    Args:
        lookback_hours: how far back to scan (default 24h).
    """
    from src.agent.tools_perception import get_exchange_announcements as _impl
    return await _impl(ctx.deps, lookback_hours)


@agent.tool
async def get_macro_calendar(
    ctx: RunContext[TradingDeps],
    lookahead_hours: int = 12,
) -> str:
    """Get upcoming macro events (FOMC, CPI, NFP) with impact level.

    Call before trading or when assessing forward-looking risk. Macro calendar covers
    the current week only — Friday evening / weekend calls may miss next week's early
    events. Output ~50-250 tokens (often empty when no scheduled events in window).

    Args:
        lookahead_hours: how far ahead to scan (default 12h).
    """
    from src.agent.tools_perception import get_macro_calendar as _impl
    return await _impl(ctx.deps, lookahead_hours)
```

**Tools_perception.py 拆分**：

`get_critical_alerts(deps, lookback_hours, lookahead_hours)` 拆为两个独立函数：

```python
async def get_exchange_announcements(deps: TradingDeps, lookback_hours: int = 24) -> str:
    if deps.news is None:
        return "News service not configured."
    try:
        announcements = await deps.news.get_announcements(lookback_hours)
    except Exception:
        announcements = None
    # 渲染逻辑（与原 get_critical_alerts 内 announcements 段一致）
    ...


async def get_macro_calendar(deps: TradingDeps, lookahead_hours: int = 12) -> str:
    if deps.news is None:
        return "News service not configured."
    try:
        macro_events = await deps.news.get_macro_events(lookahead_hours)
    except Exception:
        macro_events = None
    # 渲染逻辑（与原 get_critical_alerts 内 macro_events 段一致）
    # Footer "Note: macro calendar covers current week only..." 保留在此工具内（仅
    # 与 macro_events 语义相关），原 get_critical_alerts 内的 footer 一并迁移
    ...
```

**Footer 三态显示规则**（必须显式继承）：

| `macro_events` 状态 | 主体内容 | Footer 显示 |
|---|---|---|
| `list`（含 `[]` 空列表）| 渲染事件列表（空列表显示 "no upcoming events"）| ✓ 显示（结果有效，footer 限定 "current week only"）|
| `None`（upstream 失败）| 显示降级文案（"macro calendar unavailable"）| ✗ 不显示（无有效结果可限定）|

此规则现隐含在原 `get_critical_alerts` 内的渲染分支里，拆分后必须显式落实在 `get_macro_calendar` 内并配对应专项测试（见下方 §3.4 测试改造）。

**REGISTERED_TOOL_NAMES**：删 `"get_critical_alerts"`、加 `"get_exchange_announcements"` 和 `"get_macro_calendar"`，工具数 30 → 31，注释 `# --- 感知 (19) ---` 改为 `# --- 感知 (20) ---`。

**测试改造**：

`tests/test_news_tools.py:174-281` 现含 7 个 `test_critical_alerts_*` 测试函数。拆分**不是**简单 1→2 镜像（每边各 7 份），而是按"工具状态"重组：

| 原测试 | 拆分方式 | exchange_announcements 侧 | macro_calendar 侧 |
|---|---|---|---|
| `_no_service`（deps.news = None）| **镜像**（两侧都需要）| `test_exchange_announcements_no_service` | `test_macro_calendar_no_service` |
| `_format`（两侧都有数据）| **解耦**（各测各侧 happy path）| `test_exchange_announcements_format`（announcements=[ev1, ev2]）| `test_macro_calendar_format`（macro_events=[ev1, ev2]）|
| `_empty`（两侧都空）| **解耦**（各测空列表渲染）| `test_exchange_announcements_empty`（announcements=[]）| `test_macro_calendar_empty`（macro_events=[]，验证 footer **显示**）|
| `_passes_params`（验证 lookback/lookahead 参数透传）| **解耦**（每工具只剩一参数）| `test_exchange_announcements_passes_lookback_hours` | `test_macro_calendar_passes_lookahead_hours` |
| `_services_unavailable`（两侧都 None）| **解耦**（各测各侧 None 降级 + footer 隐藏）| `test_exchange_announcements_unavailable`（announcements=None）| `test_macro_calendar_unavailable`（macro_events=None，验证 footer **不显示**，对应 §3.4 footer 三态规则）|
| `_mixed_unavailable_and_empty`（announcements=None, macro=[]）| **分解**到对应单工具状态（拆分后 orchestrator 不存在，混合态不再可测）| 归 `test_exchange_announcements_unavailable`（已涵盖 None 分支）| 归 `test_macro_calendar_empty`（已涵盖 [] 分支 + footer 显示）|
| `_announcements_only_macro_unavailable`（announcements=[event], macro=None）| **分解**到对应单工具状态 | 归 `test_exchange_announcements_format`（已涵盖 [data] 分支）| 归 `test_macro_calendar_unavailable`（已涵盖 None 分支 + footer 隐藏）|

**净测试函数数**：每侧 5 个（no_service / format / empty / passes_params / unavailable）= 10 个独立测试函数（不是 14）。两个混合态测试**整体删除**，其覆盖语义被分解后的单工具状态测试承接。

各自的 fact-only 场景独立维护（已在 §3.5 "9 个新感知工具" 列出 `get_exchange_announcements` 和 `get_macro_calendar`）。

**NewsService 接口契约**（`src/integrations/news/service.py:25` — 不动）：

```python
class NewsService:
    async def get_macro_events(
        self, lookahead_hours: int
    ) -> list[InformationEvent] | None:
        # N3 三态契约：list (可空) 表示成功；None 表示完全失败
        ...

    async def get_announcements(
        self, lookback_hours: int
    ) -> list[InformationEvent] | None:
        # N3 三态契约：list (可空) 表示成功；None 表示完全失败
        ...
```

两方法已分开实现 — 本 iter 工具拆分**不需改 NewsService 接口**：
- 各自独立 cache + 独立 upstream（OKX API vs ForexFactory CSV）
- 各自独立 try/except 降级
- 三态契约（list / [] / None）保留与 N3 spec §3.5 一致

### 3.5 工具输出 fact-only 全工具复查（4.5）

**实施**：用全局 `FACT_ONLY_BANNED_WORDS_RE` 跑 31 个工具的代表性场景。

**已覆盖工具**（保留现有测试，不重做）：
- order_book / recent_trades / multi_tf_snapshot / get_position（Iter 2 PR #22，已使用全局 wordlist）
- technical indicators 类（N5 PR #18，由更上层的工具如 get_market_data 测试间接覆盖）

**price_pivots 特殊处理**：
- 现有 `PIVOTS_BANNED_WORDS` per-tool 测试保留（守护 strong/weak/important/key/major/minor 等 pivot 特有词）
- **新增独立测试函数** `test_get_price_pivots_global_wordlist_fact_only`（与 per-tool wordlist 测试解耦；确保也通过全局 sentiment 类禁词检测，与目标 #5 / Acceptance #9 一致）— 不混入 per-tool 测试增加 case，便于 §4.3 计数 + plan 阶段实施动作明确

**待补 fact-only 测试**（~26-27 个工具的代表性场景，每场景跑全局 wordlist）：
- **7 个老感知工具**：get_market_data / get_account_balance / get_open_orders / get_trade_journal / get_memories / get_active_alerts / get_performance
- **9 个新感知工具（含 N8 拆分两个 + price_pivots 新增全局测试）**：get_market_news / get_exchange_announcements / get_macro_calendar / get_derivatives_data / get_higher_timeframe_view / get_macro_context / get_etf_flows / get_stablecoin_supply / get_price_pivots（详见上方"price_pivots 特殊处理"段：保留 per-tool `PIVOTS_BANNED_WORDS` 测试 + 新增 1 个全局 wordlist 场景）
- **10 个执行工具**：open_position / close_position / set_stop_loss / set_take_profit / adjust_leverage / set_price_alert / cancel_order / add_price_level_alert / set_next_wake / place_limit_order
- **1 个 memory 工具**：save_memory

**测试策略 — 执行工具批量参数化**：

10 个执行工具中多数输出是固定模板（如 "Order placed" / "Insufficient margin" / "Invalid symbol"），fact-only 风险低。采用 `@pytest.mark.parametrize` 批量参数化：**1 个测试函数 + parametrize fixture 覆盖 10 工具**，避免 10 个独立测试函数（减少代码复制）。

感知工具 + memory 工具因输出语义复杂、各自有独立场景需求，**保持 1 工具 1 测试函数**（17 个独立函数：7 老感知 + 9 新感知 + 1 memory）。

**测试函数总数预估**：17 + 1（执行工具批量）= 18 个新增测试函数，覆盖 27 个工具（拆分后总 31 - 现有 4 已覆盖 = 27）。

**测试模式**：`tests/test_fact_only_wordlist.py` 内每工具一个测试函数，给定 mock fixtures 触发正常 + 降级两类输出，断言不命中全局 wordlist。

## 4. 测试策略

### 4.1 双轨 wordlist

| 轨 | 内容 | 跑覆盖 |
|---|---|---|
| **全局 `FACT_ONLY_BANNED_WORDS_RE`** | sentiment 类公认禁词（bullish/bearish/oversold/overbought/etc）| 所有 31 工具 |
| **per-tool wordlist** | 单工具特有需禁词（如 `PIVOTS_BANNED_WORDS` 的 strong/weak/important/key/major/minor）| 仅该工具 |

两轨不重叠：全局保留 sentiment；per-tool 保留语义/评价类。如未来发现某 per-tool 词应升级为全局，独立小 PR 处理（不进 Iter 4）。

**测试层边界（自动 vs 人工）**

| 层 | 守护对象 | 执行方式 | 示例 |
|---|---|---|---|
| **自动 BANNED_WORDS**（双轨 wordlist）| **工具运行时输出字符串**（fact-only guard）| `pytest tests/test_fact_only_wordlist.py` CI 自动跑 | 检测 `bullish/bearish/strong support` 等 sentiment 词出现在 tool 返回值中 |
| **人工 audit checklist**（spec §3.1）| **docstring + system prompt 文本**（避免单向决策规则）| 人工逐项审查（PR review 阶段）| 检测 "You must use X before Y" / "Always call Z" 等调用时机硬规定是否混入 docstring 或 Layer 1 |

两层**不重叠**：
- BANNED_WORDS 面向运行时字符串，`grep`/`re.search` 可精确匹配 — 适合自动化
- audit checklist 面向意图语义（"这句话是不是在规定 agent 的决策？"），需要人工判断上下文 — 不适合正则自动化

**"Always provide reasoning" 的归属**：`open_position` 等执行工具 docstring 的 `Args:` 段 `reasoning` 参数有 `"Always provide reasoning."` 描述（§3.1 A2 修订项）。这是参数用途说明（usage instruction），不是运行时输出 — 属于**人工 audit checklist** 范畴（§3.1 检查项），不属于 BANNED_WORDS 范畴。"Always" 一词在此语境下是参数约束，非 agent 决策规定，故不在禁词扫描白名单之列。

**未来禁词扩展**（scope 决策准则）：如需将新词加入全局 BANNED_WORDS（如 `spike/dip/jump`），触发条件须有实测数据支撑（观察期 tool 输出 log 中实际出现该词 + 判定该词为评价词而非纯事实），而非预防性猜测（YAGNI）。

### 4.2 Drift 测试更新

| 测试 | 改动 |
|---|---|
| `test_persona.py::test_layer1_bullet_count_25` | 改为 `test_layer1_bullet_count_5`，断言 `bullet_count == 5` |
| `test_persona.py::test_layer1_includes_get_price_pivots` | **删除** — 验证逻辑不再适用（Layer 1 不再含工具关键词）|
| `test_persona.py::test_layer1_no_tool_invocation_descriptions`（**新增**）| grep `\bUse get_\w+` / `\bUse <tool_name>\b` 等工具调用模式，断言 Layer 1 不含；guard `## How to Think` 仍是 split key |
| `test_persona.py::test_prompt_contains_missing_tool_guidance` | **删除** — 验证 `"performance"` / `"trade_journal"` / `"get_active_alerts"` 关键词存在（L65/L66/L71）；这些来自 L37 + L35 bullets，移入 docstring 后断言失效 |
| `test_persona.py::test_prompt_set_next_wake_one_shot` | **删除** — 验证 `"one-shot"` 关键词（L78），来自 L31，移入 set_next_wake docstring 后断言失效 |
| `test_persona.py::test_prompt_contains_memory_quality_guidance` | **删除** — 验证 `"actionable"` / `"not worth saving"` 关键词（L48-49），来自 L30，拆移到 save_memory + get_memories docstring 后断言失效 |
| `test_persona.py::test_prompt_contains_layer1_identity` (L7-19) | **改写 + scope 限制** — 当前测试在整 prompt 上 grep，Layer 1 瘦身后 `timeframe` / `memory` 仅靠 Layer 2 兜底命中，名实不符。改为 scope 限到 Layer 1：`layer1 = prompt.split("## How to Think")[0]`，断言只剩 Layer 1 真实保留的关键词（L26 `perpetual` / L26 `one-way` 或 `single direction` / L27 / L28 `fill` / L27 / L28 / L34 `woken`）。`timeframe` / `memory` 两条断言**删除**（Layer 1 已无对应 bullet，该两词归 Layer 2 范畴，由 `test_prompt_contains_layer2_thinking_framework` 间接覆盖）。同步加 `assert "## How to Think" in prompt` 的 split-key guard（与 `test_layer1_bullet_count_5` 一致风格）|
| `test_persona.py::test_prompt_l27_softened`（**新增**）| 验证 L27 软化生效：`assert "do not skip market data" not in prompt_lower`；`assert "structural support/resistance" not in prompt_lower`；`assert "use market data" in prompt_lower`（保留软化版关键词）|
| `test_persona.py::test_prompt_l65_softened`（**新增**）| 验证 L65 单向决策子句删除：`assert "arbitrary percentage" not in prompt_lower`；`assert "at a structural level" not in prompt_lower`；`assert "where is the logical stop loss" in prompt_lower`（保留开放问题）|
| `test_trader_agent.py:84` | `len == 30` → `len == 31`；注释 `(19+10+1)` → `(20+10+1)` |

### 4.3 测试规模预估

818 baseline → ~840-860（+22-42）：

**双口径**：本节 fact-only 项采用 **test cases**（pytest 收集量）口径汇总；persona 项采用 **测试函数**口径汇总。两者独立计算后相加。

- **N8 拆分**：原 7 个 `test_critical_alerts_*` 重组为 10 个独立测试函数（每侧 5 个 — no_service/format/empty/passes_params/unavailable），净 **+3 函数**（详见 §3.4 拆分清单）
- **Fact-only 测试**：新增 18 个测试函数（17 老/新感知/memory 工具单独 — 含 `test_get_price_pivots_global_wordlist_fact_only` 在内 + 1 batch parametrize 覆盖 10 执行工具），pytest 收集后展开为 **27 cases**（17 + 10 parametrize = 27），净 **+18 函数 / +27 cases**（与 §3.5 "17 + 1 = 18 个新增测试函数" 一致，price_pivots 全局函数已含在 17 内）
- **Persona drift 新增**：1 个新增（`test_layer1_no_tool_invocation_descriptions`），净 **+1 函数**
- **Persona 测试断裂处置**：4 个 **删除**（`test_layer1_includes_get_price_pivots` / `test_prompt_contains_missing_tool_guidance` / `test_prompt_set_next_wake_one_shot` / `test_prompt_contains_memory_quality_guidance`）+ 1 个 **改名**（`test_layer1_bullet_count_25` → `_5`，函数数不变）+ 1 个 **改写 + scope 限制**（`test_prompt_contains_layer1_identity`，函数数不变），净 **-4 函数**
- **L27 + L65 软化测试**：新增 2 个（`test_prompt_l27_softened` + `test_prompt_l65_softened`），净 **+2 函数**（详见 §4.2 新增两行）

**汇总**：函数数净 +3 + 18 + 1 - 4 + 2 = **+20 函数**；pytest cases 净 +3 + 27 + 1 - 4 + 2 = **+29 cases**。

**818 + 29 ≈ 847，落 +22-42 范围内**。

## 5. Acceptance Criteria

1. ✅ persona.py Layer 1 仅 5 个 bullet，全是跨工具行为规范
2. ✅ Layer 2 L65 删除 "at a structural level, not an arbitrary percentage" 单向规定
3. ✅ Layer 3 个性 + trading_style 段不动
4. ✅ 31 个 `@agent.tool` 函数 docstring 全部统一 Google format（first paragraph + `Args:` 段）
5. ✅ 7 个老工具 docstring 内容充实（含调用时机引导）
6. ✅ 20 条原 Layer 1 bullet 内容全部移入对应工具 docstring，无信息丢失
7. ✅ L27 Layer 1 + L50 docstring 软化措辞，删除单向决策暗示
8. ✅ `get_critical_alerts` 拆为 `get_exchange_announcements` + `get_macro_calendar`，REGISTERED_TOOL_NAMES `len == 31`
9. ✅ 31 个工具运行时输出全部通过全局 `FACT_ONLY_BANNED_WORDS_RE` 检测
10. ✅ `tests/test_fact_only_wordlist.py` 覆盖所有 31 工具至少 1 个代表性场景
11. ✅ 818 → ~840-860 passed，零 regression
12. ✅ Iter 4 单 PR 落地（不拆 4a/4b）
13. ✅ persona.py L24 标题 `## Tool Usage Notes` 改为 `## Cross-Tool Behavior`（与 §1.3 改动文件汇总一致）

## 6. 风险与 Trade-off

### 6.1 删 Layer 1 工具 bullet 无回归测试覆盖（红色挑战）

项目用 TestModel + `ALLOW_MODEL_REQUESTS=False`，**无真实 LLM 行为测试**。删 20 bullets 后 LLM 实际工具发现率 / 调用模式是否变化 — 观察期前没法证伪。

理论支撑：pydantic-ai 内置 docstring → tool description 自动传 LLM；LLM 通过 tool schema 看到全部 31 工具。但理论 ≠ 实证。

**缓解**：
1. 4.1 docstring audit 内容充实 — 即使 Layer 1 删除也保证 LLM 能从 docstring 完整理解每工具（含调用时机引导）
2. Iter 4 单 PR 落地（不拆 4a/4b），但 commit 历史可分（先 docstring，再 Layer 1） — 万一观察期发现 Layer 1 删除有问题，可单独 revert Layer 1 改动不动 docstring
3. 观察期 metrics（tool_calls 表）首批 2-4 周数据可作为"工具发现率"指标，触发 follow-up 决策

**判定 heuristic（粗略候选，PR 阶段根据观察期实测调整）**：

TradeBot 系统首次进观察期，**没有"修订前 prompt"的 baseline 可对比** — 无法用"调用频率下降 X%"这类量化阈值。候选触发条件（任一满足即评估 Layer 1 恢复）：

- 某感知工具在首批 4 周内调用次数 `< 5` 且当周期内发生过 `fill` 或 `alert trigger`（即 agent 处于活跃 trading 期，工具被忽视而不是单纯无调用机会）
- 感知工具调用分布过度集中（top-3 工具占感知调用总量 `> 80%`，剩余 ≥17 个感知工具被边缘化）
- 拆分后的 `get_exchange_announcements` 或 `get_macro_calendar` 任一 `0` 调用（拆分后 LLM 完全忽视一边）
- **fill 触发后 SL/TP 设置率下降**（与"工具发现率"并列观察）：L27 `check the chart` 通用动作引导被一并删除（详见 §3.2），fill → SL/TP 设置的桥梁现仅靠 `get_market_data` docstring + Layer 2 引导。如观察期发现 open fill 后未能在合理时间窗内（如下一 wake cycle）设置 SL/TP 比例 `> 20%`，提示该删除过于激进，需评估恢复 L27 chart 动作引导

具体阈值在 implementation PR 描述中根据观察期前几日 baseline 实测重新校准（避免硬编码）。

### 6.2 4.1 工作量风险

预估 1.5-2 天。31 工具 audit + 7 老工具补完 + 20 条 bullet 移入 + 措辞软化是体力活。如实际超预算，考虑 implementation 阶段拆 commit（先 docstring，再 Layer 1，再 N8 拆分）但 PR 不拆。

### 6.3 N8 拆分对 NewsService 接口的潜在反向影响

NewsService 当前已分 `get_announcements` + `get_macro_events` 双方法，拆分工具不需改接口。但需要确认现有 NewsService 实现确实独立缓存 / 降级 — 已在 spec §3.4 明示。

### 6.4 docstring 长度控制

部分 docstring 内容（如 L42 macro_context 的 DTWEXBGS vs DXY 长说明）移入后 docstring 会变长。控制原则：单 docstring 不超过 ~10 行（含 Args 段）；超长内容只保留核心，extra 知识放代码注释或下游服务文档。

### 6.5 单 PR 长度风险

预估 plan 1500-2000 行。bullet 个案分类（§2）落 spec 不放 plan，让 plan 聚焦执行步骤。Implementation 阶段 commit 拆分让 git history 可读。

## 7. 不在 Iter 4 范围（推 Iter 5 或观察期）

| 议题 | 归属 |
|---|---|
| `docstring_format='google'` Agent 配置启用 | Iter 5.3 |
| `require_parameter_descriptions=True` Agent 配置启用 | Iter 5.3 |
| UsageLimits / ModelRetry / pydantic-ai 版本固定 / logfire | Iter 5 |
| `prepare=` / `prepare_tools=` 动态工具评估 | 观察期数据驱动 |
| Iter 3 §7 follow-up（volume profile / touch count / 等 7 项）| 观察期 |
| Layer 3 personality / trading_style 段审计 | 不做 — 用户主动选择风格的硬性规则是风格本身 |
| Tool 实现 docstring（tools_perception.py 等内部）| 不做 — pydantic-ai 不读这层 |
| TradingDeps 6 个 `object \| None` 类型清理 | 观察期后独立 PR |
| N6 HTF volume + MA 斜率 hardening | 观察期数据驱动 |

## 8. 后续 iteration 衔接

Iter 4 完成后状态：
- Layer 1 5 bullets，纯跨工具行为规范
- 31 工具 docstring 内容充实 + Google format 统一
- 31 工具运行时输出全部通过 fact-only 全局 wordlist
- L27 + L50 + L65 单向决策暗示清理

进入 Iter 5（pydantic-ai 框架合规）：
- 5.1 UsageLimits（cli/app.py:143 `agent.run()` 加 `usage_limits=`）
- 5.2 ModelRetry（措辞 + wiring，Agent retries=2 + 1-2 工具试点）
- 5.3 框架配置：`docstring_format='google'` + `require_parameter_descriptions=True`（Iter 4 docstring audit 是这里启用的"测试网"）+ pydantic-ai 版本固定 `>=1.78,<2`
- 5.4 logfire instrumentation

Iter 5 完成后进观察期，pre-observation 6-iteration 计划闭环。

---

**End of spec.**
