# Iter 4 — DecisionLog 写入路径补全 (T0-1 PR-B) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补全 DecisionLog 双字段写入路径（`decision` 派生 + `status` 执行状态），把硬编码 `'completed'` 替换为运行时派生，加 reasoning cap 4000，回填 Iter 3→4 窗口期 mismatch 行。

**Architecture:** 派生函数 `_derive_decision_from_actions` 作 `app.py` 内 private async helper，从 `trade_actions` 反查 cycle 内 actions 按优先级（open > close > adjust > hold）派生 5 类 enum；成功路径 + forensic 路径双处复用同一派生逻辑。`SQLAlchemyError/OSError` 兜底 fallback `'derive_error'` 独立 enum，不污染 `'legacy'` 历史语义。T11/T12 drift guard 强制实施防未来漂移。

**Tech Stack:** Python 3.11+ / pydantic-ai 1.78 / SQLAlchemy 2.0 (async + aiosqlite) / pytest-asyncio / uv

**Spec:** `docs/superpowers/specs/2026-04-29-iter4-decisionlog-write-path-design.md`（7 轮 review 后稳态，commit `1a65b95`）

**前置（强制）**: user 必须先重启 main.py 一次让 Iter 3 migration apply（schema 加 `status` 列；本 Iter 实施期间需要新 schema 才能跑测试。CI in-memory SQLite 走 `init_db` create_all 路径不依赖此前置；但 spec §6/§7 SQL 在 user 重启后才有效）。

---

## File Structure

**Create:**
- `tests/test_derive_decision.py` — 派生函数 8 个单元测 + side 边界 + fallback regression + T11/T12 drift guard

**Modify:**
- `src/cli/app.py`:
  - 顶部 imports: 加 `from sqlalchemy import select` + `from sqlalchemy.exc import SQLAlchemyError` + `from sqlalchemy.ext.asyncio import AsyncSession`
  - 加 `ADJUST_ACTIONS` 常量 + `_derive_decision_from_actions` async helper
  - `app.py:165-175` forensic 路径改写
  - `app.py:248-258` 成功路径改写
- `src/storage/models.py:92` — 行内注释 `truncated to 500 chars` → `truncated to 4000 chars`
- `tests/test_usage_limits.py:103` — `DecisionLog.decision == "..."` → `DecisionLog.status == "..."`

每个文件单一职责：派生函数 + 写入路径同住 `app.py`（spec §C3 决议，YAGNI 不抽 `derivations.py`）；测试集中 `test_derive_decision.py`（spec §5.1 决议）。

---

## Task 1: 派生函数骨架 + ADJUST_ACTIONS + hold fallback (T5)

**Files:**
- Create: `tests/test_derive_decision.py`
- Modify: `src/cli/app.py`（顶部 imports + 加常量 + 加 helper）

- [ ] **Step 1.1: 写 T5 失败测试 + helper fixture**

```python
# tests/test_derive_decision.py
"""Iter 4 §5.1 — _derive_decision_from_actions 单元测 + drift guard."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, TradeAction


async def _make_engine_with_session(session_id: str = "sess-derive-test"):
    """In-memory SQLite + 1 个 SessionModel (FK target)。"""
    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="derive-test"))
        await db.commit()
    return engine


async def _insert_action(engine, session_id: str, cycle_id: str,
                         action: str, side: str | None = None):
    """插一行 TradeAction 到测试 DB。"""
    async with get_session(engine) as db:
        db.add(TradeAction(
            session_id=session_id,
            cycle_id=cycle_id,
            action=action,
            symbol="BTC/USDT:USDT",
            side=side,
        ))
        await db.commit()


@pytest.mark.asyncio
async def test_t5_zero_actions_returns_hold():
    """T5: cycle 0 actions → 'hold'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-empty"
        )
    assert result == "hold"
```

- [ ] **Step 1.2: 跑测试验证 fail**

Run: `uv run pytest tests/test_derive_decision.py::test_t5_zero_actions_returns_hold -v`
Expected: FAIL with `ImportError: cannot import name '_derive_decision_from_actions' from 'src.cli.app'`

- [ ] **Step 1.3: app.py 顶部加 imports**

`src/cli/app.py:10` 修改 — **替换 line 10 + 紧接其后插入两行**（保留原有 line 11 空行作为 sqlalchemy 块与 src 块之间的分隔；插入后 line 14 起仍为既有 `from src.agent.memory import MemoryService` 等）：

```python
# Before
# L10: from sqlalchemy import update as sql_update
# L11: (empty)
# L12: from src.agent.memory import MemoryService

# After
# L10: from sqlalchemy import select, update as sql_update     ← 替换
# L11: from sqlalchemy.exc import SQLAlchemyError              ← 新增
# L12: from sqlalchemy.ext.asyncio import AsyncSession         ← 新增
# L13: (empty — 保留原 L11 空行)
# L14: from src.agent.memory import MemoryService              ← 不动（原 L12）
```

具体写法：

```python
from sqlalchemy import select, update as sql_update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
```

- [ ] **Step 1.4: app.py 加 ADJUST_ACTIONS 常量 + 派生函数骨架**

在 `app.py` `USAGE_LIMITS_PER_CYCLE` 定义之后（约 :46 行后）插入：

```python
# Iter 4 §3.2 — DecisionLog 派生类型分类常量
# 8 个调整类 action（_record_action 站点实测，spec §8.3 + §C5 决议）
# set_next_wake 单独归 hold（spec §C5）；open_position / close_position 单独分类
ADJUST_ACTIONS = frozenset({
    "set_stop_loss",
    "set_take_profit",
    "adjust_leverage",
    "set_price_alert",
    "add_price_level_alert",
    "cancel_price_level_alert",
    "place_limit_order",
    "cancel_order",
})


async def _derive_decision_from_actions(
    session: AsyncSession,
    session_id: str,
    cycle_id: str,
) -> str:
    """从 trade_actions 反查 cycle 内 actions，按优先级派生 decision 类型。

    优先级（高 → 低）：open_position > close_position > adjust > hold
    返回 5 类 enum: open_long / open_short / close / adjust / hold
    DB 故障 fallback: derive_error（独立 enum，spec §8.1）

    spec §3.2 — 复用 outer session 避免冗余连接。
    TradeAction 由 app.py:32 module-level 导入。

    NOTE (Task 1 中间态): 当前仅返回 'hold'，rows 由 SELECT 取出但未消费；
    Task 2-4 增量补 open / close / adjust 分支后 rows 才被消费。
    """
    try:
        rows = (await session.execute(
            select(TradeAction).where(
                TradeAction.session_id == session_id,
                TradeAction.cycle_id == cycle_id,
            ).order_by(TradeAction.id)  # first-match 语义稳定
        )).scalars().all()
    except (SQLAlchemyError, OSError):
        logger.exception(
            f"derive_decision SELECT failed for cycle {cycle_id}; falling back to 'derive_error'"
        )
        return "derive_error"

    # 占位：仅 hold 分支（后续 task 补 open / close / adjust）
    return "hold"
```

注：`TradeAction` 已在 `app.py:32` module-level 导入（`from src.storage.models import DecisionLog, Session, TradeAction`），函数内**不再重复 import**。

- [ ] **Step 1.5: 跑 T5 验证 pass**

Run: `uv run pytest tests/test_derive_decision.py::test_t5_zero_actions_returns_hold -v`
Expected: PASS

- [ ] **Step 1.6: Commit**

```bash
git add tests/test_derive_decision.py src/cli/app.py
git commit -m "feat(iter-t0-1): scaffold _derive_decision_from_actions w/ hold fallback (T5)

- Add ADJUST_ACTIONS frozenset (8 adjust actions per spec §C5; set_next_wake / open / close 单独分类)
- Add _derive_decision_from_actions async helper w/ ORDER BY + (SQLAlchemyError, OSError) try/except
- Fallback 'derive_error' on DB failure (spec §8.1 独立 enum)
- T5: 0 actions → 'hold'

Spec: docs/superpowers/specs/2026-04-29-iter4-decisionlog-write-path-design.md §3.2

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: open_long / open_short 派生 (T1, T2)

**Files:**
- Modify: `tests/test_derive_decision.py` (add T1 + T2)
- Modify: `src/cli/app.py` (add open_position branch in `_derive_decision_from_actions`)

- [ ] **Step 2.1: 写 T1 + T2 失败测试**

加在 `test_derive_decision.py` 末尾：

```python
@pytest.mark.asyncio
async def test_t1_open_long_derives():
    """T1: cycle 含 open_position(side='long') → 'open_long'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-1",
                         "open_position", side="long")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-1"
        )
    assert result == "open_long"


@pytest.mark.asyncio
async def test_t2_open_short_derives():
    """T2: cycle 含 open_position(side='short') → 'open_short'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-2",
                         "open_position", side="short")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-2"
        )
    assert result == "open_short"
```

- [ ] **Step 2.2: 跑 T1 + T2 验证 fail**

Run: `uv run pytest tests/test_derive_decision.py -v -k "t1_open_long or t2_open_short"`
Expected: 2 FAIL — 都返回 `'hold'`（占位实现），断言失败

- [ ] **Step 2.3: 加 open_position 分支**

**替换** `app.py` `_derive_decision_from_actions` 函数中 `try/except` 之后到末尾的所有行（即 Task 1.4 留下的 `# 占位：仅 hold 分支...` 注释 + `return "hold"` 这两行整体）为下方 6 行：

```python
    for a in rows:
        if a.action == "open_position":
            return f"open_{a.side}"  # open_long / open_short

    # 占位：close / adjust / hold（后续 task）
    return "hold"
```

- [ ] **Step 2.4: 跑 T1 + T2 + T5 验证 pass**

Run: `uv run pytest tests/test_derive_decision.py -v`
Expected: 3 PASS（T1/T2/T5）

- [ ] **Step 2.5: Commit**

```bash
git add tests/test_derive_decision.py src/cli/app.py
git commit -m "feat(iter-t0-1): _derive_decision_from_actions open_long/open_short 分支 (T1, T2)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: close 派生 (T3)

**Files:**
- Modify: `tests/test_derive_decision.py` (add T3)
- Modify: `src/cli/app.py` (add close_position branch)

- [ ] **Step 3.1: 写 T3 失败测试**

```python
@pytest.mark.asyncio
async def test_t3_close_derives():
    """T3: cycle 含 close_position（无 open）→ 'close'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-3",
                         "close_position", side="long")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-3"
        )
    assert result == "close"
```

- [ ] **Step 3.2: 跑 T3 验证 fail**

Run: `uv run pytest tests/test_derive_decision.py::test_t3_close_derives -v`
Expected: FAIL — 返回 `'hold'`

- [ ] **Step 3.3: 加 close_position 分支**

**替换** `app.py` `_derive_decision_from_actions` 函数中 `try/except` 之后到末尾的所有行（即 Task 2 留下的 open_position 块 + `# 占位：close / adjust / hold...` 注释 + `return "hold"`）为下方 9 行：

```python
    for a in rows:
        if a.action == "open_position":
            return f"open_{a.side}"  # open_long / open_short

    if any(a.action == "close_position" for a in rows):
        return "close"

    # 占位：adjust / hold（后续 task）
    return "hold"
```

- [ ] **Step 3.4: 跑全套验证 pass**

Run: `uv run pytest tests/test_derive_decision.py -v`
Expected: 4 PASS

- [ ] **Step 3.5: Commit**

```bash
git add tests/test_derive_decision.py src/cli/app.py
git commit -m "feat(iter-t0-1): _derive_decision_from_actions close 分支 (T3)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: adjust 派生 (T4)

**Files:**
- Modify: `tests/test_derive_decision.py` (add T4)
- Modify: `src/cli/app.py` (add adjust branch)

- [ ] **Step 4.1: 写 T4 失败测试**

```python
@pytest.mark.asyncio
async def test_t4_adjust_derives_from_set_stop_loss():
    """T4: cycle 仅含 set_stop_loss → 'adjust'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-4", "set_stop_loss")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-4"
        )
    assert result == "adjust"
```

- [ ] **Step 4.2: 跑 T4 验证 fail**

Run: `uv run pytest tests/test_derive_decision.py::test_t4_adjust_derives_from_set_stop_loss -v`
Expected: FAIL — 返回 `'hold'`

- [ ] **Step 4.3: 加 adjust 分支**

**替换** `app.py` `_derive_decision_from_actions` 函数中 `try/except` 之后到末尾的所有行（即 Task 3 留下的 open + close 块 + `# 占位：adjust / hold...` 注释 + `return "hold"`）为下方 12 行：

```python
    for a in rows:
        if a.action == "open_position":
            return f"open_{a.side}"  # open_long / open_short

    if any(a.action == "close_position" for a in rows):
        return "close"

    if any(a.action in ADJUST_ACTIONS for a in rows):
        return "adjust"

    return "hold"  # 0 actions OR 仅含 set_next_wake
```

- [ ] **Step 4.4: 跑全套验证 pass**

Run: `uv run pytest tests/test_derive_decision.py -v`
Expected: 5 PASS

- [ ] **Step 4.5: Commit**

```bash
git add tests/test_derive_decision.py src/cli/app.py
git commit -m "feat(iter-t0-1): _derive_decision_from_actions adjust 分支 (T4)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: hold 双义 + 优先级 + session 隔离 regression locks (T6, T7, T8)

**Files:**
- Modify: `tests/test_derive_decision.py` (add T6/T7/T8)

T6/T7/T8 是 regression locks — 验证已实施的逻辑正确处理这三类边界。期望加测试时立即 pass（不需要再改实现），任何未来 regression 由测试拦截。

- [ ] **Step 5.1: 写 T6/T7/T8 测试**

```python
@pytest.mark.asyncio
async def test_t6_set_next_wake_only_returns_hold():
    """T6: cycle 仅含 set_next_wake → 'hold'（spec §C5 决议：set_next_wake 单独归 hold）。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-6", "set_next_wake")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-6"
        )
    assert result == "hold"


@pytest.mark.asyncio
async def test_t7_priority_open_beats_adjust():
    """T7: cycle 含 open_position + set_stop_loss 同 cycle → 'open_long'（早期返回拦截）。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-7",
                         "open_position", side="long")
    await _insert_action(engine, "sess-derive-test", "cycle-7", "set_stop_loss")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-7"
        )
    assert result == "open_long"


@pytest.mark.asyncio
async def test_t8_session_isolation():
    """T8: session_A cycle X 有 open；session_B 同 cycle_id 无 actions → 派生 session_B 返回 'hold'。

    cycle_id 实测是 UUID4 前 8 chars (spec §5.1 T8 实操含义)，
    单 session 内碰撞极低但跨 session 长尾可能重复 → 防 SELECT 漏 session_id WHERE 子句。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session(session_id="sess-A")
    # 加 sess-B 也作 FK target
    async with get_session(engine) as db:
        db.add(SessionModel(id="sess-B", name="other-session"))
        await db.commit()

    # session_A cycle X 有 open_position
    await _insert_action(engine, "sess-A", "cycle-shared",
                         "open_position", side="long")

    # 查 session_B 同 cycle_id → 应返回 hold（不互窜）
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-B", "cycle-shared"
        )
    assert result == "hold"
```

- [ ] **Step 5.2: 跑 T6/T7/T8 验证 pass（regression locks）**

Run: `uv run pytest tests/test_derive_decision.py -v`
Expected: 8 PASS（之前 5 + T6/T7/T8）

- [ ] **Step 5.3: Commit**

```bash
git add tests/test_derive_decision.py
git commit -m "test(iter-t0-1): T6/T7/T8 regression locks for hold dual-meaning, priority, session isolation

T6: set_next_wake-only cycle → 'hold' (spec §C5)
T7: open + adjust 同 cycle → 'open_long' (early-return priority)
T8: cross-session cycle_id 不互窜 (UUID4 前 8 chars 长尾碰撞兜底)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: side ∉ {long, short} 兜底 (T8.5)

**Files:**
- Modify: `tests/test_derive_decision.py` (add T8.5)
- Modify: `src/cli/app.py` (`_derive_decision_from_actions` add side 检查)

- [ ] **Step 6.1: 写 T8.5 失败测试**

```python
@pytest.mark.asyncio
async def test_t8_5_open_position_with_invalid_side_falls_through():
    """T8.5: open_position(side=None) + set_stop_loss 同 cycle → 'adjust'。

    spec §3.5: 派生函数对 side ∉ {'long', 'short'} 兜底 — skip 此 row 让 downstream 接管。
    实测 cycle = [open_position(side=None), set_stop_loss] 应返回 'adjust' 不是 'open_None'。"""
    from src.cli.app import _derive_decision_from_actions

    engine = await _make_engine_with_session()
    await _insert_action(engine, "sess-derive-test", "cycle-85",
                         "open_position", side=None)
    await _insert_action(engine, "sess-derive-test", "cycle-85", "set_stop_loss")
    async with get_session(engine) as session:
        result = await _derive_decision_from_actions(
            session, "sess-derive-test", "cycle-85"
        )
    assert result == "adjust", \
        f"side=None open_position 应被 skip 让 adjust 接管，实际 {result!r}"
```

- [ ] **Step 6.2: 跑 T8.5 验证 fail**

Run: `uv run pytest tests/test_derive_decision.py::test_t8_5_open_position_with_invalid_side_falls_through -v`
Expected: FAIL — 当前实现返回 `'open_None'`（`f"open_{a.side}"` 把 None 拼为 字符串 'None'）

- [ ] **Step 6.3: 加 side 兜底**

**替换** `app.py` `_derive_decision_from_actions` 函数中 `try/except` 之后到末尾的所有行（即 Task 4 留下的完整 open + close + adjust + hold 12 行块）为下方 17 行（仅在 open_position 分支增加 `if a.side not in (...)` 兜底，其他分支不变）：

```python
    for a in rows:
        if a.action == "open_position":
            if a.side not in ("long", "short"):
                logger.warning(
                    f"open_position with unexpected side={a.side!r} "
                    f"in cycle {cycle_id}; skipping this row, downstream "
                    f"classification (close/adjust/hold) takes over"
                )
                continue  # 跳过此 row，循环继续
            return f"open_{a.side}"  # open_long / open_short

    if any(a.action == "close_position" for a in rows):
        return "close"
    if any(a.action in ADJUST_ACTIONS for a in rows):
        return "adjust"
    return "hold"  # 0 actions OR 仅含 set_next_wake
```

- [ ] **Step 6.4: 跑全套验证 pass**

Run: `uv run pytest tests/test_derive_decision.py -v`
Expected: 9 PASS

- [ ] **Step 6.5: Commit**

```bash
git add tests/test_derive_decision.py src/cli/app.py
git commit -m "feat(iter-t0-1): _derive_decision_from_actions side ∉ {long,short} 兜底 (T8.5)

skip 此 row 让 downstream 接管，避免 f'open_None' / f'open_banana' 类污染。
spec §3.5 实测：[open_position(side=None), set_stop_loss] → 'adjust' 不是 'open_None'。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: derive_error fallback regression lock (T8.6)

**Files:**
- Modify: `tests/test_derive_decision.py` (add T8.6)

派生函数已在 Task 1 加 `try/except (SQLAlchemyError, OSError)` fallback `'derive_error'`，T8.6 是 regression lock — mock SELECT 抛 SQLAlchemyError 验证 fallback 路径。

- [ ] **Step 7.1: 写 T8.6 测试**（`AsyncMock` / `SQLAlchemyError` 已在 Task 1 文件顶部 imports）

```python
@pytest.mark.asyncio
async def test_t8_6_select_failure_falls_back_to_derive_error():
    """T8.6: SELECT 抛 SQLAlchemyError → fallback 'derive_error'（spec §3.2）。"""
    from src.cli.app import _derive_decision_from_actions

    # mock session.execute 抛 SQLAlchemyError
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=SQLAlchemyError("DB unreachable"))

    result = await _derive_decision_from_actions(
        mock_session, "sess-x", "cycle-x"
    )
    assert result == "derive_error", \
        f"DB 故障应 fallback 'derive_error'，实际 {result!r}"
```

- [ ] **Step 7.2: 跑 T8.6 验证 pass**

Run: `uv run pytest tests/test_derive_decision.py::test_t8_6_select_failure_falls_back_to_derive_error -v`
Expected: PASS（fallback 在 Task 1 已实施）

- [ ] **Step 7.3: Commit**

```bash
git add tests/test_derive_decision.py
git commit -m "test(iter-t0-1): T8.6 regression lock for SQLAlchemyError → 'derive_error' fallback

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: 成功路径写入改造 (G1) + T9 集成测 + models.py:92 注释 (Y1)

**Files:**
- Modify: `src/cli/app.py:248-258`（成功路径）
- Modify: `src/storage/models.py:92`（行内注释 500→4000）
- Modify: `tests/test_usage_limits.py`（加 T9 集成测）

- [ ] **Step 8.1: 写 T9 集成测**

加在 `tests/test_usage_limits.py` 末尾（参考既有 `_make_deps_and_engine` helper）：

```python
async def test_t9_success_path_writes_status_ok_and_long_reasoning():
    """T9: 成功路径写 decision=派生 / status='ok' / reasoning truncated to 4000."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine(session_id="sess-t9")
    budget = TokenBudget(daily_max=500_000)

    # 喂 5000-char 长输出验证 cap 4000
    long_output = "x" * 5000

    async def mock_run(prompt, **kwargs):
        result = MagicMock()
        result.usage = lambda: MagicMock(total_tokens=100, details=None)
        result.new_messages = lambda: []
        result.output = long_output
        return result

    mock_agent = MagicMock()
    mock_agent.run = mock_run
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(DecisionLog).where(DecisionLog.session_id == "sess-t9")
        )).scalars().all()

    assert len(rows) == 1, f"应写 1 行 DecisionLog，实际 {len(rows)}"
    row = rows[0]
    assert row.status == "ok", f"成功路径 status 应 'ok'，实际 {row.status!r}"
    assert row.decision == "hold", \
        f"无 trade_actions 该 cycle 派生应 'hold'，实际 {row.decision!r}"
    assert len(row.reasoning) == 4000, \
        f"reasoning 应截断到 4000 chars，实际 {len(row.reasoning)}"
```

- [ ] **Step 8.2: 跑 T9 验证 fail**

Run: `uv run pytest tests/test_usage_limits.py::test_t9_success_path_writes_status_ok_and_long_reasoning -v`
Expected: FAIL — 具体两个断言失败：
- `assert row.decision == "hold"` 失败（实际 `'completed'`，硬编码未派生）
- `assert len(row.reasoning) == 4000` 失败（实际 500，cap 未放）

注：`assert row.status == "ok"` 不会 fail，因 schema `status` server_default='ok' 自动填入。

- [ ] **Step 8.3: 改 app.py 成功路径**

`src/cli/app.py:247-259` 当前块：

```python
    # === Record to database ===
    async with get_session(engine) as session:
        session.add(
            DecisionLog(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                trigger_type=trigger_type,
                decision="completed",
                reasoning=result.output[:500],
                model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                tokens_used=tokens,
            )
        )
        await session.commit()
```

改为：

```python
    # === Record to database ===
    async with get_session(engine) as session:
        decision = await _derive_decision_from_actions(
            session, deps.session_id, cycle_id
        )
        session.add(
            DecisionLog(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                trigger_type=trigger_type,
                decision=decision,            # spec §G1: 派生而非硬编码
                status="ok",                  # spec §G1: 双字段方案
                reasoning=result.output[:4000],  # spec §G1: cap 500 → 4000
                model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                tokens_used=tokens,
            )
        )
        await session.commit()
```

- [ ] **Step 8.4: 改 models.py:92 行内注释（Y1 doc-rot 防御）**

`src/storage/models.py:92` 当前：

```python
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)             # Agent's reasoning (truncated to 500 chars)
```

改为：

```python
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)             # Agent's reasoning (truncated to 4000 chars by app.py write paths; Iter 4 §G1)
```

- [ ] **Step 8.5: 跑 T9 + 全 derive 测试 验证 pass**

Run: `uv run pytest tests/test_derive_decision.py tests/test_usage_limits.py::test_t9_success_path_writes_status_ok_and_long_reasoning -v`
Expected: PASS

- [ ] **Step 8.6: Commit**

```bash
git add src/cli/app.py src/storage/models.py tests/test_usage_limits.py
git commit -m "feat(iter-t0-1): G1 成功路径写入派生 decision + status='ok' + reasoning[:4000]

- app.py:248-258 success path uses _derive_decision_from_actions
- status='ok' 显式写入（即使 server_default 也写避免 ORM cache 问题）
- reasoning cap 500 → 4000 (W1 实测 max 800-1500，2.5x buffer)
- models.py:92 行内注释 500 → 4000（Y1 doc-rot）
- T9 集成测覆盖

Spec: §G1 / §3.2 / Y1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: forensic 路径写入改造 (G2) + T10 集成测 + test_usage_limits.py:103 (C1)

**Files:**
- Modify: `src/cli/app.py:165-175`（forensic 路径）
- Modify: `tests/test_usage_limits.py:103`（C1 断言迁移）+ 加 T10

- [ ] **Step 9.1: 改 test_usage_limits.py 两处 + 加 T10**

**操作 1 — 替换 line 103 select where 子句**:

```python
# Before (line 103)
            select(DecisionLog).where(DecisionLog.decision == "usage_limit_exceeded")

# After
            select(DecisionLog).where(DecisionLog.status == "usage_limit_exceeded")
```

**操作 2 — 替换 line 106-110 整个末尾断言块** (5 行) **为下方 8 行**（首行 message 文本变更 + 新增 status / decision 双断言；保留原 session_id / reasoning / tokens_used 断言）:

```python
    assert len(rows) == 1, f"应写 1 行 status='usage_limit_exceeded'，实际 {len(rows)} 行"
    row = rows[0]
    assert row.session_id == "sess-t2"
    assert row.status == "usage_limit_exceeded"
    assert row.decision == "hold", \
        f"forensic 路径无 trade_actions 该 cycle 派生应 'hold'，实际 {row.decision!r}"
    assert "test reason" in row.reasoning
    assert row.tokens_used == 0
```

**操作 3 — 加 T10 集成测在文件末尾**。用 `monkeypatch` 钉住 `uuid.uuid4` 让 cycle_id 可预知，预插 trade_action，跑 forensic 路径，断言派生函数从 trade_actions 反查派生 `'open_long'`：

```python
async def test_t10_forensic_path_derives_from_committed_trade_actions(monkeypatch):
    """T10: forensic 路径派生函数从 trade_actions 反查派生 — spec §5.3 集成测。

    monkeypatch uuid.uuid4 钉住 cycle_id；预插一条 trade_action(open_position, side=long)；
    跑 forensic 路径（mock UsageLimitExceeded）；
    断言 DecisionLog 行 status='usage_limit_exceeded' AND decision='open_long'（派生联通）。
    """
    from pydantic_ai.exceptions import UsageLimitExceeded
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.storage.models import TradeAction

    # 钉住 uuid.uuid4 让 cycle_id = "abcd1234"（前 8 chars）
    class _FixedUUID:
        def __str__(self):
            return "abcd1234-rest-of-uuid-format"

    # monkeypatch 改的是 src.cli.app 模块对 stdlib uuid 的引用 — 等同 patch uuid.uuid4
    # 全局；pytest fixture 退出时自动恢复，不污染其他测试。
    monkeypatch.setattr("src.cli.app.uuid.uuid4", lambda: _FixedUUID())

    deps, engine = await _make_deps_and_engine(session_id="sess-t10")
    budget = TokenBudget(daily_max=500_000)

    # 预插 trade_action 用预知的 cycle_id="abcd1234"
    async with get_session(engine) as db:
        db.add(TradeAction(
            session_id="sess-t10", cycle_id="abcd1234",
            action="open_position", symbol="BTC/USDT:USDT", side="long",
        ))
        await db.commit()

    # mock agent.run 抛 UsageLimitExceeded
    async def boom(prompt, **kwargs):
        raise UsageLimitExceeded("test t10")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(DecisionLog).where(DecisionLog.cycle_id == "abcd1234")
        )).scalars().all()

    assert len(rows) == 1, f"forensic 路径应写 1 行 DecisionLog，实际 {len(rows)}"
    row = rows[0]
    assert row.status == "usage_limit_exceeded"
    assert row.decision == "open_long", \
        f"派生函数应反查 trade_actions 得 'open_long'，实际 {row.decision!r}"
```

- [ ] **Step 9.2: 跑 test_usage_limits T2 验证 fail**

Run: `uv run pytest tests/test_usage_limits.py::test_usage_limit_exceeded_writes_forensic_decision_log -v`
Expected: FAIL — 第一个失败断言是 `assert len(rows) == 1`（实际 0 行 — 因 line 103 改 `where status='usage_limit_exceeded'` 后，未改造的 forensic 路径写入 `status='ok'`，新 WHERE 0 行）。即首先暴露 status 字段未写入；改造 forensic 路径后再次跑会走到 decision 派生断言。

- [ ] **Step 9.3: 改 app.py forensic 路径**

`src/cli/app.py:165-175` 当前块：

```python
        except UsageLimitExceeded as e:
            logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
            async with get_session(engine) as session:
                session.add(DecisionLog(
                    session_id=deps.session_id,
                    cycle_id=cycle_id,
                    trigger_type=trigger_type,
                    decision="usage_limit_exceeded",
                    reasoning=str(e)[:500],
                    model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                    tokens_used=0,
                ))
                await session.commit()
            return None
```

改为：

```python
        except UsageLimitExceeded as e:
            logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
            async with get_session(engine) as session:
                decision = await _derive_decision_from_actions(
                    session, deps.session_id, cycle_id
                )
                session.add(DecisionLog(
                    session_id=deps.session_id,
                    cycle_id=cycle_id,
                    trigger_type=trigger_type,
                    decision=decision,                    # spec §G2: 派生而非语义冲突
                    status="usage_limit_exceeded",        # spec §G2: 双字段方案
                    reasoning=str(e)[:4000],              # spec §G2: cap 500 → 4000
                    model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                    tokens_used=0,
                ))
                await session.commit()
            return None
```

- [ ] **Step 9.4: 跑 T2 + T10 + 全 usage_limits 验证 pass**

Run: `uv run pytest tests/test_usage_limits.py -v`
Expected: 全 PASS（含 T10 monkeypatch uuid → 派生 'open_long'）

- [ ] **Step 9.5: 跑全派生 + usage_limits 测试**

Run: `uv run pytest tests/test_derive_decision.py tests/test_usage_limits.py -v`
Expected: 全 PASS

- [ ] **Step 9.6: Commit**

```bash
git add src/cli/app.py tests/test_usage_limits.py
git commit -m "feat(iter-t0-1): G2 forensic 路径写入派生 decision + status='usage_limit_exceeded'

- app.py:165-175 forensic path uses _derive_decision_from_actions
- decision 派生（取代语义冲突的 'usage_limit_exceeded'）
- status='usage_limit_exceeded' 双字段方案
- reasoning cap 500 → 4000
- C1: tests/test_usage_limits.py:103 断言点 decision → status

Spec: §G2 / §C1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: grep 全 codebase 排查硬编码 (C2)

**Files:**
- Investigate: `src/` + `tests/`
- 必要时 modify 命中点

排查 `decision == "completed"` / `decision="completed"` / `decision == "usage_limit_exceeded"` / `decision="usage_limit_exceeded"` 是否还有 Iter 4 未触及的依赖点。

- [ ] **Step 10.1: 跑 grep 排查**

```bash
echo "=== decision == \"completed\" ==="
grep -rn 'decision == "completed"' src/ tests/ || echo "(no match)"
echo "=== decision=\"completed\" ==="
grep -rn 'decision="completed"' src/ tests/ || echo "(no match)"
echo "=== decision == \"usage_limit_exceeded\" ==="
grep -rn 'decision == "usage_limit_exceeded"' src/ tests/ || echo "(no match)"
echo "=== decision=\"usage_limit_exceeded\" ==="
grep -rn 'decision="usage_limit_exceeded"' src/ tests/ || echo "(no match)"
```

Expected：
- `decision="completed"`: 无命中（Task 8 已改）
- `decision="usage_limit_exceeded"`: 无命中（Task 9 已改）
- `decision == "completed"`: 无命中（之前未发现依赖）
- `decision == "usage_limit_exceeded"`: 无命中（C1 已改）

- [ ] **Step 10.2: 若有命中决议是否同步迁移**

若任一命中：
- 是测试断言 → 同步迁移到 `status == ...`
- 是源码判定 → 决议是依赖语义还是字面量；前者改派生函数，后者改 `status`

若全无命中：跳到 Step 10.3。

- [ ] **Step 10.3: 必要时 commit；否则记录无遗漏**

```bash
# 若 Step 10.2 有改动:
git add <changed files>
git commit -m "refactor(iter-t0-1): C2 sweep — 同步迁移残留硬编码点 to status field

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

# 若无改动: 不 commit，仅在 PR description 中记录 "C2 grep 排查 0 命中"
```

---

## Task 11: T11 drift guard — 输入侧 (_record_action ↔ ADJUST_ACTIONS)

**Files:**
- Modify: `tests/test_derive_decision.py`（加 T11 + helper）

- [ ] **Step 11.1: 写 T11 测试 + helper**

加在 `tests/test_derive_decision.py` 末尾（`re` / `Path` 已在 Task 1 文件顶部 imports）：

```python
_ACTION_LITERAL_RE = re.compile(
    r'_record_action\b[^)]*?\baction\s*=\s*["\']([a-z_]+)["\']',
    re.DOTALL,
)
_EXPECTED_RECORD_ACTION_SITES = 11  # spec §8.3 实测


def _grep_record_action_literals(path: str) -> set[str]:
    """单行正则扫 _record_action(...) 调用块的 action 字面量。
    Sanity check 站点数 == 11 防 regex false-empty。"""
    src = Path(path).read_text()
    matches = _ACTION_LITERAL_RE.findall(src)
    assert len(matches) == _EXPECTED_RECORD_ACTION_SITES, (
        f"扫描站点数 {len(matches)} ≠ 期望 {_EXPECTED_RECORD_ACTION_SITES}（spec §8.3）；"
        f"可能 regex 失效或站点被重命名/新增 — 实测命中: {matches}"
    )
    return set(matches)


def test_t11_adjust_actions_drift_guard():
    """T11: tools_execution.py 内所有 _record_action action 字面量
    必须落入 derive_decision_type 的分类，否则新增 action 漏分类会静默落 hold。

    spec §5.4 — 与 Iter 5 D' tests/test_trader_agent.py:69 同款纪律。"""
    from src.cli.app import ADJUST_ACTIONS

    actual = _grep_record_action_literals("src/agent/tools_execution.py")
    expected = ADJUST_ACTIONS | {"open_position", "close_position", "set_next_wake"}
    drift = actual - expected
    assert not drift, \
        f"新增未分类的 action: {drift}（请更新 ADJUST_ACTIONS 或派生逻辑）"
```

- [ ] **Step 11.2: 跑 T11 验证 pass**

Run: `uv run pytest tests/test_derive_decision.py::test_t11_adjust_actions_drift_guard -v`
Expected: PASS — 11 站点全部落入分类（实测：8 ADJUST + open_position + close_position + set_next_wake = 11）

- [ ] **Step 11.3: Commit**

```bash
git add tests/test_derive_decision.py
git commit -m "test(iter-t0-1): T11 drift guard — _record_action sites ↔ ADJUST_ACTIONS 分类一致

spec §5.4 决议方案：单行正则 + 站点数 == 11 sanity check（防 regex false-empty）。
未来 Iter 7+ 加新 _record_action(action='X') 漏配置派生逻辑会被 CI 拦截。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 12: T12 drift guard — 输出侧（派生 enum 长度）

**Files:**
- Modify: `tests/test_derive_decision.py`（加 T12）

- [ ] **Step 12.1: 写 T12 测试**

加在 `tests/test_derive_decision.py` 末尾：

```python
def test_t12_derive_output_fits_decision_column():
    """T12: 派生函数输出 enum 字符串必须 ≤ DecisionLog.decision String(20)。

    spec §5.5 — 防未来加新 enum 超约束。
    legacy 不纳入此集合（historical-only，非派生函数运行时输出）。"""
    enum_values = {"open_long", "open_short", "close", "adjust", "hold", "derive_error"}
    over_limit = [v for v in enum_values if len(v) > 20]
    assert not over_limit, f"派生输出 > 20 chars: {over_limit}"
```

- [ ] **Step 12.2: 跑 T12 验证 pass**

Run: `uv run pytest tests/test_derive_decision.py::test_t12_derive_output_fits_decision_column -v`
Expected: PASS — max `derive_error` = 12 chars，远小于 20

- [ ] **Step 12.3: Commit**

```bash
git add tests/test_derive_decision.py
git commit -m "test(iter-t0-1): T12 drift guard — 派生输出 enum 长度 ≤ String(20)

spec §5.5 — 防未来加新 enum (如 'open_long_with_alert' = 19) 后再加一字符直接溢出。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 13: 跑全套测试 + 验证 spec § 7 / §6 SQL 准备

**Files:** none

- [ ] **Step 13.1: 跑全套测试**

Run: `uv run pytest -v 2>&1 | tail -30`
Expected: 全部 PASS — 实施前先 `uv run pytest --collect-only -q | tail -3` 取 baseline 数；新增 14 个测试：
- 8 (T1-T8 in test_derive_decision)
- 1 (T8.5 side 兜底)
- 1 (T8.6 derive_error fallback)
- 1 (T11 输入侧 drift guard)
- 1 (T12 输出侧 drift guard)
- 1 (T9 in test_usage_limits — 成功路径集成测)
- 1 (T10 in test_usage_limits — forensic 路径集成测，monkeypatch uuid)

→ 期望 baseline + 14 PASSED；具体数字以实跑 baseline 为准（memory `project_w2_prep_progress` 记录可能滞后）。T2 扩展只增 assert 不新增 function，不计入数量。

- [ ] **Step 13.2: Sanity re-grep（命令同 Task 10 Step 10.1）**

复跑 Task 10.1 的 4 条 grep — 因 Task 8/9 修改后再 sanity 一次确认 codebase 0 残留硬编码。**无新动作**，仅复跑确认；若 Task 10.1 已 0 命中且后续无 Task 引入新硬编码，可跳过。

- [ ] **Step 13.3: 准备 user smoke 指引**

写入 PR description（merge 后 user 操作）：

```markdown
## merge 后验证步骤（user 跑）

1. **重启 main.py** 让 Iter 3 migration apply（已就位）
2. 跑 1-3 cycle (`uv run python main.py --debug`)
3. spec §7 验证 SQL（4 项）：
   ```bash
   sqlite3 data/tradebot.db <<EOF
   -- (1) reasoning cap 已放
   SELECT MAX(LENGTH(reasoning)) FROM decision_logs
    WHERE created_at > datetime('2026-04-29T04:07:26+00:00');
   -- 期望 > 500（directional smoke check，可能需多跑几轮）

   -- (2) decision 派生 enum
   SELECT decision, COUNT(*) FROM decision_logs
    WHERE created_at > datetime('2026-04-29T04:07:26+00:00')
    GROUP BY decision;
   -- 期望 open_long/open_short/close/adjust/hold（不再有 'completed'）

   -- (3) status 写入正确
   SELECT status, COUNT(*) FROM decision_logs
    WHERE created_at > datetime('2026-04-29T04:07:26+00:00')
    GROUP BY status;
   -- 期望 normal=ok / forensic=usage_limit_exceeded

   -- (4) market_summary 全 NULL
   SELECT COUNT(*) FROM decision_logs
    WHERE created_at > datetime('2026-04-29T04:07:26+00:00')
      AND market_summary IS NOT NULL;
   -- 期望 0
   EOF
   ```
4. **第一步窗口期回填 SQL（spec §6.1）**:
   ```bash
   sqlite3 data/tradebot.db <<EOF
   -- 识别窗口期 mismatch 行
   SELECT id, created_at, decision, status FROM decision_logs
   WHERE decision = 'usage_limit_exceeded'
     AND status = 'ok'
     AND datetime(created_at) > datetime('2026-04-29T04:07:26+00:00');

   -- 回填
   UPDATE decision_logs
      SET status = 'usage_limit_exceeded', decision = 'legacy'
    WHERE decision = 'usage_limit_exceeded'
      AND status = 'ok'
      AND datetime(created_at) > datetime('2026-04-29T04:07:26+00:00');

   -- 验证回填（应 0 行）
   SELECT COUNT(*) FROM decision_logs
    WHERE decision = 'usage_limit_exceeded'
      AND status = 'ok'
      AND datetime(created_at) > datetime('2026-04-29T04:07:26+00:00');
   EOF
   ```
```

- [ ] **Step 13.4: review-before-commit gate（按 user feedback memory）**

向用户报告：
- 所有 task 实施完成 + 测试全 PASS
- 提交 PR 前由用户决议 PR description / merge 时机 / 是否需要进一步 review

不自动 commit / merge / push — 等用户指令。

- [ ] **Step 13.5: PR description 加 W2 follow-up checklist（spec §11.1）**

PR description（除 Step 13.3 user smoke 指引外）加一节，确保 spec §11.1 三件事不遗漏：

```markdown
## merge 时 W2 follow-up checklist（spec §11.1）

- [ ] **memory 同步锚点**: 在 `project_w2_prep_progress.md` 加 Iter 4 ✅ landed 行 + commit hash
- [ ] **memory 加 hold 双义 caveat 锚点**: `project_w2_prep_progress.md` 或独立 `project_w2_sql_analysis_caveats.md`（若建立）记录：
  - `decision='hold'` 双义：(a) cycle 0 actions（纯观察），(b) cycle 仅含 `set_next_wake`（排下次醒）
  - 区分需 JOIN trade_actions WHERE action='set_next_wake' 二次过滤
- [ ] **memory 加 derive_error vs legacy 边界锚点**:
  - `legacy` historical-only（W1 109 行 Iter 3 backfill）
  - `derive_error` runtime DB 故障兜底（spec §3.2）
  - `COUNT(*) WHERE decision='derive_error'` 可作为 DB 健康度监控指标
```

实施载体：merge 后单独跑 memory 写入，不进 PR commit。

**memory 写入草稿（merge 后 user/agent 操作）**：

1. 在 `/Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/project_w2_prep_progress.md` Iter 4 行勾 ✅ + 写 commit hash + 关键 caveat（沿用 Iter 3 风格）
2. 在 `MEMORY.md` 加（如未存在）一行新 memory 锚点：
   ```
   - [Iter 4 SQL caveats](project_iter4_sql_caveats.md) — hold 双义 + derive_error vs legacy 边界
   ```
3. 新建 `project_iter4_sql_caveats.md`（或合并到既有 `project_w2_prep_progress.md` 末段）含三条：
   - `decision='hold'` 双义：(a) cycle 0 actions / (b) 仅 set_next_wake；区分需 JOIN trade_actions WHERE action='set_next_wake'
   - `decision='legacy'` historical-only（Iter 3 backfill 109 行）；`decision='derive_error'` runtime DB 故障兜底
   - DB 健康度监控指标：`SELECT COUNT(*) FROM decision_logs WHERE decision='derive_error'`

---

## Self-Review

### Spec coverage

| Spec 要求 | Plan 覆盖 |
|---|---|
| G1 成功路径派生 + status='ok' + reasoning[:4000] + 维持不传 market_summary | Task 8 |
| G2 forensic 路径派生 + status='usage_limit_exceeded' + reasoning[:4000] | Task 9 |
| G3 `_derive_decision_from_actions` private helper | Task 1-7 |
| G4 测试迁移 (test_usage_limits.py:103 + grep + 8 单测 + 集成测增量 T9/T10) | Task 5/6/8/9/10；**T9 集成测**（Task 8 — 成功路径写 status='ok' + reasoning[:4000]）+ **T10 集成测**（Task 9 Step 9.1，`monkeypatch.setattr("src.cli.app.uuid.uuid4", ...)` 钉住 cycle_id 后真实派生路径覆盖 forensic 路径反查 trade_actions 派生 'open_long'）+ **T2 扩展含 status/decision 双断言**（forensic mock 路径无 trade_actions 派生 'hold' + status='usage_limit_exceeded'） |
| G5 第一步窗口期 SQL 回填 | Task 13 (PR description) |
| §C1 派生 forensic 路径用同 derive func | Task 9 |
| §C5 set_next_wake 单独归 hold | Task 1 (ADJUST_ACTIONS) + Task 5 (T6) |
| §3.2 ORDER BY + try/except (SQLAlchemyError, OSError) + fallback derive_error | Task 1 + Task 7 |
| §3.5 side ∉ {long, short} 兜底 | Task 6 (T8.5) |
| §5.1 8 个单测 (T1-T8) | Task 1-5 |
| §5.4 T11 drift guard 强制 | Task 11 |
| §5.5 T12 drift guard 强制 | Task 12 |
| §8.1 derive_error 独立 enum | Task 1 (helper) + Task 7 (T8.6) |
| Y1 models.py:92 注释 500→4000 | Task 8 |
| §11.1 W2 follow-up 锚点 | Task 13 Step 13.5 — PR description 三项 checklist（memory 同步 / hold 双义 caveat / derive_error vs legacy 边界）；merge 后单独跑 memory 写入 |

✅ 全部 spec 要求映射到 task。

### Placeholder scan

✅ 无 TBD / TODO / "implement later" / "similar to Task N" / 无具体代码的步骤；每个改动步给完整代码块 + 期望命令输出。

### Type consistency

✅ `_derive_decision_from_actions` 签名、`ADJUST_ACTIONS` 名称、`(SQLAlchemyError, OSError)` 异常元组、`'derive_error'` 字面量在所有 task 内一致。

---

**Plan ends.** 13 tasks 总计 4-6 step 每个；按 TDD 严格分解；frequent commits（每 task 末尾 commit）。

---

## Execution Handoff

Plan 完成。两种执行模式二选一：

1. **Subagent-Driven（推荐）** — 主对话每 task 派 fresh subagent 实施，subagent 不带历史上下文（专注当 task scope）；主对话两阶段 review（task 末尾 commit 前主对话审 + 主对话末尾 PR 前总审）。
   - 优势：上下文隔离，子任务不被主对话噪音污染；review gate 多
   - 必读 sub-skill: `superpowers:subagent-driven-development`

2. **Inline Execution** — 主对话直接顺序跑 task；按既有节奏在每 N task 后 checkpoint review。
   - 优势：快，无 subagent 启动开销；连续上下文便于 cross-task 决议
   - 必读 sub-skill: `superpowers:executing-plans`

**对当前 Iter 4 的判断**：13 task 单一 PR scope / 半天工作量 / TDD 节奏紧凑 — Inline 更高效；Subagent-Driven 隔离收益不明显（无大型独立子模块）。但选择由 user 决议。

由 user 选择后再启动实施。

