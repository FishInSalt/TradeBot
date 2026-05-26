# iter-session-log-args-visibility — design spec

## 1. 议题缘起

### 1.1 session log 当前渲染状态

`src/cli/display.py:_render_action` 当前共 6 dispatch branch（编号对应代码注释 spec §4.4）：

| Branch | 触发条件 | 工具数 | 渲染样式 | args 可见性 |
|---|---|---|---|---|
| 1（orphan） | `ret is None`（tool_call_id 不匹配） | — | `⚙ tool_name [no return captured]` | ❌ |
| 2（error） | `is_tool_error()` | — | `✗ tool_name {fallback_summary}` | ❌ |
| 3（save_memory） | `tool_name == 'save_memory'` | 1（retired） | `✎ tool_name {memory summary}` | ⚠️ 部分嵌入 summary |
| 4（execution） | `tool_name in _EXECUTION_TOOL_NAMES` | 14 | `⚙ tool_name {single-line summary}` (`<22` padding) | ⚠️ 部分嵌入 summary（如 `update_price_level_alert above $76,880 → $76,860`） |
| 5（perception） | `tool_name in _PERCEPTION_TOOL_NAMES` | 19 | `⚙ tool_name\n    === Section ===\n    {body}` | ❌ 完全不显示 |
| 6（drift） | 未注册 tool_name fallback | — | `⚙ tool_name {fallback_summary}` + warning log | ❌ |

`tool_calls.args` 数据库字段已存 JSON（per `src/services/tool_call_recorder.py:140-148`：4000 char cap + `reasoning` key stripped；99% 工具 args < 4000 chars），渲染层是唯一未利用通道。本 iter 不动 DB 存储逻辑。

### 1.2 痛点实证

W3 sim #10 forensic（`.working/w2-to-w3/05-w3-forensic.md` + `narrative-evidence/`）反复对账 reasoning ↔ args 的成本：

- agent reasoning 写 "调 5min 窗口看 taker flow"，session log 显示 `⚙ get_recent_trades`，但 `window_seconds` 是默认 300 还是显式传 300 不可辨（需 SQL 查 `tool_calls.args`）
- agent 调 `get_market_data` 选 1m / 5m / 15m / 1h 是 narrative 心智 attribution 的直接信号，但全部归到一行 `⚙ get_market_data`
- `get_higher_timeframe_view` 的 `timeframes` 入参分布是 R2-Next-D MTS 议题的 attribution 数据，session log 不可见

per [[feedback-systematic-debugging-check-system-log]]，长 forensic 流程中 session log 自包含价值高于 DB SQL 反复对账。

### 1.3 数据来源就位

- `ToolCallPart.args_as_dict()` 在渲染时点直接可用（pydantic-ai 1.78 API）
- `tool_calls` 表 `args TEXT` 列已记录 raw JSON（DB persistence 路径不动）
- 无新增 schema / 无 alembic migration

## 2. 设计决策

### 2.1 渲染范式选型

四个候选方案：

| 方案 | 形态 | tradeoff |
|---|---|---|
| A：全部工具 raw JSON | `⚙ get_market_data {"timeframe":"15m","candle_count":30}` | execution 类回退现有 R2-8c semantic summary 优化 |
| B：感知类加 args 头行 | `⚙ get_market_data\n    args: timeframe=15m, candle_count=30\n    === ... ===` | 两行占位；execution 不动；不统一 |
| C：仅显示非默认参数 | 默认值不显示 | 违反 [[tool-design-principles]] 原则 1 "fact-provider 不是 guard"；隐含 agent 应用默认值的判断 |
| **D：统一函数 syntax + 完整 output**（选定） | `⚙ tool_name(k=v, k=v)\n    {return body}` | 34 工具同范式（含 save_memory 走统一 head）；execution 类 R2-8c semantic summary 让位给 args+body |

选 D 的理由：

1. **范式统一** — 34 个工具（19 perception + 14 execution + 1 save_memory）共用单一 head `⚙ tool_name(args)` / `✎ save_memory(args)`；3 个非 happy-path 分支（orphan / error / drift）独立 fallback head 形态
2. **forensic 一站式** — args + return + reasoning 三者全在 session log 同 cycle 段
3. **R2-8c 优化 wording 重排不丢失信息** — 大多数 execution 工具 return 已含 state-delta（如 `update_price_level_alert` 现行 return `Price level alert updated (id=X): above old → new — "reasoning"` 已含 old/new；display.py:246-255 `_summarize_update_price_level_alert` 正则就是匹配这个 shape）；少数缺 state-delta 的工具由 §3.4 捎带补齐
4. **可读性** — 函数 syntax 与 Python 调用 mental model 一致；比 raw JSON 更人友好
5. **强化 [[tool-design-principles]] 原则 1 fact-provider** — 与 §3.6 配合：3 个 outlier 工具（含 reasoning 在 return 中）的 return 也 normalize 成 state-only，return 不再回声入参；reasoning 入参的可见性从 return 文本统一迁移到 head args 内（单点显示，无双重冗余）

方案 C "仅显示非默认参数" 的反对理由：会让 reader 无法区分 "agent 显式传 300" vs "用默认值 300"，造成 forensic attribution 信号缺失（独立于 [[tool-design-principles]] 原则 1，session log 渲染层是 derivative consumer 不直接受原则 1 约束，但精神一致）。

### 2.2 函数 syntax 具体形态

```
⚙ tool_name(arg1=value1, arg2=value2)
    {return body line 1}
    {return body line 2}
    ...
```

空 args 保留 `()` 保证视觉一致：

```
⚙ get_position()
    === Account Balance ===
    ...
```

## 3. 实施方案

### 3.1 渲染规则

`_render_action` 的 6 个分支收敛为统一 dispatch：

```
for each tool_call:
    icon = resolve_icon(tool_name, content, outcome)  # ⚙ / ✗ / ✎
    args_call = format_args_as_call(tool_name, tcp.args_as_dict())
    head = f"  {icon} {args_call}"
    body = render_tool_body(tool_name, content, outcome)
    emit(head + body)
```

**save_memory 统一处理**：branch 3 收敛进统一 head 形态 → `✎ save_memory(content="...", category="...")` + body。Retired tool（iter-w2r3-memory-disable）revert 时也享 args 可见性，与其他工具一致。

**函数 rename**：原 `_render_perception_tool` rename 为 `_render_tool_body`（reflecting by-content dispatch，不再仅 perception）。实测 **40 处** call site / import / docstring 同步更新（`grep -rn _render_perception_tool src/ tests/`）；本 iter 内做。

**by-content body dispatch**：
- `body` 的 sectioned vs plain 判断按 content 是否含 `=== ... ===` 标记自动决定（`_parse_sections` 已有 Section(header=None) → plain fallback 能力），与 `tool_name` 解耦
- 这意味着未来若 execution 工具 return 改成多 section，自动 sectioned 化，无需改 dispatch 代码

**frozenset 作 drift guard（不再驱动渲染分歧）**：
- 工具名出现在 `_PERCEPTION_TOOL_NAMES` / `_EXECUTION_TOOL_NAMES` 之外 → log warning（branch 6 drift signal 行为不变）
- frozenset 不再为 sectioned/plain 渲染分歧 dispatch 服务，单一作 registered tool 的存在性 guard

**drift guard tradeoff 分析**：
- 收益：捕获未注册工具（如 typo / pydantic-ai 自动注册的新工具 / 测试 fixture 与 prod 不同步）
- 成本：每加新工具需同步 frozenset + drift-guard test（~5 行 / 工具）
- 选择：保留 — sim-only phase 工具数趋稳（W3 后 6 个月内只加 1 工具 set_next_wake_at），同步成本边际；drift 信号在 R2-8c iter 已实证捕获过 unregistered tool 漏渲染 bug

### 3.2 Args 格式化规则

新增 `_format_args_as_call(tool_name: str, args: dict | None) -> str`：

| 值类型 | 输出 | 示例 |
|---|---|---|
| `str` | 双引号包裹 | `target_time="20:10"`, `timeframe="15m"` |
| `int` / `float` | raw（保留原精度） | `new_price=76860`, `threshold_pct=0.5` |
| `bool` | `True` / `False` | `force=True` |
| `None` | `None` | `reasoning=None` |
| `list[str]` | `["v1", "v2"]` | `timeframes=["1h", "4h", "1d"]` |
| `list[int/float]` | `[v1, v2]` | `levels=[76800, 76900]` |
| `dict`（罕见） | `{k1: v1, k2: v2}`（短）/ `{...}`（>40 char 截断） | `meta={...}` |
| 空 args | `()` | `get_position()` |

**helper signature 释义**：`tool_name` 入参当前**仅作 fallback display 用**（异常路径渲染 `tool_name(...)`），不做 per-tool exclusion；预留 future per-tool customization 扩展点（如 PII redaction 候选）。

**字段顺序**：保持 `args.items()` 字典顺序。pydantic-ai 1.78 `args_as_dict()` 行为：dict 输入原样返 / JSON string 用 `pydantic_core.from_json` 解（保留 JSON 文本 key 顺序）；都不强制 schema 定义序。**实质来自 LLM 输出顺序**，实测通常匹配 schema 定义序但**非 pydantic-ai 契约保证**。

**reasoning 入参的处理 — uniform retain（与 DB recorder 路径 known divergence）**：

| 路径 | 行为 | rationale |
|---|---|---|
| `src/services/tool_call_recorder.py:138` DB 写入 | `args_dict.pop("reasoning", None)` strip | 与 `trade_actions.reasoning` 重复存储，DB 跨 cycle 聚合不需双份 |
| `src/cli/display.py:_format_args_as_call` 本 iter | **保留 reasoning 在 head args**（不 strip）| session log forensic 价值：每个工具调用即时 context（agent 调这个工具时怎么想） |

**这是 known design divergence，本 iter 显式标注接受**。理由：
- session log 与 DB 用途不同 — 前者 forensic narrative consumption，后者跨 cycle 聚合分析
- reasoning 入参 W3 实证 avg 192-403 chars，head 内 Rich Console width=120 软折行 2-4 行 — 视觉成本可接受
- 与 §3.6 配合：3 个 outlier 工具 return 中的 reasoning 后缀移除，全 14 个 execution 工具 reasoning 单点显示在 head args（无双重渲染）
- W4 sim impl 后 1 周 forensic 时 review head 内 reasoning **命中率**（实际被读 / 提供 cycle Reasoning block 之外的信号比例）；命中率 < 10% → 启 reasoning strip mini-iter

**长 args 处理**：单行 head 长度超过 120 chars 时**不**强制硬换行。

- terminal destination：Rich Console 动态宽度，自适应折行
- session log file destination：`logging_config.py:66` `Console(file=self._file, no_color=True, width=120)` 写入，超 120 chars 时 Rich 按 width 软折行；less / grep 跨折点匹配会失败
- 这是 design choice：避免 head 行硬切引入新解析负担（如 `tool_name(\n    arg1=...\n    arg2=...\n)` 形态会与 sectioned body 解析逻辑冲突），forensic 失真在 reasoning-stripped 假设下 99% args < 80 chars，含 reasoning 时主流 200-400 chars 折 2-4 行可接受

**异常 fallback**：`args_as_dict()` 默认 `raise_if_invalid=False` 不抛；invalid JSON 时返 `{INVALID_JSON_KEY: <raw_str>}`（pydantic-ai messages.py:1666-1668）。`_format_args_as_call` 判 `INVALID_JSON_KEY in args` 时 fallback 到 `tool_name(...)`，并 log warning（drift signal）。display.py 现有 `try/except args_as_dict()` (line 835-837) 是 over-defensive，本 iter 替换为 INVALID_JSON_KEY 判断。

**dict args caveat**：当前 14 execution + 19 perception 共 33 个工具入参类型实测无 dict 字段。`dict` 截断到 `{...}` (>40 chars) 是预防性规则；若未来工具引入 dict 入参（如 batch / multi-target 工具），需重审 truncation 策略（避免 forensic 信号丢失）。

### 3.3 Output body 处理

复用 perception 现有的 sectioned 渲染（`_parse_sections` + `_clip_body(n=10)`）。

**Sectioned vs plain dispatch by-content（与工具类无关）**：
- return content 含 `=== ... ===` 标记 → sectioned 渲染（`_parse_sections` 解析，每 section 一段）
- return content 不含 → plain 渲染（逐行缩进 4 空格输出）
- 这意味着未来若 execution 工具 return 改成多 section（如 `set_stop_loss` 返回 `=== SL Set ===` + `=== Liquidation Updated ===`），自动 sectioned 化，无需改 dispatch 代码

**裁剪策略**：`_clip_body(n=10)` **per-section 应用**（display.py:475 在 sections loop 内每段独立 clip）。

- 单 section 超 10 行 → 头 2 + `[... N rows omitted ...]` + 尾 2
- multi-section 工具（如 `get_market_data` 多个 section）总长度无统一 cap — 与现行 perception 路径行为一致
- plain body（无 `=== ===`）走 Section(header=None) 单段，clip 阈仍 10

**Empty body**：return 为 `""` 或 None → head 行后无 body（不输出空缩进行）。

### 3.4 execution return state-delta 改进（SL / TP 2 工具）

为避免 args 单独不足以承载 forensic 信息（agent 改 SL / TP 时新值在 args / 旧值仅在前 cycle perception 输出中），选 **2 个真缺 state-delta 的 execution 工具** return 加 prev state。

| 工具 | 当前 return | 改进后 return（update path） | 改进后 return（first-set path） | wiring 改动 |
|---|---|---|---|---|
| `set_stop_loss` | `Stop loss set at 76950.00 ({+0.05}% from mark price 76912.50) \| Order: abc123` | `Stop loss set at 77100.00 → 76950.00 ({+0.05}% from mark price 76912.50) \| Order: abc123` | `Stop loss set at 76950.00 ({+0.05}% ...) \| Order: abc123`（无 prev 单值，沿用现 shape）| ~3-5 行：`prev_sl: float \| None = None` + loop 内取 `o.trigger_price` + return 分支判断（has prev → "old → new" / no prev → 单值）|
| `set_take_profit` | `Take profit set at 76200.00 ({-0.05}% from mark price ...) \| Order: abc123` | `Take profit set at 76300.00 → 76200.00 ({-0.05}% ...) \| Order: abc123` | `Take profit set at 76200.00 ({-0.05}% ...) \| Order: abc123` | ~3-5 行 — 与 SL 对称（`tools_execution.py:213-216`）|

**shape 选型理由（方案 2 old → new prefix）**：
- 与 `update_price_level_alert` 同构 `(id=X): direction old → new`，符合范式统一
- 避免方案 1（was 进 distance 括号）改变 distance 括号内容引起 `_summarize_set_stop_loss` regex 退化（spec §3.6 只覆盖 3 outlier，遗漏 SL/TP regex 影响）
- 需同步改 `display.py:188 / 198` `_summarize_set_stop_loss` / `_summarize_set_take_profit` regex：first try 双值 `r"Stop loss set at\s+([\d.]+)\s*→\s*([\d.]+)\s*\(([^)]+)\)"` 捕 group(2) = new price → fallback 现有单值 regex（first-set path）

**prev state capture 细节**：
- `tools_execution.py:181-184` SL 已 fetch_open_orders 并 cancel existing stop；cancel 前 `prev_sl = o.trigger_price`
- 用 `trigger_price` 而非 `price` 的理由：`base.py:54` 注释 `trigger_price # R2-7 §4.7` 是 stop/TP 触发价的契约性字段（algo class 显式契约 per `okx.py:621/635/636` 注释 "R2-7 §4.7: algo class trigger_price = price"）；用 `trigger_price` 字段名语义更准确，避免 future limit-as-stop 等扩展时 `price` 字段歧义（彼时 price 指 limit price 不等 trigger）
- loop 内 ≥ 2 个 stop 订单是 rare case；取 loop 内**最后一个**（与 cancel 行为对称：先 cancel 旧的，最新的覆盖）；assert single 不必（防御性 over-engineering）
- TP 同上

**已合规参考样本**（不改）：
- `update_price_level_alert` 当前 return `Price level alert updated (id=X): above 76880.00 → 76860.00 — "reasoning"` 已含 old → new（`tools_execution.py:478-482`；但其中的 reasoning 后缀由 §3.6 移除）
- `set_price_volatility_alert` replace 分支 return `Price volatility alert replaced: threshold=X%, window=Ymin (was {prev_t}%/{prev_w}min, rolling window reset)`（`tools_execution.py:289-294`）已含 prev state
- `cancel_price_volatility_alert` 当前 return `Price volatility alert cancelled (was {prev_t}%/{prev_w}min)` 已含 prev state

### 3.5 adjust_leverage / set_next_wake / set_next_wake_at 的 prev state 不改的理由

| 工具 | defer 理由 |
|---|---|
| `adjust_leverage` | `success path` 是无 position（`tools_execution.py:244-249` 有 position 直接 reject），`fetch_position(symbol)` 此时返空 → prev_lev 物理上读不到。直接加 BaseExchange 抽象方法会破坏 OKX 类实例化（CLAUDE.md sim-only phase）；用 private `_leverage` 访问 hacky。**移出本 iter scope，置 §6 触发型 candidate**（实盘准备期或 head 内 args 已含 `leverage=Xx` 足够时延后） |
| `set_next_wake` | `TradingDeps.set_next_wake_fn: Callable[[int], None]` (`trader.py:45`) 单向 callable；`app.py:1063` lambda 无 prev getter。实现 prev wake delta 需扩展 deps 字段 / callable 签名 + app.py wiring + 多处 fixture 同步。**移出本 iter scope**（per [[tool-design-principles]] 原则 4 信号补齐优先：下一 cycle args 本身即 new state，cycle 间隔可推算 prev） |
| `set_next_wake_at` | 同上 |

注：本 iter §3.6 仅移除 3 个工具 return 中**重复**的 reasoning 后缀（不引入 prev state 新功能）；prev state 改进作触发型 candidate。

### 3.6 reasoning 入参在 return 中的形态 normalize

14 个 execution 工具中 **3 个 outlier** 把 `reasoning` 入参以人读形式 echo 在 return 末尾：

| 工具 | 当前 return（含 reasoning 后缀） | 改进后 return（state-only） | line |
|---|---|---|---|
| `update_price_level_alert` | `Price level alert updated (id=X): above 76880 → 76860 — "reasoning"` | `Price level alert updated (id=X): above 76880 → 76860` | 478-482 |
| `set_next_wake` | `Next wake set to 18 min. Reason: reasoning` | `Next wake set to 18 min` | 510 |
| `set_next_wake_at` | `Next wake set for 20:10 UTC (in 25 min). Reason: reasoning` | `Next wake set for 20:10 UTC (in 25 min)` | 565-568 |

**移除理由**：

1. **强化 [[tool-design-principles]] 原则 1 fact-provider** — 工具 return 是 fact + state change，不应回声入参（agent 自己传 reasoning 自己已知）
2. **与方案 D head 内 reasoning 单点显示一致** — 避免 head + body 双重渲染（其余 11 个 execution 工具 return 本就不含 reasoning，3 outlier normalize 后 14 工具完全统一）
3. **信息无丢失** — reasoning 仍在三处存在：
   - head args 内（`⚙ update_price_level_alert(alert_id="X", new_price=76860, reasoning="...")`）
   - cycle `▾ Reasoning` block（完整 cycle thinking）
   - `trade_actions.reasoning` DB（持久化 audit trail）
4. **承接 R2-Next-E (PR #51) audit trail 用意** — 原 design 加 `— "reasoning"` 到 return 是为人读 audit；本 iter 由 head 内 args 渲染承担，更符合统一范式

**测试 + docstring 同步影响**：
- `tests/test_alert_age.py:206-218` `test_update_tool_return_string_shape` 用 regex `r'^... — ".+"$'` + assert `'— "trail up after breakout" in result'` 强制断言 reasoning 后缀；§3.6 后硬失败，必须更新（regex 去 `— ".+"$` 末段 + 删 reasoning content assert）
- `tests/test_alert_family.py:200` `assert '— "trail up after breakout"' in result` 是 `update_price_level_alert` test 行为断言；§3.6 后必失败，**必须删此 assert 行**（不只是样本刷新）
- `tests/test_alert_family.py:358 / 396` `sample` (parser 输入 fixture) / `update_success` (`is_tool_error` 输入 fixture) 样本字符串带 `— "4h structural high"` reasoning；语义层不依赖此后缀（parser regex 仅 grep direction → price；is_tool_error 仅 grep success prefix），行为不变，但内嵌样本 stale，**read-only sample refresh** 避免读者误解
- **不在 scope**：`tests/test_alert_family.py:154` `assert '— "4h structural high"' in result` 是 `cancel_price_level_alert` 测试，return 中 `— "{alert["reasoning"]}"` 是被取消 alert 的存储 reasoning（state-delta），不是 arg echo；§3.6 normalize 不覆盖 cancel 工具，**保留不改**
- `tests/test_tools_execution.py` 中针对 3 工具的 return 字面断言 ~5-10 处需更新（删除 `Reason:` / `— "..."` 断言段）
- **`src/agent/tools_descriptions.py:13-45` SET_NEXT_WAKE_DESCRIPTION + SET_NEXT_WAKE_AT_DESCRIPTION 同步**（per [[tool-design-principles]] 原则 1 fact-provider — docstring 是 LLM-visible verbatim 传输，必须与实际 return 对齐）：
  - `set_next_wake(15, "...")` Example output `→ "Next wake set to 15 min. Reason: ..."` → 去 `. Reason: ...` 段
  - `set_next_wake_at("10:37", "...")` Example output `→ "Next wake set for ... (in 14 min). Reason: ..."` → 同
- **`src/cli/display.py:266` `_summarize_set_next_wake_at` docstring `"""Parse 'Next wake set for ... (in N min). Reason: ...'."""` 同步**（parser regex 不依赖 Reason 字面，但 docstring 描述需准确）
- system.log INFO 通道 `_summarize_*` parser regex 不受影响：`_summarize_set_next_wake` 仅 grep `(\d+)\s*min`（display.py:258-262），`_summarize_update_price_level_alert` 仅 grep `(above\|below)\s+([\d.]+)\s*→\s*([\d.]+)`（display.py:246-255），都不依赖 reasoning 字面
- **SL/TP 例外**：`_summarize_set_stop_loss` / `_summarize_set_take_profit` (display.py:188 / 198) 现有 regex `r"Stop loss set at\s+([\d.]+)\s*\(([^)]+)\)"` 在 §3.4 改造后**需同步扩展**先 try 双值 `r"...at\s+([\d.]+)\s*→\s*([\d.]+)\s*\(([^)]+)\)"`（捕 new price），fallback 现有单值 regex（first-set path）— 否则 display 退化丢失 distance 信号

## 4. 文件级改动

### 4.1 src 改动

| 文件 | 改动类型 | 估行数 | 说明 |
|---|---|---|---|
| `src/cli/display.py` | new helper | +50 | `_format_args_as_call(tool_name, args) -> str` |
| `src/cli/display.py` | refactor | +60 / -90 | `_render_action` 6 分支收敛为统一 dispatch；save_memory 走统一 head；frozenset 保留作 drift guard 但不再驱动 sectioned/plain 分歧 |
| `src/cli/display.py` | rename | +40 / -40 | `_render_perception_tool` → `_render_tool_body`（含 docstring 更新；含 `_summarize_set_next_wake_at` docstring 去 Reason: 描述）；实测 40 处 call site / import / 测试引用同步（`grep -rn _render_perception_tool src/ tests/`） |
| `src/cli/display.py` | refactor | -20 | `summarize_tool` 在 session log path 退出调用链（system.log INFO 摘要通道继续用，不动） |
| `src/agent/tools_execution.py` | edit return | +25 | `set_stop_loss` capture `o.trigger_price` + return 加 `(was X)` (~5 行)；`set_take_profit` 同 (~5 行)；3 outlier 工具去 reasoning 后缀（update_price_level_alert / set_next_wake / set_next_wake_at，~5 行 total） |
| `src/agent/tools_descriptions.py` | edit DESC | ±0 (char-level) | `SET_NEXT_WAKE_DESCRIPTION` / `SET_NEXT_WAKE_AT_DESCRIPTION` Examples 段 `. Reason: ...` 后缀字符级缩短（per §3.6 LLM-visible docstring 同步）— 行数净 0 |

合计 src：约 **+175 / -150**（净 **+25**）。

scope 内**不动** `src/integrations/exchange/`（adjust_leverage prev state 移到 §6 触发型 candidate，避免 BaseExchange 接口扩展破坏 OKX 实例化）。

### 4.2 tests 改动

| 文件 | 改动类型 | 估行数 |
|---|---|---|
| `tests/test_args_format.py` | new | +150 | `_format_args_as_call` 单测：每个值类型 2 case + 空 args + 长 list + 字段顺序 + reasoning retain（uniform 不 strip）+ 异常 fallback |
| `tests/test_display_cycle.py` | snapshot rebuild | +400 / -250 | **48 byte-equal 测试硬重建** (44 `test_snapshot_*` + 4 `test_render_perception_tool_*`)；另 7 个 loose `in out` assertion tests (3 `test_render_action_*` + 4 `test_format_cycle_output_*` + `test_int_1_render_action_mixed_perception_execution`) 多数免改，但 padded literal `⚙ set_next_wake          5min` 类断言需个别 check |
| `tests/test_tools_execution.py` | edit | +20 | `set_stop_loss` / `set_take_profit` return 字面断言 + first-set 'unset' case；3 outlier 工具 reasoning 去除断言更新（~10 处） |
| `tests/test_alert_age.py` | edit | +5 / -10 | `test_update_tool_return_string_shape` regex 去 `— ".+"$` 末段 + 删 `— "trail up after breakout" in result` assert（line 207-218） |
| `tests/test_alert_family.py` | edit | +0 / -2 | line 357 / 395 `update_success` 样本字符串去 `— "4h structural high"`（read-only refresh，无行为影响） |

合计 tests：约 **+675 / -312**（净 **+363**）。

### 4.3 总改动量

- src 净 +25 / tests 净 +363 / spec ~+450 行
- 合计 src + tests ≈ **+680-870 行**（远超 [[feedback-docs-only-direct-merge]] 100 行 mini-iter 阈值）
- 走 PR 路径确认无误

### 4.4 不改动

- `src/services/tool_call_recorder.py` — args 已写 DB，本 iter 不动
- `src/storage/models.py` / alembic — 无 schema 变更
- `src/cli/logging_config.py` — session log file dual-write 机制不动
- `summarize_tool` / `_SYSTEM_LOG_PERCEPTION_PARSERS` / `_EXECUTION_PARSERS` — system.log INFO 摘要通道继续用
- 任何 perception 工具的 docstring / return — perception 类 args 已可全靠 `_format_args_as_call` 渲染，无需改 return；docstring 中 PR #59/#60 已 promote 的 Examples / Returns 段描述的是 return body shape，不涉及 args / head 形态，新设计不需 perception docstring 同步

## 5. 测试覆盖

### 5.1 单元测试

- `_format_args_as_call`：每个值类型 case + 空 args + 长 list + 字段顺序 + reasoning retain（uniform 不 strip）+ args_as_dict 异常 fallback
- `_render_action` 统一 dispatch：4 类 head 形态（统一 ⚙/✎ + 3 fallback orphan/error/drift） × 2 类 body（sectioned by-content / plain by-content）
- SL/TP return 改进字面断言：含 `was X` + first-set 'unset' case + 已有值替换 case
- 3 outlier 工具 reasoning 移除断言：`update_price_level_alert` return 不含 `— "`；`set_next_wake` / `set_next_wake_at` return 不含 `Reason:`

### 5.2 集成 snapshot 测试

`tests/test_display_cycle.py` 实测 **48 byte-equal 测试**（44 `test_snapshot_*` + 4 `test_render_perception_tool_*`，详 §4.2 测算）全量重建：

- 每个 snapshot 由人工 review + 一次性 baseline commit
- 不引入 snapshot auto-regen tool（per `feedback_no_auto_edit`）
- review 总成本：48 × ~6 min ≈ **~5 小时**（原 spec 估 1-2h 严重低估；分批 commit 控制单次 PR review 负担）
- 另 7 个 loose `in out` assertion 测试需 individual check（多数 substring 匹配不受新 head 形态影响）

### 5.3 W4 sim 真实数据 verify

W4 sim #11 当前数据已 cover **≥443 tool call samples / ≥53 cycles / ≥28 distinct tool names**（session `715d3e81`，2026-05-25→26，**session 仍在跑**，数字持续增长；impl 时刷新最新值）。离线 re-render 覆盖面充足。impl 完成后用 **离线 re-render** 做 verify（避免新跑 sim 引入 LLM / 市场 noise）：

- 所有 args 类型 case 渲染正确（str / int / float / bool / None / list / 空 args）
- head args 内 reasoning 渲染验证（W3+W4 sim 实测 avg 190-400 chars，head 折 2-4 物理行）
- SL/TP return 改进版的 forensic 信息完整（含 `was` 字面）
- 3 outlier 工具 return normalize 后不含 `Reason:` / `— "`
- log 膨胀实测

**膨胀 baseline 度量方法**（原"离线 re-render"路径在 plan Task 6 阶段实证物理不可行 — pydantic-ai `ModelResponse` messages 在内存中，DB 未持久化 `ToolCallPart` + `ToolReturnPart`，无法 offline 重渲染同 cycle；详 plan Task 6 rationale）。

**改为：live sim sample-based soft estimate**（plan Task 6 步骤）：
- 数据源：impl merge 后在 PR 分支启动一个新 sim run，跑 ≥ 5-10 cycles
- 度量步骤：
  1. baseline：W3 sim #10 现有 `logs/session_1bbaa19f-...log` 的 `wc -c / grep -c "═══"` → 单 cycle 平均字节
  2. after：新 sim run 的 session log 同度量
  3. 比值 = after / baseline avg bytes/cycle
- 通过阈值：**预期比值 1.03-1.10**（reasoning 入参注入 §7.1 理论估算 +3-8%）；比值 > 1.20 escalate（可能 reasoning escape 或其他展开 mis-implementation）
- caveat：sim runs 有 LLM / 市场内在 variance，比值是 soft signal 非硬阈值

**head 内 reasoning 命中率 review**（W4 impl merge 后 1 周内）：
- 用户在 forensic 分析时主动 tally：每次抽样 cycle 看 head 内 reasoning，记录是否提供了 cycle `▾ Reasoning` block 之外的信号
- 阈值表：
  - ≥ 30% 或 ≥ 3 次显式引用 → 保留方案 D（reasoning 在 head 真实有价值）
  - 10-30% → 延长观察期 / W5 决议
  - < 10% → 启 reasoning strip mini-iter（与 DB recorder 路径再次对齐）

## 6. YAGNI / scope 外

| 议题 | 状态 | 理由 |
|---|---|---|
| system.log INFO 摘要通道改造 | scope 外 | system.log 是 ops 监控不是 forensic；保留现有 `summarize_tool` |
| args 默认值省略策略 | scope 外 | forensic attribution 信号缺失（无法区分 agent 显式传默认值 vs 用默认值）；与 [[tool-design-principles]] 原则 1 精神一致 |
| args head 长度截断 | scope 外 | 实测 args 通常 < 80 chars（不含 reasoning）；含 reasoning 时 200-400 chars 软折行 2-4 行可接受 |
| reasoning strip from head | 触发型 candidate | 本 iter retain（与 DB recorder 路径 known divergence）；W4 impl 后 1 周内 forensic 命中率 < 10% → 启 strip mini-iter（per §5.3） |
| `adjust_leverage` prev state | 触发型 candidate | success path 无 position → fetch_position 物理读不到 prev lev；BaseExchange 抽象方法扩展破坏 OKX 实例化（sim-only phase 不优先）；触发条件：W4+ forensic 实证 adjust_leverage 频次高且 head 内 `leverage=Xx` 不足以承载 forensic 信息 |
| `set_next_wake` / `set_next_wake_at` prev state | 触发型 candidate | TradingDeps callable 单向；wiring 改造成本不匹配；下一 cycle args 即 new state，cycle 间隔可推算 prev（见 §3.5） |
| 其余 9 个 execution 工具 return 改进 | 触发型 candidate | 本 iter 仅做 SL / TP 2 个真缺 state-delta 工具；其余（open / close / cancel / 各类 alert）W4 forensic 实证频次驱动 |
| save_memory 路径深度优化 | scope 外 | retired tool（iter-w2r3-memory-disable）；本 iter 仅统一其 head 形态（与其他工具一致），不改 args schema / 入参 |
| BaseExchange 接口扩展 | scope 外 | adjust_leverage 移出后无需扩展 `get_leverage`；OKX 路径维护通过测试即可（per CLAUDE.md sim-only phase） |

## 7. Tradeoff 与风险

### 7.1 log 膨胀

- execution 类从 1 行 → 1-N 行（含 reasoning 入参 head 折 2-4 物理行 + body 1-3 行）
- W3+W4 实测 reasoning 入参 avg 190-400 chars；每 cycle 5-10 execution calls 贡献 +1-3KB；预估单 cycle log 膨胀 **+3-8%**
- baseline 度量见 §5.3（离线 re-render）；阈值 **< 15%**（留 buffer）
- 可接受 — session log 用途是 forensic，不是 streaming consumer

### 7.2 snapshot test 重建成本

- **实测 51 byte-equal 测试**（详 §4.2）
- review 时间：51 × ~7 min ≈ **~6 小时**（原 spec 估 1-2h 严重低估）
- 一次性成本，commit 后不再回潮
- mitigation：可分批 commit（先 perception snapshot 一批 / 后 execution snapshot 一批），每批独立 review，控制单次 PR review 负担

### 7.3 R2-8c semantic summary 优化"形态迁移"

- 14 个 execution 工具的 single-line semantic summary（如 `update_price_level_alert above $76,880 → $76,860`）在新设计下信息源迁移到 head args + tool return body
- 3 outlier 工具（含 reasoning 在 return 中）经 §3.6 normalize 后 14 工具完全统一形态
- 信息量持平或更高（args 完整 + state delta 完整）；视觉上从 1 行 → 多行 sectioned，与 perception 工具范式一致
- 残留风险：W4 实测验证哪些工具 forensic 痛点真实出现 → 触发后续 iter（如 head 内 reasoning 命中率 < 10% / 9 个未改进 execution 工具 forensic 频次高）

### 7.4 字段顺序 drift

- args 顺序**实质来自 LLM 输出**（pydantic-ai `args_as_dict()` 不强制 schema 序，per §3.2）；实测通常匹配 schema 定义序但非契约保证
- 如 LLM 模型 / JSON wire format 变化（如 anthropic SDK 升版改 tool_use input 序），args 顺序可能不稳定
- mitigation：`_format_args_as_call` helper 自身的 dict-iteration 行为单测断言 `_format_args_as_call("test_tool", {"a":1, "b":2}) == 'test_tool(a=1, b=2)'`
- **强度澄清**：此 mitigation 仅验证 helper 内的 dict 遍历不引入二次排序；**不能**真验证 LLM 输出顺序 → `args_as_dict()` 输出序的稳定性（这是上游 dependency 假设）。后者由 W4 sim integration test 暴露（real ToolCallPart 通过 agent.run 产生）

### 7.5 reasoning retain 与 DB recorder 路径 divergence

- `tool_call_recorder.py:138` strip reasoning vs `display.py:_format_args_as_call` retain reasoning — known divergence（per §3.2）
- 风险：未来 forensic 跨 cycle 聚合分析（如"agent 在 set_stop_loss 调用时 reasoning 长度分布"）必须从 `trade_actions.reasoning` 取，不能从 `tool_calls.args` 取（reasoning 已 strip）
- mitigation：spec §3.2 显式标注 known divergence；W4 forensic 命中率 < 10% → strip mini-iter 反向对齐

## 8. 验收条件

- [ ] `_format_args_as_call` 单元测试通过（≥10 case + reasoning retain + 字段顺序 helper 自验）
- [ ] `_render_action` 统一 dispatch 后 4 类 head + 2 类 body 渲染正确（含 save_memory 走统一 head）
- [ ] `_render_perception_tool` rename 为 `_render_tool_body` 完成，全部 call site 同步更新
- [ ] `set_stop_loss` return: update path 含 `→` (old → new)；first-set path 沿用单值 shape；`_summarize_set_stop_loss` regex 扩展 dual-shape 后 display 正确
- [ ] `set_take_profit` return: update path 含 `→`；first-set path 沿用单值；display regex 同 SL 对称
- [ ] 3 outlier 工具 return normalize：`update_price_level_alert` return 不含 `— "`；`set_next_wake` / `set_next_wake_at` return 不含 `Reason:`
- [ ] **`tools_descriptions.py` 同步**：`SET_NEXT_WAKE_DESCRIPTION` / `SET_NEXT_WAKE_AT_DESCRIPTION` Examples 段去 `. Reason: ...` 后缀（LLM-visible docstring 与实际 return 一致 — fact-provider 原则 1 闭环）
- [ ] **`display.py:266` `_summarize_set_next_wake_at` docstring 同步**：去 `Reason: ...` 字面描述
- [ ] **`test_alert_age.py` regex 更新**：`test_update_tool_return_string_shape` regex 末段 `— ".+"$` 移除 + content assert 删除
- [ ] snapshot 重建后全 48 byte-equal pass + 7 个 loose-assertion 测试 individual check 通过
- [ ] live sim sample-based manual review 1 次（args 完整 + SL/TP old → new prev state + 3 工具 reasoning normalize + Rich markup escape safety；详 §5.3）
- [ ] log 膨胀比值 1.03-1.20（按 §5.3 live sim baseline 度量；> 1.20 escalate；离线 re-render 物理不可行已 supersede 原 §5.3 离线方法）
- [ ] W4 impl merge 后 1 周内做 head 内 reasoning forensic 命中率 review（结果落 memory，决议是否启 strip mini-iter）
- [ ] PR review + merge

## 9. 相关 memory / 参考

- [[tool-design-principles]] §1 fact-provider / §4 信号补齐 / §7 输出表达友好
- [[feedback-systematic-debugging-check-system-log]] forensic 时 session log 自包含价值
- [[r2-8c-tool-output-optimization]] 当前 execution 类 single-line summary 的设计源头
- [[feedback-brainstorm-decision-location]] / [[feedback-plan-doc-commit-first]] 工作流约定
- [[feedback-docs-only-direct-merge]] mini-iter 阈值（本 iter src > 100 行，走 PR）
