# get_market_data 聚焦 + 时点语义打磨

> 主线 (a) **讲清 closed vs live**：in-progress candle 独立 section（议题1）/ MA·BB 比较行显式标 `Last`（议题2）/ Technical Indicators 表头加 closed-bar 时点锚（议题3）
> 主线 (b) **删低价值/重复、聚焦本职**：删 Market Context 冗余 `Last bar vol` 行（议题4）/ ATR 归位进 Technical Indicators、Market Context 整段消失（议题5）/ 删 Period summary 整段（议题6）
> 范围：纯输出层 + LLM 描述；**0 行算法改动**（`compute_indicators` 不动 / `_closed_bars` 不动 / 指标公式不动）。改 4 源文件 + 测试。

## 1. 背景与问题

`get_market_data`（GMD）是 perception 主入口（427 个含 GMD 的 action 中 `<alone>` 仅 6），Information 类工具。2026-06-06 tool-audit（session `f670abe1`，384 cycles / 2186 reasoning blocks，报告 `.working/tool-audits/2026-06-06-get_market_data.md`）从用户两条打磨建议（①展示未收盘 candle ②指标表头标 closed）出发，实证归纳出 6 条议题、两主线。本 spec 是该审计的设计落地。

审计的两条用户原始建议判定：
- ①（展示 in-progress）✅ 合理 → 议题1。
- ②（标 closed）⚠️ 字面**有害**——Technical Indicators 段是"值 closed / 比较 live"混血，裸标 closed 会把 closed 断言错盖到本就用 live 算的 MA·BB 比较行上 → 演化为议题2（消歧）+ 议题3（时点锚）。

## 2. 计算真相（事实底座，决定议题 2/3 的措辞）

GMD impl 接线（`tools_perception.py:97-101`，`:96` 为 `df = await … get_ohlcv_dataframe(…)`）：
```
df_closed = _closed_bars(df)            # = df.iloc[:-1]，剥未收盘那根
indicators = compute_indicators(df_closed)
indicators_text = format_for_llm(indicators, current_price=live_price, timeframe=timeframe)
```

**值（`compute_indicators`，全 closed-bar，全取 `iloc[-1]`）**：RSI(14) Wilder / MA(20)·MA(50) **SMA** / MACD EMA 12/26/9 / BB(20,2) ddof=0 总体std（G-5 锁定）/ ATR(14) Wilder。

**渲染（`format_for_llm`）的混血点**：
- RSI / MACD：只渲染**值**，纯 closed-bar。
- MA 行尾 `(price vs MA: X%)`：`(current_price − ma)/ma`，`current_price = live ticker Last` → **用 live**。
- BB 行尾 `(position: …)`：`current_price` 与三档边界比较 → **用 live**。
- ATR `% of price`（现渲在 Market Context，GMD impl `atr / live_price`）：分母 **用 live** —— 与 MA/BB 同类（值 closed、% 分母 live、price 隐式），本 iter 移进 Technical Indicators 后一并消歧（§3.5，显式标 `of Last <价>`）。

算术坐实（13:14:04 渲染，`Last=71744.00`，末根 12:45 close=72186.70，MA20=72638.21）：`(71744−72638.21)/72638.21 = −1.23%` 命中渲染 −1.2%；用 closed close 则 −0.62%（对不上）→ 派生量**确用 live**。

> 一句话定性：**指标的*值*是 closed-bar 快照（截至最近收盘 candle）；MA/BB 行尾的 % 比较是拿 live Last 比 closed 结构。** 非计算 bug，是**披露问题**——描述里 "All indicators are computed on the closed-bar series only" 对那两行自相矛盾。误读率实测 0/2186（潜在歧义，非实测有害）。

本 spec 不动任何指标公式；只修披露与渲染。

## 3. 设计

### 3.1 议题1（P1）— in-progress candle 独立 section

**问题**：in-progress candle 当前仅以 Recent Candles 表头时间戳后缀呈现（`; in-progress 13:00 still open, closes at 13:15`），无任何价/量。其 OHLCV 行被 `_closed_bars = df.iloc[:-1]` 整根剥离——**但那根 bar 已 fetch 进 `df`，只是被丢**（`df.iloc[-1]`）。结果 agent 必须靠 {表末行=上一根 closed} + {Ticker `Last`} 脑内桥接当前正在形成的 candle，对其 high/low/volume 无从得知。实证：in-progress 引用 + 价格重建签名 **764/2186 = 34.9%（跨 358 cycles）**；对 high/low 显式不确定（"at least X+"）96/2186 = 4.4%；显式点出 volume 缺口 16/2186。

**设计**：Recent Closed Candles 表**之后**单开 In-progress Candle section，渲染 `df.iloc[-1]` 真实 OHLC + volume。

排序定为 closed 表**之后**（方案 S，已与用户敲定优于"统一进一张表"）：closed 表 oldest-first（最新收盘在表底），in-progress section 紧贴其下 = 时间轴无缝接续，且把 agent 最高频(34.9%)桥接的两根（last-closed↔in-progress）物理相邻。**不统一进一张表**的决定性理由：partial 语义最干净的表达是**列头** `High(so far)`/`Low(so far)`/`Vol(so far)`，统一表一行列头做不到，只能退化成脚注；且统一表会让 Vol 列（partial vs full 同列）和 `—`（本工具已表"SMA(20) 未起步"）双双语义重载（违原则 7）。

**section 格式**（列名 `Last` 已与用户敲定；值取 `df.iloc[-1].close`，整行同源）：
```
=== In-progress Candle (15m): 13:00 open, closes 13:15 — ~14 of 15 min elapsed ===
Time (open UTC)        Open  High(so far)   Low(so far)       Last  Vol(so far)
13:00              72186.70     72205.40      71700.00   71744.00       1342.0
(partial bar — excluded from all indicators; no RVol/markers until close)
```

**elapsed 单位（tf-自适应）**：`~N of M <unit> elapsed` 的单位按 bar 总时长选，避免 1d/1w/1M 渲成五位数分钟。规则（GMD 实测 80%+ 为 15m，故 intraday 形态保持已审样张不变）：
- total ≤ 90 min → `min`（15m → `~14 of 15 min`；1h → `~45 of 60 min`）。
- 90 min < total ≤ 48 h → `h`（1 位小数；4h → `~2.0 of 4 h`；1d → `~14.0 of 24 h`）。
- total > 48 h → `days`（1 位小数；1w → `~3.0 of 7 days`；1M → `~12.0 of 30 days`）。

阈值用 total（= bar 时长）判，elapsed 与 total 同单位渲染。

**三条 correctness 护栏（实现必守）**：
1. **partial-vol 不出 RVol/markers** — 给原始 `Vol(so far)` + header 的 `~N of M min elapsed`（让 agent 判 partial bar 成熟度）；**不**渲 RVol/markers（部分量 ÷ full-bar SMA(20) 是误导性低比）。
2. **`Last` 列单行同源** — 盘中行 O/H/L/Last/Vol 全取 `df.iloc[-1]`（`Last = df.iloc[-1].close`），保持该行内部一致；顶部 Ticker `Last`（= `live_price = ticker.last`，源 ticker）仍是权威 live price。两个 `Last` 同名已与用户敲定接受。**残留张力（原则 7 同名异义）明示**：两个 `Last` 来自两次独立 fetch（ticker vs OHLCV API），实测 ~0.01 bps drift、非严格相等（`ohlcv_utils.py:_live_price` docstring，2026-05-10 31-sample 实测）——`~0.01 bps × 72k ≈ 0.07 USD`，在 `.2f`（$0.01 分辨率）下**即可见**（如顶部 `Last 71744.00` vs 盘中行 `Last 71744.07`），快行情漂移更大。取舍：**可见不等时不额外标注**（标注会把零危害的 sub-bps 漂移升格成噪声；整行同源 + caveat 已足够定位"盘中行是这根 bar 自身的 running 值"，权威决策价始终是顶部 Ticker `Last`）。
3. **绝不进任何计算** — indicators / RVol 基准 / markers 继续只吃 closed（`_closed_bars` 存在理由：in-progress temporally unstable）；section caveat 明文 `excluded from all indicators`。

**配套**：删 Recent Candles 表头的 in-progress 后缀，表头 `Recent Candles` → `Recent Closed Candles`（信息收敛到独立 section，DRY）。

### 3.2 议题2（P2）— MA/BB 比较行显式标 `Last` 消歧

**问题**：MA/BB 行尾的 `(price vs MA: X%)` / `(position: …)` 里 "price" 未标是哪个，描述却声称 "closed-bar only"（§2）。

**决策（已与用户敲定）**：**两个派生量都留 + 显式标 `Last`，不删**。删除会牺牲在用的 BB position（5.9%、128/2186、干净归 GMD、被逐字引用、非对称 frame 难手算）去修零危害的潜在歧义，不划算 → 消歧而非删除。不用裸 "live"（新词）或裸 "last"（本工具已被 `Last bar vol` 重载）——用显式操作数 `Last <价>`，消歧靠数字对得上 Ticker、对不上末根 close。

**渲染改动**（`format_for_llm`）：
- MA：`MA(20): 72638.21 (price vs MA: -1.2%)` → `MA(20): 72638.21  (Last 71744.00 → -1.2% vs MA)`
- BB 三态：
  - below：`(Last 71744.00 → 0.5% below Lower)`
  - above：`(Last 71744.00 → X% above Upper)`
  - inside：`(Last 71744.00 → X% of band, 0%=Lower / 100%=Upper)`
  - equal bands（`bb_u == bb_l`）：`(position: N/A)`（沿用现有 `technical.py:106` **带冒号**写法不漂移；`test_technical.py:152` edge-case 断 paren 内含 `N/A`、无 `%`/数字 → equal-bands **不加** `Last <价>`，否则价格数字会触发 no-digit 断言）

`<价>` = `current_price`（即 live Last），`.2f`。`Last` 旁的 `→` 是同一个 live 价对 closed 结构的比较锚。

> **锚串精确性（plan 钉死）**：inside-band 的 `0%=Lower / 100%=Upper` 沿用现有代码 `technical.py:115` 与既有测试（`test_technical.py:132/149`、`test_ohlcv_utils.py:78`）的**带空格**写法，不引入无意格式漂移。本议题只在三态前缀加 `Last <价> →`、把 inside 的 `position:` 前缀替换为 `… → X% of band`，锚串本身不动。

### 3.3 议题3（P3）— Technical Indicators 段加 closed-bar 时点锚

**问题**：表头 `=== Technical Indicators (15m) ===` 不说值截至哪根 closed bar，agent 要跳到 OHLCV 表反推末根。这是用户建议②的**正确版本**（不是裸 closed）。

**设计**：表头 → `=== Technical Indicators (15m, values as of last closed 12:45) ===`。时间戳 = `df_closed.iloc[-1]["timestamp"]` 经 tf-aware `_fmt_candle_time`（15m→`12:45` / 1d·1w→日期）。`values as of` 只圈"值"，与议题2 配合后本段自解释 closed-vs-live 边界（值 as-of 末根 closed；**所有 live-touched 派生量——MA dist / BB position / ATR % of price——行内显式标 `Last <价>`**，分母/操作数均 = live Last，与表头"值 closed"清晰区分）。

### 3.4 议题4（P2）— 删 Market Context 冗余 `Last bar vol` 行

**问题**：`Last bar vol: 793.3 (0.57× SMA(20) avg)` 与 OHLCV 表末行（12:45: vol 793.3, RVol 0.57×）**逐位相等**——同分子（`iloc[-1]`）同分母（`iloc[-20:].mean()`）。根因：`Last bar vol` 是老设计；per-bar RVol 列 PR #63（2026-05-28）后加 → 这行变 vestigial。adoption：volume-vs-SMA 概念 25.1% 但几乎全走表的 per-bar RVol 列，这行 summary 独有 adoption ≈ 0。

**设计**：删除 `Last bar vol` 行（信号没丢，表末行 RVol 同数同窗）。

### 3.5 议题5（P3，激进档已敲定）— ATR 归位 + Market Context 段消失

**问题**：ATR 被描述列为 technical indicator 且 `compute_indicators` 计算 `atr_14`，**却渲染在 Market Context**（位置不一致）；描述指标清单**漏 MA(20)/MA(50)**；MA 实为 **SMA** 但输出/描述都没注明（兄弟工具 HTF 描述写了）。ATR adoption 高（raw pts 18.3% + % 3.8%，干净归 GMD）→ ATR **必留**，只是位置/描述需对齐。

**设计（激进档）**：ATR 行**移进 Technical Indicators 段**（在 `format_for_llm` 末尾渲染，`indicators` 已含 `atr_14`、已有 `current_price`）。配合议题4 删 vol 行后，**Market Context 段整段消失**（唯一独有内容 ATR 上移、vol 是重复被删），少一个 header。
- ATR 渲染：`ATR(14): 218.44 (0.30% of Last 71744.00)`（去掉旧的 `, {timeframe} candles` 后缀——段头已带 timeframe；分母 `current_price` = live Last，**显式标 `of Last <价>`** 与 MA/BB 的 Last 约定一致，消同段 closed-vs-live 歧义——议题2 的 Last 约定覆盖 MA dist / BB position / ATR % 三类 live-touched 派生量）。`% = atr / current_price * 100`，`.2f`；价 `.2f`；`atr is None or current_price <= 0` → `ATR(14): N/A`。
- 描述清单补 MA(20)/MA(50)、注明 SMA（见 §5 描述改写）。

> 注：OHLCV 表 `range↑` marker 用的 `_atr_series`（per-bar ATR）与本议题无关，原样保留。本议题只移动 Market Context 的 ATR 摘要行。

### 3.6 议题6（P2）— 删 Period summary 整段

**问题**：`Period summary`（Avg vol 5v5 + Net Δclose 5v5）决策价值低且被强工具覆盖。证据（决策价值维度，比"被提及频率"更硬）：committed `decision` 出现率 **18/384 = 4.7%** vs taker_flow/CVD **93.2%（~20×）** / per-bar RVol **42.7%（~9×）**；49 个引用 cycle 里 **94%（46/49）同 cycle 也调 get_taker_flow** → 是 corroborating 旁证而非 driver；三硬伤——① 无方向（只说量大/小，不说买/卖）② 低分辨率（5v5 粗窗 vs taker_flow 分钟级 / RVol 单根级）③ 可重算（聚合的就是正上方 OHLCV 表里可见的最近 10 根 close/volume）。

**设计**：**整段删除**（`tools_perception.py:202-216`），GMD 回归"单 TF 行情快照"本职。代价小且低风险——丢的是几乎免费的"先扫一眼"定向信号 + 18 个 decision 里的旁证（非依据），无误读证据、不破坏决策链；不触发高频手算（引用率非高频，且 RVol 列趋势 + taker_flow 覆盖同需求，原则 5 不被违反）。直接删则旧 `Net Δclose` 的 `close[-1]−close[-5]` off-by-one 口径瑕疵一并消失，不必修。

> **删 vs 留两类区分（审计核心方法学）**：议题4 `Last bar vol` = 精确重复（同表末行 RVol，逐位相等）→ 删；议题6 `Period summary` = 非重复但决策价值低 + 被强工具覆盖 → 也删，理由是"低价值不值得占地"。两者都增 GMD 聚焦度。

## 4. 最终输出样张（全议题合并，真实 13:14:04 数据）

```
=== Ticker (BTC/USDT:USDT @ 13:14:04 UTC) ===
Last: 71744.00 | Bid: 71743.90 | Ask: 71744.00
24h High: 74168.00 | 24h Low: 71700.00 | 24h base vol: 7606851.38

=== Technical Indicators (15m, values as of last closed 12:45) ===
RSI(14): 29.60
MA(20): 72638.21  (Last 71744.00 → -1.2% vs MA)
MA(50): 73113.52  (Last 71744.00 → -1.9% vs MA)
MACD: -252.15 | Signal: -214.96 | Histogram: -37.19
BB(20,2): Upper 73172.83 | Middle 72638.21 | Lower 72103.58  (Last 71744.00 → 0.5% below Lower)
ATR(14): 218.44 (0.30% of Last 71744.00)

=== Recent Closed Candles (15m, last 10, oldest-first by row) ===
Time (open UTC)        Open       High        Low      Close        Vol  RVol(×SMA20)  Markers
10:30              72683.80   72767.50   72555.00   72685.10      901.8         1.02×
...
12:45              72235.90   72266.70   72075.00   72186.70      793.3         0.57×

=== In-progress Candle (15m): 13:00 open, closes 13:15 — ~14 of 15 min elapsed ===
Time (open UTC)        Open  High(so far)   Low(so far)       Last  Vol(so far)
13:00              72186.70     72205.40      71700.00   71744.00       1342.0
(partial bar — excluded from all indicators; no RVol/markers until close)
```

（对比现状：删了 `=== Market Context ===` 段与 `=== Period summary ===` 段；ATR 从 Market Context 上移进 Technical Indicators；Recent Candles → Recent Closed Candles 并去 in-progress 后缀；MA/BB 行加 `Last` 操作数；Indicators 表头加时点锚；新增 In-progress Candle 段。）

> 样张中 in-progress 行 `Open 72186.70`(=12:45 close) 与 `Last 71744.00`(=Ticker) 是真数据；`High(so far)/Low(so far)/Vol(so far)` 为示意（实现取 `df.iloc[-1]`，正是当前被丢弃的部分）。

## 5. 实现切法（文件级）

### 5.1 `src/services/technical.py` — `format_for_llm`（GMD 独占，已验证唯一调用方 `tools_perception.py:99`，不外溢）

- MA block（`:75-81`）：`(price vs MA: {dist_pct:+.1f}%)` → `(Last {current_price:.2f} → {dist_pct:+.1f}% vs MA)`。
- BB block（`:101-122`）：三态 + equal-bands 措辞改 §3.2（`Last {current_price:.2f} → …`）。
- 末尾**新增 ATR 行**（议题5）：
  ```python
  atr = indicators.get("atr_14")
  if atr is not None and current_price > 0:
      lines.append(f"ATR(14): {atr:.2f} ({atr / current_price * 100:.2f}% of Last {current_price:.2f})")
  else:
      lines.append("ATR(14): N/A")
  ```
- signature 不变（`current_price` / `indicators`（含 `atr_14`）已具备）；`timeframe` 仍 reserved。

### 5.2 `src/agent/tools_perception.py` — `get_market_data` impl

- `:93` 捕获 `now_dt = datetime.now(timezone.utc)`，`fetch_ts` 由它派生（in-progress elapsed 复用 `now_dt`）。
- `:119-120` Technical Indicators 表头注入末根 closed 时点（议题3）：`ts_str = _fmt_candle_time(_to_pd_timestamp_utc(df_closed["timestamp"].iloc[-1]), timeframe)`；`df_closed` 空时降级回无锚表头。
- **删 Market Context block**（`:122-143`，议题4 + 议题5 ATR 已移走）。
- Recent Candles 表头（`:196-200`）：`Recent Candles` → `Recent Closed Candles`，**删 `in_progress_suffix`** 整段逻辑（`:182-194`，议题1 收敛到独立 section）。
- **新增 In-progress Candle section**（议题1）：`df` 非空时渲 `ip = df.iloc[-1]`：
  - open 时间 = `_to_pd_timestamp_utc(ip["timestamp"])`；`offset = TF_OFFSETS.get(timeframe)`；
  - offset 已知：closes = `ip_open + offset`；**elapsed/total 统一用 `pd.Timestamp` 两侧相减**（避免 stdlib `datetime` 与 `pd.Timestamp` 混用边角）——`now_ts = _to_pd_timestamp_utc(now_dt)`，`elapsed = now_ts − ip_open`，`total = (ip_open + offset) − ip_open`（**1M 走 `pd.DateOffset` 无 `total_seconds()`，必须用 `(open+offset)−open` 得 `Timedelta` 再 `.total_seconds()`**）；clamp `elapsed` 到 `[0, total]`；header 单位按 §3.1 tf-自适应规则（min/h/days）；
  - offset 未知（降级）：header 只 `{open} open`，省 closes/elapsed；
  - 列 `Time | Open | High(so far) | Low(so far) | Last | Vol(so far)`，值全取 `ip`；caveat 行。
- **删 Period summary block**（`:202-216`，议题6）。
- 更新 dev-facing impl docstring（`:56-79`）：去 Market Context / Period summary，注明 ATR 现在 Technical Indicators 段、新增 in-progress section、indicators 含 MA。

### 5.3 `src/agent/tools_descriptions.py` — `GET_MARKET_DATA_DESCRIPTION`（LLM 通道，path B，`@tool(description=)` 绕过 griffe，Example 块 survives）

全面改写（要点）：
- 指标清单补全 `RSI / MA(20) / MA(50) / MACD / BB / ATR`，注明 MA 为 **SMA**。
- 准确披露：indicator 值 closed-bar（in-progress 排除）+ 段头报 last closed 时点；MA/BB 的 `Last <price> → X%` 与 ATR 的 `X% of Last <price>` 均以 **live ticker Last** 为操作数/分母（值 closed / live-touched 派生量显式标 Last）。
- OHLCV 表描述改 `Recent Closed Candles`，删 in-progress 表头后缀句。
- **删** Market Context（ATR % + last-bar vol）、Period summary 两段引用。
- **新增** In-progress Candle section 描述（partial bar：Open/High(so far)/Low(so far)/Last/Vol(so far) + elapsed；excluded from indicators；no RVol/markers；权威 live price = ticker Last）。
- Example output 同步为 §4 样张形态（含时点锚 / `Last` 标 / ATR 在 indicators / in-progress section / 无 Market Context·Period summary）。

### 5.4 `src/agent/trader.py` — wrapper docstring（`:131`）

summary 行同步：去 `period summary`、去 `in-progress hint`，改为 `ticker + indicators (RSI/MA/MACD/BB/ATR) + closed OHLCV table (RVol column) + in-progress candle section`。

## 6. 边界与降级

- **df.iloc[-1] = in-progress 的既有假设**：`_closed_bars = df.iloc[:-1]` 无条件把末根当 in-progress（既有设计）。本 spec 沿用；in-progress section 用 `df.iloc[-1]` 真实 timestamp/OHLCV，不外推。若交易所罕见返回末根已收盘，沿用既有行为（不在本 iter 扩大处理）。**blast-radius 承认**：旧设计该 case 仅令表头多一个错时间戳后缀；新设计会把一根已收盘 bar 显式渲成 `=== In-progress Candle … — ~N of M min elapsed ===` + `(partial bar …)`，**误导可见性较旧版上升**（虽 elapsed 有 clamp）。属既有 `_closed_bars` 假设下的已知接受取舍，不阻断本 iter；若未来该 case 实测出现，再独立处理"末根是否真 in-progress"的判定。
- **df 行数极少**：`df` 空 → 不渲 in-progress section（且 Indicators 表头降级无锚）。`df_closed` < 20 → 既有 N/A 路径不变。
- **TF_OFFSETS 缺 timeframe**：in-progress header 降级为只显 open（无 closes/elapsed），与既有 in-progress 后缀的降级策略一致。
- **elapsed clamp**：时钟偏移/边界可能使 elapsed 轻微越界 → 显示前 clamp 到 `[0, total_min]`。
- **ATR markers 不受影响**：`_atr_series`（per-bar，range↑ marker）原样保留。

## 7. 测试面

清单经全库 grep 实测（改动串：`=== Recent Candles` 改名 / `=== Market Context ===` 删段 / `=== Period summary` 删段 / `still open, closes at` 删后缀 / `price vs MA:` 改标签 / `Last bar vol` 删行 / `position:` 前缀移除）。**9 个硬断文件**（真 assert 会 FAIL，含经白名单变量间接断言的 `test_display_cycle` dg_1c）+ mock-only/不硬断若干（可选刷新）+ 3 个确认不受影响（grep 假阳性）。subagent-driven TDD 实施时**先 reconcile 这批既有测试**，避免到全量 pytest 才发现批量 hard-break。

### 7.1 硬断的既有测试（逐文件断点 + 处理）

1. **`tests/test_technical.py`** — `test_format_for_llm_is_fact_only`（**单函数 `:86-107`**，注意是一个函数不是两条）：
   - `:100 assert "price vs MA:" in text` → §3.2 改标签，**断** → 改断 `Last … → … vs MA`。
   - `:105 assert "ATR" not in text` → §3.5 ATR 进段，**断** → **反转**为断 `ATR(14):` in text。
   - `:103 any(("0%=Lower / 100%=Upper", "above Upper", "below Lower"))` → 经 below/above 子串**仍 pass**（保留带空格锚串），但 inside 措辞已改，宜同步更新断言意图。
   - **BB position 三态测试不硬断（实测 survive，仅可选增强）**：`test_format_for_llm_bb_position_at_lower_band`/`_at_upper_band`（`:118/136`，fixture `current_price == band` 走 inside 分支）断 `"0%"`/`"100%"`/`"0%=Lower / 100%=Upper"`/无 above-below，新格式 `(Last … → 0%/100% of band, …)` 全保留 → **不断**；`_edge_case_equal_bands`（`:152`）断 paren 内 `N/A`、无 `%`/数字，`(position: N/A)` 保留 → **不断**。三者建议**可选**加 `Last <价>` 正锚以覆盖新格式，非 reconcile 必需。

2. **`tests/test_iter_tool_opt_gmd_polish.py`**（上一轮 GMD iter / PR #63，**整体 reconcile / 大段废弃**）：
   - `:183/200/250` `out.split("=== Recent Candles")[1].split("=== Period")[0]` → 改名 + 删段**双断**（IndexError）。
   - Task 3 in-progress hint 组：`:271/272`（`in-progress `/`still open, closes at `）、`:287/304`（4h/1d 后缀 regex）、`:313-340`/`:352-373`（in-progress open==last_closed+offset 外推断言）、`:379-389`（unknown tf → `assert "in-progress" not in out`，新设计降级仍渲 in-progress section **会断**）→ 全部**重写指向新独立 section**（断 section header + 列头 + `df.iloc[-1]` 值 + caveat；外推语义改为读 `df.iloc[-1]` 真实 timestamp）。
   - `:391 "=== Recent Candles" in out` → 改断 `=== Recent Closed Candles`。
   - `:415 "=== Market Context ===" in out` → §3.5 删段 → 改断**不再出现**。
   - `:444 "=== Period summary" in out` → §3.6 删段 → 改断**不再出现**。
   - `:467/468/473` 描述断言（`=== Recent Candles` / `=== Period summary` / `in-progress`）→ 随 §5.3 描述重写更新。

3. **`tests/test_trader_agent.py:382/383`**（GMD 描述断言）：`=== Recent Candles` → `Recent Closed Candles`；`=== Period summary` 删段 → 改断不再出现（或替换为 in-progress section 文档断言）。

4. **`tests/test_iter_w2r2_next_d_goldens.py`**（含 GMD + HTF 两类 golden，**须区分**）：
   - **GMD 断点（删整测试——测的是被删特性）**：`test_gmd_market_context_uses_last_bar_vol_and_smaperiod`（`:205`，断 `:212 "Last bar vol:"` / `:213 "SMA(20) avg)"` / `:215 out.split("=== Market Context ===")[1]` IndexError）→ §3.4/§3.5 删 Market Context → **整测试废弃**；`test_gmd_period_summary_section`（`:241`，断 `:250` Period summary header）→ §3.6 删段 → **整测试废弃**。
   - **HTF 命中不受影响（grep 假阳性）**：`:72 "price vs MA:"`（`test_htf_ma_lines_include_slope_and_price_vs_ma`）、`:102/103 "Last bar vol (base):"`/`"SMA(20) avg)"`（`test_htf_volume_regime_line`）属 `get_higher_timeframe_view`，不走 `format_for_llm`、本 iter 不动 HTF → **不动**（实施者勿在此文件找不存在的 GMD `price vs MA:`，勿改 HTF 测试）。

5. **`tests/test_tool_enhancement.py`**（**硬 assert，非 mock —— 原 §7 误判已纠**）：`:501 "=== Market Context ===" in result` / `:502 "=== Recent Candles" in result` / `:508 "ATR" in result` / `:509 "Last bar vol:" in result` / `:510 "SMA(20) avg" in result` 全断。处理：`:501`→改断不再出现；`:502`→`Recent Closed Candles`；`:508 ATR`→由 `:495` mock `format_for_llm.return_value` 刷新为含 `ATR(14): …` 新形态后覆盖（ATR 现经 format_for_llm 渲染）；`:509`→RVol 列；`:510 "SMA(20) avg"` 字样随 `Last bar vol` 删除**整串消失**（RVol 列头是 `RVol(×SMA20)` 非 `SMA(20) avg`），**mock 刷新救不回，须删/重指向**。`:495` mock（含 `price vs MA: +0.2%`）一并刷新为新标签 + ATR 行。

6. **`tests/test_ohlcv_ts_numpy_int64.py:101/105`**（**回归 guard，必重写不可删**）：`:101 assert f"in-progress {exp_open} still open, closes at {exp_close}" in out` → §3.1 删后缀 → 断。此测试守 **numpy.int64 时间戳塌缩 1970 bug**（commit `cfd871e`）；新 in-progress section 仍经 `_to_pd_timestamp_utc` 转时间戳，**同风险仍在** → **重写为断新 section 的 open/close 时间戳为正确值（非 1970）**，保留回归覆盖。`:105` 反向 guard 同步迁移。

7. **`tests/test_multi_tf_drift_guards.py:208 test_gmd_htf_last_bar_vol_ratio_match`**（**跨工具 drift guard，重指向不降级删**）：`:241/242` `re.search(r"Last bar vol:…", out_gmd); assert gmd_match` → §3.4 删 GMD 该行 → None → 断。GMD↔HTF SMA(20) 同窗不变式仍应守 → **GMD 半边重指向 per-bar RVol 列末行**（议题4 已证其与旧 `Last bar vol` 逐位相等、与 HTF 同 SMA(20) 窗），HTF 半边不动。

8. **`tests/test_ohlcv_utils.py:63 test_format_for_llm_bb_label_uses_full_words_and_explicit_periods`**：fixture `bb_lower 81494 < current_price 81870.50 < bb_upper 81960` → **inside-band 分支**。`:79 assert "position:" in out` → §3.2 inside 把 `position: P%` 改为 `Last … → P% of band`、`position:` 字样移除 → **断** → 改断新 `Last <价> → P% of band` 形态。`:64` docstring（描述旧格式）同步。`:78`（`0%=Lower`/`100%=Upper` 两子串）与 `:81`（`"BB: 81960" not in out` 旧格式 guard）**仍 pass**，不动。

9. **`tests/test_display_cycle.py`**（**dg_1c 实跑硬断，原 §7 误归 mock-only 已纠**）：`test_dg_1c_path_b_critical_fields_present[get_market_data]`（`:3194`）经 `_invoke_path_b` 调**真实** GMD impl，对白名单 `_CRITICAL_FIELDS_PATH_B["get_market_data"]`（`:3137 = ["Ticker","Technical Indicators","Market Context","Recent Candles","RSI","MACD","ATR"]`）逐项 `assert field in out`（白名单经变量间接断言，字面串 grep 抓不到——本误判教训）。三断：`"Market Context"`（§3.5 删段）/`"Recent Candles"`（§3.1 改名，非 `=== Recent Closed Candles` 子串）/`"ATR"`（§3.5 ATR 迁入 `format_for_llm`，此处被 mock 成无 ATR + Market Context 删 → 整个消失）。**双修**：① 白名单删 `"Market Context"`、`"Recent Candles"`→`"Recent Closed Candles"`（可加 `"In-progress"`/`"values as of last closed"`）；② 刷新 `_invoke_path_b` GMD `format_for_llm.return_value`（`:3001`）含 `ATR(14): …`（ATR 现经 format_for_llm 流出，**与 item 5 同款 mock×ATR 迁移**，否则白名单 `"ATR"` 救不回）。**不动**：`test_dg_1b_*` canonical 正则 `^=== (.+) ===$` 仍匹配新 in-progress header（pass）；`:450` `test_format_cycle_output_basic` 硬编码 GMD 串独立于真 impl（陈旧、可选刷新、不断）。

### 7.2 mock-only / 不硬断（可选刷新避免陈旧）

- `tests/test_tools.py`：`test_get_market_data`（`:98`）实跑真 impl 但只断 `"65000"` + `"=== Ticker"`（均 survive）→ **不断**；`:76 format_for_llm` mock 串陈旧、无断言依赖，可选刷新。
- `tests/test_fact_only_wordlist.py`：`test_get_market_data_fact_only`（`:373`）实跑真 impl，唯一断言 `hits == []`（无禁用主观词）；新增字符串（`partial bar` / `excluded from all indicators` / `so far` / `values as of last closed` / `Last X → Y% vs MA`）全 fact-only → **不断，且 guard 自动覆盖新串**。`:388` mock 串可选刷新。
- `tests/test_display_cycle.py:450` `test_format_cycle_output_basic` 硬编码 GMD 串（含旧 Market Context）独立于真 impl → 不断，可选刷新（该文件 dg_1c 硬断见 §7.1 item 9）。

### 7.3 确认不受影响（grep 假阳性，plan 排除）

- `tests/test_persona.py:632`（`test_layer1_market_context_renders_taker_fee_rate`）：persona Layer1 系统提示的 `## Market Context` 段，与 GMD 输出无关（`=== Market Context ===` 串不在其中）。
- `tests/test_perception_tools_n3.py:237`（`test_htf_ma_format_includes_vs_ma_prefix`）：HTF 渲染断 `(price vs MA:`，HTF 不走 `format_for_llm`，本 iter 不动 HTF → 不受影响。
- `tests/test_iter_tool_opt_volume_ratio_cleanup.py`（`Last bar vol` 仅在模块 docstring `:4`）：唯一测试 `test_compute_indicators_has_no_volume_ratio_field` 唯一断言 `"volume_ratio" not in indicators`，查 `compute_indicators` 返回 dict 形状、不碰 GMD 输出/`format_for_llm`/Market Context；本 iter 0 行算法改动 → `compute_indicators` 不动 → 改前改后都 pass → 不受影响。

### 7.4 新增测试

- in-progress section 渲染（header / 列头 `High(so far)`·`Low(so far)`·`Vol(so far)` / 值来自 `df.iloc[-1]` / caveat 行）。
- partial 无 RVol/markers（in-progress 行不含 `×` / `vol↑` / `range↑`）。
- Recent 表头 `Recent Closed Candles` + 无 in-progress 后缀。
- `Period summary` 与 `Market Context` 字符串**不再出现**在输出。
- ATR 出现在 Technical Indicators 段（BB 之后、Recent Closed Candles 之前）。
- Technical Indicators 表头含 `values as of last closed <ts>`（tf-aware：15m→`HH:MM` / 1d→日期）。
- MA/BB 行含 `Last <price>` 操作数（消歧）。
- 降级：TF_OFFSETS 缺 timeframe（in-progress header 只显 open）/ df 极短（不渲 in-progress section + 表头无锚）。
- elapsed clamp 到 `[0, total_min]`。

## 8. Scope / 非 scope / 流程

**In scope**：§3 六议题，改 4 源文件（`technical.py` / `tools_perception.py` / `tools_descriptions.py` / `trader.py`）+ 测试。**0 行算法改动**。

**非 scope**：
- `compute_indicators` / `_closed_bars` / 任何指标公式（不动）。
- 跨工具 `$` notional 口径（执行层 cs）、mark price、fee_rate 等（属 sim 执行保真线，per memory `feedback_sim_real_data_except_order_mgmt`）。
- candle-timing meta-pattern（审计已判非工具缺陷，不立议题）。
- ATR 三处来源口径（GMD 15m / get_position 1h / HTF per-tf）by-design，不统一。

**流程**：medium iter → 走 GitHub PR（feature 分支 `iter-gmd-focus-time-semantics`）；本 spec + plan 文档先于代码 commit（per memory `feedback_plan_doc_commit_first`）；TDD 实施。
