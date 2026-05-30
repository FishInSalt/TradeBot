"""分析 probe_taker_flow_lag.py 落的 jsonl,出每个 (symbol, period) 的:
  · closed 占比      = is_in_progress=False 的 ok 采样比例
  · 缺几根分布        = missing_bars 取值计数(5m/1h/4h;1d 锚定故 None)
  · 发布延迟 D 分布   = 由 newest_open 单根跳变估:D = 观测到新 bar 的时刻 − 该 bar open
                        （上界,误差 ≤ 采样间隔;只取 +1 根的干净跳变)
  · 采样空洞          = 相邻采样间隔 >3× 中位间隔(疑似 sleep/中断,clamshell)

用法：python scripts/analyze_taker_flow_lag.py [.working/taker_flow_lag.jsonl]
"""
import json
import statistics
import sys
from collections import Counter, defaultdict


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    return sorted_vals[min(int(q * len(sorted_vals)), len(sorted_vals) - 1)]


def _fmt_s(ms):
    return "—" if ms is None else f"{ms / 1000:.0f}s" if ms < 120_000 else f"{ms / 60_000:.1f}min"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else ".working/taker_flow_lag.jsonl"
    by_key = defaultdict(list)
    total = ok = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            total += 1
            if r.get("ok"):
                ok += 1
            by_key[(r["symbol"], r["period"])].append(r)

    span_ms = 0
    all_ts = [r["ts_utc_ms"] for recs in by_key.values() for r in recs]
    if all_ts:
        span_ms = max(all_ts) - min(all_ts)
    print(f"file: {path}")
    print(f"samples: {total} total · {ok} ok · span {_fmt_s(span_ms)} "
          f"({len(by_key)} symbol×period keys)\n")

    hdr = (f"{'symbol':<16} {'per':<4} {'n_ok':>5} {'closed%':>8} "
           f"{'D n':>4} {'D med':>7} {'D p95':>7} {'D max':>7} "
           f"{'missing(top)':>16} {'holes':>6}")
    print(hdr)
    print("-" * len(hdr))

    for (sym, per) in sorted(by_key):
        recs = sorted(by_key[(sym, per)], key=lambda r: r["ts_utc_ms"])
        okrecs = [r for r in recs if r.get("ok")]
        n_ok = len(okrecs)
        if not n_ok:
            print(f"{sym:<16} {per:<4} {0:>5}  (no ok samples)")
            continue
        closed_frac = sum(1 for r in okrecs if not r.get("is_in_progress")) / n_ok

        # 发布延迟 D:newest_open 单根跳变
        pms = recs[0]["period_ms"]
        ds, gap_jumps = [], 0
        prev_open = None
        for r in okrecs:
            no = r.get("newest_open_ms")
            if no is None:
                continue
            if prev_open is not None and no > prev_open:
                if no - prev_open == pms:
                    ds.append(r["ts_utc_ms"] - no)   # 上界
                else:
                    gap_jumps += 1
            prev_open = no
        ds_sorted = sorted(ds)
        d_med = statistics.median(ds_sorted) if ds_sorted else None
        d_p95, d_max = _pct(ds_sorted, 0.95), (ds_sorted[-1] if ds_sorted else None)

        # 缺几根 top
        miss = Counter(r.get("missing_bars") for r in okrecs if r.get("missing_bars") is not None)
        miss_s = ", ".join(f"{k}:{v}" for k, v in sorted(miss.items())[:4]) or "—"

        # 采样空洞:相邻间隔 >3× 中位
        gaps = [b["ts_utc_ms"] - a["ts_utc_ms"] for a, b in zip(recs, recs[1:])]
        med_gap = statistics.median(gaps) if gaps else 0
        holes = sum(1 for g in gaps if med_gap and g > 3 * med_gap)

        print(f"{sym:<16} {per:<4} {n_ok:>5} {closed_frac * 100:>7.0f}% "
              f"{len(ds):>4} {_fmt_s(d_med):>7} {_fmt_s(d_p95):>7} {_fmt_s(d_max):>7} "
              f"{miss_s:>16} {holes:>6}")

    print("\n解读:")
    print("  closed% 高(尤其 5m)→ 发布延迟 > 周期,最新一根几乎总已收盘(问题①高频路径)。")
    print("  D med/p95 = 一根 bar 从 open 到首次出现在 rubik 的滞后(估上界);看是否稳定。")
    print("  missing(top) 'k:v' = 缺 k 根出现 v 次;1d 为 None(16:00 锚定不计)。")
    print("  holes>0 → 有采样空洞(疑似睡眠/网络中断),该 key 的 D/占比含偏差。")


if __name__ == "__main__":
    main()
