# Iter W2 R2-8c — Tool Output Optimization

**Date**: 2026-05-03
**Status**: Spec (brainstorm done, plan/impl pending)
**Branch**: `feature/iter-w2r2-8c-tool-output-optimization`
**议题序**: W2 prep round 2 — R2-8a ✅ → **R2-8c** → R2-8b → R2-9 → 启 W2

---

## 0. TL;DR

把 cycle log tool 行展示从 "display-only 80-char fallback" 重构为 **"Tool 输出 unified sectioning + Display 端 universal section-aware 裁剪"** 双层架构。Tool 端按统一 `=== Section ===` 格式输出 LLM 友好的结构化内容（含 happy path + L2 失败 fallback 路径，§4.1.4）；Display 端 byte-equal 显示非省略部分（section body ≥10 行触发 head/tail 简化，含密集表格 + 多 entry list；可能拆条目，部分详情查 system.log 500-char debug dump (cli/app.py:338，超长 entry 仍可能截断)）。Trader 看 display 与 LLM 端字面一致 (modulo head/tail 简化)，能直接 audit prompt 质量。

| 改动维度 | 范围 |
|---|---|
| 主入口 1 | `src/agent/tools_perception.py` — 20 perception 工具按 sectioning convention 重构（其中 `get_memories` 仅走 display fallback，不强制 tool 端 sectioning — §4.2.13 / §8.8 例外）|
| 主入口 2 | `src/cli/display.py` — `_render_action` 重构 + 新增 `_parse_sections` / `_clip_body` / `_render_perception_tool` helpers + `_render_reasoning` `max_chars` 800→2000 (D10) |
| Schema | 不动（R2-7 `agent_cycles` schema 已铺好）|
| 改动量 | ~820-1070 行（逻辑代码 ~670-820 含测试 + snapshot 更新 ~150-250 单算；P2-1 review 决议保留 8 个 perception parser 改名为 `_SYSTEM_LOG_PERCEPTION_PARSERS` 不删，避免 system log 摘要退化，详见 §5.2）|
| 关联 | R2-8a ✅ landed (PR #36, 2026-05-03) / R2-8b (N10) 后续 / R2-9 终验 / N12c thinking 截断顺带决议 (D10) |

## 1. 议题源由

### 1.1 R2-8a 拆出 (D4/D5/D11)

R2-8a brainstorm（2026-05-02）末段浮现"工具输出优化"二级议题，但当时三个决议 D4（长尾 fallback 升级 80→200）/ D5（8 工具 L1 multi-line parser）/ D11（mixed C 形态）全是构想——基于 source 推断"应该长什么样"，未经 reasoning 实际引用频率验证 + 字段优先级排序 + 价值密度评估。

R2-8a 决议拆出 R2-8c 为独立议题（spec §8.1），让议题得到完整 brainstorm→spec→plan→impl 待遇。memory `project_r2_8c_tool_output_optimization` 详细记录拆出论据。

### 1.2 数据驱动入口

R2-8a landed 后跑 sim #6 短 smoke（14 cycles / 80 reasoning segments），4 维度数据完整 capture，落地 `.working/r2-8c-smoke-data-2026-05-03.md`：

| 维度 | 关键 finding |
|---|---|
| **B1 工具频次** | get_market_data 27 calls 最高频；get_macro_context 仅 2 calls 偏低 |
| **B2 reasoning 字段引用率** | get_market_data 100% / multi_tf 93% / recent_trades 86% / derivatives 79% / pivots 71% / market_news 64% / order_book 57% / HTF 29% / macro_context 7% |
| **B3 thinking 长度分布** | min 49 / median 1100 / mean 1618 / max 6059 chars per segment；**当前 800 截断率 47/80 = 58.8%** |
| **B4 工具返回长度分布** | 6 个高频 perception 工具真实 ≥500 chars；当前 80-char fallback **丢 80%+ 信息密度** |

### 1.3 议题与 W2 启动关系

R2-9（W2 startup smoke）需 R2-8a + R2-8c + R2-8b 全部 landed 后才启。R2-8c landed 后启动 R2-8b（N10 reasoning 注入）。R2-9 顺带 capture 24-48h 真实分布作 R2-8c follow-up（N=10 阈值 / 2000 thinking 截断 / MA disambiguate）数据基线。

## 2. 当前状态分析

### 2.1 Display 80-char fallback 痛点（核心矛盾）

R2-8a 已经把 cycle log 升级为 "think→act→…→decision 完整决策报告" 形态，但 `▾ Action` 段内的 tool 行 summary **维持 80-char fallback**，与"完整决策报告"诉求**结构性矛盾**：agent reasoning 引用 "RSI 33 / MACD -131 / MA50 -1.3%" 时 session log 看不到证据。

当前实施（`src/cli/display.py:572-608` `_render_action`）：
```python
def _render_action(tool_calls, returns_lookup, cycle_id):
    ...
    for tcp in tool_calls:
        ...
        icon, summary = resolve_tool_display(tcp.tool_name, content_str, outcome, args)
        line = f"  {icon} {tcp.tool_name:<22} {escape(summary)}"
        lines.append(line)
```

`resolve_tool_display` → `summarize_tool` → `_PERCEPTION_PARSERS` (8 个 regex parser) 或 `_fallback_summary(content, max_len=80)`。8 个 parser 反向解析 tool 输出提关键字段（最多 80 chars），其余 tool 走 fallback 截 80 chars。

### 2.2 Smoke #6 实证数据

完整数据见 `.working/r2-8c-smoke-data-2026-05-03.md`。

**Smoke 数据 caveat**（smoke §3.5 self-acknowledge）:
- 14 cycles 样本小（仅一阶趋势）
- B2 patterns 隐式引用未捕获 + MA 字段在 `get_market_data` / `get_higher_timeframe_view` 间 disambiguate-pending
- 短 session 不代表长 W2 trading 分布

**数据使用原则**: brainstorm 期（D3 决议前）用 B2 引用率做 R2-8c **scope 决策**（D8 perception only 而非全 24 工具）+ 字段优先级 reference；**D3 universal rule 决议后所有 perception 工具走相同 dispatch + clipping，不存在 per-tool tier 差异**（其中 19 工具 `_SECTIONED_PERCEPTION_TOOL_NAMES` 强制 sectioning + get_memories 走 display fallback，详见 §4.4 三层集合）。引用率仅作 §4.2 enum 时字段排序参考 + R2-9 W2 smoke 后字段精炼 follow-up 触发依据。

**关键 implication**（仅 reference，非 tier 划分）:
- **高引用率工具**（B2 ≥ 70%）: get_market_data 100% / get_multi_timeframe_snapshot 93% / get_recent_trades 86% / get_derivatives_data 79% / get_price_pivots 71% — §4.2 enum 时字段细致排序
- **中引用率**（B2 50-70%）: get_market_news 64% / get_order_book 57%
- **数据低 / disambiguate-pending**: get_higher_timeframe_view 29% (MA pattern 重叠) / get_macro_context 7%
- **N12c thinking 截断**: 800 baseline 实际过小（median 1100 > 800），D10 升级 2000

R2-9 W2 smoke 应重跑 disambiguated patterns + 长 session 数据，作 R2-8c follow-up（minor PR）触发依据。

### 2.3 R2-8a 已铺好的视觉框架

R2-8a 的 5 段架构（Header / Reasoning / Action / Decision / Footer）+ ThinkingPart 渲染 + retry-exhausted forensic 已经把 cycle log 框架打通。R2-8c 仅在 `▾ Action` 段内做"工具输出层"完整度补全，**不动 cycle 框架**。

### 2.4 现有 sectioning convention 部分一致

调研 20 perception 工具实际输出格式（详见 §4.2 enum），发现：
- **9/12 长尾工具已用 `=== Section ===` 风格**（trade_journal / active_alerts / performance / exchange_announcements / macro_calendar / etf_flows / stablecoin_supply / order_book / 部分场景 macro_context）
- **3/12 长尾工具用 implicit sections**（get_position / get_account_balance / get_open_orders）
- **8 候选工具**部分用 `=== Section ===`（market_data / higher_tf_view / market_news / macro_context / pivots），部分单 block（multi_tf / recent_trades / derivatives）

R2-8c 把 sectioning convention **统一化** + **完整化**——已用 convention 的工具 minimum 改动；未用的升级；单 block 的拆分。

## 3. Design Overview

### 3.1 4 个核心决议（高 level abstraction）

| # | 决议 | 实施层 |
|---|---|---|
| **D1 Sectioning Convention** | Tool 重构 perception 工具按统一 `=== Section ===` 格式输出 — **`_PERCEPTION_TOOL_NAMES` (20，D8 scope) 含 get_memories 走 fallback path；`_SECTIONED_PERCEPTION_TOOL_NAMES` (19) 是强制 sectioning enforcement 范围**（详见 §4.4 三层集合 + §4.2.13 / §8.8 例外说明）| Tool 层 |
| **D3 Display Universal Rule** | 单一 generic 规则——section header 总 keep / body < 10 行 keep all / ≥ 10 行 head+tail+`[N rows omitted]` | Display 层 |
| **D13 Byte-equal 约束** | Display body 字面一致 LLM 端（rendering envelope 自由：icon / indent / 框架元素 / 裁剪标记）| 跨层 |
| **D14 Display-as-Canary 原则** | Display 端不补救（不做视觉重排 / 数值美化），tool 端格式问题在源头解决 | 工程哲学 |

### 3.2 全部 14 决议汇总

| Tool 层 | Display 层 | 框架 |
|---|---|---|
| D1 sectioning convention | D3 universal section-aware rule | D2 LLM priority: 完整 > 友好 > display 适配 |
| D8 scope: 20 perception only | D4 裁剪规则三档 | D9 sequencing: A 合并 spec |
| | D5 短/非关键 keep | D13 byte-equal 约束 |
| | D6 密集/关键 head/tail (universal) | D14 Display-as-Canary 原则 |
| | D7 N=10 阈值 | |
| | D10 thinking 截断 800→2000 | |
| | D11 列对齐: perception multi-line 自动消解，execution 维持 `<22` | |
| | D12 工具行视觉强化: defer | |

### 3.3 完整 mockup（基于 sim #6 cycle 重 reframe）

R2-8c 后 cycle 内 `▾ Action` 段渲染示例（4-tool cycle，含 perception multi-line + execution single-line 混搭）：

```
▾ Action (4 tools)
  ⚙ get_market_data
    === Ticker (BTC/USDT:USDT) ===
    Price: 75,212.00 | Bid: 75,200.00 | Ask: 75,215.00
    24h High: 76,225.00 | Low: 74,893.00 | Volume: 8,200.00

    === Technical Indicators (5m) ===
    RSI(14): 33.55
    MACD: -131 (sig -98, hist -33)
    Bollinger Bands: 74,800 — 75,890
    SMA20: 75,345
    EMA12: 75,180
    EMA26: 75,290

    === Market Context ===
    ATR(14): 218.50 (0.29% of price, 5m candles)
    Volume: 8,200.0 (6.20x avg)
    50-candle Range: 74,893 — 76,225

    === Recent Candles (5m, last 50) ===
    Time         Open       High        Low      Close        Vol
    14:00     75,250.00  75,300.00  75,180.00  75,220.00     320.5
    [... 47 rows omitted ...]
    18:00     75,200.00  75,235.00  75,180.00  75,210.00     298.7
    18:05     75,180.00  75,220.00  75,150.00  75,212.00     310.2
  ⚙ get_position
    === Position (BTC/USDT:USDT) ===
    Side: Long | Contracts: 0.025 | Entry: 78,518.00
    Leverage: 5x | PnL: +0.10% (+0.20 USDT)
    Liquidation: 70,666.00 | Unrealized: +0.20 USDT
  ⚙ set_next_wake          5min
  ⚙ open_position          Order submitted: long 0.025 @ ~$78,518, 5x
```

**视觉 hierarchy**:
- `▾ Action (N tools)` — section 标题（R2-8a 已存在）
- `  ⚙ tool_name` — 2-space indent，tool 起点
- `    === Section ===` — 4-space indent，section header
- `    body lines` — 4-space indent，section body（与 header 同级，因为 byte-equal LLM 端无 indent）
- `  ⚙ execution_tool          summary` — execution 单行 + `<22` padding（自然区分 perception block）

## 4. 段级设计契约

### 4.1 Tool 端 Sectioning Convention (D1)

#### 4.1.1 Section Header 格式

```
=== {Section Name} ===
```

- 名称用 noun phrase + Title Case（如 `Ticker (BTC/USDT:USDT)` / `Technical Indicators (5m)` / `Recent Candles (5m, last 50)`）
- Header 前后无装饰（无空行强制）；section 间用空行分隔（`\n\n`.join sections）
- 同 tool 内 section name 唯一（避免 ambiguity）
- **Footer / note / caveat 必须独立 `=== Note ===` section**（review P2.1 校准 — 不允许无 header trailing paragraph，避免 T-DG-1 / byte-equal 测试 ambiguity）
- **参数顺序 convention**（review 校准 — 工具级 section header 参数顺序统一）:
  - 多参数: `(symbol[, timeframe[, scope]])` 顺序，例 `Higher Timeframe View (BTC/USDT:USDT, 4h)` / `Price Pivots (BTC/USDT:USDT, main TF: 1m)`
  - 仅 timeframe（无 symbol ambiguity，工具级符号已 fixed）: `Technical Indicators (5m)` 维持
  - 单参数无 ambiguity: `Recent Candles (5m, last 50)` 维持（symbol 已在父 tool name 隐含）
  - **Plan 阶段 enforce**: §4.2 enum 时按此 convention 立各工具 section names；T-DG-1 lint 检查参数顺序符合 convention

#### 4.1.2 Section 内字段格式

| 模式 | 示例 | 适用 |
|---|---|---|
| **Key-value 单行** | `RSI(14): 33.55` | 关键字段，1 字段 1 行 |
| **Inline 多字段** | `Price: 75,212.00 \| Bid: 75,200.00 \| Ask: 75,215.00` | ≤4 字段且各自短 |
| **表格行** | `14:00     75,250.00  75,300.00  ...` | 重复结构数据（如 candles, trades buckets）|
| **数值精度** | `75,212.00`（千分位 + 2 decimal）| Tool 端定，display byte-equal |

**注（D14 Display-as-Canary）**: 数值格式（千分位 / decimal / dollar 符号）由 tool 端定一次，display 端不二次美化。如 trader 觉得 `75212.00` 不易读，应改 tool 端到 `75,212.00`，因为大概率 LLM 也"别扭"（紧凑数值降低 LLM selective attention 效率）。

#### 4.1.3 Section 划分原则

| 原则 | 说明 |
|---|---|
| **语义内聚** | 同一 conceptual unit 一个 section（如 Ticker / Indicators / Context 三个独立 unit）|
| **字段相关性** | 一起被 reasoning 引用的字段进同 section（B2 数据可作 ground truth）|
| **避免 single-field section** | 1 字段不开 section，inline 进相关 section |
| **Conditional section 上限** | 1 工具内 ≤2 个 conditional section（避免 schema 漂移过度）|

#### 4.1.4 边界处理

**失败 contract 三档**（review 校准 — 区分 `outcome != success` vs tool 内捕获 vs per-source）:

| 失败档 | 触发条件 | 处置 | 进 multi-line？ |
|---|---|---|---|
| **L1: pydantic-ai outcome 失败** | `is_tool_error(name, content, outcome) == True` (outcome != "success" 或 execution success prefix mismatch) | R2-8a 单行 `✗ {tool_name:<22} {fallback_summary}` 渲染 | **不进** multi-line（dispatch 前置 branch，§4.4）|
| **L2: tool 内捕获异常 + success outcome 返回 whole-output degradation** | tool 实现内部 try/except → 仍 return success outcome 但内容是 fallback 描述 | tool 端可返回单 `=== Error ===` section + 错误描述 | **进** multi-line（走 perception render，section 显示 Error）|
| **L3: per-source / per-field degradation** | tool 部分数据源失败但其余数据可用 | 保留在对应 section 内字段 fallback (`(unavailable)` / `Temporarily unavailable.` 等短描述) | **进** multi-line（section 完整 render，字段级 fallback）|

**其他边界**:

| 情况 | 处置 |
|---|---|
| Section 内某些字段缺失 | 同 L3 — 字段省略 或 `Field: (unavailable)` |
| 单 section 工具（如 get_account_balance）| 仍用 `=== Section ===` header（consistency 优先）|
| Tool 输出无 sections | Display universal rule 视为"1 个无名 section"（fallback 路径，§4.3.1）|
| Section header 含 markup 字面值 | Display 端 `escape()` 处理（§4.3.3）|

**AC 覆盖**: AC1 / T-DG-1 仅检查 happy path + **各工具 enum 标注的 existing L2 paths**（tool 已有内部 try/except + success outcome 返回 `=== Error ===` section）的 sectioning 一致性 — **不一刀切要求每工具有 L2**（review P1.1 — L1 hard raise 不强制重构）；L1 走 R2-8a 单行不参与 lint；L3 在 §4.2.* enum 各工具内列举具体字面（不 lint level），plan 阶段 enum table 时显式列。

### 4.2 20 perception 工具 sections 设计 enum

每工具列出：sections 划分 + 关键字段 + 边界 case。具体 verbatim format string 见 source code（重构时 reference）。

**统一 fallback 规则** (review P1 校准 — 区分 L1 vs L2 design choice，避免改变错误语义)：

**L1 vs L2 区分原则**（review 校准 — 不为 lint 改变 error semantics）:
- **L1 = 当前 hard raise / outcome != success 路径**: 保持 raise，pydantic-ai catch；R2-8c **不为满足 lint 重构为 try/except**（重构会破坏 outcome != success 信号、error_type 结构化错误、is_tool_error() 识别、metrics/retry/observability 层）。Display 走 R2-8a 单行 ✗ 路径，不进 multi-line render，不进 lint。
- **L2 = tool 当前已有内部 try/except + success outcome 返回 fallback**: 这类 fallback **必须 sectioned**（如 `News service not configured.` → `=== Error ===\nNews service not configured.`），按 `=== Error ===` section 输出。包括：
  - 已有 try/except 包裹的 service unavailable / not configured fallback
  - 设计明确选择 success outcome 的 degradation path（plan 阶段 enum 标注）
- **L3 = per-source / per-field 部分失败**: 保留在对应 section 内字段 fallback（`(unavailable)` / `Temporarily unavailable.` 短描述）

**Plan 阶段任务**: 每工具 enum 时同时列 "happy path sections" + **"existing L2 paths"**（read tool source code 找内部 try/except + success outcome 的 fallback）+ "L3 字段级 fallback 描述"；T-DG-1 lint **仅验证 happy path + 各工具标注的 existing L2 paths**，不一刀切要求每工具都有 L2 路径（部分工具仅有 L1 / L3 是 by design）。

**R2-9 W2 重点观察对象（多 entry list 工具，head/tail 可能拆条目）**: get_market_news (Symbol News / General News 多 entry) / get_trade_journal (Trade Journal 多 entry)。R2-9 smoke 后评估 trader 反馈是否需要 fine-tune（minor PR：调 N 阈值 / head-tail 数 / 引入 SectionType — 后者属 substantive design 改动，独立议题）。

#### 4.2.1 get_market_data (`tools_perception.py:39`)

| Section | 字段 | 备注 |
|---|---|---|
| `=== Ticker ({symbol}) ===` | Price, Bid, Ask, 24h High, 24h Low, Volume | inline 多字段 |
| `=== Technical Indicators ({timeframe}) ===` | RSI(14), MACD (sig + hist), Bollinger Bands, SMA20, EMA12, EMA26 | 1 字段 1 行 |
| `=== Market Context ===` | ATR(14) + %, Volume vs avg, 50-candle Range | 1 字段 1 行 |
| `=== Recent Candles ({timeframe}, last {N}) ===` | OHLCV 表（header + N rows）| **密集表格，N=50 触发 head/tail** |

边界: indicators 不可用 → 字段省略；ATR/Volume 缺失 → Context section 字段省略。

#### 4.2.2 get_higher_timeframe_view (`tools_perception.py:793`)

| Section | 字段 |
|---|---|
| `=== Higher Timeframe View ({symbol}, {timeframe}) ===` | Current Price |
| `=== MA Distances ===` | MA50, MA100, MA200（each + price vs MA %）|
| `=== Range Position ===` | 100-period High, Low, Current within range % | conditional: `len(df) >= 100` |
| `=== 20-period Band ===` | 20-period High, Low, range width % | conditional: `len(df) >= 20` |

边界: data insufficient → MA/Range 字段 `insufficient data (need {N} candles)`。

#### 4.2.3 get_multi_timeframe_snapshot (`tools_perception.py:1310`)

| Section | 字段 |
|---|---|
| `=== Multi-TF Snapshot ({symbol}) ===` | Current price + columns 说明 + per-TF rows (Momentum / Structure / Volatility / Range pos) |

单 section 内表格结构（4 TFs default 5m/1h/4h/1d，4 行 < 10 keep all）。边界: per-TF 失败 → `{tf}: temporarily unavailable` 行。

#### 4.2.4 get_price_pivots (`tools_perception.py:1518`)

| Section | 字段 | 备注 |
|---|---|---|
| `=== Price Pivots ({symbol}, main TF: {tf}) ===` | Current Price | header section |
| `=== Levels Above Current Price ===` | Above pivot levels（各 1 行，含 N bars ago）| 行数 variable，可能触发 head/tail |
| `=== Levels Below Current Price ===` | Below pivot levels | 同上 |
| `=== Swing Status ===` | Swing pivot 状态 | conditional |
| `=== Prior Period H/L ===` | Prior daily/weekly/monthly H/L | conditional |

边界: no pivots → `(none)`；data insufficient → status section 描述原因。

#### 4.2.5 get_market_news (`tools_perception.py:539`)

| Section | 字段 | 备注 |
|---|---|---|
| `=== Fear & Greed Index ===` | Value (with title), Updated date | always present |
| `=== Symbol News ({base}, {N}) ===` | per-entry: timestamp, title, source, currencies | conditional, 行数 variable |
| `=== General Crypto News ({N}) ===` | 同上 | conditional, 行数 variable，**通常触发 head/tail** |
| `=== News ===` | 服务状态 / "No recent headlines." | fallback |

边界: FGI 不可用 → `FGI service temporarily unavailable.`；news 全失败 → 单 News section fallback。

#### 4.2.6 get_macro_context (`tools_perception.py:909`)

| Section | 字段 |
|---|---|
| `=== Crypto Market ===` | BTC.D, ETH.D, Total Mcap, 24h change |
| `=== US Macro (FRED) ===` | USD Index, VIX, 10Y Treasury, 2s10s Spread, 10Y Inflation Expectation（each + as-of date）|
| `=== US Equities (Alpha Vantage) ===` | SPY, QQQ（each + change % + latest trading day）|

边界: per-source 不可用 → section 内 `Temporarily unavailable.`。**B2 7% 引用率仅作字段排序 reference，不影响是否进 R2-8c scope（D8 决议: 全 perception 重构）；D3 universal rule 对所有 perception 工具同等处理，不存在工具级 drop**。

#### 4.2.7 get_macro_calendar (`tools_perception.py:644`)

| Section | 字段 | 备注 |
|---|---|---|
| `=== Upcoming Macro Events (next {hours}h) ===` | per-event: timestamp, title, impact, content (optional) | always present |
| `=== Note ===` | footer caveat (e.g. "macro calendar covers current week only; Friday evening / weekend calls may miss next week's early events.") | optional — 仅在 events list 非 None 时显示 |

边界: 服务不可用 → 单 `=== Error ===` section (L2)；no events → `No upcoming macro events.` 在 Events section 内 (L3 描述)。

#### 4.2.8 get_etf_flows (`tools_perception.py:997`)

| Section | 字段 | 备注 |
|---|---|---|
| `=== BTC Spot ETF Flows (US) ===` | per-day: date + net inflow（首行含 cum + AUM）+ N-day net 总结 | 行数 < 10 keep all |
| `=== ETH Spot ETF Flows (US) ===` | 同上 | 同上 |
| `=== Note ===` | "Today's values may be revised." | optional footer |

边界: per-asset 不可用 → section 内 `Temporarily unavailable`；窗口数据不足 → `Insufficient data in requested window.`。

#### 4.2.9 get_recent_trades (`tools_perception.py:1227`)

| Section | 字段 |
|---|---|
| `=== Recent Trades ({symbol}, last {window}s, {N} × {bucket_duration}s buckets) ===` | header |
| `=== Time Buckets ===` | 5 行 buckets（each: t-Nmin / buy / sell / net）|
| `=== Total ===` | Total buy/sell, net, taker buy %, partial coverage marker if any |
| `=== Stats ===` | Trade count, avg size |

5 buckets + total + stats 总 ~10 行边界。可能 < 10 keep all 或刚好触发。

#### 4.2.10 get_derivatives_data (`tools_perception.py:698`)

**单 section + 字段级 fallback**（review 校准 — 原 5-section 设计违反 §4.1.3 ≤2 conditional 上限；改为单 section 后 derivatives 3 个独立数据源走字段级 fallback，符合 L3 处置档）:

| Section | 字段 |
|---|---|
| `=== Derivatives Data ({symbol}) ===` | Funding Rate (rate %, settlement time, sign + direction) / Open Interest (USD value) / Long/Short Ratio (ratio + %) / Data as of (oldest timestamp UTC) |

字段级 fallback (per-source 失败): `Funding Rate: (unavailable)` / `Open Interest: (unavailable)` / `Long/Short Ratio: (unavailable)`。
若全 3 数据源都失败: 整 tool 走 §4.1.4 L2 — 单 `=== Error ===` section。
单 section 行数 ≤ 5，keep all。

#### 4.2.11 get_position (`tools_perception.py:139`)

**当前 implicit sections，重构升级到 explicit headers**:

| Section | 字段 |
|---|---|
| `=== Position ({symbol}) ===` | Side, Contracts, Entry, Leverage, Liquidation, Unrealized |
| `=== PnL ===` | PnL (USDT + % of initial capital), Duration |
| `=== Risk Exposure ===` | Notional + % of limit |
| `=== Exit Orders ===` | OCO/SL/TP details | conditional |

边界: no position → 单 `=== Position ===` section 含 `No open positions.`；ATR 不可用 → 字段省略；hard failures → Risk/Exit blocks 降级，position + PnL + Duration 保留。

#### 4.2.12 get_account_balance (`tools_perception.py:298`)

**当前 implicit，重构升级**:

| Section | 字段 |
|---|---|
| `=== Account Balance ===` | Total (with initial), Return % + USDT (incl. unrealized), Free, Used |

单 section 4 行 keep all。边界: initial=0 → return % 0% 防 ZeroDivision。

#### 4.2.13 get_memories (`tools_perception.py:312`)

**特殊 case**: 当前 delegates to `deps.memory.format_for_prompt()`，输出格式由 memory backend 决定。

R2-8c 处置（三层集合定位 — 详见 §4.4 dispatch）:
- **`_PERCEPTION_TOOL_NAMES`** (20): **包含** get_memories — display 走 perception multi-line dispatch path
- **`_SECTIONED_PERCEPTION_TOOL_NAMES`** (19): **不包含** get_memories — T-DG-1 sectioning lint **跳过**此工具
- **Tool 层**: 不强制 sectioning convention（保留 memory backend 灵活性）
- **Display 层**: `_parse_sections` fallback path 处理（无 sections → `[Section(header=None, body=lines)]` 单无名 section，keep all 或 head/tail 按行数判定）
- **Spec acknowledge**: memory backend sectioning 升级属独立议题（§8.8），与 reflection tools wishlist 联动

#### 4.2.14 get_open_orders (`tools_perception.py:340`)

**当前 implicit，重构升级**:

| Section | 字段 |
|---|---|
| `=== Pending Orders ===` | per-order: type, side, amount, price + distance, ID（OCO 单行包含两 legs）|

行数 = 订单数，可能触发 head/tail。边界: 无 orders → `No pending orders.`。

#### 4.2.15 get_trade_journal (`tools_perception.py:383`)

**当前已用 explicit headers，minimum 改动**:

| Section | 字段 |
|---|---|
| `=== Performance Summary ===` | Total trades, Win/loss, Avg win/loss, Profit Factor, Recent summary |
| `=== Trade Journal ===` | per-entry: timestamp, action, side, price, fee, status, PnL, Reasoning |

Trade Journal section 行数 = limit (default 20)，可能触发 head/tail。边界: db_engine=None or no entries → `No trade journal entries yet.`；metrics=None → 省略 Performance Summary。

#### 4.2.16 get_active_alerts (`tools_perception.py:453`)

**当前已用 explicit headers，minimum 改动**:

| Section | 字段 |
|---|---|
| `=== Price Alert Settings ===` | Volatility threshold + window |
| `=== Active Price Level Alerts ({N}/{max}) ===` | per-alert: ID, direction, price, reasoning |

Price Level Alerts 行数 ≤ 20 (max alerts)，可能触发 head/tail。边界: vol params=None → `OFF`；list 空 → `No active alerts.`。

#### 4.2.17 get_performance (`tools_perception.py:479`)

**当前已用 explicit headers + implicit sub-sections，重构拆分**:

| Section | 字段 |
|---|---|
| `=== Trading Performance ===` | Initial Balance, Current Balance, Total Return, Realized PnL, Total Fees |
| `=== Trade Stats ===` | Total/winning/losing trades, Avg win/loss, Profit Factor, Max Drawdown, Best/Worst Trade |

各 section 5-7 行 keep all。边界: metrics=None or total_trades=0 → 省略 Stats section + `No completed trades yet.` placeholder。

#### 4.2.18 get_exchange_announcements (`tools_perception.py:614`)

**当前已用 explicit header，minimum 改动**:

| Section | 字段 |
|---|---|
| `=== Exchange Announcements (past {hours}h) ===` | per-announcement: timestamp, title |

行数 = announcement 数，可能 < 10 keep all 或触发。边界: 服务不可用 → `temporarily unavailable.`；无数据 → `No exchange announcements.`。

#### 4.2.19 get_stablecoin_supply (`tools_perception.py:1083`)

**当前已用 explicit header，minimum 改动**:

| Section | 字段 |
|---|---|
| `=== Stablecoin Supply ===` | per-coin: symbol, circulating supply, 7d change (USD + %) + Total Mcap line |

单 section 短行数 keep all。边界: 服务不可用 → `temporarily unavailable.`；无 tracked symbols → `data unavailable (no tracked symbols found in response).`。

#### 4.2.20 get_order_book (`tools_perception.py:1126`)

**当前已用 explicit header + implicit sub-sections，重构拆分**:

| Section | 字段 |
|---|---|
| `=== Order Book ({symbol}) ===` | Best bid (price × size), Best ask (price × size), Spread (abs + %) |
| `=== Depth (top {N} each side) ===` | Bids cumulative + range + depth %, Asks cumulative + range + depth %, Bid share + balance |
| `=== Concentrated Levels (size > 3× median of top {N}) ===` | per-level: bid/ask, price, size, distance from mid | conditional |

各 section 短 keep all。边界: empty/short book → `insufficient data (requested depth X, got Y).`；服务错误 → `temporarily unavailable.`。

### 4.3 Display 端 Universal Rule (D3 + D4)

#### 4.3.1 `_parse_sections` 算法

```python
@dataclass
class Section:
    header: str | None     # None = unnamed (fallback)
    body: list[str]        # body lines (no leading/trailing blanks within section)


def _parse_sections(content: str) -> list[Section]:
    """Parse tool content into sections by '=== {name} ===' headers.

    Algorithm:
      1. Split content by lines
      2. Identify lines matching r'^=== (.+) ===$' as section starts
      3. Group lines: each section = (header, body lines until next header)
      4. Strip blank lines at start/end of each body
      5. No header found in entire content → return [Section(header=None, body=lines)]
      6. Empty content → return [Section(header=None, body=[])]
    """
    ...
```

**Edge cases**:
- 多 headers + body 完整 parse
- 无 headers → unnamed section (fallback)
- header 间无 body → empty section（仍渲染 header 行）
- header 字符匹配但格式异常（如 `===Section===` 无空格）→ 视为 body 行

#### 4.3.2 `_clip_body` 算法 (D7 N=10)

```python
def _clip_body(body: list[str], n: int = 10) -> list[str]:
    """D4 universal clipping rule (head=2 / tail=2).

    body length:
      < n       → keep all
      >= n      → [body[0], body[1],
                   f"[... {len(body)-4} rows omitted ...]",
                   body[-2], body[-1]]

    Rationale (review feedback 校准):
      head=1/tail=1 在 bucket-style 数据 (Recent Trades 5 buckets) 触发后
      仅 keep 首尾，中段 5 行全裁 — 而 reasoning 引用率最高的 taker
      buy/sell net 字段（B2 86%）恰在中段。head=2/tail=2 让带 column
      header 的表格（Recent Candles）也能 keep "header + 1 数据行 + ...
      + 末 2 行" 的合理形态。

      Trade-off acknowledge (review P2-2 校准):
      Algorithm 按 physical row 裁，不感知 entry boundary。多 entry list
      (news / journal 每 entry 多行) 触发时 head/tail 可能拆条目（首末
      entry 部分行被取走）。Trade-off 接受度: trader 快速扫描看头尾
      pattern + 部分详情查 system.log 500-char debug dump
      (`return={content_str[:500]}` cli/app.py:338 — 超长 entry 仍可能
      被截断，不是完整 dump)；R2-9 W2 smoke 后评估 trader 反馈是否需
      要 fine-tune (调 N 阈值 / head-tail 数 / 引入 SectionType / 扩展
      system.log debug dump 上限 — 后两者属 substantive 改动，独立议题)。
    """
    if len(body) < n:
        return body
    return [
        body[0], body[1],
        f"[... {len(body) - 4} rows omitted ...]",
        body[-2], body[-1],
    ]
```

**N=10 阈值 + head=2/tail=2**: 覆盖 Recent Candles (51 行 = column header + 50 candles) 明显密集；不误伤 Indicators (6) / Recent Trades buckets 单 section (5)；边界 case (Pivots above/below 5-15 / News list 5-20) 动态触发——5-9 keep all / 10+ 简化。head=2/tail=2 让触发后仍保留 4 行（首 2 + 末 2），bucket-style 数据中段失真可控。R2-9 W2 smoke 后可 fine-tune（N 阈值或 head/tail 数，minor PR）。

#### 4.3.3 `_render_perception_tool` 算法

```python
def _render_perception_tool(tool_name: str, content: str) -> str:
    """Multi-line section render for perception tools (D8 + D13 byte-equal).

    Output format:
      "  ⚙ {tool_name}\n"
      "    {section.header}\n"
      "    {body line 1}\n"
      "    {body line 2}\n"
      "    ...\n"
      "\n"  # blank between sections
      "    {next section.header}\n"
      ...
    """
    sections = _parse_sections(content)
    lines = [f"  ⚙ {tool_name}"]
    for i, section in enumerate(sections):
        if i > 0:
            lines.append("")  # blank between sections
        if section.header:
            lines.append(f"    {escape(section.header)}")
        clipped_body = _clip_body(section.body)
        lines.extend(f"    {escape(row)}" for row in clipped_body)
    return "\n".join(lines)
```

**Escape 范围**: section header + body lines 都需 `escape()`——content 来自 tool 返回（可能含 LLM/external API 不可预知字符如新闻 headline 含 `[bold]` markup 字面值），有 markup attack surface。**仅 body / header 部分 escape，框架 markup（icon / column padding / blank lines）保留 Rich 渲染**。

### 4.4 `_render_action` 主路径 dispatch

```python
def _render_action(tool_calls, returns_lookup, cycle_id):
    n = len(tool_calls)
    plural = "tool" if n == 1 else "tools"
    lines = [f"\n▾ Action ({n} {plural})"]

    for tcp in tool_calls:
        ret = returns_lookup.get(tcp.tool_call_id)
        if ret is None:
            # R2-8a 现有兜底
            logger.warning("tool_call_id mismatch for %s in cycle %s", tcp.tool_name, cycle_id)
            lines.append(f"  ⚙ {tcp.tool_name:<22} [no return captured]")
            continue

        content_str = str(ret.content)
        outcome = getattr(ret, "outcome", "success")

        if is_tool_error(tcp.tool_name, content_str, outcome):
            # L1 失败档 (§4.1.4): R2-8a 单行 + ✗ icon (R2-8c 不改)
            lines.append(f"  ✗ {tcp.tool_name:<22} {escape(_fallback_summary(content_str))}")
        elif tcp.tool_name == "save_memory":
            # Memory write tool 单独 branch: R2-8a single-line + ✎ icon
            try:
                args = tcp.args_as_dict()
            except Exception:
                args = None
            icon, summary = resolve_tool_display(tcp.tool_name, content_str, outcome, args)
            lines.append(f"  {icon} {tcp.tool_name:<22} {escape(summary)}")
        elif tcp.tool_name in _EXECUTION_TOOL_NAMES:
            # Execution: R2-8a single-line + <22 padding (D8 / D11)
            try:
                args = tcp.args_as_dict()
            except Exception:
                args = None
            icon, summary = resolve_tool_display(tcp.tool_name, content_str, outcome, args)
            lines.append(f"  {icon} {tcp.tool_name:<22} {escape(summary)}")
        elif tcp.tool_name in _PERCEPTION_TOOL_NAMES:
            # Perception multi-line section render (含 get_memories fallback path)
            lines.append(_render_perception_tool(tcp.tool_name, content_str))
        else:
            # 未注册工具兜底: R2-8a single-line + warning (drift guard 应在测试层 catch)
            logger.warning(
                "tool_name %s not in _PERCEPTION_TOOL_NAMES / _EXECUTION_TOOL_NAMES — falling back to R2-8a single-line",
                tcp.tool_name,
            )
            lines.append(f"  ⚙ {tcp.tool_name:<22} {escape(_fallback_summary(content_str))}")

    return "\n".join(lines)
```

**三层集合定义**（review 校准 — 替代原 `_EXECUTION_PARSERS` dispatch 漏 `cancel_price_level_alert` 的 bug）:

```python
# Display dispatch — 互斥 + 完整覆盖 + drift guard 检查
_PERCEPTION_TOOL_NAMES: frozenset[str] = frozenset({
    # 8 候选 (smoke B2 cover): get_market_data, get_higher_timeframe_view,
    # get_multi_timeframe_snapshot, get_price_pivots, get_market_news,
    # get_macro_context, get_recent_trades, get_derivatives_data
    # 12 长尾: get_position, get_account_balance, get_memories,
    # get_open_orders, get_trade_journal, get_active_alerts, get_performance,
    # get_exchange_announcements, get_macro_calendar, get_etf_flows,
    # get_stablecoin_supply, get_order_book
    # 完整 enum 见 §4.2.1-4.2.20
    ...,  # plan 阶段 fill 完整 20 entries
})

_SECTIONED_PERCEPTION_TOOL_NAMES: frozenset[str] = (
    _PERCEPTION_TOOL_NAMES - frozenset({"get_memories"})  # 19 工具
)
# 用于 T-DG-1 sectioning lint — get_memories 是 backend-dependent 例外（§4.2.13）

_EXECUTION_TOOL_NAMES: frozenset[str] = frozenset({
    "open_position", "close_position", "set_stop_loss", "set_take_profit",
    "adjust_leverage", "place_limit_order", "cancel_order",
    "set_price_alert", "add_price_level_alert", "cancel_price_level_alert",  # 含 review 校准漏的
    "set_next_wake",
})  # 11 工具

# save_memory 单独 branch — memory write tool，R2-8a ✎ icon 路径
```

**关键 invariants**:
- Section header / body 行 byte-equal 于 `content_str` 对应行（D13；详见 §4.6 + T-BE-1 escape 比较口径）
- Indent (`    `) / icon (`⚙`) / `[... N rows omitted ...]` 是 rendering envelope（不算 byte-equal violation）
- `escape()` 应用到 section content + execution summary（不应用到框架 markup）
- 三层集合 + save_memory branch **互斥 + 覆盖所有 32 registered tools**（drift guard T-DG-2 验证）

### 4.5 D10 thinking 截断升级

```python
# display.py:558 (current 800)
def _render_reasoning(thinking_text: str, max_chars: int = 2000) -> str:
    ...
```

**变更理由**:
- Smoke B3: 当前 800 截断率 47/80 = 58.8%（过半 thinking 段被切）
- median 1100 > 800，800 baseline 对真实 trader cycle 结构性过小
- 升级 2000 后预计截断率 ~25%，覆盖 ~75-80% segments
- Thinking 在 LLM 端是 cost-free（生成完已固定），display 端 truncation 完全无 token 经济风险
- 单段 2000 chars ≈ 40 lines，比 Decision 段 markdown 内嵌短，视觉可接受

**测试**: 现有 `test_reasoning_*` 加 `1500-char input keep all` / `2500-char input → 2000 + suffix` 两个 case。

### 4.6 D13 Byte-equal 约束精确化

| 元素 | LLM 端 | Display 端 | 是否 byte-equal |
|---|---|---|---|
| Tool 输出 body（sections + fields）| 有 | 有 | **必须一致**（裁剪后的非省略部分）|
| Tool name 标签（`⚙ get_market_data`）| 无（pydantic-ai 用 tool_call_id 管理）| 有（envelope）| **不要求** |
| Indent（`    ` prefix）| 无 | 有 | **不要求** |
| Cycle Header / `▾ Action` / Footer | 无 | 有 | **不要求**（display-only envelope）|
| 省略标记（`[N rows omitted]`）| 无 | 有 | **不要求**（裁剪标记）|
| Section 内字段顺序 | 有 | 有 | **必须一致** |
| Section header (`=== Section ===`) | 有 | 有 | **必须一致** |
| 数值精度 / 千分位格式 | tool 端定 | 与 tool 端同 | **必须一致**（tool 端定一次）|

**精确化的 "byte-equal"**: **Tool 输出 body 在 Section model 层一致**（tool 内 sections / fields / 数值格式 / 排版，post-parse + post-clip + post-escape），display 端可加 rendering envelope（icon / indent / 框架元素 / 裁剪标记）。

**Escape 与 byte-equal 的口径**（review P1.2 校准 — Section model 比较，非 raw line bytes）:
- Display 端 `_render_perception_tool` 对 section header / body 做 `escape()` 处理（§4.3.3）
- `_parse_sections` 会 strip section body 首尾空行 + section 间插入 display-only blank line — 两层归一化让 raw `content.splitlines()` 比较失败
- T-BE-1 byte-equal 比较口径 (post-parse + post-clip + post-escape Section model):
  ```python
  parsed_display = extract_sections_from_render(_render_perception_tool(name, content))
  expected = [
      Section(
          header=escape(s.header) if s.header else None,
          body=[escape(line) for line in _clip_body(s.body)],
      )
      for s in _parse_sections(content)
  ]
  assert parsed_display == expected
  ```
- 即两端都经过 `_parse_sections` + `_clip_body` + `escape`，在 Section model 层比对（避免 Rich render `\[bold]` literal 转回 `[bold]` 的边界 + 避免 `_parse_sections` 归一化产生的 raw bytes 误报）
- T-EC-6/T-EC-7 单独覆盖 markup 字面值的 escape behavior（含字面 `[bold]` 的 content 应在 display 渲染为字面 `[bold]` 不被解释为 markup）

**测试**: T-BE-1 — 给定随机 tool content，按上述 invariant 在 Section model 层比较；raw `content.splitlines()` **不**作为比较基准。

### 4.7 D14 Display-as-Canary 原则

**Status**: working hypothesis（R2-8c 阶段未实证）。

**原则陈述**: 当 display 端照搬 LLM 端 tool 输出 trader 觉得别扭/不友好时，**不在 display 端 patch**（dollar 美化 / 视觉重排 / 缩进装饰），而是**回到 tool 端优化输出格式**。

**底层假设**: Trader 觉得别扭的格式，LLM 大概率也"别扭"（紧凑 inline / 字段命名不清 / 数值格式难解析）。Trader 体验是 LLM 端格式质量的**实证 proxy**。

**假设验证（R2-9 AC 联动）**:
- R2-9 W2 smoke 应 capture "trader 反馈 ↔ LLM reasoning 引用率" 相关性
- 验证方式: trader 反馈某字段格式难读 → 检查同字段在 reasoning 引用率是否同期偏低
- 若 R2-9 数据显示反馈与 reasoning 引用率无相关性（trader 主观偏好不映射 LLM 行为），D14 退化为"trader 偏好驱动 tool 端改动"的弱原则，需独立议题决议是否升级为 hard rule

**Spec 内应用**:
- Tool 重构（§4.2 enum）阶段决定数值格式 / inline pattern / section 划分时同时考虑 LLM + trader readability
- Display 端 spec（§4.3）显式禁止任何 visual reformat（仅允许裁剪 + envelope）
- R2-9 W2 smoke AC 顺带 capture "trader 阅读体验问题" → tool format follow-up candidate（与 N4 信源治理 / N6 HTF hardening 同档触发）

## 5. 实施策略

### 5.1 数据流

```
LLM call → tool_returns 携带 content_str (sectioned)
            ↓
run_agent_cycle → format_cycle_output(ctx) → _render_action
            ↓
  per tool_call:
    if executed_tool: R2-8a single-line + <22 padding
    else (perception): _render_perception_tool
                          ↓
                       _parse_sections(content)
                          ↓
                       per section: _clip_body(section.body)
                          ↓
                       indent + escape + join
```

### 5.2 改动 Surface 详细定位

| 文件 | 操作 | 行数估 |
|---|---|---|
| `src/agent/tools_perception.py` | 20 工具按 §4.2 enum 重构（每工具 ~10-20 行 refactor + helper 抽取）| ~300-400 |
| `src/cli/display.py` (新增) | `_parse_sections` / `_clip_body` / `_render_perception_tool` helpers | ~80 |
| `src/cli/display.py` (修改) | `_render_action` dispatch 重构 (§4.4) — perception 不再调用 parser | ~30 |
| `src/cli/display.py` (修改) | `_render_reasoning` `max_chars` 800→2000 | 1 |
| `src/cli/display.py` (重命名 + 注释) | `_PERCEPTION_PARSERS` → `_SYSTEM_LOG_PERCEPTION_PARSERS`（8 parser functions 保留，仅供 `resolve_tool_display()` / `cli/app.py` system log 摘要使用，不被 `_render_perception_tool` 调用 — review P2-1 决议避免无谓削弱已知 system log consumer）| ~+10 |
| `tests/test_display_cycle.py` (verified path) | 现有 perception parser tests 保留（system log path 仍消费）+ 新 helpers 单测 + edge cases + 集成 | ~250-300 |
| Tool snapshot 落地（**plan 阶段决议路径** — `tests/agent/` 当前不存在；候选: 新建 `tests/agent/tool_returns/` 或 pytest-snapshot fixture 落 `tests/test_display_cycle.py` 内）| 20 工具 snapshot 重生成（机械化）| ~150-250 |
| **逻辑代码合计**（tools + display net + tests）| | **~670-820**（review P2-1 后保留 parser 不删，display.py 净 +121 而非 -39）|
| **snapshot 合计** | | **~150-250** |
| **总合计** | | **~820-1070** |

### 5.3 实施顺序（spec → plan 阶段细化）

Plan 阶段建议拆 ~9 task TDD：
1. **T0 (前置 verification)**: T-token A/B fixture 实测 — 固定 `get_market_data` fixture，spec-fixed tokenizer 估算旧 R2-8a 输出 vs 新 sectioned 输出 token / cycle，结果 record 在 plan note (§8.10 三档评判)；通过 ≤ 15% pass / 15-20% 灰区压缩尝试 / > 20% stop。**前置 task**：未通过不进入工具批量重构。
2. T1: `_parse_sections` + `_clip_body` + `_render_perception_tool` helpers + 单测（独立基础）
3. T2: `_render_action` dispatch 重构 + 三层集合定义 (`_PERCEPTION_TOOL_NAMES` / `_SECTIONED_PERCEPTION_TOOL_NAMES` / `_EXECUTION_TOOL_NAMES`) + 集成测试 + drift guard T-DG-2
4. T3: `_PERCEPTION_PARSERS` → `_SYSTEM_LOG_PERCEPTION_PARSERS` rename (review P2-1 — 仅注释 + namespace 收窄，不改 logic)
5. T4: `_render_reasoning` 800→2000 + thinking 测试 case
6. T5-T7: 20 perception 工具按 §4.2 enum 重构（按调用频次 batch 推进；每工具 enum 时同时 read source 标注 existing L2 paths — review P1.1 区分原则，**不**重构 L1 hard raise），每 batch landed 跑测 + T-DG-1 增量验证
7. T8: 测试套件 final + drift guard 完整 (T-DG-1 + T-DG-2) + AC 完整通过

## 6. 边界 / 错误处理

完整 11 个 edge cases 测试覆盖（review 校准 — 加 T-EC-10 L3 per-source + T-EC-11 未注册 tool drift）：

| ID | 情况 | 处置 |
|---|---|---|
| **T-EC-1** | Tool 输出无 `=== Section ===` header（legacy / 兼容性 / parse 失败）| `_parse_sections` 返回 `[Section(header=None, body=lines)]` → multi-line render 仍 work（含 get_memories backend-dependent fallback path）|
| **T-EC-2** | L1 失败档 — `is_tool_error=True`（outcome != success 或 execution prefix mismatch）| R2-8a 现有单行 ✗ 渲染（不进 multi-line） |
| **T-EC-3** | L2 失败档 — tool 内捕获异常 + success outcome 返回 `=== Error ===` section | 走 perception multi-line render；section 显示错误描述 |
| **T-EC-4** | Section body 仅 1 行 | `_clip_body` keep all（< 10）→ 单行 body 直接输出 |
| **T-EC-5** | Section body 0 行（空 section）| 仍渲染 header，body 为空 → 视觉是单 header 行 |
| **T-EC-6** | Section header 含 markup 字面值（如 `=== [red]Critical[/] ===`）| `escape()` 处理；display 渲染为字面 `[red]Critical[/]` 不被解释为 markup |
| **T-EC-7** | Section body 含 markup 字面值（新闻 headline 含 `[bold]`）| `escape()` 处理；display 渲染为字面 `[bold]` |
| **T-EC-8** | 极长单行 (URL ≥ terminal width) | Rich 内置 wrapping 处理（D7 R2-8a 决议 file `width=120` / terminal 动态宽度）|
| **T-EC-9** | 无 `tool_call_id` 关联（pydantic-ai 错配）| R2-8a 现有 `[no return captured]` 行（不进 multi-line） |
| **T-EC-10** | L3 失败档 — per-source / per-field degradation（如 derivatives Funding/OI 部分失败）| section 内字段 fallback `(unavailable)`，section 完整 render |
| **T-EC-11** | 未注册 tool name（不在 `_PERCEPTION_TOOL_NAMES` ∪ `_EXECUTION_TOOL_NAMES` ∪ `{save_memory}`）| dispatch 兜底 R2-8a single-line + warning log（drift guard T-DG-2 应在测试层 catch）|

## 7. 测试矩阵

| 类别 | ID | 关键 case | 数量 |
|---|---|---|---|
| **Helper 单测** | T-PARSE-1~3 | _parse_sections（多 sections / 无 header / empty）| 3 |
|  | T-CLIP-1~3 | _clip_body（< 10 / ≥ 10 / == 10 边界）| 3 |
|  | T-RPT-1~4 | _render_perception_tool（单/多 section / 密集 / empty）| 4 |
| **Edge cases** | T-EC-1~11 | §6 11 个 case（含 L3 per-source + 未注册 tool drift）| 11 |
| **集成** | T-INT-1 | 完整 cycle render（perception + execution mixed）| 1 |
|  | T-INT-2 | 失败 tool ✗ icon 维持（regression guard）| 1 |
|  | T-INT-3 | Thinking 升级（1500 keep / 2500 truncate）| 2 |
| **Byte-equal 验证** | T-BE-1 | Section model 比较：`parsed_display == [Section(escape(h), [escape(l) for l in clip(body)])]` (post-parse + post-clip + post-escape) — raw `content.splitlines()` 不作基准（§4.6 P1.2 校准）| 1 |
| **Drift guard** | T-DG-1 | **19 工具**（`_SECTIONED_PERCEPTION_TOOL_NAMES`，含 get_memories 例外）按 sectioning convention 输出验证 — **happy path + 各工具 enum 标注的 existing L2 paths**（不一刀切要求 L2 — review P1.1 校准）+ 参数顺序符合 §4.1.1 convention；plan 阶段按 §4.2 enum 表逐工具填 existing L2 list | 1 |
|  | T-DG-2 | 三层集合 + save_memory branch 互斥 + 完整覆盖：所有 32 registered tools（20 perception + 11 execution + save_memory）必须属其中一类，无重叠无遗漏 | 1 |
| **Tool snapshot** | (per-tool) | 19 sectioned + 1 get_memories backend-dependent + execution single-line snapshots（含 conditional / edge）| ~50-60 |
| **新增小计** | | | **~125** (plan-stage round-2 校准: T-DG-1 拆 a/b/c/d 4 子 lint，parametrized cases 从 19 升至 59) |

预期: 当前 **1048 测试**（verified `pytest --collect-only`, 2026-05-03）+ 新增 ~125 = **~1173 tests pass**。

## 8. Out-of-scope

### 8.1 R2-8b — 前 cycle reasoning 注入（N10 MVP）

R2-8c landed 后启动。议题独立——是 reflection tools MVP，与 cycle log 形态正交（仅 prompt 端注入；session log 渲染层无关）。详见 `memory project_n10_recent_decisions_context_injection`。

### 8.2 C6 MA disambiguate (HTF vs market_data pattern 重叠)

Smoke B2 数据中 `MA50` / `MA20` pattern 同时匹给 `get_market_data` 和 `get_higher_timeframe_view`，导致 HTF 真实价值被低估（数据显示 29% 引用率，但可能是 MA 字段被算到 market_data 那边）。

R2-8c 不在 spec 内 disambiguate，理由：
- 这是 smoke 工具的 pattern 改进，不是 R2-8c spec 的责任
- D3 universal rule 对所有 perception 工具同等处理，HTF 是否进 R2-8c scope 不依赖 disambiguate（HTF 字段排序优先级在 §4.2.2 enum 仍按 disambiguate-pending 数据决议，universal rule 兜底 work）

**Follow-up**: R2-9 W2 smoke 后跑 disambiguated patterns，若 HTF 真实引用率 ≥ 60% 则触发 follow-up（HTF section 字段排序精炼 PR），否则维持 R2-8c 现状。

### 8.3 C7 fill `is_full_close` Header 渲染 `[FULL CLOSE]` / `[PARTIAL]`

R2-8a §8.9 原指向 R2-8c，重新归位——这是 cycle Header 的 `Trigger` 行渲染问题，与 R2-8c tool 行展示议题正交。

**Follow-up**: 归 `project_w2_ops_backlog`（与 §8.4 SCHEDULED subtype 同档，按 W2 实证驱动）。

### 8.4 C8 SCHEDULED trigger subtype capture 增强

R2-8a §8.10 原 candidate，归宿明确——W2 后期或 N# candidate，与 `project_w2_ops_backlog` S 系列同档。

**论据不进 R2-8c**:
- 跨 4 层改动（scheduler / app / cycle_capture / display），不是 tool 行展示议题
- 动 R2-7 schema = 单独的 schema migration 工作量
- "+X min from prev" Header 信息已部分缓解

### 8.5 D12 工具行视觉强化（color / bold / separator）

工具行 `⚙ tool_name` 在 multi-line section 中可能视觉权重不够，D12 brainstorm 中考虑过 4 个 mockup（color / 视觉重组 / separator）但**用户决议 defer**——理由：
- byte-equal 约束 (D13) 否决了视觉重组路径
- color/bold 仅 terminal 受益（D7 R2-8a 决议双 sink 共用 markup，file 端 stripped）
- separator 与 cycle Header `═══` 系统冲突

**Follow-up**: W2 实测 trader 反馈"被淹没"再独立议题。

### 8.6 Execution 工具重构

D8 决议: scope = perception only。理由:
- Execution 工具 <130 chars 已紧凑（B4 数据），单行 ack 是 natural format
- **Execution 不走 universal clipping**（review 校准 — §4.4 dispatch 明确把 execution 排除在 `_render_perception_tool` 外）；因输出短，维持 R2-8a single-line + `<22` padding
- 一致性收益不 outweigh 重构 ROI

**Follow-up**: 后续如 execution 输出复杂化再独立议题。

### 8.7 Execution `<22` 列对齐 dynamic

`{tool_name:<22}` padding 在长 execution 工具名 spillover：`cancel_price_level_alert` (24) spill 2 chars。R2-8c 不修，归 `project_w2_ops_backlog`（影响极小）。

### 8.8 get_memories backend-dependent format

`get_memories` delegates to `deps.memory.format_for_prompt()`，输出格式由 memory backend 决定。

**三层集合定位**（review 校准 — 区分 dispatch 路径 vs lint 范围）:
- **属 `_PERCEPTION_TOOL_NAMES`** (20)：display 走 perception multi-line dispatch path（§4.4）
- **不属 `_SECTIONED_PERCEPTION_TOOL_NAMES`** (19)：T-DG-1 sectioning lint **跳过**此工具
- **Tool 层**: 不强制 sectioning convention（保留 backend 灵活性）
- **Display 层**: `_parse_sections` fallback path 处理（无 sections → `[Section(header=None, body=lines)]`）

**Follow-up**: memory backend sectioning 升级与 reflection tools wishlist 联动（独立议题，详见 `memory project_agent_reflection_tools_candidate`）。

### 8.9 长 session (24-48h) 验证

R2-8c smoke 仅 14 cycles。N=10 阈值 / 2000 thinking 截断 / sectioning convention 在长 session 下的表现需验证。

**Follow-up**: R2-9 W2 startup smoke 顺带 capture，作 R2-8c follow-up（minor PR）数据基线。

### 8.10 Token 经济回归监控

20 perception 工具 sectioning convention 增加 token 量约 5-15% / call。observation P0-1 显示 W1 73% budget 已烧光，token 经济敏感。

**Cache 覆盖范围精确化** (review feedback 校准):
- ✅ Cache **覆盖**: system prompt + tool schema definitions（不变 prefix）
- ❌ Cache **不**覆盖: 每 cycle 的 dynamic tool returns（含价格、indicators、headlines 等）—— sectioning 5-15% 增量是 dynamic content 增量，**全付不被 cache 抵消**
- W1 sim #6 cache hit 90.8% 是 prefix cache 命中率，与 dynamic content 增量 orthogonal

**Mitigation — Pre-impl A/B verification task** (review P2.2 校准 — plan verification artifact 而非 pytest，避免 CI 依赖外部 tokenizer 行为):

Plan 阶段在工具批量重构**前置** T-token verification task — 固定 `get_market_data` fixture（happy path 输出），用 spec-fixed tokenizer / 估算器对比旧 R2-8a 输出 vs 新 sectioned 输出的 token / cycle，结果写入 plan/implementation note artifact (markdown table)。

**三档评判**:
- **≤ 15%**: ✅ pass — 直接进入工具批量重构
- **15-20%**: ⚠️ 灰区 — 允许继续，但 plan note 必须 record risk + 尝试一次明显压缩（精简 section header verbosity / 合并相邻 sections / 字段缩写）后重测；二次仍 15-20% 接受继续
- **> 20%**: 🛑 stop — 触发 spec 回 brainstorm（精简 sectioning 结构 / 合并 sections / 减少 description verbosity）

**AC 联动**: AC-token (§9) 要求 T-token verification artifact 存在 + 三档评判结果 record 在 plan/implementation note。

**R2-9 W2 长 session 监控指标** (与 T-token pre-impl 区分 — pre-impl 是单工具 fixture 估算，R2-9 是真实 cycle 全工具):
- Avg input token / cycle 升幅 ≤ 15%（baseline = R2-8a landed 后短 smoke）
- Daily budget 使用率不超过 R2-8a baseline + 10%

**Follow-up**: 若 R2-9 实测 token 经济恶化（avg input token / cycle 升幅 > 20% 或 daily budget 使用率 > baseline + 15%），归 W2 ops backlog 触发 fine-tune。

## 9. Acceptance Criteria

R2-8c impl 完成的判定条件：

### 9.1 行为正确性

- [ ] AC1: **19 perception 工具**（`_SECTIONED_PERCEPTION_TOOL_NAMES`）按 `=== Section ===` convention 输出（lint 验证 **happy path + 各工具 enum 标注的 existing L2 paths**，T-DG-1）；**不一刀切要求每工具都有 L2**（review P1.1 — L1 hard raise 不强制重构为 L2，避免破坏 outcome != success / error_type 语义）；L3 (per-source/field) 在 §4.2.* enum 内具体列；**get_memories 作为 explicit exception**（§4.2.13 / §8.8 — backend-dependent format，display 走 fallback path）
- [ ] AC2: **Byte-equal 验证** — Display 与 tool content 在 **Section model 层** 比较（post-parse + post-clip + post-escape）— `parsed_display == [Section(escape(h), [escape(l) for l in clip(body)])]` (§4.6 P1.2 校准的 Section model 口径，T-BE-1)；raw `content.splitlines()` 不作为比较基准
- [ ] AC3: Universal clipping rule — body < 10 keep all / ≥ 10 head+`[N rows omitted]`+tail (T-CLIP-1~3)
- [ ] AC4: Execution 工具 11 个维持 R2-8a single-line + `<22` padding（不进 multi-line render，T-INT-1）
- [ ] AC5: Failed tool（`is_tool_error=True`）维持 R2-8a single-line + ✗ icon (T-INT-2)
- [ ] AC6: Thinking 截断 max_chars 800 → 2000 (T-INT-3)
- [ ] AC7: `_render_perception_tool` 输出格式符合 §4.3.3 contract
- [ ] AC8: `_parse_sections` 边界 case T-PARSE-1~3 全部通过
- [ ] AC9: `save_memory` 仍走 R2-8a single-line + ✎ icon 路径
- [ ] AC10: `get_memories` 走 fallback path（无 sections → 单无名 section）
- [ ] **AC-token**: Pre-impl T-token A/B verification artifact 存在（fixture-based，spec-fixed tokenizer 估算）+ 三档评判结果 record 在 plan/implementation note (§8.10) — ≤ 15% pass / 15-20% 灰区 + risk note + 一次压缩尝试 / > 20% stop 回 brainstorm

### 9.2 Edge cases

- [ ] AC11: T-EC-1 ~ T-EC-11 全部通过（§6 11 个 case，含 T-EC-10 L3 per-source/field degradation + T-EC-11 未注册 tool drift）

### 9.3 Drift guards

- [ ] AC20: **19 工具**（`_SECTIONED_PERCEPTION_TOOL_NAMES`）sectioning convention 一致性测试 (T-DG-1) + 三层集合互斥+覆盖测试 (T-DG-2)
- [ ] AC21: Test 总数 **1048 baseline**（verified 2026-05-03）+ 新增 ~125（含 T-EC-10/11 + T-DG-2 + T-DG-1a/b/c/d 4 子 lint × parametrized 19+19+19+2 = 59 cases）= **~1173 全部 pass** (plan-stage round-2 校准)
- [ ] AC22: R2-8a 现有 cycle log 5 段架构测试不破（regression）
- [ ] AC23: Tool 输出 snapshot 测试全部更新且通过（snapshot 落地路径 plan 阶段决议，§5.2）

### 9.4 文档

- [ ] AC24: spec §4.2 含 20 工具完整 sections + 字段 enum（spec 阶段已落本文档）
- [ ] AC25: 候选 memory 更新（`project_r2_8c_tool_output_optimization` 标 ✅ landed）
- [ ] AC26: `project_w2_prep_progress` 更新 R2-8c → R2-8b 序

## 10. 关联议题 / candidate memory

### 10.1 前置（R2-8c 启动依赖）

| 议题 | 关系 | 状态 |
|---|---|---|
| **R2-8a** (cycle log 5 段架构) | `▾ Action` 段必须存在才能改其内 tool 行渲染 | ✅ landed (PR #36, 2026-05-03) |

### 10.2 后续（R2-8c landed 后启动）

| 议题 | 关系 |
|---|---|
| **R2-8b** (N10 reasoning 注入) | R2-8c landed 后启动；reflection tools MVP，与 cycle log 形态正交 |
| **R2-9** (W2 startup smoke) | R2-8b landed 后启动；终验 + 数据回流多个 R2-8c follow-up（详见 §10.5）|

### 10.3 R2-8c 顺带决议

| 议题 | 决议位置 |
|---|---|
| **N12c** (thinking 截断 data-driven) | D10 已纳入本 spec — `_render_reasoning` `max_chars` 800→2000 |

### 10.4 独立 candidate（不在 R2-8c）

| 议题 | 理由 |
|---|---|
| **N12a** (agent.iter() forensic partial messages) | 独立 — forensic 路径议题，与 tool 输出 sectioning 正交 |
| **N12b** (R2-7 schema 升级保留时序) | 独立 — DB 持久化层议题 |
| **N4 信源治理** / **N6 HTF hardening** | W2 数据驱动 candidate，触发条件与 R2-8c 不耦合 |

### 10.5 R2-9 监控触发的 R2-8c follow-up

| 议题 | 触发条件 | follow-up 类型 |
|---|---|---|
| **MA disambiguate** (§8.2 / §2.2 caveat) | R2-9 重跑 disambiguated patterns，HTF 真实引用率 ≥ 60% | HTF section 字段精炼 PR |
| **N=10 / head=2/tail=2 阈值 fine-tune** (§4.3.2) | R2-9 长 session 触发分布数据 | 阈值调整 minor PR |
| **Token 经济回归** (§8.10) | avg input token / cycle 升幅 > 20% | 精简 sectioning 结构 |
| **D14 Display-as-Canary 假设验证** (§4.7) | R2-9 trader 反馈 ↔ reasoning 引用率相关性 | 升级原则为 hard rule 或独立议题 |

### 10.6 关联 observation backlog

| 议题 | 关系 |
|---|---|
| **observation P0-1** (token 经济 W1 73% budget) | §8.10 token 回归监控关联 |

## 11. 风险与 Mitigation

| 风险 | 影响 | Mitigation |
|---|---|---|
| 20 工具重构影响 agent 行为（W1 reasoning 引用 inline 字段格式做 cross-tool 关联）| Medium — 部分 reasoning 路径可能失效 | **Plan 阶段每工具 enum 列出 "旧字段名 → 新字段名" mapping table**（spec §4.2 已 list sections + 字段，plan 阶段 fill in 字段命名差异）；**T-DG-1 lint 检查无关键字段消失**（白名单校验 RSI / MACD / MA20 / Bollinger / Funding / OI / L/S / FGI 等核心字段在重构后仍存在）；R2-9 smoke 监控 reasoning 质量；如出现 regression 走 W2 ops backlog patch |
| Token 经济恶化（observation P0-1 W1 73% budget）| **Medium-High** — sectioning 5-15% 是 dynamic content 增量，**不被 prompt cache 抵消**（§8.10 review 校准）| Plan 阶段必含 1 工具 A/B 实测 task 校准 5-15% 估算；R2-9 smoke 监控 avg input token / cycle 升幅 ≤ 15%；如超 20% 触发回退 brainstorm（§8.10）|
| Snapshot 测试维护负担 | Low — 主要机械化 | 写 snapshot generator helper，避免手写；snapshot diff reviewer 重点检查字段语义不变 |
| `_render_perception_tool` 内 escape 漏（multi-line 多次 escape）| Low — markup attack | T-EC-6/7 测试 markup 字面值（含新闻 headline 含 `[bold]`）case |
| 单 PR 改动量大（~820-1070 行）| Medium-High — review 颗粒度差（review P2-1 后保留 parser 让改动量上调）| Plan 阶段拆 8 task TDD（§5.3），每 task 独立 commit + 测试通过后再下一步；reviewer 按 task 渐进 review；如 plan 阶段评估 PR 仍过大，可拆 P1 (helpers + dispatch + parser rename + 已 explicit-sectioned 工具最小改) + P2 (剩余完整 sectioning) 2 PR — 不违反 D9（A 合并 spec），仅 implementation strategy |
| `get_memories` 走 fallback 与其他工具不一致 | Low — backend-dependent 特殊 case | 三层集合显式定位（§4.4 / §4.2.13 / §8.8）：属 `_PERCEPTION_TOOL_NAMES` (20) 走 multi-line dispatch；不属 `_SECTIONED_PERCEPTION_TOOL_NAMES` (19) — T-DG-1 lint 显式 skip；与 reflection tools wishlist 联动议题 |

## 12. 与 R2-8a spec 的差异 / 互补关系

| 维度 | R2-8a | R2-8c |
|---|---|---|
| **作用层** | cycle log 5 段架构（Header / Reasoning / Action / Decision / Footer）| `▾ Action` 段内 tool 行展示完整度 |
| **数据消费** | in-memory `result.new_messages()` 时序遍历 | tool returns content_str 解析 |
| **改动文件主体** | `src/cli/display.py` + `src/cli/app.py` (CycleRenderContext) + `src/cli/session_state.py` (SessionStats) | `src/agent/tools_perception.py` + `src/cli/display.py` |
| **forensic / retry-exhausted** | 完整 contract（D8/D13/D16）| 不动（R2-8a 已 cover，R2-8c tool 行不参与 forensic 路径）|
| **Schema** | 不动 R2-7 已铺好 | 不动 |
| **Test 增量** | +58 (988 baseline) | +125 (1048 baseline → ~1173 total, plan-stage round-2 校准 — T-DG-1 4 子 lint) |
| **scope 关联** | "完整决策报告"框架 | "完整决策报告"内容完整度补全 |

R2-8c 是 R2-8a 的内容完整度补全，不是替代或并行——R2-8a 已 landed 是 R2-8c 的必要前置（`▾ Action` 段必须存在才能改其内 tool 行渲染）。

---

## 附录 A: Brainstorm 决议追溯

| # | 决议 | brainstorm 段 |
|---|---|---|
| D1 | Sectioning convention | Q1 整体形态 + Q3 重构主驱动 |
| D2 | LLM priority 排序 | Q3 用户决议（完整 > 友好 > display）|
| D3 | Universal section-aware rule | Q4 sequencing brainstorm 中浮现 |
| D4 | 裁剪规则三档 | Q5/Q6 quadrant + Case 1/2 决议 |
| D5 | Case 1 (短/非关键) keep | Q6 + 推荐 c |
| D6 | Case 2 (密集/关键) head/tail | Q6 + 用户改 b |
| D7 | N=10 阈值 | 用户提议（vs 我推 N=8）|
| D8 | Scope = perception only (20 工具) | Q7 用户改 b → 后改 c → 最终回 b → scope 重估 20 工具 |
| D9 | Sequencing = A 合并 spec | Q8 |
| D10 | Thinking 800 → 2000 | Q (D10 substantive) |
| D11 | 列对齐自动消解 + execution 维持 `<22` | Q (D11 几乎自动决议) |
| D12 | 工具行视觉强化 defer | 用户决议 "算了，这个小细节先不做了" |
| D13 | Byte-equal 约束 | 用户提议 + 边界精确化 |
| D14 | Display-as-Canary 原则 | 用户提议 |

## 附录 B: Smoke 数据验证 baseline（R2-9 follow-up reference）

R2-9 W2 startup smoke 应顺带 capture 以下数据点作 R2-8c fine-tune 基线：

| 数据点 | 当前 R2-8c 假设 | R2-9 验证 |
|---|---|---|
| Sectioning convention dynamic token 增量 | 5-15% / cycle（dynamic content 增量，**不被 prompt cache 抵消**）| 1 工具 A/B 实测前后对比（plan 阶段 task）+ R2-9 全工具 avg input token / cycle |
| Prompt cache hit rate baseline | W1 sim #6 90.8%（**与 sectioning 增量 orthogonal** — cache 仅覆盖 prefix system prompt + tool schema，不覆盖 dynamic returns）| R2-9 监控 cache hit 是否维持 ≥ 90%（独立 metric，不作 R2-8c 改动效果判定）|
| N=10 密集阈值 + head=2/tail=2 | 4 边界 case (Pivots above/below + News list) 动态触发 | 实测 long session 触发分布 |
| Thinking 2000 截断 | ~25% 触发率 | 实测真实 trader cycle 段长分布 |
| HTF MA disambiguate | 29% 引用率（pending disambiguate）| 重跑 patterns disambiguated → 真实引用率 |
| Trader 阅读体验（D14 Display-as-Canary）| Tool format LLM 友好 = trader 友好（working hypothesis）| 实测 trader 阅读问题 ↔ reasoning 引用率相关性 |
