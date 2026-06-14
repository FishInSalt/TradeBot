import { describe, it, expect } from "vitest";
import { fmtTokens, fmtDuration, fmtArgs, clipArgs, fmtNum, fmtSigned, HEAD_ARGS_MAX } from "@/utils/format";

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

describe("clipArgs", () => {
  it("空/无参 → text='' clipped=false（头渲 name()）", () => {
    expect(clipArgs(null)).toEqual({ text: "", clipped: false });
    expect(clipArgs({})).toEqual({ text: "", clipped: false });
  });
  it("短参 → 原串 clipped=false", () => {
    expect(clipArgs({ timeframe: "1h", candle_count: 30 })).toEqual({
      text: "timeframe=1h, candle_count=30", clipped: false,
    });
  });
  it("长参 → 截断到 HEAD_ARGS_MAX + … clipped=true", () => {
    const r = clipArgs({ content: "x".repeat(100) });
    expect(r.clipped).toBe(true);
    expect(r.text.endsWith("…")).toBe(true);
    expect(r.text.length).toBe(HEAD_ARGS_MAX + 1); // 60 + '…'
  });
  it("嵌套值回退 JSON 串", () => {
    expect(clipArgs({ a: { b: 1 } })).toEqual({ text: 'a={"b":1}', clipped: false });
  });
  it("按码点截断：不在代理对中间切出孤立代理（review Minor）", () => {
    // "ab=" 3 码元 + 60 个 emoji（各 2 码元）→ 码元 index 60 落在某 emoji 中间，
    // 旧 slice(0,60) 会切出孤立高代理；按码点截断则每个 emoji 完整。
    const r = clipArgs({ ab: "😀".repeat(60) });
    expect(r.clipped).toBe(true);
    expect(r.text.endsWith("…")).toBe(true);
    expect(r.text).not.toMatch(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])/); // 无孤立高代理
  });
});

describe("fmtNum", () => {
  it("千分位", () => expect(fmtNum(63896)).toBe("63,896"));
  it("小数位裁剪", () => expect(fmtNum(17.999, 2)).toBe("18"));
  it("null → —", () => expect(fmtNum(null)).toBe("—"));
});

describe("fmtSigned", () => {
  it("负值带 − 号", () => expect(fmtSigned(-42.5)).toBe("−42.5"));
  it("正值带 + 号", () => expect(fmtSigned(120)).toBe("+120"));
  it("null → —", () => expect(fmtSigned(null)).toBe("—"));
});
