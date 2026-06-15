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
  expandedCycleIds: number[]; // 多展开：受控展开态（naive expanded-names），唯一来源
  loading: boolean;
  error: string | null;
  pollFailCount: number;
  selectSeq: number; // 单调序号：区分同一会话 id 的多次在途 selectSession 调用（A→B→A 重入）
  polling: boolean; // pollTick 在途标志：慢响应下避免定时器重叠发起轮询
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
    expandedCycleIds: [],
    loading: false,
    error: null,
    pollFailCount: 0,
    selectSeq: 0,
    polling: false,
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
      const seq = ++this.selectSeq; // 区分同一 id 的多次在途调用（A→B→A 重入）；currentId 守卫只防不同会话
      this.currentId = id;
      this.expandedCycleIds = [];
      this.cycleDetails = new Map();
      this.detail = null; // 进入即清旧会话数据，避免加载窗内沿用旧会话造成视觉闪烁
      this.live = null;
      this.performance = null;
      this.cycles = [];
      this.pollFailCount = 0; // 切换会话清零失败计数，避免旧会话计数误触发新会话"轮询中断"角标
      this.loading = true;
      this.error = null;
      try {
        const [detail, live, performance, cycles] = await Promise.all([
          api.getSession(id),
          api.getLive(id),
          api.getPerformance(id),
          api.getCycles(id, { limit: 50 }),
        ]);
        if (this.currentId !== id || this.selectSeq !== seq) return; // 切走 / 同 id 重入取代 / 回 home：丢弃
        this.detail = detail;
        this.live = live;
        this.performance = performance;
        this.cycles = cycles; // 后端已 id DESC
      } catch (e) {
        if (this.currentId !== id || this.selectSeq !== seq) return; // 同上：勿覆盖更新的选择
        this.error = e instanceof ApiError ? e.message : String(e);
      } finally {
        if (this.currentId === id && this.selectSeq === seq) this.loading = false; // 仅最新一次本会话收 loading
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
      if (this.polling) return; // 上一拍未完成（慢响应）：跳过本拍，避免重叠请求 + pollFailCount 语义模糊
      this.polling = true;
      try {
        const [live, performance] = await Promise.all([
          api.getLive(sid),
          api.getPerformance(sid),
        ]);
        if (this.currentId !== sid) return; // await 期间切走：丢弃旧会话数据，防串档
        this.live = live;
        this.performance = performance;
        // cycles 维护为 id DESC（mergeCycles 排序 + 后端 DESC），首元即最大 id，省去 spread
        const maxId = this.cycles.length ? this.cycles[0].id : undefined;
        const fresh = await api.getCycles(sid, maxId != null ? { afterId: maxId } : {});
        if (this.currentId !== sid) return; // 同上：mergeCycles 前再校验会话身份
        this.mergeCycles(fresh);
        this.pollFailCount = 0;
      } catch {
        if (this.currentId !== sid) return; // 已切走：勿给新会话累加旧会话的失败数
        // 瞬态错误静默：不炸 UI，仅累加，由状态卡角标在 ≥3 次时提示
        this.pollFailCount += 1;
      } finally {
        this.polling = false;
      }
    },

    // 受控入口（唯一写展开态的 action）：naive @update:expanded-names 给全量数组。
    // 乐观写入，diff 出新增 id 各自懒加载；移除的 id 不动缓存（保留，再展开命中）。
    async setExpandedCycles(ids: number[]) {
      const prev = this.expandedCycleIds;
      const added = ids.filter((id) => !prev.includes(id));
      this.expandedCycleIds = ids;
      await Promise.all(added.map((id) => this.ensureCycleDetail(id)));
    },

    // 仅懒加载详情：成功路径不改展开态；失败仅从展开态移除该 id（不卡"加载详情…"）。
    async ensureCycleDetail(id: number) {
      if (this.cycleDetails.has(id)) return; // 命中缓存不重复拉
      const sid = this.currentId; // 与 selectSession/pollTick 一致：await 后校验会话身份
      try {
        const d = await api.getCycle(id);
        if (this.currentId !== sid) return; // 已切走：勿写陈旧详情
        this.cycleDetails.set(id, d);
      } catch (e) {
        if (this.currentId !== sid) return; // 已切走：勿用旧会话错误覆盖
        this.error = e instanceof ApiError ? e.message : String(e);
        // 失败仅收起该 id，其余展开/缓存不受影响；error 由横幅提示，再展开即重试（缓存仍空）
        this.expandedCycleIds = this.expandedCycleIds.filter((x) => x !== id);
      }
    },

    clearSelection() {
      // 回到 home / 无选中会话：清空选中态。live=null → usePolling 门控早返，停止对旧会话的轮询
      this.currentId = null;
      this.detail = null;
      this.live = null;
      this.performance = null;
      this.cycles = [];
      this.cycleDetails = new Map();
      this.expandedCycleIds = [];
      this.error = null;
      this.pollFailCount = 0;
    },
  },
});
