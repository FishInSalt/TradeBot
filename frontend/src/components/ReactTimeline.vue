<script setup lang="ts">
import { computed, ref } from "vue";
import { NTag } from "naive-ui";
import type { ToolCallRow } from "@/api/client";
import JsonBlock from "@/components/JsonBlock.vue";
import InjectionCard from "@/components/InjectionCard.vue";
import { fmtArgs, fmtDuration, clipArgs } from "@/utils/format";

interface ReactTool { tool_call_id: string | null; tool_name: string }
interface ReactStep { thinking: string | null; tools: ReactTool[] }
interface InjectedEvent {
  event: unknown;
  after_tool?: string;
  offset_ms?: number | null;
  after_tool_call_id?: string | null;
  triggered_ago?: string | null;
  kind_label?: string;
}

const props = defineProps<{
  steps: ReactStep[];
  toolCalls: ToolCallRow[];
  injectedEvents: InjectedEvent[] | null;
}>();

const toolMap = computed(() => {
  const m = new Map<string, ToolCallRow>();
  for (const t of props.toolCalls) if (t.tool_call_id) m.set(t.tool_call_id, t);
  return m;
});

// 注入锚定（spec §10）：优先 after_tool_call_id 命中骨架工具；否则按 after_tool 名 best-effort
// 锚到该名最后一次出现的工具；再不行则归到时间线末尾「未能锚定」组。byKey 以 cardKey 为键。
const injectionBuckets = computed(() => {
  const idToKey = new Map<string, string>();        // 骨架 tool_call_id → cardKey
  const nameToLastKey = new Map<string, string>();  // tool_name → 最后一次出现的 cardKey
  props.steps.forEach((step, si) => {
    step.tools.forEach((t, ti) => {
      const key = cardKey(t, si, ti);
      if (t.tool_call_id) idToKey.set(t.tool_call_id, key);
      nameToLastKey.set(t.tool_name, key);
    });
  });
  const byKey = new Map<string, InjectedEvent[]>();
  const orphan: InjectedEvent[] = [];
  const push = (key: string, e: InjectedEvent) => {
    const arr = byKey.get(key);
    if (arr) arr.push(e);
    else byKey.set(key, [e]);
  };
  for (const e of props.injectedEvents ?? []) {
    const byId = e.after_tool_call_id ? idToKey.get(e.after_tool_call_id) : undefined;
    if (byId) { push(byId, e); continue; }
    const byName = e.after_tool ? nameToLastKey.get(e.after_tool) : undefined;
    if (byName) { push(byName, e); continue; }   // §10：id 未命中 → 按 after_tool 名 best-effort
    orphan.push(e);                              // §10：名也未命中 → 时间线末尾归组
  }
  return { byKey, orphan };
});

// 每张工具卡的展开态：key = tool_call_id（无 id 用合成 key）
const openCards = ref<Set<string>>(new Set());

// 思考块整块折叠（A1）：默认折叠（openThinking 初始空集），按需整块展开。
// needsFold：有换行 或 超单行容量 → 给折叠 affordance 并默认折叠；只有真·单行短句豁免常显。
const THINKING_INLINE_MAX = 100;   // 单行容量小值（非旧的 600）
const openThinking = ref<Set<number>>(new Set());
function needsFold(text: string) {
  return text.includes("\n") || text.length > THINKING_INLINE_MAX;
}
function previewLine(text: string) {
  return text.split("\n")[0];      // 折叠态预览取首行（CSS 再做 ellipsis）
}
function toggleThinking(si: number) {
  const s = new Set(openThinking.value);
  s.has(si) ? s.delete(si) : s.add(si);
  openThinking.value = s;
}
function cardKey(t: ReactTool, si: number, ti: number) {
  return t.tool_call_id ?? `orphan-${si}-${ti}`;
}
function toggle(key: string) {
  const s = new Set(openCards.value);
  s.has(key) ? s.delete(key) : s.add(key);
  openCards.value = s;
}

function rowFor(t: ReactTool): ToolCallRow | undefined {
  return t.tool_call_id ? toolMap.value.get(t.tool_call_id) : undefined;
}
function headArgs(t: ReactTool): { text: string; clipped: boolean } {
  const r = rowFor(t);
  return r ? clipArgs(r.args) : { text: "", clipped: false };
}
function injectionsFor(t: ReactTool, si: number, ti: number): InjectedEvent[] {
  return injectionBuckets.value.byKey.get(cardKey(t, si, ti)) ?? [];
}
const orphanInjections = computed(() => injectionBuckets.value.orphan);
function statusType(s: string) {
  return s === "ok" ? "success" : s === "biz_error" ? "warning" : "error";
}
</script>

<template>
  <div class="react-timeline">
    <div v-for="(step, si) in steps" :key="si" class="react-step">
      <!-- 思考块（💭 思考，整块折叠默认收起 A1/A2；单行短句豁免常显） -->
      <div v-if="step.thinking" class="thinking">
        <span class="step-icon">💭</span>
        <div class="thinking-body">
          <template v-if="needsFold(step.thinking)">
            <div class="thinking-head clickable" @click="toggleThinking(si)">
              <span class="tk-lbl">思考</span>
              <span class="tk-caret">{{ openThinking.has(si) ? "▾" : "▸" }}</span>
              <span v-if="!openThinking.has(si)" class="tk-preview">{{ previewLine(step.thinking) }}</span>
            </div>
            <pre v-if="openThinking.has(si)" class="thinking-text">{{ step.thinking }}</pre>
          </template>
          <template v-else>
            <span class="tk-lbl">思考</span> <span class="tk-inline">{{ step.thinking }}</span>
          </template>
        </div>
      </div>

      <!-- 工具卡 + 锚定注入卡 -->
      <template v-for="(t, ti) in step.tools" :key="cardKey(t, si, ti)">
        <div class="tool-card">
          <div class="tool-head" :class="{ clickable: rowFor(t) }" @click="rowFor(t) && toggle(cardKey(t, si, ti))">
            <span class="step-icon">⚙</span>
            <span class="tool-name">{{ t.tool_name }}<template v-if="rowFor(t)">(<span class="tool-args">{{ headArgs(t).text }}</span>)</template></span>
            <template v-if="rowFor(t)">
              <n-tag size="tiny" :type="statusType(rowFor(t)!.status)">
                {{ rowFor(t)!.error_type ? `${rowFor(t)!.status} · ${rowFor(t)!.error_type}` : rowFor(t)!.status }}
              </n-tag>
              <span class="muted">{{ fmtDuration(rowFor(t)!.duration_ms) }}</span>
            </template>
            <span v-else class="muted orphan">无遥测记录（被拒或记录失败）</span>
          </div>
          <div v-if="rowFor(t) && openCards.has(cardKey(t, si, ti))" class="tool-body">
            <div v-if="headArgs(t).clipped" class="kv"><span class="k">入参</span><span class="args-compact">{{ fmtArgs(rowFor(t)!.args) }}</span></div>
            <div class="kv"><span class="k">结果</span>
              <JsonBlock v-if="rowFor(t)!.result != null" :value="rowFor(t)!.result" />
              <span v-else class="seam">结果未捕获</span>
            </div>
          </div>
        </div>

        <!-- 该工具后锚定的注入事件（批量并排，人读摘要卡） -->
        <InjectionCard v-for="(inj, ii) in injectionsFor(t, si, ti)" :key="`inj-${si}-${ti}-${ii}`" :inj="inj" />
      </template>
    </div>

    <!-- §10：未能按 id/名锚定的注入 → 时间线末尾归组 -->
    <div v-if="orphanInjections.length" class="orphan-injections">
      <div class="orphan-label muted">未能锚定的注入事件</div>
      <InjectionCard v-for="(inj, oi) in orphanInjections" :key="`orphan-inj-${oi}`" :inj="inj" />
    </div>
  </div>
</template>

<style scoped>
.react-step { border-left: 2px solid var(--ob-thinking-border); padding-left: 10px; margin-bottom: 14px; }
.thinking { display: flex; gap: 6px; margin-bottom: 8px; }
.thinking-text { white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 12px; line-height: 1.5; background: var(--ob-block-bg); padding: 6px 8px; border-radius: 4px; flex: 1; }
.tool-card { margin: 6px 0; background: var(--ob-block-bg); border-radius: 4px; }
.tool-head { display: flex; align-items: center; gap: 6px; padding: 5px 8px; cursor: pointer; user-select: none; font-size: 12px; }
.tool-name { font-weight: 600; }
.tool-args { color: var(--ob-text-muted); }
.tool-body { padding: 4px 8px 8px 26px; }
.kv { display: flex; gap: 8px; margin-top: 4px; font-size: 12px; }
.kv .k { color: var(--ob-text-muted); min-width: 32px; }
.step-icon { flex: 0 0 auto; }
.muted { color: var(--ob-text-muted); }
.orphan-label { font-size: 11px; margin: 8px 0 2px 18px; }
.orphan { font-style: italic; }
.seam { font-size: 12px; color: var(--ob-text-muted); font-style: italic; }
.clickable { cursor: pointer; }
.thinking-body { flex: 1; min-width: 0; }
.thinking-head { display: flex; align-items: baseline; gap: 6px; font-size: 12px; }
.tk-lbl { color: var(--ob-text-muted); font-weight: 600; }
.tk-caret { color: var(--ob-text-muted); }
.tk-preview { color: var(--ob-text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
.tk-inline { font-size: 12px; }
.args-compact { font-size: 12px; word-break: break-word; }
</style>
