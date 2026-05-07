# R2-Next-A — F1 Length Feedback Loop Closure (Bundle D + minimal A)

**Date**: 2026-05-07
**Iter id**: w2r2-next-a
**Status**: design (brainstorm-approved, awaiting user spec review)
**Predecessors**: R2-7 (PR #35) / R2-8a (PR #36) / R2-8b (PR #38) / R2-8d (PR #39)
**Inventory source**: `.working/sim8-w2-inventory.md` §2 P0-1 + `.working/sim8-w2-inventory-parts/dim-1-5-7-11-F1-cluster.md`

---

## 1. Problem statement

W2 真实观察期 sim #8 (`8f2ca0cb-...`, 178 cycles / 19.2h / 14.4M tokens) 暴露
"F1 自回归 priors injection 环路"——4 个表面议题 (D1/D5/D7/D11) 实为单一
正反馈环：

```
[D11] R2-8b N=3 priors verbatim 注入（by-design）
    ↓ enables
[D5]  cycle 5 (UTC 09:39) 首次 bold `**(1) Stance**` → cycles 6-8 priors
      → cycle 8 起 100% lock-in（173 cycles 0 reverts）
    ↓ + agent 无 length feedback signal
[D1]  decision avg 1666→4469 chars / 19h 单调上升 / max 6131 chars (~970 words)
    ↓ priors block 4000+ × N=3 ≈ 12000+ chars
[D7]  hard cap 4000 routinely 触发 — 末 3h 27/28 cycles ≥1 truncation；
      suspected 推 agent 写更长 self-recap
    ↓
回到 [D11]（positive feedback）
```

**载荷议题** = `D11-I1`：priors injection 没有 length budget management；
4000-char cap 是 silent guardrail（agent 不知），仅解决 1 environment 不解决环路。

## 2. Background

### 2.1 R2-8b（PR #38, 2026-05-06）— priors 注入 by-design

`_render_recent_summaries` (cli/app.py:189-214) 在每 cycle 入口 query N=3 most
recent ok cycles，verbatim 注入 user message rotating tail。设计意图：闭环
continuity（agent 自产自消）+ 避 thinking 噪音 + 与 R2-7 schema decision: Text
对齐。**本 iter 不动 R2-8b 主架构**。

### 2.2 R2-8d（PR #39, 2026-05-06）— 现 persona 形态

`_build_layer1` `## Cycle Closing Summary` 段：
- 5 字段结构（Stance / Active commitments / This cycle delta / Thesis & invalidation / Watch list）
- 长度引导："at most 400 words in normal cycles, never exceeding 600 words"
- D5 设计哲学："agent obeys word ceiling, char hard_cap kicks in only on misbehavior"
- `CYCLE_DECISION_HARD_CAP = 4000` (chars) — silent guardrail，不 interpolate 到 persona

### 2.3 W2 sim #8 暴露 R2-8d D5 假设破裂

| 指标 | 实测 | R2-8d D5 假设 |
|---|---|---|
| max words | 970 (≈6131 chars / 6.3 c/w) | 600 ceiling |
| avg words drift | 270 (early) → 720 (late), 2.7× monotone | 不漂 |
| 末 3h truncation 触发 | 27/28 cycles | "kicks in only on misbehavior" |
| 18.1% cycles ≥1 prior over cap | — | low rate |
| 13 cycles 全 3 priors over cap | "doom loop" | 不存在 |

R2-8d D5 silent cap + word ceiling 单纯软约束**不闭合环**，需注入 feedback signal。

## 3. Data baseline (5 open questions investigation)

调研脚本 `.working/sim8-w2-inventory-parts/_query_f1_open_questions.py` 跑全 178 cycles：

### Q1 — DB.decision 是 full agent message 还是 trailing segment？

**答案：full final TextPart（pydantic-ai `result.output`）**。决策：

| 起始模式 | 比例 |
|---|---|
| `(1) Stance` 直起（含 markdown variants） | 57.1% (101/177) |
| `## Cycle Summary` H2 | 4.5% (8/177) |
| markdown table delimiter `--- \| \|` 等前缀 | 38.4% (68/177) |

5-field structure 几乎所有 cycle 都有，只是 markdown 包装多样。

**对设计影响**：D2 word counting 使用 split convention 时，markdown 分隔符（`|`、`---`）作为 word 计入——天然给 agent 信号"table format inflates count"，无需强制 A1 修改。

### Q2 — 65 truncation events 精确 mapping

模拟 `_truncate_decision` 路径全 session 重放结果**精确 = 65 events**（与 system.log 行数一致），分布：

- **23 distinct decisions ≥ 4000 chars**
- **32/177 = 18.1% cycles** ≥1 prior over cap
- **13 cycles** 全 3 priors over cap（"doom loop"）
- 首次 cycle `4de0585a`（≈ cycle 152，UTC 01:06）→ 末 cycle `581b9a1c`（cycle 178）
- 分布: 145 / 12 / 7 / **13**

**对设计影响**：cap raise 单独不够（fat priors 末段连续产生）；必须治源（D1 长度漂移）。

### Q3 — cycle 8 retry_exhausted 进 N=3 priors 路径吗？

**答案：✅ filter 正确兜住。** sim #8 唯一 non-ok cycle 是 cycle 8 (`1aa0d4e5`)，
`_fetch_recent_summaries` 双 filter `execution_status='ok' AND decision IS NOT NULL`
正确排除。无 bug。

### Q4 — Multi-summary per cycle 频率

**答案：0.6% (1/177)**，inventory "5% spot-check" 高估。

| `(1) Stance` 出现次数 | cycles |
|---|---:|
| 0× | 6（5-field 完全跳过，含 cycle 1 "Waiting for fill"） |
| 1× | 170 |
| 2× | 1（`ad31e998`，6131 max-chars outlier） |

**对设计影响**：persona 不需 "write ONCE" 约束。6 cycles 0× Stance (3.4%)
属于 R2-8d #1 enforcement 残尾，**OOS for this iter**（独立议题）。

### Q5 — W1 baseline 长度对比

**答案：schema 不可比。** sim #1 `decision` 列 avg 6 chars（pre-R2-7 enum），
`reasoning` 列 avg 429 chars（pre-Iter 4 cap 500）。

**对设计影响（attribution 关键）**：F1 不是 LLM baseline drift，**是 W2 prep
架构性后果**——R2-7（schema reframe）+ R2-8b（priors 注入）联合引入；
R2-8b 的 verbatim N=3 注入是**唯一可调的反馈环节**，治理着力点确认。

## 4. Decision space considered

### 4.1 Bundle 取舍：A / B / C / D

| Bundle | 风险 | 杠杆 | 决议 |
|---|---|---|---|
| A — cosmetic（SQL widening / 单位对齐 / persona "no markdown" / 词边界 truncation） | 极低 | 仅 observability | **部分采纳**（A2 SQL 文档 / A3 单位对齐为主，A1 markdown drop） |
| B — mid（structural truncate 保 (1)(3)(4) / transparent marker / markdown-normalize priors） | 中（drift parser fragility） | 减 worst loss-of-context | **拒绝** — Q1 38.4% markdown 漂移让 parser 脆裂 |
| C — load-bearing（compressed/summarized priors，N=3 重设计） | 高（R2-8b 主架构变动） | 治根因 | **defer** — 与 `agent_reflection_tools_candidate` 上位议题耦合；W3 数据驱动决定是否启动 |
| **D — orthogonal（agent length feedback signal）** | 低 | 闭环 missing 环节 | **采纳为主轴** |

### 4.2 信号强度：(a) / (b) / (c)

| 选项 | cap 暴露程度 | 决议 |
|---|---|---|
| (a) Reactive only — D1 marker only | 仅 marker 触发时 | 拒绝（仅 18.1% cycles 收到信号） |
| (b) Proactive count — D1 + D2 | 每 cycle word count + implicit cap leak | 拒绝（implicit leak 已破 R2-8d D5 silent，不如直接 explicit） |
| **(c) Full transparency — D1 + D2 + A3** | 完整信息 | **采纳**——明确反转 R2-8d D5 silent cap 设计哲学 |

### 4.3 Word counting method

`text.split()` ≡ `re.findall(r'\S+', text)`（whitespace runs）。

理由（见 §3 Q-meta 数据脚本）：
- 与 R2-8d 实测 c/w 6.3-6.4 校准对齐（`\b\w+\b` 给 c/w≈5.7，矛盾）
- 与 wc -w 标准对齐
- 自然 deterministic，无 hyphenated/contraction 边界争议
- 标点/markdown 分隔符计入 word 数 → 天然给 agent "table format inflates count" 信号

**Reject**：`\b\w+\b`（over-count 9-10%）/ tiktoken（单位概念错误）/ NLP library（重型依赖）。

### 4.4 Cap value: 600 / 650 / 700

选 **700 words**：
- 与 persona ceiling 600 (critical) 保持 ~17% headroom，给 critical event 真实缓冲
- 等价 ~4326 chars（c/w=6.18）≈ 当前 4000 chars × 1.08
- 比 600 (collapse soft/hard) 不那么 binding；比 650 (~等价当前) 给 agent 学习空间

### 4.5 Unit alignment: chars vs words

选 **words throughout**：
- 与 R2-8d D5 word ceiling 完全对齐
- LLM 数 words 比数 chars 准（自然单位）
- word-boundary slice **顺带解决 D7-I1 naive char-slice mid-word/mid-number 问题**（free-rider）—— row-level 完整性 NOT guaranteed（见 §5.2 关键改动 + §9 P2 风险接受）
- 5% char headroom 损失（worst dense markdown 4200 chars vs 当前 4000 上限）经数据评估可接受
- "word" 定义沿用 R2-8d 同 convention（whitespace runs），不引入新边界

### 4.6 Persona A3 phrasing

选 **consequence-explicit framing**："Beyond 700 words the system hard-truncates
the summary as a safety net — when this happens, the truncated portion is lost
from prior-cycle context."

理由：
- "lost from prior-cycle context" 触发 D11 self-reference 路径（agent 重度
  依赖 priors，3.07 self-references / cycle）→ 切实代价
- 与 D2 header `879 words` + D1 body marker 形成 3-channel 闭环陈述
- 保留 400/600 words 作 self-discipline target（discipline > enforcement）

## 5. Design

### 5.1 Constants & helper

```python
# src/agent/persona.py
# R2-Next-A: hard cap exposed to agent via three channels:
#   D1 — _truncate_decision marker text (cli/app.py)
#   D2 — _render_recent_summaries header word count (cli/app.py)
#   A3 — persona §Cycle Closing Summary explicit "700 words" mention
# F1 length-loop closure (vs prior R2-8d D5 silent guardrail).
CYCLE_DECISION_WORD_CAP = 700

# Silent secondary char floor — defensive against pathological cases
# where a single `\S+` token is very large (long URL / JSON dump /
# no-space CJK / `|---|---|---|` table separator with no internal
# whitespace), which would bypass the word cap (counted as 1 word).
# NOT exposed to agent (no 4th channel) — preserves the word-unit
# primary signal of A3/D1/D2.
# sim #8 longest single token = 50 chars; max decision = 6131 chars
# → 8000 gives ~30% headroom over historical max; cap-bypass risk
# in current behavior is empirically zero, this is future-proofing.
CYCLE_DECISION_CHAR_HARD_FLOOR = 8000
```

```python
# src/cli/app.py
import re

_WORD_RE = re.compile(r'\S+')


def _count_words(text: str) -> int:
    """Whitespace-split word count (wc -w convention).

    Single source of truth across:
      - _truncate_decision (D1: word-cap enforcement)
      - _render_recent_summaries (D2: priors header signal)
      - persona drift guards (A3: ceiling consistency)

    Convention: any consecutive non-whitespace run = 1 word. Markdown
    delimiters (`|`, `---`) count as words — naturally pressures agent
    toward concise output by penalizing formatting noise.
    """
    return len(_WORD_RE.findall(text))
```

### 5.2 D1 — `_truncate_decision`（word-boundary slice + visible marker）

```python
# src/cli/app.py
def _truncate_decision(
    text: str,
    hard_cap_words: int = CYCLE_DECISION_WORD_CAP,
    hard_cap_chars: int = CYCLE_DECISION_CHAR_HARD_FLOOR,
) -> str:
    """Hard-truncate at word boundary with WARNING log + visible marker.

    R2-Next-A D1 (primary): word-unit aligned with persona ceiling.
    Word-boundary slice preserves whitespace-delimited token boundaries
    (no mid-word or mid-number cuts). Row-level integrity (markdown
    table rows / bullets) is NOT guaranteed — if cap falls between
    `|` cells of one row, that row will appear half-cut in the prior
    body. Acceptable: agent reads truncated priors as prose, not as
    rendered tables.

    Marker exposes word cap to agent (vs prior R2-8d D5 silent
    guardrail). Pairs with persona A3 explicit cap statement and D2
    priors header word count to close F1 length-feedback loop.

    Secondary defense (silent, NOT agent-facing): if word-cap path
    doesn't fire but len(text) > hard_cap_chars, fall back to silent
    char-slice with legacy `[truncated]` marker. Protects against
    pathological cases (long URL / JSON / `|---|---|---|` separator)
    where one `\\S+` token holds many chars.
    """
    matches = list(_WORD_RE.finditer(text))
    if len(matches) > hard_cap_words:
        cut_pos = matches[hard_cap_words].start()
        logger.warning(
            "Cycle decision exceeded hard cap %d words (got %d), truncating",
            hard_cap_words, len(matches),
        )
        return (
            f"{text[:cut_pos].rstrip()}\n"
            f"... [truncated by system, cut at {hard_cap_words} words]"
        )
    if len(text) > hard_cap_chars:  # P1 silent secondary safety net
        logger.warning(
            "Cycle decision exceeded char floor %d (got %d, words=%d), "
            "silent truncating",
            hard_cap_chars, len(text), len(matches),
        )
        return text[:hard_cap_chars] + " ... [truncated]"
    return text
```

**关键改动 vs 现状**：
- 主 cap 单位 chars → words；新增 silent secondary char floor 作 belt-and-suspenders（P1 反馈）
- slice 改 word-boundary（`matches[N].start()` 位置）—— 切点在第 N+1 个 word 的起始位置，保 N word 完整 + 后续 whitespace 不带入下一行
- `.rstrip()` 移除 cut_pos 之前的 trailing whitespace，让 body 末尾干净换行接 marker
- 主路径 marker 改信息性 + 移到 standalone newline；secondary 路径保留 legacy `[truncated]` marker（silent 不暴露给 agent）
- WARNING log 单位同步（主 "%d words" / secondary "%d chars"）
- **不保 markdown row 完整性**（P2 校准）—— word-boundary slice 仅保 token 边界，row 半截可接受（agent 按 prose 读不按表格读）

### 5.3 D2 — `_render_recent_summaries`（header word count）

```python
# src/cli/app.py
def _render_recent_summaries(
    summaries: list[CycleSummary], now: datetime,
) -> str:
    if not summaries:
        return ""

    blocks = []
    for s in sorted(summaries, key=lambda x: (x.created_at, x.id)):
        cycle_id_short = s.cycle_id[:8]
        utc_str = s.created_at.strftime("%Y-%m-%d %H:%M UTC")
        ago = _format_relative_time(now, s.created_at)
        word_count = _count_words(s.decision or "")  # NEW
        body = _truncate_decision(s.decision)
        blocks.append(
            f"[cycle {cycle_id_short} · {s.triggered_by} · {utc_str} "
            f"({ago}) · {word_count} words]\n{body}"        # NEW: + " · {word_count} words"
        )

    header = "Your prior cycle summaries (most recent N=3, from this session):"
    return f"{header}\n\n" + "\n\n".join(blocks)
```

**视觉效果**（含 truncated prior）：

```
Your prior cycle summaries (most recent N=3, from this session):

[cycle 9f030bb0 · scheduled · 2026-05-06 08:59 UTC (3 min ago) · 9 words]
Waiting for fill — will set SL/TP once confirmed.

[cycle 48e75786 · conditional · 2026-05-06 09:01 UTC (1 min ago) · 156 words]
**(1) Stance** — Watching for breakout reclaim above 82,200...
...

[cycle 581b9a1c · alert · 2026-05-07 04:05 UTC (10 sec ago) · 879 words]
**(1) Stance** — Holding long, ...
... [truncated by system, cut at 700 words]
```

**关键设计要点**：
- `word_count` 是 ORIGINAL count（pre-truncation）—— agent 看到 879 vs cap 700 → 知道 "我超 179 words"
- 无 `· system-truncated` 后缀（879 > 700 + body marker 已蕴含）
- 单位 words only，无 chars 副单位

### 5.4 A3 — `persona.py` Layer 1 显式暴露 cap

`_build_layer1` `## Cycle Closing Summary` 段（persona.py:100）现：

```
Length: at most 400 words in normal cycles, never exceeding 600 words
even in critical events (open/close/alert with action/SL trail with
multiple history points/thesis transition/macro event proximity).
A single sentence is sufficient when nothing actionable happened
(e.g., "Watching, no position, routine tick — no changes").
```

改为：

```
Length: at most 400 words in normal cycles, never exceeding 600 words
even in critical events (open/close/alert with action/SL trail with
multiple history points/thesis transition/macro event proximity).
Beyond 700 words the system hard-truncates the summary as a safety
net — when this happens, the truncated portion is lost from prior-cycle
context. A single sentence is sufficient when nothing actionable
happened (e.g., "Watching, no position, routine tick — no changes").
```

### 5.5 A2 — Analyst SQL pattern note（docs only, T6）

`docs/metrics/agent-cycles-schema.md` 加新段：

```markdown
## SQL pattern for 5-field anchor detection

5-field summary anchors may appear in markdown variants. SQLite default
没有 `REGEXP` operator（需 UDF 注册），所以用 multi-LIKE union 覆盖 4
个常见 markdown variants（P4 校准）：

```sql
-- Multi-LIKE pattern (executable on default SQLite)
SELECT * FROM agent_cycles
WHERE decision LIKE '%(4) Thesis%'        -- plain or bold-wrap-whole `**(4) Thesis & ...**`
   OR decision LIKE '%(4) **Thesis%'      -- bold-inner-only `(4) **Thesis**`
   OR decision LIKE '%**(4) Thesis%'      -- bold-prefix `**(4) Thesis`
   OR decision LIKE '%**(4)** Thesis%';   -- bold-tag-only `**(4)** Thesis`

-- Narrow (legacy R2-8b/R2-8d, do not use): misses bold-inner variant
SELECT * FROM agent_cycles
WHERE decision LIKE '%(4) Thesis%';
```

复杂正则需求（如 case-insensitive / 任意空白匹配）建议走 Python helper：

```python
import re, sqlite3
PATTERN = re.compile(r'\(4\)\s*\*?\*?\s*Thesis', re.IGNORECASE)
con = sqlite3.connect('data/tradebot.db')
cycles = con.execute("SELECT cycle_id, decision FROM agent_cycles").fetchall()
matched = [c for c in cycles if c[1] and PATTERN.search(c[1])]
```

W2 sim #8 实测（177 ok cycles，其中含 5-field anchor 的 171 cycles）：
narrow LIKE 命中 100 / 171 = 58.5%（漏 41.5%，71/171 是 bold-inner-only 等变体）；
multi-LIKE 4-variant union 命中 171 / 171（0 missing / 0 extra vs Python regex baseline）。
```

### 5.6 不动的代码

- `agent_cycles` schema（不变）
- R2-7 字段语义（不变）
- R2-8b `_fetch_recent_summaries` query（不变）
- `_build_recent_summaries_block` outer wrap（不变）
- `_extract_thinking_text` helper（不变）
- pydantic-ai message_history 构造（不变）
- agent 主循环 / retry / forensic 路径（不变）

## 6. Tests

### 6.1 New tests

#### `_count_words` helper（5 cases）

| Test | 输入 | 期望 |
|---|---|---|
| `test_count_words_empty` | `""` | 0 |
| `test_count_words_whitespace_only` | `"   \t\n  "` | 0 |
| `test_count_words_single` | `"hello"` | 1 |
| `test_count_words_mixed_whitespace` | `"a\tb\nc d"` | 4 |
| `test_count_words_markdown_delimiters` | `"\| - Position \|"` | 4 |

#### `_truncate_decision` D1 主路径（4 cases）

| Test | 期望 |
|---|---|
| `test_truncate_under_cap_unchanged` | 100 words 输入 → 同输入返回，无 marker / WARNING |
| `test_truncate_over_cap_word_boundary` | 800 words 输入 → 切在第 700 word 之后，body 不 mid-word，含 marker `[truncated by system, cut at 700 words]` |
| `test_truncate_marker_uses_constant` | drift guard：marker 含 `f"cut at {CYCLE_DECISION_WORD_CAP} words"` |
| `test_truncate_marker_on_new_line` | marker 前 `\n`，标识 standalone 行 |

#### `_truncate_decision` D1 secondary char floor（2 cases，P1 加固）

| Test | 期望 |
|---|---|
| `test_truncate_pathological_single_token_falls_back_to_char_floor` | 50 words 输入但其中 1 个 token = 9000 chars (no whitespace) → 主 word-cap 不触发；secondary char floor 触发；输出 marker = legacy `... [truncated]`（不是 word-cap marker），WARNING log 走 char path |
| `test_truncate_word_path_takes_precedence_over_char_path` | 800 words AND 9500 chars 输入 → word-cap path 命中（marker 是 `cut at 700 words`，非 char marker）；char floor path 不触发 |

#### `_render_recent_summaries` D2（3 cases）

| Test | 期望 |
|---|---|
| `test_header_includes_word_count` | 普通 prior header 含 `· {n} words` |
| `test_header_uses_original_count_for_truncated` | over-cap prior header 显示 ORIGINAL count（pre-truncation），与 body 末尾 `cut at 700 words` 形成对比 |
| `test_header_count_matches_helper` | drift guard：header 数字 == `_count_words(s.decision)` |

#### `persona.py` A3（3 cases）

| Test | 期望 |
|---|---|
| `test_layer1_contains_700_word_cap_anchor` | layer1 含 `"700 words"` 字面 |
| `test_layer1_truncation_consequence_keyword` | layer1 含 `"truncated"` 或 `"lost"` 关键词（防 silent revert） |
| `test_layer1_word_cap_matches_constant` | drift guard：persona 文本 `700 words` 数值 == `CYCLE_DECISION_WORD_CAP` |

#### Cross-channel consistency（1 case）

| Test | 期望 |
|---|---|
| `test_word_cap_value_consistent_across_three_channels` | 单一 `CYCLE_DECISION_WORD_CAP` 同时出现在：persona layer1 / `_truncate_decision` marker / `_count_words` 计数语义一致 |

### 6.2 Modified tests

| 现有 test | 改动 |
|---|---|
| `test_truncate_decision_at_hard_cap_*`（如有） | 单位 chars→words；assertion `[truncated]` → `[truncated by system, cut at 700 words]` |
| `test_render_recent_summaries_header_format` | 4-field header → 5-field（+ `· N words`） |
| `test_cycle_closing_summary_word_ceiling_anchor` | 现有 400/600 anchor 不变，扩展加 700 anchor |
| `test_cycle_closing_summary_field_order_delta_before_thesis` | R2-8d D2 序保护测试 — **不变**（A3 不动 (3)(4) 序） |
| **`test_cycle_closing_summary_no_legacy_fiction_or_system_aware_phrases`**（P3 校准）| `tests/test_persona.py:513-531` forbidden 列表中 **移除 `"hard-truncates"`**（保其他 8 项）。R2-8d D5 silent guardrail 哲学被 R2-Next-A 显式反转 → 对应 anti-revert anchor 退役。注释更新："hard-truncates removed in R2-Next-A; A3 deliberately surfaces system mechanic." |

### 6.3 Tests 总数

- 新增：**18 cases**（5 helper + 4 D1 主 + 2 D1 secondary + 3 D2 + 3 A3 + 1 cross-channel）
- 修改：5 cases（含 P3 forbidden 列表退役）
- 删除：0
- **测试规模 1215 → 1233（+18 净）**（baseline 实测 `uv run pytest --collect-only -q`，P5 校准）

## 7. Acceptance criteria

### 7.1 Build-time AC（impl 阶段验证）

- AC-B.1 全套测试 **1233 pass**
- AC-B.2 0 regression (**1215** 现有 + **18** 新增)
- AC-B.3 `_count_words` / `_truncate_decision` / `_render_recent_summaries`
  / persona layer1 4 channel 数值 + 措辞 drift guard 全 pass
- AC-B.4 R2-8d D2 序保护测试持续 pass（(3)(4) 序不动）
- AC-B.5 word-boundary slice 不切 mid-word（unit test 覆盖）— **不**断言 row 完整（P2 校准）
- AC-B.6 R2-8b 注入 fail-isolated 性质保留（DB error → 空注入，不阻塞 cycle）
- AC-B.7（P1）secondary char floor path 在 pathological 输入下正确 fallback；word-cap path 仍优先（test_truncate_word_path_takes_precedence_over_char_path 验证）

### 7.2 W3 观察期 AC（landed 后下次 sim 验证）

W3 跑完后由 analyst SQL 验证 R2-Next-A 是否 close 了 F1 环路：

**测量定义**：所有 AC 的 word count 均指 **agent 原始 decision 的 `_count_words(decision)`**（D2 header 显示的 ORIGINAL count），不是 truncated body 的 count。这反映 agent 实际写出的长度，不是系统机械截断后的残值。

**sim #8 baseline（word cap 700 retrospective recount）**：
- writer-side over-700: **12/177 = 6.8%**
- writer-side over-600 (critical ceiling): 27/177 = 15.3%
- writer-side over-400 (normal target): 121/177 = 68.4%
- reader-side ≥1 prior > 700 in N=3 window: 21/177 = 11.9%
- max words: 934 / avg: 454 / p99: 818
- length drift ratio (末 3h / 早 3h avg): ≈ 2.7×

| AC | 阈值 | 对照 sim #8 baseline |
|---|---|---|
| **AC-W3.1** length drift retraction（**主指标**）| 末 3h avg / 早 3h avg ratio ≤ 1.5× | 2.7× ❌ → 期望 ≤1.5× |
| **AC-W3.5** retraction visible（**主指标**）| 50-cycle moving avg word count 序列至少含 1 处回撤幅度 ≥10%（max 不在末段；OR 末段 avg < session-wide max-window avg × 0.9）| 单调无回撤 ❌ → 期望可见回撤 |
| AC-W3.2 max words (original count, 边界) | session 全程 max ≤ 800（允许 ≤14% over cap 偶发）| max 934 ❌ → 期望 ≤800 |
| AC-W3.3 truncation rate (binding 频次) | original word count > 700 的 cycles 占比 ≤ 5% | 6.8% ❌ → 期望 ≤5% |
| AC-W3.4 doom loop (硬底线) | 全 3 priors 都 over cap 的 cycles = 0 | 13 cycles ❌ → 期望 0 |

**通过 ≥4/5 AC** = R2-Next-A 视为 effective，进 next iter；
**通过 ≤2/5** = 升级 Bundle B（structural truncate）/ Bundle C（compressed priors）。

## 8. Out of scope (explicit, do not re-open)

- ❌ Bundle B structural truncate（保 (1)(3)(4) 拆分）— Q1 38.4% markdown 漂移让 parser 脆裂
- ❌ Bundle C compressed priors / N=3 重设计 — 与 `agent_reflection_tools_candidate` 上位议题耦合，W3 数据驱动
- ❌ A1 markdown emphasis 修改 — 保 agent 自主 styling，不解决 F1 长度漂移
- ❌ R2-8d #1 5-field enforcement 残尾（6 cycles 0× Stance, 3.4%）— 独立议题
- ❌ R2-7 schema 变动 / `agent_cycles` 字段 / decision 列语义 — 不动
- ❌ R2-8a cycle log render — 不动
- ❌ `_fetch_recent_summaries` query / N=3 数 / session_id filter — 不动
- ❌ N6 HTF hardening（升级 H 候选，独立 ~50 行 PR）— 不进 R2-Next-A
- ❌ W2 ops backlog S1-S5 — 弱触发，不立即修
- ❌ Decision forensic edge cases (P1-3 sim race / P1-4 retry partial commit) — 子集 ② 独立会话
- ❌ Strategy quality (P1-5/6/7/9) — 子集 ③，留 N10 next round + W3 对比
- ❌ pydantic-ai output_type=CycleSummary 结构化输出 — 与 D5-I1 reject 一致

## 9. Risks + mitigations

| 风险 | 缓解 |
|---|---|
| word-boundary truncation regex `r'\S+'` 在 Unicode 边界（中文 / emoji）表现 | 加 1 unit test 覆盖 emoji + Chinese mix；`\S+` 是 Unicode-aware（Python re 默认） |
| 700 words cap 实际太严，W3 截断率仍高 | 单 constant 数值改，下次 iter 单 PR 调（low cost） |
| agent 看到 D2 word count 后开始 "数着写" 导致行为退化 | 不可预防，W3 数据观察；若发现 → 升 Bundle C compressed priors |
| persona "truncated" / "lost" keyword test 过严捕到 future legitimate revisions | drift guard 注释明写 "防 silent revert"，未来如要改记得删/调此测试 |
| **P1**: pathological 单 token（long URL / JSON / `\|---\|---\|` separator）bypass word cap | secondary char floor 8000 chars silent fallback；2 unit tests 覆盖 fallback 路径 + 主路径优先；sim #8 实测 zero-occurrence (max single token 50 chars) 但加防御应对未来 |
| **P2**: word-boundary slice 不保 markdown row 完整性 | spec 语言降级 "preserves whitespace-delimited tokens (no mid-word/mid-number cuts)"；接受 row 半截作为 prose-readable 代价；不引入 line-aware truncation（complexity / ROI 不匹配） |
| **P3**: A3 与现有 R2-8d D5 forbidden phrase guard 冲突 | T4 显式从 `tests/test_persona.py:518-528` forbidden 列表移除 `"hard-truncates"`；其他 8 项保留（仍是 anti-revert）；commit message 注明 R2-8d D5 哲学反转的 trace |
| **P4**: 默认 SQLite 不支持 REGEXP | A2 docs 改 multi-LIKE union 4-variant 覆盖（实测命中 171/171 cycles，与 Python regex 一致），加 Python regex helper 例子供复杂场景 |
| 700 words cap 比 R2-8d 4000 chars 隐性收紧 ~7%（dense markdown 触发率上升） | 700 words ≈ 4326 chars (c/w=6.18) > 4000 chars，平均场景反而 8% 放宽；只有 worst-density (c/w=5.7) 才略紧 ~3% |

## 10. PR shape

**单一 PR**（per `feedback_brainstorm_decision_location` 不 bundle 多议题），
subagent-driven mode 6 task：

| Task | 内容 | 依赖 | 预期增量 |
|---|---|---|---|
| **T1** | `_count_words` helper + `CYCLE_DECISION_WORD_CAP=700` + `CYCLE_DECISION_CHAR_HARD_FLOOR=8000`（P1）+ 常量 rename + 5 unit tests | 独立基础 | +5 tests |
| **T2** | `_truncate_decision` D1（word-boundary slice + new marker + secondary char floor branch）+ 4 主路径 + 2 secondary 路径（P1）+ WARNING log 改 | T1 | +6 tests |
| **T3** | `_render_recent_summaries` D2（header word count）+ 3 tests + 修 ~3 现有 header 断言 | T1, T2 | +3 tests, ±3 modified |
| **T4** | `persona.py` A3（文本 + 3 drift guards + 修 R2-8d D5 forbidden 列表退役 `hard-truncates`，P3 校准）+ 修 1 现有 anchor test | T1（常量 rename） | +3 tests, ±2 modified |
| **T5** | Cross-channel consistency drift guard test + final smoke (1233 pass) | T1-T4 | +1 test |
| **T6**（轻 housekeeping） | `docs/metrics/agent-cycles-schema.md` A2 multi-LIKE SQL pattern note（P4 校准）| 独立 | docs only |

每 task 双 review（spec compliance + code quality）+ final code-reviewer agent。

**预期总产出**：
- 测试规模 1215 → **1233**（+18 净）
- source 改动 ~35 行（15 cli/app.py + 8 persona.py + 12 helper / secondary cap branch）
- tests 改动 ~55 行（35 new + 20 modified）
- spec ~600 行（本文档）
- plan ~1200 行（writing-plans skill 产出）
- A2 docs ~20 行（multi-LIKE + Python helper 双例）
- **PR 量级与 R2-8d (PR #39) 同档**

## 11. Compatibility + rollback

- **No DB migration** — 仅 source code + persona text；`agent_cycles` schema 不变
- **No breaking change to public API** — `_truncate_decision` 默认 arg 名 `hard_cap` → `hard_cap_words` + 新增 `hard_cap_chars`，函数 private (`_` 前缀)，无外部 caller
- **Constant rename**: `CYCLE_DECISION_HARD_CAP` → `CYCLE_DECISION_WORD_CAP`，仅 2 source 引用 (`persona.py:10` / `cli/app.py:16,93`)，tests 无引用
- **新增常量**: `CYCLE_DECISION_CHAR_HARD_FLOOR = 8000`（P1 secondary safety net），仅 cli/app.py 内部用，silent 不暴露 agent
- **Rollback**: 单 commit revert（无 schema 残留 / 无 data migration）；
  rollback 后 R2-8d 状态原样恢复

## 12. Related work

### 直接前置
- **R2-7 (PR #35)** — `agent_cycles` schema reframe (decision_logs → agent_cycles, decision: Text)；R2-Next-A 不动 schema
- **R2-8a (PR #36)** — cycle log narrative redesign；R2-Next-A 不动 render
- **R2-8b (PR #38)** — priors injection MVP（载荷源头）；R2-Next-A 不动 query
- **R2-8d (PR #39)** — 5-field persona calibration + cap 1200→4000；R2-Next-A 反转 D5 silent guardrail 哲学

### 上位 / 旁系
- **`project_agent_reflection_tools_candidate`** — 3-tier journal/reflections/playbook；Bundle C 升级若启动会与之合并
- **`project_w2_observation_inventory_kickoff`** — 本议题 inventory 入口
- **`project_w2_prep_progress`** — W2 prep 全 pipeline
- **`project_r2_8b_legacy_decision_restore_boundary`** — 开发者契约（不 restore R2-7 前 session）；R2-Next-A 沿用

### 反向 reject
- **N9 (`project_n9_derive_decision_limit_order_blindspot`)** — wontfix-by-design via R2-7；inventory P0-2 误判已 reframe 为 P3 verification
- **`project_persona_dead_config_decision`** — A1 (2026-04-15) 观察期哲学，不在 R2-Next-A scope
- **R2-6** — persona 5 数值字段 wontfix-by-design；与本议题方向一致（discipline 优先 enforcement）

## 13. 文档维护

- 本 spec 落地后 commit 为 R2-Next-A 第 1 个 commit（per `feedback_plan_doc_commit_first`）
- 后续 plan 文档由 writing-plans skill 产出，作第 2 个 commit
- impl T1-T6 commit 顺序按依赖（T1→T2→T3→T4→T5→T6）
- Landing 后更新 memory：`project_w2_prep_progress` § 14 / `project_w2_observation_inventory_kickoff` 标 ✅ R2-Next-A landed / 创建 `project_r2_next_a_f1_loop_closure` 候选 (W3 数据触发后启动)
