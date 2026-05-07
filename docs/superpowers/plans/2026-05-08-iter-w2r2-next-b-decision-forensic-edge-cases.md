# R2-Next-B — Decision Forensic Edge Cases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close 2 decision-forensic edge cases surfaced in W2 sim #8 — (F-P13) `place_limit_order` agent mental-model gap on async fills, and (F-P14) R2-8b priors blind to `retry_exhausted` / `usage_limit_exceeded` cycles via NULL-decision tri-state rendering.

**Architecture:** Two independent code paths in one PR (D1: same "decision forensic" theme; both improvements ~50 src lines). F-P13 = single-line tool return string change in `src/agent/tools_execution.py:356`. F-P14 = remove SQL filter in `_fetch_recent_summaries`, extend `CycleSummary` with `execution_status`, add `_render_empty_decision_body` helper, branch `_render_recent_summaries` on NULL decision. No schema migration. No persona Layer 1 changes. No write-path changes (retry_exhausted still writes `decision=None, reasoning=None`).

**Tech Stack:** Python 3.13, pydantic-ai, SQLAlchemy async, pytest + pytest-asyncio + pytest-mock. TDD discipline per `superpowers:test-driven-development`.

**Source spec:** `docs/superpowers/specs/2026-05-08-iter-w2r2-next-b-decision-forensic-edge-cases-design.md` (commit `a0efc4f`)

**Predecessors:** R2-7 (PR #35, schema reframe) / R2-8a (PR #36, cycle log narrative) / R2-8b (PR #38, priors injection — main change target) / R2-Next-A (PR #40, F1 length feedback)

---

## File Structure

| File | Role | Action |
|---|---|---|
| `src/agent/tools_execution.py` | F-P13 — `place_limit_order` return string adds async-note 2nd line | Modify line 356 |
| `src/cli/app.py` | F-P14 — `CycleSummary` dataclass / `_fetch_recent_summaries` query / `_render_recent_summaries` tri-state / new `_render_empty_decision_body` | Modify lines 161-268 (~+25/-8) |
| `tests/test_fact_only_wordlist.py` | F-P13 — happy-path invoker + 3 new tests | Append helper + 3 tests (~50 lines) |
| `tests/test_cycle_summary_injection.py` | F-P14 — extend `_make_summary` factory, rewrite 2 existing tests, add 9 new tests | Modify factory at line 426; rewrite line 324 + line 407; append T-FP14.x at end (~180 lines net) |
| `tests/test_display_cycle.py` | F-P13 — verify multi-line tool return still summarizes via `_summarize_place_limit_order` regex | Modify `test_summarize_place_limit_order` at line 280 (~+5 lines) |
| `docs/superpowers/plans/2026-05-08-iter-w2r2-next-b-decision-forensic-edge-cases.md` | THIS plan doc | New (~900 lines) |

**Net code change estimate:** ~50 src + ~235 test = ~285 lines code + spec/plan docs.

**Test count:** 1230 → 1242 passed + 3 skip (+12 net = +3 F-P13 + +9 F-P14).

---

## Pre-flight check

- [ ] **Step 0.1: Confirm branch state**

```bash
git status
git log -1 --oneline
```

Expected:
- Branch: `feature/iter-w2r2-next-b-decision-forensic-edge-cases`
- HEAD: `a0efc4f docs(iter-w2r2-next-b): decision forensic edge cases design spec`
- Working tree clean.

- [ ] **Step 0.2: Confirm baseline test count**

```bash
uv run pytest --collect-only -q 2>&1 | tail -3
```

Expected baseline: `1233 tests collected` (1230 passed + 3 skipped per memory `project_w2_prep_progress` §15).

If counts differ from baseline ± 1, **STOP** and reconcile before proceeding (probably a test was renamed in a sibling PR).

---

## Task 1: Plan doc commit

> Per memory `feedback_plan_doc_commit_first` — plan doc is its own commit before any code change.

**Files:**
- Create: `docs/superpowers/plans/2026-05-08-iter-w2r2-next-b-decision-forensic-edge-cases.md` (this file)

- [ ] **Step 1.1: Commit plan doc**

```bash
git add docs/superpowers/plans/2026-05-08-iter-w2r2-next-b-decision-forensic-edge-cases.md
git commit -m "$(cat <<'EOF'
docs(iter-w2r2-next-b): decision forensic edge cases impl plan

TDD task decomposition for spec a0efc4f (F-P13 place_limit_order async
note + F-P14 R2-8b priors NULL decision tri-state rendering). 8 tasks,
+12 tests net (1230 → 1242 + 3 skip), no schema migration.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Expected: commit lands on `feature/iter-w2r2-next-b-decision-forensic-edge-cases` branch.

---

## Task 2: F-P13 — `place_limit_order` async note

**Files:**
- Modify: `src/agent/tools_execution.py:356`
- Test: `tests/test_fact_only_wordlist.py` (append after line 730 — end of file)
- Test: `tests/test_display_cycle.py:280-286` (modify existing test)

**Spec refs:** §3.1, AC-1/2/3, T-FP13.1/2/3.

### Step 2.1: Add `_invoke_place_limit_order_happy` helper to test file

The existing `_invoke_place_limit_order` (line 716-719) takes early-return path with `side="neutral"` — does **NOT** cover the new `Note:` 2nd line. Create a parallel happy-path helper.

- [ ] **Step 2.1.1: Edit `tests/test_fact_only_wordlist.py` — append helper before the existing `_invoke_place_limit_order` definition is fine, or after `test_save_memory_fact_only` at end of file (line 731). Append at end.**

Add this helper right after `test_save_memory_fact_only` (last function in file, line 731):

```python


async def _invoke_place_limit_order_happy(deps, mocker):
    """F-P13: happy path through create_order — covers new multi-line `Note:` return."""
    from src.agent.tools_execution import place_limit_order
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.set_leverage = AsyncMock(return_value=None)
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=8000.0, used_usdt=2000.0,
    ))
    deps.exchange.amount_to_precision = mocker.Mock(return_value=0.05)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="abc12345-6789-0123-4567-89abcdef0123",  # UUID-shaped
        symbol="BTC/USDT:USDT", side="buy", order_type="limit",
        amount=0.05, price=80000.0, status="open",
    ))
    return await place_limit_order(
        deps, "long", 80000.0, 10.0, 5, reasoning="test entry",
    )
```

Run: `uv run pytest tests/test_fact_only_wordlist.py -q --collect-only 2>&1 | tail -3`
Expected: helper not picked as a test (no `test_` prefix), test count unchanged.

### Step 2.2: Write failing tests T-FP13.1/2/3

- [ ] **Step 2.2.1: Append 3 tests after the new helper from Step 2.1.1.**

```python


@pytest.mark.asyncio
async def test_place_limit_order_return_format_unchanged(mocker):
    """T-FP13.1 (AC-2): 'ID:' + UUID format strong assertion.

    order.id = str(uuid.uuid4()) — assert head 8 hex + dash explicit
    UUID shape so a simple 8-hex regex match doesn't pass weakly.
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    assert "ID: " in result
    assert re.search(r"ID: [a-f0-9]{8}-", result), \
        f"expected UUID format ID: xxxxxxxx-..., got: {result}"


@pytest.mark.asyncio
async def test_place_limit_order_return_includes_async_note(mocker):
    """T-FP13.2 (AC-1): return string contains 'only submits' AND 'has been filled'.

    sim #8 cycle 4de0585a 实证误读对齐：agent prose 用词 'limit not filled'，
    提示用 'has been filled' 命中相同 mental concept。
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    assert "only submits" in result
    assert "has been filled" in result


@pytest.mark.asyncio
async def test_place_limit_order_return_no_decision_label(mocker):
    """T-FP13.3 (AC-3): fact-only regression — _scan(output) helper applies
    full FACT_ONLY_BANNED_WORDS_RE + FACT_ONLY_BANNED_PHRASES_RE regex sets.
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    hits = _scan(result)
    assert hits == [], f"banned regex hits: {hits}"
```

- [ ] **Step 2.2.2: Run tests — expect FAIL on T-FP13.2 (and `_invoke_place_limit_order` parametrize still passes since it's the early-return)**

Run: `uv run pytest tests/test_fact_only_wordlist.py::test_place_limit_order_return_includes_async_note tests/test_fact_only_wordlist.py::test_place_limit_order_return_format_unchanged tests/test_fact_only_wordlist.py::test_place_limit_order_return_no_decision_label -v`

Expected:
- `test_place_limit_order_return_format_unchanged`: PASS (existing return already has `"ID: "` + UUID)
- `test_place_limit_order_return_includes_async_note`: **FAIL** with `assert "only submits" in result` not satisfied
- `test_place_limit_order_return_no_decision_label`: PASS (existing return is fact-clean)

### Step 2.3: Implement F-P13 — modify `place_limit_order` return

- [ ] **Step 2.3.1: Edit `src/agent/tools_execution.py:356`**

Change:

```python
    return f"Limit order placed: {side} {quantity:.6f} @ {price:.2f}, {actual_leverage}x | ID: {order.id}"
```

To:

```python
    return (
        f"Limit order placed: {side} {quantity:.6f} @ {price:.2f}, "
        f"{actual_leverage}x | ID: {order.id}\n"
        "Note: This tool only submits the order — it does not mean the order has been filled."
    )
```

Note the unicode em-dash (`—`, U+2014) — copy-paste exactly. The runtime first-line content is byte-identical to the prior return (Python implicit string concat across the two f-strings produces the same bytes), only `\n` + the `Note:` line is appended.

### Step 2.4: Run T-FP13 tests — expect PASS

- [ ] **Step 2.4.1: Re-run T-FP13 tests**

Run: `uv run pytest tests/test_fact_only_wordlist.py::test_place_limit_order_return_format_unchanged tests/test_fact_only_wordlist.py::test_place_limit_order_return_includes_async_note tests/test_fact_only_wordlist.py::test_place_limit_order_return_no_decision_label tests/test_fact_only_wordlist.py::test_execution_tool_fact_only -v`

Expected: all PASS (3 new + parametrize 10 → 13 collected).

### Step 2.5: Update `test_summarize_place_limit_order` for multi-line robustness

The `_summarize_place_limit_order` regex (`src/cli/display.py:214`) matches `r"Limit order placed:\s*(\w+)\s+([\d.]+)\s*@\s*([\d.]+),\s*(\d+)x"` — only the first line. Adding `\n Note: ...` should not break the existing assertion, but make this explicit.

- [ ] **Step 2.5.1: Edit `tests/test_display_cycle.py:280-286`**

Change:

```python
def test_summarize_place_limit_order():
    from src.cli.display import summarize_tool
    content = "Limit order placed: long 0.050000 @ 83000.00, 3x | ID: lmt-789"
    result = summarize_tool("place_limit_order", content)
    assert "Limit" in result or "limit" in result
    assert "long" in result.lower()
    assert "83000" in result or "83,000" in result
```

To:

```python
def test_summarize_place_limit_order():
    """F-P13: tool return is now multi-line (Note: line appended).
    summarize_tool regex on display.py:214 matches only first line, so
    existing single-line summary still extracts correctly."""
    from src.cli.display import summarize_tool
    content = (
        "Limit order placed: long 0.050000 @ 83000.00, 3x | ID: lmt-789\n"
        "Note: This tool only submits the order — it does not mean the order has been filled."
    )
    result = summarize_tool("place_limit_order", content)
    assert "Limit" in result or "limit" in result
    assert "long" in result.lower()
    assert "83000" in result or "83,000" in result
```

### Step 2.6: Run summarize test — expect PASS

- [ ] **Step 2.6.1: Run summarize test**

Run: `uv run pytest tests/test_display_cycle.py::test_summarize_place_limit_order -v`

Expected: PASS.

### Step 2.7: Run full suite for F-P13 regression check

- [ ] **Step 2.7.1: Run all tests touching `place_limit_order` / `tools_execution`**

Run: `uv run pytest tests/test_fact_only_wordlist.py tests/test_display_cycle.py -q`

Expected: all PASS, no regressions.

### Step 2.8: Commit F-P13

- [ ] **Step 2.8.1: Stage and commit**

```bash
git add src/agent/tools_execution.py tests/test_fact_only_wordlist.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-b): F-P13 place_limit_order async note (D2+D3)

place_limit_order tool return now appends a 2nd line teaching the
agent that the tool only submits, not confirms fill. Closes mental
model gap from sim #8 cycle 4de0585a (15ms async fill misread as
'limit not filled' for one full cycle).

- src/agent/tools_execution.py:356 — multi-line return
- tests/test_fact_only_wordlist.py — _invoke_place_limit_order_happy
  helper + T-FP13.1/2/3 (UUID format / async note / fact-only)
- tests/test_display_cycle.py — multi-line robustness assertion for
  _summarize_place_limit_order (regex only matches first line)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: F-P14 step a — `_render_empty_decision_body` helper

**Files:**
- Modify: `src/cli/app.py` — add new helper before `_render_recent_summaries` (around line 238)
- Test: `tests/test_cycle_summary_injection.py` — append 4 unit tests at end of file

**Spec refs:** §3.2.5, AC-7/8/9/10, T-FP14.4/5/6/7, D9/D9.a/D9.b/D10.

### Step 3.1: Write failing tests T-FP14.4/5/6/7

- [ ] **Step 3.1.1: Append 4 unit tests at end of `tests/test_cycle_summary_injection.py` (after line 611)**

```python


def test_render_empty_decision_body_ok():
    """T-FP14.4 (AC-7, F-P14 D9): ok+NULL → `(This cycle did not leave a summary.)`.

    Defensive branch: pydantic-ai `result.output` can rarely be empty when
    agent emits only tool calls without a final TextPart.
    """
    from src.cli.app import _render_empty_decision_body
    assert _render_empty_decision_body("ok") == \
        "(This cycle did not leave a summary.)"


def test_render_empty_decision_body_retry_exhausted():
    """T-FP14.5 (AC-8, F-P14 D9.a/D9.b): retry_exhausted →
    ⚠️ + agent-native verify hint (functional dim, no schema/tool name leak).
    """
    from src.cli.app import _render_empty_decision_body
    body = _render_empty_decision_body("retry_exhausted")
    # positive: agent-facing functional content
    assert "⚠️" in body
    assert "did not complete normally" in body
    assert "position" in body
    assert "pending orders" in body
    assert "alerts" in body
    assert "verify" in body
    # negative: schema artifact must NOT leak into agent prompt
    assert "retry_exhausted" not in body
    assert "get_position" not in body
    assert "get_open_orders" not in body
    assert "get_active_alerts" not in body


def test_render_empty_decision_body_usage_limit_exceeded():
    """T-FP14.6 (AC-9, F-P14 D9): usage_limit_exceeded → identical body
    as retry_exhausted (agent's response to either is the same).
    """
    from src.cli.app import _render_empty_decision_body
    body_retry = _render_empty_decision_body("retry_exhausted")
    body_ulx = _render_empty_decision_body("usage_limit_exceeded")
    assert body_retry == body_ulx  # exact equality (D9)
    assert "usage_limit_exceeded" not in body_ulx  # negative: no schema leak


def test_render_empty_decision_body_unknown_fallback():
    """T-FP14.7 (AC-10, F-P14 D10): forward compat — unknown status →
    fixed fallback string, value NOT interpolated (防 prompt 污染)."""
    from src.cli.app import _render_empty_decision_body
    body = _render_empty_decision_body("future_unknown_status")
    assert body == "(The previous cycle ended in an unexpected state.)"
    # negative: status value must NOT be interpolated
    assert "future_unknown_status" not in body
```

- [ ] **Step 3.1.2: Run tests — expect import error / NameError**

Run: `uv run pytest tests/test_cycle_summary_injection.py::test_render_empty_decision_body_ok tests/test_cycle_summary_injection.py::test_render_empty_decision_body_retry_exhausted tests/test_cycle_summary_injection.py::test_render_empty_decision_body_usage_limit_exceeded tests/test_cycle_summary_injection.py::test_render_empty_decision_body_unknown_fallback -v`

Expected: FAIL with `ImportError: cannot import name '_render_empty_decision_body' from 'src.cli.app'`.

### Step 3.2: Implement `_render_empty_decision_body`

- [ ] **Step 3.2.1: Edit `src/cli/app.py` — insert new helper between `_truncate_decision` (line 158, ends at "return text") and `CycleSummary` dataclass (line 161)**

Insert these lines after line 158:

```python


def _render_empty_decision_body(execution_status: str) -> str:
    """Render system-generated body for cycles that left no decision summary.

    Three known statuses (internal branching, but agent-facing text exposes
    NO schema field names — agent reads natural language only):
      - 'ok' + NULL/empty decision: defensive branch — cycle ran successfully
        but agent emitted no final message text (rare; pydantic-ai
        `result.output` can be "" or None when agent only emits tool calls
        without a final TextPart)
      - 'retry_exhausted': all retry attempts failed; partial trade_actions
        may have committed before abort
      - 'usage_limit_exceeded': UsageLimitExceeded raised mid-cycle; partial
        trade_actions may have committed

    `retry_exhausted` and `usage_limit_exceeded` share identical agent-facing
    text (D9): the agent's response to either is the same — re-verify state.
    Status differentiation is a developer-layer concern (DB / cycle log).

    Unknown statuses fall through to a fixed fallback string for forward
    compatibility with future execution_status enum extensions; the status
    value is NOT interpolated into the agent-facing text (would expose schema
    artifact + open prompt-injection surface).

    Note: this function returns a system-generated body inserted into the
    priors block in place of agent-authored decision content. Length-budget
    accounting (R2-Next-A D2) tracks agent decision length only; system
    bodies are not counted in the per-prior word_count header (header is
    shortened to omit the `· N words` segment when decision is NULL).
    """
    if execution_status == "ok":
        return "(This cycle did not leave a summary.)"
    if execution_status in ("retry_exhausted", "usage_limit_exceeded"):
        return (
            "⚠️ The previous cycle did not complete normally. Some actions "
            "may have already taken effect. Please verify the current state "
            "of your position, pending orders, and alerts before deciding "
            "what to do."
        )
    return "(The previous cycle ended in an unexpected state.)"
```

Note unicode characters: `⚠️` (U+26A0 + U+FE0F variant selector) and em-dash `—` (U+2014). Copy-paste exactly.

### Step 3.3: Run tests — expect PASS

- [ ] **Step 3.3.1: Re-run T-FP14.4/5/6/7**

Run: `uv run pytest tests/test_cycle_summary_injection.py::test_render_empty_decision_body_ok tests/test_cycle_summary_injection.py::test_render_empty_decision_body_retry_exhausted tests/test_cycle_summary_injection.py::test_render_empty_decision_body_usage_limit_exceeded tests/test_cycle_summary_injection.py::test_render_empty_decision_body_unknown_fallback -v`

Expected: all 4 PASS.

### Step 3.4: Commit T3 helper

- [ ] **Step 3.4.1: Stage and commit**

```bash
git add src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-b): F-P14 _render_empty_decision_body helper (D9/D10)

Tri-state body for priors with NULL decision:
- 'ok' (rare defensive case) → '(This cycle did not leave a summary.)'
- 'retry_exhausted' / 'usage_limit_exceeded' → ⚠️ + agent-native verify
  hint (D9 shared body; functional dim only — no schema/tool name leak)
- unknown → fixed fallback (D10 forward compat; status value NOT
  interpolated → no prompt-injection / schema-artifact surface)

T-FP14.4/5/6/7 cover all branches with positive + negative assertions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: F-P14 step b — `CycleSummary` field + factory + fetch SELECT

**Files:**
- Modify: `src/cli/app.py` lines 161-235 — `CycleSummary` dataclass + `_fetch_recent_summaries` SELECT clause + CycleSummary construction
- Modify: `tests/test_cycle_summary_injection.py:426-432` — `_make_summary` factory adds `execution_status` kwarg

**Spec refs:** §3.2.3 (CycleSummary), §3.2.2 (_fetch_recent_summaries select but NOT yet filter delete — that's Task 5), AC-5, T-FP14.2.

> **TDD scope split rationale:** Step 4 = additive only (add field, plumb through SELECT, populate constructor). Step 5 = remove filter (the behavior change). This split allows Step 4 to land with the existing filter still in place — the new field is plumbed but no behavior visible to the agent yet — keeping each commit reviewable.

### Step 4.1: Extend `_make_summary` factory

- [ ] **Step 4.1.1: Edit `tests/test_cycle_summary_injection.py:426-432`**

Change:

```python
def _make_summary(cycle_id, triggered_by, decision, created_at, sid=1):
    """Test-only CycleSummary builder."""
    from src.cli.app import CycleSummary
    return CycleSummary(
        id=sid, cycle_id=cycle_id, triggered_by=triggered_by,
        decision=decision, created_at=created_at,
    )
```

To:

```python
def _make_summary(cycle_id, triggered_by, decision, created_at,
                  sid=1, execution_status="ok"):
    """Test-only CycleSummary builder.

    F-P14: execution_status defaults to 'ok' so existing call sites
    (~10 in this file) remain compatible without per-callsite changes.
    """
    from src.cli.app import CycleSummary
    return CycleSummary(
        id=sid, cycle_id=cycle_id, triggered_by=triggered_by,
        decision=decision, execution_status=execution_status,
        created_at=created_at,
    )
```

### Step 4.2: Write failing test T-FP14.2

- [ ] **Step 4.2.1: Append T-FP14.2 at end of file (after T-FP14.7 from Task 3)**

```python


async def test_cycle_summary_execution_status_populated():
    """T-FP14.2 (AC-5, F-P14): CycleSummary.execution_status filled from query."""
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-fp14-2")
    # forensic cycle: filter still active in this task — use ok-only;
    # we'll add a retry_exhausted assertion in Task 5 once filter is removed.
    await _add_cycle(
        engine, "sess-fp14-2", "c1",
        decision="real summary", execution_status="ok",
    )
    rows = await _fetch_recent_summaries(engine, "sess-fp14-2", n=3)
    assert len(rows) == 1
    assert rows[0].execution_status == "ok"
```

- [ ] **Step 4.2.2: Run test — expect FAIL with AttributeError**

Run: `uv run pytest tests/test_cycle_summary_injection.py::test_cycle_summary_execution_status_populated -v`

Expected: FAIL with `AttributeError: 'CycleSummary' object has no attribute 'execution_status'`.

### Step 4.3: Add `execution_status` field to `CycleSummary` + plumb through fetch

- [ ] **Step 4.3.1: Edit `src/cli/app.py:161-174`**

Change:

```python
@dataclass(frozen=True)
class CycleSummary:
    """Snapshot of an AgentCycle row used for cross-cycle context injection.

    `id` is included as a tie-breaker for same-timestamp ordering stability
    (review F4): fast in-memory tests / rapid sequential inserts can produce
    multiple rows with identical created_at, and SQLite ORDER BY only on
    created_at would be non-deterministic.
    """
    id: int
    cycle_id: str
    triggered_by: str
    decision: str
    created_at: datetime
```

To:

```python
@dataclass(frozen=True)
class CycleSummary:
    """Snapshot of an AgentCycle row used for cross-cycle context injection.

    `id` is included as a tie-breaker for same-timestamp ordering stability
    (review F4): fast in-memory tests / rapid sequential inserts can produce
    multiple rows with identical created_at, and SQLite ORDER BY only on
    created_at would be non-deterministic.

    F-P14: `decision` is now Optional — retry_exhausted / usage_limit_exceeded
    cycles enter the priors list with decision=None and are rendered via
    `_render_empty_decision_body`. `execution_status` carries the cycle
    state for render-layer dispatch.
    """
    id: int
    cycle_id: str
    triggered_by: str
    decision: str | None
    execution_status: str
    created_at: datetime
```

### Step 4.4: Plumb `execution_status` through `_fetch_recent_summaries` SELECT + constructor

- [ ] **Step 4.4.1: Edit `src/cli/app.py:200-228`**

Change SELECT clause and CycleSummary construction. **Do NOT touch the WHERE filter in this task** — that's Task 5.

Change:

```python
            result = await session.execute(
                select(
                    AgentCycle.id,
                    AgentCycle.cycle_id,
                    AgentCycle.triggered_by,
                    AgentCycle.decision,
                    AgentCycle.created_at,
                )
                .where(
                    AgentCycle.session_id == session_id,
                    AgentCycle.execution_status == "ok",
                    AgentCycle.decision.is_not(None),
                )
                .order_by(
                    AgentCycle.created_at.desc(),
                    AgentCycle.id.desc(),
                )
                .limit(n)
            )
            rows = result.all()
        return [
            CycleSummary(
                id=r.id,
                cycle_id=r.cycle_id,
                triggered_by=r.triggered_by,
                decision=r.decision or "",
                created_at=r.created_at,
            )
            for r in rows
        ]
```

To:

```python
            result = await session.execute(
                select(
                    AgentCycle.id,
                    AgentCycle.cycle_id,
                    AgentCycle.triggered_by,
                    AgentCycle.decision,
                    AgentCycle.execution_status,
                    AgentCycle.created_at,
                )
                .where(
                    AgentCycle.session_id == session_id,
                    AgentCycle.execution_status == "ok",
                    AgentCycle.decision.is_not(None),
                )
                .order_by(
                    AgentCycle.created_at.desc(),
                    AgentCycle.id.desc(),
                )
                .limit(n)
            )
            rows = result.all()
        return [
            CycleSummary(
                id=r.id,
                cycle_id=r.cycle_id,
                triggered_by=r.triggered_by,
                decision=r.decision,
                execution_status=r.execution_status,
                created_at=r.created_at,
            )
            for r in rows
        ]
```

Two changes from the original:
1. `AgentCycle.execution_status` added to SELECT.
2. `decision=r.decision or ""` → `decision=r.decision` (allow None to flow through; type is `str | None`).
3. `execution_status=r.execution_status` added to constructor.

WHERE filter unchanged in this task.

### Step 4.5: Run T-FP14.2 — expect PASS

- [ ] **Step 4.5.1: Run T-FP14.2**

Run: `uv run pytest tests/test_cycle_summary_injection.py::test_cycle_summary_execution_status_populated -v`

Expected: PASS.

### Step 4.6: Run full test_cycle_summary_injection — confirm no regressions

- [ ] **Step 4.6.1: Run all cycle summary tests**

Run: `uv run pytest tests/test_cycle_summary_injection.py -v`

Expected: all PASS. The `_render_recent_summaries` tests still pass because `s.decision or ""` in line 260 (`_count_words(s.decision or "")`) handles None gracefully, and existing tests use `_make_summary(decision="some_text", ...)` (non-None). The original `s.decision or ""` cast on line 261 (`_truncate_decision(s.decision)`) — wait, that one passes `s.decision` directly without `or ""`. Let me re-check: line 261 is `body = _truncate_decision(s.decision)`. `_truncate_decision` does `_WORD_RE.finditer(text)` — would crash on None. **However**, in this task NULL decisions still get filtered out in `_fetch_recent_summaries` (filter not yet removed), so no NULL ever reaches `_render_recent_summaries`. Existing tests all use non-None decision values. So this task is regression-safe.

If any test fails here, **STOP** and inspect — the most likely candidate is a missing field in some test that constructs `CycleSummary` directly (not via `_make_summary`).

### Step 4.7: Commit Task 4

- [ ] **Step 4.7.1: Stage and commit**

```bash
git add src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-b): F-P14 CycleSummary execution_status field (T-FP14.2)

Additive plumbing — CycleSummary gains execution_status field, fetch
SELECT pulls the column, constructor populates it. Decision type
narrows from str to str|None for upcoming NULL-passthrough (Task 5).

WHERE filter (execution_status='ok' AND decision IS NOT NULL) still
in place — agent-visible behavior unchanged in this commit. Filter
removal lands in next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: F-P14 step c — Remove `_fetch_recent_summaries` filter + rewrite 2 existing tests

**Files:**
- Modify: `src/cli/app.py:177-235` — delete WHERE filter, rewrite docstring
- Modify: `tests/test_cycle_summary_injection.py` lines 324-343 + 407-421 — rewrite assertions
- Add: `tests/test_cycle_summary_injection.py` end of file — T-FP14.1

**Spec refs:** §3.2.2 (filter delete + docstring rewrite), AC-4, T-FP14.1, §5.4.1 + §5.4.2 (existing test rewrites).

### Step 5.1: Rewrite `test_fetch_excludes_forensic_cycles` → `test_fetch_includes_all_cycles_regardless_of_status`

- [ ] **Step 5.1.1: Edit `tests/test_cycle_summary_injection.py:324-343`**

Change:

```python
async def test_fetch_excludes_forensic_cycles():
    """T1.4: cycles with execution_status != 'ok' (forensic) are skipped;
    fetch returns the adjacent ok cycles."""
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-4")
    await _add_cycle(engine, "sess-t1-4", "aa11", decision="ok-1", execution_status="ok")
    # decision=None for forensic per cli/app.py:223,266
    await _add_cycle(
        engine, "sess-t1-4", "bb22", decision=None,
        execution_status="usage_limit_exceeded",
    )
    await _add_cycle(engine, "sess-t1-4", "cc33", decision="ok-2", execution_status="ok")
    await _add_cycle(
        engine, "sess-t1-4", "dd44", decision=None,
        execution_status="retry_exhausted",
    )

    rows = await _fetch_recent_summaries(engine, "sess-t1-4", n=3)
    assert {r.cycle_id for r in rows} == {"aa11", "cc33"}
```

To:

```python
async def test_fetch_includes_all_cycles_regardless_of_status():
    """T1.4 (rewritten for F-P14): cycles of all execution_status values
    (ok, usage_limit_exceeded, retry_exhausted) enter the priors list.
    Render-layer dispatch differentiates them via _render_empty_decision_body.
    """
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-4")
    # 4 cycles inserted in order; auto-increment id 1..4 → DESC LIMIT 3 → dd44/cc33/bb22
    await _add_cycle(engine, "sess-t1-4", "aa11", decision="ok-1", execution_status="ok")
    await _add_cycle(
        engine, "sess-t1-4", "bb22", decision=None,
        execution_status="usage_limit_exceeded",
    )
    await _add_cycle(engine, "sess-t1-4", "cc33", decision="ok-2", execution_status="ok")
    await _add_cycle(
        engine, "sess-t1-4", "dd44", decision=None,
        execution_status="retry_exhausted",
    )

    rows = await _fetch_recent_summaries(engine, "sess-t1-4", n=3)
    # All 3 most-recent cycles included (filter deleted)
    assert [r.cycle_id for r in rows] == ["dd44", "cc33", "bb22"]
    # Forensic statuses propagate through; decision is None for them
    assert rows[0].execution_status == "retry_exhausted"
    assert rows[0].decision is None
    assert rows[2].execution_status == "usage_limit_exceeded"
    assert rows[2].decision is None
```

### Step 5.2: Rewrite `test_fetch_excludes_cycles_with_null_decision` → `test_fetch_includes_ok_cycles_with_null_decision`

- [ ] **Step 5.2.1: Edit `tests/test_cycle_summary_injection.py:407-421`**

Change:

```python
async def test_fetch_excludes_cycles_with_null_decision():
    """T1.8 (review F2): a cycle with execution_status='ok' but decision=None
    should be physically filtered by `WHERE decision IS NOT NULL`. This is
    a defensive guard — the ok-path always writes decision=result.output, but
    if a future code path produces an ok cycle with NULL decision, the render
    block must not crash on `decision or ""` truncation downstream.
    """
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-8")
    await _add_cycle(engine, "sess-t1-8", "aa11", decision="real-summary")
    await _add_cycle(engine, "sess-t1-8", "bb22", decision=None)  # defensive case

    rows = await _fetch_recent_summaries(engine, "sess-t1-8", n=3)
    assert [r.cycle_id for r in rows] == ["aa11"]
```

To:

```python
async def test_fetch_includes_ok_cycles_with_null_decision():
    """T1.8 (rewritten for F-P14): an ok cycle with decision=None enters
    the priors list — render layer dispatches to _render_empty_decision_body
    with the 'ok' branch system body. Defensive case: pydantic-ai rarely
    produces ok+empty result.output when agent emits only tool calls
    without a final TextPart.
    """
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-t1-8")
    await _add_cycle(engine, "sess-t1-8", "aa11", decision="real-summary")
    await _add_cycle(engine, "sess-t1-8", "bb22", decision=None)  # ok+NULL defensive case

    rows = await _fetch_recent_summaries(engine, "sess-t1-8", n=3)
    # both included; bb22 most recent
    assert [r.cycle_id for r in rows] == ["bb22", "aa11"]
    assert rows[0].decision is None
    assert rows[0].execution_status == "ok"
```

### Step 5.3: Add T-FP14.1 (retry_exhausted explicit inclusion test)

- [ ] **Step 5.3.1: Append T-FP14.1 at end of file (after T-FP14.2 from Task 4 / before any T-FP14.3+ that will arrive in later tasks)**

```python


async def test_fetch_recent_summaries_includes_retry_exhausted():
    """T-FP14.1 (AC-4, F-P14): filter deletion → retry_exhausted cycle
    enters priors. Most recent first (DESC ordering preserved)."""
    from datetime import datetime, timezone, timedelta
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-fp14-1")
    base = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    await _add_cycle(
        engine, "sess-fp14-1", "c-ok",
        decision="real summary", execution_status="ok",
        created_at=base,
    )
    await _add_cycle(
        engine, "sess-fp14-1", "c-rx",
        decision=None, execution_status="retry_exhausted",
        created_at=base + timedelta(minutes=1),  # most recent
    )
    rows = await _fetch_recent_summaries(engine, "sess-fp14-1", n=3)
    assert len(rows) == 2
    assert rows[0].cycle_id == "c-rx"  # DESC: most recent first
    assert rows[0].execution_status == "retry_exhausted"
    assert rows[0].decision is None
```

### Step 5.4: Run new + rewritten tests — expect FAIL on assertion (filter still in place)

- [ ] **Step 5.4.1: Run the 3 changed tests**

Run: `uv run pytest tests/test_cycle_summary_injection.py::test_fetch_includes_all_cycles_regardless_of_status tests/test_cycle_summary_injection.py::test_fetch_includes_ok_cycles_with_null_decision tests/test_cycle_summary_injection.py::test_fetch_recent_summaries_includes_retry_exhausted -v`

Expected: all 3 FAIL — assertions fail because the filter still excludes the cycles they expect to be included. (The point: red gate before removing the filter.)

### Step 5.5: Remove WHERE filter + rewrite docstring

- [ ] **Step 5.5.1: Edit `src/cli/app.py:177-235` — replace docstring + WHERE clause**

Change:

```python
async def _fetch_recent_summaries(
    engine, session_id: str, n: int = 3,
) -> list[CycleSummary]:
    """Fetch the N most recent ok cycles (with non-NULL decision) for a session.

    Filters:
      - session_id matches (D-U1-a: session-bound, no cross-session leak)
      - execution_status='ok' (forensic cycles have decision=NULL anyway, but
        explicit filter makes intent clear)
      - decision IS NOT NULL (review F2 defensive: physically eliminate any
        future code path that lands ok+NULL into the injection list)

    Returns [] on:
      - First cycle in session (no prior rows)
      - Forensic-only history (all cycles non-ok)
      - DB error (any exception logged at WARNING + empty list — D-U4-a
        fail-isolated; cycle must continue)

    Ordering: created_at DESC, id DESC (review F4 tie-breaker for stability).
    Caller (`_render_recent_summaries`) re-sorts ASC for chronological reading.
    """
    try:
        async with get_session(engine) as session:
            result = await session.execute(
                select(
                    AgentCycle.id,
                    AgentCycle.cycle_id,
                    AgentCycle.triggered_by,
                    AgentCycle.decision,
                    AgentCycle.execution_status,
                    AgentCycle.created_at,
                )
                .where(
                    AgentCycle.session_id == session_id,
                    AgentCycle.execution_status == "ok",
                    AgentCycle.decision.is_not(None),
                )
                .order_by(
                    AgentCycle.created_at.desc(),
                    AgentCycle.id.desc(),
                )
                .limit(n)
            )
```

To:

```python
async def _fetch_recent_summaries(
    engine, session_id: str, n: int = 3,
) -> list[CycleSummary]:
    """Fetch the N most recent cycles for a session (all execution statuses).

    F-P14 (R2-Next-B): no execution_status / decision filter — render layer
    handles three-state branching (ok+valid / ok+NULL / forensic) via
    `_render_empty_decision_body`. Filter removal is intentional: priors
    must reflect actual cycle state including retry_exhausted /
    usage_limit_exceeded so the next cycle sees forensic ⚠️ hints.

    Filters:
      - session_id matches (D-U1-a: session-bound, no cross-session leak)

    Returns [] on:
      - First cycle in session (no prior rows)
      - DB error (any exception logged at WARNING + empty list — D-U4-a
        fail-isolated; cycle must continue)

    Ordering: created_at DESC, id DESC (review F4 tie-breaker for stability).
    Caller (`_render_recent_summaries`) re-sorts ASC for chronological reading
    and dispatches per-row to the empty-body branch when decision is NULL.
    """
    try:
        async with get_session(engine) as session:
            result = await session.execute(
                select(
                    AgentCycle.id,
                    AgentCycle.cycle_id,
                    AgentCycle.triggered_by,
                    AgentCycle.decision,
                    AgentCycle.execution_status,
                    AgentCycle.created_at,
                )
                .where(
                    AgentCycle.session_id == session_id,
                )
                .order_by(
                    AgentCycle.created_at.desc(),
                    AgentCycle.id.desc(),
                )
                .limit(n)
            )
```

Only change: WHERE drops the two `execution_status == "ok"` and `decision.is_not(None)` clauses; keep the `session_id == session_id` line. Docstring rewritten per spec §3.2.2.

### Step 5.6: Run modified tests — expect PASS

- [ ] **Step 5.6.1: Run rewritten / new tests**

Run: `uv run pytest tests/test_cycle_summary_injection.py::test_fetch_includes_all_cycles_regardless_of_status tests/test_cycle_summary_injection.py::test_fetch_includes_ok_cycles_with_null_decision tests/test_cycle_summary_injection.py::test_fetch_recent_summaries_includes_retry_exhausted -v`

Expected: all 3 PASS.

### Step 5.7: Run all of test_cycle_summary_injection.py — confirm no break

- [ ] **Step 5.7.1: Run full file**

Run: `uv run pytest tests/test_cycle_summary_injection.py -v`

Expected: all PASS. **However**, if `_render_recent_summaries` is invoked via `_build_recent_summaries_block` end-to-end with a NULL-decision summary, it will currently call `_truncate_decision(None)` and crash. None of the existing tests exercise this path with NULL decision (they all set `decision="..."`), but Task 6 will add the tri-state branch in render. If any test fails here that wasn't expected, **STOP** and reconcile.

### Step 5.8: Commit Task 5

- [ ] **Step 5.8.1: Stage and commit**

```bash
git add src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-b): F-P14 priors filter delete (T-FP14.1, AC-4)

_fetch_recent_summaries no longer filters execution_status='ok' AND
decision IS NOT NULL — retry_exhausted / usage_limit_exceeded / ok+NULL
cycles enter the priors list. Render-layer dispatch (Task 6) handles
the three-state branching.

Rewrites 2 existing tests (semantic inversion — exclude → include) +
adds T-FP14.1 retry_exhausted inclusion assertion. Filter removal
fixes sim #8 cycle 1aa0d4e5 forensic blindspot: agent could not see
the retry_exhausted predecessor in priors → may误judge protection state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: F-P14 step d — `_render_recent_summaries` tri-state branch

**Files:**
- Modify: `src/cli/app.py:238-268` — branch on `not s.decision`, NULL → empty body, header omits `· N words`
- Test: `tests/test_cycle_summary_injection.py` — append T-FP14.3 + T-FP14.8

**Spec refs:** §3.2.4, AC-6/11, T-FP14.3/8.

### Step 6.1: Write failing tests T-FP14.3 + T-FP14.8

- [ ] **Step 6.1.1: Append T-FP14.3 + T-FP14.8 at end of `tests/test_cycle_summary_injection.py` (after T-FP14.1 from Task 5)**

```python


def test_render_recent_summaries_ok_cycle_unchanged():
    """T-FP14.3 (AC-6, F-P14 regression): ok cycle with valid decision
    renders original 5-field header (· N words) + truncated body. R2-Next-A
    D2 word count header preserved on the non-NULL branch."""
    from datetime import datetime, timezone, timedelta
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary(
        "abc12345", "scheduled", "Some decision body.",
        now - timedelta(minutes=5), execution_status="ok",
    )
    output = _render_recent_summaries([s], now)
    assert "· 3 words]" in output  # R2-Next-A D2 word count header maintained
    assert "Some decision body." in output


def test_render_recent_summaries_null_decision_header_no_word_count():
    """T-FP14.8 (AC-11, F-P14): NULL decision row's per-prior header
    SHORTENS — no `· N words` segment. Visual signal that this row
    differs from agent-authored priors."""
    from datetime import datetime, timezone, timedelta
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary(
        "abc12345", "conditional", None,
        now - timedelta(minutes=5),
        execution_status="retry_exhausted",
    )
    output = _render_recent_summaries([s], now)
    # Find the per-prior header line (skip top-level header + blank line)
    # output structure:
    #   line 0: "Your prior cycle summaries (most recent N=3, from this session):"
    #   line 1: ""
    #   line 2: "[cycle abc12345 · conditional · 2026-... (5 min ago)]"
    #   line 3: "⚠️ The previous cycle ..."
    lines = output.split("\n")
    header_line = lines[2]
    # NULL decision row: header MUST NOT contain `words]` or `· N words`
    assert "words]" not in header_line, \
        f"NULL-decision header should omit word count, got: {header_line!r}"
    # Sanity: header still has the cycle prefix
    assert header_line.startswith("[cycle abc12345 · conditional · ")
    # Sanity: body contains the system-generated forensic hint
    assert "⚠️" in output
    assert "did not complete normally" in output
```

- [ ] **Step 6.1.2: Run tests — expect FAIL**

Run: `uv run pytest tests/test_cycle_summary_injection.py::test_render_recent_summaries_ok_cycle_unchanged tests/test_cycle_summary_injection.py::test_render_recent_summaries_null_decision_header_no_word_count -v`

Expected:
- `test_render_recent_summaries_ok_cycle_unchanged`: PASS (existing render path covers this).
- `test_render_recent_summaries_null_decision_header_no_word_count`: **FAIL** — current render unconditionally appends `· N words]` to header AND will TypeError on `_truncate_decision(None)` since `_WORD_RE.finditer(None)` fails.

### Step 6.2: Implement tri-state render branch

- [ ] **Step 6.2.1: Edit `src/cli/app.py:238-268`**

Change:

```python
def _render_recent_summaries(
    summaries: list[CycleSummary], now: datetime,
) -> str:
    """Render summaries as a user-message-ready prefix block.

    Returns "" if list is empty (caller skips header append on first cycle).
    Sorts by (created_at, id) ASC so the reader sees oldest → newest naturally
    (review F4: id tie-breaker keeps same-timestamp ordering stable).

    R2-Next-A D2: each per-prior header includes `· {N} words` showing the
    ORIGINAL word count (pre-truncation). Pairs with D1 marker and A3
    persona text — agent compares header N vs the 700-word cap to detect
    over-budget priors and self-titrate.
    """
    if not summaries:
        return ""

    blocks = []
    for s in sorted(summaries, key=lambda x: (x.created_at, x.id)):
        cycle_id_short = s.cycle_id[:8]
        utc_str = s.created_at.strftime("%Y-%m-%d %H:%M UTC")
        ago = _format_relative_time(now, s.created_at)
        word_count = _count_words(s.decision or "")  # R2-Next-A D2
        body = _truncate_decision(s.decision)
        blocks.append(
            f"[cycle {cycle_id_short} · {s.triggered_by} · {utc_str} "
            f"({ago}) · {word_count} words]\n{body}"
        )

    header = "Your prior cycle summaries (most recent N=3, from this session):"
    return f"{header}\n\n" + "\n\n".join(blocks)
```

To:

```python
def _render_recent_summaries(
    summaries: list[CycleSummary], now: datetime,
) -> str:
    """Render summaries as a user-message-ready prefix block.

    Returns "" if list is empty (caller skips header append on first cycle).
    Sorts by (created_at, id) ASC so the reader sees oldest → newest naturally
    (review F4: id tie-breaker keeps same-timestamp ordering stable).

    Tri-state per-prior rendering (F-P14):
      - decision non-NULL (ok cycle, agent-authored): R2-Next-A D2 header
        includes `· {N} words` with ORIGINAL word count (pre-truncation);
        body is `_truncate_decision(decision)`.
      - decision NULL (forensic / ok+empty): header SHORTENS (no word count
        segment); body is system-generated via `_render_empty_decision_body`
        keyed on `execution_status`. Length-budget accounting tracks
        agent-authored content only — system bodies are not counted.
    """
    if not summaries:
        return ""

    blocks = []
    for s in sorted(summaries, key=lambda x: (x.created_at, x.id)):
        cycle_id_short = s.cycle_id[:8]
        utc_str = s.created_at.strftime("%Y-%m-%d %H:%M UTC")
        ago = _format_relative_time(now, s.created_at)

        if not s.decision:
            # F-P14 tri-state: NULL decision → shortened header + system body
            header = (
                f"[cycle {cycle_id_short} · {s.triggered_by} · "
                f"{utc_str} ({ago})]"
            )
            body = _render_empty_decision_body(s.execution_status)
        else:
            # R2-Next-A D2: ok cycle with valid decision — original 5-field header
            word_count = _count_words(s.decision)
            header = (
                f"[cycle {cycle_id_short} · {s.triggered_by} · "
                f"{utc_str} ({ago}) · {word_count} words]"
            )
            body = _truncate_decision(s.decision)

        blocks.append(f"{header}\n{body}")

    header_top = "Your prior cycle summaries (most recent N=3, from this session):"
    return f"{header_top}\n\n" + "\n\n".join(blocks)
```

Notes:
- Renamed local variable `header` → `header_top` for the outer block to avoid clash with per-prior `header` in loop body (clearer than the original — defensible refactor).
- `_count_words(s.decision or "")` simplified to `_count_words(s.decision)` because the if-branch above guarantees `s.decision` is truthy here.

### Step 6.3: Run T-FP14.3 + T-FP14.8 — expect PASS

- [ ] **Step 6.3.1: Run new tests**

Run: `uv run pytest tests/test_cycle_summary_injection.py::test_render_recent_summaries_ok_cycle_unchanged tests/test_cycle_summary_injection.py::test_render_recent_summaries_null_decision_header_no_word_count -v`

Expected: both PASS.

### Step 6.4: Run full test_cycle_summary_injection.py — confirm no regressions

- [ ] **Step 6.4.1: Run full file**

Run: `uv run pytest tests/test_cycle_summary_injection.py -v`

Expected: all PASS.

### Step 6.5: Commit Task 6

- [ ] **Step 6.5.1: Stage and commit**

```bash
git add src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-b): F-P14 _render_recent_summaries tri-state (T-FP14.3/8)

Per-prior render branches on decision:
- non-NULL (ok+authored): unchanged 5-field header `· N words` + truncated body
- NULL (forensic / ok+empty): shortened header (no word count) + system body
  via _render_empty_decision_body keyed on execution_status

R2-Next-A D2 word-count header preserved on the agent-authored branch.
Length budget accounting tracks agent decision length only — system
bodies are NOT counted (header shortened to omit `· N words`).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: F-P14 step e — T-FP14.9 retry_exhausted reasoning=None drift guard

> **TDD note:** This task is a **post-condition assertion**, NOT a red→green TDD step. The implementation already guarantees the assertion (`reasoning=None` is hardcoded in `run_agent_cycle` retry-exhausted branch). The test exists to catch FUTURE drift if someone later adds `reasoning=<derived summary>` to the write path. Step 7.1.2 expects PASS on first run — that is by design.

**Files:**
- Test: `tests/test_cycle_summary_injection.py` — append T-FP14.9

**Spec refs:** AC-12, T-FP14.9, §3.2.6 (write path NOT changed). T-FP14.9 enforces that the retry-exhausted branch in `run_agent_cycle` keeps `reasoning=None`. This is a marginal regression guard — the assignment is hardcoded already; the test catches future drift. (Source line numbers will shift after Task 3 inserts `_render_empty_decision_body` ~25 lines above; the test pinpoints the write path by function name + execution_status='retry_exhausted', not by line number.)

### Step 7.1: Write T-FP14.9

The retry-exhausted write path is the third-attempt `except` branch in `run_agent_cycle`. Trigger it via mocking `agent.run` to raise `RuntimeError` 3 times, then read back the AgentCycle row and assert `reasoning is None`.

The mock chain must cover **everything `run_agent_cycle` touches BEFORE entering the retry loop** — not just the retry path itself. Specifically: `_capture_trigger_context` (sync function), `_capture_state_snapshot` (async), `_build_recent_summaries_block` (runs real SQL but returns "" on empty session — no extra patch needed), and `deps.memory.format_for_prompt()` (async). Also patch `asyncio.sleep` so 3 retries don't take ~7 seconds.

**Critical mock-chain pitfalls** (caught in plan review):
- `_capture_trigger_context` is `def` (sync), NOT `async def` — must use `mocker.Mock`, NOT `AsyncMock`. `AsyncMock` on a sync call site returns a coroutine that the production code then tries to JSON-serialize → `TypeError`.
- `deps.memory.format_for_prompt` IS awaited at `run_agent_cycle` line 436 (BEFORE retry loop), so a bare `mocker.Mock()` `deps.memory` would TypeError on `await`. Must explicitly assign `AsyncMock`.
- `deps.symbol` / `deps.timeframe` are read in the prompt-build f-string (line 401-402); auto-attr Mock works (no crash) but produces noisy `<Mock id=...>` strings. Set explicit values for cleanliness.

- [ ] **Step 7.1.1: Append T-FP14.9 at end of `tests/test_cycle_summary_injection.py` (after T-FP14.8 from Task 6)**

```python


async def test_retry_exhausted_writes_null_reasoning_unchanged(monkeypatch, mocker):
    """T-FP14.9 (AC-12, F-P14 drift guard): retry_exhausted write path
    must keep `reasoning=None`. Single-responsibility regression guard:
    agent_cycles.reasoning is reserved for agent-authored thinking
    content; system never injects derivative summaries (e.g., a trade_actions
    rollup) into this column. Anchored on function `run_agent_cycle` retry-
    exhausted branch (write coordinates: execution_status='retry_exhausted'
    + decision=None + reasoning=None) — not on a fixed source line number.

    Mocks `agent.run` to raise RuntimeError 3 times → triggers the
    retry-exhausted branch → DB writes AgentCycle(reasoning=None,
    decision=None, execution_status='retry_exhausted'). Test reads
    back the row and asserts reasoning IS None.
    """
    from unittest.mock import AsyncMock
    from sqlalchemy import select
    from src.cli.app import run_agent_cycle, TokenBudget
    from src.storage.database import get_session
    from src.storage.models import AgentCycle

    # Patch asyncio.sleep to no-op so 3 retries don't take ~7 seconds
    monkeypatch.setattr("src.cli.app.asyncio.sleep", AsyncMock(return_value=None))

    engine = await _make_engine_with_session("sess-fp14-9")

    # Mock agent — agent.run raises RuntimeError every attempt
    agent = mocker.Mock()
    agent.run = AsyncMock(side_effect=RuntimeError("synthetic LLM failure"))

    # Mock deps — must cover everything read BEFORE retry loop, not just
    # the retry-exhausted DB write. Prompt build (cli/app.py:399-438) reads
    # deps.symbol, deps.timeframe; deps.memory.format_for_prompt() is awaited.
    deps = mocker.Mock()
    deps.session_id = "sess-fp14-9"
    deps.symbol = "BTC/USDT:USDT"
    deps.timeframe = "5m"
    deps.memory = mocker.Mock()
    deps.memory.format_for_prompt = AsyncMock(return_value="No relevant memories.")

    budget = TokenBudget(daily_max=1_000_000)

    # Patch capture helpers. Note: _capture_trigger_context is SYNC (def, not
    # async def — see src/services/cycle_capture.py:24); call site cli/app.py:393
    # has no await. AsyncMock here would yield a coroutine assigned to
    # trigger_context_var, then `json.dumps(coroutine)` at the retry-exhausted
    # write would TypeError. _capture_state_snapshot IS async (line 394 awaits).
    monkeypatch.setattr(
        "src.cli.app._capture_state_snapshot",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "src.cli.app._capture_trigger_context",
        mocker.Mock(return_value=None),  # sync — must NOT be AsyncMock
    )

    # _build_recent_summaries_block runs real SQL but the empty sess-fp14-9
    # session produces [] → returns "" → no extra patch needed.

    # Run the cycle — should hit retry_exhausted branch (3 RuntimeError → DB write)
    result = await run_agent_cycle(
        agent, deps, "scheduled", budget, engine,
        context=None, model=None, console=None, stats=None,
    )
    assert result is None  # retry_exhausted returns None

    # Read back the AgentCycle row
    async with get_session(engine) as db:
        rows = (await db.execute(
            select(AgentCycle).where(AgentCycle.session_id == "sess-fp14-9")
        )).scalars().all()
    assert len(rows) == 1, "expected exactly one retry-exhausted forensic row"
    row = rows[0]
    assert row.execution_status == "retry_exhausted"
    assert row.decision is None
    # The drift guard assertion — write-path single responsibility:
    assert row.reasoning is None, \
        "retry_exhausted write path must keep reasoning=None " \
        "(do NOT inject trade_actions summaries — write-path single responsibility)"
```

- [ ] **Step 7.1.2: Run T-FP14.9 — expect PASS on first run**

This is a post-condition assertion, not a red→green TDD step (see Task 7 header note). The implementation already satisfies the assertion; the test exists to catch future drift.

Run: `uv run pytest tests/test_cycle_summary_injection.py::test_retry_exhausted_writes_null_reasoning_unchanged -v`

Expected: PASS. If it FAILs, the most likely causes are:
1. A sibling change replaced `reasoning=None` in the retry-exhausted branch — that's exactly the drift the test catches; investigate and revert if unintended.
2. Mock chain mismatch (e.g., AsyncMock on the sync helper, missing `deps.memory.format_for_prompt` AsyncMock) — re-check the pitfalls listed before Step 7.1.1.

**Caveat note:** The test patches `_capture_state_snapshot` / `_capture_trigger_context` and the `deps.memory` chain to avoid pulling unrelated machinery. If `run_agent_cycle` semantics change in the future (signature change, prompt build moves, capture moves before-after-retry, etc.), this test may need adjustment — but that adjustment is itself the drift signal the test is meant to surface.

### Step 7.2: Commit Task 7

- [ ] **Step 7.2.1: Stage and commit**

```bash
git add tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-next-b): F-P14 reasoning=None drift guard (T-FP14.9, AC-12)

Marginal regression guard for retry_exhausted write path
(cli/app.py:508 reasoning=None hardcoded). Defends single-responsibility
of agent_cycles.reasoning — reserved for agent-authored thinking
content, never derivative system summaries.

Mocks agent.run RuntimeError × 3 → DB write read-back asserts
reasoning IS None.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Final smoke + AC self-check

### Step 8.1: Full test suite

- [ ] **Step 8.1.1: Run full pytest suite**

Run: `uv run pytest -q 2>&1 | tail -10`

Expected: `1242 passed, 3 skipped` (AC-13). Compare against baseline 1230+3 → +12 net.

If a test fails, **STOP** and inspect; do not proceed to commit until green.

### Step 8.2: Schema / migration drift check

- [ ] **Step 8.2.1: Confirm no schema or migration files were touched (AC-14)**

```bash
git diff main --stat -- alembic/ src/storage/models.py
```

Expected: empty output. If anything appears, **STOP** and revert — F-P14 must not introduce schema changes.

### Step 8.3: AC verification matrix

Walk through each AC manually against the diff and tests:

- [ ] **Step 8.3.1: Print spec ACs and verify**

| AC | Test | Verification |
|---|---|---|
| AC-1 | T-FP13.2 | `pytest -k test_place_limit_order_return_includes_async_note` |
| AC-2 | T-FP13.1 | `pytest -k test_place_limit_order_return_format_unchanged` |
| AC-3 | T-FP13.3 | `pytest -k test_place_limit_order_return_no_decision_label` |
| AC-4 | T-FP14.1 | `pytest -k test_fetch_recent_summaries_includes_retry_exhausted` |
| AC-5 | T-FP14.2 | `pytest -k test_cycle_summary_execution_status_populated` |
| AC-6 | T-FP14.3 | `pytest -k test_render_recent_summaries_ok_cycle_unchanged` |
| AC-7 | T-FP14.4 | `pytest -k test_render_empty_decision_body_ok` |
| AC-8 | T-FP14.5 | `pytest -k test_render_empty_decision_body_retry_exhausted` |
| AC-9 | T-FP14.6 | `pytest -k test_render_empty_decision_body_usage_limit_exceeded` |
| AC-10 | T-FP14.7 | `pytest -k test_render_empty_decision_body_unknown_fallback` |
| AC-11 | T-FP14.8 | `pytest -k test_render_recent_summaries_null_decision_header_no_word_count` |
| AC-12 | T-FP14.9 | `pytest -k test_retry_exhausted_writes_null_reasoning_unchanged` |
| AC-13 | full suite | `pytest -q 2>&1 \| tail -3` shows 1242 + 3 skip |
| AC-14 | git diff | `git diff main -- alembic/ src/storage/models.py` empty |
| AC-15 | manual smoke | DEFER to W3 sim (per spec §4 AC-15: 自然 retry_exhausted/usage_limit_exceeded 触发时手动 inspect cycle log) |

Run them as one batch:

```bash
uv run pytest \
  tests/test_fact_only_wordlist.py::test_place_limit_order_return_includes_async_note \
  tests/test_fact_only_wordlist.py::test_place_limit_order_return_format_unchanged \
  tests/test_fact_only_wordlist.py::test_place_limit_order_return_no_decision_label \
  tests/test_cycle_summary_injection.py::test_fetch_recent_summaries_includes_retry_exhausted \
  tests/test_cycle_summary_injection.py::test_cycle_summary_execution_status_populated \
  tests/test_cycle_summary_injection.py::test_render_recent_summaries_ok_cycle_unchanged \
  tests/test_cycle_summary_injection.py::test_render_empty_decision_body_ok \
  tests/test_cycle_summary_injection.py::test_render_empty_decision_body_retry_exhausted \
  tests/test_cycle_summary_injection.py::test_render_empty_decision_body_usage_limit_exceeded \
  tests/test_cycle_summary_injection.py::test_render_empty_decision_body_unknown_fallback \
  tests/test_cycle_summary_injection.py::test_render_recent_summaries_null_decision_header_no_word_count \
  tests/test_cycle_summary_injection.py::test_retry_exhausted_writes_null_reasoning_unchanged \
  -v
```

Expected: 12 passed.

### Step 8.4: Git log review

- [ ] **Step 8.4.1: Inspect commit ladder**

```bash
git log --oneline main..HEAD
```

Expected: 7 commits in this order:
1. `docs(iter-w2r2-next-b): decision forensic edge cases impl plan` (Task 1)
2. `feat(iter-w2r2-next-b): F-P13 place_limit_order async note (D2+D3)` (Task 2)
3. `feat(iter-w2r2-next-b): F-P14 _render_empty_decision_body helper (D9/D10)` (Task 3)
4. `feat(iter-w2r2-next-b): F-P14 CycleSummary execution_status field (T-FP14.2)` (Task 4)
5. `feat(iter-w2r2-next-b): F-P14 priors filter delete (T-FP14.1, AC-4)` (Task 5)
6. `feat(iter-w2r2-next-b): F-P14 _render_recent_summaries tri-state (T-FP14.3/8)` (Task 6)
7. `test(iter-w2r2-next-b): F-P14 reasoning=None drift guard (T-FP14.9, AC-12)` (Task 7)

Plus the spec doc commit `a0efc4f` (already landed before this plan).

### Step 8.5: Trigger code review

- [ ] **Step 8.5.1: Per memory `feedback_review_before_commit` — present results to user before opening PR. User decides next: `/ultrareview`, `/code-review`, or direct merge.**

Per memory `feedback_no_pr_comment` — do NOT post review output to GitHub via `gh pr comment`. Report findings in the conversation only.

---

## Self-Review

> Performed at plan-write time. If issues found, fixed inline.

### 1. Spec coverage

Walked through `2026-05-08-iter-w2r2-next-b-decision-forensic-edge-cases-design.md`:

| Spec section | Plan task | Status |
|---|---|---|
| §3.1 F-P13 design (multi-line return) | Task 2 | ✅ Step 2.3 |
| §3.1.5 F-P13 W3 trigger (out of scope) | (none — OOS) | ✅ |
| §3.2.2 _fetch_recent_summaries delete filter + docstring | Task 5 | ✅ Step 5.5 |
| §3.2.3 CycleSummary dataclass + str→str\|None | Task 4 | ✅ Step 4.3 |
| §3.2.4 _render_recent_summaries tri-state branch + docstring | Task 6 | ✅ Step 6.2 |
| §3.2.5 _render_empty_decision_body | Task 3 | ✅ Step 3.2 |
| §3.2.6 不动的范围 (write path / sort / position / D2 / helpers) | (verification) | ✅ Tasks 4-6 leave write path / sort / D2 unchanged |
| §3.3 端到端示例 (sim #8 narrative) | (no explicit task; covered by T-FP14.3/8 + smoke) | ✅ |
| §4 AC-1..15 | Task 8.3 matrix | ✅ |
| §5.2 T-FP13.1/2/3 + happy invoker helper | Task 2 (Step 2.1+2.2) | ✅ |
| §5.3 T-FP14.1..9 + _make_summary factory ext | Tasks 3-7 | ✅ |
| §5.4.1 test_fetch_excludes_forensic_cycles rewrite | Task 5 (Step 5.1) | ✅ |
| §5.4.2 test_fetch_excludes_cycles_with_null_decision rewrite | Task 5 (Step 5.2) | ✅ |
| §5.4.3 _make_summary execution_status param | Task 4 (Step 4.1) | ✅ |
| §5.4.4 test_summarize_place_limit_order multi-line robustness | Task 2 (Step 2.5) | ✅ |
| §5.5 测试规模预估 (+12 net) | Task 8.1 expected count 1242 | ✅ |
| §6 File Changes | File Structure section + Tasks 2-7 | ✅ |
| §7 OOS | (no tasks needed — exclusion list) | ✅ acknowledged |
| §8 Risks R1-R7 | (no in-iter mitigation; R1 = future logger.warning candidate) | ✅ |
| §9 Memory References | (read at plan start; not in tasks) | ✅ |
| §10 Brainstorm decisions D1-D10 + D9.a/b | embedded in commit messages and docstrings | ✅ |

No gaps. Each AC has a corresponding test task; each design point has a code task; each unchanged-but-verified point has a verification step.

### 2. Placeholder scan

Scanned for: TBD / TODO / "fill in" / "implement later" / "add appropriate" / "similar to" / "Write tests for the above".

- ✅ No TBDs.
- ✅ No "TODO" (the only `TODO` comments in the codebase are not introduced by this plan).
- ✅ No "similar to Task N" — every step has its own complete code.
- ✅ Every test step shows complete test code; every implementation step shows complete diff.
- ✅ T-FP14.9 was flagged in spec §5.3 as "plan 阶段细化" — I expanded it in Step 7.1.1 with full mock chain + monkeypatch list + DB query. No vague reference left.

### 3. Type / identifier consistency

- `_render_empty_decision_body` (Task 3) — same name in Task 6 dispatch.
- `CycleSummary.execution_status` (Task 4) — same field name in Tasks 5/6.
- `CycleSummary.decision: str | None` (Task 4) — consistent with Task 6 NULL check `if not s.decision`.
- `_fetch_recent_summaries` (Tasks 4-5) — same SELECT additions in both tasks; Task 4 keeps filter, Task 5 removes it.
- Test names: T-FP13.1/2/3, T-FP14.1..9 — consistent across Tasks 2-7 and AC table.
- Test factory signature: `_make_summary(cycle_id, triggered_by, decision, created_at, sid=1, execution_status="ok")` — same shape used by all subsequent tests.
- Function signatures unchanged: `_render_recent_summaries(summaries, now)`, `_fetch_recent_summaries(engine, session_id, n)`.
- Header local-var rename `header → header_top` (Step 6.2) — applies only inside `_render_recent_summaries` body; no external impact.

No drift detected.

### 4. Pre-commit/CI hook awareness

This repo has no pre-commit hooks that ratchet test counts, but per spec §5.5 the +12 number is enumerated. If a `requesting-code-review` step pulls a count drift assertion from a sibling change, reconcile then.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-08-iter-w2r2-next-b-decision-forensic-edge-cases.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Each task gets implementer + spec-reviewer + code-reviewer pass per `superpowers:subagent-driven-development`. Aligns with the R2-7 / R2-8a / R2-8b / R2-Next-A precedent (per memory `project_w2_prep_progress`).

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans` with checkpoints for review.

**Which approach?**
