from __future__ import annotations

import asyncio
import json
import logging
import signal
import uuid
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from sqlalchemy import select

from src.agent.memory import MemoryService
from src.agent.trader import TradingDeps, create_trader_agent
from src.cli.approval import ApprovalGate
from src.cli.display import display_metrics
from src.config import load_settings, load_trader_config
from src.integrations.exchange.okx import OKXExchange
from src.integrations.market_data import MarketDataService
from src.scheduler.scheduler import Scheduler
from src.services.llm_router import LLMRouter
from src.services.metrics import MetricsService
from src.services.technical import TechnicalAnalysisService
from src.storage.database import get_session, init_db
from src.storage.models import DecisionLog, Session, TradeRecord

console = Console()
logger = logging.getLogger(__name__)


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


async def run_agent_cycle(
    agent,
    deps: TradingDeps,
    trigger_type: str,
    budget: TokenBudget,
    engine,
    context=None,
):
    if budget.exhausted:
        logger.warning("Daily LLM token budget exhausted, skipping cycle")
        return None

    cycle_id = str(uuid.uuid4())[:8]
    prompt = (
        f"You have been woken up by a {trigger_type} trigger.\n"
        f"Trading pair: {deps.symbol} | Timeframe: {deps.timeframe}\n"
        "Analyze the current market, check your positions, and decide what to do.\n"
        "Use your tools to gather data before making a decision."
    )
    if context is not None and hasattr(context, "trigger_reason"):
        prompt += (
            f"\n\nIMPORTANT EVENT: {context.trigger_reason} triggered "
            f"— {context.symbol} {context.amount} @ {context.fill_price}"
        )

    memory_context = await deps.memory.format_for_prompt()
    if memory_context != "No relevant memories.":
        prompt += f"\n\nYour memories:\n{memory_context}"

    # LLM call with exponential backoff retry
    result = None
    for attempt in range(3):
        try:
            result = await agent.run(prompt, deps=deps)
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

    async with get_session(engine) as session:
        session.add(
            DecisionLog(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                trigger_type=trigger_type,
                decision="completed",
                reasoning=result.output[:500],
                model_used=str(agent.model),
                tokens_used=tokens,
            )
        )
        await session.commit()

    logger.info(f"Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)")
    console.print(f"\n[bold cyan]Agent:[/]\n{result.output}\n")
    return result


async def run(
    settings_path: Path = Path("config/settings.yaml"),
    trader_path: Path = Path("config/trader.yaml"),
):
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )

    console.print("[bold green]TradeBot Phase 1a — Starting...[/]\n")

    settings = load_settings(settings_path)
    trader_config = load_trader_config(trader_path)

    console.print(f"Symbol: {settings.trading.symbol} | Timeframe: {settings.trading.timeframe}")
    console.print(f"Approval: {'ON' if settings.approval.enabled else 'OFF'}")
    console.print(
        f"Persona: {trader_config.persona.risk_tolerance} / {trader_config.persona.trading_style}\n"
    )

    # Resolve database path relative to project root (where config lives)
    project_root = settings_path.resolve().parent.parent
    data_dir = project_root / "data"
    data_dir.mkdir(exist_ok=True)
    db_url = settings.database.url
    if db_url.startswith("sqlite") and ":///" in db_url and not db_url.startswith("sqlite+aiosqlite:////"):
        # Convert relative sqlite path to absolute based on project root
        relative_path = db_url.split(":///", 1)[1]
        absolute_path = project_root / relative_path
        db_url = f"sqlite+aiosqlite:///{absolute_path}"
    engine = await init_db(db_url)

    # Get or create default session
    async with get_session(engine) as db_sess:
        stmt = select(Session).where(Session.name == "default")
        result = await db_sess.execute(stmt)
        trading_session = result.scalar_one_or_none()
        if trading_session is None:
            trading_session = Session(
                name="default",
                symbol=settings.trading.symbol,
                persona_config=json.dumps(trader_config.persona.model_dump()),
                model_config=json.dumps(settings.models.model_dump()),
                initial_balance=settings.trading.initial_balance_usdt,
                status="active",
            )
            db_sess.add(trading_session)
            await db_sess.commit()
            await db_sess.refresh(trading_session)
            logger.info(f"Created session: {trading_session.id}")
        else:
            logger.info(f"Resumed session: {trading_session.id}")
    session_id = trading_session.id

    if settings.exchange.name == "simulated":
        from src.integrations.exchange.simulated import SimulatedExchange
        exchange = SimulatedExchange(
            config=settings.exchange,
            db_engine=engine,
            session_id=session_id,
            symbol=settings.trading.symbol,
        )
        console.print("Exchange: simulated (local matching)")
    else:
        exchange = OKXExchange(
            api_key=settings.exchange.api_key,
            secret=settings.exchange.secret,
            password=settings.exchange.password,
        )
        console.print(f"Exchange: {settings.exchange.name} (REAL account)")
    market_data = MarketDataService(exchange)
    technical = TechnicalAnalysisService()
    llm_router = LLMRouter(settings.models)
    memory = MemoryService(engine, session_id=session_id)
    metrics_service = MetricsService(initial_balance=trading_session.initial_balance)
    budget = TokenBudget(daily_max=settings.llm_budget.daily_max_tokens)
    approval_gate = ApprovalGate(
        enabled=settings.approval.enabled,
        timeout_seconds=settings.approval.timeout_seconds,
    )

    model = llm_router.resolve("trade_decision")
    agent = create_trader_agent(model=model, persona_config=trader_config.persona)

    deps = TradingDeps(
        symbol=settings.trading.symbol,
        timeframe=settings.trading.timeframe,
        market_data=market_data,
        exchange=exchange,
        technical=technical,
        memory=memory,
        session_id=session_id,
        db_engine=engine,
        approval_gate=approval_gate,
        approval_enabled=settings.approval.enabled,
    )

    # Show initial metrics with current position
    async with get_session(engine) as session:
        result = await session.execute(
            select(TradeRecord)
            .where(TradeRecord.session_id == session_id)
            .where(TradeRecord.status == "closed")
        )
        trades = list(result.scalars().all())
    positions = await exchange.fetch_positions(settings.trading.symbol)
    if positions:
        pos_str = f"{positions[0].side} {positions[0].contracts}"
    else:
        pos_str = "none"
    display_metrics(metrics_service.compute_from_trades(trades, current_position=pos_str))

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        console.print("\n[yellow]Shutting down gracefully...[/]")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    async def on_tick(trigger_type: str, context=None):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(agent, deps, trigger_type, budget, engine, context)
        except Exception:
            logger.exception("Agent cycle failed")

    interval = settings.scheduler.interval_minutes * 60
    scheduler = Scheduler(interval_seconds=interval, callback=on_tick)

    # Register fill handler for simulated exchange
    if settings.exchange.name == "simulated":
        from src.integrations.exchange.simulated import FillEvent

        def _create_fill_handler(sched):
            async def handle_fill(event: FillEvent):
                try:
                    pass  # Agent layer recording — out of scope for this phase
                finally:
                    await sched.trigger("conditional", context=event)
            return handle_fill

        exchange.on_fill(_create_fill_handler(scheduler))

    # Start exchange (simulated needs async start for WebSocket + state restore)
    if settings.exchange.name == "simulated":
        await exchange.start()

    console.print(
        f"\n[bold]Scheduler: every {settings.scheduler.interval_minutes} min[/]"
    )
    console.print(f"[bold]LLM Budget: {settings.llm_budget.daily_max_tokens:,} tokens/day[/]")
    console.print("[dim]Press Ctrl+C to stop[/]\n")

    scheduler_task = asyncio.create_task(scheduler.start())
    await shutdown_event.wait()

    scheduler.stop()
    await scheduler_task
    await exchange.close()
    console.print("[green]TradeBot stopped.[/]")
