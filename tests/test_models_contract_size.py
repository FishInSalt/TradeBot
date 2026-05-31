import pytest
from sqlalchemy import select
from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel


@pytest.mark.asyncio
async def test_session_contract_size_roundtrip(tmp_path):
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with get_session(engine) as s:
        s.add(SessionModel(id="sess-1", name="t", symbol="BTC/USDT:USDT",
                           initial_balance=100.0, contract_size=0.01))
        await s.commit()
    async with get_session(engine) as s:
        row = (await s.execute(
            select(SessionModel.contract_size).where(SessionModel.id == "sess-1")
        )).scalar_one()
    assert row == 0.01


@pytest.mark.asyncio
async def test_session_contract_size_defaults_null(tmp_path):
    engine = await init_db(f"sqlite+aiosqlite:///{tmp_path}/t2.db")
    async with get_session(engine) as s:
        s.add(SessionModel(id="sess-2", name="t2", symbol="BTC/USDT:USDT", initial_balance=100.0))
        await s.commit()
    async with get_session(engine) as s:
        row = (await s.execute(
            select(SessionModel.contract_size).where(SessionModel.id == "sess-2")
        )).scalar_one()
    assert row is None   # 历史/未设置 → NULL（分析层 fallback 1.0）
