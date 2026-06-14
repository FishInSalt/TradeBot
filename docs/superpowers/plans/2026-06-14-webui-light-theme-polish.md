# WebUI 观察台浅色主题 + 可读性打磨 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实施。步骤用 checkbox（`- [ ]`）跟踪。

**Goal:** 把观察台从全黑主题换成浅灰底+白卡浅色主题，并完成工具头函数式化、快照置顶+格式化、数值/文案修缮——全部纯前端，零后端/零 schema/零迁移。

**Architecture:** 单一来源设计令牌（`tokens.css` 的 `:root{--ob-*}` + `.ob-card`），各组件 scoped CSS 引用令牌；逻辑改动（工具头/快照格式化/数值 helper）走 TDD；CSS 主题迁移按已对照 HEAD 的 spec 枚举逐文件 swap；收尾 Playwright 按面量化对比度。

**Tech Stack:** Vue 3 SPA、naive-ui（pin 2.38.1，勿 npm update）、Pinia、vitest、TypeScript、lightweight-charts。

**设计 spec:** `docs/superpowers/specs/2026-06-14-webui-light-theme-polish-design.md`（权威色值/锚点枚举）。所有命令在 `frontend/` 下执行（除非另注）。分支 `iter-webui-light-theme-polish`。

---

## Task 1: 设计令牌基座 + lightTheme 切换

奠定 `--ob-*` 令牌与 `.ob-card`；切 App 到 lightTheme 并把内容区底设为浅灰。后续所有任务引用这些令牌。

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Modify: `frontend/src/main.ts`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: 创建 tokens.css**

```css
/* 观察台浅色设计令牌（单一来源，spec §3.2）。对比度均按白卡底 #ffffff 计——
   彩字/正文经 §4 卡片化落白卡，故达标口径成立（见 spec §3.2 对比度基准注）。 */
:root {
  --ob-page-bg: #eef0f3;
  --ob-card-bg: #ffffff;
  --ob-card-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  --ob-block-bg: #f6f7f9;
  --ob-border: #e5e7eb;
  --ob-text-muted: #6b7280;
  --ob-accent: #2563eb;
  --ob-accent-soft: #eff6ff;
  --ob-thinking-border: #93c5fd;
  --ob-pos: #15803d;       /* 正向：白底 ≈5:1（文本 AA）/ ≥3:1（图形线） */
  --ob-neg: #dc2626;       /* 负向：白底 ≈4.8:1 */
  --ob-warn: #b45309;      /* 告警/未解析文本：白底 ≈5:1 */
  --ob-warn-soft: #fef3c7; /* 注入卡背景：浅琥珀 */
}

/* 统一白卡（spec §4）：dashboard 表面 + CycleDetailPanel 区块共用。
   注（审查 6）：带自身 padding 的组件根（.perf-bar/.session-meta-wrap/.decision-stream）其 scoped
   选择器含 [data-v-x] 属性、特异性 (0,2,0) 高于本全局类 (0,1,0)，故组件自身 padding 按特异性
   确定性胜出（与注入顺序无关）；.ob-card 实际只贡献 bg/radius/shadow/margin。 */
.ob-card {
  background: var(--ob-card-bg);
  border-radius: 8px;
  padding: 10px 12px;
  box-shadow: var(--ob-card-shadow);
  margin-bottom: 10px;
}
```

- [ ] **Step 2: main.ts 引入 tokens.css**

在 `frontend/src/main.ts` 顶部 import 之后加一行（全局非 scoped 样式，须在挂载前引入）：

```ts
import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "@/App.vue";
import { router } from "@/router";
import "@/styles/tokens.css";

createApp(App).use(createPinia()).use(router).mount("#app");
```

- [ ] **Step 3: App.vue 切 lightTheme + 内容区浅灰底**

`frontend/src/App.vue`：import 由 `darkTheme` 改 `lightTheme`，`:theme` 同步；scoped 样式加内容区底覆盖（naive-ui 2.38.1 footgun：`n-layout-content` 自带 `--n-color` 白底，须 `:deep` 覆盖，spec LOW）。

script 部分：
```ts
import {
  NConfigProvider,
  NGlobalStyle,
  NLayout,
  NLayoutHeader,
  NLayoutSider,
  NLayoutContent,
  lightTheme,
} from "naive-ui";
```
template 顶：
```html
<n-config-provider :theme="lightTheme">
```
scoped `<style>` 末尾追加：
```css
.app-shell :deep(.n-layout-content),
.app-shell :deep(.n-layout-content__main) {
  background: var(--ob-page-bg);
}
```

- [ ] **Step 4: 验证构建（CSS 改动 vitest 不覆盖，靠 build + 收尾 Playwright）**

Run: `cd frontend && npx vue-tsc --noEmit && npm run build`
Expected: 0 error，dist 生成。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles/tokens.css frontend/src/main.ts frontend/src/App.vue
git commit -m "$(cat <<'EOF'
feat(webui): 浅色主题基座 — tokens.css 设计令牌 + lightTheme 切换

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: format.ts 新增 clipArgs / fmtNum / fmtSigned

纯函数，full TDD。`HEAD_ARGS_MAX` 在此单一定义（spec §6.2 / Minor 2）。

**Files:**
- Modify: `frontend/src/utils/format.ts`
- Test: `frontend/test/format.spec.ts`

- [ ] **Step 1: 写失败测试**

`frontend/test/format.spec.ts` 已存在（`:2` 现为 `import { fmtTokens, fmtDuration, fmtArgs } from "@/utils/format";`）。**把 `:2` 合并为单行**（避免重复 import，审查 4）：

```ts
import { fmtTokens, fmtDuration, fmtArgs, clipArgs, fmtNum, fmtSigned, HEAD_ARGS_MAX } from "@/utils/format";
```

并在文件末尾追加：

```ts
describe("clipArgs", () => {
  it("空/无参 → text='' clipped=false（头渲 name()）", () => {
    expect(clipArgs(null)).toEqual({ text: "", clipped: false });
    expect(clipArgs({})).toEqual({ text: "", clipped: false });
  });
  it("短参 → 原串 clipped=false", () => {
    expect(clipArgs({ timeframe: "1h", candle_count: 30 })).toEqual({
      text: "timeframe=1h, candle_count=30", clipped: false,
    });
  });
  it("长参 → 截断到 HEAD_ARGS_MAX + … clipped=true", () => {
    const r = clipArgs({ content: "x".repeat(100) });
    expect(r.clipped).toBe(true);
    expect(r.text.endsWith("…")).toBe(true);
    expect(r.text.length).toBe(HEAD_ARGS_MAX + 1); // 60 + '…'
  });
  it("嵌套值回退 JSON 串", () => {
    expect(clipArgs({ a: { b: 1 } })).toEqual({ text: 'a={"b":1}', clipped: false });
  });
});

describe("fmtNum", () => {
  it("千分位", () => expect(fmtNum(63896)).toBe("63,896"));
  it("小数位裁剪", () => expect(fmtNum(17.999, 2)).toBe("18"));
  it("null → —", () => expect(fmtNum(null)).toBe("—"));
});

describe("fmtSigned", () => {
  it("负值带 − 号", () => expect(fmtSigned(-42.5)).toBe("−42.5"));
  it("正值带 + 号", () => expect(fmtSigned(120)).toBe("+120"));
  it("null → —", () => expect(fmtSigned(null)).toBe("—"));
});
```

- [ ] **Step 2: 运行验证失败**

Run: `cd frontend && npx vitest run test/format.spec.ts`
Expected: FAIL（`clipArgs`/`fmtNum`/`fmtSigned`/`HEAD_ARGS_MAX` 未导出）。

- [ ] **Step 3: 实现**

在 `frontend/src/utils/format.ts` 追加：

```ts
/** 工具头函数式参数截断阈值（单一定义，spec §6）。 */
export const HEAD_ARGS_MAX = 60;

/** 工具头 `name(参数)` 用。空/无参 → text=''（头渲 name()）；超阈值截断 + clipped=true。 */
export function clipArgs(args: unknown): { text: string; clipped: boolean } {
  if (args == null) return { text: "", clipped: false };
  let s: string;
  if (typeof args !== "object" || Array.isArray(args)) {
    s = JSON.stringify(args);
  } else {
    const entries = Object.entries(args as Record<string, unknown>);
    if (!entries.length) return { text: "", clipped: false };
    s = entries
      .map(([k, v]) => `${k}=${typeof v === "object" && v !== null ? JSON.stringify(v) : v}`)
      .join(", ");
  }
  if (s.length > HEAD_ARGS_MAX) return { text: s.slice(0, HEAD_ARGS_MAX) + "…", clipped: true };
  return { text: s, clipped: false };
}

/** 千分位数值，null → —（spec §8）。 */
export function fmtNum(n: number | null | undefined, maxFrac = 2): string {
  if (n == null) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: maxFrac });
}

/** 带正负号数值（U+2212 − 匹配 spec 示例），null → —。 */
export function fmtSigned(n: number | null | undefined, maxFrac = 2): string {
  if (n == null) return "—";
  const s = Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: maxFrac });
  return n < 0 ? `−${s}` : `+${s}`;
}
```

- [ ] **Step 4: 运行验证通过**

Run: `cd frontend && npx vitest run test/format.spec.ts`
Expected: PASS（全绿）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/format.ts frontend/test/format.spec.ts
git commit -m "$(cat <<'EOF'
feat(webui): format util 新增 clipArgs/fmtNum/fmtSigned

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: ReactTimeline 工具头函数式 + 去参数重复 + CSS 迁移

**Files:**
- Modify: `frontend/src/components/ReactTimeline.vue`
- Test: `frontend/test/ReactTimeline.spec.ts`

- [ ] **Step 1: 改测试（既有「args 紧凑单行」断言改为函数式头 + 去重复）**

替换 `frontend/test/ReactTimeline.spec.ts` 中 `§议题5 工具卡 args 紧凑单行 + duration 友好` 那个 `it(...)` 为以下两个：

```ts
it("§④ 工具头函数式：短参 name(k=v) + 展开体只给结果（不重复入参）", async () => {
  const p = {
    ...baseProps(),
    steps: [{ thinking: null, tools: [{ tool_call_id: "call_a", tool_name: "get_market_data" }] }],
    toolCalls: [{ tool_name: "get_market_data", status: "ok", duration_ms: 1500, error_type: null,
                  args: { timeframe: "1h", candle_count: 30 }, result: "ok", tool_call_id: "call_a" }],
  };
  const w = mount(ReactTimeline, { props: p as any });
  expect(w.text()).toContain("get_market_data(timeframe=1h, candle_count=30)"); // 头部函数式
  expect(w.text()).toContain("1.5s");
  await w.findAll(".tool-card .tool-head")[0].trigger("click");
  expect(w.text()).toContain("结果");
  expect(w.text()).not.toContain("入参");      // 短参：展开体不重复入参
});

it("§④ 长参头部截断 …，展开体补完整入参", async () => {
  const long = "y".repeat(80);
  const p = {
    ...baseProps(),
    steps: [{ thinking: null, tools: [{ tool_call_id: "call_b", tool_name: "save_memory" }] }],
    toolCalls: [{ tool_name: "save_memory", status: "ok", duration_ms: 8, error_type: null,
                  args: { category: "trade", content: long }, result: "saved", tool_call_id: "call_b" }],
  };
  const w = mount(ReactTimeline, { props: p as any });
  expect(w.text()).toContain("…");              // 头部截断
  await w.findAll(".tool-card .tool-head")[0].trigger("click");
  expect(w.text()).toContain("入参");           // 长参：展开体补完整入参
  expect(w.text()).toContain(long);             // 完整内容
});

it("§④ 无参工具头 name()", () => {
  const p = {
    ...baseProps(),
    steps: [{ thinking: null, tools: [{ tool_call_id: "call_c", tool_name: "get_position" }] }],
    toolCalls: [{ tool_name: "get_position", status: "ok", duration_ms: 12, error_type: null,
                  args: {}, result: "flat", tool_call_id: "call_c" }],
  };
  const w = mount(ReactTimeline, { props: p as any });
  expect(w.text()).toContain("get_position()");
});
```

- [ ] **Step 2: 运行验证失败**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts`
Expected: FAIL（头部仍是裸 tool_name、展开体仍恒渲入参）。

- [ ] **Step 3: 实现工具头 + 去重复**

`frontend/src/components/ReactTimeline.vue`：

script import 加 `clipArgs`：
```ts
import { fmtArgs, fmtDuration, clipArgs } from "@/utils/format";
```
script 加 helper（放在 `rowFor` 附近）：
```ts
function headArgs(t: ReactTool): { text: string; clipped: boolean } {
  const r = rowFor(t);
  return r ? clipArgs(r.args) : { text: "", clipped: false };
}
```
template 工具头 `<span class="tool-name">` 改为（有遥测才带括号参数；orphan 维持裸名）：
```html
<span class="tool-name">{{ t.tool_name }}<template v-if="rowFor(t)">(<span class="tool-args">{{ headArgs(t).text }}</span>)</template></span>
```
template 工具体「入参」行加 `v-if="headArgs(t).clipped"`（短参不重复）：
```html
<div v-if="rowFor(t) && openCards.has(cardKey(t, si, ti))" class="tool-body">
  <div v-if="headArgs(t).clipped" class="kv"><span class="k">入参</span><span class="args-compact">{{ fmtArgs(rowFor(t)!.args) }}</span></div>
  <div class="kv"><span class="k">结果</span>
    <JsonBlock v-if="rowFor(t)!.result != null" :value="rowFor(t)!.result" />
    <span v-else class="seam">结果未捕获</span>
  </div>
</div>
```

- [ ] **Step 4: CSS 迁移（同文件 scoped style，spec §2/§3.3）**

逐处 swap（行号以当前 HEAD 为准，按选择器定位）：

| 选择器 | 现值 | 改为 |
|--------|------|------|
| `.react-step` border-left（:158） | `rgba(96,165,250,0.3)` | `var(--ob-thinking-border)` |
| `.thinking-text` background（:160） | `rgba(0,0,0,0.18)` | `var(--ob-block-bg)` |
| `.tool-card` background（:161） | `rgba(255,255,255,0.03)` | `var(--ob-block-bg)` |
| `.injection-card` background（:167） | `rgba(250,204,21,0.1)` | `var(--ob-warn-soft)` |
| `.kv .k`（:166） opacity:0.6 | opacity | `color: var(--ob-text-muted)`（删 opacity，入参/结果标签；审查 3） |
| `.muted`（:170）/`.seam`（:172） opacity:0.55/0.5 | opacity | `color: var(--ob-text-muted)`（删 opacity） |
| `.thinking-toggle`（:175） opacity:0.6 | 保留（结构提示，可读） | — |

tool-args 加样式：`.tool-args { color: var(--ob-text-muted); }`（参数次要、与函数名区分）。

- [ ] **Step 5: 运行验证通过**

Run: `cd frontend && npx vitest run test/ReactTimeline.spec.ts`
Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ReactTimeline.vue frontend/test/ReactTimeline.spec.ts
git commit -m "$(cat <<'EOF'
feat(webui): ReactTimeline 工具头函数式 + 去参数重复 + 浅色迁移

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: CycleDetailPanel — 快照置顶/格式化 + 卡片化 + in/out 文案 + CSS 迁移

**Files:**
- Modify: `frontend/src/components/CycleDetailPanel.vue`
- Test: `frontend/test/CycleDetailPanel.spec.ts`

- [ ] **Step 1: 改/加测试**

`frontend/test/CycleDetailPanel.spec.ts`：
1) `§议题4 状态快照详情区` 那条改为默认展开（不再先点 toggle）：
```ts
it("§⑤⑥ 状态快照默认展开 + 置顶 + 格式化", () => {
  const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
  const txt = w.text();
  expect(txt).toContain("本轮开始时的状态");
  expect(txt).not.toContain("开始态");
  expect(txt).toContain("17.99");          // 默认展开即可见
  expect(txt).not.toContain("_cycle_id");
  // 置顶：快照出现在「推理与行动过程」之前
  expect(txt.indexOf("本轮开始时的状态")).toBeLessThan(txt.indexOf("推理与行动过程"));
});
```
2) 加 in/out 文案断言：
```ts
it("§① chip 输入/输出 token 文案", () => {
  const w = mount(CycleDetailPanel, { props: { detail: detail() as any } });
  expect(w.text()).toContain("输入 8,000 / 输出 1,000 tok");
});
```
3) `§议题5 chips token 千分位` 既有断言保留（不受影响）。

- [ ] **Step 2: 运行验证失败**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts`
Expected: FAIL（快照默认折叠、文案仍「开始态」、in/out 仍英文）。

- [ ] **Step 3: 实现 — script**

`frontend/src/components/CycleDetailPanel.vue` script：
```ts
import { fmtTokens, fmtDuration, fmtNum, fmtSigned } from "@/utils/format";
```
`snapshotOpen` 默认改 `true`：
```ts
const snapshotOpen = ref(true);   // §7 置顶后默认展开
```
加 pnl 着色 helper：
```ts
function pnlClass(n: number | null | undefined) { return n == null ? "" : n < 0 ? "neg" : "pos"; }
```

- [ ] **Step 4: 实现 — template（重排 + 卡片化 + 快照格式化 + in/out）**

1) chips 区 in/out 那条 `<n-tag>` 改：
```html
<n-tag v-if="detail.input_tokens != null" size="small">输入 {{ fmtTokens(detail.input_tokens) }} / 输出 {{ fmtTokens(detail.output_tokens) }} tok</n-tag>
```
2) 给「唤醒上下文」「推理与行动」「决策」三个 `<section>` 加 `class="ob-card"`。
3) **把「状态快照」`<section>` 整块移到「推理与行动」`<section>` 之前**，并替换为格式化版（2 列网格 + fmtNum/fmtSigned + pos/neg 着色 + USDT + 文案）：
```html
<section v-if="snapshot" class="ob-card">
  <h4 class="snapshot-toggle clickable" @click="snapshotOpen = !snapshotOpen">
    本轮开始时的状态 {{ snapshotOpen ? "▾" : "▸" }}
  </h4>
  <div v-if="snapshotOpen" class="snapshot">
    <template v-if="snapshot.position">
      <span class="snap-k">持仓</span>
      <span>
        <span class="dir" :class="snapshot.position.side">{{ snapshot.position.side === 'long' ? '多' : '空' }}</span>
        {{ fmtNum(snapshot.position.contracts) }} 张 · 入场 {{ fmtNum(snapshot.position.entry_price) }} · 杠杆 {{ snapshot.position.leverage }}× · 浮盈
        <span :class="pnlClass(snapshot.position.unrealized_pnl)">{{ fmtSigned(snapshot.position.unrealized_pnl) }} USDT</span>
      </span>
    </template>
    <template v-else><span class="snap-k">持仓</span><span class="muted">空仓</span></template>
    <template v-if="snapshot.balance">
      <span class="snap-k">余额</span>
      <span>总 {{ fmtNum(snapshot.balance.total_usdt) }} · 可用 {{ fmtNum(snapshot.balance.free_usdt) }} · 占用 {{ fmtNum(snapshot.balance.used_usdt) }} USDT</span>
    </template>
    <template v-if="snapshot.market">
      <span class="snap-k">现价</span>
      <span>{{ fmtNum(snapshot.market.ticker_last) }} <span class="muted">@ {{ snapshot.market.fetched_at }}</span></span>
    </template>
    <template v-if="snapshot.pending_orders && snapshot.pending_orders.length">
      <span class="snap-k">挂单</span>
      <span><span v-for="(o, i) in snapshot.pending_orders" :key="i" class="snap-item">{{ o.order_type }} {{ o.side }} @{{ fmtNum(o.trigger_price ?? o.price) }} ×{{ o.amount }}</span></span>
    </template>
    <template v-if="snapshot.active_alerts && snapshot.active_alerts.length">
      <span class="snap-k">告警</span>
      <span><span v-for="(a, i) in snapshot.active_alerts" :key="i" class="snap-item">{{ a.direction }} @{{ fmtNum(a.price) }}<span v-if="a.reasoning" class="muted"> · {{ a.reasoning }}</span></span></span>
    </template>
  </div>
</section>
```

- [ ] **Step 5: 实现 — scoped CSS（迁移 + 网格 + 着色）**

| 选择器 | 现值 | 改为 |
|--------|------|------|
| `.context` background（:144） | `rgba(0,0,0,0.22)` | `var(--ob-block-bg)` |
| `.reasoning` background（:145） | `rgba(0,0,0,0.25)` | `var(--ob-block-bg)` |
| `.decision` background（:146） | `rgba(96,165,250,0.08)` | `var(--ob-accent-soft)` |
| `.seam`（:147-148） opacity:0.5 | opacity | `color: var(--ob-text-muted)`（删 opacity） |
| `h4`（:141）/`h5`（:142） opacity:0.85/0.8 | 保留（标题、可读） | — |

`.snapshot` 改网格 + 新增 dir/pos/neg：
```css
.snapshot { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 12px; }
.snap-k { color: var(--ob-text-muted); }
.snap-item { display: inline-block; margin-right: 10px; }
.muted { color: var(--ob-text-muted); }
.dir.long, .pos { color: var(--ob-pos); font-weight: 600; }
.dir.short, .neg { color: var(--ob-neg); font-weight: 600; }
```
（删除旧 `.snap-block` flex 相关样式，已不再用。）

- [ ] **Step 6: 运行验证通过**

Run: `cd frontend && npx vitest run test/CycleDetailPanel.spec.ts`
Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/CycleDetailPanel.vue frontend/test/CycleDetailPanel.spec.ts
git commit -m "$(cat <<'EOF'
feat(webui): CycleDetailPanel 快照置顶+格式化 + 卡片化 + in/out 文案 + 浅色迁移

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: CycleRowHeader — tok 空格 + CSS 迁移

**Files:**
- Modify: `frontend/src/components/CycleRowHeader.vue`
- Test: `frontend/test/CycleRowHeader.spec.ts`

- [ ] **Step 1: 改测试**

`frontend/test/CycleRowHeader.spec.ts` 中 `遥测用 format util` 那条断言由 `toContain("80,733")` 收紧为带空格：
```ts
it("遥测用 format util（千分位 + tok 空格 + s）", () => {
  const w = mount(CycleRowHeader, { props: { cycle: cycle() as any } });
  expect(w.text()).toContain("80,733 tok");   // tok 前有空格（fixture tokens_consumed:80733）
  expect(w.text()).toContain("49.8s");
});
```

- [ ] **Step 2: 运行验证失败**

Run: `cd frontend && npx vitest run test/CycleRowHeader.spec.ts`
Expected: FAIL（现渲 `80,733tok` 无空格）。

- [ ] **Step 3: 实现**

`frontend/src/components/CycleRowHeader.vue` template `.tele`（:45）`}}tok` 加空格：
```html
<span class="tele">{{ fmtTokens(cycle.tokens_consumed) }} tok · {{ fmtDuration(cycle.wall_time_ms) }}</span>
```
scoped CSS 迁移：

| 选择器 | 现值 | 改为 |
|--------|------|------|
| `.cycle-head.keyrow` border-left-color（:51） | `#60a5fa` | `var(--ob-accent)` |
| `.time`（:52） opacity:0.7 | 保留（可读） | — |
| `.seg-label`（:54） opacity:0.5 | opacity | `color: var(--ob-text-muted)`（删 opacity） |
| `.tele`（:57） opacity:0.5 | opacity | `color: var(--ob-text-muted)`（删 opacity） |
| `.muted`（:58） opacity:0.45 | opacity | `color: var(--ob-text-muted)`（删 opacity） |

- [ ] **Step 4: 运行验证通过**

Run: `cd frontend && npx vitest run test/CycleRowHeader.spec.ts`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CycleRowHeader.vue frontend/test/CycleRowHeader.spec.ts
git commit -m "$(cat <<'EOF'
feat(webui): CycleRowHeader tok 空格 + 浅色迁移

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Dashboard 表面卡片化 + 换色

让 dashboard 彩字/灰字落白卡（spec §4 决议）。**LiveStatusCard 已是 `<n-card>`（lightTheme 自带白底），仅换色不再加卡**；PerformanceBar/SessionMeta/DecisionStream 裸平铺，包白卡。

**Files:**
- Modify: `frontend/src/components/LiveStatusCard.vue`
- Modify: `frontend/src/components/PerformanceBar.vue`
- Modify: `frontend/src/components/SessionMeta.vue`
- Modify: `frontend/src/components/DecisionStream.vue`
- Test: `frontend/test/SessionMeta.spec.ts`、`frontend/test/DecisionStream.spec.ts`

- [ ] **Step 1: 写卡片化断言（已有 spec 的组件加 class 断言）**

`frontend/test/SessionMeta.spec.ts` 加（复用该文件既有 createTestingPinia + 设 store.detail + nextTick 脚手架；`.ob-card` 在 `<div v-if="d">` 上，必须先设 detail 才渲染）：
```ts
it("§4 dashboard 卡片化：根容器带 ob-card", async () => {
  const wrapper = mount(SessionMeta, {
    global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
  });
  const store = useSessionsStore();
  store.detail = { id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1h",
    scheduler_interval_min: 15, initial_balance: 10000, token_budget: 200000,
    created_at: "2026-06-12T10:00:00Z", last_active_at: null } as any;
  await wrapper.vm.$nextTick();
  expect(wrapper.find(".ob-card").exists()).toBe(true);
});
```
`frontend/test/DecisionStream.spec.ts` 加（复用文件现成 `mountStream()` helper :14，自带 pinia + store.cycles）：
```ts
it("§4 feed 包白卡 ob-card", async () => {
  const { wrapper } = mountStream();
  await wrapper.vm.$nextTick();
  expect(wrapper.find(".ob-card").exists()).toBe(true);
});
```

- [ ] **Step 2: 运行验证失败**

Run: `cd frontend && npx vitest run test/SessionMeta.spec.ts test/DecisionStream.spec.ts`
Expected: FAIL（无 `.ob-card`）。

- [ ] **Step 3: 实现 — 卡片化 + 换色**

**LiveStatusCard.vue**（已 n-card，仅换色）scoped CSS：
```css
.label { color: var(--ob-text-muted); }
.muted { color: var(--ob-text-muted); }
.pos.long { color: var(--ob-pos); }
.pos.short { color: var(--ob-neg); }
```
（删 `.label`/`.muted` 的 opacity。）

**PerformanceBar.vue**：root `.perf-bar` 加 `.ob-card`：
```html
<div v-if="perf" class="perf-bar ob-card">
```
scoped CSS：
```css
.perf-bar { border-top: 1px solid var(--ob-border); padding: 8px 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 16px; max-height: 40vh; overflow-y: auto; }
.caliper { font-size: 12px; color: var(--ob-text-muted); margin-bottom: 4px; }
.note { font-size: 11px; color: var(--ob-text-muted); margin-top: 6px; }
.neg { color: var(--ob-neg); }
```
（删 `.caliper`/`.note` 的 opacity。`border-top` 在卡内可保留作分隔或删，二选一，建议保留换 `--ob-border`。）

**SessionMeta.vue**：root `.session-meta-wrap` 加 `.ob-card`：
```html
<div v-if="d" class="session-meta-wrap ob-card">
```
scoped CSS：
```css
.sysprompt-toggle { cursor: pointer; user-select: none; font-size: 12px; color: var(--ob-text-muted); }
.sysprompt-text { white-space: pre-wrap; word-break: break-word; background: var(--ob-block-bg); padding: 8px; border-radius: 4px; font-size: 12px; line-height: 1.5; margin: 6px 0 0; max-height: 320px; overflow-y: auto; }
```
（`.sysprompt-toggle` 删 opacity；`.sysprompt-text` bg `rgba(0,0,0,.22)`→`var(--ob-block-bg)`。）

**DecisionStream.vue**：feed 包白卡 + 换 loading/empty：
```html
<div class="decision-stream ob-card">
```
scoped CSS：
```css
.loading { padding: 12px; color: var(--ob-text-muted); font-size: 13px; }
.empty { padding: 24px; text-align: center; color: var(--ob-text-muted); font-size: 13px; }
```
（删 opacity。）

- [ ] **Step 4: 运行验证通过**

Run: `cd frontend && npx vitest run test/SessionMeta.spec.ts test/DecisionStream.spec.ts`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LiveStatusCard.vue frontend/src/components/PerformanceBar.vue frontend/src/components/SessionMeta.vue frontend/src/components/DecisionStream.vue frontend/test/SessionMeta.spec.ts frontend/test/DecisionStream.spec.ts
git commit -m "$(cat <<'EOF'
feat(webui): dashboard 表面卡片化 + 状态色 remap（彩字落白卡，达 AA）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 叶子组件换色（SessionList / TradesTable / JsonBlock / EquityChart / DashboardView）

**Files:**
- Modify: `frontend/src/components/SessionList.vue`
- Modify: `frontend/src/components/TradesTable.vue`
- Modify: `frontend/src/components/JsonBlock.vue`
- Modify: `frontend/src/components/EquityChart.vue`
- Modify: `frontend/src/views/DashboardView.vue`（审查 1：原计划漏，补全 opacity 枚举）

- [ ] **Step 1: SessionList.vue scoped CSS**

| 选择器 | 现值 | 改为 |
|--------|------|------|
| `.session-row.active` background（:43） | `rgba(96,165,250,0.15)` | `var(--ob-accent-soft)` |
| `.bottom`（:46） opacity:0.7 | 保留（可读） | — |
| `.ret`（:47） | `#4ade80` | `var(--ob-pos)` |
| `.ret.neg`（:48） | `#f87171` | `var(--ob-neg)` |
| `.empty`（:49） opacity:0.5 | opacity | `color: var(--ob-text-muted)`（删 opacity） |

- [ ] **Step 2: TradesTable.vue scoped CSS**

```css
:deep(.pos) { color: var(--ob-pos); }
:deep(.neg) { color: var(--ob-neg); }
```

- [ ] **Step 3: JsonBlock.vue scoped CSS**

```css
.json, .raw { margin: 0; padding: 8px; background: var(--ob-block-bg); border-radius: 4px; font-size: 12px; line-height: 1.4; overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
.raw { color: var(--ob-warn); }
.empty { color: var(--ob-text-muted); }
```
（`.json,.raw` bg `rgba(0,0,0,.25)`→`var(--ob-block-bg)`；`.raw` `#fbbf24`→`var(--ob-warn)`；`.empty` 删 opacity。）

- [ ] **Step 4: EquityChart.vue — JS 配置整段换浅值（无法引 CSS 令牌，手填）**

`onMounted` 内 `createChart` 配置：
```ts
layout: { background: { color: "transparent" }, textColor: "#6b7280" },
grid: { vertLines: { visible: false }, horzLines: { color: "#e5e7eb" } },
```
line series：
```ts
series = chart.addLineSeries({ color: "#15803d", lineWidth: 2 });
```
（textColor `#9ca3af`→`#6b7280`、grid `rgba(255,255,255,0.05)`→`#e5e7eb`、line `#4ade80`→`#15803d`，与 `--ob-*` 取值一致；background 维持 transparent 透出白卡。）

- [ ] **Step 5: DashboardView.vue scoped CSS（审查 1 补）**

`.empty`（:46，「请选择会话」大号占位，落 `--ob-page-bg` 灰底）opacity:0.5 → `color: var(--ob-text-muted)`（删 opacity）。`.err`（:47）仅 margin、`.dashboard`/`.stream-wrap` 无色，不动。

```css
.empty { height: 100%; display: flex; align-items: center; justify-content: center; color: var(--ob-text-muted); }
```

- [ ] **Step 6: 验证构建**

Run: `cd frontend && npx vue-tsc --noEmit && npm run build`
Expected: 0 error。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/SessionList.vue frontend/src/components/TradesTable.vue frontend/src/components/JsonBlock.vue frontend/src/components/EquityChart.vue frontend/src/views/DashboardView.vue
git commit -m "$(cat <<'EOF'
feat(webui): 叶子组件浅色迁移（SessionList/TradesTable/JsonBlock/EquityChart/DashboardView）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 全量 gate + Playwright 按面量化对比度实测

**Files:** 无源码改动（除非实测暴露问题→回到对应 Task 修）。

- [ ] **Step 1: 前端全量 gate**

Run: `cd frontend && npx vue-tsc --noEmit && npm run build && npx vitest run`
Expected: vue-tsc 0 error、build 成功、vitest 全绿。

- [ ] **Step 2: 后端未误伤确认（本 iter 零后端，应不变）**

Run: `cd /Users/z/Z/TradeBot && pytest -q`
Expected: 与改动前一致（全绿）。

- [ ] **Step 3: Playwright 浅色实测（用户起 WebUI 后）**

逐页（feed/详情/dashboard/图表）：
- 目检：浅灰底+白卡层次清晰、无"白底白字/彩底消失"、工具头函数式可读、快照置顶+红绿格式化、注入卡浅琥珀可见、console 0 error。
- **按面量化对比度**（不靠肉眼，spec §10）：用 `browser_evaluate` 取关键元素（CycleDetailPanel 持仓 PnL、PerformanceBar `.neg`、LiveStatusCard `.pos`、JsonBlock `.raw`、各 `.muted/.tele`）的 `getComputedStyle(el).color` 前景色 + 其**实际所在表面**底色（白卡 `#fff` vs 页底 `#eef0f3`），计算 WCAG 对比比值——小字号正文 ≥4.5、大号占位 ≥3。重点核 dashboard PnL/status 确在白卡上（LiveStatusCard n-card / PerformanceBar·DecisionStream·SessionMeta 的 .ob-card）。
- 若任一面取色显示彩字落在 `#eef0f3` 上（达不到 4.5），回 Task 6 补卡片化或调底色。

- [ ] **Step 4: 收尾**

实测通过后用 superpowers:finishing-a-development-branch 完成分支（开 PR 或合并，按用户选择）。

---

## Self-Review

- **Spec 覆盖**：① CycleRowHeader tok 空格（T5）+ CycleDetailPanel in/out（T4）✓；② lightTheme+令牌（T1）+ 全 13 文件迁移（T3-T7，含 DashboardView）✓；③ 卡片化 CycleDetailPanel（T4）+ dashboard（T6）✓；④ 工具头函数式+去重复（T3）✓；⑤ 快照置顶（T4）✓；⑥ 文案+格式化（T4）✓；spec-审查 Issue1 彩字 remap（T3/T6/T7）、F1 漏色（T3 注入卡 / T7 SessionList active）、F2 深绿 #15803d（T1 令牌）、F3 opacity 全 25 处枚举（T3-T7 各 CSS 步，含 DashboardView:46 + ReactTimeline .kv .k:166）、🔴 dashboard 卡片化（T6）+ 按面量化（T8）全覆盖。
- **plan-审查修订**：DashboardView.vue 补入 T7（漏文件）、T6 测试片段改可运行（createTestingPinia/mountStream）、.kv .k:166 补 T3、format.spec.ts 合并 import（T2）、删死令牌 --ob-text（T1）、.ob-card padding 特异性注（T1）。
- **无占位符**：每个改码步给了完整可运行代码/精确 swap 表。
- **类型一致**：`clipArgs`/`fmtNum`/`fmtSigned`/`HEAD_ARGS_MAX`（T2 定义）在 T3/T4 消费签名一致；`.ob-card`（T1）在 T4/T6 复用名一致；`--ob-*` 令牌名贯穿。
- **依赖序**：T1 令牌基座先于所有引用；T2 helper 先于 T3/T4 消费；T8 gate 最后。
