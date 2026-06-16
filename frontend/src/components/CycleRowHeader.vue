<script setup lang="ts">
import { computed } from "vue";
import type { CycleRow } from "@/api/client";
import { NTag } from "naive-ui";
import { fmtUtc, fmtUtcTime } from "@/utils/time";
import { fmtTokens, fmtDuration, fmtGap } from "@/utils/format";

const props = defineProps<{ cycle: CycleRow; expanded?: boolean }>();

const headText = computed(() => {
  const p = props.cycle.position;
  if (!p) return "空仓";
  const d = p.side === "long" ? "多" : p.side === "short" ? "空" : p.side;
  const ep = p.entry_price != null ? ` @${Math.round(p.entry_price)}` : "";
  return `${d} ${p.contracts}张${ep}`;
});

// C2: created_at 是 cycle 结束时刻；开始 ≈ created_at − wall_time_ms（数据核实）。
// wall_time_ms=null（forensic）→ 无法推开始，startAt=null（模板只渲结束单点）。
const startAt = computed(() => {
  const w = props.cycle.wall_time_ms;
  if (w == null) return null;
  const endMs = new Date(props.cycle.created_at).getTime();
  if (Number.isNaN(endMs)) return null;   // F2：坏 created_at → 不 toISOString(NaN) 抛 RangeError，退单点（fmtUtc 再降级占位）
  return new Date(endMs - w).toISOString();
});

// kind → chip 配色：开=绿 / 平=红 / 挂单=蓝 / 反手=黄（spec §3.4）
function chipType(kind: string): "success" | "error" | "info" | "warning" | "default" {
  if (kind === "open" || kind === "add" || kind === "fill_open") return "success";
  if (kind === "close" || kind === "fill_close" || kind === "fill_partial") return "error";
  if (kind === "limit_order") return "info";
  if (kind === "flip") return "warning";
  return "default";
}
</script>

<template>
  <div class="cycle-head" :class="{ keyrow: cycle.key_events.length > 0, expanded }">
    <span class="seq">#{{ cycle.seq }}</span>
    <span class="time">
      <template v-if="startAt">{{ fmtUtc(startAt) }} → {{ fmtUtcTime(cycle.created_at) }}</template>
      <template v-else>{{ fmtUtc(cycle.created_at) }}</template>
    </span>
    <span v-if="cycle.gap_since_prev_ms != null" class="gap">· 间隔 {{ fmtGap(cycle.gap_since_prev_ms) }}</span>
    <n-tag size="small" :bordered="false">{{ cycle.triggered_by }}</n-tag>
    <span class="seg head-pos"><span class="seg-label">开始:</span> {{ headText }}</span>
    <span class="seg end-events">
      <span class="seg-label">本轮:</span>
      <template v-if="cycle.key_events.length">
        <n-tag v-for="(e, i) in cycle.key_events" :key="i" size="tiny" :type="chipType(e.kind)"
               :bordered="false" :class="{ 'mid-cycle': e.mid_cycle }">
          {{ e.label }}
        </n-tag>
      </template>
      <span v-else class="muted">（无交易）</span>
    </span>
    <n-tag size="small" :type="cycle.execution_status === 'ok' ? 'default' : 'error'" :bordered="false">
      {{ cycle.execution_status }}
    </n-tag>
    <span class="tele">{{ fmtTokens(cycle.tokens_consumed) }} tok · {{ fmtDuration(cycle.wall_time_ms) }}</span>
  </div>
</template>

<style scoped>
.cycle-head { display: flex; align-items: center; gap: 8px; width: 100%; font-size: 13px; padding-left: 6px; border-left: 3px solid transparent; }
.cycle-head.keyrow { border-left-color: var(--ob-accent); }   /* 关键事件锚点高亮 */
/* §1②：所有展开行整体高亮——仅淡蓝底（蓝竖带专给关键事件 keyrow，避免两语义同色混淆）；
   多展开下可多条同时高亮，配 naive ▾ 箭头 + 下方灰凹陷详情区共同指明展开。 */
.cycle-head.expanded { background: var(--ob-row-active); }
.seq { color: var(--ob-text-muted); background: var(--ob-block-bg); border-radius: 4px; padding: 0 5px; font-size: 11px; white-space: nowrap; }
.time { opacity: 0.7; white-space: nowrap; }
.gap { font-size: 11px; color: var(--ob-text-muted); white-space: nowrap; }
.seg { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; overflow: hidden; }
.seg-label { color: var(--ob-text-muted); font-size: 11px; }
.head-pos { min-width: 120px; }
.end-events { flex: 1; flex-wrap: wrap; }
.tele { font-size: 11px; color: var(--ob-text-muted); white-space: nowrap; }
.muted { color: var(--ob-text-muted); }
/* 运行中注入的 fill（mid_cycle）：克制虚线描边区分——「这笔成交发生在 cycle 运行途中、
   agent 未必主动反应」vs 触发本轮的实心 chip。色继承 chip type 文本色（currentColor）。 */
.end-events :deep(.mid-cycle) { border: 1px dashed currentColor; }
</style>
