# Iter w2r2-next-e — Alert 工具家族治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the Alert family treatment cluster: cancel idempotent on not-found, new update_price_level_alert tool preserving original direction + reasoning, F-A3 reasoning surfacing in cancel + update outputs.

**Architecture:** Three direct tool files (`tools_execution.py`, `trader.py`, `display.py`) + one allowlist comment update (`tool_call_recorder.py`) + one new test file (`tests/test_alert_family.py`) + four existing test files updated (test_trader_agent / test_display_cycle / test_alert_lifecycle / test_v_alert_lifecycle). No schema migration; alert state stays in-memory on `BaseExchange._price_level_alerts`. Layer-1 persona intentionally untouched.

**Tech Stack:** Python 3.12+ / pydantic-ai 1.78 / pytest-asyncio / SQLAlchemy async (DB only for `_record_action` audit + biz_error capture).

**Spec reference:** `docs/superpowers/specs/2026-05-12-iter-w2r2-next-e-alert-family-design.md` (commit `b8fc094`).

---

## Pre-flight context

Before starting tasks, an implementer should know:

- **Worktree**: this plan executes inside `.claude/worktrees/iter-w2r2-next-e-alert-family` on branch `worktree-iter-w2r2-next-e-alert-family`. Spec commit `b8fc094` is already in the branch.
- **Test fixture imports** for `tests/test_alert_family.py` are reused from `tests/test_tool_call_recorder`: `make_deps`, `make_ctx`, `make_call`. The `engine` and `session_with_row` pytest fixtures come from the same conftest tree (see how `tests/test_alert_lifecycle.py:19` imports them).
- **`_record_action`** is at `src/agent/tools_execution.py:19-50`. kwarg-only after `*`, fault-tolerant on DB failure. Plan reuses this for the update audit row.
- **`note_biz_error`** is imported from `src/services/tool_call_recorder` (line 7 of tools_execution).
- **Return-string contract** (line 14-16 of tools_execution.py): "If you change a return string's prefix, update `_EXECUTION_SUCCESS_PREFIXES` in display.py accordingly." This plan touches both sides in Task 5 in lockstep.

---

## File structure

| File | Action | Purpose |
|---|---|---|
| `src/agent/tools_execution.py` | Modify | Add `_lookup_alert` helper; rewrite `cancel_price_level_alert` (idempotent + F-A3); add `update_price_level_alert` |
| `src/agent/trader.py` | Modify | Add `update_price_level_alert` @tool registration after `cancel_price_level_alert` (590-614); insert `"update_price_level_alert"` into `REGISTERED_TOOL_NAMES` immediately after the `"cancel_price_level_alert"` entry (around line 717 inside the execution cluster, between cancel and `set_next_wake`) |
| `src/cli/display.py` | Modify | (a) `_EXECUTION_TOOL_NAMES` frozenset add; (b) `_EXECUTION_PARSERS` add + `_summarize_update_price_level_alert` helper; (c) `_EXECUTION_SUCCESS_PREFIXES` cancel→tuple + add update entry |
| `src/services/tool_call_recorder.py` | Modify | Line 60 comment attribution shift (cancel → update) |
| `tests/test_alert_family.py` | Create | New file housing 13 tests (2 _lookup_alert + 3 cancel behavior + 4 update behavior + 1 dispatch drift guard + 1 sync invariant + 1 idempotent-classification + 1 view-orphan limitation pin) |
| `tests/test_trader_agent.py` | Modify | Line 85: `== 32` → `== 33`; literal `(20+11+1)` → `(20+12+1)` |
| `tests/test_display_cycle.py` | Modify | Four-point patch: docstrings (1442, 1445) + comment (1469) + assert (1481 `== 11` → `== 12`) |
| `tests/test_alert_lifecycle.py` | Modify | Delete three legacy tests whose semantics flip under cancel idempotent + the §5.1.4.3 prefix-tuple fix: (a) 620-639 `test_cancel_price_level_alert_tool_state_not_found` — asserts old `"already triggered or expired"` return string (R2-2 T3); (b) 668-677 `test_is_tool_error_cancel_alert_state_not_found_returns_true` — asserts `is_tool_error == True` for content starting with `"Alert "`, which Task 5's prefix-tuple flips to `False` (R2-2 T5); (c) 761-787 `test_cancel_price_level_alert_not_found_records_biz_error` — asserts the old `biz_error` semantic. (a)+(c) deleted in Task 2 commit; (b) deleted in Task 5 commit. All three are fully replaced by new tests in test_alert_family.py per the TDD-suite-green principle. |

---

## Task 1: `_lookup_alert` helper (TDD foundation)

**Files:**
- Create: `tests/test_alert_family.py` (initial file + first test)
- Modify: `src/agent/tools_execution.py:276` (insert helper above cancel function)

- [ ] **Step 1: Create `tests/test_alert_family.py` skeleton + first failing test**

```python
"""Iter w2r2-next-e Alert family treatment tests.

See: docs/superpowers/specs/2026-05-12-iter-w2r2-next-e-alert-family-design.md
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from src.storage.database import get_session
from src.storage.models import ToolCall
from tests.test_tool_call_recorder import make_call, make_ctx, make_deps


# ============ Task 1: _lookup_alert helper ============

def test_lookup_alert_returns_dict_when_present():
    """_lookup_alert returns the full alert dict when id matches."""
    from src.agent.tools_execution import _lookup_alert

    exchange = MagicMock()
    exchange.get_price_level_alerts.return_value = [
        {"id": "a3f2b8c1", "price": 82100.0, "direction": "above",
         "symbol": "BTC/USDT:USDT", "reasoning": "4h high"},
        {"id": "d7c2e9f4", "price": 81720.0, "direction": "below",
         "symbol": "BTC/USDT:USDT", "reasoning": "1h low"},
    ]

    result = _lookup_alert(exchange, "a3f2b8c1")
    assert result == {
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h high",
    }


def test_lookup_alert_returns_none_when_absent():
    """_lookup_alert returns None when id not in the list."""
    from src.agent.tools_execution import _lookup_alert

    exchange = MagicMock()
    exchange.get_price_level_alerts.return_value = [
        {"id": "a3f2b8c1", "price": 82100.0, "direction": "above",
         "symbol": "BTC/USDT:USDT", "reasoning": "4h high"},
    ]

    result = _lookup_alert(exchange, "ffffffff")
    assert result is None
```

- [ ] **Step 2: Run the two failing tests to verify import error**

```bash
pytest tests/test_alert_family.py::test_lookup_alert_returns_dict_when_present tests/test_alert_family.py::test_lookup_alert_returns_none_when_absent -v
```

Expected: FAIL with `ImportError: cannot import name '_lookup_alert' from 'src.agent.tools_execution'`

- [ ] **Step 3: Implement `_lookup_alert` in `tools_execution.py`**

Insert immediately above the existing `cancel_price_level_alert` function (currently at line 276). Locate the position by searching for `async def cancel_price_level_alert(` and place the helper just before it.

```python
def _lookup_alert(exchange, alert_id: str) -> dict | None:
    """Peek at the alert dict by id without mutating the alert list.

    Used by cancel (to capture reasoning before remove) and update (to
    capture direction + reasoning before sequential replace). Returns
    the full alert dict matching the id, or None if no match.
    """
    for alert in exchange.get_price_level_alerts():
        if alert["id"] == alert_id:
            return alert
    return None
```

- [ ] **Step 4: Run the two tests to verify pass**

```bash
pytest tests/test_alert_family.py::test_lookup_alert_returns_dict_when_present tests/test_alert_family.py::test_lookup_alert_returns_none_when_absent -v
```

Expected: PASS (2 passed)

- [ ] **Step 5: Commit Task 1**

```bash
git add tests/test_alert_family.py src/agent/tools_execution.py
git commit -m "feat(iter-w2r2-next-e): add _lookup_alert helper for cancel/update reuse

Module-level helper in tools_execution.py used by cancel (to capture
reasoning before remove for F-A3) and update (to capture direction +
reasoning before sequential replace). Spec §3.5 + §5.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `cancel_price_level_alert` idempotent + F-A3 reasoning surfacing

**Files:**
- Modify: `tests/test_alert_family.py` (add 3 tests)
- Modify: `src/agent/tools_execution.py:276-299` (cancel function body)

- [ ] **Step 1: Add three failing tests to `tests/test_alert_family.py`**

Append the following to `tests/test_alert_family.py` (after the Task 1 tests):

```python
# ============ Task 2: cancel idempotent + F-A3 reasoning ============

@pytest.mark.asyncio
async def test_cancel_idempotent_not_found(engine, session_with_row):
    """Spec §3.2: cancel of an absent alert_id returns ok with idempotent note,
    no biz_error recorded. Closes F-F1 (sim #8 40% biz_error rate).
    """
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = []  # empty: id absent
    deps.exchange.remove_price_level_alert.return_value = False

    async def handler(args):
        return await cancel_price_level_alert(
            deps, alert_id="a3f2b8c1", reasoning="auto-cleared check",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "no longer active" in result
    assert "already triggered or removed" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok"  # idempotent ok — NOT biz_error
    assert rows[0].error_type is None


@pytest.mark.asyncio
async def test_cancel_format_invalid_still_rejects(engine, session_with_row):
    """Spec §3.2: format-invalid alert_id (non-hex / wrong length) still
    records biz_error 'invalid_alert_id_format' — idempotency does not apply.
    """
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        return await cancel_price_level_alert(
            deps, alert_id="NOT-HEX!", reasoning="t",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Invalid alert_id format" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_alert_id_format"


@pytest.mark.asyncio
async def test_cancel_success_includes_reasoning(engine, session_with_row):
    """Spec §3.5 F-A3: cancel success return includes original alert reasoning."""
    from src.agent.tools_execution import cancel_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h structural high",
    }]
    deps.exchange.remove_price_level_alert.return_value = True

    async def handler(args):
        return await cancel_price_level_alert(
            deps, alert_id="a3f2b8c1", reasoning="invalidated by regime shift",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("cancel_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Price level alert cancelled" in result
    assert "id=a3f2b8c1" in result
    # F-A3: original reasoning surfaced in output
    assert '— "4h structural high"' in result
```

- [ ] **Step 2: Run the three tests to verify red**

```bash
pytest tests/test_alert_family.py::test_cancel_idempotent_not_found tests/test_alert_family.py::test_cancel_format_invalid_still_rejects tests/test_alert_family.py::test_cancel_success_includes_reasoning -v
```

Expected: FAIL on idempotent (still records biz_error) + FAIL on includes_reasoning (no `— "..."` suffix); format_invalid may PASS already.

- [ ] **Step 3: Rewrite cancel function in `tools_execution.py:276-299`**

Replace the entire existing body of `cancel_price_level_alert` (the function declared around line 276):

```python
async def cancel_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    reasoning: str,
) -> str:
    """Cancel a previously-set price level alert by its ID.

    Idempotent: if the alert is no longer active (already triggered or
    removed via close-fill auto-clear), returns ok with a Note rather
    than emitting a business error. Format-invalid IDs and unexpected
    internal exceptions still reject explicitly.

    Args:
        alert_id: 8-char hex id returned by add_price_level_alert.
        reasoning: brief rationale for the cancel (audit-only).
    """
    # 协议层：8-char hex 格式校验（uuid.uuid4()[:8] 生成，[0-9a-f]{8}）
    if not re.fullmatch(r"[0-9a-f]{8}", alert_id):
        note_biz_error("invalid_alert_id_format")
        return (
            f"Invalid alert_id format: {alert_id!r}. Expected 8-char hex "
            f"(e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids."
        )

    # Peek before mutate — captures reasoning for F-A3 success-string suffix.
    alert = _lookup_alert(deps.exchange, alert_id)
    if alert is None:
        # 状态不存在 → idempotent ok with note (spec §3.2, §3.4).
        # Covers both root causes: auto-trigger removal during cascade AND
        # _clear_stale_alerts_for_full_close on position close (PR #27).
        return (
            f"Alert {alert_id} no longer active "
            f"(already triggered or removed)"
        )

    ok = deps.exchange.remove_price_level_alert(alert_id)
    if not ok:
        # Defensive: lookup and remove are both sync, in-cycle; remove failing
        # after a successful lookup would indicate a real invariant violation.
        raise RuntimeError(
            f"remove_price_level_alert returned False for id={alert_id} "
            f"that was just present in lookup — invariant violated"
        )

    await _record_action(
        deps, action="cancel_price_level_alert",
        alert_id=alert_id,
        reasoning=reasoning,
    )
    return (
        f'Price level alert cancelled (id={alert_id}) — '
        f'"{alert["reasoning"]}"'
    )
```

- [ ] **Step 4: Run the three Task 2 tests to verify pass**

```bash
pytest tests/test_alert_family.py::test_cancel_idempotent_not_found tests/test_alert_family.py::test_cancel_format_invalid_still_rejects tests/test_alert_family.py::test_cancel_success_includes_reasoning -v
```

Expected: PASS (3 passed)

- [ ] **Step 5: Delete two legacy tests whose return-string assertions flip under cancel idempotent**

The cancel rewrite breaks two existing tests in `tests/test_alert_lifecycle.py`:

- **Lines 620-639** `test_cancel_price_level_alert_tool_state_not_found` (R2-2 T3) — uses `make_sim_exchange()` and asserts `"already triggered or expired" in result`. The new return is `"Alert {id} no longer active (already triggered or removed)"` — the substring `"already triggered or expired"` is absent. Test fails after cancel rewrite. Fully replaced by `test_cancel_idempotent_not_found` (Task 2 Step 1).
- **Lines 761-787** `test_cancel_price_level_alert_not_found_records_biz_error` — asserts `status == "biz_error"` and the old return-string substring. Cancel no longer emits biz_error for not-found, and the return string changed. Fully replaced by `test_cancel_idempotent_not_found` (idempotent ok path) and `test_update_not_found_rejects` (Task 3, update biz_error path).

Delete both function blocks (decorator through closing assert, plus the trailing blank line for each) in the same commit as the impl change. Keeps the test suite green at every commit per TDD discipline.

Note: `test_is_tool_error_cancel_alert_state_not_found_returns_true` (lines 668-677, R2-2 T5) also flips its assertion but only after Task 5 lands the prefix-tuple fix — that deletion is folded into Task 5's commit, not Task 2's.

- [ ] **Step 6: Run the cancel tests + remaining alert_lifecycle tests to verify all green**

```bash
pytest tests/test_alert_family.py::test_cancel_idempotent_not_found tests/test_alert_family.py::test_cancel_format_invalid_still_rejects tests/test_alert_family.py::test_cancel_success_includes_reasoning tests/test_alert_lifecycle.py -v
```

Expected: PASS — Task 2 tests green; test_alert_lifecycle.py test count drops by 2 (lines 620-639 + 761-787); no failures. `test_is_tool_error_cancel_alert_state_not_found_returns_true` (line 668-677) still passes here because Task 5's prefix-tuple change hasn't landed yet.

- [ ] **Step 7: Commit Task 2**

```bash
git add tests/test_alert_family.py tests/test_alert_lifecycle.py src/agent/tools_execution.py
git commit -m "feat(iter-w2r2-next-e): cancel_price_level_alert idempotent + F-A3 reasoning

Cancel of an absent alert_id now returns ok with a 'no longer active'
Note instead of biz_error. Format-invalid IDs still reject. Success
output surfaces the alert's original reasoning text (F-A3).

Also delete two legacy tests in tests/test_alert_lifecycle.py whose
assertions flip under the new return string / idempotent contract:
- 620-639 test_cancel_price_level_alert_tool_state_not_found (R2-2 T3,
  sim-driven 'already triggered or expired' substring)
- 761-787 test_cancel_price_level_alert_not_found_records_biz_error
  (recorder-driven biz_error 'alert_not_found' status)
Coverage moves to test_alert_family.py test_cancel_idempotent_not_found
+ test_update_not_found_rejects. Deleting in the same commit as the
impl keeps the test suite green at every commit.

Spec §3, §7 Task 7 (test cleanup folded forward into this commit).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `update_price_level_alert` new tool function

**Files:**
- Modify: `tests/test_alert_family.py` (add 4 tests)
- Modify: `src/agent/tools_execution.py` (insert new function after cancel)

- [ ] **Step 1: Add four failing tests to `tests/test_alert_family.py`**

Append after Task 2 tests:

```python
# ============ Task 3: update_price_level_alert new tool ============

@pytest.mark.asyncio
async def test_update_success_preserves_direction_and_reasoning(engine, session_with_row):
    """Spec §4.2 step 5+6 + AC-4: update preserves original direction and reasoning
    on the new alert; return string shows id transition and original reasoning.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h structural high",
    }]
    deps.exchange.remove_price_level_alert.return_value = True
    deps.exchange.add_price_level_alert.return_value = "d7c2e9f4"

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="a3f2b8c1", new_price=82500.0,
            reasoning="trail up after breakout",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Price level alert updated" in result
    assert "id=a3f2b8c1" in result
    assert "id=d7c2e9f4" in result
    # Direction preserved (still "above")
    assert "above 82100.00 → above 82500.00" in result
    # Reasoning preserved
    assert '— "4h structural high"' in result

    # Exchange add was called with original_direction + original_reasoning
    call_kwargs_args = deps.exchange.add_price_level_alert.call_args.args
    # signature: add_price_level_alert(price, direction, symbol, reasoning)
    assert call_kwargs_args[0] == 82500.0
    assert call_kwargs_args[1] == "above"
    assert call_kwargs_args[3] == "4h structural high"


@pytest.mark.asyncio
async def test_update_not_found_rejects(engine, session_with_row):
    """Spec §4.2 step 2 + AC-5: update of absent alert_id returns biz_error
    'alert_not_found' with directive to use add_price_level_alert.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = []  # absent

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="a3f2b8c1", new_price=82500.0,
            reasoning="trail",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Alert a3f2b8c1 not found" in result
    assert "add_price_level_alert" in result  # directive present

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "alert_not_found"

    # No mutation on the exchange
    deps.exchange.remove_price_level_alert.assert_not_called()
    deps.exchange.add_price_level_alert.assert_not_called()


@pytest.mark.asyncio
async def test_update_format_invalid(engine, session_with_row):
    """Spec §4.2 step 1 + AC-6: non-hex alert_id rejects with invalid_alert_id_format."""
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="NOT-HEX!", new_price=82500.0, reasoning="t",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    assert "Invalid alert_id format" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "invalid_alert_id_format"


@pytest.mark.asyncio
async def test_update_immediate_trigger_allowed(engine, session_with_row):
    """Spec §4.3 + AC-7: new_price on the trigger-side of current is accepted
    without warning/block (per §1.4 audit — agent strategic re-wake).
    Above-alert moved to a price that would trigger immediately on next tick
    must not produce a warning string. Acts as a drift guard against future
    addition of immediate-trigger warning logic in this tool: current impl
    has no distance/position check on new_price, so this test reads as a
    'no warning was added' invariant.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    # Original above-alert at 82,100; move to 82,200 — if anyone adds a
    # vs-current-price warning, this assertion fires.
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "spring breakout",
    }]
    deps.exchange.remove_price_level_alert.return_value = True
    deps.exchange.add_price_level_alert.return_value = "d7c2e9f4"

    async def handler(args):
        return await update_price_level_alert(
            deps, alert_id="a3f2b8c1", new_price=82200.0,
            reasoning="tighten level",
        )

    result = await recorder.wrap_tool_execute(
        make_ctx(deps),
        call=make_call("update_price_level_alert"),
        tool_def=MagicMock(),
        args={},
        handler=handler,
    )

    # No warning / block — return is plain success
    assert "Price level alert updated" in result
    assert "may trigger immediately" not in result  # no warning
    assert "WARNING" not in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].error_type is None
```

- [ ] **Step 2: Run the four tests to verify red**

```bash
pytest tests/test_alert_family.py -k "test_update_" -v
```

Expected: FAIL (4) — `ImportError: cannot import name 'update_price_level_alert'`.

- [ ] **Step 3: Implement `update_price_level_alert` in `tools_execution.py`**

Insert immediately after the rewritten `cancel_price_level_alert` function (the end of its body, before `async def set_next_wake`):

```python
async def update_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    new_price: float,
    reasoning: str,
) -> str:
    """Replace a single existing price level alert with a new price.

    Atomic: cancels the old alert and creates a new one with new_price,
    preserving the original direction and reasoning text. The direction
    (above/below) cannot change — to change direction or reasoning
    materially, use cancel + add. Trail use case: when price moves and
    you want the same alert at a new level, this preserves identity
    continuity (the alert is still "the same thing at a new price").

    Args:
        alert_id: 8-char hex id of the existing alert (see get_active_alerts).
        new_price: new trigger price.
        reasoning: brief rationale for the move (audit-only; not stored
            on the alert).
    """
    # Step 1: format validation
    if not re.fullmatch(r"[0-9a-f]{8}", alert_id):
        note_biz_error("invalid_alert_id_format")
        return (
            f"Invalid alert_id format: {alert_id!r}. Expected 8-char hex "
            f"(e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids."
        )

    # Step 2: lookup — capture original direction + reasoning
    alert = _lookup_alert(deps.exchange, alert_id)
    if alert is None:
        note_biz_error("alert_not_found")
        return (
            f"Alert {alert_id} not found. "
            f"To create a new alert, use add_price_level_alert."
        )

    original_direction = alert["direction"]
    original_reasoning = alert["reasoning"]
    old_price = alert["price"]

    # Step 4: sequential replace (single-coroutine, no yield points;
    # both calls mutate the same in-memory list — atomic by construction).
    ok = deps.exchange.remove_price_level_alert(alert_id)
    if not ok:
        raise RuntimeError(
            f"remove_price_level_alert returned False for id={alert_id} "
            f"that was just present in lookup — invariant violated"
        )
    new_id = deps.exchange.add_price_level_alert(
        new_price, original_direction, deps.symbol, original_reasoning,
    )
    if new_id is None:
        # After remove, headroom is necessarily >= 1; add returning None
        # indicates the cap path was hit, which should be impossible here.
        raise RuntimeError(
            f"add_price_level_alert returned None after a successful remove "
            f"on id={alert_id} — invariant violated (cap check unreachable)"
        )

    # Step 8: audit row — new id in canonical alert_id column;
    # old id + direction + old_price folded into reasoning string.
    await _record_action(
        deps, action="update_price_level_alert",
        alert_id=new_id,
        reasoning=(
            f"replaces {alert_id} ({original_direction} {old_price}) "
            f"→ {new_price} | {reasoning}"
        ),
    )

    # Step 7: success return
    return (
        f"Price level alert updated (id={alert_id} → id={new_id}):\n"
        f"  {original_direction} {old_price:.2f} → "
        f"{original_direction} {new_price:.2f} "
        f'— "{original_reasoning}"'
    )
```

- [ ] **Step 4: Run the four tests to verify pass**

```bash
pytest tests/test_alert_family.py -k "test_update_" -v
```

Expected: PASS (4 passed)

- [ ] **Step 5: Commit Task 3**

```bash
git add tests/test_alert_family.py src/agent/tools_execution.py
git commit -m "feat(iter-w2r2-next-e): add update_price_level_alert tool

New @tool function for the trail use case — single-call replace of an
existing alert preserving original direction and reasoning. Atomic
sequential remove + add on the in-memory alert list. Format-invalid IDs
and absent IDs reject explicitly (principle 6 — update of nonexistent
state). Immediate-trigger semantics allowed per §4.3 (agent strategic
re-wake mechanism observed in sim #8 — 10 deliberate instances).

Spec §4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: trader.py @tool registration + REGISTERED_TOOL_NAMES + count drift guards

**Files:**
- Modify: `src/agent/trader.py:591-614` (insert update @tool after cancel) + `src/agent/trader.py:687` (add to REGISTERED_TOOL_NAMES)
- Modify: `tests/test_trader_agent.py:85` (assertion + literal)
- Modify: `tests/test_display_cycle.py` (four-point patch)

- [ ] **Step 1: Add update_price_level_alert @tool registration in `trader.py`**

Locate `async def cancel_price_level_alert(` inside the `@tool` decorated block (around line 591-614). Insert the following immediately after the closing of that function block (and immediately before the next `@tool`):

```python
    @tool
    async def update_price_level_alert(
        ctx: RunContext[TradingDeps],
        alert_id: str,
        new_price: float,
        reasoning: str,
    ) -> str:
        """Replace a single existing price level alert with a new price.

        Atomic: cancels the old alert and creates a new one with new_price,
        preserving the original direction and reasoning text. The direction
        (above/below) cannot change — to change direction or reasoning
        materially, use cancel + add. Trail use case: when price moves and
        you want the same alert at a new level, this preserves identity
        continuity (the alert is still "the same thing at a new price").

        Args:
            alert_id: 8-char hex id of the existing alert (see get_active_alerts).
            new_price: new trigger price.
            reasoning: brief rationale for the move (audit-only).
        """
        from src.agent.tools_execution import update_price_level_alert as _impl

        return await _impl(ctx.deps, alert_id, new_price, reasoning=reasoning)
```

- [ ] **Step 2: Add `"update_price_level_alert"` to `REGISTERED_TOOL_NAMES`**

In `src/agent/trader.py`, the list `REGISTERED_TOOL_NAMES` is **declared** at line 687 (`REGISTERED_TOOL_NAMES: list[str] = [`) but the insertion point is inside the execution cluster around line 717 — find the line `"cancel_price_level_alert",` (the last entry before `"set_next_wake",` in the execution block) and insert `"update_price_level_alert",` on the line immediately after it. The execution cluster comment header `# --- 执行 (11) ---` (around line 708) should also update to `# --- 执行 (12) ---`.

- [ ] **Step 3: Update `tests/test_trader_agent.py:85` count drift guard**

Locate the assertion at line 85 (search for `Expected 32 tools`):

```python
# Before:
    assert len(REGISTERED_TOOL_NAMES) == 32, (
        f"Expected 32 tools (20+11+1), got {len(REGISTERED_TOOL_NAMES)}"
    )

# After:
    assert len(REGISTERED_TOOL_NAMES) == 33, (
        f"Expected 33 tools (20+12+1), got {len(REGISTERED_TOOL_NAMES)}"
    )
```

- [ ] **Step 4: Apply four-point patch to `tests/test_display_cycle.py`**

Search for `def test_dg_2_dispatch_sets_partition_all_registered_tools` (around line 1440). Make these four edits inside the test docstring + body:

```python
# Line 1442 (docstring): "covers 32 registered tools" → "covers 33 registered tools"
# Line 1445 (docstring): "(32)" → "(33)" AND "_EXECUTION_TOOL_NAMES (11)" → "_EXECUTION_TOOL_NAMES (12)"
# Line 1469 (inline comment "# 完整覆盖 32 registered"): "32" → "33"
# Line 1481 (assert): `assert len(execution) == 11` → `assert len(execution) == 12`
```

- [ ] **Step 5: Run the affected drift guards — expect Task-4-only failures**

```bash
pytest tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools tests/test_display_cycle.py::test_dg_2_dispatch_sets_partition_all_registered_tools -v
```

Expected: `test_dg_2` FAILS because `update_price_level_alert` is in `REGISTERED_TOOL_NAMES` but not in `_EXECUTION_TOOL_NAMES` (handled in Task 5). `test_registered_tool_names_matches_agent_tools` PASSES (both sides updated).

- [ ] **Step 6: Commit Task 4**

```bash
git add src/agent/trader.py tests/test_trader_agent.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-next-e): register update_price_level_alert + count drift guards

REGISTERED_TOOL_NAMES 32 → 33 (insert update_price_level_alert after
cancel in execution cluster). @tool registration in trader.py immediately
after cancel. test_trader_agent.py:85 assertion + (20+11+1)→(20+12+1)
literal updated. test_display_cycle.py four-point patch (docstrings +
comment + assert len(execution) == 12).

test_dg_2_dispatch_sets_partition_all_registered_tools still fails until
Task 5 updates _EXECUTION_TOOL_NAMES frozenset.

Spec §5.1.1, §5.1.2, §5.1.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: display.py three dispatch surfaces + dispatch drift guard test

**Files:**
- Modify: `tests/test_alert_family.py` (add 1 dispatch test)
- Modify: `src/cli/display.py:492-504` (frozenset) + `:252-263` (parsers + helper) + `:266-278` (prefixes)

- [ ] **Step 1: Add the dispatch drift-guard test to `tests/test_alert_family.py`**

Append:

```python
# ============ Task 5: display.py dispatch surfaces drift guard ============

def test_update_display_dispatch_registered():
    """Spec §5.1.4 + AC-12 + AC-15: update_price_level_alert must be present in
    all three display.py dispatch structures (frozenset / parsers / prefixes).
    """
    from src.cli.display import (
        _EXECUTION_PARSERS,
        _EXECUTION_SUCCESS_PREFIXES,
        _EXECUTION_TOOL_NAMES,
    )

    # 5.1.4.1: frozenset membership (required for test_dg_2 partition)
    assert "update_price_level_alert" in _EXECUTION_TOOL_NAMES

    # 5.1.4.2: parser registered + correctly extracts direction + prices
    assert "update_price_level_alert" in _EXECUTION_PARSERS
    parser = _EXECUTION_PARSERS["update_price_level_alert"]
    sample = (
        "Price level alert updated (id=a3f2b8c1 → id=d7c2e9f4):\n"
        "  above 82100.00 → above 82500.00 — \"4h structural high\""
    )
    summary = parser(sample)
    assert "above" in summary
    assert "$82,100" in summary
    assert "$82,500" in summary

    # 5.1.4.3: success-prefix entry registered (single string for update)
    assert _EXECUTION_SUCCESS_PREFIXES["update_price_level_alert"] == (
        "Price level alert updated"
    )
```

- [ ] **Step 2: Run the test to verify red**

```bash
pytest tests/test_alert_family.py::test_update_display_dispatch_registered -v
```

Expected: FAIL on all three asserts (entries absent).

- [ ] **Step 3: Add `"update_price_level_alert"` to `_EXECUTION_TOOL_NAMES` frozenset (display.py:492-504)**

Insert `"update_price_level_alert",` immediately after `"cancel_price_level_alert",` in the frozenset literal.

- [ ] **Step 4: Add `_summarize_update_price_level_alert` helper + `_EXECUTION_PARSERS` entry (display.py:238-263)**

Insert this helper immediately after the existing `_summarize_add_price_level_alert` (around line 238-243):

```python
def _summarize_update_price_level_alert(content: str) -> str:
    # Matches §4.2 step 7 success-return shape:
    #   "Price level alert updated (id=AAAA → id=BBBB):
    #      above 82100.00 → above 82500.00 — \"reasoning\""
    m = re.search(
        r"(above|below)\s+([\d.]+)\s*→\s*(above|below)\s+([\d.]+)", content
    )
    if m:
        return (
            f"{m.group(1)} ${float(m.group(2)):,.0f} → "
            f"${float(m.group(4)):,.0f}"
        )
    return _fallback_summary(content)
```

Then add the entry to `_EXECUTION_PARSERS` dict (around line 252-263), immediately after `"add_price_level_alert": _summarize_add_price_level_alert,`:

```python
    "update_price_level_alert": _summarize_update_price_level_alert,
```

- [ ] **Step 5: Update `_EXECUTION_SUCCESS_PREFIXES` dict (display.py:266-278) — two entries change**

Locate the `cancel_price_level_alert` entry. Change it to a tuple, and add a new entry for update right after:

```python
# Before:
    "cancel_price_level_alert": "Price level alert cancelled",

# After:
    "cancel_price_level_alert": (
        "Price level alert cancelled",   # cancel success (real removal)
        "Alert ",                         # cancel idempotent ok return prefix
    ),
    "update_price_level_alert": "Price level alert updated",
```

- [ ] **Step 6: Delete the third legacy test whose assertion flips under the prefix-tuple change**

`tests/test_alert_lifecycle.py:668-677` (`test_is_tool_error_cancel_alert_state_not_found_returns_true`, R2-2 T5) constructs `content="Alert deadbeef already triggered or expired"` and asserts `is_tool_error(...) is True`. After Task 5 Step 5's prefix tuple change, this content matches the new `"Alert "` prefix → `is_tool_error` returns `False` → assertion fails.

The test's original premise (that "state-error informational messages should be classified as tool error") is precisely the §3.2 idempotent decision being reversed. The new `test_cancel_idempotent_not_classified_as_error` (Task 6) inverts this assertion with the new return-string shape and is the proper replacement.

Locate the function (around lines 668-677, immediately after `test_is_tool_error_cancel_alert_invalid_format_returns_true`). Delete the function block (def line through closing assert + trailing blank line).

- [ ] **Step 7: Run the dispatch drift-guard test + test_dg_2 + remaining alert_lifecycle tests**

```bash
pytest tests/test_alert_family.py::test_update_display_dispatch_registered tests/test_display_cycle.py::test_dg_2_dispatch_sets_partition_all_registered_tools tests/test_alert_lifecycle.py -v
```

Expected: PASS (all). `test_alert_lifecycle.py` test count drops by 1 more (the just-deleted 668-677).

- [ ] **Step 8: Commit Task 5**

```bash
git add tests/test_alert_family.py tests/test_alert_lifecycle.py src/cli/display.py
git commit -m "feat(iter-w2r2-next-e): display.py dispatch surface — three sub-edits

(a) _EXECUTION_TOOL_NAMES frozenset adds 'update_price_level_alert' —
    required for test_dg_2 partition guard (hard failure without it).
(b) _EXECUTION_PARSERS + new _summarize_update_price_level_alert helper
    using (content: str) -> str contract (per §5.1.4.2).
(c) _EXECUTION_SUCCESS_PREFIXES — cancel entry becomes tuple
    ('Price level alert cancelled', 'Alert ') to cover idempotent ok
    return ('Alert {id} no longer active'); new update entry
    'Price level alert updated'. Without the tuple, every cancel
    idempotent ok would be silently misclassified as tool error in
    UI/metrics (post-v5 review A1).

Also delete tests/test_alert_lifecycle.py:668-677
(test_is_tool_error_cancel_alert_state_not_found_returns_true, R2-2 T5)
whose assertion flips under the prefix-tuple change — its premise
('state-error messages should be classified as error') is precisely
the §3.2 idempotent decision being reversed. Replacement coverage is
in test_alert_family.py test_cancel_idempotent_not_classified_as_error
(Task 6, asserts the opposite for the new return string).

Spec §5.1.4.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: idempotent-not-classified-as-error + sync invariant drift guards

**Files:**
- Modify: `tests/test_alert_family.py` (add 2 tests)

- [ ] **Step 1: Append three drift-guard tests to `tests/test_alert_family.py`**

```python
# ============ Task 6: drift guards — classification + sync invariant ============

def test_cancel_idempotent_not_classified_as_error():
    """Spec §5.1.4.3 + AC-14: cancel idempotent ok must NOT be misclassified
    as tool error by is_tool_error (the post-v5 review A1 finding). Without the
    prefix-tuple fix, the idempotent return string would fail prefix match
    and is_tool_error would return True, defeating the whole idempotent design.
    """
    from src.cli.display import is_tool_error

    # Cancel idempotent ok return — must NOT be error
    idempotent_ok = "Alert a3f2b8c1 no longer active (already triggered or removed)"
    assert is_tool_error(
        "cancel_price_level_alert", idempotent_ok, outcome="success",
    ) is False

    # Cancel real success — must NOT be error
    cancel_success = 'Price level alert cancelled (id=a3f2b8c1) — "4h structural high"'
    assert is_tool_error(
        "cancel_price_level_alert", cancel_success, outcome="success",
    ) is False

    # Update success — must NOT be error
    update_success = (
        "Price level alert updated (id=a3f2b8c1 → id=d7c2e9f4):\n"
        "  above 82100.00 → above 82500.00 — \"4h structural high\""
    )
    assert is_tool_error(
        "update_price_level_alert", update_success, outcome="success",
    ) is False


def test_update_atomicity_sync_invariant():
    """Spec §5.4 test #9 + AC-13: BaseExchange.add_price_level_alert and
    .remove_price_level_alert must be sync (not async). Pins the §4.2 step 4
    'no yield points' atomicity invariant.
    """
    from src.integrations.exchange.base import BaseExchange

    assert not inspect.iscoroutinefunction(BaseExchange.add_price_level_alert), (
        "BaseExchange.add_price_level_alert must be sync — "
        "update_price_level_alert atomicity depends on this invariant"
    )
    assert not inspect.iscoroutinefunction(BaseExchange.remove_price_level_alert), (
        "BaseExchange.remove_price_level_alert must be sync — "
        "update_price_level_alert atomicity depends on this invariant"
    )


def test_update_view_known_orphan_limitation():
    """Spec §4.2 step 8 + §9: v_alert_lifecycle view sees neither side of an
    update — old id stays as final_status='active' orphan (no cancel CTE row)
    and new id is entirely absent from the view (registers CTE filters
    action='add_price_level_alert' which doesn't match 'update_price_level_alert').

    This test pins the known limitation. If a future change adds dual-emit
    _record_action (candidate (a) in §9 follow-up) or extends the view CTEs,
    the assertion shape changes and the future PR author must consciously
    update this pin (forcing them to confirm the new contract).
    """
    # The action constants documented to NOT trigger view-visibility for update:
    # - 'add_price_level_alert' (registers CTE filter, views.py:99-100)
    # - 'cancel_price_level_alert' (cancels CTE filter, views.py:117-118)
    #
    # update_price_level_alert writes action='update_price_level_alert' which
    # is neither — both CTEs filter it out by construction.

    update_action_literal = "update_price_level_alert"
    add_action_literal = "add_price_level_alert"
    cancel_action_literal = "cancel_price_level_alert"

    # The contract pinned: update's action_name is distinct from the view's
    # filter literals, so the view cannot see update rows on either side.
    assert update_action_literal != add_action_literal
    assert update_action_literal != cancel_action_literal

    # Read the view source and confirm it still filters by the two original
    # literals exclusively (no 'update_price_level_alert' branch added).
    import inspect as _inspect
    from src.storage import views

    view_sql = getattr(views, "V_ALERT_LIFECYCLE_SQL", None)
    assert view_sql is not None, "V_ALERT_LIFECYCLE_SQL constant not found"
    assert f"action='{add_action_literal}'" in view_sql
    assert f"action='{cancel_action_literal}'" in view_sql
    assert f"action='{update_action_literal}'" not in view_sql, (
        "If V_ALERT_LIFECYCLE_SQL now references 'update_price_level_alert', "
        "the §4.2 step 8 known limitation has been resolved — update this "
        "pin to assert the new contract (e.g., new CTE or dual-emit rows)."
    )
```

- [ ] **Step 2: Run all three tests to verify pass**

```bash
pytest tests/test_alert_family.py::test_cancel_idempotent_not_classified_as_error tests/test_alert_family.py::test_update_atomicity_sync_invariant tests/test_alert_family.py::test_update_view_known_orphan_limitation -v
```

Expected: PASS (3 passed). All three pass on first run — Task 5's prefix-tuple fix already made the classification guard green; sync methods are already sync; view SQL filter literals are unchanged.

- [ ] **Step 3: Commit Task 6**

```bash
git add tests/test_alert_family.py
git commit -m "test(iter-w2r2-next-e): pin idempotent-classification + sync + view-orphan invariants

Three drift guards:
- test_cancel_idempotent_not_classified_as_error: pins §5.1.4.3 prefix
  tuple. If a future revert dropped 'Alert ' from the cancel tuple, every
  idempotent ok would silently flip to error in is_tool_error.
- test_update_atomicity_sync_invariant: pins §4.2 step 4 atomicity
  invariant. If a future change makes BaseExchange.add/remove async,
  update_price_level_alert's sequential replace would no longer be
  atomic — guard fires.
- test_update_view_known_orphan_limitation: pins §4.2 step 8 + §9 known
  limitation — V_ALERT_LIFECYCLE_SQL filters 'add_price_level_alert' and
  'cancel_price_level_alert' exclusively, so update writes are invisible
  to the view (both sides). If anyone resolves this via dual-emit or CTE
  extension, the SQL contract changes and this test fires, forcing a
  conscious update to the contract pin.

Spec §5.4 tests #9, #10, #11. ACs 13, 14.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: allowlist comment attribution shift

**Files:**
- Modify: `src/services/tool_call_recorder.py:60` (comment only)

(Note: the legacy `test_cancel_price_level_alert_not_found_records_biz_error` deletion is folded into Task 2 commit per TDD-discipline-keep-suite-green principle. The `tests/test_v_alert_lifecycle.py` narrative comment in the original §7 Task 6 plan does not exist in the actual file — `test_alert_lifecycle_cancel_attempts_aggregation` at lines 95-122 has only a single-line T16.4 docstring with no production-source attribution to update — so that step is dropped.)

- [ ] **Step 1: Update `tool_call_recorder.py:60` comment**

Locate line 60 in `src/services/tool_call_recorder.py`. Change the comment attribution:

```python
# Before:
    "alert_not_found",                # cancel_price_level_alert 状态错（已触发/不存在）

# After:
    "alert_not_found",                # update_price_level_alert 状态错（已触发 / 已被 close-fill 清理 / 未注册）
```

- [ ] **Step 2: Run the wider test suite touch-points to verify no incidental breakage**

```bash
pytest tests/test_alert_family.py tests/test_alert_lifecycle.py tests/test_v_alert_lifecycle.py tests/test_tool_call_recorder.py -v
```

Expected: all pass.

- [ ] **Step 3: Commit Task 7**

```bash
git add src/services/tool_call_recorder.py
git commit -m "chore(iter-w2r2-next-e): allowlist attribution shift cancel→update

src/services/tool_call_recorder.py:60 — comment attribution for
'alert_not_found' shifts from cancel_price_level_alert to
update_price_level_alert. Cancel is now idempotent (no longer emits
this biz_error type); update is the new emitter.

Allowlist set itself unchanged — the enum value 'alert_not_found' is
reused to preserve the cross-period metrics dimension (W2 cancel rate
→ W3 update rate, same series, same enum).

Spec §5.1.5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Docstring rewrites (cancel + update at @tool layer)

**Files:**
- Modify: `src/agent/trader.py` (cancel @tool docstring + update @tool docstring)

Note: the @tool wrappers are at `trader.py:591-614` (cancel) and immediately after (update, added in Task 4). The implementation-side docstrings on the `tools_execution.py` functions are already set in Tasks 2/3.

- [ ] **Step 1: Rewrite cancel @tool wrapper docstring in `trader.py`**

Locate the `@tool` block for `cancel_price_level_alert` (around line 591-614). Replace its docstring with:

```python
        """Cancel a previously-set price level alert by its ID.

        Idempotent: if the alert is no longer active (already triggered
        or auto-cleared by a position-close fill), returns ok with a
        'Note: Alert {id} no longer active' line rather than emitting a
        business error. Format-invalid IDs still reject explicitly.

        Note: alerts at SL/TP levels are auto-cleared when a position
        closes; you usually do not need to call this for that case.

        Args:
            alert_id: 8-char hex id returned by add_price_level_alert
                (also visible in get_active_alerts output as 'id=...').
                Do not use the position index '#N' from get_active_alerts —
                that is for display only.
            reasoning: brief description of why this alert is being
                cancelled.
        """
```

- [ ] **Step 2: Verify the update @tool wrapper docstring already matches §5.3 (set in Task 4 Step 1)**

The docstring inserted in Task 4 Step 1 already says: "Atomic ... preserves the original direction and reasoning ... to change direction or reasoning materially, use cancel + add." This satisfies §5.3 — no further edit needed in this step.

- [ ] **Step 3: Run the docstring drift guard from Iter 5 (require_parameter_descriptions=True)**

```bash
pytest tests/test_pydantic_ai_compliance.py -v 2>/dev/null || pytest tests/ -k "docstring or pydantic_ai or require_parameter" -v
```

Expected: PASS — every @tool has an Args block covering every parameter (existing Iter 5 PR #26 check).

- [ ] **Step 4: Commit Task 8**

```bash
git add src/agent/trader.py
git commit -m "docs(iter-w2r2-next-e): docstring rewrites for cancel + update @tool wrappers

Cancel docstring now states idempotency contract ('Note: Alert {id} no
longer active') so the agent can distinguish from a hard reject. Update
docstring (set in Task 4) names the direction/reasoning preservation +
cancel+add escape hatch for material changes.

Spec §5.3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Final verification (full test suite + spec AC check)

**Files:** none modified.

- [ ] **Step 1: Run the full test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -50
```

Expected: all tests pass. The change is **+10 net** relative to the pre-iter baseline:

- `+13` new tests in `tests/test_alert_family.py`: 2 `_lookup_alert` + 3 cancel behavior + 4 update behavior + 1 dispatch drift guard + 1 sync invariant + 1 idempotent-classification + 1 view-orphan limitation pin.
- `-3` deletions in `tests/test_alert_lifecycle.py`: lines 620-639, 668-677, 761-787 (legacy cancel state-not-found assertions; semantic moves to `test_alert_family.py`).

Net `+13 - 3 = +10`. The absolute "collected" number depends on which baseline you compare against (CLAUDE.md memo notes 1487; auto-memory tradebot-status notes 1525 post-PR-#46 / pre-this-iter) — verify the **delta** relative to `main` rather than the absolute count.

- [ ] **Step 2: Spot-check the AC matrix from spec §6 against test output**

Open the spec at `docs/superpowers/specs/2026-05-12-iter-w2r2-next-e-alert-family-design.md` and scan the AC table (§6, ACs 1-15). For each AC, verify the named test in `tests/test_alert_family.py` exists and passes:

| AC | Test | Verify |
|---|---|---|
| AC-1 | `test_cancel_idempotent_not_found` | passing |
| AC-2 | `test_cancel_format_invalid_still_rejects` | passing |
| AC-3 | `test_cancel_success_includes_reasoning` | passing |
| AC-4 | `test_update_success_preserves_direction_and_reasoning` | passing |
| AC-5 | `test_update_not_found_rejects` | passing |
| AC-6 | `test_update_format_invalid` | passing |
| AC-7 | `test_update_immediate_trigger_allowed` | passing |
| AC-8 | `test_registered_tool_names_matches_agent_tools` (trader_agent) | passing |
| AC-9 | `test_layer1_cross_tool_bullet_count` (persona) | passing, count still 6 |
| AC-10 | code inspection — `tools_execution.py:241-273` body unchanged | manual diff |
| AC-11 | `find migrations/versions -name '*alert*'` returns empty | shell check |
| AC-12 | `test_update_display_dispatch_registered` parser sub-assert | passing |
| AC-13 | `test_update_atomicity_sync_invariant` | passing |
| AC-14 | `test_cancel_idempotent_not_classified_as_error` | passing |
| AC-15 | `test_update_display_dispatch_registered` (3-structure membership) | passing |

Additional drift-guard (not in spec AC table but pinning §4.2 step 8 known limitation): `test_update_view_known_orphan_limitation` — V_ALERT_LIFECYCLE_SQL filters `add_price_level_alert` + `cancel_price_level_alert` exclusively, so update writes are invisible to the view (both sides). If a future PR resolves this via dual-emit or CTE extension, this test fires and the new contract must be re-pinned.

```bash
# AC-10 manual verification:
git diff main -- src/agent/tools_execution.py | grep -E "^[-+]" | grep -E "add_price_level_alert" | head -20

# AC-11 manual verification:
find migrations/versions -name '*alert*' 2>/dev/null  # should print nothing
```

Expected: AC-10 git diff shows no changes inside the `add_price_level_alert` function body (lines 241-273 in the original); AC-11 prints empty.

- [ ] **Step 3: Final summary commit (if any nits found)**

If any small follow-up fixes (typos, missed comments) are discovered during AC verification, batch them into a single commit:

```bash
git add -A
git commit -m "chore(iter-w2r2-next-e): final AC verification cleanup

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If nothing needs fixing, skip this step.

- [ ] **Step 4: Generate the branch change summary**

```bash
git log main..HEAD --oneline
```

Expected: roughly 8 commits (Task 1 through Task 8, plus the spec commit `b8fc094` at the base of the branch).

- [ ] **Step 5: Ready for finishing-a-development-branch skill**

The branch is now complete and ready for the `superpowers:finishing-a-development-branch` skill to decide between merge / PR / cleanup options.

---

## Self-review (writer's checklist)

**Spec coverage:**

- §3 cancel idempotent + F-A3 → Task 2 ✓
- §4 update_price_level_alert → Task 3 ✓
- §5.1.1 REGISTERED_TOOL_NAMES → Task 4 Step 2 ✓
- §5.1.2 test_trader_agent count guard → Task 4 Step 3 ✓
- §5.1.3 test_display_cycle four-point patch → Task 4 Step 4 ✓
- §5.1.4.1 _EXECUTION_TOOL_NAMES frozenset → Task 5 Step 3 ✓
- §5.1.4.2 _EXECUTION_PARSERS + _summarize helper → Task 5 Step 4 ✓
- §5.1.4.3 _EXECUTION_SUCCESS_PREFIXES tuple + update entry → Task 5 Step 5 ✓
- §5.1.5 BIZ_ERROR_TYPES comment → Task 7 Step 1 ✓
- §5.4 tests 1-10 → Tasks 2, 3, 5, 6 ✓
- §6 ACs 1-15 → Task 9 Step 2 verification ✓
- §7 Task 6 (allowlist comment) → Task 7 Step 1 ✓
- §7 Task 7 (legacy test cleanup) → Task 7 Steps 2-3 ✓
- §7 Task 9 (docstrings) → Task 8 ✓

**Placeholder scan:** no TBD / TODO / "similar to" / "implement later" patterns in any step.

**Type consistency:** `_lookup_alert(exchange, alert_id) -> dict | None` signature is consistent across Task 1, Task 2, and Task 3. The update tool's `_record_action` kwarg pattern (`alert_id=new_id`, reasoning concatenation) is identical to spec §4.2 step 8. Return-string shapes in tests match the format strings in tools_execution.py impl.

**Layer-1 unchanged:** no task touches `persona.py` Layer-1 bullets (only `REGISTERED_TOOL_NAMES` in `trader.py`).
