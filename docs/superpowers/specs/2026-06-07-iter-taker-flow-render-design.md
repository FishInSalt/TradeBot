# iter-taker-flow-render — session log 中 get_taker_flow 渲染优化

**Date**: 2026-06-07
**Status**: Design (approved, pre-plan)
**Scope**: `src/cli/display.py` session-log 渲染层；不动 agent 实见输出，不动通用折叠逻辑

---

## 1. 背景与动机

Tool audit（`.working/tool-audits/2026-06-07-get_taker_flow.md`，session f670abe1 / 384 调用；该 `.working/` 报告 ephemeral 不进 git，关键量化数字内联于 §6）发现：`get_taker_flow` 的输出在 session log 里，**per-bar 逐 bar 表格（每根 bar 的 Buy%/Net/RVol/CVD/Close 时间序列）被整段折叠**，无论 `limit` 多少都只剩 `Now` / `Window` / `1h-scale anchor` 三行摘要。

根因：`get_taker_flow` 把全部内容塞进**单个** `=== Taker Flow ===` section，整个 body（约 12–19 行）走 `display.py:_clip_body` 的 **Branch 2 list-like mode**（`len(body) >= 10` 且无 `[<word>]` anchor 行 → 只保留 `body[0]` / `body[1]` / `[... N rows omitted ...]` / `body[-2]` / `body[-1]`）。对比 `get_market_data` 的**多 section** 结构（Ticker / Indicators / Context 各占独立短 section 得以保留，只 K-line 表格 section 折叠），taker_flow 的核心数据没有别的 section 替代，折叠后逐 bar 序列在 session log 里彻底消失。

实证影响（audit 量化，见 §6）：
- agent 大量**逐 bar 引用**做推理（CVD 命中 72.5% cycle；如 "12:10 bar -$62.2M at 6.6× RVol... 12:40 flipped"），但 human reviewer 在 session log 里看不到 agent 所依据的逐 bar 数据。
- 全量 systematic：372/384 调用每次都折叠掉表格（单 section，每调用 1 个折叠标记）。
- **当前连默认 `limit=6` 也被折叠**（6 bar + 摘要 ≈ 12 行 ≥ 10 触发 Branch 2）——本 iter 一并修复。

## 2. 目标与非目标

### 目标
- session log 里 taker_flow 的整个 section（`Now` / `Window` / per-bar 全表格 / `1h-scale anchor`）**完整保留、不折叠**，让 human reviewer 能复现 agent 逐 bar 引用所依据的数据。

### 设计取舍（不省略，而非"高阈值折叠"）
实测 `limit` ∈ {4,6,8,9,10,12}，**mode=12，从不超过 12**（§6）；硬上限 36 从未接近。任何"超长才折叠"的阈值在实测里都是不触发的死分支，却要为它写易错的 bar 行识别逻辑（区分摘要/表头/脚注、处理 1d 的日期格式 Time 列）。权衡：
- session log 是磁盘文件、**零 LLM token 成本**，无压缩动机；
- 立项目标恰是暴露逐 bar，折叠中段会与目标矛盾（reviewer 实证：12-bar 窗口前 2+后 2 会省略掉动机举例的 12:10 capitulation + 12:15–12:30 bounce 中段）；
- 直接全保留消除最复杂、最易 bug 的实现部分（位置锚定 + 1d 格式特判），符合 YAGNI。

唯一代价：理论超大 `limit`（实测从未出现）session log 单次 section 会长（如 `limit=36` → ~40 行）。真出现刷屏诉求时再加阈值，不预先复杂化。

### 非目标（明确不做）
- **不改 `_render_taker_flow`**（`src/agent/tools_perception.py`）——它是 agent 实见的工具返回值，audit 证明 adoption 极健康（CVD 72.5% / anchor 63.6% / 逐 bar 引用），不动健康路径。本 iter 是**纯 session-log 渲染优化**，agent 看到的内容一字不变。
- **不改通用 `_clip_body`**——它是 5 个工具（GMD/recent_trades/pivots/order_book/taker_flow）共用的 1110+ 折叠点的通用机制，本 iter 不波及其它工具。
- 不动 audit 报告里的其它候选议题（Example 反主流 / default limit / 跨 call CVD）——各自独立。

## 3. 设计

### 3.1 识别（by-content header）

在 `_render_tool_body` 的 section 渲染循环（`display.py:599-609`）中，对每个 section 判定是否豁免折叠：

```python
# session-log 渲染：整段保留的 section（核心小表格，行折叠会让逐 bar 序列失去意义）
_FULL_KEEP_SECTION_PREFIXES = ("Taker Flow",)

def _is_full_keep_section(header: str | None) -> bool:
    return header is not None and header.startswith(_FULL_KEEP_SECTION_PREFIXES)
```

匹配的是 `_parse_sections` 解析出的 `section.header` 文本（taker_flow 的 header 是 `"Taker Flow (BTC/USDT:USDT · 5m bars · @ 12:47:22 UTC)"`），**不是** tool_name frozenset。这契合 `display.py:642-645` 现有哲学（sectioned/plain dispatch 由内容而非 tool 名集合驱动）。

GMD 的 K-line section header 是 `"Recent Closed Candles (…)"`（`tools_perception.py:167`）——结构虽同样是缩进的 `HH:MM` 数据表，但 header 前缀不匹配 → **不受影响**，精准命中 taker_flow，无外溢。

### 3.2 行为

渲染循环改动（`display.py:604`）——识别出的 section 跳过折叠，整段 body 原样保留；其它 section 走现有 `_clip_body` 不变：

```python
clipped = (section.body
           if _is_full_keep_section(section.header)
           else _clip_body(section.body))
```

无需 bar 行识别、无需省略阈值、无需位置锚定——整个 section（摘要 + 表头 + 全部 per-bar 行 + in-progress 脚注 + close note + anchor）按 `_render_taker_flow` 原样进入 session log。`section.body` 已由 `_parse_sections` 做过首尾空行 strip，渲染层照常逐行 `escape` + 缩进。

### 3.3 降级 / 错误路径

全保留对所有路径天然安全（no-op 或正向）：
- `Invalid period '15m' …`（reject）：纯文本无 `=== ===` header → `section.header is None` → 不被识别，走 `_clip_body`（本就 1 行不折叠）。
- `=== Taker Flow … ===\nTaker flow temporarily unavailable …` / `No taker-volume data available`：有 header 被识别 → 全保留（本就 1 行）。
- 正常路径（任意实测 `limit`）：整表全显示。

## 4. 测试矩阵

新增/扩展 `tests/test_taker_flow.py`（或 `tests/test_display_cycle.py` 的渲染侧）：

| 用例 | 断言 |
|---|---|
| limit=12（主流） | 全 12 bar 显示，**无** `[... rows omitted ...]`；12:10 等中段 bar 时间戳可见；`Now` / `Window` / 表头 / `1h-scale anchor` 全在 |
| limit=6（默认，附带受益） | 全 6 bar 显示，无 `omitted`（修复当前默认路径折叠） |
| in-progress 状态 | `[* row 1 = current bar still forming …]` 脚注 + newest bar 的 `*` 星标均保留 |
| 1d period | 整表全显示（日期格式 Time 列原样保留，因不再做 bar 行识别） |
| close note 路径（1d / OHLCV fail） | `Close: n/a …` note 保留 |
| **回归守护** | GMD `Recent Closed Candles`（40 candle）经渲染**仍含** `[... N rows omitted ...]`（豁免不外溢到其它 section） |
| 降级 | `No taker-volume data available` 渲染不报错 |
| `_is_full_keep_section` 单元 | header = None / `"Taker Flow (…)"` / `"Recent Closed Candles (…)"` 三态返回正确 |

## 5. 影响面

- **改动文件**：仅 `src/cli/display.py`（新增 1 个常量 + 1 个 helper + 渲染循环 1 行 dispatch）+ 测试。
- **不影响**：agent 实见输出（`_render_taker_flow` 不动）、通用 `_clip_body`、其它 4 个 list-like 折叠工具、DB/alembic、其它 session-log 段。
- **session log 文件体积**：taker_flow section 每调用从当前 ~5 行（折叠后）增至完整 ~16 行（limit=12：摘要 2 + 标题 1 + 表头 1 + 12 bar + anchor）；session log 是磁盘文件、**不消耗 LLM context**，无 token 成本，纯文件大小。
- **附带受益**：默认 `limit=6` 当前也被 Branch 2 折叠（§1），新设计下整段保留，默认路径一并改善。

## 6. 实证锚

Audit 原始报告 `.working/tool-audits/2026-06-07-get_taker_flow.md` 为 ephemeral（`.working/` 不进 git）；关键量化数字内联于此以供核验：

| 指标 | 值 | 来源 |
|---|---|---|
| 总调用 | 384（status 全 ok） | DB tool_calls，session f670abe1 |
| `limit` 取值分布 | {4:6, 6:40, 8:65, 9:1, 10:22, **12:250**}，**mode=12（65%）**，max=12 | DB args |
| `period` 分布 | 5m=372(96.9%) / 1h=10 / 15m=2(reject) / 4h=0 / 1d=0 | DB args |
| 折叠率 | 372/384 调用每次折叠表格（单 section） | session log grep |
| CVD adoption | 277/382 = 72.5% cycle reasoning 命中（taker_flow 独占词） | DB agent_cycles.reasoning |
| 逐 bar 引用举例 | "12:10 bar -$62.2M at 6.6× RVol… 12:40 flipped"（cycle b7c1dbc3） | session log |

- 工具设计原则：原则 7（表达友好 / 渲染层）。本 iter 不触及 agent 输出，故不涉及原则 1/2 fact-only。
- 渲染层根因数据：list-like 折叠是 5 工具通用（GMD 439 / taker_flow 372 / recent_trades 214 / pivots 57 / order_book 28 个折叠点），通用 `_clip_body` 不动以免波及。
