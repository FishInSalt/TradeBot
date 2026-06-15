# WebUI 观察台边界/层级打磨 + cycle 多展开 — Design

迭代名:`iter-webui-ob-boundary-polish`。纯前端、零后端、零迁移。延续 PR#79/#80/#81 浅色主题打磨脉络。

## 动机

用户实地体验后提的 5 条 ergonomics 缺口,根因均已在代码定位:

1. **cycle 与 cycle 内组件背景太接近** — 展开后 `CycleDetailPanel` 内层 `section.ob-card`(白 `#fff`)坐在 `DecisionStream` 自身 `.ob-card`(也白)里,白叠白,仅 0.06 阴影 + margin 分隔。
2. **cycle 与 cycle 间边界不好区分** — 相邻 `n-collapse-item` 仅靠 naive 默认细分隔线;普通行无背景,仅 key_events 行有 `border-left` 蓝条。
3. **cycle 区与收益分析边界不好区分** — `.stream-wrap`(`DashboardView.vue:51`,无 background、透出灰底的透明滚动容器)内的白卡是 DecisionStream 的 `.ob-card`,与 PerformanceBar(白 `.ob-card` + 1px `border-top`)同为白;折叠条紧贴 stream 底,1px `#e5e7eb` 分界弱。
4. **收益分析展开后无折叠提示** — 折叠态有显式 `点击展开 ▴`(`PerformanceBar.vue:56`),展开态 `.exp-head` 仅 "收益分析 ▾" 字形,缺对称的「点击折叠」显式提示。
5. **cycle 只能展开一个** — `<n-collapse accordion>` + store 单值 `expandedCycleId: number | null`,跨 cycle 对比不便。

①②③ 共一个根因:**浅色主题缺「同层级表面靠 hairline border 分隔、跨层级靠底色差分隔」的统一规则** —— 当前只有底色差,白叠白时差为零,无 border 兜底。

## §1 表面层级规则(①②③)

三个表面 token(`--ob-page-bg` / `--ob-card-bg` / `--ob-block-bg`,`tokens.css:4-7`)已存在,**本节真正新增的只是「同层级 hairline 分隔规则」+ 落 border + 注释化**,不新建 token:

- **三级表面(既有)**:页面 `--ob-page-bg`(`#eef0f3`,灰)> 卡 `--ob-card-bg`(`#fff`)> 内嵌块 `--ob-block-bg`(`#f6f7f9`)。
- **分隔规则(新增)**:跨层级用底色差;**同层级或同色相邻元素必须有 hairline border / 分隔线兜底**,不靠阴影。写进 `tokens.css` 注释作单一来源。

落地:

- **①**:hairline 加在全局 `.ob-card`(`tokens.css:23`)——浅色主题下所有卡面(header / perf-bar / DecisionStream / detail section)统一获得清晰边,与上面通用规则自洽。展开的 cycle detail 区做成「内嵌、属于该行」:内嵌底(`--ob-block-bg`)+ 中性 1px `--ob-border` 全边框（不用 accent 蓝——蓝竖带专给关键事件 keyrow，见 §1②）;内层 `section` 随全局 hairline 获得边。**注**:`.context/.reasoning` 已用 `--ob-block-bg`(`CycleDetailPanel.vue:163-164`),detail 区再上 block-bg 形成「灰-白(section)-灰」嵌套,靠 border 区隔属预期分层。
- **②**:cycle 行间分隔线提对比;**所有展开行**整体高亮(仅淡蓝底 `--ob-row-active`;蓝竖带专给关键事件 keyrow,二者正交避免同色混淆)——高亮需 CycleRowHeader 知道自身是否展开,由 DecisionStream 传入新增 `expanded` prop(见 T2)。
- **③**:PerformanceBar(底部抽屉)与上方 stream 间用更强分隔——加重 `border-top` + 顶部上投影制造"抽屉浮起"感;折叠条底色可微染区别于白内容区。

精确数值由实现期 Playwright 量化走查定(沿用 #80/#81:对比度含祖先 opacity 合成、**覆盖全部表面类型**——header/perf-bar/snapshot/decision 等,不只验 cycle detail)。

## §2 收益分析折叠提示对称(④)

展开态 `.exp-head` 右侧加 `点击折叠 ▾` hint,与折叠态 `点击展开 ▴`(右对齐 `.expand-hint`)对称。整条 head 保持可点。

## §3 cycle 多展开(⑤)

去 accordion,改单值展开态为多值。采用 **naive 受控写法,单一状态变更路径**(避免 toggle 与全量写回双路径并存):

- **store 状态**:`expandedCycleId: number | null` → `expandedCycleIds: number[]`(naive `expanded-names` 接收数组,数组比 Set 对响应式/测试更稳)。三处 reset(init / `selectSession` / `clearSelection`)改 `[]`。懒加载缓存 `cycleDetails: Map` 已 keyed by id,**天然支持多详情、无需改**。
- **受控入口 `setExpandedCycles(ids: number[])`**(唯一写展开态的 action):`this.expandedCycleIds = ids`(乐观更新),diff 出相对旧值的新增 id → 各调 `ensureCycleDetail(id)`。移除的 id 不动缓存(保留)。
- **`ensureCycleDetail(id)`**:仅懒加载,**成功路径不改展开态**;失败时 set error + 从 `expandedCycleIds` 移除该 id;await 前后带会话身份守卫(沿用 `sessions.ts:133/139` 模式,防切走后误改)。
- **`expandCycle` 退役**:其 toggle 语义被全量数组写回覆盖,无组件再调用(仅旧测试用),删除。
- **DecisionStream**:去 `accordion`;`:expanded-names="store.expandedCycleIds"`;`@update:expanded-names="store.setExpandedCycles"`。点击表头不再直调 `expandCycle`。
- **批量「收起全部」控件**:本迭代非目标,列 follow-up。

## §4 验证策略

- **⑤**:`store.spec.ts` / `DecisionStream.spec.ts` TDD —— 多展开断言、新增项触发懒加载、移除项保留缓存、失败仅从数组移除单项 + set error;同步改写现有单值断言:
  - `store.spec`:26/64/67/68/157/172/178(所有 `expandedCycleId` 断言/赋值点)→ 改 `expandedCycleIds` 数组语义;`expandCycle` 用例改测 `setExpandedCycles`。
  - `DecisionStream.spec`:**31-36**(点击表头断言 `expect(store.expandCycle).toHaveBeenCalledWith(3)` → 受控路径下改断言 `setExpandedCycles`)+ **40**(单详情渲染 → 多展开多详情)。
- **①②③④**:Playwright 真实数据走查。**footgun**:当前所有 sim 会话 `react_steps=null` → CycleDetailPanel 扁平回退是唯一活路径(per memory),走查须落真实会话;perf-bar 折叠/展开两态都验;多展开后多 cycle 同时展开的边界也验。
- 全套 gate:后端 `pytest -q` 不回归、前端 `npm test` + `vue-tsc --noEmit` 0 error。

## §5 任务拆解(兼 plan)

| Task | 内容 | 文件 | 验证 |
|---|---|---|---|
| T1 | §1 表面层级规则 + ① 内层 section 边界 | `tokens.css` / `CycleDetailPanel.vue` / `DecisionStream.vue` | Playwright |
| T2 | ② cycle 行间分隔 + 所有展开行高亮(CycleRowHeader 新增 `expanded` prop,DecisionStream 按 `expandedCycleIds.includes(id)` 传入) | `DecisionStream.vue` / `CycleRowHeader.vue` | Playwright |
| T3 | ③ perf-bar 抽屉边界 | `PerformanceBar.vue` / `DashboardView.vue` | Playwright |
| T4 | ④ 折叠提示对称 | `PerformanceBar.vue` | 走查 |
| T5 | ⑤ cycle 多展开(store `expandedCycleIds` + `setExpandedCycles`/`ensureCycleDetail`,`expandCycle` 退役;DecisionStream 受控) | `stores/sessions.ts` / `DecisionStream.vue` | TDD |

建议序:T5(状态模型,改 DecisionStream 结构)→ T1/T2(同改 DecisionStream/CycleDetailPanel 样式)→ T3 → T4。

## §6 非目标 / scope 边界

- 不改后端、不动 schema、零迁移。
- 不重做整体配色;仅补「边界/层级」缺口 + 多展开。
- 不加批量展开/收起控件(follow-up 候选)。
- naive-ui 2.38.1 pin 不动(勿 npm update)。
