import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useSessionsStore, PAGE_SIZE } from "@/stores/sessions";
import { api } from "@/api/client";

beforeEach(() => setActivePinia(createPinia()));
afterEach(() => vi.restoreAllMocks());

function cyc(id: number) {
  return { id, cycle_label: `c${id}`, triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z", tokens_consumed: 1, wall_time_ms: 1, execution_status: "ok", position: null, key_events: [] };
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
    expect(s.expandedCycleIds).toEqual([]);
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

  it("setExpandedCycles 懒加载新增 id 并缓存；收起时保留缓存、再展开命中缓存", async () => {
    const spy = vi.spyOn(api, "getCycle").mockResolvedValue({ id: 5 } as any);
    const s = useSessionsStore();
    await s.setExpandedCycles([5]);
    expect(s.expandedCycleIds).toEqual([5]);
    expect(s.cycleDetails.get(5)?.id).toBe(5);
    await s.setExpandedCycles([]); // 收起：从展开态移除
    expect(s.expandedCycleIds).toEqual([]);
    expect(s.cycleDetails.has(5)).toBe(true); // 缓存保留
    await s.setExpandedCycles([5]); // 再展开命中缓存,不重复拉取
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("setExpandedCycles 多 id 同时展开各自懒加载；增量展开仅拉新增 id", async () => {
    const spy = vi.spyOn(api, "getCycle").mockImplementation(async (id: any) => ({ id }) as any);
    const s = useSessionsStore();
    await s.setExpandedCycles([3, 2, 1]);
    expect(s.expandedCycleIds).toEqual([3, 2, 1]);
    expect(s.cycleDetails.get(2)?.id).toBe(2);
    expect(spy).toHaveBeenCalledTimes(3);
    await s.setExpandedCycles([3, 2, 1, 4]); // 增量：仅 4 是新增
    expect(spy).toHaveBeenCalledTimes(4);
    expect(s.cycleDetails.get(4)?.id).toBe(4);
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

  it("ensureCycleDetail await 期间被切换会话时丢弃陈旧详情", async () => {
    let resolveCycle!: (v: unknown) => void;
    const pendingCycle = new Promise((r) => { resolveCycle = r; });
    vi.spyOn(api, "getCycle").mockReturnValue(pendingCycle as any);
    const s = useSessionsStore();
    s.currentId = "A";
    const p = s.setExpandedCycles([7]); // sid=A，挂起在 getCycle(7)
    s.currentId = "B"; // await 期间切走
    resolveCycle({ id: 7 });
    await p;
    expect(s.cycleDetails.has(7)).toBe(false); // 陈旧详情未写入
  });

  it("selectSession 进入即清旧会话数据（消加载窗内闪烁）", async () => {
    let resolveDetail!: (v: unknown) => void;
    const pending = new Promise((r) => { resolveDetail = r; });
    vi.spyOn(api, "getSession").mockReturnValue(pending as any);
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    s.detail = { id: "old" } as any; // 旧会话残留
    s.cycles = [cyc(9)] as any;
    const p = s.selectSession("new");
    expect(s.detail).toBeNull(); // 进入即清，不沿用旧会话数据到加载窗
    expect(s.cycles).toEqual([]);
    resolveDetail({ id: "new" });
    await p;
    expect(s.detail).toMatchObject({ id: "new" });
  });

  it("setExpandedCycles 拉取失败：从展开态移除该 id + 设 error（不卡加载态，可重试）", async () => {
    const spy = vi.spyOn(api, "getCycle").mockRejectedValue(new Error("boom"));
    const s = useSessionsStore();
    s.currentId = "s1";
    await s.setExpandedCycles([5]);
    expect(s.expandedCycleIds).toEqual([]); // 失败仅移除该 id，不卡在"加载详情…"
    expect(s.error).toContain("boom"); // error 有出口（DashboardView 横幅消费）
    expect(s.cycleDetails.has(5)).toBe(false);
    spy.mockResolvedValue({ id: 5 } as any);
    await s.setExpandedCycles([5]); // 再展开重试：缓存仍空 → 再拉
    expect(s.cycleDetails.get(5)?.id).toBe(5);
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("setExpandedCycles 一个 id 失败仅移除自己，其余展开/缓存不受影响", async () => {
    const spy = vi.spyOn(api, "getCycle").mockImplementation(async (id: any) =>
      id === 2 ? Promise.reject(new Error("boom")) : ({ id } as any));
    const s = useSessionsStore();
    s.currentId = "s1";
    await s.setExpandedCycles([3, 2, 1]);
    expect(s.expandedCycleIds).toEqual([3, 1]); // 仅 2 被移除
    expect(s.cycleDetails.get(3)?.id).toBe(3);
    expect(s.cycleDetails.get(1)?.id).toBe(1);
    expect(s.cycleDetails.has(2)).toBe(false);
    expect(spy).toHaveBeenCalledTimes(3);
  });

  it("setExpandedCycles 多 id 并发全失败收敛到 []（链式 read-modify-write 不丢移除）", async () => {
    vi.spyOn(api, "getCycle").mockRejectedValue(new Error("boom"));
    const s = useSessionsStore();
    s.currentId = "s1";
    await s.setExpandedCycles([3, 2, 1]);
    expect(s.expandedCycleIds).toEqual([]); // 三个各自移除，单线程链式收敛到空
    expect(s.cycleDetails.size).toBe(0);
    expect(s.error).toContain("boom");
  });

  it("setExpandedCycles await 期间切会话：成功返回不污染新会话（钉死乐观写不变量）", async () => {
    // 守卫缺失隐患的回归锚点：乐观写 expandedCycleIds 在 await 前且无 sid 守卫，
    // 当前靠 selectSession/clearSelection 同步清空 + ensureCycleDetail 双路守卫才安全。
    // 若未来给 setExpandedCycles 加 await-后回写而漏守卫，此用例立即变红。
    let resolveCycle!: (v: unknown) => void;
    const pending = new Promise((r) => { resolveCycle = r; });
    vi.spyOn(api, "getCycle").mockReturnValue(pending as any);
    const s = useSessionsStore();
    s.currentId = "A";
    const p = s.setExpandedCycles([5]); // sid=A，乐观写 [5] 后挂起在 getCycle
    s.currentId = "B"; // 切会话（selectSession:63 会同步清空展开态）
    s.expandedCycleIds = [];
    resolveCycle({ id: 5 });
    await p;
    expect(s.cycleDetails.has(5)).toBe(false); // A 的详情未写入 B
    expect(s.expandedCycleIds).toEqual([]); // 不残留 A 的展开 id
  });

  it("clearSelection 清空选中态（回 home 停轮询）", () => {
    const s = useSessionsStore();
    s.currentId = "s1";
    s.detail = { id: "s1" } as any;
    s.live = { status: "active" } as any;
    s.cycles = [cyc(1)] as any;
    s.expandedCycleIds = [1];
    s.clearSelection();
    expect(s.currentId).toBeNull();
    expect(s.detail).toBeNull();
    expect(s.live).toBeNull();
    expect(s.cycles).toEqual([]);
    expect(s.expandedCycleIds).toEqual([]);
  });

  it("selectSession 同会话快速重入（A→B→A）：旧批晚到不覆盖新批（selectSeq）", async () => {
    let resolveFirst!: (v: unknown) => void;
    const firstA = new Promise((r) => { resolveFirst = r; });
    vi.spyOn(api, "getSession")
      .mockReturnValueOnce(firstA as any) // 第一次 selectSession("A")：挂起
      .mockResolvedValue({ id: "A2" } as any); // 后续调用：即时
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    const p1 = s.selectSession("A"); // seq=1，挂起在 firstA
    await s.selectSession("A"); // seq=2，即时完成 → detail={id:"A2"}
    resolveFirst({ id: "A1" }); // 第一批（旧 seq）晚到
    await p1;
    expect(s.detail).toMatchObject({ id: "A2" }); // 旧批被丢弃，保留新批（currentId 同为 A，仅 seq 能区分）
  });

  it("pollTick 上一拍在途时跳过重叠调用（in-flight guard）", async () => {
    let resolveLive!: (v: unknown) => void;
    const pendingLive = new Promise((r) => { resolveLive = r; });
    const liveSpy = vi.spyOn(api, "getLive").mockReturnValue(pendingLive as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    const p1 = s.pollTick(); // polling=true，挂起在 getLive
    await s.pollTick(); // 第二拍：polling 在途 → 立即 return，不发请求
    expect(liveSpy).toHaveBeenCalledTimes(1);
    resolveLive({ status: "active" });
    await p1;
    expect(s.polling).toBe(false); // 完成后复位
  });

  // ===== 加载更早历史(loadOlder) =====

  it("loadOlder 用最末元 id 作 beforeId 拉取并 merge 到底部", async () => {
    vi.spyOn(api, "getCycles").mockResolvedValue([cyc(50), cyc(49)] as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    s.cycles = [cyc(52), cyc(51)] as any;
    await s.loadOlder();
    expect(api.getCycles).toHaveBeenCalledWith("s1", { beforeId: 51, limit: PAGE_SIZE });
    expect(s.cycles.map((c) => c.id)).toEqual([52, 51, 50, 49]); // 追加到底部、DESC
  });

  it("loadOlder 返回 < PAGE_SIZE 置 reachedOldest（到顶）", async () => {
    vi.spyOn(api, "getCycles").mockResolvedValue([cyc(50)] as any); // 1 < PAGE_SIZE
    const s = useSessionsStore();
    s.currentId = "s1";
    s.cycles = [cyc(51)] as any;
    await s.loadOlder();
    expect(s.reachedOldest).toBe(true);
  });

  it("loadOlder 返回 === PAGE_SIZE 不置 reachedOldest（可能还有更早）", async () => {
    const full = Array.from({ length: PAGE_SIZE }, (_, i) => cyc(100 - i)); // 正好 PAGE_SIZE 条
    vi.spyOn(api, "getCycles").mockResolvedValue(full as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    s.cycles = [cyc(101)] as any;
    await s.loadOlder();
    expect(s.reachedOldest).toBe(false);
  });

  it("loadOlder 守卫：loadingOlder 在途时不发请求", async () => {
    const spy = vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    s.cycles = [cyc(5)] as any;
    s.loadingOlder = true;
    await s.loadOlder();
    expect(spy).not.toHaveBeenCalled();
  });

  it("loadOlder 守卫：reachedOldest 已置时不发请求", async () => {
    const spy = vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    s.cycles = [cyc(5)] as any;
    s.reachedOldest = true;
    await s.loadOlder();
    expect(spy).not.toHaveBeenCalled();
  });

  it("loadOlder 守卫：cycles 为空时不发请求（无游标基准）", async () => {
    const spy = vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    s.cycles = [] as any;
    await s.loadOlder();
    expect(spy).not.toHaveBeenCalled();
  });

  it("loadOlder await 期间切会话(A→B)丢弃结果、不污染新会话", async () => {
    let resolveOlder!: (v: unknown) => void;
    const pending = new Promise((r) => { resolveOlder = r; });
    vi.spyOn(api, "getCycles").mockReturnValue(pending as any);
    const s = useSessionsStore();
    s.currentId = "A";
    s.cycles = [cyc(10)] as any;
    const p = s.loadOlder(); // sid=A, beforeId=10, 在途
    s.currentId = "B";
    s.cycles = [cyc(99)] as any; // 模拟切到 B
    resolveOlder([cyc(9)]);
    await p;
    expect(s.cycles.map((c) => c.id)).toEqual([99]); // A 的 older 未合并进 B
  });

  it("loadOlder 错误：写 error、复位 loadingOlder、不置 reachedOldest", async () => {
    vi.spyOn(api, "getCycles").mockRejectedValue(new Error("boom"));
    const s = useSessionsStore();
    s.currentId = "s1";
    s.cycles = [cyc(5)] as any;
    await s.loadOlder();
    expect(s.error).toContain("boom");
    expect(s.loadingOlder).toBe(false);
    expect(s.reachedOldest).toBe(false); // 允许重试
  });

  it("selectSession 首屏 < PAGE_SIZE → reachedOldest=true（短会话不显假按钮）", async () => {
    vi.spyOn(api, "getSession").mockResolvedValue({ id: "s1" } as any);
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "paused" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([cyc(3), cyc(2), cyc(1)] as any); // 3 < PAGE_SIZE
    const s = useSessionsStore();
    await s.selectSession("s1");
    expect(s.reachedOldest).toBe(true);
    expect(api.getCycles).toHaveBeenCalledWith("s1", { limit: PAGE_SIZE });
  });

  it("selectSession 首屏 === PAGE_SIZE → reachedOldest=false（可能有更早）", async () => {
    const full = Array.from({ length: PAGE_SIZE }, (_, i) => cyc(100 - i));
    vi.spyOn(api, "getSession").mockResolvedValue({ id: "s1" } as any);
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "paused" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    vi.spyOn(api, "getCycles").mockResolvedValue(full as any);
    const s = useSessionsStore();
    await s.selectSession("s1");
    expect(s.reachedOldest).toBe(false);
  });

  it("clearSelection 重置 loadingOlder/reachedOldest", () => {
    const s = useSessionsStore();
    s.loadingOlder = true;
    s.reachedOldest = true;
    s.clearSelection();
    expect(s.loadingOlder).toBe(false);
    expect(s.reachedOldest).toBe(false);
  });

  it("loadOlder 深翻在途 + 同会话重选(A→B→A)：selectSeq 丢弃迟到响应、无空洞（去掉 selectSeq 守卫则转红）", async () => {
    const deep = Array.from({ length: 331 - 152 + 1 }, (_, i) => cyc(331 - i)); // 深翻后 cycles=[331..152]
    const firstScreen = Array.from({ length: PAGE_SIZE }, (_, i) => cyc(331 - i)); // 重选首屏 [331..282]
    const older = Array.from({ length: PAGE_SIZE }, (_, i) => cyc(151 - i)); // 迟到响应 [151..102]
    let resolveOlder!: (v: unknown) => void;
    const pendingOlder = new Promise((r) => { resolveOlder = r; });
    vi.spyOn(api, "getCycles")
      .mockReturnValueOnce(pendingOlder as any) // loadOlder 在途（深游标 beforeId=152）
      .mockResolvedValue(firstScreen as any);   // selectSession 重选首屏
    vi.spyOn(api, "getSession").mockResolvedValue({ id: "A" } as any);
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "paused" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    const s = useSessionsStore();
    s.currentId = "A";
    s.cycles = deep as any;
    const p = s.loadOlder();      // beforeId=152，在途，记 seq=当前 selectSeq
    await s.selectSession("A");   // 同会话重选：++selectSeq，cycles 复位为首屏 [331..282]
    resolveOlder(older);          // 迟到 [151..102] 返回
    await p;
    // selectSeq 变更使迟到响应作废 → cycles 保持首屏，不裂出 281..152 空洞
    expect(s.cycles.map((c) => c.id)).toEqual(firstScreen.map((c) => c.id));
    expect(s.cycles.some((c) => c.id === 102)).toBe(false); // 迟到响应未被合并
  });
});
