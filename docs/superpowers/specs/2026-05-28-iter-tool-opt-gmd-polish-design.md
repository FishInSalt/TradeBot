# iter-tool-opt-gmd-polish — design spec

## 1. 议题缘起

2026-05-28 对 `get_market_data` 做 single-tool audit（`.working/tool-audits/2026-05-28-get_market_data.md`），基于 sim #12 paused snapshot（287 calls / 248 cycles / primary tf=15m，session `f0f7b24f`）。Audit 实证 GMD 在该 session 是**调用频次最高的工具**且整体高 adoption，但浮出 6 项可优化议题，集中在 OHLCV table 渲染层与 Period summary section 的字段 adoption 分化。

### 1.1 Audit 关键量化（attribution-by-pairing，D3b self-validation 通过）

| 指标 | 量化值 |
|---|---|
| 总调用 / cycles | 287 / 248 |
| 错误率 | 0.3% (1 / 287，RequestTimeout 自动 retry 成功) |
| 计算正确性 spot-check (cycle f904 / 06:30 closed bar) | 16/16 metrics OK，最大偏差 0.76% (.2f rounding 边界) |
| OHLCV table 时刻引用 ("HH:MM candle") | 260/270 = 96.3% |
| 24h H/L 引用 | 147/270 = 54.4% |
| RSI 70% / `MACD ∪ Histogram` 58%（同 reasoning 段命中合并，非独立 metric） | 见 audit §1 D3 |
| 单 candle vol/SMA 手算 ratio | **80/270 = 29.6%（systematic，跨 18/20 log windows）** |

### 1.2 议题清单（in scope 6 / out 2 / 1 audit-wontfix 承继）

> **编号注**：本表使用 **spec-local 编号 1-7**（brainstorm Q1-Q7 决议顺序）。**spec 编号与 audit §2 编号不直接 1:1 对应**（spec 议题 4-6 与 audit 议题 4-6 错位，audit 议题 6 是修订时新增的 Period summary 字段拆分议题）。详见紧接 §1.3 spec↔audit traceability 表。

| spec # | 议题 | 维度 | 优先级 | 决议 |
|---|---|---|---|---|
| 1 | OHLCV table 缺单 candle vol/SMA(20) ratio 列 → 30% 手算闭环 | Adoption / 原则 5 接口闭环 | **P1** | 加 `RVol(×SMA20)` 列（A3） |
| 2 | in-progress candle 时间窗口提示缺失（cycle 2c09 outlier 浪费 3 calls / 30s） | 可读性 / 原则 7 表达友好 | P2 | OHLCV header 加完整指示（B1） |
| 3 | `<N>-candle High-Low` (display window) 1.1% adoption，agent 心智偏好时间锚定 | Adoption / 原则 4 信号补齐 | P3 | 删除（C1） |
| 4 | Period summary `Avg range` 仅 ~3% adoption（Net Δclose 20-25% / Avg vol 10-15% 保） | Adoption / 原则 7 | P3 | 只删 Avg range（D1） |
| 5 | `Net Δclose` label / docstring 跟随 spec 议题 3+4 改动整段重写 | 可读性 / 原则 7 | P3 | docstring 整段重写（E3） |
| 6 | `candle_count` floor=10 silent clamp 未在 docstring 明示（cap=80 已 implicit） | 可读性 / 原则 1 | P3 | docstring 顺便明示（F1） — 仅覆盖 docstring fact-only 明示路径，clamp 行为本身按 audit 议题 5 决议保留不动 |
| **7a** | **multi-TF GMD pair (15m+5m) 37/38 multi-call cycles (97%) 是 agent 绕 MTS workaround** | — | — | **G1 out-of-scope** — 根因在 MTS 信号缺口，作 MTS audit backlog（见 §2.6） |
| **7b** | **Bid/Ask spread 3.3% adoption 但保留作 limit-order placement 必要字段（承继 audit 议题 4）** | — | — | **wontfix-by-cost** — Ticker 段单行 cost 低，limit-order 路径仍需 |

### 1.3 Spec ↔ Audit 议题编号 traceability

| spec # | spec 含义 | audit §2 # | audit 含义 |
|---|---|---|---|
| spec 1 | RVol 列 (P1) | audit 议题 1 | vol/SMA ratio 接口闭环 ✓ 直接对应 |
| spec 2 | in-progress hint (P2) | audit 议题 2 | in-progress candle hint ✓ 直接对应 |
| spec 3 | 删 N-candle H-L (P3) | audit 议题 3 | N-candle High-Low ✓ 直接对应 |
| spec 4 | 删 Avg range (P3) | audit 议题 **6**（修订新增） | Period summary 字段拆分 / Avg range dead — 不同编号 |
| spec 5 | Net Δclose docstring 重写 (P3) | (无独立 audit 议题) | brainstorm Q5 决议（E3），跟随 spec 3+4 改动 |
| spec 6 | clamp docstring 明示 (P3) | audit 议题 **5** | clamp 本身 wontfix-by-design；spec 6 仅覆盖 docstring 明示路径 — 不同编号 |
| spec 7a | multi-TF GMD pair (out) | (audit 附录提及) | 未单立议题，转 MTS backlog |
| spec 7b | Bid/Ask wontfix-by-cost | audit 议题 **4** | Bid/Ask spread (3.3% adoption) — 不同编号但语义承继 |

### 1.4 议题 1 实证强信号（systematic 跨 cycle，非 locality）

源码 `src/agent/tools_perception.py:140-154` 只对**最新一根** closed bar 输出 `Last bar vol: 959.3 (0.50× SMA(20) avg)`，OHLCV table 中其他 bar 只有 binary `vol↑` marker（threshold > 2× SMA(20)）。Agent 在 reasoning 中频繁手算单 candle vol/SMA(20) ratio：

- L11389: `"Volume 3,823.5 (1.56× SMA avg)"`
- L12424: `"volume 516.9 (0.22× SMA)"`
- L52803: `"Volume: 2,645.9 (2.32× avg) — vol↑"` — marker 已亮仍手算
- L56491: `"volume was very low (708.5, 0.46× SMA20 avg)"`

80 hits 跨 18/20 log windows，**非 cycle-locality**。Agent mental model 是连续 ratio 而非 binary marker——同一信号在两 representation 之间有 gap，agent 必须自己除 SMA(20) 闭环。

### 1.5 议题 3 重要 caveat

议题 3 删除的字段在 286 paired cycles 中数据值与 24h range 显著不同——N-range / 24h-range 中位数 44%，**54.2% cycles 提供独立信息**。信号本身**客观有价值**，但 agent 1.1% adoption 反映呈现方式（纯数值边界 vs 时间锚定 `swing high` 25.2% / `X-hour high` 7.8%）与 agent 心智模型不匹配。删除是**信号路径修剪**，不是"信号无价值"。

**决议根据**：adoption 失败本身是 signal-routing 错位的实证；24h H/L (54.4% adoption) 已承担相同 anchor 角色（"价格在 24h 区间何处"），删除是**去重而非信号丢失**。原则 4 信号补齐的 "underlying data 没被丢弃才考虑新工具" 在本议题语境是**承担方迁移**（N-candle row → 24h H/L + OHLCV table 极值），不是单纯"adoption 低就删"。

## 2. 设计决策

### 2.1 议题 1：`RVol(×SMA20)` 列

**渲染 contract**：在现有 `Vol` 列右侧、`Markers` 列左侧新增 `RVol(×SMA20)` 列。每行渲染 `<vol_at_bar> / <SMA(20) at bar>` 的 ratio，格式 `<X.XX>×`，含 × 后缀。

**列宽 / 对齐 contract**：
- Header label `RVol(×SMA20)`（12 字符）右对齐占 12 列：`f"{'RVol(×SMA20)':>12}"`
- 数据行格式 `f"{ratio:>11.2f}×"` —— 数值 7 字符（如 `   3.90`）+ × 后缀 1 字符 + 右对齐占满 12 列总宽度，与 header 对齐
- NaN / 0 边界占位 `f"{'—':>12}"`（U+2014 em dash）保持列宽

**算法**：在现有 `vol_sma = df_closed["volume"].rolling(20).mean()` 基础上，每个 display row 取 `vol / vol_sma_at_idx`。当 `vol_sma_at_idx` 为 NaN / 0 时渲染 `—`：
- **满载场景**（exchange 返回 ≥ candle_count + 50 bars，即 `fetch_limit` full case，line 109-111 `available_closed >= candle_count + 50` 分支）：display window 内均有 SMA(20) 值，`—` fallback 不触发
- **Degraded path**（exchange 返回 < candle_count + 50，line 112 `display_count = max(10, available_closed - 50)` 分支）：display window 前段仍可能 NaN（若 `available_closed - 50 < 20`），由 `—` fallback 覆盖

**与现有 idiom 一致性**：
- Header 显式标分母 `(×SMA20)`，避免 agent 反查 Market Context section "Last bar vol X× SMA(20) avg"。
- 数值带 × 后缀，与 agent 手算 idiom 一致（"1.56× SMA avg" / "0.22× SMA"）。

**与 Market Context section `Last bar vol` 行的关系（redundancy-by-design）**：

新增 RVol 列后，最新一根 closed bar 的 RVol 数值与 Market Context 行 `Last bar vol: 373.0 (0.28× SMA(20) avg)` **同一信号源**。Spec **选择保留** Market Context 行（不删除），理由：

1. **Narrative anchor 角色**：Market Context section 是 prose-style summary（与 OHLCV table 的 row-style 互补），agent 在 cross-cycle 摘要 / "what's notable about latest bar" 类 reasoning 中倾向引用 prose 形式。Audit 24h H/L 54% adoption + 部分 reasoning 引用 "last bar vol N.NN× SMA" verbatim 显示 narrative-anchor 路径有 adoption。
2. **Redundancy-by-design**：与议题 3 "删 N-candle H-L" 不冲突 —— N-candle row 删除是因为 **adoption 失败 + 24h H/L 已承担同 anchor 角色**（路径迁移）；Last bar vol 保留是因为 **prose vs table 双路径**承载不同 narrative 风格的 adoption，**两者形态不同不是 pure 冗余**。
3. **Cost 边际**：Market Context 行单行 ~40 chars/call × 287 = ~11K chars session-wide，相对议题 1 加 RVol 列 ~+80-120K 渲染净增是次量级，cost-by-cost 不值得动。

**Future trigger**：若 W4+ sim 实证 Market Context "Last bar vol" 行 adoption 进一步下降（< 5%），可作 follow-up mini-iter 候选议题（"删 Market Context Last bar vol 行，RVol 列完整承担"）；本 iter 保留不动。

**与 `vol↑` marker 关系**：
- Marker 保留作 visual scan acceleration（一眼找异常 candle）
- 当 RVol > 2（严格 >，与源码 `volume > 2 * vol_sma_at` 一致；RVol == 2.0 不触发）必伴随 `vol↑`，agent 看到两者强化（不是冲突），且 marker 提供 binary "outlier yes/no" 的快读路径
- `range↑` marker 同保留，本 iter **不**新增 `RngR` 列（audit 实证 range/ATR 手算 hand-compute 未达 systematic 信号）

### 2.2 议题 2：OHLCV header 加 in-progress 完整指示

**渲染 contract**：`Recent Candles` section header 拓展为：

```
=== Recent Candles (15m, last 20, oldest-first; in-progress 20:30 still open, closes at 20:45) ===
```

**算法（tf → duration 映射，calendar-aware；CCXT 全集覆盖）**：

| tf | duration 计算 | open 渲染示例 |
|---|---|---|
| `1m` / `3m` / `5m` / `15m` / `30m` | `pd.Timedelta(minutes=N)` | `HH:MM` |
| `1h` / `2h` / `4h` / `6h` / `8h` / `12h` | `pd.Timedelta(hours=N)` | `MM-DD HH:MM` |
| `1d` / `3d` | `pd.Timedelta(days=N)` | `YYYY-MM-DD` |
| `1w` | `pd.Timedelta(weeks=1)` | `YYYY-MM-DD` |
| `1M` | `pd.DateOffset(months=1)` **（calendar-aware；不可用 Timedelta：月长 28-31 天不固定）** | `YYYY-MM` |

**timestamp 输入 dispatch**（复用现有 `tools_perception.py:164-168` isinstance 二分逻辑，避免 `pd.Timestamp(<datetime>, unit="ms")` 行为不稳问题）：

```python
def _to_pd_timestamp_utc(ts_val) -> pd.Timestamp:
    """Coerce OHLCV timestamp (epoch ms int/float OR datetime) to tz-aware pd.Timestamp UTC.
    Mirrors the dispatch at tools_perception.py:164-168."""
    if isinstance(ts_val, (int, float)):
        return pd.Timestamp(ts_val, unit="ms", tz="UTC")
    ts = pd.Timestamp(ts_val)
    return ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
```

**Algorithm**:
```python
# CCXT 全集（来自 OKX ccxt timeframes / src/integrations/exchange/ 已实测枚举）
TF_OFFSETS = {
    "1m":  pd.Timedelta(minutes=1),  "3m":  pd.Timedelta(minutes=3),
    "5m":  pd.Timedelta(minutes=5),  "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h":  pd.Timedelta(hours=1),    "2h":  pd.Timedelta(hours=2),
    "4h":  pd.Timedelta(hours=4),    "6h":  pd.Timedelta(hours=6),
    "8h":  pd.Timedelta(hours=8),    "12h": pd.Timedelta(hours=12),
    "1d":  pd.Timedelta(days=1),     "3d":  pd.Timedelta(days=3),
    "1w":  pd.Timedelta(weeks=1),
    "1M":  pd.DateOffset(months=1),
}

last_closed_dt = _to_pd_timestamp_utc(display_df["timestamp"].iloc[-1])
offset = TF_OFFSETS.get(timeframe)
if offset is None:
    # Degraded fallback: 未识别 tf 跳过 in-progress hint 而非 crash，与现有
    # tools_perception.py:175 default fallback 行为保持一致（不收紧 backward compat）
    in_progress_hint = ""  # OHLCV header 渲染为原有格式（无 in-progress 段）
else:
    in_progress_open = last_closed_dt + offset
    in_progress_close = in_progress_open + offset
    in_progress_hint = f"in-progress {fmt(in_progress_open)} still open, closes at {fmt(in_progress_close)}"
```

时间格式 helper `fmt(dt, tf)` 与表内 `time_str` **同 dispatch 表**（避免风格分裂），但现有 `tools_perception.py:169-175` `time_str` 只 3 档（1m/5m/15m → `HH:MM` / 1h/4h → `MM-DD HH:MM` / else → `%Y-%m-%d`），漏 3m / 30m / 2h / 6h / 8h / 12h / 3d / 1w / 1M。**本 iter 同步扩展 `time_str` dispatch** 与 in-progress `fmt` 共用一份 lookup：

```python
def _fmt_candle_time(dt: pd.Timestamp, tf: str) -> str:
    tf_lower = tf.lower()
    if tf_lower in ("1m", "3m", "5m", "15m", "30m"):
        return dt.strftime("%H:%M")
    if tf_lower in ("1h", "2h", "4h", "6h", "8h", "12h"):
        return dt.strftime("%m-%d %H:%M")
    if tf_lower in ("1d", "3d", "1w"):
        return dt.strftime("%Y-%m-%d")
    if tf_lower == "1M":
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y-%m-%d")  # degraded fallback for unknown tf
```

OHLCV 行渲染（`time_str = _fmt_candle_time(dt, timeframe)`）和 in-progress hint 渲染都调同一 helper，保证风格一致。扩展 `time_str` dispatch 计入 §4 行数估算（+~8 行 helper + 调用点替换）。

**Edge case**：
- `display_df.empty` → 保持现有 "Range: N/A" 路径，header 不加 in-progress 指示
- `fetch_ts` 与 last closed bar 间隔已 > tf duration（罕见，exchange catch-up 时段）→ 仍按 `last_closed + offset` 算 in-progress 开盘——这是 agent 当下能消费的真实状态
- `timeframe` 不在 `TF_OFFSETS` 字典 → **degraded fallback**：跳过 in-progress hint 渲染（header 回到无 in-progress 段格式），**不 raise**。保持与现有 `tools_perception.py:175 default %Y-%m-%d fallback` 一致的 backward-compat 行为，不收紧 API contract

**与 cycle 2c09 outlier 关系**：cycle 2c09 在 30 秒内调 GMD 3 次（20:36:43, 20:36:58, 20:37:13）寻找 "20:30 closed candle"——本 iter 后 agent 第一次 GMD 就看到 `in-progress 20:30 still open, closes at 20:45`，从源消除该类 confusion。

### 2.3 议题 3：删除 Market Context 段 `<N>-candle High-Low` 行

`src/agent/tools_perception.py:148-153` 当前：

```python
if not display_df.empty:
    ctx_lines.append(
        f"{display_count}-candle High-Low: {display_df['low'].min():.0f} — {display_df['high'].max():.0f}"
    )
else:
    ctx_lines.append("Range: N/A")
```

**改动**：整段删除（包括 else 分支）。Market Context 段保留 `ATR(14)` 和 `Last bar vol` 两行。

### 2.4 议题 4：删除 Period summary `Avg range` 行

`src/agent/tools_perception.py:199-217` 当前 3 行：

```
Avg vol:            last 5 {avg_vol_last:.1f} / prior 5 {avg_vol_prior:.1f} ({vol_ratio:.2f}×)
Avg range (H-L):    last 5 {avg_rng_last:.1f} / prior 5 {avg_rng_prior:.1f} ({rng_ratio:.2f}×)
Net Δclose:         last 5 {net_delta_last:+.1f} USDT / prior 5 {net_delta_prior:+.1f} USDT
```

**改动**：删 `Avg range` 行 + 相关计算（`avg_rng_last` / `avg_rng_prior` / `rng_ratio`）。保留 `Avg vol` + `Net Δclose`。

### 2.5 议题 5+6：docstring 整段重写（path A 源 + path B `GET_MARKET_DATA_DESCRIPTION` 同步）

**约束 1：Args section 保持 google docstring 风格不动**。docstring 主体重写时 Args section 必须维持 `Args:\n    <param>: <description>` 格式 —— 这是 pydantic-ai 通过 griffe 解析进 `parameters_json_schema` 的活跃通道，与 path B description override 不冲突（path A description 块被 path B 覆盖；Args 仍走 path A 解析）。本 iter 只改 Args 描述文案（议题 6 candle_count clamp 说明），不改结构。Drift guard：既有 `require_parameter_descriptions=True` 兜底。

**约束 2：移除 "volume ratio" 的 fact-only 修正**。原 docstring `technical indicators (RSI / MACD / BB / ATR / volume ratio)` 中的 "volume ratio" 是历史遗留 —— 实际 `src/services/technical.py:25-28` 注释明确 `Volume ratio intentionally not surfaced here — GMD/HTF inline their own "Last bar vol (X× SMA(20) avg)" rendering`，Technical Indicators section 本身**不含**volume ratio 字段。这是 docstring 与实际行为的 drift（疑似 G-calc-rigor-audit §G-4 修法时遗漏的 docstring 更新）。本 iter 顺便清理为 fact-only。

**Path A** (`src/agent/tools_perception.py:51-87`)：

```diff
- """Single-timeframe market data: ticker, technical indicators (RSI / MACD / BB / ATR / volume ratio), market context (ATR with percent of price, last-bar volume with average ratio, display-window range), the most recent N closed candles in OHLCV table form with anomaly markers, and a period summary comparing the last 5 vs prior 5 closed candles (avg volume, avg range, net Δclose).
-
- All indicators are computed on the closed-bar series only (excluding the in-progress candle). The OHLCV table also shows closed bars only and is sorted oldest-first by row.
-
- Markers in OHLCV table (upside-only thresholds):
-     "vol↑"   — bar volume > 2× SMA(20) of bar volumes
-     "range↑" — bar range (high - low) > 2× ATR(14)
-     Empty    — neither threshold tripped.
-
- Time column shows candle open in UTC.
- ...
- candle_count: Number of closed candles in the OHLCV table. Default 30. Range 10-80 (capped by exchange API).
- ...

+ """Single-timeframe market data: ticker, technical indicators (RSI / MACD / BB / ATR), market context (ATR with percent of price, last-bar volume with SMA(20) ratio), the most recent N closed candles in OHLCV table form with per-bar volume ratio (RVol = vol / SMA(20)) and anomaly markers, and a period summary comparing the last 5 vs prior 5 closed candles (avg volume, net Δclose).
+
+ All indicators are computed on the closed-bar series only (excluding the in-progress candle). The OHLCV table also shows closed bars only and is sorted oldest-first by row; the section header reports the in-progress candle's open/close timestamps.
+
+ OHLCV columns: Time (open UTC) | Open | High | Low | Close | Vol | RVol(×SMA20) | Markers.
+ - RVol = bar volume / SMA(20) of bar volumes; e.g. `2.95×` means the bar's volume is 2.95× the 20-bar average. Rendered for every closed bar.
+ - Markers (upside-only thresholds): `vol↑` for RVol > 2; `range↑` for bar range > 2× ATR(14); empty for neither threshold tripped. Markers remain alongside RVol — RVol provides the magnitude, markers provide a visual scan cue.
+ ...
+ candle_count: Number of closed candles in the OHLCV table. Default 30. Clamped to [10, 80]: values below 10 are raised to 10 (minimum useful window for indicators), values above 80 are capped to 80 (exchange API single-call limit).
+ ...
```

`Example call:` / `Example output:` 块同步更新 — sample 输出含新 `RVol(×SMA20)` 列、删 `<N>-candle High-Low` 行、删 `Avg range` 行、Recent Candles header 含 in-progress 指示。

**Path B** (`src/agent/tools_descriptions.py:48-69` `GET_MARKET_DATA_DESCRIPTION`)：**维持 block-style admonition 不变**（`Example call:` / `Example output:` / 多 section）—— path B 通过 `@tool(description=DESC_X)` 把字符串 verbatim 传给 pydantic-ai，**bypass griffe 整体**（per `tools_descriptions.py:5-6` 文件头明示 "passed verbatim ... to bypass griffe parsing and reach the LLM"），这正是 PR #59 引入 path B 的目的（path A 的 block-style 被 griffe 剥离，迁 path B 是为了**保留** block-style 到达 LLM）。本 iter 只更新 path B 内容反映新 OHLCV 表格 + RVol 列 + in-progress hint + 删 N-candle / Avg range 行，**格式风格不动**。

**Path A vs B 通道汇总**（清晰化避免混淆）：

| Path | 入口 | 处理通道 | LLM 可见性 |
|---|---|---|---|
| **A: description 块**（`tools_perception.py:51` docstring 主体 + Example 块） | python docstring | griffe sniff → `tool.tool_def.description` | **被 griffe 剥 block-style admonition**（`<词>:` + 缩进会被剥），inline 散文 / Args / Returns 通道 survives |
| **A: Args section** | python docstring `Args:` 块 | griffe sniff → `parameters_json_schema` | 完整到达 LLM（参数 schema） |
| **B: description override** | `tools_descriptions.py` 常量 + `@tool(description=DESC_X)` | **bypass griffe**，verbatim 传 pydantic-ai | **完整到达 LLM**，block-style survives |
| **C: trader.py inner docstring** | `trader.py:124-140` docstring | 被 path B `description=` override 覆盖，griffe 也读但 description 字段被 override 抢占 | **不到 LLM**（dev-facing only） |

议题 5/6 docstring 重写涉及 path A + B + C，每路径目标不同：
- **Path A**: 内容修正（删 "volume ratio" / 加 RVol 解释 / 同步 candle_count clamp 文案），保留**现有 inline 风格**（因为 griffe 会剥 block）—— path A description 主体作为 fallback；inline 散文 survives 进 LLM 的 `<summary>`
- **Path B**: 内容同 path A 但**保留 block-style**，作为**主 LLM 通道**（PR #59 之后 path B 是 LLM-facing 主路径）
- **Path C**: 简化 dev-facing 说明，与 path A / B 内容一致即可，格式自由

**Path C** (`src/agent/trader.py:124-140`)：inner docstring 简化版，被 `@tool(description=GET_MARKET_DATA_DESCRIPTION)` override，不直接到 LLM，但仍同步精确化以防 dev confusion。

### 2.6 议题 7（out-of-scope）

38 multi-call cycles 中 **37 (97%) 是 15m + 5m pair**，agent 用 GMD × 2 拼出跨 TF MACD/RSI/BB detail。根因在 MTS 工具的信号缺口（MTS 给跨 TF 摘要但不给 per-TF MACD/RSI/BB），**不在 GMD**。本 iter 守 GMD polish 边界。

**Backlog 锚定**：W4+ sim 实证再观察 GMD multi-call (5m+15m) pair frequency 是否仍 ≥ 30 cycle/session；若持续观察到，**立独立 memory `project_mts_per_tf_signal_gap`** 跟踪。本 iter 不预设 anchor 既有 memory（避免主题不契合的过渡映射，per spec review 反馈）。

## 3. Token cost / ROI 估算（坦白：渲染净增 > reasoning chars 节省）

Per-call delta（287 GMD calls in session #12）：

| 改动 | Δchars/call | Session Δ |
|---|---:|---:|
| 议题 1：RVol 列 (~14 chars × 20-30 rows + header) | **+280 ~ +420** | +80K ~ +120K |
| 议题 2：OHLCV header 加 in-progress | **+50** | +14K |
| 议题 3：删 N-candle H-L 行 | **−30** | −9K |
| 议题 4：删 Avg range 行 | **−50** | −14K |
| **渲染净增** | **+250 ~ +390** | **+71K ~ +111K** |

**Reasoning chars 节省（修正算法）**：

- Affected reasoning blocks: 80 / 270 ≈ 29.6% systematic hand-compute
- 每条 affected reasoning 节省的字符 ≈ "vol N.NN (X.XX× SMA avg)" 类短语 ≈ ~100 chars / instance
- 单条 reasoning 可能多 instance（如 cycle 1 L91 同时手算 vol + range）—— 取 average 1.5 instances/affected reasoning
- **估算节省**：80 × 100 × 1.5 ≈ **12K chars session-wide**

| | Session impact |
|---|---:|
| 渲染净增 | **+71K ~ +111K** |
| Reasoning chars 节省 | **~ −12K** |
| **Net token impact** | **+59K ~ +99K（渲染净增）** |

**Net token 是正向（增加）。议题 1 的真实价值不是 token 节省，而是消除 30% reasoning 中 hand-compute 的 mental friction**：

1. **质量 / 正确性信号**：每次手算引入 division error 风险（agent 心算偶有数值偏差，audit sample 中未观察到但理论存在）
2. **Latency 信号**：手算消耗 reasoning thinking budget，挤压其他 reasoning 步骤
3. **接口闭环**（per 原则 5）：工具职责是 fact-provide complete，"agent 30% 时间在手算 tool 应该 provide 的事实" 本身是设计反信号，不依赖 token ROI 论证

ROI 论证回归原则 5（接口闭环）而非 token 算账。Token 净增 +60K ~ +100K session-wide 在 sim #12 总输出量级（~MB）中边际成本。

## 4. 实现影响范围

| 文件 | 改动 | 估行数 |
|---|---|---:|
| `src/agent/tools_perception.py:51-219` | get_market_data 渲染 + docstring + 算法（议题 1/2/3/4/5/6 + `_fmt_candle_time` helper + `_to_pd_timestamp_utc` helper + TF_OFFSETS 字典） | ~45 |
| `src/agent/tools_descriptions.py:48-69` | path B `GET_MARKET_DATA_DESCRIPTION` 内容同步（**保留 block-style** —— per §2.5 path B bypass griffe，不改风格只改内容反映新表格） | ~25 |
| `src/agent/trader.py:124-140` | inner docstring 同步（path C） | ~5 |
| `tests/` | snapshot rebuild + new assertions（RVol 列 / in-progress hint / N-candle 行 / Avg range 行 / fmt dispatch / path B verify） | ~35 |

预估 source change **~75 行**（tools_perception 45 + tools_descriptions 25 + trader 5），离 mini-iter 上限 100 行仍有 25 行余量，符合 mini-iter direct-merge 路径（per memory `feedback_docs_only_direct_merge`）。

**Safeguard**：若实现期 src 改动累计超 100 行（例如 docstring 内容扩展比预估长 / RVol 列对齐复杂度超预期 / fmt helper / TF_OFFSETS 设计需独立 module），切走标准 GitHub PR 路径，不走 direct-merge。6 议题数本身偏多，reviewer 视角可能 push back，PR 路径降低风险。

## 5. 测试覆盖

### 5.1 既有测试 snapshot rebuild

GMD 输出格式相关测试需 rebuild：

- `tests/agent/test_tools_perception.py` — GMD 输出 sections assertion（涉 OHLCV table 列 / Market Context 行 / Period summary 行）
- `tests/services/test_technical.py` — 不变（technical service 不改）

预估 ~10-15 tests 需更新。

### 5.2 新增测试

**议题 1 RVol 列**：
- `test_gmd_rvol_column_present_in_ohlcv_table` — 验证 OHLCV table 含 `RVol(×SMA20)` 列 header + 数据行带 × 后缀
- `test_gmd_rvol_matches_vol_over_sma20` — spot-check 一根 bar 的 RVol = vol / SMA(20) 数值
- `test_gmd_rvol_marker_consistency` — 测**常规 case**：RVol >> 2 时必有 `vol↑`、RVol << 2 时必无；**避免 RVol ≈ 2.0 FP 边界**（源码 `volume > 2 * sma` vs 渲染 `vol / sma > 2` 在极端 floating-point 边界不严格 round-trip 等价，audit 实测 80 hits 数值范围 0.18×-3.90× 无近 2.0 边界，但 test 严格 iff 写法在合成数据上可能翻车）。建议用 `RVol > 2.05` 与 `RVol < 1.95` 两组常规 case + 跳过 ε-边界

**议题 2 in-progress hint**：
- `test_gmd_ohlcv_header_contains_in_progress_indicator` — 验证 header 含 "in-progress" + 正确 open/close 时间
- `test_gmd_in_progress_time_arithmetic_intraday` — last_closed=15:30 / tf=15m → in-progress=15:45, closes=16:00（5m / 15m / 1h / 4h 同测）
- `test_gmd_in_progress_time_arithmetic_daily` — last_closed=2026-05-27 / tf=1d → in-progress=2026-05-28, closes=2026-05-29
- `test_gmd_in_progress_time_arithmetic_weekly` — last_closed week → +1 week
- `test_gmd_in_progress_time_arithmetic_monthly` — last_closed=2026-01-01 / tf=1M → in-progress=2026-02-01, closes=2026-03-01（**calendar-aware via DateOffset；验 28/29/30/31 日月长不固定下正确性**）
- `test_gmd_empty_display_df_no_in_progress_indicator` — display_df.empty 时不渲染 in-progress 行（防御）
- `test_gmd_unsupported_tf_degraded_fallback` — 传 unknown tf（如 `"7m"` 合成值）时 header 回到无 in-progress 段的原格式，**不 crash 也不 raise**（与 §2.2 degraded fallback 决议一致；保持 backward-compat）

**议题 3/4 删除字段**：
- `test_gmd_no_n_candle_high_low_row` — Market Context 段不含 `<N>-candle High-Low`
- `test_gmd_period_summary_no_avg_range` — Period summary 段不含 `Avg range`
- `test_gmd_period_summary_keeps_avg_vol_and_net_delta` — Period summary 仍含 `Avg vol` + `Net Δclose`

**议题 5/6 docstring**：
- `test_gmd_description_matches_path_a_path_b_intent` — path B `GET_MARKET_DATA_DESCRIPTION` 含 RVol column + in-progress hint + clamp 文案
- 已有 path B verbatim drift guard 测试 `test_dual_mode_tool_wrapper` + `test_set_next_wake_description_carries_examples_block` (tests/test_trader_agent.py:272, :326) 验证 `@tool(description=DESC_X)` bypass griffe + block-style 完整到达 LLM，本 iter 不修；新增 path B verify 测试 `test_gmd_description_matches_path_a_path_b_intent` 复用同款断言模式

### 5.3 端到端 smoke

- 运行 sim 跑 1 cycle 验证 GMD 输出渲染无 crash / 字段齐 / 数值在合理范围。具体 smoke script 路径在 **plan 阶段确认**（候选：`scripts/smoke_simulate.py` 或同等 cycle-level driver，复用 sim #12 fixture / `data/tradebot.db` paused session）。

## 6. 验收条件 (AC)

- [ ] AC1：`pytest tests/` 全通过（snapshot rebuild + new tests）
- [ ] AC2：渲染结构 snapshot test 覆盖（auto-verified via §5.2 tests `test_gmd_rvol_column_present_in_ohlcv_table` + `test_gmd_ohlcv_header_contains_in_progress_indicator` + `test_gmd_no_n_candle_high_low_row` + `test_gmd_period_summary_no_avg_range` + `test_gmd_period_summary_keeps_avg_vol_and_net_delta`），覆盖 RVol 列 / in-progress hint / 无 N-candle H-L / 无 Avg range / 保留 Net Δclose + Avg vol
- [ ] AC3：path A docstring + path B `GET_MARKET_DATA_DESCRIPTION` 同步——**内容一致，格式差异**（**path A 保持 inline 风格**避免 block-style 被 griffe 从 `tool.tool_def.description` 剥离；**path B 保留 block-style**（`Example call:` / `Example output:` 等），通过 `@tool(description=DESC_X)` bypass griffe 完整到达 LLM，per §2.5 通道汇总）
- [ ] AC4：议题 1 RVol 与现有 `vol↑` marker 不冲突 — `vol↑` 存在 iff RVol > 2（严格 >，与源码一致），markers 仍渲染
- [ ] AC5：议题 2 in-progress 时间算术正确 — last_closed + tf_offset = in-progress_open / + 2×tf_offset = in-progress_close。**覆盖代表性 tf 子集**（intraday minute: 5m/15m；hour: 1h/4h；day: 1d；week: 1w；**month: 1M calendar-aware via `pd.DateOffset(months=1)`** —— 验证 28/29/30/31 日月长不固定下正确性）。**新增 tf 由 §2.2 TF_OFFSETS 字典 driven**，§5.2 `test_gmd_in_progress_time_arithmetic_*` + `test_gmd_unsupported_tf_degraded_fallback` 含 unit test 兜底全 CCXT 集
- [ ] AC6：议题 6 candle_count clamp 明示文案在 path A + path B 同步
- [ ] AC7：sim smoke 1 cycle 不 crash，渲染字段齐

## 7. 风险 / 回滚

**主要风险**：
- 议题 1 加列后 OHLCV table 宽度增加 ~14 chars，可能在某些极端 candle_count 配置下触发对齐边界——pytest snapshot 测试可 catch
- 议题 2 in-progress 时间算术：`1M` 需 `pd.DateOffset(months=1)` 而非 `Timedelta`（月长 28-31 天不固定），见 §2.2 算法 + AC5；本 session 全是 15m / 5m 触发不到 calendar-aware 路径，但代码路径需 cover 全 tf 集
- **Path A vs Path B 通道方向不要搞反**（per memory `project_griffe_example_stripped` + PR #59 设计意图）：path A docstring 走 griffe 会被剥 block-style；path B `@tool(description=DESC_X)` **bypass griffe** 保留 block-style。本 iter 实施期若误把 path B 改成 inline 风格 → 反方向降低 LLM 可见信息（path B 的 block 本来 survives，inline 化反而没必要）。Drift guard：`test_dual_mode_tool_wrapper` + `test_set_next_wake_description_carries_examples_block` 兜底 path B 完整性
- **议题 1 加 RVol 列后 W4+ sim adoption 不达预期风险**：若 W4 sim 观察到 RVol 列 adoption 仍 < 5%，议题 1 可能需 rollback / 重设计；本 iter 不预设 adoption 阈值，按 W4 数据决定。详见 §8 backlog

**回滚**：
- 本 iter 不动 service 层（`src/services/technical.py` 不改），不动 schema / DB / migration
- 回滚只需 revert tools_perception.py + tools_descriptions.py + trader.py 三处变更 + 测试 snapshot revert

## 8. 后续候选（backlog）

- **议题 7（MTS per-TF MACD/RSI signal gap）**：候选 `iter-tool-opt-mts-per-tf-detail` 独立 brainstorm。触发条件：W4+ sim 中再观察 GMD multi-call (5m+15m) pair ≥ 30 cycle/session。
- **可能议题：议题 1 后 W4 sim 观察 RVol 列 adoption rate**——若 ≥ 10% reasoning 直接引用 RVol 数值，证实 ROI；若仍 < 5% 则候选 rollback / 重设计（不太可能，但作 W4 attribution 数据收集）。
