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
