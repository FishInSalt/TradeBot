# Iter W2 R2-8b — Cycle Summary Injection (N10 MVP)

**Date**: 2026-05-06
**Status**: Spec (brainstorm done, plan/impl pending)
**Branch**: `feature/iter-w2r2-8b-cycle-summary-injection`
**议题序**: W2 prep round 2 — R2-8a ✅ → R2-8c ✅ → **R2-8b** → R2-9 → 启 W2

---

## 0. TL;DR

把每 cycle 的 `agent_cycles.decision` 字段从"自由 closing 文本"重塑为 **trader-native 5 字段结构化 cycle closing summary**（生产侧），并通过 `cli/app.py` user message prefix 注入 **N=3 most recent prior summaries**（消费侧），建立 cross-cycle judgment continuity。

| 改动维度 | 范围 |
|---|---|
| 主入口 1 | `src/agent/persona.py` — 新增 Layer 1 末段 `## Cycle Closing Summary` section（5 字段引导 + 三层 cap + critical events 列举）|
| 主入口 2 | `src/cli/app.py` — `run_agent_cycle()` 入口加 query + render + inject helpers，注入位置 trigger context → recent → memory |
| Schema | 不动（R2-7 `agent_cycles.decision: Text \| None` 已铺好）|
| 改动量 | ~130 行 source + ~390 行测试 = ~520 行净增（**26 新 tests** enumerated；含 review round 1 F1/F2/F3 加 3 项 + round 2 F1/F3/F4 加 1 新 test + 2 升级）|
| 关联 | R2-7 ✅ landed (PR #35) / R2-8a ✅ landed (PR #36) / R2-8c ✅ landed (PR #37) / R2-9 终验 |

---

## 1. 议题源由

### 1.1 R2-8a brainstorm 拆出 + 议题升级路径

原议题 `project_n10_recent_decisions_context_injection` (2026-04-30) 框架是"注入前 N=2~3 次 `decision_logs.reasoning` + 时间戳到 agent prompt"——但 memory 写于 R2-7 schema reframe 之前，当时 `reasoning` 字段实际存的是 `result.output` (message)，与字段名背离。

R2-7 (PR #35, 2026-05-02) 完成 5 维度叙事 schema reframe：
- `decision_logs` → `agent_cycles`
- `reasoning` 字段语义：当时存 message → R2-7 后存 thinking content (ThinkingPart)
- 新增 `decision` (Text|None) 存 message content (`result.output`)
- 新增 `trigger_context` / `state_snapshot` 客观快照字段

R2-8b 议题在 R2-7 之后**重新定位**：注入字段从"reasoning (thinking)" 改为 "decision (message)"，理由详见 §3.1。

### 1.2 R2-8b 在 W2 prep 序列中的位置

```
W2 prep round 2:
  R2-1 ✅ (set_price_alert) ... R2-7 ✅ (schema reframe)
  → R2-8a ✅ (cycle log narrative)
  → R2-8c ✅ (tool output sectioning)
  → R2-8b (cycle summary injection) ← 本议题
  → R2-9 (W2 startup smoke)
  → 启 W2 真实观察期 24-48h
```

R2-8b 是 W2 启动前最后一个**行为改造**议题。R2-9 通过 → 才正式启 W2。

### 1.3 sim #4 实证驱动

sim #4 (15 cycles / 2h12min, BTC sim #4 session `3fe27696-6a0f-4357-8bdb-26355e67438d`) 实证当前 cycle 边界是 fresh prompt — pydantic-ai 在 cycle 间 reset history（cycle 边界 sample message_count=2 / history=99 tokens）。前一 cycle 的 thesis / 计划 / alerts 100% 不可见，暴露 5 类痛点：

| sim #4 痛点 | 实证 cycle 链 | 跨度 |
|---|---|---|
| **thesis 反复重算** | `3054 → 9f57 → cc53` 5 min 内累计 ~206k tokens 重建市场结构 + R:R | 3 cycle |
| **alerts 静默替换** | `9a91 → 008c → a464` 4 min 内 alerts 三次完全替换；与 P1-1 cancel_alert 失败有直接因果 | 3 cycle |
| **R:R 漂移无 baseline** | `fdf2 → 383d → 14c5 → e567` 持仓 30 min, agent trailing 时不知入场 R:R 2.21:1 baseline，risk 缩水 64% 不察觉 | 4 cycle |
| **入场延迟 P1-3** | `9a91 → 008c → a464 → 3054` cycle 反复 "watching" 4 cycle 后才下首单；agent 每 cycle 从零判断 | 4 cycle |
| **reasoning vs actions 不匹配 P2-2** | cycle `008c` reasoning 与实际 actions 严重不匹配 | 单 cycle |

→ N=3 注入跨度刚好覆盖 thesis/alerts 重算窗口；字段引导确保 baseline 信号在持仓 cycle 重申。

---

## 2. 当前状态分析

### 2.1 当前 prompt 构造路径（baseline）

`cli/app.py:166-195` `run_agent_cycle()` 内：

```python
prompt = (
    f"You have been woken up by a {trigger_type} trigger.\n"
    f"Trading pair: {deps.symbol} | Timeframe: {deps.timeframe}\n"
    "Assess the situation and decide what to do."
)
# trigger_type-specific context appended (conditional/alert detail)
# memory_context appended:
memory_context = await deps.memory.format_for_prompt()
if memory_context != "No relevant memories.":
    prompt += f"\n\nYour memories:\n{memory_context}"
# agent.run(prompt, ...)
```

注入点位于 memory_context 之前（详见 §4.3）。

### 2.2 sim #6 decision 字段实证 baseline (2026-05-03)

R2-8a smoke session `cbcb1edf-8ecc-4628-afad-bcb1046f5d86` (BTC sim #6) 14 cycles 全 `execution_status='ok'` / 0 NULL decisions：

| 长度区间 | cycle 数 | 占比 | 形态 |
|---|---|---|---|
| 37-272 chars | 6 | 43% | "holding / no-action" 短句（自然短）|
| 272-893 chars | 7 | 50% | 多段 summary, markdown 表格 + "Why" 分析 |
| 1502 chars | 1 | 7% | over-elaborate (含大量客观数据) |

**关键发现**：

1. Agent 在 0 prompt 引导下已天然倾向 markdown summary 结构
2. 长 cycle (`fc9682ea` 782 chars / `0001dc2f` 893 chars) 已天然含 thesis / position state / active alerts list / "Why I'm holding" 段
3. 短 cycle (37 chars `b98e6a00` "Holding steady. See you in 5 minutes.") 是 agent 在 watching 状态的合理表达，**不应强制 inflate**
4. 1502 chars 那个 cycle 是 over-elaborate (含 VIX / funding / 表格)，应被压缩

→ R2-8b 引导段不需"重写 prompt 整体结构"，仅需"轻量补结构 + 显式列关键信号字段"。

### 2.3 R2-7 schema 已就位

`storage/models.py` `AgentCycle` 表（R2-7 重命名后）：

```python
class AgentCycle(Base):
    cycle_id: Mapped[str]                    # 8-char hex
    triggered_by: Mapped[str]                # scheduled/conditional/alert
    trigger_context: Mapped[str | None]      # JSON 触发瞬间客观快照
    state_snapshot: Mapped[str | None]       # JSON 决策时系统层面客观状态
    decision: Mapped[str | None]             # message content (R2-7: was String(30) enum, 改 Text+nullable)
    reasoning: Mapped[str | None]            # thinking content (R2-7: was result.output message)
    execution_status: Mapped[str]            # ok / usage_limit_exceeded / retry_exhausted
    model_id: Mapped[str | None]
    tokens_consumed: Mapped[int]
    created_at: Mapped[datetime]
```

R2-8b 消费 `decision` (Text)，无 schema 改动。

### 2.4 N7/R2-5 之后的 persona.py Layer 1 状态

`persona.py` Layer 1 当前 6 cross-tool bullets（PR #25 N7 整治 5 → R2-5 PR #34 加第 6 bullet "Wake interval control"）：

1. Fill timing
2. Open fill response
3. Close fill response
4. Alert response
5. OCO atomicity on OKX
6. Wake interval control

R2-8b 在 `## Cross-Tool Behavior` 之后**新增独立 section** `## Cycle Closing Summary`，不污染 Cross-Tool 6 bullets 的"工具操作"语义（详见 §3.4）。

### 2.5 RuntimeConfig docstring 已预言 R2-8b

`persona.py:16-18` RuntimeConfig 类 docstring：

```
Per-cycle dynamic context (e.g., previous-cycle reasoning, current
position) is NOT here — that channel is reserved for separate
mechanisms (R2-8 N10 reasoning injection).
```

设计哲学已分离：
- **system prompt (Layer 1) = 固定 cross-tool / cross-cycle behavior** → 进 cache prefix
- **per-cycle dynamic context = 独立 mechanism** → user message 注入

R2-8b 实施时顺带 update 此 docstring 措辞为 "R2-8b cross-cycle continuity (decision injection)"——议题升级前命名修正。

---

## 3. Design Overview

### 3.1 核心决议（trader 视角驱动）

R2-8b 的设计核心**不是技术问题**，是 **"trader 在 cycle 重启失忆后，希望未来的自己看到什么"** 的设计问题。把 cycle 重启类比为 trader shift change（夜班接日班），好的 shift handover note 共同特征：

- ✅ Position sheet（持仓 / 挂单 / alerts — fact）
- ✅ Trade thesis rationale
- ✅ Risk levels & invalidation triggers
- ✅ Watch list（observations needing attention）
- ❌ Market data dump（next shift can fetch fresh）
- ❌ 连续推理过程（noise）
- ❌ "You should buy at X" instructions（new shift trader has own judgment）

### 3.2 7 个最高 level 决议

| # | 决议 | 哲学 |
|---|---|---|
| **D1** | 实施路径 = (A) prompt prefix 注入（vs B 工具 / C message_history） | 强制曝光 + 不破坏 cache prefix + 改动量最小 |
| **D2** | 字段结构 = trader-native 5 字段（Stance / Active commitments / Thesis & invalidation / Cycle delta / Watch list optional）vs 4 字段叙事 vs 4 字段状态 | 全面覆盖 sim #4 5 类痛点 + Stance 字段是 trader 心理 orient anchor |
| **D3** | 注入字段 = `decision` (message)，不是 `reasoning` (thinking) | 闭环设计：agent 自己生产、自己消费；避开 thinking chain-of-thought 噪音；与 R2-7 schema reframe 天然对齐 |
| **D4** | 注入 N=3 | sim #4 三类痛点最长 3 cycle 窗口；token cost +5% 平均 / +7.5% 灾难，可接受 |
| **D5** | 长度 cap = 三层 (600 软建议 / 800 软上限 / 1200 硬截断)，全部暴露给 agent | sim #6 实测 13/14 cycles 自然 fit；与 R2-8c "Option D" 三层架构同模板 |
| **D6** | 引导段位置 = persona.py 新独立 section `## Cycle Closing Summary` | 进 system prompt cache prefix；语义与 `## Cross-Tool Behavior` 分离 |
| **D7** | 注入位置 = `intro/Assess → trigger context → recent → memory`（实际顺序）| memory 在 prompt 末段强 anchor decision（位置 bias）；recent 紧跟 trigger context 自然延续叙事；"Assess" 在早段是 baseline 设计（§8.10 OOS，本议题不动） |

### 3.3 双侧机制图

```
┌─────────────────────────────────────────────────────────────┐
│ 生产侧 (W2-1 prompt 引导)                                    │
│  persona.py 新 section `## Cycle Closing Summary`           │
│  → agent 在每 cycle decision 末段写 5 字段 trader-native    │
│    summary，落 agent_cycles.decision (R2-7 已存在)          │
└─────────────────────────────────────────────────────────────┘
                              ↓ DB persist via cli/app.py:352
┌─────────────────────────────────────────────────────────────┐
│ 消费侧 (cli/app.py 注入)                                     │
│  run_agent_cycle() 入口 query agent_cycles                  │
│  → render → 拼到 user message                                │
│    (intro/Assess → trigger context → recent → memory)       │
└─────────────────────────────────────────────────────────────┘
```

### 3.4 与 `## Cross-Tool Behavior` 语义分离

新 section `## Cycle Closing Summary` 独立于 `## Cross-Tool Behavior`，理由：

| Section | 内涵 |
|---|---|
| `## Cross-Tool Behavior` (6 bullets) | 工具操作 + 触发响应模板 + 工具间副作用（fill timing / open/close/alert response / OCO atomicity / wake interval）|
| **`## Cycle Closing Summary` (新)** | **cycle output format / 跨 cycle 契约**（与"如何用工具"是不同维度）|

强行塞进 Cross-Tool 第 7 bullet 会让 section 标题失去精确性。新独立 section 让未来 cycle-level 元规则（如 N12c thinking 截断引导）有自然归属。

### 3.5 Confirmation bias 防护策略

`agent_reflection_tools_candidate` memory 第 5 节核心 insight：
> 市场叙事（"我们在 mid-cycle"）→ **负 ROI（confirmation bias 燃料）**

R2-8b 在多层削弱 confirmation bias 风险：

| 层 | 措施 |
|---|---|
| 字段设计 | 删除原计划字段 5 "Next-trigger condition"（指令性）；保留 "Watch list (optional)"（observation-only）|
| 字段措辞 | 字段 4 引导 "actions deliberately not taken (with reasons)"——defensive non-action 显式记录防纠结 |
| 生产侧引导 (review round 2 F1 校准) | **pure output format 措辞，无 audience 揭示**：`The summary should be observational and descriptive — not prescriptive`; `Do not include instructions or recommendations for future actions`; `For price-conditional plans, prefer setting an alert or limit order rather than writing it as text intent` |
| 生产侧反向 drift guard (review F1) | T3.6 锁定 prompt 不出现 "future self" / "past self" 措辞，防未来 PR 又加回 audience 揭示 |
| 消费侧标头 | `Your prior cycle summaries (most recent N=3, from this session):` —— 用 "your" + "from this session" implicit 揭示 source；不在 prompt 显式说 "future self will see this" |
| 不加 disclaimer | 不在注入侧加 "treat as background, not commitments" meta-instruction（与 fact-only 哲学冲突；trader frame 已就位）|

### 3.6 完整 user prompt mockup（注入后）

```
You have been woken up by a alert trigger.
Trading pair: BTC/USDT:USDT | Timeframe: 5m
Assess the situation and decide what to do.

PRICE LEVEL: BTC/USDT:USDT reached 78420.00
(your alert: drop 78420 — 5m structure breakdown signal)

Your prior cycle summaries (most recent N=3, from this session):

[cycle a3f2c1d8 · scheduled · 2026-05-04 11:30 UTC (8 min ago)]
Stance: Holding long, thesis intact.
Active commitments: LONG 0.025 BTC @78517.70 5x; entry baseline R:R 2.21,
risk $4.19, TP 78888; SL 78350 (original, no trail). Alerts: #1@78800 (TP
warn), #2@78420 (early breakdown signal), vol@0.25%/2min.
Thesis & invalidation: Failed breakdown bull case; multi-TF aligned.
Invalidation: clean break <78350 with vol spike. Conviction: moderate.
Cycle delta: No actions; held through 5m pullback to 78440.

[cycle 7c918b42 · alert · 2026-05-04 11:25 UTC (13 min ago)]
... (similar)

[cycle b487f039 · conditional · 2026-05-04 11:23 UTC (15 min ago)]
... (similar)

Your memories:
- 2026-04-30: Set tighter SL after dip-buy entries (lesson)
- ...
```

---

## 4. 详细设计

### 4.1 生产侧：persona.py 新 section

#### 4.1.1 Section 完整内容

加在 `persona.py _build_layer1()` 末段，在 `## Cross-Tool Behavior` 之后：

```markdown
## Cycle Closing Summary

Your final response must be a concise cycle summary covering five elements
(do not produce an analysis followed by a summary — the summary IS the
final response):

(1) Stance — current state in one phrase. Examples: "Holding long,
    thesis intact" / "Watching for breakout" / "Pending limit order" /
    "Just closed long, cooling off".

(2) Active commitments — current positions, pending orders, and active alerts:
    - If holding position: position details + entry baseline (R:R / risk %
      / TP target) + current SL and any trail history (critical for trail
      decisions across cycles)
    - If pending orders: levels + cancellation criteria
    - If active alerts: levels + each one's signal intent
    - If none of the above: "No position. No pending orders. [Vol alert
      details if relevant]."

(3) Thesis & invalidation — why your current stance, and the specific
    conditions under which your thesis would become invalid. Include
    conviction level (low / moderate / high) when it affects risk or
    sizing decisions.

(4) This cycle delta — what changed this cycle: actions taken AND
    actions deliberately not taken (with reasons). Be specific about
    levels and timing.

(5) Watch list (optional) — non-action observations needing attention:
    pattern formation, divergence, macro events in the queue, regime
    shifts, lessons from this cycle. Skip if no relevant observations
    beyond fields 1-4.

Aim for ~600 chars (up to ~800 for critical events; the system
hard-truncates beyond ~1200). Critical events include: just opened or
closed position, alert triggered with action taken, SL trail with multiple
history points, thesis transition (conviction level change), or macro
event proximity.

The summary should be observational and descriptive — not prescriptive.
Do not include instructions or recommendations for future actions; for
price-conditional plans, prefer setting an alert or limit order rather
than writing it as text intent. Do not re-paste market data or full
thinking — those will be fresh-fetched.
```

**Review F1+F2 校准 (审查 round 2)**：prompt 重写为 pure output format ——
彻底删除 "future self" / "past self" 揭示叙事（防 perform-for-audience，
真正贯彻 brainstorm (W) 决议）+ explicit "summary IS the final response"
（防 agent 写 "analysis-then-summary" 导致注入下 cycle 时 1200 cap 截掉
关键 summary 部分）。anti-instruction guard 通过 "observational and
descriptive — not prescriptive" + "Do not include instructions or
recommendations for future actions" 措辞维持，无 audience-oriented 暗示。

#### 4.1.2 5 字段映射回 sim #4 痛点

| 字段 | 直接命中 |
|---|---|
| (1) Stance | 入场延迟 P1-3（cycle 心理 orient）|
| (2) Active commitments + SL trail 历史 | R:R 漂移 P1-4 + alerts 静默替换 P1-1 |
| (3) Thesis & invalidation + conviction | thesis 重算 + 未来决策的 risk-aware judgment |
| (4) Cycle delta（含 deliberate non-actions）| reasoning vs actions 不匹配 P2-2 + 入场延迟（防纠结） |
| (5) Watch list (optional) | 跨 cycle observation continuity / lesson 载体 |

#### 4.1.3 RuntimeConfig docstring update

`persona.py:16-18` 现写：

```
Per-cycle dynamic context (e.g., previous-cycle reasoning, current
position) is NOT here — that channel is reserved for separate
mechanisms (R2-8 N10 reasoning injection).
```

R2-8b 顺带 update 为：

```
Per-cycle dynamic context (e.g., prior cycle summaries, current
position) is NOT here — that channel is reserved for separate
mechanisms (R2-8b cross-cycle continuity / decision injection).
```

理由：议题升级前命名修正（reasoning → decision；R2-8 → R2-8b 精确 issue ID）。

### 4.2 消费侧：cli/app.py 注入机制

#### 4.2.1 query helper

```python
@dataclass(frozen=True)
class CycleSummary:
    id: int                # NEW (review F4): tie-breaker for same-timestamp ordering
    cycle_id: str
    triggered_by: str
    decision: str
    created_at: datetime


async def _fetch_recent_summaries(
    engine, session_id: str, n: int = 3
) -> list[CycleSummary]:
    """Fetch most recent N=3 ok cycles from current session.
    
    Returns [] on:
      - First cycle in session (no prior cycles)
      - DB error (logged at WARNING)
      - Forensic-only history (all cycles execution_status != 'ok')
    """
    try:
        async with get_session(engine) as session:
            result = await session.execute(
                select(
                    AgentCycle.id,                    # NEW (review F4): tie-breaker
                    AgentCycle.cycle_id,
                    AgentCycle.triggered_by,
                    AgentCycle.decision,
                    AgentCycle.created_at,
                )
                .where(
                    AgentCycle.session_id == session_id,
                    AgentCycle.execution_status == "ok",
                    AgentCycle.decision.is_not(None),  # NEW (review F2): 物理消除 NULL；理论 ok 路径 NOT NULL，但防御性 filter 避免 spec §4.3 边界矩阵自相矛盾
                )
                .order_by(
                    AgentCycle.created_at.desc(),
                    AgentCycle.id.desc(),              # NEW (review F4): tie-breaker for same-timestamp ordering stability
                )
                .limit(n)
            )
            rows = result.all()
        return [
            CycleSummary(
                id=r.id,
                cycle_id=r.cycle_id,
                triggered_by=r.triggered_by,
                decision=r.decision or "",
                created_at=r.created_at,
            )
            for r in rows
        ]
    except Exception as e:
        logger.warning(
            "Failed to fetch prior cycle summaries for injection: %s", e,
            exc_info=True,
        )
        return []
```

**Filter**: `session_id == current` AND `execution_status == 'ok'` AND `decision IS NOT NULL` (D-U2-a + D-U1-a + review F2 防御)
**ORDER BY**: `created_at DESC, id DESC` 取最新 N=3（review F4: id 作 tie-breaker for same-timestamp ordering stability）→ render 时按 `(created_at, id)` ASC 反转为时序顺序（最早→最新）以利阅读
**Error handling**: 任何异常 → log WARNING + return `[]`（D-U4-a）

#### 4.2.2 render helper

```python
def _render_recent_summaries(
    summaries: list[CycleSummary], now: datetime
) -> str:
    """Render summaries as user-message-ready prefix block.
    
    Returns "" if list is empty (silent skip — caller should not append header).
    Sorts by created_at ASC for chronological reading order.
    """
    if not summaries:
        return ""

    blocks = []
    # Sort ascending so reader sees oldest → newest naturally (review F4: include id tie-breaker)
    for s in sorted(summaries, key=lambda x: (x.created_at, x.id)):
        cycle_id_short = s.cycle_id[:8]
        utc_str = s.created_at.strftime("%Y-%m-%d %H:%M UTC")
        ago = _format_relative_time(now, s.created_at)
        body = _truncate_decision(s.decision)
        blocks.append(
            f"[cycle {cycle_id_short} · {s.triggered_by} · {utc_str} ({ago})]\n{body}"
        )

    header = "Your prior cycle summaries (most recent N=3, from this session):"
    return f"{header}\n\n" + "\n\n".join(blocks)


def _truncate_decision(
    text: str, hard_cap: int = 1200, soft_cap: int = 800
) -> str:
    """Hard-truncate at hard_cap; log INFO at soft_cap; log WARNING at hard_cap."""
    n = len(text)
    if n > hard_cap:
        logger.warning(
            "Cycle decision exceeded hard cap %d (got %d), truncating",
            hard_cap, n,
        )
        return text[:hard_cap] + " ... [truncated]"
    if n > soft_cap:
        logger.info(
            "Cycle decision exceeded soft cap %d (got %d), keeping full",
            soft_cap, n,
        )
    return text


def _format_relative_time(now: datetime, then: datetime) -> str:
    """Format delta as '8 min ago' / '2 hours ago' / '1 day ago'.
    
    SQLite returns naive datetime even when schema is DateTime(timezone=True);
    normalize to UTC-aware before subtraction (same pattern as
    session_manager.py:294-295). Verified by review F1 实测：
    AgentCycle.created_at 实际从 SQLite 读出 tzinfo=None，直接相减抛
    TypeError: can't subtract offset-naive and offset-aware datetimes.
    """
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = now - then
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs} sec ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins} min ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days > 1 else ''} ago"
```

**截断**：硬截字符 + `... [truncated]`（不按句号截，简洁优先）（D-Q-C）
**Drift logs**：INFO @ 800 / WARNING @ 1200（D-Q-D）
**时间格式**：`2026-05-04 11:30 UTC (8 min ago)` (absolute + relative)（D-D-B）

#### 4.2.3 Fail-isolated outer wrap (review F3 校准)

`_fetch_recent_summaries` 内的 try/except 仅覆盖 DB query 层，render/format 阶段
（`_render_recent_summaries` / `_format_relative_time` / `_truncate_decision`）
异常会在 `agent.run()` 之前中断整个 cycle，违反 R2-8b "任何错误都不阻塞 cycle"
fail-isolated 承诺。

引入 outer wrap helper 作真正的 fail-isolated boundary：

```python
async def _build_recent_summaries_block(
    engine, session_id: str, n: int = 3,
) -> str:
    """Fetch + render summaries with fail-isolated boundary.
    
    Returns "" on:
      - empty fetch (first cycle / forensic-only history / NULL decision filter)
      - any exception during fetch OR render OR format (logged at WARNING)
    
    Review F3: this outer wrap covers the entire injection pipeline,
    not just DB query layer. _fetch_recent_summaries 内部 try/except
    保留作 layered defense.
    """
    try:
        summaries = await _fetch_recent_summaries(engine, session_id, n)
        if not summaries:
            return ""
        return _render_recent_summaries(
            summaries, now=datetime.now(timezone.utc),
        )
    except Exception as e:
        logger.warning(
            "Failed to build recent summaries block for injection: %s", e,
            exc_info=True,
        )
        return ""
```

#### 4.2.4 注入点（cli/app.py:166-195 区域改动）

修改顺序：

```python
prompt = (
    f"You have been woken up by a {trigger_type} trigger.\n"
    f"Trading pair: {deps.symbol} | Timeframe: {deps.timeframe}\n"
    "Assess the situation and decide what to do."
)
# trigger_type-specific context (conditional/alert detail) — UNCHANGED
if trigger_type == "conditional" and context is not None:
    ...  # existing logic unchanged
elif trigger_type == "alert" and context is not None:
    ...  # existing logic unchanged

# NEW: recent summaries injection (D-D-E + review F3 fail-isolated outer wrap)
recent_block = await _build_recent_summaries_block(
    engine, deps.session_id, n=3,
)
if recent_block:
    prompt += f"\n\n{recent_block}"

# UNCHANGED: memory_context
memory_context = await deps.memory.format_for_prompt()
if memory_context != "No relevant memories.":
    prompt += f"\n\nYour memories:\n{memory_context}"

# UNCHANGED: agent.run(...)
```

注入位置序列：

```
trigger intro → "Assess" → (trigger context) → recent summaries → memory → agent.run
```

`memory` 在末段强 anchor decision（位置 bias 论据），`recent` 紧跟 trigger context 自然延续叙事。

### 4.3 边界条件矩阵

| 边界 | 行为 | 决议 |
|---|---|---|
| Session 第一个 cycle（query 返回 0 行）| 完全不注入（连标头也省）| D-U3-a |
| Session 早期 cycle（query 返回 1-2 行）| 注入实际数量 + 标头 | D-U3-a |
| Session 第 4+ cycle | 始终 cap 在 N=3 | D-U2-a query LIMIT |
| Forensic cycle 后（mixed ok + forensic）| 跳过 forensic, 取相邻 ok | D-U2-a `WHERE execution_status='ok'` |
| **新建 session（user creates new）** | 新 session_id → 第一 cycle silent skip | D-U1-a session-bound + D-U3-a |
| **Restore session（crash recovery / user resumes 同 session_id, `session_manager.py:397-402`）** | **同 session_id → 注入恢复前历史 cycles（接续语义）；可能含跨日时间断层（绝对时间 + relative ago 同时显示已让 agent 能识别 "1 day ago"）** | D-U1-a session-bound 自然行为（review F5 校准）|
| DB 故障 | log WARNING + return `[]` + cycle 正常进行 | D-U4-a |
| Decision > 1200 chars | 硬截字符 + `... [truncated]` + WARNING log | D-Q-C / D-Q-D |
| Decision in 800-1200 区间 | 保留全文 + INFO log（drift guard）| D-Q-D |
| Decision < 600 chars（短 holding cycle）| 注入全文，不补占位 | 与短 cycle 自然性一致 |
| Decision 极短 ("Holding steady. See you in 5 minutes." 37 chars）| 注入全文 | 短输出是 agent 自然产出，不 filter |
| `decision` 字段 NULL（理论上 execution_status='ok' 不会 NULL）| query filter `decision IS NOT NULL` 物理消除 + `r.decision or ""` 防御性兜底 | review F2 校准 |
| `created_at` naive datetime（SQLite 实际行为）| `_format_relative_time` 内部 normalize `tzinfo=timezone.utc` | review F1 校准 |

---

## 5. 实施策略

### 5.1 数据流

```
agent_cycles INSERT (existing path, cli/app.py:352)
           │
           ▼
   ┌────────────────┐
   │ agent_cycles   │
   │ .decision Text │
   └────────────────┘
           │
           │ on next cycle entry
           ▼
   _fetch_recent_summaries(engine, session_id, n=3)
           │ filter: session_id + execution_status='ok'
           │ order: created_at DESC, id DESC LIMIT 3 (review F4 tie-breaker)
           ▼
   list[CycleSummary]  ─── empty → silent skip
           │
           ▼
   _render_recent_summaries(summaries, now)
           │ sort ASC, format header + blocks, truncate decisions
           ▼
   recent_block (str)
           │
           ▼
   prompt += recent_block (after trigger context, before memory)
           │
           ▼
   agent.run(prompt, ...)
```

### 5.2 改动量估算

| 文件 | 改动 | 行数 |
|---|---|---|
| `src/agent/persona.py` | 新增 `## Cycle Closing Summary` section + RuntimeConfig docstring update | +30 / -2 |
| `src/cli/app.py` | 新增 `_fetch_recent_summaries` + `_render_recent_summaries` + `_truncate_decision` + `_format_relative_time` + **`_build_recent_summaries_block` outer wrap (review F3)** + `CycleSummary` dataclass (含 `id` field, review F4) + 注入点 wiring | +95 / -0 |
| `tests/test_persona.py` | drift guards 6 tests (T3.1-T3.6, 含 review round 2 F1 加 T3.6 no future-self mention) | +50 |
| `tests/test_cycle_summary_injection.py` (新) | unit tests L1+L2 (含 T1.8 NULL filter / T1.6 升级 id tie-breaker / T2.7 naive datetime) | +220 |
| `tests/test_agent_cycle_injection.py` (新) | integration tests L4 (T4.5 升级 any_injection_error) | +120 |
| **合计** | **~520 行净（含测试 ~390）** | source : test ≈ 1 : 3 |

### 5.3 实施顺序（spec → plan 阶段细化）

预期 plan 拆为以下 task（subagent-driven mode，与 R2-8a/R2-8c 同模式）：

| Task | 主题 | 估算改动 |
|---|---|---|
| T1 | `_format_relative_time` + `_truncate_decision` helpers (独立 unit-testable) | ~30 source + ~80 test |
| T2 | `CycleSummary` dataclass + `_fetch_recent_summaries` query | ~40 source + ~100 test |
| T3 | `_render_recent_summaries` render helper | ~20 source + ~80 test |
| T4 | `cli/app.py:194` 注入点 wiring + integration test | ~10 source + ~120 test |
| T5 | `persona.py` 新 section + RuntimeConfig docstring + drift guards | ~30 source + ~30 test |
| T6 | Final smoke + AC verification | manual |

每 task dispatch implementer + spec reviewer + code reviewer 各一遍（与 R2-8a 同模式）。

---

## 6. 边界 / 错误处理

### 6.1 错误处理矩阵

| 错误源 | 行为 | 影响 cycle 完成？ |
|---|---|---|
| DB 连接故障 | log WARNING + skip | ❌ 不影响（cycle 仍跑）|
| query 超时（不预期）| 同上 | ❌ |
| `cycle_id` / `decision` 字段 None | `r.decision or ""` 防御处理 | ❌ |
| `created_at` None | 不可能（NOT NULL constraint）；如果发生抛异常 → log WARNING + skip | ❌ |
| Decision 极长（10k+ chars 极端情况）| 硬截 1200 + WARNING log | ❌ |
| 同 cycle 时序错位（含同 timestamp 多 cycle）| ORDER BY `created_at DESC, id DESC` 保证稳定性（review F4: id 作 tie-breaker，避免测试场景或快速连续 insert 时 same-timestamp 排序不稳定）| ❌ |
| Concurrent session insert race | 不预期（cycle 串行）；如果发生取最近 N | ❌ |

**核心原则**：R2-8b 是辅助 context，**任何错误都不阻塞 cycle**（与 ToolCallRecorder fail-isolated 哲学一致）。

### 6.2 不动项

| 项 | 不动理由 |
|---|---|
| `agent_cycles` schema | R2-7 已就位 |
| R2-8a cycle log render | 显示层与注入层独立 |
| pydantic-ai message_history 构造 | 不走 (C) 候选 |
| `cli/app.py` 主 retry/forensic 路径 | 注入失败仅 silent skip |
| `## Cross-Tool Behavior` 现有 6 bullets | 新 section 独立 |
| memory_context 注入逻辑 | 不动 |
| baseline LLM cache prefix | 引导段进 system prompt 反而增强 cache hit |
| 现有 R2-8a `_extract_thinking_text` helper | 不动；R2-8b 不消费 thinking |
| R2-7 `_capture_trigger_context` / `_capture_state_snapshot` helpers | 不动 |

---

## 7. 测试矩阵

### 7.1 4 层测试结构

| 层 | 数量 | 文件 |
|---|---|---|
| L1 Unit — query helper | 6-7 | `tests/test_cycle_summary_injection.py` (新) |
| L2 Unit — render helper | 4-5 | 同上 |
| L3 Drift guards — persona | 3-4 | `tests/test_persona.py` (扩) |
| L4 Integration — run_agent_cycle | 4-5 | `tests/test_agent_cycle_injection.py` (新或合并) |

**净增**：**26 测试** enumerated (T1.1-T1.8 + T2.1-T2.7 + T3.1-T3.6 + T4.1-T4.5 = 8+7+6+5)。含 review round 1 加 T1.8 / T2.7 / T3.5 + round 2 加 T3.6（F1 drift guard）；T1.6 (F4) / T3.5 (F3) / T4.5 (F3) 升级。

### 7.2 L1 Unit — `_fetch_recent_summaries`

| # | 名称 | 验证 |
|---|---|---|
| T1.1 | `returns_n_most_recent_ok_cycles` | Happy path: N=3 ok cycles |
| T1.2 | `returns_empty_for_first_cycle_in_session` | session 第一 cycle → `[]` |
| T1.3 | `returns_partial_when_session_has_fewer_than_n` | session 仅 2 cycles → 返回 2 |
| T1.4 | `excludes_forensic_cycles` | mixed ok + usage_limit_exceeded → 跳 forensic, 取相邻 ok |
| T1.5 | `respects_session_boundary` | 多 session DB → 仅返回 current session |
| T1.6 | `orders_descending_by_created_at_then_id` | 时序正确；同 timestamp → 按 id DESC tie-breaker (review F4) |
| T1.7 | `returns_empty_on_db_error` | DB exception → log warning + `[]` |
| **T1.8** (review F2) | `excludes_cycles_with_null_decision` | mixed ok + decision NULL → query filter 物理消除，不进 list |

### 7.3 L2 Unit — render helper

| # | 名称 | 验证 |
|---|---|---|
| T2.1 | `renders_cycle_id_truncated_to_8_chars` | cycle_id [:8] 格式 |
| T2.2 | `renders_absolute_and_relative_time` | `2026-05-04 11:30 UTC (8 min ago)` 格式 |
| T2.3 | `truncates_decision_above_1200_chars` | > 1200 → 硬截 + `... [truncated]` |
| T2.4 | `does_not_truncate_below_1200` | <= 1200 → 保留全文 |
| T2.5 | `logs_warning_when_truncating` | > 1200 触发 WARNING log |
| T2.6 | `renders_in_chronological_order` | 输入 DESC, 渲染 ASC（最早 → 最新）|
| **T2.7** (review F1) | `handles_naive_created_at_from_sqlite` | 输入 naive datetime → 不抛 TypeError + 正确返回 relative string |

### 7.4 L3 Drift guards — persona

| # | 名称 | 验证 |
|---|---|---|
| T3.1 | `system_prompt_contains_cycle_closing_summary_section` | section header `## Cycle Closing Summary` 存在 |
| T3.2 | `system_prompt_contains_5_field_anchors` | 5 字段 anchor phrases 都在 |
| T3.3 | `system_prompt_contains_cap_numbers` | "600", "800", "1200" 都在 |
| T3.4 | `system_prompt_contains_critical_events_list` | "Critical events include:" 存在 |
| **T3.5** (review F3 / round 2 update) | `system_prompt_contains_anti_instruction_guard` | 锁 "observational and descriptive — not prescriptive" + "Do not include instructions or recommendations for future actions" + "prefer setting an alert or limit order" 三关键短语存在 |
| **T3.6** (review round 2 F1) | `system_prompt_does_not_mention_future_self_or_past_self` | drift guard 锁定 prompt 不出现 "future self" / "past self" 措辞，防未来 PR 又加回 audience 揭示 |

### 7.5 L4 Integration — run_agent_cycle

| # | 名称 | 验证 |
|---|---|---|
| T4.1 | `first_cycle_does_not_inject_prior_summaries` | session 第一 cycle prompt 不含标头 |
| T4.2 | `subsequent_cycle_injects_prior_summaries_with_header` | 第 2+ cycle prompt 含标头 + summaries |
| T4.3 | `injection_appears_before_memory_context` | 注入位置 recent → memory |
| T4.4 | `injection_caps_at_n_3_after_4_cycles` | cycle 5+ 仍只注入最近 3 |
| T4.5 | `any_injection_error_does_not_abort_cycle` | DB query / render / format 任何环节异常 → cycle 完成 + 不注入 (review F3 fail-isolated outer wrap 验证) |

---

## 8. Out-of-scope

### 8.1 已删 next-trigger 字段（confirmation bias 高风险）

原 brainstorm 候选字段 5 "Next-trigger condition" — 字面是"下次我应该在什么情况下做什么"，是 confirmation bias 燃料。已删，由 alert/order 机制层 commitment 替代（详见 §3.5）。

### 8.2 reflection wishlist 工具（articulate_thesis / surface_my_recent_pattern 等）

`agent_reflection_tools_candidate` memory 列举的 6 个工具 wishlist——R2-8b 是 journal 层 MVP，不实施这些工具。W2 数据驱动后视触发条件启动。

### 8.3 跨 session 边界（D-U1-a session-bound 自然行为，review F5 校准）

D-U1-a 决议 session-bound 严格。两类边界行为分清楚（详见 §4.3 边界矩阵）：

- **新建 session（user creates new）**：新 session_id → 第一 cycle silent skip（acceptable cost，与 baseline 行为一致）
- **Restore session（crash recovery / user resumes 同 session_id, `session_manager.py:397-402`）**：同 session_id → query 自然返回恢复前历史 cycles，按接续语义注入（**不是** silent skip）；跨日时间断层由 absolute UTC + relative "1 day ago" 同时显示让 agent 自我识别

未来若数据显示 restore 后注入历史 stale 导致 confirmation bias 加重，可加 run-boundary 字段（如 process start timestamp）作隐式 restart 标记 — 不在本 PR scope。

### 8.4 confirmation bias 行为效果验证

观察期 W2 数据驱动，不是 unit testable。AC30 用户 manual smoke 仅验证机制正确，不验证 agent 行为是否更"独立判断"。

### 8.5 token 经济实测影响

smoke 测试副产物，非新 unit test。预期 +5% 平均 / +7.5% 灾难输入 token cost。

### 8.6 sim #4 三类痛点 A/B 对比

观察期 W2 实测做，不在 PR 内。

### 8.7 metric counter (N10 query 失败计数)

`(U4-c)` 候选已否决——N10 query 失败应是罕见，metric overkill。观察期数据驱动后再决定是否升级。

### 8.8 thinking 字段注入 / 显示

R2-8b 仅消费 `decision` 字段。`reasoning` (thinking content) 不进注入。R2-8c 已决议 N12c (thinking 800→2000) 留观察期数据驱动决定。

### 8.9 注入侧 disclaimer ("treat as background, not commitments")

不加。理由：(1) fact-only 哲学（disclaimer 是 meta-instruction）；(2) W2-1 生产侧已 explicit guard；(3) 注入标头 `your` + `from this session` 已 implicit 揭示；(4) trader frame 已就位；(5) 避免 perform-for-audience（详见 §3.5）。

未来 fallback path：若 W2 观察期数据显示 agent 表现 confirmation bias 行为，可加回 disclaimer 作数据驱动迭代。

### 8.10 prompt "Assess the situation" 位置 baseline 议题

当前 prompt 把 instruction "Assess" 放在早段（line 3），所有 context 都在它之后。LLM autoregressive 位置 bias 角度看 instruction 应在末段更合理，但**不在 R2-8b scope**——是 baseline 重构议题，应作未来候选 PR。

### 8.11 字段 sub-bullets 强制结构（如 markdown 子列表）

不强制。引导段仅描述 5 字段应包含的关键信号点，让 agent 自适应措辞。强制 sub-bullets 会让 prompt 更复杂 + agent 写得更长。

---

## 9. Acceptance Criteria

### 9.1 行为正确性

| AC# | 描述 | 验证方式 |
|---|---|---|
| AC1 | `## Cycle Closing Summary` section 在 system prompt 中存在 | T3.1 drift guard |
| AC2 | section 含 5 字段 anchor phrases (Stance / Active commitments / Thesis & invalidation / Cycle delta / Watch list) | T3.2 |
| AC3 | section 含 cap 数字 600 / 800 / 1200 + critical events list | T3.3 / T3.4 |
| AC4 | session 第一个 cycle prompt 不含 prior summaries 标头 | T4.1 |
| AC5 | session 第 2+ cycle prompt 注入 N=min(3, available) | T4.2 / T4.4 |
| AC6 | forensic cycle 不进入注入候选 | T1.4 |
| AC7 | 跨 session 不污染 | T1.5 |
| AC8 | DB 查询失败不阻塞 cycle | T4.5 |
| AC9 | 注入位置在 trigger context 之后、memory_context 之前 | T4.3 |
| AC10 | decision > 1200 chars 硬截 + 标记 + WARNING log | T2.3 / T2.5 |
| AC11 | decision in 800-1200 chars 保留 + INFO log | T2.4 + log assert |

### 9.2 端到端 smoke

| AC# | 描述 | 验证方式 |
|---|---|---|
| **AC30** | sim ≥ 4 cycles, capture cycle 4 prompt 实测含正确格式 prior summaries 段（标头 + 3 cycles + 时间 + cycle_id [:8]） | 用户 manual sim 验证 (与 R2-8a 同模式) |

### 9.3 测试 baseline

| 项 | 目标 |
|---|---|
| Net new tests | 26 (enumerated) |
| Test pass rate | 100% |
| Existing tests regression | 0 |

---

## 10. 关联议题 / candidate memory

### 10.1 前置（R2-8b 启动依赖，已 landed）

- **R2-7** (PR #35, 2026-05-02): `agent_cycles` schema + 5 维度叙事 — R2-8b 消费 `decision` (Text) 字段
- **R2-8a** (PR #36, 2026-05-03): cycle log narrative — R2-8b 不动 cycle log 显示层
- **R2-8c** (PR #37, 2026-05-03): tool output sectioning — R2-8b 不动 tool 显示层

### 10.2 后续（R2-8b landed 后启动）

- **R2-9** (W2 startup smoke): R2-8b 通过后启 R2-9，对照 sim #4 baseline 验证 (a) cache hit 漂移 (b) token 经济回归 (c) 行为质性变化
- **W2 真实观察期** (R2-9 通过后启): 24-48h 实测 N=3 注入对 thesis 重算 / alerts 替换 / R:R 漂移 / 入场延迟 4 痛点的实测改善
- **N12c**: thinking 截断升级 (1500-2000 / 动态 / 不动) — R2-9 顺带 capture 真实分布数据触发

### 10.3 R2-8b 顺带触及

- **persona.py RuntimeConfig docstring update**: `R2-8 N10 reasoning injection` → `R2-8b cross-cycle continuity (decision injection)`，议题升级前命名修正

### 10.4 上位议题 `agent_reflection_tools_candidate`

R2-8b 是 3-tier 中 **journal 层最轻量 MVP**：
- ✅ 不建工具、不建表、不改 schema
- ✅ 强制曝光（绕 P2-1 "agent 不主动用反思工具" baseline）
- ✅ A/B 可验证（W2 注入前后对比 cache hit / token / 行为）

W2 数据将驱动后续 reflection wishlist 决策——不在本 PR scope。

### 10.5 关联 sim #4 痛点 inventory

`.working/sim4-issues-inventory.md` §P1-1 / §P1-3 / §P1-4 / §P2-2 / §P1-7-6（reasoning 视觉锚点）

---

## 11. 风险与 Mitigation

| 风险 | 严重度 | Mitigation |
|---|---|---|
| Confirmation bias（agent 看到 prior context 被 anchor）| 🟡 中 | 字段 4 explicit "deliberately not taken" + 删 next-trigger + Watch list optional 中性化措辞 + 生产侧引导 "observational and descriptive — not prescriptive" + "Do not include instructions or recommendations for future actions" (review round 2 F1 校准) + 标头 implicit "your" 揭示 |
| Agent 不听话写 5 字段（自然倾向偏离）| 🟢 低 | sim #6 实测 14 cycles 已天然倾向 markdown summary 结构；引导文本仅"补结构"非"重写" |
| Cycle decision 长度方差（37 → 1502 chars）| 🟡 中 | 三层 cap (600/800/1200) + 截断 fallback + drift guard 日志监控 |
| Token cost 增加（N=3 注入 ~600 tokens / cycle）| 🟢 低 | +5% 平均 / +7.5% 最大；远小于 fresh thesis 重算成本 (sim #4 5 min 烧 206k tokens) |
| Agent perform-for-audience（知道未来会看到导致 over-elaborate）| 🟢 **低** (review round 2 F1 后降级) | 生产侧 prompt **彻底删除 "future self" / "past self" 揭示叙事**，改 pure output format（"Your final response must be a concise cycle summary..."）+ T3.6 drift guard 锁定不出现 audience 措辞；消费侧标头 implicit "your" + "from this session" 仅作 source 提示；不加 disclaimer |
| 新建 session 第一 cycle 无 prior context | 🟢 低 | acceptable cost；与 baseline 行为一致；W2 数据可触发未来升级 |
| 极端 over-elaborate cycle (sim #6 d092e9fe 1502 chars) | 🟢 低 | 1200 硬截 + WARNING log 监控 |
| DB query 阻塞 cycle | 🟢 低 | try/except + silent skip + log WARNING (D-U4-a) |
| Restore session 注入恢复前 stale 历史（跨日断层 / 市场 regime 已变） | 🟢 低 | absolute UTC + "1 day ago" 同时显示让 agent 识别时间断层；未来若 W2 数据显示 confirmation bias 加重 → 加 run-boundary 字段作隐式 restart 标记 (review F5 follow-up) |

---

## 附录 A: Brainstorm 决议追溯

按时序记录 brainstorm 关键决议链（详细对话记录在 brainstorm session 内）：

### A.1 实施路径决议 (D1)

候选：(A) prompt prefix / (B) `recall_recent_decisions` 工具 / (C) message_history 注入

选 (A) 理由：
- (B) sim #4 P2-1 实证 `save_memory` 整 session **0 调用** → agent 不主动用反思工具是 baseline 风险，本 MVP 大概率重蹈覆辙
- (C) 改 pydantic-ai 构造逻辑 + 破坏 cache prefix（cycle 边界 history=99 tokens）
- (A) 强制曝光 + 不破坏 cache prefix + 改动量最小 + 与现有 memory_context 注入同位置

### A.2 注入字段决议 (D3)

R2-7 schema reframe 后两个候选：
- (i) reasoning (thinking content) — 原 N10 memory 写法
- (ii) decision (message content) — 用户 brainstorm 期间提出新方案

选 (ii) 理由：
- 闭环设计：agent 自己生产、自己消费 cycle 内容
- 避开 thinking chain-of-thought 噪音（"wait, let me reconsider..." 在 thinking 里很常见）
- 与 R2-7 schema reframe 天然对齐：`decision` 字段从 String(30) enum 改 Text + nullable，本意承接 agent 主观输出
- 隐式 `articulate_thesis`（wishlist 工具）：通过 prompt template 引导拿到 80% 价值，零新工具成本

### A.3 字段结构决议 (D2)

3 个候选演化：

**Round 1**: 5 字段状态结构（Thesis / Invalidation / Baseline / Alert intent / Next-trigger）
- 删除字段 5 Next-trigger（confirmation bias 高风险，是"对未来的指令"）
- → 4 字段状态结构

**Round 2**: 用户提出叙事 4 字段（看到 / 做了 / 原因 / 未来打算）
- 优点：自然 trader 日志结构 + 直接打 P2-2 (reasoning vs actions)
- 缺点：失去 invalidation/baseline 显式 → R:R 漂移痛点不被覆盖
- 字段 4 nuance framing：past tense + 显式 disclaimer

**Round 3**: 融合 4 字段（用户叙事 + 关键信号 sub-points）

**Round 4 (最终)**: trader 视角深度分析 → trader-native 5 字段
- 加 Stance 字段（cycle 心理 orient anchor）
- Active commitments 统一处理 positions/orders/alerts + 每个带 origin reason
- 强调 SL trail 历史（R:R 漂移直接根因）
- 引入 conviction level + deliberate non-actions
- Watch list 标 optional（场景适配）

### A.4 长度 cap 决议 (D5)

候选：(S1) 不限制 / (S2) Prompt 软引导 / (S3) 注入时安全网截断 / (S4) 写入时硬 cap

选 (S2) + (S3) 组合 + 三层数值 (600/800/1200)：
- Prompt 软引导让 agent 自适应（"~600 chars"）
- 注入时硬截作 drift guard
- 写入 DB 不限制（保留完整数据）

三层数值与 sim #6 实测 13/14 cycles 自然 fit。

### A.5 暴露 1200 给 agent 决议 (D-Q-A)

候选：(a) 不暴露（system 静默约束）/ (b) 暴露（fact-only / agent transparency）

选 (b) 理由（用户决议）：
- fact-only 哲学一致性（与 N5/Iter 4 路线）
- 避免 silent surprise（agent 写 1300 chars 突然被截会不理解）
- 与 R2-7 trigger_context fact-only 同哲学

### A.6 截断算法决议 (D-Q-C)

候选：(算 1) 硬截字符 + 标记 / (算 2) 按句号截 / (算 3) 按字段标记截

选 (算 1) 硬截：
- 既然 agent 已知 1200 cap，会主动控制不超 → 硬截只是 safety net
- 简单实施 + 与"暴露 1200"决策一致

### A.7 引导段位置决议 (D6)

候选：(L1) `cli/app.py` user message 拼 / (L2) `persona.py` Layer 1 第 7 bullet / (L3) `persona.py` 独立 section

选 (L3) 独立 section `## Cycle Closing Summary` 理由：
- 语义精确：cross-tool 6 bullets 都是工具操作约束；cycle-end summary 是 output format 约束
- 与 N7 5-bullet 简洁结构哲学不冲突（独立 section 各司其职）
- 未来扩展点（thinking 截断 N12c / reflection wishlist 引导都可进此 section）

标题候选：(T1) `## Cross-Cycle Continuity` / (T2) `## Cycle Closing Summary` / (T3) `## End-of-Cycle Snapshot`

选 (T2) `## Cycle Closing Summary`（用户决议）。

### A.8 注入格式决议 (Q5 + D-A/B/C/D)

格式：(F1) 极简 cycle_id 列
- 标头：`Your prior cycle summaries (most recent N=3, from this session):`
- 时间：`2026-05-04 11:30 UTC (8 min ago)` (absolute + relative)
- trigger_type 显示
- cycle_id `[:8]`

### A.9 注入位置决议 (D-E)

候选：(a) recent → memory（用户决议）/ (b) memory → recent

选 (a) 理由：
- prompt 末段是真正的 decision anchor（位置 bias）
- memory (lessons) 在末段紧贴 decision = lessons 强 anchor judgment
- 决策心理学：先建立情境 (recent) → 然后 lessons 指导决策

**注**：实际 prompt 顺序是 `intro/Assess → trigger context → recent → memory`（见 §3.2 D7 / §3.3 图示 / §4.2.3 注入点）。"Assess" 在 prompt 早段（line 3）是 baseline 设计；末段位置 bias 论据基于 memory_context 紧贴 prompt 末尾。"Assess" 移末段是独立议题（§8.10 OOS）。

### A.10 disclaimer 决议（不加）

候选：(W) 仅消费侧 implicit / (Y) 仅生产侧 fact / (X) 双侧揭示 / (Z) 双侧不揭示

选 (W) 理由：
- 标头 "your" + "from this session" 已 implicit 揭示
- 生产侧揭示会触发 perform-for-audience（"未来自己会看到"）
- 不加 disclaimer 是 fact-only 哲学（disclaimer 是 meta-instruction）
- W2-1 生产侧已 explicit guard（round 1 措辞 "do not write instructions to future self" → review round 2 F1 后改为 pure output format "Do not include instructions or recommendations for future actions"，删 audience 揭示）—— 注入侧 disclaimer redundant

### A.11 边界条件决议

- D-U1-a Session-bound 严格（query `WHERE session_id=current AND execution_status='ok'`）
- D-U2-a Filter ok 仅（forensic NULL 自动跳）
- D-U3-a Silent skip on 0 行（与 memory_context 模式一致）
- D-U4-a Log WARNING + silent skip on DB error（与 ToolCallRecorder fail-isolated 同哲学）
- D-U5 测试矩阵 4 层 26 tests enumerated (含 review round 1 加 T2.7 / T1.8 / T3.5 + round 2 加 T3.6 + T1.6/T3.5/T4.5 升级) + AC30 manual smoke

---

## 附录 B: sim #6 实测数据 baseline (R2-9 follow-up reference)

Smoke 元数据：session `cbcb1edf-8ecc-4628-afad-bcb1046f5d86` (BTC sim #6) / 14 cycles / 长度 37-1502 chars / DB `data/tradebot.db` 仍可重读。

完整长度分布：

| cycle_id | trigger | length |
|---|---|---|
| b98e6a00 | scheduled | 37 |
| 487772a3 | scheduled | 146 |
| 37b4149d | scheduled | 160 |
| 04a80528 | scheduled | 193 |
| fc298d36 | scheduled | 229 |
| 72052719 | scheduled | 272 |
| f7b22f5c | alert | 288 |
| ec6a8d94 | scheduled | 345 |
| 17fca887 | scheduled | 345 |
| 1486c350 | scheduled | 602 |
| d68d6554 | alert | 770 |
| fc9682ea | conditional | 782 |
| 0001dc2f | alert | 893 |
| d092e9fe | scheduled | 1502 |

R2-9 重跑后 capture 同维度数据作 R2-8b 注入前后对比 baseline。
