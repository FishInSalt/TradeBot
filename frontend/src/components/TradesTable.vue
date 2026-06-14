<script setup lang="ts">
import { computed, h } from "vue";
import { NDataTable } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { TradeRow } from "@/api/client";
import { fmtLocal } from "@/utils/time";

const props = defineProps<{ trades: TradeRow[] }>();

const columns: DataTableColumns<TradeRow> = [
  { title: "时间", key: "at", render: (r) => fmtLocal(r.at) },
  { title: "动作", key: "action" },
  { title: "方向", key: "side", render: (r) => r.side ?? "—" },
  { title: "价格", key: "price", render: (r) => (r.price ?? "—").toString() },
  { title: "数量", key: "amount", render: (r) => (r.amount ?? "—").toString() },
  {
    title: "PnL",
    key: "pnl",
    render: (r) => h("span", { class: (r.pnl ?? 0) > 0 ? "pos" : (r.pnl ?? 0) < 0 ? "neg" : "" }, String(r.pnl ?? "—")),
  },
  { title: "费", key: "fee", render: (r) => (r.fee ?? "—").toString() },
];
const data = computed(() => props.trades);
</script>

<template>
  <n-data-table :columns="columns" :data="data" size="small" :bordered="false" :max-height="220" />
</template>

<style scoped>
:deep(.pos) { color: var(--ob-pos); }
:deep(.neg) { color: var(--ob-neg); }
</style>
