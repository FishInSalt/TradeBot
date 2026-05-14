# Iter tool-opt-mark-vs-last — liquidation distance anchored to mark price + algo trigger reference disclosure

**Date**: 2026-05-14
**Iteration**: iter-tool-opt-mark-vs-last (Sprint 5, iter-12 of tool-optimization roadmap)
**Type**: Design spec (exchange API extension + 1 perception output addition + 4 distance-label text swaps)
**Source brainstorm**: 2026-05-14 session reconciling roadmap §iter-12 framing against memory `project_okx_demo_mark_vs_last_drift` (校准 2026-04-28)
**Upstream**: `.working/tool-optimization/02-execution-roadmap.md` §iter-12 + `.working/tool-optimization/99-backlog.md` OO-7 / POS-5 / SL-2 / TP-2
**Related principles**: `docs/superpowers/principles/tool-design-principles.md` — 1 (fact-only) / 3 (signal single source) / 4 (signal completion vs new tool) / 7 (output friendliness — labels / units / anchors) / 8 (trust agent + tool surface)

---

## 0. One-minute summary

OKX perpetual liquidation is computed against **mark price** (docs verified; project `project_okx_demo_mark_vs_last_drift` memory 校准 2026-04-28). Four perception/execution tools currently anchor their distance calculations to `ticker.last`:

- `get_position` Liquidation distance — **physically inconsistent**: the `liquidation_price` returned by OKX is mark-anchored, comparing against `ticker.last` mixes references.
- `get_position` Exit Orders SL/TP distance — anchored to last, matching OKX algo trigger default (project does not set `triggerPxType`, so OKX uses last per `/api/v5/trade/order-algo` docs).
- `set_stop_loss` / `set_take_profit` success message distance — same anchor as Exit Orders.
- `get_open_orders` `_render_single_order` distance — same anchor.

This spec lands a precise, narrow correction:

1. Add 1 exchange API: `BaseExchange.get_mark_price(symbol: str) -> float`.
2. `get_position` fetches mark as a 6th `asyncio.gather` member wrapped with a `_safe_mark_price` helper (parallel pattern to `_safe_ohlcv`, zero latency increment); on mark fetch failure only the Mark line + Liquidation distance degrade (Exit Orders + Notional/Margin keep rendering normally). Happy path adds a `Mark: <mark> (Last: <last>, drift <±X.XX%>)` line and recomputes Liquidation distance against mark with 2-decimal precision.
3. The four distance-label sites swap `"from current"` → `"from {trigger_ref} price"` where `trigger_ref` is read from a new `BaseExchange.algo_trigger_reference: str = "last"` class attribute (runtime-output single source of truth; OKX="last", Sim="last", future exchanges can override).
4. SimulatedExchange `get_mark_price` returns `await fetch_ticker().last` (sim has one price feed; preserves test fixture compatibility).
5. Wrapper docstrings in `trader.py` are synced: 3 in-place `"current"` → `"last price"` text edits at lines 133 / 156 / 159 + 1 new sentence added to `get_position` docstring (`"Liquidation distance is computed against mark price."`) — the added sentence covers the mark anchor for Liquidation, which is **not** covered by `algo_trigger_reference` (different anchor mechanism, not a duplication of runtime output). No new sentences added to set_stop_loss / set_take_profit / get_open_orders since their distance label is `algo_trigger_reference`-driven (would duplicate runtime output).
6. Stale docstring family across `scripts/iter6_task0_capture.py` and `scripts/iter6_diag_ticker.py` is校准 in the same iter — **4 logical sub-changes / 5 edit actions**: (a) `_fetch_mark_price` docstring (line 203-216) + (b) `_place_algo` docstring (line 222-223) + (c) module docstring item at line 13 + (d) drift formula at line 81 together with its print label at line 82 (one logical convention swap, two adjacent edits). See §3.1 "Stale script family校准" row.

`REGISTERED_TOOL_NAMES` stays at **34**. No persona or Layer-1 nudge changes (principle 8). No `triggerPxType` change to the project's algo-order submission path (out-of-scope — would be a separate iter touching write path).

The roadmap §iter-12 motivation cited "实盘漂 1.67%" — memory 校准 confirms this is a **demo-only artifact**; production mark-vs-last drift is typically <0.05%. The fix is therefore motivated by *physical correctness of the liquidation anchor* (principle 1 fact-only + principle 3 single-source) rather than by an observable agent narrative pain. The 1.67% number is retained as a demo-environment regression test fixture, not as production motivation.

---

## 1. Empirical foundations

### 1.1 Source data

- OKX V5 API documentation: `/api/v5/public/mark-price` endpoint (mark-only, swap/futures); `/api/v5/trade/order-algo` `triggerPxType` parameter default = `"last"`.
- CCXT 4.5.47 source: `okx.py:866` error 51280 message confirms last-price reference for trigger validation; `okx.py:3427` docstring confirms `triggerPxType` defaults to last.
- Memory `project_okx_demo_mark_vs_last_drift` (校准 2026-04-28): demo `ticker.last` vs `mark_price` empirically drifted -1.67%; production drift typically <0.05% (Iter 6 Task 0 capture).
- Project codebase grep: `/Users/z/Z/TradeBot/src/` — zero occurrences of `triggerPxType` / `slTriggerPxType` / `tpTriggerPxType`, confirming project relies on OKX default.
- Project script `scripts/iter6_task0_capture.py:203-216` has a `_fetch_mark_price` helper using `public_get_public_mark_price`; the docstring there is **stale** (claims OKX trigger validation uses mark — superseded by 2026-04-28 校准). The fetch mechanics are correct and informed this spec's OKX-side impl.

### 1.2 Per-issue datum table

| Issue | Datum | Source |
|---|---|---|
| POS-5 Liquidation anchor mismatch | `liquidation_price` is mark-derived (OKX engine); current `(current - liq) / current` uses `current = ticker.last` as denominator → mixed-anchor distance metric | `src/agent/tools_perception.py:327,342-343` + OKX V5 docs |
| OO-7 / SL-2 / TP-2 trigger-ref label drift | OKX algo `triggerPxType` defaults to last; project doesn't override; output text labels distance "from current" (ambiguous between last / mark / index) | `src/agent/tools_perception.py:433-435` + `src/agent/tools_execution.py:167-168, 197-198` + OKX V5 docs |
| Roadmap framing校准 | roadmap §iter-12 stated "OKX 算法单触发用 mark price" — contradicted by OKX V5 docs (default=last) + memory校准 (2026-04-28) + project code (no triggerPxType set) | `.working/tool-optimization/02-execution-roadmap.md:573` vs memory `project_okx_demo_mark_vs_last_drift` |
| Demo drift artifact | demo `ticker.last` 77986.30 vs mark 76680.30 = -1.67% (one snapshot); production drift typically <0.05% | memory `project_okx_demo_mark_vs_last_drift` |

### 1.3 Implication

This is **not** a narrative-grep-driven iter (no sim #8 agent hand-calculation pattern surfaced "from mark vs from last" confusion). The justification rests on:

- **Principle 1 (fact-only)**: comparing `last` against a mark-derived `liquidation_price` is a silent anchor mix — implicit semantic that violates fact-only on the Liquidation row.
- **Principle 3 (signal single source)**: each price reference (last / mark / index) should be one authoritative source per use. Mixing references on a distance metric forces the agent to mentally reconcile two anchors.
- **Principle 7 (label friendliness)**: distance label "from current" is anchor-ambiguous; explicit `"from last price"` (with `algo_trigger_reference` driving the word) ties the label to the actual OKX comparison reference.

The empirical strength here is structural (physical correctness + docs-verified anchor), not narrative-frequency-driven. The roadmap's "1.67% drift" motivation is reframed as a regression-test fixture, not production motivation.

---

## 2. Industry-standard reference

Cross-exchange behavior on algo trigger reference and liquidation reference (informs §3 single-source-of-truth `algo_trigger_reference`):

| Exchange | Liquidation reference | Algo trigger reference (default) | API parameter |
|---|---|---|---|
| **OKX** (perp swap) | mark | **last** | `triggerPxType` (last / index / mark), default last |
| **Binance** (USDT-M futures) | mark | **last** | `workingType` (MARK_PRICE / CONTRACT_PRICE), API default `CONTRACT_PRICE` (last). UI typically uses MARK_PRICE but API surface defaults to last per Binance USD-M futures docs. |
| **Bybit** (V5 linear) | mark | (no silent default — explicit) | `triggerBy` (LastPrice / IndexPrice / MarkPrice) |
| **dYdX** (v4) | mark/oracle | mark/oracle | (oracle = mark-equivalent across all order paths) |
| **Hyperliquid** | oracle | oracle | (single reference everywhere) |

**Implications baked into this spec**:
- "Liquidation = mark" is universal — the get_position fix is industry-aligned regardless of exchange portability.
- "Algo trigger = last" is OKX-specific in spirit but **also coincidentally matches Binance USDT-M API default** (per corrected table above). The `algo_trigger_reference` attribute on `BaseExchange` makes this an exchange-level concern; the four distance-label sites read from this attribute. The forward-compat lever bites hardest for **Bybit V5**, which has no silent default — `BybitExchange.algo_trigger_reference` must be set to one of `last`/`mark`/`index` explicitly during integration; without the abstraction this becomes four synchronized cross-file text edits. **Hyperliquid / dYdX** use oracle/mark uniformly — `HyperliquidExchange.algo_trigger_reference = "mark"` (or `"oracle"` if we add that token) flips all four output sites at once. The Binance line is therefore not the strongest forward-compat justification; Bybit + Hyperliquid are.
- The "drift / basis" exposure on the get_position Mark row is institutional-standard — perp UIs (Binance Futures, Bybit, OKX Web) all display mark and last separately with implicit or explicit drift.

This `algo_trigger_reference` indirection is the spec's primary safeguard against the OKX-specific bake-in trap flagged during brainstorm review.

---

## 3. Architecture and scope

### 3.1 Issue → change matrix

| Issue ID | Surface | Change |
|---|---|---|
| API add | `BaseExchange` (`src/integrations/exchange/base.py`) | New abstract method `get_mark_price(symbol: str) -> float` |
| API add | `BaseExchange` (class attribute) | New class attribute `algo_trigger_reference: str = "last"` — single source of truth for distance-label trigger ref text |
| API impl | `OKXExchange.get_mark_price` (`src/integrations/exchange/okx.py`) | Fetches via `public_get_public_mark_price({"instType": "SWAP", "instId": inst_id})`; `inst_id = self._client.market(symbol)["id"]`; explicit `RuntimeError` on empty `data`; wrapped in existing `@_retry()` |
| API impl | `OKXExchange.algo_trigger_reference` | Inherits `"last"` from BaseExchange (no override; project doesn't set triggerPxType) |
| API impl | `SimulatedExchange.get_mark_price` (`src/integrations/exchange/simulated.py`) | Returns `(await self.fetch_ticker(symbol)).last`; sim has single price feed, preserves existing test fixtures. Note: under the new `get_position` flow this causes a **second** `fetch_ticker` call within the same gather (the 6-tuple gather already includes ticker fetch). Safe because SimulatedExchange's `fetch_ticker` is observation-only (no internal tick advance / no state mutation) — back-to-back invocation returns the same value. If future SimulatedExchange evolves to mutate state on `fetch_ticker` (e.g., synthetic tick advancement for replay scenarios), this should be revisited — candidate fix is reading `self._latest_price` directly inside `get_mark_price`, trading minor coupling for idempotence. |
| API impl | `SimulatedExchange.algo_trigger_reference` | Inherits `"last"` from BaseExchange (no override) |
| POS-5 (gather) | `get_position` Phase 2 gather (`src/agent/tools_perception.py:306-314`) | Mark fetch is added as a **6th gather member** wrapped with a `_safe_mark_price` helper (parallel pattern to `_safe_ohlcv` at line 259-264 — wraps the call, catches all `Exception`, logs, returns `0.0` on failure). The 6-tuple gather runs all six IO calls concurrently — **zero latency increment** vs the existing 5-tuple. Variable initialization: `mark_price` receives the gather result (either the fetched float or `0.0` on `_safe_mark_price` failure). Downstream gates (`mark_price > 0`) detect failure naturally. On mark fetch failure: Mark line is omitted, Liquidation line falls back to `"Liquidation: <liq_price:.2f> (distance unavailable: mark fetch failed)"`, Exit Orders section is **unaffected** (continues to use `ticker.last`). Rationale: mark price serves only the Risk Exposure section's Mark + Liquidation rows; Exit Orders distance anchor is `ticker.last` (matches OKX algo trigger reference) and has zero dependency on mark. The `_safe_mark_price` wrapper isolates failure from the gather's outer `return_exceptions=False` semantics — only mark fetch goes through the safe wrapper; ticker/balance/orders/contract_size remain hard-fail per existing degradation contract. |
| POS-5 (Mark line) | `get_position` Risk Exposure section (`src/agent/tools_perception.py:339-349`) | Insert new line **before** Liquidation when mark fetch succeeds. Three render variants by `ticker.last` availability: <br>**(i) mark > 0 AND ticker.last > 0** (happy path): `f"Mark: {mark_price:.2f} (Last: {ticker.last:.2f}, drift {drift_pct:+.2f}%)"` where `drift_pct = (ticker.last - mark_price) / mark_price * 100`. Sign convention: positive = last above mark. <br>**(ii) mark > 0 AND ticker.last <= 0** (mark OK, ticker degraded — rare edge case since ticker fetch is gather-mandatory; reachable only if SimulatedExchange seeds ticker.last=0 in a test fixture): `f"Mark: {mark_price:.2f} (Last: unavailable)"`. Drift sub-string omitted. <br>**(iii) mark <= 0 OR mark fetch failed**: Mark line entirely omitted from output. |
| POS-5 (Liquidation) | `get_position` Liquidation distance (`src/agent/tools_perception.py:342-348`) | Gate predicate switches from `current_price > 0` to `mark_price > 0` (post-spec mark-anchoring renders ticker.last irrelevant to the Liquidation calculation). <br>**Happy path** (`p.liquidation_price is not None AND mark_price > 0`): `liq_dist_pct = abs(mark_price - p.liquidation_price) / mark_price * 100`; format precision `{:.2f}%` (was `{:.1f}%`); ATR-multiple suffix preserved when `atr_pct_1h is not None and atr_pct_1h > 0`. <br>**Mark-fail path** (mark fetch raised): `f"Liquidation: {p.liquidation_price:.2f} (distance unavailable: mark fetch failed)"` — no last-based fallback (avoids silent anchor mix that this spec exists to fix). ATR suffix also omitted since its denominator anchor is moot when numerator is unavailable. <br>**Liquidation_price None path**: Liquidation line entirely omitted (unchanged from existing behavior). |
| POS-5 | `get_position` Exit Orders distance (`src/agent/tools_perception.py:369-378`) | `_fmt_exit` last-token swap: `"{abs(dist_curr_pct):.1f}% {direction_curr} current"` → `"{abs(dist_curr_pct):.1f}% {direction_curr} {deps.exchange.algo_trigger_reference} price"`. `current_price` variable (= `ticker.last`) is the distance anchor, unchanged from current behavior. |
| OO-7 (non-OCO) | `get_open_orders._render_single_order` (`src/agent/tools_perception.py:432-435`) | `"@ {price} ({dist:+.2f}% / {pts:+.1f} pts from current)"` → `"@ {price} ({dist:+.2f}% / {pts:+.1f} pts from {trigger_ref} price)"` where `trigger_ref` is plumbed through `_render_single_order` signature (new required `trigger_ref: str` parameter — no default). Call site at `tools_perception.py:484` passes `deps.exchange.algo_trigger_reference` explicitly. |
| OO-7 (OCO) | `get_open_orders` OCO inline render (`src/agent/tools_perception.py:467-481`) | Inline `sl_dist` and `tp_dist` f-strings at lines 469 / 474 swap `"from current"` → `f"from {trigger_ref} price"` where `trigger_ref = deps.exchange.algo_trigger_reference`. Path is separate from `_render_single_order` (OCO branch detected at line 459-463 via same-id 2-leg stop+take_profit pattern). Patch shape: <br>```python<br>trigger_ref = deps.exchange.algo_trigger_reference  # add once near line 450<br>sl_dist = (<br>    f" ({(sl.price - current) / current * 100:+.2f}%"<br>    f" / {sl.price - current:+.1f} pts from {trigger_ref} price)"<br>    if current > 0 else " (ticker unavailable)"<br>)<br># tp_dist mirrors sl_dist with `tp.` substitution<br>``` |
| SL-2 | `set_stop_loss` success message (`src/agent/tools_execution.py:167-169`) | `"({dist_pct:+.2f}% from current {ticker.last:.2f})"` → `"({dist_pct:+.2f}% from {trigger_ref} price {ticker.last:.2f})"` where `trigger_ref = deps.exchange.algo_trigger_reference` |
| TP-2 | `set_take_profit` success message (`src/agent/tools_execution.py:197-199`) | Mirror SL-2 |
| Docstring sync | `get_position` wrapper (`src/agent/trader.py:133`) | Existing phrase `"SL/TP distances from both entry and current"` → `"SL/TP distances from both entry and last price"`. Add: `"Liquidation distance is computed against mark price."` (1 sentence describing the only mark-anchored field in output). |
| Docstring sync | `get_open_orders` wrapper (`src/agent/trader.py:156, 159`) | Two sites: `"distance from current price"` → `"distance from last price"` (line 156); `"price level and distance from current"` → `"price level and distance from last price"` (line 159). |
| Docstring sync | `set_stop_loss` / `set_take_profit` wrappers (`src/agent/trader.py:478-490, 495-507`) | Existing docstrings have no `"current"` wording; no sync required. Output format is described elsewhere (no new docstring sentence added — fact-only sentence would duplicate `algo_trigger_reference`-driven output text). |
| Stale script family校准 | `scripts/iter6_task0_capture.py` + `scripts/iter6_diag_ticker.py` (4 logical sub-changes / 5 edit actions) | All sites trace back to the same superseded claim (per 2026-04-28 校准 in memory `project_okx_demo_mark_vs_last_drift`). Replace in one sweep: <br>**(a) `iter6_task0_capture.py:203-216` `_fetch_mark_price` docstring** — replace `"OKX algo trigger validation uses mark price despite the 51280 error message saying last price"` with: `"OKX algo trigger validation uses last price (V5 docs + CCXT 4.5.47 verified). Demo workaround: mark is 1.67% below last in demo env, so triggers computed from mark sit well below OKX's last-reference comparison and reliably bypass 51280 errors. In production (drift typically <0.05%), this workaround offers negligible buffer over last-anchored triggers."` <br>**(b) `iter6_task0_capture.py:222-223` `_place_algo` docstring** — replace `"Trigger computed from mark price (NOT ticker.last) because OKX algo validation uses mark price internally."` with: `"Trigger computed from mark price (NOT ticker.last) as a demo workaround — see _fetch_mark_price docstring above for the校准 rationale."` <br>**(c) `iter6_diag_ticker.py:13` module docstring item (c)** — replace `"(c) trigger validation uses mark price not last price"` with: `"(c) trigger validation uses last price; demo mark/last drift offers buffer (see project memory okx-demo-mark-vs-last-drift)"`. <br>**(d1) `iter6_diag_ticker.py:81` drift formula** — replace `diff_pct = (float(mp.get('markPx', 0)) - ticker1.last) / ticker1.last * 100` with `diff_pct = (ticker1.last - float(mp.get('markPx', 0))) / float(mp.get('markPx', 0)) * 100` to match spec §4.1 sign convention. <br>**(d2) `iter6_diag_ticker.py:82` print label** — replace `"mark vs ticker.last drift"` with `"last vs mark drift (last - mark / mark)"` for parity with the new formula. <br>Note on value change: the **physical observation** (the price gap between mark and last) is unchanged across the convention swap, but both the **sign flips** (from negative to positive when last > mark, matching demo conditions) **and the magnitude shifts ~0.03pp** because the denominator changes from `ticker.last` to `mark`. With demo values (last=77986.30, mark=76680.30): old = -1.6747%, new = +1.7033%. Any cached output snapshots that reference the value need re-baselining for both sign and magnitude. |

### 3.2 Tool count invariant

`REGISTERED_TOOL_NAMES` stays at **34** (20 perception + 13 execution + 1 memory). No tool added or removed.

### 3.3 Scope boundary

**In-scope**:
- One `BaseExchange` abstract method (`get_mark_price`) + one class attribute (`algo_trigger_reference`)
- OKX + Sim implementations of the new method
- `get_position` mark-isolated `try/except` pattern (mark fetch failure does not propagate to Exit Orders)
- `get_position` Risk Exposure section: Mark line addition + Liquidation distance recomputation + 2-decimal precision + mark-fail fallback for Liquidation line
- `get_position` Exit Orders, `get_open_orders` (non-OCO + OCO branches), `set_stop_loss`, `set_take_profit` distance label text swap to use `algo_trigger_reference`
- Wrapper docstring sync at `trader.py:133` (`get_position`) and `trader.py:156, 159` (`get_open_orders`): existing `"current"` wording swapped to `"last price"` to match new output text
- Stale docstring + formula family校准 in `scripts/iter6_task0_capture.py` (lines 203-216, 222-223) and `scripts/iter6_diag_ticker.py` (lines 13, 81, 82) — 4 logical sub-changes / 5 edit actions, all tracing to memory 2026-04-28 校准
- New tests (`tests/test_iter_tool_opt_mark_vs_last.py`) covering OKX endpoint mock with full V5 envelope, Sim equivalence, mark-isolated degradation, drift formula sign convention, byte-equal + substring drift guards per §5.3 regime
- Existing test fixture updates (~25 cases across 3 files per §5.2)

**Out-of-scope** (deferred candidates noted in §9):
- Setting `triggerPxType=mark` on project's algo order submission — would change OKX trigger semantic; separate iter touching write path
- Index price (third reference) — not used anywhere in scope; defer to W3+ trigger
- LIMIT order distance anchoring to bid/ask (instead of last) — would be theoretically more correct for matching ref, but simplification using last is industry-acceptable and current scope retention
- Funding-rate / drift correlation surfacing — fact-only narrative connection exists but adding it would violate principle 1 (judgmental linkage)
- Multi-exchange `algo_trigger_reference` override fixtures — no Binance/Bybit integration yet; the attribute exists for forward compatibility but is not actively varied
- Caching mark price — get_position is per-cycle, single fetch; YAGNI

### 3.4 Principle reconciliation

- **Principle 1 (fact-only)**: All new output is fact-only. `Mark: X (Last: Y, drift +Z%)` — three numerical facts; no advisory phrasing. Liquidation distance becomes anchor-coherent. Distance label text swap moves from anchor-ambiguous `"from current"` to anchor-explicit `"from last price"`. Wrapper docstring additions are single fact-sentences, no `"use this when"` / `"should"` / `"good for"`.
- **Principle 3 (signal single source)**: Mark and last become two clearly-distinguished sources on the Risk Exposure section (label + value); Liquidation distance becomes single-anchor (mark only). `algo_trigger_reference` is a single source of truth feeding the **runtime output text** at four label sites — agents never see the literal trigger reference word (e.g., `"last"`) hardcoded across multiple output sites. Wrapper docstrings (build-time text describing current behavior) describe the OKX-specific concrete reference; if a future Binance integration ships, both the attribute override and the docstrings move together in the same PR. Two layers (runtime output / build-time docstring) intentionally distinguished; single-source applies to the runtime layer.
- **Principle 3 — forward-compat investment honesty**: `algo_trigger_reference` is a single class attribute on `BaseExchange` with one current value (`"last"`). Today it's effectively a named constant; its value as a single-source abstraction is realized only when a second exchange wrapper exists. The strongest payoff cases are **Bybit V5** (no silent default — `triggerBy` must be set explicitly; without this attribute, a Bybit wrapper would force four synchronized cross-file text edits) and **Hyperliquid / dYdX** (oracle/mark-uniform — single-attribute override flips all four label sites). Binance USDT-M is **not** a strong forward-compat case: API default `CONTRACT_PRICE` (= last) coincides with OKX, so a BinanceExchange wrapper would inherit `algo_trigger_reference = "last"` without override. Cost is small (1 attribute); benefit is forward-looking. This is intentional, not over-engineering: the alternative — hardcoding `"last"` at four output sites — would force a synchronized cross-file text edit on the first exchange whose `algo_trigger_reference` differs from OKX's, which is the failure mode this attribute exists to avoid.
- **Principle 4 (signal completion vs new tool)**: Mark is **not** "already fetched but unrendered" — OKX V5 ticker payload does not carry markPx (separate endpoint). New API is justified by physical-correctness need rather than retrieval-cost arbitrage. Net-zero tool-count change.
- **Principle 7 (output friendliness)**: Mark line decorates value with label + reference context (Last, drift). Drift has explicit sign convention. Liquidation precision upgraded `{:.1f}%` → `{:.2f}%` matches the relevance of small distances at high leverage. Distance labels become anchor-explicit.
- **Principle 8 (trust agent + tool surface)**: Tool surface lever, not prompt nudge. Wrapper docstring carries one factual sentence per affected tool; persona untouched. No `"Related:"` cross-routing.

---

## 4. Industry-standard alignment details

### 4.1 Drift formula and sign convention

```python
drift_pct = (ticker.last - mark_price) / mark_price * 100
```

- **Denominator = mark** (industry convention: mark is the reference; basis = contract premium over reference).
- **Sign**: positive when last > mark (contract trading above the reference — typically associated with positive perp funding); negative when last < mark.
- Drift is referenced colloquially in OKX/Binance/Bybit perp docs as "basis" or "mark deviation"; this spec uses "drift" as the rendered word per brainstorm preference. Equivalence is noted here for future cross-tool grep consistency.

### 4.2 Liquidation distance precision

Format `{:.2f}%` (was `{:.1f}%`). Rationale: at 100x leverage on BTC, liquidation can sit 0.5-1.0% from mark; 1-decimal display rounds away meaningful sub-percent precision. Token cost: +1 character per Liquidation line; per-cycle one occurrence; negligible.

**Same-section precision asymmetry**: Notional value and Margin used in the same Risk Exposure section retain `{:.1f}%`. Reason: Notional/Margin ratios are typically 50-300% (exposure / equity); 1-decimal precision is information-adequate for that magnitude range. Liquidation distance is the only field in the section that gets meaningfully small at high leverage (where 1-decimal vs 2-decimal makes a perceptible relative difference). Asymmetric precision is intentional — anchored to the field's typical magnitude band, not enforced uniformly across the section.

### 4.3 ATR-multiple suffix anchor

The `liq_dist_pct / atr_pct_1h` ratio in the suffix mixes a mark-anchored numerator with a last-anchored denominator (ATR is OHLCV-close-derived = last). Production drift <0.05% renders this mathematically negligible (≤0.05% noise on a ratio whose typical magnitude is ~2-10x). The mixed anchor is documented here but not algorithmically corrected; switching the ATR-% denominator to mark would be a chain of indicator-recomputation changes outside this iter's scope. Demo drift 1.67% would produce ~1.7% noise on the ATR-multiple — acceptable for a demo-only artifact, and is part of the regression-test fixture intent.

### 4.4 LIMIT order distance simplification

`get_open_orders._render_single_order` treats LIMIT and ALGO orders symmetrically with `"from last price"`. Strictly: LIMIT matching reference is bid (for sell-side limits) / ask (for buy-side limits). Last is an industry-common simplification (TradingView's "Distance to order" UI uses last). Within this scope, the simplification is retained — distinguishing per-direction bid/ask reference is a separate refinement candidate (§9).

---

## 5. Test plan

### 5.1 New tests (`tests/test_iter_tool_opt_mark_vs_last.py`)

File named after the iter (matches `tests/test_iter_tool_opt_alert_age.py` / `_error_metadata.py` / `_alert_family_rename.py` convention) for cross-iter forensic traceability.

All OKX mock fixtures use the full V5 response envelope per `project_iter2_mock_fidelity_lesson`: `{"code": "0", "msg": "", "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "markPx": "<value>", "ts": "<ms>"}]}`.

| Test | Scope |
|---|---|
| `test_okx_get_mark_price_fetches_endpoint` | Mock `public_get_public_mark_price` returning full envelope with `markPx="81920.10"`; assert OKXExchange.get_mark_price returns 81920.10 (float) |
| `test_okx_get_mark_price_raises_on_empty_data` | Mock returning `{"code": "0", "msg": "", "data": []}`; assert `RuntimeError` is raised (no silent fallback) |
| `test_okx_get_mark_price_uses_inst_id_conversion` | Assert `instId` parameter passed to endpoint is `BTC-USDT-SWAP` when symbol is `BTC/USDT:USDT` (via `self._client.market(symbol)["id"]`) |
| `test_sim_get_mark_price_returns_ticker_last` | SimulatedExchange with seeded ticker.last=80000.0; assert get_mark_price returns 80000.0 |
| `test_base_algo_trigger_reference_default_last` | Assert `BaseExchange.algo_trigger_reference == "last"` (and inherited unchanged on OKXExchange + SimulatedExchange) |
| `test_get_position_mark_line_byte_equal` | Fixture: mark=81920.10, ticker.last=81870.50, liq=51364.64, ATR(1h)=1535.40 → byte-equal Mark line = `"Mark: 81920.10 (Last: 81870.50, drift -0.06%)"` and Liquidation line = `"Liquidation: 51364.64 (37.30% away = 19.9× ATR(1h))"` |
| `test_get_position_liquidation_distance_uses_mark` | Fixture: mark=80000, ticker.last=82000, liq=51000 → distance = (80000-51000)/80000 = 36.25% (not 37.80% which is the last-anchored result) |
| `test_get_position_drift_positive_sign_demo_magnitude` | Fixture: mark=76680.30, ticker.last=77986.30 (exact values from memory `project_okx_demo_mark_vs_last_drift`); spec formula `(77986.30 - 76680.30)/76680.30*100 → +1.70%` (rounded to 2 decimals). Doubles as: (a) positive sign convention guard, (b) demo regression fixture anchored to memory-documented direction (last > mark in demo). Memory writes drift = `-1.67%` under opposite convention `(mark-last)/last` = `-1.6747%`; spec convention `(last-mark)/mark` gives `+1.7033%` → **same physical observation, sign flipped AND magnitude shifted ~0.03pp** because the denominator changes from last to mark. Test docstring must reproduce this convention-difference note to prevent future contributors from "fixing" the discrepancy. |
| `test_get_position_drift_negative_sign` | Synthetic fixture: mark=80048, ticker.last=80000 → drift = (80000 - 80048)/80048*100 ≈ `-0.06%` (negative sign convention guard; no claim of matching demo direction). |
| `test_get_position_mark_fetch_failure_isolated_to_liquidation` | Mock `get_mark_price` raising; assert: (a) Mark line is **omitted** from Risk Exposure section; (b) Liquidation line falls back to byte-equal `"Liquidation: <liq_price:.2f> (distance unavailable: mark fetch failed)"`; (c) Notional + Margin lines render normally; (d) **Exit Orders section renders normally with last-price-anchored distances** (this is the key A1-fix assertion) |
| `test_get_position_exit_orders_label_last_price` | Drift guard: exit orders line substring-contains `"% above last price"` or `"% below last price"` (variable price/contracts mean substring not byte-equal) |
| `test_set_stop_loss_message_uses_last_price` | Drift guard: success message substring-contains `"from last price"` (variable order ID + price means substring not byte-equal) |
| `test_set_take_profit_message_uses_last_price` | Drift guard: substring-contains `"from last price"` |
| `test_get_open_orders_single_order_uses_last_price` | Drift guard: non-OCO single-order line (via `_render_single_order`) substring-contains `"from last price"` |
| `test_get_open_orders_oco_pair_uses_last_price` | Drift guard: OCO line (inline render branch at `tools_perception.py:467-481`) substring-contains `"from last price"` for **both** sl_dist and tp_dist suffixes |
| `test_algo_trigger_reference_drives_label_text` | Monkey-patch a Sim exchange instance with `algo_trigger_reference = "mark"`; assert the four label sites output `"from mark price"` — confirms single-source-of-truth wiring; this test exists specifically to fail loudly if a future contributor hardcodes `"last"` at any of the four sites |

### 5.2 Existing tests requiring fixture updates

Verified via grep of `"from current"` + Liquidation/SL/TP/OCO output patterns across `tests/`. Three files carry actual fixtures impacted by the label-text + Liquidation-format changes:

| File | Affected sites | Notes |
|---|---|---|
| `tests/test_tool_enhancement.py` | 10 `"from current"` hits | Position view + open orders fixtures with detailed output assertions across SL/TP/LIMIT label sites |
| `tests/test_display_cycle.py` | 11 `"from current"` hits | Cycle-level display snapshots covering all four affected output sites |
| `tests/test_iter_tool_opt_error_metadata.py` | 4 `"from current"` hits | Recent iter goldens including OO-6 ticker-unavailable fallback path |

**Estimated ~25 existing test updates** (sum of substring-anchored label edits + any Liquidation-line precision re-baselines in the same files).

False positives identified during impact-range verification (do **not** require updates):

| File | Reason ruled out |
|---|---|
| `tests/test_alembic_roundtrip_phase1.py` | The 1 hit at line 45 is `"from current head"` in a migration comment, unrelated to tool output |
| `tests/test_fact_only_wordlist.py` | Banned-wordlist regex tests; Scenario 2 limit+OCO only sets up data, does not assert label text |
| `tests/test_iter_w2r2_next_d_goldens.py` | Only 1 substring match `assert "Liquidation: 72000" in out` — does not include distance %, so 1→2 decimal precision upgrade does not regress this assertion |
| `tests/test_toolkit_iter2.py` | Zero hits on `"from current"` and Liquidation distance line; not impacted |

Plus **~15 new tests** per §5.1.

### 5.3 Test discipline

- **Byte-equal vs substring — two regimes, applied by line type**:
  - **Byte-equal** (entire formatted line) for output lines whose values are completely fixture-controlled: Mark line, Liquidation line (happy path), Liquidation line (mark-fail fallback). Locks both wording **and** numeric format simultaneously, so a regression to last-anchored distance or to `{:.1f}%` precision fails immediately. Uses exact-string equality per project precedent (R2-8c PR #37).
  - **Substring** (`assert "<token>" in <line>`) for lines carrying variable order IDs, prices, amounts, or contracts: `_render_single_order` outputs, `_fmt_exit` outputs, `set_stop_loss` / `set_take_profit` success messages. Substring tokens are `"from last price"` (label drift guard), `"% above last price"` / `"% below last price"` (Exit Orders direction-flavored variants). Byte-equal would require fixing every variable upstream, which conflates label drift testing with downstream order-state plumbing.
- **Anchor verification on numbers**: liquidation distance and drift formula assertions use mark-anchored expectations distinct from last-anchored values (so a regression to last-anchored distance fails the test, not just a numeric tolerance).
- **No mock-fidelity gap**: every OKX-side test uses the full V5 response envelope `{"code": "0", "msg": "", "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "markPx": "<value>", "ts": "<ms>"}]}` per project's `project_iter2_mock_fidelity_lesson` memory — minimal-dict fixtures forbidden.

---

## 6. Risk and rollback

| Risk | Probability | Mitigation |
|---|---|---|
| OKX `public_get_public_mark_price` schema change | Very low | Explicit `data[0]["markPx"]` access; raises `RuntimeError` on empty data instead of silent fallback |
| Mark fetch latency adds to cycle time | Low | `asyncio.gather` parallelizes mark with ticker/balance/orders/contract_size; single endpoint per cycle |
| Sim drift=0 makes test coverage incomplete | Medium | OKX-side tests use drift fixtures (positive demo-magnitude + synthetic small negative); Sim retains zero-drift for backward fixture compatibility |
| Sim runtime output noise: Mark line always `(Last: <same>, drift +0.00%)` ~50 chars / cycle of zero-information bytes | Low | Accepted trade-off: sim/real behavioral parity > runtime token economy. SimulatedExchange could be tweaked to inject a small synthetic drift, but that would diverge from sim's "single price source" invariant (per project_sim_alignment memory). Alternative: omit drift sub-string entirely when `abs(drift_pct) < 0.01`. Deferred; can be considered if Sim cycle tokens become a constraint (current overhead negligible). |
| Existing tests need re-baselining | Medium | Estimated ~25 updates across 3 files (test_tool_enhancement / test_display_cycle / test_iter_tool_opt_error_metadata) per §5.2; byte-equal drift guards force coherent re-baseline (no silent format drift) |
| `algo_trigger_reference` indirection adds cognitive load before second exchange exists | Low–Medium | Today the attribute has one value (`"last"`) — effectively a named constant. The forward-compat value (avoiding synchronized text edits across four output sites on first Binance integration) is realized only on that future PR. Cost = 1 attribute + 1 lookup per label render; no per-call routing logic. Honest tradeoff disclosed in §3.4 principle 3 (forward-compat investment honesty). |
| OKX rate limit hit on mark endpoint | Very low | Mark endpoint sits on OKX public quota; existing `@_retry()` covers transient `NetworkError` / `ExchangeNotAvailable` |

**Rollback unit**: single PR (spec + plan + impl in three commits per `feedback_plan_doc_commit_first`). Revertable independently. The exchange-API addition is additive (no breakage to existing call sites); the four label-text changes are independently revertable per file.

---

## 7. Estimated effort

- Spec commit: ~0h (this document)
- Plan commit: ~1h
- Impl: ~6-9h
  - BaseExchange + OKX + Sim impl (`get_mark_price` + `algo_trigger_reference`): ~1.5h
  - `get_position` mark-isolated try/except + Mark line + liquidation recompute + fail-fallback line: ~2h
  - Four label-text swaps (single + OCO branches × 4 sites): ~1h
  - Docstring sync (`trader.py:133, 156, 159`) + iter6 script family校准 (`iter6_task0_capture.py` + `iter6_diag_ticker.py`, 5 edits): ~0.5h
  - New tests (`tests/test_iter_tool_opt_mark_vs_last.py`, ~15 cases): ~1.5h
  - Existing test fixture updates (~25 cases across 3 files per §5.2): ~1.5-2.5h
- Code review fix-up: ~1-2h
- **Total: ~8-12h** (matches roadmap §iter-12's original 8-12h estimate; Option C scope contraction offset by review-driven A1 mark-isolation work and verified larger test impact range)

---

## 8. Migration / backward compatibility

- **Output schema**: Mark line is additive; Liquidation distance precision +1 decimal. Both flagged by byte-equal drift guard tests — no silent regression.
- **Exchange API**: New abstract method + new class attribute on BaseExchange. Subclasses without overrides inherit defaults. No removal of existing methods or fields.
- **Wrapper signatures**: `_render_single_order` gains a required `trigger_ref: str` parameter (no default). The single production call site at `tools_perception.py:484` passes `deps.exchange.algo_trigger_reference` explicitly. Tests covering `_render_single_order` in isolation pass `"last"` directly. Required-parameter form matches project style of explicit-over-default for internal helpers.
- **No DB schema change**: pure in-process behavior; no Alembic migration.
- **No persona change**: per principle 8, tool surface carries the disclosure.

---

## 9. Out-of-scope follow-up candidates

Each item below requires its own trigger / spec session — none are committed by this iter.

| ID | Description | Trigger |
|---|---|---|
| F1 | Set `triggerPxType=mark` on project's algo order submission path | OKX implements server-side change that makes mark-trigger ergonomically equivalent, or live trading observes ≥1 51280 error on stop placement |
| F2 | Add index price as a third reference where mark is currently shown | W3+ funding-rate / basis-arb analysis surfaces ≥2 narrative occurrences referencing "index" |
| F3 | LIMIT order distance anchor to bid/ask instead of last | Narrative-grep for "limit will fill at" / "bid crossing" / "ask crossing" ≥3 occurrences in W3+ |
| F4 | Drift / funding-rate cross-reference disclosure | If post-launch agent narrative reflects basis-aware decision-making, consider a fact-only joining line (carefully — risk of advisory framing under principle 1) |
| F5 | Multi-exchange `algo_trigger_reference` override fixtures | Triggered by first non-OKX exchange integration PR |
| F6 | Mark price caching infrastructure | If a future tool consumes mark and a per-cycle multi-call pattern emerges; until then YAGNI |
| F7 | ATR-multiple anchor reconciliation (currently mixes mark numerator with last denominator on liquidation row) | Production drift consistently >0.1% for any extended period, making the ratio noise meaningful |

---

## 10. Open questions

None remaining after four review rounds:

**Round 1 — initial brainstorm strengthening (5 items)**: industry-standard cross-exchange table, `algo_trigger_reference` single source of truth, 2-decimal liquidation precision, explicit drift formula and sign convention, template-driven label text. → §1.2 / §3.1 / §4.

**Round 2 — first-pass review (11 items)**: A1 mark-isolated degradation (renamed scope) / A2 existing docstring sync / A3 dropped redundant disclosure / B1 byte-equal vs substring regime / B2 full mock envelope / B3 test impact verification / B4 test file naming / C1 precision rationale / C2 iter6 script校准 / C3 forward-compat investment honesty / C4 OCO patch snippet. → §3.1 / §3.3 / §4.2 / §5.1 / §5.2 / §5.3 / §6.

**Round 3 — second-pass review (10 items)**: A1 drift fixture sign aligned to demo direction / A2 `_place_algo` stale claim same-PR fix / A3 `iter6_diag_ticker.py` drift formula alignment / A4 §5.2 false positive pruning + ~25 estimate / B1 §0 vs §3.1 contradiction resolved / B2 §6 estimate sync / B3 §10 self-honesty (this section) / C1 Liquidation gate `current_price > 0` → `mark_price > 0` / C2 Mark line `ticker.last <= 0` fallback / C3 Sim drift=0 runtime noise trade-off. → §0 / §3.1 / §5.1 / §5.2 / §6 / §7.

**Round 4 — third-pass review (7 items)**: (1) §3.1 vs §6 serial-vs-parallel contradiction resolved — mark fetch is the 6th `asyncio.gather` member via `_safe_mark_price` helper, zero latency increment, parallel pattern to `_safe_ohlcv`. (2) `SimExchange` → `SimulatedExchange` class-name校准 throughout (8 occurrences) to match `simulated.py:59`. (3) Drift-formula convention swap notes — magnitude shift ~0.03pp on top of sign flip, documented in §3.1 sub-(d1)+(d2) and the demo-magnitude test docstring. (4) Binance USDT-M API default = `CONTRACT_PRICE` (last) — §2 table corrected; §3.4 forward-compat example switched to Bybit V5 (no silent default) + Hyperliquid/dYdX (oracle/mark uniform). (5) `mark_price` initial value explicit (`0.0` from `_safe_mark_price` on failure; downstream `mark_price > 0` gate detects). (6) Sim `fetch_ticker` idempotency note on `SimulatedExchange.get_mark_price` row. (7) Stale-script row split into (d1) line-81 formula + (d2) line-82 print label; count standardized to "4 logical sub-changes / 5 edit actions". → §0 / §2 / §3.1 / §3.3 / §3.4 / §5.1 / §7.

Wrapper docstring strategy chose Approach X (sync existing concrete "current" → "last price" in trader.py; add only the **mark-anchor disclosure sentence** on get_position; no new disclosure on the three `algo_trigger_reference`-driven label sites) over abstract-phrasing variants — build-time docstring describes current OKX-specific concrete reference; runtime output is single-source-driven; two layers intentionally distinguished.
