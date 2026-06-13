/** 后端出站时间戳均带 Z（UTC）。前端解析即 UTC instant，本地展示用 toLocaleString。 */
export function parseUtc(iso: string): Date {
  return new Date(iso);
}

export function epochSec(iso: string): number {
  return Math.floor(parseUtc(iso).getTime() / 1000);
}

export function fmtLocal(iso: string | null | undefined): string {
  if (!iso) return "—";
  return parseUtc(iso).toLocaleString();
}
