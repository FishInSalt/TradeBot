import type { components } from "./types";

type S = components["schemas"];
export type SessionSummary = S["SessionSummary"];
export type SessionDetail = S["SessionDetail"];
export type CycleRow = S["CycleRow"];
export type CycleDetail = S["CycleDetail"];
export type ToolCallRow = S["ToolCallRow"];
export type Performance = S["Performance"];
export type EquityPoint = S["EquityPoint"];
export type TradeRow = S["TradeRow"];
export type LiveStatus = S["LiveStatus"];
export type PositionInfo = S["PositionInfo"];
export type OrderInfo = S["OrderInfo"];
export type AlertInfo = S["AlertInfo"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) {
    throw new ApiError(res.status, `GET /api${path} → ${res.status}`);
  }
  return (await res.json()) as T;
}

export interface CyclesQuery {
  limit?: number;
  afterId?: number;
  beforeId?: number;
}

export const api = {
  listSessions: () => get<SessionSummary[]>("/sessions"),
  getSession: (sid: string) => get<SessionDetail>(`/sessions/${encodeURIComponent(sid)}`),
  getCycles: (sid: string, q: CyclesQuery = {}) => {
    const p = new URLSearchParams();
    if (q.limit != null) p.set("limit", String(q.limit));
    if (q.afterId != null) p.set("after_id", String(q.afterId));
    if (q.beforeId != null) p.set("before_id", String(q.beforeId));
    const qs = p.toString();
    return get<CycleRow[]>(`/sessions/${encodeURIComponent(sid)}/cycles${qs ? `?${qs}` : ""}`);
  },
  getCycle: (pk: number) => get<CycleDetail>(`/cycles/${pk}`),
  getPerformance: (sid: string) => get<Performance>(`/sessions/${encodeURIComponent(sid)}/performance`),
  getLive: (sid: string) => get<LiveStatus>(`/sessions/${encodeURIComponent(sid)}/live`),
};
