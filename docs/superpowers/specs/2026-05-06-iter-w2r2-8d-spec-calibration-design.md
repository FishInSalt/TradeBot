# Iter W2 R2-8d — Spec Calibration Based on R2-9 Smoke (sim #7)

**Status**: spec draft (lean 版本，brainstorm 已 done in dialogue 2026-05-06)
**Branch**: `feature/iter-w2r2-8d-spec-calibration`
**Predecessor**: PR #38 (R2-8b N10 cycle summary injection MVP) merged `28f7265` 2026-05-06
**Smoke source**: BTC sim #7 `e8a27d90-f722-4929-89eb-e9bf7e1060a3`, 11 cycles 1h16min, 2026-05-06

---

## §1 Goal

R2-8b 实现层无 bug（PR #38 已 merge，1210 tests pass，AC30 注入端到端 ✅），但 R2-9 smoke 实证暴露 6 项 **spec/persona 校准议题**。R2-8d 打包 6 件套修复，使 spec 与 LLM 实际行为一致，避免下个 W2 24-48h 长跑被 spec drift 污染。

**核心定位**: spec 校准 follow-up，不是 R2-8b 实现修复。改动范围窄（persona text 重写 + 2 个常量调整 + `_truncate_decision` 简化 + thinking cap 调高 + 测试同步）。

## §2 议题源由 / 实证数据

### 2.1 sim #7 关键 metrics

| 指标 | sim #7 实测 | spec 预设 | 偏差 |
|---|---|---|---|
| Decision 长度（chars）分布 | 9 个 1200-1708 / 2 个 ~150 outlier | "Aim for ~600 chars" target | 实测中位数远超 target，target 是 fiction |
| Decision 词数分布 | 189-272（critical 峰 272） | 隐含 "~95 词" target (600 chars / 6.3 c/w) | target 不可达 |
| HARD_CAP 触发率 | 8/11 (73%) 超 1200 chars | rare safety net | cap 是常态而非例外 |
| 5-field 格式遵守率 | 9/11 (82%) | 100% | 18% miss = cycle 4/cycle 11 思考开场白污染下个 cycle 注入 |
| c/w (chars/word) | 6.3-6.4 | （未论证）| 高于普通英文 5.0 — 价格/ID/简写贡献 |
| Thinking 长度（chars） | min 878 / median ~6500 / max 9492 | 2000 cap (R2-8c D10) | 实测截断率 ~91% 远超 R2-8c 预测的 25% |
| Decision (4) Delta truncation | 8/11 cycles (4) Delta 字段尾部被砍 | 不该常发生 | 反思段（"Did NOT do X because Y"）跨 cycle 最有价值，被截 |

### 2.2 6 件套议题映射

| # | 议题 | 实证证据 |
|---|---|---|
| **1** | 5-field 格式不强制，18% non-summary miss | cycle 4 (62d66966, 153 chars `"Done. Position holding... Back in 5."`) + cycle 11 (186c344c, 146 chars `"Let me assess the post-breakdown landscape..."`) |
| **2** | (4) Delta 字段尾部被截 | cycle 6 (1571 chars) 截到 "compression patterns often fake-breakdown..." 中断；(4) 包含跨 cycle 最有价值的反思 |
| **3** | HARD_CAP=1200 与 5-field 载实物理冲突 | 第 1 cycle (无 priors / 无仓位) 已 1204 chars；critical event (cycle 2 开仓) 1708 chars |
| **4** | 600/800 chars target 是 fiction | 600 词 × 6.3 c/w = 3780 chars 才是物理下限 |
| **5** | chars 是 LLM 不友好单位 | LLM 字符级计数能力差；agent 看到 "1200 hard" 倾向写到上限（cycle 1 边界踩 1204） |
| **6** | Thinking cap 2000 对 sim #7 数据严重过紧 | thinking max 9492 / median 6500 → 2000 cap 实际截断率 ~91% (R2-8c D10 预测 25%) |

## §3 决议（D1-D6）

### D1 — Cognitive flow framing + 弹性长度（议题 #1）

**问题**: sim #7 实证两类 miss 模式：
- **cycle 4 类（格式偷懒）**: 持仓状态完整，agent 思考 5465 chars + 调 7 个 tool，但 decision 只写 153 chars `"Done. Position holding... Back in 5."` — 该 5-field 载实但只写了 1 句
- **cycle 11 类（未 conclude）**: alert 后 thinking 仅 878 chars，decision 写 146 chars `"Let me assess the post-breakdown landscape..."` — agent 停在 mid-thinking 未收尾

两类都不是"无内容可总结"，是 format 失控；R2-8b 注入下个 cycle 时把这些 mid-thinking 文本当 prior summary 注入，形成上下文污染。

**修法（双层约束）**:

1. **L1（认知 flow framing）— Persona lead 改写**:
   ```
   旧（PR #38，summary-centric priming，27 词，含 2 negatives）:
   "Your final response must be a concise cycle summary covering five elements (do not produce an analysis followed by a summary — the summary IS the final response):"

   新（reasoning + action → record，summary 是动作的痕迹，18 词，0 negative）:
   "After your reasoning and any tool calls, record what you decided
   and what you observed using this structure:"
   ```

2. **L2（格式弹性）— 允许两种 form**:
   - **5-field 结构**：substantive cycles 默认（开仓/平仓/alert with action/SL trail/thesis transition/macro proximity）
   - **Single sentence**：quiet cycles（routine check / 无仓 / 无事件 / 无 thesis 变化），e.g., `"Watching, no position, routine tick — no changes."`

3. **不允许的形态**（隐式约束，不写 negative，靠 L1 framing + 结构 anchor 自然防）:
   - mid-thinking text（"Let me assess..." / "I need to check..." / "Back in 5"）
   - analysis 末尾未 conclude
   - 分析散文 + 重复 summary 双叙事（token waste）
   - **不引入 SKIP 出口** — sim #7 misses 不是"无内容"，给 SKIP 反而是错答案（agent 学会把它当偷懒出口）

**Why**:
- L1 把 summary 从主语降级为副词性 "record what you decided and observed" — 认知顺序对齐 reason → act → record，summary 是动作痕迹不是认知焦点
- L2 弹性长度允许 cycle 4 类用 1 句子合规（"Holding long, thesis intact, watching MA20 bounce" 14 词）；同时 substantive cycle 仍写 5-field
- 主语回到 agent 自己（"your reasoning and any tool calls"），无系统视角揭示，避免 perform-for-audience 风险（与 R2-8b round 2 F1 anti-instruction guard 同源原则）
- 单一 output mode（无 SKIP 边角），下游 query/inject/render 不需特殊分支

**预期效果**:
- R2-9.5 短 smoke (5-10 cycles) 期望 **0 mid-thinking miss**（AC2.A.1，response 必须是 summary 形态而非 mid-analysis text）
- 长期 W2 24-48h 监控目标 mid-thinking miss rate < 5%（AC2.B.1）

### D2 — Field 序保护 (4) Delta（议题 #2）

**问题**: HARD_CAP 触发时，truncation 砍尾部；当前序 (1)Stance / (2)Active / (3)Thesis / (4)Delta / (5)Watch list (optional)，agent 自顶向下写至中段被截，(4) Delta 尾部"Did NOT do X because Y"反思丢失。

**Options**:
- **A 重排**: 把 (3) 和 (4) 互换 → (1) Stance / (2) Active / (3) Delta / (4) Thesis / (5) Watch list
- **B 保持序，加优先级提示**: 加 "if length-constrained, prioritize fields 1-3 over 4-5"
- **C 不动**: HARD=4000 后 truncation 罕见，无需调整

**决议**: **Option A**（重排）

**Why**:
- A 改动最小（仅文档序变化），最持久（不依赖 agent 长度感知）
- (3) Delta 在前的叙事流也合理："I'm in state X (Stance) holding Y (Active), this cycle I did Z (Delta), here's why I'm in this state (Thesis), and here's what to watch (Watch list)"
- B 依赖 agent 自我管理 length，data 已显示 agent 不善 char counting
- C 在新 cap 下 truncation 罕见但不为零；保护反思段是低成本好习惯
- 优先 truncation hits (5) Watch list (optional)，丢失 acceptable

**Trade-off 讨论（与 D3 cap=4000 联动评估）**:

D3 后 truncation 罕见（sim #7 max 1708 chars / cap 4000 / 134% 缓冲），D2 belt-and-suspenders 直接收益降低。需 weigh 一项 narrative-flow 副作用：

- **副作用**: (3) Delta 在 (4) Thesis 之前 = "我做了 Z 因为 X" 序，相比旧序 "因为 X 所以做了 Z" 可能微弱诱导 post-hoc rationalization 模式（先做后找理由）
- **作用范围**: 跨 cycle 注入（R2-8b）时 agent 读 prior summaries 可能 prime 思维顺序；agent 当前 cycle 内决策已发生在 thinking + tools 期间，summary 是 record 非 generation，影响有限
- **subtle 程度**: 真实但弱效；trader 实战 journal 两序都常见（"action then reasoning" vs "thesis then action"），无强统计支持哪个更优

**决议保留 Option A** 但 acknowledge 副作用：
- truncation 罕见时 Delta 保护是 free upside（rare 触发但触发时反思段最有价值）
- post-hoc rationalization risk 弱效且不可预测，subagent 跨 cycle 注入是新 pattern（R2-8b 落地 < 1 周）暂无实证
- W2 24-48h 监控 cycle 4+ injected priors 后 agent 行为模式（特别是 thesis 重述 vs action-first 占比），W2 末若发现明显 post-hoc 倾向 → R2-8e 候选回退 Option B 或调 prompt

### D3 — Cap 调高 + 去 SOFT_CAP（议题 #3）

**改动**:
- `CYCLE_DECISION_HARD_CAP`: **1200 → 4000 chars**
- `CYCLE_DECISION_SOFT_CAP`: **删除**（不再 export 也不再 import）
- `_truncate_decision` 删 INFO band 分支，仅保留 hard cap WARNING + truncation 路径

**Why**:
- HARD_CAP=4000：覆盖 600 词 ceiling 在 sim #7 实测 c/w 6.21-6.39 范围（3726-3834 chars + 4-7% 缓冲）；极端密度 c/w=7.0 时容量 4000/7.0=571 词（不到 ceiling 的 95.2%），剩 4.8% 在边界情况可能截断 — acceptable as last-resort safety net behavior（agent obey 600 词 ceiling 时极少触线）
- SOFT_CAP 已无信号：sim #7 9/11 (82%) 触发 INFO log → drift sentinel 失效
- INFO band 路径设计动机是 "agent 看到 SOFT 当 target" → 现在去除 SOFT 暴露后路径无价值
- 简化代码：单一 threshold 单一 log level，逻辑清晰

**Token 经济**:
- 新 cap 下注入 block 平均估 ~1500 tokens/cycle (vs PR #38 ~700/cycle)
- 增量 +800 tokens/cycle × ~100 cycles/day = 80k/day = W1 daily budget 7.3M 的 ~1.1%
- 可接受 — 远低于 W1 cache_miss 区其他来源（4k tokens message_history 占 44%）

### D4 — 撤 600/800 chars fiction（议题 #4）

**改动**: Persona text 完全去掉 "Aim for ~600 chars (up to ~800 for critical events; the system hard-truncates beyond ~1200)"。

**新表述**: 完全撤所有 chars 数字（600/800/1200）暴露给 agent；HARD_CAP=4000 也**不暴露**给 agent（per D5 决议：agent 读 "never exceeding 600 words" ceiling 已是 actionable 约束，chars 系统底线仅在代码内 `_truncate_decision` 强制，揭示数字反而引入 perform-for-audience 风险）。

**Why**:
- 600 chars 物理不可达（5-field 载实下限 1200）
- 800 chars critical 也不真实（critical 实测 1500-1700）
- agent 看到三个 chars 数字会"目标→实际"对照失调，可能是 cycle 4/cycle 11 退化的猜测原因（agent 想"压简洁"，结果停在了思考中段）
- 替换为词数 + 结构 anchor（D5）

### D5 — 词数引导 + 结构主轴（议题 #5）

**新 persona 长度引导**（替换旧 "Aim for ~600 chars..." 段，单一合并段 3 句话）:

```
Write directly using the field structure — no preamble or analysis prose.
Length: at most 400 words in normal cycles, never exceeding 600 words
even in critical events (open/close/alert with action/SL trail with
multiple history points/thesis transition/macro event proximity). A
single sentence is sufficient when nothing actionable happened (e.g.,
"Watching, no position, routine tick — no changes").
```

**要点（4 层引导，全部内容驱动 / 结构驱动，无任意 prescriptive cap）**:
- **L0 系统底线 (HARD_CAP=4000 chars)**：仅在代码内 `_truncate_decision` 强制，**不暴露给 agent**（agent obey 600 词上限自然不触底线）
- **L1 总量 ceiling**：≤400 一般 / ≤600 critical events — 内容驱动（critical 可放宽，明示场景）
- **L2 内容驱动省略**：`Skip (5) Watch list when no observations` + `single sentence sufficient when nothing actionable`
- **L3 结构 anchor (5 fields)**：LLM-friendly 结构序，agent 自主分配每字段长度按内容密度

**Why（4 项 fiction 同源撤除）**:
- "Aim for ~600 chars" — wishful target，物理下限 1200 chars（D4）
- "Each field is typically 1-3 sentences" — sim #7 cycle 6 实证 (3) Thesis 5-6 句 / (4) Delta 6-7 句，载实需要超过 1-3；与 chars target 同源 fiction，一并撤
- "The system hard-truncates beyond ~{HARD_CAP}" — agent 不需感知系统底线（"never exceeding 600 words" 已是 actionable 约束），揭示系统机制反而引入 perform-for-audience 风险（与 R2-8b round 2 F1 anti-instruction guard 同源原则）
- LLM 字符级计数能力差，词数感知更准；ceiling 框架（"never exceeding 600"）vs target 框架（"aim for X"）减少边界踩线行为
- 实测 sim #7 critical event 峰 272 词 < 400 一般上限，留 47% 缓冲；最高 600 给极端 cycle（多 critical event 同时）

**预期效果**:
- R2-9.5 短 smoke (5-10 cycles) 期望：decision 长度中位数稳定在 **1300-1800 chars**（200-280 词 × 6.3 c/w），与 sim #7 outlier-removed 9-cycle median ~1547 持平或微升（D5 是放宽 ceiling 不压缩）；2 个 sim #7 short outlier (153 / 146 chars) 被收编为完整 5-field summary 后中位数自然微移；**0 hard truncation WARNING**（AC2.A.2）
- 长期 W2 24-48h 监控目标：hard truncation rate < 1%（AC2.B.3）

### D6 — Thinking cap 升级（议题 #6 / N12）

**改动**: `src/cli/display.py:739` `_render_reasoning(thinking_text, max_chars=2000)` → **`max_chars=15000`**

**Why**:
- sim #7 实测 thinking max 9492 chars / median ~6500 / min 878
- 2000 cap 实际截断率 ~91%（R2-8c D10 预测 25% 误差大）
- **15000 cap 覆盖 sim #7 max 9492 + 58% 缓冲**（vs 10000 仅 5.4% 缓冲）；11 cycles 样本太小推断长尾不可靠，W2 24-48h 高概率出现 > 9492 cycle，给充分缓冲免后续 N12c hot-fix 调参
- WARNING 触发 ≥ 15000 才是真异常信号（信号纯，避免 10000 边界 noise）
- R2-9.5 短 smoke (5-10 cycles) 期望 **0 thinking truncation**（AC2.A.4，sim #7 max 全覆盖且留 58% 缓冲）；长期 W2 24-48h 监控目标 truncation rate < 10%（AC2.B.4）
- **改动范围仅 session log 渲染**（`display.py:_render_reasoning`）；thinking 经 `_extract_thinking_text(result.new_messages())` 写入 `AgentCycle.reasoning` 列已**完整持久化**，DB 写入路径不动
  - **持久化 verified**: `grep -n "_extract_thinking_text\|reasoning=thinking_text" src/cli/app.py` → line 55 (helper def) + line 538 (extract) + line 547 (write to AgentCycle.reasoning) ✓ post-PR #38 当前状态（plan 阶段建议再 grep 一次锁住事实）
- 不影响决策 fidelity（R2-8b 注入用 `decision` 字段非 `reasoning`，thinking 不进下 cycle prompt）
- W2 24-48h 跑 thinking session log 段大量被截，事后**纯 forensic 可读性**问题（DB 端可全量 SQL 查 `reasoning`）

**File size 影响**: 假设每 cycle thinking 平均 6500 chars / max 9492 / 极端 15000，100 cycles ≈ 650KB-1.5MB session log。R2-3 (PR #32) 设 maxBytes=100MB / backupCount=30 → session log 远不到轮转阈值。**不阻塞**。

## §4 Implementation

### 4.1 `src/agent/persona.py`

#### 4.1.1 常量调整

```python
# Old (post-PR#38):
# R2-8b cycle decision caps — single source of truth shared between
# producer (persona §Cycle Closing Summary text below) and consumer
# (cli/app.py _truncate_decision defaults). Changing one updates both.
CYCLE_DECISION_SOFT_CAP = 800
CYCLE_DECISION_HARD_CAP = 1200

# New (R2-8d D3+D5) — verbatim block comment to replace above:
# R2-8d cycle decision hard cap — silent system safety net. NOT
# interpolated into persona text (D5: agent reads "never exceeding 600
# words" ceiling and self-controls; char cap protects against
# misbehavior). Used only by cli/app.py:_truncate_decision.
CYCLE_DECISION_HARD_CAP = 4000
# REMOVE: CYCLE_DECISION_SOFT_CAP (R2-8d D3 — INFO band drift sentinel
#   has lost signal at 9/11 cycles tripping in sim #7)
```

**实施 note**: 旧"shared between producer and consumer / Changing one updates both"措辞 R2-8d 后不再成立（producer 端不 reference HARD_CAP），新注释明示 "NOT interpolated into persona text" 防 stale comment。

#### 4.1.2 `_build_layer1` `## Cycle Closing Summary` 段重写

完整新版本（与 PR #38 现有文本对比）：

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

**与现版本（PR #38）对比 diff 要点**:

| 段 | 改动 |
|---|---|
| Lead sentence | **Cognitive flow framing**：旧 "Your final response must be a concise cycle summary covering five elements (do not produce an analysis followed by a summary — the summary IS the final response):" (27 词，summary-centric, 2 negatives) → 新 "After your reasoning and any tool calls, record what you decided and what you observed using this structure:" (18 词，reasoning + action 当主语，summary 是动词 record 的对象，0 negative) |
| (1)-(2) | 不变 |
| (3) ↔ (4) | **D2 序互换**：旧 (3)=Thesis / (4)=Delta → 新 (3)=Delta / (4)=Thesis（truncation 兜底序，rare safety case 时 (5) Watch list 先丢 → (4) Thesis 次之，(3) Delta 反思保留）|
| (5) | 不变 |
| 长度引导段 | 完全重写为单一合并段（3 句）：撤 600/800 chars fiction（D4） + 撤 "Each field is typically 1-3 sentences"（per-field cap 同源 fiction） + 撤 "system hard-truncates" 系统机制揭示（agent 不需感知 L0 底线）；保留 ≤400/≤600 词 ceiling + 内容驱动 single-sentence 快捷方式 |
| Anti-instruction guard 末段 | 不变（observational and descriptive 等仍有效）|

### 4.2 `src/cli/app.py`

#### 4.2.1 Import 调整

```python
# Old:
from src.agent.persona import (
    CYCLE_DECISION_HARD_CAP, CYCLE_DECISION_SOFT_CAP, RuntimeConfig,
)

# New:
from src.agent.persona import CYCLE_DECISION_HARD_CAP, RuntimeConfig
```

#### 4.2.2 `_truncate_decision` 简化

```python
# New (delete INFO band):
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
- 删除 INFO log 分支（`if n > soft_cap: logger.info(...)`）
- 函数体仅保留 hard cap 路径

### 4.3 `src/cli/display.py`

#### 4.3.1 `_render_reasoning` cap 调整

```python
# Old:
def _render_reasoning(thinking_text: str, max_chars: int = 2000) -> str:
    """... R2-8c D10 raises max_chars 800 → 2000 ..."""

# New:
def _render_reasoning(thinking_text: str, max_chars: int = 15000) -> str:
    """... R2-8d D6: 2000 → 15000 (sim #7 max 9492, median ~6500;
    2000 cap 实测截断率 ~91% 远超 R2-8c 预测 25%; 15000 覆盖 max + 58% 缓冲
    给 W2 长尾留充分余量, 免后续 N12c hot-fix 调参).
    """
```

变化：
- `max_chars` 默认值 2000 → 15000
- docstring 更新引用 R2-8d D6 + sim #7 实测数据 + 缓冲 rationale

## §5 Test changes

### 5.1 删除（2 tests）

| File:Test | 原因 |
|---|---|
| `tests/test_cycle_summary_injection.py::test_truncate_decision_in_soft_to_hard_band_keeps_full_with_info_log` | SOFT_CAP 删除后该 case 不存在；`_truncate_decision` 无 INFO log 路径 |
| `tests/test_persona.py::test_cycle_closing_summary_exposes_cap_numbers` | PR #38 该 test 断言 `"~600 chars"` / `"~800 for critical"` / `"~1200"` — R2-8d D4 全撤后该 test 必失败；与新 §5.3 `test_cycle_closing_summary_length_guidance_phrases_present` 功能重叠 → **删除**（不更新 — 否则两 test 重复）|

### 5.2 更新（10 tests，含 2 项 docstring-only stale fix）

| File:Test | 原 | 新 |
|---|---|---|
| `test_cycle_summary_injection.py::test_truncate_decision_below_soft_cap_returns_unchanged` | 测 ≤ 800 不变 | **rename 为 `test_truncate_decision_below_hard_cap_returns_unchanged`**；test data 保持 `text = "x" * 500`（baseline 远低于 4000 cap，验证基础不截断行为；无需调大 — 调到 3000 会与 above_hard_cap 测试更近，反而模糊语义）|
| `test_cycle_summary_injection.py::test_truncate_decision_above_hard_cap_truncates_with_marker_and_warning` | hard_cap=1200 测 1500 超 | hard_cap=4000 测 4500 超 |
| `test_cycle_summary_injection.py::test_truncate_decision_does_not_truncate_at_exactly_hard_cap` | n == 1200 不截 | n == 4000 不截 |
| `test_cycle_summary_injection.py::test_render_truncates_decision_above_hard_cap_via_truncate_decision` | 1500 chars 触发截断 | 4500 chars 触发截断 |
| `test_persona.py::test_cycle_closing_summary_contains_5_field_anchors` | 当前 anchor 序：`(3) Thesis & invalidation` / `(4) This cycle delta`（PR #38 现状）| **D2 序互换**：改为 `(3) This cycle delta` / `(4) Thesis & invalidation`；`(1)/(2)/(5)` anchors 不变 |
| `test_persona.py::test_cycle_closing_summary_lists_critical_events` | 当前断言含 `"Critical events include:"` literal（旧 D5 文本结构）| **D5 改写后该 phrase 不再出现**（新文本是 `"critical events (open/close/alert with action..."` 括号形式）→ 改断言：assert `"critical events"` (lowercase, in 词数 ceiling 段) + 4 enum substring (`"open/close"` / `"alert with action"` / `"thesis transition"` / `"macro event proximity"`) 仍存在 |
| `test_display_cycle.py::test_int_3_thinking_1500_chars_keep_all` | default cap 2000 时 1500 chars 不截 | default cap 15000 时 1500 chars 不截（断言不变，但 docstring R2-8d D6 注释更新）|
| `test_display_cycle.py::test_int_3_thinking_2500_chars_truncated_to_2000` | default cap 2000 时 2500 chars 截到 2000 + `[+500 chars]` | **rename 为 `test_int_3_thinking_above_default_cap_truncated` + 改 test data**：default cap 15000 时 18000 chars 截到 15000 + `[+3000 chars]`（不保留 explicit max_chars=2000 测试 — R2-8c boundary 概念由 docstring 引用即可，避免测试 surface 蔓延）|
| `test_cycle_summary_injection.py::test_render_keeps_full_decision_below_cap` (line ~373) | docstring `"T2.4: ≤ 1200 chars, no truncation marker"` (旧 cap 1200) | 仅改 docstring：`"T2.4: ≤ HARD_CAP (4000) chars, no truncation marker"`；body=800 仍 pass 断言不动 |
| `test_display_cycle.py::test_render_reasoning_over_800_truncated` (line ~623) | docstring 引 `"R2-8c D10: default max_chars 800 → 2000"` | 仅改 docstring：append `"; R2-8d D6: 2000 → 15000"`；测试本身 explicit 传 max_chars=800 不动 |

### 5.3 新增（4 tests）

| File:Test | 目的 |
|---|---|
| `test_persona.py::test_cycle_closing_summary_lead_uses_cognitive_flow_framing` | drift guard：Persona lead 含 `"After your reasoning and any tool calls, record"`（防回滚 summary-centric `"The summary IS the final response"` 措辞）|
| `test_persona.py::test_cycle_closing_summary_field_order_delta_before_thesis` | drift guard：Persona 中 `(3) This cycle delta` 出现位置 `< (4) Thesis & invalidation`（防 D2 序回滚）|
| `test_persona.py::test_cycle_closing_summary_length_guidance_phrases_present` | drift guard：Persona 含（断言字符串与 §4.1.2 实际文本一致）：`"400 words"` + `"never exceeding 600 words"` + `"single sentence is sufficient"` + `"Skip if no relevant observations"`（4 项均属"length guidance"广义范畴：word ceiling 直接限定 + content-driven skip / single-sentence 是内容驱动的 length-reduction 机制；命名 `length_guidance` 涵盖 ceiling + 省略联动）|
| `test_persona.py::test_cycle_closing_summary_no_legacy_fiction_or_system_aware_phrases` | drift guard：Persona **不**含以下旧 fiction / 系统揭示短语（防全部回滚）：`"~600 chars"` / `"~800"` / `"~1200"` / `"Aim for"` / `"is typically 1-3 sentences"` / `"hard-truncates"` / `"## SKIP"` / `"The summary IS the final response"` / `"4000"`（防 HARD_CAP 数字回流到 agent prompt）|

### 5.4 测试净增 / 删

- 删 2（INFO band test + 旧 cap_numbers redundant test）+ 改 10（含 2 项 docstring-only stale fix）+ 加 4 = **净 +2 tests**（passed framing；docstring-only updates 不影响 count）
- **passed 1210 → ~1212**（PR #38 baseline + R2-8d 净增）
- **collected 1213 → ~1215**（含 3 skipped 不变）

## §6 Acceptance Criteria

### AC1 静态测试

- [ ] AC1.1 `pytest -q` 全套 PASS（预期 ~1212 passed + 3 skipped；collected ~1215 含 skipped）
- [ ] AC1.2 `pytest tests/test_persona.py -v` 全 PASS（含 4 新 drift guards）
- [ ] AC1.3 `pytest tests/test_cycle_summary_injection.py -v` 全 PASS（新 cap 4000）
- [ ] AC1.4 `pytest tests/test_agent_cycle_injection.py -v` 全 PASS（注入逻辑不动，应自动通过）
- [ ] AC1.5 `grep -rn "CYCLE_DECISION_SOFT_CAP" src/ tests/` 返回 0 匹配（彻底清理）
- [ ] AC1.6 `grep -rn "~600 chars\|~800 for critical\|~1200" src/agent/` 返回 0 匹配（fiction 数字清除）

### AC2 R2-9.5 短 smoke gates（user 自跑 5-10 cycles 30-60min）

**口径说明**: 5-10 cycle 样本太小，百分比阈值实质等价于绝对计数。AC 分两层 — short smoke 用绝对计数（pass/fail），长期 W2 24-48h 用百分比。

#### AC2.A — R2-9.5 短 smoke 绝对计数 gates

- [ ] AC2.A.1 **0 个** mid-thinking miss（无 cycle output 是思考开场白 "Let me assess..." / "I need to check..." / "Back in 5" / 类似 mid-analysis 中段；每个 cycle output 必须是 summary 形态 — 5-field 结构 OR single-sentence routine 形式）
- [ ] AC2.A.2 **0 个** hard truncation WARNING log 触发（4000 cap 是真安全网而非常态）
- [ ] AC2.A.3 Decision 长度分布中位数落 **1300-1800 chars** 区间，与 sim #7 outlier-removed 9-cycle median ~1547（sorted: 1204/1422/1430/1527/**1547**/1571/1577/1696/1708）持平或微升（D5 是放宽 ceiling 不压缩；2 个 short outlier 收编为完整 summary 形式后中位数自然微移，不应反向下降）
- [ ] AC2.A.4 **0 个** thinking truncation 触发（15000 cap，sim #7 max 9492 全覆盖 + 58% 缓冲）
- [ ] AC2.A.5 单句 routine cycle 占比合理（quiet cycle 用单句 vs substantive cycle 用 5-field），**没有 substantive cycle 误用单句**（cycle 4 类格式偷懒）— 标准：持仓 + 有 alert/SL/TP 的 cycle 必须用 5-field
- [ ] AC2.A.6 注入到 cycle 4+ 的 prior summaries 块内容**不含** "Let me assess..." / "Done. ... Back in 5." 类思考开场白（D1 cognitive flow framing + 结构 anchor 联合验证）
- [ ] AC2.A.7 5-field anchor `(N)` 出现率 **= 100%** in substantive cycles；single-sentence form 出现率应与 quiet cycle 比例匹配（drift guard 静态测试锁结构存在 + smoke runtime 验证序 D2）

  **Measurement template** (R2-9.5 user 自跑后执行):

  ```sql
  -- substantive cycle 操作定义: length(decision) > 500 chars (proxy for "有内容")
  -- (1)-(4) 必备 (substantive cycle 合规 minimum); (5) Watch list optional 独立 flag
  -- 不计入 pass/fail (per D5 内容驱动省略哲学 — agent skip (5) when empty 是合规)
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

  **判定规则**:
  - `type='substantive' AND structure='has_4_required_anchors'` → ✅ 合规（无论 `has_optional_watch_list` 是 0 还是 1 — D5 内容驱动省略允许 skip (5) when empty）
  - `type='substantive' AND structure!='has_4_required_anchors'` → ❌ AC fail（持仓 / 有内容但缺 (1)-(4) 必备字段）
  - `type='quiet' AND structure='no_anchors_or_single_sentence'` → ✅ 合规（D1 L2 弹性，single-sentence form）
  - `type='quiet' AND structure='has_4_required_anchors'` → ⚠️ 形式正确但 over-structured（不算 fail，记录给 W2 观察）
  - `has_optional_watch_list` 列仅作 W2 观察统计 — agent 实际何时省略 (5) 的内容驱动模式数据，不进 pass/fail（per D5 哲学）

  **D2 序验证（独立 grep）**:
  ```bash
  python -c "
  import sqlite3
  rows = sqlite3.connect('data/tradebot.db').execute(
    'SELECT cycle_id, decision FROM agent_cycles '
    'WHERE session_id=? AND length(decision) > 500',
    ('<R2-9.5_session_id>',)
  ).fetchall()
  for cid, d in rows:
    pos_delta = d.find('(3) This cycle delta')
    pos_thesis = d.find('(4) Thesis')
    if 0 < pos_delta < pos_thesis: print(f'{cid}: D2 OK')
    else: print(f'{cid}: D2 FAIL (delta={pos_delta} thesis={pos_thesis})')
  "
  ```

#### AC2.B — 长期 W2 24-48h 监控目标（不阻塞 R2-9.5 通过）

- [ ] AC2.B.1 mid-thinking miss rate < 5%
- [ ] AC2.B.2 substantive cycle 误用 single-sentence 比例 < 10%（高于此则 R2-8e follow-up 调 prompt — agent 把 single-sentence 当偷懒出口）
- [ ] AC2.B.3 Hard truncation rate < 1%（agent 应自校在 600 词 ceiling 内）
- [ ] AC2.B.4 Thinking truncation rate < 1%（15000 cap 长尾余量充分；触发即真异常应 N12c follow-up）
- [ ] AC2.B.5 Decision 长度 max < HARD_CAP × 95% = 3800 chars（防触线）

## §7 Out of Scope (explicit)

- ❌ Per-field char/word cap（单 field cap 强制 — agent 按内容密度自主分配，撤 "Each field is typically 1-3 sentences" fiction 同源）
- ❌ Code-level format validation（如检测 `result.output` 是否含 5 anchor，违则 retry）— rely on persona prompt + L1 cognitive flow framing 自然引导
- ❌ SKIP fallback 出口（`## SKIP — <reason>` 单行格式）— sim #7 misses 是 format 失控不是"无内容可总结"，给 SKIP 反而是错答案；L2 单句弹性已覆盖 quiet cycle 场景
- ❌ Token-based cap（chars 仍是系统单位，与 `len(text)` 一致）
- ❌ HARD_CAP 进一步上调或动态化 — 4000 chars 充分覆盖 sim #7 实测 c/w 6.21-6.39 范围（agent obey 600 词 ceiling 时 ~3800 chars 输出 + 4-6% 缓冲）；极端密度 c/w=7.0 边界情况 4-5% marginal 截断是 acceptable last-resort safety net 行为，无需预先扩容（W2 数据如出现 > 4000 chars 高频触发 → 单独 follow-up）
- ❌ HARD_CAP 暴露给 agent — agent 读"never exceeding 600 words"已是 actionable ceiling，揭示 chars 系统底线引入 perform-for-audience 风险（与 R2-8b round 2 F1 anti-instruction guard 同源原则）
- ❌ Field 序进一步重排 — 仅 (3)(4) 互换；(1)(2)(5) 位置稳定
- ❌ Memory `feedback_observation_period_soft_constraint` §1 哲学回退（schema constraint）— 仍走 fact-only 暴露 + agent 自校
- ❌ Thinking DB 持久化路径 — thinking 已通过 `AgentCycle.reasoning` 列完整持久化（R2-7 schema reframe 决议，`cli/app.py:538-547` 写入 / `models.py:79,95` schema），R2-8d 不改 schema 不改写入路径；D6 仅调 `display.py:_render_reasoning` log render cap
- ❌ R2-8b 注入逻辑 / `_render_recent_summaries` / `_fetch_recent_summaries` / pydantic-ai message_history / `agent_cycles` schema 任何改动 — R2-8d 是 spec 校准，不动 R2-8b 实现

## §8 Self-review

### 8.1 议题打包合理性

6 件套相互依赖，应打包不应拆：
- D3 (cap 调高) 不打包 D5 (词数引导) → agent 无 ceiling 引导可能写到 4000 边界
- D4 (撤 fiction) 不打包 D3 (cap 调高) → 留下 "no length guidance" 真空，agent 行为不可预测
- D2 (field 序) 不打包 D3 (cap 调高) → cap 不变 truncation 仍频繁，单 D2 解决面窄
- D1 (cognitive flow framing + 弹性长度) 不打包 D5 (词数引导) → cycle 4 类格式偷懒退化路径仍存在
- D5 (词数引导) 不打包 D4 (撤 chars fiction) → 词数 + chars 两套数字暴露给 agent 造成混乱
- D6 (thinking cap) 与 D1-D5 无逻辑耦合，但同源（sim #7 实证），打包减少 PR 数量

打包决议 ✅。

### 8.2 风险评估

| 风险 | 概率 | 缓解 |
|---|---|---|
| 4000 cap 仍不够 critical event 极端 | 低 | sim #7 max 1708 chars，4000 留 134% 缓冲 |
| Field 序互换造成 LLM 混乱 | 低 | 5-field 命名 anchor 不变，仅位置序变；agent 按 anchor 写 |
| 词数 ceiling agent 无视 | 中 | sim #7 实测 critical 峰 272 词 < 400 一般，本就不需高墙；ceiling 是兜底 |
| Cognitive flow framing 不足以约束 mid-thinking miss | 中 | L1 lead 重写 + 结构 anchor `(N)` 同时启动；R2-9.5 5-10 cycles 期望 **0 mid-thinking miss**（AC2.A.1）；若仍有 miss → R2-8e 加 explicit anti-pattern 提示 follow-up |
| 单句弹性被滥用为 cycle 4 类格式偷懒出口 | 中 | sim #7 cycle 4 实测 5465 chars reasoning + 7 tools，"single sentence sufficient" 限定 "when nothing actionable happened" 而非 "always"；W2 长期监控单句 cycle 占比，若 > 30% 而 substantive cycle 漏 5-field → R2-8e follow-up 调 prompt |
| Thinking 15000 cap 仍不够 | 极低 | sim #7 max 9492 + 58% 缓冲；W2 24-48h 长尾出现 > 15000 已是真异常应 N12c follow-up |
| 测试改动多导致漏 case | 中 | 4 新 drift guards 覆盖关键不变量；R2-9.5 smoke 兜底实证验证 |

### 8.3 与 sim #7 实证逐项 trace

| 实证现象 | 决议 |
|---|---|
| cycle 4 持仓状态完整但 decision 153 chars 偷懒（5465 chars reasoning + 7 tools 但 1 句结束）| D1 L1 cognitive flow framing + L2 弹性长度（substantive cycle 自然走 5-field）|
| cycle 11 alert 后 mid-thinking 未 conclude（`"Let me assess..."`）| D1 L1 cognitive flow framing（"After your reasoning... record" 隐式 conclude 引导 + 结构 anchor 自然防 mid-thinking）|
| (4) Delta 尾部反思被截 | D2 field 序互换（4000 cap 后 truncation 罕见，序互换是 belt-and-suspenders）|
| 73% 超 1200 cap | D3 cap 调高 4000 |
| 600 chars target 不可达 / 第 1 cycle 1204 边界踩 cap | D4 撤 chars fiction（600/800/1200 全撤）+ D5 改词数 ceiling |
| 1-3 sentences per field 不可达（cycle 6 (3) Thesis 5-6 句 / (4) Delta 6-7 句）| D5 撤 per-field cap fiction，agent 自主分配 |
| Agent 看到 chars 数字会"目标 → 实际"对照失调 | D5 撤系统机制揭示（"hard-truncates beyond ~{HARD_CAP}"）— agent 不需感知 L0 系统底线 |
| Thinking 91% 截断率 | D6 cap 升级 15000（覆盖 sim #7 max 9492 + 58% 缓冲）|

每条实证现象都有对应决议，无遗漏。

### 8.4 OOS 完整性

§7 OOS 9 项明确列出**不在 R2-8d 范围**的议题：
- 6 项是"YAGNI 不预先做"（per-field cap / code-level format validation / SKIP fallback / token cap / dynamic cap / HARD_CAP agent-exposure）
- 2 项是"R2-8b/R2-8a 实现稳定不动"（注入逻辑 / thinking DB 持久化）
- 1 项是"哲学不回退"（fact-only soft-constraint）

新会话起手 R2-8e 类议题时，§7 防重开议题（特别是 SKIP fallback / HARD_CAP 暴露这两类已 brainstorm 后弃决议）。

### 8.5 实施量估

- `src/agent/persona.py`: ~30 行净改（常量 1 行 -1, 段重写 ~25 行 net）
- `src/cli/app.py`: ~5 行净改（import 1 行 / `_truncate_decision` 简化 ~4 行）
- `src/cli/display.py`: ~3 行净改（`max_chars=15000` + docstring）
- `tests/test_persona.py`: +4 新 drift guards / 改 1 phrase-anchor = ~50 行净增
- `tests/test_cycle_summary_injection.py`: 删 1 test / 改 3 tests = ~20 行净减
- `tests/test_display_cycle.py`: 改 3 tests = ~15 行（T-INT-3a 1500_chars_keep_all docstring 注释更新 + T-INT-3b 2500_chars_truncated_to_2000 rename + test data 改 + test_render_reasoning_over_800_truncated docstring 加 R2-8d D6 注）
- 总计 ~150 行 src + ~80 行 test 净改 = ~230 行净改

vs R2-8b 1633 行 plan + ~520 行 src/test 净改 → R2-8d ~14% 量级，lean 节奏合理。

### 8.6 节奏

- **brainstorm**: ✅ done in dialogue 2026-05-06（用户 + AI 6 件套 + 数据驱动决议）
- **spec**: 当前文档（lean ~600 行 vs R2-8b spec 1043 行）
- **plan**: 浓缩 3-4 task subagent-driven（vs R2-8b 6 task）
- **impl**: 1 会话足够（量小且 lean）
- **R2-9.5 smoke**: user 自跑 5-10 cycles 30-60min

总时间预计 2-3 会话（spec ✅ done after this commit / plan / impl）+ user 1 次跑 smoke。

### 8.7 与既有 memory 的一致性

- `feedback_observation_period_soft_constraint` §1（不加 schema constraint，fact-only 暴露）✅ 一致 — D1-D5 全是 persona text 引导，无 code 强制
- `feedback_long_walltime_experiments`（>10min 实验 user 自跑）✅ 一致 — R2-9.5 smoke 由 user 跑
- `feedback_no_auto_edit`（结论与文档分开）✅ 一致 — 本 spec 是用户确认 brainstorm 决议后的 doc 落地
- `feedback_plan_doc_commit_first`（spec/plan 先于 impl commit）✅ 本 spec 作为 R2-8d 第 1 个 commit（branch `feature/iter-w2r2-8d-spec-calibration`）
- `project_r2_8d_candidate_findings`（候选 6 件套实证）✅ 一致 — 本 spec 是 candidate 落地的 spec doc
- `project_w2_prep_progress` §10 (R2-9 ✅) §12 (R2-8d 起手指引) §13 (R2-9.5 起手指引) ✅ 一致

---

**Spec 完成。下一步**: spec commit 到 feature 分支后，user 审阅 → plan 阶段（lean 版 ~300-500 行 plan doc，3-4 task）。
