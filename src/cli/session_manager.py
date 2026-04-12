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
    alert_cooldown = None
    if s.alert_config:
        alert_data = json.loads(s.alert_config)
        alert_enabled = alert_data.get("enabled", False)
        alert_window = alert_data.get("window")
        alert_threshold = alert_data.get("threshold")
        alert_cooldown = alert_data.get("cooldown")

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

    # Update status to active
    async with get_session(engine) as db_sess:
        await db_sess.execute(
            update(Session).where(Session.id == session_id).values(status="active")
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
        alert_cooldown_min=alert_cooldown,
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
                "cooldown": result.alert_cooldown_min,
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
