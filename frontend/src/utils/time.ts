/** 后端出站时间戳均带 Z（UTC）。看板统一按 UTC 展示——与 DB / sim 分析口径一致，零时区心算。 */
export function parseUtc(iso: string): Date {
  return new Date(iso);
}

export function epochSec(iso: string): number {
  return Math.floor(parseUtc(iso).getTime() / 1000);
}

function pad2(n: number): string {
  return n < 10 ? "0" + n : String(n);
}

/** ISO → "YYYY-MM-DD HH:MM:SS"（UTC，去微秒/去 +00:00）。用 getUTC* 拼装，不经 toLocaleString（locale 会引入本地时区）。 */
export function fmtUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = parseUtc(iso);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ` +
    `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;
}

/** ISO → "HH:MM:SS"（UTC，给区间结束/紧凑场景）。 */
export function fmtUtcTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = parseUtc(iso);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;
}

/** epoch ms → "HH:MM:SS"（UTC，给注入事件 event.timestamp 这类 epoch-ms 源）。 */
export function fmtUtcEpoch(ms: number | null | undefined): string {
  if (ms == null) return "—";
  const d = new Date(ms);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}`;
}

export function fmtLocal(iso: string | null | undefined): string {
  if (!iso) return "—";
  return parseUtc(iso).toLocaleString();
}
