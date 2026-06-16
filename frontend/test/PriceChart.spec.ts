import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

const setData = vi.fn();
const setMarkers = vi.fn();
const fitContent = vi.fn();
const setVisibleLogicalRange = vi.fn();
const getVisibleLogicalRange = vi.fn(() => null as unknown);
let crosshairCb: ((p: unknown) => void) | null = null;

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addCandlestickSeries: vi.fn(() => ({ setData, setMarkers })),
    subscribeCrosshairMove: vi.fn((cb) => { crosshairCb = cb; }),
    // 同一组 hoisted spy（timeScale 每调返回新对象但复用 spy），便于断言视口是否被重置
    timeScale: vi.fn(() => ({ fitContent, setVisibleLogicalRange, getVisibleLogicalRange })),
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

const mountChart = (defaultTimeframe = "1h", latestCycleId: number | null = 1) =>
  mount(PriceChart, {
    props: { sessionId: "s1", symbol: "BTC/USDT:USDT", defaultTimeframe, trades: TRADES, latestCycleId },
  });

beforeEach(() => {
  getOhlcv.mockReset();
  setData.mockReset();
  setMarkers.mockReset();
  fitContent.mockReset();
  setVisibleLogicalRange.mockReset();
  getVisibleLogicalRange.mockReset();
  getVisibleLogicalRange.mockReturnValue(null);   // 默认无视口信息（jsdom）；个别用例覆盖以测粘性右锚
  crosshairCb = null;
});

// 让 applyViewport 真正走 setVisibleLogicalRange（否则 jsdom clientWidth=0 恒走 fitContent 兜底）
const stubWidth = (w: ReturnType<typeof mountChart>, px = 800) =>
  Object.defineProperty(w.find(".pc-canvas").element, "clientWidth", { value: px, configurable: true });

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

  it("快速连切 tf：陈旧慢请求 resolve 后不覆盖新请求结果", async () => {
    // 第一次（1h）用一个手动控制的 promise，先不 resolve；第二次（5m）立即 resolve
    let resolveSlow: (v: unknown) => void = () => {};
    const slow = new Promise((r) => { resolveSlow = r; });
    const SERIES_1H = { symbol: "BTC/USDT:USDT", timeframe: "1h",
      bars: [{ at: "2026-06-12T10:00:00Z", open: 111, high: 2, low: 0.5, close: 1.5, volume: 10 }] };
    const SERIES_5M = { symbol: "BTC/USDT:USDT", timeframe: "5m",
      bars: [{ at: "2026-06-12T10:00:00Z", open: 555, high: 2, low: 0.5, close: 1.5, volume: 10 }] };

    getOhlcv.mockReturnValueOnce(slow as any);          // init load(1h) 挂起
    const w = mountChart("1h");
    // init 的 load 还没 resolve；切到 5m
    getOhlcv.mockResolvedValueOnce(SERIES_5M as any);
    await w.findComponent(NRadioGroup).vm.$emit("update:value", "5m");
    await flushPromises();
    setData.mockClear();
    // 现在迟到的 1h 结果 resolve —— 不应覆盖
    resolveSlow(SERIES_1H);
    await flushPromises();
    // 5m 已渲染过；迟到的 1h render 即便跑，bars 仍是 5m（open 555 而非 111）
    const lastCandles = setData.mock.calls.length
      ? setData.mock.calls[setData.mock.calls.length - 1][0]
      : null;
    if (lastCandles) {
      expect(lastCandles[0].open).toBe(555);
    }
    // 关键断言：5m 是最后一次成功 getOhlcv，bars 来自它
    expect(getOhlcv).toHaveBeenLastCalledWith("s1", "5m");
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

  it("首载 → 视口适配（jsdom 宽 0 走 fitContent 兜底）", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    mountChart("1h");
    await flushPromises();
    expect(fitContent).toHaveBeenCalled();
  });

  it("新 cycle（latestCycleId 变大）→ 重新 getOhlcv 同步 bar/markers，但不重置视口", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h", 1);
    await flushPromises();
    getOhlcv.mockClear(); fitContent.mockClear(); setVisibleLogicalRange.mockClear(); setMarkers.mockClear();
    await w.setProps({ latestCycleId: 2 });
    await flushPromises();
    expect(getOhlcv).toHaveBeenCalledTimes(1);          // 追平：重拉 OHLCV
    expect(setMarkers).toHaveBeenCalled();               // 重绘 markers（仓位变化）
    expect(fitContent).not.toHaveBeenCalled();           // 视口不重置
    expect(setVisibleLogicalRange).not.toHaveBeenCalled();
  });

  it("新成交落地（trades 数变）→ 同步、不重置视口", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h", 1);
    await flushPromises();
    getOhlcv.mockClear(); fitContent.mockClear(); setMarkers.mockClear();
    await w.setProps({ trades: [...TRADES, { ...TRADES[0], at: "2026-06-12T11:00:00Z" }] });
    await flushPromises();
    expect(getOhlcv).toHaveBeenCalledTimes(1);
    expect(setMarkers).toHaveBeenCalled();
    expect(fitContent).not.toHaveBeenCalled();
  });

  it("5s 轮询同内容（trades 换新引用、cycle/笔数不变）→ 不重拉、不重绘、不复位（修复每 5s 重置）", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h", 1);
    await flushPromises();
    getOhlcv.mockClear(); fitContent.mockClear(); setMarkers.mockClear();
    await w.setProps({ trades: [...TRADES] });          // 新数组引用、同长度、latestCycleId 不变
    await flushPromises();
    expect(getOhlcv).not.toHaveBeenCalled();
    expect(setMarkers).not.toHaveBeenCalled();
    expect(fitContent).not.toHaveBeenCalled();
  });

  it("竞态：fit-load 在飞时 sync 抢入胜出 → loading 不卡死（修 stuck-loading）", async () => {
    // 首载(fit) 用可控 deferred 挂起；sync(latestCycleId 变) 立即 resolve 抢占胜出
    let resolveFit: (v: unknown) => void = () => {};
    const slow = new Promise((r) => { resolveFit = r; });
    getOhlcv.mockReturnValueOnce(slow as never);     // 首载(fit) 挂起，loading=true
    const w = mountChart("1h", 1);
    getOhlcv.mockResolvedValueOnce(SERIES);          // sync 的 load(false) 立即就绪
    await w.setProps({ latestCycleId: 2 });          // syncSig 变 → load(false) seq=2 抢占
    await flushPromises();                            // load(false) 胜出渲染
    resolveFit(SERIES);                               // 迟到的首载 resolve（陈旧，应被丢弃）
    await flushPromises();
    expect(w.text()).not.toContain("加载价格数据");   // 遮罩不卡死
    expect(setData).toHaveBeenCalled();               // 图已渲染
  });

  it("竞态：切 tf 的 fit-load 被 sync 抢占 → 新 tf 仍重置视口（fit 意图被认领）", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h", 1);
    stubWidth(w);
    await flushPromises();
    fitContent.mockClear(); setVisibleLogicalRange.mockClear();
    let resolveTf: (v: unknown) => void = () => {};
    const slow = new Promise((r) => { resolveTf = r; });
    getOhlcv.mockReturnValueOnce(slow as never);     // 切 tf 的 load(true) 挂起
    await w.findComponent(NRadioGroup).vm.$emit("update:value", "5m");
    getOhlcv.mockResolvedValueOnce(SERIES);
    await w.setProps({ latestCycleId: 2 });          // sync 抢占
    await flushPromises();                            // load(false) 胜出
    resolveTf(SERIES);
    await flushPromises();
    expect(setVisibleLogicalRange).toHaveBeenCalled();  // 新 tf 视口被重置（不是沿用旧视口）
  });

  it("视口接线：clientWidth>0 → applyViewport 真正调 setVisibleLogicalRange 右锚末根（非 fitContent）", async () => {
    const bars3 = [
      { at: "2026-06-12T10:00:00Z", open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
      { at: "2026-06-12T11:00:00Z", open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
      { at: "2026-06-12T12:00:00Z", open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
    ];
    getOhlcv.mockResolvedValue({ symbol: "BTC/USDT:USDT", timeframe: "1h", bars: bars3 });
    const w = mountChart("1h");
    stubWidth(w, 800);
    await flushPromises();
    expect(setVisibleLogicalRange).toHaveBeenCalled();
    expect(fitContent).not.toHaveBeenCalled();
    const calls = setVisibleLogicalRange.mock.calls;
    expect(calls[calls.length - 1][0].to).toBeCloseTo(2.5);  // barCount-0.5，末根贴右
  });

  it("sync 且用户原本贴右边沿 → 粘性重锚（新最新入视野）", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h", 1);
    stubWidth(w);
    await flushPromises();                            // 首载 renderedCount=1
    getVisibleLogicalRange.mockReturnValue({ from: -10, to: 0.5 });  // to >= renderedCount-1.5 → 贴右
    setVisibleLogicalRange.mockClear();
    await w.setProps({ latestCycleId: 2 });          // sync
    await flushPromises();
    expect(setVisibleLogicalRange).toHaveBeenCalled();  // 粘性右锚追新
  });

  it("sync 但用户已左滚看历史 → 保留视口（不重锚）", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h", 1);
    stubWidth(w);
    await flushPromises();
    getVisibleLogicalRange.mockReturnValue({ from: -50, to: -30 });  // 远离末根 → 不贴右
    setVisibleLogicalRange.mockClear();
    await w.setProps({ latestCycleId: 2 });
    await flushPromises();
    expect(setVisibleLogicalRange).not.toHaveBeenCalled();  // 保留用户视口
  });

  it("getOhlcv 抛非 ApiError（如 TypeError）→ 也进错误占位、不致 unhandled rejection", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    getOhlcv.mockRejectedValue(new TypeError("boom"));
    const w = mountChart("1h");
    await flushPromises();
    expect(w.text()).toContain("价格数据拉取失败");   // 错误占位
    expect(spy).toHaveBeenCalled();                    // 非预期错记 console（不静默吞）
    spy.mockRestore();
  });
});
