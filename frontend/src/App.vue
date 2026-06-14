<script setup lang="ts">
import { onMounted } from "vue";
import {
  NConfigProvider,
  NGlobalStyle,
  NLayout,
  NLayoutHeader,
  NLayoutSider,
  NLayoutContent,
  lightTheme,
} from "naive-ui";
import SessionList from "@/components/SessionList.vue";
import { useSessionsStore } from "@/stores/sessions";

const store = useSessionsStore();
onMounted(() => store.loadSessions());
</script>

<template>
  <n-config-provider :theme="lightTheme">
    <n-global-style />
    <n-layout class="app-shell" style="height: 100vh">
      <n-layout-header bordered class="topbar">TradeBot 观察台</n-layout-header>
      <n-layout has-sider class="body">
        <n-layout-sider bordered :width="240" :native-scrollbar="true" class="sider">
          <SessionList />
        </n-layout-sider>
        <n-layout-content :native-scrollbar="false" content-style="height:100%" class="main">
          <router-view />
        </n-layout-content>
      </n-layout>
    </n-layout>
  </n-config-provider>
</template>

<style scoped>
.app-shell :deep(.topbar) {
  height: 44px;
  display: flex;
  align-items: center;
  padding: 0 16px;
  font-weight: 700;
}
.app-shell .body {
  height: calc(100vh - 44px);
}
.main :deep(.n-layout-content__main) {
  height: 100%;
}
.app-shell :deep(.n-layout-content),
.app-shell :deep(.n-layout-content__main) {
  background: var(--ob-page-bg);
}
</style>
