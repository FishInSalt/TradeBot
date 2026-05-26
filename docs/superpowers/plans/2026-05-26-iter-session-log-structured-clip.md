# Session Log Structured-Row Clip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `src/cli/display.py:_clip_body` 引入 by-anchor heuristic — 当 section body 含 ≥ 2 行 `[<word>]` prefix 时进入 structured-row mode 按 group 保留；否则 fallback 到现有 D4 list-like clip（bit-for-bit 不变）。

**Architecture:** 单文件改动（`display.py`），渲染层 heuristic（不动工具输出，零 LLM context 影响），三档 dispatch (structured-row / list-like / short)，fail-open 哲学（识别不到 anchor 退化到 D4）。

**Tech Stack:** Python 3.11 + pytest + Rich (escape only)。无新依赖。

**Spec:** `docs/superpowers/specs/2026-05-26-iter-session-log-structured-clip-design.md`

---

## File Structure

**Modified files** (本 iter scope):

- `src/cli/display.py`
  - 新增 module-level constant `_ANCHOR_RE` (regex)
  - 新增 helper `_is_anchor(line: str) -> bool`
  - 新增 helper `_group_by_anchor(body) -> list[tuple[str, list[str]]]`
  - 改造 `_clip_body(body, n=10, group_cap=12)` — 三档分支 dispatch
  - 其他函数（`_parse_sections` / `_strip_blanks` / `_render_tool_body` / `_render_action`）**0 改动**

- `tests/test_display_cycle.py`
  - 在现有 T-CLIP 块（line 1397-1487）后追加 module-level `def test_*` 共 13 单测 + 2 drift guard
  - 不建 class（保持现有风格）
  - 已有 `test_snapshot_get_market_news_dense_general_news_clipped` (line 2248-2266) 需更新预期断言

**Not modified**:
- `src/agent/tools_perception.py` 及任何工具源码（design invariant）
- DB schema / migrations
- 任何其他渲染路径文件

---

## Task 0: Setup & Baseline

**Goal:** 验证 feature 分支就位 + baseline pytest count，为后续行为比对建立基准。

**Files:**
- Verify: 当前在 `iter-session-log-structured-clip` 分支
- Read: `src/cli/display.py:444-505`（看现有 `_clip_body` + `_render_tool_body`）
- Read: `tests/test_display_cycle.py:1397-1487`（看现有 T-CLIP 测试上下文）

- [ ] **Step 1: 验证 git 分支 + spec 已 commit**

```bash
git rev-parse --abbrev-ref HEAD
# Expected: iter-session-log-structured-clip

git log --oneline -3
# Expected: 包含 "docs: spec for session log structured-row clip heuristic"
```

- [ ] **Step 2: 跑 baseline pytest 锁定 starting test count**

```bash
pytest tests/ --tb=no -q 2>&1 | tail -3
# 记录 "N passed" — 这是 baseline (memory 估值 1808；以实测为准)
```

- [ ] **Step 3: 看现有 _clip_body 实现**

```bash
sed -n '444,460p' src/cli/display.py
# Expected: D4 实现 (head=2 + omitted + tail=2)，body < 10 全保留
```

- [ ] **Step 4: 看现有 T-CLIP 测试位置**

```bash
grep -n "test_clip_body\|T-CLIP" tests/test_display_cycle.py | head -10
# Expected: line 1397+ 是 test_clip_body_* 系列
```

- [ ] **Step 5: 无代码改动，无 commit**（这是 setup task）

---

## Task 1: Drift Guard — 固化 D4 现有行为

**Goal:** 在改 `_clip_body` 之前先写测试固化"D4 list-like / short mode 行为不变"。这些 test 在改造完成后必须仍 PASS。

**Files:**
- Test: `tests/test_display_cycle.py`（在 T-CLIP 块末尾追加，~ line 1490 后）

- [ ] **Step 1: 写 drift guard test #1 — D4 list-like 行为 bit-for-bit 不变**

在 `tests/test_display_cycle.py` 文末（或 T-CLIP 块后 line 1490 附近）追加：

```python
# === iter-session-log-structured-clip drift guards ===
# 这些测试固化 D4 现有 list-like / short mode 行为；本 iter 改造不应破坏它们。


def test_clip_body_drift_guard_list_like_30_row_no_anchor():
    """drift guard: 30 行无 anchor body 仍走 D4 row-clip (head=2 + omitted + tail=2)."""
    from src.cli.display import _clip_body
    body = [f"  {i:02d}:00  77{500+i:03d}.00  candle data" for i in range(30)]
    out = _clip_body(body)
    assert len(out) == 5
    assert out[0] == "  00:00  77500.00  candle data"
    assert out[1] == "  01:00  77501.00  candle data"
    assert out[2] == "[... 26 rows omitted ...]"
    assert out[3] == "  28:00  77528.00  candle data"
    assert out[4] == "  29:00  77529.00  candle data"


def test_clip_body_drift_guard_short_body_keep_all():
    """drift guard: < 10 行无 anchor body 全保留 (short mode)."""
    from src.cli.display import _clip_body
    body = ["row 0", "row 1", "row 2", "row 3", "row 4"]
    out = _clip_body(body)
    assert out == ("row 0", "row 1", "row 2", "row 3", "row 4")
```

- [ ] **Step 2: 跑 drift guard tests，验证当前 D4 实现下 PASS**

```bash
pytest tests/test_display_cycle.py::test_clip_body_drift_guard_list_like_30_row_no_anchor tests/test_display_cycle.py::test_clip_body_drift_guard_short_body_keep_all -v
# Expected: 2 passed
```

- [ ] **Step 3: Commit drift guards**

```bash
git add tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
test: drift guards for D4 list-like + short mode

iter-session-log-structured-clip 前置测试：固化现有 _clip_body D4 行为
（head=2 + omitted + tail=2 for 30-row no-anchor；< 10 行全保留），
本 iter 改造完成后必须仍 PASS。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_ANCHOR_RE` + `_is_anchor`

**Goal:** 实现 anchor 行识别 regex + helper。TDD 风格：先写 test → fail → 实现 → pass。

**Files:**
- Modify: `src/cli/display.py`（在 `_clip_body` 上方加 module-level constant + helper）
- Test: `tests/test_display_cycle.py`（drift guard 之后追加）

- [ ] **Step 1: 写 failing test for `_is_anchor`**

在 `tests/test_display_cycle.py` 末尾追加（drift guard 之后）：

```python
def test_is_anchor_matches_bracket_word_prefix():
    """_is_anchor: 行首立即是 [<word>] 返回 True。"""
    from src.cli.display import _is_anchor
    assert _is_anchor("[5m]  Mom +0.1% ...") is True
    assert _is_anchor("[1h] (last closed candle: ...)") is True
    assert _is_anchor("[2026-05-25 14:30] Headline") is True
    assert _is_anchor("[STOP] BUY 0.1 @ ...") is True  # 行首无 leading space 才匹配


def test_is_anchor_rejects_leading_space():
    """_is_anchor: 行首含 leading space 不匹配（orders 渲染前 body 内 2 空格缩进）。"""
    from src.cli.display import _is_anchor
    assert _is_anchor("  [STOP] BUY ...") is False  # 2 空格缩进
    assert _is_anchor(" [LIMIT] ...") is False  # 1 空格


def test_is_anchor_rejects_omitted_marker():
    """_is_anchor: [... N rows omitted ...] 不视为 anchor (recursive 防护)."""
    from src.cli.display import _is_anchor
    assert _is_anchor("[... 11 rows omitted ...]") is False
    assert _is_anchor("[... 9 groups omitted ...]") is False
    assert _is_anchor("[...]") is False


def test_is_anchor_rejects_non_bracket_lines():
    """_is_anchor: 非 [<word>] 起手不匹配。"""
    from src.cli.display import _is_anchor
    assert _is_anchor("plain text") is False
    assert _is_anchor("Mom +0.1%") is False
    assert _is_anchor("") is False
    assert _is_anchor("=== Section ===") is False
```

- [ ] **Step 2: 跑测试验证 fail（_is_anchor 尚不存在）**

```bash
pytest tests/test_display_cycle.py::test_is_anchor_matches_bracket_word_prefix -v
# Expected: ImportError / AttributeError — _is_anchor not defined
```

- [ ] **Step 3: 实现 `_ANCHOR_RE` + `_is_anchor`**

在 `src/cli/display.py` 中找到 `_clip_body` 函数（line 444 附近），在其**上方**插入：

```python
# === iter-session-log-structured-clip: by-anchor heuristic ===
# Anchor row 识别正则。
# Pattern 解释:
#   ^\[            行首立即是 `[`（无 leading whitespace）
#   (?!\.\.\.)     负向 lookahead 排除 [... omitted ...] / [...]
#   [^\]\s]        `[` 后第 1 字符不是 `]` 也不是 whitespace（确保 [<word>] 有内容）
_ANCHOR_RE = re.compile(r'^\[(?!\.\.\.)[^\]\s]')


def _is_anchor(line: str) -> bool:
    """Return True iff line starts with [<word>] prefix (not [... omitted ...]).

    Used by _clip_body to detect structured-row mode (≥ 2 anchor rows).
    """
    return bool(_ANCHOR_RE.match(line))
```

注：`re` 模块在文件顶部应已 import；如未 import 需检查 `src/cli/display.py` 顶部 imports。

- [ ] **Step 4: 跑测试验证 pass**

```bash
pytest tests/test_display_cycle.py -k "test_is_anchor" -v
# Expected: 4 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat: add _ANCHOR_RE + _is_anchor helper

iter-session-log-structured-clip Step 1/N:
- _ANCHOR_RE: 行首 [<word>] 匹配，排除 [... omitted ...]
- _is_anchor: 单行 → bool 判定
- 4 单测 covering match / leading-space reject / omitted-marker reject /
  non-bracket reject

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `_group_by_anchor`

**Goal:** 实现 body → groups 切分 helper。每 anchor 起新 group，非 anchor 行（含 blank）归属当前 group continuation；prelude 行各自单独成 1-row group。

**Files:**
- Modify: `src/cli/display.py`（在 `_is_anchor` 下方）
- Test: `tests/test_display_cycle.py`

- [ ] **Step 1: 写 failing tests for `_group_by_anchor`**

```python
def test_group_by_anchor_pure_anchor_body():
    """_group_by_anchor: 全 anchor body → 每 anchor 一 group, continuation=[]."""
    from src.cli.display import _group_by_anchor
    body = ["[5m] Mom +0.1%", "[1h] Mom +0.3%", "[4h] Mom -0.5%", "[1d] Mom +1.0%"]
    groups = _group_by_anchor(body)
    assert len(groups) == 4
    assert groups[0] == ("[5m] Mom +0.1%", [])
    assert groups[3] == ("[1d] Mom +1.0%", [])


def test_group_by_anchor_with_continuation_rows():
    """_group_by_anchor: anchor + 续行 + blank → 续行 + blank 都归属当前 group."""
    from src.cli.display import _group_by_anchor
    body = [
        "[5m] Mom +0.1%",
        "      Last 3 closes: ...",
        "",
        "[1h] Mom +0.3%",
        "      Last 3 closes: ...",
    ]
    groups = _group_by_anchor(body)
    assert len(groups) == 2
    assert groups[0] == ("[5m] Mom +0.1%", ["      Last 3 closes: ...", ""])
    assert groups[1] == ("[1h] Mom +0.3%", ["      Last 3 closes: ..."])


def test_group_by_anchor_prelude_each_line_single_group():
    """_group_by_anchor: prelude (非 anchor 起首行) 每行各自单独 1-row group."""
    from src.cli.display import _group_by_anchor
    body = [
        "Last: 77540.00",
        "MA fast-vs-slow: 5m above | 1h below",
        "Columns: ...",
        "",
        "[5m] Mom +0.1%",
        "[1h] Mom +0.3%",
    ]
    groups = _group_by_anchor(body)
    # 3 prelude single-row groups + 2 anchor groups = 5 groups
    # blank 在 anchor 出现前归属上一个 prelude group (Columns:) 的 continuation
    assert len(groups) == 5
    assert groups[0] == ("Last: 77540.00", [])
    assert groups[1] == ("MA fast-vs-slow: 5m above | 1h below", [])
    assert groups[2] == ("Columns: ...", [""])  # blank 进入 prelude 3 的 continuation
    assert groups[3] == ("[5m] Mom +0.1%", [])
    assert groups[4] == ("[1h] Mom +0.3%", [])


def test_group_by_anchor_empty_body():
    """_group_by_anchor: 空 body 返回空 list."""
    from src.cli.display import _group_by_anchor
    assert _group_by_anchor([]) == []
    assert _group_by_anchor(()) == []
```

- [ ] **Step 2: 跑测试验证 fail**

```bash
pytest tests/test_display_cycle.py -k "test_group_by_anchor" -v
# Expected: ImportError — _group_by_anchor not defined
```

- [ ] **Step 3: 实现 `_group_by_anchor`**

在 `src/cli/display.py` 中 `_is_anchor` 下方插入：

```python
def _group_by_anchor(
    body: tuple[str, ...] | list[str],
) -> list[tuple[str, list[str]]]:
    """Split body into groups: each anchor line starts a new group;
    non-anchor lines (blanks + plain text + continuation) attach to the
    current group's continuation list.

    Assumes body has had leading/trailing blanks stripped upstream by
    `_strip_blanks` (display.py:433-441). I.e. body[0] is non-blank,
    avoiding undefined "blank attaches to previous group" at body start.

    Prelude rule (R4): body lines before the first anchor each form a
    single-row group (head = the line itself, continuation = []).
    A blank that appears between prelude lines and the first anchor
    attaches to the LAST prelude group's continuation (per R3).

    Returns list of (head_line, [continuation_lines]) tuples.
    - In anchor-group: head is the anchor line.
    - In prelude single-row group: head is the prelude line itself
      (not a true anchor — semantically "group head line").
    """
    groups: list[tuple[str, list[str]]] = []
    for line in body:
        if _is_anchor(line):
            groups.append((line, []))
        else:
            if groups:
                # Attach to current group's continuation
                groups[-1][1].append(line)
            else:
                # No current group: this is a prelude line, form 1-row group
                groups.append((line, []))
    return groups
```

- [ ] **Step 4: 跑测试验证 pass**

```bash
pytest tests/test_display_cycle.py -k "test_group_by_anchor" -v
# Expected: 4 passed
```

注意 prelude case：`Columns: ...` 是 prelude line，紧接 blank。按实现，prelude 行第 3 加入后 `groups[-1]` 是 `("Columns: ...", [])`，blank 进入其 continuation 变成 `("Columns: ...", [""])` —— 与 test expectation 一致。

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat: add _group_by_anchor helper

iter-session-log-structured-clip Step 2/N:
- _group_by_anchor: body → list[(head_line, [continuation])]
- 每 anchor 起新 group；non-anchor 归属当前 group continuation
- prelude 行各自单独 1-row group（per spec §2.3 R4）
- 假设上游 _strip_blanks 已剥 leading/trailing blanks
- 4 单测 covering pure-anchor / with-continuation / prelude / empty

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `_clip_body` 改造 — structured-row mode 全展分支

**Goal:** 改造 `_clip_body` 加 structured-row 分支（anchor_count ≥ 2 + groups ≤ cap → 全展）。list-like 和 short mode 保持现状（drift guard 守护）。

**Files:**
- Modify: `src/cli/display.py:444-459`（`_clip_body` 完整改造）
- Test: `tests/test_display_cycle.py`

- [ ] **Step 1: 写 failing test for structured-row full-expansion**

```python
def test_clip_body_structured_row_mode_multi_tf_like():
    """structured-row mode: 4 anchor groups × 2 行 → 全展（cap 内）."""
    from src.cli.display import _clip_body
    body = [
        "[5m] Mom +0.1%",
        "      Last 3 closes: ...",
        "[1h] Mom +0.3%",
        "      Last 3 closes: ...",
        "[4h] Mom -0.5%",
        "      Last 3 closes: ...",
        "[1d] Mom +1.0%",
        "      Last 3 closes: ...",
    ]
    out = _clip_body(body)
    # 4 groups × 2 行 = 8 行；全展不 clip
    assert len(out) == 8
    joined = "\n".join(out)
    assert "[5m]" in joined and "[1h]" in joined and "[4h]" in joined and "[1d]" in joined
    assert "omitted" not in joined


def test_clip_body_structured_row_mode_threshold_2_anchors():
    """structured-row mode: 边界 — anchor_count == 2 触发模式."""
    from src.cli.display import _clip_body
    body = ["[a] 1", "[b] 2"]
    out = _clip_body(body)
    assert out == ("[a] 1", "[b] 2")


def test_clip_body_single_anchor_fallback_to_list_like():
    """边界 — anchor_count = 1 不进 structured，走 list-like (≥10) 或 short (<10)."""
    from src.cli.display import _clip_body
    # 1 anchor + 9 续行 = 10 行，走 list-like D4
    body = ["[5m] Mom"] + [f"  cont {i}" for i in range(9)]
    out = _clip_body(body)
    assert len(out) == 5
    assert "rows omitted" in out[2]


def test_clip_body_with_prelude_full_expansion():
    """structured-row + prelude: prelude 单行 group + anchor group 都保留."""
    from src.cli.display import _clip_body
    body = [
        "Last: 77540.00",
        "MA fast-vs-slow: 5m above",
        "",
        "[5m] Mom +0.1%",
        "      Last 3 closes: ...",
        "[1h] Mom +0.3%",
        "      Last 3 closes: ...",
    ]
    out = _clip_body(body)
    # 2 prelude single-row groups (blank 归属 group[1]) + 2 anchor groups = 4 groups
    # 全展输出含全部 lines
    assert "Last: 77540.00" in out
    assert "MA fast-vs-slow: 5m above" in out
    assert "[5m] Mom +0.1%" in out
    assert "[1h] Mom +0.3%" in out
    assert "omitted" not in "\n".join(out)
```

- [ ] **Step 2: 跑测试验证 fail（现 _clip_body 还是 D4，会把多 anchor body 也 clip）**

```bash
pytest tests/test_display_cycle.py -k "test_clip_body_structured_row_mode_multi_tf_like" -v
# Expected: FAIL — 当前 D4 把 8 行 body 也 clip 成 5 行（< 10 阈值不 clip，但断言 len==8 + omitted not in joined 可能仍 PASS）
# 注：8 行 < 10 阈值，D4 不 clip，所以实际可能 PASS。这是 boundary case。
# 但 test_clip_body_with_prelude_full_expansion 共 7 行 < 10，也 short mode 不 clip → PASS
```

注：multi_tf_like 测试只有 8 行 < n=10，**短 mode 也能 PASS**。需补一个明确触发 D4 clip 的 case：

```python
def test_clip_body_structured_row_8_anchors_no_d4_clip():
    """structured-row: 8 anchor groups × 2 行 = 16 行（≥10）触发 D4 但 anchor≥2 → structured 全展."""
    from src.cli.display import _clip_body
    body = []
    for i in range(8):
        body.append(f"[a{i}] row {i}")
        body.append(f"      cont {i}")
    # 16 行 ≥ n=10；D4 会 clip 成 5 行
    # 但 structured 应全展 16 行
    out = _clip_body(body)
    assert len(out) == 16, f"expected 16 lines, got {len(out)}: {out}"
    joined = "\n".join(out)
    for i in range(8):
        assert f"[a{i}]" in joined
    assert "omitted" not in joined
```

把这个 case 也加入。跑测试，verify 当前 D4 实现下**这个 case fail**：

```bash
pytest tests/test_display_cycle.py::test_clip_body_structured_row_8_anchors_no_d4_clip -v
# Expected: FAIL — D4 当前会 clip 成 5 行（len(out) == 5 != 16）
```

- [ ] **Step 3: 改造 `_clip_body`（structured-row 全展分支 + list-like 分支保留）**

替换 `src/cli/display.py:444-459` 的现有 `_clip_body`：

```python
def _clip_body(
    body: tuple[str, ...] | list[str],
    n: int = 10,
    group_cap: int = 12,
) -> tuple[str, ...]:
    """Three-tier clip dispatch (per spec §2.3 / §4.3):

    1. structured-row mode  (anchor_count >= 2)
       → group-level handling: len(groups) <= group_cap 全展，
         otherwise _flatten(head[:3]) + "[... N groups omitted ...]" + _flatten(tail[-3:])

    2. list-like mode       (len(body) >= n, anchor_count < 2)
       → existing D4 row-clip unchanged: (body[0], body[1],
         "[... N rows omitted ...]", body[-2], body[-1])

    3. short mode           (len(body) < n, anchor_count < 2)
       → keep all (unchanged)

    Symmetric head=3 / tail=3 design (structured-row cap-exceeded):
    Renderer does not pre-assume per-tool semantic priority. Class A tools
    have different internal ordering (news newest-first; trade_journal
    oldest-first via reversed(actions); macro_calendar upcoming chronological).
    Symmetric preserves both ends regardless of tool semantics.

    Omission marker forms (semantically distinct, grep should differentiate):
    - list-like:    "[... N rows omitted ...]"   (rows = line count)
    - cap-exceeded: "[... N groups omitted ...]" (groups = group count)
    """
    # Branch detection
    groups = _group_by_anchor(body)
    anchor_count = sum(1 for g in groups if _is_anchor(g[0]))

    if anchor_count >= 2:
        # Branch 1: structured-row mode
        if len(groups) <= group_cap:
            # Full expansion
            return tuple(_flatten_groups(groups))
        else:
            # cap-exceeded: head[:3] + omitted + tail[-3:]
            omitted_count = len(groups) - 6
            head_lines = _flatten_groups(groups[:3])
            tail_lines = _flatten_groups(groups[-3:])
            return tuple(head_lines + [f"[... {omitted_count} groups omitted ...]"] + tail_lines)

    if len(body) >= n:
        # Branch 2: list-like mode (D4 unchanged)
        return (
            body[0], body[1],
            f"[... {len(body) - 4} rows omitted ...]",
            body[-2], body[-1],
        )

    # Branch 3: short mode (unchanged)
    return tuple(body)


def _flatten_groups(groups: list[tuple[str, list[str]]]) -> list[str]:
    """Flatten groups → flat line list: [head, *continuation, head, *continuation, ...]"""
    out: list[str] = []
    for head, continuation in groups:
        out.append(head)
        out.extend(continuation)
    return out
```

- [ ] **Step 4: 跑全部相关测试验证 pass**

```bash
pytest tests/test_display_cycle.py -k "clip_body or is_anchor or group_by_anchor" -v
# Expected: 全部 PASS (drift guards + new structured-row tests)
```

特别确认 drift guards 仍 PASS（说明 list-like / short 行为未被破坏）。

- [ ] **Step 5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat: _clip_body structured-row mode full expansion

iter-session-log-structured-clip Step 3/N:
- _clip_body 三档 dispatch: structured (anchor≥2) / list-like (D4) / short
- structured 全展: len(groups) ≤ cap=12 → 全部 group head + continuation
- 新增 _flatten_groups helper
- 4 单测 + cap-exceeded 分支留待 Task 5
- D4 drift guards 仍 PASS（list-like / short 行为 bit-for-bit 不变）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `_clip_body` cap-exceeded 分支

**Goal:** 测 + 验证 structured-row mode 在 len(groups) > 12 时的 cap elide 行为（head 3 + omitted + tail 3）。

**Files:**
- Test: `tests/test_display_cycle.py`

注：Task 4 已经在 `_clip_body` 中实现了 cap-exceeded 分支，本 task 只需补测试覆盖。

- [ ] **Step 1: 写 cap-exceeded test**

```python
def test_clip_body_group_cap_exceeded():
    """structured-row mode cap-exceeded: 15 anchor groups → head[3] + omitted + tail[3]."""
    from src.cli.display import _clip_body
    body = [f"[a{i}] row {i}" for i in range(15)]  # 15 anchor groups, each 1 line
    out = _clip_body(body)
    # head 3 + 1 marker + tail 3 = 7 lines
    assert len(out) == 7
    assert out[0] == "[a0] row 0"
    assert out[1] == "[a1] row 1"
    assert out[2] == "[a2] row 2"
    assert out[3] == "[... 9 groups omitted ...]"
    assert out[4] == "[a12] row 12"
    assert out[5] == "[a13] row 13"
    assert out[6] == "[a14] row 14"


def test_clip_body_group_cap_exact_boundary():
    """cap 边界: len(groups) == 12 全展, == 13 触发 elide."""
    from src.cli.display import _clip_body
    # 12 groups → 全展
    body12 = [f"[a{i}]" for i in range(12)]
    out12 = _clip_body(body12)
    assert len(out12) == 12
    assert "omitted" not in "\n".join(out12)

    # 13 groups → cap elide
    body13 = [f"[a{i}]" for i in range(13)]
    out13 = _clip_body(body13)
    assert len(out13) == 7
    assert "[... 7 groups omitted ...]" in out13


def test_clip_body_cap_exceeded_with_continuation():
    """cap-exceeded: groups 含 continuation 行时 head/tail 都带各自 continuation."""
    from src.cli.display import _clip_body
    body = []
    for i in range(15):
        body.append(f"[a{i}] head {i}")
        body.append(f"  cont {i}")
    out = _clip_body(body)
    # head 3 groups × 2 lines + 1 marker + tail 3 groups × 2 lines = 13 lines
    assert len(out) == 13
    assert out[0] == "[a0] head 0"
    assert out[1] == "  cont 0"
    assert out[6] == "[... 9 groups omitted ...]"
    assert out[7] == "[a12] head 12"
    assert out[8] == "  cont 12"
```

- [ ] **Step 2: 跑测试验证 PASS（cap-exceeded 实现已在 Task 4 完成）**

```bash
pytest tests/test_display_cycle.py -k "cap_exceeded or cap_exact" -v
# Expected: 3 passed
```

- [ ] **Step 3: 如有 FAIL，调试 cap-exceeded 分支实现（应当 PASS）**

若 fail，检查 `_clip_body` cap-exceeded 分支的 `omitted_count = len(groups) - 6` 计算（应是 `total - head_count(3) - tail_count(3) = total - 6`）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
test: cap-exceeded branch coverage for _clip_body

iter-session-log-structured-clip Step 4/N:
- 3 单测 covering cap=12 边界（12 全展 / 13 elide）+ cap-exceeded 含
  continuation 的 group-level 切分
- 验证 head[:3] + "[... N groups omitted ...]" + tail[-3:] 对称行为

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 补齐剩余 edge case 单测

**Goal:** 补齐 spec §7.1 剩余的 edge case 测试：news date-anchor / htf 多续行 / omitted marker recursion / rich markup / empty body / pure prelude。

**Files:**
- Test: `tests/test_display_cycle.py`

- [ ] **Step 1: 写所有剩余 edge case 测试**

```python
def test_clip_body_news_like_date_anchor_body():
    """date-anchor `[2026-05-25 14:30]` 内含空格不被 [^\\]\\s] 误判，仍触发 structured."""
    from src.cli.display import _clip_body
    body = []
    for i in range(5):
        body.append(f"[2026-05-25 1{i}:00] Headline {i}")
        body.append(f"  Source: src{i} | Currencies: ALT{i}")
    out = _clip_body(body)
    # 5 anchor groups × 2 行 = 10 行，全展
    assert len(out) == 10
    joined = "\n".join(out)
    for i in range(5):
        assert f"[2026-05-25 1{i}:00] Headline {i}" in joined
        assert f"Source: src{i}" in joined
    assert "omitted" not in joined


def test_clip_body_htf_like_indented_continuation():
    """HTF-like: [4h] + 多个缩进续行（MA50 / ATR 各一行）正确归属."""
    from src.cli.display import _clip_body
    body = [
        "[4h] (last closed candle: open 2026-05-25 12:00 UTC)",
        "  MA50: 77920.40 (vs price: -0.49%)",
        "  MA stack: MA50 < MA100 < MA200",
        "  Last bar vol: 1521.6",
        "  ATR(14): 1572.30 (1.92% of price)",
        "[1d] (last closed candle: open 2026-05-25 00:00 UTC)",
        "  MA50: 76747.64",
        "  MA stack: MA50 < MA100 < MA200",
        "  ATR(14): 2050.20",
    ]
    out = _clip_body(body)
    # 2 anchor groups, total 9 行；structured 全展
    assert len(out) == 9
    assert out[0] == "[4h] (last closed candle: open 2026-05-25 12:00 UTC)"
    assert out[5] == "[1d] (last closed candle: open 2026-05-25 00:00 UTC)"


def test_clip_body_omitted_marker_not_recognized_as_anchor():
    """body 含 `[... 11 rows omitted ...]` 不增加 anchor_count（recursive 防护）."""
    from src.cli.display import _clip_body
    body = [
        "row 0", "row 1",
        "[... 11 rows omitted ...]",
        "row 14", "row 15",
    ]
    out = _clip_body(body)
    # 5 行 < n=10 → short mode 全保留
    assert out == ("row 0", "row 1", "[... 11 rows omitted ...]", "row 14", "row 15")


def test_clip_body_rich_markup_in_body_no_misdetect_single_line():
    """单行 Rich markup `[bold red]` → anchor_count=1 → fallback list-like / short."""
    from src.cli.display import _clip_body
    body = ["[bold red]Warning[/red]", "row 1", "row 2"]
    out = _clip_body(body)
    # anchor=1 不进 structured；3 行 < 10 → short
    assert out == ("[bold red]Warning[/red]", "row 1", "row 2")


def test_clip_body_empty_body():
    """空 body → 空 tuple."""
    from src.cli.display import _clip_body
    assert _clip_body([]) == ()
    assert _clip_body(()) == ()


def test_clip_body_pure_prelude_no_anchor():
    """body 全 non-anchor (prelude only, no anchor) → 每行单独 group, anchor_count=0 → list-like 或 short."""
    from src.cli.display import _clip_body
    body = ["Last: 77540.00", "MA: above", "Columns: ..."]
    out = _clip_body(body)
    # anchor_count=0 → 3 行 < 10 → short mode 全保留
    assert out == ("Last: 77540.00", "MA: above", "Columns: ...")
```

- [ ] **Step 2: 跑全部 _clip_body 测试**

```bash
pytest tests/test_display_cycle.py -k "clip_body or is_anchor or group_by_anchor" -v
# Expected: 13+ passed (含 drift guards / structured / cap-exceeded / edge cases)
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
test: edge cases for _clip_body structured-row mode

iter-session-log-structured-clip Step 5/N:
- news-like date anchor (含空格不被 [^\\]\\s] 误判)
- htf-like indented continuation (多缩进续行归属)
- omitted marker recursive 防护
- rich markup 单行 fallback (依赖 design invariant tools_perception.py
  不输出 Rich markup)
- empty body
- pure prelude no-anchor

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Snapshot 影响面 verify + regen

**Goal:** 跑全测，找出预期会变的 snapshot 测试，更新断言。

**Files:**
- Modify: `tests/test_display_cycle.py`（更新 `test_snapshot_get_market_news_dense_general_news_clipped` 和其他受影响 snapshot）

- [ ] **Step 1: 跑全部 test_display_cycle.py 测试，识别 FAIL**

```bash
pytest tests/test_display_cycle.py --tb=short 2>&1 | grep -E "FAILED|ERROR" | head -20
```

记录所有 FAIL test name。预期至少 `test_snapshot_get_market_news_dense_general_news_clipped` 会 FAIL（原断言基于 D4 head=2/tail=2，新行为是全展 24 行）。

- [ ] **Step 2: 查看 news dense snapshot 现有断言并理解新行为**

```bash
sed -n '2248,2266p' tests/test_display_cycle.py
```

现有断言：
```python
assert "    [2026-05-03 100:00] Headline 0" in out  # head[0]
assert "      Source: src0 | Currencies: ALT0" in out  # head[1]
assert "    [... 20 rows omitted ...]" in out
assert "    [2026-05-03 111:00] Headline 11" in out  # tail[-2]
assert "      Source: src11 | Currencies: ALT11" in out  # tail[-1]
```

新行为：12 anchor groups = cap=12 严格相等 → 全展 24 行（每 entry 1 anchor + 1 Source 续行）。

- [ ] **Step 3: 更新 news dense snapshot 断言**

修改 `tests/test_display_cycle.py:2248-2266`，把断言改为全展期望：

```python
def test_snapshot_get_market_news_dense_general_news_clipped():
    """Snapshot — General Crypto News with 12 entries triggers structured-row
    mode full expansion (anchor_count=12 == cap; prelude=0 → groups=12).
    
    Previously D4 head=2/tail=2 clipped to 5 lines; new heuristic全展 24 lines."""
    entries = []
    for i in range(12):
        entries.append(f"[2026-05-03 1{i:02d}:00] Headline {i}\n  Source: src{i} | Currencies: ALT{i}")
    content = "=== General Crypto News (12) ===\n" + "\n".join(entries)
    from src.cli.display import _render_tool_body
    out = _render_tool_body("get_market_news", content)
    assert "    === General Crypto News (12) ===" in out
    # All 12 entries fully expanded (no omitted marker)
    for i in range(12):
        assert f"[2026-05-03 1{i:02d}:00] Headline {i}" in out, f"entry {i} missing"
        assert f"Source: src{i} | Currencies: ALT{i}" in out, f"source {i} missing"
    assert "omitted" not in out, "no clip expected (groups=12 == cap)"
```

注：函数名包含 `clipped` 但新行为是 full expansion；可考虑改名 `test_snapshot_get_market_news_12_entries_full_expansion`，但更名 churn 暂缓 — 用 docstring 注释新行为。

- [ ] **Step 4: 跑全部相关 snapshot 测试**

```bash
pytest tests/test_display_cycle.py -k "snapshot" -v 2>&1 | tail -30
```

若有其他 FAIL（如 mts/htf 的 snapshot），逐个 verify：
- 看 mock content 是否含 `[anchor]` prefix
- 若含 → 新行为是 full expansion，更新断言
- 若不含 → 行为应不变（drift），检查是否误改

- [ ] **Step 5: Commit**

```bash
git add tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
test: update snapshot for market_news 12-entry full expansion

iter-session-log-structured-clip Step 6/N:
- test_snapshot_get_market_news_dense_general_news_clipped:
  12 [date] anchor groups = cap=12 严格相等 → 全展 24 行
  (前 D4 head=2/tail=2 clip 到 5 行)
- 其他 snapshot 测试 verify 不变 (无 anchor prefix / mock content 未变)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 全测 + Drift Guard Invariant Test

**Goal:** 跑完整 test suite，确认 baseline + new tests 全 pass，加 1 个 grep-based invariant test 验证 tools_perception.py 无 Rich markup。

**Files:**
- Test: `tests/test_display_cycle.py` (1 新增 invariant test)

- [ ] **Step 1: 加 invariant test for tools_perception.py 不输出 Rich markup**

```python
def test_drift_guard_tools_perception_no_rich_markup():
    """drift guard: tools_perception.py 不在 row body 中输出 Rich markup.

    Design invariant — _clip_body anchor detection 不能区分 [bold] 这种 Rich
    markup 与真正的 [<word>] anchor，所以工具层必须避免输出 Rich markup。
    若未来引入，需配套调整 heuristic 或在 escape 之前对 body 做预清洗。
    """
    import re
    from pathlib import Path

    rich_markup_pattern = re.compile(
        r"\[(bold|red|green|cyan|magenta|yellow|dim|italic|underline|reverse|strike|blink)\]"
    )
    src_path = Path("src/agent/tools_perception.py")
    content = src_path.read_text()
    matches = rich_markup_pattern.findall(content)
    assert not matches, (
        f"tools_perception.py contains Rich markup tokens {matches}; "
        "violates _clip_body anchor heuristic invariant (per design spec §6.3). "
        "Either remove Rich markup or escape it before _render_tool_body."
    )
```

- [ ] **Step 2: 跑全测**

```bash
pytest tests/ --tb=short 2>&1 | tail -5
# Expected: N+15 passed (15 = 13 unit + 2 drift guard + 1 invariant test, snapshot 已 regen)
# 应该接近 baseline + 15
```

记录最终 pass count，与 §7.4 spec 预期对比。

- [ ] **Step 3: 跑 ruff / pylint 等 linter（若项目用）**

```bash
# 检查项目是否用 ruff / pylint
ls .ruff.toml pyproject.toml setup.cfg 2>/dev/null
ruff check src/cli/display.py tests/test_display_cycle.py 2>&1 | tail -10 || echo "ruff not installed/configured"
```

修复任何 lint 报错。

- [ ] **Step 4: git diff 复审**

```bash
git diff src/cli/display.py | head -100
git diff --stat
```

确认：
- 仅 `src/cli/display.py` 修改（src 改动 ~80 行净增）
- `tests/test_display_cycle.py` 追加 ~150 行
- 无其他文件修改

- [ ] **Step 5: Commit final invariant + verify**

```bash
git add tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
test: drift guard for tools_perception.py Rich markup invariant

iter-session-log-structured-clip Step 7/N:
- _clip_body anchor heuristic 依赖 tools_perception.py 不输出 Rich markup
  这一 design invariant；本 test 在 src 文件中 grep 报错 if 引入
- 完成本 iter 全部测试：1808+15 ≈ 1823 passed (per spec §7.4 预期)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: 实际渲染 smoke verify（manual / optional）

**Goal:** 在 sim 环境跑 1 cycle，view session log 的 mts/htf/news 渲染输出，目视 verify 全展行为符合预期。

**Files:** 无修改

- [ ] **Step 1: 跑 1 个 sim cycle（短）**

```bash
# 用最短 sim 配置跑 1 cycle 产生 session log
# (具体命令视项目 sim runner 而定，可参考 CLAUDE.md 或 scripts/)
# 例如：
# python -m src.cli.main --sim --cycles 1
```

- [ ] **Step 2: 查看最新 session log 中的 mts/htf 渲染**

```bash
ls -lt logs/session_*.log | head -1
LATEST=$(ls -t logs/session_*.log | head -1)
grep -A 20 "⚙ get_multi_timeframe_snapshot" "$LATEST" | head -30
```

Expected: 4 个 `[5m]/[1h]/[4h]/[1d]` anchor + continuation 全部可见（不再有 "[... N rows omitted ...]" 在 mts 区块）。

- [ ] **Step 3: 查看 news 渲染**

```bash
grep -A 30 "⚙ get_market_news" "$LATEST" | head -40
```

Expected: 若 news ≥ 2 条则全展（若 ≤ cap=12）。

- [ ] **Step 4: 无代码改动，无 commit**

本 task 是 manual verification only。若发现行为偏离预期，回到 Task 4-6 重审。

---

## Task 10: PR / Merge Ready

**Goal:** 跑 final 完整测试，prepare push + PR description。

- [ ] **Step 1: Final full pytest**

```bash
pytest tests/ --tb=short 2>&1 | tail -3
# Expected: ~1823 passed, 0 failed, 0 errors
```

- [ ] **Step 2: 整理 commit 历史**

```bash
git log --oneline iter-session-log-structured-clip ^main
```

应该看到 ~8 个 commits：
1. docs: spec for ...
2. test: drift guards for D4 ...
3. feat: add _ANCHOR_RE + _is_anchor helper
4. feat: add _group_by_anchor helper
5. feat: _clip_body structured-row mode full expansion
6. test: cap-exceeded branch coverage
7. test: edge cases for _clip_body structured-row mode
8. test: update snapshot for market_news 12-entry full expansion
9. test: drift guard for tools_perception.py Rich markup invariant

- [ ] **Step 3: Push 到 origin（可选，PR 前确认）**

```bash
# 用户决定何时 push
# git push -u origin iter-session-log-structured-clip
```

- [ ] **Step 4: Open PR 时的 description template**

```markdown
## Summary

session log 渲染层引入 by-anchor heuristic clip：检测 body 中 ≥ 2 行 `[<word>]` prefix 时进入 structured-row mode 全展（cap=12 group level，对称 head=3+tail=3 elide）；否则 fallback 到现有 D4 list-like clip（bit-for-bit 不变）。

5 个 Class A 工具（mts / htf / news / journal / macro_calendar）受益；14 个 Class B 工具行为不变（design invariant）。

## Test Plan

- [ ] 全部 ~1823 tests pass（含 13 新单测 + 2 drift guard + 1 invariant test）
- [ ] sim 1 cycle 渲染 manual verify（mts/htf/news 全展可见）
- [ ] D4 list-like / short mode drift guard tests PASS（行为 bit-for-bit 不变）
- [ ] tools_perception.py Rich markup invariant test PASS

## Spec

`docs/superpowers/specs/2026-05-26-iter-session-log-structured-clip-design.md`
```

---

## Self-Review Checklist

执行完后自查（执行者 last task）：

- [ ] Spec §1.3 Class A 5 工具全覆盖测试（mts via test_structured_row_mode_multi_tf_like / htf via test_htf_like_indented_continuation / news via test_news_like_date_anchor_body / journal & macro_calendar 共享 anchor pattern 测试通过）
- [ ] Spec §2.3 R1-R4 rules 全覆盖
- [ ] Spec §2.4 cap=12 + head=3/tail=3 symmetric 在 cap-exceeded test 中验证
- [ ] Spec §5.3 边界 case 全部覆盖（empty / rich markup / omitted marker / single anchor / prelude）
- [ ] Spec §6.1 fail-open: anchor_count < 2 fallback 到 list-like/short 在 test_single_anchor_fallback_to_list_like 验证
- [ ] Spec §7.5 local checklist 全部跑过
- [ ] 无 placeholder / TBD / 未实现 step
- [ ] commit history 干净（每 task 1 commit，小步前进）
