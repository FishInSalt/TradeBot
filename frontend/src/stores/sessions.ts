import { defineStore } from "pinia";
import {
  api,
  ApiError,
  type SessionSummary,
  type SessionDetail,
  type LiveStatus,
  type Performance,
  type CycleRow,
  type CycleDetail,
} from "@/api/client";

interface State {
  sessions: SessionSummary[];
  currentId: string | null;
  detail: SessionDetail | null;
  live: LiveStatus | null;
  performance: Performance | null;
  cycles: CycleRow[]; // 维护为 id DESC（新在顶）
  cycleDetails: Map<number, CycleDetail>; // 展开懒加载缓存
  expandedCycleId: number | null;
  loading: boolean;
  error: string | null;
  pollFailCount: number;
}

export const useSessionsStore = defineStore("sessions", {
  state: (): State => ({
    sessions: [],
    currentId: null,
    detail: null,
    live: null,
    performance: null,
    cycles: [],
    cycleDetails: new Map(),
    expandedCycleId: null,
    loading: false,
    error: null,
    pollFailCount: 0,
  }),

  getters: {
    currentSession: (s): SessionSummary | undefined =>
      s.sessions.find((x) => x.id === s.currentId),
  },

  actions: {
    async loadSessions() {
      try {
        this.sessions = await api.listSessions();
      } catch (e) {
        this.error = e instanceof ApiError ? e.message : String(e);
      }
    },

    async selectSession(id: string) {
      this.currentId = id;
      this.expandedCycleId = null;
      this.cycleDetails = new Map();
      this.loading = true;
      this.error = null;
      try {
        const [detail, live, performance, cycles] = await Promise.all([
          api.getSession(id),
          api.getLive(id),
          api.getPerformance(id),
          api.getCycles(id, { limit: 50 }),
        ]);
        this.detail = detail;
        this.live = live;
        this.performance = performance;
        this.cycles = cycles; // 后端已 id DESC
      } catch (e) {
        this.error = e instanceof ApiError ? e.message : String(e);
      } finally {
        this.loading = false;
      }
    },

    mergeCycles(fresh: CycleRow[]) {
      const seen = new Set(this.cycles.map((c) => c.id));
      const add = fresh.filter((c) => !seen.has(c.id));
      this.cycles = [...add, ...this.cycles].sort((a, b) => b.id - a.id);
    },

    async pollTick() {
      const sid = this.currentId;
      if (!sid) return;
      try {
        const [live, performance] = await Promise.all([
          api.getLive(sid),
          api.getPerformance(sid),
        ]);
        this.live = live;
        this.performance = performance;
        const maxId = this.cycles.length
          ? Math.max(...this.cycles.map((c) => c.id))
          : undefined;
        const fresh = await api.getCycles(sid, maxId != null ? { afterId: maxId } : {});
        this.mergeCycles(fresh);
        this.pollFailCount = 0;
      } catch {
        // 瞬态错误静默：不炸 UI，仅累加，由状态卡角标在 ≥3 次时提示
        this.pollFailCount += 1;
      }
    },

    async expandCycle(id: number) {
      if (this.expandedCycleId === id) {
        this.expandedCycleId = null; // 再点同条收起
        return;
      }
      this.expandedCycleId = id;
      if (!this.cycleDetails.has(id)) {
        try {
          const d = await api.getCycle(id);
          this.cycleDetails.set(id, d);
        } catch (e) {
          this.error = e instanceof ApiError ? e.message : String(e);
        }
      }
    },
  },
});
