# SimulatedExchange Design Spec

## Overview

实现一个本地模拟交易所（SimulatedExchange），作为 `BaseExchange` 的第二个实现。它接收 OKX 实时行情数据，在本地完成订单撮合，行为对齐真实交易所。目的是零风险零成本地验证 Agent 的交易决策能力。

## Design Principles

1. **对齐真实交易所行为** — SimulatedExchange 的行为边界和真实交易所一致。遇到设计决策时，以真实交易所的行为为准。
2. **上层无感知** — Agent tools 通过 `BaseExchange` 接口操作，不区分真实/模拟环境。
3. **自治** — SimulatedExchange 自己管理行情接收、订单撮合、内部状态和持久化，不依赖上层的数据。

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         TradeBot System                             │
│                                                                     │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │                    Scheduler                                │     │
│  │   ┌──────────────┐        ┌───────────────────┐           │     │
│  │   │ 定时触发      │        │ 事件触发           │           │     │
│  │   │ (每 15 分钟)  │        │ (FillEvent 回调)  │           │     │
│  │   └──────┬───────┘        └────────┬──────────┘           │     │
│  │          └───────────┬─────────────┘                      │     │
│  │                      ▼                                     │     │
│  │            run_agent_cycle(trigger_type)                   │     │
│  └──────────────────────┬─────────────────────────────────────┘     │
│                         ▼                                           │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                   Trader Agent (Pydantic AI)                  │   │
│  │                                                               │   │
│  │  Perception Tools          Execution Tools        Memory      │   │
│  │  ┌─────────────────┐     ┌──────────────────┐   ┌─────────┐ │   │
│  │  │ get_market_data  │     │ open_position     │   │ save_   │ │   │
│  │  │ get_position     │     │ close_position    │   │ memory  │ │   │
│  │  │ get_balance      │     │ set_stop_loss     │   └────┬────┘ │   │
│  │  │ get_trade_history│     │ set_take_profit   │        │      │   │
│  │  └────────┬─────────┘     │ adjust_leverage   │        │      │   │
│  │           │               └────────┬──────────┘        │      │   │
│  └───────────┼────────────────────────┼───────────────────┼──────┘   │
│              │                        │                   │          │
│              ▼                        ▼                   ▼          │
│  ┌──────────────────────────────────────────┐   ┌──────────────┐   │
│  │          BaseExchange (接口)              │   │  Agent 存储   │   │
│  │                                           │   │              │   │
│  │  fetch_ticker()    create_order()         │   │ TradeRecord  │   │
│  │  fetch_ohlcv()     fetch_balance()        │   │ DecisionLog  │   │
│  │  fetch_positions() set_leverage()         │   │ MemoryEntry  │   │
│  │  amount_to_precision()  close()           │   └──────────────┘   │
│  └──────────┬───────────────┬────────────────┘                      │
│             │               │                                       │
│     ┌───────┴───┐   ┌──────┴────────────────────────────────────┐  │
│     │ OKXExchange│   │         SimulatedExchange                 │  │
│     │ (真实交易) │   │                                           │  │
│     │            │   │  ┌─────────────┐    ┌──────────────────┐ │  │
│     │ ccxt REST  │   │  │ OKX 公开     │    │   撮合引擎       │ │  │
│     │ + 认证 API │   │  │ WebSocket    │───▶│                  │ │  │
│     │            │   │  │ (实时行情)   │    │ 每个 tick:       │ │  │
│     └────────────┘   │  └─────────────┘    │  遍历挂单        │ │  │
│                      │                      │  检查触发条件    │ │  │
│  exchange.name:      │  ┌──────────────┐   │  成交 → FillEvent│ │  │
│  "okx" → 左          │  │  内部状态     │   └───────┬──────────┘ │  │
│  "simulated" → 右    │  │              │           │             │  │
│                      │  │ • 余额       │◄──────────┘             │  │
│                      │  │ • 持仓       │   状态更新              │  │
│                      │  │ • 挂单       │                         │  │
│                      │  └──────┬───────┘                         │  │
│                      │         │ 持久化                          │  │
│                      │         ▼                                 │  │
│                      │  ┌──────────────┐                         │  │
│                      │  │ 交易所存储    │                         │  │
│                      │  │ sim_balances  │                         │  │
│                      │  │ sim_positions │                         │  │
│                      │  │ sim_orders    │                         │  │
│                      │  └──────────────┘                         │  │
│                      └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## SimulatedExchange — Interface Implementation

SimulatedExchange 实现 `BaseExchange` 的全部方法：

| 方法 | 行为 |
|---|---|
| `fetch_ticker(symbol)` | 返回 WebSocket 缓存的最新 ticker（含 bid/ask） |
| `fetch_ohlcv(symbol, timeframe, limit)` | 通过 OKX REST 公开 API 获取 K 线（无需认证） |
| `create_order(symbol, side, order_type, amount, price)` | 见下方「订单处理」 |
| `fetch_balance()` | 从内部状态返回 Balance(total, free, used) |
| `fetch_positions(symbol)` | 从内部状态返回持仓列表，用最新 ticker 计算 unrealized_pnl |
| `set_leverage(symbol, leverage)` | 记录到内部状态 |
| `amount_to_precision(symbol, amount)` | 固定精度规则（BTC: 3 位小数） |
| `close()` | 停止撮合循环，断开 WebSocket |

额外的公开方法：

| 方法 | 说明 |
|---|---|
| `on_fill(callback)` | 注册 FillEvent 回调 |

## Order Processing

### Market Order

`create_order(symbol, side="buy", order_type="market", amount=0.001)` 时同步处理：

1. 校验余额是否足够 → 不够则抛异常（对齐真实交易所的拒单行为）
2. 确定成交价：买入用 `ticker.ask`，卖出用 `ticker.bid`
3. 计算手续费：`fill_price * amount * fee_rate`
4. 更新内部状态：扣保证金 + 手续费，创建/更新持仓
5. 持久化到 sim_* 表
6. 返回 `Order(id, symbol, side, "market", amount, fill_price, "closed")`

### Conditional Order (stop / take_profit)

`create_order(symbol, side="sell", order_type="stop", amount=0.001, price=95000)` 时：

1. 校验订单参数
2. 加入内部挂单列表
3. 持久化到 sim_orders 表
4. 返回 `Order(id, symbol, side, "stop", amount, price, "open")`

挂单不立即成交，由撮合引擎在后续 tick 中触发。

## Matching Engine

撮合引擎是 SimulatedExchange 内部的一个 async task，由 WebSocket 行情驱动：

```python
async def _matching_loop(self):
    while self._running:
        ticker = await self._watch_ticker()   # 阻塞等待下一个 WebSocket tick
        self._latest_ticker = ticker          # 缓存供 fetch_ticker() 使用

        triggered = []
        for order in self._pending_orders:
            if self._should_trigger(order, ticker):
                fill = self._execute_fill(order, ticker)
                triggered.append(fill)

        for fill in triggered:
            self._pending_orders.remove(fill.order)
            await self._persist_state()
            await self._notify_fill(fill)     # 触发回调 → Scheduler
```

### Trigger Conditions

| 订单类型 | 持仓方向 | 触发条件 |
|---|---|---|
| stop (止损) | long | `ticker.bid <= trigger_price` |
| stop (止损) | short | `ticker.ask >= trigger_price` |
| take_profit (止盈) | long | `ticker.bid >= trigger_price` |
| take_profit (止盈) | short | `ticker.ask <= trigger_price` |

触发后以对应的 bid/ask 一档价成交，扣除手续费。

## FillEvent

条件单触发成交时产生 FillEvent，通过回调通知上层：

```python
@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: str          # "buy" / "sell"
    fill_price: float
    amount: float
    fee: float
    pnl: float         # 平仓盈亏（仅平仓时有值）
    timestamp: int
```

上层 fill_handler 收到后：
1. 更新 TradeRecord（`_update_trade_closed()`）
2. 通知 Scheduler 触发 agent cycle（`trigger_type="conditional"`）

## Internal State

SimulatedExchange 在内存中维护三类状态，运行时以内存为权威数据源：

### Balance

```python
free_usdt: float   # 可用余额
used_usdt: float   # 冻结保证金
# total_usdt = free_usdt + used_usdt
```

### Positions

```python
# symbol → Position
{
    "BTC/USDT:USDT": {
        side: "long",
        contracts: 0.001,
        entry_price: 95200.0,
        leverage: 3
    }
}
```

### Pending Orders

```python
[
    {
        id: "sim_001",
        symbol: "BTC/USDT:USDT",
        side: "sell",
        order_type: "stop",
        amount: 0.001,
        trigger_price: 93000.0
    }
]
```

## Persistence

内部状态持久化到三张新表，以 `session_id` 隔离：

### sim_balances

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | PK |
| session_id | str FK | 关联 Session |
| free_usdt | float | 可用余额 |
| used_usdt | float | 冻结保证金 |
| updated_at | datetime | 最后更新时间 |

### sim_positions

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | PK |
| session_id | str FK | 关联 Session |
| symbol | str | 交易对 |
| side | str | long / short |
| contracts | float | 持仓数量 |
| entry_price | float | 入场价 |
| leverage | int | 杠杆 |
| created_at | datetime | 开仓时间 |

### sim_orders

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | PK |
| session_id | str FK | 关联 Session |
| order_id | str | 模拟订单 ID（如 "sim_001"） |
| symbol | str | 交易对 |
| side | str | buy / sell |
| order_type | str | stop / take_profit |
| amount | float | 数量 |
| trigger_price | float | 触发价格 |
| status | str | open / filled / cancelled |
| created_at | datetime | 创建时间 |

## TradeRecord Changes

在现有 `TradeRecord` 表新增一个字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| fee | float, nullable | 该笔交易累计手续费（开仓时写入开仓手续费，平仓时追加平仓手续费） |

## Configuration

### settings.yaml

```yaml
exchange:
  name: simulated       # "okx" → real trading, "simulated" → mock exchange
  fee_rate: 0.0005      # simulated mode: taker fee rate (0.05%)
```

- `name: "okx"` → 创建 `OKXExchange`（需要 API 密钥）
- `name: "simulated"` → 创建 `SimulatedExchange`（使用 OKX 公开 WebSocket，无需交易权限密钥）

`fee_rate` 仅在模拟模式下使用。默认 0.05% 对齐 OKX taker 费率。

### Session.initial_balance

模拟交易所的初始资金来自 `Session.initial_balance`。首次启动时用此值初始化 sim_balances，后续启动从 sim_balances 恢复。

## Lifecycle

### Startup

```
SimulatedExchange.__init__(config, db_engine, session_id)
│
├── 1. 从 sim_* 表查询是否有该 session_id 的记录
│      ├── 有 → 恢复余额、持仓、挂单
│      └── 无 → 用 Session.initial_balance 初始化，写入 sim_balances
├── 2. 连接 OKX 公开 WebSocket（ticker 频道）
├── 3. 启动撮合循环（async task）
└── 4. 就绪
```

### Shutdown

```
SimulatedExchange.close()
│
├── 1. 停止撮合循环（self._running = False）
├── 2. 断开 WebSocket
└── 3. 内部状态已持久化（每次状态变更时已写入 sim_* 表），无需额外操作
```

### Crash Recovery

崩溃后重新启动走正常 Startup 流程，从 sim_* 表恢复状态。由于每次状态变更都持久化，最多丢失最后一个未完成的操作（极端情况下，一笔成交的状态更新可能只完成了一半——这在当前规模下可接受）。

## Interaction Flows

### Flow 1: Agent Opens Position (Market Order)

```
Agent                    tools_execution          SimulatedExchange
  │                           │                          │
  │  open_position(long,30%)  │                          │
  │──────────────────────────▶│                          │
  │                           │  fetch_balance()         │
  │                           │─────────────────────────▶│
  │                           │  Balance(free:100)       │
  │                           │◀─────────────────────────│
  │                           │                          │
  │                           │  fetch_ticker()          │
  │                           │─────────────────────────▶│
  │                           │  Ticker(ask:95010)       │ ← WebSocket cache
  │                           │◀─────────────────────────│
  │                           │                          │
  │                           │  create_order(market,buy)│
  │                           │─────────────────────────▶│
  │                           │         ┌────────────────┤
  │                           │         │ validate balance│
  │                           │         │ fill @ ask price│
  │                           │         │ deduct margin   │
  │                           │         │ deduct fee      │
  │                           │         │ create position │
  │                           │         │ persist sim_*   │
  │                           │         └────────────────┤
  │                           │  Order(filled, 95010)    │
  │                           │◀─────────────────────────│
  │                           │                          │
  │                           │  _record_trade_open()    │
  │                           │  → write TradeRecord     │
  │                           │                          │
  │  "position opened"        │                          │
  │◀──────────────────────────│                          │
```

### Flow 2: Conditional Order Triggers (Stop Loss)

```
OKX WebSocket        SimulatedExchange         fill_handler         Scheduler          Agent
     │                      │                       │                   │                 │
     │  ticker(bid:94800)   │                       │                   │                 │
     │─────────────────────▶│                       │                   │                 │
     │                      │                       │                   │                 │
     │               ┌──────┤                       │                   │                 │
     │               │ matching engine:             │                   │                 │
     │               │ stop @ 95000                 │                   │                 │
     │               │ bid 94800 <= 95000 → trigger │                   │                 │
     │               │                              │                   │                 │
     │               │ fill @ bid 94800             │                   │                 │
     │               │ update internal state        │                   │                 │
     │               │ persist sim_*                │                   │                 │
     │               └──────┤                       │                   │                 │
     │                      │                       │                   │                 │
     │                      │  FillEvent(pnl, fee)  │                   │                 │
     │                      │──────────────────────▶│                   │                 │
     │                      │                       │                   │                 │
     │                      │                       │ write TradeRecord │                 │
     │                      │                       │                   │                 │
     │                      │                       │  trigger(         │                 │
     │                      │                       │  "conditional")   │                 │
     │                      │                       │──────────────────▶│                 │
     │                      │                       │                   │                 │
     │                      │                       │                   │ agent_cycle()   │
     │                      │                       │                   │────────────────▶│
```

### Flow 3: Startup / Crash Recovery

```
main.py                  SimulatedExchange              DB (sim_*)
  │                            │                           │
  │  create SimulatedExchange  │                           │
  │───────────────────────────▶│                           │
  │                            │  query sim_balances       │
  │                            │──────────────────────────▶│
  │                            │  exists? ─ Yes ─▶ restore balance
  │                            │           └ No  ─▶ init from Session.initial_balance
  │                            │                           │
  │                            │  query sim_positions      │
  │                            │──────────────────────────▶│
  │                            │  restore positions        │
  │                            │◀──────────────────────────│
  │                            │                           │
  │                            │  query sim_orders         │
  │                            │──────────────────────────▶│
  │                            │  restore pending orders   │
  │                            │◀──────────────────────────│
  │                            │                           │
  │                            │  connect OKX WebSocket    │
  │                            │  start matching loop      │
  │                            │  ready                    │
  │◀───────────────────────────│                           │
  │                            │                           │
  │  register on_fill callback │                           │
  │  start scheduler           │                           │
```

## File Changes Summary

### Modified Files

| File | Change |
|---|---|
| `src/storage/models.py` | Add `SimBalance`, `SimPosition`, `SimOrder` tables; add `fee` field to `TradeRecord` |
| `src/config.py` | Add `fee_rate` to `ExchangeConfig` |
| `config/settings.yaml` | Add `fee_rate` config |
| `src/cli/app.py` | Route exchange creation by `exchange.name`; register fill callback |
| `src/scheduler/scheduler.py` | Add event-based trigger support (`trigger("conditional")`) |

### New Files

| File | Content |
|---|---|
| `src/integrations/exchange/simulated.py` | SimulatedExchange implementation |

### Unchanged Files

| File | Reason |
|---|---|
| `src/agent/tools_execution.py` | Operates through BaseExchange interface |
| `src/agent/tools_perception.py` | Same |
| `src/agent/trader.py` | Same |
| `src/integrations/exchange/base.py` | Interface unchanged |
| `src/integrations/exchange/okx.py` | No changes needed |

## Scope Boundaries

**In scope:**
- SimulatedExchange 实现 BaseExchange 全部方法
- WebSocket 实时行情驱动撮合
- 市价单即时成交（ask/bid 一档价）
- 条件单（stop/take_profit）挂单管理和触发
- 手续费（可配置费率）
- 内部状态持久化和崩溃恢复
- FillEvent 回调通知机制
- Scheduler 事件触发支持
- 配置切换（exchange.name）

**Out of scope (future enhancements):**
- 历史行情回放（回测模式）
- 资金费率模拟
- 滑点模拟（当前资金规模下 BTC 永续无意义）
- 限价单（当前 agent 只用市价单）
- 部分成交
- 多市场支持（现货、股票）
