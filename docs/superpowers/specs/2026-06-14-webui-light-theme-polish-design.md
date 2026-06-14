# WebUI 观察台浅色主题 + 可读性打磨 — 设计 spec

## 1. 目标

观察台当前是 naive-ui 裸 `darkTheme`(全黑),长时间观察刺眼、区域层次弱、若干文案/
数值/工具头展示不够友好。本 iter 做一轮**视觉 + 可读性打磨**:换浅色主题、区域卡片化、
工具头函数式化、快照置顶并重格式化、数值/文案修缮。

**全部纯前端**——零后端、零 schema、零 Alembic 迁移(所有数据上一轮 PR#79 已暴露,本 iter
只改渲染)。

6 项议题(按文件重叠归 4 簇,见 §2 末):

| # | 议题 | 处置 | 改动面 |
|---|------|------|--------|
| 1 | `51,944tok` 数值贴单位 / `in·out` 隐晦 | 词单位加空格 + `输入/输出 token` | 前端文案 |
| 2 | 全黑刺眼、不易观察 | 换浅色(浅灰底 + 白卡片) | 全 12 组件 CSS(大头) |
| 3 | 区域太平淡、无层次 | 各区做成白卡浮起 | 随 ② 同刀 |
| 4 | 工具参数藏在展开、与函数名不同级 | 工具头函数式 `name(args)` + 长参截断 + 去重复 | ReactTimeline + format |
| 5 | 快照排在推理/行动之后 | 置顶(开始态领先叙事) | CycleDetailPanel 重排 |
| 6 | "开始态"术语 + 快照展示简陋 | 改通俗文案 + 红绿/千分位/单位/网格 | CycleDetailPanel + format |

## 2. 现状与约束(代码锚点已对照 worktree HEAD 复核)

- **主题入口**:`App.vue:20` `<n-config-provider :theme="darkTheme">`,`darkTheme` 从
  `naive-ui` import(App.vue:10),**零 themeOverrides、零全局色变量**。这是"全黑"根因。
- **暗色硬编码 CSS 分布(换浅色必须逐组件 review,实测全量 grep)**:12 组件/视图均有 color
  或 opacity 命中(其中含硬编码**色** 10 个;DecisionStream/DashboardView 仅 opacity、App 仅布局)。
  - `rgba(255,…)` 白色微透 **× 3 — 高危**(浅底直接消失,必改):`PerformanceBar.vue:43`
    分隔线、`ReactTimeline.vue:161` `.tool-card` 底、`EquityChart.vue:35` 网格线色。
  - `rgba(0,0,0,…)` 暗底块 **× 5**(实测全部):`.context`(CDP:144)/`.reasoning`(CDP:145)/
    `.thinking-text`(ReactTimeline:160)/`.sysprompt-text`(SessionMeta:34)/`.json,.raw`
    (JsonBlock:18)——浅底下变深灰块,换 `--ob-block-bg`。(订正:`.decision` CDP:146 是
    `rgba(96,165,250,.08)` **蓝**、非黑,归蓝 accent 处理,见 §3.3——勿再误列暗底块。)
  - **彩色 rgba 背景 × 4**(F1 补全,易漏,凑齐 rgba 共 12):蓝 `rgba(96,165,250,…)` ×3
    (`.decision` CDP:146 / `react-step` 左条 ReactTimeline:158 / `.session-row.active` 选中高亮
    SessionList:43)+ 琥珀 `rgba(250,204,21,.1)` ×1(`.injection-card` 注入卡底 ReactTimeline:167)。
    低透明、浅底"勉强存活"但脱离令牌体系,仍须 remap(§3.3)。
  - 硬编码 hex **× 11 = 10 彩色文本 + 1 蓝**(实测):绿 `#4ade80` ×4(SessionList:47 /
    LiveStatusCard:35 / TradesTable:31 / EquityChart:39)、红 `#f87171` ×4(PerformanceBar:47 /
    SessionList:48 / LiveStatusCard:36 / TradesTable:32)、琥珀 `#fbbf24` ×1(JsonBlock:19 `.raw`)、
    灰 `#9ca3af` ×1(EquityChart:34 轴文字)、蓝 `#60a5fa` ×1(CycleRowHeader:51)。**10 个彩字
    都是为暗底设计的高亮值,浅底下对比度 ~1.5–1.8:1(WCAG AA 需 4.5:1)严重不可读——与"白底
    白字"同类风险,必须 remap(§3.3),不能保留**。
  - `opacity:` 降透明 **× 25**(实测跨 **0.45~0.85**,非仅 .5~.55):浅底下深色文本降透明
    =**变灰仍可读**,属可控对比度打磨,**与"亮色/彩底浅底消失"(彩字 + F1 彩背景)不是一个
    严重度**——别混为一谈;低档(≤.55)优先改 `--ob-text-muted`,高档本就可读。
- **chips / 状态 tag 自动跟随主题**:`NTag type="success|error|warning|info"` 由 naive-ui
  主题驱动,切 `lightTheme` 自动翻浅色配色,**无需手改**(统计的硬编码 CSS 仅指 scoped
  `<style>` 里的自定义色,不含 NTag 语义色)。
- **数据可达性**:`detail.state_snapshot`(position/balance/market/pending_orders/
  active_alerts,内部键 `_errors`/`_cycle_id` 不展示)、`tool_calls[].args`(干净 dict)、
  token/duration 字段**均已在 payload**(PR#76/#77/#79 落)。本 iter 零后端。
- **DB 迁移**:无。

**簇划分**(plan 据此排任务,同簇改同批文件一刀做完):A 文案/数值(①⑥-标签)· B 浅色主题
+卡片(②+③+⑥-内容,改同批 scoped CSS)· C 工具头(④)· D 布局重排(⑤)。

## 3. 议题 2(核心/最大):浅色主题迁移

### 3.1 入口切换

`App.vue`:`:theme="darkTheme"` → 浅色。naive-ui 浅色为默认主题,移除 `:theme` 绑定或
显式 `:theme="lightTheme"`(择一,plan 用显式 `lightTheme` 以表意清晰),import 同步换。

### 3.2 全局设计令牌(单一来源,避免 ~48 处散改)

既然 12 组件都要重刷,**不逐个改 ~48 处散值(rgba 12 + hex 11 + opacity 25;含色文件 10)**,而在 App 根注入一组 CSS 自定义属性
(浅色取值,对应 brainstorm 选定的 B 方案 浅灰底+白卡),各组件 scoped CSS 引用之。这把
"白底消失"这类 bug 收敛到单一来源(契合工具设计原则 §3 信号唯一来源的精神,迁移到
设计令牌)。最小令牌集:

| 变量 | 值 | 替换对象 |
|------|----|---------|
| `--ob-page-bg` | `#eef0f3` | 页面/内容区底(浅灰) |
| `--ob-card-bg` | `#ffffff` | 各区卡片底 |
| `--ob-card-shadow` | `0 1px 3px rgba(0,0,0,.06)` | 卡片浮起阴影 |
| `--ob-block-bg` | `#f6f7f9` | 内嵌块(thinking/工具卡/context/reasoning)底,**替 5 处 `rgba(0,0,0,.18~.25)` + 高危 `rgba(255,255,255,.03)` 的 `.tool-card` 底(ReactTimeline:161)** |
| `--ob-border` | `#e5e7eb` | 分隔线/边框,**替另 2 处高危 `rgba(255,…)`**(PerformanceBar:43 分隔线 / EquityChart:35 网格) |
| `--ob-text` | `#111827` | 主文本 |
| `--ob-text-muted` | `#6b7280` | 次要文本,**替文本类 `opacity` 降透明(全 25 处逐一 review,§3.3)** |
| `--ob-accent` | `#2563eb` | 决策蓝 / react step 左条强调,**替 `#60a5fa`** |
| `--ob-accent-soft` | `#eff6ff` | 决策块底 |
| `--ob-thinking-border` | `#93c5fd` | react step / thinking 左边框 |
| `--ob-pos` | `#15803d` | **正向语义**(浮盈≥0 / session running / 盈利 trade / 图表上行)——深绿,白底 **≈5:1 达 AA(文本)**且 ≥3:1(图形线);**替亮 `#4ade80`**(F2:原选 `#16a34a` 仅 3.3:1 不达,改深) |
| `--ob-neg` | `#dc2626` | **负向语义**(浮盈<0 / error / 亏损 trade / 下行)——深红,白底 ≈4.8:1 达 AA;**替亮 `#f87171`**(审查 Issue 1) |
| `--ob-warn` | `#b45309` | 告警/未解析**文本**(JsonBlock `.raw`)——深琥珀,白底 ≈5:1 达 AA;**替亮 `#fbbf24`**(审查 Issue 1) |
| `--ob-warn-soft` | `#fef3c7` | 注入卡**背景**(ReactTimeline:167)——浅琥珀;**替 `rgba(250,204,21,.1)`**(F1) |

注入位置:App.vue 顶层容器(或 `n-config-provider` 内根 div)的 scoped `:deep` 不便跨组件,
故用**非 scoped 全局样式**(新建 `frontend/src/styles/tokens.css`,`main.ts` import,定义
`:root{ --ob-* }`)。页面底色由 `n-layout`/content 区设 `background: var(--ob-page-bg)`。

> **实现注意(审查 LOW)**:切 lightTheme 后 `n-layout-content` 自带主题白底(`--n-color`),
> 要盖成浅灰需 `:deep(.n-layout-content)` 或 `n-config-provider` `themeOverrides`——scoped
> `.main` 直接覆盖可能撞 specificity(naive-ui **2.38.1 已知 footgun**,见 memory,勿
> npm update)。§10 Playwright 实测兜底确认底色生效。

**对比度基准(审查 🔴 决议)**:表中"达 AA"均按**白卡底 `#ffffff`** 计——成立前提是彩字/正文都
落白卡。本 iter 经 §4 把 CycleDetailPanel + dashboard 表面**全卡片化**,故彩字/正文均在白卡上,
口径成立。**唯一落 `--ob-page-bg #eef0f3` 灰底的是大号占位文本**(`.empty`「请选择会话」/`.loading`),
按 **AA-Large(≥18pt 或 ≥14pt 粗体)3:1** 判定——这些色在 #eef0f3 上 ≈4.2~4.4 ≥ 3 达标。即:
**小字号正文/彩字按 AA 4.5:1(在白卡上)、大号占位按 AA-Large 3:1(灰底亦达)**,按字号分档,非笼统 4.5。

### 3.3 逐组件处置原则(plan 逐文件枚举)

- 3 处高危 `rgba(255,…)` → `var(--ob-border)`(分隔线)或 `var(--ob-block-bg)`(`.tool-card` 底)。
- `rgba(0,0,0,.x)` 暗底块 → `var(--ob-block-bg)`。
- **全部 25 处 `opacity:` 逐一 review(实测跨 0.45~0.85,非仅 .5~.55)**:文本类
  (`.muted`/`.seam`/`.seg-label`/`.tele`,**含未点名的** `DecisionStream:44-45` /
  `DashboardView:46` `.loading/.empty/.err`)→ 删 opacity、改 `color: var(--ob-text-muted)`
  (浅底受控对比度);低档(≤.55)优先,高档(≥.7)本就可读、按需;**非文本**的 opacity
  (hover/结构)保留。
- 硬编码蓝(`rgba(96,165,250,…)`:`.decision` 底 CycleDetailPanel:146 / `react-step` 左条
  ReactTimeline:158 / `.session-row.active` 选中高亮 SessionList:43〔F1 补〕;hex `#60a5fa`
  keyrow CycleRowHeader:51)→ 左条 `var(--ob-thinking-border)` / 决策块底·选中高亮
  `var(--ob-accent-soft)` / 强调 `var(--ob-accent)`。
- **彩色文本 hex(本轮新增 remap,审查 Issue 1——这是与"白底白字"同类的颜色消失风险)**:
  scoped CSS 里绿 `#4ade80` → `var(--ob-pos)`、红 `#f87171` → `var(--ob-neg)`、琥珀 `#fbbf24`
  (JsonBlock:19 `.raw` **文本**)→ `var(--ob-warn)`。覆盖 SessionList:47-48 / LiveStatusCard:35-36 /
  TradesTable:31-32 / PerformanceBar:47(`.neg`)/ JsonBlock:19。另:琥珀**背景**
  `rgba(250,204,21,.1)` 注入卡底 ReactTimeline:167 → `var(--ob-warn-soft)`(F1,与文本琥珀区分)。
- **SessionMeta.vue**(PR#79 system prompt 折叠区,§2 暗底块成员之一,易在逐文件枚举时漏):
  `.sysprompt-text:34` 底 `rgba(0,0,0,.22)` → `var(--ob-block-bg)`,显式列出。
- **EquityChart.vue 整段 chart 配色块**(lightweight-charts JS 配置,**无法引 CSS 令牌、手填浅值**):
  网格 `rgba(255,…)`:35 + 轴文字 `#9ca3af`:34 + line `#4ade80`:39 一并换浅底可读值
  (轴文字 ≈ `#6b7280`、line ≈ `#15803d` 与 `--ob-pos` 一致、网格浅灰)——不止网格。

## 4. 议题 3:区域卡片化(随 ② 同刀)

统一白卡处置:`background: var(--ob-card-bg); border-radius: 8px; padding; box-shadow:
var(--ob-card-shadow); margin-bottom`。覆盖两处表面:

1. **CycleDetailPanel.vue** 各 `<section>`(唤醒上下文 / 状态快照 / 推理与行动 / 决策):由当前
   "`<h4>` + 裸块"改白卡,区标题留卡内。
2. **Dashboard 表面(审查 🔴 决议:cardify dashboard)**:`DashboardView.vue:30-41` 现把
   `SessionMeta` / `LiveStatusCard` / `DecisionStream`(feed)/ `PerformanceBar` flex **平铺、无卡片**
   (实测 4 者均无 root background),其彩字/灰字直接落 `--ob-page-bg #eef0f3` 灰底。**逐个包白卡**
   (在各组件 root 或 DashboardView 包裹层加白卡样式),使状态彩字/正文落白底——这是让 §3.2 配色
   "达 AA"口径成立的前提(见 §3.2 对比度注),也是把 B「白卡」方案真正应用到 dashboard(原 spec 漏)。
   DecisionStream feed 整体包一张白卡、内部 cycle 行不再各自描底。

这把"太平淡"(③)+ dashboard 卡片化 + 浅色迁移在同一批 scoped CSS 里做掉。

## 5. 议题 1:数值与单位文案

- **词单位加空格**:`CycleRowHeader.vue:45` `}}tok` → `}} tok`(`51,944 tok`)。
- **符号单位维持贴紧**(pushback 已与用户确认):`fmtDuration` 输出 `43.6s`/`100ms` 是排版
  惯例,**不拆空格**;仅词单位 `tok` 需隔。
- **`in/out` 通俗化**:`CycleDetailPanel.vue:55` `in {{…}} / out {{…}}` →
  `输入 {{…}} / 输出 {{…}} tok`。
- 既有 `fmtTokens`(千分位)/`fmtDuration`(ms→s)不改。

## 6. 议题 4:工具头函数式 + 去参数重复

### 6.1 工具头(ReactTimeline.vue:115-125)

工具卡头由当前"仅 `tool_name` + 状态 + 耗时"改为**函数式** `tool_name(参数)`,参数与函数名
同级(对齐 CLI session log `_render_tool_body` 的 `⚙ name(k=v)` 心智,display.py:619-622):

- 无参 → `name()`。
- 有参 → `name(k=v, k=v)`(复用 `key=value` 拼法,嵌套值 JSON 串)。
- **超阈值截断**:渲染串长 > `HEAD_ARGS_MAX`(60 字符)→ 截断 + `…`,头部显示
  `name(timeframe=1h, content="BTC 在 64k…")`。
- orphan 工具卡(无 toolCall 行、无 args)维持现状:`tool_name` + "无遥测记录",不可点。

### 6.2 去参数重复(用户 catch)

头部已显示完整参数时,展开体**不再重复入参**:

- 头部参数**未截断**(短)→ 展开体只渲「结果」。
- 头部参数**被截断**(长)→ 展开体补一行「入参(完整)」+「结果」。

实现:新增 `clipArgs(args) → { text, clipped }`(format.ts):空/无参 → `text=""`(头渲
`name()`)、`clipped=false`;否则拼 `k=v` 串,长度 > `HEAD_ARGS_MAX`(§6.1 单一定义)
截断 + `clipped=true`。头渲
`name(${text})`;展开体「入参」行 `v-if="clipped"`,内容用既有 `fmtArgs`(完整)。`fmtArgs`
保留(展开体完整入参 + 扁平表仍用)。

## 7. 议题 5:状态快照置顶(CycleDetailPanel 重排)

section 顺序由 `chips → 唤醒上下文 → 推理与行动 → 状态快照 → 决策` 改为:

```
chips → 唤醒上下文 → 状态快照(本轮开始) → 推理与行动 → 决策
```

叙事顺为"世界长这样(开始态)→ 我怎么想/做(推理行动)→ 决策"。快照区**默认展开**
(从当前 `snapshotOpen=false` 改 `true`)——它现在是领头的上下文;仍保留折叠 toggle。
纯模板块移动 + 一个默认值。

## 8. 议题 6:快照文案 + 格式化(CycleDetailPanel + format)

- **文案**:`CycleDetailPanel.vue:97` 「状态快照(开始态)」→「**本轮开始时的状态**」。
- **持仓行格式化**:方向 `long/short` → 中文「多/空」并按方向着色(多 `var(--ob-pos)` /
  空 `var(--ob-neg)`);`contracts`/`entry_price` 千分位;`leverage` 加 `×`;`unrealized_pnl`
  带正负号 + 按符号着色(≥0 `var(--ob-pos)` / <0 `var(--ob-neg)`)+ `USDT`。示例:`空 17.99 张 · 入场 63,896 ·
  杠杆 5× · 浮盈 −42.50 USDT`。
- **余额/现价**:数值千分位 + `USDT` 单位;现价 `@ 时间` 用 muted。
- **布局**:由当前 flex `snap-block` 改 2 列网格(`label | value`,`grid-template-columns:
  auto 1fr`),对齐更整齐(brainstorm 已确认)。
- **数值 helper**:format.ts 新增 `fmtNum(n, maxFrac=2)`(`toLocaleString` 千分位,
  `null→"—"`)+ `fmtSigned(n)`(带 `+/−` 号,`null→"—"`)。空仓/缺字段沿用现有 `v-if` 守卫。

## 9. format util 变更汇总(frontend/src/utils/format.ts)

- 新增 `clipArgs(args) → {text:string, clipped:boolean}`(§6.2,头部用;空→`""`)。
- 新增 `fmtNum(n, maxFrac=2)` / `fmtSigned(n)`(§8,快照数值)。
- 既有 `fmtTokens` / `fmtDuration` / `fmtArgs` **不改**(`fmtArgs` 仍供展开体完整入参 + 扁平表)。
- 新增 `frontend/src/styles/tokens.css`(§3.2 `:root` 设计令牌)+ `main.ts` import。
- **无 openapi/类型重生成**(零 schema 变更)。

## 10. 测试策略

- 前端(vitest,18 个 spec 多为文本/class 断言,主题切换基本不破;仅改 DOM/文案处需同步):
  - `format.spec.ts`:`clipArgs` 三态(空→`{"",false}` / 短→`{原串,false}` / 长→`{截断+…,true}`)
    + `fmtNum`(千分位 / `null→"—"` / 小数位)+ `fmtSigned`(`+`/`−`/`null`)。
  - `ReactTimeline.spec.ts`:工具头函数式(`name(k=v)` / 无参 `name()` / 长参截断 `…`)+
    **去重复**(短参展开体**无**「入参」行、只「结果」;长参展开体**有**完整「入参」+「结果」)。
    既有「args 紧凑单行」断言改为断头部函数式 + 长参展开入参。
  - `CycleDetailPanel.spec.ts`:section 顺序(快照在推理之前——断 `indexOf`)+ 快照默认展开 +
    文案「本轮开始时的状态」(不含「开始态」)+ 持仓红绿/千分位/单位/`×` + `输入/输出 token` chip。
    既有「状态快照详情区展开后渲染」断言:默认展开后调整(不再需先点 toggle)。
  - `CycleRowHeader.spec.ts`:`tok` 前有空格——fixture `tokens_consumed:80733`(spec.ts:8),
    既有断言(:46)`toContain("80,733")` 改 `toContain("80,733 tok")`(非 `51,944`)。
  - **dashboard 卡片化**(§4 决议):`LiveStatusCard` / `PerformanceBar` / `SessionMeta` /
    `DecisionStream` 各 mount 后断 root 带白卡 class(如 `.ob-card`),确保彩字落白卡而非裸平铺。
- 主题/对比度(vitest 抓不到 CSS 渲染)→ **收尾 Playwright 实测**:逐页(feed/详情/dashboard/
  图表)目检无"白底白字/彩底消失"、卡片层次清晰、3 处高危 `rgba(255,…)` 已不透明、console 0 error;
  **并按面取色量化对比度**(审查 🔴):用 `getComputedStyle` 取关键彩字/灰字的前景色 + 其**所在表面**
  实际底色(白卡 `#fff` 还是页底 `#eef0f3`),计算比值——小字号正文 ≥4.5、大号占位 ≥3,**不靠肉眼**
  (0.1~0.3 差肉眼不可辨)。重点核 dashboard 的 PnL/status 色确实在白卡上。
- 全量 gate:`vue-tsc --noEmit` + `npm run build`(0 error)+ `npx vitest run`(全绿)。后端无改动,
  跑一次 `pytest -q` 确认未误伤(应不变)。

## 11. 非目标(out of scope)

- 不做明暗切换(toggle)——用户要的是换成浅色,非双主题;令牌集为浅色单值(YAGNI)。
- 不改 agent loop / 后端 schema / DB;零迁移。
- 扁平回退表(`CycleDetailPanel` `toolColumns`,legacy/forensic 路径)的工具/入参**保持表格列**
  ——表格列布局本就分级、无重复问题,不改函数式(§6 仅 ReactTimeline 主路径)。
- 不引入 UI 组件库主题深度定制(只切 `lightTheme` + 自有 `--ob-*` 令牌覆盖 scoped 自定义色);
  NTag 等语义色交给 naive-ui 主题自动翻。
- `HEAD_ARGS_MAX` 取 60 字符,不做按视口自适应截断(YAGNI;长参完整内容在展开体可见)。
- 快照 position 着色按方向(多绿/空红)、PnL 按符号——两套独立着色不合并(语义不同)。
- 不动数据轮询(仍 5s)。
