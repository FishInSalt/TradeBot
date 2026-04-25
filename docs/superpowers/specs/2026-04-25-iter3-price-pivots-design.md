# Iter 3 — `get_price_pivots` 朴素版（结构感知工具）

**Spec date:** 2026-04-25
**Branch:** `iter3-price-pivots-spec`
**Predecessor:** Iter 2b (PR #23, squash `9245bc8`)
**Successor:** Iter 4 — N7 Layer 1 重组

## 0. 背景

### 0.0 项目快照

23 PR 已合并、786 tests + 1 skip 全绿。Agent 当前 29 个 `@agent.tool`（18 感知 + 10 执行 + 1 memory，HTF / multi-tf / order book / recent trades / get_position 增强 都已 landed）。Layer 1 persona 24 bullets。

### 0.1 所处位置

进观察期前 5-iteration 计划的第 4 步：

| Iter | 状态 | 内容 |
|------|------|------|
| 1 | ✅ PR #21 | tool-call metrics enabler |
| 2 | ✅ PR #22 | toolkit expansion (3 perception tools + get_position 增强) |
| 2b | ✅ PR #23 | OKX live hardening |
| **3** | **本 iter** | **结构感知工具：朴素版 `get_price_pivots`** |
| 4 | pending | N7 Layer 1 重组（按完整 25-bullet 工具集做最终重组）|

### 0.2 为什么是这一轮

观察期前的"工具补全"最后一块拼图。Agent 现在能看到：技术指标（RSI/MA/MACD/BB/ATR）、多 TF 趋势对齐、order book 深度、recent trade flow、position risk exposure、HTF MA + 100/20-period range — 但**没有"具体支撑/阻力价位"的离散结构**。

> persona Layer 1 已经反复要求 agent "place stops at structural levels, not arbitrary percentages"，但目前没有专门的工具能列出这些 levels。HTF tool 给出 100-period H/L 是"窗口边界"，不是窗口**内部**的 swing 结构。

`get_price_pivots` 补这个 gap：让 agent 看到主 TF 最近 100 bars 的 fractal swing pivots + 上 1 天/周/月 H/L 这些标准结构位。

### 0.3 硬约束

1. **Fact-only**：输出零定性标签（strong/weak/important/key/critical/major/significant 等）。延续 PR B (N5) 与 Iter 2 (PR #22) 的 fact-only 守门
2. **不按重要性截断**：自然有界（fractal N=5 + last 100 bars + 6 prior period H/L → ~26 行 / ~700 tokens）
3. **不引入新算法库**：纯 pandas rolling，不上 `pandas_ta` 的 fractal/pivot 函数（避免增加间接依赖；rolling 一行就能写）
4. **三态契约一致**：跟 N3 spec §3.5 对齐（fact / `insufficient data` / `temporarily unavailable`），主 TF + 3 个 prior period 各自独立降级
5. **不动 Layer 1 prompt 结构**：本 iter 仅 append 一个 bullet（24 → 25），结构重组留 Iter 4

### 0.4 术语表

| 术语 | 定义 |
|------|------|
| **Williams Fractal N=5** | 中心 bar 的 high 严格大于左 5 + 右 5 个 bar 的 high → swing high；low 严格小于则 swing low |
| **Swing pivot** | 满足 fractal N=5 严格不等的局部高/低点 |
| **Prior daily H/L** | 1d timeframe `iloc[-2]` 的 high/low — "前一根完整收盘的 daily K 线"（UTC 切日） |
| **Confirmed pivot** | 中心 bar 距今 ≥ 5 bar，右侧窗口完整可验证 |
| **Unconfirmed (forming) pivot** | 距今 < 5 bar，右侧窗口未满 — 本 iter **不输出** |

## 1. 目标与非目标

### 1.1 目标

1. 提供一个 `@agent.tool get_price_pivots(deps)`，无参数，输出主 TF swing + prior period H/L
2. 输出按"在当前价上方 / 下方"分组，组内按距离绝对值升序排序
3. swing 行带 `N bars ago`，prior 行不带 ago 后缀（label 即时间锚）
4. 主 TF + 1d + 1w + 1M 四路 OHLCV 各自独立降级，不级联
5. Layer 1 加 1 bullet 描述工具用法（24 → 25 bullets，REGISTERED_TOOL_NAMES 同步加 `"get_price_pivots"`）
6. 测试覆盖：算法层 ~10 + 渲染层 ~10 + 降级层 ~6 + fact-only regression 1（单函数 5 场景）+ persona drift 2，零 regression

### 1.2 非目标

- ❌ Volume profile / volume-weighted ranking
- ❌ Touch count / "tested N times" 标签
- ❌ Ranking by importance / strength
- ❌ Top-N 截断（不论 cap 还是软 cap）
- ❌ 自适应 N（N=5 固定）
- ❌ `timeframe` 参数（固定 `deps.timeframe`，不暴露给 agent）
- ❌ Today (forming) H/L 显示
- ❌ Unconfirmed (距今 <5 bar) swing pivot 显示
- ❌ "Broken / breached" 标签 — 需要 lookahead 数据 + 偏决策语义
- ❌ 任何把 pivot 自动转成 SL/TP 建议的逻辑（决策权留 agent）

### 1.3 改动清单

| 文件 | 改动 | 估算行数 |
|------|------|---------|
| `src/agent/tools_perception.py` | 加 `_compute_swing_pivots` / `_get_prior_period_hl` / `_render_pivot_rows` / `_bars_ago_fmt` helpers + 纯实现 `async def get_price_pivots(deps: TradingDeps) -> str`（**无装饰器**，与现有 18 个感知工具一致；该文件不导入 `agent` 对象） | +200-250 |
| `src/agent/persona.py` | Layer 1 `_build_layer1` append 1 bullet | +1-2 |
| `src/agent/trader.py` | (a) 新增 `@agent.tool` 装饰薄包装 `async def get_price_pivots(ctx: RunContext[TradingDeps]) -> str`（内部 `from src.agent.tools_perception import get_price_pivots as _impl; return await _impl(ctx.deps)`）；(b) `REGISTERED_TOOL_NAMES` list 末尾感知段插入 `"get_price_pivots"` | +15-18 |
| `tests/test_price_pivots.py` (新文件) | ~26 测试（算法 10 / 渲染 10 / 降级 6） | +450-550 |
| `tests/test_fact_only_wordlist.py` | 扩展 +1 fact-only 测试函数（`test_get_price_pivots_fact_only_5_scenarios` 单函数 5 场景）+ `PIVOTS_BANNED_WORDS` per-tool 局部常量 | +30-50 |
| `tests/test_persona.py` | 新增 2 个测试函数：bullet 数 24 → 25 drift guard；Layer 1 含 `get_price_pivots` + 关键词验证 | +22-30 |
| `tests/test_trader_agent.py` | drift 测试硬编码 `len == 29` → `== 30`（19 感知 + 10 执行 + 1 memory） | +1-2 |

## 2. 工具设计

### 2.1 `get_price_pivots(deps)`

**签名 — 两文件分工**（与现有 18 感知工具一致：实现在 tools_perception.py，装饰器薄包装在 trader.py，参考 trader.py:202-252 的 `get_order_book` / `get_recent_trades` / `get_multi_timeframe_snapshot`）：

**A. `src/agent/tools_perception.py` — 纯实现，无装饰器**

```python
async def get_price_pivots(deps: TradingDeps) -> str:
    """Show structural support/resistance: last 100 main-TF swing pivots
    (Williams fractal N=5) + prior daily/weekly/monthly H/L. Fact-only.

    Returns:
        Levels grouped by 'above current price' / 'below current price';
        within each group, sorted by absolute distance ascending. Swing
        rows include 'N bars ago'; prior rows label the period.

    Degradation: per-source three-state (fact / insufficient data /
        temporarily unavailable). Ticker failure → whole tool unavailable
        (no baseline price); main-TF failure → swing section degrades only;
        per-prior failure → only that row degrades.
    """
    # body 见 §3.2 / §4.4
```

**B. `src/agent/trader.py` — `@agent.tool` 装饰薄包装**（在 `create_trader_agent` 内部，紧随 `get_multi_timeframe_snapshot` 的薄包装后）

```python
    @agent.tool
    async def get_price_pivots(ctx: RunContext[TradingDeps]) -> str:
        """Show structural support/resistance: last 100 main-TF swing pivots
        (Williams fractal N=5) + prior daily/weekly/monthly H/L. Fact-only.
        Returns levels grouped by above/below current price, sorted by
        absolute distance. Swing rows annotate 'N bars ago'; prior rows
        label the period (Daily / Weekly / Monthly). See tool implementation
        for full degradation semantics.
        """
        from src.agent.tools_perception import get_price_pivots as _impl
        return await _impl(ctx.deps)
```

注：薄包装函数的 docstring 是 agent 实际看到的工具描述（pydantic-ai 用包装签名做 schema），不是 tools_perception.py 的实现 docstring。所以 trader.py 包装层 docstring 必须含工具用法关键信息（agent prompting）。tools_perception.py 实现层 docstring 偏内部参考。两份 docstring 内容大致重叠，是项目现有约定（参 trader.py:204 / 221 / 239）。

**调用**：`symbol = deps.symbol` / `timeframe = deps.timeframe`，无 caller 参数。

**数据拉取**（4 路并发，每路独立 try/except）：

| Source | TF | limit | 用途 |
|--------|-----|-------|------|
| 主 TF OHLCV | `deps.timeframe` | 100 | swing 算法窗口 |
| Prior daily H/L | `1d` | 2 | `iloc[-2]` 取昨日 K 线 |
| Prior weekly H/L | `1w` | 2 | `iloc[-2]` 取上周 K 线 |
| Prior monthly H/L | `1M` | 2 | `iloc[-2]` 取上月 K 线 |
| Ticker | (n/a) | 1 | `ticker.last` 作为基准价 |

**Ticker fetch 处理**：跟 `get_multi_timeframe_snapshot` 一致 — ticker fetch 异常则整工具返回 `temporarily unavailable`（无基准价无法算距离）。

**主 TF fetch 处理**：异常 / df.empty / `len(df) < 11` 时 swing 段降级（参 §3.3），但 prior 段照常输出。

### 2.2 输出示例

**正常满载**：

```
=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===
Current Price: 66,523.40

=== Levels Above Current Price ===
Swing High: 66,890.00 (+0.55%, 23 bars ago)
Swing High: 67,120.50 (+0.90%, 47 bars ago)
Prior Daily H: 67,234.00 (+1.07%)
Prior Weekly H: 68,500.00 (+2.97%)
Swing High: 68,750.00 (+3.35%, 84 bars ago)
Prior Monthly H: 71,200.00 (+7.03%)

=== Levels Below Current Price ===
Swing Low: 66,102.00 (-0.63%, 8 bars ago)
Swing Low: 65,800.00 (-1.09%, 19 bars ago)
Prior Daily L: 65,500.00 (-1.54%)
Prior Weekly L: 64,200.00 (-3.49%)
Prior Monthly L: 60,800.00 (-8.60%)
```

注：`bars_ago ≥ 5`（confirmed pivot 距今至少 N=5 bar，详见 §4.1 算法）。

**Edge — swing 段空（窗口内单调上涨）**：

```
=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===
Current Price: 66,523.40

=== Levels Above Current Price ===
Prior Daily H: 67,234.00 (+1.07%)
Prior Weekly H: 68,500.00 (+2.97%)
Prior Monthly H: 71,200.00 (+7.03%)

=== Levels Below Current Price ===
Prior Daily L: 65,500.00 (-1.54%)
Prior Weekly L: 64,200.00 (-3.49%)
Prior Monthly L: 60,800.00 (-8.60%)

(No swing pivots in 100-bar window)
```

**Edge — 主 TF 数据短（50 bars，所有 prior `len(df) < 2`）**：

```
=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===
Current Price: 66,523.40

=== Levels Above Current Price ===
Swing High: 66,890.00 (+0.55%, 23 bars ago)

=== Levels Below Current Price ===
Swing Low: 66,102.00 (-0.63%, 8 bars ago)

(Window: 50 bars, less than 100)
Prior Daily H/L: insufficient data
Prior Weekly H/L: insufficient data
Prior Monthly H/L: insufficient data
```

**Edge — ticker 异常**（**唯一**触发整工具单行 short-circuit 的条件，详见 §3.2/§3.3）：

```
Price pivots (BTC/USDT:USDT, main TF: 5m): temporarily unavailable
```

**Edge — 主 TF 异常 + 3 prior ok**（OHLCV 故障**不**触发 short-circuit；above/below 仅含 prior 行）：

```
=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===
Current Price: 66,523.40

=== Levels Above Current Price ===
Prior Daily H: 67,234.00 (+1.07%)
Prior Weekly H: 68,500.00 (+2.97%)
Prior Monthly H: 71,200.00 (+7.03%)

=== Levels Below Current Price ===
Prior Daily L: 65,500.00 (-1.54%)
Prior Weekly L: 64,200.00 (-3.49%)
Prior Monthly L: 60,800.00 (-8.60%)

Swing pivots: temporarily unavailable
```

**Edge — 主 TF `df.empty` + 全部 prior fetch 异常（启动初期 + 短暂全 API 故障）**：

```
=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===
Current Price: 66,523.40

=== Levels Above Current Price ===
(none)

=== Levels Below Current Price ===
(none)

Swing pivots: insufficient data (need 11+ bars, got 0)
Prior Daily H/L: temporarily unavailable
Prior Weekly H/L: temporarily unavailable
Prior Monthly H/L: temporarily unavailable
```

视觉空但语义完整：四路 source 各自的降级原因（swing insufficient vs prior unavailable）保留可见，方便 agent / 调试者识别故障类别。**不**collapse 为整工具单行 unavailable —— 因 ticker 成功 + 渲染框架完整。

**Edge — `Levels Above` 全空**：

```
=== Levels Above Current Price ===
(none)
```

## 3. 架构

### 3.1 算法层 / 渲染层分层

跟 `get_higher_timeframe_view` 模式对仗 — 所有算法 helpers 放 `tools_perception.py` module-level，工具函数本身只做：(1) async fetch 4 路 OHLCV + ticker；(2) 调 helpers 算 pivots；(3) 调 render 拼字符串。

不引入新 `services/pivots.py` 或扩展 `TechnicalAnalysisService` —— 跟 HTF / order book / multi-tf snapshot 现有模式一致。文件长度问题留给 Iter 4 N7 重组（统一处理 `tools_perception.py` 的 25 个工具）。

### 3.2 数据层 — 4 路并发拉取

```python
async def get_price_pivots(deps: TradingDeps) -> str:
    import asyncio  # 函数内局部导入，与 get_multi_timeframe_snapshot 现有惯例一致 (tools_perception.py:1320)

    symbol = deps.symbol
    main_tf = deps.timeframe

    try:
        ticker = await deps.market_data.get_ticker(symbol)
        current_price = ticker.last
    except Exception:
        logger.exception("get_price_pivots ticker fetch failed for %s", symbol)
        return f"Price pivots ({symbol}, main TF: {main_tf}): temporarily unavailable"

    async def _fetch(tf, limit):
        try:
            return await deps.market_data.get_ohlcv_dataframe(symbol, tf, limit=limit)
        except Exception as e:
            return e

    results = await asyncio.gather(
        _fetch(main_tf, 100),
        _fetch("1d", 2),
        _fetch("1w", 2),
        _fetch("1M", 2),
    )
    main_df_or_err, daily_or_err, weekly_or_err, monthly_or_err = results
    ...
```

**降级语义对称性**：唯一触发"整工具单行 unavailable"的条件是 **ticker fetch 异常**（无基准价无法算距离 %，渲染框架本身依赖 `current_price`）。任何 OHLCV 路径异常或 empty 都按各自段降级，整工具仍渲染框架 — 包括"主 TF + 全部 3 prior 都异常"这种最坏组合（输出 4 行独立 `temporarily unavailable`，让 agent / 调试者看清各 source 失效详情）。这条规则覆盖 §2.2 中 "主 TF empty + 全 prior 异常" 与 "主 TF 异常 + 全 prior 异常" 两类视觉相同的情形（见 §2.2 末尾 edge 示例）。

### 3.3 三态契约分层

| Layer | 异常分支 | 输出 |
|-------|---------|------|
| **整工具** | ticker fetch 异常（**唯一**触发整工具 short-circuit 的条件） | `Price pivots (...): temporarily unavailable` （单行返回） |
| **swing 段** | 主 TF 异常 | `=== Levels Above ===` / `=== Levels Below ===` 仍按 prior 渲染；末尾加 `Swing pivots: temporarily unavailable` |
| **swing 段** | 主 TF df.empty 或 len < 11 | 末尾加 `Swing pivots: insufficient data (need 11+ bars, got N)` |
| **swing 段** | 11 ≤ len < 100 + 至少 1 pivot | 算法照算，末尾加 `(Window: N bars, less than 100)` |
| **swing 段** | 11 ≤ len < 100 + fractal 输出空 | 末尾加 `(Window: N bars, less than 100 — no swing pivots found)`（与"有 pivot"分支文案区分，避免 agent 把窗口约束误解成无结构信号） |
| **swing 段** | 100 bars 但 fractal 输出空 | 末尾加 `(No swing pivots in 100-bar window)` |
| **prior 段** | 单一 prior fetch 异常 | 该行替换为 `Prior {Daily\|Weekly\|Monthly} H/L: temporarily unavailable` |
| **prior 段** | 单一 prior `len(df) < 2` | 该行替换为 `Prior {Daily\|Weekly\|Monthly} H/L: insufficient data` |

**注 1**：单一 prior 降级时**不进入 above/below 分组** — 它就是一行独立的"该 prior 暂不可用"提示，放在分组下方（与正常 prior 行同段位置但不参与排序）。

**注 2**：上表 swing 段两条"主 TF 异常 / 主 TF empty 或 len<11"的输出形式从 agent 视角等价 —— 都是 above/below 段按 prior 行正常渲染（若 prior ok）+ swing_status 行尾随，仅 swing_status 文案不同。即使所有 4 路 OHLCV 都失败，整工具仍按渲染框架输出（above/below 各 `(none)` + 4 行 footer），不 collapse 为单行。

### 3.4 Fact-only 守门

延续 PR B (N5) + Iter 2 (PR #22) 的 banned-word 测试模式。本工具的输出文本 **零定性词**：

**Banned wordlist（本工具 fact-only regression 测试覆盖）**：
- `strong` / `weak` / `strongly` / `weakly`
- `important` / `unimportant` / `key` / `major` / `minor`
- `critical` / `crucial` / `significant` / `insignificant`
- `bullish` / `bearish` (从 N5 wordlist 继承)
- `broken` / `breached` (本 iter 1.2 非目标 — 这两词可能被未来代码错误产出，需测试守门)

**白名单豁免**（这些词出现在输出/docstring 但不算违规，故 `PIVOTS_BANNED_WORDS` 不列入）：
- `Swing High` / `Swing Low` / `Prior Daily H` / `Prior Weekly L` 等结构标签 — 业界标准结构定义
- `Levels Above Current Price` / `Levels Below Current Price` — 中性方位词
- `support` / `resistance` — 仅在 docstring 出现（"structural support/resistance"），运行时输出文本不会含；测试无需覆盖
- `bars ago` / `Current Price` / `insufficient data` / `temporarily unavailable` — 状态/事实词

测试做法：per-test 局部 `PIVOTS_BANNED_WORDS` 常量（不扩全局 `FACT_ONLY_BANNED_WORDS_RE`，理由见 §5.4），覆盖 5 个输入场景（正常满载 / swing 空 / 短窗口 / 主 TF 异常 / 全 prior 异常），每场景断言 grep 0 hit。

## 4. 实现细节

### 4.1 Williams Fractal 严格不等实现

**关键点**：`rolling(2n+1, center=True).max() == h` 这种朴素写法**会把横盘平台期的连续相等 high 全标为 pivot**，违反"严格不等"约束。

**正确实现**：

```python
def _compute_swing_pivots(
    df: pd.DataFrame, n: int = 5
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return (highs, lows) where each entry is (bars_ago, price).
    Confirmed pivots only — last n bars excluded due to incomplete right window.

    Williams fractal N=n with strict inequality: center bar's high must be
    strictly greater than all 2n surrounding bars' highs (and similarly low
    strictly less). Equality at any neighbor disqualifies the pivot —
    prevents flat-plateau false signals.
    """
    if len(df) < 2 * n + 1:
        return [], []

    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    last_idx = len(df) - 1
    confirm_end = last_idx - n  # last bar index with complete right window (inclusive in loop below)

    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(n, confirm_end + 1):
        center_h = h[i]
        center_l = l[i]
        # 严格不等：center 必须严格大于 / 小于左右各 n bar
        is_high = all(center_h > h[i + d] for d in range(-n, n + 1) if d != 0)
        is_low = all(center_l < l[i + d] for d in range(-n, n + 1) if d != 0)
        if is_high:
            highs.append((last_idx - i, float(center_h)))
        if is_low:
            lows.append((last_idx - i, float(center_l)))
    return highs, lows
```

**为什么不用 pandas rolling**：rolling(center=True) + 严格不等需要复杂的 NaN 与 ties 处理；显式 loop 在 100 bars 上耗时 < 1ms，可读性远好。

**Tie-break**：相邻 bar 同价时 `>` 直接返 False — 平台期不产 pivot（符合"严格"语义）。

### 4.2 Prior period H/L 取值

```python
def _get_prior_period_hl(
    df_or_err: pd.DataFrame | Exception,
) -> tuple[str, float | None, float | None]:
    """Return (status, high, low). status one of:
    'ok', 'insufficient', 'unavailable'.

    Period label ('Daily' / 'Weekly' / 'Monthly') is irrelevant here —
    it's bound by the caller in `_render_pivot_rows` when iterating the
    three period results (§4.3).
    """
    if isinstance(df_or_err, Exception):
        return "unavailable", None, None
    df = df_or_err
    if df is None or df.empty or len(df) < 2:
        return "insufficient", None, None
    prior = df.iloc[-2]
    return "ok", float(prior["high"]), float(prior["low"])
```

注意 `iloc[-2]` 对 `1M` 在 OKX / 多数 CCXT 交易所是月线 — 月初到当月跨日时 `iloc[-1]` 是当月在形成的 K 线，`iloc[-2]` 是上月完整 K 线。

### 4.3 排序与渲染

```python
def _render_pivot_rows(
    swing_highs: list[tuple[int, float]],     # (bars_ago, price)
    swing_lows: list[tuple[int, float]],
    prior_d: tuple[str, float | None, float | None],   # (status, h, l)
    prior_w: tuple[str, float | None, float | None],
    prior_m: tuple[str, float | None, float | None],
    current_price: float,
) -> tuple[list[str], list[str], list[str]]:
    """Return (above_rows, below_rows, footer_lines).
    above/below already sorted; footer contains insufficient/unavailable
    notices that don't fit in either group.
    """
    above: list[tuple[float, str]] = []  # (abs_distance, line)
    below: list[tuple[float, str]] = []
    footer: list[str] = []

    # Swing pivots — kind 直接从来源 list 确定，无需 membership check
    for kind, items in (("Swing High", swing_highs), ("Swing Low", swing_lows)):
        for ago, price in items:
            dist_pct = (price - current_price) / current_price * 100
            line = f"{kind}: {price:,.2f} ({dist_pct:+.2f}%, {_bars_ago_fmt(ago)})"
            target = above if price > current_price else below
            target.append((abs(dist_pct), line))

    # Prior period H/L
    for label, (status, h, l_) in [
        ("Daily", prior_d), ("Weekly", prior_w), ("Monthly", prior_m),
    ]:
        if status == "ok":
            for kind, value in [("H", h), ("L", l_)]:
                dist_pct = (value - current_price) / current_price * 100
                line = f"Prior {label} {kind}: {value:,.2f} ({dist_pct:+.2f}%)"
                target = above if value > current_price else below
                target.append((abs(dist_pct), line))
        else:
            note = "insufficient data" if status == "insufficient" else "temporarily unavailable"
            footer.append(f"Prior {label} H/L: {note}")

    above.sort(key=lambda x: x[0])
    below.sort(key=lambda x: x[0])
    return [l for _, l in above], [l for _, l in below], footer


def _bars_ago_fmt(n: int) -> str:
    """0 → 'now' (won't appear in confirmed pivots since min ago=5);
    1 → '1 bar ago'; N≥2 → 'N bars ago'."""
    if n == 0:
        return "now"
    if n == 1:
        return "1 bar ago"
    return f"{n} bars ago"
```

注：confirmed pivot 距今 ≥ N=5，所以 `_bars_ago_fmt` 实际只会被调用在 n ≥ 5 — 但保留 0/1 分支防御未来 N 减小。

### 4.4 整工具渲染组装

`_render_pivot_rows` 只负责 above/below 行 + prior 降级 footer。**swing 状态行**（insufficient / unavailable / window-note / no-pivots）由 `get_price_pivots` 主体决定后插入 — 因为它需要参考主 TF fetch 结果（df / Exception / df.empty / len < 100 / fractal 输出空），渲染 helper 不应再重复判断。

**主体伪代码**：

```python
async def get_price_pivots(deps: TradingDeps) -> str:
    # ... ticker / 4 路 OHLCV fetch（详见 §3.2） ...

    # 1. swing 算法 + swing 状态行确定
    swing_status: str | None = None
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    if isinstance(main_df_or_err, Exception):
        swing_status = "Swing pivots: temporarily unavailable"
    elif main_df_or_err is None or main_df_or_err.empty or len(main_df_or_err) < 11:
        got_bars = 0 if main_df_or_err is None or main_df_or_err.empty else len(main_df_or_err)
        swing_status = f"Swing pivots: insufficient data (need 11+ bars, got {got_bars})"
    else:
        bar_count = len(main_df_or_err)
        swing_highs, swing_lows = _compute_swing_pivots(main_df_or_err, n=5)
        no_pivot = not swing_highs and not swing_lows
        # 三态拆分（避免短窗口下 "有 pivot" 与 "无 pivot" 输出相同 → 语义歧义）
        if no_pivot and bar_count >= 100:
            swing_status = "(No swing pivots in 100-bar window)"
        elif no_pivot and bar_count < 100:
            swing_status = f"(Window: {bar_count} bars, less than 100 — no swing pivots found)"
        elif bar_count < 100:
            # 短窗口 + 至少 1 pivot
            swing_status = f"(Window: {bar_count} bars, less than 100)"
        # else: 100 bars + 至少 1 pivot → swing_status 保持 None

    # 2. prior 三档独立处理（详见 §4.2）
    prior_d = _get_prior_period_hl(daily_or_err)
    prior_w = _get_prior_period_hl(weekly_or_err)
    prior_m = _get_prior_period_hl(monthly_or_err)

    # 3. 渲染 helper
    above_rows, below_rows, prior_footer = _render_pivot_rows(
        swing_highs, swing_lows, prior_d, prior_w, prior_m, current_price,
    )

    # 4. 拼接顺序固定：header → current → above → below → swing_status → prior_footer
    sections: list[str] = [
        f"=== Price Pivots ({deps.symbol}, main TF: {deps.timeframe}) ===",
        f"Current Price: {current_price:,.2f}",
        "",
        "=== Levels Above Current Price ===",
        *(above_rows or ["(none)"]),
        "",
        "=== Levels Below Current Price ===",
        *(below_rows or ["(none)"]),
    ]
    if swing_status:
        sections.append("")
        sections.append(swing_status)
    if prior_footer:
        # swing_status 已存在则不再加空行；否则前置空行
        if not swing_status:
            sections.append("")
        sections.extend(prior_footer)
    return "\n".join(sections)
```

**位置规则总结**：

| 输出段 | 位置 | 触发条件 |
|--------|------|---------|
| `=== Levels Above ===` + 行 | 永远在 | 所有正常路径 |
| `(none)`（above 段内） | 替代 above_rows | above_rows 为空 |
| `=== Levels Below ===` + 行 | 永远在 | 所有正常路径 |
| `(none)`（below 段内） | 替代 below_rows | below_rows 为空 |
| swing 状态行 | above/below 之后 / prior_footer 之前 | swing_status 非 None |
| prior_footer 行 | 整体最末 | 任意 prior 状态非 ok |

### 4.5 Persona Layer 1 追加 bullet（草稿）

加在现有 `_build_layer1` 末尾（`OCO atomicity` bullet 之后）：

```python
- **Price pivots**: Use get_price_pivots to scan structural levels — swing
  highs/lows from the last 100 main-TF bars (Williams fractal N=5) plus
  prior daily/weekly/monthly H/L. Levels grouped above/below current price
  with distance % and bars-ago. Useful for placing SL/TP at structural
  levels rather than arbitrary percentages.
```

精确措辞 plan 阶段敲，本 spec 锁定要点：(1) 工具名；(2) 算法（fractal N=5 + last 100 bars）；(3) 上下分组；(4) "Useful for SL/TP at structural levels" 用法导引（与 Layer 1 既有 "place stops at meaningful prices, not arbitrary ones" 措辞对仗）。

### 4.6 REGISTERED_TOOL_NAMES 同步

`src/agent/trader.py:371` 的 `REGISTERED_TOOL_NAMES` list 加 `"get_price_pivots"`（按"感知 → 执行 → memory"分组，插入感知段末尾）。`tests/test_trader_agent.py:84` drift 测试硬编码 `len == 29` 改为 `== 30`，注释 `(18+10+1)` 改为 `(19+10+1)`。

## 5. 测试策略

### 5.1 算法层（~10 测试，新文件 `tests/test_price_pivots.py`）

合成 OHLCV `pd.DataFrame` 喂 `_compute_swing_pivots`：

1. **基础 fractal**：手工构造 30 bar，第 10 bar 是局部高点（左 5 + 右 5 都低）→ 期望返回 1 个 swing high
2. **严格不等 — 平台期不产 pivot**：第 10 bar high == 第 11 bar high → 期望返空
3. **严格不等 — 单边相邻相等**：第 10 bar high > 第 9 bar，但 == 第 11 bar → 期望不算 pivot
4. **多 pivot**：30 bar 含 2 swing high + 1 swing low → 全捕到，bars_ago 倒序
5. **单调上涨**：100 bar high 严格递增 → 返空
6. **单调下跌**：100 bar low 严格递减 → 返空
7. **最近 5 bar unconfirmed**：bar[95] 是局部最高但 bar[96-99] 数据不全 → 不返回（confirm_end = 94）
8. **数据不足**：len=10 < 2N+1=11 → 返 `([], [])`，不抛
9. **边界刚够**：len=11 → 仅可能产生 bar[5] 一个候选
10. **swing high 与 swing low 同 bar 双重 pivot**：单一 bar 在窄幅波动里 high 比左右都高 + low 比左右都低（"扩张型 fractal bar"），同时是 swing high 又是 swing low。算法应同时返两条记录，分别进 highs / lows 两个 list。测试构造 30 bar，bar[15] 的 high/low 振幅都比左右 5 bar 大 → 期望 highs 有 1 条、lows 有 1 条。

### 5.2 渲染层（~10 测试）

mock `_compute_swing_pivots` + `_get_prior_period_hl` 直接喂 `_render_pivot_rows`：

1. **正常满载**：2 swing high + 2 swing low + 3 prior 全 ok → 验证 above 段 5 行 + below 段 5 行 + footer 空
2. **above/below 分组正确**：swing high 在 current 下方时进 below 段（业务事实，不互斥）
3. **组内距离升序**：above 段 [+0.55%, +0.90%, +1.07%, +2.97%, +3.35%, +7.03%]
4. **`+`/`-` 符号**：above 全 +，below 全 -
5. **swing 行带 `bars ago`**：`Swing High: 66,890.00 (+0.55%, 23 bars ago)`
6. **prior 行不带 ago**：`Prior Daily H: 67,234.00 (+1.07%)`
7. **`Levels Above` 空**：渲染输出 `(none)`
8. **`Levels Below` 空**：同上
9. **prior 单源 insufficient → 进 footer**：`Prior Weekly H/L: insufficient data` 在 below 段下方
10. **prior 单源 unavailable → 进 footer**：`Prior Monthly H/L: temporarily unavailable`

### 5.3 降级层（~6 测试，集成 `get_price_pivots` 整工具）

mock `deps.market_data.get_ticker` + `deps.market_data.get_ohlcv_dataframe`：

1. **ticker 异常**：`get_ticker` 抛 → 整工具返回 `Price pivots (...): temporarily unavailable`，OHLCV 不被调用（验证 short-circuit）
2. **主 TF 异常 + 3 prior ok**：swing_status = `Swing pivots: temporarily unavailable`；above/below 仅含 prior 行
3. **主 TF 短窗口 (50 bars) + 3 prior `len(df) < 2`**：swing 算法照算，swing_status = `(Window: 50 bars, less than 100)`，prior_footer 含 3 行 `Prior {Daily/Weekly/Monthly} H/L: insufficient data`
4. **主 TF 短窗口 (50 bars) + 3 prior 抛异常**：swing_status = `(Window: 50 bars, less than 100)`，prior_footer 含 3 行 `temporarily unavailable`（区别于 #3 的 insufficient 路径，验证两条降级路径分别走通）
5. **主 TF `df.empty` + 3 prior 抛异常**：swing_status = `Swing pivots: insufficient data (need 11+ bars, got 0)`，above/below = `(none)`，prior_footer 含 3 行 `temporarily unavailable`（对应 §2.2 末尾 "主 TF df.empty + 全部 prior fetch 异常" edge 示例的视觉空但语义完整）
6. **主 TF 100 bars 正常 + 仅 1 路 prior 异常 + 2 路 prior ok**：swing_status = `None`；above/below 含正常 swing 行 + 2 路 prior 行（参与排序）；prior_footer 仅含 1 行 `Prior Weekly H/L: temporarily unavailable`。**关键**：验证 §4.4 拼接逻辑中 `if prior_footer and not swing_status:` 分支（footer 前需补空行），这是生产最常见的"主 TF 健康 + 单点 prior 抖动"降级组合，前 5 个测试都不命中

### 5.4 Fact-only regression（单函数多场景，扩展 `tests/test_fact_only_wordlist.py`）

**Wordlist 范围决策：per-test 局部 `PIVOTS_BANNED_WORDS`，不扩全局 `FACT_ONLY_BANNED_WORDS_RE`。**

理由：现有全局 `FACT_ONLY_BANNED_WORDS_RE` 主要含 N5 时期清理的 sentiment 类词（`bullish` / `bearish` / `oversold` / `overbought` 等），覆盖 19 个工具的回归测试。本 iter 新加的 `strong` / `weak` / `important` / `key` / `major` / `minor` / `critical` / `crucial` / `significant` 等是**结构 / 评价类词**，未经其他 19 工具的输出审计 — 直接扩全局可能：

- 在其他工具的内部错误信息 / docstring / 罕见输出路径中误报
- 引入未审过的全局约束，PR 范围蔓延

本 iter 测试有两层与现有约定的对应关系，需分清：

- **测试结构层**：单 async 函数串行跑多场景（pytest 计为 1 测试） — 与现有 `test_order_book_fact_only_4_scenarios` / `test_recent_trades_fact_only_4_scenarios` 的"`test_X_fact_only_N_scenarios`"风格一致 ✅
- **wordlist 作用域层**：`PIVOTS_BANNED_WORDS` per-tool 局部常量 — **新模式**。现有所有 4 个 per-tool fact-only 测试（order_book / recent_trades / multi_tf_snapshot / get_position）全部用全局 `FACT_ONLY_BANNED_WORDS_RE` + `FACT_ONLY_BANNED_PHRASES_RE`，没有 per-tool 局部 wordlist 的先例。本 iter 偏离全局的理由（避免对其他 19 工具引入未审过的全局约束）已在上方两条 bullet 列出，是一次有意识的**模式扩展**，不是与现有一致

**实现**（结构层与现有 `test_X_fact_only_N_scenarios` 风格对仗 — 单 async 函数串行跑 5 场景，pytest 计为 1 测试）：

**MockDeps 扩展**：现有 `tests/test_fact_only_wordlist.py:36-41` 的 `MockDeps` 不含 `timeframe` 字段，但 `get_price_pivots` 第一行就访问 `deps.timeframe`（§3.2）。**改 dataclass 加一行**（不影响现有 4 个测试 — 它们不读 `timeframe`）：

```python
@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    initial_balance: float = 10000.0
    exchange: AsyncMock = field(default_factory=AsyncMock)
    market_data: AsyncMock = field(default_factory=AsyncMock)
    technical: AsyncMock = field(default_factory=AsyncMock)
    timeframe: str = "5m"  # 新增 — 给 get_price_pivots 用
```

```python
# tests/test_fact_only_wordlist.py 新增

PIVOTS_BANNED_WORDS = (
    # 结构强度类
    "strong", "weak", "strongly", "weakly",
    # 重要性类
    "important", "unimportant", "key", "major", "minor",
    "critical", "crucial", "significant", "insignificant",
    # sentiment 类（继承自全局，per-test 显式列出避免依赖全局变更）
    "bullish", "bearish",
    # broken / breached（本 iter 1.2 非目标）
    "broken", "breached",
)
PIVOTS_BANNED_RE = re.compile(
    r"\b(" + "|".join(PIVOTS_BANNED_WORDS) + r")\b", re.IGNORECASE,
)


async def test_get_price_pivots_fact_only_5_scenarios(mocker):
    """Normal / swing-empty / short-window / main-TF-error / all-prior-error
    all fact-only (no banned wordlist hits).
    """
    scenarios = [
        ("normal_full", _build_normal_deps()),
        ("swing_empty", _build_monotonic_uptrend_deps()),
        ("short_window", _build_50bar_with_insufficient_prior_deps()),
        ("main_tf_error", _build_main_tf_error_with_prior_ok_deps()),
        ("all_prior_error", _build_main_tf_empty_with_prior_error_deps()),
    ]
    for name, deps in scenarios:
        output = await get_price_pivots(deps)
        matches = PIVOTS_BANNED_RE.findall(output)
        assert not matches, f"Banned words in scenario '{name}': {matches}"
```

注：5 场景一函数 → pytest 数 **1 个测试**（与现有 `test_order_book_fact_only_4_scenarios` 计数方式一致）。§6 Acceptance 总数 +29（fact-only 计数 1，降级 6，含 §5.3 #6 spacing 分支验证）。

**白名单豁免**（不在 PIVOTS_BANNED_WORDS）：`Swing High` / `Swing Low` / `Prior Daily H` / `Levels Above` / `Levels Below` / `bars ago` / `Current Price` / `insufficient data` / `temporarily unavailable` —— 业界标准结构标签或中性事实词。

**未来扩到全局**：若观察期发现这套 wordlist 在其他工具也有清理价值，独立 PR 把 PIVOTS_BANNED_WORDS 部分词项搬到全局并补 19 工具回归测试 —— **不**在本 iter 做。

### 5.5 Mock 数据策略

**OHLCV 合成**：手写 list of dicts 转 `pd.DataFrame`，timestamp 用 `pd.date_range(end=now, periods=N, freq="5min")`。直接喂 `_compute_swing_pivots`，不需要 mock 整 exchange。

**集成测试**：`MagicMock` deps.market_data，`get_ticker` 返 `Ticker(last=66523.40, ...)`，`get_ohlcv_dataframe` 用 `side_effect` 按 `(symbol, timeframe)` 返不同 fixture。

### 5.6 Persona / drift 测试

`tests/test_persona.py` **新增** 2 个测试函数（现有文件无 bullet 数硬编码断言）：

```python
def test_layer1_bullet_count_25():
    """Layer 1 bullet count drift guard. Bullet 定义为以 '\\n- **' 开头的行
    （tools-section bullet 格式：'- **<Tool>**: ...'），与 _build_layer1
    现有写法对仗。Iter 3 加 1 bullet → 24 + 1 = 25。
    """
    config = PersonaConfig(personality=None, trading_style=None)
    prompt = generate_system_prompt(config)
    # Guard: Layer 2 header 字符串校验，防 persona.py 改名后此测试静默 false-pass
    assert "## How to Think" in prompt, \
        "Layer 2 header changed; update split key in this test"
    layer1 = prompt.split("## How to Think")[0]
    bullet_count = layer1.count("\n- **")
    assert bullet_count == 25, f"Expected 25 Layer 1 bullets, got {bullet_count}"


def test_layer1_includes_get_price_pivots():
    """新工具描述存在 + 含关键术语，验证 §4.5 草稿要点未漂移。"""
    config = PersonaConfig(personality=None, trading_style=None)
    prompt = generate_system_prompt(config)
    assert "get_price_pivots" in prompt
    assert "## How to Think" in prompt, \
        "Layer 2 header changed; update split key in this test"
    layer1 = prompt.split("## How to Think")[0]
    # 草稿措辞要点（§4.5）：fractal / structural / above / below
    for keyword in ("fractal", "structural", "above", "below"):
        assert keyword in layer1.lower(), \
            f"Layer 1 bullet missing keyword '{keyword}'"
```

**bullet 计数方法说明**：现有 `_build_layer1` 采用 `- **<ToolName>**: <description>` 的 markdown bullet 格式（§2.1 persona Layer 1 全文可验证）。`prompt.count("\n- **")` 即为 tools-section bullet 数 — 不会误数 Market Context 段的非工具说明（那段没有 `- **` 前缀的 bullet）。

drift 测试硬编码 25 后，未来加 bullet 时同步改测试 — 与 `tests/test_trader_agent.py:84` 的 `len == 30` drift 防护逻辑同构（先 fail → 提醒同步 → 改硬编码）。

`tests/test_trader_agent.py:69` 已有的 `REGISTERED_TOOL_NAMES` drift 测试**不是** zero-effort 自动覆盖 —— 同一文件 line 84 处硬编码 `len == 29`，需配合 §1.3 改动一并改为 `== 30`（注释 `(18+10+1)` 改为 `(19+10+1)`）。`test_tool_call_instrumentation.py` 不参与 drift 防护。

## 6. Acceptance Criteria

1. ✅ `get_price_pivots(deps)` 工具存在，无参数，返回字符串
2. ✅ 主 TF + 1d + 1w + 1M 四路 OHLCV 各自独立 try/except，不级联
3. ✅ 输出按 above/below 分组 + 组内距离绝对值升序
4. ✅ swing 行带 `(N bars ago)`；prior 行无 ago 后缀
5. ✅ 所有降级场景输出明确文案（`insufficient data` / `temporarily unavailable` / `(no swing pivots)` / `(window: N bars, less than 100)`）
6. ✅ Layer 1 25 bullets，REGISTERED_TOOL_NAMES drift 测试通过
7. ✅ Fact-only regression 0 banned-word hit
8. ✅ 测试 786 → ≈815 (+29: 算法 ~10 / 渲染 ~10 / 降级 ~6 / fact-only 1（单函数 5 场景）/ persona drift 2)，零 regression
9. ✅ 工具输出 token ≤ 800 in 满载场景
10. ✅ Williams fractal **严格不等**：平台期 / 单边相邻相等场景测试都通过

## 7. 观察期 Follow-up 候选

观察期数据决定第二版升级方向，本 iter 不预留 hook：

1. **Volume profile / volume-weighted ranking**：观察期发现 agent 把"高量 pivot"和"低量 pivot"同等对待 → 第二版加 volume column
2. **Touch count / "tested N times"**：观察期发现 agent 误用孤立 pivot → 第二版加 touch_count 字段
3. **自适应 N**：观察期发现固定 N=5 在低波动期产 pivot 太多 / 高波动期太少 → 第二版按 ATR 自适应
4. **接受 timeframe 参数**：观察期发现 agent 反复在主 TF 之外调 — 但目前 HTF tool 已覆盖 4h/1d/1w/1M 的 100/20-period range，可能 redundant
5. **"Broken / breached" 标签**：观察期发现 agent 误用已被穿过的 pivot 当 SL → 加事实标签（+ fact-only 边界重审）
6. **Today (forming) H/L**：观察期发现 agent 漏关注当日突破点 → 加 today 行
7. **Pivot 与 order book 联动**：观察期发现 agent 在 pivot 附近做单时未交叉验证 order book 流动性 → persona prompt 加联动提示

均**不进 Iter 3 scope**。

## 8. 风险与 Trade-off

### 8.1 fractal N=5 在 5m TF 下的 pivot 密度

100 bars × 5min = 8.3 小时窗口。横盘震荡 8 小时通常产 5-15 swing pivot，急速单边可能产 0-2，趋势中可能产 3-8。预期范围内自然有界。**风险**：极端波动场景（重大宏观事件）一日多次反转 → 100 bars 可能产 20+ pivot → token 接近上限 800。**缓解**：spec §1.2 已明确"不按重要性截断" + token 上限只是预期不是硬上限；观察期数据驱动是否需要 cap。

### 8.2 严格不等的"过严"风险

横盘震荡频繁出现 `high == prev_high` 时，严格 `>` 会过滤掉很多"近似 pivot"。**判断**：横盘期"近似 pivot"本身价值有限（agent 在横盘里靠多个支撑/阻力定位置 ≠ 靠一个被磨平的高点），过滤掉合理。观察期评估。

### 8.3 1M timeframe iloc[-2] 的"上月"语义

OKX / CCXT 多数交易所的 `1M` K 线按自然月（UTC 月初切月）。月初第 1 天调用工具 → `iloc[-2]` 是上月完整 K 线 — 语义正确。月内任意天调用 → `iloc[-2]` 仍是上月，`iloc[-1]` 是当月在形成的 K 线（不输出）。语义完整，无需特殊处理。

### 8.4 Ticker.last 抖动 vs 距离 % 抖动

ticker 实时抖动会让 above/below 分组在边界 pivot 上"翻腾"（如 swing high 距离 -0.01% / +0.01% 来回切组）。**判断**：抖动幅度极小（<5bp），跟 SL/TP 决策的实际 buffer（通常 ATR 倍数 ≥ 1×ATR ≈ 0.5-2%）完全不在同一量级，不构成实际问题。观察期监控。

### 8.5 文件长度

`tools_perception.py` 当前 1409 行，本 iter +180-220 → ~1600 行。N7 议题（Iter 4 重组）一并处理。本 iter 不引入文件拆分（避免和 N7 范围重叠）。

### 8.6 Persona Layer 1 25 bullet

新增 bullet 让 Layer 1 数从 24 → 25，超 N7 议题阈值（已超 23 即 N7 触发）。本 iter 仍 append（spec §0.3 硬约束 5）；N7（Iter 4）是设计来处理 25-bullet 完整工具集的重组。

---

**End of spec.**
