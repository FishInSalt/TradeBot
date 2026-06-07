# 市价单同步成交 + 开仓/SL/TP 动作拆开（单 warm cycle）

> 核心：市价单（开仓 + 平仓）的成交从「异步事件 → 下一个 cycle 处理」改为「`create_order` 内同步结算并返回」，让 agent 在**同一个 warm cycle** 内完成「进场 → 设 SL/TP」整笔决策。
> 方案：**A（拆开动作 + 同步开仓）**，非 B（bracket 捆绑）。开仓与 set_stop_loss/set_take_profit 保持三个独立工具调用，但落在同一 cycle。
> sim 红利：同步化**删除** market 路径的两阶段（冻结/解冻 + pending 排队 + 下一 tick 撮合 + FillEvent→conditional 触发），并使原计划的 sync-wait 协调机制（Future/超时/让渡）在 sim 内**无需存在**。
> 边界：limit / stop / take_profit 的异步 pending 机制**完全不变**（真正延迟的订单，异步是其正确模型）；OKX 实盘按 CLAUDE.md deferred。

## 1. 背景与核心问题

### 1.1 现状机制（代码事实）

市价开仓走异步两段式：

1. **cycle N**：agent `open_position` → `create_order(market)`（`simulated.py:208-269`）冻结估算保证金（`_free_usdt -= frozen; _frozen_usdt += frozen`，253-254）→ 塞进 `_pending_orders` → 返回 `status="open"`（**不成交**）。
2. **下一个 tick**：后台 `_matching_loop`（`watch_ticker`）→ `_process_tick` 撮合市价单 → `_fill_market_open`（312-376）解冻 + 按 tick 价结算 + 建仓 + 生成 `FillEvent` → `_dispatch_fill_event` → callback → `scheduler.trigger("conditional")`（`app.py:1073`）→ 唤醒 **cycle N+1**。
3. **cycle N+1（conditional）**：agent 处理成交、设 SL/TP。

`set_stop_loss/set_take_profit` 挂在**已存在的仓位**上，仓位只有成交后才存在 → 结构性强制 SL/TP 必须落到 N+1。`open_position` 的 docstring 现亦明示「fill notification（separate trigger, not in the same cycle）」。

### 1.2 核心问题：确定性的同步成交，被当成不可预测的异步事件处理

成交事件机制（异步通知 + 触发新 cycle）本为**不可预测的成交**设计（限价何时成交、stop/TP 是否触发，agent 下单时无法确定）。但流动性强的市价单成交是「下单即成交」的**确定性**结果——把它塞进异步事件机器，产生两个后果：

- 为一件下单时已知必然发生的事，多醒一个**冷启动** cycle。该 cycle 唯一的新信息是真实 `fill_price`/`fee`(/`pnl`)（经 IMPORTANT EVENT 注入，`app.py:487-521`）——但这点信息**可改为同步交付**（开仓工具直接返回），不必为它多醒一个冷 cycle。
- 一笔本应一气呵成的开仓决策（进场 + 保护单）被切到两个互不连续的推理上下文。

### 1.3 实证（session `f670abe1` = BTC sim #15，384 cycles，2026-06-04 last active）

- **成交瞬时**：45 笔 market 单中 **44/45 < 1 秒成交**（min 13ms），1 笔 47s 离群（tick 间隙）。
- **两段式确凿**：`set_take_profit` **41/41 全部**在 conditional cycle；`set_stop_loss` 41/69 在 conditional；开仓主要在 alert(34)/scheduled(6)。84 个 conditional cycle == 84 笔 order_filled（45 market + 24 stop + 14 tp + 1 limit）。
- **重新感知 100% 冗余**：41 个「开仓后设 SL/TP」cycle **全部**在 set 单前先调感知工具（`get_market_data` 41/41，均 2.7 个感知调用/cycle）；而开仓 decision 已写明 planned SL/TP（37-40/41）。代表样本：cycle `343bfcc4` 开仓时 planned SL=72200/TP=71050 → 下个 cycle `3e8bd069` 重新感知后实际设 **SL 72200 / TP 71000**（SL 不变、TP 仅微调 50pt/0.07%——重新感知未改变计划）。
- **无保护窗口风险**：该样本 fill @71751.6，等 conditional cycle 跑起来时价已 71847.7（逆向 96 点、浮亏），其间 15x 空单**无止损**。
- **成本**：conditional cycle ≈ 85K tokens / cycle（中位 wall ~90s，均值 339s 被一个 5.8h 睡眠离群污染）；41 个开仓后冷 cycle ≈ ~3.5M tokens ≈ 会话总量（31.5M）的 ~10%。

## 2. 目标与非目标

**目标**
- 市价开仓后，agent 在**同一个 warm cycle** 内设 SL/TP，无冷启动 conditional cycle、无冗余重新感知。
- 消除/大幅缩短开仓后的无保护窗口（~100s → 秒级，同 cycle 内）。
- 借同步化**简化** sim 市价单代码路径。
- 市价平仓对称同步：realized PnL 即时回到 agent，省一个冷 cycle。

**非目标（明确不做）**
- **bracket / 交易所自动挂 SL/TP**（方案 B）——见 §3 选型。
- **持仓生命周期管理**（保本/移动止损/分批/加仓的自动化）——仍走老 cycle 节奏。
- **OKX 实盘**——sim 落地 + BaseExchange 接口形态预留，不接不测（CLAUDE.md Tier 3）。
- **limit/stop/take_profit 机制变更**——异步 pending 保留（§6）。

## 3. 方案选型：A（拆开 + 同步开仓），否 B（bracket）

三个候选：A = 同步开仓后 agent 自己调 SL/TP（三个独立工具调用，同 cycle）；B = `open_position(sl_price=, tp_price=)` 交易所成交后自动挂；纯异步 B = B 且不给 agent 同 cycle 回执。

**否纯异步 B**：让 agent 在成交时**缺席**（系统替它挂单、它下次 wake 才知道），违背「agent 在场反应」初衷；活跃日内交易者在进场后头几秒最专注。

**否 bracket B**：上一轮异常分析暴露——B 的复杂度根源是「成交即由交易所原子地替 agent 挂 SL/TP」，一旦某腿失败即出现「已成交未保护」半成品态，只能让**系统自主**做激进补救（自动平仓）+ 持久化 bracket 意图做崩溃补挂 + OCO-on-fill 联动。这些都是捆绑生出来的。

**选 A**，因为「拆开动作」让上述复杂度基本消失：

| | A：拆开 + 同步开仓 | B：bracket 捆绑 |
|---|---|---|
| 失败隔离 | 每步原子，要么成要么败，无半成品 | 「已成交未保护」半成品态 |
| 风控补救 | agent 在场决定（retry/平仓/改价） | 系统**自主**平仓，激进 |
| 崩溃恢复 | 轻：启动对账「有仓无保护单→叫醒 agent」 | 重：持久化 bracket 意图 + 自动补挂 |
| 交易所机制 | 无 OCO-on-fill、无 attach 机器 | 要 |
| 无保护窗口 | 秒级（同 cycle 内，开仓返回→set_SL 之间 ~1 步） | ≈0（同 tick 挂上） |
| token 节省 | 省第二个 cycle 的 85K 上下文重载（set 单仅 13ms） | 同 |

A 唯一让出的是秒级、同 cycle 内的无保护窗口（B 是 0）。但秒级 << 今天 ~100s，且**忠实于真实手动交易**（市价成交后手点止损本有几秒）。sim focus 下可接受。

**附带解决**：「重新感知」冗余的根因是**冷启动**（新 cycle 重建语境），非「动作拆开」本身。同一 warm cycle 里 agent 刚感知完做的开仓决策，紧接着挂已定好的 SL/TP，无语境丢失、无重建动机 → 拆开 + 一个 warm cycle 即解。

## 4. Agent 交互（改动后）

```
cycle N（一个 warm 上下文）：
  ...感知 + 决策（已含 planned SL/TP）...
  fill = open_position(side, position_pct, leverage, reasoning)
         → 同步返回 "Filled {amt} @ {fill_price}, fee {fee}.
                     Position OPEN — UNPROTECTED, set SL/TP now."
  set_stop_loss(price)      → 登记 pending stop（即时返回）
  set_take_profit(price)    → 登记 pending take_profit（即时返回）
  ...set_next_wake / alerts...
  cycle N 结束。无 conditional cycle。
```

- `open_position` 签名**不变**（不加 sl/tp 参数——这是 A 与 B 的关键区别）。
- 返回语义变化：从「预估手续费 + 等 fill notification」改为「**真实成交回执** + 显式 UNPROTECTED 提示」。
- `set_stop_loss/set_take_profit` 行为不变（同步登记一个 pending stop/tp 单，异步触发）。
- **agent-facing 契约（persona + 两 wrapper docstring）须同步改写**——`persona.py:105` 现有指令与本流程**正面冲突**，详见 **§10**。

## 5. Sim 实现：市价路径同步化

### 5.1 核心改动

`create_order` 的 `market` 分支（含 `is_close` 开仓/平仓两路，`simulated.py:233`）改为**同步结算**：取价（§5.3）→ 直接执行开/平仓结算（复用 `_fill_market_open` / `_fill_market_close` 的结算逻辑，去掉解冻步）→ 更新仓位与余额 → 返回 fill 结果。全程在 `self._lock` 内原子完成。

> **「去掉解冻步」的真实工作量（caveat，勿低估）**：`_fill_market_open` 现把保证金占用表达成 freeze→unfreeze 的 delta（`simulated.py:341-344`：`diff = order.frozen_margin - actual_cost`），与 `order.frozen_margin` 紧耦合——「去掉解冻步」**不是删一行**，要重推直接占用（`_used_usdt += actual_margin` / `_free_usdt -= actual_cost` / 余额检查改对 `actual_cost`）。且 `_fill_market_open/close` 入参是 `_PendingOrder`，同步路径不再造 pending 单（§5.2）→ 须合成临时 `_PendingOrder` 或重构这两函数签名。两者实现期定。

**返回契约（须显式定义，不可一句"复用结算逻辑"带过）**：现 `create_order` 返回 `Order`（`base.py:49`），**无 `pnl`/`entry_price` 字段**；而平仓的 round-trip net 展示（`app.py:495-503`）依赖 `entry_price`，记 order_filled action 需 `pnl`。`_fill_market_open/close` 本就产出含全部字段的 **`FillEvent`**（`base.py:356-378`：fill_price/fee/pnl/entry_price/is_full_close）。故同步路径在 sim 层应**返回 `FillEvent`（或等价的 fill-result），不是裸 `Order`**；工具层据此渲染回执并记账。BaseExchange 接口的市价 `create_order` 返回类型据此调整（OKX 路径同形）。

> **返回类型异构（落地须标注清楚）**：改后 `create_order` 的**市价**分支返回 `FillEvent`，而 **limit/stop/take_profit** 分支仍返回 `Order`（它们还没成交，无 fill 信息）→ `create_order` 返回类型变为 `Order | FillEvent` union。接口签名 + docstring 必须把这个异构写明，避免调用方误以为恒返回 `Order`。或考虑收敛为统一 fill-result 包装类型（实现期定，不在本 spec 锁死）。

**共享工具层的分派（🟠，必写）**：`open_position`/`close_position`（`tools_execution.py:66-115` 等）是 **exchange 无关共享代码**，现统一拿 `order.id` 渲"You will be notified when filled"。同步后 **sim 返 `FillEvent` / OKX(deferred) 仍返 `Order`**，工具层须显式 `isinstance(result, FillEvent)`（或等价能力标志）分派两种回执：
- `FillEvent` → **同步回执**（"Filled @ X, fee … — set SL/TP now"），字段取 `result.order_id`/`fill_price`/`fee`/`pnl`；
- `Order` → 维持现**异步回执**（"submitted, you will be notified"），字段取 `result.id`。
注意 **字段名不同**（`Order.id` vs `FillEvent.order_id`），`_record_action` 的 order_id 取值须按类型分支。这是"不阻断 OKX 未来接入"承诺的真实落地点。

**round-trip 渲染须带 `contract_size` 因子（🟡-4）**：平仓回执从 conditional cycle（`app.py:499-503`）迁到工具层后，entry_fee 的 `contract_size` 乘法须沿用——现 `app.py:499-503` 与 close 估算 `tools_execution.py:131` 都乘了 `contract_size`（USDT 计价 notional 约定）。pattern 已存在，迁移时不可丢。

**`order_filled` 记录去向（须写明字段缺口与双写，勿一句「记录 action」带过）**：现在的 `_record_action_from_fill`（`app.py:427-448`）与 conditional 触发同在 fill handler 里（`app.py:1066-1077`）。同步化后 market fill **不再走 `_dispatch_fill_event`**，故 order_filled 的 DB 记录改由**同步路径**在工具层承担。**但不能简单调 `_record_action`**：`_record_action`（`tools_execution.py:21-25`）签名只有 `order_id/alert_id/side/price/pnl/reasoning`，**缺 `fee`/`amount`/`entry_price`/`trigger_reason`** 这四个 `_record_action_from_fill` 写入、且 TradeAction 模型有列（`models.py:80/84/85/86`，依次 trigger_reason/fee/amount/entry_price）的字段。落地须**扩展 `_record_action`（加这四字段）或在工具层直接复用 `_record_action_from_fill`**（后者现成、字段齐全，优先）。漏字段的硬命中：`metrics.py:256` `total_fees = sum(f.fee ...)`（fee NULL → 静默漏计总费用）+ `models.py` 明文 invariant「所有 `order_filled` 行 `amount` 必有值」+ `trigger_reason` 分类。注意 `_sim_metrics` 的 PnL 口径**不**在此风险内——其 `_FILLS_SQL`（`scripts/_sim_metrics.py:149-160`）从 `sim_orders`(so.\*) 取 amount/fee、只从 order_filled 行取 `pnl`（`pnl` 在 `_record_action` 里、不缺），其风险归 G3 账本。**双写提示**：改后每次开/平仓在工具层有两条 DB 写入（intent action `open_position`/`close_position` + 新的 `order_filled`），今天是「工具层写 intent + 后台写 order_filled」各一条。即「**记录保留、dispatch+trigger 删除**」——§5.2 删的是异步分发与唤醒，不是记账。

**SimOrder 账本持久化（G3）**：现市价单的 SimOrder 行经异步 `_process_tick → _persist_state`（`simulated.py:956-962`）从 open 迁到 `status="closed"`（写 filled_price/fee/filled_at）。同步路径绕开 `_process_tick`，故须**在同步结算里直接写一行 closed 的 SimOrder**（市价单不再有 open 中间态）。这张账本喂 `fetch_closed_orders`（`simulated.py:779`）**和** `_sim_metrics` 的 PnL 口径（`scripts/_sim_metrics.py:150-160 / 439`，按 `order_id` JOIN + 依赖 `so.filled_at`）——漏写会让平仓 PnL / round-trip 分析直接断。

### 5.2 可删清单（仅 market 路径）

| 删除项 | 现位置 |
|---|---|
| 冻结/解冻两步 + `*1.002` 估算缓冲 | create_order 253-254 + `_fill_market_open` 342 / `_fill_market_close` |
| 市价单进 `_pending_orders` 排队 | create_order 256-263 |
| `_process_tick` 内**市价单撮合分支**（`market_orders` 循环） | _process_tick |
| 市价（开/平）fill 的 **FillEvent dispatch → conditional 触发** | base.py 分发 + app.py:1073 |
| 市价单 pending 的持久化 / 崩溃恢复路径 | _persist_state / restore |

**`*1.002` 缓冲可删的论证闭合**：该缓冲（`create_order:247`）本防「估算价→成交价之间的漂移」；同步成交用**同一** `_latest_ticker`，估算==实际、无漂移 → 缓冲存在理由消失（这正支持删除）。余额检查随之从「对 buffered `frozen`」改为「对 `actual_cost`」（与 §5.1 margin caveat 一致）。

**惰性守卫（G5，不删、记档）**：`has_pending_market_order`（用于 `tools_execution.py:84` open / `:121` close）在同步后对 sim 市价路径**恒 False**（市价单永不进 `_pending_orders`）。它原本防的「同一 symbol 重复市价单」竞态在同步路径下已不存在（开仓在 cycle 内原子完成才返回）。**保留为无害**（OKX 异步路径可能仍需），实现时注释说明其对 sim 已为死分支，勿误判为有效防线。

### 5.3 取价决策

成交价已**同步可得**（`self._latest_ticker`，由 matching loop 持续刷新 638-639，start 时实播种）——当前代码其实已用它算估算，只是把结算硬推迟到下一 tick。两选项：

- **A（选定）**：用缓存 `_latest_ticker.ask/bid`。最简、零延迟；陈旧度 ≤ 一个 watch_ticker 间隔（BTC 亚秒级），可忽略。
- B：`await self._ccxt.fetch_ticker()` 实时取。保真略高，多一次网络调用。

选 **A**：简单且陈旧度可忽略，保真增量很小。

### 5.4 sync-wait 协调机制：sim 内无需存在

原计划为「同步」准备的 per-order Future + 超时 + 让渡事件循环 + 抑制重复触发，只在「成交发生在后台 loop、`open_position` 去 await 它」时才需要。成交在 `create_order` 内同步算完后，它是普通函数返回——**无外部事件可 await，无 Future、无超时、无 yield，也无 conditional 事件需抑制（压根不产生）**。47s 超时离群来自 tick 间隙，同步取价后该失败模式消失。

> 该协调机制属于 **OKX 实盘路径**（成交由交易所 WS 异步确认，deferred），不属于 sim。

### 5.5 保留不动

`_pending_orders` + `_process_tick` 触发 + `_matching_loop` + FillEvent→conditional 触发 + 冻结保证金 —— **继续服务 limit / stop / take_profit**（§6），及 liquidation 检查。

### 5.6 下游数据语义变化（记档）

- **G4a — order_filled 将带 `cycle_id`**：现市价 order_filled 由后台 handler 记录、`cycle_id` 为 NULL（实测 sim #15 84/84 全 NULL）；改走工具层后会带发起开仓的那个 warm cycle 的 `deps.cycle_id`。**无破坏论证**：fill→发起 cycle 的关联**已由** `v_order_lifecycle.originated_cycle_id`（`views.py:174-179`）经 intent action（open/close/limit/set_sl/set_tp 的 cycle_id 按 order_id）提供，且该子查询**显式排除 `order_filled`**——故 order_filled.cycle_id 由 NULL→非 NULL **不影响**该既有关联，也无任何查询依赖"cycle_id 为 NULL"（`_sim_metrics.py:626` 按 `action!='order_filled'` 过滤，非按 NULL）。即此变化**低风险且与既有 originated_cycle_id 机制重叠**（非新增收益），仅记档。
- **G4b — "conditional == order_filled" 恒等式失效（仅分析 caveat）**：§1.3 用的「84 conditional == 84 order_filled」恒等式改后对市价单失效（市价 fill 不再产 conditional）。**经核实这不是脚本 bug**——`analyze_sim.py`/`diff_sim.py` 不引用 order_filled，`_sim_metrics` 按 `order_id` JOIN（非 conditional），真正依赖是 order_id + 账本（即 G3，已在 §5.1 处理）。故此处仅作**未来 ad-hoc 分析的口径提醒**：勿再以 conditional cycle 数代理市价成交数。
- **order_filled 消费方全枚举（确认 G4a/G4b 对全部消费方成立）**：除 `_sim_metrics.py` 外，另一消费方是 `src/services/metrics.py`（`:114` 按 `action=='order_filled'` 取行 + `:251` 汇总 `fee`）——按 action 名选行、用 created_at/fee，**不碰 `cycle_id` 也不碰 `conditional`**（grep 零命中）。故 G4a（cycle_id 转非 NULL）与 G4b（相关性失效）对**所有已知消费方**均无功能影响。

## 6. 平仓与非市价单

### 6.1 市价平仓：对称同步（低价值收尾）

开仓与平仓**共用** `create_order` 的 market 分支（`is_close` 区分），同步化市价开仓时平仓**顺带**同步——sim 层近乎免费。同步平仓把「平仓 + 撤 OCO + 清告警」放进同一 `_lock` 原子完成。

> **撤 OCO 的真实理由（勿写成「消除触发竞态」）**：无仓时孤儿 SL/TP **不会被触发**（`_process_tick` step-2 守卫 `if not self._has_position: continue`，`simulated.py:690`），今天即如此 → 不存在「平仓在途仍挂 SL/TP 触发」竞态。真实理由有二：① 撤孤儿单的 `_cancel_orphaned_orders` 被 `if triggered or all_resolved:`（`simulated.py:705`）gating，同步平仓绕开 `_process_tick`，若后续 tick 无其它订单活动则孤儿单滞留 `_pending_orders` → 成幽灵 open 单（`fetch_open_orders` `simulated.py:767` + DB 恒 open）；② **更严重**：在「平 → 同 cycle 反向开仓」（§6.3 flip）后，旧仓 orphan stop 滞留 + 新仓使 `_has_position` 复 True → `_should_trigger`/`_execute_fill` 不校验订单归属哪个仓 → 旧 long-stop 误平新 short 仓。故同步平仓**必须显式撤 OCO**。

agent-loop 层收益比开仓**软**：

| | 开仓 | 平仓 |
|---|---|---|
| 两段式性质 | **硬强制**（set SL/TP 需仓位存在） | **软信息**（送 realized PnL + 触发反思） |
| 后置动作 | 机械重放已定计划（冗余） | 重新评估/再进场——真·新决策（不冗余） |
| 风险紧迫性 | 有无保护窗口 | 平仓是**减**风险，无紧迫性 |

设计：同步平仓返回 realized PnL + **抑制其 conditional 触发**。agent 可在同 warm cycle 反思，也**可选**「平完设个 wake、稍后再评」（同步不强迫立刻再进场，避免过度交易）。DB 仍记 `order_filled`(含 pnl)，仅不触发 cycle，无数据丢失。

**G1 — full-close 善后须显式复刻「两套独立机制」（勿混为一谈）**：同步平仓绕开 `_process_tick` + `_dispatch_fill_event`，下面两件善后都要在同步路径里显式做，且来自**不同**函数：

| 要做的事 | 真实源函数 | 现触达路径 | 同步平仓绕开后 |
|---|---|---|---|
| 撤孤儿 SL/TP（OCO）单 | `_cancel_orphaned_orders`（`simulated.py:485`，内存）+ `_persist_state` step 3b（`simulated.py:975-994`，DB 置 cancelled） | `_process_tick` step 3（`if triggered or all_resolved:` gating） | 不自愈（gating 不命中即滞留），**必须显式撤** |
| 清 price-level 告警 | `_clear_stale_alerts_for_full_close`（`base.py:328`，**只清告警、不撤单**） | 仅 `_dispatch_fill_event`（`base.py:325`） | 无 fallback，**必须显式清** |

关键：`_clear_stale_alerts_for_full_close` **不撤任何订单**——把撤单写成「复刻 `_clear_stale_alerts_for_full_close` 语义」会漏掉 `_cancel_orphaned_orders`。sim #15 有 252 个 add_price_level_alert，告警暴露面真实。两件都进 §9 测试矩阵。

**G9 — `register_close_order_entry` 对 sim 同步平仓变冗余（非阻塞清理）**：`_fill_market_close`（`simulated.py:390`）已在平仓时直接 `captured_entry = pos.entry_price` 并写入 FillEvent（`:412`），故 `register_close_order_entry`（`tools_execution.py:154/195/234/645`）对 **sim 同步市价平仓**不再必要（OKX 异步路径仍需——届时仓位已不在，须预先缓存 entry）。属顺手清理项，不阻塞。

**标注**：平仓属低价值、低风险（sim #15 仅 4 例）的一致性收尾；若需收紧 scope，它是第一个可缓的子项。

### 6.2 非市价单（limit / stop / take_profit）：不变更边界

这些是**真正延迟**的订单（未来不确定时点触发），必须保持异步——改动**外科式地只摘除 market 分支**，对它们保留：

- `_pending_orders` 排队、`_process_tick` 触发撮合、`_matching_loop`、FillEvent → **conditional 触发**（限价成交了 agent 当然要被唤醒——不可预测事件，正是 conditional cycle 该存在的场景）；
- **冻结保证金**（挂单期间资金确实被占用，对限价/stop/tp 正确），只从 market 分支删除。

**设计自洽点**：限价**开仓**仍走「成交后在 conditional cycle 设 SL/TP」——因限价 fill 不可预测，agent 必须等真成交才知有无仓位可挂。即非对称是**对的**：

> **market = 可预测 = 同步**；**limit / stop / tp = 不可预测 = 异步**。

**agent 心智**：`open_position`(market) 同步返回成交 vs `place_limit_order` 返回「已挂、稍后成交」——docstring 须讲清区别（market 即时成交并回 fill / limit 挂单等待、成交后另行通知），避免 agent 误以为限价同步成交。

### 6.3 副作用：同 cycle flip（平 → 反向开，记档）

同步平仓即时弹出仓位、同步开仓即时建仓 → agent 可在**一个 cycle 内**先平多再反向开空（今天因平仓异步、仓位下个 tick 才弹出，反向开仓会撞 `_fill_market_open:318` 反向冲突守卫，需两个 cycle）。这是同步化的**正面**行为变化，非新增目标。

记档两点：① 未来 sim review 见到「同 cycle flip」模式勿误判为 bug；② flip 把 §6.1 G1 的孤儿单善后从「整洁性」升级为「**正确性**」——旧仓 orphan SL/TP 若未在平仓时显式撤除，新反向仓使 `_has_position` 复 True，旧 stop 可误平新仓（详见 §6.1 G1 表后说明）。故 flip 与「显式撤 OCO」必须同 iter 落地。

## 7. 异常处理与失败语义

### 7.1 真实交易铁律（设计标尺）

1. **永不持裸仓**：有成交必有止损；保护单挂不上 → 重试，再不行 → 市价平掉。
2. **永不假设订单状态，向交易所对账**（唯一真相源）。
3. **失败要响不静默**：把真实状态捅给决策者（agent），必要时触发 cycle。
4. **幂等、不重复下单**：超时/报错重试前先确认上一单是否已成。
5. **风险侧/收益侧不对称**：SL 挂不上 = 紧急（重试/平仓）；TP 挂不上 = 告知即可。

> **执行者归属（避免 §7.1↔§7.3 误读）**：以上是真实交易的**标尺**，不是"系统自动执行"的承诺。本设计（方案 A）里，rule 1 的"重试/平仓"由 **agent 在场决定与执行**（它当场收到失败回执），**系统不自主平仓**——具体见 §7.3。标尺约束的是"必须有人负责到裸仓被消除"，不规定该人是系统。

### 7.2 总不变量

`open_position` 同步返回时必须落在三个**已知**状态之一并明示：✅ **Filled+Protected** / ✅ **Flat**（没开成/没成交/被平） / ⚠️ **Working·Unknown→已对账并触发 cycle**。**绝不允许**第四种「仓开着、无保护、无人知」。

### 7.3 拆开（方案 A）后异常处理塌缩（sim）

同步化删除冻结中间态（§5.2）后，多数异常自然消失：

- `open_position` 失败（保证金不足 / 无 ticker / API 报错）→ 余额检查（`create_order:248-251`）、无 ticker（`:230-231` 直接 raise）等都在结算前；结算在同一 `_lock` 内**原子**，要么整笔成、要么没发生，**无半成品、无 orphan frozen margin**。如实告知，agent 重试。
- **反向持仓冲突 / 杠杆不匹配（🟡-1，须改 explicit reject）**：`_fill_market_open`（`simulated.py:318-332`）现对这两种情形 **silent log+cancel→返回 None**（agent 只能事后从状态推断）。同步路径下，开仓结果当场返回 agent → 这两条应按**工具设计原则 1 改为 explicit reject**（明确告知"反向仓冲突/杠杆不匹配，未开仓"），而非静默撤单。§7.3"原子无半成品"覆盖资金面，此条补行为面。
- `open_position` 成功 → 返回**显式标红「仓位已开、尚未保护，立即设 SL」**。
- `set_stop_loss / set_take_profit` 失败 → agent **当场**收到失败回执，自行决定 retry / `close_position` / 改价。**系统不自动平仓**（agent 在场即风控决策者，CLAUDE.md 原则 8）。
  - **sim 可达性（G6）**：`_create_conditional_order`（`simulated.py:465`）**仅在无仓时** raise；同步开仓成功后仓位已存在 → 该 SL 失败路径在 **sim 实质不可达**。它主要是 **OKX 实盘**关切（交易所拒单/即触发），本 iter 标为**实盘-deferred**，sim 侧无需为它写补救逻辑（保留回执如实即可）。
- 成交价已接近/穿过计划止损位（快市滑点）→ 同步回执给真实 fill，**由 agent 当场决定**平不平。系统不替它做激进动作。

### 7.4 仍需保留的安全网

1. 启动**对账**：发现「有仓但无对应保护单」→ 触发 cycle 叫醒 agent。
2. 可选软网：cycle 结束时若仓位裸奔 → 告警/触发一次。
3. `_fill_market_open/close` 返回 None 的真实情形（**非无流动性**）= 反向冲突 / 杠杆不匹配（开）或仓位已不在（平）；同步路径下即「未开/平成、Flat」，按 §7.3 改 explicit reject 如实返回，无需 pending 善后。（无 ticker 是 `create_order:230-231` 直接 raise，不走 None。）

### 7.5 OKX 实盘（deferred，仅记边界）

实盘成交由 WS 异步确认，§5.4 的 sync-wait 协调机制（Future/超时/对账/不重复下单）属于该路径；本 iter 不实现，仅在 BaseExchange 接口形态上不阻断未来接入。

## 8. 已知保真 caveat（按 sim-fidelity 纪律记档）

- 同步取价用缓存 `_latest_ticker`：成交价为「下单那一刻的最近 tick」，非走单簿深度撮合（与现状一致，sim 本就不建模深度滑点）。
- sim 近零滑点会让「快市滑点 → agent 当场调整」路径（§7.3）低频触发；真实环境该路径更常用。
- 秒级无保护窗口（A 方案）比真实 bracket 略大、但 << 现状；真实 bracket 亦有 ack 延迟，止损挡不住跳空——本设计不追求 0 窗口。

## 9. 测试策略

- **sim 同步结算**：市价开/平仓在 `create_order` 内即完成（仓位/余额/fill_price/fee/pnl 正确），不依赖 `_process_tick`；无 pending 残留；无 frozen margin 残留。
- **返回契约（G2）**：市价 `create_order` 返回的 fill-result 含 `entry_price`/`pnl`（平仓），足以让工具层渲染 round-trip net 而无需反推。
- **账本持久化（G3）**：同步市价开/平仓后，`sim_orders` 立即有一行 `status="closed"`（filled_price/fee/filled_at 齐全）；`fetch_closed_orders` 与 `_sim_metrics` PnL 口径在不经 `_process_tick` 的情况下仍正确。
- **全平善后（G1，两套机制分别断言）**：同步**全平**一笔仓位后——① 该 symbol 的孤儿 SL/TP 单从 `_pending_orders` 移除**且** DB 置 cancelled（`_cancel_orphaned_orders` + `_persist_state` 3b 语义，**不**靠 `_process_tick` gating）；② 该 symbol 的 price-level 告警被清（`_clear_stale_alerts_for_full_close` 语义）。补一例：全平后**同 cycle 反向开仓**，旧孤儿单不得误平新仓（防 §6.3 flip 误平）。
- **异常**：余额不足/无 ticker 在结算前 reject 且状态不变；反向冲突/杠杆不匹配走 **explicit reject**（不静默 cancel）；`set_stop_loss` 失败回执可被 agent 感知（sim 近不可达，按实盘语义测）。
- **order_id 端到端链路（🟡-3）**：同步后断 `SimOrder.order_id == 返回 FillEvent.order_id == order_filled action.order_id == open_position intent action.order_id`，且 `v_order_lifecycle.originated_cycle_id` 能解析到发起 cycle——否则 view 与 `_sim_metrics` 的 JOIN 断。
- **回归（关键）**：limit / stop / take_profit 的现有异步 pending + 触发 + 冻结测试**全部保持绿**（摘 market 分支不得误伤共享机器）。
- **触发**：市价开/平仓**不**产生 conditional cycle；limit/stop/tp 成交**仍**触发 conditional cycle。
- **真实 fixture**：critical 路径至少一条真实 exchange fixture（per `project_iter2_mock_fidelity_lesson`）。
- **LLM 通道 drift-guard（扩，见 §10）**：`open_position` **和 `close_position`** 的 wrapper docstring + **persona** 文本变更后，断 `tool_def.description` 与 persona 渲染（LLM 实见通道，per `project_tool_docstring_llm_channel` / `project_griffe_example_section_stripped`）；并断 persona 中**不再含**"do not attempt in the same cycle"类与新设计冲突的旧措辞。

## 10. Agent-facing 契约改写（LLM 可见通道，🔴 必改面）

本 iter 的本质是改变 agent「开仓+设 SL/TP」的 cycle 行为，故 **persona + wrapper docstring 是必改面**（不止文档卫生——`persona.py:105` 与新设计**正面冲突**，会给 agent 矛盾指令，且 persona 权重 > 工具回执）。改的是**文本/docstring**，`open_position`/`close_position` **签名仍不变**（§4）。统一原则：**market = 同步、同 cycle 设 SL/TP；limit = 异步、fill 通知后设**。

| 处 | 现状（旧心智） | 改写方向 |
|---|---|---|
| `persona.py:105` Fill timing | "…only after receiving fill confirmation — do not attempt in the same cycle" | 区分两路：**市价单成交同步返回，应在同一 cycle 内紧接着设 SL/TP**；**限价单**成交是后续异步通知，届时再设。删除对市价的"separate trigger / 同 cycle 禁设"。 |
| `persona.py:106` Open fill response | "When woken by an order fill notification (conditional trigger)…set them. Use market data to inform these levels" | **scope 到限价开仓**（市价开仓不再有 conditional 唤醒）；对市价路径删除"Use market data to inform"（正是要消灭的冗余重新感知指令源——市价开仓时 thesis 已新鲜，直接用开仓时已定的 SL/TP）。 |
| `persona.py:107` Close fill response | "…(stop loss, take profit, or manual close), review the trade outcome…" | 区分：**SL/TP 触发平仓仍异步**（被唤醒时反思）；**手动市价平仓**的反思现落在**同一 warm cycle**（同步回执已带 realized PnL）。 |
| `trader.py:498-503` open_position docstring | "fill notification when execution completes (separate trigger, not in the same cycle). Stop loss and take profit…require the fill notification" | 改为"市价单**同步成交并返回真实 fill**；仓位即刻存在，**在同一 cycle 内设 SL/TP**"。 |
| `trader.py:519-522` close_position docstring | "fill notification when execution completes (separate trigger)" | 改为"市价平仓**同步成交并返回 realized PnL**"。 |

drift-guard：§9 已扩到 persona + close_position，并断 persona 不再含"do not attempt in the same cycle"类冲突措辞。

## 11. 已定决策（plan 依据）

1. **取价**：用缓存 `_latest_ticker`（§5.3 选 A）。陈旧度 ≤ 一个 watch_ticker 间隔，可忽略。
2. **市价平仓**：纳入本 iter 对称同步（§6.1）；与「显式撤 OCO」（§6.1 G1）+ flip 记档（§6.3）同 iter 落地。
3. **裸仓安全网**：本 iter 只做**启动对账**（§7.4-1：发现有仓无保护单 → 触发 cycle 叫醒 agent）；「cycle 结束裸仓软告警」（§7.4-2）降为**后续候选**，不在本 iter scope。
