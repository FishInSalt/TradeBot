# 数据源风险矩阵

> **目的**：系统性盘点 TradeBot 所有外部数据源的可操纵性与可靠性，产出一份工程参考。用于支持：(1) 观察期异常定位、(2) N4 prompt 级缓解方案设计、(3) 信源替换/删除决策、(4) 未来新增数据源的风险对比基线。
>
> **性质**：工程参考文档，**不是 Agent-visible 内容**，不注入 prompt。
>
> **创建**：2026-04-19，PR A (N4 全量信源审视)。

---

## 方法论

### 审视维度

每个数据源按 5 个维度评估：

| 维度 | 说明 |
|------|------|
| **数据性质** | 数据产生方的类型 —— 官方 / 受监管 / 聚合器 / 链上 / 媒体 / 交易所内部 |
| **可操纵难度** | 操纵该源的门槛等级 |
| **攻击面** | 具体可被如何污染 —— 列出 2-5 条可识别的攻击类型 |
| **检测特征** | 如何识别源正在被操纵 —— 区分 "Agent-observable"（可让 agent 直接看到的信号）与 "工程侧"（需要人工或 dashboard 监控的信号） |
| **建议动作** | 下一步处置 —— 本批可修 / 观察期记录 / N4-prompt 议题 / 替换候选 / 删除候选 |

### 可操纵难度评分

| 等级 | 门槛 | 典型案例 |
|------|------|---------|
| 🟢 高（难以操纵） | 需监管/链上/多方同时配合 | 美联储官方数据、链上原始状态 |
| 🟡 中（可操纵但成本高） | 交易所层面或大机构配合 | 交易所自定义指标、大型聚合器 |
| 🔴 低（操纵成本低） | 单点编辑 / 刷量 / 发稿即可 | 媒体头条、情绪指数、社交数据 |

### 建议动作分类

| 符号 | 含义 |
|------|------|
| 🔴 本批可修 | 本 hardening 批次（PR A 或 C）就能落实的改动，且有已识别的具体 fix |
| 🟡 N4 prompt 议题 | 需 prompt 级介入（skepticism 引导、可信度标注等），等观察期实例支撑后启动 |
| 🟢 观察期记录 | 观察期内主动收集现象数据（异常 case、误导实例），结果驱动后续决策 |
| ⚠️ 替换候选 | 若观察期出现确切问题，考虑替换为更可靠的源 |
| ❌ 删除候选 | 若观察期出现确切问题，考虑从工具库移除 |

---

## 1. OKX Exchange (via ccxt) — Market Data / Account / Derivatives

- **接入点**：`src/integrations/exchange/okx.py`（`ccxt.async_support.okx`）
- **数据范围**：ticker、OHLCV、positions、orders、balance、funding rate、open interest、long/short ratio、WebSocket fills
- **数据性质**：交易所内部（私营，受监管）

**可操纵难度**：🟡 中 — OKX 是私营交易所，对自己发布的数据有完全控制权，但同时受法律/监管/信誉约束。整体攻击成本高但非零。

**攻击面**：

1. **Ticker/OHLCV 操纵风险**：交易所可通过自营账户刷量影响短时 price/volume；历史上有交易所对自营数据"润色"的先例，但主流大所（OKX 属此类）概率极低
2. **Long/Short Ratio (LSR) 定义不透明**：OKX 自定义"散户 vs 机构"分类标准未公开；功能上可能针对账户规模阈值微调，影响 ratio 数值的可比性
3. **Funding Rate 计算**：OKX 自有公式（接近行业标准但不开源）；异常 funding（>0.1%）历史上少数情况与 OKX 内部做市行为相关
4. **WebSocket Fill 通知**：异步推送，**存在断连后漏推的可能**；已有 `_fill_callback` 但补齐机制依赖 REST 轮询

**检测特征**：

- **Agent-observable**：
  - Ticker last 价与 bid/ask mid 偏离（`ticker.last != (bid+ask)/2`）— 可能有不同步的订单簿快照
  - Funding rate 绝对值 > 0.1% 且未被 open interest 变动支撑
  - LSR 极端值（>5 或 <0.2）且与 order flow 明显不符
- **工程侧**：
  - ccxt 返回的 candle timestamp 跳变或重复
  - WebSocket 连续断连 + 重连 log

**当前缓解**：

- `_retry` 装饰器（3 次指数退避）处理网络异常
- 衍生品层 3min TTLCache 避免短期抖动放大
- Fill-first 流程（异步 fill 驱动 SL/TP 设定）降低时序依赖风险
- **SimulatedExchange** 对齐真实行为（PR#8）— 模拟环境不会放大交易所异常

**建议动作**：

- 🟢 观察期记录：LSR 与 order flow 背离的 case、funding 极端值与后续价格的关系
- 🟡 N4 prompt 议题：若观察期 LSR 误导 agent 决策，考虑在 `get_derivatives_data` 输出补充"OKX 专有分类"的可信度标注

---

## 2. OKX Announcements（公告）

- **接入点**：`src/integrations/news/okx_announcements.py`
- **数据范围**：delistings、trading updates（规则变更、参数调整）
- **数据性质**：交易所内部（公开披露）
- **Auth**：无

**可操纵难度**：🟡 中 — 公告内容由 OKX 编辑团队掌控，**发布时机**是主要操纵维度。

**攻击面**：

1. **延迟公告**：重大变更（如 delisting、清算规则调整）可能"事后发"或"做完再发"，agent 在公告前做的决策承担非对称风险
2. **选择性披露**：小规模参数调整可能仅在页面更新而无公告推送
3. **公告措辞策略性**：重大负面（如 hack）可能以技术中立措辞软化，削弱 agent 的风险感知
4. **Schema drift**：`data[0].details[*]` 嵌套结构若改变，当前 `_extract_items` 有兼容 fallback 但未覆盖所有变化

**检测特征**：

- **Agent-observable**：
  - 公告内容与市场反应时序不符（价格早于公告大幅变动）
  - 公告文本过短或过于技术化（缺乏具体影响说明）
- **工程侧**：
  - 公告 list 突然从 N 条变成 0 条 → schema drift
  - `/support/announcements` 响应结构变化

**当前缓解**：

- `_extract_items` 双布局兼容（flat + nested）
- 10min TTLCache 减少刷屏
- 按 `pTime`（publish timestamp）过滤 `lookback_hours`

**建议动作**：

- 🟢 观察期记录：公告文本与后续价格/成交变化的关系（是否存在"事后公告"）
- 🟡 N4 prompt 议题：Layer 1 可引导 "公告缺失 ≠ 安全，关注价格异动"

---

## 3. OKX System Status（维护通知）

- **接入点**：`src/integrations/news/okx_status.py`
- **数据范围**：scheduled maintenance、ongoing incidents
- **数据性质**：交易所内部（运维层披露）
- **Auth**：无

**可操纵难度**：🟡 中 — 维护时间**安排**是主要操纵维度。

**攻击面**：

1. **战术性维护窗口**：主动在不利事件前"临时维护"可阻止用户出金/平仓（历史有案例）
2. **模糊时长标注**：维护时长低估（"预计 30min"实际 3h），agent 若据此决策持仓风险错估
3. **Schema drift 风险**：Pre-work P4b 未完全确认 response 结构（flat vs nested），已有 `_extract_items` 双支持

**检测特征**：

- **Agent-observable**：
  - 维护 begin 时间与重大事件（macro 事件、衍生品结算）时间接近
  - 同一维护反复延期（end 字段频繁变更）
- **工程侧**：
  - `/system/status` 响应结构突变

**当前缓解**：

- `_extract_items` 双布局兼容
- 10min TTLCache
- Begin 时间渲染（scheduled → future, ongoing → past）清晰

**建议动作**：

- 🟢 观察期记录：维护窗口与重大 macro 事件的时序对齐、维护实际时长 vs 公告时长
- 🟡 N4 prompt 议题：Layer 1 可引导 "维护期间平仓/出金可能受阻，高仓位前验证可达性"

---

## 4. 本地技术指标计算（pandas_ta）

- **接入点**：`src/services/technical.py`（`pandas_ta` 库）
- **数据范围**：RSI / MA / MACD / BB / ATR / volume_ratio（全部本地计算，不调用外部）
- **数据性质**：衍生计算（本地）

**可操纵难度**：🟢 高 — 计算完全本地，操纵需篡改代码或库。但**库自身 bug** 是独立于"操纵"的风险维度。

**攻击面**：

1. **库 bug 级联风险**：Agent 基于指标计算做决策，错误计算会导致所有周期/币种的决策同时偏差
2. **输入数据 NaN 处理**：`compute_indicators` 对 `None` / `NaN` 有 `_last` 守卫，但对 OHLCV 异常值（负 volume、过大 price）无健全性校验
3. **BB 边界条件**：`bb_upper == bb_lower`（极罕见但可能发生在横盘极窄 BB）当前未显式处理，除零风险在 PR B §3.2 #4 改动中处理
4. **历史 PR#9 曾修过 BB/MACD bug**（commit `74e5d10`）—— 说明即使成熟库也有集成边界 bug

**检测特征**：

- **Agent-observable**：
  - 指标值与肉眼看 K 线明显不符（但 LLM 做这种视觉校验不稳定）
  - RSI/MACD 在明显趋势中给出反向信号
- **工程侧**：
  - 单元测试覆盖（当前已有）
  - 跨库交叉验证（如与 TradingView 手工对比）

**当前缓解**：

- 单元测试覆盖主要指标（`tests/services/test_technical.py`）
- `_last` / `_col` 守卫处理 NaN / 缺列
- Volume ratio 用 `iloc[-2]`（最后完成蜡烛）避免未完成期读数
- PR B §3.2 将补 BB 位置的 zero-width 边界

**建议动作**：

- 🔴 本批可修（PR B）：BB 位置输出改 "% of band width"，含 zero-width 边界处理
- 🟢 观察期记录：是否有指标计算错误的实例（RSI 跳变、MACD 与价格明显背离等）

---

## 5. CoinDesk News

- **接入点**：`src/integrations/news/coindesk.py`（Data API）
- **数据范围**：crypto news headlines + sentiment 标签
- **数据性质**：媒体
- **Auth**：无（key-less）

**可操纵难度**：🔴 低 — **本批次最高风险源**。

**攻击面**：

1. **PR-as-news**：项目方付费包装的文章混入"news" feed 难以识别（已有先例：多家 crypto 媒体曾被曝接受 sponsored content 无明确标注）
2. **Insider 抢跑**：上市/合作/监管消息可能提前泄露给特定人脉，再以"news"形式延迟披露，agent 看到时已是信息末端
3. **协调 FUD**：大户配合负面新闻发布砸盘后吸筹 —— 历史上多次出现（如 Mt.Gox 出金传言、Genesis 相关 FUD）
4. **选择性报道**：编辑倾向影响"报道什么 / 不报道什么"
5. **Sentiment 标签主观**：CoinDesk 自己给的 POSITIVE/NEGATIVE/NEUTRAL 是其内部分类，**本身就是主观判断**
6. **Schema drift**：响应字段变化（TITLE/PUBLISHED_ON/SOURCE_DATA 等），目前 `_parse` 有 `get(...)` defensive lookup

**检测特征**：

- **Agent-observable**：
  - 新闻看多但价格 0-15min 内未反应 / 反向反应 —— 经典"PR-as-news"或"协调 FUD"信号
  - 同一主题多条头条短时间内密集发布（协调放大）
  - 新闻文本包含项目方引用但无中立第三方 source
- **工程侧**：
  - 同一条消息被多次"刷新"到 feed（重复发布）

**当前缓解**：

- `/news/coindesk.py` 仅渲染 `TITLE` + `SOURCE_DATA.NAME`，**不使用 CoinDesk 的 sentiment 标签驱动决策**（仅作为 filter 参数，agent 看到的是原文）
- 15min TTL 减少刷屏影响
- `persona.py:38` 已限制 `get_market_news` 仅显示 headlines + FGI，不做解读

**建议动作**：

- 🟢 观察期记录：**主动记录 "news 看多/看空但价格未反应" 的实例**，每条 case 包含 news timestamp、content、price reaction window（5m / 15m / 1h）
- 🟡 N4 prompt 议题：基于观察期 case，Layer 1 可加 "新闻是'被报道的内容'不是'事实'，应交叉验证"
- ⚠️ 替换候选：若观察期 case 累积显示误导率 > 某阈值，考虑换用 The Block 或 Decrypt 等（单源 vs 多源交叉是 N4 议题子话题）
- ❌ 删除候选：仅当观察期发现**系统性**被操纵（而非偶发）时才删除 —— 门槛应高，因为消息面是 agent 唯一叙事通道

---

## 6. Fear & Greed Index (alternative.me)

- **接入点**：`src/integrations/news/fear_greed.py`
- **数据范围**：0-100 情绪指数 + 分类标签
- **数据性质**：聚合器（私营）
- **Auth**：无

**可操纵难度**：🟡 中 — 构成中有可被刷量影响的成分。

**攻击面**：

1. **Twitter/社交成分可刷量**：FGI 官方文档披露构成包含 25% 社交媒体 + 10% Google Trends（可被 bot 活动影响）
2. **波动率成分依赖 BTC**：45% 来自 BTC 波动率和动量 —— 若 BTC 被操纵，FGI 也被牵动（但这是反映而非操纵）
3. **alternative.me 是私营单点**：服务中断 / 数据质量下降无外部校验
4. **阈值定义主观**：0-100 的 "Extreme Fear / Fear / Neutral / Greed / Extreme Greed" 分段由 alternative.me 自定
5. **瞬时值误导**：agent 看到"今天 32（恐惧）"但不知是刚被推到极端还是长期在恐惧区

**检测特征**：

- **Agent-observable**：
  - FGI 瞬时值与 BTC 7 日实际波动率/趋势背离
  - 社交事件（某大 V 发文）后 FGI 跳变明显
- **工程侧**：
  - alternative.me 响应延迟 / 变化频率降低（可能上游问题）

**当前缓解**：

- 6h TTL（`_FGI_TTL = 21600`）避免过度轮询
- `persona.py:38` 仅呈现 value + classification，不做方向性解读
- `get_market_news` 输出格式中 FGI 仅占一行（信息低权重）

**建议动作**：

- 🔴 本批可修（低优先级，考虑）：`get_market_news` 可增加"过去 7 天 FGI 走势"的 mini-series（历史数据 alternative.me 提供），让 agent 看变化趋势而非瞬时值 —— 但这**增加工具复杂度**，且属新功能范畴，建议放入下轮 Toolkit Expansion
- 🟢 观察期记录：FGI 与 BTC 7d 波动的背离 case、社交事件后的跳变
- 🟡 N4 prompt 议题：Layer 1 可引导 "FGI 瞬时值有噪音，应看多日趋势"

---

## 7. ForexFactory 宏观日历

- **接入点**：`src/integrations/news/calendar.py`（通过 `faireconomy.media` 第三方聚合）
- **数据范围**：美国宏观事件（FOMC / CPI / NFP 等），本周范围
- **数据性质**：聚合器（第三方 RSS/feed）
- **Auth**：无

**可操纵难度**：🟡 中 — 事件**分类**和**预期值**可被聚合器编辑调整。

**攻击面**：

1. **第三方聚合器单点故障**：`faireconomy.media` 停服 / schema 变化会静默失败
2. **Impact 分类主观**：High / Medium / Low 由 ForexFactory 编辑定，标准不透明
3. **预期值 (forecast) 来源不标注**：consensus 形成方式不公开，可能偏向某类分析师群体
4. **事件范围仅本周**：周末 / Friday 晚可能漏下周初事件（已在 `get_critical_alerts` footer 提示）
5. **历史数据不可访问**：feed 只给 `ff_calendar_thisweek.json`，无历史，无法 backtesting

**检测特征**：

- **Agent-observable**：
  - 已知重大事件（如 FOMC）未出现在列表 → 聚合器问题
- **工程侧**：
  - `faireconomy.media` 返回 HTTP 错误或空 JSON
  - 事件数量异常低（< 3 条/周）

**当前缓解**：

- 6h TTL
- Only 过滤 country=USD + impact ∈ {High, Medium}（减少噪音）
- `get_critical_alerts` footer 明确 "current week only" + "Friday evening / weekend calls may miss next week's early events"
- 事件 timestamp 做 ET → UTC 归一化
- N3 §3.2 的 "Previous: X | Forecast: Y" 格式 fallback "N/A"

**建议动作**：

- 🟢 观察期记录：漏事件 case、预期值与实际发布值差异
- ⚠️ 替换候选：若 `faireconomy.media` 长期不稳定，考虑 FRED calendar API 或 Investing.com 的替代源（但许多替代源都需要 key/付费）

---

## 8. CoinGecko /global

- **接入点**：`src/integrations/macro/cg_global.py`
- **数据范围**：BTC.D / ETH.D / Total Market Cap / 24h Mcap change
- **数据性质**：聚合器（私营，受 VC 投资支持）
- **Auth**：Demo-tier API key（header `x-cg-demo-api-key`）

**可操纵难度**：🟢 高 — 数据基于链上市值聚合，理论上操纵需广泛配合。但**聚合逻辑**（覆盖哪些 token、市值口径）由 CoinGecko 定。

**攻击面**：

1. **Token 覆盖范围决定 dominance**：CoinGecko 新增/删除 token 会影响 BTC.D 分母（例：若大量小 meme coin 被纳入，BTC.D 会下降，不代表 BTC 本身减弱）
2. **市值计算口径**：circulating supply 来源不统一（部分自动抓取、部分项目方报告），历史上有 project 虚报 supply 被 CoinGecko 纠正的 case
3. **Demo-tier key 限速 30 req/min**：遭遇 429 会降级，N3 已用 TTLCache + MacroService 层的 RateLimitHit 处理
4. **Schema 变更风险**：`market_cap_percentage.btc` 路径若改会导致 None 返回

**检测特征**：

- **Agent-observable**：
  - BTC.D 单日跳变 > 2% 且无 BTC 明显价格波动
  - Total Mcap 与主要 token 价格表现不一致
- **工程侧**：
  - CoinGecko 返回 null / missing fields

**当前缓解**：

- `or {}` 守卫层层嵌套 null（见 cg_global.py:38）
- Per-field None 渲染 "N/A"（N3 §3.2 sub-source independence）
- MacroService 的 TTL + degraded fallback

**建议动作**：

- 🟢 观察期记录：BTC.D 异常跳变的 case、是否与 CoinGecko token list 变化相关

---

## 9. FRED (Federal Reserve Economic Data)

- **接入点**：`src/integrations/macro/fred.py`
- **数据范围**：USD Index (DTWEXBGS) / VIX / 10Y Treasury / 2s10s / 10Y Inflation Expectation
- **数据性质**：官方政府数据（美联储）
- **Auth**：API key 在 **URL query param**（`api_key=...`）

**可操纵难度**：🟢 极高 — 美联储官方数据，操纵需政府层面配合，几乎不可能。

**攻击面**：

1. **数据发布延迟**：不同系列发布频率不同（daily/weekly），DTWEXBGS 有 ~1 周 report 延迟（N3 spec §2.2 已处理 limit=3 找最新非 missing 值）
2. **FRED 服务中断**：FRED 本身如不可达（极罕见）会导致全部 5 个 series 失败
3. **API key 泄露风险（工程侧）**：key 在 URL query param，`httpx.HTTPStatusError` 默认消息含 request.url → log 泄露
4. **缺失值语义**：`.` 表示 missing，已扫 3 行找最新有效值

**检测特征**：

- **Agent-observable**：
  - 多个 series 同时显示 stale date（同一天，如 "2026-04-12" 停更一周） → FRED 服务异常
- **工程侧**：
  - Log 中出现 `api_key=` 字样 → scrubber 失效
  - FRED 5xx 响应比例

**当前缓解**：

- **手工构造 HTTPStatusError**（`fred.py:46-50`），不 raise_for_status → log 不含 api_key
- `limit=3` + `sort_order=desc` 处理稀疏发布
- 每 series 独立 try/except（N3 MacroService 实现），任一失败不级联

**建议动作**：

- 🔴 本批可修（PR C §4.6）：audit 确认**无其他 log 打印包含 request.url** 的路径（非常有信心已覆盖但做一次正式 pass）
- 🟢 观察期记录：FRED 5xx 错误率、DTWEXBGS 停更时段

---

## 10. Alpha Vantage

- **接入点**：`src/integrations/macro/alpha_vantage.py`
- **数据范围**：SPY / QQQ 行情（GLOBAL_QUOTE）
- **数据性质**：聚合器（私营，数据来自 NYSE/NASDAQ）
- **Auth**：API key 在 **URL query param**（`apikey=...`）

**可操纵难度**：🟢 高 — 数据根本来源是 NYSE/NASDAQ 官方，AV 仅是聚合转发。但：
- AV 的软限流（HTTP 200 + body "Information" / "Note"）是 AV 自定义
- 25 req/day 免费档限制是商业决定

**攻击面**：

1. **AV 限流行为**：soft rate limit（HTTP 200 + body 警告）需要 client 主动识别，已处理（`alpha_vantage.py:103-105`）
2. **Budget 透支无可观测性**：25/day 预算当前无计数，透支后所有调用降级但工程侧看不到
3. **AV 数据延迟**：GLOBAL_QUOTE 最晚可能延迟 15min（AV 免费档特性）
4. **`change_pct` 语义**：AV 的 `10. change percent` 是 close-to-previous-close，N3 spec 已改为 "as of <trading_day>" 标注
5. **API key 泄露风险**：同 FRED，key 在 URL query param；已用手工 HTTPStatusError 防护
6. **Throttle instance-level**：`_last_fetch_at` 是 instance attribute，多实例场景会绕过 throttle（`alpha_vantage.py:60`）

**检测特征**：

- **Agent-observable**：
  - SPY / QQQ 显示 "Temporarily unavailable" 持续 > 2h → 可能透支 budget
  - `latest_trading_day` 与当前日期偏离 > 1 工作日 → AV 数据延迟
- **工程侧**：
  - `AlphaVantage soft rate limit` log 在短时间内出现多次
  - Daily count（若加了 metric）逼近 25

**当前缓解**：

- 手工 HTTPStatusError（无 URL 泄露）
- 软限流检测（Information / Note）→ RateLimitHit
- 时段感知 TTL（N3 §5.2）：Sat/Sun 12h、weekday 9:30-16:00 30min、pre/after 4h
- 1.1s min interval 节流
- TTLCache stale fallback

**建议动作**：

- 🔴 本批可修（PR C §4.1）：加 daily count warning at 80% 阈值
- 🔴 本批可修（PR C §4.5 M6）：`_last_fetch_at` 改为 class-level（多实例防御）
- 🟢 观察期记录：budget 耗尽频率（特别是周四周五）、`latest_trading_day` 延迟模式

---

## 11. SoSoValue (ETF flows)

- **接入点**：`src/integrations/crypto_etf/sosovalue.py`
- **数据范围**：BTC/ETH US spot ETF summary history（日净流、累计流入、AUM）
- **数据性质**：聚合器（私营，数据来自 SEC 13F filings + ETF issuer reports）
- **Auth**：API key 在 header（`x-soso-api-key`）

**可操纵难度**：🟢 高 — 根本数据来自受监管的 SEC filings 和 issuer 报告。SoSoValue 的角色是**聚合 + 展示**，不产生原始数据。

**攻击面**：

1. **聚合口径差异**：不同 ETF（BITB / IBIT / FBTC 等）披露节奏不同，SoSoValue 汇总时可能有同日多版本数据
2. **T+1 修订**：当日值可能次日被修订，已在 footer 明示（`tools_perception.py:876-877`）
3. **交易日语义依赖**：footer "weekends/holidays excluded" 假设 SoSoValue 只返回交易日；如果上游改为返回所有日历日（holiday 补 0），footer 会误导
4. **Multi-row same-date**：SoSoValue 一天可能返回多行（如 Friday + unsettled），N3 实现用 dedup by date 处理
5. **API key 在 header**：泄露风险低于 URL query param 的情况（默认 HTTPStatusError 不序列化 headers）
6. **空 data 数组**：当前视为 outage（None 而非 [] data-gap），已在 service.py 处理（`crypto_etf/service.py:67-72`）

**检测特征**：

- **Agent-observable**：
  - ETF flows 显示 "Insufficient data" 但市场有明显 ETF 相关新闻 → 聚合器延迟
  - 累计 AUM 与 flow 之和明显背离（应：AUM_today ≈ AUM_yesterday + net_flow_today + market_value_change）
- **工程侧**：
  - `cum_net_inflow` 跨行不一致 → 上游 schema 变化
  - 返回空 data 数组 → silent 401 或上游故障（service 已判为 outage）

**当前缓解**：

- Dedup by date（first occurrence wins，基于 cum_net_inflow 和 total_net_assets 跨行一致性验证）
- 日流通过 cum-delta 差分计算（多行安全）
- 空响应判为 outage（非 data-gap）
- TTL 4h（`_ETF_TTL = 14400`）
- `TypeError / ValueError / KeyError` 三重 catch（`crypto_etf/service.py:113-118`）

**建议动作**：

- 🔴 本批可修（PR C §4.5 M3）：`change_7d_pct` 在 `prev_week == 0` 时渲染 "N/A"（类型 `float | None`）—— *注意：此项属 DefiLlama 的 stablecoin snapshot，不是 SoSoValue；此处记录是因为风险类别相同*
- 🟢 观察期记录：footer "weekends/holidays excluded" 是否与 SoSoValue 实际返回一致（通过 date 值范围 verify）

---

## 12. DefiLlama (stablecoin)

- **接入点**：`src/integrations/onchain/defillama.py` + `service.py`
- **数据范围**：每个 stablecoin 的 `circulating` / `circulatingPrevWeek`（peggedUSD）
- **数据性质**：链上数据（聚合）
- **Auth**：无

**可操纵难度**：🟢 高 — 根本数据来自链上合约状态，聚合层的解析逻辑是可操纵点。

**攻击面**：

1. **Schema drift — 多链分条**：若上游某天改为"USDT-Ethereum / USDT-Tron" 分别一行，当前 `{a.get("symbol"): a for a in raw}` 会**静默后者覆盖前者**，丢失数据。**已识别，PR C §4.2 即将修复**
2. **大小写/whitespace 敏感**：当前 symbol 匹配是精确字符串，`"usdt"` / `" USDT"` 会不匹配。**已识别，PR C §4.2 即将修复**
3. **Symbol 重命名**：DefiLlama 若将 USDT 重命名为 "USDT0" 之类，会导致两个 symbol 都不匹配 → 渲染 "no tracked symbols found"（service 层已有 guard，见 `tools_perception.py:900-906`）
4. **`circulatingPrevWeek` 字段缺失**：当前 `.get(...) or {}` + `.get("peggedUSD", 0.0)` 返回 0.0，**导致 pct 固定 0.0%**（PR C §4.5 M3 将改为 None）
5. **链上合约事件**：合约 mint/burn 大额变动会被 reflect，但如果合约本身被黑（theoretically），数据会错误
6. **DefiLlama 服务中断**：完全公开无 auth，单点故障风险存在

**检测特征**：

- **Agent-observable**：
  - stablecoin 供应 7d 变化 = 0% 且 circulating 看起来正常 → prev_week 字段可能缺失
  - 显示 "no tracked symbols found in response" → symbol 重命名
- **工程侧**：
  - `logger.warning("SoSoValue returned empty response...")`（onchain service，注意命名误导 —— 实为 DefiLlama empty response）
  - 同 symbol 返回多行（schema drift）

**当前缓解**：

- TTL 6h（`_STABLECOIN_TTL = 21600`）
- `TypeError / ValueError / KeyError` 已实质处理（`onchain/service.py:36-47`）
- Empty result guard 返回 "data unavailable (no tracked symbols found)"（`tools_perception.py:900-906`）

**建议动作**：

- 🔴 本批可修（PR C §4.2）：symbol 归一化（`.strip().upper()`）+ 多行同 symbol 聚合求和
- 🔴 本批可修（PR C §4.5 M3）：`prev_week == 0` 时 pct 改 `float | None` + 渲染 "N/A"
- 🟢 观察期记录：DefiLlama 服务稳定性、symbol list 变动

---

## 总结

### 按风险等级分类

| 风险 | 数量 | 源 |
|------|------|------|
| 🔴 高（低操纵门槛） | 1 | CoinDesk News |
| 🟡 中（有可操纵成分） | 6 | OKX Exchange / Announcements / Status、FGI、ForexFactory |
| 🟢 低（操纵门槛高） | 5 | 本地指标、CoinGecko、FRED、AV、SoSoValue、DefiLlama |

（DefiLlama 虽为 🟢 但有 Schema drift 实际触发点，需立即加固）

### 本批可修项（🔴 直接立即修）

从盘点中识别出的改动，**PR A 后立即并入 PR C**（按已在现有 spec §4 列出，此处确认覆盖）：

| 源 | 改动 | 现有计划位置 |
|----|------|------------|
| DefiLlama | symbol 归一化（`.strip().upper()` + 多行求和） | PR C §4.2 |
| DefiLlama | `change_7d_pct` 在 `prev_week == 0` 时改 `float \| None` | PR C §4.5 M3 |
| Alpha Vantage | daily count warning at 80% 阈值 | PR C §4.1 |
| Alpha Vantage | `_last_fetch_at` 改 class-level | PR C §4.5 M6 |
| 本地指标 (BB) | 输出改 "% of band width" + zero-width 边界 | PR B §3.2 #4 |
| FRED | 最终 audit 确认无 URL log 泄露 | PR C §4.6 |

**本 PR A 盘点过程中发现的新项**（不在之前 PR C 清单中）：

- **无**。本次盘点**未发现**此前 spec 未识别的新 schema 漏洞或 key 泄露点。所有 🔴 本批可修项都已在 PR C §4 预定范围内。

### 观察期记录项（🟢）

按源集中清单，供观察期主动采集：

| 源 | 记录事项 |
|----|---------|
| OKX Exchange | LSR 与 order flow 背离、funding 极端值与价格的关系 |
| OKX Announcements | 公告与价格反应时序（是否"事后发"） |
| OKX Status | 维护窗口与重大 macro 事件的对齐、实际时长 vs 公告时长 |
| 本地指标 | 指标计算错误的实例（RSI 跳变、MACD 背离） |
| CoinDesk News | **重点**：news 看多/空但价格未反应的 case，含 timestamp、content、5m/15m/1h 价格反应 |
| FGI | 瞬时值与 BTC 7d 波动的背离、社交事件后跳变 |
| ForexFactory | 漏事件、预期 vs 实际发布差异 |
| CoinGecko | BTC.D 异常跳变与 token list 变化的关联 |
| FRED | 服务 5xx 错误率、DTWEXBGS 停更时段 |
| Alpha Vantage | 25/day budget 耗尽频率（特别是周四/周五）、`latest_trading_day` 延迟模式 |
| SoSoValue | footer "weekends/holidays excluded" 假设是否成立 |
| DefiLlama | 服务稳定性、symbol list 变动 |

### N4 Prompt 议题候选（🟡，观察期后）

这些方向**不在本 hardening 批次 scope**，观察期数据支撑后作为独立 N4 迭代启动：

1. Layer 1 引导"新闻是被报道的内容不是事实" —— 针对 CoinDesk
2. 工具输出加可信度标注（如 "aggregator-reported, single source"） —— 针对 FGI、CoinGecko、SoSoValue
3. 价格-新闻反向校验提示（"news 看多但价格未反应 → 可疑"） —— 针对 CoinDesk
4. 多源新闻交叉（加 The Block / Decrypt 等） —— 源扩展，已超 prompt 范畴
5. FGI 时序上下文（7 天走势而非瞬时）—— 已评估为 "增加工具复杂度"，倾向留给下轮 Toolkit Expansion

### 替换/删除候选（⚠️ / ❌）

| 源 | 替换/删除触发条件 |
|----|----------------|
| CoinDesk News | 观察期记录 ≥ N 条"被 news 误导"实例（阈值待定），考虑换 The Block / Decrypt |
| ForexFactory | `faireconomy.media` 长期 5xx 或 schema 频繁变化，考虑迁移 FRED calendar |

**暂无应立即删除的源**。

---

## 参考

- N2 spec: `docs/superpowers/specs/2026-04-16-n2-market-news-design.md`
- N3 spec: `docs/superpowers/specs/2026-04-18-n3-macro-context-design.md`
- 记忆：`project_n4_source_trust`, `project_next_iteration_toolkit_expansion`
