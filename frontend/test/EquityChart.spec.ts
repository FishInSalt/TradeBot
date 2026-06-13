import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  })),
}));

import EquityChart, { toSeriesData } from "@/components/EquityChart.vue";

describe("toSeriesData", () => {
  it("ISO→秒级时间戳并升序", () => {
    const d = toSeriesData([
      { at: "2026-06-12T10:01:00Z", equity: 101 },
      { at: "2026-06-12T10:00:00Z", equity: 100 },
    ]);
    expect(d.map((x) => x.value)).toEqual([100, 101]);
    expect(d[0].time < d[1].time).toBe(true);
  });

  it("同秒去重保留最后一个", () => {
    const d = toSeriesData([
      { at: "2026-06-12T10:00:00Z", equity: 100 },
      { at: "2026-06-12T10:00:00Z", equity: 105 },
    ]);
    expect(d.length).toBe(1);
    expect(d[0].value).toBe(105);
  });
});

describe("EquityChart", () => {
  it("挂载不抛（图表库已 mock）", () => {
    const w = mount(EquityChart, { props: { points: [{ at: "2026-06-12T10:00:00Z", equity: 100 }] } });
    expect(w.find(".equity-chart").exists()).toBe(true);
  });
});
