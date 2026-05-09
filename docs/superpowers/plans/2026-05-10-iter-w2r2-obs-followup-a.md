# iter-w2r2-obs-followup-a Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build F7 OKX REST OHLCV post-hoc helper + F5 analyze_sim/diff_sim row-label drift guard test.

**Architecture:** F7 走直接 ccxt.async_support.okx() 不复用 MarketDataService（接口缺 since）；async fn + thin CLI；half-open `[start, end)` window；last-candle-ts 推进；finally 块保证资源释放。F5 白盒直调 _render_pnl/_render_cost/_render_behavior 配 in-memory engine + make_session fixture，断言 ⊇ diff_sim STATIC label sets。

**Tech Stack:** ccxt.async_support / SQLAlchemy async / pandas / pytest (asyncio_mode="auto")

**Spec:** `docs/superpowers/specs/2026-05-09-iter-w2r2-obs-followup-a-design.md`

**Branch:** `feature/iter-w2r2-obs-followup-a`（已存在，spec 已 commit `7595e4c`）

---

## File Structure

| 文件 | 角色 | 预估行数 |
|---|---|---|
| `scripts/fetch_session_ohlcv.py` | F7 主体 — async fn + 6 helper + CLI | ~120-150 |
| `tests/test_fetch_session_ohlcv.py` | F7 单测 — 15 AC | ~200 |
| `tests/test_label_drift_guard.py` | F5 drift guard — 3 AC | ~40 |

---

## Task 0: Pre-impl Gate（手动 REPL 验证 ccxt since 行为）

**为什么先做**：spec §2.3 硬 gate — `BaseExchange.fetch_ohlcv` 仓库无 since 既有调用样本，必须先验证 ccxt 把 `since` 正确映射到 OKX REST 的 "after"（向后翻页拉历史），否则铺好 12 次分页测试架构后才发现路径不通返工成本高。

**Files:** 无（无文件改动；REPL 一次性验证）

- [ ] **Step 1: 跑 REPL 单调用**

```bash
python -c "
import asyncio
import ccxt.async_support as ccxt

async def main():
    client = ccxt.okx()
    try:
        # 拉取 2026-05-01 00:00 UTC 之后 10 根 1m candle
        since_ms = 1777593600000  # 2026-05-01T00:00:00Z (verify: datetime(2026,5,1,tzinfo=UTC).timestamp()*1000)
        rows = await client.fetch_ohlcv('BTC/USDT:USDT', '1m', since=since_ms, limit=10)
        print(f'returned {len(rows)} rows')
        if rows:
            print(f'first ts: {rows[0][0]} ({rows[0][0] >= since_ms})')
            print(f'last ts:  {rows[-1][0]}')
            print(f'first row sample: {rows[0]}')
    finally:
        await client.close()

asyncio.run(main())
"
```

Expected:
- `returned 10 rows`（或更少，但非 0）
- `first ts: 1777593600000 (True)` — 首根 ts ≥ since_ms
- last ts 比 first ts 大 9 个 1m 周期（约 540000 ms）
- first row 样本是 `[ts, open, high, low, close, volume]` 6 元素 list

- [ ] **Step 2: 判定 gate 结果**

| 观察 | 判定 |
|---|---|
| 返回数据 + first ts ≥ since | ✅ gate pass，可进 Task 1 |
| 返回数据但 first ts < since（返回最新而非历史） | ❌ gate fail — 暂停实施，回到 spec round 改 §2.1 用 `params={"before": ms}` |
| 返回 0 行（symbol 不存在 / 时间窗错） | 改 since 到近期（如 2026-05-08）重跑；仍 0 行 → ❌ gate fail |
| AttributeError / TypeError | ❌ gate fail — ccxt 版本问题，需先升 ccxt 或换 API |

- [ ] **Step 3: 记录 gate 结果（不进 commit）**

把 Step 1 输出贴到对话/笔记，作为后续 task 的前提；gate pass 才进 Task 1。

---

## Task 1: TF_MS 常量 + timeframe 白名单 + drift guard 测试

**Files:**
- Create: `scripts/fetch_session_ohlcv.py`（首批 ~15 行 — module docstring + constants）
- Create: `tests/test_fetch_session_ohlcv.py`（首批 ~30 行 — TF_MS drift guard test）

- [ ] **Step 1: 写失败测试**

Create `tests/test_fetch_session_ohlcv.py`:

```python
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
```

- [ ] **Step 2: 跑测试验证失败**

```bash
pytest tests/test_fetch_session_ohlcv.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.fetch_session_ohlcv'`

- [ ] **Step 3: 写最小实现**

Create `scripts/fetch_session_ohlcv.py`:

```python
"""F7 — OKX REST OHLCV post-hoc helper for sim sessions.

See docs/superpowers/specs/2026-05-09-iter-w2r2-obs-followup-a-design.md
for full spec including resource contract, retry semantics, and AC list.
"""
from __future__ import annotations


# spec §2.2 + §3.4：硬编码 dict 不走 ccxt.parse_timeframe；
# AC-F7-4 drift guard 锁定这些数值。
TF_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")
```

- [ ] **Step 4: 跑测试验证通过**

```bash
pytest tests/test_fetch_session_ohlcv.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_session_ohlcv.py tests/test_fetch_session_ohlcv.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-obs-followup-a): F7 task 1 — TF_MS constants + drift guard

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_resolve_session` + ValueError 测试 (AC-F7-1, AC-F7-2)

**Files:**
- Modify: `scripts/fetch_session_ohlcv.py`（追加 `_resolve_session` ~25 行）
- Modify: `tests/test_fetch_session_ohlcv.py`（追加 `_resolve_session` 测试 ~50 行）

- [ ] **Step 1: 写失败测试**

Append to `tests/test_fetch_session_ohlcv.py`:

```python
# ===== _resolve_session tests =====

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
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

    显式 bump updated_at 让 fallback 路径可观测；INSERT 后 updated_at == created_at →
    无 explicit update 会 zero_duration raise，无法验证 fallback 真实工作。
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
```

- [ ] **Step 2: 跑测试验证失败**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k resolve
```

Expected: 5 FAIL with `ImportError: cannot import name '_resolve_session'`

- [ ] **Step 3: 写最小实现**

Append to `scripts/fetch_session_ohlcv.py`:

```python
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker
from src.storage.models import Session as SessionModel


def _ensure_utc(dt: datetime) -> datetime:
    """aiosqlite 读出的 DateTime(timezone=True) 是 naive；显式补 UTC tzinfo
    与 scripts/_sim_metrics.py:33-34 / :727-728 既有 pattern 一致。"""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def _resolve_session(engine: AsyncEngine, session_id: str) -> tuple[str, int, int]:
    """Look up sim session; return (symbol, start_ms, end_ms) for [created_at, last_active_at).

    Raises ValueError if session_id not found, or if the resulting window has
    zero duration. last_active_at falls back to updated_at (schema NOT NULL,
    always populated by ORM default+onupdate); spec §4.

    Uses AsyncSession (parallel to scripts/analyze_sim.py:55-71) — engine.connect()
    + scalar_one_or_none returns first column not ORM object.
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
```

- [ ] **Step 4: 跑测试验证通过**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k resolve
```

Expected: 5 passed (not_found / zero_duration / returns_tuple / falls_back_to_updated_at / epoch_accuracy)

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_session_ohlcv.py tests/test_fetch_session_ohlcv.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-obs-followup-a): F7 task 2 — _resolve_session + AC-F7-1/2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `_paginate_ohlcv` + retry contract (AC-F7-3, F7-9, F7-10, F7-11, F7-12, F7-13)

**Files:**
- Modify: `scripts/fetch_session_ohlcv.py`（追加 `_paginate_ohlcv` + retry helper ~50 行）
- Modify: `tests/test_fetch_session_ohlcv.py`（追加 ~80 行）

- [ ] **Step 1: 写失败测试 (basic pagination + retry)**

Append to `tests/test_fetch_session_ohlcv.py`:

```python
# ===== _paginate_ohlcv tests =====

from unittest.mock import AsyncMock, MagicMock
import asyncio
import ccxt


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
```

- [ ] **Step 2: 跑测试验证失败**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k paginate
```

Expected: 7 FAIL with `ImportError: cannot import name '_paginate_ohlcv'`

- [ ] **Step 3: 写最小实现**

Append to `scripts/fetch_session_ohlcv.py`:

```python
import asyncio
import ccxt

_RETRY_SLEEP_SCHEDULE: tuple[float, ...] = (1.0, 2.0)  # 2 sleeps; raise 后不再 sleep
_THROTTLE_SLEEP_S: float = 0.5
_PAGE_LIMIT: int = 100


async def _paginate_ohlcv(
    client, symbol: str, timeframe: str, start_ms: int, end_ms: int
) -> list[list]:
    """Paginate OKX REST fetch_ohlcv from start_ms forward until end_ms.

    spec §2.1: cursor advances by last-candle-ts + tf_ms (not blind 100×tf_ms).
    Termination: cursor >= end_ms OR empty page OR last_ts <= prev_last_ts.
    Retry: 3 attempts total, sleeps [1.0, 2.0], only NetworkError /
    ExchangeNotAvailable / TimeoutError; raise others immediately.
    """
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


async def _fetch_with_retry(client, symbol: str, timeframe: str, since_ms: int) -> list[list]:
    """3 attempts total; sleep [1.0, 2.0] between failures; no tail sleep."""
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
```

- [ ] **Step 4: 跑测试验证通过**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k paginate
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_session_ohlcv.py tests/test_fetch_session_ohlcv.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-obs-followup-a): F7 task 3 — _paginate_ohlcv + retry contract

AC-F7-3/9/10/11/12.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: timeframe parametrize drift guard (AC-F7-4)

**Files:**
- Modify: `tests/test_fetch_session_ohlcv.py`（追加 ~30 行）

- [ ] **Step 1: 写失败测试**

Append to `tests/test_fetch_session_ohlcv.py`:

```python
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
```

- [ ] **Step 2: 跑测试验证通过 (实现已存在)**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k cursor_advances
```

Expected: 6 passed (parametrize 6 timeframes)

- [ ] **Step 3: Commit**

```bash
git add tests/test_fetch_session_ohlcv.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-obs-followup-a): F7 task 4 — timeframe drift guard AC-F7-4

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `_to_dataframe` + 强制 dtype on empty (AC-F7-5)

**Files:**
- Modify: `scripts/fetch_session_ohlcv.py`（追加 ~15 行）
- Modify: `tests/test_fetch_session_ohlcv.py`（追加 ~30 行）

- [ ] **Step 1: 写失败测试**

Append to `tests/test_fetch_session_ohlcv.py`:

```python
# ===== _to_dataframe tests =====

def test_to_dataframe_schema_AC_F7_5():
    """AC-F7-5: DataFrame 7 columns with correct dtypes."""
    import pandas as pd
    from scripts.fetch_session_ohlcv import _to_dataframe

    rows = [
        [1_700_000_000_000, 80000.0, 80100.0, 79900.0, 80050.0, 1.5],
        [1_700_000_060_000, 80050.0, 80200.0, 80000.0, 80150.0, 2.0],
    ]
    df = _to_dataframe(rows)
    assert list(df.columns) == [
        "timestamp_ms", "datetime_iso", "open", "high", "low", "close", "volume",
    ]
    assert df["timestamp_ms"].dtype == "int64"
    assert df["datetime_iso"].dtype == "object"
    for col in ("open", "high", "low", "close", "volume"):
        assert df[col].dtype == "float64", f"{col}: {df[col].dtype}"
    assert df["datetime_iso"].iloc[0].endswith("Z") or "+00:00" in df["datetime_iso"].iloc[0]


def test_to_dataframe_empty_dtype_preserved():
    """AC-F7-15 (dtype 部分): 空 rows 仍返回 7 列 + 正确 dtype."""
    import pandas as pd
    from scripts.fetch_session_ohlcv import _to_dataframe

    df = _to_dataframe([])
    assert len(df) == 0
    assert list(df.columns) == [
        "timestamp_ms", "datetime_iso", "open", "high", "low", "close", "volume",
    ]
    assert df["timestamp_ms"].dtype == "int64"
    for col in ("open", "high", "low", "close", "volume"):
        assert df[col].dtype == "float64", f"empty {col}: {df[col].dtype}"
```

- [ ] **Step 2: 跑测试验证失败**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k to_dataframe
```

Expected: 2 FAIL with `ImportError: cannot import name '_to_dataframe'`

- [ ] **Step 3: 写最小实现**

Append to `scripts/fetch_session_ohlcv.py`:

```python
from datetime import datetime, timezone
import pandas as pd


_DTYPE_SCHEMA = {
    "timestamp_ms": "int64",
    "datetime_iso": "object",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
}


def _to_dataframe(rows: list[list]) -> pd.DataFrame:
    """Convert ccxt raw OHLCV rows to DataFrame; force §3.2 dtypes even when empty."""
    if not rows:
        # Build empty DataFrame with explicit dtypes (default empty df is 'object')
        return pd.DataFrame({col: pd.Series(dtype=dtype) for col, dtype in _DTYPE_SCHEMA.items()})

    records = [
        {
            "timestamp_ms": int(r[0]),
            "datetime_iso": datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc).isoformat(),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        }
        for r in rows
    ]
    df = pd.DataFrame(records)
    return df.astype(_DTYPE_SCHEMA)
```

- [ ] **Step 4: 跑测试验证通过**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k to_dataframe
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_session_ohlcv.py tests/test_fetch_session_ohlcv.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-obs-followup-a): F7 task 5 — _to_dataframe + AC-F7-5

Force §3.2 dtypes even on empty rows (covers AC-F7-15 dtype clause).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `fetch_session_ohlcv` 主入口 + 半开过滤 + 去重 + finally (AC-F7-7, F7-8, F7-13, F7-14, F7-15)

**Files:**
- Modify: `scripts/fetch_session_ohlcv.py`（追加 `fetch_session_ohlcv` 主体 ~40 行）
- Modify: `tests/test_fetch_session_ohlcv.py`（追加 ~80 行）

- [ ] **Step 1: 写失败测试**

Append to `tests/test_fetch_session_ohlcv.py`:

```python
# ===== fetch_session_ohlcv main entry tests =====

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine
from src.storage.models import Session as SessionModel
from tests._sim_fixtures import _resolve_db_path

# 注意：本组测试使用 file-based db_engine fixture（tests/conftest.py:90）+
# _resolve_db_path 拿物理路径传给 F7。不能用 in-memory engine —
# `:memory:` URL 跨 engine 不共享 DB，F7 内部 create_async_engine 拿到全新空库。


async def _setup_session_with_window(db_engine, *, name: str, hours: int = 2,
                                      symbol: str = "BTC/USDT:USDT"):
    """Helper: create session with explicit start/end window. Returns (sid, start_ms, end_ms)."""
    # 用已知 UTC start 时间（避开 fixture 默认 _safe_created_at 的随机度）
    start_dt = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(hours=hours)
    sid = await make_session(db_engine, name=name, symbol=symbol, created_at=start_dt)
    async with db_engine.begin() as conn:
        await conn.execute(
            update(SessionModel).where(SessionModel.id == sid).values(last_active_at=end_dt)
        )
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    return sid, start_ms, end_ms


async def test_fetch_half_open_filter_AC_F7_7(db_engine, monkeypatch):
    """AC-F7-7: candle ts < end_ms 保留；ts >= end_ms 剔除."""
    from scripts.fetch_session_ohlcv import fetch_session_ohlcv, TF_MS
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    sid, start_ms, end_ms = await _setup_session_with_window(db_engine, name="halfopen", hours=2)
    tf_ms = TF_MS["1m"]
    # 构造从 start_ms 起、跨过 end_ms 的 page（200 根，覆盖 200 分钟 > 2h window）
    async def mock_fetch(symbol, timeframe, since, limit):
        return _make_candle_page(since, 200, tf_ms)
    mock_client = MagicMock()
    mock_client.fetch_ohlcv = AsyncMock(side_effect=mock_fetch)
    mock_client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **kw: mock_client)

    df = await fetch_session_ohlcv(sid, timeframe="1m", db_path=_resolve_db_path(db_engine))
    # window 2h = 120 candles；半开 [start, end) 应得 120 candles
    assert len(df) == 120
    assert df["timestamp_ms"].max() < end_ms
    assert df["timestamp_ms"].min() >= start_ms


async def test_fetch_dedup_AC_F7_8(db_engine, monkeypatch):
    """AC-F7-8: 同一 ts 出现两次 → 去重为 1."""
    from scripts.fetch_session_ohlcv import fetch_session_ohlcv, TF_MS
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    # window 24h 足够大，避免 dedup 后被半开过滤掉
    sid, start_ms, end_ms = await _setup_session_with_window(db_engine, name="dedup2", hours=24)
    tf_ms = TF_MS["1m"]
    # 两页，从真实 start_ms 起；第二页前 5 条与第一页重叠
    pages = [
        _make_candle_page(start_ms, 50, tf_ms),                     # ts: start..start+49*tf_ms
        _make_candle_page(start_ms + 45 * tf_ms, 20, tf_ms),       # ts: start+45..start+64*tf_ms (5 重叠)
    ]
    pages_iter = iter(pages)
    async def mock_fetch(symbol, timeframe, since, limit):
        try:
            return next(pages_iter)
        except StopIteration:
            return []
    mock_client = MagicMock()
    mock_client.fetch_ohlcv = AsyncMock(side_effect=mock_fetch)
    mock_client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **kw: mock_client)

    df = await fetch_session_ohlcv(sid, timeframe="1m", db_path=_resolve_db_path(db_engine))
    # 50 + 20 - 5 (overlap) = 65 unique
    assert len(df) == 65
    assert df["timestamp_ms"].is_monotonic_increasing
    assert df["timestamp_ms"].is_unique


async def test_fetch_resource_cleanup_success_AC_F7_13(db_engine, monkeypatch):
    """AC-F7-13: 成功路径 ccxt.close() + engine.dispose() 各调一次.

    用 patch.object(AsyncEngine, "dispose") 替代实例赋值（AsyncEngine.__slots__ 拒绝实例赋值）。
    """
    from scripts.fetch_session_ohlcv import fetch_session_ohlcv, TF_MS

    sid, start_ms, _ = await _setup_session_with_window(db_engine, name="cleanup", hours=1)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    mock_client = MagicMock()
    mock_client.fetch_ohlcv = AsyncMock(return_value=_make_candle_page(start_ms, 5, TF_MS["1m"]))
    mock_client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **kw: mock_client)

    # patch class method (not instance attr) — __slots__ 阻止实例赋值
    dispose_calls: list[None] = []
    real_dispose = AsyncEngine.dispose
    async def spy_dispose(self):
        dispose_calls.append(None)
        await real_dispose(self)

    with patch.object(AsyncEngine, "dispose", spy_dispose):
        await fetch_session_ohlcv(sid, db_path=_resolve_db_path(db_engine))

    assert mock_client.close.await_count == 1
    # F7 内部 create_async_engine 一个 engine + dispose 一次；db_engine fixture 自身也会 dispose（teardown 时）
    # 测试期间至少 1 次（F7 内部）；fixture teardown 在测试结束后才发生
    assert len(dispose_calls) >= 1


async def test_fetch_resource_cleanup_on_exception_AC_F7_14(db_engine, monkeypatch):
    """AC-F7-14: fetch_ohlcv raise 时仍调用 close + dispose."""
    from scripts.fetch_session_ohlcv import fetch_session_ohlcv

    sid, _, _ = await _setup_session_with_window(db_engine, name="cleanup_err", hours=1)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    mock_client = MagicMock()
    mock_client.fetch_ohlcv = AsyncMock(side_effect=ccxt.BadSymbol("nope"))
    mock_client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **kw: mock_client)

    dispose_calls: list[None] = []
    real_dispose = AsyncEngine.dispose
    async def spy_dispose(self):
        dispose_calls.append(None)
        await real_dispose(self)

    with patch.object(AsyncEngine, "dispose", spy_dispose):
        with pytest.raises(ccxt.BadSymbol):
            await fetch_session_ohlcv(sid, db_path=_resolve_db_path(db_engine))

    assert mock_client.close.await_count == 1
    assert len(dispose_calls) >= 1


async def test_fetch_empty_window_AC_F7_15(db_engine, monkeypatch):
    """AC-F7-15: OKX 返回 [] → 空 DataFrame (7 列, dtype 同 §3.2), 不抛 ValueError."""
    from scripts.fetch_session_ohlcv import fetch_session_ohlcv

    sid, _, _ = await _setup_session_with_window(db_engine, name="empty", hours=1)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    mock_client = MagicMock()
    mock_client.fetch_ohlcv = AsyncMock(return_value=[])
    mock_client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **kw: mock_client)

    df = await fetch_session_ohlcv(sid, db_path=_resolve_db_path(db_engine))
    assert len(df) == 0
    assert list(df.columns) == [
        "timestamp_ms", "datetime_iso", "open", "high", "low", "close", "volume",
    ]
    assert df["timestamp_ms"].dtype == "int64"
    for col in ("open", "high", "low", "close", "volume"):
        assert df[col].dtype == "float64"
```

- [ ] **Step 2: 跑测试验证失败**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k "fetch_half_open or fetch_dedup or fetch_resource or fetch_empty"
```

Expected: 5 FAIL with `ImportError: cannot import name 'fetch_session_ohlcv'`

- [ ] **Step 3: 写最小实现**

Append to `scripts/fetch_session_ohlcv.py`:

```python
from pathlib import Path
import ccxt.async_support
from sqlalchemy.ext.asyncio import create_async_engine


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
    client = ccxt.async_support.okx()
    try:
        symbol, start_ms, end_ms = await _resolve_session(engine, session_id)
        rows = await _paginate_ohlcv(client, symbol, timeframe, start_ms, end_ms)
        # Sort + dedup (spec §2.1 step 6)
        rows.sort(key=lambda r: r[0])
        seen: set[int] = set()
        deduped: list[list] = []
        for r in rows:
            ts = r[0]
            if ts in seen:
                continue
            seen.add(ts)
            deduped.append(r)
        # Half-open filter [start_ms, end_ms)
        filtered = [r for r in deduped if start_ms <= r[0] < end_ms]
        df = _to_dataframe(filtered)
        if output_path is not None:
            _write_csv(df, output_path)
        return df
    finally:
        await client.close()
        await engine.dispose()


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    """Write DataFrame to CSV; mkdir parents if missing; overwrite existing."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
```

- [ ] **Step 4: 跑测试验证通过**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k "fetch_half_open or fetch_dedup or fetch_resource or fetch_empty"
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_session_ohlcv.py tests/test_fetch_session_ohlcv.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-obs-followup-a): F7 task 6 — main entry + half-open + dedup + finally

AC-F7-7/8/13/14/15.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `_write_csv` 写盘行为测试 (AC-F7-6)

**Files:**
- Modify: `tests/test_fetch_session_ohlcv.py`（追加 ~40 行）

> Task 6 已 inline 完整 `_write_csv` 实现（无 placeholder），本 task 仅新增测试覆盖写盘 + 覆盖 + mkdir + None-guard 行为。每 commit 自包含、bisectable。

- [ ] **Step 1: 写测试**

Append to `tests/test_fetch_session_ohlcv.py`:

```python
# ===== _write_csv tests (use db_engine + _resolve_db_path; see Task 6 note) =====

async def test_fetch_writes_csv_with_overwrite_AC_F7_6(db_engine, monkeypatch, tmp_path):
    """AC-F7-6: 写 CSV + 覆盖现有文件 + 自动 mkdir 父目录."""
    from scripts.fetch_session_ohlcv import fetch_session_ohlcv, TF_MS

    sid, start_ms, _ = await _setup_session_with_window(db_engine, name="csv_write", hours=1)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    mock_client = MagicMock()
    mock_client.fetch_ohlcv = AsyncMock(return_value=_make_candle_page(start_ms, 5, TF_MS["1m"]))
    mock_client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **kw: mock_client)

    output = tmp_path / "nonexistent_subdir" / "out.csv"
    db_path = _resolve_db_path(db_engine)

    # First write
    await fetch_session_ohlcv(sid, db_path=db_path, output_path=output)
    assert output.exists()
    content1 = output.read_text()
    assert "timestamp_ms" in content1.splitlines()[0]

    # Second write should overwrite
    await fetch_session_ohlcv(sid, db_path=db_path, output_path=output)
    content2 = output.read_text()
    assert content1 == content2  # same data, no append


async def test_fetch_no_write_when_output_none(db_engine, monkeypatch, tmp_path):
    """spec §2.1 step 8: output_path=None → 不写盘."""
    from scripts.fetch_session_ohlcv import fetch_session_ohlcv, TF_MS

    sid, start_ms, _ = await _setup_session_with_window(db_engine, name="no_write", hours=1)
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    mock_client = MagicMock()
    mock_client.fetch_ohlcv = AsyncMock(return_value=_make_candle_page(start_ms, 5, TF_MS["1m"]))
    mock_client.close = AsyncMock()
    monkeypatch.setattr("ccxt.async_support.okx", lambda *a, **kw: mock_client)

    df = await fetch_session_ohlcv(sid, db_path=_resolve_db_path(db_engine), output_path=None)
    assert len(df) == 5  # mock returns 5 candles; verifies path executed without writing
    # tmp_path should remain empty (we didn't pass any output)
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: 跑测试验证通过**（实现已在 Task 6 inline）

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k "writes_csv or no_write"
```

Expected: 2 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_fetch_session_ohlcv.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-obs-followup-a): F7 task 7 — _write_csv behavior tests AC-F7-6

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: CLI main() + sanitize + label fallback

**Files:**
- Modify: `scripts/fetch_session_ohlcv.py`（追加 `main()` + `_sanitize_label` + argparse ~40 行）
- Modify: `tests/test_fetch_session_ohlcv.py`（追加 ~50 行）

- [ ] **Step 1: 写失败测试**

Append to `tests/test_fetch_session_ohlcv.py`:

```python
# ===== sanitize + label fallback tests =====

@pytest.mark.parametrize("name,expected", [
    ("clean_name", "clean_name"),
    ("BTC trend strategy", "BTC_trend_strategy"),
    ("test/with:slashes", "test_with_slashes"),
    ("trail_underscore_", "trail_underscore"),
    ("_lead_underscore", "lead_underscore"),
    ("0123456789012345678901234567890123456789_extra", "0123456789012345678901234567890123456789"),  # 40 char cap
    ("!!!", ""),  # all unsafe → empty after strip
    ("", ""),
])
def test_sanitize_label(name, expected):
    """spec §3.3: sanitize = re.sub(r'[^\\w-]+', '_', name).strip('_')[:40]"""
    from scripts.fetch_session_ohlcv import _sanitize_label
    assert _sanitize_label(name) == expected


def test_default_output_path_uses_session_name():
    """spec §3.3: label = sanitize(name) when sanitize非空."""
    from scripts.fetch_session_ohlcv import _build_default_output_path
    path = _build_default_output_path(
        session_id="11111111-2222-3333-4444-555555555555",
        name="BTC trend strategy",
        symbol="BTC/USDT:USDT",
        timeframe="1m",
    )
    assert "BTC_trend_strategy" in str(path)
    assert "BTC_USDT_USDT" in str(path)
    assert str(path).endswith("_1m.csv")


def test_default_output_path_fallback_to_session_id_prefix():
    """spec §3.3: name='' or sanitize 退化为空 → fallback session_id[:8]."""
    from scripts.fetch_session_ohlcv import _build_default_output_path
    path = _build_default_output_path(
        session_id="abcdef12-3456-7890-1234-567890abcdef",
        name="!!!",  # all unsafe → sanitize 退化
        symbol="BTC/USDT:USDT",
        timeframe="1m",
    )
    assert "abcdef12" in str(path)
```

- [ ] **Step 2: 跑测试验证失败**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k "sanitize or default_output"
```

Expected: 10 FAIL with `ImportError`

- [ ] **Step 3: 写最小实现**

Append to `scripts/fetch_session_ohlcv.py`:

```python
import re
import argparse
import asyncio


def _sanitize_label(name: str) -> str:
    """spec §3.3: re.sub(r'[^\\w-]+', '_', name).strip('_')[:40]"""
    return re.sub(r"[^\w-]+", "_", name).strip("_")[:40]


def _sanitize_symbol(symbol: str) -> str:
    """spec §3.3: 'BTC/USDT:USDT' → 'BTC_USDT_USDT'."""
    return symbol.replace("/", "_").replace(":", "_")


def _build_default_output_path(
    session_id: str, name: str, symbol: str, timeframe: str
) -> Path:
    """spec §3.3 default: .working/ohlcv/<label>_<symbol_safe>_<tf>.csv"""
    label = _sanitize_label(name) or session_id[:8]
    symbol_safe = _sanitize_symbol(symbol)
    return Path(".working/ohlcv") / f"{label}_{symbol_safe}_{timeframe}.csv"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch OKX REST OHLCV for a sim session's window."
    )
    parser.add_argument("--session", required=True, help="session id (UUID)")
    parser.add_argument("--timeframe", default="1m", choices=list(TIMEFRAMES))
    parser.add_argument("--db", default="data/tradebot.db", dest="db_path",
                        help="SQLite DB path (default: data/tradebot.db)")
    parser.add_argument("--output", default=None, dest="output_path",
                        help="output CSV path (default: .working/ohlcv/<label>_<symbol>_<tf>.csv)")
    args = parser.parse_args()

    async def _run():
        # Resolve default output: need symbol + name from DB.
        # Use AsyncSession to get ORM object (engine.connect+scalar_one returns first column not entity).
        output_path = args.output_path
        if output_path is None:
            engine = create_async_engine(f"sqlite+aiosqlite:///{args.db_path}")
            try:
                async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                async with async_session() as db:
                    result = await db.execute(
                        select(SessionModel).where(SessionModel.id == args.session)
                    )
                    row = result.scalars().first()
                if row is None:
                    raise ValueError(f"session not found: {args.session}")
                output_path = _build_default_output_path(
                    args.session, row.name, row.symbol, args.timeframe
                )
            finally:
                await engine.dispose()
        await fetch_session_ohlcv(
            args.session, timeframe=args.timeframe,
            db_path=args.db_path, output_path=Path(output_path),
        )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试验证通过**

```bash
pytest tests/test_fetch_session_ohlcv.py -v -k "sanitize or default_output"
```

Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/fetch_session_ohlcv.py tests/test_fetch_session_ohlcv.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-obs-followup-a): F7 task 8 — CLI main + sanitize + label fallback

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: F5 label drift guard tests (AC-F5-1, F5-2, F5-3)

**Files:**
- Create: `tests/test_label_drift_guard.py` (~50 行)

- [ ] **Step 1: 写失败测试**

Create `tests/test_label_drift_guard.py`:

```python
"""F5 — analyze_sim ↔ diff_sim row-label drift guard.

spec docs/superpowers/specs/2026-05-09-iter-w2r2-obs-followup-a-design.md §5.
pyproject asyncio_mode='auto' — no @pytest.mark.asyncio needed.

NOTE: 使用 AsyncSession + sessionmaker 拿 ORM 对象（与 scripts/analyze_sim.py:55-71
一致）。直接 engine.connect() + select(SessionModel) + scalar_one() 在 Connection
级别返回的是 first column (id: str) 而非 ORM 实体，后续 session.id / session.symbol 会失败。

`engine` fixture 复用 tests/conftest.py:26-29（in-memory），无需本文件重定义。
"""
from __future__ import annotations

import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from src.storage.models import Session as SessionModel

from scripts.analyze_sim import _render_pnl, _render_cost, _render_behavior
from scripts.diff_sim import PNL_LABELS, COST_STATIC_LABELS, BEH_STATIC_LABELS
from tests._sim_fixtures import make_session


_LABEL_ROW_RE = re.compile(r"^\|\s*([^\|]+?)\s*\|")


def _parse_label_column(md_output: str) -> set[str]:
    """Extract first-column labels from a markdown table; skip header + separator."""
    out: set[str] = set()
    for line in md_output.splitlines():
        m = _LABEL_ROW_RE.match(line)
        if not m:
            continue
        label = m.group(1).strip()
        if label in ("Metric", ""):  # header
            continue
        if set(label) <= set("-: "):  # separator row "|---|"
            continue
        out.add(label)
    return out


async def _load_session(engine, sid: str) -> SessionModel:
    """Load full ORM SessionModel via AsyncSession (parallel to analyze_sim.py:55-71)."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as db:
        result = await db.execute(select(SessionModel).where(SessionModel.id == sid))
        return result.scalars().one()


async def test_render_pnl_emits_all_pnl_labels_AC_F5_1(engine):
    """AC-F5-1: _render_pnl ⊇ PNL_LABELS."""
    sid = await make_session(engine, name="drift_pnl")
    session = await _load_session(engine, sid)
    output = await _render_pnl(engine, session, [])
    emitted = _parse_label_column(output)
    missing = set(PNL_LABELS) - emitted
    assert not missing, f"_render_pnl missing labels: {missing}"


async def test_render_cost_emits_all_cost_labels_AC_F5_2(engine):
    """AC-F5-2: _render_cost ⊇ COST_STATIC_LABELS."""
    sid = await make_session(engine, name="drift_cost")
    session = await _load_session(engine, sid)
    output = await _render_cost(engine, session)
    emitted = _parse_label_column(output)
    missing = set(COST_STATIC_LABELS) - emitted
    assert not missing, f"_render_cost missing labels: {missing}"


async def test_render_behavior_emits_all_beh_labels_AC_F5_3(engine):
    """AC-F5-3: _render_behavior ⊇ BEH_STATIC_LABELS."""
    sid = await make_session(engine, name="drift_beh")
    session = await _load_session(engine, sid)
    output = await _render_behavior(engine, session)
    emitted = _parse_label_column(output)
    missing = set(BEH_STATIC_LABELS) - emitted
    assert not missing, f"_render_behavior missing labels: {missing}"
```

- [ ] **Step 2: 跑测试验证通过 (analyze_sim 已存在，应直接通过；若失败说明真有 drift)**

```bash
pytest tests/test_label_drift_guard.py -v
```

Expected: 3 passed (current main HEAD has no drift; this test guards future PRs)

- [ ] **Step 3: Commit**

```bash
git add tests/test_label_drift_guard.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-obs-followup-a): F5 — analyze_sim/diff_sim label drift guard

AC-F5-1/2/3.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: 全套测试 + 整体 commit closeout

**Files:** 无新增；运行验证

- [ ] **Step 1: 跑全部测试**

```bash
pytest tests/test_fetch_session_ohlcv.py tests/test_label_drift_guard.py -v
```

Expected: 全部通过（F7: ~38 测试含 6× timeframe parametrize；F5: 3 测试 = 共 ~41）

- [ ] **Step 2: 跑全仓库测试 (regression check)**

```bash
pytest -q
```

Expected: 实施前先跑 `pytest --collect-only -q | tail -1` 取当前 baseline N（写本任务时为 1434）；实施后应得 N + 新增（F7 ~38 含 6 项 timeframe parametrize + F5 3）= **N + ~41** passed，无 regression（无 fail / error）。**不写死绝对数**，对照变化量即可。

- [ ] **Step 3: Self-review diff**

```bash
git log --oneline feature/iter-w2r2-obs-followup-a ^main
git diff main...feature/iter-w2r2-obs-followup-a --stat
```

确认：
- 11 commits（spec doc + plan doc + Task 1-9，本任务无新 commit），与 spec §7 commit 节奏一致
- 仅 `docs/superpowers/specs/...` + `docs/superpowers/plans/...` + `scripts/fetch_session_ohlcv.py` + `tests/test_fetch_session_ohlcv.py` + `tests/test_label_drift_guard.py` 5 个文件
- 无 `src/` 改动

- [ ] **Step 4: 准备 PR (用户操作)**

PR 创建命令（用户审阅后执行）：

```bash
gh pr create --title "feat(iter-w2r2-obs-followup-a): F7 OHLCV helper + F5 label drift guard" --body "$(cat <<'EOF'
## Summary
- **F7**: `scripts/fetch_session_ohlcv.py` post-hoc OKX REST OHLCV helper（~120-150 行）作为 P7 ~80% 替代方案
- **F5**: `tests/test_label_drift_guard.py` analyze_sim/diff_sim row-label drift guard（~40 行）防 PR #43 v1 同类 drift 复发

## Test plan
- [ ] `pytest tests/test_fetch_session_ohlcv.py` — F7 全部 15 AC 通过
- [ ] `pytest tests/test_label_drift_guard.py` — F5 3 AC 通过
- [ ] `pytest -q` — 全仓库无 regression
- [ ] Pre-impl gate（已跑过）— ccxt since 行为确认

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage check** — 对照 spec §6 AC 清单：

| AC | Task | 覆盖位置 |
|---|---|---|
| AC-F7-1 session not found | Task 2 | `test_resolve_session_not_found_raises` |
| AC-F7-2 zero duration | Task 2 | `test_resolve_session_zero_duration_raises` |
| AC-F7-3 1140 拼接 + 单调递增 | Task 3 | `test_paginate_basic_assembly_AC_F7_3` |
| AC-F7-4 6 timeframe parametrize drift guard | Task 4 | `test_paginate_cursor_advances_by_tf_ms_AC_F7_4` |
| AC-F7-5 7 列 schema | Task 5 | `test_to_dataframe_schema_AC_F7_5` |
| AC-F7-6 CSV 写盘 + 覆盖 + mkdir | Task 7 | `test_fetch_writes_csv_with_overwrite_AC_F7_6` |
| AC-F7-7 半开区间过滤 | Task 6 | `test_fetch_half_open_filter_AC_F7_7` |
| AC-F7-8 去重 | Task 6 | `test_fetch_dedup_AC_F7_8` |
| AC-F7-9 short return | Task 3 | `test_paginate_short_return_AC_F7_9` |
| AC-F7-10 transient retry success | Task 3 | `test_paginate_transient_retry_succeeds_AC_F7_10` |
| AC-F7-11 transient exhaust | Task 3 | `test_paginate_transient_exhaust_AC_F7_11` |
| AC-F7-12 permanent no retry | Task 3 | `test_paginate_permanent_no_retry_AC_F7_12` |
| AC-F7-13 cleanup success | Task 6 | `test_fetch_resource_cleanup_success_AC_F7_13` |
| AC-F7-14 cleanup on exception | Task 6 | `test_fetch_resource_cleanup_on_exception_AC_F7_14` |
| AC-F7-15 empty window | Task 6 + Task 5 | `test_fetch_empty_window_AC_F7_15` + `test_to_dataframe_empty_dtype_preserved` |
| AC-F5-1/2/3 | Task 9 | `test_render_pnl/cost/behavior_emits_all_..._labels_AC_F5_*` |

15 + 3 = 18 AC 全覆盖。

**Pre-impl gate** (Task 0) — spec §2.3 强制要求，作为 Task 0 先于 Task 1。

**Type consistency** — `TF_MS: dict[str, int]` / `TIMEFRAMES: tuple[str, ...]` / `_resolve_session(...) -> tuple[str, int, int]` / `_paginate_ohlcv(...) -> list[list]` / `_to_dataframe(...) -> pd.DataFrame` / `fetch_session_ohlcv(...) -> pd.DataFrame` — 跨 task 一致。

**Placeholder scan** — 搜索 "TODO" / "TBD" / "implement later" / "fill in"：均无。Task 6 已 inline 完整 `_write_csv` 实现（不留 NotImplementedError 中间态），全 plan 无 placeholder，每 commit self-contained 可 bisect。

**File scope** — 3 个文件全部对齐 spec In Scope（§1）；无 `src/` 改动。
