import { describe, it, expect, vi, afterEach } from "vitest";
import { api, ApiError } from "@/api/client";

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("listSessions 解析 2xx JSON", async () => {
    mockFetch(200, [{ id: "s1" }]);
    const rows = await api.listSessions();
    expect(rows[0].id).toBe("s1");
    expect(fetch).toHaveBeenCalledWith("/api/sessions");
  });

  it("非 2xx 抛带 status 的 ApiError", async () => {
    mockFetch(404, { detail: "nope" });
    await expect(api.getSession("nope")).rejects.toBeInstanceOf(ApiError);
    await expect(api.getSession("nope")).rejects.toMatchObject({ status: 404 });
  });

  it("getCycles 拼接 after_id/limit query", async () => {
    mockFetch(200, []);
    await api.getCycles("s1", { limit: 50, afterId: 12 });
    expect(fetch).toHaveBeenCalledWith("/api/sessions/s1/cycles?limit=50&after_id=12");
  });

  it("getCycles 无参数时不带 query string", async () => {
    mockFetch(200, []);
    await api.getCycles("s1");
    expect(fetch).toHaveBeenCalledWith("/api/sessions/s1/cycles");
  });

  it("getCycle 命中详情端点", async () => {
    mockFetch(200, { id: 7 });
    const d = await api.getCycle(7);
    expect(d.id).toBe(7);
    expect(fetch).toHaveBeenCalledWith("/api/cycles/7");
  });
});
