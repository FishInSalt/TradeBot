# WebUI 收益分析抽屉抬高展开态高度

## 背景

收益分析抽屉 `PerformanceBar.vue` 展开态的高度由单条硬上限决定:

```css
.perf-bar.expanded { max-height: 55vh; overflow-y: auto; }
```

而展开态主内容栈（有持仓、交易表折叠时）固有高已超 55vh:

| 区块 | 高度 |
|---|---|
| 抽屉头 exp-head | ~32px |
| 当前持仓条 held-bar（仅未平仓） | ~46px |
| 价格 K 线 price-section（工具栏 + 280px body） | ~342px |
| 净值曲线 / 指标网格 exp-grid | ~160px |
| 交易历程按钮 trades-fold | ~30px |
| **合计（下限）** | **~610px** |

> 此为**下限**——未计入 `.perf-bar.expanded` 自身 `padding:8px×2=16px` 与 exp-head padding。Playwright 实测(交易表折叠)主段真实高 ≈ **647px**。

55vh 在常见视口:900px 高 ≈ 495px、1050px 高 ≈ 577px——两者都 < ~647px,故主内容必须在抽屉内上下滚动才能看全（再展开交易表更甚）。

注意:交易历程表 `TradesTable` 是 `n-data-table` 自带 `max-height:280` 内滚,**设计上就独立滚动**、本不指望塞进抽屉。真正"塞不下要上下滑"的是 **K线 + 净值 + 指标 + 持仓条** 这一主段。

## 目标与范围

**目标**:展开抽屉时主内容段（交易表折叠时）一屏可见、不再需要在抽屉内上下滚。

**范围内**:纯前端、仅 `PerformanceBar.vue` 一处 CSS——展开态 `max-height` `55vh → 80vh`。无后端、无迁移、无新设计令牌、无新交互。

**Non-goals**:
- 不做连续拖拽调高（drag handle / pointer 事件 / localStorage 持久）——离散档已够,连续拖拽对只读观察台是高复杂度换低边际收益。
- **不做独立"最大化"档**（更大 cap 如 88vh + 切换按钮）——立项时曾设计 A+C 双档,但 Playwright 实证证明第二档在 flex 列里是空操作,**故砍掉、只保留单次抬高**。证据见 §为何单档。
- 不改抽屉以外布局（`DashboardView` flex 列不动）。
- 不动折叠态细条、不动任何指标/图表/交易表内容与口径。

## 设计

仅一处改动:

```css
/* 55vh → 80vh */
.perf-bar.expanded { max-height: 80vh; overflow-y: auto; padding: 8px 16px; }
```

抽屉的状态机（折叠 `expanded` boolean ↔ 展开、折叠条点击展开、exp-head 点击折叠）与按钮/样式**全部不变**。

### 布局机制（cap-not-target + flex 收缩）

理解为何"单次抬高"是对的、"第二档"是错的,须先认清抽屉高度由谁决定:

**抽屉渲染高 = `min(内容高, max-height cap, flex 列可用高)`。**

- `.perf-bar` 为 `flex:0 1 auto`(无 `flex` 声明)、不 grow → 只长到内容高,`max-height` 是**滚动上限,不是目标高**:内容装得下就不到 cap。
- `.dashboard` 是 flex column,自上而下三块:`.session-header`(SessionMeta + LiveStatusCard + divider + tz-note,`flex:0 1 auto`、~140px)、`.stream-wrap`(`flex:1 min-height:0` = `flex:1 1 0%`)、`.perf-bar`(`flex:0 1 auto`)。容器实际高 = 视口 − 应用外壳(实测 ~64px)。
- **正自由空间**(三块之和 < 容器):`.stream-wrap`(唯一 grow:1)吸收剩余 → 决策流可见;`.perf-bar` = 内容高。
- **负自由空间**(之和 > 容器,如展开交易表):`.stream-wrap` 缩权重 `shrink×basis = 1×0 = 0`、**不参与 flex 收缩**,坍向 0;负空间由 `.session-header` 与 `.perf-bar` 分摊 → 会话头轻 clip、`.perf-bar` 被**列可用高**限制在 cap 之下。flexbox 不撑破容器,故不溢出、不遮挡。

### 为何单档（实证砍掉"最大化"）

立项设计为 A(抬高)+C(独立"最大化"档 80vh→88vh + 切换按钮)。inline TDD 实现 A+C 后用 Playwright 在真实会话(sim #22、95 笔交易、未平仓)实测,**两档高度差恒为 0**:

| 视口 | 绑定约束 | 常规 80vh 高 | 最大化 88vh 高 | 差 |
|---|---|---|---|---|
| 897px（典型笔记本，交易表展开） | flex 列可用高 ~680 < 两 cap | 680 | 680 | **0** |
| 1300px（大屏，交易表展开） | 内容 988 < 80vh cap 1040 | 990 | 990 | **0** |

根因:由上式,cap 几乎从不是三者里最小的——短视口下"flex 列可用高"抢先绑定(< 两 cap),大视口下"内容高"抢先绑定(< 两 cap)。80vh 与 88vh 只在"内容恰落在 80vh~88vh 之间 **且** 列足够松"的 ~30px 视口窄带才有可见差。即:在 flex 列里用更大 `max-height` 做"最大化"是**错的机制**,按钮装上即空操作。真要"近全屏"须让抽屉脱离列约束(overlay),非本迭代目标。

而**单次抬高确有效**:实测 897px 视口、交易表展开下,抽屉从 55vh 的 ~493px 提到 80vh 的 ~680px(由 flex 列可用高封顶);交易表折叠下内容 647px 在 80vh(cap 720)内一屏可见、不再内滚。痛点解决,无需第二档。

### 高度取值依据

主段真实高 ≈ 647px(实测,含 K线/净值/指标/持仓)。选 80vh:900px 视口 cap=720 > 647 → 折叠态主段一屏不滚;较小视口(~800px,cap=640)亦接近全显。`max-height` 是上限非定值,内容更短则抽屉更矮、决策流更多;交易表展开使内容超列高时,抽屉到列可用高上限、交易表自身 `max-height:280` 内滚(设计如此,非本迭代消除目标)。

## 错误与边界

- 无 `performance` 数据:整个抽屉 `v-if="perf"` 不渲（现状,不变）。
- 折叠/展开:沿用现有 `expanded` boolean 状态机,无改动、无回归。
- 交易表展开致内容超列高:抽屉到列可用高上限、内部滚动条接管;会话头在负自由空间下轻 clip、决策流坍缩——属既有 flex 行为(本改仅抬高 cap、不改 flex 结构)。

## 测试计划

本次为**纯 CSS 取值改动**（`max-height: 55vh → 80vh`），无状态/模板/行为变化:

- **单测**:`max-height` 的 vh 值在 jsdom 下不可靠地参与计算,无法有效断言;不新增 vh 断言。现有 `test/PerformanceBar.spec.ts`(折叠/展开/各区块渲染、交易表折叠展开)须**全绿无回归**,守护状态机不被误改。
- **真实数据 Playwright**(已执行,sim #22):
  - 折叠态:无 `.expanded`、collapsed-bar 可见。
  - 展开态(表折叠,897/900px 视口):`.perf-bar` 高 ≈ 649 = 内容高、**不内滚**(对比 55vh 下 cap 495 < 647 必滚)。
  - 展开交易表:抽屉到列可用高上限(~680)、内部滚动接管、交易表渲染。
  - console 0 error / 0 warning(强刷后)。

## 验收标准

- 打开有 K线+净值+指标+持仓的会话、展开抽屉:交易表折叠时主段一屏可见、无需在抽屉内上下滚(≥~835px 视口)。
- 折叠/展开切换正常,无新增按钮、无行为回归。
- 前端 vitest 全绿（含现有 PerformanceBar 用例无回归）+ `vue-tsc` 0 + build 绿;真实数据 Playwright 已验证上述行为。
