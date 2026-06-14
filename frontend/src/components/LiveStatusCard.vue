<script setup lang="ts">
import { computed } from "vue";
import { NCard, NTag, NSpace } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import { fmtLocal } from "@/utils/time";

const store = useSessionsStore();
const live = computed(() => store.live);
const stalled = computed(() => store.pollFailCount >= 3);
</script>

<template>
  <n-card v-if="live" size="small" :bordered="false" class="status-card">
    <n-space align="center" :size="18">
      <n-tag :type="live.status === 'active' ? 'success' : 'warning'" size="small" round>{{ live.status }}</n-tag>
      <span class="muted">@ {{ fmtLocal(live.last_active_at) }}</span>
      <template v-if="live.position">
        <span class="label">持仓</span>
        <span class="pos" :class="live.position.side">{{ live.position.side }}</span>
        <span>{{ live.position.contracts }} @ {{ live.position.entry_price }} ×{{ live.position.leverage }}</span>
      </template>
      <span v-else class="muted">空仓</span>
      <span><span class="label">挂单</span> {{ live.open_orders.length }}</span>
      <span><span class="label">提醒</span> {{ live.active_alerts.length }}</span>
      <n-tag v-if="stalled" type="warning" size="small">⚠ 轮询中断</n-tag>
    </n-space>
  </n-card>
  <n-card v-else size="small" :bordered="false" class="status-card"><span class="muted">未选择会话</span></n-card>
</template>

<style scoped>
.status-card :deep(.n-card__content) { padding: 8px 16px; font-size: 13px; }
.label { color: var(--ob-text-muted); }
.muted { color: var(--ob-text-muted); }
.pos.long { color: var(--ob-pos); }
.pos.short { color: var(--ob-neg); }
</style>
