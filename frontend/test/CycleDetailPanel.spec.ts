import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CycleDetailPanel from "@/components/CycleDetailPanel.vue";

function detail(overrides = {}) {
  return {
    id: 5, cycle_label: "c5", triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z",
    reasoning: "thinking text", decision: "(1) Stance: hold",
    trigger_context: [{ type: "scheduled_tick" }],
    state_snapshot: { balance: { total_usdt: 10000 } },
    injected_events: null,
    tool_calls: [
      { tool_name: "get_position", status: "ok", duration_ms: 12, error_type: null, args: { symbol: "BTC" }, result: null },
    ],
    tokens_consumed: 9000, input_tokens: 8000, output_tokens: 1000, cache_hit_rate: 0.5,
    wall_time_ms: 5000, llm_call_ms: 4000, model_id: "claude",
    ...overrides,
  };
}

describe("CycleDetailPanel", () => {
  it("渲染推理与决策全文", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("thinking text");
    expect(w.text()).toContain("(1) Stance: hold");
  });

  it("injected_events 为 null 时不渲染该分区", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ injected_events: null }) as any } });
    expect(w.text()).not.toContain("中途注入事件");
  });

  it("injected_events 非空时渲染该分区", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ injected_events: [{ kind: "alert" }] }) as any } });
    expect(w.text()).toContain("中途注入事件");
  });

  it("展开工具表后显示 tool_name 与 duration", async () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    await w.find(".tools-toggle").trigger("click");
    expect(w.text()).toContain("get_position");
    expect(w.text()).toContain("12");
  });

  it("展开后工具 result 为 null 显示诚实空态文案", async () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    await w.find(".tools-toggle").trigger("click");
    expect(w.text()).toContain("结果未持久化");
  });
});
