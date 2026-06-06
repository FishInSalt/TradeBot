"""LLM-facing tool descriptions for tools where pydantic-ai 1.78 / griffe
strips structured sections (Examples / Example output / inline admonitions)
from `tool.tool_def.description`.

Constants in this module are passed verbatim via `@tool(description=DESC_X)`
to bypass griffe parsing and reach the LLM. Args descriptions remain in the
source docstring (parsed normally into `parameters_json_schema`).

See docs/superpowers/specs/2026-05-19-iter-tool-opt-dead-example-promote-design.md
for the audit (7 tools / 4 loss categories) + design rationale.
"""

SET_NEXT_WAKE_DESCRIPTION = """Schedule the next scheduler wake-up after a relative minute interval.

Returns a confirmation, or a reject message describing the violation. Alerts, fills, and conditional triggers always interrupt scheduled wake.

Examples:
    set_next_wake(15, "consolidation phase, check in 15 min")
    → "Next wake set to 15 min"

    set_next_wake(90, "...")
    → "Cannot set wake to 90 min: exceeds wake_max=60 min for this session."

    set_next_wake(0, "...")
    → "Cannot set wake to 0 min: below wake_min=1 min."
"""


SET_NEXT_WAKE_AT_DESCRIPTION = """Schedule the next scheduler wake-up at an absolute UTC time.

Returns a confirmation containing the resolved date-time, or a reject message describing the violation. Alerts, fills, and conditional triggers always interrupt scheduled wake.

Examples:
    set_next_wake_at("10:31", "review 15m candle close at 10:30 UTC")
    → "Next wake set for 2026-05-12 10:31 UTC (in 8 min)"

    set_next_wake_at("12:00", "...")
    → "Cannot wake at 12:00 UTC: nearest future 2026-05-12 12:00 UTC (in 97 min) exceeds wake_max=60 min for this session."

    set_next_wake_at("10:23", "...")  # now=10:23, resolves to tomorrow
    → "Cannot wake at 10:23 UTC: nearest future 2026-05-13 10:23 UTC (in 1440 min) exceeds wake_max=60 min for this session."

    set_next_wake_at("foo", "...")
    → "Invalid target_time format: 'foo'. Expected 'HH:MM' UTC with 2-digit hour and minute (e.g., '10:37' or '03:05')."
"""


GET_MARKET_DATA_DESCRIPTION = """Single-timeframe market data: ticker (last + bid/ask + 24h H/L + base volume), technical indicators (RSI / MA(20) / MA(50) / MACD / BB / ATR), the most recent N closed candles in OHLCV table form with per-bar volume ratio (RVol = vol / SMA(20)) and anomaly markers, and the in-progress (not-yet-closed) candle in its own section.

Indicator VALUES are computed on the closed-bar series only (the in-progress candle is excluded), and the Technical Indicators header reports the last closed candle's open time. Moving averages are simple moving averages (SMA). The MA / BB comparison suffixes (`Last <price> → X% vs MA`, `Last <price> → X% ... band`) and the ATR percent (`X% of Last <price>`) use the live ticker Last as the operand / denominator — the live price measured against the closed-bar structure.

OHLCV columns: Time (open UTC) | Open | High | Low | Close | Vol | RVol(×SMA20) | Markers.
- RVol = bar volume / SMA(20) of bar volumes (`2.95×` means the bar's volume is 2.95× the 20-bar average). Rendered for every closed bar; `—` when SMA(20) has not yet started (degraded display window).
- Markers (upside-only thresholds): `vol↑` for bar volume > 2× SMA(20) of bar volumes; `range↑` for bar range (high - low) > 2× ATR(14); empty for neither threshold tripped.

In-progress Candle section (after the closed table): the current not-yet-closed candle rendered from live data — Open | High(so far) | Low(so far) | Last | Vol(so far) — plus how far into the bar interval it is. This bar is excluded from all indicators and carries no RVol/markers until it closes; the authoritative live price is the ticker Last.

Example call:
    get_market_data(timeframe="5m", candle_count=30)

Example output:
    === Ticker (BTC/USDT:USDT @ 14:28:00 UTC) ===
    Last: 81870.50 | Bid: 81870.40 | Ask: 81870.60
    24h High: 82400.10 | 24h Low: 80120.00 | 24h base vol: 12345.67

    === Technical Indicators (5m, values as of last closed 14:20) ===
    RSI(14): 58.20
    MA(20): 81960.00  (Last 81870.50 → -0.1% vs MA)
    MA(50): 82150.00  (Last 81870.50 → -0.3% vs MA)
    MACD: 12.50 | Signal: 8.30 | Histogram: 4.20
    BB(20,2): Upper 82100.00 | Middle 81870.00 | Lower 81640.00  (Last 81870.50 → 50% of band, 0%=Lower / 100%=Upper)
    ATR(14): 245.30 (0.30% of Last 81870.50)

    === Recent Closed Candles (5m, last 30, oldest-first by row) ===
    Time (open UTC)        Open       High        Low      Close        Vol  RVol(×SMA20)  Markers
    ...
    14:15              81830.00   81870.00   81825.00   81865.00      400.0         3.02×  vol↑
    14:20              81865.00   81910.00   81860.00   81895.00      178.6         1.35×

    === In-progress Candle (5m): 14:25 open, closes 14:30 — ~3 of 5 min elapsed ===
    Time (open UTC)        Open High(so far)  Low(so far)       Last  Vol(so far)
    14:25              81895.00     81920.00     81880.00   81870.50         95.0
    (partial bar — excluded from all indicators; no RVol/markers until close)
"""


GET_HIGHER_TIMEFRAME_VIEW_DESCRIPTION = """Long-term structural view across one or more higher timeframes: ticker (authoritative live price), per-tf MA50/MA100/MA200 with raw value, price-vs-MA percentage, and MA slope (10-bar lookback); MA stack comparison; 100-period high and low with bars-ago and the candle open timestamp; range position within 100-period; 20-period high-low range width; last-bar volume vs 20-period SMA ratio (base volume); ATR(14) raw, percent of price, and ratio vs 20-period ATR average.

All moving averages are simple moving averages (SMA) computed on the closed-bar series only (excluding the in-progress bar). The slope reference and all rolling averages use the closed-candle series.

MA stack comparison uses ">" / "<" / "≈" with 0.1% tolerance: when |MAa - MAb| / MAb < 0.001, the operator collapses to "≈" (e.g., "MA50 ≈ MA100 < MA200").

Per-tf MA periods: 4h / 1d / 1w use (50, 100, 200) — standard moving-average periods. 1M uses (12, 24, 60), corresponding to 1-year / 2-year / 5-year monthly cycles, matching crypto-industry monthly chart conventions; the 1M section header marks the period choice explicitly.

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

Per-tf degradation: "insufficient data (need N candles)" if OHLCV history is shorter than the longest MA period; "Error: Temporarily unavailable" if the OHLCV fetch for that tf fails. Overall returns header-only error if the ticker fetch fails.
"""


GET_MULTI_TIMEFRAME_SNAPSHOT_DESCRIPTION = """Multi-timeframe snapshot — single fanout across N timeframes for fast cross-TF structural reads. Per TF: momentum vs MA20 (closed-bar series), MA20 / MA50 fast-vs-slow comparison, ATR percent of price + ratio vs 20-period ATR average, range position within 20-bar high/low. Header row gives ticker + per-tf "MA fast-vs-slow" digest line (e.g. "5m below | 1h above | 4h above | 1d below"). Last 3 closed-candle close-prices per TF for short-momentum read.

All indicators computed on closed-bar series only (excluding the in-progress candle). Algorithm-lock invariant: MTS per-TF outputs match `get_higher_timeframe_view` per-TF (algorithm shared); end-to-end verified by `test_mts_htf_overlap_values_match`.

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

Per-TF degradation: "insufficient data" or "temporarily unavailable" per failed TF. Overall returns header-only error if all TFs fail or the ticker fetch fails.
"""
