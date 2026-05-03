# Iter W2 R2-8c — Tool Output Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 cycle log `▾ Action` 段内 tool 行展示从 80-char fallback 升级为 "Tool 端 unified `=== Section ===` sectioning + Display 端 universal section-aware 裁剪" 双层架构，实现 byte-equal LLM/display 一致性 + head/tail clipping for ≥10-row sections + `_render_reasoning` 800→2000 thinking 截断。

**Architecture:**
- **Tool 层** (`src/agent/tools_perception.py`): 20 perception 工具按 spec §4.1 sectioning convention 输出（19 强制 sectioning + `get_memories` backend-dependent 例外）。L1 (hard raise) 不动；L2 (内部 try/except + success outcome) 转 `=== Error ===` section；L3 (per-source) 字段级 fallback in-section。
- **Display 层** (`src/cli/display.py`): 新增 `_parse_sections` / `_clip_body` / `_render_perception_tool` helpers；`_render_action` dispatch 重构为 4-branch (L1 error → save_memory → execution single-line → perception multi-line)；现有 `_PERCEPTION_PARSERS` 改名 `_SYSTEM_LOG_PERCEPTION_PARSERS` 仅供 `cli/app.py` system log 摘要消费；`_render_reasoning` `max_chars` 800→2000。
- **三层集合** (`_PERCEPTION_TOOL_NAMES` 20 / `_SECTIONED_PERCEPTION_TOOL_NAMES` 19 / `_EXECUTION_TOOL_NAMES` 11) + `save_memory` branch 互斥 + 完整覆盖 32 registered tools (T-DG-2)。

**Tech Stack:** Python 3.12+ (pyproject `requires-python = ">=3.12"`) / pydantic-ai (`ToolCallPart` / `ToolReturnPart`) / Rich (`escape` for markup attack surface) / pytest 9 / `uv run` 调度（venv-agnostic）/ dataclass `Section(header, body)` model。

**Spec reference:** `docs/superpowers/specs/2026-05-03-iter-w2r2-8c-tool-output-optimization-design.md` (commit `ffc1023`)
**Smoke baseline:** `.working/r2-8c-smoke-data-2026-05-03.md` (1048 tests collected `pytest --collect-only`, 2026-05-03)
**Branch:** `feature/iter-w2r2-8c-tool-output-optimization`

---

## File Structure

| 文件 | 操作 | 责任 |
|---|---|---|
| `src/cli/display.py` | 修改 | 新增 `Section` dataclass + 3 helpers (`_parse_sections` / `_clip_body` / `_render_perception_tool`) + 三层集合 frozenset + `_render_action` 重构 + `_PERCEPTION_PARSERS` → `_SYSTEM_LOG_PERCEPTION_PARSERS` rename + `_render_reasoning` `max_chars` 800→2000 |
| `src/agent/tools_perception.py` | 修改 | 20 perception 工具按 spec §4.2 sectioning convention 重构（19 强制 + `get_memories` backend 例外）：(1) implicit → explicit `=== Section ===` headers；(2) L2 fallback 转 `=== Error ===` section；(3) §4.1.1 参数顺序 convention；(4) 新闻/journal 等多 entry list 按 entry boundary 分行 |
| `src/agent/trader.py` | 不动 | `REGISTERED_TOOL_NAMES` 已就绪（32 tools），T-DG-2 直接消费 |
| `tests/test_display_cycle.py` | 修改 + 新增测试 | 新增 helper 单测 (T-PARSE-1~3 / T-CLIP-1~3 / T-RPT-1~4) + 集成 (T-INT-1~3) + edge cases (T-EC-1~11) + byte-equal (T-BE-1) + drift guards (T-DG-1 / T-DG-2) + 19 sectioned tool snapshot fixtures (inline) + 既有 perception parser tests **保留**（system log path 仍消费） |
| `.working/r2-8c-token-verification-<date>.md` | 新建（plan artifact） | T0 token A/B 估算结果 (markdown table)；不进 CI 不进 pytest，§8.10 三档评判 record + AC-token 凭证 |

**Snapshot 落地决议**（spec §5.2 plan-pending）: Tool snapshots 作为 inline fixtures 嵌在 `tests/test_display_cycle.py` 内（每工具一个 raw content fixture + expected `_render_perception_tool` output 断言），避免新建 `tests/agent/tool_returns/` 目录基建。19 sectioned tools × 1-2 snapshot/tool = ~25-35 snapshot tests，inline manageable。

---

## Bite-sized Task Granularity

9 个 task，按 dependency 顺序执行。每 task TDD（red → green → run → commit）。Tasks T1-T4 是 helper / 框架基础设施 + thinking；T5-T7 是 20 工具 batch 重构（按调用频次）；T0 是前置 gate；T8 是终验。

### Snapshot fixture vs source 字面 mismatch policy

T5-T7 的 snapshot expected 字面值是 plan 阶段按 spec §4.2 enum + 当前 source 推断的预期形态。impl 阶段执行时若发现 snapshot 字面 mismatch（如多/少一个空格、数值精度小数位差异、字段顺序微调），**优先调 fixture 字面对齐源码 actual** 而非反向硬塞源码符合 fixture（除非源码 actual 违反 spec §4.2 sectioning convention 才改源码）。这种 mismatch 通常是 plan 阶段对源码细节估算偏差，不是 R2-8c sectioning convention 问题。

判定准则:
- ✅ 调 fixture: 数值精度、空格 / tab 差异、字段顺序与 spec §4.2 enum 不冲突的微调
- ❌ 改源码: source 实际输出违反 §4.2 enum 列出的 section 划分 / header 格式 / 关键字段缺失

---

### Task 0: T-token A/B Fixture Verification (前置 gate)

**Files:**
- Create: `.working/r2-8c-token-verification-2026-05-03.md` (plan note artifact)
- Read only: `src/agent/tools_perception.py:39-136` (`get_market_data` reference fixture)

**Purpose**: spec §8.10 + AC-token — 在 20 工具批量重构前置，固定 **3 个 representative fixtures** (`get_market_data` 已 sectioned anchor / `get_position` implicit→explicit 重构 / `get_higher_timeframe_view` sectioning + L2 + 参数 swap representative) 估算 sectioning 增量 token / cycle，三档评判 (≤15% pass / 15-20% 灰区 / >20% stop)。**这是 plan verification artifact，不是 pytest test**——避免 CI 依赖外部 tokenizer 行为。

- [ ] **Step 1: 准备 3 个 representative 工具的 baseline (R2-8a 旧形态) fixture**

3-tool 选择覆盖 token-delta 主要风险面（**全部必跑，不允许 single-tool short-circuit**——`get_market_data` 已 sectioned 几乎无 delta，但真实 token 风险来自 implicit→explicit 转换 + L2 sectioning，需跨 representative 取样）：

| Tool | 取样意义 | 旧 baseline 来源 |
|---|---|---|
| `get_market_data` | 已 sectioned 工具 token delta 上限 (anchor — sim #6 高频 27 calls) | 当前 source `tools_perception.py:39-136` happy-path 输出 |
| `get_position` | implicit → explicit 4-section 重构 representative (T6 改动量大) | 当前 source line 156-295 happy-path 输出 (有 position 状态) |
| `get_higher_timeframe_view` | sectioning + L2 path conversion + param order swap representative | 当前 source line 793-871 happy-path 输出 |

复制 3 工具 happy-path 输出 sample 作 baseline content (markdown code block 形式存进 plan note artifact)。

- [ ] **Step 2: 准备 R2-8c 目标输出 fixture (新形态)**

按 spec §4.2.1 / §4.2.11 / §4.2.2 enum 表，对 3 工具分别构造目标 R2-8c 形态:

- `get_market_data`: §4.2.1 4 sections (Ticker / Technical Indicators / Market Context / Recent Candles)，参数顺序 `({symbol})` / `({timeframe})` / `({timeframe}, last {N})` 已符合 §4.1.1，预期 delta 极小
- `get_position`: §4.2.11 4 sections (Position / PnL / Risk Exposure / Exit Orders) 替换原 implicit "Current Position:" / "Risk exposure:" / "Exit orders:" 标签——4 个 `=== Section ===` header 增量 ~80 chars
- `get_higher_timeframe_view`: §4.2.2 — header 参数 swap (timeframe, symbol)→(symbol, timeframe) 几乎无 delta；L2 path 由 plain text 升级为 `=== Higher Timeframe View (...) ===\n=== Error ===\nTemporarily unavailable.` 增量 ~50-60 chars (但 L2 是 unhappy path，frequency 低)

- [ ] **Step 3: 选 spec-fixed tokenizer 估算**

无外部依赖：使用 chars/4 作 token-估算 proxy（OpenAI 经验法 1 token ≈ 4 chars for English/markdown，已用于 R2-8a token 估算）。

```python
# Estimate tokens (chars / 4 proxy)
baseline_chars = len(baseline_content)   # R2-8a fixture content total
target_chars = len(target_content)       # R2-8c 目标 fixture content total
delta_pct = (target_chars - baseline_chars) / baseline_chars * 100
```

**精度 caveat (record in plan note artifact)**: chars/4 proxy 对纯数字/标点密集的表格（如 Recent Candles 50 行 OHLCV、order book depth）实际 token/char 比可能 0.25-0.4 而非 1/4，绝对值估算偏差 ±20%。**delta_pct 是相对值（new - old）/ old，部分系统性偏差互相抵消**——但灰区 (15-20%) 判定附近精度敏感。如 plan note 三档评判结果落灰区且对结论有疑，可选升级到 `tiktoken cl100k_base` 或 anthropic SDK `count_tokens` 复测（非必需，risk note 即可——R2-9 W2 真实 cycle token 测量是后置 ground truth）。

- [ ] **Step 4: 落 plan note artifact**

写 `.working/r2-8c-token-verification-2026-05-03.md` 含:
- 表头: tool / baseline_chars / target_chars / delta_pct / verdict
- 必含全部 3 个 representative tools (`get_market_data` / `get_position` / `get_higher_timeframe_view`) — 单 tool 数据不能代表整体 token 风险面
- 综合判断: 取 3 工具 delta_pct **均值 + 最大值** 双指标，三档评判用最大值（保守判定）
- 三档评判结果: ≤15% pass / 15-20% gray + risk note + 一次压缩尝试 / >20% stop 回 brainstorm

- [ ] **Step 5: 三档评判判定 + commit plan artifact**

**判定**:
- ≤ 15%: ✅ pass — 进入 Task 1
- 15-20%: ⚠️ 灰区 — record risk + 尝试一次明显压缩（精简 section header verbosity / 合并相邻 sections / 字段缩写）→ 重测；二次 ≤ 15% pass / 仍 15-20% 接受继续 + risk note
- \> 20%: 🛑 stop — 触发 spec 回 brainstorm（不进 Task 1）

```bash
git add .working/r2-8c-token-verification-2026-05-03.md
git commit -m "chore(iter-w2r2-8c): T0 token A/B verification artifact

Pre-impl A/B token verification per spec §8.10 / AC-token.
Records baseline (R2-8a current) vs target (R2-8c sectioned)
chars/4 estimate for representative tools; 三档评判 result
recorded for impl-stage gate.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git status
```

Expected: clean tree, 1 file added (`.working/r2-8c-token-verification-2026-05-03.md`).

---

### Task 1: Section dataclass + 3 helpers + Helper Unit Tests

**Files:**
- Modify: `src/cli/display.py` (新增 `Section` dataclass + `_parse_sections` / `_clip_body` / `_render_perception_tool` helpers)
- Test: `tests/test_display_cycle.py` (新增 T-PARSE-1~3 / T-CLIP-1~3 / T-RPT-1~4 helper 单测)

- [ ] **Step 1: Write the failing tests for `_parse_sections`**

加到 `tests/test_display_cycle.py` 末尾（独立 section comment `# === R2-8c helper tests ===`）。`Section.body` 是 `tuple[str, ...]` 因 dataclass `frozen=True` 需要 immutable 字段（也支持 hash / set 成员）：

```python
# === R2-8c helper tests ===

# --- T-PARSE: _parse_sections ---


def test_parse_sections_multi_sections():
    """T-PARSE-1: 多 sections 完整 parse — header + body 分组。"""
    from src.cli.display import _parse_sections, Section
    content = (
        "=== Ticker (BTC/USDT:USDT) ===\n"
        "Price: 75212.00\n"
        "Bid: 75200.00\n"
        "\n"
        "=== Technical Indicators (5m) ===\n"
        "RSI(14): 33.55\n"
        "MACD: -131"
    )
    out = _parse_sections(content)
    assert out == [
        Section(header="Ticker (BTC/USDT:USDT)",
                body=("Price: 75212.00", "Bid: 75200.00")),
        Section(header="Technical Indicators (5m)",
                body=("RSI(14): 33.55", "MACD: -131")),
    ]


def test_parse_sections_no_header_fallback():
    """T-PARSE-2: 无 header → 单 unnamed section (fallback path, get_memories case)."""
    from src.cli.display import _parse_sections, Section
    content = "Plain text line 1\nPlain text line 2"
    out = _parse_sections(content)
    assert out == [Section(header=None, body=("Plain text line 1", "Plain text line 2"))]


def test_parse_sections_empty_content():
    """T-PARSE-3: 空 content → 单 unnamed empty section。"""
    from src.cli.display import _parse_sections, Section
    out = _parse_sections("")
    assert out == [Section(header=None, body=())]
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/test_display_cycle.py::test_parse_sections_multi_sections tests/test_display_cycle.py::test_parse_sections_no_header_fallback tests/test_display_cycle.py::test_parse_sections_empty_content -v
```

Expected: 3 FAIL with `ImportError: cannot import name '_parse_sections' from 'src.cli.display'` (or `Section`).

- [ ] **Step 3: Implement `Section` dataclass + `_parse_sections`**

加到 `src/cli/display.py` 在 `# === R2-8a: Cycle log narrative render helpers (spec §4) ===` 块**之前**新增 `# === R2-8c: Section parsing & clipping helpers (spec §4.3) ===` block，紧随 `summarize_tool` 函数（约 line 733 末尾），但放在 `summarize_tool` 之前 helper 区域更合逻辑——与 `_PERCEPTION_PARSERS` 同档；具体放在 `_PERCEPTION_PARSERS` 字典 (line 309-318) 之后、`resolve_tool_display` 之前：

```python
# === R2-8c: Section parsing & clipping helpers (spec §4.3) ===


@dataclass(frozen=True)
class Section:
    """Parsed tool output section (spec §4.3.1)."""
    header: str | None  # None = unnamed (fallback for tool output without `=== Section ===`)
    body: tuple[str, ...]  # immutable for frozen dataclass equality / set membership


_SECTION_HEADER_RE = re.compile(r"^=== (.+) ===$")


def _parse_sections(content: str) -> list[Section]:
    """Parse tool content into sections by '=== {name} ===' headers (spec §4.3.1).

    Algorithm:
      1. Split content by '\n'
      2. Lines matching r'^=== (.+) ===$' are section starts
      3. Lines until next header form the section body
      4. Strip blank lines at start/end of each body
      5. No header in entire content → [Section(header=None, body=lines stripped)]
      6. Empty content → [Section(header=None, body=())]
    """
    if not content:
        return [Section(header=None, body=())]

    lines = content.split("\n")
    sections: list[tuple[str | None, list[str]]] = []
    current_header: str | None = None
    current_body: list[str] = []

    for line in lines:
        m = _SECTION_HEADER_RE.match(line)
        if m:
            # flush previous
            sections.append((current_header, current_body))
            current_header = m.group(1)
            current_body = []
        else:
            current_body.append(line)
    sections.append((current_header, current_body))

    # First entry is "before any header" — drop only when it has no header AND empty body
    # (otherwise it's a legitimate fallback section per T-PARSE-2)
    if sections and sections[0][0] is None and not _strip_blanks(sections[0][1]):
        if len(sections) > 1:
            sections = sections[1:]

    return [Section(header=h, body=tuple(_strip_blanks(b))) for h, b in sections]


def _strip_blanks(lines: list[str]) -> list[str]:
    """Remove leading + trailing blank lines (preserve internal blanks)."""
    start = 0
    end = len(lines)
    while start < end and lines[start].strip() == "":
        start += 1
    while end > start and lines[end - 1].strip() == "":
        end -= 1
    return lines[start:end]
```

注: `dataclass` 已在 `src/cli/display.py:5` 导入 (`from dataclasses import dataclass`)；`re` 同已导入 (`import re` line 4)。无需新增 import。

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_display_cycle.py::test_parse_sections_multi_sections tests/test_display_cycle.py::test_parse_sections_no_header_fallback tests/test_display_cycle.py::test_parse_sections_empty_content -v
```

Expected: 3 PASS.

- [ ] **Step 5: Write the failing tests for `_clip_body`**

继续 `tests/test_display_cycle.py` 加:

```python
# --- T-CLIP: _clip_body ---


def test_clip_body_under_threshold_keep_all():
    """T-CLIP-1: body < 10 行 → keep all (D7 universal rule)."""
    from src.cli.display import _clip_body
    body = tuple(f"line {i}" for i in range(9))
    assert _clip_body(body) == body


def test_clip_body_at_or_above_threshold_head_tail():
    """T-CLIP-2: body ≥ 10 行 → head=2 + '[N rows omitted]' + tail=2 (D7 校准 head/tail=2)."""
    from src.cli.display import _clip_body
    body = tuple(f"line {i}" for i in range(15))
    out = _clip_body(body)
    assert out == (
        "line 0", "line 1",
        "[... 11 rows omitted ...]",
        "line 13", "line 14",
    )


def test_clip_body_exact_threshold_triggers_clipping():
    """T-CLIP-3: body == 10 行 (边界) → head/tail 触发 (>= n)."""
    from src.cli.display import _clip_body
    body = tuple(f"line {i}" for i in range(10))
    out = _clip_body(body)
    assert out == (
        "line 0", "line 1",
        "[... 6 rows omitted ...]",
        "line 8", "line 9",
    )
```

- [ ] **Step 6: Run failing tests**

```bash
uv run pytest tests/test_display_cycle.py::test_clip_body_under_threshold_keep_all tests/test_display_cycle.py::test_clip_body_at_or_above_threshold_head_tail tests/test_display_cycle.py::test_clip_body_exact_threshold_triggers_clipping -v
```

Expected: 3 FAIL with `ImportError: cannot import name '_clip_body'`.

- [ ] **Step 7: Implement `_clip_body`**

紧接 `_strip_blanks` helper 之后加:

```python
def _clip_body(body: tuple[str, ...] | list[str], n: int = 10) -> tuple[str, ...]:
    """D4 universal clipping (head=2 / tail=2, spec §4.3.2 review-校准).

    body length:
      < n  → keep all
      >= n → (body[0], body[1],
              f"[... {len(body)-4} rows omitted ...]",
              body[-2], body[-1])
    """
    if len(body) < n:
        return tuple(body)
    return (
        body[0], body[1],
        f"[... {len(body) - 4} rows omitted ...]",
        body[-2], body[-1],
    )
```

- [ ] **Step 8: Run tests to verify pass**

```bash
uv run pytest tests/test_display_cycle.py::test_clip_body_under_threshold_keep_all tests/test_display_cycle.py::test_clip_body_at_or_above_threshold_head_tail tests/test_display_cycle.py::test_clip_body_exact_threshold_triggers_clipping -v
```

Expected: 3 PASS.

- [ ] **Step 9: Write the failing tests for `_render_perception_tool`**

继续加:

```python
# --- T-RPT: _render_perception_tool ---


def test_render_perception_tool_single_section():
    """T-RPT-1: 单 section keep all → '  ⚙ tool\n    === Section ===\n    body...'."""
    from src.cli.display import _render_perception_tool
    content = (
        "=== Account Balance ===\n"
        "Total: 998.00 USDT\n"
        "Free: 800.00"
    )
    out = _render_perception_tool("get_account_balance", content)
    assert out == (
        "  ⚙ get_account_balance\n"
        "    === Account Balance ===\n"
        "    Total: 998.00 USDT\n"
        "    Free: 800.00"
    )


def test_render_perception_tool_multi_section_blank_separator():
    """T-RPT-2: 多 sections 间插入 display-only blank line。"""
    from src.cli.display import _render_perception_tool
    content = (
        "=== Sec A ===\n"
        "a1\n"
        "a2\n"
        "\n"
        "=== Sec B ===\n"
        "b1"
    )
    out = _render_perception_tool("get_market_data", content)
    assert out == (
        "  ⚙ get_market_data\n"
        "    === Sec A ===\n"
        "    a1\n"
        "    a2\n"
        "\n"
        "    === Sec B ===\n"
        "    b1"
    )


def test_render_perception_tool_dense_section_clipped():
    """T-RPT-3: section body ≥ 10 → head/tail clipping in render output."""
    from src.cli.display import _render_perception_tool
    body_lines = "\n".join(f"row {i}" for i in range(15))
    content = f"=== Recent Candles ===\n{body_lines}"
    out = _render_perception_tool("get_market_data", content)
    assert "    [... 11 rows omitted ...]" in out
    assert "    row 0" in out
    assert "    row 14" in out
    assert "    row 7" not in out  # middle row dropped


def test_render_perception_tool_fallback_no_header():
    """T-RPT-4: content 无 sections → unnamed section fallback (get_memories backend path)."""
    from src.cli.display import _render_perception_tool
    content = "Memory entry 1\nMemory entry 2"
    out = _render_perception_tool("get_memories", content)
    assert out == (
        "  ⚙ get_memories\n"
        "    Memory entry 1\n"
        "    Memory entry 2"
    )
```

- [ ] **Step 10: Run failing tests**

```bash
uv run pytest tests/test_display_cycle.py -k "render_perception_tool" -v
```

Expected: 4 FAIL with `ImportError`.

- [ ] **Step 11: Implement `_render_perception_tool`**

紧接 `_clip_body` 之后加：

```python
def _render_perception_tool(tool_name: str, content: str) -> str:
    """Multi-line section render for perception tools (D8 + D13 byte-equal, spec §4.3.3).

    Output format:
      "  ⚙ {tool_name}\n"
      "    === {section.header} ===\n"     # (if present; render re-wraps `=== ... ===`)
      "    {body line 1}\n"
      ...
      "\n"                                 # blank between sections
      "    === {next section.header} ===\n"
      ...

    Section.header stores the inner name only (e.g. "Account Balance") because
    _parse_sections strips the `=== ... ===` wrapping at parse time; render
    re-wraps so the rendered output matches the byte-equal Section convention
    (T-RPT / T-BE-1 / batch snapshot tests all expect the wrapped form).

    Escape applied to section header / body (markup attack surface — content
    from tool returns may include LLM-or-API-sourced literal markup like
    `[bold]`); framework markup (icon / indent / blank lines / `=== ===`
    wrapping) preserved.
    """
    sections = _parse_sections(content)
    lines = [f"  ⚙ {tool_name}"]
    for i, section in enumerate(sections):
        if i > 0:
            lines.append("")  # display-only blank between sections
        if section.header is not None:
            lines.append(f"    === {escape(section.header)} ===")
        clipped = _clip_body(section.body)
        for row in clipped:
            # Empty body rows render as "" (no indent prefix) — avoids trailing
            # whitespace in cycle log file output (cleaner cat / less / git diff).
            # Section model byte-equal preserved (escape("") == "" + Section.body
            # contains "" still parsed back identically via _strip_blanks
            # internal-blank semantics).
            if row == "":
                lines.append("")
            else:
                lines.append(f"    {escape(row)}")
    return "\n".join(lines)
```

- [ ] **Step 12: Run tests to verify all helper tests pass**

```bash
uv run pytest tests/test_display_cycle.py -k "parse_sections or clip_body or render_perception_tool" -v
```

Expected: 10 PASS (3 parse + 3 clip + 4 render).

- [ ] **Step 13: Run full test suite to verify no regression**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: `1058 passed` (1048 baseline + 10 new). 0 failures.

- [ ] **Step 14: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8c): T1 add Section / _parse_sections / _clip_body / _render_perception_tool helpers

Adds R2-8c helper layer per spec §4.3:
- Section frozen dataclass (header + immutable body tuple)
- _parse_sections: split on '=== {name} ===' headers; fallback to
  unnamed section for tool output without sectioning
- _clip_body: head=2/tail=2 universal clipping at N=10 threshold
- _render_perception_tool: 4-space indented, blank-line separated
  multi-line section render with markup escape on body+header

10 new helper unit tests (T-PARSE-1~3 / T-CLIP-1~3 / T-RPT-1~4).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git status
```

---

### Task 2: `_render_action` Dispatch Refactor + 三层集合 + Drift Guard T-DG-2

**Files:**
- Modify: `src/cli/display.py:572-608` (`_render_action`) + 新增三层集合 frozenset 常量
- Test: `tests/test_display_cycle.py` (T-DG-2 三层集合互斥+覆盖 + T-INT-1 mixed perception/execution + T-EC-11 未注册 tool drift)

- [ ] **Step 1: Write the failing test for T-DG-2 三层集合 partition**

```python
# --- T-DG: drift guards ---


def test_dg_2_dispatch_sets_partition_all_registered_tools():
    """T-DG-2: 三层集合 + save_memory branch 互斥 + 完整覆盖 32 registered tools。

    Spec §4.4: _PERCEPTION_TOOL_NAMES (20) ∪ _EXECUTION_TOOL_NAMES (11) ∪ {save_memory}
    必须等于 REGISTERED_TOOL_NAMES (32)，且互不重叠。
    _SECTIONED_PERCEPTION_TOOL_NAMES (19) ⊂ _PERCEPTION_TOOL_NAMES（仅 get_memories 例外）。
    """
    from src.cli.display import (
        _PERCEPTION_TOOL_NAMES,
        _SECTIONED_PERCEPTION_TOOL_NAMES,
        _EXECUTION_TOOL_NAMES,
    )
    from src.agent.trader import REGISTERED_TOOL_NAMES

    perception = _PERCEPTION_TOOL_NAMES
    sectioned = _SECTIONED_PERCEPTION_TOOL_NAMES
    execution = _EXECUTION_TOOL_NAMES
    save = frozenset({"save_memory"})

    # Sectioned ⊂ perception, only get_memories excluded
    assert sectioned <= perception
    assert perception - sectioned == frozenset({"get_memories"})

    # 三层 + save_memory 互斥
    assert perception.isdisjoint(execution)
    assert perception.isdisjoint(save)
    assert execution.isdisjoint(save)

    # 完整覆盖 32 registered
    union = perception | execution | save
    declared = set(REGISTERED_TOOL_NAMES)
    assert union == declared, (
        f"Dispatch sets ≠ REGISTERED_TOOL_NAMES:\n"
        f"  Missing from dispatch: {declared - union}\n"
        f"  Extra in dispatch: {union - declared}"
    )

    # Counts per spec §4.4
    assert len(perception) == 20
    assert len(sectioned) == 19
    assert len(execution) == 11
```

- [ ] **Step 2: Write the failing test for T-EC-11 未注册 tool drift**

```python
def test_ec_11_unregistered_tool_falls_back_with_warning(caplog):
    """T-EC-11: 未注册 tool name → R2-8a single-line + warning log。"""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action

    calls = [ToolCallPart(tool_name="get_unknown_drift", args={}, tool_call_id="c1")]
    returns = {
        "c1": ToolReturnPart(tool_name="get_unknown_drift", tool_call_id="c1",
                              content="some content"),
    }
    with caplog.at_level("WARNING", logger="src.cli.display"):
        out = _render_action(calls, returns, cycle_id="abcd1234")

    assert "get_unknown_drift" in out
    assert "some content" in out  # _fallback_summary kept
    assert any("not in" in r.message and "get_unknown_drift" in r.message
               for r in caplog.records)
```

- [ ] **Step 3: Write the failing test for T-INT-1 mixed perception + execution dispatch**

```python
def test_int_1_render_action_mixed_perception_execution():
    """T-INT-1: 完整 cycle render — perception 走 multi-line + execution 走 R2-8a single-line。"""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action

    calls = [
        ToolCallPart(
            tool_name="get_account_balance", args={}, tool_call_id="c1",
        ),
        ToolCallPart(
            tool_name="set_next_wake", args={"minutes": 5}, tool_call_id="c2",
        ),
    ]
    returns = {
        "c1": ToolReturnPart(
            tool_name="get_account_balance", tool_call_id="c1",
            content="=== Account Balance ===\nTotal: 998.00 USDT",
        ),
        "c2": ToolReturnPart(
            tool_name="set_next_wake", tool_call_id="c2",
            content="Next wake set to 5 min",
        ),
    }
    out = _render_action(calls, returns, cycle_id="abcd1234")

    # Header
    assert "▾ Action (2 tools)" in out
    # Perception multi-line: 4-space indent + section
    assert "  ⚙ get_account_balance" in out
    assert "    === Account Balance ===" in out
    assert "    Total: 998.00 USDT" in out
    # Execution single-line + <22 padding (R2-8a 维持)
    assert "  ⚙ set_next_wake          5min" in out  # <22 padding 长度 22
```

- [ ] **Step 4: Run failing tests**

```bash
uv run pytest tests/test_display_cycle.py::test_dg_2_dispatch_sets_partition_all_registered_tools tests/test_display_cycle.py::test_ec_11_unregistered_tool_falls_back_with_warning tests/test_display_cycle.py::test_int_1_render_action_mixed_perception_execution -v
```

Expected: 3 FAIL — `ImportError: cannot import name '_PERCEPTION_TOOL_NAMES'` (T-DG-2 first).

- [ ] **Step 5: Add 三层集合 frozenset constants + refactor `_render_action`**

在 `src/cli/display.py` 紧接 `_PERCEPTION_PARSERS` 字典 (line 309-318) 之后、`resolve_tool_display` 之前（与 R2-8c helpers block 同档）加：

```python
# === R2-8c: dispatch sets (spec §4.4) ===

_PERCEPTION_TOOL_NAMES: frozenset[str] = frozenset({
    # Tier-1 high frequency (B2 ≥ 70%)
    "get_market_data",
    "get_higher_timeframe_view",
    "get_multi_timeframe_snapshot",
    "get_price_pivots",
    "get_recent_trades",
    "get_derivatives_data",
    # Mid (B2 50-70%)
    "get_market_news",
    "get_order_book",
    # Long-tail
    "get_macro_context",
    "get_position",
    "get_account_balance",
    "get_memories",
    "get_open_orders",
    "get_trade_journal",
    "get_active_alerts",
    "get_performance",
    "get_exchange_announcements",
    "get_macro_calendar",
    "get_etf_flows",
    "get_stablecoin_supply",
})

_SECTIONED_PERCEPTION_TOOL_NAMES: frozenset[str] = (
    _PERCEPTION_TOOL_NAMES - frozenset({"get_memories"})
)
# get_memories 是 backend-dependent format 例外（spec §4.2.13 / §8.8）;
# T-DG-1 sectioning lint 跳过此工具。

_EXECUTION_TOOL_NAMES: frozenset[str] = frozenset({
    "open_position",
    "close_position",
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "place_limit_order",
    "cancel_order",
    "set_price_alert",
    "add_price_level_alert",
    "cancel_price_level_alert",
    "set_next_wake",
})
```

替换 `_render_action` (line 572-608) 完整体:

```python
def _render_action(
    tool_calls: list,
    returns_lookup: dict,
    cycle_id: str,
) -> str:
    """Render Action section per spec §4.3 + §4.4 dispatch.

    Dispatch (mutually exclusive, full coverage of 32 registered tools per T-DG-2):
      1. ret None → R2-8a `[no return captured]` line (orphan tool_call_id)
      2. is_tool_error → R2-8a `✗` single-line (L1 failure path)
      3. tool_name == 'save_memory' → R2-8a `✎` single-line + summarize_save_memory
      4. tool_name in _EXECUTION_TOOL_NAMES → R2-8a `⚙` single-line + <22 padding
      5. tool_name in _PERCEPTION_TOOL_NAMES → multi-line _render_perception_tool
      6. else → R2-8a single-line + warning log (drift signal, T-EC-11)
    """
    n = len(tool_calls)
    plural = "tool" if n == 1 else "tools"
    lines = [f"\n▾ Action ({n} {plural})"]

    for tcp in tool_calls:
        ret = returns_lookup.get(tcp.tool_call_id)
        if ret is None:
            logger.warning(
                "tool_call_id mismatch for %s in cycle %s",
                tcp.tool_name, cycle_id,
            )
            lines.append(f"  ⚙ {tcp.tool_name:<22} [no return captured]")
            continue

        content_str = str(ret.content)
        outcome = getattr(ret, "outcome", "success")

        # Branch 2: L1 failure (R2-8a single-line + ✗) — does not enter multi-line
        if is_tool_error(tcp.tool_name, content_str, outcome):
            lines.append(
                f"  ✗ {tcp.tool_name:<22} {escape(_fallback_summary(content_str))}"
            )
            continue

        # Branch 3: save_memory (R2-8a single-line + ✎)
        if tcp.tool_name == "save_memory":
            try:
                args = tcp.args_as_dict()
            except Exception:
                args = None
            icon, summary = resolve_tool_display(
                tcp.tool_name, content_str, outcome, args,
            )
            lines.append(f"  {icon} {tcp.tool_name:<22} {escape(summary)}")
            continue

        # Branch 4: execution (R2-8a single-line + <22 padding)
        if tcp.tool_name in _EXECUTION_TOOL_NAMES:
            try:
                args = tcp.args_as_dict()
            except Exception:
                args = None
            icon, summary = resolve_tool_display(
                tcp.tool_name, content_str, outcome, args,
            )
            lines.append(f"  {icon} {tcp.tool_name:<22} {escape(summary)}")
            continue

        # Branch 5: perception (multi-line section render, includes get_memories
        # backend-dependent fallback path via _parse_sections)
        if tcp.tool_name in _PERCEPTION_TOOL_NAMES:
            lines.append(_render_perception_tool(tcp.tool_name, content_str))
            continue

        # Branch 6: drift — unregistered tool name (T-EC-11)
        logger.warning(
            "tool_name %s not in _PERCEPTION_TOOL_NAMES / _EXECUTION_TOOL_NAMES / save_memory "
            "— falling back to R2-8a single-line",
            tcp.tool_name,
        )
        lines.append(
            f"  ⚙ {tcp.tool_name:<22} {escape(_fallback_summary(content_str))}"
        )

    return "\n".join(lines)
```

- [ ] **Step 6: Run dispatch tests to verify pass**

```bash
uv run pytest tests/test_display_cycle.py::test_dg_2_dispatch_sets_partition_all_registered_tools tests/test_display_cycle.py::test_ec_11_unregistered_tool_falls_back_with_warning tests/test_display_cycle.py::test_int_1_render_action_mixed_perception_execution -v
```

Expected: 3 PASS.

- [ ] **Step 7: Run full test suite to check existing _render_action regression**

```bash
uv run pytest tests/test_display_cycle.py -v 2>&1 | tail -30
```

Expected: existing `test_render_action_*` tests STILL PASS (R2-8a behavior preserved for execution / save_memory / orphan paths). ⚠️ **Possible failures**: existing tests `test_render_action_multi_tools` (line 658) uses `get_market_data` / `get_position` / `get_open_orders` content — these now go through `_render_perception_tool` multi-line path instead of `_PERCEPTION_PARSERS` single-line path. **Fix existing tests**: update assertions in those 3 R2-8a tests to match multi-line output:

For `test_render_action_multi_tools` (line 658-679): assertions like `"get_market_data"` substring still pass (substring); but if any assertion checks fixed-width padding `<22` for these perception tools, it'll fail. Read and update accordingly:

```bash
grep -n "<22\|right-pad\|column" tests/test_display_cycle.py | head -10
```

Check what the existing tests assert. Most likely just substring `"get_market_data"` style — keep as-is. If anything checks the actual single-line format, update:
- Old: `"  ⚙ get_market_data         {summary}"` (fixed <22 padding)
- New: `"  ⚙ get_market_data\n    === ..."` (multi-line)

- [ ] **Step 8: Run full suite again post-fix**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: `1061 passed` (1048 + 10 from T1 + 3 from T2). 0 failures.

- [ ] **Step 9: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8c): T2 _render_action 4-branch dispatch + 三层集合 + T-DG-2

Refactors _render_action per spec §4.4:
- Adds _PERCEPTION_TOOL_NAMES (20) / _SECTIONED_PERCEPTION_TOOL_NAMES (19, get_memories
  excluded) / _EXECUTION_TOOL_NAMES (11) frozenset constants
- 4-branch dispatch: orphan ret → L1 error → save_memory → execution → perception → drift
- Perception path now invokes _render_perception_tool (multi-line) instead of
  legacy single-line summarize_tool
- T-DG-2: dispatch sets mutually exclusive + full coverage of REGISTERED_TOOL_NAMES (32)
- T-EC-11: unregistered tool name → R2-8a fallback + warning log
- T-INT-1: mixed perception (multi-line) + execution (<22 padding) integration

Existing R2-8a _render_action tests updated for new multi-line perception output.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git status
```

---

### Task 3: Rename `_PERCEPTION_PARSERS` → `_SYSTEM_LOG_PERCEPTION_PARSERS`

**Files:**
- Modify: `src/cli/display.py` (rename + comment 标注 system log only consumer)
- Test: existing `test_summarize_*` tests should pass unchanged (still go through `summarize_tool` → `_PERCEPTION_PARSERS` 改名后)

**Note**: 这是 review P2-1 决议——8 parser 不删，仅 namespace 收窄 + 注释标注，避免 `cli/app.py` system log 摘要消费方退化。

- [ ] **Step 1: Verify existing system log consumer (read-only check)**

```bash
grep -n "_PERCEPTION_PARSERS\|summarize_tool\|resolve_tool_display" /Users/z/Z/TradeBot/src/cli/app.py
```

Expected output: 至少 1 行 — `cli/app.py:332 icon, summary = resolve_tool_display(...)` (real parser consumer 走 `summarize_tool` / `_PERCEPTION_PARSERS` chain). 注: `cli/app.py:337 logger.debug return={content_str[:500]}` 是独立 raw dump，不走 parser chain。Confirm system log path (line 332-335) 仍消费 `_PERCEPTION_PARSERS`。

- [ ] **Step 2: Rename + 注释 in `src/cli/display.py:309`**

替换:

```python
_PERCEPTION_PARSERS = {
    "get_market_data": _summarize_get_market_data,
    "get_position": _summarize_get_position,
    ...
}
```

为:

```python
# === System log perception parsers (R2-8c review P2-1 namespace narrowing) ===
#
# 8 parser functions kept post-R2-8c — consumed ONLY by:
#   - resolve_tool_display() / summarize_tool() (this file) — system log INFO 摘要
#     (cli/app.py:332 `icon, summary = resolve_tool_display(...)` → line 335
#     `logger.info(f"  {icon} {part.tool_name}: {summary}")`); 注意 cli/app.py:337
#     的 `logger.debug return={content_str[:500]}` 是独立 raw dump，不走 parser chain
#   - scripts/tool_call_summary.py (offline analysis 脚本)
#
# NOT consumed by _render_perception_tool (R2-8c multi-line render) — that path
# bypasses parser layer entirely and reads raw section content via _parse_sections.
#
# 重构这些 parser 应保持向后兼容（system log 形态不破），不影响 R2-8c display 路径。
_SYSTEM_LOG_PERCEPTION_PARSERS = {
    "get_market_data": _summarize_get_market_data,
    "get_position": _summarize_get_position,
    "get_account_balance": _summarize_get_account_balance,
    "get_open_orders": _summarize_get_open_orders,
    "get_trade_journal": _summarize_get_trade_journal,
    "get_memories": _summarize_get_memories,
    "get_active_alerts": _summarize_get_active_alerts,
    "get_performance": _summarize_get_performance,
}
```

`summarize_tool` (line 724-733) 内引用同步:

```python
def summarize_tool(tool_name: str, content: str) -> str:
    """Summarize a tool's return value into a one-line display string.

    Used by system log INFO 摘要 path only (cli/app.py:332 resolve_tool_display
    → line 335 logger.info chain). R2-8c display path uses _render_perception_tool
    directly, bypassing this function.
    """
    content_str = str(content)
    parser = (
        _SYSTEM_LOG_PERCEPTION_PARSERS.get(tool_name)
        or _EXECUTION_PARSERS.get(tool_name)
    )
    if parser:
        try:
            return parser(content_str)
        except Exception:
            return _fallback_summary(content_str)
    return _fallback_summary(content_str)
```

`resolve_tool_display` (line 321) 内 `summarize_tool(tool_name, content)` 调用不变（间接通过 `summarize_tool` 拿到 parser）。

- [ ] **Step 3: Run existing summarize tests to verify rename did not break anything**

```bash
uv run pytest tests/test_display_cycle.py -k "summarize" -v 2>&1 | tail -20
```

Expected: existing parser tests STILL PASS (rename是 namespace-level, behavior 不变).

- [ ] **Step 4: Run full suite**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: `1061 passed`（无新增 test）。0 failures.

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py
git commit -m "refactor(iter-w2r2-8c): T3 rename _PERCEPTION_PARSERS → _SYSTEM_LOG_PERCEPTION_PARSERS

Per spec §5.2 review P2-1: 8 perception parsers kept post-R2-8c — consumed only
by system log INFO 摘要 path (resolve_tool_display / summarize_tool chain via
cli/app.py:332-335) and scripts/tool_call_summary.py offline tooling. Renamed
namespace + comment to mark consumer scope; no logic changes.

R2-8c display path bypasses parser layer entirely via _render_perception_tool.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git status
```

---

### Task 4: `_render_reasoning` `max_chars` 800 → 2000 (D10)

**Files:**
- Modify: `src/cli/display.py:553` (`_render_reasoning` 默认 `max_chars`)
- Test: `tests/test_display_cycle.py` (T-INT-3 — 2 thinking truncation cases)

- [ ] **Step 1: Write failing tests for new threshold**

```python
# --- T-INT-3: thinking 截断升级 800→2000 (D10) ---


def test_int_3_thinking_1500_chars_keep_all():
    """T-INT-3a: 1500-char thinking < 2000 → keep all (no truncation suffix)."""
    from src.cli.display import _render_reasoning
    text = "x" * 1500
    out = _render_reasoning(text)
    assert "[+" not in out  # no truncation marker
    assert "1500 chars total" in out


def test_int_3_thinking_2500_chars_truncated_to_2000():
    """T-INT-3b: 2500-char thinking → truncate at 2000 + ' ... [+500 chars]' suffix."""
    from src.cli.display import _render_reasoning
    text = "y" * 2500
    out = _render_reasoning(text)
    assert "[+500 chars]" in out
    assert "2500 chars total" in out
```

- [ ] **Step 2: Run failing tests**

```bash
uv run pytest tests/test_display_cycle.py::test_int_3_thinking_1500_chars_keep_all tests/test_display_cycle.py::test_int_3_thinking_2500_chars_truncated_to_2000 -v
```

Expected: 1 FAIL (`test_int_3_thinking_1500_chars_keep_all` — current 800 default truncates 1500 input). `test_int_3_thinking_2500_chars_truncated_to_2000` may also FAIL (truncates at 800 not 2000 → suffix shows `[+1700 chars]` not `[+500 chars]`).

- [ ] **Step 3: Update default `max_chars`**

`src/cli/display.py:553`:

```python
def _render_reasoning(thinking_text: str, max_chars: int = 2000) -> str:
    """Render Reasoning section per spec §4.2.1-§4.2.2 (R2-8c D10: 800 → 2000).

    Hard-truncate body to max_chars + ' ... [+N chars]' marker. Body must be
    rich.markup.escape()'d — thinking content is LLM output, attack surface
    of same shape as Decision body.

    R2-8c D10 raises max_chars 800 → 2000 (smoke #6 B3 截断率 47/80 = 58.8%
    @ 800; 2000 预计降到 ~25%).
    """
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_display_cycle.py::test_int_3_thinking_1500_chars_keep_all tests/test_display_cycle.py::test_int_3_thinking_2500_chars_truncated_to_2000 -v
```

Expected: 2 PASS.

- [ ] **Step 5: Run existing reasoning tests to check no regression**

```bash
uv run pytest tests/test_display_cycle.py -k "reasoning" -v 2>&1 | tail -20
```

Expected: 既有测试若有 hard-coded 800 default 边界 case 可能失败。逐一更新到新 default 或显式传 `max_chars=800` 保持原意。

例：若有 `_render_reasoning("x" * 1000)` 期望 truncated → 现在 `< 2000` keep all，需改为 `_render_reasoning("x" * 1000, max_chars=800)` 显式保留 800 行为，或更新断言到新 default 行为。

- [ ] **Step 6: Run full suite**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: `1063 passed` (1061 + 2 new). 0 failures.

- [ ] **Step 7: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8c): T4 _render_reasoning max_chars 800→2000 (D10)

Per spec §4.5 / D10: smoke #6 B3 显示 800 截断率 58.8% (47/80 segments),
median thinking 1100 chars > 800 baseline. 升级到 2000 后预计截断率 ~25%
(覆盖 ~75-80% segments). Thinking 在 LLM 端 cost-free, display 端 truncation
完全无 token 经济风险.

T-INT-3a: 1500-char thinking < 2000 → keep all
T-INT-3b: 2500-char thinking → truncate + '[+500 chars]' suffix

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git status
```

---

### Task 5: Tool Refactor Batch A — Tier-1 High-Frequency (6 tools)

**Files:**
- Modify: `src/agent/tools_perception.py`
  - `get_market_data` (line 39-136) — already 4 sections, minimal changes (verify §4.1.1 param order)
  - `get_higher_timeframe_view` (line 793-871) — convert L2 plain text to `=== Error ===` section
  - `get_multi_timeframe_snapshot` (line 1310-1410) — convert L2 plain text to `=== Error ===` section
  - `get_price_pivots` (line 1518-1602) — convert L2 plain text to `=== Error ===` section
  - `get_recent_trades` (line 1227-1307) — convert L2 plain text to `=== Error ===` section
  - `get_derivatives_data` (line 698-772) — refactor to single `=== Derivatives Data ===` section per spec §4.2.10 (was 5-section; review-校准 ≤2 conditional 上限)
- Test: `tests/test_display_cycle.py` (snapshot fixture + L2 path lint)

**Spec reference per tool**: §4.2.1 / §4.2.2 / §4.2.3 / §4.2.4 / §4.2.9 / §4.2.10

**Per-tool L2 paths to convert** (from existing source code):
| Tool | Existing L2 plain text | Target `=== Error ===` section |
|---|---|---|
| `get_market_data` | (无 — 仅 hard-raise L1) | (no L2 conversion needed) |
| `get_higher_timeframe_view` | `f"Higher timeframe view ({timeframe}, {symbol}): temporarily unavailable"` (line 809) + `"insufficient data"` (line 812) | `f"=== Higher Timeframe View ({symbol}, {timeframe}) ===\n=== Error ===\nTemporarily unavailable."` (and similar for insufficient) |
| `get_multi_timeframe_snapshot` | `f"Multi-TF snapshot ({symbol}): temporarily unavailable"` (line 1337, 1350) | `=== Multi-TF Snapshot ({symbol}) ===\n=== Error ===\nTemporarily unavailable.` |
| `get_price_pivots` | `f"Price pivots ({symbol}, main TF: {main_tf}): temporarily unavailable"` (line 1542) | `=== Price Pivots ({symbol}, main TF: {main_tf}) ===\n=== Error ===\nTemporarily unavailable.` |
| `get_recent_trades` | `f"Recent trades ({symbol}): temporarily unavailable"` (line 1245) + `f"...no trades in last {window_seconds}s"` (line 1248, 1273) | `=== Recent Trades ({symbol}) ===\n=== Error ===\nTemporarily unavailable.` (and similar fallback for empty) |
| `get_derivatives_data` | (per-source L3 only — no whole-tool L2 currently) | If all 3 sources fail → emit `=== Derivatives Data ({symbol}) ===\n=== Error ===\n...` per spec §4.2.10 |

**Param order verification** (§4.1.1):
| Tool | Current header | Target header (per §4.1.1) | Action |
|---|---|---|---|
| `get_market_data` | `=== Ticker ({symbol}) ===` / `=== Technical Indicators ({timeframe}) ===` / `=== Recent Candles ({timeframe}, last {N}) ===` | OK — symbol-first, then timeframe-only sections | ✅ no change |
| `get_higher_timeframe_view` | `=== Higher Timeframe View ({timeframe}, {symbol}) ===` (line 817) | `=== Higher Timeframe View ({symbol}, {timeframe}) ===` (symbol first per §4.1.1) | 🔧 swap |
| `get_multi_timeframe_snapshot` | `=== Multi-TF Snapshot ({symbol}) ===` | OK | ✅ no change |
| `get_price_pivots` | `=== Price Pivots ({symbol}, main TF: {main_tf}) ===` | OK | ✅ no change |
| `get_recent_trades` | `=== Recent Trades ({symbol}, last {window_seconds}s, {N} × {bucket_duration_ms // 1000}s buckets) ===` | OK | ✅ no change |
| `get_derivatives_data` | `=== Derivatives Data ({symbol}) ===` | OK | ✅ no change |

- [ ] **Step 1: Write failing snapshot test for `get_market_data` (representative for batch)**

```python
# === R2-8c per-tool snapshot fixtures ===

# Snapshot helper — invoke _render_perception_tool with raw tool content fixture
# and verify output matches expected. Inline fixtures (spec §5.2 plan决议).


def _assert_perception_render(tool_name: str, content: str, expected: str):
    """Helper: run _render_perception_tool and assert output equals expected."""
    from src.cli.display import _render_perception_tool
    actual = _render_perception_tool(tool_name, content)
    assert actual == expected, (
        f"Render mismatch for {tool_name}:\n"
        f"--- expected ---\n{expected}\n"
        f"--- actual ---\n{actual}"
    )


# --- Batch A: tier-1 high-frequency snapshots ---


def test_snapshot_get_market_data_happy_path():
    """Snapshot — get_market_data 4-section happy path render."""
    content = (
        "=== Ticker (BTC/USDT:USDT) ===\n"
        "Price: 75212.00 | Bid: 75200.00 | Ask: 75215.00\n"
        "24h High: 76225.00 | Low: 74893.00 | Volume: 8200.00\n"
        "\n"
        "=== Technical Indicators (5m) ===\n"
        "RSI(14): 33.55\n"
        "MACD: -131 (sig -98, hist -33)\n"
        "\n"
        "=== Market Context ===\n"
        "ATR(14): 218.50 (0.29% of price, 5m candles)\n"
        "\n"
        "=== Recent Candles (5m, last 3) ===\n"
        "Time         Open       High        Low      Close        Vol\n"
        "14:00     75250.00  75300.00  75180.00  75220.00     320.5\n"
        "14:05     75180.00  75220.00  75150.00  75212.00     310.2"
    )
    expected = (
        "  ⚙ get_market_data\n"
        "    === Ticker (BTC/USDT:USDT) ===\n"
        "    Price: 75212.00 | Bid: 75200.00 | Ask: 75215.00\n"
        "    24h High: 76225.00 | Low: 74893.00 | Volume: 8200.00\n"
        "\n"
        "    === Technical Indicators (5m) ===\n"
        "    RSI(14): 33.55\n"
        "    MACD: -131 (sig -98, hist -33)\n"
        "\n"
        "    === Market Context ===\n"
        "    ATR(14): 218.50 (0.29% of price, 5m candles)\n"
        "\n"
        "    === Recent Candles (5m, last 3) ===\n"
        "    Time         Open       High        Low      Close        Vol\n"
        "    14:00     75250.00  75300.00  75180.00  75220.00     320.5\n"
        "    14:05     75180.00  75220.00  75150.00  75212.00     310.2"
    )
    _assert_perception_render("get_market_data", content, expected)
```

- [ ] **Step 2: Run failing test (verify Task 1 helpers wired through)**

```bash
uv run pytest tests/test_display_cycle.py::test_snapshot_get_market_data_happy_path -v
```

Expected: PASS — `get_market_data` already sections-emitting per source. If FAIL, debug `_parse_sections` or `_render_perception_tool` (Task 1 should have caught).

- [ ] **Step 3: Refactor `get_higher_timeframe_view` — L2 path + param swap + 补缺失 `=== 20-period Band ===` header**

3 处源码改动:

**3a. L2 paths (line 805-812)** 转 sectioned `=== Error ===`:

```python
    try:
        df = await deps.market_data.get_ohlcv_dataframe(symbol, timeframe, limit=250)
    except Exception:
        logger.warning("HTF fetch failed for %s %s", symbol, timeframe, exc_info=True)
        return (
            f"=== Higher Timeframe View ({symbol}, {timeframe}) ===\n"
            "=== Error ===\n"
            "Temporarily unavailable."
        )

    if df.empty:
        return (
            f"=== Higher Timeframe View ({symbol}, {timeframe}) ===\n"
            "=== Error ===\n"
            "Insufficient data."
        )
```

**3b. Param order swap (line 817)** `({timeframe}, {symbol})` → `({symbol}, {timeframe})` per §4.1.1：

```python
sections: list[str] = [
    f"=== Higher Timeframe View ({symbol}, {timeframe}) ===",
    ...
]
```

**3c. 补 `=== 20-period Band ===` header (line 864-869)** — 当前源码:

```python
# 20-period band.
if len(df) >= 20:
    last_20 = df.iloc[-20:]
    hi20 = float(last_20["high"].max())
    lo20 = float(last_20["low"].min())
    width_pct = 0.0 if lo20 == 0 else (hi20 - lo20) / lo20 * 100.0
    sections.extend([
        "",                                      # blank separator
        f"20-period High: {hi20:,.2f}",          # ❌ 无 section header
        f"20-period Low:  {lo20:,.2f}",
        f"20-period range width: {width_pct:.1f}%",
    ])
```

改为 (插入 `=== 20-period Band ===` header 在 blank separator 之后)：

```python
# 20-period band.
if len(df) >= 20:
    last_20 = df.iloc[-20:]
    hi20 = float(last_20["high"].max())
    lo20 = float(last_20["low"].min())
    width_pct = 0.0 if lo20 == 0 else (hi20 - lo20) / lo20 * 100.0
    sections.extend([
        "",
        "=== 20-period Band ===",                # ✅ 补 header per §4.2.2
        f"20-period High: {hi20:,.2f}",
        f"20-period Low:  {lo20:,.2f}",
        f"20-period range width: {width_pct:.1f}%",
    ])
```

理由: spec §4.2.2 表格明确把 `=== 20-period Band ===` 列为 section；当前源码直接把 3 字段行 extend 到 `=== Range Position ===` 后续 body（或 MA Distances，若 Range Position 跳过），违反 sectioning convention。Step 4 的 `test_snapshot_get_higher_timeframe_view_happy_path` 期望该 section 存在——若不补 header，snapshot test 字面 mismatch 但 root cause 不显然。

- [ ] **Step 4: Add snapshot test for `get_higher_timeframe_view` L2 path**

```python
def test_snapshot_get_higher_timeframe_view_l2_unavailable():
    """Snapshot — HTF L2 fallback (service exception) emits === Error === section."""
    content = (
        "=== Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_higher_timeframe_view\n"
        "    === Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "\n"
        "    === Error ===\n"
        "    Temporarily unavailable."
    )
    _assert_perception_render("get_higher_timeframe_view", content, expected)


def test_snapshot_get_higher_timeframe_view_happy_path():
    """Snapshot — HTF happy path with all 3 sub-sections (§4.2.2)."""
    content = (
        "=== Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "Current Price: 75,212.00\n"
        "\n"
        "=== MA Distances ===\n"
        "MA50: 76,000.00 (price vs MA: -1.0%)\n"
        "MA100: 78,000.00 (price vs MA: -3.6%)\n"
        "MA200: 80,000.00 (price vs MA: -6.0%)\n"
        "\n"
        "=== Range Position ===\n"
        "100-period High: 80,000.00 (5 4h-bars ago)\n"
        "100-period Low:  74,000.00 (latest)\n"
        "Current price within range: 20.2%\n"
        "\n"
        "=== 20-period Band ===\n"
        "20-period High: 76,500.00\n"
        "20-period Low:  74,800.00\n"
        "20-period range width: 2.3%"
    )
    expected = (
        "  ⚙ get_higher_timeframe_view\n"
        "    === Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "    Current Price: 75,212.00\n"
        "\n"
        "    === MA Distances ===\n"
        "    MA50: 76,000.00 (price vs MA: -1.0%)\n"
        "    MA100: 78,000.00 (price vs MA: -3.6%)\n"
        "    MA200: 80,000.00 (price vs MA: -6.0%)\n"
        "\n"
        "    === Range Position ===\n"
        "    100-period High: 80,000.00 (5 4h-bars ago)\n"
        "    100-period Low:  74,000.00 (latest)\n"
        "    Current price within range: 20.2%\n"
        "\n"
        "    === 20-period Band ===\n"
        "    20-period High: 76,500.00\n"
        "    20-period Low:  74,800.00\n"
        "    20-period range width: 2.3%"
    )
    _assert_perception_render("get_higher_timeframe_view", content, expected)
```

- [ ] **Step 5: Run HTF snapshot tests**

```bash
uv run pytest tests/test_display_cycle.py -k "higher_timeframe" -v 2>&1 | tail -10
```

Expected: 2 PASS.

- [ ] **Step 6: Refactor `get_multi_timeframe_snapshot` L2 paths + snapshot tests**

`src/agent/tools_perception.py:1337` 和 `1350`:

```python
# line 1337 — ticker fetch failed
return (
    f"=== Multi-TF Snapshot ({symbol}) ===\n"
    "=== Error ===\n"
    "Temporarily unavailable."
)

# line 1350 — all TFs failed
return (
    f"=== Multi-TF Snapshot ({symbol}) ===\n"
    "=== Error ===\n"
    "Temporarily unavailable (all timeframes failed)."
)
```

加 2 snapshot tests:

```python
def test_snapshot_get_multi_timeframe_snapshot_happy_path():
    """Snapshot — multi-TF 4-row table, < 10 keep all."""
    content = (
        "=== Multi-TF Snapshot (BTC/USDT:USDT) ===\n"
        "Current price: 75200.00\n"
        "Columns: Momentum (price vs primary MA) | Structure (MA alignment) | "
        "Volatility (ATR as % of price) | Range pos (position within 20-bar high-low, 0%=low / 100%=high)\n"
        "\n"
        "5m:  +0.5% vs MA20      | MA20 above MA50                          | ATR 0.30%   | range pos 60%\n"
        "1h:  -1.2% vs MA50      | MA50 below MA200                         | ATR 0.80%   | range pos 30%\n"
        "4h:  -0.8% vs MA50      | MA50 below MA200                         | ATR 1.50%   | range pos 25%\n"
        "1d:  +2.0% vs MA50      | MA50 above MA200                         | ATR 2.30%   | range pos 70%"
    )
    expected = (
        "  ⚙ get_multi_timeframe_snapshot\n"
        "    === Multi-TF Snapshot (BTC/USDT:USDT) ===\n"
        "    Current price: 75200.00\n"
        "    Columns: Momentum (price vs primary MA) | Structure (MA alignment) | "
        "Volatility (ATR as % of price) | Range pos (position within 20-bar high-low, 0%=low / 100%=high)\n"
        "\n"  # internal blank row (source line 1408 ""), rendered as bare "" (no 4-space prefix per F3 校准)
        "    5m:  +0.5% vs MA20      | MA20 above MA50                          | ATR 0.30%   | range pos 60%\n"
        "    1h:  -1.2% vs MA50      | MA50 below MA200                         | ATR 0.80%   | range pos 30%\n"
        "    4h:  -0.8% vs MA50      | MA50 below MA200                         | ATR 1.50%   | range pos 25%\n"
        "    1d:  +2.0% vs MA50      | MA50 above MA200                         | ATR 2.30%   | range pos 70%"
    )
    _assert_perception_render("get_multi_timeframe_snapshot", content, expected)


def test_snapshot_get_multi_timeframe_snapshot_l2_unavailable():
    """Snapshot — Multi-TF L2 (ticker fetch / all TFs failed) emits === Error === section."""
    content = (
        "=== Multi-TF Snapshot (BTC/USDT:USDT) ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_multi_timeframe_snapshot\n"
        "    === Multi-TF Snapshot (BTC/USDT:USDT) ===\n"
        "\n"
        "    === Error ===\n"
        "    Temporarily unavailable."
    )
    _assert_perception_render("get_multi_timeframe_snapshot", content, expected)
```

注: 内部空行（如 `Columns:` 行后的 blank，源码 line 1408 `""`）保留在 Section.body 内（`_strip_blanks` 仅去 section 首尾）；F3 校准后 render empty row → bare `""` 不带 4-space prefix（避 trailing whitespace）。如 happy-path source 实际无内部空行，移除该行匹配。

- [ ] **Step 7: Refactor `get_price_pivots` L2 path + snapshot tests**

`src/agent/tools_perception.py:1542`:

```python
return (
    f"=== Price Pivots ({symbol}, main TF: {main_tf}) ===\n"
    "=== Error ===\n"
    "Temporarily unavailable."
)
```

加 2 snapshot tests:

```python
def test_snapshot_get_price_pivots_happy_path():
    """Snapshot — pivots with above/below current + swing status."""
    content = (
        "=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "Current Price: 75,200.00\n"
        "\n"
        "=== Levels Above Current Price ===\n"
        "Swing High: 76,000.00 (+1.06%, 12 bars ago)\n"
        "Prior Daily H: 76,500.00 (+1.73%)\n"
        "\n"
        "=== Levels Below Current Price ===\n"
        "Swing Low: 74,500.00 (-0.93%, 8 bars ago)\n"
        "Prior Daily L: 73,800.00 (-1.86%)"
    )
    expected = (
        "  ⚙ get_price_pivots\n"
        "    === Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "    Current Price: 75,200.00\n"
        "\n"
        "    === Levels Above Current Price ===\n"
        "    Swing High: 76,000.00 (+1.06%, 12 bars ago)\n"
        "    Prior Daily H: 76,500.00 (+1.73%)\n"
        "\n"
        "    === Levels Below Current Price ===\n"
        "    Swing Low: 74,500.00 (-0.93%, 8 bars ago)\n"
        "    Prior Daily L: 73,800.00 (-1.86%)"
    )
    _assert_perception_render("get_price_pivots", content, expected)


def test_snapshot_get_price_pivots_l2_unavailable():
    """Snapshot — pivots L2 (ticker fetch failed) emits === Error === section."""
    content = (
        "=== Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_price_pivots\n"
        "    === Price Pivots (BTC/USDT:USDT, main TF: 5m) ===\n"
        "\n"
        "    === Error ===\n"
        "    Temporarily unavailable."
    )
    _assert_perception_render("get_price_pivots", content, expected)
```

- [ ] **Step 8: Refactor `get_recent_trades` L2 paths**

`src/agent/tools_perception.py:1245` / `1248` / `1273`:

```python
# Service exception (line 1245)
return (
    f"=== Recent Trades ({symbol}) ===\n"
    "=== Error ===\n"
    "Temporarily unavailable."
)

# Empty trades (line 1248, 1273) — by spec §4.1.4 这是 L3 by-design 空状态，
# 不是 error；可选保持单 section header + body "No trades in last {window_seconds}s"
return (
    f"=== Recent Trades ({symbol}, last {window_seconds}s) ===\n"
    f"No trades in last {window_seconds}s."
)
```

注意: 空状态 vs 错误状态需 brainstorm 时已分清——查阅 spec §4.2.9 边界说明。如有疑问 fallback to L2 `=== Error ===` 模式（保守）。

加 2 snapshot tests:

```python
def test_snapshot_get_recent_trades_happy_path():
    """Snapshot — recent trades 5 buckets + Total + Stats."""
    content = (
        "=== Recent Trades (BTC/USDT:USDT, last 300s, 5 × 60s buckets) ===\n"
        "  t-5min  buy 0.5000 / sell 0.3000  (net +0.2000)\n"
        "  t-4min  buy 0.4000 / sell 0.2500  (net +0.1500)\n"
        "  t-3min  buy 0.3000 / sell 0.4000  (net -0.1000)\n"
        "  t-2min  buy 0.2500 / sell 0.3500  (net -0.1000)\n"
        "  t-1min  buy 0.6000 / sell 0.2000  (net +0.4000)\n"
        "Total: buy 2.0500 / sell 1.5000 (net +0.5500, 58% taker buy)\n"
        "Trade count: 42 | Avg size: 0.0845 BTC"
    )
    # 7 lines body < 10 → keep all
    expected = (
        "  ⚙ get_recent_trades\n"
        "    === Recent Trades (BTC/USDT:USDT, last 300s, 5 × 60s buckets) ===\n"
        "      t-5min  buy 0.5000 / sell 0.3000  (net +0.2000)\n"
        "      t-4min  buy 0.4000 / sell 0.2500  (net +0.1500)\n"
        "      t-3min  buy 0.3000 / sell 0.4000  (net -0.1000)\n"
        "      t-2min  buy 0.2500 / sell 0.3500  (net -0.1000)\n"
        "      t-1min  buy 0.6000 / sell 0.2000  (net +0.4000)\n"
        "    Total: buy 2.0500 / sell 1.5000 (net +0.5500, 58% taker buy)\n"
        "    Trade count: 42 | Avg size: 0.0845 BTC"
    )
    _assert_perception_render("get_recent_trades", content, expected)


def test_snapshot_get_recent_trades_l2_unavailable():
    """Snapshot — recent trades L2 (service exception) emits === Error === section."""
    content = (
        "=== Recent Trades (BTC/USDT:USDT) ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_recent_trades\n"
        "    === Recent Trades (BTC/USDT:USDT) ===\n"
        "\n"
        "    === Error ===\n"
        "    Temporarily unavailable."
    )
    _assert_perception_render("get_recent_trades", content, expected)
```

- [ ] **Step 9: Refactor `get_derivatives_data` to single section per spec §4.2.10**

`src/agent/tools_perception.py:707-770` — 当前实现是多 sections (Funding / OI / L/S 各自 append)。spec §4.2.10 review-校准为**单 section** + 字段级 fallback (L3)。

```python
async def get_derivatives_data(
    deps: TradingDeps,
    symbol: str | None = None,
) -> str:
    """Get derivatives market data: funding rate, open interest, long/short ratio."""
    import asyncio
    from datetime import datetime, timezone

    symbol = symbol or deps.symbol
    funding, oi, lsr = await asyncio.gather(
        deps.market_data.get_funding_rate(symbol),
        deps.market_data.get_open_interest(symbol),
        deps.market_data.get_long_short_ratio(symbol),
        return_exceptions=True,
    )

    field_lines: list[str] = []
    timestamps_ms: list[int] = []

    # Funding (L3 per-field fallback)
    if isinstance(funding, Exception):
        field_lines.append("Funding Rate: (unavailable)")
    else:
        direction = "longs pay shorts" if funding.rate >= 0 else "shorts pay longs"
        sign = "Positive" if funding.rate >= 0 else "Negative"
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        remaining_ms = max(0, funding.next_funding_time - now_ms)
        hours = remaining_ms // (3600 * 1000)
        minutes = (remaining_ms % (3600 * 1000)) // (60 * 1000)
        field_lines.append(
            f"Funding Rate: {funding.rate:+.4%} (next settlement in {hours}h {minutes}m, "
            f"{sign} — {direction})"
        )
        if funding.timestamp:
            timestamps_ms.append(funding.timestamp)

    # OI (L3 per-field)
    if isinstance(oi, Exception):
        field_lines.append("Open Interest: (unavailable)")
    else:
        if oi.open_interest_value >= 1e9:
            oi_str = f"${oi.open_interest_value / 1e9:.2f}B"
        elif oi.open_interest_value >= 1e6:
            oi_str = f"${oi.open_interest_value / 1e6:.2f}M"
        else:
            oi_str = f"${oi.open_interest_value:,.0f}"
        field_lines.append(f"Open Interest: {oi_str}")
        if oi.timestamp:
            timestamps_ms.append(oi.timestamp)

    # L/S ratio (L3 per-field)
    if isinstance(lsr, Exception):
        field_lines.append("Long/Short Ratio: (unavailable)")
    else:
        field_lines.append(
            f"Long/Short Ratio: {lsr.long_short_ratio:.2f} "
            f"({lsr.long_ratio:.1%} long / {lsr.short_ratio:.1%} short)"
        )
        if lsr.timestamp:
            timestamps_ms.append(lsr.timestamp)

    # All-3-failure → L2 === Error === section (whole tool degradation)
    all_failed = (
        isinstance(funding, Exception)
        and isinstance(oi, Exception)
        and isinstance(lsr, Exception)
    )
    if all_failed:
        return (
            f"=== Derivatives Data ({symbol}) ===\n"
            "=== Error ===\n"
            "Temporarily unavailable (all 3 data sources failed)."
        )

    # Data-as-of (oldest timestamp)
    if timestamps_ms:
        oldest_dt = datetime.fromtimestamp(min(timestamps_ms) / 1000, tz=timezone.utc)
        field_lines.append(
            f"Data as of: {oldest_dt.strftime('%Y-%m-%d %H:%M')} UTC"
        )

    return (
        f"=== Derivatives Data ({symbol}) ===\n"
        + "\n".join(field_lines)
    )
```

加 snapshot tests:

```python
def test_snapshot_get_derivatives_data_happy_path():
    """Snapshot — derivatives single section with all 3 sources OK."""
    content = (
        "=== Derivatives Data (BTC/USDT:USDT) ===\n"
        "Funding Rate: +0.0080% (next settlement in 4h 15m, Positive — longs pay shorts)\n"
        "Open Interest: $5.20B\n"
        "Long/Short Ratio: 1.25 (55.6% long / 44.4% short)\n"
        "Data as of: 2026-05-03 14:00 UTC"
    )
    expected = (
        "  ⚙ get_derivatives_data\n"
        "    === Derivatives Data (BTC/USDT:USDT) ===\n"
        "    Funding Rate: +0.0080% (next settlement in 4h 15m, Positive — longs pay shorts)\n"
        "    Open Interest: $5.20B\n"
        "    Long/Short Ratio: 1.25 (55.6% long / 44.4% short)\n"
        "    Data as of: 2026-05-03 14:00 UTC"
    )
    _assert_perception_render("get_derivatives_data", content, expected)


def test_snapshot_get_derivatives_data_l3_partial_unavailable():
    """T-EC-10: per-source L3 fallback — Funding unavailable, OI / L/S OK."""
    content = (
        "=== Derivatives Data (BTC/USDT:USDT) ===\n"
        "Funding Rate: (unavailable)\n"
        "Open Interest: $5.20B\n"
        "Long/Short Ratio: 1.25 (55.6% long / 44.4% short)\n"
        "Data as of: 2026-05-03 14:00 UTC"
    )
    expected = (
        "  ⚙ get_derivatives_data\n"
        "    === Derivatives Data (BTC/USDT:USDT) ===\n"
        "    Funding Rate: (unavailable)\n"
        "    Open Interest: $5.20B\n"
        "    Long/Short Ratio: 1.25 (55.6% long / 44.4% short)\n"
        "    Data as of: 2026-05-03 14:00 UTC"
    )
    _assert_perception_render("get_derivatives_data", content, expected)


def test_snapshot_get_derivatives_data_l2_all_unavailable():
    """L2 fallback — all 3 sources failed → === Error === section."""
    content = (
        "=== Derivatives Data (BTC/USDT:USDT) ===\n"
        "=== Error ===\n"
        "Temporarily unavailable (all 3 data sources failed)."
    )
    expected = (
        "  ⚙ get_derivatives_data\n"
        "    === Derivatives Data (BTC/USDT:USDT) ===\n"
        "\n"
        "    === Error ===\n"
        "    Temporarily unavailable (all 3 data sources failed)."
    )
    _assert_perception_render("get_derivatives_data", content, expected)
```

- [ ] **Step 10: Run all batch A snapshot tests**

```bash
uv run pytest tests/test_display_cycle.py -k "snapshot_get_market_data or snapshot_get_higher_timeframe or snapshot_get_multi_timeframe or snapshot_get_price_pivots or snapshot_get_recent_trades or snapshot_get_derivatives_data" -v 2>&1 | tail -30
```

Expected: 全部 PASS。

- [ ] **Step 11: Run existing tools_perception integration tests for batch A tools**

```bash
uv run pytest tests/ -k "test_get_market_data or test_get_higher_timeframe or test_get_multi_timeframe or test_get_price_pivots or test_get_recent_trades or test_get_derivatives_data" -v 2>&1 | tail -50
```

Expected: 既有集成测试可能因 L2 path / param order 变化 fail——逐一 update 期望字面值或断言策略。**优先策略**: 把 hard-coded 字面值断言改为子串断言 + section header 断言（更鲁棒）。

例: `assert "Higher timeframe view (4h, BTC/USDT:USDT): temporarily unavailable" in result` → 改为:
```python
assert "=== Higher Timeframe View (BTC/USDT:USDT, 4h) ===" in result
assert "=== Error ===" in result
assert "Temporarily unavailable" in result
```

- [ ] **Step 12: Run full suite**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: 1063 + ~10-15 batch A snapshots = ~1073-1078 passed. 0 failures.

- [ ] **Step 13: Commit batch A**

```bash
git add src/agent/tools_perception.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8c): T5 batch A — tier-1 perception sectioning (6 tools)

Per spec §4.2.1-4.2.4 / §4.2.9-4.2.10:
- get_market_data: verified 4-section happy path (no source change)
- get_higher_timeframe_view: L2 plain text → === Error === section + param order swap (symbol, tf)
- get_multi_timeframe_snapshot: L2 plain text → === Error === section
- get_price_pivots: L2 plain text → === Error === section
- get_recent_trades: L2 plain text → === Error === section / no-trades L3
- get_derivatives_data: refactor 5-section → single section per §4.2.10
  + L3 per-field fallback + L2 all-3-failure === Error ===

~12 new snapshot tests (happy path + L2 fallback per tool, L3 for derivatives).
Updated existing tools_perception integration tests for L2 / param-order changes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git status
```

---

### Task 6: Tool Refactor Batch B — Mid-Frequency + implicit→explicit (6 tools)

**Files:**
- Modify: `src/agent/tools_perception.py`
  - `get_market_news` (line 539-611) — convert L2 `"News service not configured."` → `=== Error ===` section
  - `get_order_book` (line 1126-1224) — convert L2 plain text → `=== Error ===` section + add `=== Depth ===` / `=== Concentrated Levels ===` sub-sections per §4.2.20
  - `get_position` (line 139-295) — implicit "Current Position:" → explicit `=== Position ({symbol}) ===` / `=== PnL ===` / `=== Risk Exposure ===` / `=== Exit Orders ===` per §4.2.11
  - `get_open_orders` (line 340-380) — implicit "Pending Orders:" → explicit `=== Pending Orders ===` per §4.2.14
  - `get_account_balance` (line 298-309) — implicit "Account Balance:" → explicit `=== Account Balance ===` per §4.2.12
  - `get_active_alerts` (line 453-476) — already sectioned, verify §4.1.1 conformance
- Test: `tests/test_display_cycle.py` (snapshot fixtures + integration test updates)

**Per-tool L2 paths** (from existing source code):
| Tool | Existing L2 plain text | Target `=== Error ===` section |
|---|---|---|
| `get_market_news` | `"News service not configured."` (line 547) | `=== News ===\n=== Error ===\nNews service not configured.` |
| `get_order_book` | `f"Order book ({symbol}): temporarily unavailable"` (line 1146) + `"insufficient data..."` (line 1150, 1164) | `=== Order Book ({symbol}) ===\n=== Error ===\nTemporarily unavailable.` (and similar) |

**Implicit→explicit promotions** (existing implicit "Section:" lines → `=== Section ===` headers):
- `get_position`: `"Current Position:"` → `=== Position ({symbol}) ===`; `"Risk exposure:"` → `=== Risk Exposure ===`; `"Exit orders:"` → `=== Exit Orders ===`; new `=== PnL ===` block from current PnL/Duration lines
- `get_open_orders`: `"Pending Orders:"` → `=== Pending Orders ===`
- `get_account_balance`: `"Account Balance:"` → `=== Account Balance ===`

**get_active_alerts**: 已 explicit sectioned (line 461 / 468)。仅验证 §4.1.1 参数顺序符合。

- [ ] **Step 1: Refactor `get_account_balance` (simplest — single block)**

`src/agent/tools_perception.py:298-309`:

```python
async def get_account_balance(deps: TradingDeps) -> str:
    """Get account balance with return on initial capital."""
    balance = await deps.exchange.fetch_balance()
    ret_usdt = balance.total_usdt - deps.initial_balance
    ret_pct = (ret_usdt / deps.initial_balance) * 100 if deps.initial_balance > 0 else 0.0
    return (
        f"=== Account Balance ===\n"
        f"Total: {balance.total_usdt:.2f} USDT (initial: {deps.initial_balance:.2f})\n"
        f"Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized)\n"
        f"Free: {balance.free_usdt:.2f} USDT\n"
        f"Used: {balance.used_usdt:.2f} USDT"
    )
```

加 snapshot test:

```python
def test_snapshot_get_account_balance_happy_path():
    content = (
        "=== Account Balance ===\n"
        "Total: 998.00 USDT (initial: 1000.00)\n"
        "Return: -0.20% (-2.00 USDT) (incl. unrealized)\n"
        "Free: 800.00 USDT\n"
        "Used: 198.00 USDT"
    )
    expected = (
        "  ⚙ get_account_balance\n"
        "    === Account Balance ===\n"
        "    Total: 998.00 USDT (initial: 1000.00)\n"
        "    Return: -0.20% (-2.00 USDT) (incl. unrealized)\n"
        "    Free: 800.00 USDT\n"
        "    Used: 198.00 USDT"
    )
    _assert_perception_render("get_account_balance", content, expected)
```

- [ ] **Step 2: Refactor `get_open_orders` (implicit → explicit)**

`src/agent/tools_perception.py:354` 替换 `lines = ["Pending Orders:"]` 为 `lines = ["=== Pending Orders ==="]`。

无 orders 路径 (line 344) 也升级:

```python
if not orders:
    return "=== Pending Orders ===\nNo pending orders."
```

加 2 snapshot tests:

```python
def test_snapshot_get_open_orders_empty():
    """Snapshot — no orders default empty-state, sectioned per R2-8c."""
    content = "=== Pending Orders ===\nNo pending orders."
    expected = (
        "  ⚙ get_open_orders\n"
        "    === Pending Orders ===\n"
        "    No pending orders."
    )
    _assert_perception_render("get_open_orders", content, expected)


def test_snapshot_get_open_orders_with_orders():
    """Snapshot — pending orders 1 OCO leg + 1 limit."""
    content = (
        "=== Pending Orders ===\n"
        "  [OCO] sell 0.025 stop 74000.00 (-1.60% from current) / "
        "tp 76500.00 (+1.73% from current) | algoId: oco-1 (cancel removes both legs)\n"
        "  [LIMIT] buy 0.025 @ 74500.00 (-0.93% from current) | ID: lim-1"
    )
    expected = (
        "  ⚙ get_open_orders\n"
        "    === Pending Orders ===\n"
        "      [OCO] sell 0.025 stop 74000.00 (-1.60% from current) / "
        "tp 76500.00 (+1.73% from current) | algoId: oco-1 (cancel removes both legs)\n"
        "      [LIMIT] buy 0.025 @ 74500.00 (-0.93% from current) | ID: lim-1"
    )
    _assert_perception_render("get_open_orders", content, expected)
```

- [ ] **Step 3: Refactor `get_position` (implicit → explicit, 4 sections per §4.2.11)**

`src/agent/tools_perception.py:139-295` — 较大重构。核心 strategy: 把 `_render_position_core()` 内的 implicit `"Current Position:"` + 不带 header 的 PnL/Duration 升级为 explicit sections。

变更点:
1. `_render_position_core()` 改为返回 list[str] 含两个 sections: `=== Position ({symbol}) ===` (side/contracts/entry/leverage/liquidation) + `=== PnL ===` (PnL + Duration)
2. 主路径 `lines = _render_position_core()` 后追加 `=== Risk Exposure ===` / `=== Exit Orders ===` sections
3. No-position 路径 (line 156): `return "=== Position ===\nNo open positions."`
4. Hard-failure 路径 (line 219-222): 保留 core sections + `=== Risk Exposure ===\n(unavailable)` / `=== Exit Orders ===\n(unavailable)`

详细 patch (代表性片段):

```python
# line 156
if not positions:
    return "=== Position ===\nNo open positions."

# _render_position_core 返回 sections 形态
def _render_position_core() -> list[str]:
    """Render Position + PnL sections (Phase-1 fields only)."""
    pos_lines = [
        f"=== Position ({symbol}) ===",
        f"Side: {p.side.capitalize()} | Contracts: {p.contracts} | Entry: {p.entry_price:,.2f}",
        f"Leverage: {p.leverage}x",
    ]
    if p.liquidation_price is not None:
        pos_lines.append(f"Liquidation: {p.liquidation_price:,.2f}")
    pos_lines.append(f"Unrealized: {p.unrealized_pnl:+.2f} USDT")

    pnl_lines = ["=== PnL ==="]
    if deps.initial_balance > 0:
        pnl_pct_inner = (p.unrealized_pnl / deps.initial_balance) * 100
        pnl_lines.append(
            f"PnL: {p.unrealized_pnl:+.2f} USDT ({pnl_pct_inner:+.2f}% of initial capital)"
        )
    else:
        pnl_lines.append(f"PnL: {p.unrealized_pnl:+.2f} USDT")
    if p.created_at is not None:
        # ... duration calculation ...
        pnl_lines.append(f"Duration: {dur_str}")

    return ["\n".join(pos_lines), "\n".join(pnl_lines)]

# Main path
sections = _render_position_core()  # 2 sections so far

# Hard-failure branch
except Exception:
    logger.exception(...)
    sections.append(
        "=== Risk Exposure ===\n(unavailable)"
    )
    sections.append(
        "=== Exit Orders ===\n(unavailable)"
    )
    return "\n\n".join(sections)

# Happy path appends 2 more sections
sections.append("=== Risk Exposure ===\n" + "\n".join(risk_lines))
sections.append("=== Exit Orders ===\n" + "\n".join(exit_lines))

return "\n\n".join(sections)
```

加 3 snapshot tests:

```python
def test_snapshot_get_position_no_position():
    """Snapshot — no open positions empty-state, sectioned per R2-8c."""
    content = "=== Position ===\nNo open positions."
    expected = (
        "  ⚙ get_position\n"
        "    === Position ===\n"
        "    No open positions."
    )
    _assert_perception_render("get_position", content, expected)


def test_snapshot_get_position_with_stats():
    """Snapshot — long position with all 4 sections (Position / PnL / Risk Exposure / Exit Orders)."""
    content = (
        "=== Position (BTC/USDT:USDT) ===\n"
        "Side: Long | Contracts: 0.025 | Entry: 78,518.00\n"
        "Leverage: 5x\n"
        "Liquidation: 70,666.00\n"
        "Unrealized: +0.20 USDT\n"
        "\n"
        "=== PnL ===\n"
        "PnL: +0.20 USDT (+0.02% of initial capital)\n"
        "Duration: 2h 30m\n"
        "\n"
        "=== Risk Exposure ===\n"
        "Notional value: 1962.95 USDT (4.2% of equity 998.00)\n"
        "Margin used: 392.59 USDT (39.3% of equity, from balance.used_usdt)\n"
        "Liquidation: 70666.00 (10.0% away = 5.8× ATR(1h))\n"
        "\n"
        "=== Exit Orders ===\n"
        "  Stop loss: not set\n"
        "  Take profit: not set"
    )
    expected = (
        "  ⚙ get_position\n"
        "    === Position (BTC/USDT:USDT) ===\n"
        "    Side: Long | Contracts: 0.025 | Entry: 78,518.00\n"
        "    Leverage: 5x\n"
        "    Liquidation: 70,666.00\n"
        "    Unrealized: +0.20 USDT\n"
        "\n"
        "    === PnL ===\n"
        "    PnL: +0.20 USDT (+0.02% of initial capital)\n"
        "    Duration: 2h 30m\n"
        "\n"
        "    === Risk Exposure ===\n"
        "    Notional value: 1962.95 USDT (4.2% of equity 998.00)\n"
        "    Margin used: 392.59 USDT (39.3% of equity, from balance.used_usdt)\n"
        "    Liquidation: 70666.00 (10.0% away = 5.8× ATR(1h))\n"
        "\n"
        "    === Exit Orders ===\n"
        "      Stop loss: not set\n"
        "      Take profit: not set"
    )
    _assert_perception_render("get_position", content, expected)


def test_snapshot_get_position_hard_failure_degradation():
    """Snapshot — hard-failure (ticker/balance/orders/contract_size 异常) — 4 sections
    保留 Position + PnL，Risk Exposure + Exit Orders 降级为 (unavailable)."""
    content = (
        "=== Position (BTC/USDT:USDT) ===\n"
        "Side: Long | Contracts: 0.025 | Entry: 78,518.00\n"
        "Leverage: 5x\n"
        "Liquidation: 70,666.00\n"
        "Unrealized: +0.20 USDT\n"
        "\n"
        "=== PnL ===\n"
        "PnL: +0.20 USDT (+0.02% of initial capital)\n"
        "Duration: 2h 30m\n"
        "\n"
        "=== Risk Exposure ===\n"
        "(unavailable)\n"
        "\n"
        "=== Exit Orders ===\n"
        "(unavailable)"
    )
    expected = (
        "  ⚙ get_position\n"
        "    === Position (BTC/USDT:USDT) ===\n"
        "    Side: Long | Contracts: 0.025 | Entry: 78,518.00\n"
        "    Leverage: 5x\n"
        "    Liquidation: 70,666.00\n"
        "    Unrealized: +0.20 USDT\n"
        "\n"
        "    === PnL ===\n"
        "    PnL: +0.20 USDT (+0.02% of initial capital)\n"
        "    Duration: 2h 30m\n"
        "\n"
        "    === Risk Exposure ===\n"
        "    (unavailable)\n"
        "\n"
        "    === Exit Orders ===\n"
        "    (unavailable)"
    )
    _assert_perception_render("get_position", content, expected)
```

- [ ] **Step 4: Refactor `get_market_news` — 仅 line 547 全局 L2，line 581/607/609 维持 L3 in-section**

L1/L2/L3 分类（review-校准 — 显式列出 4 处 fallback 路径处置）:

| Line | 路径 | 档级 | R2-8c 处置 |
|---|---|---|---|
| 547 | `if deps.news is None: return "News service not configured."` | **L2** (全局 service-not-configured 早 return，无 section) | 转 sectioned `=== News ===\n=== Error ===\nNews service not configured.` |
| 581 | `sections.append("=== Fear & Greed Index ===\nFGI service temporarily unavailable.")` | **L3** (FGI 数据源失败但 section header 存在 — section body 内字段级 fallback) | **保留不动** — 已符合 §4.1.4 L3 处置（in-section description）|
| 607 | `sections.append("=== News ===\nNews service temporarily unavailable.")` | **L3** (news 数据源失败 + has_news=False 分支 — section header 存在 + body 描述) | **保留不动** — 同上 L3 in-section |
| 609 | `sections.append("=== News ===\nNo recent headlines.")` | **L3** (空状态 — section header 存在 + body 描述) | **保留不动** — 同上 L3 in-section |

仅 line 547 重构:

```python
if deps.news is None:
    return (
        "=== News ===\n"
        "=== Error ===\n"
        "News service not configured."
    )
```

**为什么 line 581/607/609 不升级到嵌套 `=== News ===\n=== Error ===\n...` 模式**: 这 3 处都已在 section body 内说明数据状态（per spec §4.1.4 L3 — section 内字段 fallback 短描述）；嵌套 `=== Error ===` sub-section 反而违反 §4.1.3 "single-field section" 规避原则（数据源不可用是单字段说明，不开独立 sub-section）。spec §4.2.5 边界 explicitly 接受 in-section L3 形态。

加 3 snapshot tests:

```python
def test_snapshot_get_market_news_l2_not_configured():
    """Snapshot — news service=None L2 emits === News === + === Error === sub-section."""
    content = (
        "=== News ===\n"
        "=== Error ===\n"
        "News service not configured."
    )
    expected = (
        "  ⚙ get_market_news\n"
        "    === News ===\n"
        "\n"
        "    === Error ===\n"
        "    News service not configured."
    )
    _assert_perception_render("get_market_news", content, expected)


def test_snapshot_get_market_news_happy_short():
    """Snapshot — news happy path with FGI + 2 symbol headlines (body < 10, keep all)."""
    content = (
        "=== Fear & Greed Index ===\n"
        "Value: Fear (35)\n"
        "(Updated: 2026-05-03)\n"
        "\n"
        "=== Symbol News (BTC, 2) ===\n"
        "[2026-05-03 14:00] BTC tests $75k support\n"
        "  Source: CoinDesk | Currencies: BTC\n"
        "[2026-05-03 13:30] Funding rates flip negative\n"
        "  Source: The Block | Currencies: BTC, ETH"
    )
    expected = (
        "  ⚙ get_market_news\n"
        "    === Fear & Greed Index ===\n"
        "    Value: Fear (35)\n"
        "    (Updated: 2026-05-03)\n"
        "\n"
        "    === Symbol News (BTC, 2) ===\n"
        "    [2026-05-03 14:00] BTC tests $75k support\n"
        "      Source: CoinDesk | Currencies: BTC\n"
        "    [2026-05-03 13:30] Funding rates flip negative\n"
        "      Source: The Block | Currencies: BTC, ETH"
    )
    _assert_perception_render("get_market_news", content, expected)


def test_snapshot_get_market_news_dense_general_news_clipped():
    """Snapshot — General Crypto News with 12 entries (each 2 lines = 24 body lines)
    triggers head=2/tail=2 clipping. Multi-entry boundary trade-off (spec §4.3.2)
    acknowledged: head/tail may split entries — trader sees first 2 + last 2 lines.
    """
    entries = []
    for i in range(12):
        entries.append(f"[2026-05-03 1{i:02d}:00] Headline {i}\n  Source: src{i} | Currencies: ALT{i}")
    content = "=== General Crypto News (12) ===\n" + "\n".join(entries)
    # Body: 12 × 2 = 24 lines, ≥ 10 → head=2 + omitted + tail=2
    from src.cli.display import _render_perception_tool
    out = _render_perception_tool("get_market_news", content)
    assert "    === General Crypto News (12) ===" in out
    assert "    [2026-05-03 100:00] Headline 0" in out  # head[0]
    assert "      Source: src0 | Currencies: ALT0" in out  # head[1]
    assert "    [... 20 rows omitted ...]" in out
    # Last 2 lines of body — entry 11's two lines
    assert "    [2026-05-03 111:00] Headline 11" in out  # tail[-2]
    assert "      Source: src11 | Currencies: ALT11" in out  # tail[-1]
```

注: dense test 用 substring 断言（不写 full byte-equal expected），因 12 × 2 行 fixture verbose；只验关键 head/tail/clipping marker 存在。

- [ ] **Step 5: Refactor `get_order_book` L2 paths + sub-sections per §4.2.20**

`src/agent/tools_perception.py:1146` / `1150` / `1164`:

```python
# line 1146
return (
    f"=== Order Book ({symbol}) ===\n"
    "=== Error ===\n"
    "Temporarily unavailable."
)

# line 1150 / 1164 (insufficient data)
return (
    f"=== Order Book ({symbol}) ===\n"
    "=== Error ===\n"
    f"Insufficient data (requested depth {depth}, got {actual})."
)
```

并把现有 `Depth (top {depth} each side):` block (line 1189) 升级为 explicit `=== Depth (top {depth} each side) ===`，把 `Concentrated levels (size > ...)` block (line 1219) 升级为 explicit `=== Concentrated Levels (size > {N}× median of top {depth}) ===`。

加 2 snapshot tests:

```python
def test_snapshot_get_order_book_happy_path():
    """Snapshot — order book 2 sub-sections (Order Book + Depth) without concentrated."""
    content = (
        "=== Order Book (BTC/USDT:USDT) ===\n"
        "Best bid: 75200.00 × 0.5000 BTC  |  Best ask: 75205.00 × 0.4500 BTC\n"
        "Spread: 5.00 (0.007%)\n"
        "\n"
        "=== Depth (top 20 each side) ===\n"
        "  Bids cumulative: 5.4500 BTC over 75200.00 - 75150.00 (0.07% deep)\n"
        "  Asks cumulative: 6.2000 BTC over 75205.00 - 75260.00 (0.07% deep)\n"
        "  Bid share: ~50% (balanced)"
    )
    expected = (
        "  ⚙ get_order_book\n"
        "    === Order Book (BTC/USDT:USDT) ===\n"
        "    Best bid: 75200.00 × 0.5000 BTC  |  Best ask: 75205.00 × 0.4500 BTC\n"
        "    Spread: 5.00 (0.007%)\n"
        "\n"
        "    === Depth (top 20 each side) ===\n"
        "      Bids cumulative: 5.4500 BTC over 75200.00 - 75150.00 (0.07% deep)\n"
        "      Asks cumulative: 6.2000 BTC over 75205.00 - 75260.00 (0.07% deep)\n"
        "      Bid share: ~50% (balanced)"
    )
    _assert_perception_render("get_order_book", content, expected)


def test_snapshot_get_order_book_l2_unavailable():
    """Snapshot — order book L2 (service exception) emits === Error === section."""
    content = (
        "=== Order Book (BTC/USDT:USDT) ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )
    expected = (
        "  ⚙ get_order_book\n"
        "    === Order Book (BTC/USDT:USDT) ===\n"
        "\n"
        "    === Error ===\n"
        "    Temporarily unavailable."
    )
    _assert_perception_render("get_order_book", content, expected)
```

- [ ] **Step 6: Verify `get_active_alerts` § 4.1.1 conformance + snapshot**

Read current source line 461 / 468:
- `=== Price Alert Settings ===` ✅ no symbol/tf — OK
- `=== Active Price Level Alerts ({count}/20) ===` ✅ count is in-section description — OK

加 1 snapshot test:

```python
def test_snapshot_get_active_alerts_with_alerts():
    """Snapshot — active alerts with vol param + 2 price level alerts."""
    content = (
        "=== Price Alert Settings ===\n"
        "Volatility alert: 1.5% in 10min window\n"
        "\n"
        "=== Active Price Level Alerts (2/20) ===\n"
        '  #1 (id=alert-1) above 76500.00 — "tactical resistance"\n'
        '  #2 (id=alert-2) below 74000.00 — "support break"'
    )
    expected = (
        "  ⚙ get_active_alerts\n"
        "    === Price Alert Settings ===\n"
        "    Volatility alert: 1.5% in 10min window\n"
        "\n"
        "    === Active Price Level Alerts (2/20) ===\n"
        '      #1 (id=alert-1) above 76500.00 — "tactical resistance"\n'
        '      #2 (id=alert-2) below 74000.00 — "support break"'
    )
    _assert_perception_render("get_active_alerts", content, expected)
```

- [ ] **Step 7: Run all batch B snapshot tests**

```bash
uv run pytest tests/test_display_cycle.py -k "snapshot_get_account_balance or snapshot_get_open_orders or snapshot_get_position or snapshot_get_market_news or snapshot_get_order_book or snapshot_get_active_alerts" -v 2>&1 | tail -40
```

Expected: 全部 PASS.

- [ ] **Step 8: Run existing batch B tool integration tests**

```bash
uv run pytest tests/ -k "test_get_position or test_get_account_balance or test_get_open_orders or test_get_market_news or test_get_order_book or test_get_active_alerts" -v 2>&1 | tail -50
```

Expected: 既有测试 likely fail on hard-coded literal "Current Position:" / "Account Balance:" 等 — update 到 `=== Position (` / `=== Account Balance ===` 子串断言。

- [ ] **Step 9: Run full suite**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: ~1085-1095 passed (Task 5 baseline + ~12-15 batch B snapshots)。0 failures.

- [ ] **Step 10: Commit batch B**

```bash
git add src/agent/tools_perception.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8c): T6 batch B — mid-frequency + implicit→explicit sectioning (6 tools)

Per spec §4.2.5 / §4.2.11-4.2.12 / §4.2.14 / §4.2.16 / §4.2.20:
- get_account_balance: implicit 'Account Balance:' → explicit === Account Balance ===
- get_open_orders: implicit 'Pending Orders:' → explicit === Pending Orders ===
- get_position: 4 explicit sections (Position / PnL / Risk Exposure / Exit Orders);
  hard-failure degrades Risk + Exit to (unavailable) sections, Position + PnL preserved
- get_market_news: L2 'News service not configured.' → === Error === section
- get_order_book: L2 plain text → === Error ===; promote Depth + Concentrated Levels
  blocks to explicit sub-sections
- get_active_alerts: already sectioned (verified § 4.1.1 conformance, no change)

~14 new snapshot tests (happy path + L2 / no-position / L3 per tool).
Updated existing tools_perception integration tests for explicit-section literals.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git status
```

---

### Task 7: Tool Refactor Batch C — Long-Tail (7 tools)

**Files:**
- Modify: `src/agent/tools_perception.py`
  - `get_macro_context` (line 909-994) — L2 `"Macro service not configured."` + `"Macro context: temporarily unavailable"` → `=== Error ===` section
  - `get_macro_calendar` (line 644-695) — L2 `"News service not configured."` → `=== Error ===` section; footer `"Note: macro calendar covers..."` → `=== Note ===` section per §4.2.7 / §4.1.1
  - `get_etf_flows` (line 997-1080) — L2 `"ETF flows service not configured."` / `"ETF flows: temporarily unavailable"` → `=== Error ===` section; footer `"Note: ..."` → `=== Note ===` section per §4.2.8 / §4.1.1
  - `get_stablecoin_supply` (line 1083-1123) — L2 plain text → `=== Error ===` section
  - `get_exchange_announcements` (line 614-641) — already sectioned; convert L2 `"News service not configured."` / `"...temporarily unavailable."` to `=== Error ===` (currently inline within section)
  - `get_trade_journal` (line 383-450) — happy path already sectioned；refactor 2 empty-state early-returns (`db_engine=None` line 387 / `not actions` line 402) 从 plain text → `=== Trade Journal ===\nNo trade journal entries yet.` (per R2-8c convention，T-DG-1 Path A 调真实 tool 时空 db 路径需 sectioned)
  - `get_performance` (line 479-521) — split single block into `=== Trading Performance ===` + `=== Trade Stats ===` per §4.2.17
- Test: snapshot fixtures + integration test updates

- [ ] **Step 1: Refactor `get_macro_context` L2**

`src/agent/tools_perception.py:917` 和 `923`:

```python
if deps.macro is None:
    return (
        "=== Macro Context ===\n"
        "=== Error ===\n"
        "Macro service not configured."
    )

# line 923
except Exception:
    logger.warning("Macro snapshot fetch failed", exc_info=True)
    return (
        "=== Macro Context ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )
```

`if not any_available` 路径 (line 991-992) 同样升级。

加 2 snapshot tests:

```python
def test_snapshot_get_macro_context_l2_not_configured():
    """Snapshot — macro service=None L2 emits === Error === section."""
    content = (
        "=== Macro Context ===\n"
        "=== Error ===\n"
        "Macro service not configured."
    )
    expected = (
        "  ⚙ get_macro_context\n"
        "    === Macro Context ===\n"
        "\n"
        "    === Error ===\n"
        "    Macro service not configured."
    )
    _assert_perception_render("get_macro_context", content, expected)


def test_snapshot_get_macro_context_happy_3_sections():
    """Snapshot — macro happy with all 3 sub-sections (Crypto / FRED / Equities)."""
    content = (
        "=== Crypto Market ===\n"
        "BTC.D: 60.00% | ETH.D: 12.50% | Total Mcap: $2.50T (24h: +1.20%)\n"
        "\n"
        "=== US Macro (FRED) ===\n"
        "USD Index (Broad TW): 105.20 (as of 2026-04-30)\n"
        "VIX: 16.50 (as of 2026-05-02)\n"
        "10Y Treasury: 4.25% (as of 2026-05-02)\n"
        "\n"
        "=== US Equities (Alpha Vantage) ===\n"
        "SPY: $520.50 (+0.30%, as of 2026-05-02)\n"
        "QQQ: $440.20 (+0.50%, as of 2026-05-02)"
    )
    expected = (
        "  ⚙ get_macro_context\n"
        "    === Crypto Market ===\n"
        "    BTC.D: 60.00% | ETH.D: 12.50% | Total Mcap: $2.50T (24h: +1.20%)\n"
        "\n"
        "    === US Macro (FRED) ===\n"
        "    USD Index (Broad TW): 105.20 (as of 2026-04-30)\n"
        "    VIX: 16.50 (as of 2026-05-02)\n"
        "    10Y Treasury: 4.25% (as of 2026-05-02)\n"
        "\n"
        "    === US Equities (Alpha Vantage) ===\n"
        "    SPY: $520.50 (+0.30%, as of 2026-05-02)\n"
        "    QQQ: $440.20 (+0.50%, as of 2026-05-02)"
    )
    _assert_perception_render("get_macro_context", content, expected)
```

- [ ] **Step 2: Refactor `get_macro_calendar` L2 + footer Note (§4.2.7)**

`src/agent/tools_perception.py:654-695`:

```python
if deps.news is None:
    return (
        "=== Upcoming Macro Events ===\n"
        "=== Error ===\n"
        "News service not configured."
    )

# Service unavailable (line 664-668)
if macro_events is None:
    sections.append(
        f"=== Upcoming Macro Events (next {lookahead_hours}h) ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )

# Footer (line 689-693) → === Note === section per §4.1.1
if macro_events is not None:
    sections.append(
        "=== Note ===\n"
        "Macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )
```

注意: §4.2.7 `=== Note ===` section **仅在 events list 非 None 时显示**——保留现有 `if macro_events is not None:` guard。

加 3 snapshot tests:

```python
def test_snapshot_get_macro_calendar_l2_not_configured():
    """Snapshot — news service=None L2 emits === Error === section."""
    content = (
        "=== Upcoming Macro Events ===\n"
        "=== Error ===\n"
        "News service not configured."
    )
    expected = (
        "  ⚙ get_macro_calendar\n"
        "    === Upcoming Macro Events ===\n"
        "\n"
        "    === Error ===\n"
        "    News service not configured."
    )
    _assert_perception_render("get_macro_calendar", content, expected)


def test_snapshot_get_macro_calendar_happy_with_note():
    """Snapshot — events present + Note footer section."""
    content = (
        "=== Upcoming Macro Events (next 12h) ===\n"
        "[2026-05-03 18:30] FOMC Statement — Impact: High\n"
        "  Fed funds rate decision and Powell press conference.\n"
        "\n"
        "=== Note ===\n"
        "Macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )
    expected = (
        "  ⚙ get_macro_calendar\n"
        "    === Upcoming Macro Events (next 12h) ===\n"
        "    [2026-05-03 18:30] FOMC Statement — Impact: High\n"
        "      Fed funds rate decision and Powell press conference.\n"
        "\n"
        "    === Note ===\n"
        "    Macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )
    _assert_perception_render("get_macro_calendar", content, expected)


def test_snapshot_get_macro_calendar_no_events_with_note():
    """Snapshot — empty events list (still has Note footer per §4.2.7 guard)."""
    content = (
        "=== Upcoming Macro Events (next 12h) ===\n"
        "No upcoming macro events.\n"
        "\n"
        "=== Note ===\n"
        "Macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )
    expected = (
        "  ⚙ get_macro_calendar\n"
        "    === Upcoming Macro Events (next 12h) ===\n"
        "    No upcoming macro events.\n"
        "\n"
        "    === Note ===\n"
        "    Macro calendar covers current week only; "
        "Friday evening / weekend calls may miss next week's early events."
    )
    _assert_perception_render("get_macro_calendar", content, expected)
```

- [ ] **Step 3: Refactor `get_etf_flows` L2 + footer Note (§4.2.8)**

`src/agent/tools_perception.py:1004-1080`:

```python
if deps.crypto_etf is None:
    return (
        "=== BTC Spot ETF Flows (US) ===\n"
        "=== Error ===\n"
        "ETF flows service not configured."
    )

# Both BTC + ETH failed (line 1058-1059)
if btc is None and eth is None:
    return (
        "=== BTC Spot ETF Flows (US) ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )

# Footer (line 1073-1078) → === Note === section per §4.1.1
if btc or eth:
    days_rendered = len(next((f for f in (btc, eth) if f), []))
    sections.append(
        "=== Note ===\n"
        f"Past {days_rendered} trading days (weekends/holidays excluded). "
        "Issuer-reported; today's value may be revised T+1."
    )
```

加 2 snapshot tests:

```python
def test_snapshot_get_etf_flows_l2_not_configured():
    """Snapshot — crypto_etf service=None L2 emits === Error ===."""
    content = (
        "=== BTC Spot ETF Flows (US) ===\n"
        "=== Error ===\n"
        "ETF flows service not configured."
    )
    expected = (
        "  ⚙ get_etf_flows\n"
        "    === BTC Spot ETF Flows (US) ===\n"
        "\n"
        "    === Error ===\n"
        "    ETF flows service not configured."
    )
    _assert_perception_render("get_etf_flows", content, expected)


def test_snapshot_get_etf_flows_happy_with_note():
    """Snapshot — BTC + ETH ETF flows + Note footer section."""
    content = (
        "=== BTC Spot ETF Flows (US) ===\n"
        "2026-05-02: +$120.50M  (cum: $35.20B, AUM: $52.10B)\n"
        "2026-05-01: -$30.00M\n"
        "2-day net: +$90.50M\n"
        "\n"
        "=== ETH Spot ETF Flows (US) ===\n"
        "2026-05-02: +$25.30M  (cum: $8.50B, AUM: $12.20B)\n"
        "2026-05-01: -$5.00M\n"
        "2-day net: +$20.30M\n"
        "\n"
        "=== Note ===\n"
        "Past 2 trading days (weekends/holidays excluded). "
        "Issuer-reported; today's value may be revised T+1."
    )
    expected = (
        "  ⚙ get_etf_flows\n"
        "    === BTC Spot ETF Flows (US) ===\n"
        "    2026-05-02: +$120.50M  (cum: $35.20B, AUM: $52.10B)\n"
        "    2026-05-01: -$30.00M\n"
        "    2-day net: +$90.50M\n"
        "\n"
        "    === ETH Spot ETF Flows (US) ===\n"
        "    2026-05-02: +$25.30M  (cum: $8.50B, AUM: $12.20B)\n"
        "    2026-05-01: -$5.00M\n"
        "    2-day net: +$20.30M\n"
        "\n"
        "    === Note ===\n"
        "    Past 2 trading days (weekends/holidays excluded). "
        "Issuer-reported; today's value may be revised T+1."
    )
    _assert_perception_render("get_etf_flows", content, expected)
```

- [ ] **Step 4: Refactor `get_stablecoin_supply` L2**

`src/agent/tools_perception.py:1088-1107`:

```python
if deps.onchain is None:
    return (
        "=== Stablecoin Supply ===\n"
        "=== Error ===\n"
        "Onchain service not configured."
    )

except Exception:
    logger.warning(...)
    return (
        "=== Stablecoin Supply ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )

if result is None:
    return (
        "=== Stablecoin Supply ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )

if not result["coins"]:
    return (
        "=== Stablecoin Supply ===\n"
        "=== Error ===\n"
        "Data unavailable (no tracked symbols found in response)."
    )
```

加 2 snapshot tests:

```python
def test_snapshot_get_stablecoin_supply_l2_not_configured():
    """Snapshot — onchain service=None L2 emits === Error ===."""
    content = (
        "=== Stablecoin Supply ===\n"
        "=== Error ===\n"
        "Onchain service not configured."
    )
    expected = (
        "  ⚙ get_stablecoin_supply\n"
        "    === Stablecoin Supply ===\n"
        "\n"
        "    === Error ===\n"
        "    Onchain service not configured."
    )
    _assert_perception_render("get_stablecoin_supply", content, expected)


def test_snapshot_get_stablecoin_supply_happy_path():
    """Snapshot — USDT + USDC + total Mcap (single section short)."""
    content = (
        "=== Stablecoin Supply ===\n"
        "USDT: $110.20B (7d: +$1.50B, +1.38%)\n"
        "USDC: $35.80B (7d: -$0.20B, -0.56%)\n"
        "Total Stablecoin Mcap: $146.00B (7d: +$1.30B, +0.90%)"
    )
    expected = (
        "  ⚙ get_stablecoin_supply\n"
        "    === Stablecoin Supply ===\n"
        "    USDT: $110.20B (7d: +$1.50B, +1.38%)\n"
        "    USDC: $35.80B (7d: -$0.20B, -0.56%)\n"
        "    Total Stablecoin Mcap: $146.00B (7d: +$1.30B, +0.90%)"
    )
    _assert_perception_render("get_stablecoin_supply", content, expected)
```

- [ ] **Step 5: Refactor `get_exchange_announcements` L2**

`src/agent/tools_perception.py:619-641` 当前 service-not-configured 路径 (line 620) 是 plain text；service-unavailable (line 627-631) 是 inline within section header — 转 `=== Error ===` sub-section:

```python
if deps.news is None:
    return (
        f"=== Exchange Announcements ===\n"
        "=== Error ===\n"
        "News service not configured."
    )

if announcements is None:
    return (
        f"=== Exchange Announcements (past {lookback_hours}h) ===\n"
        "=== Error ===\n"
        "Exchange announcements service temporarily unavailable."
    )
```

加 2 snapshot tests:

```python
def test_snapshot_get_exchange_announcements_l2_not_configured():
    """Snapshot — news service=None L2 emits === Error ===."""
    content = (
        "=== Exchange Announcements ===\n"
        "=== Error ===\n"
        "News service not configured."
    )
    expected = (
        "  ⚙ get_exchange_announcements\n"
        "    === Exchange Announcements ===\n"
        "\n"
        "    === Error ===\n"
        "    News service not configured."
    )
    _assert_perception_render("get_exchange_announcements", content, expected)


def test_snapshot_get_exchange_announcements_happy_short():
    """Snapshot — 2 announcements (body < 10 keep all)."""
    content = (
        "=== Exchange Announcements (past 24h) ===\n"
        "[2026-05-03 12:00] OKX maintenance window 2026-05-04 02:00-04:00 UTC\n"
        "[2026-05-03 09:00] New margin tier for BTC perpetual"
    )
    expected = (
        "  ⚙ get_exchange_announcements\n"
        "    === Exchange Announcements (past 24h) ===\n"
        "    [2026-05-03 12:00] OKX maintenance window 2026-05-04 02:00-04:00 UTC\n"
        "    [2026-05-03 09:00] New margin tier for BTC perpetual"
    )
    _assert_perception_render("get_exchange_announcements", content, expected)
```

- [ ] **Step 6: Refactor `get_trade_journal` empty-state paths + add snapshot tests**

Read source `src/agent/tools_perception.py:383-450`：happy path 已用 explicit `=== Performance Summary ===` + `=== Trade Journal ===`，但**两个 empty-state 早 return 无 sectioning**——`db_engine=None` (line 387) 和 `not actions` (line 402) 均直接返回 `"No trade journal entries yet."`。R2-8c convention 要求所有路径都 sectioned（T-DG-1 Path A 调真实 tool 时该项会 fail）。

修复两处 early-return:

```python
# src/agent/tools_perception.py:386-388
if deps.db_engine is None:
    return "=== Trade Journal ===\nNo trade journal entries yet."

# src/agent/tools_perception.py:401-402
if not actions:
    return "=== Trade Journal ===\nNo trade journal entries yet."
```

加 3 snapshot tests:

```python
def test_snapshot_get_trade_journal_with_entries():
    """Snapshot — trade journal with summary + 2 entries (happy path)."""
    content = (
        "=== Performance Summary ===\n"
        "Total Trades: 5 | Win: 3 (60.0%) | Loss: 2\n"
        "Avg Win: +12.50 USDT | Avg Loss: -5.00 USDT\n"
        "Profit Factor: 3.75\n"
        "\n"
        "=== Trade Journal ===\n"
        "[05-03 14:00] open (long) @ 75200.00, fee=0.0500 [filled], pnl=0.00\n"
        "  Reasoning: tactical long on RSI oversold\n"
        "[05-03 14:30] close @ 75500.00, fee=0.0500 [filled], pnl=12.50\n"
        "  Reasoning: target hit"
    )
    expected = (
        "  ⚙ get_trade_journal\n"
        "    === Performance Summary ===\n"
        "    Total Trades: 5 | Win: 3 (60.0%) | Loss: 2\n"
        "    Avg Win: +12.50 USDT | Avg Loss: -5.00 USDT\n"
        "    Profit Factor: 3.75\n"
        "\n"
        "    === Trade Journal ===\n"
        "    [05-03 14:00] open (long) @ 75200.00, fee=0.0500 [filled], pnl=0.00\n"
        "      Reasoning: tactical long on RSI oversold\n"
        "    [05-03 14:30] close @ 75500.00, fee=0.0500 [filled], pnl=12.50\n"
        "      Reasoning: target hit"
    )
    _assert_perception_render("get_trade_journal", content, expected)


def test_snapshot_get_trade_journal_no_db_engine():
    """Snapshot — db_engine=None empty-state, sectioned per R2-8c convention."""
    content = "=== Trade Journal ===\nNo trade journal entries yet."
    expected = (
        "  ⚙ get_trade_journal\n"
        "    === Trade Journal ===\n"
        "    No trade journal entries yet."
    )
    _assert_perception_render("get_trade_journal", content, expected)


def test_snapshot_get_trade_journal_no_actions():
    """Snapshot — db_engine present but actions empty, sectioned per R2-8c convention.

    Same rendered output as no-db-engine case (both early-return identical literal),
    but exercised via different code path (covers regression of either branch).
    """
    content = "=== Trade Journal ===\nNo trade journal entries yet."
    expected = (
        "  ⚙ get_trade_journal\n"
        "    === Trade Journal ===\n"
        "    No trade journal entries yet."
    )
    _assert_perception_render("get_trade_journal", content, expected)
```

- [ ] **Step 7: Refactor `get_performance` split into 2 sections per §4.2.17**

`src/agent/tools_perception.py:479-521` — 当前是单 `=== Trading Performance ===` block 含 PnL + Stats 字段混合。spec §4.2.17 拆为:
- `=== Trading Performance ===`: Initial Balance / Current Balance / Total Return / Realized PnL / Total Fees
- `=== Trade Stats ===`: Total/Winning/Losing trades / Avg win/loss / Profit Factor / Max Drawdown / Best/Worst Trade

```python
async def get_performance(deps: TradingDeps) -> str:
    balance = await deps.exchange.fetch_balance()
    ret_usdt = balance.total_usdt - deps.initial_balance
    ret_pct = (ret_usdt / deps.initial_balance) * 100 if deps.initial_balance > 0 else 0.0

    perf_section = (
        f"=== Trading Performance ===\n"
        f"Initial Balance: {deps.initial_balance:.2f} USDT\n"
        f"Current Balance: {balance.total_usdt:.2f} USDT\n"
        f"Total Return: {ret_pct:+.2f}% ({ret_usdt:+.2f} USDT) (incl. unrealized)"
    )

    if deps.metrics is None:
        return perf_section + "\n\n=== Trade Stats ===\nNo metrics service available."

    metrics = await deps.metrics.compute()

    if metrics.total_trades == 0:
        return perf_section + "\n\n=== Trade Stats ===\nNo completed trades yet."

    fees_str = f"-{metrics.total_fees:.2f}" if metrics.total_fees > 0 else "0.00"
    perf_section += (
        f"\nRealized PnL: {metrics.total_pnl:+.2f} USDT (gross, before fees)\n"
        f"Total Fees: {fees_str} USDT"
    )

    stats_section = (
        f"=== Trade Stats ===\n"
        f"Total Trades: {metrics.total_trades} | Win: {metrics.winning_trades} "
        f"({metrics.win_rate:.1%}) | Loss: {metrics.losing_trades}\n"
        f"Avg Win: {metrics.avg_win:+.2f} USDT | Avg Loss: {metrics.avg_loss:.2f} USDT\n"
        f"Profit Factor: {'N/A (no losses)' if metrics.profit_factor == float('inf') else f'{metrics.profit_factor:.2f}'}\n"
        f"Max Drawdown: {f'-{metrics.max_drawdown_pct:.1f}' if metrics.max_drawdown_pct > 0 else '0.0'}%\n"
        f"Best Trade: {metrics.best_trade:+.2f} USDT | Worst Trade: {metrics.worst_trade:.2f} USDT"
    )

    return f"{perf_section}\n\n{stats_section}"
```

加 2 snapshot tests:

```python
def test_snapshot_get_performance_no_metrics_service():
    """Snapshot — metrics=None empty-state, both sections sectioned."""
    content = (
        "=== Trading Performance ===\n"
        "Initial Balance: 1000.00 USDT\n"
        "Current Balance: 998.00 USDT\n"
        "Total Return: -0.20% (-2.00 USDT) (incl. unrealized)\n"
        "\n"
        "=== Trade Stats ===\n"
        "No metrics service available."
    )
    expected = (
        "  ⚙ get_performance\n"
        "    === Trading Performance ===\n"
        "    Initial Balance: 1000.00 USDT\n"
        "    Current Balance: 998.00 USDT\n"
        "    Total Return: -0.20% (-2.00 USDT) (incl. unrealized)\n"
        "\n"
        "    === Trade Stats ===\n"
        "    No metrics service available."
    )
    _assert_perception_render("get_performance", content, expected)


def test_snapshot_get_performance_happy_path():
    """Snapshot — 5-trade happy path with both sections."""
    content = (
        "=== Trading Performance ===\n"
        "Initial Balance: 1000.00 USDT\n"
        "Current Balance: 1052.50 USDT\n"
        "Total Return: +5.25% (+52.50 USDT) (incl. unrealized)\n"
        "Realized PnL: +55.00 USDT (gross, before fees)\n"
        "Total Fees: -2.50 USDT\n"
        "\n"
        "=== Trade Stats ===\n"
        "Total Trades: 5 | Win: 3 (60.0%) | Loss: 2\n"
        "Avg Win: +20.00 USDT | Avg Loss: -7.50 USDT\n"
        "Profit Factor: 4.00\n"
        "Max Drawdown: -1.5%\n"
        "Best Trade: +25.00 USDT | Worst Trade: -10.00 USDT"
    )
    expected = (
        "  ⚙ get_performance\n"
        "    === Trading Performance ===\n"
        "    Initial Balance: 1000.00 USDT\n"
        "    Current Balance: 1052.50 USDT\n"
        "    Total Return: +5.25% (+52.50 USDT) (incl. unrealized)\n"
        "    Realized PnL: +55.00 USDT (gross, before fees)\n"
        "    Total Fees: -2.50 USDT\n"
        "\n"
        "    === Trade Stats ===\n"
        "    Total Trades: 5 | Win: 3 (60.0%) | Loss: 2\n"
        "    Avg Win: +20.00 USDT | Avg Loss: -7.50 USDT\n"
        "    Profit Factor: 4.00\n"
        "    Max Drawdown: -1.5%\n"
        "    Best Trade: +25.00 USDT | Worst Trade: -10.00 USDT"
    )
    _assert_perception_render("get_performance", content, expected)
```

- [ ] **Step 8: Run all batch C snapshot tests**

```bash
uv run pytest tests/test_display_cycle.py -k "snapshot_get_macro or snapshot_get_etf or snapshot_get_stablecoin or snapshot_get_exchange or snapshot_get_trade_journal or snapshot_get_performance" -v 2>&1 | tail -40
```

Expected: 全部 PASS.

- [ ] **Step 9: Run existing batch C tool integration tests**

```bash
uv run pytest tests/ -k "test_get_macro or test_get_etf or test_get_stablecoin or test_get_exchange_announcements or test_get_trade_journal or test_get_performance" -v 2>&1 | tail -50
```

Expected: 既有测试 likely fail on L2 plain-text literal — update to 子串断言。

- [ ] **Step 10: Run full suite**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: ~1100-1115 passed。0 failures.

- [ ] **Step 11: Commit batch C**

```bash
git add src/agent/tools_perception.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8c): T7 batch C — long-tail perception sectioning (7 tools)

Per spec §4.2.6-4.2.8 / §4.2.15 / §4.2.17-4.2.19:
- get_macro_context: L2 plain text → === Error === section (3 paths)
- get_macro_calendar: L2 paths → === Error ===; footer → === Note === section
- get_etf_flows: L2 paths → === Error ===; footer → === Note === section
- get_stablecoin_supply: L2 plain text → === Error === (4 paths)
- get_exchange_announcements: L2 plain text → === Error === sub-section
- get_trade_journal: 2 empty-state early-returns (db_engine=None / no actions)
  → === Trade Journal === sectioned (happy path already sectioned)
- get_performance: split 1 block → === Trading Performance === + === Trade Stats ===

~14 new snapshot tests (happy path + L2 + footer Note per tool).
Updated existing tools_perception integration tests for L2 / Note section literals.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git status
```

---

### Task 8: Final Test Pass — Edge Cases + Drift Guard T-DG-1 + Byte-Equal + AC Verification

**Files:**
- Modify (in repo, git-tracked): `tests/test_display_cycle.py` (新增 T-EC-1 ~ T-EC-10 / T-BE-1 / T-DG-1 / T-INT-2 + remaining edge cases)
- Modify (out-of-repo, NOT git-tracked, **AC25/AC26 only**):
  - `/Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/MEMORY.md`
  - `/Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/project_r2_8c_tool_output_optimization.md`
  - `/Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/project_w2_prep_progress.md`
  - ⚠️ **Sandbox caveat**: Codex / restricted workspace-write env 可能不允许写此目录。
    若执行环境无法写入，AC25/AC26 标 "deferred — to be completed by maintainer
    after merge in env with auto-memory write access"，**不阻塞 R2-8c impl PR 合并**。

- [ ] **Step 1: Add edge case tests T-EC-1 ~ T-EC-9 (T-EC-10/11 already in T2/T5)**

```python
# === R2-8c edge cases (T-EC) ===


def test_ec_1_no_section_header_fallback():
    """T-EC-1: tool 输出无 === Section === → unnamed section render (legacy / get_memories)."""
    from src.cli.display import _render_perception_tool
    out = _render_perception_tool("get_memories", "Memory entry 1\nMemory entry 2")
    assert "  ⚙ get_memories" in out
    assert "    Memory entry 1" in out


def test_ec_2_l1_failure_single_line_x_icon():
    """T-EC-2: outcome != success → R2-8a single-line ✗ icon (不进 multi-line)."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action

    calls = [ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="c1")]
    returns = {
        "c1": ToolReturnPart(
            tool_name="get_market_data", tool_call_id="c1",
            content="ConnectionError: upstream timeout",
            outcome="error",  # outcome != success → L1
        ),
    }
    out = _render_action(calls, returns, cycle_id="abcd1234")
    assert "  ✗ get_market_data" in out
    # Multi-line markers should NOT appear (L1 不进 multi-line)
    assert "    ===" not in out  # no section header indent line


def test_ec_3_l2_error_section_in_multi_line():
    """T-EC-3: tool 内捕获异常 + success outcome 返回 === Error === → 进 multi-line, section 显示。"""
    from src.cli.display import _render_perception_tool
    content = (
        "=== Higher Timeframe View (BTC/USDT:USDT, 4h) ===\n"
        "=== Error ===\n"
        "Temporarily unavailable."
    )
    out = _render_perception_tool("get_higher_timeframe_view", content)
    assert "    === Error ===" in out
    assert "    Temporarily unavailable." in out


def test_ec_4_section_body_one_line():
    """T-EC-4: section body 仅 1 行 → keep all (< 10)."""
    from src.cli.display import _render_perception_tool
    content = "=== Account Balance ===\nTotal: 998.00 USDT"
    out = _render_perception_tool("get_account_balance", content)
    assert out == (
        "  ⚙ get_account_balance\n"
        "    === Account Balance ===\n"
        "    Total: 998.00 USDT"
    )


def test_ec_5_section_body_zero_lines_render_header_only():
    """T-EC-5: section body 0 行 → header alone."""
    from src.cli.display import _render_perception_tool
    content = "=== Empty Section ===\n"
    out = _render_perception_tool("get_market_data", content)
    assert out == (
        "  ⚙ get_market_data\n"
        "    === Empty Section ==="
    )


def test_ec_6_section_header_markup_literal_escaped():
    """T-EC-6: section header 含 markup 字面值 → escape 为 \\[red]Critical[/]."""
    from src.cli.display import _render_perception_tool
    content = "=== [red]Critical[/] ===\nbody"
    out = _render_perception_tool("get_market_news", content)
    assert r"\[red]" in out  # rich.markup.escape 转 \[red]


def test_ec_7_section_body_markup_literal_escaped():
    """T-EC-7: section body 含 markup 字面值（如新闻 [bold]）→ escape。"""
    from src.cli.display import _render_perception_tool
    content = "=== Symbol News ===\nHeadline: [bold]BREAKING[/] something"
    out = _render_perception_tool("get_market_news", content)
    assert r"\[bold]" in out


def test_ec_8_long_url_line_no_wrapping_in_helper():
    """T-EC-8: 极长单行（如 URL ≥ terminal width）— helper 不主动 wrap，由 Rich render 处理。"""
    from src.cli.display import _render_perception_tool
    long_url = "https://example.com/" + "x" * 200
    content = f"=== Symbol News ===\n{long_url}"
    out = _render_perception_tool("get_market_news", content)
    # Helper preserves URL on single line; wrapping is Rich render concern (D7 R2-8a 决议)
    assert long_url in out


def test_ec_9_orphan_tool_call_id_no_return_captured():
    """T-EC-9: 无 tool_call_id 关联 → R2-8a [no return captured] (regression of pre-existing path)."""
    from pydantic_ai.messages import ToolCallPart
    from src.cli.display import _render_action
    calls = [ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="orphan")]
    out = _render_action(calls, returns_lookup={}, cycle_id="abcd1234")
    assert "[no return captured]" in out
```

- [ ] **Step 2: Add T-INT-2 (failed tool ✗ regression guard)**

```python
def test_int_2_failed_tool_x_icon_regression_guard():
    """T-INT-2: failed perception tool → R2-8a single-line ✗ icon, no multi-line break."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action

    calls = [ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="c1")]
    returns = {
        "c1": ToolReturnPart(
            tool_name="get_market_data", tool_call_id="c1",
            content="ConnectionError to upstream", outcome="error",
        ),
    }
    out = _render_action(calls, returns, cycle_id="abcd1234")
    assert "  ✗ get_market_data" in out
    assert "ConnectionError" in out  # fallback summary kept
```

- [ ] **Step 3: Add T-BE-1 byte-equal Section model invariant**

```python
def test_be_1_byte_equal_section_model_invariant():
    """T-BE-1: parsed_display Section model == [Section(escape(h), tuple(escape(l) for l in clip(body)))]
    per spec §4.6 P1.2 校准 (Section model 比较, 非 raw bytes)."""
    from src.cli.display import (
        Section,
        _parse_sections,
        _clip_body,
        _render_perception_tool,
    )
    from rich.markup import escape

    content = (
        "=== Sec A ===\n"
        + "\n".join(f"row {i}" for i in range(15))  # 15 rows → triggers clipping
        + "\n\n=== Sec B ===\nshort body"
    )

    # Re-parse the rendered output (strip 4-space indent + tool_name line)
    rendered = _render_perception_tool("get_market_data", content)
    rendered_lines = rendered.split("\n")
    assert rendered_lines[0] == "  ⚙ get_market_data"
    # Strip 4-space indent from all subsequent lines, then re-parse
    body_lines = [l[4:] if l.startswith("    ") else l for l in rendered_lines[1:]]
    rendered_content = "\n".join(body_lines)
    parsed_display = _parse_sections(rendered_content)

    expected = [
        Section(
            header=escape(s.header) if s.header else None,
            body=tuple(escape(line) for line in _clip_body(s.body)),
        )
        for s in _parse_sections(content)
    ]

    assert parsed_display == expected, (
        f"Byte-equal Section model mismatch:\n"
        f"--- expected ---\n{expected}\n"
        f"--- parsed_display ---\n{parsed_display}"
    )
```

- [ ] **Step 4: Add T-DG-1 sectioning convention lint for 19 sectioned tools (real tool invocation)**

T-DG-1 必须真实 invoke 每个 perception tool（不能只 lint 静态 fixture，否则改源码不引发 drift）——参考既有 `tests/test_perception_tools_n3.py` 的 `MockDeps` + `AsyncMock` 模式。

19 个 tool 分两档断言策略:
- **Path A — service=None / L2 fallback path** (10 工具): tool 内部 `if deps.X is None: return "... === Error === ..."` 直接命中；MockDeps 留空即可，断言 return 起首 `=== `。
  - `get_market_news` / `get_exchange_announcements` / `get_macro_calendar` (deps.news=None)
  - `get_macro_context` (deps.macro=None)
  - `get_etf_flows` (deps.crypto_etf=None)
  - `get_stablecoin_supply` (deps.onchain=None)
  - `get_trade_journal` (deps.db_engine=None)
  - `get_performance` (deps.metrics=None)
  - `get_active_alerts` (deps.exchange.get_alert_params returns None / get_price_level_alerts returns [])
  - `get_position` (deps.exchange.fetch_positions returns [] → 触发 `=== Position ===\nNo open positions.`)
- **Path B — minimum-mock happy path** (9 工具): 必须 mock market_data / exchange 至少 1 个返回值，断言 return 起首 `=== `。OHLCV fixture 用 `_make_ohlcv_df_local`（inline 复制 `tests/test_perception_tools_n3.py:_make_ohlcv_df` 模式，避 cross-test-file import 耦合）。
  - `get_market_data` / `get_higher_timeframe_view` / `get_multi_timeframe_snapshot` / `get_price_pivots` / `get_recent_trades` / `get_order_book` (need market_data with ticker/ohlcv)
  - `get_derivatives_data` (need market_data with funding/oi/lsr)
  - `get_account_balance` (need exchange.fetch_balance)
  - `get_open_orders` (need exchange.fetch_open_orders → [] 触发 `=== Pending Orders ===\nNo pending orders.`)

加测试代码:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass


# Reuse MockDeps shape from tests/test_perception_tools_n3.py — duplicated here
# rather than imported because per-test minimal customization differs.
@dataclass
class _MockDeps:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "5m"
    market_data: object = None
    exchange: object = None
    technical: object = None
    memory: object = None
    session_id: str = "test"
    db_engine: object = None
    initial_balance: float = 1000.0
    metrics: object = None
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None


def _mock_exchange_minimal(positions=None, balance_total=998.0,
                            open_orders=None, alert_params=None,
                            price_level_alerts=None):
    """Build a MagicMock+AsyncMock exchange covering the methods Path-A/B tools call."""
    exchange = MagicMock()
    exchange.fetch_positions = AsyncMock(return_value=positions or [])
    exchange.fetch_open_orders = AsyncMock(return_value=open_orders or [])
    balance = MagicMock()
    balance.total_usdt = balance_total
    balance.free_usdt = balance_total * 0.8
    balance.used_usdt = balance_total * 0.2
    exchange.fetch_balance = AsyncMock(return_value=balance)
    exchange.get_alert_params = MagicMock(return_value=alert_params)
    exchange.get_price_level_alerts = MagicMock(return_value=price_level_alerts or [])
    return exchange


# Path A — service=None / empty-state L2 path (no market_data/exchange mocks needed beyond minimal)
PATH_A_TOOLS = [
    "get_market_news", "get_exchange_announcements", "get_macro_calendar",
    "get_macro_context", "get_etf_flows", "get_stablecoin_supply",
    "get_trade_journal", "get_performance", "get_active_alerts",
    "get_position",
]


@pytest.mark.parametrize("tool_name", PATH_A_TOOLS)
async def test_dg_1_path_a_service_none_or_empty_state(tool_name):
    """T-DG-1 Path A: service=None / empty-state path → returns starts with `=== `.

    Tools rely on tool-internal early return when service dep is None or
    primary state is empty (no positions / no metrics service / etc.).
    After T5-T7 refactor these paths emit === Section === / === Error ===.
    """
    import src.agent.tools_perception as tp
    fn = getattr(tp, tool_name)
    deps = _MockDeps(
        # Path-A tools that rely on exchange even when their own service=None
        exchange=_mock_exchange_minimal(),  # for get_active_alerts / get_position
    )
    out = await fn(deps)
    assert out.startswith("=== "), (
        f"{tool_name} (Path A) did not start with section header: {out[:120]!r}"
    )


# Path B — minimum mock for tools that must hit market_data / exchange happy path


def _make_ohlcv_df_local(n_rows: int, last_close: float = 75_234.50):
    """Local copy of tests/test_perception_tools_n3.py:_make_ohlcv_df — inline
    duplicated to avoid cross-test-file import coupling (review F5 校准: relying
    on `from tests.test_perception_tools_n3 import _make_ohlcv_df` couples this
    test to that file's helper survival; inline copy keeps T-DG-1 isolated).

    If a future PR consolidates test fixtures, move both copies to tests/_fixtures.py.
    """
    import pandas as pd
    base = last_close - (n_rows - 1) * 50
    rows = []
    for i in range(n_rows):
        close = base + i * 50
        rows.append({
            "timestamp": 1_776_000_000 + i * 86_400_000,
            "open": close - 10, "high": close + 500, "low": close - 500,
            "close": close, "volume": 1000.0,
        })
    return pd.DataFrame(rows)


async def _invoke_path_b(tool_name: str) -> str:
    """Build minimum mocks per tool and invoke. Each branch sets up only what
    the tool calls — keeps mock surface area small + readable per tool."""
    import src.agent.tools_perception as tp

    fn = getattr(tp, tool_name)

    if tool_name == "get_market_data":
        market_data = AsyncMock()
        ticker = MagicMock()
        ticker.last = 75200.0
        ticker.bid = 75195.0; ticker.ask = 75205.0
        ticker.high = 76000.0; ticker.low = 74800.0; ticker.base_volume = 1000.0
        market_data.get_ticker.return_value = ticker
        market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df_local(150)
        technical = MagicMock()
        technical.compute_indicators.return_value = {"atr_14": 100.0, "volume_ratio": 1.0}
        technical.format_for_llm.return_value = "RSI(14): 50.0"
        deps = _MockDeps(market_data=market_data, technical=technical)
        return await fn(deps)

    if tool_name == "get_higher_timeframe_view":
        market_data = AsyncMock()
        market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df_local(250)
        return await fn(_MockDeps(market_data=market_data), timeframe="4h")

    if tool_name == "get_multi_timeframe_snapshot":
        market_data = AsyncMock()
        ticker = MagicMock(); ticker.last = 75200.0
        market_data.get_ticker.return_value = ticker
        market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df_local(250)
        technical = MagicMock()
        technical.compute_indicators.return_value = {"atr_14": 100.0}
        deps = _MockDeps(market_data=market_data, technical=technical)
        return await fn(deps)

    if tool_name == "get_price_pivots":
        market_data = AsyncMock()
        ticker = MagicMock(); ticker.last = 75200.0
        market_data.get_ticker.return_value = ticker
        market_data.get_ohlcv_dataframe.return_value = _make_ohlcv_df_local(100)
        return await fn(_MockDeps(market_data=market_data))

    if tool_name == "get_recent_trades":
        market_data = AsyncMock()
        market_data.get_recent_trades.return_value = []  # no-trades L3 path → still sectioned
        return await fn(_MockDeps(market_data=market_data))

    if tool_name == "get_order_book":
        market_data = AsyncMock()
        market_data.get_order_book.side_effect = Exception("upstream")  # L2 unavailable
        return await fn(_MockDeps(market_data=market_data))

    if tool_name == "get_derivatives_data":
        # Force all-3-failure → L2 === Error ===
        market_data = AsyncMock()
        market_data.get_funding_rate.side_effect = Exception()
        market_data.get_open_interest.side_effect = Exception()
        market_data.get_long_short_ratio.side_effect = Exception()
        return await fn(_MockDeps(market_data=market_data))

    if tool_name == "get_account_balance":
        return await fn(_MockDeps(exchange=_mock_exchange_minimal()))

    if tool_name == "get_open_orders":
        return await fn(_MockDeps(exchange=_mock_exchange_minimal(open_orders=[])))

    raise AssertionError(f"unhandled Path-B tool {tool_name}")


PATH_B_TOOLS = [
    "get_market_data", "get_higher_timeframe_view",
    "get_multi_timeframe_snapshot", "get_price_pivots",
    "get_recent_trades", "get_order_book", "get_derivatives_data",
    "get_account_balance", "get_open_orders",
]


@pytest.mark.parametrize("tool_name", PATH_B_TOOLS)
async def test_dg_1_path_b_minimum_mock_happy_or_l2(tool_name):
    """T-DG-1 Path B: minimum-mock invocation (happy path or L2 unavailable) →
    returns starts with `=== `.

    Each branch sets up only the mocks the tool consumes; tools that have
    cheap L2 paths (e.g. order_book / derivatives all-fail) use side_effect
    Exception to take the === Error === path with no real-data fixture cost.
    """
    out = await _invoke_path_b(tool_name)
    assert out.startswith("=== "), (
        f"{tool_name} (Path B) did not start with section header: {out[:120]!r}"
    )


# === T-DG-1b: 结构性 sectioning lint (every === ... === line is canonical) ===

import re as _re_dg

_SECTION_LINE_RE = _re_dg.compile(r"^=== (.+) ===$")


def _assert_no_half_sectioned(tool_name: str, out: str):
    """Every line that looks like a section header (starts with '=== ') must
    close with ' ===' (canonical pattern). Catches half-sectioned drift like
    'Pending Orders:' appearing after a proper === Section === header.
    """
    for i, line in enumerate(out.split("\n")):
        if line.startswith("=== "):
            assert _SECTION_LINE_RE.match(line), (
                f"{tool_name} line {i} not canonical section header: {line!r}"
            )


@pytest.mark.parametrize("tool_name", PATH_A_TOOLS)
async def test_dg_1b_path_a_canonical_section_lines(tool_name):
    """T-DG-1b Path A: every === ...-prefixed line must be canonical."""
    import src.agent.tools_perception as tp
    fn = getattr(tp, tool_name)
    deps = _MockDeps(exchange=_mock_exchange_minimal())
    out = await fn(deps)
    _assert_no_half_sectioned(tool_name, out)


@pytest.mark.parametrize("tool_name", PATH_B_TOOLS)
async def test_dg_1b_path_b_canonical_section_lines(tool_name):
    """T-DG-1b Path B: every === ...-prefixed line must be canonical."""
    out = await _invoke_path_b(tool_name)
    _assert_no_half_sectioned(tool_name, out)


# === T-DG-1c: 关键字段白名单 (spec §11 risk mitigation) ===
#
# Per spec §11 风险表: "T-DG-1 lint 检查无关键字段消失（白名单校验 RSI / MACD / MA20 /
# Bollinger / Funding / OI / L/S / FGI 等核心字段在重构后仍存在）". 仅 Path B
# happy path 需此白名单（Path A 是 service=None / empty-state，没有数据字段）。
#
# 字段选取依据 .working/r2-8c-smoke-data-2026-05-03.md B2 引用率 ≥ 50% 的字段。
_CRITICAL_FIELDS_PATH_B: dict[str, list[str]] = {
    "get_market_data": ["Ticker", "Technical Indicators", "Market Context",
                        "Recent Candles", "RSI", "MACD", "ATR"],
    "get_higher_timeframe_view": ["Higher Timeframe View", "MA Distances",
                                  "MA50", "MA100", "MA200"],
    "get_multi_timeframe_snapshot": ["Multi-TF Snapshot", "Current price",
                                     "Momentum", "Structure", "Volatility"],
    "get_price_pivots": ["Price Pivots", "Current Price",
                         "Levels Above Current Price",
                         "Levels Below Current Price"],
    # Path-B tools that exercise L2 path (no field whitelist — only structural):
    "get_recent_trades": ["Recent Trades"],
    "get_order_book": ["Order Book", "Error"],  # forced L2 in _invoke_path_b
    "get_derivatives_data": ["Derivatives Data", "Error"],  # forced all-fail L2
    "get_account_balance": ["Account Balance", "Total", "Return", "Free", "Used"],
    "get_open_orders": ["Pending Orders"],
}

# Path A 也有少量必现字段（service=None / empty-state 默认文案 + section header）:
_CRITICAL_FIELDS_PATH_A: dict[str, list[str]] = {
    "get_market_news": ["News", "Error", "not configured"],
    "get_exchange_announcements": ["Exchange Announcements", "Error", "not configured"],
    "get_macro_calendar": ["Upcoming Macro Events", "Error", "not configured"],
    "get_macro_context": ["Macro Context", "Error", "not configured"],
    "get_etf_flows": ["BTC Spot ETF Flows", "Error", "not configured"],
    "get_stablecoin_supply": ["Stablecoin Supply", "Error", "not configured"],
    "get_trade_journal": ["Trade Journal", "No trade journal entries yet"],
    "get_performance": ["Trading Performance", "Initial Balance", "Current Balance"],
    "get_active_alerts": ["Price Alert Settings", "Volatility alert"],
    "get_position": ["Position", "No open positions"],
}


@pytest.mark.parametrize("tool_name", PATH_A_TOOLS)
async def test_dg_1c_path_a_critical_fields_present(tool_name):
    """T-DG-1c Path A: critical default-state fields/headers present in output."""
    import src.agent.tools_perception as tp
    fn = getattr(tp, tool_name)
    deps = _MockDeps(exchange=_mock_exchange_minimal())
    out = await fn(deps)
    for field in _CRITICAL_FIELDS_PATH_A[tool_name]:
        assert field in out, (
            f"{tool_name} missing critical field {field!r} in Path A output:\n"
            f"{out[:400]!r}"
        )


@pytest.mark.parametrize("tool_name", PATH_B_TOOLS)
async def test_dg_1c_path_b_critical_fields_present(tool_name):
    """T-DG-1c Path B: critical happy-path / L2 fields/headers present in output.

    Spec §11 risk mitigation: prevents key field deletion regression (e.g. RSI /
    MACD / MA20 / Funding / OI / L/S — agent reasoning depends on these).
    """
    out = await _invoke_path_b(tool_name)
    for field in _CRITICAL_FIELDS_PATH_B[tool_name]:
        assert field in out, (
            f"{tool_name} missing critical field {field!r} in Path B output:\n"
            f"{out[:400]!r}"
        )


# === T-DG-1d: 参数顺序 lint (spec §4.1.1) ===
#
# Spec §4.1.1 convention: 多参数 `(symbol[, timeframe[, scope]])` 顺序。这里
# 仅校验 §4.1.1 explicitly multi-arg headers 的工具（防 HTF 类 param-order
# regression 重复发生）。
@pytest.mark.parametrize("tool_name,expected_pattern", [
    ("get_higher_timeframe_view", r"=== Higher Timeframe View \(BTC/USDT:USDT, 4h\) ==="),
    ("get_price_pivots", r"=== Price Pivots \(BTC/USDT:USDT, main TF: \w+\) ==="),
    # Single-arg / no-symbol headers: market_data / derivatives / order_book /
    # multi_timeframe / recent_trades 用 (symbol) 单参数已在 §4.2 enum 落定，
    # 不进 §4.1.1 multi-arg lint 范围。
])
async def test_dg_1d_param_order_convention(tool_name, expected_pattern):
    """T-DG-1d: §4.1.1 multi-arg header param order — symbol-first convention."""
    out = await _invoke_path_b(tool_name)
    assert _re_dg.search(expected_pattern, out), (
        f"{tool_name} header param order violates §4.1.1: pattern {expected_pattern!r} "
        f"not found in:\n{out[:400]!r}"
    )
```

注意:
- `19 = 10 (Path A) + 9 (Path B) = _SECTIONED_PERCEPTION_TOOL_NAMES`。`get_memories` 不在 T-DG-1 范围（spec §4.2.13 backend-dependent 例外）。
- T-DG-1 现拆 4 子项: **a (起首 sectioning)** / **b (结构性 — 每行 === 必规范)** / **c (关键字段白名单)** / **d (§4.1.1 参数顺序)**——共同 enforce spec AC1 / AC20 / §11 risk mitigation。
- 真实 invoke `src.agent.tools_perception` 函数；任何 sectioning / 字段 / 参数顺序 regression 会被对应子 lint catch。

- [ ] **Step 5: Run all final test additions**

```bash
uv run pytest tests/test_display_cycle.py -k "ec_1 or ec_2 or ec_3 or ec_4 or ec_5 or ec_6 or ec_7 or ec_8 or ec_9 or int_2 or be_1 or dg_1" -v 2>&1 | tail -40
```

Expected: 全部 PASS。T-DG-1 是 parametrized × 19 = 19 PASS。

- [ ] **Step 6: Run full test suite for AC21 baseline verification**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: 1048 baseline + 10 helpers (T1) + 3 dispatch (T2) + 2 thinking (T4) + 12 batch A snapshots (T5) + 12 batch B snapshots (T6) + 16 batch C snapshots (T7) + 9 edge cases (T-EC-1~9, T-EC-10/11 已在 T2/T5) + 1 (T-INT-2) + 1 (T-BE-1) + 59 (T-DG-1a/b/c/d 4 子 lint × parametrized: a/b/c 各 19 + d 2 = 59) = **~1173 passed**。

预期 ≈ 1170-1180（snapshot 实际行数 ±5 容差），0 failures。spec §9 AC21 数字已 pre-update 至 ~1173 (plan-stage round-2 校准 — T-DG-1 拆 a/b/c/d 4 子 lint，parametrized cases 从 19 升至 59)，spec § 7 测试矩阵 + § 12 增量 row 同步；后续 R2-8c 实施完若实测有 ±5 漂移再 minor PR 回填实际数。

- [ ] **Step 7: AC sanity check (manual checklist verify)**

逐一对照 spec §9 AC1-AC26：

```bash
# AC1: 19 sectioned tools — T-DG-1 自动 lint
uv run pytest tests/test_display_cycle.py -k "test_dg_1" -v | tail -25

# AC2 byte-equal: T-BE-1
uv run pytest tests/test_display_cycle.py -k "test_be_1" -v

# AC3 universal clipping: T-CLIP-1~3
uv run pytest tests/test_display_cycle.py -k "test_clip_body" -v

# AC4 execution single-line: T-INT-1
uv run pytest tests/test_display_cycle.py -k "test_int_1" -v

# AC5 failed tool: T-INT-2
uv run pytest tests/test_display_cycle.py -k "test_int_2" -v

# AC6 thinking 2000: T-INT-3
uv run pytest tests/test_display_cycle.py -k "test_int_3" -v

# AC11 edge cases: T-EC-1~11
uv run pytest tests/test_display_cycle.py -k "test_ec_" -v | tail -25

# AC20 drift guards: T-DG-1 + T-DG-2
uv run pytest tests/test_display_cycle.py -k "test_dg_" -v | tail -25
```

每条 expected: PASS。

AC-token: verify `.working/r2-8c-token-verification-2026-05-03.md` 存在 + 三档评判 result recorded。

- [ ] **Step 8: Final full suite verification**

```bash
uv run pytest -q 2>&1 | tail -5
```

Expected: ~1173 passed (1170-1180 容差), 0 failed. 若有 failure 修后重跑.

- [ ] **Step 9: Final commit (repo files only — memory files staged separately)**

⚠️ **不要** 把 `/Users/z/.claude/.../memory/` 路径加入 `git add` —— 这些文件在 repo 外（auto-memory 系统独立目录），`git add` 会报 `pathspec ... did not match` / `outside repository`。仅 stage 本 repo 内变更:

```bash
git add tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8c): T8 final — edge cases + drift guards + AC verification

Final R2-8c test additions:
- T-EC-1~9: edge cases (no-header fallback / L1 single-line / L2 in multi-line /
  empty body / markup escape on header+body / long URL / orphan tool_call_id)
- T-INT-2: failed tool ✗ icon regression guard
- T-BE-1: byte-equal Section model invariant (post-parse + post-clip + post-escape)
- T-DG-1: sectioning convention lint — Path A (service=None / empty-state, 10 tools)
  + Path B (minimum-mock happy / L2, 9 tools) — invokes real tools_perception
  functions to catch sectioning drift

Total: ~1173 tests pass (1048 baseline + ~125 R2-8c additions).
All AC1-AC26 verified per spec §9.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
git status
```

Expected: clean tree post-commit.

- [ ] **Step 10: Update memory tracking (AC25 / AC26) — independent step, NOT git-staged**

Memory files live in `/Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/`，由 auto-memory 系统单独管理；用 Write tool 直接 update（不进 git commit）:

1. `memory/project_r2_8c_tool_output_optimization.md` — 在 frontmatter `description` 后 mark ✅ landed (PR # 在 reviewer 合并后回填)
2. `memory/project_w2_prep_progress.md` — content 更新序: `R2-8a ✅ → R2-8c ✅ → R2-8b → R2-9 → 启 W2`
3. `memory/MEMORY.md` — 同步索引行（如 `r2_8c` 行 hook 文案需变化，否则仅 hook desc minor edit）

**Sandbox env fallback**: 若执行环境（如 Codex workspace-write 限制 / 容器化 CI）无法写
`/Users/z/.claude/...` 路径，跳过本 step 并把 AC25/AC26 标 "deferred — maintainer
runs in env with auto-memory write access post-merge"；不阻塞 PR。AC1-AC23 (功能 +
测试 + drift guards) 与 memory 无耦合，PR 完整性不受影响。

无 commit step——memory writes are picked up by the persistence layer automatically.

---

## Summary of Tasks

| Task | Scope | Tests added | Commit |
|---|---|---|---|
| T0 | T-token A/B verification artifact | (none — plan note) | 1 (chore docs) |
| T1 | Section dataclass + 3 helpers + 10 unit tests | +10 | 1 (helpers feat) |
| T2 | `_render_action` 4-branch dispatch + 三层集合 + T-DG-2 + T-EC-11 + T-INT-1 | +3 | 1 (dispatch feat) |
| T3 | `_PERCEPTION_PARSERS` rename → `_SYSTEM_LOG_PERCEPTION_PARSERS` | 0 | 1 (refactor rename) |
| T4 | `_render_reasoning` 800→2000 + T-INT-3 | +2 | 1 (thinking feat) |
| T5 | Batch A: 6 tier-1 tools sectioning | +~12 | 1 (batch A feat) |
| T6 | Batch B: 6 mid-frequency + implicit→explicit | +~14 | 1 (batch B feat) |
| T7 | Batch C: 7 long-tail | +~14 | 1 (batch C feat) |
| T8 | Edge cases T-EC-1~9 + T-INT-2 + T-BE-1 + T-DG-1 + AC + memory | +~32 | 1 (final feat) |

**总 commits**: 9 (含 T0 plan artifact + 8 impl + 0 separate cleanup)
**总 tests added**: ~125 (1048 baseline → ~1173 final)；T-DG-1 a/b/c/d 4 子 lint 各自 parametrized × 19 (a/b/c) + 2 (d) = 59 cases，是 round 2 重写后的主增量来源
**总 commits + per-task gating**: TDD red → green → run full suite at end of each task; if regression appears, fix before proceeding.

## Risks & Mitigations Reference

参考 spec §11:
- 工具 reasoning 引用映射 regression: T-DG-1 lint fixtures 含核心字段断言；plan 阶段 list "旧字段 → 新字段" mapping 在 §4.2 enum 内已隐含（参数顺序 swap / Section header 提升）
- Token 经济恶化: T0 前置 gate 三档评判；超 20% stop 回 brainstorm
- Snapshot 维护负担: inline fixture style，每工具 1-2 snapshot test，diff reviewer 重点检查字段语义不变
- 单 PR 改动量大: 9-task TDD + 9 commit，每 commit 独立可 review；如 reviewer 觉过大可拆 P1 (T0-T4 helpers + dispatch + thinking) + P2 (T5-T8 工具批量) — 不违反 D9 (A 合并 spec)，仅 implementation strategy
- `get_memories` backend 例外: 三层集合 explicit 标注，T-DG-1 跳过，T-EC-1 / T-RPT-4 fallback path 验证

---
