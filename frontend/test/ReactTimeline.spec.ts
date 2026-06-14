import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import ReactTimeline from "@/components/ReactTimeline.vue";

const baseProps = () => ({
  steps: [
    { thinking: "评估趋势", tools: [
      { tool_call_id: "call_1", tool_name: "get_market_data" },
      { tool_call_id: "call_2", tool_name: "get_position" },
    ] },
    { thinking: "决定开多", tools: [
      { tool_call_id: "call_3", tool_name: "open_position" },
    ] },
  ],
  toolCalls: [
    { tool_name: "get_market_data", status: "ok", duration_ms: 30, error_type: null, args: { sym: "BTC" }, result: "px 63000", tool_call_id: "call_1" },
    { tool_name: "get_position", status: "ok", duration_ms: 12, error_type: null, args: {}, result: "flat", tool_call_id: "call_2" },
    { tool_name: "open_position", status: "ok", duration_ms: 80, error_type: null, args: { side: "long" }, result: "ok", tool_call_id: "call_3" },
  ],
  injectedEvents: null as any,
});

describe("ReactTimeline", () => {
  it("按 steps 顺序渲染 thinking 与工具名", () => {
    const w = mount(ReactTimeline, { props: baseProps() as any });
    const txt = w.text();
    expect(txt).toContain("评估趋势");
    expect(txt).toContain("决定开多");
    // 工具名按骨架顺序出现
    const i0 = txt.indexOf("get_market_data");
    const i1 = txt.indexOf("get_position");
    const i2 = txt.indexOf("open_position");
    expect(i0).toBeGreaterThanOrEqual(0);
    expect(i0).toBeLessThan(i1);
    expect(i1).toBeLessThan(i2);
  });

  it("工具卡按 tool_call_id 解析出 args/result（展开后）", async () => {
    const w = mount(ReactTimeline, { props: baseProps() as any });
    // 点开第一张工具卡
    await w.findAll(".tool-card .tool-head")[0].trigger("click");
    expect(w.text()).toContain("px 63000");
  });

  it("orphan tool_call_id（无对应 toolCall 行）→ 渲 tool_name + 因因中性标注", () => {
    const p = baseProps();
    p.steps[1].tools[0].tool_call_id = "call_missing";   // 骨架引用但 toolCalls 无此行
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("open_position");
    expect(w.text()).toContain("无遥测记录");
  });

  it("注入卡按 after_tool_call_id 锚在对应工具后", () => {
    const p = baseProps();
    p.injectedEvents = [
      { event: { type: "fill", side: "long" }, after_tool: "get_position", offset_ms: 1200, after_tool_call_id: "call_2" },
    ];
    const w = mount(ReactTimeline, { props: p as any });
    const txt = w.text();
    expect(txt).toContain("触发事件注入");
    // 注入卡出现在 get_position 之后、open_position 之前
    const iInj = txt.indexOf("触发事件注入");
    const iNext = txt.indexOf("open_position");
    expect(iInj).toBeGreaterThan(txt.indexOf("get_position"));
    expect(iInj).toBeLessThan(iNext);
  });

  it("批量注入（共享 after_tool_call_id）并排多张", () => {
    const p = baseProps();
    p.injectedEvents = [
      { event: { type: "fill" }, after_tool: "get_position", offset_ms: 1, after_tool_call_id: "call_2" },
      { event: { type: "price_level_alert" }, after_tool: "get_position", offset_ms: 2, after_tool_call_id: "call_2" },
    ];
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.findAll(".injection-card").length).toBe(2);
  });
});
