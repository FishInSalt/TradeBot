<script setup lang="ts">
import { computed } from "vue";
import { useRouter } from "vue-router";
import { NList, NListItem, NTag } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";

const store = useSessionsStore();
const router = useRouter();
const sessions = computed(() => store.sessions);

function open(id: string) {
  router.push({ name: "session", params: { id } });
}
</script>

<template>
  <n-list hoverable clickable class="session-list">
    <n-list-item
      v-for="s in sessions"
      :key="s.id"
      :class="['session-row', { active: s.id === store.currentId }]"
      @click="open(s.id)"
    >
      <div class="row">
        <div class="top">
          <n-tag :type="s.status === 'active' ? 'success' : 'warning'" size="small" round>{{ s.status }}</n-tag>
          <span class="name">{{ s.name }}</span>
        </div>
        <div class="bottom">
          <span class="symbol">{{ s.symbol }}</span>
          <span class="ret" :class="{ neg: s.total_return_pct < 0 }">
            {{ s.total_return_pct >= 0 ? "+" : "" }}{{ s.total_return_pct.toFixed(2) }}%
          </span>
        </div>
      </div>
    </n-list-item>
    <div v-if="!sessions.length" class="empty">暂无会话</div>
  </n-list>
</template>

<style scoped>
.session-row { cursor: pointer; }
.session-row.active { background: var(--ob-accent-soft); }
.row { display: flex; flex-direction: column; gap: 2px; width: 100%; }
.top { display: flex; align-items: center; gap: 6px; font-weight: 600; }
.bottom { display: flex; justify-content: space-between; font-size: 12px; opacity: 0.7; }
.ret { color: var(--ob-pos); }
.ret.neg { color: var(--ob-neg); }
.empty { padding: 16px; color: var(--ob-text-muted); font-size: 13px; }
</style>
