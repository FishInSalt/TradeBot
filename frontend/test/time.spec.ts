import { describe, it, expect } from "vitest";
import { parseUtc, epochSec, fmtUtc, fmtUtcTime, fmtUtcEpoch } from "@/utils/time";

describe("time utils", () => {
  it("parseUtc 把带 Z 的串按 UTC 解析", () => {
    // 2026-06-12T10:00:00Z = 1781258400 秒（实算：2026-01-01=1767225600 + 162天 + 10h）
    expect(parseUtc("2026-06-12T10:00:00Z").getTime()).toBe(1781258400000);
  });

  it("epochSec 返回秒级时间戳", () => {
    expect(epochSec("2026-06-12T10:00:00Z")).toBe(1781258400);
  });

  it("fmtUtc 输出 YYYY-MM-DD HH:MM:SS（UTC，不随本地时区漂移）", () => {
    expect(fmtUtc("2026-06-12T10:00:00Z")).toBe("2026-06-12 10:00:00");
  });

  it("fmtUtc 去微秒 + 去 +00:00", () => {
    expect(fmtUtc("2026-06-14T14:52:08.590628+00:00")).toBe("2026-06-14 14:52:08");
  });

  it("fmtUtc 对 null 返回占位", () => {
    expect(fmtUtc(null)).toBe("—");
  });

  it("fmtUtcTime 输出 HH:MM:SS（UTC）", () => {
    expect(fmtUtcTime("2026-06-12T10:00:00Z")).toBe("10:00:00");
  });

  it("fmtUtcEpoch 把 epoch-ms 按 UTC 渲成 HH:MM:SS", () => {
    // 1781258400000 = 2026-06-12T10:00:00Z
    expect(fmtUtcEpoch(1781258400000)).toBe("10:00:00");
    expect(fmtUtcEpoch(null)).toBe("—");
  });

  it("§F1 不可解析输入降级为占位（非 NaN-NaN-NaN）", () => {
    expect(fmtUtc("not-a-date")).toBe("—");
    expect(fmtUtcTime("not-a-date")).toBe("—");
    expect(fmtUtcEpoch(NaN)).toBe("—");
    // 注入 blob 的 timestamp 运行时可能是非数字串（TS 类型挡不住），也应降级
    expect(fmtUtcEpoch("garbage" as unknown as number)).toBe("—");
  });
});
