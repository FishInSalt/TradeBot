import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";
import SessionMeta from "@/components/SessionMeta.vue";

describe("SessionMeta", () => {
  it("消费 store.detail 展示周期与调度间隔", async () => {
    const wrapper = mount(SessionMeta, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.detail = { id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1h", scheduler_interval_min: 15, initial_balance: 10000, token_budget: 200000, created_at: "2026-06-12T10:00:00Z", last_active_at: null } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("1h");
    expect(wrapper.text()).toContain("15");
    expect(wrapper.text()).toContain("200000");
  });

  it("detail 为空时不渲染", () => {
    const wrapper = mount(SessionMeta, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    expect(wrapper.find(".session-meta").exists()).toBe(false);
  });

  it("§议题1 有 system_prompt：折叠区可展开看全文", async () => {
    const wrapper = mount(SessionMeta, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.detail = { id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1h",
      scheduler_interval_min: 15, initial_balance: 10000, token_budget: 200000,
      created_at: "2026-06-12T10:00:00Z", last_active_at: null,
      system_prompt: "You are a disciplined futures trader." } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("System Prompt");
    expect(wrapper.text()).not.toContain("disciplined");      // 默认折叠
    await wrapper.find(".sysprompt-toggle").trigger("click");
    expect(wrapper.text()).toContain("disciplined");          // 展开后全文
  });

  it("§议题1 无 system_prompt：不渲染折叠区", async () => {
    const wrapper = mount(SessionMeta, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.detail = { id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1h",
      scheduler_interval_min: 15, initial_balance: 10000, token_budget: 200000,
      created_at: "2026-06-12T10:00:00Z", last_active_at: null, system_prompt: null } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).not.toContain("System Prompt");
  });

  it("§4 dashboard 卡片化：根容器带 ob-card", async () => {
    const wrapper = mount(SessionMeta, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.detail = { id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1h",
      scheduler_interval_min: 15, initial_balance: 10000, token_budget: 200000,
      created_at: "2026-06-12T10:00:00Z", last_active_at: null } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".ob-card").exists()).toBe(true);
  });
});
