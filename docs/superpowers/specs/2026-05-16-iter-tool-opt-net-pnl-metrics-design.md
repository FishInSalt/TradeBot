# Iter tool-opt-net-pnl-metrics — PnL metrics 净值化重构

**Date**: 2026-05-16
**Iteration**: iter-tool-opt-net-pnl-metrics
**Status**: design (spec)
**Upstream**: `.working/sim8-w2-fee-and-manual-close.md` §6; fee_visibility iter (PR #56, `e7f7e78`) landed
**Anchor 原则**: 3（信号唯一权威来源）/ 4（信号补齐优先于新工具）/ 7（输出与命名表达友好）+ 元（实证优先于直觉）

---

## 0. 摘要

`.working/sim8-w2-fee-and-manual-close.md` §6 全代码库 PnL 计算审计后浮出 3 处 Level A 数字偏离行业标准（OKX/Binance/Bybit perp 实际显示）：

1. **A1: max_drawdown 缺 fees** — `src/services/metrics.py:90-100` `equity = initial + Σ gross_pnl`；行业标准 `equity = initial + Σ pnl − Σ fees`。sim #8 fee 占失血 77.4%，gross MDD 严重低估真实回撤。
2. **A2: profit_factor 用 gross** — `src/services/metrics.py:115` `gross_profit / gross_loss`；行业标准 `(gross_profit − winners_fees) / (gross_loss + losers_fees)`。sim #8 gross PF ≈ 1.34（误判正期望）vs net PF ≈ 0.45（实际负期望）—— 策略评估字段越线。
3. **A3: pnl_pct 同名跨分母** — `src/agent/tools_perception.py:291` 用 `initial_balance`（ROI on capital）；`src/services/cycle_capture.py:124` 用 `notional`（ROI on notional）。同字段名两套语义 → 跨视角对比失真。

并同时处理 Level C convention（gross vs net 双视角对 win_rate / avg / best/worst —— 行业两种都用）。

本 iter 通过 **FIFO lot pairing 算法**（与 `scripts/_sim_metrics.collect_roundtrips` 同源）在 `MetricsService.compute()` 内部从 `trade_actions` 表重建 lot queue 实现 per-trade net pnl，并在 4 个层级（runtime metrics / cycle snapshot / DB view / post-session analytics）统一 gross + net 双视角输出。

**关键 schema 变更**：
- `trade_actions` 表加 2 个 nullable 列：`entry_price`（close fill 时的 position weighted-avg entry）+ `amount`（fill 的成交量）
- 字段来源前置：`FillEvent.amount` 一直存在（base.py:344 必填），`FillEvent.entry_price` 由 fee_visibility iter PR #56 引入（base.py:349，close fill 时由 cli renderer 消费）。**两者当前都未持久化**——本 iter 通过修改 `_record_action_from_fill` 把这两个值写入 `trade_actions`
- 1 新 alembic migration（add 2 columns + drop/recreate `v_cycle_metrics` view with renamed JSON path）

**Per-lot-pair trade 语义切换**（design 后果，非 bug）：
- 现有 `MetricsService.compute()` 用 `pnls = [f.pnl for close_fill]`（一笔 close fill = 一个 trade）
- 新 FIFO 算法用 `roundtrips = [Roundtrip per (lot, close) pair]`（一笔 close 跨 N 个 open lot → N 个 trades）
- 影响：含 partial close 或多次加仓后单次平仓的 session，gross 系列（total_trades / win_rate 分母 / avg_win/loss / best/worst_trade）和 net 系列**都**改变粒度
- 与行业 perp futures convention 对齐（"一笔交易 = 一个 lot open-close 周期"），同向 PnL 正确性提升
- §1 / §C10 注：既有断言（per-close-fill 视角）在加仓 session 上会震荡；fixture 重审必须

surface delta: 工具数量 **不变**；BaseExchange 接口 **不变**；FillEvent schema **不变**；`sessions.fee_rate` schema **不变**；新增 1 alembic migration + 2 nullable columns + view DDL update。

不在 scope（独立议题）：
- funding fee 模拟 / 累计（与 OKX 实盘准备期议题同期）
- OKX maker rebate / fee 负值（fee_visibility iter 已锁定正数，maker/taker mix 实盘准备期）
- contract_size multiplier 显式化（G-calc audit 系列）
- mark vs last 一致化（memory `project_okx_demo_mark_vs_last_drift`）
- **OKX `_close_order_entry_cache` miss 时 API fallback 反查 entry_price** — 沿用 fee_visibility iter limitation；缺 entry_price 的 close fill 走 skip-with-caveat；彻底解由 `iter-tool-opt-okx-fee-rate-auto-fetch` follow-up 同期处理

---

## 1. 现状证据 — sim #8 W2 实证

> Source SID: `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`（178 cycles / 19.2h / 1818 tool calls / BTC/USDT:USDT；fee_rate=0.001）

| 实证项 | 数据 | 说明 |
|---|---|---|
| Fee 失血占比 | gross PnL −81.10 / total fees 277.55 / net −358.65 → **fee 占总亏损 77.4%** | trade_actions SUM(fee) / SUM(pnl) |
| Gross profit_factor vs Net | gross PF ≈ 1.34（正期望）vs net PF ≈ 0.45（负期望）—— 跨越 1.0 边界（档案 §6.1 已计算）| trade_actions analysis |
| Gross win_rate vs Net | 估计 gross win_rate ~53% vs net win_rate ~40%（fee 翻转部分 win→loss）—— **量级 estimate；plan-phase probe 跑 ground truth** | 同上 |
| Gross MDD vs Net | gross MDD 显著低估 net MDD（fee 占失血 77.4%）—— **量级 estimate；plan-phase probe 跑 ground truth** | equity series 反算 |
| pnl_pct convention drift | `tools_perception.py:291` 用 initial_balance / `cycle_capture.py:124` 用 notional | grep cross-tool |
| 数学恒等（sim 路径，per fee_visibility iter §1.5）| `entry_price × contracts × fee_rate ≡ Σ(individual fill fees)` 仅在 sim + 同 fee_rate 全程稳定时成立 | Trade #1: 81878.6 × 0.366 × 0.001 = 29.96756 vs actual 29.9676 |

**关键 insight**：
- fee 占总亏损 77.4% 量级，gross/net 跨越 profit_factor=1 边界 → 策略评估字段直接误导 agent
- FIFO lot pairing 算法对 sim/OKX 通用（不依赖 sim 数学恒等假设）；OKX 的 entry_price 走 `_close_order_entry_cache`（继承 fee_visibility iter limitation）
- pnl_pct 双语义混用是潜伏 bug：agent get_position 看 ROI on capital，DB 分析看 ROI on notional → 复盘失真

---

## 2. 设计决策

| 决策点 | 选项 | 选择 | 理由 |
|---|---|---|---|
| Scope 深度 | A-only / A+C / A+C+替换 | **A+C 完整化** | 与档案 §6.4 路径一致；trade-level net 视角支持 Level C 字段下沉 |
| Fee 归集算法 | Per-fill proportional / FIFO lot pairing / DB FIFO lot 新表 | **FIFO lot pairing**（与 scripts/_sim_metrics 同算法）| sim/OKX 通用；与 scripts 算法 byte-equal 可达；partial close / 多次加仓自然处理；不依赖 sim 数学恒等假设 |
| FIFO 数据通路 | 复用 sim_orders / 复用 trade_actions（需加列）/ 新表 | **trade_actions + 加 entry_price + amount 列** | sim/OKX 共用 `_record_action_from_fill` 写入路径；OKX 无 sim_orders 表；最小 schema 变更覆盖两条 path |
| 输出形态 | 并列双视角 / Net 主+gross 副 / 独立 section | **并列双视角** | agent 一眼对比 fee impact；fact-provider 不评判优先级 |
| pnl_pct 处理 | 保留两套 explicit rename / 统一 capital / 统一 notional / 三视角并列 | **保留两套 + explicit rename** | 两个语义各自唯一来源（原则 3）；不丢信息 |
| MDD 处理 | 仅 net / 并列双视角 | **仅 net (替换)** | 业界 MDD 默认含 fees；gross MDD 业务价值低 |
| Scripts 同步 | 同步切 net / 加 gross 视角对齐 src | **加 gross 视角**（scripts 当前已是 net；加 gross 并列）| scripts/_sim_metrics 当前 PF/win_rate 已用 FIFO `pnl_net`；MDD 走 broker equity 路径（`state_snapshot.balance.total_usdt`）与 FIFO 异源但同向 net。本 iter 加 gross 视角让 scripts 与 src 输出对齐 |
| Zero-denom 约定 | src inf 沿用 / scripts None 沿用 / 统一一种 | **统一为 None**（scripts 现行）| src 现有 `gross_loss > 0 else float("inf")` 与 scripts `wins==0 or losses==0 → None` 语义不一致；统一更鲜明：None=undefined ratio。src `metrics.py:115` 改为 None；`tools_perception.py:743` 渲染逻辑同步 `metrics.profit_factor is None` → "N/A" |
| Trade 单位语义 | per-close-fill（现行）/ per-lot-pair（FIFO 自然产物）| **per-lot-pair** | FIFO 自然单元；与行业 perp convention（每个 lot open-close 周期 = 1 trade）对齐；partial close / 加仓场景统计粒度更精确。代价：既有 per-close-fill 断言震荡（§C10 fixture 重审） |
| fee_rate 注入路径 | TradingDeps 注入 / `SELECT FROM sessions` 自洽 | **`SELECT FROM sessions`** | 与 scripts/_sim_metrics `_fetch_fee_rate` 同 pattern；MetricsService 自洽不依赖 TradingDeps 字段（便于跨 session forensic 复用）；不改 `compute()` signature；运行时一次 query 成本可忽略 |
| `_close_order_entry_cache` miss fallback | 本 iter 实现 OKX API 反查 / 留 follow-up | **留 follow-up** + 本 iter skip-with-caveat | 与 fee_visibility iter §7 follow-up（`iter-tool-opt-okx-fee-rate-auto-fetch`）同期处理；本 iter 主线已 schema 变更 |
| Backward compat 旧数据 | 接受 NULL / COALESCE 兼容 / backfill | **接受 NULL + skip-with-caveat** | fee_visibility 之前 close fill rows entry_price=NULL；CLAUDE.md "Don't add backwards-compatibility shims"；caveat 文案精确为 "based on rows after net-metrics iter landed" |
| sessions.fee_rate IS NULL 旧数据 | fail-loud / 0-fallback / skip net | **0-fallback + warning log** | fee_visibility 之前 sim sessions 可能 NULL；0-fallback 等价于 "fee_rate 未知 → 假设 0" → net 数字降级为 close_fee-only 视角 + caveat "fee_rate unknown for this session, net excludes entry fees" |
| OKX session 检测方式 | isinstance / `.name` 属性 | **isinstance(deps.exchange, OKXExchange)** | 类型清晰；BaseExchange 无 `.name` 属性，加 attribute 违反 "interface 不变" 承诺 |
| Migration 风险 | add column / 重构旧表 | **add 2 nullable columns** | SQLite ALTER TABLE add nullable column 无锁表；零 backfill；W3R1 已过 Path 1 alembic upgrade，migration 接新 head 单步可达 |

---

## 3. Architecture — FIFO lot pairing 贯穿 4 层

```
[trade_actions DB row (after schema upgrade)]      [in-tool runtime]               [DB persist / view]                  [post-session analytics]
 open fill:                                          ↓                              ↓                                    ↓
   action='order_filled', pnl=None,              MetricsService.compute()       cycle_capture snapshot           scripts/_sim_metrics.py
   fee=F_o, amount=A_o, price=E_o                    ↓                              ↓                                    ↓
   side='buy'/'sell', entry_price=NULL          collect_roundtrips()           pnl_pct_of_notional             collect_roundtrips() (existing)
                                                  (FIFO lot queue                                                + 加 gross 视角输出
 close fill:                                       重建 from trade_actions)
   action='order_filled', pnl=P_g,                   ↓
   fee=F_c, amount=A_c, price=X,               Roundtrip dataclass
   side='sell'/'buy',                             (gross + net per pair)
   entry_price=E_close (NEW)                         ↓
                                              get_performance() 并列双视角
```

**核心 invariant**：

对每个 `(open_lot, close_fill)` 配对 (FIFO consumed)：
```
consumed = min(lot.remaining_amount, close_remaining)
fee_open_share  = lot.open_fee × (consumed / lot.original_amount)
fee_close_share = close_fill.fee × (consumed / close_fill.amount)
pnl_gross = (close_fill.price − lot.entry_px) × consumed × sign(side)
pnl_net   = pnl_gross − fee_open_share − fee_close_share
```

**算法源**：复用 `scripts/_sim_metrics.collect_roundtrips()` (lines 165-274)；在 `MetricsService` 内部以同等逻辑从 `trade_actions` 重建（不依赖 `sim_orders`）。

**OKX 路径**：
- `_record_action_from_fill` (CLI 层) 同写 `entry_price + amount` from `FillEvent`
- OKX FillEvent.entry_price 来自 `_close_order_entry_cache` (in-memory submit-time hook)
- cache hit → 数据完整；cache miss → entry_price=NULL → 该 close fill skip net 计算 + caveat
- cache miss fallback 由 `iter-tool-opt-okx-fee-rate-auto-fetch` follow-up 处理（不在本 iter）

**Fee_rate 注入**：
- `MetricsService` 通过 `SELECT fee_rate FROM sessions WHERE id = :sid` 自洽获取（与 scripts/_sim_metrics 同 pattern；不改 `compute()` signature）
- NULL fee_rate → 0-fallback + warning log（per §6.1）

---

## 4. Components — 变更清单

| # | 文件 | 改动 |
|---|---|---|
| **C0** | `alembic/versions/<new>.py` | **NEW migration**: add `trade_actions.entry_price (Float, nullable)` + `trade_actions.amount (Float, nullable)`；drop+recreate `v_cycle_metrics` view with renamed JSON path `pnl_pct_of_notional` |
| C1 | `src/storage/models.py` | `TradeAction` 加 2 字段：`entry_price: Mapped[float | None]` / `amount: Mapped[float | None]`。**Docstring 限定（两字段范围不同）**：<br>• `amount`：所有 `action='order_filled'` 行（open + close）都有值（per FillEvent.amount 必填）；非 fill 行（cancel / submit 等由 `tools_execution.py:_record_action` 写）NULL by design<br>• `entry_price`：open fill 行永远 NULL（per FillEvent.entry_price 设计 "open fill 永远 None"，base.py:349-360）；close fill 行通常有值，**OKX cache miss 时可 NULL**（继承 fee_visibility iter limitation，详见 §6.5）；非 fill 行 NULL by design |
| C2 | `src/cli/app.py` | `_record_action_from_fill` 写入 `entry_price=event.entry_price, amount=event.amount`。**不改** `src/agent/tools_execution.py:_record_action`（非 fill 行不需要 amount/entry_price）|
| C3 | `src/services/metrics.py` | `compute()` 重构：内部实现 FIFO lot pairing（从 `trade_actions` 重建）；`PerformanceMetrics` dataclass 加 7 net 字段（net_pnl / net_profit_factor / net_win_rate / avg_win_net / avg_loss_net / best_trade_net / worst_trade_net）+ 2 计数字段（net_winning_trades / net_losing_trades，因 gross/net 计数可能不同）；`max_drawdown_pct` 切 net equity；内部 `SELECT fee_rate FROM sessions` 自洽获取；`profit_factor` / `net_profit_factor` zero-denom 切 None 约定（与 scripts 对齐）；liquidation 分支按 §5.2 用 `fill.pnl / fill.amount × consumed` 反推（与 scripts 对齐）|
| C4 | `src/agent/tools_perception.py` | `get_performance` 并列双视角输出；MDD 单 net；OKX session caveat footnote（`isinstance(deps.exchange, OKXExchange)` —— 需新增 `from src.integrations.exchange.okx import OKXExchange` import，trivial 无循环依赖风险）；`get_position` 变量 `pnl_pct_inner` → `pnl_pct_of_capital`（输出文案 `% of initial capital` 已 explicit 保留）|
| C5 | `src/services/cycle_capture.py` | snapshot JSON field rename `pnl_pct` → `pnl_pct_of_notional` |
| C6 | `src/storage/views.py` | `v_cycle_metrics.position_pnl_pct` JSON 路径 → `pnl_pct_of_notional`；view DDL 同步（与 C0 migration 保持一致；views.py 是 fresh-DB 路径，migration 是存量 DB 路径）|
| C7 | `src/cli/display.py` | line 717 `pos.get("pnl_pct")` → `pos.get("pnl_pct_of_notional")` |
| C8 | `scripts/_sim_metrics.py` | **加 gross 视角**（不是切 net）。当前状态：PF / win_rate / largest_win_loss / avg 等 Roundtrip-based metrics 已用 `pnl_net`；MDD 走 broker-equity 路径（`state_snapshot.balance.total_usdt`）独立。本 iter 在现有 `Roundtrip` dataclass 上加 gross-derived metrics（profit_factor_gross / win_rate_gross / largest_win_loss_gross etc.）|
| C9 | `scripts/analyze_sim.py` + `scripts/diff_sim.py` | 报表 column 加 gross/net 并列输出 |
| C10 | `tests/` | 新增 ~25-30 测试（FIFO 算法 / migration / OKX cache miss / fee_rate NULL fallback / pnl_pct rename / src↔scripts parity）+ 修改 ~5-8 既有断言 |

**不动**：
- BaseExchange 接口（fee_rate 通过 `SELECT FROM sessions` 自洽获取）
- FillEvent schema（entry_price + amount 字段已存在）
- sessions schema（fee_rate 已 nullable=True）
- RuntimeConfig / TradingDeps（fee_rate 注入已就位但 metrics 自洽 fetch 不依赖）
- persona.py（fee_rate 描述已由 fee_visibility iter 处理）

---

## 5. Data Flow

### 5.1 Trade_actions schema upgrade（一次性）

```
alembic upgrade head （新 migration）
   ↓
ALTER TABLE trade_actions ADD COLUMN entry_price REAL;
ALTER TABLE trade_actions ADD COLUMN amount REAL;
DROP VIEW IF EXISTS v_cycle_metrics;
CREATE VIEW v_cycle_metrics AS ... (JSON path: $.position.pnl_pct_of_notional)
   ↓
W3R1+ DB schema 包含新列；旧行 entry_price/amount = NULL
```

### 5.2 In-runtime（agent decision loop）

```
sessions.fee_rate (wizard set) — DB authoritative
   ↓
MetricsService.compute() 内部：
   SELECT fee_rate FROM sessions WHERE id = :sid   → fee_rate (may be None)
   if fee_rate is None:
       log.warning("fee_rate NULL for session %s, net metrics use 0-fallback", sid)
       fee_rate = 0.0
   ↓
SELECT * FROM trade_actions WHERE session_id=X AND action='order_filled' ORDER BY created_at
   ↓
FIFO lot queue reconstruction (trade_actions.side stores position_side per _record_action_from_fill；
长仓 close fill.side='long' 消耗 long lot 队列，短仓同理):

Naming: 本节伪代码中 `fill.side` = `trade_actions.side` = `FillEvent.position_side`（"long"/"short"），
不是 buy/sell 订单方向。判 open/close 用 `pnl IS NULL` 信号，不用 side。

Note on entry_price: FIFO 算法 pnl_gross 仅用 lot.entry_px（来自 open fill 的 fill.price），
**不使用** close fill 的 entry_price。close.entry_price 在 DB 仍有用（cli renderer / audit），
但对本算法是"数据质量指示器"非"算法输入"。Skip 条件因此简化为仅检查 fill.amount。

Note on liquidation: scripts FIFO 对 liquidation fill 用 `pnl_gross = (fill.pnl / fill.amount) × consumed`
反推（吸收 sim pnl_cap 截断；scripts/_sim_metrics.py:215-225,238-241）。src FIFO 必须同步处理
避免几何公式 `(fill.price − lot.entry_px) × consumed × sign` 在 sim 平仓爆仓场景偏离 trade_actions.pnl
实际记录值；检测信号是 `fill.trigger_reason == "liquidation"`（FillEvent.trigger_reason 已落 DB
per cli/app.py:427）。

   for each fill in fills:
      if open fill (pnl IS NULL):
         if fill.amount IS NULL:                    # pre-iter legacy row (migration 前)
            log.warning, caveat: legacy_open_skipped += 1
            continue                                # 不 append lot → 后续 close 自然走 invariant 路径
         lots[fill.side].append(_Lot(entry_px=fill.price, original_amount=fill.amount,
                                     remaining_amount=fill.amount, open_fee=fill.fee))
      else (close fill, pnl IS NOT NULL):
         if fill.amount IS NULL:                    # pre-iter legacy close OR severe data corruption
            log.warning, caveat: legacy_close_skipped += 1
            continue                                # 不弹 lot（不知道弹多少）；§6.9 invariant 路径覆盖后续
         if fill.entry_price IS NULL:               # OKX cache miss — algorithmic OK, data-quality flag
            caveat: okx_cache_miss_count += 1
            # 继续，不 skip
         # Liquidation: pre-compute per-unit pnl once per fill (mirrors scripts §215-225)
         liq_pnl_per_unit = None
         if fill.trigger_reason == "liquidation":
            if fill.pnl is None or fill.amount <= 0:
               caveat: invariant_violations += 1
               liq_pnl_per_unit = 0.0
            else:
               liq_pnl_per_unit = fill.pnl / fill.amount
         close_remaining = fill.amount
         while close_remaining > epsilon:
            if not lots[fill.side]:                 # invariant: close 无对应 open lot
               caveat: invariant_violations += 1
               log.error, break
            lot = lots[fill.side].peek()
            consumed = min(lot.remaining_amount, close_remaining)
            fee_open_share  = lot.open_fee × (consumed / lot.original_amount)
            fee_close_share = fill.fee × (consumed / fill.amount)
            sign = +1 if fill.side == "long" else -1
            if fill.trigger_reason == "liquidation":
               pnl_gross = liq_pnl_per_unit × consumed
            else:
               pnl_gross = (fill.price − lot.entry_px) × consumed × sign
            pnl_net   = pnl_gross − fee_open_share − fee_close_share
            roundtrips.append(Roundtrip(gross=pnl_gross, net=pnl_net, ...))
            lot.remaining_amount -= consumed
            close_remaining -= consumed
            if lot.remaining_amount <= epsilon:
               lots[fill.side].popleft()
   ↓
gross series:  gross_pnls = [rt.pnl_gross for rt in roundtrips]
net series:    net_pnls   = [rt.pnl_net   for rt in roundtrips]
   ↓
─── compute both sets of metrics in parallel ───
gross: total_pnl, win_rate, profit_factor, avg_win, avg_loss, best, worst
net:   net_pnl, net_win_rate, net_profit_factor, avg_win_net, avg_loss_net, best_net, worst_net
MDD:   max_drawdown_pct = peak-relative on net equity (initial_balance + Σ net_pnls)
   ↓
PerformanceMetrics dataclass → get_performance renders 并列双视角 string
caveat output (Trade Stats section 顶；两类 caveat 各自独立，可同时出现)：
   - 若 legacy_open_skipped + legacy_close_skipped > 0：
       "Note: net stats based on m/n trades (k legacy rows skipped — pre-net-metrics-iter data)."
   - 若 okx_cache_miss_count > 0：
       "Note: k close fills had cache-miss entry_price (FIFO unaffected; audit trail incomplete for those trades)."
   - 若 m=0（无 valid roundtrips）：
       "Net stats unavailable: all close fills are pre-net-metrics-iter legacy data."
```

### 5.3 Cycle snapshot（DB persistence）

```
cycle_capture.capture_state(...) → fetch_positions
   ↓
notional = entry_price × contracts
pnl_pct_of_notional = unrealized_pnl / notional × 100
   ↓
state_snapshot JSON: { "position": { ..., "pnl_pct_of_notional": -1.94 } }
   ↓
v_cycle_metrics view (via migration): position_pnl_pct = json_extract($.position.pnl_pct_of_notional)
   ↓
cli/display.py:717 reads pos["pnl_pct_of_notional"]
scripts/analyze_sim.py reads via view
```

### 5.4 Post-session analytics

```
scripts/_sim_metrics.py：FIFO 算法保留（既有 sim_orders 来源）
   ↓
新增 gross 视角 metric functions（profit_factor_gross / win_rate_gross / ...）
   ↓
scripts/analyze_sim.py / diff_sim.py 报表渲染 gross + net 并列
```

### 5.5 Sign conventions（沿用现有）

| 字段 | 符号 |
|---|---|
| `fee` | 正数（cost；fee_visibility iter 锁定） |
| `pnl` / `pnl_gross` / `pnl_net` | 正赚负亏 |
| `avg_loss / avg_loss_net` | 负数（沿用 metrics.py 现有约定）|
| `max_drawdown_pct` | 正数，渲染时加 `-` 前缀 |
| `entry_price` | 正数（无符号语义） |
| `amount` | 正数（无符号语义；side/position_side 表方向） |

---

## 6. Error Handling & Edge Cases

### 6.1 sessions.fee_rate IS NULL（旧 session）

- 触发：fee_visibility iter 之前创建的 sim/OKX session（schema nullable=True 历史遗留）
- 处理：`MetricsService.compute()` 取 fee_rate 时 NULL → log.warning + 0-fallback
- 后果：该 session 的 net 数字等价于 `gross − close_fee`（无 entry fee 项），但因 entry_price 同样 NULL（pre-iter 老数据）实际走 §6.2 全 skip 路径
- 输出：caveat "fee_rate unknown for this session (legacy); net excludes entry fees"
- **不**抛异常阻塞 `get_performance`

### 6.2 trade_actions 缺 amount / entry_price（pre-iter legacy 或 OKX cache miss）

两条独立路径，区分处理：

**(a) `amount IS NULL`（pre-iter legacy 行 — migration 前所有 trade_actions 行 amount=NULL）**：
- open fill：log.warning + caveat `legacy_open_skipped` += 1；不 append lot
- close fill：log.warning + caveat `legacy_close_skipped` += 1；不弹 lot（不知道弹多少）
- 后果：pre-iter 全 NULL session 上 FIFO 空跑（无 trade 产出）；混合 session（pre-iter open + post-iter close）走 §6.9 invariant 路径
- **不**抛异常；输出 caveat：`Note: net stats based on m/n trades (k legacy rows skipped; pre-net-metrics-iter data).`

**(b) `entry_price IS NULL` 但 `amount` 完整（OKX cache miss / 罕见 pre-iter mixed）**：
- FIFO 算法不依赖 close.entry_price（用 lot.entry_px），**继续配对计算**
- caveat `okx_cache_miss_count` += 1（仅信息标记，不影响数据可用性）
- 输出 caveat：`Note: k close fills had cache-miss entry_price (FIFO unaffected; OKX VIP audit trail incomplete for those trades).`

**(c) 完全无可用数据（m=0 即所有 close 都 legacy skip）**：
- **所有 stats 字段**（gross 和 net 同等）渲染 `N/A`——FIFO 算法不产 roundtrips，gross 系列因 per-lot-pair 语义切换同样无法计算
- section 顶提示 `Stats unavailable: all close fills are pre-net-metrics-iter legacy data (forensic analysis via scripts/_sim_metrics.py from sim_orders table).`
- 这是与现有 `f.pnl` 直接累计的行为回归：legacy session 通过 `get_performance` 不再可见汇总；forensic 完整数据仍可通过 scripts 端的 sim_orders 复盘获取

设计声明：close fill 的 `entry_price` 在本 iter 是**数据质量指示器**（标记 OKX cache miss 等异常），不是 FIFO 算法输入。其算法独立性使 OKX cache miss 不再阻塞 net 计算。

### 6.3 fee_rate = 0 / entry_price = 0 / amount 边界

- `fee_rate = 0`：`fee_open_share = fee_close_share = 0` → `net = gross`（合法，user 故意关 fee 测策略）
- `entry_price = 0`：`pnl_gross = (fill.price − 0) × consumed × sign` —— 数值上 ok 但语义异常；视为 corrupt data，skip + log.error
- `amount = 0`：除零保护 → skip + log.error

### 6.4 OKX path fee_rate VIP tier 偏差（已知 caveat）

- 现状：CCXT 实际 fee 准确（来自 OKX response），但 user-input fee_rate 与 actual VIP tier 可能偏差
- 影响分析（FIFO 算法）：`fee_open_share = lot.open_fee × (consumed / lot.original_amount)` 用 **lot.open_fee（CCXT 实际值，不依赖 fee_rate）**，分摊系数 `consumed / lot.original_amount` 是 amount 比例
- 残留偏差源：`lot.original_amount` 来自 OKX echo（可能精度截断），分摊比例有 ε 误差，量级 ≤ 1e-9
- OKX session `get_performance` 输出末尾加 footnote：
  ```
  Note: OKX net metrics use exchange-echoed fees (accurate); minor ε from lot amount precision possible.
  ```
- 检测：`isinstance(deps.exchange, OKXExchange)`

### 6.5 OKX `_close_order_entry_cache` miss

- 触发：(a) OKX exchange-side 自动 SL/TP 触发但 agent 未 submit-time `register_close_order_entry`；(b) cache TTL 24h 后才 close
- 后果：FillEvent.entry_price=None → trade_actions.entry_price=NULL → 走 §6.2 (b) 路径，FIFO 算法不受影响（continue 配对），只产 informational caveat
- **本 iter 处理充分**：FIFO 解耦 close.entry_price 后，cache miss 不再阻塞 net 计算
- Audit-trail 完整性 follow-up：`iter-tool-opt-okx-fee-rate-auto-fetch`（API fallback 回填 entry_price 提升 audit trail；不影响算法）

### 6.6 OKX maker rebate（fee 负值场景）

- 当前 fee_visibility iter 已锁定 `FillEvent.fee` 始终正数；maker rebate 未来才处理
- 本 iter 不展开（与 OKX maker/taker mix 实盘准备期议题同期）
- 测试加 1 个 placeholder：FIFO 算法本身 sign-agnostic（`fee_open_share = open_fee × ratio` / `pnl_net = pnl_gross − fee_open_share − fee_close_share` 符号自然传递）
- **未定**：下游字段约定（`total_fees = sum(fee)` 汇总语义 / `losing_pnls = [p for p in pnls if p <= 0]` breakeven 归类）在 fee 负值出现时的边界行为，由 maker rebate iter plan-phase 单独 audit

### 6.7 metrics 服务不可用

- 现有 `get_performance` L3 by-design empty state ("No metrics service available.") 保留
- net 字段也不出现

### 6.8 零 close fill（n=0）

- 现有 early-return 保留；所有 net 字段 dataclass default 0.0
- 输出层 `"No completed trades yet."` 保留

### 6.9 FIFO 算法异常：close 时无对应 open lot

- 触发：DB 数据损坏 / migration 前后混合 fills
- 处理：log.error + 跳过该 close fill 的剩余 amount + caveat counter
- 输出末尾 `Invariant violation: k fill(s) had no preceding open lot.`（罕见）

### 6.10 跨 src ↔ scripts FIFO parity（drift guard）

- src `MetricsService` 与 scripts `_sim_metrics.collect_roundtrips` 都跑 FIFO，但数据源不同：
  - src: `trade_actions` 表（直读 `amount`）
  - scripts: `sim_orders` 表 + JOIN trade_actions（走 `_derive_close_amount(fill, fee_rate)` line 93-106，fee 反推优先 1% 容差内）
- **drift 风险**：当 sim_orders 历史数据存在 `stale_close_amount_count > 0` 路径（fee-derived ≠ 原 order_amount 但 ≤1.01×），scripts 用 fee-derived 值，src 用原 amount，consumed 比例与 fee_open_share 分摊会偏离
- **fixture 约束**（drift guard 可用）：synthetic fixture 构造时保证 `fill.fee = filled_price × fill.amount × fee_rate`（数学相容），即 `_derive_close_amount` 返回 `(fill.amount, True)` 不走 stale 分支；test 显式 assert `caveats["stale_close_amount_count"] == 0`
- drift guard 测试：用上述构造的 small synthetic fixture，验证两端 `roundtrips` 浮点 tolerance 1e-9 一致

**MDD 不在 parity 范围**（重要 caveat）：
- src 新 MDD = `initial_balance + Σ net_pnls` 时间序列（realized-only equity）
- scripts MDD = `agent_cycles.state_snapshot.balance.total_usdt` 时间序列（broker equity，含 unrealized 浮动）
- 两者数据源不同 + 中间态含 unrealized 差异，连"同向 byte-equal"都不能保证；仅在 flat（无持仓）切片处趋同
- drift guard 测试**不**对比 MDD 数值；§7.4 测试列表 MDD 测试只验 src 自身 algorithm 正确性

- **OKX session 不 enforce parity**：OKX 不写 sim_orders，scripts 不适用

### 6.11 Legacy state_snapshot view 列 NULL（by design）

- 触发：本 iter migration 后，`v_cycle_metrics` view DDL 读 `$.position.pnl_pct_of_notional`；但 migration 前的 `agent_cycles.state_snapshot` JSON 仍是 `$.position.pnl_pct`（无 `_of_notional` 后缀，含 W3R1 已跑的 sessions）
- 后果：legacy 行 `v_cycle_metrics.position_pnl_pct` 列返回 NULL
- 影响范围：`scripts/analyze_sim.py` 跨 session 分析时，legacy session 该列空值；live agent `get_position` 调 exchange API 实时计算 pnl_pct 不依赖 view；`cli/display.py:701-719` 读 state_snapshot dict 直接（不经 view），其 `pos.get("pnl_pct")` 在 post-migration 新 cycle 写入 `pnl_pct_of_notional` 后会 fallback 为 None → 此处也需配套改 key（已在 C7 列出）
- **不**加 COALESCE shim（per CLAUDE.md "Don't add backwards-compatibility shims"）
- 显式声明：legacy `state_snapshot` 行历史 pnl_pct 数据不进新 view 列；如需历史回查走 `json_extract(state_snapshot, '$.position.pnl_pct')` 直查（scripts ad-hoc 写法）

---

## 7. Testing

### 7.1 新增测试分布（估算 ~25-30 个）

| 类别 | # | 关键 case |
|---|---:|---|
| Migration (C0) | 3 | upgrade 加 2 列 + view drop/recreate / downgrade 移除列 / 已有数据保留 NULL |
| trade_actions 写入 (C2) | 2 | open fill 写 amount + entry_price=NULL / close fill 写 amount + entry_price |
| FIFO 算法 (C3) | 9 | 单 open / 单 close / partial close 多次 / 多 open 加仓后 close / OKX cache miss continue-with-caveat / sim 数学恒等 sanity / **liquidation fill (trigger_reason='liquidation' 用 fill.pnl/fill.amount 反推，与 scripts 对齐)** / 零 close fill / 加 close 但无对应 open lot 的 invariant 路径 |
| net MDD equity | 3 | net equity 含 fees / vs gross 对比 / 单 trade |
| net profit_factor / win_rate / avg / best/worst | 4 | sim #8 ground truth digits（synthetic fixture）/ fee 翻转 win→loss / 符号约定 / zero-denom → None（per §2 决策） |
| pnl_pct rename + view (C5/C6/C7) | 4 | cycle_capture 写入 / views.py JSON 路径 / display.py 读 / get_position 输出文案 |
| Src ↔ scripts FIFO parity (drift guard) | 2 | 小型 sim fixture 两端 roundtrips byte-equal / OKX session 不 enforce |
| OKX caveat & cache miss | 4 | OKX isinstance footnote / sim 无 footnote / cache miss continue-with-caveat (entry_price NULL, FIFO unaffected, informational caveat only) / **cache miss pnl_net 等价**：同一 fill spec 一次 entry_price 完整、一次 NULL，断言两次产出 `pnl_net` byte-equal（验证算法解耦核心 claim）|
| fee_rate NULL fallback | 2 | sessions.fee_rate IS NULL → warning + 0-fallback / fee_rate=0 user case |
| Scripts gross 视角加 (C8/C9) | 2 | scripts profit_factor_gross / win_rate_gross / analyze_sim.py 输出列 |

### 7.2 修改既有测试（~5-8 个）

- `tests/services/test_metrics.py`: 现有 gross-only assertions 保持 + net 平行 + FIFO 重构（之前 mock TradeAction 行不含 entry_price/amount → fixture 更新）
- `tests/agent/test_tools_perception_perf.py`: 输出文案 `(gross-based)` → `gross X / net Y`
- `tests/services/test_cycle_capture.py`: snapshot field name `pnl_pct` → `pnl_pct_of_notional`
- `tests/storage/test_views.py`: v_cycle_metrics JSON 路径断言
- `tests/cli/test_display.py`: read field name
- `tests/cli/test_app.py` (_record_action_from_fill): 加 entry_price + amount 字段断言

### 7.3 Fixture 重用与新建

- 复用 fee_visibility iter PR #56 的 `FillEvent.entry_price` / `amount` fixtures
- **不引用 sim #8 真实 DB**（非 repo asset，CI 跑不通）；改用小型 synthetic fixture（手构造 10-20 fills 验证 FIFO 行为）
- sim #8 ground truth 数字（PF gross 1.34 / net 0.45）作 plan-phase 实证 reference，不作 CI 断言

### 7.4 Drift guard 示例

```python
async def test_src_scripts_fifo_parity():
    """Small synthetic sim fixture: src MetricsService FIFO ↔ scripts collect_roundtrips byte-equal.

    Fixture protocol: helper double-writes sim_orders + trade_actions rows from a single fill
    spec, with fee = price × amount × fee_rate exactly (math-consistent → no stale_close_amount path).
    """
    fee_rate = 0.0005
    # Each fill spec: (event_type, position_side, price, amount)
    # Helper computes fee = price × amount × fee_rate; writes BOTH:
    #   - sim_orders row (side/position_side derived; for scripts FIFO)
    #   - trade_actions row (action='order_filled', pnl=None for open / value for close,
    #     side=position_side, entry_price=NULL for open / lot.entry_px for close, amount)
    fixture_sid = await _setup_synthetic_sim_session(engine, fee_rate=fee_rate, fills=[
        ("open",  "long", 50000.0, 0.1),
        ("close", "long", 51000.0, 0.05),  # partial close
        ("close", "long", 49500.0, 0.05),
    ])
    src_rts = await _src_metrics_collect_roundtrips(engine, fixture_sid)
    script_rts, caveats = await collect_roundtrips(engine, fixture_sid)
    # Drift guard precondition: no stale_close_amount path
    assert caveats["stale_close_amount_count"] == 0
    assert len(src_rts) == len(script_rts) == 2
    for s, t in zip(src_rts, script_rts):
        assert math.isclose(s.pnl_net, t.pnl_net, abs_tol=1e-9)
        assert math.isclose(s.pnl_gross, t.pnl_gross, abs_tol=1e-9)
        assert math.isclose(s.fee_open_share, t.fee_open_share, abs_tol=1e-9)
        assert math.isclose(s.fee_close_share, t.fee_close_share, abs_tol=1e-9)
```

### 7.5 Fixture scanning

- 复用 fee_visibility iter fixture 扫描机制（FillEvent 含 entry_price 字段）
- 新增：扫描所有 TradeAction fixture，断言 close action 行含 entry_price + amount 字段或显式 NULL（drift guard）

### 7.6 性能 sanity

- `MetricsService.compute()` 保持 O(n)；FIFO queue 操作 amortized O(1)
- W3 sim ~200-400 close fills/session 无性能顾虑
- 1000-fill compute < 100ms sanity test（非 hard limit）

---

## 8. Output Format — get_performance 并列双视角

### 8.1 Sim session 输出（schema 示例；具体数字 plan-phase probe 给）

```
=== Trading Performance (@ 14:23:45 UTC) ===
Initial Balance: 10000.00 USDT
Current Balance: 9683.25 USDT
Total Return: -3.17% (-316.75 USDT) (incl. +41.90 USDT unrealized)
Realized PnL: -81.10 USDT gross / -358.65 USDT net (fees -277.55 USDT)
Total Fees: -277.55 USDT

=== Trade Stats ===
Total Trades: 15
Win Rate: 53% gross (8W/7L) / 40% net (6W/9L)
Profit Factor: 1.34 gross / 0.45 net
Avg Win:  +6.20 USDT gross / +3.85 USDT net
Avg Loss: -8.10 USDT gross / -11.42 USDT net
Best Trade: +18.20 USDT gross / +14.10 USDT net
Worst Trade: -12.30 USDT gross / -15.72 USDT net
Max Drawdown: -X.X% (net equity)
```

（本示例数字含 estimate；最终精确值由 plan-phase probe 在 sim #8 ground truth 上跑出后写入 plan 文档。spec 锁定的是**输出格式 schema 与字段名**。）

**Total Return 视角说明（by design）**：单 net（不双视角）—— `Total Return = (current_balance − initial_balance) / initial_balance × 100`，broker balance 已扣 fees 是天然 net；引入 gross 对应字段需重新分配 fees 到 equity 时间线，超出本 iter scope 且业界 ROI 约定本就 net。

### 8.2 OKX session 输出尾追加

```
Note: OKX net metrics use exchange-echoed fees (accurate); minor ε from lot amount precision possible.
      Cache-miss close fills (if any) excluded from net stats; see caveat above.
```

### 8.3 边界场景输出

- 零 trade：`No completed trades yet.`（沿用现有 line 718）
- amount NULL（legacy 行）subset（m < n）：Trade Stats section 顶单行 caveat：
  ```
  === Trade Stats ===
  Note: net stats based on m/n trades (k legacy rows skipped — pre-net-metrics-iter data).
  Total Trades: 15
  Win Rate: 53% gross (8W/7L) / 40% net (6W/9L)  ← net 基于 m 笔
  ...
  ```
- entry_price NULL（OKX cache miss，amount 完整）：FIFO 算法不受影响，独立 informational caveat：
  ```
  Note: k close fills had cache-miss entry_price (FIFO unaffected; audit trail incomplete for those trades).
  ```
  此 caveat 与上一条可同时出现（两类独立计数）。
- 全部 legacy skip（m=0）：所有 net 字段渲染 `N/A`，section 顶提示 `Net stats unavailable: all close fills are pre-net-metrics-iter legacy data.`
- sessions.fee_rate NULL：caveat 末加 ` (fee_rate unknown for this session; net excludes entry fees)`

### 8.4 cli 输出折行考虑

- 每行 "X gross / Y net" 在窄终端可能折行
- 倾向**不**加 compact 模式：agent 视角主要消费者是 LLM，不是终端 user；cli `display.py` 只显示 position 简报（不显示 get_performance 输出）
- 若 plan-phase 实测明显影响 LLM 解析，再加 compact 备选

---

## 9. Surface Δ 汇总

| 维度 | 变化 |
|---|---|
| 工具数量 | **不变**（无新工具，无 deprecation）|
| BaseExchange 接口 | **不变** |
| FillEvent schema | **不变**（entry_price + amount 字段已存在）|
| sessions.fee_rate | **不变**（已 nullable=True；wizard 必填执行在应用层）|
| RuntimeConfig | **不变** |
| TradingDeps | **不变** |
| **trade_actions schema** | **+2 nullable columns**（entry_price, amount）|
| **alembic migration** | **+1 new migration**（add columns + drop/recreate view）|
| **v_cycle_metrics view** | DDL 同步（旧 DB 通过 migration 重建；fresh DB 通过 views.py）|
| PerformanceMetrics dataclass | +7 fields |
| 测试新增 | ~25-30 + 修改 ~5-8 |

---

## 10. Out-of-scope（独立议题）

| 议题 | 排除依据 | 触发条件 |
|---|---|---|
| Funding fee 模拟 / 累计 | sim 不模拟 funding settlement；OKX 实盘 funding 独立账目 | 实盘准备期；与 `project_okx_demo_mark_vs_last_drift` 同期 |
| OKX maker rebate / fee 负值 | fee_visibility iter 锁定 fee 正数；与 maker/taker mix 议题同期 | OKX 实盘准备期 |
| Contract_size multiplier 显式化 | G-calc audit 系列议题（memory `project_g_calc_audit_closure`）| BTC contract_size != 0.01 的其他 perp 接入时 |
| Mark vs Last 一致化 | 独立议题（memory `project_okx_demo_mark_vs_last_drift`）| 实盘 demo 暴露后 |
| **OKX `_close_order_entry_cache` miss fallback** | 与 `iter-tool-opt-okx-fee-rate-auto-fetch` follow-up 同期 | W3+ OKX 实账 cache miss 频次 ≥ 5% 实证 |
| OKX 自动 fee_rate 抓取 | fee_visibility §7 follow-up iter | W3+ OKX 实账 fee 偏差 ≥ 5% 实证 |
| Persona.py fee 描述 | fee_visibility iter 已处理 | — |
| pnl_pct 第三视角（of_margin）| 信息收益 vs verbose 取舍倾向保留 | 若 W3 user feedback 需要 ROI on margin 视角 |
| cli compact 输出模式 | LLM 主要消费者非终端 user | plan-phase 实测影响 LLM 解析后启动 |

---

## 11. 触发条件 ↔ 与档案 §6.6 对应

档案 §6.6 列触发条件：
- ✅ `iter-tool-opt-fee-visibility` landed（PR #56, 2026-05-15）
- ⏸ W3 baseline ≥1 session 跑完（W3R1 PR #55 已 land，可视为部分满足）
- ⏸ W3 数据再现"gross profit_factor 与 net profit_factor 跨越 1.0 边界" → sim #8 W2 数据已客观验证（PF 1.34 → 0.45 跨越 1.0），数学上不依赖 W3 重现

**结论**：触发条件已实质满足；本 iter 现在立项合规。

**Fallback 选项**（若评审方坚持严格按档案 §6.6 文字 "W3 重现" 解读）：W3 sim 跑完后用 sim_metrics.py 跑当时 session 的 gross PF / net PF 数字，作为本 iter ground truth 二次确认；此 fallback **不**阻塞 spec / plan 评审与 impl 启动。
