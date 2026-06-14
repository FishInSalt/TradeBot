<script setup lang="ts">
import { computed } from "vue";
import { NTag, NSpace } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import { fmtUtc } from "@/utils/time";

const store = useSessionsStore();
const live = computed(() => store.live);
const stalled = computed(() => store.pollFailCount >= 3);
</script>

<template>
  <div v-if="live" class="status-card">
    <n-space align="center" :size="18">
      <n-tag :type="live.status === 'active' ? 'success' : 'warning'" size="small" round>{{ live.status }}</n-tag>
      <span class="muted">@ {{ fmtUtc(live.last_active_at) }}</span>
      <template v-if="live.position">
        <span class="label">持仓</span>
        <span class="pos" :class="live.position.side">{{ live.position.side }}</span>
        <span>{{ live.position.contracts }} @ {{ live.position.entry_price }} ×{{ live.position.leverage }}</span>
      </template>
      <span v-else class="muted">空仓</span>
      <span><span class="label">挂单</span> {{ live.open_orders.length }}</span>
      <n-tag v-if="stalled" type="warning" size="small">⚠ 轮询中断</n-tag>
    </n-space>
  </div>
  <div v-else class="status-card"><span class="muted">未选择会话</span></div>
</template>

<style scoped>
.status-card { font-size: 13px; }
.label { color: var(--ob-text-muted); }
.muted { color: var(--ob-text-muted); }
.pos.long { color: var(--ob-pos); }
.pos.short { color: var(--ob-neg); }
</style>
