import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "@/App.vue";
import DashboardView from "@/views/DashboardView.vue";

describe("app scaffold", () => {
  it("App 壳挂载并渲染 .app-shell 容器", async () => {
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: "/", component: DashboardView }],
    });
    router.push("/");
    await router.isReady();
    const wrapper = mount(App, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true }), router] },
    });
    expect(wrapper.find(".app-shell").exists()).toBe(true);
  });
});
