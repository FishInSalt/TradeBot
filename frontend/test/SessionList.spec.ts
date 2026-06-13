import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";
import SessionList from "@/components/SessionList.vue";

const sessions = [
  { id: "sim19", name: "sim#19", symbol: "BTC/USDT:USDT", status: "active", total_return_pct: 2.5, created_at: "2026-06-12T10:00:00Z", last_active_at: "2026-06-12T10:30:00Z", cycle_count: 10 },
  { id: "sim18", name: "sim#18", symbol: "BTC/USDT:USDT", status: "paused", total_return_pct: -1.1, created_at: "2026-06-11T10:00:00Z", last_active_at: null, cycle_count: 5 },
];

function mountList() {
  const wrapper = mount(SessionList, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })],
      stubs: { "router-link": true },
    },
  });
  const store = useSessionsStore();
  store.sessions = sessions as any;
  return { wrapper, store };
}

describe("SessionList", () => {
  it("渲染每个会话名称与 symbol", async () => {
    const { wrapper } = mountList();
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("sim#19");
    expect(wrapper.text()).toContain("sim#18");
  });

  it("currentId 命中行标记 active 类", async () => {
    const { wrapper, store } = mountList();
    store.currentId = "sim19";
    await wrapper.vm.$nextTick();
    const active = wrapper.find(".session-row.active");
    expect(active.exists()).toBe(true);
    expect(active.text()).toContain("sim#19");
  });
});
