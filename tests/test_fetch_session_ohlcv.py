"""F7 OKX REST OHLCV helper — unit tests (mock-only, no live REST)."""
from __future__ import annotations

import pytest


# ===== TF_MS drift guard (AC-F7-4 配套) =====

EXPECTED_TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

EXPECTED_TIMEFRAMES = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})


def test_tf_ms_dict_matches_expected():
    """spec §2.2 决议：tf_ms 硬编码 dict（不走 ccxt.parse_timeframe），本测试是 drift guard。"""
    from scripts.fetch_session_ohlcv import TF_MS
    assert TF_MS == EXPECTED_TF_MS, f"TF_MS drift: {TF_MS} vs {EXPECTED_TF_MS}"


def test_timeframes_whitelist_matches_tf_ms_keys():
    """spec §3.4：TIMEFRAMES 白名单与 TF_MS 同位维护。"""
    from scripts.fetch_session_ohlcv import TIMEFRAMES, TF_MS
    assert frozenset(TIMEFRAMES) == frozenset(TF_MS.keys()) == EXPECTED_TIMEFRAMES


# ===== _resolve_session tests =====

import pytest_asyncio
from src.storage.database import init_db
from tests._sim_fixtures import make_session


@pytest_asyncio.fixture
async def engine():
    """Local in-memory engine + schema (mirror tests/conftest.py:26-29)."""
    e = await init_db("sqlite+aiosqlite:///:memory:")
    yield e
    await e.dispose()


async def test_resolve_session_not_found_raises(engine):
    """AC-F7-1: session_id 不存在 → ValueError."""
    from scripts.fetch_session_ohlcv import _resolve_session
    with pytest.raises(ValueError, match="session not found"):
        await _resolve_session(engine, "nonexistent-uuid")


async def test_resolve_session_zero_duration_raises(engine):
    """AC-F7-2: time window = 0 → ValueError.

    NOTE: aiosqlite raw SQL 读 DateTime 返回 str（不是 datetime），不能 +timedelta，
    也不能直接写回 last_active_at。改为直接用已知 aware datetime 构造 + UPDATE。
    """
    from datetime import datetime, timezone
    from sqlalchemy import update
    from scripts.fetch_session_ohlcv import _resolve_session
    from src.storage.models import Session as SessionModel

    known_start = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    sid = await make_session(engine, name="zero_dur", created_at=known_start)
    async with engine.begin() as conn:
        await conn.execute(
            update(SessionModel).where(SessionModel.id == sid).values(last_active_at=known_start)
        )
    with pytest.raises(ValueError, match="zero duration"):
        await _resolve_session(engine, sid)


async def test_resolve_session_returns_tuple(engine):
    """Happy path: returns (symbol, start_ms, end_ms)."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from scripts.fetch_session_ohlcv import _resolve_session
    from src.storage.models import Session as SessionModel

    known_start = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    end_dt = known_start + timedelta(hours=2)
    sid = await make_session(engine, name="happy", symbol="BTC/USDT:USDT",
                              created_at=known_start)
    async with engine.begin() as conn:
        await conn.execute(
            update(SessionModel).where(SessionModel.id == sid).values(last_active_at=end_dt)
        )

    symbol, start_ms, end_ms = await _resolve_session(engine, sid)
    assert symbol == "BTC/USDT:USDT"
    assert end_ms - start_ms == 2 * 60 * 60 * 1000  # 2h in ms
    assert isinstance(start_ms, int) and isinstance(end_ms, int)


async def test_resolve_session_falls_back_to_updated_at(engine):
    """spec §4: last_active_at None → fallback updated_at (NOT NULL, must exist).

    显式 bump updated_at 让 fallback 路径可观测：make_session INSERT 时 _utcnow() 默认
    给 updated_at 一个 ≈now() 时间，比 fixture 用的未来 created_at (2026-05-15) 早 →
    无 explicit update 会 end_dt < start_dt → zero_duration raise，无法验证 fallback。
    """
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from scripts.fetch_session_ohlcv import _resolve_session
    from src.storage.models import Session as SessionModel

    known_start = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    later = known_start + timedelta(minutes=30)
    sid = await make_session(engine, name="fallback", created_at=known_start)
    # last_active_at 留 None；显式 bump updated_at 让 fallback 路径可观测
    async with engine.begin() as conn:
        await conn.execute(
            update(SessionModel).where(SessionModel.id == sid).values(updated_at=later)
        )
    _, start_ms, end_ms = await _resolve_session(engine, sid)
    assert end_ms - start_ms == 30 * 60 * 1000, f"fallback path not used: {end_ms-start_ms}ms"


async def test_resolve_session_epoch_accuracy_AC_F7_3a(engine):
    """P0 #3 修订：aiosqlite 读出 naive datetime，需补 UTC tzinfo；否则
    Asia/Shanghai 系统会按 CST 解释偏 8 小时。本测试对照已知 UTC datetime 验证 ms 精确性."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from scripts.fetch_session_ohlcv import _resolve_session
    from src.storage.models import Session as SessionModel

    # 已知 epoch ms (verify: int(datetime(2026,5,15,12,0,0,tzinfo=UTC).timestamp()*1000))
    known_start = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    known_end = known_start + timedelta(hours=1)  # +3_600_000 ms

    sid = await make_session(engine, name="epoch_check", created_at=known_start)
    async with engine.begin() as conn:
        await conn.execute(
            update(SessionModel).where(SessionModel.id == sid).values(last_active_at=known_end)
        )
    _, start_ms, end_ms = await _resolve_session(engine, sid)
    assert start_ms == 1778846400000, f"start_ms drift (likely tzinfo bug): {start_ms}"
    assert end_ms - start_ms == 3_600_000, f"window != 1h: {end_ms - start_ms}ms"
