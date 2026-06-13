import { createRouter, createWebHashHistory } from "vue-router";
import DashboardView from "@/views/DashboardView.vue";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "home", component: DashboardView },
    { path: "/sessions/:id", name: "session", component: DashboardView, props: true },
  ],
});
