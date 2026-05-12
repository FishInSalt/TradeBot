# Iter W2-R2-Next-H Implementation Plan — set_next_wake_at + clamp→reject

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `set_next_wake_at(target_time: str, reasoning: str)` 绝对时间唤醒工具；顺手把 `set_next_wake` silent clamp 改成 explicit reject；Layer 1 cross-tool bullet L3 抽象化；cli/display.py 3 处契约 + sim_metrics wake-only 分类对齐。

**Architecture:** 新工具与既有 `set_next_wake(minutes)` 并列于 `tools_execution.py`，HH:MM UTC 字符串入参 → 内部 `re.fullmatch` 验证 + 跨日 future inference + `math.ceil(delta_sec/60)` 取整 + bound check → 复用既有 `deps.set_next_wake_fn` 与 `_record_action`。Success message prefix 与 set_next_wake 不同 (`"Next wake set for"` vs `"Next wake set to"`)，因此必须同步 `cli/display.py` 三处 dispatch dict。

**Tech Stack:** Python 3.13 / pydantic-ai 1.78 / pytest-asyncio / SQLAlchemy async / SQLite。无新 dependency。

**Spec reference:** `docs/superpowers/specs/2026-05-13-iter-w2r2-next-h-set-next-wake-at-design.md` @ `ed99b8c`

---

## File Structure

**Modified** (in-place, no new files):

| 文件 | 责任 |
|---|---|
| `src/agent/persona.py:92` | Layer 1 "Wake interval control" bullet L3 抽象 (不点名工具签名) |
| `src/agent/tools_execution.py:302-320` | `set_next_wake` clamp → reject；新增 `set_next_wake_at` (~50 LOC) |
| `src/agent/trader.py:36-37 / :617-631 / :719` | docstring 修订；新增 `@tool set_next_wake_at`；`REGISTERED_TOOL_NAMES` 加 `"set_next_wake_at"` |
| `src/cli/display.py:252 / :266 / :492` | `_EXECUTION_PARSERS` / `_EXECUTION_SUCCESS_PREFIXES` / `_EXECUTION_TOOL_NAMES` 三处加 `set_next_wake_at` |
| `scripts/_sim_metrics.py:586` | wake-only 分类 `actions == {"set_next_wake"}` → `actions <= {"set_next_wake", "set_next_wake_at"} and actions` |
| `tests/test_persona.py:312-409` | 5 处既有测试改造 (G8 / G9 / G10 / G11 / PR#34 I-1) |
| `tests/test_tools.py:344-365` | 2 处 clamp 测试改 reject |
| `tests/test_display_cycle.py:1481` + 新增 | `len(execution) == 11 → 12`；新增 `_summarize_set_next_wake_at` 测试 + `is_tool_error()` 覆盖 |
| `tests/test_sim_metrics.py:710` | 既有 `test_decision_type_distribution_hold_double_meaning` 增 set_next_wake_at 覆盖 |
| `tests/test_fact_only_wordlist.py:621` | 加 `_invoke_set_next_wake_at` helper + parametrize 列表 |

**No new test files** — extend existing files in place per codebase convention.

---

## Task Decomposition Rationale

7 tasks，每个 task 是一个 self-contained commit。关键耦合：
- **Task 4 必须合并 trader.py 注册 + display.py 三处 + test_display_cycle 计数更新** — 否则注册新工具后 `test_dg_2_dispatch_sets_partition_all_registered_tools` (test_display_cycle.py:1440) 会失败（execution 集合不含 set_next_wake_at）
- Task 1-3 可独立 commit；Task 5-7 也可独立

---

### Task 1: `persona.py` Layer 1 L3 抽象 + test_persona.py 5 处改造

**Files:**
- Modify: `src/agent/persona.py:92`
- Modify: `tests/test_persona.py:312-409` (5 处)

- [ ] **Step 1.1: Read 现状确认**

Run: `sed -n '85,100p' src/agent/persona.py`
Expected output includes line 92: `- **Wake interval control**: \`set_next_wake(minutes)\` requests the next scheduler ... Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting.`

- [ ] **Step 1.2: 改 persona.py:92 bullet (L3 抽象)**

替换 line 92 整行为：

```python
- **Wake interval control**: scheduled wake-up applies only when no external trigger fires; alerts, fills, and conditional triggers always interrupt sleep. Allowed range: next 1-{runtime.wake_max_minutes} min from now for this session.
```

- [ ] **Step 1.3: 改 test_persona.py G8 (line 320)**

将 line 320 断言改为新 wording：

```python
# Before:
assert "Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting" in layer1, \
    "Layer 1 Wake interval control bullet missing cross-tool interrupt clause"

# After:
assert "alerts, fills, and conditional triggers always interrupt sleep" in layer1, \
    "Layer 1 Wake interval control bullet missing cross-tool interrupt clause"
```

- [ ] **Step 1.4: 改 test_persona.py G11 (line 329 / 331 / 335)**

将 3 个 substring 断言改为新 wording：

```python
# Line 329:
assert "1-120 min from now for this session" in layer1_120, \
    "wake_max=120 not rendered in bullet"
# Line 331:
assert "1-60 min from now for this session" not in layer1_120, \
    "default 60 leaked when explicit 120 passed"
# Line 335:
assert "1-60 min from now for this session" in layer1_60, \
    "wake_max=60 not rendered in bullet"
```

- [ ] **Step 1.5: 改 test_persona.py G9 (line 348)**

```python
# Before:
assert "1-60 min for this session" in prompt_default, \
    "Default RuntimeConfig() should render 1-60 min"

# After:
assert "1-60 min from now for this session" in prompt_default, \
    "Default RuntimeConfig() should render 1-60 min"
```

- [ ] **Step 1.6: 删 test_persona.py G10 "one-shot" sanity assertion (line 378-380)**

仅删除该 sanity 断言三行，**保留**测试函数主体（N5 wordlist 验证）：

```python
# Delete these 3 lines (test_set_next_wake_no_decision_hints_in_description tail):
# Sanity: factual content preserved
assert "one-shot" in desc.lower(), \
    f"set_next_wake description should preserve 'one-shot' fact: {desc!r}"
```

- [ ] **Step 1.7: 删整个 test_set_next_wake_wrapper_layer1_reference_intact (line 383-409)**

L3 抽象后 SSOT 不变量不再适用（wrapper Args.minutes.description 不再引用 Layer 1 bullet 名）。整函数删除（包括 docstring）。

- [ ] **Step 1.8: Run test_persona.py**

Run: `uv run pytest tests/test_persona.py -v 2>&1 | tail -30`
Expected: 所有断言更新到位，无 failure（G8/G9/G10/G11 各项 pass；I-1 删除）。

- [ ] **Step 1.9: Commit**

```bash
WT=/Users/z/Z/TradeBot/.claude/worktrees/iter-w2r2-next-h-set-next-wake-at
git -C "$WT" add src/agent/persona.py tests/test_persona.py
git -C "$WT" commit -m "$(cat <<'EOF'
refactor(iter-w2r2-next-h): persona.py Layer 1 L3 抽象 + test_persona.py 5 处改造

Layer 1 "Wake interval control" bullet 不再点名 set_next_wake(minutes) 签名 —
工具描述交 docstring (pydantic-ai/griffe sniff) 自承，Layer 1 仅保留 cross-tool
behavior + session-aware bound (per Iter 4 DRY 反转 pattern, PR #25)。

Test 改造：
- G8 (test_layer1_contains_wake_interval_control_bullet) 断言改新 wording
- G11 (test_layer1_renders_dynamic_wake_max) 3 处 substring 改 "from now for"
- G9 (test_generate_system_prompt_default_runtime) substring 改 "from now for"
- G10 (test_set_next_wake_no_decision_hints_in_description) 删 "one-shot" sanity
  (wordlist 主体保留)
- PR #34 I-1 (test_set_next_wake_wrapper_layer1_reference_intact) 整体删除
  (L3 抽象后 wrapper↔Layer1 SSOT 不变量不再适用)

Spec §1.6 D6 / §5.1 / §6.5 row 1-5。
EOF
)"
```

---

### Task 2: `set_next_wake` clamp → reject

**Files:**
- Modify: `src/agent/tools_execution.py:302-320`
- Modify: `src/agent/trader.py:617-631` (docstring)
- Modify: `tests/test_tools.py:17-30` (MockDeps + cycle_id field), `:344-365` (2 clamp tests → reject), 新增 reject no-row + 边界 60/61

- [ ] **Step 2.0: 改 MockDeps 加 `cycle_id` 字段（前置 fixture 准备）**

替换 `tests/test_tools.py:17-30` 区域的 MockDeps dataclass（仅加一行 cycle_id）：

```python
@dataclass
class MockDeps:
    symbol: str
    timeframe: str
    market_data: AsyncMock
    exchange: AsyncMock
    technical: MagicMock
    memory: AsyncMock
    session_id: str = "test-session"
    cycle_id: str = "test-cycle"   # ← NEW: _record_action 路径需要
    db_engine: object = None
    approval_gate: object = None
    approval_enabled: bool = False
    wake_min_minutes: int = 1
    wake_max_minutes: int = 60
    set_next_wake_fn: object = None
    initial_balance: float = 10000.0
    metrics: object = None
    news: object = None
    macro: object = None
    crypto_etf: object = None
    onchain: object = None
```

理由：`_record_action` (tools_execution.py:36-47) 在 db_engine 非空路径访问 `deps.cycle_id`；MockDeps 当前无该字段，AttributeError 被 except 吞掉 → T1.9 (1 row prefix) **真实失败**、T1.10 (0 row) **虚假通过**。Task 2 Step 2.6 reject no-row 断言以及 Task 3 T1.9/T1.10 都依赖此 fixture，故前置到 Task 2 一起 commit。

- [ ] **Step 2.1: Write failing test — reject above wake_max**

替换 `tests/test_tools.py:344` 整个 `test_set_next_wake_clamps_to_max` 函数：

```python
async def test_set_next_wake_rejects_above_max(deps):
    """Minutes above wake_max → reject; set_next_wake_fn not called."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 90, reasoning="test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot set wake to 90 min" in result
    assert "exceeds wake_max=60 min" in result
    assert "for this session" in result
```

- [ ] **Step 2.2: Write failing test — reject below wake_min**

替换 `tests/test_tools.py:356` 整个 `test_set_next_wake_clamps_to_min` 函数：

```python
async def test_set_next_wake_rejects_below_min(deps):
    """Minutes below wake_min → reject; set_next_wake_fn not called."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 0, reasoning="test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot set wake to 0 min" in result
    assert "below wake_min=1 min" in result
```

- [ ] **Step 2.3: Run tests to verify they fail**

Run: `uv run pytest tests/test_tools.py::test_set_next_wake_rejects_above_max tests/test_tools.py::test_set_next_wake_rejects_below_min -v`
Expected: FAIL — current impl 返回 "clamped from X" 而非 "Cannot set wake to..."

- [ ] **Step 2.4: 改 tools_execution.py set_next_wake 实现 (line 302-320)**

替换整函数为：

```python
async def set_next_wake(
    deps: TradingDeps,
    minutes: int,
    reasoning: str,
) -> str:
    """See trader.py wrapper docstring."""
    if deps.set_next_wake_fn is None:
        return "Dynamic wake not available"

    if minutes < deps.wake_min_minutes:
        return (
            f"Cannot set wake to {minutes} min: "
            f"below wake_min={deps.wake_min_minutes} min."
        )
    if minutes > deps.wake_max_minutes:
        return (
            f"Cannot set wake to {minutes} min: "
            f"exceeds wake_max={deps.wake_max_minutes} min for this session."
        )

    deps.set_next_wake_fn(minutes)
    await _record_action(
        deps, action="set_next_wake",
        reasoning=f"interval={minutes}min | {reasoning}",
    )
    return f"Next wake set to {minutes} min. Reason: {reasoning}"
```

- [ ] **Step 2.5: 改 trader.py:617-631 wrapper docstring**

替换整个 `set_next_wake` @tool 函数 docstring：

```python
    @tool
    async def set_next_wake(
        ctx: RunContext[TradingDeps],
        minutes: int,
        reasoning: str,
    ) -> str:
        """Schedule the next scheduler wake-up after a relative minute interval.

        Args:
            minutes: minutes from now until the next wake-up. Must fall within
                [wake_min_minutes, wake_max_minutes]; rejected otherwise.
            reasoning: brief description of your decision logic.

        Returns a confirmation, or a reject message describing the violation.

        Examples:
            set_next_wake(15, "consolidation phase, check in 15 min")
            → "Next wake set to 15 min. Reason: ..."

            set_next_wake(90, "...")
            → "Cannot set wake to 90 min: exceeds wake_max=60 min for this session."

            set_next_wake(0, "...")
            → "Cannot set wake to 0 min: below wake_min=1 min."

        Alerts, fills, and conditional triggers always interrupt scheduled wake.
        """
        from src.agent.tools_execution import set_next_wake as _impl
        return await _impl(ctx.deps, minutes, reasoning=reasoning)
```

- [ ] **Step 2.6: 加 T2 reject no-row + 边界 60/61 测试**

依赖 Step 2.0 (MockDeps.cycle_id) + conftest db_engine fixture。在 `tests/test_tools.py` 新增（与 `test_set_next_wake_rejects_above_max` 相邻）：

```python
async def test_set_next_wake_reject_no_trade_action(deps, db_engine):
    """T2 reject path does not write trade_actions row."""
    from src.agent.tools_execution import set_next_wake
    from src.storage.models import TradeAction
    from sqlalchemy import select
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    deps.db_engine = db_engine

    await set_next_wake(deps, 90, reasoning="reject test")  # exceeds max

    async with db_engine.begin() as conn:
        rows = (await conn.execute(
            select(TradeAction).where(TradeAction.action == "set_next_wake")
        )).scalars().all()
    assert len(rows) == 0


async def test_set_next_wake_boundary_60_ok(deps):
    """T2.5: minutes=60 (wake_max boundary) → ok, fn called."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 60, reasoning="boundary test")
    deps.set_next_wake_fn.assert_called_once_with(60)
    assert "Next wake set to 60 min" in result


async def test_set_next_wake_boundary_61_rejects(deps):
    """T2.4: minutes=61 (wake_max+1) → reject."""
    from src.agent.tools_execution import set_next_wake
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake(deps, 61, reasoning="boundary test")
    deps.set_next_wake_fn.assert_not_called()
    assert "exceeds wake_max=60 min" in result
```

- [ ] **Step 2.7: Run T2 tests — should all pass**

Run: `uv run pytest tests/test_tools.py -k "set_next_wake" -v`
Expected: PASS — 既有 happy / not_available + 新 reject above/below + no-row + 边界 60/61。

- [ ] **Step 2.8: 兜底 grep — clamp 残留**

Run: `grep -rn "clamped" src/agent/ tests/ scripts/`
Expected: 0 命中 (R2-W2-5 引入的 "clamped from X" 全清除)

- [ ] **Step 2.9: Commit**

```bash
WT=/Users/z/Z/TradeBot/.claude/worktrees/iter-w2r2-next-h-set-next-wake-at
git -C "$WT" add src/agent/tools_execution.py src/agent/trader.py tests/test_tools.py
git -C "$WT" commit -m "$(cat <<'EOF'
refactor(iter-w2r2-next-h): set_next_wake clamp → explicit reject

落实 principle 6 (操作类 reject + retry) + feedback_observation_period_soft_constraint
§2 (执行类优先 explicit reject 而非 silent clamp)。Supersede R2-W2-5 D8 决议——
失败语义范式由 silent clamp + "clamped from X" 反馈 切到 explicit reject + 精确
bound 描述。

set_next_wake_fn 在 reject 路径不调用；_record_action 不写 trade_actions row。

Fixture 准备：tests/test_tools.py MockDeps 加 cycle_id 字段（defense-in-depth
for _record_action access；T2.6 reject no-row 与 Task 3 T1.9/T1.10 都依赖）。
db_engine fixture 复用 tests/conftest.py:90-103 已有。

Test 改造：
- test_set_next_wake_clamps_to_max → test_set_next_wake_rejects_above_max
- test_set_next_wake_clamps_to_min → test_set_next_wake_rejects_below_min
- 新增 test_set_next_wake_reject_no_trade_action (T2.6)
- 新增 test_set_next_wake_boundary_60_ok / _61_rejects (T2.4 / T2.5)

Spec §1.6 D4 / D9 / §3.3 / §6.2 / §10 supersede 声明。
EOF
)"
```

---

### Task 3: `set_next_wake_at` execution layer 新工具（TDD 多 sub-step）

**Files:**
- Modify: `src/agent/tools_execution.py` (新增函数 + import math)
- Modify: `tests/test_tools.py` (新增测试组 T1.1-T1.10)

- [ ] **Step 3.0a: db_engine fixture 已在 conftest.py 可用（无需新加）**

`tests/conftest.py:90-103` 已定义 `db_engine` fixture（tmp_path + `await init_db(db_url)` Path 3），对全 `tests/` 目录可见。T1.9 / T1.10 测试函数签名直接含 `db_engine` 参数即可注入。

`init_db(url: str) -> AsyncEngine` 签名接受 connection string，conftest 已正确使用 `f"sqlite+aiosqlite:///{db_path}"`；本议题无需额外 fixture。

**注意**：MockDeps.cycle_id 字段已在 Task 2 Step 2.0 提前补上（避免 Task 3 回头改 fixture）。

- [ ] **Step 3.1: 写 T1.1 happy path test + 共用 FakeDateTime helper**

在 `tests/test_tools.py` 文件顶部 imports 区域加（与 `tests/test_av_time_of_day_cache.py:11` pattern 一致）：

```python
from datetime import datetime, timezone


def _patch_now(monkeypatch, year=2026, month=5, day=12, hour=10, minute=23, second=0):
    """Helper to monkeypatch datetime.now in tools_execution module to a fixed UTC time.

    Pattern匹配 tests/test_av_time_of_day_cache.py:11 (FakeDateTime(datetime) 继承)。
    """
    from src.agent import tools_execution as mod
    fixed = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(mod, "datetime", FakeDateTime)
    return fixed
```

T1.1 测试：

```python
async def test_set_next_wake_at_happy_path(deps, monkeypatch):
    """T1.1: now=10:23:00, target='10:37' → ok + delta=14 + trade_actions written."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch)  # default 2026-05-12 10:23:00 UTC

    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:37", reasoning="align 1h close")
    deps.set_next_wake_fn.assert_called_once_with(14)
    assert "Next wake set for 2026-05-12 10:37 UTC" in result
    assert "in 14 min" in result
    assert "align 1h close" in result
```

- [ ] **Step 3.2: Run test to verify it fails**

Run: `uv run pytest tests/test_tools.py::test_set_next_wake_at_happy_path -v`
Expected: FAIL — `ImportError: cannot import name 'set_next_wake_at' from 'src.agent.tools_execution'`

- [ ] **Step 3.3: 实现 set_next_wake_at (minimal pass)**

**(a) 改 `src/agent/tools_execution.py` 文件顶部 imports**（line 1-12 区域）：

```python
# Before (lines 1-7):
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from src.services.tool_call_recorder import note_biz_error

# After:
from __future__ import annotations

import logging
import math   # ← NEW
import re
from datetime import datetime, timedelta, timezone   # ← NEW
from typing import TYPE_CHECKING

from src.services.tool_call_recorder import note_biz_error
```

**(b) 在 `set_next_wake` 函数后新增 set_next_wake_at：**

```python
async def set_next_wake_at(
    deps: TradingDeps,
    target_time: str,
    reasoning: str,
) -> str:
    """See trader.py wrapper docstring."""
    if deps.set_next_wake_fn is None:
        return "Dynamic wake not available"

    # 1. Format validation — strict HH:MM (00:00 - 23:59)
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", target_time)
    if not match:
        return (
            f"Invalid target_time format: {target_time!r}. "
            f"Expected 'HH:MM' UTC with 2-digit hour and minute "
            f"(e.g., '10:37' or '03:05')."
        )
    h, m = int(match[1]), int(match[2])

    # 2. Future inference — today HH:MM if still ahead, else tomorrow HH:MM
    now_utc = datetime.now(timezone.utc)
    candidate = now_utc.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now_utc:
        candidate += timedelta(days=1)

    # 3. Delta + bound validation — ceil to avoid waking before target moment
    delta_seconds = (candidate - now_utc).total_seconds()
    delta_minutes = math.ceil(delta_seconds / 60)
    candidate_label = candidate.strftime("%Y-%m-%d %H:%M")

    if delta_minutes < deps.wake_min_minutes:
        return (
            f"Cannot wake at {target_time} UTC: nearest future "
            f"{candidate_label} UTC (in {delta_minutes} min) "
            f"below wake_min={deps.wake_min_minutes} min."
        )
    if delta_minutes > deps.wake_max_minutes:
        return (
            f"Cannot wake at {target_time} UTC: nearest future "
            f"{candidate_label} UTC (in {delta_minutes} min) "
            f"exceeds wake_max={deps.wake_max_minutes} min for this session."
        )

    # 4. Success
    deps.set_next_wake_fn(delta_minutes)
    await _record_action(
        deps, action="set_next_wake_at",
        reasoning=(
            f"target={target_time} UTC resolves_to={candidate_label} UTC "
            f"interval={delta_minutes}min | {reasoning}"
        ),
    )
    return (
        f"Next wake set for {candidate_label} UTC (in {delta_minutes} min). "
        f"Reason: {reasoning}"
    )
```

imports 已在 Step 3.3 (a) 添加，function 内无再 import。

- [ ] **Step 3.4: Run T1.1 to verify pass**

Run: `uv run pytest tests/test_tools.py::test_set_next_wake_at_happy_path -v`
Expected: PASS

- [ ] **Step 3.5: 写 T1.2 cross-day + T1.4 exceeds wake_max + T1.5 ceil 边界 ok + T1.6 past resolves to tomorrow + T1.7 fn=None + T1.10 reject 不写 trade_actions**

所有测试复用 Step 3.1 定义的 `_patch_now` helper（避免 7 处 inline class 重复）。

```python
async def test_set_next_wake_at_cross_day(deps, monkeypatch):
    """T1.2: now=23:50, target='00:37' → tomorrow 00:37, delta=47."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, hour=23, minute=50)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "00:37", reasoning="cross-day test")
    deps.set_next_wake_fn.assert_called_once_with(47)
    assert "Next wake set for 2026-05-13 00:37 UTC" in result
    assert "in 47 min" in result


async def test_set_next_wake_at_exceeds_wake_max(deps, monkeypatch):
    """T1.4: target 97 min away → reject, fn not called."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch)  # 10:23:00
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "12:00", reasoning="test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot wake at 12:00 UTC" in result
    assert "nearest future 2026-05-12 12:00 UTC" in result
    assert "in 97 min" in result
    assert "exceeds wake_max=60 min for this session" in result


async def test_set_next_wake_at_ceil_boundary_ok(deps, monkeypatch):
    """T1.5: now=10:23:30, target='10:24' → ceil(30/60)=1, ok (not reject)."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, second=30)  # 10:23:30
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:24", reasoning="ceil edge")
    deps.set_next_wake_fn.assert_called_once_with(1)
    assert "in 1 min" in result


async def test_set_next_wake_at_past_resolves_tomorrow_exceeds_max(deps, monkeypatch):
    """T1.6: now=10:23:00, target='10:23' (same minute past) → tomorrow → 1440 min → reject."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:23", reasoning="past test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot wake at 10:23 UTC" in result
    assert "nearest future 2026-05-13 10:23 UTC" in result
    assert "in 1440 min" in result
    assert "exceeds wake_max=60 min" in result


async def test_set_next_wake_at_fn_none(deps):
    """T1.7: deps.set_next_wake_fn=None → 'Dynamic wake not available'."""
    from src.agent.tools_execution import set_next_wake_at
    deps.set_next_wake_fn = None
    result = await set_next_wake_at(deps, "10:37", reasoning="test")
    assert result == "Dynamic wake not available"


async def test_set_next_wake_at_reject_no_trade_action(deps, monkeypatch, db_engine):
    """T1.10: reject path does not write trade_actions row."""
    from src.agent.tools_execution import set_next_wake_at
    from src.storage.models import TradeAction
    from sqlalchemy import select
    _patch_now(monkeypatch)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    deps.db_engine = db_engine

    await set_next_wake_at(deps, "12:00", reasoning="test")  # reject (97 min)

    async with db_engine.begin() as conn:
        rows = (await conn.execute(
            select(TradeAction).where(TradeAction.action == "set_next_wake_at")
        )).scalars().all()
    assert len(rows) == 0
```

注意：T1.10 需要 `db_engine` fixture — 若 `tests/test_tools.py` 已有这个 fixture（参考既有 `_record_action` 测试），直接 reuse；否则参考 `tests/test_sim_metrics.py:db_engine` 引入。

- [ ] **Step 3.6: 写 T1.3a 格式无效 + T1.3b 边界格式 + T1.8 / T1.8b / T1.8c ceil drift guard + T1.9 reasoning prefix**

```python
@pytest.mark.parametrize("bad_input", ["foo", "25:00", "10:60", "10", "10:37:00", "", "3:05", "10:5"])
async def test_set_next_wake_at_format_invalid(deps, bad_input):
    """T1.3a: invalid format → reject with hint."""
    from src.agent.tools_execution import set_next_wake_at
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, bad_input, reasoning="test")
    deps.set_next_wake_fn.assert_not_called()
    assert "Invalid target_time format" in result
    assert "2-digit hour and minute" in result
    assert "'10:37'" in result


@pytest.mark.parametrize("good_input,now_h,now_m,expected_delta", [
    ("00:00", 23, 30, 30),  # tomorrow 00:00 from 23:30 → 30 min
    ("23:59", 23, 0, 59),   # today 23:59 from 23:00 → 59 min
])
async def test_set_next_wake_at_format_edge_ok(deps, monkeypatch, good_input, now_h, now_m, expected_delta):
    """T1.3b: format edge (00:00 / 23:59) accepted by regex."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, hour=now_h, minute=now_m)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, good_input, reasoning="edge test")
    deps.set_next_wake_fn.assert_called_once_with(expected_delta)


async def test_set_next_wake_at_ceil_drift_guard_59s(deps, monkeypatch):
    """T1.8: ceil drift guard — now=10:23:01, target='10:24' (delta_sec=59) → ceil=1."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, second=1)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:24", reasoning="drift")
    deps.set_next_wake_fn.assert_called_once_with(1)


async def test_set_next_wake_at_ceil_drift_guard_120s(deps, monkeypatch):
    """T1.8b: ceil drift guard — integer minute boundary, delta_sec=120 → ceil=2."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:25", reasoning="120s boundary")
    deps.set_next_wake_fn.assert_called_once_with(2)


async def test_set_next_wake_at_below_wake_min_custom(deps, monkeypatch):
    """T1.8c: wake_min=2 fixture — ceil=1 < wake_min=2 → reject."""
    from src.agent.tools_execution import set_next_wake_at
    _patch_now(monkeypatch, second=30)
    deps.wake_min_minutes = 2  # custom
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    result = await set_next_wake_at(deps, "10:24", reasoning="custom wake_min")
    deps.set_next_wake_fn.assert_not_called()
    assert "Cannot wake at 10:24 UTC" in result
    assert "in 1 min" in result
    assert "below wake_min=2 min" in result


async def test_set_next_wake_at_trade_actions_reasoning_prefix(deps, monkeypatch, db_engine):
    """T1.9: trade_actions row reasoning prefix format."""
    from src.agent.tools_execution import set_next_wake_at
    from src.storage.models import TradeAction
    from sqlalchemy import select
    _patch_now(monkeypatch)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = 60
    deps.set_next_wake_fn = MagicMock()
    deps.db_engine = db_engine

    await set_next_wake_at(deps, "10:37", reasoning="align 1h close at 11:00 UTC")

    async with db_engine.begin() as conn:
        row = (await conn.execute(
            select(TradeAction).where(TradeAction.action == "set_next_wake_at")
        )).scalars().one()
    expected_prefix = "target=10:37 UTC resolves_to=2026-05-12 10:37 UTC interval=14min | align 1h close at 11:00 UTC"
    assert row.reasoning == expected_prefix
```

- [ ] **Step 3.7: Run all new T1 tests**

Run: `uv run pytest tests/test_tools.py -k "set_next_wake_at" -v`
Expected: 全部 PASS（10+ tests）

- [ ] **Step 3.8: Commit**

```bash
WT=/Users/z/Z/TradeBot/.claude/worktrees/iter-w2r2-next-h-set-next-wake-at
git -C "$WT" add src/agent/tools_execution.py tests/test_tools.py
git -C "$WT" commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-h): set_next_wake_at execution layer + 完整 T1.1-T1.10 tests

新增 set_next_wake_at(target_time: str, reasoning: str) 工具：
- HH:MM UTC 简略入参（strict regex `[01]\d|2[0-3]:[0-5]\d`）
- 跨日 future inference (candidate <= now → +1 day)
- math.ceil(delta_sec / 60) 取整 (per spec D7: 避免早唤醒)
- bound check [wake_min_minutes, wake_max_minutes]，超界 explicit reject
- success 输出回填完整日期 "Next wake set for YYYY-MM-DD HH:MM UTC (in N min)"
- reject 4 类全 fact-only (principle 1)

Tests: T1.1 happy / T1.2 cross-day / T1.3a-b format / T1.4 exceeds /
T1.5 ceil ok / T1.6 past→tomorrow→exceed / T1.7 fn=None / T1.8/b/c ceil
drift guard / T1.9 reasoning prefix / T1.10 reject no row。

Time mock pattern: monkeypatch.setattr(mod, "datetime", FakeDateTime)
(与 tests/test_av_time_of_day_cache.py:16 一致；codebase 0 freezegun)。

Spec §1.6 D1-D12 / §2.1 / §3.1 / §3.2 / §4 / §6.1。
EOF
)"
```

---

### Task 4: trader.py @tool 注册 + cli/display.py 3 处契约 + count 断言

**Files:**
- Modify: `src/agent/trader.py` (新增 @tool set_next_wake_at + REGISTERED_TOOL_NAMES)
- Modify: `src/cli/display.py:252 / :266 / :492` (3 dict 加 set_next_wake_at)
- Modify: `tests/test_display_cycle.py:1481` (count 11→12)
- Modify: `tests/test_display_cycle.py:321` (新增 _summarize_set_next_wake_at 测试)

**Why merge into one commit:** trader.py 注册 set_next_wake_at 后 `test_dg_2_dispatch_sets_partition_all_registered_tools` 立即要求 `_EXECUTION_TOOL_NAMES` 含新工具，分两 commit 会让中间状态测试失败。

- [ ] **Step 4.1: 改 trader.py 加 @tool set_next_wake_at**

紧邻既有 `set_next_wake` @tool 之后（line 633 之前）插入：

```python
    @tool
    async def set_next_wake_at(
        ctx: RunContext[TradingDeps],
        target_time: str,
        reasoning: str,
    ) -> str:
        """Schedule the next scheduler wake-up at an absolute UTC time.

        Args:
            target_time: future wake time in 'HH:MM' UTC format (e.g., '10:37').
                Resolves to the nearest future time matching HH:MM (today if
                HH:MM is still ahead in UTC; otherwise tomorrow). Must fall
                within [now+wake_min_minutes, now+wake_max_minutes]; rejected
                otherwise.
            reasoning: brief description of your decision logic.

        Returns a confirmation containing the resolved date-time, or a reject
        message describing the violation.

        Examples:
            set_next_wake_at("10:37", "align with 1h candle close at 11:00 UTC")
            → "Next wake set for 2026-05-12 10:37 UTC (in 14 min). Reason: ..."

            set_next_wake_at("12:00", "...")
            → "Cannot wake at 12:00 UTC: nearest future 2026-05-12 12:00 UTC
               (in 97 min) exceeds wake_max=60 min for this session."

            set_next_wake_at("10:23", "...")  # now=10:23, resolves to tomorrow
            → "Cannot wake at 10:23 UTC: nearest future 2026-05-13 10:23 UTC
               (in 1440 min) exceeds wake_max=60 min for this session."

            set_next_wake_at("foo", "...")
            → "Invalid target_time format: 'foo'. Expected 'HH:MM' UTC
               with 2-digit hour and minute (e.g., '10:37' or '03:05')."

        Alerts, fills, and conditional triggers always interrupt scheduled wake.
        """
        from src.agent.tools_execution import set_next_wake_at as _impl
        return await _impl(ctx.deps, target_time, reasoning=reasoning)
```

- [ ] **Step 4.2: 改 trader.py REGISTERED_TOOL_NAMES + section header (line 709-720 区域)**

两处同步：
1. line 709 section header 注释 `# --- 执行 (11) ---` → `# --- 执行 (12) ---`
2. 在 `"set_next_wake"` 行后插入 `"set_next_wake_at"`，保持元组次序紧邻

```python
REGISTERED_TOOL_NAMES = [
    # ... perception tools ...
    # --- 执行 (12) ---   # ← header 数字 11 → 12
    "open_position",
    # ... other execution tools ...
    "set_next_wake",
    "set_next_wake_at",   # ← NEW entry
    "place_limit_order",
    # --- memory (1) ---
    "save_memory",
]
```

- [ ] **Step 4.3: 改 cli/display.py:252 _EXECUTION_PARSERS**

紧邻既有 `_summarize_set_next_wake` 函数后新增：

```python
def _summarize_set_next_wake_at(content: str) -> str:
    """Parse 'Next wake set for YYYY-MM-DD HH:MM UTC (in N min). Reason: ...'."""
    m = re.search(r"\(in (\d+)\s*min\)", content)
    if m:
        return f"{m.group(1)}min"
    return _fallback_summary(content)
```

在 `_EXECUTION_PARSERS` dict 中（line 260 区域）加入条目：

```python
_EXECUTION_PARSERS = {
    # ...
    "set_next_wake": _summarize_set_next_wake,
    "set_next_wake_at": _summarize_set_next_wake_at,   # ← NEW
}
```

- [ ] **Step 4.4: 改 cli/display.py:266 _EXECUTION_SUCCESS_PREFIXES**

在 dict 中加：

```python
_EXECUTION_SUCCESS_PREFIXES = {
    # ...
    "set_next_wake": "Next wake set to",  # existing at line 277
    "set_next_wake_at": "Next wake set for",   # ← NEW (紧邻 set_next_wake)
}
```

- [ ] **Step 4.5: 改 cli/display.py:492 _EXECUTION_TOOL_NAMES**

```python
_EXECUTION_TOOL_NAMES: frozenset[str] = frozenset({
    # ...
    "set_next_wake",
    "set_next_wake_at",   # ← NEW
})
```

- [ ] **Step 4.6: 改 test_display_cycle.py 全部 5 处 count 数字**

`test_dg_2_dispatch_sets_partition_all_registered_tools` 函数含 5 处数字（docstring 3 + 注释 1 + 断言 1），全部同步：

| Line | Before | After |
|---|---|---|
| 1442 | `三层集合 + save_memory branch 互斥 + 完整覆盖 32 registered tools.` | `... 完整覆盖 33 registered tools.` |
| 1444 | `_PERCEPTION_TOOL_NAMES (20) ∪ _EXECUTION_TOOL_NAMES (11) ∪ {save_memory}` | `_PERCEPTION_TOOL_NAMES (20) ∪ _EXECUTION_TOOL_NAMES (12) ∪ {save_memory}` |
| 1445 | `必须等于 REGISTERED_TOOL_NAMES (32)，且互不重叠。` | `必须等于 REGISTERED_TOOL_NAMES (33)，且互不重叠。` |
| 1469 | `# 完整覆盖 32 registered` | `# 完整覆盖 33 registered` |
| 1481 | `assert len(execution) == 11` | `assert len(execution) == 12` |

注意 line 1479 `assert len(perception) == 20` 保持不变（perception 集合本议题不动）。

- [ ] **Step 4.7: 新增 test_summarize_set_next_wake_at + is_tool_error 测试**

紧邻 `test_summarize_set_next_wake` (line 321) 加：

```python
def test_summarize_set_next_wake_at():
    from src.cli.display import summarize_tool
    content = "Next wake set for 2026-05-12 10:37 UTC (in 14 min). Reason: align 1h close"
    result = summarize_tool("set_next_wake_at", content)
    assert "14" in result
    assert "min" in result
    assert "Reason" not in result


def test_is_tool_error_set_next_wake_at_success():
    """set_next_wake_at success message must not be flagged as error."""
    from src.cli.display import is_tool_error
    content = "Next wake set for 2026-05-12 10:37 UTC (in 14 min). Reason: test"
    assert is_tool_error("set_next_wake_at", content) is False


@pytest.mark.parametrize("reject_msg", [
    "Invalid target_time format: 'foo'. Expected 'HH:MM' UTC with 2-digit hour and minute (e.g., '10:37' or '03:05').",
    "Cannot wake at 12:00 UTC: nearest future 2026-05-12 12:00 UTC (in 97 min) exceeds wake_max=60 min for this session.",
    "Cannot wake at 10:24 UTC: nearest future 2026-05-12 10:24 UTC (in 1 min) below wake_min=2 min.",
])
def test_is_tool_error_set_next_wake_at_reject(reject_msg):
    """All 3 reject classes must be flagged as error."""
    from src.cli.display import is_tool_error
    assert is_tool_error("set_next_wake_at", reject_msg) is True
```

(若 `pytest` import 未在文件顶部，加 `import pytest`。)

- [ ] **Step 4.8: Run integration tests**

Run: `uv run pytest tests/test_display_cycle.py tests/test_trader_agent.py -v 2>&1 | tail -40`
Expected: PASS（含新增 summarize / is_tool_error / count 断言更新 + 既有 `test_registered_tool_names_matches_agent_tools` 自动覆盖 set_next_wake_at）

- [ ] **Step 4.9: Commit**

```bash
WT=/Users/z/Z/TradeBot/.claude/worktrees/iter-w2r2-next-h-set-next-wake-at
git -C "$WT" add src/agent/trader.py src/cli/display.py tests/test_display_cycle.py
git -C "$WT" commit -m "$(cat <<'EOF'
feat(iter-w2r2-next-h): @tool register set_next_wake_at + cli/display.py 3 处契约

- trader.py @tool wrapper docstring (per spec §2.1, 4 完整 call→output 示例)
- REGISTERED_TOOL_NAMES 加 "set_next_wake_at" (32 → 33)
- cli/display.py 3 dispatch dict 同步：
  - _EXECUTION_PARSERS 加 _summarize_set_next_wake_at (匹配 "(in N min)")
  - _EXECUTION_SUCCESS_PREFIXES 加 "Next wake set for"
  - _EXECUTION_TOOL_NAMES frozenset 加 set_next_wake_at (11 → 12)
- test_display_cycle.py:1481 count 断言 11 → 12 同步
- 新增 test_summarize_set_next_wake_at + is_tool_error 4 测试覆盖 success +
  3 reject 类

避免 trader 注册与 display.py dispatch 一致性测试中间失败，3 文件合并 commit。

Spec §2.3 / §6.6 / §9。
EOF
)"
```

---

### Task 5: `scripts/_sim_metrics.py` wake-only 分类 + 测试

**Files:**
- Modify: `scripts/_sim_metrics.py:586`
- Modify: `tests/test_sim_metrics.py:710-725` (extend existing test)

- [ ] **Step 5.1: 写 failing test — set_next_wake_at 也落 wake-only**

替换 `tests/test_sim_metrics.py:710` 整个 `test_decision_type_distribution_hold_double_meaning` 函数尾部，加新断言：

```python
async def test_decision_type_distribution_hold_double_meaning(db_engine):
    """Spec §3.5 caveat 1: hold (pure-observation) vs hold (wake-only).
    R2-Next-H: wake-only 分类同样包含 set_next_wake_at + 混合 set_next_wake/at。"""
    sid = await make_session(db_engine)
    await make_cycle(db_engine, sid, "c1")  # no trade_action → pure-observation
    await make_cycle(db_engine, sid, "c2")
    await make_cycle(db_engine, sid, "c3")
    await make_cycle(db_engine, sid, "c4")
    from sqlalchemy import insert
    from src.storage.models import TradeAction
    async with db_engine.begin() as conn:
        # c2: only set_next_wake → wake-only (legacy)
        await conn.execute(insert(TradeAction).values(
            session_id=sid, cycle_id="c2", action="set_next_wake",
            symbol="BTC/USDT:USDT",
        ))
        # c3: only set_next_wake_at → wake-only (NEW)
        await conn.execute(insert(TradeAction).values(
            session_id=sid, cycle_id="c3", action="set_next_wake_at",
            symbol="BTC/USDT:USDT",
        ))
        # c4: both set_next_wake + set_next_wake_at → wake-only (NEW)
        await conn.execute(insert(TradeAction).values(
            session_id=sid, cycle_id="c4", action="set_next_wake",
            symbol="BTC/USDT:USDT",
        ))
        await conn.execute(insert(TradeAction).values(
            session_id=sid, cycle_id="c4", action="set_next_wake_at",
            symbol="BTC/USDT:USDT",
        ))
    dist = await decision_type_distribution(db_engine, sid)
    assert dist.get("hold (pure-observation)") == 1
    assert dist.get("hold (wake-only)") == 3   # c2 + c3 + c4
```

- [ ] **Step 5.2: Run test to verify it fails**

Run: `uv run pytest tests/test_sim_metrics.py::test_decision_type_distribution_hold_double_meaning -v`
Expected: FAIL — `assert 1 == 3`（c3 + c4 fallthrough 到 else 分支，未计入 wake-only）

- [ ] **Step 5.3: 改 scripts/_sim_metrics.py:586**

```python
# Before:
if actions == {"set_next_wake"}:
    dist["hold (wake-only)"] = dist.get("hold (wake-only)", 0) + 1
    continue

# After (与 spec §6.7 字面对齐):
if actions <= {"set_next_wake", "set_next_wake_at"} and actions:
    dist["hold (wake-only)"] = dist.get("hold (wake-only)", 0) + 1
    continue
```

- [ ] **Step 5.4: Run test to verify pass**

Run: `uv run pytest tests/test_sim_metrics.py::test_decision_type_distribution_hold_double_meaning -v`
Expected: PASS

- [ ] **Step 5.5: 跑 sim_metrics 相关全测**

Run: `uv run pytest tests/test_sim_metrics.py tests/test_drift_phase2_metrics.py tests/test_diff_sim.py -v 2>&1 | tail -20`
Expected: 全 PASS（无 regression）

- [ ] **Step 5.6: Commit**

```bash
WT=/Users/z/Z/TradeBot/.claude/worktrees/iter-w2r2-next-h-set-next-wake-at
git -C "$WT" add scripts/_sim_metrics.py tests/test_sim_metrics.py
git -C "$WT" commit -m "$(cat <<'EOF'
fix(iter-w2r2-next-h): _sim_metrics wake-only 分类包含 set_next_wake_at

scripts/_sim_metrics.py:586 精确集合匹配 `actions == {"set_next_wake"}`
改 subset 包含 `actions <= {"set_next_wake", "set_next_wake_at"} and actions`。
W3 cross-sim analytics 中 set_next_wake_at-only cycle / 混合 wake-only cycle
正确归类 hold (wake-only)，不漂到 else fallthrough 分支。

Test: test_decision_type_distribution_hold_double_meaning 扩 4 cycle case
覆盖 set_next_wake_at 独占 + 混合两 wake 工具。

Spec §6.7。
EOF
)"
```

---

### Task 6: `tests/test_fact_only_wordlist.py` helper + parametrize

**Files:**
- Modify: `tests/test_fact_only_wordlist.py` (helper 新增 + parametrize 列表更新)

- [ ] **Step 6.1: Read 现状定位 helper pattern**

Run: `grep -nE "_invoke_set_next_wake\b" tests/test_fact_only_wordlist.py`
Expected: 应有定义点 + parametrize 引用点。Read 该 helper 实现作模板（早返回 set_next_wake_fn=None 路径）。

- [ ] **Step 6.2: 加 `_invoke_set_next_wake_at` helper**

紧邻 `_invoke_set_next_wake` helper 定义后新增（与既有 `_invoke_set_next_wake` 风格一致，不显式 set fn=None，依赖 MockDeps 默认）：

```python
async def _invoke_set_next_wake_at(deps, mocker):
    """Early return: set_next_wake_fn=None (MockDeps default)."""
    from src.agent.tools_execution import set_next_wake_at
    return await set_next_wake_at(deps, "10:37", reasoning="test")
```

- [ ] **Step 6.3: 加 parametrize 项 (line 621-632 区域)**

```python
@pytest.mark.parametrize("invoker", [
    "_invoke_open_position",
    "_invoke_close_position",
    "_invoke_set_stop_loss",
    "_invoke_set_take_profit",
    "_invoke_adjust_leverage",
    "_invoke_set_price_alert",
    "_invoke_cancel_order",
    "_invoke_add_price_level_alert",
    "_invoke_set_next_wake",
    "_invoke_set_next_wake_at",   # ← NEW
    "_invoke_place_limit_order",
])
async def test_execution_tool_fact_only(invoker, mocker):
    # ...
```

- [ ] **Step 6.4: Run fact_only_wordlist test**

Run: `uv run pytest tests/test_fact_only_wordlist.py -v 2>&1 | tail -20`
Expected: 全 PASS（新增 set_next_wake_at parametrize case 通过；输出无 banned word）

- [ ] **Step 6.5: Commit**

```bash
WT=/Users/z/Z/TradeBot/.claude/worktrees/iter-w2r2-next-h-set-next-wake-at
git -C "$WT" add tests/test_fact_only_wordlist.py
git -C "$WT" commit -m "$(cat <<'EOF'
test(iter-w2r2-next-h): fact_only_wordlist 加 set_next_wake_at 覆盖

新增 _invoke_set_next_wake_at helper (早返回 fn=None 路径) + parametrize 列表
加项，与既有 10 个 execution tools 同覆盖 N5 fact-only wordlist 验证
(parametrize 列表 pre-existing 10 项；cancel_price_level_alert 缺 helper 是
pre-existing off-by-one，本议题不顺手补——scope discipline)。

Spec §6.5 row 8。
EOF
)"
```

---

### Task 7: 全 grep 兜底 + 全测最终验证

**Files:**
- None modified (verification only)

- [ ] **Step 7.1: 兜底 grep — 关键 wording drift**

Run:
```bash
echo "===clamped 残留==="
grep -rn "clamped" src/agent/ tests/ scripts/ 2>/dev/null && echo "FOUND" || echo "clean"

echo "===one-shot 残留==="
grep -rn "one-shot" src/agent/ tests/ 2>/dev/null && echo "FOUND" || echo "clean"

echo "===regardless of this setting 残留==="
grep -rn "regardless of this setting" src/ tests/ 2>/dev/null && echo "FOUND" || echo "clean"

echo "===\"min for this session\" (without 'from now') 残留==="
grep -rn "min for this session" src/ tests/ 2>/dev/null | grep -v "from now for this session" && echo "FOUND DRIFT" || echo "clean"
```
Expected: 所有 grep "clean"。若有命中，按 spec §6.5 改造。

- [ ] **Step 7.2: 全测验证**

Run: `uv run pytest tests/ -x 2>&1 | tail -10`
Expected: 全 pass（基线 1520 passed + 5 skipped；新增 Task 2/3/4/5/6 共 ~25-30 tests 含 parametrize 展开后，总数 ≈ 1545+ passed + 5 skipped）

- [ ] **Step 7.3: 校验 spec §6 测试矩阵覆盖**

手动 verify 每个 spec §6.1-§6.7 ID 都有 impl plan 对应 step：

| Spec ID | Impl step |
|---|---|
| T1.1 happy | Step 3.1 |
| T1.2 cross-day | Step 3.5 |
| T1.3a/b format | Step 3.6 |
| T1.4 exceeds | Step 3.5 |
| T1.5 ceil ok | Step 3.5 |
| T1.6 past→tomorrow | Step 3.5 |
| T1.7 fn=None | Step 3.5 |
| T1.8/b/c ceil drift guard | Step 3.6 |
| T1.9 reasoning prefix | Step 3.6 |
| T1.10 reject no row | Step 3.5 |
| T2.1-T2.5 clamp→reject | Step 2.1/2.2 + 兜底 |
| T3.1-T3.3 Layer 1 | Step 1.3-1.5 |
| T4.1 REGISTERED_TOOL_NAMES | Step 4.8 既有 test 自动 |
| T4.2 @tool 注册 | Step 4.8 |
| T4.3-T4.5 is_tool_error / parser | Step 4.7 |
| T4.6 sim_metrics wake-only | Step 5.1 |

- [ ] **Step 7.4: 若 Step 7.1 / 7.2 / 7.3 全通过 → 无 final commit；若有 fix → commit**

```bash
WT=/Users/z/Z/TradeBot/.claude/worktrees/iter-w2r2-next-h-set-next-wake-at
git -C "$WT" log --oneline main..HEAD
```
Expected: 6-7 commits（task 1-6 各 1 commit + Task 7 verification commit if any）

---

## Self-Review Notes

**Spec coverage check** — 每个 spec section 至少有 1 task 覆盖：

| Spec section | Impl task |
|---|---|
| §1 背景与动机 | (rationale only) |
| §2.1 set_next_wake_at docstring | Step 4.1 |
| §2.2 set_next_wake docstring 修订 | Step 2.5 |
| §2.3 trader.py @tool | Step 4.1-4.2 |
| §3 解析算法 + 边界 | Step 3.3 + 3.5 + 3.6 |
| §4 输出消息 + trade_actions 落库 | Step 3.3 + 3.6 (T1.9) |
| §5 persona.py Layer 1 | Step 1.2 |
| §6.1-6.4 测试矩阵 | Task 2-4 |
| §6.5 既有测试清理 | Task 1 (test_persona) + Task 2 (test_tools clamp) + Task 6 (fact_only_wordlist) |
| §6.6 cli/display.py 3 处 | Step 4.3-4.5 + 4.7 |
| §6.7 sim_metrics 改造 | Task 5 |
| §7 风险表 | (mitigations 散见各 task) |
| §8 token 估算 | (informational) |
| §9 scope checklist | (cross-cut, 全覆盖) |
| §10 R2-W2-5 D8 supersede | Task 2 commit message |
| §11 邻 iter 边界 | (信息性) |
| §12 W3 follow-up | OOS |

**Type consistency check**:
- 工具名: `set_next_wake_at` 全 plan 一致 (无 `set_next_wake_at_event` / `set_next_wake_target` 等 drift)
- 入参名: `target_time: str` 全一致
- 函数 attr 名: `set_next_wake_fn` 既有，不变
- 输出 prefix: `"Next wake set for"` (set_next_wake_at) vs `"Next wake set to"` (set_next_wake) 严格区分

**Placeholder scan**: 所有 task step 含完整 code / 完整 command / 完整 expected output。无 "TODO" / "TBD" / "similar to" 类引用。
