# iter-alert-trigger-id-unknown-tool-render — Spec & Plan

**Date**: 2026-05-29
**Scope**: mini-iter (per `feedback_docs_only_direct_merge` — feature branch → direct-merge,
src 改动 < 100 行)
**Trigger**: `get_active_alerts` audit (`.working/tool-audits/2026-05-28-get_active_alerts.md`)
附录 out-of-scope finding #1 + #2 — 两个 alert-adjacent finding 打包修复

## 1. 议题与根因

### #2 Alert trigger prompt 缺 alert_id

**现象**: `PriceLevelAlertInfo.alert_id` 数据结构有 `alert_id` 字段 (8-char hex,
`src/integrations/exchange/base.py:371`), 但 alert-triggered cycle 的 user prompt
(`src/cli/app.py:514-519`) 只 surface `direction + target_price + reasoning`,
**未携带 alert_id**.

**影响 (audit 量化)**: 84 alert-triggered cycles × 85.7% 内调 `get_active_alerts`
= 72 reconciliation events/session — agent 用 `get_active_alerts` 反向"找哪个 ID
不在 list 里"来确定哪个 alert fired (e.g. session log cycle 8af2 line 23976:
"There's no alert at 75880. This alert must have been auto-cleared or was from
an external source.").

**原则违反**: §3 信号唯一权威来源 — `alert_id` 本应是 fired alert 的 unique identifier,
fired event 的 source-of-truth 应直接 surface,不让 agent 反向推导.

### #1 Singular `get_active_alert` log artifact (4 occurrences)

**现象**: session log 中 4 处 `⚙ get_active_alert() ` (singular, trailing space,
no output, no `[no return captured]` tag), DB `tool_calls` 表 0 行.

**实证根因 (彻底闭环)**:

1. LLM 幻觉调用了不存在的工具名 `get_active_alert` (singular, 缺 `s`)
2. pydantic-ai `tool_manager.py:375` 检测到 unknown tool → `raise ModelRetry(...)`
3. ModelRetry 被 `_wrap_error_as_retry` (line 175-181) 转为 **`RetryPromptPart`**
   (NOT `ToolReturnPart`)
4. 我们的 `format_cycle_output` (`src/cli/display.py:1066-1071`) 只 capture
   `ToolReturnPart` 进 `tool_returns_lookup` — `RetryPromptPart` 被丢
5. `_render_action` 对该 tool_call 查 `tool_returns_lookup.get(tool_call_id)` → None
6. 走 orphan path (line 943-949), 应该输出
   `  ⚙ get_active_alert() [no return captured]`
7. **关键 bug** (display.py:948):
   ```python
   lines.append(f"  ⚙ {tcp.tool_name}() [no return captured]")
   ```
   **literal `[no return captured]` 未经 `escape()`** — Rich `console.print`
   把 `[no return captured]` 解析为 markup tag 并静默 strip,只留 trailing
   space (前 ` ` from f-string)
8. 实证 Rich 输出: `'  ⚙ get_active_alert() \n'` (与 session log 完全一致)
9. agent 在下一个 Reasoning 块自纠正: "Let me fix that - `get_active_alerts`
   not `get_active_alert`" (session log L40235, L81237 都有)

**System 行为本身正确**: 幻觉 → reject → retry → recover, **唯一缺陷是渲染层
markup escape 漏**.

**4 处验证**: cycle 834c / 1c2f / 99f9 / 2e57 — 4 处都是同一 LLM hallucination
→ self-correct 模式, 100% systematic.

## 2. 修复方案

### Fix A (#2): `cli/app.py:513-519` 抽 helper `_format_price_level_alert_trigger` + 加 alert_id

**抽 helper** (per review point 2 — 避免 test 走 mock 全 cycle 的 fragile path):

```python
# cli/app.py 模块顶层 (与 _build_recent_summaries_block 同位置) 新增 helper
def _format_price_level_alert_trigger(context: PriceLevelAlertInfo) -> str:
    """Build the PRICE LEVEL trigger suffix exposing alert_id for lifecycle joins."""
    return (
        f"\n\nPRICE LEVEL: {context.symbol} reached {context.current_price:.2f} "
        f"(alert id={context.alert_id} {context.direction} {context.target_price:.2f} "
        f"— {context.reasoning})"
    )

# cli/app.py:514-519 替换为:
        if isinstance(context, PriceLevelAlertInfo):
            prompt += _format_price_level_alert_trigger(context)
        else:
            ...  # 既有 volatility-alert 分支不变
```

- 改动: src +~10 / -5 (helper 提取 + inline 替换);LLM-facing prompt 多出
  `id=8a3be62a` 段, drop "your" (prompt context 已暗示 ownership)
- **采纳 review point 5 cosmetic**: 改 `your alert:` → `alert id=` (drop "your"
  避免双重所有格 noise + 保留 id= 分隔语义)
- **测试好测**: helper 纯字符串,test 调 `_format_price_level_alert_trigger(ctx)`
  断言 substring,无需 mock engine / deps / agent.run

### Fix B (#1): orphan path 升级 — capture `RetryPromptPart` + 显式 ✗ 区分 hallucination vs 真 orphan

**两层修复**:

**B-1**: `cli/display.py:948` orphan path literal escape
```python
# 改前
lines.append(f"  ⚙ {tcp.tool_name}() [no return captured]")
# 改后
lines.append(f"  ⚙ {tcp.tool_name}() {escape('[no return captured]')}")
```
让 Rich `console.print` 把 `[no return captured]` 当**纯文本**而非 markup tag.

**B-2**: capture `RetryPromptPart` + 区分渲染 (per 原则 8 — agent 行为偏差 surface 给 maintainer)

```python
# format_cycle_output (display.py L1066-1071) — 新增 retry_lookup
tool_returns_lookup: dict = {}
retry_lookup: dict = {}  # NEW
for msg in ctx.messages:
    if isinstance(msg, ModelRequest):
        for part in msg.parts:
            if isinstance(part, ToolReturnPart):
                tool_returns_lookup[part.tool_call_id] = part
            elif isinstance(part, RetryPromptPart):  # NEW
                retry_lookup[part.tool_call_id] = part

# L1087 — 传入 retry_lookup
lines.append(_render_action(tool_calls, tool_returns_lookup, ctx.cycle_id, retry_lookup=retry_lookup))

# _render_action — kwarg with default 保持向后兼容,无需改既有 9 测试 callsite
def _render_action(
    tool_calls: list,
    returns_lookup: dict,
    cycle_id: str,
    retry_lookup: dict | None = None,  # NEW kwarg, default None
) -> str:
    ...
    for tcp in tool_calls:
        ret = returns_lookup.get(tcp.tool_call_id)
        if ret is None:
            retry = (retry_lookup or {}).get(tcp.tool_call_id)  # NEW
            if retry is not None:
                # pydantic-ai 拒绝该 call — 经 _wrap_error_as_retry 路径产出 RetryPromptPart,
                # 两种触发各对应不同 content 形态 (per pydantic_ai/messages.py:1321):
                #   - ModelRetry  (unknown tool / 工具内 raise) → content: str
                #   - ValidationError (arg-validation 失败)       → content: list[ErrorDetails]
                content = retry.content
                if isinstance(content, list):
                    # ErrorDetails dict 结构: {'type': ..., 'loc': (path,), 'msg': str, ...}
                    # 取前 3 条 loc + msg 拼成可读单行,>3 条截断 (内层 [:100] 再防爆)
                    first_line = "; ".join(
                        f"{'.'.join(map(str, e.get('loc', ())))}: {e.get('msg', '?')}"
                        for e in content[:3]
                    )[:100]
                else:
                    first_line = content.split('\n')[0][:100]
                lines.append(
                    f"  ✗ {escape(tcp.tool_name)}() "
                    f"{escape(f'[invalid call: {first_line}]')}"
                )
            else:
                # 真 orphan (tool_call_id mismatch — 理论上不应发生)
                logger.warning(
                    "tool_call_id mismatch for %s in cycle %s",
                    tcp.tool_name, cycle_id,
                )
                lines.append(
                    f"  ⚙ {escape(tcp.tool_name)}() {escape('[no return captured]')}"
                )
            continue
        ... # 既有 happy path 不变
```

- **kwarg with default `None`**: `_render_action` 既有 9 个测试 callsite 不需要改
  (passes `retry_lookup=None`, 走 fallback orphan path 行为与原一致)
- **B-1 单独 fix** 写在 retry==None 的 orphan 分支里 (真 orphan 兜底)
- **B-2 新增**: 任何 `RetryPromptPart` 路径走 ✗ + 摘要 (内层 100 字符截断防爆);
  **附带覆盖 arg-validation 失败** (`tool_manager.py:176-181` 实证 —
  `ValidationError` 与 `ModelRetry` 都经 `_wrap_error_as_retry` → 同一
  `RetryPromptPart` 路径,不走 ToolReturnPart `outcome != "success"`)
- **content list-vs-str 双形态分支**: `RetryPromptPart.content` 类型为
  `list[ErrorDetails] | str` (`messages.py:1321`)。`isinstance(content, list)`
  分支提取 `loc`+`msg` 字段拼可读单行 (前 3 条),避开 `str(list_obj)` 默认
  dict-repr 渲染 — 否则 arg-validation 路径退化为"能渲染但难读"
- **采纳 review point 4 nomenclature**: 用 `[invalid call: ...]` 而非
  `[rejected: ...]` — 与 pydantic-ai 内建 `outcome='denied'` (user-denied,
  `_agent_graph.py:1703` 唯一生成点) 语义区隔;label 同时覆盖 unknown tool +
  arg-validation 两类 reject
- **采纳 review point 3 — silent warning 降级声明** (intentional behavior change):
  原 `logger.warning("tool_call_id mismatch ...")` 仅保留为**真 orphan 兜底**;
  retry path 通过 ✗ icon + `[invalid call: ...]` 文本承担 observability,
  `system.log` **不再每次 hallucination 落 warning** (W2 实测 4/248 cycle 稀有
  事件,渲染层可见已足够;system.log 噪声降低)
- `escape(tcp.tool_name)` 加 escape 是 belt-and-suspenders (工具名理论 ASCII,
  但 LLM 幻觉 args 可能传 markup char 进 tool_name — 防御)

## 3. 测试

### `test_iter_alert_trigger_id_unknown_tool_render.py` (新文件)

#### #2 covers (走 helper 直接调用, 不 mock 全 cycle)

- `test_format_price_level_alert_trigger_includes_alert_id`:
  - 构造 `PriceLevelAlertInfo(alert_id="abc12345", direction="above",
    target_price=76470.0, current_price=76482.5, reasoning="early warning", ...)`
  - 直接调 `_format_price_level_alert_trigger(context)` → returns str
  - 断言: `"id=abc12345"` / `"above 76470.00"` / `"early warning"` 都在返回
- `test_format_price_level_alert_trigger_drops_pronoun`:
  - 同上构造,断言返回**不含** `"your alert"` (review point 5 — drop "your"
    避免双重所有格 noise)

#### #1 covers

**B-1 regression**: `test_orphan_no_return_captured_survives_rich_markup`
- 构造 `ToolCallPart(tool_name="get_active_alert", ...)`, 空 returns_lookup, 不传 retry_lookup
- 调 `_render_action(calls, {}, cycle_id="abc")` (走真 orphan 兜底分支)
- 用 `Console(file=buf, no_color=True, width=120)` 模拟 SessionConsole
- 断言 `buf.getvalue()` 含字面串 `"[no return captured]"`
- 防止后续 refactor 漏 escape 又导致 markup strip

**B-2 str content** (ModelRetry / unknown tool 路径):
`test_retry_prompt_renders_as_invalid_call`
- 构造 `RetryPromptPart(content="Unknown tool name: 'get_active_alert'. Available tools: ...")`
  (**str 形态** — ModelRetry 路径)
- 调 `_render_action(calls, {}, cycle_id="abc", retry_lookup={"c1": retry_part})`
- 用 Console 模拟 render
- 断言:
  - 输出含 `"✗"` (不是 ⚙)
  - 输出含 `"get_active_alert"` (tool 名保留)
  - 输出含 `"[invalid call:"` 前缀 (与 `outcome='denied'` 区隔)
  - 输出含 `"Unknown tool name"` (摘自 retry.content 第一行)
  - 输出**不含** `"[no return captured]"` (走 retry 分支不走 orphan)

**B-2 list content** (ValidationError / arg-validation 路径):
`test_retry_prompt_list_content_formats_loc_and_msg`
- 构造 `RetryPromptPart(content=[
    {'type': 'missing', 'loc': ('symbol',), 'msg': 'Field required', ...},
    {'type': 'int_parsing', 'loc': ('amount',), 'msg': 'Input should be valid integer', ...},
  ])` (**list[ErrorDetails] 形态** — `tool_manager.py:178` 给出的真实 shape)
- 断言:
  - 输出含 `"symbol: Field required"` (loc + msg 拼接)
  - 输出含 `"amount: Input should be valid integer"`
  - **不含** 原始 dict repr `"{'type':"` 或 `"'loc':"` (避免 ugly str(list_obj))

**B-2 边界**:
- `test_retry_prompt_str_first_line_capped_at_100_chars`: str 形态长 content,断 100 字符截断
- `test_retry_prompt_str_multiline_keeps_first_line_only`: str 形态多行,断只保留第一行
- `test_retry_prompt_list_caps_at_3_errors`: list 形态 ≥ 4 条 ErrorDetails,断只取前 3 条

**B-2 integration**: `test_format_cycle_output_captures_retry_prompt_part`
- 构造 minimal `messages` = `[ModelResponse([ToolCallPart]), ModelRequest([RetryPromptPart])]`
- 调 `format_cycle_output(ctx)` 验证 `RetryPromptPart` 经 lookup 进入 `_render_action`
- 断言输出含 `"✗"` / `"[invalid call:"`

### 既有测试不破

`_render_action` 在 `test_display_cycle.py` 共 **9 个 callsite**: lines
744 / 760 / 770 / 1547 / 1578 / 1622 / 2737 / 2808 / 2827。

全部 `_render_action(calls, ..., cycle_id=...)` 不传 `retry_lookup` 形态,默认
`None` 触发 `(retry_lookup or {})` 退到空 dict,走真 orphan 兜底分支 → raw
字符串依然含 `[no return captured]` (escape 不改字符串内容仅改 markup 语义),
所有 assertion 通过 — **无需修改既有测试**。

其中两处特别值得标注:
- **L770** `test_tc_4_no_return_captured` 与 **L2808** `test_ec_9_orphan_tool_call_id_no_return_captured`
  专测 orphan 兜底分支(空 returns_lookup),Fix B-1 后 `[no return captured]` 字符串
  保留,既有断言 `assert "[no return captured]" in out` 仍通过

## 4. 改动估算

| 文件 | 改动行 |
|---|---|
| `src/cli/app.py` (Fix A — helper + inline 替换) | +~10 / -5 |
| `src/cli/display.py` (Fix B-1 + B-2 — retry_lookup + list/str 分支 + escape + import) | +~28 / -4 |
| `tests/test_iter_alert_trigger_id_unknown_tool_render.py` (新) | +~150 / 9 tests |
| **总 src 改动** | **~29 net** |
| **总测试** | **~150 行 / 9 tests** |

仍在 mini-iter direct-merge < 100 行门槛内 (src 改动 ~29 << 100).

## 5. 不在 scope

- **未做**: address get_active_alerts audit 议题 1 (path B description 缺失) —
  defer to batch follow-up (≥ 3 类似工具触发再启)
- **未做**: 修 DB vs log 1-row drift (audit 附录 #3) — 独立 root cause 调研,
  保留为 cross-tool 累计证据触发型 candidate

(原 §5 第 3 bullet 关于 "arg-validation 走 ToolReturnPart 路径" 是事实错误,
review point 1 grep 实证后已删除;arg-validation 失败实际走 RetryPromptPart 同
路径,由 Fix B-2 附带覆盖,见 §2 Fix B-2 第二个 bullet)

## 6. 工作流

1. Commit 1: 本 spec doc (per `feedback_plan_doc_commit_first` — doc 先于 impl)
2. Commit 2: plan doc (independent commit, per CLAUDE.md `docs/superpowers/plans/`)
3. Commit 3+: Fix A + Fix B + 新测试文件 (impl, 可单 commit 或拆 commit)
4. pytest 全跑 (1922 baseline → 1931 期望: +9 new tests)
5. direct-merge → main (per `feedback_docs_only_direct_merge` mini-iter 路径)
6. Memory anchor 更新 `project_tradebot_status`

## 7. 闭环 sanity check (post-fix follow-up)

议题闭环 audit gate:

### Fix A 验证

下一轮 sim (post-merge first session) 跑完后, 量化 `get_active_alerts` 在
alert-triggered cycles 内调率,**baseline = 85.7%** (audit:72/84 events/session)。

**关键 caveat (避免 over-aggressive gate)**: alert-triggered cycle 内调
`get_active_alerts` 有两类:
1. **反向 reconcile fired alert** — Fix A 直接消除
2. **legitimate full-matrix read** — agent 看全部 active alerts 规划 lifecycle
   (cancel stale / update price / 检查 cap 是否到 20),Fix A 不消除

Baseline 85.7% 是两类**叠加**;Fix A 只压第 1 类。

**Gate (任一满足即 PASS)**:
- 相对降幅 ≥ 50% (i.e. 内调率 ≤ ~43%) **或**
- 绝对值进入 30-50% 窗口

**FAIL 信号**: 若 ≥ 50% **且** narrative grep `there's no alert at|must have been auto-clear|external source`
(反向推导 idiom) 命中率与 baseline 持平 → Fix A 失效,follow-up 检查:
- `cli/app.py` prompt 构建链路 alert_id 字段是否实际抵达 user prompt
- pydantic-ai 是否 strip
- agent 是否忽略

验证手段:
```sql
-- internal alert-trigger cycle 调用率
SELECT COUNT(DISTINCT cycle_id) * 1.0 /
       (SELECT COUNT(*) FROM agent_cycles WHERE session_id='<post-fix>' AND triggered_by='alert')
FROM tool_calls
WHERE session_id='<post-fix>' AND tool_name='get_active_alerts'
  AND cycle_id IN (SELECT cycle_id FROM agent_cycles
                   WHERE session_id='<post-fix>' AND triggered_by='alert');
```

### Fix B 验证

下一轮 sim 跑完后:
- hallucinated tool calls 应渲染为 `✗ <tool>() [invalid call: <first line>]`
  (不再是 silent ⚙)
- 通过 `grep -c '✗.*\[invalid call' logs/session_*.log` 估个数
- 若同 audit 期 (W3 4/248=1.6%) 数量级一致 → capture 路径有效
- 若 = 0 且 narrative 未见 "Let me fix that - get_active_alerts" 类自纠正 → LLM
  hallucination 在 post-fix model 下不再触发(也是 OK 结果,只是变量收敛)
- 若 narrative 仍有自纠正但 grep 不到 ✗ → capture 链路有 bug
