# Toolkit Expansion Iter 2 — 设计文档（Iteration 2 / 4）

## 0. 背景

### 0.0 项目快照

**TradeBot** 是一个 LLM 驱动的加密货币自动交易系统。Agent（Claude）通过工具调用感知市场、管理仓位、做出交易决策，在 USDT 保证金永续合约上自主交易。

**运行循环**：每 15 分钟唤醒一次（也可被订单成交、价格警报等事件提前唤醒），进入 `run_agent_cycle()` → LLM 调用工具分析 → 返回交易决策 → 写入 DecisionLog。

**技术栈**：Python 3.13 / pydantic-ai 1.78.0 / SQLAlchemy 2.0 async + SQLite(WAL) / pytest + pytest-asyncio / ccxt (OKX, defaultType=swap)。

**工具库规模**：本轮后 **29 个**（18 感知 + 10 执行 + 1 memory）—— 当前 26（15+10+1），新增 3 感知工具（order_book / recent_trades / multi_timeframe_snapshot），另 1 个 `get_position` 原地增强不新增名字。

**当前状态（2026-04-20，21 PRs merged）**：681 测试通过；Iter 1 的 tool-call metrics enabler 已 landed（PR #21，`tool_calls` 表 + ToolCallRecorder 零侵入包装）。

### 0.1 所处位置

本 spec 是"进观察期前 4-iteration 计划"的第 2 轮。4 轮主题依次为：

| # | 主题 | 状态 |
|---|------|------|
| 1 | 观察基础设施 — tool-call metrics enabler | ✅ PR #21 landed |
| **2** | **工具补全 — order_book + recent_trades + multi_tf_snapshot + get_position 增强** | ✅ **本文** |
| 3 | 结构感知工具 `get_price_pivots` 朴素版 | 下一 session |
| 4 | N7 Layer 1 prompt 组织重构（基于 29 工具完整集合） | 最后一 session |

### 0.2 为什么是这一轮

两个另起会话独立做的"代入 agent 视角审视工具库 gap"分析收敛到同一份 15 项清单（记 memory `project_next_iteration_toolkit_expansion`）。其中 🔴 最高优先级 6 项是 agent 决策的**核心依赖**。本轮实现 6 项中的 5 项：

| 优先级 | 工具 | 本轮 | 理由 |
|------|------|------|------|
| 🔴 #1 | `get_order_book` | ✅ | 流动性 / 挂单分布 / 止损击穿风险 |
| 🔴 #2 | `get_price_pivots`（原 `get_key_levels`）| ❌ Iter 3 | swing 算法 + "朴素版 vs 完整版"边界独立讨论 |
| 🔴 #3 | `get_recent_trades` | ✅ | 主动盘方向 / 突破真假 / 吸筹出货节奏 |
| 🔴 #4 | `get_multi_timeframe_snapshot` | ✅ | 多 TF 对齐扫视 / 防止忘调某周期 |
| 🔴 #5 | `get_position` 风险敞口增强 | ✅ | 爆仓缓冲 ATR 倍数 / 敞口占比 |
| 🔴 #6 | `get_position` SL/TP 距离增强 | ✅ | 跟踪管理必备 / 裸仓风险显式化 |

#2 独立到 Iter 3 的理由：swing 算法有独立设计决策（N 值、label 命名、朴素版边界），且第二版依赖观察期数据做 volume profile / ranking 决策。详见 memory `project_pre_observation_iterations` 方案 D。

### 0.3 硬约束

- **N5 fact-only**：PR #18 已把所有工具输出清除情感标签（bullish/bearish/oversold/wall 等）。本轮 3 新工具 + `get_position` 增强部分必须延续此约束，禁词包括：单词类 `wall / aggressive / bullish / bearish / overbought / oversold / dry powder / risk-on / risk-off / bull market / bear market`；组合词类 `strong support / strong resistance / weak support / weak resistance`（单独 `strong` / `weak` 在 fact 场景可能无害，不做 word-boundary 级禁用，见 §3.5 regex 定义）。"Concentrated levels" / "alignment" 等**事实描述词**允许。
- **Three-state 契约**（PR C §3.5）：每个新工具自己处理 `数据 / 空数据 / 服务失败` 三态，format 模仿 `get_higher_timeframe_view`（`src/agent/tools_perception.py:614-692`）。
- **ToolCallRecorder 零侵入**（PR #21）：新 `@agent.tool` 自动被 capability 包装，无需手动注册，但必须更新 `REGISTERED_TOOL_NAMES`（`src/agent/trader.py:319-349`）使 drift-detection 测试通过。
- **Rate limit 尊重**：OKX public market endpoints rate limit 充裕（agent cycle 15min 一次，每轮 1-2 次调用），本轮**不加** TTL cache（order book / trades 的数据时效性要求高，缓存反有害），但要复用 `@_retry` 装饰器（`src/integrations/exchange/okx.py:53-79`）。
- **不触碰 HTF**：`get_higher_timeframe_view` 在 PR C 已闭环打磨，`get_multi_timeframe_snapshot` 的 4h/1d 输出**精简到 3-4 字段**以区分职责（HTF = 单 TF 深度读数，snapshot = 多 TF 横向扫视）。
- **不触碰 Layer 1 prompt**：N7 重组留 Iter 4 做。本轮只在 `persona.py` 的 Layer 1 末尾追加 4 条新工具条目，不动其他结构。

### 0.4 术语表

| 术语 | 含义 |
|------|------|
| **动能 / 结构 / 波动 / 范围** | `get_multi_timeframe_snapshot` 每 TF 4 栏事实，见 §2.3 |
| **Concentrated levels** | `get_order_book` 中"挂单量 > 3× top-20 median"的价位，不是 "wall"（见 §2.1） |
| **Taker buy/sell** | CCXT `fetch_trades` 返回的 `side` 字段；主动买即 taker 吃 ask、主动卖即 taker 吃 bid |
| **ATR(1h)** | 基于 1h OHLCV 的 14-period ATR，用作本轮跨上下文的"波动基准"（见 §3.3） |
| **Bucket** | `get_recent_trades` 的时间分桶（默认 5 × 60s） |
| **Primary MA** | 每 TF 的"动能栏"主参考 MA：5m→MA20，1h/4h/1d→MA50 |

---

## 1. 目标与非目标

### 1.1 目标

1. 新增 3 个 perception 工具：`get_order_book` / `get_recent_trades` / `get_multi_timeframe_snapshot`
2. 原地增强 1 个现有工具：`get_position`（+ Risk exposure 区块 + Exit orders 区块，名字不变）
3. 扩展 `BaseExchange` 增加 3 个抽象方法：`fetch_order_book` / `fetch_trades` / `get_contract_size`，在 `OKXExchange` 与 `SimulatedExchange` 分别实现
4. 所有新工具遵循 fact-only + three-state 契约
5. `REGISTERED_TOOL_NAMES` 从 26 更新到 29
6. Agent Layer 1 prompt（`persona.py`）末尾追加 4 条新工具引导（3 新工具 + 1 增强说明）

> **本轮不做**：OKX `_parse_order` algo-order 归一化（拆 Iter 2b，见 §1.2 非目标表；SL/TP 识别本轮只对 Sim 原生 `order_type` 工作，实盘接入前必须完成 Iter 2b）

### 1.2 非目标

| 项 | 原因 |
|----|------|
| `get_price_pivots` / swing 结构识别 | Iter 3 独立做 |
| 大单识别加入 `get_recent_trades` | 阈值调参需观察期数据支撑（YAGNI） |
| 订单簿 bin 聚合（按 0.05% 分桶）| 与 "concentrated levels" 路线二选一，已选后者 |
| 订单簿缓存 / TTLCache | order book 数据时效性强，缓存反害 |
| 改 `get_higher_timeframe_view` | PR C 已闭环，本轮不触碰 |
| Layer 1 prompt 重组 / bullet 分组 | N7 议题留 Iter 4（基于完整 29 工具集做 final 重组） |
| `get_open_orders` 暴露 `order_type` 字段 | 本轮不动 — SL/TP 距离通过内部 query 在 `get_position` 渲染即可 |
| 真实 OKX 创建 SL/TP 单的 algo-order 支持 | 本轮只 read 已存在订单，不 create |
| **OKX `_parse_order` algo-order 归一化** | 本轮**明牌延后到 Iter 2b**。理由：`OKXExchange.__init__`（`okx.py:85-92`）当前无 `sandboxMode` 配置，`.env.example` 也无 demo 账户字段，Pre-work 所需的"对 demo 账户验 algo raw 格式"前置不成立（见 §9）。本轮 SL/TP 识别只对 `simulated.py:48` 原生支持的 `order_type in ("stop", "take_profit")` 工作；实盘接入前必须完成 Iter 2b（其工作范围：sandboxMode 配置化 + `.env` 扩展 + `_parse_order` algo 归一化 + `get_open_orders` OCO 合并展示）|

**留 writing-plans 阶段敲定的实施细节**（不属 spec 本轮决策）：
- 3 新 `@agent.tool` 的 docstring 模板（LLM 工具选择的一级入口，plan 阶段必须给出）
- `OKXExchange.start()` 中 `load_markets()` 调用的异常处理位置（try 内 / try 外 / fail-fast vs fallback）。当前 WebSocket try/except 会吞异常自然落到 `get_contract_size` 的懒加载 fallback，行为 OK 但 plan 阶段需明确写清

### 1.3 改动清单

**新建文件**（3 个）：

| 文件 | 作用 | 规模估算 |
|------|------|----------|
| `tests/test_exchange_order_book.py` | `fetch_order_book` + `fetch_trades` 两端（OKX CCXT mock / SimulatedExchange）单元测试 | ~180 行 |
| `tests/test_toolkit_iter2.py` | 3 新工具 + `get_position` 增强渲染测试 | ~350 行 |
| `tests/test_fact_only_wordlist.py` | 新工具 fact-only 禁词扫描 regression 测试（4 × 3-4 场景 ≈ 15 测试） | ~100 行 |

**修改文件**（12 个 — 7 个源码 + 5 个测试；其中 `test_display_cycle.py` 为非 blocker 的 representational 更新，其余必修）：

| 文件 | 改动 |
|------|------|
| `src/integrations/exchange/base.py` | 新增 dataclass `OrderBookLevel` / `OrderBook` / `Trade`；`BaseExchange` 抽象方法 `fetch_order_book` / `fetch_trades` / `get_contract_size`（`get_contract_size` 定义见 §3.2） |
| `src/integrations/exchange/okx.py` | `fetch_order_book` / `fetch_trades` CCXT 实现（含 **`@_retry(max_retries=2, base_delay=0.5)` 高时效重载**，见 §3.3）+ `get_contract_size`（读 `self._client.markets[symbol]["contractSize"]`，见 §3.2）；现有 `start()` 头部追加 `await self._client.load_markets()` 预加载。**不动 `_parse_order`**（algo-order 归一化延后 Iter 2b，见 §1.2） |
| `src/integrations/exchange/simulated.py` | `fetch_order_book` / `fetch_trades` / `get_contract_size` 模拟实现；新增 `self._prev_ticker: Ticker \| None = None` 字段（用于 `fetch_trades` 方向偏置，在每次 `_latest_ticker` 更新前把旧值写入 `_prev_ticker`，见 §4.3） |
| `src/integrations/market_data.py` | 薄方法：`get_order_book` / `get_recent_trades`（委托 exchange，不加 cache） |
| `src/agent/tools_perception.py` | 新增 3 tool 函数（order_book / recent_trades / multi_timeframe_snapshot）+ 原地增强 `get_position`；新增 module-level 常量（见 §3.1）|
| `src/agent/trader.py` | 3 个 `@agent.tool` 新包装（order_book / recent_trades / multi_timeframe_snapshot；`get_position` 是原地增强不算新 wrap）；`REGISTERED_TOOL_NAMES` 从 26 → 29 |
| `src/agent/persona.py` | Layer 1 末尾追加 4 条工具引导 bullet |
| `tests/test_trader_agent.py` | **必修**：`test_registered_tool_names_matches_agent_tools` 在 `test_trader_agent.py:84-85` 硬编码 `assert len(REGISTERED_TOOL_NAMES) == 26` 和 error message `"Expected 26 tools (15+10+1)"`，本轮必须同步更新到 29 和 `"Expected 29 tools (18+10+1)"`；追加测试验证 3 新工具被 ToolCallRecorder 自动包装（复用 Iter 1 pattern） |
| `tests/test_exchange.py` | **必修**：7 个 `BaseExchange` 子类（`IncompleteExchange` 一处故意保留测试抽象合同报错；另外 5 个 `DummyExchange` + 1 个 `_Stub`）需要分别添加 `fetch_order_book` / `fetch_trades` / `get_contract_size` 的 stub 实现（各返回最小合法值），否则 `Can't instantiate abstract class` |
| `tests/test_price_level_alert.py` | **必修**：1 个 `_TestExchange` 子类加同样 3 个 stub |
| `tests/test_tool_enhancement.py` | **必修**：2 个 `_TestExchange` 子类加同样 3 个 stub；追加 Risk exposure + Exit orders 新字段断言（现有 `:499` `"away" in result.lower()` 不 regress，但应补"notional" / "Exit orders" 字段覆盖） |
| `tests/test_display_cycle.py` | **建议更新**（非 blocker）：`:39` 的 `summarize_get_position_with_position` mock content 硬编码了旧 `get_position` 格式（只有 `Liquidation: 55000.00 (34.7% away)`），应更新到新格式（含 Risk exposure + Exit orders）以保持 mock 的 representational accuracy。不更新不会导致测试 regress（`summarize_tool` 单测不依赖 content 精确格式），但 mock 与真实输出 divergent 会降低测试可读性 |

**测试总数估计**：681 → **~725**（新增约 45，含 exchange 层 ~10（含 `get_contract_size` 3 场景）+ 工具渲染层 ~25 + fact-only 守门 ~15 - algo 归一化测试 ~5 移至 Iter 2b）。与 §6 acceptance 一致。

**源码规模估计**：~700 行（exchange 层 ~150 + tools_perception 新代码 ~400 + 其他散点 ~150）。

---

## 2. 工具设计

### 2.1 `get_order_book(depth=20)`

**Signature**: `async def get_order_book(deps: TradingDeps, depth: int = 20) -> str`

**输出示例**（BTC 场景）：

```
=== Order Book (BTC/USDT:USDT) ===
Best bid: 64190.5 × 0.024 BTC  |  Best ask: 64200.5 × 0.032 BTC
Spread: 10.0 (0.016%)

Depth (top 20 each side):
  Bids cumulative: 5.23 BTC over 64190.5 - 64185.0 (0.08% deep)
  Asks cumulative: 4.87 BTC over 64200.5 - 64206.0 (0.08% deep)
  Bid share: 51.8% (bid : ask = 1.07 : 1)

Concentrated levels (size > 3× median of top 20):
  Bid  64185.5  1.52 BTC  (0.08% below mid)
  Bid  64182.0  0.85 BTC  (0.13% below mid)
  Ask  64203.5  1.20 BTC  (0.05% above mid)
  Ask  64215.0  2.45 BTC  (0.23% above mid)
```

**三态**：
- 数据：如上格式
- 空数据（order book 为空 / depth < 请求值）：`f"Order book ({symbol}): insufficient data (requested depth {depth}, got {actual})"`
- 服务失败：`f"Order book ({symbol}): temporarily unavailable"`

**关键阈值/常量**（module-level，`src/agent/tools_perception.py`）：

```python
ORDER_BOOK_CONCENTRATION_MULTIPLIER = 3.0   # 大单阈值：挂单量 > 3× 同边 top-N median
ORDER_BOOK_MAX_CONCENTRATED_LEVELS = 10     # 最多展示 10 个 concentrated levels（防爆）
ORDER_BOOK_DEPTH_DEFAULT = 20               # depth 参数默认值
ORDER_BOOK_BALANCED_THRESHOLD_PCT = 5.0     # bid_share 在 (45%, 55%) 开区间 → "balanced"；条件 abs(bid_share - 50) < 5.0（严格 <，45/55 恰好值不算 balanced）。放宽到 5% —— BTC top-20 ≤ 1% imbalance 几乎不出现，1% 下该分支基本死代码
```

**Median 分边取**：阈值 `3× median` 中的 median **分边独立计算** —— bid 的 concentrated 阈值以 bid-side top-N median 为基准，ask 同理。理由：不对称深度下混排 median 偏向厚侧，会在薄侧漏掉集中挂单或误报。

**超过 10 条时的截断策略**：先按 `amount` 降序排序所有过阈值的 levels（bids + asks 混排），取 top-10，再按 "bid 在前 / ask 在后；**bids 组内按 price 降序（距 mid 近→远）、asks 组内按 price 升序（距 mid 近→远）**" 恢复展示顺序（即 nearest-to-mid first，与示例输出一致）。理由：保"找大单"意图不变形（size 越大越值得看），展示顺序以"距当前价的距离"递增，agent 读时先看最近的墙。

**Bid share 计算**：`total_sum = total_bid + total_ask`，分三态输出：
- `total_bid == 0 and total_ask > 0`：`"Bid share: 0% (asks only, no bids in top 20)"` — 极端冷盘单边
- `total_ask == 0 and total_bid > 0`：`"Bid share: 100% (bids only, no asks in top 20)"` — 同上反向
- `total_sum == 0`：整体降级（等同 empty order book，走 §2.1 空数据三态）
- 正常：`bid_share_pct = total_bid / total_sum * 100`，`bid_ratio = total_bid / total_ask`，输出 `f"Bid share: {bid_share:.1f}% (bid : ask = {bid_ratio:.2f} : 1)"`；当 `abs(bid_share - 50) < ORDER_BOOK_BALANCED_THRESHOLD_PCT` 时退化为 `"Bid share: ~50% (balanced)"`

理由：`N% heavier` 在中间区（如 bid:ask=10:5 得 50% heavier）语义歧义，`share%` + `ratio` 无歧义；分母除零必须显式三态降级，不能 raise。

**mid price 定义**：`(best_bid + best_ask) / 2`，concentrated levels 距离百分比相对 mid。

### 2.2 `get_recent_trades(window_seconds=300)`

**Signature**: `async def get_recent_trades(deps: TradingDeps, window_seconds: int = 300) -> str`

**输出示例**：

```
=== Recent Trades (BTC/USDT:USDT, last 300s, 5 × 60s buckets) ===
  t-5min  buy 0.31 / sell 0.45  (net -0.14)
  t-4min  buy 0.52 / sell 0.28  (net +0.24)
  t-3min  buy 0.82 / sell 0.35  (net +0.47)
  t-2min  buy 1.24 / sell 0.41  (net +0.83)
  t-1min  buy 0.95 / sell 0.52  (net +0.43)
Total: buy 3.84 / sell 2.01 (net +1.83, 66% taker buy)
Trade count: 847 | Avg size: 0.0069 BTC
```

**三态**：
- 数据：如上
- 空数据（`trades == []`，极端冷盘）：`f"Recent trades ({symbol}): no trades in last {window_seconds}s"`
- 服务失败：`f"Recent trades ({symbol}): temporarily unavailable"`

**关键常量**：

```python
RECENT_TRADES_WINDOW_DEFAULT = 300   # 默认窗口 5 分钟
RECENT_TRADES_BUCKET_COUNT = 5       # 固定 5 个桶（不参数化，避免过度工程）
RECENT_TRADES_MAX_FETCH = 500        # OKX `/api/v5/market/trades` 单次上限 = 500（CCXT 不做自动分页）；plan 阶段 verify `ccxt.okx.describe()['limits']['fetchTrades']['max']` 确认后定稿。若 BTC 高活跃 5min 实际成交 > 500，partial coverage 降级会触发（见下），agent 能识别覆盖不全的事实，不静默掩盖
```

**Partial coverage 降级**：用两个自然语义变量做双条件判断：

```python
fetch_ratio = n / RECENT_TRADES_MAX_FETCH          # 拉回量 / 上限，接近 1.0 = 被 limit 截断
oldest_age_ratio = oldest_age_ms / (window_ms)     # 最老 trade age / 窗口长，接近 1.0 = 覆盖到老端

is_partial = fetch_ratio >= 0.95 and oldest_age_ratio < 0.95
```

**判断语义**：`fetch_ratio >= 0.95`（拉到上限）**AND** `oldest_age_ratio < 0.95`（最老仍不够老）→ 窗口老端被截断。Partial 时输出行尾追加 `" (partial coverage: {n} trades fetched at limit, oldest age {oldest_age}s ({oldest_age_ratio:.0%} of window), window not fully covered — consider smaller window or higher limit)"`，并在 Total 行标注 `net +1.83*` + 脚注 `"* partial coverage"`。

**为什么双条件**：极冷盘拉回 20 条 trade 最老 age=30s，单看 `oldest_age_ratio=0.1 < 0.95` 会误报 partial；加 `fetch_ratio >= 0.95`（冷盘 = 20/500 = 0.04）后冷盘不触发，真正被 limit 截断的热市场（`fetch_ratio ≈ 1.0` 且 `oldest_age_ratio < 0.95`）才触发。

**数量精度规则**（跨 symbol 自适应）：示例 BTC 用 4 位小数（`0.0069 BTC`）。通用规则：精度取决于 `market['precision']['amount']`（CCXT unified，OKX BTC swap = 4）。若该字段不可得，按 symbol 推断：BTC/ETH → 4 位，其他 alt → 2-4 位（取 `round(log10(1/avg_size)) + 1` 启发式）。spec 不写死，实施时从 `ccxt.markets[symbol]["precision"]["amount"]` 动态取。

**Bucket 计算**：
- `bucket_duration = window_seconds / BUCKET_COUNT`
- 每个 trade 按 `(now_ms - trade_ts_ms) / 1000 / bucket_duration` 定位 bucket index（0 = 最老，4 = 最新）
- Bucket label: `t-5min` / `t-4min` / ... 使用 `bucket_index → (BUCKET_COUNT - i) * bucket_duration_minutes` 反查
- 窗口 = 300s 时精确整分钟；非 300s 时退化为 `t-Xmin` 近似显示（精度够，细节见 §4）

**Taker buy/sell 定义**：CCXT `fetch_trades` 返回的每条 trade 有 `side: 'buy' | 'sell'` 字段，语义为 **taker 方向**（CCXT unified spec）。buy = taker 吃 ask，sell = taker 吃 bid。

### 2.3 `get_multi_timeframe_snapshot(tfs=None)`

**Signature**: `async def get_multi_timeframe_snapshot(deps: TradingDeps, tfs: list[str] | None = None) -> str`

默认 `tfs = ["5m", "1h", "4h", "1d"]`（在函数内设置，不用可变 default argument）。

**输出示例**：

```
=== Multi-TF Snapshot (BTC/USDT:USDT) ===
Current price: 64200.00
Columns: Momentum (price vs primary MA) | Structure (MA alignment) | Volatility (ATR as % of price) | Range pos (position within 20-bar high-low, 0%=low / 100%=high)

5m:  +0.3% vs MA20  | MA20 above MA50   | ATR 0.13%  | range pos 45%
1h:  +0.8% vs MA50  | MA50 above MA200  | ATR 0.65%  | range pos 72%
4h:  +2.1% vs MA50  | MA50 above MA200  | ATR 1.87%  | range pos 85%
1d:  -1.5% vs MA50  | MA50 below MA200  | ATR 3.74%  | range pos 32%
```

**Primary MA 映射**（模块常量，不暴露）：

```python
MULTI_TF_PRIMARY_MA = {"5m": 20, "1h": 50, "4h": 50, "1d": 50, "1w": 50, "1M": 50}
# Primary MA 原则：每 TF 的"特征趋势 MA" = 结构对的**快边**。5m 结构是 (20, 50) → primary=20（快边）；
# 1h/4h/1d 结构是 (50, 200) → primary=50（快边）。
# 1w/1M 特例：结构降级到 (20, 50)，primary 本应按快边原则 = 20，但**保持 50 是为了与 1h/4h/1d 输出行 momentum 列的语义一致**
# （agent 读"+X.X% vs MA50"跨 TF 无需脑内切换基准 MA）。1M limit=60 刚够 MA50（OKX BTC swap 2019 上线至今约 60+ 根月线），
# 属可用但边缘 TF。
MULTI_TF_STRUCTURE_MAS = {
    "5m": (20, 50),    # 5m 的 MA200 = 1000min ≈ 16.7h 跨度过长，对 5m 决策价值弱；用 MA20 vs MA50
    "1h": (50, 200),
    "4h": (50, 200),
    "1d": (50, 200),
    "1w": (20, 50),    # 1w × 200 = 约 4 年，OKX BTC swap 历史不足；降级到 (20, 50) 覆盖 ~1 年
    "1M": (20, 50),    # 1M × 200 = 约 17 年，远超 OKX BTC swap 上线时间（2019）；降级到 (20, 50) 覆盖 ~20 个月
}
MULTI_TF_RANGE_PERIODS = 20          # 20-bar 高低点
MULTI_TF_OHLCV_LIMIT = {              # 按 TF 细分
    "5m": 80,        # MA50 + 30 冗余
    "1h": 250,       # MA200 + 50 冗余，对齐 HTF
    "4h": 250,
    "1d": 250,
    "1w": 60,        # 结构 MA 降级到 (20, 50)，limit 相应缩到 60；OKX BTC swap 历史容量内
    "1M": 60,        # 同上
}
```

**MA 计算方案**：`TechnicalAnalysisService.compute_indicators()` 当前只返回 `ma_20` / `ma_50`（`src/services/technical.py:43-55`），**不返回 MA100/MA200**。本工具 inline 用 `df["close"].rolling(n).mean().iloc[-1]` 现算所需 MA，复用 HTF pattern（`tools_perception.py:644-648`）—— 本轮**不扩展** `compute_indicators`（会波及所有调用方的返回 dict 键，触发 drift 测试连锁，不划算）。

**与 primary MA 的一致性**：5m 的 primary MA 是 MA20（结构对 `(MA20, MA50)` 的快边）—— 短 TF 看短周期结构。1h/4h/1d 的 primary 是 MA50（结构对 `(MA50, MA200)` 的快边），读法对齐 HTF。1w/1M 的 primary 是 MA50（结构对 `(MA20, MA50)` 的慢边，特例 — 见 dict 注释）。

**1w / 1M 定位**：因 OKX BTC swap 历史有限（2019 年上线），1w 需要的 MA200 = 4 年历史 / 1M 需要的 MA200 = 17 年历史几乎必然 `insufficient data`。本轮把 1w/1M 的结构 MAs **降级到 `(MA20, MA50)`**，limit 缩到 60。**primary MA 统一保持 MA50**（1w/1M 的慢边），与 1h/4h/1d 读法对齐。1M 的 limit=60 刚够 MA50（OKX BTC swap 2019 上线至今约 60+ 根月线），属**可用但边缘**。**默认 `tfs=["5m","1h","4h","1d"]` 不含 1w/1M**，agent 可主动传入；观察期若发现 1w/1M 被调用频繁且信号价值高，follow-up 决定是否保留此类 TF 支持。

**1w/1M 渲染的显式 structure 标注**：1w/1M 的 structure 列用的是 `(MA20, MA50)` 而非 1h/4h/1d 的 `(MA50, MA200)`。为避免 agent 误读为"所有 TF 用同一套结构 MA"，1w/1M 行的 structure 列末尾加前缀 `[short-structure]` 或后缀 `(MA20 vs MA50)` 显式标注。例如：
```
1h: +0.8% vs MA50  | MA50 above MA200                 | ATR 0.65% | range pos 72%
1w: -2.3% vs MA50  | MA20 above MA50 (short-structure)| ATR 4.12% | range pos 65%
```

**三态（逐 TF 独立）**：
- 某 TF 数据不足（candles < `MULTI_TF_STRUCTURE_MAS[tf][1]` 条无法算慢 MA — 5m/1w/1M 需 ≥50，1h/4h/1d 需 ≥200，按各自 dict 实际值）：该行渲染为 `"{tf}: insufficient data (need {slow_ma} candles, got {n})"`
- 某 TF 服务失败：该行渲染为 `"{tf}: temporarily unavailable"`
- 全部 TF 失败：整个工具返回单行 `"Multi-TF snapshot ({symbol}): temporarily unavailable"`
- 策略：**逐 TF 独立降级**（参考 `MacroService` 多源 pattern），任一 TF 失败不级联

**Structure 字段渲染**（`MA{fast}` vs `MA{slow}` 按 `MULTI_TF_STRUCTURE_MAS[tf]` 取）：
- `MA{fast} > MA{slow}`: `"MA{fast} above MA{slow}"`（例：5m `"MA20 above MA50"`、1h `"MA50 above MA200"`）
- `MA{fast} < MA{slow}`: `"MA{fast} below MA{slow}"`
- 差距 < 0.1%: `"MA{fast} at MA{slow}"`（事实：MA 纠缠）

**Momentum 字段**：`"{sign}X.X% vs MA{N}"`，sign 强制 `+/-`（复用现有 `{:+.1f}%` 格式）

**Range 字段**：`range pos {pct:.0f}%`（复用 HTF range position 算法，20-bar 而非 100-bar；"pos" 避免被误读为 range width/宽度）

### 2.4 `get_position` 增强（#5 + #6）

**Signature 不变**：`async def get_position(deps: TradingDeps, symbol: str | None = None) -> str`

**原输出**（现有，不变）：

```
Current Position:
  LONG 0.01 contracts @ 64000.00 | 3x leverage
  PnL: 10.00 USDT (+0.10% of initial capital)
  Liquidation: 55000.00 (14.1% away)
  Duration: 2h 15m
```

**新输出**（Sim 语义示例 — `contracts=0.01 BTC × contract_size=1.0`；OKX 语义下等价的是 `contracts=1 张 × contract_size=0.01 BTC`，Notional 数值一致）：

```
Current Position:
  LONG 0.01 contracts @ 64000.00 | 3x leverage
  PnL: 10.00 USDT (+0.10% of initial capital)
  Duration: 2h 15m

Risk exposure:
  Notional value: 640.00 USDT (6.4% of equity 10010.00)
  Margin used: 213.33 USDT (2.1% of equity, from balance.used_usdt)
  Liquidation: 55000.00 (14.1% away = 7.3× ATR(1h))

Exit orders:
  Stop loss:   62000.00 (3.1% below entry, 4.7% below current = 2.4× ATR(1h))
  Take profit: 68000.00 (6.3% above entry, 5.9% above current = 3.0× ATR(1h))
```

**子决策（已与用户确认）**：

| 子决策 | 选择 |
|--------|------|
| ATR 用哪个 TF | **固定 1h**（行业最普适波动基准，跨场景可比） |
| SL/TP 距离展示 | **同时给距 entry + 距 current**（entry 用于评估 R:R，current 用于动态管仓） |
| 多 SL / 多 TP | 按价格排序全部列出，每条附 `[contracts]` 契约量 |
| 无 SL/TP | **显式提示 `"Stop loss: not set"` / `"Take profit: not set"`**（裸仓风险显式化） |

**Risk exposure 字段来源**：
- `contract_size` = 由 `BaseExchange.get_contract_size(symbol)` helper 提供（见 §3.2）：OKX 读 `market['contractSize']`（BTC/USDT:USDT swap = 0.01），Sim 返回 1.0（Sim 的 `_Position.contracts` 已是 BTC 数量单位，见 `simulated.py:103-105`，不走合约张数模型）
- `Notional value` = `contracts * entry_price * contract_size`
- `equity` = `balance.total_usdt`（**CCXT `fetch_balance` 的 total 已实时含 unrealized**；Sim `simulated.py:138` 同样把 unrealized 并入 total_usdt。再加 `unrealized_pnl` 会**双计**）
- `Margin used` = **`balance.used_usdt`**（直接读交易所真实值 —— `Balance` dataclass 已有此字段见 `base.py:48`，`fetch_balance` 在 OKX / Sim 两端都已填充，无需改 dataclass / mock；**不用** `notional / leverage` 简化算式 —— OKX 有 tier-based initial margin 差异，简化算式在大仓位下偏差显著）
- `Liquidation ATR 倍数` = `liquidation_distance_pct / atr_pct_1h`

**SL/TP 识别逻辑**：
- 调用 `exchange.fetch_open_orders(symbol)` 拿到所有 open orders
- 过滤 `order.order_type in ("stop", "take_profit")` 且 `order.symbol == position.symbol`
- 按 order_type 分组并按 `price` 排序
- **注意**：`tools_execution.py:167` 已在用 `o.order_type == "take_profit"` 识别，pattern 复用

**实盘 / Sim 差异（本轮现状）**：
- **Sim**：`simulated.py:48` 原生支持 `order_type in ("stop", "take_profit")`，本轮 `get_position` SL/TP 区块正常工作
- **OKX 实盘**：`OKXExchange._parse_order`（`okx.py:321`）直接 `order_type=data["type"]` 透传，OKX algo 单 raw `type` 不是 `"stop"` / `"take_profit"` 而是 `"conditional"` / `"trigger"` 等 — **本轮实盘 SL/TP 识别会失败**，`get_position` 在实盘上 "Exit orders" 区块会渲染 `"Stop loss: not set"`（裸仓提示误报）
- **Iter 2b 解锁实盘**：`sandboxMode` 配置化（`okx.py:85-92` 目前无此配置、`.env.example` 无 demo 账户字段）+ `_parse_order` algo 归一化 + `get_open_orders` OCO 合并展示 — 作为独立 PR 做，实盘接入前必须完成
- **本轮删掉的内容（原 "Pre-work Gate" / "OCO cancel 语义" / "归一化影响面"）**：全部随 algo 归一化延后到 Iter 2b 时再讨论。当时的讨论要点（unified vs raw info 层键选择、OCO 拆两条 vs 一条带双 trigger、cancel 原子性、trigger_price 写入 `Order.price`、`_TRIGGER_REASON_MAP` 同步）已记入 memory `project_iter2b_okx_algo_normalization` 待 Iter 2b session 直接取用

**三态**（仅 ATR 获取失败时）：
- ATR(1h) 可获取：完整输出
- ATR(1h) 不可获取（1h OHLCV 数据不足 / 服务失败）：
  - Risk exposure 里 `Liquidation: 55000.00 (14.1% away)` 省略 ATR 倍数尾部
  - Exit orders 里每条省略 ` = X.Xx ATR(1h)` 尾部
  - 不整体降级为 "unavailable"（position 主要信息仍有价值）

---

## 3. 架构

### 3.1 数据层 / 渲染层分层

**严格分层**（保持未来调整成本低）：

```
CCXT OKX API
    ↓
BaseExchange.fetch_order_book()    → OrderBook dataclass
BaseExchange.fetch_trades()        → list[Trade]
BaseExchange.get_contract_size()   → float   (OKX 读内存 markets；Sim 固定 1.0)
    ↓
MarketDataService.get_order_book()    (薄包装，无缓存)
MarketDataService.get_recent_trades() (薄包装，无缓存)
    ↓
tools_perception.get_order_book()         (渲染：聚合/format/三态)
tools_perception.get_recent_trades()      (渲染：分桶/format/三态)
tools_perception.get_multi_timeframe_snapshot()   (渲染：组合 get_ohlcv + technical)
tools_perception.get_position()           (渲染：增强 — 拉 balance/orders/atr/position，Notional 算式用 get_contract_size)
```

**备注**：`get_contract_size` 走 `exchange` 层**直通**（`tools_perception → deps.exchange.get_contract_size`），不经 `MarketDataService` —— 合约乘数不属市场数据范畴，是 exchange 特定事实。

**原则**：
- 渲染层只消费 dataclass，不碰 CCXT raw dict
- 所有阈值/常量顶到模块级（§2 各小节已列）
- 格式化字符串集中到辅助函数（`_format_depth_line`, `_format_mtf_row` 等），便于调整

### 3.2 `BaseExchange` 扩展

新增 dataclass（`src/integrations/exchange/base.py`）：

```python
@dataclass
class OrderBookLevel:
    price: float
    amount: float  # base-currency 量

@dataclass
class OrderBook:
    symbol: str
    bids: list[OrderBookLevel]  # 按价格降序（best 在前）
    asks: list[OrderBookLevel]  # 按价格升序（best 在前）
    timestamp: int | None  # CCXT fetch_order_book 在部分交易所/网络条件下可能返回 None；OKX 实现层 fallback `int(time.time() * 1000)` 或保留 None 由 tool 层处理

@dataclass
class Trade:
    timestamp: int  # ms
    side: str       # "buy" | "sell"（taker 方向，CCXT unified spec）
    price: float
    amount: float   # base-currency
    trade_id: str | None  # CCXT fetch_trades 可能返回 int / 缺失；实现时：raw_id = raw.get("id"); trade_id = str(raw_id) if raw_id is not None else None（避免 str(None) == "None" 非空字符串坑）
```

新增抽象方法：

```python
@abstractmethod
async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook: ...

@abstractmethod
async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]: ...

@abstractmethod
async def get_contract_size(self, symbol: str) -> float:
    """合约乘数。OKX BTC swap = 0.01 BTC/张；Sim = 1.0（contracts 已是 BTC 数量单位）。"""
    ...
```

**`get_contract_size` 实现**：
- `OKXExchange.get_contract_size`：读 `self._client.markets[symbol]["contractSize"]` 内存字段。要求 `self._client.markets` 已加载 —— 在 `OKXExchange.__init__` 中 **不能** 同步调用 `load_markets`（async），改为在**现有** `OKXExchange.start()`（`okx.py:115` 已实现，现做 WebSocket 启动）**头部追加** `await self._client.load_markets()` 预加载一次。`get_contract_size` 正常路径纯内存读；若 `markets` 未加载（测试 mock / 未调 start() 场景）fallback 到懒加载 `await self._client.load_markets()`
- `SimulatedExchange.get_contract_size`：直接 `return 1.0`（无合约张数模型，`_Position.contracts` 就是 BTC 数量）

### 3.3 ATR 跨 TF 获取策略 + 并行化（`get_position` 增强 + `get_multi_timeframe_snapshot`）

**跨 TF ATR 获取**：`TechnicalAnalysisService.compute_indicators()` 返回 `atr_14`（MA50/MA200 需 inline rolling，见 §2.3）。对每个 TF 独立调用 `exchange.fetch_ohlcv(symbol, tf, limit=MULTI_TF_OHLCV_LIMIT[tf])` + `technical.compute_indicators(df)` 即可。`limit` 按 §2.3 的 per-TF dict 取值（`5m=80`、`1h/4h/1d=250`、`1w/1M=60`）。**`get_position` 的 ATR(1h) 独立用 `limit=50`**（ATR(14) 只需 ~30 根即可稳定，50 给 20 根冗余；不与 snapshot 共享避免跨工具耦合）。

**`get_multi_timeframe_snapshot` 的 fetch 并行化**：4 个 TF 顺序 fetch → 串行耗时 ~2s。用 `asyncio.gather(..., return_exceptions=True)` 并行化到 ~0.5s。错误隔离靠 gather 的 `return_exceptions=True`，逐 TF 独立处理（见 §2.3 三态）。

**`get_position` 的并行化（两段式）**：增强后该工具需要 5 次 IO：`exchange.fetch_positions` + `market_data.get_ticker` + `exchange.fetch_balance` + `exchange.fetch_ohlcv(1h, limit=50)` + `exchange.fetch_open_orders`（`get_contract_size` 是内存读，不计 IO — 见 §3.2 preload 约定）。

**两段式拍板（推荐）**：
1. 先 `await exchange.fetch_positions(symbol)` 拿到持仓；若为空直接 return `"No open positions."`（现有 `tools_perception.py:117` 行为，保留）
2. 有持仓时用 `asyncio.gather(return_exceptions=True)` 并行其余 4 次 IO

理由：多数 agent cycle 场景（开仓前 check、无仓监控）会命中"无仓"分支 —— 两段式在该分支只做 1 次 IO（~100ms）而不是 5 次浪费；有仓场景多 1 RTT（~100ms 串行）对 15 分钟 cycle 不敏感。

**时延预期**：无仓分支 ~100ms；有仓分支 先 1 RTT ~100ms + 4 次并行 ~150ms = **~250ms**（原 spec 单段全并行的 ~150ms 估计偏乐观，两段式更诚实）。ATR(1h) 取失败降级见 §2.4。

**Ticker 层级一致性**：现有 `get_position`（`tools_perception.py:132`）走 `deps.market_data.get_ticker`（MarketDataService 层），**本轮保留不变**。**注**：`market_data.py:18-19` 的 `get_ticker` 实际是**无缓存直通包装**（MarketDataService 上唯一 cache 是 `_DERIVATIVES_TTL` for funding/OI/LSR），所以保留现调用并非为了 cache 一致性，只是**不折腾已稳定工具的调用层**。其他 4 个 IO 走 `deps.exchange.*` 直通（functional 上与 market_data 包装等价）。若未来 market_data 引入 ticker 级 cache，再评估一致性影响。

**`@_retry` 参数（order_book / trades 专用重载）**：默认 `@_retry(max_retries=3, base_delay=1.0)` 最坏退避 1+2+4=7s 对"高时效数据"太长——订单簿/成交流失败时宁可快速 fallback 到 three-state `"temporarily unavailable"`，让 agent 继续 cycle。`OKXExchange.fetch_order_book` 和 `fetch_trades` 用 **`@_retry(max_retries=2, base_delay=0.5)`**（最坏 0.5+1=1.5s），其他 retry 复用现有默认。

### 3.4 Three-state 契约

参考 PR C §3.5 pattern，每个工具自己 try/except + 空值判断：

```python
try:
    ob = await deps.market_data.get_order_book(symbol, depth)
except Exception:
    logger.exception(f"get_order_book failed for {symbol}")
    return f"Order book ({symbol}): temporarily unavailable"

if not ob.bids or not ob.asks or min(len(ob.bids), len(ob.asks)) < depth:
    actual = min(len(ob.bids), len(ob.asks))
    return f"Order book ({symbol}): insufficient data (requested depth {depth}, got {actual})"

# ... render ...
```

**日志**：用 `logger.exception()` 带 stacktrace，保留调试能力。

### 3.5 Fact-only 守门

**允许的事实描述词**（不违反 fact-only）：
- `above / below / at` （位置关系）
- `concentrated`（密度事实 — 不是 "wall"）
- `cumulative / bid share / ratio`（聚合事实 —— `imbalance` / `heavier` 已被 I3 替换）
- `trend up/down/flat` **禁用** — 改用 `MA20 above MA50` 等关系式表达

**禁词（regression 测试扫描所有新工具输出）**：

```python
# 情绪/标签类单词：PR #18 (N5) 已清理的核心清单 + 本轮增量
FACT_ONLY_BANNED_WORDS_RE = [
    # PR #18 N5 clean-up 核心情绪标签
    r"\bwall\b",
    r"\baggressive\b",
    r"\bbullish\b",
    r"\bbearish\b",
    r"\boverbought\b",
    r"\boversold\b",
    r"\bdry powder\b",
    r"\brisk[- ]on\b",
    r"\brisk[- ]off\b",
    r"\bbull market\b",
    r"\bbear market\b",
    # 本轮增量（主观价格动作描述词 — 除 Momentum 列标题的特殊豁免见下）
    r"\bpressure\b",
    r"\brally\b",
    r"\bplunge\b",
    r"\bsurge\b",
    r"\bcrash\b",
    r"\bpump\b",
    r"\bdump\b",
]
# 组合词禁词：显式 phrase 匹配（避免 "support" 单独出现误伤）
FACT_ONLY_BANNED_PHRASES_RE = [
    r"\bstrong support\b",
    r"\bstrong resistance\b",
    r"\bweak support\b",
    r"\bweak resistance\b",
    r"\btrend\s+(up|down|flat)\b",  # "trend up/down/flat" — 允许词段声明"禁用"，在这里被 regex 守门
]
```

**豁免规则（拍板）**：`get_multi_timeframe_snapshot` 的列标题 `"Columns: Momentum (price vs primary MA) | ..."` 包含 `Momentum`，中性术语不禁。扫描实现**跳过以 `Columns:` 开头的 header 行**（testing 代码 `[line for line in output.splitlines() if not line.startswith("Columns:")]` 后再 regex），不走"regex 白名单 Momentum"方案（后者难扩展：新工具加新标题要不断增白名单）。

**扫描实现**：测试用 `re.IGNORECASE + re.search(pattern, output)`，任一命中 → fail，报错信息包含命中的 regex 和 output 片段。未来若发现新禁词，扩展本清单即可（同时加对应测试样例）。

**测试**：`tests/test_fact_only_wordlist.py` 对 3 新工具 + 增强 `get_position` 每个至少跑 **3-4 个场景**（典型 + 两端 + 降级行），grep 合并输出字符串，命中禁词 → fail。理由：单"典型输出"扫描覆盖度有限，边缘分支（极端 imbalance、全买/全卖、MA 纠缠、三态降级）可能走入不同 format 路径，禁词要在所有路径都被守门。

**每工具场景清单**：
- `get_order_book`：典型 / bid 远厚于 ask 的 imbalance 极端 / 无 concentrated levels / temporarily unavailable 降级
- `get_recent_trades`：典型 / 全 taker buy / 全 taker sell / 冷盘 0 trades 降级
- `get_multi_timeframe_snapshot`：典型 / MA 纠缠（`at`）/ 某 TF insufficient（逐 TF 独立）/ 全部 TF failure 整体降级
- `get_position` 增强：典型 / 无 SL 无 TP 裸仓 / 多 TP 分批 / ATR(1h) 不可用尾部省略

---

## 4. 实现细节

### 4.1 `get_recent_trades` 时间桶精度

**CCXT 排序约定**：`fetch_trades` 按 CCXT unified spec **默认升序（最老在前）**，但**不保证交易所实现一致**。`OKXExchange.fetch_trades` 返回前**必须显式排序** `sorted(trades, key=lambda t: t.timestamp)`（升序），调用方不依赖默认顺序。Bucket 算法对顺序无要求（基于 `age_ms` 定位 bucket index），但显式排序提升可读性。

Bucket 分配算法：

```python
now_ms = int(time.time() * 1000)
bucket_duration_ms = window_seconds * 1000 // BUCKET_COUNT
for trade in trades:
    age_ms = now_ms - trade.timestamp
    if age_ms >= window_seconds * 1000:  # 严格 >= 防止边界 trade 的 bucket_idx 变成 -1
        continue  # 超窗（含正好等于窗口边界的）
    bucket_idx = BUCKET_COUNT - 1 - (age_ms // bucket_duration_ms)
    buckets[bucket_idx].append(trade)
```

> **边界 bug 防护**：若写成 `>` 严格大于，当 `age_ms == window_seconds * 1000`（如 300000ms）时：condition `300000 > 300000 = False` 不过滤；`bucket_idx = 4 - (300000 // 60000) = 4 - 5 = -1`；Python 负索引 `buckets[-1] = buckets[4]`（最新桶）—— 5 分钟前的 trade 被静默放进 t-1min 桶。`>=` 覆盖等边界。

**Label 生成**：`window_seconds == 300` 时 label = `t-5min` / `t-4min` / ... / `t-1min`（整分钟）。其他 `window_seconds` 时 label = `bucket {i+1}/5 ({start_s}-{end_s}s ago)` 降级显示 — 保证正确性。

### 4.2 `get_contract_size` 实现

**架构选择**（方案 a — 审查建议）：合约乘数归属 **exchange 抽象层**（而非 tools_perception 渲染层）。理由：Notional 是 exchange 特定事实（OKX 有张数概念，Sim 没有），exchange 比渲染层更清楚；渲染层只读不算，职责清晰。不修改共享 `Position` dataclass（API 稳定性）。

**`OKXExchange.get_contract_size(symbol)`**：
```python
async def get_contract_size(self, symbol: str) -> float:
    if not self._client.markets:
        await self._client.load_markets()  # fallback，正常 start() 已 preload
    market = self._client.markets.get(symbol)
    if market is None:
        logger.warning("Market %s not loaded, defaulting contract_size=1.0", symbol)
        return 1.0
    return float(market.get("contractSize", 1.0))
```

BTC/USDT:USDT swap 返回 0.01（OKX 规格）。Preload 时机：`OKXExchange.start()` override，见 §3.2。

**并发安全**：多个 `asyncio.gather` 并行调用同时命中 `self._client.markets is None`（首次懒加载场景）时，CCXT 的 `async_support.Exchange.load_markets()` 内部有 memoization / `_market_lock`，保证只会触发一次真实 HTTP 调用，其余 awaiter 共享结果。spec 不需额外加锁。**plan 阶段 verify**：确认当前项目锁定的 CCXT 版本（`requirements.txt` / `pyproject.toml`）里 `async_support.Exchange.load_markets` 仍有 memoization（grep `_markets_loading` 或类似）；若版本变更移除该行为，fallback 到 spec 自加 `asyncio.Lock`。

**`SimulatedExchange.get_contract_size(symbol)`**：
```python
async def get_contract_size(self, symbol: str) -> float:
    return 1.0  # Sim 的 _Position.contracts 就是 BTC 数量（simulated.py:103-105），无张数模型
```

**实盘与 Sim Notional 一致性**：
- 实盘：`contracts(张)=1.0 × price(64000) × contract_size(0.01) = 640 USDT`
- Sim ：`contracts(BTC)=0.01 × price(64000) × contract_size(1.0) = 640 USDT`
- Agent 看到的 Notional 数字语义一致，`_Position.contracts` 单位差异（张 vs BTC）对 agent 透明

### 4.3 SimulatedExchange 合成策略

Sim 没有真实 order book / taker trade stream，但必须实现 `BaseExchange` 抽象方法（合同完整性）。采用**最小合成**：

- `fetch_order_book(symbol, depth)`：基于 `ticker` 合成桩数据 —— best bid = ticker.bid、best ask = ticker.ask、从 best 价按 ±0.01% 步长生成 `depth` 档，每档 amount = `0.01 * (1 + depth_idx * 0.1)` BTC。不反映真实流动性，仅保证结构可读、三态测试可覆盖。
- `fetch_trades(symbol, limit)`：**带方向偏置的合成**（SimExchange 没有 `_fills` / `_trade_history` 结构，grep 零匹配；DB 里的 `SimOrder` 记录只是订单而非成交流）。基于 ticker 中间价 ± 0.02% 随机扰动生成 20-50 条 trade，每条 amount 0.001-0.01 BTC，时间戳按 `now_ms - random(0, window_ms)` 均匀分布。
  - **SimExchange 字段补齐**：当前只有 `self._latest_ticker`（`simulated.py:72`）无 prev。本轮为 SimExchange 新增 `self._prev_ticker: Ticker | None = None` 字段，在 `_latest_ticker` 每次更新前先把旧值写入 `_prev_ticker`（见 `simulated.py:583` 的 ticker update point）。成本小（一行赋值），用于 `fetch_trades` 的方向偏置计算。
  - **taker buy/sell 方向**：不用 50/50 纯随机（这样 agent 读 `get_recent_trades` 永远看到 `net ≈ 0` 学不到 taker bias 决策模式）。改为按 **prev → latest bid 变化**带弱偏置：`price_change_pct = (latest.bid - prev.bid) / prev.bid` if `prev_ticker` 存在，else `0`（首次调用退化到 50/50）。`buy_prob = 0.5 + clip(price_change_pct * 20, -0.15, 0.15)`（例如涨 0.5% → buy_prob = 60%），每条 trade 按此概率抽取方向。
  - **随机模块**：使用 Python 标准库 `random` 模块（`random.random()` / `random.uniform()` / `random.choices()`），**不用** `secrets` / `os.urandom`。这样测试侧可 `random.seed(42)` 固定随机种子做确定性断言（见 §5.1）。
  - **`clip` 实现**：Python 无内置 `clip()`，用 `max(-0.15, min(0.15, price_change_pct * 20))`（或 `numpy.clip` 若项目已依赖 numpy）。spec 写 `clip(...)` 是伪代码表述，实施时按前者展开。
  - 这样 Sim 下观察期仍能看到弱但真实的 taker flow 信号（价格上涨时 taker 偏 buy），提升观察期数据含金量。
  - 保证结构可读、三态测试可覆盖；不反映真实大户流向（因为没有真实 order flow 数据源）。

**Rationale**：观察期早期主要验证"agent 能否正确调工具 + 解析输出"，不验证"agent 基于真实 order book 做流动性决策"。真实 order book 感知等上实盘（见 §8.3）。

### 4.4 `persona.py` Layer 1 追加（不动其他）

**追加锚点**：`persona.py._build_layer1()` 返回的 Markdown 字符串中，`## Tool Usage Notes` 段当前已有 19 个 bullet（N3 `stablecoin supply` bullet 为最后一条 —— grep `^- \*\*` 得 19，与 memory `project_n7_layer1_organization` "N3 后 Layer 1 19 bullet" 一致），本轮 4 条新 bullet **追加到该段末尾**，紧跟在现最后一个 bullet（`- **Stablecoin supply**: ...`）之后，与现有缩进 / 格式完全一致。不创建新段（N7 议题留 Iter 4 做 final 分组重组）。

**bullet 数演进**：Iter 2 后 **19 → 23**；Iter 3（`get_price_pivots`）后 **23 → 24**。Iter 2 后 bullet 数即达到 N7 触发阈值（≥23），但 N7 的**实质触发条件是"观察期发现 agent 忽略靠后工具"而非纯计数**（见 memory `project_n7_layer1_organization`）—— 本轮明牌 Iter 3 仍按计划在 N7 之前，观察期数据会在 Iter 3 后共同评估 N7 必要性。

Layer 1 末尾追加 4 条 bullet（N7 议题留 Iter 4 做 final 重组，本轮只追加）：

```
- Call get_order_book when evaluating liquidity, slippage risk, or concentrated levels near current price.
- Call get_recent_trades to read taker-flow bias and rhythm over recent minutes (default 300s, 5 × 60s buckets).
- Call get_multi_timeframe_snapshot once per cycle to scan multi-TF alignment (default 5m/1h/4h/1d) before committing to a direction.
- Use get_position to see risk exposure (notional / margin / liquidation and SL/TP distances expressed in ATR(1h) multiples — 1h is the fixed baseline regardless of session trading style) — useful both when opening and during ongoing position management.
```

（实际文案可能微调，以最终 spec review 为准。）

### 4.5 `@agent.tool` docstring 格式约束（plan 阶段填内容，spec 敲结构）

3 新工具的 docstring（pydantic-ai 读取作为 LLM tool-selection 的一级入口）**结构约束**：

1. **首行 one-liner**（≤ 80 字符）：动宾短语描述"做什么"，不含主观词（"show / return / report"，不是"analyze / evaluate"）
2. **参数段**：每个参数一行 `Args: name (type): description + default value`
3. **返回段**：`Returns: str` + 一句话说明格式（"multi-line fact-only text; see spec §2.X"）
4. **三态提示**（必含）：`Degradation: ...` 一句话列出三态字符串模板，便于 LLM 预期不同场景输出
5. **禁用**：长篇交易语境解释（Layer 1 bullet 已承担）；"when to call"（避免偏引导 agent 决策）

**示例骨架**（plan 阶段填具体内容）：

```python
async def get_order_book(deps: TradingDeps, depth: int = 20) -> str:
    """Return top-N order book depth with concentrated-level breakdown.

    Args:
        depth: Levels per side to fetch. Default 20.

    Returns:
        str: Multi-line fact-only text (best bid/ask + cumulative depth + bid share +
        concentrated levels). See spec §2.1.

    Degradation: Returns "Order book ({symbol}): insufficient data" if book is empty;
    "Order book ({symbol}): temporarily unavailable" on service failure.
    """
```

---

## 5. 测试策略

### 5.1 Exchange 层（~10 测试）

**`tests/test_exchange_order_book.py`**：
- OKX `fetch_order_book` — mock `ccxt.okx.fetch_order_book` 返回典型/空/异常，断言 `OrderBook` 结构
- OKX `fetch_trades` — 同上
- SimulatedExchange `fetch_order_book` — 按 §4.3 的合成策略（基于 ticker ± 0.01% 步长），验证 OrderBook 结构 + depth 边界
- SimulatedExchange `fetch_trades` — 按 §4.3 合成策略（带方向偏置），验证 Trade 结构 + 窗口边界 + **taker 方向偏置生效**。**Flake 防护**：用 `random.seed(42)` 固定随机种子 + 跑 **N=100 轮累计断言**（feed 连续 N 轮上涨 ticker，累计 `sum(buy.amount) > sum(sell.amount)` 且偏置比 ≥ 55%；N 轮下跌同理反向）。单轮 20-50 trade 抽样 buy_prob=60% 会 flake，累计 100 轮大数定律稳定；seed 防止 CI 偶发失败
- `@_retry` 装饰器在 order_book / trades 上生效（复用 `tests/test_exchange.py` 现有 retry 测试 pattern；非 Iter 1 — PR #21 是 metrics enabler 与 retry 无关）
- **`get_contract_size` 三场景**（约 +3 测试）：
  - 正常路径：mock `markets[symbol] = {"contractSize": 0.01}` → 返回 0.01
  - 懒加载 fallback：mock `markets = {}` + `load_markets` 被 await → 返回加载后的值
  - 市场缺失：mock `markets = {"OTHER/USDT": ...}`（symbol 不在）→ 返回 1.0 fallback + 触发 `logger.warning`
  - Sim 对应：断言 `SimulatedExchange.get_contract_size(any_symbol)` 始终返回 1.0

### 5.2 工具渲染层（~25 测试）

**`tests/test_toolkit_iter2.py`**：

- `get_order_book` — 典型场景 / 仅 bids / 空 order book / concentrated levels 数量 == 0 / 数量 > 10 截断 / bid share 三态（bid 重 / ask 重 / balanced）/ service failure 降级
- `get_recent_trades` — 典型 / 冷盘（0 trades）/ window_seconds != 300 的 label 降级 / service failure / 全主动买场景 / 全主动卖场景
- `get_multi_timeframe_snapshot` — 典型 / 某 TF insufficient / 某 TF failure（逐 TF 独立降级）/ 全部 TF failure / 自定义 tfs 参数 / `MA50 at MA200` 纠缠场景
- `get_position` 增强 — 典型（有 SL + TP）/ 无 SL（裸止损风险）/ 无 TP / 多个 TP（部分止盈）/ ATR(1h) 不可用降级 / 无持仓（保留原有 "No open position" 输出）

### 5.3 Fact-only regression（~15 测试）

**`tests/test_fact_only_wordlist.py`**：按 §3.5 "每工具 3-4 场景清单"（典型 + 两端 + 降级行）跑 mock，grep 合并输出字符串扫描禁词，命中 → fail。3 新工具 + `get_position` 增强 ≈ 4 × 3-4 = ~15 测试。

### 5.4 Mock 数据策略

- **Exchange 层**：mock CCXT 级别（`ccxt.okx.fetch_order_book` 等），返回 CCXT unified 格式 dict
- **工具渲染层**：mock service 级别（`MarketDataService.get_order_book` 返回 `OrderBook` dataclass），follow 现有 `test_perception_tools_n3.py` 的 `MockDeps` 模式
- **SimulatedExchange**：新增 `fetch_order_book` / `fetch_trades` 可以返回"最小合成数据"—— 对真实模拟交易需求价值有限（Sim 没有真 order book），但保证 `BaseExchange` 抽象合同不破。测试时允许 Sim 返回固定桩数据。

### 5.5 集成测试

不新增 end-to-end 集成测试（工具渲染覆盖足够）。`test_trader_agent.py` 的 `test_registered_tool_names_matches_agent_tools` 已在 §1.3 列入改动清单（硬编码 26 → 29 必修），更新后 **drift 防护照常运作**。

---

## 6. Acceptance Criteria

- [ ] `BaseExchange` 新增 `fetch_order_book` / `fetch_trades` / `get_contract_size` 抽象方法
- [ ] `OKXExchange` / `SimulatedExchange` 分别实现，CCXT mock 测试通过
- [ ] `OKXExchange.start()` 头部追加 `await self._client.load_markets()` 预加载（不影响现有 WebSocket 启动）
- [ ] `OKXExchange._parse_order` **本轮不动**（algo 归一化延后 Iter 2b，见 memory `project_iter2b_okx_algo_normalization`）
- [ ] 3 个新 `@agent.tool` 注册完毕（order_book / recent_trades / multi_timeframe_snapshot）
- [ ] `REGISTERED_TOOL_NAMES` = 29，`test_trader_agent.py:84-85` 硬编码 26 已更新到 29（drift 防护测试通过）
- [ ] `get_position` 输出包含 Risk exposure + Exit orders 区块，两段式 IO（先 positions，有仓再 gather 其余 4 次），无仓分支 ~100ms / 有仓分支 ~250ms
- [ ] `get_position` 的 Notional 在 OKX 与 Sim 下数值一致（§4.2 一致性段验证）
- [ ] `get_position` Sim 下 SL/TP 正常识别；OKX 实盘下"Stop loss: not set"是**已知局限**（Iter 2b 解锁）
- [ ] ToolCallRecorder 自动包装 3 个新工具（验证一条 integration test）
- [ ] Three-state 契约在所有新/增强工具生效（数据 / 空 / 服务失败三场景测试）
- [ ] Fact-only regression 每工具 3-4 场景均通过禁词扫描（禁词列表见 §3.5，场景清单见 §5.3）
- [ ] Layer 1 prompt 追加 4 bullet（3 新工具 + 1 get_position 增强说明）且 `test_persona.py` 无破坏
- [ ] 测试总数 681 → ~725（约 +45，原 +50 -5：algo 归一化测试移到 Iter 2b）
- [ ] `pytest` 全绿、零 regression

---

## 7. 观察期 Follow-up 候选

本轮按朴素设计上线，以下演进路径留观察期数据驱动：

| Follow-up | 触发条件 |
|-----------|---------|
| **大单识别加入 `get_recent_trades`** | 观察期发现 agent 在"判断大户操纵"场景推理盲目，且 `get_order_book` 的 concentrated levels 不足以替代 |
| **`concentration_threshold` 调参**（3× → 5× 或按 % of top-20 total）| 观察期发现 concentrated levels 总是空或总是过多 |
| **`get_multi_timeframe_snapshot` 加 volume ratio 或 range 规格调整** | 观察期发现 4 栏不够用，agent 额外调 `get_market_data` 补信息 |
| **`get_order_book` 改分桶聚合** | 观察期发现 "concentrated levels" 不如区间聚合好读 |
| **ATR TF 改成 session 主 TF / agent 可参数化** | 观察期发现 1h ATR 跨 session 风格差异大 |
| **`get_position` 加 R:R 比 / 预期 profit loss** | 观察期发现 agent 自己算 R:R 频繁、易错 |
| **1h OHLCV `TTLCache(ttl=10s)`** | **已知冗余**（非观察期才能验证）：`get_position`（`limit=50`）+ `get_multi_timeframe_snapshot`（1h 行 `limit=250`）同 cycle 内各拉一次 1h OHLCV，因 limit 不同不能 naive 共享。本轮不做 cache 是为简化（共享状态 + 并发安全需额外设计）。触发条件改：观察期若判定 token / IO 成本不可接受，做 limit-aware TTLCache（大 limit 命中可复用前 N 根） |
| **partial order book depth** | 观察期若发现 OKX 偶尔返回 < 请求 depth（如请求 20 档只返 15 档，流动性差的时段），spec 现判 `insufficient data` 硬降级 —— 15 档仍有信息价值。follow-up 改为展示可用档数 + 加注 `(partial depth: {actual}/{requested})` |

记入 memory `project_observation_period_metrics_review_checklist` 更新时纳入。

---

## 8. 风险与 Trade-off

### 8.1 真实 OKX `fetch_trades` rate limit

OKX `GET /api/v5/market/trades` rate limit 为 100 次/2s（高于 `fetch_ticker` 的 20 次/2s）。本轮 agent cycle 每 15 分钟一次，单 cycle 最多调用 `get_recent_trades` 1-2 次，远在 limit 内。多 session 并发（未来）时仍充裕。**无需加 cache / throttle**。

### 8.2 `get_multi_timeframe_snapshot` 并行 fetch 风险

`asyncio.gather()` 同时发 4 次 `fetch_ohlcv`。OKX 限制是单连接 20 次/2s，安全。`@_retry` 装饰器在单 TF 失败时重试 3 次，不阻塞其他 TF（`return_exceptions=True`）。

### 8.3 SimulatedExchange 的 order book 不真实

SimExchange 没有真实订单簿。`fetch_order_book` 返回合成桩数据（best bid/ask 基于 ticker ± spread），这对"agent 读订单簿做流动性决策"的观察价值**低**。

**缓解**：
- 观察期早期先跑 SimExchange，主要验证 agent 是否正确调用工具 + 输出可解析
- 真实 order_book 感知的观察等上实盘（OKX 真实账户）
- 本轮 `SimulatedExchange.fetch_order_book` 仅为"合同完整性"实现，不投入复杂模拟逻辑

### 8.4 "Concentrated levels" 的 fact-only 边界

`"concentrated"` 是**密度事实**，不是"wall"（"wall" 暗示"强支撑/阻力"）。但若观察期发现 agent 仍把 `Concentrated levels` 当作"关键位"误用（例如在其上设止损 SL），应评估是否在 prompt 端加警示 — **非本轮治理**，记为观察期候选议题。

### 8.5 `OKXExchange.start()` 新增 `load_markets()` fail-fast 风险

§3.2 要求 `start()` 头部追加 `await self._client.load_markets()` 预加载。若首次启动时 REST 调用失败（API key 错 / 限流 / 网络故障），`start()` 会抛出异常**阻止整个 exchange 启动** —— 而当前 `start()` 的 WebSocket 初始化失败只是降级到 REST-only。这是**新增的 fail-fast 失败路径**。

**缓解策略**（plan 阶段拍板 try 位置）：
- 推荐：`load_markets()` 放在 WebSocket try **外部**先执行，失败 fail-fast 抛出（markets 未加载后续所有工具都会坏掉，早失败比晚失败好）
- 备选：放 try 内 + fallback 到 `get_contract_size` 首次调用时的懒加载（`§3.2` 已有 fallback 逻辑兜底）

观察期早期可能触发（若 OKX API key 配置错误），应在日志 / 启动告警中明显报错。

### 8.6 `get_position` 增强对现有调用的影响

现有 `get_position` 输出长度约 6 行，增强后约 12 行（+ Risk exposure 4 行 + Exit orders 2-4 行）。Token 成本翻倍但信息密度翻倍。如果观察期发现 agent cycle 频繁调用此工具导致 token 爆涨，考虑：
- 拆出 `get_risk_exposure` 独立工具（YAGNI 暂不做）
- 通过 prompt 引导"已知风险状态时不要重复调 get_position"
- **SL/TP 3 种距离度量简化退路**：当前每条 SL/TP 给"距 entry / 距 current / ATR 倍数"三项事实（示例里差异 1.6 pct）。若观察期 token 压力明显，退路是只保留"距 current + ATR 倍数"（砍掉距 entry，agent 可从 entry_price 自己算），信息密度保留 2/3，token 省 ~30%

---

## 9. 规模与节奏

- 预计实施 **2 天**（brainstorming 已完成 / writing-plans 下一步；原 2.5 天 -0.5 天：algo 归一化延后 Iter 2b）
- 单 PR landed（不拆分）— 3 新工具 + 1 增强主题一致（"扫环境 + 管仓"），独立 PR 过碎
- **Iter 2b（独立 PR，本轮不做）**：`sandboxMode` 配置化 + `_parse_order` algo 归一化 + `get_open_orders` OCO 合并展示 — 详细 scope 与讨论要点见 memory `project_iter2b_okx_algo_normalization`。实盘接入前必须完成
- 若 `#4 multi_tf_snapshot` 实施中发现 snapshot vs HTF 字段边界分歧升级（目前无此迹象），退出条件：拆 `#4` 到独立 PR，保 `#5/#6/#1/#3` 作为 2a
- 合并后切下一 session 进 Iter 3（`get_price_pivots`）
