import { describe, it, expect } from "vitest";
import { deriveTradeFills, summarizeEpisodes, OPEN_LABEL, CLOSE_LABEL } from "@/utils/trades";
import type { TradeRow } from "@/api/client";

const f = (o: Partial<TradeRow>): TradeRow => ({
  at: "2026-06-12T10:00:00Z", action: "order_filled", side: "long",
  price: 50000, amount: 0.1, pnl: null, fee: 1, trigger_reason: "market", ...o,
});

describe("OPEN_LABEL / CLOSE_LABEL", () => {
  it("OPEN_LABEL：limit 区分加仓、market/未知泛标签", () => {
    expect(OPEN_LABEL("limit", false)).toBe("限价开仓");
    expect(OPEN_LABEL("limit", true)).toBe("限价加仓");
    expect(OPEN_LABEL("market", false)).toBe("开仓");
    expect(OPEN_LABEL("market", true)).toBe("加仓");
    expect(OPEN_LABEL(null, false)).toBe("开仓");
  });
  it("CLOSE_LABEL：五标签", () => {
    expect(CLOSE_LABEL("stop")).toBe("止损平仓");
    expect(CLOSE_LABEL("take_profit")).toBe("止盈平仓");
    expect(CLOSE_LABEL("liquidation")).toBe("强平");
    expect(CLOSE_LABEL("limit")).toBe("限价平仓");
    expect(CLOSE_LABEL("market")).toBe("平仓");
    expect(CLOSE_LABEL(null)).toBe("平仓");
  });
});

describe("deriveTradeFills", () => {
  it("单开单平（market）→ 2 行、类型开仓/平仓、最终收益扣两费、episodeIndex 0", () => {
    const out = deriveTradeFills([
      f({ pnl: null, fee: 1 }),
      f({ pnl: 100, fee: 1.5, trigger_reason: "market" }),
    ]);
    expect(out.map((r) => r.type)).toEqual(["开仓", "平仓"]);
    expect(out[0].finalPnl).toBeNull();
    expect(out[0].grossPnl).toBeNull();
    expect(out[1].grossPnl).toBe(100);
    expect(out[1].finalPnl).toBeCloseTo(100 - 1 - 1.5, 6);
    expect(out[1].feeBreakdown).toEqual([1, 1.5]);
    expect(out.every((r) => r.episodeIndex === 0)).toBe(true);
  });

  it("加仓周期（开+加+平）→ 中间行加仓、平仓扣三费、同 episodeIndex", () => {
    const out = deriveTradeFills([
      f({ pnl: null, fee: 1 }),
      f({ pnl: null, fee: 1 }),                 // 加仓
      f({ pnl: -50, fee: 1, trigger_reason: "stop" }),
    ]);
    expect(out.map((r) => r.type)).toEqual(["开仓", "加仓", "止损平仓"]);
    expect(out[1].isAdd).toBe(true);
    expect(out[2].finalPnl).toBeCloseTo(-50 - 3, 6);
    expect(out[2].feeBreakdown).toEqual([1, 1, 1]);
    expect(out.every((r) => r.episodeIndex === 0)).toBe(true);
  });

  it("两个连续周期 → episodeIndex 0/1", () => {
    const out = deriveTradeFills([
      f({ pnl: null }), f({ pnl: 10 }),
      f({ pnl: null }), f({ pnl: -5 }),
    ]);
    expect(out.map((r) => r.episodeIndex)).toEqual([0, 0, 1, 1]);
  });

  it("尾部未平仓 → 末行 finalPnl=null、不递增 episodeIndex", () => {
    const out = deriveTradeFills([
      f({ pnl: null }), f({ pnl: 10 }),
      f({ pnl: null }),                         // 尾部开仓未平
    ]);
    expect(out[2].finalPnl).toBeNull();
    expect(out[2].episodeIndex).toBe(1);
  });

  it("孤儿平仓（无前开仓）→ finalPnl = pnl − 平费", () => {
    const out = deriveTradeFills([f({ pnl: -9.62, fee: 3, trigger_reason: "stop" })]);
    expect(out[0].finalPnl).toBeCloseTo(-9.62 - 3, 6);
    expect(out[0].feeBreakdown).toEqual([3]);
  });

  it("缺失 fee → 按 0", () => {
    const out = deriveTradeFills([f({ pnl: null, fee: null }), f({ pnl: 10, fee: null })]);
    expect(out[1].finalPnl).toBe(10);
    expect(out[1].feeBreakdown).toEqual([0, 0]);
  });

  it("legacy null-amount fill 被跳过", () => {
    expect(deriveTradeFills([f({ amount: null, pnl: null }), f({ amount: null, pnl: 100 })])).toEqual([]);
    const mixed = deriveTradeFills([
      f({ amount: null, pnl: null }),           // legacy 跳过
      f({ pnl: null, fee: 1 }),                 // clean 开
      f({ pnl: 50, fee: 1 }),                   // clean 平
    ]);
    expect(mixed.map((r) => r.type)).toEqual(["开仓", "平仓"]);
  });
});

describe("summarizeEpisodes", () => {
  it("1 胜 1 负 → 计数/胜率/盈亏比/最佳最差", () => {
    const fills = deriveTradeFills([
      f({ pnl: null, fee: 1 }), f({ pnl: 100, fee: 1 }),          // ep0 win: final 98
      f({ pnl: null, fee: 1 }), f({ pnl: -50, fee: 1 }),         // ep1 loss: final -52
    ]);
    const s = summarizeEpisodes(fills);
    expect(s.episodes).toBe(2);
    expect(s.wins).toBe(1);
    expect(s.losses).toBe(1);
    expect(s.winRate).toBeCloseTo(0.5, 6);
    expect(s.profitFactor).toBeCloseTo(98 / 52, 4);
    expect(s.best).toBeCloseTo(98, 6);
    expect(s.worst).toBeCloseTo(-52, 6);
  });

  it("全打平（胜+负=0）→ 净胜率 / 盈亏比 null，不抛", () => {
    const fills = deriveTradeFills([f({ pnl: null, fee: 1 }), f({ pnl: 2, fee: 1 })]);  // final 0
    const s = summarizeEpisodes(fills);
    expect(s.episodes).toBe(1);
    expect(s.winRate).toBeNull();
    expect(s.profitFactor).toBeNull();
  });

  it("无已平仓周期（空 / 仅未平仓开仓）→ 全 null/0", () => {
    expect(summarizeEpisodes([])).toEqual({
      episodes: 0, wins: 0, losses: 0, winRate: null, profitFactor: null, best: null, worst: null,
    });
    const onlyOpen = summarizeEpisodes(deriveTradeFills([f({ pnl: null })]));
    expect(onlyOpen.episodes).toBe(0);
    expect(onlyOpen.best).toBeNull();
  });

  it("有盈无亏 → 盈亏比 null（分母 0，避免 ∞）", () => {
    const s = summarizeEpisodes(deriveTradeFills([f({ pnl: null, fee: 1 }), f({ pnl: 100, fee: 1 })]));
    expect(s.profitFactor).toBeNull();
  });
});
