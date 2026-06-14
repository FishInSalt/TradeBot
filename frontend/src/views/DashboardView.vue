<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from "vue";
import { NAlert } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import { usePolling } from "@/composables/usePolling";
import SessionMeta from "@/components/SessionMeta.vue";
import LiveStatusCard from "@/components/LiveStatusCard.vue";
import DecisionStream from "@/components/DecisionStream.vue";
import PerformanceBar from "@/components/PerformanceBar.vue";

const props = defineProps<{ id?: string }>();
const store = useSessionsStore();
const polling = usePolling(store);

const hasSession = computed(() => !!props.id);

watch(
  () => props.id,
  (id) => {
    if (id && id !== store.currentId) void store.selectSession(id);
    else if (!id) store.clearSelection(); // 回到 home：清状态、停轮询，不再续打旧会话
  },
  { immediate: true },
);

onMounted(() => polling.start());
onUnmounted(() => polling.stop());
</script>

<template>
  <div v-if="hasSession" class="dashboard">
    <n-alert v-if="store.error" type="error" title="加载出错" closable class="err" @close="store.error = null">
      {{ store.error }}
    </n-alert>
    <SessionMeta />
    <LiveStatusCard />
    <div class="stream-wrap"><DecisionStream /></div>
    <PerformanceBar />
  </div>
  <div v-else class="empty">请选择会话</div>
</template>

<style scoped>
.dashboard { height: 100%; display: flex; flex-direction: column; min-height: 0; }
.stream-wrap { flex: 1; overflow-y: auto; min-height: 0; }
.empty { height: 100%; display: flex; align-items: center; justify-content: center; color: var(--ob-text-muted); }
.err { margin: 8px 16px 0; }
</style>
