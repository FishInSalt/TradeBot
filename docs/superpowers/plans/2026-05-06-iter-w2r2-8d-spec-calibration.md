# Iter W2 R2-8d — Spec Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 R2-8d 6 件套 spec/persona 校准（D1-D6），使 spec 与 LLM 实际行为一致，启动 W2 24-48h 真实观察期前修订完成。

**Spec:** `docs/superpowers/specs/2026-05-06-iter-w2r2-8d-spec-calibration-design.md` (commit `a26f23f`, 533 行 / 5 轮 review 浓缩)

**Branch:** `feature/iter-w2r2-8d-spec-calibration` (已 checkout, spec 已 commit)

**Predecessor:** PR #38 (R2-8b N10 cycle summary injection MVP) merged `28f7265` 2026-05-06 (1210 tests pass)

**Successor:** R2-9.5 短 smoke (5-10 cycles user 自跑) → W2 启动

---

## File Structure

| 文件 | 操作 | 责任 |
|---|---|---|
| `src/agent/persona.py` | Modify | D1+D2+D4+D5 persona text rewrite + D3 常量调整（HARD_CAP 4000 / 删 SOFT_CAP）|
| `src/cli/app.py` | Modify | D3 import 调整 + `_truncate_decision` 简化（删 INFO band）|
| `src/cli/display.py` | Modify | D6 `_render_reasoning` max_chars 2000→15000 |
| `tests/test_persona.py` | Modify | 删 1 (cap_numbers) + 改 2 (5_field_anchors / lists_critical_events) + 加 4 (cognitive_flow_framing / field_order_delta_before_thesis / length_guidance_phrases_present / no_legacy_fiction_or_system_aware_phrases) |
| `tests/test_cycle_summary_injection.py` | Modify | 删 1 (INFO band test) + 改 5 (4 truncate cap 数字 + 1 docstring stale) |
| `tests/test_display_cycle.py` | Modify | 改 3 (T-INT-3a docstring + T-INT-3b rename + render_reasoning_over_800 docstring) |

预期净改 (per-file 实测):
- `src/agent/persona.py`: ~30 行 (text 段重写)
- `src/cli/app.py`: ~5 行 (import + truncate 简化)
- `src/cli/display.py`: ~3 行 (max_chars + docstring)
- 测试: ~85 行 (4 新 drift guards + 5 改 truncate + 3 改 thinking + 2 docstring + 删 2)
- **总 ~38 src + ~85 test = ~123 行净改**（spec §8.5 写 ~230 是估算偏高 ~2x，本 plan 校准为实际 per-file 拆解值）；测试净 +2 (1210 → ~1212 passed, 1213 → ~1215 collected)

**实施依赖**：T1 → T2（T1 移除 persona 中 `{CYCLE_DECISION_SOFT_CAP}` + `{CYCLE_DECISION_HARD_CAP}` **两处** f-string 插值后 T2 才能安全删 SOFT_CAP 常量 + 改 HARD_CAP 值；中间状态不破）→ T3（独立 D6）→ T4（final verification）

---

## 实施顺序总览

| Task | 主题 | 关键产出 | 估算 |
|---|---|---|---|
| T1 | persona.py text 重写 (D1+D2+D4+D5) + persona drift guards | persona §Cycle Closing Summary 段 ~30 行净改 + 4 新 drift guards / 2 改 / 1 删 | ~30 src + ~50 test |
| T2 | persona constants (D3) + cli/app.py truncate 简化 + cycle_summary_injection 测试 | 常量块 ~5 行 / cli/app.py ~5 行 / 5 改 truncate test + 1 删 INFO band test | ~10 src + ~20 test |
| T3 | display.py thinking cap (D6) + thinking 测试 | max_chars 默认值 + 3 改 docstring/rename | ~3 src + ~10 test |
| T4 | Final verification + AC self-check + R2-9.5 prep | full suite ~1212 passed + AC mapping + manual smoke instructions | manual |

每 task 独立 commit，subagent-driven mode 每 task 三段（implementer → spec-reviewer → code-reviewer）。

---

## Task 1: persona.py text 重写 + drift guards (D1+D2+D4+D5)

**Files:**
- Modify: `src/agent/persona.py` (重写 `_build_layer1` 中 `## Cycle Closing Summary` 整段)
- Modify: `tests/test_persona.py` (1 删 + 2 改 + 4 加)

**关键约束**:
- 仅改 `## Cycle Closing Summary` 段（line 82 起 至 anti-instruction guard 段末）；**不动**：常量声明（T2 处理）/ Cross-Tool Behavior 6 bullets / Layer 2/3 / RuntimeConfig 类
- 移除 persona 中 `{CYCLE_DECISION_HARD_CAP}` f-string 插值（D5 决议不暴露）

### - [ ] Step 1.1: Update `tests/test_persona.py` — 删 cap_numbers + 改 2 + 加 4

按 spec §5.1 / §5.2 / §5.3：

**删除**: `test_cycle_closing_summary_exposes_cap_numbers`（PR #38 phrase-anchor `~600 chars / ~800 / ~1200` R2-8d D4 全撤后必失败；与新 length_guidance_phrases drift guard 重复 → 直删）

**更新 2 个**:

`test_cycle_closing_summary_contains_5_field_anchors`：
- 旧: `(3) Thesis & invalidation` / `(4) This cycle delta`
- 新: `(3) This cycle delta` / `(4) Thesis & invalidation`（D2 序互换；`(1)/(2)/(5)` 不变）

`test_cycle_closing_summary_lists_critical_events`：
- 旧: 含 `"Critical events include:"` literal
- 新: D5 改写后该 phrase 不存在；改断言 assert `"critical events"` (lowercase, in 词数 ceiling 段) + 4 enum substring (`"open/close"` / `"alert with action"` / `"thesis transition"` / `"macro event proximity"`) 仍存在

**新增 4 个 drift guards**:

```python
def test_cycle_closing_summary_lead_uses_cognitive_flow_framing():
    """T-D1: lead 必须用 cognitive flow framing (After your reasoning... record),
    防回滚 summary-centric "The summary IS the final response" 措辞。"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    assert "After your reasoning and any tool calls, record" in layer1


def test_cycle_closing_summary_field_order_delta_before_thesis():
    """T-D2: D2 序互换 - (3) This cycle delta 必须在 (4) Thesis & invalidation 之前。
    truncation 兜底序保护反思段。"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    pos_delta = layer1.find("(3) This cycle delta")
    pos_thesis = layer1.find("(4) Thesis & invalidation")
    assert pos_delta > 0 and pos_thesis > 0, "anchors missing"
    assert pos_delta < pos_thesis, f"D2 序错: delta@{pos_delta} >= thesis@{pos_thesis}"


def test_cycle_closing_summary_length_guidance_phrases_present():
    """T-D5: length guidance 4 phrases (词数 ceiling + 内容驱动省略快捷方式)。"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    assert "400 words" in layer1
    assert "never exceeding 600 words" in layer1
    assert "single sentence is sufficient" in layer1
    assert "Skip if no relevant observations" in layer1


def test_cycle_closing_summary_no_legacy_fiction_or_system_aware_phrases():
    """T-D4+D5: persona NOT 含 legacy fiction 数字 + 系统机制揭示短语。
    防全部回滚到 PR #38 形态。"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    forbidden = [
        "~600 chars",  # D4 撤
        "~800",        # D4 撤
        "~1200",       # D4 撤
        "Aim for",     # D4 撤 wishful target framing
        "is typically 1-3 sentences",  # D5 撤 per-field cap fiction
        "hard-truncates",              # D5 撤系统机制揭示
        "## SKIP",                     # D1 不引入 SKIP fallback
        "The summary IS the final response",  # D1 撤 summary-centric priming
        "~4000",       # D5 HARD_CAP 不暴露 (用 "~4000" anchor 而非 bare "4000"
                       # 避免与未来 RuntimeConfig 大数值字段 false-positive 冲突)
    ]
    for phrase in forbidden:
        assert phrase not in layer1, f"forbidden phrase leaked: {phrase!r}"
```

### - [ ] Step 1.2: Run persona tests to verify they fail

```bash
pytest tests/test_persona.py -v -k "cycle_closing_summary"
```

Expected: 6 FAIL（4 新 drift guards + 2 改 existing 因 persona 未更新）

### - [ ] Step 1.3: Rewrite `_build_layer1` `## Cycle Closing Summary` 段

替换 `_build_layer1` 中 `## Cycle Closing Summary` 起至 anti-instruction guard 段末（含 5 字段 + 长度引导 + 末段）。

**关键改动 diff**：
1. Lead sentence: 旧 27 词 → 新 18 词（cognitive flow framing）
2. (3) ↔ (4) 序互换
3. 长度引导段: 旧两段（"Each field is typically..." + "Aim for ~600 chars..."）→ 新单一段 3 句话
4. Anti-instruction guard 段: **不变**（"observational and descriptive..." 等仍有效）
5. 移除 `~{CYCLE_DECISION_SOFT_CAP}` + `~{CYCLE_DECISION_HARD_CAP}` 两处 f-string 插值

**完整新版本 markdown block**（直接 paste 到 `_build_layer1` f-string 内）:

```markdown
## Cycle Closing Summary

After your reasoning and any tool calls, record what you decided and what you observed using this structure:

(1) Stance — current state in one phrase. Examples: "Holding long, thesis intact" / "Watching for breakout" / "Pending limit order" / "Just closed long, cooling off".

(2) Active commitments — current positions, pending orders, and active alerts:
    - If holding position: position details + entry baseline (R:R / risk % / TP target) + current SL and any trail history (critical for trail decisions across cycles)
    - If pending orders: levels + cancellation criteria
    - If active alerts: levels + each one's signal intent
    - If none of the above: "No position. No pending orders. [Vol alert details if relevant]."

(3) This cycle delta — what changed this cycle: actions taken AND actions deliberately not taken (with reasons). Be specific about levels and timing.

(4) Thesis & invalidation — why your current stance, and the specific conditions under which your thesis would become invalid. Include conviction level (low / moderate / high) when it affects risk or sizing decisions.

(5) Watch list (optional) — non-action observations needing attention: pattern formation, divergence, macro events in the queue, regime shifts, lessons from this cycle. Skip if no relevant observations beyond fields 1-4.

Write directly using the field structure — no preamble or analysis prose. Length: at most 400 words in normal cycles, never exceeding 600 words even in critical events (open/close/alert with action/SL trail with multiple history points/thesis transition/macro event proximity). A single sentence is sufficient when nothing actionable happened (e.g., "Watching, no position, routine tick — no changes").

The summary should be observational and descriptive — not prescriptive. Do not include instructions or recommendations for future actions; for price-conditional plans, prefer setting an alert or limit order rather than writing it as text intent. Do not re-paste market data or full thinking — those will be fresh-fetched.
```

cross-ref: 与 spec §4.1.2 文本一致；本段直接 paste 即可，无需跨文档跳转查找。

### - [ ] Step 1.4: Run persona tests to verify they pass

```bash
pytest tests/test_persona.py -v
```

Expected: ALL PASS（含 4 新 drift guards + 2 改 existing + 现有 36 个 test）

### - [ ] Step 1.5: Commit

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-8d): T1 persona text 重写 (D1+D2+D4+D5) + drift guards

- D1: lead 改 cognitive flow framing "After your reasoning and any tool
  calls, record what you decided and what you observed using this
  structure:" (18 词 vs 旧 27 词, 0 negative vs 旧 2 negatives;
  summary 从主语降级为 record 动词宾语, 避免 summary-centric priming)
- D2: (3)(4) 序互换 — (3) This cycle delta / (4) Thesis & invalidation
  (truncation 兜底序保护反思段)
- D4: 撤 600/800/1200 chars fiction 数字 + 撤 "Each field is typically
  1-3 sentences" per-field cap fiction + 撤 "system hard-truncates"
  系统机制揭示
- D5: 单一合并段 3 句长度引导 (Write directly + 词数 ceiling ≤400/≤600
  + single-sentence quiet cycle escape hatch); agent 自主分配每字段
  长度按内容密度

测试改动:
- 删 1: test_cycle_closing_summary_exposes_cap_numbers (PR #38 旧
  phrase-anchor 全撤后必失败 + 与新 length_guidance drift guard 重复)
- 改 2: contains_5_field_anchors (D2 序互换) + lists_critical_events
  (phrase 重构括号形式)
- 加 4: lead_uses_cognitive_flow_framing / field_order_delta_before_thesis
  / length_guidance_phrases_present / no_legacy_fiction_or_system_aware_phrases

Spec §3 D1+D2+D4+D5 / §4.1.2 / §5.1+§5.2+§5.3 (T-D1-D5).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: persona 常量 (D3) + cli/app.py truncate 简化 + tests

**Files:**
- Modify: `src/agent/persona.py` (常量块 lines 6-10 区域)
- Modify: `src/cli/app.py` (line 14-16 import + `_truncate_decision`)
- Modify: `tests/test_cycle_summary_injection.py` (5 改 + 1 删)

**关键约束**:
- 在 T1 已 landed 后执行（persona 文本已不引用 `{CYCLE_DECISION_HARD_CAP}`，T2 改常量不破文本）
- `_truncate_decision` 删 INFO band 分支（无 SOFT_CAP 后无信号）

### - [ ] Step 2.1: Update `tests/test_cycle_summary_injection.py`

**删除 1 个**:
- `test_truncate_decision_in_soft_to_hard_band_keeps_full_with_info_log`（SOFT_CAP 删除后 case 不存在；`_truncate_decision` 无 INFO log 路径）

**改 4 truncate test 数据 (1200/800 → 4000)**:

| 测试 | 旧 | 新 |
|---|---|---|
| `test_truncate_decision_below_soft_cap_returns_unchanged` | rename 为 `test_truncate_decision_below_hard_cap_returns_unchanged`；text=`"x"*500` 保持 | （已 rename 含义改）|
| `test_truncate_decision_above_hard_cap_truncates_with_marker_and_warning` | hard_cap=1200 测 1500 超 | hard_cap=4000 测 4500 超 |
| `test_truncate_decision_does_not_truncate_at_exactly_hard_cap` | n == 1200 不截 | n == 4000 不截 |
| `test_render_truncates_decision_above_hard_cap_via_truncate_decision` | 1500 chars 触发截断 | 4500 chars 触发截断 |

**改 1 docstring stale**:
- `test_render_keeps_full_decision_below_cap`: docstring `"T2.4: ≤ 1200 chars, no truncation marker"` → `"T2.4: ≤ HARD_CAP (4000) chars, no truncation marker"`；body=800 仍 pass，断言不动

### - [ ] Step 2.2: Run tests to verify they fail

```bash
pytest tests/test_cycle_summary_injection.py -v -k "truncate"
```

Expected: **3 FAIL** + 其余 PASS（详细 breakdown）:

| 测试 | 现状 (cap=1200) | 改后断言 (cap=4000) | 验证结果 |
|---|---|---|---|
| `_below_hard_cap_returns_unchanged` (renamed) | text=`"x"*500` 不截 | 同上断言不变 | **PASS**（500 < 1200 仍不截）|
| `_above_hard_cap_truncates_with_marker_and_warning` | 测 1500 截到 1200 | 测 4500 截到 4000 | **FAIL**（log "1200" ≠ "4000"）|
| `_does_not_truncate_at_exactly_hard_cap` | n=1200 不截 | n=4000 不截 | **FAIL**（4000 > 1200 当前会被截）|
| `_render_truncates_..._via_truncate_decision` | 1500 触发 log "1200" | 4500 触发 log "4000" | **FAIL** |
| `test_render_keeps_full_decision_below_cap` | docstring only 改动 | 同 | **PASS** |

Confirm 3 FAIL 是 expected mode，再进 Step 2.3。

### - [ ] Step 2.3: Update persona.py constants

替换 lines 6-10 区域的常量块（spec §4.1.1 verbatim）：

```python
# Old (post-PR#38):
# R2-8b cycle decision caps — single source of truth shared between
# producer (persona §Cycle Closing Summary text below) and consumer
# (cli/app.py _truncate_decision defaults). Changing one updates both.
CYCLE_DECISION_SOFT_CAP = 800
CYCLE_DECISION_HARD_CAP = 1200

# New (R2-8d D3+D5):
# R2-8d cycle decision hard cap — silent system safety net. NOT
# interpolated into persona text (D5: agent reads "never exceeding 600
# words" ceiling and self-controls; char cap protects against
# misbehavior). Used only by cli/app.py:_truncate_decision.
CYCLE_DECISION_HARD_CAP = 4000
# REMOVE CYCLE_DECISION_SOFT_CAP entirely (no longer needed)
```

### - [ ] Step 2.4: Update `src/cli/app.py` import + `_truncate_decision`

**Import (现 line 14-16)**:

```python
# Old:
from src.agent.persona import (
    CYCLE_DECISION_HARD_CAP, CYCLE_DECISION_SOFT_CAP, RuntimeConfig,
)

# New:
from src.agent.persona import CYCLE_DECISION_HARD_CAP, RuntimeConfig
```

**`_truncate_decision` 简化** (spec §4.2.2 verbatim):

```python
def _truncate_decision(
    text: str, hard_cap: int = CYCLE_DECISION_HARD_CAP,
) -> str:
    """Hard-truncate at hard_cap with WARNING log.

    Word ceiling (≤400/≤600 words) exposed to agent via persona §Cycle
    Closing Summary; this char cap is a silent system safety net — NOT
    exposed to agent (R2-8d D5: agent obeys word ceiling, char hard_cap
    kicks in only on misbehavior).
    """
    n = len(text)
    if n > hard_cap:
        logger.warning(
            "Cycle decision exceeded hard cap %d (got %d), truncating",
            hard_cap, n,
        )
        return text[:hard_cap] + " ... [truncated]"
    return text
```

变化：
- 删除 `soft_cap: int = CYCLE_DECISION_SOFT_CAP` 参数
- 删除 INFO log 分支 (`if n > soft_cap: logger.info(...)`)
- 函数体仅保留 hard cap 路径

### - [ ] Step 2.5: Run tests to verify they pass

```bash
pytest tests/test_cycle_summary_injection.py -v
```

Expected: ALL PASS（删 1 + 改 5；其余测试自动通过）

### - [ ] Step 2.6: Sanity grep — 无 SOFT_CAP 残留

```bash
grep -rn "CYCLE_DECISION_SOFT_CAP" src/ tests/
```

Expected: 0 matches（彻底清理）

### - [ ] Step 2.7: Commit

```bash
git add src/agent/persona.py src/cli/app.py tests/test_cycle_summary_injection.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-8d): T2 persona constants (D3) + cli/app.py truncate 简化

- persona.py 常量: CYCLE_DECISION_HARD_CAP 1200 → 4000 (sim #7 73% 超
  1200 cap → 物理需 4000); CYCLE_DECISION_SOFT_CAP 删除 (sim #7 9/11
  cycles 触发 INFO log 已无 drift sentinel 信号; INFO band 路径无 agent-
  facing 价值因 D5 不暴露 SOFT 数字)
- 常量块注释重写: 旧 "shared between producer and consumer" 不再成立
  (R2-8d 后 producer 不 reference HARD_CAP); 新注释明示 "silent system
  safety net, NOT interpolated into persona text"
- cli/app.py import: 删 SOFT_CAP import
- _truncate_decision 简化: 删 soft_cap 参数 + 删 INFO log 分支; 函数体
  仅保留 hard cap WARNING + 截断路径; docstring 显式 "char cap is
  silent system safety net — NOT exposed to agent"

测试改动:
- 删 1: test_truncate_decision_in_soft_to_hard_band_keeps_full_with_info_log
  (SOFT_CAP 删除后 case 不存在)
- 改 4 truncate cap 数字: hard_cap 1200→4000 (3 tests) + render_truncates
  via_truncate_decision (1500→4500)
- 改 1 docstring stale: test_render_keeps_full_decision_below_cap T2.4 引
  ≤ 1200 chars → ≤ HARD_CAP (4000) chars

Spec §3 D3 / §4.1.1 / §4.2.1+§4.2.2 / §5.1+§5.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: display.py thinking cap (D6) + tests

**Files:**
- Modify: `src/cli/display.py` (line ~739 `_render_reasoning` default)
- Modify: `tests/test_display_cycle.py` (3 改 docstring/rename + test data)

**关键约束**:
- 仅改 `_render_reasoning` 默认 max_chars 值 + docstring；不动函数体逻辑

### - [ ] Step 3.1: Update `tests/test_display_cycle.py`

**改 3 tests** (spec §5.2):

`test_int_3_thinking_1500_chars_keep_all` (line 1533):
- 测试本身不改（断言 1500 chars 不截，default cap 任意 ≥ 1500 都 pass）
- 仅 docstring 加注：`"R2-8d D6: default cap 2000→15000; this test still valid as 1500 < both"`

`test_int_3_thinking_2500_chars_truncated_to_2000` (line 1542) — **rename + 改 test data**:
- 新名: `test_int_3_thinking_above_default_cap_truncated`
- 测试 data: `text = "y" * 18000`，期望截到 15000 + ` ... [+3000 chars]` suffix
- docstring 更新引 R2-8d D6

`test_render_reasoning_over_800_truncated` (line 623):
- 测试本身**不改**（explicit `max_chars=800` 参数不动；测 R2-8c boundary 行为仍有效）
- 仅 docstring append：`"; R2-8d D6: 2000 → 15000 default (this test pins R2-8c boundary via explicit max_chars=800)"`

### - [ ] Step 3.2: Run tests to verify they fail

```bash
pytest tests/test_display_cycle.py -v -k "test_int_3_thinking or test_render_reasoning_over_800"
```

Expected: 1 FAIL (`test_int_3_thinking_above_default_cap_truncated` 因 default cap 仍是 2000，18000 chars 截到 2000 不是 15000)；其他 2 PASS（测试本身未变行为）

### - [ ] Step 3.3: Update `src/cli/display.py` `_render_reasoning` default

替换 line ~739 函数签名 + docstring（spec §4.3.1 verbatim）：

```python
# Old:
def _render_reasoning(thinking_text: str, max_chars: int = 2000) -> str:
    """Render Reasoning section per spec §4.2.1-§4.2.2 (R2-8c D10: 800 → 2000).
    ...
    """

# New:
def _render_reasoning(thinking_text: str, max_chars: int = 15000) -> str:
    """Render Reasoning section per spec §4.2.1-§4.2.2.

    R2-8d D6: 2000 → 15000 (sim #7 max 9492, median ~6500;
    2000 cap 实测截断率 ~91% 远超 R2-8c 预测 25%; 15000 覆盖 max + 58% 缓冲
    给 W2 长尾留充分余量, 免后续 N12c hot-fix 调参).
    R2-8c D10 (800 → 2000) 历史: smoke #6 B3 截断率 47/80 = 58.8% @ 800.

    Hard-truncate body to max_chars + ' ... [+N chars]' marker. Body must be
    rich.markup.escape()'d — thinking content is LLM output, attack surface
    of same shape as Decision body.
    """
```

变化：
- `max_chars` 默认值 2000 → 15000
- docstring 重写引 R2-8d D6 + sim #7 实测数据 + 缓冲 rationale + R2-8c 历史

### - [ ] Step 3.4: Run tests to verify they pass

```bash
pytest tests/test_display_cycle.py -v -k "test_int_3_thinking or test_render_reasoning_over_800"
```

Expected: ALL PASS

### - [ ] Step 3.5: Commit

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-8d): T3 display.py thinking cap 2000 → 15000 (D6)

- _render_reasoning default max_chars 2000 → 15000
- sim #7 实测 thinking max 9492 chars / median ~6500 / min 878
- 2000 cap 实测截断率 ~91% 远超 R2-8c D10 预测 25%
- 15000 cap 覆盖 sim #7 max + 58% 缓冲, 给 W2 24-48h 长尾留充分余量
  免后续 N12c hot-fix 调参; WARNING 触发 ≥ 15000 才是真异常信号
- 改动范围仅 session log render (display.py); thinking 已通过
  AgentCycle.reasoning 列完整持久化 (R2-7 schema reframe), R2-8d 不改
  schema/写入路径 (DB 端可全量 SQL 查 reasoning)

测试改动:
- T-INT-3a (1500_chars_keep_all) docstring 加 R2-8d D6 注释 (测试本身仍
  valid: 1500 < both 2000 and 15000)
- T-INT-3b rename: test_int_3_thinking_2500_chars_truncated_to_2000 →
  test_int_3_thinking_above_default_cap_truncated; test data 2500→18000,
  截断 marker 调整 [+500 chars] → [+3000 chars]
- test_render_reasoning_over_800_truncated docstring append D6 注 (测试
  本身未变, explicit max_chars=800 pin R2-8c boundary 仍有效)

Spec §3 D6 / §4.3.1 / §5.2.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Final verification + AC self-check + R2-9.5 prep

**Files:** none modified (除非 Step 6 surface 出 housekeeping fix)

### - [ ] Step 4.1: Run full pytest suite

```bash
pytest -q
```

Expected: **~1212 passed + 3 skipped**（PR #38 baseline 1210 + R2-8d 净 +2）

如出现新失败 → 不能直接进 R2-9.5；先排查根因。

### - [ ] Step 4.2: AC self-check — 静态测试 (AC1.1 - AC1.6)

| AC# | 验证 |
|---|---|
| AC1.1 | `pytest -q` 全套 PASS（~1212 passed + 3 skipped；collected ~1215 含 skipped）|
| AC1.2 | `pytest tests/test_persona.py -v` 全 PASS（含 4 新 drift guards）|
| AC1.3 | `pytest tests/test_cycle_summary_injection.py -v` 全 PASS（新 cap 4000）|
| AC1.4 | `pytest tests/test_agent_cycle_injection.py -v` 全 PASS（注入逻辑不动）|
| AC1.5 | `grep -rn "CYCLE_DECISION_SOFT_CAP" src/ tests/` 返回 0 matches |
| AC1.6 | `grep -rn "~600 chars\|~800 for critical\|~1200" src/agent/` 返回 0 matches |

### - [ ] Step 4.3: 验证 persona text 不含 forbidden phrases (drift guard 静态等价)

```bash
python -c "
from src.agent.persona import _build_layer1, RuntimeConfig
layer1 = _build_layer1(RuntimeConfig())
forbidden = ['~600 chars', '~800', '~1200', 'Aim for', 'is typically 1-3 sentences',
             'hard-truncates', '## SKIP', 'The summary IS the final response', '4000']
hits = [p for p in forbidden if p in layer1]
print('Forbidden phrases in persona:', hits if hits else 'NONE ✓')
"
```

Expected: `NONE ✓`

### - [ ] Step 4.4: 准备 R2-9.5 短 smoke instructions（user 自跑 5-10 cycles）

将以下贴回会话中（user 跑完 R2-9.5 smoke 后用此 mapping 判定 AC2.A pass/fail）：

**R2-9.5 短 smoke gates (AC2.A 绝对计数)**:

```sql
-- AC2.A.7 measurement template (post-smoke 跑此查 substantive cycle 5-field 合规)
-- (1)-(4) 必备; (5) Watch list optional 独立 flag (per D5 内容驱动省略, 不进 pass/fail)
SELECT cycle_id,
  CASE WHEN length(decision) > 500 THEN 'substantive' ELSE 'quiet' END AS type,
  CASE WHEN decision LIKE '%(1) Stance%' AND decision LIKE '%(2) Active commitments%'
            AND decision LIKE '%(3) This cycle delta%' AND decision LIKE '%(4) Thesis%'
            THEN 'has_4_required_anchors'
       WHEN decision LIKE '%(1) Stance%' THEN 'partial_anchors'
       ELSE 'no_anchors_or_single_sentence' END AS structure,
  CASE WHEN decision LIKE '%(5) Watch list%' THEN 1 ELSE 0 END AS has_optional_watch_list,
  length(decision) AS chars,
  substr(decision, 1, 60) AS preview
FROM agent_cycles
WHERE session_id='<R2-9.5_session_id>' AND execution_status='ok'
ORDER BY created_at;
```

**判定规则**（spec §6 AC2.A.7）:
- `substantive AND has_4_required_anchors` → ✅
- `substantive AND not has_4_required_anchors` → ❌ AC fail
- `quiet AND no_anchors_or_single_sentence` → ✅（D1 L2 弹性）
- `has_optional_watch_list` 仅作 W2 观察统计，不进 pass/fail

**AC2.A.1 mid-thinking miss check**:
```sql
SELECT cycle_id, substr(decision, 1, 80) FROM agent_cycles
WHERE session_id='<R2-9.5_session_id>'
AND (decision LIKE 'Let me%' OR decision LIKE 'I need to%' OR decision LIKE 'Done.%Back in%');
```
Expected: 0 rows.

**AC2.A.2 hard truncation count** (WARNING 走 system logger, 落 `logs/system.log`):
```bash
# system log 全局共享 (不是 session-specific); 用时间窗口隔离 R2-9.5 smoke 区间
grep "exceeded hard cap" logs/system.log
```
Expected: 0 行匹配 R2-9.5 smoke 时间区间。

**AC2.A.4 thinking truncation count** (`[+N chars]` marker 走 console + session log):
```bash
# session log 命名格式: logs/session_<session_id>.log (前缀 session_, 在 logs/ 目录)
grep "\\[+.*chars\\]" logs/session_<R2-9.5_session_id>.log
```
Expected: 0 行（15000 cap 覆盖 sim #7 max 9492 + 58% 缓冲）

**注意**: AC2.A.2 的 WARNING 是 Python `logger.warning()` 走系统 logger handler → `logs/system.log`（与 cli/app.py:107 同 logger）；AC2.A.4 的 truncation marker 是 console 渲染输出 → 落 session log（cli/logging_config.py:64 命名格式）。两者不同 sink，不要混用 grep 路径。

### - [ ] Step 4.5: Verify no AGENTS.md / CLAUDE.md drift

```bash
git diff main...HEAD -- '*.md' | head -30
```

Expected: 仅 `docs/superpowers/specs/...8d-spec-calibration-design.md` + `docs/superpowers/plans/...8d-spec-calibration.md` 两份新增 md。

### - [ ] Step 4.6: Final summary commit (only if housekeeping changes)

如 Steps 4.1-4.5 surface 无新 fix 需要 → T4 ends with **no new commit**，直接进入 PR 创建。

如有小 housekeeping fix（例如 docstring typo）：

```bash
git add <changed files>
git commit -m "$(cat <<'EOF'
chore(iter-w2r2-8d): T4 final verification follow-ups

[describe small fix(es)]

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

### 1. Spec coverage check

| Spec section | Implementation locus | Plan task |
|---|---|---|
| §3 D1 (cognitive flow framing + 弹性长度) | `persona.py` lead 改写 + drift guard | T1 |
| §3 D2 ((3)(4) 序互换) | `persona.py` 字段序 | T1 |
| §3 D3 (cap 4000 + 删 SOFT) | `persona.py` 常量 + `cli/app.py` truncate | T2 |
| §3 D4 (撤 600/800/1200/1-3 sentences/hard-truncates fiction) | `persona.py` text + drift guard | T1 |
| §3 D5 (词数 ceiling + 结构主轴) | `persona.py` text 长度引导段 | T1 |
| §3 D6 (thinking cap 15000) | `display.py` max_chars default | T3 |
| §4.1.1 (常量块注释 verbatim) | T2 Step 2.3 | T2 |
| §4.1.2 (persona text verbatim) | T1 Step 1.3 | T1 |
| §4.2.1+§4.2.2 (cli/app import + truncate) | T2 Step 2.4 | T2 |
| §4.3.1 (display max_chars + docstring) | T3 Step 3.3 | T3 |
| §5.1 (删 2 tests) | T1 (cap_numbers) + T2 (INFO band) | T1+T2 |
| §5.2 (改 10 tests) | T1 (2 persona) + T2 (5 truncate) + T3 (3 thinking) | T1+T2+T3 |
| §5.3 (加 4 drift guards) | T1 Step 1.1 | T1 |
| §6 AC1 (静态测试) | T4 Step 4.1+4.2 | T4 |
| §6 AC2 (R2-9.5 smoke gates) | T4 Step 4.4 prep | T4 (user runs smoke) |

无 spec section 漏映射。

### 2. Placeholder scan

无 TBD / TODO / "implement later" / "fill in" placeholders；T4 manual smoke instructions 是显式 user-runnable 步骤而非 placeholder。

### 3. Type / signature consistency

- `_truncate_decision(text: str, hard_cap: int = CYCLE_DECISION_HARD_CAP) -> str` — T2 单一签名，与 spec §4.2.2 一致
- `_render_reasoning(thinking_text: str, max_chars: int = 15000) -> str` — T3 单一签名，与 spec §4.3.1 一致
- `_build_layer1(runtime: RuntimeConfig) -> str` — T1 不动签名，仅改函数体内 f-string 内容

### 4. Imports check

- T1: persona.py 不加新 import（仅改 text）
- T2: cli/app.py 删 `CYCLE_DECISION_SOFT_CAP` import；persona.py 删 `CYCLE_DECISION_SOFT_CAP` 常量声明（也即不再 export）
- T3: display.py 不加新 import（仅改 default 值）
- 测试: 无新 import 需求

### 5. Test count check

- 删 2 (T1 cap_numbers + T2 INFO band)
- 改 10 (T1: 2 persona / T2: 5 truncate / T3: 3 thinking)
- 加 4 (T1: 4 drift guards)
- **净 +2 tests** (passed: 1210 → ~1212; collected: 1213 → ~1215)

与 spec §5.4 一致。

### 6. Risk re-check

- **T1 风险**: persona text 大段重写 (~30 行)，可能影响其他 persona 测试。Mitigation: T1.4 跑 full `tests/test_persona.py` 验证（含原 36 个 tests + 6 R2-8d 相关）
- **T2 风险**: 常量块改动 + cli/app.py import 改动，跨文件耦合。Mitigation: T2 集中处理两处改动 + T2.6 grep sanity 确保 SOFT_CAP 0 残留
- **T3 风险**: max_chars default 改动可能影响其他用 _render_reasoning 的代码路径。Mitigation: T3.4 跑 display 测试 + T4.1 full suite 验证
- **T4 风险**: R2-9.5 smoke 由 user 自跑，AC2.A 判定依赖 user 数据。Mitigation: T4.4 提供 SQL/grep 模板使 user 可独立判定

### 7. What's NOT covered by this plan (explicit OOS)

- ❌ Per-field char/word cap enforcement
- ❌ Code-level format validation (rely on persona prompt + L1 framing)
- ❌ SKIP fallback 出口
- ❌ Token-based cap (chars 仍是系统单位)
- ❌ HARD_CAP agent-exposure (silent system safety net)
- ❌ R2-8b 注入逻辑 / agent_cycles schema / pydantic-ai message_history 任何改动

详见 spec §7 OOS 9 项。

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-06-iter-w2r2-8d-spec-calibration.md`.**

**Two execution options:**

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task (T1 → T2 → T3 → T4)，三段 review (implementer + spec-reviewer + code-reviewer)，与 R2-7/R2-8a/R2-8b 同模式
2. **Inline Execution** — execute tasks 在当前会话用 `superpowers:executing-plans`，batch 执行带 checkpoints
