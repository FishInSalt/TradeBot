# Iter w2r2-next-e — Alert 工具家族治理

**Date**: 2026-05-12
**Iteration**: w2r2-next-e (Iter 2 of sim #8 W2 tool optimization roadmap)
**Type**: Design spec
**Source brainstorm**: 6-question brainstorm session 2026-05-12, followed by sim #8 first-source audit
**Prep doc**: `.working/sim8-w2-tool-optimization-roadmap.md` §3.5.3 + §4.3 Iter 2 + §6.1
**Audit pivots**: sim #8 narrative + tool_calls table inspection invalidated the original "drop direction parameter" hypothesis and weakened the "trail frequency" justification for the new update tool; this spec reflects the post-audit decisions.
**Related principles**: `docs/superpowers/principles/tool-design-principles.md` — 1 / 4 / 5 / 6 / 8

---

## 0. One-minute summary

The agent's alert-management workflow shows three frictions in sim #8 (1818 tool calls / 19.2h):

1. **`cancel_price_level_alert` 40% biz_error rate** (10/25 calls) — every failure is `alert_not_found` after either auto-trigger removal OR position-close auto-clear (`_clear_stale_alerts_for_full_close`, PR #27 Iter 6). The agent self-recovers via prose but burns 100-150 tokens per cycle.
2. **Trail-update workflow is two-step** — agent moves an alert as price drifts by `cancel(old) + add(new)`; in W2 this occurs in ≤6 / 178 cycles (3.4%) — not high-frequency, but the multi-step pattern is reproducible and identity-bearing (the alert "is the same thing at a new price", not a fresh alert).
3. **Reasoning lost on cancel/update output** — `get_active_alerts` already surfaces per-alert reasoning (`tools_perception.py:572`); cancel and update do not.

This spec applies the **Alert family treatment** narrowed cluster:

- **`cancel_price_level_alert`** turns idempotent — alert-not-found becomes `ok` with a fact-only `Note:` line that covers both root causes ("already triggered or removed"). Format-invalid and DB exceptions remain `biz_error` (principle 6 — operation failures reject explicitly; state-already-resolved is idempotent ok with note).
- **`update_price_level_alert`** is added (new tool) — single-alert replacement `(alert_id, new_price, reasoning) → str`; preserves the original direction and reasoning; alert not active → explicit reject (principle 6 — `update` of nonexistent state is semantically unfulfillable, unlike `cancel`).
- **F-A3 piggyback** — `cancel` and `update` tool outputs surface the alert's original reasoning text. `get_active_alerts` already includes reasoning, so this completes the family for cancel/update.

`add_price_level_alert` is **intentionally unchanged** in this iter. The original Iter 2 plan proposed a `levels: list[float]` batch form with auto-inferred direction. The sim #8 audit found 10 instances of `may trigger immediately` tool warnings where the agent *deliberately* accepted the immediate-trigger state as a strategic re-wake mechanism — auto-inferring direction from `level vs current_price` would silently flip the agent's intent (above → below for preemptive levels). The `direction` parameter is therefore load-bearing and stays. Batch form is **deferred to W3** when a direction-preserving batch shape can be validated against fresh data (see §9 follow-ups).

Layer-1 in `persona.py` is **intentionally untouched** (principle 8 — agent behavior changes through tool surface and docstrings, not prompt nudges; consistent with PR #46 revert in commit `7510a56`).

No schema migration: price-level alerts are in-memory state on `BaseExchange` (`self._price_level_alerts: list[dict]`); the alert dict already carries `reasoning` (`base.py:188`) and `direction`.

`REGISTERED_TOOL_NAMES` count grows 32 → 33.

---

## 1. Empirical foundations

### 1.1 Source data and audit method

- sim #8: 178 cycles / 19.2h / 14.36M tokens / 1818 tool calls
  - DB: `data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`
  - Session log: `logs/session_8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3.log`
- Inventory: `.working/sim8-w2-tool-ergonomics.md` (5-dimension F-* findings) — relevant: F-F1 (cancel 40% fail), F-A2 (trail/update), F-A3 (reasoning visibility); F-T3 (batch form) is deferred.
- Roadmap: `.working/sim8-w2-tool-optimization-roadmap.md` §3.5.3 + §4.3 Iter 2 + §6.1
- **Audit (2026-05-12)**: tool_calls table SQL + session log grep verified the data for each issue; corrections to the original roadmap interpretation are noted inline.

### 1.2 Per-issue datum table

| Issue | Tool | Datum | Source |
|---|---|---|---|
| F-F1 cancel idempotent | `cancel_price_level_alert` | 25 calls / 10 biz_error `alert_not_found` (40%); 15 ok | sim #8 tool_calls table |
| F-F1 root cause class A | `cancel_price_level_alert` | auto-trigger removal during price cascade — session log cycle 7ee9130d "auto-consumed during the cascade" (lines around 2487 in log) | session log narrative |
| F-F1 root cause class B | `cancel_price_level_alert` | position-close auto-clear via `_clear_stale_alerts_for_full_close` (PR #27 Iter 6) — session log "*it was at 82,000 and we never got there, so it must have been auto-cleared*" (cycle 32babac6 area) | session log narrative + `base.py:244` (`_clear_stale_alerts_for_full_close`, calls `clear_level_alerts_by_symbol` at `base.py:217`) |
| F-A2 trail frequency | `cancel + add` cycles | 7/178 cycles contain both ok cancels and ok adds; subtracting bulk-cleanup cycle e6929b2c (4 cancels + 1 add) leaves **6/178 = 3.4% upper bound**. The strict-trail lower bound (cancel of a level X immediately followed by add of a different level Y, with the agent narrative explicitly framing it as a move) is harder to enumerate from tool_calls args alone and is necessarily ≤ this upper bound; the §2.2 closure-value argument uses the upper bound as the load-bearing datum and does not require a tighter lower bound. | tool_calls cycle-level aggregation; cycle-id list available in spec drafting notes |
| F-A2 trail narrative | `cancel + add` cycles | explicit "trail" or "move alert" phrasing in cycles dc3d1b8a, e6929b2c, and the 81,410-replaced-with-81,400 sequence around session log lines 29302-29327 | session log grep `trail|move.*alert|update.*alert` |
| F-A3 reasoning lost on cancel | `cancel` output | 10 cancel-fail narratives in session log; agent acknowledges fate ("auto-consumed", "already triggered or expired") but does not consistently re-narrate the original reasoning; benefit of surfacing it is observed as low-friction nicety, not load-bearing | session log narrative |

### 1.3 Implications for the spec

- **F-F1 root cause is dual-source**: original roadmap §3.5.3 attributed all 10 failures to "alert auto-trigger removal"; audit reveals at least one substantial cohort is `_clear_stale_alerts_for_full_close` removal on position close. Both belong to the **principle 6 §3.2 "state does not exist → idempotent ok with note"** boundary class (not the §2 "silent clamp" class). The uniform note "(already triggered or removed)" is fact-only and covers both root paths without forcing an audit query.
- **F-A2 trail frequency is low (3.4%)**: this weakens — but does not eliminate — the case for `update_price_level_alert`. The justification shifts from "high-frequency batch closure" (originally proposed) to "single-call identity-continuity closure for the trail pattern that does occur". See §2.2.
- **F-A3 ROI is low but cost is low**: cancel/update output reasoning surfacing is a low-friction nicety (~20-30 lines of code, 2 tests); kept in scope as a piggyback because dropping it would not meaningfully simplify the spec.

### 1.4 Audit pivot: direction parameter is load-bearing (F-T3 deferred)

The original spec proposed dropping the `direction` parameter from `add_price_level_alert` on the rationale that direction can be inferred from `level vs current_price`. The audit refuted this:

- **10 sim #8 instances of the `may trigger immediately` tool warning**: the warning fires when `direction="above"` is set with `level ≤ current_price` (or `direction="below"` with `level ≥ current_price`). Auto-inference would replace these with the opposite direction.
- **In ≥6 of 10 instances, the agent's narrative explicitly accepted or welcomed the immediate-trigger state** as a strategic re-wake mechanism:
  - session log line 16940 — "*the 81,662 alert may trigger immediately, which would be a wake-up. That's fine — it tells me the 1h…*"
  - session log line 30678 — "*the alert may trigger immediately and wake me up. Let me just get fresh market data…*"
  - session log line 31338 — "*'current price 81,508.90 already above 81,488.00, may trigger immediately.' So it will trigger. But that's actually…*"
  - session log line 10488 — "*the system may wake me up again immediately for that alert. But I'm already processing this cycle, so I should handle everything now.*"
- **The direction is the agent's expressed intent for the trigger direction, not a function of current price**. An "above 82,000" alert means "wake me when price crosses 82,000 upward" — whether current price is below or above 82,000, the intent of the *crossing direction* is the data, and the agent uses it deliberately.

Auto-inference would silently flip 6+ deliberate intents from the agent. This violates principle 1 (fact-only, no silent re-interpretation) and principle 8 (trust the agent, do not nudge or rewrite). The `direction` parameter stays as a required argument on `add_price_level_alert`.

The batch form (F-T3 — multi-call cycles 41/178 = 23%) is deferred to W3 when a direction-preserving batch shape can be designed. Candidate shapes (`levels_above` + `levels_below` paired lists, `list[dict]` with per-level direction, parallel arrays) all introduce LLM ergonomics trade-offs that are not worth resolving without fresher data. See §9.

---

## 2. Architecture and scope

### 2.1 Issue → change matrix

| Issue ID | Tool | Change |
|---|---|---|
| F-F1 | `cancel_price_level_alert` | idempotent on alert-not-found (uniform `Note:` line covering both auto-trigger and close-fill removal); format-invalid + DB exceptions remain `biz_error` |
| F-A2 | `update_price_level_alert` (new) | new @tool: single-alert replace `(alert_id, new_price, reasoning) → str`; preserves original `direction` and `reasoning`; explicit reject if alert not active |
| F-A3 | `cancel` + `update` output | output strings include the alert's original `reasoning` text |
| F-T3 | `add_price_level_alert` | **deferred to W3**; no change in this iter |

### 2.2 Principle reconciliation for the new tool (principle 4 ↔ 5, post-audit)

Adding `update_price_level_alert` raises the registered tool count from 32 to 33. Principle 4 ("信号补齐优先于新工具；工具数量是 agent 选择延迟的物理约束") sets a high bar for new tools. Principle 5 ("接口闭环常用 pattern；高频 multi-call 拼凑是设计缺陷，应通过 list / preset / batch 让单调用闭环") favors closing the trail-update loop in a single call.

The audit revised one leg of the original three-fold justification:

1. ~~Empirical frequency~~ — **DROPPED**. W2 trail-pattern frequency is 3.4% (≤6/178 cycles), below the spec's own original W3 revisit threshold of "< 0.1/cycle". The frequency argument does not survive its own data.
2. **Single-call closure value (preserved)**: a trail via `cancel + add` does not preserve identity continuity for the alert — each replaced alert gets a new id and (in the absence of F-A3) loses the original reasoning string. `update` preserves the "this is the same alert at a new price" intent, returning the new id as an implementation detail of how `BaseExchange.add_price_level_alert` mints ids. The agent's narrative across trail flows ("trailing the 81,720 alert up to 82,200") is more legible when the tool layer expresses one operation rather than two.
3. **Symmetry with `cancel` and `add` (preserved)**: the alert family already exposes `add` (create) and `cancel` (remove). `update` (replace) completes the standard create / replace / remove triple — agents do not need to learn a new shape. The mental model cost is sub-linear in the tool count.

The principle-4 vs principle-5 trade-off resolves toward principle 5 here because:

- The closure-value argument is **per-event load-bearing**: when trail does happen (rare but reproducible), `cancel + add` discards identity information the agent values (original reasoning, "this is the same alert"). `update` recovers it cleanly.
- The principle-4 cost is bounded by *low observed trail frequency × marginal selection-latency from one extra tool name*. Selection latency in LLM tool selection is anecdotally believed to scale with the tool *name* count more than with docstring length (no measured citation in this codebase; Iter 4 PR #25 reduced Layer-1 docstrings but did not measure tool-selection-latency directly). Either way the marginal cost is small in absolute terms (one name added to a 32-entry list) but is paid every cycle, while the closure benefit accrues only on the 3.4% of cycles where trail occurs. Accepting this asymmetry is a deliberate trade-off, not a free win — see W3 gate in §9.

W3 validation gate is **re-specified** in §9: instead of a frequency threshold, the gate asks whether agent narrative actually uses `update` (as opposed to falling back to `cancel + add`) and whether trail flows in cycles that have `update` available are more legible. If the answer is "agent uses cancel+add anyway" or "narrative is no clearer", revisit removal.

### 2.3 Layer-1 persona is intentionally untouched

Per principle 8 ("信任 agent + 工具优先；prompt nudge 是 last-resort"), the docstring-on-tool surface is the sole behavioral lever. The docstrings of `cancel`, `update`, and the unchanged `add` cover the family's intent expressions. PR #46's revert (`7510a56`) is precedent: Layer-1 nudges added to support a tool change are last-resort, not first-resort.

`REGISTERED_TOOL_NAMES` in `src/agent/trader.py:687` is the only persona-adjacent file that needs editing, and only because it's the drift-guard list (not a behavioral nudge).

### 2.4 Out of scope (deferred to W3 or independent mini-PR)

- **F-T3 batch form for `add_price_level_alert`** — deferred to W3 (see §1.4 and §9). Candidate shapes preserve direction at the cost of LLM ergonomics; W3 data is the natural decision point.
- **Iter 3 `set_next_wake` event-driven mode** — separate iteration, separate brainstorm.
- **Iter 4 `get_derivatives_data` OI change rate** — separate mini-PR.
- **OOS `adjust_leverage` deletion** — independent mini-PR (10 lines, aligned with `persona_dead_config_decision` memory).
- **Volatility alert (`set_price_alert`) family** — distinct tool (percent-move threshold + window), out of this iter's scope.
- **`evaluate_trade_setup` new tool** — Iter 5, deferred to W3.
- **Layer-1 prompt nudges** — principle 8 (see §2.3).

---

## 3. `cancel_price_level_alert` — idempotent

### 3.1 Current signature and behavior

`src/agent/tools_execution.py:276-299`:

```python
async def cancel_price_level_alert(deps: TradingDeps, alert_id: str, reasoning: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{8}", alert_id):
        note_biz_error("invalid_alert_id_format")
        return f"Invalid alert_id format: {alert_id!r}. ..."
    ok = deps.exchange.remove_price_level_alert(alert_id)
    if ok:
        await _record_action(...)
        return f"Price level alert cancelled (id={alert_id})"
    note_biz_error("alert_not_found")
    return f"Alert {alert_id} already triggered or expired"
```

### 3.2 Target signature and behavior

Signature unchanged. Behavior changes:

| Case | Current return | Current biz_error | Target return | Target biz_error |
|---|---|---|---|---|
| `alert_id` format invalid | `Invalid alert_id format: ...` | `invalid_alert_id_format` | unchanged | unchanged |
| `alert_id` format ok, found and removed | `Price level alert cancelled (id={alert_id})` | none | `Price level alert cancelled (id={alert_id}) — "{reasoning}"` (F-A3) | none |
| `alert_id` format ok, not found in active set | `Alert {id} already triggered or expired` | `alert_not_found` | `Alert {id} no longer active (already triggered or removed)` | **none (idempotent ok)** |
| Unexpected internal exception (no DB on this path; in-memory list scan only) | (raises through) | (raises through) | unchanged | unchanged |

### 3.3 Boundary with `feedback_observation_period_soft_constraint` §2

The memory entry `feedback_observation_period_soft_constraint` §2 says: "执行类优先 explicit reject 而非 silent clamp". The principle 6 entry in `tool-design-principles.md` adds: "**状态不存在 idempotent + ok with note**".

The cancel-not-found case is the §3.2 idempotent class:

- **§2 silent clamp** is when the tool receives a parameter, silently coerces it to a different valid value, and proceeds — the user is unaware their input was modified.
- **§3.2 idempotent ok with note** is when the requested end-state (alert id no longer active) is already satisfied — no parameter is silently changed; the operation's goal has been reached by another path (auto-trigger OR close-fill auto-clear). The `Note:` line makes the path-divergence explicit (fact-only).

### 3.4 The fact-only `Note:` line

Uniform message: `Alert {id} no longer active (already triggered or removed)`. Rationale:

- **Fact-only**: "no longer active" is observable fact (`remove_price_level_alert` returned False). The parenthetical disjunction names two physical paths to this state (auto-trigger or close-fill removal) without claiming which one happened — no audit query needed.
- **Covers both root causes**: §1.2 audit identified at least two removal paths (auto-trigger during cascade; close-fill via `_clear_stale_alerts_for_full_close`). The disjunctive note covers both without adding implementation overhead.
- **Principle 1 alignment**: "fact-provider 不是 guard". The note does not advise the agent; it states the state.

### 3.5 F-A3 piggyback (success case)

Success return string is extended to include the original reasoning text:

```
Price level alert cancelled (id={alert_id}) — "{reasoning}"
```

Where `{reasoning}` is the `reasoning` field stored on the alert dict at creation time (`base.py:188`), retrieved **before** `remove_price_level_alert` mutates the list. Pseudo-code:

```python
alert = _lookup_alert(deps.exchange, alert_id)   # peek, no mutation
if alert is None:
    return idempotent_ok_with_note(alert_id)
ok = deps.exchange.remove_price_level_alert(alert_id)  # mutates list
if not ok:
    # Defensive: lookup and remove are both sync, in-cycle; remove failing
    # after a successful lookup would indicate a real invariant violation.
    raise RuntimeError(
        f"remove_price_level_alert returned False for id={alert_id} "
        f"that was just present in lookup — invariant violated"
    )
return f'Price level alert cancelled (id={alert_id}) — "{alert["reasoning"]}"'
```

(The earlier draft used `assert ok` here; `assert` is stripped under `python -O`, so any production check must be explicit `if not ok: raise`.)

`_lookup_alert(exchange, alert_id) -> dict | None` is a **`tools_execution.py` module-level private helper** (not a `BaseExchange` method) that scans `exchange.get_price_level_alerts()` once and returns the matching dict by id (or None). Defined in this module so it can be shared between `cancel_price_level_alert` and `update_price_level_alert` without expanding the exchange interface — see §5.2 impl notes.

The cancel call's own `reasoning` argument is logged via `_record_action` as before; it does not enter the return string.

---

## 4. `update_price_level_alert` — new tool

### 4.1 Signature

```python
async def update_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    new_price: float,
    reasoning: str,
) -> str:
    """Replace a single existing price level alert with a new price.

    Atomic: cancels the old alert and creates a new one with new_price, preserving
    the original direction and reasoning text. The direction (above/below) cannot
    change — to change direction or reasoning materially, use cancel + add.

    Args:
        alert_id: 8-char hex id of the existing alert (see get_active_alerts).
        new_price: new trigger price.
        reasoning: brief rationale for the move (audit-only; not stored on alert).
    """
```

### 4.2 Behavior

1. **Validate `alert_id` format** (8-char hex regex, same as `cancel_price_level_alert`). Format invalid → `biz_error: invalid_alert_id_format`. Message: `Invalid alert_id format: {alert_id!r}. ...`.
2. **Lookup alert** in `self._price_level_alerts` by id (read-only scan of the list). Not found → `biz_error: alert_not_found`. Message: `Alert {id} not found. To create a new alert, use add_price_level_alert.` (Principle 6: update of nonexistent state rejects; principle 1: fact-only directive to the correct tool.)
3. **Capture original `direction` and `reasoning`** from the alert dict (for transfer to the new alert and for F-A3 surfacing in return).
4. **Sequential replace (single-coroutine, no yield points)**: call `remove_price_level_alert(alert_id)` then `BaseExchange.add_price_level_alert(new_price, original_direction, symbol, original_reasoning)`. Both calls are synchronous and mutate the same in-memory list on `BaseExchange` (`self._price_level_alerts`); the coroutine yields no control between them, so no other task can observe a partial state. After the remove succeeds, the cap headroom is necessarily ≥ 1, so the add cannot fail with `None` from the cap check.
5. **Direction preservation**: the new alert keeps `original_direction`. Direction-change semantics (e.g., turning an above-alert into a below-alert) belong to `cancel + add`, not `update`. The new_price can be on either side of current_price — see §4.3.
6. **Reasoning preservation**: the new alert keeps `original_reasoning`. The agent's `reasoning` argument to `update_price_level_alert` is logged via `_record_action` (audit trail of why the move was made), not propagated to the new alert.
7. **Return success**:
   ```
   Price level alert updated (id={alert_id} → id={new_id}):
     {original_direction} {old_price:.2f} → {original_direction} {new_price:.2f} — "{original_reasoning}"
   ```
8. **`_record_action`** logs the update with `action="update_price_level_alert"` (literal), `alert_id={new_id}` (the new id, to mirror `add_price_level_alert`'s `_record_action` convention), and the agent's `reasoning` argument concatenated with the audit context:

   ```python
   await _record_action(
       deps,
       action="update_price_level_alert",
       alert_id=new_id,
       reasoning=f"replaces {alert_id} ({original_direction} {old_price}) → {new_price} | {reasoning}",
   )
   ```

   This convention puts the **new** id in the canonical `alert_id` column (consistent with `add_price_level_alert` `_record_action` semantics) and folds the **old** id + direction + old price into the reasoning string. The `trade_actions.alert_id` schema is not extended in this iter (single column, no `alert_id_new`/`alert_id_old` split).

   **Consequence — `v_alert_lifecycle` view sees neither side of the update**:
   - `registers` CTE (`storage/views.py:94-100`) filters `action='add_price_level_alert'` — the update's audit row has `action='update_price_level_alert'`, so the **new id is entirely absent from the view** (the view's `FROM registers r` at line 149 never has the row to LEFT JOIN against).
   - `cancels` CTE (`storage/views.py:112-118`) filters `action='cancel_price_level_alert'` — the **old id receives no cancel row** and stays as `final_status='active'` orphan.
   - Net effect over an N-step trail chain: N `active` orphans (each old id at each trail step) + 0 final cancel/trigger records, and the latest id (the one actually live in `_price_level_alerts`) doesn't appear in the view at all.

   This is a known limitation; remediation is a §9 follow-up (W3-validated view evolution), not a blocker for this iter — the in-memory state is correct, only the view's trail-chain reconstruction is broken. The view limitation is recoverable post-hoc from `tool_calls.args` if W3 analysis needs it.

No distance / minimum-move validation is performed on `new_price`. This keeps `update` symmetric with `add_price_level_alert`, which has no distance floor either (verified at `tools_execution.py:241-273`). If a zero-move or near-zero-move update is found to be a real friction in W3, the floor can be added to both tools as a paired change (see §9).

### 4.3 Direction is preserved across moves; immediate-trigger remains valid

When the agent updates an alert, the new price may cross the current price (e.g., `above 82,000` updated to `above 82,500` while current price is 82,300 → still above; or `above 82,000` updated to `above 81,900` while current price is 81,950 → triggers immediately).

This is intentional: `update` is a *price reposition*, not a direction-reset. If the new price would cause immediate trigger, the alert dispatches immediately on the next `_check_price_levels` tick (consistent with the agent's `add` semantic — see §1.4 audit finding on the agent's deliberate use of immediate-trigger as a re-wake mechanism). The tool does not warn or block.

To change direction materially (e.g., flip "above 82,000" to "below 82,000"), the agent uses `cancel + add` — direction-flip is a semantic re-creation, not a trail.

### 4.4 Reasoning ownership boundary

The original alert's `reasoning` text identifies *why this price level is interesting* (e.g., "82,000 是 4h 高点"). When the agent trails the alert, the underlying *why* typically does not change — the structural level shifts slightly, but the intent persists. Carrying the original reasoning into the new alert preserves identity continuity.

If the agent's reasoning has materially changed, the correct flow is `cancel + add(reasoning="new rationale")` — a two-step explicit re-creation. `update_price_level_alert` is the lightweight trail tool, not a reasoning-rewrite tool. This boundary is documented in the `update` docstring (§5.3).

### 4.5 Why update produces a new id

`BaseExchange.add_price_level_alert` mints a fresh id (`uuid.uuid4()[:8]`). Re-using the old id would require API surgery in the integration layer with no observable benefit — the agent already reads the new id from the return string (same as `add_price_level_alert`). Future read-backs via `get_active_alerts` show the new id with the same reasoning, which is the load-bearing identity carrier for the agent.

The `Updated (id=X → id=Y)` syntax in the return string makes the rebinding explicit.

### 4.6 Why update is not just `cancel + add`

The principal argument is identity continuity (§4.4, §2.2). Secondary cost factors when trail does occur:

- Two tool round-trips instead of one (concrete latency depends on LLM/network environment; not quantified here)
- Two `tool_calls` table rows (observability noise)
- Two agent-narrative segments ("I'll cancel X" + "Now I'll add the new alert at Y") versus one ("I'll move alert X up to Y")
- F-A3 reasoning surfacing only on the cancel path requires the agent to manually pass `reasoning` to the new add call — without `update`, the F-A3 piggyback alone cannot enforce reasoning continuity across the trail

These are smaller effects than identity continuity, but they compound per-event.

---

## 5. Cross-cutting

### 5.1 Cross-cutting registry / dispatch updates

This iter touches **six** registry / dispatch surfaces beyond the three direct tool files. Missing any breaks tests, silently disables R2-8a business-rejection detection, or — most subtly — causes the new `cancel` idempotent-ok return to be misclassified as a tool error in UI/metrics layers (A1 in the post-v5 review). Each surface is enumerated below.

#### 5.1.1 `REGISTERED_TOOL_NAMES` (`src/agent/trader.py:687`)

- 32 → 33 entries; insert `"update_price_level_alert"` in the execution-tools cluster (immediately after `"cancel_price_level_alert"`).

#### 5.1.2 `tests/test_trader_agent.py:85` count drift guard

- Assertion `len(REGISTERED_TOOL_NAMES) == 32` → `== 33`.
- Error-message literal `"Expected 32 tools (20+11+1), got {len(REGISTERED_TOOL_NAMES)}"` → `"Expected 33 tools (20+12+1), got ..."` (execution cluster 11 → 12).

#### 5.1.3 `tests/test_display_cycle.py` count drift guard — four-point patch

This test (`test_dg_2_dispatch_sets_partition_all_registered_tools`) has four literal occurrences of the count `32` / `11` that must update in lockstep:

| Line (approx; verify in plan stage) | Item | Patch |
|---|---|---|
| 1442 | Docstring "covers 32 registered tools" | `32` → `33` |
| 1445 | Docstring `(32)` and `_EXECUTION_TOOL_NAMES (11)` | `32` → `33`; `(11)` → `(12)` |
| 1469 | Inline comment | `32` → `33` |
| **1481** | `assert len(execution) == 11` | `== 11` → `== 12` (**real failure** — not doc-only) |

Plan stage confirms the exact lines after the cluster edit lands.

#### 5.1.4 `src/cli/display.py` execution dispatch (three sub-structures)

`is_tool_error` (display.py:289-303) and `resolve_tool_display` route execution-tool returns through **three** module-level dispatch structures. All three must update in lockstep.

##### 5.1.4.1 `_EXECUTION_TOOL_NAMES` frozenset (display.py:492-504)

Canonical execution-tools membership set, used by `display.py:815` (`if tcp.tool_name in _EXECUTION_TOOL_NAMES`) and by `test_dg_2_dispatch_sets_partition_all_registered_tools` (asserts `_PERCEPTION_TOOL_NAMES ∪ _EXECUTION_TOOL_NAMES ∪ {save_memory} == REGISTERED_TOOL_NAMES`).

- Add `"update_price_level_alert"` to the frozenset (after `"cancel_price_level_alert"`).
- Without this, `test_dg_2` fails (union has 32, declared has 33) — **hard test failure**, not silent drift. The §5.1.3 `len(execution) == 12` literal patch alone is insufficient if the source set is not also extended.

##### 5.1.4.2 `_EXECUTION_PARSERS` dict (display.py:252-263)

Map `"update_price_level_alert"` → a `_summarize_update_price_level_alert(content: str) -> str` helper. Dispatch contract is **`(content: str) -> str`** — parsers receive the tool's return string (only `summarize_save_memory` uses `args`). Follow the existing `_summarize_add_price_level_alert` pattern (display.py:238):

```python
def _summarize_update_price_level_alert(content: str) -> str:
    # Matches §4.2 step 7 success-return shape:
    #   "Price level alert updated (id=AAAA → id=BBBB):
    #      above 82100.00 → above 82500.00 — \"reasoning\""
    m = re.search(r"(above|below)\s+([\d.]+)\s*→\s*(above|below)\s+([\d.]+)", content)
    if m:
        return f"{m.group(1)} ${float(m.group(2)):,.0f} → ${float(m.group(4)):,.0f}"
    return _fallback_summary(content)
```

##### 5.1.4.3 `_EXECUTION_SUCCESS_PREFIXES` dict (display.py:266-278) — two entries change

`is_tool_error` (display.py:289-303) treats any execution-tool return that does **not** start with a registered success prefix as a tool error (`✗` icon in UI + error-count metrics). The cancel idempotent-ok return (`"Alert {id} no longer active (already triggered or removed)"`) does **not** start with the existing `"Price level alert cancelled"` prefix — **without remediation, every idempotent ok is silently misclassified as error**, defeating the entire idempotent design.

`_EXECUTION_SUCCESS_PREFIXES` supports tuple-of-strings to enumerate multiple valid prefixes (see existing `add_price_level_alert` entry which is already a tuple). Two entries change:

```python
# Before:
"add_price_level_alert": ("Price level alert set:", "Alert set"),
"cancel_price_level_alert": "Price level alert cancelled",

# After:
"add_price_level_alert": ("Price level alert set:", "Alert set"),                # unchanged
"cancel_price_level_alert": (
    "Price level alert cancelled",   # cancel success (real removal)
    "Alert ",                         # cancel idempotent ok ("Alert {id} no longer active ...")
),
"update_price_level_alert": "Price level alert updated",                         # NEW
```

Notes:
- `"Alert "` (trailing space) covers the idempotent return shape `"Alert {id} no longer active (...)"`. Format-invalid errors (`"Invalid alert_id format: ..."`) are emitted via `note_biz_error` with outcome != "success" and short-circuit at line 295 before any prefix check, so the `"Alert "` prefix does not false-match them.
- The new `update` entry is a single string because update's not-found path is reject (outcome != "success" via `note_biz_error`), not idempotent ok — only one success-prefix needed.
- §5.4 test #10 (`test_cancel_idempotent_not_classified_as_error`) pins this against future regression.

Missing any of 5.1.4.1 / 5.1.4.2 / 5.1.4.3 produces a different failure mode (hard test crash vs missing display fidelity vs idempotent ok misclassified) — drift guards in §5.4 cover all three.

#### 5.1.5 `BIZ_ERROR_TYPES` allowlist (`src/services/tool_call_recorder.py:57-83`)

`alert_not_found` is **already present** in the allowlist (line 60). After this iter, the entry's attribution shifts:

- **Before** (W1/W2): cancel emits `alert_not_found` when the alert is gone.
- **After**: cancel no longer emits any biz_error (idempotent ok with note); update emits `alert_not_found` when the alert is gone.

Comment update:

```python
# Before (line 60):
"alert_not_found",  # cancel_price_level_alert 状态错（已触发/不存在）

# After:
"alert_not_found",  # update_price_level_alert 状态错（已触发 / 已被 close-fill 清理 / 未注册）
```

No new allowlist entry — reusing `alert_not_found` for update preserves the cross-period metrics dimension and removes A-2's "deprecation dance" question. Note: the W2 → W3 attribution shift is documented here; cross-period queries on `error_type='alert_not_found'` over time need this context.

### 5.2 Implementation notes

#### `cancel_price_level_alert` (`tools_execution.py:276-299`)

- Add a lookup helper or inline read of the alert's reasoning before calling `remove_price_level_alert`. Suggested helper signature: `_lookup_alert(exchange, alert_id) -> dict | None` returning the full alert dict (so update can reuse it).
- After `remove_price_level_alert` returns True, use the captured reasoning in the success string.
- After `remove_price_level_alert` returns False, return idempotent ok with note (no `note_biz_error` call).

#### `update_price_level_alert` (new in `tools_execution.py`)

- New `async def update_price_level_alert(deps, alert_id, new_price, reasoning) -> str` immediately after `cancel_price_level_alert` for proximity.
- Reuse `_lookup_alert` helper from cancel changes; captures `direction`, `reasoning`, and `price` (old) in one call.

#### `trader.py` @tool registration

- Add `update_price_level_alert` @tool registration immediately after `cancel_price_level_alert` (`trader.py:591-614`). Order: add (569-589) → cancel (591-614) → update (after 614).
- Use the `@tool` shim from Iter 5 (PR #26) that enforces `require_parameter_descriptions=True`.

#### `tools_perception.py` `get_active_alerts`

- **No change**. Already surfaces reasoning (`tools_perception.py:572`).

#### `add_price_level_alert` (`tools_execution.py:241-273`)

- **No change in this iter**. Signature, immediate-trigger warning, direction-validate block all preserved. F-T3 batch form deferred to W3 per §1.4.

### 5.3 Docstring contracts

The two changed tools and the new tool each get a Google-format docstring with `Args:` covering every parameter (per Iter 5 PR #26 enforcement):

- **`cancel_price_level_alert`**: states that cancel is idempotent (`Returns ok with a Note if the alert is no longer active`). The Args block documents `alert_id` and `reasoning`.
- **`update_price_level_alert`** (§4.1): states direction preservation; the docstring's first paragraph names the cancel+add alternative for direction changes.

The intent is that the agent reads the tool docstring once (via griffe sniff at startup) and can fluently choose between add / cancel / update without consulting Layer-1.

### 5.4 Drift guards (new tests)

`tests/test_alert_family.py` (new file):

1. **`test_cancel_idempotent_not_found`** — given an `alert_id` not present in `_price_level_alerts`, cancel returns ok-with-note, no `biz_error` is recorded.
2. **`test_cancel_format_invalid_still_rejects`** — non-hex / wrong-length `alert_id` returns biz_error, idempotency does not apply.
3. **`test_cancel_success_includes_reasoning`** (F-A3) — alert with reasoning "X" cancelled, return string contains `— "X"`.
4. **`test_update_success_preserves_direction_and_reasoning`** — given active above-alert with reasoning "X", update returns success with `id=A → id=B`, `above {old} → above {new}`, and `— "X"`; old id removed, new id present in `_price_level_alerts` with `direction="above"` and `reasoning="X"`.
5. **`test_update_not_found_rejects`** — given a non-existent `alert_id`, update returns biz_error `alert_not_found`; no alerts mutated.
6. **`test_update_format_invalid`** — non-hex `alert_id` → biz_error `invalid_alert_id_format`.
7. **`test_update_immediate_trigger_allowed`** — `new_price` on the trigger side of current (e.g., above-alert at price below current) is accepted without warning/block, consistent with §4.3.
8. **`test_update_display_dispatch_registered`** — verifies all **three** dispatch structures contain `"update_price_level_alert"`: `_EXECUTION_TOOL_NAMES` (frozenset, display.py:492-504), `_EXECUTION_PARSERS` (display.py:252-263), `_EXECUTION_SUCCESS_PREFIXES` (display.py:266-278). Closes the silent-drift gap (any one missing produces a different failure mode).
9. **`test_update_atomicity_sync_invariant`** — uses `inspect.iscoroutinefunction` to assert `BaseExchange.add_price_level_alert` and `BaseExchange.remove_price_level_alert` are sync (not `async def`). Pins §4.2 step 4's "no yield points" invariant. If a future change makes either method async, this guard fires and the update flow's atomicity must be re-evaluated.
10. **`test_cancel_idempotent_not_classified_as_error`** — calls `is_tool_error("cancel_price_level_alert", "Alert {id} no longer active (already triggered or removed)", outcome="success")` and asserts the result is `False`. Pins the §5.1.4.3 prefix-tuple fix (the post-v5 review A1 finding). Without this guard, a future revert of the prefix tuple silently breaks idempotent ok classification with no test crash. Companion sub-asserts: `is_tool_error("cancel_price_level_alert", "Price level alert cancelled (id=AAAA) — \"reason\"", outcome="success")` returns `False`; `is_tool_error("update_price_level_alert", "Price level alert updated (id=AAAA → id=BBBB): above 82100.00 → above 82500.00 — \"reason\"", outcome="success")` returns `False`.

`tests/test_trader_agent.py:85` (existing — drift guard for `REGISTERED_TOOL_NAMES` count): assertion `== 32` → `== 33`; breakdown literal `(20+11+1)` → `(20+12+1)` in the error message.

`tests/test_display_cycle.py:1445` (existing — drift guard for display dispatch sets vs `REGISTERED_TOOL_NAMES`): count literal `32` → `33` in docstring + assertion; dispatch set membership for `update_price_level_alert` added (verify in plan stage that dispatch routing includes the new tool).

`tests/test_persona.py:261` (existing — Layer-1 bullet count): stays at `== 6` (no change; Layer-1 is intentionally untouched per §5.5).

`tests/test_alert_lifecycle.py:761-787` — `test_cancel_price_level_alert_not_found_records_biz_error` hard-asserts `error_type == "alert_not_found"` and `"already triggered or expired" in result` (the old return string). Both flip under cancel idempotent (§3.2). **Required action (not "verify")**: delete this test entirely — its semantic is fully replaced by `test_cancel_idempotent_not_found` in the new `test_alert_family.py` (§5.4 test #1) and by `test_update_not_found_rejects` (§5.4 test #5) on the biz_error side. See §7 Task 6 for the explicit deletion item.

`tests/test_v_alert_lifecycle.py:95-122` — `cancel_attempts` view CTE test currently constructs a `biz_error / error_type="alert_not_found"` fixture row from a cancel call. After cancel idempotent, this fixture path no longer arises naturally in production. The test continues to work as a synthetic fixture (it just constructs the row directly), but the in-comment narrative about "cancel failures" becomes a historical attribution. Plan stage updates the comment to reflect the cancel-vs-update attribution shift; no assertion changes.

Other existing tests on `add_price_level_alert` — no migration expected since `add_price_level_alert` signature is unchanged.

### 5.5 Layer-1 persona (intentionally untouched)

Per principle 8 and PR #46 commit `7510a56` precedent. No bullets added to `persona.py` Layer-1. The drift-guard test that pins Layer-1 bullet count (`tests/test_persona.py:261`, currently asserting 6 after R2-5 added the "Wake interval control" bullet) stays at 6.

---

## 6. Acceptance criteria

| AC | Statement | Verification |
|---|---|---|
| AC-1 | `cancel_price_level_alert` for an absent `alert_id` returns ok with `Note: Alert {id} no longer active (already triggered or removed)` and records no biz_error | `test_cancel_idempotent_not_found` |
| AC-2 | `cancel_price_level_alert` for a format-invalid `alert_id` still records biz_error `invalid_alert_id_format` | `test_cancel_format_invalid_still_rejects` |
| AC-3 | `cancel_price_level_alert` success output includes the alert's original reasoning in `— "{reasoning}"` form | `test_cancel_success_includes_reasoning` |
| AC-4 | `update_price_level_alert` on an active alert preserves the alert's original `direction` and `reasoning`; returns `Updated (id=X → id=Y)` shape with both | `test_update_success_preserves_direction_and_reasoning` |
| AC-5 | `update_price_level_alert` on an absent `alert_id` returns biz_error `alert_not_found` with directive to use `add_price_level_alert` | `test_update_not_found_rejects` |
| AC-6 | `update_price_level_alert` format check rejects non-hex `alert_id` upfront via `biz_error: invalid_alert_id_format` (no `new_price` distance/minimum-move validation — symmetric with `add_price_level_alert`) | `test_update_format_invalid` |
| AC-7 | `update_price_level_alert` accepts `new_price` on the trigger-side of `current_price` (does not warn or block immediate-trigger scenarios — see §4.3) | `test_update_immediate_trigger_allowed` |
| AC-8 | `REGISTERED_TOOL_NAMES` includes `"update_price_level_alert"` and total count is 33 | existing drift-guard test, updated expected count |
| AC-9 | Layer-1 persona bullets unchanged; bullet count still 6 | existing `tests/test_persona.py` drift guard |
| AC-10 | `add_price_level_alert` signature and behavior are unchanged in this iter (direction parameter, immediate-trigger warning, `tools_execution.py:241-273` body preserved) | code inspection in PR diff |
| AC-11 | No new Alembic migration required (alert state remains in-memory `BaseExchange._price_level_alerts`) | verified by absence of `migrations/versions/*alert*` |
| AC-12 | `_summarize_update_price_level_alert` is registered in `display.py:_EXECUTION_PARSERS` and its regex correctly extracts direction + old price + new price from the §4.2 step 7 success-return shape (with fail-soft to `_fallback_summary`) | `test_update_display_dispatch_registered` (parser sub-assertion: `_EXECUTION_PARSERS["update_price_level_alert"]("Price level alert updated (id=AAAA → id=BBBB): above 82100.00 → above 82500.00 — \"x\"")` returns a string containing `"above $82,100"` and `"$82,500"`) |
| AC-13 | `BaseExchange.add_price_level_alert` and `BaseExchange.remove_price_level_alert` are sync (not async) — pinning update's atomicity invariant from §4.2 step 4 | `test_update_atomicity_sync_invariant` |
| AC-14 | `is_tool_error("cancel_price_level_alert", idempotent-ok-return, outcome="success")` returns `False` (idempotent ok is **not** misclassified as tool error in UI/metrics) — pinning §5.1.4.3 prefix-tuple fix | `test_cancel_idempotent_not_classified_as_error` |
| AC-15 | `update_price_level_alert` is present in all three display.py dispatch structures: `_EXECUTION_TOOL_NAMES` frozenset, `_EXECUTION_PARSERS`, `_EXECUTION_SUCCESS_PREFIXES` | `test_update_display_dispatch_registered` |

---

## 7. PR plan

Single PR. Suggested task breakdown for plan stage:

1. **Task 1** — `_lookup_alert` helper in `tools_execution.py` (module-level; used by both cancel and update).
2. **Task 2** — `cancel_price_level_alert` idempotent + F-A3 reasoning surfacing.
3. **Task 3** — `update_price_level_alert` new @tool function (impl per §4).
4. **Task 4** — `trader.py:687` `REGISTERED_TOOL_NAMES` 32 → 33 (insert `"update_price_level_alert"` in execution cluster, between `cancel_price_level_alert` and the next entry) + @tool registration immediately after `cancel_price_level_alert` (`trader.py:591-614`) + both count drift-guard tests updated: `tests/test_trader_agent.py:85` (assert `== 32` → `== 33`; literal `(20+11+1)` → `(20+12+1)`) and `tests/test_display_cycle.py` four-point patch (1442 docstring / 1445 docstring / 1469 comment / **1481 `assert len(execution) == 11` → `== 12`**).
5. **Task 5** — `src/cli/display.py` execution dispatch surface — three sub-edits (see §5.1.4):
   - (a) `_EXECUTION_TOOL_NAMES` frozenset (492-504): add `"update_price_level_alert"` after `"cancel_price_level_alert"` (required for `test_dg_2` to pass — hard test crash without this).
   - (b) `_EXECUTION_PARSERS` (252-263): add `"update_price_level_alert"` → new `_summarize_update_price_level_alert(content: str) -> str` helper (note: **`content: str`**, not `args`; see §5.1.4.2 for the contract). Helper body per §5.1.4.2 pseudo-code.
   - (c) `_EXECUTION_SUCCESS_PREFIXES` (266-278): **two entries** change — `"cancel_price_level_alert"` becomes a tuple `("Price level alert cancelled", "Alert ")` (the trailing-space `"Alert "` covers idempotent ok return per §5.1.4.3); new `"update_price_level_alert"` entry maps to single string `"Price level alert updated"`. Without (c), every cancel idempotent ok is silently misclassified as tool error in UI/metrics.
6. **Task 6** — `src/services/tool_call_recorder.py:60` comment attribution update for `alert_not_found` from cancel → update (per §5.1.5). No allowlist set change.
7. **Task 7** — Test cleanup of legacy `alert_not_found` cancel semantics: **delete** `tests/test_alert_lifecycle.py:761-787` (`test_cancel_price_level_alert_not_found_records_biz_error`) — its semantic moves to `test_alert_family.py` test #1 (idempotent) and test #5 (update biz_error). Update `tests/test_v_alert_lifecycle.py:95-122` in-comment narrative for the cancel→update attribution shift (fixture rows unchanged).
8. **Task 8** — `tests/test_alert_family.py` new file with 10 tests (see §5.4): 3 cancel + 4 update behavior + 1 dispatch drift guard (3 sub-structures) + 1 sync invariant + 1 idempotent-not-error classification.
9. **Task 9** — Docstring rewrites (cancel + update) per §5.3.

Each task gets dual review (spec compliance + code quality) per subagent-driven-development discipline.

---

## 8. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| `update_price_level_alert` adds tool-count drag (32 → 33) against principle 4 | Med | §2.2 closure-value + symmetry justification (frequency justification dropped post-audit); W3 validation gate in §9 |
| Idempotent cancel masks a legitimate "cancel was a typo, alert is actually live" bug class | Very low | Format check still rejects malformed ids; legitimate cancel of an actually-active alert continues to succeed. The masked class would require an 8-char hex id that is malformed enough to coincidentally not exist — zero observed instances in sim #8 |
| Agent confused by direction preservation in update (expects to flip direction) | Low | Docstring (§4.1) names cancel + add as the correct flow for direction change; `update` is documented as price-only |
| Spec scope under-delivers on the original Iter 2 plan (drops batch form) | Low | F-T3 batch form deferred to W3 with named candidate shapes (§9); the audit pivot is documented so W3 starts from data, not from the dropped roadmap |
| F-A3 reasoning surfacing low ROI as observed in sim #8 narrative | Very low | Cost is also low (~20 lines + 2 tests); kept as a nicety |

---

## 9. Open follow-ups (post-release tracker)

- **F-T3 W3 revisit**: re-evaluate batch form for `add_price_level_alert` after W3 sim. Candidate shapes:
  - Paired lists: `levels_above: list[float] | None` + `levels_below: list[float] | None` + shared `reasoning`
  - List of dicts: `alerts: list[dict]` where each dict is `{price, direction}` + shared `reasoning`
  - Parallel arrays: `prices: list[float]` + `directions: list[Literal["above","below"]]` + shared `reasoning`
  - The decision criterion is LLM ergonomic measurement on W3 cycles, not just per-cycle call count.
- **`update_price_level_alert` validation gate (replaces frequency threshold)** — three measurable proxies:
  1. **Adoption rate**: SQL `SELECT COUNT(*) FROM tool_calls WHERE tool_name='update_price_level_alert' AND status='ok'` vs `(cancel_ok + add_ok within same cycle, where the cancelled alert's register reasoning has text-similarity > 0.5 with the added alert's reasoning, OR the cycle narrative contains 'trail'/'move')` over W3. Adoption rate = `update_count / (update_count + trail-via-cancel-add_count)`. Threshold: ≥ 50% adoption when trail is the obvious flow. The precise operational definition of "trail-via-cancel-add" (similarity threshold, narrative grep terms, cross-cycle tolerance) is finalized after W3 baseline sampling — the rough proxy above is sufficient to start observation but may need tightening once W3 data exposes edge cases (e.g., agent cancels alert A and adds alert B at unrelated levels in the same cycle — that pair must not count as trail).
  2. **Narrative legibility**: session-log grep — number of cycles containing both `update_price_level_alert` and a single trail-narrative segment (one of "trail", "move alert", "update alert"), divided by cycles containing `update_price_level_alert` at all. Higher ratio = better legibility. No firm threshold; compare with baseline W2 (where trail narrative is two segments).
  3. **Intent confusion**: session-log grep for `update_price_level_alert` followed within the same cycle by `cancel_price_level_alert` or `add_price_level_alert` (signaling the agent treated update incorrectly and corrected). Threshold: 0 instances per W3.
  - If adoption < 50% **or** confusion ≥ 1 instance, revisit removal.
- **F-A3 W3 validation**: monitor whether cancel-success narrative becomes streamlined (less re-narration of fate) after surfacing reasoning.
- **`set_price_alert` (volatility alert) alignment**: out of this iter; if W3 surfaces friction with the percent-move alert path, separate iter.
- **`v_alert_lifecycle` view trail-chain reconstruction** (per §4.2 step 8): the view sees neither side of an update — old id stays as `final_status='active'` orphan (no `cancel_price_level_alert` row), and the new id is **entirely absent** from the view (no `add_price_level_alert` row in `registers` CTE either). Remediation candidates:
  - (a) **Dual-emit `_record_action` from inside `update_price_level_alert`** — within the same coroutine, after the in-memory replace succeeds, call `_record_action` three times: `action='cancel_price_level_alert' alert_id=old_id`, `action='add_price_level_alert' alert_id=new_id`, and a third anchor `action='update_price_level_alert' alert_id=new_id` for audit. Pros: zero view change, trail chain becomes natively visible, W2/W3 cancel-add patterns look identical to the view. Cons: one user-facing tool call produces three `trade_actions` rows — analysts must group on `action='update_price_level_alert'` to find true updates, and `action='cancel_price_level_alert'` aggregations get inflated by virtual rows. Document the dual-emit convention in the view docstring + `_record_action` site comment.
  - (b) **Extend `trade_actions` schema** with `replaces_alert_id` + `replaced_by_alert_id` columns (Alembic migration) and a new view CTE that joins on these. Pros: explicit trail-chain modelling. Cons: schema change overhead, view rewrite required.
  - (c) **Parse the update audit `reasoning` string** to reconstruct the cancel side in a new `update_implied_cancels` CTE (regex on `"replaces (\w{8})"`). Pros: zero schema change. Cons: regex on free-text is brittle; depends on §4.2 step 8 reasoning format remaining stable.
  - (d) **Accept the limitation** and document it in the view docstring; provide a recipe SQL for analysts that joins `trade_actions WHERE action='update_price_level_alert'` to reconstruct chains.
  Decision deferred to W3 — needs trail-frequency data to justify dual-emit's row inflation, schema migration, or fragile regex.
- **`scripts/_sim_metrics.py:550` cancel failure rate metric**: under cancel idempotent the `cancel_price_level_alert` biz_error rate trends to zero (cancel emits no biz_error after this iter). The metric's W2-to-W3 comparison loses semantic continuity. Two candidate remediations: (a) rebase the metric to track `update_price_level_alert alert_not_found` rate (the new semantic carrier of "stale alert reference"); (b) add a separate `update_not_found_rate` metric and document the cancel rate as a deprecated W1/W2-only series. Decision deferred to W3 — confirmation that update is being used (per the W3 adoption gate above) precedes the metric refactor.
- **Cross-period `alert_not_found` attribution shift**: queries on `tool_calls.error_type='alert_not_found'` spanning W2 → W3 must note the attribution shift documented in §5.1.5 (cancel was the historical emitter; update is the new emitter). Tooling that aggregates across periods should add a `tool_name` group-by to disambiguate.

---

## 10. References

- `docs/superpowers/principles/tool-design-principles.md` — principles 1 / 4 / 5 / 6 / 8
- `.working/sim8-w2-tool-optimization-roadmap.md` §3.5.3 + §4.3 Iter 2 + §6.1
- `.working/sim8-w2-tool-ergonomics.md` — F-F1, F-A2, F-A3 data; F-T3 deferred
- Memory: `feedback_observation_period_soft_constraint` — §2 vs §3.2 boundary argument referenced in §3.3
- Memory: `project_w2_ops_backlog` — Iter 2 entry in W2 ops backlog inventory
- Memory: `persona_dead_config_decision` — OOS `adjust_leverage` deletion candidate (out of scope here)
- PR #30 (R2-1) — 0.1 *threshold_pct* floor for `set_price_alert` (volatility alert, percent-move). Distinct from price-level alert distance; **not reused** in this iter to preserve `add` ↔ `update` symmetry. If a future iter introduces a distance floor on `add_price_level_alert`, `update` should adopt the same floor (paired change).
- PR #27 (Iter 6) — `_clear_stale_alerts_for_full_close` (second root cause of F-F1)
- PR #46 (`281171d`, `7510a56`) — Iter 1 multi-TF reversal; principle 8 precedent for Layer-1 immutability
- Code locations (line ranges as of 2026-05-12):
  - `src/agent/tools_execution.py:241-273` — `add_price_level_alert` (unchanged in this iter)
  - `src/agent/tools_execution.py:276-299` — `cancel_price_level_alert` (idempotent target)
  - `src/agent/tools_perception.py:554-577` — `get_active_alerts` (F-A3 reference for already-included reasoning)
  - `src/integrations/exchange/base.py:180-215` — `add_price_level_alert` / `remove_price_level_alert` / `_check_price_levels`
  - `src/integrations/exchange/base.py:217-227` — `clear_level_alerts_by_symbol` (helper)
  - `src/integrations/exchange/base.py:244` — `_clear_stale_alerts_for_full_close` (root cause class B for F-F1; calls the helper above)
  - `src/agent/trader.py:591-614` — `cancel_price_level_alert` @tool registration; `update_price_level_alert` registration inserts immediately after (verify exact line in plan stage after the cluster edit lands)
