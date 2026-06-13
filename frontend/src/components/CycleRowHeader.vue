<script setup lang="ts">
import type { CycleRow } from "@/api/client";
import { NTag } from "naive-ui";
import { fmtLocal } from "@/utils/time";

defineProps<{ cycle: CycleRow }>();
</script>

<template>
  <div class="cycle-head">
    <span class="time">{{ fmtLocal(cycle.created_at) }}</span>
    <n-tag size="small" :bordered="false">{{ cycle.triggered_by }}</n-tag>
    <span class="head">{{ cycle.decision_head || "—" }}</span>
    <n-tag size="small" :type="cycle.execution_status === 'ok' ? 'default' : 'error'" :bordered="false">
      {{ cycle.execution_status }}
    </n-tag>
    <span class="tele">{{ cycle.tokens_consumed }}tok · {{ cycle.wall_time_ms ?? "?" }}ms</span>
  </div>
</template>

<style scoped>
.cycle-head { display: flex; align-items: center; gap: 10px; width: 100%; font-size: 13px; }
.time { opacity: 0.7; white-space: nowrap; }
.head { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tele { font-size: 11px; opacity: 0.5; white-space: nowrap; }
</style>
