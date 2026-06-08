# Batch Event Drain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the scheduler drain all pending events into ONE agent cycle per wake (instead of one full cycle per event), so the agent reasons holistically over a single market move instead of myopically re-running on stale events.

**Architecture:** The scheduler snapshot-drains its priority heap into a list (synchronous, no `await` between pops; cap 20, defer-not-drop + WARNING) and hands the whole list to the callback. `run_agent_cycle` takes a `list[tuple[str, Any]]`, builds a priority-sectioned prompt (N==1 byte-identical to today, N>1 multi-event header + per-event blocks), persists `trigger_context` as a JSON array with `triggered_by` = dominant type, and the session-log Header shrinks to type+count while Context owns per-event detail. The `v_alert_lifecycle` view is rewritten to unnest the array (with legacy-object normalization).

**Tech Stack:** Python 3.12, asyncio, pydantic-ai, SQLAlchemy (SQLite, `json_each`/`json_extract`), pytest-asyncio, Rich.

**Spec:** `docs/superpowers/specs/2026-06-08-batch-event-drain-design.md` (read it before starting — Decisions log §1-8 are settled, do not re-litigate).

**Baseline:** `2144 passed` on `main`. Every task ends green.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/scheduler/scheduler.py` | Wake loop, priority heap, drain | Drain-all into list, cap-20+WARNING, callback contract → `list[tuple]`, `_type_counts` helper |
| `src/services/cycle_capture.py` | trigger metadata → DB dict | Add `_capture_trigger_contexts` list wrapper (keep single-event helper) |
| `src/cli/display.py` | Session-log rendering | `_format_trigger_detail` → type+count (list-safe); `_extract_event_lines` + `_render_context` per-event bullets; `_render_header` defensive wrap; `CycleRenderContext.trigger_context` type |
| `src/cli/app.py` | `run_agent_cycle`, `on_tick`, prompt assembly | `events` list contract; `_render_event_block` + `_wake_header_line` helpers; `triggered_by` = dominant; capture list; 3 persistence paths + 3 render-ctx |
| `src/storage/views.py` | SQL views | `v_alert_lifecycle` array-aware unnest, drop `triggered_by='alert'` |
| `tests/test_scheduler.py` | Scheduler tests | Full event-path rewrite (list contract) |
| 8 test files | `run_agent_cycle` callers | Migrate 39 call sites to `events=[...]` |

**Task order (each commit leaves tests green):** leaf helpers first (1 capture, 2 display, 3 prompt blocks — all additive/back-compat), then the atomic contract flip (4 scheduler+app+call-sites — cannot be split without a broken intermediate), then the view (5), then full-suite + anchors (6).

---

### Task 1: `_capture_trigger_contexts` list wrapper

**Files:**
- Modify: `src/services/cycle_capture.py` (add function after `_capture_trigger_context`, ends `:81`)
- Test: `tests/test_p4_cycle_capture.py` (add tests; existing single-event tests untouched)

Additive — `_capture_trigger_context` (single) stays exactly as-is; the new list form maps over it.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_p4_cycle_capture.py`:

```python
def test_capture_trigger_contexts_maps_batch():
    from src.services.cycle_capture import _capture_trigger_contexts
    from src.integrations.exchange.base import PriceLevelAlertInfo

    alert = PriceLevelAlertInfo(
        alert_id="a1", symbol="BTC/USDT:USDT", current_price=80050.0,
        target_price=80000.0, direction="above", reasoning="r", timestamp=1_700_000_000_000,
    )
    out = _capture_trigger_contexts("cyc1", [("scheduled", None), ("alert", alert)])
    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0] == {"type": "scheduled_tick"}
    assert out[1]["type"] == "price_level_alert"
    assert out[1]["alert_id"] == "a1"


def test_capture_trigger_contexts_all_fail_yields_none_slots():
    from src.services.cycle_capture import _capture_trigger_contexts

    # context that raises on attribute access → per-event None, count preserved
    class Bad:
        def __getattr__(self, name):
            raise RuntimeError("boom")

    out = _capture_trigger_contexts("cyc1", [("conditional", Bad()), ("conditional", Bad())])
    assert out == [None, None]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_p4_cycle_capture.py::test_capture_trigger_contexts_maps_batch tests/test_p4_cycle_capture.py::test_capture_trigger_contexts_all_fail_yields_none_slots -v`
Expected: FAIL with `ImportError: cannot import name '_capture_trigger_contexts'`

- [ ] **Step 3: Write minimal implementation**

In `src/services/cycle_capture.py`, immediately after `_capture_trigger_context` (after line 81, before `async def _capture_state_snapshot`):

```python
def _capture_trigger_contexts(cycle_id: str, events: list) -> list[dict | None]:
    """Capture trigger metadata for a batch of events (spec 2026-06-08 §3).

    Maps `_capture_trigger_context` over the drained `(trigger_type, context)` list.
    Per-event best-effort: a failing event yields `None` in its slot without sinking
    the batch. Always returns a list of the same length as `events` (count preserved;
    all-fail → `[None, ...]`, never a `scheduled_tick` mislabel).
    """
    return [_capture_trigger_context(cycle_id, tt, ctx) for tt, ctx in events]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_p4_cycle_capture.py -v`
Expected: PASS (new tests + all existing single-event tests)

- [ ] **Step 5: Commit**

```bash
git add src/services/cycle_capture.py tests/test_p4_cycle_capture.py
git commit -m "feat(capture): _capture_trigger_contexts batch wrapper"
```

---

### Task 2: display — type+count Header + per-event Context bullets

**Files:**
- Modify: `src/cli/display.py` — `CycleRenderContext.trigger_context` (`:728`); `_format_trigger_detail` (`:779-846`); `_extract_event_line` → `_extract_event_lines` (`:1008-1024`); `_render_context` Woke-by block (`:1155-1164`); `_render_header` defensive wrap (`:911`)
- Test: `tests/test_display_cycle.py` (`_format_trigger_detail` tests — replace detail-branch tests); `tests/test_session_log_cycle_context.py` (`_extract_event_line` → plural + bullets)

Back-compat: every changed function accepts BOTH the new list form AND a legacy single dict, so this task is green without app.py sending lists yet. Tests construct list/dict inputs directly.

#### 2a — `_format_trigger_detail` → type + count

- [ ] **Step 1: Write the failing test**

In `tests/test_display_cycle.py`, **delete** the existing tests that assert per-event Header detail for fill / price_level_alert / percentage_alert / T-EH-3 partial degradation against `_format_trigger_detail` (they assert strings like `ALERT — vol ...` / `CONDITIONAL — TP ...` that the new type+count Header no longer produces). Replace with:

```python
def test_format_trigger_detail_single_is_type_only():
    from src.cli.display import _format_trigger_detail
    # N==1 (incl. legacy single dict) → bare type, no detail
    assert _format_trigger_detail("alert", {"type": "price_level_alert", "alert_id": "a1"}) == "ALERT"
    assert _format_trigger_detail("conditional", [{"type": "fill", "trigger_reason": "tp"}]) == "CONDITIONAL"
    assert _format_trigger_detail("scheduled", [{"type": "scheduled_tick"}]) == "SCHEDULED"
    assert _format_trigger_detail("scheduled", None) == "SCHEDULED"


def test_format_trigger_detail_multi_is_type_plus_count():
    from src.cli.display import _format_trigger_detail
    batch = [
        {"type": "fill", "trigger_reason": "tp"},
        {"type": "price_level_alert", "alert_id": "a1"},
        {"type": "percentage_alert"},
    ]
    assert _format_trigger_detail("conditional", batch) == "CONDITIONAL +2 (1 fill, 2 alerts)"


def test_format_trigger_detail_multi_alerts_only():
    from src.cli.display import _format_trigger_detail
    batch = [{"type": "price_level_alert"}, {"type": "price_level_alert"}]
    assert _format_trigger_detail("alert", batch) == "ALERT +1 (2 alerts)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_display_cycle.py::test_format_trigger_detail_multi_is_type_plus_count -v`
Expected: FAIL (returns old detailed string / AttributeError on list)

- [ ] **Step 3: Write minimal implementation**

Replace `_format_trigger_detail` (`src/cli/display.py:779-846`) entirely with:

```python
def _format_trigger_detail(trigger_type: str, ctx) -> str:
    """Format Header 'Trigger    ...' line (spec 2026-06-08 §3): type + count only.

    Per-event detail (fill PnL / alert summary) lives in the ▾ Context section now,
    not the Header (the Header can't fit N events; detail is preserved losslessly in
    Context). Accepts the new batch list `list[dict|None]`, a legacy single dict, or None.

    Returns:
        N<=1 (incl. legacy single-object / None) → bare type, e.g. "ALERT" / "SCHEDULED".
        N>1 → "<TYPE> +<N-1> (<breakdown>)", e.g. "CONDITIONAL +2 (1 fill, 2 alerts)".
    """
    type_upper = trigger_type.upper()
    if ctx is None:
        events = []
    elif isinstance(ctx, dict):
        events = [ctx]                       # legacy single-object row
    else:
        events = list(ctx)
    n = len(events)
    if n <= 1:
        return type_upper
    n_fill = sum(1 for e in events if isinstance(e, dict) and e.get("type") == "fill")
    n_alert = sum(
        1 for e in events
        if isinstance(e, dict) and e.get("type") in ("price_level_alert", "percentage_alert")
    )
    parts: list[str] = []
    if n_fill:
        parts.append(f"{n_fill} fill{'s' if n_fill > 1 else ''}")
    if n_alert:
        parts.append(f"{n_alert} alert{'s' if n_alert > 1 else ''}")
    breakdown = ", ".join(parts) if parts else f"{n} events"
    return f"{type_upper} +{n - 1} ({breakdown})"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_display_cycle.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(display): Trigger Header line = type + count (detail moves to Context)"
```

#### 2b — `_extract_event_lines` + per-event Context bullets + defensive Header

- [ ] **Step 1: Write the failing test**

In `tests/test_session_log_cycle_context.py`, update references from `_extract_event_line` (singular) to `_extract_event_lines` (plural, returns `list[str]`), and add the multi-event case. Example tests:

```python
def test_extract_event_lines_single():
    from src.cli.display import _extract_event_lines
    wake = (
        "You have been woken up by a alert trigger.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 15m\n"
        "Assess the situation and decide what to do.\n\n"
        "PRICE LEVEL: BTC/USDT:USDT reached 80050.00 (alert id=a1 above 80000.00 — r)"
        " — fired 2026-06-01 14:34 UTC (4 min ago)"
    )
    lines = _extract_event_lines(wake, "alert")
    assert len(lines) == 1
    assert lines[0].startswith("PRICE LEVEL: BTC/USDT:USDT reached 80050.00")


def test_extract_event_lines_multi_splits_per_prefix():
    from src.cli.display import _extract_event_lines
    wake = (
        "You have been woken up by 2 triggers (1 fill, 1 alert) since the last cycle.\n"
        "Trading pair: BTC/USDT:USDT | Timeframe: 15m\n"
        "Assess the situation and decide what to do.\n\n"
        "IMPORTANT EVENT: tp triggered — BTC/USDT:USDT 1.0 @ 80000, Fee: -1.00 USDT"
        " — filled 2026-06-01 14:34 UTC (1 min ago)\n\n"
        "PRICE ALERT: BTC/USDT:USDT surged 1.5% in 15min (78000.00 → 79170.00)"
        " — fired 2026-06-01 14:34 UTC (2 min ago)"
    )
    lines = _extract_event_lines(wake, "conditional")
    assert len(lines) == 2
    assert lines[0].startswith("IMPORTANT EVENT: tp triggered")
    assert lines[1].startswith("PRICE ALERT: BTC/USDT:USDT surged")


def test_extract_event_lines_scheduled_empty():
    from src.cli.display import _extract_event_lines
    assert _extract_event_lines("anything", "scheduled") == []
```

For `_render_context`, add a multi-event bullet test (constructing a 2-event `user_prompt_snapshot`) asserting the rendered Context contains `Woke by — 2 events:` and two `• ` bullet lines; keep an existing N==1 test asserting the single `Woke by — ...` form is unchanged.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_session_log_cycle_context.py::test_extract_event_lines_multi_splits_per_prefix -v`
Expected: FAIL with `ImportError: cannot import name '_extract_event_lines'`

- [ ] **Step 3: Write minimal implementation**

(i) Replace `_extract_event_line` (`src/cli/display.py:1008-1024`) with the plural form:

```python
def _extract_event_lines(wake_half: str, trigger_type: str) -> list[str]:
    """Extract the verbatim variable event text(s) from the wake prompt (spec 2026-06-08 §3).

    scheduled → [] (no variable event line; pure boilerplate). conditional/alert →
    split `wake_half` at each known prefix (IMPORTANT EVENT / PRICE ALERT / PRICE LEVEL)
    into one segment per event, preserving alert id / reasoning / fee / PnL / age clause,
    collapsing whitespace and truncating **each event individually** to `_CONTEXT_EVENT_CAP`.
    No prefix found → [].
    """
    if trigger_type == "scheduled":
        return []
    pattern = re.compile("|".join(re.escape(p) for p in _EVENT_PREFIXES))
    positions = [m.start() for m in pattern.finditer(wake_half)]
    if not positions:
        return []
    out: list[str] = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(wake_half)
        seg = re.sub(r"\s+", " ", wake_half[start:end]).strip()
        out.append(_truncate_with_marker(seg, _CONTEXT_EVENT_CAP))
    return out
```

(ii) Update the Woke-by block in `_render_context` (`src/cli/display.py:1155-1164`). Replace:

```python
        event_line = _extract_event_line(wake_half, trigger_type)
        blocks = _parse_injected_summaries(summaries_half)

        lines: list[str] = []
        if event_line:
            lines.append(f"  Woke by — {escape(event_line)}")
        elif trigger_type == "scheduled":
            # scheduled 无变量事件行（_extract_event_line → None）；仍渲类型标签 +
            # header 上的唤醒时间后缀，使 Context 段自包含（不必回看 Header 取 cycle 时间）。
            lines.append(f"  Woke by — SCHEDULED{_extract_scheduled_wake_suffix(wake_half)}")
```

with:

```python
        event_lines = _extract_event_lines(wake_half, trigger_type)
        blocks = _parse_injected_summaries(summaries_half)

        lines: list[str] = []
        if len(event_lines) == 1:
            lines.append(f"  Woke by — {escape(event_lines[0])}")
        elif len(event_lines) > 1:
            # Batch wake (spec 2026-06-08 §3): one bullet per event, each truncated
            # individually (Header carries only type+count; Context owns the detail).
            lines.append(f"  Woke by — {len(event_lines)} events:")
            for el in event_lines:
                lines.append(f"    • {escape(el)}")
        elif trigger_type == "scheduled":
            # scheduled 无变量事件行；仍渲类型标签 + header 唤醒时间后缀，使 Context 段自包含。
            lines.append(f"  Woke by — SCHEDULED{_extract_scheduled_wake_suffix(wake_half)}")
```

(iii) Defensive Header wrap — in `_render_header` (`src/cli/display.py:911`), replace:

```python
    trigger_line = _format_trigger_detail(trigger_type, trigger_context)
```

with:

```python
    try:
        trigger_line = _format_trigger_detail(trigger_type, trigger_context)
    except Exception:
        # _render_header is NOT inside a try in format_cycle_output; a raise here
        # propagates out of the whole renderer to on_tick's except → misleading
        # "Agent cycle failed" even though the cycle already committed. Degrade like
        # _render_context does (spec 2026-06-08 §3).
        logger.warning("Trigger header render failed; falling back to bare type", exc_info=True)
        trigger_line = trigger_type.upper()
```

(iv) Update `CycleRenderContext.trigger_context` annotation (`src/cli/display.py:728`):

```python
    trigger_context: list[dict | None] | dict | None  # batch list (spec 2026-06-08); legacy single dict tolerated
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_session_log_cycle_context.py tests/test_display_cycle.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_session_log_cycle_context.py
git commit -m "feat(display): per-event Context bullets + fail-isolated Trigger header"
```

---

### Task 3: prompt-block helpers in `app.py` (additive)

**Files:**
- Modify: `src/cli/app.py` — add `_render_event_block` + `_wake_header_line` near the other prompt helpers (after `_format_price_level_alert_trigger`, `:377`). These are **extracted verbatim** from the current inline assembly (`:518-573`) so N==1 output is byte-identical; they are unused until Task 4.
- Test: `tests/test_wake_event_timestamp.py` (add direct helper tests)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_wake_event_timestamp.py`:

```python
def test_wake_header_line_single_scheduled_has_suffix():
    from datetime import datetime, timezone
    from src.cli.app import _wake_header_line
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    line = _wake_header_line([("scheduled", None)], now)
    assert line == "You have been woken up by a scheduled trigger — fired 2026-06-01 14:38 UTC (just now)"


def test_wake_header_line_single_conditional_no_suffix():
    from datetime import datetime, timezone
    from src.cli.app import _wake_header_line
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    assert _wake_header_line([("conditional", object())], now) == "You have been woken up by a conditional trigger"


def test_wake_header_line_multi_breakdown():
    from datetime import datetime, timezone
    from src.cli.app import _wake_header_line
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    events = [("conditional", object()), ("alert", object()), ("alert", object())]
    line = _wake_header_line(events, now)
    assert line == "You have been woken up by 3 triggers (1 fill, 2 alerts) since the last cycle"


async def test_render_event_block_percentage_alert(monkeypatch):
    from datetime import datetime, timezone
    from src.cli.app import _render_event_block
    from src.services.price_alert import AlertInfo
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    alert = AlertInfo(
        symbol="BTC/USDT:USDT", current_price=79170.0, reference_price=78000.0,
        change_pct=1.5, window_minutes=15, timestamp=int(now.timestamp() * 1000),
    )
    block = await _render_event_block(deps=None, trigger_type="alert", context=alert, cycle_started_at=now)
    assert block.startswith("\n\nPRICE ALERT: BTC/USDT:USDT surged 1.5% in 15min (78000.00 → 79170.00)")
    assert "fired 2026-06-01 14:38 UTC" in block


async def test_render_event_block_scheduled_empty():
    from datetime import datetime, timezone
    from src.cli.app import _render_event_block
    now = datetime(2026, 6, 1, 14, 38, tzinfo=timezone.utc)
    assert await _render_event_block(deps=None, trigger_type="scheduled", context=None, cycle_started_at=now) == ""
```

(`AlertInfo` field names: confirm against `src/services/price_alert.py:8-15` — `symbol, current_price, reference_price, change_pct, window_minutes, timestamp`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_wake_event_timestamp.py::test_wake_header_line_multi_breakdown -v`
Expected: FAIL with `ImportError: cannot import name '_wake_header_line'`

- [ ] **Step 3: Write minimal implementation**

In `src/cli/app.py`, after `_format_price_level_alert_trigger` (ends `:377`):

```python
def _wake_header_line(events: list, cycle_started_at: datetime) -> str:
    """Build the wake-prompt header line (spec 2026-06-08 §2).

    N==1: byte-identical to the prior single-trigger header
    (`You have been woken up by a {type} trigger`), with the scheduled fire-time suffix
    appended only for scheduled (its fire time ≡ cycle_started_at → "just now").
    N>1: a multi-event header `You have been woken up by {n} triggers ({breakdown}) since
    the last cycle`, breakdown counted fill-first then alert (heap pop order).
    """
    if len(events) == 1:
        tt = events[0][0]
        line = f"You have been woken up by a {tt} trigger"
        if tt == "scheduled":
            line += _wake_time_suffix(
                "fired", int(cycle_started_at.timestamp() * 1000), cycle_started_at,
            )
        return line
    n = len(events)
    n_fill = sum(1 for tt, _ in events if tt == "conditional")
    n_alert = sum(1 for tt, _ in events if tt == "alert")
    parts: list[str] = []
    if n_fill:
        parts.append(f"{n_fill} fill{'s' if n_fill > 1 else ''}")
    if n_alert:
        parts.append(f"{n_alert} alert{'s' if n_alert > 1 else ''}")
    breakdown = ", ".join(parts) if parts else f"{n} events"
    return f"You have been woken up by {n} triggers ({breakdown}) since the last cycle"


async def _render_event_block(deps, trigger_type: str, context, cycle_started_at: datetime) -> str:
    """Render one event's prompt block (spec 2026-06-08 §2), verbatim with the prior
    inline assembly so N==1 prompts are byte-identical.

    Async + IO: the full-close fill branch awaits `deps.exchange.get_contract_size` and
    reads `deps.fee_rate` (symbol from `context.symbol`). scheduled / context-None → "".
    """
    if trigger_type == "conditional" and context is not None:
        msg = (
            f"\n\nIMPORTANT EVENT: {context.trigger_reason} triggered "
            f"— {context.symbol} {context.amount} @ {context.fill_price}"
        )
        if context.pnl is None:
            # Open fill — fee only
            msg += f", Fee: {-context.fee:+.2f} USDT"
        elif context.is_full_close and context.entry_price is not None:
            # Full close fill — fee + gross + equiv-round-trip net.
            _contract_size = await deps.exchange.get_contract_size(context.symbol)
            entry_fee_recompute = (
                context.entry_price * context.amount * _contract_size * deps.fee_rate
            )
            round_trip_net = -entry_fee_recompute + context.pnl - context.fee
            msg += (
                f", Fee: {-context.fee:+.2f} USDT, "
                f"PnL: {context.pnl:+.2f} USDT (gross) / "
                f"{round_trip_net:+.2f} USDT (this fill, equiv-round-trip)"
            )
        else:
            base = (
                f", Fee: {-context.fee:+.2f} USDT, "
                f"PnL: {context.pnl:+.2f} USDT (gross)"
            )
            if context.is_full_close and context.entry_price is None:
                base += " [round-trip net unavailable: entry_price not cached]"
            msg += base
        msg += _wake_time_suffix("filled", context.timestamp, cycle_started_at)
        return msg
    if trigger_type == "alert" and context is not None:
        if isinstance(context, PriceLevelAlertInfo):
            return _format_price_level_alert_trigger(context, cycle_started_at)
        direction = "dropped" if context.change_pct < 0 else "surged"
        return (
            f"\n\nPRICE ALERT: {context.symbol} {direction} {abs(context.change_pct):.1f}% "
            f"in {context.window_minutes}min ({context.reference_price:.2f} → {context.current_price:.2f})"
            + _wake_time_suffix("fired", context.timestamp, cycle_started_at)
        )
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_wake_event_timestamp.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/app.py tests/test_wake_event_timestamp.py
git commit -m "feat(app): extract _wake_header_line + _render_event_block (additive)"
```

---

### Task 4: THE CONTRACT FLIP — scheduler drain-all + `run_agent_cycle(events)` + call-site migration

This is the atomic core: the callback contract changes from `(trigger_type, context)` to `(events: list[tuple[str, Any]])`. It cannot be split without a broken intermediate, so scheduler + app + all callers + `test_scheduler.py` flip in one commit. Many fine-grained steps; one commit at the end.

**Files:**
- Modify: `src/scheduler/scheduler.py` (`:30` callback type, `:58` bootstrap, `:67-75` drain loop, `:82-89` `_run_cycle`, add `_type_counts`)
- Modify: `src/cli/app.py` (`run_agent_cycle` signature `:485-495` + prompt assembly `:516-573` + 3 persistence `triggered_by`/`trigger_context` `:617-618/670-671/774-775` + 3 `CycleRenderContext` `:643-644/696-697/807-808` + `on_tick` `:1080-1087`)
- Modify: `tests/test_scheduler.py` (full event-path rewrite)
- Modify: 8 caller test files (39 call sites) — `test_usage_limits`, `test_cli_app`, `test_p4_cycle_capture`, `test_agent_cycle_injection`, `test_cycle_log`, `test_wake_event_timestamp`, `test_run_agent_cycle_phase1`, `test_cycle_summary_injection`

#### 4a — scheduler drain-all + `test_scheduler.py` rewrite

- [ ] **Step 1: Rewrite `test_scheduler.py` event-path tests to the list contract**

Every callback becomes `async def callback(events)` collecting `events` (a `list[tuple[str, Any]]`). The 5 ordering tests assert **one callback call carrying an ordered list**, not N separate calls. Concretely:

- `test_scheduler_fires_on_interval`: callback appends `events[0][0]`; assert `fired[0] == "scheduled"` (bootstrap passes `[("scheduled", None)]`).
- `test_scheduler_trigger_wakes_from_sleep`: callback appends the whole `events` list; assert one call's list `== [("scheduled", None)]` (bootstrap) and a later call contains `("conditional", "fill_event_1")`.
- `test_scheduler_trigger_merges_multiple_events`: enqueue two conditionals during one sleep; assert a single non-bootstrap callback call received `[("conditional", "event1"), ("conditional", "event2")]` (FIFO within priority).
- `test_scheduler_preserves_trigger_type`: assert the batch list contains `("alert", "price_drop")`.
- `test_scheduler_priority_then_fifo`: enqueue conditional fill_1 / alert / conditional fill_2; assert the non-scheduled batch list `== [("conditional", "fill_1"), ("conditional", "fill_2"), ("alert", "price_drop")]`.
- `test_scheduler_priority_conditional_over_alert`: 6 alerts + 1 conditional; assert the batch list[0] is `("conditional", "close_fill")` and the next 6 are the alerts in order.
- `test_scheduler_fifo_within_same_priority`: 3 conditionals → batch list in FIFO.
- `test_scheduler_context_not_lost_on_multiple_triggers`: assert both contexts present in the batch list.
- `test_scheduler_event_preempts_scheduled`: assert first call list `== [("scheduled", None)]`, second call list[0] `== ("conditional", "urgent")`.
- **`test_scheduler_safety_valve_max_drain` → rename `test_scheduler_drain_cap_20`**: enqueue 21 conditionals; assert the first batch has `len == 20`, a WARNING was logged with `total=21`, and across the back-to-back drains all 21 are eventually delivered (heap non-empty → `_interruptible_sleep` returns immediately, no sleep between). Add a boundary case: 20 enqueued → one batch of 20, **no** WARNING.
- **`test_scheduler_drain_respects_stop` (`:307`) → DELETE.** Its premise (stop mid-drain skips later events) is negated by the synchronous single-batch drain — all enqueued events enter one list and one `_run_cycle`. Replace with `test_scheduler_drain_is_single_batch`: enqueue 5 conditionals, assert exactly one non-bootstrap callback call carrying all 5.

Use `caplog` for the WARNING assertions:

```python
async def test_scheduler_drain_cap_20(caplog):
    import logging
    from src.scheduler.scheduler import Scheduler
    batches = []

    async def callback(events):
        batches.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)
    for i in range(21):
        await scheduler.trigger("conditional", context=f"e{i}")
    with caplog.at_level(logging.WARNING):
        await asyncio.sleep(0.3)
    scheduler.stop()
    await task

    non_bootstrap = [b for b in batches if b != [("scheduled", None)]]
    assert len(non_bootstrap[0]) == 20
    delivered = [c for b in non_bootstrap for (_, c) in b]
    assert len(delivered) == 21                       # all eventually drained
    assert any("event drain capped" in r.message and "total=21" in r.message
               for r in caplog.records)


async def test_scheduler_drain_cap_boundary_no_warning(caplog):
    import logging
    from src.scheduler.scheduler import Scheduler
    batches = []

    async def callback(events):
        batches.append(list(events))

    scheduler = Scheduler(interval_seconds=10, callback=callback)
    task = asyncio.create_task(scheduler.start())
    await asyncio.sleep(0.05)
    for i in range(20):
        await scheduler.trigger("conditional", context=f"e{i}")
    with caplog.at_level(logging.WARNING):
        await asyncio.sleep(0.2)
    scheduler.stop()
    await task

    non_bootstrap = [b for b in batches if b != [("scheduled", None)]]
    assert len(non_bootstrap) == 1 and len(non_bootstrap[0]) == 20
    assert not any("event drain capped" in r.message for r in caplog.records)
```

Leave the `set_next_interval` tests (`:334-376`) as-is **except** the two that pass `callback=lambda t, c: None` (`:358`, `:372`) — change to `callback=lambda events: None`.

- [ ] **Step 2: Run the rewritten tests to verify they fail**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: FAIL (old `Scheduler` still calls callback with 2 args; new tests expect 1)

- [ ] **Step 3: Rewrite the scheduler drain**

In `src/scheduler/scheduler.py`:

(i) Callback type (`:30`):

```python
        callback: Callable[[list[tuple[str, Any]]], Awaitable[None]],
```

(ii) Add a module-level helper after `_PRIORITY_MAP` (`:15`):

```python
def _type_counts(events: list[tuple[str, Any]]) -> str:
    """Compact `type:count` summary for the drain-cap WARNING, e.g. 'alert:18 conditional:2'."""
    counts: dict[str, int] = {}
    for trigger_type, _ in events:
        counts[trigger_type] = counts.get(trigger_type, 0) + 1
    return " ".join(f"{k}:{v}" for k, v in sorted(counts.items()))
```

(iii) Bootstrap call (`:58`):

```python
        await self._run_cycle([("scheduled", None)])
```

(iv) Drain loop (`:67-75`) — replace the `for` loop block with:

```python
            if self._pending_events:
                events: list[tuple[str, Any]] = []
                while self._pending_events and len(events) < 20:
                    ev = heapq.heappop(self._pending_events)   # heap already priority-ordered
                    events.append((ev.trigger_type, ev.context))
                deferred = len(self._pending_events)           # leftover == post-drain heap depth
                if deferred > 0:                               # ⟺ started with strictly >20
                    logger.warning(
                        "event drain capped: drained=%d deferred=%d total=%d types=%s",
                        len(events), deferred, len(events) + deferred, _type_counts(events),
                    )
                await self._run_cycle(events)                  # ONE cycle consumes the batch
            else:
                await self._run_cycle([("scheduled", None)])
```

(v) `_run_cycle` (`:82-89`):

```python
    async def _run_cycle(self, events: list[tuple[str, Any]]) -> None:
        self._cycle_running = True
        try:
            await self._callback(events)
        except Exception:
            logger.exception("Agent cycle failed")
        finally:
            self._cycle_running = False
```

- [ ] **Step 4: Run scheduler tests to verify they pass**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: PASS

#### 4b — `run_agent_cycle(events)` + `on_tick` + 39 call-site migration

- [ ] **Step 5: Migrate `run_agent_cycle` signature + prompt assembly + persistence + render-ctx**

In `src/cli/app.py`:

(i) Signature (`:485-495`) — replace `trigger_type` + `context` with `events`:

```python
async def run_agent_cycle(
    agent,
    deps: TradingDeps,
    events: list[tuple[str, object]],
    budget: TokenBudget,
    engine,
    model=None,
    console=None,
    stats: SessionStats | None = None,
):
```

(ii) Derive dominant type + capture list. Replace the capture line (`:510`):

```python
    trigger_context_var = _capture_trigger_contexts(cycle_id, events)
```

and add, right after it (before `_capture_state_snapshot`):

```python
    # triggered_by = dominant (highest-priority) type — events arrive in heap priority
    # order (conditional > alert > scheduled), so the lead element is the dominant type.
    triggered_by = events[0][0]
```

Swap the import (`app.py:40`). After this task `run_agent_cycle` no longer calls the singular `_capture_trigger_context` directly (only the plural wrapper does, inside `cycle_capture.py`), so replace it to avoid an unused import:

```python
# was: from src.services.cycle_capture import _capture_state_snapshot, _capture_trigger_context
from src.services.cycle_capture import _capture_state_snapshot, _capture_trigger_contexts
```

(iii) Prompt assembly — replace the whole inline block (`:516-573`, from the `# Wake-event time clause` comment through the end of the `elif trigger_type == "alert"` block) with:

```python
    # Wake prompt (spec 2026-06-08 §2): priority-sectioned. N==1 is byte-identical to the
    # prior single-event prompt; N>1 uses a multi-trigger header + one block per event in
    # heap priority order (fills before alerts).
    header_line = _wake_header_line(events, cycle_started_at)
    prompt = (
        f"{header_line}.\n"
        f"Trading pair: {deps.symbol} | Timeframe: {deps.timeframe}\n"
        "Assess the situation and decide what to do."
    )
    for tt, ctx in events:
        prompt += await _render_event_block(deps, tt, ctx, cycle_started_at)
```

(iv) Persistence — in all **three** `AgentCycle(...)` constructions (usage-limit `:614`, retry-exhausted `:667`, success `:770`), change `triggered_by=trigger_type` → `triggered_by=triggered_by`. The `trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None` lines are unchanged (the list is non-empty → truthy → a JSON array; `[None]` is also truthy, preserving count).

(v) Render context — in all **three** `CycleRenderContext(...)` constructions (`:643`, `:696`, `:807`), change `trigger_type=trigger_type, trigger_context=trigger_context_var` → `trigger_type=triggered_by, trigger_context=trigger_context_var` (trigger_context_var is already the list).

(vi) `on_tick` (`:1080-1087`):

```python
    async def on_tick(events):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(
                agent, deps, events, budget, engine,
                model=result.model, console=sc, stats=stats,
            )
        except Exception:
            logger.exception("Agent cycle failed")
```

- [ ] **Step 6: Migrate all 39 `run_agent_cycle` call sites in the 8 test files**

Mechanical transform: `trigger_type=X, ..., context=Y` (or positional `X, budget, engine, context=Y`) → `events=[(X, Y)]`. Counts per file: `test_usage_limits` 13, `test_cli_app` 6, `test_agent_cycle_injection` 6, `test_p4_cycle_capture` 5, `test_cycle_log` 3, `test_run_agent_cycle_phase1` 3, `test_cycle_summary_injection` 2, `test_wake_event_timestamp` 1.

Two canonical forms:

```python
# positional trigger + keyword context:
#   run_agent_cycle(agent, deps, "scheduled", budget, engine, context=None, model="test-model")
# becomes:
    run_agent_cycle(agent, deps, [("scheduled", None)], budget, engine, model="test-model")

# all-keyword:
#   run_agent_cycle(agent=agent, deps=deps, trigger_type="conditional", budget=..., engine=engine, context=fill)
# becomes:
    run_agent_cycle(agent=agent, deps=deps, events=[("conditional", fill)], budget=..., engine=engine)
```

Find them with: `grep -rn "run_agent_cycle(" tests/`. Any test that asserts on the persisted `triggered_by` column or the rendered Header still passes (single-event → dominant type == the original type; Header for N==1 is bare type — update any test that asserted Header *detail* for a single event to assert the bare type instead, mirroring Task 2a).

- [ ] **Step 7: Add the N==1 byte-identical regression + N>1 integration tests**

In `tests/test_wake_event_timestamp.py` (or `test_cli_app.py`, wherever the prompt is captured), add a test that runs `run_agent_cycle` with `events=[("scheduled", None)]` and asserts the captured prompt **equals** the exact current scheduled prompt string (lock byte-identity); similarly one for a single conditional fill and one for a single price-level alert (reuse the existing captured-prompt harness). Add an N>1 test: `events=[("conditional", fill), ("alert", alert)]` → prompt header `You have been woken up by 2 triggers (1 fill, 1 alert) since the last cycle.`, fill block before alert block, each with its age suffix; persisted `triggered_by == "conditional"`; `json.loads(trigger_context)` is a 2-element array.

- [ ] **Step 8: Run the full affected suite to verify green**

Run: `python -m pytest tests/test_scheduler.py tests/test_cli_app.py tests/test_usage_limits.py tests/test_p4_cycle_capture.py tests/test_agent_cycle_injection.py tests/test_cycle_log.py tests/test_wake_event_timestamp.py tests/test_run_agent_cycle_phase1.py tests/test_cycle_summary_injection.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/scheduler/scheduler.py src/cli/app.py tests/test_scheduler.py \
  tests/test_usage_limits.py tests/test_cli_app.py tests/test_p4_cycle_capture.py \
  tests/test_agent_cycle_injection.py tests/test_cycle_log.py \
  tests/test_wake_event_timestamp.py tests/test_run_agent_cycle_phase1.py \
  tests/test_cycle_summary_injection.py
git commit -m "feat(scheduler,app): batch event drain — one cycle consumes all pending events"
```

---

### Task 5: `v_alert_lifecycle` array-aware rewrite

**Files:**
- Modify: `src/storage/views.py` — `V_ALERT_LIFECYCLE_SQL` `triggers` CTE (`:102-111`) + header comment (`:88-92`)
- Test: `tests/test_v_alert_lifecycle.py` (existing legacy-object tests must keep passing; add array + batched-with-fill tests)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_v_alert_lifecycle.py` (the existing `test_alert_lifecycle_triggered_state` already covers the **legacy single-object** row — it must keep passing as the back-compat guarantee; do not change it):

```python
@pytest.mark.asyncio
async def test_alert_lifecycle_triggered_via_array(db_session):
    """New model: trigger_context is a JSON array; the price_level_alert element resolves."""
    db_session.add(TradeAction(
        session_id="test-arr", cycle_id="c1", action="add_price_level_alert",
        alert_id="arr0001", symbol="BTC/USDT:USDT", price=80000.0, reasoning="above 80000",
    ))
    db_session.add(AgentCycle(
        session_id="test-arr", cycle_id="c2", triggered_by="alert",
        trigger_context=json.dumps([
            {"type": "price_level_alert", "alert_id": "arr0001",
             "current_price": 80050.0, "target_price": 80000.0, "direction": "above"},
        ]),
        state_snapshot=json.dumps({"position": None}), decision="hold",
    ))
    await db_session.commit()
    row = (await db_session.execute(text(
        "SELECT final_status, triggered_price FROM v_alert_lifecycle WHERE alert_id='arr0001'"
    ))).mappings().one()
    assert row["final_status"] == "triggered"
    assert row["triggered_price"] == 80050.0


@pytest.mark.asyncio
async def test_alert_lifecycle_alert_batched_with_fill(db_session):
    """A price-level alert batched with a fill has triggered_by='conditional' — the
    dropped `triggered_by='alert'` clause means it must STILL resolve."""
    db_session.add(TradeAction(
        session_id="test-mix", cycle_id="c1", action="add_price_level_alert",
        alert_id="mix0001", symbol="BTC/USDT:USDT", price=80000.0, reasoning="above 80000",
    ))
    db_session.add(AgentCycle(
        session_id="test-mix", cycle_id="c2", triggered_by="conditional",  # dominant = fill
        trigger_context=json.dumps([
            {"type": "fill", "trigger_reason": "tp", "symbol": "BTC/USDT:USDT"},
            {"type": "price_level_alert", "alert_id": "mix0001",
             "current_price": 80050.0, "target_price": 80000.0, "direction": "above"},
        ]),
        state_snapshot=json.dumps({"position": None}), decision="hold",
    ))
    await db_session.commit()
    row = (await db_session.execute(text(
        "SELECT final_status, triggered_price FROM v_alert_lifecycle WHERE alert_id='mix0001'"
    ))).mappings().one()
    assert row["final_status"] == "triggered"
    assert row["triggered_price"] == 80050.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_v_alert_lifecycle.py::test_alert_lifecycle_alert_batched_with_fill -v`
Expected: FAIL (old view filters `triggered_by='alert'` → the conditional-dominant batch row is dropped; `json_extract(array, '$.alert_id')` is NULL → no match)

- [ ] **Step 3: Rewrite the `triggers` CTE**

In `src/storage/views.py`, replace the `triggers` CTE (`:102-111`) with:

```sql
triggers AS (
  -- spec 2026-06-08: trigger_context is now a JSON array (one element per drained event);
  -- unnest it. Legacy single-object rows are wrapped in json_array() first — bare
  -- json_each('{...}') on an object iterates BY KEY (one row per field), polluting the
  -- result. Drop the old `triggered_by='alert'` clause: a price-level alert batched with a
  -- fill has triggered_by='conditional', so that clause would silently drop it; filter
  -- per-element on '$.type' instead. ALL per-element reads come from json_each.value.
  SELECT ac.session_id,
         json_extract(e.value, '$.alert_id') AS alert_id,
         ac.created_at AS triggered_at,
         CAST(json_extract(e.value, '$.current_price') AS REAL) AS triggered_price
  FROM agent_cycles ac,
       json_each(
         CASE WHEN json_type(ac.trigger_context) = 'array'
              THEN ac.trigger_context
              ELSE json_array(json(ac.trigger_context)) END
       ) e
  WHERE json_extract(e.value, '$.type') = 'price_level_alert'
    AND json_extract(e.value, '$.alert_id') IS NOT NULL
),
```

And update the stale header comment (`:88-92`) to the array-aware example:

```sql
-- direction 不在本 view 投影 (spec §4.2 OOS); analyst 需 above/below 时从 trigger_context
-- 数组取 (spec 2026-06-08 起 trigger_context 是 JSON 数组):
--   SELECT json_extract(e.value, '$.direction')
--   FROM agent_cycles ac,
--        json_each(CASE WHEN json_type(ac.trigger_context)='array' THEN ac.trigger_context
--                       ELSE json_array(json(ac.trigger_context)) END) e
--   WHERE ac.cycle_id = <triggered cycle> AND json_extract(e.value,'$.type')='price_level_alert'
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_v_alert_lifecycle.py tests/test_view_historical_compat.py tests/test_sim_metrics.py tests/test_analyze_sim.py -v`
Expected: PASS (new array tests + legacy-object back-compat + downstream view consumers)

- [ ] **Step 5: Commit**

```bash
git add src/storage/views.py tests/test_v_alert_lifecycle.py
git commit -m "feat(views): v_alert_lifecycle unnest trigger_context array (drop triggered_by='alert')"
```

---

### Task 6: Full-suite green + observability anchor

**Files:**
- (verify only) full `pytest`
- Modify: `docs/superpowers/specs/2026-06-08-batch-event-drain-design.md` — already carries the observability caveat (§3); no change needed unless a gap surfaces
- Memory anchor (after merge) per project convention

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: green. Baseline was `2144 passed`; net count shifts down (deleted `test_scheduler_drain_respects_stop` + deleted `_format_trigger_detail` detail-branch tests; added capture/header/extract/array/byte-identical/N>1 tests). The expected end state is **all passing** — investigate any failure, do not adjust the count blindly.

- [ ] **Step 2: Verify no stale 2-arg callback / `trigger_type=`/`context=` call sites remain**

Run:
```bash
grep -rn "run_agent_cycle(" tests/ src/ | grep -E "trigger_type=|context=" || echo "clean"
grep -rn "async def callback(trigger_type" tests/ || echo "clean"
grep -rn "_run_cycle(\"scheduled\"" src/ || echo "clean"
```
Expected: `clean` on all three.

- [ ] **Step 3: Verify the empirical caveat is documented**

Confirm the spec §3 "Observability caveat — a cycle is no longer a single-event unit" paragraph is present (it is, in the committed spec). No code change; this step is the explicit `feedback_data_mismatch_old_impl_inference` checkpoint — post-merge sim comparisons must be event- or time-denominated across the batch boundary.

- [ ] **Step 4: Final commit (if any test-count/cleanup adjustments were needed)**

```bash
git add -A
git commit -m "test: batch event drain full-suite green"
```

---

## Self-Review

### Spec coverage

| Spec section | Task |
|---|---|
| §1 drain-all + cap-20 + WARNING (`deferred>0`) + callback contract + `:58` bootstrap | Task 4a |
| §1 `_type_counts` | Task 4a (ii) |
| §2 priority-sectioned prompt, N==1 byte-identical, N>1 header | Task 3 + Task 4b (iii) + Task 4b step 7 |
| §2 `triggered_by` = dominant type | Task 4b (ii) |
| §2 capture-once before retry loop (unchanged) | Task 4b (ii) — capture stays at `:510-511` position |
| §3 `trigger_context` JSON array, no migration/column | Task 1 + Task 4b (iv) |
| §3 `_capture_trigger_contexts` + all-fail `[None]*n` | Task 1 |
| §3 `v_alert_lifecycle` array-aware (json_array wrap, drop `triggered_by='alert'`, per-element reads, comment) | Task 5 |
| §3 Header type+count; Context per-event bullets; `_render_header` fail-isolation; `_extract_scheduled_wake_suffix` unchanged | Task 2 |
| §3 observability caveat | Task 6 (3) |
| §4 edge cases (spike/persistent/memory/set_next_wake_at) | No code (accepted/noted) — WARNING from Task 4a is the diagnostic |
| §5 testing (scheduler rewrite, ~40 call sites, byte-identical, N>1, capture, view, display) | Tasks 1-5 |

### Placeholder scan
No `TBD`/`TODO`/"add error handling"; every code step shows complete code. ✓

### Type consistency
- `events: list[tuple[str, Any]]` (scheduler) / `list[tuple[str, object]]` (app signature) — same shape, tuple of `(trigger_type, context)`. ✓
- `_capture_trigger_contexts(cycle_id, events) -> list[dict | None]` — used in Task 4b (ii); defined Task 1. ✓
- `_wake_header_line(events, cycle_started_at) -> str` / `_render_event_block(deps, trigger_type, context, cycle_started_at) -> str` — defined Task 3, used Task 4b (iii). ✓
- `_extract_event_lines(wake_half, trigger_type) -> list[str]` — defined Task 2b, used in `_render_context`. ✓
- `_format_trigger_detail(trigger_type, ctx)` — `ctx: list[dict|None] | dict | None`; `CycleRenderContext.trigger_context` annotation matches (Task 2b iv). ✓
- `triggered_by = events[0][0]` (dominant type) — persisted + passed to `CycleRenderContext.trigger_type`. ✓
