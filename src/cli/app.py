from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update as sql_update

from src.agent.memory import MemoryService
from src.agent.trader import TradingDeps, create_trader_agent
from src.agent.persona import (
    CYCLE_DECISION_CHAR_HARD_FLOOR,
    CYCLE_DECISION_WORD_CAP,
    RuntimeConfig,
)
from src.cli.approval import ApprovalGate
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, ThinkingPart,
    ToolCallPart, ToolReturnPart,
)
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from src.cli.display import (
    display_metrics, format_cycle_output,
    is_tool_error, resolve_tool_display,
)
from src.cli.logging_config import SessionConsole, setup_session_logging, setup_system_logging
from src.cli.session_state import SessionStats
from src.config import ExchangeConfig, Settings, load_settings, load_trader_config
from src.integrations.exchange.okx import OKXExchange
from src.integrations.market_data import MarketDataService
from src.scheduler.scheduler import Scheduler
from src.services.cycle_capture import _capture_state_snapshot, _capture_trigger_context
from src.services.technical import TechnicalAnalysisService
from src.storage.database import get_session, init_db
from src.storage.models import AgentCycle, Session, TradeAction
from src.integrations.exchange.base import FillEvent, PriceLevelAlertInfo
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


def _format_relative_time(now: datetime, then: datetime) -> str:
    """Format a delta as '8 min ago' / '2 hours ago' / '1 day ago'.

    SQLite returns naive datetime even when schema is DateTime(timezone=True);
    normalize to UTC-aware before subtraction (same pattern as
    session_manager.py:294-295).
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
    """将 FillEvent 记录为 TradeAction。"""
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
            reasoning=f"(exchange: {event.trigger_reason} order filled @ {event.fill_price:.2f})",
        ))
        await session.commit()


async def run_agent_cycle(
    agent,
    deps: TradingDeps,
    trigger_type: str,
    budget: TokenBudget,
    engine,
    context=None,
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

    # R2-7 §6.7: capture trigger_context + state_snapshot 在 retry loop 之前
    # (一次, success / forensic 两路径复用同一对 *_var)
    # P8: 必须在 `for attempt in range(3):` retry loop 之前, 不能在 loop 内
    # (重复 capture 会让 IO 4× retry + state_snapshot 时刻漂移 + 违反 §6.7 不变量)
    trigger_context_var = _capture_trigger_context(cycle_id, trigger_type, context)
    state_snapshot_var = await _capture_state_snapshot(cycle_id, deps)
    # PR #35 I3: 与 capture-once P8 同模式 — hoist model_id 到 retry loop 之前
    # 防 forensic 路径在 except 块内 getattr/str(agent.model) raise 致整 cycle 写入丢失.
    model_id_var = getattr(model, 'model_name', str(model)) if model else str(agent.model)

    prompt = (
        f"You have been woken up by a {trigger_type} trigger.\n"
        f"Trading pair: {deps.symbol} | Timeframe: {deps.timeframe}\n"
        "Assess the situation and decide what to do."
    )
    if trigger_type == "conditional" and context is not None:
        msg = (
            f"\n\nIMPORTANT EVENT: {context.trigger_reason} triggered "
            f"— {context.symbol} {context.amount} @ {context.fill_price}"
        )
        if context.pnl is not None:
            msg += f", PnL: {context.pnl:.2f} USDT"
        prompt += msg
    elif trigger_type == "alert" and context is not None:
        if isinstance(context, PriceLevelAlertInfo):
            prompt += (
                f"\n\nPRICE LEVEL: {context.symbol} reached {context.current_price:.2f} "
                f"(your alert: {context.direction} {context.target_price:.2f} "
                f"— {context.reasoning})"
            )
        else:
            direction = "dropped" if context.change_pct < 0 else "surged"
            prompt += (
                f"\n\nPRICE ALERT: {context.symbol} {direction} {abs(context.change_pct):.1f}% "
                f"in {context.window_minutes}min ({context.reference_price:.2f} → {context.current_price:.2f})"
            )

    # R2-8b: inject most recent N=3 cycle summaries from this session
    # (D-D-E injection position: trigger context → recent → memory).
    # _build_recent_summaries_block is fail-isolated (review F3) — any
    # error returns "" and lets the cycle proceed.
    recent_block = await _build_recent_summaries_block(
        engine, deps.session_id, n=3,
    )
    if recent_block:
        prompt += f"\n\n{recent_block}"

    memory_context = await deps.memory.format_for_prompt()
    if memory_context != "No relevant memories.":
        prompt += f"\n\nYour memories:\n{memory_context}"

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
            async with get_session(engine) as session:
                session.add(AgentCycle(
                    session_id=deps.session_id,
                    cycle_id=cycle_id,
                    triggered_by=trigger_type,
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
                ))
                await session.commit()
            # capture cycle_ended_at AFTER DB commit — 与正常路径时序对齐：
            # Footer Duration 字段语义统一为 "实墙时间含 DB 写入"
            cycle_ended_at = datetime.now(timezone.utc)
            if console is not None:
                from src.cli.display import CycleRenderContext
                ctx = CycleRenderContext(
                    cycle_id=cycle_id, trigger_type=trigger_type,
                    trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
                    messages=None, final_text=None,
                    cycle_tokens=0, stats=stats, cache_hit_rate=None,
                    cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
                    forensic_reason="usage_limit_exceeded",
                )
                console.print(format_cycle_output(ctx))
            stats.record_cycle(0, cycle_ended_at)
            return None
        except Exception as e:
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
                        triggered_by=trigger_type,
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
                    ))
                    await session.commit()
                # capture cycle_ended_at AFTER DB commit — 与正常路径 + UsageLimitExceeded 路径
                # 时序对齐：Footer Duration 字段语义统一为 "实墙时间含 DB 写入"
                cycle_ended_at = datetime.now(timezone.utc)
                if console is not None:
                    from src.cli.display import CycleRenderContext
                    ctx = CycleRenderContext(
                        cycle_id=cycle_id, trigger_type=trigger_type,
                        trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
                        messages=None, final_text=None,
                        cycle_tokens=0, stats=stats, cache_hit_rate=None,
                        cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
                        forensic_reason=f"aborted: {err_class}: {err_msg}",
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
                triggered_by=trigger_type,
                trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                state_snapshot=json.dumps(state_snapshot_var),
                reasoning=thinking_text,                          # R2-7 §6.3: thinking content
                decision=result.output,                           # R2-7 §6.4: message content (no cap)
                execution_status="ok",
                model_id=model_id_var,
                tokens_consumed=tokens,
                # === Phase 1 (T10) ===
                wall_time_ms=int((datetime.now(timezone.utc) - cycle_started_at).total_seconds() * 1000),
                llm_call_ms=llm_call_ms,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                reasoning_tokens=reasoning_tokens,
                cache_hit_rate=hit_rate,
            )
        )
        await session.commit()

    logger.info(f"Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)")

    # === R2-8a: Build CycleRenderContext + render + record stats ===
    cycle_ended_at = datetime.now(timezone.utc)
    if console is not None:
        from src.cli.display import CycleRenderContext
        ctx = CycleRenderContext(
            cycle_id=cycle_id, trigger_type=trigger_type,
            trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
            messages=result.new_messages(), final_text=result.output,
            cycle_tokens=tokens, stats=stats, cache_hit_rate=hit_rate,
            cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
            forensic_reason=None,
        )
        console.print(format_cycle_output(ctx))
    stats.record_cycle(tokens, cycle_ended_at)

    return result


# --- Phase 5: Service construction ---

_DEFAULT_PRECISION = {
    "BTC/USDT:USDT": 3,
    "ETH/USDT:USDT": 2,
}


def build_services(
    result: WizardResult,
    engine,
    session_id: str,
    sc: SessionConsole,
    settings: Settings,
):
    """Build exchange, deps, agent, budget from WizardResult."""
    from src.services.price_alert import PriceAlertService

    # Exchange
    if result.exchange_type == "simulated":
        from src.integrations.exchange.simulated import SimulatedExchange
        precision = {result.symbol: _DEFAULT_PRECISION.get(result.symbol, 3)}
        config = ExchangeConfig(
            name="simulated", fee_rate=result.fee_rate, precision=precision,
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
    max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
    runtime_config = RuntimeConfig(wake_max_minutes=max_wake)
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

    # News service — all upstream sources are keyless (CoinDesk, FGI, ForexFactory, OKX).
    news_service = None
    if settings.news.enabled:
        from src.integrations.news.service import NewsService
        news_service = NewsService()
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
        metrics=metrics_service,
        news=news_service,
        macro=macro_service,
        crypto_etf=crypto_etf_service,
        onchain=onchain_service,
        wake_min_minutes=1,
        wake_max_minutes=max_wake,
    )

    # R2-5 PR #34 I-A: prompt range (RuntimeConfig) and clamp authority (TradingDeps)
    # must agree by construction — fail-loud at startup if a future refactor breaks one
    assert deps.wake_max_minutes == runtime_config.wake_max_minutes, (
        f"R2-5 drift: prompt range {runtime_config.wake_max_minutes} vs "
        f"clamp {deps.wake_max_minutes} must match"
    )

    # Alert service
    if result.alert_enabled:
        alert_service = PriceAlertService(
            symbol=result.symbol,
            window_minutes=result.alert_window_min,
            threshold_pct=result.alert_threshold_pct,
        )
        exchange.set_alert_service(alert_service)
        sc.print(
            f"Alerts: ON ({result.alert_window_min}min / "
            f"{result.alert_threshold_pct}%)"
        )
    else:
        sc.print("Alerts: OFF")

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

    result, session_id = await select_or_create_session(
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

    # ── Phase 5: Build services ──
    exchange, deps, agent, budget, stats = build_services(
        result, engine, session_id, sc, settings,
    )

    # ── Phase 6: Main loop ──
    shutdown_event = asyncio.Event()

    def _signal_handler():
        sc.print("\n[yellow]Shutting down gracefully...[/]")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    async def on_tick(trigger_type: str, context=None):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(
                agent, deps, trigger_type, budget, engine,
                context, model=result.model, console=sc, stats=stats,
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
    deps.set_next_wake_fn = lambda minutes: scheduler.set_next_interval(minutes * 60)

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
