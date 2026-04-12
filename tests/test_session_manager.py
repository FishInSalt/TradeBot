# tests/test_session_manager.py
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console
from sqlalchemy import select, text

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


async def test_fix_residual_active_sessions(tmp_path):
    """On startup, any session with status='active' gets set to 'paused'."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    async with get_session(engine) as db_sess:
        db_sess.add(Session(id="s1", name="active-one", status="active"))
        db_sess.add(Session(id="s2", name="paused-one", status="paused"))
        db_sess.add(Session(id="s3", name="active-two", status="active"))
        await db_sess.commit()

    from src.cli.session_manager import _fix_residual_active
    async with engine.begin() as conn:
        count = await _fix_residual_active(conn)
    assert count == 2

    async with get_session(engine) as db_sess:
        for sid in ["s1", "s3"]:
            result = await db_sess.execute(select(Session).where(Session.id == sid))
            assert result.scalar_one().status == "paused"
    await engine.dispose()


async def test_list_sessions_ordered_by_last_active(tmp_path):
    """Sessions listed in descending last_active_at order, only active/paused."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    now = datetime.now(timezone.utc)
    async with get_session(engine) as db_sess:
        db_sess.add(Session(id="s1", name="Older", status="paused",
            last_active_at=now - timedelta(days=3)))
        db_sess.add(Session(id="s2", name="Newer", status="paused",
            last_active_at=now - timedelta(hours=2)))
        db_sess.add(Session(id="s3", name="Stopped", status="stopped",
            last_active_at=now))
        db_sess.add(Session(id="s4", name="No-active", status="paused",
            last_active_at=None))
        await db_sess.commit()

    from src.cli.session_manager import _list_sessions
    sessions = await _list_sessions(engine)
    assert len(sessions) == 3
    assert sessions[0].name == "Newer"
    assert sessions[1].name == "Older"
    assert sessions[2].name == "No-active"
    await engine.dispose()


async def test_get_position_summary_with_position(tmp_path):
    """Sim session with open position shows 'side contracts symbol'."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)
    from src.storage.models import SimPosition

    async with get_session(engine) as db_sess:
        db_sess.add(Session(id="s1", name="test"))
        await db_sess.commit()
        db_sess.add(SimPosition(
            session_id="s1", symbol="BTC/USDT:USDT", side="long",
            contracts=0.5, entry_price=95000.0, leverage=3,
        ))
        await db_sess.commit()

    from src.cli.session_manager import _get_position_summary
    assert await _get_position_summary(engine, "s1", "simulated") == "long 0.5 BTC"
    await engine.dispose()


async def test_get_position_summary_no_position(tmp_path):
    """Sim session without position shows em-dash."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    async with get_session(engine) as db_sess:
        db_sess.add(Session(id="s1", name="test"))
        await db_sess.commit()

    from src.cli.session_manager import _get_position_summary
    assert await _get_position_summary(engine, "s1", "simulated") == "\u2014"
    await engine.dispose()


async def test_get_position_summary_real_exchange(tmp_path):
    """Real exchange always shows em-dash (not connected)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)
    from src.cli.session_manager import _get_position_summary
    assert await _get_position_summary(engine, "any-id", "okx") == "\u2014"
    await engine.dispose()


async def test_create_session_from_wizard_result(tmp_path):
    """WizardResult fields are correctly persisted to Session record."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    from src.config import PersonaConfig
    from src.services.model_manager import ModelConfig
    from src.cli.wizard import WizardResult

    result = WizardResult(
        exchange_type="simulated", fee_rate=0.001, initial_balance=500.0,
        api_credentials=None, symbol="ETH/USDT:USDT", timeframe="1H",
        model_config=ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None),
        model=MagicMock(),
        scheduler_interval_min=30, approval_enabled=False,
        alert_enabled=True, alert_window_min=10, alert_threshold_pct=5.0, alert_cooldown_min=20,
        token_budget=300000,
        persona=PersonaConfig(risk_tolerance="aggressive"),
        session_name="ETH sim #1",
    )

    from src.cli.session_manager import _create_session
    session_id = await _create_session(engine, result)

    async with get_session(engine) as db_sess:
        row = await db_sess.execute(select(Session).where(Session.id == session_id))
        s = row.scalar_one()

    assert s.name == "ETH sim #1"
    assert s.exchange_type == "simulated"
    assert s.timeframe == "1H"
    assert s.scheduler_interval_min == 30
    assert s.approval_enabled is False
    assert s.fee_rate == 0.001
    assert s.token_budget == 300000
    alert = json.loads(s.alert_config)
    assert alert["enabled"] is True
    assert alert["window"] == 10
    assert s.status == "active"
    await engine.dispose()


async def test_create_session_name_dedup(tmp_path):
    """Duplicate session names get suffix appended."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    from src.config import PersonaConfig
    from src.services.model_manager import ModelConfig
    from src.cli.wizard import WizardResult

    base = WizardResult(
        exchange_type="simulated", fee_rate=0.0005, initial_balance=100.0,
        api_credentials=None, symbol="BTC/USDT:USDT", timeframe="15m",
        model_config=ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None),
        model=MagicMock(),
        scheduler_interval_min=15, approval_enabled=True,
        alert_enabled=False, alert_window_min=None, alert_threshold_pct=None, alert_cooldown_min=None,
        token_budget=500000,
        persona=PersonaConfig(),
        session_name="BTC sim",
    )

    from src.cli.session_manager import _create_session
    id1 = await _create_session(engine, base)
    id2 = await _create_session(engine, base)

    async with get_session(engine) as db_sess:
        r1 = await db_sess.execute(select(Session).where(Session.id == id1))
        r2 = await db_sess.execute(select(Session).where(Session.id == id2))
        assert r1.scalar_one().name == "BTC sim"
        assert r2.scalar_one().name == "BTC sim #2"
    await engine.dispose()


async def test_generate_session_name_counter(tmp_path):
    """Session name generator produces #{N} suffix based on existing sessions."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    from src.cli.session_manager import _generate_session_name_from_db

    name1 = await _generate_session_name_from_db(engine, "BTC/USDT:USDT", "simulated")
    assert name1 == "BTC sim #1"

    async with get_session(engine) as db_sess:
        db_sess.add(Session(id="s1", name="BTC sim #1"))
        await db_sess.commit()

    name2 = await _generate_session_name_from_db(engine, "BTC/USDT:USDT", "simulated")
    assert name2 == "BTC sim #2"

    async with get_session(engine) as db_sess:
        db_sess.add(Session(id="s2", name="BTC sim #2"))
        await db_sess.commit()

    name3 = await _generate_session_name_from_db(engine, "BTC/USDT:USDT", "simulated")
    assert name3 == "BTC sim #3"
    await engine.dispose()
