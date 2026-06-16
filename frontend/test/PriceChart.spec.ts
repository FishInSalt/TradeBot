import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

const setData = vi.fn();
const setMarkers = vi.fn();
let crosshairCb: ((p: unknown) => void) | null = null;

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addCandlestickSeries: vi.fn(() => ({ setData, setMarkers })),
    subscribeCrosshairMove: vi.fn((cb) => { crosshairCb = cb; }),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    remove: vi.fn(),
  })),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, getOhlcv: vi.fn() } };
});

import { NRadioGroup } from "naive-ui";
import PriceChart from "@/components/PriceChart.vue";
import { api, ApiError, type TradeRow } from "@/api/client";

const getOhlcv = api.getOhlcv as Mock;

const TRADES: TradeRow[] = [
  { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "long", price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
  { at: "2026-06-12T10:15:00Z", action: "order_filled", side: "long", price: 66000, amount: 1, pnl: 1000, fee: 1, trigger_reason: "stop" },
];
const SERIES = {
  symbol: "BTC/USDT:USDT", timeframe: "1h",
  bars: [{ at: "2026-06-12T10:00:00Z", open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }],
};

const mountChart = (defaultTimeframe = "1h") =>
  mount(PriceChart, { props: { sessionId: "s1", symbol: "BTC/USDT:USDT", defaultTimeframe, trades: TRADES } });

beforeEach(() => {
  getOhlcv.mockReset();
  setData.mockReset();
  setMarkers.mockReset();
  crosshairCb = null;
});

describe("PriceChart", () => {
  it("挂载不抛 + init 用 defaultTimeframe 调 getOhlcv", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h");
    await flushPromises();
    expect(getOhlcv).toHaveBeenCalledWith("s1", "1h");
    expect(setData).toHaveBeenCalled();
    expect(setMarkers).toHaveBeenCalled();
    expect(w.find(".price-chart-wrap").exists()).toBe(true);
  });

  it("大写会话 tf（1H）→ 归一为 1h 高亮 + 请求 1h", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    mountChart("1H");
    await flushPromises();
    expect(getOhlcv).toHaveBeenCalledWith("s1", "1h");
  });

  it("切 timeframe → 重新调 getOhlcv", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h");
    await flushPromises();
    getOhlcv.mockClear();
    await w.findComponent(NRadioGroup).vm.$emit("update:value", "5m");
    await flushPromises();
    expect(getOhlcv).toHaveBeenCalledWith("s1", "5m");
  });

  it("getOhlcv 抛 ApiError → error 占位、不崩", async () => {
    getOhlcv.mockRejectedValue(new ApiError(503, "boom"));
    const w = mountChart("1h");
    await flushPromises();
    expect(w.text()).toContain("价格数据拉取失败");
  });

  it("空 bars → 空态占位", async () => {
    getOhlcv.mockResolvedValue({ ...SERIES, bars: [] });
    const w = mountChart("1h");
    await flushPromises();
    expect(w.text()).toContain("该窗口无行情数据");
  });

  it("crosshair 命中已加载 bar 时刻 → hover 浮层列该刻成交（类型/方向/价/量）", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h");
    await flushPromises();
    const t = Math.floor(Date.parse("2026-06-12T10:00:00Z") / 1000);
    crosshairCb?.({ time: t, point: { x: 10, y: 20 } });
    await w.vm.$nextTick();
    const tip = w.find(".pc-tip");
    expect(tip.exists()).toBe(true);
    expect(tip.text()).toContain("开仓");      // DerivedFill.type for first fill (no pnl, non-add = "开仓")
    expect(tip.text()).toContain("多");         // side "long" → "多"
  });
});
