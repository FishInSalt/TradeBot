# WebUI 观察台 Phase 1b：前端 SPA 设计

## 背景与目标

Phase 1a 已交付只读 JSON API(`src/webui/`,6 端点,merge `3502f41`)。Phase 1b 在其上建一个浏览器前端,把 agent 的**决策过程**与**交易表现**从终端/session log 搬到浏览器。

本工具的本质是**决策可观察性**——agent 的思考(reasoning)与决策(decision)是主角,交易表现是佐证。布局与信息层级都服从这一定位。

**范围边界**:
- 纯前端 SPA,只消费 Phase 1a 的只读 API,**不向 agent 发任何指令**(start/stop/pause 属 Phase 3)。
- **Phase 1b 前端实现不改后端**;仅依赖一处**契约层准备(不触 DB,已就绪)**:① `_loads` 喂养的 JSON 字段(`trigger_context` / `state_snapshot` / `args`)放宽容纳 list 形态——同时修掉 Phase 1a `GET /api/cycles/{pk}` 对 list 形态 cycle(当前 capture 代码恒产 list,id≥1523 起 100%;dict 为早期实现历史遗留)的 500;② `ToolCallRow.result` 预留字段(恒 None)。两者均为 API 契约层、不触 DB,详见 §7。
- 单机单人,localhost,无鉴权。

## 技术栈

| 关注点 | 选型 | 理由 |
|---|---|---|
| 框架 | Vue 3 + TypeScript | 用户既定 |
| 构建 | Vite | 用户既定;dev server + 同源 build |
| 组件库 | **Naive UI** | TS-native、tree-shakeable、内置暗色主题(交易台合适)、无全局 CSS reset 污染。优于更重的 Element Plus |
| 状态 | **Pinia** | 用户既定;集中 store,为 Phase 2/3 写操作预留 |
| 路由 | vue-router(**hash mode**) | 深链 `/#/sessions/:id`;hash 模式刷新永命中 `/`、后端零改(理由见 §构建/开发/部署) |
| 图表 | lightweight-charts | 净值曲线 |
| 类型 | openapi-typescript | 从后端 `/api/openapi.json` 生成,后端 schema 为唯一真相源 |
| 测试 | Vitest + Vue Test Utils | 与 Vite 一体 |

## 整体布局:主从式 App-shell

整体固定满视口(不整页滚,VS Code 式),分区各自独立滚动:

```
┌───────────────────────────────────────────────┐
│ 顶栏  TradeBot 观察台                  (固定)    │
├──────────┬────────────────────────────────────┤
│ 会话列表  │ 实时状态卡 (常驻顶部)                │
│ (常驻,    ├────────────────────────────────────┤
│  独立滚)  │ 决策流 · 核心                        │
│ ● sim#19 │ (唯一大滚动区;手风琴就地展开)        │
│   sim#18 │                                      │
│   sim#17 ├────────────────────────────────────┤
│          │ 表现概览 (常驻底部摘要条)            │
└──────────┴────────────────────────────────────┘
```

- **左栏 — 会话列表**:常驻,点选即切会话,URL 同步 `/#/sessions/:id`(hash mode,可深链刷新)。每行:名称、symbol、状态点、`total_return_pct`。自身可独立滚。
- **主区**(列向 flex,占满剩余宽高):
  - **状态卡横条**(顶部常驻):当前状态、position、挂单数、提醒数。
  - **决策流**(`flex:1`,`overflow-y:auto`,**唯一大滚动区**):agent 决策时间线,核心区。
  - **表现概览**(底部常驻摘要条):净值曲线 + 关键已实现指标,无需滚页即见。
- **可选后续(v1 不做)**:底部表现面板像 VS Code 那样可拖拽调高/折叠。v1 用固定高摘要条。

布局选型经可视化线框确认:列表→详情下钻改为**常驻主从**(直击多会话切换痛点);表现概览置底(变体 2);决策流独立滚、表现常驻可见(VS Code 式滚动)。

## 决策流与 cycle 详情

### 列表项(折叠态)

决策流按 `id` 倒序(keyset,新在顶;后端 `get_cycles` 即 id DESC,与轮询 `after_id` 游标同键),每条 cycle 一行,数据来自 `GET /api/sessions/{sid}/cycles`(`CycleRow`):
- `created_at`(本地显示)· `triggered_by` 徽标(scheduled/conditional/alert)· `decision_head` 摘要 · `execution_status` · 右侧轻量遥测(`tokens_consumed`、`wall_time_ms`)。

### 展开态(手风琴,就地展开)

点击某行**就地展开**完整详情,**同时只展开一条**(再点别条则收起前一条)。展开时**懒加载** `GET /api/cycles/{pk}`(`CycleDetail` 全量)——避免一次性拉取所有 cycle 的长 `reasoning`(实测 reasoning 均值 ~9.6k、峰值 ~31k 字符)。

详情按 **R2-7 五维叙事**分区(五维 = `triggered_by` + `trigger_context` + `state_snapshot` + `reasoning` + `decision`;`AgentCycle` docstring 框定的叙事顺序:触发→现状→推理→决策):

1. **头部**:`created_at` · `triggered_by` 徽标 · 决策头条(取自 `decision` 首句)· 遥测 chips(`tokens_consumed` 及 in/out、`cache_hit_rate`、`wall_time_ms`、`model_id`)。让用户不展开也能扫到结果与成本。
2. **触发上下文** `trigger_context`(JSON;如 `[{"type":"scheduled_tick"}]`,alert 触发时含告警信息)。
3. **中途注入事件** `injected_events`(**仅当非空**渲染;cycle 执行中途注入的事件数组,NULL=无注入)。
4. **决策时状态** `state_snapshot`(JSON;余额/持仓/挂单等决策瞬间客观状态)。
5. **工具调用(感知)** `tool_calls`(默认折叠,显示摘要"N 个 · 最慢 X"):展开为表 `tool_name · status · duration_ms · error_type(失败行) · args`。**结果列见 §7 预留**。
6. **推理** `reasoning`(thinking,主角):全文,放在固定高度 + 内部滚动的容器,字符数标注。
7. **决策** `decision`(message):结构化多字段文本((1) Stance / (2) Active commitments / SL·TP …)。

`trigger_context` / `state_snapshot` / `injected_events` 在 API 里是 `dict | list | str | None`(`str` 为损坏/截断行的原始回退),前端渲染时:dict/list → 折叠 JSON 视图;str → 原样代码块。

## 数据流 / 状态管理

- **类型**:构建期 `openapi-typescript` 生成 `src/api/types.ts`(勿手改)。
- **API client**(`src/api/client.ts`):6 端点各一类型化 `fetch` 封装,非 2xx 抛 `ApiError`(带 status)。
- **Pinia store**(`src/stores/sessions.ts`):
  - **state**:`sessions[]`、`currentId`、`detail`(`SessionDetail`)、`live`(`LiveStatus`)、`performance`(`Performance`)、`cycles[]`(`CycleRow`,增量 append)、`cycleDetails`(`Map<id, CycleDetail>`,展开懒加载缓存)、`expandedCycleId`、`loading`、`error`。
  - **actions**:
    - `loadSessions()` — 拉会话列表。
    - `selectSession(id)` — 设 currentId,并发拉 `detail`+`live`+`performance`+首屏 `cycles`(limit=50)。
    - `expandCycle(id)` — 设 `expandedCycleId`;若 `cycleDetails` 无缓存则拉 `/cycles/{pk}` 存入。
    - `pollTick()` — 见 §轮询。
- **去重**:增量 cycles 以 `id` 去重再 append(后端 `after_id` 已修空洞,但前端仍按 id 幂等合并以防重叠 tick)。

## 轮询(usePolling)

`src/composables/usePolling.ts`:
- 间隔 **5s**;**仅当 `currentSession.status === 'active'`** 才轮询,paused 会话不轮询。
- 每 tick:① `GET /live` ② `GET /performance` ③ 增量 `GET /cycles?after_id=<当前最大 cycle id>`,新 cycle append 到流顶。
- `document.hidden`(标签页不可见)暂停,可见恢复——省后端读。
- 瞬态错误静默重试,不炸 UI;连续 ≥3 次失败在状态卡角标提示"轮询中断"。
- 切换会话 / 组件卸载时清理定时器。

## 工具调用结果:预留设计空间

**现状缺口**:`tool_calls` 表无 output/result 列 → 后端读不到、前端无法展示工具返回结果。这是可观测性的**主要痛点**,定位为 Phase 1b 之后**第一优先后端项**。

两层 seam 使后端补全后前端**零改**点亮:
- **API 契约层(已就绪)**:`ToolCallRow.result: dict | list | str | None = None`,当前**恒为 None**(DB 无对应列,query 不取),schema 标注为预留。该字段进入生成的 `types.ts`,前端类型已就绪。
- **UI 层(Phase 1b 实现)**:工具行展开区现示 `args`,并为 `result` 预留位 + **诚实空态**:「结果未持久化(待后端补全)」——让缺口可见,而非静默消失。

**真正的捕获**(给 `tool_calls` 加 `result` 列 + 执行层 capture 工具返回值)是**独立后端 mini-iter**:它修改写入路径与 DB schema,超出 Phase 1b 只读前端范围。落地后只需 `queries.py` 取该列、`result` 字段自然填充,UI 空态自动变为真实结果。

## 表现概览

底部摘要条,数据来自 `GET /api/sessions/{sid}/performance`(`Performance`):
- **净值曲线**(lightweight-charts):`equity_curve`(逐 cycle 盯市,含未实现 PnL)。实现注记:逐 cycle、同日可能多点,line series 的 `time` 须用秒级 `UTCTimestamp`(非 business-day 字符串),否则同日点冲突。
- **关键指标**:`total_return_pct` / `net_pnl` / `net_win_rate` / `max_drawdown_pct` / `total_trades`。
- **双口径标注(必须)**:`equity_curve` 是盯市口径(含浮盈浮亏),与 `total_return_pct`(gross 已实现)、`max_drawdown_pct`(net 已实现 equity 模拟)**不同口径、不可逐点对账**。UI 须分别标注,避免用户把曲线回撤与 `max_drawdown_pct` 数字对齐(沿用 Phase 1a `Performance` schema 的 by-intent 说明)。
- **成交表** `trades`:可折叠展开(摘要条默认只显指标 + 曲线)。

## 实时状态卡

数据来自 `GET /api/sessions/{sid}/live`(`LiveStatus`):
- `status`(active/paused,原始字段,**非"运行中"断言**)+ `last_active_at`(原始戳,让陈旧的 active 自证;沿用 Phase 1a §5.2 定位)。
- `position`(无则"空仓")、`open_orders`、`active_alerts`。

## 构建 / 开发 / 部署

- **开发**:`npm run dev`(:5173);`vite.config.ts` 的 `server.proxy` 把 `/api` 代理到 `http://127.0.0.1:8000` → 开发期零 CORS,**后端不加任何中间件**。
- **构建**:`npm run build` → `frontend/dist`(`build.outDir`);由现有 `app.py` 的 `StaticFiles(directory=frontend/dist, html=True)` 在 `/` 同源挂载(Phase 1a 已写好挂载逻辑)。
- **路由/深链**:vue-router 用 **hash mode**(`createWebHashHistory`)。理由:`StaticFiles(html=True)` 对未知路径**不做 SPA fallback**(实证 starlette 1.0.0:`/sessions/x` → 404,仅 `/` 与真实文件返 200);history mode 刷新深链会在 prod 404(dev 有 vite fallback 故隐形)。hash mode 下 URL 永远是 `/`、刷新必命中 `index.html`,**后端零改**——契合范围边界。
- **生成类型**:`npm run gen:types`(脚本调用 openapi-typescript,需后端在跑或有离线 openapi.json)。

## 目录结构

全部新增在 `frontend/`,不碰 `src/`:

```
frontend/
  index.html
  package.json
  tsconfig.json
  vite.config.ts            # server.proxy /api → :8000; build.outDir = dist
  src/
    main.ts                 # createApp + Pinia + router + Naive UI
    App.vue                 # app-shell 壳:顶栏 + 左列表 + <router-view>
    router.ts               # hash mode：/ 与 /#/sessions/:id（createWebHashHistory）
    api/
      types.ts              # openapi-typescript 生成(勿手改)
      client.ts             # 类型化 fetch + ApiError
    stores/
      sessions.ts           # Pinia
    composables/
      usePolling.ts         # 5s active-only 增量轮询
    views/
      DashboardView.vue     # app-shell 主区路由目标(/sessions/:id)
    components/
      SessionList.vue       # 左栏会话列表
      LiveStatusCard.vue    # 状态卡横条
      DecisionStream.vue    # 决策流(手风琴)
      CycleRowItem.vue      # 单条 cycle(折叠/展开态)
      CycleDetail.vue       # 展开详情(五维分区 + 工具表 + result 预留)
      PerformanceBar.vue    # 底部表现摘要条
      EquityChart.vue       # lightweight-charts 封装
      TradesTable.vue       # 可折叠成交表
      JsonBlock.vue         # dict/list 折叠 JSON / str 原样代码块
```

## 测试策略(Vitest + Vue Test Utils)

重点测逻辑层,组件做轻量冒烟,不追像素级:
- **API client**:2xx 解析、非 2xx 抛 `ApiError`。
- **Pinia store**:`selectSession` 并发装配、`pollTick` 增量 append + 按 id 去重、`expandCycle` 懒加载缓存命中。
- **usePolling**:`status!=='active'` 不轮询、`after_id` 取当前最大 id、`document.hidden` 暂停/恢复、卸载清理。
- **时间显示**:出站带 Z 的 UTC 串按本地时区正确呈现。
- **组件冒烟**:`SessionList` 渲染 + 选中态、`CycleRowItem` 手风琴开合(只开一条)、`CycleDetail` 工具结果空态文案、空数据态(无会话/无 cycle/空仓)。

## 后续(非本期)

1. **【第一优先后端项】工具调用结果持久化**:`tool_calls` 加 `result` 列 + 执行层 capture;落地后本期预留 seam 自动点亮。
2. 底部表现面板可拖拽调高 / 折叠(VS Code 式)。
3. Phase 2:多会话并发编排器。
4. Phase 3:WebUI 接管会话创建/控制(start/stop/pause)。
