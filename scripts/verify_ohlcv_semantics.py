"""Verify the semantic claims about three "current price" candidates used
in get_market_data / get_higher_timeframe_view / get_multi_timeframe_snapshot:

(a) ticker.last           — live last-trade price from ticker endpoint
(b) df["close"].iloc[-1]  — claimed: in-progress candle's current close (≈ a)
(c) df["close"].iloc[-2]  — claimed: last fully closed candle's close

Empirically checks (per timeframe, against OKX live public endpoint, no auth):
  1. Is df.iloc[-1].timestamp = open time of the in-progress candle?
       i.e., wall_clock - df.iloc[-1].timestamp ∈ [0, tf_duration)
  2. Is df.iloc[-1].close ≈ ticker.last?
       reported as bps drift, ≤ a few bps would confirm (b) ≈ (a)
  3. Is df.iloc[-2].close noticeably different from ticker.last?
       (assuming price has moved within the last candle window)
  4. Sequential fetch: does df.iloc[-1].close drift toward ticker.last over time?

Run:
  python scripts/verify_ohlcv_semantics.py

No auth / .env needed; uses ccxt public endpoints for OKX BTC/USDT:USDT.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import ccxt.async_support as ccxt


SYMBOL = "BTC/USDT:USDT"
TIMEFRAMES = ["1m", "5m", "1h", "4h", "1d"]
TF_SECONDS = {"1m": 60, "5m": 300, "1h": 3600, "4h": 14400, "1d": 86400}


def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


async def fetch_snapshot(ex: ccxt.okx, tf: str) -> dict:
    """Fetch ticker + OHLCV for a single tf, return diagnostic dict."""
    wall_clock_ms_before = int(time.time() * 1000)

    # Ticker
    ticker = await ex.fetch_ticker(SYMBOL)

    # OHLCV (limit=3 to get -1 / -2 / -3)
    ohlcv = await ex.fetch_ohlcv(SYMBOL, tf, limit=3)

    wall_clock_ms_after = int(time.time() * 1000)
    fetch_round_ms = wall_clock_ms_after - wall_clock_ms_before

    # ohlcv rows: [timestamp_ms, open, high, low, close, volume]
    last_row = ohlcv[-1]
    second_last_row = ohlcv[-2]

    last_ts_ms, _, _, _, last_close, last_vol = last_row
    second_ts_ms, _, _, _, second_close, second_vol = second_last_row

    tf_ms = TF_SECONDS[tf] * 1000
    age_of_last_row_ms = wall_clock_ms_after - last_ts_ms

    # Is df.iloc[-1] timestamp within current in-progress window?
    in_progress_window = (0 <= age_of_last_row_ms < tf_ms)

    # ticker.last vs (b) drift
    drift_b_bps = ((last_close - ticker["last"]) / ticker["last"]) * 1e4 if ticker["last"] else None
    # ticker.last vs (c) drift
    drift_c_bps = ((second_close - ticker["last"]) / ticker["last"]) * 1e4 if ticker["last"] else None

    return {
        "tf": tf,
        "wall_clock_after": _fmt_ts(wall_clock_ms_after),
        "wall_clock_after_ms": wall_clock_ms_after,
        "fetch_round_ms": fetch_round_ms,
        "ticker_last": ticker["last"],
        "ticker_timestamp": _fmt_ts(ticker["timestamp"]) if ticker.get("timestamp") else "n/a",
        "iloc_-1_ts": _fmt_ts(last_ts_ms),
        "iloc_-1_close": last_close,
        "iloc_-1_vol": last_vol,
        "iloc_-2_ts": _fmt_ts(second_ts_ms),
        "iloc_-2_close": second_close,
        "iloc_-2_vol": second_vol,
        "age_of_last_row_seconds": age_of_last_row_ms / 1000,
        "tf_duration_seconds": TF_SECONDS[tf],
        "in_progress_window": in_progress_window,
        "drift_(b)_vs_ticker_bps": drift_b_bps,
        "drift_(c)_vs_ticker_bps": drift_c_bps,
    }


async def sequential_drift_test(ex: ccxt.okx, tf: str = "5m", n: int = 4, sleep_s: float = 5) -> list[dict]:
    """Repeated fetches of (a) and (b) to see if df.iloc[-1].close
    drifts toward subsequent ticker.last (would confirm in-progress
    candle's close field is updated by trades, not frozen)."""
    results = []
    for i in range(n):
        snap = await fetch_snapshot(ex, tf)
        results.append({
            "iter": i,
            "wall_clock": snap["wall_clock_after"],
            "ticker_last": snap["ticker_last"],
            "iloc_-1_close": snap["iloc_-1_close"],
            "iloc_-1_ts": snap["iloc_-1_ts"],
            "drift_bps": snap["drift_(b)_vs_ticker_bps"],
        })
        if i < n - 1:
            await asyncio.sleep(sleep_s)
    return results


def print_snapshot(s: dict) -> None:
    print(f"\n=== {s['tf']} ===")
    print(f"  Wall clock         : {s['wall_clock_after']}")
    print(f"  Fetch round        : {s['fetch_round_ms']} ms")
    print(f"  ticker.last        : {s['ticker_last']}")
    print(f"  ticker.timestamp   : {s['ticker_timestamp']}")
    print(f"  df.iloc[-1].ts     : {s['iloc_-1_ts']}")
    print(f"  df.iloc[-1].close  : {s['iloc_-1_close']}")
    print(f"  df.iloc[-1].volume : {s['iloc_-1_vol']}")
    print(f"  df.iloc[-2].ts     : {s['iloc_-2_ts']}")
    print(f"  df.iloc[-2].close  : {s['iloc_-2_close']}")
    print(f"  df.iloc[-2].volume : {s['iloc_-2_vol']}")
    print(f"  Age of -1 row      : {s['age_of_last_row_seconds']:.1f} s of {s['tf_duration_seconds']} s tf")
    print(f"  In-progress window : {s['in_progress_window']}")
    print(f"  Drift (b) vs (a)   : {s['drift_(b)_vs_ticker_bps']:+.3f} bps")
    print(f"  Drift (c) vs (a)   : {s['drift_(c)_vs_ticker_bps']:+.3f} bps")


def print_drift_test(rows: list[dict]) -> None:
    print("\n=== Sequential drift test (5m, 4 fetches × 5 s spacing) ===")
    print(f"{'iter':<5} {'wall_clock':<26} {'ticker.last':<12} {'iloc[-1].close':<16} {'iloc[-1].ts':<26} {'drift_bps':<10}")
    for r in rows:
        print(
            f"{r['iter']:<5} {r['wall_clock']:<26} {r['ticker_last']:<12.2f} "
            f"{r['iloc_-1_close']:<16.2f} {r['iloc_-1_ts']:<26} {r['drift_bps']:+.3f}"
        )


async def main():
    ex = ccxt.okx({"enableRateLimit": True})
    try:
        print(f"Symbol: {SYMBOL}")
        print("Fetching one snapshot per timeframe...")
        for tf in TIMEFRAMES:
            snap = await fetch_snapshot(ex, tf)
            print_snapshot(snap)

        print("\n\nRunning sequential drift test on 5m...")
        rows = await sequential_drift_test(ex, "5m", n=4, sleep_s=5)
        print_drift_test(rows)

        print("\n\n=== Verdict ===")
        print("Expected (per spec brainstorm claims):")
        print("  - in_progress_window = True for every tf")
        print("  - drift_(b)_vs_ticker_bps ≈ 0 (close to a few bps)")
        print("  - drift_(c)_vs_ticker_bps could be 0–100+ bps depending on price movement within last candle")
        print("  - Sequential drift_bps changes / iloc[-1] same ts → confirms in-progress close is updated by trades")
    finally:
        await ex.close()


if __name__ == "__main__":
    asyncio.run(main())
