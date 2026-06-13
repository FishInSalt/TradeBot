<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ value: unknown }>();

const isObject = computed(() => props.value !== null && typeof props.value === "object");
const pretty = computed(() => (isObject.value ? JSON.stringify(props.value, null, 2) : ""));
const isEmpty = computed(() => props.value === null || props.value === undefined);
</script>

<template>
  <span v-if="isEmpty" class="empty">—</span>
  <pre v-else-if="isObject" class="json">{{ pretty }}</pre>
  <pre v-else class="raw">{{ value }}</pre>
</template>

<style scoped>
.json, .raw { margin: 0; padding: 8px; background: rgba(0, 0, 0, 0.25); border-radius: 4px; font-size: 12px; line-height: 1.4; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
.raw { color: #fbbf24; }
.empty { opacity: 0.5; }
</style>
