# iter-tool-opt-alert-family-rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hard-rename `set_price_alert` → `set_price_volatility_alert`, align `get_active_alerts` section headers (`Price Volatility Alert` / `Price Level Alerts`), drop the redundant `Volatility alert:` body-line prefix, and complete the `iter-tool-opt-as-of-header` (commit b31ffc3) sweep by adding inline `(@ HH:MM:SS UTC)` to the `Price Level Alerts` section header.

**Architecture:** Pure name + section-header text rename plus one name-induced impl docstring fact-alignment. No schema, parameter, or behavior change. Single atomic implementation commit (hard rename — any partial state breaks tests). All anchors are line-accurate in spec §3.

**Tech Stack:** Python, pytest, pydantic-ai (tool registration via `@agent.tool` decorator + `REGISTERED_TOOL_NAMES` drift-guard list).

**Spec:** `docs/superpowers/specs/2026-05-13-iter-tool-opt-alert-family-rename-design.md` (v5, with REGISTERED_TOOL_NAMES count fix in commit `2b792bb`).

**Branch:** `iter-tool-opt-alert-family-rename` (off `main` `7642c6f`; already contains spec commit `d4efc3f` + spec-fix commit `2b792bb`).

---

## File map

**Modify (source, 5 files):**
- `src/agent/tools_execution.py` — impl rename + impl docstring + `_record_action` action label + success message (L223-251)
- `src/agent/tools_perception.py` — impl docstring fact-alignment (L565) + section header renames (L574/L576/L581)
- `src/agent/trader.py` — wrapper rename + import + `REGISTERED_TOOL_NAMES` slot (L527-545, L784)
- `src/cli/display.py` — summarize fn name + 2 dict keys + 1 success prefix + 1 list entry (L231, L283, L299, L530)
- `src/services/tool_call_recorder.py` — biz_error comment label (L58)

**Modify (tests, 7 files):**
- `tests/test_tools.py` (L351-388) — 4 `test_set_price_alert_*` rename
- `tests/test_trader_agent.py` (L199-210) — schema drift-guard rename
- `tests/test_tool_call_recorder.py` (L274) — `make_call` string rename
- `tests/test_alert_lifecycle.py` (L671-685) — biz_error invocation rename
- `tests/test_fact_only_wordlist.py` (L628, L701-705) — invocation helper rename
- `tests/test_tool_enhancement.py` (L720-758, L835/L840) — 4 test fn rename + 2 section-header assertion updates
- `tests/test_display_cycle.py` (L151-163, L307-310, L2229-2248, L3089) — fixture renames + summarize test rename + `_CRITICAL_FIELDS_PATH_A` wordlist update

**Add (drift-guard tests, 1 file):**
- `tests/test_tool_enhancement.py` — 2 new drift-guard test functions co-located with existing `test_get_active_alerts_*` cases

---

## Pre-flight

- [ ] **Step 0.1: Verify branch state**

```bash
git rev-parse --abbrev-ref HEAD              # expect: iter-tool-opt-alert-family-rename
git log --oneline -3                          # expect: 2b792bb spec fix, d4efc3f spec, 7642c6f main HEAD
git status --short                            # expect: clean
```

- [ ] **Step 0.2: Capture baseline pytest count + verify all pass**

```bash
uv run pytest tests/ --collect-only -q | tail -1     # capture: "<N> tests collected"
uv run pytest tests/ -x --tb=no -q                   # all must pass
```

Record the baseline N (the number of collected tests on the current tree). Step 6.3 expects `N + 2` after impl (the 2 new drift-guard tests from Task 1).

If pytest fails on the baseline, halt and investigate — don't start renaming on a broken tree.

---

## Task 1: Add 2 drift-guard tests (RED phase — tests will fail until Task 5 done)

**Files:**
- Modify: `tests/test_tool_enhancement.py` (insert after the existing `test_get_active_alerts_disabled` at ~L856 — co-locate with the other `test_get_active_alerts_*` cases)

- [ ] **Step 1.1: Read the existing `test_get_active_alerts_*` block to find insertion point**

```bash
grep -n "async def test_get_active_alerts" tests/test_tool_enhancement.py
```

Expected output: 2 lines — `test_get_active_alerts_with_data` (L821) and `test_get_active_alerts_disabled` (L847). Insert the new tests after the end of `test_get_active_alerts_disabled` (around L857).

- [ ] **Step 1.2: Add the two drift-guard tests after `test_get_active_alerts_disabled` (around L857)**

Insert exactly this block (preserve the blank-line separator and async signature; reuse `_make_deps` already defined in the file):

```python
def test_set_price_volatility_alert_in_registered_tool_names():
    """Drift guard (iter-10): set_price_volatility_alert renamed from set_price_alert.
    Hard rename — old name must be absent."""
    from src.agent.trader import REGISTERED_TOOL_NAMES

    assert "set_price_volatility_alert" in REGISTERED_TOOL_NAMES
    assert "set_price_alert" not in REGISTERED_TOOL_NAMES


async def test_get_active_alerts_section_headers_renamed():
    """Drift guard (iter-10): section headers renamed from
    Price Alert Settings / Active Price Level Alerts
    to Price Volatility Alert / Price Level Alerts."""
    from src.agent.tools_perception import get_active_alerts

    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=(2.5, 30))
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[
        {"id": "abc12345", "price": 75000.0, "direction": "above",
         "reasoning": "drift-guard fixture"},
    ])

    output = await get_active_alerts(deps)

    assert "=== Price Volatility Alert (@" in output
    assert "=== Price Level Alerts (1/20) (@" in output
    assert "Price Alert Settings" not in output
    assert "Active Price Level Alerts" not in output
```

- [ ] **Step 1.3: Run the new drift-guard tests — they MUST fail (RED)**

```bash
uv run pytest tests/test_tool_enhancement.py::test_set_price_volatility_alert_in_registered_tool_names tests/test_tool_enhancement.py::test_get_active_alerts_section_headers_renamed -v
```

Expected: both FAIL with AssertionError. This proves the drift-guards are wired correctly — they will turn GREEN only after the rename is applied. Do NOT commit yet; the broader test suite still passes.

---

## Task 2: Source rename — `tools_execution.py` impl + `trader.py` wrapper + REGISTERED_TOOL_NAMES

**Files:**
- Modify: `src/agent/tools_execution.py:223-251`
- Modify: `src/agent/trader.py:527-545, L784`

- [ ] **Step 2.1: Rename impl in `tools_execution.py:223-251`**

Replace the entire `set_price_alert` block (L223-251) with:

```python
async def set_price_volatility_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    reasoning: str,
) -> str:
    """Adjust price volatility alert parameters. threshold_pct: min 0.1, max 50, window_minutes: min 1, max 240."""
    # Check if alerts are enabled
    if deps.exchange.get_alert_params() is None:
        return "Alerts are disabled for this session. Enable alerts in wizard to use this feature."

    # Parameter validation
    if not (0.1 <= threshold_pct <= 50.0):
        note_biz_error("invalid_threshold_range")
        return f"Invalid threshold_pct: must be 0.1-50.0, got {threshold_pct}"
    if not (1 <= window_minutes <= 240):
        return f"Invalid window_minutes: must be 1-240, got {window_minutes}"

    deps.exchange.update_alert_params(threshold_pct, window_minutes)

    await _record_action(
        deps, action="set_price_volatility_alert",
        reasoning=f"threshold={threshold_pct}%, window={window_minutes}min | {reasoning}",
    )

    return (
        f"Price volatility alert updated: threshold={threshold_pct}%, "
        f"window={window_minutes}min"
    )
```

Changes vs. original (5 swaps):
- L223 `async def set_price_alert(` → `async def set_price_volatility_alert(`
- L229 impl docstring `Adjust price alert parameters.` → `Adjust price volatility alert parameters.`
- L244 `_record_action(action="set_price_alert",` → `action="set_price_volatility_alert"`
- L248-251 success message `Price alert updated:` → `Price volatility alert updated:`

The wrapper docstring at `trader.py:534` (`"""Adjust volatility alert sensitivity."""`) is **intentionally NOT modified** per spec §3.1 footnote (principle 8 — name carries family identity; docstring intentionally compact).

- [ ] **Step 2.2: Rename wrapper in `trader.py:527-545`**

Replace the entire `set_price_alert` wrapper block (L527-545) with:

```python
    @tool
    async def set_price_volatility_alert(
        ctx: RunContext[TradingDeps],
        threshold_pct: float,
        window_minutes: int,
        reasoning: str,
    ) -> str:
        """Adjust volatility alert sensitivity.

        Related: get_active_alerts (current volatility + price-level alert state).

        Args:
            threshold_pct: alert threshold percent (min 0.1, max 50).
            window_minutes: time window in minutes (min 1, max 240).
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_price_volatility_alert as _impl

        return await _impl(ctx.deps, threshold_pct, window_minutes, reasoning=reasoning)
```

Changes vs. original (3 swaps):
- L528 method name `set_price_alert` → `set_price_volatility_alert`
- L543 import `set_price_alert as _impl` → `set_price_volatility_alert as _impl`
- (L534 wrapper docstring kept literally as before — `Adjust volatility alert sensitivity.`)

- [ ] **Step 2.3: Update `REGISTERED_TOOL_NAMES` entry at `trader.py:784`**

Find the line:

```python
    "set_price_alert",
```

Replace with:

```python
    "set_price_volatility_alert",
```

The slot is position-preserving — `"set_price_volatility_alert"` lands at the same position as the old `"set_price_alert"` (between `"adjust_leverage"` and `"cancel_order"` in the execution sub-list). This keeps the drift-guard list slot-stable.

- [ ] **Step 2.4: Quick smoke — drift-guard test 1 should now pass**

```bash
uv run pytest tests/test_tool_enhancement.py::test_set_price_volatility_alert_in_registered_tool_names -v
```

Expected: PASS. (Test 2 still fails — section headers haven't been renamed yet.)

---

## Task 3: Display + recorder rename

**Files:**
- Modify: `src/cli/display.py` (L231, L283, L299, L530)
- Modify: `src/services/tool_call_recorder.py:58`

- [ ] **Step 3.1: Rename summarize function in `cli/display.py:231`**

Replace:

```python
def _summarize_set_price_alert(content: str) -> str:
    m = re.search(r"threshold=([\d.]+)%.*window=(\d+)min", content)
    if m:
        return f"threshold={m.group(1)}%, window={m.group(2)}min"
    return _fallback_summary(content)
```

With:

```python
def _summarize_set_price_volatility_alert(content: str) -> str:
    m = re.search(r"threshold=([\d.]+)%.*window=(\d+)min", content)
    if m:
        return f"threshold={m.group(1)}%, window={m.group(2)}min"
    return _fallback_summary(content)
```

(Only the function name changes; the body is identical.)

- [ ] **Step 3.2: Update `_EXECUTION_PARSERS` dict key at `cli/display.py:283`**

Find:

```python
    "set_price_alert": _summarize_set_price_alert,
```

Replace with:

```python
    "set_price_volatility_alert": _summarize_set_price_volatility_alert,
```

- [ ] **Step 3.3: Update `_EXECUTION_SUCCESS_PREFIXES` dict entry at `cli/display.py:299`**

Find:

```python
    "set_price_alert": "Price alert updated:",
```

Replace with:

```python
    "set_price_volatility_alert": "Price volatility alert updated:",
```

(Both the key AND the value string change — value tracks the new success message from Task 2 step 2.1.)

- [ ] **Step 3.4: Update list entry at `cli/display.py:530`**

Find the line in the execution-tool list (around L530):

```python
    "set_price_alert",
```

Replace with:

```python
    "set_price_volatility_alert",
```

- [ ] **Step 3.5: Update biz_error comment in `tool_call_recorder.py:58`**

Find:

```python
    "invalid_threshold_range",        # set_price_alert 阈值越界
```

Replace with:

```python
    "invalid_threshold_range",        # set_price_volatility_alert 阈值越界
```

(Only the comment text changes; the biz_error key `"invalid_threshold_range"` itself is preserved per spec §2.3 — forensic-side, not user-facing.)

---

## Task 4: Perception layer — impl docstring + section headers

**Files:**
- Modify: `src/agent/tools_perception.py` (L565, L574, L576, L581)

- [ ] **Step 4.1: Update impl docstring at `tools_perception.py:565`**

Find:

```python
async def get_active_alerts(deps: TradingDeps) -> str:
    """Get current alert configuration: volatility alert params and active price level alerts."""
```

Replace with:

```python
async def get_active_alerts(deps: TradingDeps) -> str:
    """Get current alert configuration: price volatility alert params and price level alerts."""
```

(Fact-alignment per spec §3.1 P2 — rendered output drops "Active", docstring must follow per principle 1.)

- [ ] **Step 4.2: Update first section header (alerts enabled branch) at `tools_perception.py:574`**

Find:

```python
        sections.append(f"=== Price Alert Settings (@ {fetch_ts} UTC) ===\nVolatility alert: {threshold}% in {window}min window")
```

Replace with:

```python
        sections.append(f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\n{threshold}% in {window}min window")
```

Changes: header `Price Alert Settings` → `Price Volatility Alert`; body line drops the redundant `Volatility alert: ` prefix.

- [ ] **Step 4.3: Update first section header (alerts disabled branch) at `tools_perception.py:576`**

Find:

```python
        sections.append(f"=== Price Alert Settings (@ {fetch_ts} UTC) ===\nVolatility alert: OFF")
```

Replace with:

```python
        sections.append(f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\nOFF")
```

Same swap as Step 4.2 applied to the disabled state.

- [ ] **Step 4.4: Update second section header at `tools_perception.py:581` (drop "Active" + ADD `@ UTC`)**

Find:

```python
    lines = [f"=== Active Price Level Alerts ({count}/20) ==="]
```

Replace with:

```python
    lines = [f"=== Price Level Alerts ({count}/20) (@ {fetch_ts} UTC) ==="]
```

This completes the `iter-tool-opt-as-of-header` (b31ffc3) sweep — the second section was the only perception-tool section header still missing the inline UTC timestamp. The `fetch_ts` variable is already in scope (assigned at L567).

- [ ] **Step 4.5: Quick smoke — drift-guard test 2 should now pass**

```bash
uv run pytest tests/test_tool_enhancement.py::test_get_active_alerts_section_headers_renamed -v
```

Expected: PASS. Both drift-guard tests now GREEN. Existing tests still fail (their fixtures reference old strings — Task 5 fixes that).

---

## Task 5: Test lockstep updates (7 test files)

Each step renames a specific test surface so the existing test suite stays in lockstep with the rename. All changes are mechanical string swaps.

- [ ] **Step 5.1: `tests/test_tools.py:351-388` — 4 test functions**

Find every occurrence of `set_price_alert` in this range and replace with `set_price_volatility_alert`. The 4 affected test functions are:

```python
test_set_price_alert_valid                  # L351
test_set_price_alert_threshold_too_low      # L360
test_set_price_alert_threshold_too_high     # L369
test_set_price_alert_window_out_of_range    # L378
```

Renames apply to: function names, `from src.agent.tools_execution import set_price_alert` → `set_price_volatility_alert`, and the invocation `await set_price_alert(...)` → `await set_price_volatility_alert(...)`. Use a scoped grep to verify:

```bash
grep -n "set_price_alert" tests/test_tools.py        # expect 0 hits after edit
```

- [ ] **Step 5.2: `tests/test_trader_agent.py:199-210` — schema drift-guard**

In `test_set_price_alert_schema_exposes_threshold_range` (L199):
- Rename test function: `test_set_price_alert_schema_exposes_threshold_range` → `test_set_price_volatility_alert_schema_exposes_threshold_range`
- Update the dict lookup: `agent._function_toolset.tools["set_price_alert"]` → `agent._function_toolset.tools["set_price_volatility_alert"]`
- Update the docstring reference if it mentions the tool by old name

Verify:

```bash
grep -n "set_price_alert" tests/test_trader_agent.py    # expect 0
```

- [ ] **Step 5.3: `tests/test_tool_call_recorder.py:274` — make_call string**

Find:

```python
        call=make_call("set_price_alert"),
```

Replace with:

```python
        call=make_call("set_price_volatility_alert"),
```

- [ ] **Step 5.4: `tests/test_alert_lifecycle.py:671-685` — biz_error end-to-end test**

In `test_set_price_alert_invalid_threshold_records_biz_error` (L671):
- Rename test function: `test_set_price_alert_invalid_threshold_records_biz_error` → `test_set_price_volatility_alert_invalid_threshold_records_biz_error`
- Update the docstring (L672) `set_price_alert 传 0.05 越界` → `set_price_volatility_alert 传 0.05 越界`
- Update import (L673): `from src.agent.tools_execution import set_price_alert` → `set_price_volatility_alert`
- Update invocation (L681): `await set_price_alert(...)` → `await set_price_volatility_alert(...)`
- Update `make_call` arg (L685): `make_call("set_price_alert")` → `make_call("set_price_volatility_alert")`

- [ ] **Step 5.5: `tests/test_fact_only_wordlist.py:628 + L701-705` — wordlist test helper**

At L628 in the helper list, find:

```python
    "_invoke_set_price_alert",
```

Replace with:

```python
    "_invoke_set_price_volatility_alert",
```

At L701-705, find:

```python
async def _invoke_set_price_alert(deps, mocker):
    """..."""
    from src.agent.tools_execution import set_price_alert
    ...
    return await set_price_alert(deps, 1.5, 30, reasoning="test")
```

Rename the helper function + import + invocation:

```python
async def _invoke_set_price_volatility_alert(deps, mocker):
    """..."""
    from src.agent.tools_execution import set_price_volatility_alert
    ...
    return await set_price_volatility_alert(deps, 1.5, 30, reasoning="test")
```

- [ ] **Step 5.6: `tests/test_tool_enhancement.py:720-758` — 4 test functions + 1 success-string assert**

In the 4 test functions at L720, L730, L741, L752, rename:
- function names: `test_set_price_alert_*` → `test_set_price_volatility_alert_*`
- imports: `from src.agent.tools_execution import set_price_alert` → `set_price_volatility_alert`
- invocations: `await set_price_alert(...)` → `await set_price_volatility_alert(...)`

At **L748** (inside `test_set_price_volatility_alert_accepts_threshold_0_1`), update the assertion:

```python
    assert "Price alert updated" in result
```

→

```python
    assert "Price volatility alert updated" in result
```

(Spec §3.3 anchor; do NOT touch L745 — that line is `deps.exchange.get_alert_params = MagicMock(...)`, not the assertion.)

- [ ] **Step 5.7: `tests/test_tool_enhancement.py:835, L840` — section-header assertions in `test_get_active_alerts_with_data`**

At L835, update the regex:

```python
    assert _re.search(
        r"=== Price Alert Settings \(@ \d{2}:\d{2}:\d{2} UTC\) ===",
        result,
    ), result[:200]
```

→

```python
    assert _re.search(
        r"=== Price Volatility Alert \(@ \d{2}:\d{2}:\d{2} UTC\) ===",
        result,
    ), result[:200]
```

At L840, update the substring check:

```python
    assert "=== Active Price Level Alerts" in result
```

→

```python
    assert "=== Price Level Alerts" in result
```

- [ ] **Step 5.8: `tests/test_display_cycle.py:151-163` — `test_summarize_get_active_alerts` fixture**

In the `content` fixture starting around L154, find:

```python
        "=== Price Alert Settings ===\n"
        "Volatility alert: 5.0% in 60min window\n\n"
        "=== Active Price Level Alerts (2/20) ===\n"
```

Replace with:

```python
        "=== Price Volatility Alert ===\n"
        "5.0% in 60min window\n\n"
        "=== Price Level Alerts (2/20) ===\n"
```

(This is a `summarize_tool` test fixture — it does NOT include the `(@ UTC)` segment because it tests the summarize parser on bare section names, not the live render. Keep the bare form.)

- [ ] **Step 5.9: `tests/test_display_cycle.py:307-312` — `test_summarize_set_price_alert` rename**

Find (actual code is 3 lines longer than originally drafted — contains function-local import + two `in result` asserts):

```python
def test_summarize_set_price_alert():
    from src.cli.display import summarize_tool
    content = "Price alert updated: threshold=5.0%, window=60min"
    result = summarize_tool("set_price_alert", content)
    assert "5.0" in result
    assert "60" in result
```

Replace with (only 3 minimal swaps — function name, content prefix, summarize_tool key — preserving the import line + both `in result` assertions verbatim):

```python
def test_summarize_set_price_volatility_alert():
    from src.cli.display import summarize_tool
    content = "Price volatility alert updated: threshold=5.0%, window=60min"
    result = summarize_tool("set_price_volatility_alert", content)
    assert "5.0" in result
    assert "60" in result
```

Do NOT collapse the two `in result` asserts into a single `==` assertion — that would change the test's semantic (loose contains vs strict equality).

- [ ] **Step 5.10: `tests/test_display_cycle.py:2229-2248` — `test_snapshot_get_active_alerts_with_alerts` (TWO passes)**

This test has BOTH an input `content` fixture (L2232-2235) AND a rendered `expected` block (L2241-2244). Apply the same swap to both:

Input fixture (L2232-2235):

```python
        "=== Price Alert Settings ===\n"
        "Volatility alert: 1.5% in 10min window\n"
        "\n"
        "=== Active Price Level Alerts (2/20) ===\n"
```

→

```python
        "=== Price Volatility Alert ===\n"
        "1.5% in 10min window\n"
        "\n"
        "=== Price Level Alerts (2/20) ===\n"
```

Rendered expected (L2241-2244):

```python
        "    === Price Alert Settings ===\n"
        "    Volatility alert: 1.5% in 10min window\n"
        ...
        "    === Active Price Level Alerts (2/20) ===\n"
```

→

```python
        "    === Price Volatility Alert ===\n"
        "    1.5% in 10min window\n"
        ...
        "    === Price Level Alerts (2/20) ===\n"
```

(Both fixtures use the bare form — no `(@ UTC)` — because this test exercises the display layer's snapshot rendering of a static input string, not the live render.)

- [ ] **Step 5.11: `tests/test_display_cycle.py:3089` — `_CRITICAL_FIELDS_PATH_A` wordlist**

Find:

```python
    "get_active_alerts": ["Price Alert Settings", "Volatility alert"],
```

Replace with:

```python
    "get_active_alerts": ["Price Volatility Alert", "OFF"],
```

Why these two anchors: `_CRITICAL_FIELDS_PATH_A` is consumed by `test_dg_1c_path_a_critical_fields_present` (L3095) which invokes `get_active_alerts` via `_MockDeps(exchange=_mock_exchange_minimal())`. `_mock_exchange_minimal()` (L2834) defaults `alert_params=None` → triggers the disabled branch → output is `=== Price Volatility Alert (@ ...) ===\nOFF\n\n=== Price Level Alerts (0/20) (@ ...) ===\n  No active alerts.`. Both anchors must appear in this output: `Price Volatility Alert` (header) + `OFF` (body).

---

## Task 6: Verification + atomic commit

**Files:** none (verification only, then commit all changes)

- [ ] **Step 6.1: Stale-string sweep — every old anchor must be absent from `src/` and `tests/`**

```bash
grep -rn "set_price_alert" src/ tests/                              # expect: no output
grep -rn "Price Alert Settings\|Active Price Level Alerts" src/ tests/  # expect: no output
grep -rn "Volatility alert: " src/                                  # expect: no output
```

If any command produces output, the rename is incomplete — go back to the corresponding task. Note that `docs/` and `.working/` are intentionally NOT swept (historical audit trail per `feedback_docs_no_inline_changelog`).

Substring safety note: `set_price_alert` is NOT a substring of `set_price_volatility_alert` (they share the `set_price_` prefix and `_alert` suffix but differ at byte 11 — `a` vs `v`), so plain grep does not produce false positives.

- [ ] **Step 6.2: Drift-guard tests pass**

```bash
uv run pytest tests/test_tool_enhancement.py::test_set_price_volatility_alert_in_registered_tool_names tests/test_tool_enhancement.py::test_get_active_alerts_section_headers_renamed -v
```

Expected: both PASS.

- [ ] **Step 6.3: Full test suite passes**

```bash
uv run pytest tests/ -x --tb=short
```

Expected: `N + 2` passed where `N` is the baseline captured at Step 0.2 (the 2 new drift-guard tests from Task 1 are the only collected-count delta). If any FAIL, fix in place — do NOT commit a failing tree.

- [ ] **Step 6.4: Fact-only wordlist regression**

```bash
uv run pytest tests/test_fact_only_wordlist.py -x
```

Expected: PASS. The renamed `_invoke_set_price_volatility_alert` helper plus the unchanged wrapper docstring `Adjust volatility alert sensitivity.` must clear the wordlist lint.

- [ ] **Step 6.5: Commit all source + test changes as a single atomic commit**

```bash
git add src/agent/tools_execution.py \
        src/agent/tools_perception.py \
        src/agent/trader.py \
        src/cli/display.py \
        src/services/tool_call_recorder.py \
        tests/test_tools.py \
        tests/test_trader_agent.py \
        tests/test_tool_call_recorder.py \
        tests/test_alert_lifecycle.py \
        tests/test_fact_only_wordlist.py \
        tests/test_tool_enhancement.py \
        tests/test_display_cycle.py

git commit -m "$(cat <<'EOF'
iter-tool-opt-alert-family-rename: set_price_alert → set_price_volatility_alert + sectioning

Hard rename of vol-alert tuning tool + alignment of get_active_alerts
section headers (Price Volatility Alert / Price Level Alerts). Drops
the redundant `Volatility alert:` body-line prefix and completes the
iter-tool-opt-as-of-header (b31ffc3) sweep by adding `(@ HH:MM:SS UTC)`
to the Price Level Alerts header.

Spec: docs/superpowers/specs/2026-05-13-iter-tool-opt-alert-family-rename-design.md
Plan: docs/superpowers/plans/2026-05-13-iter-tool-opt-alert-family-rename.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6.6: Verify clean post-commit state**

```bash
git status --short          # expect: clean
git log --oneline -5        # expect: rename impl + plan + spec-fix + spec + main HEAD
```

---

## Self-review notes

**Spec coverage matrix:**

| Spec section | Plan task(s) |
|---|---|
| §3.1 (tool rename surface, 11 file anchors) | Task 2 (impl + wrapper + REGISTERED_TOOL_NAMES) + Task 3 (display + recorder) + Task 4.1 (impl docstring fact-alignment) |
| §3.2 (section header rename + line-prefix drop + `@ UTC` completion) | Task 4.2-4.4 |
| §3.3 (test files in lockstep, 9 anchor rows) | Task 5.1-5.11 |
| §3.4 (2 new drift-guard tests) | Task 1 |
| §4.1 (TDD ordering RED → GREEN → REFACTOR) | Task 1 (RED) → Tasks 2-5 (GREEN) → Task 6.4 (wordlist REFACTOR) |
| §4.2 (verification commands) | Task 6.1, 6.3, 6.4 |
| §4.3 (rollback unit: single PR / single revert) | Task 6.5 (single atomic commit) |
| §5 (W3 validation gate) | Post-merge, out of scope for impl |
| §6 (out of scope) | Honored — iter-11 alert-age / analytics scripts / DB historical / Layer-1 persona NOT touched |

**Commit graph after this plan:**

```
* <new>   iter-tool-opt-alert-family-rename: impl rename + sectioning (Task 6.5)
* <plan>  iter-tool-opt-alert-family-rename: plan (this file)
* 2b792bb iter-tool-opt-alert-family-rename: fix REGISTERED_TOOL_NAMES count 32→34 in spec
* d4efc3f iter-tool-opt-alert-family-rename: spec for set_price_alert → set_price_volatility_alert + sectioning
* 7642c6f review-followups: window field in error headers + drift guard + test naming  (main)
```

Per `feedback_plan_doc_commit_first`, the plan itself lands as a separate commit (before this task list starts executing) — see the "Plan commit" step in the execution handoff section below.

---

## Plan commit (do this NOW, before starting Task 1)

- [ ] **Step P.1: Commit this plan as a standalone commit**

```bash
git add docs/superpowers/plans/2026-05-13-iter-tool-opt-alert-family-rename.md
git commit -m "$(cat <<'EOF'
iter-tool-opt-alert-family-rename: plan for set_price_alert → set_price_volatility_alert

6-task TDD plan: drift-guard RED → source rename (tools_execution / trader /
REGISTERED_TOOL_NAMES / display / recorder / perception sections) → 7-file test
lockstep → verification + atomic commit.

Spec: docs/superpowers/specs/2026-05-13-iter-tool-opt-alert-family-rename-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

After this commit, proceed to **Pre-flight Step 0.1** above and walk the tasks in order.
