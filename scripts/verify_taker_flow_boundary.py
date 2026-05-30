"""验证 get_taker_flow 在 bar 边界后的发布窗口内,任意 period 都会让最新 bar
已 closed,从而触发表头 'row 1 = current in-progress' 的假事实(问题①)。

A) 真实网络:测每个 period 的 rubik 发布滞后(当前 wall bar 是否已发布)。
B) 纯合成(确定性):把 now 设在某整点后 2 分钟、当前 bar 未发布,跑 1h 渲染,
   展示 is_in_progress=False 时表头仍断言 in-progress 的矛盾。

用法：python scripts/verify_taker_flow_boundary.py
"""
import asyncio
import time
from unittest.mock import MagicMock

import ccxt.async_support as ccxt

from src.integrations.exchange.simulated import SimulatedExchange
from src.integrations.exchange.base import TakerFlowBar
from src.agent.tools_perception import _render_taker_flow, _TAKER_FLOW_PERIOD_MS

SYMBOL = "BTC/USDT:USDT"


def _make_sim() -> SimulatedExchange:
    config = MagicMock()
    config.fee_rate = 0.0005
    config.precision = {SYMBOL: 3}
    return SimulatedExchange(config=config, db_engine=None, session_id="verify", symbol=SYMBOL)


def _fmt(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M:%S")


async def part_a(ex):
    print("=" * 78)
    print("A) 真实 rubik 发布滞后探测 (UTC-aligned floor;1d 因 16:00 锚定单列说明)")
    print("=" * 78)
    now_ms = int(time.time() * 1000)
    print(f"  wall-clock now = {_fmt(now_ms)} UTC\n")
    print(f"  {'period':>6}  {'newest bar open':>17}  {'newest close':>17}  "
          f"{'当前应在bar open':>17}  {'缺几根':>6}  {'is_in_progress':>14}")
    for period in ("5m", "1h", "4h", "1d"):
        bars = await ex.fetch_taker_flow(SYMBOL, period, 3)
        newest = bars[-1]
        pms = _TAKER_FLOW_PERIOD_MS[period]
        newest_close = newest.ts + pms
        is_ip = newest_close > now_ms
        if period == "1d":
            cur_open_s, missing_s = "(16:00 UTC 锚定,floor不适用)", "—"
        else:
            cur_open = (now_ms // pms) * pms
            missing = (cur_open - newest.ts) // pms
            cur_open_s, missing_s = _fmt(cur_open), str(missing)
        print(f"  {period:>6}  {_fmt(newest.ts):>17}  {_fmt(newest_close):>17}  "
              f"{cur_open_s:>17}  {missing_s:>6}  {str(is_ip):>14}")
    print("\n  解读:'缺几根'>=1 → 当前 wall bar 尚未发布,最新返回 bar 已 closed,")
    print("        is_in_progress=False。这等价于'整点后处于发布滞后窗口'。\n")


def part_b():
    print("=" * 78)
    print("B) 确定性复现:1h period,now 落在整点后 2 分钟、当前 bar 未发布")
    print("=" * 78)
    # 21 根 1h bars,最新一根 open = 13:00 UTC(整点对齐),已于 14:00 收盘。
    # now = 14:02 UTC —— 14:00 那根'当前 bar'尚未发布(故不放进 bars)。
    T = 1780146000000  # 1h-aligned open (= 13:00 UTC of the sample day)
    pms = _TAKER_FLOW_PERIOD_MS["1h"]
    bars = [TakerFlowBar(ts=T - i * pms, sell_usd=5e6, buy_usd=6e6) for i in range(21)]
    bars.reverse()  # ascending; bars[-1].ts == T (13:00, already closed)
    now_ms = T + pms + 120_000  # 14:02 UTC

    out = _render_taker_flow(
        bars, "1h", 6, now_ms=now_ms, symbol=SYMBOL, fetch_ts="14:02:00",
        closes=None, close_note="Close: n/a (synthetic)", anchor=None,
    )
    print(out)

    newest = bars[-1]
    is_ip = newest.ts + pms > now_ms
    print("\n  --- 矛盾点 ---")
    print(f"  is_in_progress              = {is_ip}  (newest 13:00 已于 14:00 收盘,now=14:02)")
    print(f"  'Now' 行说                  : {'closed' if not is_ip else 'forming'}  ✅ 正确")
    print(f"  row 1 是否打星              : {'否' if not is_ip else '是'}  ✅ 正确")
    print(f"  脚注 [* still forming]      : {'无' if not is_ip else '有'}  ✅ 正确")
    has_false_header = "row 1 = current in-progress" in out and not is_ip
    print(f"  表头仍断言 in-progress      : {'是 ❌ 假事实' if has_false_header else '否'}")
    print("\n  → 1h(及任意 period)在 bar 边界后的发布窗口内,同样触发问题①。\n")


async def main():
    ex = _make_sim()
    ex._ccxt = ccxt.okx()
    try:
        await ex._ccxt.load_markets()
        await part_a(ex)
    finally:
        await ex._ccxt.close()
    part_b()


if __name__ == "__main__":
    asyncio.run(main())
