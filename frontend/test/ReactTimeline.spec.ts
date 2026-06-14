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

  // === spec §10 注入锚点无法解析的兜底（name-based best-effort → 末尾归组） ===

  it("§10 兜底：after_tool_call_id 为 null 但 after_tool 名匹配骨架 → best-effort 锚到该工具", () => {
    const p = baseProps();
    p.injectedEvents = [
      { event: { type: "fill" }, after_tool: "get_position", offset_ms: 5, after_tool_call_id: null },
    ];
    const w = mount(ReactTimeline, { props: p as any });
    const txt = w.text();
    expect(txt).toContain("触发事件注入");
    // 按名锚到 get_position：出现在 get_position 之后、open_position 之前
    const iInj = txt.indexOf("触发事件注入");
    expect(iInj).toBeGreaterThan(txt.indexOf("get_position"));
    expect(iInj).toBeLessThan(txt.indexOf("open_position"));
  });

  it("§10 兜底：既不匹配 id 也不匹配名 → 时间线末尾归组 + 未能锚定标注", () => {
    const p = baseProps();
    p.injectedEvents = [
      { event: { type: "fill" }, after_tool: "ghost_tool", offset_ms: 9, after_tool_call_id: "call_ghost" },
    ];
    const w = mount(ReactTimeline, { props: p as any });
    const txt = w.text();
    expect(txt).toContain("未能锚定");
    expect(txt.indexOf("未能锚定")).toBeGreaterThan(txt.indexOf("open_position"));
    expect(w.findAll(".injection-card").length).toBe(1);
  });

  it("§议题2 超长 thinking 默认折叠 + 可展开全文", async () => {
    const long = "x".repeat(700);
    const p = { ...baseProps(), steps: [{ thinking: long, tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("展开全文");
    expect(w.text()).not.toContain(long);               // 折叠态不渲染全文
    await w.find(".thinking-toggle").trigger("click");
    expect(w.text()).toContain(long);                   // 展开后渲染全文
    expect(w.find(".thinking-toggle").text()).toContain("收起");   // 切换文案
  });

  it("§议题2 短 thinking 不折叠（无展开按钮）", () => {
    const p = { ...baseProps(), steps: [{ thinking: "短推理", tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("短推理");
    expect(w.text()).not.toContain("展开全文");
  });

  it("§议题5 工具卡 args 紧凑单行 + duration 友好", async () => {
    const p = {
      ...baseProps(),
      steps: [{ thinking: null, tools: [{ tool_call_id: "call_a", tool_name: "get_market_data" }] }],
      toolCalls: [{ tool_name: "get_market_data", status: "ok", duration_ms: 1500, error_type: null,
                    args: { timeframe: "1h", candle_count: 30 }, result: "ok", tool_call_id: "call_a" }],
    };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("1.5s");                          // duration 友好
    await w.findAll(".tool-card .tool-head")[0].trigger("click");
    expect(w.text()).toContain("timeframe=1h, candle_count=30"); // args 紧凑单行
  });

  it("orphan 工具卡 .tool-head 不带 clickable（无遥测则点击无意义）", () => {
    const p = baseProps();
    p.steps[1].tools[0].tool_call_id = "call_missing";   // open_position 变 orphan
    const w = mount(ReactTimeline, { props: p as any });
    const heads = w.findAll(".tool-head");
    expect(heads[0].classes()).toContain("clickable");                  // 正常卡可点
    expect(heads[heads.length - 1].classes()).not.toContain("clickable"); // orphan 卡不可点
  });
});
