<script setup lang="ts">
import { computed, ref } from "vue";
import { NStatistic, NTag, NSpace, NButton } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import EquityChart from "@/components/EquityChart.vue";
import TradesTable from "@/components/TradesTable.vue";

const store = useSessionsStore();
const perf = computed(() => store.performance);
const showTrades = ref(false);
</script>

<template>
  <div v-if="perf" class="perf-bar ob-card">
    <div class="left">
      <div class="caliper">净值曲线 <n-tag size="tiny" :bordered="false">盯市·含未实现PnL</n-tag></div>
      <EquityChart :points="perf.equity_curve" />
    </div>
    <div class="right">
      <n-space :size="16" wrap>
        <n-statistic label="总回报">
          <span :class="{ neg: perf.total_return_pct < 0 }">{{ perf.total_return_pct.toFixed(2) }}%</span>
          <template #suffix><n-tag size="tiny" :bordered="false">gross已实现</n-tag></template>
        </n-statistic>
        <n-statistic label="净PnL" :value="perf.net_pnl.toFixed(2)" />
        <n-statistic label="净胜率" :value="(perf.net_win_rate * 100).toFixed(1) + '%'" />
        <n-statistic label="最大回撤">
          <span class="neg">{{ perf.max_drawdown_pct.toFixed(2) }}%</span>
          <template #suffix><n-tag size="tiny" :bordered="false">net已实现equity</n-tag></template>
        </n-statistic>
        <n-statistic label="总交易" :value="perf.total_trades" />
      </n-space>
      <div class="note">曲线为盯市口径，与上方已实现指标不同口径、不可逐点对账。</div>
      <n-button text size="small" @click="showTrades = !showTrades">
        {{ showTrades ? "收起成交表 ▾" : `成交表（${perf.trades.length}）▸` }}
      </n-button>
    </div>
    <div v-if="showTrades" class="trades-wrap"><TradesTable :trades="perf.trades" /></div>
  </div>
</template>

<style scoped>
.perf-bar { border-top: 1px solid var(--ob-border); padding: 8px 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 16px; max-height: 40vh; overflow-y: auto; }
.left { min-width: 0; }
.caliper { font-size: 12px; color: var(--ob-text-muted); margin-bottom: 4px; }
.note { font-size: 11px; color: var(--ob-text-muted); margin-top: 6px; }
.neg { color: var(--ob-neg); }
.trades-wrap { grid-column: 1 / -1; margin-top: 8px; }
</style>
