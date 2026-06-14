import { describe, it, expect } from "vitest";
import { fmtTokens, fmtDuration, fmtArgs } from "@/utils/format";

describe("fmtTokens", () => {
  it("千分位", () => {
    expect(fmtTokens(80733)).toBe("80,733");
    expect(fmtTokens(0)).toBe("0");
  });
  it("null/undefined → 占位", () => {
    expect(fmtTokens(null)).toBe("—");
    expect(fmtTokens(undefined)).toBe("—");
  });
});

describe("fmtDuration", () => {
  it("≥1000ms → s（1 位小数）", () => {
    expect(fmtDuration(49770)).toBe("49.8s");
    expect(fmtDuration(1000)).toBe("1.0s");
  });
  it("<1000ms → ms", () => {
    expect(fmtDuration(320)).toBe("320ms");
  });
  it("0 → <1ms；null → 占位", () => {
    expect(fmtDuration(0)).toBe("<1ms");
    expect(fmtDuration(null)).toBe("—");
  });
});

describe("fmtArgs", () => {
  it("dict → 紧凑 key=value 单行", () => {
    expect(fmtArgs({ timeframe: "1h", candle_count: 30 })).toBe("timeframe=1h, candle_count=30");
  });
  it("空/无参 → （无参）", () => {
    expect(fmtArgs({})).toBe("（无参）");
    expect(fmtArgs(null)).toBe("（无参）");
    expect(fmtArgs(undefined)).toBe("（无参）");
  });
  it("嵌套值 dict/list → 回退 JSON 串", () => {
    expect(fmtArgs({ levels: [1, 2] })).toBe("levels=[1,2]");
    expect(fmtArgs({ cfg: { a: 1 } })).toBe('cfg={"a":1}');
  });
  it("顶层非 dict（截断回退 str / list）→ JSON 串", () => {
    expect(fmtArgs("broken")).toBe('"broken"');
    expect(fmtArgs([1, 2])).toBe("[1,2]");
  });
});
