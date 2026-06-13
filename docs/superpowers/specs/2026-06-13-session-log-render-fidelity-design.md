# Session log 渲染保真设计（mid-cycle 注入块 error 路径 + scheduled wake reason 截断）

## 目标

修两处 session log 渲染失真——**用户在 session log 看到的与 agent 实读的 prompt 不一致**，会误导对 agent 行为/决策的判读：

1. **mid-cycle 注入块在工具拒绝/出错路径被压平**：cycle 中途触发的事件（fill / alert）注入在工具返回之后；当该工具返回是业务拒绝（如 `close_position` 的 "No positions to close."）时，渲染走单行 error 路径，把注入块连同换行折叠、80 字符截断——注入事件几乎不可见。agent 实读的 prompt 是完整结构化的，唯独 session log 失真。

2. **scheduled wake reason 在内含空行时被截断**：Context 段回显 agent 上轮 `set_next_wake` 的 reasoning，提取时按第一个 `\n\n` 截断——多段落 reasoning 会丢尾段，而 agent 看到的是全文。

两处均**纯 render 层（只动 `src/cli/display.py`）**，不改 agent prompt、不改落库数据、不改 system-log 摘要通道。

## 背景 / 当前状态

### 渲染数据来源（关键前提）

- session log 由 `format_cycle_output(ctx)`（`display.py:1369`）渲染，读 in-memory `ctx.messages`（pydantic-ai message history）。注入块由 `MidCycleEventInjector.wrap_tool_execute`（`midcycle_injector.py:129`）以 `result + block` 追加进 ToolReturnPart——**只在 message history，不落 `tool_calls.result`**（`ToolCallRecorder` 是内层 capability，注入前已捕获 raw result；sim #19 实测 630 条 tool_calls 的 result 列全 NULL，因 result 持久化 PR 在 sim #19 之后才合并）。故 session log 是注入块的唯一可视来源。
- Context 段由 `_render_context`（`display.py:1149`）从 `agent_cycles.user_prompt_snapshot`（agent 本轮实读那份）派生。

### Issue 1 根因

- `_render_action`（`display.py:1218`）每条 tool call 分三支：1a/1b ret-None；**Branch 2**（`is_tool_error` → 单行 `✗ {args_call} {_fallback_summary(content_str)}`，`display.py:1288-1292`）；**Branch 3**（happy path → `_render_tool_body`，多行 section 渲染）。
- 注入块 full-keep 豁免（`_FULL_KEEP_SECTION_PREFIXES`，`display.py:579`；`_is_full_keep_section`，`display.py:582`）**只在 Branch 3 的 `_render_tool_body` 内生效**。
- 业务拒绝（`close_position` 返回 "No positions to close."，不匹配成功前缀 `("Orders submitted:", "Closed")`，`display.py:309`）→ `is_tool_error` 判 True → 走 Branch 2 → `_fallback_summary`（`display.py:49`）`" ".join(content.split())` 折叠全部换行 + 截断 80 char。注入块被腰斩。
- 实证：sim #19 cycle `4a9e5c4e`（agent 平仓但 stop 已先成交，close 返回拒绝、同时注入该 stop fill），渲染为 `... No positions to close. === NEW EVENTS TRIGGERED (1 fill) === IMPORTANT EVENT: st`（正好 80 char + `...`，"stop" 腰斩成 "st"）。7 次工具后注入仅此 1 次坏（其余落 perception / 成功 execution 工具，走 Branch 3 正常多行）。
- bug 类边界：触发条件 = **注入落在被 `is_tool_error` 判为 error/拒绝的工具上**。

### Issue 2 根因

- `_extract_scheduled_wake_context`（`display.py:1036-1056`）：`rest.split("\n\n", 1)[0]` 取 marker 后第一段。SCHEDULED WAKE CONTEXT block 在 prompt 里是 scheduled 事件块（`event_render.py:122-123`），其后即 summaries（已被 `_split_wake_prompt` 切走）→ **该 block 恒为 `wake_half` 末尾段**（sim #19 实证 26/26 HAS_REASON cycle）。故 `split("\n\n")` 不必要，且会截断 agent 设的多段落 reasoning。
- 实证：sim #19 实际 reasoning 均单段，渲染与 agent 全文 content 差异 0/26，未触发；属真实但低频的保真缺口。

## 改动面（纯 render，只动 display.py + tests）

| # | 位置 | 改动 |
|---|------|------|
| 1 | `display.py` 新增 `_split_injection_block` | 按 `"\n\n=== " + INJECTION_HEADER_PREFIX` 锚点把 content 切成 `(tool_result, injection \| None)` |
| 2 | `display.py` 从 `_render_tool_body` 抽出 `_render_sections` | section 解析 + full-keep + 缩进的渲染循环（`display.py:623-637`）共享化 |
| 3 | `display.py` `_render_action` | 分支分派前 pre-split；Branch 2/3 只吃 `tool_result`；`injection` 统一在末尾用 `_render_sections` 渲成 full-keep section |
| 4 | `display.py` `_extract_scheduled_wake_context` | 去掉 `.split("\n\n", 1)[0]`，取整段 `rest` |
| 5 | `tests/` | 见测试节 |

## Issue 1 修复：统一 pre-split（Option B）

import `INJECTION_HEADER_PREFIX`（`midcycle_injector.py:36`）作 split 锚点，避免字面 drift（与 `_FULL_KEEP_SECTION_PREFIXES` 既有"逐字同源"约定一致）。

```python
def _split_injection_block(content: str) -> tuple[str, str | None]:
    anchor = "\n\n=== " + INJECTION_HEADER_PREFIX
    idx = content.find(anchor)
    if idx == -1:
        return content, None
    return content[:idx], content[idx:].lstrip("\n")
```

`_render_action` 每条 call（`display.py:1280` 后）改为：

```python
content_str = str(ret.content)
tool_result, injection = _split_injection_block(content_str)
# ... args_call / icon 不变 ...
if is_tool_error(tcp.tool_name, tool_result, outcome):
    rendered = f"  ✗ {escape(args_call)} {escape(_fallback_summary(tool_result))}"
else:
    rendered = _render_tool_body(tcp.tool_name, tool_result, head_icon=icon, head_args=args_call)
if injection:
    rendered += "\n\n" + "\n".join(_render_sections(injection))
lines.append(rendered)
```

设计要点：

- **`is_tool_error` 改吃 `tool_result`**：成功前缀在开头、注入在末尾，分类结果与吃含注入 content 完全相同；判定只看工具真实返回更干净。
- **注入渲染单一来源、与 error/happy 路径解耦**：根治"注入渲染寄生在工具成功/失败分支"的耦合（即 bug 本因——将来若新增第三条渲染路径，同类 bug 不复发）。
- **happy-path 逐字节不变**：注入块经 `_render_sections`（内含 `_is_full_keep_section` 对 "NEW EVENTS TRIGGERED" 的全保留豁免），以 `"\n\n"` 单空行分隔拼接——已用真实 `display.py` 渲染函数验证：`body + "\n\n" + _render_sections(injection)` 与现状 `_render_tool_body(整段含注入)` 输出 byte-identical。
- **`_FULL_KEEP_SECTION_PREFIXES` 的 "NEW EVENTS TRIGGERED" 项保留不删**：改由 injection 路径的 `_render_sections` 消费（happy 路径 `_render_tool_body` 此后只收到 injection-free 内容，不再经手该 section）。两路径同调 `_render_sections`，full-keep 逻辑单源不漂；该 frozenset 同时仍覆盖真实工具 section "Taker Flow"。

### 修复后渲染（sim #19 cycle `4a9e5c4e`，error 路径）

```
  ✗ close_position(reasoning="Thesis invalidated...") No positions to close.

    === NEW EVENTS TRIGGERED (1 fill) ===
    IMPORTANT EVENT: stop triggered — BTC/USDT:USDT 15.73 @ 63614.4, Fee: -10.01 USDT, PnL: -8.70 USDT (gross) / -18.71 USDT (this fill, equiv-round-trip) — filled 13:47 UTC (just now)
```

## Issue 2 修复：scheduled wake reason 取整段

```python
rest = wake_half[idx + len(marker):]
return re.sub(r"\s+", " ", rest).strip()   # 去掉 .split("\n\n", 1)[0]
```

`re.sub(r"\s+", " ")` 已归一内部换行（含 agent free-text 里的杂散 `\n\n`）保持 Woke-by 单行；block 恒为 `wake_half` 末尾段（已验证），取整段 `rest` 安全、不再截断多段落 reasoning。

## Out of scope（触发型 candidate，留存触发条件）

- **agent-facing 定界符重设计 / session-log 注入块醒目标记**：`=== NEW EVENTS TRIGGERED ===` 与工具自身输出 section 同 `=== ... ===` 语法。**暂不立项**——sim #19 最高信号样本（fill-injection n=2，cycle `4a9e5c4e` / `cf00ef88`）显示 agent 正确把注入事件当 async 状态变更解析（精确引用 fill 价/PnL、把注入 fill 与注入 percentage_alert 关联成同一市场事件），无观察到的混淆（per 工具设计原则 8：信任 agent，format nudge 是 last-resort，不预防性改 agent-facing 格式；样本局限 n=2 但属定性强信号，非脆弱截面代理）。**触发条件**：后续 sim 出现 agent 把注入事件误归因为工具输出 / 与工具 section 混淆致错误推理或决策的 narrative，则以该 narrative 为实证立 agent-facing spec（render-only 醒目标记方案当时一并落地——pre-split 已使注入块成独立 render 单元，加标记零额外耦合）。
- **system-log INFO 摘要通道**（`resolve_tool_display`，`cli/app.py`）：读同一份 `result + block`，那条 80 字符 INFO 摘要同样会压平注入，但属 ephemeral 终端日志、非 forensic 落库产物，stakes 低，本轮不动。

## 测试

- Issue 1：
  - `_split_injection_block` 单测：无注入 → `(content, None)`；有注入 → 锚点处正确切分（tool_result 不含注入、injection 以 header 起头）。
  - 注入-after-拒绝（`close_position` "No positions to close." + fill 注入）→ 渲染出独立多行 `=== NEW EVENTS TRIGGERED ===` full-keep section，事件行完整、不被折叠/截断。
  - 注入-after-happy（perception 工具返回 + 注入）→ 与现状 `_render_tool_body(整段含注入)` 输出 byte-identical（回归 guard）。
- Issue 2：
  - 多段落（内含 `\n\n`）scheduled wake reason → Context `Woke by` 行完整渲出全文，无截断。
  - 既有 scheduled / alert / conditional Context 渲染测试全绿。
- 既有 `test_display_cycle.py` / `test_session_log_cycle_context.py` / `test_midcycle_injection_integration.py` / `test_event_render.py` 回归。
