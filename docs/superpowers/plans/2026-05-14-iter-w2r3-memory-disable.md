# iter-w2r3-memory-disable Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 禁用 memory 工具 wiring（tool register + prompt injection + persona dead pointers + display 集合层），建立 W3 sim 的"仅 Recent Cycle Summaries 单注入" baseline，0 storage 改动 + 两个连续 commit（drift guard + refactor）可通过 `git revert` 干净还原。

**Architecture:** Wiring-only removal — `MemoryService` 类、`memory_entries` 表、历史数据、`deps.memory` 装配、`MemoryService.format_for_prompt` 函数、`tools_memory.save_memory` impl 函数、`tools_perception.get_memories` impl 函数、`display.py` summarizer 函数全部保留。改动局限于 4 个 source file + 4 个既有 test 文件 + 1 个新 drift guard test 文件。

**Tech Stack:** pydantic-ai @tool decorator / SQLAlchemy 2.x async / pytest-asyncio / Python 3.13

---

## File Structure

| 类型 | 文件 | 角色 |
|---|---|---|
| Source modify | `src/agent/trader.py` | 移除 2 个 `@tool` 装饰函数 + REGISTERED_TOOL_NAMES 列表 2 项 + 段头注释 |
| Source modify | `src/cli/app.py` | 移除 `memory_context` 注入块 (line 504-506) + 上方注释尾部修正 (line 495) |
| Source modify | `src/agent/persona.py` | 删 Layer 1 line 89 末句 + 删 Layer 2 line 135 整句（dead pointer） |
| Source modify | `src/cli/display.py` | `_PERCEPTION_TOOL_NAMES` / `_SECTIONED_*` 集合移除 `get_memories` + dispatch branch 加 retired 注释 |
| Test modify | `tests/test_trader_agent.py` | 删 2 硬断言 + 注释 + count 34→32 |
| Test modify | `tests/test_display_cycle.py` | T-DG-2 5 步综合处置 |
| Test modify | `tests/test_agent_cycle_injection.py` | T4.3 反向断言 + module docstring 改 |
| Test modify | `tests/test_persona.py` | 删 `test_prompt_contains_memory_quality_guidance` + line 33 注释 |
| Test create | `tests/test_iter_w2r3_memory_disabled.py` | 新 drift guard (4 断言 a/b/c/d) |

**保留清单** (out-of-scope per spec §5):
- `src/agent/tools_memory.py` / `src/agent/memory.py` / `src/agent/tools_perception.py:get_memories` impl 函数
- `src/agent/trader.py:10` (`from src.agent.memory import MemoryService` import) + `:31` (`memory: MemoryService` typing field)
- `src/cli/app.py:15` (import) + `:825` (`memory = MemoryService(...)` 创建) + `:891` (`deps = TradingDeps(`) + `:897` (`memory=memory` keyword)
- `src/cli/display.py` 中 `_summarize_get_memories` / `summarize_save_memory` 函数体 + `_SYSTEM_LOG_PERCEPTION_PARSERS` dict 中 `"get_memories"` key
- `memory_entries` DB 表 + 索引 + 历史数据
- 13 处 dead fixture mocks（spec §3.2 清单，follow-up）

---

## Pre-flight checks

- [ ] **Verify branch state**

```bash
git status
git log --oneline -3
git rev-parse --abbrev-ref HEAD
```

Expected: branch = `iter-w2r3-memory-disable`, HEAD = `docs(iter-w2r3-memory-disable): design spec`, working tree clean.

- [ ] **Verify baseline test suite passes**

```bash
.venv/bin/python -m pytest tests/ -x --tb=short 2>&1 | tail -20
```

Expected: 1696 collected / 1691 passed (approx; current baseline per memory).

---

## Task 1: Add drift guard test (TDD red)

**Files:**
- Create: `tests/test_iter_w2r3_memory_disabled.py`

**Goal**: 4 个 drift guard 断言先红，作为后续 wiring 移除的 acceptance signal。

- [ ] **Step 1: Create drift guard test file**

Create `tests/test_iter_w2r3_memory_disabled.py`:

```python
"""Drift guard for iter-w2r3-memory-disable.

Asserts that memory tool wiring is removed:
(a) save_memory / get_memories not in agent toolset
(b) run_agent_cycle user_prompt does not contain memory injection markers
(c) generate_system_prompt does not reference deprecated memory tool
(d) MemoryService class and memory_entries table still exist (storage layer untouched)
"""
from __future__ import annotations

import re

import pytest


def test_a_memory_tools_unregistered():
    """(a) save_memory / get_memories must be absent from agent toolset."""
    from src.agent.trader import create_trader_agent, REGISTERED_TOOL_NAMES
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool_names = set(agent._function_toolset.tools)

    assert "save_memory" not in tool_names, "save_memory must be unregistered"
    assert "get_memories" not in tool_names, "get_memories must be unregistered"
    assert "save_memory" not in REGISTERED_TOOL_NAMES
    assert "get_memories" not in REGISTERED_TOOL_NAMES
    assert len(REGISTERED_TOOL_NAMES) == 32, (
        f"Expected 32 tools (19 perception + 13 execution), got {len(REGISTERED_TOOL_NAMES)}"
    )


def test_b_app_py_wiring_removed():
    """(b) src/cli/app.py source must not contain memory injection wiring.

    Static source-code guard. Rationale: the three injection markers
    ('Your memories:' / '=== Long-term Memory ===' / '=== Recent Context ===')
    can only reach the runtime user_prompt via the wiring path in app.py
    (`memory_context = await deps.memory.format_for_prompt(); prompt += "Your memories:\\n" + memory_context`).
    The latter two strings originate from MemoryService.format_for_prompt
    (memory.py:91, 97) which spec preserves — so asserting them absent
    from prompt requires asserting the wiring call site is gone.

    Static source assertion is stronger and simpler than full run_agent_cycle
    mocking (which risks vacuous-pass via incomplete mocks).
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    app_src = (repo_root / "src" / "cli" / "app.py").read_text(encoding="utf-8")

    assert "Your memories:" not in app_src, (
        "Wiring regression: 'Your memories:' string found in src/cli/app.py"
    )
    assert "deps.memory.format_for_prompt" not in app_src, (
        "Wiring regression: 'deps.memory.format_for_prompt' call found in src/cli/app.py"
    )
    assert "memory_context" not in app_src, (
        "Wiring regression: 'memory_context' variable found in src/cli/app.py"
    )


def test_c_system_prompt_has_no_memory_pointer():
    """(c) generate_system_prompt must not reference deprecated memory tool."""
    from src.agent.persona import generate_system_prompt
    from src.config import PersonaConfig

    prompt = generate_system_prompt(PersonaConfig())

    assert not re.search(r"Save actionable lessons to memory", prompt), (
        "persona.py:89 dead pointer leaked: 'Save actionable lessons to memory.'"
    )
    assert not re.search(r"lessons in your memory", prompt), (
        "persona.py:135 dead pointer leaked: 'Are there relevant lessons in your memory?'"
    )


def test_d_storage_layer_intact():
    """(d) MemoryService class and memory_entries table must still exist.

    Storage layer is out-of-scope for this iter — confirm no over-reach.
    """
    from src.agent.memory import MemoryService  # noqa: F401  (import must succeed)
    from src.storage.models import MemoryEntry  # noqa: F401  (import must succeed)

    assert MemoryEntry.__tablename__ == "memory_entries"
    # MemoryService class methods still callable
    assert hasattr(MemoryService, "save_long_term")
    assert hasattr(MemoryService, "format_for_prompt")
    assert hasattr(MemoryService, "get_relevant_memories")
```

- [ ] **Step 2: Run drift guard test, verify (a)(b)(c) FAIL and (d) PASS**

Run:

```bash
.venv/bin/python -m pytest tests/test_iter_w2r3_memory_disabled.py -v 2>&1 | tail -25
```

Expected:
- `test_a_memory_tools_unregistered` FAIL ("save_memory" still in tool_names)
- `test_b_app_py_wiring_removed` FAIL ('memory_context' / 'Your memories:' still in app.py source)
- `test_c_system_prompt_has_no_memory_pointer` FAIL ("Save actionable lessons" in prompt)
- `test_d_storage_layer_intact` PASS

- [ ] **Step 3: Commit drift guard (red state)**

```bash
git add tests/test_iter_w2r3_memory_disabled.py
git commit -m "$(cat <<'EOF'
test(iter-w2r3-memory-disable): add drift guard

4 assertions guarding wiring removal:
(a) save_memory / get_memories unregistered + REGISTERED_TOOL_NAMES == 32
(b) src/cli/app.py source has no memory injection wiring
    ('Your memories:' / 'deps.memory.format_for_prompt' / 'memory_context')
(c) generate_system_prompt has no 'Save actionable lessons to memory'
    or 'lessons in your memory'
(d) MemoryService class + memory_entries table still exist (storage layer
    untouched — out-of-scope per spec §5)

Currently (a)(b)(c) RED, (d) GREEN — wiring removal in subsequent commits
turns the first three GREEN.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Remove trader.py memory tool registration

**Files:**
- Modify: `src/agent/trader.py` (multiple line ranges)
- Modify: `tests/test_trader_agent.py:27,34,35,85-86`

- [ ] **Step 1: Remove `@tool async def get_memories` (line 177-186 — `@tool` decorator @ 177, function body 178-186)**

Open `src/agent/trader.py`. Delete the entire decorated function:

```python
    @tool
    async def get_memories(ctx: RunContext[TradingDeps]) -> str:
        """Get long-term memories (lessons, patterns, trade reviews).

        Check past memories before making decisions to avoid repeating mistakes
        and apply pattern recognitions that proved correct previously.
        """
        from src.agent.tools_perception import get_memories as _impl

        return await _impl(ctx.deps)
```

Leave the surrounding `@tool async def get_active_alerts` and `@tool async def get_trade_journal` untouched. Verify no blank-line gap regression.

- [ ] **Step 2: Remove `@tool async def save_memory` (line 726-746) + `# === Memory Tools ===` segment header**

Open `src/agent/trader.py`. Delete:

```python
    # === Memory Tools ===

    @tool
    async def save_memory(
        ctx: RunContext[TradingDeps], category: str, content: str, importance: float = 0.5
    ) -> str:
        """Save a learning or observation to long-term memory.

        Save memories that your future self would find actionable — trade
        outcomes, pattern recognitions that proved correct or incorrect, and
        mistakes to avoid. Routine observations like "market is quiet" are
        not worth saving.

        Args:
            category: 'trade_review', 'market_pattern', or 'lesson'.
            content: the memory content to save.
            importance: weight 0-1 (default 0.5).
        """
        from src.agent.tools_memory import save_memory as _impl

        return await _impl(ctx.deps, category, content, importance)
```

The next line should now be `return agent` (the closing of `create_trader_agent`).

- [ ] **Step 3: Update REGISTERED_TOOL_NAMES list (line 755-793) + nearby comments**

In `src/agent/trader.py`:

(a) Line 751 comment, change:

```python
# REGISTERED_TOOL_NAMES: 与 `@agent.tool` 装饰顺序保持一致（感知 → 执行 → memory）。
```

to:

```python
# REGISTERED_TOOL_NAMES: 与 `@agent.tool` 装饰顺序保持一致（感知 → 执行）。
```

(b) In the list body:

- Change `# --- 感知 (20) ---` → `# --- 感知 (19) ---`
- Remove `"get_memories",` line
- Remove the entire `# --- memory (1) ---` segment header + `"save_memory",` entry (3 lines including blank separator)

The final list shape:

```python
REGISTERED_TOOL_NAMES: list[str] = [
    # --- 感知 (19) ---
    "get_market_data",
    "get_position",
    "get_account_balance",
    "get_open_orders",
    "get_trade_journal",
    "get_active_alerts",
    "get_performance",
    "get_market_news",
    "get_exchange_announcements",
    "get_macro_calendar",
    "get_derivatives_data",
    "get_higher_timeframe_view",
    "get_macro_context",
    "get_etf_flows",
    "get_stablecoin_supply",
    "get_order_book",
    "get_recent_trades",
    "get_multi_timeframe_snapshot",
    "get_price_pivots",
    # --- 执行 (13) ---
    "open_position",
    "close_position",
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "set_price_volatility_alert",
    "cancel_order",
    "add_price_level_alert",
    "cancel_price_level_alert",
    "update_price_level_alert",
    "set_next_wake",
    "set_next_wake_at",
    "place_limit_order",
]
```

- [ ] **Step 4: Sync test_trader_agent.py:27,34,35**

Open `tests/test_trader_agent.py`. Around line 27-35:

```python
    assert "get_trade_journal" in tool_names
    assert "get_memories" in tool_names         # line 27 — DELETE
    # 执行类
    assert "open_position" in tool_names
    assert "close_position" in tool_names
    assert "set_stop_loss" in tool_names
    assert "set_take_profit" in tool_names
    assert "adjust_leverage" in tool_names
    # 记忆类                                    # line 34 — DELETE
    assert "save_memory" in tool_names          # line 35 — DELETE
```

Delete the 3 marked lines.

- [ ] **Step 5: Sync test_trader_agent.py:85-86 (count assertion)**

In `tests/test_trader_agent.py`, change:

```python
    assert len(REGISTERED_TOOL_NAMES) == 34, (
        f"Expected 34 tools (20+13+1), got {len(REGISTERED_TOOL_NAMES)}"
    )
```

to:

```python
    assert len(REGISTERED_TOOL_NAMES) == 32, (
        f"Expected 32 tools (19+13), got {len(REGISTERED_TOOL_NAMES)}"
    )
```

- [ ] **Step 6: Run targeted tests**

```bash
.venv/bin/python -m pytest tests/test_trader_agent.py tests/test_iter_w2r3_memory_disabled.py -v 2>&1 | tail -30
```

Expected:
- `test_iter_w2r3_memory_disabled.py::test_a_memory_tools_unregistered` PASS
- `test_iter_w2r3_memory_disabled.py::test_b...` still FAIL (wiring layer not done yet)
- `test_iter_w2r3_memory_disabled.py::test_c...` still FAIL (persona layer not done yet)
- `test_iter_w2r3_memory_disabled.py::test_d...` PASS
- All `tests/test_trader_agent.py` tests PASS

- [ ] **Step 7: Stage and continue (no commit yet — combined with Tasks 3-5 in one refactor commit)**

```bash
git status  # verify both files modified, working tree consistent
```

---

## Task 3: Remove cli/app.py prompt injection

**Files:**
- Modify: `src/cli/app.py:494-506`
- Modify: `tests/test_agent_cycle_injection.py:7,168-192`

- [ ] **Step 1: Remove memory_context injection block (line 504-506)**

Open `src/cli/app.py`. Locate:

```python
    memory_context = await deps.memory.format_for_prompt()
    if memory_context != "No relevant memories.":
        prompt += f"\n\nYour memories:\n{memory_context}"
```

Delete these 3 lines entirely. Verify the surrounding context — the previous block (`recent_block` from R2-8b) and the next block (`# P4 (obs roadmap Phase 3): ...`) must remain unchanged, with one blank line between them.

- [ ] **Step 2: Update line 495 comment (remove trailing "→ memory")**

In `src/cli/app.py`, change:

```python
    # (D-D-E injection position: trigger context → recent → memory).
```

to:

```python
    # (D-D-E injection position: trigger context → recent).
```

- [ ] **Step 3: Update test_agent_cycle_injection.py module docstring (line 7)**

Open `tests/test_agent_cycle_injection.py`. The module docstring starts at line 1. Change line 7:

```python
  - Injection appears AFTER trigger context, BEFORE memory_context.
```

to:

```python
  - Injection appears AFTER trigger context (memory_context removed in iter-w2r3-memory-disable).
```

- [ ] **Step 4: Rewrite test_injection_appears_before_memory_context (line 168-192)**

Replace the entire function with a reverse-assertion variant. Find:

```python
async def test_injection_appears_before_memory_context():
    """T4.3: order in prompt is trigger intro → recent summaries → memory."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-3")
    deps.memory = AsyncMock(
        format_for_prompt=AsyncMock(return_value="lesson-X-marker"),
    )
    await _seed_prior_cycles(engine, "sess-t4-3", count=1)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    pos_recent = prompt.index("Your prior cycle summaries")
    pos_memory = prompt.index("Your memories:")
    pos_intro = prompt.index("Assess the situation")
    assert pos_intro < pos_recent < pos_memory, (
        f"Order broken: intro={pos_intro} recent={pos_recent} memory={pos_memory}\n"
        f"prompt:\n{prompt}"
    )
```

Replace with:

```python
async def test_injection_appears_after_trigger_no_memory():
    """T4.3 (iter-w2r3-memory-disable): order in prompt is trigger intro → recent summaries; memory injection removed."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks("sess-t4-3")
    # deps.memory remains an AsyncMock per fixture; format_for_prompt should not be called
    await _seed_prior_cycles(engine, "sess-t4-3", count=1)
    budget = TokenBudget(daily_max=500_000)
    agent, captured = _make_capturing_agent()

    await run_agent_cycle(
        agent=agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    prompt = captured["prompt"]
    pos_recent = prompt.index("Your prior cycle summaries")
    pos_intro = prompt.index("Assess the situation")
    assert pos_intro < pos_recent, (
        f"Order broken: intro={pos_intro} recent={pos_recent}\nprompt:\n{prompt}"
    )
    assert "Your memories:" not in prompt, (
        f"memory injection regression: 'Your memories:' in prompt\n{prompt[:500]}"
    )
```

- [ ] **Step 5: Run targeted tests**

```bash
.venv/bin/python -m pytest tests/test_agent_cycle_injection.py tests/test_iter_w2r3_memory_disabled.py -v 2>&1 | tail -30
```

Expected:
- `test_injection_appears_after_trigger_no_memory` PASS
- `test_iter_w2r3_memory_disabled.py::test_a` PASS
- `test_iter_w2r3_memory_disabled.py::test_b` PASS (newly green)
- `test_iter_w2r3_memory_disabled.py::test_c` still FAIL
- `test_iter_w2r3_memory_disabled.py::test_d` PASS
- All other `test_agent_cycle_injection.py` tests PASS

---

## Task 4: Remove persona.py dead pointers

**Files:**
- Modify: `src/agent/persona.py:89,135`
- Modify: `tests/test_persona.py:33,48-53`

- [ ] **Step 1: Remove Layer 1 line 89 ending phrase**

Open `src/agent/persona.py`. Locate the bullet (around line 89):

```
- **Close fill response**: When woken by a fill that closed a position (stop loss, take profit, or manual close), review the trade outcome: what worked, what didn't, and what you would do differently. Save actionable lessons to memory.
```

Change to:

```
- **Close fill response**: When woken by a fill that closed a position (stop loss, take profit, or manual close), review the trade outcome: what worked, what didn't, and what you would do differently.
```

(Delete the trailing space + "Save actionable lessons to memory." — keep the period before the deleted text.)

- [ ] **Step 2: Remove Layer 2 line 135 dead pointer sentence**

In `src/agent/persona.py` (inside `_build_layer2()`), locate Self-Review paragraph (around line 135):

```
**Self-Review**
What happened in similar market conditions before? Are there relevant lessons in your memory? What can you learn from this cycle, regardless of whether you take a trade?
```

Change to:

```
**Self-Review**
What happened in similar market conditions before? What can you learn from this cycle, regardless of whether you take a trade?
```

(Delete the middle sentence "Are there relevant lessons in your memory? " — keep the question mark on the preceding sentence.)

- [ ] **Step 3: Delete test_prompt_contains_memory_quality_guidance (line 48-53)**

Open `tests/test_persona.py`. Delete the entire function:

```python
def test_prompt_contains_memory_quality_guidance():
    """L28 retained-bullet guard: 'Save actionable lessons to memory.' (spec §2.1)."""
    from src.agent.persona import generate_system_prompt
    prompt = generate_system_prompt(PersonaConfig())
    prompt_lower = prompt.lower()
    assert "actionable" in prompt_lower
```

(The new drift guard in `tests/test_iter_w2r3_memory_disabled.py::test_c_system_prompt_has_no_memory_pointer` covers the reverse assertion.)

- [ ] **Step 4: Update test_prompt_contains_fill_response_guidance line 33 comment**

In `tests/test_persona.py`, change line 33:

```python
    # Close fill: review outcome, save memory
```

to:

```python
    # Close fill: review outcome
```

- [ ] **Step 5: Run targeted tests**

```bash
.venv/bin/python -m pytest tests/test_persona.py tests/test_iter_w2r3_memory_disabled.py -v 2>&1 | tail -30
```

Expected:
- `test_persona.py` all remaining tests PASS (the deleted `test_prompt_contains_memory_quality_guidance` no longer runs)
- `test_iter_w2r3_memory_disabled.py::test_a` PASS
- `test_iter_w2r3_memory_disabled.py::test_b` PASS
- `test_iter_w2r3_memory_disabled.py::test_c` PASS (newly green)
- `test_iter_w2r3_memory_disabled.py::test_d` PASS

---

## Task 5: display.py dispatch sets sync + T-DG-2 fix

**Files:**
- Modify: `src/cli/display.py:502,514,516,549`
- Modify: `tests/test_display_cycle.py:1471-1511`

- [ ] **Step 1: Remove "get_memories" from _PERCEPTION_TOOL_NAMES**

Open `src/cli/display.py`. Locate around line 500-513 the `_PERCEPTION_TOOL_NAMES` frozenset literal. Remove the `"get_memories",` entry.

Verify final length = 19 entries. Look for an inline `# get_memories ...` comment in or near the set and update/remove if present.

- [ ] **Step 2: Simplify _SECTIONED_PERCEPTION_TOOL_NAMES expression**

In `src/cli/display.py` (around line 514), the expression is currently:

```python
_SECTIONED_PERCEPTION_TOOL_NAMES = (
    _PERCEPTION_TOOL_NAMES - frozenset({"get_memories"})
)
```

After `get_memories` is removed from `_PERCEPTION_TOOL_NAMES`, the subtraction is a no-op. Change to (preserving the `frozenset[str]` type annotation):

```python
_SECTIONED_PERCEPTION_TOOL_NAMES: frozenset[str] = _PERCEPTION_TOOL_NAMES
```

- [ ] **Step 3: Remove or update line 516 explanatory comment**

In `src/cli/display.py` around line 516, locate:

```python
# get_memories 是 backend-dependent format 例外（spec §4.2.13 / §8.8）;
```

Delete this comment line (the rationale no longer applies; `get_memories` is no longer in the perception set).

- [ ] **Step 4: Add retired-tool comment near save_memory dispatch branch**

In `src/cli/display.py` around line 549-551 (the `if tool_name == "save_memory":` dispatch branch), add a comment immediately above the branch:

```python
# Retired tool: iter-w2r3-memory-disable — dispatch branch kept for revert path.
if tool_name == "save_memory":
    if isinstance(tcp, ToolCallPart):
        return "✎", summarize_save_memory(args)
```

- [ ] **Step 5: Update T-DG-2 docstring (line 1471-1481)**

Open `tests/test_display_cycle.py`. Locate `test_dg_2_dispatch_sets_partition_all_registered_tools`. Replace the docstring:

Current:

```python
def test_dg_2_dispatch_sets_partition_all_registered_tools():
    """T-DG-2: 三层集合 + save_memory branch 互斥 + 完整覆盖 34 registered tools.

    Spec §4.4: _PERCEPTION_TOOL_NAMES (20) ∪ _EXECUTION_TOOL_NAMES (13) ∪ {save_memory}
    必须等于 REGISTERED_TOOL_NAMES (34)，且互不重叠。
    _SECTIONED_PERCEPTION_TOOL_NAMES (19) ⊂ _PERCEPTION_TOOL_NAMES（仅 get_memories 例外）。
    """
```

Replace with:

```python
def test_dg_2_dispatch_sets_partition_all_registered_tools():
    """T-DG-2: 二层集合互斥 + 完整覆盖 32 registered tools.

    Post iter-w2r3-memory-disable: _PERCEPTION_TOOL_NAMES (19) ∪ _EXECUTION_TOOL_NAMES (13)
    必须等于 REGISTERED_TOOL_NAMES (32)，且互不重叠。
    _SECTIONED_PERCEPTION_TOOL_NAMES = _PERCEPTION_TOOL_NAMES (no exclusion).
    """
```

- [ ] **Step 6: Update T-DG-2 body (line 1481-1511)**

Replace the function body (after the docstring) with:

```python
    from src.cli.display import (
        _PERCEPTION_TOOL_NAMES,
        _SECTIONED_PERCEPTION_TOOL_NAMES,
        _EXECUTION_TOOL_NAMES,
    )
    from src.agent.trader import REGISTERED_TOOL_NAMES

    perception = _PERCEPTION_TOOL_NAMES
    sectioned = _SECTIONED_PERCEPTION_TOOL_NAMES
    execution = _EXECUTION_TOOL_NAMES

    # Post iter-w2r3-memory-disable: sectioned equals perception (no exclusion).
    assert sectioned == perception

    # Two-layer disjointness.
    assert perception.isdisjoint(execution)

    # Complete coverage of 32 registered tools.
    union = perception | execution
    declared = set(REGISTERED_TOOL_NAMES)
    assert union == declared, (
        f"Dispatch sets ≠ REGISTERED_TOOL_NAMES:\n"
        f"  Missing from dispatch: {declared - union}\n"
        f"  Extra in dispatch: {union - declared}"
    )

    # Counts.
    assert len(perception) == 19
    assert len(execution) == 13
```

(This removes the `save = frozenset({"save_memory"})` local var, the `perception - sectioned == frozenset({"get_memories"})` assertion, the `len(sectioned) == 19` assertion which is now redundant with `sectioned == perception`, and the three `isdisjoint(save)` assertions.)

- [ ] **Step 7: Run targeted tests**

```bash
.venv/bin/python -m pytest tests/test_display_cycle.py tests/test_iter_w2r3_memory_disabled.py -v 2>&1 | tail -30
```

Expected:
- `test_dg_2_dispatch_sets_partition_all_registered_tools` PASS
- All other `test_display_cycle.py` tests PASS
- All 4 drift guard tests in `test_iter_w2r3_memory_disabled.py` PASS

---

## Task 6: Full test suite + final commit

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/python -m pytest tests/ 2>&1 | tail -10
```

Expected: all tests pass (count should be `1690+ passed` — exact baseline minus `test_prompt_contains_memory_quality_guidance` deleted in Task 4, plus 4 new drift guard tests in Task 1; net change roughly +3 over baseline).

If any test fails, debug before proceeding. Common failure modes:
- Missed a dead mock that's now invoked (highly unlikely given fixture mocks are AsyncMock-tolerant)
- Test using string match for "Long-term Memory" or similar — search and fix
- Import order issue in `tests/test_iter_w2r3_memory_disabled.py`

- [ ] **Step 2: Sanity check — grep for stray references**

```bash
echo "=== Should be ZERO: memory wiring in active code ==="
grep -n "save_memory\|get_memories" src/agent/trader.py src/cli/app.py src/agent/persona.py
echo "---"
echo "=== Should remain: MemoryService import / typing / instantiation ==="
grep -n "MemoryService\|deps.memory" src/agent/trader.py src/cli/app.py
echo "---"
echo "=== Should remain: tools_memory.py / memory.py / tools_perception.get_memories — file existence ==="
ls -la src/agent/tools_memory.py src/agent/memory.py
grep -n "^async def get_memories" src/agent/tools_perception.py
```

Expected:
- First grep: zero hits for `save_memory` / `get_memories` in trader/app/persona (only `MemoryService` and `deps.memory` references should remain)
- Second grep: `MemoryService` import + typing + instantiation all present
- Third command: all files exist and `get_memories` impl function still in `tools_perception.py`

- [ ] **Step 3: Commit refactor (Tasks 2-5 combined)**

```bash
git add src/agent/trader.py src/cli/app.py src/agent/persona.py src/cli/display.py \
        tests/test_trader_agent.py tests/test_agent_cycle_injection.py \
        tests/test_persona.py tests/test_display_cycle.py
git status  # verify staged file list
git commit -m "$(cat <<'EOF'
refactor(iter-w2r3-memory-disable): remove memory tool wiring

Wiring-only removal per spec — 0 storage layer changes.

Source removals:
- trader.py: @tool get_memories + @tool save_memory + REGISTERED_TOOL_NAMES
  list entries (34 → 32 = 19 perception + 13 execution) + segment header
  comment sync
- cli/app.py: memory_context injection block (line 504-506) + line 495
  injection-order comment sync (→ memory removed)
- persona.py:89: drop "Save actionable lessons to memory." trailing phrase
- persona.py:135: drop "Are there relevant lessons in your memory?" dead
  pointer (Layer 2 wiring cleanup, not reasoning steering — per spec §5
  exception clause)
- cli/display.py: _PERCEPTION_TOOL_NAMES drop "get_memories" +
  _SECTIONED_PERCEPTION_TOOL_NAMES expression simplified + line 516
  rationale comment removed + save_memory dispatch branch retired-tool
  marker comment added

Retained (out-of-scope per spec §5 / revert path):
- MemoryService class + memory.py / tools_memory.py / tools_perception.get_memories
  impl function
- trader.py:10 import + :31 TradingDeps.memory typing field
- cli/app.py:15 import + :825 MemoryService instantiation + :891/:897
  deps.memory wiring
- display.py _summarize_get_memories + summarize_save_memory functions +
  save_memory dispatch branch + _SYSTEM_LOG_PERCEPTION_PARSERS dict
  "get_memories" key
- memory_entries DB table + index + historical rows

Existing test sync:
- test_trader_agent.py: drop 2 hard assertions on registered tools + count
  34 → 32 + "Expected 34 tools (20+13+1)" → "Expected 32 tools (19+13)"
- test_agent_cycle_injection.py: rewrite T4.3 with reverse memory assertion +
  module docstring sync
- test_persona.py: delete test_prompt_contains_memory_quality_guidance +
  line 33 comment sync
- test_display_cycle.py: T-DG-2 5-step comprehensive rewrite (docstring +
  body — drop save_memory branch, drop sectioned exclusion, sets now 32)

Drift guard (added in prior commit): 4 assertions now all GREEN
(a) tool unregistration + REGISTERED_TOOL_NAMES count
(b) src/cli/app.py source has no memory injection wiring
(c) generate_system_prompt has no dead memory pointers
(d) storage layer (MemoryService + memory_entries) intact

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Verify final state**

```bash
git log --oneline -5
git status  # should be clean
.venv/bin/python -m pytest tests/test_iter_w2r3_memory_disabled.py -v 2>&1 | tail -10
```

Expected:
- 3 commits on this branch above main: docs(plan) → test(drift guard) → refactor
- Working tree clean
- All 4 drift guard tests GREEN

---

## Post-implementation

Open PR from `iter-w2r3-memory-disable` → `main` with:

- Title: `iter-w2r3-memory-disable: disable memory tool for W3 clean baseline`
- Body: summarize spec §0 + reference spec doc + drift guard count
- Link to spec: `docs/superpowers/specs/2026-05-14-iter-w2r3-memory-disable-design.md`

PR description should explicitly state W3 baseline is now "Recent Cycle Summaries only" + cross-period attribution caveat (see spec §4.4).
