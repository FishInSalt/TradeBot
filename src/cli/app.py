from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, update as sql_update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.memory import MemoryService
from src.agent.trader import TradingDeps, create_trader_agent
from src.cli.approval import ApprovalGate
from pydantic_ai.messages import (
    ModelRequest, ModelResponse,
    ToolCallPart, ToolReturnPart,
)
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
from src.cli.display import (
    display_metrics, format_cycle_output,
    is_tool_error, resolve_tool_display,
)
from src.cli.logging_config import SessionConsole, setup_session_logging, setup_system_logging
from src.config import ExchangeConfig, Settings, load_settings, load_trader_config
from src.integrations.exchange.okx import OKXExchange
from src.integrations.market_data import MarketDataService
from src.scheduler.scheduler import Scheduler
from src.services.technical import TechnicalAnalysisService
from src.storage.database import get_session, init_db
from src.storage.models import DecisionLog, Session, TradeAction
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


# Iter 4 §3.2 + R2-4 spec §5.3 — DecisionLog 派生类型分类常量
# R2-4 拆 ADJUST_ACTIONS 为 4 子集 (sim4-issues §P0-3)
# 派生优先级（业务直觉默认）: protect > entry_order > leverage > alert
# trade_actions 留底，未来若数据反证可仅重派生历史 decision_logs.decision，无需 schema 演进
PROTECT_ACTIONS = frozenset({
    "set_stop_loss",
    "set_take_profit",
})
ENTRY_ORDER_ACTIONS = frozenset({
    "place_limit_order",
    "cancel_order",
})
LEVERAGE_ACTIONS = frozenset({
    "adjust_leverage",
})
ALERT_ACTIONS = frozenset({
    "set_price_alert",
    "add_price_level_alert",
    "cancel_price_level_alert",
})

# 兜底 union — 用于 drift guard 测试 (T5 t11) / 任何"任意 adjust"判断
# set_next_wake 单独归 hold（spec §C5）；open_position / close_position 单独分类
ADJUST_ACTIONS = (
    PROTECT_ACTIONS | ENTRY_ORDER_ACTIONS | LEVERAGE_ACTIONS | ALERT_ACTIONS
)


async def _derive_decision_from_actions(
    session: AsyncSession,
    session_id: str,
    cycle_id: str,
) -> str:
    """从 trade_actions 反查 cycle 内 actions，按优先级派生 decision 类型。

    优先级（高 → 低）:
        open_long > open_short > close
        > adjust_protect > adjust_entry_order > adjust_leverage > adjust_alert
        > hold

    返回 9 类 enum: open_long / open_short / close /
    adjust_protect / adjust_entry_order / adjust_leverage / adjust_alert /
    hold / derive_error

    R2-4 spec §5.3 — 拆 'adjust' 单值为 4 子类（sim4-issues §P0-3）。
    DB 故障 fallback: derive_error（独立 enum，spec §8.1）。
    """
    try:
        rows = (await session.execute(
            select(TradeAction).where(
                TradeAction.session_id == session_id,
                TradeAction.cycle_id == cycle_id,
            ).order_by(TradeAction.id)  # first-match 语义稳定
        )).scalars().all()
    except (SQLAlchemyError, OSError):
        logger.exception(
            f"derive_decision SELECT failed for cycle {cycle_id}; falling back to 'derive_error'"
        )
        return "derive_error"

    actions = {a.action for a in rows}

    # 1. 开仓（最高优先级）
    for a in rows:
        if a.action == "open_position":
            if a.side not in ("long", "short"):
                logger.warning(
                    f"open_position with unexpected side={a.side!r} "
                    f"in cycle {cycle_id}; skipping this row, downstream "
                    f"classification (close/adjust/hold) takes over"
                )
                continue  # 跳过此 row，循环继续
            return f"open_{a.side}"  # open_long / open_short

    # 2. 平仓
    if "close_position" in actions:
        return "close"

    # 3. adjust 子类（按事件重要性优先级）
    if actions & PROTECT_ACTIONS:
        return "adjust_protect"
    if actions & ENTRY_ORDER_ACTIONS:
        return "adjust_entry_order"
    if actions & LEVERAGE_ACTIONS:
        return "adjust_leverage"
    if actions & ALERT_ACTIONS:
        return "adjust_alert"

    # 4. hold（无任何 ADJUST_ACTIONS，含 cycle 仅有 set_next_wake 的情况）
    return "hold"


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
):
    if budget.exhausted:
        logger.warning("Daily LLM token budget exhausted, skipping cycle")
        return None

    cycle_id = str(uuid.uuid4())[:8]
    deps.cycle_id = cycle_id   # propagate to ToolCallRecorder via ctx.deps (§3.4 of spec)
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

    memory_context = await deps.memory.format_for_prompt()
    if memory_context != "No relevant memories.":
        prompt += f"\n\nYour memories:\n{memory_context}"

    # LLM call with exponential backoff retry
    run_kwargs = {"deps": deps}
    if model is not None:
        run_kwargs["model"] = model

    result = None
    for attempt in range(3):
        try:
            result = await agent.run(
                prompt,
                usage_limits=USAGE_LIMITS_PER_CYCLE,
                **run_kwargs,
            )
            break
        except UsageLimitExceeded as e:
            # 病理状态（LLM 死循环 / runaway tools），不重试，写 forensic trace。
            # 注：ToolCallRecorder capability 已在 agent.run 内部独立 session 写完
            # 任何已成功 tool 调用的 tool_calls 行（不需要本路径协调 rollback）。
            logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
            async with get_session(engine) as session:
                decision = await _derive_decision_from_actions(
                    session, deps.session_id, cycle_id
                )
                session.add(DecisionLog(
                    session_id=deps.session_id,
                    cycle_id=cycle_id,
                    trigger_type=trigger_type,
                    decision=decision,                    # spec §G2: 派生而非语义冲突
                    status="usage_limit_exceeded",        # spec §G2: 双字段方案
                    reasoning=str(e)[:4000],              # spec §G2: cap 500 → 4000
                    model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                    tokens_used=0,  # spec §3.1 #3: UsageLimitExceeded 不携带 partial usage
                ))
                await session.commit()
            return None
        except Exception as e:
            if attempt < 2:
                delay = 2 ** attempt
                logger.warning(f"LLM call attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                logger.error(f"LLM call failed after 3 attempts: {e}")
                return None

    usage = result.usage()
    tokens = usage.total_tokens if usage else 0
    details = (usage.details or {}) if usage else {}

    # reasoning_tokens 由 DeepSeek/OpenAI o-series 等 thinking 模型返回；
    # > 0 即可验证 thinking mode 在本 cycle 真实生效。
    reasoning_tokens = details.get("reasoning_tokens", 0)
    # DeepSeek prompt-cache 命中观测（pre-next-observation §B3 Step 1）：
    # cycle 2+ hit_rate > 0 表明前缀 cache 工作，是 input token 削减最大杠杆。
    cache_hit = details.get("prompt_cache_hit_tokens", 0)
    cache_miss = details.get("prompt_cache_miss_tokens", 0)
    input_total = cache_hit + cache_miss
    hit_rate = (cache_hit / input_total * 100) if input_total > 0 else 0.0

    logger.info(
        f"cycle {cycle_id} tokens: total={tokens} reasoning={reasoning_tokens} "
        f"cache_hit={cache_hit} cache_miss={cache_miss} rate={hit_rate:.1f}%"
    )
    budget.record(tokens)

    # === A2: Extract tool calls from message history ===
    tool_calls = []
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
                    tool_calls.append({
                        "tool_name": part.tool_name,
                        "content": content_str,
                        "outcome": outcome,
                        "args": args,
                    })

                    # System log: INFO summary, DEBUG full content
                    icon, summary = resolve_tool_display(
                        part.tool_name, content_str, outcome, args,
                    )
                    logger.info(f"  {icon} {part.tool_name}: {summary}")
                    logger.debug(
                        f"  Tool {part.tool_name} args={args} "
                        f"return={content_str[:500]}"
                    )

    # === Record to database ===
    async with get_session(engine) as session:
        decision = await _derive_decision_from_actions(
            session, deps.session_id, cycle_id
        )
        session.add(
            DecisionLog(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                trigger_type=trigger_type,
                decision=decision,            # spec §G1: 派生而非硬编码
                status="ok",                  # spec §G1: 双字段方案
                reasoning=result.output[:4000],  # spec §G1: cap 500 → 4000
                model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                tokens_used=tokens,
            )
        )
        await session.commit()

    logger.info(f"Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)")

    # === A2: Display formatted cycle output ===
    if console is not None:
        output = format_cycle_output(
            cycle_id=cycle_id,
            trigger_type=trigger_type,
            tool_calls=tool_calls,
            agent_output=result.output,
            tokens_used=tokens,
            budget_remaining=budget.remaining,
        )
        console.print(output)

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

    agent = create_trader_agent(model=result.model, persona_config=result.persona)

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

    return exchange, deps, agent, budget


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
    exchange, deps, agent, budget = build_services(
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
                context, model=result.model, console=sc,
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

    # R4: dynamic wake interval
    max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = max_wake
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
