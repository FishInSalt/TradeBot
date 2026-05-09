"""F7 OKX REST OHLCV helper — unit tests (mock-only, no live REST)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import ccxt
import pytest
import pytest_asyncio

from src.storage.database import init_db
from tests._sim_fixtures import make_session


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


# ===== _paginate_ohlcv tests =====


def _mock_client_returning_pages(pages: list[list[list]]) -> MagicMock:
    """Build a mock ccxt client whose fetch_ohlcv returns each page in sequence."""
    client = MagicMock()
    pages_iter = iter(pages)
    async def fake_fetch_ohlcv(symbol, timeframe, since, limit):
        try:
            return next(pages_iter)
        except StopIteration:
            return []
    client.fetch_ohlcv = AsyncMock(side_effect=fake_fetch_ohlcv)
    return client


def _make_candle_page(start_ms: int, count: int, tf_ms: int) -> list[list]:
    """Build `count` consecutive candles starting at start_ms with tf_ms cadence."""
    return [
        [start_ms + i * tf_ms, 80000.0, 80100.0, 79900.0, 80050.0, 1.5]
        for i in range(count)
    ]


async def test_paginate_basic_assembly_AC_F7_3(monkeypatch):
    """AC-F7-3: mock 按 since 偏移返回 100 条，任意非整百窗口拼接 + 单调递增."""
    from scripts.fetch_session_ohlcv import _paginate_ohlcv, TF_MS
    monkeypatch.setattr("asyncio.sleep", AsyncMock())  # 跳过节流 sleep

    tf_ms = TF_MS["1m"]
    start_ms = 1_700_000_000_000
    end_ms = start_ms + 250 * tf_ms  # 250 candles → 3 pages (100/100/50)
    pages = [
        _make_candle_page(start_ms + 0 * tf_ms, 100, tf_ms),
        _make_candle_page(start_ms + 100 * tf_ms, 100, tf_ms),
        _make_candle_page(start_ms + 200 * tf_ms, 50, tf_ms),
    ]
    client = _mock_client_returning_pages(pages)

    rows = await _paginate_ohlcv(client, "BTC/USDT:USDT", "1m", start_ms, end_ms)
    assert len(rows) == 250
    timestamps = [r[0] for r in rows]
    assert timestamps == sorted(timestamps)  # 单调递增
    assert timestamps[0] == start_ms
    assert timestamps[-1] == start_ms + 249 * tf_ms


async def test_paginate_short_return_AC_F7_9(monkeypatch):
    """AC-F7-9: 服务端少返回（50 而非 100），cursor 仍正确推进至覆盖完整窗口."""
    from scripts.fetch_session_ohlcv import _paginate_ohlcv, TF_MS
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    tf_ms = TF_MS["1m"]
    start_ms = 1_700_000_000_000
    end_ms = start_ms + 100 * tf_ms
    pages = [
        _make_candle_page(start_ms, 50, tf_ms),
        _make_candle_page(start_ms + 50 * tf_ms, 50, tf_ms),
    ]
    client = _mock_client_returning_pages(pages)

    rows = await _paginate_ohlcv(client, "BTC/USDT:USDT", "1m", start_ms, end_ms)
    assert len(rows) == 100


async def test_paginate_terminates_on_empty(monkeypatch):
    """spec §2.1 step 5: 本次返回为空 → 终止."""
    from scripts.fetch_session_ohlcv import _paginate_ohlcv, TF_MS
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    client = _mock_client_returning_pages([[]])
    rows = await _paginate_ohlcv(client, "BTC/USDT:USDT", "1m",
                                  1_700_000_000_000, 1_700_000_000_000 + 1000 * TF_MS["1m"])
    assert rows == []


async def test_paginate_terminates_on_stale_last_ts(monkeypatch):
    """spec §2.1 step 5: 末根 ts <= 上次末根 → 终止 (防卡死)."""
    from scripts.fetch_session_ohlcv import _paginate_ohlcv, TF_MS
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    tf_ms = TF_MS["1m"]
    start_ms = 1_700_000_000_000
    page1 = _make_candle_page(start_ms, 5, tf_ms)
    # Page 2 returns same last ts (服务端重复返回同一窗口)
    page2 = _make_candle_page(start_ms, 5, tf_ms)
    client = _mock_client_returning_pages([page1, page2])

    rows = await _paginate_ohlcv(client, "BTC/USDT:USDT", "1m", start_ms,
                                  start_ms + 10000 * tf_ms)
    # page1: 5 candles stored, last_seen_ts=ts4
    # page2: same 5 candles, last ts == ts4 → stale break
    # 严格 == 2 (放宽到 <= 3 会让"循环再多走一轮" regression 漏检)
    assert client.fetch_ohlcv.await_count == 2
    assert len(rows) == 5  # page1 stored, page2 not appended (stale break before extend)


async def test_paginate_transient_retry_succeeds_AC_F7_10(monkeypatch):
    """AC-F7-10: NetworkError 抛 2 次后第 3 次成功 → 函数返回正常结果, sleep=[1,2]."""
    from scripts.fetch_session_ohlcv import _paginate_ohlcv, TF_MS
    sleep_calls: list[float] = []
    async def fake_sleep(d): sleep_calls.append(d)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    tf_ms = TF_MS["1m"]
    start_ms = 1_700_000_000_000
    success_page = _make_candle_page(start_ms, 5, tf_ms)
    call_count = [0]
    async def flaky(symbol, timeframe, since, limit):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise ccxt.NetworkError("transient")
        return success_page
    client = MagicMock()
    client.fetch_ohlcv = AsyncMock(side_effect=flaky)

    rows = await _paginate_ohlcv(client, "BTC/USDT:USDT", "1m", start_ms,
                                  start_ms + 5 * tf_ms)
    assert len(rows) == 5
    # Filter out throttle sleeps (0.5s); retry sleeps are [1.0, 2.0]
    retry_sleeps = [s for s in sleep_calls if s in (1.0, 2.0)]
    assert retry_sleeps == [1.0, 2.0]


async def test_paginate_transient_exhaust_AC_F7_11(monkeypatch):
    """AC-F7-11: NetworkError 连抛 3 次 → raise, sleep=[1,2] (no tail sleep)."""
    from scripts.fetch_session_ohlcv import _paginate_ohlcv, TF_MS
    sleep_calls: list[float] = []
    async def fake_sleep(d): sleep_calls.append(d)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    client = MagicMock()
    client.fetch_ohlcv = AsyncMock(side_effect=ccxt.NetworkError("dead"))

    with pytest.raises(ccxt.NetworkError, match="dead"):
        await _paginate_ohlcv(client, "BTC/USDT:USDT", "1m",
                              1_700_000_000_000, 1_700_000_000_000 + 1000 * TF_MS["1m"])
    retry_sleeps = [s for s in sleep_calls if s in (1.0, 2.0)]
    assert retry_sleeps == [1.0, 2.0]  # 2 sleeps, not 3


async def test_paginate_permanent_no_retry_AC_F7_12(monkeypatch):
    """AC-F7-12: BadSymbol → 立即 raise (no retry, no sleep)."""
    from scripts.fetch_session_ohlcv import _paginate_ohlcv, TF_MS
    sleep_calls: list[float] = []
    async def fake_sleep(d): sleep_calls.append(d)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    client = MagicMock()
    client.fetch_ohlcv = AsyncMock(side_effect=ccxt.BadSymbol("nope"))

    with pytest.raises(ccxt.BadSymbol):
        await _paginate_ohlcv(client, "BTC/USDT:USDT", "1m",
                              1_700_000_000_000, 1_700_000_000_000 + 1000 * TF_MS["1m"])
    retry_sleeps = [s for s in sleep_calls if s in (1.0, 2.0)]
    assert retry_sleeps == []
    assert client.fetch_ohlcv.await_count == 1


# ===== AC-F7-4: timeframe parametrize drift guard =====

@pytest.mark.parametrize("tf,tf_ms", [
    ("1m", 60_000),
    ("5m", 300_000),
    ("15m", 900_000),
    ("1h", 3_600_000),
    ("4h", 14_400_000),
    ("1d", 86_400_000),
])
async def test_paginate_cursor_advances_by_tf_ms_AC_F7_4(monkeypatch, tf, tf_ms):
    """AC-F7-4: cursor_ms == 上页末根 ts + tf_ms (drift guard for TF_MS dict)."""
    from scripts.fetch_session_ohlcv import _paginate_ohlcv
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    start_ms = 1_700_000_000_000
    page1_last_ts = start_ms + 99 * tf_ms  # last candle of page 1
    page2_first_ts = page1_last_ts + tf_ms  # expected: cursor advances by tf_ms

    captured_since: list[int] = []
    async def capture_since(symbol, timeframe, since, limit):
        captured_since.append(since)
        if len(captured_since) == 1:
            return _make_candle_page(start_ms, 100, tf_ms)
        return []  # terminate after 2nd call

    client = MagicMock()
    client.fetch_ohlcv = AsyncMock(side_effect=capture_since)

    await _paginate_ohlcv(client, "BTC/USDT:USDT", tf, start_ms, start_ms + 1000 * tf_ms)

    assert captured_since[0] == start_ms
    assert captured_since[1] == page2_first_ts, (
        f"{tf}: cursor expected {page2_first_ts}, got {captured_since[1]}"
    )
