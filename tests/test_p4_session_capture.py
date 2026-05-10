"""P4 session-level capture — sessions.system_prompt populated at session start.

Tests:
  1. Happy path: run-style invocation triggers generate_system_prompt + UPDATE,
     resulting non-NULL sessions.system_prompt matching the rendered text.
  2. Failure isolation: DB UPDATE failure logs a warning and leaves the row
     intact (system_prompt = NULL), without raising.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel


async def _seed_session(engine, session_id: str = "sess-p4-cap"):
    """Seed a session row that the capture path will UPDATE.

    Note: persona_config is intentionally not seeded — the helper takes
    PersonaConfig as direct argument and never reads sessions.persona_config
    column. Leaving it NULL avoids implying a fixture coupling that doesn't
    exist.
    """
    async with get_session(engine) as db:
        db.add(SessionModel(
            id=session_id,
            name="p4-test",
            scheduler_interval_min=15,
        ))
        await db.commit()
    return session_id


async def test_session_create_captures_system_prompt():
    """AC-2: after capture, sessions.system_prompt equals the rendered text."""
    from src.cli.app import _capture_session_system_prompt
    # PersonaConfig is at src/config.py:100, NOT in src/agent/persona.py
    from src.config import PersonaConfig
    from src.agent.persona import generate_system_prompt, RuntimeConfig

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    session_id = await _seed_session(engine)

    persona = PersonaConfig()  # uses defaults
    runtime = RuntimeConfig(wake_max_minutes=60)
    expected_prompt = generate_system_prompt(persona, runtime)

    await _capture_session_system_prompt(engine, session_id, persona, runtime)

    async with get_session(engine) as db:
        row = (await db.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )).scalar_one()
        assert row.system_prompt == expected_prompt, (
            f"system_prompt mismatch:\n"
            f"  expected (first 200): {expected_prompt[:200]!r}\n"
            f"  actual   (first 200): {(row.system_prompt or '')[:200]!r}"
        )


async def test_session_capture_failure_does_not_block_startup(caplog):
    """AC-5: DB exception during capture → logger.warning + system_prompt stays NULL."""
    import logging
    from src.cli.app import _capture_session_system_prompt
    # PersonaConfig is at src/config.py:100, NOT in src/agent/persona.py
    from src.config import PersonaConfig
    from src.agent.persona import RuntimeConfig

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    session_id = await _seed_session(engine)

    persona = PersonaConfig()
    runtime = RuntimeConfig(wake_max_minutes=60)

    # Simulate DB failure: patch get_session to raise inside the helper
    boom = RuntimeError("simulated DB failure")
    with patch("src.cli.app.get_session", side_effect=boom), caplog.at_level(logging.WARNING):
        # Must NOT raise
        await _capture_session_system_prompt(engine, session_id, persona, runtime)

    # Verify warning was logged
    assert any(
        "P4 system_prompt capture failed" in rec.message
        for rec in caplog.records
        if rec.levelno >= logging.WARNING
    ), f"Expected warning not logged. caplog records: {[r.message for r in caplog.records]}"

    # Verify row still has NULL system_prompt (helper did not partially commit)
    async with get_session(engine) as db:
        row = (await db.execute(
            select(SessionModel).where(SessionModel.id == session_id)
        )).scalar_one()
        assert row.system_prompt is None, (
            f"system_prompt should be NULL after capture failure, got: {row.system_prompt!r}"
        )
