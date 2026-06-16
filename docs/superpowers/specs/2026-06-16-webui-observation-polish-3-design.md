# WebUI 观察台打磨 #3 — 设计 spec

> 焦点：sim 观察台三处人读体验缺口。纯前端 3 项 + 后端 1 个可空字段（零迁移）。所有改动隔离、无架构变更。

## 目标

补齐观察 agent 交易行为时的三处读数/时效/层级缺口：

1. K 线 hover 任意 bar 都能读到 OHLC（当前仅成交点弹浮窗）。
2. 收益分析「当前持仓」的未实现 PnL 标明盯市 as-of 时间（当前无时间戳，掩盖了它是上一 cycle 的滞后值）。
3. 唤醒时状态的挂单区改为逐行排列 + 克制微染，提升可读性与关注度。

## 范围边界

- **只动** `CycleDetailPanel` 的「唤醒时状态·挂单」；`LiveStatusCard` 顶栏「挂单 N」是单行状态条，仍只显计数，不在本次范围（避免撑破状态条）。
- ② 仅给未实现 PnL 加 as-of，**不**给整条持仓打统一时间戳：side/contracts/entry_price 取自 SimPosition（实时权威态），只有 unrealized_pnl/pnl_pct 是借自最新 cycle snapshot 的滞后值。时间戳挂错位置会误导。
- 不新增依赖。lightweight-charts 维持 v4 当前版本。naive-ui 维持 pin 2.38.1。

---

## ① K 线 hover 统一浮窗：OHLC + 成交

### 现状

`PriceChart.vue` 的 `subscribeCrosshairMove` 回调仅当 `hoverMap.has(t)`（该 bar 有成交）时设 `hover.value` 弹浮窗；无成交 bar → `hover.value = null`，整张图任意非成交位置读不到任何价格数值。浮窗（`.pc-tip`）光标跟随。

### 改法

把浮窗从「仅有成交才弹」改为「悬停任意有效 bar 都弹」，OHLC 与成交合进同一浮窗：

- 顶部恒显该 bar 的 **OHLC 四值**（开/高/低/收），收盘 ≥ 开盘绿、否则红（复用 `POS_HEX`/`NEG_HEX`）。
- 该 bar 有成交时，OHLC 下方接现有成交行（类型·方向·价·量·毛利/最终），格式不变。
- 光标移出数据区（`param.time` 空 / 无 `param.point`）→ 浮窗隐藏。
- 按决策「只显示 OHLC 值」：**不加** Δ%、不加成交量（留作日后可选）。

### 数据流

crosshair 回调内：OHLC 取 lightweight-charts v4 的 `param.seriesData.get(series)`（返回该 bar 的 candlestick data，含 open/high/low/close）；fills 仍取 `hoverMap.get(t)`（可为空）。两者合进 `hover.value`。`hoverMap` 构建逻辑不变，唯一变化是「无 fill」不再 early-return null —— 只要有有效 bar 时间 + point 就弹。

`hover` 类型扩展：`{ x; y; ohlc: {open,high,low,close} | null; fills: DerivedFill[] }`。OHLC 为纯加法，不触碰撮合/markers 逻辑。

### 边界 & 测试

- 无成交 bar 悬停 → 浮窗出现且只含 OHLC 行。
- 有成交 bar 悬停 → 浮窗含 OHLC 行 + 成交行。
- 光标移出数据区 → 浮窗隐藏（`hover` 为 null）。
- 收 ≥ 开 → OHLC 绿；收 < 开 → 红。
- `seriesData` 取不到（防御）→ 不弹 OHLC（不抛错）。

---

## ② 未实现 PnL 加「盯市 as-of」时间戳

### 现状

`OpenPositionBrief`（`src/webui/schemas.py`）无任何时间字段。`PerformanceBar.vue` 的 held-bar 显示 side/张数/入场/未实现，无时间戳。后端 `_derive_open_position`（`src/webui/queries.py`）混合时效：side/contracts/entry_price 取自 SimPosition（权威实时态），unrealized_pnl/pnl_pct_of_notional 在「同向闸」内借自最新 cycle 的 `state_snapshot.position`（本轮开始态、盯市价停在上一 cycle）。5s 轮询期间未实现不变，只在新 cycle 落地才跳。

### 改法

- `OpenPositionBrief` 加可空字段 `unrealized_as_of: str | None`（ISO 时间串）。
- `_derive_open_position` 在借用未实现的**同向闸内**一并取 `snapshot["market"]["fetched_at"]`（与未实现同源、同一最新 cycle snapshot）。未借到（反向 / snapshot 无 position / 无 market.fetched_at）→ None。
- schema 重生 → `frontend/src/api/types.ts`。
- 前端 held-bar 在未实现那段尾部加 `<span class="muted">as-of {{ fmtUtc(openPos.unrealized_as_of) }}</span>`，仅当 `unrealized_as_of != null` 才渲染（项目全局 UTC，复用 `fmtUtc`）。

### 为何不前端推导

前端 `store.detail` 是**当前选中** cycle 的 snapshot，而 open_position 的未实现取自**最新** cycle 的 snapshot —— 二者可能不同 cycle，前端推导会时间错配。故必须后端在同源同 cycle 处出字段。

### 边界 & 测试

- 后端：同向 + market.fetched_at 存在 → `unrealized_as_of` 带值；反向 / 无 position / 无 market.fetched_at → None；同向但未实现为 None → as_of 也应为 None（同源，不单独漏带）。
- 前端：`unrealized_as_of` 有值才渲染 as-of span；None 不渲染（不留空 "as-of"）。

---

## ③ 唤醒时状态·挂单区（`CycleDetailPanel`）

### 现状

「唤醒时状态」section 内，挂单已有逐单明细（`order_type side @price ×amount`），但横向 inline 排（`.snap-item` inline-block，`margin-right`）；告警区是纵向逐条堆叠（`.alert-grp` flex-column）。两者排版不一致，多笔挂单时横排可读性差。挂单区与持仓/余额/告警同为平级、无视觉强调。

### a) 逐行排列

挂单值单元格改为纵向逐条（flex-column），与告警 `.alert-grp` 同结构。每笔挂单独占一行。纯 CSS（挂单的 `.snap-item` 在挂单上下文改为 block / 容器 flex-column；注意 `.snap-item` 当前与告警共用，须用挂单专属包裹类避免影响告警）。

### b) 克制微染

挂单值区加**很浅的底色微染**，与告警/持仓区分、轻度提示「待成交、值得关注」：

- 新增令牌 `--ob-pending-bg`（浅琥珀向，呼应挂单待成交语义；与 accent 蓝、告警语义都区分），落 `tokens.css` 单源。
- 挂单值容器套圆角小块 + 内边距 + 该底色。**克制**：不用醒目色、不加边框竖带（蓝竖带专给 keyrow）。
- **硬约束**：微染底色上承载的文字（挂单值为正文 default 色）对比度 ≥ 4.5，Playwright 含祖先 opacity 合成实测。该单元格刻意只放正文色挂单文本，**不放** muted/neg/pos 文字（如未来要放须复验或调浅底色——浅琥珀对 muted #6b7280 余量极小）。规避 `accent-soft #eff6ff` 致 muted 4.44 < AA 的旧坑。

### 边界 & 测试

- 多笔挂单 → 逐行渲染、不再横排。
- 微染单元格存在；Playwright 真实数据下该单元格全部文字对比度 ≥ 4.5。
- 告警区不受 `.snap-item` 改动影响（回归守护）。

---

## 交付与验证

- 纯前端 3 项 + ② 一个后端可空字段（零迁移：schema 加可空字段，无 alembic 视图改动）。
- 实现走 TDD（vitest 前端 / pytest 后端），逐条红→绿。
- 真实数据 Playwright 走查：① 三周期 hover OHLC（含粗 tf）、② 未平会话(如 sim#13) held-bar as-of、③ 挂单逐行 + 对比度量化。
- gate：前端 vitest 全绿 + vue-tsc 0 + build；后端 pytest 全绿。
