# Cycle 崩溃退避重唤 + 自收敛工具不可用可观测化

## 背景与动机

sim #21（`d10b0442`）的 alert-触发 cycle `83c932da`（2026-06-16 08:16–08:19 UTC）以 `execution_status='retry_exhausted'` 收尾，decision/reasoning 空、tokens=0、wall 158.6s。取证（`logs/session_d10b0442-*.log` 第 58906 行）：

```
[cycle aborted — 3 attempts failed: RequestTimeout: okx GET
 https://www.okx.com/api/v5/market/candles?instId=BTC-USDT-SWAP&bar=5m&limit=100]
```

根因链：`get_market_data(timeframe="5m")` 拉取 OKX 5m K 线连续 3 次撞 ccxt 默认 10s `RequestTimeout`。`get_market_data` 对真正的网络拉取（`get_ticker` / `get_ohlcv_dataframe`）**没有 try/except**（唯一只防 `normalize_timeframe` 的 `ValueError`），异常穿透工具 → 抛出 `agent.run` → 被 `src/cli/app.py` 的 cycle 级 3-attempt 重试循环捕获。08:18–08:19 期间 OKX 5m 端点持续退化，3 次 attempt 全撞同一超时 → `retry_exhausted`。同根因还击穿了 sim #21 的 cycle `64070d96`（`ExchangeNotAvailable` ×3，06-15 02:03）。

这暴露两个相互关联但技术独立的问题：

1. **崩溃后的重唤不可靠。** 崩溃的 `agent.run` 被整个丢弃，agent 本该基于完整分析设的 `set_next_wake_at` 永不发生。安静行情下崩溃 = 回落到会话兜底间隔（sim #21 = 60min）。本次只隔 2min 被下个 cycle 接上，纯属行情活跃恰好持续产生 alert/fill 触发（`_interruptible_sleep` 被事件抢醒），**靠运气不是设计**。
2. **自收敛的工具不可用静默无踪。** 与 `get_market_data` 穿透崩溃相对，`get_taker_flow` / `get_recent_trades` / `get_multi_timeframe_snapshot` 等工具 catch 异常后返回 "temporarily unavailable" 降级字符串——这些调用在 `tool_calls` 表里记成 `status='ok'`，与"成功拿到真实数据"无法区分。sim #21 全会话有 12 次降级调用全部淹没在 1498 个 ok 里，唯一捞法是 grep `result` 列。用户无法知道哪些工具 agent 想用却一直拿不到数据——直接侵蚀"工具优化迭代"赖以判断的"agent 实际看到了什么"。

## 范围

**两半同属"工具不可用的处理"一个问题域，一个 iter / 一个 spec。** 焦点落在 CLAUDE.md 的"系统运行机制"（`src/scheduler/` + `src/cli/app.py` 崩溃路径）与 metrics 可观测性。sim-only，不碰 agent loop 决策逻辑、不碰下单路径、不碰 OKX 实盘。

涉及文件：

- `src/agent/trader.py` — `TradingDeps` 新增 `scheduler_interval_min: int` 字段（崩溃路径计算退避封顶需要它，而 `run_agent_cycle` 签名与 `deps` 现有字段都拿不到——见 §1「fallback 来源」）。
- `src/cli/app.py` — wiring 处（`run()` 内 deps 装配，邻近 line 1106 `set_next_wake_fn` 接线）给 `deps.scheduler_interval_min = result.scheduler_interval_min`；`retry_exhausted` 分支（约 line 635）写完 crash 行后，调 `deps.set_next_wake_fn(backoff_min, ctx)` 设退避重唤；新增退避曲线纯函数与 DB 派生的连续崩溃计数。
- `src/services/tool_call_recorder.py` — `BIZ_ERROR_TYPES` 白名单新增 `"source_unavailable"`。
- `src/agent/tools_perception.py` — 12 个网络型工具的「整体不可用」返回点调 `note_biz_error("source_unavailable")`（逐工具见 §2 表）；`get_market_data` 裸 fetch 处加显式崩溃语义注释。
- `tests/test_tool_call_recorder.py` — `test_biz_error_types_drift_guard` 扫描范围从仅 `tools_execution.py` 扩到 `src/agent/`（含 `tools_perception.py`）——否则 perception 里拼错的字面量（如 `source_unavailble`）逃过 drift-guard，而 `note_biz_error` 对未知 type 是 log-error-then-skip（记成 ok），恰是本 iter 要消灭的盲区（见 §2「拼写保护」）。
- `tests/` — 退避曲线 / DB 计数 / 触发条件 / biz_error 打点 / get_market_data 穿透 drift-guard 的 TDD 测试。

不新增 DB 迁移、不动 `MetricsService` 聚合逻辑（现成管道自动接住 `biz_error`）、不新增 config 旋钮。

### 不做（YAGNI / 触发型）

- **调整 cycle 内 3× 重试**。已确认保持不动——cycle `086e5ea2` 正是靠这层从 08:14:31 的 RequestTimeout 中恢复（最终 ok），证明它对瞬时抖动有价值；两层各管一段时间尺度。
- **get_market_data 改降级**。已确认保持一律崩溃（见 §3）。
- **per-timeframe 区分崩溃 vs 降级**。get_market_data 任意 timeframe 失败都崩溃，不按 primary/非 primary 分支。
- **WebUI 聚合"工具健康"面板**（跨 cycle 每工具 source_unavailable 占比）。单次黄标已在 cycle 详情免费可见（见 §2 surface）；聚合占比走 `scripts/tool_call_summary.py`，专门面板留触发型 follow-up。
- **`get_market_news` 双源（FGI+News）可用性观测**。无单一总失败状态，需先裁定 hard-dep / 拆 tool（见 §2 特例）；触发型 follow-up。
- **`get_open_orders:566` ticker 裸调的 partial-degrade 化**。ticker 超时现会崩整 cycle，但 orders 本可独立呈现（sim-local）——疑似 latent 降级缺口（见 §3）；本 iter 不改其行为，触发型 follow-up。
- **ccxt 异常类粒度的 biz_error 分型**（RequestTimeout / ExchangeNotAvailable 各一类）。统一 `source_unavailable`（见 §2）。
- **退避的 thrash 显式上限计数**。曲线封顶 = 兜底间隔后自然收敛回普通调度，无需额外计数器。
- **对 `usage_limit_exceeded` 退避重唤**。该状态是病理死循环，spec 明确不重试。

## 设计

### §1 跨 cycle 崩溃退避重唤

**机制。** `src/scheduler/scheduler.py` 主循环每轮取 `interval = _next_interval（agent 的 set_next_wake，一次性）或 _interval（会话兜底 = scheduler_interval_min）`。`deps.set_next_wake_fn = lambda minutes, ctx: scheduler.set_next_interval(minutes*60, ctx)`（app.py:1106 现成接线）。崩溃路径在 `agent.run` 全部 attempt 失败、写完 crash `AgentCycle` 行之后，调 `deps.set_next_wake_fn(backoff_min, "crash-backoff: <err_class>")` 设下次重唤。

**fallback 来源（关键实现决策）。** 退避封顶 `fallback = scheduler_interval_min`，但崩溃路径所在的 `run_agent_cycle`（app.py:482-491）签名不含它，`TradingDeps`（trader.py:42-46）也只有 `wake_min/max_minutes`，且无法由 `wake_max_minutes` 反推（`_compute_max_wake(x)=min(max(4x,60),180)` 在 x≤15 恒 60、x≥45 恒 180，两端不可逆）。**解法：给 `TradingDeps` 加 `scheduler_interval_min` 字段，wiring 时赋值，崩溃路径读 `deps.scheduler_interval_min`。** 计算仍留在 `run_agent_cycle`，不改其 `return None` 契约。

- `set_next_interval` 本身不 clamp，故退避值绕过 tool 层的 bound 校验也无冲突（tool 层对 `deps.wake_min/max_minutes` 是 **explicit-reject 返回错误串**，非 silent-clamp，符合原则 1；崩溃路径直接走 `set_next_wake_fn` lambda，不经该校验）。退避值由构造落在 `[floor, scheduler_interval_min]`。
- 崩溃路径在所有 attempt 之后运行 → 即便某 attempt 曾调过 `set_next_wake_at` 设过 `_next_interval`，退避值在最后覆盖它（语义正确：丢弃半截分析的 wake）。

**触发条件。** 仅 `execution_status='retry_exhausted'`。**排除** `usage_limit_exceeded`（病理死循环，快重唤只会重复烧钱）。

**退避曲线（纯函数）。**

```
backoff_min(n, fallback) = min(fallback, floor · 2^(n-1))
floor                    = min(2, fallback)
```

- `n` = 连续 `retry_exhausted` 次数（≥1）。
- `fallback` = `scheduler_interval_min`（会话兜底间隔，sim #21 = 60）。**封顶是兜底间隔，不是 `wake_max_minutes`（=180）**——崩溃后最坏退回会话正常巡检节奏，绝不更慢。
- 序列示例：fallback=60 → `2, 4, 8, 16, 32, 60(封顶), 60…`；fallback=1 → floor 被 `min(2,1)` 压成 1 → 退避恒 1（no-op，本就每分钟巡检）；fallback=180 → `2, 4, …, 128, 180(封顶)`。

**连续崩溃计数 `n`（DB 派生）。** 不持久化计数器字段。崩溃时写完 crash 行后，查本会话按 **`id` 倒序**（`AgentCycle.id` 是自增 PK，严格单调；优于 `created_at` DESC——后者在 SQLite `DateTime(timezone=True)` 下读回 naive 易踩 `feedback_sqlite_naive_datetime_readback` 坑【恰是 sim#21 resume 根因】，且同秒并列无序）从最新 cycle 起连续为 `retry_exhausted` 的 cycle 数（遇首个非 `retry_exhausted` 即止）= `n`（含刚写的这一行）。理由：无状态、resume/重启鲁棒（sim 会话频繁 pause/resume）、单一真相源（cycle log 即状态）、查询仅在崩溃路径跑（极罕见）。

**收敛即降级。** 连崩多次 → 退避值 = 兜底 → 自动回归普通 scheduled 调度，无需额外 thrash 上限。事件（alert/fill）经 `_interruptible_sleep` 仍随时抢醒，真有信号不被退避拖住。首个非 `retry_exhausted` cycle 使 `n` 重置（DB 派生天然成立），重唤交回 agent 的 `set_next_wake_at`。

### §2 自收敛工具不可用可观测化

**机制已半成品。** `ToolCallRecorder.wrap_tool_execute` 是三态：`ok`（正常返回）/ `error`（异常穿透，recorder catch）/ `biz_error`（工具显式调 `note_biz_error(type)`，经 ContextVar 在返回后被 recorder 读取）。`note_biz_error` 现仅接到 `tools_execution.py` 的 **4 处调用 / 3 个 type**（`invalid_threshold_range` ×1 / `invalid_alert_id_format` ×2 / `alert_not_found` ×1，均为执行类校验）；**无任何自收敛感知工具在降级分支调它**，故记 `ok`。

下游管道**自动接住** `biz_error`，无需改动：

- `MetricsService.get_tool_call_summary`：`ok_count = (status=="ok")`，`error_count = count - ok_count`（含 biz_error），`error_breakdown` 按 `error_type` 计数。
- `v_alert_lifecycle`：已用 `SUM(CASE WHEN status='biz_error' ...)`。
- `scripts/tool_call_summary.py`：读上述 summary。

**改动。**

1. `BIZ_ERROR_TYPES` 白名单新增 `"source_unavailable"`。
2. 12 个网络型工具在**"整体不可用"返回点**调 `note_biz_error("source_unavailable")`（逐工具见下表）。
3. 扩 drift-guard 扫描到 `src/agent/`（见「拼写保护」）。

**error_type 用单一 `"source_unavailable"`。** 不用 ccxt 异常类名——从 agent / 可观测视角，相关事实是"这个工具拿不到它的数据"，具体 RequestTimeout vs ExchangeNotAvailable 是 infra 噪声（tools 已 `logger.exception` 记类名；agent-facing 降级字符串里仍保留类名不变，如 "Taker flow temporarily unavailable (RequestTimeout)"）。白名单小、metric 干净。

**打点规则：只在"因上游 outage 导致无可用数据"的总失败返回点打点。** 触发 = 工具因**外部源不可达**而整体无数据可返回，具体形态两种：①`except` catch 到异常（如 taker_flow 1338）；②上游以 **sentinel 表达 outage**（fetch 返回 `None` / 全 `None`，如 stablecoin 1935、etf_flows 1888 的 `btc is None and eth is None`、macro_calendar 的 `macro_events is None`）——二者语义相同（源拿不到），都打点。**不打点**的三类，均保持 `ok`：

- **部分降级**：部分子段不可用但工具仍返回可用数据（MTS 部分 TF / HTF per-TF / price_pivots swing / position Risk&Exit / open_orders 距离）→ agent 拿到了可用数据。
- **`insufficient data`**：合法的数据可得性事实（新市场 / 历史短），非故障。
- **schema-drift / 空结果**：源**可达且有响应**但数据不可映射（如 stablecoin 1940 `not result["coins"]`，DefiLlama 改名 USDT→USDT0；代码以不同文案 "Data unavailable (no tracked symbols)" 区别于 outage 的 "Temporarily unavailable"）→ 属数据质量非可用性，归 `ok`（同 `insufficient data`）。若日后要观测 schema-drift，另立 type，不混入 `source_unavailable`。

**20 个注册 perception 工具的完整处置**（`REGISTERED_TOOL_NAMES` "感知 (20)"）。锚点行号对齐 worktree HEAD `0680cc9`，精确打点位置在 TDD 钉死：

| 工具 | 处置 | 总失败打点（def 行 / 返回行） |
|---|---|---|
| `get_market_data` | **CRASH**（§3，穿透，不记 biz_error） | 52 |
| `get_position` | skip — real-net ticker 等在 324-338 try/except 内 **partial-degrade**（core Position+PnL 经 `_render_position_core` 在 gather 前已建，失败仍返回）；`fetch_positions`(248) 裸调但 sim-local（SimulatedExchange，无网络失败面）| 228 |
| `get_account_balance` | skip — 仅 `fetch_balance`(511) sim-local，无 real-net 调用 | 510 |
| `get_open_orders` | skip（biz_error） — orders(562) sim-local；但 real-net ticker(566) **裸调，超时会崩溃**（§3 同型，记 `error` 非 masked-ok；"(ticker unavailable)" 仅处理 `current<=0` 零值非抛错；见 §3）| 558 |
| `get_trade_journal` | skip — `metrics.compute`(DB) + per-order `fetch_order` 在 try/except（partial enrichment soft-fail）| 606 |
| `get_active_alerts` | skip — 纯 state 读，无 fetch / 无降级分支 | 685 |
| `get_performance` | skip — `fetch_balance`(sim) + `metrics.compute`(DB)；"Stats unavailable" 是 legacy/invariant 数据态说明，非 source outage | 718 |
| `get_market_news` | **skip（特例）** — 见下「get_market_news」 | 902 |
| `get_exchange_announcements` | POINT | 1005 |
| `get_macro_calendar` | POINT | `macro_events is None` 分支 ~1044 |
| `get_taker_flow` | POINT — 仅主 rubik fetch | 1338（**非** 1356 OHLCV 子段） |
| `get_derivatives_data` | POINT | 1403（all 3 sources failed） |
| `get_higher_timeframe_view` | POINT — 仅 ticker 失败 | 1540（**非** 1564 per-TF） |
| `get_macro_context` | POINT ×2 | 1738（snapshot 抛错）+ 1810（all sources） |
| `get_etf_flows` | POINT | 1888（**非** 1857 per-label 子段） |
| `get_stablecoin_supply` | POINT ×2 | 1929（异常 catch）+ 1935（`result is None` = 上游 outage sentinel）；**非** 1940（`not result["coins"]` schema-drift → ok，见规则）|
| `get_order_book` | POINT | 1996 |
| `get_recent_trades` | POINT | 2109 |
| `get_multi_timeframe_snapshot` | POINT ×2 — 仅 ticker / all-TF | 2212 + 2237（**非** 2261 per-TF） |
| `get_price_pivots` | POINT | 2516（**非** 2540 swing 子段） |

**`get_market_news` 特例 → skip（V1）。** 它无单一总失败返回点：唯一 early return 是 `deps.news is None`（"not configured"，永久配置缺失非瞬态故障，不打点）；FGI（948）与 News（974）是**两个独立软信号源的子段降级**，恒 `return "\n\n".join(sections)`。两者无 hard-dep 主从关系，故不存在"工具整体不可用"的单一状态——"News 挂但 FGI 在"仍是有可用数据。强行加"FGI 且 News 双挂 → biz_error"需先裁定 FGI-only 算不算可用，属新判断、且与"只打现有总失败点"框定相左。**V1 不打点**；若后续要观测 news-feed 可用性，作触发型 follow-up（可能拆 tool 或加 per-source 计数，呼应 news tool audit memory）。

**拼写保护（M3）。** `test_biz_error_types_drift_guard` 现只扫 `tools_execution.py`，而 `note_biz_error` 对未知 type 是 **log-error 后 skip（记成 ok）**——perception 里拼错字面量会逃过 drift-guard 并静默落回盲区。故扩扫描到 `src/agent/`（含 `tools_perception.py`）。该 drift-guard + 测试 #4（per-tool mock 断言 `status='biz_error'`）共同构成拼写防线，两者都 load-bearing。

**预期指标位移（m5）。** 把 `source_unavailable` 从 `ok` 重标为 `biz_error` 后，受影响工具在 `get_tool_call_summary` 的 `error_count`/`error_rate` 会台阶式上升（`error_count = count - ok_count`）。**这是本 iter 的预期效果，非回归**——但跨 sim 的 error_rate baseline / diff 会看到跳变，分析时注意 landing 时点（呼应 cross-sim 不可比教训）。

**surface（两层，分清在/不在 scope）。**

- **单次可见 — WebUI，免费且 in-scope。** `ToolCallRow`（webui/schemas.py）已暴露 `status` + `error_type`，且**两条渲染路径都已接 `biz_error`**：`ReactTimeline.vue::statusType`（`biz_error→"warning"`）与 `CycleDetailPanel.vue` 扁平回退表（同口径 + `${status} · ${error_type}` 列）。故降级调用打 `biz_error` 后，cycle 详情里该工具卡自动从绿 `ok` 变**黄 `biz_error · source_unavailable`**，与成功调用一眼可分。**零前端改动**——现有 4 个 `tools_execution.py` biz_error 已在走这条渲染（活路径，非休眠代码），本 iter 仅在验收时用真实数据确认 source_unavailable 同样渲染。
- **聚合 — 走脚本，WebUI 面板 defer。** 跨 cycle 的"工具 X：调 N 次、M% source_unavailable"由现成 `scripts/tool_call_summary.py` / `get_tool_call_summary` 回答；WebUI 聚合"工具健康"面板留触发型 follow-up（见「不做」）。

### §3 get_market_data 崩溃语义显式化

`get_market_data` 保持对 fetch 异常**一律穿透崩溃**（已确认：primary 市场数据不可用时让 agent 在"看不见市场"下硬决策更糟；由 §1 crash-backoff 恢复）。但当前"崩溃"是意外（碰巧没 try/except）而非设计。显式化：

1. 在 `tools_perception.py` 裸 fetch 处（`get_ticker` / `get_ohlcv_dataframe` 调用点）加注释，说明 primary 市场数据不可用必须 abort cycle、由 crash-backoff 重唤恢复——故意不 catch，与 §2 的自收敛策略明确区分。
2. 加 drift-guard 测试断言 get_market_data 在底层 fetch 抛网络异常时**会穿透**（不降级、不记 biz_error），防将来被"顺手加降级"而静默改变行为。

**其他裸 real-net fetch（诚实记录，V1 不改行为）。** `get_market_data` 是 sim#21 **观察到**的 real-network 崩溃，但**不是结构上唯一**：`get_open_orders:566` 的 `get_ticker` 同样裸调 real-net（`deps.market_data`），ticker 超时会穿透崩溃（其 "(ticker unavailable)" 仅处理 `current<=0` 零值、不接抛错）。其余账户/状态工具的裸 fetch（`get_position:248`、`get_account_balance:511`、`get_performance` balance）读 `deps.exchange`（SimulatedExchange，sim 下无网络失败面），实践中不崩。V1 的显式注释 + drift-guard **只覆盖 `get_market_data`**（empirically-implicated）；`get_open_orders` 的 ticker 崩溃属同型、行为上 crash→backoff 自洽，但它本可 partial-degrade（orders 本就 sim-local 可得）——疑似 latent 降级缺口，列入「不做」作触发型 follow-up，本 iter 不改其行为、不加 biz_error。

### 失败语义与边界

- 崩溃路径 `deps.set_next_wake_fn` 可能为 `None`（如非交互 / 单测路径未接线）：设退避前判空，None 则跳过（退回默认 `_interval`），不抛错。
- DB 派生计数查询自身失败：fail-isolated，回退到 `floor`（首次退避值），不让计数查询错误再次击穿崩溃路径。
- 连续计数按 `id` 倒序，跨触发类型（alert/scheduled/conditional）的连续 `retry_exhausted` 都计入（计数只看 status，不区分触发来源）。

## 测试

TDD，断言前缀 / 子串匹配以兼容 sibling iter（per `feedback_parallel_subagent_cross_iter_tests`）：

1. **退避曲线纯函数** — fallback ∈ {1, 60, 180} × n ∈ {1..7}：验 floor clamp（fallback=1 → 恒 1）、翻倍、封顶 = fallback。
2. **DB 派生 `n`** — 构造尾部连续 retry_exhausted + 中间夹 ok 的 cycle 序列，验"末尾连续"计数遇首个非 retry_exhausted 即止。
3. **触发条件** — retry_exhausted 调 `set_next_wake_fn`（值 = 曲线输出）；`usage_limit_exceeded` **不**调；`set_next_wake_fn=None` 不抛。
4. **biz_error 打点（per-tool）** — 对 12 个 POINT 工具各 mock 底层抛异常，断言 `tool_calls.status='biz_error'`、`error_type='source_unavailable'`（覆盖完整性是拼写防线之一，§2「拼写保护」）；其中 outage-sentinel 路径单独验（stablecoin `result=None`→1935、etf_flows `btc=eth=None`→1888、macro_calendar `macro_events=None`）也记 biz_error。**保持 `ok` 的反例**：部分降级（MTS 部分 TF / HTF per-TF / price_pivots swing / position Risk&Exit / open_orders 距离）；`insufficient data`；**schema-drift（stablecoin 1940 `not result["coins"]`）**；`get_market_news` 双源降级；`deps.news is None`（配置缺失）。
5. **get_market_data 穿透** — mock fetch 抛 `RequestTimeout`，断言异常穿透（不返回降级字符串、不记 biz_error）。`get_open_orders:566` ticker 抛错同样穿透（确认其行为未被本 iter 改动）。
6. **白名单 drift-guard** — `source_unavailable` ∈ `BIZ_ERROR_TYPES`；且 `test_biz_error_types_drift_guard` 扫描范围含 `tools_perception.py`（扩到 `src/agent/`）后仍全绿（perception 新增字面量全部 ∈ 白名单）。
7. **WebUI 单次可见性（验收，真实数据）** — 用一条 `source_unavailable` 调用确认 cycle 详情渲染黄 `biz_error · source_unavailable` 标签（ReactTimeline + 扁平回退两路径）。渲染本身零改动、由现有 biz_error 路径保证，此项为端到端确认而非新单测。
