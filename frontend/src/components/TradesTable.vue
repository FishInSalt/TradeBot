<script setup lang="ts">
import { computed, h } from "vue";
import { NDataTable } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { TradeRow } from "@/api/client";
import { fmtUtc } from "@/utils/time";
import { fmtNum, fmtSigned } from "@/utils/format";
import { deriveTradeFills, type DerivedFill } from "@/utils/trades";

const props = defineProps<{ trades: TradeRow[] }>();
const rows = computed(() => deriveTradeFills(props.trades));

const signClass = (n: number | null | undefined) =>
  n != null && n > 0 ? "pos" : n != null && n < 0 ? "neg" : "";

const columns: DataTableColumns<DerivedFill> = [
  { title: "时刻(UTC)", key: "at", render: (r) => fmtUtc(r.at) },
  {
    title: "类型", key: "type",
    render: (r) => h("span", { class: r.isAdd ? "tag-add" : r.grossPnl != null ? "tag-close" : "" }, r.type),
  },
  {
    title: "方向", key: "side",
    render: (r) => h("span", { class: r.side === "long" ? "pos" : r.side === "short" ? "neg" : "" }, r.side ?? "—"),
  },
  { title: "价格", key: "price", render: (r) => fmtNum(r.price) },
  { title: "数量", key: "amount", render: (r) => fmtNum(r.amount, 4) },
  { title: "手续费", key: "fee", render: (r) => h("span", { class: "fee" }, fmtNum(r.fee)) },
  {
    title: "毛利PnL", key: "grossPnl",
    render: (r) => (r.grossPnl == null ? "—" : h("span", { class: signClass(r.grossPnl) }, fmtSigned(r.grossPnl))),
  },
  {
    title: "最终收益", key: "finalPnl",
    render: (r) => {
      if (r.finalPnl == null) return "—";
      const formula = "= " + fmtSigned(r.grossPnl) + (r.feeBreakdown ?? []).map((x) => ` − ${fmtNum(x)}`).join("");
      return h("div", { class: "final-cell" }, [
        h("div", { class: `${signClass(r.finalPnl)} final-v` }, fmtSigned(r.finalPnl)),
        h("div", { class: "formula" }, formula),
      ]);
    },
  },
];

const rowClassName = (row: DerivedFill) => (row.episodeIndex % 2 === 0 ? "ep-even" : "ep-odd");
</script>

<template>
  <div class="trades-a">
    <div class="unit-caption">金额单位 USDT · 时刻为 UTC · 价格为成交价</div>
    <n-data-table
      :columns="columns" :data="rows" size="small" :bordered="false"
      :max-height="280" :row-class-name="rowClassName"
    />
  </div>
</template>

<style scoped>
.unit-caption { font-size: 11px; color: var(--ob-text-muted); margin-bottom: 4px; }
:deep(.pos) { color: var(--ob-pos); }
:deep(.neg) { color: var(--ob-neg); }
:deep(.fee) { color: var(--ob-warn); }
.final-v { font-weight: 600; }
.formula { font-size: 10px; color: var(--ob-text-muted); }
.tag-add { background: var(--ob-warn-soft); color: var(--ob-warn); padding: 0 5px; border-radius: 3px; }
.tag-close { color: var(--ob-text-muted); }
:deep(tr.ep-odd td) { background: rgba(0, 0, 0, 0.025); }
</style>
