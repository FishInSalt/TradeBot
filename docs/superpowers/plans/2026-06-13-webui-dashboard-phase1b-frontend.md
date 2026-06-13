# WebUI 观察台 Phase 1b 前端 SPA 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Phase 1a 只读 JSON API 之上建一个 Vue 3 SPA，把 agent 的决策过程（reasoning/decision）与交易表现搬到浏览器，主从式 app-shell 布局，UI 用 Naive UI 组件库构建。

**Architecture:** 纯前端 SPA，只消费 `src/webui/` 的 6 个只读端点（`/api/*`），**不改后端**。Pinia 集中 store + 5s active-only 增量轮询；决策流用 `NCollapse` 手风琴就地展开、懒加载 cycle 详情。类型从后端 OpenAPI 经 openapi-typescript 生成（后端 schema 为唯一真相源）。

**Tech Stack:** Vue 3 + TypeScript + Vite + Pinia + vue-router(hash mode) + Naive UI + lightweight-charts；测试 Vitest + Vue Test Utils。

---

## 范围与约束（执行者必读）

- **所有产物落 `frontend/`**，不碰 `src/`（§7 契约层准备已在 Phase 1a 落地，本期不再动后端）。
- 所有 npm 命令在 `frontend/` 目录下执行（除显式注明从仓库根执行的）。
- 当前分支已是 `iter-webui-dashboard-phase1b`，**不要切到 main**。本计划文档作为独立 commit 先于前端代码（已由上层流程处理）。
- 后端契约真相源：`src/webui/schemas.py`；6 端点定义见 `src/webui/app.py`。**禁止手改生成的 `types.ts`**。
- 路由用 **hash mode**（`createWebHashHistory`），spec §构建/开发/部署已定，不要改成 history mode。
- **UI 一律用 Naive UI 组件**（`NLayout`/`NList`/`NCard`/`NTag`/`NDescriptions`/`NCollapse`/`NDataTable`/`NStatistic` 等），避免大面积手搓 CSS——这是用户既定的组件库选择。仅净值曲线（lightweight-charts）与少量纯文本块（reasoning/decision `<pre>`）例外。
- 时间戳：后端出站均带 `Z`（UTC）。前端 `new Date(iso)` 解析即为 UTC instant，`toLocaleString()` 展示本地时区——直接用，勿再手动加减时区。

## 文件结构

全部新增在 `frontend/`：

```
frontend/
  index.html                  # #app 挂载点 + main.ts
  package.json                # deps + scripts(dev/build/test/gen:types)
  tsconfig.json               # Vue3 + Vite TS 配置
  tsconfig.node.json          # vite.config 的 node 侧 TS 配置（composite，不设 noEmit）
  vite.config.ts              # vue 插件 + server.proxy /api→:8000 + build.outDir=dist + vitest test 配置
  env.d.ts                    # vue SFC 类型声明
  openapi.json                # 后端 dump 的快照（gen:types 输入；可重生成）
  src/
    main.ts                   # createApp + Pinia + router，mount #app
    App.vue                   # app-shell 壳：NLayout 顶栏 + 左 SessionList + <router-view>
    router.ts                 # hash mode：/ 与 /sessions/:id
    api/
      types.ts                # openapi-typescript 生成（勿手改）
      client.ts               # 类型别名 + 类型化 fetch + ApiError
    stores/
      sessions.ts             # Pinia store（state + actions）
    composables/
      usePolling.ts           # 5s active-only 增量轮询，document.hidden 暂停/恢复
    utils/
      time.ts                 # UTC 解析 / 本地展示助手
    views/
      DashboardView.vue       # 主区路由目标（/ 与 /sessions/:id）：会话元信息 + 状态卡 + 决策流 + 表现条
    components/
      SessionList.vue         # 左栏会话列表（NList，点选切会话，URL 同步）
      SessionMeta.vue         # 会话元信息条（NDescriptions，消费 store.detail）
      LiveStatusCard.vue      # 状态卡横条（NCard/NTag，常驻顶部）
      DecisionStream.vue      # 决策流（NCollapse 手风琴容器）
      CycleRowHeader.vue      # 单条 cycle 折叠态表头（纯展示，NCollapseItem #header 槽）
      CycleDetailPanel.vue    # 展开详情（R2-7 五维分区 + NDataTable 工具表 + result 预留空态）
      PerformanceBar.vue      # 底部表现摘要条（NStatistic 指标 + 双口径标注，常驻底部）
      EquityChart.vue         # lightweight-charts 净值曲线封装
      TradesTable.vue         # 可折叠成交表（NDataTable）
      JsonBlock.vue           # dict/list 折叠 JSON / str 原样代码块
  test/
    setup.ts                  # vitest 全局 setup：ResizeObserver/matchMedia 补桩（Naive UI 依赖）
    *.spec.ts                 # 与被测单元同名的测试
```

> 命名：组件用 `CycleDetailPanel.vue`（非 `CycleDetail.vue`）以与后端 schema 概念 `CycleDetail` 区分——前者是渲染组件，后者是数据契约。

---

## Task 1: 脚手架与工具链

**Files:**
- Create: `frontend/package.json` / `tsconfig.json` / `tsconfig.node.json` / `env.d.ts` / `vite.config.ts` / `index.html` / `.gitignore`
- Create: `frontend/src/main.ts` / `App.vue`（最小壳，Task 7 补全）/ `router.ts` / `views/DashboardView.vue`（占位，Task 13 补全）
- Create: `frontend/test/setup.ts`
- Test: `frontend/test/smoke.spec.ts`

- [ ] **Step 1: 创建 `frontend/package.json`**

```json
{
  "name": "tradebot-webui",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "gen:types": "openapi-typescript openapi.json -o src/api/types.ts"
  },
  "dependencies": {
    "lightweight-charts": "^4.2.0",
    "naive-ui": "^2.38.1",
    "pinia": "^2.1.7",
    "vue": "^3.4.21",
    "vue-router": "^4.3.0"
  },
  "devDependencies": {
    "@pinia/testing": "^0.1.3",
    "@vitejs/plugin-vue": "^5.0.4",
    "@vue/test-utils": "^2.4.5",
    "jsdom": "^24.0.0",
    "openapi-typescript": "^7.0.0",
    "typescript": "^5.4.5",
    "vite": "^5.2.8",
    "vitest": "^2.0.0",
    "vue-tsc": "^2.0.13"
  }
}
```

- [ ] **Step 2: 创建 `frontend/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "module": "ESNext",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "skipLibCheck": true,
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "preserve",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": { "@/*": ["src/*"] },
    "types": ["vitest/globals"]
  },
  "include": ["src/**/*.ts", "src/**/*.vue", "test/**/*.ts", "env.d.ts"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 3: 创建 `frontend/tsconfig.node.json`**（C 修复：`composite` 不与 `noEmit` 同设，对齐 canonical 模板，避免 build-mode 摩擦）

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 4: 创建 `frontend/env.d.ts`**

```typescript
/// <reference types="vite/client" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<{}, {}, any>;
  export default component;
}
```

- [ ] **Step 5: 创建 `frontend/vite.config.ts`**

```typescript
/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    proxy: {
      // 开发期把 /api 代理到后端，零 CORS、后端不加中间件
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist", // 由 src/webui/app.py 的 StaticFiles(frontend/dist, html=True) 同源挂载
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./test/setup.ts"],
  },
});
```

- [ ] **Step 6: 创建 `frontend/test/setup.ts`**（Naive UI 的 `NDataTable` 等依赖 `ResizeObserver`/`matchMedia`，jsdom 无 → 补桩，否则相关组件挂载即抛）

```typescript
// jsdom 缺 ResizeObserver / matchMedia；Naive UI（NDataTable 等）依赖它们，测试环境补最小桩。
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!(globalThis as any).ResizeObserver) {
  (globalThis as any).ResizeObserver = ResizeObserverStub;
}
if (!(globalThis as any).matchMedia) {
  (globalThis as any).matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return false;
    },
  });
}
export {};
```

- [ ] **Step 7: 创建 `frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>TradeBot 观察台</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 8: 创建 `frontend/src/router.ts`**

```typescript
import { createRouter, createWebHashHistory } from "vue-router";
import DashboardView from "@/views/DashboardView.vue";

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", name: "home", component: DashboardView },
    { path: "/sessions/:id", name: "session", component: DashboardView, props: true },
  ],
});
```

- [ ] **Step 9: 创建占位 `frontend/src/views/DashboardView.vue`（Task 13 补全）**

```vue
<script setup lang="ts"></script>

<template>
  <div class="dashboard-placeholder">观察台主区（待补全）</div>
</template>
```

- [ ] **Step 10: 创建最小 `frontend/src/App.vue`（占位壳，Task 7 补全为 NLayout）**

```vue
<script setup lang="ts"></script>

<template>
  <div class="app-shell">
    <router-view />
  </div>
</template>
```

- [ ] **Step 11: 创建 `frontend/src/main.ts`**

```typescript
import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "@/App.vue";
import { router } from "@/router";

createApp(App).use(createPinia()).use(router).mount("#app");
```

- [ ] **Step 12: 写冒烟测试 `frontend/test/smoke.spec.ts`**（B 修复：用 `createTestingPinia` stub actions，保持纯壳语义；断稳定的 `.app-shell` 选择器，不依赖会随后续任务消失的占位文案）

```typescript
import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "@/App.vue";
import DashboardView from "@/views/DashboardView.vue";

describe("app scaffold", () => {
  it("App 壳挂载并渲染 .app-shell 容器", async () => {
    const router = createRouter({
      history: createWebHashHistory(),
      routes: [{ path: "/", component: DashboardView }],
    });
    router.push("/");
    await router.isReady();
    const wrapper = mount(App, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true }), router] },
    });
    expect(wrapper.find(".app-shell").exists()).toBe(true);
  });
});
```

- [ ] **Step 13: 创建 `frontend/.gitignore`**

```
node_modules/
dist/
*.local
```

- [ ] **Step 14: 安装依赖并跑测试**

Run（在 `frontend/`）：`npm install && npm run test`
Expected: smoke.spec.ts PASS（1 passed）。

- [ ] **Step 15: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/tsconfig.node.json frontend/env.d.ts frontend/vite.config.ts frontend/index.html frontend/.gitignore frontend/src/main.ts frontend/src/App.vue frontend/src/router.ts frontend/src/views/DashboardView.vue frontend/test/setup.ts frontend/test/smoke.spec.ts
git commit -m "feat(webui): Phase 1b 前端脚手架（Vue3+Vite+Pinia+router+Naive UI+vitest）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 从后端 OpenAPI 生成类型

**Files:**
- Create: `frontend/openapi.json`（后端 dump 快照）
- Create: `frontend/src/api/types.ts`（openapi-typescript 生成）

- [ ] **Step 1: 从仓库根 dump 后端 OpenAPI 到 `frontend/openapi.json`**

Run（在仓库根 `/Users/z/Z/TradeBot`）：

```bash
python -c "import json; from src.webui.app import create_app; print(json.dumps(create_app().openapi(), ensure_ascii=False))" > frontend/openapi.json
```

说明：`create_app()` 只构造引擎对象不连接 DB（`create_async_engine` 惰性），无需真实 DB 文件即可生成 schema。
Expected: `frontend/openapi.json` 含 `"components"` 且包含 `SessionSummary`/`CycleDetail`/`ToolCallRow` 等 schema 名。

- [ ] **Step 2: 验证 dump 含关键 schema**

Run（在仓库根）：`grep -o '"ToolCallRow"' frontend/openapi.json | head -1`
Expected: 输出 `"ToolCallRow"`（确认 `result` 预留字段所在 schema 已进 OpenAPI）。

- [ ] **Step 3: 生成 `frontend/src/api/types.ts`**

Run（在 `frontend/`）：`npm run gen:types`
Expected: 生成 `src/api/types.ts`，含 `export interface components { schemas: { SessionSummary: {...}, ... } }`。

- [ ] **Step 4: 验证生成的类型含 result 预留字段**

Run（在 `frontend/`）：`grep -n "result" src/api/types.ts | head -3`
Expected: 至少一行命中（`ToolCallRow.result` union 类型已生成）。

- [ ] **Step 5: Commit**

```bash
git add frontend/openapi.json frontend/src/api/types.ts
git commit -m "feat(webui): 从后端 OpenAPI 生成前端类型（openapi-typescript）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: API client + ApiError

**Files:**
- Create: `frontend/src/api/client.ts`
- Test: `frontend/test/client.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/client.spec.ts`**

```typescript
import { describe, it, expect, vi, afterEach } from "vitest";
import { api, ApiError } from "@/api/client";

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    })),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("listSessions 解析 2xx JSON", async () => {
    mockFetch(200, [{ id: "s1" }]);
    const rows = await api.listSessions();
    expect(rows[0].id).toBe("s1");
    expect(fetch).toHaveBeenCalledWith("/api/sessions");
  });

  it("非 2xx 抛带 status 的 ApiError", async () => {
    mockFetch(404, { detail: "nope" });
    await expect(api.getSession("nope")).rejects.toBeInstanceOf(ApiError);
    await expect(api.getSession("nope")).rejects.toMatchObject({ status: 404 });
  });

  it("getCycles 拼接 after_id/limit query", async () => {
    mockFetch(200, []);
    await api.getCycles("s1", { limit: 50, afterId: 12 });
    expect(fetch).toHaveBeenCalledWith("/api/sessions/s1/cycles?limit=50&after_id=12");
  });

  it("getCycles 无参数时不带 query string", async () => {
    mockFetch(200, []);
    await api.getCycles("s1");
    expect(fetch).toHaveBeenCalledWith("/api/sessions/s1/cycles");
  });

  it("getCycle 命中详情端点", async () => {
    mockFetch(200, { id: 7 });
    const d = await api.getCycle(7);
    expect(d.id).toBe(7);
    expect(fetch).toHaveBeenCalledWith("/api/cycles/7");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/client.spec.ts`
Expected: FAIL（`@/api/client` 不存在）。

- [ ] **Step 3: 实现 `frontend/src/api/client.ts`**

```typescript
import type { components } from "./types";

type S = components["schemas"];
export type SessionSummary = S["SessionSummary"];
export type SessionDetail = S["SessionDetail"];
export type CycleRow = S["CycleRow"];
export type CycleDetail = S["CycleDetail"];
export type ToolCallRow = S["ToolCallRow"];
export type Performance = S["Performance"];
export type EquityPoint = S["EquityPoint"];
export type TradeRow = S["TradeRow"];
export type LiveStatus = S["LiveStatus"];
export type PositionInfo = S["PositionInfo"];
export type OrderInfo = S["OrderInfo"];
export type AlertInfo = S["AlertInfo"];

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) {
    throw new ApiError(res.status, `GET /api${path} → ${res.status}`);
  }
  return (await res.json()) as T;
}

export interface CyclesQuery {
  limit?: number;
  afterId?: number;
  beforeId?: number;
}

export const api = {
  listSessions: () => get<SessionSummary[]>("/sessions"),
  getSession: (sid: string) => get<SessionDetail>(`/sessions/${sid}`),
  getCycles: (sid: string, q: CyclesQuery = {}) => {
    const p = new URLSearchParams();
    if (q.limit != null) p.set("limit", String(q.limit));
    if (q.afterId != null) p.set("after_id", String(q.afterId));
    if (q.beforeId != null) p.set("before_id", String(q.beforeId));
    const qs = p.toString();
    return get<CycleRow[]>(`/sessions/${sid}/cycles${qs ? `?${qs}` : ""}`);
  },
  getCycle: (pk: number) => get<CycleDetail>(`/cycles/${pk}`),
  getPerformance: (sid: string) => get<Performance>(`/sessions/${sid}/performance`),
  getLive: (sid: string) => get<LiveStatus>(`/sessions/${sid}/live`),
};
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/client.spec.ts`
Expected: PASS（5 passed）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/test/client.spec.ts
git commit -m "feat(webui): 类型化 API client + ApiError

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 时间助手

**Files:**
- Create: `frontend/src/utils/time.ts`
- Test: `frontend/test/time.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/time.spec.ts`**（A 修复：epoch 实算复核——`2026-06-12T10:00:00Z` = `1781258400` 秒）

```typescript
import { describe, it, expect } from "vitest";
import { parseUtc, epochSec, fmtLocal } from "@/utils/time";

describe("time utils", () => {
  it("parseUtc 把带 Z 的串按 UTC 解析", () => {
    // 2026-06-12T10:00:00Z = 1781258400 秒（实算：2026-01-01=1767225600 + 162天 + 10h）
    expect(parseUtc("2026-06-12T10:00:00Z").getTime()).toBe(1781258400000);
  });

  it("epochSec 返回秒级时间戳", () => {
    expect(epochSec("2026-06-12T10:00:00Z")).toBe(1781258400);
  });

  it("fmtLocal 对 null 返回占位", () => {
    expect(fmtLocal(null)).toBe("—");
  });

  it("fmtLocal 对有效串返回非空字符串", () => {
    expect(fmtLocal("2026-06-12T10:00:00Z")).not.toBe("—");
    expect(typeof fmtLocal("2026-06-12T10:00:00Z")).toBe("string");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/time.spec.ts`
Expected: FAIL（`@/utils/time` 不存在）。

- [ ] **Step 3: 实现 `frontend/src/utils/time.ts`**

```typescript
/** 后端出站时间戳均带 Z（UTC）。前端解析即 UTC instant，本地展示用 toLocaleString。 */
export function parseUtc(iso: string): Date {
  return new Date(iso);
}

export function epochSec(iso: string): number {
  return Math.floor(parseUtc(iso).getTime() / 1000);
}

export function fmtLocal(iso: string | null | undefined): string {
  if (!iso) return "—";
  return parseUtc(iso).toLocaleString();
}
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/time.spec.ts`
Expected: PASS（4 passed）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/time.ts frontend/test/time.spec.ts
git commit -m "feat(webui): UTC 解析 / 本地展示时间助手

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Pinia sessions store

**Files:**
- Create: `frontend/src/stores/sessions.ts`
- Test: `frontend/test/store.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/store.spec.ts`**

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useSessionsStore } from "@/stores/sessions";
import { api } from "@/api/client";

beforeEach(() => setActivePinia(createPinia()));
afterEach(() => vi.restoreAllMocks());

function cyc(id: number) {
  return { id, cycle_label: `c${id}`, triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z", decision_head: "d", tokens_consumed: 1, wall_time_ms: 1, execution_status: "ok" };
}

describe("sessions store", () => {
  it("selectSession 并发装配 detail/live/performance/cycles 并设 currentId", async () => {
    vi.spyOn(api, "getSession").mockResolvedValue({ id: "s1" } as any);
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({ initial_balance: 100 } as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([cyc(3), cyc(2), cyc(1)] as any);
    const s = useSessionsStore();
    await s.selectSession("s1");
    expect(s.currentId).toBe("s1");
    expect(s.detail?.id).toBe("s1");
    expect(s.live?.status).toBe("active");
    expect(s.performance?.initial_balance).toBe(100);
    expect(s.cycles.map((c) => c.id)).toEqual([3, 2, 1]);
    expect(s.expandedCycleId).toBeNull();
  });

  it("pollTick 增量 append 且按 id 去重并保持 id DESC", async () => {
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    s.cycles = [cyc(2), cyc(1)] as any;
    vi.spyOn(api, "getCycles").mockResolvedValue([cyc(4), cyc(3), cyc(2)] as any);
    await s.pollTick();
    expect(s.cycles.map((c) => c.id)).toEqual([4, 3, 2, 1]); // 去重 + DESC
    expect(api.getCycles).toHaveBeenCalledWith("s1", { afterId: 2 }); // 取当前最大 id 之上
  });

  it("pollTick 失败累加 pollFailCount 不抛", async () => {
    vi.spyOn(api, "getLive").mockRejectedValue(new Error("boom"));
    const s = useSessionsStore();
    s.currentId = "s1";
    await s.pollTick();
    expect(s.pollFailCount).toBe(1);
  });

  it("pollTick 成功重置 pollFailCount", async () => {
    vi.spyOn(api, "getLive").mockResolvedValue({ status: "active" } as any);
    vi.spyOn(api, "getPerformance").mockResolvedValue({} as any);
    vi.spyOn(api, "getCycles").mockResolvedValue([] as any);
    const s = useSessionsStore();
    s.currentId = "s1";
    s.pollFailCount = 2;
    await s.pollTick();
    expect(s.pollFailCount).toBe(0);
  });

  it("expandCycle 懒加载并缓存，再点同一条收起", async () => {
    const spy = vi.spyOn(api, "getCycle").mockResolvedValue({ id: 5 } as any);
    const s = useSessionsStore();
    await s.expandCycle(5);
    expect(s.expandedCycleId).toBe(5);
    expect(s.cycleDetails.get(5)?.id).toBe(5);
    await s.expandCycle(5); // toggle 收起
    expect(s.expandedCycleId).toBeNull();
    await s.expandCycle(5); // 再展开命中缓存,不重复拉取
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("currentSession getter 按 currentId 命中列表项", () => {
    const s = useSessionsStore();
    s.sessions = [{ id: "s1", status: "active" } as any, { id: "s2", status: "paused" } as any];
    s.currentId = "s2";
    expect(s.currentSession?.status).toBe("paused");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/store.spec.ts`
Expected: FAIL（`@/stores/sessions` 不存在）。

- [ ] **Step 3: 实现 `frontend/src/stores/sessions.ts`**

```typescript
import { defineStore } from "pinia";
import {
  api,
  ApiError,
  type SessionSummary,
  type SessionDetail,
  type LiveStatus,
  type Performance,
  type CycleRow,
  type CycleDetail,
} from "@/api/client";

interface State {
  sessions: SessionSummary[];
  currentId: string | null;
  detail: SessionDetail | null;
  live: LiveStatus | null;
  performance: Performance | null;
  cycles: CycleRow[]; // 维护为 id DESC（新在顶）
  cycleDetails: Map<number, CycleDetail>; // 展开懒加载缓存
  expandedCycleId: number | null;
  loading: boolean;
  error: string | null;
  pollFailCount: number;
}

export const useSessionsStore = defineStore("sessions", {
  state: (): State => ({
    sessions: [],
    currentId: null,
    detail: null,
    live: null,
    performance: null,
    cycles: [],
    cycleDetails: new Map(),
    expandedCycleId: null,
    loading: false,
    error: null,
    pollFailCount: 0,
  }),

  getters: {
    currentSession: (s): SessionSummary | undefined =>
      s.sessions.find((x) => x.id === s.currentId),
  },

  actions: {
    async loadSessions() {
      try {
        this.sessions = await api.listSessions();
      } catch (e) {
        this.error = e instanceof ApiError ? e.message : String(e);
      }
    },

    async selectSession(id: string) {
      this.currentId = id;
      this.expandedCycleId = null;
      this.cycleDetails = new Map();
      this.loading = true;
      this.error = null;
      try {
        const [detail, live, performance, cycles] = await Promise.all([
          api.getSession(id),
          api.getLive(id),
          api.getPerformance(id),
          api.getCycles(id, { limit: 50 }),
        ]);
        this.detail = detail;
        this.live = live;
        this.performance = performance;
        this.cycles = cycles; // 后端已 id DESC
      } catch (e) {
        this.error = e instanceof ApiError ? e.message : String(e);
      } finally {
        this.loading = false;
      }
    },

    mergeCycles(fresh: CycleRow[]) {
      const seen = new Set(this.cycles.map((c) => c.id));
      const add = fresh.filter((c) => !seen.has(c.id));
      this.cycles = [...add, ...this.cycles].sort((a, b) => b.id - a.id);
    },

    async pollTick() {
      const sid = this.currentId;
      if (!sid) return;
      try {
        const [live, performance] = await Promise.all([
          api.getLive(sid),
          api.getPerformance(sid),
        ]);
        this.live = live;
        this.performance = performance;
        const maxId = this.cycles.length
          ? Math.max(...this.cycles.map((c) => c.id))
          : undefined;
        const fresh = await api.getCycles(sid, maxId != null ? { afterId: maxId } : {});
        this.mergeCycles(fresh);
        this.pollFailCount = 0;
      } catch {
        // 瞬态错误静默：不炸 UI，仅累加，由状态卡角标在 ≥3 次时提示
        this.pollFailCount += 1;
      }
    },

    async expandCycle(id: number) {
      if (this.expandedCycleId === id) {
        this.expandedCycleId = null; // 再点同条收起
        return;
      }
      this.expandedCycleId = id;
      if (!this.cycleDetails.has(id)) {
        try {
          const d = await api.getCycle(id);
          this.cycleDetails.set(id, d);
        } catch (e) {
          this.error = e instanceof ApiError ? e.message : String(e);
        }
      }
    },
  },
});
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/store.spec.ts`
Expected: PASS（6 passed）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stores/sessions.ts frontend/test/store.spec.ts
git commit -m "feat(webui): Pinia sessions store（装配/增量轮询/懒加载展开）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: usePolling 轮询 composable

**Files:**
- Create: `frontend/src/composables/usePolling.ts`
- Test: `frontend/test/usePolling.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/usePolling.spec.ts`**

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { usePolling } from "@/composables/usePolling";

function setHidden(v: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => v });
}

beforeEach(() => {
  vi.useFakeTimers();
  setHidden(false);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("usePolling", () => {
  it("active 会话每 5s 调一次 pollTick", () => {
    const store = { currentSession: { status: "active" }, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.start();
    vi.advanceTimersByTime(15000);
    expect(store.pollTick).toHaveBeenCalledTimes(3);
    p.stop();
  });

  it("paused 会话不调 pollTick", () => {
    const store = { currentSession: { status: "paused" }, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.start();
    vi.advanceTimersByTime(15000);
    expect(store.pollTick).not.toHaveBeenCalled();
    p.stop();
  });

  it("document.hidden 时 tick 不调 pollTick", () => {
    setHidden(true);
    const store = { currentSession: { status: "active" }, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.tick();
    expect(store.pollTick).not.toHaveBeenCalled();
  });

  it("stop 后清理定时器", () => {
    const store = { currentSession: { status: "active" }, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.start();
    p.stop();
    vi.advanceTimersByTime(15000);
    expect(store.pollTick).not.toHaveBeenCalled();
  });

  it("无 currentSession 时 tick 不调 pollTick", () => {
    const store = { currentSession: undefined, pollTick: vi.fn() };
    const p = usePolling(store as any);
    p.tick();
    expect(store.pollTick).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/usePolling.spec.ts`
Expected: FAIL（`@/composables/usePolling` 不存在）。

- [ ] **Step 3: 实现 `frontend/src/composables/usePolling.ts`**

```typescript
import type { useSessionsStore } from "@/stores/sessions";

type Store = Pick<ReturnType<typeof useSessionsStore>, "currentSession" | "pollTick">;

/**
 * 5s active-only 增量轮询。document.hidden（标签页不可见）暂停、可见恢复——省后端读。
 * 无内部生命周期钩子：由调用方在 onMounted(start)/onUnmounted(stop) 接管。
 */
export function usePolling(store: Store, intervalMs = 5000) {
  let timer: ReturnType<typeof setInterval> | null = null;

  const tick = () => {
    if (typeof document !== "undefined" && document.hidden) return;
    if (store.currentSession?.status !== "active") return;
    void store.pollTick();
  };

  function startTimer() {
    if (timer) return;
    timer = setInterval(tick, intervalMs);
  }
  function stopTimer() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }
  const onVisibility = () => {
    if (document.hidden) stopTimer();
    else startTimer();
  };

  function start() {
    document.addEventListener("visibilitychange", onVisibility);
    startTimer();
  }
  function stop() {
    document.removeEventListener("visibilitychange", onVisibility);
    stopTimer();
  }

  return { start, stop, tick };
}
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/usePolling.spec.ts`
Expected: PASS（5 passed）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/composables/usePolling.ts frontend/test/usePolling.spec.ts
git commit -m "feat(webui): usePolling 5s active-only 增量轮询（hidden 暂停/恢复）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: App-shell（NLayout）+ SessionList（NList）

**Files:**
- Modify: `frontend/src/App.vue`（NLayout 满视口主从布局 + 暗色主题 + 启动拉会话）
- Create: `frontend/src/components/SessionList.vue`
- Test: `frontend/test/SessionList.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/SessionList.spec.ts`**

```typescript
import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";
import SessionList from "@/components/SessionList.vue";

const sessions = [
  { id: "sim19", name: "sim#19", symbol: "BTC/USDT:USDT", status: "active", total_return_pct: 2.5, created_at: "2026-06-12T10:00:00Z", last_active_at: "2026-06-12T10:30:00Z", cycle_count: 10 },
  { id: "sim18", name: "sim#18", symbol: "BTC/USDT:USDT", status: "paused", total_return_pct: -1.1, created_at: "2026-06-11T10:00:00Z", last_active_at: null, cycle_count: 5 },
];

function mountList() {
  const wrapper = mount(SessionList, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })],
      stubs: { "router-link": true },
    },
  });
  const store = useSessionsStore();
  store.sessions = sessions as any;
  return { wrapper, store };
}

describe("SessionList", () => {
  it("渲染每个会话名称与 symbol", async () => {
    const { wrapper } = mountList();
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("sim#19");
    expect(wrapper.text()).toContain("sim#18");
  });

  it("currentId 命中行标记 active 类", async () => {
    const { wrapper, store } = mountList();
    store.currentId = "sim19";
    await wrapper.vm.$nextTick();
    const active = wrapper.find(".session-row.active");
    expect(active.exists()).toBe(true);
    expect(active.text()).toContain("sim#19");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/SessionList.spec.ts`
Expected: FAIL（`@/components/SessionList.vue` 不存在）。

- [ ] **Step 3: 实现 `frontend/src/components/SessionList.vue`**（NList + NListItem + NTag）

```vue
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
.session-row.active { background: rgba(96, 165, 250, 0.15); }
.row { display: flex; flex-direction: column; gap: 2px; width: 100%; }
.top { display: flex; align-items: center; gap: 6px; font-weight: 600; }
.bottom { display: flex; justify-content: space-between; font-size: 12px; opacity: 0.7; }
.ret { color: #4ade80; }
.ret.neg { color: #f87171; }
.empty { padding: 16px; opacity: 0.5; font-size: 13px; }
</style>
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/SessionList.spec.ts`
Expected: PASS（2 passed）。

- [ ] **Step 5: 补全 `frontend/src/App.vue`**（NConfigProvider darkTheme + NGlobalStyle + NLayout 满视口主从）

```vue
<script setup lang="ts">
import { onMounted } from "vue";
import {
  NConfigProvider,
  NGlobalStyle,
  NLayout,
  NLayoutHeader,
  NLayoutSider,
  NLayoutContent,
  darkTheme,
} from "naive-ui";
import SessionList from "@/components/SessionList.vue";
import { useSessionsStore } from "@/stores/sessions";

const store = useSessionsStore();
onMounted(() => store.loadSessions());
</script>

<template>
  <n-config-provider :theme="darkTheme">
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
</style>
```

> 说明：`n-layout-content` 设 `native-scrollbar=false` 让其不自管滚动，由 `DashboardView` 内部的决策流区独立滚（VS Code 式）。`.app-shell` 类落在 `NLayout` 根 div 上，供冒烟测试稳定命中。

- [ ] **Step 6: 跑全量测试确认无回归**

Run（在 `frontend/`）：`npm run test`
Expected: 全部 PASS（含 smoke/client/time/store/usePolling/SessionList）。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.vue frontend/src/components/SessionList.vue frontend/test/SessionList.spec.ts
git commit -m "feat(webui): NLayout 满视口主从壳 + NList 会话列表

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: JsonBlock 通用 JSON/字符串渲染

**Files:**
- Create: `frontend/src/components/JsonBlock.vue`
- Test: `frontend/test/JsonBlock.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/JsonBlock.spec.ts`**

```typescript
import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import JsonBlock from "@/components/JsonBlock.vue";

describe("JsonBlock", () => {
  it("dict/list 渲染为格式化 JSON", () => {
    const w = mount(JsonBlock, { props: { value: { a: 1, b: [2, 3] } } });
    expect(w.text()).toContain('"a"');
    expect(w.text()).toContain("2");
  });

  it("string 原样渲染为代码块", () => {
    const w = mount(JsonBlock, { props: { value: "raw broken json {" } });
    expect(w.text()).toContain("raw broken json {");
  });

  it("null 渲染空态占位", () => {
    const w = mount(JsonBlock, { props: { value: null } });
    expect(w.text()).toContain("—");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/JsonBlock.spec.ts`
Expected: FAIL（组件不存在）。

- [ ] **Step 3: 实现 `frontend/src/components/JsonBlock.vue`**

```vue
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
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/JsonBlock.spec.ts`
Expected: PASS（3 passed）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/JsonBlock.vue frontend/test/JsonBlock.spec.ts
git commit -m "feat(webui): JsonBlock（dict/list 折叠 JSON / str 原样代码块）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: CycleDetailPanel（五维分区 + NDataTable 工具表 + result 预留空态）

**Files:**
- Create: `frontend/src/components/CycleDetailPanel.vue`
- Test: `frontend/test/CycleDetailPanel.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/CycleDetailPanel.spec.ts`**

```typescript
import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import CycleDetailPanel from "@/components/CycleDetailPanel.vue";

function detail(overrides = {}) {
  return {
    id: 5, cycle_label: "c5", triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z",
    reasoning: "thinking text", decision: "(1) Stance: hold",
    trigger_context: [{ type: "scheduled_tick" }],
    state_snapshot: { balance: { total_usdt: 10000 } },
    injected_events: null,
    tool_calls: [
      { tool_name: "get_position", status: "ok", duration_ms: 12, error_type: null, args: { symbol: "BTC" }, result: null },
    ],
    tokens_consumed: 9000, input_tokens: 8000, output_tokens: 1000, cache_hit_rate: 0.5,
    wall_time_ms: 5000, llm_call_ms: 4000, model_id: "claude",
    ...overrides,
  };
}

describe("CycleDetailPanel", () => {
  it("渲染推理与决策全文", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    expect(w.text()).toContain("thinking text");
    expect(w.text()).toContain("(1) Stance: hold");
  });

  it("injected_events 为 null 时不渲染该分区", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ injected_events: null }) as any } });
    expect(w.text()).not.toContain("中途注入事件");
  });

  it("injected_events 非空时渲染该分区", () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail({ injected_events: [{ kind: "alert" }] }) as any } });
    expect(w.text()).toContain("中途注入事件");
  });

  it("展开工具表后显示 tool_name 与 duration", async () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    await w.find(".tools-toggle").trigger("click");
    expect(w.text()).toContain("get_position");
    expect(w.text()).toContain("12");
  });

  it("展开后工具 result 为 null 显示诚实空态文案", async () => {
    const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
    await w.find(".tools-toggle").trigger("click");
    expect(w.text()).toContain("结果未持久化");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/CycleDetailPanel.spec.ts`
Expected: FAIL（组件不存在）。

- [ ] **Step 3: 实现 `frontend/src/components/CycleDetailPanel.vue`**

```vue
<script setup lang="ts">
import { computed, ref, h } from "vue";
import { NDataTable, NTag, NSpace } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { CycleDetail, ToolCallRow } from "@/api/client";
import JsonBlock from "@/components/JsonBlock.vue";

const props = defineProps<{ detail: CycleDetail }>();

const hasInjected = computed(() => {
  const e = props.detail.injected_events;
  if (e == null) return false;
  if (Array.isArray(e)) return e.length > 0;
  return true;
});
const toolsOpen = ref(false);
const slowest = computed(() => {
  const ds = props.detail.tool_calls.map((t) => t.duration_ms ?? 0);
  return ds.length ? Math.max(...ds) : 0;
});
const reasoningChars = computed(() => props.detail.reasoning?.length ?? 0);

const toolColumns: DataTableColumns<ToolCallRow> = [
  { title: "工具", key: "tool_name" },
  {
    title: "状态",
    key: "status",
    render: (r) =>
      h(NTag, { size: "small", type: r.status === "ok" ? "success" : "error" }, { default: () => (r.error_type ? `${r.status} · ${r.error_type}` : r.status) }),
  },
  { title: "耗时(ms)", key: "duration_ms" },
  { title: "入参", key: "args", render: (r) => h(JsonBlock, { value: r.args }) },
  {
    title: "结果",
    key: "result",
    render: (r) => (r.result == null ? h("span", { class: "seam" }, "结果未持久化（待后端补全）") : h(JsonBlock, { value: r.result })),
  },
];
</script>

<template>
  <div class="cycle-detail">
    <!-- 1. 头部遥测 chips -->
    <n-space class="chips" :size="6">
      <n-tag size="small">tokens {{ detail.tokens_consumed }}</n-tag>
      <n-tag v-if="detail.input_tokens != null" size="small">in {{ detail.input_tokens }} / out {{ detail.output_tokens }}</n-tag>
      <n-tag v-if="detail.cache_hit_rate != null" size="small">cache {{ (detail.cache_hit_rate * 100).toFixed(0) }}%</n-tag>
      <n-tag v-if="detail.wall_time_ms != null" size="small">wall {{ detail.wall_time_ms }}ms</n-tag>
      <n-tag v-if="detail.model_id" size="small">{{ detail.model_id }}</n-tag>
    </n-space>

    <!-- 2. 触发上下文 -->
    <section><h4>触发上下文</h4><JsonBlock :value="detail.trigger_context" /></section>
    <!-- 3. 中途注入事件（仅非空渲染） -->
    <section v-if="hasInjected"><h4>中途注入事件</h4><JsonBlock :value="detail.injected_events" /></section>
    <!-- 4. 决策时状态 -->
    <section><h4>决策时状态</h4><JsonBlock :value="detail.state_snapshot" /></section>

    <!-- 5. 工具调用（感知），默认折叠 -->
    <section>
      <h4 class="tools-toggle clickable" @click="toolsOpen = !toolsOpen">
        工具调用（{{ detail.tool_calls.length }} 个 · 最慢 {{ slowest }}ms）{{ toolsOpen ? "▾" : "▸" }}
      </h4>
      <n-data-table v-if="toolsOpen" :columns="toolColumns" :data="detail.tool_calls" size="small" :bordered="false" />
    </section>

    <!-- 6. 推理（主角），固定高 + 内部滚 -->
    <section>
      <h4>推理 <span class="muted">（{{ reasoningChars }} 字符）</span></h4>
      <pre class="reasoning">{{ detail.reasoning || "—" }}</pre>
    </section>
    <!-- 7. 决策 -->
    <section><h4>决策</h4><pre class="decision">{{ detail.decision || "—" }}</pre></section>
  </div>
</template>

<style scoped>
.cycle-detail { padding: 8px 4px; }
.chips { margin-bottom: 10px; }
section { margin-bottom: 12px; }
h4 { margin: 0 0 4px; font-size: 13px; opacity: 0.85; }
h4.clickable { cursor: pointer; user-select: none; }
.muted { opacity: 0.5; font-weight: 400; }
:deep(.seam) { font-size: 12px; opacity: 0.5; font-style: italic; }
.reasoning { max-height: 360px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; background: rgba(0, 0, 0, 0.25); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; }
.decision { white-space: pre-wrap; word-break: break-word; background: rgba(96, 165, 250, 0.08); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 0; }
</style>
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/CycleDetailPanel.spec.ts`
Expected: PASS（5 passed）。

> **首个 NDataTable 落地验证（重要）**：本 Task 是全计划第一处用 `NDataTable` 的组件。`NDataTable` 的 tbody 行随 `data` 同步渲染（非虚拟滚动），配合 `test/setup.ts` 的 ResizeObserver stub，预期能在 jsdom 断言到单元格文本（`get_position` / `12` / `结果未持久化`）。**若此步因「行文本不在 DOM」而失败**（ResizeObserver stub 致测量延迟、行未渲染），务必在此修通——同款断言模式 Task 12（TradesTable）会复用，别拖到那时才暴露。Fallback：若确认 jsdom 无法稳定渲染 NDataTable 行文本，把受影响的文本断言降级为「断言组件挂载 + 传入 `data`/`columns` 的条目数」，真实文本呈现交 DoD 手动验收覆盖。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CycleDetailPanel.vue frontend/test/CycleDetailPanel.spec.ts
git commit -m "feat(webui): cycle 详情五维分区 + NDataTable 工具表 + result 诚实空态

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: DecisionStream（NCollapse 手风琴）+ CycleRowHeader

**Files:**
- Create: `frontend/src/components/CycleRowHeader.vue`
- Create: `frontend/src/components/DecisionStream.vue`
- Test: `frontend/test/DecisionStream.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/DecisionStream.spec.ts`**

```typescript
import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";
import DecisionStream from "@/components/DecisionStream.vue";

function cyc(id: number) {
  return { id, cycle_label: `c${id}`, triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z", decision_head: `head${id}`, tokens_consumed: 1, wall_time_ms: 1, execution_status: "ok" };
}
function det(id: number) {
  return { id, cycle_label: `c${id}`, triggered_by: "scheduled", created_at: "2026-06-12T10:00:00Z", reasoning: "r", decision: "d", trigger_context: null, state_snapshot: null, injected_events: null, tool_calls: [], tokens_consumed: 1, input_tokens: null, output_tokens: null, cache_hit_rate: null, wall_time_ms: null, llm_call_ms: null, model_id: null };
}

function mountStream() {
  const wrapper = mount(DecisionStream, {
    global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
  });
  const store = useSessionsStore();
  store.cycles = [cyc(3), cyc(2), cyc(1)] as any;
  return { wrapper, store };
}

describe("DecisionStream", () => {
  it("按 store.cycles 顺序渲染每条 cycle 表头", async () => {
    const { wrapper } = mountStream();
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("head3");
    expect(wrapper.text()).toContain("head1");
  });

  it("点击折叠项表头调 store.expandCycle(id)", async () => {
    const { wrapper, store } = mountStream();
    await wrapper.vm.$nextTick();
    await wrapper.find(".n-collapse-item__header").trigger("click");
    expect(store.expandCycle).toHaveBeenCalledWith(3);
  });

  it("expandedCycleId 命中且详情已缓存时仅渲染一个详情面板", async () => {
    const { wrapper, store } = mountStream();
    store.expandedCycleId = 2;
    store.cycleDetails = new Map([[2, det(2)]]) as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".cycle-detail").length).toBe(1);
  });

  it("无 cycle 时显示空态", async () => {
    const { wrapper, store } = mountStream();
    store.cycles = [] as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("暂无决策");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/DecisionStream.spec.ts`
Expected: FAIL（组件不存在）。

- [ ] **Step 3: 实现 `frontend/src/components/CycleRowHeader.vue`**（纯展示折叠行，用于 NCollapseItem 的 #header 槽）

```vue
<script setup lang="ts">
import type { CycleRow } from "@/api/client";
import { NTag } from "naive-ui";
import { fmtLocal } from "@/utils/time";

defineProps<{ cycle: CycleRow }>();
</script>

<template>
  <div class="cycle-head">
    <span class="time">{{ fmtLocal(cycle.created_at) }}</span>
    <n-tag size="small" :bordered="false">{{ cycle.triggered_by }}</n-tag>
    <span class="head">{{ cycle.decision_head || "—" }}</span>
    <n-tag size="small" :type="cycle.execution_status === 'ok' ? 'default' : 'error'" :bordered="false">
      {{ cycle.execution_status }}
    </n-tag>
    <span class="tele">{{ cycle.tokens_consumed }}tok · {{ cycle.wall_time_ms ?? "?" }}ms</span>
  </div>
</template>

<style scoped>
.cycle-head { display: flex; align-items: center; gap: 10px; width: 100%; font-size: 13px; }
.time { opacity: 0.7; white-space: nowrap; }
.head { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tele { font-size: 11px; opacity: 0.5; white-space: nowrap; }
</style>
```

- [ ] **Step 4: 实现 `frontend/src/components/DecisionStream.vue`**（NCollapse accordion，store 单向驱动 + @update 调 expandCycle）

```vue
<script setup lang="ts">
import { computed } from "vue";
import { NCollapse, NCollapseItem } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import CycleRowHeader from "@/components/CycleRowHeader.vue";
import CycleDetailPanel from "@/components/CycleDetailPanel.vue";

const store = useSessionsStore();
const cycles = computed(() => store.cycles);

// 单向：从 store 派生展开项；accordion 模式下至多一项
const expandedNames = computed<number[]>(() =>
  store.expandedCycleId != null ? [store.expandedCycleId] : [],
);

function onUpdate(names: Array<string | number>) {
  const next = names.length ? Number(names[0]) : null;
  if (next == null) {
    // 关闭当前项：expandCycle 同 id toggle 成 null
    if (store.expandedCycleId != null) void store.expandCycle(store.expandedCycleId);
  } else if (next !== store.expandedCycleId) {
    void store.expandCycle(next); // 打开：设 id + 懒加载
  }
}

const detailFor = (id: number) => store.cycleDetails.get(id);
</script>

<template>
  <div class="decision-stream">
    <n-collapse accordion :expanded-names="expandedNames" @update:expanded-names="onUpdate">
      <n-collapse-item v-for="c in cycles" :key="c.id" :name="c.id">
        <template #header><CycleRowHeader :cycle="c" /></template>
        <CycleDetailPanel v-if="detailFor(c.id)" :detail="detailFor(c.id)!" />
        <div v-else class="loading">加载详情…</div>
      </n-collapse-item>
    </n-collapse>
    <div v-if="!cycles.length" class="empty">暂无决策</div>
  </div>
</template>

<style scoped>
.decision-stream { padding: 4px 8px; }
.loading { padding: 12px; opacity: 0.5; font-size: 13px; }
.empty { padding: 24px; text-align: center; opacity: 0.5; font-size: 13px; }
</style>
```

- [ ] **Step 5: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/DecisionStream.spec.ts`
Expected: PASS（4 passed）。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/CycleRowHeader.vue frontend/src/components/DecisionStream.vue frontend/test/DecisionStream.spec.ts
git commit -m "feat(webui): NCollapse 手风琴决策流（只开一条 + 懒加载详情）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: SessionMeta（NDescriptions，消费 detail）+ LiveStatusCard

**Files:**
- Create: `frontend/src/components/SessionMeta.vue`
- Create: `frontend/src/components/LiveStatusCard.vue`
- Test: `frontend/test/SessionMeta.spec.ts`
- Test: `frontend/test/LiveStatusCard.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/SessionMeta.spec.ts`**

```typescript
import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";
import SessionMeta from "@/components/SessionMeta.vue";

describe("SessionMeta", () => {
  it("消费 store.detail 展示周期与调度间隔", async () => {
    const wrapper = mount(SessionMeta, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.detail = { id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1h", scheduler_interval_min: 15, initial_balance: 10000, token_budget: 200000, created_at: "2026-06-12T10:00:00Z", last_active_at: null } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("1h");
    expect(wrapper.text()).toContain("15");
    expect(wrapper.text()).toContain("200000");
  });

  it("detail 为空时不渲染", () => {
    const wrapper = mount(SessionMeta, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    expect(wrapper.find(".session-meta").exists()).toBe(false);
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/SessionMeta.spec.ts`
Expected: FAIL（组件不存在）。

- [ ] **Step 3: 实现 `frontend/src/components/SessionMeta.vue`**（NDescriptions 消费 `store.detail`，消灭 dead state）

```vue
<script setup lang="ts">
import { computed } from "vue";
import { NDescriptions, NDescriptionsItem } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";

const store = useSessionsStore();
const d = computed(() => store.detail);
</script>

<template>
  <n-descriptions v-if="d" :column="5" size="small" label-placement="left" class="session-meta" bordered>
    <n-descriptions-item label="Symbol">{{ d.symbol }}</n-descriptions-item>
    <n-descriptions-item label="周期">{{ d.timeframe }}</n-descriptions-item>
    <n-descriptions-item label="调度间隔">{{ d.scheduler_interval_min }}min</n-descriptions-item>
    <n-descriptions-item label="初始余额">{{ d.initial_balance }}</n-descriptions-item>
    <n-descriptions-item label="Token 预算">{{ d.token_budget }}</n-descriptions-item>
  </n-descriptions>
</template>

<style scoped>
.session-meta { padding: 6px 16px; }
</style>
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/SessionMeta.spec.ts`
Expected: PASS（2 passed）。

- [ ] **Step 5: 写失败测试 `frontend/test/LiveStatusCard.spec.ts`**

```typescript
import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";
import LiveStatusCard from "@/components/LiveStatusCard.vue";

function mountCard() {
  const wrapper = mount(LiveStatusCard, {
    global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
  });
  return { wrapper, store: useSessionsStore() };
}

describe("LiveStatusCard", () => {
  it("有持仓时显示方向与张数", async () => {
    const { wrapper, store } = mountCard();
    store.live = { status: "active", last_active_at: "2026-06-12T10:00:00Z", position: { symbol: "BTC/USDT:USDT", side: "long", contracts: 1.5, entry_price: 63000, leverage: 5 }, open_orders: [], active_alerts: [] } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("long");
    expect(wrapper.text()).toContain("1.5");
  });

  it("无持仓显示空仓", async () => {
    const { wrapper, store } = mountCard();
    store.live = { status: "active", last_active_at: null, position: null, open_orders: [], active_alerts: [] } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("空仓");
  });

  it("pollFailCount≥3 显示轮询中断角标", async () => {
    const { wrapper, store } = mountCard();
    store.live = { status: "active", last_active_at: null, position: null, open_orders: [], active_alerts: [] } as any;
    store.pollFailCount = 3;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("轮询中断");
  });
});
```

- [ ] **Step 6: 实现 `frontend/src/components/LiveStatusCard.vue`**（NCard + NTag + NSpace）

```vue
<script setup lang="ts">
import { computed } from "vue";
import { NCard, NTag, NSpace } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import { fmtLocal } from "@/utils/time";

const store = useSessionsStore();
const live = computed(() => store.live);
const stalled = computed(() => store.pollFailCount >= 3);
</script>

<template>
  <n-card v-if="live" size="small" :bordered="false" class="status-card">
    <n-space align="center" :size="18">
      <n-tag :type="live.status === 'active' ? 'success' : 'warning'" size="small" round>{{ live.status }}</n-tag>
      <span class="muted">@ {{ fmtLocal(live.last_active_at) }}</span>
      <template v-if="live.position">
        <span class="label">持仓</span>
        <span class="pos" :class="live.position.side">{{ live.position.side }}</span>
        <span>{{ live.position.contracts }} @ {{ live.position.entry_price }} ×{{ live.position.leverage }}</span>
      </template>
      <span v-else class="muted">空仓</span>
      <span><span class="label">挂单</span> {{ live.open_orders.length }}</span>
      <span><span class="label">提醒</span> {{ live.active_alerts.length }}</span>
      <n-tag v-if="stalled" type="warning" size="small">⚠ 轮询中断</n-tag>
    </n-space>
  </n-card>
  <n-card v-else size="small" :bordered="false" class="status-card"><span class="muted">未选择会话</span></n-card>
</template>

<style scoped>
.status-card :deep(.n-card__content) { padding: 8px 16px; font-size: 13px; }
.label { opacity: 0.55; }
.muted { opacity: 0.5; }
.pos.long { color: #4ade80; }
.pos.short { color: #f87171; }
</style>
```

- [ ] **Step 7: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/SessionMeta.spec.ts test/LiveStatusCard.spec.ts`
Expected: PASS（5 passed）。

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/SessionMeta.vue frontend/src/components/LiveStatusCard.vue frontend/test/SessionMeta.spec.ts frontend/test/LiveStatusCard.spec.ts
git commit -m "feat(webui): 会话元信息条（NDescriptions 消费 detail）+ 实时状态卡

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: 表现概览（EquityChart + TradesTable + PerformanceBar）

**Files:**
- Create: `frontend/src/components/EquityChart.vue`
- Create: `frontend/src/components/TradesTable.vue`
- Create: `frontend/src/components/PerformanceBar.vue`
- Test: `frontend/test/EquityChart.spec.ts`
- Test: `frontend/test/TradesTable.spec.ts`
- Test: `frontend/test/PerformanceBar.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/EquityChart.spec.ts`**（测纯映射助手 + mock 图表库冒烟）

```typescript
import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  })),
}));

import EquityChart, { toSeriesData } from "@/components/EquityChart.vue";

describe("toSeriesData", () => {
  it("ISO→秒级时间戳并升序", () => {
    const d = toSeriesData([
      { at: "2026-06-12T10:01:00Z", equity: 101 },
      { at: "2026-06-12T10:00:00Z", equity: 100 },
    ]);
    expect(d.map((x) => x.value)).toEqual([100, 101]);
    expect(d[0].time < d[1].time).toBe(true);
  });

  it("同秒去重保留最后一个", () => {
    const d = toSeriesData([
      { at: "2026-06-12T10:00:00Z", equity: 100 },
      { at: "2026-06-12T10:00:00Z", equity: 105 },
    ]);
    expect(d.length).toBe(1);
    expect(d[0].value).toBe(105);
  });
});

describe("EquityChart", () => {
  it("挂载不抛（图表库已 mock）", () => {
    const w = mount(EquityChart, { props: { points: [{ at: "2026-06-12T10:00:00Z", equity: 100 }] } });
    expect(w.find(".equity-chart").exists()).toBe(true);
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/EquityChart.spec.ts`
Expected: FAIL（组件不存在）。

- [ ] **Step 3: 实现 `frontend/src/components/EquityChart.vue`**

```vue
<script lang="ts">
import type { EquityPoint } from "@/api/client";
import { epochSec } from "@/utils/time";
import type { UTCTimestamp } from "lightweight-charts";

/** 逐 cycle 盯市点 → lightweight-charts line data。秒级 UTCTimestamp、升序、同秒去重保留最后。 */
export function toSeriesData(points: EquityPoint[]): { time: UTCTimestamp; value: number }[] {
  const byTime = new Map<number, number>();
  for (const p of points) byTime.set(epochSec(p.at), p.equity);
  return [...byTime.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time: time as UTCTimestamp, value }));
}
</script>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import { createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";

const props = defineProps<{ points: EquityPoint[] }>();
const el = ref<HTMLElement | null>(null);
let chart: IChartApi | null = null;
let series: ISeriesApi<"Line"> | null = null;

function render() {
  if (series) series.setData(toSeriesData(props.points));
  chart?.timeScale().fitContent();
}

onMounted(() => {
  if (!el.value) return;
  chart = createChart(el.value, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#9ca3af" },
    grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.05)" } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true },
  });
  series = chart.addLineSeries({ color: "#4ade80", lineWidth: 2 });
  render();
});

watch(() => props.points, render, { deep: true });

onUnmounted(() => {
  chart?.remove();
  chart = null;
  series = null;
});
</script>

<template>
  <div ref="el" class="equity-chart"></div>
</template>

<style scoped>
.equity-chart { width: 100%; height: 120px; }
</style>
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/EquityChart.spec.ts`
Expected: PASS（3 passed）。

- [ ] **Step 5: 写失败测试 `frontend/test/TradesTable.spec.ts`**

```typescript
import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import TradesTable from "@/components/TradesTable.vue";

describe("TradesTable", () => {
  it("渲染成交行", () => {
    const w = mount(TradesTable, {
      props: { trades: [{ at: "2026-06-12T10:00:00Z", action: "open", side: "long", price: 63000, amount: 1, pnl: 50, fee: 1 }] },
    });
    expect(w.text()).toContain("open");
    expect(w.text()).toContain("63000");
  });
});
```

- [ ] **Step 6: 实现 `frontend/src/components/TradesTable.vue`**（NDataTable）

```vue
<script setup lang="ts">
import { computed, h } from "vue";
import { NDataTable } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { TradeRow } from "@/api/client";
import { fmtLocal } from "@/utils/time";

const props = defineProps<{ trades: TradeRow[] }>();

const columns: DataTableColumns<TradeRow> = [
  { title: "时间", key: "at", render: (r) => fmtLocal(r.at) },
  { title: "动作", key: "action" },
  { title: "方向", key: "side", render: (r) => r.side ?? "—" },
  { title: "价格", key: "price", render: (r) => (r.price ?? "—").toString() },
  { title: "数量", key: "amount", render: (r) => (r.amount ?? "—").toString() },
  {
    title: "PnL",
    key: "pnl",
    render: (r) => h("span", { class: (r.pnl ?? 0) > 0 ? "pos" : (r.pnl ?? 0) < 0 ? "neg" : "" }, String(r.pnl ?? "—")),
  },
  { title: "费", key: "fee", render: (r) => (r.fee ?? "—").toString() },
];
const data = computed(() => props.trades);
</script>

<template>
  <n-data-table :columns="columns" :data="data" size="small" :bordered="false" :max-height="220" />
</template>

<style scoped>
:deep(.pos) { color: #4ade80; }
:deep(.neg) { color: #f87171; }
</style>
```

- [ ] **Step 7: 实现 `frontend/src/components/PerformanceBar.vue`**（NStatistic 指标 + 双口径标注必须）

```vue
<script setup lang="ts">
import { computed, ref } from "vue";
import { NStatistic, NTag, NSpace, NButton } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import EquityChart from "@/components/EquityChart.vue";
import TradesTable from "@/components/TradesTable.vue";

const store = useSessionsStore();
const perf = computed(() => store.performance);
const showTrades = ref(false);
</script>

<template>
  <div v-if="perf" class="perf-bar">
    <div class="left">
      <div class="caliper">净值曲线 <n-tag size="tiny" :bordered="false">盯市·含未实现PnL</n-tag></div>
      <EquityChart :points="perf.equity_curve" />
    </div>
    <div class="right">
      <n-space :size="16" wrap>
        <n-statistic label="总回报">
          <span :class="{ neg: perf.total_return_pct < 0 }">{{ perf.total_return_pct.toFixed(2) }}%</span>
          <template #suffix><n-tag size="tiny" :bordered="false">gross已实现</n-tag></template>
        </n-statistic>
        <n-statistic label="净PnL" :value="perf.net_pnl.toFixed(2)" />
        <n-statistic label="净胜率" :value="(perf.net_win_rate * 100).toFixed(1) + '%'" />
        <n-statistic label="最大回撤">
          <span class="neg">{{ perf.max_drawdown_pct.toFixed(2) }}%</span>
          <template #suffix><n-tag size="tiny" :bordered="false">net已实现equity</n-tag></template>
        </n-statistic>
        <n-statistic label="总交易" :value="perf.total_trades" />
      </n-space>
      <div class="note">曲线为盯市口径，与上方已实现指标不同口径、不可逐点对账。</div>
      <n-button text size="small" @click="showTrades = !showTrades">
        {{ showTrades ? "收起成交表 ▾" : `成交表（${perf.trades.length}）▸` }}
      </n-button>
    </div>
    <div v-if="showTrades" class="trades-wrap"><TradesTable :trades="perf.trades" /></div>
  </div>
</template>

<style scoped>
.perf-bar { border-top: 1px solid rgba(255, 255, 255, 0.08); padding: 8px 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 16px; max-height: 40vh; overflow-y: auto; }
.left { min-width: 0; }
.caliper { font-size: 12px; opacity: 0.75; margin-bottom: 4px; }
.note { font-size: 11px; opacity: 0.5; margin-top: 6px; }
.neg { color: #f87171; }
.trades-wrap { grid-column: 1 / -1; margin-top: 8px; }
</style>
```

- [ ] **Step 8: 写失败测试 `frontend/test/PerformanceBar.spec.ts`**

```typescript
import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    remove: vi.fn(),
  })),
}));

import PerformanceBar from "@/components/PerformanceBar.vue";

describe("PerformanceBar", () => {
  it("显示指标且带双口径标注", async () => {
    const wrapper = mount(PerformanceBar, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
    });
    const store = useSessionsStore();
    store.performance = { initial_balance: 10000, current_position: "flat", total_return_pct: 2.5, net_pnl: 250, net_win_rate: 0.6, max_drawdown_pct: -3.2, net_profit_factor: 1.5, total_trades: 10, net_winning_trades: 6, net_losing_trades: 4, total_fees: 12, equity_curve: [{ at: "2026-06-12T10:00:00Z", equity: 10000 }], trades: [] } as any;
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("2.50%");
    expect(wrapper.text()).toContain("盯市");
    expect(wrapper.text()).toContain("不可逐点对账");
  });
});
```

- [ ] **Step 9: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/EquityChart.spec.ts test/TradesTable.spec.ts test/PerformanceBar.spec.ts`
Expected: PASS（5 passed）。

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/EquityChart.vue frontend/src/components/TradesTable.vue frontend/src/components/PerformanceBar.vue frontend/test/EquityChart.spec.ts frontend/test/TradesTable.spec.ts frontend/test/PerformanceBar.spec.ts
git commit -m "feat(webui): 表现概览（净值曲线 + NStatistic 双口径指标 + NDataTable 成交表）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: DashboardView 装配 + 路由联动 + 轮询接入

**Files:**
- Modify: `frontend/src/views/DashboardView.vue`（补全主区装配）
- Test: `frontend/test/DashboardView.spec.ts`

- [ ] **Step 1: 写失败测试 `frontend/test/DashboardView.spec.ts`**

```typescript
import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { createRouter, createWebHashHistory } from "vue-router";
import { useSessionsStore } from "@/stores/sessions";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    remove: vi.fn(),
  })),
}));

import DashboardView from "@/views/DashboardView.vue";

function makeRouter() {
  return createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: "/", name: "home", component: DashboardView },
      { path: "/sessions/:id", name: "session", component: DashboardView, props: true },
    ],
  });
}

describe("DashboardView", () => {
  it("home 路由（无 id）显示请选择会话提示", async () => {
    const router = makeRouter();
    router.push("/");
    await router.isReady();
    const wrapper = mount(DashboardView, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true }), router] },
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("请选择会话");
  });

  it("带 id 路由时调 selectSession", async () => {
    const router = makeRouter();
    router.push("/sessions/sim19");
    await router.isReady();
    const wrapper = mount(DashboardView, {
      global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true }), router] },
      props: { id: "sim19" },
    });
    const store = useSessionsStore();
    await wrapper.vm.$nextTick();
    expect(store.selectSession).toHaveBeenCalledWith("sim19");
  });
});
```

- [ ] **Step 2: 跑测试验证失败**

Run（在 `frontend/`）：`npx vitest run test/DashboardView.spec.ts`
Expected: FAIL（占位 DashboardView 不含上述行为）。

- [ ] **Step 3: 补全 `frontend/src/views/DashboardView.vue`**

```vue
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
```

- [ ] **Step 4: 跑测试验证通过**

Run（在 `frontend/`）：`npx vitest run test/DashboardView.spec.ts`
Expected: PASS（2 passed）。

- [ ] **Step 5: 跑全量测试 + 类型检查确认无回归**

Run（在 `frontend/`）：`npm run test && npx vue-tsc --noEmit`
Expected: 全部测试 PASS；`vue-tsc` 无类型错误。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/DashboardView.vue frontend/test/DashboardView.spec.ts
git commit -m "feat(webui): DashboardView 主区装配（元信息/状态卡/决策流/表现）+ 路由联动 + 轮询接入

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 14: 生产构建验证 + README

**Files:**
- Create: `frontend/README.md`
- 验证：`frontend/dist`（构建产物，已 gitignore，不提交）

- [ ] **Step 1: 生产构建**

Run（在 `frontend/`）：`npm run build`
Expected: `vue-tsc --noEmit` 通过 + 构建成功，产物落 `frontend/dist/`（含 `index.html` + `assets/`）。
（若此处报 `composite`/`noEmit` 相关 tsconfig 摩擦，按 Task 1 已采用的 canonical 写法即可避免；如仍出现，确认 `tsconfig.node.json` 未设 `noEmit`。）

- [ ] **Step 2: 验证后端能挂载构建产物（同源 prod 路径）**

Run（在仓库根，需已安装 `[webui]` extra）：

```bash
python -c "
from src.webui.app import create_app
from fastapi.testclient import TestClient
c = TestClient(create_app())
r = c.get('/')
print('root', r.status_code, r.headers.get('content-type'))
"
```

Expected: `root 200 text/html`（`app.py` 的 `StaticFiles(frontend/dist, html=True)` 命中 `index.html`）。
说明：hash mode 下深链如 `/#/sessions/sim19` 不经服务端路由——浏览器只请求 `/`，刷新必命中 `index.html`，无需后端 SPA fallback。

- [ ] **Step 3: 写 `frontend/README.md`**

```markdown
# TradeBot WebUI 前端（Phase 1b）

只读观察台前端 SPA。消费 `src/webui/` 的 6 个只读端点，不向 agent 发指令。UI 用 Naive UI 构建。

## 开发

​```bash
cd frontend
npm install
npm run dev          # :5173，/api 代理到 http://127.0.0.1:8000
​```

后端另起：`python -m src.webui`（默认 :8000，读 `data/tradebot.db`）。

## 类型生成

类型从后端 OpenAPI 生成，后端 schema 为唯一真相源：

​```bash
# 1. 从仓库根 dump openapi（无需真实 DB）
python -c "import json; from src.webui.app import create_app; print(json.dumps(create_app().openapi(), ensure_ascii=False))" > frontend/openapi.json
# 2. 生成 types.ts（勿手改）
cd frontend && npm run gen:types
​```

## 构建与部署

​```bash
npm run build        # → frontend/dist
​```

`src/webui/app.py` 在 `/` 同源挂载 `frontend/dist`（`StaticFiles(html=True)`）。
路由用 hash mode（`/#/sessions/:id`），刷新深链必命中 `index.html`，后端零改。

## 测试

​```bash
npm run test         # Vitest（逻辑层单测 + 组件冒烟）
​```
```

> 注：上面 README 代码块的围栏字符在写入文件时用普通三反引号；此处用全角符号 `​``` ` 仅为在本 plan 内规避嵌套围栏冲突。落地 README 时务必改回三个半角反引号。

- [ ] **Step 4: Commit**

```bash
git add frontend/README.md
git commit -m "docs(webui): Phase 1b 前端 README（开发/类型生成/构建/测试）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 完成标准（DoD）

- [ ] `npm run test` 全绿（client/time/store/usePolling + 各组件冒烟 + DashboardView 联动）。
- [ ] `npm run build` 成功，`vue-tsc --noEmit` 无类型错误（**真实契约关卡**：吃生成的 `types.ts`，捕获单测 `as any` 掩盖的字段错配——见审查 Finding 4，不可因单测全绿跳过）。
- [ ] 后端 `TestClient.get('/')` 在有 `frontend/dist` 时返 200 text/html。
- [ ] 手动验收（**真实契约关卡**；后端跑真实 `data/tradebot.db`，`npm run dev`）：
  - 左栏列出会话，点选切换、URL 同步 `/#/sessions/:id`，刷新保持。
  - 会话元信息条展示 symbol/周期/调度间隔/初始余额/token 预算（消费 `detail`）。
  - 决策流按 id 倒序、手风琴只开一条、展开懒加载详情、五维分区齐全、工具 result 显示「结果未持久化」空态。
  - 状态卡显示 status/持仓/挂单/提醒；active 会话 5s 增量出新 cycle；切到后台标签暂停轮询。
  - 表现条净值曲线 + 双口径标注可见，无需滚页。
- [ ] 全程未改 `src/`（除 Phase 1a 已落的契约层）。

## 后续（非本期，记录于 spec §后续）

1. **【第一优先后端项】工具调用结果持久化**：`tool_calls` 加 `result` 列 + 执行层 capture；落地后本期 `ToolCallRow.result` seam 与 UI 空态自动点亮。
2. 底部表现面板可拖拽调高 / 折叠（VS Code 式）。
3. Phase 2：多会话并发编排器。
4. Phase 3：WebUI 接管会话创建/控制。
