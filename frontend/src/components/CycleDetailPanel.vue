<script setup lang="ts">
import { computed, ref, h } from "vue";
import { NDataTable, NTag, NSpace } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { CycleDetail, ToolCallRow } from "@/api/client";
import JsonBlock from "@/components/JsonBlock.vue";
import ReactTimeline from "@/components/ReactTimeline.vue";
import { fmtTokens, fmtDuration } from "@/utils/format";

const props = defineProps<{ detail: CycleDetail }>();

const hasTimeline = computed(() => Array.isArray(props.detail.react_steps) && props.detail.react_steps.length > 0);
const hasInjected = computed(() => {
  const e = props.detail.injected_events;
  return Array.isArray(e) ? e.length > 0 : e != null;
});
const contextOpen = ref(true);

// 与 ReactTimeline.statusType 同口径（biz_error→warning），避免同数据两视图配色不一致
function statusType(s: string) {
  return s === "ok" ? "success" : s === "biz_error" ? "warning" : "error";
}

// 回退扁平视图：仅 react_steps 缺失（legacy/forensic）时用
const toolsOpen = ref(false);
const snapshotOpen = ref(false);
// state_snapshot 可能是 dict|list|str（放宽形态）；仅 dict 渲染结构化详情，内部键剔除
const snapshot = computed(() => {
  const s = props.detail.state_snapshot;
  return s && typeof s === "object" && !Array.isArray(s) ? (s as Record<string, any>) : null;
});
const slowest = computed(() => {
  const ds = props.detail.tool_calls.map((t) => t.duration_ms ?? 0);
  return ds.length ? Math.max(...ds) : 0;
});
const toolColumns: DataTableColumns<ToolCallRow> = [
  { title: "工具", key: "tool_name" },
  {
    title: "状态", key: "status",
    render: (r) => h(NTag, { size: "small", type: statusType(r.status) },
      { default: () => (r.error_type ? `${r.status} · ${r.error_type}` : r.status) }),
  },
  { title: "耗时(ms)", key: "duration_ms" },
  { title: "入参", key: "args", render: (r) => h(JsonBlock, { value: r.args }) },
  { title: "结果", key: "result",
    render: (r) => (r.result == null ? h("span", { class: "seam" }, "结果未捕获") : h(JsonBlock, { value: r.result })) },
];
</script>

<template>
  <div class="cycle-detail">
    <!-- 1. 头部遥测 chips -->
    <n-space class="chips" :size="6">
      <n-tag size="small">tokens {{ fmtTokens(detail.tokens_consumed) }}</n-tag>
      <n-tag v-if="detail.input_tokens != null" size="small">in {{ fmtTokens(detail.input_tokens) }} / out {{ fmtTokens(detail.output_tokens) }}</n-tag>
      <n-tag v-if="detail.cache_hit_rate != null" size="small">cache {{ detail.cache_hit_rate.toFixed(0) }}%</n-tag>
      <n-tag v-if="detail.wall_time_ms != null" size="small">wall {{ fmtDuration(detail.wall_time_ms) }}</n-tag>
      <n-tag v-if="detail.llm_call_ms != null" size="small">llm {{ fmtDuration(detail.llm_call_ms) }}</n-tag>
      <n-tag size="small" :type="detail.execution_status === 'ok' ? 'default' : 'error'">{{ detail.execution_status }}</n-tag>
      <n-tag v-if="detail.model_id" size="small">{{ detail.model_id }}</n-tag>
    </n-space>

    <!-- 2. 唤醒上下文（原文版，可折叠；null 不渲染） -->
    <section v-if="detail.user_prompt_snapshot">
      <h4 class="clickable" @click="contextOpen = !contextOpen">唤醒上下文 {{ contextOpen ? "▾" : "▸" }}</h4>
      <pre v-if="contextOpen" class="context">{{ detail.user_prompt_snapshot }}</pre>
    </section>

    <!-- 3. ReAct 时间线（主角）或扁平回退 -->
    <section>
      <h4>推理与行动过程</h4>
      <ReactTimeline
        v-if="hasTimeline"
        :steps="(detail.react_steps as any)"
        :tool-calls="detail.tool_calls"
        :injected-events="(detail.injected_events as any) ?? null"
      />
      <div v-else class="flat-fallback">
        <p class="seam">该 cycle 无交错时间线（历史 / 取证记录）。下方为扁平视图。</p>
        <h5 class="tools-toggle clickable" @click="toolsOpen = !toolsOpen">
          工具调用（{{ detail.tool_calls.length }} 个 · 最慢 {{ slowest }}ms）{{ toolsOpen ? "▾" : "▸" }}
        </h5>
        <n-data-table v-if="toolsOpen" :columns="toolColumns" :data="detail.tool_calls" size="small" :bordered="false" />
        <!-- 注入事件：legacy cycle 可能 react_steps=null 而 injected_events 非空（注入 iter 晚于无骨架行），
             回退分支须渲染，否则其注入在 WebUI 彻底丢失（恢复旧 CycleDetailPanel 行为） -->
        <div v-if="hasInjected" class="inj-fallback">
          <h5>中途注入事件</h5>
          <JsonBlock :value="detail.injected_events" />
        </div>
        <pre class="reasoning">{{ detail.reasoning || "—" }}</pre>
      </div>
    </section>

    <!-- 4. 状态快照（默认折叠，议题 4）；state_snapshot 是本轮开始态 -->
    <section v-if="snapshot">
      <h4 class="snapshot-toggle clickable" @click="snapshotOpen = !snapshotOpen">
        状态快照（开始态）{{ snapshotOpen ? "▾" : "▸" }}
      </h4>
      <div v-if="snapshotOpen" class="snapshot">
        <div class="snap-block" v-if="snapshot.position">
          <span class="snap-k">持仓</span>
          <span>{{ snapshot.position.side }} · {{ snapshot.position.contracts }}张 · 入场 {{ snapshot.position.entry_price }} · 杠杆 {{ snapshot.position.leverage }}x · 浮盈 {{ snapshot.position.unrealized_pnl }}</span>
        </div>
        <div class="snap-block" v-else><span class="snap-k">持仓</span><span class="muted">空仓</span></div>
        <div class="snap-block" v-if="snapshot.balance">
          <span class="snap-k">余额</span>
          <span>total {{ snapshot.balance.total_usdt }} · free {{ snapshot.balance.free_usdt }} · used {{ snapshot.balance.used_usdt }}</span>
        </div>
        <div class="snap-block" v-if="snapshot.market">
          <span class="snap-k">现价</span>
          <span>{{ snapshot.market.ticker_last }} <span class="muted">@ {{ snapshot.market.fetched_at }}</span></span>
        </div>
        <div class="snap-block" v-if="snapshot.pending_orders && snapshot.pending_orders.length">
          <span class="snap-k">挂单</span>
          <span>
            <span v-for="(o, i) in snapshot.pending_orders" :key="i" class="snap-item">
              {{ o.order_type }} {{ o.side }} @{{ o.trigger_price ?? o.price }} ×{{ o.amount }}
            </span>
          </span>
        </div>
        <div class="snap-block" v-if="snapshot.active_alerts && snapshot.active_alerts.length">
          <span class="snap-k">告警</span>
          <span>
            <span v-for="(a, i) in snapshot.active_alerts" :key="i" class="snap-item">
              {{ a.direction }} @{{ a.price }}<span v-if="a.reasoning" class="muted"> · {{ a.reasoning }}</span>
            </span>
          </span>
        </div>
      </div>
    </section>

    <!-- 5. 决策 -->
    <section><h4>决策</h4><pre class="decision">{{ detail.decision || "—" }}</pre></section>
  </div>
</template>

<style scoped>
.cycle-detail { padding: 8px 4px; }
.chips { margin-bottom: 10px; }
section { margin-bottom: 12px; }
h4 { margin: 0 0 4px; font-size: 13px; opacity: 0.85; }
h5 { margin: 6px 0 4px; font-size: 12px; opacity: 0.8; }
.clickable { cursor: pointer; user-select: none; }
.context { white-space: pre-wrap; word-break: break-word; background: rgba(0,0,0,0.22); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; max-height: 240px; overflow-y: auto; }
.reasoning { max-height: 360px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; background: rgba(0, 0, 0, 0.25); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 8px 0 0; }
.decision { white-space: pre-wrap; word-break: break-word; background: rgba(96, 165, 250, 0.08); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; }
:deep(.seam) { font-size: 12px; opacity: 0.5; font-style: italic; }
.seam { font-size: 12px; opacity: 0.5; font-style: italic; }
.inj-fallback { margin-top: 8px; }
.snapshot { font-size: 12px; }
.snap-block { display: flex; gap: 8px; margin: 4px 0; line-height: 1.5; }
.snap-k { opacity: 0.55; min-width: 36px; flex: 0 0 auto; }
.snap-item { display: inline-block; margin-right: 10px; }
.muted { opacity: 0.55; }
</style>
