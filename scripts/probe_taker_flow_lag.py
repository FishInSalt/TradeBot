"""探针:定时轮询 OKX rubik taker-volume 各 period 的最新 bar,落 jsonl,
用于实证'发布延迟 D'与'closed 占比'画像(get_taker_flow 问题①/②的 grounding)。

直接调 ccxt public_get_rubik_stat_taker_volume_contract —— 与 SimulatedExchange.
fetch_taker_flow 同一上游;sim 包装(reverse/TakerFlowBar)不影响 newest.ts,且
sim 限定单 symbol,故探针走 ccxt 以测多 symbol。

长跑(>10min),建议防睡眠(否则 clamshell sleep 会在 jsonl 留空洞):
  caffeinate -dis python scripts/probe_taker_flow_lag.py --hours 24

默认:30s 间隔、BTC/ETH/SOL、5m/1h/4h/1d、append 落 .working/taker_flow_lag.jsonl
（append 模式 → 中断后重跑会续写同一文件）。配套分析:analyze_taker_flow_lag.py
"""
import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone

import ccxt.async_support as ccxt

from src.integrations.exchange.base import _TAKER_VOLUME_PERIOD
from src.agent.tools_perception import _TAKER_FLOW_PERIOD_MS

SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
PERIODS = ["5m", "1h", "4h", "1d"]
DEFAULT_OUT = ".working/taker_flow_lag.jsonl"


async def sample_one(client, inst_id, symbol, period):
    """一次采样:取该 (symbol, period) 最新 bar 的 open + 是否 in-progress。
    now_ms 在 fetch 当刻取,故每条记录自带其精确观测时刻。"""
    pms = _TAKER_FLOW_PERIOD_MS[period]
    now_ms = int(time.time() * 1000)
    rec = {"ts_utc_ms": now_ms, "symbol": symbol, "period": period, "period_ms": pms}
    try:
        raw = await client.public_get_rubik_stat_taker_volume_contract({
            "instId": inst_id, "period": _TAKER_VOLUME_PERIOD[period],
            "unit": "2", "limit": "2",
        })
        rows = raw.get("data") or []
        if not rows:
            rec.update(ok=False, error="empty")
            return rec
        newest_open = int(rows[0][0])          # OKX newest-first → rows[0] = newest
        newest_close = newest_open + pms
        rec.update(ok=True, newest_open_ms=newest_open, newest_close_ms=newest_close,
                   is_in_progress=newest_close > now_ms, n_rows=len(rows))
        # 缺几根:UTC-floor 对 5m/1h/4h 准;1d 因 16:00(UTC+8 日界)锚定,floor 不适用 → None
        if period != "1d":
            cur_open = (now_ms // pms) * pms
            rec["missing_bars"] = (cur_open - newest_open) // pms
        else:
            rec["missing_bars"] = None
    except Exception as e:
        rec.update(ok=False, error=type(e).__name__)
    return rec


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0, help="总时长(小时)")
    ap.add_argument("--interval", type=float, default=30.0, help="采样间隔(秒)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    client = ccxt.okx()
    try:
        await client.load_markets()
        inst = {s: client.market(s)["id"] for s in SYMBOLS}
        deadline = time.time() + args.hours * 3600
        rounds = 0
        per_round = len(SYMBOLS) * len(PERIODS)
        print(f"probe start {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC · "
              f"{len(SYMBOLS)} symbols × {len(PERIODS)} periods · {args.interval:g}s · "
              f"{args.hours:g}h → {args.out}")
        with open(args.out, "a") as f:
            while time.time() < deadline:
                round_start = time.time()
                for s in SYMBOLS:
                    for p in PERIODS:
                        rec = await sample_one(client, inst[s], s, p)
                        f.write(json.dumps(rec) + "\n")
                f.flush()
                rounds += 1
                if rounds % 10 == 0:
                    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {rounds} rounds · "
                          f"{rounds * per_round} samples")
                await asyncio.sleep(max(1.0, args.interval - (time.time() - round_start)))
    finally:
        await client.close()
    print(f"done · {rounds} rounds · {rounds * per_round} samples → {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
