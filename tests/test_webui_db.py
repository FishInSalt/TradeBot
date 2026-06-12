import pytest
from sqlalchemy import text

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel
from src.webui.db import make_readonly_engine


@pytest.mark.asyncio
async def test_readonly_engine_reads_committed_data(tmp_path):
    db_file = tmp_path / "t.db"
    wengine = await init_db(f"sqlite+aiosqlite:///{db_file}")
    async with get_session(wengine) as s:
        s.add(SessionModel(id="sess-1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0))
        await s.commit()

    ro = make_readonly_engine(str(db_file))
    async with ro.connect() as conn:
        row = (await conn.execute(text("SELECT name FROM sessions WHERE id='sess-1'"))).first()
        assert row[0] == "n1"


@pytest.mark.asyncio
async def test_readonly_engine_rejects_write(tmp_path):
    db_file = tmp_path / "t2.db"
    await init_db(f"sqlite+aiosqlite:///{db_file}")
    ro = make_readonly_engine(str(db_file))
    with pytest.raises(Exception):
        async with ro.connect() as conn:
            await conn.execute(text("INSERT INTO sessions(id,name,symbol,initial_balance,status) "
                                    "VALUES('x','x','BTC',1,'active')"))
            await conn.commit()
