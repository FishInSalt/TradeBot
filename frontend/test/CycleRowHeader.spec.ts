import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CycleRowHeader from "@/components/CycleRowHeader.vue";

function cycle(overrides = {}) {
  return {
    id: 1, seq: 7, cycle_label: "c1", triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z",
    tokens_consumed: 80733, wall_time_ms: 49770, execution_status: "ok",
    position: null, key_events: [],
    ...overrides,
  };
}

describe("CycleRowHeader", () => {
  it("flat 开始态 + 无交易：head 空仓 + end （无交易）", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle() as any } });
    expect(w.text()).toContain("开始");
    expect(w.text()).toContain("空仓");
    expect(w.text()).toContain("（无交易）");
    expect(w.find(".keyrow").exists()).toBe(false);   // 噪声轮无色条
  });

  it("有开始态持仓：head 显示方向/张数/入场价", () => {
    const w = mount(CycleRowHeader, {
      props: { cycle: cycle({ position: { side: "short", contracts: 17.99, entry_price: 63896.0 } }) as any },
    });
    expect(w.text()).toContain("空");
    expect(w.text()).toContain("17.99");
    expect(w.text()).toContain("63896");
  });

  it("key_events 非空：每事件一枚 chip + 整行色条高亮", () => {
    const w = mount(CycleRowHeader, {
      props: { cycle: cycle({ key_events: [
        { kind: "fill_close", label: "止损平仓", direction: "long" },
        { kind: "flip", label: "反手→空", direction: "short" },
      ] }) as any },
    });
    expect(w.text()).toContain("止损平仓");
    expect(w.text()).toContain("反手→空");
    expect(w.find(".keyrow").exists()).toBe(true);    // 关键事件锚点色条
  });

  it("遥测用 format util（千分位 + tok 空格 + s）", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle() as any } });
    expect(w.text()).toContain("80,733 tok");   // tok 前有空格（fixture tokens_consumed:80733）
    expect(w.text()).toContain("49.8s");
  });

  it("§C1 行首显示会话内序号 #N", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle() as any } });
    expect(w.text()).toContain("#7");
  });

  it("§C2 时间为起→止区间（created_at 是结束，开始 = created_at − wall），UTC", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle() as any } });
    const txt = w.text();
    // created_at=10:00:00Z, wall=49770ms → 开始 09:59:10（UTC）
    expect(txt).toContain("2026-06-12 09:59:10");
    expect(txt).toContain("→");
    expect(txt).toContain("10:00:00");           // 结束时分秒
  });

  it("§C2 wall_time_ms=null（forensic）→ 只渲结束单点、无 →", () => {
    const w = mount(CycleRowHeader, { props: { cycle: cycle({ wall_time_ms: null }) as any } });
    const txt = w.text();
    expect(txt).toContain("2026-06-12 10:00:00");
    expect(txt).not.toContain("→");
  });
});
