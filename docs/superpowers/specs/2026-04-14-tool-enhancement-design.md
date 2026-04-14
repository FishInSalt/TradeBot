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

- 6 个工具增强
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

**新增参数**：`candle_count: int = 50`（上限 100）

**改进后输出**（四段结构）：

```
=== Ticker ===
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
- ATR: 占价格百分比，<0.1% low, 0.1-0.3% moderate, >0.3% high
- Volume ratio: <0.7x low, 0.7-1.3x normal, >1.3x above normal

**实现改动**：
- `src/services/technical.py`: 新增 ATR、成交量比率、K 线范围计算；重写 `format_for_llm` 输出格式
- `src/agent/tools_perception.py`: `get_market_data` 接受 `candle_count` 参数，输出 K 线表
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
  LONG 0.001 BTC @ 74761.10 | 3x leverage
  PnL: -19.09 USDT (-0.19% of capital)
  Liquidation: 50200.00 (32.8% away)
  Duration: 25 min
```

**新增信息**：
- 盈亏占初始本金百分比
- 距清算价百分比
- 持仓时长（从 position 的 created_at 到当前时间）

**实现改动**：
- `src/agent/tools_perception.py`: 从 `deps.initial_balance` 获取初始本金，计算百分比和时长
- `src/agent/trader.py`: `TradingDeps` 新增 `initial_balance: float` 字段
- `src/cli/app.py`: `build_services` 中从 `WizardResult.initial_balance` 传入 deps

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
  Return: -0.19% (-19.00 USDT)
  Free: 8981.00 USDT
  Used: 1000.00 USDT
```

**实现改动**：
- `src/agent/tools_perception.py`: 从 session 获取 initial_balance，计算收益率

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

**实现改动**：
- `src/agent/tools_perception.py`: 调用共享的交易统计函数（见"共享统计逻辑"），输出在流水前

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
- `src/agent/tools_execution.py`: 在返回前获取当前价格，计算百分比

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
- `src/agent/tools_perception.py`: 获取当前价格，对有 price 的订单计算距离百分比

---

## 二、新增工具

### 7. cancel_order — 取消指定订单

**用途**：取消不再需要的限价单、止损单、止盈单。

**参数**：`order_id: str, reasoning: str`

**输出格式**：

```
Order cancelled: limit buy 0.001 @ 72000.00 | ID: abc123
```

**错误情况**：
- 订单不存在: `"Order not found: {order_id}"`
- 市价单: `"Cannot cancel market orders"`

**实现改动**：
- `src/agent/tools_execution.py`: 新增 `cancel_order` 函数。先通过 `exchange.fetch_open_orders` 查到订单详情（类型、方向、数量、价格），然后调用 `exchange.cancel_order` 取消，最后返回包含订单信息的确认消息。记录 TradeAction。
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

**实现改动**：
- `src/integrations/exchange/base.py`: 新增 `get_alert_info()` 方法，返回告警参数和活跃告警列表
- `src/integrations/exchange/simulated.py`: 实现该方法
- `src/agent/tools_perception.py`: 新增 `get_active_alerts` 函数
- `src/agent/trader.py`: 注册工具

### 9. get_performance — 交易表现统计

**用途**：Agent 复盘时查看整体交易表现。

**参数**：无

**输出格式**：

```
=== Trading Performance ===
Initial Balance: 10000.00 USDT
Current Balance: 10245.00 USDT
Return: +2.45% (+245.00 USDT)

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

**实现改动**：
- `src/agent/tools_perception.py`: 新增 `get_performance` 函数，调用共享的交易统计函数 + 当前余额计算
- `src/agent/trader.py`: 注册工具

**共享统计逻辑**：`get_trade_journal` 的汇总头部和 `get_performance` 的详细统计都需要从 TradeAction 表计算胜率/盈亏比等指标。提取一个共享函数 `_compute_trade_stats(db_engine, session_id)` 放在 `tools_perception.py` 中（或复用 `src/services/metrics.py`），两个工具复用，避免重复实现。

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
# candle_count 默认 50，上限 100
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
| `src/services/technical.py` | 重写：新增 ATR、成交量比率、K 线范围；重写 format_for_llm |
| `src/agent/tools_perception.py` | 重写：增强 6 个现有函数 + 新增 3 个函数 |
| `src/agent/tools_execution.py` | 修改：set_stop_loss/set_take_profit 返回值增加距离百分比；新增 cancel_order |
| `src/agent/trader.py` | 修改：更新工具签名和 docstring；注册 3 个新工具 |
| `src/integrations/exchange/base.py` | 修改：新增 get_alert_info 默认方法 |
| `src/integrations/exchange/simulated.py` | 修改：实现 get_alert_info |
| `src/integrations/market_data.py` | 可能修改：limit 参数透传 |
| `tests/` | 新增/修改测试 |

## 五、不在本轮范围

- Persona prompt 改动（P0 多时间框架引导，单独做）
- 新闻/消息面工具（需外部 API 集成）
- 资金费率查询（当前本金规模下可忽略）
- 硬性风控代码约束（P3，联调观察后决定）
