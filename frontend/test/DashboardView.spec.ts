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
