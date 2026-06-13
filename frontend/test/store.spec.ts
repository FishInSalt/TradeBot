import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useSessionsStore } from "@/stores/sessions";
import { api } from "@/api/client";

beforeEach(() => setActivePinia(createPinia()));
afterEach(() => vi.restoreAllMocks());

function cyc(id: number) {
  return { id, cycle_label: `c${id}`, triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z", decision_head: "d", tokens_consumed: 1, wall_time_ms: 1, execution_status: "ok" };
}

describe("sessions store", () => {
  it("selectSession 并发装配 detail/live/performance/cycles 并设 currentId", async () => {
    vi.spyOn(api, "getSession").mockResolvedValue({ id: "s1" } as any);
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({ initial_balance: 100 } as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([cyc(3), cyc(2), cyc(1)] as any);
    const s = useSessionsStore();
    await s.selectSession("s1");
    expect(s.currentId).toBe("s1");
    expect(s.detail?.id).toBe("s1");
    expect(s.live?.status).toBe("active");
    expect(s.performance?.initial_balance).toBe(100);
    expect(s.cycles.map((c) => c.id)).toEqual([3, 2, 1]);
    expect(s.expandedCycleId).toBeNull();
  });

  it("pollTick 增量 append 且按 id 去重并保持 id DESC", async () => {
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    s.cycles = [cyc(2), cyc(1)] as any;
    vi.spyOn(api, "getCycles").mockResolvedValue([cyc(4), cyc(3), cyc(2)] as any);
    await s.pollTick();
    expect(s.cycles.map((c) => c.id)).toEqual([4, 3, 2, 1]); // 去重 + DESC
    expect(api.getCycles).toHaveBeenCalledWith("s1", { afterId: 2 }); // 取当前最大 id 之上
  });

  it("pollTick 失败累加 pollFailCount 不抛", async () => {
    vi.spyOn(api, "getLive").mockRejectedValue(new Error("boom"));
    const s = useSessionsStore();
    s.currentId = "s1";
    await s.pollTick();
    expect(s.pollFailCount).toBe(1);
  });

  it("pollTick 成功重置 pollFailCount", async () => {
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    s.pollFailCount = 2;
    await s.pollTick();
    expect(s.pollFailCount).toBe(0);
  });

  it("expandCycle 懒加载并缓存，再点同一条收起", async () => {
    const spy = vi.spyOn(api, "getCycle").mockResolvedValue({ id: 5 } as any);
    const s = useSessionsStore();
    await s.expandCycle(5);
    expect(s.expandedCycleId).toBe(5);
    expect(s.cycleDetails.get(5)?.id).toBe(5);
    await s.expandCycle(5); // toggle 收起
    expect(s.expandedCycleId).toBeNull();
    await s.expandCycle(5); // 再展开命中缓存,不重复拉取
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("currentSession getter 按 currentId 命中列表项", () => {
    const s = useSessionsStore();
    s.sessions = [{ id: "s1", status: "active" } as any, { id: "s2", status: "paused" } as any];
    s.currentId = "s2";
    expect(s.currentSession?.status).toBe("paused");
  });

  it("selectSession await 期间被切换抢占时丢弃旧会话结果（防串档）", async () => {
    let resolveA!: (v: unknown) => void;
    const pendingDetail = new Promise((r) => { resolveA = r; });
    vi.spyOn(api, "getSession").mockReturnValue(pendingDetail as any);
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    const p = s.selectSession("A"); // currentId=A，挂起在 getSession(A)
    s.currentId = "B"; // 模拟用户切到别的会话
    resolveA({ id: "A" });
    await p;
    expect(s.detail).toBeNull(); // A 的结果未写入（已切走）
  });

  it("pollTick await 期间被切换抢占时丢弃旧会话数据", async () => {
    let resolveLive!: (v: unknown) => void;
    const pendingLive = new Promise((r) => { resolveLive = r; });
    vi.spyOn(api, "getLive").mockReturnValue(pendingLive as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({ initial_balance: 999 } as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    s.currentId = "A";
    const p = s.pollTick(); // sid=A，挂起在 getLive(A)
    s.currentId = "B"; // await 期间切走
    resolveLive({ status: "paused" });
    await p;
    expect(s.live).toBeNull(); // 旧会话 live/performance 未写入
    expect(s.performance).toBeNull();
  });

  it("selectSession 重置 pollFailCount", async () => {
    vi.spyOn(api, "getSession").mockResolvedValue({ id: "s1" } as any);
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    s.pollFailCount = 2;
    await s.selectSession("s1");
    expect(s.pollFailCount).toBe(0);
  });

  it("expandCycle await 期间被切换会话时丢弃陈旧详情", async () => {
    let resolveCycle!: (v: unknown) => void;
    const pendingCycle = new Promise((r) => { resolveCycle = r; });
    vi.spyOn(api, "getCycle").mockReturnValue(pendingCycle as any);
    const s = useSessionsStore();
    s.currentId = "A";
    const p = s.expandCycle(7); // sid=A，挂起在 getCycle(7)
    s.currentId = "B"; // await 期间切走
    resolveCycle({ id: 7 });
    await p;
    expect(s.cycleDetails.has(7)).toBe(false); // 陈旧详情未写入
  });
});
