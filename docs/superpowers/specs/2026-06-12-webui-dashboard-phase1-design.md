# WebUI Phase 1 — 只读观察台设计

> 目标架构与三期路线图见 `2026-06-12-webui-target-architecture.md`。本文件是 Phase 1 的详细设计：一个**只读**的本机观察台，把 agent 的决策过程与交易表现从终端/session log 搬到浏览器。

## 1. 目标与范围

**目标**：本机单人（localhost）通过浏览器观察——
1. **决策时间线**：按时序看每个 cycle 的 触发 → 思考 → 工具调用(+入参) → 5 段决策叙事。
2. **表现概览**：净值曲线 + 收益/胜率/回撤/盈亏比 + 成交列表。
3. **实时状态卡**：会话的当前持仓/挂单/活跃告警 + 会话状态(`status`)/最后活跃(`last_active_at`)。
4. **会话列表**：在 DB 中所有 session 间切换（含正在运行与历史）。

**近实时**：sim worker 与观察台读写同一 SQLite 文件；前端轮询 live 端点（默认 5s），sim 写入新 cycle 后下次轮询即可见。无进程间通信。

### 范围边界（YAGNI — Phase 1 明确不做）

- ❌ 鉴权 / 多用户 / 公网（仅 localhost 单人）
- ❌ 市场上下文回放（agent 当时看到的行情/taker flow）—— 后续迭代
- ❌ 真 WebSocket/SSE 推送（用轮询；JSON API 为未来 SSE 留接口）
- ❌ 任何向 agent 发指令的写操作（纯只读）
- ❌ 会话创建/启停（Phase 3）
- ❌ 改 DB schema / 新增写表（净值曲线全部派生）

## 2. 与目标架构的关系

Phase 1 即目标架构的**数据面雏形**：`src/webui/queries.py`（只读层）+ `src/webui/app.py`（FastAPI JSON API）就是未来编排器的数据面，Phase 2 在同一 FastAPI app 上叠加控制面（`SessionSupervisor`）与 worker，不推翻本期产出。

## 3. 模块划分

两个强隔离单元 + 一个只读数据层。**不修改任何现有 `src/` 模块**；仅新增 `src/webui/` 与 `frontend/`，并在 `pyproject.toml` 增可选依赖 extra。

### 后端 `src/webui/`（Python / FastAPI）

```
src/webui/
  __init__.py
  __main__.py        # python -m src.webui → uvicorn 启动
  app.py             # FastAPI app、路由注册、静态资源挂载（frontend/dist）
  queries.py         # 只读查询函数（核心可测单元，仅依赖 src/storage + metrics）
  schemas.py         # pydantic 响应模型 = API 契约
```

- `queries.py`：纯 async 函数，输入 `engine` + 参数，输出 pydantic/dataclass。每个函数独立可测。
- `app.py`：HTTP 层极薄——解析参数 → 调 `queries` → 返回 `schemas`。无业务逻辑。
- 复用现有：`models.py`、`views.py`、`src/services/metrics.py`、`_collect_roundtrips_from_trade_actions`。
- **只读连接，不跑 init/migration**：webui 自建指向同一 DB 文件的 async engine，**不调 `database.py:init_db()`**——`init_db` 会跑 Alembic upgrade / `create_all` / apply views + 起写事务，等于在别的进程 live 的 DB 上动写路径。webui 仅建只读 `async_sessionmaker`。
  - **强制只读 = `file:<path>?mode=ro`（spike 已实测验证，非假设）**：用 `mode=ro` URI（aiosqlite `uri=True`）真正只读打开。2026-06-12 对 live sim #19 的 WAL 库（4.2MB 未 checkpoint -wal）实测（SQLite 3.50.4 / macOS arm64）：
    - ✅ live sim 在跑时 `mode=ro` 读到的 `max(agent_cycles.id)` 与普通读写连接**完全一致**——读得到未 checkpoint 的已提交帧（主用例成立）。
    - ✅ 无 live writer 的副本（模拟 sim 已停）、甚至 `-shm` 缺失时，`mode=ro` 仍从 `-wal` 在堆内重建 wal-index、读到最新帧。
    - ⛔ **禁用 `immutable=1`**：实测它忽略 `-wal`、返回陈旧数据（差 ~1h/11 cycle）。
    - 结论：`mode=ro` 即可，杜绝误写/误建锁文件；**不要**用 `immutable`。（注：WAL+ro 行为随 SQLite 版本而异，此结论锚定目标环境 macOS/3.50.4。）
    - **再加 `PRAGMA busy_timeout`（如 3000ms）+ `PRAGMA query_only=ON`**：成本极低，吸收 sim 瞬时 checkpoint / fresh-DB rollback-journal 并发读撞上的短暂锁（消 `database is locked` 告警），并在连接层再兜一层只读。
  - **WAL 前置条件**：WAL 是 DB 级持久设置，由跑 sim 的进程经 `init_db` 启用（`database.py` `PRAGMA journal_mode=WAL`），对已被 sim 跑过的库成立。**若对一个从未被 `init_db` 过的 fresh DB 先起 webui**，库仍是 rollback-journal 模式，并发读可能撞 `database is locked`。localhost 场景可接受；UI/文档一句话点明"先有跑过的 sim 库"前置。

### 前端 `frontend/`（Vue 3 + TS + Vite）

```
frontend/
  index.html
  package.json  vite.config.ts  tsconfig.json
  src/
    main.ts
    App.vue                    # 路由壳
    api/client.ts              # 类型化 fetch 封装
    types.ts                   # 镜像后端 schema
    composables/usePolling.ts  # live 端点定时轮询
    views/
      SessionList.vue          # "/" 会话列表
      SessionDetail.vue        # "/session/:id" 主面板
    components/
      LiveStatusCard.vue       # 顶部常驻：持仓/挂单/告警/会话状态+最后活跃
      DecisionTimeline.vue     # cycle feed
      CycleCard.vue            # 单 cycle 展开：触发→思考→工具→5段决策
      PerformanceOverview.vue  # 指标 + 成交列表
      EquityChart.vue          # 净值曲线（lightweight-charts）
```

- **路由**：`/`（会话列表）→ `/session/:id`（详情：`LiveStatusCard` 常驻顶部 + tab 切换 [决策时间线 | 表现概览]）。
- **图表库**：TradingView **lightweight-charts**（金融级、轻量）。
- **构建**：`vite build` → `frontend/dist/`，由 FastAPI 挂载为静态资源。

## 4. 数据来源映射

| 查询函数 | 数据源 | 说明 |
|----------|--------|------|
| `list_sessions()` | `sessions` | name/symbol/status/created_at/last_active_at；附每会话 `total_return_pct`(调 MetricsService) 与 cycle 数 |
| `get_session(id)` | `sessions` | 元信息（timeframe / `scheduler_interval_min` / initial_balance / token_budget / status） |
| `list_cycles(id, limit, before_id)` | `agent_cycles` | 分页 feed 摘要行：cycle_id / triggered_by / created_at / decision 首段 / tokens_consumed / wall_time_ms |
| `get_cycle_detail(pk)` | `agent_cycles` + JOIN `tool_calls`(同 cycle_id+session_id) | reasoning / decision(5 段) / trigger_context(JSON) / state_snapshot(JSON) / injected_events / 工具调用列表(名 + 入参 args + status + duration_ms + error_type) / token & timing 明细 |
| `get_performance(id)` | `MetricsService.compute()` + 净值曲线 | 标量指标 + 成交列表 + equity series |
| `get_live_status(id)` | `sim_positions` / `sim_orders`(status=`open`) / `v_alert_lifecycle`(`final_status='active'`) / `sessions` | 当前持仓/挂单/活跃告警 + 会话状态(`status`)/最后活跃(`last_active_at`)（§5.2：不重构唤醒/liveness） |

**实现注意（数据源约束，已核对代码）**：

- **`MetricsService` 必须显式传 `initial_balance`**：其 `__init__` 默认 `10000.0`（`metrics.py:219`），而 `Session.initial_balance` 默认 `100.0`（`models.py:42`）——`queries` 必须从 `sessions` 读该会话真实值并传入，否则 `total_return_pct` / MDD 全错。
- **`list_sessions` 的 per-session 指标是 N+1**：`compute()` 内含多次查询 + FIFO，列表 N 个会话即 N×。localhost 小 N 可接受；如列表变长，可将 return% 列改为惰性/按需计算。文档在此点明，不在 v1 优化。
- **活跃告警是 DB 事件还原**：`v_alert_lifecycle.final_status='active'` 由 `trade_actions` 的注册/触发/撤销事件历史推导，而 exchange 真实告警在内存（`cycle_capture.py` 经 `get_price_level_alerts()` 读）。纯只读读 DB 是最优近似；UI 标注"基于 DB 事件还原，可能与运行进程内存态短暂不一致"（告警事件已持久化，漂移窗很小）。
- **工具输出不落库（Plan A 边界）**：`ToolCall` 表只有 `args`（入参），**无 output/result 列**（`tool_call_recorder.py` 返回值给 agent 后从不写库；全库 8 表无 transcript 表）。故 cycle 详情的工具部分只展示 名/入参/status/duration_ms/error_type，**不展示工具返回内容**——"agent 当时看到的工具返回/行情"是已 defer 的"市场上下文回放"视图（需新增持久化，属未来范围升级），不在 Phase 1。
- **复用私有符号**：`_collect_roundtrips_from_trade_actions` 是 `_` 前缀私有函数，跨模块导入是软耦合。Phase 1 可接受；若长期复用，作为不阻塞 follow-up 提升为 `metrics.py` 公开 API。
- **live 持仓/挂单是 DB-as-of-last-write（风险低）**：`get_live_status` 读 `sim_positions`/`sim_orders` 而非 cycle 内存态。已核对 `_persist_state` 对 positions 做 **delete+insert 全量同步**，且在每次状态变更后调用（市价 fill `:258`、条件单 fill `:291`/`:299`、tick `:666`、cancel `:791`、tick 内成交 `:1118`，共 6 处）——故 DB 反映"最后一次操作后"的状态，读它新鲜。UI 标"截至最后写入"caveat 即可，无需改取数源。
- **`get_performance` 须显式传 `current_position`**：`MetricsService.compute(current_position="none")`（`metrics.py:227`）用该形参填充输出"当前持仓"字段。只读台须先从 `sim_positions` 派生当前持仓再传入，否则成交概览里"当前持仓"恒为 `none`。
- **`get_cycle_detail` 取数形态（非行放大 JOIN）**：`cycle_id` 是 String 软关联、不声明 DB FK（`models.py:209`）。`get_cycle_detail` 应**先按 int `id` 取唯一 cycle 行**，再按 `(cycle_id, session_id)` 取其 `tool_calls` 作 **1:N 子列表**（一个 cycle 多次工具调用）——不要把 `agent_cycles ⋈ tool_calls` 当 1:1 JOIN，否则 cycle 字段按工具调用数放大。前置假设：`cycle_id` 在单 `session_id` 内对 `agent_cycles` 唯一。

## 5. 关键数据决定（已核对代码）

### 5.1 净值曲线（双口径，分别标注）

`sim_balances` 是 `session_id` 主键的**单行当前态**，不含历史；`MetricsService` 的 equity 序列是内部局部变量未暴露。因此：

- **净值曲线** = **per-cycle 账户盯市净值**，取自 `agent_cycles.state_snapshot` 的 `$.balance.total_usdt`（含未实现盈亏，见 `simulated.py:162-171` `fetch_balance`，total_usdt 在 :168 = free+used+frozen+unrealized），每 cycle 一个点。回答"净值如何随时间演变"，开仓持有/无平仓时也有曲线。
  - **直接 `json_extract('$.balance.total_usdt')`，不复用 `v_cycle_metrics`**：该视图只投影了 `$.balance.free_usdt`（`views.py:63`），没有 total_usdt。
  - **边界 = balance 为 None 的点要跳过**：`state_snapshot` 列实际**永非 NULL**（`cycle_capture.py` 调用方无条件 `json.dumps`），但 best-effort 取余额失败时 snapshot dict 中 `balance` 保持初始值 `None`（dict 预置 `"balance": None`，except 分支不重设）→ `json_extract` 得 NULL，曲线渲染须跳过该点。
- **回撤等标量指标** = `MetricsService.compute()`（基于 FIFO round-trip 的**已实现、扣费后**净值序列，`metrics.py:285-299` 用 `net_pnls`）。

两者口径不同（盯市-per-cycle vs 已实现-per-roundtrip），曲线视觉回撤可能 ≠ MDD 指标值。前端对两者**显式打标签**："账户净值（盯市，每 cycle）" 与 "最大回撤（已实现净值，净/扣费后）"，避免同名不同义（对齐工具设计原则 7）。

### 5.2 会话状态展示（不做唤醒/liveness 重构）

**v1 决定（复杂度不抵价值）**：状态卡直接显示 `Session.status`（active/paused）+ `last_active_at` 原始时间戳，**不重构"下次唤醒"、不派生精确 liveness**。本机单人观察自己启动的 sim，"哪个在跑 / 下次何时唤醒"价值不高，而其精确化要从只读 DB 反推 scheduler 运行时态（一次性语义 / HH:MM 跨日 / stale / 被告警抢占），复杂度远超 v1 收益。唤醒决策仍可在决策时间线的 cycle 详情里看到 `set_next_wake[_at]` 工具调用。

**诚实性 caveat（为何配 `last_active_at`）**：`status` 不是可靠 liveness——实测写路径只产生两个值：

- `'active'`：创建（`session_manager.py:231`）/ 恢复（`:182`）
- `'paused'`：① 优雅退出（`app.py:1164`）；② 启动残留清理——`select_or_create_session` 入口在 wizard 前调 `_fix_residual_active`（`session_manager.py:47-52`，调用点 `:336`）把所有 `active`→`paused`
- `'stopped'`：**死值**（`models.py` 注释列了但写路径零出现）

崩溃 / kill -9 / 合盖睡死的会话无优雅退出钩子 → **永停 `'active'`**（直到下次 CLI 启动被清理）。故裸 `status='active'` 可能是已死会话。**对策不是重构 liveness，而是同时显示 `last_active_at` 原始戳**——"最后活跃：2 小时前" 让陈旧的 active 自证，用户一眼自判。UI 字段标"会话状态（来自 status 字段）"而非"运行中"，不替 status 做超出其语义的 liveness 断言。

（注：`session_manager.py:284` 的 active/paused 是 CLI 列表显示映射，非状态写入。精确"下次唤醒 / liveness"重构若未来需要，作为 defer 项重新立项——它服务的 UI 价值低、实现复杂，v1 不做。）

## 6. API 契约（v1，全部 GET，只读）

```
GET /api/sessions                      → SessionSummary[]
GET /api/sessions/{id}                 → SessionDetail
GET /api/sessions/{id}/cycles
        ?limit=50&before_id=<int>      → CycleRow[]（向旧翻页，ORDER BY id DESC）
        &after_id=<int>                → live 轮询增量（id > after_id 取新，避免每次重取 limit=50 再客户端去重）
GET /api/cycles/{pk}                   → CycleDetail
GET /api/sessions/{id}/performance     → Performance（指标 + equity[] + trades[]）
GET /api/sessions/{id}/live            → LiveStatus
```

`schemas.py` 用 pydantic 定义上述模型，作为后端↔前端的接口契约。`CycleDetail` 内 `trigger_context` / `state_snapshot` / `injected_events` 作为已解析 JSON 透传。

`frontend/src/types.ts` 镜像后端 schema。**优先用 `openapi-typescript` 从 FastAPI 自带的 OpenAPI 自动生成 TS 类型**（一条 build 脚本），而非手抄——彻底消除手动镜像 drift，契合本项目 drift-guard 文化（如 `cli/app.py` R2-5 drift assert / `REGISTERED_TOOL_NAMES` 漂移测试）。若 v1 暂手写，至少记为 follow-up。

分页用 `ORDER BY id DESC` 单键 keyset（`before_id` 游标）而非 `(created_at, id)` 双键——`agent_cycles.id` 自增且按时序插入，与 `created_at` 单调一致，单键即正确且更诚实。

**cycle 双标识符须显式区分**（对齐工具设计原则 7）：`agent_cycles` 同时有 `id`(int 自增 PK) 与 `cycle_id`(String，如 "6923")。feed 行**显示** `cycle_id`（人读），但 `GET /api/cycles/{pk}` 与 `before_id` 游标用的是 **int `id`**。schema 字段命名须区分这两者（如 `id` vs `cycle_label`），详情跳转用 int `id`、不能用 `cycle_id` 字符串。

## 7. 实时 / 轮询

- 前端 `usePolling` 组合式：在 `SessionDetail` 挂载时，对 `status=='active'` 的会话每 5s 重取 `/live` 与 `/cycles`（取最新，增量插入时间线顶部）；`paused` 会话不轮询。
- 轮询间隔可配（默认 5s）。

## 8. 隔离与测试

- **queries.py**：用 seeded 测试 DB 单元测试每个函数。边界：空数据 / 仅历史会话 / 含 open 持仓 / **`state_snapshot` 非空但 `balance` 为 None**（净值曲线跳点）/ `initial_balance` 取自会话真实值。
- **API 层**：FastAPI `TestClient` 测端点契约（状态码 + schema 形状）。
- **前端**：v1 不强制测试框架，靠 TS 类型 + 手动验收；如需，Vitest 备选。
- **WAL**：SQLite WAL 已由跑 sim 的进程经 `init_db` 启用（`database.py` `PRAGMA journal_mode=WAL`，DB 级持久设置）；webui 只读侧无需再设，并发读不阻塞 sim 写入。

## 9. 依赖与运行

- **后端依赖**：`pyproject.toml` 增可选 extra `[project.optional-dependencies] webui = ["fastapi", "uvicorn[standard]"]`，不污染核心运行依赖。
- **前端依赖**：`frontend/package.json`（vue / vite / typescript / lightweight-charts），`node_modules` 与 `frontend/dist/` 入 `.gitignore`。
- **开发**：`npm run dev`（Vite :5173 代理 `/api` → FastAPI :8000）+ uvicorn 热重载。
- **使用**：`npm run build` → `frontend/dist/`；`python -m src.webui` 启动，浏览器开 `localhost:8000`。

## 10. 核对项状态

spec review（2026-06-12）已把以下原"待核对"项对照源码定论并折进正文：

- ✅ `state_snapshot` 余额 key = `$.balance.total_usdt`；失败时 `balance=None` 边界 → §5.1
- ✅ WAL 已由 `init_db` 启用（`database.py`），webui 只读不设 → §3 / §8
- ✅ 活跃告警过滤 = `v_alert_lifecycle.final_status='active'`（`views.py`）→ §4
- ✅ `_collect_roundtrips_from_trade_actions(engine, session_id, contract_size)` 复用，`contract_size` 取自 `sessions` → §4
- ✅ `MetricsService` 须显式传 `sessions.initial_balance`（默认值不匹配）→ §4
- ✅ `tool_calls` 列已确认：`id/session_id/cycle_id/tool_name/status/duration_ms/error_type/created_at/args`，**无 output 列**；`get_cycle_detail` JOIN 键 = `cycle_id`+`session_id` → §4
- ✅ **spike 实测（2026-06-12, SQLite 3.50.4/macOS）**：`mode=ro` 能读 live WAL 库的未 checkpoint 帧（live + 无 writer 副本 + 缺 `-shm` 均通过）；`immutable=1` 返回陈旧数据须禁用 → §3
- ✅ `Session.status` 实测只写 `'active'`/`'paused'`，`'stopped'` 是死值；状态卡故配 `last_active_at` 原始戳让 stale-active 自证 → §5.2

**仍留实现时核对（窄）**：（本期无——精简掉唤醒/liveness 重构后无遗留项）
