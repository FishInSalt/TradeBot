import { describe, it, expect } from "vitest";
import { toCandleData, snapToBarTime, toMarkers, POS_HEX, NEG_HEX, MUTED_HEX } from "@/utils/markers";
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
