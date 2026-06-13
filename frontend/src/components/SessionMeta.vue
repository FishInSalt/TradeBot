<script setup lang="ts">
import { computed } from "vue";
import { NDescriptions, NDescriptionsItem } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";

const store = useSessionsStore();
const d = computed(() => store.detail);
</script>

<template>
  <n-descriptions v-if="d" :column="5" size="small" label-placement="left" class="session-meta" bordered>
    <n-descriptions-item label="Symbol">{{ d.symbol }}</n-descriptions-item>
    <n-descriptions-item label="周期">{{ d.timeframe }}</n-descriptions-item>
    <n-descriptions-item label="调度间隔">{{ d.scheduler_interval_min }}min</n-descriptions-item>
    <n-descriptions-item label="初始余额">{{ d.initial_balance }}</n-descriptions-item>
    <n-descriptions-item label="Token 预算">{{ d.token_budget }}</n-descriptions-item>
  </n-descriptions>
</template>

<style scoped>
.session-meta { padding: 6px 16px; }
</style>
