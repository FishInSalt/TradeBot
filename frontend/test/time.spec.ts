import { describe, it, expect } from "vitest";
import { parseUtc, epochSec, fmtLocal } from "@/utils/time";

describe("time utils", () => {
  it("parseUtc 把带 Z 的串按 UTC 解析", () => {
    // 2026-06-12T10:00:00Z = 1781258400 秒（实算：2026-01-01=1767225600 + 162天 + 10h）
    expect(parseUtc("2026-06-12T10:00:00Z").getTime()).toBe(1781258400000);
  });

  it("epochSec 返回秒级时间戳", () => {
    expect(epochSec("2026-06-12T10:00:00Z")).toBe(1781258400);
  });

  it("fmtLocal 对 null 返回占位", () => {
    expect(fmtLocal(null)).toBe("—");
  });

  it("fmtLocal 对有效串返回非空字符串", () => {
    expect(fmtLocal("2026-06-12T10:00:00Z")).not.toBe("—");
    expect(typeof fmtLocal("2026-06-12T10:00:00Z")).toBe("string");
  });
});
