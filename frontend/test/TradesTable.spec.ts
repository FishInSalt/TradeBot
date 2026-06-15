import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import TradesTable from "@/components/TradesTable.vue";
import type { TradeRow } from "@/api/client";

const f = (o: Partial<TradeRow>): TradeRow => ({
  at: "2026-06-12T10:00:00Z", action: "order_filled", side: "short",
  price: 65000, amount: 10, pnl: null, fee: 1, trigger_reason: "market", ...o,
});

describe("TradesTable (A+)", () => {
  it("单位标注在表区顶部（不在每格重复）", () => {
    const w = mount(TradesTable, { props: { trades: [f({})] } });
    expect(w.text()).toContain("USDT");
    expect(w.text()).toContain("UTC");
  });

  it("加仓周期：开/加/平 + 平仓行逐笔算式 + 开仓行最终收益占位", () => {
    const w = mount(TradesTable, {
      props: { trades: [
        f({ pnl: null, fee: 1 }),
        f({ pnl: null, fee: 1 }),                          // 加仓
        f({ pnl: -7.62, fee: 1, trigger_reason: "market" }),  // final -10.62 (3×fee=1)
      ] },
    });
    const t = w.text();
    expect(t).toContain("加仓");
    expect(t).toContain("平仓");
    expect(t).toContain("−10.62");       // 最终收益（U+2212）
    expect(t).toContain("=");            // 逐笔算式行前缀
  });

  it("止损平仓触发细分标签", () => {
    const w = mount(TradesTable, {
      props: { trades: [f({ pnl: null }), f({ pnl: -50, trigger_reason: "stop" })] },
    });
    expect(w.text()).toContain("止损平仓");
  });

  it("周期交替底色 class（两周期 → ep-even + ep-odd 行）", () => {
    const w = mount(TradesTable, {
      props: { trades: [
        f({ pnl: null }), f({ pnl: 10 }),
        f({ pnl: null }), f({ pnl: -5 }),
      ] },
    });
    expect(w.find(".ep-even").exists()).toBe(true);
    expect(w.find(".ep-odd").exists()).toBe(true);
  });

  it("§全局 成交时刻按 UTC 展示", () => {
    const w = mount(TradesTable, { props: { trades: [f({ pnl: 10 })] } });
    expect(w.text()).toContain("2026-06-12 10:00:00");
  });
});
