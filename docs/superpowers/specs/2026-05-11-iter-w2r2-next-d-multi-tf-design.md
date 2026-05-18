# Iter w2r2-next-d — Multi-TF 视图统一治理

**Date**: 2026-05-11
**Iteration**: w2r2-next-d (Iter 1 of sim #8 W2 tool optimization roadmap)
**Type**: Design spec
**Source brainstorm**: 7-question brainstorm session 2026-05-11
**Empirical foundations**: `scripts/verify_ohlcv_semantics_v2.py` (data captured 2026-05-10)

---

## 0. One-minute summary

Path-reverse the three multi-timeframe perception tools so the agent's mental model and the tool surface align. sim #8 narrative shows the agent already thinks in terms of "multi-TF alignment" (≥6 explicit references), but the tool path forces 100% of three-call cycles through `get_market_data ×3` because `get_multi_timeframe_snapshot` lacks K-line data and the agent can't close the loop on it.

This spec restructures the three tools' signal boundaries:

- **MTS** becomes the cycle-opening primary entry — adds K-line snippet, MA values column, count-based MA structure summary, authoritative ticker-as-of timestamp.
- **GMD** retreats to single-timeframe depth tool — removes the docstring nudge that pushed agents into multi-call patterns, lowers default `candle_count` from 50 to 30 (matching empirical 60% usage), adds OHLCV anomaly markers (B3) and a last-5-vs-prior-5 period summary (B4).
- **HTF** becomes a list-form tool covering long-term structural anchors — accepts `timeframes: list[Literal[...]]`, adds N6 G1-G5 enrichment (volume regime, MA slope, MA stack, ATR regime, monthly-tf adapted MA periods).

Cross-cutting work covers a **wrapper-docstring "Related perception tools" tail** that introduces the three-tool routing at the LLM-facing docstring layer (Layer-1 in `persona.py` is intentionally untouched — see §6.1 for the rationale and PR #25 discipline), comprehensive docstring rewrites (N1-N7+N12+N13), data-source unification across all three tools (`ticker.last` for live state, closed-bar series for indicator inputs), Liquidation field deduplication (F-P2), Bollinger label verbosing (F-O2), Volume label semantic fix (F-O3), and **six** cross-tool drift-guard tests.

---

## 1. Empirical foundations

### 1.1 Source data

- sim #8: 178 cycles / 19.2h / 14.36M tokens / 1818 tool calls
  - DB: `data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`
  - Session log: `logs/session_8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3.log`
- Brainstorm prep: `.working/sim8-w2-multi-tf-deep-dive.md`, `.working/sim8-w2-tool-ergonomics.md`, `.working/sim8-w2-tool-optimization-roadmap.md`
- OHLCV semantics verification: `scripts/verify_ohlcv_semantics_v2.py` (31-sample multi-window, authoritative source for the empirical claims in §1.3). `scripts/verify_ohlcv_semantics.py` (v1, single-snapshot) is retained as a historical precursor and quick smoke-check; v2 supersedes it for any verification or audit purpose.

### 1.2 Behavioral signals from sim #8

| Signal | Datum |
|---|---|
| GMD calls / cycle | 511 calls / 178 cycles = 2.87 avg |
| 3-call GMD cycles | 129/178 (72%); 100% are `(1m, 5m, 1h)` triples (only ordering varies) |
| MTS-after-GMD cycles | 44/44 (100%); zero MTS-only cycles |
| MTS default usage | 41/44 (93%) use the default `["5m","1h","4h","1d"]` |
| HTF args | 4h: 48 (75%), 1d: 16 (25%), 1w / 1M: 0 |
| HTF same-cycle pairs | 5 cycles call (4h, 1d) together; 100% of pairs |
| GMD `candle_count` distribution | `=20` 35%, `=30` 17%, `=10` 16%, `=50` 9%; default 50 captures 8.8% of actual usage |
| Agent narrative `"multi-TF alignment"` references | ≥6 explicit references across multiple cycles, used as conviction-level concept |
| Agent narrative `"stale data"` confusion | ≥8 cycles citing MTS-vs-ticker price drift |

### 1.3 OHLCV semantics — empirical verdicts (verify_ohlcv_semantics_v2)

Captured 2026-05-10 19:41–19:43 UTC, 31 samples on `BTC/USDT:USDT @ 1m`, 2 candle rotations observed.

| Claim | Verdict | Quantitative result |
|---|---|---|
| **A1**: `ticker.last` ≈ `df["close"].iloc[-1]` at the same fetch | PASS within sub-bps drift floor | 26/31 zero drift; 5/31 sub-bps drift; max abs drift 0.0123 bps (= 1 USDT cent on $81k) |
| **A2**: `df["close"].iloc[-2]` is a frozen snapshot at the previous candle's close, distinct from live `ticker.last` | PASS at long tfs (v1: 1d -99 bps); INCONCLUSIVE at 1m flat windows | v1 1d drift -99 bps; v2 1m max 1.66 bps in flat 90s window |
| **A3**: `df["close"].iloc[-1]` is the in-progress candle's continuously-updating close, frozen at official close moment, then surfaced as `iloc[-2]` | PASS (sampling-discretization caveat) | 2/2 timestamp match on rotation; 1/2 close-value match (the mismatch reflects trades between sample and rotation, not a refutation) |
| **A4**: indicators on closed-only inputs are temporally stable; indicators on full-df inputs drift with each trade | PASS | closed-only MA(5) drift 0.0000; full-df MA(5) drift 0.0200 in same candle window |

### 1.4 Implications for the spec

- Choosing `ticker.last` over `df["close"].iloc[-1]` for live-state fields is a **semantic-clarity** choice (canonical "live trade price" naming), not an accuracy choice — they are functionally equivalent within sub-bps.
- Computing indicators on closed-only inputs (`df.iloc[:-1]`) is the **load-bearing** refactor: it provides temporal stability that makes per-cycle facts reproducible. This is the dominant ROI of the data-source work.
- Showing in-progress candles in K-line tables produces unreliable anomaly markers (volume / range incomplete) — closed-only K-line tables eliminate the false-positive class.

---

## 2. Architecture and scope

### 2.1 Core proposition

**Re-partition the three tools' signal boundaries so the agent's mental path and the tool path align.** The agent's natural cycle structure is:

```
Cycle entry → MTS (regime + alignment overview, single call)
            ↓ branch on demand
            ├─ candle pattern / indicator depth → GMD (single tf)
            └─ long-term structure / MA200 / monthly cycle → HTF (list of higher tfs)
```

### 2.2 Signal authority matrix

Scope notation in matrix: "MTS tfs" = MTS default `["5m", "1h", "4h", "1d"]`; "HTF tfs" = HTF default `["4h", "1d"]` (and optionally `1w`/`1M`). Some signals are computed in both MTS and HTF for shared timeframes (4h, 1d) — see §2.2.1 for the intentionality argument and the algorithm-lock invariant + end-to-end drift-guard verification that together prevent value drift between the two views.

| Signal | MTS | GMD | HTF |
|---|---|---|---|
| `Last:` (live trade price) | Authoritative — header with ticker fetch timestamp | Header | Header |
| Cross-TF MA fast-vs-slow direction summary | Authoritative — count-based fact line | — | — |
| Per-TF momentum vs primary MA (%) | Yes (MTS tfs) | — | — |
| Per-TF Structure: fast MA + slow MA raw values + comparison | Yes inline (MTS tfs); 5m/1h MAs are MTS-only authoritative; 4h/1d MA50/MA200 raw shared with HTF (§2.2.1) | Yes — single-tf raw with full historical context (RSI/MACD/BB series + 30 OHLCV) | Authoritative for HTF tfs (4h/1d/1w/1M) — also surfaces MA100 + slope + MA stack |
| Per-TF Volatility: ATR(14) % + ratio vs 20-period ATR avg | Yes (MTS tfs); 4h/1d shared with HTF (§2.2.1) | Indirectly via ATR(14) line in Market Context | Authoritative for HTF tfs — also surfaces ATR raw value |
| MTS Range pos within 20-bar high-low (%) | Authoritative (MTS-only window) | Indirectly via 30-candle High-Low line | — |
| HTF Range pos within 100-period high-low (%) | — | — | Authoritative — distinct 100-period window with bars-ago + candle open timestamp |
| Last 3 closed candle closes per TF | Authoritative (MTS tfs) | (full-table mode) | — |
| RSI / MACD / BB / Volume ratio | — | Authoritative | — |
| Full OHLCV table with anomaly markers | — | Authoritative — N candles, oldest-first, closed-only | — |
| Period summary (last 5 vs prior 5 closed candles) | — | Authoritative | — |
| MA stack (3-way comparison) | — | — | Authoritative |
| MA slope vs 10 bars ago | — | — | Authoritative |
| 20-period band (HTF tfs) | — | — | Authoritative |
| Last bar volume + 20-bar SMA ratio (HTF tfs) | — | — | Authoritative |

### 2.2.1 Intentional signal overlap and the algorithm-lock invariant

For shared timeframes (specifically 4h and 1d when MTS default and HTF default intersect), the following signals appear in **both** MTS and HTF outputs:

- **MA50 raw value** (Structure column in MTS for 1h/4h/1d; first MA line in HTF for 4h/1d/1w/1M). Overlap exists only at 4h/1d.
- **MA200 raw value** (Structure column in MTS for 1h/4h/1d; third MA line in HTF for 4h/1d/1w; not in HTF 1M which uses MA60 instead per §5.4). Overlap exists only at 4h/1d.
- **ATR(14) percent of price + ratio vs 20-period ATR average** (MTS for all of `["5m","1h","4h","1d"]`; HTF for `["4h","1d","1w","1M"]`). Overlap exists only at 4h/1d.

Note: 1h is **MTS-only** (HTF intentionally excludes 1h per §5.6); 1w and 1M (and the MA100 row for HTF tfs) are **HTF-only** in their authoritative form. The invariant below applies strictly to the 4h/1d shared signals.

This overlap is **intentional**, not a violation of "signal uniqueness" (principle 3 in `docs/superpowers/principles/tool-design-principles.md`). Each tool surfaces these signals in service of a different agent mental flow:

- **MTS** surfaces them inline in a per-tf row layout, optimized for cross-tf alignment scanning at cycle entry — agent gets enough fact (raw MA + comparison + ATR ratio) to decide whether the regime warrants drilling deeper.
- **HTF** surfaces them in a tf-section layout with adjacent long-term signals (MA100, slope, MA stack, 100-period range), optimized for structural depth on demand.

To prevent value drift between the two views, both tools compute these signals through **the same algorithm primitives** (see §6.4):

- **SMA formula**: every shared MA is computed as `df.iloc[:-1]["close"].rolling(n).mean().iloc[-1]` (closed-bar series via `_closed_bars`, identical pandas call). Pandas `rolling(n).mean()` is deterministic, so identical inputs produce identical outputs by construction.
- **ATR algorithm lock**: every shared ATR(14) and its 20-period rolling-mean ratio is computed via `_atr_series(df_closed, period=14)`, which calls `pandas_ta.atr(..., mamode="rma")` explicitly — locking Wilder's smoothing against future pandas_ta default changes (see §6.4.2).
- **Live price source**: every `Last:` field reads through `_live_price(ticker)` so both tools sample `ticker.last` rather than mixing live and closed-bar sources.

**Scope of the invariant**: given the same `df_closed` and `ticker` inputs, MTS-rendered and HTF-rendered MA50 / MA200 / ATR-ratio values are equal because the underlying primitives are deterministic and algorithm-locked. The invariant is **verified end-to-end** by the drift-guard test `test_mts_htf_overlap_values_match` (§7.1): the test invokes both `get_multi_timeframe_snapshot` and `get_higher_timeframe_view` against the same fixture OHLCV at 4h and 1d, extracts the rendered MA50 / MA200 / ATR-ratio numbers via regex, and asserts equality. End-to-end verification catches both compute drift (a primitive call deviates) and render-side bugs (e.g., a typo that surfaces MA100 in the MA50 slot).

**Scope of what is NOT guaranteed**: MTS and HTF are independent pydantic-ai tool invocations with independent OHLCV fetches; in production runs the inputs may differ by milliseconds. Divergence sources are reduced to "fetch-time OHLCV delta only" — at runtime, the only condition that can produce non-equal rendered values is a fetch pair straddling a candle close boundary, where one tool's `df_closed` includes a bar the other's does not (one MA-window contribution). See §9 row "MTS / HTF fetches straddling a candle close boundary".

### 2.3 Scope envelope

**In scope** (single PR):

- Three-tool refactor (MTS upgrade, GMD retreat, HTF list-form) per Sections 3-5 below
- N6 G1-G5 enrichment on HTF
- Brainstorm-confirmed adjacent issues (deep-dive doc `.working/sim8-w2-multi-tf-deep-dive.md` §11.1): B3 anomaly candle markers (GMD), B4 period summary (GMD), B5 volatility regime (folded into MTS per-tf ATR ratio)
- N1-N7 + N12 + N13 docstring and label rewrites
- F-O2 Bollinger labels, F-O3 Volume label, F-P2 Liquidation deduplication
- Wrapper-docstring "Related perception tools" tail on MTS / GMD / HTF (LLM-facing cross-tool routing via pydantic-ai griffe sniff; Layer-1 in `persona.py` untouched per §6.1)
- Drift-guard and integration tests
- Empirical-foundations script archival

**Out of scope** (independent specs / mini-PRs):

- `evaluate_trade_setup` (R:R / risk preview tool) — DEFER R2-Next-I
- Alert tool family — independent R2-Next-E spec
- `set_next_wake` event-driven mode — independent R2-Next-H spec
- `get_derivatives_data` OI rate-of-change — independent R2-Next-G spec
- `adjust_leverage` deletion — OOS mini-PR
- Memory cleanup (N8 stale) — OOS
- Cross-tool OHLCV fetch caching (MTS + HTF both pulling 4h/1d in the same cycle re-fetches the same OHLCV) — OOS for this spec; future optimization PR if same-cycle fetch overlap becomes a measured latency/cost concern in W3+

**Iteration code note** — `project_n6_htf_hardening` memory previously flagged N6 as a candidate for `R2-Next-C` (~50-line standalone PR). Because path reversal (this spec) needs HTF list-form to land in lockstep with MTS upgrade (overlap signals in §2.2.1), N6 G1-G5 is **absorbed into R2-Next-D** rather than shipped as a separate `R2-Next-C` PR. The "next-C" code is therefore retired; downstream tracking should follow `R2-Next-D` and the post-merge memory updates.

---

## 3. MTS upgrade

### 3.1 Output mockup

```
=== Multi-TF Snapshot (BTC/USDT:USDT) ===
Last (ticker @ 14:23:08 UTC): 81870.50
MA fast-vs-slow per tf: 5m below | 1h above | 4h above | 1d below
Columns: Momentum (live ticker vs primary MA, %) | Structure (fast MA value vs slow MA value, with comparison) | Volatility (ATR % of price; ratio vs 20-period ATR avg) | Range pos (live close within 20-bar high-low; 0%=Low, 100%=High) | Last 3 closed candle closes

[5m]  Mom -0.3% (vs MA20) | MA20: 81960 < MA50: 82150 | ATR 0.15% (20p avg 0.18%, 0.83×) | Range pos 65%
      Last 3 closes (closed @ 2026-05-11 14:20 UTC): 81870→81848→81870

[1h]  Mom +1.2% (vs MA50) | MA50: 80900 > MA200: 78400 | ATR 0.85% (20p avg 0.78%, 1.09×) | Range pos 78%
      Last 3 closes (closed @ 2026-05-11 14:00 UTC): 81883→81972→81870

[4h]  Mom +5.5% (vs MA50) | MA50: 79200 > MA200: 76200 | ATR 1.92% (20p avg 1.85%, 1.04×) | Range pos 88%
      Last 3 closes (closed @ 2026-05-11 12:00 UTC): 79200→80100→81870

[1d]  Mom +12.1% (vs MA50) | MA50: 73000 < MA200: 81100 | ATR 2.61% (20p avg 1.85%, 1.41×) | Range pos 99%
      Last 3 closes (closed @ 2026-05-10 00:00 UTC): 78500→80100→81870
```

### 3.2 Field semantics

| Field | Source | Notes |
|---|---|---|
| `Last (ticker @ T UTC):` | `ticker.last`, T = wall-clock at fetch | Authoritative live price; sub-bps drift vs `df["close"].iloc[-1]` is acceptable per A1 |
| `MA fast-vs-slow per tf:` | Per tf, count `fast_MA > slow_MA`; list tfs above and below | Count-based fact, no "ALIGNED-UP" / "MIXED" evaluation labels |
| `Mom X% (vs MAn)` | `(ticker.last - primary_MA) / primary_MA × 100%`; primary_MA computed on closed-bar series | Inline reference label `(vs MAn)` so the agent knows which MA the percentage is against |
| `MAa: X < MAb: Y` | Both MA values raw (closed-bar series); operator from {`<`, `>`, `=`, `≈`} with 0.1% tolerance for `≈` | Colon format aligns with HTF; raw values inline for SL/TP anchor needs |
| `ATR X% (20p avg Y%, Z×)` | `ATR(14) / ticker.last × 100%`; 20-period rolling mean of ATR(14) series | Replaces the prior raw-only ATR display; ratio is fact-only volatility regime |
| `Range pos X%` | `(ticker.last - low20) / (high20 - low20) × 100%`; low20/high20 from the **last 20 closed bars** (i.e., `df.iloc[:-1].iloc[-20:]`). **Out-of-bounds behavior**: live ticker may exceed [low20, high20] when a breakout occurs (closed-bar window no longer contains the breakout extreme). The formula is rendered **without clamping** — values >100% or <0% are returned as fact (`Range pos 105%` / `Range pos -2%`) so the agent receives an explicit "breakout beyond closed-bar window" signal rather than a clamped misread. | Anchor explained in Columns header; "20-bar" in the header refers to closed bars |
| `Last 3 closes (closed @ T UTC): a→b→c` | `df["close"].iloc[-4:-1]`; T = `df["timestamp"].iloc[-2] + tf_seconds` | Closed-only series; T is the most recent candle's official close moment |

### 3.3 docstring

```python
"""Multi-timeframe snapshot: ticker (authoritative current price) plus
a cross-tf MA fast-vs-slow direction line plus per-tf rows containing
momentum (live ticker vs primary MA, %), fast-vs-slow MA structure
(MA names with raw values and comparison operator), volatility (ATR
% of price and its ratio vs 20-period ATR average), range position
(live close within the last 20-bar high-low, 0% = low / 100% = high),
and the most recent 3 closed candle closes with the close timestamp.

All moving averages are simple moving averages (SMA) computed on the
closed-bar series only (excluding the in-progress bar). Per-tf MA
values are rendered inline in the Structure column; the Momentum
column shows the percentage from live ticker to the primary MA on
each tf.

Args:
    tfs: List of CCXT timeframes. Default ["5m", "1h", "4h", "1d"].

Example call:
    get_multi_timeframe_snapshot()
Example output:
    === Multi-TF Snapshot (BTC/USDT:USDT) ===
    Last (ticker @ 14:23:08 UTC): 81870.50
    MA fast-vs-slow per tf: 5m below | 1h above | 4h above | 1d below
    Columns: ...

    [5m]  Mom -0.3% (vs MA20) | MA20: 81960 < MA50: 82150 | ATR 0.15% (20p avg 0.18%, 0.83×) | Range pos 65%
          Last 3 closes (closed @ 2026-05-11 14:20 UTC): 81870→81848→81870
    ... (3 more tf rows)

Degradation: per-TF "insufficient data" or "temporarily unavailable";
overall returns header-only error if all TFs fail or ticker fetch fails.
"""
```

### 3.4 Token budget

Per-tf row including Last 3 closes line ≈ 60 tokens; header + ticker + MA-direction + Columns header ≈ 140 tokens; default 4 tfs ≈ 380-450 tokens (vs status quo ~250 — 1.5-1.8× expansion). Per Q10 acceptance criteria, this expansion is offset by frequency increase from 25% to ≥60% of cycles, displacing GMD ×3 cost.

---

## 4. GMD changes

### 4.1 Output mockup

```
=== Ticker (BTC/USDT:USDT @ 14:23:08 UTC) ===
Last: 81870.50 | Bid: 81870.40 | Ask: 81870.60
24h High: 82500.00 | 24h Low: 81000.00 | 24h base vol: 1234.56

=== Technical Indicators (5m) ===
RSI(14): 47.2
MACD(12,26,9): -12.5 | Signal: -8.3 | Hist: -4.2
BB(20,2): Upper 81960 | Middle 81727 | Lower 81494 (position: 81%, 0%=Lower / 100%=Upper)

=== Market Context ===
ATR(14): 122.50 (0.15% of price, 5m candles)
Last bar vol: 142.3 (1.10× SMA(20) avg)
30-candle High-Low: 81450 — 82150

=== Recent Candles (5m, last 30, oldest-first by row) ===
Time (open UTC)   Open       High        Low      Close        Vol  Markers
12:05         81610.00   81640.00   81595.00   81625.00      118.4
12:10         81625.00   81655.00   81605.00   81632.50      109.7
...
14:00         81750.00   81970.00   81720.00   81920.00      378.2  vol↑ range↑
14:05         81920.00   81940.00   81880.00   81895.00      152.6
14:10         81895.00   81910.00   81855.00   81870.00      245.3  vol↑
14:15         81870.00   81895.00   81865.00   81883.50      125.6

=== Period summary (last 5 closed candles vs prior 5 closed candles) ===
Avg vol:            last 5 178.6 / prior 5 132.4 (1.35×)
Avg range (H-L):    last 5 38.2 / prior 5 24.8 (1.54×)
Net Δclose:         last 5 -25.0 USDT / prior 5 +120.0 USDT
```

### 4.2 Changes vs status quo

| # | Change | Source issue |
|---|---|---|
| 1 | `candle_count` default 50 → 30. Empirical (sim #8): `=20` 35%, `=30` 17%, `=10` 16%, `=50` 9%. The new default `30` is **on the high end of the modal cluster `[10, 20, 30]` (cumulatively 68%)**; the prior default 50 captured only 9% of calls (i.e., the agent had to override the default in 91% of GMD calls). | Q4 / N10 |
| 2 | docstring rewritten: drop "Use multiple timeframes to build conviction" reverse-pressure (N5), drop "Total output ~1000-1200 tokens" meta-info (N1), drop "candle_count=20 for quick check" timing nudge, add example call → output (N7) | N1, N5, N7 |
| 3 | `Markers` column added to OHLCV table: `vol↑` if bar volume > 2× SMA(20) of bar volumes; `range↑` if (high - low) > 2× ATR(14); empty otherwise. Closed-only — no in-progress markers (avoids false positives on partial-bar data) | B3 |
| 4 | Period summary section after the OHLCV table: avg volume, avg range (H-L), net Δclose for the last 5 closed candles vs the prior 5 closed candles | B4 |
| 5 | Bollinger labels: `BB(20,2): Upper X | Middle Y | Lower Z (position: P%, 0%=Lower / 100%=Upper)` — full word labels (no abbreviation lookup), period (20,2) explicit, anchor explicit | F-O2 |
| 6 | Volume label: `Last bar vol: X (Y× SMA(20) avg)` (was `Volume: X (Y× avg)`) — `Last bar vol` semantic, SMA(20) period explicit, Unicode `×` | F-O3 |
| 7 | Header timestamp: `=== Ticker (BTC/USDT:USDT @ T UTC) ===`; `Last:` replaces `Price:` (ccxt field alignment, drops "Current" evaluation-flavored qualifier) | N12, N13 |
| 8 | Ticker volume label: `24h base vol: X` (was `Volume: X`) — disambiguates 24h aggregate vs bar volume | fact label |
| 9 | OHLCV table sort: explicit `oldest-first by row` in header; `Time (open UTC)` column header | LLM recency bias + reading-flow alignment |
| 10 | Indicator inputs and OHLCV display use closed-only series (`df.iloc[:-1]`) — prevents partial-bar drift on indicators and false-positive markers on in-progress rows | A4 empirical |
| 11 | (Negative-space decision) **Intentionally NOT adding** `timeframes: list[...]` parameter to GMD signature — Q7 brainstorm decision: GMD remains a single-tf depth tool; multi-tf path is served by MTS (§3) per the path-reversal architecture (§2.1). Adding a list parameter would give the agent a fallback that bypasses MTS, undermining the path reversal. The agent's wrapper docstring + "Related perception tools" tail (§6.1) carries the routing signal, not a schema constraint. | Q7 |

### 4.3 Marker thresholds and semantics

| Marker | Trigger |
|---|---|
| `vol↑` | bar volume > 2× SMA(20) of closed-bar volumes |
| `range↑` | (high - low) > 2× ATR(14) on closed bars |
| (empty) | neither tripped |

Markers are upside-only by design; the agent narrative attacks anomaly-high signals, not below-average bars. Documented in docstring.

### 4.4 Period summary fields

| Field | Computation |
|---|---|
| `Avg vol: last 5 X / prior 5 Y (Z×)` | `mean(df_closed["volume"].iloc[-5:])` vs `mean(df_closed["volume"].iloc[-10:-5])`; `Z = X / Y` |
| `Avg range (H-L): last 5 X / prior 5 Y (Z×)` | `mean((df_closed["high"] - df_closed["low"]).iloc[-5:])` vs `... iloc[-10:-5]`; `Z = X / Y` |
| `Net Δclose: last 5 X USDT / prior 5 Y USDT` | `df_closed["close"].iloc[-1] - df_closed["close"].iloc[-5]` (last window); `df_closed["close"].iloc[-6] - df_closed["close"].iloc[-10]` (prior window). **Definition lock**: "Net Δclose" measures the difference between the **first and last closed-candle closes within a 5-candle window** — i.e., 4 intervals over 5 candles, not 5 intervals. Both windows use the same 4-interval convention so the last/prior ratio is meaningful. **Window adjacency**: the two windows are **contiguous and non-overlapping** — together they span exactly 10 closed bars (indices -1 to -10), with no gap and no shared bar. |

Fact-only — no "trend strengthening" / "reversal" / "exhaustion" labels. Agent self-judges.

### 4.5 docstring

```python
"""Single-timeframe market data: ticker, technical indicators
(RSI / MACD / BB / ATR / volume ratio), market context (ATR with
percent of price, last-bar volume with average ratio, display-window
range), the most recent N closed candles in OHLCV table form with
anomaly markers, and a period summary comparing the last 5 vs prior
5 closed candles (avg volume, avg range, net Δclose).

All indicators are computed on the closed-bar series only (excluding
the in-progress candle). The OHLCV table also shows closed bars only
and is sorted oldest-first by row.

Args:
    symbol: Trading symbol. Defaults to session symbol.
    timeframe: CCXT timeframe ("1m", "5m", "1h", etc.). Defaults to
        session primary timeframe.
    candle_count: Number of closed candles in the OHLCV table.
        Default 30. Range 10-80 (capped by exchange API).

Markers in OHLCV table (upside-only thresholds):
    "vol↑"   — bar volume > 2× SMA(20) of bar volumes
    "range↑" — bar range (high - low) > 2× ATR(14)
    Empty    — neither threshold tripped.

Time column shows candle open in UTC.

Example call:
    get_market_data(timeframe="5m", candle_count=30)
Example output:
    === Ticker (BTC/USDT:USDT @ 14:23:08 UTC) ===
    Last: 81870.50 | Bid: 81870.40 | Ask: 81870.60
    ...
    === Recent Candles (5m, last 30, oldest-first by row) ===
    Time (open UTC)   Open ... Vol     Markers
    14:20         ...         245.3   vol↑
    ...
    === Period summary (last 5 closed candles vs prior 5 closed candles) ===
    Avg vol:            last 5 178.6 / prior 5 132.4 (1.35×)
    Avg range (H-L):    last 5 38.2 / prior 5 24.8 (1.54×)
    Net Δclose:         last 5 -25.0 USDT / prior 5 +120.0 USDT
"""
```

---

## 5. HTF list-form + N6 G1-G5

### 5.1 Output mockup

```
=== Higher Timeframe View (BTC/USDT:USDT @ 14:23:08 UTC) ===
Last: 81870.50

[4h] (last closed candle: open 2026-05-11 08:00 UTC)
  MA50:  79200.00  (price vs MA: +3.4%; MA slope vs 10 bars ago: +0.8%)
  MA100: 78400.00  (price vs MA: +4.4%; MA slope vs 10 bars ago: +0.5%)
  MA200: 76200.00  (price vs MA: +7.4%; MA slope vs 10 bars ago: +0.2%)
  MA stack: MA50 > MA100 > MA200
  100-period High: 82800.00  (32 bars ago, candle open 2026-05-06 00:00 UTC)
  100-period Low:  68500.00  (87 bars ago, candle open 2026-04-26 20:00 UTC)
  Range pos (within 100-period): 94%  (0%=Low, 100%=High)
  20-period High: 82500.00 / Low: 78900.00 / range width: 4.6% (= (High-Low)/Low)
  Last bar vol (base): 1521.6  (5.0× SMA(20) avg)
  ATR(14): 1572.30  (1.92% of price; 1.04× vs 20-period ATR(14) avg)

[1d] (last closed candle: open 2026-05-10 00:00 UTC)
  MA50:  73000.00  (price vs MA: +12.1%; MA slope vs 10 bars ago: +2.1%)
  MA100: 67800.00  (price vs MA: +20.7%; MA slope vs 10 bars ago: +1.5%)
  MA200: 81100.00  (price vs MA: +0.9%;  MA slope vs 10 bars ago: +0.3%)
  MA stack: MA100 < MA50 < MA200
  100-period High: 96500.00  (45 bars ago, candle open 2026-03-26 00:00 UTC)
  100-period Low:  47100.00  (95 bars ago, candle open 2026-02-04 00:00 UTC)
  Range pos (within 100-period): 70%  (0%=Low, 100%=High)
  20-period High: 83500.00 / Low: 78200.00 / range width: 6.8% (= (High-Low)/Low)
  Last bar vol (base): 89432.0  (1.2× SMA(20) avg)
  ATR(14): 2138.31  (2.61% of price; 1.41× vs 20-period ATR(14) avg)
```

### 5.2 Changes vs status quo

| # | Change | Source issue |
|---|---|---|
| 1 | Signature `timeframes: list[Literal["4h","1d","1w","1M"]]` with default `["4h", "1d"]` (1h intentionally excluded — see §5.6) | Q5 / F-T2 |
| 2 | Header `Last:` (ticker.last, authoritative) replaces `Current Price:` (was OHLCV `iloc[-1].close`) — three-tool data-source unification | N12, N13, A1 |
| 3 | Per-tf header marks last closed candle with full date and open time: `[4h] (last closed candle: open 2026-05-11 08:00 UTC)` | fact stamping |
| 4 | Each MA line adds slope vs 10 bars ago: `(price vs MA: +X%; MA slope vs 10 bars ago: +Y%)` | N6 G2 |
| 5 | After the MA list, a `MA stack:` line shows the 3-way comparison: `MA50 > MA100 > MA200` (or other ordering); `≈` operator when relative diff < 0.1% | N6 G3 |
| 6 | 100-period High/Low: keep the existing `N bars ago` anchor and **append** a `candle open T UTC` full-date stamp, e.g., `(32 bars ago, candle open 2026-05-05 16:00 UTC)`. Current code (`_htf_ago_fmt` at `tools_perception.py:915-916`) renders only `N bars ago` without a real date — the addition gives the agent an absolute time anchor without removing the existing relative one. | fact stamping |
| 7 | `Range pos (within 100-period): X% (0%=Low, 100%=High)` adds anchor | fact stamping |
| 8 | 20-period band: explicit anchor `range width: 4.6% (= (High-Low)/Low)` | fact stamping |
| 9 | New `Last bar vol (base): X (Y× SMA(20) avg)` line — bar volume regime fact | N6 G1 |
| 10 | New `ATR(14): X (Y% of price; Z× vs 20-period ATR(14) avg)` line | N6 G4 |
| 11 | 1M timeframe uses MA(12, 24, 60) (= 1y / 2y / 5y monthly) instead of MA(50, 100, 200). **No sim #8 trigger** — sim #8 has 0 calls on 1M; this is a design-level adaptation per brainstorm Q9. Reference for monthly-cycle MA convention: Bitcoin Magazine 2-Year MA Heatmap (MA24); Glassnode multi-year cycle analysis; LookIntoBitcoin's 200-week MA framing (= MA200 on weekly, distinct from MA200 on monthly). MA200 on monthly would span ≈16.7 years, approaching the edge of BTC's full price history (~192 monthly candles since 2010) — meaning MA200 effectively averages almost every monthly close that has ever existed, eroding its discriminating power as a long-term anchor. **W3+ trigger**: if W3 surfaces ≥1 cycle with 1M tool call and the agent narrative reads MA-distance signals non-trivially, validate (12, 24, 60) choice or revisit. | N6 G5 / Q9 |
| 12 | All indicator inputs use `df.iloc[:-1]` (closed-only); `last_price = ticker.last` for live-state percentages | A4 empirical |
| 13 | docstring rewritten: drop "Output is fact-only per spec §3.1..." design-contract meta (N2), drop "~250 tokens total" budget meta (N3), upgrade `long-period MAs` → `long-term structural view` (N6 wording) | N2, N3, N6 |

### 5.3 MTS vs HTF: MA periods for 1w/1M differ intentionally

MTS continues to use its existing `MULTI_TF_STRUCTURE_MAS` table for 1w/1M (`(20, 50)` with `(short-structure)` marker — degraded due to weekly/monthly history shortage in the MTS 20-bar window context). HTF for the same 1w/1M uses the periods defined in §5.4 below (`(50, 100, 200)` for 1w, `(12, 24, 60)` for 1M). If a user passes 1w or 1M to MTS, they will see MA20/MA50; if they pass 1w or 1M to HTF, they will see MA50/100/200 or MA12/24/60.

This is **intentional**: the two tools serve different mental flows (alignment scan vs long-term structural depth) and the MA periods are tuned to each flow. The §2.2.1 algorithm-lock invariant deliberately does **not** extend to 1w/1M — those signals are not "the same fact rendered twice" but two different period choices. Agent docstrings (§3.3 for MTS, §5.7 for HTF) describe the period choices per tool so the agent can interpret the differing values as design, not bug.

### 5.4 Per-TF MA period table

```python
HTF_MA_PERIODS: dict[str, tuple[int, int, int]] = {
    "4h": (50, 100, 200),
    "1d": (50, 100, 200),
    "1w": (50, 100, 200),
    "1M": (12, 24, 60),  # 1y / 2y / 5y in monthly tf — crypto industry convention
}
```

For 1M, the section header explicitly notes the period choice:

```
[1M] (last closed candle: open 2026-04-30 00:00 UTC; MA periods 12/24/60 = 1y/2y/5y monthly — adapted for crypto-industry monthly cycle conventions)
```

### 5.5 Field semantics

| Field | Computation |
|---|---|
| `Last:` | `ticker.last` — same source across MTS / GMD / HTF (see §6.4) |
| `(last closed candle: open T UTC)` | `df["timestamp"].iloc[-2]` formatted with full date |
| `MAn: X (price vs MA: ±Y%; MA slope vs 10 bars ago: ±Z%)` | MA = `df.iloc[:-1]["close"].rolling(n).mean().iloc[-1]`; `Y = (last_price - MA) / MA × 100%`; slope = `(MA_now - MA_10_bars_ago) / MA_10_bars_ago × 100%` on closed-bar series |
| `MA stack: MAa > MAb > MAc` | sort the three MAs; render with `<` / `>` / `≈`; `≈` when relative diff < 0.1% |
| `100-period High: X (N bars ago, candle open T UTC)` | `df.iloc[:-1].iloc[-100:]["high"].max()`; bar-index of max; corresponding `df["timestamp"]` rendered as full date |
| `Range pos (within 100-period): X% (0%=Low, 100%=High)` | `(last_price - low100) / (high100 - low100) × 100%`. Same out-of-bounds policy as MTS Range pos (§3.2): rendered **without clamping**; values >100% / <0% surface as explicit breakout signal beyond the closed-bar window |
| `20-period High: X / Low: Y / range width: Z% (= (High-Low)/Low)` | unchanged from current code, with explicit anchor formula |
| `Last bar vol (base): X (Y× SMA(20) avg)` | `df["volume"].iloc[-2]` divided by `df.iloc[:-1]["volume"].rolling(20).mean().iloc[-1]` |
| `ATR(14): X (Y% of price; Z× vs 20-period ATR(14) avg)` | ATR(14) raw on closed bars; `Y = ATR / last_price × 100%`; `Z = ATR_now / ATR.rolling(20).mean().iloc[-1]` |

### 5.6 Why 1h is not in HTF

sim #8 narrative shows the agent treats 1h as **mid-term context** (paired with 5m for "context"), not as **long-term structure** (paired with 4h+ for "broader picture"). MA50/100/200 on 1h spans 8/17/33 days — not "long-term" in the sense HTF is built for. 100-period range on 1h spans only 4 days, with marginal value as a historical-extreme reference. The current MTS default already covers 1h; GMD covers 1h depth on demand. Re-evaluation trigger: ≥3 W3+ cycles where the agent narrative explicitly requests a 1h MA200 / MA100 anchor.

### 5.7 docstring

```python
"""Long-term structural view across one or more higher timeframes:
ticker (authoritative live price), per-tf MA50/MA100/MA200 with raw
value, price-vs-MA percentage, and MA slope (10-bar lookback);
MA stack comparison; 100-period high and low with bars-ago and the
candle open timestamp; range position within 100-period; 20-period
high-low range width; last-bar volume vs 20-period SMA ratio (base
volume); ATR(14) raw, percent of price, and ratio vs 20-period ATR
average.

All moving averages are simple moving averages (SMA) computed on the
closed-bar series only (excluding the in-progress bar). The slope
reference and all rolling averages use the closed-candle series.

MA stack comparison uses ">" / "<" / "≈" with 0.1% tolerance: when
|MAa - MAb| / MAb < 0.001, the operator collapses to "≈" (e.g.,
"MA50 ≈ MA100 < MA200").

Per-tf MA periods: 4h / 1d / 1w use (50, 100, 200) — standard
moving-average periods. 1M uses (12, 24, 60), corresponding to
1-year / 2-year / 5-year monthly cycles, matching crypto-industry
monthly chart conventions; the 1M section header marks the period
choice explicitly.

Args:
    timeframes: List of CCXT timeframes from {"4h", "1d", "1w", "1M"}.
        Default ["4h", "1d"]. Each timeframe rendered as a separate
        section.

Example call:
    get_higher_timeframe_view(timeframes=["4h", "1d"])
Example output:
    === Higher Timeframe View (BTC/USDT:USDT @ 14:23:08 UTC) ===
    Last: 81870.50

    [4h] (last closed candle: open 2026-05-11 08:00 UTC)
      MA50: 79200.00 (price vs MA: +3.4%; MA slope vs 10 bars ago: +0.8%)
      ...
      MA stack: MA50 > MA100 > MA200
      100-period High: 82800.00 (32 bars ago, candle open 2026-05-06 00:00 UTC)
      ...
      Last bar vol (base): 1521.6 (5.0× SMA(20) avg)
      ATR(14): 1572.30 (1.92% of price; 1.04× vs 20-period ATR(14) avg)
    ...

Degradation: per-tf "insufficient data (need N candles)" if OHLCV
history is shorter than the longest MA period; per-tf "Error:
Temporarily unavailable" if the OHLCV fetch for that tf fails;
overall returns header-only error if the ticker fetch fails.
"""
```

### 5.8 Token budget

Per-tf section ≈ 180 tokens; default 2 tfs ≈ 385 tokens (vs status quo single-tf ~250 tokens; saves ~115 tokens per cycle on the 5-cycle-per-sim same-cycle (4h, 1d) pairs that previously needed two calls).

---

## 6. Cross-cutting

### 6.1 Cross-tool routing — REVERTED to "no nudge, tool capability only"

**Status**: superseded by tool-design principle 8 ("信任 agent + 工具优先 / agent 行为偏差是工具反馈，不是 prompt 失败"). The wrapper-docstring "Related perception tools" tail described in earlier revisions of this section has been **removed from both `trader.py` wrappers and `tools_perception.py` impl docstrings**. No equivalent nudge added to `persona.py` Layer-1.

**Why reverted** (R2-Next-D 实施反思, 2026-05-12):

1. **Token cost without proportional routing effect** — Each of the three tool descriptions appeared 3× in the LLM-facing tool registry (once as the tool's own description, twice in sibling tools' "Related" sections). LLM tool-selection literature shows selectors primarily attend to the first 1-2 sentences of a tool description; a tail "Related" section pays the token cost of being read by the context loader without proportionally shifting the selection decision.

2. **Wrong direction of nudge** — "When invoking X, here are siblings Y / Z" activates an "explore alternatives" reflex at call time, which is the opposite of path-reversal intent. Path reversal needs an "at cycle entry, MTS first" routing — that timing belongs in `persona.py` Layer-1 (system prompt, read once per cycle) or, by principle 8, **in the tool's own capability** (a sufficiently capable MTS pulls itself to the front without external prompting).

3. **Principle 8 supersedes the original rationale** — Tool capability is the first-class routing signal. If W3+ data shows MTS frequency below the §7.2 ≥60% target, the **reflection order is tool-side** (MTS capability sufficient? Default tfs aligned? K-line snippet long enough? docstring fact-only and accurate? Last-3-closes carrying enough cycle-entry information?), not prompt-nudge-side. Adding a "Related" section pre-empted that reflection sequence by inserting a nudge as the implicit fallback — which is exactly the anti-pattern principle 8 names.

4. **DRY discipline alignment** — Per `project_n7_layer1_organization` (PR #25), tool descriptions live once in each tool's own docstring; cross-references that restate sibling capabilities re-introduce the duplication PR #25 eliminated.

**Implementation state** (as landed):
- `trader.py` GMD / HTF / MTS wrapper docstrings end at the `Degradation:` line; no "Related perception tools" tail.
- `tools_perception.py` impl-side docstrings mirror the wrappers; same removal.
- `persona.py` Layer-1 remains at 6 bullets (drift-guard `test_layer1_cross_tool_bullet_count` unchanged); no multi-tf bullet added.
- No new drift-guard test required — the absence of the "Related" tail is enforced by tool-design principle 8 red flag (`wrapper docstring 末尾出现 "Related tools" / "See also" / "Use this when not X" 类 cross-routing 段`) and reviewed in `tool-design-principles.md` §4 checklist principle 8 row.

**W3+ fallback contract**: if MTS frequency target (§7.2) is missed, the next iteration's first move is to revisit the tool itself (capability / default / docstring / interface), per principle 8 reflection order. A prompt-nudge fallback (whether Layer-1 or wrapper-docstring "Related" tail) is **explicitly out of bounds** without principle 8 sign-off.

### 6.2 docstring rewrite cross-reference

**Apply each docstring rewrite at TWO layers**:

1. **`src/agent/trader.py` @tool wrapper docstring** — this is the LLM-facing description that pydantic-ai's griffe sniff extracts and passes into the agent's tool registry. Changes here change what the agent sees. (Per memory `project_n7_layer1_organization`, PR #25 — Layer 1 reduced from 25 to 5 bullets precisely because tool descriptions migrated to wrapper docstrings sniffed by pydantic-ai.)
2. **`src/agent/tools_perception.py` implementation function docstring** — dev-facing reference for the actual implementation; should mirror the wrapper docstring so dev reading impl gets the same fact-only contract.

**Signature and default-value changes** (GMD `candle_count` default `50 → 30`; HTF signature `timeframe: Literal[...]` → `timeframes: list[Literal[...]] = ["4h", "1d"]`) **must** also be applied at the wrapper layer where pydantic-ai introspects them; the impl function signature should match the wrapper.

**Specific wrapper-only text removals (verified against `trader.py` at sim-#8 baseline)**:

| Tool | Wrapper text to remove or rewrite | Justification |
|---|---|---|
| GMD wrapper (`trader.py:86-109`) | `"Use multiple timeframes to build conviction before acting (e.g., \"1h\" for the bigger picture, \"5m\" for entry timing). Pass candle_count=20 for secondary timeframes to save tokens."` | N5 reverse-pressure conflicts with path reversal |
| GMD wrapper | `"Total output ~1000-1200 tokens (K-line table ~750-800 + indicators + context)."` | N1 developer meta-info |
| GMD wrapper | `Args.candle_count: ... Use 20 for quick checks or secondary timeframes; 50 for detailed analysis.` | N5 "X for Y" timing nudge |
| HTF wrapper (`trader.py:274-290`) | `"No default — explicitly pick the timeframe."` | Conflicts directly with new list default `["4h","1d"]` |
| HTF wrapper | `"Output ~250 tokens."` | N3 token budget meta-info |
| HTF wrapper | `Args.timeframe: '4h' bridges LTF and 1d; '1d'/'1w'/'1M' for swing/position context.` | N5 "X for Y"; also obsolete because signature shifts to list |
| MTS wrapper (`trader.py:365-378`) | `"Scan multi-TF alignment in a single call"` | "Scan" is mild evaluation phrasing (N4 family) — replace with fact description |
| MTS wrapper | `"Useful for a once-per-cycle structural overview before committing to a direction."` | N5 "Useful for ... before" timing nudge |
| MTS wrapper | `"Reports 4 columns per TF: momentum / structure / volatility / range position."` | Outdated — new layout adds Last-3-closes line and MA values inline; rewrite to match §3.1 mockup |

After the rewrite, the GMD / HTF / MTS wrapper docstrings should match §3.3 / §4.5 / §5.7 verbatim (or include them in full plus the `Args:` block).

| Issue | Tool | Action |
|---|---|---|
| N1 | GMD | Drop `Total output ~1000-1200 tokens` developer meta |
| N2 | HTF | Drop `Output is fact-only per spec §3.1: ..., no labels like 'uptrend'...` design-contract meta (belongs in spec, not docstring) |
| N3 | HTF | Drop `~250 tokens total` budget meta |
| N4 | MTS | Drop `Quick multi-timeframe scan` evaluation phrasing |
| N5 | GMD | Drop `Use multiple timeframes to build conviction` reverse-pressure (conflicts with path reversal) |
| N6 | HTF | Upgrade `long-period MAs` → `long-term structural view` (matches agent narrative) |
| N7 | MTS / GMD / HTF | Add example call → output for each tool (no "X for Y" framing) |
| N12 | HTF | `Current Price:` → `Last:` with ticker timestamp |
| N13 | MTS / GMD / HTF | Unify Price label to `Last:` (ccxt field alignment, drops "Current" qualifier) |

### 6.3 Adjacent fact-stamping cleanups

| Issue | Location | Change |
|---|---|---|
| F-O2 | `services/technical.py` BB format | `BB: U / M / L (...)` → `BB(20,2): Upper X | Middle Y | Lower Z (position: P%, 0%=Lower / 100%=Upper)` |
| F-O3 | `tools_perception.py` GMD Market Context | `Volume: X (Yx avg)` → `Last bar vol: X (Y× SMA(20) avg)` |
| F-P2 | `tools_perception.py` `get_position` | Remove `Liquidation:` line from Position section (the Risk Exposure section retains the richer `Liquidation: X (P% away = Q× ATR(1h))` form) |
| N13 expansion | `tools_perception.py` `get_price_pivots` (line 1709) | `Current Price: {current_price:,.2f}` → `Last: {current_price:.2f}` (label + thousand-separator removal per §6.5). Section dividers `=== Levels Above Current Price ===` / `=== Levels Below Current Price ===` retained as prose. Brings perception-tool header label unification to 4 of 4 tools (MTS / GMD / HTF / pivots). |

### 6.4 Data-source unification

Three helpers (`_live_price`, `_closed_bars`, `_atr_series`) land in a **new module `src/utils/ohlcv_utils.py`** — chosen over inlining them into `tools_perception.py` because (a) all three are cross-tool (consumed by MTS, GMD, HTF); (b) `_closed_bars` and `_live_price` may be useful to `get_position` and other future consumers; (c) a dedicated utils module makes the helper API discoverable and unit-testable without importing the full `tools_perception` graph. Each of the three is a thin wrapper that carries one design decision (algorithm lock / closed-only strip / canonical live-price source); together they constitute the algorithm-lock primitives the §2.2.1 invariant rests on. No per-tf signal-pack dataclass is introduced — MA50 / MA200 are 1-line `df.iloc[:-1]["close"].rolling(n).mean().iloc[-1]` calls and inlining them in each tool keeps the tool bodies linear and self-contained. Import path for tests and impl: `from src.utils.ohlcv_utils import _live_price, _closed_bars, _atr_series`.

```python
def _live_price(ticker) -> float:
    """Canonical live current price.

    Empirically (verify_ohlcv_semantics_v2.py 2026-05-10) approximately
    equal to df['close'].iloc[-1] within a sub-bps drift floor (~0.01 bps
    observed in 31-sample window; not strictly equal due to sub-second
    trade flow between independent ticker and OHLCV API calls). Choose
    ticker.last as the canonical live-price source for code-semantic
    clarity ('this is the live decision-time price').
    """
    return float(ticker.last)


def _closed_bars(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV with the in-progress candle stripped.

    Use as the input to all indicator computations (MA, ATR, BB, RSI,
    MACD, volume_ratio) and as the source for any 'last closed bar'
    field. Empirically (verify_ohlcv_semantics_v2.py 2026-05-10): in
    a 31-sample, 1m timeframe window with two candle rotations, the
    closed-only MA(5) showed 0.0000 drift while the full-df MA(5)
    drifted by 0.0200 in the same candle window. Stripping is required
    for temporally stable per-cycle facts.
    """
    return df.iloc[:-1]
```

All three helpers are used by MTS / GMD / HTF for any field that should be live-state (`_live_price`), closed-bar derived (`_closed_bars`), or ATR-series derived (`_atr_series`). No higher-level per-tf signal-pack dataclass is introduced — MA50 / MA200 are 1-line `df.iloc[:-1]["close"].rolling(n).mean().iloc[-1]` calls that do not warrant a wrapper, and a pack would force one tool to carry fields the other does not read. The §2.2.1 invariant is delivered by:

1. **Algorithm primitives are shared**: every overlap signal at 4h/1d uses `_closed_bars`, `_live_price`, and `_atr_series` (the three helpers above) and the same pandas SMA call. Pandas determinism plus the explicit `mamode="rma"` lock makes the computation step bit-equal on identical inputs by construction.
2. **End-to-end drift-guard verification**: `test_mts_htf_overlap_values_match` (§7.1) invokes both `get_multi_timeframe_snapshot` and `get_higher_timeframe_view` on the same fixture OHLCV at 4h and 1d, regex-extracts the MA50 / MA200 / ATR-ratio numbers from each rendered output, and asserts equality. This guards both compute drift and render-side bugs.

Helper API contract is finalized in commit 2 (per §8 commit plan); commits 3-5 consume the three helpers without redefining.

#### 6.4.1 closed-only strategy: where is `df.iloc[:-1]` applied?

**Per-caller, not global.** `src/services/technical.py compute_indicators` remains input-agnostic — it processes whatever DataFrame is handed to it. Each tool entering the indicator path is responsible for calling `_closed_bars(df)` first and passing the result to `compute_indicators`. Rationale:

- `compute_indicators` is also called by `get_position` (`tools_perception.py:237`) for risk-exposure calcs. Changing its global behavior would change the meaning of "MA distance" / "ATR" returned to `get_position` callers, an out-of-scope ripple.
- Per-caller stripping localizes the closed-only contract to the three tools this spec touches; reviewers see exactly where the change applies.
- Helper `_closed_bars` becomes the single source for the stripping rule (drop in-progress = `df.iloc[:-1]`), so the rule is uniform even though it's invoked from three call sites.

`get_position`, `get_price_pivots`, and other consumers of `compute_indicators` keep their current input semantics unless their own specs decide otherwise.

#### 6.4.2 ATR ratio compute path

§5.5 specifies `ATR(14) vs 20-period ATR(14) avg ratio`. Computing this requires the ATR(14) **series**, not just the latest scalar. Current `compute_indicators` returns the latest ATR as `indicators["atr_14"]: float`. Two options:

- (a) **Add a dedicated `_atr_series(df_closed, period=14) -> pd.Series` helper** in `src/utils/ohlcv_utils.py` that both MTS and HTF call when they need the rolling-20 ratio. This is the chosen path — keeps `compute_indicators` interface unchanged, avoids ripple to other consumers, and the helper carries the `mamode="rma"` algorithm lock (load-bearing per the constraint below).
- (b) Extend `compute_indicators` to optionally return series — broader scope, rejected.

Implementation note for commit 2: `_atr_series(df_closed, period=14) -> pd.Series` lives next to `_live_price` / `_closed_bars`. MTS and HTF each call it directly when computing the ATR-ratio; the helper is public to other future callers needing the same algorithm-locked series.

**Algorithm-consistency constraint**: `_atr_series` MUST use the same true-range and smoothing algorithm as `services/technical.py compute_indicators.atr_14`. Empirical anchor (verified at sim-#8 baseline, 2026-05-11): `services/technical.py:19` calls `pandas_ta.atr(...)`; `pandas_ta` 0.x default `mamode` is `"rma"` (i.e., Wilder's smoothing of TR). `_atr_series` must explicitly use `pandas_ta.atr(..., mamode="rma")` (or equivalent) to lock in the same algorithm — relying on `pandas_ta` defaults is fragile against future library upgrades. A drift-guard test (`test_atr_series_last_value_equals_compute_indicators_atr_14`, added to §7.1) enforces that `_atr_series(df_closed, 14).iloc[-1] == compute_indicators(df_closed)["atr_14"]` bit-for-bit. Otherwise HTF's `ATR(14): X` (latest of series) and GMD's `ATR(14): X` (scalar from compute_indicators) would silently diverge.

### 6.5 Number formatting convention

All numeric output across MTS / GMD / HTF uses **no thousand separator** (e.g., `81870.50`, not `81,870.50`). Current HTF code uses `f"{ma:,.2f}"` (thousand separator); this spec changes it to `f"{ma:.2f}"` to match MTS / GMD output style and the mockups in §3.1 / §4.1 / §5.1.

Rationale: the agent narrative in sim #8 consistently reads prices without thousand separators ("81870.50" not "81,870.50"); LLM tokenization is also slightly more compact without the comma; and uniform formatting across all three tools makes the `Last:` value visually identical wherever it appears.

Decimal precision: 2 decimal places for prices and MA values (matching ccxt ticker.last precision on BTC/USDT:USDT). ATR percentages and ratios use 2 decimal places (`0.15%`, `1.04×`). Volume uses 1 decimal place (`245.3`).

**Symbol scope caveat**: precision policy is currently scoped to BTC/USDT:USDT (the only symbol traded by this agent). For lower-priced symbols (e.g., alt-coin perpetuals where price ≪ 100 USDT), 2-decimal precision may be too coarse — `0.01 USDT` is a material move at those price levels. **Cross-symbol precision normalization is OOS for this spec**; if the agent's symbol scope expands in the future, revisit precision-per-symbol (likely via ccxt `market.precision.price`).

---

## 7. Acceptance criteria

### 7.1 PR-level criteria (must pass before merge)

| Category | Criteria |
|---|---|
| Unit tests | All three tools' output format tests pass; format lint with four sub-checks (no evaluation words / explicit N period / explicit anchor / explicit unit) |
| Drift-guard tests | (1) `test_indicator_temporal_stability_within_candle` — closed-only indicators stable, full-df indicators drift; reproduces verify_v2 A4 numerically. (2) `test_live_price_field_equals_ticker_last` — `Last:` header / MA distance / Range pos / BB position fields all derive from `ticker.last`. (3) `test_three_tools_use_same_ticker_last_in_Last_label` — MTS / GMD / HTF all surface ticker.last in their `Last:` line. (4) `test_no_in_progress_candle_in_indicator_inputs` — supplying df with an in-progress bar matches df.iloc[:-1] in all indicator outputs. (5) `test_mts_htf_overlap_values_match` (§2.2.1 invariant) — **end-to-end**: the test invokes `get_multi_timeframe_snapshot` and `get_higher_timeframe_view` against the same fixture OHLCV at 4h and 1d through the same mocked `MarketDataService`; the rendered MA50 / MA200 / ATR-ratio numbers are regex-extracted from each tool's output and asserted equal. This catches both compute drift (one side diverges from the shared SMA / `_atr_series` primitives) and render-side bugs (a wrong attribute is surfaced in the MA50 slot). (6) `test_atr_series_last_value_equals_compute_indicators_atr_14` (§6.4.2 invariant) — given the same `df_closed`, `_atr_series(df_closed, 14).iloc[-1]` equals `compute_indicators(df_closed)["atr_14"]` bit-for-bit; ensures HTF's series-based ATR display and GMD's scalar-based ATR display never diverge silently. |
| Golden mockup tests | Each of the three tools has a golden test using a fixture OHLCV data; output diff against the mockups in §3.1 / §4.1 / §5.1 |
| Cross-tool consistency tests | `Last:` label / ticker timestamp format / candle timestamp format are identical across tools |
| Per-tf degradation | HTF list with one tf having insufficient data renders `MAn: insufficient data` for that tf only; other tfs unaffected |
| 1M G5 adaptation | 1M timeframe uses (12, 24, 60) periods; section header marks the period choice |
| Empirical re-verification | `python scripts/verify_ohlcv_semantics_v2.py` runs successfully after the refactor; A4 closed-only stability still holds |
| Test count | Total tests collected ≥ baseline (1487 collected, including 5 skip, as of 2026-05-11 per `CLAUDE.md`) plus new tests added by this PR; no existing tests removed |

### 7.1.1 HTF signature change — existing test migration

The HTF signature change `timeframe: Literal[...]` → `timeframes: list[Literal[...]] = ["4h", "1d"]` requires migrating every existing call site. Enumerated migration matrix (audited 2026-05-11):

| File | Line range | Current pattern | Migration |
|---|---|---|---|
| `tests/test_perception_tools_n3.py` | ~14 call sites (lines 82, 106, 117, 128, 139, etc.) | `await get_higher_timeframe_view(deps, timeframe="1d")` (keyword) | `await get_higher_timeframe_view(deps, timeframes=["1d"])` (list with single element); update output-assertion strings to expect a single-tf section in the list-form layout per §5.1 |
| `tests/test_fact_only_wordlist.py` | line 555 | `await get_higher_timeframe_view(deps, "4h")` (positional) | `await get_higher_timeframe_view(deps, ["4h"])` |
| `tests/test_display_cycle.py` | lines 1625-1680 | HTF golden render snapshot expecting `Current Price: X` and single-tf section | Update golden snapshot to expect `Last: X` (N13) and the list-form per-tf section layout (§5.1) |

No backward-compatibility wrapper for the old single-tf signature is kept — the prior `timeframe=` parameter is removed at commit 3. Reason: keeping both interfaces splits the agent's mental model and weakens the signal that "HTF returns a list view"; a one-time test sweep is cheaper than long-term ambiguity.

### 7.1.2 `Current Price:` → `Last:` test sweep

N13 unification (per §6.3) touches every test that asserts the prior label:

| File | Affected pattern | Update |
|---|---|---|
| `tests/test_display_cycle.py` | line 393 (`"Price: 84200.00"`) | `"Last: 84200.00"` |
| `tests/test_display_cycle.py` | lines 1629 / 1649 (`"Current Price: 75,212.00"`) | `"Last: 75212.00"` (label change + thousand-separator removal per §6.5) |
| `tests/test_perception_tools_n3.py` | line 91 (`"Current price within range"`) | unchanged — this is prose, not a label (§5.5 retains the prose "current price within 100-period range" phrasing); audit verified the natural-language reference is preserved |
| `tests/test_toolkit_iter2.py` | lines 306, 372 (`"Current price:"`) | `"Last (ticker @ ...):"` (MTS-specific format per §3.1) |

`get_price_pivots` section headers `=== Levels Above Current Price ===` / `=== Levels Below Current Price ===` are **retained** — these are prose section dividers, not header labels; changing them to "Above Last" / "Below Last" reads awkward. Only the standalone `Current Price: V` field becomes `Last: V`.

### 7.2 W3+ post-release criteria (validated in next sim)

| Metric | sim #8 baseline | Target | Source |
|---|---|---|---|
| MTS-called-cycle share | 25% (44/178) | ≥ 45% sanity check（agent 是否真在用工具；过低 → tool dead-called，与下面 ref-rate 高低无关都要 rollback） | DISTINCT cycle_id WHERE `tool_name='get_multi_timeframe_snapshot'` / total cycles |
| MTS structure-terms ref rate (**primary adoption gate**) | W2 HTF+pivots era: 11.1% (9/81) | ≥ 60% retain / 50-60% observe / 31-50% docstring promo / < 31% rollback | MTS-call-cycles ∩ reasoning regex `golden cross\|death cross\|MA stack\|MA50.*MA200` / MTS-call-cycles |
| GMD ×3 cycles | 72% (129/178) | **MVP ≤ 50%** / **stretch ≤ 30%**. Same MVP/stretch tier as MTS frequency target above. | Cycles with three GMD calls |
| Mean tokens / cycle | ~80,674 (sim #8: 14.36M / 178 cycles) | ≤ 85% of baseline (~68,573 tokens/cycle) | Session log token counts |
| Narrative `"stale data"` confusion | ≥8 cycles | 0 cycles | `grep` on session log narrative |
| Narrative `"manual MA value compute"` traces | several | reduced | `grep` on session log narrative |

If targets are not met, iterate on the **wrapper-docstring "Related perception tools" tail phrasing** (the cross-tool routing surface per §6.1) rather than rewriting the tool spec. Layer-1 in `persona.py` remains untouched per the PR #25 discipline.

**为什么主 adoption gate 是 ref rate 而不是 call-frequency**：agent cycle 必定先调 state 工具（`get_position` / `get_open_orders` / `get_active_alerts`），MTS 是 multi-TF anchor 不是 cycle 入口；任何"first call per cycle"类口径在此架构下都会 ≈ 0%，与 MTS 是否被深度 consume 无关。"调用后 reasoning 引用 structure terms 比例" 才是 path-reversal 是否成功的真实信号 — agent 调用 MTS 后是否把 MA stack / golden cross / death cross 等 anchor 内化进 thesis。W3 sim #10 实测 structure-terms ref rate = 71.4% (85/119) → 达 retain 阈值；MTS-called-cycle share 也从 W2 25% 升至 W3 119/384 ≈ 31%（达 sanity check）。forensic 完整数据：`.working/w2-to-w3/05-w3-forensic.md` §二。

---

## 8. PR plan

Single PR on branch `iter-w2r2-next-d/multi-tf` with eight ordered commits:

```
commit 1: docs(iter-w2r2-next-d): spec + plan + empirical-foundations scripts
commit 2: refactor(technical+utils): F-O2 BB labels + helpers in src/utils/ohlcv_utils.py (_live_price / _closed_bars / _atr_series — three primitives frozen here, §6.4)
commit 3: feat(htf): list-form signature + N6 G1-G5 + 13 fact-stamping changes + "Related perception tools" docstring tail + test migration (per §7.1.1) — both trader.py @tool wrapper signature/docstring and tools_perception.py impl
commit 4: feat(gmd): default 30 + B3/B4 markers + K-line closed-only + F-O3 + N13 Last + "Related perception tools" docstring tail + tests — both trader.py @tool wrapper signature/docstring and tools_perception.py impl
commit 5: feat(mts): primary alignment + MA values column + Last 3 closes + "Related perception tools" docstring tail + tests — both trader.py @tool wrapper docstring and tools_perception.py impl
commit 6: chore(perception): N13 unification — get_price_pivots `Current Price:` → `Last:` for cross-tool perception-header consistency (§6.3 expansion)
commit 7: chore(get_position): F-P2 Liquidation deduplication
commit 8: test: cross-tool drift-guard and integration tests (all 6 invariants per §7.1)
```

Commit ordering rationale: docs first (per `feedback_plan_doc_commit_first` memory); shared infrastructure (commit 2) lands before any tool that depends on it; HTF (commit 3) before GMD (commit 4) before MTS (commit 5) so the most signal-edge tool ships first; N13 unification on `get_price_pivots` (commit 6) and Liquidation dedup (commit 7) close the perception-layer cosmetic consistency; integration drift-guard tests (commit 8) verify the path-reversal lock-in across all three multi-tf tools.

**Layer-1 in `persona.py` is intentionally untouched** in any commit — see §6.1 for why path-reversal cross-tool signal lives in wrapper docstrings rather than Layer-1, avoiding the `test_layer1_cross_tool_bullet_count` and `test_layer1_no_tool_invocation` drift-guards established by PR #25.

Total estimated diff:
- spec doc: ~600 lines
- plan doc (writing-plans skill output): ~400-600 lines
- impl code: ~530-690 lines
- tests: ~200-300 lines
- empirical scripts: ~270 lines (already written in this session)
- **total ≈ 2000-2400 lines diff**

The scope is intentionally Maximal — three coupled tool refactors, wrapper-docstring cross-tool routing tails (§6.1), multiple cross-cutting cleanups, and supporting tests + empirical scripts. Splitting into multiple PRs was considered (see brainstorm session) but rejected in favor of atomic landing matching the project's customary single-PR-per-iter cadence (PRs #42 / #43 / #44 are recent precedents).

---

## 9. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| MTS upgrade output exceeds 600-token budget | M | Golden mockup tests assert token range; if exceeded, trim Last-3-closes to Last-2-closes or compress alignment line |
| Wrapper-docstring "Related perception tools" tail (§6.1) insufficient to reverse the agent path (W3 MTS share < 60%) | M | Phrasing iteration is cheap (docstring tail edit only, no Layer-1 modification needed); §7.2 explicitly allows post-release iteration without spec rewrite; MVP/stretch tiered targets give 45-60% acceptable band |
| Closed-only indicator behavior changes agent's expectations (sim #8 agent narrative was reading partial-bar volume / range as "current") | M | Drift-guard tests enforce stability; W3 narrative grep monitors confusion; if needed, add an explicit `(closed candles only)` annotation in tool docstrings |
| GMD K-line table no longer shows in-progress row (visible UI change) | L | Header explicitly states `oldest-first by row, closed candles only`; agents needing in-progress price use `Last:` ticker / MTS |
| 1M G5 (12, 24, 60) has zero sim coverage | L | Unit tests cover (12, 24, 60) MA computation correctness; W3+ surfaces real 1M usage |
| Drift-guard tests differ from SimExchange behavior (Sim does not generate in-progress candles) | L | Tests use mock OHLCV fixtures with hand-crafted in-progress rows; verify_v2 script covers OKX live |
| MTS / HTF fetches straddling a candle close boundary cause one MA-window divergence (§2.2.1 algorithm-lock invariant holds for identical inputs only; production fetches are independent) | L | Acknowledged by design; agent narrative parsing tolerates ms-level drift; `test_mts_htf_overlap_values_match` mocks the OHLCV layer so MTS and HTF receive identical inputs and rendered MA50 / MA200 / ATR-ratio are asserted equal end-to-end. The test guards algorithm + render parity; it does not, and cannot, assert live cross-tool equality |

---

## 10. Empirical re-verification tooling

`scripts/verify_ohlcv_semantics.py` (v1, single-snapshot) and `scripts/verify_ohlcv_semantics_v2.py` (v2, 31-sample multi-window) are preserved in this PR as reusable verification tooling. Run after any future change touching the OHLCV / ticker data path:

```bash
python scripts/verify_ohlcv_semantics_v2.py
```

Expected outputs after this spec is implemented:
- A1 sub-bps drift floor unchanged (~0.01 bps)
- A2 distinct from (a) at long tfs; potentially flat at 1m
- A3 candle rotation matches when sample-to-rotation interval is small
- **A4 closed-only stability PASS** — this is the load-bearing assertion for spec correctness

---

## 11. Open follow-ups (post-release tracker)

See `.working/sim8-w2-multi-tf-deep-dive.md` §11.2 for additional independent-spec tool wishlist candidates (B1 taker flow, B2 volume profile, D1 alert distance, A3 funding history) — out of scope for this PR; they are own-spec candidates if W3+ data motivates them.

- F4 alert reasoning visible at cancel time (Iter 2 candidate)
- `evaluate_trade_setup` re-evaluation if W3 manual R:R compute traces persist or expand to ≥3 decision contexts
- 1h-on-HTF re-evaluation if ≥3 W3 cycles narrate explicit 1h MA200 / MA100 anchor needs
- N6 G3-G5 phrasing iteration if W3 narrative reveals the MA-stack / ATR-regime / 1M-period adaptations are read in unexpected ways

---

## 12. References

- Brainstorm prep: `.working/sim8-w2-multi-tf-deep-dive.md`, `.working/sim8-w2-tool-ergonomics.md`, `.working/sim8-w2-tool-optimization-roadmap.md`
- Tool design principles: `docs/superpowers/principles/tool-design-principles.md`
- Data: sim #8 SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`
- Empirical scripts: `scripts/verify_ohlcv_semantics.py`, `scripts/verify_ohlcv_semantics_v2.py`
- Source code touched: `src/agent/tools_perception.py` (39-138 GMD, 849-934 HTF, 1423-1529 MTS, get_position section), `src/services/technical.py`, `src/agent/persona.py`
