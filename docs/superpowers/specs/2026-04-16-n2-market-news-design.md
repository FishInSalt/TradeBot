# N2: 市场信息面工具增强 — 设计文档

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
| **稳定性** | **中低** — 非官方 feed，无文档/SLA，可能随时改格式。但已稳定运行多年，被众多开源项目使用 |

### 2.4 OKX 公告 API

| 项目 | 详情 |
|------|------|
| 端点 | OKX 公开 REST API（非 ccxt 封装） |
| 认证 | 无需认证（公开端点） |
| 内容 | 合约参数变更、维护停机、上下币公告等 |
| **稳定性** | **中** — 非标准化，OKX 可能改版。返回内容含营销/运营混杂，需过滤 |

### 2.5 ccxt 衍生品数据

| 项目 | 详情 |
|------|------|
| 方法 | `fetchFundingRate(symbol)`, `fetchOpenInterest(symbol)` |
| 认证 | 无需认证（公开市场数据端点） |
| 数据 | funding rate（当前/下期）、OI（全市场持仓量）、多空比 |
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
    filter: str | None = None,
) -> str:
    """Get recent crypto news headlines and market sentiment.
    filter: 'rising' (trending), 'bullish', 'bearish', 'important'. Default: no filter (latest).
    Returns 10 headlines (5 symbol-specific + 5 general crypto) + Fear & Greed Index.
    Output ~500-700 tokens."""
```

**参数：**
- `filter`（可选）：CryptoPanic 过滤器 — `rising|bullish|bearish|important|hot`。默认：最新新闻。
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
- `BaseExchange` 新增 `fetch_funding_rate()` / `fetch_open_interest()` 抽象方法
- `OKXExchange` 用已有的 `self._client`（ccxt）实现
- `SimulatedExchange` 用已有的 `self._ccxt`（ccxt.pro）实现——和它的 `fetch_ohlcv()` 一样，读的是真实市场数据
- `MarketDataService` 通过 `self._exchange` 调用，和 `get_ticker()` / `get_ohlcv_dataframe()` 一致

不引入额外的 ccxt 客户端，不破坏现有架构。

**输出格式：**

```
=== Derivatives Data (ETH/USDT:USDT) ===
Funding Rate: +0.0125% (next settlement in 3h 42m)
  8h avg: +0.0098% — longs pay shorts (bullish bias)
Open Interest: $4.82B (+3.2% in 24h)
  OI rising with price rising → new money entering longs
Long/Short Ratio: 1.35 (57.4% long / 42.6% short)
```

**Token 估算：** ~150-250 tokens

### 3.4 优雅降级（三个工具统一策略）

| 故障场景 | 行为 |
|---------|------|
| 某个 API 不可用 / 超时 | 该部分返回 "temporarily unavailable"，其余部分正常返回 |
| 全部 API 不可用 | 返回 "services currently unavailable, rely on technical analysis" |
| 未配置 API key（CryptoPanic）| 仅返回 FGI + 提示配置 |
| 网络超时 | 每个 API 调用 5 秒超时，快速失败 |
| ForexFactory feed 格式变更 | 返回 "macro calendar unavailable"，不影响其他功能 |

---

## 4. 数据模型

统一的 Pydantic 基类，不做持久化存储，仅规范内存中的数据结构：

```python
class InformationEvent(BaseModel):
    timestamp: datetime
    source: str           # "cryptopanic" / "alternative_me" / "okx" / "forexfactory"
    category: str         # "news" / "fgi" / "announcement" / "macro_event"
    importance: str       # "low" / "medium" / "high"
    title: str
    content: str = ""
    url: str = ""
    symbols: list[str] = []
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
        # 缓存
        self._news_cache: ...       # TTL 5 min
        self._fgi_cache: ...        # TTL 6 hours
        self._calendar_cache: ...   # TTL 6 hours (周数据，每日刷新足够)
        self._announce_cache: ...   # TTL 10 min

    # get_market_news 使用
    async def get_news(self, symbol: str, filter: str | None, limit: int = 5) -> list[InformationEvent]
    async def get_fear_greed_index(self) -> InformationEvent | None

    # get_critical_alerts 使用
    async def get_macro_events(self, lookahead_hours: int) -> list[InformationEvent]
    async def get_announcements(self, lookback_hours: int) -> list[InformationEvent]
```

衍生品数据不经过 NewsService，走 BaseExchange 抽象层（与 ticker / OHLCV 相同路径）：

```python
# src/integrations/exchange/base.py — 新增抽象方法
class BaseExchange(ABC):
    # 现有方法...

    @abstractmethod
    async def fetch_funding_rate(self, symbol: str) -> FundingRate: ...

    @abstractmethod
    async def fetch_open_interest(self, symbol: str) -> OpenInterest: ...

# src/integrations/market_data.py — 新增便捷方法
class MarketDataService:
    # 现有方法（不变）...

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        return await self._exchange.fetch_funding_rate(symbol)

    async def get_open_interest(self, symbol: str) -> OpenInterest:
        return await self._exchange.fetch_open_interest(symbol)
```

数据路径与 `get_market_data` 完全一致：
```
tool → MarketDataService → BaseExchange
  ├─ OKXExchange:       self._client.fetchFundingRate()    (已有 ccxt)
  └─ SimulatedExchange:  self._ccxt.fetch_funding_rate()   (已有 ccxt.pro，与 fetch_ohlcv 同理)
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
- `TradingDeps` 新增 `news: object | None = None`
- 注册三个新工具

**`src/agent/tools_perception.py`：**
- 新增 `get_market_news()`、`get_critical_alerts()`、`get_derivatives_data()` 实现

**`src/cli/app.py`：**
- 初始化 `NewsService` 并注入 `TradingDeps`
- 从环境变量加载 `CRYPTOPANIC_API_KEY`

**`src/integrations/exchange/base.py`：**
- 新增 `FundingRate`、`OpenInterest` 数据类
- 新增 `fetch_funding_rate()`、`fetch_open_interest()` 抽象方法

**`src/integrations/exchange/okx.py`：**
- 实现 `fetch_funding_rate()` — 调用已有 `self._client.fetchFundingRate()`
- 实现 `fetch_open_interest()` — 调用已有 `self._client.fetchOpenInterest()`

**`src/integrations/exchange/simulated.py`：**
- 实现 `fetch_funding_rate()` — 调用已有 `self._ccxt.fetch_funding_rate()`（与 `fetch_ohlcv` 同路径）
- 实现 `fetch_open_interest()` — 调用已有 `self._ccxt.fetch_open_interest()`

**`src/integrations/market_data.py`：**
- 新增 `get_funding_rate()`、`get_open_interest()` 便捷方法，委托给 `self._exchange`

**`src/agent/persona.py`：**
- Layer 1 新增三个工具的引导

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
get_market_news(filter?) — Get crypto news headlines + Fear & Greed Index.
  - Returns 10 headlines: 5 for your symbol + 5 general crypto news
  - Use for macro context and market narrative
  - filter: 'rising', 'bullish', 'bearish', 'important' (optional)
  - Fear & Greed extremes (< 20 or > 80) suggest increased caution
  - News confirms or challenges your technical read — never trade on headlines alone
  - Output ~500-700 tokens

get_critical_alerts(lookback_hours?, lookahead_hours?) — Exchange announcements + upcoming macro events.
  - CHECK BEFORE opening any new position
  - Contract maintenance, parameter changes, delistings = immediate risk
  - FOMC/CPI/NFP within 1h = avoid new entries or reduce size
  - Often returns empty — no alerts is good news
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
| CryptoPanic | 48-96 tool calls（5min 缓存，单次调用不带 currencies 过滤取 20 条本地分组，cache key 仅含 filter，实际 API 调用 ~20-50） | ~200/day |
| FGI | 4（6h 缓存） | 无限制 |
| ForexFactory | 1-4（6h 缓存） | 2 req/5min |
| OKX 公告 | 48-144（10min 缓存） | 无限制 |
| ccxt funding/OI | 48-96 | 交易所 API 标准限额 |

**注意：** 采用单次 API 调用（不带 `currencies` 过滤、`limit=20`）+ 本地分组方案后，缓存 key 仅含 `filter`（不含 symbol），缓存命中率大幅提高。15 分钟 cycle + 5 分钟缓存 TTL 下，实际 API 调用量远低于免费额度。

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
