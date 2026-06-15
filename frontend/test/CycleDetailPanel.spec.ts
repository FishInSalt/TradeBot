import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CycleDetailPanel from "@/components/CycleDetailPanel.vue";

function detail(overrides = {}) {
  return {
    id: 5, cycle_label: "c5", triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z",
    reasoning: "thinking text", decision: "(1) Stance: hold",
    trigger_context: [{ type: "scheduled_tick" }],
    state_snapshot: {
      position: { side: "short", contracts: 17.99, entry_price: 63896.0, unrealized_pnl: -12.5, leverage: 5 },
      balance: { total_usdt: 10000, free_usdt: 9000, used_usdt: 1000 },
      market: { ticker_last: 63900, fetched_at: "2026-06-12T10:00:00Z" },
      pending_orders: [{ id: "o1", order_type: "stop", side: "sell", trigger_price: 62000, amount: 1 }],
      active_alerts: [{ id: "a1", direction: "above", price: 64000, reasoning: "breakout" }],
      _errors: [], _cycle_id: "c5",
    },
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

  it("决策块默认展开、可折叠（caret 切换 + 点击收起内容）", async () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.find(".decision-toggle .toggle-caret").text()).toBe("▾");   // 默认展开
    expect(w.find("pre.decision").exists()).toBe(true);
    expect(w.text()).toContain("(1) Stance: hold");
    await w.find(".decision-toggle").trigger("click");
    expect(w.find(".decision-toggle .toggle-caret").text()).toBe("▸");   // 折叠态
    expect(w.find("pre.decision").exists()).toBe(false);                  // 内容收起
  });

  it("§A3 唤醒上下文默认折叠，点击展开原文", async () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("唤醒上下文");                 // 标题在
    expect(w.text()).not.toContain("Woke by scheduled tick"); // 默认折叠：原文不渲染
    await w.find(".context-toggle").trigger("click");
    expect(w.text()).toContain("Woke by scheduled tick");      // 展开后可见
  });

  it("三个同级折叠开关用裸 caret（▾/▸）指示展开态，无冗余「展开/折叠」文字", async () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ react_steps: null }) as any } });
    const caret = (sel: string) => w.find(`${sel} .toggle-caret`);
    expect(caret(".snapshot-toggle").text()).toBe("▾");   // 唤醒时状态默认展开
    expect(caret(".context-toggle").text()).toBe("▸");    // 唤醒上下文默认折叠
    expect(caret(".tools-toggle").text()).toBe("▸");      // 工具调用默认折叠
    await w.find(".tools-toggle").trigger("click");
    expect(caret(".tools-toggle").text()).toBe("▾");      // 展开后切换
    expect(w.text()).not.toContain("展开");                // 去掉冗余文字提示
    expect(w.text()).not.toContain("折叠");
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

  it("§D回退 react_steps=null + injected_events list → 渲 InjectionCard 人读摘要（非原始 JSON dump）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ react_steps: null, injected_events: [
      { event: { type: "fill", position_side: "short", amount: 13.46, fill_price: 63916, pnl: 7.59, fee: 8.6, timestamp: Date.UTC(2026, 5, 12, 16, 13, 9) },
        kind_label: "止损平仓", triggered_ago: "2 sec ago" },
    ] }) as any } });
    const txt = w.text();
    expect(txt).toContain("中途注入事件");        // 分组标题保留
    expect(txt).toContain("止损平仓");             // InjectionCard 标题（后端 kind_label）
    expect(txt).toContain("13.46 张");             // 人读摘要
    expect(txt).toContain("2 sec ago");            // age 片
    expect(txt).not.toContain("position_side");    // 折叠态不裸 dump JSON key（区别于旧 JsonBlock）
  });

  it("§D回退 injected_events 非 list（legacy dict）→ 保留 JsonBlock 兜底", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ react_steps: null, injected_events: { legacy: "blob" } }) as any } });
    expect(w.text()).toContain("中途注入事件");
    expect(w.text()).toContain("legacy");          // 非 list 形态走 JsonBlock dump
  });

  it("§议题5 flat 回退路径耗时走 fmtDuration（与主路径一致，不再裸 ms）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ react_steps: null,
      tool_calls: [{ tool_name: "get_position", status: "ok", duration_ms: 1500, error_type: null, args: null, result: "ok", tool_call_id: "c1" }] }) as any } });
    expect(w.text()).toContain("最慢 1.5s");
    expect(w.text()).not.toContain("1500ms");
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

  it("§议题6 标题改为「推理与行动过程」", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("推理与行动过程");
    expect(w.text()).not.toContain("ReAct 过程");
  });

  it("§重排/重命名 唤醒时状态默认展开 + 置顶（先于唤醒上下文与时间线）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    const txt = w.text();
    expect(txt).toContain("唤醒时状态");
    expect(txt).not.toContain("本轮开始时的状态");
    expect(txt).toContain("17.99");          // 默认展开即可见（持仓 contracts）
    expect(txt).not.toContain("_cycle_id");
    expect(txt.indexOf("唤醒时状态")).toBeLessThan(txt.indexOf("唤醒上下文"));
    expect(txt.indexOf("唤醒时状态")).toBeLessThan(txt.indexOf("推理与行动过程"));
  });

  it("§⑥ 快照格式化真实行为：方向/杠杆×/USDT/− 号/红绿着色（议题 6 核心交付）", () => {
    // fixture：side=short / unrealized_pnl=-12.5 / leverage=5
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    const txt = w.text();
    expect(txt).toContain("空");              // short → 空
    expect(txt).toContain("杠杆 5×");          // leverage 带 × 单位
    expect(txt).toContain("−12.5");           // 浮盈带 U+2212 负号（非 ASCII -）
    expect(txt).toContain("USDT");            // 浮盈带单位
    expect(w.find(".dir.short").exists()).toBe(true);  // 方向着色 class
    expect(w.find(".neg").exists()).toBe(true);        // 负浮盈红字 class
  });

  it("§① chip 输入/输出 token 文案", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("输入 8,000 / 输出 1,000 tok");
  });

  it("§C3 chips 去掉 tokens/wall 重复片（只留 header），保留拆解", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ tokens_consumed: 80733, wall_time_ms: 49770, input_tokens: 60000, output_tokens: 20000, llm_call_ms: 30000 }) as any } });
    const txt = w.text();
    expect(txt).not.toContain("tokens 80,733");   // 去掉总 tokens 片
    expect(txt).not.toContain("wall ");            // 去掉 wall 片
    expect(txt).toContain("输入");                 // 保留输入/输出拆解
    expect(txt).toContain("llm");                  // 保留 llm
  });

  it("§B1 余额三标签格（总额/可用/占用 + USDT 收尾）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    const txt = w.text();
    expect(txt).toContain("总额");
    expect(txt).toContain("可用");
    expect(txt).toContain("占用");
    expect(txt).toContain("10,000");          // total_usdt 千分位
    expect(txt).toContain("USDT");
    expect(txt).not.toContain("总 10,000");   // 旧平铺文案移除
  });

  it("§B2 现价时间 UTC 格式化（非裸 ISO）", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    const txt = w.text();
    expect(txt).toContain("2026-06-12 10:00:00");        // fetched_at 2026-06-12T10:00:00Z
    expect(txt).not.toContain("2026-06-12T10:00:00Z");   // 不再裸 ISO
  });

  it("§B3 快照渲染波动告警（价格/波动两子标签）", () => {
    const base = detail();
    const w = mount(CycleDetailPanel, { props: { detail: { ...base,
      state_snapshot: { ...base.state_snapshot, volatility_alert: { threshold_pct: 1.5, window_minutes: 15 } } } as any } });
    const txt = w.text();
    expect(txt).toContain("价格");            // 价格告警子标签（fixture active_alerts 非空）
    expect(txt).toContain("波动");            // 波动子标签
    expect(txt).toContain("±1.5% / 15min");   // 波动阈值/窗口
  });

  it("§B3 历史快照缺 volatility_alert 键 → 不渲波动子段", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });   // fixture 无 volatility_alert
    expect(w.text()).not.toContain("波动");
  });

  it("§③ 多条价格告警逐条成行：上下符号 + 英文单词配合 + 各条独立 .snap-item + 值单元格 .alerts", () => {
    const base = detail();
    const w = mount(CycleDetailPanel, { props: { detail: { ...base,
      state_snapshot: { ...base.state_snapshot, active_alerts: [
        { id: "a1", direction: "below", price: 64400, reasoning: "early warning" },
        { id: "a2", direction: "below", price: 64636, reasoning: "failed reclaim" },
        { id: "a3", direction: "above", price: 65600, reasoning: "breakout" },
      ] } } as any } });
    // 各告警条独立成行（值单元格 column 容器 + 价格组内逐条）
    expect(w.find(".alerts").exists()).toBe(true);
    expect(w.findAll(".alerts .alert-grp .snap-item").length).toBe(3);
    const txt = w.text();
    expect(txt).toContain("↓ below @64,400");   // 上下符号 + 英文单词配合
    expect(txt).toContain("↑ above @65,600");
  });
});
