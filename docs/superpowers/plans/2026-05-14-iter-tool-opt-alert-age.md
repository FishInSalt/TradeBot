# Iter tool-opt-alert-age Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land alert `created_at` field + age display in `get_active_alerts` + R2-Next-E §4.4/§4.5 amend (make `update_price_level_alert` an in-place update with stable id, reasoning overwrite, and `created_at` reset).

**Architecture:** alert dict on `BaseExchange._price_level_alerts` gains one new field (`created_at: float`, epoch sec). A new `BaseExchange.update_price_level_alert(alert_id, new_price, new_reasoning) -> bool` method does in-place mutation. The tool layer (`tools_execution.update_price_level_alert`) wires through to the new method (drops the R2-Next-E `remove + add` sequence). `tools_perception.get_active_alerts` renders an age suffix via a new `_fmt_age_humanized(seconds) -> str` helper. The CLI display dispatch (`display.py:_summarize_update_price_level_alert`) regex amends to parse the new single-direction return shape.

**Tech Stack:** Python 3, pytest (existing), pydantic-ai (existing), in-memory `BaseExchange._price_level_alerts: list[dict]` (no DB migration).

**Spec:** `docs/superpowers/specs/2026-05-14-iter-tool-opt-alert-age-design.md`

**Branch:** `iter-tool-opt-alert-age` (already created; spec commit `237ab7f` is the first commit on the branch).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/integrations/exchange/base.py` | Modify | Alert dict gains `created_at`; new `update_price_level_alert` method (in-place) |
| `src/agent/tools_execution.py` | Modify | `update_price_level_alert` tool rewritten to call new BaseExchange method; new return shape |
| `src/agent/tools_perception.py` | Modify | `_fmt_age_humanized` helper (new) + `get_active_alerts` renders age suffix |
| `src/agent/trader.py` | Modify | Wrapper docstring for `update_price_level_alert` updated to in-place semantics |
| `src/cli/display.py` | Modify | `_summarize_update_price_level_alert` regex amended for new single-direction return shape |
| `tests/test_alert_age.py` | Create | New test suite: created_at, in-place update behavior, `_fmt_age_humanized`, age rendering |
| `tests/test_alert_family.py` | Modify | Amend R2-Next-E tests: update-related tests get rewritten/deleted to match new in-place semantics |
| `tests/test_tool_enhancement.py` | Modify (audit) | Existing `get_active_alerts` fixtures + assertions accommodate age suffix |
| `tests/test_display_cycle.py` | Modify (audit) | Existing `get_active_alerts` snapshot + summarize fixtures accommodate age suffix |
| `tests/test_alert_lifecycle.py` | Modify (audit) | Verify no test assumes id-transition or absence of `created_at` |

**Test-fixture pattern across all new tests:** mock `time.time` via `monkeypatch.setattr` on the per-module reference — e.g. `monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: <value>)` (and the analogous patch in `src.agent.tools_perception` for rendering tests). `time` is a module singleton, so the patch is effectively global within the test scope, with automatic teardown by monkeypatch. This keeps tests independent of wall-clock without leaking into siblings.

---

## Task 1: AL-1 — add `created_at` to alert dict

**Files:**
- Modify: `src/integrations/exchange/base.py:1-10` (add `import time`), `src/integrations/exchange/base.py:190-200` (`add_price_level_alert` body)
- Create: `tests/test_alert_age.py`

- [ ] **Step 1: Create `tests/test_alert_age.py` with the first failing test**

Create the new test file. Header + first test:

```python
"""Iter tool-opt-alert-age tests.

Spec: docs/superpowers/specs/2026-05-14-iter-tool-opt-alert-age-design.md

Time mocking pattern: tests patch `time.time` via monkeypatch on the per-module
reference (`time` is a module singleton; patch is test-scoped with auto-teardown).
For BaseExchange tests:
    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: <value>)
For get_active_alerts rendering tests:
    monkeypatch.setattr("src.agent.tools_perception.time.time", lambda: <value>)
"""
from __future__ import annotations

import re
import pytest
from unittest.mock import MagicMock


# ============ Task 1: AL-1 — created_at on add ============

def test_add_price_level_alert_stores_created_at(monkeypatch):
    """Spec §5.1.1 + AC-1: add_price_level_alert writes a created_at: float
    field on the alert dict, equal to time.time() at the call site.
    """
    from src.integrations.exchange.simulated import SimulatedExchange

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10_000.0)

    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h structural high",
    )

    assert alert_id is not None
    alerts = ex.get_price_level_alerts()
    assert len(alerts) == 1
    a = alerts[0]
    assert a["id"] == alert_id
    assert a["price"] == 82_100.0
    assert a["direction"] == "above"
    assert a["symbol"] == "BTC/USDT:USDT"
    assert a["reasoning"] == "4h structural high"
    # AL-1 the new field:
    assert "created_at" in a
    assert a["created_at"] == 1700000000.0
```

- [ ] **Step 2: Run test to verify it FAILS**

Run: `uv run pytest tests/test_alert_age.py::test_add_price_level_alert_stores_created_at -v`

Expected: FAIL with `KeyError: 'created_at'` or `assert "created_at" in a` failing — the alert dict at `base.py:196-199` does not yet include `created_at`.

- [ ] **Step 3: Add `import time` to `base.py` top + write `created_at` field on add**

Edit `src/integrations/exchange/base.py`. Insert `import time` between the existing `import logging` (line 3) and `import uuid` (line 4) per alphabetical order:

```python
import logging
import time   # <-- insert here
import uuid
```

The full import block (lines 1-8) is documented in the spec §11 Code locations.

Then modify `add_price_level_alert` at lines 190-200 from:

```python
def add_price_level_alert(self, price: float, direction: str,
                           symbol: str, reasoning: str) -> str | None:
    """Add a price level alert. Returns alert_id, or None if at limit (20)."""
    if len(self._price_level_alerts) >= 20:
        return None
    alert_id = str(uuid.uuid4())[:8]
    self._price_level_alerts.append({
        "id": alert_id, "price": price, "direction": direction,
        "symbol": symbol, "reasoning": reasoning,
    })
    return alert_id
```

to:

```python
def add_price_level_alert(self, price: float, direction: str,
                           symbol: str, reasoning: str) -> str | None:
    """Add a price level alert. Returns alert_id, or None if at limit (20)."""
    if len(self._price_level_alerts) >= 20:
        return None
    alert_id = str(uuid.uuid4())[:8]
    self._price_level_alerts.append({
        "id": alert_id, "price": price, "direction": direction,
        "symbol": symbol, "reasoning": reasoning,
        "created_at": time.time(),
    })
    return alert_id
```

- [ ] **Step 4: Run test to verify it PASSES**

Run: `uv run pytest tests/test_alert_age.py::test_add_price_level_alert_stores_created_at -v`

Expected: PASS.

- [ ] **Step 5: Run full test suite to confirm no regressions**

Run: `uv run pytest tests/ -x`

Expected: all tests pass. (R2-Next-E tests in `test_alert_family.py` still use mocked alert dicts that omit `created_at` — they will not break because they don't touch the real `add_price_level_alert`.)

- [ ] **Step 6: Commit**

```bash
git add tests/test_alert_age.py src/integrations/exchange/base.py
git commit -m "iter-tool-opt-alert-age: add created_at to alert dict (AL-1)

BaseExchange.add_price_level_alert now writes a created_at: float
(epoch sec, set via time.time()) onto each new alert dict.
get_active_alerts rendering will consume this in a later task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `BaseExchange.update_price_level_alert` new method

**Files:**
- Modify: `src/integrations/exchange/base.py:202-207` (insert new method immediately after `remove_price_level_alert`)
- Modify: `tests/test_alert_age.py` (append new test cases)

- [ ] **Step 1: Add 5 failing tests to `tests/test_alert_age.py`**

Append after the existing `test_add_price_level_alert_stores_created_at`:

```python
# ============ Task 2: BaseExchange.update_price_level_alert ============

def test_update_price_level_alert_is_in_place(monkeypatch):
    """Spec §5.1.2 + AC-2: update is in-place — id is preserved across update."""
    from src.integrations.exchange.simulated import SimulatedExchange

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10_000.0)
    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )

    ok = ex.update_price_level_alert(alert_id, 82_500.0, "tighten level")
    assert ok is True

    alerts = ex.get_price_level_alerts()
    assert len(alerts) == 1
    assert alerts[0]["id"] == alert_id  # id stable


def test_update_price_level_alert_overwrites_price_and_reasoning(monkeypatch):
    """Spec §4.2 + AC-2: update writes new price and new reasoning in place."""
    from src.integrations.exchange.simulated import SimulatedExchange

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10_000.0)
    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )

    ex.update_price_level_alert(alert_id, 82_500.0, "tighten after breakout")

    a = ex.get_price_level_alerts()[0]
    assert a["price"] == 82_500.0
    assert a["reasoning"] == "tighten after breakout"


def test_update_price_level_alert_keeps_direction_and_symbol(monkeypatch):
    """Spec §4.2 + AC-2: direction and symbol survive update unchanged."""
    from src.integrations.exchange.simulated import SimulatedExchange

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10_000.0)
    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )

    # new_price crosses the original level (would-trigger-immediately territory),
    # but direction must not auto-flip.
    ex.update_price_level_alert(alert_id, 81_900.0, "lower level")

    a = ex.get_price_level_alerts()[0]
    assert a["direction"] == "above"  # preserved
    assert a["symbol"] == "BTC/USDT:USDT"  # preserved


def test_update_price_level_alert_resets_created_at(monkeypatch):
    """Spec §4.2 + AC-2: created_at is rewritten to time.time() on update."""
    from src.integrations.exchange.simulated import SimulatedExchange

    # First add at t=1700000000
    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10_000.0)
    alert_id = ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )
    assert ex.get_price_level_alerts()[0]["created_at"] == 1700000000.0

    # Then update at t=1700005000 (5000s later)
    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700005000.0)
    ex.update_price_level_alert(alert_id, 82_500.0, "trail")

    assert ex.get_price_level_alerts()[0]["created_at"] == 1700005000.0


def test_update_price_level_alert_not_found_returns_false(monkeypatch):
    """Spec §5.1.2 + AC-3: unknown alert_id returns False; list unchanged."""
    from src.integrations.exchange.simulated import SimulatedExchange

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700000000.0)
    ex = SimulatedExchange(symbol="BTC/USDT:USDT", initial_balance=10_000.0)
    ex.add_price_level_alert(
        price=82_100.0, direction="above",
        symbol="BTC/USDT:USDT", reasoning="4h high",
    )
    before = list(ex.get_price_level_alerts())

    ok = ex.update_price_level_alert("deadbeef", 82_500.0, "trail")
    assert ok is False

    after = ex.get_price_level_alerts()
    assert after == before  # unchanged
```

- [ ] **Step 2: Run new tests to verify they FAIL**

Run: `uv run pytest tests/test_alert_age.py -v -k update_price_level_alert`

Expected: FAIL with `AttributeError: 'SimulatedExchange' object has no attribute 'update_price_level_alert'` (method does not yet exist).

- [ ] **Step 3: Add `BaseExchange.update_price_level_alert` method**

Edit `src/integrations/exchange/base.py`. Insert the new method immediately after `remove_price_level_alert` (which ends at line 207). After:

```python
def remove_price_level_alert(self, alert_id: str) -> bool:
    for i, a in enumerate(self._price_level_alerts):
        if a["id"] == alert_id:
            self._price_level_alerts.pop(i)
            return True
    return False
```

Insert:

```python
def update_price_level_alert(self, alert_id: str, new_price: float,
                              new_reasoning: str) -> bool:
    """In-place update of an existing price level alert.

    Overwrites price, reasoning, and created_at on the matching alert dict;
    preserves id, direction, and symbol. Returns True if a matching alert
    was found and updated, False otherwise.
    """
    for alert in self._price_level_alerts:
        if alert["id"] == alert_id:
            alert["price"] = new_price
            alert["reasoning"] = new_reasoning
            alert["created_at"] = time.time()
            return True
    return False
```

- [ ] **Step 4: Run new tests to verify they PASS**

Run: `uv run pytest tests/test_alert_age.py -v -k update_price_level_alert`

Expected: all 5 new `update_price_level_alert` tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -x`

Expected: all tests pass. R2-Next-E tests in `test_alert_family.py` are unaffected because the tool layer (`tools_execution.update_price_level_alert`) has not yet been rewritten — it still uses the old `remove + add` path which the R2-Next-E tests mock.

- [ ] **Step 6: Commit**

```bash
git add tests/test_alert_age.py src/integrations/exchange/base.py
git commit -m "iter-tool-opt-alert-age: BaseExchange.update_price_level_alert (in-place)

New method on BaseExchange: in-place mutation of an existing alert dict.
Overwrites price + reasoning + created_at; preserves id + direction +
symbol. Returns True if found, False otherwise. The tool layer
(tools_execution.update_price_level_alert) wires to this in a follow-up.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `tools_execution.update_price_level_alert` rewrite + `display.py` regex amend + `test_alert_family.py` amends

This task is the chunky one. It rewrites the tool to call the new BaseExchange method, amends the CLI parser regex to match the new return shape, and updates the R2-Next-E test surface in lockstep. All changes land as one commit because the return-string shape change is tightly coupled across the impl, the display parser, and the tests.

**Files:**
- Modify: `src/agent/tools_execution.py:357-435` (`update_price_level_alert` body)
- Modify: `src/cli/display.py:245-263` (`_summarize_update_price_level_alert` regex)
- Modify: `tests/test_alert_family.py:160-460` (selected tests)
- Modify: `tests/test_alert_age.py` (append new tool-level tests)

### Step-by-step

- [ ] **Step 1: Add 2 new tests to `tests/test_alert_age.py`** for tool-level shape + biz_error

Append after the Task 2 tests:

```python
# ============ Task 3: update_price_level_alert tool layer ============


@pytest.mark.asyncio
async def test_update_tool_return_string_shape(engine, session_with_row, monkeypatch):
    """Spec §5.2 + AC-4: tool layer success returns the new single-direction shape:
    'Price level alert updated (id={alert_id}): {direction} {old_price} → {new_price} — "{new_reasoning}"'
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder
    from tests.test_tool_call_recorder import make_call, make_ctx, make_deps

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h structural high",
        "created_at": 1700000000.0,
    }]
    deps.exchange.update_price_level_alert.return_value = True

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

    # Shape: 'Price level alert updated (id=AAAA): above 82100.00 → 82500.00 — "..."'
    pattern = re.compile(
        r'^Price level alert updated \(id=[0-9a-f]{8}\): '
        r'(above|below) [\d.]+ → [\d.]+ '
        r'— ".+"$',
        re.DOTALL,
    )
    assert pattern.match(result), f"unexpected shape: {result!r}"

    # Anchored content: single id, preserved direction, new reasoning carried.
    assert "id=a3f2b8c1" in result
    assert "above 82100.00 → 82500.00" in result
    assert '— "trail up after breakout"' in result

    # New shape must NOT contain double direction or id transition.
    assert "→ above" not in result
    assert "id=a3f2b8c1 → id=" not in result

    # Exchange method called once with the new in-place signature.
    deps.exchange.update_price_level_alert.assert_called_once_with(
        "a3f2b8c1", 82500.0, "trail up after breakout",
    )


@pytest.mark.asyncio
async def test_update_tool_emits_biz_error_alert_not_found(
    engine, session_with_row, monkeypatch,
):
    """Spec §5.2 + AC-5: tool layer on not-found emits biz_error alert_not_found
    and returns directive text. Behavior preserved from R2-Next-E."""
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder
    from src.storage.database import get_session
    from src.storage.models import ToolCall
    from sqlalchemy import select
    from tests.test_tool_call_recorder import make_call, make_ctx, make_deps

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
    assert "add_price_level_alert" in result  # directive

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "alert_not_found"

    # No mutation
    deps.exchange.update_price_level_alert.assert_not_called()
```

Both tests above use the shared `engine` and `session_with_row` fixtures from `tests/conftest.py` (same fixtures `test_alert_family.py` consumes); no new fixture is introduced.

- [ ] **Step 2: Amend `tests/test_alert_family.py:160-203` — rename + rewrite `test_update_success_preserves_direction_and_reasoning`**

Replace the existing function (lines 160-203) with:

```python
@pytest.mark.asyncio
async def test_update_success_overwrites_price_and_reasoning_keeps_direction_and_id(
    engine, session_with_row,
):
    """Spec amend §3.3 + AC-4: update is in-place — id preserved, direction
    preserved, price + reasoning overwritten. The tool calls
    BaseExchange.update_price_level_alert once with the new in-place signature.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "4h structural high",
        "created_at": 1700000000.0,
    }]
    deps.exchange.update_price_level_alert.return_value = True

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
    # Single id (no transition) — id-stability.
    assert "id=a3f2b8c1" in result
    assert "id=a3f2b8c1 → id=" not in result
    # Direction preserved (still "above"), single direction token.
    assert "above 82100.00 → 82500.00" in result
    # New reasoning carried — overwrite semantics.
    assert '— "trail up after breakout"' in result

    # BaseExchange.update_price_level_alert called once with the new signature.
    deps.exchange.update_price_level_alert.assert_called_once_with(
        "a3f2b8c1", 82500.0, "trail up after breakout",
    )
    # The old remove+add path must NOT be used.
    deps.exchange.remove_price_level_alert.assert_not_called()
    deps.exchange.add_price_level_alert.assert_not_called()
```

- [ ] **Step 3: Amend `tests/test_alert_family.py:207-244` — update `test_update_not_found_rejects` mock surface**

Replace the existing function (lines 207-244) with:

```python
@pytest.mark.asyncio
async def test_update_not_found_rejects(engine, session_with_row):
    """Spec amend §3.3 + AC-5: update of absent alert_id returns biz_error
    'alert_not_found' with directive to use add_price_level_alert.

    The not-found rejection short-circuits before any BaseExchange mutation.
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
    assert "add_price_level_alert" in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "biz_error"
    assert rows[0].error_type == "alert_not_found"

    # No exchange-level mutation on either the new path or the legacy path.
    deps.exchange.update_price_level_alert.assert_not_called()
    deps.exchange.remove_price_level_alert.assert_not_called()
    deps.exchange.add_price_level_alert.assert_not_called()
```

- [ ] **Step 4: Amend `tests/test_alert_family.py:247-274` — update `test_update_format_invalid` mock surface**

After the body around line 270-274 that already asserts the rejection, add (or amend) the trailing block to:

```python
    # No exchange-level mutation
    deps.exchange.update_price_level_alert.assert_not_called()
    deps.exchange.remove_price_level_alert.assert_not_called()
    deps.exchange.add_price_level_alert.assert_not_called()
```

If those assertions already exist for the old methods, replace them; if not, append. The intent is to gate the format-rejection from any mutation.

- [ ] **Step 5: Amend `tests/test_alert_family.py:277-324` — update `test_update_immediate_trigger_allowed` mock surface**

Replace the existing function body (the `deps.exchange.*.return_value = ...` block and the result assertions, lines 290-318) with:

```python
@pytest.mark.asyncio
async def test_update_immediate_trigger_allowed(engine, session_with_row):
    """Spec amend §3.3 + AC-7: new_price on the trigger-side of current is accepted
    without warning/block (the agent uses immediate-trigger as a strategic re-wake;
    see R2-Next-E §1.4 audit). This is also a drift guard against future addition
    of an immediate-trigger warning in this tool.
    """
    from src.agent.tools_execution import update_price_level_alert
    from src.services.tool_call_recorder import ToolCallRecorder

    recorder = ToolCallRecorder()
    deps = make_deps(engine, session_with_row)
    # Above-alert at 82,100; move to 82,200.
    deps.exchange.get_price_level_alerts.return_value = [{
        "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
        "symbol": "BTC/USDT:USDT", "reasoning": "spring breakout",
        "created_at": 1700000000.0,
    }]
    deps.exchange.update_price_level_alert.return_value = True

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
    assert "may trigger immediately" not in result
    assert "WARNING" not in result

    async with get_session(engine) as db:
        rows = (await db.execute(select(ToolCall))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "ok"
    assert rows[0].error_type is None
```

- [ ] **Step 6: Amend `tests/test_alert_family.py:329-357` — update `test_update_display_dispatch_registered` regex assertion**

Replace the sample text + assertion block (the `sample = (...)` and `parser(sample)` block, lines 345-352) with:

```python
    # New return shape (post iter-tool-opt-alert-age):
    #   "Price level alert updated (id=AAAA): above 82100.00 → 82500.00 — \"reason\""
    sample = (
        'Price level alert updated (id=a3f2b8c1): '
        'above 82100.00 → 82500.00 — "4h structural high"'
    )
    summary = parser(sample)
    assert "above" in summary
    assert "$82,100" in summary
    assert "$82,500" in summary
```

The frozenset / prefix assertions above and below the sample block remain unchanged.

- [ ] **Step 7: Amend `tests/test_alert_family.py:362-389` — `test_cancel_idempotent_not_classified_as_error` update sample text**

Within this test (still pinning is_tool_error classification), update the `update_success` sample string at line 383-386:

```python
    # Update success — must NOT be error (new in-place return shape)
    update_success = (
        'Price level alert updated (id=a3f2b8c1): '
        'above 82100.00 → 82500.00 — "4h structural high"'
    )
    assert is_tool_error(
        "update_price_level_alert", update_success, outcome="success",
    ) is False
```

- [ ] **Step 8: Delete `tests/test_alert_family.py:392-406` — `test_update_atomicity_sync_invariant`**

Delete this function entirely. The sync-atomicity invariant pinned the old `remove + add` sequence; the new path is a single in-place method call, so the invariant no longer applies. The substitute behavior — "update mutates the same dict object, no intermediate state" — is covered by `test_update_price_level_alert_is_in_place` in `test_alert_age.py` (Task 2).

- [ ] **Step 9: Rewrite `tests/test_alert_family.py:409-460` — `test_update_view_known_orphan_limitation` → `test_update_view_chain_connected_after_id_stability`**

Replace the entire `test_update_view_known_orphan_limitation` function (the docstring + body) with:

```python
@pytest.mark.asyncio
async def test_update_view_chain_connected_after_id_stability(engine, session_with_row):
    """Spec amend §3.3 + AC-11: id-stability in update_price_level_alert means
    the v_alert_lifecycle view naturally connects add → update → cancel via the
    stable alert_id — the view's registers CTE catches the add row, the cancels
    CTE catches the cancel row, and they join cleanly. No orphan branch.

    The view SQL is unchanged in this iter; the resolution is structural
    (the same alert_id flows through both CTEs because update preserves the id).
    """
    from sqlalchemy import text
    from src.storage import views
    from src.storage.database import get_session
    from src.storage.models import TradeAction

    # Structural confirmation: the view still filters by the two original
    # action literals (no special update branch was added).
    view_sql = getattr(views, "V_ALERT_LIFECYCLE_SQL", None)
    assert view_sql is not None
    assert "action='add_price_level_alert'" in view_sql
    assert "action='cancel_price_level_alert'" in view_sql
    # No update-specific CTE branch needed because id stays the same.
    assert "action='update_price_level_alert'" not in view_sql

    # End-to-end chain assertion: same alert_id across add → update → cancel
    # appears as a single row in the view with final_status='cancelled'.
    #
    # TradeAction schema (verified against src/storage/models.py:59-77):
    #   - session_id: str (FK to sessions.id; session_with_row fixture
    #     returns the str "sess-test" directly — see tests/conftest.py:33-40)
    #   - cycle_id: str | None (per-cycle correlation; nullable)
    #   - action: str (literal action name)
    #   - alert_id: str | None (8-char hex)
    #   - symbol: str (NOT NULL, no default — must be provided)
    #   - reasoning: str | None
    #   - created_at: datetime with default=_utcnow — omit kwarg to use default
    alert_id = "a3f2b8c1"
    async with get_session(engine) as db:
        db.add(TradeAction(
            session_id=session_with_row,
            cycle_id="cyc-test-1",
            action="add_price_level_alert",
            alert_id=alert_id,
            symbol="BTC/USDT:USDT",
            reasoning="above 82100 | initial",
        ))
        db.add(TradeAction(
            session_id=session_with_row,
            cycle_id="cyc-test-2",
            action="update_price_level_alert",
            alert_id=alert_id,
            symbol="BTC/USDT:USDT",
            reasoning="price 82100 → 82500 | tighten",
        ))
        db.add(TradeAction(
            session_id=session_with_row,
            cycle_id="cyc-test-3",
            action="cancel_price_level_alert",
            alert_id=alert_id,
            symbol="BTC/USDT:USDT",
            reasoning="thesis invalidated",
        ))
        await db.commit()

    async with get_session(engine) as db:
        result = await db.execute(
            text(
                "SELECT alert_id, final_status FROM v_alert_lifecycle "
                "WHERE session_id = :sid"
            ),
            {"sid": session_with_row},
        )
        rows = result.fetchall()

    assert len(rows) == 1
    assert rows[0][0] == alert_id
    assert rows[0][1] == "cancelled"
```

The `import time as _time` line at the top of the test is no longer needed (we removed the `created_at=now` kwargs); drop it from the imports block too. If the v_alert_lifecycle view's `final_status` column name or value enum differs from what's asserted above, adjust the SELECT and the comparison — but the schema-aligned kwargs themselves should now insert cleanly without IntegrityError.

- [ ] **Step 10: Run all amended tests to verify they FAIL**

Run: `uv run pytest tests/test_alert_family.py tests/test_alert_age.py -v`

Expected: the renamed/amended R2-Next-E tests fail. Specifically:
- `test_update_success_overwrites_price_and_reasoning_keeps_direction_and_id` — fails because the current impl returns the old `id=X → id=Y` shape
- `test_update_not_found_rejects` mock-surface assertions — fails because current impl still touches `remove_price_level_alert`/`add_price_level_alert`
- `test_update_immediate_trigger_allowed` mock surface — fails similarly
- `test_update_display_dispatch_registered` — fails because current display regex parses the old shape, not the new
- `test_update_tool_return_string_shape` (new) — fails because current impl returns the old shape
- `test_update_tool_emits_biz_error_alert_not_found` (new) — should pass since not-found rejection is preserved; verify
- `test_update_view_chain_connected_after_id_stability` — depends on TradeAction schema; may pass at the view_sql assertions even before impl

- [ ] **Step 11: Rewrite `src/agent/tools_execution.py:357-435` — `update_price_level_alert` body**

Replace the entire function body (lines 357-435) with:

```python
async def update_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    new_price: float,
    reasoning: str,
) -> str:
    """Update an existing price level alert in place: change its trigger price
    and reasoning. The direction (above/below) cannot change — to flip
    direction, cancel and add a new alert. The alert's id stays the same.

    Args:
        alert_id: 8-char hex id of the existing alert (see get_active_alerts).
        new_price: new trigger price.
        reasoning: new rationale text; overwrites the alert's stored reasoning.
    """
    # Step 1: format validation
    if not re.fullmatch(r"[0-9a-f]{8}", alert_id):
        note_biz_error("invalid_alert_id_format")
        return (
            f"Invalid alert_id format: {alert_id!r}. Expected 8-char hex "
            f"(e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids."
        )

    # Step 2: lookup — capture direction + old_price for the success return.
    # (new_reasoning is the caller's arg; old_reasoning is not needed.)
    alert = _lookup_alert(deps.exchange, alert_id)
    if alert is None:
        note_biz_error("alert_not_found")
        return (
            f"Alert {alert_id} not found. "
            f"To create a new alert, use add_price_level_alert."
        )
    direction = alert["direction"]
    old_price = alert["price"]

    # Step 3: in-place update via the new BaseExchange method
    ok = deps.exchange.update_price_level_alert(alert_id, new_price, reasoning)
    if not ok:
        # Defensive: lookup just succeeded; in-place update should not fail.
        raise RuntimeError(
            f"update_price_level_alert returned False for id={alert_id} "
            f"that was just present in lookup — invariant violated"
        )

    # Step 4: audit row — single alert_id; reasoning records the move
    await _record_action(
        deps, action="update_price_level_alert",
        alert_id=alert_id,
        reasoning=f"price {old_price} → {new_price} | {reasoning}",
    )

    # Step 5: success return — new single-direction shape
    return (
        f"Price level alert updated (id={alert_id}): "
        f"{direction} {old_price:.2f} → {new_price:.2f} "
        f'— "{reasoning}"'
    )
```

- [ ] **Step 12: Amend `src/cli/display.py:245-263` — `_summarize_update_price_level_alert` regex**

Read the current function body around line 245-263 (it follows the R2-Next-E §5.1.4.2 pattern). Replace the regex line and the format-string line:

Before:

```python
m = re.search(r"(above|below)\s+([\d.]+)\s*→\s*(above|below)\s+([\d.]+)", content)
if m:
    return f"{m.group(1)} ${float(m.group(2)):,.0f} → ${float(m.group(4)):,.0f}"
```

After:

```python
m = re.search(r"(above|below)\s+([\d.]+)\s*→\s*([\d.]+)", content)
if m:
    return f"{m.group(1)} ${float(m.group(2)):,.0f} → ${float(m.group(3)):,.0f}"
```

The fallback path (calling `_fallback_summary(content)`) and the prefix entries elsewhere in display.py stay unchanged.

- [ ] **Step 13: Run amended tests to verify they PASS**

Run: `uv run pytest tests/test_alert_family.py tests/test_alert_age.py -v`

Expected: all amended R2-Next-E tests + new tool-level tests pass. If `test_update_view_chain_connected_after_id_stability` still fails on TradeAction schema kwargs, adjust the kwargs per `src/storage/models.py` and rerun.

- [ ] **Step 14: Run full suite to confirm no other regressions**

Run: `uv run pytest tests/ -x`

Expected: all tests pass. Any failure here will be downstream rendering tests (`test_tool_enhancement.py:test_get_active_alerts*`, `test_display_cycle.py:test_summarize_get_active_alerts` / `test_snapshot_get_active_alerts_with_alerts`) — those are addressed in Task 6. If they fail here, that means Task 6 has work to do; do NOT fix them inline.

If a test fails in a file other than the rendering-related ones above, stop and debug — it's a real regression.

- [ ] **Step 15: Commit**

```bash
git add tests/test_alert_age.py tests/test_alert_family.py \
        src/agent/tools_execution.py src/cli/display.py
git commit -m "iter-tool-opt-alert-age: update_price_level_alert in-place rewrite

R2-Next-E §4.4/§4.5 amend: update is now in-place via the new
BaseExchange.update_price_level_alert method. id is preserved
across update; reasoning is overwritten with the caller's new value;
created_at refreshes to time.time(); direction and symbol are immutable.

Return string shape changes: 'Price level alert updated (id=AAAA):
above OLD → NEW — \"new_reasoning\"' (single id, single direction
token). display.py:_summarize_update_price_level_alert regex amended.

R2-Next-E test surface in tests/test_alert_family.py updated to match
the new in-place semantics; test_update_atomicity_sync_invariant
deleted (no longer applicable); test_update_view_known_orphan_limitation
rewritten as test_update_view_chain_connected_after_id_stability —
id-stability resolves the chain.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `trader.py` wrapper docstring sync

**Files:**
- Modify: `src/agent/trader.py:609-631` (`update_price_level_alert` wrapper docstring)

- [ ] **Step 1: Read the current wrapper docstring**

The wrapper at `src/agent/trader.py:609-631` currently contains the R2-Next-E docstring with phrasing "Replace a single existing price level alert with a new price. Atomic: cancels the old alert and creates a new one with new_price, preserving the original direction and reasoning text…".

- [ ] **Step 2: Replace the docstring**

Replace lines 615-628 (the docstring block between the triple-quotes) with:

```python
        """Update an existing price level alert in place: change its trigger
        price and reasoning. The direction (above/below) cannot change —
        to flip direction, cancel and add a new alert. The alert's id stays
        the same. Trail use case: when price moves and you want the same
        alert at a new level, this preserves identity (id, direction) while
        refreshing the price and reasoning.

        Args:
            alert_id: 8-char hex id of the existing alert (see get_active_alerts).
            new_price: new trigger price.
            reasoning: new rationale text; overwrites the alert's stored reasoning.
        """
```

The function signature (lines 609-614) and body (lines 629-631) stay unchanged.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -x`

Expected: all tests pass (docstring change has no behavioral test impact unless a test explicitly grep-asserts docstring text; none such are known).

- [ ] **Step 4: Commit**

```bash
git add src/agent/trader.py
git commit -m "iter-tool-opt-alert-age: sync update_price_level_alert wrapper docstring

The trader.py wrapper docstring carried the R2-Next-E framing
('cancels the old alert and creates a new one ... preserving the
original direction and reasoning'). Rewrite to the in-place semantics
introduced by the impl rewrite: id stable, direction immutable,
price + reasoning overwritten.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `_fmt_age_humanized` helper

**Files:**
- Modify: `src/agent/tools_perception.py:1-10` (add `import time`)
- Modify: `src/agent/tools_perception.py` (insert new helper alongside `_bars_ago_fmt` at ~line 1892)
- Modify: `tests/test_alert_age.py` (append helper tests)

- [ ] **Step 1: Add parametrized failing tests to `tests/test_alert_age.py`**

Append after the Task 3 tests:

```python
# ============ Task 5: _fmt_age_humanized helper ============


@pytest.mark.parametrize("seconds,expected", [
    (0, "just now"),
    (30, "just now"),
    (59, "just now"),
    (60, "1m ago"),
    (61, "1m ago"),
    (119, "1m ago"),
    (120, "2m ago"),
    (3599, "59m ago"),
    (3600, "1h 0m ago"),
    (3660, "1h 1m ago"),
    (7259, "2h 0m ago"),
    (7261, "2h 1m ago"),
    (86399, "23h 59m ago"),
    (86400, "1d 0h ago"),
    (86401, "1d 0h ago"),
    (90000, "1d 1h ago"),
    (172800, "2d 0h ago"),
])
def test_fmt_age_humanized_thresholds(seconds, expected):
    """Spec §5.3.1 + AC-6: humanized duration boundary cases."""
    from src.agent.tools_perception import _fmt_age_humanized
    assert _fmt_age_humanized(seconds) == expected


def test_fmt_age_humanized_negative_clamps_to_just_now():
    """Spec §5.3.1 + AC-7: negative input (clock skew) clamps to 'just now'."""
    from src.agent.tools_perception import _fmt_age_humanized
    assert _fmt_age_humanized(-5) == "just now"
    assert _fmt_age_humanized(-1000) == "just now"


def test_fmt_age_humanized_float_truncates():
    """Spec §5.3.1: fractional seconds truncate via int() — 59.9s is 'just now'."""
    from src.agent.tools_perception import _fmt_age_humanized
    assert _fmt_age_humanized(59.9) == "just now"  # int(59.9) == 59
    assert _fmt_age_humanized(60.5) == "1m ago"
```

- [ ] **Step 2: Run new tests to verify they FAIL**

Run: `uv run pytest tests/test_alert_age.py -v -k fmt_age_humanized`

Expected: FAIL with `ImportError: cannot import name '_fmt_age_humanized'`.

- [ ] **Step 3: Add `import time` to `tools_perception.py` if not already present**

Check `src/agent/tools_perception.py:1-15`. If `import time` is not present at module top, add it (e.g. after `import logging` at line 3).

- [ ] **Step 4: Add the helper function**

Insert `_fmt_age_humanized` in `tools_perception.py` near the existing `_bars_ago_fmt` at line 1892. Append immediately after `_bars_ago_fmt`:

```python
def _fmt_age_humanized(seconds: float) -> str:
    """Render a wall-clock duration as a humanized 'X ago' suffix.

    Thresholds:
      < 60s    → 'just now'
      < 60min  → 'Nm ago'         (e.g. '5m ago')
      < 24h    → 'Hh Mm ago'      (e.g. '2h 15m ago')
      >= 24h   → 'Dd Hh ago'      (e.g. '1d 4h ago')

    seconds is non-negative; negative input (clock skew) clamps to 0.
    """
    s = max(0, int(seconds))
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        h, rem = divmod(s, 3600)
        return f"{h}h {rem // 60}m ago"
    d, rem = divmod(s, 86400)
    return f"{d}d {rem // 3600}h ago"
```

- [ ] **Step 5: Run new tests to verify they PASS**

Run: `uv run pytest tests/test_alert_age.py -v -k fmt_age_humanized`

Expected: 21 PASS (17 parametrized thresholds + 2 negative clamp + 2 float truncate; exact count depends on parametrize tuple count).

- [ ] **Step 6: Run full suite**

Run: `uv run pytest tests/ -x`

Expected: all tests pass. No downstream impact since the helper is not yet wired into `get_active_alerts`.

- [ ] **Step 7: Commit**

```bash
git add tests/test_alert_age.py src/agent/tools_perception.py
git commit -m "iter-tool-opt-alert-age: _fmt_age_humanized helper

Pure function: renders a wall-clock duration as a humanized 'X ago'
suffix. Boundaries: <60s→'just now', <60min→'Nm ago',
<24h→'Hh Mm ago', >=24h→'Dd Hh ago'. Negative input (clock skew)
clamps to 'just now'. Used by get_active_alerts rendering in the
following task.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `get_active_alerts` age rendering + downstream test fixture sweeps

This task changes `get_active_alerts` rendering AND fixes downstream tests in `test_tool_enhancement.py` / `test_display_cycle.py` whose fixtures or assertions break under the new output. All changes land in one commit because the rendering change directly drives the test-fixture changes.

**Files:**
- Modify: `src/agent/tools_perception.py:578-587` (`get_active_alerts` level-alert rendering)
- Modify: `tests/test_alert_age.py` (append rendering tests)
- Modify: `tests/test_tool_enhancement.py` (existing `get_active_alerts` tests — fixtures + assertions)
- Modify: `tests/test_display_cycle.py` (existing `get_active_alerts` summarize + snapshot tests — fixtures + expected)

- [ ] **Step 1: Add failing rendering tests to `tests/test_alert_age.py`**

Append:

```python
# ============ Task 6: get_active_alerts age rendering ============


@pytest.mark.asyncio
async def test_get_active_alerts_renders_age_suffix(monkeypatch):
    """Spec §5.3.2 + AC-8: each level-alert line ends with a humanized age
    suffix like ' (5m ago)' or ' (just now)'.
    """
    from src.agent.tools_perception import get_active_alerts
    from tests.test_tool_call_recorder import make_deps

    # Both modules expose `time.time`; patch both.
    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700005000.0)
    monkeypatch.setattr("src.agent.tools_perception.time.time", lambda: 1700005000.0)

    deps = MagicMock()
    deps.exchange = MagicMock()
    deps.exchange.get_alert_params.return_value = (0.5, 30)
    # Two alerts: one set "now" (just now), one set 300s ago (5m ago).
    deps.exchange.get_price_level_alerts.return_value = [
        {
            "id": "a3f2b8c1", "price": 82100.0, "direction": "above",
            "symbol": "BTC/USDT:USDT", "reasoning": "R1 level",
            "created_at": 1700005000.0,  # just now
        },
        {
            "id": "d7c2e9f4", "price": 81000.0, "direction": "below",
            "symbol": "BTC/USDT:USDT", "reasoning": "S1 level",
            "created_at": 1700004700.0,  # 300s ago
        },
    ]

    output = await get_active_alerts(deps)

    # Header carries the b31ffc3 (@ HH:MM:SS UTC) anchor; body rows carry age.
    age_pat = re.compile(r"\((?:just now|\d+m ago|\d+h \d+m ago|\d+d \d+h ago)\)")
    body_lines = [ln for ln in output.splitlines() if ln.strip().startswith("#")]
    assert len(body_lines) == 2
    for ln in body_lines:
        assert age_pat.search(ln), f"missing age suffix in: {ln!r}"

    # Specific anchors
    assert "(just now)" in output
    assert "(5m ago)" in output


@pytest.mark.asyncio
async def test_get_active_alerts_age_uses_single_now_baseline(monkeypatch):
    """Spec §5.3.2 + AC-9: time.time() is called once per render — all level
    alerts use the same `now` baseline.
    """
    from src.agent.tools_perception import get_active_alerts
    from tests.test_tool_call_recorder import make_deps  # noqa: F401

    call_count = {"n": 0}

    def fake_now():
        call_count["n"] += 1
        return 1700005000.0

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700005000.0)
    monkeypatch.setattr("src.agent.tools_perception.time.time", fake_now)

    deps = MagicMock()
    deps.exchange = MagicMock()
    deps.exchange.get_alert_params.return_value = (0.5, 30)
    deps.exchange.get_price_level_alerts.return_value = [
        {"id": f"{i:08x}", "price": 82000.0 + i, "direction": "above",
         "symbol": "BTC/USDT:USDT", "reasoning": f"#{i}",
         "created_at": 1700004700.0}
        for i in range(4)
    ]

    await get_active_alerts(deps)

    # tools_perception.fetch_ts uses datetime.now(timezone.utc), NOT time.time
    # (verified at tools_perception.py:566-567), so the body baseline `now =
    # time.time()` is the only call into `time.time` during a render. A loop
    # that re-queried time.time per row would push this above 1.
    assert call_count["n"] == 1, (
        f"expected exactly 1 time.time() call (single body baseline), "
        f"got {call_count['n']} — likely re-querying inside the loop"
    )


@pytest.mark.asyncio
async def test_get_active_alerts_disabled_state_unchanged(monkeypatch):
    """Disabled state is unchanged — no level alerts to render, no age."""
    from src.agent.tools_perception import get_active_alerts

    monkeypatch.setattr("src.integrations.exchange.base.time.time", lambda: 1700005000.0)
    monkeypatch.setattr("src.agent.tools_perception.time.time", lambda: 1700005000.0)

    deps = MagicMock()
    deps.exchange = MagicMock()
    deps.exchange.get_alert_params.return_value = None  # disabled
    deps.exchange.get_price_level_alerts.return_value = []

    output = await get_active_alerts(deps)
    assert "OFF" in output
    assert "No active alerts" in output
    # No age suffix anywhere
    assert "ago)" not in output
```

- [ ] **Step 2: Run new tests to verify they FAIL**

Run: `uv run pytest tests/test_alert_age.py -v -k get_active_alerts`

Expected: FAIL — `get_active_alerts` does not yet render an age suffix.

- [ ] **Step 3: Modify `get_active_alerts` rendering**

Edit `src/agent/tools_perception.py:578-587`. Replace:

```python
    alerts = deps.exchange.get_price_level_alerts()
    count = len(alerts)
    lines = [f"=== Price Level Alerts ({count}/20) (@ {fetch_ts} UTC) ==="]
    if alerts:
        for i, a in enumerate(alerts, 1):
            lines.append(f'  #{i} (id={a["id"]}) {a["direction"]} {a["price"]:.2f} — "{a["reasoning"]}"')
    else:
        lines.append("  No active alerts.")
    sections.append("\n".join(lines))
```

with:

```python
    alerts = deps.exchange.get_price_level_alerts()
    count = len(alerts)
    lines = [f"=== Price Level Alerts ({count}/20) (@ {fetch_ts} UTC) ==="]
    if alerts:
        now = time.time()  # single baseline for all rows
        for i, a in enumerate(alerts, 1):
            age = _fmt_age_humanized(now - a["created_at"])
            lines.append(
                f'  #{i} (id={a["id"]}) {a["direction"]} {a["price"]:.2f} '
                f'— "{a["reasoning"]}" ({age})'
            )
    else:
        lines.append("  No active alerts.")
    sections.append("\n".join(lines))
```

Confirm `import time` is at module top (added in Task 5 step 3); if it was not added there because `time` was already imported, do nothing additional.

- [ ] **Step 4: Run new tests to verify they PASS**

Run: `uv run pytest tests/test_alert_age.py -v -k get_active_alerts`

Expected: all 3 rendering tests PASS.

- [ ] **Step 5: Run downstream tests to identify breakage**

Run: `uv run pytest tests/test_tool_enhancement.py tests/test_display_cycle.py -v -k "get_active_alerts or active_alerts"`

Expected: failures in some or all of:
- `tests/test_tool_enhancement.py::test_get_active_alerts_with_data` (line 821) — fixture has no `created_at` → `KeyError`
- `tests/test_tool_enhancement.py::test_get_active_alerts_section_headers_renamed` (line 868) — fixture has no `created_at` → `KeyError`
- `tests/test_display_cycle.py::test_snapshot_get_active_alerts_with_alerts` (line 2229) — fixture content lacks age suffix → snapshot mismatch
- `tests/test_display_cycle.py::test_summarize_get_active_alerts` (line 151) — expected to **pass unchanged** (substring assertions, not affected by body content)

Note the failing tests; fix them in Steps 6-8.

- [ ] **Step 6: Fix `tests/test_tool_enhancement.py` — add `created_at` to alert fixtures**

Locate the alert fixture in `test_get_active_alerts_with_data` at line 826-840. Add `"created_at": 1700000000.0` to each alert dict.

Locate the alert fixture in `test_get_active_alerts_section_headers_renamed` at line 876+. Add `"created_at": <suitable epoch>` to each alert dict.

For any assertion in these tests that checks exact rendered output, switch to a regex match that tolerates the age suffix. Example pattern: `r' \((?:just now|\d+m ago|\d+h \d+m ago|\d+d \d+h ago)\)$'`. If the test only asserts substring presence (e.g. `assert "=== Price Level Alerts (1/20) (@" in output`), no change is needed.

Specifically inspect lines 884-886 of `test_get_active_alerts_section_headers_renamed`:

```python
assert "=== Price Level Alerts (1/20) (@" in output
# (no change to header assertion)
assert "Active Price Level Alerts" not in output  # (no change)
```

If those tests assert specific body line content (e.g. `assert '#1 (id=...) above 82100.00 — "reason"' in output`), add tolerance for the trailing ` ({age})`. Use regex:

```python
import re
body_pat = re.compile(
    r'#1 \(id=[0-9a-f]{8}\) above 82100\.00 — "reason" '
    r'\((?:just now|\d+m ago|\d+h \d+m ago|\d+d \d+h ago)\)'
)
assert body_pat.search(output)
```

- [ ] **Step 7: `tests/test_display_cycle.py:test_summarize_get_active_alerts` — no change needed**

Verified at plan stage (`tests/test_display_cycle.py:151-163`): the test's assertions are pure substring checks — `assert "5.0" in result`, `assert "60" in result`, `assert "2" in result` — none of which touch the level-alert body lines or any age-suffix shape. The fixture content lacks an age suffix but the summarizer's behavior depends on the volatility-alert section header and the level-alert count, not on body-line content. No change is needed for this test.

If the implementing engineer finds during Step 5 that this test does fail (unexpected), inspect the actual failure and reconcile — but the plan-stage read says it will pass unmodified.

- [ ] **Step 8: Fix `tests/test_display_cycle.py:test_snapshot_get_active_alerts_with_alerts`**

Locate the test at line 2229. The fixture has both an `input content` (with `=== Price Volatility Alert ===\n...\n=== Price Level Alerts (2/20) ===\n  #1 (id=...) above ... — "..."\n`) and an `expected` rendered block (with indented `===` headers).

Update the input content block (lines ~2232-2235): append ` (just now)` to each alert body line. Update the expected rendered block (lines ~2241-2244) to mirror the added suffix.

Concretely, for the input content lines:

```python
# Before
"  #1 (id=AAAA) above 82100.00 — \"R1\"\n"
"  #2 (id=BBBB) below 81000.00 — \"S1\"\n"

# After
"  #1 (id=AAAA) above 82100.00 — \"R1\" (just now)\n"
"  #2 (id=BBBB) below 81000.00 — \"S1\" (just now)\n"
```

And the expected rendered block adds matching ` (just now)` on the corresponding lines.

If the test fixture uses parametric content (not literal strings shown above), inspect the actual fixture content variable and adjust accordingly.

- [ ] **Step 9: Run downstream tests again to verify GREEN**

Run: `uv run pytest tests/test_tool_enhancement.py tests/test_display_cycle.py -v -k "active_alerts"`

Expected: all pass.

- [ ] **Step 10: Run full suite**

Run: `uv run pytest tests/ -x`

Expected: all tests pass. If any other test in any other file breaks (most likely in `tests/test_alert_lifecycle.py`), defer the fix to Task 7. If a non-rendering test breaks unexpectedly, debug.

- [ ] **Step 11: Commit**

```bash
git add tests/test_alert_age.py src/agent/tools_perception.py \
        tests/test_tool_enhancement.py tests/test_display_cycle.py
git commit -m "iter-tool-opt-alert-age: render age suffix in get_active_alerts (AA-4)

get_active_alerts level-alert lines now end with a humanized age suffix
(' (just now)' / ' (5m ago)' / ' (2h 15m ago)' / ' (1d 4h ago)') via
the _fmt_age_humanized helper. All rows in a single render share one
time.time() baseline.

Downstream test fixtures in test_tool_enhancement.py and
test_display_cycle.py updated to populate created_at on mocked alerts
and to accommodate the age suffix in assertions / snapshots.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `tests/test_alert_lifecycle.py` audit

**Files:**
- Modify: `tests/test_alert_lifecycle.py` (if needed)

- [ ] **Step 1: Audit the file for breakages**

Run: `uv run pytest tests/test_alert_lifecycle.py -v`

Expected: most tests pass. The tests at lines 47, 439, 576 (per the earlier grep) call `sim.add_price_level_alert(...)` against the real `SimulatedExchange` — these alerts will now include `created_at` automatically, so the existing test bodies should still pass unless they explicitly compare the alert dict by equality (in which case the `created_at` field will appear and any structural-equality assertion will fail).

Likely breakage location: a test that asserts the alert dict's keyset matches a hardcoded set (e.g. `assert set(alert) == {"id", "price", "direction", "symbol", "reasoning"}`). Search for any such pattern.

Run:

```bash
grep -n 'set(.*alert' tests/test_alert_lifecycle.py
grep -n '.keys()' tests/test_alert_lifecycle.py | grep -i alert
```

- [ ] **Step 2: Fix any breakage found**

If a test asserts the alert dict keyset, update the expected set to include `"created_at"`. If a test asserts byte-equal alert content, switch to field-by-field assertions that ignore `created_at` (or assert `created_at > 0`).

If no test breaks (likely outcome — `test_alert_lifecycle.py` is about state transitions, not dict shape), this step is a no-op and no commit is created.

- [ ] **Step 3: Run full suite to confirm**

Run: `uv run pytest tests/ -x`

Expected: all tests pass.

- [ ] **Step 4: Commit (only if Step 2 made changes)**

```bash
git add tests/test_alert_lifecycle.py
git commit -m "iter-tool-opt-alert-age: test_alert_lifecycle audit fix

Update <test name(s)> to accommodate the new created_at field on
alert dicts. Field-by-field assertions replace structural-equality
where applicable.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

If no changes were needed, skip this commit step entirely.

---

## Task 8: Final verification + sanity grep

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v 2>&1 | tail -40`

Expected: All tests pass. The exact count is ~1631 + N where N is the count of new tests added in this iter (≈12 new tests in `test_alert_age.py` after parametrize expansion). Compare with the recent baseline of 1631 passed (from memory `project_tradebot_status`).

- [ ] **Step 2: Sanity grep — old return shape gone**

Run:

```bash
grep -rn "id=[a-f0-9]\{8\} → id=" src/ tests/ 2>/dev/null | grep -v __pycache__
```

Expected: no matches in `src/`. Matches in `tests/` are OK only if they're in commit-history-reading tests (e.g. spec text in docstring) — should be 0 in practice.

- [ ] **Step 3: Sanity grep — `_lookup_alert` still used by cancel**

Run:

```bash
grep -n "_lookup_alert" src/agent/tools_execution.py
```

Expected: 2+ matches. The helper is shared between `cancel_price_level_alert` (R2-Next-E preserved) and `update_price_level_alert` (new in-place path). If only 1 match remains, that means update no longer uses the helper — verify against §5.2 step 2 (it should: `update` still needs to look up `direction` + `old_price` before mutation).

- [ ] **Step 4: Sanity grep — `created_at` only in alert dict path**

Run:

```bash
grep -n "created_at" src/integrations/exchange/base.py
grep -n "created_at" src/agent/tools_perception.py
```

Expected: 2 matches in `base.py` (one in `add_price_level_alert`, one in `update_price_level_alert`). 1 match in `tools_perception.py` (inside the `get_active_alerts` rendering loop).

- [ ] **Step 5: Sanity grep — no Alembic migration touched**

Run:

```bash
find migrations/versions -name '*alert*' -newer docs/superpowers/specs/2026-05-14-iter-tool-opt-alert-age-design.md
```

Expected: 0 files. Alerts remain in-memory; no DB schema migration.

- [ ] **Step 6: Confirm `REGISTERED_TOOL_NAMES` count unchanged**

Run:

```bash
uv run python -c "from src.agent.trader import REGISTERED_TOOL_NAMES; print(len(REGISTERED_TOOL_NAMES))"
```

Expected: `34` (same as iter-10 baseline). Cross-check `tests/test_trader_agent.py` for the drift-guard assertion — it should still pass.

- [ ] **Step 7: Spot-check `get_active_alerts` rendering manually**

Run a quick interactive check:

```bash
uv run python -c "
import asyncio, time
from unittest.mock import MagicMock
from src.agent.tools_perception import get_active_alerts

async def main():
    deps = MagicMock()
    deps.exchange = MagicMock()
    deps.exchange.get_alert_params.return_value = (0.5, 30)
    deps.exchange.get_price_level_alerts.return_value = [
        {'id': 'aaaaaaaa', 'price': 82100.0, 'direction': 'above',
         'symbol': 'BTC/USDT:USDT', 'reasoning': 'R1', 'created_at': time.time() - 300},
        {'id': 'bbbbbbbb', 'price': 81000.0, 'direction': 'below',
         'symbol': 'BTC/USDT:USDT', 'reasoning': 'S1', 'created_at': time.time() - 8000},
    ]
    print(await get_active_alerts(deps))

asyncio.run(main())
"
```

Expected output (approx):

```
=== Price Volatility Alert (@ HH:MM:SS UTC) ===
0.5% in 30min window

=== Price Level Alerts (2/20) (@ HH:MM:SS UTC) ===
  #1 (id=aaaaaaaa) above 82100.00 — "R1" (5m ago)
  #2 (id=bbbbbbbb) below 81000.00 — "S1" (2h 13m ago)
```

Visual check: humanized age, single now baseline, header carries UTC, body carries age.

- [ ] **Step 8: Branch state — confirm spec is the first commit, plan is second, impl is third+**

Run:

```bash
git log --oneline iter-tool-opt-alert-age ^main
```

Expected (top of list = most recent):

```
<sha> iter-tool-opt-alert-age: test_alert_lifecycle audit fix   (optional, only if Task 7 committed)
<sha> iter-tool-opt-alert-age: render age suffix in get_active_alerts (AA-4)
<sha> iter-tool-opt-alert-age: _fmt_age_humanized helper
<sha> iter-tool-opt-alert-age: sync update_price_level_alert wrapper docstring
<sha> iter-tool-opt-alert-age: update_price_level_alert in-place rewrite
<sha> iter-tool-opt-alert-age: BaseExchange.update_price_level_alert (in-place)
<sha> iter-tool-opt-alert-age: add created_at to alert dict (AL-1)
<sha> iter-tool-opt-alert-age: implementation plan
237ab7f iter-tool-opt-alert-age: design spec
```

The plan commit lands as the second commit on the branch (added in plan-stage commit before any impl commit), per `feedback_plan_doc_commit_first`.

---

## Self-Review

After the plan is written, scan against the spec:

**1. Spec coverage** (each section of the spec → task that implements it):

| Spec section | Task | Status |
|---|---|---|
| §2.1 issue → change matrix (AL-1) | Task 1 | ✓ |
| §2.1 issue → change matrix (BaseExchange new method) | Task 2 | ✓ |
| §2.1 issue → change matrix (tools_execution rewrite) | Task 3 | ✓ |
| §2.1 issue → change matrix (display.py regex) | Task 3 | ✓ |
| §2.1 issue → change matrix (_fmt_age_humanized) | Task 5 | ✓ |
| §2.1 issue → change matrix (get_active_alerts) | Task 6 | ✓ |
| §3 R2-Next-E amend rationale | Embedded in Task 3 commit message | ✓ |
| §4.1 alert dict schema | Task 1 (created_at) + Task 2 (in-place writes) | ✓ |
| §4.2 update behavior matrix | Task 2 + Task 3 | ✓ |
| §5.1 BaseExchange changes | Task 1 + Task 2 | ✓ |
| §5.2 tools_execution rewrite | Task 3 | ✓ |
| §5.3 _fmt_age_humanized + rendering | Task 5 + Task 6 | ✓ |
| §5.4 display.py regex | Task 3 | ✓ |
| §5.5 trader.py wrapper docstring | Task 4 | ✓ |
| §6.1 new test file | Tasks 1, 2, 3, 5, 6 (all append to test_alert_age.py) | ✓ |
| §6.2 R2-Next-E test amendments | Task 3 (steps 2-9) | ✓ |
| §6.3 existing test sweep | Task 6 (tool_enhancement, display_cycle) + Task 7 (alert_lifecycle) | ✓ |
| §7 ACs | AC-1 (Task 1), AC-2 (Task 2), AC-3 (Task 2), AC-4 (Task 3), AC-5 (Task 3), AC-6 (Task 5), AC-7 (Task 5), AC-8 (Task 6), AC-9 (Task 6), AC-10 (Task 3), AC-11 (Task 3), AC-12 (Task 8 grep), AC-13 (Task 8 grep) | ✓ |
| §8 PR plan | Task 1-8 ≈ Task 1-8 of spec | ✓ |

No gaps.

**2. Placeholder scan**: zero `TBD` / `TODO` / `fill in` / unconditional `add appropriate X` in the plan. Two explicitly-deferred items are called out and not placeholders:
- Task 3 Step 9 NOTE about `TradeAction(...)` kwargs — explicit verification step with documented fallback (structural-only assertion if schema differs)
- Task 6 Steps 6-8 — fixture content reads "specifically inspect lines X" because the exact line numbers depend on whether iter-10 sectioning has settled the surrounding lines

Both have concrete actions and fallback behavior; not blocking placeholders.

**3. Type consistency**:
- `update_price_level_alert(alert_id, new_price, new_reasoning)` signature on `BaseExchange` (Task 2) matches the tool-layer call site `deps.exchange.update_price_level_alert(alert_id, new_price, reasoning)` (Task 3 step 11): three positional args, `new_reasoning` ↔ tool's `reasoning` parameter ✓
- Helper name `_fmt_age_humanized` consistent across Task 5 (definition), Task 6 (consumer in `get_active_alerts`) ✓
- Return-string shape consistent across Task 3 impl (step 11), Task 3 display regex (step 12), and Task 3 test assertions (steps 1, 6) ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-14-iter-tool-opt-alert-age.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — one fresh subagent per task, review between tasks, fast iteration. Use `superpowers:subagent-driven-development`. Subagent prompt MUST start with `cd /Users/z/Z/TradeBot && git rev-parse --abbrev-ref HEAD` (per memory `feedback_subagent_worktree_cwd` — Agent tool subagents do not inherit worktree cwd).

**2. Inline Execution** — execute tasks in this session via `superpowers:executing-plans`, batch with checkpoints. Lower overhead but no subagent isolation between tasks.

Which approach?
