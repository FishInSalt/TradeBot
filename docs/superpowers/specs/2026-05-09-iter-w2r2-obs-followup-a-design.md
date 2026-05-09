# iter-w2r2-obs-followup-a — F7 OKX REST OHLCV helper + F5 label drift guard

## §1 Scope & Goal

| 维度 | 内容 |
|---|---|
| **F7 目标** | 提供 OKX REST OHLCV post-hoc 回填工具，覆盖 P7 ~80% 替代方案（reaction-lag / regime / 方向命中率分析） |
| **F5 目标** | 防 PR #43 v1 同类 row-label drift 复发（`analyze_sim` 漏 emit `diff_sim` 列出的 metric label） |
| **In Scope** | `scripts/fetch_session_ohlcv.py` + `tests/test_fetch_session_ohlcv.py` + `tests/test_label_drift_guard.py` |
| **Out of Scope** | 主流程接口改动 / `BaseExchange.fetch_ohlcv` 加 `since` / `sim_market_snapshot` 表 / 方案 A 共享常量重构 |
| **代码影响** | 无 `src/` 改动；`scripts/` + `tests/` 纯增量 |

### §1.1 议题来源与决策依据

- **F7 = P7 替代方案**：spec `2026-05-09-iter-w2r2-obs-phase2-design.md` §1.1 决议 P7 (`sim_market_snapshot`) OOS-1。sim 跑在 OKX live websocket ticker + REST OHLCV 之上（非纯 mock：REST OHLCV 路径 `src/integrations/exchange/simulated.py:124-128` `fetch_ohlcv` 委托 `self._ccxt`；websocket ticker 路径 `simulated.py:1080` `ccxtpro.okx()` + `:1112` `watch_ticker`），P7 独有痛点是 ticker stream 流过即丢（不可重放）；1m kline 历史 OKX REST 在近年范围内可拉（OKX 文档承诺 "recent years"，1s 仅最近 3 个月；F7 用 1m 不踩短窗限制）。F7 总 ~120-150 行 src（核心拉取循环 ~30 行 + retry/CLI/资源管理 ~90-120 行）替代 ~400 行 P7 主流程改动 + 1 alembic + sim_exchange hook，覆盖 sub-minute 之外的 ~80% reaction-lag 需求。
- **F5 = drift guard**：PR #43 v1 review 抓到 `5field_complete_rate` (analyze) vs `five_field_complete_rate` (diff) drift；当前 36 row labels 无自动化保护。三方案对比（测试 guard / module assert / 共享常量重构）后，方案 A 重构 ~150-200 行 ROI 偏低（drift 频率低、抽象债中等、spec frozen 副作用），选择测试 guard ~40 行 test lean 路径。

### §1.2 接口选型对比

`MarketDataService.get_ohlcv_dataframe` 签名 (`src/integrations/market_data.py:21`) 为 `(symbol, timeframe, limit=100)` ——**未暴露 `since` 参数**。`BaseExchange.fetch_ohlcv` (`src/integrations/exchange/base.py:101`) 同样无 `since`。F7 拉历史时间窗（19h × 1m = 1140 candles）需分页步进，**无法直接复用**现有抽象。

候选路径对比：

| 路径 | 改动 | 主流程影响 |
|---|---|---|
| 直接 `ccxt.async_support.okx().fetch_ohlcv(...)` | F7 自带 ~120-150 行 src（7 个 helper：fetch_session_ohlcv / _resolve_session / _paginate_ohlcv / _to_dataframe / _write_csv / main / __main__）+ ~200 行 test | **零**（推荐） |
| 复用 `MarketDataService` | 需扩展 `BaseExchange.fetch_ohlcv` 加 `since` → 改基类 + `OKXExchange` + `SimulatedExchange` + 调用方 | 改主流程接口 |
| 复用 `MarketDataService` 但只 limit 拉最新 N 根 | 简单 | 零，**但 F7 核心需求废**（拉历史 sim 时段做不到） |

**决议：直接 ccxt**。F7 是一次性 ad-hoc 工具，不应进主抽象层；扩展 `BaseExchange.fetch_ohlcv` 接口属于"主流程级 PR"，触发条件 = P7 真启动或主流程其他场景需要 since（独立议题）。

---

## §2 F7 Architecture

```
scripts/fetch_session_ohlcv.py
├── fetch_session_ohlcv(session_id, timeframe="1m", db_path="data/tradebot.db", output_path=None) -> DataFrame
├── _resolve_session(engine, session_id) -> (symbol, start_ms, end_ms)
├── _paginate_ohlcv(ccxt_client, symbol, timeframe, start_ms, end_ms) -> list[list]   # ccxt raw [ts,o,h,l,c,v]
├── _to_dataframe(rows: list[list]) -> DataFrame   # 即使 rows 为空也用 .astype({...}) 强制 §3.2 dtype
├── _write_csv(df, path)
└── main() / __main__   # CLI thin wrapper (argparse)
```

### §2.1 数据流

1. CLI 解析 `--session/--timeframe/--output/--db`
2. async fn 打开 SQLAlchemy engine → 查 `sessions.symbol/created_at/last_active_at`，得 `(symbol, start_ms, end_ms)`
3. 实例化 `ccxt.async_support.okx()`（**无 api key**，公开 endpoint）
4. 循环 `fetch_ohlcv(symbol, timeframe, since=cursor_ms, limit=100)`，每次以**返回结果中最后一根 candle 的 timestamp + tf_ms** 作为下次 `cursor_ms`（不假设服务端按 100 步进，避免对齐 / gap 漂移）；sleep 0.5s 节流
5. 终止条件：`cursor_ms >= end_ms`，或本次返回为空，或本次返回末根 ts <= 上次末根 ts（防"末根不前进"退化，含 ts 倒退与重复返回同一根两类）。`last_seen_ts: int | None = None` 在循环外初始化；首次循环时 `last_seen_ts is None` 跳过此 check，从第二次循环起生效
6. **去重 + 区间过滤**：合并所有页 → 按 `timestamp_ms` 升序 → 去重（同 ts 取首条）→ 仅保留 `start_ms <= timestamp_ms < end_ms`（半开区间，避免 sim 结束后 candle 污染）
7. 扁平化 → DataFrame（7 列）
8. **若 `output_path is not None`**：写 CSV（`pandas.to_csv`，覆盖）；否则跳过写盘步骤
9. **资源关闭**：`finally` 块中 `await ccxt_client.close()` + `await engine.dispose()`，无论成功 / 异常 / `KeyboardInterrupt`

### §2.2 关键决定

- **不复用 `MarketDataService`** — 接口缺 `since`（§1.2）
- **直接 ccxt 实例化** — 拉公开数据无需 api key，boilerplate 极少
- **DataFrame 返回值** — 让 W3 复盘可在 Python / Jupyter 中 `import` 直接用，CLI 是 thin wrapper
- **半开区间 `[start, end)`** — 过滤条件 `start_ms <= ts < end_ms` 作用于 candle 的**起始 ts**：保留首根起始 ts 在 sim 末尾之前的 candle（其数据可能跨过 sim end），这正是 reaction-lag 分析所需（"alert 触发后第一根完整 candle 的反应"）。剔除的是起始 ts 已 ≥ end_ms 的 candle（纯 sim 之后数据）
- **last-candle-ts 推进** — 不按 `since += 100 × tf_ms` 盲推：OKX 实际可能少返回 (gap / 数据缺) 或对齐到 tf 边界，盲推会丢段或卡死
- **tf_ms 来源 = 硬编码 dict**（与 §3.4 timeframe 白名单同位，不依赖 `ccxt.parse_timeframe`）— 让 6 项 parametrize 测试（AC-F7-4）真正成为 drift guard，而非重复 ccxt 逻辑

### §2.3 Pre-impl gate（实施前必跑）

仓库现有 `BaseExchange.fetch_ohlcv` 不传 `since`，无既有调用样本可参照。OKX REST 原生有 `before/after` 两个分页参数（语义易混），ccxt 内部需把 `since` 正确映射到 OKX 的 "after"（向后翻页拉历史）。

**gate 检验**：实施 commit 前手跑一次 REPL 单调用，确认 `ccxt.async_support.okx().fetch_ohlcv("BTC/USDT:USDT", "1m", since=<某历史 ms>, limit=10)` 返回 ts ≥ since 的 1m kline；若 ccxt 行为意外（如返回最新 而非历史），spec §2.1 数据流需重做（改用 `params={"before": ms}` 等 OKX 原生参数）再继续。

**失败回退 budget**：gate fail → **暂停实施，回到 spec round** 重写 §2.1 数据流；不进入 commit 阶段。即使是小行为偏差（如 since 偏移 1 tf 单位）也走 spec round 而非 in-place edit，避免 spec / impl 漂移。

通过此 gate 才能动 source code，避免铺好 12 次分页 + 测试架构后才发现路径不通。

---

## §3 F7 接口契约

### §3.1 公开 async fn 签名

```python
async def fetch_session_ohlcv(
    session_id: str,
    timeframe: str = "1m",      # 1m / 5m / 15m / 1h / 4h / 1d (ccxt 标准)
    db_path: str = "data/tradebot.db",  # 与 analyze_sim / diff_sim --db default 对齐；不走 load_settings()
    output_path: Path | None = None,  # None = 不写盘，仅返回 DataFrame
) -> pd.DataFrame:
    """Fetch OKX REST OHLCV for a sim session's [created_at, last_active_at) window.

    Returns: DataFrame；window 内 OKX 无数据时返回空（不抛）。完整 schema 与
             空返回契约见 spec §3.2 / §4 表。

    Raises: ValueError if session_id not found or window has zero duration.
            Re-raises ccxt errors after retry exhaustion (transient) or
            immediately (permanent — BadSymbol etc).
    """
```

**资源契约**：函数在 `finally` 块中关闭 `ccxt_client.close()` + `engine.dispose()`，对调用方透明（即使 raise，资源也已释放）。

### §3.2 CSV schema (7 列)

| 列 | 类型 | 说明 |
|---|---|---|
| `timestamp_ms` | int64 | candle 起始 epoch ms |
| `datetime_iso` | str | UTC ISO-8601（pandas helper 派生） |
| `open / high / low / close` | float64 | OHLC |
| `volume` | float64 | base volume |

### §3.3 CLI

```bash
python -m scripts.fetch_session_ohlcv --session <id> [--timeframe 1m] [--output PATH] [--db PATH]
# --db PATH: SQLite file 路径，default "data/tradebot.db"（与 analyze_sim / diff_sim 对齐）
# 默认 output: .working/ohlcv/<label>_<symbol_safe>_<timeframe>.csv
#   sanitize(name) = re.sub(r"[^\w-]+", "_", name).strip("_")[:40]
#                    # 保留 word chars + dash，其他转 _，去首尾 _，截断 40 防文件名过长
#   label = sanitize(sessions.name) if sanitize(sessions.name) else session_id[:8]
#                    # fallback 触发场景：name="" 或全 unsafe chars 致 sanitize 退化为空
#                    # （schema NOT NULL 不禁止空 string；这是真实可能场景，非死代码）
#   symbol_safe = "BTC_USDT_USDT" 替换 / : 为 _
```

### §3.4 timeframe 白名单

`{"1m", "5m", "15m", "1h", "4h", "1d"}` — argparse `choices=` 在 CLI 拒绝非法值；async fn 内部断言双重保护。

---

## §4 F7 错误处理与边界

| 边界 | 行为 |
|---|---|
| `session_id` 不存在 | `ValueError("session not found: <id>")` |
| `last_active_at` 为 None | fallback `updated_at`（schema NOT NULL，必有值；不再二次 None 检查） |
| 时间窗 = 0（`created_at == last_active_at`） | `ValueError("session has zero duration")` |
| OKX REST 首次返回空 | **不报错** — 返回空 DataFrame（7 列 schema 仍存在），由调用方决定如何处理（symbol 历史早于 sim / 数据缺失等场景） |
| OKX REST transient 失败 | **3 attempts total**（首次 + 2 retry），sleep schedule：`try → sleep(1s) → try → sleep(2s) → try → raise`（**2 次 sleep，raise 前不再 sleep**；不继承 `okx.py:75-101 _retry` 的 dead-sleep 行为）；仅捕 `ccxt.NetworkError / ccxt.ExchangeNotAvailable / asyncio.TimeoutError`（`RateLimitExceeded` 是 `NetworkError` 子类自动覆盖）；3 次后 raise 保留原异常 |
| OKX REST 永久错误 | 不 retry（`ccxt.BadSymbol / AuthenticationError / ExchangeError` 等），直接 raise |
| `timeframe` 不在白名单 | argparse `choices=` 在 CLI 拒绝；async fn 内部断言双重保护 |
| 输出文件已存在 | 静默覆盖（最简，符合 "ad-hoc helper" 定位） |
| 输出目录不存在 | `mkdir(parents=True, exist_ok=True)` |
| 函数任意异常 / `KeyboardInterrupt` | `finally` 块保证 `ccxt_client.close()` + `engine.dispose()` 执行 |

---

## §5 F5 测试结构

`tests/test_label_drift_guard.py`（独立文件，预估 ~40 行）

### §5.1 测试形态（白盒调 `_render_*`）

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker
from src.storage.models import Session as SessionModel
from scripts.analyze_sim import _render_pnl, _render_cost, _render_behavior
from scripts.diff_sim import PNL_LABELS, COST_STATIC_LABELS, BEH_STATIC_LABELS
from tests._sim_fixtures import make_session

# pyproject asyncio_mode="auto" — 不写 @pytest.mark.asyncio
async def test_analyze_pnl_emits_all_pnl_labels(engine):
    sid = await make_session(engine, name="drift_guard_pnl")
    # 用 AsyncSession + sessionmaker 拿 ORM 对象（与 scripts/analyze_sim.py:55-71 一致）。
    # engine.connect() + select(SessionModel) + scalar_one() 在 Connection 级别返回的是
    # first column (id: str) 而非 ORM 实体，后续 session.symbol / session.id 会失败。
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == sid))
        session = result.scalars().one()
    output = await _render_pnl(engine, session, [])
    emitted = _parse_label_column(output)
    missing = set(PNL_LABELS) - emitted
    assert not missing, f"analyze_sim _render_pnl missing labels: {missing}"

# 同模式 _render_cost / _render_behavior
```

### §5.2 辅助函数（同文件 ~10 行）

- `_parse_label_column(md_output: str) -> set[str]` — markdown 表抽第 1 列
- `_load_session(engine, sid) -> SessionModel` — AsyncSession + sessionmaker + scalars().one() 拿 ORM 对象（3 个测试共用，避免重复 sessionmaker boilerplate）

> 不另起 `_get_session` 全局命名（与 `analyze_sim.py:48 _resolve_session(engine, key)` 的 by-name 路径命名相似而语义不同）；`_load_session` 是测试模块内部 helper，作用域窄不冲突。

### §5.3 断言与边界

- **单向断言** `analyze_emitted ⊇ diff_static_labels` — 抓"analyze 漏"
- **不抓** "analyze 多 emit"（动态 label / 装饰文本会误报，触发条件 = W3+ 真发生反向 drift）
- **动态 label 处理**：测试只断言 STATIC 部分（`PNL_LABELS / COST_STATIC_LABELS / BEH_STATIC_LABELS`），动态 label（`triggered_by[*] / decision_type[*] / exit_type[*] / per_tool_call_top10[*]`）不入集合比较
- **`per_field_hit_rate` 5 keys**（`has_stance / has_active_commitments / has_this_cycle_delta / has_thesis_invalidation / has_watch_list`）— 虽是 runtime dict 注入，但已 hard-code 在 `BEH_STATIC_LABELS`（`scripts/_sim_metrics.py:621-624` fields list 与 diff_sim STATIC list 并行维护），drift 由本测试间接捕获
- **timeframe 维度**：F5 与 timeframe 无关（label 不依赖 timeframe），无 timeframe 维度

### §5.4 fixture 复用

- `engine` fixture — `tests/conftest.py:26-29`（in-memory SQLite + schema），**白盒直调**
- `make_session` — `tests/_sim_fixtures.py`，返回 sid 直接用
- 区别于 `tests/test_analyze_sim.py` 的 `db_engine` (`conftest.py:90-103` file-based + tmp_path) + subprocess 黑盒模式 — F5 在白盒方向新增，沿用 codebase 已有 `make_session` + 简单断言风格

---

## §6 测试与 AC

### §6.1 F7 测试（`tests/test_fetch_session_ohlcv.py`，预估 ~200 行）

| AC | 测试 |
|---|---|
| AC-F7-1 | session 不存在 → `ValueError` |
| AC-F7-2 | session 时间窗为 0 → `ValueError` |
| AC-F7-3 | mock ccxt 按 `since` 偏移**返回连续 100 条 ts**（base + i × tf_ms，i ∈ [0,100)），**任意非整百窗口**正确拼接 + 单调递增（具体窗口尺寸由测试代码自定，spec 不绑定；与 AC-F7-9 少返回场景互补） |
| AC-F7-4 | `pytest.parametrize` 6 个 timeframe（1m/5m/15m/1h/4h/1d）：mock fetch_ohlcv 返回 2 页 candle，断言下次 `cursor_ms == 上页末根 ts + tf_ms`（tf_ms = {1m:60_000, 5m:300_000, 15m:900_000, 1h:3_600_000, 4h:14_400_000, 1d:86_400_000}）。**用途：drift guard**（与 §2.2 决议"硬编码 dict"配套，防 `tf_ms` 表与 §3.4 白名单失同步） |
| AC-F7-5 | DataFrame 7 列 schema 正确（`timestamp_ms / datetime_iso / open / high / low / close / volume`） |
| AC-F7-6 | CSV 写盘 + 覆盖现有文件 + 自动 mkdir 父目录 |
| AC-F7-7 | **半开区间过滤**：mock 返回 candle 跨过 `end_ms`，结果只保留 `ts < end_ms` |
| AC-F7-8 | **去重**：mock 同一 ts 两次出现（OKX 重叠分页），结果中仅 1 条 |
| AC-F7-9 | **last-candle-ts 推进**：mock 服务端少返回（如 50 而非 100 条），下次 `since` 仍能正确推进至覆盖完整窗口 |
| AC-F7-10 | **transient retry**：mock `ccxt.NetworkError` 抛 2 次后第 3 次成功 → 函数返回正常结果，sleep 序列 = `[1.0, 2.0]` |
| AC-F7-11 | **transient 耗尽**：mock `ccxt.NetworkError` 连抛 3 次 → raise 保留原异常，sleep 序列恰 `[1.0, 2.0]`（raise 前不再 sleep） |
| AC-F7-12 | **永久错误不 retry**：mock `ccxt.BadSymbol` 1 次 → 立即 raise（不进 retry 循环，sleep 序列 = `[]`） |
| AC-F7-13 | **资源关闭**：成功路径下 `ccxt_client.close()` + `engine.dispose()` 各被调一次 |
| AC-F7-14 | **资源关闭异常路径**：mock fetch_ohlcv raise，仍能保证 `close()` + `dispose()` 被调用 |
| AC-F7-15 | **空窗口契约**：mock fetch_ohlcv 第一次即返回 `[]`（OKX 该 symbol 历史空 / 时间窗在 OKX 数据起点之前）→ 函数返回**空 DataFrame**（7 列 schema 仍正确，**dtype 同 §3.2 表**：timestamp_ms=int64 / datetime_iso=object / open/high/low/close/volume=float64；`_to_dataframe` 用 `.astype({...})` 强制即使空 DF），不抛 `ValueError` |

### §6.2 F5 测试（`tests/test_label_drift_guard.py`，预估 ~40 行）

| AC | 测试 |
|---|---|
| AC-F5-1 | `_render_pnl` ⊇ `PNL_LABELS` |
| AC-F5-2 | `_render_cost` ⊇ `COST_STATIC_LABELS` |
| AC-F5-3 | `_render_behavior` ⊇ `BEH_STATIC_LABELS` |

### §6.3 不做的测试

- F7 集成测试（不打真 OKX REST，CI 不稳）
- F5 双向断言（analyze 多 emit 不抓）
- F5 timeframe 维度（label drift 与 timeframe 无关）

---

## §7 提交与 PR 节奏

按 `feedback_plan_doc_commit_first` + TDD `frequent commits` 原则：

| 阶段 | 内容 | Commit 数 |
|---|---|---|
| 1. spec | `docs/superpowers/specs/...-design.md` | 1（spec doc） + 后续 amend 视 plan/impl 阶段发现 |
| 2. plan | `docs/superpowers/plans/...-plan.md` | 1（plan doc） |
| 3. F7 impl (TDD) | 按 plan task-by-task：常量 → _resolve_session → _paginate_ohlcv → tf parametrize → _to_dataframe → 主入口/半开/去重/finally → _write_csv → CLI/sanitize | ~8（每 task 一个 self-contained commit；每 commit 测试自洽，bisectability 不退化） |
| 4. F5 impl | `tests/test_label_drift_guard.py` | 1 |

总计 ~11 commits。每 commit 通过自身 TDD 测试 + 不引入 broken 中间态（`_write_csv` 等 placeholder 不允许跨 commit）。

**分支**：`feature/iter-w2r2-obs-followup-a`
**PR**：单 PR `feat(iter-w2r2-obs-followup-a): F7 OHLCV helper + F5 label drift guard`

---

## §8 不做的（明确 wontfix-by-design 范围）

| 项 | 触发条件 |
|---|---|
| 扩展 `BaseExchange.fetch_ohlcv` 加 `since` 参数 | P7 真启动 / trading 主流程其他场景需要 |
| 共享常量重构 / fetcher map（方案 A） | metric > 70 row labels / 第三 consumer 出现 / W3+ 再发 ≥2 次 drift（详见 `project_f5_drift_guard_decision`） |
| `sim_market_snapshot` 表 / live ticker 持久化（P7） | W3+ ≥2 次 sub-minute / tick 级 reaction-lag 痛点（详见 `project_p7_roi_evaluation`） |
| F1-F4 / F6 | W3 sim 数据真实暴露 ≥2 次（详见 `project_phase2_w3_followups`） |
