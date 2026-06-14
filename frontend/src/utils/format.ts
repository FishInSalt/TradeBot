/** 数值与展示友好化（议题 5）。 */

export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US");
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms === 0) return "<1ms";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 工具入参紧凑展示：`timeframe=1h, candle_count=30`。嵌套值回退 JSON 串。
 *  空 / 无参 → `（无参）`；顶层非 dict（截断回退 str / list）→ JSON 串。 */
export function fmtArgs(args: unknown): string {
  if (args == null) return "（无参）";
  if (typeof args !== "object" || Array.isArray(args)) return JSON.stringify(args);
  const entries = Object.entries(args as Record<string, unknown>);
  if (!entries.length) return "（无参）";
  return entries
    .map(([k, v]) => `${k}=${typeof v === "object" && v !== null ? JSON.stringify(v) : v}`)
    .join(", ");
}
