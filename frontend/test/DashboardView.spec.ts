import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { createRouter, createWebHashHistory } from "vue-router";
import { useSessionsStore } from "@/stores/sessions";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    remove: vi.fn(),
  })),
}));

import DashboardView from "@/views/DashboardView.vue";

function makeRouter() {
  return createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: "/", name: "home", component: DashboardView },
      { path: "/sessions/:id", name: "session", component: DashboardView, props: true },
    ],
  });
}

describe("DashboardView", () => {
  it("home 路由（无 id）显示请选择会话提示", async () => {
    const router = makeRouter();
    router.push("/");
    await router.isReady();
    const wrapper = mount(DashboardView, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true }), router] },
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("请选择会话");
  });

  it("带 id 路由时调 selectSession", async () => {
    const router = makeRouter();
    router.push("/sessions/sim19");
    await router.isReady();
    const wrapper = mount(DashboardView, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true }), router] },
      props: { id: "sim19" },
    });
    const store = useSessionsStore();
    await wrapper.vm.$nextTick();
    expect(store.selectSession).toHaveBeenCalledWith("sim19");
  });

  it("会话头合并为单卡：.session-header.ob-card 内同含配置与实时状态", async () => {
    const router = makeRouter();
    router.push("/sessions/s1");
    await router.isReady();
    const wrapper = mount(DashboardView, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true }), router] },
      props: { id: "s1" },
    });
    const store = useSessionsStore();
    store.detail = { id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1h",
      scheduler_interval_min: 15, initial_balance: 10000, token_budget: 200000,
      created_at: "2026-06-12T10:00:00Z", last_active_at: null } as any;
    store.live = { status: "active", last_active_at: "2026-06-12T10:00:00Z", position: null,
      open_orders: [], active_alerts: [] } as any;
    await wrapper.vm.$nextTick();
    const card = wrapper.find(".session-header.ob-card");
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("BTC/USDT:USDT"); // 配置段
    expect(card.text()).toContain("空仓");          // 实时状态段
    expect(card.text()).not.toContain("提醒");
  });

  it("store.error 时渲染错误横幅（错误不再静默）", async () => {
    const router = makeRouter();
    router.push("/sessions/bad");
    await router.isReady();
    const wrapper = mount(DashboardView, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true }), router] },
      props: { id: "bad" },
    });
    const store = useSessionsStore();
    store.error = "GET /api/sessions/bad → 404";
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("加载出错");
    expect(wrapper.text()).toContain("404");
  });
});
