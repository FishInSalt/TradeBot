# Iter tool-opt-alert-family-rename — `set_price_alert` → `set_price_volatility_alert` + sectioning

**Date**: 2026-05-13
**Iteration**: iter-tool-opt-alert-family-rename (Sprint 4, iter-10 of tool-optimization roadmap)
**Type**: Design spec (破坏性 rename)
**Source brainstorm**: 2026-05-13 session covering Sprint 4 alert family scope; iter-11 (alert-age) split to a follow-on session
**Upstream**: `.working/tool-optimization/02-execution-roadmap.md` §2 iter-10 + `.working/tool-optimization/99-backlog.md` INV-11 / SP-1 / AA-2
**Related principles**: `docs/superpowers/principles/tool-design-principles.md` — 1 (fact-only) / 4 (tool count is selection latency) / 5 (closure pattern) / 8 (trust agent + tool surface)

---

## 0. One-minute summary

sim #8 (178 cycles / 1818 tool calls) shows the alert family suffers from a name-collision-induced selection bias:

- `set_price_alert` (volatility threshold + window) — **4 calls**
- `add_price_level_alert` (one-shot level trigger) — **136 calls**
- Ratio **1:34**

The two tools share the `price_alert` prefix, but their semantics differ: the first tunes a *volatility-sensitivity* parameter (% change in N minutes), the second registers an *individual price-level* one-shot trigger. The shared prefix forces the agent to disambiguate via docstring at every selection, biasing toward the more-frequently-used `add_price_level_alert` even when volatility tuning is the actual intent.

This spec hard-renames `set_price_alert` → `set_price_volatility_alert` and aligns `get_active_alerts` section headers (`Price Volatility Alert` / `Price Level Alerts`) so the family becomes name-disambiguated end-to-end. As a same-PR follow-on, it also completes the `iter-tool-opt-as-of-header` (commit b31ffc3) sweep — the second section of `get_active_alerts` was the only remaining one without the inline `(@ HH:MM:SS UTC)` timestamp. No alias is kept. `REGISTERED_TOOL_NAMES` count stays at 34.

The change is scoped to **name + section-header text + name-induced fact-alignment in adjacent impl docstrings/comments**. Schema, parameters, behavior, biz_error keys, and Layer-1 persona are untouched (principle 8 — name and docstring are the levers, not prompt nudges).

---

## 1. Empirical foundations

### 1.1 Source data

- sim #8: 178 cycles / 19.2h / 1818 tool calls (DB `data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`)
- Tool-optimization inventory: `.working/tool-optimization/01-inventory.md` INV-11
- Backlog: `.working/tool-optimization/99-backlog.md` §3.4 SP-1 + §3.4 AA-2 + §5.1 INV-11

### 1.2 Per-issue datum

| Issue | Datum | Source |
|---|---|---|
| Name-collision call ratio | `set_price_alert` 4 / `add_price_level_alert` 136 = **1:34** in sim #8 | sim #8 tool_calls aggregation |
| Section-header redundancy | `=== Price Alert Settings ===` immediately followed by `Volatility alert: ...` line — doubles "alert" word in 3 lines | `tools_perception.py:574,576,581` |
| Family-prefix collision count | 4 tools (`set_price_alert`, `add_price_level_alert`, `cancel_price_level_alert`, `update_price_level_alert`) + 2 `get_active_alerts` output section labels ("Price Alert Settings", "Active Price Level Alerts") all carry the `price_alert` / `price level alert` collocation | `REGISTERED_TOOL_NAMES` + `tools_perception.py:574-587` |

### 1.3 Implication

The fix is name-level (principle 4: tool count is selection latency — but name disambiguation in a 34-tool list is the dominant lever for low-frequency tools). Hard rename has clean semantics; an alias would dilute the ergonomic gain by leaving two names in the selection pool (both names pay the principle-4 cost; only one carries forward).

---

## 2. Architecture and scope

### 2.1 Issue → change matrix

| Issue ID | Surface | Change |
|---|---|---|
| INV-11 / SP-1 | Tool name | `set_price_alert` → `set_price_volatility_alert` (hard rename, no alias) |
| INV-11 / AA-2 | `get_active_alerts` section headers | `=== Price Alert Settings ===` → `=== Price Volatility Alert ===`; `=== Active Price Level Alerts (N/20) ===` → `=== Price Level Alerts (N/20) ===` |
| AA-2 follow-on | Volatility section body line | Drop redundant `Volatility alert: ` prefix; header already anchors the section. Body becomes `{threshold}% in {window}min window` or `OFF` |
| b31ffc3 sweep completion | `get_active_alerts` second section header | Add inline `(@ HH:MM:SS UTC)` timestamp to `=== Price Level Alerts (N/20) ===` (the only perception-tool section header still missing it after b31ffc3 covered 14 tools). Same-PR follow-on; reuses `fetch_ts` already generated at L567 |

### 2.2 Tool count invariant

`REGISTERED_TOOL_NAMES` stays at **34** entries (20 perception + 13 execution + 1 memory):

- 1 entry removed: `"set_price_alert"`
- 1 entry added: `"set_price_volatility_alert"`
- Position in the list preserved (slot-stable, drift-guard friendly)

### 2.3 Out of scope

- **iter-11 `alert-age` (AL-1 + AA-4)** — separate iter, separate spec, separate PR (per brainstorm decision 2026-05-13)
- **`add/cancel/update_price_level_alert`** — names not changed; these are already R2-Next-E governed
- **DB historical rows** — sim #8 has 4 rows with `action='set_price_alert'`; left in place per memory `project_r2_8b_legacy_decision_restore_boundary` (legacy enum / label rows are not query-path migrated)
- **Layer-1 persona** — untouched (principle 8: name + docstring are the levers)
- **biz_error keys** — `"invalid_threshold_range"` keeps its current spelling (forensic-side, not user-facing)
- **Tool schema / parameters / validation ranges** — unchanged

### 2.4 Principle reconciliation

- **Principle 1 (fact-only)**: old name `set_price_alert` is misleading — "price alert" is too generic for a tool that adjusts volatility-sensitivity parameters. New name is fact-disambiguating.
- **Principle 4 (tool count)**: net-zero (34 → 34). Hard rename does not inflate the selection pool.
- **Principle 5 (closure pattern)**: not the dominant principle here; the rename is a name-disambiguation, not a multi-call closure.
- **Principle 8 (trust agent + tool surface)**: no Layer-1 nudge added; the new name + the existing docstring is the lever.

---

## 3. Components affected

### 3.1 Tool rename surface (rename `set_price_alert` → `set_price_volatility_alert`)

| File | Anchor | Change |
|---|---|---|
| `src/agent/tools_execution.py` | L223 | `async def set_price_alert(` → `async def set_price_volatility_alert(` |
| `src/agent/tools_execution.py` | L229 | impl docstring `Adjust price alert parameters. ...` → `Adjust price volatility alert parameters. ...` |
| `src/agent/tools_execution.py` | L244 | `_record_action(action="set_price_alert", ...)` → `action="set_price_volatility_alert"` |
| `src/agent/tools_execution.py` | L248-251 | success message `Price alert updated: threshold=...` → `Price volatility alert updated: threshold=...` |
| `src/agent/trader.py` | L528 | `async def set_price_alert(` → `async def set_price_volatility_alert(` |
| `src/agent/trader.py` | L543 | `from src.agent.tools_execution import set_price_alert as _impl` → `set_price_volatility_alert as _impl` |
| `src/agent/trader.py` | L784 (REGISTERED_TOOL_NAMES) | `"set_price_alert"` → `"set_price_volatility_alert"` (position preserved) |
| `src/cli/display.py` | L231 | `def _summarize_set_price_alert(` → `def _summarize_set_price_volatility_alert(` |
| `src/cli/display.py` | L283 | dict key `"set_price_alert": _summarize_set_price_alert` → `"set_price_volatility_alert": _summarize_set_price_volatility_alert` |
| `src/cli/display.py` | L299 | dict key `"set_price_alert": "Price alert updated:"` → `"set_price_volatility_alert": "Price volatility alert updated:"` |
| `src/cli/display.py` | L530 | list entry `"set_price_alert"` → `"set_price_volatility_alert"` |
| `src/services/tool_call_recorder.py` | L58 | comment `# set_price_alert 阈值越界` → `# set_price_volatility_alert 阈值越界` (forensic comment kept aligned; biz_error key `"invalid_threshold_range"` itself unchanged per §2.3) |
| `src/agent/tools_perception.py` | L565 | impl docstring `"""Get current alert configuration: volatility alert params and active price level alerts."""` → `"""Get current alert configuration: price volatility alert params and price level alerts."""` (fact-alignment: rendered output drops "Active" — docstring must follow per principle 1; impl docstring is the agent's tool-selection fact card and drift here causes mental-model noise) |

The wrapper docstring at `trader.py:534` (`Adjust volatility alert sensitivity.`) is **intentionally left as-is**: per principle 8, the tool name `set_price_volatility_alert` already carries the family identity; the docstring describes the operation (adjust sensitivity attribute), and repeating "price volatility alert" three times in name+docstring would dilute information density. Surface asymmetry between name and docstring summary is accepted as a deliberate trade-off favoring docstring density over textual mirror.

### 3.2 Section header rename surface (`get_active_alerts`)

| File | Anchor | Change |
|---|---|---|
| `src/agent/tools_perception.py` | L574 | `f"=== Price Alert Settings (@ {fetch_ts} UTC) ===\nVolatility alert: {threshold}% in {window}min window"` → `f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\n{threshold}% in {window}min window"` |
| `src/agent/tools_perception.py` | L576 | `f"=== Price Alert Settings (@ {fetch_ts} UTC) ===\nVolatility alert: OFF"` → `f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\nOFF"` |
| `src/agent/tools_perception.py` | L581 | `f"=== Active Price Level Alerts ({count}/20) ==="` → `f"=== Price Level Alerts ({count}/20) (@ {fetch_ts} UTC) ==="`. The `fetch_ts` variable is already generated at L567 for the first section header; this completes the iter-tool-opt-as-of-header (b31ffc3) sweep of 14 perception tools — the second section of `get_active_alerts` was the only remaining one without the inline UTC timestamp |

The `Volatility alert:` line-prefix is dropped because the new section header already anchors the semantic; the body line carries only the fact (threshold + window or `OFF`).

**Contract clarification (no behavior change)**: `get_price_level_alerts()` returns only currently-active alerts by construction — `_check_price_levels()` pops triggered alerts from `_price_level_alerts` (`base.py:209-225`), and `remove_price_level_alert()` pops cancelled ones (`base.py:202-207`). The `Active` word in the old header was descriptive of this contract, not constitutive of it; removing the word does not weaken the guarantee.

### 3.3 Test files (rename in lockstep)

| File | Anchor | Change |
|---|---|---|
| `tests/test_tools.py` | L351-388 | 4 `test_set_price_alert_*` test fn names + imports → `test_set_price_volatility_alert_*` |
| `tests/test_trader_agent.py` | L199-210 | `test_set_price_alert_schema_exposes_threshold_range` → `test_set_price_volatility_alert_schema_exposes_threshold_range`; `tools["set_price_alert"]` → `tools["set_price_volatility_alert"]` |
| `tests/test_tool_call_recorder.py` | L274 | `make_call("set_price_alert")` → `make_call("set_price_volatility_alert")` |
| `tests/test_alert_lifecycle.py` | L671-685 | `test_set_price_alert_invalid_threshold_records_biz_error` → `test_set_price_volatility_alert_invalid_threshold_records_biz_error`; import + invoke updated |
| `tests/test_fact_only_wordlist.py` | L628, L701-705 | `_invoke_set_price_alert` helper → `_invoke_set_price_volatility_alert`; list entry updated |
| `tests/test_tool_enhancement.py` | L720-758 | 4 `test_set_price_alert_*` test fns (`_disabled` / `_enabled` / `_accepts_threshold_0_1` / `_rejects_threshold_below_0_1`) — rename fn names + imports + invokes; L748 assert `"Price alert updated"` → `"Price volatility alert updated"` |
| `tests/test_tool_enhancement.py` | L835, L840 | section-header assertions in `test_get_active_alerts_with_alerts`: regex `r"=== Price Alert Settings \(@ ..."` → `r"=== Price Volatility Alert \(@ ..."`; substring `"=== Active Price Level Alerts"` → `"=== Price Level Alerts"` |
| `tests/test_display_cycle.py` | L151-163 | `test_summarize_get_active_alerts` fixture: `=== Price Alert Settings ===` → `=== Price Volatility Alert ===`; drop `Volatility alert: ` line-prefix; `=== Active Price Level Alerts ===` → `=== Price Level Alerts ===` |
| `tests/test_display_cycle.py` | L307-310 | `test_summarize_set_price_alert` → `test_summarize_set_price_volatility_alert`; `summarize_tool("set_price_alert", ...)` → `summarize_tool("set_price_volatility_alert", ...)` |
| `tests/test_display_cycle.py` | L2229-2248 | `test_snapshot_get_active_alerts_with_alerts` — both the input `content` fixture (L2232-2235) AND the rendered `expected` block (L2241-2244) need section-header rename + line-prefix drop. Two passes through the same swap |
| `tests/test_display_cycle.py` | L3089 (`_CRITICAL_FIELDS_PATH_A`) | `"get_active_alerts": ["Price Alert Settings", "Volatility alert"]` → `"get_active_alerts": ["Price Volatility Alert", "OFF"]`. Note: PATH_A invokes `get_active_alerts` against `_mock_exchange_minimal()` which yields the disabled-state branch (`get_alert_params()` → None → render `OFF`), so both new anchors must appear in that branch's output |

### 3.4 New drift-guard tests

Add to `tests/test_tool_enhancement.py` (co-located with the other `test_get_active_alerts_*` cases at L720+ / L820+ region):

```python
def test_set_price_volatility_alert_in_registered_tool_names():
    """Drift guard (iter-10): set_price_volatility_alert renamed from set_price_alert.
    Hard rename — old name must be absent."""
    from src.agent.trader import REGISTERED_TOOL_NAMES
    assert "set_price_volatility_alert" in REGISTERED_TOOL_NAMES
    assert "set_price_alert" not in REGISTERED_TOOL_NAMES


async def test_get_active_alerts_section_headers_renamed():
    """Drift guard (iter-10): section headers renamed from Price Alert Settings /
    Active Price Level Alerts to Price Volatility Alert / Price Level Alerts."""
    # Setup: enable alerts + add 1 level alert via deps.exchange
    ...
    output = await get_active_alerts(deps)
    assert "=== Price Volatility Alert (@" in output
    assert "=== Price Level Alerts (1/20) (@" in output
    assert "Price Alert Settings" not in output
    assert "Active Price Level Alerts" not in output
```

---

## 4. Test strategy

### 4.1 TDD ordering

1. **RED**: add the two drift-guard tests in §3.4 → fail (old names still in code).
2. **GREEN**: apply §3.1 + §3.2 + §3.3 changes top-down. Tests transition to GREEN.
3. **REFACTOR**: confirm `_CRITICAL_FIELDS_PATH_A` wordlist (L3089) and PATH_A test still pass — wordlist anchors moved from disabled-state-incidental words to header + `OFF` body.

### 4.2 Verification commands (before claiming complete)

```bash
# Stale-string sweep — old tool name must be absent from src/ and tests/.
# docs/ is intentionally NOT in scope: historical plan/spec/inventory files
# (docs/superpowers/plans/, .working/) retain old name as audit trail (per memory
# `feedback_docs_no_inline_changelog` — historical plans are not rewritten).
#
# Substring safety note: `set_price_alert` is NOT a substring of the new name
# `set_price_volatility_alert` — both share the prefix `set_price_` and suffix
# `_alert` but the middle differs (`a` vs `v` at byte 11), so plain grep does
# not false-match the new name. Plain grep IS used intentionally because it
# also captures superstring forms we want renamed in the same sweep:
#   - `set_price_alert`              (tool name itself)
#   - `_invoke_set_price_alert`      (test helper in test_fact_only_wordlist.py)
#   - `test_set_price_alert_*`       (test function names)
#   - `_summarize_set_price_alert`   (display.py)
# A word-boundary form (`\bset_price_alert\b`) would miss `_invoke_*` and
# `_summarize_*` because `_` is a word character.
grep -rn "set_price_alert" src/ tests/                              # expect 0
grep -rn "Price Alert Settings\|Active Price Level Alerts" src/ tests/  # expect 0
grep -rn "Volatility alert: " src/                                  # candidate-A line-prefix dropped, expect 0

# Full test pass
uv run pytest tests/ -x

# Wordlist regression — fact-only lint must still pass after the rename
uv run pytest tests/test_fact_only_wordlist.py -x
```

### 4.3 Risk and rollback

| Risk | Level | Mitigation |
|---|---|---|
| sim forensic ambiguity — `set_price_alert` in sim #8 DB vs `set_price_volatility_alert` in post-iter sims | 🟡 medium | YAGNI: historical rows left as-is per memory `project_r2_8b_legacy_decision_restore_boundary`; cross-sim analytics (`analyze_sim.py` / `diff_sim.py`) update tracked as a §6 follow-up if W3 data needs aggregation |
| Agent's residual mental model around old name | 🟢 low | Layer-1 persona / system prompt do not reference specific tool names (principle 8); agent re-learns from docstring at first cycle post-rename |
| Test byte-equal drift miss in `cli/display.py` strings | 🟢 low | Drift-guard test in §3.4 + grep sweep in §4.2 |
| `REGISTERED_TOOL_NAMES` position drift | 🟢 low | Position-preserving rename (list slot unchanged) |

**Rollback unit**: single feature branch `iter-tool-opt-alert-family-rename` → single PR → single `git revert <merge-commit>`. Spec/plan commits land first (per memory `feedback_plan_doc_commit_first`) and are retained on revert as historical reference.

---

## 5. W3 validation gate

**Independence of rename and gate**: the rename is justified independently by principle 1 — `set_price_alert` is misleading-by-name (the tool tunes volatility threshold + window, not a discrete price alert). §5 gates the *interpretability of the W3 ratio metric*, not whether the rename ships. The rename ships regardless of audit outcome; §5.1 only decides whether §5.2's ratio is a meaningful adoption signal.

### 5.1 Pre-launch intent-correctness audit (gates §5.2 meaningfulness)

The 1:34 baseline (4 vs 136 calls) is only a *bias signal* if some non-trivial portion of those calls reflect tool-name confusion — i.e. the agent picked one when intent was the other. If audit shows all observed calls were intent-correct, 1:34 is *natural demand* and rename should not be expected to move it; post-rename W3 ratio variance would then be noise, not signal.

Audit procedure (one-time, before W3 ratio is interpreted):

1. **Universe**: sim #8 (`data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`) — 4 `set_price_alert` cycles + 136 `add_price_level_alert` cycles.
2. **Sampling**:
   - Audit **all 4** `set_price_alert` cycles (small enough to enumerate).
   - Audit a **stratified sample** of `add_price_level_alert` cycles:
     - **Priority stratum (audit all)**: cycles whose agent narrative within ±3 cycles contains any of `spike|sharp|volatile|sudden|whipsaw` — these are vol-tune intent candidates likely misrouted to `add_price_level_alert`. Upper bound on this stratum: all cycles matching the regex.
     - **Background stratum**: from the remainder, draw **N=20 random** (`random.seed(42)` for reproducibility).
3. **Per-cycle judgment**: read the agent's narrative reasoning for each sampled cycle. Did the stated intent match the tool semantics, or did the narrative reveal name confusion (e.g. agent describes wanting a one-shot level trigger but called `set_price_alert`, or describes wanting volatility-sensitivity tuning but called `add_price_level_alert`)?
4. **Audit verdict** (record in follow-up memory):
   - **Confusion found (≥1 misroute in either direction)** → 1:34 is bias signal; W3 ratio gate (§5.2) is meaningful.
   - **No confusion found (priority stratum + 20-sample background both intent-correct)** → 1:34 is natural demand; W3 ratio gate is *non-applicable*. Iter-10 success is judged by absence of regression in alert-family narrative legibility (qualitative read of W3 sim narrative), not by ratio.

### 5.2 W3 ratio gate (only if §5.1 finds bias signal)

Define the W3 ratio as `R = vol_calls / level_calls` where `vol_calls` = post-rename `set_price_volatility_alert` calls and `level_calls` = `add_price_level_alert` calls. Baseline `R_0 = 4/136 ≈ 0.029` (i.e. 1:34).

| Numeric bucket | Symbolic form | Verdict | Action |
|---|---|---|---|
| `R > 0.10` | better than 1:10 | retain | Mark iter-10 as adoption-confirmed in `MEMORY.md` |
| `0.04 < R ≤ 0.10` | (1:25, 1:10] | observe | Hold one more sim cycle before declaring |
| `0.025 ≤ R ≤ 0.04` | [1:40, 1:25] | docstring promote | Iterate on docstring; do not revert rename |
| `R < 0.025` AND `vol_calls + level_calls ≥ 40` | worse than 1:40, adequate sample | rollback | `git revert` + reopen with alternative naming candidate |
| `R < 0.025` AND `vol_calls + level_calls < 40` | worse than 1:40, low sample | non-evaluable | Hold for another W3 cycle before judging |

The buckets partition `R ∈ [0, ∞)` without gap or overlap (modulo the non-evaluable predicates in §5.3). The asymmetric rollback gate (total ≥40 requirement) prevents low-sample noise from triggering a false-positive revert.

### 5.3 Non-evaluable predicates (apply before §5.2)

W3 monitoring assumes W3 sim covers comparable activity. The signal is **non-evaluable** if any of these holds:

- `vol_calls == 0`: cannot distinguish "agent doesn't need volatility tuning" from "rename failed to surface the tool". Defer to a later sim with ≥1 vol_call.
- `level_calls == 0`: ratio undefined (division by zero).
- Both `== 0`: no alert activity at all.

In any non-evaluable case, defer judgment to a later sim with alert activity rather than entering the §5.2 bucket table.

---

## 6. Out of scope / follow-ups

| Item | Why deferred |
|---|---|
| iter-11 `alert-age` (AL-1 + AA-4) | Separate iter, separate spec, separate PR — brainstorm decision 2026-05-13 |
| `analyze_sim.py` / `diff_sim.py` alias mapping | Cross-sim aggregation script change is YAGNI until W3 actually needs aggregation across the rename boundary |
| DB historical row migration | Per `project_r2_8b_legacy_decision_restore_boundary` — legacy enum / label rows not query-path migrated |
| Other alert-family ergonomic ideas (batch form / direction inference) | Out of scope per R2-Next-E spec §1.4 + §9 (F-T3 deferred to W3) |
| Wrapper docstring further trim | If W3 shows docstring promote (1:25-1:40), iterate then |

---

## 7. References

- Tool optimization roadmap: `.working/tool-optimization/02-execution-roadmap.md` §2 iter-10
- Backlog: `.working/tool-optimization/99-backlog.md` §2.1 / §3.4 / §5.1
- R2-Next-E spec (alert family idempotent + update + reasoning surface): `docs/superpowers/specs/2026-05-12-iter-w2r2-next-e-alert-family-design.md`
- Tool design principles: `docs/superpowers/principles/tool-design-principles.md`
- Memory: `project_r2_8b_legacy_decision_restore_boundary` (historical row migration policy)
- Memory: `feedback_plan_doc_commit_first` (spec/plan commit before impl)
- Memory: `feedback_docs_no_inline_changelog` (no inline changelog / self-review sections)
