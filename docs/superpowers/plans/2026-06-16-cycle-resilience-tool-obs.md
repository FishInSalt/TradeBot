# Cycle 崩溃退避重唤 + 自收敛工具不可用可观测化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cycle 崩溃（`retry_exhausted`）后用 DB 派生的指数退避主动重唤，并把 12 个网络型感知工具的「整体不可用」自收敛降级从静默 `ok` 提升为可观测的 `biz_error('source_unavailable')`。

**Architecture:** 两个技术独立的半边，同属「工具不可用的处理」一个问题域。半 A（崩溃退避）：在 `run_agent_cycle` 的 `retry_exhausted` 终态分支，写完 crash 行后查询本会话尾部连续 `retry_exhausted` 数 `n`，经纯函数 `backoff_min(n, fallback)` 算出退避分钟数，调现成的 `deps.set_next_wake_fn` 设下次重唤（封顶 = 会话兜底间隔）。半 B（可观测化）：`note_biz_error("source_unavailable")` 三态记录管道已成型，只需把 `source_unavailable` 加入白名单、在 12 个工具的总失败返回点打点、把拼写 drift-guard 扫描扩到 `src/agent/`；下游 metrics 与 WebUI 渲染管道零改动自动接住。

**Tech Stack:** Python 3.12 / pydantic-ai capability（`ToolCallRecorder.wrap_tool_execute` 三态 + ContextVar side-channel）/ SQLAlchemy async + SQLite / APScheduler-like 自研 `Scheduler`（one-shot `_next_interval` 覆盖）/ pytest + pytest-asyncio。

设计依据：`docs/superpowers/specs/2026-06-16-cycle-resilience-tool-obs-design.md`（§1 退避 / §2 可观测化 / §3 崩溃语义显式化）。

---

## File Structure

**半 A — 崩溃退避（§1 + §3 的崩溃语义）**

- `src/agent/trader.py` — `TradingDeps` dataclass 新增 `scheduler_interval_min: int = 60` 字段（退避封顶来源；`run_agent_cycle` 签名与 `deps` 现有字段都拿不到）。
- `src/cli/app.py` — 三处新增（均为 module-level helper + 一处 wiring + 一处崩溃分支调用）：
  - `backoff_min(n, fallback)` 纯函数（退避曲线）。
  - `_CRASH_STREAK_FETCH_CAP` 常量 + `_count_consecutive_retry_exhausted(engine, session_id)` async helper（DB 派生连崩计数）。
  - `_schedule_crash_backoff(engine, deps, err_class)` async helper（编排：None-guard + fail-isolation + 调 `set_next_wake_fn`）。
  - `build_services` deps 构造处接线 `scheduler_interval_min=result.scheduler_interval_min`。
  - `retry_exhausted` 终态分支（写完 crash 行 commit 后）调 `await _schedule_crash_backoff(...)`。

**半 B — 可观测化（§2 + §3 的工具显式化）**

- `src/services/tool_call_recorder.py` — `BIZ_ERROR_TYPES` 白名单新增 `"source_unavailable"`。
- `src/agent/tools_perception.py` — module-level `from src.services.tool_call_recorder import note_biz_error`；12 个网络型工具的「整体不可用」返回点（共 15 处）前插 `note_biz_error("source_unavailable")`；`get_market_data` 裸 fetch 处加崩溃语义注释。
- `tests/test_tool_call_recorder.py` — `test_biz_error_types_drift_guard` 扫描范围从仅 `tools_execution.py` 扩到 `src/agent/` 全目录。
- `tests/test_tool_unavailability_biz_error.py`（新建）— 半 B 全部行为测试：12 工具 POINT 打点 + outage-sentinel 路径 + 「保持 ok」反例（partial / insufficient / schema-drift / market_news）+ §3 崩溃穿透（get_market_data / get_open_orders）。
- `tests/test_cli_app_crash_backoff.py`（新建）— 半 A 全部行为测试：`backoff_min` 曲线 / DB 计数 / 触发条件 / None-guard / fail-isolation / 端到端 retry_exhausted 重唤。

两个新建测试文件按「行为半边」分文件（changes-together-live-together）：半 A 的调度/退避逻辑与半 B 的工具打点逻辑无共享 fixture，无理由混居。

---

## Task 0: 提交计划文档（先于任何代码改动）

**Files:**
- Commit: `docs/superpowers/plans/2026-06-16-cycle-resilience-tool-obs.md`

> Per memory `feedback_plan_doc_commit_first`：plan 文档作独立 commit，先于全部 impl commit。spec 已于 `b34879c` 落库。

- [ ] **Step 1: 提交计划文档**

```bash
cd /Users/z/Z/TradeBot/.claude/worktrees/iter-cycle-resilience-tool-obs
git add docs/superpowers/plans/2026-06-16-cycle-resilience-tool-obs.md
git commit -m "$(cat <<'EOF'
docs(plan): Cycle 崩溃退避重唤 + 自收敛工具不可用可观测化实施计划

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# 半 A — 跨 cycle 崩溃退避重唤

## Task A1: `TradingDeps.scheduler_interval_min` 字段 + wiring

**Files:**
- Modify: `src/agent/trader.py:42-46`（`TradingDeps` 字段区）
- Modify: `src/cli/app.py:974-976`（`build_services` 内 `deps = TradingDeps(...)` 构造）
- Test: `tests/test_cli_app_crash_backoff.py`

- [ ] **Step 1: 写失败测试（字段存在 + 默认值 + 可赋值）**

新建 `tests/test_cli_app_crash_backoff.py`，写入文件头 + 本测试：

```python
"""半 A — 跨 cycle 崩溃退避重唤（spec §1）。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from src.storage.models import AgentCycle


def test_trading_deps_has_scheduler_interval_min_field():
    """TradingDeps 暴露 scheduler_interval_min（退避封顶来源），默认 60，可覆写。"""
    from src.agent.trader import TradingDeps

    deps = TradingDeps(
        symbol="BTC/USDT:USDT", timeframe="15m",
        market_data=MagicMock(), exchange=MagicMock(), technical=MagicMock(),
        memory=MagicMock(), session_id="s",
    )
    assert deps.scheduler_interval_min == 60

    deps2 = TradingDeps(
        symbol="BTC/USDT:USDT", timeframe="15m",
        market_data=MagicMock(), exchange=MagicMock(), technical=MagicMock(),
        memory=MagicMock(), session_id="s", scheduler_interval_min=30,
    )
    assert deps2.scheduler_interval_min == 30
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_cli_app_crash_backoff.py::test_trading_deps_has_scheduler_interval_min_field -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'scheduler_interval_min'`

- [ ] **Step 3: 加 dataclass 字段**

`src/agent/trader.py`，在 `wake_max_minutes: int = 60` 之后插入新字段：

```python
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    # 崩溃退避封顶来源（spec §1「fallback 来源」）：run_agent_cycle 签名与 deps 现有
    # 字段都拿不到会话兜底间隔，且无法由 wake_max_minutes 反推（_compute_max_wake 在
    # x≤15 恒 60 / x≥45 恒 180 两端不可逆）。wiring 时由 build_services 赋实值；
    # 默认 60 仅为单测/旧路径兜底。
    scheduler_interval_min: int = 60
    set_next_wake_fn: Callable[[int, str], None] | None = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_cli_app_crash_backoff.py::test_trading_deps_has_scheduler_interval_min_field -v`
Expected: PASS

- [ ] **Step 5: wiring — build_services 赋实值**

`src/cli/app.py`，`build_services` 内 `deps = TradingDeps(...)` 构造块末尾（现 `wake_min_minutes=1, wake_max_minutes=max_wake,` 之后）追加一行：

```python
        wake_min_minutes=1,
        wake_max_minutes=max_wake,
        scheduler_interval_min=result.scheduler_interval_min,   # spec §1: 退避封顶来源
    )
```

- [ ] **Step 6: 跑既有 app/启动测试确认 wiring 不破坏构造**

Run: `pytest tests/test_cli_app.py -q`
Expected: PASS（构造未破坏；新字段有默认值，既有调用不受影响）

- [ ] **Step 7: 提交**

```bash
git add src/agent/trader.py src/cli/app.py tests/test_cli_app_crash_backoff.py
git commit -m "$(cat <<'EOF'
feat(scheduler): TradingDeps 新增 scheduler_interval_min（崩溃退避封顶来源）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task A2: `backoff_min` 退避曲线纯函数

**Files:**
- Modify: `src/cli/app.py`（在 `_compute_max_wake` 之后、`_capture_session_system_prompt` 之前，约 line 800 新增 module-level 函数）
- Test: `tests/test_cli_app_crash_backoff.py`

- [ ] **Step 1: 写失败测试（曲线参数化）**

在 `tests/test_cli_app_crash_backoff.py` 追加：

```python
@pytest.mark.parametrize("n, fallback, expected", [
    # fallback=1 → floor=min(2,1)=1 → 恒 1（no-op，本就每分钟巡检）
    (1, 1, 1), (5, 1, 1),
    # fallback=60 → 2,4,8,16,32,60(封顶),60…
    (1, 60, 2), (2, 60, 4), (3, 60, 8), (4, 60, 16),
    (5, 60, 32), (6, 60, 60), (7, 60, 60),
    # fallback=180 → 2,4,…,128,180(封顶)
    (1, 180, 2), (7, 180, 128), (8, 180, 180), (12, 180, 180),
])
def test_backoff_min_curve(n, fallback, expected):
    from src.cli.app import backoff_min
    assert backoff_min(n, fallback) == expected
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_cli_app_crash_backoff.py::test_backoff_min_curve -v`
Expected: FAIL — `ImportError: cannot import name 'backoff_min'`

- [ ] **Step 3: 实现纯函数**

`src/cli/app.py`，在 `_compute_max_wake`（结尾 line 799 `return min(...)`）之后插入：

```python
def backoff_min(n: int, fallback: int) -> int:
    """崩溃重唤退避分钟数（spec §1 退避曲线纯函数）。

    n        = 连续 retry_exhausted 次数（≥1）。
    fallback = scheduler_interval_min（会话兜底间隔，即封顶）。

    curve: min(fallback, floor · 2^(n-1)), floor = min(2, fallback)
      fallback=60  → 2,4,8,16,32,60(封顶),60…
      fallback=180 → 2,4,…,128,180(封顶)
      fallback=1   → floor 被 min(2,1) 压成 1 → 恒 1（no-op）

    封顶是兜底间隔而非 wake_max_minutes：崩溃后最坏退回会话正常巡检节奏，绝不更慢。
    """
    floor = min(2, fallback)
    return min(fallback, floor * 2 ** (n - 1))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_cli_app_crash_backoff.py::test_backoff_min_curve -v`
Expected: PASS（14 个参数组合全绿）

- [ ] **Step 5: 提交**

```bash
git add src/cli/app.py tests/test_cli_app_crash_backoff.py
git commit -m "$(cat <<'EOF'
feat(scheduler): backoff_min 退避曲线纯函数（封顶=会话兜底间隔）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task A3: DB 派生连崩计数 `_count_consecutive_retry_exhausted`

**Files:**
- Modify: `src/cli/app.py`（在 `backoff_min` 之后新增常量 + async helper）
- Test: `tests/test_cli_app_crash_backoff.py`

- [ ] **Step 1: 写失败测试（尾部连续计数 / 遇非 RE 即止 / 会话隔离 / cap）**

在 `tests/test_cli_app_crash_backoff.py` 追加：

```python
async def _add_cycle(db_session, session_id, status):
    db_session.add(AgentCycle(
        session_id=session_id, cycle_id="c", triggered_by="scheduled",
        execution_status=status,
    ))
    await db_session.commit()


@pytest.mark.asyncio
async def test_count_consecutive_retry_exhausted_stops_at_first_non_re(db_engine, db_session):
    """末尾连续 retry_exhausted 计数，遇首个非 RE（含中间夹 ok）即止。"""
    from src.cli.app import _count_consecutive_retry_exhausted

    # 插入顺序 = id 升序；newest-first 看尾部：RE, RE, ok(止)
    for st in ["ok", "ok", "retry_exhausted", "ok", "retry_exhausted", "retry_exhausted"]:
        await _add_cycle(db_session, "sess-A", st)

    n = await _count_consecutive_retry_exhausted(db_engine, "sess-A")
    assert n == 2


@pytest.mark.asyncio
async def test_count_consecutive_single_crash(db_engine, db_session):
    """会话首个 cycle 即崩 → n=1。"""
    from src.cli.app import _count_consecutive_retry_exhausted
    await _add_cycle(db_session, "sess-B", "retry_exhausted")
    assert await _count_consecutive_retry_exhausted(db_engine, "sess-B") == 1


@pytest.mark.asyncio
async def test_count_consecutive_is_session_scoped(db_engine, db_session):
    """计数只看本会话；别的会话的 RE 不串味。"""
    from src.cli.app import _count_consecutive_retry_exhausted
    await _add_cycle(db_session, "sess-C", "retry_exhausted")
    await _add_cycle(db_session, "sess-D", "retry_exhausted")
    await _add_cycle(db_session, "sess-D", "retry_exhausted")
    assert await _count_consecutive_retry_exhausted(db_engine, "sess-C") == 1
    assert await _count_consecutive_retry_exhausted(db_engine, "sess-D") == 2


@pytest.mark.asyncio
async def test_count_consecutive_capped(db_engine, db_session):
    """连崩超过 fetch cap → 返回 cap（曲线已饱和，超出部分无意义）。"""
    from src.cli.app import _count_consecutive_retry_exhausted, _CRASH_STREAK_FETCH_CAP
    for _ in range(_CRASH_STREAK_FETCH_CAP + 5):
        await _add_cycle(db_session, "sess-E", "retry_exhausted")
    assert await _count_consecutive_retry_exhausted(db_engine, "sess-E") == _CRASH_STREAK_FETCH_CAP
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_cli_app_crash_backoff.py -k count_consecutive -v`
Expected: FAIL — `ImportError: cannot import name '_count_consecutive_retry_exhausted'`

- [ ] **Step 3: 实现常量 + async helper**

`src/cli/app.py`，在 `backoff_min` 之后插入：

```python
# 退避曲线在 n≈8 即饱和到 fallback（floor·2^7=256 > 最大 fallback 180），故连崩计数
# 取到 cap 即可——超出部分既不改退避值、又能 bound 内存 + 防 2^(n-1) 大整数膨胀。
_CRASH_STREAK_FETCH_CAP = 16


async def _count_consecutive_retry_exhausted(engine, session_id: str) -> int:
    """本会话尾部连续 retry_exhausted 的 cycle 数（spec §1「连续崩溃计数」）。

    按 id 倒序（自增 PK 严格单调）从最新 cycle 起数，遇首个非 retry_exhausted 即止。
    不用 created_at DESC——SQLite DateTime(timezone=True) 读回 naive（feedback_sqlite_
    naive_datetime_readback）且同秒并列无序。fetch 上限 _CRASH_STREAK_FETCH_CAP：曲线
    已饱和，streak ≥ cap 与 = cap 产出同一（封顶）退避。
    """
    async with get_session(engine) as session:
        rows = await session.execute(
            select(AgentCycle.execution_status)
            .where(AgentCycle.session_id == session_id)
            .order_by(AgentCycle.id.desc())
            .limit(_CRASH_STREAK_FETCH_CAP)
        )
        n = 0
        for (status,) in rows:
            if status == "retry_exhausted":
                n += 1
            else:
                break
        return n
```

> `select` / `AgentCycle` / `get_session` 均已在 `src/cli/app.py` 顶部 import（line 15 / 56 / 55），无需新增 import。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_cli_app_crash_backoff.py -k count_consecutive -v`
Expected: PASS（4 个测试全绿）

- [ ] **Step 5: 提交**

```bash
git add src/cli/app.py tests/test_cli_app_crash_backoff.py
git commit -m "$(cat <<'EOF'
feat(scheduler): DB 派生连崩计数（id 倒序 + fetch cap）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task A4: `_schedule_crash_backoff` 编排 + 接入 retry_exhausted 分支

**Files:**
- Modify: `src/cli/app.py`（在 `_count_consecutive_retry_exhausted` 之后新增 async helper；在 retry_exhausted 分支 commit 后调用）
- Test: `tests/test_cli_app_crash_backoff.py`

- [ ] **Step 1: 写失败测试（编排单元 + 端到端）**

在 `tests/test_cli_app_crash_backoff.py` 追加。先是 `_schedule_crash_backoff` 的三条单元（None-guard / fail-isolation / 正常算值），再是端到端两条（retry_exhausted 触发 / usage_limit 不触发）：

```python
# --- _schedule_crash_backoff 单元 ---

@pytest.mark.asyncio
async def test_schedule_crash_backoff_none_fn_no_raise(db_engine, db_session):
    """set_next_wake_fn=None（非交互/单测路径）→ 跳过不抛。"""
    from src.cli.app import _schedule_crash_backoff
    deps = MagicMock()
    deps.set_next_wake_fn = None
    await _schedule_crash_backoff(db_engine, deps, "RequestTimeout")  # 不抛即通过


@pytest.mark.asyncio
async def test_schedule_crash_backoff_normal_value(db_engine, db_session):
    """已有 1 条 RE 行 → n=1 → backoff_min(1, 60)=2；context 带 err_class。"""
    from src.cli.app import _schedule_crash_backoff
    await _add_cycle(db_session, "sess-F", "retry_exhausted")
    calls = []
    deps = MagicMock()
    deps.session_id = "sess-F"
    deps.scheduler_interval_min = 60
    deps.set_next_wake_fn = lambda minutes, ctx: calls.append((minutes, ctx))

    await _schedule_crash_backoff(db_engine, deps, "RequestTimeout")
    assert len(calls) == 1
    minutes, ctx = calls[0]
    assert minutes == 2
    assert ctx.startswith("crash-backoff:")
    assert "RequestTimeout" in ctx


@pytest.mark.asyncio
async def test_schedule_crash_backoff_count_query_failure_uses_floor(db_engine, monkeypatch):
    """计数查询自身失败 → fail-isolated 回退 n=1（floor），不二次击穿崩溃路径。"""
    from src.cli import app as app_mod
    calls = []
    deps = MagicMock()
    deps.session_id = "sess-G"
    deps.scheduler_interval_min = 60
    deps.set_next_wake_fn = lambda minutes, ctx: calls.append((minutes, ctx))

    async def _boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(app_mod, "_count_consecutive_retry_exhausted", _boom)

    await app_mod._schedule_crash_backoff(db_engine, deps, "RequestTimeout")
    assert calls == [(2, "crash-backoff: RequestTimeout")]   # 回退 n=1 → backoff_min(1, 60)=2=floor


# --- 端到端：run_agent_cycle 崩溃路径 ---

def _mock_agent():
    agent = MagicMock()
    return agent


@pytest.mark.asyncio
async def test_retry_exhausted_schedules_backoff(deps_factory, db_engine, db_session):
    """3 attempt 全崩 → 写 retry_exhausted 行 + 调 set_next_wake_fn（值=曲线、context=crash-backoff）。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps = deps_factory()
    deps.scheduler_interval_min = 60
    calls = []
    deps.set_next_wake_fn = lambda minutes, ctx: calls.append((minutes, ctx))

    agent = _mock_agent()
    agent.run = AsyncMock(side_effect=RuntimeError("network down"))

    with patch("asyncio.sleep", new=AsyncMock()):
        await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "retry_exhausted"
    # 本会话仅此 1 条 RE → n=1 → backoff_min(1, 60)=2
    assert len(calls) == 1
    minutes, ctx = calls[0]
    assert minutes == 2
    assert ctx.startswith("crash-backoff:")
    assert "RuntimeError" in ctx


@pytest.mark.asyncio
async def test_usage_limit_does_not_schedule_backoff(deps_factory, db_engine, db_session):
    """usage_limit_exceeded 是病理死循环 → 不退避重唤（spec §1 排除）。"""
    from src.cli.app import TokenBudget, run_agent_cycle
    from pydantic_ai.exceptions import UsageLimitExceeded

    deps = deps_factory()
    deps.scheduler_interval_min = 60
    calls = []
    deps.set_next_wake_fn = lambda minutes, ctx: calls.append((minutes, ctx))

    agent = _mock_agent()
    agent.run = AsyncMock(side_effect=UsageLimitExceeded("runaway"))

    with patch("asyncio.sleep", new=AsyncMock()):
        await run_agent_cycle(agent, deps, [("scheduled", None)], TokenBudget(daily_max=10**7), db_engine)

    row = (await db_session.execute(
        select(AgentCycle).order_by(AgentCycle.id.desc()).limit(1))).scalar_one()
    assert row.execution_status == "usage_limit_exceeded"
    assert calls == [], "usage_limit 不应触发退避重唤"
```

> `deps_factory` / `db_engine` / `db_session` 来自 `tests/conftest.py`（line 105 / 121 / 129）；`deps_factory()` 造的 `TradingDeps` 已带 `scheduler_interval_min=60` 默认值（Task A1），测试再显式赋一遍以钉死预期。`UsageLimitExceeded` 的构造签名以 `pydantic_ai.exceptions` 实际为准——若构造参数不同，按其签名调整（仅影响测试，不影响实现）。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_cli_app_crash_backoff.py -k "schedule_crash_backoff or retry_exhausted_schedules or usage_limit_does_not" -v`
Expected: FAIL — `ImportError: cannot import name '_schedule_crash_backoff'`（端到端两条会因尚未接线而 `calls` 为空 FAIL）

- [ ] **Step 3: 实现 `_schedule_crash_backoff` helper**

`src/cli/app.py`，在 `_count_consecutive_retry_exhausted` 之后插入：

```python
async def _schedule_crash_backoff(engine, deps: TradingDeps, err_class: str) -> None:
    """崩溃终态后设指数退避重唤（spec §1）。仅在 retry_exhausted 分支调用。

    None-guard：set_next_wake_fn 未接线（非交互 / 单测）→ 跳过，退回默认 _interval。
    fail-isolation：计数查询自身失败 → 回退 n=1（floor），不让计数错误二次击穿崩溃路径。
    """
    if deps.set_next_wake_fn is None:
        return
    fallback = deps.scheduler_interval_min
    try:
        n = await _count_consecutive_retry_exhausted(engine, deps.session_id)
    except Exception:
        logger.warning("crash-backoff count query failed; falling back to floor", exc_info=True)
        n = 1
    n = max(1, n)   # 防御：崩溃行已 commit 故 ≥1，但守住 backoff_min 的 n≥1 契约
    minutes = backoff_min(n, fallback)
    deps.set_next_wake_fn(minutes, f"crash-backoff: {err_class}")
    logger.info("crash-backoff: n=%d → next wake in %dmin (%s)", n, minutes, err_class)
```

- [ ] **Step 4: 接入 retry_exhausted 分支**

`src/cli/app.py`，retry_exhausted 终态分支：在 crash 行 `await session.commit()`（约 line 651，`async with get_session(engine)` 块末）之后、`cycle_ended_at` 捕获注释之前插入调用。

定位锚点（现状）：

```python
                    await session.commit()
                # capture cycle_ended_at AFTER DB commit — 与正常路径 + UsageLimitExceeded 路径
                # 时序对齐：Footer Duration 字段语义统一为 "实墙时间含 DB 写入"
                cycle_ended_at = datetime.now(timezone.utc)
```

改为：

```python
                    await session.commit()
                # crash-backoff 重唤（spec §1）：仅 retry_exhausted（不含 usage_limit——病理死
                # 循环不重试）。DB 派生连崩计数 → 指数退避，封顶 = 会话兜底间隔。None-guard +
                # fail-isolated。须在 crash 行 commit 之后，使本行被计入 n。
                await _schedule_crash_backoff(engine, deps, err_class)
                # capture cycle_ended_at AFTER DB commit — 与正常路径 + UsageLimitExceeded 路径
                # 时序对齐：Footer Duration 字段语义统一为 "实墙时间含 DB 写入"
                cycle_ended_at = datetime.now(timezone.utc)
```

> 仅改 `retry_exhausted`（`except Exception` 的 `else` 分支，约 line 619-668）。`UsageLimitExceeded` 分支（line 564-612）**不动**——其无 backoff 调用即满足「排除 usage_limit」。`err_class` 已在 line 622 定义（`err_class = type(e).__name__`），在作用域内。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_cli_app_crash_backoff.py -v`
Expected: PASS（半 A 全部测试绿）

- [ ] **Step 6: 跑既有崩溃路径测试确认无回归**

Run: `pytest tests/test_midcycle_forensics.py tests/test_cli_app.py -q`
Expected: PASS（retry_exhausted / usage_limit 既有 forensic 行为不变）

- [ ] **Step 7: 提交**

```bash
git add src/cli/app.py tests/test_cli_app_crash_backoff.py
git commit -m "$(cat <<'EOF'
feat(scheduler): retry_exhausted 崩溃后指数退避主动重唤

写完 crash 行后按本会话连崩计数算退避分钟数，调 set_next_wake_fn 设
下次重唤。封顶=会话兜底间隔；usage_limit 不触发；None-guard+fail-isolated。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# 半 B — 自收敛工具不可用可观测化

## Task B1: `source_unavailable` 白名单 + drift-guard 扩扫描

**Files:**
- Modify: `src/services/tool_call_recorder.py:57-61`（`BIZ_ERROR_TYPES`）
- Modify: `tests/test_tool_call_recorder.py:415-432`（`test_biz_error_types_drift_guard`）

- [ ] **Step 1: 改 drift-guard 测试为扫 `src/agent/` 全目录（先红）**

`tests/test_tool_call_recorder.py`，替换 `test_biz_error_types_drift_guard` 整个函数：

```python
def test_biz_error_types_drift_guard():
    """BIZ_ERROR_TYPES 集合 vs `note_biz_error("...")` 字面引用一致。
    扫 src/agent/ 全目录（含 tools_execution.py + tools_perception.py）所有
    note_biz_error 调用，断言 string literal 全部 ∈ BIZ_ERROR_TYPES。

    扩到 src/agent/（原仅 tools_execution.py）：note_biz_error 对未知 type 是
    log-error-then-skip（记成 ok），perception 里拼错字面量（如 source_unavailble）
    会逃过 guard 并静默落回盲区——正是本 iter 要消灭的。
    """
    import re
    from src.services.tool_call_recorder import BIZ_ERROR_TYPES

    pattern = re.compile(r'note_biz_error\(["\']([a-z_]+)["\']\)')
    cited: set[str] = set()
    for py in (_REPO_ROOT / "src/agent").rglob("*.py"):
        cited |= set(pattern.findall(py.read_text()))

    drift = cited - BIZ_ERROR_TYPES
    assert not drift, \
        f"src/agent/ 引用未注册的 biz error type: {drift}（请在 BIZ_ERROR_TYPES 注册或更正字面量）"

    # Sanity: R2-4 应 instrument ≥ 3 处（spec §4.3）
    assert len(cited) >= 3, \
        f"应 instrument ≥3 处 note_biz_error；实测 {len(cited)} 处: {cited}"
```

> 此刻 perception 尚未打点，`cited` 仍只含 tools_execution 的 3 个旧 type，全部 ∈ 白名单 → 本测试此时应 **PASS**（扩扫描本身不引入 drift）。它真正的红→绿发生在 Task B3：若 B3 打点时拼错 `source_unavailable`，本 guard 会变红。

- [ ] **Step 2: 跑测试确认仍绿（扩扫描不破坏现状）**

Run: `pytest tests/test_tool_call_recorder.py::test_biz_error_types_drift_guard -v`
Expected: PASS（扫到的字面量仍是旧 3 type，⊆ 白名单）

- [ ] **Step 3: 写白名单断言测试（先红）**

`tests/test_tool_call_recorder.py`，在 `test_biz_error_types_drift_guard` 之后追加：

```python
def test_source_unavailable_in_biz_error_types():
    """source_unavailable 已注册（自收敛工具不可用打点用，spec §2）。"""
    from src.services.tool_call_recorder import BIZ_ERROR_TYPES
    assert "source_unavailable" in BIZ_ERROR_TYPES
```

- [ ] **Step 4: 跑测试确认失败**

Run: `pytest tests/test_tool_call_recorder.py::test_source_unavailable_in_biz_error_types -v`
Expected: FAIL — `assert 'source_unavailable' in frozenset({...})`

- [ ] **Step 5: 加白名单条目**

`src/services/tool_call_recorder.py`，`BIZ_ERROR_TYPES` 追加：

```python
BIZ_ERROR_TYPES: frozenset[str] = frozenset({
    "invalid_threshold_range",        # set_price_volatility_alert 阈值越界
    "invalid_alert_id_format",        # cancel_price_level_alert 协议错（非 8-char hex）
    "alert_not_found",                # update_price_level_alert 状态错（已触发 / 已被 close-fill 清理 / 未注册）
    "source_unavailable",             # 感知工具因外部源不可达而整体无数据可返回（spec 2026-06-16 §2）
})
```

- [ ] **Step 6: 跑测试确认通过**

Run: `pytest tests/test_tool_call_recorder.py -k "source_unavailable or drift_guard" -v`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add src/services/tool_call_recorder.py tests/test_tool_call_recorder.py
git commit -m "$(cat <<'EOF'
feat(metrics): 注册 source_unavailable biz_error type + drift-guard 扩扫 src/agent/

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task B2: 12 工具 biz_error 打点测试（新建文件，先红）

**Files:**
- Create: `tests/test_tool_unavailability_biz_error.py`

设计：直接调用工具 impl 函数，用 `_biz_error_type` ContextVar 作 seam（镜像 `ToolCallRecorder.wrap_tool_execute` 在 handler 返回后读取 ContextVar 的真实行为）。验证「工具到达总失败返回点 + 调 note_biz_error('source_unavailable') + 该 type 被白名单接受」三件事。recorder→DB（ContextVar → status='biz_error'）的翻译已由 `tests/test_tool_call_recorder.py::test_records_biz_error_when_note_biz_error_called` 等覆盖，不重复。

- [ ] **Step 1: 写文件头 + MockDeps + seam helper + 全部测试**

新建 `tests/test_tool_unavailability_biz_error.py`：

```python
"""半 B — 自收敛工具不可用可观测化（spec §2 + §3）。

seam：工具调 note_biz_error('source_unavailable') 写 ContextVar；这里在工具返回后读
该 ContextVar（= recorder 的读取点），验证打点是否发生。ContextVar→DB 翻译另有覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.tool_call_recorder import _biz_error_type


@dataclass
class MockDeps:
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    market_data: object = None
    exchange: object = None
    technical: object = None
    memory: object = None
    session_id: str = "test"
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None


async def _biz_after(coro):
    """跑工具 coro，返回 (result, 它设的 biz_error type)；镜像 recorder 读取点。"""
    token = _biz_error_type.set(None)
    try:
        result = await coro
        return result, _biz_error_type.get()
    finally:
        _biz_error_type.reset(token)


# ============ POINT 工具：异常 catch → biz_error ============

@pytest.mark.asyncio
async def test_taker_flow_outage_points():
    from src.agent.tools_perception import get_taker_flow
    md = SimpleNamespace(get_taker_flow=AsyncMock(side_effect=RuntimeError("down")))
    result, biz = await _biz_after(get_taker_flow(MockDeps(market_data=md), "1h", 20))
    assert "unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_derivatives_all_sources_failed_points():
    from src.agent.tools_perception import get_derivatives_data
    md = SimpleNamespace(
        get_funding_rate=AsyncMock(side_effect=RuntimeError("d")),
        get_open_interest_history=AsyncMock(side_effect=RuntimeError("d")),
        get_long_short_ratio=AsyncMock(side_effect=RuntimeError("d")),
    )
    result, biz = await _biz_after(get_derivatives_data(MockDeps(market_data=md)))
    assert "all 3 data sources failed" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_htf_view_ticker_outage_points():
    from src.agent.tools_perception import get_higher_timeframe_view
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("t")))
    result, biz = await _biz_after(get_higher_timeframe_view(MockDeps(market_data=md), timeframes=["1d"]))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_macro_context_snapshot_exception_points():
    from src.agent.tools_perception import get_macro_context
    macro = SimpleNamespace(get_snapshot=AsyncMock(side_effect=RuntimeError("m")))
    result, biz = await _biz_after(get_macro_context(MockDeps(macro=macro)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_macro_context_all_sources_none_points():
    """snapshot 成功但全字段 None → any_available False → 1810 总失败点。"""
    from src.agent.tools_perception import get_macro_context
    snap = SimpleNamespace(
        btc_dominance=None, eth_dominance=None, total_mcap_usd=None, mcap_change_24h_pct=None,
        usd_index_broad_tw=None, vix=None, treasury_10y=None, spread_10y_2y=None, inflation_10y=None,
        spy=None, qqq=None,
    )
    macro = SimpleNamespace(get_snapshot=AsyncMock(return_value=snap))
    result, biz = await _biz_after(get_macro_context(MockDeps(macro=macro)))
    assert "all sources temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_order_book_outage_points():
    from src.agent.tools_perception import get_order_book
    md = SimpleNamespace(get_order_book=AsyncMock(side_effect=RuntimeError("ob")))
    result, biz = await _biz_after(get_order_book(MockDeps(market_data=md)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_recent_trades_outage_points():
    from src.agent.tools_perception import get_recent_trades
    md = SimpleNamespace(get_recent_trades=AsyncMock(side_effect=RuntimeError("rt")))
    result, biz = await _biz_after(get_recent_trades(MockDeps(market_data=md)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_mts_ticker_outage_points():
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("t")))
    result, biz = await _biz_after(get_multi_timeframe_snapshot(MockDeps(market_data=md)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_mts_all_timeframes_failed_points():
    """ticker 成功但所有 TF 的 OHLCV 全失败 → 2237 总失败点。"""
    from src.agent.tools_perception import get_multi_timeframe_snapshot
    md = SimpleNamespace(
        get_ticker=AsyncMock(return_value=SimpleNamespace(last=75000.0, bid=74999.0, ask=75001.0)),
        get_ohlcv_dataframe=AsyncMock(side_effect=RuntimeError("ohlcv")),
    )
    result, biz = await _biz_after(get_multi_timeframe_snapshot(MockDeps(market_data=md), tfs=["5m", "1h"]))
    assert "all timeframes failed" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_price_pivots_ticker_outage_points():
    from src.agent.tools_perception import get_price_pivots
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("t")))
    result, biz = await _biz_after(get_price_pivots(MockDeps(market_data=md)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_exchange_announcements_outage_points():
    from src.agent.tools_perception import get_exchange_announcements
    news = SimpleNamespace(get_announcements=AsyncMock(side_effect=RuntimeError("a")))
    result, biz = await _biz_after(get_exchange_announcements(MockDeps(news=news)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


# ============ outage-sentinel 路径（非异常，上游 None/全None）→ biz_error ============

@pytest.mark.asyncio
async def test_macro_calendar_none_sentinel_points():
    from src.agent.tools_perception import get_macro_calendar
    news = SimpleNamespace(get_macro_events=AsyncMock(side_effect=RuntimeError("m")))
    result, biz = await _biz_after(get_macro_calendar(MockDeps(news=news)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_etf_flows_both_none_sentinel_points():
    """BTC+ETH 两侧都抛 → btc=eth=None → 1888 总失败点。"""
    from src.agent.tools_perception import get_etf_flows
    etf = SimpleNamespace(get_etf_flows=AsyncMock(side_effect=RuntimeError("e")))
    result, biz = await _biz_after(get_etf_flows(MockDeps(crypto_etf=etf)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_stablecoin_exception_points():
    from src.agent.tools_perception import get_stablecoin_supply
    onchain = SimpleNamespace(get_stablecoin_snapshot=AsyncMock(side_effect=RuntimeError("s")))
    result, biz = await _biz_after(get_stablecoin_supply(MockDeps(onchain=onchain)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


@pytest.mark.asyncio
async def test_stablecoin_none_sentinel_points():
    """snapshot 返回 None（上游 outage sentinel）→ 1935 总失败点。"""
    from src.agent.tools_perception import get_stablecoin_supply
    onchain = SimpleNamespace(get_stablecoin_snapshot=AsyncMock(return_value=None))
    result, biz = await _biz_after(get_stablecoin_supply(MockDeps(onchain=onchain)))
    assert "temporarily unavailable" in result.lower()
    assert biz == "source_unavailable"


# ============ 反例：保持 ok（ContextVar 不被 set）============

@pytest.mark.asyncio
async def test_stablecoin_schema_drift_stays_ok():
    """result['coins'] 空 = schema-drift（源可达、数据不可映射）→ ok，不打点。"""
    from src.agent.tools_perception import get_stablecoin_supply
    onchain = SimpleNamespace(get_stablecoin_snapshot=AsyncMock(return_value={"coins": [], "total": None}))
    result, biz = await _biz_after(get_stablecoin_supply(MockDeps(onchain=onchain)))
    assert "no tracked symbols" in result.lower()
    assert biz is None


@pytest.mark.asyncio
async def test_htf_per_tf_partial_degrade_stays_ok():
    """ticker 成功、某 TF 失败 = 部分降级（仍返回可用数据）→ ok。"""
    from src.agent.tools_perception import get_higher_timeframe_view
    md = SimpleNamespace(
        get_ticker=AsyncMock(return_value=SimpleNamespace(last=75000.0, bid=74999.0, ask=75001.0)),
        get_ohlcv_dataframe=AsyncMock(side_effect=RuntimeError("one tf")),
    )
    result, biz = await _biz_after(get_higher_timeframe_view(MockDeps(market_data=md), timeframes=["1d"]))
    assert "[1d] error: temporarily unavailable" in result.lower()  # per-TF 降级行
    assert biz is None, "部分降级不打点（agent 仍拿到 Last 等可用数据）"


@pytest.mark.asyncio
async def test_market_news_not_configured_stays_ok():
    """deps.news is None = 配置缺失（非瞬态故障）→ ok，且 market_news 全程未被 instrument。"""
    from src.agent.tools_perception import get_market_news
    result, biz = await _biz_after(get_market_news(MockDeps(news=None)))
    assert "not configured" in result.lower()
    assert biz is None
```

> 工具签名以实际为准：`get_taker_flow(deps, period, limit)` / `get_higher_timeframe_view(deps, timeframes=...)` / `get_multi_timeframe_snapshot(deps, tfs=...)` / `get_macro_calendar(deps, lookahead_hours=...)` 等已对齐 `src/agent/tools_perception.py` 现状。`get_market_news` 参数若有 `lookback_hours` 默认值则零参可调。

- [ ] **Step 2: 跑测试确认失败（POINT 测试全红，反例已绿）**

Run: `pytest tests/test_tool_unavailability_biz_error.py -v`
Expected: 15 个 POINT/sentinel 测试 FAIL（`assert biz == "source_unavailable"` 失败，因尚未打点 → biz is None）；4 个反例测试 PASS（这些路径本就不该打点）。

- [ ] **Step 3: 提交（红测试入库，下一 task 转绿）**

```bash
git add tests/test_tool_unavailability_biz_error.py
git commit -m "$(cat <<'EOF'
test(perception): 12 工具 source_unavailable 打点 + 反例（先红，B3 转绿）

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task B3: 12 工具总失败返回点打点（转绿）

**Files:**
- Modify: `src/agent/tools_perception.py`（顶部 import + 15 处插 `note_biz_error`）

- [ ] **Step 1: 加 module-level import**

`src/agent/tools_perception.py`，在 `from src.integrations.news.models import extract_base_currency`（line 7）之后插入：

```python
from src.services.tool_call_recorder import note_biz_error
```

> 无循环 import 风险：`tool_call_recorder` 运行期只 import `pydantic_ai` + `src.storage.*`，不 import `tools_perception` / `trader`（后者仅 TYPE_CHECKING）。

- [ ] **Step 2: 打点 — `get_exchange_announcements`（announcements is None）**

定位：

```python
    if announcements is None:
        suffix = f" ({exc_class_name})" if exc_class_name else ""
        return (
```

改为：

```python
    if announcements is None:
        note_biz_error("source_unavailable")
        suffix = f" ({exc_class_name})" if exc_class_name else ""
        return (
```

- [ ] **Step 3: 打点 — `get_macro_calendar`（macro_events is None）**

定位：

```python
    if macro_events is None:
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h @ {fetch_ts} UTC) ===\n"
            "Error: Temporarily unavailable."
        )
```

改为：

```python
    if macro_events is None:
        note_biz_error("source_unavailable")
        sections.append(
            f"=== Upcoming Macro Events (next {lookahead_hours}h @ {fetch_ts} UTC) ===\n"
            "Error: Temporarily unavailable."
        )
```

- [ ] **Step 4: 打点 — `get_taker_flow`（main rubik except）**

定位：

```python
    except Exception as e:
        logger.exception("get_taker_flow main fetch failed for %s", symbol)
        return f"{header}\nTaker flow temporarily unavailable ({e.__class__.__name__})."
```

改为：

```python
    except Exception as e:
        logger.exception("get_taker_flow main fetch failed for %s", symbol)
        note_biz_error("source_unavailable")
        return f"{header}\nTaker flow temporarily unavailable ({e.__class__.__name__})."
```

- [ ] **Step 5: 打点 — `get_derivatives_data`（all 3 failed）**

定位：

```python
    if (
        isinstance(funding, Exception)
        and isinstance(oi_hist, Exception)
        and isinstance(lsr, Exception)
    ):
        return (
            f"=== Derivatives Data ({symbol}) ===\n"
            f"Error: Temporarily unavailable (all 3 data sources failed)."
        )
```

改为（在 return 前插一行）：

```python
    if (
        isinstance(funding, Exception)
        and isinstance(oi_hist, Exception)
        and isinstance(lsr, Exception)
    ):
        note_biz_error("source_unavailable")
        return (
            f"=== Derivatives Data ({symbol}) ===\n"
            f"Error: Temporarily unavailable (all 3 data sources failed)."
        )
```

- [ ] **Step 6: 打点 — `get_higher_timeframe_view`（ticker except）**

定位：

```python
    except Exception:
        logger.warning("HTF ticker fetch failed for %s", symbol, exc_info=True)
        return f"=== Higher Timeframe View ({symbol}) ===\nError: Temporarily unavailable."
```

改为：

```python
    except Exception:
        logger.warning("HTF ticker fetch failed for %s", symbol, exc_info=True)
        note_biz_error("source_unavailable")
        return f"=== Higher Timeframe View ({symbol}) ===\nError: Temporarily unavailable."
```

- [ ] **Step 7: 打点 — `get_macro_context`（snapshot except + all sources）**

定位（snapshot except）：

```python
    except Exception:
        logger.warning("Macro snapshot fetch failed", exc_info=True)
        return (
            f"=== Macro Context (@ {fetch_ts} UTC) ===\n"
            "Error: Temporarily unavailable."
        )
```

改为：

```python
    except Exception:
        logger.warning("Macro snapshot fetch failed", exc_info=True)
        note_biz_error("source_unavailable")
        return (
            f"=== Macro Context (@ {fetch_ts} UTC) ===\n"
            "Error: Temporarily unavailable."
        )
```

定位（all sources，函数末）：

```python
    if not any_available:
        return (
            f"=== Macro Context (@ {fetch_ts} UTC) ===\n"
            "Error: All sources temporarily unavailable."
        )
```

改为：

```python
    if not any_available:
        note_biz_error("source_unavailable")
        return (
            f"=== Macro Context (@ {fetch_ts} UTC) ===\n"
            "Error: All sources temporarily unavailable."
        )
```

- [ ] **Step 8: 打点 — `get_etf_flows`（btc is None and eth is None）**

定位：

```python
    if btc is None and eth is None:
        return (
            f"=== BTC Spot ETF Flows (US @ {fetch_ts} UTC) ===\n"
            "Error: Temporarily unavailable."
        )
```

改为：

```python
    if btc is None and eth is None:
        note_biz_error("source_unavailable")
        return (
            f"=== BTC Spot ETF Flows (US @ {fetch_ts} UTC) ===\n"
            "Error: Temporarily unavailable."
        )
```

- [ ] **Step 9: 打点 — `get_stablecoin_supply`（except + result is None；**不**碰 1940 schema-drift）**

定位（except）：

```python
    except Exception:
        logger.warning("Stablecoin snapshot fetch failed", exc_info=True)
        return (
            "=== Stablecoin Supply ===\n"
            "Error: Temporarily unavailable."
        )

    if result is None:
        return (
            "=== Stablecoin Supply ===\n"
            "Error: Temporarily unavailable."
        )
```

改为（两处各插一行；`if not result["coins"]:` schema-drift 块**保持不动**）：

```python
    except Exception:
        logger.warning("Stablecoin snapshot fetch failed", exc_info=True)
        note_biz_error("source_unavailable")
        return (
            "=== Stablecoin Supply ===\n"
            "Error: Temporarily unavailable."
        )

    if result is None:
        note_biz_error("source_unavailable")
        return (
            "=== Stablecoin Supply ===\n"
            "Error: Temporarily unavailable."
        )
```

- [ ] **Step 10: 打点 — `get_order_book`（except）**

定位：

```python
    except Exception as e:
        logger.exception("get_order_book failed for %s", symbol)
        return (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Error: Temporarily unavailable ({e.__class__.__name__})."
        )
```

改为：

```python
    except Exception as e:
        logger.exception("get_order_book failed for %s", symbol)
        note_biz_error("source_unavailable")
        return (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Error: Temporarily unavailable ({e.__class__.__name__})."
        )
```

- [ ] **Step 11: 打点 — `get_recent_trades`（except）**

定位：

```python
    except Exception as e:
        logger.exception("get_recent_trades failed for %s", symbol)
        return f"=== Recent Trades ({symbol} · @ {fetch_ts} UTC) ===\nRecent trades temporarily unavailable ({e.__class__.__name__})."
```

改为：

```python
    except Exception as e:
        logger.exception("get_recent_trades failed for %s", symbol)
        note_biz_error("source_unavailable")
        return f"=== Recent Trades ({symbol} · @ {fetch_ts} UTC) ===\nRecent trades temporarily unavailable ({e.__class__.__name__})."
```

- [ ] **Step 12: 打点 — `get_multi_timeframe_snapshot`（ticker except + all TF failed）**

定位（ticker except）：

```python
    except Exception:
        logger.exception("get_multi_timeframe_snapshot ticker fetch failed for %s", symbol)
        return f"=== Multi-TF Snapshot ({symbol}) ===\nError: Temporarily unavailable."
```

改为：

```python
    except Exception:
        logger.exception("get_multi_timeframe_snapshot ticker fetch failed for %s", symbol)
        note_biz_error("source_unavailable")
        return f"=== Multi-TF Snapshot ({symbol}) ===\nError: Temporarily unavailable."
```

定位（all TF failed）：

```python
    if all(isinstance(r[1], Exception) for r in results):
        return (
            f"=== Multi-TF Snapshot ({symbol}) ===\n"
            f"Error: Temporarily unavailable (all timeframes failed)."
        )
```

改为：

```python
    if all(isinstance(r[1], Exception) for r in results):
        note_biz_error("source_unavailable")
        return (
            f"=== Multi-TF Snapshot ({symbol}) ===\n"
            f"Error: Temporarily unavailable (all timeframes failed)."
        )
```

- [ ] **Step 13: 打点 — `get_price_pivots`（ticker except）**

定位：

```python
    except Exception:
        logger.exception("get_price_pivots ticker fetch failed for %s", symbol)
        return (
            f"=== Price Pivots ({symbol}, main TF: {main_tf} @ {fetch_ts} UTC) ===\n"
            f"Error: Temporarily unavailable."
        )
```

改为：

```python
    except Exception:
        logger.exception("get_price_pivots ticker fetch failed for %s", symbol)
        note_biz_error("source_unavailable")
        return (
            f"=== Price Pivots ({symbol}, main TF: {main_tf} @ {fetch_ts} UTC) ===\n"
            f"Error: Temporarily unavailable."
        )
```

- [ ] **Step 14: 跑 B2 测试确认全绿**

Run: `pytest tests/test_tool_unavailability_biz_error.py -v`
Expected: PASS（15 POINT/sentinel 全转绿 + 4 反例仍绿）

- [ ] **Step 15: 跑 drift-guard 确认无拼写漂移**

Run: `pytest tests/test_tool_call_recorder.py::test_biz_error_types_drift_guard -v`
Expected: PASS（perception 新增字面量全部 = `source_unavailable` ∈ 白名单）

- [ ] **Step 16: 跑既有 perception 工具测试确认无回归**

Run: `pytest tests/test_perception_tools_n3.py tests/test_taker_flow.py tests/test_price_pivots.py -q`
Expected: PASS（打点是纯加法，不改返回字符串）

- [ ] **Step 17: 提交**

```bash
git add src/agent/tools_perception.py
git commit -m "$(cat <<'EOF'
feat(perception): 12 工具总失败返回点打 source_unavailable biz_error

异常 catch / 上游 outage sentinel（None/全None）两类总失败点埋点；
部分降级 / insufficient / schema-drift 保持 ok。下游 metrics/WebUI 自动接住。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task B4: `get_market_data` / `get_open_orders` 崩溃语义显式化（§3）

**Files:**
- Modify: `src/agent/tools_perception.py:102,108`（`get_market_data` 裸 fetch 注释）
- Test: `tests/test_tool_unavailability_biz_error.py`

- [ ] **Step 1: 写崩溃穿透 drift-guard 测试（先红/或已绿——见下）**

在 `tests/test_tool_unavailability_biz_error.py` 追加 §3 测试：

```python
# ============ §3 崩溃穿透：get_market_data / get_open_orders 不降级、不打点 ============

@pytest.mark.asyncio
async def test_get_market_data_ticker_outage_propagates():
    """primary 市场数据不可用必须 abort cycle（由 crash-backoff 恢复）：异常穿透，不记 biz_error。"""
    from src.agent.tools_perception import get_market_data
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("ticker down")))
    token = _biz_error_type.set(None)
    try:
        with pytest.raises(RuntimeError, match="ticker down"):
            await get_market_data(MockDeps(market_data=md))
        assert _biz_error_type.get() is None, "get_market_data 不得降级 / 打 biz_error"
    finally:
        _biz_error_type.reset(token)


@pytest.mark.asyncio
async def test_get_open_orders_ticker_outage_propagates():
    """get_open_orders:566 ticker 裸调（real-net）：超时穿透崩溃，行为本 iter 不改。"""
    from src.agent.tools_perception import get_open_orders
    exchange = SimpleNamespace(fetch_open_orders=AsyncMock(return_value=[object()]))  # 非空 → 走到 ticker
    md = SimpleNamespace(get_ticker=AsyncMock(side_effect=RuntimeError("ticker down")))
    token = _biz_error_type.set(None)
    try:
        with pytest.raises(RuntimeError, match="ticker down"):
            await get_open_orders(MockDeps(exchange=exchange, market_data=md))
        assert _biz_error_type.get() is None
    finally:
        _biz_error_type.reset(token)
```

> 这两条**实现层无改动即应 PASS**（两工具现状就是裸 fetch、无 try/except、无 note_biz_error）。它们是 drift-guard：将来若有人「顺手给 get_market_data 加降级」，本测试立刻变红。`get_market_data` 在 line 100 后先调 `normalize_timeframe`（默认 timeframe `"15m"` 合法）再到 line 102 ticker → 抛 RuntimeError 穿透。

- [ ] **Step 2: 跑测试确认通过（drift-guard 锚定现状）**

Run: `pytest tests/test_tool_unavailability_biz_error.py -k "propagates" -v`
Expected: PASS（异常穿透 + ContextVar None）

- [ ] **Step 3: 加 `get_market_data` 裸 fetch 崩溃语义注释**

`src/agent/tools_perception.py`，定位：

```python
    candle_count = max(10, min(candle_count, 80))

    ticker = await deps.market_data.get_ticker(symbol)
```

改为：

```python
    candle_count = max(10, min(candle_count, 80))

    # 故意不 catch（spec §3）：primary 市场数据（ticker / OHLCV）不可用时，让 agent 在
    # "看不见市场"下硬决策更糟——异常穿透 → cycle abort → 由 §1 crash-backoff 重唤恢复。
    # 与 §2 自收敛降级（note_biz_error）的策略明确区分：那是软信号源缺一仍可决策。
    ticker = await deps.market_data.get_ticker(symbol)
```

> `get_ohlcv_dataframe`（line 108）同属此注释覆盖的裸 fetch，注释已统述「ticker / OHLCV」，无需在 108 重复。

- [ ] **Step 4: 跑测试确认仍通过（注释不改行为）**

Run: `pytest tests/test_tool_unavailability_biz_error.py -k "propagates" -v`
Expected: PASS

- [ ] **Step 5: 跑既有 get_market_data 测试确认无回归**

Run: `pytest tests/ -k "market_data" -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/agent/tools_perception.py tests/test_tool_unavailability_biz_error.py
git commit -m "$(cat <<'EOF'
docs(perception): get_market_data 崩溃语义显式化 + 穿透 drift-guard

故意不 catch primary 市场数据 fetch（注释 + 测试钉死）：异常穿透由 §1
crash-backoff 恢复，与 §2 自收敛降级区分。get_open_orders:566 同型穿透留证。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task B5: WebUI 单次可见性验收（真实数据，端到端确认）

**Files:**
- 无代码改动 — 验证现有渲染路径对 `source_unavailable` 同样生效。

> spec 测试 #7：渲染本身零改动（`ToolCallRow` 已暴露 `status`+`error_type`，`ReactTimeline.vue::statusType` 与 `CycleDetailPanel.vue` 扁平回退表都已把 `biz_error→"warning"`）。本 task 是端到端确认，非新单测。

- [ ] **Step 1: 确认渲染路径已接 biz_error（代码核对）**

Run: `grep -n "biz_error" frontend/src/components/ReactTimeline.vue frontend/src/components/CycleDetailPanel.vue`
Expected: 两文件 `statusType` 映射含 `biz_error: "warning"`（现状已具备，确认未被改动）。

- [ ] **Step 2: 找一条真实 `source_unavailable` 调用喂前端**

优先用已有 sim DB 中、本 iter landing 后产生的 `source_unavailable` 行；若当前 DB 无此类行（landing 前数据），用 SQL 临时构造一条 ToolCall 验证渲染（不入正式数据）：

Run（确认是否已有真实行）：
```bash
sqlite3 data/tradebot.db "SELECT tool_name, status, error_type, cycle_id FROM tool_calls WHERE error_type='source_unavailable' LIMIT 5;"
```
若有 → 记下其 `cycle_id` 用于下一步导航；若无 → 在新 sim run 中等其自然产生，或用 webui 测试夹具构造（按 `tests/test_webui_api.py` 的 ToolCall 插入模式）。

- [ ] **Step 3: 启动 webui server + 前端，导航到该 cycle 详情**

> 验证坑（见 project memory）：(a) 旧 webui server 占 8000 会让新进程 address-already-in-use 没绑上、应答旧码 → 先 `kill` 旧进程再启；(b) 浏览器 HTTP 缓存会把重启前 performance 喂 Pinia store → 用 `about:blank` + `?cb=<n>` 强制全新加载。

按项目现有 webui 启动方式起后端 + `cd frontend && npm run dev`，浏览器开到目标 cycle 详情面板。

- [ ] **Step 4: 用 Playwright 确认黄标渲染（两路径）**

对有 `react_steps` 的 cycle 走 ReactTimeline、对老 cycle 走扁平回退表，确认该工具卡：
- 状态标签为黄色 `warning`（非绿 `ok`）；
- 文案含 `biz_error · source_unavailable`（扁平回退表的 `${status} · ${error_type}` 列）；
- console 0 特性错误。

Run（示意，按现有 Playwright 夹具调整 selector）：
```
browser_navigate <cycle 详情 URL>
browser_snapshot   # 断言含 "source_unavailable" 黄标
browser_console_messages   # 断言 0 error
```

- [ ] **Step 5: 记录验收结果（不提交代码）**

在对话中报告：黄标渲染通过 / console 干净。无代码改动 → 无 commit。

---

## Task Z: 全量回归 + spec 交叉核对

**Files:**
- 无改动 — 收口验证。

- [ ] **Step 1: 跑两个新测试文件 + drift-guard 全绿**

Run: `pytest tests/test_cli_app_crash_backoff.py tests/test_tool_unavailability_biz_error.py tests/test_tool_call_recorder.py -v`
Expected: PASS

- [ ] **Step 2: 跑全量 pytest（merge 前 gate，per `feedback_parallel_subagent_cross_iter_tests`）**

Run: `pytest -q`
Expected: PASS（全绿；预期 tool_calls 出现 `source_unavailable` 是本 iter 设计效果，非回归——见 spec m5）

- [ ] **Step 3: spec 覆盖核对（自查清单）**

逐条对照 spec，确认有 task 实现：
- §1 退避机制 / fallback 来源 / 触发条件 / 曲线 / DB 计数 / 收敛降级 → Task A1-A4 ✓
- §2 白名单 / 12 工具打点 / drift-guard 扩扫 / 打点规则（异常 + sentinel；partial/insufficient/schema-drift 保持 ok）/ surface 单次可见 → Task B1-B3 + B5 ✓
- §3 get_market_data 显式化 + drift-guard / get_open_orders 留证 → Task B4 ✓
- 失败语义边界（None-guard / 计数 fail-isolation / 跨触发类型计数）→ Task A4 ✓
- 测试 #1-#7 → A2/A3/A4/B2/B4/B1/B5 一一对应 ✓

- [ ] **Step 4: 报告完成（不自动合并）**

在对话中汇报全绿 + spec 覆盖；等用户指示是否开 PR / merge（per `feedback_git_branch` + `feedback_review_before_commit`）。
