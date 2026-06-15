<script setup lang="ts">
import { computed, ref, h } from "vue";
import { NDataTable, NTag, NSpace } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { CycleDetail, ToolCallRow } from "@/api/client";
import JsonBlock from "@/components/JsonBlock.vue";
import ReactTimeline from "@/components/ReactTimeline.vue";
import InjectionCard from "@/components/InjectionCard.vue";
import { fmtTokens, fmtDuration, fmtNum, fmtSigned } from "@/utils/format";
import { fmtUtc } from "@/utils/time";

const props = defineProps<{ detail: CycleDetail }>();

const hasTimeline = computed(() => Array.isArray(props.detail.react_steps) && props.detail.react_steps.length > 0);
const hasInjected = computed(() => {
  const e = props.detail.injected_events;
  return Array.isArray(e) ? e.length > 0 : e != null;
});
// 回退路径注入渲染：list（富化后主流形态）→ 逐条 InjectionCard 人读摘要（D 对 react_steps=null
// 的 legacy/forensic cycle 同样生效，当前所有 sim 均属此路径）；非 list legacy 形态保留 JsonBlock 兜底。
const injectedFlat = computed(() =>
  Array.isArray(props.detail.injected_events) ? props.detail.injected_events : null,
);
const contextOpen = ref(false);   // A3：唤醒上下文默认折叠，按需展开

// 与 ReactTimeline.statusType 同口径（biz_error→warning），避免同数据两视图配色不一致
function statusType(s: string) {
  return s === "ok" ? "success" : s === "biz_error" ? "warning" : "error";
}

// 回退扁平视图：仅 react_steps 缺失（legacy/forensic）时用
const toolsOpen = ref(false);
const snapshotOpen = ref(true);
// state_snapshot 可能是 dict|list|str（放宽形态）；仅 dict 渲染结构化详情，内部键剔除
const snapshot = computed(() => {
  const s = props.detail.state_snapshot;
  return s && typeof s === "object" && !Array.isArray(s) ? (s as Record<string, any>) : null;
});
function pnlClass(n: number | null | undefined) { return n == null ? "" : n < 0 ? "neg" : "pos"; }
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
  { title: "耗时", key: "duration_ms", render: (r) => fmtDuration(r.duration_ms) },
  { title: "入参", key: "args", render: (r) => h(JsonBlock, { value: r.args }) },
  { title: "结果", key: "result",
    render: (r) => (r.result == null ? h("span", { class: "seam" }, "结果未捕获") : h(JsonBlock, { value: r.result })) },
];
</script>

<template>
  <div class="cycle-detail">
    <!-- 1. 头部遥测 chips（C3：去掉 tokens 总片 + wall 片，已在 header 显示；保留拆解/cache/llm/status/model） -->
    <n-space class="chips" :size="6">
      <n-tag v-if="detail.input_tokens != null" size="small">输入 {{ fmtTokens(detail.input_tokens) }} / 输出 {{ fmtTokens(detail.output_tokens) }} tok</n-tag>
      <n-tag v-if="detail.cache_hit_rate != null" size="small">cache {{ detail.cache_hit_rate.toFixed(0) }}%</n-tag>
      <n-tag v-if="detail.llm_call_ms != null" size="small">llm {{ fmtDuration(detail.llm_call_ms) }}</n-tag>
      <n-tag size="small" :type="detail.execution_status === 'ok' ? 'default' : 'error'">{{ detail.execution_status }}</n-tag>
      <n-tag v-if="detail.model_id" size="small">{{ detail.model_id }}</n-tag>
    </n-space>

    <!-- 2. 唤醒时状态（默认展开，置顶；state_snapshot 是唤醒瞬间态） -->
    <section v-if="snapshot" class="ob-card">
      <h4 class="snapshot-toggle clickable" @click="snapshotOpen = !snapshotOpen">
        唤醒时状态 {{ snapshotOpen ? "▾" : "▸" }}
      </h4>
      <div v-if="snapshotOpen" class="snapshot">
        <template v-if="snapshot.position">
          <span class="snap-k">持仓</span>
          <span>
            <span class="dir" :class="snapshot.position.side">{{ snapshot.position.side === 'long' ? '多' : '空' }}</span>
            {{ fmtNum(snapshot.position.contracts) }} 张 · 入场 {{ fmtNum(snapshot.position.entry_price) }} · 杠杆 {{ snapshot.position.leverage }}× · 浮盈
            <span :class="pnlClass(snapshot.position.unrealized_pnl)">{{ fmtSigned(snapshot.position.unrealized_pnl) }} USDT</span>
          </span>
        </template>
        <template v-else><span class="snap-k">持仓</span><span class="muted">空仓</span></template>
        <template v-if="snapshot.balance">
          <span class="snap-k">余额</span>
          <span class="bal">
            <span class="seg"><span class="sl">总额</span><span class="sv">{{ fmtNum(snapshot.balance.total_usdt) }}</span></span>
            <span class="seg"><span class="sl">可用</span><span class="sv">{{ fmtNum(snapshot.balance.free_usdt) }}</span></span>
            <span class="seg"><span class="sl">占用</span><span class="sv">{{ fmtNum(snapshot.balance.used_usdt) }}</span></span>
            <span class="unit">USDT</span>
          </span>
        </template>
        <template v-if="snapshot.market">
          <span class="snap-k">现价</span>
          <span>{{ fmtNum(snapshot.market.ticker_last) }} <span class="muted">@ {{ fmtUtc(snapshot.market.fetched_at) }}</span></span>
        </template>
        <template v-if="snapshot.pending_orders && snapshot.pending_orders.length">
          <span class="snap-k">挂单</span>
          <span><span v-for="(o, i) in snapshot.pending_orders" :key="i" class="snap-item">{{ o.order_type }} {{ o.side }} @{{ fmtNum(o.trigger_price ?? o.price) }} ×{{ o.amount }}</span></span>
        </template>
        <template v-if="(snapshot.active_alerts && snapshot.active_alerts.length) || snapshot.volatility_alert">
          <span class="snap-k">告警</span>
          <span class="alerts">
            <span v-if="snapshot.active_alerts && snapshot.active_alerts.length" class="alert-grp">
              <span class="muted alert-lbl">价格</span>
              <span v-for="(a, i) in snapshot.active_alerts" :key="i" class="snap-item"><span class="dir-glyph">{{ a.direction === 'below' ? '↓' : '↑' }}</span> {{ a.direction }} @{{ fmtNum(a.price) }}<span v-if="a.reasoning" class="muted"> · {{ a.reasoning }}</span></span>
            </span>
            <span v-if="snapshot.volatility_alert" class="alert-grp">
              <span class="muted alert-lbl">波动</span>
              <span class="snap-item">±{{ fmtNum(snapshot.volatility_alert.threshold_pct) }}% / {{ snapshot.volatility_alert.window_minutes }}min</span>
            </span>
          </span>
        </template>
      </div>
    </section>

    <!-- 3. 唤醒上下文（原文版，默认折叠 A3；null 不渲染） -->
    <section v-if="detail.user_prompt_snapshot" class="ob-card">
      <h4 class="context-toggle clickable" @click="contextOpen = !contextOpen">唤醒上下文 {{ contextOpen ? "▾" : "▸" }}</h4>
      <pre v-if="contextOpen" class="context">{{ detail.user_prompt_snapshot }}</pre>
    </section>

    <!-- 4. ReAct 时间线（主角）或扁平回退 -->
    <section class="ob-card">
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
          工具调用（{{ detail.tool_calls.length }} 个 · 最慢 {{ fmtDuration(slowest) }}）{{ toolsOpen ? "▾" : "▸" }}
        </h5>
        <n-data-table v-if="toolsOpen" :columns="toolColumns" :data="detail.tool_calls" size="small" :bordered="false" />
        <!-- 注入事件：legacy cycle 可能 react_steps=null 而 injected_events 非空（注入 iter 晚于无骨架行），
             回退分支须渲染，否则其注入在 WebUI 彻底丢失（恢复旧 CycleDetailPanel 行为） -->
        <div v-if="hasInjected" class="inj-fallback">
          <h5>中途注入事件</h5>
          <template v-if="injectedFlat">
            <InjectionCard v-for="(inj, i) in injectedFlat" :key="i" :inj="(inj as any)" />
          </template>
          <JsonBlock v-else :value="detail.injected_events" />
        </div>
        <pre class="reasoning">{{ detail.reasoning || "—" }}</pre>
      </div>
    </section>

    <!-- 5. 决策 -->
    <section class="ob-card"><h4>决策</h4><pre class="decision">{{ detail.decision || "—" }}</pre></section>
  </div>
</template>

<style scoped>
/* §1①：展开详情做成「内嵌、属于该行」的凹陷区——灰底 + 左 accent 边；
   内层 section.ob-card（白 + 全局 hairline）在此灰底上获得清晰边界，破白叠白。 */
.cycle-detail { padding: 10px 12px; background: var(--ob-block-bg); border-left: 3px solid var(--ob-accent); border-radius: 0 6px 6px 0; }
.chips { margin-bottom: 10px; }
section { margin-bottom: 12px; }
h4 { margin: 0 0 4px; font-size: 13px; opacity: 0.85; }
h5 { margin: 6px 0 4px; font-size: 12px; opacity: 0.8; }
.clickable { cursor: pointer; user-select: none; }
.context { white-space: pre-wrap; word-break: break-word; background: var(--ob-block-bg); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; max-height: 240px; overflow-y: auto; }
.reasoning { max-height: 360px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; background: var(--ob-block-bg); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 8px 0 0; }
.decision { white-space: pre-wrap; word-break: break-word; background: var(--ob-accent-soft); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; }
:deep(.seam) { font-size: 12px; color: var(--ob-text-muted); font-style: italic; }
.seam { font-size: 12px; color: var(--ob-text-muted); font-style: italic; }
.inj-fallback { margin-top: 8px; }
.snapshot { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 12px; }
.snap-k { color: var(--ob-text-muted); }
.snap-item { display: inline-block; margin-right: 10px; }
.bal { display: inline-flex; gap: 18px; align-items: baseline; flex-wrap: wrap; }
.seg { display: inline-flex; gap: 5px; align-items: baseline; }
.seg .sl { color: var(--ob-text-muted); }
.seg .sv { font-variant-numeric: tabular-nums; }
.unit { color: var(--ob-text-muted); }
/* 告警值单元格：价格组 / 波动组纵向堆叠（组间竖排，波动落价格下方） */
.alerts { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
/* 组内竖排：label 在上、各告警条逐条独占一行（杠杆在容器方向，非 .snap-item——后者与挂单共用） */
.alert-grp { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.alert-lbl { font-size: 11px; }
.dir-glyph { display: inline-block; width: 1em; text-align: center; color: var(--ob-text-muted); }
.muted { color: var(--ob-text-muted); }
.dir.long, .pos { color: var(--ob-pos); font-weight: 600; }
.dir.short, .neg { color: var(--ob-neg); font-weight: 600; }
</style>
