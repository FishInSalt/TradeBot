from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import signal
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update as sql_update

from src.agent.memory import MemoryService
from src.agent.trader import TradingDeps, create_trader_agent
from src.agent.persona import (
    CYCLE_DECISION_CHAR_HARD_FLOOR,
    CYCLE_DECISION_WORD_CAP,
    RuntimeConfig,
    generate_system_prompt,
)
from src.cli.approval import ApprovalGate
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, ThinkingPart,
    ToolCallPart, ToolReturnPart,
)
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from src.cli.display import (
    display_metrics, format_cycle_output, build_react_steps,
    is_tool_error, resolve_tool_display,
)
from src.cli.logging_config import (
    SessionConsole,
    setup_session_logging,
    setup_system_logging,
    write_session_header,
)
from src.cli.session_state import SessionStats
from src.config import ExchangeConfig, PersonaConfig, Settings, load_settings, load_trader_config
from src.integrations.exchange.okx import OKXExchange
from src.integrations.market_data import MarketDataService
from src.scheduler.scheduler import Scheduler
from src.services.cycle_capture import _capture_state_snapshot, _capture_trigger_contexts
from src.services.event_render import (
    _format_event_breakdown,
    _format_relative_time,
    _render_event_block,
    _wake_time_suffix,
)
from src.services.technical import TechnicalAnalysisService
from src.storage.database import get_session, init_db
from src.storage.models import AgentCycle, Session, TradeAction
from src.integrations.exchange.base import FillEvent
from src.cli.wizard import WizardResult

logger = logging.getLogger(__name__)

# Iter 5 §3.1: 单 cycle 防爆裂兜底；非业务 throttle。
# request/tool_calls limit 留 5x buffer（typical cycle ~10 tool calls）。
# total_tokens W2 prep Iter 5 校准（W1 实测 avg 70k / max 141k → 200k 留 1.4x）。
USAGE_LIMITS_PER_CYCLE = UsageLimits(
    request_limit=50,            # = pydantic-ai default，显式传防 1.79+ 默认变化
    tool_calls_limit=50,
    total_tokens_limit=200_000,  # 单 cycle 上限；外层 daily TokenBudget 是日累积
)


def _extract_thinking_text(messages) -> str | None:
    """R2-7 §6.3: 遍历 result.new_messages() 找所有 ModelResponse 内的 ThinkingPart 拼接 content.

    PR #35 I2: 用 isinstance(msg, ModelResponse) 显式收紧 — ThinkingPart 仅出现在 ModelResponse,
    与下方 tool_calls extraction (line ~234-258) 同款 narrowing 模式. getattr 容错过宽会
    silently 丢未来 pydantic-ai 新消息类型的 thinking content.
    """
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ThinkingPart):
                    parts.append(part.content)
    return "\n\n".join(parts) if parts else None


def _safe_build_react_steps(messages) -> str | None:
    """收尾构建 ReAct 骨架并序列化为 JSON（spec §5.3）。

    fail-isolated：构建 + 序列化任一步异常 → None + logger.warning，绝不阻断关键的
    AgentCycle 写入（与现有 render 失败降级同策略）。空骨架 → None（不存 "[]"）。
    """
    try:
        steps = build_react_steps(messages)
        return json.dumps(steps, ensure_ascii=False) if steps else None
    except Exception:
        logger.warning("build_react_steps failed; react_steps=None", exc_info=True)
        return None


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
    pathological cases (long URL / JSON / `|---|---|` separator)
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


def _render_empty_decision_body(execution_status: str) -> str:
    """Render system-generated body for cycles that left no decision summary.

    Three known statuses (internal branching, but agent-facing text exposes
    NO schema field names — agent reads natural language only):
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

    Unknown statuses fall through to a fixed fallback string for forward
    compatibility with future execution_status enum extensions; the status
    value is NOT interpolated into the agent-facing text (would expose schema
    artifact + open prompt-injection surface).

    Note: this function returns a system-generated body inserted into the
    priors block in place of agent-authored decision content. Length-budget
    accounting (R2-Next-A D2) tracks agent decision length only; system
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


@dataclass(frozen=True)
class CycleSummary:
    """Snapshot of an AgentCycle row used for cross-cycle context injection.

    `id` is included as a tie-breaker for same-timestamp ordering stability
    (review F4): fast in-memory tests / rapid sequential inserts can produce
    multiple rows with identical created_at, and SQLite ORDER BY only on
    created_at would be non-deterministic.

    F-P14: `decision` is now Optional — retry_exhausted / usage_limit_exceeded
    cycles enter the priors list with decision=None and are rendered via
    `_render_empty_decision_body`. `execution_status` carries the cycle
    state for render-layer dispatch.
    """
    id: int
    cycle_id: str
    triggered_by: str
    decision: str | None
    execution_status: str
    created_at: datetime


async def _fetch_recent_summaries(
    engine, session_id: str, n: int = 3,
) -> list[CycleSummary]:
    """Fetch the N most recent cycles for a session (all execution statuses).

    F-P14 (R2-Next-B): no execution_status / decision filter — render layer
    handles three-state branching (ok+valid / ok+NULL / forensic) via
    `_render_empty_decision_body`. Filter removal is intentional: priors
    must reflect actual cycle state including retry_exhausted /
    usage_limit_exceeded so the next cycle sees forensic ⚠️ hints.

    Filters:
      - session_id matches (D-U1-a: session-bound, no cross-session leak)

    Returns [] on:
      - First cycle in session (no prior rows)
      - DB error (any exception logged at WARNING + stack trace via
        exc_info=True + empty list — D-U4-a fail-isolated; cycle must continue)

    Ordering: created_at DESC, id DESC (review F4 tie-breaker for stability).
    Caller (`_render_recent_summaries`) re-sorts ASC for chronological reading
    and dispatches per-row to the empty-body branch when decision is NULL.
    """
    try:
        async with get_session(engine) as session:
            result = await session.execute(
                select(
                    AgentCycle.id,
                    AgentCycle.cycle_id,
                    AgentCycle.triggered_by,
                    AgentCycle.decision,
                    AgentCycle.execution_status,
                    AgentCycle.created_at,
                )
                .where(
                    AgentCycle.session_id == session_id,
                )
                .order_by(
                    AgentCycle.created_at.desc(),
                    AgentCycle.id.desc(),
                )
                .limit(n)
            )
            rows = result.all()
        return [
            CycleSummary(
                id=r.id,
                cycle_id=r.cycle_id,
                triggered_by=r.triggered_by,
                decision=r.decision,
                execution_status=r.execution_status,
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


def _render_recent_summaries(
    summaries: list[CycleSummary], now: datetime,
) -> str:
    """Render summaries as a user-message-ready prefix block.

    Returns "" if list is empty (caller skips header append on first cycle).
    Sorts by (created_at, id) ASC so the reader sees oldest → newest naturally
    (review F4: id tie-breaker keeps same-timestamp ordering stable).

    Tri-state per-prior rendering (F-P14):
      - decision non-NULL (ok cycle, agent-authored): R2-Next-A D2 header
        includes `· {N} words` with ORIGINAL word count (pre-truncation);
        body is `_truncate_decision(decision)`.
      - decision NULL (forensic / ok+empty): header SHORTENS (no word count
        segment); body is system-generated via `_render_empty_decision_body`
        keyed on `execution_status`. Length-budget accounting tracks
        agent-authored content only — system bodies are not counted.
    """
    if not summaries:
        return ""

    blocks = []
    for s in sorted(summaries, key=lambda x: (x.created_at, x.id)):
        cycle_id_short = s.cycle_id[:8]
        utc_str = s.created_at.strftime("%Y-%m-%d %H:%M UTC")
        ago = _format_relative_time(now, s.created_at)

        if not s.decision:
            # F-P14 tri-state: NULL decision → shortened header + system body
            header = (
                f"[cycle {cycle_id_short} · {s.triggered_by} · "
                f"{utc_str} ({ago})]"
            )
            body = _render_empty_decision_body(s.execution_status)
        else:
            # R2-Next-A D2: ok cycle with valid decision — original 5-field header
            word_count = _count_words(s.decision)
            header = (
                f"[cycle {cycle_id_short} · {s.triggered_by} · "
                f"{utc_str} ({ago}) · {word_count} words]"
            )
            body = _truncate_decision(s.decision)

        blocks.append(f"{header}\n{body}")

    header_top = "Your prior cycle summaries (most recent N=3, from this session):"
    return f"{header_top}\n\n" + "\n\n".join(blocks)


def _wake_header_line(events: list[tuple[str, Any]], cycle_started_at: datetime) -> str:
    """Build the wake-prompt header line (spec 2026-06-08 §2).

    N==1: byte-identical to the prior single-trigger header
    (`You have been woken up by a {type} trigger`), with the scheduled fire-time suffix
    appended only for scheduled (its fire time ≡ cycle_started_at → "just now").
    N>1: a multi-event header `You have been woken up by {n} triggers ({breakdown}) since
    the last cycle`, breakdown lists fills before alerts (matching heap priority
    conditional<alert).
    """
    if len(events) == 1:
        tt = events[0][0]
        line = f"You have been woken up by a {tt} trigger"
        if tt == "scheduled":
            line += _wake_time_suffix(
                "fired", int(cycle_started_at.timestamp() * 1000), cycle_started_at,
            )
        return line
    n = len(events)
    breakdown = _format_event_breakdown(events)
    return f"You have been woken up by {n} triggers ({breakdown}) since the last cycle"


async def _build_recent_summaries_block(
    engine, session_id: str, n: int = 3,
) -> str:
    """Fetch + render summaries with a fail-isolated boundary.

    Returns "" on:
      - empty fetch (first cycle in session — F-P14: forensic / NULL decision
        cycles are no longer filtered out, they render via _render_empty_decision_body)
      - any exception during fetch OR render OR format (logged at WARNING with
        stack trace via exc_info=True)

    Review F3: this outer wrap covers the entire injection pipeline, not just
    the DB query layer. _fetch_recent_summaries keeps its own try/except as
    layered defense. Rationale: a render/format exception would otherwise
    bubble before agent.run() and abort the cycle — violating the R2-8b
    "any error never blocks a cycle" promise.
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


class TokenBudget:
    def __init__(self, daily_max: int):
        self._daily_max = daily_max
        self._used = 0
        self._reset_date = self._today()

    @staticmethod
    def _today() -> str:
        from datetime import date
        return date.today().isoformat()

    def _check_reset(self) -> None:
        today = self._today()
        if today != self._reset_date:
            logger.info(f"New day ({today}), resetting token budget")
            self._used = 0
            self._reset_date = today

    def record(self, tokens: int) -> None:
        self._check_reset()
        self._used += tokens

    @property
    def remaining(self) -> int:
        self._check_reset()
        return max(0, self._daily_max - self._used)

    @property
    def exhausted(self) -> bool:
        self._check_reset()
        return self._used >= self._daily_max


class _DummySessionStats(SessionStats):
    """No-op SessionStats subclass for tests that pass run_agent_cycle without stats kwarg.

    Inherits SessionStats so type annotations `stats: SessionStats` are LSP-compatible
    (no mypy/pyright strict warning). __init__ inherits → properties return 0/None defaults.
    Override only record_cycle to no-op (no per-cycle stat mutation).

    Module-level singleton (`_DUMMY_STATS`) — avoid per-cycle instantiation overhead.
    """
    def record_cycle(self, cycle_tokens: int, cycle_ended_at: datetime) -> None:  # noqa: ARG002
        pass  # no-op — discard inputs


_DUMMY_STATS = _DummySessionStats()


async def _record_action_from_fill(engine, session_id, event: FillEvent):
    """将 FillEvent 记录为 TradeAction。

    iter-tool-opt-net-pnl-metrics: 同步写 amount + entry_price
    （per spec §C2 / §6.5 OKX cache miss 时 entry_price 可 NULL）.
    """
    async with get_session(engine) as session:
        session.add(TradeAction(
            session_id=session_id,
            action="order_filled",
            order_id=event.order_id,
            symbol=event.symbol,
            side=event.position_side,
            trigger_reason=event.trigger_reason,
            price=event.fill_price,
            pnl=event.pnl,
            fee=event.fee,
            amount=event.amount,
            entry_price=event.entry_price,
            reasoning=f"(exchange: {event.trigger_reason} order filled @ {event.fill_price:.2f})",
        ))
        await session.commit()


def _rollback_injected_events(deps: TradingDeps) -> None:
    """被丢弃的 run ⇒ 注入回滚（spec §2）。

    retry 重试前、usage_limit / retry_exhausted 终态 forensic 写库前调用：被该 run
    消费的注入事件 requeue 回堆（经兜底通道重新送达——retry 场景通常被下一 attempt
    的首次工具调用重新注入），累积器清空 → 被丢弃 run 的 injected_events 落 NULL。
    不回滚则事件永远到不了任何存活决策（送达盲区换处藏身）。
    """
    if deps.injected_events_log and deps.requeue_events_fn is not None:
        deps.requeue_events_fn([rec["raw"] for rec in deps.injected_events_log])
    deps.injected_events_log.clear()


async def run_agent_cycle(
    agent,
    deps: TradingDeps,
    events: list[tuple[str, Any]],
    budget: TokenBudget,
    engine,
    model=None,
    console=None,
    stats: SessionStats | None = None,
):
    if stats is None:
        stats = _DUMMY_STATS
    if budget.exhausted:
        logger.warning("Daily LLM token budget exhausted, skipping cycle")
        return None

    cycle_started_at = datetime.now(timezone.utc)
    cycle_id = str(uuid.uuid4())[:8]
    deps.cycle_id = cycle_id   # propagate to ToolCallRecorder via ctx.deps (§3.4 of spec)
    deps.cycle_started_at = cycle_started_at     # 注入 offset_ms 时间基准（spec §6）
    deps.injected_events_log.clear()             # per-cycle 复位（spec §6）

    # R2-7 §6.7: capture trigger_context + state_snapshot 在 retry loop 之前
    # (一次, success / forensic 两路径复用同一对 *_var)
    # P8: 必须在 `for attempt in range(3):` retry loop 之前, 不能在 loop 内
    # (重复 capture 会让 IO 4× retry + state_snapshot 时刻漂移 + 违反 §6.7 不变量)
    trigger_context_var = _capture_trigger_contexts(cycle_id, events)
    # triggered_by = dominant (highest-priority) type — events arrive in heap priority
    # order (conditional > alert > scheduled; see scheduler.py drain), so the lead element
    # is the dominant type. Precondition: events is always non-empty — the scheduler passes
    # at least the degenerate [("scheduled", None)] tick — so events[0] never IndexErrors.
    triggered_by = events[0][0]
    state_snapshot_var = await _capture_state_snapshot(cycle_id, deps)
    # PR #35 I3: 与 capture-once P8 同模式 — hoist model_id 到 retry loop 之前
    # 防 forensic 路径在 except 块内 getattr/str(agent.model) raise 致整 cycle 写入丢失.
    model_id_var = getattr(model, 'model_name', str(model)) if model else str(agent.model)

    # Wake prompt (spec 2026-06-08 §2): priority-sectioned. N==1 is byte-identical to the
    # prior single-event prompt; N>1 uses a multi-trigger header + one block per event in
    # heap priority order (fills before alerts).
    header_line = _wake_header_line(events, cycle_started_at)
    prompt = (
        f"{header_line}.\n"
        f"Trading pair: {deps.symbol} | Timeframe: {deps.timeframe}\n"
        "Assess the situation and decide what to do."
    )
    for tt, ctx in events:
        prompt += await _render_event_block(deps, tt, ctx, cycle_started_at)

    # R2-8b: inject most recent N=3 cycle summaries from this session
    # (D-D-E injection position: trigger context → recent).
    # _build_recent_summaries_block is fail-isolated (review F3) — any
    # error returns "" and lets the cycle proceed.
    recent_block = await _build_recent_summaries_block(
        engine, deps.session_id, n=3,
    )
    if recent_block:
        prompt += f"\n\n{recent_block}"

    # P4 (obs roadmap Phase 3): capture full user_prompt for forensic snapshot. String
    # reference assignment cannot raise — see spec §5.3 (cycle-level new
    # failure surface = 0).
    user_prompt_snapshot_var = prompt

    # LLM call with exponential backoff retry
    run_kwargs = {"deps": deps}
    if model is not None:
        run_kwargs["model"] = model

    result = None
    llm_call_ms = None      # Phase 1 (T10): default None; happy 路径覆写为实际值；forensic 路径保 None
    for attempt in range(3):
        try:
            llm_start = datetime.now(timezone.utc)
            result = await agent.run(
                prompt,
                usage_limits=USAGE_LIMITS_PER_CYCLE,
                **run_kwargs,
            )
            llm_end = datetime.now(timezone.utc)
            llm_call_ms = int((llm_end - llm_start).total_seconds() * 1000)
            break
        except UsageLimitExceeded as e:
            # 病理状态（LLM 死循环 / runaway tools），不重试，写 forensic trace。
            # 注：ToolCallRecorder capability 已在 agent.run 内部独立 session 写完
            # 任何已成功 tool 调用的 tool_calls 行（不需要本路径协调 rollback）。
            logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
            _rollback_injected_events(deps)   # 被丢弃 run ⇒ 注入回滚（spec §2）
            async with get_session(engine) as session:
                session.add(AgentCycle(
                    session_id=deps.session_id,
                    cycle_id=cycle_id,
                    triggered_by=triggered_by,
                    trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                    state_snapshot=json.dumps(state_snapshot_var),
                    reasoning=None,                              # R2-7 §6.5: forensic NULL
                    decision=None,
                    execution_status="usage_limit_exceeded",
                    model_id=model_id_var,
                    tokens_consumed=0,                            # spec §3.1 #3: UsageLimitExceeded 不携带 partial usage
                    # === Phase 1 (T11 forensic) — 仅 wall_time_ms 填，其余 NULL ===
                    wall_time_ms=int((datetime.now(timezone.utc) - cycle_started_at).total_seconds() * 1000),
                    llm_call_ms=llm_call_ms,    # default None (T10 retry-loop pre-init)
                    input_tokens=None,
                    output_tokens=None,
                    cache_read_tokens=None,
                    cache_write_tokens=None,
                    reasoning_tokens=None,
                    cache_hit_rate=None,
                    user_prompt_snapshot=user_prompt_snapshot_var,  # P4 (obs roadmap Phase 3)
                    injected_events=None,   # 回滚后落 NULL（spec §2/§6）
                    react_steps=None,       # webui-react-timeline §5.3: forensic 无骨架
                ))
                await session.commit()
            # capture cycle_ended_at AFTER DB commit — 与正常路径时序对齐：
            # Footer Duration 字段语义统一为 "实墙时间含 DB 写入"
            cycle_ended_at = datetime.now(timezone.utc)
            if console is not None:
                from src.cli.display import CycleRenderContext
                ctx = CycleRenderContext(
                    cycle_id=cycle_id, trigger_type=triggered_by,
                    trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
                    messages=None, final_text=None,
                    cycle_tokens=0, stats=stats, cache_hit_rate=None,
                    cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
                    forensic_reason="usage_limit_exceeded",
                    user_prompt_snapshot=user_prompt_snapshot_var,
                )
                console.print(format_cycle_output(ctx))
            stats.record_cycle(0, cycle_ended_at)
            return None
        except Exception as e:
            _rollback_injected_events(deps)   # 被丢弃 attempt ⇒ 注入回滚（spec §2，重试前 / 终态写库前）
            if attempt < 2:
                delay = 2 ** attempt
                logger.warning(f"LLM call attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                # spec §6.5 D16: retry-exhausted forensic write + session log render — 避免 W2 SQL 黑洞
                logger.error(f"LLM call failed after 3 attempts: {e}")
                err_class = type(e).__name__
                # spec §6.5 T-EX-2: > 200 chars 截断 + 省略号
                err_raw = str(e)
                err_msg = (err_raw[:200] + "...") if len(err_raw) > 200 else err_raw
                async with get_session(engine) as session:
                    session.add(AgentCycle(
                        session_id=deps.session_id,
                        cycle_id=cycle_id,
                        triggered_by=triggered_by,
                        trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                        state_snapshot=json.dumps(state_snapshot_var),
                        reasoning=None,
                        decision=None,
                        execution_status="retry_exhausted",
                        model_id=model_id_var,
                        tokens_consumed=0,
                        # === Phase 1 (T11 forensic) — 仅 wall_time_ms 填，其余 NULL ===
                        wall_time_ms=int((datetime.now(timezone.utc) - cycle_started_at).total_seconds() * 1000),
                        llm_call_ms=llm_call_ms,    # default None
                        input_tokens=None,
                        output_tokens=None,
                        cache_read_tokens=None,
                        cache_write_tokens=None,
                        reasoning_tokens=None,
                        cache_hit_rate=None,
                        user_prompt_snapshot=user_prompt_snapshot_var,  # P4 (obs roadmap Phase 3)
                        injected_events=None,   # 回滚后落 NULL（spec §2/§6）
                        react_steps=None,       # webui-react-timeline §5.3: forensic 无骨架
                    ))
                    await session.commit()
                # capture cycle_ended_at AFTER DB commit — 与正常路径 + UsageLimitExceeded 路径
                # 时序对齐：Footer Duration 字段语义统一为 "实墙时间含 DB 写入"
                cycle_ended_at = datetime.now(timezone.utc)
                if console is not None:
                    from src.cli.display import CycleRenderContext
                    ctx = CycleRenderContext(
                        cycle_id=cycle_id, trigger_type=triggered_by,
                        trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
                        messages=None, final_text=None,
                        cycle_tokens=0, stats=stats, cache_hit_rate=None,
                        cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
                        forensic_reason=f"aborted: {err_class}: {err_msg}",
                        user_prompt_snapshot=user_prompt_snapshot_var,
                    )
                    console.print(format_cycle_output(ctx))
                stats.record_cycle(0, cycle_ended_at)
                return None

    usage = result.usage()
    tokens = usage.total_tokens if usage else 0
    details = (usage.details or {}) if usage else {}

    # === 旧变量名保留（cli/app.py:613-616 logger.info + sim log 解析脚本兼容）===
    # reasoning_tokens 由 DeepSeek/OpenAI o-series 等 thinking 模型返回；
    # > 0 即可验证 thinking mode 在本 cycle 真实生效。
    reasoning_tokens = details.get("reasoning_tokens", 0)
    # DeepSeek prompt-cache 命中观测：cycle 2+ hit_rate > 0 表明前缀 cache 工作。
    cache_hit = details.get("prompt_cache_hit_tokens", 0)
    cache_miss = details.get("prompt_cache_miss_tokens", 0)
    input_total = cache_hit + cache_miss
    hit_rate = (cache_hit / input_total * 100) if input_total > 0 else 0.0

    # === 新变量 — pydantic-ai 标准属性给 DB 写入（更 portable + AC-11 验证一致）===
    # T0 实测验证 0.0% 误差; spec §5.5.1 Note 1.
    cache_read  = usage.cache_read_tokens  if usage else 0
    cache_write = usage.cache_write_tokens if usage else 0
    input_tok   = usage.input_tokens       if usage else 0
    output_tok  = usage.output_tokens      if usage else 0

    logger.info(
        f"cycle {cycle_id} tokens: total={tokens} reasoning={reasoning_tokens} "
        f"cache_hit={cache_hit} cache_miss={cache_miss} rate={hit_rate:.1f}%"
    )
    budget.record(tokens)

    # === A2: System log per-tool INFO/DEBUG ===
    # (R2-8a: 不再累积 tool_calls list 给 display 用 — ctx 直接消费 messages；
    # _call_args_by_id 仅 lifetime 内 args lookup, 写完 logger 即弃)
    _call_args_by_id: dict[str, dict | None] = {}
    for msg in result.new_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    try:
                        args = part.args_as_dict()
                    except Exception:
                        args = None
                    _call_args_by_id[part.tool_call_id] = args
        elif isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    content_str = str(part.content)
                    outcome = part.outcome
                    if part.tool_call_id not in _call_args_by_id:
                        logger.warning(
                            f"tool_call_id mismatch for {part.tool_name}, using fallback"
                        )
                    args = _call_args_by_id.get(part.tool_call_id)
                    icon, summary = resolve_tool_display(
                        part.tool_name, content_str, outcome, args,
                    )
                    logger.info(f"  {icon} {part.tool_name}: {summary}")
                    logger.debug(
                        f"  Tool {part.tool_name} args={args} "
                        f"return={content_str[:500]}"
                    )

    # === Record to database ===
    thinking_text = _extract_thinking_text(result.new_messages())
    async with get_session(engine) as session:
        session.add(
            AgentCycle(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                triggered_by=triggered_by,
                trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                state_snapshot=json.dumps(state_snapshot_var),
                reasoning=thinking_text,                          # R2-7 §6.3: thinking content
                decision=result.output,                           # R2-7 §6.4: message content (no cap)
                execution_status="ok",
                model_id=model_id_var,
                tokens_consumed=tokens,
                # === Phase 1 (T10) ===
                # spec §5.5.1 Note 2: wall_time_ms 在 commit 之前 capture，
                # 比 footer Duration (commit 之后) 少 ~5-50ms（DB write 时间）。
                # 分析者比对二者时勿误认为 bug；R2-Next-J cycle state machine
                # refactor 是消除此差的 follow-up.
                wall_time_ms=int((datetime.now(timezone.utc) - cycle_started_at).total_seconds() * 1000),
                llm_call_ms=llm_call_ms,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                reasoning_tokens=reasoning_tokens,
                cache_hit_rate=hit_rate,
                user_prompt_snapshot=user_prompt_snapshot_var,  # P4 (obs roadmap Phase 3)
                injected_events=json.dumps(
                    [{k: v for k, v in rec.items() if k != "raw"}
                     for rec in deps.injected_events_log]
                ) if deps.injected_events_log else None,   # raw 回滚句柄落库剥离（spec §6）
                react_steps=_safe_build_react_steps(result.new_messages()),  # webui-react-timeline §5.3
            )
        )
        await session.commit()

    logger.info(f"Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)")

    # === R2-8a: Build CycleRenderContext + render + record stats ===
    cycle_ended_at = datetime.now(timezone.utc)
    if console is not None:
        from src.cli.display import CycleRenderContext
        ctx = CycleRenderContext(
            cycle_id=cycle_id, trigger_type=triggered_by,
            trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
            messages=result.new_messages(), final_text=result.output,
            cycle_tokens=tokens, stats=stats, cache_hit_rate=hit_rate,
            cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
            forensic_reason=None,
            user_prompt_snapshot=user_prompt_snapshot_var,
        )
        console.print(format_cycle_output(ctx))
    stats.record_cycle(tokens, cycle_ended_at)

    return result


# --- Phase 5: Service construction ---

def _compute_max_wake(scheduler_interval_min: int) -> int:
    """Compute wake_max_minutes ceiling from scheduler interval.

    Formula: 4 * interval, clamped to [60, 180]. Single source of truth shared
    by build_services and P4 session-level capture (run() in src/cli/app.py).

    See test_drift_p4_capture_paths.py::test_p4_runtime_config_matches_build_services
    for invariant pinning.
    """
    return min(max(4 * scheduler_interval_min, 60), 180)


def backoff_min(n: int, fallback: int) -> int:
    """崩溃重唤退避分钟数（spec §1 退避曲线纯函数）。

    n        = 连续 retry_exhausted 次数（≥1）。
    fallback = scheduler_interval_min（会话兜底间隔，即封顶）。

    curve: min(fallback, floor · 2^(n-1)), floor = min(2, fallback)
      fallback=60  → 2,4,8,16,32,60(封顶),60…
      fallback=180 → 2,4,…,128,180(封顶)
      fallback=1   → floor 被 min(2,1) 压成 1 → 恒 1（no-op）

    封顶是兜底间隔而非 wake_max_minutes：崩溃后最坏退回会话正常巡检节奏，绝不更慢。
    """
    floor = min(2, fallback)
    return min(fallback, floor * 2 ** (n - 1))


# 退避曲线在 n≈8 即饱和到 fallback（floor·2^7=256 > 最大 fallback 180），故连崩计数
# 取到 cap 即可——超出部分既不改退避值、又能 bound 内存 + 防 2^(n-1) 大整数膨胀。
_CRASH_STREAK_FETCH_CAP = 16


async def _count_consecutive_retry_exhausted(engine, session_id: str) -> int:
    """本会话尾部连续 retry_exhausted 的 cycle 数（spec §1「连续崩溃计数」）。

    按 id 倒序（自增 PK 严格单调）从最新 cycle 起数，遇首个非 retry_exhausted 即止。
    不用 created_at DESC——SQLite DateTime(timezone=True) 读回 naive（feedback_sqlite_
    naive_datetime_readback）且同秒并列无序。fetch 上限 _CRASH_STREAK_FETCH_CAP：曲线
    已饱和，streak ≥ cap 与 = cap 产出同一（封顶）退避。
    """
    async with get_session(engine) as session:
        rows = await session.execute(
            select(AgentCycle.execution_status)
            .where(AgentCycle.session_id == session_id)
            .order_by(AgentCycle.id.desc())
            .limit(_CRASH_STREAK_FETCH_CAP)
        )
        n = 0
        for (status,) in rows:
            if status == "retry_exhausted":
                n += 1
            else:
                break
        return n


async def _capture_session_system_prompt(
    engine,
    session_id: str,
    persona: PersonaConfig,
    runtime: RuntimeConfig,
) -> None:
    """P4 session-level capture: render system_prompt + UPDATE sessions row.

    Fail-isolated: any exception is logged at WARNING and swallowed; session
    startup is never blocked. Resulting NULL row is interpreted as "capture
    failed" via the warning in session log (see spec §5.4).

    Called once per session start (including resume) from run(). The UPDATE
    semantics align with the resume-path model_config rewrite at
    src/cli/session_manager.py:170-176 — both fields are session-fixed and
    refreshed on every startup.
    """
    try:
        system_prompt_text = generate_system_prompt(persona, runtime)
        async with get_session(engine) as s:
            await s.execute(
                sql_update(Session)
                .where(Session.id == session_id)
                .values(system_prompt=system_prompt_text)
            )
            await s.commit()
    except Exception as e:
        logger.warning(f"P4 system_prompt capture failed: {e!r}")


async def build_services(
    result: WizardResult,
    engine,
    session_id: str,
    sc: SessionConsole,
    settings: Settings,
):
    """Build exchange, deps, agent, budget from WizardResult."""
    if result.fee_rate is None:
        raise ValueError(
            "Session has no fee_rate configured. This usually means a legacy "
            "session was loaded but the resume flow's fee_rate sub-step did "
            "not run. To recover: (a) restart the CLI to trigger wizard resume "
            "flow; (b) or manually UPDATE sessions SET fee_rate=0.0005 WHERE "
            "id=<your_session_id> in DB and restart."
        )
    # Exchange
    if result.exchange_type == "simulated":
        from src.integrations.exchange.simulated import SimulatedExchange
        config = ExchangeConfig(
            name="simulated", fee_rate=result.fee_rate,
        )
        exchange = SimulatedExchange(
            config=config, db_engine=engine,
            session_id=session_id, symbol=result.symbol,
        )
        sc.print("Exchange: simulated (local matching)")
    else:
        creds = result.api_credentials
        exchange = OKXExchange(
            api_key=creds["api_key"], secret=creds["secret"],
            password=creds["password"], symbol=result.symbol,
            sandbox=settings.exchange.sandbox,
        )
        account_label = "demo (sandbox)" if settings.exchange.sandbox else "REAL (live)"
        sc.print(f"Exchange: okx — {account_label}")

    # iter-tool-opt-contract-fee-visibility: 市场元数据前置（spec §3.6）。
    # init_market_meta 失败发生在 run() 的 try/finally 之前 → 此处自行清理（硬约束 2）。
    try:
        contract_size = await exchange.init_market_meta()
    except Exception:
        # close() 自身抛错不得 mask init_market_meta 的原始异常（如 contractSize
        # missing）——suppress 保证原始错误总是浮现，而非降级为 __context__。
        with contextlib.suppress(Exception):
            await exchange.close()
        raise

    market_data = MarketDataService(exchange)
    technical = TechnicalAnalysisService()
    memory = MemoryService(engine, session_id=session_id)
    budget = TokenBudget(daily_max=result.token_budget)
    approval_gate = ApprovalGate(
        enabled=result.approval_enabled,
        timeout_seconds=settings.approval.timeout_seconds,
        console=sc,
    )

    # R2-5: session-fixed runtime config injected into system prompt
    from src.integrations.news.models import extract_base_currency

    max_wake = _compute_max_wake(result.scheduler_interval_min)
    runtime_config = RuntimeConfig(
        wake_max_minutes=max_wake,
        taker_fee_rate=result.fee_rate,
        contract_size=contract_size,
        base_ccy=extract_base_currency(result.symbol),
    )
    agent = create_trader_agent(
        model=result.model,
        persona_config=result.persona,
        runtime=runtime_config,
    )

    from src.services.metrics import MetricsService
    metrics_service = MetricsService(
        engine=engine,
        session_id=session_id,
        initial_balance=result.initial_balance,
    )

    # News service — CoinDesk News is key-gated (env COINDESK_API_KEY); FGI,
    # ForexFactory and OKX remain keyless.
    news_service = None
    if settings.news.enabled:
        from src.integrations.news.service import NewsService
        news_service = NewsService(api_key=settings.news.coindesk_api_key)
        sc.print("News: ON (CoinDesk News + FGI + alerts)")
    else:
        sc.print("News: OFF")

    # N3: Macro service — CoinGecko /global + FRED + Alpha Vantage.
    macro_service = None
    if settings.macro.enabled:
        from src.integrations.macro.service import MacroService
        macro_service = MacroService(
            fred_key=settings.macro.fred_api_key,
            av_key=settings.macro.alpha_vantage_api_key,
            cg_key=settings.macro.coingecko_demo_api_key,
        )
        sc.print("Macro: ON (FRED + Alpha Vantage + CoinGecko)")
    else:
        sc.print("Macro: OFF")

    # N3: Crypto ETF service — SoSoValue.
    crypto_etf_service = None
    if settings.crypto_etf.enabled:
        from src.integrations.crypto_etf.service import CryptoEtfService
        crypto_etf_service = CryptoEtfService(
            api_key=settings.crypto_etf.sosovalue_api_key,
        )
        sc.print("Crypto ETF: ON (SoSoValue)")
    else:
        sc.print("Crypto ETF: OFF")

    # N3: Onchain service — DefiLlama stablecoins.
    onchain_service = None
    if settings.onchain.enabled:
        from src.integrations.onchain.service import OnchainService
        onchain_service = OnchainService()
        sc.print("Onchain: ON (DefiLlama stablecoins)")
    else:
        sc.print("Onchain: OFF")

    deps = TradingDeps(
        symbol=result.symbol,
        timeframe=result.timeframe,
        market_data=market_data,
        exchange=exchange,
        technical=technical,
        memory=memory,
        session_id=session_id,
        db_engine=engine,
        approval_gate=approval_gate,
        approval_enabled=result.approval_enabled,
        initial_balance=result.initial_balance,
        fee_rate=result.fee_rate,
        metrics=metrics_service,
        news=news_service,
        macro=macro_service,
        crypto_etf=crypto_etf_service,
        onchain=onchain_service,
        wake_min_minutes=1,
        wake_max_minutes=max_wake,
        scheduler_interval_min=result.scheduler_interval_min,   # spec §1: 退避封顶来源
    )

    # R2-5 PR #34 I-A: prompt range (RuntimeConfig) and clamp authority (TradingDeps)
    # must agree by construction — fail-loud at startup if a future refactor breaks one
    assert deps.wake_max_minutes == runtime_config.wake_max_minutes, (
        f"R2-5 drift: prompt range {runtime_config.wake_max_minutes} vs "
        f"clamp {deps.wake_max_minutes} must match"
    )
    assert deps.fee_rate == runtime_config.taker_fee_rate, (
        f"fee_rate drift: TradingDeps {deps.fee_rate} vs "
        f"RuntimeConfig {runtime_config.taker_fee_rate} must match"
    )

    stats = SessionStats()
    return exchange, deps, agent, budget, stats


async def run(
    settings_path: Path = Path("config/settings.yaml"),
    trader_path: Path = Path("config/trader.yaml"),
    model_id: str | None = None,
    debug: bool = False,
):
    # ── Phase 1: System logging ──
    log_dir = settings_path.resolve().parent.parent / "logs"
    pre_console = setup_system_logging(debug, log_dir)
    pre_console.print("[bold green]TradeBot — Starting...[/]\n")

    # ── Phase 2: Config + Database ──
    settings = load_settings(settings_path)
    trader_config = load_trader_config(trader_path)

    project_root = settings_path.resolve().parent.parent
    data_dir = project_root / "data"
    data_dir.mkdir(exist_ok=True)
    db_url = settings.database.url
    if db_url.startswith("sqlite") and ":///" in db_url and not db_url.startswith("sqlite+aiosqlite:////"):
        relative_path = db_url.split(":///", 1)[1]
        absolute_path = project_root / relative_path
        db_url = f"sqlite+aiosqlite:///{absolute_path}"
    engine = await init_db(db_url)

    # ── Phase 3: Session select / wizard ──
    from src.cli.session_manager import select_or_create_session
    from src.services.model_manager import ModelManager

    config_dir = project_root / "config"
    model_manager = ModelManager(config_path=config_dir / "models.json")

    result, session_id, is_new = await select_or_create_session(
        engine=engine,
        settings=settings,
        trader_config=trader_config,
        model_manager=model_manager,
        model_id=model_id,
        console=pre_console,
        config_dir=config_dir,
    )

    # ── Phase 4: Session logging ──
    sc = setup_session_logging(session_id, log_dir)
    write_session_header(
        sc,
        name=result.session_name,
        session_id=session_id,
        symbol=result.symbol,
        mode=result.exchange_type,
        timeframe=result.timeframe,
        interval_min=result.scheduler_interval_min,
        is_new=is_new,
        started_at=datetime.now(timezone.utc),
    )

    # ── Phase 5: Build services ──
    exchange, deps, agent, budget, stats = await build_services(
        result, engine, session_id, sc, settings,
    )

    # ── Phase 5b: P4 system_prompt capture ──
    from src.integrations.news.models import extract_base_currency

    runtime_config_for_capture = RuntimeConfig(
        wake_max_minutes=_compute_max_wake(result.scheduler_interval_min),
        taker_fee_rate=result.fee_rate,
        # 幂等返回已校验值；不走 get_contract_size（其含 1.0 静默兜底，与硬约束 1 口径相左）
        contract_size=await exchange.init_market_meta(),
        base_ccy=extract_base_currency(result.symbol),
    )
    await _capture_session_system_prompt(
        engine, session_id, result.persona, runtime_config_for_capture,
    )

    # ── Phase 6: Main loop ──
    shutdown_event = asyncio.Event()

    def _signal_handler():
        sc.print("\n[yellow]Shutting down gracefully...[/]")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    async def on_tick(events: list[tuple[str, Any]]):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(
                agent, deps, events, budget, engine,
                model=result.model, console=sc, stats=stats,
            )
        except Exception:
            logger.exception("Agent cycle failed")
        finally:
            # Update last_active_at
            try:
                async with get_session(engine) as db_sess:
                    await db_sess.execute(
                        sql_update(Session).where(Session.id == session_id).values(
                            last_active_at=datetime.now(timezone.utc)
                        )
                    )
                    await db_sess.commit()
            except Exception:
                logger.warning("Failed to update last_active_at", exc_info=True)

    interval = result.scheduler_interval_min * 60
    scheduler = Scheduler(interval_seconds=interval, callback=on_tick)

    # R4: dynamic wake fn binds scheduler (wake bounds assembled in build_services)
    deps.set_next_wake_fn = lambda minutes, ctx: scheduler.set_next_interval(minutes * 60, ctx)

    # iter-midcycle-event-injection §1: 注入弹堆/回滚句柄（两者须同时接线）
    deps.drain_pending_events_fn = scheduler.drain_pending_events
    deps.requeue_events_fn = scheduler.requeue_events

    def _create_fill_handler(sched, eng, sid):
        async def handler(event: FillEvent):
            try:
                await _record_action_from_fill(eng, sid, event)
            except Exception:
                logger.warning("Failed to record fill event", exc_info=True)
            finally:
                await sched.trigger("conditional", context=event)
        return handler

    handle_fill = _create_fill_handler(scheduler, engine, session_id)
    exchange.on_fill(handle_fill)

    async def handle_alert(alert_info):
        await scheduler.trigger("alert", context=alert_info)
    exchange.on_alert(handle_alert)

    # Wrap startup → main loop → shutdown in try/finally so that an error
    # anywhere after build_services (exchange.start failures, scheduler
    # setup, etc.) still triggers resource cleanup. Without this, a failing
    # exchange.start() would leak the NewsService's httpx client and the
    # exchange's WebSocket connections until GC.
    try:
        await exchange.start()

        # Initial metrics
        positions = await exchange.fetch_positions(result.symbol)
        pos_str = f"{positions[0].side} {positions[0].contracts}" if positions else "none"
        metrics = await deps.metrics.compute(current_position=pos_str)
        display_metrics(metrics, console=sc)

        sc.print(f"\n[bold]Scheduler: every {result.scheduler_interval_min} min[/]")
        sc.print(f"[bold]LLM Budget: {result.token_budget:,} tokens/day[/]")
        sc.print("[dim]Press Ctrl+C to stop[/]\n")

        scheduler_task = asyncio.create_task(scheduler.start())
        await shutdown_event.wait()

        scheduler.stop()
        await scheduler_task
    finally:
        try:
            await exchange.close()
        except Exception:
            logger.warning("Failed to close exchange", exc_info=True)
        if deps.news is not None:
            try:
                await deps.news.close()
            except Exception:
                logger.warning("Failed to close news service", exc_info=True)
        if deps.macro is not None:
            try:
                await deps.macro.close()
            except Exception:
                logger.warning("Failed to close macro service", exc_info=True)
        if deps.crypto_etf is not None:
            try:
                await deps.crypto_etf.close()
            except Exception:
                logger.warning("Failed to close crypto_etf service", exc_info=True)
        if deps.onchain is not None:
            try:
                await deps.onchain.close()
            except Exception:
                logger.warning("Failed to close onchain service", exc_info=True)

    # Update session status to paused on graceful shutdown
    async with get_session(engine) as db_sess:
        await db_sess.execute(
            sql_update(Session).where(Session.id == session_id).values(status="paused")
        )
        await db_sess.commit()

    sc.close()
    pre_console.print("[green]TradeBot stopped.[/]")
