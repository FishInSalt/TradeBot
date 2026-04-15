# src/cli/session_manager.py
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from sqlalchemy import select, text, update

from src.cli.wizard import WizardResult, run_wizard
from src.config import Settings, TraderConfig
from src.services.model_manager import ModelConfig, ModelManager
from src.storage.database import get_session
from src.storage.models import Session


async def _migrate_session_table(conn) -> None:
    """Check and add R2 columns to sessions table. Idempotent — safe to run repeatedly."""
    result = await conn.execute(text("PRAGMA table_info(sessions)"))
    existing = {row[1] for row in result}
    migrations = [
        ("exchange_type", "TEXT DEFAULT 'simulated'"),
        ("timeframe", "TEXT DEFAULT '15m'"),
        ("scheduler_interval_min", "INTEGER DEFAULT 15"),
        ("approval_enabled", "BOOLEAN DEFAULT 1"),
        ("alert_config", "TEXT"),
        ("fee_rate", "REAL"),
        ("token_budget", "INTEGER DEFAULT 500000"),
        ("last_active_at", "TIMESTAMP"),
    ]
    for col, defn in migrations:
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col} {defn}"))


async def _migrate_trade_actions_table(conn) -> None:
    """Add fee column to trade_actions table. Idempotent."""
    result = await conn.execute(text("PRAGMA table_info(trade_actions)"))
    existing = {row[1] for row in result}
    if "fee" not in existing:
        await conn.execute(text("ALTER TABLE trade_actions ADD COLUMN fee REAL"))


async def _fix_residual_active(conn) -> int:
    """Set any 'active' sessions to 'paused'. Returns count of fixed sessions."""
    result = await conn.execute(
        text("UPDATE sessions SET status = 'paused' WHERE status = 'active'")
    )
    return result.rowcount


async def _list_sessions(engine) -> list[Session]:
    """Return active/paused sessions ordered by last_active_at DESC (NULLs last)."""
    async with get_session(engine) as db_sess:
        result = await db_sess.execute(
            select(Session)
            .where(Session.status.in_(["active", "paused"]))
            .order_by(Session.last_active_at.desc().nulls_last())
        )
        return list(result.scalars().all())


async def _get_position_summary(engine, session_id: str, exchange_type: str) -> str:
    """Get position summary for session list display.
    Sim: query SimPosition table. Real: return em-dash (exchange not connected)."""
    if exchange_type != "simulated":
        return "\u2014"
    from src.storage.models import SimPosition
    async with get_session(engine) as db_sess:
        result = await db_sess.execute(
            select(SimPosition).where(SimPosition.session_id == session_id)
        )
        pos = result.scalar_one_or_none()
    if pos is None:
        return "\u2014"
    return f"{pos.side} {pos.contracts} {pos.symbol.split('/')[0]}"


async def _restore_session(
    engine,
    session_id: str,
    model_manager: ModelManager,
    model_id: str | None,
    console: Console,
    config_dir: Path,
) -> WizardResult | None:
    """Load session config from DB and reconstruct WizardResult. Returns None on cancel."""
    async with get_session(engine) as db_sess:
        result = await db_sess.execute(select(Session).where(Session.id == session_id))
        s = result.scalar_one()

    # Persona
    from src.config import PersonaConfig
    persona = PersonaConfig(**json.loads(s.persona_config)) if s.persona_config else PersonaConfig()

    # Model — resolve from session record
    saved_model_cfg = json.loads(s.model_config) if s.model_config else None
    saved_model_id = saved_model_cfg.get("id") if saved_model_cfg else None

    all_models = model_manager.load_models()
    selected_config = None
    selected_model = None

    # --model flag takes priority
    if model_id:
        selected_config = model_manager.get_model_by_id(model_id, all_models)
        if selected_config is None:
            console.print(f"[yellow]Model '{model_id}' not found, entering selection...[/]")
        else:
            selected_model = model_manager.create_model(selected_config)

    # No --model flag or --model not found — try session's saved model
    if selected_model is None and saved_model_id:
        selected_config = model_manager.get_model_by_id(saved_model_id, all_models)
        if selected_config:
            if Confirm.ask(
                f"  Continue with model [bold]{selected_config.id}[/]?",
                default=True, console=console,
            ):
                selected_model = model_manager.create_model(selected_config)
        else:
            console.print(f"[yellow]Previous model '{saved_model_id}' no longer available[/]")

    # If still no model, run wizard step 3
    if selected_model is None:
        from src.cli.wizard import _step_model
        model_data = await _step_model(model_manager, None, console)
        if model_data is None:
            return None
        selected_config = model_data["model_config"]
        selected_model = model_data["model"]

    # Alert config
    alert_enabled = False
    alert_window = None
    alert_threshold = None
    if s.alert_config:
        alert_data = json.loads(s.alert_config)
        alert_enabled = alert_data.get("enabled", False)
        alert_window = alert_data.get("window")
        alert_threshold = alert_data.get("threshold")

    # Credentials for real exchange
    api_credentials = None
    if s.exchange_type == "okx":
        from src.cli.wizard import _load_credentials
        saved_creds = _load_credentials(config_dir)
        if "okx" in saved_creds:
            api_credentials = saved_creds["okx"]
        else:
            console.print("[yellow]OKX credentials not found — please re-enter[/]")
            from rich.prompt import Prompt as RichPrompt
            api_key = RichPrompt.ask("  API Key", password=True, console=console)
            secret = RichPrompt.ask("  Secret", password=True, console=console)
            password = RichPrompt.ask("  Password", password=True, console=console)
            api_credentials = {"api_key": api_key, "secret": secret, "password": password}
            from src.cli.wizard import _save_credentials
            _save_credentials(config_dir, "okx", api_credentials)

    # Update status to active + persist model choice
    new_model_config = json.dumps({
        "id": selected_config.id,
        "provider": selected_config.provider,
        "model": selected_config.model,
    })
    async with get_session(engine) as db_sess:
        await db_sess.execute(
            update(Session).where(Session.id == session_id).values(
                status="active", model_config=new_model_config,
            )
        )
        await db_sess.commit()

    return WizardResult(
        exchange_type=s.exchange_type,
        fee_rate=s.fee_rate,
        initial_balance=s.initial_balance,
        api_credentials=api_credentials,
        symbol=s.symbol,
        timeframe=s.timeframe,
        model_config=selected_config,
        model=selected_model,
        scheduler_interval_min=s.scheduler_interval_min,
        approval_enabled=s.approval_enabled,
        alert_enabled=alert_enabled,
        alert_window_min=alert_window,
        alert_threshold_pct=alert_threshold,
        token_budget=s.token_budget,
        persona=persona,
        session_name=s.name,
    )


async def _create_session(engine, result: WizardResult) -> str:
    """Create a new Session record from WizardResult. Returns session_id.
    Handles name deduplication by appending ' #N' suffix."""
    async with get_session(engine) as db_sess:
        # Name deduplication
        base_name = result.session_name
        name = base_name
        suffix = 2
        while True:
            existing = await db_sess.execute(
                select(Session).where(Session.name == name)
            )
            if existing.scalar_one_or_none() is None:
                break
            name = f"{base_name} #{suffix}"
            suffix += 1

        # Alert config JSON
        alert_config = None
        if result.alert_enabled:
            alert_config = json.dumps({
                "enabled": True,
                "window": result.alert_window_min,
                "threshold": result.alert_threshold_pct,
            })

        trading_session = Session(
            name=name,
            symbol=result.symbol,
            persona_config=json.dumps(result.persona.model_dump()),
            model_config=json.dumps({
                "id": result.model_config.id,
                "provider": result.model_config.provider,
                "model": result.model_config.model,
            }),
            initial_balance=result.initial_balance,
            status="active",
            exchange_type=result.exchange_type,
            timeframe=result.timeframe,
            scheduler_interval_min=result.scheduler_interval_min,
            approval_enabled=result.approval_enabled,
            alert_config=alert_config,
            fee_rate=result.fee_rate,
            token_budget=result.token_budget,
        )
        db_sess.add(trading_session)
        await db_sess.commit()
        await db_sess.refresh(trading_session)
        return trading_session.id


_EXCHANGE_DISPLAY = {"simulated": "sim", "okx": "okx"}


async def _generate_session_name_from_db(engine, symbol: str, exchange_type: str) -> str:
    """Generate session name with #{N} counter from DB.
    Pattern: '{symbol_short} {exchange_display} #{N}'"""
    symbol_short = symbol.split("/")[0]
    exchange_display = _EXCHANGE_DISPLAY.get(exchange_type, exchange_type)
    prefix = f"{symbol_short} {exchange_display}"

    async with get_session(engine) as db_sess:
        # Escape LIKE wildcards in prefix, then match "{prefix} %"
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        result = await db_sess.execute(
            select(Session).where(Session.name.like(f"{escaped} %", escape="\\"))
        )
        existing_names = {s.name for s in result.scalars().all()}

    n = 1
    while f"{prefix} #{n}" in existing_names:
        n += 1
    return f"{prefix} #{n}"


async def _display_session_list(sessions: list[Session], engine, console: Console) -> None:
    """Display session list as Rich Table with position summary."""
    table = Table(title="TradeBot Sessions", border_style="blue")
    table.add_column("#", style="dim", width=4)
    table.add_column("Name", style="bold")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("Position")
    table.add_column("Last Active")

    for i, s in enumerate(sessions, 1):
        mode = _EXCHANGE_DISPLAY.get(s.exchange_type, s.exchange_type)
        if s.status == "active":
            status = "[green]\u25b6 active[/]"
        else:
            status = "[yellow]\u23f8 paused[/]"

        position = await _get_position_summary(engine, s.id, s.exchange_type)

        if s.last_active_at:
            last_active = s.last_active_at
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - last_active
            if delta < timedelta(minutes=1):
                active_str = "just now"
            elif delta < timedelta(hours=1):
                active_str = f"{int(delta.total_seconds() / 60)} min ago"
            elif delta < timedelta(days=1):
                active_str = f"{int(delta.total_seconds() / 3600)} hours ago"
            else:
                active_str = f"{delta.days} days ago"
        else:
            active_str = "\u2014"

        table.add_row(str(i), s.name, mode, status, position, active_str)

    table.add_row(
        str(len(sessions) + 1), "[green]+ New Session[/]", "", "", "", "",
    )
    console.print(table)


def _make_name_generator(engine):
    """Create an async name_generator callback bound to the DB engine."""
    async def _gen(symbol: str, exchange_type: str) -> str:
        return await _generate_session_name_from_db(engine, symbol, exchange_type)
    return _gen


async def select_or_create_session(
    engine,
    settings: Settings,
    trader_config: TraderConfig,
    model_manager: ModelManager,
    model_id: str | None,
    console: Console,
    config_dir: Path,
) -> tuple[WizardResult, str]:
    """Entry point for session management.
    Returns (WizardResult, session_id). Calls sys.exit(0) on wizard cancel."""
    # Fix residual active sessions from unclean shutdown
    async with engine.begin() as conn:
        await _migrate_session_table(conn)
        await _migrate_trade_actions_table(conn)
        fixed = await _fix_residual_active(conn)
    if fixed:
        console.print(f"[dim]Fixed {fixed} residual active session(s)[/]")

    sessions = await _list_sessions(engine)
    name_gen = _make_name_generator(engine)

    # Pre-fetch all session names for uniqueness check in wizard
    async with get_session(engine) as db_sess:
        all_sessions = await db_sess.execute(select(Session))
        all_names = {s.name for s in all_sessions.scalars().all()}

    if not sessions:
        # No history — go straight to wizard
        result = await run_wizard(
            model_manager=model_manager,
            defaults=settings,
            trader_defaults=trader_config,
            config_dir=config_dir,
            console=console,
            model_id=model_id,
            name_generator=name_gen,
            existing_names=all_names,
        )
        if result is None:
            console.print("Cancelled.")
            sys.exit(0)
        session_id = await _create_session(engine, result)
        return result, session_id

    # Show session list and let user choose
    await _display_session_list(sessions, engine, console)
    new_option = len(sessions) + 1
    while True:
        choice = IntPrompt.ask(
            "Select session", default=1, console=console,
        )
        if 1 <= choice <= new_option:
            break
        console.print(f"[red]Please enter a number between 1 and {new_option}[/]")

    if choice == new_option:
        # New session
        result = await run_wizard(
            model_manager=model_manager,
            defaults=settings,
            trader_defaults=trader_config,
            config_dir=config_dir,
            console=console,
            model_id=model_id,
            name_generator=name_gen,
            existing_names=all_names,
        )
        if result is None:
            console.print("Cancelled.")
            sys.exit(0)
        session_id = await _create_session(engine, result)
        return result, session_id

    # Restore existing session
    selected = sessions[choice - 1]
    console.print(f'\nRestoring "[bold]{selected.name}[/]"...')
    result = await _restore_session(
        engine, selected.id, model_manager, model_id, console, config_dir,
    )
    if result is None:
        console.print("Cancelled.")
        sys.exit(0)
    return result, selected.id
