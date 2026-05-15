# Iter w3r1-vol-alert-agent-owned — singleton, agent-owned volatility alert (delete wizard gating)

**Date**: 2026-05-15
**Iteration**: iter-w3r1-vol-alert-agent-owned (W3 baseline iteration 1)
**Type**: Design spec (state model + tool surface + wizard/config/DB cleanup)
**Source brainstorm**: 2026-05-15 session covering volatility alert ownership transfer + tool surface unification with price-level alerts
**Upstream**: W2 sim #8 alert family observation; tool-design-principles 1 / 2 / 4 / 5 / 6 / 7 / 8
**Related principles**: 1 (fact-provider not guard) / 4 (tool count is agent decision-latency budget) / 5 (interface closure for common patterns) / 6 (failure semantics: reject vs idempotent) / 7 (label / unit clarity) / 8 (trust agent + tool surface, prompt nudge is last-resort)

---

## 0. One-minute summary

Today the volatility alert is owned by **the wizard**: a one-time human decision at session creation flips an `enabled` flag persisted to `Session.alert_config`. If the human picks OFF, `set_price_volatility_alert` is permanently dead for the entire session — the agent receives a dead-end string `"Alerts are disabled for this session. Enable alerts in wizard to use this feature."` even though the wizard is unreachable from the agent's runtime.

This iter transfers ownership to the **agent**: there is no `enabled` concept, no on/off state, no wizard step. The exchange starts with `_alert_service = None`; the agent sets one alert (singleton) via `set_price_volatility_alert(threshold, window, reasoning)`, replaces it via the same tool, or removes it via a new `cancel_price_volatility_alert(reasoning)`. Both tools follow the same idempotent failure protocol as the price-level alert family (R2-Next-E PR #47).

Surface delta: **+1 tool** (`cancel_price_volatility_alert`), bringing `REGISTERED_TOOL_NAMES` from 32 → 33. No backwards compatibility shim, no Alembic migration (volatility alert is in-memory state on `BaseExchange`; the obsolete `Session.alert_config` column becomes a dead column on existing SQLite databases — see §5.3).

The Section 1 model is the single fact-only signal `_alert_service is None`. No additional flag (`active`, `enabled`) is introduced — principle 4 (signal completion before new fields). Lazy instantiation (`_alert_service` lazily constructed on first set, dropped to `None` on cancel) keeps the cost of "agent never used it" at zero and avoids any third "instance exists but inactive" state.

---

## 1. Empirical foundations

### 1.1 Source data

- sim #8: 178 cycles / 19.2h / 1818 tool calls (DB `data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`)
- Tool-optimization tools file: `.working/tool-optimization/tools/set_price_alert.md`
- Tool-optimization backlog: `.working/tool-optimization/99-backlog.md` SP-1 + INV-11
- Recent precedent: PR #50 (`iter-tool-opt-alert-family-rename`) renamed `set_price_alert` → `set_price_volatility_alert`; PR #51 (`iter-tool-opt-alert-age`) added `created_at` + age display; PR #47 (R2-Next-E) established the price-level idempotent-cancel protocol

### 1.2 Per-issue datum table

| Issue | Datum | Source |
|---|---|---|
| Ownership inversion | sim #8: `set_price_volatility_alert` was called 4 times, all in the alert-enabled session — the alerts-disabled wizard branch (`Alerts are disabled for this session`) was NOT exercised by any sim cycle in W2 observation | `.working/tool-optimization/tools/set_price_alert.md` Snapshot |
| Surface unification | `set_price_volatility_alert` 4 calls vs `add_price_level_alert` 136 calls (ratio 1:34); semantic gap exists between them — the fact that volatility-alert is gated by wizard while price-level is not, while both share `_alert_callback` and the `get_active_alerts` display, is one root cause of the asymmetry | `.working/tool-optimization/99-backlog.md` SP-1 + INV-11 |
| Lazy create cost | `PriceAlertService.__init__` (`src/services/price_alert.py:26-37`) is pure-memory: 4 float comparisons (validation), 4 attribute assignments, one empty `deque()`. No file / network / DB / lock / subprocess / module-singleton dependency. Sub-microsecond, < 0.01% of any tool-call dispatch overhead | `src/services/price_alert.py` direct read |
| Symmetry with price-level | Price-level alert: `_price_level_alerts: list[dict]` initial `[]`, agent fills via `add_price_level_alert`. Volatility alert (post-iter): `_alert_service: PriceAlertService \| None` initial `None`, agent fills via `set_price_volatility_alert`. Both initial-empty containers, both agent-driven population | `src/integrations/exchange/base.py:108` and §3 of this spec |
| Wizard cleanup scope | wizard.py 14 line refs / session_manager.py 10 line refs / config.py 6 line refs / models.py 1 line ref / app.py 14 line refs (already in `if result.alert_enabled:` block) | grep `alert_config|alert_enabled|alert_window|alert_threshold|AlertsConfig` |
| Test fanout | 13 test files reference `PriceAlertService`, `set_alert_service`, `update_alert_params`, or wizard alert fields | grep across `tests/` |

### 1.3 Implication

The empirical strength is **structural**, not high-frequency-hand-calc:

- The ownership inversion is justified by principle 8 (trust agent + tool surface, not human prior decisions affecting tool availability). The dead-end string `"Enable alerts in wizard"` is meaningless to an agent for which the wizard is not a reachable surface — this is a categorical violation of "fact-provider not guard" (principle 1) on a path that the agent cannot recover from.
- The surface unification with price-level is justified by principle 5 (interface closure: same lifecycle pattern across the two alert families).
- The Lazy choice over Eager is justified empirically by zero resource dependency in `PriceAlertService.__init__` and by principle 4 (signal completion: `_alert_service is None` is the single fact signal — no `active` flag is needed).

The hand-calc / narrative bar (principle 2) does not apply to this iter because no factual signal is being added. The iter is a state-model and surface-symmetry change, not a new fact.

---

## 2. Architecture and scope

### 2.1 State model

```
exchange._alert_service: PriceAlertService | None
                          ↑
                    None = unset (initial / post-cancel)
                    Set  = agent has configured exactly 1 alert
```

- Singleton: at most one volatility alert configuration at any time.
- No on/off concept, no `enabled` / `active` flag.
- Lazy: `_alert_service` is constructed on the first `set_price_volatility_alert`; on `cancel_price_volatility_alert` it returns to `None`.
- In-memory only: session restart resets to `None` (symmetric with price-level alerts which also do not persist runtime state).

### 2.2 Lifecycle

| Event | State transition |
|---|---|
| `build_services` (session start) | `_alert_service = None` |
| Agent first `set_price_volatility_alert(t, w, ...)` | `_alert_service = PriceAlertService(symbol, w, t)` |
| Agent subsequent `set_price_volatility_alert(t', w', ...)` | `_alert_service.update_params(t', w')` — replaces parameters AND clears the rolling tick window (existing reset semantics in `PriceAlertService.update_params`) |
| Agent `cancel_price_volatility_alert(...)` | `_alert_service = None` |
| Tick → if `_alert_service:` `.check(price, ts)` triggers | `AlertInfo` → `_alert_callback` → `scheduler.trigger("alert", context=...)` (existing path, unchanged) |
| Session restart | `_alert_service = None` (no persistence layer reads back) |

### 2.3 Issue → change matrix

| Issue ID | Surface | Change |
|---|---|---|
| **OWN-1** | `BaseExchange` API | Replace `set_alert_service` + `update_alert_params` with `set_volatility_alert(threshold_pct, window_minutes, symbol)` (lazy create / update) and `cancel_volatility_alert()` (clear to `None`). `get_alert_params` retained unchanged. |
| **OWN-2** | `tools_execution.set_price_volatility_alert` | Delete the `if get_alert_params() is None: return "Alerts are disabled..."` branch. Wire to new `exchange.set_volatility_alert(...)`. Success message distinguishes create vs replace. |
| **OWN-3** | `tools_execution` (new) | Add `cancel_price_volatility_alert(reasoning)` with idempotent semantics (matches `cancel_price_level_alert` protocol from R2-Next-E PR #47). |
| **OWN-4** | `tools_perception.get_active_alerts` | Volatility section unset path: `"OFF"` → `"Not set"`. |
| **OWN-5** | `trader.py` | Update `set_price_volatility_alert` wrapper docstring (creates-or-replaces semantic + reset side-effect). Register `cancel_price_volatility_alert`. Add to `REGISTERED_TOOL_NAMES`. |
| **OWN-6** | `cli/wizard.py` | Delete `_step_risk_scheduling` alert sub-prompts; delete summary `Alerts` row; delete `WizardResult.alert_enabled / alert_window_min / alert_threshold_pct`. |
| **OWN-7** | `src/config.py` | Delete `AlertsConfig` class and `Settings.alerts` field. |
| **OWN-8** | `cli/session_manager.py` | Delete read/write paths for `alert_config`; delete `("alert_config", "TEXT")` from `_migrate_session_table` migrations list. |
| **OWN-9** | `cli/app.py` | Delete the `if result.alert_enabled: alert_service = PriceAlertService(...); exchange.set_alert_service(...)` block (`L915-928`); delete the unused `PriceAlertService` import; delete the `Alerts: ON / OFF` banner line. |
| **OWN-10** | `storage/models.py` | Delete `Session.alert_config` mapped column (L51). New SQLite databases will not have the column; existing databases retain it as a dead column (§5.3). |

---

## 3. Tool surface contracts (agent-facing)

### 3.1 `set_price_volatility_alert(threshold_pct, window_minutes, reasoning)`

**Signature** (unchanged from current):

```python
async def set_price_volatility_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    reasoning: str,
) -> str
```

**Behavior**:

1. Range validation: `0.1 ≤ threshold_pct ≤ 50.0` and `1 ≤ window_minutes ≤ 240`. Out-of-range → reject + `note_biz_error("invalid_threshold_range")` (existing protocol retained).
2. Call `deps.exchange.set_volatility_alert(threshold_pct, window_minutes, deps.symbol)`. Internally:
   - If `_alert_service is None` → lazy construct `PriceAlertService(deps.symbol, window_minutes, threshold_pct)`.
   - Else → call `_alert_service.update_params(threshold_pct, window_minutes)` (existing `PriceAlertService` method, which clears `_ticks` deque as a side effect).
3. `_record_action(deps, action="set_price_volatility_alert", reasoning=...)` (unchanged).
4. Return string distinguishes create vs replace path so the agent can recognize the side effect on the rolling window:
   - Create (was `None`): `"Price volatility alert set: threshold=2.0%, window=30min"`
   - Replace (was Set with `(was_t, was_w)`): `"Price volatility alert replaced: threshold=2.0%, window=30min (was 5.0%/60min, rolling window reset)"`

**Failure semantics**:

| Condition | Result |
|---|---|
| `threshold_pct` out of `[0.1, 50.0]` | Reject + biz_error `"invalid_threshold_range"` |
| `window_minutes` out of `[1, 240]` | Reject (no biz_error noted — pre-existing asymmetry inherited from `tools_execution.py:240-241`; not addressed in this iter to avoid scope creep) |
| Otherwise | Always succeeds (no "already exists" reject — `set` is by definition idempotent over creation) |

**Removed**: the dead-end branch `if deps.exchange.get_alert_params() is None: return "Alerts are disabled..."` (`tools_execution.py:232-234`) is deleted in its entirety.

**Wrapper docstring** (`trader.py`):

```
Set the price volatility alert (singleton). Creates if none is configured;
otherwise replaces the existing one — replacing resets the rolling tick
window, so the next trigger requires re-accumulating ticks across the full
window from scratch. Use cancel_price_volatility_alert to remove without
setting a new one.

Args:
    threshold_pct: alert threshold percent (0.1-50).
    window_minutes: time window in minutes (1-240).
    reasoning: brief description of your decision logic.
```

### 3.2 `cancel_price_volatility_alert(reasoning)` — new

**Signature**:

```python
async def cancel_price_volatility_alert(
    deps: TradingDeps,
    reasoning: str,
) -> str
```

**Behavior**:

1. Read current params via `deps.exchange.get_alert_params()`.
2. Already-unset path (`params is None`) — return `"No volatility alert active to cancel."` immediately without calling `cancel_volatility_alert` and without `_record_action`. Matches `cancel_price_level_alert` (R2-Next-E PR #47) which also short-circuits before mutation and audit on the not-found path; principle 6 ("ok with note" for state-not-found, no audit row for a no-op).
3. Set path (`params is not None`) — call `deps.exchange.cancel_volatility_alert()` (which sets `_alert_service = None`); call `_record_action(deps, action="cancel_price_volatility_alert", reasoning=...)`; return `"Price volatility alert cancelled (was {t}%/{w}min)"`.

**Failure semantics**:

| Condition | Result |
|---|---|
| Already unset | Idempotent ok with note (no biz_error, no reject) |
| Set | Clear + return cancellation confirmation |

There are no protocol-level validation steps (no `alert_id` parameter, no format check) because the singleton has no identifier.

**Wrapper docstring** (`trader.py`):

```
Cancel the active price volatility alert. Idempotent: if no alert is set,
returns ok with a note. Use set_price_volatility_alert to configure a new
one.

Args:
    reasoning: brief description of your decision logic.
```

### 3.3 `get_active_alerts` — text adjustment only

The volatility section unset case (`tools_perception.py:621`):

```python
# Before
sections.append(f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\nOFF")

# After
sections.append(f"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\nNot set")
```

Rationale: `OFF` carries an "on/off switch" connotation that no longer matches the model. `Not set` aligns with the price-level "No active alerts." text — both convey "empty container, agent may populate."

Set case (`L619`) is unchanged: `"=== Price Volatility Alert (@ {fetch_ts} UTC) ===\n{threshold}% in {window}min window"`.

### 3.4 Failure semantics consolidated table

| Tool | Protocol-level reject | State-not-found | State-already-exists |
|---|---|---|---|
| `set_price_volatility_alert` | Range out of bounds | (creates) | (replaces; window reset noted in success string) |
| `cancel_price_volatility_alert` | (none) | Idempotent ok with note | (clears) |
| `add_price_level_alert` (unchanged) | direction / cap-20 | n/a | n/a |
| `cancel_price_level_alert` (unchanged) | id format | Idempotent ok with note | (clears) |
| `update_price_level_alert` (unchanged) | id format | Reject (not found) | (in-place update) |

The volatility cancel protocol matches the price-level cancel protocol byte-for-byte. The volatility set has no `not-found` concept because singleton-creation collapses the create / update distinction (principle 5 — single tool covers the full closure).

---

## 4. Backend wiring

### 4.1 `src/integrations/exchange/base.py`

**Add import** (top of file):

```python
from src.services.price_alert import PriceAlertService
```

(`price_alert.py` only imports `collections` and `dataclasses` from stdlib — no circular dependency risk.)

**Replace API** (lines around 186-198):

```python
# DELETE
def set_alert_service(self, service): ...
def update_alert_params(self, threshold_pct, window_minutes): ...

# ADD
def set_volatility_alert(
    self,
    threshold_pct: float,
    window_minutes: int,
    symbol: str,
) -> None:
    """Lazy-create on first call, update_params on subsequent calls.
    Replacing parameters resets the rolling tick window."""
    if self._alert_service is None:
        self._alert_service = PriceAlertService(symbol, window_minutes, threshold_pct)
    else:
        self._alert_service.update_params(threshold_pct, window_minutes)

def cancel_volatility_alert(self) -> None:
    """Clear the singleton; subsequent ticks no longer evaluate volatility."""
    self._alert_service = None

# RETAIN unchanged
def get_alert_params(self) -> tuple[float, int] | None:
    if self._alert_service is not None:
        return self._alert_service.get_params()
    return None
```

**Type tightening**: `self._alert_service: Any | None = None` (L110) → `self._alert_service: PriceAlertService | None = None`.

### 4.2 `src/integrations/exchange/simulated.py` and `okx.py`

Zero changes. The tick-path defensive check is already correct:

```python
# simulated.py:699-700, okx.py:296-299 — unchanged
if self._alert_service:
    alert_info = self._alert_service.check(ticker.last, ticker.timestamp)
```

The `_alert_callback` registration and dispatch path are likewise unchanged.

### 4.3 `src/cli/app.py`

Delete `L915-928` (the entire `if result.alert_enabled: ... else:` block). Remove the unused `PriceAlertService` import. The handler registration `exchange.on_alert(handle_alert)` (`L1045-1047`) is unchanged — the callback wiring is independent of whether an alert is configured.

---

## 5. Wizard / config / DB cleanup

### 5.1 `src/cli/wizard.py`

| Location | Change |
|---|---|
| L37-39 (`WizardResult`) | Delete `alert_enabled / alert_window_min / alert_threshold_pct` fields |
| L250-260 (`_step_risk_scheduling`) | Delete `Confirm.ask("Price alerts")` and the nested `IntPrompt`/`FloatPrompt` for window/threshold; delete the three keys from the returned dict |
| L321-327 (`_show_summary`) | Delete the `table.add_row("Alerts", ...)` row |

### 5.2 `src/config.py`

| Location | Change |
|---|---|
| L59-62 (`AlertsConfig` class) | Delete |
| L93 (`Settings.alerts` field) | Delete |
| `config/settings.yaml` (project root) | Delete the `alerts:` section if present (silently ignored at runtime once the `Settings` field is gone, but keeping the file clean prevents reader confusion) |

### 5.3 DB schema — dead-column strategy for `Session.alert_config`

**Decision**: drop the column from the SQLAlchemy model and from `_migrate_session_table`'s migration list. **Do not** introduce a new Alembic migration to physically drop the column.

**Rationale**:

- `Session.alert_config` was never managed by Alembic — it was added via the ad-hoc `_migrate_session_table` PRAGMA + `ALTER TABLE ADD COLUMN` mechanism in `src/cli/session_manager.py:21-34`. There is no Alembic precedent for column drops in this project.
- SQLite did not support `ALTER TABLE DROP COLUMN` until 3.35 (2021); adding a "create new table, copy, swap" Alembic migration just for a column with no data integrity meaning would introduce ~50 lines of migration boilerplate the project has never used before, for zero functional benefit.
- The dead column on existing SQLite databases occupies near-zero storage (NULL values) and is invisible to all post-iter code (no model field, no read path). Future refactors that decide to do a full schema rebuild can drop it then.

**Concrete changes**:

| Location | Change |
|---|---|
| `src/storage/models.py:51` | Delete the `alert_config` mapped column line |
| `src/cli/session_manager.py:30` | Delete `("alert_config", "TEXT"),` from the `migrations` list in `_migrate_session_table` |
| `src/cli/session_manager.py:137-145` | Delete the `if s.alert_config: alert_data = json.loads(...)` block |
| `src/cli/session_manager.py:189-191` | Delete the three `alert_*` fields from `WizardResult(...)` construction |
| `src/cli/session_manager.py:215-222` | Delete the `if result.alert_enabled: alert_config = json.dumps(...)` block (whole block including the variable initialization) |
| `src/cli/session_manager.py:239` | Delete `alert_config=alert_config` from the `Session(...)` constructor kwargs |

---

## 6. Test plan

### 6.1 Modifications to existing tests (13 files)

| Category | Files | Action |
|---|---|---|
| Wizard / Session field cleanup | `test_wizard.py` (~14 line refs) / `test_session_manager.py` (~10 line refs) / `test_storage.py` (1 line ref) / `test_n3_wiring.py` (3 line refs) / `test_okx_algo_normalization.py` (1 line ref) | Delete `alert_enabled`, `alert_window_min`, `alert_threshold_pct`, `alert_config`, `AlertsConfig` references; delete parameterized "alerts disabled vs enabled" branches (one branch per affected test fixture) |
| DB schema | `test_alembic_migration.py:63` (`alert_config TEXT` in expected schema) / `test_storage.py:230` (`assert s.alert_config is None`) | Delete |
| Base API drift guard | `test_tool_enhancement.py:80-86` (direct `set_alert_service` / `update_alert_params` calls) and `:164-177` (subclass-no-override drift guard) | Replace `set_alert_service` / `update_alert_params` references with the new `set_volatility_alert` / `cancel_volatility_alert` methods. The drift guard pattern itself is retained. |
| Execution tool tests | `test_tools.py:352-390` (4 cases) and `test_tool_enhancement.py:729-761+` (5 cases) | Change mock target from `update_alert_params` to `set_volatility_alert`; delete `test_set_price_volatility_alert_disabled` (the disabled-in-wizard branch is gone); add cancel test suite (see §6.2) |
| `PriceAlertService` unit | `test_price_alert.py` | **Zero changes** (the service's internal API `check / update_params / get_params` is unchanged) |
| Integration / scenario | `test_simulated_exchange.py` / `test_okx_websocket.py` / `test_alert_lifecycle.py` / `test_trader_agent.py` / `test_session_state.py` / `test_tool_call_recorder.py` / `test_display_cycle.py` / `test_exchange.py` / `test_fact_only_wordlist.py` | Per-file audit. Tests that injected via `exchange.set_alert_service(svc)` switch to `exchange.set_volatility_alert(t, w, symbol)` (lazy path); wizard fixtures drop alert fields. |

### 6.2 New tests

**`test_tools.py`** — agent-facing volatility tool surface:

| Test | Asserts |
|---|---|
| `test_set_price_volatility_alert_creates_when_none` | First call constructs `PriceAlertService`; success string contains `"set:"` (not `"replaced:"`); `get_alert_params()` returns the configured tuple |
| `test_set_price_volatility_alert_replaces_when_exists` | Second call invokes `update_params`; success string contains `"replaced:"`, `"was {old}"`, `"rolling window reset"`; `get_alert_params()` returns new tuple |
| `test_cancel_price_volatility_alert_when_active` | `_alert_service` becomes `None`; success string contains `"was {t}%/{w}min"`; `_record_action` called once |
| `test_cancel_price_volatility_alert_when_none_idempotent` | Returns `"No volatility alert active to cancel."`; does not raise; no biz_error noted; `_record_action` NOT called (no-op short-circuit, matches `cancel_price_level_alert` not-found path) |
| `test_get_active_alerts_volatility_section_when_unset` | Section text contains `"Not set"` (and explicitly does not contain `"OFF"`) |
| `test_get_active_alerts_volatility_section_when_set` | Section text contains `"{threshold}% in {window}min window"` |

**`test_tool_enhancement.py`** — base-layer drift guards:

| Test | Asserts |
|---|---|
| `test_base_set_volatility_alert_lazy_creates_when_none` | `_alert_service` transitions from `None` to `PriceAlertService` instance; constructed with the passed `(symbol, window, threshold)` |
| `test_base_set_volatility_alert_updates_when_exists` | `_alert_service` instance preserved; `update_params` called with new args; `_ticks` cleared |
| `test_base_cancel_volatility_alert_clears_to_none` | `_alert_service` → `None` |
| `test_simulated_does_not_override_set_volatility_alert` | `SimulatedExchange.set_volatility_alert is BaseExchange.set_volatility_alert` (drift guard) |
| `test_okx_does_not_override_set_volatility_alert` | Symmetric drift guard for OKX |
| `test_simulated_does_not_override_cancel_volatility_alert` | Drift guard |
| `test_okx_does_not_override_cancel_volatility_alert` | Drift guard |

### 6.3 Tests removed

| Test | Reason |
|---|---|
| `test_set_price_volatility_alert_disabled` (`test_tool_enhancement.py:729`) | Disabled-in-wizard branch is gone; the dead-end string `"Enable alerts in wizard to use this feature"` no longer exists in the codebase |
| `test_restore_session_null_alert_config` (`test_session_manager.py:110`) | Field no longer exists on `Session` |
| Any wizard parameterized "alerts: OFF" branch | Two-state model is gone; OFF is no longer a wizard outcome |

### 6.4 Verification strategy

**Repo-wide grep guard** (post-implementation, expected zero hits except `test_price_alert.py`'s own `update_params` unit tests):

```bash
grep -rn "alert_config\|alert_enabled\|alert_window\|alert_threshold\|set_alert_service\|update_alert_params\|AlertsConfig" tests/ src/
```

**Pytest baseline drift**:

- Baseline: 1694 passed (post PR #54)
- Expected delta: removed ~8 cases (disabled / null-config branches) + added ~13 cases (cancel + lazy create + drift guards) → net +5 ± 3 → expected 1697-1702 passed
- Acceptance: the test count moves in the expected direction; any unexpected failures are investigated, not suppressed

**Manual smoke** (sim session):

1. Launch a fresh sim session via wizard. Assert: no `Price alerts` prompt appears in `_step_risk_scheduling`; no `Alerts` row in summary; no `Alerts: ON / OFF` banner in app startup.
2. From a running session, agent calls `set_price_volatility_alert(2.0, 30, "test")`. Assert: `get_active_alerts` shows `2.0% in 30min window`; success string contains `"set:"`.
3. Agent calls `set_price_volatility_alert(1.5, 60, "tighter")`. Assert: success string contains `"replaced:"` and `"rolling window reset"`.
4. Agent calls `cancel_price_volatility_alert("done")`. Assert: `get_active_alerts` shows `Not set`; success string contains `"was 1.5%/60min"`.
5. Agent calls `cancel_price_volatility_alert("again")` immediately after. Assert: `"No volatility alert active to cancel."`; no exception.
6. Restart the session. Assert: `get_active_alerts` shows `Not set` (no persistence carry-over).

---

## 7. Out of scope

| Item | Reason |
|---|---|
| Persistence of agent-set volatility-alert config across session restart | Q1 explicitly did not select "solve runtime persistence loss" as a driver; price-level alerts are also non-persistent — this iter preserves symmetry, leaves persistence as a separable W3+ candidate if narrative supports it |
| Persona / system-prompt changes nudging agent toward / away from setting volatility alerts | Principle 8 — surface change is the lever; prompt nudge is last-resort |
| `cycle_capture` / `trigger_context` schema changes | `type="percentage_alert"` 6-field payload (`cycle_capture.py:64-74`) is unchanged — when an alert triggers, the AlertInfo carries the same fields whether the alert was set by wizard or by agent |
| Price-level alert protocol audit | R2-Next-E PR #47 + iter-tool-opt-alert-age PR #51 are the canonical price-level work; this iter only adopts the cancel protocol pattern, does not modify it |
| Re-introducing `enabled` as a runtime toggle distinct from create/cancel | Explicitly rejected in §1 brainstorm Q2 ("no on/off state") — adding it back would re-introduce the asymmetry this iter exists to remove |
| Multi-volatility-alert (multiple windows / thresholds simultaneously) | Explicitly rejected in §1 brainstorm Q2 ("only one") — the singleton constraint is part of the design contract |
| Alembic migration to physically drop `Session.alert_config` column | §5.3 — dead-column strategy chosen over schema-rebuild dance |
| Performance benchmark of lazy create | §1.2 datum: `PriceAlertService.__init__` is sub-microsecond pure-memory; benchmark would yield no actionable signal |

---

## 8. W3 follow-up candidates (not in this iter)

| Candidate | Trigger condition |
|---|---|
| Persist agent-set volatility-alert config across session restart | W3+ sim narrative ≥ 2 occurrences of agent re-establishing the same volatility-alert config after a restart, or explicit complaint that the rolling window had to re-warm |
| `get_active_alerts` showing how long the volatility alert has been active (parallel to price-level age) | W3+ narrative requesting the equivalent of `created_at` for the singleton |
| Surfacing the time of last trigger in `get_active_alerts` | W3+ narrative around "did this alert just fire, am I seeing a stale tick?" |
| Tool-call counter watch on `set_price_volatility_alert` and `cancel_price_volatility_alert` | If aggregate calls drop further from sim #8's 4-per-19h, signal that the surface is too high-friction even after ownership transfer; if calls spike above ~1 per cycle, signal possible agent over-fiddling and a surface that needs a different shape (e.g., default config + tune-only) |
