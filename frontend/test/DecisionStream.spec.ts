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

  it("点击折叠项表头调 store.expandCycle(id)", async () => {
    const { wrapper, store } = mountStream();
    await wrapper.vm.$nextTick();
    await wrapper.find(".n-collapse-item__header-main").trigger("click");
    expect(store.expandCycle).toHaveBeenCalledWith(3);
  });

  it("expandedCycleId 命中且详情已缓存时仅渲染一个详情面板", async () => {
    const { wrapper, store } = mountStream();
    store.expandedCycleId = 2;
    store.cycleDetails = new Map([[2, det(2)]]) as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".cycle-detail").length).toBe(1);
  });

  it("无 cycle 时显示空态", async () => {
    const { wrapper, store } = mountStream();
    store.cycles = [] as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("暂无决策");
  });
});
