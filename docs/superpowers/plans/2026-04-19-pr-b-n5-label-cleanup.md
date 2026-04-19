# PR B — N5 工具输出标签清理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `services/technical.py::format_for_llm` 和 `tools_perception.py::get_market_data` Market Context 段的定性/强度标签（`bearish/bullish/neutral/oversold/overbought/upper half/lower half/low|moderate|high volatility/above normal`）全部替换为纯事实输出（数值/百分比/位置定位），让 agent 在观察期基于事实独立判断而不是跟随工具内置标签。

**Architecture:** 本 PR 不涉及新模块或数据流改造，只改两个函数的渲染分支 + 对应测试。改动分三组：
1. `format_for_llm` 里的 RSI / MA(20,50) / MACD / BB 四个渲染段——删标签，MA 改带符号百分比 `(price vs MA: +2.3%)`，BB 改三分支事实渲染（带内 `position: N% of band width`；脱带上方 `X% above upper band`；脱带下方 `Y% below lower band`；`bb_upper == bb_lower` 退化时 `position: N/A`）。
2. `tools_perception.py::get_market_data` Market Context 段的 ATR/Volume 渲染——把 5m ATR 的 low/moderate/high 分支合并到统一的 `% of price, {timeframe} candles`；去掉 Volume 的 low/normal/above normal 标签，保留 `x avg` 倍数。
3. 测试：反转 `test_technical.py:103` 的正断言、重命名 `:95`、更新 `tests/test_tool_enhancement.py` 相关断言；新增 4 个测试（3 BB 边界 + 1 5m ATR 对称）；清理 5 处 stale mock/fixture。

**Tech Stack:** Python 3.12, pandas, pytest + pytest-asyncio（项目既有依赖，零新增）。

**Spec:** `docs/superpowers/specs/2026-04-19-hardening-batch-design.md` §2

---

## Design Deviations from Spec

无（本 plan 完全遵循 spec §2 + §5.1 + §6.1，包括所有边界决策与措辞）。

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/services/technical.py` | Modify（`:57-122`）| `format_for_llm`：RSI/MA/MACD 删标签；BB 三分支事实渲染（含 `bb_upper == bb_lower` edge case）|
| `src/agent/tools_perception.py` | Modify（`:59-95`）| Market Context 段：5m ATR 合并分支；Volume 删标签 |
| `tests/test_technical.py` | Modify + 新增 | 反转 `:103` 断言、重命名 `:95`、更新 RSI/MA/MACD/BB 断言；新增 3 个 BB 边界测试 |
| `tests/test_tool_enhancement.py` | Modify + 新增 | 清理 3 处 stale mock fixture（`:313,:348,:375`）；新增 `test_get_market_data_5m_atr_no_qualitative_label` 对称测试 |
| `tests/test_display_cycle.py` | Modify | 清理 2 处 stale fixture（`:14-18`、`:376`）——输入 content 字符串含旧标签，改为新格式（不破 CI，但让 fixture 和现实输出保持一致）|

**其他文件**：`src/cli/display.py:38-52` 的 `_summarize_get_market_data` 只 regex 提数字，不解析标签，**不需要改**（确认过）。

---

## Task 0: Pre-work — 基线与全量 baseline

**Files:** 无代码改动。

- [ ] **Step 1: 确认当前在 feature 分支 + main 同步**

Run:

```bash
git status
git rev-parse --abbrev-ref HEAD
```

Expected: `On branch feat/pr-b-n5-label-cleanup` + 工作树干净。

- [ ] **Step 2: 跑全量 baseline，记录测试数与通过状态**

Run: `uv run pytest -q 2>&1 | tail -5`

Expected: `647 passed` 附近（spec §5.3 基线），全绿。记下数字作为 commit message 时的对照基准。

---

## Task 1: RSI 标签清理

**Files:**
- Modify: `src/services/technical.py:68-83`（RSI 渲染段）
- Test: `tests/test_technical.py:95-107`

- [ ] **Step 1: 反转并重命名 `test_format_for_llm_5m_annotations` 为 `test_format_for_llm_is_fact_only`**

把 `tests/test_technical.py:95-107` 整块替换为：

```python
def test_format_for_llm_is_fact_only(sample_ohlcv):
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = service.compute_indicators(sample_ohlcv)
    text = service.format_for_llm(indicators, current_price=65000.0, timeframe="5m")
    assert "RSI" in text
    assert "MA(20)" in text
    # Fact-only: no qualitative / directional labels
    for label in ("neutral", "bullish", "bearish", "overbought", "oversold",
                  "upper half", "lower half", "price above", "price below"):
        assert label not in text.lower()
    # Positive anchors: guard against "deleted label but forgot to add the
    # fact-only replacement" regression — negative-only assertions would pass
    # silently if MA/BB rendered without the new phrasing.
    assert "price vs MA:" in text
    assert any(
        phrase in text
        for phrase in ("of band width", "above upper band", "below lower band")
    )
    # format_for_llm should NOT include ATR or Volume (those are in Market Context)
    assert "ATR" not in text
    assert "Volume" not in text
```

- [ ] **Step 2: 跑该测试，确认它先失败（TDD red）**

Run: `uv run pytest tests/test_technical.py::test_format_for_llm_is_fact_only -v`
Expected: FAIL（因为 RSI/MA/MACD/BB 目前都输出标签，`"bullish" in text.lower()` 当前为 True）

- [ ] **Step 3: 实施 RSI 渲染清理**

把 `src/services/technical.py:68-83` 的 RSI 段替换为：

```python
        # RSI
        rsi = indicators.get("rsi_14")
        if rsi is not None:
            lines.append(f"RSI(14): {rsi:.2f}")
        else:
            lines.append("RSI(14): N/A")
```

- [ ] **Step 4: 其他三段（MA/MACD/BB）仍含标签，整测试还会失败——留给 Task 2-4**

Run: `uv run pytest tests/test_technical.py::test_format_for_llm_is_fact_only -v`
Expected: 仍然 FAIL（直到 Task 4 完成 BB）。**不提交**，继续 Task 2。

---

## Task 2: MA 渲染改为 `price vs MA: +X%`

**Files:**
- Modify: `src/services/technical.py:85-92`

- [ ] **Step 1: 实施 MA 渲染清理**

把 `src/services/technical.py:85-92` 的 MA 段替换为：

```python
        # MA
        for period in (20, 50):
            ma = indicators.get(f"ma_{period}")
            if ma is not None:
                dist_pct = (current_price - ma) / ma * 100
                lines.append(f"MA({period}): {ma:.2f} (price vs MA: {dist_pct:+.1f}%)")
            else:
                lines.append(f"MA({period}): N/A")
```

**Why `:+.1f`**：带符号 + 一位小数，与 HTF MA 未来将同步改为的格式一致（HTF 对齐属于 PR C §3.3 范畴，不在本 PR）。

- [ ] **Step 2: 快速 sanity check（MACD/BB 还会让 Task 1 测试 fail）**

Run: `uv run pytest tests/test_technical.py::test_format_for_llm_is_fact_only -v`
Expected: 仍然 FAIL（因 MACD + BB 还在输出标签）。继续 Task 3。

---

## Task 3: MACD 标签清理

**Files:**
- Modify: `src/services/technical.py:94-107`

- [ ] **Step 1: 实施 MACD 渲染清理**

把 `src/services/technical.py:94-107` 的 MACD 段替换为：

```python
        # MACD
        macd = indicators.get("macd")
        signal = indicators.get("macd_signal")
        hist = indicators.get("macd_histogram")
        if all(v is not None for v in (macd, signal, hist)):
            lines.append(
                f"MACD: {macd:.2f} | Signal: {signal:.2f} | Histogram: {hist:.2f}"
            )
        else:
            lines.append(f"MACD: {_fmt(macd)} | Signal: {_fmt(signal)} | Histogram: {_fmt(hist)}")
```

- [ ] **Step 2: 再跑 Task 1 的测试**

Run: `uv run pytest tests/test_technical.py::test_format_for_llm_is_fact_only -v`
Expected: 仍然 FAIL（BB 还在输出 `"upper half"/"lower half"`）。继续 Task 4。

---

## Task 4: BB 三分支事实渲染 + edge case

**Files:**
- Modify: `src/services/technical.py:109-120`
- Test: `tests/test_technical.py`（新增 3 个）

### 4a — 先写 BB 三个新测试（TDD red）

- [ ] **Step 1: 在 `tests/test_technical.py` 末尾追加 3 个 BB 边界测试**

```python
def test_format_for_llm_bb_position_at_lower_band():
    """When price == bb_lower, position should be 0% of band width."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = {
        "rsi_14": 50.0, "ma_20": 100.0, "ma_50": 100.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 110.0, "bb_middle": 100.0, "bb_lower": 90.0,
        "atr_14": None, "volume_ratio": None,
    }
    text = service.format_for_llm(indicators, current_price=90.0, timeframe="5m")
    # BB line must mention 0% position
    bb_line = next(line for line in text.split("\n") if line.startswith("BB:"))
    assert "0%" in bb_line
    assert "of band width" in bb_line
    assert "above" not in bb_line and "below" not in bb_line


def test_format_for_llm_bb_position_at_upper_band():
    """When price == bb_upper, position should be 100% of band width."""
    from src.services.technical import TechnicalAnalysisService
    service = TechnicalAnalysisService()
    indicators = {
        "rsi_14": 50.0, "ma_20": 100.0, "ma_50": 100.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 110.0, "bb_middle": 100.0, "bb_lower": 90.0,
        "atr_14": None, "volume_ratio": None,
    }
    text = service.format_for_llm(indicators, current_price=110.0, timeframe="5m")
    bb_line = next(line for line in text.split("\n") if line.startswith("BB:"))
    assert "100%" in bb_line
    assert "of band width" in bb_line


def test_format_for_llm_bb_position_edge_case_equal_bands():
    """When bb_upper == bb_lower (extremely narrow band), position segment must be N/A.

    Acceptance criteria (spec §6.1):
      - position segment inside BB line parentheses contains 'N/A'
      - position segment must NOT contain '%' or numeric digits (prevents future
        regression writing 'N/A%' or '0%' as a compromise)
    """
    from src.services.technical import TechnicalAnalysisService
    import re
    service = TechnicalAnalysisService()
    indicators = {
        "rsi_14": 50.0, "ma_20": 100.0, "ma_50": 100.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 100.0, "bb_middle": 100.0, "bb_lower": 100.0,
        "atr_14": None, "volume_ratio": None,
    }
    text = service.format_for_llm(indicators, current_price=100.0, timeframe="5m")
    bb_line = next(line for line in text.split("\n") if line.startswith("BB:"))
    # Extract content inside the parentheses (position segment only)
    m = re.search(r"\(([^)]*)\)", bb_line)
    assert m, f"BB line missing parentheses: {bb_line}"
    pos_segment = m.group(1)
    assert "N/A" in pos_segment
    # Guard against future 'N/A%' or '0%' compromise
    assert "%" not in pos_segment
    assert not any(ch.isdigit() for ch in pos_segment)
```

- [ ] **Step 2: 跑 3 个新测试，确认全部 fail（TDD red）**

Run: `uv run pytest tests/test_technical.py::test_format_for_llm_bb_position_at_lower_band tests/test_technical.py::test_format_for_llm_bb_position_at_upper_band tests/test_technical.py::test_format_for_llm_bb_position_edge_case_equal_bands -v`
Expected: 三个都 FAIL（当前 BB 渲染是 `(price in upper half)/(price in lower half)`）

### 4b — 实施 BB 三分支事实渲染

- [ ] **Step 3: 实施 BB 段渲染改动**

把 `src/services/technical.py:109-120` 的 BB 段替换为：

```python
        # Bollinger Bands — fact-only: position as % of band width inside band;
        # 'X% above/below upper/lower band' when price breaks out. Anchor inside
        # the band is band width; anchor outside is the band edge (asymmetric on
        # purpose — band is the reference frame, see spec §2.3 #2).
        bb_u = indicators.get("bb_upper")
        bb_m = indicators.get("bb_middle")
        bb_l = indicators.get("bb_lower")
        if all(v is not None for v in (bb_u, bb_m, bb_l)):
            if bb_u == bb_l:
                pos = "position: N/A"
            elif current_price < bb_l:
                pct_below = (bb_l - current_price) / bb_l * 100
                pos = f"{pct_below:.1f}% below lower band"
            elif current_price > bb_u:
                pct_above = (current_price - bb_u) / bb_u * 100
                pos = f"{pct_above:.1f}% above upper band"
            else:
                pct = (current_price - bb_l) / (bb_u - bb_l) * 100
                pos = f"position: {pct:.0f}% of band width"
            lines.append(f"BB: {bb_u:.0f} / {bb_m:.0f} / {bb_l:.0f} ({pos})")
        else:
            lines.append(f"BB: {_fmt(bb_u)} / {_fmt(bb_m)} / {_fmt(bb_l)}")
```

- [ ] **Step 4: 跑三个新测试 + Task 1 的 fact_only 测试**

Run: `uv run pytest tests/test_technical.py -v -k "test_format_for_llm"`
Expected:
- `test_format_for_llm_is_fact_only` — PASS
- `test_format_for_llm_bb_position_at_lower_band` — PASS
- `test_format_for_llm_bb_position_at_upper_band` — PASS
- `test_format_for_llm_bb_position_edge_case_equal_bands` — PASS
- `test_format_for_llm_none_values` — PASS（不受影响）

---

## Task 5: 跑 technical.py 全部测试，确保无回归

- [ ] **Step 1: 跑整个 test_technical.py**

Run: `uv run pytest tests/test_technical.py -v`
Expected: 全绿（旧有 `test_compute_indicators_*` 5 个 + `test_format_for_llm_none_values` + `test_format_for_llm_is_fact_only` + 3 新 BB = 10 个测试）。

- [ ] **Step 2: 跑全量 pytest，确认其他测试未被本次改动波及**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: **650 passed**（baseline 647 + 3 新 BB 测试；5m ATR 对称测试尚未加，在 Task 6a）。若数字不对或有 red，先 `git diff` 排查；`test_tool_enhancement.py` 的 `(neutral)` mock 和 `test_display_cycle.py` 的 fixture 在本 commit 时仍含旧标签，但都是输入字符串（mock 覆盖 format_for_llm 返回值 / summarize_tool 只 regex 提数字），不应 break CI——若 break 说明漏了哪个断言，需排查。

- [ ] **Step 3: Commit 阶段性产出**

```bash
git add src/services/technical.py tests/test_technical.py
git commit -m "$(cat <<'EOF'
refactor(technical): fact-only output for RSI/MA/MACD/BB (N5)

- Drop qualitative labels (bearish/bullish/neutral/oversold/overbought/
  upper half/lower half) from format_for_llm.
- MA renders as "price vs MA: +X.X%" (signed pct vs the MA itself) to
  eliminate ambiguity with "price +X%" phrasing.
- BB renders in three fact-only branches: position inside band (% of
  band width), breakout above (% above upper band), breakout below
  (% below lower band); degenerate bb_upper == bb_lower returns N/A.
- Test test_format_for_llm_5m_annotations inverted and renamed to
  test_format_for_llm_is_fact_only; 3 new BB edge-case tests added.

Spec: docs/superpowers/specs/2026-04-19-hardening-batch-design.md §2.2 #1-4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Market Context — 5m ATR 合并分支 + Volume 标签清理

**Files:**
- Modify: `src/agent/tools_perception.py:59-95`
- Test: `tests/test_tool_enhancement.py`（新增 1 + 清理 3）

### 6a — 新增对称 5m ATR 测试（TDD red）

- [ ] **Step 1: 在 `tests/test_tool_enhancement.py` 既有 `test_get_market_data_1h_atr_no_qualitative_label`（`:356`）之后追加对称 5m 版**

找到 `tests/test_tool_enhancement.py:384`（1h 测试最后一行 `assert "high volatility" not in result`），在空行之后追加：

```python


async def test_get_market_data_5m_atr_no_qualitative_label():
    """5m timeframe must NOT emit ATR qualitative labels — symmetric with 1h.

    Regression guard: previously the 5m branch rendered
    "low volatility / moderate / high volatility" based on pct thresholds.
    N5 cleanup removes this; this test prevents label regrowth.
    """
    from src.agent.tools_perception import get_market_data

    deps = _make_deps()
    deps.timeframe = "5m"
    n = 100
    deps.market_data.get_ohlcv_dataframe.return_value = pd.DataFrame({
        "timestamp": [1000 + i * 300000 for i in range(n)],
        "open": np.full(n, 74800.0), "high": np.full(n, 74900.0),
        "low": np.full(n, 74700.0), "close": np.full(n, 74880.0),
        "volume": np.full(n, 125.0),
    })
    deps.technical.compute_indicators.return_value = {
        "rsi_14": 50.0, "ma_20": 74750.0, "ma_50": 74500.0,
        "macd": 0.0, "macd_signal": 0.0, "macd_histogram": 0.0,
        "bb_upper": 75000.0, "bb_middle": 74750.0, "bb_lower": 74500.0,
        "atr_14": 85.2, "volume_ratio": 1.0,
    }
    deps.technical.format_for_llm.return_value = "RSI(14): 50.00"

    result = await get_market_data(deps, timeframe="5m")
    assert "ATR(14): 85.20" in result
    assert "5m candles" in result
    # NO qualitative labels
    assert "low volatility" not in result
    assert "moderate" not in result
    assert "high volatility" not in result
    # Volume label also gone (Task 6 removes it)
    assert "above normal" not in result
    # "normal" alone is too common to grep safely; use "— normal" marker
    assert "— normal" not in result
```

- [ ] **Step 2: 跑新测试，确认 fail**

Run: `uv run pytest tests/test_tool_enhancement.py::test_get_market_data_5m_atr_no_qualitative_label -v`
Expected: FAIL（当前 5m 分支会输出 `"low volatility"` / `"moderate"` / `"high volatility"`；Volume 会输出 `"— normal"`）

### 6b — 实施 Market Context 渲染清理

- [ ] **Step 3: 替换 `src/agent/tools_perception.py:58-87` Market Context 的 ATR+Volume 段**

定位 `src/agent/tools_perception.py` 内的 `# === Market Context ===` 块（`:58` 起），把 ATR 段（含 header + `ctx_lines = []`，即 `:58-74`）替换为：

```python
    # === Market Context ===
    ctx_lines = []
    atr = indicators.get("atr_14")
    if atr is not None and ticker.last > 0:
        pct = atr / ticker.last * 100
        ctx_lines.append(
            f"ATR(14): {atr:.2f} ({pct:.2f}% of price, {timeframe} candles)"
        )
    else:
        ctx_lines.append("ATR(14): N/A")
```

然后把 Volume 段（`:76-87`）替换为：

```python
    vr = indicators.get("volume_ratio")
    if vr is not None:
        raw_vol = df["volume"].iloc[-2] if len(df) >= 2 else df["volume"].iloc[-1]
        ctx_lines.append(f"Volume: {raw_vol:.1f} ({vr:.2f}x avg)")
    else:
        ctx_lines.append("Volume: N/A")
```

保持 Range 段（`:89-94`）不动。

- [ ] **Step 4: 跑新测试 + 既有 1h 测试**

Run: `uv run pytest tests/test_tool_enhancement.py::test_get_market_data_5m_atr_no_qualitative_label tests/test_tool_enhancement.py::test_get_market_data_1h_atr_no_qualitative_label -v`
Expected: 两个 PASS

### 6c — 清理 test_tool_enhancement.py 中 3 处 stale mock fixture

- [ ] **Step 5: 更新 `tests/test_tool_enhancement.py:313`、`:348`、`:375` 的 `format_for_llm.return_value`**

三处 mock 返回值当前都含 `(neutral)` 标签；在本 PR 后 `format_for_llm` 不会输出 `(neutral)`，让 mock 返回值偏离实际契约是维护债。改动（精确定位后）：

- `:313`：`"RSI(14): 52.88 (neutral)\nMA(20): 74750.00"` → `"RSI(14): 52.88\nMA(20): 74750.00 (price vs MA: +0.2%)"`
- `:348`：`"RSI(14): 50.00 (neutral)"` → `"RSI(14): 50.00"`
- `:375`：`"RSI(14): 50.00 (neutral)"` → `"RSI(14): 50.00"`

（`:375` 是 1h ATR 测试的 mock，已验证改动不破坏该测试的断言。）

- [ ] **Step 6: 跑整份 test_tool_enhancement.py**

Run: `uv run pytest tests/test_tool_enhancement.py -v`
Expected: 全绿（新增 1 + 既有未动 + 3 处 mock 更新）。

---

## Task 7: display_cycle 测试 fixture 清理

**Files:**
- Modify: `tests/test_display_cycle.py:14-18`（`test_summarize_get_market_data`）
- Modify: `tests/test_display_cycle.py:376`（`test_format_cycle_output_basic`）

**为什么改**：这两处是测试输入 fixture（模拟 agent 看到的 `get_market_data` 输出），不是断言。`src/cli/display.py:38-52` 只 regex 提数字，所以不破 CI；但 fixture 含 `(neutral)`/`(price above — bullish)`/`(price in upper half)` 等旧标签，与新契约不一致，该跟随更新。

- [ ] **Step 1: 更新 `tests/test_display_cycle.py:9-26` 的 `content` 字符串**

定位 `test_summarize_get_market_data` 的 `content`（约 `:9-26`），把 Technical Indicators 段与 Market Context 段改为新格式。完整替换块：

```python
    content = (
        "=== Ticker (BTC/USDT:USDT) ===\n"
        "Price: 84200.50 | Bid: 84190.00 | Ask: 84210.00\n"
        "24h High: 85000.00 | Low: 83000.00 | Volume: 1234.56\n\n"
        "=== Technical Indicators (15m) ===\n"
        "RSI(14): 62.30\n"
        "MA(20): 84000.00 (price vs MA: +0.2%)\n"
        "MA(50): 83500.00 (price vs MA: +0.8%)\n"
        "MACD: 50.00 | Signal: 45.00 | Histogram: 5.00\n"
        "BB: 85000 / 84000 / 83000 (position: 60% of band width)\n\n"
        "=== Market Context ===\n"
        "ATR(14): 101.04 (0.12% of price, 15m candles)\n"
        "Volume: 500.0 (1.10x avg)\n"
        "50-candle Range: 83000 — 85000\n\n"
        "=== Recent Candles (15m, last 50) ===\n"
        "Time           Open       High        Low      Close        Vol\n"
        "12:00         84000.00  84300.00  83900.00  84200.50      100.0"
    )
```

- [ ] **Step 2: 更新 `tests/test_display_cycle.py:376` 的 inline `content` 字符串**

把 `RSI(14): 62.30 (neutral)` 改为 `RSI(14): 62.30`（其余部分保持）。改动后该行变成：

```python
        {"tool_name": "get_market_data", "content": "=== Ticker (BTC/USDT:USDT) ===\nPrice: 84200.00 | Bid: 84190.00 | Ask: 84210.00\n\n=== Technical Indicators (15m) ===\nCurrent Price: 84200.00\n\nRSI(14): 62.30\n\n=== Market Context ===\nATR(14): 101.04 (0.12% of price, 15m candles)", "outcome": "success"},
```

- [ ] **Step 3: 跑 test_display_cycle.py**

Run: `uv run pytest tests/test_display_cycle.py -v`
Expected: 全绿。

---

## Task 8: 全仓回归 + grep 验收

- [ ] **Step 1: 跑全量测试**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: `651 passed`（647 + 4 新增：3 BB 边界 + 1 5m ATR 对称）。若数字不对，核对 `git diff` 找出被遗漏的断言。

- [ ] **Step 2: grep 验收 acceptance criteria（spec §6.1）**

Run:

```bash
uv run python -c "
from src.services.technical import TechnicalAnalysisService
svc = TechnicalAnalysisService()
indicators = {
    'rsi_14': 35.2, 'ma_20': 42800.0, 'ma_50': 42500.0,
    'macd': 15.2, 'macd_signal': 12.3, 'macd_histogram': 2.9,
    'bb_upper': 43200.0, 'bb_middle': 42800.0, 'bb_lower': 42400.0,
    'atr_14': 150.0, 'volume_ratio': 1.2,
}
text = svc.format_for_llm(indicators, current_price=42900.0, timeframe='5m')
print(text)
for banned in ('bearish', 'bullish', 'neutral', 'oversold', 'overbought',
               'upper half', 'lower half',
               'price above — bullish', 'price below — bearish'):
    assert banned not in text.lower(), f'FAIL: {banned!r} still in output'
print('OK — no banned labels')
"
```

Expected: 打印的 `text` 含新格式（`RSI(14): 35.20` / `MA(20): 42800.00 (price vs MA: +0.2%)` / `MACD: 15.20 | Signal: 12.30 | Histogram: 2.90` / `BB: 43200 / 42800 / 42400 (position: 62% of band width)`）+ 最终行 `OK — no banned labels`。

- [ ] **Step 3: 确认 Market Context 段无 ATR/Volume 标签**

Run:

```bash
uv run grep -rn 'low volatility\|high volatility\|above normal\|— moderate\|— normal' src/ tests/
```

Expected: 只在 `tests/test_tool_enhancement.py` 的 `"low volatility" not in result` 等**反向断言**处出现（防御性检查），不在 `src/` 任何文件里出现。

- [ ] **Step 4: 传染性 grep — 捕捉漏网的硬编码旧标签**

对标 spec §6.1 PR C 的 Stablecoin 传染性检查思路；本 step 验证 PR B 清理完整，没有除 plan 识别的 5 处以外还存在硬编码旧标签。

Run:

```bash
uv run python -c "
import subprocess, sys
pattern = r'\(neutral\)|\(bullish\)|\(bearish\)|upper half|lower half|— normal|low volatility|high volatility'
result = subprocess.run(['grep', '-rnE', pattern, 'src/', 'tests/'],
                        capture_output=True, text=True)
lines = [l for l in result.stdout.splitlines() if l.strip()]
stray = []
for line in lines:
    # Acceptable: reverse assertions ('not in result'/'not in text'/'assert ... not in')
    #             and this plan's banned-list literals inside test_format_for_llm_is_fact_only
    if 'not in ' in line:
        continue
    if 'test_format_for_llm_is_fact_only' in line:
        continue  # the banned-list tuple inside the fact-only test
    if 'for label in' in line or 'for banned in' in line:
        continue  # banned-list declarations
    stray.append(line)
if stray:
    print('STRAY HARDCODED OLD LABELS:')
    for s in stray:
        print('  ' + s)
    sys.exit(1)
print('OK — no stray old labels')
"
```

Expected: `OK — no stray old labels`。若有命中，核对是否为 plan 未覆盖的隐藏 fixture / 硬编码；视情况补 Task 或调整 acceptable 白名单。

---

## Task 9: Commit 剩余改动

- [ ] **Step 1: 确认 `git status` 的改动范围**

Run: `git status`
Expected: 改动限于：
- `src/agent/tools_perception.py`
- `tests/test_tool_enhancement.py`
- `tests/test_display_cycle.py`

（`src/services/technical.py` + `tests/test_technical.py` 已在 Task 5 commit。）

- [ ] **Step 2: Commit**

```bash
git add src/agent/tools_perception.py tests/test_tool_enhancement.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
refactor(perception): fact-only Market Context ATR/Volume (N5)

- Unify 5m ATR output with other timeframes: drop low/moderate/high
  volatility labels, render "X.XX% of price, {timeframe} candles" for
  all timeframes.
- Drop Volume low/normal/above-normal label; keep "x avg" multiplier.
- Add symmetric test_get_market_data_5m_atr_no_qualitative_label (pairs
  with existing 1h version); clean 3 stale format_for_llm mocks in
  test_tool_enhancement.py and 2 stale fixtures in test_display_cycle.py
  to match the new fact-only contract.

Spec: docs/superpowers/specs/2026-04-19-hardening-batch-design.md §2.2 #5-6

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: 最终 baseline 再核对一次**

Run: `uv run pytest -q 2>&1 | tail -5`
Expected: `651 passed`。

---

## Task 10: push + 创建 PR（**需用户 checkpoint**）

Push 分支与 `gh pr create` 都是对外可见操作（分支出现在 origin、PR 触发 CI / 通知 reviewer），按项目 `feedback_review_before_commit` + system prompt 的"shared state 操作需 confirm"原则，**执行前必须先报告给用户并等待明确批准**，不得在无确认情况下连贯跑完。

### 10a — Push 分支（等用户批准再执行）

- [ ] **Step 1: 预检：汇报即将推送的内容**

Run:

```bash
git log --oneline origin/main..HEAD
git diff --stat origin/main..HEAD
```

把输出整理为摘要（几个 commit / 哪些文件 / 总行数），报告给用户并**明确提问**："确认 push `feat/pr-b-n5-label-cleanup` 到 origin 吗？"

- [ ] **Step 2: 等用户明确批准后再执行 push**

**禁止自动跑**。仅当用户回答"push / 可以 / 好 / 确认"等明确同意时执行：

```bash
git push -u origin feat/pr-b-n5-label-cleanup
```

Expected: 新分支创建在 origin。

### 10b — 创建 PR（等用户批准再执行）

- [ ] **Step 3: 预检：把拟 PR 标题 + body 草稿贴给用户审阅**

把下列内容作为"将用 `gh pr create` 提交的草稿"**先贴给用户**，等待用户回答"创建 PR / 可以 / 确认"后再执行 Step 4：

```
标题: refactor(perception): N5 fact-only tool output cleanup

Body:

## Summary

- 删除 `format_for_llm` 的 RSI/MA/MACD/BB 定性标签（bearish/bullish/neutral/oversold/overbought/upper half/lower half）。
- MA 改带符号百分比 `(price vs MA: +X.X%)`，消除与 "price +X%" 的歧义。
- BB 改三分支事实渲染：带内 `position: N% of band width`；脱带 `X% above/below upper/lower band`；`bb_upper == bb_lower` 时 `position: N/A`。
- Market Context 段：5m ATR 合并到统一的 `% of price, {timeframe} candles`；Volume 删 low/normal/above-normal 标签。
- 4 个新增测试（3 BB 边界 + 1 5m ATR 对称）；5 处 stale mock/fixture 清理。

## Test plan

- [ ] `uv run pytest -q` 全绿，测试数从 647 → 651（+4 新增）
- [ ] `tests/test_technical.py::test_format_for_llm_is_fact_only` 断言反转后仍通过
- [ ] BB 边界三测试覆盖 lower/upper/equal-bands
- [ ] 5m ATR 对称测试防标签回潮
- [ ] Grep `src/` 无 `bearish|bullish|upper half|low volatility` 等 banned 标签

Spec: `docs/superpowers/specs/2026-04-19-hardening-batch-design.md` §2
```

- [ ] **Step 4: 用户批准后再执行 `gh pr create`**

**禁止自动跑**。仅当用户明确同意后：

```bash
gh pr create --title "refactor(perception): N5 fact-only tool output cleanup" --body "$(cat <<'EOF'
## Summary

- 删除 `format_for_llm` 的 RSI/MA/MACD/BB 定性标签（bearish/bullish/neutral/oversold/overbought/upper half/lower half）。
- MA 改带符号百分比 `(price vs MA: +X.X%)`，消除与 "price +X%" 的歧义。
- BB 改三分支事实渲染：带内 `position: N% of band width`；脱带 `X% above/below upper/lower band`；`bb_upper == bb_lower` 时 `position: N/A`。
- Market Context 段：5m ATR 合并到统一的 `% of price, {timeframe} candles`；Volume 删 low/normal/above-normal 标签。
- 4 个新增测试（3 BB 边界 + 1 5m ATR 对称）；5 处 stale mock/fixture 清理。

## Test plan

- [ ] `uv run pytest -q` 全绿，测试数从 647 → 651（+4 新增）
- [ ] `tests/test_technical.py::test_format_for_llm_is_fact_only` 断言反转后仍通过
- [ ] BB 边界三测试覆盖 lower/upper/equal-bands
- [ ] 5m ATR 对称测试防标签回潮
- [ ] Grep `src/` 无 `bearish|bullish|upper half|low volatility` 等 banned 标签

Spec: `docs/superpowers/specs/2026-04-19-hardening-batch-design.md` §2

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: 返回 PR URL；贴给用户。

---

## 完工验收（spec §6.1 回检）

- [ ] `services/technical.py::format_for_llm` 输出不含：`bearish` / `bullish` / `neutral` / `oversold` / `overbought` / `upper half` / `lower half` / `price above — bullish` / `price below — bearish`
- [ ] `tools_perception.py::get_market_data` Market Context 段不含：`low volatility` / `— moderate` / `high volatility` / `— normal` / `above normal`
- [ ] BB 按三分支渲染（带内 / 脱带上 / 脱带下）
- [ ] `bb_upper == bb_lower` 时 BB 括号内段含 `N/A` 且无 `%`、无数字
- [ ] 4 个新增测试通过（3 BB + 1 5m ATR）
- [ ] 测试总数 651，全绿
