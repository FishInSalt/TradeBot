# N3: Macro Context & Institutional Flows — 设计文档

## 0. 背景

### 0.1 项目概述

TradeBot 是一个 LLM 驱动的加密货币自动交易系统。Agent（Claude）通过工具调用感知市场、管理仓位、做出交易决策，在 USDT 保证金永续合约上自主交易。

核心运行循环：Agent 每 15 分钟被唤醒一次（也可被订单成交、价格警报等事件提前唤醒），通过工具获取市场数据和账户状态，分析后决定是否操作。

### 0.2 当前架构

**System Prompt 三层结构**（详见 `src/agent/persona.py`）：
- **Layer 1**（身份 + 工具引导）— Agent 是谁、市场上下文（永续合约、单向持仓）、每个工具的使用场景和注意事项
- **Layer 2**（思维框架）— 通用交易分析维度：市场结构、信号确认、风险回报、仓位管理、自我复盘
- **Layer 3**（人格 + 策略，均可选）— 注入交易风格（conservative/moderate/aggressive）和策略偏好（trend_following/swing/breakout）

**现有工具（22 个）**：

| 类别 | 工具 |
|------|------|
| 感知（8） | `get_market_data` `get_position` `get_account_balance` `get_open_orders` `get_trade_journal` `get_memories` `get_active_alerts` `get_performance` |
| 市场情报（3，N2 引入） | `get_market_news` `get_critical_alerts` `get_derivatives_data` |
| 执行（10） | `open_position` `close_position` `set_stop_loss` `set_take_profit` `adjust_leverage` `place_limit_order` `cancel_order` `set_price_alert` `add_price_level_alert` `set_next_wake` |
| 记忆（1） | `save_memory` |

**N2 已交付的能力**：crypto news + FGI（Alternative.me）+ OKX 公告/维护 + ForexFactory 宏观日历 + funding rate / OI / long-short ratio。

### 0.3 为什么做这个迭代

观察期前评估发现：明确定位为 **swing/position trading（分钟~天级）**后，agent 仍有 3 块结构性信息缺口需要补：

1. **更长时间结构** — 当前 `get_market_data` 上限 80 根 K 线，看不到 MA200 或周/月级别关键位
2. **宏观环境** — DXY、VIX、10Y、SPY/QQQ 这些跨市场 risk-on/off 信号完全缺失
3. **机构资金流** — BTC/ETH spot ETF 流入流出、稳定币 dry powder 是 2024 后机构周期 narrative 的核心

这三块都是 **HFT 系统看不懂、但 swing trader 必看**的信息维度，**正是 LLM 系统的差异化空间**。

### 0.4 与其他议题的边界

本 spec 范围**不包含**：
- 链上 whale alerts、交易所 netflow（暂缓，等付费源 CoinGlass 上线再议）
- 期权 IV / put-call ratio（OKX 期权数据可用性未验证）
- 信源可信度治理（→ N4 议题，CoinDesk/FGI 等"软信号源"处理改进）
- 工具输出 / prompt 描述里的标签化判断清理（→ N5 议题）

---

## 1. 目标

### 1.1 功能目标

引入 4 个新 LLM 工具：

| 工具 | 数据 | 决策价值 |
|---|---|---|
| `get_higher_timeframe_view` | 1d/1w/1M K 线结构（MA50/100/200 + 100-period 高低 + 范围位置） | 看 position trading 大结构 |
| `get_macro_context` | BTC.D/ETH.D + 总市值 + DXY/VIX/10Y/2s10s/通胀预期 + SPY/QQQ | 跨市场环境定位 |
| `get_etf_flows` | BTC + ETH spot ETF 7 日净流入明细 + 累计 AUM | 机构资金 narrative |
| `get_stablecoin_supply` | USDT/USDC 当前供应 + 7d 变化 | 入场资金"干火药"指标 |

### 1.2 非功能目标

- **数据源全部已实测**（FRED + Alpha Vantage + SoSoValue + CoinGecko + DefiLlama 5 个源 smoke test 通过）
- **工具输出严格遵守"事实/无决策暗示"原则**（不包含 "bullish"/"signals"/"often precedes" 等判断性语言）
- **降级一致性**：沿用 N2 §3.5 三态返回（数据 / 空 / temporarily unavailable）
- **无新增运行时依赖**：仅使用已有的 `httpx`，新增 `zoneinfo`（Python 3.9+ stdlib）；测试侧用 `monkeypatch` 替代 `freezegun`，**不引入新 dev 依赖**
- **测试覆盖匹配 N2 比例**：约 100 个新增测试（详见 §8.4）

### 1.3 非目标（明确不做）

- 不实现 ETF 历史超过 30 天（SoSoValue 免费档限制）
- 不做 NYSE 假日日历（接受假日时段少量 budget 浪费 — 工作日假日按时段感知逻辑会命中短 TTL 重复 fetch 静态数据；观察期若有预算压力再加 `pandas_market_calendars`）
- 不引入"可信度评级"语义（→ N4）
- 不重构 N2 现有 tool 的标签化输出（→ N5）

---

## 2. 数据源

### 2.1 CoinGecko `/global` — 加密市场总览

| 项目 | 值 |
|---|---|
| 端点 | `GET https://api.coingecko.com/api/v3/global` |
| Auth | header `x-cg-demo-api-key: <key>`（**Demo tier，需注册免费 key**；CoinGeckoGlobalClient 构造时接收 key 并放入 header） |
| 限流 | **30 req/min**（Demo tier；公共无 key 调用约 5-15/min 不稳定，故必须用 Demo key） |
| Cache TTL | **15 min** |
| 字段 | `data.market_cap_percentage.btc`, `.eth` / `data.total_market_cap.usd` / `data.market_cap_change_percentage_24h_usd` |
| 实测响应 | BTC.D=57.31%, ETH.D=10.79%, total_mcap=$2.69T, 24h change=+2.58% |

**为什么 15min**：crypto 市场 24/7 实时变动，30min 在剧烈行情中过长。15min cache 实际触发的 API 调用量远低于 30 req/min 限流。

**为什么必须用 Demo key**：CoinGecko 公共无 key 限流约 5-15 req/min 且不稳定（2024 后大幅收紧）；Demo tier（免费注册）30 req/min 稳定，与其他源（FRED/AV/SoSoValue）的 key-based 模式一致。

### 2.2 FRED — US 宏观时间序列

| 项目 | 值 |
|---|---|
| 端点 | `GET https://api.stlouisfed.org/fred/series/observations?series_id=X&api_key=Y&file_type=json&limit=3&sort_order=desc` |
| Auth | query string `api_key`（注册免费） |
| 限流 | 120 req/min（2 req/sec）|
| Cache TTL | **6h** |
| Series 清单 | `DTWEXBGS` (USD Trade-Weighted Broad), `VIXCLS` (VIX), `DGS10` (10Y), `T10Y2Y` (2s10s), `T10YIE` (10Y inflation expectation) |

**为什么 5 个 series**（不含 `DFF`）：DFF 仅在 FOMC 变动（约 8 次/年），cycle 间几乎为常量，每个 cycle 输出是噪音。

**为什么 6h**：FRED 时序日级更新，6h cache 足够新；5 series × 4 refreshes/day = 20 calls/day，远低于 120 req/min。

**关于 USD Index 的选择**（重要）：
- **`DTWEXBGS` 是 Fed 宽口径贸易加权美元指数（26 货币，基期 2006=100），不是 ICE DXY（市场默认"DXY"，6 货币，基期 1973=100）**
- ICE DXY 没有免费 API（属 ICE 专有指数），FRED 上没有完全等价 series
- DTWEXBGS 当前数值（~118-120）高于 ICE DXY（~100），因基期不同
- **短期方向通常一致（同向 USD 强弱），但幅度不必相等**（不同货币篮子使涨跌弹性不同）；agent 看 DTWEXBGS 适合判断 USD 方向 / 趋势变化，不适合直接套用 ICE DXY 的关键位（如"100 整数关口"）
- **agent prompt + 工具输出必须明确标识为 "USD Index (FRED Broad TW)"，避免误读为 ICE DXY**

**实测响应**（smoke test 在 2026-04-18 跑，**各 series 报告延迟不同**）：
- DTWEXBGS = 118.86，最新观测日 **2026-04-10**（FRED 对 trade-weighted 系列有较长报告延迟）
- VIXCLS = 17.94，最新观测日 **2026-04-16**
- DGS10 = 4.32%，最新观测日 **2026-04-16**
- → 工具输出每个值带 `(as of YYYY-MM-DD)` 标注让 agent 知数据真实日期

### 2.3 Alpha Vantage — US 美股报价

| 项目 | 值 |
|---|---|
| 端点 | `GET https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=X&apikey=Y` |
| Auth | query string `apikey`（注册免费） |
| 限流 | **1 req/sec burst + 25 req/day** |
| Cache TTL | **时段感知**（详见 §5.2） |
| 字段 | `Global Quote.05. price`, `.10. change percent`, `.07. latest trading day` |

**契约漂移**：限流时返回 HTTP 200 + body 含 `Information` 字段（**不是** 4xx）。Client 必须解析此字段当作软错误（`RateLimitHit`）。

**实测响应（2026-04-17）**：SPY=$710.14 (+1.21%), QQQ=$648.85 (+1.31%)。

### 2.4 SoSoValue — 加密 spot ETF 流入

| 项目 | 值 |
|---|---|
| 端点 | `GET https://openapi.sosovalue.com/openapi/v1/etfs/summary-history?symbol=BTC&country_code=US` |
| Auth | header `x-soso-api-key`（**严格大小写敏感**） |
| 限流 | 20 req/min, 100,000 req/月 |
| Cache TTL | **4h** |
| 字段 | `data[].date`, `.cum_net_inflow`, `.total_net_assets`, `.total_net_inflow`, `.total_value_traded` |
| 历史窗口 | **限最近 30 天** |
| 支持 symbol | `BTC`, `ETH`, `SOL`, `XRP`（本 spec 仅用 BTC + ETH） |

**Auth 踩坑记录**：`X-API-KEY`、`Bearer` 都返回 401 "API Key is invalid or does not exist"，**只有 `x-soso-api-key`（小写连字符）能用**。

**多行同日期问题**：API 响应同一 `date` 可能出现 2-3 行（特别是 Friday 和最近未结算日）。Smoke test（2026-04-18）验证：
- **`cum_net_inflow` 跨行完全一致** ✓
- **`total_net_assets`（aum_usd）跨行完全一致** ✓
- **`total_net_inflow` 跨行不同**（这是问题源头，需用 cum delta 反推）
- **`total_value_traded` 跨行不同**（spec 不暴露此字段）

详见 §5.3 cum delta 算法。

**为什么 4h**：ETF 数据每日 1-2 次更新（盘后 16:00-18:00 ET），4h cache 仍能在 EOD 窗口拿到新值；2h 反而多次刷到同一陈旧数据。

### 2.5 DefiLlama — 稳定币供应

| 项目 | 值 |
|---|---|
| 端点 | `GET https://stablecoins.llama.fi/stablecoins` |
| Auth | 无 |
| 限流 | 无文档限制 |
| Cache TTL | **6h** |
| 字段 | `peggedAssets[].symbol`, `.circulating.peggedUSD`, `.circulatingPrevDay`, `.circulatingPrevWeek` |

**实测响应**：USDT=$186.62B, USDT 7d 变化 +$2.33B (+1.27%)。响应 ~250KB（包含所有稳定币 + 链分布），处理时只取 USDT/USDC 即可。

### 2.6 OKX 市场数据（已有，扩展使用）

`get_higher_timeframe_view` 直接调用现有 `MarketDataService.get_ohlcv_dataframe(symbol, timeframe, limit=250)`。

**澄清边界**：`tools_perception.py:26` 的 `candle_count` 上限 80 是 `get_market_data` **工具层**的 token 保护（防止 LTF 大表把 prompt 撑爆），**底层 `MarketDataService.get_ohlcv_dataframe()` 的 `limit` 参数没有上限约束**。新工具不经过 `get_market_data`，直接调 service 即可。**service 层不需要任何修改**。

OKX `bar=1D&limit=300` 实测可用（响应 300 根日线），ccxt 透传，无新依赖。

### 2.7 备选源对比 — 已考虑并拒绝

设计前对比了多个候选源，下表记录拒绝理由（避免评审员重复提问）：

| 候选源 | 拟覆盖 | 拒绝理由 |
|---|---|---|
| **Twelve Data** | 替代 Alpha Vantage（800 req/day vs 25） | 避免重新走一轮 verification；Alpha Vantage 已 smoke test 通过，时段感知 cache 后 25/day 够用；切换需重测准确性、 rate limit、auth |
| **Finnhub** | 同上 | 同上 |
| **CoinGlass** | ETF flows / 交易所 netflow / 爆仓数据 | **无免费 API 档**（HOBBYIST $29/mo 起）；与 N3"全免费"目标冲突；可考虑作为 N6 候选（付费补 netflow + liquidation） |
| **Farside Investors** | ETF flows（HTML scrape） | **Cloudflare 拦截（HTTP 403）**，加浏览器 UA 也过不去 challenge；与 N2 CryptoPanic 同类风险 |
| **Yahoo Finance / yfinance** | DXY / SPY / QQQ | **数据中心 IP 被 429 封**；服务器侧不可用；本地能跑不代表生产能跑 |
| **CryptoQuant / Glassnode** | 链上活跃度 / 交易所 netflow / 巨鲸 | $30-799/mo 付费；本轮聚焦免费源；可考虑 N6 |
| **Whale Alert** | 大额转账 | 免费档 100 calls/day 偏紧；信号边际价值未验证；暂缓 |
| **CoinMarketCap `/global`** | 替代 CoinGecko BTC.D | 与 CoinGecko 数据基本等价，无 verify 后切换收益 |
| **Alternative.me F&G**（已用） | crypto sentiment | 已在 N2 引入，不重复 |

**选源原则**（写下作为后续议题的对照）：
1. **必须有免费档**（与 N3 目标一致）
2. **必须能从服务器 IP 访问**（排除 yfinance）
3. **必须不依赖 HTML scrape**（N2 教训：CryptoPanic 替换为 CoinDesk）
4. **必须实测端点拿到目标字段**（避免 N2 CryptoPanic 同类返工）
5. **必须有官方/权威性背书**（FRED/SoSoValue/DefiLlama 都属业内标准源）

---

## 3. 工具设计

### 3.1 `get_higher_timeframe_view`

**签名**：
```python
async def get_higher_timeframe_view(
    deps: TradingDeps,
    timeframe: Literal["4h", "1d", "1w", "1M"],
) -> str
```

**timeframe 选择**：覆盖从 4h（与默认 5m/15m 主时间框比例 ~16-48x）到 1M（极远尺度）的常用 HTF 选项。Position trader 用 4h 桥接 LTF↔1d，swing trader 用 1d/1w/1M 看大结构。

**输出格式**：
```
=== Higher Timeframe View (1d, BTC/USDT:USDT) ===
Current Price: 75,234.50

=== MA Distances ===
MA50:  72,108.30 (price +4.3%)
MA100: 68,452.10 (price +9.9%)
MA200: 61,890.45 (price +21.6%)

=== Range Position ===
100-period High: 78,920.00 (32 days ago)
100-period Low:  52,340.00 (78 days ago)
Current price within range: 86.1%

20-period High: 78,920.00
20-period Low:  71,500.00
20-period range width: 10.4%
```

**输出约束**：
- 无主观标签（不出现 "uptrend"/"strong"/"upper third" 等）
- 所有距离/位置都是**百分比数字**，不分类
- ~250 tokens
- 公式：
  - "MA Distance" = `(price - ma) / ma × 100`
  - "Current price within range" = `(price - low) / (high - low) × 100`，0% 在最低、100% 在最高
  - "20-period range width" = `(high - low) / low × 100`（以低点为基准，与"涨幅"语义一致）

**Service 调用**：
```python
df = await deps.market_data.get_ohlcv_dataframe(deps.symbol, timeframe, limit=250)
ma50 = df.close.rolling(50).mean().iloc[-1]
ma100 = df.close.rolling(100).mean().iloc[-1]
ma200 = df.close.rolling(200).mean().iloc[-1]

# Range positions + 距今"周期数"（与 timeframe 单位一致）。
# 用 reset_index(drop=True) 确保是 0-based 整数索引，防御未来 market_data 重构
# 改用 timestamp 索引时这段代码失效。
last_100 = df.iloc[-100:].reset_index(drop=True)
hi100_idx = last_100["high"].idxmax()  # 0..99
lo100_idx = last_100["low"].idxmin()
hi100 = last_100["high"].max()
lo100 = last_100["low"].min()
hi100_periods_ago = 99 - hi100_idx     # positions from end
lo100_periods_ago = 99 - lo100_idx

last_20 = df.iloc[-20:]
hi20, lo20 = last_20["high"].max(), last_20["low"].min()

# 渲染 label：根据 timeframe 选单位（避免误用 days）
_UNIT_LABEL = {"4h": "4h-bars", "1d": "days", "1w": "weeks", "1M": "months"}
unit = _UNIT_LABEL[timeframe]
hi_label = f"{hi100_periods_ago} {unit} ago"
lo_label = f"{lo100_periods_ago} {unit} ago"
```

**降级**：OKX fetch 失败 → `"Higher timeframe view: temporarily unavailable"`

### 3.2 `get_macro_context`

**签名**：
```python
async def get_macro_context(deps: TradingDeps) -> str
```

**输出格式**：
```
=== Crypto Market ===
BTC.D: 57.31% | ETH.D: 10.79% | Total Mcap: $2.69T (24h: +2.58%)

=== US Macro (FRED) ===
USD Index (Broad TW): 118.86 (as of 2026-04-10)
VIX: 17.94 (as of 2026-04-16)
10Y Treasury: 4.32% (as of 2026-04-16)
2s10s Spread: +0.06% (as of 2026-04-16)
10Y Inflation Expectation: 2.43% (as of 2026-04-16)

=== US Equities (Alpha Vantage) ===
SPY: $710.14 (24h: +1.21%)
QQQ: $648.85 (24h: +1.31%)
```

**重要**：USD Index 标签明确为 "Broad TW"（Trade-Weighted），**不是** ICE DXY。详见 §2.2。

**输出约束**：
- 无主观标签（不出现 "slightly positive"/"strong dollar" 等）
- 每个 FRED 值带 `as of` 日期（agent 知数据日期，便于判断新鲜度）
- ~200 tokens

**部分降级**：三个 sub-source（CG / FRED / AV）独立。任一坏只影响对应 section，其他 section 正常输出；该 section 显示 `temporarily unavailable`。全坏时整个工具返回 `"Macro context: all sources temporarily unavailable"`。

### 3.3 `get_etf_flows`

**签名**：
```python
async def get_etf_flows(deps: TradingDeps, days: int = 7) -> str
    # service 内部 clamp days 到 [1, 14]：
    # - 上限 14：留余量给 SoSoValue 30 天历史窗口（含 multi-row dedup 后实际 ~25-28 distinct dates）
    # - 下限 1：避免无意义 0/负值
    # - 默认 7：跨一个完整交易周
```

**输出格式**：
```
=== BTC Spot ETF Flows (US) ===
2026-04-17: +$663.91M (cum: $57.74B)
2026-04-16: +$26.05M
2026-04-15: +$186.03M
2026-04-14: +$411.50M
2026-04-13: -$291.11M
2026-04-10: +$240.42M
2026-04-09: +$358.17M
7-day net: +$1,594.97M

=== ETH Spot ETF Flows (US) ===
2026-04-17: +$83.45M (cum: $14.23B)     ← 数值仅示意，ETH ETF 未做 smoke test
...
7-day net: +$427.68M

Note: Past 7 trading days (weekends/holidays excluded).
Note: Issuer-reported; today's value may be revised T+1.
```

**输出约束**：
- 仅展示 BTC + ETH 两个市场
- footer 是**运营事实**（数据更新规律），不是可信度判断
- ~300 tokens

**关键算法**：cum delta 反推日流（详见 §5.3）

**部分降级**：BTC / ETH 独立。任一坏不影响另一个 section。

### 3.4 `get_stablecoin_supply`

**签名**：
```python
async def get_stablecoin_supply(deps: TradingDeps) -> str
```

**输出格式**：
```
=== Stablecoin Supply ===
USDT: $186.62B (7d: +$2.33B, +1.27%)
USDC: $42.18B (7d: +$0.51B, +1.22%)        ← USDC/Total 数值仅示意，未单独 smoke test
Total Stablecoin Mcap: $319.61B (7d: +$3.85B, +1.22%)
```

**输出约束**：
- 仅展示 USDT + USDC + 总计（占 ~90%）
- 7d 变化既给绝对值（$）也给百分比（%）
- 无 narrative 语言（不出现 "dry powder"/"capital entering" 等）
- ~80 tokens

### 3.5 三态返回契约（沿用 N2 §3.5）

| Service 返回 | Tool 输出 |
|---|---|
| 完整数据 | 正常 sections |
| 空容器（[]） | `"No data."` 或省略对应 section |
| `None` | `"<Domain>: temporarily unavailable"` |

子源独立的工具（`get_macro_context`, `get_etf_flows`）：每个 section 独立判断三态。

### 3.6 运营事实呈现位置原则

不同源都有"运营事实"（agent 解读数据时需要知道的非业务事实，如更新规律、修订机制、数据源属性）。**为避免日后摇摆，明确分配原则**：

| 事实类型 | 呈现位置 | 例子 |
|---|---|---|
| **数据时效**（每个数据点的"截至日期"） | 输出值旁的 `(as of YYYY-MM-DD)` 标注 | FRED 每个 series 的 latest observation date |
| **未来数据会变**（agent 当场必须知道才不会误读） | **工具 footer**（同一工具调用上下文内可见） | SoSoValue ETF "may be revised T+1" |
| **数据源属性 / 更新频率**（背景知识） | Layer 1 prompt 描述（一次性常驻） | "FRED data has daily granularity"；"sourced from on-chain data via DefiLlama" |
| **可信度判断 / 操纵风险**（属于价值判断） | **不引入**（→ N4 议题） | 不在 N3 范围 |

**判断准则**：footer 仅当 agent 需要 *在工具调用上下文内* 知道才能正确解读时使用；可作为时效标注的优先用 `as of`；属于背景知识的归 prompt。

---

## 4. 数据模型

### 4.1 `src/integrations/macro/models.py`

```python
@dataclass(frozen=True)
class FREDObservation:
    series_id: str
    date: str          # ISO date "YYYY-MM-DD"
    value: float

@dataclass(frozen=True)
class EquityQuote:
    symbol: str
    price: float
    change_pct: float       # 24h, e.g. +1.21
    latest_trading_day: str # ISO date

@dataclass(frozen=True)
class MacroSnapshot:
    """完整宏观快照 — sub-source 各自可能为 None。
    
    注意：DXY 字段命名为 'usd_index_broad_tw' 而非 'dxy'，避免与 ICE DXY 混淆
    （详见 §2.2 USD Index 选择说明）。
    """
    # CoinGecko /global
    btc_dominance: float | None
    eth_dominance: float | None
    total_mcap_usd: float | None
    mcap_change_24h_pct: float | None
    
    # FRED（5 个）
    usd_index_broad_tw: FREDObservation | None  # DTWEXBGS, NOT ICE DXY
    vix: FREDObservation | None
    treasury_10y: FREDObservation | None
    spread_10y_2y: FREDObservation | None       # T10Y2Y series; "10Y minus 2Y"，避免方向歧义
    inflation_10y: FREDObservation | None
    
    # Alpha Vantage
    spy: EquityQuote | None
    qqq: EquityQuote | None
```

### 4.2 `src/integrations/crypto_etf/models.py`

```python
@dataclass(frozen=True)
class ETFFlowEntry:
    date: str                # ISO date "YYYY-MM-DD"
    net_inflow_usd: float    # 当日净流入（USD），可负
    cumulative_usd: float    # 截至当日累计净流入
    aum_usd: float           # 当日 AUM（total net assets）
    # SoSoValue 还提供 total_value_traded（成交额），暂不暴露 —
    # ETF 成交量与基础资产价格关系弱（受 NAV 套利等因素影响），
    # 对 swing 决策边际价值不明；如未来需要可加字段
```

### 4.3 `src/integrations/onchain/models.py`

```python
@dataclass(frozen=True)
class StablecoinSnapshot:
    symbol: str           # "USDT" / "USDC"
    circulating_usd: float
    change_7d_usd: float
    change_7d_pct: float

@dataclass(frozen=True)
class StablecoinTotal:
    total_circulating_usd: float
    total_change_7d_usd: float
    total_change_7d_pct: float
```

---

## 5. 实现方案

### 5.1 新增文件

```
src/integrations/macro/
  __init__.py
  models.py              ← FREDObservation, EquityQuote, MacroSnapshot
  fred.py                ← FREDClient
  alpha_vantage.py       ← AlphaVantageClient（含时段感知 TTL 计算 + Information 软错误检测）
  cg_global.py           ← CoinGeckoGlobalClient
  service.py             ← MacroService

src/integrations/crypto_etf/
  __init__.py
  models.py              ← ETFFlowEntry
  sosovalue.py           ← SoSoValueClient（含 cum delta 算法）
  service.py             ← CryptoEtfService

src/integrations/onchain/
  __init__.py
  models.py              ← StablecoinSnapshot, StablecoinTotal
  defillama.py           ← DefiLlamaClient
  service.py             ← OnchainService
```

### 5.2 时段感知 TTL（Alpha Vantage）

```python
from datetime import datetime
from zoneinfo import ZoneInfo

_NY = ZoneInfo("America/New_York")

def alpha_vantage_ttl_seconds() -> float:
    """Time-of-day aware cache TTL for Alpha Vantage SPY/QQQ.
    
    - Sat/Sun: 12h (data static, conserve budget)
    - Weekday market open (9:30 ET - 16:00 ET): 30min (catch intraday moves)
    - Weekday pre/after market: 4h
    
    NYSE holidays not handled — weekday holidays will use the short TTL based on
    time-of-day, wasting a few API calls on static data. Acceptable for now;
    if observed budget pressure becomes an issue, add a holiday calendar
    (e.g. pandas_market_calendars or hardcoded 2026 NYSE holiday list).
    """
    now_et = datetime.now(_NY)
    if now_et.weekday() >= 5:
        return 12 * 3600.0
    hour_min = now_et.hour + now_et.minute / 60.0
    if 9.5 <= hour_min < 16.0:
        return 30 * 60.0
    return 4 * 3600.0
```

### 5.3 SoSoValue cum delta 算法

```python
async def get_etf_flows(self, symbol: str, days: int = 7) -> list[ETFFlowEntry] | None:
    """Compute daily flows from cumulative inflows (multi-row safe).

    SoSoValue API may return multiple rows per date (especially Fridays + most
    recent unsettled day). Smoke test verified all such rows share identical
    `cum_net_inflow` AND `total_net_assets`; only `total_net_inflow` and
    `total_value_traded` differ. Computing today.cum - yesterday.cum gives the
    canonical daily flow without depending on undocumented row ordering.
    """
    days = max(1, min(days, 14))  # clamp to [1, 14]
    raw = await self._cache.get_or_fetch(
        f"etf:{symbol}", _ETF_TTL,
        lambda: self._client.fetch_summary_history(symbol),
    )
    if raw is None:
        return None
    
    # Step 1: dedup by date — cum_net_inflow AND total_net_assets are
    # cross-row consistent (verified §2.4), so first occurrence is correct.
    seen: dict[str, dict] = {}
    for r in raw:
        seen.setdefault(r["date"], r)
    
    # Step 2: descending by date.
    sorted_desc = sorted(seen.values(), key=lambda x: x["date"], reverse=True)
    
    # Step 3: need days+1 entries to compute `days` deltas.
    if len(sorted_desc) < days + 1:
        return None  # 数据不足
    
    flows: list[ETFFlowEntry] = []
    for i in range(days):
        today = sorted_desc[i]
        yest = sorted_desc[i + 1]
        flows.append(ETFFlowEntry(
            date=today["date"],
            net_inflow_usd=float(today["cum_net_inflow"]) - float(yest["cum_net_inflow"]),
            cumulative_usd=float(today["cum_net_inflow"]),
            aum_usd=float(today["total_net_assets"]),
        ))
    return flows
```

### 5.4 Alpha Vantage 软错误检测 + burst 节流

```python
import asyncio
import time

class AlphaVantageClient:
    BASE = "https://www.alphavantage.co/query"
    _MIN_INTERVAL = 1.1  # 1 req/sec hard limit + 100ms safety margin
    
    def __init__(self, http: httpx.AsyncClient, api_key: str):
        self._http = http
        self._api_key = api_key
        self._last_fetch_at: float = 0.0
    
    async def fetch_quote(self, symbol: str) -> EquityQuote:
        # Enforce 1 req/sec to avoid 'Information' soft limit on burst.
        # Only sleeps when an actual HTTP call is about to happen — cache hits
        # in MacroService never reach this method.
        # Note: MacroService._fetch_av_all currently calls SPY then QQQ serially,
        # so this throttle is partly redundant in current usage. Kept as defensive
        # measure: if a future refactor parallelizes per-symbol fetches, this
        # client-level throttle remains the last line of defense against soft limit.
        elapsed = time.monotonic() - self._last_fetch_at
        if elapsed < self._MIN_INTERVAL:
            await asyncio.sleep(self._MIN_INTERVAL - elapsed)
        
        try:
            resp = await self._http.get(self.BASE, params={
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": self._api_key,
            })
        finally:
            self._last_fetch_at = time.monotonic()
        
        resp.raise_for_status()
        data = resp.json()
        
        # Alpha Vantage 限流 / 错误时返回 HTTP 200 + body 含 "Information" / "Note"。
        # 必须当作 RateLimitHit，让 TTLCache 走 stale-fallback 流程。
        # （即便有 _MIN_INTERVAL 节流，daily quota 25/day 仍可能触发，需保留检测。）
        if "Information" in data or "Note" in data:
            msg = data.get("Information") or data.get("Note")
            raise RateLimitHit(f"Alpha Vantage soft limit: {msg}")
        
        if "Global Quote" not in data:
            raise ValueError(f"Unexpected AV response shape: {list(data.keys())}")
        
        return EquityQuote.from_av(data["Global Quote"])
```

### 5.5 Service 设计 — MacroService 示例

```python
# Cache TTLs (seconds) — codified from §2 design
_CG_TTL = 900.0           # 15 min
_FRED_TTL = 21600.0       # 6 h

class MacroService:
    """Aggregates CG /global + FRED + Alpha Vantage with per-source caching."""
    
    def __init__(
        self,
        fred_key: str,
        av_key: str,
        cg_key: str,                           # CoinGecko Demo key (required, see §2.1)
        http: httpx.AsyncClient | None = None,
    ):
        self._http = http or httpx.AsyncClient(timeout=5.0)
        self._owns_http = http is None
        self._cache = TTLCache()
        
        self._cg = CoinGeckoGlobalClient(self._http, cg_key)
        self._fred = FREDClient(self._http, fred_key)
        self._av = AlphaVantageClient(self._http, av_key)
    
    async def get_snapshot(self) -> MacroSnapshot:
        """Returns MacroSnapshot with sub-source independence — failed source = None field."""
        cg_data, fred_data, av_data = await asyncio.gather(
            self._fetch_cg(),
            self._fetch_fred_all(),
            self._fetch_av_all(),
            return_exceptions=True,
        )
        # ... assemble MacroSnapshot, treating Exceptions as None ...
    
    async def _fetch_fred_all(self) -> dict[str, FREDObservation | None]:
        """Fetch 5 series in parallel (FRED rate limit is generous)."""
        series = ["DTWEXBGS", "VIXCLS", "DGS10", "T10Y2Y", "T10YIE"]
        results = await asyncio.gather(*[
            self._cache.get_or_fetch(f"fred:{s}", _FRED_TTL,
                                     lambda s=s: self._fred.fetch_latest(s))
            for s in series
        ], return_exceptions=True)
        return {
            s: (r if not isinstance(r, Exception) else None)
            for s, r in zip(series, results)
        }
    
    async def _fetch_av_all(self) -> dict[str, EquityQuote | None]:
        """SPY + QQQ serial (1 req/sec limit)."""
        result = {}
        for sym in ["SPY", "QQQ"]:
            ttl = alpha_vantage_ttl_seconds()
            try:
                result[sym] = await self._cache.get_or_fetch(
                    f"av:{sym}", ttl,
                    lambda s=sym: self._av.fetch_quote(s),
                )
            except RateLimitHit:
                result[sym] = None  # cache 已无 stale，降级
            except Exception:
                logger.warning("AV fetch failed for %s", sym, exc_info=True)
                result[sym] = None
        return result
    
    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()
```

`CryptoEtfService` 和 `OnchainService` 结构类似，更简单（单一 client）。

### 5.6 配置变更（`src/config.py`）

```python
class MacroConfig(BaseModel):
    enabled: bool = True
    fred_api_key: str = ""              # from env FRED_API_KEY
    alpha_vantage_api_key: str = ""     # from env ALPHA_VANTAGE_API_KEY
    coingecko_demo_api_key: str = ""    # from env COINGECKO_DEMO_API_KEY

class CryptoEtfConfig(BaseModel):
    enabled: bool = True
    sosovalue_api_key: str = ""         # from env SOSOVALUE_API_KEY

class OnchainConfig(BaseModel):
    enabled: bool = True

class Settings(BaseModel):
    # ... existing fields ...
    macro: MacroConfig = MacroConfig()
    crypto_etf: CryptoEtfConfig = CryptoEtfConfig()
    onchain: OnchainConfig = OnchainConfig()
```

`load_settings` 增加 env override 逻辑（仿现有 OKX key 模式）。**4 个 env vars 总计**：`FRED_API_KEY` / `ALPHA_VANTAGE_API_KEY` / `COINGECKO_DEMO_API_KEY` / `SOSOVALUE_API_KEY`。

**Key 缺失行为**（与现有 OKX 模式一致）：default `""` 让 settings 加载不报错；service 实例化也不报错；只有当工具被实际调用、HTTP 请求带空 key 出去时，upstream 返回 401/400 → service catch → tool 层显示 "temporarily unavailable"。**好处**：开发/测试时可只配部分 key 跑部分功能；**代价**：错误首次出现在 cycle 中而非启动时。如需更严格"启动期 key 校验"，可加可选 `validate_keys` 启动 hook（暂不做，N3 后视实际困扰再加）。

### 5.7 集成点（`src/cli/app.py:236` `build_services`）

**TradingDeps 字段扩展**（`src/agent/trader.py:18` 是 `class TradingDeps`，`news: object | None = None` 在 `:34`；需新增 3 个字段）：

```python
@dataclass
class TradingDeps:
    # ... existing fields ...
    news: object | None = None              # NewsService（已有）
    macro: object | None = None             # NEW: MacroService
    crypto_etf: object | None = None        # NEW: CryptoEtfService
    onchain: object | None = None           # NEW: OnchainService
    # 与 news 同样 typed as object 避免 circular import
```

**Startup**（仿 N2 fix `9a81663` 模式，在 `src/cli/app.py:236` `build_services` 内 — **以下仅展示新增逻辑片段**，实际签名是 `build_services(result, engine, session_id, sc, settings)`）：
```python
def build_services(result, engine, session_id, sc, settings):
    # ... existing services（exchange / deps / agent / budget）...
    
    if settings.macro.enabled:
        macro = MacroService(
            fred_key=settings.macro.fred_api_key,
            av_key=settings.macro.alpha_vantage_api_key,
            cg_key=settings.macro.coingecko_demo_api_key,
        )
    else:
        macro = None
    
    if settings.crypto_etf.enabled:
        crypto_etf = CryptoEtfService(api_key=settings.crypto_etf.sosovalue_api_key)
    else:
        crypto_etf = None
    
    if settings.onchain.enabled:
        onchain = OnchainService()
    else:
        onchain = None
    
    deps = TradingDeps(..., macro=macro, crypto_etf=crypto_etf, onchain=onchain)
```

**Shutdown 顺序**（精确插入位置见下；`src/cli/app.py` 现有结构是 try/finally 内 close exchange + news，finally 块外做 session status update + sc.close()）：

```
现有结构：
  finally 块（line 462-470）：
    1. exchange.close()           ← line 463
    2. deps.news.close()          ← line 466-470
  finally 块外：
    3. session status update      ← line 472-477
    4. sc.close()                 ← line 479

修改后（3 个新 close 全部插在 finally 块内、news close 之后、session update 之前）：
  finally 块内：
    1. exchange.close()           ← 现有
    2. deps.news.close()          ← 现有
    3. deps.macro.close()         ← NEW
    4. deps.crypto_etf.close()    ← NEW
    5. deps.onchain.close()       ← NEW
  finally 块外：
    6. session status update      ← 现有，不变
    7. sc.close()                 ← 现有，不变
```

**关键**：3 个新 close 必须在 finally 块内（保证异常路径也执行），不能放到 sc.close() 旁边。

每个新 close 都用 try/except + warning log（仿现有 news close 模式）：

```python
if deps.macro is not None:
    try:
        await deps.macro.close()
    except Exception:
        logger.warning("Failed to close macro service", exc_info=True)
# ... 同样模式 for crypto_etf 和 onchain ...
```

**Tool registration**（`src/agent/trader.py`）：
```python
@agent.tool
async def get_higher_timeframe_view(...): ...
@agent.tool
async def get_macro_context(...): ...
@agent.tool
async def get_etf_flows(...): ...
@agent.tool
async def get_stablecoin_supply(...): ...
```

### 5.8 Verification Recipe（实施前必跑）

**Step 1**：注册 4 个免费 API key 并写入 `.env`：

| 源 | 注册页 | env var |
|---|---|---|
| FRED | https://fredaccount.stlouisfed.org/apikeys | `FRED_API_KEY` |
| Alpha Vantage | https://www.alphavantage.co/support/#api-key | `ALPHA_VANTAGE_API_KEY` |
| CoinGecko Demo | https://www.coingecko.com/en/api/pricing → "Create Demo Account" | `COINGECKO_DEMO_API_KEY` |
| SoSoValue | https://sosovalue.com/developer | `SOSOVALUE_API_KEY` |

**Step 2**：跑这组 smoke test（应全部返回真实数据）：

```bash
set -a; source .env; set +a

# FRED 5 个 series 各跑一次
for s in DTWEXBGS VIXCLS DGS10 T10Y2Y T10YIE; do
  echo "=== FRED $s ==="
  curl -s "https://api.stlouisfed.org/fred/series/observations?series_id=$s&api_key=$FRED_API_KEY&file_type=json&limit=1&sort_order=desc"
done

# Alpha Vantage SPY + QQQ（注意：必须 ≥1 秒间隔，否则返回 "Information" 软错误）
curl -s "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=SPY&apikey=$ALPHA_VANTAGE_API_KEY"
sleep 2
curl -s "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=QQQ&apikey=$ALPHA_VANTAGE_API_KEY"

# CoinGecko /global（必须用 Demo key，公共无 key 限流不稳定）
curl -s -H "x-cg-demo-api-key: $COINGECKO_DEMO_API_KEY" "https://api.coingecko.com/api/v3/global" | python3 -c "import json,sys; d=json.load(sys.stdin)['data']; print(f'BTC.D={d[\"market_cap_percentage\"][\"btc\"]:.2f}%')"

# SoSoValue BTC ETF（重点：复现 multi-row 现象，验证 cum delta 必要性）
curl -s -H "x-soso-api-key: $SOSOVALUE_API_KEY" \
  "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history?symbol=BTC&country_code=US" \
  | python3 -c "
import json, sys
from collections import Counter
d = json.load(sys.stdin)['data']
c = Counter(r['date'] for r in d)
multi = [k for k, v in c.items() if v > 1]
print(f'Total rows: {len(d)}, distinct dates: {len(c)}, multi-row dates: {len(multi)}')
"

# SoSoValue ETH ETF（spec 未实测，作为 implementer 第一步必跑）
curl -s -H "x-soso-api-key: $SOSOVALUE_API_KEY" \
  "https://openapi.sosovalue.com/openapi/v1/etfs/summary-history?symbol=ETH&country_code=US" | head -c 500

# DefiLlama 稳定币（同时验证 USDT + USDC）
curl -s "https://stablecoins.llama.fi/stablecoins" \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)['peggedAssets']
for sym in ['USDT', 'USDC']:
    a = next((x for x in d if x['symbol']==sym), None)
    if a:
        print(f'{sym}={a[\"circulating\"][\"peggedUSD\"]/1e9:.2f}B (prevWeek diff: {(a[\"circulating\"][\"peggedUSD\"]-a[\"circulatingPrevWeek\"][\"peggedUSD\"])/1e9:+.2f}B)')
    else:
        print(f'{sym}: NOT FOUND')
"
```

**Step 3**：结果检查清单：
- [ ] FRED 5 series 全部返回 `observations` 数组（非 401）
- [ ] AV SPY/QQQ 返回 `Global Quote` 字段（非 `Information`）
- [ ] CoinGecko 返回 BTC.D 数值（用 Demo key header）
- [ ] SoSoValue BTC 响应里 multi-row dates ≥ 1（验证 cum delta 必要性）
- [ ] SoSoValue ETH 端点响应结构与 BTC 一致
- [ ] DefiLlama 返回 USDT **AND** USDC 当前供应（两者都应在百亿美元级）+ `circulatingPrevWeek` 字段存在

任一失败 → **停止实施，调查根因后再启动**（避免 N2 风格返工）。

---

## 6. System Prompt 更新

`_build_layer1()` 末尾追加 4 段（严格 fact-only，无因果暗示）：

```
- **Higher timeframe view**: Use get_higher_timeframe_view with timeframe="4h"/"1d"/"1w"/"1M"
  to see long-period moving averages (MA50/100/200), price position within the recent
  100-period range, and structural highs/lows over a longer window than your default
  trading timeframe.

- **Macro context**: Use get_macro_context for cross-market data — BTC/ETH dominance,
  Total Crypto Market Cap (CoinGecko), USD Trade-Weighted Index (FRED DTWEXBGS — note:
  this is the Fed's broad TW index across 26 currencies, NOT the ICE DXY across 6
  currencies; absolute values differ but directional movement is highly correlated),
  VIX, 10Y Treasury yield, 2s10s spread, 10Y inflation expectation (FRED), and SPY/QQQ
  closing quotes (Alpha Vantage). FRED data has daily granularity; SPY/QQQ are equity
  ETFs with NYSE trading-hour quotes.

- **ETF flows**: Use get_etf_flows for daily net flow data of US-traded BTC and ETH
  spot ETFs over the past 7 days, plus cumulative AUM. Today's value may be revised T+1.

- **Stablecoin supply**: Use get_stablecoin_supply for current USDT/USDC total supply
  and 7-day changes, sourced from on-chain data via DefiLlama.
```

**风格**：每条只描述工具是什么、返回什么数据、操作约束。**不暗示决策方向**，把"如何解读"完全交给 Layer 2 思维框架 + agent 自主判断。

---

## 7. Token 成本分析

### 7.1 单次调用成本

| 工具 | 输出 tokens |
|---|---|
| `get_higher_timeframe_view` | ~250 |
| `get_macro_context` | ~200 |
| `get_etf_flows` | ~300 |
| `get_stablecoin_supply` | ~80 |

### 7.2 System prompt 增量（一次性）

Layer 1 新增 4 段描述：~280-400 tokens（每个 cycle 都常驻）。**估算偏差**：英文段含技术名词、连字符、source 标注会让实际 token 数偏向高位；macro context 段因含 DXY 区分说明较长（~120 词）。按上限 400 tokens 估算预算更保守。

### 7.3 每日成本估算（15 分钟 cycle = 96 cycles/day 上限）

**注**：96 cycles 假设默认 15min wake interval 全程。实际 agent 通过 `set_next_wake` 可主动拉长（市场静默时）或缩短（高波动时），下表为粗略中位数估计。

| 工具 | 预期频率 | 每日 tokens |
|---|---|---|
| `get_higher_timeframe_view` | 1-2 次/天 | ~500 |
| `get_macro_context` | 4-8 次/天 | ~1600 |
| `get_etf_flows` | 1-3 次/天 | ~900 |
| `get_stablecoin_supply` | 1-2 次/天 | ~160 |
| **总计输出 tokens/day** | | **~3200** |
| **System prompt 常驻 × cycles** | | 280-400 × 96 = **26,880-38,400 tokens/day**（默认 15min cycle 上限；上限按 §7.2 保守估算） |

**关于 prompt caching**（提醒，不计入预算）：Anthropic 标准 prompt cache TTL = 5 min，scheduler 默认 15min cycle → 每次 cycle 都落在 cache miss 窗口，cache 命中率 ≈ 0；cache write 还有 1.25× 加价，所以**默认配置下启用 caching 反而略涨价**。若要从 caching 省钱，需用 1-hour 扩展 cache（beta 特性，需显式开启），且仅在 cycle 间隔 ≪ 1h 时有意义。本 spec 不依赖 caching 节省 — 280 tokens/cycle 本身不大，无需粉饰。

### 7.4 API 调用量（与 N2 对齐）

| 源 | TTL | 理论 max（单 symbol，假设每 cycle 都查） | 实际预期（取决于 agent 调用频率） |
|---|---|---|---|
| CoinGecko `/global` | 15min | 96 | 4-8（agent 调 macro 时） |
| FRED（5 series） | 6h | 4 × 5 = 20 总 | 8-12 总 |
| Alpha Vantage（2 symbols） | 时段感知 | **工作日单 symbol 17.4**（盘中 13 + 盘外 4.4），双 symbol 工作日**理论 ~35**（极端假设：agent 在每个 cache 过期窗口都恰好调一次 macro，即每 30min 一次，远超合理频率）会超 25/day | **8-16 总**（cache miss 仅在 agent 调用时触发，非按 schedule 刷新；按 §7.3 估算 macro 4-8 次/天 → AV 8-16 次/天） |
| SoSoValue | 4h | 6 | 1-3 |
| DefiLlama | 6h | 4 | 1-2 |

**关于 Alpha Vantage 的 25/day 限额**：
- **理论上限会超**：如果 agent 每 cycle 都调用 `get_macro_context` 且每次都 cache miss，工作日双 symbol 可达 ~35 calls/day
- **实际不会超**：cache miss 仅在 agent **主动调工具**且**当前 cache 已过期**时触发；按 §7.3 估算 macro 调用 4-8 次/天 → AV 实际 ~8-16 次/天
- **超限保护**：若仍触发软限流，AV client 抛 `RateLimitHit` → TTLCache 返回 stale + 临时 30min TTL（详见 §5.4）

---

## 8. 测试策略

### 8.1 单元测试

| 文件 | 覆盖 | 数量估算 |
|---|---|---|
| `tests/test_macro_clients.py` | FREDClient / AlphaVantageClient / CoinGeckoGlobalClient 各自 mock httpx | ~25 |
| `tests/test_crypto_etf_client.py` | SoSoValueClient（多行 dedup / 401 / 429 / 字段缺失） | ~10 |
| `tests/test_onchain_client.py` | DefiLlamaClient | ~6 |
| `tests/test_macro_service.py` | 三 sub-source 独立降级 / cache 命中-miss-stale fallback | ~15 |
| `tests/test_crypto_etf_service.py` | **重点：cum delta 算法**（multi-row + 边界 + 数据不足） | ~12 |
| `tests/test_onchain_service.py` | DefiLlama 聚合 | ~6 |
| `tests/test_av_time_of_day_cache.py` | 时段感知 TTL（用 `monkeypatch.setattr` 替换 `datetime.now`，4 个时间点 + 周末；不引入 freezegun 新依赖） | ~8 |
| **小计** | | **~82** |

**关键测试用例**：

`test_av_information_field_treated_as_rate_limit`：
```python
async def test_av_information_field_is_soft_error():
    """AV returns HTTP 200 + body containing 'Information' on rate limit.
    Client must raise RateLimitHit, not return malformed Quote."""
    mock_response = {"Information": "Thank you for using Alpha Vantage..."}
    # ... mock httpx ...
    with pytest.raises(RateLimitHit):
        await client.fetch_quote("SPY")
```

`test_etf_cum_delta_with_multirow_dates`（数值取自实测响应）：
```python
async def test_etf_cum_delta_handles_multirow_friday():
    """SoSoValue may return 2-3 rows per date with identical cum but
    different daily figures. Cum delta should compute correct daily flow."""
    raw = [
        # All 3 rows for 2026-04-17 share identical cum_net_inflow.
        {"date": "2026-04-17", "cum_net_inflow": 57_739_993_739.43,
         "total_net_inflow": 663_911_366.47, "total_net_assets": 1.0145e11},
        {"date": "2026-04-17", "cum_net_inflow": 57_739_993_739.43,
         "total_net_inflow": 996_375_546.47, "total_net_assets": 1.0145e11},
        {"date": "2026-04-17", "cum_net_inflow": 57_739_993_739.43,
         "total_net_inflow": 1_617_957_506.54, "total_net_assets": 1.0145e11},
        {"date": "2026-04-16", "cum_net_inflow": 57_076_082_372.97,
         "total_net_inflow": 26_051_070.56, "total_net_assets": 9.79e10},
        # ... more days ...
    ]
    flows = await service.get_etf_flows("BTC", days=1)
    assert len(flows) == 1
    # delta = 57_739_993_739.43 - 57_076_082_372.97 = 663_911_366.46
    assert abs(flows[0].net_inflow_usd - 663_911_366.46) < 1.0
```

### 8.2 工具测试

`tests/test_perception_tools_n3.py`：
- 4 个新工具的正常输出格式
- service 返回 None → 正确显示 "temporarily unavailable"
- 部分降级（macro 三个 sub 各自 None 的组合）
- **NO-LABEL 断言**：grep 输出确认不含 `bullish|bearish|moderate|strong|signals|precedes|follows`

### 8.3 集成测试

`tests/test_n3_wiring.py`：
- `build_services` 正确实例化 3 个新 service（启用时）
- service.enabled=False → 对应 service 为 None
- TradingDeps 正确注入

`tests/test_app_lifecycle_n3.py`：
- startup 注册成功
- shutdown close 各自 service 不抛（service 内部各自 close 其 client）
- 仿 N2 fix `9a81663` 模式，service 级 close 顺序：`exchange → news → macro → crypto_etf → onchain`（每个 service 内部按需关闭其 http client / 子 client）

### 8.4 测试新增数

- 单元测试 ~82
- 工具测试 ~12-15
- 集成测试 ~6
- **总计 ~100**（与 N2 baseline 109 同量级）

---

## 9. 实现成本汇总

### 9.1 代码量估算

| 模块 | 新代码（含测试） |
|---|---|
| `src/integrations/macro/` | ~600 行 |
| `src/integrations/crypto_etf/` | ~250 行 |
| `src/integrations/onchain/` | ~200 行 |
| `src/agent/tools_perception.py` 新增 4 函数 | ~200 行 |
| `src/agent/persona.py` Layer 1 增 4 段 | ~30 行 |
| `src/config.py` 新增 3 config 类 + env override | ~50 行 |
| `src/cli/app.py` / `build_services` 集成 | ~80 行 |
| 测试（单元 + 工具 + 集成） | ~1500 行 |
| **小计** | **~2900 行** |

### 9.2 工作量

- 设计已完成（本 spec）
- 实施估算：**3-3.5 天**（与 N2 量级匹配，N2 实施约 4 天但含信源选型）
- 数据源验证已完成（不计入）

### 9.3 阶段性交付建议

可拆 2 个 PR（独立可滚回）：

1. **PR-A: macro context + HTF view** — `src/integrations/macro/` + `get_higher_timeframe_view` + `get_macro_context`
2. **PR-B: ETF flows + stablecoin** — `src/integrations/crypto_etf/` + `src/integrations/onchain/` + 对应 2 个工具

或者打包单 PR（与 N2 一致）。

**PR 切分由用户最终决定**（implementer 不应自作主张）。理由：用户偏好 review-before-commit 模式，PR 大小直接影响 review 工作量；用户对当前心智带宽和评审节奏更有把握。

---

## 10. 设计决定（已确认）

| # | 决定 | 理由 |
|---|---|---|
| 1 | 长 K 线用**新工具** `get_higher_timeframe_view`，不扩展 `get_market_data` | HTF 与 LTF 是不同 mental model；token 输出可控；MA200 仅在 HTF 有意义 |
| 2 | 模块**按业务域分 3 个**（macro / crypto_etf / onchain） | **对 N2 单一 `news/` 目录混放多个业务子域的优化**：N2 的 5 个源都属"信息事件"子类（新闻/情绪/日历/公告/状态），合一可接受；N3 的源跨度更大（金融市场 / 加密机构资金流 / 链上基本面），分立后未来扩展落点清晰、service.py 大小可控 |
| 3 | 仅 SoSoValue ETF 加**运营事实 footer**（"may be revised T+1"），其他源不加 footer | 不暗示可信度判断（→ N4）；ETF 修订是必须告知 agent 的事实 |
| 4 | FRED **5 个 series**（不含 DFF） | DFF 几乎为常量，每 cycle 输出是噪音 |
| 5 | CoinGecko TTL **15min**（不是 30min） | crypto 24/7 实时，30min 在剧烈行情中过长 |
| 6 | SoSoValue TTL **4h**（不是 2h） | ETF 数据每日 1-2 次更新，2h cache 多刷的是同一陈旧数据 |
| 7 | Alpha Vantage 用**时段感知 TTL**（30min / 4h / 12h） | 美股市场时段差异大；最优化 25/day budget |
| 8 | SoSoValue 多行 dedup 用 **cum delta 算法** | 不依赖 row 顺序（未文档化约定）；数学上 100% 正确 |
| 9 | 工具输出**严格 fact-only**，无 "bullish/strong/signals" 类标签 | 工具是事实查询接口，决策由 agent 自主做出 |
| 10 | Layer 1 prompt 描述**严格 fact-only**，无因果暗示 | 同上原则延伸到 prompt 层 |
| 11 | 不实现 NYSE 假日日历 | 工作日假日按时段感知逻辑会命中短 TTL 重复 fetch 静态数据，浪费 budget 量小；可接受，观察期若有预算压力再加日历（如 `pandas_market_calendars`） |
| 12 | **不引入信源可信度治理** | → N4 议题；混入 N3 会失焦 |
| 13 | **不清理 N2 现有工具的标签化输出** | → N5 议题；scope creep |
| 14 | **Layer 1 prompt 保持 4 段独立描述**（不合并为单段"market context tools"） | 4 段 × ~70 tokens = 280 tokens 一次性常驻，相对 context window 占比极小；4 个工具概念跨度大（HTF / macro / ETF / stablecoin），合并损失清晰度，token 节省微小不值得换 |
| 15 | **`get_etf_flows` 保持 BTC + ETH 双币合一**（不拆为两个工具） | 双币总输出 ~300 tokens 可控；避免增加 Layer 1 描述负担（拆为两个工具需多写一段 prompt） |
| 16 | **`get_higher_timeframe_view` 不设 timeframe 默认值，必须显式传** | 默认 1d 会让 agent 漏看其他周期；显式传强制 agent 思考"我要看哪个尺度"；增加的思考成本可接受 |

---

## 11. API 稳定性总评

| 源 | 稳定性 | 已验证 | 风险预案 |
|---|---|---|---|
| CoinGecko `/global` | ★★★★★ | ✅ smoke test | 无 auth、行业标准、长期可用 |
| FRED | ★★★★★ | ✅ smoke test 3 个 series | 美联储官方，最稳定的免费金融数据源 |
| Alpha Vantage | ★★★★ | ✅ smoke test SPY + QQQ | 商业 aggregator；唯一硬限流（25/day），cache 设计已应对 |
| SoSoValue | ★★★★ | ✅ smoke test BTC ETF | 行业标准 ETF flow 源；多行问题已敲定 cum delta 算法 |
| DefiLlama | ★★★★ | ✅ smoke test 稳定币 | DeFi 基础设施级源；无 auth 无限流 |

**已知运营事实（写入工具 footer）**：
- SoSoValue ETF 数据 T+1 可修订
- FRED 数据日级更新（盘后）
- AV 是商业 aggregator（非交易所原始数据）

**没有依赖任何 HTML scraping**（避免 N2 的 CryptoPanic / Farside 同类风险）。

---

## 12. Open Questions / Risks

下表汇总实施 / 运行期可能遇到的非阻塞风险，及检测/缓解方式。**所有 5 个数据源均已 smoke test 通过**，下列是"已知未知"。

### 12.1 数据源相关

| 风险 | 触发条件 | 影响 | 检测 / 缓解 |
|---|---|---|---|
| **SoSoValue multi-row pattern 变化** | API 内部聚合规则调整（增减分类、改返回顺序） | cum delta 仍正确（不依赖 row 顺序），但 dedup 行为可能变 | cum delta 算法已 100% 数学验证；测试覆盖 row 各种排列；行为变更不会导致数据错，最差只是 dedup 后剩 row 数变化 |
| **SoSoValue 30 天历史窗口缩短** | API 调整保留窗口 | `get_etf_flows(days=7)` 边界 case：数据不足返回 None | service 已处理（详见 §5.3）；用户感知为 "ETF flows: temporarily unavailable" |
| **AV 25 req/day budget 超用** | agent 行为异常高频调 `get_macro_context` | AV 软限流，stale 数据被服务（TTLCache 30min 兜底） | 监控 metrics（见下方埋点项）；prompt 已引导"使用 macro 不必每 cycle"；time-of-day cache 是主防线 |
| **FRED series 重命名 / 撤销** | 美联储调整 series_id（罕见但发生过） | 对应 FRED 字段降级为 None | series_id 集中在 `_FRED_SERIES` 常量字典，单点修改；service 单源降级不影响其他 |
| **DefiLlama 字段变更 / 引入限流** | 项目方调整 API 结构 | 解析失败 / 429 | client 测试覆盖字段缺失；TTLCache 兜底 stale；`OnchainConfig.enabled=false` 可一键关闭 |
| **CoinGecko free demo 限流收紧** | 30 req/min 调整为更紧 | cache miss 时偶发 429 | TTLCache 已支持 stale-fallback；15min TTL 远低于限流阈值，正常使用不会触发 |

**首次观察期建议增加 metrics 埋点**（验证"实际不会超 25/day"假设）：
- 在 `AlphaVantageClient.fetch_quote` 实际触发 HTTP 时记一次 counter
- 按 UTC 日聚合，每日生成 `av_actual_calls_yyyy-mm-dd: N` 形式 log
- **接入方式**：纯 `logger.info` 输出（不扩展 `MetricsService` — 后者是交易性能指标 return/win_rate/drawdown 的语义，与 API counter 无关）；后续若要持久化或聚合可独立加 `ApiCallCounter` 服务
- 观察期跑 7-14 天，若实际峰值 ≥ 20 → 触发收紧 cache TTL 或加假日日历
- 实施工作量：~10 行代码 + 1 个测试，可作为 N3 实施附带项

### 12.2 实施相关

| 风险 | 触发条件 | 缓解 |
|---|---|---|
| **ETH ETF 端点行为与 BTC 不一致** | SoSoValue 对 `symbol=ETH` 可能字段差异 | **§5.8 Verification recipe Step 2 包含 ETH smoke test**，实施第一步先验证 |
| **Alpha Vantage "Information" 字段在不同错误下措辞不同** | 限流 / 服务降级 / API 调整 | client 检测 `"Information" in data or "Note" in data` 双重 key（实测 N2 和文档都见过两种） |
| **时段感知 TTL 边界 case**（夏令时切换、跨日） | 美东 DST 转换日 / UTC 跨日 | 用 `ZoneInfo("America/New_York")` 自动处理 DST；测试包含 9:29/9:30/15:59/16:00 边界 |
| **MA200 计算需 ≥200 根 K 线**，OKX 极少数标的不足 | symbol 上市不足 200 天 | `get_higher_timeframe_view` fetch 失败 / MA 字段为 None；输出"insufficient data for MA200" |

### 12.3 长期演进

| 议题 | 触发条件 | 处理路径 |
|---|---|---|
| **观察期发现 agent 想看但拿不到的数据** | 比如交易所 netflow / 期权 IV / 链上活跃度 | 触发 N6（付费源补全：CoinGlass / Glassnode 等） |
| **CoinDesk 新闻 / FGI 被识别为操纵源影响决策** | 观察期 agent 对新闻反应明显异常 | 触发 N4（信源可信度治理） |
| **现有 N2 工具的标签化输出（"bullish/bearish"）影响 agent 自主判断** | 观察期识别 agent 对标签过度依赖 | 触发 N5（工具输出 + prompt 描述清理） |
| **Layer 1 prompt 累积过长**（N2 共 15 bullet，N3 +4 → ~19 bullet） | 观察期识别 agent 出现"忽略靠后工具"现象（如 set_next_wake / save_memory 调用率显著低于预期） | 触发 N7（Layer 1 整理：bullet 合并 / 工具按类别分组 / 移到独立 prompt 段） |

### 12.4 评审期决议（仅供溯源）

原 §12.4 列出的 3 个 open question 已在评审期间敲定，决策记入 §10 #14 / #15 / #16。本节保留以便追溯思考过程。
