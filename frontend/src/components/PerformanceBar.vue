<script setup lang="ts">
import { computed, ref } from "vue";
import { NTag, NButton } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import EquityChart from "@/components/EquityChart.vue";
import TradesTable from "@/components/TradesTable.vue";
import PriceChart from "@/components/PriceChart.vue";
import { fmtNum, fmtSigned, fmtSignedPct } from "@/utils/format";
import { fmtUtc } from "@/utils/time";
import { deriveTradeFills, summarizeEpisodes } from "@/utils/trades";

const store = useSessionsStore();
const expanded = ref(false);
const showTrades = ref(false);

const perf = computed(() => store.performance);
const detail = computed(() => store.detail);
// cycles 维护为 id DESC，首元即最新 cycle id；驱动 PriceChart 在新 cycle 时追平（不每 5s 复位）
const latestCycleId = computed(() => (store.cycles.length ? store.cycles[0].id : null));
const fills = computed(() => (perf.value ? deriveTradeFills(perf.value.trades) : []));
const summary = computed(() => summarizeEpisodes(fills.value));

const initial = computed(() => perf.value?.initial_balance ?? 0);
const netPnl = computed(() => perf.value?.net_pnl ?? 0);
const netPnlPct = computed(() => (initial.value > 0 ? (netPnl.value / initial.value) * 100 : null));
const grossPnl = computed(() => perf.value?.total_pnl ?? 0);   // 毛额直取（review minor 5），免反推
const grossPnlPct = computed(() => perf.value?.total_return_pct ?? 0);
const feesRealized = computed(() => grossPnl.value - netPnl.value);   // 毛−净 = 已实现手续费（精确）
const mdd = computed(() => perf.value?.max_drawdown_pct ?? 0);

const openPos = computed(() => perf.value?.open_position ?? null);
const unrealizedEntryFee = computed(() =>
  openPos.value && perf.value ? perf.value.total_fees - feesRealized.value : 0);

const decided = computed(() => summary.value.wins + summary.value.losses);
const winRateText = computed(() =>
  summary.value.winRate == null ? "—" : `${(summary.value.winRate * 100).toFixed(0)}%`);
const profitFactorText = computed(() =>
  summary.value.profitFactor == null ? "—" : summary.value.profitFactor.toFixed(2));

const signClass = (n: number | null | undefined) =>
  n != null && n > 0 ? "pos" : n != null && n < 0 ? "neg" : "";
</script>

<template>
  <div v-if="perf" class="perf-bar ob-card" :class="{ expanded }">
    <!-- 折叠态：细条 -->
    <div v-if="!expanded" class="collapsed-bar" @click="expanded = true">
      <span class="lead">收益 ▴</span>
      <span>净PnL <b :class="signClass(netPnl)">{{ fmtSigned(netPnl) }}</b>
        <span class="muted">{{ fmtSignedPct(netPnlPct) }}</span></span>
      <span class="dot">·</span>
      <span>毛PnL <b :class="signClass(grossPnl)">{{ fmtSigned(grossPnl) }}</b>
        <span class="muted">{{ fmtSignedPct(grossPnlPct) }}</span></span>
      <span class="dot">·</span>
      <span>手续费 <b class="fee">{{ fmtNum(feesRealized) }}</b></span>
      <span class="dot">·</span>
      <span>胜率 <b>{{ winRateText }}</b> <span class="muted">({{ summary.wins }}/{{ decided }})</span></span>
      <span v-if="openPos && openPos.unrealized_pnl != null" class="held-box">持仓 未实现(毛)
        <b :class="signClass(openPos.unrealized_pnl)">{{ fmtSigned(openPos.unrealized_pnl) }}</b></span>
      <span class="expand-hint">点击展开 ▴</span>
    </div>

    <!-- 展开态 -->
    <template v-else>
      <div class="exp-head" @click="expanded = false">
        <span class="lead">收益分析 ▾</span>
        <span class="caveat">已实现指标 vs 盯市曲线 不同口径、不可逐点对账</span>
        <span class="collapse-hint">点击折叠 ▾</span>
      </div>

      <!-- 当前持仓条（仅未平仓） -->
      <div v-if="openPos" class="held-bar">
        <span class="held-title">当前持仓(未平仓)</span>
        <span class="side-tag" :class="openPos.side">{{ openPos.side === "long" ? "多" : "空" }}</span>
        <span>{{ fmtNum(openPos.contracts, 4) }} @ {{ fmtNum(openPos.entry_price) }}</span>
        <span v-if="openPos.unrealized_pnl != null">未实现收益(毛)
          <b :class="signClass(openPos.unrealized_pnl)">{{ fmtSigned(openPos.unrealized_pnl) }}</b>
          <span class="muted">{{ fmtSignedPct(openPos.pnl_pct_of_notional) }} · 盯市,未扣平仓费<template v-if="openPos.unrealized_as_of"> · 截至 {{ fmtUtc(openPos.unrealized_as_of) }}</template></span></span>
        <span class="held-fee">未平仓入场费 <b>{{ fmtNum(unrealizedEntryFee) }}</b>
          <span class="muted">已付,从净值扣</span></span>
      </div>

      <!-- 价格走势 K 线 + 买卖点 markers（整宽 section，spec §F） -->
      <div v-if="detail" class="price-section">
        <PriceChart
          :session-id="detail.id"
          :symbol="detail.symbol"
          :default-timeframe="detail.timeframe"
          :trades="perf.trades"
          :latest-cycle-id="latestCycleId"
        />
      </div>

      <div class="exp-grid">
        <div class="curve">
          <div class="caliper">净值曲线 <n-tag size="tiny" :bordered="false">盯市·含未实现</n-tag></div>
          <EquityChart :points="perf.equity_curve" />
        </div>
        <div class="metrics">
          <div class="tier-label">Tier 1 · 最关心 <span class="muted">(已实现)</span></div>
          <div class="tier1-grid">
            <div class="cell"><div class="k">净PnL <span class="sub">net已实现</span></div>
              <div class="v" :class="signClass(netPnl)">{{ fmtSigned(netPnl) }}</div>
              <div class="pct" :class="signClass(netPnlPct)">{{ fmtSignedPct(netPnlPct) }}</div></div>
            <div class="cell"><div class="k">毛PnL <span class="sub">gross已实现</span></div>
              <div class="v" :class="signClass(grossPnl)">{{ fmtSigned(grossPnl) }}</div>
              <div class="pct" :class="signClass(grossPnlPct)">{{ fmtSignedPct(grossPnlPct) }}</div></div>
            <div class="cell"><div class="k">手续费 <span class="sub">毛−费=净</span></div>
              <div class="v fee">{{ fmtNum(feesRealized) }}</div></div>
            <div class="cell"><div class="k">净胜率</div><div class="v">{{ winRateText }}</div></div>
            <div class="cell"><div class="k">盈亏比</div><div class="v">{{ profitFactorText }}</div></div>
            <div class="cell"><div class="k">最大回撤 <span class="sub">net equity</span></div>
              <div class="v neg">{{ fmtNum(mdd) }}%</div></div>
          </div>
          <div class="tier2">
            持仓周期 <b>{{ summary.episodes }}</b>
            · 胜负 <b class="pos">{{ summary.wins }}</b>/<b class="neg">{{ summary.losses }}</b>
            · 最佳 <b :class="signClass(summary.best)">{{ fmtSigned(summary.best) }}</b>
            · 最差 <b :class="signClass(summary.worst)">{{ fmtSigned(summary.worst) }}</b>
            · 初始 {{ fmtNum(initial) }}
          </div>
        </div>
      </div>

      <div class="trades-fold">
        <n-button text size="small" @click="showTrades = !showTrades">
          {{ showTrades ? "交易历程 ▾" : `交易历程（${summary.episodes} 笔 · 净 ${fmtSigned(netPnl)}）▸` }}
        </n-button>
        <TradesTable v-if="showTrades" :trades="perf.trades" />
      </div>
    </template>
  </div>
</template>

<style scoped>
/* §1③：底部抽屉与上方 stream 明确分隔——上投影制造"浮起"感（覆盖 .ob-card 默认下投影），
   折叠条微染区别于白内容区。四边框继承全局 .ob-card（§1 所有卡面统一带边，有意为之，非仅 border-top）。 */
.perf-bar { box-shadow: 0 -3px 10px rgba(0, 0, 0, 0.08); }
.perf-bar:not(.expanded) { background: var(--ob-block-bg); }
.perf-bar.expanded { max-height: 55vh; overflow-y: auto; padding: 8px 16px; }

.collapsed-bar { display: flex; align-items: center; gap: 10px; padding: 9px 16px; font-size: 13px; cursor: pointer; flex-wrap: wrap; }
.lead { font-weight: 600; }   /* 文字色继承（--ob-text 未定义，与现有非 muted 文字一致，review minor 1）*/
.dot { color: var(--ob-border); }
.muted { color: var(--ob-text-muted); font-size: 11px; }
.expand-hint { margin-left: auto; color: var(--ob-text-muted); font-size: 12px; }
.held-box { border: 1px dashed var(--ob-warn); border-radius: 4px; padding: 0 6px; }

.exp-head { display: flex; align-items: center; gap: 12px; padding: 4px 0 8px; cursor: pointer; }
.caveat { color: var(--ob-text-muted); font-size: 11px; }
/* ④：与折叠态 .expand-hint「点击展开 ▴」对称的折叠提示 */
.collapse-hint { margin-left: auto; color: var(--ob-text-muted); font-size: 12px; }

.held-bar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; font-size: 13px;
  margin-bottom: 10px; padding: 8px 12px; border-radius: 6px;
  background: var(--ob-warn-soft); border: 1px solid var(--ob-warn); color: var(--ob-warn); }
.held-title { font-weight: 600; }
.side-tag { padding: 1px 6px; border-radius: 3px; }
.side-tag.long { color: var(--ob-pos); }
.side-tag.short { color: var(--ob-neg); }

.exp-grid { display: grid; grid-template-columns: 1.15fr 1fr; gap: 16px; }
.curve { min-width: 0; }
.caliper { font-size: 12px; color: var(--ob-text-muted); margin-bottom: 4px; }
.tier-label { font-size: 11px; color: var(--ob-text-muted); margin-bottom: 6px; }
.tier1-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px 14px; }
.cell .k { font-size: 10px; color: var(--ob-text-muted); }
.cell .sub { font-size: 9px; }
.cell .v { font-size: 16px; font-weight: 700; }
.cell .pct { font-size: 10px; }
.tier2 { font-size: 12px; margin-top: 10px; border-top: 1px dashed var(--ob-border); padding-top: 8px; }

.price-section { margin-bottom: 12px; }
.trades-fold { margin-top: 8px; }
.pos { color: var(--ob-pos); }
.neg { color: var(--ob-neg); }
.fee { color: var(--ob-warn); }
</style>
