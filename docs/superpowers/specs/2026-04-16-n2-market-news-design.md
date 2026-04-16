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
    → MemoryService (记忆)              ├─ OKXExchange (真实交易, ccxt)
    → MetricsService (绩效)             └─ SimulatedExchange (模拟交易, ccxt.pro 实时行情)
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

### 2.1 CryptoPanic API（新闻聚合 + 情绪）

| 项目 | 详情 |
|------|------|
| 端点 | `GET https://cryptopanic.com/api/v1/posts/` |
| 认证 | `auth_token` 查询参数（免费注册获取） |
| 免费额度 | ~200 req/day，基础字段（title, url, source, currencies） |
| 情绪过滤 | `filter=bullish\|bearish\|important\|rising\|hot` |
| 币种过滤 | `currencies=BTC,ETH`（CSV 格式） |
| 响应格式 | JSON `results[]`，含 `title`, `published_at`, `source.title`, `currencies[]`, `url` |
| 延迟 | 近实时（发布后数分钟内出现） |
| **稳定性** | **中高** — 2014 年起运营，有正式文档和付费计划。小公司，免费 tier 额度可能缩减 |

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
| 响应格式 | JSON 数组，含 `title`, `country`, `date`, `impact`(High/Medium/Low), `forecast`, `previous` |
| 覆盖范围 | 当周所有经济事件：FOMC、CPI、NFP、PPI、GDP 等 |
| 已知限制 | 仅包含当前一周数据。周五晚间 `lookahead_hours=12` 可能跨入下周，但数据中无下周事件，存在假阴性风险（如漏掉下周一/二的 FOMC）。实际影响有限——高影响力经济事件不在周末发生，且 Agent 下一个工作日会自动获取新一周数据 |
| **稳定性** | **中低** — 非官方 feed，无文档/SLA，可能随时改格式。但已稳定运行多年，被众多开源项目使用 |

### 2.4 OKX 公告 API

| 项目 | 详情 |
|------|------|
| 端点 | 候选：`GET /api/v5/support/announcements` 或 `GET /api/v5/public/announcements`（实现时调研确认） |
| 认证 | 无需认证（公开端点） |
| 内容 | 合约参数变更、维护停机、上下币公告等 |
| **稳定性** | **中** — 非标准化，OKX 可能改版。返回内容含营销/运营混杂，需过滤 |

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
| CryptoCompare News | 与 CryptoPanic 功能重叠；可后续作为备用源 |
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
    news_filter: str | None = None,
) -> str:
    """Get recent crypto news headlines and market sentiment.
    news_filter: 'rising' (trending), 'bullish', 'bearish', 'important'. Default: no filter (latest).
    Returns 10 headlines (5 symbol-specific + 5 general crypto) + Fear & Greed Index.
    Output ~500-700 tokens."""
```

**参数：**
- `news_filter`（可选）：CryptoPanic 过滤器 — `rising|bullish|bearish|important`。默认：最新新闻（无过滤）。不暴露 `hot`（与 `rising` 重叠度高，减少 filter 变体有助于控制 API 配额）。避免使用 `filter` 以防遮蔽 Python 内建函数。
- 币种从 `deps.symbol` 自动提取（如 `BTC/USDT:USDT` → `BTC`）。

**新闻范围：10 条 = 5 币种相关 + 5 通用**

单次 API 调用不带 `currencies` 过滤、`limit=20`，返回后在本地按是否包含交易币种分为两组，各取 top 5。好处：
- 只用 1 次 API 调用（节省 CryptoPanic 配额）
- 币种相关新闻确保 Agent 不漏关键事件
- 通用新闻捕捉宏观事件（如监管政策、市场恐慌）
- 如果币种相关不足 5 条，用通用新闻补齐

**为何 FGI 搭车而非独立工具：**
- FGI 仅 ~20 tokens，附带成本可忽略
- Agent 一次调用即获完整宏观视图
- 不值得为一个数字单独占一个 tool call

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
    Use before opening positions to check for risks.
    Output ~100-400 tokens (often empty — no news is good news)."""
```

**参数：**
- `lookback_hours`：回看多久的交易所公告（默认 24h）
- `lookahead_hours`：前瞻多久的宏观事件（默认 12h）

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
```

大部分时候返回较短甚至为空（"No exchange announcements" / "No upcoming macro events"），token 成本极低。

### 3.3 `get_derivatives_data` — 衍生品市场数据

```python
@agent.tool
async def get_derivatives_data(
    ctx: RunContext[TradingDeps],
    symbol: str | None = None,
) -> str:
    """Get derivatives market data: funding rate, open interest, long/short ratio.
    Essential for perpetual contract trading decisions.
    Output ~150-250 tokens."""
```

**设计决策：** derivatives 数据是全市场公开数据（无需 API key），不是账户数据。无论使用 SimulatedExchange 还是 OKXExchange，都直接从 OKX 获取真实数据。

**实现方式：** 与 `get_market_data` 走完全相同的数据路径——通过 BaseExchange 抽象层：
- `BaseExchange` 新增 `fetch_funding_rate()` / `fetch_open_interest()` / `fetch_long_short_ratio()` 三个抽象方法（`fetch_long_short_ratio` 保持简洁接口，实现层内部调用 ccxt 的 `fetch_long_short_ratio_history(symbol, "5m", limit=1)` 取最新一条）
- `OKXExchange` 用已有的 `self._client`（ccxt）实现
- `SimulatedExchange` 用已有的 `self._ccxt`（ccxt.pro）实现——和它的 `fetch_ohlcv()` 一样，读的是真实市场数据
- `MarketDataService` 通过 `self._exchange` 调用，和 `get_ticker()` / `get_ohlcv_dataframe()` 一致
- 衍生品数据缓存加在 `MarketDataService` 层（TTL 3 分钟，cache key 为 `symbol`），与 NewsService 的缓存同级

不引入额外的 ccxt 客户端，不破坏现有架构。

**输出格式：**

```
=== Derivatives Data (ETH/USDT:USDT) ===
Funding Rate: +0.0125% (next settlement in 3h 42m)
  Positive rate — longs pay shorts (bullish bias)
Open Interest: $4.82B
Long/Short Ratio: 1.35 (57.4% long / 42.6% short)
```

注意：v1 仅展示 `fetch_funding_rate()` 返回的当前费率，不包含历史均值。计算 8h 平均需调用 `fetch_funding_rate_history()`，工作量与边际价值不匹配，留待后续迭代。

**Token 估算：** ~150-250 tokens

### 3.4 优雅降级（三个工具统一策略）

| 故障场景 | 行为 |
|---------|------|
| `deps.news` 为 None（NewsService 未初始化） | `get_market_news` / `get_critical_alerts` 返回 "News service not configured, rely on technical analysis"。`get_derivatives_data` 不受影响（走 MarketDataService/BaseExchange 路径） |
| 某个 API 不可用 / 超时 | 该部分返回 "temporarily unavailable"，其余部分正常返回 |
| 全部 API 不可用 | 返回 "services currently unavailable, rely on technical analysis" |
| 未配置 API key（CryptoPanic）| 仅返回 FGI + 提示配置 |
| 网络超时 | 每个 API 调用 5 秒超时，快速失败 |
| ForexFactory feed 格式变更 | 返回 "macro calendar unavailable"，不影响其他功能 |
| CryptoPanic 日配额耗尽（计数器 ≥ 150） | 自动复用最近缓存数据，记录 WARNING |

---

## 4. 数据模型

统一的 dataclass，不做持久化存储，仅规范内存中的数据结构。使用 `@dataclass` 而非 `BaseModel`，与代码库中所有 DTO（Ticker, Candle, Order, Balance, Position 等）风格一致（`BaseModel` 仅用于配置类）：

```python
@dataclass
class InformationEvent:
    timestamp: datetime
    source: str           # "cryptopanic" / "alternative_me" / "okx" / "forexfactory"
    category: str         # "news" / "fgi" / "announcement" / "macro_event"
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
  __init__.py
  cryptopanic.py        # CryptoPanic API 客户端
  fear_greed.py         # Alternative.me FGI 客户端
  calendar.py           # ForexFactory 经济日历客户端
  announcements.py      # OKX 公告客户端
  models.py             # InformationEvent 基类
  service.py            # NewsService（聚合 news + FGI + calendar + announcements，含缓存）
```

### 5.2 Service 设计

```python
class NewsService:
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=5.0)
        self._cryptopanic_daily_calls = 0         # 全局日调用计数器
        self._cryptopanic_daily_reset: float = 0  # 上次重置时间戳

    # get_market_news 使用
    async def get_news(self, symbol: str, news_filter: str | None, max_per_group: int = 5) -> list[InformationEvent]
        """API 请求 limit=20（不带 currencies 过滤），本地按 symbol 分为两组各取 top max_per_group 条。"""
    async def get_fear_greed_index(self) -> InformationEvent | None

    # get_critical_alerts 使用
    async def get_macro_events(self, lookahead_hours: int) -> list[InformationEvent]
    async def get_announcements(self, lookback_hours: int) -> list[InformationEvent]

    # 生命周期
    async def close(self) -> None:
        """关闭 httpx 客户端。在 app.py shutdown 逻辑中调用。"""
        await self._http.aclose()
```

**CryptoPanic 配额保护：**

Agent 行为不可完全预测，可能交替使用不同 `news_filter` 值导致缓存不命中。为防止超过 ~200/day 免费限额，`NewsService` 维护一个**全局日调用计数器**（`_cryptopanic_daily_calls`）：
- 每次实际调用 CryptoPanic API 时计数 +1
- 每日 UTC 0:00 重置
- 当计数达到 150（预留 25% 安全裕量）时，自动复用最近一次任意 filter 的缓存数据，不再发起新的 API 调用
- 行为类似 429 处理——使用过期缓存，记录 WARNING 日志

**缓存策略：**

所有缓存为内存级别、进程级别，无需持久化。

| 数据源 | Cache Key | 默认 TTL | 理由 |
|--------|-----------|---------|------|
| CryptoPanic 新闻 | `news_filter` 值（如 `"rising"` / `"bullish"` / `None`） | 15 min | 与默认 cycle 间隔（15min）对齐，保证每个 cycle 最多触发一次 API 调用。不同 `news_filter` 返回不同文章集，需分别缓存 |
| FGI | 无（固定 key） | 6 hours | 每日更新一次，高频请求无意义 |
| ForexFactory 宏观日历 | 无（固定 key） | 6 hours | 按周发布，拉取整周数据后本地按 `lookback/lookahead` 过滤 |
| OKX 公告 | 无（固定 key） | 10 min | 拉取全部公告后本地过滤，公告更新频率低 |
| Derivatives（funding/OI） | `symbol` | 3 min | 不同币种数据不同；funding rate 每 8h 结算一次，3min 缓存已足够 |

**限流保护（Rate Limit Handling）：**

当 API 返回 HTTP 429（Too Many Requests）时：
- 不视为错误，不触发降级消息
- 自动将该数据源的缓存 TTL **临时延长至 30 分钟**（使用上次缓存的数据继续服务）
- 记录 WARNING 日志
- 实现机制：缓存条目记录 `(data, created_at, ttl)` 三元组。正常时 `ttl` 为默认值；收到 429 时将该条目的 `ttl` 覆盖为 1800 秒。下次请求时判断 `now - created_at > ttl`，过期则重新请求并恢复默认 TTL

其他 HTTP 错误（5xx / timeout）仍使用标准降级策略（返回 "temporarily unavailable"）。

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
    open_interest: float      # 张数或币数
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
        # 缓存结构与 NewsService 统一：dict[key, (data, created_at, ttl)]
        # 429 时将 ttl 覆盖为 1800s
        self._derivatives_cache: dict[str, tuple[Any, float, float]] = {}

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        return await self._cached_fetch("funding:" + symbol, 180,
            lambda: self._exchange.fetch_funding_rate(symbol))

    async def get_open_interest(self, symbol: str) -> OpenInterest:
        return await self._cached_fetch("oi:" + symbol, 180,
            lambda: self._exchange.fetch_open_interest(symbol))

    async def get_long_short_ratio(self, symbol: str) -> LongShortRatio:
        return await self._cached_fetch("lsr:" + symbol, 180,
            lambda: self._exchange.fetch_long_short_ratio(symbol))
```

注意：当前 `MarketDataService` 是纯透传（25 行），加入缓存是引入新的架构模式。缓存实现采用与 `NewsService` 相同的 `(data, created_at, ttl)` 三元组结构，通过统一的 `_cached_fetch()` 辅助方法处理 TTL 判断和 429 延长逻辑。

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
    cryptopanic_api_key: str = ""   # 从环境变量 CRYPTOPANIC_API_KEY 加载
```

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
- 初始化 `NewsService` 并注入 `TradingDeps`
- 从环境变量加载 `CRYPTOPANIC_API_KEY`
- 在 shutdown 逻辑中调用 `news_service.close()`（与 `exchange.close()` 同位置）

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
- Layer 1 新增三个工具的引导

**`src/cli/wizard.py`：**
- 在现有 Step 5 之后新增 Step 6：CryptoPanic API key 配置（允许跳过，提示 "Press Enter to skip — Fear & Greed Index still works without it"）
- 启动时校验已有 key：超时重试、429 视为有效、401/403 重新引导

### 5.5 币种提取

```python
def extract_base_currency(symbol: str) -> str:
    return symbol.split("/")[0]
    # BTC/USDT:USDT → BTC
    # ETH/USDT:USDT → ETH
```

---

## 6. System Prompt 更新

Layer 1（工具引导段）新增：

```
get_market_news(news_filter?) — Get crypto news headlines + Fear & Greed Index.
  - Returns 10 headlines: 5 for your symbol + 5 general crypto news
  - Use for macro context and market narrative
  - Usually call without news_filter (default gives latest headlines, sufficient for most decisions)
  - news_filter only when you need a specific lens: 'rising', 'bullish', 'bearish', 'important'
  - Fear & Greed extremes (< 20 or > 80) suggest increased caution
  - News confirms or challenges your technical read — never trade on headlines alone
  - Output ~500-700 tokens

get_critical_alerts(lookback_hours?, lookahead_hours?) — Exchange announcements + upcoming macro events.
  - CHECK BEFORE opening any new position
  - Contract maintenance, parameter changes, delistings = immediate risk
  - FOMC/CPI/NFP within 1h = avoid new entries or reduce size
  - Often returns empty — no alerts is good news
  - Note: macro calendar covers current week only — Friday evening/weekend may miss next week's early events
  - Output ~100-400 tokens

get_derivatives_data(symbol?) — Funding rate, open interest, long/short ratio.
  - Funding rate extremes signal crowded trades and potential reversals
  - OI + price divergence = key inflection signal
  - Use alongside technical analysis for every trade decision
  - Output ~150-250 tokens
```

---

## 7. Token 成本分析

### 单次调用成本：

| 工具 | 输出 tokens | 对比 |
|------|------------|------|
| `get_market_news` | ~500-700 | `get_market_data` ~1000-1200 |
| `get_critical_alerts` | ~100-400 | 大部分时候 < 100（空结果） |
| `get_derivatives_data` | ~150-250 | 最紧凑 |

### 每日成本估算（15 分钟周期 = 96 cycles/day）：

| 场景 | market_news | critical_alerts | derivatives | 总计 |
|------|------------|----------------|-------------|------|
| 典型（各 50% cycle） | 28,800 | 4,800 | 12,000 | ~45,600 |
| 最差（每 cycle 全调） | 57,600 | 19,200 | 24,000 | ~100,800 |

最差情况约 1M token 每日预算的 10%，成本可控。

### API 调用量：

| 数据源 | 每日调用量 | 限额 |
|--------|----------|------|
| CryptoPanic | 48-96 tool calls（15min 缓存与 cycle 间隔对齐，每个 cycle 最多触发 1 次 API 调用。最坏情况 ~96/day，典型 ~48/day） | ~200/day |
| FGI | 4（6h 缓存） | 无限制 |
| ForexFactory | 1-4（6h 缓存） | 2 req/5min |
| OKX 公告 | 48-144（10min 缓存） | 无限制 |
| ccxt funding/OI | 48-96 | 交易所 API 标准限额 |

**注意：** 采用单次 API 调用（不带 `currencies` 过滤、`limit=20`）+ 本地分组方案后，缓存 key 仅含 `news_filter`（不含 symbol），且 TTL 与 cycle 间隔对齐（15min），每个 cycle 最多触发 1 次 API 调用。Agent 若交替使用不同 `news_filter` 值，会产生多个缓存条目，但由于 TTL 与 cycle 间隔一致，不会导致缓存穿透。

---

## 8. 测试策略

### 8.1 单元测试

```
tests/test_news_service.py
  # CryptoPanic
  - test_cryptopanic_parse_response         # JSON → InformationEvent 列表
  - test_cryptopanic_filter_param           # filter 参数映射
  - test_cryptopanic_symbol_extraction      # BTC/USDT:USDT → BTC
  # FGI
  - test_fgi_parse_response                 # JSON → value + classification
  # ForexFactory
  - test_calendar_parse_response            # JSON → InformationEvent 列表
  - test_calendar_filter_by_impact          # 仅保留 High/Medium
  - test_calendar_lookahead_window          # 时间窗口过滤
  # OKX 公告
  - test_announcement_parse_response        # JSON → InformationEvent 列表
  - test_announcement_filter_relevant       # 过滤营销内容
  # 缓存
  - test_news_cache_hit                     # 缓存命中
  - test_news_cache_expired                 # 缓存过期刷新
  - test_fgi_cache_ttl                      # 6h TTL
  # 限流保护
  - test_rate_limit_429_extends_ttl         # 收到 429 后 TTL 延长至 30min
  - test_rate_limit_429_uses_stale_cache    # 429 时继续使用上次缓存数据
  - test_rate_limit_429_recovery            # 30min 后恢复正常 TTL
  # 降级
  - test_graceful_degradation_partial       # 单个 API 失败 → 其余正常
  - test_graceful_degradation_total         # 全部失败 → 降级消息
  - test_no_api_key                         # 无 key → 仅返回 FGI
  # 格式化
  - test_market_news_format                 # 完整输出格式
  - test_critical_alerts_format             # 完整输出格式
  - test_critical_alerts_empty              # 无事件时的输出

tests/test_derivatives_data.py
  - test_funding_rate_format                # 格式化输出
  - test_open_interest_format               # 格式化输出
  - test_long_short_ratio_format            # 格式化输出
  - test_derivatives_cache_hit              # 3min TTL 缓存命中
  - test_derivatives_cache_by_symbol        # 不同 symbol 独立缓存
  - test_derivatives_api_failure            # API 失败 → 降级消息

tests/test_config.py（扩展）
  - test_news_config_from_yaml              # YAML 加载
  - test_news_config_env_override           # 环境变量覆盖
```

### 8.2 集成测试方式

- Mock HTTP 响应（CI 中不调用真实 API）
- 使用 `httpx.MockTransport` 提供响应 fixture
- 衍生品数据：mock ccxt 方法
- 开发阶段手动使用真实 API 验证

---

## 9. 实现成本汇总

| 工具 | 代码量（含测试） | 时间 | 风险 |
|------|----------------|------|------|
| `get_market_news` | ~380 行 | ~1 天 | 低 — 数据源成熟 |
| `get_critical_alerts` | ~400 行 | ~1.5 天 | 中 — ForexFactory 非官方、OKX 公告需过滤 |
| `get_derivatives_data` | ~250 行 | ~0.5 天 | 低 — ccxt 原生支持，走 BaseExchange 抽象（新增抽象方法 + 两个 exchange 实现 + MarketDataService 便捷方法） |
| 共享基础设施 | ~100 行 | 含在上述 | InformationEvent 模型、配置、persona 更新 |
| **合计** | **~1130 行** | **~3 天** | |

单个 PR，主题：`feat(N2): add market intelligence tools — news, alerts, derivatives`

---

## 10. 设计决定（已确认）

1. **标题数量 + 新闻范围**：返回 10 条标题 — 5 条与交易币种相关 + 5 条通用加密货币新闻。单次 API 调用不带 `currencies` 过滤、`limit=20`，本地按是否包含交易币种分组，各取 top 5。币种相关不足 5 条时用通用新闻补齐。

2. **Wizard 集成**：初次配置时 wizard 引导用户设置 CryptoPanic API key，允许跳过（FGI 无需 key 仍可工作）。后续启动时检测 key 是否已配置并测试可用性：
   - 超时 → 重试
   - HTTP 429（配额耗尽）→ 视为 key 有效但暂时不可用，正常启动
   - 其他错误（401/403）→ 判定为无效 key，引导用户重新配置

3. **宏观日历降级**：ForexFactory feed 是非官方源，如果下线，接受功能暂时不可用。工具返回 "macro calendar unavailable"，Agent 退回到技术分析。不投入备选源（如 FMP）。

4. **OKX 公告过滤**：自维护过滤逻辑，只保留对 Agent 决策有影响的公告。实现时调研 OKX 公告 API 返回结构，据此设计过滤规则：
   - **保留**：合约参数变更、维护停机、下币通知、funding rate 调整、涉及交易币种的重大变更
   - **过滤掉**：营销活动、返佣推广、新品上线（除非涉及交易币种）、社区活动

## 11. API 稳定性总评

| 稳定性等级 | 数据源 | 降级影响 |
|-----------|--------|---------|
| **高** | ccxt 衍生品数据、Alternative.me FGI | 核心功能，但有降级兜底 |
| **中高** | CryptoPanic | 新闻暂不可用 → Agent 退回纯技术分析 |
| **中** | OKX 公告 API | 公告暂不可用 → Agent 失去风险预警 |
| **中低** | ForexFactory 经济日历 | 非官方源，可能随时下线 → Agent 不知道宏观事件 |

所有外部 API 都有降级兜底：不可用时返回提示信息，Agent 退回到技术分析决策。不会因为某个 API 下线导致整个 Agent 无法运行。
