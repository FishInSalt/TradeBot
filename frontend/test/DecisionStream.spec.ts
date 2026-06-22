import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";
import DecisionStream from "@/components/DecisionStream.vue";

function cyc(id: number) {
  return { id, cycle_label: `c${id}`, triggered_by: `t${id}`, created_at: "2026-06-12T10:00:00Z", tokens_consumed: 1, wall_time_ms: 1, execution_status: "ok", position: null, key_events: [] };
}
function det(id: number) {
  return { id, cycle_label: `c${id}`, triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z", reasoning: "r", decision: "d", trigger_context: null, state_snapshot: null, injected_events: null, tool_calls: [], tokens_consumed: 1, input_tokens: null, output_tokens: null, cache_hit_rate: null, wall_time_ms: null, llm_call_ms: null, model_id: null };
}

function mountStream() {
  const wrapper = mount(DecisionStream, {
    global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
  });
  const store = useSessionsStore();
  store.cycles = [cyc(3), cyc(2), cyc(1)] as any;
  return { wrapper, store };
}

describe("DecisionStream", () => {
  it("按 store.cycles 顺序渲染每条 cycle 表头", async () => {
    const { wrapper } = mountStream();
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".cycle-head").length).toBe(3);   // 三条都渲染
    expect(wrapper.text().indexOf("t3")).toBeLessThan(wrapper.text().indexOf("t1"));  // store.cycles 顺序
  });

  it("点击折叠项表头走受控路径调 store.setExpandedCycles（含新展开 id）", async () => {
    const { wrapper, store } = mountStream();
    await wrapper.vm.$nextTick();
    await wrapper.find(".n-collapse-item__header-main").trigger("click");
    expect(store.setExpandedCycles).toHaveBeenCalledWith([3]);
  });

  it("expandedCycleIds 命中且详情已缓存时渲染对应详情面板", async () => {
    const { wrapper, store } = mountStream();
    store.expandedCycleIds = [2];
    store.cycleDetails = new Map([[2, det(2)]]) as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".cycle-detail").length).toBe(1);
  });

  it("多个 cycle 同时展开各自渲染详情面板（去 accordion）", async () => {
    const { wrapper, store } = mountStream();
    store.expandedCycleIds = [3, 2];
    store.cycleDetails = new Map([[3, det(3)], [2, det(2)]]) as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".cycle-detail").length).toBe(2);
  });

  it("无 cycle 时显示空态", async () => {
    const { wrapper, store } = mountStream();
    store.cycles = [] as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("暂无决策");
  });

  it("§4 feed 包白卡 ob-card", async () => {
    const { wrapper } = mountStream();
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".ob-card").exists()).toBe(true);
  });

  it("有更多历史时（cycles 非空 + 未到顶）显示「加载更早」按钮", async () => {
    const { wrapper } = mountStream(); // reachedOldest 默认 false
    await wrapper.vm.$nextTick();
    const btn = wrapper.find(".load-older");
    expect(btn.exists()).toBe(true);
    expect(btn.text()).toContain("加载更早");
  });

  it("loadingOlder 时按钮 disabled + 文案「加载中」", async () => {
    const { wrapper, store } = mountStream();
    store.loadingOlder = true;
    await wrapper.vm.$nextTick();
    const btn = wrapper.find(".load-older");
    expect(btn.attributes("disabled")).toBeDefined();
    expect(btn.text()).toContain("加载中");
  });

  it("reachedOldest 时不显按钮、显「已到最早」", async () => {
    const { wrapper, store } = mountStream();
    store.reachedOldest = true;
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".load-older").exists()).toBe(false);
    expect(wrapper.text()).toContain("已到最早");
  });

  it("空列表不显「加载更早」按钮", async () => {
    const { wrapper, store } = mountStream();
    store.cycles = [] as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".load-older").exists()).toBe(false);
  });

  it("点击「加载更早」走受控路径调 store.loadOlder", async () => {
    const { wrapper, store } = mountStream();
    await wrapper.vm.$nextTick();
    await wrapper.find(".load-older").trigger("click");
    expect(store.loadOlder).toHaveBeenCalled();
  });
});
