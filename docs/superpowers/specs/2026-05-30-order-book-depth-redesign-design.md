# get_order_book 重设计：单位解耦归一化 + 距离改 pts/bp + 输出精简

> 数据层：`fetch_order_book` 内张→base 归一化（用 `_ccxt.market()` 真 contractSize，**绕过**执行层 `get_contract_size`，解耦）
> 渲染层：规模量改 **USD notional**；距离/深度 `%` → **price points（主）+ bp（辅）**；去 `(0.00% deep)` 冗余；Concentrated 段去 best 重复 + 去距离列
> 范围：只碰 `fetch_order_book` + `get_order_book`；`fetch_trades` 留 taker-flow iter；执行层 `get_contract_size=1.0` 留 sim 执行保真 iter

## 1. 背景与问题

B 类换源（`bbdf3aa`，2026-05-29）把 `SimulatedExchange.fetch_order_book` 从合成数据改为调真实 `_ccxt`。代入真实 OKX 盘口后，`get_order_book` 暴露三类确定性缺陷（每次活跃调用必复现，1 个 smoke 即确认）。

**实证复现**（2026-05-30 @05:28 UTC，BTC/ETH/SOL 各一次真实拉取，`.working/tool-optimization/diag_order_book_real.py`）：

| 标的 | price | real cs | sim cs | spread% 渲染 | deep% 渲染 | best bid 标签 | 真值 | 误差 | top-15 跨度 |
|---|---|---|---|---|---|---|---|---|---|
| BTC | 73510 | 0.01 | **1.0** | `0.000%` | `0.00%` | `2031 BTC` | 20.31 BTC | **100×** | 0.4 bp |
| ETH | 2013 | 0.1 | **1.0** | `0.000%` | `0.01%` | `2227 ETH` | 222.7 ETH | **10×** | 0.8 bp |
| SOL | 82 | 1.0 | 1.0 | `0.012%` | `0.17%` | `1579 SOL` | 1579 SOL | 碰巧对 | 17 bp |

1. **% 字段集体塌陷**：所有距离/深度字段 = `(price差 / price) × 100`，经 `.2f`/`.3f` 截断。真实 top-15 盘口价差极小（BTC mid ±0.003%），相对 % 数学上趋零 → `spread`/`bid_deep`/`ask_deep`/concentrated 的 `dist` 全显 `0.00%`。BTC 10 个 concentrated 档距离全 `0.00% below/above mid`，agent 无法分辨远近。**价格量级放大效应被 SOL 反证**：同样致密的盘口，BTC（$73510）塌成 0、SOL（$82）还能显 0.17%——塌陷 = 绝对价差 ÷ 价格，价格越高越严重。是**表达方式错误，非计算 bug**。
2. **amount 单位错标**：`l.amount` 是合约**张数**（OKX swap 原生单位，CCXT 不转换），却按 `symbol.split("/")[0]` 标成 base 币名（`tools_perception.py:1707`）。BTC 差 100×、ETH 差 10×、SOL 因 `cs=1.0` 碰巧不露馅。
3. **冗余**：`(0.00% deep)` 既塌陷又与价格区间 `over X - Y` 重复。

**反向佐证根源边界**：`Bid share: 84.1% (5.30:1)` 在 BTC 上**正确**——它是张数比值、单位无关。证明单位错只污染**绝对量标签**，不污染相对结构信号。

## 2. 根源（数据流追踪）

链路 `ccxt.okx() → SimulatedExchange.fetch_order_book → market_data → get_order_book 渲染`：

- **根源 A（% 塌陷，纯渲染层）**：`tools_perception.py:1728/1740/1741/1785` 用相对 %，致密盘口下精度不足。修法：距离改 price points（主，永不塌）+ bp（辅，跨币种可比）。
- **根源 B（单位错，双层）**：
  - B1 渲染层（`tools_perception.py:1707`）：直接把张数标 base 币名，从未做 contractSize 换算。
  - B2 数据层（`simulated.py:1226`）：`get_contract_size` 硬编码 `1.0`，是**执行层地基**（调用方遍布 `tools_execution.py:103/128/654` 下单/平仓 notional+fee、`tools_perception.py:319-383` get_position Risk Exposure、`cli/app.py:499`）；改它牵动 PnL/撮合/margin，属独立 sim 执行保真 iter（per memory `feedback_sim_real_data_except_order_mgmt`）。
  - 附：`base.py:67/83` model 注释 `# base-currency` 是错误的契约声明。
- **根源 C** 是 A 的衍生（deep% 塌 0 后冗余）。

## 3. 设计

### 3.1 数据层 — `fetch_order_book` 张→base 归一化（解耦）

在 `simulated.py::fetch_order_book` 构造 `OrderBookLevel` 时 `amount × contractSize`：

```python
data = await self._ccxt.fetch_order_book(symbol, limit=depth)
# ccxt fetch_order_book 首行 await self.load_markets()（okx.py:2045 已验证）→ market() 必可用，无需 sim start() 额外预加载
cs = float(self._ccxt.market(symbol).get("contractSize") or 1.0)
bids = [OrderBookLevel(price=float(p), amount=float(a) * cs)   # 张 → base
        for p, a, *_ in data.get("bids", []) if p is not None and a is not None]
asks = [OrderBookLevel(price=float(p), amount=float(a) * cs)
        for p, a, *_ in data.get("asks", []) if p is not None and a is not None]
```

**解耦三性质**：
- 数据源用 `_ccxt.market()["contractSize"]`（ccxt 真值 0.01/0.1/1.0），**不是** sim 执行层 `get_contract_size()=1.0`；
- `OrderBook.amount` 变真 base 币 → 兑现 `base.py:67` 契约 → 渲染层单位标签自动正确；
- 执行层 `get_contract_size=1.0` 原封不动，留给独立 iter。

**前提已验证天然满足**：ccxt `fetch_order_book` 首行 `await self.load_markets()`（`okx.py:2045`），fetch 返回后 `market()` 必可用。

### 3.2 渲染层 — 新输出格式

真实 @05:28 UTC BTC 数据样张（吸收单位 notional / %→pts+bp / 去冗余 / 去 best 重复 / 去距离列）：

```
=== Order Book (BTC/USDT:USDT @ 05:28:25 UTC) ===
Best bid: 73509.90 × $1.49M  |  Best ask: 73510.00 × $241K
Spread: 0.10 pts (0.01 bp)

=== Depth (top 15 each side) ===
  Bids: $1.54M over 73509.90 - 73506.70  (span 3.2 pts / 0.4 bp)
  Asks: $0.29M over 73510.00 - 73512.00  (span 2.0 pts / 0.3 bp)
  Bid share: 84.1% (bid : ask = 5.30 : 1)

=== Concentrated Levels (beyond best bid/ask, size > 3× median of top 15) ===
  Bid  73509.80  $20.8K
  Bid  73508.60  $15.7K
  Ask  73511.60  $36.7K
```

**逐项对比现状**：

| 字段 | 现状（塌陷/错） | 新 |
|---|---|---|
| best bid/ask 量 | `2031.31 BTC`（张数错标） | `$1.49M`（USD notional） |
| spread | `0.10 (0.000%)` | `0.10 pts (0.01 bp)` |
| depth 量 | `2096.89 BTC cumulative` | `$1.54M` |
| depth 跨度 | `(0.00% deep)` 塌陷+冗余 | `(span 3.2 pts / 0.4 bp)` |
| bid share 接近 50% | `~50% (balanced)`（评价词） | `49.6% (bid:ask = 0.98:1)`（实际值） |
| concentrated 量（同档 73509.80） | `28.31 BTC`（张数错标） | `$20.8K` |
| concentrated 距离 | `(0.00% below mid)` ×10 全一样 | 去掉（位置由 price 隐含） |
| best 在 concentrated | 重复出现 | 排除（已在 Best 行） |

### 3.3 字段语义

- **规模量（USD notional）**：`notional = level.amount × level.price`（amount 已归一化成真 base 币）。逐值自适应 `$K/$M`（order_book 单档跨度大，per-value 比 per-invocation 可读；量级差本身是信号）。
- **距离/深度（pts + bp）**：`pts` = 绝对价差（永不塌）；`bp` = `pts / price × 10000`（跨币种可比）。spread 与 depth span 两者都给。**bp 的跨币种价值**（诊断实证）：BTC top-15 跨 `0.4 bp` vs SOL `17 bp`，一眼揭示"BTC 盘口极致密 / SOL 相对稀疏"——这正是 `%` 在 BTC 侧塌成 0 后丢掉的真实信号。
- **Bid share**：`total_bid / (total_bid+total_ask)`（张数比值、单位无关、本就正确）。三态 fact 化：
  - 中间态统一显示实际值 + 比值 `X% (bid:ask = R:1)`，**取消"接近 50% → 固定 ~50% (balanced)"特例**（去评价词 + 去信息损失）；
  - 边界态保留事实描述：`0% (asks only, no bids in top N)` / `100% (bids only, no asks in top N)`。
- **Depth 段**：`Bids/Asks: <cumulative notional> over <best price> - <第N档 price> (span <pts> / <bp>)`。cumulative notional = 逐档 `amount × price` 累加。
- **Concentrated Levels**：识别"纵深里的大单墙"（best 之外的隐藏支撑/阻力）。
  - 触发：`size（张数）> 3× 同侧 top-N median`（阈值在张数维度判定，与单位换算正交）；
  - **排除 best bid[0]/ask[0]**（已在 Best 行）；若排除后无档则段不显示（fact：纵深无大单墙）；
  - 列 `side / price / notional`，**无距离列**（致密盘口距离≈0 是噪声；位置由 price vs best 隐含）；
  - 副标题给精确 fact 定义 `size > 3× median of top N`。
- **header**：`@<取数 wall time> UTC`（瞬时快照，保留秒级 timestamp）。

### 3.4 错误与降级（不变，已符合 fact-only）

- 服务异常：`=== Order Book (...) ===\nError: Temporarily unavailable (ClassName).`
- 数据不足 / 全零 amount：`Error: Insufficient data (requested depth X, got Y).`

## 4. 架构与数据流

```
get_order_book (工具, tools_perception.py)  ← 渲染重做（单位→notional / %→pts+bp / 去冗余 / concentrated 精简）
  → market_data.get_order_book(symbol, depth)            # 透传不变
    → SimulatedExchange.fetch_order_book(symbol, depth)  # 内部张→base 归一化（§3.1，本 iter 唯一 sim 层改动）
      → _ccxt.fetch_order_book(symbol, limit=depth)      # 首行 load_markets → market() 真 contractSize 可用
```

- 改动面 1：`simulated.py::fetch_order_book` 加 contractSize 归一化（§3.1）。
- 改动面 2：`tools_perception.py::get_order_book` 渲染层重做（§3.2/§3.3）；`ORDER_BOOK_*` 常量保留（depth=15 / concentration 3× / max concentrated / balanced threshold——后者随 bid share 三态 fact 化可删）。
- 改动面 3：`trader.py` wrapper docstring 更新为 call→output 新格式示例（LLM 通道走 wrapper docstring，per `[[project_tool_docstring_llm_channel]]`；`Returns:` 块整段进 LLM，per `[[project_griffe_example_section_stripped]]`）。
- **不碰**：`fetch_trades`（taker-flow iter）、`OrderBook` model 形状、`market_data` 透传、执行层 `get_contract_size`。
- `okx.py::fetch_order_book` 同步加同款归一化（base 抽象契约一致；实盘暂不用，per CLAUDE.md「维护通过测试」）。

## 5. 测试策略

- **① fetch 层归一化**：mock `_ccxt`（含 `market(symbol)` 返回带 `contractSize` 的 dict），断言 `OrderBookLevel.amount` 已 ×cs；显式排序/None-safe 行为保持；限频异常上抛。
- **② 渲染层**：mock `OrderBook`，断言 notional = amount×price / spread 与 depth span 的 pts+bp / bid share 三态 fact 化（中间态实际值无 "balanced" / 边界态描述）/ Concentrated 排除 best + 无距离列 + 张数维度阈值 / 空 concentrated 不显示段 / 降级路径。
- **③ 单位回归**：≥1 个 `cs≠1`（BTC 0.01）fixture，断言 notional 量级正确（防单位③回归）。
- **④ mock 保真**（per `[[project_iter2_mock_fidelity_lesson]]`）：≥1 fixture 按真实 OKX 盘口形态（张数 amount + cs≠1）。

## 6. 范围边界与已知遗留

- **本 iter**：`fetch_order_book` 归一化 + `get_order_book` 渲染重做。
- **跨层 notional 过渡期风险（必记）**：本 iter 后 `get_order_book` notional 用真 cs，而 `get_position`/执行层 notional 仍用 `cs=1.0`（偏小 100× for BTC）。两者同标 `$` 但口径差 100×，agent 同 cycle 对账有误判风险（原则 7「同名字段不同语义」）。根源是 F1 执行层 gap，**非本工具缺陷**；sim 执行保真 iter 修 F1 后两者自动一致。不在工具输出加 caveat（违反 fact-only）。
- **换源后 adoption 验证 gate**：本设计在**计算正确性**维度实证驱动（诊断真实数据）；在**心智贴合/adoption**维度是 design-time 推测（换源后 get_order_book 无新 session，换源前用合成盘口不能推断真实盘口下 agent 行为，per `[[feedback_data_mismatch_old_impl_inference]]`）。换源后新 session 用 grounding 评估：agent 是否真读 notional/bp/concentrated、depth 分布、是否对 pts/bp 困惑。**阈值不预设，由首个真实 session baseline 校准**。
- **depth 默认值**：保持 `15`。memory 把"主币 depth=15 太浅（top-15 仅跨 0.4 bp）"列为"需更多数据"候选；无行为数据支撑调参，按数据错配纪律本 iter 不动，留触发型候选。
- **Concentrated 命名**：保留 `Concentrated Levels`（描述 size 分布事实，副标题给精确定义）；改 `Large/Outsized` 等仍带评价，`Levels > 3× median` 作标题生硬——维持。
- **不做**：`fetch_trades` 单位（taker-flow iter）、执行层 `get_contract_size` 真值（sim 执行保真 iter）、load_markets 完整元数据层（precision/min size）、wrapper docstring 降级文案 mini-fix（可独立 direct-merge）。

## 7. 实证依据附录

- **诊断 probe（已落 `.working/tool-optimization/diag_order_book_real.py`）**：2026-05-30 @05:28 UTC 实拉 BTC/ETH/SOL。
  - 单位③确认：amount=张数，real contractSize 0.01/0.1/1.0，sim.get_contract_size 全 1.0；best bid 标签误差 BTC 100× / ETH 10× / SOL 碰巧对。
  - % 塌陷确认：BTC spread_pct 0.000136% → `.3f` 渲染 `0.000%`；bid_deep 0.004353% → `.2f` `0.00%`；10 个 concentrated 档距离全 `0.00%`。
  - 替代表达可行：BTC bid_deep = 3.20 pts / 0.4 bp；SOL = 0.14 pts / 17 bp（pts 永不塌、bp 跨币种可比）。
  - 相对信号正确：Bid share 84.1% / bid:ask 5.30:1 单位无关。
- **ccxt 自动 load_markets**：`okx.py:2045` fetch_order_book 首行 `await self.load_markets()` → `market()` 必可用（离线源码核实）。
- **get_contract_size 执行层耦合面**：`tools_execution.py:103/128/654` + `tools_perception.py:319-383` + `cli/app.py:499`（决定改它属独立执行保真 iter）。
