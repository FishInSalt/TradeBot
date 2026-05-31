"""Grounding: 端到端真实拉取 OKX → get_recent_trades / get_taker_flow 渲染
+ 从原始数据独立重算关键聚合做交叉校验（验证"真实、准确"）。

用法：python scripts/grounding_order_flow_tools.py
一次性 grounding 工件，非测试；ephemeral。
"""
import asyncio
import statistics
from unittest.mock import MagicMock

import ccxt.async_support as ccxt

from src.integrations.exchange.simulated import SimulatedExchange
from src.integrations.market_data import MarketDataService
from src.agent.tools_perception import (
    get_recent_trades, get_taker_flow,
    _TAKER_FLOW_RVOL_BARS, _TAKER_FLOW_PERIOD_MS, _TAKER_FLOW_ANCHOR,
)

SYMBOL = "BTC/USDT:USDT"


def _make_sim() -> SimulatedExchange:
    config = MagicMock()
    config.fee_rate = 0.0005
    return SimulatedExchange(config=config, db_engine=None, session_id="grounding", symbol=SYMBOL)


def _deps(market_data) -> MagicMock:
    d = MagicMock()
    d.symbol = SYMBOL
    d.market_data = market_data
    return d


def _hr(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


async def check_recent_trades(ex, md):
    _hr("get_recent_trades — TOOL OUTPUT")
    print(await get_recent_trades(_deps(md)))

    _hr("get_recent_trades — INDEPENDENT RECOMPUTE (from raw fetch_trades)")
    trades = await ex.fetch_trades(SYMBOL, limit=500)
    trades = sorted(trades, key=lambda t: t.timestamp)
    n = len(trades)
    span_s = (trades[-1].timestamp - trades[0].timestamp) / 1000
    usd = [t.amount * t.price for t in trades]
    total = sum(usd)
    buy_usd = sum(u for u, t in zip(usd, trades) if t.side == "buy")
    buy_cnt = sum(1 for t in trades if t.side == "buy")
    net = buy_usd - (total - buy_usd)
    srt = sorted(usd)
    p95 = srt[min(int(0.95 * n), n - 1)]
    li = max(range(n), key=lambda i: usd[i])
    cs = float((ex._ccxt.market(SYMBOL) or {}).get("contractSize") or 1.0)
    raw0 = await ex._ccxt.fetch_trades(SYMBOL, limit=1)
    print(f"  contractSize (张→base 乘子)        : {cs}")
    print(f"  raw ccxt amount (1 trade, 张)       : {raw0[0]['amount']}  → base={raw0[0]['amount'] * cs}")
    print(f"  n trades                            : {n}")
    print(f"  span_s                              : {span_s:.1f}s   ({n / span_s:.1f} tr/s)")
    print(f"  taker buy %(count)                  : {buy_cnt / n * 100:.1f}%")
    print(f"  taker buy %(volume)                 : {buy_usd / total * 100:.1f}%")
    print(f"  net USD                             : {net:+,.0f}")
    print(f"  total window USD                    : {total:,.0f}")
    print(f"  largest single                      : {usd[li]:,.0f} {trades[li].side.upper()} "
          f"({usd[li] / total * 100:.1f}% of window)")
    print(f"  size med / mean / p95 (USD)         : {statistics.median(srt):,.0f} / "
          f"{total / n:,.0f} / {p95:,.0f}")


async def check_taker_flow(ex, md, period, limit):
    _hr(f"get_taker_flow(period={period!r}, limit={limit}) — TOOL OUTPUT")
    print(await get_taker_flow(_deps(md), period=period, limit=limit))

    _hr(f"get_taker_flow({period!r}) — INDEPENDENT RECOMPUTE (from raw rubik)")
    n_fetch = max(limit + 1, 21)
    bars = await ex.fetch_taker_flow(SYMBOL, period, n_fetch)  # ascending, [-1]=in-progress
    import time
    now_ms = int(time.time() * 1000)
    period_ms = _TAKER_FLOW_PERIOD_MS[period]
    newest = bars[-1]
    is_ip = newest.ts + period_ms > now_ms
    closed = bars[:-1] if is_ip else bars
    baseline = closed[-_TAKER_FLOW_RVOL_BARS:]
    baseline_avg = (sum(b.sell_usd + b.buy_usd for b in baseline) / len(baseline)
                    if len(baseline) >= _TAKER_FLOW_RVOL_BARS else None)
    display = bars[-limit:]

    def total(b): return b.sell_usd + b.buy_usd
    def net(b): return b.buy_usd - b.sell_usd
    def buypct(b): return (b.buy_usd / total(b) * 100) if total(b) > 0 else 0.0

    print(f"  fetched bars                        : {len(bars)} (asc; [-1]=in-progress={is_ip})")
    print(f"  baseline bars for RVol              : {len(baseline)} (need {_TAKER_FLOW_RVOL_BARS}); "
          f"avg total USD = {baseline_avg and f'{baseline_avg:,.0f}'}")
    print(f"  anchor up-tier                      : {_TAKER_FLOW_ANCHOR[period]}")
    print(f"  {'bar_open_ms':>14}  {'buy%':>5}  {'net($)':>14}  {'total($)':>14}  "
          f"{'RVol':>5}  {'CVD($)':>14}")
    cvd = 0.0
    for b in display:
        cvd += net(b)
        rvol = (total(b) / baseline_avg) if baseline_avg else None
        print(f"  {b.ts:>14}  {buypct(b):>4.0f}%  {net(b):>+14,.0f}  {total(b):>14,.0f}  "
              f"{(f'{rvol:.1f}x' if rvol else '—'):>5}  {cvd:>+14,.0f}")


async def main():
    ex = _make_sim()
    ex._ccxt = ccxt.okx()
    try:
        await ex._ccxt.load_markets()
        md = MarketDataService(ex)
        await check_recent_trades(ex, md)
        await check_taker_flow(ex, md, "5m", 6)
        await check_taker_flow(ex, md, "1h", 6)
        await check_taker_flow(ex, md, "1d", 3)
    finally:
        await ex._ccxt.close()


if __name__ == "__main__":
    asyncio.run(main())
