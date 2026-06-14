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

  it("§⑤⑥ 状态快照默认展开 + 置顶 + 格式化", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    const txt = w.text();
    expect(txt).toContain("本轮开始时的状态");
    expect(txt).not.toContain("开始态");
    expect(txt).toContain("17.99");          // 默认展开即可见（持仓 contracts）
    expect(txt).not.toContain("_cycle_id");
    expect(txt.indexOf("本轮开始时的状态")).toBeLessThan(txt.indexOf("推理与行动过程"));
  });

  it("§① chip 输入/输出 token 文案", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("输入 8,000 / 输出 1,000 tok");
  });

  it("§议题5 chips token 千分位 + 耗时 s", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ tokens_consumed: 80733, wall_time_ms: 49770 }) as any } });
    expect(w.text()).toContain("80,733");
    expect(w.text()).toContain("49.8s");
  });
});
