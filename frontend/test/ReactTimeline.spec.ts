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
    expect(txt).not.toContain("+1200ms");   // D：去掉 offset_ms 显示
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

  it("§A1 多行 thinking 默认折叠（只显首行预览）+ 点击展开全文", async () => {
    const full = "第一行预览\n第二行隐藏内容\n第三行也隐藏";
    const p = { ...baseProps(), steps: [{ thinking: full, tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("第一行预览");           // 首行预览可见
    expect(w.text()).not.toContain("第二行隐藏内容");    // 折叠态：后续行不渲染（pre v-if）
    await w.find(".thinking-head").trigger("click");
    expect(w.text()).toContain("第二行隐藏内容");         // 展开后全文
  });

  it("§A1 短单行 thinking 不折叠（无折叠 affordance、常显全文）", () => {
    const p = { ...baseProps(), steps: [{ thinking: "短推理", tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("短推理");
    expect(w.find(".thinking-head").exists()).toBe(false);   // 无折叠头
  });

  it("§F3 单行无换行但 >100 字符 → 折叠（锁 THINKING_INLINE_MAX 阈值分支）", () => {
    const long = "x".repeat(101);   // 无换行、超单行容量
    const p = { ...baseProps(), steps: [{ thinking: long, tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.find(".thinking-head").exists()).toBe(true);    // length 分支也给折叠 affordance
  });

  it("§A2 思考块用 💭 图标 + 「思考」标签", () => {
    const p = { ...baseProps(), steps: [{ thinking: "短推理", tools: [] }] };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("💭");
    expect(w.text()).toContain("思考");
    expect(w.text()).not.toContain("🧠");
  });

  it("§④ 工具头函数式：短参 name(k=v) + 展开体只给结果（不重复入参）", async () => {
    const p = {
      ...baseProps(),
      steps: [{ thinking: null, tools: [{ tool_call_id: "call_a", tool_name: "get_market_data" }] }],
      toolCalls: [{ tool_name: "get_market_data", status: "ok", duration_ms: 1500, error_type: null,
                    args: { timeframe: "1h", candle_count: 30 }, result: "ok", tool_call_id: "call_a" }],
    };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("get_market_data(timeframe=1h, candle_count=30)"); // 头部函数式
    expect(w.text()).toContain("1.5s");
    await w.findAll(".tool-card .tool-head")[0].trigger("click");
    expect(w.text()).toContain("结果");
    expect(w.text()).not.toContain("入参");      // 短参：展开体不重复入参
  });

  it("§④ 长参头部截断 …，展开体补完整入参", async () => {
    const long = "y".repeat(80);
    const p = {
      ...baseProps(),
      steps: [{ thinking: null, tools: [{ tool_call_id: "call_b", tool_name: "save_memory" }] }],
      toolCalls: [{ tool_name: "save_memory", status: "ok", duration_ms: 8, error_type: null,
                    args: { category: "trade", content: long }, result: "saved", tool_call_id: "call_b" }],
    };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("…");              // 头部截断
    await w.findAll(".tool-card .tool-head")[0].trigger("click");
    expect(w.text()).toContain("入参");           // 长参：展开体补完整入参
    expect(w.text()).toContain(long);             // 完整内容
  });

  it("§④ 无参工具头 name()", () => {
    const p = {
      ...baseProps(),
      steps: [{ thinking: null, tools: [{ tool_call_id: "call_c", tool_name: "get_position" }] }],
      toolCalls: [{ tool_name: "get_position", status: "ok", duration_ms: 12, error_type: null,
                    args: {}, result: "flat", tool_call_id: "call_c" }],
    };
    const w = mount(ReactTimeline, { props: p as any });
    expect(w.text()).toContain("get_position()");
  });

  it("orphan 工具卡 .tool-head 不带 clickable（无遥测则点击无意义）", () => {
    const p = baseProps();
    p.steps[1].tools[0].tool_call_id = "call_missing";   // open_position 变 orphan
    const w = mount(ReactTimeline, { props: p as any });
    const heads = w.findAll(".tool-head");
    expect(heads[0].classes()).toContain("clickable");                  // 正常卡可点
    expect(heads[heads.length - 1].classes()).not.toContain("clickable"); // orphan 卡不可点
  });

  it("工具卡带展开/折叠 caret：折叠 ▸、展开 ▾；orphan 卡无 caret", async () => {
    const p = baseProps();
    p.steps[1].tools[0].tool_call_id = "call_missing";   // 最后一张 open_position 变 orphan
    const w = mount(ReactTimeline, { props: p as any });
    const cards = w.findAll(".tool-card");
    const caret0 = cards[0].find(".tool-caret");
    expect(caret0.exists()).toBe(true);
    expect(caret0.text()).toBe("▸");                          // 折叠态
    await cards[0].find(".tool-head").trigger("click");
    expect(cards[0].find(".tool-caret").text()).toBe("▾");    // 展开态
    expect(cards[cards.length - 1].find(".tool-caret").exists()).toBe(false);  // orphan 无 caret
  });
});
