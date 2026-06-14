<script setup lang="ts">
import { ref } from "vue";
import JsonBlock from "@/components/JsonBlock.vue";
import { fmtNum, fmtSigned } from "@/utils/format";
import { fmtUtcEpoch } from "@/utils/time";

interface InjectedEvent {
  event: unknown;
  after_tool?: string;
  offset_ms?: number | null;
  after_tool_call_id?: string | null;
  triggered_ago?: string | null;
  kind_label?: string;
}
const props = defineProps<{ inj: InjectedEvent }>();
const rawOpen = ref(false);

// 第二套更轻的展示渲染器：仅格式化，不做事件分类（开/平及类型已由后端 kind_label 决定）。
function e(): any { return (props.inj.event ?? {}) as any; }
function baseName(sym: string | undefined): string { return sym ? sym.split("/")[0] : ""; }
function sideLabel(s: string | undefined): string { return s === "long" ? "多" : s === "short" ? "空" : (s ?? "?"); }
function dirLabel(d: string | undefined): string { return d === "above" ? "上破" : d === "below" ? "下破" : (d ?? ""); }
function pnlClass(n: number | null | undefined): string { return n == null ? "" : n < 0 ? "neg" : "pos"; }
</script>

<template>
  <div class="injection-card">
    <div class="inj-head">
      <span class="step-icon">⚡</span>
      <span class="inj-title">{{ inj.kind_label || "触发事件注入" }}</span>
      <span v-if="inj.triggered_ago" class="inj-age">{{ inj.triggered_ago }}</span>
    </div>
    <div class="inj-sum">
      <template v-if="e().type === 'percentage_alert'">
        {{ baseName(e().symbol) }} {{ e().window_minutes }}min 窗口
        <span :class="pnlClass(e().change_pct)">{{ fmtSigned(e().change_pct) }}%</span>
        · {{ fmtNum(e().reference_price) }} → {{ fmtNum(e().current_price) }}
      </template>
      <template v-else-if="e().type === 'fill'">
        {{ sideLabel(e().position_side) }} {{ fmtNum(e().amount) }} 张 @{{ fmtNum(e().fill_price) }}
        <template v-if="e().pnl != null"> · 盈亏 <span :class="pnlClass(e().pnl)">{{ fmtSigned(e().pnl) }}</span></template>
        <template v-if="e().fee != null"> · 手续费 {{ fmtNum(e().fee) }} USDT</template>
      </template>
      <template v-else-if="e().type === 'price_level_alert'">
        {{ dirLabel(e().direction) }} @{{ fmtNum(e().target_price) }}（现价 {{ fmtNum(e().current_price) }}）
        <div v-if="e().reasoning" class="inj-reason">{{ e().reasoning }}</div>
      </template>
    </div>
    <div v-if="e().timestamp != null" class="inj-meta">触发于 {{ fmtUtcEpoch(e().timestamp) }}</div>
    <div class="inj-raw-toggle clickable" @click="rawOpen = !rawOpen">原始 JSON {{ rawOpen ? "▾" : "▸" }}</div>
    <JsonBlock v-if="rawOpen" :value="inj.event" />
  </div>
</template>

<style scoped>
/* warn-soft 琥珀底上 muted 仅 ~4.34 → 文字用 --ob-warn 达 AA（沿用 ReactTimeline 既有处方）。 */
.injection-card { margin: 6px 0 6px 18px; padding: 6px 9px; background: var(--ob-warn-soft); border-radius: 4px; font-size: 12px; }
.inj-head { display: flex; align-items: center; gap: 7px; }
.inj-title { font-weight: 600; }
.inj-age { margin-left: auto; font-size: 11px; color: var(--ob-warn); border: 1px solid var(--ob-warn); border-radius: 4px; padding: 0 6px; }
.inj-sum { margin: 4px 0 0 22px; }
.inj-reason { color: var(--ob-text-muted); font-style: italic; margin-top: 2px; }
.inj-meta { margin: 3px 0 0 22px; font-size: 11px; color: var(--ob-warn); }
.inj-raw-toggle { margin: 4px 0 0 22px; font-size: 11px; color: var(--ob-warn); cursor: pointer; user-select: none; }
.step-icon { flex: 0 0 auto; }
.neg { color: var(--ob-neg); font-weight: 600; }
.pos { color: var(--ob-pos); font-weight: 600; }
.clickable { cursor: pointer; user-select: none; }
</style>
