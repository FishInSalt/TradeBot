# Phase 1b: 补完单 Agent 事件驱动闭环

**Goal:** 补完单 agent 事件驱动闭环，使系统能够模拟现实世界中单交易员的完整交易流程。

**Scope:** 三个独立模块 — 多模型支持、OKX WebSocket fill 推送、价格异动警报。

**Tech Stack:** Python 3.12+, pydantic-ai, ccxt / ccxt.pro, asyncio

**Design spec:** `docs/superpowers/specs/2026-04-10-agent-layer-design.md`（Phase 1a Agent 层设计）

---

## 背景

### 当前系统能力（Phase 1a 已完成）

系统实现了基于事件驱动的 AI 交易 agent：
- **Agent 决策循环**：Scheduler 定时唤醒 → Agent（pydantic-ai）调用感知 tools 获取市场/持仓/挂单信息 → LLM 分析决策 → 调用执行 tools 下单
- **SimulatedExchange**：本地撮合引擎，通过 OKX WebSocket `watch_ticker` 获取实时价格驱动撮合，支持市价单/条件单/清算
- **FillEvent 异步流程**：订单成交后构造 FillEvent → 回调通知 → 唤醒 agent 设置止损止盈或复盘
- **TradeAction 事件模型**：append-only 记录 agent 决策（带 reasoning）和交易所成交（带 pnl），支持交易日志查询和绩效计算

关键代码路径：
- `src/cli/app.py` — `run_agent_cycle()` 执行 agent 决策循环，`on_tick()` 处理 Scheduler 触发并在 finally 中 drain 市价单 fill
- `src/integrations/exchange/simulated.py` — `_process_tick()` 处理每个 ticker，检查清算和条件单触发；`drain_pending_fills()` 返回并清空市价单 fill 队列
- `src/integrations/exchange/base.py` — `FillEvent` dataclass（含 pnl 字段），`BaseExchange` 定义 `on_fill()`、`drain_pending_fills()` 接口

### 当前缺失

1. **模型锁定**：只能用 Anthropic Claude，无法切换其他大模型测试决策质量
2. **OKX 实盘 fill 缺失**：OKXExchange 只有 REST 接口，条件单被 OKX 触发后 agent 最多等 15 分钟才知道（下次定时唤醒）
3. **无价格异动响应**：市场剧烈波动时 agent 无法被主动唤醒，只能按固定间隔定时分析

## 模块间依赖

三个模块**技术上独立**，可以按任意顺序实现。建议顺序：
1. 多模型支持（改动最小，立即可用）
2. OKX WebSocket fill 推送（补完实盘闭环）
3. 价格异动警报（两种 Exchange 都需要集成，放最后统一做）

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| API key 存储方式 | 明文 JSON 文件 | 业界标准（Claude Code、Cursor、Continue.dev 等均如此），加密需要 master key 管理，复杂度不匹配当前阶段 |
| OKX 客户端架构 | REST + WebSocket 双客户端共存 | 职责分离：REST 负责主动请求（查询/下单），WebSocket 负责被动监听（fill/ticker），互不影响 |
| 价格警报实现位置 | Exchange 内部 + 独立 PriceAlertService | 检测逻辑独立可测（PriceAlertService），触发源在 Exchange 内部（复用已有 ticker 流） |
| 价格警报暴露为 agent tool | `set_price_alert` tool | 模拟真实交易员行为 — 交易员根据市场状况自主调整预警条件；配置文件提供默认值兜底 |
| 模型配置迁出 settings.yaml | 统一到 `config/models.json` + 启动交互 | 启动时用户选择模型的流程取代了 YAML 静态配置；routing（strong/weak tier 分发）留待 sub-agent 阶段 |

---

## 模块一：多模型支持

### 目标

支持通过配置切换任意主流大模型（Anthropic/OpenAI/Gemini/DeepSeek/Qwen 等），方便测试不同模型的交易决策质量。

### 模型配置管理

模型配置存储在 `config/models.json`（gitignored），格式：

```json
[
  {
    "id": "claude-opus",
    "provider": "anthropic",
    "model": "claude-opus-4-6",
    "api_key": "sk-ant-...",
    "base_url": null
  },
  {
    "id": "deepseek-chat",
    "provider": "openai",
    "model": "deepseek/deepseek-chat",
    "api_key": "sk-or-...",
    "base_url": "https://openrouter.ai/api/v1"
  }
]
```

字段说明：
- `id`: 用户可读的唯一标识，用于启动时选择
- `provider`: pydantic-ai provider 类型（`anthropic` / `openai` / `google-gla` / `groq` 等）
- `model`: provider 内的模型标识
- `api_key`: 该 provider 的 API key
- `base_url`: 可选，用于 OpenRouter 等兼容 API（`null` 表示使用 provider 默认地址）

### 启动交互流程

```
启动系统
  ↓
扫描 config/models.json
  ↓
有已配置模型?
  ├─ 是 → 列出已配置模型，用户选择或输入新模型
  └─ 否 → 提示用户输入模型信息（provider, model, api_key, base_url）
  ↓
测试 API 连通性（发送简单请求）
  ├─ 成功 → 保存到 models.json（如果是新模型），继续启动
  └─ 失败 → 提示错误，要求重新输入
```

### 运行时模型切换

- 启动时用户选择的模型作为当前 session 的模型
- `run_agent_cycle` 调用 `agent.run()` 时传入 `model=` 参数覆盖 agent 默认模型
- 模型管理统一由 `config/models.json` + 启动交互负责，`settings.yaml` 中不再配置模型（原 `models` 配置段移除，routing 留待 sub-agent 阶段）

### pydantic-ai model 字符串构造

根据 `provider` 和 `model` 字段拼接 pydantic-ai 格式的 model ID：
- `provider=anthropic, model=claude-opus-4-6` → `"anthropic:claude-opus-4-6"`
- `provider=openai, model=deepseek/deepseek-chat` → `"openai:deepseek/deepseek-chat"`
- `provider=google-gla, model=gemini-2.5-pro` → `"google-gla:gemini-2.5-pro"`

对于 `base_url` 非空的条目（如 OpenRouter），需要在 `agent.run()` 前设置对应环境变量或通过 pydantic-ai 的 provider 配置传入。

### 不做的事

- **不做 provider 自动发现**：不根据 model ID 自动判断需要哪个 SDK 或环境变量。用户配置什么就用什么。
- **不做模型能力检测**：不验证模型是否支持 tool calling。如果不支持，运行时报错。
- **不做 fallback 链**：主模型失败不自动切换备用模型。现有 3 次重试仍保留，但只重试同一模型。

---

## 模块二：OKX WebSocket Fill 推送

### 目标

实盘模式下，条件单（止损/止盈）被 OKX 触发成交后，通过 WebSocket 实时通知 agent，使 agent 能及时响应（复盘、调整策略）。

### 架构

`OKXExchange` 新增一个 `ccxt.pro` 客户端，专用于 WebSocket 监听。现有 `ccxt.async_support` REST 客户端保持不变。

```
OKXExchange
  ├── _client (ccxt.async_support.okx)  ← 现有，REST 主动请求
  └── _ws_client (ccxt.pro.okx)         ← 新增，WebSocket 被动监听
```

### 接口变更

`OKXExchange` 新增以下方法（与 SimulatedExchange 接口一致）：

```python
def on_fill(self, callback: Callable[[FillEvent], Awaitable[None]]) -> None:
    """注册 fill 回调。"""
    self._fill_callback = callback

async def start(self) -> None:
    """启动 WebSocket 监听循环。"""
    # 创建 ccxt.pro 客户端（复用相同 API credentials）
    # 启动 _watch_orders_loop 后台任务

async def close(self) -> None:
    """关闭 REST + WebSocket 客户端。"""
    # 现有 REST close + 新增 WebSocket close
```

### watch_orders 监听循环

```python
async def _watch_orders_loop(self) -> None:
    while self._running:
        try:
            orders = await self._ws_client.watch_orders(self._symbol)
            for order_data in orders:
                if order_data["status"] == "closed":  # 已成交
                    fill_event = self._parse_fill_event(order_data)
                    if self._fill_callback:
                        await self._fill_callback(fill_event)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.error("watch_orders error", exc_info=True)
            await asyncio.sleep(5)  # 断连后等待重连
```

### FillEvent 构造

从 `watch_orders` 返回的 order 数据构造 FillEvent：

| FillEvent 字段 | 数据来源 |
|---|---|
| `order_id` | `order_data["id"]` |
| `symbol` | `order_data["symbol"]` |
| `side` | `order_data["side"]` |
| `position_side` | 根据 order_type + side 推断（sell stop on long → "long"） |
| `trigger_reason` | order_type 映射：stop → "stop", take_profit → "take_profit", market → "market" |
| `fill_price` | `order_data["average"]` 或 `order_data["price"]` |
| `amount` | `order_data["filled"]` |
| `fee` | `order_data["fee"]["cost"]` |
| `pnl` | 优先取 `order_data["info"]["pnl"]`；缺失则调 REST `fetch_order` 补查 |
| `timestamp` | `order_data["timestamp"]` |

### app.py 改动

- fill handler 注册逻辑从 `if simulated` 扩展到 OKX 模式，共用同一个 `_create_fill_handler`
- OKX 模式也调用 `exchange.start()` 启动 WebSocket
- OKX 不需要 `drain_pending_fills`（fill 通过 callback 直接推送，不入队）

### 错误处理

- WebSocket 断连：ccxt.pro 内置自动重连，监听循环捕获异常后 sleep 再重试
- 解析失败的订单：跳过并 log warning，不影响后续推送
- `start()` 失败：不阻塞系统启动，降级为定时轮询模式（现有行为），log error 提示

### 不做的事

- 不处理 OKX 市价单的 fill（市价单 REST `create_order` 返回时已成交，不需要 WebSocket 通知）
- 不做主动重连逻辑（依赖 ccxt.pro 内置机制）

---

## 模块三：价格异动警报

### 目标

市场出现剧烈价格波动时，主动唤醒 agent 做出响应（平仓、调整止损等），而不是等到下一次定时唤醒。

### 检测逻辑

新建 `src/services/price_alert.py`：

```python
class PriceAlertService:
    def __init__(self, window_minutes, threshold_pct, cooldown_minutes):
        ...

    def check(self, price: float, timestamp: int) -> AlertInfo | None:
        """喂入 tick 价格，返回 AlertInfo 或 None。"""
        # 维护滑动时间窗口内的价格记录
        # 计算当前价格与窗口起点的变化百分比
        # 超过阈值且不在冷却期 → 返回 AlertInfo
        # 否则返回 None

    def update_params(self, window_minutes, threshold_pct, cooldown_minutes):
        """运行时更新参数（由 agent tool 调用）。"""
        ...
```

`AlertInfo` 数据：
- `symbol`: 交易对
- `current_price`: 当前价格
- `reference_price`: 窗口起点价格
- `change_pct`: 变化百分比（正 = 涨，负 = 跌）
- `window_minutes`: 时间窗口

### 触发流程

```
Exchange ticker 流（每个 tick）
  ↓
PriceAlertService.check(price, timestamp)
  ↓
超过阈值且不在冷却期?
  ├─ 否 → 忽略
  └─ 是 → alert callback
        ↓
      scheduler.trigger("alert", context=alert_info)
        ↓
      agent cycle 启动，prompt 追加:
      "PRICE ALERT: BTC/USDT:USDT dropped 3.5% in 5min (61200 → 59058)"
```

### 冷却机制

- 触发一次 alert 后，同方向（涨/跌）在 cooldown_minutes 内不重复触发
- 反方向不受影响（跌触发 alert 后，如果快速反弹涨超阈值，仍然触发）
- 冷却计时器在每次触发时重置

### Exchange 集成

- **SimulatedExchange**: 在 `_process_tick` 中调用 `PriceAlertService.check()`，有结果则调用 alert callback
- **OKXExchange**: 在 `start()` 中新增 `watch_ticker` 循环（与 `watch_orders` 并行），每个 tick 调用 `PriceAlertService.check()`

两种 Exchange 注册 alert callback 的接口一致：
```python
def on_alert(self, callback: Callable[[AlertInfo], Awaitable[None]]) -> None
```

### Agent Tool

新增执行类 tool `set_price_alert`，让 agent 可以运行时调整警报参数：

```python
async def set_price_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    cooldown_minutes: int,
    reasoning: str,
) -> str:
    """Adjust price alert parameters."""
```

agent 可以根据市场状况调整（如高波动时收紧阈值），也可以不调（使用默认值）。

### 启动交互

启动时询问用户价格预警参数：

```
Price alert settings:
  Window (minutes) [5]:
  Threshold (%) [3.0]:
  Cooldown (minutes) [15]:
```

不填则使用 `settings.yaml` 中的默认值：

```yaml
alerts:
  enabled: true
  window_minutes: 5
  threshold_pct: 3.0
  cooldown_minutes: 15
```

### 不做的事

- **不做多级阈值**（如 3%/5%/10% 分级）：agent 自己判断严重程度
- **不做自定义指标触发**（如 RSI 跌破 30）：agent 在定时 cycle 中已分析指标
- **不做外部通知推送**（Slack/Telegram）：当前系统是 agent 自主交易，不通知人类

---

## 典型端到端流程

以 OKX 实盘 agent 首次开仓为例：

1. **启动** → 用户选择模型 + 设置价格预警参数 → 系统测试 API 连通性 → 启动 WebSocket（watch_orders + watch_ticker）
2. **定时唤醒** → Agent 分析市场 → 决定开多 → `open_position` 提交市价单到 OKX
3. **Fill 推送** → WebSocket 收到成交通知 → FillEvent → 唤醒 agent → Agent 设置 SL/TP
4. **价格异动** → BTC 5 分钟跌 4% → PriceAlertService 触发 → 唤醒 agent → Agent 检查仓位、收紧止损
5. **止损触发** → OKX 执行止损 → WebSocket fill 推送 → 唤醒 agent → Agent 复盘 + save_memory
6. **定时唤醒** → Agent 查看 trade journal + memories → 决定是否重新入场

---

## 文件变更概览

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `config/models.json` | 新建 | 模型配置（gitignored） |
| `src/services/model_manager.py` | 新建 | 模型配置读写、API 连通性测试、启动交互 |
| `src/services/price_alert.py` | 新建 | 价格异动检测（滑动窗口 + 阈值 + 冷却） |
| `src/integrations/exchange/okx.py` | 修改 | 新增 ccxt.pro 客户端、on_fill、on_alert、start()、watch 循环 |
| `src/integrations/exchange/simulated.py` | 修改 | 集成 PriceAlertService、on_alert |
| `src/integrations/exchange/base.py` | 修改 | BaseExchange 添加 on_fill、on_alert 默认空实现 |
| `src/agent/tools_execution.py` | 修改 | 新增 set_price_alert tool |
| `src/agent/trader.py` | 修改 | 注册 set_price_alert、动态模型传入 |
| `src/cli/app.py` | 修改 | 启动交互流程、OKX fill handler 注册、alert handler 注册 |
| `src/config.py` | 修改 | alerts 配置项、移除 ModelsConfig（模型配置迁移到 models.json） |
| `src/services/llm_router.py` | 修改/移除 | 单 agent 阶段不再需要 tier routing，逻辑迁入 model_manager |
| `config/settings.yaml` | 修改 | 新增 alerts 配置段、移除 models 配置段 |
