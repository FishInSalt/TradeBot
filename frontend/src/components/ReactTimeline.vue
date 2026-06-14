<script setup lang="ts">
import { computed, ref } from "vue";
import { NTag } from "naive-ui";
import type { ToolCallRow } from "@/api/client";
import JsonBlock from "@/components/JsonBlock.vue";
import { fmtArgs, fmtDuration, clipArgs } from "@/utils/format";

interface ReactTool { tool_call_id: string | null; tool_name: string }
interface ReactStep { thinking: string | null; tools: ReactTool[] }
interface InjectedEvent {
  event: unknown;
  after_tool?: string;
  offset_ms?: number | null;
  after_tool_call_id?: string | null;
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

// 思考块折叠态（议题 2）
const THINKING_FOLD_CHARS = 600;   // 超此字符数默认折叠
const THINKING_HEAD_CHARS = 360;   // 折叠态预览长度（≈前 6 行）
const openThinking = ref<Set<number>>(new Set());
function thinkingFolds(text: string) {
  return text.length > THINKING_FOLD_CHARS;
}
function thinkingShown(text: string, si: number) {
  if (!thinkingFolds(text) || openThinking.value.has(si)) return text;
  return text.slice(0, THINKING_HEAD_CHARS) + "…";
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
      <!-- 思考块（超长默认折叠，议题 2） -->
      <div v-if="step.thinking" class="thinking">
        <span class="step-icon">🧠</span>
        <div class="thinking-body">
          <pre class="thinking-text">{{ thinkingShown(step.thinking, si) }}</pre>
          <span v-if="thinkingFolds(step.thinking)" class="thinking-toggle clickable" @click="toggleThinking(si)">
            {{ openThinking.has(si) ? "收起 ▴" : "展开全文 ▾" }}
          </span>
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

        <!-- 该工具后锚定的注入事件（批量并排） -->
        <div v-for="(inj, ii) in injectionsFor(t, si, ti)" :key="`inj-${si}-${ti}-${ii}`" class="injection-card">
          <span class="step-icon">⚡</span>
          <span class="inj-title">触发事件注入</span>
          <span v-if="inj.offset_ms != null" class="muted">+{{ inj.offset_ms }}ms</span>
          <JsonBlock :value="inj.event" />
        </div>
      </template>
    </div>

    <!-- §10：未能按 id/名锚定的注入 → 时间线末尾归组 -->
    <div v-if="orphanInjections.length" class="orphan-injections">
      <div v-for="(inj, oi) in orphanInjections" :key="`orphan-inj-${oi}`" class="injection-card">
        <span class="step-icon">⚡</span>
        <span class="inj-title">触发事件注入（未能锚定）</span>
        <span v-if="inj.offset_ms != null" class="muted">+{{ inj.offset_ms }}ms</span>
        <JsonBlock :value="inj.event" />
      </div>
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
.injection-card { display: flex; align-items: center; gap: 6px; margin: 6px 0 6px 18px; padding: 5px 8px; background: var(--ob-warn-soft); border-radius: 4px; font-size: 12px; }
.inj-title { font-weight: 600; }
.step-icon { flex: 0 0 auto; }
.muted { color: var(--ob-text-muted); }
/* 注入卡 warn-soft 琥珀底上 muted(#6b7280) 仅 4.34 → 用更深 warn 达 AA（review）。
   scoped (0,2,0) 按特异性胜 .muted (0,1,0)，全局 muted/工具耗时仍白卡 4.83 不受影响。 */
.injection-card .muted { color: var(--ob-warn); }
.orphan { font-style: italic; }
.seam { font-size: 12px; color: var(--ob-text-muted); font-style: italic; }
.clickable { cursor: pointer; }
.thinking-body { flex: 1; }
.thinking-toggle { font-size: 11px; color: var(--ob-text-muted); }
.args-compact { font-size: 12px; word-break: break-word; }
</style>
