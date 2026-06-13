# TradeBot WebUI 前端（Phase 1b）

只读观察台前端 SPA。消费 `src/webui/` 的 6 个只读端点，不向 agent 发指令。UI 用 Naive UI 构建。

## 开发

```bash
cd frontend
npm install
npm run dev          # :5173，/api 代理到 http://127.0.0.1:8000
```

后端另起：`python -m src.webui`（默认 :8000，读 `data/tradebot.db`）。

## 类型生成

类型从后端 OpenAPI 生成，后端 schema 为唯一真相源：

```bash
# 1. 从仓库根 dump openapi（无需真实 DB）
python -c "import json; from src.webui.app import create_app; print(json.dumps(create_app().openapi(), ensure_ascii=False))" > frontend/openapi.json
# 2. 生成 types.ts（勿手改）
cd frontend && npm run gen:types
```

## 构建与部署

```bash
npm run build        # → frontend/dist
```

`src/webui/app.py` 在 `/` 同源挂载 `frontend/dist`（`StaticFiles(html=True)`）。
路由用 hash mode（`/#/sessions/:id`），刷新深链必命中 `index.html`，后端零改。

## 测试

```bash
npm run test         # Vitest（逻辑层单测 + 组件冒烟）
```

## 已知限制

- **会话 `paused→active` 翻转不自动恢复轮询**：轮询门控读 `live.status`（仅由轮询自身刷新），会话从 active 变 paused 后轮询停止、`live.status` 不再更新，故之后即便会话恢复 active，前端也不会自动重启轮询——需重新点选该会话刷新。观察工具场景可接受（本机单人自知哪个 sim 在跑）；真要自动探测翻转，需让轻量 `/live` 始终轮询、仅把 cycles/performance 拉取门控在 active（后续增量）。
- 工具调用 `result` 已持久化（后端 `tool_calls.result` 列 + 执行层捕获，原始文本 / 30000 char cap）。空态文案「结果未捕获」仅在 result 为空时出现——error 路径、工具返回 None、或迁移前的历史会话行。
- 生产 bundle 为单包（Naive UI 较重，~788kB/gzip ~234kB），未做 code-split；本机同源工具可接受。
