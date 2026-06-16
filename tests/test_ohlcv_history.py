"""ohlcv_history 共享核心——单元测试（mock ccxt，无 live REST）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import ccxt
import pytest
import pytest_asyncio

from src.storage.database import init_db
from src.storage.models import Session as SessionModel
from tests._sim_fixtures import make_session


EXPECTED_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000,
                  "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def test_tf_ms_drift_guard():
    """drift guard：TF_MS / TIMEFRAMES 数值锁定（沿用 F7 AC-F7-4）。"""
    from src.services.ohlcv_history import TF_MS, TIMEFRAMES
    assert TF_MS == EXPECTED_TF_MS
    assert frozenset(TIMEFRAMES) == frozenset(TF_MS.keys())


@pytest_asyncio.fixture
async def engine():
    e = await init_db("sqlite+aiosqlite:///:memory:")
    yield e
    await e.dispose()


def _page(start_ms: int, count: int, tf_ms: int) -> list[list]:
    return [[start_ms + i * tf_ms, 80000.0, 80100.0, 79900.0, 80050.0, 1.5]
            for i in range(count)]


async def test_resolve_session_window_returns_tuple(engine):
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from src.services.ohlcv_history import resolve_session_window

    start = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    sid = await make_session(engine, name="happy", symbol="BTC/USDT:USDT", created_at=start)
    async with engine.begin() as conn:
        await conn.execute(update(SessionModel).where(SessionModel.id == sid)
                           .values(last_active_at=start + timedelta(hours=2)))
    symbol, start_ms, end_ms = await resolve_session_window(engine, sid)
    assert symbol == "BTC/USDT:USDT"
    assert end_ms - start_ms == 2 * 60 * 60 * 1000


async def test_resolve_session_window_not_found_raises(engine):
    from src.services.ohlcv_history import resolve_session_window
    with pytest.raises(ValueError, match="session not found"):
        await resolve_session_window(engine, "nope")


async def test_resolve_session_window_does_not_dispose_engine(engine):
    """借用 engine 不 dispose——webui 共享只读 engine 绝不能被关。
    resolve 后 engine 仍可用 = 未被 dispose。"""
    from datetime import datetime, timezone
    from src.services.ohlcv_history import resolve_session_window
    sid = await make_session(engine, name="nodispose",
                             created_at=datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc))
    try:
        await resolve_session_window(engine, sid)
    except ValueError:
        pass  # 零时长无所谓，只验 engine 未被关
    async with engine.connect() as conn:  # 未 dispose → 仍能开连接
        assert conn is not None


async def test_fetch_ohlcv_window_sort_dedup_halfopen(monkeypatch):
    """分页拼接 + 排序 + 同 ts 去重 + 半开过滤（越界根剔除）；返回裸行 list[list]。"""
    from src.services.ohlcv_history import fetch_ohlcv_window, TF_MS
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    tf_ms = TF_MS["1m"]
    start = 1_700_000_000_000
    end = start + 60 * tf_ms
    pages = iter([
        _page(start, 50, tf_ms),                 # ts: start..start+49
        _page(start + 45 * tf_ms, 35, tf_ms),    # 5 重叠 (45-49) + 越界 (到 start+79)
        [],                                       # 终止
    ])
    client = MagicMock()
    client.fetch_ohlcv = AsyncMock(side_effect=lambda *a, **k: next(pages, []))
    client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **k: client)

    rows = await fetch_ohlcv_window("BTC/USDT:USDT", "1m", start, end)
    ts = [r[0] for r in rows]
    assert ts == sorted(ts)                       # 升序
    assert len(ts) == len(set(ts))                # 去重
    assert all(start <= t < end for t in ts)      # 半开
    assert len(ts) == 60                           # 去重证据：85 raw 含 5 窗内 dup，未去重会得 65
    assert client.close.await_count == 1


async def test_fetch_ohlcv_window_empty(monkeypatch):
    from src.services.ohlcv_history import fetch_ohlcv_window
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = MagicMock()
    client.fetch_ohlcv = AsyncMock(return_value=[])
    client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **k: client)
    rows = await fetch_ohlcv_window("BTC/USDT:USDT", "1m", 1_700_000_000_000,
                                    1_700_000_000_000 + 10 * 60_000)
    assert rows == []
    assert client.close.await_count == 1


async def test_fetch_ohlcv_window_closes_on_exception(monkeypatch):
    """异常路径也 close（守 AC-F7-14 等价语义）。"""
    from src.services.ohlcv_history import fetch_ohlcv_window
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    client = MagicMock()
    client.fetch_ohlcv = AsyncMock(side_effect=ccxt.BadSymbol("nope"))
    client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **k: client)
    with pytest.raises(ccxt.BadSymbol):
        await fetch_ohlcv_window("BTC/USDT:USDT", "1m", 1_700_000_000_000,
                                 1_700_000_000_000 + 10 * 60_000)
    assert client.close.await_count == 1
