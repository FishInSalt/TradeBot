"""Phase 1 view 性能 benchmark.

跑 sim #8 archive DB 上 SELECT * FROM v_cycle_metrics / v_alert_lifecycle /
v_order_lifecycle 各 10 次取中位数时间。

Usage:
    python scripts/benchmark_view_phase1.py --db data/tradebot.db
"""
import argparse
import sqlite3
import statistics
import time
import sys


def bench(conn, query, n=10):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        list(conn.execute(query))
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times), statistics.mean(times), max(times)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/tradebot.db")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    queries = {
        "v_cycle_metrics":     "SELECT * FROM v_cycle_metrics",
        "v_alert_lifecycle":   "SELECT * FROM v_alert_lifecycle",
        "v_order_lifecycle":   "SELECT * FROM v_order_lifecycle",
    }

    print(f"DB: {args.db}")
    print(f"{'view':<25} {'median_ms':>12} {'mean_ms':>12} {'max_ms':>12}")
    print("-" * 65)
    for view, q in queries.items():
        med, mean, mx = bench(conn, q)
        print(f"{view:<25} {med:>12.2f} {mean:>12.2f} {mx:>12.2f}")
        if med > 100:
            print(f"  ⚠️  {view} median > 100ms — see spec §8.3 future work")
            sys.exit(1)
    print("✓ All views < 100ms median")


if __name__ == "__main__":
    main()
