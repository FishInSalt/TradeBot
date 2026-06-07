# iter-taker-flow-render Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** session log 渲染层（`src/cli/display.py`）把 `Taker Flow` section 整段保留、不折叠，让 human reviewer 能复现 agent 逐 bar 引用所依据的数据。

**Architecture:** 在 `_render_tool_body` 的 section 渲染循环里，对 header 前缀匹配 `"Taker Flow"` 的 section 跳过通用 `_clip_body` 折叠、整段 `section.body` 原样输出。新增 1 个常量 + 1 个 by-content header 判定 helper + 改 1 行 dispatch。不动 agent 实见输出（`_render_taker_flow`），不动通用 `_clip_body`（5 工具共用），不外溢到其它 section。

**Tech Stack:** Python 3 / pytest / Rich（`escape`）。改动文件仅 `src/cli/display.py` + 测试 `tests/test_display_cycle.py`。

参考 spec：`docs/superpowers/specs/2026-06-07-iter-taker-flow-render-design.md`

---

## File Structure

- **Modify** `src/cli/display.py`
  - 新增常量 `_FULL_KEEP_SECTION_PREFIXES` + helper `_is_full_keep_section`（插在 `_flatten_groups` 之后、`_render_tool_body` 之前，紧邻消费点）。
  - 改 `_render_tool_body` 渲染循环 dispatch 行（当前 `display.py:604` `clipped = _clip_body(section.body)`）。
- **Modify (test)** `tests/test_display_cycle.py`
  - 新增本地 `_taker_bars` helper（测试隔离，不跨文件 import `test_taker_flow._bars`）。
  - 新增 `_is_full_keep_section` 单元测试 3 例。
  - 新增 round-trip 渲染测试：核心用真实 `_render_taker_flow` 生成 content 喂 `_render_tool_body`（防 fixture drift）。
  - 新增 GMD K-line 折叠不外溢回归守护 + 降级路径。

**根因事实（已核验，写 fixture 的前提）：** `_render_taker_flow` 的 per-bar 行形如 `  12:45* 55%  …`（行首 2 空格缩进 + 时间，**非** `[<word>]`），`_is_anchor` 要求行首立即 `[` → `anchor_count == 0` → body（limit=6 即 18→实测 12 行内部含空行；limit=12 为 18 行）走 `_clip_body` Branch 2 list-like 折叠。豁免后整段保留。GMD K-line section header 是 `"Recent Closed Candles (…)"`，前缀不匹配 `"Taker Flow"` → 不豁免、仍折叠。

---

## Task 1: `_is_full_keep_section` helper + 常量（纯函数地基）

**Files:**
- Modify: `src/cli/display.py`（插在 `_flatten_groups` 函数定义之后、`_render_tool_body` 之前）
- Test: `tests/test_display_cycle.py`

- [ ] **Step 1: 写失败的单元测试**

在 `tests/test_display_cycle.py` 末尾追加：

```python
# === iter-taker-flow-render: _is_full_keep_section 单元 ===


def test_is_full_keep_section_matching():
    """Taker Flow header 前缀命中豁免（易变后缀 symbol·period·@ts 不影响判定）。"""
    from src.cli.display import _is_full_keep_section
    assert _is_full_keep_section(
        "Taker Flow (BTC/USDT:USDT · 5m bars · @ 12:47 UTC)"
    ) is True


def test_is_full_keep_section_non_matching_gmd():
    """GMD K-line header 'Recent Closed Candles' 前缀不匹配 → 不豁免。"""
    from src.cli.display import _is_full_keep_section
    assert _is_full_keep_section("Recent Closed Candles (15m, last 40)") is False


def test_is_full_keep_section_none_header():
    """无 header 的 fallback section（reject 纯文本路径）→ 不豁免。"""
    from src.cli.display import _is_full_keep_section
    assert _is_full_keep_section(None) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_display_cycle.py -k is_full_keep_section -v`
Expected: FAIL — `ImportError: cannot import name '_is_full_keep_section' from 'src.cli.display'`

- [ ] **Step 3: 写最小实现**

在 `src/cli/display.py` 的 `_flatten_groups` 函数（当前结尾 `return out`）之后、`_render_tool_body` 之前插入：

```python
# === iter-taker-flow-render: full-keep sections (session-log 渲染豁免) ===
# 这些 section 的 body 整段保留、不走 _clip_body 折叠：核心小表格的逐 bar
# 序列被 list-like 行折叠后会失去意义（reviewer 无法复现 agent 逐 bar 引用所
# 依据的数据）。匹配 _parse_sections 出的 section.header 文本前缀 —— by-content
# （契合本文件既有 by-content sectioned/plain dispatch 哲学），不靠 tool_name
# frozenset。get_taker_flow header = "Taker Flow (BTC/USDT:USDT · 5m bars · @ …)"；
# GMD K-line header = "Recent Closed Candles (…)" 前缀不匹配 → 不受影响、仍折叠。
_FULL_KEEP_SECTION_PREFIXES: tuple[str, ...] = ("Taker Flow",)


def _is_full_keep_section(header: str | None) -> bool:
    """Return True iff this section should bypass _clip_body folding entirely.

    header is the _parse_sections-extracted section header text (None for an
    unnamed fallback section). Matched by prefix so the volatile suffix
    (symbol · period · @ timestamp) does not affect the decision.
    """
    return header is not None and header.startswith(_FULL_KEEP_SECTION_PREFIXES)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_display_cycle.py -k is_full_keep_section -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat(display): _is_full_keep_section helper — Taker Flow 渲染豁免判定

by-content header 前缀判定（契合既有 by-content dispatch），尚未接入
渲染循环（Task 2 接线）。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 接入 dispatch + 主流 round-trip 测试 + GMD 不外溢守护

**Files:**
- Modify: `src/cli/display.py:604`（dispatch 行）
- Test: `tests/test_display_cycle.py`

- [ ] **Step 1: 写失败的 round-trip 测试 + 本地 helper + GMD 守护**

在 `tests/test_display_cycle.py` 末尾追加。先加本地 bars helper（测试隔离副本，不跨文件 import）：

```python
# === iter-taker-flow-render: 渲染层 round-trip（真实 _render_taker_flow 输出）===


def _taker_bars(n, period_ms, *, base_open, sell=1_000_000.0, buy=1_000_000.0):
    """n ascending TakerFlowBar; bar i opens at base_open + i*period_ms.
    本地隔离副本（mirror of tests/test_taker_flow.py::_bars）；caller 设 base_open
    使最后一根相对 now_ms in-progress。"""
    from src.integrations.exchange.base import TakerFlowBar
    return [TakerFlowBar(ts=base_open + i * period_ms, sell_usd=sell, buy_usd=buy)
            for i in range(n)]


def test_taker_flow_section_full_kept_limit_12():
    """limit=12 真实 taker_flow 输出经 session-log 渲染整段保留、无折叠
    （旧 list-like Branch 2 会把 18 行 body 折成 5 行，丢掉 Per-bar 标题/表头/中段 bar）。"""
    from src.agent.tools_perception import _render_taker_flow
    from src.cli.display import _render_tool_body
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _taker_bars(21, period_ms, base_open=now - 120_000 - 20 * period_ms)
    content = _render_taker_flow(bars, "5m", 12, now_ms=now,
                                 symbol="BTC/USDT:USDT", fetch_ts="12:47")
    out = _render_tool_body("get_taker_flow", content)
    assert "omitted" not in out                          # 无折叠标记
    assert "Per-bar (bar open UTC, newest first" in out  # 中段标题保留（旧会折掉）
    assert "RVol(×20-bar)" in out                        # 表头保留（旧会折掉）
    assert "Now (" in out and "Window (12 bars" in out
    assert len(out.splitlines()) >= 18                   # 全 18 body 行 + head + header


def test_taker_flow_section_full_kept_default_limit_6():
    """附带受益：默认 limit=6（body ≥10 行）当前也被 Branch 2 折叠，新设计下全保留。"""
    from src.agent.tools_perception import _render_taker_flow
    from src.cli.display import _render_tool_body
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _taker_bars(21, period_ms, base_open=now - 120_000 - 20 * period_ms)
    content = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00")
    out = _render_tool_body("get_taker_flow", content)
    assert "omitted" not in out
    assert "Per-bar (bar open UTC, newest first" in out
    assert "RVol(×20-bar)" in out


def test_full_keep_does_not_leak_to_gmd_kline_section():
    """豁免不外溢：GMD 'Recent Closed Candles' section（前缀不匹配 Taker Flow）
    仍走 _clip_body 折叠。"""
    from src.cli.display import _render_tool_body
    body_lines = "\n".join(f"14:{i:02d}  100 101 99 100  1.5" for i in range(40))
    content = f"=== Recent Closed Candles (15m, last 40) ===\n{body_lines}"
    out = _render_tool_body("get_market_data", content)
    assert "omitted" in out                              # 仍折叠（守护非目标 section）
```

- [ ] **Step 2: 跑测试确认 round-trip 失败、GMD 守护通过**

Run: `python -m pytest tests/test_display_cycle.py -k "taker_flow_section_full_kept or full_keep_does_not_leak" -v`
Expected:
- `test_taker_flow_section_full_kept_limit_12` FAIL（dispatch 仍调 `_clip_body` → 出现 `[... N rows omitted ...]`，`assert "omitted" not in out` 失败）
- `test_taker_flow_section_full_kept_default_limit_6` FAIL（同上）
- `test_full_keep_does_not_leak_to_gmd_kline_section` PASS（GMD 本就折叠，守护提前到位）

- [ ] **Step 3: 接入 dispatch**

在 `src/cli/display.py` 的 `_render_tool_body` 渲染循环，把当前这一行（`display.py:604`）：

```python
        clipped = _clip_body(section.body)
```

改为：

```python
        clipped = (
            section.body
            if _is_full_keep_section(section.header)
            else _clip_body(section.body)
        )
```

- [ ] **Step 4: 跑测试确认全通过**

Run: `python -m pytest tests/test_display_cycle.py -k "taker_flow_section_full_kept or full_keep_does_not_leak" -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat(display): Taker Flow section 整段保留、跳过 _clip_body 折叠

_render_tool_body 渲染循环：_is_full_keep_section 命中的 section 用
section.body 原样输出，否则走通用 _clip_body。修复 taker_flow 逐 bar
表格在 session log 被 list-like 折叠（含默认 limit=6 路径）。不动 agent
实见输出（_render_taker_flow），不外溢到 GMD 等其它折叠工具（守护测试）。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 边界 round-trip（in-progress / 1d / close-note / 降级）

**Files:**
- Test: `tests/test_display_cycle.py`（仅新增测试，无源码改动 —— 锁定 spec §4 边界行为）

- [ ] **Step 1: 写边界测试**

在 `tests/test_display_cycle.py` 末尾追加：

```python
def test_taker_flow_full_kept_in_progress_footnote_and_star():
    """in-progress：'row 1 = current in-progress' header + still-forming footnote
    在全保留输出里都可见。"""
    from src.agent.tools_perception import _render_taker_flow
    from src.cli.display import _render_tool_body
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _taker_bars(21, period_ms, base_open=now - 120_000 - 20 * period_ms)
    content = _render_taker_flow(bars, "5m", 12, now_ms=now, symbol="X", fetch_ts="00:00")
    out = _render_tool_body("get_taker_flow", content)
    assert "row 1 = current in-progress" in out
    assert "still forming" in out                        # per-bar footnote 保留
    assert "omitted" not in out


def test_taker_flow_full_kept_1d_period():
    """1d period：日期格式 Time 列整表全保留（不再做 bar 行识别/格式特判）。"""
    from src.agent.tools_perception import _render_taker_flow
    from src.cli.display import _render_tool_body
    period_ms = 86_400_000
    now = 1_000_000_000_000
    bars = _taker_bars(21, period_ms, base_open=now - 3_600_000 - 20 * period_ms)
    content = _render_taker_flow(bars, "1d", 12, now_ms=now, symbol="X", fetch_ts="00:00")
    out = _render_tool_body("get_taker_flow", content)
    assert "omitted" not in out
    assert "Per-bar (bar open UTC, newest first" in out


def test_taker_flow_full_kept_close_note_preserved():
    """close_note 路径：note 行在全保留输出里可见。"""
    from src.agent.tools_perception import _render_taker_flow
    from src.cli.display import _render_tool_body
    period_ms = 86_400_000
    now = 1_000_000_000_000
    bars = _taker_bars(21, period_ms, base_open=now - 3_600_000 - 20 * period_ms)
    note = "Close: n/a — 1d rubik/OHLCV day-boundary mismatch (16:00 vs 00:00 UTC)"
    content = _render_taker_flow(bars, "1d", 6, now_ms=now, symbol="X",
                                 fetch_ts="00:00", close_note=note)
    out = _render_tool_body("get_taker_flow", content)
    assert note in out
    assert "omitted" not in out


def test_taker_flow_full_kept_degraded_no_data():
    """降级路径：有 header 被豁免、单行 body 渲染不报错、内容保留
    （渲染层合成 content；豁免对短 body 安全）。"""
    from src.cli.display import _render_tool_body
    content = ("=== Taker Flow (X · 5m bars · @ 00:00 UTC) ===\n"
               "No taker-volume data available for X 5m (rubik returned 0 bars).")
    out = _render_tool_body("get_taker_flow", content)
    assert "No taker-volume data available" in out
```

- [ ] **Step 2: 跑测试确认通过**

Run: `python -m pytest tests/test_display_cycle.py -k "taker_flow_full_kept" -v`
Expected: PASS（4 passed —— dispatch 已在 Task 2 接入，这些是边界锁定）

- [ ] **Step 3: 跑两个相关测试文件确认无回归**

Run: `python -m pytest tests/test_display_cycle.py tests/test_taker_flow.py -q`
Expected: PASS（全绿，含既有 T-CLIP / drift-guard / `_render_taker_flow` 测试不受影响）

- [ ] **Step 4: 全量 pytest 确认无跨文件回归**

Run: `python -m pytest -q`
Expected: PASS（基线 2092 passed 之上净增本 iter 新测试，0 failed）

- [ ] **Step 5: Commit**

```bash
git add tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
test(display): Taker Flow 全保留边界 —— in-progress/1d/close-note/降级

锁定 spec §4 边界行为：in-progress 星标+footnote、1d 日期 Time 列、
close_note、降级单行 body 均整段保留、不报错。纯测试新增，无源码改动。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage（逐条对 spec 章节）：**

| spec 章节 | 实现位置 |
|---|---|
| §3.1 识别（by-content header `_FULL_KEEP_SECTION_PREFIXES` + `_is_full_keep_section`） | Task 1 |
| §3.2 行为（dispatch：命中跳过 `_clip_body`） | Task 2 Step 3 |
| §3.3 降级 / 错误路径（None header 不豁免 / 短 body 安全） | Task 1 `none_header` 单元 + Task 3 降级 |
| §4 测试矩阵 limit=12 全显示无 omit | Task 2 `full_kept_limit_12` |
| §4 测试矩阵 limit=6 默认附带受益 | Task 2 `full_kept_default_limit_6` |
| §4 测试矩阵 in-progress footnote | Task 3 `in_progress_footnote_and_star` |
| §4 测试矩阵 1d period | Task 3 `full_kept_1d_period` |
| §4 测试矩阵 close note | Task 3 `full_kept_close_note_preserved` |
| §4 测试矩阵 GMD `Recent Closed Candles` 回归守护 | Task 2 `full_keep_does_not_leak_to_gmd_kline_section` |
| §4 测试矩阵 降级 No taker-volume | Task 3 `full_kept_degraded_no_data` |
| §4 测试矩阵 `_is_full_keep_section` 单元三态 | Task 1（matching / gmd / none） |
| §5 影响面（仅 display.py 1 常量+1 helper+1 dispatch 行 + 测试） | File Structure |

全 11 项 spec 要求均有对应 task，无 gap。

**Placeholder scan：** 无 TBD / TODO / "类似 Task N" / "添加适当处理" —— 每个 step 含完整可执行代码与精确断言。

**Type consistency：** `_is_full_keep_section(header: str | None) -> bool` 在 Task 1 定义、Task 2 dispatch 调用签名一致；`_FULL_KEEP_SECTION_PREFIXES: tuple[str, ...]` 单一定义；`_taker_bars` 在 Task 2 引入、Task 3 复用同签名；测试均经真实 `_render_taker_flow` / `_render_tool_body`（既有公共签名，已核验）。
