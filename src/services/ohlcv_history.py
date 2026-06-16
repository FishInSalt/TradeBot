"""OHLCV 拉取共享核心：会话窗口解析 + OKX REST 分页 + 重试 + 排序去重 + 半开过滤。

从 scripts/fetch_session_ohlcv.py（F7）上提，供 webui 复用（spec §A）。后续脚本侧将按旧私名
re-export 本模块以零破坏既有测试；CLI / CSV / DataFrame 落盘仍留在脚本。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import ccxt
import ccxt.async_support
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.storage.models import Session as SessionModel


# spec §2.2 + §3.4：硬编码 dict 不走 ccxt.parse_timeframe；AC-F7-4 drift guard 锁定数值。
TF_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")

_RETRY_SLEEP_SCHEDULE: tuple[float, ...] = (1.0, 2.0)  # 2 sleeps; raise 後不再 sleep
_THROTTLE_SLEEP_S: float = 0.5
_PAGE_LIMIT: int = 100  # OKX REST default; max 300, 100 conservative for rate limit


def _ensure_utc(dt: datetime) -> datetime:
    """aiosqlite 讀出的 DateTime(timezone=True) 是 naive；顯式補 UTC tzinfo。"""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def resolve_session_window(engine: AsyncEngine, session_id: str) -> tuple[str, int, int]:
    """查 sim 會話；返回 [created_at, last_active_at) 的 (symbol, start_ms, end_ms)。

    session_id 不存在 / 零時長 → ValueError。last_active_at NULL 回退 updated_at
    (NOT NULL，ORM default+onupdate 必填)。

    只借用傳入 engine（經 sessionmaker 開 AsyncSession 查詢）、**不 dispose**——webui
    路徑傳的是共享只讀 engine，絕不能被關。
    """
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
        row = result.scalars().first()
    if row is None:
        raise ValueError(f"session not found: {session_id}")

    end_dt = row.last_active_at if row.last_active_at is not None else row.updated_at
    start_ms = int(_ensure_utc(row.created_at).timestamp() * 1000)
    end_ms = int(_ensure_utc(end_dt).timestamp() * 1000)
    if end_ms <= start_ms:
        raise ValueError(f"session has zero duration: {session_id}")
    return row.symbol, start_ms, end_ms


async def _fetch_with_retry(client, symbol: str, timeframe: str, since_ms: int) -> list[list]:
    """3 attempts total; sleep [1.0, 2.0] between failures; no tail sleep。"""
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return await client.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=_PAGE_LIMIT)
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, asyncio.TimeoutError) as e:
            last_err = e
            if attempt < 2:  # 前 2 次失敗 sleep；第 3 次失敗直接 raise
                await asyncio.sleep(_RETRY_SLEEP_SCHEDULE[attempt])
    assert last_err is not None
    raise last_err


async def _paginate_ohlcv(
    client, symbol: str, timeframe: str, start_ms: int, end_ms: int
) -> list[list]:
    """從 start_ms 向前分頁直到 end_ms。游標按末根 ts + tf_ms 前進；
    終止：游標≥end / 空頁 / 末根不前進。"""
    tf_ms = TF_MS[timeframe]
    cursor_ms = start_ms
    last_seen_ts: int | None = None
    rows: list[list] = []

    while cursor_ms < end_ms:
        page = await _fetch_with_retry(client, symbol, timeframe, cursor_ms)
        if not page:
            break
        page_last_ts = page[-1][0]
        if last_seen_ts is not None and page_last_ts <= last_seen_ts:
            break  # 末根不前進 → 防卡死
        rows.extend(page)
        last_seen_ts = page_last_ts
        cursor_ms = page_last_ts + tf_ms
        await asyncio.sleep(_THROTTLE_SLEEP_S)

    return rows


async def fetch_ohlcv_window(
    symbol: str, timeframe: str, start_ms: int, end_ms: int
) -> list[list]:
    """拉取 [start_ms, end_ms) 的 OKX REST OHLCV，返回升序裸行 list[list]。

    client = ccxt.async_support.okx()（**屬性形式調用**——F7 測試 monkeypatch.setattr
    全局屬性 patch，綁名 import 會繞過）。try 內分頁 + sort + 同 ts 去重 + 半開過濾；
    finally 必 close（**異常路徑也 close**，守 AC-F7-14）。ccxt okx 默認 timeout=10000
    (10s)，不顯式傳以保 F7 客戶端構造零行為變化。

    窗口內無數據 → []；重試耗盡的瞬態錯 re-raise。
    """
    client = ccxt.async_support.okx()
    try:
        rows = await _paginate_ohlcv(client, symbol, timeframe, start_ms, end_ms)
        rows.sort(key=lambda r: r[0])
        seen: set[int] = set()
        deduped: list[list] = []
        for r in rows:
            ts = r[0]
            if ts in seen:
                continue
            seen.add(ts)
            deduped.append(r)
        return [r for r in deduped if start_ms <= r[0] < end_ms]
    finally:
        await client.close()
