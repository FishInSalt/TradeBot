<script setup lang="ts">
import { computed } from "vue";
import { NCollapse, NCollapseItem } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import CycleRowHeader from "@/components/CycleRowHeader.vue";
import CycleDetailPanel from "@/components/CycleDetailPanel.vue";

const store = useSessionsStore();
const cycles = computed(() => store.cycles);

// 单向：从 store 派生展开项；accordion 模式下至多一项
const expandedNames = computed<number[]>(() =>
  store.expandedCycleId != null ? [store.expandedCycleId] : [],
);

function onUpdate(names: Array<string | number>) {
  const next = names.length ? Number(names[0]) : null;
  if (next == null) {
    // 关闭当前项：expandCycle 同 id toggle 成 null
    if (store.expandedCycleId != null) void store.expandCycle(store.expandedCycleId);
  } else if (next !== store.expandedCycleId) {
    void store.expandCycle(next); // 打开：设 id + 懒加载
  }
}

const detailFor = (id: number) => store.cycleDetails.get(id);
</script>

<template>
  <div class="decision-stream">
    <n-collapse accordion :expanded-names="expandedNames" @update:expanded-names="onUpdate">
      <n-collapse-item v-for="c in cycles" :key="c.id" :name="c.id">
        <template #header><CycleRowHeader :cycle="c" /></template>
        <CycleDetailPanel v-if="detailFor(c.id)" :detail="detailFor(c.id)!" />
        <div v-else class="loading">加载详情…</div>
      </n-collapse-item>
    </n-collapse>
    <div v-if="!cycles.length" class="empty">暂无决策</div>
  </div>
</template>

<style scoped>
.decision-stream { padding: 4px 8px; }
.loading { padding: 12px; opacity: 0.5; font-size: 13px; }
.empty { padding: 24px; text-align: center; opacity: 0.5; font-size: 13px; }
</style>
