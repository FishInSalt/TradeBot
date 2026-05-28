# iter-alert-trigger-id-unknown-tool-render Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打包修复 `get_active_alerts` audit 附录 #1 (LLM 幻觉 → orphan path 渲染层 markup escape 漏) + #2 (alert-triggered cycle prompt 缺 `alert_id`)。两个 fix 都是 alert-adjacent surface friction,符合 mini-iter direct-merge 门槛。

**Architecture:** Fix A 抽 module-level helper `_format_price_level_alert_trigger(context)` 至 `cli/app.py`,内部产出 PRICE LEVEL trigger suffix 字符串 + 显式携带 `alert_id`;原 inline 拼接点改为单行 helper 调用,便于纯字符串单测。Fix B 双层: B-1 给 `cli/display.py:_render_action` orphan 兜底分支的 `[no return captured]` literal 加 `escape()` 防 Rich markup strip;B-2 在 `format_cycle_output` 顺便 capture `RetryPromptPart` 进新 `retry_lookup` 字典,通过 kwarg `retry_lookup: dict | None = None` 注入 `_render_action`,retry-present 时走新增 ✗ + `[invalid call: ...]` 分支(兼容 `content: list[ErrorDetails] | str` 双形态)。9 个既有 `_render_action` 测试 callsite 因 kwarg default `None` 不受影响。

**Tech Stack:** Python 3.13 / pydantic-ai 1.78 / Rich (markup escape via `rich.markup.escape`) / pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-29-iter-alert-trigger-id-unknown-tool-render-design.md`

**Branch:** `worktree-iter-alert-trigger-id-unknown-tool-render`(已创建,spec 已 commit `8880f68`)。

---

## Context for the implementer

### Project conventions

- **Worktree cwd**: 全程在 `/Users/z/Z/TradeBot/.claude/worktrees/iter-alert-trigger-id-unknown-tool-render` 操作。所有 file path 用绝对路径或相对此 worktree。
- **测试基线**: `1922 passed, 9 skipped` (worktree HEAD `8880f68` + 1 commit on top = spec only,无 src 改动)。9 个新测试预期 → `1931 passed`。
- **Mini-iter direct-merge 路径**: per `feedback_docs_only_direct_merge` — 工作完成后 `git checkout main && git merge --ff-only worktree-iter-...` 直接合 main,不创建 GitHub PR(但仍守 review-before-commit + tests pass + memory anchor 三纪律)。
- **No PR comment**: per `feedback_no_pr_comment`,跑完 code-review skill(若需)直接在对话报告,不 `gh pr comment`。

### pydantic-ai 1.78 关键事实(spec §1 验证过)

- `RetryPromptPart.content: list[pydantic_core.ErrorDetails] | str` (`messages.py:1321`)
  - ModelRetry (unknown tool / 工具内 raise) → `content = error.message: str`
  - ValidationError (arg-validation) → `content = error.errors(...): list[ErrorDetails]`
  - 两种路径都经 `_wrap_error_as_retry` (`tool_manager.py:175-181`) → 同一 `RetryPromptPart`
- `outcome: Literal['success', 'failed', 'denied']` (`messages.py:1086`),只有 ToolDenied 触发 `denied`(`_agent_graph.py:1703`)。本 iter 用 `invalid call` label 与 `denied` 区隔。
- `RetryPromptPart` 出现在 `ModelRequest.parts`,与 `ToolReturnPart` 同位置但不同类型 — 当前 `format_cycle_output` 只 capture 后者。

### 文件 lookup

| File | 关键 line |
|---|---|
| `src/cli/app.py` | 514-519 (PRICE LEVEL prompt 拼接处) / 1086 行附近的 `_format_relative_time` (module-level helper 同位置参考点) |
| `src/cli/display.py` | 1066-1071 (`tool_returns_lookup` 构建) / 1087 (`_render_action` 调用) / 943-949 (`_render_action` orphan path) / 9-17 (pydantic-ai imports) |
| `src/integrations/exchange/base.py` | 363-371 (`PriceLevelAlertInfo` dataclass 含 `alert_id`) |
| `tests/test_display_cycle.py` | 9 个 `_render_action` callsite: 744 / 760 / 770 / 1547 / 1578 / 1622 / 2737 / 2808 / 2827 |

### Run tests

```bash
# Worktree cwd:
cd /Users/z/Z/TradeBot/.claude/worktrees/iter-alert-trigger-id-unknown-tool-render

# Subset (本 iter 新文件)
python -m pytest tests/test_iter_alert_trigger_id_unknown_tool_render.py -v --tb=short

# Display.py regression
python -m pytest tests/test_display_cycle.py -v --tb=short

# Full suite (baseline 1922 → expected 1931)
python -m pytest tests/ --tb=short -q
```

---

## File Structure

| File | Action | Responsibility | LOC |
|---|---|---|---|
| `src/cli/app.py` | Modify | 抽 module-level helper `_format_price_level_alert_trigger(context)`;原 inline 拼接处改为单行 `prompt += _format_...(context)` | +~10 / -5 |
| `src/cli/display.py` | Modify | 加 `RetryPromptPart` import;`format_cycle_output` 加 `retry_lookup` 字典构建;`_render_action` 加 `retry_lookup` kwarg + retry-vs-orphan 分支 + B-1 escape + list/str content 双形态分支 + escape(tcp.tool_name) | +~28 / -4 |
| `tests/test_iter_alert_trigger_id_unknown_tool_render.py` | Create | 9 tests: 2 Fix A (prompt helper) + 1 B-1 regression + 4 B-2 happy/边界 + 1 B-2 list content + 1 B-2 integration | +~150 |

**总 src ~29 net + ~150 行 / 9 tests** << 100 mini-iter 门槛。

---

## Task 1: TDD pre-flight — 测试基线 + 写所有 9 个失败测试

**Files:**
- Modify: `tests/test_iter_alert_trigger_id_unknown_tool_render.py` (Create)
- Verify: 全 9 tests 都 fail (因为 src 还没改)

TDD 流程: 写完 9 个测试先跑一遍确认全部 fail (or 部分 fail with ImportError for new helper),再分别落 src。

- [ ] **Step 1: 确认 baseline pytest 通过**

```bash
cd /Users/z/Z/TradeBot/.claude/worktrees/iter-alert-trigger-id-unknown-tool-render
python -m pytest tests/ --tb=no -q
```

期望: `1922 passed, 9 skipped`。

- [ ] **Step 2: Create 新测试文件**

完整内容见下方"测试源码"段(放在本 plan 末尾,免散文档)。

- [ ] **Step 3: 跑新文件确认 fail**

```bash
python -m pytest tests/test_iter_alert_trigger_id_unknown_tool_render.py -v --tb=short
```

期望:
- Fix A 两 tests `test_format_price_level_alert_trigger_*`: fail with `ImportError` 或 `AttributeError` (helper 还没定义)
- B-1 `test_orphan_no_return_captured_survives_rich_markup`: fail — Rich strip 掉 `[no return captured]` (现有 bug)
- B-2 6 tests (str + list + edge + integration): fail with `TypeError` (kwarg `retry_lookup` 还没加) 或 assertion fail

---

## Task 2: Fix A — `_format_price_level_alert_trigger` helper + prompt 拼接替换

**Files:**
- Modify: `src/cli/app.py:337` (helper 插入位置 — 与 `_build_recent_summaries_block` 同位置) + `:514-519` (inline 替换)

- [ ] **Step 1: 加 module-level helper**

`src/cli/app.py` 加新 helper(位置: `_build_recent_summaries_block` 定义之前或之后均可,选 `:337` 该函数定义前一行):

```python
def _format_price_level_alert_trigger(context: PriceLevelAlertInfo) -> str:
    """Build the PRICE LEVEL trigger suffix exposing alert_id for lifecycle joins."""
    return (
        f"\n\nPRICE LEVEL: {context.symbol} reached {context.current_price:.2f} "
        f"(alert id={context.alert_id} {context.direction} {context.target_price:.2f} "
        f"— {context.reasoning})"
    )
```

**注**: `PriceLevelAlertInfo` 已在 `app.py:44` import,helper 可直接用类型 annotation。

- [ ] **Step 2: 替换 inline 拼接**

`src/cli/app.py:514-519`:

```python
# 改前
        if isinstance(context, PriceLevelAlertInfo):
            prompt += (
                f"\n\nPRICE LEVEL: {context.symbol} reached {context.current_price:.2f} "
                f"(your alert: {context.direction} {context.target_price:.2f} "
                f"— {context.reasoning})"
            )

# 改后
        if isinstance(context, PriceLevelAlertInfo):
            prompt += _format_price_level_alert_trigger(context)
```

- [ ] **Step 3: 跑 Fix A 测试**

```bash
python -m pytest tests/test_iter_alert_trigger_id_unknown_tool_render.py::test_format_price_level_alert_trigger_includes_alert_id tests/test_iter_alert_trigger_id_unknown_tool_render.py::test_format_price_level_alert_trigger_drops_pronoun -v --tb=short
```

期望: 2 PASS。

---

## Task 3: Fix B-1 — orphan path `[no return captured]` escape

**Files:**
- Modify: `src/cli/display.py:948`

- [ ] **Step 1: 加 escape**

`src/cli/display.py:948` (原 orphan path 单行):

```python
# 改前
            lines.append(f"  ⚙ {tcp.tool_name}() [no return captured]")

# 改后(此 step 仅做 escape,Task 4 时还会再改这一段)
            lines.append(f"  ⚙ {escape(tcp.tool_name)}() {escape('[no return captured]')}")
```

**注**: `escape` 已在 `display.py:18` import (`from rich.markup import escape`),直接用。

- [ ] **Step 2: 跑 B-1 regression 测试**

```bash
python -m pytest tests/test_iter_alert_trigger_id_unknown_tool_render.py::test_orphan_no_return_captured_survives_rich_markup -v --tb=short
```

期望: PASS — `buf.getvalue()` 经 Rich console 后仍含 `"[no return captured]"`。

- [ ] **Step 3: 跑 9 个既有 callsite regression**

```bash
python -m pytest tests/test_display_cycle.py -v --tb=short -k "orphan or no_return or render_action or test_tc_4 or test_ec_9"
```

期望: 既有 `test_tc_4_no_return_captured` (L770) + `test_ec_9_orphan_tool_call_id_no_return_captured` (L2808) 都 PASS(raw 字符串依然含 `[no return captured]`,escape 仅改 markup 语义不改字符串内容)。

---

## Task 4: Fix B-2 — `RetryPromptPart` capture + ✗ 渲染 + list/str 双形态

**Files:**
- Modify: `src/cli/display.py` — 4 处:
  1. `:11-17` imports 加 `RetryPromptPart`
  2. `:920-924` `_render_action` 函数 signature 加 `retry_lookup` kwarg
  3. `:941-948` orphan path 改 retry-vs-real-orphan 分支(取代 Task 3 落的单行 escape)
  4. `:1066-1071` + `:1087` `format_cycle_output` 加 `retry_lookup` dict + 调用传参

- [ ] **Step 1: import 加 `RetryPromptPart`**

`src/cli/display.py:10-17`:

```python
# 改前
from pydantic_ai.messages import (
    INVALID_JSON_KEY,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

# 改后(加 RetryPromptPart, 保字母序)
from pydantic_ai.messages import (
    INVALID_JSON_KEY,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)
```

- [ ] **Step 2: `_render_action` signature 加 kwarg**

`src/cli/display.py:920-936`:

```python
# 改前
def _render_action(
    tool_calls: list,
    returns_lookup: dict,
    cycle_id: str,
) -> str:
    """Render Action section per spec §3.1 unified dispatch.

    Dispatch:
      1. ret None → orphan single-line: `⚙ tool_name() [no return captured]`
      2. is_tool_error → error single-line: `✗ tool_name(args) {fallback}`
      ...
    """

# 改后
def _render_action(
    tool_calls: list,
    returns_lookup: dict,
    cycle_id: str,
    retry_lookup: dict | None = None,
) -> str:
    """Render Action section per spec §3.1 unified dispatch.

    Dispatch:
      1a. ret None + retry present → `✗ tool_name() [invalid call: <first line>]`
          (pydantic-ai _wrap_error_as_retry — unknown tool / arg-validation 等)
      1b. ret None + retry absent → `⚙ tool_name() [no return captured]`
          (genuine orphan — tool_call_id mismatch; should not happen)
      2. is_tool_error → error single-line: `✗ tool_name(args) {fallback}`
      ...

    retry_lookup: 可选 {tool_call_id → RetryPromptPart} map. `format_cycle_output`
    构建后传入;既有 testsite 不传 → 默认 None → 走 1b 真 orphan 分支保持原行为.
    """
```

- [ ] **Step 3: orphan path 改两分支**

`src/cli/display.py:941-949` (替换 Task 3 落的单行 escape):

```python
# 改后
    retry_lookup = retry_lookup or {}

    for tcp in tool_calls:
        ret = returns_lookup.get(tcp.tool_call_id)
        if ret is None:
            retry = retry_lookup.get(tcp.tool_call_id)
            if retry is not None:
                # pydantic-ai 拒绝该 call (unknown tool / arg-validation 等;
                # 均经 _wrap_error_as_retry → RetryPromptPart 同一路径).
                # content 类型为 list[ErrorDetails] | str (messages.py:1321):
                #   ModelRetry     → content: str
                #   ValidationError → content: list[ErrorDetails]
                content = retry.content
                if isinstance(content, list):
                    # ErrorDetails 是 dict-like; 取前 3 条拼 loc + msg 单行
                    # (前 3 条已足够 LLM 自纠 + agent 不需对外暴露内部 type/url 字段)
                    first_line = "; ".join(
                        f"{'.'.join(map(str, e.get('loc', ())))}: {e.get('msg', '?')}"
                        for e in content[:3]
                    )[:100]
                else:
                    first_line = content.split("\n")[0][:100]
                lines.append(
                    f"  ✗ {escape(tcp.tool_name)}() "
                    f"{escape(f'[invalid call: {first_line}]')}"
                )
            else:
                # genuine orphan: tool_call_id mismatch — system bug, log + render
                logger.warning(
                    "tool_call_id mismatch for %s in cycle %s",
                    tcp.tool_name, cycle_id,
                )
                lines.append(
                    f"  ⚙ {escape(tcp.tool_name)}() "
                    f"{escape('[no return captured]')}"
                )
            continue
```

**注**: Task 3 的 B-1 escape 这里融入 1b 真 orphan 分支(同一行)。`(retry_lookup or {})` 已展开到 `retry_lookup = retry_lookup or {}` 单独一行,避免每个 cycle iteration 重复 `or {}` 开销。

- [ ] **Step 4: `format_cycle_output` 加 retry_lookup 构建 + 调用传参**

`src/cli/display.py:1066-1071`:

```python
# 改前
    # === Build tool_call_id → ToolReturnPart map ===
    tool_returns_lookup: dict = {}
    for msg in ctx.messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_returns_lookup[part.tool_call_id] = part

# 改后
    # === Build tool_call_id → ToolReturnPart / RetryPromptPart maps ===
    # RetryPromptPart 出现在 ModelRequest.parts (同位置不同类型 vs ToolReturnPart),
    # 由 pydantic-ai _wrap_error_as_retry 在 unknown tool / arg-validation 等 reject
    # 场景生成,需独立 capture 让 _render_action 可区分 retry-reject vs 真 orphan.
    tool_returns_lookup: dict = {}
    retry_lookup: dict = {}
    for msg in ctx.messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_returns_lookup[part.tool_call_id] = part
                elif isinstance(part, RetryPromptPart) and part.tool_call_id is not None:
                    retry_lookup[part.tool_call_id] = part
```

`src/cli/display.py:1087`:

```python
# 改前
        if tool_calls:
            lines.append(_render_action(tool_calls, tool_returns_lookup, ctx.cycle_id))

# 改后
        if tool_calls:
            lines.append(_render_action(
                tool_calls, tool_returns_lookup, ctx.cycle_id,
                retry_lookup=retry_lookup,
            ))
```

**注 1**: `RetryPromptPart.tool_call_id` 类型 declaration 是 `str` 但 dataclass default `field(default_factory=_generate_tool_call_id)` 总会有值。`None` 守卫是 belt-and-suspenders(pydantic-ai 未来变更不变 None 兼容)。

**注 2**: `format_cycle_output` 在 `ctx.messages is None` 时短路返回(line 1052-1063 forensic 路径),`retry_lookup` 构建只在 messages 有内容时执行,无额外冗余开销。

- [ ] **Step 5: 跑 Fix B-2 全测试**

```bash
python -m pytest tests/test_iter_alert_trigger_id_unknown_tool_render.py -v --tb=short
```

期望: 全 9 tests PASS。

- [ ] **Step 6: 跑 display.py 全 regression**

```bash
python -m pytest tests/test_display_cycle.py -v --tb=short
```

期望: 既有 callsite (9 处) 全 PASS,无 regression。

---

## Task 5: Full suite + commit

- [ ] **Step 1: 全套 pytest**

```bash
python -m pytest tests/ --tb=short -q
```

期望: `1931 passed, 9 skipped`(baseline 1922 + 9 新 tests)。若有 fail,debug 后再 commit;若有 fail 非本 iter 引入,标记 issue 候选 follow-up。

- [ ] **Step 2: Commit impl**

```bash
git add src/cli/app.py src/cli/display.py tests/test_iter_alert_trigger_id_unknown_tool_render.py
git commit -m "$(cat <<'EOF'
iter-alert-trigger-id-unknown-tool-render: impl

#2 Fix A — alert trigger prompt 加 alert_id (cli/app.py)
- 抽 module-level helper _format_price_level_alert_trigger(context) -> str
- 原 inline 拼接处改单行 helper 调用
- helper 输出携带 alert_id (8-char hex) + 移除 "your" 冗余代词

#1 Fix B — orphan path 修复 + LLM hallucination 可视化 (cli/display.py)
- B-1: orphan path [no return captured] literal 加 escape() (Rich markup strip 实证 bug)
- B-2: capture RetryPromptPart 进 retry_lookup 字典 (kwarg with default,9 个既有 testsite 0 改动)
- B-2: ✗ + [invalid call: <first line>] 渲染区分 pydantic-ai reject vs 真 orphan
- B-2: content list[ErrorDetails]|str 双形态分支处理 (ModelRetry str + ValidationError list)
- nomenclature: [invalid call:] 与 outcome='denied' 区隔

Spec: docs/superpowers/specs/2026-05-29-iter-alert-trigger-id-unknown-tool-render-design.md
Tests: 9 new (1922 baseline → 1931)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: direct-merge to main**

```bash
# 退出 worktree
# (ExitWorktree action: keep)

# 在 main 上 fast-forward merge
cd /Users/z/Z/TradeBot
git checkout main
git merge --ff-only worktree-iter-alert-trigger-id-unknown-tool-render
git log --oneline -3   # 验证 2 commits (spec + impl) on top of 6614fdf
```

- [ ] **Step 4: Memory anchor 更新**

Update `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_tradebot_status.md`:
- 加 "**iter-alert-trigger-id-unknown-tool-render / squash `<new-hash>` (2026-05-29, mini-iter direct-merge)**: ..."
- 更新 test count `1922 passed → 1931 passed`

---

## Task 6 (optional): post-fix sanity check 占位

per spec §7,若有 post-merge sim 跑完:
- grep `get_active_alerts` 在 alert-triggered cycles 内调率 (Fix A 验证)
- grep `✗ ... [invalid call:` 在 logs (Fix B 验证)

不阻塞 merge — sim 跑完再回头量化。

---

## 测试源码

`tests/test_iter_alert_trigger_id_unknown_tool_render.py` 完整内容:

```python
"""iter-alert-trigger-id-unknown-tool-render — Fix A (#2) + Fix B (#1).

Fix A: alert-triggered cycle's user prompt exposes `alert_id` for lifecycle joins.
       Helper `_format_price_level_alert_trigger` makes the format unit-testable.

Fix B: `_render_action` orphan path splits into:
  - retry present → ✗ rejected (pydantic-ai validation/unknown-tool reject)
  - retry absent → ⚙ [no return captured] (genuine orphan, escape()-protected)

content: list[ErrorDetails] | str dual-form handled per pydantic_ai/messages.py:1321.

Spec: docs/superpowers/specs/2026-05-29-iter-alert-trigger-id-unknown-tool-render-design.md
"""
from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart,
)
from rich.console import Console

from src.cli.display import _render_action


# === Fix A (#2): alert trigger prompt surfaces alert_id =========================


def test_format_price_level_alert_trigger_includes_alert_id():
    """Fix A: helper output carries alert_id + direction + price + reasoning."""
    from src.cli.app import _format_price_level_alert_trigger
    from src.integrations.exchange.base import PriceLevelAlertInfo

    context = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=76470.0, direction="above",
        current_price=76482.5,
        reasoning="Reclaim of 17:15 candle high — early warning before SL.",
        timestamp=1779800855603, alert_id="725cfc9f",
    )
    out = _format_price_level_alert_trigger(context)

    assert "id=725cfc9f" in out
    assert "above 76470.00" in out
    assert "76482.50" in out  # current_price
    assert "Reclaim of 17:15 candle high" in out
    assert "BTC/USDT:USDT" in out


def test_format_price_level_alert_trigger_drops_pronoun():
    """Fix A cosmetic: drop 'your' pronoun (prompt context already implies ownership).
    Avoids double-possessive noise: was '(your alert: above ...)', now '(alert id=... above ...)'.
    """
    from src.cli.app import _format_price_level_alert_trigger
    from src.integrations.exchange.base import PriceLevelAlertInfo

    context = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=76470.0, direction="above",
        current_price=76482.5, reasoning="x", timestamp=0, alert_id="abc12345",
    )
    out = _format_price_level_alert_trigger(context)

    assert "your alert" not in out
    assert "(alert id=" in out  # new form starts with `(alert id=`


# === Fix B-1 (#1): [no return captured] survives Rich markup =====================


def test_orphan_no_return_captured_survives_rich_markup():
    """Fix B-1 regression: orphan path renders [no return captured] as literal
    (Rich console doesn't strip the brackets as a markup tag).
    """
    calls = [ToolCallPart(tool_name="get_active_alert", args={}, tool_call_id="c1")]
    raw_out = _render_action(calls, returns_lookup={}, cycle_id="abc")

    buf = io.StringIO()
    Console(file=buf, no_color=True, width=120).print(raw_out)
    rendered = buf.getvalue()

    assert "[no return captured]" in rendered
    assert "get_active_alert" in rendered
    assert " ⚙ " in rendered


# === Fix B-2 (#1): RetryPromptPart → ✗ [invalid call: ...] =======================


def test_retry_prompt_renders_as_invalid_call():
    """Fix B-2 str path: ModelRetry / unknown-tool reject with `content: str`.
    Renders as ✗ + [invalid call: <first line>] (NOT ⚙ + [no return captured]).
    """
    calls = [ToolCallPart(tool_name="get_active_alert", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="get_active_alert", tool_call_id="c1",
        content=(
            "Unknown tool name: 'get_active_alert'. "
            "Available tools: 'get_active_alerts', 'get_position', 'get_market_data'"
        ),
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc",
        retry_lookup={"c1": retry},
    )

    assert "✗" in raw_out
    assert " ⚙ " not in raw_out
    assert "get_active_alert" in raw_out
    assert "[invalid call:" in raw_out
    assert "Unknown tool name" in raw_out
    assert "[no return captured]" not in raw_out


def test_retry_prompt_list_content_formats_loc_and_msg():
    """Fix B-2 list path: ValidationError reject with `content: list[ErrorDetails]`.
    Extracts loc + msg per error (≤3), avoiding ugly str(list) dict repr.
    """
    calls = [ToolCallPart(tool_name="open_position", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="open_position", tool_call_id="c1",
        content=[
            {"type": "missing", "loc": ("symbol",), "msg": "Field required", "input": {}},
            {"type": "int_parsing", "loc": ("amount",),
             "msg": "Input should be a valid integer", "input": "abc"},
        ],
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc",
        retry_lookup={"c1": retry},
    )

    assert "✗" in raw_out
    assert "open_position" in raw_out
    assert "symbol: Field required" in raw_out
    assert "amount: Input should be a valid integer" in raw_out
    # Avoid ugly str(list) — should NOT contain raw dict repr
    assert "{'type':" not in raw_out
    assert "'loc':" not in raw_out


def test_retry_prompt_str_first_line_capped_at_100_chars():
    """Fix B-2 str edge: long single-line content truncates at 100 chars."""
    long_content = "Validation failed: " + ("x" * 500)
    calls = [ToolCallPart(tool_name="bad_tool", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="bad_tool", tool_call_id="c1", content=long_content,
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc", retry_lookup={"c1": retry},
    )

    assert "Validation failed:" in raw_out
    # "Validation failed: " = 19 chars → 81 'x's fit in 100-char cap
    assert "x" * 81 in raw_out
    assert "x" * 82 not in raw_out


def test_retry_prompt_str_multiline_keeps_first_line_only():
    """Fix B-2 str edge: multiline content surfaces only first line in the orphan
    row; later lines (e.g. 'Available tools' enumeration) folded out."""
    multi_content = "First line summary.\nSecond line detail.\nThird line context."
    calls = [ToolCallPart(tool_name="bad_tool", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="bad_tool", tool_call_id="c1", content=multi_content,
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc", retry_lookup={"c1": retry},
    )

    assert "First line summary." in raw_out
    assert "Second line detail." not in raw_out
    assert "Third line context." not in raw_out


def test_retry_prompt_list_caps_at_3_errors():
    """Fix B-2 list edge: ≥ 4 ErrorDetails → only first 3 surface, avoids overlong row.
    """
    calls = [ToolCallPart(tool_name="multi_err", args={}, tool_call_id="c1")]
    retry = RetryPromptPart(
        tool_name="multi_err", tool_call_id="c1",
        content=[
            {"type": "missing", "loc": ("first",),  "msg": "msg_first"},
            {"type": "missing", "loc": ("second",), "msg": "msg_second"},
            {"type": "missing", "loc": ("third",),  "msg": "msg_third"},
            {"type": "missing", "loc": ("FOURTH",), "msg": "msg_FOURTH"},
            {"type": "missing", "loc": ("FIFTH",),  "msg": "msg_FIFTH"},
        ],
    )
    raw_out = _render_action(
        calls, returns_lookup={}, cycle_id="abc", retry_lookup={"c1": retry},
    )

    assert "first: msg_first" in raw_out
    assert "second: msg_second" in raw_out
    assert "third: msg_third" in raw_out
    assert "FOURTH" not in raw_out
    assert "FIFTH" not in raw_out


def test_format_cycle_output_captures_retry_prompt_part():
    """Fix B-2 integration: format_cycle_output builds retry_lookup from
    ModelRequest.parts and threads it to _render_action.
    """
    from src.cli.display import CycleRenderContext, format_cycle_output
    from src.cli.session_state import SessionStats

    tool_call = ToolCallPart(
        tool_name="get_active_alert", args={}, tool_call_id="c1",
    )
    retry = RetryPromptPart(
        tool_name="get_active_alert", tool_call_id="c1",
        content="Unknown tool name: 'get_active_alert'.",
    )
    messages = [
        ModelResponse(parts=[tool_call]),
        ModelRequest(parts=[retry]),
    ]

    ctx = CycleRenderContext(
        cycle_id="abcd1234",
        trigger_type="scheduled",
        trigger_context=None,
        state_snapshot=None,
        messages=messages,
        final_text="done",
        cycle_tokens=0,
        stats=SessionStats(),
        cache_hit_rate=None,
        cycle_started_at=datetime(2026, 5, 29, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 29, tzinfo=timezone.utc),
        forensic_reason=None,
    )

    out = format_cycle_output(ctx)

    assert "✗" in out
    assert "get_active_alert" in out
    assert "[invalid call: Unknown tool name" in out
```

---

## Rollback plan

mini-iter direct-merge 后若发现回归(unlikely 但准备好):

```bash
# 单 commit 回退(impl commit)
git revert HEAD~0   # 撤 impl
# 如需也撤 spec/plan doc:
git revert HEAD~1   # plan
git revert HEAD~2   # spec
```

或更彻底 reset(无 push 风险):

```bash
git reset --hard 6614fdf   # 回到 mini-iter 前 state
```

post-merge 若 sim 数据显示 Fix A 失效(per spec §7 gate),不需要回退 Fix B(它无副作用);仅 Fix A 走 follow-up 调研。
