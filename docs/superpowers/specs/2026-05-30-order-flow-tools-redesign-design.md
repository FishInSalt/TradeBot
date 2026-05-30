# Order-flow 双工具重构：recent_trades 笔数化 + taker_flow（rubik 分钟级）设计

> `get_recent_trades`（逐笔 tape）：时间桶 → **等笔数桶**（秒级微观，服务 A 类入场时点确认）+ 修单位③
> 新增 `get_taker_flow`：rubik **分钟级**主动买卖量序列（实时 in-progress 当前桶，服务 B 类 flow 趋势）
> 两工具**互补、不互删**；recent_trades 去留 deferred 到重构落地后的新 sim run（§6 gate）

## 1. 背景与问题

B 类换源（`bbdf3aa`，2026-05-29）把 `SimulatedExchange.fetch_trades` 从合成数据改为调真实 `_ccxt`。代入真实数据后，逐笔 tape 工具暴露的问题：

1. **4/5 桶恒空（渲染 bug，可修，非删工具理由）**：OKX 单次 `fetch_trades` 上限 500 笔，活跃永续上覆盖 5–57s（实测 BTC 41s / ETH 28s / SOL 57s，bursty 时低至 ~6s），而工具固定按 `5×60s` **时间桶**渲染 → t−5min~t−2min 四个桶恒空。**根因是"按时间桶渲染 vs 数据只够几十秒"的错配，数据本身是好的**——改成按**笔数桶**即解。
2. **amount 单位错标（③，本次一并修）**：`amount` 是合约**张数**，旧工具误标为 base 币量。实测 contractSize BTC 0.01 / ETH 0.1 / SOL 1.0 → BTC/ETH 差了 contractSize 倍（BTC 单笔中位真实 **$59**，旧标误算成 $5859），SOL 因 cs=1 碰巧没露馅。诚实渲染规模/量需把张归一为 base——在 `fetch_trades` 适配器层完成（§4.2），使 `Trade.amount` 真正等于其注释声称的 base-currency。
3. 深度% 塌陷（属 `get_order_book`，另开 iter）。

**逐笔 tape 与分钟级 agent 是"分工"不是"不适配"**：

- 旧 session（f0f7b24f，248 cycles / 201 含 tape 推理块，5 路并行分类）：agent 把 tape 当**分钟级趋势/背离**过度解读 52%（B 类）；罗列 39%（C 类）；用于**入场时点确认** 3.5%（A 类）。
  - ⚠️ **数据错配 caveat**（per `[[feedback_data_mismatch_old_impl_inference]]`）：该 session 跑在**换源前合成噪声**上。它**能**证明 agent"把 flow 编趋势叙事"的结构性心智倾向；**不能**证明真实数据下各类用途的价值（A 类低很可能是噪声所致）。
- 关键洞察：逐笔 tape 是**秒级**数据——**恰好适配 A 类**（"此刻进场，买压在不在"，几十秒覆盖够用），**不适配 B 类**（分钟级趋势被秒级数据过度外推）。故正确动作不是删 tape，而是：
  - **修 tape 渲染为笔数桶 + 诚实标覆盖秒数** → 把 agent 心智重锚到"这是秒级微观快照"，fact-only 地压制 B 类过度解读；
  - **另建 `get_taker_flow` 服务 B 类**（分钟级 flow，用服务端真实聚合而非逐笔外推）。
- **两工具互补、不互删**：recent_trades（秒级微观 / A 类）+ taker_flow（分钟级趋势 / B 类）覆盖不同时间分辨率。recent_trades 真实价值 deferred 到**本次重构落地后的新 sim run** 用真实 grounding 评估（§6）。

## 2. 数据源核实：rubik taker-volume（实时、in-progress 当前桶）

想要「一段时间（几分钟到几小时）」的 taker flow 方向/强度/演化，逐笔成交是死路（500 笔=几十秒；分页拉 1000 笔仅 ~3s；堆"几分钟"需上千次调用，物理不可行）。OKX 服务端已聚合的 **rubik taker-volume** 是唯一可行源。

**接口**：`public_get_rubik_stat_taker_volume_contract`（单合约，instId 级）。

**实拉验证**（2026-05-30，BTC-USDT-SWAP，probe `.working/tool-optimization/probe_okx_taker_volume_contract.py`，覆盖 5m/1H/4H/1D/1W 全档）：
- 返回 `[ts, sellVol, buyVol]` 序列（新→旧），`ts`=桶**开盘**，间隔精确=period，单次 100 点（1M 27 点）。
- **最新一根是 in-progress 当前桶**（全 5 档一致：`ts + period > now`），数据**近实时、无发布滞后**——与兄弟端点 OI history 同款行为（`_derive_oi_anchors` 处理的那种）。
- **关键设计含义**：若改用"最新已完成桶"而非 in-progress 桶，5m 下会引入 ~5–10min 的数据年龄（newest-closed 桶的 age），1d 下达近一天——**这正是保留 in-progress 当前桶的理由**（数据实时，丢掉才有滞后）。本设计**保留 in-progress 桶并标注成型度**，不剔除（§3.2/§3.3）。
- `unit` 参数 `0/1/2 = 币/张/USD`，本设计用 **`2`（USD 名义）**：单位明确、跨品种可比。
- period 支持 `5m/15m/30m/1H/2H/4H/6H/12H/1D/2D/3D/1W/1M`（1m/3m 不支持）。

**为何 instId 单合约**：精确对口 agent 交易的具体合约，`unit` 可选；代价是覆盖较短（100 点）。ccy 级（`public_get_rubik_stat_taker_volume`，576 点/全市场聚合）作未采纳备选记录。

**现有工具占用核查 + 为何独立工具（原则 3/4/5/7）**：`get_derivatives_data` 已用 OI + LSR + funding（**标量快照**类）；`taker-volume*` 系列无任何工具使用。taker flow 输出是 6–36 行**时序表 + CVD 窗口**，与标量快照的基数/阅读模式差异大，并入会臃肿、违反原则 7；独立成立（原则 3 信号唯一权威；原则 4「工具数=选择延迟」权衡后仍独立）。

## 3. 工具设计

### 3.1 `get_taker_flow` 签名

```
get_taker_flow(period: str = "5m", limit: int = 6) -> str
```

- **`period`** `{5m, 1h, 4h, 1d}`：对齐 agent 现有 MTS（默认 5m/1h/4h/1d）/ HTF（默认 4h/1d）尺度栈，心智零迁移（原则 2/7）。对 agent 暴露**小写**，内部经**新建 `_TAKER_VOLUME_PERIOD` 全量映射表**（`5m/1h/4h/1d/1w` 五项，含锚点用的 1w）转 rubik 大写——**不复用** `_OKX_OI_PERIOD`（仅 3 项 5m/1h/1d，合法 period 集不同，混用会 `KeyError`）。rubik 其它档不纳入 period 选项：`15m/30m/2H…` 是 agent 现无尺度；`1w/1M` 虽在 MTS/HTF 词汇内但超出本工具日内 flow 聚焦（`1w` 仍作 1d 锚点）。
- **`limit`** 默认 6，范围 `[1, 36]`（上界 36 = **人读 / context 成本**上限，非"100 点物理上限"；5m 下 36=3h 窗口）。`limit` = **明细行数 + CVD/净卖比/Window 统计窗口**。**RVol baseline 解耦**：固定 20 根 closed bar（不随 `limit`），故工具内 fetch `max(limit+1, 21)` 点（端点 honor limit，probe G），显示最新 `limit` 行——同 GMD「fetch 多、显示少、RVol vs 固定 20」pattern。
- **两参数正交**：`period` 管粒度，`limit` 管窗口长度。设计依据：period 切粒度有质的语义价值；limit 满足"同粒度看更长"（趋势持续性 / CVD 起点 / 背离确认）。
  - ⚠️ **设计纪律**：不得用旧 `recent_trades` 的 `window_seconds` 94.6% 默认率论证"不开放参数"（数据错配陷阱，per `[[feedback_data_mismatch_old_impl_inference]]`）。

### 3.2 `get_taker_flow` 输出格式

```
=== Taker Flow (BTC-USDT-SWAP · 5m bars · @ 04:34 UTC) ===

Now (current 5m, 4.0/5min formed):  41% taker buy · net −5.8$M · vol 0.3× (vs 20-bar avg)
Window (6 bars = 30min):  CVD +109.8$M · 2/6 bars net-sell

Per-bar (bar open UTC, newest first; row 1 = current in-progress):
  Time     Buy%   Net($M)   RVol(×20-bar)   CVD($M)   Close
  04:30*    41%     −5.8    0.3×   +109.8    73531
  04:25     39%    −22.2    0.9×   +115.6    73553
  04:20     68%    +75.6    1.8×   +137.8    73650
  04:15     66%    +18.4    0.6×    +62.2    73626
  04:10     70%    +25.2    0.7×    +43.8    73278
  04:05     56%    +18.6    1.0×    +18.6    73059
  [* row 1 = current bar still forming (4.0/5min)]

1h-scale anchor (current 1h, 34min formed):  53% buy · net +62$M
```

**字段语义**：
- **Now**：最新一根（=当前 in-progress 桶）的 `buy% · net · RVol`，**标成型度**（`current 5m, X/Ymin formed`）。早期成型度低 → buy%/net 是小样本，agent 据成型度自行折价（fact-only，原则 1/7）。**类比边界**：成型度标注借 GMD in-progress header-hint 先例；但 GMD 把 in-progress candle **排除出表**、taker_flow 反而**保留为 row 1**（flow 是累计量、半成桶仍有意义）——后者是新 pattern，非 GMD 先例。
- **Window 汇总**：`limit` 根累计 `CVD` + 净卖 bar 占比。窗口大小**显式标**（`N bars = X min`），防不同 `limit` 的 CVD 误比。（均量 baseline 不在此显示——RVol 用固定 20-bar baseline，见下。）
- **Per-bar 表**：`limit` 行，**newest-first**（row 1 = 当前 in-progress 桶，带 `*` + 脚注成型度；CVD 列自最旧 bar 向上累加）。**结构层**对齐 `get_market_data` candles（列头/单位/`Time (open UTC)` 语义一致），行序不对齐。
  - `Time`：bar 开盘 UTC；`Close` 该 bar 收盘价，比 `Time` 晚一 period。
  - `CVD`：窗口内从**最旧** bar 累加 net（相对窗口，非全局），标 "CVD over last N bars"。**跨调用警告**：CVD 是**窗口相对累计量**——每次调用以当前窗口最旧 bar 为零点；同 `limit` 下最旧 bar 每 cycle 前滚一格、零点静默移动，故**不宜跨调用做 CVD 绝对比较**（wrapper docstring 同述）。
  - `RVol`：bar taker-total / **固定 20 根 closed bar 的 taker-total 均量**（baseline 不随 `limit`、排除 in-progress；从已 fetch 的 ≥21 点取最近 20 根 closed）。标 **`RVol(×20-bar)`**——结构同 GMD `RVol(×SMA20)`（皆 20 根 closed 基准），量纲是 taker-vol（≈ bar 总量，每笔成交恰一 taker）故 ratio 可比，但**不宣称等同 GMD**（量纲 USD-notional vs candle base-vol）。in-progress row 1 的 RVol = 其部分量 / 20-bar baseline（读数偏低，靠 formed% 折价）。`limit=1` 也能算（vs 20-bar，**无退化**）。
  - `Close`：**嵌价格列**（一站式 CVD-价格背离）。**对齐契约**：flow 与 OHLCV **同期对齐**——rubik 的 in-progress 当前桶 ↔ OHLCV 的 in-progress 当前 candle 都是"当前"，两者皆近实时、**无 lag**，按 bar-open timestamp join（非按位）；个别 bar 找不到对应 OHLCV → `Close` 显 `—`。
    - **period=1d 例外（probe 实证）**：rubik 1D 用 16:00 UTC（HKT 日界）vs ccxt OHLCV "1d" 用 00:00 UTC → **0/10 对齐**；故 1d 的 `Close` **整列省略 + 显式标注**（`Close: n/a — 1d rubik/OHLCV 日界不一致`），**不逐格静默 —**；CVD/flow 列对 1d 仍有效（rubik-only）。5m/4h 实测对齐（20/20、10/10）；1h impl 前确认。impl 前再 probe 是否有 HKT 对齐的 OHLCV `bar` 变体可救 1d。
    - **通用安全网**：若某 period 的 `Close` **整列皆 —**，工具层显式降级标注（避免系统性失败伪装成零星缺失）。
- **context 锚点行**：fact-only 给上一档**当前 in-progress** bar 的 `buy% · net` + **成型度标注**（防"只看短期 flow 下结论"，对治 52% 过度解读），不贴背离/反转标签（详 §3.3）。
- **header**：`@ <取数 wall time> UTC`，**不标 data lag**（数据实时）；in-progress 状态由 Now 行 + per-bar `*` 脚注表达。
- **单位**：net/vol USD 名义，自适应 `$K/$M`（列头货币单位按品种量级 **per-invocation** 选取，同次调用内一致）；buy% 百分比；RVol 比值。

### 3.3 `get_taker_flow` context 锚点规则

锚点 = `period` 在粒度阶梯 `5m→1h→4h→1d→1w` 上**上一档的当前 in-progress bar**，**固定映射、不随 `limit` 变**。

| period（agent 可选）| context 锚点 |
|---|---|
| `5m` | `1h` |
| `1h` | `4h` |
| `4h` | `1d` |
| `1d` | `1w` |

- 取**上一档当前 in-progress 桶**（接口默认返回的最新那根），**标成型度**（`current 1h, 34min formed`）。理由：锚点是"我正在交易的这一档大节奏往哪走"的方向参照，**当前累积方向比上一根已完成更相关**（用户决策）；早期成型噪声（尤其 1w 周初）靠成型度标注让 agent 折价。
- **与主序列纪律一致**：主序列 row 1 也保留 in-progress（带成型度），锚点同款——**无"主丢弃 / 锚保留"非对称**。
- 实现需 `newest.ts + period_ms > now_ms` 检测**以标注成型度**（非剔除），mirror `_derive_oi_anchors` 的检测逻辑、但动作是 **keep+label** 而非 shift to `points[-2]`。
- **为何 flow keep 而 OI drop**（两 sibling rubik 工具新鲜度取舍相反）：flow 是**累计量**，半成桶是"未完成的和"，标成型度即可诚实保留；OI 是**存量水平**，半成桶是误导性瞬时读数，故 G-6 选 drop（取 `points[-2]`）。
- 锚点链止于 `1w`（`1w` 不作主 period）；每个可选 period 都有锚点，无省略例外。

### 3.4 `get_recent_trades` 重构（等笔数桶）

旧实现按 `5×60s` 时间桶渲染（4/5 桶恒空）；重构为**按笔数分桶**，诚实呈现"最近 500 笔"的秒级微观。服务 A 类（入场时点：此刻买卖压 + 短窗演化）。

```
=== Recent Trades (BTC-USDT-SWAP · last 500 · 40.9s · @ 04:34 UTC) ===

Taker buy:  40% by count · 49% by volume      Net: −$34.8K · 12.2 tr/s
Largest single:  $168K SELL  (= 12.7% of window vol)
Size (USD notional):  med $59 · mean $2.6K · p95 $9.7K

Per 100-trade slice (newest first):
  Slice    Span   Buy%(cnt)  Buy%(vol)    Net($)    MaxTrade
  1 (new)  8.1s     44%        58%       +$12.1K    $168K S
  2        7.4s     38%        41%       −$22.0K     $41K S
  3        9.9s     35%        44%       −$18.3K     $30K B
  4        7.2s     42%        52%        +$3.1K     $22K S
  5 (old)  8.3s     41%        50%        −$9.7K     $19K B
```

**设计要点**：
- **header**：`last <n> · <span>s · @ <wall time>`——把"500 笔覆盖多少秒"作 fact 标注（秒级、随活跃度 5–60s 浮动）。
- **聚合行**：全窗口 `buy%(count) · buy%(volume)` + `net($)` + `rate(tr/s)`。**count 与 volume 买占比都给**——实测背离 9–18pp（SOL 按笔 45%/按量 63%），是"散户笔数方向 vs 大单规模方向"的核心信号。
- **Largest single 行**：最大单笔 `$` + 方向 + 占窗口量%（实测 BTC 单笔达 12.7%，巨鲸信号免费可得）。
- **Size 行**：med/mean/p95 USD（极端 skew = 散户尘埃 + 巨鲸纹理）。
- **等笔数桶表**：固定 **5×100 笔**，newest-first。列 `Slice · Span · Buy%(cnt) · Buy%(vol) · Net($) · MaxTrade(含 S/B 方向)`。每桶 `Span` 不同（爆发 vs 清淡本身是信号）；`Net` 逐桶 → 看压力加速/衰减。
- **单位**：所有 `$` = `amount(base) × price`（逐笔各自成交价）——`amount` 已在 `fetch_trades` 适配器层由张归一为 base（§4.2），工具层零 cs 逻辑。标 "USD notional"（修单位③）。
- **不足 500 笔降级**（fact-only）：实际笔数 < 500 → 按实际笔数减桶 + 逐桶显真实笔数；极少（< ~100 笔）→ 降级为单聚合（无桶表）。
- 工具名**不变**（`get_recent_trades`）：改渲染不改身份，agent 心智零迁移。

**跨工具防撞（承重墙）**：recent_trades = `~40s 微观 / 笔数桶`；taker_flow = `分钟级 bars / 时间桶`。两者 header 显式不同窗口（`last 500 · 40.9s` vs `5m bars`），buy%/规模都带各自窗口标签 → agent 不会跨工具对账误判（同名异义显式区分，原则 3/7）。

### 3.5 错误与降级（两工具，fact-only，不 guard）

- **非法参数**：taker_flow 非法 `period`（不在 `{5m,1h,4h,1d}`）/ `limit` 越界（不在 `[1,36]`）→ **fact-only explicit reject** 文案（如 `period must be one of: 5m, 1h, 4h, 1d`），不 `KeyError`、不静默 clamp（soft-constraint §2）；签名保持 `str`/`int`，**不收紧 `Literal`**（fact-provider 不加 schema 约束，soft-constraint §1）。
- **接口异常 / partial-failure 降级层级**（taker_flow 每次 3 个未缓存调用：主 rubik + OHLCV + 锚点 rubik；OKX rubik 限额宽松，rate-limit 无虞）：**主 rubik 失败** → 整工具 `Taker flow temporarily unavailable ({ClassName}).`（无 flow 数据无法渲染）；**OHLCV 失败** → `Close` 整列降级标注（§3.2）、flow 列照渲染；**锚点 rubik 失败** → 丢锚点行、主序列照渲染（同 `_derive_oi_anchors` anchors-skipped-silently）。recent_trades 接口异常 → `Recent trades temporarily unavailable ({ClassName}).`
- **空数据**：taker_flow → `=== Taker Flow (...) ===\nNo taker-volume data available.`；recent_trades → 同型 header + `No recent trades.`
- recent_trades 返回点数 < 桶需求 → §3.4 降级；不报错。
- taker_flow `Close` 整列无对应 OHLCV（如 period=1d 日界不一致）→ 整列显式降级标注，非逐格静默 `—`（§3.2）。

## 4. 架构与数据流

### 4.1 `get_taker_flow`（新增）

```
get_taker_flow (工具, tools_perception.py)   # 显示 limit 行；内部 fetch n=max(limit+1,21)（够固定 20-bar RVol baseline + in-progress）
  → market_data.get_taker_flow(symbol, period, n)        # 不缓存
    → SimulatedExchange / OKXExchange.fetch_taker_flow(symbol, period, n)
      → _ccxt.public_get_rubik_stat_taker_volume_contract({instId, period(大写), unit:"2", limit:n})
  + market_data.get_ohlcv_dataframe(symbol, period, n)    # 价格对齐：in-progress↔in-progress 同期，按 bar-open ts join，无 lag 缓冲
  + 额外取 1 根上一档粒度的 context flow bar（§3.3 映射，含 1d→1w；取 in-progress + 标成型度）
```

新增：
- **`TakerFlowBar`**（base.py model）：`ts: int / sell_usd: float / buy_usd: float`。
- **`fetch_taker_flow(symbol, period="5m", limit=6) -> list[TakerFlowBar]`**：base.py 抽象 + `simulated.py` + `okx.py` 实现。
  - instId 映射 `_ccxt.market(symbol)["id"]`（`BTC/USDT:USDT` → `BTC-USDT-SWAP`）。
  - **新建 `_TAKER_VOLUME_PERIOD` 全量映射**（5 项含 1w），**不复用** `_OKX_OI_PERIOD`。
  - ccxt 方法名 snake_case（`public_get_rubik_stat_taker_volume_contract`，与代码库 `public_get_rubik_stat_contracts_open_interest_history` 一致）。
  - parse `[ts, sell, buy]`（字符串→数值），按 ts 升序返回。
  - **返回含最新 in-progress 桶的升序列表（不 shift/drop）**：fetch 层**不做检测**，仅返回全部 raw 升序点（含 in-progress newest）。in-progress 检测（`newest.ts+period_ms>now_ms`）+ 成型度计算/标注归**工具层**（`_derive_oi_anchors` 同属工具层 `:1049`；OI 的 fetch 层也只返 raw 升序点 —— `TakerFlowBar` 亦无成型度字段，fetch 层传不了标注）。
  - `okx.py` 同步实现（base 抽象契约；实盘暂不用，per CLAUDE.md「维护通过测试」）。
  - **新 `@abstractmethod` 的 blast radius**：会破坏 **11 个完整** `BaseExchange` 测试 stub（共 12 个子类，`IncompleteExchange` 是 abstractness 负向测试、本就 incomplete 不破坏；分布于 `test_exchange.py` / `test_price_level_alert.py` / `test_tool_enhancement.py`）→ 同步补 stub 实现（或 `base.py` 给默认体），否则实例化 `TypeError`。
- **`market_data.get_taker_flow(symbol, period, limit)`**：**不缓存**（区别于 `get_open_interest_history` 的 180s `_derivatives_cache`）——taker_flow 的价值是实时 in-progress 桶，缓存会令 formed% 失真、且与未缓存的 `get_ohlcv_dataframe` 错位（破坏 §3.2 同期 join）。
- **`get_taker_flow` 工具**（tools_perception.py）：以 fetch 数 `n=max(limit+1, 21)` 调 `fetch_taker_flow`（够固定 20-bar RVol baseline + in-progress），显示最新 `limit` 行；OHLCV 同期对齐 + context 锚点，**做 in-progress 检测（`newest.ts+period_ms>now_ms`）**、算 buy%/net/CVD（窗口 `limit`）/ RVol（vs 固定 20-bar）+ 成型度（formed%），渲染 §3.2。
- **`trader.py` wrapper** `@tool`：docstring 用完整 call→output 示例（LLM 通道走 wrapper docstring，per `[[project_tool_docstring_llm_channel]]`；`Returns:` 块整段进 LLM，避免 griffe 剥离 block-style，per `[[project_griffe_example_section_stripped]]`）。
- **注册**：`get_taker_flow` 加入 `trader.py::REGISTERED_TOOL_NAMES`（感知工具 19→20，更新计数注释），过 drift-guard `test_registered_tool_names_matches_agent_tools`（`test_trader_agent.py:66`）。

### 4.2 `get_recent_trades`（重构渲染 + 单位换算；管线不退役）

- **不退役** `Trade` / `fetch_trades`（base/okx/sim）——recent_trades 保留。
- 改动面：(a) `simulated.py` / `okx.py` `fetch_trades` 加单位③归一；(b) `tools_perception.py::get_recent_trades` 渲染层（时间桶 → 等笔数桶 §3.4）。
- **单位③在 `fetch_trades` 适配器层修（张→base 归一化），不用执行层 `get_contract_size`**：sim 的 `get_contract_size=1.0` 是**执行层** cs（撮合/notional 假设 quantity=base，load-bearing，本 iter 不动）；而 raw `_ccxt.fetch_trades` 的 `amount` 是 OKX **张**（真实 *market* cs 基准）——两者在 sim 里值不同。故在 `simulated.py` / `okx.py` 的 `fetch_trades` 内读真实 market contractSize（`_ccxt.market(symbol)["contractSize"]` / `_client.market(...)`，循环外取一次）把 `amount` 张→base 归一化，使 `Trade.amount` 真正等于模型注释声称的 `# base-currency`。recent_trades 随后 `amount(base) × price = USD`，工具层零 cs 逻辑。
  - 这是适配器把交易所原生单位归一到 canonical base 的**永久正确职责**（非临时绕过），独立于执行层 cs；即使将来 F1（`get_contract_size` 改真）落地也零返工。`get_contract_size=1.0` / precision=config / mark=last 等执行层保真问题由**紧接的独立「sim 执行保真」iter** 收（per `[[feedback_sim_real_data_except_order_mgmt]]`）。
  - markets 已加载（OI 路径 `:1047` 已用 `market()`；`okx.py:911` 有 `if not markets: load_markets()` 先例）。`fetch_trades` 唯一消费者是 recent_trades（撮合不碰），归一化语义变更安全。
  - **量纲护栏**：Option B 后 sim 内 `trade.amount`(base) 与 `position.contracts`(base，执行 cs=1.0) **同尺度**、无冲突；分裂仅在 okx（`trade.amount`=base vs `position`=张，okx 未用 + recent_trades 仅展示）。仍立护栏：**禁止跨 `trade.amount` 与 `position`/`order.amount` 直接运算**（okx 下量纲不同）。
- `RECENT_TRADES_*` 时间桶常量替换为笔数桶常量（`SLICE_SIZE=100` / `N_SLICES=5`）。
- `trader.py` wrapper docstring 同步更新（call→output 示例换 §3.4 新格式）。
- `cli/display.py` 注册不变（工具仍在）。

## 5. 测试策略

- **① taker_flow sim 层**：mock `_ccxt.public_get_rubik_stat_taker_volume_contract`，断言 parse / unit 透传 / ts 升序 / instId 映射 / 限频异常上抛 / **最新 in-progress 桶被保留（行数/存在；fetch 层不检测不标注）** / **列序 `[ts,sellVol,buyVol]`（fixture col2=sell·col3=buy，防方向翻转）**。
- **② taker_flow 工具层**：mock `market_data.get_taker_flow` + `get_ohlcv_dataframe`，断言 **in-progress 检测（`ts+period>now`）+ formed% 计算/标注（在工具层）** / CVD 累加 / **RVol（baseline = 固定 20 根 closed，不随 limit；fetch `n=max(limit+1,21)`；`limit=1` 也算 vs 20、无退化）** / **in-progress↔in-progress 同期 ts join（个别 bar 无 OHLCV → `Close` 显 `—`）** / **period=1d `Close` 整列降级标注（非逐格 —）+ "整列皆 — → 降级"安全网** / 分段结构 / **锚点取 in-progress + 标成型度（`5m→1h / 1h→4h / 4h→1d / 1d→1w`，无省略，与 §3.3 一致）** / **非法 period 与越界 limit → explicit reject 文案** / **partial-failure 降级（主 rubik 失败→整工具 unavailable / OHLCV 失败→Close 整列降级 / 锚点 rubik 失败→丢锚点行、主序列照渲染）** / 空数据降级。
- **③ 映射完备性**：period 白名单 `{5m,1h,4h,1d}` + 锚点链档（含 `1w`）全在 `_TAKER_VOLUME_PERIOD`（防 KeyError 回归）。
- **④ recent_trades（两层）**：
  - **适配器层**（`simulated.py` / `okx.py` `fetch_trades`）：mock `_ccxt.fetch_trades` 返回 raw 张 + mock `market()["contractSize"]=0.01`，断言 `Trade.amount` = 张×0.01（base 归一化，防单位③回归）；同步更新现有 B 类 `fetch_trades` 测试期望 base。
  - **工具层**（mock `market_data.get_recent_trades` 返 base-amount Trade）：断言 USD=`amount×price` / **等笔数分桶（5×100，newest-first）** / count 与 volume 买占比都算 / Largest single + 方向 + 占窗口% / Size med/mean/p95 / **< 500 笔降级（减桶 + 真实笔数；< ~100 单聚合）** / 空数据降级。
- **⑤ mock 保真**（per `[[project_iter2_mock_fidelity_lesson]]`）：≥1 fixture 按 OKX 真实 rubik 响应（`[["ts","sellVol","buyVol"], ...]` 字符串数组，含列序）+ ≥1 真实 trades 形态（含 `info.sz` + `contractSize≠1` 的 BTC/ETH，覆盖张→base 归一盲区）。
- **⑥ 集成守护**：11 个完整 `BaseExchange` 测试 stub（`IncompleteExchange` 除外）同步实现 `fetch_taker_flow`（或 base 默认体）→ 全 stub 可实例化；`get_taker_flow` ∈ `REGISTERED_TOOL_NAMES` → drift-guard `test_registered_tool_names_matches_agent_tools` 通过——**含改其硬编码计数断言 `test_trader_agent.py:82` `len(...) == 33` → `== 34` + 报错串 `(19+14)` → `(20+14)`**（否则该断言必挂）。

## 6. 范围边界与已知遗留

- **本 iter**：`get_taker_flow` 新增 + `get_recent_trades` 笔数重构（含单位③）。`get_order_book` % 精度 + concentration 噪声**另开 iter**。
- **两工具并存 + recent_trades 去留 deferred + 可执行 gate**：recent_trades 不是被 taker_flow 替代（二者时间分辨率不同、互补），但其对分钟级 agent 的真实价值未证（§1 数据错配 caveat）。**本次重构落地后的下个 sim run** **用 §1 的 A/B/C/D grounding 分类口径**（看合理 grounding 率，非裸调用%）评估：若 B 类过度解读仍主导、A 类（入场确认）grounding 可忽略 → 说明秒级 tape 对此 agent 无实益 → 启动工具反思（能力/描述/默认值/接口，per 原则 8），含"是否退役"。**阈值不预设 false-precision 数，由首个真实 session 的 baseline 校准**（区别于 `[[project_r2_next_g_followups]]` 的 docstring-promotion 语境，不直接套其梯度）。
- **taker_flow 对称 gate**：taker_flow 是本 iter 更大投入（新端点 + model + sim/okx/market_data/工具/wrapper），同样设 post-launch review——agent 是否把 B 类 flow 解读 **grounding 到 taker_flow** + 是否较无此工具增值；低 grounding / 低增值 → 同样工具反思，不默认留存。
- **本 iter 上线后 watch-list（与上述 grounding review 同批观察）**：① **跨工具 `taker buy%` 对账困惑**——recent_trades(~40s) 与 taker_flow(分钟级) 双源，承重墙窗口标签是否兜住（narrative grep，呼应 mts current_price vs ticker ≥8 cycles 先例）；② **agent 是否据成型度折价低成型 in-progress 桶**——keep+label 设计依赖此行为假设（尤其 1w 周初 / 低成型 Now），per 原则 8 实测，标签失效则工具反思。
- **巨鲸/大宗**：recent_trades 已 surface 最大单笔 + 集中度（普通 feed 内）；专门大宗（block trades）后续候选 `public_get_public_block_trades`（低频靶向）。
- **执行层保真（F1/F2/F3）独立 iter**：本 iter 在行情层（`fetch_trades`）读真实 market contractSize 做单位归一；**执行层** `get_contract_size=1.0`（F1）、precision=config（F2）、mark=last（F3）的修复（牵动撮合/notional 的张语义）由**紧接的独立「sim 执行保真」iter** 收（per `[[feedback_sim_real_data_except_order_mgmt]]`），与本 iter 正交、零返工；完整元数据层（precision / min size）亦在该 iter（`[[project_sim_market_data_fidelity]]`）。
- **可比性**：rubik + trades 均真实 live，跨 session 非确定（与 ticker/OHLCV 同性质，`analyze_sim`/`diff_sim` 可比性性质不变）。

## 7. 实证依据附录

- **probe ①（taker-volume，已落 `.working/tool-optimization/probe_okx_taker_volume_contract.py`）**：2026-05-30 实拉，全 5 档（5m/1H/4H/1D/1W）最新一根均为 **in-progress 当前桶**（`ts+period>now`），数据近实时、无发布滞后；`ts`=桶开盘、间隔=period；100 点/period（1M 27 点）；unit 0/1/2=币/张/USD。退到"最新已完成桶"才会引入 ~5–10min（5m）数据年龄——保留 in-progress 桶即无滞后。
- **probe ②（recent_trades，已落 `.working/tool-optimization/probe_recent_trades_real.py`）**：BTC/ETH/SOL 各 500 笔——覆盖 41/28/57s；count vs volume 买占比背离 9/12/18pp（SOL 按笔 45%/按量 63%）；规模长尾（BTC 单笔最大=窗口 12.7%、top5=35%）。
- **probe ③（判别验证，已落 `.working/tool-optimization/probe_verify_assumptions.py`）**：
  - **A 单位③判定**：`ccxt amount == raw info.sz`（5/5）、`!= sz×cs` → `amount` 是张（未换算）；量级佐证（max 228 张×0.01=$168K 合理 vs 228 BTC=$16.7M 荒谬）→ ×contractSize 归一为 base **方向确认**（BTC 单笔中位真实 $59）。
  - **B 列序**：`(col3−col2)` 符号 == `(close−open)` 符号 **14/20（70%）** + OKX `[ts,sellVol,buyVol]` 约定 → col3=buy（`[ts,sell,buy]`）支持；impl 加列序断言坐实。
  - **G limit**：传 `limit=6` → 返 6 行，端点 **honor limit**（§3.1 拉取根数=明细行数成立）。
  - **E OHLCV 对齐**：5m **20/20**、4h **10/10** 对齐；**1d 0/10**（rubik 16:00 UTC HKT 日界 vs OHLCV 00:00 UTC）→ period=1d Close 整列降级（§3.2）。
- **fetch_trades 上限**：默认接口 500 硬 clamp；history-trades 100/页；paginate 1000 笔仅覆盖 ~3s（逐笔堆"几分钟"物理不可行）。
- **旧 session 行为分类**：A=3.5% / B=52% / C=39% / D=4%（§1，含数据错配 caveat）。
- **smoke（2026-05-30, BTC/ETH/SOL，更早 bursty 一轮）**：逐笔 500 笔覆盖 5.6/5.8/27.8s；4/5 时间桶恒空。
