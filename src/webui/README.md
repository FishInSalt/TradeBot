# TradeBot WebUI — Phase 1a 只读后端

把 agent 决策过程 + 交易表现从 SQLite **只读**暴露为 JSON API。
不向 agent 发指令，不写库，不执行 migration。

---

## 开发 / 启动

### 安装依赖

```bash
uv pip install -e ".[webui]"
```

### 启动服务

```bash
python -m src.webui
# 或
.venv/bin/python -m src.webui
```

默认监听 `http://127.0.0.1:8000`。

### API 文档

| 地址 | 说明 |
|------|------|
| `http://127.0.0.1:8000/api/docs` | Swagger UI（交互式文档）|
| `http://127.0.0.1:8000/api/openapi.json` | OpenAPI schema |

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TRADEBOT_DB` | `data/tradebot.db` | SQLite 库路径 |
| `TRADEBOT_WEBUI_PORT` | `8000` | 监听端口 |

---

## 端点清单

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/api/sessions` | 会话列表（含 `cycle_count` + `total_return_pct`）|
| `GET` | `/api/sessions/{sid}` | 单会话元信息 |
| `GET` | `/api/sessions/{sid}/cycles` | 决策时间线 feed（id DESC keyset 分页，`limit` ∈ [1,200] 越界 422 / `before_id` 翻旧 / `after_id` 增量取新）|
| `GET` | `/api/cycles/{pk}` | 单 cycle 完整细节（含 `tool_calls` 子列表）|
| `GET` | `/api/sessions/{sid}/performance` | 表现概览（指标 + 盯市净值曲线 + 成交）|
| `GET` | `/api/sessions/{sid}/live` | 实时状态卡（status + last_active_at + 持仓 / 挂单 / 活跃告警）|

**缺失语义**：单资源端点（`/sessions/{sid}`、`/cycles/{pk}`、`/sessions/{sid}/performance`、`/sessions/{sid}/live`）在资源不存在时返回 `404`。集合端点 `/sessions/{sid}/cycles` 对未知或无 cycle 的 session 返回 `200` + 空数组 `[]`（集合语义，非 404）。

---

## 只读约束

- 连接使用 `make_readonly_engine`，SQLite URI `mode=ro` + `PRAGMA query_only=ON`。
- **不调 `init_db`、不跑 migration、绝不写库**。
- 可安全读正在被 sim 进程写入的 live WAL 库（SQLite WAL 读写分离）。

---

## 表现概览口径说明（重要）

`/api/sessions/{sid}/performance` 同时返回**两组不同口径**的数字，前端**不可逐点对账**：

- `equity_curve`：逐 cycle **盯市**净值（`state_snapshot.balance.total_usdt` = free+used+frozen+**未实现 PnL**，含浮盈浮亏、已扣 fee）。
- `total_return_pct`：**gross 已实现** roundtrip PnL 之和 / 初始余额（不含浮动、不含 fee）。
- `max_drawdown_pct`：**net 已实现** equity 模拟的 MDD（不含浮动）。

即：曲线可能明显回撤而 `max_drawdown_pct` 数字很小，曲线终点涨幅 ≠ `total_return_pct`——这是双口径 by-intent（spec §5.1），前端同台展示须分别打标签、勿引导用户对齐两者。

---

## 实时状态卡说明

`/api/sessions/{sid}/live` v1 只暴露：

- `Session.status`（枚举原值）
- `last_active_at`（UTC 原始时间戳）
- 当前持仓、挂单数量、活跃告警列表

设计依据 spec §5.2：重构"下次唤醒"或派生精确 liveness 的复杂度不抵价值；
陈旧的 `active` 状态由 `last_active_at` 自证，由消费方判断新鲜度。

---

## 时间戳约定

所有出站 `datetime` 字段经 `schemas.UtcDatetime` 归一化为带 `Z` 后缀的 UTC 字符串
（例：`"2026-06-12T14:23:01Z"`），前端统一按 UTC 解析，无需时区转换。

---

## Phase 1b 待接

- 前端 Vue SPA 另起独立计划，不在 Phase 1a 范围内。
- TypeScript 类型由 `/api/openapi.json` 经 `openapi-typescript` 生成。
- 构建产物落 `frontend/dist`；`app.py` 会在目录存在时自动挂载静态文件，不存在则跳过。
