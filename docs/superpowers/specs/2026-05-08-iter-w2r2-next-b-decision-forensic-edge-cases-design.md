# R2-Next-B — Decision Forensic Edge Cases

**Date**: 2026-05-08
**Iter id**: w2r2-next-b
**Status**: design (brainstorm-approved, awaiting user spec review)
**Predecessors**: R2-7 (PR #35) / R2-8a (PR #36) / R2-8b (PR #38) / R2-Next-A (PR #40)
**Inventory source**: `.working/sim8-w2-inventory.md` §3 P1-3 + P1-4 + §6 Cluster B + §7 R2-Next-B
**Handoff**: `.working/sim8-w2-handoff.md` §4

---

## 1. Problem statement

W2 真实观察期 sim #8 (`8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`, 178 cycles / 19.2h / 14.4M tokens) 在 trade outcome / cycle stability 复盘中暴露 **2 个 decision 持久化路径的 edge case**，互不耦合但共属 "decision forensic edge cases" 主题：

### 1.1 P1-3 — sim_exchange limit order async race

Cycle `4de0585a` (UTC 01:09:45) 实证：
- `place_limit_order` 调用后 **15ms 内** 同 cycle 完成 fill（DB: place at `01:09:45.287`, fill at `01:09:45.302`）
- 但 agent decision prose 写：`"The limit order was placed but apparently not filled (no pending orders, no fill notification)"`
- next cycle `6b0c8bc3` 由这次 fill 触发，agent 才察觉

**根因**（代码事实，`src/integrations/exchange/simulated.py:243-280` + `:1109-1119`）：
- `create_order(order_type="limit")` synchronous return `Order(status="open")`，**不等 fill**（这是正确设计）
- `_matching_loop` 是独立 asyncio task，通过 `_ccxt.watch_ticker()` 推进 ticker；每 tick 调 `_process_tick` → `_execute_limit_fill` (line 525) → `_dispatch_fill_event` 触发下个 conditional cycle
- 这是 **OKX live 也存在的真实异步行为**（撮合 engine vs API call 解耦），**不是 sim-only bug**
- agent prose "limit not filled" 在写出来的当下事实正确（tool return 时确实没 fill），但 mental model 缺 "fill 走独立通道" 的认知

**性质**：信息架构问题，非 race bug；不修法则未来 sim 同模式偶发，实盘也会有同类 mental model gap。

### 1.2 P1-4 — retry_exhausted 部分提交无 forensic / recovery 通道

Cycle `1aa0d4e5` (UTC 09:43-09:50, conditional trigger) 实证：
- `agent_cycles` 行：`execution_status='retry_exhausted'`, `decision=NULL`, `reasoning=NULL`, `tokens_consumed=0`
- 但 `trade_actions` 表此 cycle_id 下有 **3 行已 commit**：`cancel_price_level_alert` + `set_take_profit @ 83000` + `set_stop_loss @ 81550`
- 持仓 long 0.253 BTC @ 81985, 15× lev — SL/TP 已成功落地，retry 失败**不构成风控敞口**

**根因**（代码事实）：
- `_record_action` (`src/agent/tools_execution.py:19`) 在 tool 内 sync write `trade_actions`（每 tool 执行后立即 commit，无缓冲）
- cycle retry 在 `for attempt in range(3)` (`src/cli/app.py:446`) 包住 `agent.run()`；tool 在 agent.run 内执行 → trade_actions 已 commit
- retry_exhausted 路径 (`src/cli/app.py:489-530`) 写 `AgentCycle(execution_status="retry_exhausted", decision=None, reasoning=None)`
- **无 cycle-level transaction boundary** — trade_actions 与 agent_cycles 是两套独立写入路径
- R2-8b priors filter `decision IS NOT NULL` (`src/cli/app.py:211`) 让 retry_exhausted cycle **不进** N=3 priors 注入
- → next cycle agent **看不到** 上 cycle 的 partial commit，可能误判 "未保护持仓"，重发 SL/TP

**性质**：partial commit 是 by-design 的 best-effort（acceptable in semantic），但有 2 个下游缺口：
1. **运行时缺口**：next cycle agent 不知道上 cycle 异常 + 已 commit actions → 可能重复操作 / 误判状态
2. **复盘缺口（非阻塞）**：SQL 单表查 retry_exhausted 拿不到 partial 痕迹，需跨表 join trade_actions（acceptable，非本 iter 改造目标）

### 1.3 议题边界 vs 已 reframed P3 verification

子集 ② 不含 ~~原 P0-2 N9 派生盲区~~（已 reframe 为 P3 verification, 详见 inventory §3 P0-2 段及 memory `project_n9_derive_decision_limit_order_blindspot`）。R2-7 schema reframe 设计正面验证：sim #8 0/178 cycles 含 enum tokens (`open_*` / `close_*`)，符合预期；2026-05-07 grep `src/` + `tests/` + `docs/metrics/` + memory 全部确认无 stale 工件。本 iter **不重开** N9 议题。

---

## 2. Background

### 2.1 R2-7 (PR #35, 2026-05-02) — agent_cycles schema reframe

`agent_cycles` 表 5 维度叙事 schema：
- `triggered_by` (alert / scheduled / conditional)
- `trigger_context` (JSON, 触发上下文)
- `state_snapshot` (JSON, 决策时现状)
- `reasoning` (Text/nullable, agent thinking content)
- `decision` (Text/nullable, agent message content)
- `execution_status` (String(30), 'ok' / 'usage_limit_exceeded' / 'retry_exhausted')

retry_exhausted / usage_limit_exceeded 路径写 `decision=NULL, reasoning=NULL, tokens_consumed=0`（spec §6.5 D16 forensic write）。

**本 iter 不动 R2-7 schema**：execution_status enum 不扩、decision/reasoning 字段语义不变、写入路径单一职责（不在写入端拼衍生数据）。

### 2.2 R2-8a (PR #36, 2026-05-03) — cycle log narrative

`CycleRenderContext` + 5 段渲染（Header/Reasoning/Action/Decision/Footer）。retry_exhausted 路径写 forensic AgentCycle 后 console.print Header + Footer + `[cycle aborted — N attempts failed: <error>]` 占位。

reasoning 字段在 cycle log 已渲染：当前为 NULL → 渲染空。本 iter 改造后 NULL 仍 NULL，cycle log 渲染逻辑不变。

### 2.3 R2-8b (PR #38, 2026-05-06) — priors injection

`_fetch_recent_summaries` (cli/app.py:177-235) query N=3 most recent cycles，filter `execution_status='ok' AND decision IS NOT NULL`，按 `created_at DESC, id DESC` 排序，rotating tail 注入 user prompt。

`_render_recent_summaries` (cli/app.py:238-268) 重排 ASC `(created_at, id)`，渲染 oldest → newest（reader 阅读自然顺序），每 prior 含 header `[cycle ID · trigger · UTC · ago · N words]` + body（truncated decision）。

**本 iter 改造**：去掉 `decision IS NOT NULL` filter（议题：filter 隐瞒 retry_exhausted/usage_limit_exceeded cycle，导致 next cycle agent 看不到异常），扩展 `_render_recent_summaries` 处理 NULL decision 三态（ok+NULL / retry_exhausted / usage_limit_exceeded）。R2-8b 排序 / N=3 / session-bound / fail-isolated 边界保持。

### 2.4 R2-Next-A (PR #40, 2026-05-07) — F1 length feedback loop closure

ASC ordering F4 决议保留（reader sees oldest → newest naturally）。priors 与本 iter 三态渲染兼容：NULL decision body 渲染时长度极短（< 200 chars），不影响 R2-Next-A 的 700-word cap / D2 word count header。

---

## 3. Design

### 3.1 F-P13 — `place_limit_order` tool result 异步提示

#### 3.1.1 改动位置

`src/agent/tools_execution.py:356`（return string）

#### 3.1.2 改后形态

```python
# Source-level: single-line f-string 改 3-element Python implicit string concat。
# Runtime user-facing: 原第一行 content 完全不变（implicit concat 等价），但
# 末尾从无换行变有换行（追加 `\n`），再追加 Note: 第二行。最终 return 是
# 2 行 string："Limit order placed: ... ID: <uuid>\nNote: This tool only ..."
return (
    f"Limit order placed: {side} {quantity:.6f} @ {price:.2f}, "
    f"{actual_leverage}x | ID: {order.id}\n"
    "Note: This tool only submits the order — it does not mean the order has been filled."
)
```

#### 3.1.3 设计依据

1. **教育而非约束** — 告诉 agent "此工具的功能边界 = 提交"，让它建立正确 mental model；不强制改变行为
2. **OKX live 同语义** — OKX 真实 API 也是异步成交，未来切实盘 prose 也正确
3. **对齐 agent 实证用词** — sim #8 误读 prose 是 `"limit not filled"`，提示用 `"has been filled"` 直接命中 agent mental concept；避免抽象术语 `"execution"` 解读歧义
4. **fact-only 纯度** — 不嵌入 "call get_open_orders" / "subsequent cycle" 隐式 hint；与 Iter 4 prompt optimization 哲学一致（agent agency）
5. **Append-only 改动** — runtime user-facing 第一行 content 不变（Python implicit string concat 等价），仅追加 `\n` + Note: 段；不破坏 R2-8a session log 渲染对 multi-line tool return 的假设（display.py:213 `_summarize_place_limit_order` 用 `re.search` 仅匹配第一行模式，新增第二行不影响 summarize）；视觉上工具仍主要是第一行响应

#### 3.1.4 不引入

- ❌ persona Layer 1 / Cross-Tool Behavior 加 bullet（agent 在 place_limit_order 现场看不到 persona）
- ❌ 改 `create_order` 内部行为（`_matching_loop` async 撮合是 OKX live 也有的真实语义）
- ❌ tool result 内嵌 verify 工具列表（让 agent 自主选择 verify 路径）

#### 3.1.5 W3 实证 trigger（独立后续 PR 候选）

如果 W3 sim 仍出现 agent prose `"limit not filled"` 误读，再 escalate 加更具体提示（如 "absent from get_open_orders may mean filled OR cancelled OR never placed"）。本 iter 数据不支持启动。

### 3.2 F-P14 — R2-8b priors 渲染扩展（NULL decision 三态）

#### 3.2.1 设计哲学

- **数据完整性**：priors 如实反映最近 N=3 cycle 状态，不论 ok / retry_exhausted / usage_limit_exceeded（之前 R2-8b filter 隐瞒了异常）
- **写入路径单一职责**：cycle 状态由 `execution_status` 字段承载，`decision`/`reasoning` 字段不被衍生信息污染
- **渲染时智能化**：NULL decision 时由系统根据 `execution_status` 动态生成提示文案
- **统一架构**：ok+NULL / retry_exhausted / usage_limit_exceeded 三态用同一个 priors 通道处理，不引入独立 hint section
- **agent agency**：不告诉 agent 具体 partial commit actions，让它主动 verify 当前状态（与 F-P13 同源哲学）

#### 3.2.2 改动 1 — `_fetch_recent_summaries` 移除 filter

**位置**：`src/cli/app.py:198-218`

**改前**：
```python
result = await session.execute(
    select(
        AgentCycle.id,
        AgentCycle.cycle_id,
        AgentCycle.triggered_by,
        AgentCycle.decision,
        AgentCycle.created_at,
    )
    .where(
        AgentCycle.session_id == session_id,
        AgentCycle.execution_status == "ok",        # ← 删
        AgentCycle.decision.is_not(None),            # ← 删
    )
    .order_by(
        AgentCycle.created_at.desc(),
        AgentCycle.id.desc(),
    )
    .limit(n)
)
```

**改后**：
```python
result = await session.execute(
    select(
        AgentCycle.id,
        AgentCycle.cycle_id,
        AgentCycle.triggered_by,
        AgentCycle.decision,
        AgentCycle.execution_status,                  # ← 新增 SELECT
        AgentCycle.created_at,
    )
    .where(
        AgentCycle.session_id == session_id,         # ← 仅保留 session-bound
    )
    .order_by(
        AgentCycle.created_at.desc(),
        AgentCycle.id.desc(),
    )
    .limit(n)
)
```

**Docstring 同步改写要点**（cli/app.py:180-196 必须改）：

| 旧 docstring 段 | 新 docstring 形态 |
|---|---|
| 第 1 行 `"Fetch the N most recent ok cycles (with non-NULL decision)"` | `"Fetch the N most recent cycles for a session (all execution statuses; render layer handles three-state branching)"` |
| Filters 段：`execution_status='ok'` + `decision IS NOT NULL` | 删除该两行 filter；保留 `session_id matches (D-U1-a: session-bound, no cross-session leak)` |
| Returns [] on 段：`"Forensic-only history (all cycles non-ok)"` | 删除该行（forensic 不再返回 []）；保留 `First cycle in session` + `DB error fail-isolated` |
| 新增段（解释三态分流）| `"Caller renders ok+valid decision normally (per-prior word count header); render layer dispatches retry_exhausted / usage_limit_exceeded / ok+NULL to _render_empty_decision_body for system-generated body."` |

Ordering 段（DESC + id.desc tie-breaker）保持不动。

#### 3.2.3 改动 2 — `CycleSummary` dataclass 扩字段

**位置**：`src/cli/app.py`（CycleSummary 定义处）

```python
@dataclass(frozen=True)
class CycleSummary:
    id: int
    cycle_id: str
    triggered_by: str
    decision: str | None             # ← 类型改：str → str | None
    execution_status: str            # ← 新增字段
    created_at: datetime
```

`_fetch_recent_summaries` 构造 `CycleSummary` 时填入 `execution_status=r.execution_status`、`decision=r.decision`（不强转 ""）。

#### 3.2.4 改动 3 — `_render_recent_summaries` 三态分支

**位置**：`src/cli/app.py:238-268`

**Docstring 增补三态条件性**：原 docstring 第 247-250 行 `"R2-Next-A D2: each per-prior header includes · {N} words"` 改后**不再 unconditional** —— NULL decision 行 header 缩短不含 word count（AC-11 enforce）。原 docstring **不能完整保留**，需增补 NULL decision 三态条件性。函数签名不变，for-loop 内部增三态判断。

**Docstring rewrite 要点**：
| 旧 docstring 段 | 新 docstring 形态 |
|---|---|
| `"R2-Next-A D2: each per-prior header includes · {N} words showing the ORIGINAL word count (pre-truncation)..."` | `"R2-Next-A D2: ok cycles with non-NULL decision render header with · {N} words showing ORIGINAL word count (pre-truncation). NULL decision rows (forensic / ok+empty, F-P14) shorten the header (no word count) and substitute body via _render_empty_decision_body for system-generated text."` |
| ASC sort + id tie-breaker 段 | 保留不动 |
| 空 list `""` return 段 | 保留不动 |

```python
def _render_recent_summaries(
    summaries: list[CycleSummary], now: datetime,
) -> str:
    """<existing docstring with F-P14 三态条件性增补 per above>"""
    if not summaries:
        return ""

    blocks = []
    for s in sorted(summaries, key=lambda x: (x.created_at, x.id)):
        cycle_id_short = s.cycle_id[:8]
        utc_str = s.created_at.strftime("%Y-%m-%d %H:%M UTC")
        ago = _format_relative_time(now, s.created_at)

        # F-P14: NULL decision 三态渲染
        if not s.decision:
            header = (
                f"[cycle {cycle_id_short} · {s.triggered_by} · "
                f"{utc_str} ({ago})]"
            )
            body = _render_empty_decision_body(s.execution_status)
        else:
            word_count = _count_words(s.decision)
            header = (
                f"[cycle {cycle_id_short} · {s.triggered_by} · "
                f"{utc_str} ({ago}) · {word_count} words]"
            )
            body = _truncate_decision(s.decision)

        blocks.append(f"{header}\n{body}")

    header_top = "Your prior cycle summaries (most recent N=3, from this session):"
    return f"{header_top}\n\n" + "\n\n".join(blocks)
```

#### 3.2.5 改动 4 — 新 helper `_render_empty_decision_body`

**位置**：`src/cli/app.py`（`_render_recent_summaries` 之前定义）

```python
def _render_empty_decision_body(execution_status: str) -> str:
    """Render system-generated body for cycles that left no decision summary.

    Three known statuses (internal branching by status, but agent-facing text
    does not expose schema field names — agent reads natural language only):
      - 'ok' + NULL/empty decision: defensive branch — cycle ran successfully
        but agent emitted no final message text (rare; pydantic-ai
        `result.output` can be "" or None when agent only emits tool calls
        without a final TextPart)
      - 'retry_exhausted': all retry attempts failed; partial trade_actions
        may have committed before abort
      - 'usage_limit_exceeded': UsageLimitExceeded raised mid-cycle; partial
        trade_actions may have committed

    `retry_exhausted` and `usage_limit_exceeded` share identical agent-facing
    text (D9): the agent's response to either is the same — re-verify state.
    Status differentiation is a developer-layer concern (DB / cycle log).

    Unknown statuses fall through to a fallback string for forward compatibility
    with future execution_status enum extensions; status value is NOT
    interpolated into the agent-facing text (would expose schema artifact).

    Note: this function returns a system-generated body text inserted into the
    priors block in place of agent-authored decision content. Length budget
    accounting (R2-Next-A D2) tracks agent decision length only; system-generated
    bodies are not counted in the per-prior word_count header (header is
    shortened to omit the `· N words` segment when decision is NULL).
    """
    if execution_status == "ok":
        return "(This cycle did not leave a summary.)"
    if execution_status in ("retry_exhausted", "usage_limit_exceeded"):
        return (
            "⚠️ The previous cycle did not complete normally. Some actions "
            "may have already taken effect. Please verify the current state "
            "of your position, pending orders, and alerts before deciding "
            "what to do."
        )
    return "(The previous cycle ended in an unexpected state.)"
```

#### 3.2.6 改动 5 — 不动的范围

- ❌ retry_exhausted / usage_limit_exceeded 写入路径（`cli/app.py:454-530`）— `decision=None, reasoning=None` 保持
- ❌ R2-8b排序 ASC by (created_at, id) 保持（reader sees oldest → newest）
- ❌ R2-8b 注入位置（trigger context 之后，memory_context 之前）保持
- ❌ R2-Next-A D2 word count header（非 NULL decision 分支保持原样）
- ❌ `_truncate_decision` / `_count_words` / `_format_relative_time` 三个 helper 不动
- ❌ `_build_recent_summaries_block` outer wrap fail-isolated 边界保持

### 3.3 端到端示例

**场景**：sim #8 cycle `ee42e1db` (10:06 UTC scheduled) 起手，按 `created_at DESC LIMIT 3` 取最近 3 cycle:
- `1aa0d4e5` 09:43 conditional (retry_exhausted, decision=NULL)
- `2ee32bcd` 09:41 scheduled (ok, decision="...")
- `32babac6` ~09:30 conditional (ok, decision="...")

**改后 prompt**（priors block 部分）：
```
Your prior cycle summaries (most recent N=3, from this session):

[cycle 32babac6 · conditional · 2026-05-06 09:30 UTC (36 min ago) · 286 words]
... (close_position decision content)

[cycle 2ee32bcd · scheduled · 2026-05-06 09:41 UTC (25 min ago) · 312 words]
... (open_position decision content) Plan to set SL ~81,550 and TP ~83,000.

[cycle 1aa0d4e5 · conditional · 2026-05-06 09:43 UTC (23 min ago)]
⚠️ The previous cycle did not complete normally. Some actions may have already taken effect. Please verify the current state of your position, pending orders, and alerts before deciding what to do.
```

**关键差异点**：
1. 末尾 prior（`1aa0d4e5`） header 缺 `· N words` 标记（缩短形态，agent 视觉感知"这条不一样"）
2. body 是系统生成 ⚠️ 文案，**纯 agent-native 语言**：不暴露 schema 字段名（如 `status=retry_exhausted`）也不列具体工具名（如 `get_position`），仅功能性描述 verify scope（position / pending orders / alerts 三 dim）
3. agent 阅读时序连贯：`09:30 close → 09:41 open + 计划 SL/TP → 09:43 异常`，因果链支持精准 verify — agent 自主选择 query 路径（`get_position` / `get_open_orders` / `get_active_alerts` 等已知工具），看到 SL/TP 已挂即不重发

---

## 4. Acceptance Criteria

| AC | 描述 | 验证方式 |
|---|---|---|
| **AC-1** | `place_limit_order` return string 包含 `"only submits"` AND `"has been filled"` | T-FP13.2 |
| **AC-2** | `place_limit_order` return string 仍含 `"ID: "` + UUID 形态（强断言 `r"ID: [a-f0-9]{8}-"` 显式 dash 防 regex 弱命中）| T-FP13.1 |
| **AC-3** | `place_limit_order` return string 不含 banned decision wordlist | T-FP13.3 |
| **AC-4** | `_fetch_recent_summaries` 返回包含 retry_exhausted / usage_limit_exceeded cycle（filter 已删）| T-FP14.1 |
| **AC-5** | `CycleSummary.execution_status` 字段从 query 正确填充 | T-FP14.2 |
| **AC-6** | `_render_recent_summaries` ok cycle with valid decision 仍渲染原 header (`· N words`) + truncated decision body（regression）| T-FP14.3 |
| **AC-7** | `_render_empty_decision_body('ok')` 返回 `"(This cycle did not leave a summary.)"` | T-FP14.4 |
| **AC-8** | `_render_empty_decision_body('retry_exhausted')` 含 `"⚠️"` + `"did not complete normally"` + `"position"` + `"pending orders"` + `"alerts"` + `"verify"`（不含 schema 字段名 `"retry_exhausted"`，不含工具名 `"get_position"` 等）| T-FP14.5 |
| **AC-9** | `_render_empty_decision_body('usage_limit_exceeded')` 与 AC-8 文案完全一致（D9 共享 body）| T-FP14.6 |
| **AC-10** | `_render_empty_decision_body('unknown_xxx')` 返回兜底 `"(The previous cycle ended in an unexpected state.)"`（status 值不插值进文案）| T-FP14.7 |
| **AC-11** | NULL decision 行 header 不含 `· N words` 部分（缩短形态）| T-FP14.8 |
| **AC-12** | retry_exhausted 写入路径 reasoning 字段保持 `None`（防误改 → 单一职责）| T-FP14.9 |
| **AC-13** | 全测试套件：1230 → 1242 passed + 3 skip 区间，零 regression | `uv run pytest` |
| **AC-14** | 不引入 schema 变化、Alembic migration、新表、新索引 | `git diff alembic/` 为空；`git diff src/storage/models.py` 为空 |
| **AC-15** | session log render（R2-8a `format_cycle_output`）对 NULL reasoning 处理无视觉异常 | **手动 smoke**（W3 sim 自然触发 retry_exhausted / usage_limit_exceeded 时 inspect cycle log；如 W3 不触发，推迟到首次自然出现的 sim）。**注**：T-FP14.9 仅断言 DB 写入路径 reasoning=None，不覆盖 cycle log render；本 AC 与 T-FP14.9 解耦 |

---

## 5. Test Strategy

### 5.1 Test 文件分布

| 文件 | 用途 | 新增/修改 |
|---|---|---|
| `tests/test_fact_only_wordlist.py`（扩展，line 629-719 已含 execution tools 段）| F-P13 place_limit_order return string fact-only regression + format 断言 | 新增 3 tests (T-FP13.1/2/3) — 复用现有 `_scan(output)` helper + `FACT_ONLY_BANNED_WORDS_RE` / `FACT_ONLY_BANNED_PHRASES_RE` regex 列表（line 10-30）|
| `tests/test_cycle_summary_injection.py`（已存，R2-8b 引入）| F-P14 priors filter 删除 + 三态渲染 | 改 2 既有 fetch test 断言形态（B1）+ 改 `_make_summary` factory（B3，line 426 加 execution_status 参数）+ 新增 9 tests (T-FP14.1-9) |
| `tests/test_display_cycle.py`（既有）| F-P13 multi-line return 兼容 `summarize_tool` | 修订 `test_summarize_place_limit_order` (line 280)，验证 multi-line content 仍正确 summarize（C4 miss）|

**Factory 复用**：所有新增 cycle insert 测试用既有 `_add_cycle` test-local factory（`tests/test_cycle_summary_injection.py:262`，已支持 `execution_status` + `decision=None` kwargs）。`tests/_fixtures.py` 不动（无 cycle factory）。

### 5.2 T-FP13.x 详情

测试位于 `tests/test_fact_only_wordlist.py` 末尾追加，复用既有 `_scan(output)` helper (line 30) + 文件级 regex 列表 + `MockDeps` dataclass (line 36-60)。

**Setup helper**：现有 `_invoke_place_limit_order` (line 716-719) 走 `side="neutral"` 早 return 不覆盖新加的 Note: 段，必须新建对称命名的 `_invoke_place_limit_order_happy(deps, mocker)` helper（仿 `_invoke_open_position` line 642 的 5-mock 链 pattern）：

```python
async def _invoke_place_limit_order_happy(deps, mocker):
    """Happy path: full mock chain through create_order (covers new Note: line)."""
    from src.agent.tools_execution import place_limit_order
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.set_leverage = AsyncMock(return_value=None)
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=8000.0, used_usdt=2000.0,
    ))
    deps.exchange.amount_to_precision = mocker.Mock(return_value=0.05)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="abc12345-6789-0123-4567-89abcdef0123",  # UUID-shaped
        symbol="BTC/USDT:USDT", side="buy", order_type="limit",
        amount=0.05, price=80000.0, status="open",
    ))
    return await place_limit_order(
        deps, "long", 80000.0, 10.0, 5, reasoning="test entry",
    )
```

**Test cases**：

```python
@pytest.mark.asyncio
async def test_place_limit_order_return_includes_async_note(mocker):
    """T-FP13.2: return string contains 'only submits' AND 'has been filled'.
    
    sim #8 cycle 4de0585a 实证误读对齐：agent prose 用词 'limit not filled'，
    提示用 'has been filled' 命中相同 mental concept。
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    assert "only submits" in result
    assert "has been filled" in result


@pytest.mark.asyncio
async def test_place_limit_order_return_format_unchanged(mocker):
    """T-FP13.1: 'ID:' + UUID format strong assertion (C3 regex 强断言).
    
    order.id = str(uuid.uuid4()) — 36 chars dash-separated; assert head 8 hex
    + dash 显式 UUID 形态防 regex 因前 8 hex 弱命中。
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    assert "ID: " in result
    assert re.search(r"ID: [a-f0-9]{8}-", result), \
        f"expected UUID format ID: xxxxxxxx-..., got: {result}"


@pytest.mark.asyncio
async def test_place_limit_order_return_no_decision_label(mocker):
    """T-FP13.3: fact-only regression — 复用 _scan(output) helper.
    
    helper 内部走 FACT_ONLY_BANNED_WORDS_RE + FACT_ONLY_BANNED_PHRASES_RE
    两套 regex（line 10-30），不是简单 wordlist literal in 检查。
    """
    deps = MockDeps()
    result = await _invoke_place_limit_order_happy(deps, mocker)
    hits = _scan(result)
    assert hits == [], f"banned regex hits: {hits}"
```

**注**：T-FP13.x 不能与现有 `parametrize` execution-tool fact-only 套件合并（早 return 路径 vs happy 路径不同 mock chain）。现有 `_invoke_place_limit_order` (line 716, 走 neutral 早 return) 保留不动，新 `_invoke_place_limit_order_happy` 与之并存。

### 5.3 T-FP14.x 详情

测试位于 `tests/test_cycle_summary_injection.py` 现有文件，复用既有 `_add_cycle` factory（line 262，已支持 execution_status / decision=None）+ 改用既有 `_make_engine_with_session` engine setup。`_make_summary` factory（line 426）必须先扩展加 execution_status 参数。

**前置 fixture 改动**（B3）：
```python
def _make_summary(cycle_id, triggered_by, decision, created_at,
                  sid=1, execution_status="ok"):  # ← 新增 execution_status kwarg
    """Test-only CycleSummary builder."""
    from src.cli.app import CycleSummary
    return CycleSummary(
        id=sid, cycle_id=cycle_id, triggered_by=triggered_by,
        decision=decision, execution_status=execution_status,  # ← 新增
        created_at=created_at,
    )
```

**注**：现有 ~10 处 `_make_summary` 调用（line 450/472/488/503/520/535/539/543/565/586/601）默认 `execution_status="ok"` 兼容，无需 call site 改动。

**新 tests**：
```python
async def test_fetch_recent_summaries_includes_retry_exhausted():
    """T-FP14.1: filter deletion → retry_exhausted cycle 进入 priors."""
    from datetime import datetime, timezone, timedelta
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-fp14-1")
    base = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    await _add_cycle(engine, "sess-fp14-1", "c-ok",
        decision="real summary", execution_status="ok",
        created_at=base)
    await _add_cycle(engine, "sess-fp14-1", "c-rx",
        decision=None, execution_status="retry_exhausted",
        created_at=base + timedelta(minutes=1))  # most recent
    rows = await _fetch_recent_summaries(engine, "sess-fp14-1", n=3)
    assert len(rows) == 2
    assert rows[0].cycle_id == "c-rx"  # most recent first (DESC)

async def test_cycle_summary_execution_status_populated():
    """T-FP14.2: CycleSummary.execution_status from query."""
    from src.cli.app import _fetch_recent_summaries

    engine = await _make_engine_with_session("sess-fp14-2")
    await _add_cycle(engine, "sess-fp14-2", "c1",
        decision=None, execution_status="usage_limit_exceeded")
    rows = await _fetch_recent_summaries(engine, "sess-fp14-2", n=3)
    assert rows[0].execution_status == "usage_limit_exceeded"

def test_render_recent_summaries_ok_cycle_unchanged():
    """T-FP14.3: regression — ok+valid decision renders original format."""
    from datetime import datetime, timezone, timedelta
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary("abc12345", "scheduled", "Some decision body.",
        now - timedelta(minutes=5), execution_status="ok")
    output = _render_recent_summaries([s], now)
    assert "· 3 words]" in output  # R2-Next-A D2 word count header maintained
    assert "Some decision body." in output

def test_render_empty_decision_body_ok():
    """T-FP14.4: ok+NULL → 'did not leave a summary'."""
    from src.cli.app import _render_empty_decision_body
    assert _render_empty_decision_body("ok") == \
        "(This cycle did not leave a summary.)"

def test_render_empty_decision_body_retry_exhausted():
    """T-FP14.5: retry_exhausted → ⚠️ + agent-native verify hint.
    
    Asserts: no schema field name leak (`"retry_exhausted"` literal absent),
    no tool name leak (`"get_position"` etc absent), only functional dim
    description (position / pending orders / alerts).
    """
    from src.cli.app import _render_empty_decision_body
    body = _render_empty_decision_body("retry_exhausted")
    assert "⚠️" in body
    assert "did not complete normally" in body
    assert "position" in body
    assert "pending orders" in body
    assert "alerts" in body
    assert "verify" in body
    # negative assertions: schema artifacts must NOT leak into agent-facing text
    assert "retry_exhausted" not in body
    assert "get_position" not in body
    assert "get_open_orders" not in body
    assert "get_active_alerts" not in body

def test_render_empty_decision_body_usage_limit_exceeded():
    """T-FP14.6: usage_limit_exceeded → identical body as retry_exhausted (D9).
    
    Both abnormal statuses share the exact same agent-facing text — agent's
    response is the same regardless of internal status.
    """
    from src.cli.app import _render_empty_decision_body
    body_retry = _render_empty_decision_body("retry_exhausted")
    body_ulx = _render_empty_decision_body("usage_limit_exceeded")
    assert body_retry == body_ulx  # exact equality (D9)
    assert "usage_limit_exceeded" not in body_ulx  # negative: no schema leak

def test_render_empty_decision_body_unknown_fallback():
    """T-FP14.7: forward compat — unknown status → generic fallback (no value insertion)."""
    from src.cli.app import _render_empty_decision_body
    body = _render_empty_decision_body("future_unknown_status")
    assert body == "(The previous cycle ended in an unexpected state.)"
    # negative: status value must NOT be interpolated (防 prompt 污染)
    assert "future_unknown_status" not in body

def test_render_recent_summaries_null_decision_header_no_word_count():
    """T-FP14.8: NULL decision row 的 header 不含 ' · N words'."""
    from datetime import datetime, timezone, timedelta
    from src.cli.app import _render_recent_summaries

    now = datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)
    s = _make_summary("abc12345", "conditional", None,
        now - timedelta(minutes=5), execution_status="retry_exhausted")
    output = _render_recent_summaries([s], now)
    # header 形态: [cycle abc12345 · conditional · 2026-... (5min ago)]
    header_line = output.split("\n")[2]  # skip top header + blank line
    assert "words]" not in header_line

async def test_retry_exhausted_writes_null_reasoning_unchanged():
    """T-FP14.9: 防误改 — retry_exhausted 写入路径 reasoning=None.
    
    Test 价值：marginal regression guard（reasoning=None 在 cli/app.py:508
    已 hardcoded，本测试 enforce 写入路径单一职责不被未来误改）。
    
    NOTE (spec § scope)：mock chain + cycle_id capture 的具体形态 plan 阶段细化
    （需 mocker.patch agent.run 抛 RuntimeError 三次 + 走 run_agent_cycle 主路径
    capture 生成的 cycle_id）。spec 不展开端到端 mock 链，避免 over-spec；
    plan 阶段 implementer 选最小 mock chain pattern 即可。
    """
    # plan 阶段细化：完整 mock + run_agent_cycle 调用 + AgentCycle row 取证
    pass
```

### 5.4 既有 test 调整

`tests/test_cycle_summary_injection.py` 中**精确 2 个**既有 test 直接依赖被删 filter；**1 个 factory** 需扩字段：

#### 5.4.1 `test_fetch_excludes_forensic_cycles` (line 324)

**改前形态**：构造 4 cycles（aa11 ok / bb22 usage_limit_exceeded / cc33 ok / dd44 retry_exhausted），断言 `{r.cycle_id for r in rows} == {"aa11", "cc33"}`（forensic 被过滤剩 2 个）。

**改后形态**：保留 fixture，断言重写为含全部 4 cycle（按 created_at DESC LIMIT 3 取最近 3：dd44 / cc33 / bb22）：
```python
rows = await _fetch_recent_summaries(engine, "sess-t1-4", n=3)
assert [r.cycle_id for r in rows] == ["dd44", "cc33", "bb22"]
# 验证 retry_exhausted / usage_limit_exceeded 进入 priors（filter 已删）
assert rows[0].execution_status == "retry_exhausted"
assert rows[0].decision is None
assert rows[2].execution_status == "usage_limit_exceeded"
```

**改测试名**：`test_fetch_excludes_forensic_cycles` → `test_fetch_includes_all_cycles_regardless_of_status`（语义反转）。

#### 5.4.2 `test_fetch_excludes_cycles_with_null_decision` (line 407)

**改前形态**：2 cycles（aa11 ok+decision / bb22 ok+NULL），断言 `[r.cycle_id for r in rows] == ["aa11"]`（NULL 被过滤）。

**改后形态**：保留 fixture，断言改为含两 cycle（按 DESC：bb22 / aa11）：
```python
rows = await _fetch_recent_summaries(engine, "sess-t1-8", n=3)
assert [r.cycle_id for r in rows] == ["bb22", "aa11"]
assert rows[0].decision is None  # ok+NULL 进 priors（render 走 ok 分支系统提示）
assert rows[0].execution_status == "ok"
```

**改测试名**：`test_fetch_excludes_cycles_with_null_decision` → `test_fetch_includes_ok_cycles_with_null_decision`（语义反转）。

**注**：原 test 注释 (line 408-413) 引用 "review F2 defensive guard `WHERE decision IS NOT NULL`" 已 stale，本 iter 主动删除该 filter；test 注释也改为反映新行为。

#### 5.4.3 `_make_summary` factory (line 426) 加 execution_status 参数

如 §5.3 前置 fixture 改动所示，~10 处 `_make_summary` call site 默认值 `"ok"` 兼容不动。但 plan 阶段必须确认现有 6 个 _make_summary 相关 render test (test_render_*) 调用仍 pass — `_render_recent_summaries` 改动后 ok+decision 路径不变，predicted regression: 0。

#### 5.4.4 既有 test 改动汇总

| Item | 文件 | 类型 | line |
|---|---|---|---|
| test_fetch_excludes_forensic_cycles → includes_all_cycles_regardless_of_status | test_cycle_summary_injection.py | 重写断言 + rename | 324-343 |
| test_fetch_excludes_cycles_with_null_decision → includes_ok_cycles_with_null_decision | test_cycle_summary_injection.py | 重写断言 + rename | 407-421 |
| `_make_summary` factory | test_cycle_summary_injection.py | 加 execution_status="ok" 默认参数 | 426-432 |
| test_summarize_place_limit_order (F-P13 兼容性验证) | test_display_cycle.py | 修订 fixture content 为 multi-line 形态 + 显式断言 multi-line 不影响 summarize | 280-286 |

**test_summarize_place_limit_order 修订说明**：实证 `src/cli/display.py:213-217 _summarize_place_limit_order` 用 `re.search(r"Limit order placed:\s*(\w+)...")` 匹配第一行模式，**新 multi-line content（第 2 行 `Note: ...`）不会 break 既有断言** — 该 test 修订实际是 nice-to-have（显式断言 multi-line robustness），不是必需。但保 §5 ↔ §6 一致性应纳入。

**Plan 阶段验证**：grep `_make_summary(` / `_fetch_recent_summaries(` 全部 call site 确认无遗漏。

### 5.5 测试规模预估

| 类别 | 数量 |
|---|---|
| F-P13 新 tests | +3 (T-FP13.1/2/3) |
| F-P14 新 tests | +9 (T-FP14.1-9) |
| 既有 test 改 | 3 (2 rename + 断言重写 + 1 修订 test_summarize_place_limit_order multi-line robustness，count 不变) |
| **净增** | **+12** (1230 → 1242 + 3 skip) |

---

## 6. File Changes

| 文件 | 改动 | 行数 |
|---|---|---|
| `src/agent/tools_execution.py` | F-P13 multi-line return（single-line 改 3-element tuple 拼接 + `\n`）| +3 |
| `src/cli/app.py` | `_fetch_recent_summaries` 删 filter + select execution_status | ~5 |
| `src/cli/app.py` | `CycleSummary` 加 execution_status 字段，decision 类型 str → str\|None | ~3 |
| `src/cli/app.py` | `_render_recent_summaries` 三态分支（保留原 docstring，仅加 NULL 分支）| ~15 |
| `src/cli/app.py` | 新 helper `_render_empty_decision_body` | ~25 |
| `tests/test_fact_only_wordlist.py` | T-FP13.x 增 3 tests（复用 `_scan` helper + 文件级 regex）| ~50 |
| `tests/test_cycle_summary_injection.py` | T-FP14.x 增 9 tests + 改 2 既有 test 断言形态 + 改 `_make_summary` factory | ~180 |
| `tests/test_display_cycle.py` | 修订 `test_summarize_place_limit_order` (line 280) 验证 multi-line content 仍 summarize 正确 | ~5 |
| spec doc (本文件) | 新增 | ~700 |
| **总计** | src ~50 + test ~235 + spec ~700 | **~985 lines** |

---

## 7. Out of Scope

### 7.1 Scope 外议题（独立后续 PR 候选）

- W3 sim 数据驱动验证：F-P13 提示是否解决 `"limit not filled"` 误读 → 留 W3 sim 后实证
- 子集 ③ Strategy quality（P1-5/6/7/9）— 留 N10 next round + W3 对比
- N6 HTF hardening（R2-Next-C 候选独立小 PR）— 与本议题不耦合
- F1 cluster Bundle B/C 升级 — 看 W3 R2-Next-A 5 项 AC 验证决定
- W2 ops backlog S1-S5 弱触发议题
- retry_exhausted 频次升级触发的硬风控（A1 观察期哲学 wontfix 一致）
- partial commit summary 的 SQL forensic 单表查询路径（acceptable via cross-table join trade_actions, sim #8 1/178 罕见路径）
- D6.I1 首笔开仓 thesis miss（cycle `9f030bb0` decision 49 chars）— 不属本 iter 议题，独立小 PR 候选

### 7.2 已 reframed 不进 scope

- N9 派生盲区已 wontfix-by-design via R2-7（PR #35），sim #8 0/178 enum tokens 实证（详见 inventory §3 P0-2 reframe + memory `project_n9_derive_decision_limit_order_blindspot`）

### 7.3 设计 explicitly rejected 的方向

- ❌ Cycle-level transaction boundary（trade_actions 缓冲到 cycle 末一起 commit）— 巨大重构，agent 看不到已执行 tool 反馈，违背 R2-7 "agent 主动决策" 哲学
- ❌ 改 sim_exchange `_matching_loop` 让 limit 同步成交 — 偏离 OKX live 真实异步语义，掩盖未来实盘相同问题
- ❌ persona Layer 1 加 "limit fill 异步" cross-tool bullet — agent 在 place_limit_order 现场看不到 persona Layer 1
- ❌ next-cycle 独立 ⚠️ section（融入 R2-8b priors block，不引入第二个 hint 通道）
- ❌ retry_exhausted 写入端拼 trade_actions 摘要进 reasoning（写入路径单一职责，不污染字段语义）
- ❌ R2-8b 排序倒序（newest → oldest）— 因果是单向的，narrative ordering 比 newsfeed ordering 更易 LLM 推理（详见 §2.4 + brainstorm 决议）

---

## 8. Risks

| ID | Risk | 缓解 |
|---|---|---|
| R1 | retry_exhausted/usage_limit_exceeded cycle 进 priors 后 N=3 priors 中可能 1-2 条是 ⚠️ 提示 → 真正 ok cycle priors 减少 | 触发频次：sim #8 1/178 retry_exhausted, 0 usage_limit_exceeded（弱样本）。**注**：W3 + 1M context + DeepSeek thinking mode 可能 usage_limit_exceeded 频次上升；本 iter 不落地 monitoring，**follow-up candidate**：未来加 logger.warning when N=3 中 ≥2 是 forensic 监控（不阻塞当前 iter） |
| R2 | NULL decision 对 R2-Next-A D2 word count header 的影响 | NULL decision 行 header 缩短不含 word count（T-FP14.8 enforce），不影响其他 prior 的 D2 length feedback signal |
| R3 | `_render_empty_decision_body` 的兜底 fallback 在未来扩 enum 时被沉默 | T-FP14.7 测试 enforce fallback string 形态；§3.2.5 docstring 显式标"forward compat"；如未来扩 enum，新会话 grep `_render_empty_decision_body` 即可定位调整点 |
| R4 | NULL decision 行的 system 提示字符数（~220 chars / ~30 words）算入 R2-Next-A 的 cycle decision word/char budget | system 文案不算 agent decision；§3.2.5 docstring 已显式声明边界；R2-Next-A 的 budget 仅约束 agent 自己写的 decision；本 iter 不改 R2-Next-A budget 逻辑 |
| R5 | F-P13 tool result 多一行 `Note:` 增加 cycle log render 渲染长度 | 单行 ~80 chars，可忽略；R2-8a cycle log render 已支持任意长 tool return |
| R6 | session log render（cycle log）对 retry_exhausted cycle reasoning=NULL 的渲染 | 不变（reasoning 字段 NULL 时本就渲染 placeholder），T-FP14.9 enforce reasoning 不被误填，不破坏 R2-8a 渲染逻辑 |
| R7 | `_render_empty_decision_body` fallback 注入 prompt 的潜在 schema artifact / prompt injection 面 | **已消除**：D10 修订后 fallback 返回固定 string `"(The previous cycle ended in an unexpected state.)"`，**不插值 status 值**进 agent-facing 文案；T-FP14.7 negative assertion enforce status 值不出现在 body。开发者 forensic 仍可通过 DB / cycle log 看到精确 status 值（写入路径仅 3 处 `cli/app.py:468/510/596`，受控 enum）|

---

## 9. Memory References

### 9.1 必读 memory

- `project_w2_observation_inventory_kickoff` — 议题分布 + handoff pointer（本 iter 上位入口）
- `project_n9_derive_decision_limit_order_blindspot` — N9 wontfix-by-design via R2-7（确认本 iter 不重开）
- `project_r2_8b_legacy_decision_restore_boundary` — R2-8b filter 议题决议背景（本 iter 改 filter，但不 restore legacy）
- `project_w2_ops_backlog` — S1-S5 弱触发不进 scope

### 9.2 必读相邻 spec

- `2026-05-01-iter-w2r2-7-agent-cycle-schema-reframe-design.md` — R2-7 schema 设计（reasoning/decision/execution_status 字段语义）
- `2026-05-02-iter-w2r2-8a-cycle-log-narrative-redesign-design.md` — R2-8a forensic 写入路径（execution_status="retry_exhausted" + cycle log render）
- `2026-05-06-iter-w2r2-8b-cycle-summary-injection-design.md` — R2-8b priors filter / `_fetch_recent_summaries` 设计（本 iter 主要改造目标）
- `2026-05-07-iter-w2r2-next-a-f1-feedback-loop-design.md` — R2-Next-A D2 word count header（与本 iter NULL decision 三态兼容）

### 9.3 数据来源

- `.working/sim8-w2-inventory.md` §3 P1-3 + P1-4 + §6 Cluster B
- `.working/sim8-w2-inventory-parts/dim-2-trade-outcomes.md` §3 议题 4（cycle `4de0585a` race）
- `.working/sim8-w2-inventory-parts/dim-6-9-misses-stability.md` §D9（cycle `1aa0d4e5` retry_exhausted）

---

## 10. Brainstorm Decisions Summary（防 review 议题反复）

下列决议已在 brainstorm 阶段闭环，未来 review 阶段不再重开：

| Decision | 取值 | 理由 |
|---|---|---|
| **D1** Scope | 单 PR 打包 P1-3 + P1-4 | 同源 "decision forensic edge cases" 主题；改动量都极小；review overhead 节省 |
| **D2** P1-3 修法 | tool result 加单句异步提示 | 教育 agent mental model；与 OKX live 同语义；fact-only 纯度高；零 schema/行为变 |
| **D3** P1-3 措辞 | `"This tool only submits the order — it does not mean the order has been filled."` | 对齐 sim #8 agent 实证用词 `"filled"`；避免抽象术语 `"execution"` |
| **D4** P1-4 修法 | R2-8b priors filter 改造 + 三态渲染（不引入独立 hint section、不在写入端拼数据） | 数据完整性 + 写入路径单一职责 + 渲染时智能化 + 统一架构 |
| **D5** Partial commit details | 不告诉 agent 具体 actions（give agent agency） | 与 F-P13 同源哲学；agent 自主 verify state |
| **D6** Priors 排序 | 保持 ASC by (created_at, id)（oldest → newest）| 因果单向、narrative reasoning、LLM recency-at-boundary 优势；R2-8b F4 决议保留 |
| **D7** Recovery hint position | 融入 priors block 内（NULL decision 渲染分支），不独立 section | 与 R2-8b "agent 自己写自己读"哲学统一；避免双重注入冲突 |
| **D8** Reasoning 字段在写入端 | 保持 None（不拼 trade_actions） | 写入路径单一职责，不污染字段语义；SQL forensic 通过跨表 join 已可行（acceptable for sim #8 1/178 罕见路径） |
| **D9** ⚠️ 文案统一 | retry_exhausted 与 usage_limit_exceeded **共享完全一致的 agent-facing 文案**（不插值 status 字段名） | 两者都是 "cycle 异常 + partial commit 可能性" 同源场景，agent 应对方式相同；status 字段差异是开发者 forensic（DB / cycle log）层关心的事，prompt 层不暴露 schema artifact |
| **D9.a** ⚠️ emoji 前缀选择 | 保留 ⚠️ 不改纯文本 marker（如 `[FORENSIC]` / `[ABNORMAL]`）| 与 R2-8a (PR #36) cycle log forensic Header 同 marker 保信号一致（agent 跨场景识别"异常 cycle"）；DeepSeek thinking mode 在 sim #6/#7/#8 实证 emoji attention 工作正常；emoji ~1-2 tokens 反而比 `[FORENSIC]` 6 tokens 省；与 banned wordlist 无冲突保 fact-only 纯度。alternatives `[FORENSIC]` / `[ABNORMAL]` rejected because 引入与 R2-8a 信号分裂（agent 需 mental map 两个等价 marker）。例外触发：W3+ sim 出现 agent 解读 ⚠️ 失效实证 OR 切换到 emoji-attention-weak LLM |
| **D9.b** Verify scope 描述形态 | 用功能性 dim 描述（position / pending orders / alerts），不列具体工具名（如 `get_position`）| 与 F-P13 哲学一致（give agent agency）；agent 已知 verify 路径，列工具名是 implementation detail 冗余；工具名漂移风险消除（rename 时 spec 文案 stale 风险消除）；"position / pending orders / alerts" 是 agent native 概念，给最小 scope hint 避免漏检 |
| **D10** 未知 status 兜底 | `(The previous cycle ended in an unexpected state.)` 固定 string，**不插值 status 值** | forward compat 防止未来 enum 扩展时沉默忽略；status 值不暴露进 prompt（防 schema artifact + 消除潜在 prompt injection 面）|

---

## 11. Spec Self-Review Checklist

- [x] No "TBD" / "TODO" placeholders（T-FP14.9 mock chain 显式声明 plan 阶段细化，非 placeholder）
- [x] No internal contradictions（D1-D10 + D9.a + D9.b 互不冲突；§3 design / §4 AC / §5 test 一致；F-P13 与 F-P14 哲学完全一致：均用功能性描述，不暴露 schema/工具名）
- [x] Scope focused（单 iter 单 PR ~50 src + ~235 test）
- [x] No ambiguous requirements（每 AC 有具体 test 名锚定；AC-15 与 T-FP14.9 明确 decouple；§5.2 T-FP13.x 用 MockDeps + `_invoke_place_limit_order_happy` helper 不留 vague code 引用）
- [x] 数据 baseline cited（§1.2 cycle `1aa0d4e5` 实证 + §3.3 端到端示例 `N min ago` 形态对齐 `_format_relative_time`）
- [x] Predecessors / 不动点 / OOS 全部列明
- [x] Brainstorm decisions D1-D10 + D9.a + D9.b 提供 future-self 防议题反复（含 emoji 选择论证 + agent-native 文案哲学）
- [x] Code-fact 引用准确（`_record_action` `tools_execution.py:19` / retry path `cli/app.py:489-530`；`_add_cycle` factory `test_cycle_summary_injection.py:262`；`_make_summary` factory `test_cycle_summary_injection.py:426`；`MockDeps` `test_fact_only_wordlist.py:36-60`；`_invoke_open_position` pattern `:642`；`_summarize_place_limit_order` `display.py:213`；`_format_relative_time` `cli/app.py:107`）
- [x] **Docstring 同步改写要点显式列出**（§3.2.2 `_fetch_recent_summaries` + §3.2.4 `_render_recent_summaries`）— 防 docstring 与改后行为相反误导 implementer
- [x] 26 项 reviewer finding 全部 inline fix（首轮 13 + E 类 2 + 第二轮 4 + 第三轮 agent-native 重写 4 + 第四轮 docstring 同步 + ago typo + T-FP14.9 plan 细化 + §3.1.2 措辞精化 5 项）
- [x] Agent-facing prompt 不暴露 schema artifact（execution_status 字段名 / 工具名 / 实现细节）— T-FP14.5/6/7 negative assertion enforce
