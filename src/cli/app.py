from __future__ import annotations

import asyncio
import logging
import signal
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update as sql_update

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
# 正常 cycle ~10 tool calls / ~5 LLM requests，阈值留 5x buffer。
# 观察期 W1 末校准（实测中位数 + safety buffer）。
USAGE_LIMITS_PER_CYCLE = UsageLimits(
    request_limit=50,            # = pydantic-ai default，显式传防 1.79+ 默认变化
    tool_calls_limit=50,
    total_tokens_limit=300_000,  # 单 cycle 上限；外层 daily TokenBudget 是日累积
)


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
        except Exception as e:
            if attempt < 2:
                delay = 2 ** attempt
                logger.warning(f"LLM call attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                logger.error(f"LLM call failed after 3 attempts: {e}")
                return None

    tokens = result.usage().total_tokens if result.usage() else 0
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
        session.add(
            DecisionLog(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                trigger_type=trigger_type,
                decision="completed",
                reasoning=result.output[:500],
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
