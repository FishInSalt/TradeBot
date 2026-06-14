import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    remove: vi.fn(),
  })),
}));

import PerformanceBar from "@/components/PerformanceBar.vue";

describe("PerformanceBar", () => {
  it("显示指标且带双口径标注", async () => {
    const wrapper = mount(PerformanceBar, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.performance = { initial_balance: 10000, current_position: "flat", total_return_pct: 2.5, net_pnl: 250, net_win_rate: 0.6, max_drawdown_pct: -3.2, net_profit_factor: 1.5, total_trades: 10, net_winning_trades: 6, net_losing_trades: 4, total_fees: 12, equity_curve: [{ at: "2026-06-12T10:00:00Z", equity: 10000 }], trades: [] } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("2.50%");
    expect(wrapper.text()).toContain("盯市");
    expect(wrapper.text()).toContain("不可逐点对账");
  });

  it("§4 dashboard 卡片化：根容器带 ob-card（.neg 红字落白卡达 AA 的结构前提）", async () => {
    const wrapper = mount(PerformanceBar, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.performance = { initial_balance: 10000, current_position: "flat", total_return_pct: -3.2, net_pnl: -250, net_win_rate: 0.4, max_drawdown_pct: -5, net_profit_factor: 0.8, total_trades: 5, net_winning_trades: 2, net_losing_trades: 3, total_fees: 8, equity_curve: [{ at: "2026-06-12T10:00:00Z", equity: 10000 }], trades: [] } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".ob-card").exists()).toBe(true);
  });
});
