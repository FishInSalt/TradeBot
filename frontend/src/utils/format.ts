/** 数值与展示友好化（议题 5）。 */

export function fmtTokens(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US");
}

/** 会话累计 token 紧凑展示：千为单位、千分位、`K` 后缀、无小数（如 `6,779K`）。null → —。 */
export function fmtTokensCompact(n: number | null | undefined): string {
  if (n == null) return "—";
  return Math.round(n / 1000).toLocaleString("en-US") + "K";
}

/** 空闲间隔展示：<1m / Nm / NhMm（整点省略分）。null → —（前端通常 v-if 不渲染，仍给占位）。 */
export function fmtGap(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 60000) return "<1m";
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m`;
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return m === 0 ? `${h}h` : `${h}h${m}m`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms === 0) return "<1ms";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 把 dict 拼成 `k=v, k2=v2`（嵌套值回退 JSON 串）。fmtArgs / clipArgs 共用单源，
 *  避免两处拼法漂移（PR#80 review Finding 2）。 */
function kvPairs(obj: Record<string, unknown>): string {
  return Object.entries(obj)
    .map(([k, v]) => `${k}=${typeof v === "object" && v !== null ? JSON.stringify(v) : v}`)
    .join(", ");
}

/** 工具入参紧凑展示：`timeframe=1h, candle_count=30`。嵌套值回退 JSON 串。
 *  空 / 无参 → `（无参）`；顶层非 dict（截断回退 str / list）→ JSON 串。 */
export function fmtArgs(args: unknown): string {
  if (args == null) return "（无参）";
  if (typeof args !== "object" || Array.isArray(args)) return JSON.stringify(args);
  if (!Object.keys(args as object).length) return "（无参）";
  return kvPairs(args as Record<string, unknown>);
}

/** 工具头函数式参数截断阈值（单一定义，spec §6）。 */
export const HEAD_ARGS_MAX = 60;

/** 工具头 `name(参数)` 用。空/无参 → text=''（头渲 name()）；超阈值截断 + clipped=true。 */
export function clipArgs(args: unknown): { text: string; clipped: boolean } {
  if (args == null) return { text: "", clipped: false };
  let s: string;
  if (typeof args !== "object" || Array.isArray(args)) {
    s = JSON.stringify(args);
  } else {
    if (!Object.keys(args as object).length) return { text: "", clipped: false };
    s = kvPairs(args as Record<string, unknown>);
  }
  // 按码点截断，避免在代理对（emoji / CJK 扩展区）中间切出孤立代理渲成 �（review Minor）。
  const chars = Array.from(s);
  if (chars.length > HEAD_ARGS_MAX) return { text: chars.slice(0, HEAD_ARGS_MAX).join("") + "…", clipped: true };
  return { text: s, clipped: false };
}

/** 千分位数值，null → —（spec §8）。 */
export function fmtNum(n: number | null | undefined, maxFrac = 2): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: maxFrac });
}

/** 带正负号数值（U+2212 − 匹配 spec 示例），null → —。 */
export function fmtSigned(n: number | null | undefined, maxFrac = 2): string {
  if (n == null) return "—";
  const s = Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: maxFrac });
  return n < 0 ? `−${s}` : `+${s}`;
}

/** 带符号百分比（U+2212 负号、固定两位小数、带 %），null → —。用于 PnL%。 */
export function fmtSignedPct(n: number | null | undefined): string {
  if (n == null) return "—";
  const s = Math.abs(n).toFixed(2);
  return n < 0 ? `−${s}%` : `+${s}%`;
}
