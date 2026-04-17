# N2: 市场信息面工具增强 — 设计文档

## 0. 背景

### 0.1 项目概述

TradeBot 是一个 LLM 驱动的加密货币自动交易系统。Agent（Claude）通过工具调用感知市场、管理仓位、做出交易决策，在 USDT 保证金永续合约上自主交易。

核心运行循环：Agent 每 15 分钟被唤醒一次（也可被订单成交、价格警报等事件提前唤醒），通过工具获取市场数据和账户状态，分析后决定是否操作。

### 0.2 当前架构

**System Prompt 三层结构：**
- **Layer 1**（身份 + 工具引导）— Agent 是谁、市场上下文（永续合约、单向持仓）、每个工具的使用场景和注意事项
- **Layer 2**（思维框架）— 通用交易分析维度：市场结构、信号确认、风险回报、仓位管理、自我复盘
- **Layer 3**（人格 + 策略，均可选）— 注入交易风格（conservative/moderate/aggressive）和策略偏好（trend_following/swing/breakout）

**现有工具（19 个）：**

| 类别 | 工具 | 说明 |
|------|------|------|
| 感知（8） | `get_market_data` | K 线 + 技术指标 + ticker（~1000-1200 tokens） |
| | `get_position` | 当前持仓 + PnL + 清算距离 |
| | `get_account_balance` | 账户余额 + 收益率 |
| | `get_open_orders` | 挂单列表 + 与当前价距离 |
| | `get_trade_journal` | 交易记录 + 绩效摘要 |
| | `get_memories` | 长期记忆（教训、模式、复盘） |
| | `get_active_alerts` | 警报配置 |
| | `get_performance` | 详细绩效统计 |
| 执行（10） | `open_position`, `close_position`, `set_stop_loss`, `set_take_profit`, `adjust_leverage`, `place_limit_order`, `cancel_order`, `set_price_alert`, `add_price_level_alert`, `set_next_wake` | 订单管理 + 警报 + 唤醒间隔 |
| 记忆（1） | `save_memory` | 保存经验到长期记忆 |

**服务分层：**

```
Agent Tool (thin wrapper)
  → tools_perception.py / tools_execution.py (实现)
    → MarketDataService (市场数据)    → BaseExchange (抽象层)
    → MemoryService (记忆)              ├─ OKXExchange (真实交易; ccxt.async_support REST + ccxt.pro WebSocket)
    → MetricsService (绩效)             └─ SimulatedExchange (模拟交易; ccxt.pro 实时行情)
```

`SimulatedExchange` 模拟的是账户状态（仓位、余额、订单撮合），但使用的市场数据（ticker、K 线）来自真实 OKX 行情。即：模拟交易环境下 Agent 看到的价格是真实的，只是订单不会真的提交到交易所。

**配置体系：**
- `config/settings.yaml` — 交易所、交易对、模型路由、调度间隔、预算、审批等
- `config/trader.yaml` — 人格和策略偏好
- 环境变量 — API key 等敏感信息（通过 `.env` + `load_dotenv` 加载）

### 0.3 为什么做这个迭代

当前 Agent 的**感知能力有一个关键盲区**：它只能看到技术面数据（K 线、指标），完全看不到信息面。这意味着：

- 不知道有重大新闻事件（如监管公告、交易所安全事件），可能在市场恐慌时逆势开仓
- 不知道 FOMC / CPI 等宏观事件即将发生，可能在高波动窗口贸然操作
- 做永续合约却看不到 funding rate / OI / 多空比，缺少衍生品交易的核心决策指标
- 不知道交易所维护停机或合约参数变更，可能做出灾难性决策

这个盲区的优先级排序来自与专业交易员的讨论，按 "Agent 不知道就会犯的错误严重程度" 排列。

---

## 1. 目标

为交易 Agent 增加三个感知工具，覆盖当前缺失的关键信息维度：

| 工具 | 定位 | 解决的问题 |
|------|------|-----------|
| `get_market_news()` | 了解市场叙事 | Agent 不了解新闻事件和市场情绪，无法判断基本面 |
| `get_critical_alerts()` | 下单前扫雷 | Agent 不知道即将发生的宏观事件或交易所公告，可能在高风险窗口贸然操作 |
| `get_derivatives_data()` | 衍生品市场微观结构 | Agent 做永续合约却看不到 funding rate / OI / 多空比，缺少核心决策指标 |

**本迭代不做：**
- 全文分析（token 成本太高，仅使用标题 + 元数据）
- 社交媒体情绪（Twitter/Reddit — 需付费 API 或爬虫）
- 链上指标（交易所净流入等 — 免费 API 限制多，P1 暂缓）
- BTC 相关性参考（看 Agent 实际表现再决定）
- 新闻触发自动交易（新闻仅作为输入之一，由 Agent 自行决策）

---

## 2. 数据源

### 2.1 CoinDesk Data News API（新闻聚合 + 情绪 + 分类）

> 历史背景：最初选型用 CryptoPanic，但 2026-04-01 起 CryptoPanic 免费 Developer tier 已下线（需付费）。迁移至 CoinDesk Data News API（前身 CCData / CryptoCompare，2024-10 被 CoinDesk 收购并重品牌）。字段更丰富、无需 key、响应结构更规整。

| 项目 | 详情 |
|------|------|
| 端点 | `GET https://data-api.coindesk.com/news/v1/article/list` |
| 认证 | **无需认证**（公开端点，也无需 key 注册） |
| 免费额度 | 无明显限额（pre-work 10 次连发均 200，响应 body 无 RateLimit 字段，HTTP header 无 X-RateLimit-*） |
| 语言过滤 | `lang=EN` |
| 情绪过滤 | `sentiment=POSITIVE\|NEGATIVE\|NEUTRAL`（每篇文章已标注情绪） |
| 分类过滤 | `categories=BTC`（按 `CATEGORY_DATA[].NAME` 匹配） |
| 条数 | `limit=20`（可更高） |
| 响应顶层 | `{ Data: [...], Err: {} }` |
| 每条字段 | `ID`, `GUID`, `PUBLISHED_ON`(Unix 秒), `TITLE`, `URL`, `BODY`(全文，本项目不使用), `KEYWORDS`, `LANG`, `SENTIMENT`, `SCORE`, `UPVOTES`, `DOWNVOTES`, `SOURCE_DATA.NAME`(原始媒体名), `CATEGORY_DATA[].NAME`(分类数组，字符串如 "BTC"/"ETH"/"MARKET") |
| 延迟 | 近实时（所有字段实时填充） |
| **稳定性** | **高** — CoinDesk 正式产品，文档在 `developers.coindesk.com`，2024 年起合并 CCData/CryptoPanic 基础设施 |

**相对 CryptoPanic 的关键差异：**
- 字段名**全大写**（`TITLE` 而非 `title`）
- `PUBLISHED_ON` 是 Unix 秒时间戳，不是 ISO8601 字符串
- `CATEGORY_DATA` 是对象数组（需 `.NAME` 提取），不是 `{code}` 结构
- **无 `trending` 对应**：`SCORE` 字段目前始终为 0，排序用不起来；`news_filter` 简化为 3 值（见 §3.1）
- 无 API key = 无配额保护负担 = 实现层简化

### 2.2 Alternative.me Fear & Greed Index

| 项目 | 详情 |
|------|------|
| 端点 | `GET https://api.alternative.me/fng/` |
| 认证 | 无需认证 |
| 免费额度 | 无限制 |
| 响应格式 | `{ data: [{ value: "23", value_classification: "Extreme Fear" }] }` |
| 更新频率 | 每 12-24 小时 |
| **稳定性** | **高** — 运营 5+ 年，极简端点，社区广泛使用 |

### 2.3 ForexFactory 经济日历（via nfs.faireconomy.media）

| 项目 | 详情 |
|------|------|
| 端点 | `GET https://nfs.faireconomy.media/ff_calendar_thisweek.json` |
| 认证 | 无需认证 |
| 免费额度 | 每 5 分钟 2 次请求 |
| 响应格式 | JSON 数组，含 `title`, `country`, `date`(ISO8601 带时区偏移，如 `"2026-04-14T08:30:00-04:00"`), `impact`(High/Medium/Low), `forecast`, `previous`（预测/前值 pre-work 确认都是字符串，可能为空字符串 `""`） |
| 覆盖范围 | 当周所有经济事件（全球）。本地过滤规则：`country="USD"` + `impact` 为 `"High"` 或 `"Medium"`。仅保留美国经济事件，因为加密市场与美元政策高度相关（FOMC、CPI、NFP、PPI、GDP 等） |
| 已知限制 | 仅包含当前一周数据。周五晚间 `lookahead_hours=12` 可能跨入下周，但数据中无下周事件，存在假阴性风险（如漏掉下周一/二的 FOMC）。实际影响有限——高影响力经济事件不在周末发生，且 Agent 下一个工作日会自动获取新一周数据 |
| **稳定性** | **中低** — 非官方 feed，无文档/SLA，可能随时改格式。但已稳定运行多年，被众多开源项目使用 |

### 2.4 OKX 公告与系统状态（已验证）

需要两个端点配合覆盖 "业务公告 + 维护停机"：

**端点 A：业务公告** — `GET https://www.okx.com/api/v5/support/announcements`

| 项目 | 详情 |
|------|------|
| 认证 | 无需认证 |
| 参数 | `annType`（字符串，如 `"announcements-delistings"`）、`page`（页码，每页 20 条） |
| 响应字段 | `title`, `url`, `annType`, `pTime`(发布时间戳 ms), `businessPTime`(生效时间戳 ms) |
| 注意 | 无正文内容——只有标题和链接，符合我们只展示标题的输出格式 |
| 分类查询 | `GET /api/v5/support/announcement-types` 返回可用的 `annType` 值 |
| 已知分类 | `announcements-new-listings`(新上币)、`announcements-delistings`(下币)、`trading-updates-us-aus`(交易规则变更，合约参数变更归此类) |

**端点 B：系统维护** — `GET https://www.okx.com/api/v5/system/status`

| 项目 | 详情 |
|------|------|
| 认证 | 无需认证 |
| 参数 | `state`（`scheduled` / `ongoing` / `completed` / `canceled`） |
| 响应字段（基于 OKX 文档） | `title`, `state`, `begin`(开始时间 ms), `end`(结束时间 ms), `maintType`, `serviceType`, `system` |
| 用途 | 计划内维护停机、进行中故障——与业务公告互补 |
| **⚠️ Schema 待验证** | Pre-work P4 探测时 `state=scheduled`/`ongoing` 返回空数组（当前没有排期维护），无法 100% 确认嵌套结构。**Task 3 实现前建议用 `state=completed` 拉历史数据验证**（历史维护一定有记录）——若发现像 `/support/announcements` 一样嵌套成 `data[0].details[*]`，parser 需同步调整 |

**共同特性：**

| 项目 | 详情 |
|------|------|
| 速率限制 | 无显式 header，但激进轮询触发 `50011 Too Many Requests`。安全默认值 ~5 req/s |
| **稳定性** | **中** — 非标准化，OKX 可能改版。`/api/v5/public/announcements` 已确认不存在（404） |

**过滤策略：**
- 端点 A：使用 `annType` 参数过滤，仅拉取 `announcements-delistings` 和 `trading-updates-us-aus`（跳过 `announcements-new-listings`，新上币对现有交易币种无直接影响）
- 端点 B：拉取 `state=scheduled` 和 `state=ongoing`，仅保留影响交易功能的维护事件

### 2.5 ccxt 衍生品数据

| 项目 | 详情 |
|------|------|
| 方法 | `fetch_funding_rate(symbol)`, `fetch_open_interest(symbol)`, `fetch_long_short_ratio_history(symbol, timeframe, since, limit)` |
| 认证 | 无需认证（公开市场数据端点） |
| 数据 | funding rate（当前费率 + 下次结算时间）、OI（全市场持仓量）、多空比（底层调 `/api/v5/rubik/stat/contracts/long-short-account-ratio-contract`） |
| 注意 | ccxt OKX 的 `has.fetchLongShortRatio` 为 `False`，不支持单条查询；需使用 `fetch_long_short_ratio_history(symbol, "5m", limit=1)` 取最新一条。`fetch_funding_rate()` 仅返回当前费率，不含历史均值（v1 不做历史均值）。ccxt 返回的 `longShortRatio` 仅为比值（如 0.94），`long_ratio` / `short_ratio` 需在实现层推算：`long_ratio = ratio / (1 + ratio)`，`short_ratio = 1 / (1 + ratio)` |
| **稳定性** | **高** — 官方交易所 API 的标准封装，ccxt 库活跃维护 |

### 2.6 未选方案及理由

| 数据源 | 未选理由 |
|--------|---------|
| CryptoPanic | 2026-04-01 起免费 Developer tier 下线；付费 tier 对个人项目不划算 |
| TradingEconomics | 免费版仅返回 ~3 条事件，不可用 |
| Investing.com | 无 API |
| Finnhub 经济日历 | 需付费 |
| NewsAPI.org | 免费版延迟 24 小时，禁止商业用途 |
| LunarCrush / Santiment | API 访问需付费 |

---

## 3. 工具设计

### 3.1 `get_market_news` — 新闻 + 情绪

```python
@agent.tool
async def get_market_news(
    ctx: RunContext[TradingDeps],
    news_filter: Literal["positive", "negative", "neutral"] | None = None,
) -> str:
    """Get recent crypto news headlines and market sentiment.
    news_filter: 'positive', 'negative', 'neutral'. Default: no filter (latest mix).
    Returns up to 10 headlines (up to 5 symbol-specific, remainder general crypto) + Fear & Greed Index.
    Output ~500-700 tokens."""
```

**参数：**
- `news_filter`（可选）：按情绪过滤——`positive` / `negative` / `neutral`（映射到 CoinDesk `sentiment` 参数）。默认不过滤。避免使用 Python 内建名 `filter`。
- 币种从 `deps.symbol` 自动提取（如 `BTC/USDT:USDT` → `BTC`）。

**新闻范围：目标 10 条 = 5 币种相关 + 5 通用**

单次 API 调用不带 `categories` 过滤、`limit=20`，返回后在本地按 `CATEGORY_DATA[].NAME` 是否包含交易币种分为两组，各取 top 5。好处：
- 只用 1 次 API 调用
- 币种相关新闻确保 Agent 不漏关键事件
- 通用新闻捕捉宏观事件（如监管政策、市场恐慌）

**补齐规则（非对称，仅正向）：**
- 若币种相关 < 5 条，用通用新闻补齐到总数 10 条
- 若通用 < 5 条（极罕见），**不反向**用币种新闻补齐——总数可能 < 10。理由：反向补齐会放大 symbol-specific 内容占比、改变 Agent 看到的"通用 vs 定向"配比语义，不划算

**为何 FGI 搭车而非独立工具：**
- FGI 仅 ~20 tokens，附带成本可忽略
- Agent 一次调用即获完整宏观视图
- 不值得为一个数字单独占一个 tool call

**实现方式：** 工具层对 `NewsService.get_news()` 和 `NewsService.get_fear_greed_index()` 使用 `asyncio.gather(..., return_exceptions=True)` 并行调用——两者的上游、缓存条目、HTTP client 都独立。串行的话，新闻慢时 FGI 跟着陪跑；并行能把 wall-clock 压到 max(news, fgi) 而不是 sum。与 §3.2 / §3.3 的并行策略一致。

**输出格式：**

```
=== Fear & Greed Index ===
Value: 23 / 100 — Extreme Fear
(Updated: 2026-04-16)

=== Symbol News (BTC, 5) ===
[2026-04-16 14:30] SEC Approves New Bitcoin ETF Options Trading
  Source: CoinDesk | Currencies: BTC

[2026-04-16 13:15] Bitcoin Breaks $90K as Institutional Inflows Surge
  Source: CoinTelegraph | Currencies: BTC, ETH

[2026-04-16 12:00] Major Bitcoin Mining Difficulty Adjustment Coming
  Source: Decrypt | Currencies: BTC

[2026-04-16 11:00] MicroStrategy Adds 5,000 BTC to Holdings
  Source: Bloomberg | Currencies: BTC

[2026-04-16 10:00] Bitcoin Hash Rate Hits All-Time High
  Source: The Block | Currencies: BTC

=== General Crypto News (5) ===
[2026-04-16 12:30] Federal Reserve Signals Rate Cut Timeline
  Source: Reuters | Currencies: BTC, ETH

[2026-04-16 11:45] EU Passes Comprehensive Crypto Regulation Framework
  Source: CoinDesk | Currencies: —

[2026-04-16 10:30] Tether Mints $1B USDT on Ethereum
  Source: Whale Alert | Currencies: USDT, ETH

[2026-04-16 09:00] Binance Announces Fee Restructuring for Futures
  Source: CoinTelegraph | Currencies: BNB

[2026-04-16 08:00] South Korea Central Bank Explores CBDC Pilot
  Source: CryptoSlate | Currencies: —
```

**Token 估算：** ~500-700 tokens（10 条标题各 ~50 tokens + FGI ~30 tokens + 标题/页脚 ~70 tokens）

**时区约定：** 所有输出的 `[YYYY-MM-DD HH:MM]` 时间戳均为 **UTC**（与 `get_market_data` 一致）。`InformationEvent.timestamp` 是带时区的 `datetime`，tool 层统一用 `.strftime("%Y-%m-%d %H:%M")` 格式化；不显示 "UTC" 后缀以节省 tokens，但约定不变。

### 3.2 `get_critical_alerts` — 交易所公告 + 宏观事件

```python
@agent.tool
async def get_critical_alerts(
    ctx: RunContext[TradingDeps],
    lookback_hours: int = 24,
    lookahead_hours: int = 12,
) -> str:
    """Get critical alerts: exchange announcements and upcoming macro events.
    lookback_hours: how far back to check announcements (default 24h).
    lookahead_hours: how far ahead to check macro events (default 12h).
    Output ~100-400 tokens (often empty when no relevant events are scheduled)."""
```

**参数：**
- `lookback_hours`：回看多久的交易所公告（默认 24h）
- `lookahead_hours`：前瞻多久的宏观事件（默认 12h）

**实现方式：** 工具层内部对 `NewsService.get_announcements()` 和 `NewsService.get_macro_events()` 使用 `asyncio.gather(..., return_exceptions=True)` 并行调用——两者的上游、缓存条目都独立，没有串行的必要。NewsService 内部各方法已做 per-source 降级（某上游失败返空列表），`gather` 的 `return_exceptions` 仅作为防御性兜底。

**输出格式：**

```
=== Exchange Announcements (past 24h) ===
[2026-04-16 10:00] ETH/USDT contract maintenance scheduled 2026-04-17 02:00-04:00 UTC
[2026-04-15 18:30] Funding rate settlement interval changed to 4h for ETH perpetual

=== Upcoming Macro Events (next 12h) ===
[2026-04-16 18:00] FOMC Meeting Minutes — Impact: High
  Previous: N/A | Forecast: N/A
[2026-04-16 20:30] US Initial Jobless Claims — Impact: Medium
  Previous: 215K | Forecast: 220K

Note: macro calendar covers current week only; Friday evening / weekend calls may miss next week's early events.
```

末尾固定的 `Note:` 行让 Agent 始终知道日历作用域边界，而不必依赖上下文推断是否临近周末。**即使 macro events section 返回 "No upcoming macro events"，这行 Note 仍然保留**——它解释的是"为什么可能看不到事件"（日历只覆盖当前一周），这正是空结果需要的语境。大部分时候返回较短甚至为空，加上 ~30 tokens 的 Note 后 token 成本仍可忽略。

### 3.3 `get_derivatives_data` — 衍生品市场数据

```python
@agent.tool
async def get_derivatives_data(
    ctx: RunContext[TradingDeps],
    symbol: str | None = None,
) -> str:
    """Get derivatives market data: funding rate, open interest, long/short ratio.
    When symbol is None, uses deps.symbol (the currently traded pair).
    Output ~150-250 tokens."""
```

**设计决策：** derivatives 数据是全市场公开数据（无需 API key），不是账户数据。无论使用 SimulatedExchange 还是 OKXExchange，都直接从 OKX 获取真实数据。

**实现方式：** 与 `get_market_data` 走完全相同的数据路径——通过 BaseExchange 抽象层：
- `BaseExchange` 新增 `fetch_funding_rate()` / `fetch_open_interest()` / `fetch_long_short_ratio()` 三个抽象方法（`fetch_long_short_ratio` 保持简洁接口，实现层内部调用 ccxt 的 `fetch_long_short_ratio_history(symbol, "5m", limit=1)` 取最新一条）
- `OKXExchange` 用已有的 `self._client`（ccxt）实现
- `SimulatedExchange` 用已有的 `self._ccxt`（ccxt.pro）实现——和它的 `fetch_ohlcv()` 一样，读的是真实市场数据
- `MarketDataService` 通过 `self._exchange` 调用，和 `get_ticker()` / `get_ohlcv_dataframe()` 一致
- 衍生品数据缓存加在 `MarketDataService` 层（TTL 3 分钟，cache key 为 `symbol`），与 NewsService 的缓存同级
- **工具层并行**：`get_derivatives_data` 的三个 method（funding / OI / LSR）通过 `asyncio.gather(..., return_exceptions=True)` 并行调用——每个 method 走独立缓存/独立 ccxt 请求，无共享状态。串行实现会把 3× round-trip 延迟堆到用户侧；并行让 wall-clock 等于最慢那一路。partial failure（某个 method 抛异常）由 §3.4 的"单个 method 失败"分支处理

不引入额外的 ccxt 客户端，不破坏现有架构。

**输出格式：**

```
=== Derivatives Data (ETH/USDT:USDT) ===
Funding Rate: +0.0125% (next settlement in 3h 42m)
  Positive rate — longs pay shorts
Open Interest: $4.82B
Long/Short Ratio: 1.35 (57.4% long / 42.6% short)
Data as of: 2026-04-16 14:30 UTC
```

末尾 `Data as of:` 显示三个数据中**最旧**的 `timestamp`（Unix ms 转 UTC）。stale-fallback 生效时 Agent 可从时间戳判断数据陈旧，决定是否谨慎使用。

注意：v1 仅展示 `fetch_funding_rate()` 返回的当前费率，不包含历史均值。计算 8h 平均需调用 `fetch_funding_rate_history()`，工作量与边际价值不匹配，留待后续迭代。

**Token 估算：** ~160-270 tokens（+10 来自 timestamp 行）

### 3.4 优雅降级

工具分两组处理：**NewsService 组** (`get_market_news` + `get_critical_alerts`) 和 **衍生品组** (`get_derivatives_data`)。衍生品走 MarketDataService/BaseExchange，不依赖 NewsService。

**NewsService 组（`get_market_news` + `get_critical_alerts`）：**

| 故障场景 | 行为 |
|---------|------|
| `deps.news` 为 None（NewsService 未初始化） | 返回 "News service not configured" |
| 单个上游 API 不可用 / 超时 / 5xx | 该 section 返回 "temporarily unavailable"，其余 section 正常 |
| 该 tool 覆盖的所有上游 API 都不可用（`get_market_news`: CoinDesk + FGI；`get_critical_alerts`: OKX announcements + OKX status + ForexFactory） | 返回 "News/alerts services currently unavailable" |
| 任何上游 API 返回 HTTP 429 | `TTLCache` 将该条目 TTL 延长至 30min、返回 stale 缓存；若无缓存则 section 降级 |
| ForexFactory feed 格式变更 | `get_critical_alerts` 的 macro section 返回 "macro calendar unavailable"，公告 section 不受影响 |

**衍生品组（`get_derivatives_data`）：**

| 故障场景 | 行为 |
|---------|------|
| 单个 method 失败（funding / OI / LSR 之一） | 该行返回 "X temporarily unavailable"，其他两行正常 |
| 全部三个 method 失败 | 三行全部显示 "temporarily unavailable"（工具仍返回结构化输出，Agent 知道衍生品暂不可用） |
| ccxt 抛 `RateLimitExceeded` | 在 exchange 实现层转换为 `RateLimitHit`，由 `TTLCache` 做 stale-fallback（与 NewsService 组一致） |

**共用：**
- 所有 HTTP 调用 5 秒超时，快速失败
- 所有数据源均无需 API key，因此不存在"未配置 key"或"日配额耗尽"分支——显著简化降级

---

## 4. 数据模型

统一的 dataclass，不做持久化存储，仅规范内存中的数据结构。使用 `@dataclass` 而非 `BaseModel`，与代码库中所有 DTO（Ticker, Candle, Order, Balance, Position 等）风格一致（`BaseModel` 仅用于配置类）：

```python
@dataclass
class InformationEvent:
    """Unified data model for all market intelligence events.

    Per-source conventions (each tool section only formats one source so
    these are safe in practice):

    `timestamp` — the time a lookback/lookahead filter compares against:
      - coindesk         → article PUBLISHED_ON (Unix 秒 → UTC datetime)
      - alternative_me   → FGI timestamp (Unix 秒 → UTC datetime)
      - forexfactory     → event `date` (ISO8601 → UTC datetime)
      - okx_announcement → `pTime` (发布时间戳 ms；**不用 `businessPTime`**，lookback 语义是"最近发布"而非"生效时间")
      - okx_status       → `begin`(ms → UTC datetime)：真实维护开始时间，scheduled 事件在未来，
                           ongoing 事件在近过去。**NewsService.get_announcements 对 okx_status 不做
                           lookback 过滤**（OKX API 的 `state=scheduled|ongoing` 已限定"相关事件"），
                           所以未来 begin 不会被滤掉。begin=0 异常时回退 `datetime.now(UTC)`。

    `importance` (required Literal["low","medium","high"]) per source:
      - coindesk         → "medium"（CoinDesk API 无 impact 字段，统一中等）
      - alternative_me   → "low"（FGI 是数值指数，分 tier 价值有限）
      - forexfactory     → 直接映射：`impact == "High"` → "high"，否则 "medium"（已过滤掉 Low）
      - okx_announcement → "high"（我们只拉下币 + 交易规则变更，本质都是重要事件）
      - okx_status       → "high"（维护影响交易）

    `title` — 人类可读展示行：
      - coindesk         → 文章标题原样
      - alternative_me   → 合成串 `"{value} / 100 — {classification}"`（例 `"23 / 100 — Extreme Fear"`）
      - forexfactory     → 事件名（例 `"FOMC Meeting Minutes"`）
      - okx_announcement → 公告标题
      - okx_status       → `"{title} {begin:YYYY-MM-DD HH:MM}-{end:HH:MM} UTC"` 合成串

    `content` (source-specific free-form metadata; NOT article full text):
      - coindesk         → 原始媒体名（`SOURCE_DATA.NAME`，如 "CoinTelegraph"）
      - alternative_me   → classification 字符串（如 "Extreme Fear"，不含数值）
      - forexfactory     → `"Previous: X | Forecast: Y"`（供宏观事件 section 显示）
      - okx_announcement / okx_status → 空字符串

    `url`:
      - coindesk         → 文章 URL
      - okx_announcement → OKX 公告详情 URL
      - alternative_me / forexfactory / okx_status → 空串（无稳定 detail URL）

    `symbols` — 用于 `get_market_news` 的 "symbol-specific vs general" 分组匹配：
      - coindesk         → 保留 `CATEGORY_DATA[].NAME` 中**所有条目**（包括主题标签 MARKET/CRYPTOCURRENCY 等，
                           因为它们不影响 `base in symbols` 判断——base 只会是 BTC/ETH 这类 ticker）。
                           **显示层**由 tool formatter 过滤非币种标签（denylist：MARKET / MACROECONOMICS /
                           CRYPTOCURRENCY / FIAT / EXCHANGE / TRADING / REGULATION / BUSINESS / TECHNOLOGY /
                           ALTCOIN），保持 "Currencies: BTC, ETH" 干净输出
      - alternative_me / forexfactory / okx_announcement / okx_status → 空列表

    If a new tool ever renders mixed sources, add a dedicated field rather
    than overloading `content`.
    """
    timestamp: datetime
    source: str           # "coindesk" / "alternative_me" / "okx_announcement" / "okx_status" / "forexfactory"
    category: str         # "news" / "fgi" / "announcement" / "maintenance" / "macro_event"
    importance: Literal["low", "medium", "high"]
    title: str
    content: str = ""
    url: str = ""
    symbols: list[str] = field(default_factory=list)
```

后续加新数据源只需新增采集器，不需要改数据结构。

---

## 5. 实现方案

### 5.1 新增文件

```
src/integrations/news/
  __init__.py           # 新建包（空文件）
  coindesk.py           # CoinDesk Data News API 客户端
  fear_greed.py         # Alternative.me FGI 客户端
  calendar.py           # ForexFactory 经济日历客户端
  okx_announcements.py  # OKX 公告客户端（/api/v5/support/announcements）
  okx_status.py         # OKX 系统状态客户端（/api/v5/system/status）
  models.py             # InformationEvent 基类
  service.py            # NewsService（聚合所有数据源，含缓存）
src/utils/
  __init__.py           # 新建包（空文件）— src/utils/ 目录本次迭代新建
  cache.py              # TTLCache 共享缓存抽象
```

### 5.2 Service 设计

```python
class NewsService:
    def __init__(self, http: httpx.AsyncClient | None = None):
        # http 可注入（测试用），默认自建一个 AsyncClient
        self._http = http if http is not None else httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._cache = TTLCache()

    # get_market_news 使用
    async def get_news(self, symbol: str, news_filter: str | None, max_per_group: int = 5) -> tuple[list[InformationEvent], list[InformationEvent]] | None
        """API 请求 limit=20（不带 categories 过滤），本地按 CATEGORY_DATA 是否含交易币种分为两组各取 top max_per_group 条。返回 (symbol_news, general_news) 二元组，供 tool 分区格式化。上游完全不可用（RateLimitHit 无 stale cache 或其他异常）时返回 None，与 get_announcements / get_macro_events 的 §3.5 契约一致，让 tool 层能区分"安静窗口"和"outage"。

        缓存策略：缓存层存"未分组"的 posts，具体是**已 parse 为 `list[InformationEvent]`** 的结果（不是原始 JSON dict），cache hit 时零解析开销。cache key = `news:{news_filter}`，不含 symbol——symbol 分组在每次调用时基于当前 `symbol` 即时完成。这样 cache hit 不会返回陈旧的错误分组，符合 deps.symbol 是会话级固定值的假设，也为未来多 symbol 并行做好了准备。"""
    async def get_fear_greed_index(self) -> InformationEvent | None

    # get_critical_alerts 使用
    async def get_macro_events(self, lookahead_hours: int) -> list[InformationEvent] | None
    async def get_announcements(self, lookback_hours: int) -> list[InformationEvent] | None
        """聚合两个 OKX 端点：/support/announcements（下币、交易规则变更）+ /system/status（维护停机）。两者都失败时返回 None（§3.5），让 tool 层渲染"temporarily unavailable"；至少一个成功则返回 list（可能为空）。"""

    # 生命周期
    async def close(self) -> None:
        """关闭 httpx 客户端（仅当 __init__ 自建时；注入则由调用方管理）。"""
        if self._owns_http:
            await self._http.aclose()
```

**所有数据源均无需 API key**，因此：
- 没有配额计数器、没有日重置逻辑
- 没有 "未配置 key" 分支
- Wizard 无需为新闻功能做任何配置

**缓存策略：**

所有缓存为内存级别、进程级别，无需持久化。

**所有 cache key 都带来源前缀**，避免不同数据源在同一 `TTLCache` 里撞 key：

| 数据源 | Cache Key 格式 | 默认 TTL | 理由 |
|--------|--------------|---------|------|
| CoinDesk 新闻 | `news:{news_filter}`（如 `news:positive` / `news:None`） | 15 min | 与默认 cycle 间隔对齐。不同 `news_filter` 产生独立 cache 条目。Service 层接受小写 `positive/negative/neutral`，client 内 `.upper()` 转为 CoinDesk 的 `POSITIVE/NEGATIVE/NEUTRAL` 后作为 `?sentiment=` 透传 |
| FGI | `fgi`（固定 key） | 6 hours | 每日更新一次，高频请求无意义 |
| ForexFactory 宏观日历 | `macro_calendar`（固定 key） | 6 hours | 按周发布，拉取整周数据后本地按 `lookback/lookahead` 过滤 |
| OKX 公告 + 系统状态 | `okx_ann` / `okx_status`（2 个固定 key；每个 fetch 内部做 2 次 HTTP 合并——announcements 遍历 2 个 annTypes、status 遍历 2 个 states） | 10 min | 公告更新频率低；2 个 cache 条目粒度足够，不需要拆成 4 条 |
| Derivatives | `funding:{symbol}` / `oi:{symbol}` / `lsr:{symbol}`（3 个 key，每 symbol） | 3 min | 不同币种数据不同；funding rate 每 8h 结算一次，3min 缓存已足够 |

**`TTLCache` 接口（定义在 `src/utils/cache.py`）：**

```python
class RateLimitHit(Exception):
    """所有 client 把 HTTP 429 / ccxt.RateLimitExceeded 转成这个异常。"""


class TTLCache:
    async def get_or_fetch(
        self,
        key: str,
        default_ttl: float,  # seconds
        fetch_fn: Callable[[], Awaitable[T]],  # async callable, no args
    ) -> T: ...
    # Cache miss / expired → call fetch_fn; store (data, now, default_ttl).
    # RateLimitHit: 有 stale data 则延长 TTL 至 1800s 返 stale；否则再抛出。
    # 其他异常不捕获，透传给 caller。

    def get_stale(self, key: str) -> Any | None: ...
    # 无 TTL 检查，存在就返回；不存在返回 None。测试/降级用。
```

NewsService 和 MarketDataService 各自持有一个 `TTLCache` 实例。

**限流保护（Rate Limit Handling）：**

通过 `TTLCache.get_or_fetch` 统一处理，**前提是所有上游 rate-limit 异常都被规范化为 `RateLimitHit`**：

- **httpx 客户端**（CoinDesk / FGI / ForexFactory / OKX announcements / OKX status）：在 client 层检查 `resp.status_code == 429` → `raise RateLimitHit`。
- **ccxt 客户端**（BaseExchange 派生类的衍生品 method）：用 `try / except ccxt.RateLimitExceeded` 包住 ccxt 调用，转换为 `raise RateLimitHit(...) from e`。
- 所有数据源向 `TTLCache` 抛的都是 `RateLimitHit`，`TTLCache` 只认这一个异常类型。

`TTLCache.get_or_fetch` 捕获 `RateLimitHit` 后：
- 如果该 cache key 有过期 data → 延长 TTL 至 30 分钟、返回 stale data、记录 WARNING
- 无 stale data 时 → 再抛出，caller（NewsService 方法 / MarketDataService.get_*）降级为返回空 / 抛给 tool 层

其他错误（5xx / timeout / ccxt.NetworkError / ccxt.ExchangeNotAvailable）不经过 stale-cache 路径，直接降级——tool 层显示 "temporarily unavailable"。

衍生品数据不经过 NewsService，走 BaseExchange 抽象层（与 ticker / OHLCV 相同路径）：

```python
# src/integrations/exchange/base.py — 新增抽象方法 + 数据类
@dataclass
class FundingRate:
    symbol: str
    rate: float               # 当前费率
    next_funding_time: int     # 下次结算时间戳 (ms)
    timestamp: int

@dataclass
class OpenInterest:
    symbol: str
    open_interest: float      # base-currency amount (per ccxt unified `openInterestAmount`)
    open_interest_value: float # USD 价值
    timestamp: int

@dataclass
class LongShortRatio:
    symbol: str
    long_short_ratio: float   # API 原始值, e.g. 1.35
    long_ratio: float         # 衍生计算: ratio / (1 + ratio), e.g. 0.574
    short_ratio: float        # 衍生计算: 1 / (1 + ratio), e.g. 0.426
    timestamp: int

class BaseExchange(ABC):
    # 现有方法...

    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> FundingRate: ...

    @abstractmethod
    async def fetch_open_interest(self, symbol: str) -> OpenInterest: ...

    @abstractmethod
    async def fetch_long_short_ratio(self, symbol: str) -> LongShortRatio: ...

# src/integrations/market_data.py — 新增便捷方法（含缓存）
class MarketDataService:
    # 现有方法（不变）...

    def __init__(self, exchange: BaseExchange):
        self._exchange = exchange
        # 新增：衍生品数据缓存（TTL 3min，cache key = symbol）
        # 使用 src/utils/cache.py 中的 TTLCache 抽象，封装 (data, created_at, ttl) 三元组
        # 以及 429 时将 ttl 延长至 1800s 的逻辑。NewsService 也使用同一 TTLCache。
        self._derivatives_cache = TTLCache()

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        return await self._derivatives_cache.get_or_fetch(
            f"funding:{symbol}", 180,
            lambda: self._exchange.fetch_funding_rate(symbol))

    async def get_open_interest(self, symbol: str) -> OpenInterest:
        return await self._derivatives_cache.get_or_fetch(
            f"oi:{symbol}", 180,
            lambda: self._exchange.fetch_open_interest(symbol))

    async def get_long_short_ratio(self, symbol: str) -> LongShortRatio:
        return await self._derivatives_cache.get_or_fetch(
            f"lsr:{symbol}", 180,
            lambda: self._exchange.fetch_long_short_ratio(symbol))
```

**MarketDataService 角色变化：** 当前 `MarketDataService` 是纯透传（25 行），加入缓存是引入新的架构模式。明确边界：**仅新增的衍生品方法（`get_funding_rate` / `get_open_interest` / `get_long_short_ratio`）有缓存，现有方法（`get_ticker` / `get_current_price` / `get_ohlcv_dataframe`）保持无缓存透传不变。** 缓存实现统一使用 `src/utils/cache.py` 中的 `TTLCache` 类，封装 `(data, created_at, ttl)` 三元组结构以及 TTL 判断、429 延长、stale fallback 逻辑。`TTLCache` 同时被 NewsService 和 MarketDataService 复用，避免两份实现漂移。

数据路径与 `get_market_data` 完全一致（ccxt Python 全部使用 snake_case）：
```
tool → MarketDataService (缓存 3min) → BaseExchange
  ├─ OKXExchange:       self._client.fetch_funding_rate()       (已有 ccxt)
  └─ SimulatedExchange:  self._ccxt.fetch_funding_rate()        (已有 ccxt.pro，与 fetch_ohlcv 同理)
```

### 5.3 配置变更

**`src/config.py`** — 新增 `NewsConfig`：

```python
class NewsConfig(BaseModel):
    enabled: bool = True
```

所有新闻/情报数据源（CoinDesk News / FGI / ForexFactory / OKX 公告）均无需 API key，也无需配额保护（CoinDesk 无明确限额、429 由 `TTLCache` 的 stale-fallback 处理）。`NewsConfig` 仅保留 `enabled` 开关——禁用时 `NewsService` 不初始化，三个新闻相关 tool 返回 "News service not configured"。

**`Settings` 模型** — 新增字段：

```python
class Settings(BaseModel):
    ...
    news: NewsConfig = NewsConfig()
```

### 5.4 集成点

**`src/agent/trader.py`：**
- `TradingDeps` 新增 `news: NewsService | None = None`（直接导入，与现有 MemoryService / MarketDataService 等保持一致；NewsService 是新模块，不存在循环导入问题）
- 注册三个新工具

**`src/agent/tools_perception.py`：**
- 新增 `get_market_news()`、`get_critical_alerts()`、`get_derivatives_data()` 实现

**`src/cli/app.py`：**
- 在 `build_services()` 中 gate 初始化：`news_service = NewsService() if settings.news.enabled else None`。`enabled=False` 时 `TradingDeps.news=None`，三个新闻相关 tool 返回 "News service not configured"（见 §3.4）
- `build_services()` 返回值**不扩展**；`news_service` 引用存在 `deps.news` 里即可（与 `deps.memory` / `deps.metrics` / `deps.technical` 同惯例——这些 service 也都没单独返回）
- 在 `run()` 的 shutdown 段、`await exchange.close()` **之后**新增 `if deps.news is not None: await deps.news.close()`——位置选在 exchange 之后是为了先让 WebSocket 停，再关 HTTP client，减少 pending request

**`src/integrations/exchange/base.py`：**
- 新增 `FundingRate`、`OpenInterest`、`LongShortRatio` 数据类
- 新增 `fetch_funding_rate()`、`fetch_open_interest()`、`fetch_long_short_ratio()` 抽象方法

**`src/integrations/exchange/okx.py`：**
- 实现 `fetch_funding_rate()` — 调用 `self._client.fetch_funding_rate()`，使用 `@_retry()` 装饰器（与现有 REST 方法一致）
- 实现 `fetch_open_interest()` — 调用 `self._client.fetch_open_interest()`，使用 `@_retry()`
- 实现 `fetch_long_short_ratio()` — 内部调用 `self._client.fetch_long_short_ratio_history(symbol, "5m", limit=1)` 取最新一条，推算 `long_ratio` / `short_ratio`，使用 `@_retry()`

**`src/integrations/exchange/simulated.py`：**
- 实现 `fetch_funding_rate()` — 调用 `self._ccxt.fetch_funding_rate()`（与 `fetch_ohlcv` 同路径）
- 实现 `fetch_open_interest()` — 调用 `self._ccxt.fetch_open_interest()`
- 实现 `fetch_long_short_ratio()` — 内部调用 `self._ccxt.fetch_long_short_ratio_history(symbol, "5m", limit=1)` 取最新一条

**`src/integrations/market_data.py`：**
- 新增 `get_funding_rate()`、`get_open_interest()`、`get_long_short_ratio()` 便捷方法（含 3min TTL 缓存），委托给 `self._exchange`

**`src/agent/persona.py`：**
- 在 `_build_layer1()` 返回字符串的末尾（闭合三引号之前）追加三个工具的引导，紧接在现有最后一条 `- **Self-assessment**: ...` bullet 之后。保持与现有 bullet 格式一致：单行 `- **Tool name**: ...` 形式，不换行分段，不嵌套 bullet。

**`src/cli/wizard.py`：**
- **无改动**。所有新闻/情报数据源均无需 key，wizard 不新增任何步骤。（早期设计曾有 Step 6 给 CryptoPanic 配 key，切换到 CoinDesk 后删除。）

**`tests/test_tools.py`：**
- `MockDeps` dataclass 追加 `news: object = None` 字段，保持与 `TradingDeps` 新字段对称，避免现有 tool 测试因缺字段报错。

### 5.5 币种提取

定义在 `src/integrations/news/models.py`（与 `InformationEvent` 同文件，models 层公共工具）。NewsService 和 tool 层都会导入它。

```python
def extract_base_currency(symbol: str) -> str:
    """Extract base currency for matching against CoinDesk CATEGORY_DATA.

    OKX uses multiplier-prefixed contracts for low-price memecoins (1000PEPE,
    1000SHIB, kBONK) — the prefix is a contract-size scaling convention, not
    part of the asset identity. CoinDesk's CATEGORY_DATA uses the underlying
    asset code (PEPE / SHIB / BONK), so we strip `1000` / `k` prefixes to get
    the real match target. Without this, those symbols would silently fall
    through to general-only news.

    Still best-effort: truly non-standard tickers not in CoinDesk's taxonomy
    just get empty symbol-specific news (general news backfills).
    """
    base = symbol.split("/")[0]
    # Strip OKX multiplier prefixes so "1000PEPE" → "PEPE", "kSHIB" → "SHIB".
    # Only strip when the remainder is all-alpha, to avoid false positives like "k9".
    for prefix in ("1000", "k"):
        if base.startswith(prefix):
            remainder = base[len(prefix):]
            if remainder and remainder.isalpha():
                return remainder
    return base
    # BTC/USDT:USDT        → BTC
    # ETH/USDT:USDT        → ETH
    # 1000PEPE/USDT:USDT   → PEPE
    # kSHIB/USDT:USDT      → SHIB
```

---

## 6. System Prompt 更新

Layer 1（工具引导段）在末尾追加，紧接现有 `- **Self-assessment**: ...` 之后。格式**必须**与既有 bullet 一致：单行 `- **Name**: ...`，不换行、不嵌套子项：

```
- **Market news**: Use get_market_news to check crypto news headlines + Fear & Greed Index (0 = max fear, 100 = max greed). Returns up to 10 headlines (up to 5 symbol-specific, remainder general). Usually call without news_filter; use 'positive' / 'negative' / 'neutral' when you want a specific sentiment lens.
- **Critical alerts**: Use get_critical_alerts before trading to scan exchange announcements (maintenance, delistings, parameter changes) over the past lookback_hours and upcoming macro events (FOMC, CPI, NFP with impact level) within the next lookahead_hours. Often empty when nothing is scheduled. Macro calendar covers the current week only — Friday evening / weekend calls may miss next week's early events.
- **Derivatives structure**: Use get_derivatives_data for funding rate, open interest, and long/short ratio. Positive funding rate means longs pay shorts, negative means shorts pay longs (settlement interval varies by contract — see next settlement time in output). Open interest is total outstanding contracts. Long/short ratio is the ratio of long vs short account positions.
```

**引导原则：** Layer 1 工具引导仅描述工具功能和输出数据的事实性含义（如 "positive funding rate means longs pay shorts"），不包含交易启发式规则或策略建议（如 "extremes signal reversals"）。Agent 自行决定如何解读和使用这些数据。

---

## 7. Token 成本分析

### 单次调用成本：

| 工具 | 输出 tokens | 对比 |
|------|------------|------|
| `get_market_news` | ~500-700 | `get_market_data` ~1000-1200 |
| `get_critical_alerts` | ~100-400 | 大部分时候 < 100（空结果） |
| `get_derivatives_data` | ~150-250 | 最紧凑 |

### 每日成本估算（15 分钟周期 = 96 cycles/day）：

| 场景 | market_news (700) | critical_alerts (400) | derivatives (250) | 总计 |
|------|------------|----------------|-------------|------|
| 典型（各 50% cycle） | 33,600 | 9,600 | 12,000 | ~55,200 |
| 最差（每 cycle 全调） | 67,200 | 38,400 | 24,000 | ~129,600 |

最差情况使用各工具输出上限计算。`settings.yaml` 默认 `daily_max_tokens: 10000000`（10M），129,600 / 10M ≈ **1.3%**，成本可控。（若用户自定义 1M 预算，占比升至 ~13%，仍属可接受范围。）

### API 调用量：

**假设：** 96 cycles/day（15min 间隔）；每工具被 Agent 调用的频率范围 0.5–1 次/cycle。

| 数据源 | 每日 API 调用量 | 限额 |
|--------|--------------|------|
| CoinDesk News | 48–96（15min 缓存与 cycle 对齐，每 cycle 触发 ≤1 次调用。`news_filter` 不同值会产生独立缓存条目） | 无明确限额（pre-work 验证无限速迹象） |
| FGI | ≤4（6h 缓存） | 无限制 |
| ForexFactory | ≤4（6h 缓存） | 2 req/5min |
| OKX 公告 + 系统状态 | 192–384（2 个 cache 条目 × 48–96 miss 次/day × 每次 miss 内部 2 个 HTTP 调用 = 4 HTTP per full refresh） | ~5 req/s（安全默认值） |
| ccxt 衍生品（funding / OI / LSR） | 144–288（每工具 3 个 method，每 method 独立 3min 缓存，3min < 15min 每 cycle 均 miss；× 3 methods × 48–96 cycles） | 交易所 API 标准限额 |

**注意（CoinDesk 专用）：** CoinDesk 的缓存 key 是 `news:{news_filter}`，不含 symbol——因为 symbol 是会话级固定值且分组在 cache 之外进行，同一 session 内 cache 命中返回的 raw posts 对当前 symbol 一定正确。TTL 与 cycle 间隔对齐（15min），Agent 若交替使用不同 `news_filter` 值会产生多个缓存条目，但由于 TTL 与 cycle 一致，不会导致缓存穿透。其他数据源（FGI / ForexFactory / OKX 公告 / 衍生品）的 cache key 结构见 §5.2 缓存表——各有带前缀的独立 key。

---

## 8. 测试策略

### 8.1 单元测试

```
tests/test_news_clients.py
  # CoinDesk News
  - test_coindesk_parse_response            # JSON (TITLE/SOURCE_DATA/CATEGORY_DATA/...) → InformationEvent
  - test_coindesk_sentiment_param           # sentiment=POSITIVE/NEGATIVE/NEUTRAL 透传
  - test_coindesk_429_raises_rate_limit     # HTTP 429 → RateLimitHit
  # FGI
  - test_fgi_parse_response                 # JSON → value + classification
  - test_fgi_empty_data_returns_none
  # ForexFactory
  - test_calendar_parse_response            # JSON → InformationEvent 列表
  - test_calendar_filter_by_country_impact  # 仅保留 USD + High/Medium
  # OKX 公告 + 系统状态
  - test_okx_announcements_parse            # 嵌套 data[0].details[] 解析
  - test_okx_announcements_queries_correct_types
  - test_system_status_parse_response       # /system/status JSON → InformationEvent
  - test_system_status_queries_both_states

tests/test_news_service.py
  # 基础聚合
  - test_get_news_splits_by_symbol          # CATEGORY_DATA 匹配 deps.symbol
  - test_get_news_fills_from_general_when_symbol_short
  - test_get_news_passes_filter             # news_filter 透传 sentiment 参数
  - test_get_fgi / test_get_fgi_failure_returns_none / test_get_fgi_cached
  - test_get_announcements_combines_both_sources
  - test_get_announcements_filters_by_lookback
  - test_get_announcements_partial_failure
  - test_get_macro_events_filters_by_lookahead
  - test_get_macro_events_failure_returns_empty
  # 缓存 + 限流
  - test_news_cache_hit / test_news_cache_expired
  - test_different_filters_separate_cache
  - test_rate_limit_429_extends_ttl_with_stale
  - test_rate_limit_429_no_stale_raises
  # 生命周期
  - test_close_injected_http_not_closed
  - test_close_owned_http_closes

tests/test_cache.py
  # InformationEvent / extract_base_currency
  - test_information_event_creation / test_extract_base_currency_btc/_eth/_sol
  # TTLCache
  - test_cache_stores_and_returns / test_cache_different_keys_independent
  - test_cache_expires / test_cache_429_extends_ttl_with_stale
  - test_cache_429_no_stale_raises / test_cache_429_extended_ttl_persists
  - test_get_stale_returns_expired_data / test_get_stale_returns_none_if_missing

tests/test_news_tools.py
  - test_market_news_format                 # 完整输出格式
  - test_market_news_no_service             # deps.news is None
  - test_market_news_passes_filter
  - test_critical_alerts_format
  - test_critical_alerts_empty
  - test_critical_alerts_passes_params
  - test_derivatives_data_format
  - test_derivatives_data_negative_funding
  - test_derivatives_data_partial_failure
  - test_derivatives_data_custom_symbol

tests/test_derivatives_data.py
  - test_funding_rate_fields / test_open_interest_fields / test_long_short_ratio_fields
  - test_okx_fetch_funding_rate / test_okx_fetch_open_interest / test_okx_fetch_long_short_ratio
  - test_okx_long_short_ratio_empty_raises
  - test_sim_fetch_funding_rate / test_sim_fetch_open_interest / test_sim_fetch_long_short_ratio
  - test_sim_fetch_funding_rate_no_ccxt
  - test_market_data_get_funding_rate / ...get_open_interest / ...get_long_short_ratio
  - test_market_data_derivatives_cache_hit / ..._cache_by_symbol

tests/test_config.py（扩展）
  - test_news_config_defaults               # enabled=True
  - test_settings_with_news / test_settings_without_news
```

### 8.2 集成测试方式

- Mock HTTP 响应（CI 中不调用真实 API）
- 使用 `httpx.MockTransport` 提供响应 fixture
- 衍生品数据：mock ccxt 方法
- 开发阶段手动使用真实 API 验证

---

## 9. 实现成本汇总

拆分为**实现**（src/）与**测试**（tests/）两列，后者明显更大：

| 组件 | 实现行数 | 测试行数 | 时间 | 风险 |
|------|--------|--------|------|------|
| `get_market_news` | ~160 | ~280 | ~1 天 | 低 — 数据源成熟（CoinDesk pre-work 已验证） |
| `get_critical_alerts` | ~260 | ~350 | ~1.5 天 | 中 — ForexFactory 非官方、OKX `/system/status` schema 未完全验证（见 §2.4 caveat） |
| `get_derivatives_data` | ~180 | ~280 | ~0.5 天 | 中 — ccxt `fetch_long_short_ratio_history` runtime 待 Task 5 前验证（P5） |
| 共享基础设施 | ~100 | ~120 | 含在上述 | `InformationEvent` / `TTLCache` / `RateLimitHit` / `extract_base_currency` / `NewsConfig` |
| **合计** | **~700 行** | **~1030 行** | **~3 天** | 约 60+ 测试用例 |

单个 PR，主题：`feat(N2): add market intelligence tools — news, alerts, derivatives`

---

## 10. 设计决定（已确认）

1. **标题数量 + 新闻范围**：返回 10 条标题 — 5 条与交易币种相关 + 5 条通用加密货币新闻。单次 API 调用不带 `categories` 过滤、`limit=20`，本地按 `CATEGORY_DATA[].NAME` 是否含交易币种分组，各取 top 5。币种相关不足 5 条时用通用新闻补齐。

2. **数据源选型**：2026-04-17 pre-work 验证期间发现 CryptoPanic 于 2026-04-01 下线免费 Developer tier，切换到 **CoinDesk Data News API**（`data-api.coindesk.com/news/v1/article/list`）：
   - 无需 API key（公开端点），无配额限制迹象
   - 原生支持 `sentiment=POSITIVE/NEGATIVE/NEUTRAL` 过滤，替代原计划 `bullish/bearish/important/rising`
   - 无 `trending` 对应（`SCORE` 字段目前恒为 0），故 `news_filter` 精简为 3 值
   - 因为无 key，wizard 不新增任何步骤，`NewsConfig` 只保留 `enabled`

3. **宏观日历降级**：ForexFactory feed 是非官方源，如果下线，接受功能暂时不可用。工具返回 "macro calendar unavailable"，Agent 退回到技术分析。不投入备选源（如 FMP）。

4. **OKX 公告过滤**（已验证端点和分类）：
   - **端点 A（`/support/announcements`）**：通过 `annType` 参数过滤，仅拉取 `announcements-delistings`（下币）和 `trading-updates-us-aus`（交易规则/合约参数变更），跳过 `announcements-new-listings`（新上币对已持仓币种无直接影响）
   - **端点 B（`/system/status`）**：拉取 `state=scheduled` 和 `state=ongoing`，保留影响交易功能的维护事件

5. **ccxt 衍生品方法（待验证）**：spec §2.5 断言 ccxt OKX 的 `has.fetchLongShortRatio == False`，需用 `fetch_long_short_ratio_history(symbol, "5m", limit=1)` 取最新一条；且 `fetch_funding_rate` / `fetch_open_interest` 可用。Pre-work 只覆盖了 HTTP API，**未用 Python 验证过 ccxt 方法**。

   **Task 5 实现前必须先跑一次完整验证**（详细命令见 plan Pre-work P5）。概括：

   - `has` 表查询：`ex.has.get('fetchLongShortRatioHistory')` 应为 `True`
   - **实际调通**：`await ex.fetch_long_short_ratio_history('BTC/USDT:USDT', '5m', limit=1)` 应返回非空列表（`has` 是声明，runtime 才是证据）
   - `await ex.fetch_funding_rate('BTC/USDT:USDT')` 应返回含 `fundingRate` 的 dict
   - `await ex.fetch_open_interest('BTC/USDT:USDT')` 应返回含 `openInterestValue` 的 dict

   如果任一 runtime 调用抛 `ccxt.NotSupported` / `ccxt.ExchangeError`（即使 `has` 表为 True），需调整 Task 5 实现或回退到 OKX REST。

   **REST fallback 字段映射**（仅 long/short ratio 需要 fallback；funding rate / OI 由 ccxt 标准方法覆盖，无 fallback 需求）：

   - 端点：`GET https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio-contract?instId=BTC-USDT-SWAP&period=5m&limit=1`
   - 响应：`{"code":"0","data":[[ts, longShortRatio], ...]}`，`data` 是时间序列数组，每条 `[ts(ms 字符串), longShortRatio(字符串)]`

   映射到 `LongShortRatio` dataclass：

   | OKX REST 字段 | `LongShortRatio` 字段 | 计算 |
   |---|---|---|
   | `data[0][0]` | `timestamp` | `int(...)`（ms） |
   | `data[0][1]` | `long_short_ratio` | `float(...)`（如 "1.35"） |
   | 推算 | `long_ratio` | `r / (1 + r)` |
   | 推算 | `short_ratio` | `1 / (1 + r)` |
   | 传入 | `symbol` | 调用方（`OKXExchange.fetch_long_short_ratio(symbol)` 的参数） |

   注意：REST 端点参数 `instId` 用的是 `BTC-USDT-SWAP` 格式，不是 ccxt 的 `BTC/USDT:USDT`。fallback 实现时需要从 ccxt symbol 映射到 OKX instId。

   **ccxt `market()` 需要 markets 已加载**，但 `OKXExchange.__init__` 不自动 `load_markets()`。fallback 实现必须先显式加载，否则首次调用抛 `BadSymbol`：

   ```python
   async def fetch_long_short_ratio(self, symbol: str) -> LongShortRatio:
       try:
           history = await self._client.fetch_long_short_ratio_history(symbol, "5m", limit=1)
           # ... parse ccxt response
       except ccxt.NotSupported:
           # fallback path: OKX REST
           if not self._client.markets:
               await self._client.load_markets()  # 无害；ccxt 内部缓存，重复调用零代价
           inst_id = self._client.market(symbol)['id']  # "BTC-USDT-SWAP"
           # ... GET /api/v5/rubik/stat/contracts/long-short-account-ratio-contract
   ```

## 11. API 稳定性总评

| 稳定性等级 | 数据源 | 降级影响 |
|-----------|--------|---------|
| **高** | ccxt 衍生品数据、Alternative.me FGI、CoinDesk Data News API | 核心功能，但有降级兜底 |
| **中** | OKX 公告 + 系统状态（两个端点已验证可用） | 公告暂不可用 → Agent 失去风险预警 |
| **中低** | ForexFactory 经济日历 | 非官方源，可能随时下线 → Agent 不知道宏观事件 |

所有外部 API 都有降级兜底：不可用时返回提示信息，Agent 退回到技术分析决策。不会因为某个 API 下线导致整个 Agent 无法运行。
