# WebUI 收益分析观察台重设计

## 背景与动机

观察台底部「收益分析」区(`PerformanceBar` + `EquityChart` + `TradesTable`)是 sim 性能复盘的主要入口,但当前存在四类问题:

1. **后端已算的关键指标被前端丢弃。** `Performance` schema 有 13 个字段,`PerformanceBar` 只渲染了 5 个标量(总回报 / 净PnL / 净胜率 / 最大回撤 / 总交易)。其中 4 个已算未展示的标量指标 `total_fees` / `net_profit_factor` / `net_winning_trades` / `net_losing_trades`,由 `MetricsService` 算出、经 API 返回、前端类型已生成,却未渲染。`total_fees` 量级巨大:实测 sim #19 毛PnL **+6.90**、手续费 **102.03** → 净 **−95.14**,手续费把一个打平的策略亏成 −0.95%;Phase 1 设计 spec 本就把「盈亏比」列入表现概览,实现时遗漏。

2. **成交表数据杂乱,看不清交易历程。** `TradesTable` 列为 时间/动作/方向/价格/数量/PnL/费;「动作」列因 query 已 `WHERE action='order_filled'` 而恒为同值(纯噪声);开仓/平仓只能靠 pnl 空否反推;「价格」对开/平两义;数值裸 `.toString()`(如 `2.05636000000119`)未格式化;且分不清平仓是 agent 主动 / 止损 / 止盈 / 强平。

3. **收益区常驻、挤占决策时间线。** `PerformanceBar` 固定占底部最高 40vh,而看板主体是 `DecisionStream`。

4. **「总回报」标签含糊。** 其实是 `total_return_pct`(毛 PnL 的百分比),易被误读为最终战绩。

## 范围

**前端为主 + 两处后端字段暴露,无迁移、不动 `MetricsService`。** 展示值多数从现有 `Performance` API 字段派生(见「数据来源与派生」);两处后端改动都只是把 DB 现有数据经 API 暴露:

- `TradeRow` 增 `trigger_reason`(`trade_actions.trigger_reason` 现有列)→ 支持平仓/开仓触发细分。
- `Performance` 增 `open_position: OpenPositionBrief | None` → 支持未平仓时的未实现收益展示。**数据源 = `SimPosition`(权威当前态),非 `state_snapshot.position`(本轮开始态、会与权威矛盾)**;详见下「open_position 数据源」。
- `Performance` 增 `total_pnl: float`(`MetricsService.total_pnl` 现有,毛额)→ 毛PnL 直取,免前端反推 `total_return_pct/100*initial`。

涉及文件:

- `src/webui/schemas.py` — `TradeRow` 增 `trigger_reason: str | None`;`Performance` 增 `open_position`(类型见下)+ `total_pnl: float`。新增 `OpenPositionBrief`(side/contracts/entry_price/unrealized_pnl/pnl_pct_of_notional)与既有 `PositionBrief`(schemas.py:45,side/contracts/entry_price)字段重叠但语义不同(后者=feed-head 开始态、无未实现;前者=当前态+未实现);平行模型(不复用 PositionBrief,因后者无未实现字段且语义为开始态)
- `src/webui/queries.py` — `get_performance` 映 `t.trigger_reason` + `m.total_pnl`;`open_position` 以已查的 `pos`(`SimPosition`,queries.py:345)为权威构造 side/contracts/entry_price(平/空 → None),unrealized_pnl/pnl_pct_of_notional 仅当最新 cycle `state_snapshot.position` 与 `pos` **同向**时借用、否则 None
- `frontend/src/components/PerformanceBar.vue` — 重构为可折叠底部抽屉 + 指标分层 + 当前持仓条 + 布局
- `frontend/src/components/TradesTable.vue` — 重写为 A+ 交易历程表
- `frontend/src/utils/trades.ts` — **新增**:持仓周期(episode)派生 + 类型标签 + 周期级聚合(计数/胜负/盈亏比/最佳最差)纯函数
- `frontend/src/utils/format.ts` — 新增带符号百分比格式化
- `frontend/src/components/EquityChart.vue` — 不变,复用
- `frontend/src/views/DashboardView.vue` — 抽屉折叠/展开下的高度协同(若需)
- `frontend/openapi.json` + `frontend/src/api/types.ts` — 随 schema 变更重生成(`npm run gen:types`)

### 不做(YAGNI / 数据驱动触发)

- **拖拽调节面板大小。** 已有三级控制(折叠条 / 展开面板 / 表自身折叠)+ 内部滚动覆盖主要诉求;拖拽 resize 需拖拽态、min/max 钳制、窗口 resize 重算、持久化,为尚未验证的灵活性。固定高度用着别扭再加(独立小迭代)。
- **「未实现净收益」估算。** 只展示未实现毛(盯市 mark-vs-entry),不估尚未发生的平仓费(假设性数字,违反 fact-only)。
- **`MetricsService` 改动 / 数据库迁移。** 两处后端暴露均读既有数据。
- **折叠/展开状态持久化(localStorage)。** 默认折叠,ephemeral 组件态。

## 数据来源与派生

**口径基准:本面板"一笔交易" = 持仓周期(episode,flat→flat)**,与交易历程表同一单元。所有 per-trade 计数/比率均从 `trades` 经 episode 派生(前端);对**非-legacy 会话**(2026-05-16 起,fill 均有 amount),看板内部与表完全自洽(legacy 会话发散见「边界与降级」)。聚合 PnL / 费用 / 净值取自 API 标量(与粒度无关)。

| 展示项 | 来源 / 派生 |
|---|---|
| 净PnL 绝对值 | `net_pnl`(= Σ 各周期最终收益,见下) |
| 净PnL % | `net_pnl / initial_balance * 100` |
| 毛PnL 绝对值 | `total_pnl`(新暴露,毛额直取;免反推) |
| 毛PnL % | `total_return_pct` |
| 手续费(已实现) | `毛PnL − 净PnL`(按定义恒等于已实现手续费,保证 `毛 − 费 = 净` 精确) |
| 净胜率 | episode 胜数 /(胜数 + 负数) |
| 盈亏比 | Σ(最终收益>0) / \|Σ(最终收益<0)\|(无盈利周期 → `—`) |
| 最大回撤 | `max_drawdown_pct` |
| 持仓周期数 | 已平仓 episode 数 |
| 胜 / 负 | 最终收益 >0 / <0 的 episode 数 |
| 最佳 / 最差单笔 | `max` / `min`(各 episode 最终收益) |
| 净值曲线 | `equity_curve`（不变,盯市含未实现） |
| 交易历程表 | `trades`(按时序的完整 fill 列表)经 episode 派生 |
| 类型（开/加/平 + 触发细分） | `trade.pnl`(空=开仓型 / 非空=平仓型)+ 周期内是否已有同向开仓(开 vs 加)+ `trade.trigger_reason`(market/limit/stop/take_profit/liquidation 细分) |
| 未实现收益(毛) | `open_position.unrealized_pnl`(盯市 mark-vs-entry,未扣平仓费;snapshot 与权威持仓同向时借用,否则 None — 不展示「未实现」行) |
| 未实现 % | `open_position.pnl_pct_of_notional`(同上,同向才有) |
| 未平仓入场费 | `total_fees − 手续费(已实现)`(= 未平仓 open lot 的已付入场费;平尾时为 0) |
| 当前持仓 方向/数量/入场价 | `open_position.{side, contracts, entry_price}` ← `SimPosition`(权威当前态,与 LiveStatusCard 同源,杜绝同屏矛盾) |

**口径一致性(单一来源,非-legacy 会话):** per-trade 量(持仓周期数 / 胜负 / 净胜率 / 盈亏比 / 最佳最差)全部出自同一 episode 派生,故 Tier 2 计数与表头「N 笔」、胜率分母完全一致。`net_pnl`(API)= Σ 各周期最终收益(两者都 = Σ毛利 − Σ已实现手续费,已实证非-legacy sim#19 = −95.14)。**与 `MetricsService` 的 FIFO 计数差异:** `total_trades`/`net_win_rate`/`net_profit_factor`/`net_winning_trades`/`net_losing_trades`(schema 现有,本面板不再消费)按 FIFO lot 配对,加仓时一平拆多配,会比 episode 多;仅 2 个 clean 加仓会话(sim#19 / sim#20)受影响(如 sim#19:FIFO 8 笔 1/8 vs episode 7 周期 1/7;sim#1 的"加仓"混在 legacy null-amount 数据中,见边界),且这些 FIFO 数在人看的 WebUI 别处不展示(会话列表只显 `total_return_pct`,与粒度无关),无跨面矛盾。选 episode 因其 = 用户心智的"一笔" + 与表一致。

## 设计

### §A 底部抽屉(默认折叠)

`PerformanceBar` 持组件态 `expanded`(ref,默认 `false`,ephemeral)。

**折叠态**(细条,钉在看板底部,高约 40px;以平尾的 sim#19 为例):

```
收益 ▴   净PnL −95.14 (−0.95%)  ·  毛PnL +6.90 (+0.07%)  ·  手续费 102.03  ·  胜率 14% (1/7)        点击展开 ▴
```

四项:净PnL(绝对值+%)、毛PnL(绝对值+%)、手续费(已实现)、胜率。胜率括注 `(胜/总)`,总 = episode 胜数+负数(与净胜率同分母,例 sim#19 `1/7`)——区别于 Tier 2 的「胜负」(胜:负,例 `1/6`)。**未平仓时**末尾追加一格虚线框 `持仓 未实现(毛) {unrealized}`(见 §F)。点击整条展开。

**展开态**(高约 55vh,内部 `overflow-y:auto`):见 §D 布局。点头条 `收益分析 ▾` 折回。

### §B 指标分层

**Tier 1**(展开态右上,六格 grid,醒目,均为**已实现**口径),顺序固定:

1. 净PnL — 绝对值 + %,标签 `net已实现`
2. 毛PnL — 绝对值 + %,标签 `gross已实现`
3. 手续费 — 已实现口径,下标 `毛−费=净`
4. 净胜率
5. 盈亏比
6. 最大回撤 — 标签 `net equity`

**Tier 2**(Tier 1 下方,单行次级文字):持仓周期 · 胜负(胜:负)· 最佳单笔 · 最差单笔 · 初始余额。

双口径警示(`已实现指标 vs 盯市曲线 不同口径、不可逐点对账`)沿用现有文案,置于头条。

**口径声明(观察者跨工具对账提示):** 本台净胜率 / 盈亏比 / 胜负计数为**持仓周期(episode, flat→flat)口径**,与 `MetricsService` / `analyze_sim.py` 的 **FIFO roundtrip 口径**不同——加仓会话里一个 episode 会被 FIFO 拆成多个 roundtrip(零部分平仓下仅此一类差异),故两者胜率/盈亏比可能不同;本台选 episode 因 = 人读「一笔交易」直觉 + 与表头「N 笔」/胜率分母自洽(已决设计,见「数据来源与派生」)。

### §C A+ 交易历程表

**列:** 时刻(UTC) · 类型 · 方向 · 价格 · 数量 · 手续费 · 毛利PnL · 最终收益。
**单位:** 「金额单位 USDT · 时刻为 UTC · 价格为成交价」标注于表区顶部,不在每格重复。
**时刻:** 复用 `fmtUtc`。**数值:** 复用 `fmtNum` / `fmtSigned`(千分位、收尾、带符号)。

**持仓周期(episode)派生** — 纯函数 `deriveTradeFills(trades)`(`utils/trades.ts`):

输入 `trades`(按 `id ASC` 的 fill 列表,含开/平,每条带 `at/action/side/price/amount/pnl/fee/trigger_reason`)。

**类型标签词汇**(`CLOSE_LABEL` 与 `queries._classify_fill` 平仓词汇逐字同源,以 drift-guard 测试锁同步;`OPEN_LABEL` 为前端原创,有意不同于 `_classify_fill`——后者市价开仓返 None、限价为带方向的 `限价开多/空`,而本表方向另列):

```
OPEN_LABEL(reason, isAdd):  limit → isAdd?'限价加仓':'限价开仓'  ;  其余(market/未知) → isAdd?'加仓':'开仓'
CLOSE_LABEL(reason):  stop→'止损平仓' · take_profit→'止盈平仓' · liquidation→'强平' · limit→'限价平仓' · 其余(market/未知)→'平仓'
```

算法:

```
episodeIndex = 0
cur = []            # 当前周期内的 fill 累积（用于 fee 合计与 开/加 判定）
out = []
for f in trades:
    if f.amount == null: continue                 # 跳过 legacy null-amount fill（镜像 MetricsService 的 legacy skip,使表 Σ最终收益 与 API net_pnl 对齐;sim#8 等全 null 会话表将为空,如实)
    isClose = f.pnl != null                       # 平仓 = pnl 非空（已实证:无 pnl 空的平仓型；agent close_position 市价平仓亦带 pnl）
    if not isClose:                               # 开仓 / 加仓
        isAdd = (cur 中已有 open)
        type = OPEN_LABEL(f.trigger_reason, isAdd)
        out.push({ ...f, type, isAdd, grossPnl: null, finalPnl: null, feeBreakdown: null, episodeIndex })
        cur.push(f)
    else:                                          # 平仓 → 结束本周期
        fees = [x.fee ?? 0 for x in cur] + [f.fee ?? 0]
        finalPnl = (f.pnl) - sum(fees)
        type = CLOSE_LABEL(f.trigger_reason)
        out.push({ ...f, type, isAdd: false, grossPnl: f.pnl, finalPnl, feeBreakdown: fees, episodeIndex })
        episodeIndex += 1
        cur = []
return out
```

周期级聚合 `summarizeEpisodes(fills)`(同模块)从已平仓行的 `finalPnl` 算:持仓周期数、胜/负数、净胜率、盈亏比、最佳/最差单笔 —— 供 Tier 1/2 与表头复用(单一来源)。**除零守卫:** 胜+负=0(无决出周期 / 全打平)→ 净胜率 `—`;无盈利周期 → 盈亏比 `—`;无已平仓周期 → 最佳/最差 `—`。

**不变量(全库实证,2026-06-15 复核):** **零部分平仓** —— `trade_actions` 153 个平仓 fill(pnl≠null)按持仓重建 100% 全平,`trigger_context.is_full_close` 在真平仓事件中亦 100% = true(`is_full_close=false` 的 138 条全是开仓 fill,开仓本不平仓、该 flag 天然 false——勿将其误计为部分平仓)。故「平仓即结束周期」「开仓后同向再开 = 加仓」对现有全部数据成立。**降级:** 若未来出现部分平仓(连续平仓),首个平仓行会把当时累积的开仓手续费全摊给它(口径略偏),作为已知边界,待部分平仓真实出现时(数据驱动)再细化。会话以未平仓持仓结尾时,尾部开/加行 `finalPnl = —`、不递增 episodeIndex,如实呈现未平仓入场(其入场费见 §F「未平仓入场费」)。

**渲染:**

- 按 `episodeIndex` 奇偶**交替底色**,使同一笔交易的开/加/平视觉聚拢。
- 「类型」列直接显示派生 `type` 文本(开仓 / 加仓 / 限价开仓 / 平仓 / 止损平仓 / 止盈平仓 / 强平 / 限价平仓)。`isAdd` 行用琥珀底标签突出「加仓」;平仓细分(止损/止盈/强平)用与「平仓」可区分的弱样式(如颜色/角标),不喧宾夺主。
- 开仓/加仓行:毛利PnL、最终收益 = `—`。
- 平仓行:毛利PnL = `grossPnl`(带符号着色);最终收益 = `finalPnl`(带符号着色,加粗),其下补一行浅灰**逐笔算式** `= {grossPnl} − {fee1} − {fee2} …`(由 `feeBreakdown` 拼,各项对回上方各行手续费格)。

**默认折叠。** 折叠头 `交易历程（N 笔 · 净 {net_pnl}）▸`,N = 持仓周期数;点击展开表体。组件态 `showTrades`(默认 `false`,沿用现有惯例)。

### §D 布局(展开态)

```
┌ 收益分析 ▾    已实现指标 vs 盯市曲线 不同口径、不可逐点对账 ┐
├───────────────────────────────────────────────────────────┤
│ 〔当前持仓(未平仓)条 — 仅有持仓时出现,见 §F〕            │
├───────────────────────────┬──────────────────────────────┤
│ 净值曲线（盯市·含未实现）   │ Tier 1 · 最关心（六格 grid）  │
│ [EquityChart]             │ 净PnL  毛PnL  手续费           │
│ 10,000 → 9,904.86 · 峰…   │ 净胜率  盈亏比  最大回撤        │
│                           │ ── Tier 2 · 次级（单行） ──    │
│                           │ 持仓周期·胜负·最佳·最差·初始   │
├───────────────────────────┴──────────────────────────────┤
│ 交易历程（N 笔 · 净 X）▸     （默认折叠，点击展开 A+ 表）   │
└───────────────────────────────────────────────────────────┘
```

上半 grid `grid-template-columns: 1.15fr 1fr`(曲线 | 指标)。展开态容器 `max-height: ~55vh; overflow-y:auto`。折叠态时整个上下结构被细条替代。

### §E 着色规则

- **PnL 类**(净PnL、毛PnL、毛利PnL、最终收益、最佳/最差单笔、未实现收益):**按各自正负** —— 正 `--ob-pos`、负 `--ob-neg`。由符号驱动,非固定配色。
- **手续费 / 未平仓入场费:** 始终是成本,**不**按"正=绿"(会误导),用 `--ob-warn`(琥珀)标"拖累"。这是对"按正负着色"的明确例外。
- **当前持仓条:** 背景 `--ob-warn-soft`、边框/强调文字 `--ob-warn`(沿用 `InjectionCard` 既有处方,保对比度)。
- **最大回撤:** `--ob-neg`(亏损量级,惯例)。
- **净胜率 / 盈亏比 / 持仓周期 / 初始余额:** 中性默认文本色。

令牌一律引用 `--ob-*`(`tokens.css`),不写死 hex。

### §F 当前持仓与未实现收益(仅未平仓时)

**open_position 数据源(权威性):** 存在性 + `side`/`contracts`/`entry_price` 取自 `SimPosition`(权威当前态,与 `get_live_status` / LiveStatusCard 同源,queries.py:302/323/345),**不取 `state_snapshot.position`**——后者是「本轮开始态」快照,实测全库 3/21 会话与权威持仓矛盾(snapshot 显示已平的幻影持仓 / snapshot flat 却实际有仓),用它会让 PerformanceBar 持仓条与同屏 LiveStatusCard 自相矛盾。`SimPosition` 无 `unrealized_pnl`(mark 相关、未存),故 `unrealized_pnl`/`pnl_pct_of_notional` 从最新 cycle `state_snapshot.position` 借用,**仅当其 `side` 与 `SimPosition` 同向**(best-effort 一致性闸;**只比 `side` 不比 `contracts`**——若末轮同向改了仓位大小,借用值是 snapshot 旧 size 的盯市,而未实现本就是「本轮开始态」近似,可接受);不同向 / snapshot 无仓 → 该两值 None,持仓条仍渲(方向/数量/入场价)但不显「未实现」行(如实,不编造)。`SimPosition` 平/空 → `open_position = None`、本条不渲染。

`open_position != null` 时,在展开态头条下渲一条独立「当前持仓(未平仓)」条,**与 Tier 1 已实现指标口径分开**:

- `{方向标签} {contracts} @ {entry_price}`(方向按多/空着色)
- (`unrealized_pnl != null` 时)`未实现收益(毛) {unrealized_pnl}` + `{unrealized_pct}%` + 标签 `盯市,未扣平仓费`
- `未平仓入场费 {total_fees − 已实现手续费}` + 注 `已付,从净值扣`

折叠态末尾在 `unrealized_pnl != null` 时同步追加一格 `持仓 未实现(毛) {unrealized_pnl}`。

**口径自洽(实证 sim#21):** `初始 + 已实现净 + 未实现毛 − 未平仓入场费 = 盯市净值`(`10000 − 538.04 − 13.97 − 7.09 = 9440.90 ≈ 9440.89`)。未实现是**毛**(mark-vs-entry、未扣平仓费),入场费已单列且已从净值扣;两者分开后,**非-legacy 会话** Tier 1 的 `毛 − 手续费(已实现) = 净` 精确成立(legacy 见边界)。平尾会话(如 sim#19)`open_position = null`,本条不渲染、未平仓入场费 = 0、手续费 = `total_fees`。

## 边界与降级

- `store.performance` 为 null(未选会话 / 加载中):整条不渲染(沿用现有 `v-if="perf"`)。
- `trades` 为空:折叠头显示 `交易历程（0 笔）`,展开为空表占位;Tier1/2 各项回退 `—` / `0`。
- 净胜率胜+负=0、盈亏比无盈利周期 → `—`;`initial_balance` 为 0 → 百分比回退 `—`(避免除零,沿用后端 `>0` 守卫)。
- 坏 / 截断 fill 数据:`deriveTradeFills` 对缺失数值按 `?? 0` / `null` 处理,不抛异常。
- `trigger_reason` 为 null / 未知值:OPEN_LABEL → `开仓`/`加仓`、CLOSE_LABEL → `平仓`(泛标签兜底)。
- `open_position` null(平尾)→ 不渲染当前持仓条;`未平仓入场费 = total_fees − 已实现手续费` 此时应 ≈ 0,若因 FIFO caveat 出现微小残值,仅在当前持仓条内展示(平尾不展示),不影响 Tier 1。
- **legacy 会话(2026-05-16 前):** `MetricsService` 跳过 null-amount fill 与 invariant-violation fill,故 API 标量(net_pnl 等)排除它们;`deriveTradeFills` 同样 `continue` 跳过 null-amount fill,使表 Σ最终收益 与 net_pnl 在**大头**对齐(如 sim#8 全 null → 表为空、net_pnl=0,一致)。**残留发散:** invariant-violation fill(罕见,如 sim#1 的 1 条)前端无 FIFO 态、无法等价剔除,可能令 API-vs-表、`毛−费=净` 出现轻微不符。legacy 会话为存档/forensic 用途,选择器仍可选;此发散为已知边界,不为其加复杂前端 FIFO 重建(YAGNI)。

## 测试策略(TDD)

逐项 red-green:

1. **后端 — `get_performance` 暴露 `trigger_reason` + `open_position`(SimPosition 权威)+ `total_pnl`:** seed 带 `trigger_reason='stop'` 的 close fill → `trades[0].trigger_reason=='stop'`、`total_pnl` 等于 metrics 毛额。`open_position` 三态(对应实测矛盾会话):(a) `SimPosition` 有仓 + snapshot 同向 → side/contracts/entry 取 SimPosition、unrealized 借 snapshot;(b) `SimPosition` 有仓 + snapshot flat/异向(漏显反例)→ side/contracts/entry 仍取 SimPosition、`unrealized_pnl is None`;(c) `SimPosition` 平/空 + snapshot 有仓(幻影反例)→ `open_position is None`。
2. **`utils/trades.ts` — `deriveTradeFills`(纯函数,核心):**
   - 单开单平(market)→ 2 行,类型 `开仓`/`平仓`,平仓 finalPnl = pnl − (开费+平费),feeBreakdown 两项,episodeIndex 0。
   - 加仓周期(开+加+平)→ 3 行,中间行 isAdd/`加仓`,平仓 finalPnl = pnl − 三费,feeBreakdown 三项,同 episodeIndex。
   - 平仓细分:stop/take_profit/liquidation/limit → `止损平仓`/`止盈平仓`/`强平`/`限价平仓`;market/未知 → `平仓`。开仓细分:limit → `限价开仓`/`限价加仓`。
   - 两个连续周期 → episodeIndex 0/1;尾部未平仓 → finalPnl=null、不递增;孤儿平仓 → finalPnl = pnl − 平费;缺失 fee → 按 0。
   - **legacy 跳过:** `amount == null` 的 fill 被 `continue` 跳过(全 null 样本 → 空输出;混合样本 → 仅 clean fill 进表)。
   - **drift-guard:** CLOSE_LABEL 五标签与 `queries._classify_fill` 同值。
3. **`utils/trades.ts` — `summarizeEpisodes`:** sim#19 型样本(7 周期 1 胜)→ 持仓周期=7、胜负=1/6、净胜率=1/7、盈亏比=0.33、最佳/最差;全打平样本(胜+负=0)→ 净胜率/盈亏比 `—`(不抛)。
4. **`utils/format.ts` — 带符号百分比:** 正 `+0.07%`、负 `−0.95%`(U+2212)、null → `—`。
5. **`PerformanceBar.vue`:** 默认折叠(只见细条);折叠条四项;点击 `expanded` true、渲 Tier1 六格 + Tier2;Tier1 净在前毛在后;手续费下标 `毛−费=净`、琥珀类;净PnL 负红、毛PnL 正绿(sign 驱动);**有 `open_position` → 渲当前持仓条(未实现毛 + 未平仓入场费)+ 折叠条多一格;无 → 不渲**。
6. **`TradesTable.vue`(A+):** 默认 `showTrades` false(折叠头带 N 笔 + 净);展开后加仓行有「加仓」标签、止损平仓行显 `止损平仓`;平仓行有逐笔算式;开仓行最终收益 `—`;周期交替底色 class。
7. **Playwright(真实数据):** sim#19(平尾)— 折叠态四项数值正确、无持仓格、展开 A+ 表加仓周期(开空/加空/平,最终收益 −9.62 + 算式);sim#21(未平仓)— 当前持仓条显未实现(毛) −13.97 + 未平仓入场费 7.09、手续费已实现 402.77;console 0 error。

## 验收标准

- 后端仅 `TradeRow.trigger_reason` + `Performance.open_position`(SimPosition 权威)+ `Performance.total_pnl` 三处暴露既有数据;`MetricsService` 未动;无迁移;`api/types.ts` 已重生成。
- `open_position` 与同屏 LiveStatusCard 持仓**同源(SimPosition)、不矛盾**;snapshot 仅在同向时贡献未实现毛。
- 后端测试套件全绿(无回归);前端 vitest 全绿;vue-tsc 0 错。
- 默认折叠;展开见(当前持仓条 if 未平仓)+ 曲线 + Tier1 六格 + Tier2 + 交易历程(再折叠)。
- per-trade 计数全 episode 口径,Tier2「持仓周期」与表头「N 笔」、胜率分母一致;非-legacy 会话 `毛 − 手续费(已实现) = 净` 精确(legacy 跳 null-amount 后大头对齐,残留 invariant 发散见边界)。
- 加仓在 A+ 表中作为同一周期内一行「加仓」,最终收益扣全周期手续费并附逐笔算式;平仓细分标签正确。
- Playwright 在 sim#19(平尾)+ sim#21(未平仓)两态真实数据通过,console 无报错。
