# WebUI 目标系统架构与分期路线图

> North-star 文档。描述加入 WebUI 后 TradeBot 的目标系统架构，以及"只读观察台 → 多会话并发 → WebUI 接管会话管理"的三期演进。Phase 1 的详细设计见 `2026-06-12-webui-dashboard-phase1-design.md`；Phase 2/3 到时各自 brainstorm 独立 spec。

## 1. 背景与痛点

- **可观察性痛点**：agent 的推理 / 决策过程目前只通过终端输出或 session log 呈现，开发者与用户难以观察决策细节，也难看清整体交易表现。
- **多会话痛点**：当前实际上只能同时运行一个会话——`main.py` 是单进程跑单 session 的 scheduler loop；起第二个进程会共享同一 SQLite 文件并可能踩到非会话隔离的全局/模块级资源，导致"后开的会话改动先前会话状态"的观感。

## 2. 现状关键事实（设计基线，已核对代码）

| 事实 | 出处 | 对架构的意义 |
|------|------|--------------|
| 全部 sim 状态表已按 `session_id` 隔离 | `sim_positions`(session_id 索引) / `sim_orders`(session_id FK) / `sim_balances`(session_id 主键) | 数据层隔离已正确；多会话隔离的缺口在**进程层**不在数据层 |
| `SimulatedExchange` 按 session 实例化、内存态实例级 | `simulated.py` 构造函数收 `session_id`+`symbol` | 非全局单例；进程隔离可消除内存态串扰 |
| `main.py` = `asyncio.run(run(model_id=…, debug=…))` 单进程单 session | `main.py:13` / `src/cli/app.py:run()` | 多会话并发 = 多进程，需要一个编排器 |
| sim 仍用真实 ccxt 取 OHLCV/ticker + WS 取 mark | `simulated.py` `self._ccxt.fetch_ohlcv` / `watch_mark_price` | N 并发会话 = N× 外部 API 调用 + N WS 连接 → 限流/连接是 Phase 2 难点 |
| 会话状态字段 + 活跃时间戳 | `sessions.status`(实测只写 active/paused；stopped 死值) + `last_active_at`(每 cycle 更新) | "哪个会话在跑"= `status=='active'` AND `last_active_at` recency（崩溃会话永停 active，status 非权威，见 Phase 1 §5.3） |
| 性能指标已 FIFO 净值化 | `MetricsService.compute()`（PR #57） | 观察台直接复用，不重算 |

## 3. 目标三层架构

核心洞察：**SQLite 本身就是数据总线**。会话 worker 写 DB、编排器只读 DB 喂前端——"看数据"这条路不需要任何进程间通信；编排器只为"管会话"做 OS 进程生命周期管理。

```
┌─────────────────────────────────────────────────────────────┐
│  前端 SPA (Vue 3 + TS + Vite)        浏览器                    │
└───────────────▲─────────────────────────────────────────────┘
                │ HTTP / (未来 WS)
┌───────────────┴─────────────────────────────────────────────┐
│  编排器后端 (FastAPI, 单进程, 常驻)         控制面 + 数据面      │
│   • queries.py        只读查询 → 喂前端           (数据面)      │
│   • SessionSupervisor spawn/探活/停止 会话子进程  (控制面/P2)   │
│   • 创建会话 = 写 sessions 行 + 起 worker         (P3)         │
└───┬───────────────────────────┬──────────────────▲───────────┘
    │ spawn / SIGINT             │ spawn            │ 只读
    ▼                           ▼                   │
┌─────────────┐         ┌─────────────┐             │
│ 会话 worker  │  ...    │ 会话 worker  │ headless, 每会话一进程   │
│ scheduler +  │         │ scheduler +  │            │
│ agent loop + │         │ agent loop + │            │
│ SimExchange  │         │ SimExchange  │            │
└──────┬──────┘         └──────┬──────┘             │
       │ 写 (session-keyed)    │ 写                  │
       ▼                       ▼                    │
┌─────────────────────────────────────────────────┴───────────┐
│  SQLite (WAL)  = 唯一事实源 / 数据总线（已按 session_id 隔离）   │
└──────────────────────────────────────────────────────────────┘
```

- **数据面（看）**：worker 写 DB，编排器只读 DB 喂前端。完全解耦。
- **控制面（管）**：编排器做进程 spawn / 探活 / SIGINT 优雅停（Ctrl+C 等 cycle 完成是 by-design，见 memory `graceful_shutdown_design`）；会话状态读 `sessions.status` + `last_active_at` + 子进程 PID 探活。

## 4. 三期路线图

各期独立 spec→plan→impl。Phase 1 的产出（`queries.py` 只读层 + JSON API + Vue SPA）正是目标架构的数据面，**Phase 2/3 在其上叠加控制面与 worker，不推翻 Phase 1**。

| 期 | 内容 | 价值 / 风险 |
|----|------|-------------|
| **Phase 1** | 只读观察台：会话列表 + 决策时间线 + 表现概览 + 实时状态卡。跑在当前单进程模型上，多会话以"DB 中多个历史 session"体现 | 立刻解决可观察性痛点；纯只读、零风险 |
| **Phase 2** | 多会话并发：编排器 `SessionSupervisor` + headless worker 入口 + 进程管理 + 状态隔离 bug 正式定位（systematic-debugging）+ 限流/SQLite 写锁加固 | 解决"只能跑一个会话"；架构改动大，需独立 brainstorm |
| **Phase 3** | WebUI 接管会话创建/控制（start/stop/pause）：复用 `select_or_create_session` 搬进编排器 + 前端表单 | 可延后项；建在 P1+P2 之上 |

## 5. Phase 2 预备：需要为之设计的难点

1. **共享外部资源 / 限流**：N 并发会话对 OKX/CoinDesk/FRED 的调用倍增。小 N（2–3）可接受独立取数 + 各源限流；要扩需共享 market-data 缓存层（会牺牲部分进程隔离，权衡留 Phase 2）。
2. **SQLite 写并发**：WAL 支持 1 写 + N 读；多 worker 并发写靠锁串行化，短事务下可接受，高频可能 `database is locked`，需 WAL + 短事务 + 写重试。再扩才评估 Postgres（YAGNI）。
3. **headless 会话入口**：当前 `run()` 交互式（wizard + Rich）。worker 需非交互入口——给定 session 配置直接跑 scheduler loop。`run()` 已分离"会话选择"与运行循环，加一条 headless 路径即可。
4. **崩溃 / 生命周期**：编排器探测 worker 死亡、暴露状态、支持重启。
5. **状态隔离 bug 根因**：DB 既已 session-keyed，进程隔离很可能直接消除串扰；Phase 2 仍需审计是否存在漏带 `session_id` 的查询或模块级全局态。
   - **已证实具体实例 `_fix_residual_active`**：`session_manager.py:336`（每次 CLI 启动、wizard 之前运行）**无条件**把所有 `status='active'`→`'paused'`（清理非正常退出残留）。单进程模型下无害（启动时无其他会话在跑）；但**多会话并发下是 footgun**——经现有 CLI 流程启动第 2 个会话会把正在运行的第 1 个会话翻成 `paused`。这是本条"非会话隔离的全局写"的一个确证实例，Phase 2 编排器须绕开/改造此启动清理（如仅清理"非本次将启动"的会话，或改用 PID/recency 判残留而非一刀切）。

## 6. 设计原则对齐

- **只读观察台而非控制台**（Phase 1）：读 DB、不向 agent 发指令，契合"fact-provider 不是 guard"。
- **DB 即总线**：避免为观察引入 IPC，降低耦合。
- **零返工分期**：Phase 1 即目标架构的数据面雏形。
- **YAGNI**：不提前做鉴权 / 多用户 / Postgres / WebSocket，按期触发。
