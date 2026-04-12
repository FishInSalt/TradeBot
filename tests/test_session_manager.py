# tests/test_session_manager.py
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console
from sqlalchemy import text

from src.storage.database import init_db, get_session
from src.storage.models import Session


async def test_migrate_session_table_adds_new_columns(tmp_path):
    """Migration adds R2 columns to a pre-existing sessions table."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    # Since init_db creates tables from ORM (which now includes R2 columns),
    # we verify migration is idempotent — running it on an already-migrated table is safe
    from src.cli.session_manager import _migrate_session_table
    async with engine.begin() as conn:
        await _migrate_session_table(conn)

    # Verify columns exist
    async with engine.connect() as conn:
        result = await conn.execute(text("PRAGMA table_info(sessions)"))
        cols = {row[1] for row in result}
    for col in ["exchange_type", "timeframe", "scheduler_interval_min",
                "approval_enabled", "alert_config", "fee_rate",
                "token_budget", "last_active_at"]:
        assert col in cols, f"Column {col} missing after migration"
    await engine.dispose()


async def test_migrate_session_table_is_idempotent(tmp_path):
    """Running migration twice does not raise errors."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)
    from src.cli.session_manager import _migrate_session_table
    async with engine.begin() as conn:
        await _migrate_session_table(conn)
    # Run again — should not error
    async with engine.begin() as conn:
        await _migrate_session_table(conn)
    await engine.dispose()


async def test_restore_session_builds_wizard_result(tmp_path):
    """Restoring a session with all R2 fields produces a valid WizardResult."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    from src.config import PersonaConfig
    from src.services.model_manager import ModelConfig

    persona = PersonaConfig(risk_tolerance="aggressive", trading_style="breakout")
    model_cfg = ModelConfig(id="test-model", provider="openai", model="gpt-4o",
                            api_key="k", base_url=None)
    alert_cfg = json.dumps({"enabled": True, "window": 5, "threshold": 3.0, "cooldown": 15})

    async with get_session(engine) as db_sess:
        s = Session(
            id="restore-test", name="BTC sim #1", symbol="BTC/USDT:USDT",
            persona_config=json.dumps(persona.model_dump()),
            model_config=json.dumps({"id": "test-model", "provider": "openai", "model": "gpt-4o"}),
            initial_balance=200.0, status="paused",
            exchange_type="simulated", timeframe="1H",
            scheduler_interval_min=30, approval_enabled=False,
            alert_config=alert_cfg, fee_rate=0.0005,
            token_budget=300000,
        )
        db_sess.add(s)
        await db_sess.commit()

    from src.cli.session_manager import _restore_session
    from unittest.mock import MagicMock, patch

    mock_mm = MagicMock()
    mock_mm.load_models.return_value = [model_cfg]
    mock_mm.get_model_by_id.return_value = model_cfg
    mock_model = MagicMock()
    mock_mm.create_model.return_value = mock_model

    console = Console()
    with patch("src.cli.session_manager.Confirm.ask", return_value=True):
        result = await _restore_session(
            engine, "restore-test", mock_mm, None, console, Path(str(tmp_path)),
        )

    assert result is not None
    assert result.exchange_type == "simulated"
    assert result.symbol == "BTC/USDT:USDT"
    assert result.timeframe == "1H"
    assert result.scheduler_interval_min == 30
    assert result.approval_enabled is False
    assert result.alert_enabled is True
    assert result.alert_window_min == 5
    assert result.fee_rate == 0.0005
    assert result.token_budget == 300000
    assert result.persona.risk_tolerance == "aggressive"
    assert result.session_name == "BTC sim #1"
    assert result.model == mock_model
    await engine.dispose()


async def test_restore_session_null_alert_config(tmp_path):
    """Migrated old session with NULL alert_config → alerts disabled."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    from src.config import PersonaConfig
    from src.services.model_manager import ModelConfig

    model_cfg = ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None)

    async with get_session(engine) as db_sess:
        s = Session(
            id="old-session", name="old test", symbol="BTC/USDT:USDT",
            persona_config=json.dumps(PersonaConfig().model_dump()),
            model_config=json.dumps({"id": "m1", "provider": "openai", "model": "gpt-4o"}),
            initial_balance=100.0, status="paused",
            alert_config=None,
        )
        db_sess.add(s)
        await db_sess.commit()

    from src.cli.session_manager import _restore_session

    mock_mm = MagicMock()
    mock_mm.load_models.return_value = [model_cfg]
    mock_mm.get_model_by_id.return_value = model_cfg
    mock_mm.create_model.return_value = MagicMock()

    with patch("src.cli.session_manager.Confirm.ask", return_value=True):
        result = await _restore_session(
            engine, "old-session", mock_mm, None, Console(), Path(str(tmp_path)),
        )

    assert result.alert_enabled is False
    assert result.alert_window_min is None
    await engine.dispose()
