# iter-session-log-structured-clip — design spec

## 1. 议题缘起

### 1.1 现象

`src/cli/display.py:_clip_body`（PR #37 R2-8c 引入的 D4 通用 row-clip）对**任何 section body**只要 ≥ 10 行就压成 `head[2] + omitted-marker + tail[2]`。对**结构化异质** body（如 `get_multi_timeframe_snapshot` 4 个 tf 的独立数据段）造成语义性丢失。

实测样本（`logs/session_715d3e81-...log:111-116`）：

```
=== Multi-TF Snapshot (BTC/USDT:USDT) ===
Last (ticker @ 16:26:36 UTC): 77540.00
MA fast-vs-slow per tf: 5m above | 1h below | 4h below | 1d below
[... 11 rows omitted ...]
[1d]  Mom +1.0% (vs MA50) | MA50: 76747.64 < MA200: 80520.37 | ATR 2.66% (20p avg 2.72%, 0.98×) | Range pos 39%
      Last 3 closes (closed @ 2026-05-25 00:00 UTC): 75508.70→76709.20→77029.90
```

5m / 1h / 4h 三个 tf 数据全丢，开发者 / 用户无法从 session log 单独验证工具输出的正确性与完整性。

### 1.2 跨工具普适性诊断

跨 3 个最大 session log（W3 sim #10 区段）共 **1558 次 omission 触发**，按工具拆分：

**统计口径**：1558 = `grep -c "rows omitted"` 在 3 个 session log（862 + 559 + 137）总命中行数 = omission marker 出现次数。每次工具调用可能多 section，每 section 单独 clip 生成 1 个 marker，所以这个口径**不等于工具调用次数**，而是 (工具调用 × 触发 clip 的 section 数) 之和。

| 工具 | 触发次数 | 占比 | body 性质 |
|---|---:|---:|---|
| `get_market_data` | 969 | 62% | 多 section；Recent Candles 30 行（无 `[anchor]`）+ Period summary 5-vs-5（无 anchor） |
| `get_multi_timeframe_snapshot` | 320 | 21% | 4 tf × (row1 + row2 + blank)；`[tf]` anchor |
| `get_higher_timeframe_view` | 96 | 6% | 多 tf 指标；`[tf]` anchor |
| `get_price_pivots` | 91 | 6% | `Swing High: ... / Prior Daily H: ...`（无 `[anchor]`） |
| `get_market_news` | 68 | 4% | headlines 列表；`[date]` anchor |
| `get_trade_journal` | 14 | 1% | trade history；`[ts]` anchor |

### 1.3 受新 heuristic 改善的范围（基于工具源码 + 默认参数估算）

**方法学声明**：单纯从 session log 反推 anchor 分布**不可靠** —— 当前 D4 clip 已把 4-anchor body（如 mts）压成 head 2 + tail 2，只剩 1 anchor 可见，post-clip log 失去 pre-clip 信号。"受改善覆盖率"应基于工具源码 + 典型 sim 调用 estimate，不能从 omission log 直接加总。

实证可得的两个分类信号：

**关键判定条件**：anchor row = body line 行首**立即**是 `[`（无 leading whitespace）。`get_open_orders` / `get_position` 等工具的行格式为 `f"  [STOP] ..."`（2 空格缩进）或 `f"... [current X]"`（inline label），均**不**符合判定条件，保持 D4 现状。

**Class A — 行首 `[<word>]` 输出 + 工具语义保证 anchor ≥ 2 的工具（19 工具完整 grep 实证）**：

| 工具 | pre-clip anchor 数（源码） |
|---|---|
| `get_multi_timeframe_snapshot` | 默认 4 tfs（`["5m", "1h", "4h", "1d"]`，可传 1-6 tfs） → 4 anchor |
| `get_higher_timeframe_view` | 默认 2 tfs（`["4h", "1d"]`，per `tools_perception.py:1219`） / max 4 tfs（`["4h", "1d", "1w", "1M"]`） → default 2 anchor / max 4 anchor |
| `get_market_news` | 每条 entry 1 个 `[date]` anchor，N 条 → N anchor（典型 5-20，极端 30+） |
| `get_trade_journal` | 每笔 trade 1 个 `[ts]` anchor，N 笔 → N anchor（典型 5-15） |
| `get_macro_calendar` | 每事件 1 个 `[ts]` anchor，N 事件 → N anchor（典型 1-10） |

**Class B — 无 `[<word>]` anchor 在行首的工具（保持 D4 list-like 现状）**：14 工具，含：

| 工具 | 行首格式 |
|---|---|
| `get_market_data` Recent Candles | `06:00  77530.00  77545.00  ...`（time + numerical） |
| `get_price_pivots` | `Swing High: ... / Prior Daily H: ...`（key: value） |
| `get_open_orders` | `  [STOP] BUY 0.1 @ ...`（行首 2 空格 → 不匹配 anchor regex） |
| `get_position` | `Breakeven: 77540.00 [current 77550, +10 pts]`（inline label，不在行首） |
| 其余 10 工具 | balance / performance / market_data 其他 section / recent_trades / derivatives_data / order_book / macro_context / active_alerts / exchange_announcements / etf_flows / stablecoin_supply |

**结论**：
- 5 个工具（mts / htf / news / journal / macro_calendar）的 `anchor ≥ 2` 调用进 structured-row mode 全展
- 14 个工具保持 D4 现状（设计意图，per §2.5）
- 量化"改善覆盖率"需从 sim 期 ad-hoc instrumented build 才准；spec 内**不写死百分比**

### 1.4 痛点（per CLAUDE.md 顶部 simulated-only 阶段焦点）

- **开发者验证**：W3 sim forensic 阶段反复对账 multi-tf 实际 tf 是否齐全；session log 显示 1d only → 必须 SQL 查 `tool_calls.args`（虽然 args 没存 result，相当于盲）
- **结构理解**：用户 / reviewer 看 session log 看不出 multi-tf 是 4 tf 还是 6 tf、HTF view 是 1h/4h 还是 4h/1d/1w
- **trade-off 张力**：要展示结构 + 数据 完整性，同时省略真正重复的 list（如 30 行 candles）

## 2. 设计决策

### 2.1 Scope

**全部 sectioned 工具普适**（PR #61 by-content dispatch 下，所有 19 个 `_PERCEPTION_TOOL_NAMES`（Tier-1 6 + Mid 2 + Long-tail 11，per `src/cli/display.py:509-532` + `tests/test_display_cycle.py:1501` drift-guard）+ 任何新加入的 sectioned 工具自动受益），不只针对 `get_multi_timeframe_snapshot`。注：受惠的是渲染层走 `_render_tool_body` 的工具集；不等同于会被新 heuristic 改变行为（见 §1.3）。理由：诊断数据显示 6 个工具触发 omission，单工具修法（如升格 sub-section）需在每工具源码都改一遍且 LLM-visible return 受影响，违反原则 8 "信任 agent + 工具优先"（变动工具输出影响已 calibrated 心智）。

### 2.2 决定权归属：渲染层 heuristic

三方案 trade-off：

| 维度 | 方案 1 工具端 marker | **方案 2 渲染层 heuristic** | 方案 3 混合 |
|---|---|---|---|
| 改动位置 | 工具 + 渲染层 | **仅渲染层** | 渲染层 + opt-in marker |
| LLM context 影响 | ⚠️ tool return string 改变 | ✅ **零影响** | ✅ 默认零，opt-in 有 |
| 原则 8 一致性 | ✅ 工具决定 | ⚠️ 渲染层猜（轻微违反） | ✅ 默认 heuristic + 工具可强制 |
| W3 forensic baseline 连续 | ⚠️ 中断（return string 改） | ✅ **连续** | ✅ 连续 |
| 新工具扩展性 | ✅ marker 显式 | ⚠️ heuristic 可能漏识 | ✅ marker 兜底 |
| 工程量 | 中（~60 行 src + 20+ snapshots） | **小（~80 行 display + 1-3 个 snapshot 测试 regen，per §7.2 实测）** | 中 |

**选定方案 2**：

- LLM context 0 影响 → W3 sim baseline 不中断、可继续累积 forensic 数据
- 工程量小 + 单文件改动
- heuristic 与已有行首 `[<word>]` 输出天然匹配的 5 个工具（mts / htf / news / journal / macro_calendar，per §1.3 Class A）；其他工具如 orders 用 `  [STOP]`（含 2 空格缩进）、position 用 inline `[current ...]`（非行首），不在 anchor 触发范围 — 这是设计意图（list-like 保持 D4）

### 2.3 核心识别规则

**R1**：行首匹配 `^\[(?!\.\.\.)[^\]\s]` → "anchor row"。负向 lookahead `(?!\.\.\.)` 排除 `[... N rows omitted ...]` 自身。

**R2**：section body 中 anchor row count ≥ 2 → 进入 "structured-row mode"（< 2 不触发）。

**R3**：continuation row = 任何非 anchor 行（含空行 / 含缩进 / 含 plain text，不限定 leading whitespace）。每 anchor 行起一个 group，归属其后所有 non-anchor 行直至下个 anchor。统一二分逻辑消除空格数歧义。

**R4**：body 起始的 non-anchor lines（在第一个 anchor 出现之前的 prelude）每行**各自单独成 1-row group**（每行作为该 group 的 anchor=该行本身、continuation=[]）。空行不单独成 group，而是归属上一个 group 的 continuation。MTS 实测：prelude 3 行非空（`Last: ... / MA fast-vs-slow: ... / Columns: ...`）+ blank + 4 anchor 行 = 3 prelude 单行 group + 4 anchor group = 7 groups（cap 12 安全）。

选 "每行各自单独" 而非 "合并为 1 个 group" 的理由：（a）与算法线性扫一行起一 group 实现一致；（b）group cap 计数可预测；（c）prelude 行通常 ≤ 5 行，不会因此触发 cap elide。

### 2.4 Group cap N = 12

**关键概念区分**：cap 比对的是 `len(groups)` 而不是 `anchor_count`。`len(groups)` = anchor groups + prelude 单行 groups（per §2.3 R4）。

**维度 1：源码 default & 典型 sim 调用 group 数估算**

| 工具 | prelude 行数（实测源码） | 典型 group 数 | 极端 group 数 |
|---|---:|---:|---:|
| `get_multi_timeframe_snapshot` | 3 (`Last:` / `MA fast-vs-slow:` / `Columns:`) | 3 + 4 = **7** | 3 + 6 = **9** |
| `get_higher_timeframe_view` | 1（`Last: {live_price}`，per `tools_perception.py:1269`；blank 归 prelude group continuation） | 1 + 2 = **3**（default 2 tfs） | 1 + 4 = **5**（max 4 tfs，无 6 tfs option） |
| `get_market_news` | 0-1（section header 之后通常直接 entry） | 5-12 | 20-30+ |
| `get_trade_journal` | 0-1 | 5-10 | 15-20 |
| `get_macro_calendar` | 0-1 | 1-10 | 10-20 |

**维度 2：视觉体积上限**

structured-row mode 下若 N groups × 2 行 / group：
- 7 groups → 14 行（mts/htf 默认场景，舒适）
- 12 groups → 24 行（cap 上限，可读边界）
- 30+ groups → 60+ 行（news/journal 极端，溢出 terminal viewport）

**N=12 选择 + 实际触发评估**：

| 场景 | groups 估算 | cap=12 行为 |
|---|---|---|
| mts default (4 tfs) | 7 | 全展（1.7× 余量） |
| mts max (6 tfs) | 9 | 全展（1.3× 余量） |
| htf default (2 tfs) | 3 | 全展（4× 余量） |
| htf max (4 tfs) | 5 | 全展（2.4× 余量） |
| news 5-10 条 | 5-11 | 全展 |
| **news 12 条**（`test_snapshot_get_market_news_dense_general_news_clipped`） | **12**（section header 后直接 entries，prelude=0，per test code line 2255） | **= cap 严格相等，全展（24 行）**；该测试 snapshot 必然变化（D4 → 全展） |
| news 20-30 条 | 20-30 | **会触发** cap elide |
| journal 15-20 笔 | 15-20 | **会触发** cap elide |
| macro_calendar 10+ 事件 | 10-11 | 全展 / 边界 |

**关键校准**：cap=12 **不是**"mts/htf 3× 余量"（之前误算 anchor count 而非 group count）。真实余量：

- mts 默认 1.7× / mts 极端 1.3×（仍安全）
- htf 默认 4× / htf 极端 2.4×（per 维度 1 表新算值）
- news 12 条 = cap 严格相等（snapshot 测试 prelude=0，per test code line 2255），全展 24 行（D4 head=2/tail=2 → 全展，必改 snapshot）
- news 20+ / journal 15+ / macro_calendar 10+ 极端场景**会触发 cap elide**（保头 3 + omitted + 尾 3，对称），符合视觉防爆设计意图

**与之前错误论述的对比**：

| 前版 spec（已撤回） | 实际 |
|---|---|
| "max=6, N=12 ≈ 2× max" | max=6 是 post-clip 反推误测；cap 比对 group 数不是 anchor 数 |
| "mts/htf 3× 余量" | anchor count 3× 但 group count 仅 1.3-1.7×（含 prelude） |
| "cap 永不触发，是 dead code" | news/journal/macro_calendar 极端场景会触发；cap 是视觉防爆 |

| 选项 | 决议 |
|---|---|
| N=12 | ✅ **选定**（mts 极端 1.3× safety + news 12 条边界 + 极端 ≥ 13 时 graceful elide） |
| N=8 | mts 9 groups 已超，会误触发 elide |
| N=20 | news 20 条 = 40 行全展，超一屏 |
| 不 cap | 体积不可控（news 100 / journal 100 极端） |

### 2.5 List-like fallback：D4 n=10 不动

candles 30 行 / news 50 条 这类**纯同质 list** 在本 iter 内**保持 D4 row-clip (n=10) 行为 bit-for-bit 不变**。理由：

- 与原始需求 "省略重复结构的内容" 一致
- 减少本 iter blast radius（snapshot 影响面）
- 未来若需要可独立 candidate（W4 数据触发条件）

## 3. 架构

### 3.1 改动位置

**单文件**：`src/cli/display.py`

- 改造：`_clip_body` 函数（当前 16 行 / `src/cli/display.py:444-459` → 改造后 ~50 行）
- 新增 3 项：1 个 module-level regex (`_ANCHOR_RE`) + 2 个 helper 函数 (`_is_anchor` / `_group_by_anchor`)
- 不动：`_parse_sections` / `_strip_blanks` / `_render_tool_body` / `_render_action`

### 3.2 改动哲学

| 层 | 改动 |
|---|---|
| 工具输出层（`tools_perception.py` 等） | **0** |
| DB 层（`tool_calls.args/result`） | **0** |
| LLM context（pydantic-ai 注入） | **0** |
| 渲染层（`display.py`） | `_clip_body` 内部 sub-dispatch |

### 3.3 与 PR #61 的关系

- PR #61 已统一 `_render_tool_body` 入口 + by-content dispatch
- 本 iter 在 `_render_tool_body` → `_clip_body` 下游 dispatch（structured-row vs list-like vs short）
- 三档分支互不重叠，invariant：list-like 行为 bit-for-bit 不变

### 3.4 组件依赖图

```
_render_action (#61 dispatch)
    └─> _render_tool_body
            └─> _parse_sections
            └─> _clip_body              ← 本 iter 改造的唯一节点
                    ├─> _group_by_anchor (NEW)
                    └─> _is_anchor (NEW)
```

## 4. 组件 spec

### 4.1 `_ANCHOR_RE` + `_is_anchor`

```python
# src/cli/display.py
_ANCHOR_RE = re.compile(r'^\[(?!\.\.\.)[^\]\s]')

def _is_anchor(line: str) -> bool:
    """Return True iff line starts with [<word>] prefix (not [... omitted ...])."""
    return bool(_ANCHOR_RE.match(line))
```

**职责**：单行 → 是否 anchor 行 (Bool)。

**依赖**：无。

**外部消费者**：仅 `_clip_body` + 单元测试。

### 4.2 `_group_by_anchor`

```python
def _group_by_anchor(
    body: tuple[str, ...] | list[str],
) -> list[tuple[str, list[str]]]:
    """切分 body 为 [(anchor_line_or_standalone, [continuation_lines]), ...]

    Assumes body has had leading/trailing blanks stripped upstream by
    `_strip_blanks` (display.py:433-441). 即 body 首行非空，避免 R4
    "空行归属上一个 group" 在 body 起始空行时无定义。

    规则（per design spec §2.3 R3, R4）：
    - anchor 行（_is_anchor=True）起一个新 group
    - 非 anchor 行（含空行 + 非空 continuation）归属当前 group 的 continuation 列表
    - body 起始的 non-anchor lines（未碰到任何 anchor 之前）每行各自成单行
      group（head = 该行本身、continuation = [] 初始；若紧接空行则空行进入
      该 group 的 continuation，即 continuation = [""]）
    - 返回 tuple[0] 在 prelude 场景下是 prelude line 本身（非真正的 anchor）；
      在 anchor 场景下是 anchor line。命名上视为 "group head line" 更准确，
      tuple[1] 是 continuation lines。
    - cap 计数比对 len(groups)（含 prelude 单行 group + anchor group）

    实测 MTS 例（prelude 3 行 + blank + 4 anchor）：
      groups = [
        ("Last: ...",            []),     # prelude 1
        ("MA fast-vs-slow: ...", []),     # prelude 2
        ("Columns: ...",         [""]),   # prelude 3 + blank 归属其 continuation
        ("[5m] ...",             ["..."]),
        ("[1h] ...",             ["..."]),
        ("[4h] ...",             ["..."]),
        ("[1d] ...",             ["..."]),
      ]
      len(groups) = 7  (cap=12 安全)
    """
```

**职责**：list[str] body → list[(anchor, [conts])] groups。

**依赖**：`_is_anchor`。

**外部消费者**：仅 `_clip_body` + 单元测试。

### 4.3 `_clip_body`（改造）

```python
def _clip_body(
    body: tuple[str, ...] | list[str],
    n: int = 10,
    group_cap: int = 12,
) -> tuple[str, ...]:
    """三档分支 dispatch：

    1. structured-row mode  (anchor_count >= 2)
       → group-level 处理；len(groups) <= group_cap 全展，
         否则 _flatten(head[:3]) + "[... N groups omitted ...]" + _flatten(tail[-3:])

    2. list-like mode       (len(body) >= n, anchor_count < 2)
       → 现有 D4 row-clip 不变：(body[0], body[1],
         "[... N rows omitted ...]", body[-2], body[-1])

    3. short mode           (len(body) < n, anchor_count < 2)
       → 全保留（不变）

    Symmetric head=3 / tail=3 设计理由（structured-row mode cap-exceeded）：
    - 渲染层 heuristic **不预设工具语义优先级**：不同工具的"最新最相关"
      方向不同（news 默认 newest-first → head 是最新；trade_journal 实测
      `tools_perception.py:629` 用 `reversed(actions)` chronological → head 是最旧；
      macro_calendar upcoming chronological → head 是最近）—— 渲染层选 asymmetric
      会在某些工具上反直觉
    - 对称 head=3/tail=3 让两端 edge entries 都被保留，无论工具内部顺序如何
    - 与 D4 list-like 的 head=2/tail=2 同对称风格延续（D4 row-level、本 mode
      group-level，对称风格一致）
    - cap-exceeded 输出体积：head 3 + omitted marker + tail 3 = 7 group head +
      各 group continuation，总计典型 12-15 行（vs 全展 24+ 行）

    Omission marker 两种形态（语义不同，future grep 应分别处理）：
    - list-like:    "[... N rows omitted ...]"   (rows，行数)
    - cap-exceeded: "[... N groups omitted ...]" (groups，组数)
    """
```

**职责**：单一入口，body → clipped body。

**返回**：`tuple[str, ...]`（与现有 signature 一致）。

**Signature 兼容性**：现有调用点 `_render_tool_body:498` 即 `_clip_body(section.body)`，仅传 body 位置参数。新增 `group_cap=12` 是 keyword 默认参数，**调用方 0 改动**。`n=10` 默认保持 PR #37 R2-8c 引入的语义。

## 5. 数据流

### 5.1 端到端 trace（unchanged 部分省略）

```
工具调用 → _render_action [PR #61] → _render_tool_body → _parse_sections
                                                      → for each section:
                                                          _clip_body(section.body)  ← 改造点
                                                          │
                                                          ├─ groups = _group_by_anchor(body)
                                                          ├─ anchor_count = sum(1 for g in groups if _is_anchor(g[0]))
                                                          │
                                                          ├─ if anchor_count >= 2:
                                                          │     ┌── structured-row mode ──┐
                                                          │     │  if len(groups) <= 12:  │ → _flatten(all groups)
                                                          │     │  else:                  │ → _flatten(head[:3]) + omitted-marker + _flatten(tail[-3:])
                                                          │     └────────────────────────┘
                                                          │
                                                          ├─ elif len(body) >= n (=10):
                                                          │     └── list-like mode (D4 unchanged):
                                                          │          (body[0], body[1], "[... N rows omitted ...]", body[-2], body[-1])
                                                          │
                                                          └─ else:
                                                                └── short mode: tuple(body) 原样
```

### 5.2 7 工具真实 trace 验证

| 工具 | section body 形态 | anchor count | 分支 | 输出行数变化 |
|---|---|---:|---|---|
| `get_multi_timeframe_snapshot` | 4 tf × 3 行 | 4 | structured-row → 全展（<12） | 5 → ~16 行 ✅ |
| `get_higher_timeframe_view` | default 2 tfs（4h, 1d） / max 4 tfs（+1w, 1M）各 2-3 行 | 2-4 | structured-row → 全展（≤ cap） | clip → 全展 ✅ |
| `get_market_data` Recent Candles | 30 行 candles（无 `[anchor]`） | 0 | list-like (D4 unchanged) | head 2 + tail 2 ✅ |
| `get_market_data` Period summary | 5 行（无 anchor） | 0 | short mode (<10) | 全保留 ✅ |
| `get_market_news` | N 条 `[date] ...` headlines | N ≥ 2 | structured-row → 全展（≤ 12） / cap elide（≥ 13） | 4 → N 条 ✅ |
| `get_trade_journal` | N 笔 `[ts] action` history（oldest first，per `tools_perception.py:629` reversed） | N ≥ 2 | structured-row → 全展（≤ 12） / cap elide（≥ 13） | 4 → N 笔 ✅ |
| `get_macro_calendar` | N 个 `[ts] event` upcoming（最近 → 最远） | N ≥ 2 | structured-row → 全展（≤ 12） / cap elide（≥ 13） | 5 → N events ✅ |

### 5.3 边界 case 流向

| Case | 行为 |
|---|---|
| body = `[]` / `()` | short mode → `()` 空 tuple |
| body 全空字符串 | `_strip_blanks` 上游剥离；防御性走 short 或 list-like |
| anchor 正则畸形输入（`[` 没闭合） | `_ANCHOR_RE` 严格要求 `[<非括号非空白>]`，不匹配 → `_is_anchor=False` |
| body 含 `[... N rows omitted ...]`（recursive 输入） | 负向 lookahead 排除，不视为 anchor |
| anchor 行中含 Rich markup（如 `[bold red]`） | 正则匹配 → 误判为 anchor；实际工具输出不主动用 Rich，测试守护 |
| body 中仅 1 anchor + 续行 | `anchor_count < 2` → fallback list-like / short |
| 2 anchor + body 总 ≤ 4 行 | 触发 structured 但 < n=10 也能全展，行为正确 |
| anchor 前有 prelude 行 | prelude 行成单行 group，参与 cap 计数 |

## 6. 错误处理 & 降级

### 6.1 Fail-open 哲学

heuristic 设计为 **fail-open**：识别不到 anchor → fallback 到现有 D4 → 最坏退化为 PR #61 前体验。**不会因 heuristic 误判使工具完全不可读**。

### 6.2 Recursive clip 防护

若 `_clip_body` output 又作为 input 传入（理论上不应发生），`[... N rows omitted ...]` 标记**不被识别为 anchor**（lookahead 排除）→ 安全降级。

### 6.3 Rich markup 误判风险

| 风险 | 路径 | 缓解 |
|---|---|---|
| 工具输出含 `[bold]` / `[red]` | tools_perception.py 不主动用 Rich（grep 实证 — 见下方命令） | 无 path |
| LLM 写的 reasoning 含 `[bold]` | reasoning 在 head（`tool(args)` 行），不进 body | 无 path |
| head_args 含 Rich markup | PR #61 已 `escape()` 处理 | 已防护 |
| 多行 Rich markup 同时出现（2+ 行）会触发 structured 误判 | 同上 — 工具不输出 Rich markup，理论 case 无 path | invariant 兜底 |

**结论**：单元测试 `test_rich_markup_in_body_no_misdetect` 只验证单行 → anchor_count=1 → fallback；2+ 行 Rich markup body 理论会触发 structured-mode 误判。

**真实防线**：**Design invariant — `tools_perception.py` 及所有 sectioned 工具不在 row body 中输出 Rich markup**。这是设计约束而非 heuristic 防护。若未来引入 Rich markup 到工具 body（不预期），需配套调整 heuristic 或在 escape 之前对 body 做预清洗。

**Invariant 实证（impl 时复查）**：

```
$ grep -nE "\[(bold|red|green|cyan|magenta|yellow|dim|italic|underline|reverse|strike|blink)\]" src/agent/tools_perception.py
(no output — 0 hits)
```

W4 sim 启动前应 re-run 此 grep 作为 drift guard。可固化为 `tests/test_display_cycle.py` 的一个 module-level invariant test。

### 6.4 Logging / observability

**不加 runtime log**。`_clip_body` 是渲染 hot path，每次工具调用都跑。

**替代**：加 drift guard test 固化典型工具输出 → snapshot 回归保护。

## 7. 测试策略

### 7.1 单元测试

新增测试位置：在 `tests/test_display_cycle.py`（3309 行，含现有 `_clip_body` / `_render_tool_body` 测试 line 1397-1487）的 `# --- T-CLIP:` 块**后追加 module-level def**（保持现有风格，不建 class —— 现有 `test_clip_body_under_threshold_keep_all` 等都是 module-level def）：

| Test case | Input body | Expected mode | Assertion |
|---|---|---|---|
| `test_structured_row_mode_multi_tf_like` | 4 anchor groups × 2 行 | structured-row | output 含全部 4 `[tf]` + 续行 |
| `test_structured_row_mode_anchor_count_threshold` | 2 anchors（边界） | structured-row | output 全展 |
| `test_single_anchor_fallback_to_list_like` | 1 anchor + 8 续行 | list-like / short | output 走 D4 / short |
| `test_no_anchor_30_row_candles_d4_unchanged` | 30 行无 anchor | list-like (D4) | output bit-for-bit = head[2] + omitted + tail[2] |
| `test_short_body_no_clip` | 5 行无 anchor | short | output 全保留 |
| `test_group_cap_exceeded` | 15 anchor groups | structured-row → cap elide | output = head[3] + `[... 9 groups omitted ...]` + tail[3] |
| `test_anchor_with_continuation_rows` | `[tf]` + 续行 + blank + `[tf2]` + 续行 | structured-row | groups 正确切分 |
| `test_news_like_date_anchor_body` | 5 条 `[2026-05-25 14:30] Title` + `  Source: ...` 续行 | structured-row | 全展 5 条，验证 date-anchor 内含空格不被 `[^\]\s]` 误判 |
| `test_htf_like_indented_continuation` | `[4h] (...)` + `  MA50: ...` + `  ATR: ...` 多缩进续行 | structured-row | 多 continuation 行正确归属 |
| `test_omitted_marker_not_recognized_as_anchor` | body 含 `[... 11 rows omitted ...]` | anchor_count 不增 | 行为不变 |
| `test_rich_markup_in_body_no_misdetect` | body 含 `[bold red]Hello[/red]` | 不触发 structured | fallback list-like |
| `test_empty_body` | `[]` / `()` | short | `()` |
| `test_anchor_with_stand_alone_prelude` | non-anchor prelude × 3 + anchor × 4 | structured-row | 每 prelude 行作单行 group + 4 anchor group = 7 groups 参与 cap |

### 7.2 Snapshot 测试

**实测确认会变的**（已 grep `rows omitted` in `tests/test_display_cycle.py`）：

- `test_snapshot_get_market_news_dense_general_news_clipped` (line 2248-2264) — 12 条 `[date]`-anchored news，body 24 行，必然触发新 structured-row mode（4 → 12 条）

**实测确认不变的**（已 grep `tests/test_display_cycle.py:1471-1484`）：

- `test_render_tool_body_dense_section_clipped` — mock content `row {i}`（行首是 `r`，非 `[`），anchor_count=0，仍走 list-like，行为不变

**潜在会变的**（impl 时需逐个 verify）：

- 其他 `get_market_news` / `get_trade_journal` / `get_macro_calendar` 多 entry snapshot
- `get_higher_timeframe_view` snapshot — 当前 happy-path 可能 < 10 行（短 mode 不变），需 grep verify
- `get_multi_timeframe_snapshot` snapshot — 同上

**不应变化的 snapshot**（drift guard 守护）：

- `test_clip_body_*` D4 单元测试（3 处：`_under_threshold_keep_all` / `_at_or_above_threshold_head_tail` / `_exact_threshold_triggers_clipping`）
- `get_market_data` Recent Candles section snapshot
- `get_price_pivots` snapshot（无 anchor）
- `get_open_orders` / `get_position` 当 order 数 < 2 时
- 任何无 `[anchor]` prefix 工具的 snapshot

**Impl 期 pre-flight 校准**：执行 `grep -n "rows omitted" tests/test_display_cycle.py` + 检查每处 context 的 content 是否含 anchor，精确锁定影响数量。当前 5 处 omission 出现，初估 1-3 个测试会变（不是 8-12）。

### 7.3 Drift guard regression test

```python
def test_list_like_section_d4_unchanged_regression():
    """30 行无 anchor body 仍走 D4 row-clip，新 heuristic 不误触。"""
    body = [f"  {i:02d}:00  77{500+i:03d}.00  ..." for i in range(30)]
    result = _clip_body(body)
    assert len(result) == 5
    assert "[... 26 rows omitted ...]" in result[2]


def test_real_multi_tf_body_full_expansion():
    """Real-shaped multi_timeframe_snapshot body 被全展。"""
    body = [
        "[5m]  Mom +0.1% (vs MA20) | ...",
        "      Last 3 closes (closed @ ...): ...",
        "",
        "[1h]  Mom +0.3% (vs MA50) | ...",
        "      Last 3 closes (closed @ ...): ...",
        "",
        "[4h]  ...",
        "      ...",
        "",
        "[1d]  ...",
        "      ...",
    ]
    result = _clip_body(body)
    joined = "\n".join(result)
    assert "[5m]" in joined and "[1h]" in joined and "[4h]" in joined and "[1d]" in joined
    assert "omitted" not in joined
```

### 7.4 Test count 预期

| 类别 | 数量估计 |
|---|---:|
| 新单元测试（heuristic logic） | +13（含 2 新增 news/htf-like case） |
| 新 drift guard test | +2 |
| 现有 snapshot regen（行为变化） | 1-3 个测试（per §7.2 校准） |
| 现有 test 修改（list-like 应不变） | 0 |
| **预期总数** | 1808 → ~1823 passed (+15 净增) |

### 7.5 Local 验证 checklist

```
□ pytest tests/test_display_cycle.py -v  →  100% pass
□ pytest tests/ -k "snapshot"  →  仅预期内 snapshot 变化
□ pytest tests/  →  total pass 1808+15 ≈ 1823
  (注：1808 baseline 来自 memory `project_tradebot_status` PR #60，
   impl 时需 pre-iter 跑一次 actual pytest 校准 baseline)
□ git diff src/ → 仅 display.py 变化（净增 ~80 行）
```

## 8. Scope-out 与触发型 candidate

### 8.1 不在本 iter scope（保持现状）

- 工具输出层格式调整（不动 `tools_perception.py`）
- List-like 阈值 n=10 调整（保持 D4 行为）
- Section header 渲染样式（不动 `=== ... ===` 格式）
- `_render_action` 6-branch dispatch（PR #61 刚定，不动）
- DB tool_calls.result 字段（不存）

### 8.2 触发型 candidate（W4+ 数据驱动）

- **C1**：若 W4 sim 出现 anchor groups > 12 的真实工具调用 → cap 提升至 16 或动态
- **C2**：若 list-like clip 在 W4 forensic 中暴露丢失关键 candle（不太可能）→ n=10 调整
- **C3**：若新工具加入采用新 anchor 格式（非 `[<word>]`）heuristic 漏识 → R1 正则扩展
- **C4**：若 Rich markup 误判在 W4 真实出现 → 加显式 escape pre-check

### 8.3 与已有 iter 的关系

- **PR #61 `iter-session-log-args-visibility`**：上游统一 dispatch，本 iter 是下游 clip 策略细化，两者正交
- **R2-8c (PR #37)**：本 iter 的 D4 base 来源，list-like 分支保留其语义
- **`iter-trade-discipline-quality`**（pre-W4 ④，brainstorm 已成）：scope 独立，本 iter 不阻塞 trade discipline 启动

## 9. 改动量预估

| 文件 | 改动行数（净增）| 性质 |
|---|---:|---|
| `src/cli/display.py` | ~80 | _clip_body 改造 + 2 helper |
| `tests/test_display_cycle.py` 新增 ~150 行（T-CLIP 块后追加 module-level def） | ~150 | 13 单元测试 + 2 drift guard（per §7.1 / §7.3） |
| `tests/test_cli_display_*.py` snapshots | 1-3 个测试 regen（per §7.2） | snapshot |
| **合计** | src ~80 / tests ~150 + 1-3 snapshots | mini-iter 规模 |

per [[feedback-docs-only-direct-merge]] mini-iter 直 merge candidate（< 100 行 src），但本 iter 含 snapshot 影响面，建议走 PR 流程稳妥。
