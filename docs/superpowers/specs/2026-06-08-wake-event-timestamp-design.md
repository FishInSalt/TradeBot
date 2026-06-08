# Wake-event timestamp & relative age — design

**Date**: 2026-06-08
**Type**: mini-iter (rendering-only, `src/cli/app.py`)
**Status**: design approved, pending impl

## Problem

When the agent is woken, the prompt names the trigger and the event but carries **no
time information**. The agent knows "now" (every perception tool stamps `@ HH:MM:SS UTC`)
but not **when the waking event fired** — i.e. how stale the event is by the time it reasons.

The event timestamp already exists in `trigger_context` (`context.timestamp`, ms epoch) and
is captured to the DB; it simply never reaches the prompt. This is a last-mile rendering gap,
not a data-plumbing gap.

### Empirical grounding (session `f670abe1`, 384 cycles)

- **65% of cycles are event-driven** (alert 43% + conditional/fill 22%); scheduled 35%.
- Lag from event-fire → cycle-start (LLM duration removed): alert avg 37s / max 4.6min;
  fill avg 92s / max 78min. **60% of alerts processed ≤5s, but 20–22% waited >60s** — and
  the agent cannot tell which case it is in.
- Concrete misread: cycle `bf84ca93` calls a **4.6-min-old** alert "just triggered."
- Workaround in the wild: cycle `308202ba` manually derives "3 min ago" from cycle-history
  timestamps to bound the alert age — the "hand-compute ≥3×" tool-design trigger signal.
- Verb usage (agent's own narrative; counted as **cycles whose `reasoning` contains the term**,
  not raw occurrences): alerts → `fired` (157 vs `triggered` 116); fills → `triggered`/`filled`
  (67/48), `fired` least (33). Ordering is robust to counting basis; the design uses only the
  ordering, not the exact integers. Verb choice is event-type-specific.

> **Caveat — session predates PR #70 (sync market fill, merged 2026-06-08).** `f670abe1` ran
> 2026-06-01…06-04, before market orders settled synchronously in `create_order`. Of the 84 fill
> triggers, **market = 54%** (stop 29%, tp 17%, limit 1%) — exactly the class #70 folds into the
> placing cycle (no async wake). Post-#70 the fill-triggered share drops (~22% → ~10%, leaving
> stop/tp/limit/liquidation), so the fill **frequency** sizing above is on a stale population. The
> **lag** signal is unaffected or stronger: market fills were the freshest (avg 31s); the surviving
> stop/tp fills average **163s** (tp avg 377s, incl. the 78-min max). The design conclusion holds —
> per-event value rises after #70 — but re-verify fill frequency on the first post-#70 session.
> (This is the `feedback_data_mismatch_old_impl_inference` trap; no post-#70 session exists yet.)

The codebase already has a ratified convention for "when did X happen" — **absolute UTC +
relative age** — in `_build_recent_summaries_block` (`14:38 UTC (3 min ago)`) and
`_fmt_news_ts` (whose comment states the rationale: *"Explicit UTC + relative age so the
agent reads freshness directly rather than subtracting the fetch header by hand"*). The wake
event is the lone place this is absent.

## Design

Append a `{verb} {abs-UTC} ({age})` clause to each wake event's description line. Verb is
event-type-specific (matches agent's native vocabulary); the absolute UTC and relative age
follow the existing house style.

| Trigger | Verb | Rendered line |
|---|---|---|
| scheduled | `fired` | `You have been woken up by a scheduled trigger — fired 2026-06-01 14:38 UTC (just now)` |
| alert (pct) | `fired` | `PRICE ALERT: BTC dropped 0.5% in 15min (68002 → 67658) — fired 2026-06-01 14:34 UTC (4 min ago)` |
| alert (level) | `fired` | `PRICE LEVEL: BTC reached 67193.70 (alert id=… below 67200 — …) — fired 2026-06-01 14:34 UTC (4 min ago)` |
| fill | `filled` | `IMPORTANT EVENT: stop triggered — BTC 17.13 @ 65526.4 … — filled 2026-06-01 14:34 UTC (4 min ago)` |

- **Verb placement** — the clause attaches to the line that *describes the event* for each
  type (the `PRICE …`/`IMPORTANT EVENT` line for fill/alert; the header line for scheduled,
  which has no separate event line). Keeps "what" and "when" colocated.
- **fill uses `filled`, not `triggered`** — the fill line already says `{reason} triggered`;
  reusing `triggered` in the age clause would echo. `filled` is the agent's 2nd-most verb (48×)
  and is semantically exact: `FillEvent.timestamp = now_ms` is the fill moment.
- **scheduled age is always `just now`** by construction (its trigger time ≡ `cycle_started_at`).
  Carried per the explicit decision that every trigger type gets a uniform `{verb} {UTC} ({age})`
  line. The `(just now)` is tautological for scheduled, so its informational value is the
  **abs-UTC** (the prompt-build wall clock, in-prompt without a tool call) — not a "now anchor" the
  agent otherwise lacks (it already reads `now` from any perception tool's `@ HH:MM:SS UTC` stamp).
  It does **not** signal wake-punctuality (see Non-goals).
- **session-log Context self-containment** — the scheduled suffix sits on the header line, which
  `_extract_event_line` does not capture (it anchors on the `PRICE …`/`IMPORTANT EVENT` prefixes).
  So the session-log `▾ Context` section renders the scheduled wake-time clause via a dedicated
  `_extract_scheduled_wake_suffix` (parsed verbatim from `user_prompt_snapshot`, same source as the
  alert/fill event lines): `Woke by — SCHEDULED — fired 2026-06-01 14:38 UTC (just now)`. This keeps
  the section self-contained (each section stands on its own — the cycle time also appears in the
  Header `HH:MM:SS UTC`, but Context no longer relies on the reader looking up there). Legacy
  snapshots without the clause fall back to the bare `Woke by — SCHEDULED` label.

### Relative-age ladder — `_format_event_age(now, then)`

Thin wrapper over the existing `_format_relative_time` (already second-granular). Reuse, do
not fork (F5 drift-guard).

| Condition | Output |
|---|---|
| `then > now` (clock skew / sleep artifact) | absolute UTC only, **no age clause** (mirror `_fmt_news_ts` future guard) |
| `now - then < 2s` | `just now` |
| else | delegate to `_format_relative_time` → `42 sec ago` / `4 min ago` / `1 hour ago` |

- **now anchor** = `cycle_started_at` (prompt-build moment), **not** DB `created_at` (which is
  cycle-end and would inflate age by one cycle's wall time).
- **abs-UTC format** = `%Y-%m-%d %H:%M UTC` (house style; age clause carries sub-minute precision).
- The multi-minute / multi-hour tail (e.g. the 78-min outlier) renders a large age — itself a
  useful "something is off" signal — rather than a false "recent."
- **Hour-scale precision** — reusing `_format_relative_time` coarsens ≥1h to `N hours ago`
  (drops the minutes; `_fmt_news_ts` would say `1h 18m ago`). Accepted: >1h is unambiguously
  "very stale," and minute precision at that scale does not change the staleness read. The reuse
  is not strictly free, but the lost precision is immaterial here.

## Scope / non-goals

- **In**: prompt rendering for scheduled / alert (pct + level) / conditional in `run_agent_cycle`
  + `_format_price_level_alert_trigger` + a new `_format_event_age` helper. Plus the session-log
  `▾ Context` section surfacing the scheduled wake-time clause (`src/cli/display.py`,
  `_extract_scheduled_wake_suffix`). Sim-only phase.
- **Out — wake punctuality**: "did I wake on time / did the system sleep" needs the scheduler to
  capture *intended* wake time (a different datum than fire time). Separate issue. Not covered here.
- **Out — scheduler / cycle_capture changes**: none required. Scheduled's fire time ≡
  `cycle_started_at`, computed inline; fill/alert timestamps already in `context`. (Mirroring the
  scheduled fire time into DB `trigger_context` is an optional, deferred symmetry nicety.)
- **Out — OKX live paths**: untouched (sim-only run phase).

## Implementation surface

`src/cli/app.py` only (est. <60 lines src):

1. `_format_event_age(now, then: datetime) -> str | None` — pure age ladder: future→None,
   <2s→"just now", else delegate to `_format_relative_time`. `then` is always tz-aware on this
   path, so no tz-naive normalization is exercised here.
2. `_wake_time_suffix(verb: str, event_ts_ms: int, now: datetime) -> str` — owns the int-ms →
   `datetime.fromtimestamp(ms / 1000, tz=utc)` conversion, the `%Y-%m-%d %H:%M UTC` abs-UTC
   render, and assembles ` — {verb} {UTC} ({age})` (or ` — {verb} {UTC}` when age is None).
   **Pure + sync — no `deps`, no `await`.** The prompt body stays inline (the full-close fill
   branch keeps its `await deps.exchange.get_contract_size`); only the suffix is extracted.
3. Each of the 4 branches calls `_wake_time_suffix` and appends to its event line; `now` =
   `cycle_started_at`. fill/alert pass `context.timestamp`; scheduled passes
   `int(cycle_started_at.timestamp() * 1000)` (→ "just now").
4. `src/cli/display.py`: `_extract_scheduled_wake_suffix(wake_half) -> str` parses the
   ` — fired …` clause off the scheduled header line; `_render_context`'s scheduled branch appends
   it to the `Woke by — SCHEDULED` label. "" for legacy snapshots (backward-compatible).

## Testing

- `_format_event_age`: future → None; <2s → "just now"; sub-minute → "N sec ago"; minute/hour
  ladder. (No tz-naive case — `then` is always tz-aware on this path.)
- `_wake_time_suffix` per verb: int-ms → UTC conversion; scheduled→`fired … (just now)`,
  pct/level alert→`fired`, fill→`filled` (assert no double `triggered`); future ts → UTC-only,
  no parenthetical.
- Branch integration: each of the 4 wake branches embeds the suffix on its event line.
- `_extract_scheduled_wake_suffix`: new header → ` — fired … (just now)`; legacy header → `""`.
- session-log Context: scheduled snapshot with the clause → `Woke by — SCHEDULED — fired …` in the
  rendered `▾ Context`; legacy snapshot → bare `Woke by — SCHEDULED` (existing substring tests hold).
- Existing 3 prompt-asserting test files (`test_p4_cycle_capture`, `test_agent_cycle_injection`,
  `test_session_log_cycle_context`) — clause is appended, not substituted; verify substrings still
  pass and update any end-anchored assertions.

## Decision — fill verb

- **fill verb = `filled`** (not `triggered`) — avoids echoing the fill line's own `{reason}
  triggered`, and is semantically exact (`FillEvent.timestamp` is the fill moment). Flip to
  `triggered` only if matching the agent's top raw count is preferred over avoiding the echo.
