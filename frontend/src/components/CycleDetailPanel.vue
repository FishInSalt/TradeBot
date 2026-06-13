<script setup lang="ts">
import { computed, ref, h } from "vue";
import { NDataTable, NTag, NSpace } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { CycleDetail, ToolCallRow } from "@/api/client";
import JsonBlock from "@/components/JsonBlock.vue";

const props = defineProps<{ detail: CycleDetail }>();

const hasInjected = computed(() => {
  const e = props.detail.injected_events;
  if (e == null) return false;
  if (Array.isArray(e)) return e.length > 0;
  return true;
});
const toolsOpen = ref(false);
const slowest = computed(() => {
  const ds = props.detail.tool_calls.map((t) => t.duration_ms ?? 0);
  return ds.length ? Math.max(...ds) : 0;
});
const reasoningChars = computed(() => props.detail.reasoning?.length ?? 0);

const toolColumns: DataTableColumns<ToolCallRow> = [
  { title: "工具", key: "tool_name" },
  {
    title: "状态",
    key: "status",
    render: (r) =>
      h(NTag, { size: "small", type: r.status === "ok" ? "success" : "error" }, { default: () => (r.error_type ? `${r.status} · ${r.error_type}` : r.status) }),
  },
  { title: "耗时(ms)", key: "duration_ms" },
  { title: "入参", key: "args", render: (r) => h(JsonBlock, { value: r.args }) },
  {
    title: "结果",
    key: "result",
    render: (r) => (r.result == null ? h("span", { class: "seam" }, "结果未捕获") : h(JsonBlock, { value: r.result })),
  },
];
</script>

<template>
  <div class="cycle-detail">
    <!-- 1. 头部遥测 chips -->
    <n-space class="chips" :size="6">
      <n-tag size="small">tokens {{ detail.tokens_consumed }}</n-tag>
      <n-tag v-if="detail.input_tokens != null" size="small">in {{ detail.input_tokens }} / out {{ detail.output_tokens }}</n-tag>
      <n-tag v-if="detail.cache_hit_rate != null" size="small">cache {{ detail.cache_hit_rate.toFixed(0) }}%</n-tag>
      <n-tag v-if="detail.wall_time_ms != null" size="small">wall {{ detail.wall_time_ms }}ms</n-tag>
      <n-tag v-if="detail.model_id" size="small">{{ detail.model_id }}</n-tag>
    </n-space>

    <!-- 2. 触发上下文 -->
    <section><h4>触发上下文</h4><JsonBlock :value="detail.trigger_context" /></section>
    <!-- 3. 中途注入事件（仅非空渲染） -->
    <section v-if="hasInjected"><h4>中途注入事件</h4><JsonBlock :value="detail.injected_events" /></section>
    <!-- 4. 决策时状态 -->
    <section><h4>决策时状态</h4><JsonBlock :value="detail.state_snapshot" /></section>

    <!-- 5. 工具调用（感知），默认折叠 -->
    <section>
      <h4 class="tools-toggle clickable" @click="toolsOpen = !toolsOpen">
        工具调用（{{ detail.tool_calls.length }} 个 · 最慢 {{ slowest }}ms）{{ toolsOpen ? "▾" : "▸" }}
      </h4>
      <n-data-table v-if="toolsOpen" :columns="toolColumns" :data="detail.tool_calls" size="small" :bordered="false" />
    </section>

    <!-- 6. 推理（主角），固定高 + 内部滚 -->
    <section>
      <h4>推理 <span class="muted">（{{ reasoningChars }} 字符）</span></h4>
      <pre class="reasoning">{{ detail.reasoning || "—" }}</pre>
    </section>
    <!-- 7. 决策 -->
    <section><h4>决策</h4><pre class="decision">{{ detail.decision || "—" }}</pre></section>
  </div>
</template>

<style scoped>
.cycle-detail { padding: 8px 4px; }
.chips { margin-bottom: 10px; }
section { margin-bottom: 12px; }
h4 { margin: 0 0 4px; font-size: 13px; opacity: 0.85; }
h4.clickable { cursor: pointer; user-select: none; }
.muted { opacity: 0.5; font-weight: 400; }
:deep(.seam) { font-size: 12px; opacity: 0.5; font-style: italic; }
.reasoning { max-height: 360px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; background: rgba(0, 0, 0, 0.25); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; }
.decision { white-space: pre-wrap; word-break: break-word; background: rgba(96, 165, 250, 0.08); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; }
</style>
