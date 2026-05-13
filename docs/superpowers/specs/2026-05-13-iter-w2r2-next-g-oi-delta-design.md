# Iter W2-R2-Next-G — `get_derivatives_data` OI history anchors + delta

**Date**: 2026-05-13
**Branch**: `iter-w2r2-next-g`
**Source**: `.working/sim8-w2-tool-optimization-roadmap.md` §6.1 Iter 4 (R2-Next-G) + sim #8 实证 deep dive（本会话）
**前置依赖**: 无（独立 PR，与 R2-Next-D / E / H 代码区域不重叠）
**预估工作量**: ~80-100 行 src + ~21 测试新增 / 5-10 测试校准；~3-4 小时（spec / plan / impl / review）

---

## 1. 背景与动机

### 1.1 议题来源

承接 sim #8 W2 观察期数据分析（`.working/sim8-w2-tool-optimization-roadmap.md` §3.5.4「V 榜深 dive — V6 `get_derivatives_data`」F-D1 议题）。Roadmap §6.1 中本议题归入 Iter 4，是该 roadmap 唯一未启动的 in-scope 迭代。R2-Next-D / E / H 三 sibling spec 均一致 hand-off 给本议题作 separate mini-PR。

### 1.2 Sim #8 实证（本会话 deep dive）

数据源：
- DB: `data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`
- Session log: `logs/session_8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3.log` (72,980 行)
- 19.2h / 178 cycles / 103 `get_derivatives_data` calls / 100% ok

**OI 引用 narrative 实证**:

| 指标 | 值 |
|---|---|
| `get_derivatives_data` 整体 reasoning 引用率（R2-8c spec L37 baseline）| 79% |
| Distinct cycles reasoning 中引用 OI 数值 | 25 / 178 |
| Open/close 决策 cycles fetched derivs | 13 / 16 |
| 其中实际引用 OI 数值 | **4 / 13 = 31%**（远低于 79% 平均）|
| 显式 OI delta need 表达（关键证据 2 处 + 1 stable 判断）| 3 cycles |
| OI 跨 sim 漂移范围 | $2.71B – $2.95B（~8.5% 跨 19.2h，~0.4% 相邻 fetch）|

**3 处关键 narrative（驱动议题立项）**:

1. **cycle `dc3d1b8a`** — 显式 need 表达：
   > `OI: $2.87B — need to track if OI is increasing or decreasing`
2. **cycle `e6929b2c`** — 跨 cycle 心算 + 解读 short cover：
   > `Shorts: 58.4%, OI $2.74B (slight decline, some covering)` + earlier `OI dropped slightly from $2.76B to $2.74B`
3. **cycle `19488b64`** — 跨 cycle 心算稳定性：
   > `OI $2.77B — stable`

### 1.3 痛点 root cause（原则 7 触发）

当前 `tools_perception.py:880-892` OI 字段渲染：

```
Open Interest: $2.92B
```

**单点 raw，无窗口标签**——直接违反 `docs/superpowers/principles/tool-design-principles.md` 原则 7「输出与命名的表达友好——字段必带标签 / 单位 / 窗口」。Agent 必须**跨 cycle 自己心算 delta**（如 e6929b2c 实证）才能形成 OI 趋势视角。

### 1.4 议题 ROI 校准（与 roadmap §3.5.4 偏差）

| Roadmap §3.5.4 暗示 | Spec deep dive 实证 |
|---|---|
| "narrative 强烈" / V 榜信号 | 仅 2 处显式 need / 1 处跨 cycle 心算 |
| 隐含高决策利用率 | **关键决策 cycle 实际利用率 31%** |
| 直接化解多次手算 | 直接化解 1 处 e6929b2c-style + 1 处 dc3d1b8a need |

**议题级别从 🟡 M → 🟢 L**：实证支持比 roadmap 暗示弱，但议题仍然成立 —
- 原则 7（窗口缺失）一致性补齐
- 1 处 e6929b2c-style 跨 cycle 心算被直接化解
- ~70 行小成本
- 3 sibling spec 一致 hand-off 不能空头支票

**spec language 严格约束**（per 本会话修正建议）：
- 不承诺改善决策质量 / 入场出场 timing
- 承诺范围 = 补齐原则 7 + 化解已观察的跨 cycle 心算 narrative
- 闭环依据 = §6.2 W3 gate 表（量化阈值）

### 1.5 OKX API 关键技术发现

ccxt 统一 API `fetch_open_interest_history(symbol, '1h', limit)` 返回**货币级聚合 OI**（OKX BTC 全合约总和 ~$3.43B），不是单合约（BTC-USDT-SWAP ~$2.69B）—— 会与现 `fetchOpenInterest`（per-instId, $2.69B）信号混源，违反原则 3。

OKX 提供未被 ccxt 统一包装的 raw endpoint：

```
publicGetRubikStatContractsOpenInterestHistory({
    instId='BTC-USDT-SWAP', period='1H', limit='26'
})
→ {"data": [[ts_ms, oi_contracts, oi_base, oi_usd], ...]}
   ─ ts_ms:        period start timestamp (ms)
   ─ oi_contracts: OI in contract count (dimensionless)
   ─ oi_base:      OI in base currency (e.g. BTC)
   ─ oi_usd:       OI in USD value
```

实测：raw endpoint newest record `oi_usd` 与 single-point `fetchOpenInterest.open_interest_value` 一致（$2.69B match），即同 instId 同口径同 USD 值。本议题统一走该 raw endpoint。

**period casing 注**：OKX raw endpoint 严格要求 `'5m' / '1H' / '1D'`（实测 lowercase 返 51000 "Parameter period error"）。ccxt unified `fetch_open_interest_history` 对外接受 lowercase '1h'，内部 `options['timeframes']` mapping 翻译为 '1H' 后才 validate。本 spec ABC / service 层暴露 lowercase（与项目既有 `fetch_ohlcv(timeframe='1h')` 惯例一致），translation 落在 `okx.py` / `simulated.py` 内部。

---

## 2. 工具签名 + Components

### 2.1 新 dataclass — `src/integrations/exchange/base.py`

```python
@dataclass
class OpenInterestHistoryPoint:
    """One historical OI snapshot at a given timestamp.

    open_interest_value is USD-denominated and shares semantics with
    OpenInterest.open_interest_value (same single-contract scope,
    just at a point in time).
    """
    timestamp: int
    open_interest: float  # base-currency amount
    open_interest_value: float  # USD value
```

`BaseExchange` ABC 新增方法：

```python
async def fetch_open_interest_history(
    self,
    symbol: str,
    period: Literal["5m", "1h", "1d"] = "1h",
    limit: int = 26,
) -> list[OpenInterestHistoryPoint]: ...
```

ABC 暴露 lowercase period 与项目既有 timeframe 惯例一致（`fetch_ohlcv(timeframe='1h')` / `get_higher_timeframe_view(timeframes=['4h','1d'])`）；OKX-native casing 翻译落在 §2.2 / §2.3 实现层。

### 2.2 OKXExchange 实现 — `src/integrations/exchange/okx.py`

```python
# Module-level mapping (落 base.py 顶部 OR okx.py 顶部 — plan 决):
_OKX_OI_PERIOD = {"5m": "5m", "1h": "1H", "1d": "1D"}


async def fetch_open_interest_history(
    self,
    symbol: str,
    period: Literal["5m", "1h", "1d"] = "1h",
    limit: int = 26,
) -> list[OpenInterestHistoryPoint]:
    inst_id = self._client.market(symbol)["id"]  # BTC/USDT:USDT -> BTC-USDT-SWAP
    raw = await self._client.public_get_rubik_stat_contracts_open_interest_history({
        "instId": inst_id, "period": _OKX_OI_PERIOD[period], "limit": str(limit),
    })
    rows = raw.get("data") or []
    # OKX rubik 4-col schema: [ts_ms, oi_contracts, oi_base, oi_usd].
    # r[1] (contract count) intentionally not consumed — agent uses USD anchor only.
    points = [
        OpenInterestHistoryPoint(
            timestamp=int(r[0]),
            open_interest=float(r[2]),        # oi_base (base-currency amount)
            open_interest_value=float(r[3]),  # oi_usd (USD value)
        )
        for r in rows
    ]
    points.reverse()  # OKX returns newest-first; flip to oldest-first
    return points
```

### 2.3 SimulatedExchange 实现 — `src/integrations/exchange/simulated.py`

```python
async def fetch_open_interest_history(
    self,
    symbol: str,
    period: Literal["5m", "1h", "1d"] = "1h",
    limit: int = 26,
) -> list[OpenInterestHistoryPoint]:
    self._validate_symbol(symbol)
    if not hasattr(self, "_ccxt"):
        raise RuntimeError("Exchange not started — call start() first")
    try:
        raw = await self._ccxt.public_get_rubik_stat_contracts_open_interest_history({
            "instId": self._ccxt.market(symbol)["id"],
            "period": _OKX_OI_PERIOD[period],
            "limit": str(limit),
        })
    except ccxt.RateLimitExceeded as e:
        raise RateLimitHit(f"Sim open interest history: {e}") from e
    rows = raw.get("data") or []
    points = [
        OpenInterestHistoryPoint(
            timestamp=int(r[0]),
            open_interest=float(r[2]),
            open_interest_value=float(r[3]),
        )
        for r in rows
    ]
    points.reverse()
    return points
```

3 guard 沿用 simulated.py:1011-1024 `fetch_open_interest` 既定 pattern：
1. `self._validate_symbol(symbol)` — symbol 白名单
2. `hasattr(self, "_ccxt")` — start() 未跑时 raise RuntimeError
3. `ccxt.RateLimitExceeded → RateLimitHit` 包装

`_OKX_OI_PERIOD` 与 §2.2 共享（落 `okx.py` 顶部 OR `base.py` 模块作用域，plan 决；不重复定义）。

### 2.4 MarketDataService — `src/integrations/market_data.py`

```python
async def get_open_interest_history(
    self,
    symbol: str,
    period: str = "1h",
    limit: int = 26,
) -> list[OpenInterestHistoryPoint]:
    return await self._derivatives_cache.get_or_fetch(
        f"oi_history:{symbol}:{period}:{limit}", _DERIVATIVES_TTL,
        lambda: self._exchange.fetch_open_interest_history(symbol, period, limit),
    )
```

复用现有 `_derivatives_cache` + `_DERIVATIVES_TTL=180s`，不引入新 TTL 常量。Cache key 含完整 args 防止参数歧义。

**`get_open_interest` (single-point) 处置**：本议题改造后 `get_derivatives_data` 不再 caller。Plan 阶段必须执行：

```bash
grep -rn "get_open_interest\b\|fetch_open_interest\b" src/ tests/
```

穷举 `market_data.get_open_interest` / `BaseExchange.fetch_open_interest` / `OKXExchange.fetch_open_interest` / `SimulatedExchange.fetch_open_interest` 全 caller。**若仅 `get_derivatives_data` 一个 caller**（grep 后预期结果），全 4 处一并删除（含 ABC abstractmethod + dataclass `OpenInterest` 若再无引用）。不预设保留以未来用 — per CLAUDE.md "Don't design for hypothetical future requirements" + 原则 3（两路径 OI 源潜在 confusion）。

### 2.5 Render layer — `src/agent/tools_perception.py`

替换 `get_derivatives_data` 中 `oi` 分支（原 line 880-892）：

```python
# Open interest history (replaces single-point fetch).
if isinstance(oi_hist, Exception) or not oi_hist:
    field_lines.append("Open Interest: (unavailable)")
else:
    current = oi_hist[-1]  # newest, after .reverse() in fetch
    oi_str = _format_oi_usd(current.open_interest_value)
    anchors = _derive_oi_anchors(oi_hist, current)
    if anchors:
        field_lines.append(f"Open Interest: {oi_str} ({anchors})")
    else:
        field_lines.append(f"Open Interest: {oi_str}")
    if current.timestamp:
        timestamps_ms.append(current.timestamp)
```

`asyncio.gather` 三路调用对应改为 funding / **oi_history** / lsr：

```python
funding, oi_hist, lsr = await asyncio.gather(
    deps.market_data.get_funding_rate(symbol),
    deps.market_data.get_open_interest_history(symbol, "1h", 26),
    deps.market_data.get_long_short_ratio(symbol),
    return_exceptions=True,
)
```

模块作用域辅助函数：

```python
def _format_oi_usd(v: float) -> str:
    """Format OI USD value with auto-scale unit (B / M / raw)."""
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


def _derive_oi_anchors(
    points: list[OpenInterestHistoryPoint],
    current: OpenInterestHistoryPoint,
) -> str:
    """Render '1h ago $X.XXB, +Y.Y%; 24h ago $X.XXB, -Y.Y%' fragments.

    Anchor indices measured from end (points[-1] = current). Partial-history
    degrades gracefully: insufficient or zero-value anchors are skipped.
    """
    fragments: list[str] = []
    for label, idx_from_end in [("1h ago", 2), ("24h ago", 25)]:
        if len(points) < idx_from_end:
            continue
        anchor = points[-idx_from_end]
        if anchor.open_interest_value <= 0:
            continue
        delta_pct = (current.open_interest_value / anchor.open_interest_value - 1) * 100
        fragments.append(
            f"{label} {_format_oi_usd(anchor.open_interest_value)}, {delta_pct:+.1f}%"
        )
    return "; ".join(fragments)
```

### 2.6 docstring 更新 — `src/agent/trader.py:271-287`

```python
"""Get derivatives market data: funding rate, open interest (with 1h/24h
anchors and percent change), and long/short ratio.

Positive funding rate means longs pay shorts; negative means shorts pay
longs (settlement interval varies by contract — see next settlement time
in output). Open interest is total outstanding contracts in USD, shown
with anchor values from 1h ago and 24h ago plus percent change so trend
direction is explicit. Anchor labels correspond to OKX 1H-bar boundaries
and may differ from wall-clock 1h/24h offsets by 0-60 minutes when the
latest bar is still in progress. Long/short ratio is the ratio of long
vs short account positions. Output ~180-260 tokens.

Args:
    symbol: trading symbol; None uses the currently traded pair.
"""
```

Layer 1 (persona) **不动** — `get_derivatives_data` 现有 perception bullets 抽象描述足够，per principle 8 + N7 DRY 反转，工具描述由 pydantic-ai/griffe sniff 自动传 LLM。

---

## 3. Data Flow & Output Examples

### 3.1 调用链

```
agent ─call─> get_derivatives_data(symbol)
                │
                ▼
              asyncio.gather(
                ├── deps.market_data.get_funding_rate(symbol)
                ├── deps.market_data.get_open_interest_history(symbol, '1h', 26)
                └── deps.market_data.get_long_short_ratio(symbol)
              , return_exceptions=True)
                │
                ▼
              cache hit (≤180s) OR cache miss → exchange fetch → parse → cache
                │
                ▼
              render: Funding (unchanged) / OI (new) / L:S (unchanged) / Data as of
```

### 3.2 完整输出示例 (happy path)

```
=== Derivatives Data (BTC/USDT:USDT) ===
Funding Rate: +0.0014% (next settlement in 4h 12m)
  Positive rate — longs pay shorts
Open Interest: $2.92B (1h ago $2.93B, -0.5%; 24h ago $2.91B, +0.3%)
Long/Short Ratio: 0.66 (39.9% long / 60.1% short)
Data as of: 2026-05-13 14:00 UTC
```

### 3.3 退化态示例

| 场景 | 输出片段 |
|---|---|
| history 异常 (rate limit / network) | `Open Interest: (unavailable)` |
| history 返回 1 条 | `Open Interest: $2.92B`（无 anchor）|
| history 返回 2 条 | `Open Interest: $2.92B (1h ago $2.93B, -0.5%)` |
| history 返回 25 条（OKX 当前 1h 未完结时常见）| 满 anchor，24h anchor 来自 points[-25] |
| history 返回 26 条（满 buffer）| 满 anchor，oldest record 不参与渲染 |
| anchor.open_interest_value ≤ 0 | 该 anchor 跳过，其他保留 |
| 全 3 derivatives 异常 | R2-8c L2 全失败 section: `Error: Temporarily unavailable (all 3 data sources failed).` |

### 3.4 Token 估算

| 场景 | Baseline | 新版 | Δ |
|---|---|---|---|
| Happy path 全字段 | ~155 tokens | ~180 tokens | +25 |
| Per-field fallback (OI 缺) | ~140 | ~140 | 0 |
| 全失败 L2 | ~25 | ~25 | 0 |

Sim 估算：103 calls × +25 tokens = **+2.6k tokens / 19.2h sim**，占总 14.36M 的 0.018% — 远低于 R2-8c reasoning gain 1% 阈值。

---

## 4. Error Handling & Failure Semantics

### 4.1 失败模式分类（per 原则 6）

| 失败源 | 分类 | 处置 |
|---|---|---|
| `RateLimitExceeded` (OKX 503/429) | 真异常 | simulated.py / okx.py 包装为 `RateLimitHit`；`asyncio.gather(return_exceptions=True)` 捕到 → OI section `(unavailable)` |
| 其他 ccxt exception (network / parse / decode) | 真异常 | 同上 |
| `raw.data` missing / `[]` | 退化态 | `oi_hist = []` → render path 走 `(unavailable)`（与异常同处置）|
| < 2 records | 退化态 | 仅显示 current 单点（无 1h anchor）|
| < 25 records | 退化态 | 1h anchor 显示，24h 跳过 |
| `anchor.open_interest_value ≤ 0` | 退化态 | 该 anchor 跳过（defensive 防 div-by-zero）|
| 全 3 derivatives 异常 | 全失败档 | R2-8c L2: 单 `Error:` 行，不渲染 3 个 (unavailable) |

### 4.2 与原则 1 (fact-only) 校验

- 退化态 fragment 缺失时**不写解释词**（如 "history insufficient"）— 自然降级保留 fact
- 完全失败 `(unavailable)` 沿用 R2-8c 已固化的 fact-only 模式
- 不输出 "anchor missing" / "delta n/a" 类元信息字符串

### 4.3 与原则 6 (失败语义区分) 校验

- 真异常 → `(unavailable)` field 标记（perception 类工具 reject 形式）
- 状态不存在 / 数据不全 → idempotent ok（best-effort 输出，不抛异常）
- 两类语义不混在同一 enum，各走 sub-path

### 4.4 边界 case 决策

| 边界 case | 决策 |
|---|---|
| OKX 返回 `code != '0'` (API biz error) | ccxt 上抛 ExchangeError → `(unavailable)` |
| OKX rate limit 仅命中 history 端点 | OI section per-field fallback；funding / L:S 不受影响 |
| anchor timestamp 与 expected interval drift | 不做 drift detection — agent 看到的是 OKX 时间锚（与 funding/L:S 同 trust 边界）|
| OKX 实际返 < limit 条（最新 1h 未完结）| `limit=26` buffer 设计已消化此 case：实际拿到 25 条仍足以填满 24h anchor (points[-25]) |

---

## 5. Testing Strategy

### 5.1 测试层次

| 层次 | 比例 | 用途 |
|---|---|---|
| Unit (pure / mocked) | 70% | 公式 / 退化 / 边界 |
| Integration (mocked ccxt) | 25% | 端点拼接 / 失败模式 |
| Contract (real OKX, opt) | 5% | endpoint shape 漂移哨兵（不入 CI 必跑路径）|

### 5.2 Unit tests — `tests/test_tools_perception_oi_history.py` (新)

**Render 公式 (8 cases)**:

| 测试名 | 输入 | 期望输出片段 |
|---|---|---|
| `test_oi_render_happy_path_inline` | 26 records | `$2.92B (1h ago $2.93B, -0.3%; 24h ago $2.91B, +0.3%)` |
| `test_oi_render_positive_deltas` | 24h-ago $2.50B, current $2.92B | `24h ago $2.50B, +16.8%` |
| `test_oi_render_zero_delta` | 全等于 current | `+0.0%` |
| `test_oi_render_million_scale` | $850M | `$850M` (1e6 auto-scale) |
| `test_oi_render_exactly_25_records` | 恰 25 records | 24h anchor 来自 points[-25]（24h-anchor 最小边界）|
| `test_oi_render_exactly_2_records` | 恰 2 records | 仅 `1h ago` 显示（1h-anchor 最小边界）|
| `test_oi_render_1_record` | 仅 1 record | 无 anchor，单点 |
| `test_oi_render_anchor_zero_skipped` | 1h-ago.usd=0 | `1h ago` 跳过，`24h ago` 保留 |

**Service / fetch layer (4 cases)**:

| 测试名 | 模拟 | 验证 |
|---|---|---|
| `test_market_data_get_oi_history_cache_hit` | 2 次连续调，TTL 内 | 第 2 次不调 exchange |
| `test_market_data_get_oi_history_ttl_expiry` | 第 2 次在 TTL 外 | 第 2 次再调 exchange |
| `test_okx_fetch_oi_history_parses_raw_response` | mock raw newest-first | 返回 reversed 到 oldest-first |
| `test_okx_fetch_oi_history_empty_data` | raw `{"data": []}` | 返回 `[]` |

**Failure path (7 cases) — 与 §4 一一对应**:

| 测试名 | 注入 | 验证 |
|---|---|---|
| `test_derivs_oi_history_rate_limit` | history raises RateLimitHit | OI `(unavailable)`，funding/lsr 正常 |
| `test_derivs_oi_history_empty_list` | history returns `[]` | OI `(unavailable)`，funding/lsr 正常 |
| `test_derivs_oi_history_one_record` | history returns 1 element | `$X.XXB`（无 anchor）|
| `test_derivs_oi_history_two_records` | history returns 2 elements | 仅 `1h ago` |
| `test_derivs_oi_history_anchor_zero` | points[-25].open_interest_value=0 | 24h anchor 跳过 |
| `test_derivs_all_three_fail` | 全 3 raise | L2 single Error line |
| `test_derivs_oi_history_fail_others_ok` | history fail / funding+lsr ok | OI line `(unavailable)`，无连锁 |

### 5.3 Integration — `tests/test_simulated_exchange.py` 顺手 extend

`test_simulated_fetch_open_interest_history_returns_list_of_points` — simulated.start() 后调 fetch_open_interest_history，验证返回 `list[OpenInterestHistoryPoint]` 长度 ≥ 1 且字段非空。与现有 simulated `fetch_open_interest` test 同 pattern（实跑 OKX，CI 中可依网络条件 skip）。

### 5.4 Drift guard

**T-DG-OI-1**: snapshot golden output 含 `Open Interest:` line + anchor format → 断言 substring 含 `"(1h ago "` 与 `"24h ago "` 字面。

落位：`tests/test_drift_guards.py` 复用现有 file（与 R2-8c T-DG-1 lint 同类不同 case）。

### 5.5 TDD 顺序

参考 `superpowers:test-driven-development`:

1. §5.2 render 公式 case 1-3 → red
2. 实现 `_format_oi_usd` + `_derive_oi_anchors` → green
3. §5.2 退化 case 6-8 → red → 加分支 → green
4. §5.2 service layer test → 实现 `get_open_interest_history`
5. §5.2 exchange layer test → 实现 `fetch_open_interest_history` (base / okx / simulated)
6. §5.2 failure path → 改 `get_derivatives_data` 协调
7. §5.4 drift guard

### 5.6 现有测试影响面

- `tests/test_tools_perception.py` 中 derivatives 相关 mock 校准（`get_open_interest` → `get_open_interest_history`）— 估 ~5-10 处
- `tests/test_market_data.py` 顺手 extend get_open_interest_history cache test
- `tests/test_okx.py` 保留单点 `fetch_open_interest` 现有 tests（method 不删）

### 5.7 测试不做的部分（YAGNI）

- OKX raw 各种异常 HTTP code（ccxt 包装层已 cover）
- `_format_oi_usd` / `_derive_oi_anchors` 100% 分支覆盖（render path tests 隐式覆盖）
- freezegun / 时钟 mock（anchor 不依赖 wall-clock）
- perf test（25 records / call 远低于 budget）

---

## 6. Success Criteria & W3 Hard Gate

### 6.1 实施完成 acceptance

- [ ] §5.2 / §5.3 所有测试通过；1487 现 tests 不 regress
- [ ] `make lint` / `mypy` / `ruff` clean
- [ ] 实 simulated sim (10-20 min smoke) — `get_derivatives_data` 输出新格式无 crash + token 数符合 §3.4 估算
- [ ] OI line 总长 < 100 字符 happy path
- [ ] 维持 R2-8c §4.2.10 sectioning 风格（单 section，无新 `=== Section ===`）

### 6.2 W3 sim 后量化 gate

**目标**：W3 sim（拟 ≥18h、≥150 cycles、≥80 `get_derivatives_data` calls）后从 DB 抽取 **OI delta 引用率**。

**分母对齐 baseline**：§1.2 baseline 31% 的分母是 entry/close decision cycles（13）— hold cycle 占多数会稀释信号，且 entry/close 才是真正 utility metric。本 gate 公式与 baseline 同口径：

```
oi_delta_reference_rate =
  count(distinct entry/close cycles with reasoning matching <OI_DELTA_PAT>)
  / count(distinct entry/close cycles with get_derivatives_data success call)
```

**`<OI_DELTA_PAT>` 候选正则**（plan 阶段精化 + sim #8 dry-run 验证）：

```regex
OI\s+(?:rose|dropped|fell|stable|increased|decreased)
 | OI(?:[: ])?\s*\$?[0-9.]+B?\s*(?:\(|→)
 | (?:1h|24h)\s+ago\s+\$
 | OI\s+[+-]?\d+\.\d+%
```

避开 §1.2 报告中提到的 false-positive 风险（`OI.*[+-]\d` 会命中混合行）。

**Plan 阶段 dry-run 要求**: 用 sim #8 数据跑候选正则，命中 cycle 集合应**精确等于** §1.2 中那 4 个 cycles (9f030bb0 / 67a294bf / 7116c6c1 / 994dab79) + 必要时加入手算 narrative cycle (e6929b2c / dc3d1b8a / 19488b64) 但这些不在 entry/close 集合内，仅作辅助验证。

**Sim #8 baseline (single-point only)**: 31% (4/13 entry/close cycles 引用 OI 数值)

**Gate 决策**:

| W3 实测 | 行动 |
|---|---|
| ≥ 60% | ✅ **保留** — 改造成功，议题闭环 |
| 50-60% | ⚠️ **观察** — 边际改善；继续 W4+ 监控，不立即改 |
| 31-50% | ⚠️ **触发 follow-up** — 改善有限；优先评估 **docstring promo**（工具层，与原则 8 一致）；若 docstring promo 后续 W4 仍不达标，再考虑 **Layer 1 nudge**（persona 层，属原则 8 last-resort）|
| < 31% | 🛑 **降级 / 回退** — 改造后 utilization 反而下降是 regression；考虑 simplify (remove anchor 仅留 delta) 或 wontfix-by-design |

### 6.3 Secondary metrics（信息项，非 gate）

| 指标 | 关注点 |
|---|---|
| `get_derivatives_data` reasoning 引用率（整体）| sim #8 baseline 79%；新版应 ≥ 75%（不 regress 即可）|
| Same-cycle 多调比例 | sim #8 ~0%；新版应 ≤ 5% |
| Derivs section 平均 token | sim #8 ~155 tokens；新版预算 ≤ 200 tokens |
| Narrative 含 `1h ago` / `24h ago` 字面 | 新指标，无 baseline；记观察值 |

### 6.4 Spec language 自查（per 修正建议 1）

- [x] **不**承诺 "improve decision quality" / "better entry/exit timing"
- [x] **不**承诺 "agent will leverage delta to ..." 预测性叙述
- [x] **承诺范围**: 补齐原则 7 缺失窗口 + 化解 e6929b2c-style 跨 cycle 心算 + 给 agent 提供 OI 变化方向的事实视角
- [x] **W3 数据驱动**: §6.2 阈值表是唯一闭环依据

### 6.5 W3 触发动作（memory 候选）

W3 sim 完成后:
- 抽 reasoning 引用率（§6.2 公式）
- 抽 secondary metrics（§6.3）
- 与 R2-Next-D / E / H 三 sibling 一并 review（共享 sim batch）
- 结论文档：`.working/sim-w3-r2-next-g-validation.md`

---

## 7. 议题立项 Checklist（CLAUDE.md mandated）

承接 `docs/superpowers/principles/tool-design-principles.md` §4：

| 原则 | 自检 |
|---|---|
| **1 fact-only** | ✓ anchor + delta 全 fact-only；docstring 增量是事实陈述非 "X for Y" 指导；非执行类不涉 clamp |
| **2 心智对齐** | ✓ e6929b2c `from $X to $Y` 原生表达被新格式覆盖；docstring 沿用工具事实陈述；手算痕迹仅 2-3 处弱信号 → ROI 已下调（§1.4），spec language §6.4 严格限定 |
| **3 唯一权威来源** | ✓ OI 统一从 `publicGetRubikStatContractsOpenInterestHistory` (per-instId)；舍弃 ccxt unified `fetchOpenInterestHistory` (per-ccy 聚合 $3.43B 货币级混源)；history newest 与 `fetchOpenInterest` 同 instId 同口径同 USD 值（实测 $2.69B match）|
| **4 信号补齐优先** | ✓ 不新增工具，仅补齐现有 `get_derivatives_data` OI 字段已 fetch underlying data 的窗口维度 |
| **5 接口闭环** | ✓ same-cycle 多调 ~0% 不存在闭环议题；method default `period='1h', limit=26` 直接 hardcode 给 `get_derivatives_data`，agent 接口表面零变化 |
| **6 失败语义** | ✓ rate limit / network → `(unavailable)` (R2-8c L3 per-field)；history 不全 → 自然降级；不混 enum |
| **7 表达友好** | ✓ **议题主原则** — 原 `Open Interest: $2.92B` 缺窗口正是 F-D1 痛点；新格式 `(1h ago $X.XXB, ±Y%; 24h ago $X.XXB, ±Y%)` 明示 window + unit + sign；维持 R2-8c §4.2.10 sectioning 风格 |
| **8 信任 agent + 工具优先** | ✓ 工具能力 + docstring 已 self-contained；persona Layer 1 不动；W3 31-50% 触发评估 docstring promo（工具层，原则 8 一致），仅 docstring promo 后 W4 仍不达标才考虑 Layer 1 nudge（last-resort），初版不预设任何 nudge |
| **元 实证优先** | ✓ sim #8 DB 全维度引用（103 calls / 100% ok / 25 cycles OI ref / 4 of 16 entry-close ref / 2 显式 delta narrative + 1 stable 判断 / cross-sim OI 漂移 $2.71B-$2.95B / single-point vs history value match）|

---

## 8. 历史 spec / memory 关系 + Open questions

### 8.1 Hand-off / 承接关系

| 引用 | 关系 |
|---|---|
| `.working/sim8-w2-tool-optimization-roadmap.md` §6.1 Iter 4 | **承接** — roadmap 唯一未启动 in-scope iter |
| `.working/sim8-w2-tool-optimization-roadmap.md` §3.5.4 F-D1 | **承接 + ROI 校准** — 痛点定性正确；ROI 在本 spec deep dive 后下调 |
| `docs/superpowers/specs/2026-05-11-iter-w2r2-next-d-multi-tf-design.md` L146 | **接收 hand-off** — "OI rate-of-change — independent R2-Next-G spec" |
| `docs/superpowers/specs/2026-05-12-iter-w2r2-next-e-alert-family-design.md` L121 | **接收 hand-off** — "Iter 4 get_derivatives_data OI change rate — separate mini-PR" |
| `docs/superpowers/specs/2026-05-13-iter-w2r2-next-h-set-next-wake-at-design.md` L577 | **接收 hand-off** — "R2-Next-G OI 变化率 (Iter 4) \| 无重叠" |
| `docs/superpowers/specs/2026-05-03-iter-w2r2-8c-tool-output-optimization-design.md` §4.2.10 | **沿用** — derivatives 单 section + per-field fallback 风格 |
| `docs/superpowers/principles/tool-design-principles.md:119` | **直接 follow-up** — "OI 变化率手算反复出现"实证案例本议题闭环 |
| memory `project_w2_prep_progress` | **更新触发** — landed 后从"未启动" → "✅" |

### 8.2 Open questions（plan / impl 阶段解决）

1. 26 records 时 oldest record 是否参与 cache 存值？（impl detail — 不参与 anchor 渲染，但作为 list 元素仍入 cache 值）
2. `_format_oi_usd` 精度 `.2f` 在小于 $10M 量级 fallback 到 raw 整数显示（已 §2.5 helper 决定，不再优化）
3. `_OKX_OI_PERIOD` 模块作用域落 `base.py` 还是 `okx.py` 顶部（plan 决；倾向 `okx.py` 以保持 okx-specific 翻译逻辑就近）
4. `fetch_open_interest` (single-point) 全 caller grep 后实际有几处？是否真可一并删除（§2.4 处置规则的 plan 阶段事实验证）

### 8.3 OOS / 不在 scope

- Funding rate history delta（sim 实证 0 hits 不支持）
- Long/short ratio history delta（仅 1 弱 hit 不支持；§3.5.4 评 F-D2 设计正确）
- `_DERIVATIVES_TTL` 调整（180s 沿用，新议题不引入）
- Layer 1 persona nudge（per 原则 8 last-resort，W3 数据触发后才考虑）

---

## 9. 维护

- 本 spec landing 后归档 path: `docs/superpowers/specs/2026-05-13-iter-w2r2-next-g-oi-delta-design.md`（不动）
- 配套 plan 文档: `docs/superpowers/plans/2026-05-13-iter-w2r2-next-g.md`（spec landing 后由 writing-plans skill 起草）
- 与 `.working/sim8-w2-tool-optimization-roadmap.md` 配对: 本 spec = 该 roadmap §6.1 Iter 4 (R2-Next-G) 议题最终设计层
- W3 sim 完成后归档 `.working/sim-w3-r2-next-g-validation.md`（实证 §6.2 gate 判定）
- 议题闭环 / 降级动作走 memory 更新（不在本文档加 changelog 段）
