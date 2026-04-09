# SimulatedExchange Design Spec

## Overview

实现一个本地模拟交易所（SimulatedExchange），作为 `BaseExchange` 的第二个实现。它接收 OKX 实时行情数据，在本地完成订单撮合，行为对齐真实交易所。目的是零风险零成本地验证 Agent 的交易决策能力。

## Design Principles

1. **对齐真实交易所行为** — SimulatedExchange 的行为边界和真实交易所一致。遇到设计决策时，以真实交易所的行为为准。
2. **上层无感知** — Agent tools 通过 `BaseExchange` 接口操作，不区分真实/模拟环境。
3. **自治** — SimulatedExchange 自己管理行情接收、订单撮合、内部状态和持久化，不依赖上层的数据。
4. **安全护栏优先** — 当对齐真实交易所行为可能导致 agent 错误操作引发意外后果时（如意外反向开仓、穿仓负余额），选择更安全的行为。此类偏离在文档中标注 `[安全护栏]`。

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
| `fetch_ticker(symbol)` | 返回 WebSocket 缓存的最新 ticker（含 bid/ask）。WebSocket 断连期间返回最后可用 ticker（可能过时，是基于最后可用数据的估算值） |
| `fetch_ohlcv(symbol, timeframe, limit)` | 通过 OKX REST 公开 API 获取 K 线（无需认证） |
| `create_order(symbol, side, order_type, amount, price)` | 见下方「订单处理」。symbol 必须匹配 Session 配置的交易对，否则抛异常 |
| `fetch_balance()` | 从内部状态返回 `Balance(total_usdt, free_usdt, used_usdt)`，含未实现盈亏（见下方公式） |
| `fetch_positions(symbol)` | 从内部状态返回持仓列表，用最新 ticker 计算 unrealized_pnl 和 liquidation_price（见下方公式） |
| `set_leverage(symbol, leverage)` | 校验范围（1-125，对齐 OKX 上限 `[安全护栏]`），记录到内部状态 |
| `amount_to_precision(symbol, amount)` | 按 symbol 精度规则向下截断 truncate（与 OKXExchange/ccxt 一致，避免超出余额） |
| `close()` | 停止撮合循环，断开 WebSocket |

构造函数和初始化拆分为两步（Python `__init__` 必须是同步方法）：

- `__init__(config, db_engine, session_id, symbol)` — 同步，仅保存配置和引用，不执行 I/O。`symbol` 用于确定 WebSocket 订阅频道和 `create_order` 的 symbol 校验
- `async start()` — 异步初始化：查询 DB 恢复状态、REST 拉取种子 ticker、连接 WebSocket、启动撮合循环

`app.py` 工厂逻辑中：`exchange = SimulatedExchange(config, db_engine, session_id, symbol); await exchange.start()`。`OKXExchange` 的 `__init__` 也是纯同步的（仅创建 ccxt 客户端），两者风格一致。构造函数不属于 `BaseExchange` 接口，实例化由 `app.py` 根据 `exchange.name` 配置决定。

### fetch_positions() 计算公式

```python
unrealized_pnl:
  long:  (ticker.bid - entry_price) * contracts
  short: (entry_price - ticker.ask) * contracts

liquidation_price (simplified, ignores maintenance margin rate):
  long:  entry_price * (1 - 1 / leverage)
  short: entry_price * (1 + 1 / leverage)
```

注：清算价公式是简化版本，真实 OKX 还考虑维持保证金率。当前规模下差异极小，精确清算价模拟列为 out of scope。

### set_leverage() 与 create_order() 的隐式依赖

`set_leverage(symbol, leverage)` 将杠杆值存入内部状态。后续 `create_order()` 从内部状态读取当前杠杆来计算保证金。这对齐真实交易所的行为——OKX 也是先设置杠杆，后续下单按已设杠杆执行。

额外的公开方法：

| 方法 | 说明 |
|---|---|
| `on_fill(callback)` | 注册 FillEvent 回调（单回调，后注册覆盖前注册） |

## Order Processing

### Market Order

`create_order(symbol, side, order_type="market", amount)` 时同步处理。根据当前持仓状态推断意图（对齐 OKX 净仓模式）：

| 当前持仓 | side="buy" | side="sell" |
|---|---|---|
| 无持仓 | 开多仓 | 开空仓 |
| 持有 long | 加仓（合并均价） | 平多仓 |
| 持有 short | 平空仓 | 加仓（合并均价） |

#### Open Position (开仓 / 加仓)

1. 确定成交价：买入用 `ticker.ask`，卖出用 `ticker.bid`
2. 计算保证金和手续费：
   ```
   margin = (price * amount) / leverage
   fee = price * amount * fee_rate
   required = margin + fee
   ```
3. 校验余额：`free_usdt >= required`，不够则抛异常（对齐真实交易所的拒单行为）。注：上层 `open_position` 工具使用 `ticker.last` 估算下单数量，而实际成交价是 `ticker.ask`/`ticker.bid`，两者有微小差异。这是预期行为——和真实交易所一样，下单前的估算和实际成交会有价差。在极端边界情况下可能导致余额校验失败，agent 会收到拒单反馈
4. 更新内部状态：
   - 余额：`free_usdt -= required`，`used_usdt += margin`
   - 持仓：如果同 symbol 同方向已有持仓，校验 leverage 一致（不一致则抛异常 `[安全护栏]`——要求 agent 先平仓再用新杠杆开仓，避免多杠杆保证金账不平），然后合并并计算加权均价；否则创建新持仓
5. 持久化到 sim_* 表
6. 返回 `Order(id, symbol, side, "market", amount, price, "closed", fee=fee)`

#### Close Position (平仓)

1. 确定成交价：平多用 `ticker.bid`，平空用 `ticker.ask`
2. 计算已实现 PnL 和手续费：
   ```
   long PnL:  (fill_price - entry_price) * amount
   short PnL: (entry_price - fill_price) * amount
   fee = fill_price * amount * fee_rate
   ```
3. 更新内部状态：
   - 释放保证金：`used_usdt -= (entry_price * amount) / leverage`，`free_usdt += released_margin + pnl - fee`。注：entry_price 是加权均价，`weighted_avg * total_amount == Σ(price_i * amount_i)`，所以即使经过加仓，按均价释放保证金总额是正确的
   - 如果 amount >= position.contracts，全部平仓并移除持仓；如果 amount < position.contracts，减少 contracts（部分平仓）。`[安全护栏]` 如果 amount > position.contracts，clamp 到 position.contracts（OKX 净仓模式下超额 sell 会反向开空仓，但这里选择更安全的行为防止 agent 错误输入意外反向开仓）。返回的 Order.amount 为实际成交量（clamp 后的值），上层据此记录准确的交易量

以上步骤 1-3 为**核心平仓逻辑**（`_close_position_core`），由 agent 市价平仓、撮合引擎条件单触发、强制清算三个场景共享。订单取消**不包含在核心逻辑中**，由调用方负责：

- **Agent 市价平仓**（`create_order`）：调用核心逻辑 + 取消该 symbol 所有条件单
- **撮合引擎条件单/清算触发**：调用核心逻辑，由外层 `_remove_order_by_id` + `_cancel_orphaned_orders` 统一处理

4. 持久化到 sim_* 表
5. 返回 `Order(id, symbol, side, "market", amount, price, "closed", fee=fee)`

### Conditional Order (stop / take_profit)

`create_order(symbol, side="sell", order_type="stop", amount=0.001, price=95000)` 时：

1. 校验订单参数；如果当前无持仓，抛异常（对齐真实交易所——无持仓时条件单无意义）。注：创建时不校验 amount <= position.contracts，因为持仓量可能在创建后变化（加仓/部分平仓）。触发时以 `min(order.amount, position.contracts)` 实际执行
2. 从当前持仓推导 position_side，加入内部挂单列表
3. 持久化到 sim_orders 表
4. 返回 `Order(id, symbol, side, "stop", amount, price, "open")`

挂单不立即成交，由撮合引擎在后续 tick 中触发。

### Position Close and Order Cancellation

当持仓被平仓时（无论是市价平仓还是条件单触发），该 symbol 的所有残留条件单自动取消。对齐真实交易所行为——无持仓时条件单无意义。

### Duplicate Conditional Orders

同一持仓可以设置多个条件单（对齐 OKX 行为）。例如 agent 先设止损 95000，再设止损 94000，两个条件单共存，先触发的执行后，另一个随持仓平仓自动取消（见上条）。

## Matching Engine

撮合引擎是 SimulatedExchange 内部的一个 async task，由 WebSocket 行情驱动：

```python
async def _matching_loop(self):
    while self._running:
        ticker = await self._watch_ticker()   # 阻塞等待下一个 WebSocket tick

        # _latest_ticker 在 lock 外更新，是 best-effort 缓存值
        # 不需要与内部状态保持原子一致性——它只是 fetch_ticker() 的快照
        self._latest_ticker = ticker

        # 使用同一个 ticker 快照进行触发判断和成交，避免中间价格变化
        # _has_position 检查保证同一持仓最多触发一个条件单：
        # 第一个 fill 平掉持仓后，后续条件单因 _has_position=False 被跳过
        triggered = []
        async with self._lock:
            # 1. 清算检查：价格穿越 liquidation_price 时强制平仓
            # _force_liquidate 复用 Close Position 逻辑（计算 PnL、释放保证金、移除持仓、取消条件单）
            # [安全护栏] PnL cap: pnl = max(pnl, -(released_margin - fee))，确保清算后余额精确归零
            # 计算: free_usdt += margin + (-(margin - fee)) - fee = 0
            for symbol, pos in list(self._positions.items()):
                liq = self._calc_liquidation_price(pos)
                if pos.side == "long" and ticker.bid <= liq:
                    fill = self._force_liquidate(pos, ticker.bid)
                    triggered.append(fill)
                elif pos.side == "short" and ticker.ask >= liq:
                    fill = self._force_liquidate(pos, ticker.ask)
                    triggered.append(fill)

            # 2. 条件单检查（仅在持仓仍存在时）
            for order in list(self._pending_orders):
                if self._should_trigger(order, ticker):
                    if not self._has_position(order.symbol):
                        continue  # 持仓已被清算或前一个 fill 平掉，跳过
                    fill = self._execute_fill(order, ticker)
                    triggered.append(fill)

            if triggered:
                for fill in triggered:
                    self._remove_order_by_id(fill.order_id)
                self._cancel_orphaned_orders()
                await self._persist_state()  # 单个事务包裹三表写入
        # lock 已释放 —— _notify_fill 必须在 lock 外执行
        # 否则 fill → agent → create_order() → acquire lock → 死锁
        # （asyncio.Lock 不可重入，同一 task 再次 acquire 会永久阻塞）
        for fill in triggered:
            await self._notify_fill(fill)
```

**并发安全**：`create_order()` 和 `_matching_loop()` 都修改内部状态。使用 `asyncio.Lock` 保护所有内部状态修改操作（balance/positions/pending_orders）。关键约束：`_notify_fill()` 必须在 lock 释放后执行，因为通知链路（fill → fill_handler → scheduler.trigger → agent_cycle → create_order）会重新请求 lock。

**启动失败处理**：`start()` 中任何 I/O 步骤失败（DB 查询、REST ticker 拉取、WebSocket 连接）都直接抛异常，由上层 `app.py` 处理（打日志 + 退出）。不在 `start()` 内重试——启动阶段的失败应快速暴露。

**WebSocket 首次 tick 竞态**：在 WebSocket 连接建立后、收到第一个 tick 之前，`fetch_ticker()` 可能被调用。启动时先通过 REST API 拉取一次 ticker 作为种子值写入 `_latest_ticker`，再启动 WebSocket 和撮合循环。

**WebSocket 断连处理**：ccxt 的 `watch_ticker` 内置自动重连机制。断连期间撮合循环阻塞在 `_watch_ticker()` 上，不会产生错误触发。重连后自动恢复，挂单继续按最新行情检查。

**Order ID 生成**：使用 UUID（`str(uuid.uuid4())`），避免计数器状态管理。

**日志策略**：使用项目现有的 `logging` 模块，对关键事件记录结构化日志：订单创建/成交（含价格、数量、fee）、清算触发、条件单触发、状态恢复（启动时从 DB 恢复的持仓/挂单数量）、WebSocket 连接/断连。日志级别：成交和清算用 INFO，状态变更用 DEBUG。

### Trigger Conditions

| 订单类型 | 持仓方向 | 触发条件 |
|---|---|---|
| stop (止损) | long | `ticker.bid <= trigger_price` |
| stop (止损) | short | `ticker.ask >= trigger_price` |
| take_profit (止盈) | long | `ticker.bid >= trigger_price` |
| take_profit (止盈) | short | `ticker.ask <= trigger_price` |

触发后以对应的 bid/ask 一档价成交，扣除手续费。触发时实际平仓数量 = `min(order.amount, position.contracts)`，复用 Close Position 的 clamp 逻辑（agent 可能在设置条件单后加仓导致 amount 与实际持仓不一致）。

## FillEvent

条件单触发成交时产生 FillEvent，通过回调通知上层：

```python
@dataclass
class FillEvent:
    order_id: str
    symbol: str
    side: str              # "buy" / "sell"（订单方向）
    position_side: str     # "long" / "short"（被平仓的持仓方向，用于匹配 TradeRecord）
    trigger_reason: str    # "stop" / "take_profit" / "liquidation"（触发原因，agent 可据此调整反思策略）
    fill_price: float
    amount: float
    fee: float
    timestamp: int
    # 注：不含 pnl 字段。PnL 由 TradeRecord 的 entry_price/exit_price/quantity/side/fee 推导，
    # 不作为冗余值传递。交易所内部仍计算 PnL 用于余额更新，但不对外暴露。
```

### fill_handler (app.py)

fill_handler 是连接 SimulatedExchange 和上层系统的胶水层，定义在 `app.py` 中：

```python
def _create_fill_handler(deps: TradingDeps, scheduler):
    async def handle_fill(event: FillEvent):
        # 1. 更新 TradeRecord（用 position_side 匹配 "long"/"short"）
        # 注：side 参数语义为 position side（"long"/"short"），不是 order side（"buy"/"sell"）
        try:
            await _update_trade_closed(
                deps, event.symbol, event.position_side,
                exit_price=event.fill_price, fee=event.fee
            )
        except Exception:
            logger.error("Failed to update TradeRecord for fill %s", event.order_id, exc_info=True)
            # 不 return —— 交易所状态已更新，必须唤醒 agent 让其感知最新状态
        # 2. 唤醒 agent
        await scheduler.trigger("conditional", context=event)
    return handle_fill

# 注册回调
exchange.on_fill(_create_fill_handler(deps, scheduler))
```

通过闭包捕获 `deps`（含 db_engine、session_id 等）和 `scheduler` 引用，调用现有的 `_update_trade_closed()` 写 TradeRecord。注：`_update_trade_closed()` 按 session_id + symbol + side + status='open' 四重条件查询（已确认现有代码 tools_execution.py:42-50），确保精确匹配目标记录。

### Scheduler.trigger() 语义

Scheduler 新增 `trigger(trigger_type, context=None)` 方法：

| 行为 | 说明 |
|---|---|
| 绕过 cooldown | 条件单触发是时间敏感的，不等待 cooldown 倒计时 |
| 防重入 | 如果 agent cycle 正在运行，设置一个 pending flag（不是队列）。当前 cycle 结束后检查 flag，如有则触发一次新 cycle（`context=None`）。多个事件只设一个 flag，合并为一次触发，避免连续 cycle 风暴。context=None 的设计理由：多个事件合并后没有单一"正确"的 context，agent 应基于最新全局状态（通过 fetch_positions/fetch_balance 查询）做决策，而非依赖某个特定事件 |
| 与定时触发的关系 | 事件触发独立于定时调度。事件触发后重置定时计时器（避免刚处理完 fill 又立即触发定时 cycle） |
| 异常处理 | 如果 fill 触发的 agent cycle 执行失败（抛异常），清除 pending flag，与现有 scheduler 的 except 后继续循环行为一致 |

callback 签名变更：

```python
# 现有：scheduler.py 中 on_tick 闭包硬编码 "scheduled"
async def on_tick():
    await run_agent_cycle(agent, deps, "scheduled", budget, engine)

# 变更为：Scheduler 持有 trigger_type 和 context，调用时传入
callback: Callable[[str, Any | None], Awaitable[None]]
#          trigger_type ↑    context ↑

# on_tick 变更为接收参数并转发
async def on_tick(trigger_type: str, context: Any | None):
    await run_agent_cycle(agent, deps, trigger_type, budget, engine, context)
```

- 定时触发时 Scheduler 调用 `callback("scheduled", None)`
- 事件触发时 Scheduler 调用 `callback("conditional", fill_event)`
- `run_agent_cycle` 新增 `context` 参数。当 context 非 None 时，在 agent prompt 中追加事件摘要：`"Event: {trigger_reason} triggered at {fill_price}"`，让 agent 知道发生了什么并据此决策（agent 通过 fetch_balance/fetch_positions 查询最新状态获取完整信息）

## Internal State

SimulatedExchange 在内存中维护三类状态，运行时以内存为权威数据源：

### Balance

```python
free_usdt: float   # 可用余额（不含未实现盈亏）
used_usdt: float   # 冻结保证金
```

`fetch_balance()` 返回时包含未实现盈亏（对齐 OKX equity 计算）：

```python
unrealized = sum(calc_unrealized_pnl(pos, ticker) for pos in positions)
Balance(
    total_usdt = free_usdt + used_usdt + unrealized,   # equity
    free_usdt  = max(0, free_usdt + unrealized),        # 可用于开仓，不低于 0
    used_usdt  = used_usdt,                             # 冻结保证金不变
)
```

注：内部存储的 `free_usdt` 不含 unrealized_pnl，仅在 `fetch_balance()` 返回时动态加上。这避免了每次 tick 都更新存储。unrealized_pnl 基于 `_latest_ticker` 计算，与 `fetch_ticker()` 语义一致——WebSocket 断连或启动初期使用最后可用 ticker，是 best-effort 估算值。

注意：当 unrealized 亏损较大时，`total_usdt != free_usdt(returned) + used_usdt`。这是正确的——total 是 equity（含浮亏），free 被 clamp 到 0（不允许用浮亏资金开仓），两者是独立概念。Agent 的 perception tools 应使用 `free_usdt` 判断可用资金，使用 `total_usdt` 判断总权益。

### Leverage

```python
# symbol → int
{ "BTC/USDT:USDT": 3 }
```

由 `set_leverage()` 写入，`create_order()` 通过 `_leverage.get(symbol, 1)` 读取（默认 1x 无杠杆，最安全的缺省值）。

### Positions

```python
# symbol → Position
{
    "BTC/USDT:USDT": {
        side: "long",
        contracts: 0.001,
        entry_price: 95200.0,
        leverage: 3,
        created_at: datetime,     # 开仓时间，持久化时写入 DB
        updated_at: datetime,     # 最后修改时间（加仓/部分平仓时更新）
    }
}
```

`fetch_positions()` 返回时，`unrealized_pnl` 和 `liquidation_price` 根据上方公式实时计算，不存储。

### Adding to Position (加仓)

同 symbol 同方向再次开仓时，合并持仓并计算加权均价（对齐 OKX 行为）：

```
# 前置校验：current_leverage must == position.leverage，否则抛异常 [安全护栏]
new_entry_price = (old_entry * old_contracts + fill_price * new_contracts)
                  / (old_contracts + new_contracts)
new_contracts = old_contracts + new_contracts
# position.leverage 保持不变（强制一致保证了这一点）
```

### Pending Orders

```python
[
    {
        id: "a1b2c3d4-...",       # UUID
        symbol: "BTC/USDT:USDT",
        side: "sell",              # 订单方向 (buy/sell)
        position_side: "long",     # 关联的持仓方向 (long/short)，用于 _should_trigger 匹配触发条件
        order_type: "stop",
        amount: 0.001,
        trigger_price: 93000.0
    }
]
```

`position_side` 在 `create_order()` 创建条件单时写入（从当前持仓方向推导），`_should_trigger()` 直接用此字段查表，无需反查持仓。

## Persistence

内部状态持久化到三张新表，以 `session_id` 隔离：

### sim_balances

| 字段 | 类型 | 说明 |
|---|---|---|
| session_id | str FK | PK，关联 Session（每个 session 只有一行） |
| free_usdt | float | 可用余额（不含 unrealized_pnl） |
| used_usdt | float | 冻结保证金 |
| updated_at | datetime | 最后更新时间 |

### sim_positions

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | PK |
| session_id | str FK | 关联 Session |
| symbol | str | 交易对，UNIQUE(session_id, symbol) |
| side | str | long / short |
| contracts | float | 持仓数量 |
| entry_price | float | 入场价 |
| leverage | int | 杠杆 |
| created_at | datetime | 开仓时间 |
| updated_at | datetime | 最后修改时间（加仓/部分平仓） |

### sim_orders

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | PK |
| session_id | str FK | 关联 Session |
| order_id | str, UNIQUE | 模拟订单 ID（UUID） |
| symbol | str | 交易对 |
| side | str | buy / sell |
| position_side | str | long / short（关联的持仓方向） |
| order_type | str | stop / take_profit |
| amount | float | 数量 |
| trigger_price | float | 触发价格 |
| status | str | open / filled / cancelled |
| filled_price | float, nullable | 实际成交价（filled 时写入） |
| filled_at | datetime, nullable | 成交时间（filled 时写入） |
| created_at | datetime | 创建时间 |

## BaseExchange Interface Changes

### Order dataclass 新增 fee 字段

```python
@dataclass
class Order:
    id: str
    symbol: str
    side: str
    order_type: str
    amount: float
    price: float | None
    status: str
    fee: float | None = None   # 新增：成交手续费
```

- `SimulatedExchange`：在 `create_order()` 中计算并填入 fee
- `OKXExchange`：从 ccxt 响应中解析 fee（如有），或返回 None。已知限制：OKX 的 market order REST 响应中 fee 可能为空（需 fetch_order 才能拿到完整 fee），因此 OKX 模式下 TradeRecord.fee 可能不完整
- `tools_execution.py`：统一从 `Order.fee` 读取并写入 TradeRecord

这样 fee 信息通过接口层传递，上层不需要区分模拟/真实模式。

## TradeRecord Changes

| 变更 | 说明 |
|---|---|
| 移除 `pnl` 字段 | PnL 从 entry_price/exit_price/quantity/side/fee 推导，不再存储 |
| 新增 `fee` 字段 (float, nullable) | 该笔交易累计手续费（开仓 + 平仓） |

手续费累加规则：
- `_record_trade_open()`：`record.fee = order.fee`（写入开仓手续费）
- `_update_trade_closed()`：`record.fee = (record.fee or 0) + order.fee`（追加平仓手续费）

`_update_trade_closed()` 签名变更：移除 `pnl` 参数，新增 `fee` 参数：

```python
# 现有
async def _update_trade_closed(deps, symbol, side, pnl, exit_price=None)

# 变更为
async def _update_trade_closed(deps, symbol, side, exit_price, fee=None)
# 不再写入 pnl —— PnL 从 entry_price/exit_price/quantity/side/fee 推导
```

### tools_execution.py 价格准确性修复

现有代码使用 `ticker.last` 近似 entry_price 和 exit_price，实际成交价（特别是模拟模式下）是 ask/bid。趁此次修改一并修复：

| 字段 | 现有（不准确） | 修正为 |
|---|---|---|
| entry_price | `ticker.last` (tools_execution.py:144) | `order.price`（create_order 返回的实际成交价） |
| exit_price | `ticker.last` (tools_execution.py:181) | `order.price` |

PnL 不再存储，由 `MetricsService` 从 entry_price/exit_price/quantity/side/fee 推导。

### TradeRecord.pnl 字段移除

移除现有 `TradeRecord.pnl` 字段。PnL 是 entry_price、exit_price、quantity、side、fee 的推导值，存储冗余字段会引入不一致风险（特别是加仓后部分平仓场景）。

**MetricsService 改动**：从直接读 `t.pnl` 改为推导计算：

```python
def _calc_pnl(t: TradeRecord) -> float | None:
    if t.exit_price is None:
        return None
    if t.side == "long":
        gross = (t.exit_price - t.entry_price) * t.quantity
    else:
        gross = (t.entry_price - t.exit_price) * t.quantity
    return gross - (t.fee or 0)
```

下游不变——total_pnl、win_rate、profit_factor、max_drawdown 仍基于 pnls 列表计算。

**受影响的测试**：`tests/test_metrics.py` 需要重写（构造 TradeRecord 时用 entry/exit/quantity/side/fee 替代直接设 pnl）。

## Configuration

### settings.yaml

```yaml
exchange:
  name: simulated       # "okx" → real trading, "simulated" → mock exchange
  fee_rate: 0.0005      # simulated mode: taker fee rate (0.05%)
  precision:            # simulated mode: symbol → decimal places for amount_to_precision()
    BTC/USDT:USDT: 3   # 全局配置，运行时只使用 Session 配置的 symbol 对应的精度
    ETH/USDT:USDT: 2
```

- `name: "okx"` → 创建 `OKXExchange`（需要 API 密钥）
- `name: "simulated"` → 创建 `SimulatedExchange`（使用 OKX 公开 WebSocket，无需交易权限密钥）

`fee_rate` 仅在模拟模式下使用。默认 0.05% 对齐 OKX taker 费率。`precision` 为 symbol → 小数位数映射，供 `amount_to_precision()` 使用。

### Dependencies

SimulatedExchange 使用 `watch_ticker()`（WebSocket），这是 ccxt Pro API。需要在 `pyproject.toml` 中将 `ccxt` 依赖改为 `ccxt[pro]`（ccxt Pro 包含标准版全部功能，向后兼容）。

SimulatedExchange 内部使用一个无认证的 `ccxt.pro.okx` 实例，同时用于 REST API（K 线查询、种子 ticker）和 WebSocket（实时行情）。无需交易权限密钥——公开行情接口不需要认证。

### Session.initial_balance

模拟交易所的初始资金来自 `Session.initial_balance`。首次启动时用此值初始化 sim_balances，后续启动从 sim_balances 恢复。

## Lifecycle

### Startup

```
SimulatedExchange.__init__(config, db_engine, session_id, symbol)  # 同步，仅保存配置
│
await exchange.start()                                       # 异步初始化
│
├── 1. 从 sim_* 表查询该 session_id 的记录
│      ├── 有 → 恢复余额、持仓、挂单（仅 sim_orders.status='open'）
│      │        从 sim_positions.leverage 初始化 leverage 字典（持仓的杠杆即当前杠杆）
│      │        已知限制：(a) 无持仓时 leverage 恢复为默认值 1x；(b) 有持仓时恢复为 position.leverage，crash 前若已调 set_leverage 改了值但未下单，该值丢失。两种情况影响极小（agent 开仓前总会调 set_leverage）
│      └── 无 → 用 Session.initial_balance 初始化，写入 sim_balances
├── 2. 通过 REST API 拉取一次 ticker 写入 _latest_ticker（种子值，避免首次 tick 前竞态）
├── 3. 连接 OKX 公开 WebSocket（ticker 频道）
├── 4. 启动撮合循环（async task）
└── 5. 就绪
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

崩溃后重新启动走正常 Startup 流程，从 sim_* 表恢复状态。恢复语义为 **at-least-once**：如果 crash 发生在 `_persist_state()` 的 DB commit 之前，状态回退到上次成功持久化的点，条件单可能被重新触发。这在模拟环境下可接受。

`_persist_state()` 使用单个 SQLAlchemy 事务（`async with session.begin()`）包裹三张表的写入，保证原子性。各表写入策略：

| 表 | 策略 | 说明 |
|---|---|---|
| sim_balances | upsert by session_id | 只有一行，直接覆盖 |
| sim_positions | delete where session_id → insert current | 全量替换，平仓后该行被删除 |
| sim_orders | UPDATE + upsert，不删除历史 | 本轮 filled/cancelled 的 order 执行 UPDATE（写入 status、filled_price、filled_at）；open orders 执行 upsert。历史记录保留用于审计 |

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
  │───────────────────────────▶│  (sync, no I/O)           │
  │◀───────────────────────────│                           │
  │                            │                           │
  │  register on_fill callback │  ← 在 start() 之前注册
  │                            │                           │
  │  await exchange.start()    │                           │
  │───────────────────────────▶│  query sim_balances       │
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
  │                            │  REST fetch seed ticker   │
  │                            │  connect OKX WebSocket    │
  │                            │  start matching loop      │
  │◀───────────────────────────│  ready                    │
  │                            │                           │
  │  start scheduler           │                           │
```

## File Changes Summary

### Modified Files

| File | Change |
|---|---|
| `src/integrations/exchange/base.py` | `Order` dataclass 新增 `fee: float \| None = None` 字段 |
| `src/integrations/exchange/okx.py` | `create_order()` 从 ccxt 响应解析 fee 并填入 Order |
| `src/storage/models.py` | Add `SimBalance`, `SimPosition`, `SimOrder` tables; TradeRecord: 移除 `pnl` 字段, 新增 `fee` 字段 |
| `src/agent/tools_execution.py` | `_update_trade_closed()` 移除 `pnl` 参数, 新增 `fee` 参数；`_record_trade_open()` 写入 `order.fee`；entry_price/exit_price 改用 Order.price |
| `src/services/metrics.py` | PnL 从直接读 `t.pnl` 改为从 entry_price/exit_price/quantity/side/fee 推导计算 |
| `tests/test_metrics.py` | 重写：TradeRecord 构造改用 entry/exit/quantity/side/fee |
| `src/config.py` | Add `fee_rate`, `precision` to `ExchangeConfig` |
| `pyproject.toml` | `ccxt` → `ccxt[pro]`（WebSocket 支持） |
| `config/settings.yaml` | Add `fee_rate` config |
| `src/cli/app.py` | Route exchange creation by `exchange.name`; register fill callback; `run_agent_cycle` 新增 `context` 参数 |
| `src/scheduler/scheduler.py` | Add event-based trigger support (`trigger("conditional")`) |

### New Files

| File | Content |
|---|---|
| `src/integrations/exchange/simulated.py` | SimulatedExchange implementation |

### Unchanged Files

| File | Reason |
|---|---|
| `src/agent/tools_perception.py` | Operates through BaseExchange interface |
| `src/agent/trader.py` | Same |

## Scope Boundaries

**Constraints:**
- 当前版本仅支持单一 symbol（Session 级别配置的交易对）。`create_order()` 收到非配置 symbol 抛异常。WebSocket 仅订阅该 symbol 的 ticker 频道。

**In scope:**
- SimulatedExchange 实现 BaseExchange 全部方法
- WebSocket 实时行情驱动撮合
- 市价单即时成交（ask/bid 一档价）
- 条件单（stop/take_profit）挂单管理和触发
- 手续费（可配置费率）
- 内部状态持久化和崩溃恢复
- 自动强制平仓（清算）：价格穿越 liquidation_price 时强制平仓，防止负余额
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
- 精确清算价计算（含维持保证金率）
- amount_to_precision 从 REST API market info 自动获取（当前手动配置）
- WebSocket 长时间无 tick 的超时处理（BTC/USDT 主流交易对下极不可能发生）
- 部分平仓的 TradeRecord 管理（当前工具层总是全额平仓，TradeRecord 一开一闭匹配）
- sim_orders 历史记录清理机制（长期运行会增长，当前规模下不构成问题）
