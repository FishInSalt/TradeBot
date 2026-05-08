# Iter W2R2 — Observability Phase 2 (Cross-Sim Analytics)

> **Status**: spec v1, 2026-05-09
> **Branch**: `feature/iter-w2r2-obs-phase2`
> **Source brainstorm**: 本会话 dialogue（B → D → E 三轮 architecture 推理触底）
> **Pair-doc**: `.working/observability-gaps-from-sim8.md` + `.working/observability-solutions-from-sim8.md`
> **Phase 1 prerequisite**: PR #42 / `6250e51` (2026-05-08) 已 landed
> **Memory anchors**: `project_phase2_brainstorm_handoff` / `project_observability_roadmap_from_sim8` / `project_iter4_sql_caveats` / `project_r2_8b_legacy_decision_restore_boundary` / `project_w2_ops_backlog`

---

## §1 Background & Scope

### §1.1 Phase 1 已闭与 Phase 2 P8-only 决议

Phase 1（PR #42, `6250e51`, 2026-05-08）已落地"派生层基础设施"：
- `agent_cycles` +8 列（timing 2 + tokens 6）+ `trade_actions` +1 列（alert_id）= 共 +9 列
- 3 个 SQL view 单一来源（`src/storage/views.py`）：
  - `v_cycle_metrics`（38 列）
  - `v_alert_lifecycle`（4-CTE：registers / triggers / cancels / cancel_attempts）
  - `v_order_lifecycle`

Phase 2 brainstorm 起手议题为 solutions doc §3 "打包 ④" = P7 (sim_market_snapshot 表) + P8 (analyze/diff CLI)。本 spec 决议**仅做 P8**，理由如下：

1. **W2 复盘实际痛点排序**：`sim8-w2-rerun-findings.md` 系列暴露的 80% 痛点已在 Phase 1 解锁。剩余两痛点中，**P8 cross-sim diff 高频踩**（每次复盘手写 Python），**P7 reaction lag 未真做**（W2 没明确卡在精确价格序列上）。
2. **Phase 1 派生层是 P8 的主要数据源**：v_cycle_metrics 38 列 + v_alert_lifecycle / v_order_lifecycle 已涵盖 PnL / Cost / Behavior 三类核心指标的大部分原子事实。
3. **scope 控制**：P7 数据源决策点更复杂（写入触发点 / retention / live mode 兼容），值得独立 brainstorm。

### §1.2 事实校准（写入 spec 防再误）

**`solutions doc §P7` 段写"sim_exchange 内部 fixture / mock 完整 OHLCV"——不准确**。读 `src/integrations/exchange/simulated.py:1080,1112`：

```python
self._ccxt = ccxtpro.okx()                                  # line 1080
raw = await self._ccxt.watch_ticker(self._symbol)           # line 1112 (websocket live)
data = await self._ccxt.fetch_ohlcv(...)                    # line 128 (REST historical)
```

Sim 实际 = **OKX live websocket ticker + REST OHLCV + simulated balance/orders/positions**。所以 P7 的真实痛点是 sim 期间 ticker websocket stream 流过即丢（websocket 不可重放）；fetch_ohlcv 历史数据 OKX REST 永久可拉。这事实校准**降低了 P7 紧迫性**：精确 reaction lag 才需要 P7，1m kline 粒度的方向命中率用 OKX REST post-hoc 即可。

### §1.3 v_trade_roundtrip view 不入决议依据

solutions doc §B "Lifecycle View" 列了三件套（v_alert_lifecycle / v_order_lifecycle / v_trade_roundtrip）。Phase 1 落了前两个，未落 v_trade_roundtrip。本期评估后**决议不入 view**：

| 评估论据 | 是否成立 |
|---|---|
| "v_trade_roundtrip 是 stable atom" | ❌ 不成立。Trade roundtrip 配对 = FIFO 状态机 + position 累积 + 部分平仓拆分；SQL window function 写 stateful 状态机痛苦，Python stack ~30 行直白。`sim8-w2-rerun-findings.md` 的 ad-hoc Python 配对正是事实证据。|
| "未来 P7 reaction lag 分析需要 roundtrip" | ❌ 不成立。Reaction lag = "alert/decision 时刻 → 后续价格序列"，用 alert_lifecycle / order_lifecycle + sim_market_snapshot 已够，不需配对。|
| "dbeaver / SQL CLI 直接访问 view 自然" | ❌ 想象需求。W2 复盘未用 dbeaver；偶发探索跑 analyze_sim.py 看 markdown 完全够。|
| "gaps doc §B 列了三件套" | ❌ doc 是建议，不是约束；doc 自己也写"window function or self-join"——作者承认 SQL 写法不自然。|

**结论**：Trade roundtrip 配对在 Python 自然，落 view 别扭。本期写 Python；未来若 ≥3 个消费者真需要 SQL 直接访问，再升级到 view（W3 follow-up F-A）。

### §1.4 Scope 边界

| 维度 | In Scope | Out of Scope |
|---|---|---|
| Schema 变动 | 无 | （所有 P7 / v_trade_roundtrip view / agent_cycles 字段扩展）|
| Alembic migration | 无 | （0 migration）|
| `src/storage/` | 不动 | （models / views / database 完全保留）|
| `src/cli/` | 不动 | （main.py 入口 / app.py 主流程不动）|
| `src/integrations/` | 不动 | （sim_exchange / market_data 不动）|
| 新增 | `scripts/analyze_sim.py` / `scripts/diff_sim.py` / `scripts/_sim_metrics.py`（如需）| — |
| 测试 | `tests/test_sim_metrics.py` / `tests/test_analyze_sim.py` / `tests/test_diff_sim.py` / 可选 drift guard | — |

**纯增量 PR**，无 schema / 主流程变动。

---

## §2 Architecture

### §2.1 文件清单

```
                   ┌────────────────────────────┐
                   │  Phase 1 (PR #42)          │   ← 已 landed，本期纯消费
                   │  Views (派生层):           │
                   │    v_cycle_metrics (38 col)│
                   │    v_alert_lifecycle       │
                   │    v_order_lifecycle       │
                   │  Tables (raw):             │
                   │    agent_cycles            │
                   │    trade_actions           │
                   │    sim_orders              │
                   │    tool_calls              │
                   └──────────┬─────────────────┘
                              │ SQLAlchemy SELECT
                              ▼
                ┌─────────────────────────────┐
                │  scripts/analyze_sim.py     │   单 sim 全景 markdown
                │  scripts/diff_sim.py        │   二 sim 比对 markdown + delta + flag
                │  scripts/_sim_metrics.py    │   共享 helper（仅在真有重叠时建）
                └─────────────────────────────┘
```

| 文件 | 性质 | 预估行数 |
|---|---|---|
| `scripts/analyze_sim.py` | 新增，单 sim 全景输出 | ~280-380 |
| `scripts/diff_sim.py` | 新增，二 sim diff 输出 | ~120-180 |
| `scripts/_sim_metrics.py` | 新增 helper（含 trade roundtrip 配对 + 共享 metric 函数）| ~150-220 |
| `tests/test_analyze_sim.py` | 新增 | ~150-200 |
| `tests/test_diff_sim.py` | 新增 | ~80-120 |
| `tests/test_sim_metrics.py` | 新增 | ~120-180 |
| `tests/_sim_fixtures.py`（如需）| 新增 fixture helper | ~50-80 |

**总计**：约 950-1360 行（含测试约 350-580），单 PR，0 alembic / 0 schema。

### §2.2 数据流

```
sessions / agent_cycles / trade_actions / sim_orders / tool_calls / 
v_cycle_metrics / v_alert_lifecycle / v_order_lifecycle
                              │
                              │  SQLAlchemy SELECT
                              ▼
              _sim_metrics.collect_roundtrips(engine, session_id)
              _sim_metrics.compute_pnl_metrics(...)
              _sim_metrics.compute_cost_metrics(...)
              _sim_metrics.compute_behavior_metrics(...)
                              │
                              ▼
              {pnl: {...}, cost: {...}, behavior: {...}, caveats: [...]}
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
      analyze_sim.py:                  diff_sim.py:
      render_markdown(metrics)         render_diff_markdown(metrics_a, metrics_b)
              │                               │
              ▼                               ▼
        stdout / --out file              stdout / --out file
```

### §2.3 Architecture 不变量（不动什么）

- ❌ 不动 `src/storage/views.py`（不加 v_trade_roundtrip view）
- ❌ 不动 `alembic/versions/`（0 migration）
- ❌ 不动 `src/storage/models.py`
- ❌ 不动 `src/cli/app.py` 主流程 + `main.py` 入口
- ❌ 不动 `src/integrations/exchange/simulated.py`（P7 后置）
- ❌ 不引入新 dependency（仅用 sqlalchemy / argparse / stdlib；markdown 用纯字符串拼接）
- ❌ 不建 `scripts/analytics/` 子包（与 scripts/ 现有惯例一致：每脚本 self-contained）

### §2.4 与 scripts/ 现有惯例对齐

scripts/ 现有 4 个 ad-hoc 脚本（`benchmark_view_phase1.py` / `tool_call_summary.py` / `observation_token_audit.py` / `iter6_diag_ticker.py` 等）共同模式：
- 单文件 self-contained
- argparse + 直白函数 + main()
- 重用 `from src.storage` / `from src.config` 等 src 入口
- 不用 logging module（写 stderr 直接 print）

本期 3 个新增脚本严格沿用此模式。

---

## §3 Metric Inventory

First cut 三类共 **28 个 metric**。每条标数据源 + 派生方式。

### §3.1 PnL 类（10 个 metric group）

| # | Metric | 数据源 | 派生 |
|---|---|---|---|
| P1 | `win_rate` | FIFO lot 配对（attribution）| `pnl_net > 0` 占比（lot 级 PnL）；分母 = roundtrip 数；**与 sim 实际 realized PnL 在 partial close 场景合法 diverge**（见 §4.4 caveat）|
| P2 | `total_pnl_net` | **sim realized net PnL**：`sum(close trade_actions.pnl) - sum(roundtrip.fee_total)` | sum(close trade_actions.pnl) 是 sim 加权 gross PnL 累计；sum(roundtrip.fee_total) 是已配对 lot 的双侧 fee 按消耗比例分摊后总和（**不含未平仓 lot 的 open fee**）。**不等于 balance 变化**（balance 还含 unrealized + 未释放保证金）；**不等于 sum(lot pnl_net)**（partial close 场景下合法 diverge）。**口径警示**：若 `caveats['invariant_violations'] > 0`（孤儿 close 无对应 lot），分子含孤儿 close PnL 但分母不含其 fee 分摊 → P2 偏差；caveat 段已标，量级 = 孤儿 close 的 fee（一般 < 0.1 USDT 量级）|
| P3 | `roundtrip_count` | FIFO lot 配对结果 | 计数 |
| P4 | `avg_fifo_pnl_per_roundtrip` | mean(Roundtrip.pnl_net) | 与 P3 同口径（FIFO attribution）；**不**用 P2/P3 跨口径混算 |
| P5 | `avg_roundtrip_duration_min` | FIFO lot 配对 | open_at → close_at 平均 |
| P6 | `median_roundtrip_duration_min` | 同 P5 | median |
| P7 | `max_drawdown_pct` | **raw json_extract(state_snapshot, '$.balance.total_usdt')** 时序（绕过 v_cycle_metrics——view 仅投影 free_usdt，total_usdt 才是权益曲线）| running max - current；起点 = sessions.initial_balance；**粒度 caveat**：state_snapshot 在 cycle 起点写一次（cycle_capture），intra-cycle 大幅波动（cycle 内 fill 触发的 unrealized 跳变）会漏检——这是 first cut 接受的精度局限，W3 follow-up 候选（per-fill snapshot）|
| P8 | `exit_type_distribution` | **Roundtrip.exit_type**（FIFO 配对结果，仅 close fill）| count by type ∈ **{market, stop, take_profit, limit, liquidation}**；**不**直接 GROUP BY sim_orders.order_type（会含 market open 等开仓 fill 污染分布）|
| P9 | `largest_win` / `largest_loss` | FIFO lot 配对 | max / min（**渲染为 2 行**：`largest_win` + `largest_loss`）|
| P10 | `profit_factor` | sum(wins) / abs(sum(losses)) | 比值；**对称约定**：all wins (losses=0) → None / all losses (wins=0) → None / 0 roundtrip → None（三种均"无意义"返同值；下游消费者读 P3 roundtrip_count 与 win_rate 联合判断含义）|

**核心算法 = roundtrip 配对**（详见 §4），落 `_sim_metrics.py`。

### §3.2 Cost 类（8 个）

| # | Metric | 数据源 | 派生 |
|---|---|---|---|
| C1 | `total_input_tokens` | v_cycle_metrics.input_tokens | sum |
| C2 | `total_output_tokens` | v_cycle_metrics.output_tokens | sum |
| C3 | `total_cache_read_tokens` | v_cycle_metrics.cache_read_tokens | sum |
| C4 | `avg_cache_hit_rate` | v_cycle_metrics.cache_hit_rate | weighted avg by input_tokens |
| C5 | `tokens_per_cycle_p50` / `_p95` | v_cycle_metrics.tokens_consumed | percentile |
| C6 | `avg_wall_time_ms` | v_cycle_metrics.wall_time_ms | avg |
| C7 | `avg_llm_call_ms` / `avg_tool_total_ms` | v_cycle_metrics | avg；**渲染 2 行**（同 P9 / C5 / B7 的多 metric 表项）|
| C8 | `per_tool_call_top10` | tool_calls GROUP BY tool_name | count desc / 取 top 10 |

### §3.3 Behavior 类（10 个）

| # | Metric | 数据源 | 派生 |
|---|---|---|---|
| B1 | `total_cycles` | v_cycle_metrics | count |
| B2 | `ok_vs_forensic_count` | v_cycle_metrics.is_ok_cycle / is_forensic_cycle | sum 两路 |
| B3 | `triggered_by_distribution` | v_cycle_metrics.triggered_by | count by enum |
| B4 | `decision_type_distribution` | trade_actions.action GROUP BY cycle | count（open_long/open_short/close/adjust/hold/wake-only ）— 见 §3.5 caveat 1 |
| B5 | `5field_complete_rate` | v_cycle_metrics.five_field_complete | mean |
| B6 | `per_field_hit_rate` | has_stance / has_active_commitments / has_this_cycle_delta / has_thesis_invalidation / has_watch_list | 5 个 mean |
| B7 | `avg_decision_length_chars` / `_p95` | v_cycle_metrics.decision_length | avg + percentile |
| B8 | `retraction_rate` | agent_cycles.decision text 时序 | cycle-to-cycle stance 改判（详见下注）|
| B9 | `avg_reasoning_tokens` / `avg_thinking_chars` | v_cycle_metrics.reasoning_tokens + agent_cycles.reasoning length | avg |
| B10 | `alert_lifecycle_summary` | v_alert_lifecycle | triggered_rate / cancelled_rate / avg_cancel_attempts |

**B8 retraction_rate 算法**：

```python
STANCE_RE = re.compile(
    r"(?:^|\n)\s*(?:\*\*)?\(?1\)?\.?\s*(?:\*\*)?\s*[Ss]tance(?:\*\*)?\s*[:：]\s*"
    r"(?:\*\*)?(\w+)",
    re.MULTILINE,
)
# stance 取值规范化：lowercase + strip；非空字符串集合（W2 sim #8 实测含 
# bull/bear/neutral/cautious/wait 等，不固定枚举，分析时用比较而非分类）

def extract_stance(decision: str | None) -> str | None:
    if not decision:
        return None
    m = STANCE_RE.search(decision)  # 取首个匹配（5-field 段中 (1) Stance 在头部）
    return m.group(1).lower().strip() if m else None

def retraction_rate(cycles: list[AgentCycle]) -> float | None:
    """retraction = cycle N stance ≠ cycle N-1 stance 的比例。
    
    - 分子：相邻 ok cycle 对中 stance 改判数（不含 None / forensic）
    - 分母：可比较 cycle 对数（两侧都有非 None stance）
    - 0 可比较对 → None
    """
    valid = [(c.cycle_id, extract_stance(c.decision))
             for c in cycles if c.execution_status == 'ok']
    pairs = [(prev, curr) for prev, curr in zip(valid, valid[1:])
             if prev[1] is not None and curr[1] is not None]
    if not pairs:
        return None
    retractions = sum(1 for prev, curr in pairs if prev[1] != curr[1])
    return retractions / len(pairs)
```

**精度局限（接受的 first cut）**：基于 §3.5 caveat 3 / §8.1 R3——anchor 用 substring LIKE 未严格 anchor 行首；R2-Next-A priors-injection 引述上 cycle 会 false positive 抬高 retraction 计数。W3 follow-up F-D 与 R2-Next-J 联动收紧后自然解决。

### §3.4 数据源 / 派生方式总表

| 数据源 | 用于 metric | 备注 |
|---|---|---|
| `v_cycle_metrics`（38 列） | C1-C7, B1, B2, B5-B7 | 已 landed Phase 1 |
| `v_alert_lifecycle` | B10 | 已 landed Phase 1 |
| `v_order_lifecycle` | （间接，via sim_orders）| 已 landed Phase 1 |
| `agent_cycles`（raw）| B8（reasoning text grep）, B9（reasoning length）, **P7**（json_extract `$.balance.total_usdt` 时序）| Phase 1 已新增列；P7 走 raw 因 view 仅投影 free_usdt |
| `sim_orders`（raw）| FIFO 配对（P1-P10）, P8 exit_type | sim/exchange 写入 |
| `trade_actions`（raw）| FIFO 配对辅助（cycle_id JOIN）, B4 | append-only 事件流 |
| `tool_calls`（raw）| C8 per-tool 频次 | append-only |
| `sessions`（raw）| header 元数据（symbol / created_at / last_active_at）| — |

### §3.5 SQL caveat 内化

以下 caveat 必须在 metric 实现时吸收，落代码注释 + 测试覆盖。来源：`memory project_iter4_sql_caveats`（虽然 R2-7 已把 decision_logs rename 为 agent_cycles，但语义 caveat 仍然有效）。

**Caveat 1 — `hold` 双义**

`B4 decision_type_distribution` 必须区分两类 hold：
- (a) cycle 0 actions（agent 纯观察，无 trade_actions row）
- (b) cycle 仅含 set_next_wake（trade_actions.action='set_next_wake'）

实现：先 GROUP BY 后用 HAVING 检查 cycle 内 action 集合（**不能**先 WHERE action='set_next_wake' 过滤——会把"含 set_next_wake + 其他"也错归类为"wake-only"，因 WHERE 后只剩 set_next_wake 行 COUNT(DISTINCT)=1 恒真）。正确 SQL：

```sql
-- wake-only cycles: 仅含 set_next_wake 一种 action
SELECT cycle_id FROM trade_actions
WHERE session_id = ?
GROUP BY cycle_id
HAVING COUNT(DISTINCT action) = 1 AND MAX(action) = 'set_next_wake'
```

`decision_type_distribution` 输出包含明确的 `hold (pure-observation)` / `hold (wake-only)` 两个 key。

**Caveat 2 — Legacy session 不进 query path**

R2-7 (PR #35, merge 2026-05-02) 之前的 session 不查（per `memory r2_8b_legacy_decision_restore_boundary` 契约）。analyze 启动时检测：

```python
R2_7_MERGED_AT = datetime(2026, 5, 2, tzinfo=timezone.utc)

def assert_not_legacy(session: SessionModel) -> None:
    if session.created_at < R2_7_MERGED_AT:
        raise SystemExit(...)
```

`R2_7_MERGED_AT` 常量由 drift guard test（§7.4）锁住。

**Caveat 3 — 5-field anchor view 误报**

Phase 1 PR #42 review 留下 §I-4 caveat：`v_cycle_metrics.has_stance` 等 6 个 anchor 列用 substring LIKE 未 anchor 到行首；R2-Next-A priors-injection 引述上 cycle 会触发 false positive。

本期 metric（B5/B6）直接复用 view 列接受这个误差，**不**收紧 anchor。理由：
- 收紧 anchor 是 view SQL 改动，违反本期"0 schema 变动"承诺
- 误差量级在 W2 sim #8 数据中 <5%（per Phase 1 review 段），可接受
- W3 follow-up F-D 单独议题（与 R2-Next-J 联动）

实现：B5/B6 注释引此 caveat；不 fix。

---

## §4 Trade Roundtrip 配对算法（FIFO Lot 模型）

### §4.1 Sim 行为事实校准

读 `src/integrations/exchange/simulated.py` + `src/agent/tools_execution.py` 实证（reviewer round 1 触发的事实勘误）：

| 事实 | 实证依据 | 算法影响 |
|---|---|---|
| 同 symbol 一次一仓 | `self._positions: dict[str, _Position]` 单实例（line 72）；同时持仓不可 long+short | 不需多 position 并行 |
| **同向加仓物理支持** | `simulated.py:327-333` market merge / `:560-566` limit merge：`new_contracts = pos.contracts + order.amount`；`new_entry = (pos.entry_price * pos.contracts + fill_price * order.amount) / new_contracts` | 必须用 lot 模型；`open_position` 工具不阻止 agent 重复 open 同 side |
| **部分平仓物理支持** | `simulated.py:422-427` `_close_position_core`：`if amount >= pos.contracts: del; else: pos.contracts -= amount`；SL/TP `_fill_market_close:360` `actual_amount = min(order.amount, pos.contracts)` | close fill 可消耗 lot 一部分；roundtrip 按 lot consumed 计数 |
| **Liquidation 是额外 close type** | `simulated.py:584-601` `_force_liquidate` → `trigger_reason="liquidation"`；`:635/:644` `order_type="liquidation"` 写入 sim_orders | exit_type 集合 = {market, stop, take_profit, limit, **liquidation**}（5 enum）|
| **PnL 字段是 gross 不是 net** | `simulated.py:404` `pnl = (fill_price - pos.entry_price) * amount`（毛）；`:412` `free_usdt += released_margin + pnl - fee`（balance 体现 net；trade_actions.pnl 字段持的是 gross）| `pnl_net = pnl_gross - fee_open - fee_close`，open/close fee 双侧累积 |
| **Close PnL 用加权 entry**（不是 FIFO entry）| `simulated.py:399-404` 用 `pos.entry_price` 已加权；如 close 跨多 lot，sim 算的是整体加权 PnL，不是 FIFO 分摊 | P8 不读 `trade_actions.pnl`，按 **lot 自身 entry_px** 重算 PnL（精确到 lot 级 win/loss）|
| 每 fill 写 trade_action | `_record_action_from_fill` (cli/app.py:417) action='order_filled' + trigger_reason；**不写 cycle_id** | open/close cycle_id 用 `v_order_lifecycle.originated_cycle_id` 公式（5 enum：open_position/close_position/place_limit_order/set_stop_loss/set_take_profit），不含 order_filled |
| **Liquidation cycle_id 不可解** | liquidation 不写 5 enum 任一 trade_action | liquidation roundtrip 的 close_cycle_id = None；caveat 段标"liquidation has no preceding agent action"；可用"最近 preceding cycle"作 best-effort 标 close_cycle_id_proxy（first cut 不做，仅 None） |

### §4.2 算法（FIFO Lot 模型）

```python
# scripts/_sim_metrics.py 内部

def _is_close_fill(position_side: str, side: str) -> bool:
    """与 simulated.py:94 _is_close_order_static 同公式。"""
    return (
        (position_side == "long" and side == "sell") or
        (position_side == "short" and side == "buy")
    )


@dataclass
class _Lot:
    """Open lot — 每次 open fill (含同向加仓) 创建一条；同 side FIFO 队列消耗。"""
    open_at: datetime
    open_cycle_id: str | None      # 来自 v_order_lifecycle.originated_cycle_id
    side: str                      # 'long' | 'short'
    entry_px: float                # 此 lot 自身 fill price (不读 sim 加权 entry)
    original_amount: float         # 用于 fee 按消耗比例分摊
    remaining_amount: float        # 剩余可被 close 消耗
    leverage: int
    open_fee: float                # 此 lot open fill 的 fee


@dataclass
class Roundtrip:
    """配对完成的 lot — 一个 lot 可能被 1 次或多次 close 消耗，每次消耗产 1 个 Roundtrip。"""
    open_at: datetime
    close_at: datetime
    open_cycle_id: str | None
    close_cycle_id: str | None     # liquidation 时为 None
    side: str
    entry_px: float                # = lot.entry_px
    exit_px: float                 # = close fill_price
    amount: float                  # consumed (可能 < lot.original_amount, partial close)
    leverage: int
    pnl_gross: float               # = (exit_px - entry_px) * amount * sign(side)，不读 trade_actions.pnl
    fee_open_share: float          # = lot.open_fee * (amount / lot.original_amount)
    fee_close_share: float         # = close.fee * (amount / close.amount_total)
    fee_total: float               # = fee_open_share + fee_close_share
    pnl_net: float                 # = pnl_gross - fee_total
    duration_seconds: int
    exit_type: str                 # 'market' | 'stop' | 'take_profit' | 'limit' | 'liquidation'


def collect_roundtrips(engine, session_id: str) -> tuple[list[Roundtrip], dict]:
    """扫 sim_orders 已 filled 的 fill 事件，按 side FIFO 配对成 roundtrip。
    
    返回: (roundtrips, caveats)
      - roundtrips: 配对完成的 Roundtrip 列表（一个 close 跨多 lot 时产多条）
      - caveats: {'unclosed_lot_count': {'long': int, 'short': int},
                  'invariant_violations': int,
                  'liquidation_count': int,
                  'stale_close_amount_count': int}
    
    算法（伪码）：
      1. SELECT sim_orders LEFT JOIN v_order_lifecycle USING (order_id)
         WHERE session_id=? AND filled_at IS NOT NULL
         ORDER BY filled_at ASC, id ASC
         (id ASC 作 tiebreaker：同 tick 内多 fill 共享 datetime.now() 写入；
          id 是 INTEGER PK 自增 (`src/storage/models.py:162` `id: Mapped[int] = mapped_column(primary_key=True)`，
          SQLAlchemy default rowid)，保证同 tick 内 open 先于 liquidation/close 处理。
          **如未来 PK 改 UUID 此 tiebreaker 失效**——drift guard 已锁住 SimOrder.id 类型)
         (originated_cycle_id 直接从 v_order_lifecycle 投影；本期不复刻 5-enum 公式，
          自然继承 view 演化，避免 scripts 复制 SQL 漂移)
      2. open_lots: dict[str, deque[_Lot]] = {'long': deque(), 'short': deque()}
      3. for fill in fills:
             is_close = _is_close_fill(fill.position_side, fill.side)
             if not is_close:
                 # OPEN — push lot
                 open_lots[fill.position_side].append(_Lot(
                     open_at=fill.filled_at,
                     open_cycle_id=fill.originated_cycle_id,
                     side=fill.position_side,
                     entry_px=fill.filled_price,
                     original_amount=fill.amount,
                     remaining_amount=fill.amount,
                     leverage=fill.leverage,
                     open_fee=fill.fee or 0.0,
                 ))
             else:
                 # CLOSE — FIFO consume
                 # 校正实际成交量（stale SL/TP fix）：sim_orders.amount 是 order.amount，
                 # 但 _execute_fill 用 actual_amount = min(order.amount, pos.contracts)，
                 # 落库时只写 fee/filled_price/filled_at 不回写 amount（simulated.py:921-929）。
                 # 用 fee 反推：actual_amount = fee / (filled_price * fee_rate)
                 # 兜底：fee 缺/0/fee_rate 缺 → 退回 sim_orders.amount + caveat
                 actual_amount, derived_ok = _derive_close_amount(fill, session.fee_rate)
                 if not derived_ok:
                     caveats['stale_close_amount_count'] += 1
                 close_amount_remaining = actual_amount
                 close_amount_total = actual_amount
                 close_fee_total = fill.fee or 0.0
                 lot_queue = open_lots[fill.position_side]
                 while close_amount_remaining > 0:
                     if not lot_queue:
                         caveats['invariant_violations'] += 1
                         print(f"close fill {fill.order_id} has no preceding open lot",
                               file=sys.stderr)
                         break
                     lot = lot_queue[0]
                     consumed = min(lot.remaining_amount, close_amount_remaining)
                     # PnL: liquidation 用 sim cap 后的实际 PnL 按比例分摊；其他用 lot.entry_px 重算
                     if fill.order_type == 'liquidation':
                         # 读 trade_actions.pnl（sim cap 后）按 consumed/actual_amount 比例分摊。
                         # JOIN 漏 trade_actions row（fill.trade_action_pnl is None）= invariant
                         # violation：liquidation 必有 _record_action_from_fill 写 trade_actions.pnl
                         # 行；缺失说明 JOIN/数据异常，不能静默记 0。
                         if fill.trade_action_pnl is None:
                             caveats['invariant_violations'] += 1
                             print(f"liquidation fill {fill.order_id} missing trade_actions.pnl "
                                   f"row — cannot allocate pnl_cap value",
                                   file=sys.stderr)
                             pnl_gross = 0.0  # 仅 fallback 防 NaN；caveat 已标
                         else:
                             pnl_gross = fill.trade_action_pnl * (consumed / actual_amount)
                     else:
                         pnl_gross = _compute_pnl(lot.entry_px, fill.filled_price, consumed, lot.side)
                     fee_open_share = lot.open_fee * (consumed / lot.original_amount)
                     fee_close_share = close_fee_total * (consumed / close_amount_total)
                     fee_total = fee_open_share + fee_close_share
                     roundtrips.append(Roundtrip(
                         open_at=lot.open_at,
                         close_at=fill.filled_at,
                         open_cycle_id=lot.open_cycle_id,
                         close_cycle_id=(fill.originated_cycle_id 
                                          if fill.order_type != 'liquidation' else None),
                         side=lot.side, entry_px=lot.entry_px, exit_px=fill.filled_price,
                         amount=consumed, leverage=lot.leverage,
                         pnl_gross=pnl_gross,
                         fee_open_share=fee_open_share, fee_close_share=fee_close_share,
                         fee_total=fee_total,
                         pnl_net=pnl_gross - fee_total,
                         duration_seconds=int((fill.filled_at - lot.open_at).total_seconds()),
                         exit_type=fill.order_type,  # market/stop/take_profit/limit/liquidation
                     ))
                     lot.remaining_amount -= consumed
                     close_amount_remaining -= consumed
                     if lot.remaining_amount <= 1e-9:  # float epsilon
                         lot_queue.popleft()
                 if fill.order_type == 'liquidation':
                     caveats['liquidation_count'] += 1
      4. caveats['unclosed_lot_count'] = {'long': len(open_lots['long']),
                                           'short': len(open_lots['short'])}
         (按 side 分计；testing case 用此格式，与 §7.3.1 unclosed_lot 期望一致)
      （注：不做 sanity check 比 sum(roundtrip.pnl_gross) vs sim realized PnL —— 详见 §4.4 项 6，partial close 场景两者合法 diverge）
    """


def _compute_pnl(entry_px: float, exit_px: float, amount: float, side: str) -> float:
    """与 simulated.py:403-406 同公式（lot 级，非加权）。
    
    注：liquidation 不用此函数 — sim 内 _close_position_core(pnl_cap=True)
    会 cap 损失到保证金（simulated.py:408-409），lot.entry_px 重算会绕过 cap，
    导致 largest_loss / profit_factor 比 sim 实际更负。Liquidation 走 trade_actions.pnl
    按 consumed/total 比例分摊（见 §4.2 算法分支 + §4.4 caveat）。
    """
    if side == "long":
        return (exit_px - entry_px) * amount
    return (entry_px - exit_px) * amount


def _derive_close_amount(fill, fee_rate: float | None) -> tuple[float, bool]:
    """反推 close fill 的实际成交量（解决 stale SL/TP 问题，§4.4 caveat）。
    
    返回: (actual_amount, derived_ok)
      - derived_ok=True：用 fee 反推成功（amount 可信）
      - derived_ok=False：fallback 到 sim_orders.amount（caller 应 +1 stale_close_amount_count）
    
    背景（实证 line 引用）：
      - simulated.py:507-509 conditional close (`_execute_fill`)：
        `actual_amount = min(order.amount, pos.contracts)`（局部 actual_amount）
      - simulated.py:511 调 `_close_position_core(symbol, side, actual_amount, ...)`
        （传 actual_amount 不是 order.amount）
      - simulated.py:401 `fee = fill_price * amount * self._fee_rate`（amount 即 actual_amount）
      - simulated.py:519 FillEvent.fee = actual_amount-based fee
      - simulated.py:921-929 UPDATE sim_orders SET fee = fill.fee（持久化 actual_amount-based fee；
        但 amount 字段不回写，仍为 order 原始量）
    
    **关键事实**：sim_orders.fee 是 actual_amount-based（不是 order.amount-based），
    所以 fee 反推 actual_amount 数学成立。
    
    用 fee 反推：fee = filled_price × actual_amount × fee_rate
    → actual_amount = fee / (filled_price × fee_rate)
    
    兜底：fee 缺 / 0 / fee_rate 缺 / fill.filled_price 缺 / 反推值 > sim_orders.amount × 1.01
          → 退回 sim_orders.amount, derived_ok=False
    """
    if fill.fee and fill.filled_price and fee_rate and fee_rate > 0:
        derived = fill.fee / (fill.filled_price * fee_rate)
        # sanity: 反推值应 ≤ sim_orders.amount（order 原始量）
        if derived <= fill.amount * 1.01:  # 1% 浮点容忍
            return derived, True
    # fallback
    return fill.amount, False
```

### §4.3 数据组装（字段映射 + cycle_id 公式）

| Roundtrip 字段 | 取自 |
|---|---|
| `open_cycle_id` / `close_cycle_id` | 直接 SELECT `v_order_lifecycle.originated_cycle_id`（已物化的 5-enum JOIN 结果）；**liquidation 时 close_cycle_id=None**（liquidation 不写 5 enum 任一 trade_action，view 里也是 NULL）|
| `pnl_gross` | **非 liquidation**：lot 级重算 `(exit_px - entry_px) * consumed` (long) / 反向 (short)；不读 trade_actions.pnl（sim 用加权 entry 算）。**Liquidation special-case**：用 `trade_actions.pnl * (consumed / actual_amount)` 按比例分摊（sim 用 `pnl_cap=True` cap 损失到保证金，simulated.py:408-409；lot 级重算会绕过 cap 算得过负）|
| `fee_open_share` | `lot.open_fee * (consumed / lot.original_amount)` |
| `fee_close_share` | `close.fee * (consumed / actual_amount)`，actual_amount 用 `_derive_close_amount(fill, fee_rate)` 反推（解决 stale SL/TP 问题，§4.4 caveat） |
| `fee_total` | `fee_open_share + fee_close_share` |
| `pnl_net` | `pnl_gross - fee_total` |
| `exit_type` | close 的 `sim_orders.order_type`（'market' / 'stop' / 'take_profit' / 'limit' / **'liquidation'**）|
| `duration_seconds` | `(close.filled_at - lot.open_at).total_seconds()` |
| `entry_px` / `exit_px` | 各自 `sim_orders.filled_price` |
| `amount` | consumed（可能 < lot.original_amount, partial close 时；用 actual_amount 而非 sim_orders.amount）|
| `leverage` | `lot.leverage`（open 时定型）|
| `side` | `lot.side`（= position_side）|

### §4.4 边界与 invariant violations

First cut 接受的约束：

1. **未配对 lot（sim 中途截止）**：不产 Roundtrip；`unclosed_lot_count` 在 caveats 段标出（按 long/short 分计）。不影响 win_rate 等核心 metric 分母（分母 = 配对完成的 Roundtrip 数）。
2. **Close 无对应 lot（双 close / 越界）**：stderr print warning + 中断当前 close 的剩余 consume；first cut 不主动 fix（W3 follow-up F-E）。
3. **Liquidation cycle_id N/A**：liquidation 不写 5 enum 任一 trade_action；close_cycle_id=None；caveat 段单独 1 项 "<N> liquidation event(s) — cycle_id N/A"。
4. **Liquidation PnL cap special-case**：sim `_close_position_core(pnl_cap=True)` 限制 liq 损失到保证金（simulated.py:408-409 `pnl = max(pnl, -(released_margin - fee))`）。lot 级重算会绕过此 cap，算出过负值。修法：liquidation roundtrip 的 `pnl_gross` 用 `trade_actions.pnl * (consumed / actual_amount)` 分摊（§4.3 表）。caveat 段标 "<N> liquidation roundtrip(s) — pnl read from trade_actions due to pnl_cap"。
5. **Stale SL/TP partial fill（amount 不准）**：`simulated.py:507-509` `actual_amount = min(order.amount, pos.contracts)`；`:921-929` 落库 UPDATE 仅写 `fee/filled_price/filled_at` **不回写 amount**。所以 sim_orders.amount 是 order 原始量，可能 > 实际成交量（如 SL amount=0.2 但仓位仅 0.05，实际成交 0.05 但 amount 字段写 0.2）。修法：用 `_derive_close_amount(fill, fee_rate)` 公式反推 `actual_amount = fee / (filled_price * fee_rate)`（§4.2 helper）；fee 缺/0/fee_rate 缺时 fallback 到 sim_orders.amount + caveat 段标 `<N> stale close amount(s) — actual_amount derivation failed`。**关键不变量**：fee_rate 在 session 内为常量（`simulated.py:65` `self._fee_rate = config.fee_rate`，无 per-fill 动态）；公式依赖此假设；如未来 sim 引入 per-fill 不同 fee_rate，此 helper 必须重写（W3 follow-up 候选）。
6. **Legacy session（R2-7 之前）**：本期不 query；analyze fail-fast（§6.4）。
7. **Open 字段 NULL**：仍配对，相应字段为 None；下游 metric 遇 None 返回 None；caveat 段标 affected count。
8. **FIFO lot PnL vs sim 加权 PnL 合法 diverge**（**重要**）：partial close + 同向加仓场景下，sum(lot pnl_net) ≠ sim realized PnL，且**这不是 bug**。反例：lot1(entry=100, amt=1) + lot2(entry=200, amt=1) → sim weighted entry=150；close 0.5 at 150 → sim PnL=0，FIFO lot PnL=+25（lot1 被消耗 0.5）。两者各自 self-consistent：
   - **sim 视角**：partial close 按整体加权 entry 算，剩余仓位仍用同一 weighted entry 计 unrealized
   - **FIFO 视角**：先进先出 attribution，已平的归 lot1（已实现 +25），未平的留 lot2（unrealized -25）
   - 两者必相等的场景：所有 lot 全平时（数学上 sum FIFO = sum sim weighted）
   - **设计决议**：`P2 total_pnl_net` 用 sim realized net PnL（已配对部分；详见下文 "P2 语义清楚" 段；**不等于 balance 变化**——后者还含未平仓 lot 的 unrealized + 未释放保证金）；roundtrip 级 pnl_net 用 FIFO（attribution，分配 win_rate / largest_win/loss）；**不做 sanity check**（不应 check 一个不必守恒的等式）

**SQL 草案 — `total_pnl_net` 计算**（sim realized net）：

```sql
-- sum of close fill gross PnL (sim weighted entry; partial close 已分摊)
SELECT SUM(ta.pnl)
FROM trade_actions ta
JOIN sim_orders so ON so.order_id = ta.order_id
WHERE ta.session_id = ?
  AND ta.action = 'order_filled'
  AND ((so.position_side='long' AND so.side='sell')
    OR (so.position_side='short' AND so.side='buy'))
```
gross 减去 `sum(roundtrip.fee_total)` 得 net。**roundtrip.fee_total 已按消耗比例分摊**（fee_open_share = lot.open_fee × consumed/lot.original_amount + fee_close_share = close.fee × consumed/actual_amount），所以未平仓 lot 的 open fee 部分自然不入分子（这部分 fee 仍冻结在 balance.used 里，待该 lot 被未来 close 消耗时才 attribute 到对应 roundtrip）。

**注**：close 判定不能仅看 trade_actions.trigger_reason —— `market` 同时是 open 和 close 的 trigger_reason（`simulated.py:345 / :380` 都写 `"market"`）；必须 JOIN sim_orders 用 `_is_close_fill(position_side, side)` 判定。

**P2 语义清楚**：sim 已实现净 PnL（已配对部分）；不含 unrealized；不含未平仓 lot 的 open fee。Balance 变化 = P2 + Σ(unrealized lot PnL) − Σ(未平仓 lot open fee) − Σ(未平仓 lot frozen margin already deducted），不在本 metric 范围。

### §4.5 测试矩阵

详见 §7.3.1。关键 case：

| Case | 期望 |
|---|---|
| 1 open + 1 market close（同 amount）| 1 roundtrip, exit_type='market', amount = open.amount |
| 1 open + SL fill | 1 roundtrip, exit_type='stop' |
| 1 open + TP fill | 1 roundtrip, exit_type='take_profit' |
| 1 open + liquidation | 1 roundtrip, exit_type='liquidation', close_cycle_id=None |
| 2 完整 long roundtrip 时序连续 | 2 roundtrip |
| Long + short 交替 | 各 1 roundtrip，按 side 分队列不混合 |
| **同向加仓**（lot1 + lot2 同 long）+ 1 close 全平 | 2 roundtrip（lot1 → close 配对 1 / lot2 → close 配对 2，consumed=各自 amount）|
| **部分平仓**（lot1 amount=0.2 + close amount=0.05）| 1 roundtrip, amount=0.05；lot1.remaining=0.15 |
| **跨 lot close**（lot1 0.1 + lot2 0.1，close 0.15）| 2 roundtrip：lot1 全消耗（0.1）+ lot2 部分（0.05），lot2.remaining=0.05 |
| **fee 比例分摊**（open.fee=0.50, lot 50% 被 close）| roundtrip.fee_open_share = 0.25 |
| 1 open 无 close（截止）| 0 roundtrip + unclosed_lot_count=1（按 long/short 分计）|
| 双 close 越界（close 无对应 lot）| stderr print warning + invariant_violations=1 |
| Empty session（无 fill）| (空 list, 全 0 caveats) |
| **Partial close PnL diverge**（lot1=100/1 + lot2=200/1 + close 0.5@150）| FIFO lot pnl=+25 / sim weighted pnl=0（合法 diverge，**非** sanity failure）|
| **Full close PnL match**（lot 全平 / 无 partial）| sum(FIFO lot pnl) == sim realized（数学必相等，浮点容忍 1 USDT）|

---

## §5 Diff 报表形态

### §5.1 CLI 签名

```bash
scripts/analyze_sim.py --session <session_id_or_name> [--db PATH] [--out FILE]
scripts/diff_sim.py --a <session_id_or_name> --b <session_id_or_name> [--db PATH] [--out FILE]
```

| 参数 | 含义 | 默认 |
|---|---|---|
| `--session` / `--a` / `--b` | session UUID 或 sessions.name | 必需 |
| `--db` | DB 路径 | `data/tradebot.db` |
| `--out` | 输出文件 | stdout |

**Session 解析**：UUID 优先匹配；非 UUID 时按 sessions.name（unique 字段）匹配；同时 ambiguous 时（sessions.name 实质 unique，仅 race 罕见）报错列候选退出。

**顺序约束（diff）**：A = baseline / B = compare（语义"B 与 A 比有何差异"）。

### §5.2 输出结构

```markdown
# Sim {Analysis | Diff} Report

- **{A|Session}**: <name> (<symbol>, <created_at> → <last_active_at>, <ok_cycles> ok cycles)
- **B**: <name> (...)        ← 仅 diff 模式
- Generated: <now UTC>

## PnL          ← 用户最关心，置首
…
## Behavior     ← 决策行为漂移
…
## Cost         ← token / timing 经济
…
## Caveats      ← unclosed open / invariant / forensic / legacy / etc
…
```

每段一表，列固定：
- analyze: `| Metric | Value |`
- diff: `| Metric | Sim A | Sim B | Δ | Δ% | Flag |`

### §5.3 Δ / Δ% 算法（按 metric 类型分流）

| 类别 | 示例 metric | Δ 单位 | Δ% 公式 | Flag 依据 |
|---|---|---|---|---|
| **Counts** | total_cycles, roundtrip_count | 数量差 | (b-a)/a × 100 | Δ% |
| **Sums (PnL)** | total_pnl_net, largest_win/loss | USDT 差 | （特殊：分母跨 0 时 'n/a'）| 绝对差（见 §5.4）|
| **Sums (tokens)** | total_input_tokens etc. | 数量差 | (b-a)/a × 100 | Δ% |
| **Averages (non-PnL)** | avg_wall_time_ms, avg_llm_call_ms, avg_tool_total_ms, avg_cache_hit_rate(注: rate 走 Rates 行) | 单位差 | (b-a)/a × 100 | Δ% |
| **Averages (PnL, USDT)** | avg_fifo_pnl_per_roundtrip | USDT 差 | (b-a)/a × 100；divisor=0 或跨 0 → 'n/a' | **优先 Δ%**（≥10% / ≥30%）；Δ%='n/a' 时回落 PnL 绝对差阈值（≥ 50 / ≥ 200 USDT，与 Sums (PnL) 同标准）|
| **Rates** (已是 %) | win_rate, cache_hit_rate, 5field_complete_rate | **pp**（b-a 直接差）| (b-a)/a × 100（相对变化）| max(\|Δ pp\| 阈值, \|Δ%\| 阈值) |
| **Percentiles** | tokens_per_cycle_p95 | 单位差 | (b-a)/a × 100 | Δ% |
| **Distributions** (dict) | exit_type, triggered_by | 拆 N 行，每 key 一行 `metric[key]` | 每 key 单独算 | 每 key Flag |

**Distribution key 缺失处理**：A 与 B 的 key 集合可能不同（如 sim_a 含 `liquidation`=5%、sim_b 该 key 缺失）。规则：**取并集**作为渲染 key 集合；任一侧缺失的 key 视为 0%（落 §5.5 "sim_a == 0 用作分母" 或对应缺失值规则）。例如：sim_a `triggered_by[alert]=20%` / sim_b 缺该 key → sim_b=0%, Δ=-20pp, Δ%=-100%, flag=🔴。

### §5.4 Flag 阈值表

写死代码（`_sim_metrics.py` 内常量），不暴露 CLI 参数（YAGNI；W3 follow-up F-C 数据驱动决定 per-metric override）。

| Flag 依据 | ⚠️ | 🔴 |
|---|---|---|
| Δ% (counts / averages / latency / token sums) | \|Δ%\| ≥ 10% | \|Δ%\| ≥ 30% |
| Rates (pp 与 % 都评估，**取更严重 flag**) | (\|Δ pp\| ≥ 5) **OR** (\|Δ%\| ≥ 10) | (\|Δ pp\| ≥ 15) **OR** (\|Δ%\| ≥ 30) |
| PnL 绝对差（sums 特殊；**只用 \|Δ\|**，不参考 \|Δ%\|）| \|Δ\| ≥ 50 USDT | \|Δ\| ≥ 200 USDT |

**Rate flag 规则明确**：rate 类同时按 pp 和 % 两个判据评估，任一达到 🔴 阈值则 🔴；否则任一达到 ⚠️ 阈值则 ⚠️；否则 `—`。"取更严重的 flag"是 OR 语义，**不**是"pp 主导抑制 %"。

实例：
- 91% → 92%：pp=1, Δ%=1.1% — 都未触发，flag=`—`（避免 1pp 微漂移误报）
- 91% → 96%：pp=5, Δ%=5.5% — **仅 pp 触发 ⚠️**（% 未达 ⚠️ 阈值），最终 ⚠️（pp 兜底高位 rate 微漂移）
- 5% → 10%：pp=5, Δ%=100% — pp ≥ 5 触发 ⚠️，**Δ% ≥ 30% 触发 🔴 → 取 🔴**（rate 翻倍是大事）
- 95% → 92%：pp=3, Δ%=3.2% — 都未触发，flag=`—`
- 50% → 35%：pp=15, Δ%=30% — pp ≥ 15 触发 🔴，Δ% 边界 ≥ 30% 也触发 🔴 → 🔴
- 50% → 65%：pp=15, Δ%=30% — 同上 🔴

**注**：阈值用 ≥ 不是 >（边界值 5 pp / 10% / 30% / 50 USDT 触发对应 flag）。

**PnL 用绝对差不用 %**：sim_a=-81 / sim_b=+120 时 Δ% 数学上无意义（divisor 跨 0）；50/200 USDT 与 sim 100 USDT 起始 balance 量级匹配。**PnL 类 Δ% 仅作信息显示，不参与 flag 判定**。

**为什么 PnL 用绝对差**：sim_a=-81 → sim_b=+120 时 Δ% 数学上无意义（divisor 跨 0）；50/200 USDT 与 sim 100 USDT 起始 balance 量级匹配（50 = 50% balance ⚠️ / 200 = 200% 🔴）。**注意**：实盘量级会差异巨大，spec 标"sim-only first cut"，实盘启动时 follow-up（W3 F-C）。

### §5.5 缺失值 / 数值精度

**缺失值规则**：

| 情况 | Sim A 列 | Sim B 列 | Δ | Δ% | Flag |
|---|---|---|---|---|---|
| 两边都 None / 空 sim | `—` | `—` | `—` | `—` | `—` |
| sim_a 有 / sim_b None | value | `—` | `—` | `—` | ⚠️（信号丢失值得标）|
| sim_a None / sim_b 有 | `—` | value | `—` | `—` | ⚠️（信号新增值得标，对称处理）|
| sim_a == 0 用作分母（**非 PnL 类**）| 0 | value | b | `n/a` | 按 \|Δ\| > 0 一律 ⚠️ |
| sim_a == 0 用作分母（**PnL 类，USDT 单位**）| 0 | value | b | `n/a` | 沿用 §5.4 PnL 绝对差阈值（\|Δ\| ≥ 50 → ⚠️ / ≥ 200 → 🔴）；例：sim_a=0, sim_b=+250 USDT → 🔴 |
| sim_a < 0 / sim_b > 0 跨 0（PnL）| value | value | (b-a) | `n/a` | 按 \|Δ\| 阈值判 |

**数值精度**：

| 类型 | 格式 | 例 |
|---|---|---|
| Counts | 整数千分位 | `1,243` |
| Rates / percentages | 1 位小数 + `%` | `35.0%` |
| PnL / 货币 | 2 位小数 + " USDT" | `-81.10 USDT` |
| Latency | 整数 + " ms" | `850 ms` |
| Tokens | 整数千分位 | `89,000` |
| Δ pp | 1 位小数 + `pp` | `+15.0pp` |
| Δ% | 1 位小数 + `%` | `+42.9%` |

### §5.6 输出样例

**Analyze（精简）**：

```markdown
# Sim Analysis Report

- Session: sim_8 (BTC/USDT:USDT, 2026-05-06 → 2026-05-07, 177 ok cycles)
- Generated: 2026-05-09 12:34 UTC

## PnL

| Metric                       | Value         |
|------------------------------|---------------|
| total_pnl_net                | -81.10 USDT   |
| win_rate                     | 35.0%         |
| roundtrip_count              | 23            |
| ...

## Behavior

| Metric                       | Value         |
|------------------------------|---------------|
| total_cycles                 | 177           |
| 5field_complete_rate         | 96.6%         |
| ...

## Cost

| Metric                       | Value         |
|------------------------------|---------------|
| total_input_tokens           | 14,432,000    |
| avg_cache_hit_rate           | 91.2%         |
| ...

## Caveats

- 1 unclosed open at session end (BTC long opened cycle 178, no fill before截止)
- 0 invariant violations
- 0 forensic cycles
```

**Diff（精简，PnL 段）**：

```markdown
## PnL

| Metric                       | Sim A         | Sim B         | Δ            | Δ%      | Flag |
|------------------------------|---------------|---------------|--------------|---------|------|
| total_pnl_net                | -81.10 USDT   | +120.50 USDT  | +201.60 USDT | n/a     | 🔴   |
| win_rate                     | 35.0%         | 50.0%         | +15.0pp      | +42.9%  | 🔴   |
| roundtrip_count              | 23            | 31            | +8           | +34.8%  | 🔴   |
| avg_fifo_pnl_per_roundtrip   | -3.53 USDT    | +3.89 USDT    | +7.42 USDT   | n/a     | —    |
| max_drawdown_pct             | 12.5%         | 8.2%          | -4.3pp       | -34.4%  | 🔴   |
| avg_roundtrip_duration_min   | 25.3          | 18.7          | -6.6         | -26.1%  | ⚠️   |
| largest_win                  | +12.40 USDT   | +28.10 USDT   | +15.70 USDT  | +126.6% | —    |
| largest_loss                 | -45.20 USDT   | -8.30 USDT    | +36.90 USDT  | -81.6%  | —    |
| profit_factor                | 0.78          | 2.13          | +1.35        | +173.1% | 🔴   |
| exit_type[market]            | 65%           | 45%           | -20pp        | -30.8%  | 🔴   |
| exit_type[stop]              | 20%           | 30%           | +10pp        | +50.0%  | 🔴   |
| exit_type[take_profit]       | 15%           | 20%           | +5pp         | +33.3%  | 🔴   |
| exit_type[liquidation]       | 0%            | 5%            | +5pp         | n/a     | ⚠️   |
```

---

## §6 Error Handling

### §6.1 错误分级

| 级别 | 行为 | exit code | 例 |
|---|---|---|---|
| **Fatal** | stderr 友好提示 + 立即退出 | 1 | session 不存在 / legacy / DB schema 未 migrate |
| **Argparse** | argparse 自动处理 | 2 | 缺 `--a` / 类型错 |
| **Caveat** | 继续运行，结尾 caveats 段汇总 | 0 | unclosed open / invariant violation / 跨 symbol diff |
| **Data N/A** | 单 metric 显示 `—` | 0 | empty list percentile / divide by 0 |

### §6.2 Fatal 类规则

| 情况 | stderr 信息 | 提示 |
|---|---|---|
| Session 名/id 不匹配 | `Session 'X' not found in <db_path>.` | `Use --list-sessions to see candidates.` |
| Legacy session（R2-7 之前 created_at）| `Session 'X' was created at <ts> (before R2-7 schema reframe at 2026-05-02); legacy sessions are intentionally unsupported (pre-R2-7 schema cutoff).` | （开发者契约：见 `memory r2_8b_legacy_decision_restore_boundary`，不暴露给 end-user）|
| Sessions 表不存在 / schema 未 migrate | `agent_cycles / sim_orders / v_cycle_metrics not found in DB.` | `Run: alembic upgrade head` |
| DB 文件不存在 | `Database file not found: <path>` | `Use --db PATH to override (default: data/tradebot.db).` |
| diff: `--a` / `--b` 任一 session 找不到 | 同 "Session not found" | — |
| `--out` 父目录不存在 | `Output dir <dir> does not exist.` | `Create it first or use a different path.` |
| 其他 sqlite OperationalError | exception traceback 直接透传 | （不掩盖根因，便于诊断）|

### §6.3 Caveat 类规则

继续运行，结尾 `## Caveats` 段汇总：

| 情况 | Caveat 段写入 | 模式 |
|---|---|---|
| Session 0 ok cycle | `Session has 0 ok cycles — all metrics N/A.` | both |
| 0 roundtrip（无完整配对）| `0 closed roundtrips — PnL metrics N/A.` | both |
| 1+ unclosed lot | `<N> unclosed lot(s) at session end (long: <L>, short: <S>) excluded from roundtrip metrics.` | both |
| Invariant violation | `<N> invariant violation(s) detected — see stderr logs for details.` （覆盖：close fill without preceding lot / liquidation fill missing trade_actions.pnl row）| both |
| **Liquidation event** | `<N> liquidation event(s) — close_cycle_id N/A (liquidation does not write 5-enum trade_action); pnl read from trade_actions.pnl due to sim pnl_cap.` | both |
| **Stale close amount** | `<N> stale close amount(s) — actual_amount derivation failed (fee or fee_rate missing); fell back to sim_orders.amount which may overstate close size.` | both |
| Forensic cycle 数 > 0 | `<N> forensic cycle(s) (usage_limit_exceeded: M, retry_exhausted: K) — excluded from cycle averages.` | both |
| diff: A == B | `WARNING: A and B refer to same session — all deltas are zero.` | diff |
| diff: 跨 symbol（A.symbol ≠ B.symbol）| `WARNING: A=<sym_A>, B=<sym_B>; PnL comparable in USDT but market context differs.` | diff |
| analyze: NULL 字段 >5% 行 | `<N> rows with NULL <field> in agent_cycles — affected metrics may be biased.` | both |

### §6.4 Legacy session 检测策略

```python
# scripts/_sim_metrics.py 顶层常量

R2_7_MERGED_AT = datetime(2026, 5, 2, tzinfo=timezone.utc)  # PR #35 merge `e66c263`


def assert_not_legacy(session: SessionModel) -> None:
    """Fail-fast on R2-7 前的 session（per memory r2_8b_legacy_decision_restore_boundary）.
    
    SQLite + aiosqlite 取回的 created_at 是 naive datetime（DateTime(timezone=True)
    在 SQLite 实现下不持 tzinfo）；R2_7_MERGED_AT 是 aware UTC。直接比会 TypeError。
    必须先规范化 tzinfo。
    
    drift guard test 锁住常量值；如未来 R2-7 merge 时间被验证错误（极不可能），
    更新常量值同步更新 test_r2_7_merged_at_constant_matches_pr35。
    """
    created_at = session.created_at
    if created_at.tzinfo is None:
        # naive datetime from SQLite — assume UTC (与 _utcnow() 写入一致)
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at < R2_7_MERGED_AT:
        raise SystemExit(
            f"Session '{session.name}' was created at {created_at.isoformat()} "
            f"(before R2-7 schema reframe at {R2_7_MERGED_AT.date()}); "
            f"legacy sessions are intentionally unsupported "
            f"(pre-R2-7 schema cutoff)."
        )
```

**为什么不精细化（如检查 `decision IS NULL` 比例）**：契约层面已决议不 restore legacy session；fail-fast 比 best-effort 兼容更清晰；用户实际不会试图分析 R2-7 前 session（自我约束）。

### §6.5 Logging

- 所有 stderr 输出用 `print(file=sys.stderr)` 简单写法，**不**用 logging module（不写到 system.log——这些是 ad-hoc 分析脚本，不是 production agent）
- 与 `scripts/benchmark_view_phase1.py` / `scripts/tool_call_summary.py` 现有风格一致

### §6.6 不做的（YAGNI）

- ❌ `--no-clobber` 阻止 `--out` overwrite（写文件 overwrite 是默认 unix 语义，user 自己 git commit / cp 备份）
- ❌ `--validate` 模式跑预检不输出报告（直接跑 + caveats 段已够）
- ❌ retry on transient sqlite lock（sim DB 并发不冲突）
- ❌ JSON error response（CLI 用户读 stderr 就够）

---

## §7 Testing Strategy

### §7.1 测试文件组织

| 文件 | 范围 | 测试数 |
|---|---|---|
| `tests/test_sim_metrics.py` | `_sim_metrics.py` 内的纯函数（roundtrip 配对 ~24 + metric 函数 ~21） | ~42-46 |
| `tests/test_analyze_sim.py` | analyze script 端到端（含 markdown render / argparse / error handling） | ~11-14 |
| `tests/test_diff_sim.py` | diff render（Δ/Δ%/flag 算法 / 缺失值 / distributions / inclusive 阈值） | ~14-17 |
| `tests/test_drift_phase2_metrics.py` | Drift guards（含 v_order_lifecycle 列契约 + caveat msg + group count + section order + R2-7 时间常量 + exit_type 5 enum + SimOrder.id 类型）| ~7-9 |

总 ~74-86 个测试，~530-690 行。

### §7.2 Fixture 策略

复用 `tests/conftest.py` 已有 `db_engine_with_real_db` fixture（init_db + Alembic 全跑）。本期依赖 v_cycle_metrics / v_alert_lifecycle / v_order_lifecycle，必须用 real Alembic upgrade 路径。

新增 `tests/_sim_fixtures.py`（underscore 内部使用，仅本期测试用）：

```python
# Schema：与 src/storage/models.py 实际字段对齐
async def make_session(engine, *, name="test_sim", symbol="BTC/USDT:USDT",
                       created_at=R2_7_MERGED_AT + timedelta(days=1)) -> str:
    """Insert a SessionModel after R2-7 cutoff. Returns session_id."""

async def make_cycle(engine, session_id, cycle_id, *, decision="...",
                     execution_status="ok", input_tokens=5000, ...) -> None:

async def make_open_close_pair(engine, session_id, *, open_cycle, close_cycle,
                                side="long", entry_px=80000, exit_px=82000,
                                amount=0.1, leverage=1, fee=0.5,
                                exit_type="market") -> tuple[str, str]:
    """Insert correlated SimOrder + TradeAction rows simulating one roundtrip.
    
    sim_orders.fee 双侧分别写（open + close）；trade_actions.pnl 写 sim 加权 PnL
    （驱动 P2 total_pnl_net 计算）。FIFO lot 配对所需的 entry_px / amount /
    leverage 来自 sim_orders.filled_price / amount / leverage。
    """

async def make_open_lot(engine, session_id, *, cycle, side="long",
                         entry_px=80000, amount=0.1, leverage=1, fee=0.5) -> str:
    """Insert open-only fill (lot creation, no close yet). For partial-close
    / 同向加仓 / unclosed lot test cases."""

async def make_close_fill(engine, session_id, *, cycle, side="long",
                           exit_px=82000, amount=0.1, fee=0.5,
                           exit_type="market", pnl_gross=None) -> str:
    """Insert close-only fill (consumes lot via FIFO). pnl_gross is the sim's
    weighted-entry PnL written to trade_actions.pnl (drives P2 total_pnl_net;
    not used for FIFO lot attribution which recomputes from lot.entry_px)."""
```

### §7.3 测试矩阵

#### §7.3.1 test_sim_metrics.py — Roundtrip 配对（FIFO Lot 模型）

| 测试 | 验证 |
|---|---|
| `test_collect_roundtrips_empty_session` | 0 fill → list 空 |
| `test_collect_roundtrips_single_market_close` | open + close（market）→ 1 roundtrip, exit_type='market' |
| `test_collect_roundtrips_sl_close` | open + SL fill → exit_type='stop' |
| `test_collect_roundtrips_tp_close` | open + TP fill → exit_type='take_profit' |
| `test_collect_roundtrips_liquidation_close` | open + liquidation → 1 roundtrip, exit_type='liquidation', close_cycle_id=None |
| `test_collect_roundtrips_two_long_sequential` | 2 完整 long roundtrip 时序连续 |
| `test_collect_roundtrips_long_short_alternating` | long + short 各队列不混合 |
| `test_collect_roundtrips_same_side_addition_two_lots_one_close` | open(0.1) + open(0.1) 同 long + close(0.2) → 2 roundtrip（lot1 全消耗 + lot2 全消耗）|
| `test_collect_roundtrips_partial_close` | open(0.2) + close(0.05) → 1 roundtrip(amount=0.05), lot.remaining=0.15 仍在 queue → unclosed_lot_count={'long':1, 'short':0} |
| `test_collect_roundtrips_stale_sl_amount_derived_from_fee` | mock SL order amount=0.2 但 pos 仅 0.05；sim_orders.amount=0.2 (stale) / fill.fee=0.05*price*fee_rate → `_derive_close_amount` 反推 0.05；roundtrip.amount=0.05；non-stale path |
| `test_collect_roundtrips_stale_amount_fallback_to_order_amount` | fee=0 / fee_rate 缺 → fallback sim_orders.amount + caveats['stale_close_amount_count']=1 |
| `test_collect_roundtrips_liquidation_uses_trade_actions_pnl` | mock liquidation：sim 算 PnL cap 后 = -10000（保证金）；FIFO 重算 = -50000；验 roundtrip.pnl_gross == sim cap 后 (-10000) 不是 lot.entry_px 重算值 |
| `test_collect_roundtrips_liquidation_missing_trade_action_invariant` | mock liquidation fill 但 trade_actions.pnl JOIN 漏（None）→ stderr warning + invariant_violations += 1 + pnl_gross=0.0（不静默）|
| `test_collect_roundtrips_non_liquidation_recomputes_pnl_from_lot_entry` | non-liquidation close → pnl_gross 用 `(exit_px - lot.entry_px) * consumed`，不读 trade_actions.pnl |
| `test_collect_roundtrips_close_spans_multiple_lots` | open(0.1) + open(0.1) + close(0.15) → 2 roundtrip（lot1 amount=0.1 全消耗, lot2 amount=0.05 部分消耗）|
| `test_collect_roundtrips_fee_proportional_split` | open.fee=0.50, lot 50% 被 close → fee_open_share=0.25, fee_close_share 按 close 比例 |
| `test_collect_roundtrips_pnl_uses_lot_entry_not_weighted` | 验 roundtrip.pnl_gross 用 lot.entry_px 算，不读 trade_actions.pnl（即使同向加仓后 close，仍用 lot 级 entry）|
| `test_collect_roundtrips_partial_close_lot_pnl_diverges_from_sim_weighted` | 反例：lot1(entry=100,amt=1) + lot2(entry=200,amt=1) + close 0.5 at 150 → FIFO lot PnL=+25, sim weighted PnL=0；两者合法 diverge，**非** sanity failure（验证算法不抛异常 / 不写 sanity caveat）|
| `test_collect_roundtrips_full_close_lot_pnl_matches_sim_weighted` | 全平场景：sum(FIFO lot pnl_gross) == sim realized PnL（数学上必相等）|
| `test_collect_roundtrips_unclosed_lot` | 1 open 无 close → list 空 + unclosed_lot_count={'long':1,'short':0} |
| `test_collect_roundtrips_close_no_lot_warning` | close 无对应 lot（mock）→ warning + invariant_violations=1, 中断 consume |
| `test_collect_roundtrips_cycle_id_5_enum_join` | open_cycle_id 来自 v_order_lifecycle.originated_cycle_id 5 enum；不含 order_filled |
| `test_collect_roundtrips_liquidation_close_cycle_id_none` | liquidation 的 close_cycle_id=None（无 5 enum 的 trade_action）|
| `test_collect_roundtrips_duration_seconds` | duration = (close.filled_at - lot.open_at).total_seconds() |

#### §7.3.2 test_sim_metrics.py — Metric 函数

PnL 类（每函数 1-2 测试）：
- `test_win_rate_basic` / `test_win_rate_all_wins_returns_100pct` / `test_win_rate_zero_roundtrips_returns_none`
- `test_total_pnl_net_uses_sim_realized_minus_roundtrip_fees`（验 P2 = `sum(close trade_actions.pnl) - sum(roundtrip.fee_total)`；未平仓 lot 的 open fee **不**入扣减项）
- `test_total_pnl_net_excludes_unclosed_lot_open_fee`（mock：lot1 已平 + lot2 仍 open；P2 应只扣 lot1 双侧 fee + close fee 中已配对部分，不扣 lot2.open_fee）
- `test_max_drawdown_pct_uses_total_usdt_not_free`（验数据源是 raw json_extract `$.balance.total_usdt`）
- `test_profit_factor_all_wins_returns_none`（divide by 0：sum(losses)=0）
- `test_profit_factor_all_losses_returns_none`（对称约定：losses-only 同样无意义，不返 0 避免与 0 roundtrip 混淆）
- `test_profit_factor_zero_roundtrips_returns_none`
- `test_exit_type_distribution_dict_format_5_keys`（market/stop/take_profit/limit/liquidation）

Cost 类：
- `test_avg_cache_hit_rate_weighted_by_input_tokens` / `test_..._all_null_returns_none`
- `test_per_tool_call_top10_aggregation`
- `test_tokens_percentile_p95`

Behavior 类：
- `test_decision_type_distribution_hold_double_meaning_filter`（caveat 1）
- `test_5field_complete_rate_uses_view_column`
- `test_per_field_hit_rate_5_keys`
- `test_retraction_rate_cycle_to_cycle_stance_change`
- `test_alert_lifecycle_summary_from_view`

Helper：
- `test_is_close_fill_long_sell_returns_true`
- `test_is_close_fill_short_buy_returns_true`
- `test_is_close_fill_open_returns_false`

#### §7.3.3 test_analyze_sim.py — 集成测

| 测试 | 验证 |
|---|---|
| `test_analyze_runs_on_minimal_session` | 1 cycle / 0 roundtrip session → script exit 0 + markdown 含 3 段 + caveats 段 |
| `test_analyze_runs_on_realistic_session` | 30 cycle + 5 roundtrip → 28 metric groups 全部产出（非 `—`）|
| `test_analyze_renders_partial_close_correctly` | open(0.2) + close(0.05) → markdown 显示 1 roundtrip 而非 0；amount=0.05 |
| `test_analyze_renders_liquidation_in_exit_distribution` | mock liquidation fill → exit_type[liquidation] 行出现，PnL 段含 1 roundtrip |
| `test_analyze_session_not_found_exit_1` | `--session typo` → exit 1 + stderr "Session 'typo' not found" |
| `test_analyze_legacy_session_rejected` | session created_at < R2_7_MERGED_AT → exit 1 + stderr "legacy sessions not supported" |
| `test_analyze_legacy_session_naive_datetime_normalized` | mock SQLite 取回 naive datetime → assert_not_legacy 不抛 TypeError，正确比较 |
| `test_analyze_db_file_missing_exit_1` | `--db /nonexistent` → exit 1 |
| `test_analyze_out_file_writes_markdown` | `--out file.md` → 文件创建，stdout 空 |
| `test_analyze_out_dir_missing_exit_1` | `--out /nonexistent/x.md` → exit 1 |
| `test_analyze_session_by_name_resolves` | name 优先匹配 |
| `test_analyze_markdown_3_section_structure` | 输出含 `## PnL` / `## Behavior` / `## Cost` / `## Caveats` |

#### §7.3.4 test_diff_sim.py — Diff render

| 测试 | 验证 |
|---|---|
| `test_diff_basic_two_sessions` | A vs B 不同 cycle 数 → Δ / Δ% / flag 全列 |
| `test_diff_a_equals_b_warning` | `--a sim --b sim` → warning + 所有 Δ=0 |
| `test_diff_cross_symbol_warning` | A.symbol≠B.symbol → caveat 段标 + 仍输出 |
| `test_diff_pnl_negative_to_positive_returns_na` | sim_a=-81, sim_b=+120 → Δ%='n/a', flag 按绝对差判 |
| `test_diff_rate_uses_max_pp_or_pct` | 91% → 92% (Δ%=1.1%, pp=1) → flag=`—`；5% → 10% (Δ%=100%, pp=5) → flag=⚠️ |
| `test_diff_zero_divisor_returns_na_pct` | sim_a=0, sim_b=10 → Δ%='n/a' |
| `test_diff_distribution_expansion` | exit_type dict → 多行 `metric[key]` 展开 |
| `test_diff_missing_value_handling_a_has_b_none` | sim_a value / sim_b None → 'sim_b 列 = `—`'，flag=⚠️ |
| `test_diff_missing_value_handling_a_none_b_has` | sim_a None / sim_b value → 'sim_a 列 = `—`'，flag=⚠️（对称）|
| `test_diff_threshold_warn_at_10pct_inclusive` | 10.0% → ⚠️（≥ 边界含）；9.9% → `—` |
| `test_diff_threshold_crit_at_30pct_inclusive` | 30.0% → 🔴；29.9% → ⚠️ |
| `test_diff_threshold_rate_5pp_only_inclusive` | 91%→96% (pp=5, Δ%=5.5%) → ⚠️（仅 pp 触发，% 未达 ⚠️ 阈值）|
| `test_diff_threshold_rate_5pp_pct_promotes_to_crit` | 5%→10% (pp=5, Δ%=100%) → 🔴（pp=⚠️ + Δ%≥30=🔴 → OR 取更严重）|
| `test_diff_threshold_rate_15pp_inclusive` | 50%→35% (pp=15, Δ%=30%) → 🔴（两边界都触发 🔴）|
| `test_diff_threshold_rate_below_5pp_no_flag` | 91%→92% (pp=1, Δ%=1.1%) → `—`（都未触发）|
| `test_diff_pnl_threshold_50_200_usdt_inclusive` | 50 USDT → ⚠️；200 USDT → 🔴 |
| `test_diff_caveats_aggregated_from_both` | A 1 unclosed + B 0 → caveats 段含两条 |

### §7.4 Drift guards

`tests/test_drift_phase2_metrics.py`（与 Phase 1 view drift guards 同模式）：

| Drift guard | 检查 |
|---|---|
| `test_metric_group_inventory_count_28` | analyze 产出 28 个 **metric group keys**（PnL 10 + Cost 8 + Behavior 10）；未来加新 group 故意 break test。**注**：渲染行数 ≠ group 数（largest_win/loss 渲染 2 行，p50/p95 2 行，distributions 多行）|
| `test_caveat_messages_match_section_6_3` | Caveat 段输出消息字面量与 spec §6.3 表 10 条对齐 |
| `test_section_order_pnl_behavior_cost` | Markdown 输出段序固定 |
| `test_exit_type_5_enum` | exit_type distribution 5 keys = {market, stop, take_profit, limit, liquidation}（与 sim_orders.order_type 对齐）|
| `test_r2_7_merged_at_constant_matches_pr35` | `R2_7_MERGED_AT` 常量 == `datetime(2026, 5, 2, tzinfo=UTC)`（防未来误改）|
| `test_v_order_lifecycle_originated_cycle_id_column_present` | scripts 直接 SELECT v_order_lifecycle.originated_cycle_id；drift guard 锁住 view 输出列契约（如未来 view 重命名/删该列，break test 提醒 scripts 跟进）。**注**：scripts 不复刻 5-enum 公式（直接读 view），所以无需"enum 集合相等"测试 |
| `test_simorder_id_is_int_pk_type` | 反射 `SimOrder.__table__.c.id.type` 为 `sqlalchemy.Integer`；防未来误改 UUID 导致 §4.2 同 tick tiebreaker (`ORDER BY filled_at ASC, id ASC`) 失效 |

### §7.5 不做的（YAGNI）

- ❌ Hypothesis property-based testing（roundtrip 配对算法直白，不需要 fuzz）
- ❌ Snapshot test（markdown 输出格式稳定，文字断言已够）
- ❌ End-to-end pytest 跑真 sim（test fixture 已 mock data 充分；真 sim 是用户复盘场景非 CI）
- ❌ Performance benchmark（28 metric × ≤200 cycles sim 数据量级，毫秒级）

### §7.6 测试运行

```bash
uv run pytest tests/test_sim_metrics.py tests/test_analyze_sim.py tests/test_diff_sim.py tests/test_drift_phase2_metrics.py -v
```

测试用 `db_engine_with_real_db` fixture，每测试单独 fresh DB（避免跨测试污染）。async 测试用 `pytest-asyncio` `asyncio_mode = "auto"`（pyproject.toml 已配置）。

---

## §8 Risks / Out-of-Scope / W3 Follow-ups

### §8.1 Risks

| # | 风险 | 缓解 |
|---|---|---|
| **R1** | Roundtrip 配对 invariant violation（close 无对应 lot）真发生 | 已设计 stderr print warning + skip + caveat 计数（§4.4）；W3 数据驱动决定是否 hardening |
| **R2** | FIFO lot pnl_net（attribution）与 sim 加权 PnL（realized）在 partial close 场景合法 diverge | 设计层接受：`P2 total_pnl_net` 用 sim realized；roundtrip 级 pnl_net 用 FIFO 仅作 attribution；不做 sanity check（§4.4 项 6 详述）|
| **R3** | retraction_rate 用 regex 抽 stance（基于 Phase 1 view §I-4 caveat：未 anchor 行首会 false positive）| metric 实现注释引 view caveat；接受 W3 数据噪声；R2-Next-J 收紧 anchor 后自然解决 |
| **R4** | PnL 阈值 50/200 USDT 是 sim 100 USDT 起始 balance 量级 hardcode | 实盘量级会差异巨大；spec 标 "sim-only first cut"；实盘启动时 follow-up F-C |
| **R5** | Markdown 表格列宽过宽（distributions 展开后 metric 名长）| 测试加 stdout 列宽断言（≤120 chars）；超出时换行处理 |
| **R6** | 跨 R2-7 schema reframe 边界（legacy session）的硬时间线 (`R2_7_MERGED_AT = 2026-05-02`) | drift guard test 锁住常量；`memory r2_8b_legacy_decision_restore_boundary` 契约引用 |

### §8.2 Out of Scope（明确不做）

| # | OOS 项 | 理由 / 何时启动 |
|---|---|---|
| **OOS-1** | P7 sim_market_snapshot 表 | 后置独立 iter；reaction lag / 方向命中率分析触发时启动 |
| **OOS-2** | v_trade_roundtrip view 落地 | YAGNI；Python FIFO lot 配对足够；多消费者出现时升级（F-A）|
| **OOS-2b** | 工具层 guard 禁止已有同向仓位再 open | 违反"§2.3 不动 src/agent"承诺，且引入 agent 行为变更（观察期红线）；改用 FIFO lot 模型在分析层消化 |
| **OOS-3** | baseline mode（pin 一 sim 作 reference） / git-style multi-sim diff | 用户需求未明确；first cut 仅 pair |
| **OOS-4** | JSON 双输出 | 仅 markdown stdout；无下游 pipeline 消费者 |
| **OOS-5** | CLI 阈值参数（`--threshold-warn` 等）| 写死代码；W3 数据驱动决定 per-metric override（F-C）|
| **OOS-6** | tradebot CLI 子命令体系（`tradebot analyze` / `tradebot diff`）| 与 main.py 完全解耦；scripts/ 独立 .py 模式 |
| **OOS-7** | per-tool 频次以外的 tool_call payload 分析 | P3 范畴（tool_call_responses 表）→ Phase 3 |
| **OOS-8** | Prompt snapshot diff（cycle 4 init 注入了什么）| P4 范畴 → Phase 3 |
| **OOS-9** | Live 实盘 session 分析 | first cut 仅支持 sim session；实盘需考虑 retention / 隐私 / 多 symbol |
| **OOS-10** | Hypothesis property-based / snapshot test / e2e subprocess 测试 | first cut 不需要 |

### §8.3 W3 Follow-up candidates（数据驱动启动）

| # | Candidate | 触发条件 |
|---|---|---|
| **F-A** | v_trade_roundtrip view 升级（Python → SQL） | scripts/analyze_sim.py 之外 ≥3 个消费者需要 trade roundtrip 数据 |
| **F-B** | baseline mode（pin reference sim）| W3 复盘出现"sim #6 W1 baseline 锁定 + 后续多 sim 偏移度量"明确需求 |
| **F-C** | per-metric 阈值 override（YAML / CLI） | W3 出现 ≥3 个 metric 阈值 50/200 USDT 不适用 |
| **F-D** | retraction_rate anchor 收紧（行首 regex）| 与 R2-Next-J 联动，与本期无依赖关系 |
| **F-E** | Roundtrip invariant violation 在 Sim 行为层 hardening | W3 数据触发 ≥1 次双 open / 双 close 实例 |
| **F-F** | analyze 输出 "per-trigger" / "per-decision-type" 分维度 sub-table | W3 复盘出现"想看 alert-triggered cycle 比 scheduled-triggered cycle 是否 PnL 不同"类需求 |
| **F-G** | P7 sim_market_snapshot 启动（与 OOS-1 同议题；当前 OOS）| Reaction lag / 方向命中率分析需求出现 |

---

## §9 Acceptance Criteria

| # | AC | 验证方式 |
|---|---|---|
| **AC-1** | 全测试通过：1284 → ~1358-1370（净 +74-86，§7.1 估算范围内）| `uv run pytest -v` |
| **AC-2** | 0 alembic / 0 schema 变动（含 storage/database.py / storage/views.py / storage/models.py 等所有 storage 模块）| `git diff main -- alembic/ src/storage/` 应为空 |
| **AC-3** | `src/cli/` / `src/integrations/` / `src/agent/` / `main.py` 不动 | `git diff main -- src/cli/ src/integrations/ src/agent/ main.py` 应为空 |
| **AC-4** | analyze_sim.py 在 sim #8 上跑 markdown 输出含 3 段 + caveats 段 | manual smoke：`uv run python scripts/analyze_sim.py --session sim_8` |
| **AC-5** | diff_sim.py 在 sim #8 vs sim #7 上跑输出含 `\| Sim A \| Sim B \| Δ \| Δ% \| Flag \|` 列结构 | manual smoke |
| **AC-6** | 28 个 **metric groups** 全部产出（drift guard `test_metric_group_inventory_count_28`）| pytest（注：渲染行数 > 28，因 largest_win/loss、p50/p95、distributions 多行）|
| **AC-7** | Legacy session（R2-7 之前 created_at）fail-fast exit 1 + stderr 友好提示 | pytest test_analyze_legacy_session_rejected |
| **AC-8** | Roundtrip invariant violation stderr print warning + skip + caveat 计数（不抛异常）| pytest test_collect_roundtrips_close_no_lot_warning |
| **AC-9** | Flag 阈值边界值（10%/30%/50/200/5pp/15pp）触发正确 | pytest test_diff_threshold_* |
| **AC-10** | Caveats 段 10 类消息（0 ok cycles / 0 roundtrips / unclosed lot / invariant / liquidation / stale_close_amount / forensic / a==b / cross-symbol / null-pollution）至少 1 条 fixture 触发覆盖 | pytest |
| **AC-11** | sim #9 跑完后能 10 分钟内产出与 sim #8 的标准 markdown diff 报表 | manual user smoke（W3 sim 跑后）|
| **AC-12** | FIFO lot pnl_net 用于 attribution（roundtrip 级 win/loss）；P2 `total_pnl_net` 用 sim realized；partial close 场景两者合法 diverge 不算 mismatch | pytest test_..._partial_close_lot_pnl_diverges_from_sim_weighted + test_..._full_close_lot_pnl_matches_sim_weighted + test_total_pnl_net_uses_sim_realized_not_lot_sum |
| **AC-13** | exit_type 5 enum 全覆盖（market/stop/take_profit/limit/liquidation）；P8 用 Roundtrip.exit_type 不直接 GROUP BY sim_orders | pytest test_collect_roundtrips_*_close + test_exit_type_5_enum |
| **AC-14** | max_drawdown_pct 用 raw `state_snapshot.balance.total_usdt` 不是 free_usdt | pytest test_max_drawdown_pct_uses_total_usdt_not_free |
| **AC-15** | Stale SL/TP close amount 用 `_derive_close_amount` 反推（fee / (filled_price × fee_rate)），fallback 时记 caveat | pytest test_collect_roundtrips_stale_sl_amount_derived_from_fee + test_collect_roundtrips_stale_amount_fallback_to_order_amount |
| **AC-16** | Liquidation roundtrip pnl_gross 用 trade_actions.pnl（sim cap 后）按比例分摊；非 liquidation 仍 lot.entry_px 重算 | pytest test_collect_roundtrips_liquidation_uses_trade_actions_pnl + test_collect_roundtrips_non_liquidation_recomputes_pnl_from_lot_entry |
| **AC-17** | Legacy session naive datetime 时区规范化不抛 TypeError | pytest test_analyze_legacy_session_naive_datetime_normalized |
| **AC-18** | Flag 阈值边界值用 `≥` 含边界（5pp / 10% / 30% / 50/200 USDT 触发对应 flag）| pytest test_diff_threshold_*_inclusive |
| **AC-19** | P4 `avg_fifo_pnl_per_roundtrip = mean(roundtrip.pnl_net)` 与 P3 同口径（不用 P2/P3 跨口径混算）| pytest test_avg_fifo_pnl_per_roundtrip_uses_lot_mean |

