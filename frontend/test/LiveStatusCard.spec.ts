import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";
import LiveStatusCard from "@/components/LiveStatusCard.vue";

function mountCard() {
  const wrapper = mount(LiveStatusCard, {
    global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
  });
  return { wrapper, store: useSessionsStore() };
}

describe("LiveStatusCard", () => {
  it("有持仓时显示方向与张数", async () => {
    const { wrapper, store } = mountCard();
    store.live = { status: "active", last_active_at: "2026-06-12T10:00:00Z", position: { symbol: "BTC/USDT:USDT", side: "long", contracts: 1.5, entry_price: 63000, leverage: 5 }, open_orders: [], active_alerts: [] } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("long");
    expect(wrapper.text()).toContain("1.5");
  });

  it("无持仓显示空仓", async () => {
    const { wrapper, store } = mountCard();
    store.live = { status: "active", last_active_at: null, position: null, open_orders: [], active_alerts: [] } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("空仓");
  });

  it("pollFailCount≥3 显示轮询中断角标", async () => {
    const { wrapper, store } = mountCard();
    store.live = { status: "active", last_active_at: null, position: null, open_orders: [], active_alerts: [] } as any;
    store.pollFailCount = 3;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("轮询中断");
  });
});
