# get_market_data 聚焦 + 时点语义打磨 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `get_market_data` 输出讲清 closed-vs-live 时点语义（in-progress candle 独立 section / MA·BB·ATR 显式标 `Last` / Technical Indicators 表头时点锚）并删除低价值/重复内容（Market Context 整段 / Period summary 整段），回归单 TF 行情快照本职。

**Architecture:** 纯输出层 + LLM 描述改动，**0 行算法**（`compute_indicators` / `_closed_bars` / 指标公式 / `_atr_series` 全不动）。改 4 源文件：`technical.py`（`format_for_llm` 渲染）/ `tools_perception.py`（GMD 输出组装）/ `tools_descriptions.py`（LLM path B 描述）/ `trader.py`（wrapper docstring）。设计依据见 spec `docs/superpowers/specs/2026-06-06-gmd-focus-time-semantics-design.md`。

**Tech Stack:** Python 3 / pandas / pandas_ta / pydantic-ai（@tool description override path B）/ pytest（asyncio）。

---

## 文件结构（改动定位）

| 文件 | 责任 | 本 iter 改什么 |
|---|---|---|
| `src/services/technical.py` | `format_for_llm`：indicators dict → LLM 文本（GMD 独占，唯一调用方 `tools_perception.py:99`） | MA 行 + BB 行加 `Last <价>` 操作数（议题2）；末尾新增 ATR 行（议题5 渲染半） |
| `src/agent/tools_perception.py` | `get_market_data` impl：组装 Ticker / Technical Indicators / Recent Candles / (删) Market Context / (删) Period summary / (新) In-progress section | Technical Indicators 表头时点锚（议题3）；删 Market Context 段（议题4 + 议题5 删半）；Recent Candles→Recent Closed Candles 改名 + 删 in-progress 后缀（议题1 配套）；新增 In-progress Candle section（议题1）；删 Period summary 段（议题6）；impl docstring 更新 |
| `src/agent/tools_descriptions.py` | `GET_MARKET_DATA_DESCRIPTION`：LLM 实见描述（path B，`@tool(description=)` 绕 griffe，Example 块 survives） | 全面改写：指标清单补 MA(20)/MA(50)+SMA、披露 closed 值 + Last 操作数、Recent Closed Candles、删 Market Context/Period summary 引用、新增 in-progress section、Example output 同步 §4 样张 |
| `src/agent/trader.py` | `get_market_data` wrapper docstring（`:131`） | summary 行同步：去 period summary / in-progress hint，改为 indicators(RSI/MA/MACD/BB/ATR) + closed OHLCV + in-progress section |

**helper（只读不改）**：`src/utils/ohlcv_utils.py` 的 `_to_pd_timestamp_utc` / `_fmt_candle_time` / `TF_OFFSETS` / `_closed_bars` / `_live_price` / `_atr_series` 全部沿用。

**任务排序与「每 commit green」原则**：改动都在同一条 GMD 输出路径上，集成测试（`test_iter_tool_opt_gmd_polish` / `test_display_cycle` / `test_tool_enhancement` / `test_iter_w2r2_next_d_goldens` / `test_trader_agent`）横跨多个改动点。排序按依赖：format_for_llm（Task 1-2）→ tools_perception 输出（Task 2-5）→ 描述（Task 6）→ 全量收口（Task 7）。**每个 task 只更新它那一步打断的断言**，未改动点的旧断言对未改代码仍 pass、已改点的断言已在前序 task 同步 → 每次 commit 全绿。各文件被触碰的 task 已在下方逐条标注。

---

## Task 1: `format_for_llm` — MA / BB 行加 `Last <价>` 消歧（议题2）

**Files:**
- Modify: `src/services/technical.py:75-118`
- Test: `tests/test_technical.py:86-107`、`tests/test_ohlcv_utils.py:63-81`

- [ ] **Step 1: 更新单元测试断言（先红）**

`tests/test_technical.py` 的 `test_format_for_llm_is_fact_only`（`:100`）：

```python
# 旧（删除）：
    assert "price vs MA:" in text
# 新（替换为）：
    assert "→ -" in text or "→ +" in text  # MA dist 现以 `(Last <价> → ±X% vs MA)` 渲染
    assert "vs MA)" in text
```

`tests/test_ohlcv_utils.py` 的 `test_format_for_llm_bb_label_uses_full_words_and_explicit_periods`（`:79`），fixture `current_price=81870.50` 落 inside-band（81494 < 81870.50 < 81960）：

```python
# 旧（删除）：
    assert "position:" in out
# 新（替换为）：
    assert "Last 81870.50 →" in out      # inside-band 现渲 `(Last <价> → P% of band, ...)`
    assert "% of band" in out
# :78 的 "0%=Lower" / "100%=Upper"、:75-77 的 Upper/Middle/Lower、:81 的 "BB: 81960" not in out 全部保留不动
```

同步把 `:64` docstring 旧格式描述（`(position: P%, 0%=Lower / 100%=Upper)`）改成 `(Last <price> → P% of band, 0%=Lower / 100%=Upper)`。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_technical.py::test_format_for_llm_is_fact_only tests/test_ohlcv_utils.py::test_format_for_llm_bb_label_uses_full_words_and_explicit_periods -v`
Expected: FAIL（旧实现仍渲 `price vs MA:` / `position:`）

- [ ] **Step 3: 改 MA block（`technical.py:75-81`）**

```python
        # MA
        for period in (20, 50):
            ma = indicators.get(f"ma_{period}")
            if ma is not None:
                dist_pct = (current_price - ma) / ma * 100
                lines.append(f"MA({period}): {ma:.2f}  (Last {current_price:.2f} → {dist_pct:+.1f}% vs MA)")
            else:
                lines.append(f"MA({period}): N/A")
```

（`{ma:.2f}` 后 **两个空格** 再接 `(Last`，与 spec §4 样张一致。）

- [ ] **Step 4: 改 BB block 三态措辞（`technical.py:105-118`）**

`bb_u == bb_l` 的 equal-bands 分支 **保持原样**（`pos = "position: N/A"`，带冒号、不加 `Last`——见 spec §3.2，`test_technical.py:152` edge-case 断 paren 内含 `N/A`、无 `%`/数字）。其余三态：

```python
            if bb_u == bb_l:
                pos = "position: N/A"
            elif current_price < bb_l:
                pct_below = (bb_l - current_price) / bb_l * 100
                pos = f"Last {current_price:.2f} → {pct_below:.1f}% below Lower"
            elif current_price > bb_u:
                pct_above = (current_price - bb_u) / bb_u * 100
                pos = f"Last {current_price:.2f} → {pct_above:.1f}% above Upper"
            else:
                pct = (current_price - bb_l) / (bb_u - bb_l) * 100
                pos = f"Last {current_price:.2f} → {pct:.0f}% of band, 0%=Lower / 100%=Upper"
            lines.append(
                f"BB(20,2): Upper {bb_u:.2f} | Middle {bb_m:.2f} | Lower {bb_l:.2f}  ({pos})"
            )
```

（锚串 `0%=Lower / 100%=Upper` **带空格不动**；`Lower {bb_l:.2f}` 后两个空格再接 `(`，与 §4 样张一致。`{pos}` 外层括号保留。）

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_technical.py tests/test_ohlcv_utils.py -v`
Expected: PASS（含 `test_format_for_llm_bb_position_at_lower_band` / `_at_upper_band` / `_edge_case_equal_bands` 三个 inside/equal 测试——它们的 fixture `current_price==band` 走 inside/equal 分支，新格式保留 `0%`/`100%`/`N/A` 锚 → 仍 pass）

- [ ] **Step 6: Commit**

```bash
git add src/services/technical.py tests/test_technical.py tests/test_ohlcv_utils.py
git commit -m "feat(gmd): MA/BB 比较行显式标 Last <价> 消歧 (议题2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: ATR 归位进 Technical Indicators + 删 Market Context 整段（议题5 + 议题4）

**原子改动**：ATR 同时（a）加进 `format_for_llm` 末尾、（b）从 `tools_perception` Market Context 块删除——避免中间态出现 ATR 双渲或全缺。`Last bar vol` 行随 Market Context 整段删除（议题4，信号已被 OHLCV 表 RVol 列逐位覆盖）。

**Files:**
- Modify: `src/services/technical.py:124`（append ATR 行）
- Modify: `src/agent/tools_perception.py:122-143`（删 Market Context 块）
- Test: `tests/test_technical.py:105-106`、`tests/test_iter_w2r2_next_d_goldens.py:205-215`(删)、`tests/test_iter_tool_opt_gmd_polish.py:396-416`、`tests/test_tool_enhancement.py:489-510`、`tests/test_display_cycle.py:3001,3137`、`tests/test_multi_tf_drift_guards.py:240-243`

- [ ] **Step 1: 更新 / 删除被打断的断言（先红）**

(a) `tests/test_technical.py` 的 `test_format_for_llm_is_fact_only`（`:105-106`）—— ATR **反转**：

```python
# 旧（删除）：
    # format_for_llm should NOT include ATR or Volume (those are in Market Context)
    assert "ATR" not in text
    assert "Volume" not in text
# 新（替换为）：
    # ATR now rendered by format_for_llm (议题5: ATR 归位 Technical Indicators)
    assert "ATR(14):" in text
    assert "of Last" in text  # ATR % 以 live Last 为分母，显式标注
    assert "Volume" not in text
```

(b) `tests/test_iter_w2r2_next_d_goldens.py` —— **整删** `test_gmd_market_context_uses_last_bar_vol_and_smaperiod`（`:204-215`，测的就是被删的 Market Context `Last bar vol` 行）。

(c) `tests/test_iter_tool_opt_gmd_polish.py` 的 `TestDeletedNCandleHL.test_no_n_candle_high_low_row`（`:415-416`）：

```python
# 旧（删除）：
        # Market Context section should still exist (ATR / Last bar vol remain)
        assert "=== Market Context ===" in out
        assert "ATR(14):" in out
# 新（替换为）：
        # Market Context section deleted (议题4+5); ATR moved into Technical Indicators
        assert "=== Market Context ===" not in out
        assert "ATR(14):" in out  # still present, now in Technical Indicators section
```

（`:409` 的 `not re.search(r"\d+-candle High-Low:", out)` 与 `:412` 的 24h H/L 断言保留不动——仍真。）

(d) `tests/test_tool_enhancement.py` 的 segment-headers 测试（`:489-510`）—— mock 刷新 + 断言重指向：

```python
# :489-495 mock 刷新（compute_indicators 已含 atr_14=85.2；刷新 format_for_llm 含 ATR + 新 MA 标签）：
    deps.technical.format_for_llm.return_value = (
        "RSI(14): 52.88\n"
        "MA(20): 74750.00  (Last 74880.00 → +0.2% vs MA)\n"
        "ATR(14): 85.20 (0.11% of Last 74880.00)"
    )
    ...
    assert "=== Ticker" in result
    assert "=== Technical Indicators" in result
# 旧 :501（删除）：assert "=== Market Context ===" in result
    assert "=== Market Context ===" not in result          # 议题4+5 删段
    assert "=== Recent Candles" in result                  # Task 4 改名后再处理；此 task 保持旧名仍真
    assert "74880" in result
    assert "74870" in result  # bid
    assert "ATR" in result                                  # 现经刷新后的 format_for_llm mock 流出
# 旧 :509-510（删除 "Last bar vol:" / "SMA(20) avg" 两断言）→ 改断 OHLCV RVol 列：
    assert "RVol(×SMA20)" in result                         # vol/SMA20 信号现只在表内 RVol 列
```

（注：`:502 "=== Recent Candles"` 在本 task 仍真——改名在 Task 4，届时再改此行为 `=== Recent Closed Candles`。）

(e) `tests/test_display_cycle.py` —— dg_1c 白名单 + `_invoke_path_b` mock 双修：

```python
# :3001 mock 刷新（ATR 现经 format_for_llm 流出，否则白名单 "ATR" 救不回）：
        technical.format_for_llm.return_value = "RSI(14): 50.0\nMACD: -1.2 | Signal: -0.8 | Histogram: -0.4\nATR(14): 100.00 (0.13% of Last 75200.00)"
# :3137 白名单删 "Market Context"（本 task）：
    "get_market_data": ["Ticker", "Technical Indicators",
                        "Recent Candles", "RSI", "MACD", "ATR"],
```

（`"Recent Candles"` 在本 task 仍是 `=== Recent Candles ...` 的子串、仍真；Task 4 改名时再改白名单为 `"Recent Closed Candles"`。）

(f) `tests/test_multi_tf_drift_guards.py` 的 `test_gmd_htf_last_bar_vol_ratio_match`（`:240-243`）—— GMD 半边从被删的 `Last bar vol:` 行重指向 OHLCV 表 RVol 列**末行**（议题4 已证其与旧 `Last bar vol` 同分子 `iloc[-1]`/同分母 `iloc[-20:].mean()`、逐位相等）：

```python
# 旧 :240-243（删除）：
#     # GMD: "Last bar vol: X.X (Y.YY× SMA(20) avg)"  (2dp)
#     gmd_match = re.search(r"Last bar vol:[^(]*\((\d+\.\d+)× SMA\(20\) avg\)", out_gmd)
#     assert gmd_match, f"GMD missing Last bar vol line\n{out_gmd}"
#     gmd_ratio = float(gmd_match.group(1))
# 新（替换为）：GMD 的 last-bar vol/SMA20 现由 OHLCV 表最末数据行的 RVol 列承载。
# 必须先限定到 Recent [Closed] Candles 表内再取 [-1]——否则 Period summary 的
# (2.00×)（df_4h_recent_vol_spike: last_5 avg 200 / prior 100 = 2.00×）会污染 [-1]：
# Period 段直到 Task 5 才删，本 task / Task 3 / Task 4 期间它都在、且是输出最末段。
# "=== Recent" 前缀跨 Task 4 改名稳健；"\n\n=== " 切到下一段头，对 in-progress 段
# 新增 / Period 删除都免 re-point。
    gmd_table = out_gmd.split("=== Recent")[1].split("\n\n=== ")[0]
    gmd_rvols = re.findall(r"(\d+\.\d{2})×", gmd_table)
    assert gmd_rvols, f"GMD missing RVol column values\n{out_gmd}"
    gmd_ratio = float(gmd_rvols[-1])  # 表 oldest-first，末行 = 最近收盘 bar = spike bar = 4.80×
```

（其余 HTF 半边 `:245-272`、canonical 公式与 4.8 sanity check 全部不动。`df_4h_recent_vol_spike` 的 spike 在最末收盘 bar，RVol 末行 = 600/((19×100+600)/20) = 4.8 → `round(gmd_ratio,1)==htf_ratio` 仍成立。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_technical.py::test_format_for_llm_is_fact_only tests/test_iter_tool_opt_gmd_polish.py::TestDeletedNCandleHL tests/test_multi_tf_drift_guards.py::test_gmd_htf_last_bar_vol_ratio_match -v`
Expected: FAIL（旧实现仍有 Market Context / `Last bar vol`，format_for_llm 无 ATR）

- [ ] **Step 3: 给 `format_for_llm` 末尾新增 ATR 行（`technical.py:124` 之前，`return` 之上）**

```python
        # ATR (议题5: 归位进 Technical Indicators；% 以 live Last 为分母，显式标 of Last)
        atr = indicators.get("atr_14")
        if atr is not None and current_price > 0:
            lines.append(f"ATR(14): {atr:.2f} ({atr / current_price * 100:.2f}% of Last {current_price:.2f})")
        else:
            lines.append("ATR(14): N/A")

        return "\n".join(lines)
```

- [ ] **Step 4: 删 `tools_perception.py` Market Context 整块（`:122-143`）**

删除从 `# === Market Context ===`（`:122`）到 `sections.append("=== Market Context ===\n" + "\n".join(ctx_lines))`（`:143`）的全部代码（含 `ctx_lines` / `atr` / `Last bar vol` 逻辑）。`indicators` 仍在 scope（`:98`），ATR 已由 `format_for_llm` 渲染。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_technical.py tests/test_iter_tool_opt_gmd_polish.py tests/test_tool_enhancement.py tests/test_display_cycle.py tests/test_multi_tf_drift_guards.py tests/test_iter_w2r2_next_d_goldens.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/services/technical.py src/agent/tools_perception.py tests/
git commit -m "feat(gmd): ATR 归位 Technical Indicators + 删 Market Context 整段 (议题5+4)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Technical Indicators 表头加 closed-bar 时点锚（议题3）

**Files:**
- Modify: `src/agent/tools_perception.py:119-120`
- Test: `tests/test_iter_w2r2_next_d_goldens.py`（新增 1 测试，与 TestGMDGolden 同类）

- [ ] **Step 1: 写新测试（先红）**

在 `tests/test_iter_w2r2_next_d_goldens.py` 的 `TestGMDGolden` 内新增：

```python
    @pytest.mark.asyncio
    async def test_gmd_technical_indicators_header_has_closed_anchor(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题3: Technical Indicators 表头报最近收盘 bar 时点（5m → HH:MM）。"""
        import re
        from src.agent.tools_perception import get_market_data
        deps = _build_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert re.search(
            r"=== Technical Indicators \(5m, values as of last closed \d{2}:\d{2}\) ===",
            out,
        ), f"Technical Indicators header missing closed-bar anchor; out={out[:400]}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_iter_w2r2_next_d_goldens.py::TestGMDGolden::test_gmd_technical_indicators_header_has_closed_anchor -v`
Expected: FAIL（表头仍是 `=== Technical Indicators (5m) ===` 无 anchor）

- [ ] **Step 3: 注入时点锚（`tools_perception.py:119-120`）**

```python
    # === Technical Indicators ===
    if not df_closed.empty:
        ti_ts = _fmt_candle_time(_to_pd_timestamp_utc(df_closed["timestamp"].iloc[-1]), timeframe)
        ti_header = f"=== Technical Indicators ({timeframe}, values as of last closed {ti_ts}) ==="
    else:
        ti_header = f"=== Technical Indicators ({timeframe}) ==="
    sections.append(f"{ti_header}\n{indicators_text}")
```

（`_fmt_candle_time` / `_to_pd_timestamp_utc` 已在 `:82-85` import；`df_closed` 空时降级回无锚表头。）

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_iter_w2r2_next_d_goldens.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_iter_w2r2_next_d_goldens.py
git commit -m "feat(gmd): Technical Indicators 表头加 closed-bar 时点锚 (议题3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: In-progress Candle 独立 section + Recent Candles 改名 + 删 in-progress 后缀（议题1）

**Files:**
- Modify: `src/agent/tools_perception.py:93`（capture `now_dt`）、`:182-200`（删后缀逻辑 + 改名 + 新增 section）
- Test: `tests/test_iter_tool_opt_gmd_polish.py`（TestRVolColumn split anchor 重指向 + TestInProgressHint 整组重写 + `:391`）、`tests/test_ohlcv_ts_numpy_int64.py:70-106`（回归 guard 重写）、`tests/test_display_cycle.py:3137`（白名单改名）、`tests/test_tool_enhancement.py:502`（改名）；新增 in-progress section 测试

- [ ] **Step 1: 重写 / 更新被打断的断言（先红）**

(a) `tests/test_iter_tool_opt_gmd_polish.py` —— TestRVolColumn 的 split anchor（`:183` / `:200` / `:250`）`"=== Recent Candles"` → `"=== Recent Closed Candles"`（`"=== Recent Candles"` 不是 `"=== Recent Closed Candles"` 的子串，原 split 会 IndexError）：

```python
# 三处（:183 / :200 / :250）统一替换为——"\n\n=== " 切到下一段头，精确隔离出
# Recent Closed Candles 表本身（不含其后的 In-progress / Period 段），对 Task 5
# 删 Period 稳健、免再 re-point：
        section = out.split("=== Recent Closed Candles")[1].split("\n\n=== ")[0]
```

`:391` `assert "=== Recent Candles" in out` → `assert "=== Recent Closed Candles" in out`。

(b) `tests/test_iter_tool_opt_gmd_polish.py` —— `TestInProgressHint` 整组（`:257-391`）重写为断**独立 section**（旧逻辑断表头后缀 `in-progress … still open, closes at …` + 外推 last_closed+offset；新设计 in-progress section 直接渲 `df.iloc[-1]` 真实 timestamp）。替换整个 `class TestInProgressHint` 为：

```python
class TestInProgressSection:
    @pytest.mark.asyncio
    async def test_in_progress_section_header_and_columns_5m(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1: 独立 In-progress Candle section（header + so-far 列头 + caveat）。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== In-progress Candle (5m):" in out
        assert "High(so far)" in out and "Low(so far)" in out and "Vol(so far)" in out
        assert "(partial bar — excluded from all indicators; no RVol/markers until close)" in out

    @pytest.mark.asyncio
    async def test_in_progress_row_values_from_iloc_minus1(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1 护栏2: section 行 O/H/L/Last/Vol 全取 df.iloc[-1]（含被丢弃那根）。"""
        from src.agent.tools_perception import get_market_data
        df = df_5m_130bars
        ip = df.iloc[-1]
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df})
        out = await get_market_data(deps)
        ip_block = out.split("=== In-progress Candle")[1].split("\n\n=== ")[0]  # 限定到段内
        assert f"{ip['open']:.2f}" in ip_block
        assert f"{ip['high']:.2f}" in ip_block
        assert f"{ip['low']:.2f}" in ip_block
        assert f"{ip['close']:.2f}" in ip_block   # Last 列 = df.iloc[-1].close

    @pytest.mark.asyncio
    async def test_in_progress_no_rvol_no_markers(
        self, fake_ticker_81870, df_5m_anomaly,
    ):
        """议题1 护栏1: in-progress 行不含 RVol(×) / vol↑ / range↑。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_anomaly})
        out = await get_market_data(deps)
        # 限定到 in-progress 段内（"\n\n=== " 切到下一段头）——否则仍未删的 Period
        # summary 的 (2.00×)（df_5m_anomaly: bar127=600 落在 last_5）会污染 not-in 断言。
        ip_block = out.split("=== In-progress Candle")[1].split("\n\n=== ")[0]
        assert "×" not in ip_block
        assert "vol↑" not in ip_block and "range↑" not in ip_block

    @pytest.mark.asyncio
    async def test_in_progress_open_close_timestamps_5m(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1: header 的 open/close 时点 = df.iloc[-1] timestamp 与 +1 tf offset。"""
        import pandas as pd
        from src.agent.tools_perception import get_market_data
        df = df_5m_130bars
        ip_open_ms = int(df["timestamp"].iloc[-1])  # 独立换算，不经被测函数
        ip_open = pd.Timestamp(ip_open_ms, unit="ms", tz="UTC")
        ip_close = ip_open + pd.Timedelta(minutes=5)
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df})
        out = await get_market_data(deps)
        assert f"{ip_open.strftime('%H:%M')} open" in out
        assert f"closes {ip_close.strftime('%H:%M')}" in out

    @pytest.mark.asyncio
    async def test_in_progress_monthly_uses_dateoffset(
        self, fake_ticker_81870,
    ):
        """议题1: 1M tf 用 DateOffset（calendar-aware），elapsed 走 days 单位。"""
        import pandas as pd
        from tests.fixtures.multi_tf_ohlcv import _build
        from src.utils.ohlcv_utils import _to_pd_timestamp_utc, TF_OFFSETS
        from src.agent.tools_perception import get_market_data
        closes = [50000.0 + i * 100 for i in range(81)]
        df_1M = _build(
            start_ms=int(pd.Timestamp("2020-01-01", tz="UTC").value / 1e6),
            tf="1M", closes=closes,
        )
        ip_open = _to_pd_timestamp_utc(df_1M["timestamp"].iloc[-1])
        ip_close = ip_open + TF_OFFSETS["1M"]
        deps = _build_gmd_deps(fake_ticker_81870, {"1M": df_1M}, tf="1M")
        out = await get_market_data(deps, timeframe="1M")
        assert f"{ip_open.strftime('%Y-%m')} open" in out
        assert f"closes {ip_close.strftime('%Y-%m')}" in out
        assert "days elapsed ===" in out   # 1M total > 48h → days 单位

    @pytest.mark.asyncio
    async def test_in_progress_unknown_tf_degraded_open_only(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1 降级: 未知 tf → header 只显 open（无 closes/elapsed），不 raise。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"7m": df_5m_130bars}, tf="7m")
        out = await get_market_data(deps, timeframe="7m")
        assert "=== In-progress Candle (7m):" in out
        assert "open ===" in out          # 降级头以 ` open ===` 收尾
        assert "elapsed" not in out.split("=== In-progress Candle")[1].split("\n")[0]

    @pytest.mark.asyncio
    async def test_recent_table_renamed_no_suffix(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题1 配套: 表头 Recent Closed Candles，旧 in-progress 后缀消失。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Recent Closed Candles (5m, last" in out
        assert "still open, closes at" not in out   # 旧后缀已收敛到独立 section
```

（`fake_ticker_81870` / `df_5m_130bars` / `df_5m_anomaly` / `_build_gmd_deps` 已在该文件 import（`:128-157`）。）

(c) `tests/test_ohlcv_ts_numpy_int64.py` —— `TestInProgressHeaderUsesRealClock.test_5m_in_progress_header_not_collapsed_to_1970`（`:75-105`）重写为断**新 section** 的 open/close 时点为真值（守 numpy.int64→1970 塌缩回归，commit `cfd871e`；新 section 仍经 `_to_pd_timestamp_utc`，同风险仍在）：

```python
        # 新设计: in-progress section 直接渲 df.iloc[-1]（= 被丢弃那根），open = 该行 timestamp
        ip_open = pd.Timestamp(_KNOWN_MS + 129 * step_ms, unit="ms", tz="UTC")  # df.iloc[-1]，独立换算
        ip_close = ip_open + pd.Timedelta(minutes=5)
        exp_open = ip_open.strftime("%H:%M")
        exp_close = ip_close.strftime("%H:%M")

        ticker = SimpleNamespace(
            last=70128.0, bid=70127.9, ask=70128.1,
            high=70200.0, low=69900.0, base_volume=1234.56,
        )
        deps = _build_gmd_deps(ticker, {"5m": df}, tf="5m")
        from src.agent.tools_perception import get_market_data
        out = await get_market_data(deps, timeframe="5m")

        assert f"{exp_open} open, closes {exp_close}" in out, (
            f"expected in-progress {exp_open}/{exp_close} in In-progress Candle header; out={out[:600]}"
        )
        # 回归 guard: 绝不塌缩到 1970（旧 bug 渲成 00:xx）。
        assert ip_open.year == 2023
        assert "1970" not in out
```

（`step_ms`/`closes`/`df`/`_KNOWN_MS` 沿用 `:80-83` 既有定义；删除旧的 `header = next(...)` + `:101` `in-progress … still open, closes at …` + `:105` `00:34 …` 断言。`df.iloc[-1]` open = `_KNOWN_MS + 129*step_ms`，与旧 `last_closed(128) + offset` 数值相等，故独立换算仍 anchor 在 2023。）

(d) `tests/test_display_cycle.py:3137` 白名单 `"Recent Candles"` → `"Recent Closed Candles"`（可加 `"In-progress"`）：

```python
    "get_market_data": ["Ticker", "Technical Indicators",
                        "Recent Closed Candles", "In-progress", "RSI", "MACD", "ATR"],
```

(e) `tests/test_tool_enhancement.py:502` `assert "=== Recent Candles" in result` → `assert "=== Recent Closed Candles" in result`。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_iter_tool_opt_gmd_polish.py::TestInProgressSection tests/test_ohlcv_ts_numpy_int64.py -v`
Expected: FAIL（旧实现无独立 section）

- [ ] **Step 3: capture `now_dt`（`tools_perception.py:93`）**

```python
    now_dt = datetime.now(timezone.utc)
    fetch_ts = now_dt.strftime("%H:%M:%S")
```

- [ ] **Step 4: 删 in-progress 后缀逻辑 + 改名 + 新增 section（`tools_perception.py:182-200`）**

删除 `:182-194` 的 `in_progress_suffix` 整段逻辑，并把 Recent Candles 段（`:196-200`）替换为：

```python
    sections.append(
        f"=== Recent Closed Candles ({timeframe}, last {display_count}, "
        f"oldest-first by row) ===\n"
        + "\n".join(candle_lines)
    )

    # === In-progress Candle (议题1) === —— 渲 df.iloc[-1]（被 _closed_bars 丢弃的那根）
    if not df.empty:
        ip = df.iloc[-1]
        ip_open = _to_pd_timestamp_utc(ip["timestamp"])
        offset = TF_OFFSETS.get(timeframe)
        if offset is not None:
            ip_close = ip_open + offset
            # 1M 是 DateOffset 无 .total_seconds()，用 (open+offset)-open 得 Timedelta
            total = (ip_open + offset) - ip_open
            elapsed = _to_pd_timestamp_utc(now_dt) - ip_open
            total_s = total.total_seconds()
            elapsed_s = min(max(elapsed.total_seconds(), 0.0), total_s)  # clamp [0, total]
            if total_s <= 90 * 60:
                e_str, t_str, unit = f"{elapsed_s / 60:.0f}", f"{total_s / 60:.0f}", "min"
            elif total_s <= 48 * 3600:
                e_str, t_str, unit = f"{elapsed_s / 3600:.1f}", f"{total_s / 3600:.1f}", "h"
            else:
                e_str, t_str, unit = f"{elapsed_s / 86400:.1f}", f"{total_s / 86400:.1f}", "days"
            ip_header = (
                f"=== In-progress Candle ({timeframe}): "
                f"{_fmt_candle_time(ip_open, timeframe)} open, "
                f"closes {_fmt_candle_time(ip_close, timeframe)} "
                f"— ~{e_str} of {t_str} {unit} elapsed ==="
            )
        else:
            ip_header = (
                f"=== In-progress Candle ({timeframe}): "
                f"{_fmt_candle_time(ip_open, timeframe)} open ==="
            )
        ip_col_header = (
            f"{'Time (open UTC)':<16} {'Open':>10} {'High(so far)':>12} "
            f"{'Low(so far)':>12} {'Last':>10} {'Vol(so far)':>12}"
        )
        ip_row = (
            f"{_fmt_candle_time(ip_open, timeframe):<16} {ip['open']:>10.2f} "
            f"{ip['high']:>12.2f} {ip['low']:>12.2f} {ip['close']:>10.2f} "
            f"{ip['volume']:>12.1f}"
        )
        sections.append(
            f"{ip_header}\n{ip_col_header}\n{ip_row}\n"
            "(partial bar — excluded from all indicators; no RVol/markers until close)"
        )
```

（`df` / `now_dt` / `TF_OFFSETS` / `_to_pd_timestamp_utc` / `_fmt_candle_time` 均在 scope。In-progress 行只读 `df.iloc[-1]`，绝不进任何计算——护栏3。）

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_iter_tool_opt_gmd_polish.py tests/test_ohlcv_ts_numpy_int64.py tests/test_display_cycle.py tests/test_tool_enhancement.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/
git commit -m "feat(gmd): in-progress candle 独立 section + Recent Closed Candles 改名 (议题1)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 删 Period summary 整段（议题6）

**Files:**
- Modify: `src/agent/tools_perception.py:202-216`（删段）
- Test: `tests/test_iter_tool_opt_gmd_polish.py:419-446`、`tests/test_iter_w2r2_next_d_goldens.py:240-254`(删)；新增 1 测试

- [ ] **Step 1: 更新 / 删除被打断的断言（先红）**

(a) `tests/test_iter_tool_opt_gmd_polish.py` 的 `TestPeriodSummaryNoAvgRange`（`:419-446`）：**删除** `test_period_summary_keeps_avg_vol_and_net_delta`（`:435-446`，测的就是被删的段）；把 `test_period_summary_no_avg_range`（`:421-433`）强化为断整段消失：

```python
class TestPeriodSummaryDeleted:
    @pytest.mark.asyncio
    async def test_period_summary_section_removed(
        self, fake_ticker_81870, df_5m_130bars,
    ):
        """议题6: Period summary 整段删除（决策价值 4.7%，被 taker_flow/RVol 覆盖）。"""
        from src.agent.tools_perception import get_market_data
        deps = _build_gmd_deps(fake_ticker_81870, {"5m": df_5m_130bars})
        out = await get_market_data(deps)
        assert "=== Period summary" not in out
        assert "Net Δclose" not in out
        assert "Avg vol:" not in out
```

(b) `tests/test_iter_tool_opt_gmd_polish.py` 的 TestRVolColumn split anchor —— **无需触碰**：Task 4 已把三处改成 `.split("=== Recent Closed Candles")[1].split("\n\n=== ")[0]`（"\n\n=== " 切到下一段头），删 Period 段后 `section` 仍精确隔离为 OHLCV 表本身，断言不受影响、不必 re-point。

(c) `tests/test_iter_w2r2_next_d_goldens.py` —— **整删** `test_gmd_period_summary_section`（`:240-254`，测的就是被删的段）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_iter_tool_opt_gmd_polish.py::TestPeriodSummaryDeleted -v`
Expected: FAIL（旧实现仍渲 Period summary）

- [ ] **Step 3: 删 Period summary 整块（`tools_perception.py:202-216`）**

删除从 `# === Period summary ===`（`:202`）到 `sections.append(summary)`（`:216`）的全部代码。`return "\n\n".join(sections)`（`:218`）保留。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_iter_tool_opt_gmd_polish.py tests/test_iter_w2r2_next_d_goldens.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/
git commit -m "feat(gmd): 删 Period summary 整段，聚焦单 TF 行情快照 (议题6)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: LLM 描述 + wrapper / impl docstring 同步（议题1-6 披露）

**Files:**
- Modify: `src/agent/tools_descriptions.py:48-81`（`GET_MARKET_DATA_DESCRIPTION` 全面改写）
- Modify: `src/agent/trader.py:131`（wrapper docstring）
- Modify: `src/agent/tools_perception.py:56-79`（impl docstring）
- Test: `tests/test_trader_agent.py:381-385`、`tests/test_iter_tool_opt_gmd_polish.py:451-482`

- [ ] **Step 1: 更新被打断的断言（先红）**

(a) `tests/test_trader_agent.py`（`:382-383`）：

```python
    assert "=== Ticker" in desc, f"Ticker section header missing in example: {desc!r}"
    assert "=== Recent Closed Candles" in desc, f"Recent Closed Candles header missing: {desc!r}"
    assert "=== Period summary" not in desc, f"Period summary should be removed: {desc!r}"
    assert "=== In-progress Candle" in desc, f"In-progress Candle section missing: {desc!r}"
    assert "vol↑" in desc, f"OHLCV vol marker literal missing: {desc!r}"
    assert "range↑" in desc, f"OHLCV range marker literal missing: {desc!r}"
```

(b) `tests/test_iter_tool_opt_gmd_polish.py` 的 `TestDocstringRewrite.test_ch_desc_description_contains_new_content`（`:465-482`）：

```python
        # Block-style sections still present (CH-DESC bypasses griffe)
        assert "=== Ticker" in desc, "Ticker section header missing from CH-DESC"
        assert "=== Recent Closed Candles" in desc, "Recent Closed Candles header missing"
        assert "=== Period summary" not in desc, "Period summary should be removed"
        assert "=== In-progress Candle" in desc, "In-progress Candle section missing"

        # New content from this iter:
        assert "RVol(×SMA20)" in desc, \
            f"RVol column header (literal `RVol(×SMA20)`) missing in CH-DESC: {desc!r}"
        assert "in-progress" in desc, \
            f"in-progress documentation missing in CH-DESC: {desc!r}"

        # Markers semantics preserved:
        assert "vol↑" in desc, "vol↑ marker semantics missing"
        assert "range↑" in desc, "range↑ marker semantics missing"

        # Deletions reflected:
        assert "Avg range" not in desc, \
            f"Avg range should be removed: {desc!r}"
        assert "Market Context" not in desc, \
            f"Market Context should be removed: {desc!r}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_trader_agent.py::test_get_market_data_description_carries_example_output tests/test_iter_tool_opt_gmd_polish.py::TestDocstringRewrite -v`
Expected: FAIL（旧描述仍含 Market Context / Period summary / `=== Recent Candles`）

- [ ] **Step 3: 改写 `GET_MARKET_DATA_DESCRIPTION`（`tools_descriptions.py:48-81`）**

整段替换为：

```python
GET_MARKET_DATA_DESCRIPTION = """Single-timeframe market data: ticker (last + bid/ask + 24h H/L + base volume), technical indicators (RSI / MA(20) / MA(50) / MACD / BB / ATR), the most recent N closed candles in OHLCV table form with per-bar volume ratio (RVol = vol / SMA(20)) and anomaly markers, and the in-progress (not-yet-closed) candle in its own section.

Indicator VALUES are computed on the closed-bar series only (the in-progress candle is excluded), and the Technical Indicators header reports the last closed candle's open time. Moving averages are simple moving averages (SMA). The MA / BB comparison suffixes (`Last <price> → X% vs MA`, `Last <price> → X% ... band`) and the ATR percent (`X% of Last <price>`) use the live ticker Last as the operand / denominator — the live price measured against the closed-bar structure.

OHLCV columns: Time (open UTC) | Open | High | Low | Close | Vol | RVol(×SMA20) | Markers.
- RVol = bar volume / SMA(20) of bar volumes (`2.95×` means the bar's volume is 2.95× the 20-bar average). Rendered for every closed bar; `—` when SMA(20) has not yet started (degraded display window).
- Markers (upside-only thresholds): `vol↑` for bar volume > 2× SMA(20) of bar volumes; `range↑` for bar range (high - low) > 2× ATR(14); empty for neither threshold tripped.

In-progress Candle section (after the closed table): the current not-yet-closed candle rendered from live data — Open | High(so far) | Low(so far) | Last | Vol(so far) — plus how far into the bar interval it is. This bar is excluded from all indicators and carries no RVol/markers until it closes; the authoritative live price is the ticker Last.

Example call:
    get_market_data(timeframe="5m", candle_count=30)

Example output:
    === Ticker (BTC/USDT:USDT @ 14:27:30 UTC) ===
    Last: 81870.50 | Bid: 81870.40 | Ask: 81870.60
    24h High: 82400.10 | 24h Low: 80120.00 | 24h base vol: 12345.67

    === Technical Indicators (5m, values as of last closed 14:25) ===
    RSI(14): 58.20
    MA(20): 81960.00  (Last 81870.50 → -0.1% vs MA)
    MA(50): 82150.00  (Last 81870.50 → -0.3% vs MA)
    MACD: 12.50 | Signal: 8.30 | Histogram: 4.20
    BB(20,2): Upper 82100.00 | Middle 81870.00 | Lower 81640.00  (Last 81870.50 → 50% of band, 0%=Lower / 100%=Upper)
    ATR(14): 245.30 (0.30% of Last 81870.50)

    === Recent Closed Candles (5m, last 30, oldest-first by row) ===
    Time (open UTC)        Open       High        Low      Close        Vol  RVol(×SMA20)  Markers
    ...
    14:15              81830.00   81870.00   81825.00   81865.00      400.0         3.02×  vol↑
    14:20              81865.00   81910.00   81860.00   81895.00      178.6         1.35×

    === In-progress Candle (5m): 14:25 open, closes 14:30 — ~3 of 5 min elapsed ===
    Time (open UTC)        Open  High(so far)   Low(so far)       Last  Vol(so far)
    14:25              81895.00     81920.00      81880.00   81870.50         95.0
    (partial bar — excluded from all indicators; no RVol/markers until close)
"""
```

- [ ] **Step 4: 改 wrapper docstring（`trader.py:131`）**

```python
        """Single-timeframe market data: ticker + indicators (RSI/MA/MACD/BB/ATR) + closed OHLCV table (RVol column) + in-progress candle section. LLM-visible description override: src.agent.tools_descriptions.GET_MARKET_DATA_DESCRIPTION (carries Example block).
```

（保留下方 `Args:` 块——griffe 从这里取 `candle_count` clamp 文案进 `parameters_json_schema`，`test_iter_tool_opt_gmd_polish.py:484-506` 依赖之，不动。）

- [ ] **Step 5: 改 impl docstring（`tools_perception.py:56-79`）**

把 `:57-66` 的 `Renders:` + closed-bar 两段替换为：

```python
    Renders: Ticker (last + bid/ask + 24h H/L + base volume); Technical Indicators
    (RSI / MA(20) / MA(50) / MACD / BB / ATR via TechnicalAnalysisService), with the
    section header reporting the last closed candle's open time; Recent Closed Candles
    OHLCV table with per-bar RVol(×SMA20) column + vol↑/range↑ markers; and the
    in-progress (not-yet-closed) candle in its own section (Open / High(so far) /
    Low(so far) / Last / Vol(so far) + elapsed-into-bar).

    Indicator values / OHLCV rows are computed on closed bars (via `_closed_bars(df)`);
    the in-progress candle (`df.iloc[-1]`) is excluded from all indicators and rendered
    only in its own section.
```

（`:68-78` 的 NOTE（LLM-facing description 来源）+ `Args:` 块保留不动。）

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_trader_agent.py tests/test_iter_tool_opt_gmd_polish.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/agent/tools_descriptions.py src/agent/trader.py src/agent/tools_perception.py tests/
git commit -m "feat(gmd): LLM 描述 + docstring 同步六议题披露 (议题1-6)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 全量回归 + 陈旧 mock 可选刷新 + false-positive 验证

**Files:**
- 可选刷新（陈旧 mock，无断言依赖）：`tests/test_tools.py:76`、`tests/test_fact_only_wordlist.py:388`、`tests/test_display_cycle.py:450`
- 验证不受影响（spec §7.3 grep 假阳性，**不动**）：`tests/test_persona.py:632`、`tests/test_perception_tools_n3.py:237`、`tests/test_iter_tool_opt_volume_ratio_cleanup.py`

- [ ] **Step 1: 跑全量 pytest**

Run: `pytest -q`
Expected: 全绿（基线 2087 passed / 5 skip，本 iter 净增 in-progress section 测试、删除 4 个测被删特性的测试，最终计数随增删变化但 0 fail）

- [ ] **Step 2: 若有 stragglers，逐个定位修复**

任何 fail 大概率是 §7.2 陈旧 mock（如 `test_tools.py:76` 的 `format_for_llm` mock 串、`test_fact_only_wordlist.py:388` mock 串、`test_display_cycle.py:450` `test_format_cycle_output_basic` 硬编码 GMD 串含旧 Market Context）。这些 mock 无断言依赖、不应 fail；若 fail 则刷新其 mock 串为新形态（含 ATR(14)/新 MA 标签/Recent Closed Candles/无 Market Context）。

- [ ] **Step 3: 验证 §7.3 假阳性仍 pass（未被误改）**

Run: `pytest tests/test_persona.py::test_layer1_market_context_renders_taker_fee_rate tests/test_perception_tools_n3.py::test_htf_ma_format_includes_vs_ma_prefix tests/test_iter_tool_opt_volume_ratio_cleanup.py -v`
Expected: PASS（persona Layer1 `## Market Context` 与 GMD 无关；HTF `(price vs MA:` 不走 `format_for_llm`；`compute_indicators` 0 行改动 → `volume_ratio not in indicators` 不变）

- [ ] **Step 4: `fact-only` guard 自动覆盖新串确认**

Run: `pytest tests/test_fact_only_wordlist.py::test_get_market_data_fact_only -v`
Expected: PASS（新增字符串 `partial bar` / `excluded from all indicators` / `so far` / `values as of last closed` / `Last X → Y% vs MA` 全 fact-only，guard 断 `hits == []` 自动覆盖）

- [ ] **Step 5: Commit（若 Step 2 有改动；否则跳过）**

```bash
git add tests/
git commit -m "test(gmd): 刷新陈旧 mock 串至新输出形态

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 非 scope / 已知脱钩消费方

**`src/cli/display.py:57 _summarize_get_market_data`（system-log INFO 一行摘要器）—— 本 iter 不动，document-only。**

该函数（经 `summarize_tool` 派发，仅服务 `app.py` 的 `logger.info` 一行摘要路径；交互式 Rich 显示走 `_render_tool_body` 不经此）首行 `re.search(r"Price:\s*([\d.]+)", content)` 匹配 `Price:`，但 GMD 输出自 w2r2-next-d 改名起就渲 `Last:` 不渲 `Price:`（goldens `:201` 断 `"Price:" not in out`）→ `price_m=None` → **早已恒走 `_fallback_summary`（首 80 字符）**，与本 iter 无关的**既有死代码**。其 ATR 正则（`:62 ...% of price`）随本 iter 议题5 的 `% of price → % of Last` 会进一步漂移，但因路径已被 `Price:` 卡死而不可达 → **无新增功能回归**。

该死状态被一个 stale 测试掩盖：`tests/test_display_cycle.py:9 test_summarize_get_market_data` 喂手写旧格式 content（`Price:` / `% of price` / `Market Context` / `Recent Candles` / `Volume:`），与真实 impl 输出分叉、在隔离环境 self-consistent 地 PASS——故本 iter 的 impl/格式改动**不影响该测试**（它不喂真实输出），无需触碰。

**决策（2026-06-06，用户定夺）**：守本次 audit 的 6 议题边界（GMD agent-facing 输出域，非 CLI session-log UX）。复活 summarizer（`Price:→Last:` + `% of price→% of Last` + 重写 masking 测试为喂真实 `get_market_data` 输出）= net-new feature + scope 扩张，**留作后续独立候选**，本 iter 不做。本 plan 维持「改 4 源文件」框定。

## Self-Review（写完计划后对照 spec 自查）

**1. Spec coverage（§3 六议题 → task 映射）：**
- 议题1（in-progress section）→ Task 4 ✓
- 议题2（MA/BB `Last` 消歧）→ Task 1 ✓
- 议题3（Technical Indicators 时点锚）→ Task 3 ✓
- 议题4（删 `Last bar vol`）→ Task 2（随 Market Context 整段删）✓
- 议题5（ATR 归位 + Market Context 段消失）→ Task 2（format_for_llm 加 ATR + 删 Market Context）✓
- 议题6（删 Period summary）→ Task 5 ✓
- §5.3 描述 / §5.4 wrapper / §5.2 impl docstring → Task 6 ✓
- 4 源文件全覆盖（technical.py: Task 1-2；tools_perception.py: Task 2-6；tools_descriptions.py: Task 6；trader.py: Task 6）✓
- §7 测试面 9 硬断文件全部 reconcile（test_technical→T1/T2、test_ohlcv_utils→T1、test_iter_w2r2_next_d_goldens→T2/T3/T5、test_iter_tool_opt_gmd_polish→T2/T4/T5/T6、test_trader_agent→T6、test_tool_enhancement→T2/T4、test_ohlcv_ts_numpy_int64→T4、test_multi_tf_drift_guards→T2、test_display_cycle→T2/T4）✓；§7.3 假阳性→T7 验证不动 ✓

**2. Placeholder scan：** 无 TBD/TODO；每个 code step 含完整 f-string / 完整断言；删段步骤给出精确行号区间与起止锚。

**3. Type / 命名一致性：** `now_dt`（T4 capture）→ T4 in-progress 用；`ti_header`/`ti_ts`（T3）；`ip`/`ip_open`/`ip_close`/`e_str`/`t_str`/`unit`（T4）——均 task 内定义即用，无跨 task 悬空引用。`_fmt_candle_time` / `_to_pd_timestamp_utc` / `TF_OFFSETS` 签名与既有 helper 一致（`ohlcv_utils.py` 只读）。

**4. 0 行算法不变量：** `compute_indicators`（technical.py:8-54）/ `_closed_bars` / `_atr_series` / 指标公式 / RVol·markers 计算（tools_perception.py:146-181）全程不改——所有 task 只动渲染与描述。✓

**「每 commit green」trace（含 plan-review 修订）：** 关键陷阱——跨 task 存活的 Period summary（直到 Task 5 才删）自带 `(2.00×)` token，任何对 whole-output / split-to-end 做 `×` 匹配的断言都会被它污染（plan-review 抓出两处：Task 2 drift-guard 取 `findall(...)[-1]` 误中 Period 的 2.00× 而非 RVol 末行 4.80×；Task 4 in-progress `"×" not in ip_block` 误含 Period 段）。修订后，drift-guard（Task 2）与 in-progress 无-marker 断言（Task 4）及 TestRVolColumn 重指向均用 `\n\n=== ` 段分隔符精确限定到目标 section，**不再依赖"那一刻输出末尾只有 RVol"的隐含假设** → 每个 commit 实测绿、不靠后续 task 意外恢复。已独立重扫全部新增断言，确认仅此两处对段边界敏感、均已 scope；其余新断言走 `in` presence / 表头首行 / 删除后 `not in`，不受 Period 段存活影响。
