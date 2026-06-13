<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from "vue";
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
  },
  { immediate: true },
);

onMounted(() => polling.start());
onUnmounted(() => polling.stop());
</script>

<template>
  <div v-if="hasSession" class="dashboard">
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
.empty { height: 100%; display: flex; align-items: center; justify-content: center; opacity: 0.5; }
</style>
