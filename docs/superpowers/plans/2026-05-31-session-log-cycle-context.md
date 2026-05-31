# Session log — per-cycle Context section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 session log 每个 cycle 的 `Header` 框与第一段 `▾ Reasoning` 之间插入一个 `▾ Context (carried into this cycle)` 段，把 agent 本轮唤醒所携带的关键上下文（变量事件行 + 最近 3 条 prior summary 的 Stance/Thesis）摆在 reasoning 之前，使读 log 的人能就地评估"在这个上下文下本轮推理是否合理"。

**Architecture:** 整段从已存的单一字段 `agent_cycles.user_prompt_snapshot` 派生 —— 零新 DB 字段、渲染层零 DB 查询、零 replay。`user_prompt_snapshot` 透传进 `CycleRenderContext`（新增末尾默认 `None` 字段），在 `format_cycle_output` 内于 header 之后、forensic 短路之前调用一个 fail-isolated 的 `_render_context(...)`。该 helper 以注入块标记切两半：前半取 conditional/alert 的 verbatim 事件行（scheduled 省略），后半切成最多 3 个 per-cycle block（源 ASC → 反转为 newest-first），每条渲 Stance、仅最近一条加 Thesis、各以独占行 `(+N more)` 指示被省略字段数；解析失配 / terse / forensic body 走整条兜底。

**Tech Stack:** Python 3.12 · pydantic-ai message types · `rich.markup.escape` · 正则 position-slicing · pytest（TDD，断言渲染输出文本结构而非内部正则）。

**Spec:** `docs/superpowers/specs/2026-05-31-session-log-cycle-context-design.md`（§3 设计 / §5 降级 / §6 风险 / §8 测试策略）。
**Grounding:** `scripts/ground_cycle_context_render.py`（§2 实证复跑，4 种 marker 写法）。

---

## File Structure

- **`src/cli/display.py`**（渲染逻辑主改动 — 就近落在消费者层，避免 `display→app` 循环 import；app.py 仅 3 处 kwargs 透传，见下）
  - `CycleRenderContext` 新增字段 `user_prompt_snapshot: str | None = None`（末尾、带默认）。
  - 新增 Context 段常量 + 7 个 helper（`_split_wake_prompt` / `_extract_event_line` / `_clean_field` / `_truncate_with_marker` / `_extract_summary_fields` / `_parse_injected_summaries` / `_render_carried_block` / `_render_context`）。
  - `format_cycle_output` 在 header append 后、`messages is None` 短路前插入一处调用。
- **`src/cli/app.py`**（仅 3 处 kwargs 透传，无逻辑改动）
  - success（行 ~761）/ usage_limit forensic（行 ~599）/ retry_exhausted forensic（行 ~651）三处构造 `CycleRenderContext` 时传 `user_prompt_snapshot=user_prompt_snapshot_var`。
- **`tests/test_session_log_cycle_context.py`**（新建 — 本 iter 全部单元 + 集成 + drift-guard 测试）
- **`tests/test_display_cycle.py`**（改 `_make_ctx` helper 增 `user_prompt_snapshot` 形参）
- **`tests/test_p4_cycle_capture.py`**（既有 live-path 测试增 console 捕获断言，作为 app.py 透传 guard）

**任务依赖序**：Task 1（字段）→ Task 2-6（纯 helper，可独立单测）→ Task 7（`_render_context` + 接线 `format_cycle_output`，依赖 Task 1 字段 + Task 2-6 helper）→ Task 8（app.py 3 处透传，live-path guard，依赖 Task 7 已接线）→ Task 9（真实数据 fixture + round-trip drift-guard + 向后兼容审计）。

---

## Task 1: `CycleRenderContext.user_prompt_snapshot` 字段

**Files:**
- Modify: `src/cli/display.py:686-712`（dataclass 末尾加字段）
- Modify: `tests/test_display_cycle.py:886-919`（`_make_ctx` 增形参）
- Test: `tests/test_session_log_cycle_context.py`（新建）

**为何末尾 + 默认 None**：`CycleRenderContext` 是 `@dataclass(frozen=True)` 且现有 12 字段全无默认值；新字段若不带默认或不放末尾，现有 8 处 kw 构造点（app.py ×3 + tests ×5）会 `TypeError`。默认 `None` 同时是降级语义（§5：None → 整段省略）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_session_log_cycle_context.py`，写入文件头 + 第一个测试：

```python
"""session-log-cycle-context iter (2026-05-31) — Context 段渲染测试。

覆盖 spec §3 设计 / §5 降级 / §6 风险 / §8 测试策略。断言锚定渲染输出
文本结构（行为）而非内部正则。
"""
from __future__ import annotations

import pytest


def test_cycle_render_context_user_prompt_snapshot_defaults_none():
    """新字段默认 None（保现有构造点不 TypeError）；可显式赋值。"""
    from datetime import datetime, timezone
    from src.cli.display import CycleRenderContext
    from src.cli.session_state import SessionStats

    started = datetime(2026, 5, 31, 7, 35, 0, tzinfo=timezone.utc)
    # 不传 user_prompt_snapshot —— 应默认 None
    ctx = CycleRenderContext(
        cycle_id="06e9abcd", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"}, state_snapshot=None,
        messages=None, final_text=None, cycle_tokens=0,
        stats=SessionStats(), cache_hit_rate=None,
        cycle_started_at=started, cycle_ended_at=started,
        forensic_reason=None,
    )
    assert ctx.user_prompt_snapshot is None
    # 显式赋值
    ctx2 = CycleRenderContext(
        cycle_id="06e9abcd", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"}, state_snapshot=None,
        messages=None, final_text=None, cycle_tokens=0,
        stats=SessionStats(), cache_hit_rate=None,
        cycle_started_at=started, cycle_ended_at=started,
        forensic_reason=None, user_prompt_snapshot="hello",
    )
    assert ctx2.user_prompt_snapshot == "hello"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_log_cycle_context.py::test_cycle_render_context_user_prompt_snapshot_defaults_none -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'user_prompt_snapshot'`

- [ ] **Step 3: 加字段**

`src/cli/display.py`，在 `CycleRenderContext` 最后一个字段 `forensic_reason: str | None` 之后追加：

```python
    forensic_reason: str | None
    user_prompt_snapshot: str | None = None  # spec 2026-05-31: Context 段唯一数据源；None → 整段省略
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_session_log_cycle_context.py::test_cycle_render_context_user_prompt_snapshot_defaults_none -q`
Expected: PASS

- [ ] **Step 5: 改 `_make_ctx` 测试 helper 增形参**

`tests/test_display_cycle.py`，`_make_ctx` 形参表（行 886-899）在 `forensic_reason=None,` 后加一行：

```python
    forensic_reason=None,
    user_prompt_snapshot=None,
):
```

并在其 `return CycleRenderContext(...)`（行 912-919）的 `forensic_reason=forensic_reason,` 后加：

```python
        cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
        forensic_reason=forensic_reason,
        user_prompt_snapshot=user_prompt_snapshot,
    )
```

- [ ] **Step 6: 跑既有 display 测试确认向后兼容（默认 None 不破坏现有断言）**

Run: `python -m pytest tests/test_display_cycle.py -q`
Expected: PASS（全绿 — 现有测试不传 `user_prompt_snapshot` → 默认 None → 不渲 Context）

- [ ] **Step 7: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py tests/test_session_log_cycle_context.py
git commit -m "iter-session-log-cycle-context: CycleRenderContext.user_prompt_snapshot field (default None)"
```

---

## Task 2: `_split_wake_prompt` — 按注入标记切两半

**Files:**
- Modify: `src/cli/display.py`（在 `_render_reasoning` 之后、`_render_action` 之前新增 Context 段常量 + helper）
- Test: `tests/test_session_log_cycle_context.py`

注入标记常量须与 `app.py:333` 的 `header_top` 逐字一致。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_session_log_cycle_context.py`：

```python
def test_split_wake_prompt_with_marker():
    """有注入块 → 前半=唤醒 scaffold+事件行，后半=注入 summary 块（标记行被丢弃）。"""
    from src.cli.display import _split_wake_prompt
    snapshot = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 (alert id=934c above 73384.00 — x)\n\n"
        "Your prior cycle summaries (most recent N=3, from this session):\n\n"
        "[cycle 00f7abcd · alert · 2026-05-31 07:27 UTC (8 min ago) · 96 words]\n"
        "body here"
    )
    wake, summaries = _split_wake_prompt(snapshot)
    assert "PRICE LEVEL" in wake
    assert "Your prior cycle summaries" not in wake
    assert "Your prior cycle summaries" not in summaries  # 标记行本身已丢弃
    assert "[cycle 00f7abcd" in summaries


def test_split_wake_prompt_no_marker():
    """无注入块（首 cycle 无 prior）→ 后半为空字符串。"""
    from src.cli.display import _split_wake_prompt
    snapshot = (
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    wake, summaries = _split_wake_prompt(snapshot)
    assert wake == snapshot
    assert summaries == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k split_wake_prompt -q`
Expected: FAIL — `ImportError: cannot import name '_split_wake_prompt'`

- [ ] **Step 3: 实现常量 + helper**

`src/cli/display.py`，在 `_render_reasoning` 函数结束（行 919）之后插入：

```python
# === Context section (carried into cycle) — spec 2026-05-31 ===
#
# 整段从已存的 user_prompt_snapshot 派生（零新 DB 字段 / 渲染层零查询 / 零 replay）。
# format_cycle_output 在 header 后、forensic 短路前调用 _render_context（fail-isolated）。

# 与 app._render_recent_summaries 的 header_top 逐字一致（格式耦合，由 Task 9 round-trip drift-guard 兜底）
_SUMMARIES_MARKER = "Your prior cycle summaries (most recent N=3, from this session):"

# conditional/alert 唤醒切片里的变量事件文本前缀（app.py:489/524/528 三种）
_EVENT_PREFIXES = ("IMPORTANT EVENT", "PRICE ALERT", "PRICE LEVEL")

# 字段 marker 的 4 种 cosmetic 写法（均行首）：**(N) Field / (N) **Field / (N) Field / ### (N) Field
_FIELD_MARKER_RE = re.compile(r"(?m)^(?:#{1,6}\s*)?\**\s*\(([1-5])\)\s*")

# 字段名 header（persona.py:116/126 模板 `(N) Name — content` —— marker 后紧跟字段名）。
# _FIELD_MARKER_RE 只吃到 `(N) `，字段名仍留在 value 里（fields[1]="Stance — ..."）；
# render 须先剥它再 prepend 归一标签，否则双标签 `Stance — Stance — ...`。
# 字符类排除 — / – / :（不含 hyphen，避免吃掉内容里的 73,000-73,100）；≤40 字符内无分隔符 → 不剥离。
_FIELD_LABEL_RE = re.compile(r"^[^—–:\n]{1,40}[—–:]\s*")

# 注入块头两变体：valid `[cycle <id8> · <trig> · <utc> (<ago>) · <N> words]`
# / NULL-forensic `[cycle <id8> · <trig> · <utc> (<ago>)]`（无 `· N words`）。
# 捕获组 1 = id（8 hex），组 2 = ago 文本（去括号）。
_BLOCK_HEADER_RE = re.compile(
    r"\[cycle\s+([0-9a-fA-F]+)\s+·\s+[^·]+·\s+[^(]+\(([^)]+)\)"
    r"(?:\s+·\s+\d+\s+words)?\]"
)

# 长度安全网（spec §3.6）—— 实测均不触发，仅防病态长文 / 未来新写法落兜底
_CONTEXT_THESIS_CAP = 1500     # 最近一条 Thesis（实测 ④ max 1185）
_CONTEXT_EVENT_CAP = 500       # Woke-by 事件行（实测最长事件行 ~150c）
_CONTEXT_FALLBACK_CAP = 500    # 兜底 whole-block（尤其 earlier-slot，防整条长文跨 cycle 重复）


def _split_wake_prompt(snapshot: str) -> tuple[str, str]:
    """Split user_prompt_snapshot at the injected-summaries marker.

    Returns (wake_half, summaries_half). 标记缺失（首 cycle 无 prior）→
    summaries_half 为 ""。标记行本身被丢弃（不进任一半）。
    """
    idx = snapshot.find(_SUMMARIES_MARKER)
    if idx == -1:
        return snapshot, ""
    return snapshot[:idx], snapshot[idx + len(_SUMMARIES_MARKER):]
```

（`re` 与 `escape` 已在 `display.py:5` / `:19` import，无需新增 import。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k split_wake_prompt -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_session_log_cycle_context.py
git commit -m "iter-session-log-cycle-context: _split_wake_prompt + Context-section constants"
```

---

## Task 3: `_extract_event_line` — conditional/alert verbatim 事件行

**Files:**
- Modify: `src/cli/display.py`（接 `_split_wake_prompt` 之后）
- Test: `tests/test_session_log_cycle_context.py`

本 task 的 `_extract_event_line` 调用 `_truncate_with_marker`，故**在本 task Step 3 一并定义 `_truncate_with_marker`**（4 行纯函数）；它后续也被 Task 6 `_render_carried_block` 复用，不重复定义。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_session_log_cycle_context.py`：

```python
def test_extract_event_line_scheduled_returns_none():
    """scheduled → 事件行整体省略（spec §3.3）。"""
    from src.cli.display import _extract_event_line
    wake = (
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    assert _extract_event_line(wake, "scheduled") is None


def test_extract_event_line_price_level_verbatim():
    """price-level alert → 保 alert id + reasoning，空白 collapse。"""
    from src.cli.display import _extract_event_line
    wake = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 "
        "(alert id=934cfd above 73384.00 — MA20 reclaim: bounce)"
    )
    line = _extract_event_line(wake, "alert")
    assert line is not None
    assert line.startswith("PRICE LEVEL:")
    assert "alert id=934cfd" in line
    assert "MA20 reclaim: bounce" in line
    assert "You have been woken up" not in line  # scaffold 已剥离


def test_extract_event_line_conditional_fill():
    """conditional fill → 保 fee/PnL 段。"""
    from src.cli.display import _extract_event_line
    wake = (
        "You have been woken up by a conditional trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "IMPORTANT EVENT: take_profit triggered — BTC/USDT:USDT 0.265 @ 75350.0, "
        "Fee: -2.10 USDT, PnL: +12.40 USDT (gross) / +8.20 USDT (this fill, equiv-round-trip)"
    )
    line = _extract_event_line(wake, "conditional")
    assert line.startswith("IMPORTANT EVENT: take_profit triggered")
    assert "PnL: +12.40 USDT (gross)" in line


def test_extract_event_line_no_known_prefix_returns_none():
    """alert 但无任何已知前缀（识别不到）→ None（不渲 Woke by）。"""
    from src.cli.display import _extract_event_line
    wake = "You have been woken up by a alert trigger.\nTrading pair: X | Timeframe: 5m\n..."
    assert _extract_event_line(wake, "alert") is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k extract_event_line -q`
Expected: FAIL — `ImportError: cannot import name '_extract_event_line'`

- [ ] **Step 3: 实现**

`src/cli/display.py`，接 `_split_wake_prompt` 之后插入：

```python
def _truncate_with_marker(text: str, max_chars: int) -> str:
    """Hard-truncate to max_chars + ASCII ' ... [+N chars]'（与 _render_reasoning 一致）。"""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f" ... [+{len(text) - max_chars} chars]"


def _extract_event_line(wake_half: str, trigger_type: str) -> str | None:
    """Extract the verbatim variable event text from the wake prompt (spec §3.3).

    scheduled → None（纯样板、与 Header Trigger 重叠，整体省略）。
    conditional/alert → 以已知前缀（IMPORTANT EVENT / PRICE ALERT / PRICE LEVEL）
    锚定到 wake_half 末尾，原样保留（alert id / reasoning / fee / PnL / round-trip），
    collapse 空白 + 上限截断。识别不到前缀 → None。
    """
    if trigger_type == "scheduled":
        return None
    positions = [p for p in (wake_half.find(pre) for pre in _EVENT_PREFIXES) if p != -1]
    if not positions:
        return None
    event = re.sub(r"\s+", " ", wake_half[min(positions):]).strip()
    return _truncate_with_marker(event, _CONTEXT_EVENT_CAP)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k extract_event_line -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_session_log_cycle_context.py
git commit -m "iter-session-log-cycle-context: _extract_event_line + _truncate_with_marker"
```

---

## Task 4: `_clean_field` + `_extract_summary_fields` — 字段提取 + 清洗

**Files:**
- Modify: `src/cli/display.py`（接 `_extract_event_line` 之后）
- Test: `tests/test_session_log_cycle_context.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_session_log_cycle_context.py`：

```python
def test_clean_field_strips_bold_and_collapses_whitespace():
    from src.cli.display import _clean_field
    raw = "Flat. MA20 reclaim **confirmed**\n  by 07:30 close;   bearish bias tempered."
    cleaned = _clean_field(raw)
    assert "**" not in cleaned
    assert "\n" not in cleaned
    assert "  " not in cleaned  # 多空格已 collapse
    assert cleaned == "Flat. MA20 reclaim confirmed by 07:30 close; bearish bias tempered."


@pytest.mark.parametrize("marker", [
    "**(1) Stance** —",      # **(N) Field
    "(1) **Stance** —",      # (N) **Field
    "(1) Stance —",          # (N) Field
    "### (1) Stance —",      # ### (N) Field (markdown heading)
])
def test_extract_summary_fields_four_marker_styles(marker):
    """4 种 cosmetic 写法均能定位 ①④。"""
    from src.cli.display import _extract_summary_fields
    body = (
        f"{marker} Flat near MA20.\n"
        "**(2) Active commitments** — alert above 73,384.\n"
        "**(3) This cycle delta** — updated alert.\n"
        "**(4) Thesis & invalidation** — bearish macro; invalidation > 74,200.\n"
        "**(5) Watch list** — 74,200 resistance."
    )
    fields = _extract_summary_fields(body)
    assert 1 in fields and 4 in fields
    assert "Flat near MA20" in fields[1]
    assert "bearish macro" in fields[4]
    assert len(fields) == 5


def test_extract_summary_fields_terse_returns_empty():
    """terse 一句话（无 (N) marker）→ {}（caller 走整条兜底）。"""
    from src.cli.display import _extract_summary_fields
    assert _extract_summary_fields("Done. Next wake in 30 min.") == {}


def test_extract_summary_fields_degraded_only_1_and_4():
    """退化：仅 ①④ 在（缺 ②③⑤）→ ④ 以 block 末兜底定界。"""
    from src.cli.display import _extract_summary_fields
    body = "(1) Stance — flat.\n(4) Thesis — bearish; invalidation > 74,200."
    fields = _extract_summary_fields(body)
    assert set(fields) == {1, 4}
    assert "bearish; invalidation > 74,200" in fields[4]


def test_strip_field_label_removes_name_header():
    """剥 '<FieldName> — ' header（_extract_summary_fields 保留了字段名，render 前须剥）。

    覆盖审查发现的双标签根因：fields[1]='Stance — ...'，若不剥则 render 出 'Stance — Stance — ...'。
    """
    from src.cli.display import _strip_field_label
    # ① em-dash 分隔
    assert _strip_field_label("Stance — flat near MA20.") == "flat near MA20."
    # ④ 长字段名 + 内容含 colon（colon 在 em-dash 之后，不被误剥）
    assert (_strip_field_label("Thesis & invalidation — bearish; conviction: low")
            == "bearish; conviction: low")
    # colon 分隔写法
    assert _strip_field_label("Stance: flat") == "flat"
    # 内容含 hyphen 不被吃（hyphen 不在分隔符类）
    assert _strip_field_label("Stance — range 73,000-73,100") == "range 73,000-73,100"
    # 无 name—sep 前缀（≤40 内无分隔符）→ 原样返回（降级，不吃内容）
    raw = "flat near MA20 with no leading label or separator anywhere in here"
    assert _strip_field_label(raw) == raw
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k "clean_field or extract_summary_fields or strip_field_label" -q`
Expected: FAIL — `ImportError: cannot import name '_clean_field'`

- [ ] **Step 3: 实现**

`src/cli/display.py`，接 `_extract_event_line` 之后插入：

```python
def _clean_field(text: str) -> str:
    """Strip markdown bold + collapse internal whitespace（log 渲 plain text）。"""
    return re.sub(r"\s+", " ", text.replace("**", "")).strip()


def _strip_field_label(text: str) -> str:
    """Remove a leading '<FieldName> — ' header（persona `(N) Name — content`）。

    _extract_summary_fields 切片只去 `(N) ` marker，字段名（Stance / Thesis &
    invalidation …）仍留在 value 开头。render 须先剥它再 prepend 归一标签
    `Stance —` / `Thesis —`（④ 缩写归一同时落地），否则双标签
    `Stance — Stance — ...`。≤40 字符内无 — / – / : 分隔符 → 原样返回（降级，不吃内容）。
    """
    return _FIELD_LABEL_RE.sub("", text, count=1)


def _extract_summary_fields(body: str) -> dict[int, str]:
    """Position-slice a summary body into {field_num: raw_content}（spec §3.4）。

    容忍 4 种 cosmetic marker 写法（_FIELD_MARKER_RE）。按相邻 marker 位置切片，
    每段以"下一个 marker 或 block 末"定界（故仅 ①④ 在的退化情形 ④ 自动以末尾兜底）。
    切片保留字段名（`Stance — ...`）—— render 经 _strip_field_label 去名后再 prepend
    归一标签。无任何 (N) marker（terse / forensic system body）→ {}（caller 走整条兜底）。
    """
    marks = [(m.start(), int(m.group(1)), m.end()) for m in _FIELD_MARKER_RE.finditer(body)]
    if not marks:
        return {}
    out: dict[int, str] = {}
    for i, (_, num, end) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        out[num] = body[end:nxt].strip()
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k "clean_field or extract_summary_fields or strip_field_label" -q`
Expected: PASS（8 passed — 含 4 参数化 + strip_field_label）

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_session_log_cycle_context.py
git commit -m "iter-session-log-cycle-context: _clean_field + _strip_field_label + _extract_summary_fields"
```

---

## Task 5: `_parse_injected_summaries` — 切块 + 反转 + 两变体块头

**Files:**
- Modify: `src/cli/display.py`（接 `_extract_summary_fields` 之后）
- Test: `tests/test_session_log_cycle_context.py`

注入块源序为 ASC（最旧在前，`app._render_recent_summaries`）；Context 要 newest-first，故解析后反转。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_session_log_cycle_context.py`：

```python
def _injected_block_asc() -> str:
    """模拟 app._render_recent_summaries 产出：ASC（最旧在前），3 条，含两块头变体。"""
    return (
        "\n\n"
        "[cycle 824e2233 · conditional · 2026-05-31 07:00 UTC (35 min ago) · 91 words]\n"
        "**(1) Stance** — flat; cascade compressing.\n"
        "**(4) Thesis & invalidation** — bearish; invalidation > 74,200.\n\n"
        "[cycle 47d5ef01 · usage_limit_exceeded · 2026-05-31 07:01 UTC (34 min ago)]\n"  # NULL 变体：无 · N words
        "⚠️ The previous cycle did not complete normally. Please verify state.\n\n"
        "[cycle 00f7abcd · alert · 2026-05-31 07:27 UTC (8 min ago) · 96 words]\n"
        "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
        "**(4) Thesis & invalidation** — bearish macro intact."
    )


def test_parse_injected_summaries_reversed_newest_first():
    """源 ASC → 反转为 newest-first（00f7 最新在前，824e 最旧在后）。"""
    from src.cli.display import _parse_injected_summaries
    blocks = _parse_injected_summaries(_injected_block_asc())
    assert len(blocks) == 3
    ids = [b[0] for b in blocks]
    assert ids == ["00f7", "47d5", "824e"]  # newest-first，且 id8 → id4


def test_parse_injected_summaries_two_header_variants_ago():
    """两块头变体（有/无 · N words）均能取 id+ago（去括号）。"""
    from src.cli.display import _parse_injected_summaries
    blocks = _parse_injected_summaries(_injected_block_asc())
    by_id = {b[0]: b for b in blocks}
    assert by_id["00f7"][1] == "8 min ago"      # valid 变体（有 · 96 words）
    assert by_id["47d5"][1] == "34 min ago"     # NULL 变体（无 · N words）
    # body 切片正确（含字段标记 / forensic 系统文本）
    assert "MA20 reclaim confirmed" in by_id["00f7"][2]
    assert "did not complete normally" in by_id["47d5"][2]


def test_parse_injected_summaries_empty_no_blocks():
    from src.cli.display import _parse_injected_summaries
    assert _parse_injected_summaries("") == []
    assert _parse_injected_summaries("no block header here") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k parse_injected_summaries -q`
Expected: FAIL — `ImportError: cannot import name '_parse_injected_summaries'`

- [ ] **Step 3: 实现**

`src/cli/display.py`，接 `_extract_summary_fields` 之后插入：

```python
def _parse_injected_summaries(summaries_half: str) -> list[tuple[str, str, str]]:
    """Slice the injected block into per-cycle (id4, ago, body), newest-first（spec §3.4）。

    源序 ASC（最旧在前，app._render_recent_summaries）→ 反转为 newest-first 对齐
    Header 'Cycle' 阅读序。块头两变体（有/无 '· N words'）均容忍。无块头 → []。
    id 由块头 id8 再切 4 字符；ago 去括号。
    """
    marks = [
        (m.start(), m.group(1)[:4], m.group(2).strip(), m.end())
        for m in _BLOCK_HEADER_RE.finditer(summaries_half)
    ]
    if not marks:
        return []
    blocks: list[tuple[str, str, str]] = []
    for i, (_, id4, ago, end) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(summaries_half)
        blocks.append((id4, ago, summaries_half[end:nxt].strip()))
    blocks.reverse()  # ASC → newest-first
    return blocks
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k parse_injected_summaries -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_session_log_cycle_context.py
git commit -m "iter-session-log-cycle-context: _parse_injected_summaries (ASC->newest-first, 2 header variants)"
```

---

## Task 6: `_render_carried_block` — Stance 全渲 + Thesis 仅最近 + `(+N more)`

**Files:**
- Modify: `src/cli/display.py`（接 `_parse_injected_summaries` 之后）
- Test: `tests/test_session_log_cycle_context.py`

渲染规则优先级：**兜底 > 字段渲染**。`(+N more)` 独占行，`N = len(fields) − rendered`（动态：⑤Watch 常缺 → N 自动反映实存）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_session_log_cycle_context.py`：

```python
_FULL5 = (
    "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
    "**(2) Active commitments** — alert above 73,384.\n"
    "**(3) This cycle delta** — updated alert.\n"
    "**(4) Thesis & invalidation** — bearish macro intact; invalidation > 74,200.\n"
    "**(5) Watch list** — 74,200 resistance."
)
_NO_WATCH4 = (
    "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
    "**(2) Active commitments** — alert above 73,384.\n"
    "**(3) This cycle delta** — updated alert.\n"
    "**(4) Thesis & invalidation** — bearish macro intact; invalidation > 74,200."
)


def test_render_carried_block_newest_stance_and_thesis():
    """最近一条 → Stance + Thesis；(+N more) = 5 − 2 = 3。

    断言用精确整行（`in lines`），catch 审查发现的双标签：buggy 实现产出
    '      Stance — Stance — flat...' / '      Thesis — Thesis & invalidation — ...'
    与下面精确行不等 → 红。
    """
    from src.cli.display import _render_carried_block
    lines = _render_carried_block("00f7", "8 min ago", _FULL5, is_newest=True)
    assert "    00f7 · 8 min ago" in lines
    # 单标签、字段名已剥 —— 精确整行（双标签会变成另一行字符串 → 不在 lines 里）
    assert "      Stance — flat; MA20 reclaim confirmed." in lines
    assert "      Thesis — bearish macro intact; invalidation > 74,200." in lines
    # (+N more) 独占行
    assert "      (+3 more)" in lines


def test_render_carried_block_earlier_stance_only():
    """较早一条 → 仅 Stance，无 Thesis；(+N more) = 5 − 1 = 4。"""
    from src.cli.display import _render_carried_block
    lines = _render_carried_block("47d5", "34 min ago", _FULL5, is_newest=False)
    assert "      Stance — flat; MA20 reclaim confirmed." in lines
    assert not any(line.lstrip().startswith("Thesis —") for line in lines)
    assert "      (+4 more)" in lines


def test_render_carried_block_n_more_dynamic_no_watch():
    """缺 ⑤Watch（4 字段）→ N 动态减 1：newest 4−2=2，earlier 4−1=3。"""
    from src.cli.display import _render_carried_block
    newest = _render_carried_block("00f7", "8 min ago", _NO_WATCH4, is_newest=True)
    earlier = _render_carried_block("47d5", "34 min ago", _NO_WATCH4, is_newest=False)
    assert "      (+2 more)" in newest
    assert "      (+3 more)" in earlier


def test_render_carried_block_fallback_terse_no_labels():
    """terse / 无 ①④ → 整条兜底：无 Stance/Thesis 标签、无 (+N more)。"""
    from src.cli.display import _render_carried_block
    lines = _render_carried_block("824e", "35 min ago", "Done. Next wake in 30 min.", is_newest=True)
    text = "\n".join(lines)
    assert "Done. Next wake in 30 min." in text
    assert "Stance —" not in text
    assert "Thesis —" not in text
    assert "more)" not in text


def test_render_carried_block_newest_fallback_when_self_terse():
    """最近一条自身落兜底（无 ①④）→ 同样整条兜底（优先级 兜底 > 字段）。"""
    from src.cli.display import _render_carried_block
    lines = _render_carried_block("00f7", "8 min ago", "Holding. No change.", is_newest=True)
    text = "\n".join(lines)
    assert "Holding. No change." in text
    assert "Stance —" not in text and "more)" not in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k render_carried_block -q`
Expected: FAIL — `ImportError: cannot import name '_render_carried_block'`

- [ ] **Step 3: 实现**

`src/cli/display.py`，接 `_parse_injected_summaries` 之后插入：

```python
def _render_carried_block(id4: str, ago: str, body: str, is_newest: bool) -> list[str]:
    """Render one carried-cycle block → indented lines（spec §3.4）。

    结构化路径（①④ 均可定位）—— 字段名经 _strip_field_label 剥离后 prepend 归一标签：
        <id4> · <ago>
          Stance — <① 去名内容>
          Thesis — <④ 去名内容>        # 仅 is_newest（④ Thesis & invalidation 归一为 Thesis）
          (+N more)                    # 独占行，N = len(fields) − rendered
    兜底路径（无 ①④ — terse / forensic body，含 is_newest）—— 不剥标签（无字段名可剥）：
        <id4> · <ago>
          <cleaned whole body, capped>
    """
    out = [f"    {id4} · {ago}"]
    fields = _extract_summary_fields(body)
    if 1 in fields and 4 in fields:
        rendered = 1
        stance = _strip_field_label(_clean_field(fields[1]))
        out.append(f"      Stance — {escape(stance)}")
        if is_newest:
            thesis = _truncate_with_marker(
                _strip_field_label(_clean_field(fields[4])), _CONTEXT_THESIS_CAP,
            )
            out.append(f"      Thesis — {escape(thesis)}")
            rendered = 2
        n_more = len(fields) - rendered
        if n_more > 0:
            out.append(f"      (+{n_more} more)")
    else:
        whole = _truncate_with_marker(_clean_field(body), _CONTEXT_FALLBACK_CAP)
        out.append(f"      {escape(whole)}")
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k render_carried_block -q`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_session_log_cycle_context.py
git commit -m "iter-session-log-cycle-context: _render_carried_block (stance-all + thesis-newest + dynamic (+N more))"
```

---

## Task 7: `_render_context` 组装 + 接线 `format_cycle_output`

**Files:**
- Modify: `src/cli/display.py`（接 `_render_carried_block` 之后新增 `_render_context`；并改 `format_cycle_output` 行 1079-1084 之后插一处调用）
- Test: `tests/test_session_log_cycle_context.py`

依赖 Task 1（字段）+ Task 2/3/5/6（helper）。测试经 `format_cycle_output(_make_ctx(user_prompt_snapshot=..., ...))` 断言 Context 段位置与内容。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_session_log_cycle_context.py`（顶部加一个本地 `_make_ctx` 引用 helper —— 复用 test_display_cycle 的构造模式）：

```python
def _ctx(trigger_type, user_prompt_snapshot, messages=None, final_text="Hold."):
    """构造一个带 user_prompt_snapshot 的 success-path ctx（messages 给最小非 None 值触发正常渲染路径）。"""
    from datetime import datetime, timezone, timedelta
    from src.cli.display import CycleRenderContext
    from src.cli.session_state import SessionStats
    started = datetime(2026, 5, 31, 7, 35, 0, tzinfo=timezone.utc)
    return CycleRenderContext(
        cycle_id="06e9abcd", trigger_type=trigger_type,
        trigger_context={"type": "scheduled_tick"}, state_snapshot=None,
        messages=messages, final_text=final_text, cycle_tokens=1000,
        stats=SessionStats(), cache_hit_rate=90.0,
        cycle_started_at=started, cycle_ended_at=started + timedelta(seconds=3),
        forensic_reason=None, user_prompt_snapshot=user_prompt_snapshot,
    )


_ALERT_SNAPSHOT = (
    "You have been woken up by a alert trigger.\n"
    "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
    "Assess the situation and decide what to do.\n\n"
    "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 (alert id=934cfd above 73384.00 — MA20 reclaim)\n\n"
    "Your prior cycle summaries (most recent N=3, from this session):\n\n"
    "[cycle 824e2233 · conditional · 2026-05-31 07:00 UTC (35 min ago) · 91 words]\n"
    "**(1) Stance** — flat; cascade compressing.\n"
    "**(2) Active commitments** — none.\n"
    "**(3) This cycle delta** — closed short.\n"
    "**(4) Thesis & invalidation** — bearish; invalidation > 74,200.\n"
    "**(5) Watch list** — 73,000 support.\n\n"
    "[cycle 00f7abcd · alert · 2026-05-31 07:27 UTC (8 min ago) · 96 words]\n"
    "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
    "**(2) Active commitments** — alert above 73,384.\n"
    "**(3) This cycle delta** — updated alert.\n"
    "**(4) Thesis & invalidation** — bearish macro intact; invalidation > 74,200.\n"
    "**(5) Watch list** — 74,200 resistance."
)


def test_render_context_section_present_between_header_and_reasoning():
    """Context 段在 Header 之后、第一段 Reasoning 之前。"""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Assess."], tool_call_segments=[[]], final_text="Hold.",
    )
    out = format_cycle_output(_ctx("alert", _ALERT_SNAPSHOT, messages=msgs))
    ctx_idx = out.find("▾ Context (carried into this cycle)")
    header_idx = out.find("Cycle 06e9")
    reasoning_idx = out.find("▾ Reasoning")
    assert header_idx >= 0 and ctx_idx > header_idx, "Context 须在 Header 之后"
    assert reasoning_idx > ctx_idx, "Context 须在第一段 Reasoning 之前"
    # Woke by（alert 事件行）
    assert "Woke by — PRICE LEVEL:" in out
    assert "alert id=934cfd" in out
    # Carried thesis newest-first：00f7 在 824e 之前
    assert out.find("00f7 · 8 min ago") < out.find("824e · 35 min ago")
    # 最近一条有 Thesis，较早只有 Stance
    assert "Thesis — bearish macro intact" in out      # 00f7（newest）
    assert "(+3 more)" in out                            # newest 5−2
    assert "(+4 more)" in out                            # earlier 5−1


def test_render_context_scheduled_omits_woke_by():
    """scheduled → 无 Woke by，直接从 Carried thesis 起。"""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    snap = _ALERT_SNAPSHOT.replace(
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 (alert id=934cfd above 73384.00 — MA20 reclaim)\n\n",
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n",
    )
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("scheduled", snap, messages=msgs))
    assert "▾ Context (carried into this cycle)" in out
    assert "Woke by" not in out
    assert "Carried thesis" in out


def test_render_context_none_snapshot_omits_section():
    """user_prompt_snapshot=None → 整段省略（spec §5）。"""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("scheduled", None, messages=msgs))
    assert "▾ Context" not in out


def test_render_context_scheduled_first_cycle_omits_section():
    """scheduled 首 cycle（无 Woke by 无 prior）→ 整段省略。"""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    snap = (
        "You have been woken up by a scheduled trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do."
    )
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("scheduled", snap, messages=msgs))
    assert "▾ Context" not in out


def test_render_context_alert_first_cycle_woke_by_only():
    """conditional/alert 首 cycle（有 Woke by、无 prior）→ 只渲 Woke by。"""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    snap = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 5m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 73384.00 (alert id=934cfd above 73384.00 — x)"
    )
    msgs = build_cycle_messages(thinking_segments=["x."], tool_call_segments=[[]], final_text="Hold.")
    out = format_cycle_output(_ctx("alert", snap, messages=msgs))
    assert "Woke by — PRICE LEVEL:" in out
    assert "Carried thesis" not in out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k render_context -q`
Expected: FAIL — `AttributeError`/`ImportError`（`_render_context` 未定义）或 `▾ Context` 不在输出中

- [ ] **Step 3: 实现 `_render_context`**

`src/cli/display.py`，接 `_render_carried_block` 之后插入：

```python
def _render_context(user_prompt_snapshot: str | None, trigger_type: str) -> str:
    """Render the '▾ Context (carried into this cycle)' section (spec §3).

    数据源 = user_prompt_snapshot（agent 本轮实读那份）。无可展示内容
    （None / scheduled 首 cycle 无 prior）→ ""（caller 跳过）。fail-isolated：
    任何解析异常降级为空 / 仅 Woke by，绝不阻断整 cycle 渲染（spec §5）。
    """
    if not user_prompt_snapshot:
        return ""
    try:
        wake_half, summaries_half = _split_wake_prompt(user_prompt_snapshot)
        event_line = _extract_event_line(wake_half, trigger_type)
        blocks = _parse_injected_summaries(summaries_half)

        lines: list[str] = []
        if event_line:
            lines.append(f"  Woke by — {escape(event_line)}")
        if blocks:
            n = len(blocks)
            lines.append(
                f"  Carried thesis — last {n} cycle{'s' if n > 1 else ''} (newest first):"
            )
            for slot, (id4, ago, body) in enumerate(blocks):
                lines.extend(_render_carried_block(id4, ago, body, is_newest=(slot == 0)))

        if not lines:
            return ""
        return "\n▾ Context (carried into this cycle)\n" + "\n".join(lines)
    except Exception:
        logger.warning("Context section render failed; omitting", exc_info=True)
        return ""
```

- [ ] **Step 4: 接线 `format_cycle_output`**

`src/cli/display.py`，`format_cycle_output` 内 `lines = [_render_header(...)]`（行 1079-1083 闭合）之后、`# === Forensic / retry-exhausted 短路 ===` 注释（行 1085）之前，插入：

```python
    )]

    # spec 2026-05-31: Context 段插在 Header 后、Reasoning/forensic 短路前
    # （success + forensic 两路径共用此处，因 user_prompt_snapshot 在两路径均已落库）
    context_section = _render_context(ctx.user_prompt_snapshot, ctx.trigger_type)
    if context_section:
        lines.append(context_section)

    # === Forensic / retry-exhausted 短路 ===
    if ctx.messages is None:
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k render_context -q`
Expected: PASS（5 passed）

- [ ] **Step 6: 跑全文件 + 既有 display 测试确认无回归**

Run: `python -m pytest tests/test_session_log_cycle_context.py tests/test_display_cycle.py -q`
Expected: PASS（全绿）

- [ ] **Step 7: Commit**

```bash
git add src/cli/display.py tests/test_session_log_cycle_context.py
git commit -m "iter-session-log-cycle-context: _render_context + wire into format_cycle_output"
```

---

## Task 8: app.py 透传 `user_prompt_snapshot_var`（3 处）+ live-path guard

**Files:**
- Modify: `src/cli/app.py:599-606`（usage_limit forensic）/ `:651-658`（retry_exhausted forensic）/ `:761-768`（success）
- Modify: `tests/test_p4_cycle_capture.py`（既有 live-path 测试增 console 捕获断言）
- Test: `tests/test_p4_cycle_capture.py`

`user_prompt_snapshot_var` 在 retry loop 之前定义（`app.py:545`），3 路径均可取。此前不传 → 字段默认 None → live Context 永不渲染。本 task 的 guard 测试经真实 `run_agent_cycle` + console 捕获，证明透传生效（若漏传则 Context 缺失、测试红）。

- [ ] **Step 1: 写失败测试（live-path guard）**

`tests/test_p4_cycle_capture.py`，照搬既有 `test_cycle_captures_user_prompt_snapshot_happy`（行 74）的 mock 模式（**模块级** `_make_deps_engine_with_capture_mocks` / `_mock_usage_legacy` / `MagicMock` / `UsageLimitExceeded` 均已在文件顶部，无需新 import；裸 `async def`，靠 `asyncio_mode="auto"`，不加 `@pytest.mark.asyncio`），追加（success path + alert trigger，使 Woke by 必现）：

```python
async def test_cycle_console_renders_context_section_happy():
    """app.py success 构造点透传 user_prompt_snapshot → console 渲出 ▾ Context + Woke by。

    drift-guard：若 app.py 漏传 user_prompt_snapshot，字段默认 None → 无 Context 段 → 红。
    """
    import io
    from rich.console import Console
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.integrations.exchange.base import PriceLevelAlertInfo

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-ctx-happy")

    async def mock_run(prompt, **kwargs):
        result = MagicMock()
        result.usage = lambda: _mock_usage_legacy(1000)   # 全 token 属性为 int，避 commit 崩
        result.new_messages = lambda: []
        result.output = "**(1) Stance** — flat.\n**(4) Thesis & invalidation** — bearish."
        return result

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"

    buf = io.StringIO()
    console = Console(file=buf, width=120, no_color=True)
    alert = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=73384.0, direction="above",
        current_price=73384.0, reasoning="MA20 reclaim", timestamp=1746098096000,
        alert_id="934cfd12",
    )
    await run_agent_cycle(
        agent, deps, "alert", TokenBudget(daily_max=1_000_000), engine,
        context=alert, console=console, model="test-model",
    )
    out = buf.getvalue()
    assert "▾ Context (carried into this cycle)" in out
    assert "Woke by — PRICE LEVEL:" in out
    assert "alert id=934cfd12" in out
```

> **关键点（审查修正）**：① deps/engine 经模块级 async helper `_make_deps_engine_with_capture_mocks(session_id)` 解包元组，**非** pytest fixture。② usage mock 必须用文件内 `_mock_usage_legacy()`（把 `input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_write_tokens` 全设为 int）—— 否则 app.py:679-682 读到 auto-`MagicMock` 写进 `AgentCycle` Integer 列，`session.commit()` 报错（不是干净 fail→pass）。③ `PriceLevelAlertInfo`（`base.py:382` dataclass）7 个必填字段全给，含 `timestamp`。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_p4_cycle_capture.py::test_cycle_console_renders_context_section_happy -q`
Expected: FAIL — 输出不含 `▾ Context`（app.py 尚未透传，字段默认 None）

- [ ] **Step 3: 透传 success 构造点**

`src/cli/app.py:761-768`，success 路径 `CycleRenderContext(...)` 的 `forensic_reason=None,` 之后加：

```python
            cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
            forensic_reason=None,
            user_prompt_snapshot=user_prompt_snapshot_var,
        )
```

- [ ] **Step 4: 透传两 forensic 构造点**

`src/cli/app.py:599-606`（usage_limit），`forensic_reason="usage_limit_exceeded",` 之后加 `user_prompt_snapshot=user_prompt_snapshot_var,`：

```python
                    cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
                    forensic_reason="usage_limit_exceeded",
                    user_prompt_snapshot=user_prompt_snapshot_var,
                )
```

`src/cli/app.py:651-658`（retry_exhausted），`forensic_reason=f"aborted: {err_class}: {err_msg}",` 之后加：

```python
                        cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
                        forensic_reason=f"aborted: {err_class}: {err_msg}",
                        user_prompt_snapshot=user_prompt_snapshot_var,
                    )
```

- [ ] **Step 5: 写 forensic live-path guard 测试**

`tests/test_p4_cycle_capture.py`，照搬既有 `test_cycle_captures_user_prompt_snapshot_usage_limit`（行 114）的 `raise UsageLimitExceeded` 模式（`UsageLimitExceeded` 已在文件 `pydantic_ai.usage` import，且既有测试已证它被 app.py:565 handler 捕获 → 走 usage_limit forensic 短路），追加（spec §5：forensic 短路路径也渲 Context）：

```python
async def test_cycle_console_renders_context_on_forensic():
    """usage_limit forensic 短路路径透传 user_prompt_snapshot → console 渲出 Woke by。"""
    import io
    from rich.console import Console
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.integrations.exchange.base import PriceLevelAlertInfo

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-ctx-forensic")

    async def mock_run(prompt, **kwargs):
        raise UsageLimitExceeded("simulated token cap")

    agent = MagicMock()
    agent.run = mock_run
    agent.model = "test-model"

    buf = io.StringIO()
    console = Console(file=buf, width=120, no_color=True)
    alert = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=73384.0, direction="above",
        current_price=73384.0, reasoning="MA20 reclaim", timestamp=1746098096000,
        alert_id="934cfd12",
    )
    await run_agent_cycle(
        agent, deps, "alert", TokenBudget(daily_max=1_000_000), engine,
        context=alert, console=console, model="test-model",
    )
    out = buf.getvalue()
    assert "▾ Context (carried into this cycle)" in out
    assert "Woke by — PRICE LEVEL:" in out
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest tests/test_p4_cycle_capture.py -q`
Expected: PASS（含 2 个新 console guard + 3 个既有 DB capture 测试全绿）

- [ ] **Step 7: Commit**

```bash
git add src/cli/app.py tests/test_p4_cycle_capture.py
git commit -m "iter-session-log-cycle-context: thread user_prompt_snapshot into CycleRenderContext (3 sites) + live-path guard"
```

---

## Task 9: 真实数据 fixture + round-trip drift-guard + 向后兼容审计

**Files:**
- Modify: `tests/test_session_log_cycle_context.py`
- Test: `tests/test_session_log_cycle_context.py` + 全量回归

§6 风险首条：Context 段解析耦合 `_render_recent_summaries` 的注入格式。本 task 用**真实 `_render_recent_summaries` 产出**（含 valid + forensic-prior 两种 body）喂解析链，断言能正确切块 + 反转 + 提字段 + 算 `(+N more)`；格式漂移时该测试先红。

- [ ] **Step 1: 写 round-trip drift-guard 测试**

追加到 `tests/test_session_log_cycle_context.py`：

```python
def test_roundtrip_render_recent_summaries_parses_correctly():
    """drift-guard：app._render_recent_summaries 真实产出（valid + forensic body）
    → _split_wake_prompt + _parse_injected_summaries + _extract_summary_fields 全链正确。
    格式漂移 → 本测试先红（spec §6 首条风险缓解）。
    """
    from datetime import datetime, timezone, timedelta
    from src.cli.app import _render_recent_summaries, CycleSummary
    from src.cli.display import (
        _SUMMARIES_MARKER, _parse_injected_summaries, _extract_summary_fields,
    )

    now = datetime(2026, 5, 31, 8, 0, 0, tzinfo=timezone.utc)
    summaries = [
        # 最旧：valid 5-field（ASC 源序 → 列表首）
        CycleSummary(
            id=1, cycle_id="824e2233aa", triggered_by="conditional",
            decision=(
                "**(1) Stance** — flat; cascade compressing.\n"
                "**(2) Active commitments** — none.\n"
                "**(3) This cycle delta** — closed short.\n"
                "**(4) Thesis & invalidation** — bearish; invalidation > 74,200.\n"
                "**(5) Watch list** — 73,000 support."
            ),
            execution_status="ok", created_at=now - timedelta(minutes=35),
        ),
        # 中间：forensic（decision=None → _render_empty_decision_body，NULL 块头变体）
        CycleSummary(
            id=2, cycle_id="47d5ef0199", triggered_by="scheduled",
            decision=None, execution_status="usage_limit_exceeded",
            created_at=now - timedelta(minutes=34),
        ),
        # 最新：valid 5-field（ASC 源序 → 列表尾）
        CycleSummary(
            id=3, cycle_id="00f7abcd55", triggered_by="alert",
            decision=(
                "**(1) Stance** — flat; MA20 reclaim confirmed.\n"
                "**(2) Active commitments** — alert above 73,384.\n"
                "**(3) This cycle delta** — updated alert.\n"
                "**(4) Thesis & invalidation** — bearish macro intact; invalidation > 74,200.\n"
                "**(5) Watch list** — 74,200 resistance."
            ),
            execution_status="ok", created_at=now - timedelta(minutes=8),
        ),
    ]
    block = _render_recent_summaries(summaries, now=now)
    assert block.startswith(_SUMMARIES_MARKER)  # 标记逐字一致 — 否则 _split_wake_prompt 失配

    # 模拟完整 snapshot 的后半（标记之后部分）
    summaries_half = block[len(_SUMMARIES_MARKER):]
    blocks = _parse_injected_summaries(summaries_half)

    # 切块 + 反转：3 条，newest-first
    assert [b[0] for b in blocks] == ["00f7", "47d5", "824e"]
    assert blocks[0][1] == "8 min ago"
    assert blocks[1][1] == "34 min ago"     # NULL 块头变体仍取到 ago
    # 字段提取
    f_new = _extract_summary_fields(blocks[0][2])   # 00f7 valid
    assert 1 in f_new and 4 in f_new and len(f_new) == 5
    f_forensic = _extract_summary_fields(blocks[1][2])  # 47d5 forensic body → 兜底
    assert f_forensic == {}
```

- [ ] **Step 2: 跑测试确认通过（实现已就绪 — 此为 guard，应直接绿）**

Run: `python -m pytest tests/test_session_log_cycle_context.py::test_roundtrip_render_recent_summaries_parses_correctly -q`
Expected: PASS

> 若红：说明 `_render_recent_summaries` 实际格式与解析器假设不符 —— 这正是 drift-guard 要捕获的。停下来核对 `_BLOCK_HEADER_RE` / `_SUMMARIES_MARKER` 与 `app.py` 实际产出，修到绿，**不要**改测试迁就 bug。

- [ ] **Step 3: 写截断长度安全网测试**

追加到 `tests/test_session_log_cycle_context.py`：

```python
def test_thesis_cap_truncates_pathological_long():
    """最近一条 Thesis 超 _CONTEXT_THESIS_CAP → ASCII ' ... [+N chars]' 截断。"""
    from src.cli.display import _render_carried_block, _CONTEXT_THESIS_CAP
    long_thesis = "x" * (_CONTEXT_THESIS_CAP + 500)
    body = f"**(1) Stance** — flat.\n**(4) Thesis & invalidation** — {long_thesis}"
    text = "\n".join(_render_carried_block("00f7", "8 min ago", body, is_newest=True))
    assert "... [+" in text and "chars]" in text


def test_fallback_whole_block_cap():
    """兜底 whole-block 超 _CONTEXT_FALLBACK_CAP → 截断。"""
    from src.cli.display import _render_carried_block, _CONTEXT_FALLBACK_CAP
    body = "y" * (_CONTEXT_FALLBACK_CAP + 300)  # 无 (N) marker → 兜底
    text = "\n".join(_render_carried_block("824e", "35 min ago", body, is_newest=False))
    assert "... [+" in text


def test_markdown_stars_stripped_in_render():
    """log 不解释 markdown：字面 ** 被剥离。"""
    from src.cli.display import _render_carried_block
    body = "**(1) Stance** — **flat** near MA20.\n**(4) Thesis & invalidation** — bearish."
    text = "\n".join(_render_carried_block("00f7", "8 min ago", body, is_newest=True))
    assert "**" not in text
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_session_log_cycle_context.py -k "cap or markdown_stars" -q`
Expected: PASS（3 passed）

- [ ] **Step 5: 向后兼容审计 — 跑相关既有测试全绿**

Run:
```bash
python -m pytest tests/test_display_cycle.py tests/test_iter_alert_trigger_id_unknown_tool_render.py tests/test_p4_cycle_capture.py tests/test_cycle_summary_injection.py tests/test_agent_cycle_injection.py -q
```
Expected: PASS（全绿 — 新字段默认 None，现有结构断言不受影响）

- [ ] **Step 6: 全量回归**

Run: `python -m pytest -q`
Expected: PASS（全部通过；记录最终 test 数，应 = 既有 baseline + 本 iter 新增约 30 个测试，0 fail）

- [ ] **Step 7: Commit**

```bash
git add tests/test_session_log_cycle_context.py
git commit -m "iter-session-log-cycle-context: round-trip drift-guard + length caps + backward-compat audit"
```
