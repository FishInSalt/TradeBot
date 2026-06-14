<script setup lang="ts">
import { computed } from "vue";
import type { CycleRow } from "@/api/client";
import { NTag } from "naive-ui";
import { fmtLocal } from "@/utils/time";
import { fmtTokens, fmtDuration } from "@/utils/format";

const props = defineProps<{ cycle: CycleRow }>();

const headText = computed(() => {
  const p = props.cycle.position;
  if (!p) return "空仓";
  const d = p.side === "long" ? "多" : p.side === "short" ? "空" : p.side;
  const ep = p.entry_price != null ? ` @${Math.round(p.entry_price)}` : "";
  return `${d} ${p.contracts}张${ep}`;
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
  <div class="cycle-head" :class="{ keyrow: cycle.key_events.length > 0 }">
    <span class="time">{{ fmtLocal(cycle.created_at) }}</span>
    <n-tag size="small" :bordered="false">{{ cycle.triggered_by }}</n-tag>
    <span class="seg head-pos"><span class="seg-label">开始:</span> {{ headText }}</span>
    <span class="seg end-events">
      <span class="seg-label">本轮:</span>
      <template v-if="cycle.key_events.length">
        <n-tag v-for="(e, i) in cycle.key_events" :key="i" size="tiny" :type="chipType(e.kind)" :bordered="false">
          {{ e.label }}
        </n-tag>
      </template>
      <span v-else class="muted">（无交易）</span>
    </span>
    <n-tag size="small" :type="cycle.execution_status === 'ok' ? 'default' : 'error'" :bordered="false">
      {{ cycle.execution_status }}
    </n-tag>
    <span class="tele">{{ fmtTokens(cycle.tokens_consumed) }}tok · {{ fmtDuration(cycle.wall_time_ms) }}</span>
  </div>
</template>

<style scoped>
.cycle-head { display: flex; align-items: center; gap: 8px; width: 100%; font-size: 13px; padding-left: 6px; border-left: 3px solid transparent; }
.cycle-head.keyrow { border-left-color: #60a5fa; }   /* 关键事件锚点高亮 */
.time { opacity: 0.7; white-space: nowrap; }
.seg { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; overflow: hidden; }
.seg-label { opacity: 0.5; font-size: 11px; }
.head-pos { min-width: 120px; }
.end-events { flex: 1; flex-wrap: wrap; }
.tele { font-size: 11px; opacity: 0.5; white-space: nowrap; }
.muted { opacity: 0.45; }
</style>
