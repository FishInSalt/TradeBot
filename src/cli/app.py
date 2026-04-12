from __future__ import annotations

import asyncio
import json
import logging
import signal
import uuid
from pathlib import Path

from sqlalchemy import select

from src.agent.memory import MemoryService
from src.agent.trader import TradingDeps, create_trader_agent
from src.cli.approval import ApprovalGate
from src.cli.display import display_metrics
from src.cli.logging_config import setup_system_logging, setup_session_logging
from src.config import load_settings, load_trader_config
from src.integrations.exchange.okx import OKXExchange
from src.integrations.market_data import MarketDataService
from src.scheduler.scheduler import Scheduler
from src.services.metrics import MetricsService
from src.services.technical import TechnicalAnalysisService
from src.storage.database import get_session, init_db
from src.storage.models import DecisionLog, Session, TradeAction
from src.integrations.exchange.base import FillEvent

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
    prompt = (
        f"You have been woken up by a {trigger_type} trigger.\n"
        f"Trading pair: {deps.symbol} | Timeframe: {deps.timeframe}\n"
        "Analyze the current market, check your positions, and decide what to do.\n"
        "Use your tools to gather data before making a decision."
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
            result = await agent.run(prompt, **run_kwargs)
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
                model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                tokens_used=tokens,
            )
        )
        await session.commit()

    logger.info(f"Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)")
    if console is not None:
        console.print(f"\n[bold cyan]Agent:[/]\n{result.output}\n")
    return result


async def run(
    settings_path: Path = Path("config/settings.yaml"),
    trader_path: Path = Path("config/trader.yaml"),
    model_id: str | None = None,
    debug: bool = False,
):
    # Phase 1: System logging (before session_id is known)
    log_dir = settings_path.resolve().parent.parent / "logs"
    pre_console = setup_system_logging(debug, log_dir)

    pre_console.print("[bold green]TradeBot Phase 1b — Starting...[/]\n")

    settings = load_settings(settings_path)
    trader_config = load_trader_config(trader_path)

    pre_console.print(f"Symbol: {settings.trading.symbol} | Timeframe: {settings.trading.timeframe}")
    pre_console.print(f"Approval: {'ON' if settings.approval.enabled else 'OFF'}")
    pre_console.print(
        f"Persona: {trader_config.persona.risk_tolerance} / {trader_config.persona.trading_style}\n"
    )

    # --- Model selection via ModelManager ---
    from src.services.model_manager import ModelManager

    project_root = settings_path.resolve().parent.parent
    model_manager = ModelManager(config_path=project_root / "config" / "models.json")
    existing_models = model_manager.load_models()

    selected_model = None  # pydantic-ai Model object
    selected_config = None

    if model_id:
        selected_config = model_manager.get_model_by_id(model_id, existing_models)
        if selected_config is None:
            pre_console.print(f"[red]Model '{model_id}' not found in models.json[/]")
            return
        selected_model = model_manager.create_model(selected_config)
        pre_console.print(f"Model: {selected_config.id} ({selected_config.provider}:{selected_config.model})")
    elif existing_models:
        pre_console.print("[bold]Available models:[/]")
        for i, m in enumerate(existing_models):
            pre_console.print(f"  {i + 1}. {m.id} ({m.provider}:{m.model})")
        pre_console.print(f"  {len(existing_models) + 1}. Add new model")

        choice = input(f"\nSelect model [1-{len(existing_models) + 1}]: ").strip()
        try:
            idx = int(choice) - 1
        except ValueError:
            idx = 0

        if 0 <= idx < len(existing_models):
            selected_config = existing_models[idx]
            selected_model = model_manager.create_model(selected_config)
        else:
            selected_config, selected_model = await _interactive_add_model(
                model_manager, existing_models, pre_console
            )
    else:
        pre_console.print("[yellow]No models configured. Let's add one.[/]\n")
        selected_config, selected_model = await _interactive_add_model(
            model_manager, existing_models, pre_console
        )

    if selected_model is None:
        pre_console.print("[red]No model selected. Exiting.[/]")
        return

    # 测试 API 连通性
    pre_console.print(f"\nTesting API connectivity for {selected_config.id}...")
    success, error = await model_manager.test_connectivity(selected_model)
    if success:
        pre_console.print("[green]API connection OK[/]")
    else:
        pre_console.print(f"[red]API connection failed: {error}[/]")
        skip = input("Skip test and continue anyway? [y/N]: ").strip().lower()
        if skip != "y":
            return

    if selected_config not in existing_models:
        existing_models.append(selected_config)
        model_manager.save_models(existing_models)
        pre_console.print(f"[green]Model '{selected_config.id}' saved to models.json[/]")

    pre_console.print(f"Model: {selected_config.id} ({selected_config.provider}:{selected_config.model})\n")

    # Resolve database path relative to project root (where config lives)
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
                model_config=json.dumps({"id": selected_config.id, "provider": selected_config.provider, "model": selected_config.model}),
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

    # Phase 2: Session logging (session_id now known)
    sc = setup_session_logging(session_id, log_dir)

    if settings.exchange.name == "simulated":
        from src.integrations.exchange.simulated import SimulatedExchange
        exchange = SimulatedExchange(
            config=settings.exchange,
            db_engine=engine,
            session_id=session_id,
            symbol=settings.trading.symbol,
        )
        sc.print("Exchange: simulated (local matching)")
    else:
        exchange = OKXExchange(
            api_key=settings.exchange.api_key,
            secret=settings.exchange.secret,
            password=settings.exchange.password,
            symbol=settings.trading.symbol,
        )
        sc.print(f"Exchange: {settings.exchange.name} (REAL account)")
    market_data = MarketDataService(exchange)
    technical = TechnicalAnalysisService()
    memory = MemoryService(engine, session_id=session_id)
    metrics_service = MetricsService(initial_balance=trading_session.initial_balance)
    budget = TokenBudget(daily_max=settings.llm_budget.daily_max_tokens)
    approval_gate = ApprovalGate(
        enabled=settings.approval.enabled,
        timeout_seconds=settings.approval.timeout_seconds,
        console=sc,
    )

    agent = create_trader_agent(model=selected_model, persona_config=trader_config.persona)

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

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        sc.print("\n[yellow]Shutting down gracefully...[/]")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    # --- Price alert setup ---
    from src.services.price_alert import PriceAlertService

    alert_service = None
    if settings.alerts.enabled:
        sc.print("\n[bold]Price alert settings:[/]")
        try:
            window_input = input(f"  Window (minutes) [{settings.alerts.window_minutes}]: ").strip()
            threshold_input = input(f"  Threshold (%) [{settings.alerts.threshold_pct}]: ").strip()
            cooldown_input = input(f"  Cooldown (minutes) [{settings.alerts.cooldown_minutes}]: ").strip()
            window = int(window_input) if window_input else settings.alerts.window_minutes
            threshold = float(threshold_input) if threshold_input else settings.alerts.threshold_pct
            cooldown = int(cooldown_input) if cooldown_input else settings.alerts.cooldown_minutes
            alert_service = PriceAlertService(
                symbol=settings.trading.symbol,
                window_minutes=window,
                threshold_pct=threshold,
                cooldown_minutes=cooldown,
            )
            sc.print(f"  Price alerts: ON (threshold={threshold}%, window={window}min, cooldown={cooldown}min)")
        except (ValueError, TypeError) as e:
            sc.print(f"[yellow]Invalid alert settings ({e}), using defaults[/]")
            alert_service = PriceAlertService(
                symbol=settings.trading.symbol,
                window_minutes=settings.alerts.window_minutes,
                threshold_pct=settings.alerts.threshold_pct,
                cooldown_minutes=settings.alerts.cooldown_minutes,
            )
            sc.print(
                f"  Price alerts: ON (threshold={settings.alerts.threshold_pct}%, "
                f"window={settings.alerts.window_minutes}min, cooldown={settings.alerts.cooldown_minutes}min)"
            )
        exchange.set_alert_service(alert_service)
    else:
        sc.print("Price alerts: OFF")

    handle_fill = None

    async def on_tick(trigger_type: str, context=None):
        if shutdown_event.is_set():
            return
        try:
            await run_agent_cycle(agent, deps, trigger_type, budget, engine, context, model=selected_model, console=sc)
        except Exception:
            logger.exception("Agent cycle failed")
        finally:
            # drain_pending_fills: SimulatedExchange 返回市价单 fill，OKX 返回 []（无害）
            if handle_fill is not None:
                for fill in exchange.drain_pending_fills():
                    try:
                        await handle_fill(fill)
                    except Exception:
                        logger.exception("Fill handler failed for order %s", fill.order_id)

    interval = settings.scheduler.interval_minutes * 60
    scheduler = Scheduler(interval_seconds=interval, callback=on_tick)

    # --- Fill handler registration (unified for both exchange types) ---
    def _create_fill_handler(sched, eng, sid):
        async def handle_fill(event: FillEvent):
            try:
                await _record_action_from_fill(eng, sid, event)
            except Exception:
                logger.warning("Failed to record fill event", exc_info=True)
            finally:
                await sched.trigger("conditional", context=event)
        return handle_fill

    handle_fill = _create_fill_handler(scheduler, engine, session_id)
    exchange.on_fill(handle_fill)

    # --- Alert handler registration ---
    if settings.alerts.enabled:
        async def handle_alert(alert_info):
            await scheduler.trigger("alert", context=alert_info)

        exchange.on_alert(handle_alert)

    # --- Start exchange (both simulated and OKX) ---
    await exchange.start()  # SimulatedExchange: 恢复状态 + 撮合循环; OKXExchange: WebSocket loops

    # Show initial metrics (after start so simulated mode has restored state)
    positions = await exchange.fetch_positions(settings.trading.symbol)
    pos_str = f"{positions[0].side} {positions[0].contracts}" if positions else "none"
    metrics = await metrics_service.compute(engine, session_id, current_position=pos_str)
    display_metrics(metrics, console=sc)

    sc.print(
        f"\n[bold]Scheduler: every {settings.scheduler.interval_minutes} min[/]"
    )
    sc.print(f"[bold]LLM Budget: {settings.llm_budget.daily_max_tokens:,} tokens/day[/]")
    sc.print("[dim]Press Ctrl+C to stop[/]\n")

    scheduler_task = asyncio.create_task(scheduler.start())
    await shutdown_event.wait()

    scheduler.stop()
    await scheduler_task
    await exchange.close()
    sc.close()
    pre_console.print("[green]TradeBot stopped.[/]")


async def _interactive_add_model(model_manager, existing_models, console):
    """交互式添加新模型。返回 (ModelConfig, pydantic-ai Model) 或 (None, None)。"""
    from src.services.model_manager import ModelConfig

    console.print("Supported providers: anthropic, openai, google-gla, groq")
    provider = input("Provider: ").strip()
    model_name = input("Model name (e.g. claude-opus-4-6, gpt-4o): ").strip()
    api_key = input("API key: ").strip()
    base_url_input = input("Base URL (press Enter for default): ").strip()
    base_url = base_url_input if base_url_input else None
    model_id = input("Friendly ID (e.g. claude-opus, gpt4o): ").strip()

    if not all([provider, model_name, api_key, model_id]):
        console.print("[red]All fields except base_url are required.[/]")
        return None, None

    config = ModelConfig(
        id=model_id,
        provider=provider,
        model=model_name,
        api_key=api_key,
        base_url=base_url,
    )

    try:
        model = model_manager.create_model(config)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        return None, None

    return config, model
