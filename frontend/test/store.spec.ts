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
});
