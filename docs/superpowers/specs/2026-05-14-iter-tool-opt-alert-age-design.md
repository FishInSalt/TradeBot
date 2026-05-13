# Iter tool-opt-alert-age — alert `created_at` + age display + update id-stability amend

**Date**: 2026-05-14
**Iteration**: iter-tool-opt-alert-age (Sprint 4, iter-11 of tool-optimization roadmap)
**Type**: Design spec (in-memory schema add + R2-Next-E §4.4/§4.5 amend)
**Source brainstorm**: 2026-05-14 session covering AL-1 + AA-4 + the latent R2-Next-E update id-stability question
**Upstream**: `.working/tool-optimization/02-execution-roadmap.md` §2 iter-11 + `.working/tool-optimization/99-backlog.md` §3.6 AL-1 + AA-4
**Related principles**: `docs/superpowers/principles/tool-design-principles.md` — 1 (fact-only) / 4 (tool count) / 6 (failure semantics) / 7 (output friendliness) / 8 (trust agent + tool surface)

---

## 0. One-minute summary

The alert family carries one missing fact and one latent design seam:

- **Missing fact (AL-1 + AA-4)**: alert dicts on `BaseExchange._price_level_alerts` carry `id` / `price` / `direction` / `symbol` / `reasoning` but no creation timestamp. `get_active_alerts` therefore cannot show *when* an alert was set. Sim #8 cycle 32babac6 narrative — "the alert might auto-clear when the position closes? Let me check…" — surfaces an information-completeness gap rather than a high-frequency hand-calc pattern. The driver is information-completeness for a high-frequency tool (`add_price_level_alert` 136 calls / `get_active_alerts` 38 calls in sim #8), not a principle 2 ≥3-times hand-calc trigger.
- **Latent seam (R2-Next-E §4.4 / §4.5 amend)**: `update_price_level_alert` (PR #47) was implemented as `remove + add`, which mints a fresh `id` (per R2-Next-E §4.5) and preserves the original `reasoning` as an "identity continuity" carrier (per §4.4). Brainstorm review found both decisions misaligned with the literal `update` semantic: a stable `id` *is* the identity carrier, and `reasoning` is a description field that should follow the agent's evolving understanding. The `created_at` design question forced this seam into view because field-level reset on `update` only makes coherent sense once the dict is updated in-place rather than rebuilt.

This spec lands all three changes in one iter:

1. Alert dict gains `created_at: float` (epoch sec, set via `time.time()`).
2. `BaseExchange.update_price_level_alert(alert_id, new_price, new_reasoning) -> bool` performs an in-place update of `price` + `reasoning` + `created_at`; `id` + `direction` + `symbol` are preserved. The tool layer (`tools_execution.update_price_level_alert`) wires through to this single method, dropping the `_lookup_alert + remove + add` sequence.
3. `get_active_alerts` appends a humanized age suffix to each level-alert line via a new `_fmt_age_humanized(seconds) -> str` helper.

`REGISTERED_TOOL_NAMES` stays at **34**. No Alembic migration: alerts remain in-memory state on `BaseExchange`. Layer-1 persona is untouched (principle 8 — name + tool surface are the levers).

The R2-Next-E `cancel_price_level_alert` idempotent behavior, `_lookup_alert` helper, and reasoning surfacing on cancel success are **unchanged**. The only R2-Next-E amend surface is the `update` path.

---

## 1. Empirical foundations

### 1.1 Source data

- sim #8: 178 cycles / 19.2h / 1818 tool calls (DB `data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`)
- Tool-optimization backlog: `.working/tool-optimization/99-backlog.md` §3.6 (AL-1 + AA-4 entries)
- Roadmap: `.working/tool-optimization/02-execution-roadmap.md` §2 iter-11

### 1.2 Per-issue datum table

| Issue | Datum | Source |
|---|---|---|
| AL-1 + AA-4 | sim #8 cycle 32babac6 narrative explicitly questions alert lifecycle ("alert might auto-clear when the position closes?") — surfaces missing-time-anchor concern, not a hand-calc count | sim #8 narrative grep |
| AL-1 + AA-4 | `add_price_level_alert` called 136 times, `get_active_alerts` called 38 times in sim #8 — high-frequency surface where missing time anchor is paid every read | sim #8 tool_calls aggregation |
| R2-Next-E §4.4/§4.5 amend | `update_price_level_alert` (PR #47, `28f7265`) implementation: `_lookup_alert + remove + add` mints a new id every update; original `reasoning` carried forward as identity proxy | `src/agent/tools_execution.py:357-435` |
| R2-Next-E §4.4/§4.5 amend | Latent collateral: `v_alert_lifecycle` view does not see either side of an update (old id has no cancel row in `cancels` CTE; new id has no add row in `registers` CTE) — R2-Next-E §4.2 step 8 documented this as a known limitation deferred to W3 | `src/storage/views.py:94-149` + R2-Next-E spec §9 view-trail-chain follow-up |

### 1.3 Implication

The empirical strength for AL-1 + AA-4 alone is **moderate** — one narrative occurrence is below the principle 2 ≥3-times-hand-calc bar. The justification rests on information-completeness for high-frequency surfaces plus principle 7 (label / unit / window decoration on a fact-bearing field). The amend leg of this spec (R2-Next-E §4.4/§4.5) is justified independently by literal-semantic alignment of `update`, not by sim frequency.

---

## 2. Architecture and scope

### 2.1 Issue → change matrix

| Issue ID | Surface | Change |
|---|---|---|
| AL-1 | `BaseExchange.add_price_level_alert` (`src/integrations/exchange/base.py:190-200`) | alert dict gains `created_at: float` set via `time.time()` |
| R2-Next-E amend | `BaseExchange` (new method) | `update_price_level_alert(alert_id: str, new_price: float, new_reasoning: str) -> bool` — in-place mutation; returns True if found, False otherwise |
| R2-Next-E amend | `tools_execution.update_price_level_alert` (`src/agent/tools_execution.py:357-435`) | Drop `_lookup_alert + remove + add` sequence. Call `BaseExchange.update_price_level_alert` once; on True, build success return with the unchanged `id` and the updated fields; on False, emit `biz_error: alert_not_found` (behavior preserved). `_record_action` records `alert_id={alert_id}` (no transition syntax). |
| R2-Next-E amend | `tools_execution.update_price_level_alert` return string | `f'Price level alert updated (id={alert_id}): {direction} {old_price:.2f} → {new_price:.2f} — "{new_reasoning}"'` (single id, direction once on the LHS since direction is invariant on update, new reasoning) |
| R2-Next-E amend | `src/cli/display.py:_summarize_update_price_level_alert` | Regex amended: drop the double-direction / `id → id` pattern; match the new shape `r"(above\|below)\s+([\d.]+)\s*→\s*([\d.]+)"` |
| AA-4 helper | `src/agent/tools_perception.py` (new module-level fn) | `_fmt_age_humanized(seconds: float) -> str` |
| AA-4 render | `get_active_alerts` (`src/agent/tools_perception.py:582-584`) | Compute `now = time.time()` once outside the loop; each level-alert line appends ` ({_fmt_age_humanized(now - a["created_at"])})` |

### 2.2 Tool count invariant

`REGISTERED_TOOL_NAMES` stays at **34** (20 perception + 13 execution + 1 memory). `add/cancel/update_price_level_alert` cluster unchanged.

### 2.3 Scope boundary

**In-scope**:
- alert dict schema (1 new field) + 1 new `BaseExchange` method + 1 new render helper
- `update_price_level_alert` tool layer rewrite (in-place) + display dispatch regex amend
- New drift-guard tests (`tests/test_alert_age.py`) + amendments to existing R2-Next-E tests (`tests/test_alert_family.py`)
- Docstring update on `update_price_level_alert` tool (Args + behavior text now matches in-place semantics)

**Out-of-scope**:
- DB schema migration (alerts remain in-memory; `_price_level_alerts` lifecycle is per-process)
- `add_price_level_alert` return string augmentation (creation-time age = `just now` carries no information)
- `set_price_volatility_alert` age (single-param tuning; no schema-level "creation moment" semantic)
- `cancel_price_level_alert` changes — R2-Next-E idempotent + reasoning-surface behavior preserved
- `_lookup_alert` helper deletion — still used by `cancel_price_level_alert` per R2-Next-E §3.5
- `v_alert_lifecycle` view edits — id-stability makes the view's trail-chain naturally connected (collateral fix); no CTE rewrite required
- Layer-1 persona / system prompt edits (principle 8)
- Cross-tool `Related:` docstring lines (out of this iter family per R2-Next-E §2.3)

### 2.4 Principle reconciliation

- **Principle 1 (fact-only)**: `created_at` is an event-time fact (`time.time()` at creation/update); `age` is a derived observation fact (`now - created_at`). No advisory framing.
- **Principle 4 (tool count)**: Net-zero — no new tools registered. The amend reuses existing `update_price_level_alert` name.
- **Principle 6 (failure semantics)**: `update` on a not-found id continues to reject (`biz_error: alert_not_found`) per R2-Next-E §3.3 — `update` of nonexistent state is semantically unfulfillable, distinct from `cancel`'s idempotent state-already-resolved case.
- **Principle 7 (output friendliness)**: age suffix carries label-and-window decoration on the level-alert row (`(2h 15m ago)`). The header already carries `(@ HH:MM:SS UTC)` per b31ffc3 sweep; header is the read time, body suffix is the create/update time — two independent anchors, no conflict.
- **Principle 8 (trust agent + tool surface)**: no Layer-1 nudge added. The `update` tool docstring is updated to match the new in-place semantic; that's the lever.

---

## 3. R2-Next-E §4.4 / §4.5 amend rationale

### 3.1 What the original spec said

R2-Next-E spec (`docs/superpowers/specs/2026-05-12-iter-w2r2-next-e-alert-family-design.md`) §4.4 + §4.5 made two coupled decisions:

1. **§4.5**: `update` mints a new id. Reason given: "Re-using the old id would require API surgery in the integration layer with no observable benefit." The `Updated (id=X → id=Y)` syntax in the return string was framed as making the rebinding explicit.
2. **§4.4**: Original `reasoning` is preserved across update — "the underlying why typically does not change — the structural level shifts slightly, but the intent persists." Direction-change or reasoning-change flows are routed to `cancel + add`.

### 3.2 Why each was misaligned

**§4.5 (new id mint)** — the "API surgery" framing overstates the cost. An in-place update is a 10-line method on `BaseExchange` that scans `_price_level_alerts` and mutates one dict. The "no observable benefit" framing misses three downstream effects:

1. Literal `update` semantic: the agent's mental model for `update` is "this same thing now has different field values." A mint-new-id implementation forces the agent to learn an idiosyncratic semantic ("update means replace-with-new-id"), which is a docstring-tax not a name-tax.
2. `v_alert_lifecycle` view trail-chain disconnection (R2-Next-E §4.2 step 8) — old id appears as `final_status='active'` orphan and new id is entirely absent from the view's `registers` CTE. The original spec deferred this as a W3 follow-up; in-place update makes the chain naturally connected without any view edit.
3. `tool_calls` cross-sim aggregation (R2-Next-E §9 cross-period attribution shift follow-up) — id-stable update lets analytics group by `alert_id` across `add` / `update` rows without bridging `alert_id_new` ↔ `alert_id_old`.

**§4.4 (reasoning preservation)** — the "intent persists" framing assumes a specific use case (level-trail) and projects it onto the universal semantic. In practice the `update_price_level_alert` signature has accepted `reasoning` as a parameter all along (R2-Next-E §4.2 step 7), but routed it only to the audit trail, not to the alert dict. That is a parameter that the tool accepts and quietly discards — a principle 1 violation in shape (agent passes a fact, the tool does not honor it in the data layer).

The agent's reasoning *can* materially evolve as price unfolds — "R1 level" → "R1 level confirmed after rejection at 82,100." Forcing the agent through `cancel + add` to capture an evolved rationale duplicates the trail call when `update` is the obvious shape.

### 3.3 The new semantic

`update_price_level_alert(alert_id, new_price, new_reasoning)` performs three in-place writes (`price`, `reasoning`, `created_at`) and leaves three fields alone (`id`, `direction`, `symbol`). The only "you cannot update this" field is `direction`, and the reason is that `above` ↔ `below` is a kind-change, not a value-change — `above 82,000` and `below 82,000` are functionally different alerts at the same level. The tool docstring spells this out: "to flip direction, cancel and add a new alert."

### 3.4 What is preserved from R2-Next-E

- `cancel_price_level_alert` idempotent behavior (§3.1-3.5)
- `cancel_price_level_alert` F-A3 reasoning surfacing on success (§3.5)
- `_lookup_alert` helper at `tools_execution.py:289-299`
- `update_price_level_alert` rejection on not-found (§3.3 / AC-5)
- `update_price_level_alert` rejection on format-invalid `alert_id`
- `update_price_level_alert` immediate-trigger acceptance (the new price may be on the trigger side of current; agent handles this deliberately per R2-Next-E §1.4)
- Layer-1 persona untouched

---

## 4. Data contract

### 4.1 Alert dict schema (after this iter)

```python
{
    "id":          str,    # 8-char hex (uuid.uuid4()[:8]); STABLE across update
    "price":       float,  # mutable via add (creation) and update (in-place)
    "direction":   str,    # "above" | "below"; IMMUTABLE post-creation
    "symbol":      str,    # IMMUTABLE
    "reasoning":   str,    # mutable via update (in-place overwrite)
    "created_at":  float,  # epoch sec; refreshed to time.time() on update
}
```

New keys vs current: `created_at` only.

### 4.2 Update behavior matrix

| Field | On `add_price_level_alert` | On `update_price_level_alert` | Rationale |
|---|---|---|---|
| `id` | minted via `uuid.uuid4()[:8]` | **preserved** | id-stability; literal `update` semantic |
| `price` | set to caller arg | overwritten with `new_price` | core purpose of update |
| `direction` | set to caller arg | **preserved** (no update path) | above ↔ below is a kind-change; flip via cancel + add |
| `symbol` | set from `deps.symbol` | **preserved** | session-bound; not an updatable concept |
| `reasoning` | set to caller arg | overwritten with `new_reasoning` | description field follows agent's evolving rationale |
| `created_at` | set to `time.time()` | **reset to `time.time()`** | field-level reset aligns with "this set of facts as of now" semantics |

### 4.3 `created_at` semantics

`created_at` reads as "as of when does this set of field values reflect the agent's intent." On `add`, that is creation time. On `update`, that is the moment the agent restated the alert with new values. This aligns with the brainstorm decision in `MEMORY.md > project_observation_period_metrics_review_checklist`-adjacent reasoning: when the dict's facts are rewritten in-place, the dict's "as-of" timestamp moves with them.

The age suffix `now - created_at` therefore answers "how long has this set of facts been live," which is the read the agent needs when looking at `get_active_alerts` to decide whether to keep, trail, or cancel.

---

## 5. Components affected

### 5.1 `BaseExchange` (`src/integrations/exchange/base.py`)

Two changes on the existing class:

#### 5.1.1 `add_price_level_alert` — add `created_at` to the dict

```python
# Current (base.py:190-200)
def add_price_level_alert(self, price: float, direction: str,
                           symbol: str, reasoning: str) -> str | None:
    if len(self._price_level_alerts) >= 20:
        return None
    alert_id = str(uuid.uuid4())[:8]
    self._price_level_alerts.append({
        "id": alert_id, "price": price, "direction": direction,
        "symbol": symbol, "reasoning": reasoning,
    })
    return alert_id

# After
def add_price_level_alert(self, price: float, direction: str,
                           symbol: str, reasoning: str) -> str | None:
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

Module top adds `import time` if not already present (verify in plan stage).

#### 5.1.2 New method `update_price_level_alert`

Placed immediately after `remove_price_level_alert` (`base.py:202-207`):

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

### 5.2 `tools_execution.update_price_level_alert` (`src/agent/tools_execution.py:357-435`)

Rewritten to use the new in-place path. Format validation and not-found rejection are unchanged from R2-Next-E behavior; only the success path differs.

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

    # Step 2: lookup direction + old_price for the return string
    # (the new_reasoning is the caller's arg; old_reasoning is not needed)
    alert = _lookup_alert(deps.exchange, alert_id)
    if alert is None:
        note_biz_error("alert_not_found")
        return (
            f"Alert {alert_id} not found. "
            f"To create a new alert, use add_price_level_alert."
        )
    direction = alert["direction"]
    old_price = alert["price"]

    # Step 3: in-place update
    ok = deps.exchange.update_price_level_alert(alert_id, new_price, reasoning)
    if not ok:
        # Defensive: lookup just succeeded; in-place update should not fail.
        raise RuntimeError(
            f"update_price_level_alert returned False for id={alert_id} "
            f"that was just present in lookup — invariant violated"
        )

    # Step 4: audit row — single alert_id; reasoning records the move.
    await _record_action(
        deps, action="update_price_level_alert",
        alert_id=alert_id,
        reasoning=f"price {old_price} → {new_price} | {reasoning}",
    )

    # Step 5: success return
    return (
        f"Price level alert updated (id={alert_id}): "
        f"{direction} {old_price:.2f} → {new_price:.2f} "
        f'— "{reasoning}"'
    )
```

Notes:

- The `_lookup_alert` helper at `tools_execution.py:289-299` is reused; the lookup is still needed to (a) reject not-found before mutation and (b) capture `direction` + `old_price` for the success return.
- The wrapper docstring on `trader.py` (R2-Next-E surface) is updated in lockstep to remove the "preserves the original direction and reasoning" framing.

### 5.3 `tools_perception._fmt_age_humanized` + `get_active_alerts` rendering

#### 5.3.1 `_fmt_age_humanized` helper

New module-level function in `src/agent/tools_perception.py`, placed alongside `_bars_ago_fmt` / `_htf_ago_fmt` for locality:

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

Boundary expectations (parametrize in test §6.2):

| `seconds` input | Output |
|---:|---|
| 0 | `just now` |
| 30 | `just now` |
| 59 | `just now` |
| 60 | `1m ago` |
| 119 | `1m ago` |
| 3599 | `59m ago` |
| 3600 | `1h 0m ago` |
| 7259 | `2h 0m ago` |
| 86399 | `23h 59m ago` |
| 86400 | `1d 0h ago` |
| 172800 | `2d 0h ago` |
| -5 | `just now` (negative clamps) |

#### 5.3.2 `get_active_alerts` rendering

Existing block at `tools_perception.py:578-587`:

```python
# Current
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

After:

```python
import time  # if not already at module scope; verify in plan stage

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

`now` captured once outside the loop so all rows use the same baseline (avoid sub-second jitter rendering inconsistent ages for siblings).

### 5.4 `src/cli/display.py` — `_summarize_update_price_level_alert` regex amend

R2-Next-E §5.1.4.2 introduced this helper. Its regex matches the original return shape (`above 82100.00 → above 82500.00`); the new shape collapses the second `direction` token. Update the regex:

```python
# Before (R2-Next-E §5.1.4.2)
m = re.search(r"(above|below)\s+([\d.]+)\s*→\s*(above|below)\s+([\d.]+)", content)
if m:
    return f"{m.group(1)} ${float(m.group(2)):,.0f} → ${float(m.group(4)):,.0f}"

# After
m = re.search(r"(above|below)\s+([\d.]+)\s*→\s*([\d.]+)", content)
if m:
    return f"{m.group(1)} ${float(m.group(2)):,.0f} → ${float(m.group(3)):,.0f}"
```

`_EXECUTION_SUCCESS_PREFIXES["update_price_level_alert"]` stays `"Price level alert updated"` (the prefix is unchanged in the new return shape).

### 5.5 Wrapper docstring on `trader.py`

The `@tool` wrapper for `update_price_level_alert` (added in PR #47) carries language about preserving direction and reasoning. Rewrite in lockstep with §5.2:

```text
Update an existing price level alert in place: change its trigger price and
reasoning. The direction (above/below) cannot change — to flip direction,
cancel and add a new alert. The alert's id stays the same.
```

Exact line range to be confirmed in plan stage (the registration sits immediately after `cancel_price_level_alert` at `trader.py:591-614` per R2-Next-E §7 Task 4).

---

## 6. Test strategy

### 6.1 New test file: `tests/test_alert_age.py`

| Test name | What it asserts |
|---|---|
| `test_add_price_level_alert_stores_created_at` | After `add`, the alert dict has `created_at` close to `time.time()` (abs delta ≤ 1s) |
| `test_update_price_level_alert_is_in_place` | After `update`, the same alert dict object remains in `_price_level_alerts` (id stable) |
| `test_update_price_level_alert_overwrites_price_and_reasoning` | After `update`, `price == new_price` and `reasoning == new_reasoning` |
| `test_update_price_level_alert_resets_created_at` | mock `time.time()` to return monotonically increasing values; assert post-update `created_at` > pre-update `created_at` |
| `test_update_price_level_alert_keeps_direction` | `direction` unchanged after update (even when `new_price` crosses the current-price boundary) |
| `test_update_price_level_alert_not_found_returns_false` | `BaseExchange.update_price_level_alert` returns False on unknown id; `_price_level_alerts` unchanged |
| `test_update_tool_emits_biz_error_alert_not_found` | tool layer on missing id emits `biz_error: alert_not_found` and returns directive text |
| `test_update_tool_return_string_shape` | regex match the new shape: `r"^Price level alert updated \(id=[0-9a-f]{8}\): (above\|below) [\d.]+ → [\d.]+ — \".+\"$"` |
| `test_fmt_age_humanized_thresholds` | parametrize per the §5.3.1 boundary table |
| `test_fmt_age_humanized_negative_clamps_to_just_now` | negative input → `just now` |
| `test_get_active_alerts_renders_age_suffix` | with 1 active alert, output line matches `r' \((?:just now\|\d+m ago\|\d+h \d+m ago\|\d+d \d+h ago)\)$'` |
| `test_get_active_alerts_age_uses_single_now_baseline` | mock `time.time()` to advance 0.5s between calls; assert all rendered ages derive from the same baseline (one `time.time()` call per render) |

### 6.2 Existing R2-Next-E tests to amend (`tests/test_alert_family.py`)

| Test | Current behavior | New behavior | Action |
|---|---|---|---|
| `test_update_success_preserves_direction_and_reasoning` (line 160) | Asserts `id=a3f2b8c1 → id=d7c2e9f4` transition; asserts old reasoning preserved | Asserts `id=a3f2b8c1` single id; asserts new reasoning overwrites old; asserts direction preserved | Rename to `test_update_success_overwrites_price_and_reasoning_keeps_direction_and_id`; rewrite assertions; remove the `remove_price_level_alert` / `add_price_level_alert` mock plumbing and assert `update_price_level_alert` was called once with `(alert_id, new_price, new_reasoning)` |
| `test_update_not_found_rejects` (line 207) | Asserts `biz_error: alert_not_found`; asserts `remove_price_level_alert` and `add_price_level_alert` were not called | Asserts same biz_error; asserts `update_price_level_alert` was called once and returned False | Update the mock-not-called assertions to reference the new method |
| `test_update_format_invalid` (line 247) | Asserts `biz_error: invalid_alert_id_format` on non-hex id | Unchanged | Verify exchange method `update_price_level_alert` was not called |
| `test_update_immediate_trigger_allowed` (line 278) | Asserts new price on trigger side is accepted without block | Unchanged in spirit; rewire mocks to the new method | Update the mock surface |
| `test_update_display_dispatch_registered` (line 329) | Asserts parser regex matches `id=X → id=Y` shape | Asserts parser regex matches the new shape (`id=X` single, no `→ id=Y`) | Update the regex assertion + the input fixture |
| `test_update_atomicity_sync_invariant` (line 392) | Asserts `BaseExchange.add_price_level_alert` and `BaseExchange.remove_price_level_alert` are sync (pins R2-Next-E §4.2 step 4 "no yield points") | No longer relevant — update is one method call | **Delete** this test; replace conceptually with `test_update_in_place_single_write` in §6.1 (covered by `test_update_price_level_alert_is_in_place`) |
| `test_update_view_known_orphan_limitation` (line 409) | Asserts the `v_alert_lifecycle` view's known limitation under R2-Next-E (old id orphan, new id absent) | id-stability resolves the limitation; trail chain becomes naturally connected via stable `alert_id` | **Rewrite** to `test_update_view_chain_connected_after_id_stability`: assert that for `add → update → cancel` on the same `alert_id`, the view's `registers` CTE has the add row, the `cancels` CTE has the cancel row, and they join cleanly by `alert_id` (no orphan branch) |

### 6.3 Existing sweep — verify (no anticipated change, but plan-stage audit required)

- `tests/test_tool_enhancement.py` — `test_get_active_alerts_with_alerts` and adjacent: alert fixture may need `created_at` populated; rendered-output assertions may need age suffix accommodation. Plan stage confirms exact lines.
- `tests/test_display_cycle.py` — `test_snapshot_get_active_alerts_with_alerts` (R2-Next-E §3.3 anchor): fixture and expected block both need age suffix accommodation. If the existing test uses byte-equal snapshot, switch the alert-row assertions to regex per principle "avoid wall-clock-flaky byte-equal." Plan stage confirms exact lines after the iter-10 sectioning is freshly seated.
- `tests/test_alert_lifecycle.py` — audit for any assertion that assumes id-transition or absence of `created_at` field. No specific known breakages, but the audit closes the loop.

### 6.4 Time mocking pattern

All tests touching `created_at` or `_fmt_age_humanized` mock `time.time` via `monkeypatch.setattr("src.integrations.exchange.base.time.time", ...)` and `monkeypatch.setattr("src.agent.tools_perception.time.time", ...)` as appropriate. No reliance on wall-clock; all flake risk eliminated at the test boundary.

---

## 7. Acceptance criteria

| AC | Statement | Verification |
|---|---|---|
| AC-1 | `BaseExchange.add_price_level_alert` writes a `created_at: float` field on the alert dict, value within `[time.time() - 1, time.time()]` at the call site | `test_add_price_level_alert_stores_created_at` |
| AC-2 | `BaseExchange.update_price_level_alert(alert_id, new_price, new_reasoning)` in-place writes `price` / `reasoning` / `created_at`; preserves `id` / `direction` / `symbol`; returns True | `test_update_price_level_alert_is_in_place` + `_overwrites_price_and_reasoning` + `_keeps_direction` + `_resets_created_at` |
| AC-3 | `BaseExchange.update_price_level_alert` on unknown id returns False; `_price_level_alerts` unchanged | `test_update_price_level_alert_not_found_returns_false` |
| AC-4 | `tools_execution.update_price_level_alert` success returns `f'Price level alert updated (id={alert_id}): {direction} {old_price:.2f} → {new_price:.2f} — "{new_reasoning}"'` with the preserved `id`, preserved `direction`, new `price`, and new `reasoning` | `test_update_tool_return_string_shape` |
| AC-5 | `tools_execution.update_price_level_alert` on not-found emits `biz_error: alert_not_found` (R2-Next-E behavior preserved) | `test_update_tool_emits_biz_error_alert_not_found` |
| AC-6 | `_fmt_age_humanized` correctly switches format at the 60s / 3600s / 86400s boundaries per §5.3.1 table | `test_fmt_age_humanized_thresholds` (parametrized) |
| AC-7 | `_fmt_age_humanized` clamps negative input to `just now` | `test_fmt_age_humanized_negative_clamps_to_just_now` |
| AC-8 | `get_active_alerts` appends an age suffix to each level-alert line matching `r' \((?:just now\|\d+m ago\|\d+h \d+m ago\|\d+d \d+h ago)\)$'` | `test_get_active_alerts_renders_age_suffix` |
| AC-9 | `get_active_alerts` renders all level-alert rows using a single `now` baseline captured once per render | `test_get_active_alerts_age_uses_single_now_baseline` |
| AC-10 | `display.py:_summarize_update_price_level_alert` regex matches the new return shape (single direction token) | updated R2-Next-E `test_update_display_dispatch_registered` |
| AC-11 | `v_alert_lifecycle` view connects `add → update → cancel` cleanly via stable `alert_id` (no orphan branch) | `test_update_view_chain_connected_after_id_stability` |
| AC-12 | Layer-1 persona bullets unchanged (still 6); `REGISTERED_TOOL_NAMES` count unchanged (still 34) | existing drift guards in `tests/test_persona.py` + `tests/test_trader_agent.py` |
| AC-13 | No Alembic migration introduced for alerts (alerts remain in-memory state) | `find migrations/versions -name '*alert*'` returns no new file |

---

## 8. PR plan

Single PR on branch `iter-tool-opt-alert-age`. Suggested task breakdown for plan stage:

1. **Task 1** — `BaseExchange.add_price_level_alert` writes `created_at`; `BaseExchange.update_price_level_alert` new method (in-place).
2. **Task 2** — `tools_execution.update_price_level_alert` rewrite: drop `_lookup_alert + remove + add` sequence; wire to `BaseExchange.update_price_level_alert`; new return string shape; updated `_record_action` reasoning text.
3. **Task 3** — `trader.py` wrapper docstring for `update_price_level_alert` rewrite in lockstep.
4. **Task 4** — `tools_perception._fmt_age_humanized` helper + `get_active_alerts` rendering changes; capture `now` once per render.
5. **Task 5** — `display.py:_summarize_update_price_level_alert` regex amend.
6. **Task 6** — New `tests/test_alert_age.py` (12 tests per §6.1).
7. **Task 7** — Amend `tests/test_alert_family.py` per §6.2 (7 tests: 5 rewrites + 1 delete + 1 substitute).
8. **Task 8** — Audit `tests/test_tool_enhancement.py`, `tests/test_display_cycle.py`, `tests/test_alert_lifecycle.py` per §6.3; fix what surfaces.

Each task gets dual review (spec compliance + code quality) per subagent-driven-development discipline.

---

## 9. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Wall-clock-dependent tests flake on slow CI when age crosses a threshold mid-test | Medium | All time-sensitive tests use `monkeypatch.setattr` on `time.time` at the module-import boundary (both `src.integrations.exchange.base` and `src.agent.tools_perception`); no test depends on real wall-clock |
| `get_active_alerts` byte-equal snapshot tests fail because of dynamic age | Medium | Switch level-alert row assertions to regex per §6.3 audit; fixture-based tests use mocked `time.time` |
| R2-Next-E `update` behavior change breaks downstream consumers outside the agent (CLI history, future analytics) | Low | sim datasets are observation-only (per `project_r2_8b_legacy_decision_restore_boundary`); CLI dispatch updated in §5.4; no other consumers exist |
| Cross-sim aggregation analytics see a transition point in update return shape | Low | New shape is strictly simpler (one id, one direction); analytics that parsed the old shape can fall back to the new shape with a minor regex change. No analytics are known to depend on the old shape today |
| `BaseExchange.update_price_level_alert` returning False after a `_lookup_alert` succeeded indicates a real invariant violation | Low | Defensive `raise RuntimeError` in §5.2 step 3; covered by no specific test (defensive path, not happy-path) |
| Layer-1 persona inadvertently references `id` transition or the old return shape | Very low | grep confirms Layer-1 / system prompt do not reference these surfaces |

**Rollback unit**: single feature branch → single PR → single `git revert <merge-commit>`. Spec and plan land as commits before impl (per `feedback_plan_doc_commit_first`); revert removes impl, leaves spec/plan as historical reference.

---

## 10. Open follow-ups

| Item | Why deferred |
|---|---|
| `add_price_level_alert` return string augmentation (age) | Creation-time age is `just now` by construction — no information. If a future surface needs it (e.g., delayed-confirmation flow), revisit |
| `set_price_volatility_alert` age | Single-param tuning has no "creation moment" semantic worth surfacing; no narrative gap observed |
| R2-Next-E §9 `v_alert_lifecycle` view trail-chain follow-up (candidates a/b/c/d) | Resolved by id-stability in this iter; remove the follow-up entry from R2-Next-E §9 after merge |
| R2-Next-E §9 `scripts/_sim_metrics.py:550` cancel failure rate metric | Independent of id-stability; still tracked under R2-Next-E §9 |
| R2-Next-E §9 cross-period `alert_not_found` attribution shift | Independent of this iter; still tracked under R2-Next-E §9 |
| W3 adoption monitoring for `age` field consumption | If sim narrative does not reference age within `get_active_alerts` output across 1+ W3 sim, evaluate whether the suffix is dead weight (principle 4 — output column friction is selection latency proxy) |

---

## 11. References

- `.working/tool-optimization/02-execution-roadmap.md` §2 iter-11
- `.working/tool-optimization/99-backlog.md` §3.6 (AL-1 + AA-4 entries)
- `docs/superpowers/specs/2026-05-12-iter-w2r2-next-e-alert-family-design.md` (R2-Next-E source spec; §4.4 / §4.5 / §9 amended by this iter)
- `docs/superpowers/specs/2026-05-13-iter-tool-opt-alert-family-rename-design.md` (iter-10 sibling; sectioning anchors this iter renders into)
- `docs/superpowers/principles/tool-design-principles.md` — principles 1 / 4 / 6 / 7 / 8
- Memory: `project_r2_8b_legacy_decision_restore_boundary` (legacy sim DB rows are not query-path migrated)
- Memory: `feedback_plan_doc_commit_first` (spec/plan commit before impl)
- Memory: `feedback_docs_no_inline_changelog` (no inline changelog / self-review sections)
- Code locations (line numbers as of 2026-05-14, branch `iter-tool-opt-alert-age`):
  - `src/integrations/exchange/base.py:190-200` — `add_price_level_alert` (AL-1 add `created_at`)
  - `src/integrations/exchange/base.py:202-207` — `remove_price_level_alert` (unchanged; new `update_price_level_alert` inserts after)
  - `src/agent/tools_execution.py:289-299` — `_lookup_alert` (unchanged)
  - `src/agent/tools_execution.py:302-354` — `cancel_price_level_alert` (R2-Next-E behavior preserved)
  - `src/agent/tools_execution.py:357-435` — `update_price_level_alert` (rewritten per §5.2)
  - `src/agent/tools_perception.py:578-587` — `get_active_alerts` level-alert rendering (AA-4 add age suffix)
  - `src/cli/display.py:_summarize_update_price_level_alert` (R2-Next-E §5.1.4.2; regex amended per §5.4)
- Sim data: `data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3` (sim #8)
