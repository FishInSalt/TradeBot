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
- `src/integrations/exchange/base.py` — `FillEvent` dataclass（含 pnl 字段），`BaseExchange` 定义 `drain_pending_fills()` 默认实现。注意：`on_fill()` 目前仅在 SimulatedExchange 中实现，BaseExchange 中没有声明

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
| LLMRouter 处置 | 保留代码，迁移调用点 | 当前 app.py 使用 `llm_router.resolve("trade_decision")` 获取模型，Phase 1b 将该调用点替换为 ModelManager 获取 model 对象。LLMRouter 类保留，sub-agent 阶段再启用 |

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
- `id`: 用户可读的唯一标识，用于启动时选择和 CLI 参数引用
- `provider`: pydantic-ai provider 类型（`anthropic` / `openai` / `google-gla` / `groq` 等）
- `model`: provider 内的模型标识
- `api_key`: 该 provider 的 API key
- `base_url`: 可选，用于 OpenRouter 等兼容 API（`null` 表示使用 provider 默认地址）

### 启动交互流程

```
启动系统
  ↓
检查 CLI 参数 --model <id>
  ├─ 有 → 从 models.json 查找该 id，跳过交互
  └─ 无 → 进入交互模式
         ↓
       扫描 config/models.json
         ↓
       有已配置模型?
         ├─ 是 → 列出已配置模型，用户选择或输入新模型
         └─ 否 → 提示用户输入模型信息（provider, model, api_key, base_url）
  ↓
测试 API 连通性（发送简短 chat completion 请求，如 "say hi"，验证 API key 有效且模型可访问）
  ├─ 成功 → 保存到 models.json（如果是新模型），继续启动
  └─ 失败 → 提示具体错误（认证失败/模型不存在/网络超时），要求重新输入（CLI 模式下直接报错退出）
```

CLI 参数 `--model <id>` 支持无人值守启动（cron / systemd 等场景）。

### 运行时模型切换

- 启动时用户选择的模型作为当前 session 的模型
- `run_agent_cycle` 调用 `agent.run()` 时传入 `model=` 参数覆盖 agent 默认模型
- 模型管理统一由 `config/models.json` + 启动交互负责，`settings.yaml` 中移除 `models` 配置段
- 迁移改造点：
  - `config.py`：`ModelsConfig`、`ModelRouting` 类保留不删除（sub-agent 阶段复用），但 `Settings` 中的 `models` 字段改为 `Optional` 并默认 `None`
  - `app.py`：移除 `llm_router.resolve("trade_decision")` 调用，改为从 `ModelManager`（启动交互）获取 model 对象，直接传入 `agent.run(model=...)`
  - `src/services/llm_router.py`：代码保留不删除，不再在 `app.py` 中实例化，sub-agent 阶段再启用

### pydantic-ai model 构造

根据 `provider`、`model`、`base_url`、`api_key` 字段构造 pydantic-ai 的 model 对象：

```python
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel

def create_model(config: ModelConfig):
    if config.base_url:
        # OpenRouter 等兼容 API — 使用 OpenAIModel 直接传入 base_url 和 api_key
        return OpenAIModel(
            config.model,
            base_url=config.base_url,
            api_key=config.api_key,
        )
    else:
        # 原生 provider — 通过环境变量传入 api_key，返回 model_id 字符串
        # pydantic-ai 根据前缀自动选择 provider
        os.environ[_env_var_for_provider(config.provider)] = config.api_key
        return f"{config.provider}:{config.model}"
```

provider → 环境变量映射：
- `anthropic` → `ANTHROPIC_API_KEY`
- `openai` → `OPENAI_API_KEY`
- `google-gla` → `GOOGLE_API_KEY`
- `groq` → `GROQ_API_KEY`

### 不做的事

- **不做 provider 自动发现**：不根据 model ID 自动判断需要哪个 SDK 或环境变量。用户配置什么就用什么。
- **不做模型能力检测**：不验证模型是否支持 tool calling。如果不支持，运行时报错。
- **不做 fallback 链**：主模型失败不自动切换备用模型。现有 3 次重试仍保留，但只重试同一模型。
- **不支持同一 provider 多 API key 并存**：`create_model()` 通过环境变量设置 API key，同 provider 后设置的会覆盖前者。当前启动时只选一个模型，不会实际冲突。未来做运行时切换到同 provider 不同 key 时需要改用 provider 对象直接传入。

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

`BaseExchange` 提升以下方法为统一接口（默认空实现，子类按需覆写）：

```python
# src/integrations/exchange/base.py — 新增到 BaseExchange
async def start(self) -> None:
    """启动 WebSocket 等后台任务。默认空实现。"""
    pass

def on_fill(self, callback: Callable[['FillEvent'], Awaitable[None]]) -> None:
    """注册 fill 回调。默认空实现。"""
    pass

def on_alert(self, callback: Callable[[Any], Awaitable[None]]) -> None:
    """注册价格异动回调。默认空实现。"""
    pass
```

注：`start()` 和 `on_fill()` 已在 SimulatedExchange 中有实现，提升到 BaseExchange 后 SimulatedExchange 保持不变。`close()` 已是 abstract method。

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
    """关闭 REST + WebSocket 客户端（try/finally 确保两个都尝试关闭）。"""
    try:
        await self._client.close()
    finally:
        if hasattr(self, '_ws_client') and self._ws_client:
            await self._ws_client.close()
```

### watch_orders 监听循环

```python
async def _watch_orders_loop(self) -> None:
    while self._running:
        try:
            orders = await self._ws_client.watch_orders(self._symbol)
            self._ws_orders_error_count = 0  # 成功后重置
            for order_data in orders:
                if order_data["status"] == "closed":  # 已成交
                    fill_event = self._parse_fill_event(order_data)
                    if self._fill_callback:
                        await self._fill_callback(fill_event)
        except asyncio.CancelledError:
            break
        except Exception:
            self._ws_orders_error_count += 1
            delay = min(5 * (2 ** (self._ws_orders_error_count - 1)), 60)
            logger.error("watch_orders error (retry in %ds)", delay, exc_info=True)
            await asyncio.sleep(delay)
```

### FillEvent 构造

从 `watch_orders` 返回的 order 数据构造 FillEvent：

| FillEvent 字段 | 数据来源 |
|---|---|
| `order_id` | `order_data["id"]` |
| `symbol` | `order_data["symbol"]` |
| `side` | `order_data["side"]` |
| `position_side` | 优先取 `order_data["info"]["posSide"]`（OKX 原始字段）；缺失时根据 side + order_type 推断 |
| `trigger_reason` | order_type 映射：stop → "stop", take_profit → "take_profit", market → "market" |
| `fill_price` | `order_data["average"]` 或 `order_data["price"]` |
| `amount` | `order_data["filled"]` |
| `fee` | `order_data["fee"]["cost"]` |
| `pnl` | 优先取 `order_data["info"]["pnl"]`；缺失则调 REST `fetch_order` 补查（超时 5s，失败则 pnl=None） |
| `timestamp` | `order_data["timestamp"]` |

`position_side` 推断规则（仅当 `info.posSide` 缺失时使用）：

| side | order_type | position_side |
|------|-----------|---------------|
| sell | stop | long（多头止损） |
| buy | stop | short（空头止损） |
| sell | take_profit | long（多头止盈） |
| buy | take_profit | short（空头止盈） |

注：market 类型不走此推断表 — 市价单 fill 不通过 WebSocket 处理（见"不做的事"）。

### app.py 改动

- fill handler 注册逻辑从 `if simulated` 扩展到 OKX 模式，共用同一个 `_create_fill_handler`
- OKX 模式也调用 `exchange.start()` 启动 WebSocket
- OKX 不需要 `drain_pending_fills`（fill 通过 callback 直接推送，不入队）

### 错误处理

- WebSocket 断连：ccxt.pro 内置自动重连，监听循环捕获异常后 sleep 再重试
- 解析失败的订单：跳过并 log warning，不影响后续推送
- pnl 补查：设 5 秒超时上限，失败时 pnl=None（不阻塞后续 fill 处理），后续 agent cycle 可通过 `get_trade_journal` 补充查询
- `start()` 失败：不阻塞系统启动，降级为现有行为（仅在 agent cycle 中通过 REST 查询订单状态）。除 log error 外，在 CLI 显示醒目 warning（如 `"⚠ WebSocket connection failed, running in REST-only mode"`）。设置 `_ws_connected = False` 标志；降级后 fill/alert callback 无需注销（WebSocket 未启动，callback 不会被调用）

### 不做的事

- 不处理 OKX 市价单的 fill（市价单 REST `create_order` 返回时已成交，不需要 WebSocket 通知）
- 不处理部分成交（partial fill）通知：只在订单完全成交（`status == "closed"`）时推送 FillEvent。OKX 合约大额单可能分批成交（`status` 仍为 `"open"` 但 `filled > 0`），当前不处理中间状态
- 不做主动重连逻辑（依赖 ccxt.pro 内置机制）。外层循环异常捕获后使用指数退避重试（5s → 10s → 20s → 60s 封顶），避免 ccxt.pro 内部重连失败时的高频重试

---

## 模块三：价格异动警报

### 目标

市场出现剧烈价格波动时，主动唤醒 agent 做出响应（平仓、调整止损等），而不是等到下一次定时唤醒。

### 检测逻辑

新建 `src/services/price_alert.py`：

```python
from collections import deque

class PriceAlertService:
    def __init__(self, symbol, window_minutes, threshold_pct, cooldown_minutes):
        self._symbol = symbol  # 绑定交易对（当前单 symbol 系统）
        self._window_ms = window_minutes * 60 * 1000
        self._threshold_pct = threshold_pct
        self._cooldown_ms = cooldown_minutes * 60 * 1000
        self._ticks: deque[tuple[float, int]] = deque()  # (price, timestamp)
        self._last_alert_ts: dict[str, int] = {}  # direction → timestamp

    def check(self, price: float, timestamp: int) -> AlertInfo | None:
        """喂入 tick 价格，返回 AlertInfo 或 None。"""
        # 1. 追加当前 tick，淘汰窗口外的旧数据
        self._ticks.append((price, timestamp))
        cutoff = timestamp - self._window_ms
        while self._ticks and self._ticks[0][1] < cutoff:
            self._ticks.popleft()
        # 2. 计算窗口内 high/low
        high = max(p for p, _ in self._ticks)
        low = min(p for p, _ in self._ticks)
        # 3. 计算偏离
        drop_pct = (price - high) / high * 100 if high > 0 else 0
        rise_pct = (price - low) / low * 100 if low > 0 else 0
        # 4. 取绝对值更大的方向，超阈值且不在冷却期 → 返回 AlertInfo

    def update_params(self, threshold_pct, window_minutes, cooldown_minutes):
        """运行时更新参数（由 agent tool 调用）。"""
        ...
```

使用 **sliding window**（deque 存储采样点），而非 tumbling window。每个 tick 淘汰窗口外旧数据，实时计算 high/low。5 分钟窗口、每秒一个 tick ≈ 300 个点，`max()/min()` 微秒级，内存可忽略。

Sliding window 避免了 tumbling window 的窗口重置盲区：tumbling 每 5 分钟重置时丢失历史极值，可能低估真实波动幅度。Sliding window 始终准确反映最近 N 分钟内的 high/low。

`AlertInfo` 数据：
- `symbol`: 交易对
- `current_price`: 当前价格
- `reference_price`: 触发方向的参考价格（跌时为 window_high，涨时为 window_low）
- `change_pct`: 变化百分比（负 = 跌，正 = 涨）
- `window_minutes`: 时间窗口
- `timestamp`: 警报产生时间（ms），与 FillEvent.timestamp 格式一致

### 触发流程

```
Exchange ticker 流（每个 tick）
  ↓
PriceAlertService.check(price, timestamp)
  ↓
|当前价格 vs 窗口 high/low| > 阈值，且不在冷却期?
  ├─ 否 → 忽略
  └─ 是 → alert callback（在 Exchange 锁外执行，与 fill callback 同模式）
        ↓
      scheduler.trigger("alert", context=alert_info)
        ↓
      agent cycle 启动，prompt 追加:
      "PRICE ALERT: BTC/USDT:USDT dropped 3.5% in 5min (61200 → 59058)"

注：Scheduler 为**串行调度** — 当前 cycle 完成后才处理下一个 trigger。如果 agent 正在执行定时 cycle 时收到 fill 或 alert，`scheduler.trigger()` 只设标志位，待当前 cycle 完成后再启动新 cycle。不存在两个 agent cycle 并发操作仓位的情况。
```

### 冷却机制

- 触发一次 alert 后，同方向（涨/跌）在 cooldown_minutes 内不重复触发
- 反方向不受影响（跌触发 alert 后，如果快速反弹涨超阈值，仍然触发）
- 冷却计时器在每次触发时重置
- **不做冷却期内的累计加倍触发**（如跌 3% 后又跌 5%）：agent 在首次 alert 唤醒后会自行处理（平仓/调止损），再次触发不会改变 agent 的决策依据（agent 每次唤醒都会重新获取最新行情）

### Exchange 集成

- **SimulatedExchange**: 在 `_process_tick` 中调用 `PriceAlertService.check()`，有结果则在锁外调用 alert callback（与 fill callback 同模式，避免死锁）
- **OKXExchange**: 在 `start()` 中新增 `_watch_ticker_loop` 后台任务（与 `_watch_orders_loop` 并行），共用同一个 `_ws_client`（ccxt.pro 支持单客户端多 subscription），共用 `_running` 标志控制生命周期

```python
async def _watch_ticker_loop(self) -> None:
    error_count = 0
    while self._running:
        try:
            raw = await self._ws_client.watch_ticker(self._symbol)
            error_count = 0  # 成功后重置
            ticker = self._parse_ticker(raw)
            if self._alert_service:
                alert = self._alert_service.check(ticker.last, ticker.timestamp)
                if alert and self._alert_callback:
                    await self._alert_callback(alert)
        except asyncio.CancelledError:
            break
        except Exception:
            error_count += 1
            delay = min(5 * (2 ** (error_count - 1)), 60)
            logger.error("watch_ticker error (retry in %ds)", delay, exc_info=True)
            await asyncio.sleep(delay)
```

两种 Exchange 注册 alert callback 的接口一致：
```python
def on_alert(self, callback: Callable[[AlertInfo], Awaitable[None]]) -> None
```

BaseExchange 添加默认空实现。

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

参数边界验证：`threshold_pct` 0.5%–50%，`window_minutes` 1–60，`cooldown_minutes` 1–120。超出范围返回错误提示，不执行更新。

agent 可以根据市场状况调整（如高波动时收紧阈值），也可以不调（使用默认值）。

### 启动交互

启动时根据 `alerts.enabled` 决定行为：

- `enabled: false` → 跳过参数询问，不创建 PriceAlertService，不注册 alert callback
- `enabled: true` → 询问用户价格预警参数：

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
- **不做冷却期内累计加倍触发**：agent 首次唤醒后会重新获取行情自行判断，多次 alert 不增加信息量

---

## 统一启动流程

```
1. 加载 settings.yaml 配置
2. 模型选择（交互或 --model 参数）
   → 扫描 models.json → 用户选择/新增 → 测试 API 连通性
3. 价格预警参数（交互或使用默认值）
   → 用户设置 window/threshold/cooldown
4. 初始化 Exchange
   → SimulatedExchange 或 OKXExchange
5. 注册 fill handler + alert handler
6. exchange.start()
   → SimulatedExchange: 恢复状态 + WebSocket ticker 撮合循环
   → OKXExchange: WebSocket watch_orders + watch_ticker 循环
7. 显示初始 metrics
8. 启动 Scheduler
9. 等待 shutdown 信号
```

## 典型端到端流程

以 OKX 实盘 agent 首次开仓为例：

1. **启动** → 用户选择模型 + 设置价格预警参数 → 系统测试 API 连通性 → 启动 WebSocket（watch_orders + watch_ticker）
2. **定时唤醒** → Agent 分析市场 → 决定开多 → `open_position` 提交市价单到 OKX
3. **Fill 推送** → WebSocket 收到成交通知 → FillEvent → 唤醒 agent → Agent 设置 SL/TP
4. **价格异动** → BTC 5 分钟跌 4% → PriceAlertService 触发 → 唤醒 agent → Agent 检查仓位、收紧止损
5. **Agent 调整预警** → Agent 判断当前高波动，调用 `set_price_alert(threshold_pct=1.5, ...)` 收紧阈值
6. **止损触发** → OKX 执行止损 → WebSocket fill 推送 → 唤醒 agent → Agent 复盘 + save_memory
7. **定时唤醒** → Agent 查看 trade journal + memories → 决定是否重新入场

---

## 测试策略

| 模块 | 测试方式 |
|------|---------|
| `PriceAlertService` | 纯逻辑单元测试：阈值触发、冷却机制、V 形反弹场景、参数更新、边界验证 |
| `ModelManager` | 单元测试：models.json 读写、model 字符串构造；API 连通性测试需 mock HTTP 请求 |
| `OKX WebSocket fill` | mock `ccxt.pro` 的 `watch_orders` 返回数据，验证 FillEvent 构造和 callback 调用；pnl 补查失败场景 |
| `OKX WebSocket ticker` | mock `watch_ticker`，验证 PriceAlertService 集成和 alert callback |
| `set_price_alert tool` | 通过 mock deps 验证参数边界、PriceAlertService 参数更新 |
| `启动流程` | 集成测试：验证 CLI 参数 `--model`、交互模式、models.json 不存在等场景 |

---

## 文件变更概览

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `config/models.json` | 新建 | 模型配置（gitignored） |
| `.gitignore` | 修改 | 添加 `config/models.json` |
| `src/services/model_manager.py` | 新建 | 模型配置读写、pydantic-ai model 构造、API 连通性测试、启动交互 |
| `src/services/price_alert.py` | 新建 | 价格异动检测（deque sliding window + 阈值 + 冷却） |
| `src/integrations/exchange/okx.py` | 修改 | 新增 ccxt.pro 客户端、on_fill、on_alert、start()、watch_orders + watch_ticker 循环 |
| `src/integrations/exchange/simulated.py` | 修改 | 集成 PriceAlertService、on_alert（锁外回调） |
| `src/integrations/exchange/base.py` | 修改 | BaseExchange 提升 `start()`、`on_fill()`、`on_alert()` 为统一接口（默认空实现） |
| `src/agent/tools_execution.py` | 修改 | 新增 set_price_alert tool（含参数边界验证） |
| `src/agent/trader.py` | 修改 | 注册 set_price_alert、动态模型传入 `agent.run(model=...)` |
| `src/cli/app.py` | 修改 | 统一启动交互流程、fill/alert handler 注册扩展到 OKX、CLI `--model` 参数 |
| `src/config.py` | 修改 | 新增 alerts 配置项 |
| `src/services/llm_router.py` | 保留 | 代码不删除但本阶段不使用，sub-agent 阶段再启用 |
| `config/settings.yaml` | 修改 | 新增 alerts 配置段、移除 models 配置段 |
