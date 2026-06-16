import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
    addCandlestickSeries: vi.fn(() => ({ setData: vi.fn(), setMarkers: vi.fn() })),
    subscribeCrosshairMove: vi.fn(),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    remove: vi.fn(),
  })),
}));

import PerformanceBar from "@/components/PerformanceBar.vue";

const PriceChartStub = {
  name: "PriceChart",
  props: ["sessionId", "symbol", "defaultTimeframe", "trades"],
  template: "<div class='pc-stub'></div>",
};

const DETAIL = {
  id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1H",
  scheduler_interval_min: 15, initial_balance: 10000, token_budget: 0,
  created_at: "2026-06-12T08:00:00Z", last_active_at: "2026-06-12T10:00:00Z", system_prompt: null,
};

// 2 周期（1 胜 1 负）trades + 标量字段。
// 注：标量（net_pnl/total_pnl/total_fees）与 trades 是【独立测试输入】——组件 PnL/费/% 取 API 标量、
// 计数/胜率/盈亏比取 episode 派生（deriveTradeFills），二者不交叉校验（镜像真实双来源设计），
// 故此处标量值无需与 trades 的 Σfinals 对齐（如 net_pnl=-95.14 用于触发负色，与 trades 的 +46 无关）。
const PERF_FLAT = {
  initial_balance: 10000, current_position: "flat",
  total_return_pct: 0.07, total_pnl: 7, net_pnl: -95.14, net_win_rate: 0.14, max_drawdown_pct: 0.95,  // MDD 恒非负（metrics.py max_dd_ratio*100）
  net_profit_factor: 0.33, total_trades: 2, net_winning_trades: 1, net_losing_trades: 1,
  total_fees: 102.03,
  equity_curve: [{ at: "2026-06-12T10:00:00Z", equity: 10000 }],
  trades: [
    { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "short", price: 65000, amount: 10, pnl: null, fee: 1, trigger_reason: "market" },
    { at: "2026-06-12T10:05:00Z", action: "order_filled", side: "short", price: 65100, amount: 10, pnl: 100, fee: 1, trigger_reason: "market" },
    { at: "2026-06-12T10:10:00Z", action: "order_filled", side: "long", price: 65000, amount: 10, pnl: null, fee: 1, trigger_reason: "market" },
    { at: "2026-06-12T10:15:00Z", action: "order_filled", side: "long", price: 64900, amount: 10, pnl: -50, fee: 1, trigger_reason: "stop" },
  ],
  open_position: null,
};

const mountBar = (perf: unknown, detail: unknown = null) => {
  const w = mount(PerformanceBar, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })],
      stubs: { PriceChart: PriceChartStub },
    },
  });
  (useSessionsStore() as any).performance = perf;
  (useSessionsStore() as any).detail = detail;
  return w;
};

describe("PerformanceBar 抽屉", () => {
  it("默认折叠：见细条、不见 Tier1 六格", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    expect(w.find(".collapsed-bar").exists()).toBe(true);
    expect(w.find(".tier1-grid").exists()).toBe(false);
  });

  it("折叠条四项 + 数值（净/毛 PnL 带符号、手续费、胜率）", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    const t = w.text();
    expect(t).toContain("净PnL");
    expect(t).toContain("−95.14");           // 净PnL（U+2212）
    expect(t).toContain("毛PnL");
    expect(t).toContain("手续费");
    expect(t).toContain("胜率");
  });

  it("点击展开 → 渲 Tier1 六格 + Tier2 + 双口径警示", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    const t = w.text();
    expect(w.find(".tier1-grid").exists()).toBe(true);
    expect(t).toContain("净胜率");
    expect(t).toContain("盈亏比");
    expect(t).toContain("持仓周期");
    expect(t).toContain("不可逐点对账");
  });

  it("Tier1 净在前毛在后 + 手续费下标 毛−费=净", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    const t = w.text();
    expect(t.indexOf("net已实现")).toBeLessThan(t.indexOf("gross已实现"));
    expect(t).toContain("毛−费=净");
  });

  it("sign 驱动着色：净PnL 负 .neg、毛PnL 正 .pos", async () => {
    const w = mountBar(PERF_FLAT);          // net_pnl=-95.14（负）、total_return_pct=0.07（正）
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    expect(w.findAll(".tier1-grid .neg").length).toBeGreaterThan(0);
    expect(w.findAll(".tier1-grid .pos").length).toBeGreaterThan(0);
  });

  it("未平仓：渲当前持仓条（未实现毛 + 未平仓入场费）+ 折叠条多一格", async () => {
    const perfOpen = {
      ...PERF_FLAT, total_fees: 109.12,     // 已实现费 = 毛−净 = 7 − (-95.14)=102.14 → 入场费 ≈ 6.98
      open_position: { side: "short", contracts: 10.82, entry_price: 65542.1, unrealized_pnl: -13.97, pnl_pct_of_notional: -0.2 },
    };
    const w = mountBar(perfOpen);
    await w.vm.$nextTick();
    expect(w.find(".held-box").exists()).toBe(true);    // 折叠条持仓格
    await w.find(".collapsed-bar").trigger("click");
    const t = w.text();
    expect(w.find(".held-bar").exists()).toBe(true);    // 展开态持仓条
    expect(t).toContain("当前持仓(未平仓)");
    expect(t).toContain("未实现收益(毛)");
    expect(t).toContain("−13.97");
    expect(t).toContain("未平仓入场费");
  });

  it("平尾：无当前持仓条 + 无折叠条持仓格", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    expect(w.find(".held-box").exists()).toBe(false);
    await w.find(".collapsed-bar").trigger("click");
    expect(w.find(".held-bar").exists()).toBe(false);
  });

  it("未平仓但 unrealized=null（snapshot 异向）：持仓条显方向/数量、不显未实现行、无折叠条持仓格", async () => {
    const perfNoUnreal = {
      ...PERF_FLAT, total_fees: 109.12,
      open_position: { side: "short", contracts: 0.265, entry_price: 65000, unrealized_pnl: null, pnl_pct_of_notional: null },
    };
    const w = mountBar(perfNoUnreal);
    await w.vm.$nextTick();
    expect(w.find(".held-box").exists()).toBe(false);    // 折叠条不加未实现格
    await w.find(".collapsed-bar").trigger("click");
    const t = w.text();
    expect(w.find(".held-bar").exists()).toBe(true);     // 持仓条仍渲
    expect(t).toContain("当前持仓(未平仓)");
    expect(t).toContain("未平仓入场费");                  // 入场费照常
    expect(t).not.toContain("未实现收益(毛)");            // 不编造未实现行（spec §F fact-only）
    expect(t).not.toContain("盯市,未扣平仓费");
  });

  it("未平仓 + unrealized_as_of：未实现段显盯市时刻（截至 + UTC 全时戳）", async () => {
    const perfAsOf = {
      ...PERF_FLAT, total_fees: 109.12,
      open_position: { side: "short", contracts: 10.82, entry_price: 65542.1,
        unrealized_pnl: -13.97, pnl_pct_of_notional: -0.2, unrealized_as_of: "2026-06-12T10:00:30+00:00" },
    };
    const w = mountBar(perfAsOf);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    const t = w.text();
    expect(t).toContain("截至");
    expect(t).toContain("2026-06-12 10:00:30");   // fmtUtc 全时戳
  });

  it("未平仓 + unrealized_as_of=null：不显盯市时刻（不编造时间）", async () => {
    const perfNoAsOf = {
      ...PERF_FLAT, total_fees: 109.12,
      open_position: { side: "short", contracts: 10.82, entry_price: 65542.1,
        unrealized_pnl: -13.97, pnl_pct_of_notional: -0.2, unrealized_as_of: null },
    };
    const w = mountBar(perfNoAsOf);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    expect(w.text()).not.toContain("截至");
  });

  it("交易历程默认折叠（不见 A+ 表单位标注）、展开后可见", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    expect(w.text()).toContain("交易历程");
    expect(w.find(".trades-a").exists()).toBe(false);   // showTrades 默认 false
    await w.find(".trades-fold button").trigger("click");
    expect(w.find(".trades-a").exists()).toBe(true);
  });

  it("store.detail 有值 + 展开 → 渲价格走势 section + 传 props 给 PriceChart", async () => {
    const w = mountBar(PERF_FLAT, DETAIL);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    expect(w.find(".price-section").exists()).toBe(true);
    const pc = w.findComponent(PriceChartStub);
    expect(pc.exists()).toBe(true);
    expect(pc.props("symbol")).toBe("BTC/USDT:USDT");
    expect(pc.props("defaultTimeframe")).toBe("1H");
    expect(pc.props("sessionId")).toBe("s1");
    expect(pc.props("trades")).toHaveLength(4);
  });

  it("store.detail null → 不渲价格走势 section", async () => {
    const w = mountBar(PERF_FLAT, null);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    expect(w.find(".price-section").exists()).toBe(false);
  });
});
