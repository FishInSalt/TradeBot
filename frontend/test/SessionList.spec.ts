import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";
import SessionList from "@/components/SessionList.vue";

const sessions = [
  { id: "sim19", name: "sim#19", symbol: "BTC/USDT:USDT", status: "active", total_return_pct: 2.5, net_return_pct: 1.8, created_at: "2026-06-12T10:00:00Z", last_active_at: "2026-06-12T10:30:00Z", cycle_count: 10 },
  { id: "sim18", name: "sim#18", symbol: "BTC/USDT:USDT", status: "paused", total_return_pct: -1.1, net_return_pct: -1.4, created_at: "2026-06-11T10:00:00Z", last_active_at: null, cycle_count: 5 },
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

  it("收益率显示净口径（net_return_pct）并带「净」标签，不显示毛", async () => {
    const { wrapper } = mountList();
    await wrapper.vm.$nextTick();
    const ret = wrapper.find(".session-row .ret");
    expect(ret.text()).toContain("净");
    expect(ret.text()).toContain("+1.80%");   // net_return_pct=1.8
    expect(ret.text()).not.toContain("2.50");  // 毛 total_return_pct 不再展示
  });

  it("净收益率为负时标 neg 类", async () => {
    const { wrapper } = mountList();
    await wrapper.vm.$nextTick();
    const rets = wrapper.findAll(".session-row .ret");
    expect(rets[1].classes()).toContain("neg");   // sim#18 net=-1.4
    expect(rets[1].text()).toContain("-1.40%");
  });
});
