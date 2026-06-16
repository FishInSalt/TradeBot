/** 价格 K 线买卖点 markers 纯函数。消费 deriveTradeFills 输出（单一口径，与 A+ 表同源，spec §D）。 */
import type { OhlcvBar } from "@/api/client";
import type { DerivedFill } from "@/utils/trades";
import { epochSec } from "@/utils/time";
import type { CandlestickData, SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";

// canvas 不能读 CSS 变量；镜像 --ob-pos / --ob-neg / --ob-text-muted（改这三处须同步 tokens.css）。
export const POS_HEX = "#15803d";
export const NEG_HEX = "#dc2626";
export const MUTED_HEX = "#6b7280";

/** OhlcvBar[] → candlestick data。秒级 UTCTimestamp、升序、同秒去重保留最后（镜像 EquityChart.toSeriesData）。 */
export function toCandleData(bars: OhlcvBar[]): CandlestickData[] {
  const byTime = new Map<number, CandlestickData>();
  for (const b of bars) {
    const sec = epochSec(b.at);
    byTime.set(sec, { time: sec as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close });
  }
  return [...byTime.values()].sort((a, b) => (a.time as number) - (b.time as number));
}

/** 成交秒戳吸附到 ≤ 它的最大已加载 bar 时间（用实际 candle，非 floor-to-tf——处理行情缺口）。
 *  早于首根 → 钳首根；barTimes 空 → 返回原值（无图可标）。barTimes 须升序（取自 toCandleData 的 time 列）。 */
export function snapToBarTime(atSec: number, barTimes: number[]): number {
  if (barTimes.length === 0) return atSec;
  if (atSec <= barTimes[0]) return barTimes[0];
  let lo = 0, hi = barTimes.length - 1, ans = barTimes[0];
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (barTimes[mid] <= atSec) { ans = barTimes[mid]; lo = mid + 1; }
    else hi = mid - 1;
  }
  return ans;
}

/** DerivedFill[] → markers。time 经 snapToBarTime（与 hover map 键同源，保 crosshair param.time 命中）。 */
export function toMarkers(fills: DerivedFill[], barTimes: number[]): SeriesMarker<Time>[] {
  const markers: SeriesMarker<Time>[] = fills.map((f) => {
    const isOpen = f.grossPnl == null;                          // 开/加型（与表同判据）
    const color = f.side === "long" ? POS_HEX : f.side === "short" ? NEG_HEX : MUTED_HEX;
    return {
      time: snapToBarTime(epochSec(f.at), barTimes) as UTCTimestamp,
      position: isOpen ? "belowBar" : "aboveBar",               // 进场标在下、出场标在上
      shape: isOpen ? "arrowUp" : "arrowDown",
      color,
      text: isOpen ? (f.isAdd ? "加" : "开") : "平",            // 常驻短标签；细分/数值留 hover
    };
  });
  return markers.sort((a, b) => (a.time as number) - (b.time as number));  // lightweight-charts 要求 time 升序
}
