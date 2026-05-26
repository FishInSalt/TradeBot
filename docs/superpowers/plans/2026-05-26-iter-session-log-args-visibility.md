# Session Log Args Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify session log tool call rendering to function-call syntax (`⚙ tool_name(args)` + body) across all 34 tools; normalize 3 outlier execution tools to remove return-text reasoning duplication; add state-delta on SL/TP.

**Architecture:** Add `_format_args_as_call` helper formatting `args_as_dict()` into `key=value, ...` syntax. Refactor `_render_action` 6 branches into unified head + body dispatch (body is sectioned-or-plain by content, not by tool class). Rename `_render_perception_tool` → `_render_tool_body`. Patch `tools_execution.py` SL/TP for prev state capture, 3 outlier tools for return normalize. Sync `tools_descriptions.py` LLM-visible docstrings to match new return shape (fact-provider principle 1 closure).

**Tech Stack:** Python 3.13 / pydantic-ai 1.78 / pytest / Rich console

**Spec:** `docs/superpowers/specs/2026-05-26-iter-session-log-args-visibility-design.md`

---

## File Structure

| File | Role |
|---|---|
| `src/cli/display.py` | Add `_format_args_as_call` / `_format_arg_value` helpers; refactor `_render_action` to unified dispatch; rename `_render_perception_tool` → `_render_tool_body`; update `_render_tool_body` signature to accept `head_icon` / `head_args` |
| `src/agent/tools_execution.py` | `set_stop_loss` / `set_take_profit` capture `o.trigger_price` and return `(was X)`; 3 outlier tools (`update_price_level_alert` / `set_next_wake` / `set_next_wake_at`) remove reasoning from return |
| `src/agent/tools_descriptions.py` | `SET_NEXT_WAKE_DESCRIPTION` / `SET_NEXT_WAKE_AT_DESCRIPTION` Examples: remove `Reason: ...` suffix (LLM-visible fact-provider sync) |
| `tests/test_args_format.py` | NEW: `_format_args_as_call` unit tests covering all value types + INVALID_JSON_KEY fallback |
| `tests/test_display_cycle.py` | Rebuild 51 byte-equal snapshots (44 `test_snapshot_*` + 3 `test_render_action_*` + 4 `test_format_cycle_output_*`) |
| `tests/test_tools_execution.py` | SL/TP return string assertions + first-set unset case + 3 outlier reasoning-removal assertions |
| `tests/test_alert_age.py` | `test_update_tool_return_string_shape` regex updated (remove `— ".+"$` suffix + content assert) |
| `tests/test_alert_family.py` | Sample strings refresh (line 357, 395) to drop `— "..."` (no behavior change) |
| ~~`scripts/verify_args_visibility_log_size.py`~~ | ~~offline re-render~~ — **dropped**: re-render infeasible (messages not persisted in DB); replaced by manual sample review on live sim (Task 6) |

---

## Task 1: `_format_args_as_call` helper + unit tests

**Files:**
- Modify: `src/cli/display.py` (add helper after existing helper section, ~line 985)
- Test: `tests/test_args_format.py` (NEW)

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_args_format.py`:

```python
"""Unit tests for _format_args_as_call (spec §3.2)."""

import pytest
from src.cli.display import _format_args_as_call


def test_empty_args_renders_parens():
    assert _format_args_as_call("get_position", None) == "get_position()"
    assert _format_args_as_call("get_position", {}) == "get_position()"


def test_str_value_double_quoted():
    assert _format_args_as_call("t", {"timeframe": "15m"}) == 't(timeframe="15m")'


def test_int_value_raw():
    assert _format_args_as_call("t", {"new_price": 76860}) == "t(new_price=76860)"


def test_float_value_preserved_precision():
    assert _format_args_as_call("t", {"threshold_pct": 0.5}) == "t(threshold_pct=0.5)"


def test_bool_value_python_literal():
    assert _format_args_as_call("t", {"force": True}) == "t(force=True)"
    assert _format_args_as_call("t", {"force": False}) == "t(force=False)"


def test_none_value():
    assert _format_args_as_call("t", {"reasoning": None}) == "t(reasoning=None)"


def test_list_str_quoted_inner():
    assert (
        _format_args_as_call("t", {"timeframes": ["1h", "4h", "1d"]})
        == 't(timeframes=["1h", "4h", "1d"])'
    )


def test_list_int_raw_inner():
    assert (
        _format_args_as_call("t", {"levels": [76800, 76900]})
        == "t(levels=[76800, 76900])"
    )


def test_dict_short_inline():
    assert (
        _format_args_as_call("t", {"meta": {"a": 1, "b": "x"}})
        == 't(meta={a: 1, b: "x"})'
    )


def test_dict_long_truncated():
    long_dict = {"meta": {"a": "x" * 50}}
    assert _format_args_as_call("t", long_dict) == "t(meta={...})"


def test_field_order_preserved_from_dict_iteration():
    # Helper preserves dict iteration order; pydantic-ai schema-order is
    # LLM-output dependent (see spec §3.2 / §7.4 — not pydantic-ai contract).
    args = {"a": 1, "b": 2, "c": 3}
    assert _format_args_as_call("t", args) == "t(a=1, b=2, c=3)"


def test_invalid_json_key_fallback():
    """pydantic-ai messages.INVALID_JSON_KEY in args → fallback to tool_name(...)."""
    from pydantic_ai.messages import INVALID_JSON_KEY
    args = {INVALID_JSON_KEY: "<unparseable raw>"}
    assert _format_args_as_call("t", args) == "t(...)"


def test_multi_arg_mixed_types():
    args = {"alert_id": "bf2a9786", "new_price": 76860, "reasoning": "trail up"}
    assert (
        _format_args_as_call("update_price_level_alert", args)
        == 'update_price_level_alert(alert_id="bf2a9786", new_price=76860, reasoning="trail up")'
    )


def test_str_with_embedded_quote_is_escaped():
    """reasoning containing " must not break function-call syntax."""
    args = {"reasoning": 'trail "after" MA reclaim'}
    # json.dumps escapes " → \" so output is parseable
    assert _format_args_as_call("t", args) == 't(reasoning="trail \\"after\\" MA reclaim")'


def test_str_with_backslash_escaped():
    args = {"path": "a\\b"}
    assert _format_args_as_call("t", args) == 't(path="a\\\\b")'


def test_str_with_newline_escaped():
    args = {"text": "line1\nline2"}
    assert _format_args_as_call("t", args) == 't(text="line1\\nline2")'
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
pytest tests/test_args_format.py -v
```

Expected: ImportError (`cannot import name '_format_args_as_call'`)

- [ ] **Step 1.3: Implement `_format_arg_value` and `_format_args_as_call`**

Add to `src/cli/display.py` after the existing parsers section (after line ~985):

```python
import json

def _format_arg_value(v: object) -> str:
    """Format a single arg value per spec §3.2.

    Strings use json.dumps for proper escaping of embedded quotes / control
    chars (e.g. reasoning='trail "after" MA reclaim' must not break syntax).
    """
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        # json.dumps handles " / \ / control-char escape + outputs double-quoted
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_format_arg_value(item) for item in v) + "]"
    if isinstance(v, dict):
        inner = ", ".join(f"{k}: {_format_arg_value(val)}" for k, val in v.items())
        if len(inner) > 40:
            return "{...}"
        return "{" + inner + "}"
    return repr(v)


def _format_args_as_call(tool_name: str, args: dict | None) -> str:
    """Format tool call as Python-like function syntax: tool_name(k=v, k=v).

    Empty args → tool_name(). INVALID_JSON_KEY (pydantic-ai unparseable
    arg) → tool_name(...). reasoning is uniformly retained in head per
    spec §3.2 (known divergence with tool_call_recorder.py:138 DB strip).

    `tool_name` is currently only used for fallback display; future
    extension point for per-tool customization (e.g. PII redaction).
    """
    from pydantic_ai.messages import INVALID_JSON_KEY

    if not args:
        return f"{tool_name}()"
    if INVALID_JSON_KEY in args:
        logger.warning(
            "tool %s args unparseable JSON: %r",
            tool_name, args[INVALID_JSON_KEY],
        )
        return f"{tool_name}(...)"

    parts = [f"{k}={_format_arg_value(v)}" for k, v in args.items()]
    return f"{tool_name}({', '.join(parts)})"
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
pytest tests/test_args_format.py -v
```

Expected: 16 passed (13 base cases + 3 escape edge cases)

- [ ] **Step 1.5: Commit**

```bash
git add src/cli/display.py tests/test_args_format.py
git commit -m "$(cat <<'EOF'
feat(display): add _format_args_as_call helper for function-syntax args

Renders tool calls as Python-like syntax: tool_name(k=v, k=v).
Foundation for unified _render_action dispatch (next task).
Empty args → tool_name(); INVALID_JSON_KEY → tool_name(...).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Rename `_render_perception_tool` → `_render_tool_body`

**Files:**
- Modify: `src/cli/display.py` (definition + all internal calls)
- Modify: all test files referencing `_render_perception_tool` (~40 occurrences total per `grep -rn _render_perception_tool src/ tests/`)

- [ ] **Step 2.1: Find all occurrences**

```bash
grep -rn "_render_perception_tool" /Users/z/Z/TradeBot/src /Users/z/Z/TradeBot/tests
```

Expected: ~40 lines spanning `src/cli/display.py` + multiple test files

- [ ] **Step 2.2: Rename across codebase**

Use the Edit tool with `replace_all=true` per file (do NOT use shell sed — preserve file framework hooks).

For each file containing matches, run Edit:
- `old_string`: `_render_perception_tool`
- `new_string`: `_render_tool_body`
- `replace_all`: `true`

Repeat for every file from Step 2.1 output. Also update the function's docstring inside `src/cli/display.py` line ~446 to remove "perception" mention:

Replace the function's docstring header:
```python
def _render_tool_body(tool_name: str, content: str) -> str:
    """Multi-line section render for tool body (by-content sectioned-or-plain).

    Used by unified _render_action dispatch (spec §3.1 / §3.3). Body
    dispatch is by content (presence of `=== ... ===` markers), not by
    tool class — render_tool_body works for any tool's return.
    ...
```

Keep the rest of the docstring intact (Output format / escape behavior sections).

- [ ] **Step 2.3: Run full test suite to verify rename is consistent**

```bash
pytest tests/test_display_cycle.py -v --tb=short 2>&1 | tail -40
```

Expected: All passing (rename is internal, no behavior change). If any test fails with `NameError: _render_perception_tool` or `AttributeError`, that file was missed in Step 2.2 — find and patch.

- [ ] **Step 2.4: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(display): rename _render_perception_tool to _render_tool_body

Function is about to render execution tool bodies too (next task: unified
dispatch). Rename clarifies it's a by-content body renderer, not a
perception-specific function. Pure rename, no behavior change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Unified `_render_action` dispatch + snapshot rebuild

**Files:**
- Modify: `src/cli/display.py:793-871` `_render_action` (collapse 6 branches into unified head + body)
- Modify: `src/cli/display.py:446-486` `_render_tool_body` (add `head_icon` / `head_args` kwargs)
- Modify: `src/cli/display.py:516` `_SECTIONED_PERCEPTION_TOOL_NAMES` dead field cleanup decision
- Modify: `tests/test_display_cycle.py` — update `_assert_perception_render` helper signature + rebuild all 51 byte-equal snapshots + 4 T-RPT tests + test_dg_2 (if cleanup)

This task is large; split commits within: code change first, snapshot rebuild second.

- [ ] **Step 3.1: Modify `_render_tool_body` to accept head params**

In `src/cli/display.py`, update `_render_tool_body` (post-rename from Task 2):

```python
def _render_tool_body(
    tool_name: str,
    content: str,
    *,
    head_icon: str = "⚙",
    head_args: str | None = None,
) -> str:
    """Multi-line section render for tool body (by-content sectioned-or-plain).

    Used by unified _render_action dispatch (spec §3.1 / §3.3). Body
    dispatch is by content (presence of `=== ... ===` markers), not by
    tool class — render_tool_body works for any tool's return.

    head_args: function-syntax args string (e.g. 'tool(k=v)'). If None,
    falls back to bare tool_name (used only by orphan / pre-refactor
    call sites; new dispatch always passes head_args).

    Output format:
      "  {icon} {head_args}\n"               # head (function syntax)
      "    === {section.header} ===\n"       # (if present)
      "    {body line 1}\n"
      ...
      "\n"                                   # blank between sections
      "    === {next section.header} ===\n"
      ...
    """
    head = head_args if head_args is not None else f"{tool_name}()"
    sections = _parse_sections(content)
    # escape head: when head_args is supplied by _render_action, it contains
    # LLM-written reasoning that may include Rich markup ([bold] / [red] etc.)
    lines = [f"  {head_icon} {escape(head)}"]
    for i, section in enumerate(sections):
        if i > 0:
            lines.append("")
        if section.header is not None:
            lines.append(f"    === {escape(section.header)} ===")
        clipped = _clip_body(section.body)
        for row in clipped:
            if row == "":
                lines.append("")
            else:
                lines.append(f"    {escape(row)}")
    return "\n".join(lines)
```

- [ ] **Step 3.2: Refactor `_render_action` to unified dispatch**

Replace `src/cli/display.py:793-871` `_render_action` body:

```python
def _render_action(
    tool_calls: list,
    returns_lookup: dict,
    cycle_id: str,
) -> str:
    """Render Action section per spec §3.1 unified dispatch.

    Dispatch:
      1. ret None → orphan single-line: `⚙ tool_name() [no return captured]`
      2. is_tool_error → error single-line: `✗ tool_name(args) {fallback}`
      3. happy path (perception / execution / save_memory / drift) →
         unified head `{icon} {args_call}` + body (sectioned or plain
         by content). icon = ✎ for save_memory, ⚙ otherwise.
      4. Drift signal: tool_name not in any registered frozenset → log
         warning (no rendering change; frozenset is drift guard only,
         not dispatch driver — spec §3.1).
    """
    n = len(tool_calls)
    plural = "tool" if n == 1 else "tools"
    lines = [f"\n▾ Action ({n} {plural})"]

    for tcp in tool_calls:
        ret = returns_lookup.get(tcp.tool_call_id)
        if ret is None:
            logger.warning(
                "tool_call_id mismatch for %s in cycle %s",
                tcp.tool_name, cycle_id,
            )
            lines.append(f"  ⚙ {tcp.tool_name}() [no return captured]")
            continue

        content_str = str(ret.content)
        outcome = getattr(ret, "outcome", "success")
        args = tcp.args_as_dict()
        args_call = _format_args_as_call(tcp.tool_name, args)

        # Branch 2: L1 error single-line + ✗
        # escape args_call: reasoning is LLM-written, may contain Rich markup
        # like [bold] / [red] — must not be parsed as markup.
        if is_tool_error(tcp.tool_name, content_str, outcome):
            lines.append(
                f"  ✗ {escape(args_call)} {escape(_fallback_summary(content_str))}"
            )
            continue

        # Drift guard: warn for tools not in any registered frozenset
        # (per spec §3.1 — frozenset is guard only, doesn't drive render).
        if (
            tcp.tool_name != "save_memory"
            and tcp.tool_name not in _EXECUTION_TOOL_NAMES
            and tcp.tool_name not in _PERCEPTION_TOOL_NAMES
        ):
            logger.warning(
                "tool_name %s not in any registered frozenset "
                "(perception / execution / save_memory) — drift signal",
                tcp.tool_name,
            )

        # Unified head + body for all happy-path tools
        icon = "✎" if tcp.tool_name == "save_memory" else "⚙"
        lines.append(
            _render_tool_body(
                tcp.tool_name, content_str,
                head_icon=icon, head_args=args_call,
            )
        )

    return "\n".join(lines)
```

**`_render_tool_body` escapes `head_args` internally** (see Step 3.1 — the `head` line uses `escape(head)` to neutralize Rich markup in reasoning).

- [ ] **Step 3.3: Run targeted unit tests (non-snapshot first)**

```bash
pytest tests/test_args_format.py tests/test_display_cycle.py -v -k "not snapshot and not render_action and not format_cycle_output" 2>&1 | tail -20
```

Expected: All passing (unit tests for helpers not affected; snapshot tests fail as expected — that's Step 3.4).

- [ ] **Step 3.4: Run all snapshot tests and capture failure output**

```bash
pytest tests/test_display_cycle.py -v 2>&1 | tee /tmp/snapshot_failures.txt | tail -10
```

Expected: ~51 failures with diff output showing head form change `⚙ tool_name` → `⚙ tool_name(args)`. Save the output for systematic rebuild.

- [ ] **Step 3.5a: Update `_assert_perception_render` helper signature**

`tests/test_display_cycle.py:1625-1633` `_assert_perception_render` currently calls `_render_perception_tool(tool_name, content)` with no `args` — but post-rename to `_render_tool_body` + Task 3 head extension, the helper must pass `head_args` to produce the new `⚙ tool_name(args)` head form.

Edit the helper:

```python
def _assert_perception_render(
    tool_name: str,
    content: str,
    expected: str,
    args: dict | None = None,
):
    """Helper: run _render_tool_body and assert output equals expected.

    args: optional dict for head function-syntax rendering. When provided,
    helper formats via _format_args_as_call to mirror real dispatch.
    Defaults to None → bare tool_name() head form (per spec §2.2 empty
    args rendered as parens for visual consistency).
    """
    from src.cli.display import _render_tool_body, _format_args_as_call
    head_args = _format_args_as_call(tool_name, args)
    actual = _render_tool_body(
        tool_name, content,
        head_args=head_args,
    )
    assert actual == expected, (
        f"Render mismatch for {tool_name}:\n"
        f"--- expected ---\n{expected}\n"
        f"--- actual ---\n{actual}"
    )
```

- [ ] **Step 3.5b: Rebuild 44 `test_snapshot_*` perception tests**

Each `test_snapshot_*` calls `_assert_perception_render(tool_name, content, expected)`. After Step 3.5a, missing `args=` defaults to `None` → head becomes `⚙ tool_name()` (empty parens).

For each `test_snapshot_*` test:
1. Locate the `expected` golden string literal
2. Update the first line: `⚙ tool_name` → `⚙ tool_name()` (空 args 加括号 per spec §2.2)
3. Body unchanged
4. (Optional but recommended) if test has natural args (e.g. `get_market_data(timeframe="15m", candle_count=30)`), update test call site to pass `args=` and update expected head to include args — keeps fixture realistic. Skip for tests where empty args is intent.

**Iteration approach:** Run `pytest tests/test_display_cycle.py -k test_snapshot_get_market_data -v` per test name, update expected, repeat.

- [ ] **Step 3.5c: Update 4 T-RPT tests (`test_render_perception_tool_*`)**

`tests/test_display_cycle.py:1433-1494` 4 tests directly call `_render_perception_tool(tool_name, content)` (post-rename: `_render_tool_body`). After Task 2 rename, calls become `_render_tool_body(tool_name, content)` — `head_args` defaults to None → head becomes `⚙ tool_name()`.

For each:
- `test_render_perception_tool_single_section` (line 1433): expected `⚙ get_account_balance` → `⚙ get_account_balance()`
- `test_render_perception_tool_multi_section_blank_separator` (line 1450): expected `⚙ get_market_data` → `⚙ get_market_data()`
- `test_render_perception_tool_dense_section_clipped` (line 1473): expected `⚙ get_market_data` → `⚙ get_market_data()`
- `test_render_perception_tool_fallback_no_header` (line 1485): expected `⚙ get_memories` → `⚙ get_memories()`

Also rename the test function names to `test_render_tool_body_*` for consistency (4 occurrences).

- [ ] **Step 3.5d: Rebuild `test_render_action_*` (3 tests) and `test_format_cycle_output_*` (4 tests)**

These integration tests construct full `tool_calls + returns` fixtures with ToolCallPart args populated. The head form changes from `⚙ tool_name` / `⚙ tool_name {summary}` (execution) to `⚙ tool_name(actual_args_from_fixture)`.

For each:
1. Read the fixture's ToolCallPart args dict
2. Update the `expected` golden string head line to `⚙ tool_name(args_formatted)` using actual fixture args
3. For execution tool integration tests, the body may need rebuilding too (previously single-line `summary` form, now body is from `tool_return.content` per by-content dispatch)

- [ ] **Step 3.5e: Decide `_SECTIONED_PERCEPTION_TOOL_NAMES` cleanup**

`src/cli/display.py:516` `_SECTIONED_PERCEPTION_TOOL_NAMES = _PERCEPTION_TOOL_NAMES` is a legacy alias. After by-content dispatch (Step 3.2), this field is functionally dead (frozenset retained as drift guard only — `_PERCEPTION_TOOL_NAMES` alone suffices). But `test_dg_2_dispatch_sets_partition_all_registered_tools` (test_display_cycle.py:1500-1515) still imports/uses it.

**Decision: keep field + test as drift guard** (matches spec §3.1 "frozenset retained as drift guard"). Update the field's inline comment to mark legacy purpose:

```python
# Legacy alias retained for drift-guard partition test (test_dg_2_*).
# Post-iter-session-log-args-visibility: dispatch is by-content, this set
# no longer drives sectioned/plain rendering. Field kept (not deleted)
# because partition test asserts frozenset coverage of all registered tools.
_SECTIONED_PERCEPTION_TOOL_NAMES: frozenset[str] = _PERCEPTION_TOOL_NAMES
```

No test changes needed for `test_dg_2_*` — its partition assertion remains valid (it tests that perception + execution + save_memory frozensets cover all registered tools, independent of dispatch logic).

- [ ] **Step 3.6: Verify all 51 snapshot tests pass**

```bash
pytest tests/test_display_cycle.py -v 2>&1 | tail -10
```

Expected: 0 failed, all 51 byte-equal tests pass.

- [ ] **Step 3.7: Run full test suite**

```bash
pytest -x 2>&1 | tail -20
```

Expected: All passing. If any test fails outside `test_display_cycle.py` / `test_args_format.py`, it's likely a snapshot in another file affected by dispatch refactor — find and fix.

- [ ] **Step 3.8: Commit (split into 2 commits: code + snapshots)**

```bash
git add src/cli/display.py
git commit -m "$(cat <<'EOF'
refactor(display): unify _render_action dispatch with function-syntax head

Collapses 6 dispatch branches (orphan/error/save_memory/execution/perception/
drift) into 3-branch unified pipeline:
- orphan: tool_name() [no return captured]
- error: ✗ tool_name(args) {fallback}
- happy path (incl save_memory + drift): {icon} tool_name(args) + body

Body dispatch is by content (=== ... === markers), not by tool class.
frozenset _PERCEPTION_TOOL_NAMES / _EXECUTION_TOOL_NAMES retained as
drift guard only (warn for unregistered tools; doesn't drive render).

Spec §3.1 / §3.3 / §3.5 design closure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"

git add tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
test(display): rebuild 51 snapshots + helper + T-RPT for function-syntax head

Head form changed: ⚙ tool_name → ⚙ tool_name(args). Updates:
- _assert_perception_render helper signature: accepts optional args dict,
  forwards to _render_tool_body as head_args
- 44 test_snapshot_* expected strings: head line ⚙ tool_name → ⚙ tool_name()
- 4 test_render_perception_tool_* (renamed to test_render_tool_body_*):
  same head form update
- 3 test_render_action_* + 4 test_format_cycle_output_*: head + body
  rebuild per fixture args + new body content
- _SECTIONED_PERCEPTION_TOOL_NAMES retained with legacy alias comment
  (drift guard partition test_dg_2_* still uses it)

No body changes for SL/TP/3-outlier tools (those come in tasks 4-5).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: SL/TP return state-delta

**Files:**
- Modify: `src/agent/tools_execution.py:173-202` `set_stop_loss`
- Modify: `src/agent/tools_execution.py:205-234` `set_take_profit`
- Modify: `tests/test_tools_execution.py` (assertions)
- Modify: `tests/test_display_cycle.py` (snapshot rebuild for SL/TP-affected tests)

- [ ] **Step 4.1: Write the failing test for SL prev-state capture**

Add to `tests/test_tools_execution.py`:

```python
async def test_set_stop_loss_includes_prev_sl_when_existing():
    """set_stop_loss return includes (was X) when an existing stop order is replaced."""
    deps = _make_deps_with_position_and_existing_stop(prev_sl=77100.00)
    result = await set_stop_loss(deps, price=76950.00, reasoning="trail up after MA reclaim")
    assert "(was 77100.00)" in result
    assert "Stop loss set at 76950.00" in result


async def test_set_stop_loss_unset_when_no_existing():
    """set_stop_loss return includes (was unset) when no prior stop order."""
    deps = _make_deps_with_position_no_stop()
    result = await set_stop_loss(deps, price=76950.00, reasoning="initial SL after entry")
    assert "(was unset)" in result
    assert "Stop loss set at 76950.00" in result


async def test_set_take_profit_includes_prev_tp_when_existing():
    deps = _make_deps_with_position_and_existing_tp(prev_tp=76300.00)
    result = await set_take_profit(deps, price=76200.00, reasoning="extend target")
    assert "(was 76300.00)" in result


async def test_set_take_profit_unset_when_no_existing():
    deps = _make_deps_with_position_no_tp()
    result = await set_take_profit(deps, price=76200.00, reasoning="initial TP")
    assert "(was unset)" in result
```

If `_make_deps_with_position_and_existing_stop` etc. don't exist, build them. Reference existing fixture patterns in `tests/test_tools_execution.py`.

- [ ] **Step 4.2: Run new tests to verify they fail**

```bash
pytest tests/test_tools_execution.py -v -k "set_stop_loss_includes_prev or set_stop_loss_unset or set_take_profit_includes or set_take_profit_unset" 2>&1 | tail -10
```

Expected: 4 FAILED (assertions about `(was X)` not matching).

- [ ] **Step 4.3: Implement SL prev-state capture**

Update `src/agent/tools_execution.py` `set_stop_loss`:

```python
async def set_stop_loss(deps: TradingDeps, price: float, reasoning: str) -> str:
    """Set stop loss on current position. Auto-cancels existing stop orders."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No open position to set stop loss on."
    p = positions[0]

    # Cancel existing stop orders + capture prev SL trigger_price for return
    # (per spec §3.4: use trigger_price not price — base.py:54 R2-7 §4.7
    # algo class contract; price is overloaded for limit-as-stop futures).
    # Multiple stops is a rare case; take the last (matches cancel sequence).
    prev_sl: float | None = None
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "stop":
            prev_sl = o.trigger_price
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)

    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="stop", amount=p.contracts, price=price,
    )
    deps.exchange.register_close_order_entry(order.id, p.entry_price)

    await _record_action(
        deps, action="set_stop_loss", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    ticker = await deps.market_data.get_ticker(deps.symbol)
    trigger_ref = deps.exchange.algo_trigger_reference
    prev_str = f"(was {prev_sl:.2f}) " if prev_sl is not None else "(was unset) "
    if ticker.last > 0:
        dist_pct = (price - ticker.last) / ticker.last * 100
        return (
            f"Stop loss set at {price:.2f} {prev_str}"
            f"({dist_pct:+.2f}% from {trigger_ref} price {ticker.last:.2f}) | Order: {order.id}"
        )
    return f"Stop loss set at {price:.2f} {prev_str}| Order: {order.id}"
```

- [ ] **Step 4.4: Implement TP prev-state capture (symmetric to SL)**

Update `src/agent/tools_execution.py` `set_take_profit`:

```python
async def set_take_profit(deps: TradingDeps, price: float, reasoning: str) -> str:
    """Set take profit on current position. Auto-cancels existing take profit orders."""
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions:
        return "No open position to set take profit on."
    p = positions[0]

    # Cancel existing take profit orders + capture prev TP trigger_price
    prev_tp: float | None = None
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "take_profit":
            prev_tp = o.trigger_price
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)

    side = "sell" if p.side == "long" else "buy"
    order = await deps.exchange.create_order(
        symbol=deps.symbol, side=side, order_type="take_profit", amount=p.contracts, price=price,
    )
    deps.exchange.register_close_order_entry(order.id, p.entry_price)

    await _record_action(
        deps, action="set_take_profit", order_id=order.id,
        side=p.side, price=price, reasoning=reasoning,
    )

    ticker = await deps.market_data.get_ticker(deps.symbol)
    trigger_ref = deps.exchange.algo_trigger_reference
    prev_str = f"(was {prev_tp:.2f}) " if prev_tp is not None else "(was unset) "
    if ticker.last > 0:
        dist_pct = (price - ticker.last) / ticker.last * 100
        return (
            f"Take profit set at {price:.2f} {prev_str}"
            f"({dist_pct:+.2f}% from {trigger_ref} price {ticker.last:.2f}) | Order: {order.id}"
        )
    return f"Take profit set at {price:.2f} {prev_str}| Order: {order.id}"
```

- [ ] **Step 4.5: Run new tests to verify they pass**

```bash
pytest tests/test_tools_execution.py -v -k "set_stop_loss_includes_prev or set_stop_loss_unset or set_take_profit_includes or set_take_profit_unset" 2>&1 | tail -10
```

Expected: 4 PASSED.

- [ ] **Step 4.6: Run full tests and rebuild affected snapshots**

```bash
pytest tests/test_tools_execution.py tests/test_display_cycle.py -v 2>&1 | tail -20
```

If snapshot tests for SL/TP-rendered content fail, locate them (typically `test_snapshot_set_stop_loss*` / `test_snapshot_set_take_profit*` / `test_render_action_*` integration cases), and update the `expected` golden string to include `(was X)` / `(was unset)` per Step 4.3-4.4 return shape.

- [ ] **Step 4.7: Run full test suite**

```bash
pytest -x 2>&1 | tail -10
```

Expected: All passing.

- [ ] **Step 4.8: Commit**

```bash
git add src/agent/tools_execution.py tests/test_tools_execution.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat(tools_execution): SL/TP return includes prev state delta

set_stop_loss and set_take_profit now include '(was X)' or '(was unset)'
in return string by capturing trigger_price of the existing stop/TP
order before cancellation. Adds forensic state-delta context (spec §3.4).

Use o.trigger_price (not o.price) per base.py:54 R2-7 §4.7 algo class
contract — avoids future limit-as-stop ambiguity.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 3 outlier tools reasoning normalize + LLM-visible sync

**Files:**
- Modify: `src/agent/tools_execution.py:478-482` `update_price_level_alert` (remove `— "reasoning"` suffix)
- Modify: `src/agent/tools_execution.py:510` `set_next_wake` (remove `. Reason: reasoning`)
- Modify: `src/agent/tools_execution.py:565-568` `set_next_wake_at` (remove `. Reason: reasoning`)
- Modify: `src/agent/tools_descriptions.py:13-45` (sync `SET_NEXT_WAKE_DESCRIPTION` + `SET_NEXT_WAKE_AT_DESCRIPTION` Examples)
- Modify: `src/cli/display.py:266` `_summarize_set_next_wake_at` docstring (remove `Reason: ...` mention)
- Modify: `tests/test_alert_age.py:207-218` `test_update_tool_return_string_shape` (regex update)
- Modify: `tests/test_alert_family.py:357, 395` (sample string refresh)
- Modify: `tests/test_tools_execution.py` (3 outlier return assertions)
- Modify: `tests/test_display_cycle.py` (3 outlier snapshot rebuild)

- [ ] **Step 5.1: Write the failing test for `update_price_level_alert` reasoning removal**

Add to `tests/test_tools_execution.py`:

```python
async def test_update_price_level_alert_return_no_reasoning_suffix():
    """spec §3.6: return is state-only, no `— "reasoning"` suffix."""
    deps = _make_deps_with_existing_alert(alert_id="a3f2b8c1", price=82100.00, direction="above")
    result = await update_price_level_alert(
        deps, alert_id="a3f2b8c1", new_price=82500.00,
        reasoning="trail up after breakout",
    )
    assert "82100.00 → 82500.00" in result
    assert "—" not in result  # no em-dash reasoning suffix
    assert "trail up" not in result  # reasoning NOT echoed


async def test_set_next_wake_return_no_reasoning_suffix():
    deps = _make_deps_basic()
    result = await set_next_wake(deps, minutes=18, reasoning="check 4h close")
    assert "Next wake set to 18 min" in result
    assert "Reason:" not in result
    assert "check 4h" not in result


async def test_set_next_wake_at_return_no_reasoning_suffix():
    deps = _make_deps_basic()
    result = await set_next_wake_at(deps, target_time="20:10", reasoning="align with candle close")
    assert "20:10" in result
    assert "in" in result and "min" in result
    assert "Reason:" not in result
    assert "align with" not in result
```

- [ ] **Step 5.2: Run new tests to verify they fail**

```bash
pytest tests/test_tools_execution.py -v -k "no_reasoning_suffix" 2>&1 | tail -10
```

Expected: 3 FAILED.

- [ ] **Step 5.3: Update `update_price_level_alert` return**

In `src/agent/tools_execution.py`, replace the return block at lines 478-482:

```python
    # Step 5: success return — state-only (reasoning normalized to head args per spec §3.6)
    return (
        f"Price level alert updated (id={alert_id}): "
        f"{direction} {old_price:.2f} → {new_price:.2f}"
    )
```

- [ ] **Step 5.4: Update `set_next_wake` return**

In `src/agent/tools_execution.py`, replace line 510:

```python
    return f"Next wake set to {minutes} min"
```

- [ ] **Step 5.5: Update `set_next_wake_at` return**

In `src/agent/tools_execution.py`, replace the return block at lines 565-568:

```python
    return f"Next wake set for {candidate_label} UTC (in {delta_minutes} min)"
```

- [ ] **Step 5.6: Run tools_execution tests to verify they pass**

```bash
pytest tests/test_tools_execution.py -v -k "no_reasoning_suffix" 2>&1 | tail -10
```

Expected: 3 PASSED.

- [ ] **Step 5.7: Sync LLM-visible docstrings (`tools_descriptions.py`)**

Edit `src/agent/tools_descriptions.py` `SET_NEXT_WAKE_DESCRIPTION` at line ~17-26 — change Example outputs to remove `. Reason: ...`:

```python
SET_NEXT_WAKE_DESCRIPTION = """Schedule the next scheduler wake-up after a relative minute interval.

Returns a confirmation, or a reject message describing the violation. Alerts, fills, and conditional triggers always interrupt scheduled wake.

Examples:
    set_next_wake(15, "consolidation phase, check in 15 min")
    → "Next wake set to 15 min"

    set_next_wake(90, "...")
    → "Cannot set wake to 90 min: exceeds wake_max=60 min for this session."

    set_next_wake(0, "...")
    → "Cannot set wake to 0 min: below wake_min=1 min."
"""
```

Edit `SET_NEXT_WAKE_AT_DESCRIPTION` at line ~33-44 — change first Example output line:

```python
SET_NEXT_WAKE_AT_DESCRIPTION = """Schedule the next scheduler wake-up at an absolute UTC time.

Returns a confirmation containing the resolved date-time, or a reject message describing the violation. Alerts, fills, and conditional triggers always interrupt scheduled wake.

Examples:
    set_next_wake_at("10:37", "align with 1h candle close at 11:00 UTC")
    → "Next wake set for 2026-05-12 10:37 UTC (in 14 min)"

    set_next_wake_at("12:00", "...")
    → "Cannot wake at 12:00 UTC: nearest future 2026-05-12 12:00 UTC (in 97 min) exceeds wake_max=60 min for this session."

    set_next_wake_at("10:23", "...")  # now=10:23, resolves to tomorrow
    → "Cannot wake at 10:23 UTC: nearest future 2026-05-13 10:23 UTC (in 1440 min) exceeds wake_max=60 min for this session."

    set_next_wake_at("foo", "...")
    → "Invalid target_time format: 'foo'. Expected 'HH:MM' UTC with 2-digit hour and minute (e.g., '10:37' or '03:05')."
"""
```

- [ ] **Step 5.8: Sync `_summarize_set_next_wake_at` docstring**

In `src/cli/display.py:266`, replace the docstring line:

Old:
```python
    """Parse 'Next wake set for YYYY-MM-DD HH:MM UTC (in N min). Reason: ...'."""
```

New:
```python
    """Parse 'Next wake set for YYYY-MM-DD HH:MM UTC (in N min)'."""
```

- [ ] **Step 5.9: Update `tests/test_alert_age.py:207-218`**

Edit `tests/test_alert_age.py` `test_update_tool_return_string_shape`:

Old regex (lines 207-211):
```python
    pattern = re.compile(
        r'^Price level alert updated \(id=[0-9a-f]{8}\): '
        r'(above|below) [\d.]+ → [\d.]+ '
        r'— ".+"$',
        re.DOTALL,
    )
```

New regex (drop reasoning suffix):
```python
    pattern = re.compile(
        r'^Price level alert updated \(id=[0-9a-f]{8}\): '
        r'(above|below) [\d.]+ → [\d.]+$',
        re.DOTALL,
    )
```

Old asserts (line 218):
```python
    assert '— "trail up after breakout"' in result
```

Delete this assertion entirely (reasoning is no longer in return).

- [ ] **Step 5.10: Refresh `tests/test_alert_family.py` sample strings**

Look at `tests/test_alert_family.py` lines 357 and 395 (the `update_success` sample strings). They contain `— "4h structural high"` or similar reasoning suffixes. Remove the `— "..."` portion to keep samples accurate. This is a read-only refresh — these strings are inputs for `is_tool_error` checks, behavior is unchanged.

Example edit at line 357 (and similarly at line 395):

Old:
```python
update_success = 'Price level alert updated (id=abc12345): above 82100.00 → 82500.00 — "4h structural high"'
```

New:
```python
update_success = 'Price level alert updated (id=abc12345): above 82100.00 → 82500.00'
```

- [ ] **Step 5.11: Run full test suite and rebuild affected snapshots**

```bash
pytest tests/test_tools_execution.py tests/test_alert_age.py tests/test_alert_family.py tests/test_display_cycle.py -v 2>&1 | tail -30
```

For any snapshot test failures involving `update_price_level_alert` / `set_next_wake` / `set_next_wake_at` (typically `test_snapshot_set_next_wake*` / `test_snapshot_update_price_level_alert*` / integration tests like `test_render_action_*` containing these tools), update the `expected` golden string body to remove `. Reason: ...` / `— "..."`.

- [ ] **Step 5.12: Run full test suite**

```bash
pytest -x 2>&1 | tail -10
```

Expected: All passing.

- [ ] **Step 5.13: Commit**

```bash
git add src/agent/tools_execution.py src/agent/tools_descriptions.py src/cli/display.py tests/
git commit -m "$(cat <<'EOF'
feat(tools): normalize 3 outlier tools return to state-only (no reasoning echo)

update_price_level_alert / set_next_wake / set_next_wake_at all had
'Reason: {reasoning}' / '— "{reasoning}"' suffix in return. Per spec
§3.6 (tool-design-principles §1 fact-provider), return is fact + state
change, not an echo of args. reasoning is single-point displayed in
head args ('⚙ tool(reasoning="...")') by unified _render_action.

Sync LLM-visible docstrings in tools_descriptions.py (else spec self-
contradicts: claims principle 1 closure but docstring still describes
the old shape — H3 fix from third review round).

display.py:266 _summarize_set_next_wake_at docstring sync (parser regex
unaffected, docstring description accuracy only).

Note: test_alert_family.py sample string refresh (lines 357, 395) is
read-only — strings are inputs to is_tool_error tests; success_prefix
match unchanged, no behavior change. Sample accuracy refresh only.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Manual structural verify (impl 后 sample-based review)

**Files:** None (manual review on running sim; no new code).

**Rationale (Why no automated script):**

Initial plan had `scripts/verify_args_visibility_log_size.py` to offline re-render W4 sim cycles for byte-comparison. **This is physically infeasible**:

- `format_cycle_output` requires full `list[ModelResponse]` containing `ToolCallPart` + `ToolReturnPart`
- pydantic-ai messages are **in-memory only** — never persisted to DB
- `agent_cycles` table only stores `reasoning` + `decision` + `state_snapshot` (cycle_capture output, not the message list)
- `tool_calls.args` is persisted but `tool_returns` content is not
- Any DB-based "re-render" would compare identical fields between main and PR branches → inflation always 0%

**Replacement verify approach: manual sample review on live sim post-impl** (per spec §5.3 manual-only path; §7.1 theoretical estimate +3-8% accepted as soft estimate; §8 threshold "< 15%" relaxed to "spot-check no surprise inflation").

- [ ] **Step 6.1: Wait for impl to land on a sim**

After Task 5 commit, restart the W4 sim (or any sim) with this branch's code. Let it run ≥ 5-10 cycles to accumulate a representative sample.

(If W4 sim #11 `715d3e81` is still running on main branch code, this iter's render changes do NOT affect its existing log file. You need a fresh sim start with the new branch checked out.)

```bash
git switch iter-session-log-args-visibility
# Start a fresh sim or resume an existing paused sim via CLI wizard
# Let it run >=5-10 cycles (~30-60 min at 5min scheduler interval)
```

- [ ] **Step 6.2: Locate the live sim's session log**

```bash
ls -lt logs/session_*.log | head -3
```

Identify the most recently modified session log (created/updated after this branch checkout).

- [ ] **Step 6.3: Visual structural review — sample 3-5 cycles**

```bash
less +G logs/session_<id>.log
# Scroll to a complete cycle (look for "▾ Action" / "▾ Reasoning" / "▾ Decision" / "═══" footer)
```

For 3-5 sample cycles, confirm the following structural properties:

| Property | Expected | If wrong |
|---|---|---|
| Head form for every tool call | `⚙ tool_name(arg1=v1, arg2=v2, reasoning="...")` (or `⚙ tool_name()` for empty args) | Task 3 dispatch broken — investigate |
| `_render_action` icon | `⚙` for normal / `✗` for error / `✎` for save_memory if any | Task 3 icon resolution broken |
| reasoning in head args (execution tools) | quoted string, may fold 2-4 physical lines at width=120 | json.dumps escape working (Issue 4 fix) |
| 3 outlier tools (update_price_level_alert / set_next_wake / set_next_wake_at) | body has NO `Reason: ...` / `— "..."` | Task 5 normalize broken |
| SL/TP body | contains `(was X)` or `(was unset)` | Task 4 prev capture broken |
| Sectioned vs plain body | sectioned for perception (`=== ... ===` markers), plain (no markers) for execution returns | by-content dispatch broken |
| Rich markup safety | reasoning containing `[bold]` etc. shows verbatim (not parsed as Rich markup) | escape() coverage broken (Issue 5 fix) |

- [ ] **Step 6.4: Soft inflation estimate**

Compare avg bytes/cycle:

```bash
# Live sim with this branch
wc -c logs/session_<live-sim-id>.log
# Divide by cycle count (count "═══" footer occurrences)
grep -c "═══" logs/session_<live-sim-id>.log
```

Versus a pre-impl baseline (W3 sim #10):
```bash
wc -c logs/session_1bbaa19f-4cce-4243-91be-a9f76c9005b9.log
grep -c "═══" logs/session_1bbaa19f-4cce-4243-91be-a9f76c9005b9.log
```

Compute approx avg bytes/cycle each. Expect avg/cycle ratio in range **1.03-1.10** (spec §7.1 estimate +3-8%). Treat as soft signal — sim runs have intrinsic LLM/market variance, exact inflation cannot be measured without running same prompt twice (out of scope per spec §5.3).

If ratio > 1.20, escalate — likely reasoning escape mis-implementation or unintended other expansion source.

- [ ] **Step 6.5: Record W4 forensic hit-rate review schedule**

Per spec §5.3 / §8: 1 week post-merge, track during real forensic usage whether head-args reasoning provides signals beyond cycle `▾ Reasoning` block. Decision rule:
- ≥ 30% / ≥ 3 explicit citations → retain plan D
- 10-30% → extend observation
- < 10% → strip mini-iter (revert to DB recorder alignment)

Record this commitment in commit / memory; no script needed at impl time.

---

## Task 7: Create PR

**Files:** No code; uses `gh` CLI.

- [ ] **Step 7.1: Verify final state**

```bash
git log --oneline iter-session-log-args-visibility ^main
```

Expected: **~8 commits in this iter** — 2 docs already landed (`e8641a7` spec / `b326156` plan) + 6 impl commits:
- Task 1: `_format_args_as_call` helper + unit tests
- Task 2: rename `_render_perception_tool` → `_render_tool_body`
- Task 3 (split into 2): dispatch refactor / snapshot rebuild
- Task 4: SL/TP state-delta
- Task 5: 3 outlier normalize + tools_descriptions.py sync

Task 6 (manual review) and Task 7 (PR) produce no source commits.

```bash
pytest 2>&1 | tail -5
```

Expected: All passing.

- [ ] **Step 7.2: Push branch**

```bash
git push -u origin iter-session-log-args-visibility
```

- [ ] **Step 7.3: Create PR**

```bash
gh pr create --title "iter-session-log-args-visibility: unify ⚙ tool_name(args) head + reasoning normalize" --body "$(cat <<'EOF'
## Summary

- Unifies session log tool call rendering to Python function-syntax head: `⚙ tool_name(arg=value, ...)` + body
- Normalizes 3 outlier execution tools (`update_price_level_alert` / `set_next_wake` / `set_next_wake_at`) to remove duplicate `Reason: {reasoning}` from return body (reasoning is single-point displayed in head args)
- Adds `(was X)` state-delta to `set_stop_loss` / `set_take_profit` returns for forensic visibility
- Renames `_render_perception_tool` → `_render_tool_body` (function now serves all tools via by-content dispatch)

**Spec:** `docs/superpowers/specs/2026-05-26-iter-session-log-args-visibility-design.md`

**Why:** W3 sim #10 forensic exposed pain in cross-referencing reasoning ↔ args (e.g. agent says "调 5min 窗口" but session log shows `⚙ get_recent_trades` — `window_seconds` not visible without DB SQL query). Session log self-containedness is high-value for forensic; tool_calls.args is already in DB but never surfaced to log.

## Design highlights

- **frozenset retained as drift guard** (not dispatch driver): `_PERCEPTION_TOOL_NAMES` / `_EXECUTION_TOOL_NAMES` warn for unregistered tools but don't drive sectioned/plain body decision (which is now by-content `=== ... ===` detection).
- **save_memory goes through unified head** (`✎ save_memory(args)`) — retired tool but revert path benefits from args visibility too.
- **reasoning retained in head args** (with DB recorder `tool_call_recorder.py:138` strip — known divergence per spec §3.2). Forensic value: per-tool-call thinking context. W4 forensic hit-rate review (1 week post-merge) decides whether to strip mini-iter.
- **tools_descriptions.py LLM-visible docstring sync** for the 3 outlier tools (fact-provider principle 1 closure — else spec self-contradicts).

## Scope explicitly excluded (triggered candidates)

- `adjust_leverage` prev state — would require BaseExchange interface extension breaking OKX instantiation (sim-only phase)
- `set_next_wake` / `set_next_wake_at` prev wake state — TradingDeps callable is one-way; wiring rewrite cost-mismatched
- Other 9 execution tools' return improvements — W4 forensic frequency-driven

## Test plan

- [x] `_format_args_as_call` unit tests (13 cases covering all value types + INVALID_JSON_KEY fallback)
- [x] SL/TP `(was X)` / `(was unset)` assertions
- [x] 3 outlier tools reasoning-removal assertions
- [x] 51 byte-equal snapshot tests rebuilt
- [x] `test_alert_age.py` regex updated
- [x] `tests/test_alert_family.py` sample strings refreshed
- [x] Manual structural review on live sim (Task 6 — sample 3-5 cycles for head form / SL/TP delta / 3-outlier normalize / Rich escape safety)
- [x] Soft inflation estimate vs W3 sim #10 baseline (Step 6.4, expected avg/cycle ratio 1.03-1.10)
- [ ] W4 forensic head-args reasoning hit-rate review (1 week post-merge, result → memory; decision: < 10% hit → strip mini-iter per spec §6)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7.4: Return PR URL to user**

After PR creation, share the PR URL.

---

## Self-Review

### Spec coverage check

| Spec section | Task |
|---|---|
| §3.1 unified dispatch + drift guard | Task 3 |
| §3.2 args format rules + reasoning retain + INVALID_JSON_KEY fallback + dict caveat | Task 1 |
| §3.3 by-content body dispatch + per-section _clip_body | Task 3 |
| §3.4 SL/TP state-delta + trigger_price | Task 4 |
| §3.5 adjust_leverage / set_next_wake defer (scope out) | No task (correctly out of scope) |
| §3.6 3 outlier tools reasoning normalize + tools_descriptions.py + display.py:266 | Task 5 |
| §4.1 src changes (display.py + tools_execution.py + tools_descriptions.py) | Tasks 1, 2, 3, 4, 5 |
| §4.2 tests rebuild | Tasks 3, 4, 5 |
| §5.3 offline re-render baseline | Task 6 (replaced offline re-render with manual structural review — re-render physically infeasible, see Task 6 rationale) |
| §7.5 reasoning retain divergence with DB recorder | Task 1 helper docstring + Task 6 Step 6.5 hit-rate review commitment (rollback path: spec §6 "reasoning strip from head" triggered candidate at < 10% hit rate) |
| §8 verification conditions | All tasks (`_format_args_as_call` Task 1; rename Task 2; dispatch Task 3; SL/TP Task 4; outlier + sync Task 5; W4 verify Task 6) |

All spec sections covered.

### Placeholder scan

No "TBD" / "implement later" / "add appropriate error handling" / etc. Each step has actual code or commands.

### Type consistency

- `_format_args_as_call(tool_name: str, args: dict | None) -> str` — same signature in Task 1 helper, Task 3 dispatch usage
- `_render_tool_body(tool_name, content, *, head_icon="⚙", head_args=None)` — same in Task 2 rename + Task 3 signature extension
- `set_stop_loss` / `set_take_profit` return strings — same shape in Task 4 implementation, Task 5 normalize, snapshot rebuild references

All consistent.
