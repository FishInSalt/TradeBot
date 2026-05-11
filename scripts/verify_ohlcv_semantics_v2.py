"""Multi-sample empirical verification of OHLCV semantics — rigorous v2.

Replaces single-snapshot v1 with 4 dedicated tests over a longer sampling
window. Validates the four claims the multi-tf spec relies on:

  Claim A1: (a) ticker.last  ≡  (b) df["close"].iloc[-1]  at the same fetch
            moment, even across many trades
  Claim A2: (c) df["close"].iloc[-2]  ≠  (a) ticker.last  in general
            (frozen snapshot at last close)
  Claim A3: When a candle closes, the old (b) becomes the new (c)
            (= (b) is in-progress; confirmed at the rotation boundary)
  Claim A4: Indicators on closed-only inputs are temporally stable;
            indicators on full df drift with each trade

Strategy: sample every SAMPLE_INTERVAL_S seconds for SAMPLE_WINDOW_S
seconds at 1m timeframe (fast candle rotation). All claims are evaluated
across the sample series.

Run:
  python scripts/verify_ohlcv_semantics_v2.py
"""
from __future__ import annotations

import asyncio
import statistics
import time
from datetime import datetime, timezone

import ccxt.async_support as ccxt
import pandas as pd


SYMBOL = "BTC/USDT:USDT"
TIMEFRAME = "1m"
TF_SECONDS = 60
SAMPLE_INTERVAL_S = 2.0
SAMPLE_WINDOW_S = 90  # 45 samples, expects 1-2 candle rotations


def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M:%S")


async def collect_samples(ex: ccxt.okx) -> list[dict]:
    """Collect (ticker, OHLCV) sample pairs every SAMPLE_INTERVAL_S seconds
    for SAMPLE_WINDOW_S seconds. OHLCV fetches enough history (limit=10) to
    let downstream tests compute MA(5) on both full-df and closed-only."""
    samples = []
    start = time.time()
    iteration = 0
    while time.time() - start < SAMPLE_WINDOW_S:
        wall_clock_ms = int(time.time() * 1000)
        ticker = await ex.fetch_ticker(SYMBOL)
        ohlcv = await ex.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=10)

        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])

        ma5_full = float(df["close"].tail(5).mean())
        ma5_closed = float(df["close"].iloc[:-1].tail(5).mean())  # closed-only

        samples.append({
            "iter": iteration,
            "wall_clock_ms": wall_clock_ms,
            "ticker_last": float(ticker["last"]),
            "iloc_-1_close": float(df["close"].iloc[-1]),
            "iloc_-1_ts": int(df["timestamp"].iloc[-1]),
            "iloc_-2_close": float(df["close"].iloc[-2]),
            "iloc_-2_ts": int(df["timestamp"].iloc[-2]),
            "ma5_full": ma5_full,
            "ma5_closed": ma5_closed,
        })
        iteration += 1
        elapsed = time.time() - start
        if elapsed < SAMPLE_WINDOW_S:
            await asyncio.sleep(max(0, SAMPLE_INTERVAL_S - (time.time() - (start + elapsed))))

    return samples


def test_a1_equivalence(samples: list[dict]) -> dict:
    """Claim A1: (a) ≡ (b) at same fetch — drift should be 0 bps across all samples."""
    drifts = []
    for s in samples:
        if s["ticker_last"] == 0:
            continue
        drift_bps = (s["iloc_-1_close"] - s["ticker_last"]) / s["ticker_last"] * 1e4
        drifts.append(drift_bps)
    return {
        "n_samples": len(drifts),
        "max_abs_drift_bps": max(abs(d) for d in drifts) if drifts else 0,
        "min_drift_bps": min(drifts) if drifts else 0,
        "max_drift_bps": max(drifts) if drifts else 0,
        "all_zero": all(abs(d) < 0.001 for d in drifts) if drifts else False,
        "samples_with_drift": sum(1 for d in drifts if abs(d) > 0.001),
    }


def test_a2_distinctness(samples: list[dict]) -> dict:
    """Claim A2: (c) ≠ (a) in general — drift should vary."""
    drifts = []
    for s in samples:
        if s["ticker_last"] == 0:
            continue
        drift_bps = (s["iloc_-2_close"] - s["ticker_last"]) / s["ticker_last"] * 1e4
        drifts.append(drift_bps)
    return {
        "n_samples": len(drifts),
        "min_drift_bps": min(drifts) if drifts else 0,
        "max_drift_bps": max(drifts) if drifts else 0,
        "abs_mean_drift_bps": statistics.mean(abs(d) for d in drifts) if drifts else 0,
        "stdev_drift_bps": statistics.stdev(drifts) if len(drifts) > 1 else 0,
    }


def test_a3_candle_rotation(samples: list[dict]) -> dict:
    """Claim A3: When candle rotates, the old (b) becomes the new (c)."""
    rotations = []
    for i in range(1, len(samples)):
        prev = samples[i - 1]
        curr = samples[i]
        if curr["iloc_-1_ts"] != prev["iloc_-1_ts"]:
            # Candle rotation detected; previous (b) at prev["iloc_-1_close"]
            # should become current (c) at curr["iloc_-2_close"]
            rotations.append({
                "iter": curr["iter"],
                "rotation_at": _fmt_ts(curr["wall_clock_ms"]),
                "prev_b_close": prev["iloc_-1_close"],
                "curr_c_close": curr["iloc_-2_close"],
                "match": prev["iloc_-1_close"] == curr["iloc_-2_close"],
                "delta_if_mismatch": curr["iloc_-2_close"] - prev["iloc_-1_close"],
                "prev_b_ts": _fmt_ts(prev["iloc_-1_ts"]),
                "curr_c_ts": _fmt_ts(curr["iloc_-2_ts"]),
                "ts_match": prev["iloc_-1_ts"] == curr["iloc_-2_ts"],
            })
    return {
        "n_rotations_observed": len(rotations),
        "rotations": rotations,
        "all_rotations_match": all(r["match"] and r["ts_match"] for r in rotations) if rotations else None,
    }


def test_a4_indicator_stability(samples: list[dict]) -> dict:
    """Claim A4: closed-only indicators are stable; full-df indicators drift.

    Within each candle window (no rotation), MA(5) closed-only should be
    constant; MA(5) full-df should vary as ticker.last drifts.
    """
    # Group samples by candle window (same iloc_-1_ts)
    groups: dict[int, list[dict]] = {}
    for s in samples:
        ts = s["iloc_-1_ts"]
        groups.setdefault(ts, []).append(s)

    closed_drifts = []
    full_drifts = []
    for ts, group in groups.items():
        if len(group) < 2:
            continue
        ma5_closed_values = [g["ma5_closed"] for g in group]
        ma5_full_values = [g["ma5_full"] for g in group]
        closed_range = max(ma5_closed_values) - min(ma5_closed_values)
        full_range = max(ma5_full_values) - min(ma5_full_values)
        closed_drifts.append(closed_range)
        full_drifts.append(full_range)

    return {
        "n_windows_analyzed": len(closed_drifts),
        "max_closed_only_drift": max(closed_drifts) if closed_drifts else 0,
        "max_full_df_drift": max(full_drifts) if full_drifts else 0,
        "mean_closed_only_drift": statistics.mean(closed_drifts) if closed_drifts else 0,
        "mean_full_df_drift": statistics.mean(full_drifts) if full_drifts else 0,
    }


def print_sample_table(samples: list[dict]) -> None:
    print("\n=== Sample series (first 10 rows + last 5 rows) ===")
    print(f"{'iter':<5} {'wall_clock':<10} {'ticker':<10} {'iloc[-1]':<10} {'iloc[-1] open':<15} "
          f"{'iloc[-2]':<10} {'iloc[-2] open':<15}")
    head = samples[:10]
    tail = samples[-5:] if len(samples) > 15 else []
    for s in head:
        print(f"{s['iter']:<5} {_fmt_ts(s['wall_clock_ms']):<10} "
              f"{s['ticker_last']:<10.2f} {s['iloc_-1_close']:<10.2f} "
              f"{_fmt_ts(s['iloc_-1_ts']):<15} "
              f"{s['iloc_-2_close']:<10.2f} {_fmt_ts(s['iloc_-2_ts']):<15}")
    if tail:
        print(f"... [{len(samples) - 15} samples elided] ...")
        for s in tail:
            print(f"{s['iter']:<5} {_fmt_ts(s['wall_clock_ms']):<10} "
                  f"{s['ticker_last']:<10.2f} {s['iloc_-1_close']:<10.2f} "
                  f"{_fmt_ts(s['iloc_-1_ts']):<15} "
                  f"{s['iloc_-2_close']:<10.2f} {_fmt_ts(s['iloc_-2_ts']):<15}")


def print_results(samples: list[dict], a1, a2, a3, a4) -> None:
    print("\n\n" + "=" * 70)
    print("EMPIRICAL TEST RESULTS")
    print("=" * 70)

    print("\n--- A1: (a) ticker.last ≡ (b) df.iloc[-1].close ---")
    print(f"  n_samples              : {a1['n_samples']}")
    print(f"  max |drift|            : {a1['max_abs_drift_bps']:.4f} bps")
    print(f"  drift range            : [{a1['min_drift_bps']:+.4f}, {a1['max_drift_bps']:+.4f}] bps")
    print(f"  all samples zero drift : {a1['all_zero']}")
    print(f"  samples with non-zero  : {a1['samples_with_drift']}")
    verdict_a1 = "PASS — equivalent" if a1["all_zero"] else "REVIEW — sub-bps drift observed"
    print(f"  Verdict                : {verdict_a1}")

    print("\n--- A2: (c) df.iloc[-2].close ≠ (a) in general ---")
    print(f"  n_samples              : {a2['n_samples']}")
    print(f"  drift range            : [{a2['min_drift_bps']:+.2f}, {a2['max_drift_bps']:+.2f}] bps")
    print(f"  abs mean drift         : {a2['abs_mean_drift_bps']:.2f} bps")
    print(f"  stdev                  : {a2['stdev_drift_bps']:.2f} bps")
    verdict_a2 = ("PASS — distinct from (a)" if a2["abs_mean_drift_bps"] > 0.1
                  else "INCONCLUSIVE — price too flat during sampling")
    print(f"  Verdict                : {verdict_a2}")

    print("\n--- A3: Candle rotation: old (b) → new (c) ---")
    print(f"  n_rotations_observed   : {a3['n_rotations_observed']}")
    if a3["rotations"]:
        for r in a3["rotations"]:
            print(f"  Rotation @ {r['rotation_at']}:")
            print(f"    prev (b) close = {r['prev_b_close']}, ts open = {r['prev_b_ts']}")
            print(f"    curr (c) close = {r['curr_c_close']}, ts open = {r['curr_c_ts']}")
            print(f"    close match    : {r['match']}")
            print(f"    timestamp match: {r['ts_match']}")
            if not r["match"]:
                print(f"    (delta if mismatch: {r['delta_if_mismatch']})")
        verdict_a3 = ("PASS — (b) → (c) transition confirmed"
                      if a3["all_rotations_match"]
                      else "FAIL — mismatch on rotation")
    else:
        verdict_a3 = "INCONCLUSIVE — no candle rotation in sampling window"
    print(f"  Verdict                : {verdict_a3}")

    print("\n--- A4: Indicator stability (closed-only vs full-df) ---")
    print(f"  n_windows_analyzed     : {a4['n_windows_analyzed']}")
    print(f"  max  closed-only drift : {a4['max_closed_only_drift']:.4f}")
    print(f"  max  full-df    drift  : {a4['max_full_df_drift']:.4f}")
    print(f"  mean closed-only drift : {a4['mean_closed_only_drift']:.4f}")
    print(f"  mean full-df    drift  : {a4['mean_full_df_drift']:.4f}")
    if a4["max_closed_only_drift"] < 1e-6 and a4["max_full_df_drift"] > 0:
        verdict_a4 = "PASS — closed-only stable; full-df drifts"
    elif a4["max_full_df_drift"] == 0:
        verdict_a4 = "INCONCLUSIVE — full-df also stable (price didn't move within candle windows)"
    elif a4["max_closed_only_drift"] > 1e-6:
        verdict_a4 = "FAIL — closed-only also drifted (unexpected)"
    else:
        verdict_a4 = "MIXED"
    print(f"  Verdict                : {verdict_a4}")


async def main():
    ex = ccxt.okx({"enableRateLimit": True})
    try:
        print(f"Symbol: {SYMBOL}")
        print(f"Sampling: {TIMEFRAME} timeframe, every {SAMPLE_INTERVAL_S}s for {SAMPLE_WINDOW_S}s")
        print(f"Expected samples ≈ {SAMPLE_WINDOW_S // SAMPLE_INTERVAL_S}, expected candle rotations ≈ 1-2")
        print()

        samples = await collect_samples(ex)
        print(f"Collected {len(samples)} samples.")
        print_sample_table(samples)

        a1 = test_a1_equivalence(samples)
        a2 = test_a2_distinctness(samples)
        a3 = test_a3_candle_rotation(samples)
        a4 = test_a4_indicator_stability(samples)
        print_results(samples, a1, a2, a3, a4)

    finally:
        await ex.close()


if __name__ == "__main__":
    asyncio.run(main())
