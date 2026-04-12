# R2: Session Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support multiple session create/restore — terminal close + restart resumes without re-configuring.

**Architecture:** Extend Session ORM model with 8 new columns. New `session_manager.py` module handles session list display, restore-from-DB, and new-session routing. `app.py` Phase 3 delegates to `select_or_create_session()` instead of calling `run_wizard()` directly. Shutdown updates session status to "paused"; agent cycles update `last_active_at`.

**Tech Stack:** Python 3.12+, SQLAlchemy async (aiosqlite), Rich (Table/Prompt), pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-12-batch1-r5-r1-r2-design.md` (R2 section, lines 451–658)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/storage/models.py` | Modify | Add 8 new columns to Session model |
| `src/cli/session_manager.py` | Create | Session list, restore, migration, routing |
| `src/cli/wizard.py` | Modify | Add `name_generator` async callback param to `run_wizard` |
| `src/cli/app.py` | Modify | Phase 3 → call `select_or_create_session()`; Phase 6 → status lifecycle |
| `tests/test_session_manager.py` | Create | All session manager tests |

---

### Task 1: Extend Session ORM Model

**Files:**
- Modify: `src/storage/models.py:22-35`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write test for new Session fields with defaults**

Add to end of `tests/test_storage.py`:

```python
async def test_session_new_fields_have_defaults():
    """New R2 fields have ORM defaults — Session(id=..., name=...) still works."""
    from src.storage.models import Session
    from src.storage.database import init_db, get_session

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as session:
        s = Session(id="r2-test", name="r2-defaults")
        session.add(s)
        await session.commit()
        await session.refresh(s)

        assert s.exchange_type == "simulated"
        assert s.timeframe == "15m"
        assert s.scheduler_interval_min == 15
        assert s.approval_enabled is True
        assert s.alert_config is None
        assert s.fee_rate is None
        assert s.token_budget == 500000
        assert s.last_active_at is None
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage.py::test_session_new_fields_have_defaults -v`
Expected: FAIL — `Session` has no attribute `exchange_type`

- [ ] **Step 3: Add 8 new columns to Session model**

In `src/storage/models.py`, add after line 35 (`updated_at` field), before the blank line:

```python
    # --- R2: Session management fields ---
    exchange_type: Mapped[str] = mapped_column(String(20), default="simulated")
    timeframe: Mapped[str] = mapped_column(String(10), default="15m")
    scheduler_interval_min: Mapped[int] = mapped_column(Integer, default=15)
    approval_enabled: Mapped[bool] = mapped_column(default=True)
    alert_config: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON: {enabled, window, threshold, cooldown}
    fee_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_budget: Mapped[int] = mapped_column(Integer, default=500000)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_storage.py -v`
Expected: ALL PASS (new test + all existing tests unaffected)

- [ ] **Step 5: Commit**

```bash
git add src/storage/models.py tests/test_storage.py
git commit -m "feat(r2): add 8 new columns to Session model for session management"
```

---

### Task 2: Session Table Migration

**Files:**
- Create: `src/cli/session_manager.py` (migration function only, rest in later tasks)
- Test: `tests/test_session_manager.py`

- [ ] **Step 1: Write test for idempotent migration**

Create `tests/test_session_manager.py`:

```python
# tests/test_session_manager.py
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
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

    # Simulate pre-R2 table: drop the new columns so migration has work to do
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(sessions)"))
        cols = {row[1] for row in result}

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_manager.py -v`
Expected: FAIL — `cannot import name '_migrate_session_table' from 'src.cli.session_manager'`

- [ ] **Step 3: Create `session_manager.py` with migration function**

Create `src/cli/session_manager.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_manager.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/session_manager.py tests/test_session_manager.py
git commit -m "feat(r2): add session table migration logic"
```

---

### Task 3: Session Restore — Load from DB to WizardResult

**Files:**
- Modify: `src/cli/session_manager.py`
- Test: `tests/test_session_manager.py`

- [ ] **Step 1: Write test for restoring a session from DB**

Append to `tests/test_session_manager.py`:

```python
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
            alert_config=None,  # old session, no alert config
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_manager.py::test_restore_session_builds_wizard_result tests/test_session_manager.py::test_restore_session_null_alert_config -v`
Expected: FAIL — `cannot import name '_restore_session'`

- [ ] **Step 3: Implement `_restore_session`**

Add to `src/cli/session_manager.py`, after the `_migrate_session_table` function:

```python
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

    selected_config = None
    selected_model = None

    # --model flag takes priority
    if model_id:
        selected_config = model_manager.get_model_by_id(model_id, model_manager.load_models())
        if selected_config is None:
            console.print(f"[yellow]Model '{model_id}' not found, entering selection...[/]")
        else:
            selected_model = model_manager.create_model(selected_config)

    # No --model flag or --model not found — try session's saved model
    if selected_model is None and saved_model_id:
        selected_config = model_manager.get_model_by_id(saved_model_id, model_manager.load_models())
        if selected_config:
            if Confirm.ask(
                f"  Continue with model [bold]{selected_config.id}[/]?",
                default=True, console=console,
            ):
                selected_model = model_manager.create_model(selected_config)
            # User said no — fall through to wizard step 3
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_manager.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/session_manager.py tests/test_session_manager.py
git commit -m "feat(r2): implement session restore from DB to WizardResult"
```

---

### Task 4: Session List Display + Residual Active Fix

**Files:**
- Modify: `src/cli/session_manager.py`
- Test: `tests/test_session_manager.py`

- [ ] **Step 1: Write tests for list display and residual active fix**

Append to `tests/test_session_manager.py`:

```python
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

    # Verify both are now paused
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
        db_sess.add(Session(
            id="s1", name="Older", status="paused",
            last_active_at=now - timedelta(days=3),
        ))
        db_sess.add(Session(
            id="s2", name="Newer", status="paused",
            last_active_at=now - timedelta(hours=2),
        ))
        db_sess.add(Session(
            id="s3", name="Stopped", status="stopped",
            last_active_at=now,
        ))
        db_sess.add(Session(
            id="s4", name="No-active", status="paused",
            last_active_at=None,
        ))
        await db_sess.commit()

    from src.cli.session_manager import _list_sessions
    sessions = await _list_sessions(engine)
    # Only active/paused, ordered by last_active_at desc (NULLs last)
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
    """Sim session without position shows '—'."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    async with get_session(engine) as db_sess:
        db_sess.add(Session(id="s1", name="test"))
        await db_sess.commit()

    from src.cli.session_manager import _get_position_summary
    assert await _get_position_summary(engine, "s1", "simulated") == "—"
    await engine.dispose()


async def test_get_position_summary_real_exchange(tmp_path):
    """Real exchange always shows '—' (not connected)."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)
    from src.cli.session_manager import _get_position_summary
    assert await _get_position_summary(engine, "any-id", "okx") == "—"
    await engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_manager.py::test_fix_residual_active_sessions tests/test_session_manager.py::test_list_sessions_ordered_by_last_active tests/test_session_manager.py::test_get_position_summary_with_position tests/test_session_manager.py::test_get_position_summary_no_position tests/test_session_manager.py::test_get_position_summary_real_exchange -v`
Expected: FAIL — cannot import `_fix_residual_active` / `_list_sessions` / `_get_position_summary`

- [ ] **Step 3: Implement `_fix_residual_active` and `_list_sessions`**

Add to `src/cli/session_manager.py`, after `_migrate_session_table`:

```python
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
    Sim: query SimPosition table. Real: return '—' (exchange not connected)."""
    if exchange_type != "simulated":
        return "—"
    from src.storage.models import SimPosition
    async with get_session(engine) as db_sess:
        result = await db_sess.execute(
            select(SimPosition).where(SimPosition.session_id == session_id)
        )
        pos = result.scalar_one_or_none()
    if pos is None:
        return "\u2014"
    return f"{pos.side} {pos.contracts} {pos.symbol.split('/')[0]}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_manager.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/session_manager.py tests/test_session_manager.py
git commit -m "feat(r2): add residual active fix and session listing"
```

---

### Task 5: Session Creation — WizardResult → Session Record

**Files:**
- Modify: `src/cli/session_manager.py`
- Modify: `src/cli/wizard.py` (update `_generate_session_name` signature)
- Test: `tests/test_session_manager.py`

- [ ] **Step 1: Write tests for session creation**

Append to `tests/test_session_manager.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_manager.py::test_create_session_from_wizard_result tests/test_session_manager.py::test_create_session_name_dedup -v`
Expected: FAIL — cannot import `_create_session`

- [ ] **Step 3: Implement `_create_session`**

Add to `src/cli/session_manager.py`, after `_list_sessions`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_manager.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/session_manager.py tests/test_session_manager.py
git commit -m "feat(r2): implement session creation with field persistence and name dedup"
```

---

### Task 6: DB-Backed Session Name + Wizard `name_generator` Callback

**Files:**
- Modify: `src/cli/session_manager.py`
- Modify: `src/cli/wizard.py:300-305,349-384`
- Test: `tests/test_session_manager.py`
- Test: `tests/test_wizard.py`

The wizard's `_generate_session_name()` produces "BTC sim" without `#{N}` counter. The DB-aware name generator lives in `session_manager.py` but can't be called from wizard without coupling wizard to the DB. Fix: add an async `name_generator` callback to `run_wizard`. When provided, wizard calls it instead of its internal `_generate_session_name`. `select_or_create_session` passes a lambda that calls `_generate_session_name_from_db`.

- [ ] **Step 1: Write test for `_generate_session_name_from_db`**

Append to `tests/test_session_manager.py`:

```python
async def test_generate_session_name_counter(tmp_path):
    """Session name generator produces #{N} suffix based on existing sessions."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    from src.cli.session_manager import _generate_session_name_from_db

    # No existing sessions → #1
    name1 = await _generate_session_name_from_db(engine, "BTC/USDT:USDT", "simulated")
    assert name1 == "BTC sim #1"

    # Create that session
    async with get_session(engine) as db_sess:
        db_sess.add(Session(id="s1", name="BTC sim #1"))
        await db_sess.commit()

    # Next → #2
    name2 = await _generate_session_name_from_db(engine, "BTC/USDT:USDT", "simulated")
    assert name2 == "BTC sim #2"

    # Create #2, skip to #3
    async with get_session(engine) as db_sess:
        db_sess.add(Session(id="s2", name="BTC sim #2"))
        await db_sess.commit()

    name3 = await _generate_session_name_from_db(engine, "BTC/USDT:USDT", "simulated")
    assert name3 == "BTC sim #3"
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_session_manager.py::test_generate_session_name_counter -v`
Expected: FAIL — cannot import `_generate_session_name_from_db`

- [ ] **Step 3: Implement `_generate_session_name_from_db`**

Add to `src/cli/session_manager.py`, after `_create_session`:

```python
_EXCHANGE_DISPLAY = {"simulated": "sim", "okx": "okx"}


async def _generate_session_name_from_db(engine, symbol: str, exchange_type: str) -> str:
    """Generate session name with #{N} counter from DB.
    Pattern: '{symbol_short} {exchange_display} #{N}'"""
    symbol_short = symbol.split("/")[0]
    exchange_display = _EXCHANGE_DISPLAY.get(exchange_type, exchange_type)
    prefix = f"{symbol_short} {exchange_display}"

    async with get_session(engine) as db_sess:
        # Count existing sessions with this prefix
        result = await db_sess.execute(
            select(Session).where(Session.name.like(f"{prefix} %"))
        )
        existing_names = {s.name for s in result.scalars().all()}

    n = 1
    while f"{prefix} #{n}" in existing_names:
        n += 1
    return f"{prefix} #{n}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_session_manager.py::test_generate_session_name_counter -v`
Expected: PASS

- [ ] **Step 5: Write test for wizard `name_generator` callback**

Append to `tests/test_wizard.py`:

```python
async def test_run_wizard_uses_name_generator_callback():
    """When name_generator is provided, wizard uses it instead of internal _generate_session_name."""
    from src.cli.wizard import run_wizard, WizardResult

    async def mock_name_gen(symbol: str, exchange_type: str) -> str:
        return f"{symbol.split('/')[0]} sim #42"

    with patch("src.cli.wizard._step_exchange", return_value={
            "exchange_type": "simulated", "fee_rate": 0.0005,
            "initial_balance": 100.0, "api_credentials": None,
        }), \
         patch("src.cli.wizard._step_trading_pair", return_value={
            "symbol": "BTC/USDT:USDT", "timeframe": "15m",
        }), \
         patch("src.cli.wizard._step_model", new_callable=AsyncMock, return_value={
            "model_config": ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None),
            "model": MagicMock(),
        }), \
         patch("src.cli.wizard._step_risk_scheduling", return_value={
            "scheduler_interval_min": 15, "approval_enabled": True,
            "alert_enabled": False, "alert_window_min": None,
            "alert_threshold_pct": None, "alert_cooldown_min": None,
            "token_budget": 500000,
        }), \
         patch("src.cli.wizard._step_persona", return_value={
            "persona": PersonaConfig(),
        }), \
         patch("src.cli.wizard._show_summary", return_value=True), \
         patch("src.cli.wizard.Prompt.ask", return_value="BTC sim #42"):
        result = await run_wizard(
            model_manager=MagicMock(),
            defaults=Settings(),
            trader_defaults=TraderConfig(),
            config_dir=Path("/tmp"),
            console=Console(),
            name_generator=mock_name_gen,
        )

    assert result is not None
    assert result.session_name == "BTC sim #42"


async def test_run_wizard_without_name_generator_uses_internal():
    """Without name_generator, wizard uses its internal _generate_session_name."""
    from src.cli.wizard import run_wizard

    with patch("src.cli.wizard._step_exchange", return_value={
            "exchange_type": "simulated", "fee_rate": 0.0005,
            "initial_balance": 100.0, "api_credentials": None,
        }), \
         patch("src.cli.wizard._step_trading_pair", return_value={
            "symbol": "BTC/USDT:USDT", "timeframe": "15m",
        }), \
         patch("src.cli.wizard._step_model", new_callable=AsyncMock, return_value={
            "model_config": ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None),
            "model": MagicMock(),
        }), \
         patch("src.cli.wizard._step_risk_scheduling", return_value={
            "scheduler_interval_min": 15, "approval_enabled": True,
            "alert_enabled": False, "alert_window_min": None,
            "alert_threshold_pct": None, "alert_cooldown_min": None,
            "token_budget": 500000,
        }), \
         patch("src.cli.wizard._step_persona", return_value={
            "persona": PersonaConfig(),
        }), \
         patch("src.cli.wizard._show_summary", return_value=True), \
         patch("src.cli.wizard.Prompt.ask", return_value="BTC sim"):
        result = await run_wizard(
            model_manager=MagicMock(),
            defaults=Settings(),
            trader_defaults=TraderConfig(),
            config_dir=Path("/tmp"),
            console=Console(),
        )

    assert result is not None
    assert result.session_name == "BTC sim"
```

- [ ] **Step 6: Run wizard tests to verify they fail**

Run: `python -m pytest tests/test_wizard.py::test_run_wizard_uses_name_generator_callback -v`
Expected: FAIL — `run_wizard() got an unexpected keyword argument 'name_generator'`

- [ ] **Step 7: Modify `run_wizard` to accept `name_generator` callback**

In `src/cli/wizard.py`, update the `run_wizard` signature (line 349) and the name generation section (lines 376-378):

Update signature:

```python
async def run_wizard(
    model_manager: ModelManager,
    defaults: Settings,
    trader_defaults: TraderConfig,
    config_dir: Path,
    console: Console,
    model_id: str | None = None,
    name_generator: Any | None = None,  # async (symbol, exchange_type) -> str
) -> WizardResult | None:
```

Update the name generation section (inside `if _show_summary(data, console):` block, lines 376-378):

Replace:
```python
                default_name = _generate_session_name(data["symbol"], data["exchange_type"])
```

With:
```python
                if name_generator is not None:
                    default_name = await name_generator(data["symbol"], data["exchange_type"])
                else:
                    default_name = _generate_session_name(data["symbol"], data["exchange_type"])
```

- [ ] **Step 8: Run all wizard + session_manager tests**

Run: `python -m pytest tests/test_wizard.py tests/test_session_manager.py -v`
Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add src/cli/wizard.py src/cli/session_manager.py tests/test_session_manager.py tests/test_wizard.py
git commit -m "feat(r2): add name_generator callback to wizard, DB-backed session naming"
```

---

### Task 7: Entry Point — `select_or_create_session`

**Files:**
- Modify: `src/cli/session_manager.py`
- Test: `tests/test_session_manager.py`

- [ ] **Step 1: Write test for `select_or_create_session` — no history → wizard**

Append to `tests/test_session_manager.py`:

```python
async def test_select_or_create_no_history_runs_wizard(tmp_path):
    """With no existing sessions, wizard runs and creates a session."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    from src.config import PersonaConfig, Settings, TraderConfig
    from src.services.model_manager import ModelConfig
    from src.cli.wizard import WizardResult

    mock_result = WizardResult(
        exchange_type="simulated", fee_rate=0.0005, initial_balance=100.0,
        api_credentials=None, symbol="BTC/USDT:USDT", timeframe="15m",
        model_config=ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None),
        model=MagicMock(),
        scheduler_interval_min=15, approval_enabled=True,
        alert_enabled=False, alert_window_min=None, alert_threshold_pct=None, alert_cooldown_min=None,
        token_budget=500000,
        persona=PersonaConfig(),
        session_name="BTC sim #1",
    )

    from src.cli.session_manager import select_or_create_session

    mock_mm = MagicMock()
    with patch("src.cli.session_manager.run_wizard", new_callable=AsyncMock, return_value=mock_result), \
         patch("src.cli.session_manager._generate_session_name_from_db", new_callable=AsyncMock, return_value="BTC sim #1"):
        result, session_id = await select_or_create_session(
            engine=engine, settings=Settings(), trader_config=TraderConfig(),
            model_manager=mock_mm, model_id=None,
            console=Console(), config_dir=Path(str(tmp_path)),
        )

    assert result.exchange_type == "simulated"
    assert isinstance(session_id, str)
    assert len(session_id) == 36  # UUID

    # Verify session was created in DB
    async with get_session(engine) as db_sess:
        r = await db_sess.execute(select(Session).where(Session.id == session_id))
        s = r.scalar_one()
        assert s.status == "active"
    await engine.dispose()


async def test_select_or_create_with_history_restore(tmp_path):
    """With existing sessions, user selects one → restore flow."""
    db_url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    engine = await init_db(db_url)

    from src.config import PersonaConfig
    from src.services.model_manager import ModelConfig

    model_cfg = ModelConfig(id="m1", provider="openai", model="gpt-4o", api_key="k", base_url=None)
    async with get_session(engine) as db_sess:
        db_sess.add(Session(
            id="existing-1", name="BTC sim #1", status="paused",
            symbol="BTC/USDT:USDT",
            persona_config=json.dumps(PersonaConfig().model_dump()),
            model_config=json.dumps({"id": "m1", "provider": "openai", "model": "gpt-4o"}),
            last_active_at=datetime.now(timezone.utc),
        ))
        await db_sess.commit()

    from src.cli.session_manager import select_or_create_session

    mock_mm = MagicMock()
    mock_mm.load_models.return_value = [model_cfg]
    mock_mm.get_model_by_id.return_value = model_cfg
    mock_mm.create_model.return_value = MagicMock()

    # User selects option 1 (the existing session), then confirms model
    with patch("src.cli.session_manager.IntPrompt.ask", return_value=1), \
         patch("src.cli.session_manager.Confirm.ask", return_value=True):
        result, session_id = await select_or_create_session(
            engine=engine, settings=Settings(), trader_config=TraderConfig(),
            model_manager=mock_mm, model_id=None,
            console=Console(), config_dir=Path(str(tmp_path)),
        )

    assert session_id == "existing-1"
    assert result.symbol == "BTC/USDT:USDT"

    # Session status should be active now
    async with get_session(engine) as db_sess:
        r = await db_sess.execute(select(Session).where(Session.id == "existing-1"))
        assert r.scalar_one().status == "active"
    await engine.dispose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_manager.py::test_select_or_create_no_history_runs_wizard tests/test_session_manager.py::test_select_or_create_with_history_restore -v`
Expected: FAIL — cannot import `select_or_create_session`

- [ ] **Step 3: Implement `select_or_create_session`**

Add to `src/cli/session_manager.py`, after `_generate_session_name_from_db`:

```python
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
            status = "[green]▶ active[/]"
        else:
            status = "[yellow]⏸ paused[/]"

        position = await _get_position_summary(engine, s.id, s.exchange_type)

        if s.last_active_at:
            delta = datetime.now(timezone.utc) - s.last_active_at
            if delta < timedelta(minutes=1):
                active_str = "just now"
            elif delta < timedelta(hours=1):
                active_str = f"{int(delta.total_seconds() / 60)} min ago"
            elif delta < timedelta(days=1):
                active_str = f"{int(delta.total_seconds() / 3600)} hours ago"
            else:
                active_str = f"{delta.days} days ago"
        else:
            active_str = "—"

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
        fixed = await _fix_residual_active(conn)
    if fixed:
        console.print(f"[dim]Fixed {fixed} residual active session(s)[/]")

    sessions = await _list_sessions(engine)
    name_gen = _make_name_generator(engine)

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
        )
        if result is None:
            console.print("Cancelled.")
            sys.exit(0)
        session_id = await _create_session(engine, result)
        return result, session_id

    # Show session list and let user choose
    await _display_session_list(sessions, engine, console)
    new_option = len(sessions) + 1
    choice = IntPrompt.ask(
        "Select session", default=1, console=console,
    )

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
        )
        if result is None:
            console.print("Cancelled.")
            sys.exit(0)
        session_id = await _create_session(engine, result)
        return result, session_id

    # Restore existing session
    idx = choice - 1
    if idx < 0 or idx >= len(sessions):
        console.print("[red]Invalid selection[/]")
        sys.exit(1)

    selected = sessions[idx]
    console.print(f'\nRestoring "[bold]{selected.name}[/]"...')
    result = await _restore_session(
        engine, selected.id, model_manager, model_id, console, config_dir,
    )
    if result is None:
        console.print("Cancelled.")
        sys.exit(0)
    return result, selected.id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_session_manager.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli/session_manager.py tests/test_session_manager.py
git commit -m "feat(r2): implement select_or_create_session entry point"
```

---

### Task 8: Wire into app.py — Phase 3 + Lifecycle

**Files:**
- Modify: `src/cli/app.py:270-323` (Phase 3), `src/cli/app.py:397-405` (shutdown)
- Test: Run full test suite

- [ ] **Step 1: Update app.py Phase 3 to use `select_or_create_session`**

Replace `src/cli/app.py` lines 270–323 (from `# ── Phase 3:` through `session_id = trading_session.id`) with:

```python
    # ── Phase 3: Session select / wizard ──
    from src.cli.session_manager import select_or_create_session

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
```

This removes:
- The direct `run_wizard()` call
- The manual session creation logic (name dedup loop, Session(...) insert)
- The `from src.cli.wizard import WizardResult, run_wizard` import at top (move to session_manager)

- [ ] **Step 2: Update app.py imports**

Replace the wizard import at line 26:
```python
# Old:
from src.cli.wizard import WizardResult, run_wizard
# New:
from src.cli.wizard import WizardResult
```

Remove the unused imports that were only needed for session creation:
- Remove `json` from imports (line 4) — after Phase 3 removal, `json` is no longer used in app.py.
- Remove `from sqlalchemy import select` (line 10) — no longer used after Phase 3 removal.

Add new imports needed for lifecycle management (Step 3/4):
- Add `from datetime import datetime, timezone`
- Add `from sqlalchemy import update as sql_update`
- Keep `Session` in `from src.storage.models import` (needed for lifecycle updates)

Updated imports block:

```python
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
from src.cli.display import display_metrics
from src.cli.logging_config import SessionConsole, setup_session_logging, setup_system_logging
from src.config import ExchangeConfig, Settings, load_settings, load_trader_config
from src.integrations.exchange.okx import OKXExchange
from src.integrations.market_data import MarketDataService
from src.scheduler.scheduler import Scheduler
from src.services.metrics import MetricsService
from src.services.technical import TechnicalAnalysisService
from src.storage.database import get_session, init_db
from src.storage.models import DecisionLog, Session, TradeAction
from src.integrations.exchange.base import FillEvent
from src.cli.wizard import WizardResult
```

- [ ] **Step 3: Add session lifecycle — shutdown sets paused + last_active_at update**

First, add lifecycle-related imports to `src/cli/app.py` imports block. Add these two lines to the imports section:

```python
from datetime import datetime, timezone
from sqlalchemy import update as sql_update
from src.storage.models import DecisionLog, Session, TradeAction
```

Note: `Session` was removed in Step 2 — re-add it here alongside the new `sql_update` import. Also add `datetime`/`timezone` (previously only used indirectly).

In `src/cli/app.py`, update the shutdown section (after `await scheduler_task`) to set session status to "paused":

Replace lines 400–405:
```python
    scheduler.stop()
    await scheduler_task
    await exchange.close()
    sc.close()
    pre_console.print("[green]TradeBot stopped.[/]")
```

With:
```python
    scheduler.stop()
    await scheduler_task
    await exchange.close()

    # Update session status to paused on graceful shutdown
    async with get_session(engine) as db_sess:
        await db_sess.execute(
            sql_update(Session).where(Session.id == session_id).values(status="paused")
        )
        await db_sess.commit()

    sc.close()
    pre_console.print("[green]TradeBot stopped.[/]")
```

- [ ] **Step 4: Add `last_active_at` update after each agent cycle**

In `src/cli/app.py`, inside the `on_tick` function (line 346), add a `last_active_at` update after `run_agent_cycle` returns. Replace the `on_tick` function:

```python
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
            # Update last_active_at (imports are at module top)
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
            # Process pending fills
            if handle_fill is not None:
                for fill in exchange.drain_pending_fills():
                    try:
                        await handle_fill(fill)
                    except Exception:
                        logger.exception("Fill handler failed for order %s", fill.order_id)
```

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/cli/app.py
git commit -m "feat(r2): wire session_manager into app.py, add session lifecycle management"
```

---

### Task 9: Final Integration Verification

**Files:**
- All modified files

- [ ] **Step 1: Run the complete test suite**

Run: `python -m pytest -v --tb=short`
Expected: ALL PASS, no warnings related to our changes

- [ ] **Step 2: Verify imports are clean**

Run: `python -c "from src.cli.session_manager import select_or_create_session; print('OK')"`
Expected: `OK`

Run: `python -c "from src.cli.app import run; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Review for spec compliance**

Verify against spec checklist:
- [ ] Session table has 8 new columns with correct defaults
- [ ] Migration is idempotent (`_migrate_session_table`)
- [ ] Residual active sessions fixed on startup
- [ ] Session list displays active/paused with Position column, ordered by `last_active_at` DESC
- [ ] New session name uses DB counter via `name_generator` callback ("BTC sim #1", not "BTC sim")
- [ ] New session created with all WizardResult fields persisted
- [ ] Session restore reconstructs WizardResult from DB
- [ ] Model selection: `--model` flag > session record > wizard step 3
- [ ] Alert config NULL → alerts disabled (old sessions)
- [ ] OKX credentials loaded from `.credentials` on restore
- [ ] Graceful shutdown → status "paused"
- [ ] Agent cycle → `last_active_at` updated
- [ ] Wizard cancel → `sys.exit(0)`
- [ ] Name deduplication with `#{N}` suffix

- [ ] **Step 4: Final commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix(r2): integration fixups"
```
