<script setup lang="ts">
import { computed, ref } from "vue";
import { NTag } from "naive-ui";
import type { ToolCallRow } from "@/api/client";
import JsonBlock from "@/components/JsonBlock.vue";

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

const injectionsByToolId = computed(() => {
  const m = new Map<string, InjectedEvent[]>();
  for (const e of props.injectedEvents ?? []) {
    const k = e.after_tool_call_id;
    if (!k) continue;
    (m.get(k) ?? m.set(k, []).get(k)!).push(e);
  }
  return m;
});

// 每张工具卡的展开态：key = tool_call_id（无 id 用合成 key）
const openCards = ref<Set<string>>(new Set());
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
function injectionsFor(t: ReactTool): InjectedEvent[] {
  return t.tool_call_id ? injectionsByToolId.value.get(t.tool_call_id) ?? [] : [];
}
function statusType(s: string) {
  return s === "ok" ? "success" : s === "biz_error" ? "warning" : "error";
}
</script>

<template>
  <div class="react-timeline">
    <div v-for="(step, si) in steps" :key="si" class="react-step">
      <!-- 思考块 -->
      <div v-if="step.thinking" class="thinking">
        <span class="step-icon">🧠</span>
        <pre class="thinking-text">{{ step.thinking }}</pre>
      </div>

      <!-- 工具卡 + 锚定注入卡 -->
      <template v-for="(t, ti) in step.tools" :key="cardKey(t, si, ti)">
        <div class="tool-card">
          <div class="tool-head clickable" @click="toggle(cardKey(t, si, ti))">
            <span class="step-icon">⚙</span>
            <span class="tool-name">{{ t.tool_name }}</span>
            <template v-if="rowFor(t)">
              <n-tag size="tiny" :type="statusType(rowFor(t)!.status)">
                {{ rowFor(t)!.error_type ? `${rowFor(t)!.status} · ${rowFor(t)!.error_type}` : rowFor(t)!.status }}
              </n-tag>
              <span class="muted">{{ rowFor(t)!.duration_ms }}ms</span>
            </template>
            <span v-else class="muted orphan">无遥测记录（被拒或记录失败）</span>
          </div>
          <div v-if="rowFor(t) && openCards.has(cardKey(t, si, ti))" class="tool-body">
            <div class="kv"><span class="k">入参</span><JsonBlock :value="rowFor(t)!.args" /></div>
            <div class="kv"><span class="k">结果</span>
              <JsonBlock v-if="rowFor(t)!.result != null" :value="rowFor(t)!.result" />
              <span v-else class="seam">结果未捕获</span>
            </div>
          </div>
        </div>

        <!-- 该工具后锚定的注入事件（批量并排） -->
        <div v-for="(inj, ii) in injectionsFor(t)" :key="`inj-${si}-${ti}-${ii}`" class="injection-card">
          <span class="step-icon">⚡</span>
          <span class="inj-title">触发事件注入</span>
          <span v-if="inj.offset_ms != null" class="muted">+{{ inj.offset_ms }}ms</span>
          <JsonBlock :value="inj.event" />
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.react-step { border-left: 2px solid rgba(96, 165, 250, 0.3); padding-left: 10px; margin-bottom: 14px; }
.thinking { display: flex; gap: 6px; margin-bottom: 8px; }
.thinking-text { white-space: pre-wrap; word-break: break-word; margin: 0; font-size: 12px; line-height: 1.5; background: rgba(0,0,0,0.18); padding: 6px 8px; border-radius: 4px; flex: 1; }
.tool-card { margin: 6px 0; background: rgba(255,255,255,0.03); border-radius: 4px; }
.tool-head { display: flex; align-items: center; gap: 6px; padding: 5px 8px; cursor: pointer; user-select: none; font-size: 12px; }
.tool-name { font-weight: 600; }
.tool-body { padding: 4px 8px 8px 26px; }
.kv { display: flex; gap: 8px; margin-top: 4px; font-size: 12px; }
.kv .k { opacity: 0.6; min-width: 32px; }
.injection-card { display: flex; align-items: center; gap: 6px; margin: 6px 0 6px 18px; padding: 5px 8px; background: rgba(250, 204, 21, 0.1); border-radius: 4px; font-size: 12px; }
.inj-title { font-weight: 600; }
.step-icon { flex: 0 0 auto; }
.muted { opacity: 0.55; }
.orphan { font-style: italic; }
.seam { font-size: 12px; opacity: 0.5; font-style: italic; }
.clickable { cursor: pointer; }
</style>
