"""Iter 5 §3.1 — UsageLimits + UsageLimitExceeded forensic path tests.

R2-7 update (T5): legacy helper `_make_deps_and_engine` removed (its AsyncMock
exchange/market_data triggered capture-path RuntimeWarnings); all tests now
use `_make_deps_engine_with_capture_mocks` (real Balance/Ticker fixtures so
`_capture_state_snapshot` succeeds before the retry loop).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import models
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, AgentCycle

models.ALLOW_MODEL_REQUESTS = False


def _mock_usage_legacy(total_tokens: int = 100):
    """T23: legacy `MagicMock(total_tokens=N, details=None)` 替换 helper.

    Phase 1 起 cli/app.py 写 8 字段需 input/output/cache_read/cache_write_tokens
    具体 int 值；inline MagicMock 会让 cli/app.py 取到 auto-MagicMock 写入失败.
    """
    u = MagicMock()
    u.total_tokens = total_tokens
    u.input_tokens = total_tokens
    u.output_tokens = 0
    u.cache_read_tokens = 0
    u.cache_write_tokens = 0
    u.details = None
    return u


async def _make_deps_engine_with_capture_mocks(session_id: str = "sess-wp"):
    """Helper: TradingDeps + engine wired so cycle_capture helpers don't blow up.

    Adds AsyncMock returns for fetch_positions / fetch_balance / fetch_open_orders
    / get_price_level_alerts / market_data.get_ticker — needed because
    _capture_state_snapshot is called BEFORE the retry loop and must succeed
    even on the forensic path. Best-effort capture means errors get swallowed,
    but mocked happy-path returns make `state_snapshot is not None` true.
    """
    from src.agent.trader import TradingDeps
    from src.integrations.exchange.base import Balance, Ticker

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="iter-w2r2-7"))
        await db.commit()

    exchange = MagicMock()
    exchange.fetch_positions = AsyncMock(return_value=[])
    exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=10000.0, used_usdt=0.0,
    ))
    exchange.fetch_open_orders = AsyncMock(return_value=[])
    exchange.get_price_level_alerts = MagicMock(return_value=[])

    market_data = MagicMock()
    market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=75000.0, bid=74999.0, ask=75001.0,
        high=75500.0, low=74500.0, base_volume=1000.0, timestamp=1746098096000,
    ))

    deps = TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=market_data,
        exchange=exchange,
        technical=MagicMock(),
        memory=AsyncMock(format_for_prompt=AsyncMock(return_value="No relevant memories.")),
        session_id=session_id,
        db_engine=engine,
    )
    return deps, engine


async def test_usage_limits_passed_to_agent_run(monkeypatch):
    """T1: run_agent_cycle 调用 agent.run 时 kwargs 含 usage_limits 且 == USAGE_LIMITS_PER_CYCLE。"""
    from src.cli.app import USAGE_LIMITS_PER_CYCLE, TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-iter5")
    budget = TokenBudget(daily_max=500_000)

    captured_kwargs = {}

    async def mock_run(prompt, **kwargs):
        captured_kwargs.update(kwargs)
        result = MagicMock()
        result.usage = lambda: _mock_usage_legacy(100)
        result.new_messages = lambda: []
        result.output = "test output"
        return result

    mock_agent = MagicMock()
    mock_agent.run = mock_run
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent,
        deps=deps,
        trigger_type="scheduled",
        budget=budget,
        engine=engine,
    )

    assert "usage_limits" in captured_kwargs, (
        f"agent.run 未收到 usage_limits 参数, captured: {list(captured_kwargs.keys())}"
    )
    assert captured_kwargs["usage_limits"] is USAGE_LIMITS_PER_CYCLE, (
        f"usage_limits 不是 USAGE_LIMITS_PER_CYCLE 常量"
    )


async def test_usage_limit_exceeded_writes_forensic_agent_cycle():
    """T2: UsageLimitExceeded 触发时写 agent_cycles 1 行 + 函数返回 None.

    R2-7 §6.5: forensic path → reasoning=None, decision=None,
    execution_status='usage_limit_exceeded', tokens_consumed=0.
    """
    from pydantic_ai.exceptions import UsageLimitExceeded

    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-t2")
    budget = TokenBudget(daily_max=500_000)

    async def boom(prompt, **kwargs):
        raise UsageLimitExceeded("test reason")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    result = await run_agent_cycle(
        agent=mock_agent,
        deps=deps,
        trigger_type="scheduled",
        budget=budget,
        engine=engine,
    )

    assert result is None, "病理 cycle 应返回 None"

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(AgentCycle).where(AgentCycle.execution_status == "usage_limit_exceeded")
        )).scalars().all()

    assert len(rows) == 1, f"应写 1 行 AgentCycle execution_status='usage_limit_exceeded'，实际 {len(rows)} 行"
    row = rows[0]
    assert row.session_id == "sess-t2"
    assert row.execution_status == "usage_limit_exceeded"
    # R2-7 §6.5: forensic 路径 reasoning + decision 都 NULL（旧 enum 'hold' 派生已删）
    assert row.reasoning is None, f"forensic reasoning 应 None，实际 {row.reasoning!r}"
    assert row.decision is None, f"forensic decision 应 None，实际 {row.decision!r}"
    assert row.tokens_consumed == 0  # spec §3.1 #3 设计取舍


async def test_usage_limit_exceeded_does_not_retry():
    """T3: UsageLimitExceeded 不进 range(3) 重试，agent.run 仅被调 1 次。"""
    from pydantic_ai.exceptions import UsageLimitExceeded

    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-t3")
    budget = TokenBudget(daily_max=500_000)

    call_count = {"n": 0}

    async def boom(prompt, **kwargs):
        call_count["n"] += 1
        raise UsageLimitExceeded("test")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    assert call_count["n"] == 1, (
        f"agent.run 应仅被调 1 次（不重试），实际 {call_count['n']} 次"
    )


async def test_generic_exception_still_retries_3_times(monkeypatch):
    """T4: 通用 Exception 不被 UsageLimitExceeded 路径误捕，仍走 3 次重试。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-t4")
    budget = TokenBudget(daily_max=500_000)

    # 跳过实际 sleep 加速测试
    async def fast_sleep(_):
        pass
    monkeypatch.setattr("asyncio.sleep", fast_sleep)

    call_count = {"n": 0}

    async def flaky(prompt, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError("transient network error")
        result = MagicMock()
        result.usage = lambda: _mock_usage_legacy(100)
        result.new_messages = lambda: []
        result.output = "recovered"
        return result

    mock_agent = MagicMock()
    mock_agent.run = flaky
    mock_agent.model = "test-model"

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    assert call_count["n"] == 3, f"应重试 3 次，实际 {call_count['n']}"
    assert result is not None, "第 3 次成功应返回 result"


async def test_t9_success_path_writes_execution_status_ok_and_full_decision():
    """T9: 成功路径写 execution_status='ok' / decision=result.output 全文 (R2-7 无 cap).

    R2-7 §6.4: decision 是 message free-form Text（旧 'hold' enum 派生已删）；
    reasoning 是 ThinkingPart content（new_messages=[] 无 ThinkingPart 时 reasoning=None）。
    """
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-t9")
    budget = TokenBudget(daily_max=500_000)

    # 5000-char output: R2-7 不 cap, 应原样写入
    long_output = "x" * 5000

    async def mock_run(prompt, **kwargs):
        result = MagicMock()
        result.usage = lambda: _mock_usage_legacy(100)
        result.new_messages = lambda: []
        result.output = long_output
        return result

    mock_agent = MagicMock()
    mock_agent.run = mock_run
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(AgentCycle).where(AgentCycle.session_id == "sess-t9")
        )).scalars().all()

    assert len(rows) == 1, f"应写 1 行 AgentCycle，实际 {len(rows)}"
    row = rows[0]
    assert row.execution_status == "ok", \
        f"成功路径 execution_status 应 'ok'，实际 {row.execution_status!r}"
    assert row.decision == long_output, \
        f"R2-7 decision 应为 result.output 全文 (no cap)，实际 len={len(row.decision) if row.decision else 0}"
    assert len(row.decision) == 5000, \
        f"decision 不应被截断，期望 5000 chars，实际 {len(row.decision)}"
    # new_messages=[] 故无 ThinkingPart, reasoning 应 None
    assert row.reasoning is None, \
        f"无 ThinkingPart 时 reasoning 应 None，实际 {row.reasoning!r}"


# NOTE: test_t10_forensic_path_derives_from_committed_trade_actions deleted in
# R2-7 (spec §10.3 P10). 派生函数 (trade_actions → DecisionLog.decision) 已
# 在 Task 4 删除；forensic 路径 R2-7 §6.5 写 decision=NULL（不再反查派生），
# T10 验证的逻辑已不存在 → 整体删除（重构成本高 + 收益低，T-WP-2 已覆盖
# forensic null-write 不变量）。


def test_usage_limit_total_tokens_capped_at_200k():
    """T5: USAGE_LIMITS_PER_CYCLE.total_tokens_limit == 200_000.

    pre-next-observation §T1-1c (W2 prep Iter 5) drift guard：W1 实测
    max 141k tokens/cycle，200k 留 ~40% buffer 同时收紧病理 cycle 爆裂上限
    （从 Iter 5 §3.1 引入时的 300k 兜底降下来）。
    """
    from src.cli.app import USAGE_LIMITS_PER_CYCLE
    assert USAGE_LIMITS_PER_CYCLE.total_tokens_limit == 200_000, (
        f"期望 200_000，实际 {USAGE_LIMITS_PER_CYCLE.total_tokens_limit}；"
        "如需调整请同步更新 pre-next-observation §T1-1c 与本测试"
    )


# === R2-7 §6 — Write-path tests (T-WP-1~3) ===
#
# T-WP-1 (success): reasoning=ThinkingPart, decision=result.output (no cap),
# state_snapshot json valid.
# T-WP-2 (forensic): reasoning=None, decision=None, execution_status=
# 'usage_limit_exceeded', state_snapshot still written, tokens_consumed=0.
# T-WP-3 (forensic + trigger_context): trigger_context json valid for
# 'scheduled' triggers (capture happens before retry loop).


# Helper `_make_deps_engine_with_capture_mocks` defined at module top (line 21);
# T5 lifted it from this T-WP-only block to module scope so t1-t9 can reuse it.


async def test_wp_1_success_path_writes_thinking_and_full_decision():
    """T-WP-1: success path → reasoning=thinking text, decision=result.output (no cap),
    state_snapshot is non-null valid JSON."""
    import json as json_mod
    from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart

    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-wp1")
    budget = TokenBudget(daily_max=500_000)

    long_output = "x" * 5000  # > 4000 to verify NO cap
    thinking_content = "step 1: assess regime\nstep 2: decide action"

    async def mock_run(prompt, **kwargs):
        result = MagicMock()
        result.usage = lambda: _mock_usage_legacy(123)
        result.new_messages = lambda: [
            ModelResponse(parts=[
                ThinkingPart(content=thinking_content),
                TextPart(content=long_output),
            ]),
        ]
        result.output = long_output
        return result

    mock_agent = MagicMock()
    mock_agent.run = mock_run
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(AgentCycle).where(AgentCycle.session_id == "sess-wp1")
        )).scalars().all()

    assert len(rows) == 1, f"应写 1 行 AgentCycle，实际 {len(rows)}"
    row = rows[0]
    assert row.reasoning == thinking_content, \
        f"reasoning 应为 ThinkingPart content，实际 {row.reasoning!r}"
    assert row.decision == long_output, \
        f"decision 应为 result.output 全文（无 cap 4000），实际 len={len(row.decision)}"
    assert len(row.decision) == 5000, \
        f"decision 不应被截断，期望 5000 chars，实际 {len(row.decision)}"
    assert row.execution_status == "ok"
    assert row.tokens_consumed == 123
    # state_snapshot is non-null valid JSON
    assert row.state_snapshot is not None
    parsed = json_mod.loads(row.state_snapshot)
    assert "_cycle_id" in parsed


async def test_wp_2_forensic_path_writes_null_reasoning_decision():
    """T-WP-2: UsageLimitExceeded → reasoning=None, decision=None,
    execution_status='usage_limit_exceeded', state_snapshot 仍有
    (capture 在 try 之前)，tokens_consumed=0."""
    import json as json_mod
    from pydantic_ai.exceptions import UsageLimitExceeded

    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-wp2")
    budget = TokenBudget(daily_max=500_000)

    async def boom(prompt, **kwargs):
        raise UsageLimitExceeded("LLM 死循环")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )
    assert result is None

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(AgentCycle).where(AgentCycle.session_id == "sess-wp2")
        )).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.reasoning is None, f"forensic reasoning 应 None，实际 {row.reasoning!r}"
    assert row.decision is None, f"forensic decision 应 None，实际 {row.decision!r}"
    assert row.execution_status == "usage_limit_exceeded"
    assert row.tokens_consumed == 0
    # state_snapshot 仍写 (R2-7 §6.7: capture 在 retry loop 之前完成)
    assert row.state_snapshot is not None
    parsed = json_mod.loads(row.state_snapshot)
    assert "_cycle_id" in parsed


async def test_wp_3_forensic_path_writes_trigger_context():
    """T-WP-3: forensic 路径 trigger_context 仍有 (capture 在 retry loop 之前)."""
    import json as json_mod
    from pydantic_ai.exceptions import UsageLimitExceeded

    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-wp3")
    budget = TokenBudget(daily_max=500_000)

    async def boom(prompt, **kwargs):
        raise UsageLimitExceeded("test")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(AgentCycle).where(AgentCycle.session_id == "sess-wp3")
        )).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.trigger_context is not None, \
        "trigger_context 应有 (scheduled trigger 也写非 None: {'type': 'scheduled_tick'})"
    parsed = json_mod.loads(row.trigger_context)
    assert parsed == {"type": "scheduled_tick"}


# === R2-8a: T-EX retry-exhausted forensic write ===


async def test_usage_limit_exceeded_renders_session_log_placeholder():
    """T-FO-3 (R2-8a 加): UsageLimitExceeded 路径 session log 端到端渲染
    [no decision — usage limit exceeded; partial messages unavailable] 占位 + Cache N/A (forensic)."""
    from pydantic_ai.exceptions import UsageLimitExceeded
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-fo3")
    budget = TokenBudget(daily_max=500_000)

    async def boom(prompt, **kwargs):
        raise UsageLimitExceeded("simulated runaway")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    captured_print = []
    mock_console = MagicMock()
    mock_console.print = lambda s: captured_print.append(s)

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine, console=mock_console,
    )
    assert result is None
    rendered = "\n".join(str(s) for s in captured_print)
    assert "no decision — usage limit exceeded" in rendered, \
        f"未渲染 forensic Decision 占位; rendered={rendered!r}"
    assert "partial messages unavailable" in rendered
    assert "N/A (forensic)" in rendered, "Footer Cache 行未走 forensic 分支"


async def test_retry_exhausted_writes_forensic_agent_cycle():
    """T-EX-1: 3 次重试都失败 → AgentCycle 行 execution_status='retry_exhausted'."""
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.storage.models import AgentCycle
    from sqlalchemy import select

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-tex1")
    budget = TokenBudget(daily_max=500_000)

    call_count = {"n": 0}

    async def boom(prompt, **kwargs):
        call_count["n"] += 1
        raise ConnectionError("simulated network failure")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )
    assert result is None
    assert call_count["n"] == 3, f"应重试 3 次，实际 {call_count['n']}"

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(AgentCycle).where(AgentCycle.execution_status == "retry_exhausted")
        )).scalars().all()
    assert len(rows) == 1, f"应写 1 行 retry_exhausted，实际 {len(rows)}"
    row = rows[0]
    assert row.session_id == "sess-tex1"
    assert row.execution_status == "retry_exhausted"
    assert row.reasoning is None
    assert row.decision is None
    assert row.tokens_consumed == 0


async def test_retry_exhausted_session_log_renders_aborted_placeholder():
    """T-EX-2 (集成版): retry-exhausted 路径 session log 渲染 [cycle aborted ...]."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-tex2")
    budget = TokenBudget(daily_max=500_000)

    async def boom(prompt, **kwargs):
        raise ConnectionError("timeout")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    captured_print = []
    mock_console = MagicMock()
    mock_console.print = lambda s: captured_print.append(s)

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine, console=mock_console,
    )
    assert result is None
    assert any("cycle aborted" in str(s) for s in captured_print), \
        f"未渲染 [cycle aborted] 占位; captured={captured_print!r}"
    assert any("ConnectionError" in str(s) for s in captured_print)
    assert any("timeout" in str(s) for s in captured_print)


async def test_retry_exhausted_records_session_stats():
    """T-EX-3: retry-exhausted 调 stats.record_cycle(0, end_ts) — cycle_count 计入但 total_tokens 不增."""
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.cli.session_state import SessionStats

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-tex3")
    budget = TokenBudget(daily_max=500_000)
    stats = SessionStats()

    async def boom(prompt, **kwargs):
        raise ConnectionError("net err")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine, stats=stats,
    )
    assert stats.cycle_count == 1
    assert stats.total_tokens == 0
    assert stats.last_cycle_ended_at is not None


async def test_retry_exhausted_error_message_truncated_at_200():
    """spec §6.5: error message 超 200 chars → 截断到 200 (forensic placeholder)."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-tex4")
    budget = TokenBudget(daily_max=500_000)

    long_msg = "x" * 500
    async def boom(prompt, **kwargs):
        raise ConnectionError(long_msg)

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    captured = []
    mock_console = MagicMock()
    mock_console.print = lambda s: captured.append(s)

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine, console=mock_console,
    )
    rendered = "\n".join(str(s) for s in captured)
    assert "ConnectionError" in rendered
    assert "x" * 200 in rendered
    assert "x" * 250 not in rendered  # confirms truncation
    assert "..." in rendered, "spec §6.5 T-EX-2 要求截断后追加省略号"
