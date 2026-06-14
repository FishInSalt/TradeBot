<script setup lang="ts">
import { computed, ref } from "vue";
import { NDescriptions, NDescriptionsItem } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";

const store = useSessionsStore();
const d = computed(() => store.detail);
const promptOpen = ref(false);
</script>

<template>
  <div v-if="d" class="session-meta-wrap ob-card">
    <n-descriptions :column="5" size="small" label-placement="left" class="session-meta" bordered>
      <n-descriptions-item label="Symbol">{{ d.symbol }}</n-descriptions-item>
      <n-descriptions-item label="周期">{{ d.timeframe }}</n-descriptions-item>
      <n-descriptions-item label="调度间隔">{{ d.scheduler_interval_min }}min</n-descriptions-item>
      <n-descriptions-item label="初始余额">{{ d.initial_balance }}</n-descriptions-item>
      <n-descriptions-item label="Token 预算">{{ d.token_budget }}</n-descriptions-item>
    </n-descriptions>
    <section v-if="d.system_prompt" class="sysprompt">
      <span class="sysprompt-toggle clickable" @click="promptOpen = !promptOpen">
        System Prompt（persona，会话固定）{{ promptOpen ? "▾" : "▸" }}
      </span>
      <pre v-if="promptOpen" class="sysprompt-text">{{ d.system_prompt }}</pre>
    </section>
  </div>
</template>

<style scoped>
.session-meta-wrap { padding: 6px 16px; }
.session-meta { margin-bottom: 6px; }
.sysprompt-toggle { cursor: pointer; user-select: none; font-size: 12px; color: var(--ob-text-muted); }
.clickable { cursor: pointer; user-select: none; }
.sysprompt-text { white-space: pre-wrap; word-break: break-word; background: var(--ob-block-bg); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 6px 0 0; max-height: 320px; overflow-y: auto; }
</style>
