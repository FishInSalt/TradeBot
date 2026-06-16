import { describe, it, expect } from "vitest";
import { toCandleData, snapToBarTime, toMarkers, clampBarSpacing, latestVisibleRange, POS_HEX, NEG_HEX, MUTED_HEX } from "@/utils/markers";
import { deriveTradeFills } from "@/utils/trades";
import { epochSec } from "@/utils/time";
import type { TradeRow, OhlcvBar } from "@/api/client";

const bar = (at: string, o = 1, h = 2, l = 0.5, c = 1.5): OhlcvBar =>
  ({ at, open: o, high: h, low: l, close: c, volume: 10 });

describe("toCandleData", () => {
  it("ISO→秒级、升序、同秒去重保留最后、映 OHLC", () => {
    const d = toCandleData([
      bar("2026-06-12T10:01:00Z", 2),
      bar("2026-06-12T10:00:00Z", 1),
      bar("2026-06-12T10:00:00Z", 9),   // 同秒 → 保留最后
    ]);
    expect(d.length).toBe(2);
    expect((d[0].time as number) < (d[1].time as number)).toBe(true);
    expect(d[0].open).toBe(9);          // 同秒保留最后
  });
});

describe("snapToBarTime", () => {
  const t = (s: string) => epochSec(s);
  const barTimes = [t("2026-06-12T10:00:00Z"), t("2026-06-12T10:05:00Z"), t("2026-06-12T10:15:00Z")];
  it("成交落 bar 内 → 吸附该 bar 开盘时间", () => {
    expect(snapToBarTime(t("2026-06-12T10:07:00Z"), barTimes)).toBe(t("2026-06-12T10:05:00Z"));
  });
  it("有缺口 → 吸附到最近较早 bar（非不存在的 floor 时间）", () => {
    // 10:05 与 10:15 间缺 10:10 这根；落在 10:12 → 吸附 10:05
    expect(snapToBarTime(t("2026-06-12T10:12:00Z"), barTimes)).toBe(t("2026-06-12T10:05:00Z"));
  });
  it("早于首根 → 钳首根", () => {
    expect(snapToBarTime(t("2026-06-12T09:00:00Z"), barTimes)).toBe(barTimes[0]);
  });
  it("barTimes 空 → 返回原值", () => {
    expect(snapToBarTime(12345, [])).toBe(12345);
  });
  it("atSec 精确等于某非首 bar → 返回该 bar（<= 语义）", () => {
    expect(snapToBarTime(t("2026-06-12T10:05:00Z"), barTimes)).toBe(t("2026-06-12T10:05:00Z"));
  });
});

describe("clampBarSpacing", () => {
  it("理想间距落 [min,max] 内 → 原样返回", () => {
    expect(clampBarSpacing(1000, 100, 8, 16)).toBeCloseTo(10);  // 1000/100 = 10
  });
  it("粗周期 bar 少 → 夹到 max（蜡烛不膨胀）", () => {
    expect(clampBarSpacing(1000, 10, 8, 16)).toBe(16);          // 100 > 16
  });
  it("细周期 bar 多 → 夹到 min（保可读）", () => {
    expect(clampBarSpacing(1000, 2000, 8, 16)).toBe(8);         // 0.5 < 8
  });
  it("默认 min=8（与生产 MIN_BAR_SPACING 一致，不留非生产值）", () => {
    expect(clampBarSpacing(1000, 2000)).toBe(8);                // 默认实参即生产档
  });
  it("bar ≤ 1 或宽 ≤ 0 → max（退化兜底）", () => {
    expect(clampBarSpacing(1000, 1, 8, 16)).toBe(16);
    expect(clampBarSpacing(0, 100, 8, 16)).toBe(16);
  });
});

describe("latestVisibleRange", () => {
  it("中周期恰好放下 → 满铺全宽（from≈-0.5，末根贴右）", () => {
    const r = latestVisibleRange(800, 100, 8, 16)!;   // ideal=8 落区间内 → 可见 100 == barCount
    expect(r.to).toBe(99.5);
    expect(r.from).toBeCloseTo(-0.5);
  });
  it("细周期放不下 → 右锚最新（from>0，仅末段可见、其余可左滚）", () => {
    const r = latestVisibleRange(800, 2000, 8, 16)!;  // spacing 夹到 8 → 可见 100 << 2000
    expect(r.to).toBe(1999.5);                          // 末根恒贴右
    expect(r.from).toBeGreaterThan(0);                  // 起点不在视野内（可左滚回看）
    expect(r.to - r.from).toBeCloseTo(100);             // 可见根数 = 800/8
  });
  it("粗周期 bar 少 → 末根仍贴右、左侧留白（from<0）", () => {
    const r = latestVisibleRange(800, 5, 8, 16)!;       // ideal 大 → 夹到 16 → 可见 50 >> 5
    expect(r.to).toBe(4.5);
    expect(r.from).toBeLessThan(0);
  });
  it("width≤0 或 barCount<1 → null（调用方兜底 fitContent）", () => {
    expect(latestVisibleRange(0, 100, 8, 16)).toBeNull();
    expect(latestVisibleRange(800, 0, 8, 16)).toBeNull();
  });
});

describe("toMarkers", () => {
  const t = (s: string) => epochSec(s);
  const barTimes = [t("2026-06-12T10:00:00Z"), t("2026-06-12T10:05:00Z"),
                    t("2026-06-12T10:10:00Z"), t("2026-06-12T10:15:00Z")];
  const longTrades: TradeRow[] = [
    { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "long", price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
    { at: "2026-06-12T10:15:00Z", action: "order_filled", side: "long", price: 66000, amount: 1, pnl: 1000, fee: 1, trigger_reason: "stop" },
  ];

  it("单开单平 long → 2 markers：开 belowBar/arrowUp/POS/「开」、平 aboveBar/arrowDown/POS/「平」", () => {
    const ms = toMarkers(deriveTradeFills(longTrades), barTimes);
    expect(ms.length).toBe(2);
    expect(ms[0]).toMatchObject({ position: "belowBar", shape: "arrowUp", color: POS_HEX, text: "开" });
    expect(ms[1]).toMatchObject({ position: "aboveBar", shape: "arrowDown", color: POS_HEX, text: "平" });
    expect((ms[0].time as number) < (ms[1].time as number)).toBe(true);   // 按 time 升序
  });

  it("marker.time === snapToBarTime(epochSec(fill.at), barTimes)（与 hover map 键同源）", () => {
    const fills = deriveTradeFills(longTrades);
    const ms = toMarkers(fills, barTimes);
    expect(ms[0].time).toBe(snapToBarTime(epochSec(fills[0].at), barTimes));
  });

  it("加仓行 isAdd → text「加」、belowBar", () => {
    const adds: TradeRow[] = [
      { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "long", price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
      { at: "2026-06-12T10:05:00Z", action: "order_filled", side: "long", price: 65500, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
    ];
    const ms = toMarkers(deriveTradeFills(adds), barTimes);
    expect(ms[1]).toMatchObject({ text: "加", position: "belowBar" });
  });

  it("short → NEG 色；side null → MUTED 色", () => {
    const shortClose: TradeRow[] = [
      { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "short", price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
    ];
    expect(toMarkers(deriveTradeFills(shortClose), barTimes)[0].color).toBe(NEG_HEX);
    const noSide: TradeRow[] = [
      { at: "2026-06-12T10:00:00Z", action: "order_filled", side: null, price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
    ];
    expect(toMarkers(deriveTradeFills(noSide), barTimes)[0].color).toBe(MUTED_HEX);
  });

  it("平仓细分（stop）不改 marker text（仍「平」，细分留 hover）", () => {
    const ms = toMarkers(deriveTradeFills(longTrades), barTimes);
    expect(ms[1].text).toBe("平");   // longTrades[1].trigger_reason === "stop"
  });

  it("空 fills → []", () => {
    expect(toMarkers([], barTimes)).toEqual([]);
  });

  it("同口径：markers 数 == deriveTradeFills 行数（同一样本）", () => {
    const fills = deriveTradeFills(longTrades);
    expect(toMarkers(fills, barTimes).length).toBe(fills.length);
  });

  it("两个完整 episode → markers 数 == deriveTradeFills 行数、且 time 升序", () => {
    const twoEp: TradeRow[] = [
      { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "long", price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
      { at: "2026-06-12T10:05:00Z", action: "order_filled", side: "long", price: 66000, amount: 1, pnl: 1000, fee: 1, trigger_reason: "tp" },
      { at: "2026-06-12T10:10:00Z", action: "order_filled", side: "short", price: 66000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
      { at: "2026-06-12T10:15:00Z", action: "order_filled", side: "short", price: 65000, amount: 1, pnl: 500, fee: 1, trigger_reason: "tp" },
    ];
    const fills = deriveTradeFills(twoEp);
    const ms = toMarkers(fills, barTimes);
    expect(ms.length).toBe(fills.length);
    const times = ms.map((m) => m.time as number);
    expect(times).toEqual([...times].sort((a, b) => a - b));   // 升序
  });
});
