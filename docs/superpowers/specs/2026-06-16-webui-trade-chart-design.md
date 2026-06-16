# WebUI 会话价格 K 线 + 买卖点可视化

## 背景与动机

观察台「收益分析」抽屉(`PerformanceBar`)现有 净值曲线(盯市)+ Tier 指标 + A+ 交易历程表,能回答「赚了多少 / 每笔进出扣费」,但**看不到价格上下文**——agent 在什么价位进、什么价位出、那一笔进场相对当时行情是好是坏,只能在表里读裸价格数字脑补。sim 复盘的核心诉求之一正是「把 agent 的买卖点叠在标的真实价格走势上,一眼看出择时质量」。

本迭代新增一个**价格 K 线图**,加载会话运行窗口内标的的真实 OHLCV,并把交易记录的入场/出场点叠加为标记(markers)。这是纯观察侧能力,不碰 agent loop、不碰下单路径。

## 范围

**前端为主 + 一个新后端端点(读真实行情,不写库),无 DB 迁移、不动 `MetricsService`。**

新增能力分三块,三块积木均已在代码中存在,本迭代是组装 + 补端点:

- **OHLCV 数据源** — 复用 `scripts/fetch_session_ohlcv.py`(F7)已有的 OKX REST 拉取核心(按会话窗口分页 + 重试 + 排序去重 + 半开过滤,`ccxt.async_support.okx()` 无凭证,公开行情)。
- **入场/出场点** — 复用 `/performance` 已返回的 `trades`(`TradeRow`),经**与 A+ 历程表同一个 `deriveTradeFills`** 分类(单一口径,图表与表永不打架)。
- **图表** — `lightweight-charts ^4.2.0`(已是依赖,`EquityChart.vue` 已在用),v4 API:`addCandlestickSeries` + `series.setMarkers` + `chart.subscribeCrosshairMove`(注意勿误用 v5 primitive 形式)。

涉及文件:

- `src/services/ohlcv_history.py` — **新增**:把 F7 的窗口解析 + 分页拉取 + 重试 + 排序去重 + 半开过滤 + `TF_MS`/`TIMEFRAMES` 上提为共享核心;`fetch_ohlcv_window` 返回**裸行 `list[list]`**(与 `_to_dataframe` 输入形态一致,零破坏 F7,详见 §A)。
- `scripts/fetch_session_ohlcv.py` — 改为 import 共享核心,并**按旧私名 re-export**(`_resolve_session` / `_paginate_ohlcv` 等)使 F7 既有测试 import 不动;`_to_dataframe` + CLI + CSV writer 留在脚本(详见 §A)。
- `src/webui/ohlcv_cache.py` — **新增**:文件层缓存(读 / 写 / 覆盖判定);缓存目录从**正在使用的 engine** 派生(`cache_dir_for(engine)`,剥 `file:` 前缀;`:memory:`/无路径 → 不缓存,详见 §B)。
- `src/webui/queries.py` — **新增** `get_ohlcv(engine, sid, timeframe)`:归一 tf(**复用 `src/utils/timeframe.py::normalize_timeframe`** + 6 框白名单)→ 解析窗口 → 查缓存 → miss 则拉取(裸行)+ 落盘 → 转 `OhlcvBar` 返回 `OhlcvSeries`。
- `src/webui/schemas.py` — **新增** `OhlcvBar` + `OhlcvSeries`。
- `src/webui/app.py` — **新增**端点 `GET /api/sessions/{sid}/ohlcv?timeframe=<tf>`。
- `frontend/src/components/PriceChart.vue` — **新增**:K 线 + markers + hover tooltip + timeframe 切换器(详见 §C–§E)。
- `frontend/src/utils/markers.ts` — **新增**:`toCandleData` / `snapToBarTime` / `toMarkers` 纯函数(消费 `DerivedFill`,单一口径)。
- `frontend/src/components/PerformanceBar.vue` — 抽屉内新增整宽 section 挂载 `PriceChart`(详见 §F);从 `store.detail` + `store.performance.trades` 取数,父级零改动。
- `frontend/src/api/client.ts` — 新增 `api.getOhlcv(sid, tf)`。
- `frontend/openapi.json` + `frontend/src/api/types.ts` — 随 schema 变更重生成(`npm run gen:types`)。

### 不做(YAGNI / 数据驱动触发)

- **价格 K 线与净值曲线时间轴联动**(crosshair / 缩放同步)。布局选项 3,留 follow-up;MVP 两图各自独立。
- **增量 tail-merge 缓存**。活跃会话窗口增长时走全量重拉(sim-focus = 多为已结束会话,缓存恒命中);tail-merge 等长活跃会话成痛点再做。
- **缓存并发去重(per-key in-flight lock)**。同一未缓存 `(sid, tf)` 被并发首开 → 双拉 + 落盘覆盖(幂等,后果仅浪费一次拉取);MVP 接受,成痛点再加锁(见边界)。
- **「反手」专属标记**。fill 级无此类型——反手在数据里天然呈现为「平旧 + 开新」相邻两标记,如实呈现,不另造。
- **OKX 实盘 / 下单路径**。sim-only 纪律,不碰。
- **多标的 / 跨会话叠加**。单会话单标的。

## 数据来源与派生

| 展示项 | 来源 / 派生 |
|---|---|
| K 线 OHLCV | `ohlcv_history.fetch_ohlcv_window`(OKX REST,会话 `[created_at, last_active_at)` 窗口,排序+去重+半开过滤,timeframe 参数化) |
| 会话窗口 + symbol | `Session.created_at` / `last_active_at`(NULL 回退 `updated_at`)/ `symbol`(复用 F7 `resolve_session_window` 逻辑) |
| 默认 timeframe | `SessionDetail.timeframe` **经归一**(`1H→1h`,见 §C)——agent 实际感知的分辨率 |
| 可切 timeframe | `TIMEFRAMES = (1m, 5m, 15m, 1h, 4h, 1d)`(F7 共享常量) |
| 买卖点 markers | `/performance` 的 `trades` 经 `deriveTradeFills` → `DerivedFill[]`(与 A+ 表同源) |
| marker 时间 | `snapToBarTime(epochSec(fill.at), barTimes)` —— 吸附到实际加载的 K 线时间(见 §D) |
| marker 方向(多/空着色) | `DerivedFill.side` |
| marker 动作(开/加/平) | `DerivedFill.grossPnl`(null=开/加型,非 null=平仓型)+ `isAdd` + `type` 文本 |
| hover 详情 | `DerivedFill.{type, side, price, amount, grossPnl, finalPnl, trigger_reason}` |

**口径一致性:** markers 与 A+ 历程表消费**同一个** `deriveTradeFills` 输出(同一个 legacy null-amount 跳过、同一个 开/加/平 分类),故图上标记数与表行数、类型标签逐字一致(原则 3:信号唯一权威来源)。

## 设计

### §A 后端共享核心 `ohlcv_history.py`

把 F7 脚本中与「拉取」相关、与「CLI/CSV 落盘」无关的部分上提为可被 webui 复用的模块:

- `TF_MS: dict[str, int]` / `TIMEFRAMES: tuple[str, ...]` — 时间框毫秒表(F7 §3.4 drift-guard 锁定的硬编码,**不走 ccxt.parse_timeframe**)。
- `resolve_session_window(engine, session_id) -> tuple[str, int, int]` — 返回 `(symbol, start_ms, end_ms)`,对应 `[created_at, last_active_at)`(`last_active_at` NULL 回退 `updated_at`);未找到 / 零时长 → `ValueError`(原 `_resolve_session` 重命名上提)。**只借用传入 engine(经 sessionmaker 开 session)、不 `dispose`**——webui 路径传的是共享只读 engine,绝不能被关(对应审查独立补点)。
- `fetch_ohlcv_window(symbol, timeframe, start_ms, end_ms) -> list[list]` — `client = ccxt.async_support.okx()`(**属性形式调用,不 `from ccxt.async_support import okx`**——F7 测试用 `monkeypatch.setattr("ccxt.async_support.okx", …)` 全局属性 patch,绑名 import 会绕过 patch);`try:` `_paginate_ohlcv`(游标按末根 ts + tf_ms 前进,终止:游标≥end / 空页 / 末根不前进)→ **`rows.sort(key=ts)` + 同 ts 去重 + 半开过滤 `start_ms <= r[0] < end_ms`**(原 F7 主入口里的三步,整体迁入);`finally: await client.close()`(**异常路径也 close**,守 AC-F7-14)。**返回裸行 `list[list]`**(升序、`[[ts_ms, o, h, l, c, v], …]`);窗口内 OKX 无数据 → `[]`(不抛);重试耗尽的瞬态错(`NetworkError` / `ExchangeNotAvailable` / `TimeoutError`)→ re-raise。ccxt okx 默认 `timeout=10000`(10s/调用),**不显式传**以保 F7 客户端构造零行为变化(最坏耗时见 §C)。
- `_paginate_ohlcv` / `_fetch_with_retry`(3 次尝试,sleep `[1.0, 2.0]`)— 原样迁入;**连同其模块级依赖** `_RETRY_SLEEP_SCHEDULE` / `_THROTTLE_SLEEP_S` / `_PAGE_LIMIT`(分页/限流/重试参数)+ `resolve_session_window` 依赖的 `_ensure_utc`,一并搬入共享模块(显式列出免遗漏)。

**F7 脚本零破坏改造**(对应审查 #4):

- `scripts/fetch_session_ohlcv.py` 顶部 `from src.services.ohlcv_history import resolve_session_window as _resolve_session, _paginate_ohlcv, _fetch_with_retry, fetch_ohlcv_window, TF_MS, TIMEFRAMES`——**按旧私名 re-export**,使 `from scripts.fetch_session_ohlcv import _resolve_session / _paginate_ohlcv` 的存量测试 import **完全不动**。
- `fetch_ohlcv_window` 返回**裸行 `list[list]`**(非 dict),正是 `_to_dataframe(rows: list[list])` 现有的输入形态——`_to_dataframe` + `_write_csv` + CLI 留在脚本、**一行不改**,`test_to_dataframe_schema` 等测试照旧绿。
- 脚本主入口 `fetch_session_ohlcv` 改为:`engine = create_async_engine(...)` → `try:` `symbol, start, end = await resolve_session_window(engine, sid)` → `rows = await fetch_ohlcv_window(symbol, tf, start, end)` → `df = _to_dataframe(rows)`(排序/去重/半开过滤已在核心内完成)→ `finally: await engine.dispose()`。**保留自有 engine `try/finally` dispose**,使 `test_fetch_resource_cleanup_success_AC_F7_13`(`close` 与 `dispose` 各 1 次)/ `_on_exception_AC_F7_14`(异常仍 close+dispose)原样绿——client 的 close 现落在 `fetch_ohlcv_window` 的 finally、engine 的 dispose 仍在脚本主入口,两测端到端计数不变。
- `TF_MS` drift-guard(AC-F7-4)随常量迁移,断言点改指共享模块(或经 re-export 仍命中)。

### §B 文件缓存 `ohlcv_cache.py`

webui 用**只读 DB 引擎**,缓存写文件(不写库)。缓存目录从**正在使用的 engine** 派生(对应审查 #1):

- `cache_dir_for(engine) -> Path | None`:取 `engine.url.database`;为 `None` / `":memory:"` / 空 → 返回 `None`(降级为不缓存,每次实拉);否则 `db = database.removeprefix("file:").split("?", 1)[0]` → `Path(db).parent / "ohlcv_cache"`。
  - **为何从 engine 派生而非新增 `app.state.db_path`(否决审查给的「更干净」假设性备选):** 注意 `app.state.db_path` **当前并不存在**(`create_app` 仅设 `app.state.engine`,app.py:23),它是上一轮审查**建议新增**的方案。即便新增也不可行:端点测试是 `create_app()`(默认 `data/tradebot.db`)+ `dependency_overrides[get_engine]` 注入**内存** engine;`app.state.db_path` 会停在 `data/tradebot.db`,导致**测试期往真 `data/` 写脏缓存**。从 engine 派生才天然跟随 override:生产只读库 `file:/abs/data/tradebot.db` → 剥 `file:` → `data/ohlcv_cache/`;端点测试 `:memory:` → `None`(不落盘);query 测试 tmp DB `/tmp/...db` → tmp 目录(随测试清理)。
- 缓存文件 `<sid>_<tf>.json`,内容 `{"symbol", "timeframe", "fetched_end_ms", "bars": [[ts,o,h,l,c,v], …]}`。
- `read(cache_dir, sid, tf, current_end_ms) -> list[list] | None` — `cache_dir` 为 None → None;文件存在 **且 `current_end_ms <= fetched_end_ms`**(覆盖判定:历史/已结束会话 `==` 恒命中;活跃会话窗口增长 → `>` → miss)→ 返回裸行;否则 `None`。
- `write(cache_dir, sid, tf, symbol, fetched_end_ms, bars)` — `cache_dir` 为 None → no-op;否则 `mkdir(parents=True)` + 覆盖写。

历史 sim 窗口固定永不过期,故缓存无 TTL,只靠 `fetched_end_ms` 覆盖判定。

### §C 端点 + schema + tf 归一

**schema(`schemas.py`):**

```python
class OhlcvBar(BaseModel):
    at: UtcDatetime          # 该 K 线开盘时刻(ts_ms → aware UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float

class OhlcvSeries(BaseModel):
    symbol: str
    timeframe: str            # 归一后的小写形态(1h 等)
    bars: list[OhlcvBar]      # 升序、同 ts 去重、半开过滤;窗口内无数据 → []
```

**tf 归一(对应审查 #3):** DB 存量 `sessions.timeframe` 含大写 `1H`(2 会话)与小写 `1h`(4)混用,而 `TIMEFRAMES` 全小写(`fetch_session_ohlcv:166` 脚本主入口尚有 `assert timeframe in TF_MS`,`_paginate_ohlcv` 则直接 `TF_MS[tf]` → KeyError;重构后 query 层已归一+白名单收窄,core 拿到的恒合法,该 assert 可有可无)。**复用既有单一归一点 `src/utils/timeframe.py::normalize_timeframe`**(sim#17 `1H` 崩溃的修复产物,已被 config/tools_perception/wizard 复用;原则 3/4——**不自造 `.lower()`**:`.lower()` 会把 `1M`(月)误折成 `1m`(分),而 `normalize_timeframe` 刻意只 fold H/D/W、保 m/M 区分)。两层职责分明:`normalize_timeframe` 做**规范化**(`1H→1h`,非法 → `ValueError`,其 `SUPPORTED_TIMEFRAMES` 15 框是超集)→ 本端点再用**自有 6 框白名单 `TIMEFRAMES`** 做图表收窄(`TF_MS` 键 = 可拉取集 = 这 6 框)。

- **显式传入** tf:`normalize_timeframe(raw)` → ∉ `TIMEFRAMES` → `400`。
- **默认路径**(省略 tf):取会话 `timeframe` → `normalize_timeframe` → 若 ∈ `TIMEFRAMES` 直接用;**若归一后落在 6 框外**(如未来 `30m`/`2h`/`1M` 会话,在 15 框 `SUPPORTED` 内但不可拉取)→ **一律兜底 `1h`**(确定性规则,不做模糊的「最近较粗框」匹配),而非 400/落空,保证默认请求恒能渲图(当前 DB 全部 `{1H,1h,15m,1m,5m}` 都 ∈ 6 框,兜底不触发,属未来稳健)。

**query(`queries.py`):** `get_ohlcv(engine, sid, timeframe) -> OhlcvSeries`:
1. 解析 `tf`:`timeframe` 省略 → **单独查一次 `SessionModel.timeframe`**(因 `resolve_session_window` 三元组签名被 F7 re-export 契约冻结、不含 tf,不能扩成四元组)→ `normalize_timeframe` → ∈ 6 框直接用、否则兜底 `1h`;显式传入 → `normalize_timeframe` → ∉ `TIMEFRAMES` → 抛(端点转 400)。
2. `symbol, start_ms, end_ms = resolve_session_window(engine, sid)`(未知 sid → `ValueError`)。
3. `cache_dir = ohlcv_cache.cache_dir_for(engine)`;`rows = ohlcv_cache.read(cache_dir, sid, tf, end_ms)`;命中 → 跳到 5。
4. miss → `rows = await fetch_ohlcv_window(symbol, tf, start_ms, end_ms)` → `ohlcv_cache.write(cache_dir, sid, tf, symbol, end_ms, rows)`。
5. 裸行 → `OhlcvBar`(`at = datetime.fromtimestamp(r[0]/1000, tz=utc)`,o/h/l/c/v = r[1:6]),构造 `OhlcvSeries`。

**端点(`app.py`):**

```
GET /api/sessions/{sid}/ohlcv?timeframe=<tf>
```

- `timeframe` 省略 → query 内取会话 `timeframe` 归一 + clamp 兜底;显式传入 → 归一后须 ∈ 6 框。
- 未知 sid(`resolve_session_window` 抛 `ValueError`)→ `404`。
- 显式 tf 归一后不 ∈ `TIMEFRAMES`(含 `normalize_timeframe` 抛 `ValueError`)→ `400`。
- 拉取失败(重试耗尽的瞬态 ccxt 错)→ `503`,`detail = type(e).__name__`(**仅类名,redaction 纪律**)。
- 成功 → `200` + `OhlcvSeries`(`bars` 可能为 `[]`)。
- **最坏耗时(对应审查 #7):** ccxt okx 默认单次 REST 调用超时 10s(非新约束);最坏 ≈ 页数 × 10s × 3 重试。粗粒度短会话(会话自身 tf,常 1–2 页)秒级;细粒度长会话(如 5 天 1m ≈ 72 页)首拉数十秒、落盘后秒开。属观察工具可接受范围,前端 `loading` 占位兜住。

### §D 前端纯函数 `markers.ts`

**`toCandleData(bars: OhlcvBar[]): CandlestickData[]`** — 镜像 `EquityChart.toSeriesData` 处方:`at` → 秒级 `UTCTimestamp`、升序、同秒去重保留最后;映 `{ time, open, high, low, close }`。

**`snapToBarTime(atSec: number, barTimes: number[]): number`**(对应审查 #2 核心修法)— `barTimes` 升序(取自 `toCandleData` 的 time 列)。返回 ≤ `atSec` 的最大 `barTime`(即包含该成交的 K 线开盘时间);`atSec` 早于首根 → 钳到首根;`barTimes` 空 → 返回 `atSec`(无图可标,markers 实际为空)。**用实际加载到的 candle 时间吸附**,而非 floor 到 tf 边界——后者在行情有缺口(某根 candle 缺失)时会指向不存在的时间,`param.time` 仍 miss。

**`toMarkers(fills: DerivedFill[], barTimes: number[]): SeriesMarker[]`** — 消费 `deriveTradeFills` 输出(已剔 legacy null-amount):

```
对每个 fill:
  time     = snapToBarTime(epochSec(fill.at), barTimes)  # 与 hover map 键同源,保 param.time 命中
  isOpen   = fill.grossPnl == null                        # 开/加型(与表同判据)
  position = isOpen ? 'belowBar' : 'aboveBar'             # 进场标在下、出场标在上
  shape    = isOpen ? 'arrowUp' : 'arrowDown'
  color    = fill.side === 'long' ? POS_HEX : fill.side === 'short' ? NEG_HEX : MUTED_HEX
  text     = isOpen ? (fill.isAdd ? '加' : '开') : '平'    # 常驻短标签;细分/数值留 hover
return markers.sort((a, b) => a.time - b.time)             # lightweight-charts 要求按 time 升序
```

`POS_HEX` / `NEG_HEX` / `MUTED_HEX` 镜像 `--ob-pos #15803d` / `--ob-neg #dc2626` / `--ob-text-muted #6b7280`(canvas 不能读 CSS 变量,以常量镜像并注释保持同步)。markers 常驻标签刻意极简(开/加/平 1 字),细分(止损/止盈/强平/限价)与 价格/数量/PnL/触发原因 全部走 hover(§E,选定「完整动作 + hover 详情」)。

### §E `PriceChart.vue`

复用 `EquityChart` 的 **`toSeriesData` 式数据处理(epoch 转换/升序/去重)+ `onUnmounted → chart.remove()` 生命周期**;但 candlestick + markers + crosshair hover 是**净新增、`EquityChart`(line,无 marker/crosshair)无先例**(与审查 #2/#6 互证),需自建并以测试守住。

- **Props:** `sessionId: string`、`symbol: string`、`defaultTimeframe: string`、`trades: TradeRow[]`。
- **State:** `tf`(ref,init = `defaultTimeframe`,组件内 `normalizeTf` 一致)、`bars`、`loading`、`error`(ref)。
- **取数:** `watch(tf)` → `api.getOhlcv(sessionId, tf)` → 填 `bars`;`loading` 期显占位、`ApiError` → `error` 占位(图隐,A+ 表仍可用)。
- **图:** `addCandlestickSeries`(涨/跌色用 `--ob-pos`/`--ob-neg` 镜像);`barTimes = toCandleData(bars).map(c => c.time)`;`series.setData(toCandleData(bars))`;`series.setMarkers(toMarkers(deriveTradeFills(trades), barTimes))`;`timeScale().fitContent()`。
- **hover tooltip:** 预构 `Map<number, DerivedFill[]>`,**键 = `snapToBarTime(epochSec(fill.at), barTimes)`(与 marker.time 同源)**;`chart.subscribeCrosshairMove` → `param.time`(bar 对齐时间)命中该 map → 浮层 div 列该时刻每笔:`{type} · {多/空} · 价 {price} · 量 {amount} · [平仓行] 毛利 {grossPnl} / 最终 {finalPnl} · {trigger_reason 中文}`;未命中 → 隐藏浮层。
- **timeframe 切换器:** `n-radio-group`(naive-ui,pin 2.38.1)`1m/5m/15m/1h/4h/1d`,默认高亮会话(归一后)tf;切换写 `tf` → 触发重拉(细 tf 首拉有 loading,缓存后秒开)。
- **生命周期:** `onUnmounted` → `chart.remove()`(同 EquityChart)。
- **空 trades:** 只渲 K 线、无 markers(如实);空 bars → 空态占位「该窗口无行情数据」。

### §F 布局(PerformanceBar 抽屉内)

展开态抽屉内,在现有 `净值曲线 | 指标格` grid **上方**新增一条整宽 section(布局选项 1「独立整宽区块」):

```
┌ 收益分析 ▾   已实现指标 vs 盯市曲线 不同口径、不可逐点对账            ┐
├──────────────────────────────────────────────────────────────────┤
│ 〔当前持仓(未平仓)条 — 仅有持仓时,见 perf-panel-redesign §F〕    │
├──────────────────────────────────────────────────────────────────┤
│ 价格走势 · BTC/USDT:USDT      [1m 5m 15m ●1h 4h 1d]               │  ← 新增
│ ┌────────────────────────────────────────────────────────────┐  │
│ │  [PriceChart — candlestick + 开/加/平 markers + hover]       │  │  ~280px
│ └────────────────────────────────────────────────────────────┘  │
├───────────────────────────┬──────────────────────────────────────┤
│ 净值曲线(盯市·含未实现)   │ Tier 1 六格 / Tier 2                  │
│ [EquityChart]             │                                       │
├───────────────────────────┴──────────────────────────────────────┤
│ 交易历程(N 笔 · 净 X)▸                                          │
└──────────────────────────────────────────────────────────────────┘
```

`PerformanceBar` 从 `store.detail`(`id` / `symbol` / `timeframe`)+ `store.performance.trades` 取 props 传入 `PriceChart`;`store.detail` 为 null(加载中)时该 section 不渲。section 标题左「价格走势 · {symbol}」、右 timeframe 切换器。~280px 高,与净值曲线(120px)分区,不挤占。

**新依赖注记:** `PerformanceBar` 现仅消费 `store.performance`,本迭代新增对 `store.detail` 的读取(取 `id`/`symbol`/`timeframe`)——字段齐全;`detail` 与 `performance` 在 `selectSession` 是 `Promise.all` 同拉(stores/sessions.ts:73),门控「`store.detail` null → 不渲」与现有 `v-if="perf"` 同时序,无半态风险。

样式:section 用既有 `.ob-card` / `--ob-*` 令牌,同层级 hairline 兜底(沿用 boundary-polish §规则),不写死 hex(canvas marker/series 色除外,见 §D 镜像注释)。

## 边界与降级

- `store.detail` / `store.performance` 为 null:section 不渲染。
- OHLCV 拉取失败(网络 / OKX 不可达)→ 端点 `503` → 前端 `error` 占位「价格数据拉取失败」,图隐,A+ 表与其余指标不受影响。
- 窗口内 OKX 无数据 → `bars: []` → 图显空态占位「该窗口无行情数据」。
- 空 `trades` → 只渲 K 线、无 markers。
- 同一 K 线多笔成交 → markers 在该 bar 堆叠;hover 浮层列该 bar 时刻全部成交(map value 为数组)。
- `fill.side` ∉ {`long`, `short`} → marker 用 `MUTED_HEX` 中性色,**防御性处理,与 `TradesTable` 方向列 `long?pos:short?neg:""` 同口径**。实证:喂图的 `order_filled` 切片(queries.py:374-378 `WHERE action='order_filled'`)side **恒为 long/short**(long×130 / short×235);全表的 `buy×15`/`sell×6` 都在 `cancel_order` 动作里、被过滤不入图/表。故 `MUTED` 是对未来数据的稳健兜底、当前为死分支(勿误判现有数据会出中性 marker)。
- 行情缺口(某根 candle 缺失):`snapToBarTime` 吸附到最近的较早 bar(非不存在的 floor 时间),hover 仍命中。
- timeframe:DB 存量含大写 `1H` → `normalize_timeframe`(复用,非 `.lower()`)归一后入 6 框白名单;显式 tf 归一后非法 → 端点 `400`;默认路径会话 tf 落 6 框外 → clamp 兜底(见 §C);前端切换器只给 `TIMEFRAMES` 合法值。
- 活跃会话窗口增长 → `fetched_end_ms` 覆盖判定 miss → 全量重拉(MVP 接受;不做 tail-merge)。
- 内存库 / 无文件路径 DB → `cache_dir_for` 返回 `None` → 每次实拉(不缓存,功能仍可用)。
- **并发首开竞态:** 同一未缓存 `(sid, tf)` 被两请求同时打 → 双拉 + 落盘覆盖(幂等,后果仅浪费一次拉取)。MVP 接受;成痛点再加 per-key in-flight 去重(已列入「不做」)。
- 细粒度长会话首拉慢(如 5 天 1m ≈ 数十秒):`loading` 占位 + 落盘后秒开;切换器默认停在会话自身 tf(最省),下钻是用户主动选择。

## 测试策略(TDD)

逐项 red-green:

1. **`ohlcv_history`(后端核心,mock ccxt client):** `resolve_session_window` 返回 `(symbol, start_ms, end_ms)`、未知 sid / 零时长 → `ValueError`、**不 dispose 传入 engine**;`fetch_ohlcv_window` 分页推进 + 排序 + 同 ts 去重 + **半开过滤(末页含 > end_ms 的根被剔)** + 空页终止 + 末根不前进终止,**返回裸行 `list[list]`**,且 **`try/finally` 内 client.close()(异常路径也 close)**;`_fetch_with_retry` 3 次尝试后 re-raise 瞬态错。**drift-guard:** `TF_MS` / `TIMEFRAMES` 数值锁定(沿用 F7 AC-F7-4)。
2. **F7 零破坏回归:** `from scripts.fetch_session_ohlcv import _resolve_session, _paginate_ohlcv` 经 re-export 仍可 import;`_to_dataframe([[ts,o,h,l,c,v],…])`(裸行)+ `test_to_dataframe_schema` 等**原样绿**(不改);**`test_fetch_resource_cleanup_success_AC_F7_13`(close×1 + dispose×1)+ `_on_exception_AC_F7_14`(异常仍 close+dispose)纳入回归、原样绿**;`fetch_session_ohlcv` CSV 导出端到端不变。
3. **`ohlcv_cache`:** `cache_dir_for`:`file:/abs/x.db` → `<abs 父>/ohlcv_cache`(剥 `file:`)、`/tmp/x.db` → tmp 父目录、`:memory:`/None → `None`;`read` 覆盖判定(`current_end_ms <= fetched_end_ms` 命中 / `>` miss / 文件不存在 / cache_dir None → None);`write` 落盘后 `read` 命中、cache_dir None → no-op。
4. **`get_ohlcv`(query,mock `fetch_ohlcv_window` + 真实 in-memory/tmp engine):** miss → 调 fetch + 落盘 + 返回;hit → 不调 fetch;裸行 → `OhlcvBar.at` aware UTC + 升序;空 fetch → `bars == []`;**tf 归一(复用 `normalize_timeframe`):** 默认路径会话 `timeframe='1H'` → `1h` 不抛、`OhlcvSeries.timeframe=='1h'`;默认路径会话 `timeframe='30m'`(15 框内但 6 框外)→ **clamp 兜底 `1h`**(不报错,保默认渲图);**显式**传入 `1M`(月,归一有效但 6 框外)→ **`400`**(不误折成分钟);显式非法 `ZZ` → `normalize_timeframe` 抛 `ValueError` → `400`。
5. **端点(`app.py`,`dependency_overrides[get_engine]` 注入测试 engine,mock fetch):** `200` + `OhlcvSeries`(默认 tf = 会话归一 timeframe);显式 `?timeframe=5m` 透传;`?timeframe=ZZ` 归一后非法 → `400`;未知 sid → `404`;fetch 抛瞬态错 → `503` + `detail` 仅类名;**断言不在真 `data/` 下落缓存**(内存 engine → cache_dir None)。
6. **`markers.ts` — `toCandleData`(纯函数):** ISO→秒级、升序、同秒去重保留最后、映 OHLC 四值。
7. **`markers.ts` — `snapToBarTime`(纯函数,审查 #2 核心):** 成交落在 bar 内 → 吸附该 bar 开盘时间;早于首根 → 钳首根;`barTimes` 空 → 返回原值;有缺口时吸附到最近较早 bar。
8. **`markers.ts` — `toMarkers`(纯函数,核心):**
   - 单开单平(long)→ 2 markers:开 `belowBar/arrowUp/POS/「开」`、平 `aboveBar/arrowDown/POS/「平」`;按 time 升序;**marker.time === `snapToBarTime(epochSec(fill.at), barTimes)`**。
   - 加仓行 `isAdd` → text `「加」`、position belowBar。
   - short → `NEG` 色;`side` null → `MUTED` 色。
   - 空 fills → `[]`;平仓细分(止损/止盈/强平)不改 marker text(仍「平」,细分留 hover)。
   - **同口径:** 输入由 `deriveTradeFills` 产出,markers 数 == 表行数(同一样本)。
9. **`PriceChart.vue`(mock `lightweight-charts` 补 `addCandlestickSeries`/`setMarkers`/`subscribeCrosshairMove`、mock `api.getOhlcv`):** mount 不抛;init 用 `defaultTimeframe`;切 tf → 重新调 `getOhlcv`;`getOhlcv` 抛 `ApiError` → 显 error 占位、不崩;空 bars → 空态占位;**hover map 键经 `snapToBarTime` 与 marker.time 同源**(同一 fill → 同一键)。
10. **`PerformanceBar.vue`:** `store.detail` 有值 → 渲价格走势 section + 传 symbol/tf/trades 给 `PriceChart`(stub);`store.detail` null → 不渲该 section。
11. **Playwright(真实数据):** 选有交易的会话 → 价格走势 section 出 K 线 + 开/加/平 markers;**hover 一个 marker → 浮层显 类型/方向/价格/数量/PnL/触发原因(验证 #2:粗 tf 下 hover 能命中)**;切 timeframe → 图重渲(首切 loading、再切秒开);拉取失败态 → error 占位;console 0 error。

## 验收标准

- 新增端点 `GET /api/sessions/{sid}/ohlcv` 读真实 OKX 行情、走文件缓存(从**正在使用的 engine** 派生,剥 `file:` 前缀;内存/无路径 → 不缓存)、不写库、`MetricsService` 与 DB schema 未动、无迁移;**测试期不污染真 `data/`**。
- F7 脚本改为复用 `ohlcv_history` 共享核心,`fetch_ohlcv_window` 返裸行 `list[list]`(`try/finally` close)+ 按旧私名 re-export + 模块级常量/`_ensure_utc` 一并迁入 → **F7 既有测试 import 不动、原样全绿(含 AC-F7-13/14 资源清理:close×1 + dispose×1、异常路径仍清理)**,`_to_dataframe` 与 CSV 导出行为不变;ccxt 客户端构造零行为变化(不显式传 timeout)。
- 共享核心含 **sort + 同 ts 去重 + 半开过滤 `[start_ms, end_ms)`**(末页越界根被剔);`resolve_session_window` 借用 engine 不 dispose。
- 价格 K 线 markers 与 A+ 历程表**同一个 `deriveTradeFills` 口径**,标记数 / 类型 / 方向与表逐字一致;**marker.time 与 hover map 键经 `snapToBarTime` 同源**,粗粒度(1h/4h/1d)下 hover 能命中。
- tf 归一:**复用 `src/utils/timeframe.py::normalize_timeframe`(不自造 `.lower()`,保 m/M 区分)** + 自有 6 框白名单;DB 存量 `1H` 会话默认请求不 400 / 不 KeyError(归一 `1h`);默认路径 6 框外会话 clamp 兜底;显式 6 框外 → 400。
- 端点失败语义:未知 sid `404` / 归一后非法 tf `400` / 拉取失败 `503`(detail 仅类名)/ 窗口无数据 `200` + `[]`;单调用 10s 超时。
- 缓存:历史会话恒命中(不重拉)、活跃会话窗口增长触发重拉、内存/无路径降级实拉。
- 前端:`toCandleData` / `snapToBarTime` / `toMarkers` 纯函数测试 + `PriceChart` mount/切换/错误/空态/hover-键同源测试 + `PerformanceBar` section 渲染测试全绿;vue-tsc 0 错;后端测试套件全绿(无回归)。
- 默认 timeframe = 会话自身 tf(归一);切换器可下钻 `1m–1d`;布局为净值曲线上方整宽 section,不挤占现有指标区。
- Playwright 真实数据:K 线 + 买卖点 markers + hover 详情 + timeframe 切换三态通过,console 无报错。
