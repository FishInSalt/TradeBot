# sim 执行保真 iter-1：contract_size + precision 真实化（向真实 OKX 对齐）

> 执行层：`get_contract_size` 1.0 → 真实 `contractSize`；`amount_to_precision` config decimals → ccxt 原生
> 语义：sim 的 `amount`/`contracts` 从「base 数量」对齐到真实 OKX 的「张数」；钱的量纲（notional / margin / PnL / %-equity）在 **exchange 内核内**数值不变，但**所有从存储字段独立重算钱的下游消费者必须同步 ×cs / ÷cs**（否则错 cs 倍）
> 范围：`SimulatedExchange` 执行内核 + 两条下单路径（open_position + place_limit_order）+ 下游分析/持久化层（metrics / cycle_capture / _sim_metrics 含 _derive_close_amount）+ `config.precision` 退役 + `sessions` cs 持久化列；**mark price 留 iter-2，清算价 MMR 分档留 iter-3，fee_rate / min-order-size 维持现状**

## 1. 背景与问题

`SimulatedExchange.get_contract_size` 硬编码 `return 1.0`（`simulated.py:1262`），是 order-book 重设计（PR #64, `c821d31`）与 order-flow（PR #65, `4cc83e1`）两个 iter 反复 defer 出来的「sim 执行保真」议题。两个 market-data iter 已在**数据层**用真实 `_ccxt.market()["contractSize"]` 把盘口/成交的张数归一化为 base 币（解耦执行层），但**执行层本身仍是 cs=1.0**。

**cs=1.0 的本质**：sim 把下单 `amount`（合约张数）直接当作 base 币数量处理。链路自洽——`open_position`（`tools_execution.py:77`）用 `raw_quantity = usdt×lev/price` 算出 base 数量，sim 撮合内核以 `contracts × price`（隐含 cs=1）计 notional/margin/fee/PnL，结果在 sim 内部数值正确。但它与真实 OKX 的合约语义分叉：

- **真实 OKX**：`create_order(amount=N)` 中 `N` 是合约张数，1 张 = `contractSize` base 币（BTC swap = 0.01，ETH = 0.1，SOL = 1.0）。`get_contract_size` 返回真实 `market["contractSize"]`（`okx.py:941`）。
- **sim**：同一个 `amount=N` 被当作 `N` 个 base 币，cs=1.0。

**两个后果**：

1. **跨层量纲基准不一致**：market-data 层（order_book / recent_trades / taker_flow）用真实 cs 把张数归一化为真实 base 币，执行层（get_position / open_position）用 cs=1.0 把 `amount` 当 base 币。两层 base/notional 来自不同 cs 基准，agent 跨层比较（仓位规模 vs 盘口深度 / taker flow）会差真实 cs 倍（BTC ~100×）。
2. **fidelity gap（实盘 blocker）**：`open_position`/`place_limit_order` 把 base 数量当 `amount` 传给 ccxt，真实 OKX 会当张数 → 实际开仓小 `cs` 倍。

**用户决议（2026-05-31）**：向真实 OKX 对齐，接受「contracts 存储尺度变（base→张数）+ 历史 sim 数据不可比」的代价（sim-only 观察期，新 run 重新开始）。

**关键认知（两轮外部审查 2026-05-31 暴露）**：cs 真实化的本质是改变 `_Position.contracts` / `FillEvent.amount` 的**存储语义**（base→张数）。exchange 内核内部因同步 ×cs 而数值不变，但**任何从这些存储字段独立重算钱的下游**（第二下单路径 + 两套独立 FIFO 分析 + 持久化快照）若不同步，则错 cs 倍。尤其反推公式（从已含 cs 的 money 反算张数）隐含 cs=1、方向相反、更隐蔽。在 CLAUDE.md「当前唯一焦点 = sim 数据收集/性能分析」语境下，这些下游恰是性能分析核心产出——错 cs 倍 ≠「历史不可比」，是**新 run 的分析也错**。故下游消费者纳入本 iter scope（§2.5 / §3.5）。

## 2. 根源与对齐参照

### 2.1 cs=1.0 的消费面

**(a) 执行内核（裸 `amount`，隐含 cs=1，需补 ×cs）**——见 §3.2 的 7 处清单。

**(b) 工具层已正确乘 `× contract_size`**（cs 真实化后自动正确，无需改）：
- `open_position` notional/fee（`tools_execution.py:103-105`）
- `close_position` notional/fee/net-PnL（`tools_execution.py:128-136`）
- `place_limit_order` notional/fee（`tools_execution.py:654-656`）
- `get_position` Risk Exposure + Fee & Breakeven（`tools_perception.py:349/382`）
- `cli/app.py:500-501` entry_fee（`:499` 取 cs）

**(c) 下游存储重算（从 contracts/amount 当 base 重算钱，需同步 ×cs / ÷cs）**——见 §2.5。

### 2.2 黄金参照：okx.py

向真实 OKX 对齐 = 在这两点上复制 `okx.py` 的语义：

| 点 | okx.py 实证 |
|---|---|
| contract_size | `get_contract_size:941` → `market["contractSize"]`；`start():210` 显式 `_preload_markets()`（`load_markets`，带 `@_retry`）fail-fast |
| precision | `amount_to_precision:780` → `float(self._client.amount_to_precision(symbol, amount))`（ccxt 原生） |

### 2.3 precision 现状

`amount_to_precision`（`simulated.py:198`）用 `self._precision[symbol]`（来自 `ExchangeConfig.precision` 子段，`config.py:18`；由 `cli/app.py:777` 定义、`:844` 填充的 `_DEFAULT_PRECISION`）。grep 确认 **`config.precision` 仅 sim 消费**（okx.py 用 ccxt 原生）。改用 ccxt 后该字段彻底 dead。

### 2.4 start() 的 load_markets 缺口

sim 的 `start()`（`simulated.py:1124`）**没有显式 `load_markets()`**，靠 `fetch_ticker`（`:1151`，自带 3 次重试）内部隐式触发；`fetch_order_book`/`fetch_trades` 的 `market()` 可用性依赖「fetch_ticker 已先跑过」这个脆弱前提。okx.py 有显式 `_preload_markets()`（带 `@_retry`）fail-fast（`:210`）。本 iter 缓存 cs 需 markets 已加载，顺带补齐。

### 2.5 下游消费者审计（cs 存储语义传播面，逐处已核验）

`metrics.py`（runtime，读 `FillEvent`）与 `_sim_metrics.py`（离线，读 DB `sim_orders`）是**两套独立 FIFO 实现**，各需独立同步。

| ID | 位置 | 代码 | cs 后后果 |
|---|---|---|---|
| **B1** | `tools_execution.py:623` | `raw_quantity = (usdt_amount × actual_leverage) / price`（无 ×cs，与 open_position 旧码同构） | limit 开仓 raw_quantity 仍是 base 数量 → 传入 cs 化内核被当张数 → 实际开仓小 cs 倍（sim 内当场触发） |
| **B2** | `metrics.py:195` | 普通平仓 `pnl_gross = (fill.price − lot.entry_px) × consumed × sign`，`consumed` 为张数 | runtime FIFO PnL 错 cs 倍（注：`:193` liquidation 从 `fill.pnl/fill.amount` 反推 per-unit，**自洽**；`:189-190` fee 摊分是无量纲比例，**不乘 cs**） |
| **B3** | `_sim_metrics.py:88/243` | 正向 `_compute_pnl = (exit − entry) × amount`，`amount` 为张数 | 离线 analyze_sim/diff_sim PnL 错 cs 倍（**需 ×cs**，仅普通平仓项） |
| **B3-bis** | `_sim_metrics.py:105` | `_derive_close_amount`：`derived = fill.fee / (filled_price × fee_rate)` 反推平仓张数 | cs 后内核存 `fee = price×amount_张×cs×fee_rate` → `derived = amount_张×cs`；守卫 `derived ≤ amount×1.01` 对 cs<1（BTC/ETH）**恒放行 + derived_ok=True 静默** → FIFO 欠消费/孤儿 lot + liq per-unit（`:227`）连带错（**需 ÷cs**） |
| **B4** | `cycle_capture.py:119` | `notional = entry_price × contracts`（无 cs）→ `pnl_pct_of_notional` | 状态快照 notional 偏 cs 倍 → pnl_pct 错 cs 倍 |

## 3. 设计

### 3.0 语义锚点

`amount` / `contracts`：base 数量 → **张数**；`cs`：1.0 → **真实**。

**钱的量纲数值不变——仅对 exchange 内核成立**（pre-rounding 代数）：
- `open_position`：新 `amount_张 = usdt×lev/(price×cs)`；`notional = amount_张 × price × cs = usdt×lev`（与旧 `amount_base × price` 相同）。
- `margin = notional/lev = usdt`（不变）；`pnl = price_diff × amount_张 × cs = price_diff × usdt×lev/price`（与旧 `price_diff × amount_base` 相同）。

**内核外的下游（§2.5）不享此抵消**——它们从存储的张数独立重算（正向 ×cs / 反推 ÷cs），必须显式补（§3.5）。

**可见变化**：① `contracts` 裸数尺度（BTC ×100：0.1 → 10，与真实 OKX 一致）② 下单量按真实张数精度取整（粒度与旧 config decimals 不同，见 §5 不变量澄清）。

### 3.1 cs 数据源 + 缓存（start）

`start()` 在 `self._ccxt = ccxtpro.okx()` 之后、撮合循环之前：
```python
await self._load_markets_with_retry()                            # 带重试，对齐 okx._preload_markets:189 取舍；放在 seed fetch_ticker 前
self._contract_size = float(self._ccxt.market(self._symbol).get("contractSize") or 1.0)
```
重试包装复用 sim 既有模式（fetch_ticker 的 3 次指数退避，`:1149`），避免与 okx.py 的 `@_retry` 取舍不一致、且不让启动期 transient 网络抖动硬失败。markets 加载彻底失败 → fail-fast（cs 不可得时所有计价错，宁可启动即失败）。

`get_contract_size` 返回 `self._contract_size`（缓存值；撮合内核高频调用，cs 是 per-symbol 常量；可顺手加 `_validate_symbol` 做单 symbol 一致性硬化）。`__init__` 初始化 `self._contract_size: float = 1.0`（test 直接构造、未 start 时的安全默认；start 后覆写）。

### 3.2 撮合内核 ×cs（helper 封装，7 处）

引入 helper：
```python
def _base_qty(self, amount: float) -> float:
    """张数 → base 币当量（amount × contractSize）。计价统一入口。"""
    return amount * self._contract_size
```

下列计价点把裸 `amount` 换成 `self._base_qty(amount)`：

| # | 位置 | 表达式 |
|---|---|---|
| 1 | `_calc_unrealized_pnl:114/116` | `(bid−entry) × amount` / `(entry−ask) × amount` |
| 2 | `create_order` market-close:234 | `estimated_fee` |
| 3 | `create_order` market-open:240/241 | `estimated_margin` + `estimated_fee` |
| 4 | `create_order` limit-open:279/280 | `margin` + `fee` |
| 5 | `_fill_market_open:331/332` | `actual_margin` + `actual_fee` |
| 6 | `_close_position_core:424/425/428/430` | `released_margin` + `fee` + `pnl` |
| 7 | `_execute_limit_fill:575/576` | `actual_margin` + `actual_fee` |

**显式排除（不 ×cs，附理由防机械误改）**：
- `_calc_liquidation_price:118`：纯价格比例 `entry×(1±1/lev)/(1∓fee)`，与 amount 无关。
- 加权均价 `_fill_market_open:352` / `_execute_limit_fill:589`（`new_entry = (entry×contracts + price×amount)/new_contracts`）：cs 在分子分母约分，张数算术天然正确。

### 3.3 下单量张数化（两条下单路径）

两条路径同构修改——把 cs 提前到 `raw_quantity`：
- `open_position`（`tools_execution.py:77`）：`raw_quantity = (usdt_amount × leverage) / (ticker.last × cs)`
- `place_limit_order`（`tools_execution.py:623`）：`raw_quantity = (usdt_amount × actual_leverage) / (price × cs)`

**plan 注意（取数时序）**：当前两路径的 `get_contract_size` 取数在 `create_order` **之后**（`:103` / `:654`），必须上移到 `raw_quantity` 计算前，并把同一 cs 复用给末尾 `notional = price × quantity × contract_size`（`:104` / `:655`，保持不变）。

**不改**：`close_position`（传 `pos.contracts` 整仓张数）/ `set_stop_loss` / `set_take_profit` / 条件单（`_create_conditional_order` 用 `pos.contracts`）。

### 3.4 precision 用 ccxt 原生（含 too-small 守卫）

```python
def amount_to_precision(self, symbol: str, amount: float) -> float:
    try:
        return float(self._ccxt.amount_to_precision(symbol, amount))
    except ccxt.InvalidOrder:
        return 0.0   # 保住 open_position:79 / place_limit_order:625 的优雅 "Position too small" 分支
```

**理由**：ccxt 4.5.47 `base/exchange.py:6607` 的 `amount_to_precision` 在截断结果 == `'0'` 时 **raise `InvalidOrder`**（已查源码）。裸用会让 sub-precision 下单从「优雅提示」退化为「异常打断 cycle」。catch 后返回 0.0 复原现有 `if quantity <= 0: return "Position too small..."` 守卫。`amount` 此时是张数，ccxt 按真实张数精度取整，与 cs 真实化天然自洽（强耦合，故同 iter）。

退役：`config.precision`（`config.py:18`）+ `_DEFAULT_PRECISION`（`cli/app.py:777`）+ `self._precision`（`simulated.py:75/199`）。

### 3.5 下游消费者 ×cs / ÷cs + cs 来源

**B1**（`place_limit_order`）：已并入 §3.3。

**B4（runtime，有 exchange）**：`cycle_capture` 持 `deps.exchange` → 直接 `await deps.exchange.get_contract_size(symbol)` 乘入 notional。

**B2（runtime，无 exchange）**：`MetricsService.__init__`（`metrics.py:214`）只持 `engine/session_id/initial_balance`，**不持 exchange** → 必须走持久化 cs（非「有则 get_contract_size」的条件分支，**前支不存在**）。修正粒度精确到：**仅普通平仓的 `pnl_gross`（`:195`）×cs**；`fee_open_share`/`fee_close_share`（`:189-190`，`consumed/amount` 无量纲比例 × 已存 money）与 liquidation `pnl_gross`（`:193`，per-unit 已 money/张）**不乘 cs**。

**B3 + B3-bis（离线 _sim_metrics，无 exchange）**：两处都依赖 cs，缺一则 BTC/ETH 静默出错——
- B3：`_compute_pnl`（`:88`，调用点 `:243`）正向 PnL **×cs**（仅普通平仓项；liq `:241` 不乘，同 B2 粒度）；
- B3-bis：`_derive_close_amount`（`:105`）反推张数 **÷cs**（`derived = fill.fee/(filled_price × cs × fee_rate)`），且**须先于** fee 摊分修正——`:246` `fee_close_share` 的分母 `actual_amount` 依赖它。

**cs 来源 = `sessions` 新列持久化（B2/B3/B3-bis 硬依赖，非可选）**：
- sim `start()` 缓存 cs 时持久化到 `sessions` 新列（per-session 常量）。`MetricsService.compute`（`metrics.py:228`）本就 `select SessionModel.fee_rate`，加一列直接同址读；离线 `_sim_metrics` 与既有 `_fetch_fee_rate`（`:158`）对称读取——无需联网/实例化 ccxt。
- **历史兼容红利**：历史 session 该列 NULL → 分析层 fallback cs=1.0（旧 base 语义），新 run 真 cs（张数语义）→ analyze_sim/diff_sim 跨新旧 run **各自正确**，部分缓解「历史不可比」。
- 代价：DB schema +1 nullable 列 + migration（见 §7，有 `sessions.fee_rate` 先例）。

## 4. 决策记录

- **D1 — `config.precision` 删除 + 测试迁移**：字段 + `_DEFAULT_PRECISION` + `self._precision` 清除。功能上是 dead config（仅 sim 读），但删除牵动测试契约——需迁移：`test_config.py:91/93/100`（直接断言 `config.precision`，删字段后属性读取 `AttributeError`，pydantic v2 `extra='ignore'` 使含残留键的 JSON 构造不报错但**读取**炸）+ `conftest.py` / `_fixtures.py` / `test_simulated_exchange.py` / `test_exchange_order_book.py` / `test_alert_age.py` / `test_tool_enhancement.py` / `test_derivatives_data.py` / `test_iter_tool_opt_mark_vs_last.py`（9 测试文件）+ 3 scripts（`smoke_sim_microstructure` / `verify_taker_flow_boundary` / `grounding_order_flow_tools`）构造点。plan 列完整迁移清单。
- **D2 — `contracts` 单位标签留给 grounding**：cs 对齐后 sim/OKX 的 `contracts`=张数语义本就一致；下个 sim run grounding 若发现 agent 困惑再补「张」单位标签（工具设计原则 7），避免本 iter scope 蔓延。
- **D3 — helper `_base_qty` 封装**：单点封装防 7 处遗漏 + 单点测试。
- **D4 — cs 来源 = `sessions` 新列持久化（B2/B3/B3-bis 硬依赖，含历史 NULL→1.0 fallback）**：见 §3.5。**非可选**——`MetricsService` 与离线脚本均无 exchange 引用，离线 `_sim_metrics` 更无法联网取 cs。`sessions.fee_rate`（nullable per-session）是现成 migration 先例，列与读取路径（`compute` select / `_fetch_fee_rate`）已就位，加 `contract_size` 列对称扩展。plan 定列名 + migration。

## 5. 测试策略

- **核心不变量护栏（A3 澄清）**：「notional/margin/PnL/%-equity 数值不变 + contracts=旧值/cs」用**直接构造持仓**（绕过 sizing + amount_to_precision）验 **kernel 计价不变**——因为 ccxt TRUNCATE(tick) 与旧 `math.floor(decimals)` 粒度不同，端到端走 sizing 的路径无法精确相等。端到端测试另接受 tolerance 或比 pre-rounding 值。
- **cs≠1 为主断言（修正 vacuous）**：`_base_qty` 无条件乘，cs=1.0 会吸收漏乘错误——主断言用 **cs≠1**（BTC cs=0.01 / ETH cs=0.1，张数≠base，如 BTC 10 张 = 0.1 base）覆盖 7 处 ×cs；SOL cs=1.0 仅作「防 cs 默认值写死」守卫。
- **真实 cs fixture**：mock `self._ccxt.market()` 返回真实 `contractSize`（per `project_iter2_mock_fidelity_lesson`）。
- **跨层 cs 同源（A4）**：断言 `get_contract_size(sym) == _ccxt.market(sym)["contractSize"]`，锁死执行层缓存 cs 与 market-data 层 live cs 不漂移。
- **amount_to_precision（A1/A2）**：① sub-precision 输入断言返回 0.0（catch InvalidOrder）+ 优雅 too-small 分支可达；② 测试助手 `_make_exchange`（`test_simulated_exchange.py:7`）+ `conftest.py` / `_fixtures.py` 注入 mock `_ccxt`（`amount_to_precision` + `market`），使离线构造测试不 `AttributeError` 且新逻辑可离线单测。
- **下游 ×cs / ÷cs 回归（B1-B4 + B3-bis）**：limit 开仓 raw_quantity 张数化 / `metrics` FIFO `pnl_gross`（仅普通平仓项）/ `cycle_capture` pnl_pct / `_sim_metrics._compute_pnl` 各加 cs≠1 断言。**B3-bis 专项**：构造 cs<1（BTC/ETH）的 close fill，断言 `_derive_close_amount` 返回正确张数（÷cs）而非缩小值、且 `stale_close_amount_count` 守卫不再被恒放行掩盖。
- **start() load_markets**：断言显式预加载（带重试）+ cs 缓存；加载失败 fail-fast。

## 6. 非 scope（明确排除）

- **mark price**（iter-2）：清算触发价源 `ticker.bid/ask` → mark；`_calc_unrealized_pnl` 改用 mark。需新增 mark 实时数据源（OKX `mark-price` WS channel，待验证 ccxtpro 支持）+ 碰资金安全路径，独立立项。
- **清算价 MMR 分档**（iter-3）：`_calc_liquidation_price` 现为 MMR=0 近似；真实 OKX 用分档维持保证金率（tiered MMR）。需 `fetchLeverageTiers` 分档表 + 逐仓含 MMR 清算价公式建模，独立立项。
- **fee_rate**：维持 `config.fee_rate`（真实 fee 依赖账户 VIP、非公开市场数据）。
- **min-order-size 校验**：cs/precision 真实化后 OKX `limits.amount.min` 仍未校验；显式 defer（与 fee/F1/F2 同批，per `feedback_sim_real_data_except_order_mgmt`）。
- **contracts 单位标签**：见 D2，grounding 触发型。

## 7. 风险与回滚

- **风险（7 处 ×cs 遗漏）**：helper 单点 + §5 cs≠1 主断言全路径覆盖。
- **风险（下游传播遗漏）**：§2.5 审计 B1-B4 + B3-bis 全列（两套独立 FIFO 各覆盖）+ §5 各加回归断言；cs 来源统一走 §3.5 single source。反推公式（÷cs）专项测守卫恒放行场景。
- **风险（load_markets 启动失败）**：带重试 + fail-fast（对齐 okx.py / sim fetch_ticker 既有取舍）。
- **DB schema 变更**：D4 的 cs 持久化引入 `sessions` +1 nullable 列 + alembic migration（**修订原 spec「无 schema 变更」**）。`sessions.fee_rate`（nullable per-session，`compute` 已 select / `_sim_metrics._fetch_fee_rate` 已读）是现成对称先例，降低 migration 风险。历史 session 该列 NULL → 分析层 cs=1.0 fallback，新旧 run 各自语义正确。
- **回滚单位**：sim 执行层 + 两条下单路径 + 下游 5 处（B1-B4 + B3-bis）+ config + 1 个 DB 列 migration。`_Position.contracts` 字段类型不变（仍 float），仅存储数从 base 变张数；历史 session restore 出旧 base 尺度——用户已接受历史不可比，sim-only 不跨 iter restore session（同 `project_r2_8b_legacy_decision_restore_boundary`）。

## 8. 杂项修订

- `base.py:191` `get_contract_size` docstring 硬编「Sim = 1.0」，本期后 stale，更新为真实 cs 语义。
