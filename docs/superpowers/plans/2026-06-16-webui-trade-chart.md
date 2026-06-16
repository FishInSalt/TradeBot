# WebUI 会话价格 K 线 + 买卖点可视化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在「收益分析」抽屉内新增一个价格 K 线图，加载会话运行窗口内标的的真实 OKX OHLCV，并把交易记录的入场/出场点叠加为 markers（与 A+ 历程表同一 `deriveTradeFills` 口径）。

**Architecture:** 后端把 F7 脚本（`scripts/fetch_session_ohlcv.py`）的拉取核心上提为共享模块 `src/services/ohlcv_history.py`（脚本按旧私名 re-export、零破坏既有测试）；新增文件缓存 `src/webui/ohlcv_cache.py`（缓存目录从正在使用的只读 engine 派生）+ query `get_ohlcv` + 端点 `GET /api/sessions/{sid}/ohlcv`。前端新增纯函数 `markers.ts` + 组件 `PriceChart.vue`，挂进 `PerformanceBar.vue` 抽屉。无 DB 迁移、不动 `MetricsService`。

**Tech Stack:** Python / FastAPI / SQLAlchemy async / ccxt.async_support / pytest；Vue 3 + TypeScript + lightweight-charts ^4.2.0（v4 API）+ naive-ui 2.38.1（pin）+ vitest + Playwright。

参考 spec：`docs/superpowers/specs/2026-06-16-webui-trade-chart-design.md`。

---

## 文件结构

**后端新增/改：**
- Create `src/services/ohlcv_history.py` — 拉取共享核心（窗口解析 + 分页 + 重试 + 排序去重 + 半开过滤 + 常量）。
- Modify `scripts/fetch_session_ohlcv.py` — 改为 import 共享核心 + 按旧私名 re-export；`_to_dataframe`/`_write_csv`/CLI 留下。
- Create `src/webui/ohlcv_cache.py` — 文件层缓存（`cache_dir_for`/`read`/`write`）。
- Modify `src/webui/schemas.py` — 新增 `OhlcvBar` + `OhlcvSeries`。
- Modify `src/webui/queries.py` — 新增 `get_ohlcv` + `InvalidTimeframe` 异常。
- Modify `src/webui/app.py` — 新增端点。

**后端测试：**
- Create `tests/test_ohlcv_history.py`、`tests/test_ohlcv_cache.py`。
- Modify `tests/test_webui_queries.py`、`tests/test_webui_api.py`（追加用例）。
- `tests/test_fetch_session_ohlcv.py` — **一行不改**，作零破坏回归。

**前端新增/改：**
- Create `frontend/src/utils/markers.ts`。
- Create `frontend/src/components/PriceChart.vue`。
- Modify `frontend/src/components/PerformanceBar.vue`、`frontend/src/api/client.ts`、`frontend/openapi.json`、`frontend/src/api/types.ts`。

**前端测试：**
- Create `frontend/test/markers.spec.ts`、`frontend/test/PriceChart.spec.ts`。
- Modify `frontend/test/PerformanceBar.spec.ts`。

---

## Task 1: 后端共享核心 `ohlcv_history.py`

**Files:**
- Create: `src/services/ohlcv_history.py`
- Test: `tests/test_ohlcv_history.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_ohlcv_history.py`：

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_ohlcv_history.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.services.ohlcv_history'`。

- [ ] **Step 3: 写实现**

Create `src/services/ohlcv_history.py`：

```python
"""OHLCV 拉取共享核心：会话窗口解析 + OKX REST 分页 + 重试 + 排序去重 + 半开过滤。

从 scripts/fetch_session_ohlcv.py（F7）上提，供 webui 复用（spec §A）。脚本侧按旧私名
re-export 以零破坏既有测试；CLI / CSV / DataFrame 落盘仍留在脚本。
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

_RETRY_SLEEP_SCHEDULE: tuple[float, ...] = (1.0, 2.0)  # 2 sleeps; raise 后不再 sleep
_THROTTLE_SLEEP_S: float = 0.5
_PAGE_LIMIT: int = 100  # OKX REST default; max 300, 100 conservative for rate limit


def _ensure_utc(dt: datetime) -> datetime:
    """aiosqlite 读出的 DateTime(timezone=True) 是 naive；显式补 UTC tzinfo。"""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def resolve_session_window(engine: AsyncEngine, session_id: str) -> tuple[str, int, int]:
    """查 sim 会话；返回 [created_at, last_active_at) 的 (symbol, start_ms, end_ms)。

    session_id 不存在 / 零时长 → ValueError。last_active_at NULL 回退 updated_at
    (NOT NULL，ORM default+onupdate 必填)。

    只借用传入 engine（经 sessionmaker 开 AsyncSession 查询）、**不 dispose**——webui
    路径传的是共享只读 engine，绝不能被关。
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
            if attempt < 2:  # 前 2 次失败 sleep；第 3 次失败直接 raise
                await asyncio.sleep(_RETRY_SLEEP_SCHEDULE[attempt])
    assert last_err is not None
    raise last_err


async def _paginate_ohlcv(
    client, symbol: str, timeframe: str, start_ms: int, end_ms: int
) -> list[list]:
    """从 start_ms 向前分页直到 end_ms。游标按末根 ts + tf_ms 前进；
    终止：游标≥end / 空页 / 末根不前进。"""
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
            break  # 末根不前进 → 防卡死
        rows.extend(page)
        last_seen_ts = page_last_ts
        cursor_ms = page_last_ts + tf_ms
        await asyncio.sleep(_THROTTLE_SLEEP_S)

    return rows


async def fetch_ohlcv_window(
    symbol: str, timeframe: str, start_ms: int, end_ms: int
) -> list[list]:
    """拉取 [start_ms, end_ms) 的 OKX REST OHLCV，返回升序裸行 list[list]。

    client = ccxt.async_support.okx()（**属性形式调用**——F7 测试 monkeypatch.setattr
    全局属性 patch，绑名 import 会绕过）。try 内分页 + sort + 同 ts 去重 + 半开过滤；
    finally 必 close（**异常路径也 close**，守 AC-F7-14）。ccxt okx 默认 timeout=10000
    (10s)，不显式传以保 F7 客户端构造零行为变化。

    窗口内无数据 → []；重试耗尽的瞬态错 re-raise。
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_ohlcv_history.py -q`
Expected: PASS（7 passed）。

- [ ] **Step 5: 提交**

```bash
git add src/services/ohlcv_history.py tests/test_ohlcv_history.py
git commit -m "feat(ohlcv): 上提 F7 拉取核心为 ohlcv_history 共享模块"
```

---

## Task 2: F7 脚本零破坏改造（import 共享核心 + 旧私名 re-export）

**Files:**
- Modify: `scripts/fetch_session_ohlcv.py`
- Regression: `tests/test_fetch_session_ohlcv.py`（**一行不改**）

- [ ] **Step 1: 改脚本顶部——删除已上提的定义，改为 import + re-export**

把 `scripts/fetch_session_ohlcv.py` 顶部（第 6–115 行，即 import 块 + `TF_MS`/`TIMEFRAMES`/`_ensure_utc`/`_resolve_session`/`_RETRY_*`/`_THROTTLE_*`/`_PAGE_LIMIT`/`_paginate_ohlcv`/`_fetch_with_retry`）整段替换为：

```python
"""F7 — OKX REST OHLCV post-hoc helper for sim sessions.

See docs/superpowers/specs/2026-05-09-iter-w2r2-obs-followup-a-design.md
for full spec including resource contract, retry semantics, and AC list.

拉取核心已上提至 src/services/ohlcv_history.py（spec 2026-06-16 §A）；下方按旧私名
re-export 以保既有测试 import 不动。本脚本仅留 DataFrame / CSV / CLI。
"""
from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.storage.models import Session as SessionModel

# 旧私名 re-export：使 `from scripts.fetch_session_ohlcv import _resolve_session,
# _paginate_ohlcv, TF_MS, TIMEFRAMES` 的存量测试 import 完全不动。
from src.services.ohlcv_history import (  # noqa: F401  (re-export)
    TF_MS,
    TIMEFRAMES,
    _ensure_utc,
    _fetch_with_retry,
    _paginate_ohlcv,
    fetch_ohlcv_window,
    resolve_session_window as _resolve_session,
)
```

> 注：`_RETRY_SLEEP_SCHEDULE` / `_THROTTLE_SLEEP_S` / `_PAGE_LIMIT` 不被任何测试按 `scripts.*` 路径 import（已 grep 确认仅 `ohlcv_history` 内部用），无需 re-export。

- [ ] **Step 2: 改脚本主入口 `fetch_session_ohlcv`——委托拉取核心**

把 `fetch_session_ohlcv`（原第 151–191 行）的函数体替换为（docstring 保留原文不动）：

```python
async def fetch_session_ohlcv(
    session_id: str,
    timeframe: str = "1m",
    db_path: str = "data/tradebot.db",
    output_path: Path | None = None,
) -> pd.DataFrame:
    """Fetch OKX REST OHLCV for a sim session's [created_at, last_active_at) window.

    Returns: DataFrame；window 内 OKX 无数据时返回空（不抛）。完整 schema 与
             空返回契约见 spec §3.2 / §4 表。

    Raises: ValueError if session_id not found or window has zero duration.
            Re-raises ccxt errors after retry exhaustion (transient) or
            immediately (permanent — BadSymbol etc).
    """
    assert timeframe in TF_MS, f"unsupported timeframe: {timeframe}"

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        symbol, start_ms, end_ms = await _resolve_session(engine, session_id)
        # 排序 / 去重 / 半开过滤已在 fetch_ohlcv_window 内完成；client.close 也在其 finally。
        rows = await fetch_ohlcv_window(symbol, timeframe, start_ms, end_ms)
        df = _to_dataframe(rows)
        if output_path is not None:
            _write_csv(df, output_path)
        return df
    finally:
        await engine.dispose()
```

> `_to_dataframe` / `_DTYPE_SCHEMA` / `_write_csv` / `_sanitize_*` / `_build_default_output_path` / `main` 一行不改，留在脚本原位。`main` 内仍用 `create_async_engine` + `sessionmaker` 解析默认输出路径（保留 import）。

- [ ] **Step 3: 跑 F7 回归——必须原样全绿**

Run: `python -m pytest tests/test_fetch_session_ohlcv.py -q`
Expected: PASS（全部 passed，含 `test_fetch_resource_cleanup_success_AC_F7_13`：close×1 + dispose×1；`test_fetch_resource_cleanup_on_exception_AC_F7_14`：异常仍 close+dispose；`test_to_dataframe_schema_AC_F7_5`；半开/去重/CSV 等）。

> 关键不变量：client.close 现落在 `fetch_ohlcv_window` 的 finally、engine.dispose 仍在脚本主入口，两测端到端计数仍各 1。`ccxt.async_support.okx` 全局 patch 命中 `fetch_ohlcv_window` 内的属性调用。

- [ ] **Step 4: 提交**

```bash
git add scripts/fetch_session_ohlcv.py
git commit -m "refactor(f7): 复用 ohlcv_history 共享核心 + 旧私名 re-export（零破坏）"
```

---

## Task 3: 文件缓存 `ohlcv_cache.py`

**Files:**
- Create: `src/webui/ohlcv_cache.py`
- Test: `tests/test_ohlcv_cache.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_ohlcv_cache.py`：

```python
"""ohlcv_cache 文件缓存——单元测试。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from src.webui import ohlcv_cache
from src.webui.db import make_readonly_engine


def test_cache_dir_for_readonly_engine_strips_file_prefix(tmp_path):
    """只读 engine .database='file:/abs/x.db' → 剥 file: → <abs 父>/ohlcv_cache。"""
    db = tmp_path / "tradebot.db"
    db.write_text("")  # make_readonly_engine 用 abspath，不要求文件存在，但建之无害
    eng = make_readonly_engine(str(db))
    assert ohlcv_cache.cache_dir_for(eng) == tmp_path / "ohlcv_cache"


def test_cache_dir_for_plain_file():
    eng = create_async_engine("sqlite+aiosqlite:////tmp/x.db")
    assert ohlcv_cache.cache_dir_for(eng) == Path("/tmp/ohlcv_cache")


def test_cache_dir_for_memory_returns_none():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    assert ohlcv_cache.cache_dir_for(eng) is None


def test_read_write_roundtrip(tmp_path):
    cache_dir = tmp_path / "ohlcv_cache"
    bars = [[1_700_000_000_000, 1.0, 2.0, 0.5, 1.5, 10.0]]
    ohlcv_cache.write(cache_dir, "sid1", "1h", "BTC/USDT:USDT", 1_700_000_060_000, bars)
    # 覆盖判定：current_end <= fetched_end → 命中
    assert ohlcv_cache.read(cache_dir, "sid1", "1h", 1_700_000_060_000) == bars
    assert ohlcv_cache.read(cache_dir, "sid1", "1h", 1_700_000_000_000) == bars  # 更早也命中
    # current_end > fetched_end（活跃会话窗口增长）→ miss
    assert ohlcv_cache.read(cache_dir, "sid1", "1h", 1_700_000_120_000) is None


def test_read_missing_file_returns_none(tmp_path):
    assert ohlcv_cache.read(tmp_path / "ohlcv_cache", "nope", "1h", 1) is None


def test_read_write_none_cache_dir_noop():
    """cache_dir None（内存库降级）→ read None / write no-op，不抛。"""
    assert ohlcv_cache.read(None, "sid", "1h", 1) is None
    ohlcv_cache.write(None, "sid", "1h", "BTC/USDT:USDT", 1, [[1, 1, 1, 1, 1, 1]])  # 不抛
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_ohlcv_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.webui.ohlcv_cache'`。

- [ ] **Step 3: 写实现**

Create `src/webui/ohlcv_cache.py`：

```python
"""OHLCV 文件缓存（不写库）。缓存目录从正在使用的只读 engine 派生（spec §B）。

历史 sim 窗口固定永不过期，故缓存无 TTL，只靠 fetched_end_ms 覆盖判定：
current_end_ms <= fetched_end_ms 命中（已结束会话恒命中），> 则 miss（活跃会话窗口增长）。
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine


def cache_dir_for(engine: AsyncEngine) -> Path | None:
    """从 engine.url.database 派生缓存目录；None / :memory: / 空 → None（降级不缓存）。

    为何从 engine 派生而非新增 app.state.db_path：端点测试 create_app() 默认 data/
    tradebot.db + dependency_overrides 注入内存 engine；从 engine 派生才天然跟随
    override（内存 → None，不污染真 data/）。spec §B 否决论证。
    """
    db = engine.url.database
    if not db or db == ":memory:":
        return None
    db = db.removeprefix("file:").split("?", 1)[0]
    return Path(db).parent / "ohlcv_cache"


def _cache_file(cache_dir: Path, sid: str, tf: str) -> Path:
    return cache_dir / f"{sid}_{tf}.json"


def read(cache_dir: Path | None, sid: str, tf: str, current_end_ms: int) -> list[list] | None:
    """命中（文件存在 且 current_end_ms <= fetched_end_ms）→ 裸行；否则 None。"""
    if cache_dir is None:
        return None
    path = _cache_file(cache_dir, sid, tf)
    if not path.is_file():
        return None
    blob = json.loads(path.read_text())
    if current_end_ms <= blob["fetched_end_ms"]:
        return blob["bars"]
    return None


def write(cache_dir: Path | None, sid: str, tf: str, symbol: str,
          fetched_end_ms: int, bars: list[list]) -> None:
    """落盘 <sid>_<tf>.json（mkdir parents + 覆盖写）。cache_dir None → no-op。"""
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {"symbol": symbol, "timeframe": tf,
               "fetched_end_ms": fetched_end_ms, "bars": bars}
    _cache_file(cache_dir, sid, tf).write_text(json.dumps(payload))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_ohlcv_cache.py -q`
Expected: PASS（6 passed）。

- [ ] **Step 5: 提交**

```bash
git add src/webui/ohlcv_cache.py tests/test_ohlcv_cache.py
git commit -m "feat(webui): OHLCV 文件缓存（engine 派生目录 + 覆盖判定）"
```

---

## Task 4: schema `OhlcvBar` + `OhlcvSeries`

**Files:**
- Modify: `src/webui/schemas.py`
- Test: `tests/test_webui_api.py`（追加 schema 可构造断言）

- [ ] **Step 1: 写失败测试**

在 `tests/test_webui_api.py` 末尾追加：

```python
def test_ohlcv_schemas_importable():
    from src.webui import schemas
    bar = schemas.OhlcvBar(at=datetime(2026, 6, 12, 10, 0, tzinfo=UTC),
                           open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)
    s = schemas.OhlcvSeries(symbol="BTC/USDT:USDT", timeframe="1h", bars=[bar])
    dumped = s.model_dump()
    assert dumped["timeframe"] == "1h"
    assert dumped["bars"][0]["open"] == 1.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_webui_api.py::test_ohlcv_schemas_importable -q`
Expected: FAIL — `AttributeError: module 'src.webui.schemas' has no attribute 'OhlcvBar'`。

- [ ] **Step 3: 写实现**

在 `src/webui/schemas.py` 的 `EquityPoint` 类之后插入：

```python
class OhlcvBar(BaseModel):
    at: UtcDatetime          # 该 K 线开盘时刻（ts_ms → aware UTC）
    open: float
    high: float
    low: float
    close: float
    volume: float


class OhlcvSeries(BaseModel):
    symbol: str
    timeframe: str            # 归一后的小写形态（1h 等）
    bars: list[OhlcvBar]      # 升序、同 ts 去重、半开过滤；窗口内无数据 → []
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_webui_api.py::test_ohlcv_schemas_importable -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/webui/schemas.py tests/test_webui_api.py
git commit -m "feat(webui): OhlcvBar / OhlcvSeries schema"
```

---

## Task 5: query `get_ohlcv` + `InvalidTimeframe`

**Files:**
- Modify: `src/webui/queries.py`
- Test: `tests/test_webui_queries.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_webui_queries.py` 末尾追加（文件顶部已有 `import pytest` / `get_session` / `SessionModel` / `datetime,timezone,timedelta`）：

```python
from unittest.mock import AsyncMock


async def _seed_session_with_window(engine, sid, *, timeframe, hours=2,
                                    symbol="BTC/USDT:USDT"):
    start = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id=sid, name=sid, symbol=symbol, initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, timeframe=timeframe,
                           created_at=start, last_active_at=start + timedelta(hours=hours)))
        await s.commit()


async def test_get_ohlcv_fetches_and_builds_series(engine, monkeypatch):
    """miss → 调 fetch + 返回 OhlcvSeries（裸行 → OhlcvBar，aware UTC）。内存 engine → cache_dir None。"""
    from src.webui import queries, ohlcv_cache
    await _seed_session_with_window(engine, "s1", timeframe="1h")
    bars = [[1_778_846_400_000, 1.0, 2.0, 0.5, 1.5, 10.0]]   # ts = start_ms（2026-05-15 12:00 UTC）
    fetch = AsyncMock(return_value=bars)
    monkeypatch.setattr(queries, "fetch_ohlcv_window", fetch)
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: None)   # 内存库降级：每次实拉

    series = await queries.get_ohlcv(engine, "s1", None)  # 默认 tf=会话 1h
    assert series.timeframe == "1h"
    assert series.symbol == "BTC/USDT:USDT"
    assert len(series.bars) == 1
    assert series.bars[0].at.tzinfo is not None              # aware UTC
    assert fetch.await_count == 1


async def test_get_ohlcv_cache_hit_skips_fetch(engine, tmp_path, monkeypatch):
    from src.webui import queries, ohlcv_cache
    await _seed_session_with_window(engine, "s1", timeframe="1h")
    cache_dir = tmp_path / "ohlcv_cache"
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: cache_dir)
    fetch = AsyncMock(return_value=[[1_778_846_400_000, 1.0, 2.0, 0.5, 1.5, 10.0]])
    monkeypatch.setattr(queries, "fetch_ohlcv_window", fetch)

    await queries.get_ohlcv(engine, "s1", None)              # miss → fetch + write
    await queries.get_ohlcv(engine, "s1", None)              # hit → 不再 fetch
    assert fetch.await_count == 1


async def test_get_ohlcv_empty_window(engine, monkeypatch):
    from src.webui import queries, ohlcv_cache
    await _seed_session_with_window(engine, "s1", timeframe="1h")
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: None)
    monkeypatch.setattr(queries, "fetch_ohlcv_window", AsyncMock(return_value=[]))
    series = await queries.get_ohlcv(engine, "s1", None)
    assert series.bars == []


async def test_get_ohlcv_default_uppercase_1H_normalized(engine, monkeypatch):
    """默认路径会话 timeframe='1H' → 归一 1h，不抛、timeframe=='1h'。"""
    from src.webui import queries, ohlcv_cache
    await _seed_session_with_window(engine, "s1", timeframe="1H")
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: None)
    monkeypatch.setattr(queries, "fetch_ohlcv_window", AsyncMock(return_value=[]))
    series = await queries.get_ohlcv(engine, "s1", None)
    assert series.timeframe == "1h"


async def test_get_ohlcv_default_outside_6frame_clamps_to_1h(engine, monkeypatch):
    """默认路径会话 timeframe='30m'（15 框内、6 框外）→ 确定性兜底 1h，不报错。"""
    from src.webui import queries, ohlcv_cache
    await _seed_session_with_window(engine, "s1", timeframe="30m")
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: None)
    monkeypatch.setattr(queries, "fetch_ohlcv_window", AsyncMock(return_value=[]))
    series = await queries.get_ohlcv(engine, "s1", None)
    assert series.timeframe == "1h"


async def test_get_ohlcv_explicit_month_1M_rejected(engine, monkeypatch):
    """显式 1M（月，归一有效但 6 框外）→ InvalidTimeframe（不误折成分钟 1m）。"""
    from src.webui import queries, ohlcv_cache
    await _seed_session_with_window(engine, "s1", timeframe="1h")
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: None)
    with pytest.raises(queries.InvalidTimeframe):
        await queries.get_ohlcv(engine, "s1", "1M")


async def test_get_ohlcv_explicit_garbage_rejected(engine):
    from src.webui import queries
    await _seed_session_with_window(engine, "s1", timeframe="1h")
    with pytest.raises(queries.InvalidTimeframe):
        await queries.get_ohlcv(engine, "s1", "ZZ")


async def test_get_ohlcv_unknown_sid_raises_valueerror(engine):
    """未知 sid（默认路径）→ resolve_session_window 抛 ValueError（端点转 404）。"""
    from src.webui import queries
    with pytest.raises(ValueError, match="session not found"):
        await queries.get_ohlcv(engine, "nope", None)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_webui_queries.py -k ohlcv -q`
Expected: FAIL — `AttributeError: module 'src.webui.queries' has no attribute 'InvalidTimeframe'` / `get_ohlcv`。

- [ ] **Step 3: 写实现**

在 `src/webui/queries.py` 顶部 import 区追加：

```python
from src.services.ohlcv_history import fetch_ohlcv_window, resolve_session_window, TIMEFRAMES
from src.webui import ohlcv_cache
from src.utils.timeframe import normalize_timeframe
```

在文件末尾（`get_session_detail` 之后）追加：

```python
class InvalidTimeframe(ValueError):
    """显式传入的 timeframe 归一后非法或不可绘图（端点转 400）。

    ValueError 子类——端点须【先】catch 本类（400）再 catch ValueError
    （resolve_session_window 的未知 sid → 404）。
    """


def _resolve_chart_tf(raw: str | None, session_tf: str | None) -> str:
    """tf 归一（spec §C）。显式非法 → InvalidTimeframe；默认路径落 6 框外 → 兜底 1h。

    复用 src.utils.timeframe.normalize_timeframe（不自造 .lower()，保 m/M 区分），
    再以自有 6 框白名单 TIMEFRAMES 做图表收窄。
    """
    if raw is not None:
        try:
            tf = normalize_timeframe(raw)
        except ValueError as e:
            raise InvalidTimeframe(str(e)) from e
        if tf not in TIMEFRAMES:
            raise InvalidTimeframe(f"timeframe not chartable: {raw}")
        return tf
    # 默认路径：会话 tf 归一；非法 / 6 框外 → 一律确定性兜底 1h（不做模糊「最近较粗框」）
    if session_tf is None:
        return "1h"
    try:
        tf = normalize_timeframe(session_tf)
    except ValueError:
        return "1h"
    return tf if tf in TIMEFRAMES else "1h"


async def get_ohlcv(engine: AsyncEngine, session_id: str,
                    timeframe: str | None) -> schemas.OhlcvSeries:
    # 1. 解析 tf：默认路径单独查一次 SessionModel.timeframe（resolve_session_window 三元组
    #    签名被 F7 re-export 契约冻结、不含 tf，不能扩成四元组）。
    session_tf: str | None = None
    if timeframe is None:
        async with get_session(engine) as s:
            session_tf = (await s.execute(
                select(SessionModel.timeframe).where(SessionModel.id == session_id)
            )).scalar_one_or_none()
    tf = _resolve_chart_tf(timeframe, session_tf)

    # 2. 解析窗口（未知 sid / 零时长 → ValueError → 端点 404）
    symbol, start_ms, end_ms = await resolve_session_window(engine, session_id)

    # 3-4. 缓存命中则用，否则拉取 + 落盘
    cache_dir = ohlcv_cache.cache_dir_for(engine)
    rows = ohlcv_cache.read(cache_dir, session_id, tf, end_ms)
    if rows is None:
        rows = await fetch_ohlcv_window(symbol, tf, start_ms, end_ms)
        ohlcv_cache.write(cache_dir, session_id, tf, symbol, end_ms, rows)

    # 5. 裸行 → OhlcvBar
    bars = [
        schemas.OhlcvBar(
            at=datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
            open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5],
        )
        for r in rows
    ]
    return schemas.OhlcvSeries(symbol=symbol, timeframe=tf, bars=bars)
```

> 注：`datetime` / `timezone` / `select` / `get_session` / `SessionModel` 在 queries.py 顶部已 import（第 7/9/13/14 行），无需重复。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_webui_queries.py -k ohlcv -q`
Expected: PASS（8 passed）。

- [ ] **Step 5: 提交**

```bash
git add src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): get_ohlcv query（tf 归一复用 normalize_timeframe + 缓存 + InvalidTimeframe）"
```

---

## Task 6: 端点 `GET /api/sessions/{sid}/ohlcv`

**Files:**
- Modify: `src/webui/app.py`
- Test: `tests/test_webui_api.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_webui_api.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_ohlcv_endpoint(engine, monkeypatch):
    from datetime import timedelta
    from unittest.mock import AsyncMock
    import ccxt
    from src.webui import queries, ohlcv_cache
    start = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id="s1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, timeframe="1H",
                           created_at=start, last_active_at=start + timedelta(hours=2)))
        await s.commit()
    # 内存 engine → cache_dir None（断言不污染真 data/）；mock fetch
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: None)
    bars = [[1_778_846_400_000, 1.0, 2.0, 0.5, 1.5, 10.0]]
    monkeypatch.setattr(queries, "fetch_ohlcv_window", AsyncMock(return_value=bars))

    c = _client(engine)
    # 200 + 默认 tf = 会话归一 timeframe（1H → 1h）
    r = c.get("/api/sessions/s1/ohlcv")
    assert r.status_code == 200
    body = r.json()
    assert body["timeframe"] == "1h"
    assert body["symbol"] == "BTC/USDT:USDT"
    assert body["bars"][0]["at"].endswith("Z")          # UTC 归一带 Z
    # 显式合法 tf 透传
    assert c.get("/api/sessions/s1/ohlcv?timeframe=5m").json()["timeframe"] == "5m"
    # 显式非法 tf → 400
    assert c.get("/api/sessions/s1/ohlcv?timeframe=ZZ").status_code == 400
    assert c.get("/api/sessions/s1/ohlcv?timeframe=1M").status_code == 400   # 月，6 框外
    # 未知 sid → 404
    assert c.get("/api/sessions/nope/ohlcv").status_code == 404


@pytest.mark.asyncio
async def test_ohlcv_endpoint_fetch_failure_503(engine, monkeypatch):
    from datetime import timedelta
    from unittest.mock import AsyncMock
    import ccxt
    from src.webui import queries, ohlcv_cache
    start = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SessionModel(id="s1", name="n1", symbol="BTC/USDT:USDT", initial_balance=10000.0,
                           status="active", scheduler_interval_min=15, timeframe="1h",
                           created_at=start, last_active_at=start + timedelta(hours=2)))
        await s.commit()
    monkeypatch.setattr(ohlcv_cache, "cache_dir_for", lambda eng: None)
    monkeypatch.setattr(queries, "fetch_ohlcv_window",
                        AsyncMock(side_effect=ccxt.NetworkError("dead")))
    c = _client(engine)
    r = c.get("/api/sessions/s1/ohlcv")
    assert r.status_code == 503
    assert r.json()["detail"] == "NetworkError"          # 仅类名（redaction 纪律）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_webui_api.py -k ohlcv_endpoint -q`
Expected: FAIL — 404（端点未注册，FastAPI 返回 Not Found）。

- [ ] **Step 3: 写实现**

在 `src/webui/app.py` 顶部 import 区追加：

```python
import asyncio

import ccxt
```

在 `_perf` 端点之后（第 56 行后）插入新端点：

```python
    @app.get("/api/sessions/{sid}/ohlcv", response_model=schemas.OhlcvSeries)
    async def _ohlcv(sid: str, timeframe: str | None = Query(None),
                     eng: AsyncEngine = Depends(get_engine)):
        try:
            return await queries.get_ohlcv(eng, sid, timeframe)
        except queries.InvalidTimeframe:           # ValueError 子类——须先于 ValueError catch
            raise HTTPException(400, "invalid timeframe")
        except ValueError:                          # resolve_session_window：未知 sid / 零时长
            raise HTTPException(404, "session not found")
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, asyncio.TimeoutError) as e:
            raise HTTPException(503, type(e).__name__)   # 仅类名（redaction 纪律）
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_webui_api.py -k ohlcv -q`
Expected: PASS（含 schema 测 + 端点 200/400/404/503）。

- [ ] **Step 5: 提交**

```bash
git add src/webui/app.py tests/test_webui_api.py
git commit -m "feat(webui): GET /api/sessions/{sid}/ohlcv 端点（400/404/503 失败语义）"
```

---

## Task 7: 重生成 openapi.json + types.ts + client.ts api.getOhlcv

**Files:**
- Regenerate: `frontend/openapi.json`、`frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`

- [ ] **Step 1: 重生成 openapi.json（minified）**

Run（项目根目录）:
```bash
python -c "import json; from src.webui.app import app; print(json.dumps(app.openapi()))" > frontend/openapi.json
```
Expected: 写入成功；`grep -c OhlcvSeries frontend/openapi.json` ≥ 1。

- [ ] **Step 2: 重生成 types.ts**

Run:
```bash
cd frontend && npm run gen:types
```
Expected: `src/api/types.ts` 更新；`grep -c OhlcvSeries frontend/src/api/types.ts` ≥ 1。

- [ ] **Step 3: client.ts 加类型导出 + api.getOhlcv**

在 `frontend/src/api/client.ts` 类型导出区（第 15 行 `AlertInfo` 后）追加：

```typescript
export type OhlcvBar = S["OhlcvBar"];
export type OhlcvSeries = S["OhlcvSeries"];
```

在 `api` 对象内（`getLive` 之后）追加：

```typescript
  getOhlcv: (sid: string, tf?: string) => {
    const qs = tf ? `?timeframe=${encodeURIComponent(tf)}` : "";
    return get<OhlcvSeries>(`/sessions/${encodeURIComponent(sid)}/ohlcv${qs}`);
  },
```

- [ ] **Step 4: vue-tsc 校验**

Run:
```bash
cd frontend && npx vue-tsc --noEmit
```
Expected: 0 错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/openapi.json frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "chore(webui): 重生成 openapi.json + 前端类型 + api.getOhlcv"
```

---

## Task 8: 前端纯函数 `markers.ts`

**Files:**
- Create: `frontend/src/utils/markers.ts`
- Test: `frontend/test/markers.spec.ts`

- [ ] **Step 1: 写失败测试**

Create `frontend/test/markers.spec.ts`：

```typescript
import { describe, it, expect } from "vitest";
import { toCandleData, snapToBarTime, toMarkers, POS_HEX, NEG_HEX, MUTED_HEX } from "@/utils/markers";
import { deriveTradeFills } from "@/utils/trades";
import { epochSec } from "@/utils/time";
import type { TradeRow } from "@/api/client";
import type { OhlcvBar } from "@/api/client";

const bar = (at: string, o = 1, h = 2, l = 0.5, c = 1.5): OhlcvBar =>
  ({ at, open: o, high: h, low: l, close: c, volume: 10 });

describe("toCandleData", () => {
  it("ISO→秒级、升序、同秒去重保留最后、映 OHLC", () => {
    const d = toCandleData([
      bar("2026-06-12T10:01:00Z", 2),
      bar("2026-06-12T10:00:00Z", 1),
      bar("2026-06-12T10:00:00Z", 9),   // 同秒 → 保留最后
    ]);
    expect(d.length).toBe(2);
    expect((d[0].time as number) < (d[1].time as number)).toBe(true);
    expect(d[0].open).toBe(9);          // 同秒保留最后
  });
});

describe("snapToBarTime", () => {
  const t = (s: string) => epochSec(s);
  const barTimes = [t("2026-06-12T10:00:00Z"), t("2026-06-12T10:05:00Z"), t("2026-06-12T10:15:00Z")];
  it("成交落 bar 内 → 吸附该 bar 开盘时间", () => {
    expect(snapToBarTime(t("2026-06-12T10:07:00Z"), barTimes)).toBe(t("2026-06-12T10:05:00Z"));
  });
  it("有缺口 → 吸附到最近较早 bar（非不存在的 floor 时间）", () => {
    // 10:05 与 10:15 间缺 10:10 这根；落在 10:12 → 吸附 10:05
    expect(snapToBarTime(t("2026-06-12T10:12:00Z"), barTimes)).toBe(t("2026-06-12T10:05:00Z"));
  });
  it("早于首根 → 钳首根", () => {
    expect(snapToBarTime(t("2026-06-12T09:00:00Z"), barTimes)).toBe(barTimes[0]);
  });
  it("barTimes 空 → 返回原值", () => {
    expect(snapToBarTime(12345, [])).toBe(12345);
  });
});

describe("toMarkers", () => {
  const t = (s: string) => epochSec(s);
  const barTimes = [t("2026-06-12T10:00:00Z"), t("2026-06-12T10:05:00Z"),
                    t("2026-06-12T10:10:00Z"), t("2026-06-12T10:15:00Z")];
  const longTrades: TradeRow[] = [
    { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "long", price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
    { at: "2026-06-12T10:15:00Z", action: "order_filled", side: "long", price: 66000, amount: 1, pnl: 1000, fee: 1, trigger_reason: "stop" },
  ];

  it("单开单平 long → 2 markers：开 belowBar/arrowUp/POS/「开」、平 aboveBar/arrowDown/POS/「平」", () => {
    const ms = toMarkers(deriveTradeFills(longTrades), barTimes);
    expect(ms.length).toBe(2);
    expect(ms[0]).toMatchObject({ position: "belowBar", shape: "arrowUp", color: POS_HEX, text: "开" });
    expect(ms[1]).toMatchObject({ position: "aboveBar", shape: "arrowDown", color: POS_HEX, text: "平" });
    expect((ms[0].time as number) < (ms[1].time as number)).toBe(true);   // 按 time 升序
  });

  it("marker.time === snapToBarTime(epochSec(fill.at), barTimes)（与 hover map 键同源）", () => {
    const fills = deriveTradeFills(longTrades);
    const ms = toMarkers(fills, barTimes);
    expect(ms[0].time).toBe(snapToBarTime(epochSec(fills[0].at), barTimes));
  });

  it("加仓行 isAdd → text「加」、belowBar", () => {
    const adds: TradeRow[] = [
      { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "long", price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
      { at: "2026-06-12T10:05:00Z", action: "order_filled", side: "long", price: 65500, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
    ];
    const ms = toMarkers(deriveTradeFills(adds), barTimes);
    expect(ms[1]).toMatchObject({ text: "加", position: "belowBar" });
  });

  it("short → NEG 色；side null → MUTED 色", () => {
    const shortClose: TradeRow[] = [
      { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "short", price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
    ];
    expect(toMarkers(deriveTradeFills(shortClose), barTimes)[0].color).toBe(NEG_HEX);
    const noSide: TradeRow[] = [
      { at: "2026-06-12T10:00:00Z", action: "order_filled", side: null, price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
    ];
    expect(toMarkers(deriveTradeFills(noSide), barTimes)[0].color).toBe(MUTED_HEX);
  });

  it("平仓细分（stop）不改 marker text（仍「平」，细分留 hover）", () => {
    const ms = toMarkers(deriveTradeFills(longTrades), barTimes);
    expect(ms[1].text).toBe("平");   // longTrades[1].trigger_reason === "stop"
  });

  it("空 fills → []", () => {
    expect(toMarkers([], barTimes)).toEqual([]);
  });

  it("同口径：markers 数 == deriveTradeFills 行数（同一样本）", () => {
    const fills = deriveTradeFills(longTrades);
    expect(toMarkers(fills, barTimes).length).toBe(fills.length);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/markers.spec.ts`
Expected: FAIL — 无法解析 `@/utils/markers`。

- [ ] **Step 3: 写实现**

Create `frontend/src/utils/markers.ts`：

```typescript
/** 价格 K 线买卖点 markers 纯函数。消费 deriveTradeFills 输出（单一口径，与 A+ 表同源，spec §D）。 */
import type { OhlcvBar } from "@/api/client";
import type { DerivedFill } from "@/utils/trades";
import { epochSec } from "@/utils/time";
import type { CandlestickData, SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";

// canvas 不能读 CSS 变量；镜像 --ob-pos / --ob-neg / --ob-text-muted（改这三处须同步 tokens.css）。
export const POS_HEX = "#15803d";
export const NEG_HEX = "#dc2626";
export const MUTED_HEX = "#6b7280";

/** OhlcvBar[] → candlestick data。秒级 UTCTimestamp、升序、同秒去重保留最后（镜像 EquityChart.toSeriesData）。 */
export function toCandleData(bars: OhlcvBar[]): CandlestickData[] {
  const byTime = new Map<number, CandlestickData>();
  for (const b of bars) {
    const sec = epochSec(b.at);
    byTime.set(sec, { time: sec as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close });
  }
  return [...byTime.values()].sort((a, b) => (a.time as number) - (b.time as number));
}

/** 成交秒戳吸附到 ≤ 它的最大已加载 bar 时间（用实际 candle，非 floor-to-tf——处理行情缺口）。
 *  早于首根 → 钳首根；barTimes 空 → 返回原值（无图可标）。barTimes 须升序（取自 toCandleData 的 time 列）。 */
export function snapToBarTime(atSec: number, barTimes: number[]): number {
  if (barTimes.length === 0) return atSec;
  if (atSec <= barTimes[0]) return barTimes[0];
  let lo = 0, hi = barTimes.length - 1, ans = barTimes[0];
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (barTimes[mid] <= atSec) { ans = barTimes[mid]; lo = mid + 1; }
    else hi = mid - 1;
  }
  return ans;
}

/** DerivedFill[] → markers。time 经 snapToBarTime（与 hover map 键同源，保 crosshair param.time 命中）。 */
export function toMarkers(fills: DerivedFill[], barTimes: number[]): SeriesMarker<Time>[] {
  const markers: SeriesMarker<Time>[] = fills.map((f) => {
    const isOpen = f.grossPnl == null;                          // 开/加型（与表同判据）
    const color = f.side === "long" ? POS_HEX : f.side === "short" ? NEG_HEX : MUTED_HEX;
    return {
      time: snapToBarTime(epochSec(f.at), barTimes) as UTCTimestamp,
      position: isOpen ? "belowBar" : "aboveBar",               // 进场标在下、出场标在上
      shape: isOpen ? "arrowUp" : "arrowDown",
      color,
      text: isOpen ? (f.isAdd ? "加" : "开") : "平",            // 常驻短标签；细分/数值留 hover
    };
  });
  return markers.sort((a, b) => (a.time as number) - (b.time as number));  // lightweight-charts 要求 time 升序
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/markers.spec.ts`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/markers.ts frontend/test/markers.spec.ts
git commit -m "feat(webui): markers.ts 纯函数（toCandleData/snapToBarTime/toMarkers）"
```

---

## Task 9: `PriceChart.vue` 组件

**Files:**
- Create: `frontend/src/components/PriceChart.vue`
- Test: `frontend/test/PriceChart.spec.ts`

- [ ] **Step 1: 写失败测试**

Create `frontend/test/PriceChart.spec.ts`：

```typescript
import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

const setData = vi.fn();
const setMarkers = vi.fn();
let crosshairCb: ((p: unknown) => void) | null = null;

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addCandlestickSeries: vi.fn(() => ({ setData, setMarkers })),
    subscribeCrosshairMove: vi.fn((cb) => { crosshairCb = cb; }),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    remove: vi.fn(),
  })),
}));

vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return { ...actual, api: { ...actual.api, getOhlcv: vi.fn() } };
});

import { NRadioGroup } from "naive-ui";   // 实引用查找——naive 内部 name 是 "RadioGroup"，按 {name:"NRadioGroup"} 查不到
import PriceChart from "@/components/PriceChart.vue";
import { api, ApiError, type TradeRow } from "@/api/client";

const getOhlcv = api.getOhlcv as Mock;

const TRADES: TradeRow[] = [
  { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "long", price: 65000, amount: 1, pnl: null, fee: 1, trigger_reason: "market" },
  { at: "2026-06-12T10:15:00Z", action: "order_filled", side: "long", price: 66000, amount: 1, pnl: 1000, fee: 1, trigger_reason: "stop" },
];
const SERIES = {
  symbol: "BTC/USDT:USDT", timeframe: "1h",
  bars: [{ at: "2026-06-12T10:00:00Z", open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 }],
};

const mountChart = (defaultTimeframe = "1h") =>
  mount(PriceChart, { props: { sessionId: "s1", symbol: "BTC/USDT:USDT", defaultTimeframe, trades: TRADES } });

beforeEach(() => {
  getOhlcv.mockReset();
  setData.mockReset();
  setMarkers.mockReset();
  crosshairCb = null;
});

describe("PriceChart", () => {
  it("挂载不抛 + init 用 defaultTimeframe 调 getOhlcv", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h");
    await flushPromises();
    expect(getOhlcv).toHaveBeenCalledWith("s1", "1h");
    expect(setData).toHaveBeenCalled();
    expect(setMarkers).toHaveBeenCalled();
    expect(w.find(".price-chart-wrap").exists()).toBe(true);
  });

  it("大写会话 tf（1H）→ 归一为 1h 高亮 + 请求 1h", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    mountChart("1H");
    await flushPromises();
    expect(getOhlcv).toHaveBeenCalledWith("s1", "1h");
  });

  it("切 timeframe → 重新调 getOhlcv", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h");
    await flushPromises();
    getOhlcv.mockClear();
    // n-radio-group v-model:value=tf；驱动 update:value 等价于用户切换
    await w.findComponent(NRadioGroup).vm.$emit("update:value", "5m");
    await flushPromises();
    expect(getOhlcv).toHaveBeenCalledWith("s1", "5m");
  });

  it("getOhlcv 抛 ApiError → error 占位、不崩", async () => {
    getOhlcv.mockRejectedValue(new ApiError(503, "boom"));
    const w = mountChart("1h");
    await flushPromises();
    expect(w.text()).toContain("价格数据拉取失败");
  });

  it("空 bars → 空态占位", async () => {
    getOhlcv.mockResolvedValue({ ...SERIES, bars: [] });
    const w = mountChart("1h");
    await flushPromises();
    expect(w.text()).toContain("该窗口无行情数据");
  });

  it("crosshair 命中已加载 bar 时刻 → hover 浮层列该刻成交（类型/方向/价/量）", async () => {
    getOhlcv.mockResolvedValue(SERIES);
    const w = mountChart("1h");
    await flushPromises();
    // 命中开仓 marker 所在 bar（10:00:00Z）。snapToBarTime 把 10:00 fill 吸附到该 bar 时间。
    const t = Math.floor(Date.parse("2026-06-12T10:00:00Z") / 1000);
    crosshairCb?.({ time: t, point: { x: 10, y: 20 } });
    await w.vm.$nextTick();
    const tip = w.find(".pc-tip");
    expect(tip.exists()).toBe(true);
    expect(tip.text()).toContain("开仓");      // DerivedFill.type
    expect(tip.text()).toContain("多");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/PriceChart.spec.ts`
Expected: FAIL — 无法解析 `@/components/PriceChart.vue`。

- [ ] **Step 3: 写实现**

Create `frontend/src/components/PriceChart.vue`：

```vue
<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from "vue";
import { createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { NRadioGroup, NRadioButton } from "naive-ui";
import { api, ApiError, type OhlcvBar, type TradeRow } from "@/api/client";
import { deriveTradeFills, type DerivedFill } from "@/utils/trades";
import { toCandleData, snapToBarTime, toMarkers, POS_HEX, NEG_HEX } from "@/utils/markers";
import { epochSec } from "@/utils/time";
import { fmtNum, fmtSigned } from "@/utils/format";

const props = defineProps<{
  sessionId: string;
  symbol: string;
  defaultTimeframe: string;
  trades: TradeRow[];
}>();

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"] as const;
const FOLD: Record<string, string> = { H: "h", D: "d", W: "w" };

/** 会话 tf 归一为 6 框之一（1H→1h；落 6 框外兜底 1h）。镜像后端 _resolve_chart_tf 默认路径。 */
function normalizeTf(tf: string): string {
  const m = /^(\d+)([a-zA-Z])$/.exec((tf ?? "").trim());
  const folded = m ? `${m[1]}${FOLD[m[2]] ?? m[2]}` : tf;
  return (TIMEFRAMES as readonly string[]).includes(folded) ? folded : "1h";
}

const tf = ref(normalizeTf(props.defaultTimeframe));
const bars = ref<OhlcvBar[]>([]);
const loading = ref(false);
const error = ref(false);
const hover = ref<{ x: number; y: number; fills: DerivedFill[] } | null>(null);

const el = ref<HTMLElement | null>(null);
let chart: IChartApi | null = null;
let series: ISeriesApi<"Candlestick"> | null = null;
let hoverMap = new Map<number, DerivedFill[]>();

async function load() {
  loading.value = true;
  error.value = false;
  hover.value = null;
  try {
    const s = await api.getOhlcv(props.sessionId, tf.value);
    bars.value = s.bars;
  } catch (e) {
    if (e instanceof ApiError) error.value = true;
    else throw e;
  } finally {
    loading.value = false;
  }
}

function render() {
  if (!series) return;
  const candles = toCandleData(bars.value);
  const barTimes = candles.map((c) => c.time as number);
  series.setData(candles);
  const fills = deriveTradeFills(props.trades);
  series.setMarkers(toMarkers(fills, barTimes));
  // hover map 键 = snapToBarTime（与 marker.time 同源），保 crosshair param.time（bar 对齐）命中
  hoverMap = new Map();
  for (const f of fills) {
    const key = snapToBarTime(epochSec(f.at), barTimes);
    (hoverMap.get(key) ?? hoverMap.set(key, []).get(key)!).push(f);
  }
  chart?.timeScale().fitContent();
}

onMounted(() => {
  if (!el.value) return;
  chart = createChart(el.value, {
    autoSize: true,
    layout: { background: { color: "transparent" }, textColor: "#6b7280" },
    grid: { vertLines: { visible: false }, horzLines: { color: "#e5e7eb" } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: true },
  });
  series = chart.addCandlestickSeries({
    upColor: POS_HEX, downColor: NEG_HEX,
    borderUpColor: POS_HEX, borderDownColor: NEG_HEX,
    wickUpColor: POS_HEX, wickDownColor: NEG_HEX,
  });
  chart.subscribeCrosshairMove((param) => {
    const t = param.time as number | undefined;
    if (t == null || !param.point || !hoverMap.has(t)) { hover.value = null; return; }
    hover.value = { x: param.point.x, y: param.point.y, fills: hoverMap.get(t)! };
  });
  load().then(render);
});

watch(tf, () => load().then(render));
watch(() => props.trades, render, { deep: true });

onUnmounted(() => {
  chart?.remove();
  chart = null;
  series = null;
});

const sideText = (s: string | null | undefined) => (s === "long" ? "多" : s === "short" ? "空" : "—");
</script>

<template>
  <div class="price-chart-wrap ob-card">
    <div class="pc-head">
      <span class="pc-title">价格走势 · {{ symbol }}</span>
      <n-radio-group v-model:value="tf" size="small">
        <n-radio-button v-for="f in TIMEFRAMES" :key="f" :value="f">{{ f }}</n-radio-button>
      </n-radio-group>
    </div>
    <div class="pc-body">
      <div ref="el" class="pc-canvas"></div>
      <div v-if="loading" class="pc-overlay">加载价格数据…</div>
      <div v-else-if="error" class="pc-overlay">价格数据拉取失败</div>
      <div v-else-if="bars.length === 0" class="pc-overlay">该窗口无行情数据</div>
      <div v-if="hover" class="pc-tip" :style="{ left: hover.x + 'px', top: hover.y + 'px' }">
        <div v-for="(f, i) in hover.fills" :key="i" class="pc-tip-row">
          {{ f.type }} · {{ sideText(f.side) }} · 价 {{ fmtNum(f.price) }} · 量 {{ fmtNum(f.amount, 4) }}
          <template v-if="f.grossPnl != null">
            · 毛利 {{ fmtSigned(f.grossPnl) }} / 最终 {{ fmtSigned(f.finalPnl) }}
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.price-chart-wrap { padding: 8px 12px; }
.pc-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
.pc-title { font-size: 12px; color: var(--ob-text-muted); }
.pc-body { position: relative; width: 100%; height: 280px; }
.pc-canvas { width: 100%; height: 100%; }
.pc-overlay {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  color: var(--ob-text-muted); font-size: 13px; background: var(--ob-block-bg);
}
.pc-tip {
  position: absolute; pointer-events: none; transform: translate(8px, 8px); z-index: 2;
  background: var(--ob-block-bg); border: 1px solid var(--ob-border); border-radius: 4px;
  padding: 4px 8px; font-size: 11px; max-width: 320px;
}
.pc-tip-row { white-space: nowrap; }
</style>
```

> hoverMap 累积写法说明：`(hoverMap.get(key) ?? hoverMap.set(key, []).get(key)!).push(f)` 等价「无则建空数组再 push」。若实现者觉得晦涩，可拆成显式三行（`const arr = hoverMap.get(key) ?? []; arr.push(f); hoverMap.set(key, arr);`）——行为一致。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/PriceChart.spec.ts`
Expected: PASS（6 passed）。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/PriceChart.vue frontend/test/PriceChart.spec.ts
git commit -m "feat(webui): PriceChart.vue（K 线 + 买卖点 markers + hover + tf 切换器）"
```

---

## Task 10: 挂进 `PerformanceBar.vue` 抽屉

**Files:**
- Modify: `frontend/src/components/PerformanceBar.vue`
- Test: `frontend/test/PerformanceBar.spec.ts`

- [ ] **Step 1: 写失败测试**

在 `frontend/test/PerformanceBar.spec.ts`：

(a) 顶部 `vi.mock("lightweight-charts", ...)` 补齐 candlestick API（避免万一未 stub 时崩），并新增 PriceChart 显式 stub。把现有 mock 块替换为：

```typescript
vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
    addCandlestickSeries: vi.fn(() => ({ setData: vi.fn(), setMarkers: vi.fn() })),
    subscribeCrosshairMove: vi.fn(),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    remove: vi.fn(),
  })),
}));

const PriceChartStub = {
  name: "PriceChart",
  props: ["sessionId", "symbol", "defaultTimeframe", "trades"],
  template: "<div class='pc-stub'></div>",
};

const DETAIL = {
  id: "s1", name: "n1", symbol: "BTC/USDT:USDT", status: "active", timeframe: "1H",
  scheduler_interval_min: 15, initial_balance: 10000, token_budget: 0,
  created_at: "2026-06-12T08:00:00Z", last_active_at: "2026-06-12T10:00:00Z", system_prompt: null,
};
```

(b) 把 `mountBar` 改为注入 PriceChart stub：

```typescript
const mountBar = (perf: unknown, detail: unknown = null) => {
  const w = mount(PerformanceBar, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })],
      stubs: { PriceChart: PriceChartStub },
    },
  });
  const store = useSessionsStore() as any;
  store.performance = perf;
  store.detail = detail;
  return w;
};
```

> 现有用例调用 `mountBar(PERF_FLAT)`（detail 默认 null）——行为不变，仍全绿。

(c) 末尾追加两条新用例：

```typescript
  it("store.detail 有值 + 展开 → 渲价格走势 section + 传 props 给 PriceChart", async () => {
    const w = mountBar(PERF_FLAT, DETAIL);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    expect(w.find(".price-section").exists()).toBe(true);
    const pc = w.findComponent(PriceChartStub);
    expect(pc.exists()).toBe(true);
    expect(pc.props("symbol")).toBe("BTC/USDT:USDT");
    expect(pc.props("defaultTimeframe")).toBe("1H");
    expect(pc.props("sessionId")).toBe("s1");
    expect(pc.props("trades")).toHaveLength(4);
  });

  it("store.detail null → 不渲价格走势 section", async () => {
    const w = mountBar(PERF_FLAT, null);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    expect(w.find(".price-section").exists()).toBe(false);
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run test/PerformanceBar.spec.ts`
Expected: FAIL — 新两条找不到 `.price-section`。

- [ ] **Step 3: 写实现**

在 `frontend/src/components/PerformanceBar.vue`：

(a) `<script setup>` import 区追加：

```typescript
import PriceChart from "@/components/PriceChart.vue";
```

(b) 在 `const perf = computed(...)` 附近追加：

```typescript
const detail = computed(() => store.detail);
```

(c) template 展开态内、`held-bar` 之后、`exp-grid` 之前插入：

```html
      <!-- 价格走势 K 线 + 买卖点 markers（整宽 section，spec §F） -->
      <div v-if="detail" class="price-section">
        <PriceChart
          :session-id="detail.id"
          :symbol="detail.symbol"
          :default-timeframe="detail.timeframe"
          :trades="perf.trades"
        />
      </div>
```

(d) `<style scoped>` 追加：

```css
.price-section { margin-bottom: 12px; }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run test/PerformanceBar.spec.ts`
Expected: PASS（原 9 条 + 新 2 条全绿）。

- [ ] **Step 5: vue-tsc + 全前端测试**

Run:
```bash
cd frontend && npx vue-tsc --noEmit && npx vitest run
```
Expected: 0 类型错误；全部前端测试 PASS。

- [ ] **Step 6: 提交**

```bash
git add frontend/src/components/PerformanceBar.vue frontend/test/PerformanceBar.spec.ts
git commit -m "feat(webui): PerformanceBar 抽屉挂载价格走势 K 线 section"
```

---

## Task 11: 真实数据 Playwright 验证 + 全套 gate

**Files:**
- 无新增源码；构建 + 端到端走查 + 全测试套件。

- [ ] **Step 1: 全后端测试套件 gate（无回归）**

Run: `python -m pytest -q`
Expected: 全绿（含 F7 零破坏回归、新增 ohlcv_history / ohlcv_cache / queries / api 用例）。

- [ ] **Step 2: 构建前端**

Run: `cd frontend && npm run build`
Expected: vue-tsc 0 错 + vite build 成功（产出 `frontend/dist`）。

- [ ] **Step 3: 启动只读 webui 指向真实 sim 库**

Run（项目根，后台）:
```bash
TRADEBOT_DB=data/tradebot.db python -m uvicorn src.webui.app:app --port 8765
```
> 选一个有交易记录的已结束会话（如 sim#19 平尾 / sim#13）。首次拉取会真连 OKX 公开行情（无凭证）并落盘到 `data/ohlcv_cache/`。

- [ ] **Step 4: Playwright 真实数据走查（用 playwright MCP）**

逐项验证（spec 测试策略 #11 + 验收）：
- 选有交易的会话 → 展开收益分析抽屉 → 价格走势 section 出 K 线 + 开/加/平 markers。
- hover 一个 marker → 浮层显 类型/方向/价格/数量/（平仓行）PnL；**粗 tf（1h）下 hover 能命中**（验证 snapToBarTime 同源修法）。
- 切 timeframe（如 1h→5m）→ 图重渲（首切有 loading、再切回 1h 秒开＝缓存命中）。
- 拉取失败态：临时断网或指向不可达，观察 error 占位「价格数据拉取失败」，A+ 表与其余指标不受影响。
- console 0 error。
- markers 数 / 类型 / 方向与 A+ 历程表逐字一致（同一 `deriveTradeFills` 口径）。

- [ ] **Step 5: 确认缓存落盘位置正确**

Run: `ls data/ohlcv_cache/`
Expected: 出现 `<sid>_<tf>.json`；**不应**有 `file:` 前缀的怪目录、不应污染 `.working/`。

- [ ] **Step 6: 收尾提交（若走查中有微调）**

```bash
git add -A
git commit -m "test(webui): 价格 K 线 Playwright 真实数据走查通过"
```

---

## Self-Review（plan 对照 spec）

**Spec 覆盖：**
- §A 共享核心 `ohlcv_history.py`（resolve_session_window 借 engine 不 dispose / fetch_ohlcv_window 裸行 + try-finally close）→ Task 1。
- §A F7 零破坏改造（re-export 旧私名 / 主入口保 engine dispose / AC-F7-13/14）→ Task 2。
- §B `ohlcv_cache.py`（engine 派生目录 / removeprefix file: / :memory:→None / 覆盖判定）→ Task 3。
- §C schema OhlcvBar/OhlcvSeries → Task 4；tf 归一复用 normalize_timeframe + 6 框白名单 + 默认兜底 1h + InvalidTimeframe → Task 5；端点 200/400/404/503（detail 仅类名）→ Task 6。
- §D markers.ts（toCandleData / snapToBarTime / toMarkers / POS·NEG·MUTED）→ Task 8。
- §E PriceChart.vue（candlestick + setMarkers + crosshair hover + tf 切换器 + onUnmounted remove）→ Task 9。
- §F 布局（PerformanceBar 抽屉整宽 section / store.detail 依赖 / detail null 不渲）→ Task 10。
- 边界与降级（空 trades / 空 bars / 拉取失败 / side∉{long,short}→MUTED / 缺口吸附 / 内存库降级）→ Task 1/3/5/8/9 各测试覆盖。
- 测试策略 #1–#11 → Task 1/2/3/5/6/8/9/10/11 一一对应。

**类型一致性：** `resolve_session_window`(3-tuple) / `fetch_ohlcv_window`(list[list]) / `cache_dir_for`→`read`/`write` / `get_ohlcv`(engine, sid, timeframe|None) / `InvalidTimeframe`(ValueError 子类，端点先 catch) / 前端 `toCandleData`/`snapToBarTime`/`toMarkers` 与 `DerivedFill` 字段（grossPnl/isAdd/side/type）跨 Task 命名一致。

**占位扫描：** 无 TBD/TODO；每个 code step 给完整代码与精确命令、预期输出。

**最终 gate：** 完成 Task 1–11 后，整迭代走 PR 工作流（涉及后端 6 文件 + 前端 5 文件 + 双侧测试，远超 mini-iter 5 条 criterion，per feedback_docs_only_direct_merge → 开 PR）。
