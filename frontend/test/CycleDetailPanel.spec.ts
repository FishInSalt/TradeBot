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
    react_steps: [
      { thinking: "评估趋势", tools: [{ tool_call_id: "call_1", tool_name: "get_position" }] },
    ],
    user_prompt_snapshot: "Woke by scheduled tick at 10:00",
    execution_status: "ok",
    tool_calls: [
      { tool_name: "get_position", status: "ok", duration_ms: 12, error_type: null, args: { symbol: "BTC" }, result: "flat", tool_call_id: "call_1" },
    ],
    tokens_consumed: 9000, input_tokens: 8000, output_tokens: 1000, cache_hit_rate: 92.76,
    wall_time_ms: 5000, llm_call_ms: 4000, model_id: "claude",
    ...overrides,
  };
}

describe("CycleDetailPanel", () => {
  it("渲染 ReAct 时间线（thinking + 工具名）与决策", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("评估趋势");
    expect(w.text()).toContain("get_position");
    expect(w.text()).toContain("(1) Stance: hold");
  });

  it("渲染唤醒上下文原文（user_prompt_snapshot）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("Woke by scheduled tick at 10:00");
  });

  it("user_prompt_snapshot 为 null（legacy）时不渲染 Context 块", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ user_prompt_snapshot: null }) as any } });
    expect(w.text()).not.toContain("唤醒上下文");
  });

  it("react_steps 为 null（legacy/forensic）→ 回退扁平视图 + 说明", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ react_steps: null }) as any } });
    expect(w.text()).toContain("无交错时间线");
    expect(w.text()).toContain("thinking text");   // 回退渲 reasoning 整块
  });

  it("回退分支：react_steps=null 但 injected_events 非空时仍渲染注入事件（防丢失）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ react_steps: null, injected_events: [{ event: { type: "fill" } }] }) as any } });
    expect(w.text()).toContain("中途注入事件");
  });

  it("chips 含 llm 与 execution_status", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("llm");
    expect(w.text()).toContain("ok");
  });

  it("cache 命中率按 0-100 口径直接显示，不再 ×100", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ cache_hit_rate: 92.76 }) as any } });
    expect(w.text()).toContain("cache 93%");
    expect(w.text()).not.toContain("9276");
  });
});
