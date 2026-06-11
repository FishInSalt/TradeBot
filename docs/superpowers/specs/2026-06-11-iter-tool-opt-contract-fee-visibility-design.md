# Iter: tool-opt-contract-fee-visibility — 设计 spec

日期：2026-06-11
来源：sim #17（session `64b4ea1f`，133 cycles / 18h / 11 opens）全量日志取证 + agent 上下文通道盘点
关联：pre-W4 backlog ⑥（open_position docstring P0 候选的 lean 切片）；G framework（`.working/iter-trade-discipline-quality/`）保持未启动，本 iter 不与其冲突

---

## 1. 背景与实证

### 1.1 contract_size 完全不可见（主议题）

`sessions.contract_size = 0.01`（BTC/USDT:USDT，exchange 启动时从 ccxt market 持久化），`simulated.py` 全程使用，但**没有任何 agent 可见通道渲染过该值**。`get_position` 的 Fee & Breakeven 公式 `(= entry × contracts × contract_size × rate)` 符号化引用它却不给值——四个操作数中 agent 已知三个（entry / contracts / fee_rate via persona），唯独 contract_size 缺失。

sim #17 实测心智负担（agent narrative 原文）：

- log 4575-4615：开仓后第二个 cycle，agent 假设 1 张 = 0.001 BTC，算出 SL 损失 $1.40 与系统矛盾，整段多轮自我纠正后从 notional 反推出 0.01，仍标注 "That seems strange"，最终 "Let me just trust the system"
- log 8651："actually I'm not sure of contract size"
- log 15580：约 3 小时后靠 "OKX contract size for BTC/USDT is typically 0.01" 的背景知识猜测确认
- log 16505：PnL 手算对不上，放弃精确计算（"Let me not worry about exact PnL math"）

反推/不确定段落 ≥4 次，达到工具设计原则 2 的"反复手算 ≥3 次"信号补齐触发条件。

`notional = contracts × contract_size × price` 这条换算规则当前在任何 agent 可见处都不存在——agent 看得到 notional 数值与 contracts 数值，但没有换算规则，这是反推行为的根因。

### 1.2 开仓前 fee 不进入决策（副议题）

- 11 次开仓中仅 1 次（log 37725）在开仓前推理显式计算 fee（"Round-trip fees: ~0.2% of notional = ~$12.60"）；基线 = 1/11（9%）
- agent 有该能力但应用不一致：2 次用 "yields <1:1 R:R before fees — structurally inferior" 否决边际 setup（log 2157 / 2265），即 fee 意识在"拒绝"时出现、在"进场"时缺席
- 代价：全场 gross +627.77 / fees -360.44 / net +267.33，fee 吃掉 57% 毛利；多笔 4-5 分钟即止损的快进快出各付一个往返 0.2%
- 事实可见性已饱和仍无效：agent 在 log 40716 看到 `fees -237.16` 的 gross/net 对照后，下一次开仓（42170）依旧未把 fee 纳入开仓前推理

按原则 8 的反思顺序（能力 / 描述 / 默认值 / 接口）逐项排除后，persona 流程句作为 last-resort nudge 成立（见 §4）。

### 1.3 取证中排除的议题（不立项）

- liquidation 公式不可见（实现于 `simulated.py:125-129`）：133 cycles 中 agent 从未尝试推导强平价，只引用工具给的距离 → 无心智负担证据
- funding 结算金额：agent 只使用方向信息，无困惑痕迹
- 永续合约"教科书式"规则段：取证显示 agent 规则理解除 contract_size 外无真实缺口；大段描述回退 N7 的 Layer 1 精简成果

---

## 2. Scope 与 Non-goals

### Scope（4 处改动 + 2 项一致性收尾，全部文案级 + 1 处时序重排）

| # | 位置 | 改动 | 对应实证 |
|---|---|---|---|
| 1 | `persona.py` Layer 1 | Fee 行后加 contract size 行 + notional 换算规则 | §1.1 |
| 2 | `tools_perception.py` get_position | Fee & Breakeven / Notional 行符号公式代入实际数值 | §1.1 |
| 3 | `trader.py` open_position + place_limit_order docstring | fee 事实数值化 inline 示例 + 两工具 position_pct margin 语义消歧（措辞逐字相同，`tools_execution.py:106-108` / `:707-708` 同 margin 语义） | §1.2 + log 19872/37672 |
| 4 | `persona.py` Layer 2 Risk-Reward | 加一句 breakeven 疑问句（流程性，无数值阈值） | §1.2 |
| 收尾 a | `trader.py` get_position docstring | fee 公式表述统一为 `notional × fee_rate` | §3.5 |
| 收尾 b | `trader.py` get_position docstring | 输出格式描述与改动 2 新渲染对齐 | drift 纪律 |

配套：`RuntimeConfig` 新增 `contract_size` 与 `symbol`（或 `base_ccy`）两个字段（persona 现无任何 symbol 通道，base_ccy 渲染必须新增注入）+ 注入时序改动（§3.6）。

风险分布：改动 1-4 与收尾 a/b 为纯文案级；**§3.6 是本 iter 唯一有回归面的部分**（初始化拆分 + build_services async 化 + 回调时序约束），plan 与 review 的注意力应集中于此。

### Non-goals

- G framework（G2-G8）不启动；本 iter 不实现任何 trade plan 模板 / mode / 数值纪律阈值
- 不新增工具、不改 fact-provider 输出之外的任何执行语义
- S1 fee bleed 直接干预（per-trade fee budget 等）维持 defer，触发条件不变
- OKX 路径只保证测试通过，不做行为对齐（maker/taker 区分等 Tier 3 议题不动）

---

## 3. 设计详情

### 3.1 改动 1 — persona Layer 1 `Market Context`

`persona.py` `_build_layer1`，现有 Fee 两行后新增一行：

```
Fee: taker {fee_pct:.3f}% per side (set at session start).
Round-trip cost on a position = entry_fee + exit_fee ≈ 2 × fee_rate × notional.
Contract size: 1 contract = {contract_size:g} {base_ccy}. Notional (USDT) = contracts × contract_size × price.
```

- `contract_size` 来自 `RuntimeConfig.contract_size`（注入路径见 §3.6）
- `base_ccy` 从 symbol 解析（`BTC/USDT:USDT` → `BTC`）；**注意 persona 现无任何 symbol 通道**（`RuntimeConfig` 仅 `wake_max_minutes` / `taker_fee_rate`，`PersonaConfig` 仅 personality / trading_style），symbol（或解析后的 base_ccy）需作为新字段与 contract_size 一并注入（§3.6）
- 第二句是 `notional = contracts × contract_size × price` 规则的**唯一权威源**（原则 3，因子序与 §3.2 Notional 行实例一致：张数 × 每张面值 × 价格）；get_position 输出只做数值实例

### 3.2 改动 2 — get_position 渲染（`tools_perception.py` Fee & Breakeven / Risk Exposure）

三行符号变量全部替换为代入数值（数值为示例）：

```
改前：Entry fee paid: ~-32.39 USDT (= entry × contracts × contract_size × rate)
改后：Entry fee paid: ~-32.39 USDT (= notional 32,393.84 × 0.100%)

改前：  = 62,355.80 × (1 − 2 × fee_rate) [short round-trip taker]
改后：  = 62,355.80 × (1 − 2 × 0.100%) [short round-trip taker]

改前：Notional value: 32393.84 USDT (75.1% of equity 43128.50)
改后：Notional value: 32,393.84 USDT = 51.95 contracts × 0.01 BTC × entry 62,355.80 (75.1% of equity 43,128.50)
```

公式分工（与 §3.5 收尾 a 完全一致）：Entry fee 行用 `notional × fee_rate` 形式——与 get_position / open_position docstring 同一因式；`contracts × contract_size × price` 的分解**只由 Notional 行承担一次**（规则权威源在 persona §3.1）。三处表述统一，contract_size 在输出中单点展开，反推链条不受损（Notional 行紧邻其上）。

- `~` 保留：entry_fee 由均价重算，加仓场景与逐笔 fill fee 之和有微差，`~` 是诚实标注
- Unicode minus（U+2212）等现有格式细节不变；**例外（有意变更）**：Notional 行的 notional / equity 现为 `{:.2f}` 无千分位，本次顺带统一为 `{:,.2f}`——同一行内 entry / breakeven 已带千分位，混用不一致，统一即原则 7（既有测试断言同步更新）
- 与 sim 同步开仓返回 `Entry fee: -7.50 USDT (notional 7,498.52)`（`tools_execution.py:140`）及 place_limit_order 回执 `(notional ~7,498.52 × ~0.100%)`（`:745`）数值口径互证；`:154/243` 为 OKX deferred 异步分支，sim-only 运行不走，不作互证依据

### 3.3 改动 3 — open_position + place_limit_order docstring（`trader.py`）

两点，均在 wrapper docstring（LLM 通道 = `tool_def.description`）：

1. fee 事实数值化（open_position）：现有 "Entry incurs taker fee = notional × fee_rate; the return reports the actual fee." 后接 inline 散文示例（防 griffe 剥离，不用块状 `Example:` section）：

   ```
   For example, a fill of 12.02 contracts @ 62,383.70 returns
   'Entry fee: -7.50 USDT (notional 7,498.52)' (rate is session-specific;
   this example uses 0.1%).
   ```

   示例数值取自 sim #17（fee_rate 0.001）；默认 `DEFAULT_TAKER_FEE_RATE = 0.0005`，故加 session-specific 限定防误读。

2. position_pct 语义消歧——**两工具同修**（措辞逐字相同：`trader.py` open_position 与 place_limit_order；sim #17 中 agent 两次自行推理该语义，log 19872/37672；实现核验 `tools_execution.py:106-108` 与 `:707-708` 均为 margin 语义）：

   ```
   改前：position_pct: percent of free balance to allocate (0-100).
   改后：position_pct: percent of free balance to use as margin (0-100);
         resulting notional = margin × leverage.
   ```

### 3.4 改动 4 — persona Layer 2 `Risk-Reward`

在现有四个疑问句后追加一句，保持该节疑问句体例，不设数值阈值：

```
Does the expected move clear the round-trip fee cost — where is breakeven
(entry ± 2 × fee_rate) relative to your stop and target?
```

定位：last-resort nudge（原则 8 反思顺序已走完，见 §1.2）；引用 Layer 1 公式不另造表述（原则 3）；带独立回滚条件（§6）。

### 3.5 收尾 a/b — get_position docstring 统一与对齐（`trader.py`）

- fee 公式表述统一：`entry_fee paid (= entry × contracts × contract_size × rate)` → `entry_fee paid (= notional × fee_rate)`。与 open_position docstring 同一写法；notional 的分解规则由 persona（§3.1）唯一承担。分工：**persona 管 notional 怎么算，docstring 管 fee = notional × rate**，每条规则单源
- 输出格式描述与 §3.2 新渲染对齐（docstring 中引用的输出样式不得与实际渲染漂移）

### 3.6 contract_size / symbol 注入时序（唯一有回归面的改动）

现状问题：`build_services`（`src/cli/app.py:921`，sync）中 `create_trader_agent` 构建 system prompt 发生在 `await exchange.start()`（`src/cli/app.py:1199`，`_init_contract_size()` 所在）之前——新 session 在 persona 构建时 contract_size 未知（`sessions.contract_size` 为 NULL，exchange 内部默认 1.0）。

**方案：把 contract_size 初始化从 start() 中拆出、单独前置；撮合循环启动位置不动。**

`_init_contract_size()`（`simulated.py:1120`，load_markets + market lookup）与撮合/mark 循环拉起（`simulated.py:1146-1148`）在 `start()` 内本就是两个独立步骤。拆为独立的市场元数据初始化方法（如 `init_market_meta()`），在 build_services 内创建 exchange 后、`create_trader_agent` 前 await 调用（build_services 相应 async 化，caller `src/cli/app.py:1123` 同步改 await）；`start()` 退化为仅做 ticker seed + 循环拉起（幂等跳过已完成的元数据初始化），位置保持在 `src/cli/app.py:1199` 的 try 块内不动。

**接口层归属：`init_market_meta()` 落 `BaseExchange` 接口、两端实现**——build_services 的 OKX 分支（`src/cli/app.py:949-956`）同样走到 `create_trader_agent`，fail-loud 硬门对两种 exchange 都生效；只在 SimulatedExchange 实现会让 OKX 路径要么缺方法、要么 build 时拿不到 contract_size 被 fail-loud 拦截，打破 Non-goals 的"OKX 路径保证测试通过"。实现：sim = load_markets + market lookup + persist；okx = `_preload_markets`（`okx.py:188-190`）包装 + market lookup。sentinel fail-loud 细则（硬约束 1）保证 init 路径不经过 `okx.py:946-948` 惰性 `get_contract_size` 的两处 `1.0` 静默兜底；该惰性兜底自身的清退属 OKX runtime 行为，sim-only 阶段不动，列实盘准备期（Tier 3）follow-up。

否决的备选——"把 start() 整体提前到 agent 创建之前"：`start()` 立即拉起后台循环并可能触发 fill/alert 回调，而回调注册在 `src/cli/app.py:1187/1191`；回调为 None 时事件被**静默丢弃**（`base.py:355-356`、`simulated.py:683/687`）。整体前移会让 resume session（已恢复持仓/挂单/alert）在 "start → 回调注册" 窗口内的止损/强平/alert 被吞——正确性回归。方案 (b) 不启动循环，无此窗口，回归面也更小。

硬约束：

- **硬约束 1：persona 不得渲染 fallback 值**。若构建时 contract_size 不可得 → fail loud（与 build_services 现有 `fee_rate is None → ValueError` 同 pattern），不允许 silent 渲染 `1 contract = 1 BTC`。落实细则：`_init_contract_size` 现有 `or 1.0` 兜底（`simulated.py:1290`）会架空该约束（无法区分"未初始化"与"合法 1.0"）——plan 用 sentinel（内部未初始化态 ≠ 数值 1.0）落实
- **硬约束 2：初始化失败时的资源清理保证不得回退**。现 `src/cli/app.py:1192-1196` try/finally 的存在目的即兜住启动失败的 NewsService httpx client / exchange 连接泄漏；前置的 `init_market_meta()` 若失败（发生在该 try 块之前），其已创建的资源（ccxt HTTP session 等）必须有等价清理路径
- **硬约束 3：回调注册与循环启动的相对序不变**——`on_fill`/`on_alert` 注册（`src/cli/app.py:1187/1191`）必须仍先于撮合循环拉起；配套一个 "resume session 在循环启动后第一个 tick 触发 fill/alert 仍被路由" 的回归测试

配套决定：

- **注入字段**：`RuntimeConfig` 新增 `contract_size` 与 `symbol`（或解析后 `base_ccy`）两字段，**默认值策略沿用 `taker_fee_rate` 既有模式**（默认值仅供测试 / 生产路径必须显式设置 / build_services fail-loud）——避免 23 处裸 `RuntimeConfig()` 与 test_persona.py 39 处调用点触发 raise，仅需适配其中做字符串断言的测试
- **base_ccy 解析复用 `extract_base_currency`**（`src/integrations/news/models.py:64`，已处理 `BTC/USDT:USDT → BTC` 及 1000PEPE/kSHIB 前缀），不另写解析（原则 3）；如认为 cli/persona 依赖 news 模块属层次倒挂，plan 阶段可决定将该纯函数提升至共享 util，但单源不变
- 两处 `RuntimeConfig` 构造点（`build_services` 内 + Phase 5b `runtime_config_for_capture`，`src/cli/app.py:1128-1131`）必须同步注入；**扩展既有 AST 守卫** `tests/test_drift_p4_capture_paths.py:239`（已兼容 AsyncFunctionDef，async 化不破坏）补 `contract_size=`/`symbol=` 断言，不新建守卫

---

## 4. 原则合规

| 原则 | 论证 |
|---|---|
| 1 fact-provider 不是 guard | 全部改动为事实补齐/数值实例化；改动 4 在 persona（style/process 合法载体），不在工具通道；docstring 无 directive 词 |
| 2 服务 agent 心智路径 | contract_size 手算 ≥4 次达到信号补齐触发条件；docstring 升级为完整 call→output 示例 |
| 3 信号唯一权威源 | notional 规则单源于 persona；fee 规则统一为 `notional × fee_rate` 单一表述；get_position 输出只做实例不复述规则 |
| 4 信号补齐优先于新工具 | 0 新工具；underlying data（contract_size）已存在仅未渲染 |
| 7 输出表达友好 | 符号变量 → 带值带单位（`0.01 BTC` / `0.100%`） |
| 8 信任 agent + 工具优先 | 改动 4 的 last-resort 论证见 §1.2；反思顺序（能力/描述/默认值/接口）逐项排除后才用 nudge，且带回滚条件 |
| 元：实证优先 | 全部改动对应 sim #17 具体 log 行号；liquidation/funding 等无实证议题明确不立项（§1.3） |

---

## 5. 测试

| # | 测试 | 断言对象 |
|---|---|---|
| 1 | persona Layer 1 注入 | system prompt 含 `Contract size: 1 contract = 0.01 BTC` + notional 公式行（注入 RuntimeConfig.contract_size=0.01） |
| 2 | persona fail-loud | **作用域 = build_services 生产路径**（沿用 `fee_rate is None → ValueError` 模式）；`_build_layer1` 对测试默认值正常渲染不 raise；sentinel 区分"未初始化"与合法 1.0（§3.6 硬约束 1 细则） |
| 3 | get_position 渲染 | 三行新格式（更新现有 U+2212 断言所在测试，含 Notional 行千分位变更）；输出不再含符号变量 `contract_size × rate` / `2 × fee_rate`（字面） |
| 4 | docstring drift guards | 断言 `tool_def.description`（非 impl docstring）：open_position 含 inline 示例与 margin 语义；place_limit_order 含 margin 语义；get_position 含 `notional × fee_rate` 且不含旧符号公式 |
| 5 | persona Layer 2 | Risk-Reward 节含 breakeven 疑问句 |
| 6 | RuntimeConfig 双构造点守卫 | **扩展既有 AST 守卫** `tests/test_drift_p4_capture_paths.py:239`，补 `contract_size=`/`symbol=` 断言（不新建） |
| 7 | 回调时序回归（§3.6 硬约束 3） | resume session（已恢复持仓/挂单/alert）在撮合循环启动后第一个 tick 触发 fill/alert 仍被正确路由到 scheduler |
| 8 | 回归 | 全量 pytest 通过（1756+，兼容 sibling 改动按 prefix/regex 断言）；既有 persona 测试中对 Layer 1 做字符串断言的点需同步适配新增行（23 处裸 `RuntimeConfig()` / test_persona.py 39 处调用点因默认值策略不 raise，仅断言点需适配） |

---

## 6. W4 / sim #18 验证口径

**主指标 1 — contract_size 心智负担消除**（改动 1+2）
narrative 中 contract size 反推/不确定段落数（"not sure of contract size" / multiplier 倒推 / "doesn't match" 类，人工核验上下文）。baseline sim #17 ≥4 段；**目标 = 0**。事实补齐类，无回滚条件（除 token 成本异常）。

**主指标 2 — 开仓前 fee 进入推理**（改动 3+4）
open_position 调用前同 cycle narrative 的 fee/breakeven 提及率（grep `fee|breakeven|round-trip` 于调用前窗口，人工剔除工具输出回显与 "feels" 类误匹配）。baseline = 1/11（9%）。

Gate：**≥50% retain / 20-50% observe 一期 / <20% 判 nudge 无效 → 回滚改动 4**（一行 revert；per 原则 8 nudge 无效就撤，不加码）。

归因 caveat：主指标 2 测的是**改动 3+4 的合并效果**，回滚动作只撤改动 4（改动 3 是事实补齐，恒留）——若落入回滚带，无法分离两者贡献，按"nudge 未达标"处置即可，不回溯改动 3。另：每 sim 开仓次数 ~11，提及率量化步长 ~9pp，gate 边界为粗粒度判断，不作精确统计推断。

**观察指标（不作 gate）**：fee 占毛利比（sim #17 = 57%）。受 market regime 与交易频率影响，cross-sim 不可比（sim#15 教训），只记录不判定。

---

## 7. Token 成本记账

- base 增量：persona Layer 1 +1 行（两句）+ Layer 2 +1 句 + docstring +~50 词（两工具）≈ **<100 tokens/cycle**（每 cycle 随 system prompt + tool defs 发送）
- 变动增量：get_position 每次调用行长 +~60 chars
- 对照 sim#15 取证（工具/prompt 改动抬 base 占 token 回归 ~70%）：本 iter 为有意识的小额支出，sim #18 token 分析时按本节口径归因
