# Agent 工具库增强设计

> 目标：补全和优化 Agent 工具库，提供好用且全面的工具集，提升 Agent 交易决策质量。

## 审查员上下文

### 系统架构

TradeBot 是一个 LLM 驱动的加密货币交易系统。核心架构：

- **Agent**：基于 pydantic-ai 的 ReAct Agent，使用 DeepSeek-V3 模型，通过 tool calling 与系统交互
- **Exchange**：抽象层支持模拟交易所（SimulatedExchange）和真实交易所（OKXExchange）
- **Scheduler**：事件驱动调度器，定时唤醒 Agent 或在成交/告警时立即唤醒
- **交易标的**：BTC/USDT 永续合约

### Agent 工作流

每次唤醒后，Agent 进入一个 ReAct 循环（非单次 API 调用）：

```
唤醒(定时/成交/告警) → Agent 自主决定调用哪些工具 → 收集信息 → 分析判断 → 执行/观望 → 休眠
```

Agent 通过工具获取所有外部信息（市场数据、持仓、余额等），也通过工具执行所有操作（开仓、设止损等）。工具是 Agent 的"感官和手脚"——工具质量直接决定决策质量。

### 为什么要做这个改动

联调验证（Phase 1 冒烟测试通过）发现：Agent 的工作流结构正确，但感知工具的信息密度严重不足。对比真实交易员的信息需求：

| 信息维度 | 真实交易员 | 当前 Agent |
|---------|-----------|-----------|
| 价格走势 | 看 K 线图（多时间框架） | 只有 9 个指标数值，无 K 线数据 |
| 波动率 | ATR、布林带宽度 | 有 BB 数值但无 ATR |
| 成交量 | 量能分析（放量/缩量） | 无 |
| 支撑阻力 | 近期高低点、关键价位 | 无 |
| 指标解读 | 看数值含义（超买/超卖） | 裸数值，需要 LLM 自行解读 |
| 仓位风险 | 占本金比例、距清算距离 | 只有 PnL 绝对值 |
| 整体表现 | 胜率、盈亏比、回撤 | 无统计工具 |

### 现有工具列表（16 个）

**感知工具**（信息输入）— 本次增强重点：
| 工具 | 用途 | 参数 |
|------|------|------|
| `get_market_data(symbol, timeframe)` | 市场数据 + 技术指标 | 2 个必填 |
| `get_position(symbol)` | 当前持仓 | 1 个必填 |
| `get_account_balance()` | 账户余额 | 无 |
| `get_open_orders()` | 挂单列表 | 无 |
| `get_trade_journal()` | 交易流水 | 无 |
| `get_memories()` | 长期记忆 | 无 |

**执行工具**（行动输出）— 小幅优化：
| 工具 | 用途 | 参数 |
|------|------|------|
| `open_position(side, position_pct, leverage, reasoning)` | 市价开仓 | 4 个 |
| `close_position(reasoning)` | 市价平仓 | 1 个 |
| `place_limit_order(side, price, position_pct, leverage, reasoning)` | 限价开仓 | 5 个 |
| `set_stop_loss(price, reasoning)` | 设止损 | 2 个 |
| `set_take_profit(price, reasoning)` | 设止盈 | 2 个 |
| `adjust_leverage(leverage, reasoning)` | 调杠杆 | 2 个 |
| `set_price_alert(threshold_pct, window_minutes, reasoning)` | 调波动告警参数 | 3 个 |
| `add_price_level_alert(price, direction, reasoning)` | 设价位告警 | 3 个 |
| `set_next_wake(minutes, reasoning)` | 设下次唤醒时间 | 2 个 |
| `save_memory(category, content, importance)` | 存记忆 | 3 个 |

## 背景

联调验证发现 Agent 的工作流结构正确（ReAct 循环），但感知工具的信息密度不足，导致 Agent 决策所需的市场信息严重缺失。本次改动统一审视并增强现有工具、补全缺失工具。

## 设计标准

工具设计遵循 6 条标准（从 LLM 作为工具使用者的特性出发）：

1. **命名和描述清晰** — LLM 靠 docstring 决定调哪个工具，描述必须一看就懂
2. **参数尽量少** — LLM 填参可靠性与参数数量成反比，可选参数给默认值
3. **返回信息预消化** — LLM 数学能力弱，在工具内完成计算，返回结论性信息
4. **信息密度适中** — 足够决策但不淹没，LLM 处理长序列数据效率会下降
5. **输出带上下文参照** — 裸数值（ATR: 85）无意义，加定性标注（0.11% of price — low volatility）
6. **单一职责** — 一个工具做一件事，让 Agent 自己组合调用

## 改动范围

- 8 个工具函数增强（7 节，SL/TP 合并为一节，含 set_price_alert 禁用状态处理）
- 3 个新增工具
- 2 个参数默认值优化
- 不涉及工作流变更，不涉及 persona prompt 改动

---

## 一、工具增强

### 1. get_market_data — 扩展市场数据

**当前问题**：只返回 9 个裸数值指标，缺 K 线数据、波动率、成交量趋势、支撑阻力。

**当前输出**：

```
Symbol: BTC/USDT:USDT
Price: 74880.00 | Bid: 74870.00 | Ask: 74890.00
24h High: 75200.00 | Low: 73800.00 | Volume: 12345.60

Technical Indicators (5m):
Current Price: 74880.00

RSI(14): 52.88
MA(20): 74750.00
MA(50): 74500.00
MACD: 12.50
MACD Signal: 8.30
MACD Histogram: 4.20
Bollinger Upper: 75100.00
Bollinger Middle: 74750.00
Bollinger Lower: 74400.00
```

**新增参数**：`candle_count: int = 50`（下限 10，上限 80，实现中 `candle_count = max(10, min(candle_count, 80))`）

**获取与展示解耦**：`candle_count` 控制 K 线表展示的数量，不影响指标计算。实际获取的 K 线数量为 `max(candle_count + 50, 100)`，确保 MA(50)、RSI(14)、MACD(12,26,9) 等指标有足够的热身数据。例如 Agent 传 `candle_count=20`，实际获取 100 根 K 线用于计算指标，K 线表只展示最后 20 根。

**API 限制说明**：OKX `fetch_ohlcv` 单次返回上限取决于时间框架（通常 100-300 根）。当请求量超出 API 限制时 CCXT 会静默截断。`candle_count` 上限设为 80（而非 100），确保 `max(80+50, 100) = 130` 即使被截断到 100 根仍有 20 根热身数据。如果返回数据不足 `candle_count + 50`，展示量为 `返回数量 - 50`（确保指标有基本热身），最少展示 10 根。

**改进后输出**（四段结构）：

```
=== Ticker (BTC/USDT:USDT) ===
Price: 74880.00 | Bid: 74870.00 | Ask: 74890.00
24h High: 75200.00 | Low: 73800.00 | Volume: 12345.60

=== Technical Indicators (5m) ===
RSI(14): 52.88 (neutral)
MA(20): 74750.00 (price above — bullish)
MA(50): 74500.00 (price above — bullish)
MACD: 12.50 | Signal: 8.30 | Histogram: 4.20 (bullish)
BB: 75100 / 74750 / 74400 (price in upper half)

=== Market Context ===
ATR(14): 85.20 (0.11% of price — low volatility)
Volume: 125.3 (1.35x avg — above normal)
50-candle Range: 73800 — 75200

=== Recent Candles (5m, last 50) ===
Time      Open     High     Low      Close    Vol
16:35     74650    74720    74630    74700    112.5
16:40     74700    74780    74680    74760    98.3
...
```

**关键变化**：
- 每个指标加定性标注（neutral/bullish/bearish、above/below average 等）
- 新增 ATR（波动率）、成交量比率（放量/缩量）、K 线范围（支撑阻力参考）
- 新增最近 N 根 K 线的完整 OHLCV 数据表

**定性标注规则**：
- RSI: <30 oversold, 30-45 bearish, 45-55 neutral, 55-70 bullish, >70 overbought
- MA: price above → bullish, price below → bearish
- MACD: histogram > 0 → bullish, < 0 → bearish
- BB: price 位于上下轨间的位置描述
- ATR: 占价格百分比。5m timeframe 使用定性标注（<0.1% low, 0.1-0.3% moderate, >0.3% high）。非 5m timeframe 只标数值和百分比，不加定性标签（如 `ATR(14): 850.20 (1.14% of price, 1H candles)`），因为阈值不随 timeframe 缩放，高 timeframe 的定性标注会系统性误导。让 LLM 结合 timeframe 自行判断波动率水平。
- Volume ratio: 使用倒数第 2 根 K 线（最近一根已完成的）的 volume / SMA(volume, 20)。最后一根 K 线可能正在形成中，volume 偏低会导致误判。<0.7x low, 0.7-1.3x normal, >1.3x above normal

**职责划分**：
- `technical.py` 的 `compute_indicators`：扩展为使用完整 OHLCV DataFrame，新增返回字段。`format_for_llm` 只负责 Technical Indicators 的指标行格式化（定性标注），不含段标题，不含 Market Context。Volume ratio 计算需加除零保护（SMA=0 时返回 None）。
- `tools_perception.py` 的 `get_market_data`：负责所有段落的拼接和段标题。具体职责：
  - **Ticker 段**：含 symbol（如 `=== Ticker (BTC/USDT:USDT) ===`）
  - **Technical Indicators 段**：段标题含 timeframe（如 `=== Technical Indicators (5m) ===`），内容来自 `format_for_llm` 返回的指标行
  - **Market Context 段**：ATR 和 volume_ratio 从 indicators dict 读取并格式化，candle range 从展示的 K 线切片计算
  - **K 线表段**：标题标注实际展示数量（如 `=== Recent Candles (5m, last 50) ===`），截断时 Agent 可从标题看到实际数量K 线时间列使用 UTC，格式按 timeframe 自适应：1m/5m/15m → `HH:MM`，1H/4H → `MM-DD HH:MM`，1D/1W → `YYYY-MM-DD`。

**实现改动**：
- `src/services/technical.py`: `compute_indicators` 扩展为使用完整 OHLCV DataFrame（当前只用 close 列，需改为同时使用 high/low/close/volume）。新增返回字段：`atr_14`、`volume_ratio`（分子取 volume.iloc[-2]，分母取 SMA(volume, 20).iloc[-2]，保持分子分母取值位置一致，均避开最后一根未完成 K 线）。当数据不足时（SMA 窗口不够、ATR 计算行数不足、或 SMA 值为 0），对应字段返回 None。`format_for_llm` 新增 `timeframe: str` 参数，重写输出格式，加入定性标注（ATR 定性标签仅对 5m 生效，非 5m 只标数值），**只输出 Technical Indicators 的指标行**。数据不足导致的 None 值输出为 "N/A"，与现有 `_last()` 模式一致——这是预期行为，非 bug。**同时修复现有指标列索引 bug**：当前用位置索引访问 pandas_ta 返回列，存在两处反转：(1) BB: `bb_cols[0]` 赋给 `bb_upper`，实际是 BBL（lower）。pandas_ta 返回 [BBL, BBM, BBU, BBB, BBP]。(2) MACD: `macd_cols[1]` 赋给 `macd_signal`，实际是 MACDh（histogram）；`macd_cols[2]` 赋给 `macd_histogram`，实际是 MACDs（signal）。pandas_ta 返回 [MACD, MACDh, MACDs]。全部改为列名匹配（`filter(like='BBU')`、`filter(like='MACDh')` 等）。
- `src/agent/tools_perception.py`: `get_market_data` 接受 `candle_count` 参数，负责 Ticker 段和 K 线表段的格式化。工具 docstring 注明用法建议（如 "candle_count=20 for quick check or secondary timeframes, 50 for detailed analysis. Default 50. Values above 50 may be capped by exchange API limits. Total output ~1000-1200 tokens (K-line table ~750-800 + indicators + context)."），降低 Agent 选择负担，引导多 timeframe 场景使用较小的 candle_count。
- `src/integrations/market_data.py`: `get_ohlcv_dataframe` 的 `limit` 参数由上层传入

### 2. get_position — 增加风险上下文

**当前问题**：缺少仓位风险信息。PnL 是绝对值，Agent 不知道占本金多少。

**当前输出**：

```
Current Positions:
  LONG 0.001 contracts @ 74761.10 | Leverage: 3x | PnL: -19.09 USDT| Liq: 50200.00
```

**改进后输出**：

```
Current Position:
  LONG 0.001 contracts @ 74761.10 | 3x leverage
  PnL: -19.09 USDT (-0.19% of initial capital)
  Liquidation: 50200.00 (32.8% away)
  Duration: 25 min
```

**新增信息**：
- 盈亏占初始本金百分比
- 距清算价百分比
- 持仓时长（从 position 的 created_at 到当前时间）

**持仓时长实现方案**：公开 `Position` dataclass（base.py）没有 `created_at` 字段。新增可选字段 `created_at: datetime | None = None`。SimulatedExchange 的 `fetch_positions` 从内部 `_Position.created_at` 填充；OKXExchange 留 `None`（CCXT 的 position 对象没有可靠的创建时间）。工具端对 `None` 显示 "N/A"。

**实现改动**：
- `src/integrations/exchange/base.py`: `Position` dataclass 新增 `created_at: datetime | None = None`
- `src/integrations/exchange/simulated.py`: `fetch_positions` 填充 `created_at`
- `src/integrations/exchange/okx.py`: `fetch_positions` 保持 `created_at=None`（无需改动，使用默认值）
- `src/agent/tools_perception.py`: 从 `deps.initial_balance` 获取初始本金，调用 `deps.market_data.get_ticker()` 获取当前价格（用于计算清算距离百分比 `abs(current_price - liquidation_price) / current_price * 100`），计算百分比和时长。注意：OKX 模式新增 1 次 ticker REST 请求。
- `src/agent/trader.py`: `TradingDeps` 新增 `initial_balance: float = 10000.0` 和 `metrics: MetricsService | None = None`（均需默认值，因为 Python dataclass 要求无默认值字段在有默认值字段之前，而 TradingDeps 从 `db_engine` 开始已有默认值）
- `src/cli/app.py`: 将 `MetricsService` 创建移入 `build_services` 内部（当前在 app.py:364，在 build_services 返回之后），从 `result.initial_balance` 获取值，同时传入 deps。app.py:367 的现有调用 `metrics_service.compute(engine, session_id, ...)` 也需同步改为 `deps.metrics.compute(current_position=...)`

### 3. get_account_balance — 增加收益率

**当前问题**：只有 total/free/used，Agent 不知道自己整体赚了还是亏了。

**当前输出**：

```
Account Balance:
  Total: 9981.00 USDT
  Free: 8981.00 USDT
  Used: 1000.00 USDT
```

**改进后输出**：

```
Account Balance:
  Total: 9981.00 USDT (initial: 10000.00)
  Return: -0.19% (-19.00 USDT) (incl. unrealized)
  Free: 8981.00 USDT
  Used: 1000.00 USDT
```

**实现改动**：
- `src/agent/tools_perception.py`: Return = `(balance.total_usdt - deps.initial_balance) / deps.initial_balance * 100`。SimulatedExchange 和 OKX 的 `total_usdt` 均已含 unrealized PnL，直接用即可，无需额外查持仓。

### 4. get_trade_journal — 增加汇总统计

**当前问题**：只有原始流水，缺少汇总。

**当前输出**：

```
=== Trade Journal ===
[04-14 16:35] open_position (long) @ 74761.10 [closed]
  Reasoning: trend following entry on RSI pullback
[04-14 17:10] close_position (long) @ 74900.00, pnl=12.50 [closed]
  Reasoning: take profit at resistance
...
```

**改进后输出**（在原始流水前加统计头部）：

```
=== Performance Summary ===
Total Trades: 12 | Win: 7 (58.3%) | Loss: 5
Avg Win: +45.20 USDT | Avg Loss: -22.10 USDT
Profit Factor: 2.87
Recent: 3W 1L (last 4 trades)

=== Trade Journal ===
[04-14 16:35] open_position (long) @ 74761.10 ...
...
```

**Docstring 定位**：`"""Get trade journal — decision timeline with quick stats summary. Use for reviewing recent decisions and their outcomes."""`（强调决策时间线 + 快速概要，区别于 get_performance 的详细复盘）

**实现改动**：
- `src/agent/tools_perception.py`: 调用 `deps.metrics.compute()` 获取统计，输出在流水前

**设计取舍**：get_trade_journal 已查 TradeAction 获取流水，`MetricsService.compute()` 会再次查 TradeAction（过滤 pnl IS NOT NULL）。两次查询数据部分重叠，但过滤条件不同（流水含全部 action，统计只含 fills with pnl），合并会增加耦合。SQLite 本地查询开销极小，接受双查询以保持 MetricsService 作为统计逻辑的单一来源。

### 5. set_stop_loss / set_take_profit — 返回距离百分比

**当前问题**：返回只有绝对价格，Agent 不知道距当前价多远。

**当前输出**：

```
Stop loss set at 72500.00 | Order: abc123
```

**改进后输出**：

```
Stop loss set at 72500.00 (-3.02% from current 74761.10) | Order: abc123
Take profit set at 79200.00 (+5.94% from current 74761.10) | Order: def456
```

**实现改动**：
- `src/agent/tools_execution.py`: 在返回前调用 `deps.market_data.get_ticker()` 获取当前价格，计算百分比。注意：这对 OKX 模式新增一次 REST 请求，但 SL/TP 设置是低频操作，可接受。

### 6. get_open_orders — SL/TP 显示距当前价百分比

**当前问题**：SL/TP 价格是裸值。

**当前输出**：

```
Pending Orders:
  [STOP] sell 0.001 @ 72500.00 | ID: abc123
  [TAKE_PROFIT] sell 0.001 @ 79200.00 | ID: def456
```

**改进后输出**：

```
Pending Orders:
  [STOP] sell 0.001 @ 72500.00 (-3.02% from current) | ID: abc123
  [TAKE_PROFIT] sell 0.001 @ 79200.00 (+5.94% from current) | ID: def456
  [LIMIT] buy 0.001 @ 72000.00 (-3.69% from current) | ID: ghi789
  [PENDING] buy 0.001 market price | ID: jkl012
```

**实现改动**：
- `src/agent/tools_perception.py`: 调用 `deps.market_data.get_ticker()` 获取当前价格，对有 price 的订单计算距离百分比。注意：OKX 模式新增 1 次 ticker REST 请求（原本该工具无外部请求）。

### 7b. set_price_alert — 禁用状态明确提示

**当前问题**：当 wizard 配置 `alert_enabled=False`（不创建 PriceAlertService）时，Agent 调用 `set_price_alert` 后 `update_alert_params` 静默跳过，工具仍返回成功消息——误导 Agent 认为设置生效了。新增的 `get_active_alerts` 工具让 OFF 状态对 Agent 可见，可能导致 Agent 反复尝试"修复"。

**改进行为**：工具先调用 `deps.exchange.get_alert_params()`，返回 `None` 时直接返回 `"Alerts are disabled for this session. Enable alerts in wizard to use this feature."`，不调用 `update_alert_params`。

**实现改动**：
- `src/agent/tools_execution.py`: `set_price_alert` 函数开头加 `get_alert_params()` 检查

---

## 二、新增工具

### 7. cancel_order — 取消指定订单

**用途**：取消不再需要的限价单、止损单、止盈单。

**参数**：`order_id: str, reasoning: str`

**输出格式**：

```
Order cancelled: limit buy 0.001 @ 72000.00 | ID: abc123
```

**异常处理**：工具端先调用 `exchange.fetch_open_orders(deps.symbol)` 查找目标订单（使用 `deps.symbol`，当前单交易对系统）：
- 找不到 → 直接返回 `"Order not found or already filled: {order_id}"`（不调用 exchange.cancel_order，避免 Sim/OKX 异常差异）
- 找到且为 market 类型 → 返回 `"Cannot cancel market orders"`
- 找到且为 limit/stop/take_profit → 调用 `exchange.cancel_order` 取消，返回订单详情确认

**实现改动**：
- `src/agent/tools_execution.py`: 新增 `cancel_order` 函数。先 fetch_open_orders 找订单，再 cancel，记录 TradeAction（`action="cancel_order"`）。
- `src/agent/trader.py`: 注册工具

### 8. get_active_alerts — 查看当前告警配置

**用途**：查看百分比波动告警参数和所有活跃的价位级别告警。

**参数**：无

**输出格式**：

```
=== Price Alert Settings ===
Volatility alert: 5.0% in 60min window

=== Active Price Level Alerts (2/20) ===
  #1 above 75000.00 — "key resistance breakout"
  #2 below 74000.00 — "support breakdown"
```

无告警时：

```
=== Price Alert Settings ===
Volatility alert: OFF

=== Active Price Level Alerts (0/20) ===
  No active alerts.
```

**数据来源**：
- **价位级别告警**：存在 `BaseExchange._price_level_alerts`（base.py:63）。新增公开方法 `get_price_level_alerts() -> list[dict]` 返回列表拷贝，避免跨层访问私有属性。
- **百分比波动告警参数**：存在 `PriceAlertService` 实例中。**解决方案**：在 `PriceAlertService` 上新增 `get_params() -> tuple[float, int]` 方法，返回 `(threshold_pct, window_minutes)`。在 `BaseExchange` 层统一实现告警相关方法（`set_alert_service`、`update_alert_params`、`get_alert_params`），两个子类的实现完全相同，无需各自覆写：

```python
# base.py — __init__ 新增
self._alert_service: Any | None = None

# base.py — 替换原有空实现
def set_alert_service(self, service: Any) -> None:
    self._alert_service = service

def update_alert_params(self, threshold_pct: float, window_minutes: int) -> None:
    if self._alert_service:
        self._alert_service.update_params(threshold_pct, window_minutes)

def get_alert_params(self) -> tuple[float, int] | None:
    if self._alert_service is not None:
        return self._alert_service.get_params()
    return None
```

工具直接从 exchange 读取，无需 deps 中间存储，完全消除参数不一致风险。Sim 和 OKX 两种模式行为一致。

**实现改动**：
- `src/services/price_alert.py`: `PriceAlertService` 新增 `get_params()` 方法
- `src/integrations/exchange/base.py`: `__init__` 新增 `self._alert_service = None`；改写 `set_alert_service` 和 `update_alert_params`（从空实现改为真实实现）；新增 `get_alert_params()` 和 `get_price_level_alerts()` 方法
- `src/integrations/exchange/simulated.py`: 删除 `set_alert_service` 和 `update_alert_params` 覆写（继承 BaseExchange 即可）
- `src/integrations/exchange/okx.py`: 删除 `set_alert_service` 和 `update_alert_params` 覆写（继承 BaseExchange 即可）
- `src/agent/tools_perception.py`: 新增 `get_active_alerts` 函数，从 exchange 读取两类告警
- `src/agent/trader.py`: 注册工具

### 9. get_performance — 交易表现统计

**用途**：Agent 复盘时查看整体交易表现。

**参数**：无

**输出格式**：

```
=== Trading Performance ===
Initial Balance: 10000.00 USDT
Current Balance: 10245.00 USDT
Total Return: +2.45% (+245.00 USDT) (incl. unrealized)
Realized PnL: +210.00 USDT (gross, before fees)
Total Fees: -15.00 USDT

Total Trades: 12 | Win: 7 (58.3%) | Loss: 5
Avg Win: +45.20 USDT | Avg Loss: -22.10 USDT
Profit Factor: 2.87
Max Drawdown: -1.8%
Best Trade: +120.50 USDT | Worst Trade: -55.30 USDT
```

无交易记录时：

```
=== Trading Performance ===
Initial Balance: 10000.00 USDT
Current Balance: 10000.00 USDT
Return: +0.00% (+0.00 USDT)

No completed trades yet.
```

**Docstring 定位**：`"""Get detailed trading performance statistics. Use for reviewing overall results and evaluating strategy effectiveness."""`（强调详细复盘统计，区别于 get_trade_journal 的决策时间线）

**实现改动**：
- `src/agent/tools_perception.py`: 新增 `get_performance` 函数，调用 `deps.metrics.compute()` + 当前余额计算
- `src/agent/trader.py`: 注册工具

**共享统计逻辑**：`get_trade_journal` 的汇总头部和 `get_performance` 的详细统计都需要计算胜率/盈亏比等指标。**扩展现有 `src/services/metrics.py` 的 `MetricsService`**，而非新建函数：
- `PerformanceMetrics` dataclass 新增字段：`avg_win: float`、`avg_loss: float`、`best_trade: float`、`worst_trade: float`、`recent_summary: str`（近 N 笔交易的统计汇总，如 "3W 1L (last 4 trades)"。N = min(5, total_trades)，在 MetricsService 中格式化为 str。交易不足时展示全部。按时间取最近 N 笔的胜负统计，不跟踪连胜/连败序列。）
- `MetricsService.__init__` 新增 `engine` 和 `session_id` 参数注入（`initial_balance` 已有），`compute()` 简化为 `deps.metrics.compute(current_position="none")`，其中 `current_position` 保留为可选 kwarg（app.py 初始显示仍需传入）
- `MetricsService.compute()` 补全新增字段的计算逻辑。**PnL 口径说明**：FillEvent.pnl 是毛利（不含 fee）。一个完整交易的余额变化 = gross_pnl - open_fee - close_fee，但 MetricsService 只看 close fill 的 pnl（开仓 pnl=None）。如果做 net_pnl = pnl - close_fee，仍漏掉 open_fee，需要匹配开平仓记录，复杂度过高。

**解决方案**：保持 MetricsService 统计 gross PnL（不改现有逻辑），新增 `total_fees: float` 字段累加所有 fill 的 fee（开仓 + 平仓），在输出中单独展示。Total Return（从余额计算）是准确的净值指标，Realized PnL 标注为 gross。差异对 Agent 透明。

新增改动：
- `src/storage/models.py`: TradeAction 新增 `fee: Mapped[float | None]` 列
- `src/cli/app.py`: `_record_action_from_fill` 写入 `fee=event.fee`
- `src/cli/session_manager.py`: 新增 `_migrate_trade_actions_table`，用 `ALTER TABLE trade_actions ADD COLUMN fee REAL` 处理已有数据库
- `PerformanceMetrics` 新增 `total_fees: float` 字段
- `MetricsService.compute()` 累加 fee（含 pnl=None 的开仓 fill）
- `get_trade_journal` 和 `get_performance` 都调用 `deps.metrics.compute()` 获取统计数据

---

## 三、参数默认值优化

### get_market_data

```python
# 现在
get_market_data(symbol: str, timeframe: str)

# 改为
get_market_data(symbol: str | None = None, timeframe: str | None = None, candle_count: int = 50)
# symbol 默认 deps.symbol
# timeframe 默认 deps.timeframe
# candle_count 默认 50，上限 80
```

### get_position

```python
# 现在
get_position(symbol: str)

# 改为
get_position(symbol: str | None = None)
# 默认 deps.symbol
```

### 不变的参数

`open_position` 和 `place_limit_order` 的 `leverage` 保持必填——执行工具参数显式传递比隐式默认更安全。

---

## 四、文件改动汇总

| 文件 | 改动类型 |
|------|---------|
| `src/services/technical.py` | 重写：新增 ATR、成交量比率；修复 BB/MACD 列索引 bug；重写 format_for_llm |
| `src/agent/tools_perception.py` | 重写：增强 6 个现有函数 + 新增 3 个函数 |
| `src/agent/tools_execution.py` | 修改：set_stop_loss/set_take_profit 返回值增加距离百分比；新增 cancel_order；set_price_alert 对 alert_service=None 返回明确提示（"Alerts are disabled for this session"）而非静默成功 |
| `src/agent/trader.py` | 修改：更新工具签名和 docstring；注册 3 个新工具；TradingDeps 新增 initial_balance + metrics 字段 |
| `src/cli/app.py` | 修改：build_services 传入 initial_balance 和 MetricsService 实例到 deps；`_record_action_from_fill` 写入 fee；app.py:367 调用方同步改为 deps.metrics.compute() |
| `src/storage/models.py` | 修改：TradeAction 新增 fee 列 |
| `src/cli/session_manager.py` | 修改：新增 `_migrate_trade_actions_table`，ALTER TABLE ADD COLUMN fee |
| `src/integrations/exchange/base.py` | 修改：Position 新增 created_at；改写 set_alert_service（存储引用）；新增 get_alert_params / get_price_level_alerts |
| `src/integrations/exchange/simulated.py` | 修改：fetch_positions 填充 created_at；删除 set_alert_service + update_alert_params 覆写 + __init__ 中冗余的 `self._alert_service = None` 赋值 |
| `src/integrations/exchange/okx.py` | 修改：删除 set_alert_service + update_alert_params 覆写 + __init__ 中冗余的 `self._alert_service = None` 赋值；fetch_positions 无需改动 |
| `src/services/price_alert.py` | 修改：PriceAlertService 新增 get_params 方法 |
| `src/services/metrics.py` | 扩展：PerformanceMetrics 新增 avg_win/avg_loss/best_trade/worst_trade/recent_summary/total_fees；__init__ 改为注入 engine+session_id+initial_balance |
| `src/integrations/market_data.py` | 修改：limit 参数透传 |
| `tests/` | 新增/修改测试 |

## 五、不在本轮范围

- Persona prompt 改动（P0 多时间框架引导，单独做）
- 新闻/消息面工具（需外部 API 集成）
- 资金费率查询（当前本金规模下可忽略）
- 硬性风控代码约束（P3，联调观察后决定）
- BaseExchange 回调整合：on_fill / on_alert 在两个子类中实现完全相同（`self._fill_callback = callback` / `self._alert_callback = callback`），与本次 set_alert_service 整合属同一类问题，可后续一并上移到 BaseExchange 并在 `__init__` 中初始化
- Ticker 缓存：本次增强后，典型 Agent 周期的 REST 调用从 ~5 次增至 ~8-9 次（get_position +1 ticker、get_open_orders +1 ticker、set_stop_loss/set_take_profit 各 +1 ticker）。OKX 模式下同一循环内多次获取几乎相同的 ticker。可在 MarketDataService 加简单 TTL 缓存（如 5s），但超出本次范围
- OKX initial_balance 精度：OKX 模式下 initial_balance 是用户手动输入的近似值（非 API 查询），get_account_balance 和 get_position 的百分比计算基于此值。如需精确，后续可在首次启动时调用 fetch_balance() 获取真实值
- get_trade_journal N+1 查询：tools_perception.py:98-104 逐个 order_id 调用 fetch_order()，OKX 模式下 20 条流水可能产生 20 次 REST 请求。可后续优化为批量查询
