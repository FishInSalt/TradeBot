# sim 执行保真 iter-2：mark price 真实化（清算 + uPnL 对齐真实 OKX）

> 范围：`SimulatedExchange` 引入真实 mark price 数据源（`watch_mark_price` WS 并行 loop + `fetch_mark_price` seed）；3 处内核消费点改用 mark —— `get_mark_price` 返真实 mark、`_calc_unrealized_pnl` 用 mark、清算**触发判定**用 mark；强平**成交价**仍用盘口 bid/ask；stop/TP 触发维持现状（sim 现用 bid/ask，与 OKX `triggerPxType` 默认 last 存在 gap，本 iter 不动，见 §6）；**清算价 MMR 分档留 iter-3，fee_rate / min-order-size 维持现状**。

## 1. 背景与问题

`SimulatedExchange.get_mark_price`（`simulated.py:148`，`def@136`）硬编码 `return self._latest_ticker.last`，docstring 明写 "Sim has a single price source — mark = last"。这是 PR #66（sim 执行保真 iter-1）显式 defer 出来的 F3 议题（per `feedback_sim_real_data_except_order_mgmt`）。

真实 OKX 永续合约用 **mark price**（独立标记价，基于 index price + 基差 EMA）触发清算、计算 unrealized PnL，目的是防止针对性插针把仓位扫出局。sim 当前的价格消费面全部基于 `ticker`（last/bid/ask），与真实 OKX 存在两处确定的语义偏差：

1. **清算触发**（`_process_tick:660-679`）：用 `ticker.bid<=liq`（long）/ `ticker.ask>=liq`（short）判定。真实 OKX 用 mark 判定。正常行情 mark≈last，但插针 / 流动性枯竭时 bid/ask 比 mark 极端 → **sim 系统性高估爆仓频率**。
2. **uPnL**（`_calc_unrealized_pnl:114-120`）：long 用 `ticker.bid`、short 用 `ticker.ask`。真实 OKX 用 mark（多空同一基准）→ sim uPnL 带 **bid-ask spread 的方向性噪声**，且偏离 mark-based 真值。

`get_mark_price` 当前只被感知层消费（`tools_perception.py` `_safe_mark_price:265` → `get_position` 6-tuple gather:318），内核撮合/清算/uPnL **未真正消费 mark**。本 iter 把 mark 从"感知层占位值"升级为"真实数据源 + 内核计价基准"。

### 解除 ROI 张力（为何此前判"保持现状"现在可做）

memory `project_sim_market_data_fidelity`（2026-05-29）曾判 `get_mark_price=last` **保持现状**，两条理由及其当前判定：

- **"抓 demo mark 反而降保真"**：已证伪。该担忧源于 `project_okx_demo_mark_vs_last_drift`——OKX **demo simulated-trading 账户** ticker.last 与 mark 异常漂 1.67%。但 sim 用的是 `ccxtpro.okx()` **mainnet 公开行情**（`start():1152`，无 `set_sandbox_mode`），mark 与 last 都是真实 mainnet 数据，drift 是真实的 <0.05%，不受 demo 异常基差影响。
- **"实盘 mark vs last <0.05%，边际收益小"**：成立但不构成 wontfix。价值不在正常行情的数值精度，而在 **语义对齐**——消除"sim 用 bid/ask 触发清算 → 比真实 OKX 更易爆仓"的系统性偏差，使 sim 作上实盘前业绩参考时清算判定可信。gap 的**存在性**是 OKX 文档确定的（非直觉猜测），数据源技术无障碍（见 §2.2）。

## 2. 根源与对齐参照

### 2.1 当前 sim 价格消费面（逐处已核验）

| 消费点 | 位置 | 当前价格源 | 真实 OKX 基准 | 本 iter |
|---|---|---|---|---|
| `get_mark_price` | `:136` | `ticker.last` | mark | **改 mark** |
| `_calc_unrealized_pnl` | `:114-120` | `bid`(long)/`ask`(short) | mark | **改 mark** |
| 清算触发判定 | `_process_tick:660-679` | `ticker.bid/ask` vs liq | mark vs liq | **改 mark** |
| 强平成交价 (fill price) | `_force_liquidate(.., ticker.bid/ask)` | `bid/ask` | 市价吃盘口 | **保持盘口** |
| stop/TP 触发 | `_should_trigger:524-533` | `ticker.bid/ask` | last (`triggerPxType` 默认) | **保持现状（gap，见 §6）** |
| 市价单成交价 | `_execute_market_fill:652` | `ticker` 盘口 | 市价吃盘口 | **保持** |
| 价格 alert | `:717/720` | `ticker.last` | — | **保持** |
| `_calc_liquidation_price` 公式 | `:122-126` | MMR=0 近似 | tiered MMR | **保持（iter-3）** |

### 2.2 黄金参照与数据源能力

- **okx.py 实盘参照**：`get_mark_price`（`:511`）走 `public_get_public_mark_price` REST 取 `markPx`；docstring 已注明"OKX uses mark price for perpetual liquidation calculation"。
- **ccxtpro.okx 能力**（实测 `.has`）：`watchMarkPrice: True` / `watchMarkPrices: True`（WS）、`fetchMarkPrice: True`（REST）、`fetchMarketLeverageTiers: True`（iter-3 MMR 用）。数据源无技术障碍。
- **WS 对称性**：现有 `_matching_loop`（`:1182`）跑 `watch_ticker`；mark 用并行 `watch_mark_price` loop 是同模式自然扩展。

### 2.3 感知层现状（已支持真实 drift，逻辑不改）

`tools_perception.py:393-413` 已有完整 mark-vs-last drift 展示：`drift_pct=(last-mark)/mark`，drift round 到 `±0.00%` 时抑制后缀（squash `6614fdf`），否则显示 `Mark: X (Last: Y, drift Z%)`；清算距离 `liq_dist_pct` 已用 mark 算（`:413`）。注释明写 "under sim, mark==last by construction"。

**iter-2 副作用**：mark 真实化后 mark≠last（真实 <0.05% drift），后缀从"恒抑制"变"偶发显示"。渲染**逻辑**无需改，但两处**事实性注释/docstring 在 "mark==last" 不再成立后变误导，纳入本 iter scope 更新**：① `tools_perception.py:397-402` 的 "under sim, mark==last by construction … the suffix is dead 100% of the time there"（改为"偶发显示"语义）；② `simulated.py:137` `get_mark_price` docstring "Sim has a single price source — mark = last"（§3.2 改函数体必然重写）。两处均为代码内部注释 / impl docstring（非 LLM `@tool` wrapper 通道，区别于 `project_tool_docstring_llm_channel`），但仍属注释保真。另需审相关测试是否含"sim 产出 mark==last"的集成断言。

## 3. 设计

### 3.0 语义锚点

> **触发判定基准** = mark（清算，本 iter 改）；stop/TP 维持现状（sim 现用 bid/ask，本 iter 不动，gap 见 §6）；**成交价（fill price）** = 始终盘口（市价吃单的真实损失）。本 iter 只改清算的**触发基准** bid/ask→mark，不改 stop/TP 触发、不改任何**成交价**语义。

### 3.1 mark 数据源 + 生命周期

- 新增实例状态 `self._latest_mark_price: float | None = None`（`__init__`）。
- **seed**（`start()`，与 seed_ticker 并列；**须在 `_init_contract_size():1153`（含 `_load_markets_with_retry:1268`）之后**——`fetch_mark_price` 内部依赖 markets 已加载取 instId，放在 seed_ticker 旁 `:1155+` 即满足）：`fetch_mark_price(symbol)` 取首值，3 次指数退避重试（复用 seed_ticker 既有模式 `:1155-1165`）；**失败 fail-fast**（清算现依赖 mark，属 critical，对齐 seed_ticker 取舍）。
- **运行期**：新增并行 task `self._mark_task = asyncio.create_task(self._mark_loop())`（与 `_matching_task:1179` 并列）；`_mark_loop` 跑 `watch_mark_price` 持续更新 `_latest_mark_price`。error 处理**模式**对齐 `_matching_loop`（出错保持上一个 mark，stale 可用，退避），但**必须用独立计数器 `self._mark_error_count`**——`_matching_loop` 的 `self._error_count`（`:1196-1201`）是实例级、ticker 成功即 `else` 分支清零；两个独立 WS 流共享同一计数器会交叉污染（ticker 每成功一次抹掉 mark 累积的错误 → mark 连续断连也触发不了退避，反之亦然）。"对齐"是模式对齐、不是状态共享。
- **close()**（`:1203`）：cancel `_mark_task`（与 `_matching_task` 并列）。
- **不持久化**：mark 是 transient 实时态，不入 `sessions` / `SimBalance`，**不碰 DB schema / alembic**。restore 后由 seed + WS 重新拿。

### 3.2 内核 3 处改 mark

```python
# get_mark_price (:136) — 返真实 mark
return self._latest_mark_price          # 替代 self._latest_ticker.last

# _calc_unrealized_pnl (:114) — guard 判据 _latest_ticker→_latest_mark_price；多空同一 mark 基准
mark = self._latest_mark_price
if mark is None:                  # 替代现有 `if self._latest_ticker is None: return 0.0` (:115)
    return 0.0
if pos.side == "long":
    return (mark - pos.entry_price) * self._base_qty(pos.contracts)
else:
    return (pos.entry_price - mark) * self._base_qty(pos.contracts)

# 清算触发 (_process_tick:660) — 触发用 mark，成交价仍盘口
mark = self._latest_mark_price
if pos.side == "long" and mark <= liq:
    fill = self._force_liquidate(pos, symbol, ticker.bid)   # 触发=mark, 成交价=盘口
elif pos.side == "short" and mark >= liq:
    fill = self._force_liquidate(pos, symbol, ticker.ask)
```

清算检查**仍在 `_process_tick`**（ticker-tick 驱动，复用单 `self._lock`），读最新 `self._latest_mark_price`（由并行 mark loop 更新）。**生产 `_process_tick` 签名不变**（唯一生产调用点 `_matching_loop:1192`，mark 经实例态进入）；但 `_process_tick:636-639` 只更新 `_latest_ticker` 不碰 mark，故**测试推进价格须经 helper 同步 mark**（见 §5，D5）。

### 3.3 mark 不可得的语义

- seed 失败 → fail-fast（§3.1）。
- 运行期 `_latest_mark_price` 在 seed 后恒有值；WS 出错保持 stale 上一值。
- 因 seed fail-fast，内核**清算触发点**不需 `None` 兜底。但 `_calc_unrealized_pnl` 的现有 guard（`:115` `if self._latest_ticker is None: return 0.0`）**判据改为 `_latest_mark_price is None`**——测试直接构造（不走 start）或任何 start 前调用路径下，保证 `fetch_balance`/`fetch_positions` 退化为"返回不含 uPnL 的余额"而非 `TypeError` 崩溃，与 `get_mark_price` 未 start 的 `RuntimeError` 防御对称。

## 4. 决策记录

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 范围深度 | 完整真实化（数据源+感知+清算+uPnL） | gap 是文档确定的语义偏差；数据源技术无障碍；spec 预定义 scope；分层只做感知层会留"mark 取到却不被内核用"半成品 |
| D2 | 强平成交价 | 触发 mark + 成交盘口 bid/ask | 对齐真实 OKX「mark 触发、市价吃盘口」；强平损失最贴实盘；改动最小（仅触发基准变） |
| D3 | mark 不可得 | seed fail-fast | 清算现依赖 mark→critical；对齐 seed_ticker 既有 fail-fast；避免隐式降保真（fallback last 会掩盖 WS 挂掉） |
| D4 | 清算检查时机 | ticker-tick 驱动，读最新 mark | 复用单锁；ticker WS 亚秒频繁，mark 穿线延迟可忽略；mark-WS 也触发会致双 loop 改仓位、锁竞争 |
| D5 | mark 进 `_process_tick` | 保持实例态 + 测试 helper（选项 B） | 生产 `_process_tick` 签名不变，读实例 `_latest_mark_price`（WS loop 更新），生产唯一调用点 `_matching_loop:1192`；测试新增 `_advance(ex, ticker, mark)` helper 封装"设 mark + 推进"，统一覆盖"推进价格→查 uPnL/清算"路径，免手动逐处设 mark 漏改。**原"79 调用点零适配"理由已证伪**——`_process_tick:636-639` 只更新 ticker 不碰 mark，生产仅 1 处调用（`_matching_loop:1192`），其余全是测试；三文件（`test_simulated_exchange` / `test_simulated_cs_kernel` / `test_alert_lifecycle`）`_process_tick` 共 ~90 次调用中，仅"推价格→查 uPnL/清算"子集需同步 mark（多数 fill-open 调用不推价、不受影响） |
| D6 | 持久化 | 不入 DB | mark 是 transient，区别于 iter-1 contract_size（session 不变量） |

## 5. 测试策略

- **测试架构 gap + helper（核心，D5 选项 B）**：测试普遍以 `_process_tick(ticker)` 作价格推进入口，但 mark 不走 `_process_tick`（`:636-639` 只更新 `_latest_ticker`）→ mark 真实化后凡"推进价格→查 uPnL / 触发清算"的测试 mark 会 stuck 在默认 seed。涉及**两套构造体系 + conftest 第三自建路径**：① `test_simulated_exchange.py` 的 `_make_exchange`（`:7`）+ `_tick`（`test_simulated_cs_kernel.py` 复用）；② `_fixtures.py` 的 `make_sim_exchange`（`:90`，**仅** `test_alert_lifecycle.py` import）+ `make_ticker`；③ `conftest.py:_make`（`:128`，自建 `TradingDeps` fixture via 共享 `inject_mock_ccxt`）。（`test_derivatives_data.py` 用本地 `_make_sim_exchange`、不碰 mark 消费点，不受影响。）**统一方案：mark 测试基础设施全部沉到 `_fixtures.py` 共享层**——新增 `async def _advance(ex, ticker, mark=None)`（`mark` 给值则先 `ex._latest_mark_price = mark` 再 `await ex._process_tick(ticker)`；接受任意 `Ticker`，两套 tick helper 通用），两体系 import 复用；所有"推进价格查 uPnL/清算"测试改走 `_advance` 同步 mark，不关心 mark 的测试可不传。
- **构造路径默认 + AsyncMock**：**三个**构造路径（`_make_exchange:7` + `make_sim_exchange` `_fixtures.py:90` + `conftest.py:_make:128`）都加默认 `_latest_mark_price`（= last seed），否则 `__init__` 默认 `None` 会让清算 `mark<=liq` 对 `None` 比较抛 `TypeError`、`get_mark_price` 抛 `RuntimeError`。`inject_mock_ccxt`（`_fixtures.py:11`，共享于三处）补 **`AsyncMock`** 的 `watch_mark_price` / `fetch_mark_price`——非"避免 AttributeError"（`MagicMock` 对任意属性自动返子 Mock 不会 AttributeError），而是 `watch_mark_price` 是 **async**，`await` 普通 Mock 会炸，需 `AsyncMock` 返回可控 `markPrice` 结构。
- **受影响测试（实质迁移，非 no-op）**：① `test_simulated_exchange.py` uPnL 测试 bid-based → mark-based（如 `test_fetch_balance_with_unrealized_pnl` 现基于 bid 94990）。② `test_simulated_cs_kernel.py:test_unrealized_pnl_scales_with_cs:13-30` 经 `_process_tick` 推 ticker 到 101000 制造 uPnL，改 mark 后 mark stuck 95000 → uPnL=(95000−100000)×0.1=−500≠+100 **会失败**，须经 `_advance(.., mark=101000)` 同步推进。③ 清算测试（`_make_exchange` 体系）：`test_simulated_exchange.py` 多个 `_process_tick` 推 bid/ask 触发清算（`test_liquidation_short` / `test_liquidation_triggers_before_stop` 等）+ cs_kernel `test_liquidation_via_process_tick_cs_not_one:136`，逐个改走 `_advance` 设 mark 触发 + 配对不触发对照。④ **`test_alert_lifecycle.py`（`make_sim_exchange` 体系）**：多个砸盘触发清算的 e2e（如 `:437+` 开仓@50000 → `_process_tick(make_ticker(last=40000))` 触发清算 → 断言 `not in sim._positions`），mark stuck 后砸盘不触发→断言失败，须改走 `_advance` 同步 mark。（`test_derivatives_data.py` 用本地 `_make_sim_exchange`、只测 funding/LSR 不碰 mark 消费点 → 已核不受影响、不改。）规模合计 ~15-25 测试，非一句带过。
- **新增真断言**：① mark 穿线触发清算 vs 同时 last/bid 未穿线不触发（对照，证明触发基准已切 mark）；② 触发=mark、成交价=盘口的区分（fill_price==bid 而非 mark）；③ uPnL mark-based 数值（多空对称、无 spread 噪声）；④ seed fail-fast（mock fetch_mark_price 抛错 → start() raise）；⑤ 运行期 WS 出错保持 stale mark；⑥ `get_mark_price` 返真实 mark（注入 mark≠last，断言返 mark）。
- **语境审查**：`test_iter_tool_opt_mark_vs_last.py` / `test_iter_tool_opt_getpos_mark_suppress.py` 多为渲染层 mock（注入任意 mark/last），应不受影响；逐个确认无依赖"sim 内部 mark==last"的集成断言。（注：`test_alert_lifecycle.py` 走 `make_sim_exchange` e2e 清算/uPnL，属**受影响**清单 ④，非此渲染 mock 类；`test_derivatives_data.py` 用本地 `_make_sim_exchange` 不碰 mark 消费点、不受影响。）
- **mock fidelity**（per `project_iter2_mock_fidelity_lesson`）：至少一条真实 ccxt fixture 验证 `watch_mark_price`/`fetch_mark_price` 返回结构解析（markPrice key 提取）。
- **全量 pytest**：基线 2036 passed + 5 skip（来源 PR #66 merge run / memory anchor，非 commit message）；资金安全路径回归面大，全量是 go/no-go gate。

## 6. 非 scope（明确排除）

- **清算价 MMR 分档**（iter-3）：`_calc_liquidation_price` 现 MMR=0 近似；真实 OKX 分档维持保证金率，需 `fetchLeverageTiers` 分档表 + 逐仓 MMR 公式建模。
- **mark OHLCV 历史**（`fetchMarkOHLCV`）：sim 实时驱动，无回放需求。
- **fee_rate config 校验** / **min-order-size 校验**：维持现状（per `feedback_sim_real_data_except_order_mgmt` F4 / load_markets 元数据层）。
- **stop/TP triggerPxType gap（本 iter 核查 mark 时新发现的现存 gap）**：`_should_trigger:524-533` 现用 `ticker.bid/ask` 触发 stop/TP，OKX `triggerPxType` 默认 last → sim 比 OKX 更早/更敏感触发（long stop `bid<=trigger` 早于 `last<=trigger`，因 bid<last）。本 iter **不动**，记入 Tier 3 backlog（`.working/all-pending-needs.md` + memory `feedback_sim_real_data_except_order_mgmt`）；不引入 mark/index 可选触发源（实盘准备期议题）。

## 7. 风险与回滚

- **资金安全路径**（清算触发 + 余额 uPnL）：回归面比 iter-1 大。缓解：清算/uPnL 数值 parity 专项测试 + 全量 pytest gate；触发/成交价解耦使改动局部化（只动触发基准一行级别）。
- **WS 第二路（mark loop）启动失败 / 断连**：seed fail-fast 保证启动期 mark 可得；运行期 stale 保持避免 mark 缺失致清算静默退化；error 退避对齐既有 `_matching_loop`（独立 `_mark_error_count`）。**stale mark 清算边界（诚实披露）**：mark WS 断连期间用 stale mark 判清算，若此时价格剧烈波动，清算判据滞后于真实 mark → 可能漏判该爆的仓 / 误判——属 WS 中断的固有降级，正常双流亚秒同步可忽略。
- **mark vs last 时序错配**：ticker-tick 驱动读最新 mark，亚秒级延迟；不引入跨 loop 锁竞争。
- **并发安全（同构现有 ticker 写）**：`_mark_loop` 锁外写 `_latest_mark_price`，`_process_tick` 在 `self._lock`（`:648`）内读；asyncio 单线程 + 单条引用赋值原子，且与现有 `_latest_ticker = ticker`（`:638`，同样锁外写）**同构**——无新增"两 loop 共享可变态"的并发风险。
- **离散采样近似（诚实披露）**：清算语义已切 mark-driven，但机制仍由 ticker-tick 驱动采样（D4）。当 ticker 流停滞而 mark 流仍在更新时，清算检查会延迟到下一个 ticker tick——与 §7 "mark loop 断连"对称的另一向 gap。正常双流亚秒同步可忽略；属离散撮合的固有近似，非本 iter 引入。
- **触发/成交价解耦的强平 PnL 数值后果（诚实披露）**：`mark<=liq` 触发但按 `ticker.bid` 结算，流动性枯竭使盘口偏离 mark 时，强平 PnL 偏离"恰好亏到 liq"。这更贴实盘（市价吃盘口，D2）而非"破产价精确结算"；正常 <0.05% 可忽略，极端 spread 时结算价可显著偏离破产价。
- **回滚**：纯内存态 + 无 DB schema 变更 → revert 即恢复 mark=last，无迁移负担。
