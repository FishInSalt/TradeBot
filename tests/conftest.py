import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import Settings, ExchangeConfig, TradingConfig, TraderConfig, PersonaConfig


@pytest.fixture
def settings() -> Settings:
    return Settings(
        exchange=ExchangeConfig(name="okx", api_key="test", secret="test", password="test"),
        trading=TradingConfig(initial_balance_usdt=10000.0),
    )


@pytest.fixture
def trader_config() -> TraderConfig:
    return TraderConfig(persona=PersonaConfig())


@pytest.fixture
async def engine() -> AsyncEngine:
    """In-memory SQLite engine + schema (R2-4 共享 fixture，原在 test_tool_call_recorder.py)."""
    from src.storage.database import init_db
    return await init_db("sqlite+aiosqlite:///:memory:")


@pytest.fixture
async def session_with_row(engine: AsyncEngine) -> str:
    """Insert parent session row so child rows' FK holds (R2-4 共享 fixture)."""
    from src.storage.database import get_session
    from src.storage.models import Session as SessionModel
    async with get_session(engine) as db:
        db.add(SessionModel(id="sess-test", name="unit-test"))
        await db.commit()
    return "sess-test"
