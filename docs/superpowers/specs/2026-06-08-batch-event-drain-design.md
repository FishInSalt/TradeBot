# Batch event drain — one cycle consumes all pending events

**Date**: 2026-06-08
**Type**: medium iter (scheduler + agent-loop mechanism) → PR workflow
**Status**: design approved, pending impl

## Problem

When the scheduler wakes, it pops events **one at a time** and runs a **full separate agent
cycle per event** (`src/scheduler/scheduler.py:67-75`):

```python
for _ in range(min(len(self._pending_events), 10)):
    if not self._running or not self._pending_events:
        break
    event = heapq.heappop(self._pending_events)
    await self._run_cycle(event.trigger_type, event.context)   # one event → one full cycle
```

The `min(…, 10)` is **not** batching — it caps how many *separate* cycles run back-to-back.
So when N events have piled up (e.g. during a 1–3 min cycle, in fast markets), the system runs
N consecutive full agent cycles, each reacting to **one** event, and the later cycles process
events that are already minutes stale. Events are never dropped (the rest wait for the next
sleep), but each gets its own expensive reasoning pass, and the agent reacts myopically to one
slice of what is really a single market move.

This is the structural cause behind the symptom the wake-event-timestamp iter (`187873c`)
band-aided at the prompt layer (telling the agent "this alert is 4.6 min old" so it stops
misreading stale events as fresh). Per tool-design principle 8 (*trust agent + tool over prompt
nudge; nudge is last-resort*), the structural fix is to stop feeding the agent stale events
one-by-one at all — drain the whole pending set into **one** cycle at the freshest moment and
let the agent reason holistically.

### Empirical grounding (session `f670abe1` / BTC sim #15, 384 cycles, 2.5 days)

- **65% of cycles are event-driven**: alert 166, conditional/fill 84, scheduled 134.
- **Cycle wall time** p50 **102s** / p90 141s / p95 153s (avg 179s inflated by one ~5.8h
  clamshell-sleep outlier). Confirms the "1–3 min per cycle" premise.
- **Bursts** (consecutive cycles each starting <10s after the prior ended — i.e. drained
  back-to-back with no sleep): of 287 wakes, **78 (27%) drained ≥2 events**; depth distribution
  2→61, 3→15, 4→2; **max depth 4**.
- **97 cycles (25% of all 384)** are the "extra" 2nd/3rd/4th cycles of a burst — exactly what
  this change collapses into the first cycle of each burst. At ~82k tokens/cycle that is
  ~8M tokens/session of redundant reasoning, plus wall time.
- **Staleness when processed**: burst 2nd+ (blocked) events lag avg **153s** / max **350s
  (5.8 min)** from fire→cycle-start. Lag buckets across all event cycles: 1–3min 79%,
  **3–5min 17%, 5–10min 3%**.
- Documented harm (wake-event-timestamp spec): cycle `bf84ca93` called a **4.6-min-old** alert
  "just triggered."

> **Caveat — N=1 session, and it predates PR #70 (sync market fill, merged 2026-06-08).**
> `f670abe1` is a large sample (384 cycles / 2.5 days, burst rate robust within it) but one
> market regime. Bursts are **alert-driven** (alert 166 ≫ conditional 84), and alerts are
> unaffected by #70; the conditional/fill *frequency* is on a stale population (#70 folds market
> fills into the placing cycle, dropping the fill-triggered share ~22%→~10%). Core conclusion —
> bursts are frequent and event staleness is real — does not depend on the fill count. Re-verify
> burst rate on the first post-#70 session (`feedback_data_mismatch_old_impl_inference`).

## Design

### §1 — Core mechanism (`src/scheduler/scheduler.py`)

The drain loop becomes a **snapshot-drain into a list**, consumed by **one** cycle:

```python
if self._pending_events:
    events: list[tuple[str, Any]] = []
    while self._pending_events and len(events) < 20:
        ev = heapq.heappop(self._pending_events)        # heap is already priority-ordered
        events.append((ev.trigger_type, ev.context))
    deferred = len(self._pending_events)                 # leftover after the cap == heap depth
    if deferred > 0:                                     # ⟺ started with strictly >20
        logger.warning(
            "event drain capped: drained=%d deferred=%d total=%d types=%s",
            len(events), deferred, len(events) + deferred, _type_counts(events),
        )
    await self._run_cycle(events)                        # ONE cycle consumes the batch
else:
    await self._run_cycle([("scheduled", None)])         # degenerate: scheduled tick
```

> The bootstrap call before the loop (`scheduler.py:58`, `await self._run_cycle("scheduled",
> None)`) is a real second call site and also changes to the list form
> `await self._run_cycle([("scheduled", None)])`.

- **Callback contract** changes from `Callable[[str, Any|None], Awaitable[None]]` to
  `Callable[[list[tuple[str, Any]]], Awaitable[None]]`. `_run_cycle(events)` and the
  `on_tick`/`run_agent_cycle` call chain update accordingly. The callback **always receives a
  non-empty list** (scheduled path passes `[("scheduled", None)]`), eliminating an empty-list
  branch. `_TriggerEvent` stays internal — only `(trigger_type, context)` tuples cross the
  boundary.
- **No loop-safety cap needed.** The drain is fully synchronous (no `await` between `heappop`s),
  so events produced *during* the subsequent cycle land on the next wake, not this one — the
  original infinite-loop risk that motivated `min(…, 10)` is gone.
- **Cap = 20, semantics = defer (not drop) + warn.** The heap is priority-ordered (conditional 0
  → alert 1, FIFO within), so a fill is always inside the first 20; only the lowest-priority,
  oldest alerts are ever deferred, and they are processed on the **immediately following** wake
  (`_interruptible_sleep` returns at once while the heap is non-empty — no sleep between
  back-to-back drains). 20 is a guard threshold (5× the observed max of 4), not a tuning knob;
  it should essentially never bite in legitimate operation.
- **Warning predicate = `deferred > 0`** (heap non-empty *after* the drain), **never**
  `len(events) == 20` — at exactly 20 pending, drained=20/deferred=0 and no warning must fire
  (boundary false-positive trap). `total` is computed as `drained + deferred` because the heap
  has already been consumed down to the leftover.
- `set_next_interval` / `_interruptible_sleep` / `_cycle_running` / `trigger` unchanged.

### §2 — Prompt assembly (`src/cli/app.py`, priority-sectioned)

The current per-event prompt blocks (lines 528-573: conditional / price-level / percentage)
are extracted into one **`async _render_event_block(deps, trigger_type, context,
cycle_started_at) -> str`** helper. It is **async with IO** — the full-close fill branch awaits
`deps.exchange.get_contract_size(context.symbol)` and reads `deps.fee_rate` (`app.py:540-542`;
symbol comes from `context.symbol`, not `deps`), so `deps` is a required parameter (the earlier
`(trigger_type, context, cycle_started_at)` signature was wrong). `run_agent_cycle` awaits it per
event, iterating the list in priority order.

- **N == 1 (73% of wakes): the prompt is byte-identical to today.** The single-event *prompt*
  header (`You have been woken up by a {trigger_type} trigger…`) and the one event block are
  emitted exactly as now, preserving the just-tuned wake-event-timestamp (`187873c`) wording with
  zero regression. This guarantee is about the **prompt the agent sees** — *not* the session-log
  `Trigger` Header line, which deliberately changes per §3 (the human-facing Header drops event
  detail in favor of Context). This is the common path and the prompt must not drift.
- **N > 1** uses a multi-event header:
  `You have been woken up by 3 triggers (1 fill, 2 alerts) since the last cycle.` followed by
  the fill block(s) first, then alert block(s) — i.e. heap pop order — each carrying its own
  `_wake_time_suffix` age clause. This preserves the deliberate "fill must not be buried under
  stale alerts" intent (`scheduler.py:13-14`) as *presentation order*, and aligns with
  principle 7 (sectioning over flat alignment).
- `triggered_by` (the single DB column) = the **highest-priority type** in the batch
  (conditional > alert > scheduled), matching the header's lead. Existing `GROUP BY triggered_by`
  analysis keeps working (a batched cycle counts as its dominant type).
- `cycle_id` (`app.py:503`), `trigger_context` + `state_snapshot` (`app.py:506-511`), and
  `user_prompt_snapshot` (`app.py:588`) capture stay once-per-cycle, before the retry loop (current
  `§6.7 capture-once` invariant, unchanged). `trigger_context` capture is now the list form (§3).

### §3 — Data model & observability

- **`trigger_context` becomes a JSON array.** New rows are **always** an array (single-event
  cycles are a 1-element array; scheduled is `[{"type":"scheduled_tick"}]`) for uniform reads.
  Legacy single-object rows coexist; readers handle both. Column type is already `Text`
  (`models.py:104`) — **no migration, no new column** (event count is
  `json_array_length(trigger_context)`, per the YAGNI decision).
- **`_capture_trigger_context` (`cycle_capture.py:24`)** keeps its single-event mapping as an
  inner helper and adds a list form `_capture_trigger_contexts(events) -> list[dict | None]` that
  maps over the batch. Per-event capture stays best-effort: a failing event records `None` in its
  slot without sinking the batch. **No all-fail special-case** — if every event fails the map
  naturally yields `[None, None, …]`, which preserves the event count, never mislabels the type
  (`triggered_by` still carries the real dominant type), and persists fine (cycle is never lost).
  (An earlier *draft* of this design proposed a `[{"type":"scheduled_tick"}]` all-fail fallback —
  **never present in current code**, do not grep for it — but it was self-contradictory: it labeled
  an alert/conditional batch as scheduled and dropped the count, so it is dropped from the design.)
- **`v_alert_lifecycle` view (`views.py:87-110`) must be updated.** It currently does
  `json_extract(trigger_context, '$.alert_id')` + `WHERE triggered_by='alert'`, both of which
  break under the new model. Rewrite the `triggers` CTE to:
  1. **Unnest with `json_each`**, normalizing legacy *object* rows first —
     `json_each(CASE WHEN json_type(trigger_context)='array' THEN trigger_context
     ELSE json_array(json(trigger_context)) END)`. **Required**: bare `json_each('{…}')` on an
     object iterates it *by key* (one row per field), polluting the result — the `json_array()`
     wrap makes a legacy object a single element.
  2. **Drop the `triggered_by='alert'` clause** — a price-level alert batched with a fill has
     `triggered_by='conditional'`, so that clause would silently drop it. Filter per-element on
     `json_extract(value, '$.type')='price_level_alert' AND … '$.alert_id' IS NOT NULL` instead.
  3. **All per-element reads come from `json_each.value`, not the row column** — this includes the
     projected `triggered_price` (currently `json_extract(trigger_context, '$.current_price')`,
     `views.py:106`) and `$.alert_id`, not just the `$.type` filter. Easy to miss.
  4. Update the stale `json_extract($.direction)` example in the header comment (lines 88-90) to
     the array-aware form.
- **`display.py` session-log rendering — corrected, and event detail moves Header → Context.**
  The Header `Trigger` line and the Context `Woke by` line **already duplicate** the event
  description today (Header = compact summary via `_format_trigger_detail`; Context = verbatim from
  the prompt). Batching exposes this (the Header can't fit N events). Resolution: **Header keeps
  only the type label + count; Context owns the full per-event detail.** Three sub-paths:
  - **Header `Trigger` line** (`_format_trigger_detail`, **`display.py:779`**) is **reduced to
    type + count** — `ALERT` / `CONDITIONAL` / `SCHEDULED` for N==1, and `CONDITIONAL  +2 (1 fill,
    2 alerts)` for N>1. The current per-event detail branches (fill `position_side/amount/
    fill_price/PnL`, alert summary, the T-EH-3 partial-degradation logic) are **removed** — that
    detail now lives only in Context (no info loss). The signature accepts the new
    `ctx: list[dict|None] | dict | None` and computes the count/breakdown from the list. This fixes
    a real crash: today `_format_trigger_detail` does `ctx.get("type")` (`:790`) — a list input
    raises `AttributeError`. **`format_cycle_output` (`:1338-1418`) has NO `try`** around its
    `_render_header` call (`:1344`, the renderer's first line), so the exception propagates out of
    the *entire* renderer to `on_tick`'s `except` (`app.py:1088`), which logs a misleading
    **"Agent cycle failed"** even though the cycle already committed (`app.py:798`) and succeeded —
    the DB row survives, only the session-log block is lost. (The `try` at `display.py:1482` is in
    the unrelated `summarize_tool`, not `format_cycle_output`.) The fix is *simpler* than the
    original "lossy summary" plan. Defensive: also wrap the `trigger_line` computation so
    `_render_header` degrades gracefully like `_render_context`.
  - **`▾ Context` `Woke by`** (`_render_context`, fail-isolated via the `:1153` try; the `Woke by`
    line is emitted at `:1160`) derives from `user_prompt_snapshot` (prompt text) via
    `_extract_event_line` (`:1008`). Change it to render
    **per-event bullets**: split `wake_half` at each `_EVENT_PREFIXES` occurrence into N segments,
    emit one `• …` line per event, each truncated **individually** (`_CONTEXT_EVENT_CAP = 500`
    `:985` now applies per event, not to one concatenated blob — this resolves the multi-event
    truncation cleanly). N==1 keeps the current single `Woke by — …` form.
  - **`_extract_scheduled_wake_suffix` is unchanged** — only reached on the scheduled branch
    (`:1164`), and scheduled is always N==1 (never batched).
- **Observability caveat — a cycle is no longer a single-event unit** (write into the spec, à la
  `feedback_data_mismatch_old_impl_inference`). Batching collapses ~25% of cycles (the burst 2nd+
  events — see Empirical grounding), so the **cycle count itself drops** post-batch. Consequences
  for cross pre-batch vs post-batch comparison:
  - **Any per-cycle-denominated metric is incomparable across the boundary** — cycles/day,
    avg tokens/cycle, wake/cycle ratio, and the 5-field decision per-cycle adoption rates all shift
    purely from the denominator change, not behavior. Compare on **event-denominated or
    time-denominated** bases instead (e.g. per-event, per-day), or restrict comparisons within one
    regime.
  - **`triggered_by` distribution** specifically: a batched alert co-occurring with a fill no
    longer counts as an `alert` cycle (`triggered_by` = dominant type;
    `triggered_by_distribution` `scripts/_sim_metrics.py:590` GROUP BY). `diff_sim` of
    `triggered_by[*]` across the boundary shows batching-induced pseudo-differences; compare on the
    unnested event array, not the cycle label.

### §4 — Edge cases & failure semantics

- **Spike vs persistent backlog.** A one-time >20 spike (legit flash move) defers the excess and
  clears across the immediately-following back-to-back drains (20/cycle, no sleep between) — a
  25-event spike = 2 cycles (20-array + 5-array), ~2–6 min. A *persistent* producer storm
  (source outpaces drain) never clears: WARNING fires **every** cycle and the logged `deferred`
  (== post-drain heap depth) keeps rising. The two diagnostics are distinct — occasional single
  WARNING = handled spike; sustained per-cycle WARNING with growing `deferred` = anomaly →
  triggers layer 2 (below).
- **Overflow is not blinding.** The event list is a trigger/context layer, not the agent's only
  input — every cycle re-grounds on live market state (`state_snapshot` + `get_market_data`).
  A deferred stale alert describes a price the agent already observes from live data, so dropping
  it from *this* batch is immaterial to the decision.
- **Memory bound — accepted, not closed.** Defer-only means the heap is theoretically unbounded
  under a persistent producer bug. Accepted for now because: (a) never observed (max 4); (b) the
  WARNING gives early signal and the observation-phase operator can kill/restart (human in loop);
  (c) the first real WARNING is the trigger to build layer 2. See follow-up backlog.
- **`set_next_wake_at` is overridden while a backlog exists** (pre-existing, not a regression):
  `_interruptible_sleep` returns immediately when the heap is non-empty (`scheduler.py:92-93`), so
  the agent's chosen wake interval is ignored until the backlog drains. The old per-event path
  already behaved this way; batching just makes multi-event drains more common. Noted to avoid
  later confusion — no change.
- Cycle-level failures, retry, and `last_active_at` update (`on_tick` finally block,
  `app.py:1090-1101`) are per-cycle and unchanged.

### §5 — Testing

This is a **large mechanical migration**, not a 3-test touch — the callback contract change
ripples across 9 test files. Plan must budget for it explicitly:

- **`tests/test_scheduler.py` (~full rewrite of the event paths).** All **13** `callback`
  definitions use the old 2-arg `async def callback(trigger_type, context)` and break under the
  list contract. The 5 multi-call ordering tests assert *per-event callback invocations* and must
  be reframed as *one callback call + ordering within the list*: `_priority_then_fifo` (`:124`),
  `_priority_conditional_over_alert` (`:160`), `_fifo_within_same_priority` (`:200`),
  `_context_not_lost_on_multiple_triggers` (`:230`), `_preserves_trigger_type` (`:101`). Also
  `_trigger_merges_multiple_events` (`:43`), `_safety_valve_max_drain` (`:256`),
  `_event_preempts_scheduled` (`:282`). **`_scheduler_drain_respects_stop` (`:307`, not 282) must
  be deleted/rewritten** — its premise (stop mid-drain skips later events) is negated by the
  synchronous single-batch drain (all 5 events enter one list + one `_run_cycle`).
- **New scheduler tests**: drain-all (no sleep between back-to-back batches while heap non-empty);
  cap=20 **boundary** — pending=20 → drained 20 / **no** WARNING; pending=21 → drained 20 /
  deferred 1 / WARNING with `total=21`; scheduled-tick degenerate path passes `[("scheduled",
  None)]`.
- **`run_agent_cycle` call-site migration**: **~40 call sites across 8 test files**
  (`test_usage_limits`, `test_cli_app`, `test_p4_cycle_capture`, `test_agent_cycle_injection`,
  `test_cycle_log`, `test_wake_event_timestamp`, `test_run_agent_cycle_phase1`,
  `test_cycle_summary_injection`) pass `trigger_type`/`context` positionally — all need the new
  list contract. `byte-identical` (below) guarantees output, **not** signature; every call site
  still changes.
- `run_agent_cycle` behavior: N==1 prompt **byte-identical** regression (lock the current
  single-event strings); N>1 priority-sectioned header + fill-before-alert ordering + per-event
  age suffix; `triggered_by` = dominant type.
- `_capture_trigger_contexts`: returns list; one-event-fails → `None` slot, batch survives;
  all-fail → `[None, …]` (count preserved, no `scheduled_tick` mislabel).
- `v_alert_lifecycle`: array rows (incl. a multi-event batch carrying one price-level alert +
  a fill — must still resolve the alert_id despite `triggered_by='conditional'`) resolve correctly;
  **legacy single-object rows still resolve** (the `json_array()` wrap regression).
- `display`: `_format_trigger_detail` returns **type + count** (`ALERT`; `CONDITIONAL +2 (1 fill,
  2 alerts)`); list input does not crash the Header. The removed detail branches mean the existing
  `_format_trigger_detail` fill/alert/T-EH-3 detail tests are **deleted/replaced**. `▾ Context`
  renders **per-event bullets** (split at `_EVENT_PREFIXES`, per-event truncation); N==1 keeps the
  single `Woke by — …` form; legacy single-object snapshot unchanged.
- Full `pytest` green (current baseline 2144 passed).

## Scope / non-goals

- **In**: `scheduler.py` (drain-all + cap-20 + warn, incl. the `:58` bootstrap call),
  `app.py` (`on_tick`/`run_agent_cycle` list contract + priority-sectioned prompt + async
  `_render_event_block(deps, …)`), `cycle_capture.py` (`_capture_trigger_contexts`), `views.py`
  (`v_alert_lifecycle` array-aware + drop `triggered_by='alert'`), `display.py`
  (`_format_trigger_detail` → type+count Header, list-safe; `_extract_event_line` → per-event
  Context bullets). Plus the **cross-9-file test migration** (§5 — ~40 `run_agent_cycle` call
  sites + full `test_scheduler.py` event-path rewrite). Sim-only phase.
- **Out — coalescing / dedup / staleness-drop**: scheduler stays a pure fact-provider; it does
  not decide which events matter (principle 1). Agent judges; the age suffix lets it discount
  stale ones.
- **Out — layer 2 anomaly handling** (see backlog): sustained-backlog ERROR escalation, hard
  memory ceiling with drop-oldest-lowest-priority, source circuit-breaker. Data-triggered, not
  built now (max observed 4; building drop-policy speculatively = over-engineering + re-opens the
  fact-provider trade-off).
- **Out — OKX live paths**: untouched (sim-only run phase).

## Follow-up backlog (data-triggered)

- **Layer 2 — drain anomaly handling.** Trigger: the §1 WARNING fires repeatedly (sustained
  per-cycle, rising `deferred`) on a real session. Then the data shows the failure mode (legit
  cascade vs duplicate storm vs producer bug) and dictates which to build: ERROR escalation after
  K consecutive capped wakes, a hard heap ceiling (drop oldest lowest-priority, never fills,
  ERROR), and/or per-source backpressure.
- **Re-verify burst rate** on the first post-#70 session (the N=1 / pre-#70 caveat above).

## Decisions log (for plan/impl — do not re-litigate)

1. **Pure batch, zero filtering** (not dedup, not staleness-drop) — principle 1 + 8.
2. **Priority-sectioned prompt**, N==1 **prompt** byte-identical to current (session-log Header
   may change — see #7).
3. **`trigger_context` = array, `triggered_by` = dominant type, no new column** (count via
   `json_array_length`). **`triggered_by` is retained** — it is a denormalized dominant-type
   rollup with real consumers (agent-facing recent-cycle summaries `app.py:348/356`; analytics
   `triggered_by_distribution` in `analyze_sim`/`diff_sim`; `v_cycle_metrics` projection), not
   redundant with the array (cheap cycle label vs full unnest). Do not drop it.
4. **Cap = 20**, semantics = defer + WARNING; predicate = `deferred > 0` (never `len==20`);
   `total = drained + deferred`.
5. **All-fail capture → `[None]*n`** (no `scheduled_tick` special-case): preserve count, never
   mislabel type.
6. **`v_alert_lifecycle` rewrite has two hard constraints**: `json_array()`-wrap legacy object
   rows before `json_each`; drop the `triggered_by='alert'` clause (filter per-element type).
7. **Header `Trigger` line = type + count only** (detail moves to Context `Woke by`, rendered
   per-event). Session-log human-facing change; does not touch the prompt. Removes the
   pre-existing Header↔Context detail duplication. **Explicit scope decision (confirmed):** the
   N==1 Header regression (loses fill PnL / alert summary for the 73% single-event cycles; T-EH-3
   degradation branch deleted) ships *with* this iter rather than as a follow-up — justified
   because batching forces the `_format_trigger_detail` change anyway and splitting would put two
   iters on the same function with an inconsistent interim. Detail is preserved losslessly in
   Context.
8. **Layer 1 only this iter**; layer 2 anomaly handling is data-triggered backlog.
