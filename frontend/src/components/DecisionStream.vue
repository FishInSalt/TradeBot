<script setup lang="ts">
import { computed } from "vue";
import { NCollapse, NCollapseItem } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import CycleRowHeader from "@/components/CycleRowHeader.vue";
import CycleDetailPanel from "@/components/CycleDetailPanel.vue";

const store = useSessionsStore();
const cycles = computed(() => store.cycles);

// 受控：展开态唯一来源 store.expandedCycleIds（多展开，无 accordion）。naive 给全量数组，
// 仅做 number 归一（name 绑数字 id），交由 store.setExpandedCycles 做新增懒加载 + 移除保留。
function onUpdate(names: Array<string | number>) {
  void store.setExpandedCycles(names.map(Number));
}

const detailFor = (id: number) => store.cycleDetails.get(id);
</script>

<template>
  <div class="decision-stream ob-card">
    <n-collapse :expanded-names="store.expandedCycleIds" @update:expanded-names="onUpdate">
      <n-collapse-item v-for="c in cycles" :key="c.id" :name="c.id">
        <template #header><CycleRowHeader :cycle="c" :expanded="store.expandedCycleIds.includes(c.id)" /></template>
        <CycleDetailPanel v-if="detailFor(c.id)" :detail="detailFor(c.id)!" />
        <div v-else class="loading">加载详情…</div>
      </n-collapse-item>
    </n-collapse>
    <div v-if="!cycles.length" class="empty">暂无决策</div>
  </div>
</template>

<style scoped>
.decision-stream { padding: 4px 8px; }
/* §1②：相邻 cycle 行不糊在一起——清晰 hairline 分隔线 + 表头 hover 反馈（可点 affordance） */
.decision-stream :deep(.n-collapse-item:not(:first-child)) { border-top: 1px solid var(--ob-border); }
.decision-stream :deep(.n-collapse-item__header) { transition: background 0.12s; }
.decision-stream :deep(.n-collapse-item__header:hover) { background: var(--ob-block-bg); }
.loading { padding: 12px; color: var(--ob-text-muted); font-size: 13px; }
.empty { padding: 24px; text-align: center; color: var(--ob-text-muted); font-size: 13px; }
</style>
