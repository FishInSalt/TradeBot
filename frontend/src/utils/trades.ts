/** 收益分析 A+ 交易历程：持仓周期（episode, flat→flat）派生 + 类型标签 + 周期级聚合。
 *  纯函数，单一来源供 TradesTable 与 PerformanceBar 复用（spec §C）。 */
import type { TradeRow } from "@/api/client";

export interface DerivedFill extends TradeRow {
  type: string;                   // 开仓/加仓/限价开仓/限价加仓/平仓/止损平仓/止盈平仓/强平/限价平仓
  isAdd: boolean;                 // 加仓行（同周期内已有同向开仓）
  grossPnl: number | null;        // 平仓行 = trade.pnl（毛）；开/加行 = null
  finalPnl: number | null;        // 平仓行 = grossPnl − Σ周期手续费；开/加行 = null
  feeBreakdown: number[] | null;  // 平仓行 = 本周期各 fill 手续费列表（拼算式用）；开/加行 = null
  episodeIndex: number;           // 0-based 周期号（交替底色用）
}

/** 平仓触发细分标签（与 queries._classify_fill 平仓词汇逐字同源，drift-guard 锁同步，见 Task 6）。 */
export function CLOSE_LABEL(reason: string | null | undefined): string {
  switch (reason) {
    case "stop": return "止损平仓";
    case "take_profit": return "止盈平仓";
    case "liquidation": return "强平";
    case "limit": return "限价平仓";
    default: return "平仓";        // market / 未知
  }
}

/** 开仓/加仓标签（前端原创，有意不同于 _classify_fill：方向另列、市价开仓不返 None）。 */
export function OPEN_LABEL(reason: string | null | undefined, isAdd: boolean): string {
  if (reason === "limit") return isAdd ? "限价加仓" : "限价开仓";
  return isAdd ? "加仓" : "开仓";  // market / 未知
}

/** trades（id ASC 的 fill 列表）→ 逐行派生。平仓即结束周期、开仓后同向再开 = 加仓。
 *  跳过 legacy null-amount fill（镜像 MetricsService skip，使表 Σ最终收益 与 net_pnl 对齐）。 */
export function deriveTradeFills(trades: TradeRow[]): DerivedFill[] {
  let episodeIndex = 0;
  let cur: TradeRow[] = [];        // 当前周期 fill 累积（fee 合计 + 开/加判定）
  const out: DerivedFill[] = [];
  for (const fill of trades) {
    if (fill.amount == null) continue;          // legacy null-amount → 跳过
    const isClose = fill.pnl != null;           // 平仓 = pnl 非空
    if (!isClose) {
      const isAdd = cur.length > 0;
      out.push({ ...fill, type: OPEN_LABEL(fill.trigger_reason, isAdd), isAdd,
                 grossPnl: null, finalPnl: null, feeBreakdown: null, episodeIndex });
      cur.push(fill);
    } else {
      const fees = [...cur.map((x) => x.fee ?? 0), fill.fee ?? 0];
      const finalPnl = (fill.pnl as number) - fees.reduce((a, b) => a + b, 0);
      out.push({ ...fill, type: CLOSE_LABEL(fill.trigger_reason), isAdd: false,
                 grossPnl: fill.pnl, finalPnl, feeBreakdown: fees, episodeIndex });
      episodeIndex += 1;
      cur = [];
    }
  }
  return out;
}
