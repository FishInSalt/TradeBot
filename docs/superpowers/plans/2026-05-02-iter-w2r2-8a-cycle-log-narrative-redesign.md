# R2-8a Cycle Log Narrative Architecture Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 cycle log 从"工具流水账 + agent 最终输出"重设计为"还原 think → act → think → act → decision 完整 cognition flow"，对齐 R2-7 五维度叙事 schema；retry-exhausted 路径补写 forensic AgentCycle 行避免 W2 SQL 黑洞。

**Architecture:** 渲染层为主 + 持久化层最小补写。`src/cli/display.py` `format_cycle_output(ctx: CycleRenderContext)` 签名重构 + 5 个私有 `_render_*` helper 时序遍历 `result.new_messages()`；新建 `src/cli/session_state.py` 的 `SessionStats` 与 `TokenBudget` 解耦；`run_agent_cycle` retry-exhausted 分支补写 `execution_status="retry_exhausted"` AgentCycle 行；不动 schema / prompt / agent 主循环。

**Tech Stack:** pydantic-ai 1.78 (`ModelResponse` / `ThinkingPart` / `ToolCallPart` / `ToolReturnPart` / `TextPart`) + Rich (markup escape via `rich.markup.escape`) + dataclass (`@dataclass(frozen=True)`) + pytest async + SQLAlchemy 2.x async（仅 retry-exhausted forensic 写入用）

---

## Pre-impl 验证（已完成 2026-05-02）

`.working/verify_message_structure.py` 实跑（DeepSeek v4-pro）已 verify 4 个结构性假设——详见 spec §9。新会话起手**不需要重跑**。R2-9 smoke 时顺带 capture 真实 trader cycle thinking 长度分布喂入 R2-8c brainstorm + N12c candidate。

## Spec drift 警示（新会话起手对照 spec 时必读）

> spec §5.3 line 721 列 `_extract_reasoning_per_response` helper 在 `src/cli/app.py`。
> **本 plan 改放 `src/cli/display.py`**（消费者所在层 — `format_cycle_output` 在 display.py 内调，避免 display→app 循环 import）。
> 决议理由：spec §4.2.3 / AC22 仅约束 helper 行为契约（每 Response 首 ThinkingPart），未严格 binding 模块；display.py placement 让 helper 真正服务渲染层不死代码。
> T-DG-1 drift guard 跨模块比对（`from src.cli.app import _extract_thinking_text` + `from src.cli.display import _extract_reasoning_per_response`）— 两 helper 在 smoke baseline 行为等价。
>
> spec §5.1 line 642 `cycle_started_at` 写 "函数入口时刻"，本 plan 实际放 `if budget.exhausted: return None` 之后（避免 exhausted 路径浪费 datetime.now() — 详见 Step 5.6(a)）。
>
> spec §4.4.2 写 "Decision 数据源 = `ctx.final_text` (= `result.output`)"，本 plan format_cycle_output 实施确实改用 `ctx.final_text`（**不是从 messages 重提取 TextPart**）— 单源真相 + ctx 字段不死代码 + drift guard T-INT-11 兜底 messages 无 TextPart 时仍渲染。
>
> spec AC17 line 1079 写 "T-INT-1b 与 mockup §3.2 byte-equal"，但 spec §3.2 line 119 mockup 自标 "illustrative，非 byte-equal verbatim copy" — spec 内部矛盾。本 plan T-INT-1b (`test_int_1b_structural_fragments_vs_mockup`) 选 illustrative 路线（structural fragments only）。spec AC17 文字 follow-up 同步。
>
> spec §6.2 T-ES-4 写 `_errors` 非空 → "warning log _errors 列表"。但 `cycle_capture.py:134-164` per-fetch failure 时**已经** `logger.warning(...)`（capture-time 已记），renderer 再 log 重复噪音。本 plan `_format_state_line` 不再 re-log；spec §6.2 T-ES-4 边界表 over-specify，按 capture-time 已 log 视作满足契约。

---

## File Structure

### 新建（3 个文件）

| 路径 | 责任 |
|---|---|
| `src/cli/session_state.py` | `SessionStats` class — session-级 cycle tracker，与 daily TokenBudget 解耦（spec §4.5.3）|
| `tests/test_session_state.py` | SessionStats 单元测试（5-7 cases）|
| `tests/fixtures/cycle_fixtures.py` | `build_cycle_messages` in-memory builder — 构造 `list[ModelRequest \| ModelResponse]` 给 display 集成测试用（避免持久化二进制 fixture 文件带来的反序列化执行风险；纯参数化 builder 比静态文件更灵活）|

### 修改（src，2 个文件）

| 路径 | 改动 |
|---|---|
| `src/cli/display.py` | `format_cycle_output(ctx: CycleRenderContext)` 重构 + 新增 `CycleRenderContext` dataclass + 新增 `_render_header` / `_render_reasoning` / `_render_action` / `_render_decision` / `_render_footer` private helper + 新增 `_extract_reasoning_per_response` helper（spec §5.3 placement 微调，从 app.py 移到 display.py 消费者所在层）；新增 module-level `logger = logging.getLogger(__name__)`；`summarize_tool` / `_fallback_summary` / `is_tool_error` / `resolve_tool_display` **不动**（R2-8c 议题）|
| `src/cli/app.py` | run_agent_cycle 装填 `CycleRenderContext`（capture `cycle_started_at` / `cycle_ended_at`，3 路径都调 `format_cycle_output` + `stats.record_cycle`）；retry-exhausted 路径写 forensic AgentCycle (`execution_status="retry_exhausted"`)；`build_services` return signature 4-tuple → 5-tuple 注入 `SessionStats`；`_DummySessionStats(SessionStats)` 子类 module-level singleton 给 stats=None 默认使用；`_extract_thinking_text` 行为不动；新 `_extract_reasoning_per_response` helper 放 `src/cli/display.py`（消费者所在层）— spec §5.3 placement 微调避免 display→app 循环 import |

### 修改（docs + comment，2 个文件）

| 路径 | 改动 |
|---|---|
| `docs/metrics/agent-cycles-schema.md:17` | `execution_status` 列描述改 `ok / usage_limit_exceeded / retry_exhausted` |
| `src/storage/models.py:94` | 注释 `# ok / usage_limit_exceeded` 改 `# ok / usage_limit_exceeded / retry_exhausted` |

### 修改（tests，5 个文件）

| 路径 | 改动 |
|---|---|
| `tests/test_display_cycle.py` | 现有 `format_cycle_output` 4 测试（line 381-453）签名迁移到 `CycleRenderContext`；新增 ~25 测试（11 helper 单测 + 11 集成 + 3 drift guard + 边界细化）|
| `tests/test_cycle_log.py` | 3 处 `run_agent_cycle(...)` 调用——kwarg 风格 + `stats=None` 默认值，**无需改签名**；R2-8a `run_agent_cycle` 不再触发新 mock 需求（旧 capture-aware fixture line 23-62 已覆盖），仅校验现有 3 测试 PASS 即可 |
| `tests/test_wizard.py:481/526/552` | `build_services` 4-tuple 解构改 5-tuple（共 3 处）|
| `tests/test_n3_wiring.py:92/111/128/141/158` | `build_services` 4-tuple 解构改 5-tuple（共 5 处）|
| `tests/test_okx_algo_normalization.py` | **不属 5-tuple 同步**（不解构 return）—— 但需校验 patch 链覆盖 `src.cli.app.SessionStats` 构造（避免 build_services 内 SessionStats() 实例化 raise） |
| `tests/test_usage_limits.py` | 8 处 `run_agent_cycle(...)` 调用全 kwargs 风格——`stats=None` 默认值兼容，**无需改签名**；新增 retry-exhausted 路径 forensic 写入测试（T-EX-1/2/3）|

---

## Task 1: SessionStats class（独立基础）

**Files:**
- Create: `src/cli/session_state.py`
- Test: `tests/test_session_state.py`

**Spec ref:** §4.5.3 / §5.3 / §5.4 / §10.1 AC13/AC14 / §10.3 AC26

- [ ] **Step 1.1: Write failing tests for SessionStats**

Create `tests/test_session_state.py`:

```python
"""SessionStats — session-level cycle tracker, decoupled from daily TokenBudget."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_session_stats_initial_state():
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    assert stats.cycle_count == 0
    assert stats.total_tokens == 0
    assert stats.avg_tokens_per_cycle == 0
    assert stats.last_cycle_ended_at is None


def test_session_stats_record_single_cycle():
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    end_ts = datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc)
    stats.record_cycle(cycle_tokens=46_500, cycle_ended_at=end_ts)
    assert stats.cycle_count == 1
    assert stats.total_tokens == 46_500
    assert stats.avg_tokens_per_cycle == 46_500
    assert stats.last_cycle_ended_at == end_ts


def test_session_stats_record_multiple_cycles_avg():
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    base = datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc)
    for i, tokens in enumerate([40_000, 50_000, 30_000]):
        stats.record_cycle(tokens, base + timedelta(minutes=i * 5))
    assert stats.cycle_count == 3
    assert stats.total_tokens == 120_000
    assert stats.avg_tokens_per_cycle == 40_000  # 120000 // 3
    assert stats.last_cycle_ended_at == base + timedelta(minutes=10)


def test_session_stats_avg_zero_when_no_cycles():
    """Defensive: avg accessor on empty stats should return 0, not divide by zero."""
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    assert stats.avg_tokens_per_cycle == 0


def test_session_stats_forensic_cycle_increments_count_but_not_tokens():
    """spec §4.5.3 lifecycle: forensic / retry-exhausted cycles 调 record_cycle(0, ts).
    cycle_count 计入但 total_tokens 不增 — avg 反映 trigger 容量浪费."""
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    base = datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc)
    stats.record_cycle(50_000, base)
    stats.record_cycle(0, base + timedelta(minutes=5))   # forensic
    stats.record_cycle(0, base + timedelta(minutes=10))  # retry-exhausted
    assert stats.cycle_count == 3
    assert stats.total_tokens == 50_000
    assert stats.avg_tokens_per_cycle == 16_666  # 50000 // 3


def test_session_stats_last_cycle_ended_at_overwrites_each_record():
    """T-INT-8 / T-INT-9 spec invariant: last_cycle_ended_at 跨日不重置（lifecycle bound to session
    not daily budget）—— record 调用每次覆盖到 latest cycle 的 end_ts."""
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    day1 = datetime(2026, 5, 2, 23, 55, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 3, 3, 55, 0, tzinfo=timezone.utc)
    stats.record_cycle(40_000, day1)
    stats.record_cycle(35_000, day2)
    assert stats.last_cycle_ended_at == day2
    # 跨日不归零（不显式 reset 调用）
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_session_state.py -v
```

Expected: 6 FAIL with `ModuleNotFoundError: No module named 'src.cli.session_state'`.

- [ ] **Step 1.3: Implement SessionStats**

Create `src/cli/session_state.py`:

```python
"""Session-level cycle tracking — independent of daily token budget reset.

Decoupled from TokenBudget (which has its own daily lifecycle)：cycle 时序
metric 是 session 语义（不跨日重置 last_cycle_ended_at），与 TokenBudget._used
归零节奏不同。R2-8a §4.5.3.
"""
from __future__ import annotations

from datetime import datetime


class SessionStats:
    """Session-level cycle tracker. 1 instance per cli session, lives from
    session start to shutdown. NOT reset on daily token budget reset
    (跨夜 wake interval 仍可见 → "+540 min from prev"）."""

    def __init__(self) -> None:
        self._cycle_count = 0
        self._total_tokens = 0
        self._last_cycle_ended_at: datetime | None = None

    def record_cycle(self, cycle_tokens: int, cycle_ended_at: datetime) -> None:
        """Called once per cycle, after format_cycle_output renders.

        forensic / retry-exhausted cycles 也调用此 (cycle_tokens=0)，
        消耗 trigger 容量但无 token 产出 — avg 反映容量浪费。
        """
        self._cycle_count += 1
        self._total_tokens += cycle_tokens
        self._last_cycle_ended_at = cycle_ended_at

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def avg_tokens_per_cycle(self) -> int:
        if self._cycle_count == 0:
            return 0
        return self._total_tokens // self._cycle_count

    @property
    def last_cycle_ended_at(self) -> datetime | None:
        return self._last_cycle_ended_at
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_session_state.py -v
```

Expected: 6 PASS.

- [ ] **Step 1.5: Commit**

```bash
git add src/cli/session_state.py tests/test_session_state.py
git commit -m "feat(iter-w2r2-8a): add SessionStats class (T1)

新建 src/cli/session_state.py，session-级 cycle tracker，与 daily TokenBudget
解耦。cycle_count / total_tokens / avg_tokens_per_cycle / last_cycle_ended_at
四 metric。forensic / retry-exhausted cycles 调 record_cycle(0, ts)，
cycle_count 计入但 total_tokens 不增（avg 反映容量浪费）。
spec §4.5.3 + AC13/AC14/AC26."
```

---

## Task 2: cycle_fixtures builder（测试基础设施）

**Files:**
- Create: `tests/fixtures/__init__.py`（空文件，package marker；如已存在则 skip）
- Create: `tests/fixtures/cycle_fixtures.py`
- Test: 新文件 `tests/test_cycle_fixtures.py`

**Spec ref:** §5.3 (新 builder) + §7.4 mock fidelity + AC16

- [ ] **Step 2.1: Check if tests/fixtures/ exists as Python package**

```bash
ls tests/fixtures/__init__.py 2>/dev/null && echo "exists" || echo "missing"
```

Expected: `missing`（当前目录只含 OKX JSON fixture 文件，无 `__init__.py`）。

- [ ] **Step 2.2: Create package marker**

Create `tests/fixtures/__init__.py`（空文件）：

```python
```

- [ ] **Step 2.3: Write failing sanity tests for builder**

Create `tests/test_cycle_fixtures.py`:

```python
"""Sanity tests for tests/fixtures/cycle_fixtures.build_cycle_messages.

Builder 构造 in-memory list[ModelRequest | ModelResponse] 给 display.py 集成
测试用。结构参数参考 .working/verify_message_structure.py 实测：
- 每 ModelResponse 1 ThinkingPart at parts[0]（先于 ToolCallPart）
- 跨 ModelResponse 时序 = LLM 生成时序
- 最终 ModelResponse 含 ThinkingPart + TextPart
"""
from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart


def test_build_cycle_messages_minimal_no_tools():
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Quick reasoning."],
        tool_call_segments=[[]],   # 1 segment, 0 tool calls
        final_text="Final decision text.",
    )
    response_msgs = [m for m in msgs if isinstance(m, ModelResponse)]
    assert len(response_msgs) == 1, "thinking_segments=1 → 1 ModelResponse"
    parts = response_msgs[0].parts
    assert isinstance(parts[0], ThinkingPart)
    assert parts[0].content == "Quick reasoning."
    assert any(isinstance(p, TextPart) and p.content == "Final decision text." for p in parts)


def test_build_cycle_messages_multi_segment_with_tools():
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Think 1", "Think 2", "Think 3 final"],
        tool_call_segments=[
            [("get_market_data", {}, "BTC $75,000")],
            [("get_position", {}, "Short 0.265 @ 75350"),
             ("get_open_orders", {}, "1 orders")],
            [],  # final response: no tool calls, only ThinkingPart + TextPart
        ],
        final_text="Hold short.",
    )
    response_msgs = [m for m in msgs if isinstance(m, ModelResponse)]
    assert len(response_msgs) == 3
    # ModelResponse[0]: ThinkingPart + 1 ToolCallPart
    parts0 = response_msgs[0].parts
    assert isinstance(parts0[0], ThinkingPart)
    assert sum(1 for p in parts0 if isinstance(p, ToolCallPart)) == 1
    # ModelResponse[1]: ThinkingPart + 2 ToolCallPart
    parts1 = response_msgs[1].parts
    assert isinstance(parts1[0], ThinkingPart)
    assert sum(1 for p in parts1 if isinstance(p, ToolCallPart)) == 2
    # ModelResponse[2]: ThinkingPart + TextPart (no tools)
    parts2 = response_msgs[2].parts
    assert isinstance(parts2[0], ThinkingPart)
    assert any(isinstance(p, TextPart) for p in parts2)
    # Tool returns wired into ModelRequest with matching tool_call_id
    request_msgs = [m for m in msgs if isinstance(m, ModelRequest)]
    return_parts = [p for m in request_msgs for p in m.parts if isinstance(p, ToolReturnPart)]
    assert len(return_parts) == 3, "3 tool calls → 3 returns"
    # Returns reference the same tool_call_id as the calls
    call_ids = {p.tool_call_id for m in response_msgs for p in m.parts if isinstance(p, ToolCallPart)}
    return_ids = {p.tool_call_id for p in return_parts}
    assert call_ids == return_ids


def test_build_cycle_messages_no_thinking():
    """Non-thinking model: thinking_segments=[None, None] → ModelResponse 无 ThinkingPart."""
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=[None, None],
        tool_call_segments=[[("get_position", {}, "FLAT")], []],
        final_text="No action.",
    )
    response_msgs = [m for m in msgs if isinstance(m, ModelResponse)]
    assert all(
        not any(isinstance(p, ThinkingPart) for p in r.parts)
        for r in response_msgs
    ), "thinking_segments=None → 全无 ThinkingPart"
```

- [ ] **Step 2.4: Run tests to verify they fail**

```bash
uv run pytest tests/test_cycle_fixtures.py -v
```

Expected: 3 FAIL with `ModuleNotFoundError: No module named 'tests.fixtures.cycle_fixtures'`.

- [ ] **Step 2.5: Implement builder**

Create `tests/fixtures/cycle_fixtures.py`:

```python
"""In-memory builder for pydantic-ai message lists used by display.py tests.

Why an in-memory builder (instead of dumping binary fixtures to git)：
- 不持久化二进制 fixture 避免反序列化执行风险
- pydantic-ai message classes 不全支持 model_dump_json round-trip（私有字段）
- 测试需要 thinking 长度等参数化控制——builder 比静态 fixture 更灵活

Structure mirrors .working/verify_message_structure.py 实测 (2026-05-02)：
- 每 ModelResponse parts 顺序: [ThinkingPart, ToolCallPart...]（最终: [ThinkingPart, TextPart]）
- ToolReturnPart 在后续 ModelRequest 内，通过 tool_call_id 关联
- 跨 ModelResponse 时序 = LLM 生成时序
"""
from __future__ import annotations

import uuid

from pydantic_ai.messages import (
    ModelRequest, ModelResponse, TextPart, ThinkingPart,
    ToolCallPart, ToolReturnPart,
)


def build_cycle_messages(
    thinking_segments: list[str | None],
    tool_call_segments: list[list[tuple[str, dict, str]]],
    final_text: str,
) -> list[ModelRequest | ModelResponse]:
    """Build a list of pydantic-ai messages mimicking 1 cycle.

    Args:
        thinking_segments: per-ModelResponse thinking content（None=该 Response 无 ThinkingPart）
        tool_call_segments: per-ModelResponse list of (tool_name, args_dict, return_content)
        final_text: text in the final ModelResponse's TextPart

    Length contract: len(thinking_segments) == len(tool_call_segments) == N，
    其中 N = ModelResponse 数。最终 ModelResponse 强制有 TextPart（final_text）。
    """
    if len(thinking_segments) != len(tool_call_segments):
        raise ValueError("thinking_segments and tool_call_segments must have equal length")
    n = len(thinking_segments)
    if n == 0:
        raise ValueError("at least 1 segment required")

    msgs: list[ModelRequest | ModelResponse] = []

    for i in range(n):
        # Build ModelResponse parts: [ThinkingPart?, ToolCallPart..., TextPart? if last]
        parts: list = []
        if thinking_segments[i] is not None:
            parts.append(ThinkingPart(content=thinking_segments[i]))
        tool_calls_for_response: list[ToolCallPart] = []
        for tool_name, args_dict, _ret_content in tool_call_segments[i]:
            tcp = ToolCallPart(
                tool_name=tool_name,
                args=args_dict,
                tool_call_id=f"call_{uuid.uuid4().hex[:8]}",
            )
            parts.append(tcp)
            tool_calls_for_response.append(tcp)
        if i == n - 1:
            parts.append(TextPart(content=final_text))
        msgs.append(ModelResponse(parts=parts))

        # If this Response had tool calls, append a ModelRequest with matching ToolReturnPart
        if tool_calls_for_response:
            return_parts = []
            for tcp, (_tn, _args, ret_content) in zip(
                tool_calls_for_response, tool_call_segments[i]
            ):
                return_parts.append(ToolReturnPart(
                    tool_name=tcp.tool_name,
                    tool_call_id=tcp.tool_call_id,
                    content=ret_content,
                ))
            msgs.append(ModelRequest(parts=return_parts))

    return msgs
```

- [ ] **Step 2.6: Run tests to verify they pass**

```bash
uv run pytest tests/test_cycle_fixtures.py -v
```

Expected: 3 PASS. If `ToolReturnPart` constructor signature differs from above (pydantic-ai `outcome` field default), check via:

```bash
uv run python -c "from pydantic_ai.messages import ToolReturnPart; import inspect; print(inspect.signature(ToolReturnPart.__init__))"
```

If `outcome` is required, add `outcome="success"` to the constructor call. (Spec §4.3 micro-spec uses `part.outcome`; default is "success" in pydantic-ai 1.78 but verify.)

- [ ] **Step 2.7: Commit**

```bash
git add tests/fixtures/__init__.py tests/fixtures/cycle_fixtures.py tests/test_cycle_fixtures.py
git commit -m "test(iter-w2r2-8a): add cycle_fixtures.build_cycle_messages builder (T2)

In-memory builder 构造 list[ModelRequest | ModelResponse] 给 display.py 集成
测试用 (T-INT-1a/2/3 等)。不持久化二进制 fixture（避免反序列化执行风险）。
结构参数参考 .working/verify_message_structure.py 实测 (1 ThinkingPart per
ModelResponse / ThinkingPart 在 parts[0] / 跨 Response 时序 = LLM 时序)。
spec §5.3 + §7.4 + AC16."
```

---

## Task 3: Render helpers + CycleRenderContext dataclass

**Files:**
- Modify: `src/cli/display.py`（新增 import + dataclass + 5 个 `_render_*` 私有 helper；不动 `format_cycle_output` 公开签名）
- Test: `tests/test_display_cycle.py`（追加 ~16 helper 单测 T-RH/T-RR/T-RA/T-RD/T-RF + escape）

**Spec ref:** §4.1.1-§4.5.1 段级契约 + §5.1 dataclass + §7.1 helper 单测

- [ ] **Step 3.1: Write failing tests for render helpers**

Append to `tests/test_display_cycle.py`（在文件末尾）：

```python


# === R2-8a: Render helper unit tests (T-RH / T-RR / T-RA / T-RD / T-RF) ===

from datetime import datetime, timezone


def _make_state_snapshot(position=None, balance=None, errors=None):
    """Helper: minimal state_snapshot dict matching cycle_capture._capture_state_snapshot output."""
    return {
        "position": position,
        "balance": balance,
        "market": None,
        "pending_orders": [],
        "active_alerts": [],
        "_errors": errors or [],
        "_cycle_id": "test-cycle",
    }


# --- T-RH: _render_header ---


def test_render_header_full_alert_trigger():
    """T-RH-1: 完整字段 — ALERT trigger + 持仓 + balance."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    stats.record_cycle(40_000, datetime(2026, 5, 2, 18, 2, 23, tzinfo=timezone.utc))
    out = _render_header(
        cycle_id="9f57abcd",
        trigger_type="alert",
        trigger_context={
            "type": "percentage_alert",
            "symbol": "BTC/USDT:USDT",
            "current_price": 75448.0,
            "reference_price": 76225.0,
            "change_pct": -1.6,
            "window_minutes": 10,
            "timestamp": "2026-05-02T18:14:23Z",
        },
        state_snapshot=_make_state_snapshot(
            position={
                "symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.265,
                "entry_price": 75350.0, "unrealized_pnl": 75.0,
                "leverage": 5, "liquidation_price": 0.0, "pnl_pct": 0.10,
            },
            balance={"total_usdt": 9990.0, "free_usdt": 9990.0, "used_usdt": 0.0},
        ),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=stats,
    )
    assert "9f57" in out
    assert "18:14:23 UTC" in out
    assert "+12 min from prev" in out
    assert "ALERT" in out
    assert "vol -1.6%/10min" in out
    assert "75,448" in out and "76,225" in out
    assert "Short 0.265 @ $75,350" in out
    assert "(5x)" in out
    assert "PnL +0.10%" in out
    assert "Balance $9,990" in out


def test_render_header_first_cycle():
    """T-RH-2: 首 cycle，stats.last_cycle_ended_at=None → '(first cycle)'."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    out = _render_header(
        cycle_id="aabbccdd",
        trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=SessionStats(),
    )
    assert "(first cycle)" in out
    assert "+0 min" not in out


def test_render_header_trigger_context_none():
    """T-RH-3: trigger_context=None → 仅 {TYPE_UPPER} 不带详情."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    out = _render_header(
        cycle_id="aabbccdd",
        trigger_type="alert",
        trigger_context=None,
        state_snapshot=_make_state_snapshot(),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=SessionStats(),
    )
    assert "ALERT" in out
    assert "—" not in out.split("Trigger")[1].split("\n")[0]  # 无 em-dash 后缀


def test_render_header_scheduled_no_metadata():
    """spec §4.1.3: scheduled_tick verbatim "Trigger    SCHEDULED" 不带 em-dash 后缀."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    out = _render_header(
        cycle_id="aabbccdd",
        trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=SessionStats(),
    )
    trigger_line = next(l for l in out.splitlines() if "Trigger" in l)
    assert trigger_line.strip().startswith("Trigger") and "SCHEDULED" in trigger_line
    assert "—" not in trigger_line


def test_render_header_flat_no_position():
    """§4.1.4: position=None → State 段渲染 'FLAT | Balance $X'."""
    from src.cli.display import _render_header
    from src.cli.session_state import SessionStats
    out = _render_header(
        cycle_id="aabbccdd",
        trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(
            balance={"total_usdt": 10000.0, "free_usdt": 10000.0, "used_usdt": 0.0},
        ),
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        stats=SessionStats(),
    )
    state_line = next(l for l in out.splitlines() if "State" in l)
    assert "FLAT" in state_line
    assert "Balance $10,000" in state_line


# --- T-RR: _render_reasoning ---


def test_render_reasoning_under_800():
    """T-RR-1: thinking < 800 chars → no truncation marker."""
    from src.cli.display import _render_reasoning
    text = "Position fine — limit short still pending at 75550."
    out = _render_reasoning(text)
    assert "▾ Reasoning" in out
    assert f"({len(text)} chars total)" in out
    assert "... [+" not in out
    assert text in out


def test_render_reasoning_at_800_exact():
    """T-RR-2: thinking == 800 chars → no marker."""
    from src.cli.display import _render_reasoning
    text = "x" * 800
    out = _render_reasoning(text)
    assert "(800 chars total)" in out
    assert "... [+" not in out


def test_render_reasoning_over_800_truncated():
    """T-RR-3: thinking > 800 chars → truncate to 800 + '... [+N chars]' marker."""
    from src.cli.display import _render_reasoning
    text = "y" * 1547
    out = _render_reasoning(text)
    assert "(1547 chars total)" in out
    assert "... [+747 chars]" in out
    # body length 800 chars + marker
    assert out.count("y") == 800


def test_render_reasoning_multiline_indent():
    """T-RR-4: thinking 含 \\n → 每行加 2-space indent."""
    from src.cli.display import _render_reasoning
    text = "Line 1.\nLine 2.\nLine 3."
    out = _render_reasoning(text)
    body_lines = [l for l in out.splitlines() if l.startswith("  ")]
    assert any("Line 1." in l for l in body_lines)
    assert any("Line 2." in l for l in body_lines)
    assert any("Line 3." in l for l in body_lines)


def test_render_reasoning_escape_rich_markup():
    """spec §4.2.2 P1 escape: thinking content 含 [red] / [bold] 等字面值需 escape，
    避免 console.print 解析为 markup 渲染错乱."""
    from src.cli.display import _render_reasoning
    text = "Discussing [red]error handling[/] in code."
    out = _render_reasoning(text)
    # rich.markup.escape 把 '[red]' → '\\[red]'，body 含 escaped form
    assert r"\[red]" in out


# --- T-RA: _render_action ---


def test_render_action_multi_tools():
    """T-RA-1: 3 ToolCallPart → '▾ Action (3 tools)' 复数."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action
    calls = [
        ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="c1"),
        ToolCallPart(tool_name="get_position", args={}, tool_call_id="c2"),
        ToolCallPart(tool_name="get_open_orders", args={}, tool_call_id="c3"),
    ]
    returns = {
        "c1": ToolReturnPart(tool_name="get_market_data", tool_call_id="c1",
                              content="=== Ticker ===\nPrice: 75212.0"),
        "c2": ToolReturnPart(tool_name="get_position", tool_call_id="c2",
                              content="No open positions."),
        "c3": ToolReturnPart(tool_name="get_open_orders", tool_call_id="c3",
                              content="No pending orders."),
    }
    out = _render_action(calls, returns, cycle_id="9f57abcd")
    assert "▾ Action (3 tools)" in out
    assert "get_market_data" in out
    assert "get_position" in out
    assert "get_open_orders" in out


def test_render_action_single_tool_singular():
    """T-RA-2: 1 ToolCallPart → '▾ Action (1 tool)' 单数."""
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart
    from src.cli.display import _render_action
    calls = [ToolCallPart(tool_name="set_next_wake", args={"minutes": 5}, tool_call_id="c1")]
    returns = {
        "c1": ToolReturnPart(tool_name="set_next_wake", tool_call_id="c1",
                              content="Next wake set to 5 min"),
    }
    out = _render_action(calls, returns, cycle_id="9f57abcd")
    assert "▾ Action (1 tool)" in out
    assert "▾ Action (1 tools)" not in out


def test_render_action_missing_return_fallback():
    """T-TC-4: ret lookup miss → '⚙ {tool_name} [no return captured]' + 不抛."""
    from pydantic_ai.messages import ToolCallPart
    from src.cli.display import _render_action
    calls = [ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="orphan")]
    out = _render_action(calls, returns_lookup={}, cycle_id="9f57abcd")
    assert "[no return captured]" in out
    assert "get_market_data" in out


# --- T-RD: _render_decision ---


def test_render_decision_multiline_markdown_indented():
    """T-RD-1: 完整 markdown 内嵌，每行 2-space indent."""
    from src.cli.display import _render_decision
    text = "## Title\n\n**Bold** text.\n- Item 1\n- Item 2"
    out = _render_decision(text)
    assert "▾ Decision" in out
    body_lines = [l for l in out.splitlines() if l and not l.startswith("▾")]
    for l in body_lines:
        assert l.startswith("  "), f"Decision body not indented: {l!r}"


def test_render_decision_escape_rich_markup():
    """spec §4.4.1 attack surface: result.output 含 [red] 字面值 → 强制 escape."""
    from src.cli.display import _render_decision
    text = "Result: [red]rejected[/] by approval."
    out = _render_decision(text)
    assert r"\[red]" in out


# --- T-RF: _render_footer ---


def test_render_footer_full_normal_path():
    """T-RF-1: 正常 cycle footer — 含 cycle_tokens / Session / Cache / Duration / Ended."""
    from src.cli.display import _render_footer, CycleRenderContext
    from src.cli.session_state import SessionStats
    stats = SessionStats()
    # Pretend 7 cycles already done (avg 47k each)
    for i in range(7):
        stats.record_cycle(47_000, datetime(2026, 5, 2, 18, i, 0, tzinfo=timezone.utc))
    ctx = CycleRenderContext(
        cycle_id="9f57abcd",
        trigger_type="alert",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        messages=[],
        final_text="",
        cycle_tokens=41_947,
        stats=stats,
        cache_hit_rate=93.2,
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 2, 18, 14, 27, tzinfo=timezone.utc),
        forensic_reason=None,
    )
    out = _render_footer(ctx)
    assert "41,947 cycle" in out
    # Projected total = 7*47000 + 41947 = 370947 → 371k rounded
    assert "Session 371k" in out
    # Projected count = 8 cycles
    assert "8 cycles" in out
    # Projected avg = 370947 // 8 = 46368 → 46k rounded
    assert "avg 46k/cycle" in out
    assert "Cache    93.2% hit rate" in out
    assert "Duration 4.0s" in out
    assert "Ended 18:14:27 UTC" in out


def test_render_footer_forensic_path():
    """spec §6.4: forensic → Cache N/A (forensic) + cycle_tokens=0."""
    from src.cli.display import _render_footer, CycleRenderContext
    from src.cli.session_state import SessionStats
    ctx = CycleRenderContext(
        cycle_id="9f57abcd",
        trigger_type="alert",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        messages=None,
        final_text=None,
        cycle_tokens=0,
        stats=SessionStats(),
        cache_hit_rate=None,
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 2, 18, 14, 27, tzinfo=timezone.utc),
        forensic_reason="usage_limit_exceeded",
    )
    out = _render_footer(ctx)
    assert "Cache    N/A (forensic)" in out
    assert "0 cycle" in out


def test_render_footer_aborted_path():
    """spec §6.5: retry-exhausted → Cache N/A (aborted)."""
    from src.cli.display import _render_footer, CycleRenderContext
    from src.cli.session_state import SessionStats
    ctx = CycleRenderContext(
        cycle_id="9f57abcd",
        trigger_type="alert",
        trigger_context={"type": "scheduled_tick"},
        state_snapshot=_make_state_snapshot(),
        messages=None,
        final_text=None,
        cycle_tokens=0,
        stats=SessionStats(),
        cache_hit_rate=None,
        cycle_started_at=datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 2, 18, 14, 30, tzinfo=timezone.utc),
        forensic_reason="aborted: ConnectionError: timeout",
    )
    out = _render_footer(ctx)
    assert "Cache    N/A (aborted)" in out
```

- [ ] **Step 3.2: Run helper tests to verify they fail**

```bash
uv run pytest tests/test_display_cycle.py -v -k "render_header or render_reasoning or render_action or render_decision or render_footer"
```

Expected: ~16 FAIL with `AttributeError: module 'src.cli.display' has no attribute '_render_header'` (or similar). Existing legacy `format_cycle_output` 4 tests **still PASS** (we haven't changed signature yet).

- [ ] **Step 3.3: Implement render helpers + CycleRenderContext dataclass in display.py**

Modify `src/cli/display.py`:

(a) Add imports at top of file (after line 5 `from rich.panel import Panel`) + module-level logger:

```python
import logging
from dataclasses import dataclass
from datetime import datetime
from rich.markup import escape
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, TextPart, ThinkingPart,
    ToolCallPart, ToolReturnPart,
)

from src.cli.session_state import SessionStats

logger = logging.getLogger(__name__)
```

(b) Add `CycleRenderContext` dataclass (insert just before existing `def format_cycle_output` at line ~325 — adjacent to the function it serves, **not** at file top — keeps related layout):

```python
@dataclass(frozen=True)
class CycleRenderContext:
    """Single-arg context for format_cycle_output(ctx). Constructed by run_agent_cycle
    once per cycle; 3 paths (normal / forensic / retry-exhausted) share this dataclass.

    Field nullability semantics:
        messages / final_text: None for forensic (UsageLimitExceeded — agent.run raised, result=None)
                                and retry-exhausted (3 attempts failed)
        cycle_tokens: 0 for forensic / retry-exhausted (per spec §4.5.3 caveat — not physical 0)
        cache_hit_rate: None triggers footer "N/A (forensic)" / "N/A (aborted)" branch
        forensic_reason: "usage_limit_exceeded" | "aborted: <error class>: <msg[:200]>" | None
    """
    cycle_id: str
    trigger_type: str               # "scheduled" / "conditional" / "alert"
    trigger_context: dict | None    # in-memory dict from _capture_trigger_context
    state_snapshot: dict | None     # in-memory dict from _capture_state_snapshot
    messages: list | None
    final_text: str | None
    cycle_tokens: int
    stats: SessionStats
    cache_hit_rate: float | None
    cycle_started_at: datetime
    cycle_ended_at: datetime
    forensic_reason: str | None
```

(c) Add `_extract_reasoning_per_response` helper + the 5 `_render_*` private helpers (insert before existing `def format_cycle_output`). Order: extract → trigger detail → state line → header → reasoning → action → decision → footer.

```python
# === R2-8a: Cycle log narrative render helpers (spec §4) ===


def _extract_reasoning_per_response(messages: list) -> list[str | None]:
    """每个 ModelResponse 仅取首个 ThinkingPart 的 content（与 pre-impl smoke baseline 一致）。

    返回 list 长度 = ModelResponse 数；None = 该 Response 无 ThinkingPart。
    与 src.cli.app._extract_thinking_text 行为分离：
    - 渲染层（本 helper）接受 '每 Response 首 ThinkingPart' 限缩 — 时序渲染消费
    - DB 写入层（_extract_thinking_text）保持全收集 — agent_cycles.reasoning 列写入
    spec §4.2.3 drift guard T-DG-1 兜底两 helper 在 smoke baseline 行为等价。

    Placement note: spec §5.3 列在 app.py，本 plan 改放 display.py（消费者所在层）
    避免 display→app 循环 import；helper 唯一使用方是 format_cycle_output。
    """
    out: list[str | None] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            thinking_parts = [p for p in msg.parts if isinstance(p, ThinkingPart)]
            if thinking_parts:
                if len(thinking_parts) > 1:
                    # spec §6.3 T-RE-6 + spec §4.2.4: smoke baseline 是每 Response 1 ThinkingPart;
                    # 多 ThinkingPart per Response 出现 → drift signal (R2-8c / N12 议题接管)
                    logger.warning(
                        "ModelResponse has %d ThinkingParts (smoke baseline = 1); "
                        "renderer takes only parts[0] — see spec §4.2.4 / R2-8c",
                        len(thinking_parts),
                    )
                out.append(thinking_parts[0].content)
            else:
                out.append(None)
    return out


_TRIGGER_LINE_PREFIX = "  Trigger    "
_STATE_LINE_PREFIX   = "  State      "

# Mapping from trigger_context.type → Header "Trigger" line detail formatter
def _format_trigger_detail(trigger_type: str, ctx: dict | None) -> str:
    """Format Header 'Trigger    ...' line per spec §4.1.3.

    Returns the entire content after the column prefix; e.g.,
        "ALERT — vol -1.6%/10min fired (BTC 76,225 → 75,448)"
        "SCHEDULED"
    """
    type_upper = trigger_type.upper()
    if not ctx:
        return type_upper

    ctx_type = ctx.get("type")

    if ctx_type == "scheduled_tick":
        # spec §4.1.3 verbatim: "Trigger    SCHEDULED" — 无 em-dash 后缀
        return type_upper

    if ctx_type == "fill":
        # spec §6.1 T-EH-3 partial degradation: 缺 fill_price / 其他字段 → 保留 trigger_reason
        # （TP/SL/liquidation/market_close 区分是 conditional cycle 排查关键信息）
        tr = ctx.get("trigger_reason")
        if tr is None:
            return type_upper  # 连 trigger_reason 都缺 → 全 fallback
        try:
            symbol_short = (ctx.get("symbol") or "").split("/")[0]
            return (
                f"{type_upper} — {tr} {ctx['position_side']} "
                f"{symbol_short} {ctx['amount']} @ ${ctx['fill_price']:,.0f}, "
                f"PnL {ctx['pnl']:+.2f} USDT"
            )
        except (KeyError, TypeError):
            return f"{type_upper} — {tr}"  # spec §6.1 T-EH-3: 部分降级保留 trigger_reason

    if ctx_type == "price_level_alert":
        try:
            symbol_short = (ctx.get("symbol") or "").split("/")[0]
            return (
                f"{type_upper} — {symbol_short} reached "
                f"{ctx['current_price']:,.0f} ({ctx['direction']} "
                f"${ctx['target_price']:,.0f} alert)"
            )
        except (KeyError, TypeError):
            return type_upper

    if ctx_type == "percentage_alert":
        try:
            symbol_short = (ctx.get("symbol") or "").split("/")[0]
            return (
                f"{type_upper} — vol {ctx['change_pct']:+.1f}%/{ctx['window_minutes']}min "
                f"fired ({symbol_short} {ctx['reference_price']:,.0f} → "
                f"{ctx['current_price']:,.0f})"
            )
        except (KeyError, TypeError):
            return type_upper

    # Unknown type (schema drift) — fallback to bare type
    logger.warning(
        "trigger_context.type unknown: %r (keys=%r)",
        ctx_type, list(ctx.keys()) if ctx else None,
    )
    return type_upper


def _format_state_line(state_snapshot: dict | None) -> str:
    """Format Header 'State    ...' line per spec §4.1.4.

    Examples:
        持仓: "Short 0.265 @ $75,350 (5x) | PnL +0.10% | Balance $9,990"
        无仓: "FLAT | Balance $10,000"
        snapshot=None: "[snapshot unavailable]"
    """
    if state_snapshot is None:
        return "[snapshot unavailable]"

    pos = state_snapshot.get("position")
    bal = state_snapshot.get("balance")
    parts: list[str] = []

    if pos is None:
        parts.append("FLAT")
    else:
        try:
            side = pos["side"].capitalize()
            contracts = pos["contracts"]
            entry = pos["entry_price"]
            leverage = pos.get("leverage")
            piece = f"{side} {contracts} @ ${entry:,.0f}"
            if leverage:
                piece += f" ({leverage}x)"
            parts.append(piece)
            pnl_pct = pos.get("pnl_pct")
            if pnl_pct is not None:
                parts.append(f"PnL {pnl_pct:+.2f}%")
        except (KeyError, TypeError):
            parts.append("[position malformed]")

    if bal is not None:
        try:
            parts.append(f"Balance ${bal['total_usdt']:,.0f}")
        except (KeyError, TypeError):
            pass  # 缺字段 → 静默省略 Balance 段（spec §4.1.4）

    return " | ".join(parts) if parts else "[snapshot unavailable]"


def _render_header(
    cycle_id: str,
    trigger_type: str,
    trigger_context: dict | None,
    state_snapshot: dict | None,
    cycle_started_at: datetime,
    stats: SessionStats,
) -> str:
    """Render Header section per spec §4.1.1."""
    short_id = cycle_id[:4]
    start_ts = cycle_started_at.strftime("%H:%M:%S UTC")
    if stats.last_cycle_ended_at is None:
        delta_segment = "(first cycle)"
    else:
        delta_min = int((cycle_started_at - stats.last_cycle_ended_at).total_seconds() / 60)
        delta_segment = f"+{delta_min} min from prev"

    sep_top = "═" * 75
    sep_mid = "─" * 75

    trigger_line = _format_trigger_detail(trigger_type, trigger_context)
    state_line = _format_state_line(state_snapshot)

    return (
        f"{sep_top}\n"
        f"  Cycle {short_id}  •  {start_ts}  •  {delta_segment}\n"
        f"{sep_mid}\n"
        f"{_TRIGGER_LINE_PREFIX}{trigger_line}\n"
        f"{_STATE_LINE_PREFIX}{state_line}\n"
        f"{sep_top}"
    )


def _render_reasoning(thinking_text: str, max_chars: int = 800) -> str:
    """Render Reasoning section per spec §4.2.1-§4.2.2.

    Hard-truncate body to max_chars + ' ... [+N chars]' marker. Body must be
    rich.markup.escape()'d — thinking content is LLM output, attack surface
    of same shape as Decision body.
    """
    total = len(thinking_text)
    if total <= max_chars:
        body = thinking_text
        suffix = ""
    else:
        body = thinking_text[:max_chars]
        remaining = total - max_chars
        suffix = f" ... [+{remaining} chars]"
    indented = "\n".join(f"  {escape(line)}" for line in body.splitlines())
    return f"\n▾ Reasoning ({total} chars total)\n{indented}{suffix}"


def _render_action(
    tool_calls: list,
    returns_lookup: dict,
    cycle_id: str,
) -> str:
    """Render Action section per spec §4.3.

    `tool_calls` is list[ToolCallPart], `returns_lookup` is dict[tool_call_id, ToolReturnPart].
    Tool summary line uses existing resolve_tool_display() (parser layer is R2-8c scope).
    """
    n = len(tool_calls)
    plural = "tool" if n == 1 else "tools"
    lines = [f"\n▾ Action ({n} {plural})"]

    for tcp in tool_calls:
        try:
            args = tcp.args_as_dict()
        except Exception:
            args = None

        ret = returns_lookup.get(tcp.tool_call_id)
        if ret is None:
            logger.warning(
                "tool_call_id mismatch for %s in cycle %s",
                tcp.tool_name, cycle_id,
            )
            line = f"  ⚙ {tcp.tool_name:<22} [no return captured]"
        else:
            content_str = str(ret.content)
            outcome = getattr(ret, "outcome", "success")
            icon, summary = resolve_tool_display(tcp.tool_name, content_str, outcome, args)
            # body escape 防 markup attack（summary 来自 tool return content；
            # 框架 markup icon / column padding 在 prefix 部分不动）
            line = f"  {icon} {tcp.tool_name:<22} {escape(summary)}"
        lines.append(line)

    return "\n".join(lines)


def _render_decision(text: str) -> str:
    """Render Decision section per spec §4.4.1.

    Full markdown body inlined with 2-space indent. Rich markup escape forced —
    LLM output may contain [red]/[bold] literals that would otherwise be parsed
    as Rich markup (attack surface widened by 'full markdown inlined' vs. legacy
    short agent_output).
    """
    indented = "\n".join(f"  {line}" for line in escape(text).splitlines())
    return f"\n▾ Decision\n{indented}"


def _render_footer(ctx: "CycleRenderContext") -> str:
    """Render Footer section per spec §4.5.1.

    Projected stats (含当前 cycle): footer renders BEFORE stats.record_cycle is called,
    so we add cycle_tokens / +1 cycle inline (spec §4.5.3 P1 fix — avoid lifecycle reorder
    to prevent last_cycle_ended_at self-reference).
    """
    sep_mid = "─" * 75
    sep_bot = "═" * 75

    proj_total = ctx.stats.total_tokens + ctx.cycle_tokens
    proj_count = ctx.stats.cycle_count + 1
    proj_avg = proj_total // proj_count if proj_count > 0 else 0
    session_total_k = round(proj_total / 1000)
    session_avg_k = round(proj_avg / 1000)

    # Cache line: forensic / aborted → N/A; normal → percentage
    if ctx.cache_hit_rate is None:
        if ctx.forensic_reason and ctx.forensic_reason.startswith("aborted"):
            cache_line = "Cache    N/A (aborted)"
        else:
            cache_line = "Cache    N/A (forensic)"
    else:
        cache_line = f"Cache    {ctx.cache_hit_rate:.1f}% hit rate"

    duration = (ctx.cycle_ended_at - ctx.cycle_started_at).total_seconds()
    end_ts = ctx.cycle_ended_at.strftime("%H:%M:%S UTC")

    return (
        f"\n{sep_mid}\n"
        f"  Tokens   {ctx.cycle_tokens:,} cycle  |  Session {session_total_k}k "
        f"(avg {session_avg_k}k/cycle, {proj_count} cycles)\n"
        f"  {cache_line}\n"
        f"  Duration {duration:.1f}s  |  Ended {end_ts}\n"
        f"{sep_bot}"
    )
```

- [ ] **Step 3.4: Run helper tests to verify they pass**

```bash
uv run pytest tests/test_display_cycle.py -v -k "render_header or render_reasoning or render_action or render_decision or render_footer"
```

Expected: ~16 PASS. Existing 4 legacy `format_cycle_output_*` tests still PASS (untouched).

If `ToolCallPart`/`ToolReturnPart` constructor signatures differ from `args=` / `content=`, adjust per Step 2.6 verification.

- [ ] **Step 3.5: Commit**

```bash
git add src/cli/display.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8a): add CycleRenderContext + 5 render helpers (T3)

新增 src/cli/display.py 内 CycleRenderContext frozen dataclass + 5 个私有
_render_* helper (header/reasoning/action/decision/footer)，与 spec §4 段级
契约一一对应。format_cycle_output 公开签名暂未改 (T5 任务)，summarize_tool /
_fallback_summary / is_tool_error / resolve_tool_display 不动 (R2-8c 议题)。

Reasoning + Decision body 强制 rich.markup.escape() 防 LLM 输出含 [red] /
[bold] 字面值时 console.print 解析为 markup (spec §4.2.2 P1 + §4.4.1 attack
surface).

新增 ~16 helper 单测 (T-RH/T-RR/T-RA/T-RD/T-RF + scheduled / FLAT / escape)，
spec §7.1 矩阵 11 cases 之外加 markup escape 验证。

spec §4.1-§4.5 + AC1-AC10 部分覆盖."
```

---

## Task 4: build_services 5-tuple + run_agent_cycle stats kwarg

**Files:**
- Modify: `src/cli/app.py`（`build_services` return 4 → 5；`run_agent_cycle` 接受 `stats` kwarg with `_DummySessionStats` default；`run` 解构 5-tuple；`on_tick` 传 stats 到 `run_agent_cycle`）
- Modify: `tests/test_n3_wiring.py`（5 处 4-tuple → 5-tuple line 92/111/128/141/158）
- Modify: `tests/test_wizard.py`（3 处 4-tuple → 5-tuple line 481/526/552）
- Modify: `tests/test_okx_algo_normalization.py`（patch 链增加 `src.cli.app.SessionStats`）

**Spec ref:** §5.3 注 2 / §5.4 / AC22

- [ ] **Step 4.1: Write failing test for build_services 5-tuple shape (inspect-based)**

`SimulatedExchange` / `OKXExchange` 等大量构造在 `build_services` 内 lazy import 或真实调用，patch 链 brittle (e.g. `src.cli.app.SimulatedExchange` 是 lazy import 不是 module-level attr)。改用 `inspect.getsource` 静态 signature 检查——没有 patch chain 不会因 mock 不全而 raise。

Append to `tests/test_session_state.py`：

```python


# === R2-8a: build_services 5-tuple wiring (static signature check) ===


def test_build_services_returns_5_tuple_per_source():
    """spec §5.3: build_services return signature must be 5-tuple ending with SessionStats.

    Static check via inspect.getsource — avoids patch chain fragility (SimulatedExchange /
    MetricsService / PriceAlertService are lazy-imported inside build_services, and mocking
    them via patch('src.cli.app.X', create=True) does NOT intercept the local import).
    Behavioral coverage of the 5-tuple destructure is provided by tests/test_n3_wiring.py
    (5 sites) + tests/test_wizard.py (3 sites) — those tests destructure 5-tuple and would
    naturally fail with ValueError if build_services returned 4-tuple.
    """
    import inspect
    from src.cli.app import build_services
    src = inspect.getsource(build_services)
    last_return_line = next(
        l for l in reversed(src.splitlines()) if l.strip().startswith("return ")
    )
    items = [s.strip() for s in last_return_line.replace("return", "", 1).split(",")]
    assert len(items) == 5, (
        f"build_services should return 5-tuple, got {len(items)}-tuple: {items}\n"
        f"last return line: {last_return_line!r}"
    )
    assert items[-1] == "stats", (
        f"5th tuple element should be 'stats' (SessionStats instance), got {items[-1]!r}"
    )
```

- [ ] **Step 4.2: Run to verify it fails**

```bash
uv run pytest tests/test_session_state.py::test_build_services_returns_5_tuple_per_source -v
```

Expected: FAIL — current build_services returns 4-tuple ending with `budget` not `stats`.

- [ ] **Step 4.3: Update build_services + run_agent_cycle in src/cli/app.py**

(a) Add SessionStats import at top of file (with other src.cli imports, after line 27):

```python
from src.cli.session_state import SessionStats
```

(b) Add `_DummySessionStats` module-level singleton near `TokenBudget` class (insert just after line 97 `class TokenBudget` body ends):

```python


class _DummySessionStats(SessionStats):
    """No-op SessionStats subclass for tests that pass run_agent_cycle without stats kwarg.

    Inherits SessionStats so type annotations `stats: SessionStats` are LSP-compatible
    (no mypy/pyright strict warning). __init__ inherits → properties return 0/None defaults.
    Override only record_cycle to no-op (no per-cycle stat mutation).

    Module-level singleton (`_DUMMY_STATS`) — avoid per-cycle instantiation overhead.
    """
    def record_cycle(self, cycle_tokens: int, cycle_ended_at: datetime) -> None:  # noqa: ARG002
        pass  # no-op — discard inputs


_DUMMY_STATS = _DummySessionStats()
```

(c) Update `run_agent_cycle` signature (line 118-127) to accept `stats` kwarg with `_DUMMY_STATS` default:

Replace:
```python
async def run_agent_cycle(
    agent,
    deps: TradingDeps,
    trigger_type: str,
    budget: TokenBudget,
    engine,
    context=None,
    model=None,
    console=None,
):
```

With:
```python
async def run_agent_cycle(
    agent,
    deps: TradingDeps,
    trigger_type: str,
    budget: TokenBudget,
    engine,
    context=None,
    model=None,
    console=None,
    stats: SessionStats | None = None,
):
    if stats is None:
        stats = _DUMMY_STATS
```

(d) Update `build_services` return statement at line 466 (4-tuple → 5-tuple):

Replace:
```python
    return exchange, deps, agent, budget
```

With:
```python
    stats = SessionStats()
    return exchange, deps, agent, budget, stats
```

(e) Update `run` function — line 515 destructure:

Replace:
```python
    exchange, deps, agent, budget = build_services(
        result, engine, session_id, sc, settings,
    )
```

With:
```python
    exchange, deps, agent, budget, stats = build_services(
        result, engine, session_id, sc, settings,
    )
```

(f) Update `on_tick` (line 530-537) — pass stats kwarg:

Replace:
```python
            await run_agent_cycle(
                agent, deps, trigger_type, budget, engine,
                context, model=result.model, console=sc,
            )
```

With:
```python
            await run_agent_cycle(
                agent, deps, trigger_type, budget, engine,
                context, model=result.model, console=sc, stats=stats,
            )
```

- [ ] **Step 4.4: Run shape test**

```bash
uv run pytest tests/test_session_state.py::test_build_services_returns_5_tuple_per_source -v
```

Expected: PASS — `build_services` source's last `return` line now contains 5 comma-separated items ending with `stats`.

- [ ] **Step 4.5: Update test_wizard.py 5-tuple destructure (3 sites)**

```bash
grep -n "exchange, deps, agent, budget = build_services\|_, deps, _, _ = build_services" tests/test_wizard.py
```

Expected output: line 481 (`exchange, deps, agent, budget = build_services(`), line 526 + 552 (`_, deps, _, _ = build_services(`).

Edit `tests/test_wizard.py`:

Replace at line 481 region:
```python
        exchange, deps, agent, budget = build_services(
```

With:
```python
        exchange, deps, agent, budget, _stats = build_services(
```

Replace at line 526 + 552 regions (use `replace_all` since both lines have identical text):
```python
        _, deps, _, _ = build_services(
```

With:
```python
        _, deps, _, _, _stats = build_services(
```

- [ ] **Step 4.6: Update test_n3_wiring.py 5-tuple destructure (5 sites)**

```bash
grep -n "exchange, deps, agent, budget = build_services" tests/test_n3_wiring.py
```

Expected: line 92, 111, 128, 141, 158. All 5 occurrences are identical text — use `replace_all`:

Replace 5 occurrences of:
```python
    exchange, deps, agent, budget = build_services(
```

With:
```python
    exchange, deps, agent, budget, _stats = build_services(
```

- [ ] **Step 4.7: Update test_okx_algo_normalization.py patch chain (1 site)**

The test at line 35-82 patches multiple services then calls `build_services`. Find the `with patch(...)` block (line 59-65) and append a `SessionStats` patch:

Find:
```python
    with patch("src.cli.app.OKXExchange") as mock_okx_cls, \
         patch("src.cli.app.MarketDataService"), \
         patch("src.cli.app.TechnicalAnalysisService"), \
         patch("src.cli.app.MemoryService"), \
         patch("src.cli.app.TokenBudget"), \
         patch("src.cli.app.ApprovalGate"), \
         patch("src.cli.app.create_trader_agent"):
```

Replace with:
```python
    with patch("src.cli.app.OKXExchange") as mock_okx_cls, \
         patch("src.cli.app.MarketDataService"), \
         patch("src.cli.app.TechnicalAnalysisService"), \
         patch("src.cli.app.MemoryService"), \
         patch("src.cli.app.TokenBudget"), \
         patch("src.cli.app.ApprovalGate"), \
         patch("src.cli.app.create_trader_agent"), \
         patch("src.cli.app.SessionStats"):
```

(SessionStats patch prevents the new `SessionStats()` instantiation at the end of `build_services` from raising in the heavily mocked environment.)

- [ ] **Step 4.8: Run all touched tests**

```bash
uv run pytest tests/test_wizard.py tests/test_n3_wiring.py tests/test_okx_algo_normalization.py tests/test_session_state.py tests/test_usage_limits.py tests/test_cycle_log.py -v
```

Expected: all PASS. `test_usage_limits.py` 8 sites use kwarg style without `stats=` so default `_DUMMY_STATS` engages (no signature drift). `test_cycle_log.py` 3 sites likewise.

- [ ] **Step 4.9: Commit**

```bash
git add src/cli/app.py tests/test_wizard.py tests/test_n3_wiring.py tests/test_okx_algo_normalization.py tests/test_session_state.py
git commit -m "feat(iter-w2r2-8a): wire SessionStats through build_services + run_agent_cycle (T4)

build_services 4-tuple → 5-tuple (尾元素 SessionStats instance)。
run_agent_cycle 接 stats kwarg with _DUMMY_STATS default (module-level
singleton, no-op record_cycle) — 兼容 test_usage_limits.py 8 处 + test_cycle_log.py
3 处 kwarg-style 调用无需改签名。

测试更新：
- test_wizard.py 3 处 4-tuple 解构 → 5-tuple
- test_n3_wiring.py 5 处 4-tuple 解构 → 5-tuple
- test_okx_algo_normalization.py patch 链补 SessionStats
- test_session_state.py 加 5-tuple shape sanity 测试

_DummySessionStats 继承 SessionStats（LSP 兼容；type annotation 'stats: SessionStats'
不违反 mypy strict）。

行为变化：仅 wiring；run_agent_cycle 内部尚未消费 stats（T5 任务装填 ctx
+ 调 stats.record_cycle）。

spec §5.3 注 2 + §5.4 + AC22."
```

---

## Task 5: format_cycle_output(ctx) reframe + run_agent_cycle 装填 + integration tests

**Files:**
- Modify: `src/cli/display.py`（`format_cycle_output` 重构为 single-arg `(ctx: CycleRenderContext)`）
- Modify: `src/cli/app.py`（新增 `_extract_reasoning_per_response` helper；`run_agent_cycle` 装填 `CycleRenderContext` + 调 `format_cycle_output(ctx)` + 调 `stats.record_cycle`；capture `cycle_started_at` / `cycle_ended_at`；forensic 路径同 capture + 调 ctx）
- Modify: `tests/test_display_cycle.py`（4 个 legacy `format_cycle_output_*` 测试改 `CycleRenderContext` 签名 + 加 11 集成测试 T-INT-1a/1b/2/3/4/5/5b/6/7/8/9/10）

**Spec ref:** §5.1 / §5.2 / §4.2.3 / §6.4 / §7.2 / AC1-AC10

- [ ] **Step 5.1: Write failing integration tests**

Append to `tests/test_display_cycle.py`:

```python


# === R2-8a: format_cycle_output(ctx) integration tests (T-INT-*) ===


from datetime import timedelta


def _make_ctx(
    cycle_id="9f57abcd",
    trigger_type="alert",
    trigger_context=None,
    state_snapshot=None,
    messages=None,
    final_text="",
    cycle_tokens=10_000,
    stats=None,
    cache_hit_rate=92.0,
    cycle_started_at=None,
    cycle_ended_at=None,
    forensic_reason=None,
):
    from src.cli.display import CycleRenderContext
    from src.cli.session_state import SessionStats
    if stats is None:
        stats = SessionStats()
    if cycle_started_at is None:
        cycle_started_at = datetime(2026, 5, 2, 18, 14, 23, tzinfo=timezone.utc)
    if cycle_ended_at is None:
        cycle_ended_at = cycle_started_at + timedelta(seconds=4)
    if state_snapshot is None:
        state_snapshot = _make_state_snapshot()
    if trigger_context is None:
        trigger_context = {"type": "scheduled_tick"}
    return CycleRenderContext(
        cycle_id=cycle_id, trigger_type=trigger_type,
        trigger_context=trigger_context, state_snapshot=state_snapshot,
        messages=messages, final_text=final_text, cycle_tokens=cycle_tokens,
        stats=stats, cache_hit_rate=cache_hit_rate,
        cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
        forensic_reason=forensic_reason,
    )


def test_int_1a_section_structure_via_builder():
    """T-INT-1a: 5 段架构结构断言 — Header / Reasoning / Action / Decision / Footer."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Initial assessment.", "Need more data.", "Decision time."],
        tool_call_segments=[
            [("get_market_data", {}, "=== Ticker (BTC/USDT:USDT) ===\nPrice: 75212.0")],
            [("get_position", {}, "No open positions.")],
            [],
        ],
        final_text="Hold position. 5min wake.",
    )
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="Hold position. 5min wake."))
    # Header
    assert "Cycle 9f57" in out
    assert "Trigger" in out and "State" in out
    # Reasoning + Action 交织 (3 Reasoning, 2 Action segments — final has no tools)
    assert out.count("▾ Reasoning") == 3
    assert out.count("▾ Action") == 2
    # Decision precedes Footer
    decision_idx = out.find("▾ Decision")
    footer_idx = out.find("Tokens")
    assert decision_idx > 0 and footer_idx > decision_idx, "Decision must precede Footer"
    # Footer
    assert "Cache" in out and "Duration" in out


def test_int_1b_structural_fragments_vs_mockup():
    """T-INT-1b: Structural fragments check against spec §3.2 mockup (illustrative —
    not byte-equal verbatim per spec §3.2 注 'illustrative, non-byte-equal copy')."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    # Hand-craft messages to match mockup §3.2 cycle 9f57 structure:
    # 4 ModelResponse with Reasoning + tool calls, plus 1 final ModelResponse with TextPart.
    # We validate structural layout + key string fragments — mockup is illustrative
    # per spec §3.2 注, not byte-equal.
    msgs = build_cycle_messages(
        thinking_segments=["Z" * 892, "X" * 1247, "Y" * 1567, "W" * 445],
        tool_call_segments=[
            [("get_market_data", {}, "BTC $75,212"),
             ("get_position", {}, "Short 0.265 @ $75,350"),
             ("get_open_orders", {}, "1 orders")],
            [("get_derivatives_data", {}, "Funding ..."),
             ("get_recent_trades", {}, "Recent ..."),
             ("get_higher_timeframe_view", {}, "HTF ..."),
             ("get_multi_timeframe_snapshot", {}, "MTF ...")],
            [("get_market_news", {}, "FGI Value: 26"),
             ("get_price_pivots", {}, "Pivots ..."),
             ("get_macro_context", {}, "BTC.D 58.00%")],
            [("add_price_level_alert", {}, "Price level alert set: below 74,890"),
             ("add_price_level_alert", {}, "Price level alert set: above 75,625"),
             ("set_next_wake", {}, "Next wake set to 10 min")],
        ],
        final_text="## Situation Assessment: BTC Flash Crash\n\n**What happened**: BTC dropped ~1.6% in 10 minutes",
    )
    out = format_cycle_output(_make_ctx(
        messages=msgs,
        final_text="## Situation Assessment: BTC Flash Crash\n\n**What happened**: BTC dropped ~1.6% in 10 minutes",
        cycle_tokens=41_947,
    ))
    # Structural fragments
    assert "Cycle 9f57" in out
    assert "(892 chars total)" in out
    assert "(1247 chars total)" in out
    assert "▾ Action (3 tools)" in out
    assert "▾ Action (4 tools)" in out
    assert "Situation Assessment" in out
    assert "41,947 cycle" in out


def test_int_2_non_thinking_model():
    """T-INT-2: 非 thinking model → 跳过 ▾ Reasoning，▾ Action 紧接 Header."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=[None, None],  # no ThinkingPart
        tool_call_segments=[[("get_position", {}, "FLAT")], []],
        final_text="No action.",
    )
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="No action."))
    assert "▾ Reasoning" not in out, "non-thinking model 不应渲染 Reasoning 段"
    assert "▾ Action" in out
    assert "▾ Decision" in out


def test_int_3_zero_tool_call_cycle():
    """T-INT-3: 0 tool call cycle → 仅 Reasoning + Decision，无 ▾ Action."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["Direct decision, no info needed."],
        tool_call_segments=[[]],
        final_text="Hold.",
    )
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="Hold."))
    assert "▾ Reasoning" in out
    assert "▾ Action" not in out
    assert "▾ Decision" in out


def test_int_4_forensic_usage_limit_exceeded():
    """T-INT-4: forensic 路径 → Header + Footer + 占位 Decision，Cache N/A (forensic)."""
    from src.cli.display import format_cycle_output
    out = format_cycle_output(_make_ctx(
        messages=None, final_text=None, cycle_tokens=0,
        cache_hit_rate=None, forensic_reason="usage_limit_exceeded",
    ))
    assert "▾ Reasoning" not in out, "forensic 不渲染 partial Reasoning"
    assert "▾ Action" not in out, "forensic 不渲染 partial Action"
    assert "[no decision — usage limit exceeded; partial messages unavailable]" in out
    assert "Cache    N/A (forensic)" in out


def test_int_5_retry_exhausted_path():
    """T-INT-5: retry-exhausted → 占位 Decision + Cache N/A (aborted)."""
    from src.cli.display import format_cycle_output
    out = format_cycle_output(_make_ctx(
        messages=None, final_text=None, cycle_tokens=0,
        cache_hit_rate=None,
        forensic_reason="aborted: ConnectionError: timeout",
    ))
    assert "[cycle aborted — 3 attempts failed: ConnectionError: timeout]" in out
    assert "Cache    N/A (aborted)" in out


def test_int_5b_retry_exhausted_with_markup_in_error():
    """T-INT-5b: retry-exhausted error message 含 markup 字面值 → 仅一次 escape，
    终端显示自然字面值无反斜杠 (spec §5.2 round-7 校准)."""
    from src.cli.display import format_cycle_output
    out = format_cycle_output(_make_ctx(
        messages=None, final_text=None, cycle_tokens=0,
        cache_hit_rate=None,
        forensic_reason="aborted: RuntimeError: [red]boom[/]",
    ))
    # rich.markup.escape converts '[red]' → '\\[red]' (single backslash); intended
    # console.print input — terminal renders it literally as '[red]' visible to user.
    # Test asserts placeholder + no MarkupError raised (function returns successfully)
    # + no double-escape (no '\\\\[red]' which would render as visible backslash).
    assert "RuntimeError" in out
    assert "boom" in out
    assert r"\\[red]" not in out  # double-escape signature


def test_int_6_session_stats_累计_with_forensic():
    """T-INT-6: 5 cycles 累加（含 1 forensic）→ footer Session 累计 / forensic 也计 cycle_count."""
    from src.cli.display import format_cycle_output
    from src.cli.session_state import SessionStats
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    stats = SessionStats()
    base = datetime(2026, 5, 2, 18, 0, 0, tzinfo=timezone.utc)
    # Pretend 4 prior cycles: 3 normal (40k each) + 1 forensic (0)
    stats.record_cycle(40_000, base)
    stats.record_cycle(40_000, base + timedelta(minutes=5))
    stats.record_cycle(0, base + timedelta(minutes=10))   # forensic
    stats.record_cycle(40_000, base + timedelta(minutes=15))
    # 5th cycle (current): normal 40k → projected total 160k / 5 cycles
    msgs = build_cycle_messages(
        thinking_segments=["Decision."], tool_call_segments=[[]], final_text="OK.",
    )
    out = format_cycle_output(_make_ctx(
        messages=msgs, final_text="OK.", cycle_tokens=40_000, stats=stats,
        cycle_started_at=base + timedelta(minutes=20),
        cycle_ended_at=base + timedelta(minutes=20, seconds=4),
    ))
    # Projected: total 160k / count 5 / avg 32k
    assert "Session 160k" in out
    assert "5 cycles" in out
    assert "avg 32k/cycle" in out


def test_int_7_cache_hit_rate_normal_branch():
    """T-INT-7: cache_hit_rate=92.0 → footer 'Cache    92.0% hit rate'."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="d")
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="d", cache_hit_rate=92.0))
    assert "Cache    92.0% hit rate" in out


def test_int_8_session_stats_no_cross_day_reset():
    """T-INT-8 / AC13: 跨日 last_cycle_ended_at 不重置 → Header 显示 +X min from prev."""
    from src.cli.display import format_cycle_output
    from src.cli.session_state import SessionStats
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    stats = SessionStats()
    # Day 1 last cycle 23:55 UTC
    stats.record_cycle(40_000, datetime(2026, 5, 2, 23, 55, 0, tzinfo=timezone.utc))
    # Day 2 first cycle 03:55 UTC → +240 min
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="d")
    out = format_cycle_output(_make_ctx(
        messages=msgs, final_text="d", stats=stats,
        cycle_started_at=datetime(2026, 5, 3, 3, 55, 0, tzinfo=timezone.utc),
        cycle_ended_at=datetime(2026, 5, 3, 3, 55, 4, tzinfo=timezone.utc),
    ))
    assert "+240 min from prev" in out
    assert "(first cycle)" not in out


def test_int_9_first_cycle_short_label():
    """AC10 / T-RH-2 集成版：首 cycle Header '(first cycle)' 不带 +X min from prev."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="d")
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="d"))  # default fresh stats
    assert "(first cycle)" in out


def test_int_11_decision_uses_ctx_final_text_not_textpart():
    """T-INT-11 (P1 reviewer 补): Decision 段 SoT = ctx.final_text，不依赖 messages 中
    TextPart 提取 (spec §4.4.2)。

    Scenario: messages 最终 ModelResponse 仅 ThinkingPart 无 TextPart (理论极少；
    pydantic-ai 在某些 structured output 模式下可能合成 result.output 与 messages 不同)。
    Renderer 应信任 ctx.final_text 仍渲染 Decision 段。"""
    from pydantic_ai.messages import ModelResponse, ThinkingPart
    from src.cli.display import format_cycle_output
    msgs = [ModelResponse(parts=[ThinkingPart(content="thought.")])]
    out = format_cycle_output(_make_ctx(
        messages=msgs, final_text="Synthesized decision from ctx.",
    ))
    assert "▾ Decision" in out
    assert "Synthesized decision from ctx." in out


def test_int_12_decision_empty_string_placeholder():
    """spec §4.4.3: ctx.final_text == "" → [empty decision text] 占位."""
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="")
    out = format_cycle_output(_make_ctx(messages=msgs, final_text=""))
    assert "[empty decision text]" in out


def test_int_10_unknown_trigger_type_fallback(caplog):
    """T-INT-10 / T-EH-2 (renumbered): trigger_context.type 未知 → fallback {TYPE_UPPER} 不带详情."""
    import logging
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(thinking_segments=["t"], tool_call_segments=[[]], final_text="d")
    with caplog.at_level(logging.WARNING):
        out = format_cycle_output(_make_ctx(
            messages=msgs, final_text="d", trigger_type="alert",
            trigger_context={"type": "unknown_future_type"},
        ))
    trigger_line = next(l for l in out.splitlines() if "Trigger" in l)
    assert "ALERT" in trigger_line
    assert "—" not in trigger_line  # 无 em-dash → 无详情后缀
    assert any("trigger_context.type unknown" in r.message for r in caplog.records)
```

- [ ] **Step 5.2: Migrate 4 legacy format_cycle_output tests in tests/test_display_cycle.py to new signature**

Locate `test_format_cycle_output_basic` (line 381) and 3 others. Replace each test body to use `CycleRenderContext` instead of 6-arg call. Use the `_make_ctx` helper added in Step 5.1.

Replace `test_format_cycle_output_basic` (line 381):

Find:
```python
def test_format_cycle_output_basic():
    from src.cli.display import format_cycle_output
    tool_calls = [
        {"tool_name": "get_market_data", "content": "=== Ticker (BTC/USDT:USDT) ===\nPrice: 84200.00 | Bid: 84190.00 | Ask: 84210.00\n\n=== Technical Indicators (15m) ===\nCurrent Price: 84200.00\n\nRSI(14): 62.30\n\n=== Market Context ===\nATR(14): 101.04 (0.12% of price, 15m candles)", "outcome": "success"},
        {"tool_name": "get_position", "content": "No open positions.", "outcome": "success"},
    ]
    result = format_cycle_output(
        cycle_id="a3f2e1b4",
        trigger_type="scheduled",
        tool_calls=tool_calls,
        agent_output="Market is quiet, no action taken.",
        tokens_used=1200,
        budget_remaining=48800,
    )
    assert "a3f2" in result
    assert "scheduled" in result
    assert "get_market_data" in result
    assert "get_position" in result
    assert "Agent:" in result
    assert "Market is quiet" in result
    assert "1,200" in result
    assert "48,800" in result
```

Replace with:
```python
def test_format_cycle_output_basic():
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=[None],
        tool_call_segments=[[
            ("get_market_data", {}, "=== Ticker (BTC/USDT:USDT) ===\nPrice: 84200.00 | Bid: 84190.00 | Ask: 84210.00\n\n=== Technical Indicators (15m) ===\nCurrent Price: 84200.00\n\nRSI(14): 62.30\n\n=== Market Context ===\nATR(14): 101.04 (0.12% of price, 15m candles)"),
            ("get_position", {}, "No open positions."),
        ]],
        final_text="Market is quiet, no action taken.",
    )
    out = format_cycle_output(_make_ctx(
        cycle_id="a3f2e1b4", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        messages=msgs, final_text="Market is quiet, no action taken.",
        cycle_tokens=1200,
    ))
    assert "a3f2" in out
    assert "SCHEDULED" in out  # was "scheduled" — uppercase per spec
    assert "get_market_data" in out
    assert "get_position" in out
    assert "Market is quiet" in out
    assert "1,200" in out
    # budget_remaining → no longer in footer (replaced by Session累计); skip that assertion
```

Replace `test_format_cycle_output_with_memory` (line 405):

Find:
```python
def test_format_cycle_output_with_memory():
    from src.cli.display import format_cycle_output
    tool_calls = [
        {"tool_name": "save_memory", "content": "Memory saved [lesson] (importance=0.8): Always wait for confirmation", "outcome": "success", "args": {"category": "lesson", "content": "Always wait for RSI confirmation before entry", "importance": 0.8}},
    ]
    result = format_cycle_output(
        cycle_id="b5c6d7e8",
        trigger_type="conditional",
        tool_calls=tool_calls,
        agent_output="Lesson recorded.",
        tokens_used=500,
        budget_remaining=49500,
    )
    assert "✎" in result
    assert "[lesson]" in result
    assert "Always wait for RSI confirmation" in result  # full content from args
```

Replace with:
```python
def test_format_cycle_output_with_memory():
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    # save_memory passes args dict for full content (not truncated return)
    msgs = build_cycle_messages(
        thinking_segments=[None],
        tool_call_segments=[[
            ("save_memory",
             {"category": "lesson", "content": "Always wait for RSI confirmation before entry", "importance": 0.8},
             "Memory saved [lesson] (importance=0.8): Always wait for confirmation"),
        ]],
        final_text="Lesson recorded.",
    )
    out = format_cycle_output(_make_ctx(
        cycle_id="b5c6d7e8", trigger_type="conditional",
        trigger_context={"type": "scheduled_tick"},
        messages=msgs, final_text="Lesson recorded.", cycle_tokens=500,
    ))
    assert "✎" in out
    assert "[lesson]" in out
    assert "Always wait for RSI confirmation" in out
```

Replace `test_format_cycle_output_with_error` (line 423):

Find:
```python
def test_format_cycle_output_with_error():
    from src.cli.display import format_cycle_output
    tool_calls = [
        {"tool_name": "open_position", "content": "Trade rejected by human approval.", "outcome": "success"},
    ]
    result = format_cycle_output(
        cycle_id="c7d8e9f0",
        trigger_type="scheduled",
        tool_calls=tool_calls,
        agent_output="Trade was rejected.",
        tokens_used=800,
        budget_remaining=49200,
    )
    assert "✗" in result
```

Replace with:
```python
def test_format_cycle_output_with_error():
    from src.cli.display import format_cycle_output
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=[None],
        tool_call_segments=[[
            ("open_position", {}, "Trade rejected by human approval."),
        ]],
        final_text="Trade was rejected.",
    )
    out = format_cycle_output(_make_ctx(
        cycle_id="c7d8e9f0", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        messages=msgs, final_text="Trade was rejected.", cycle_tokens=800,
    ))
    assert "✗" in out
```

Replace `test_format_cycle_output_outcome_failed` (line 439). Builder default `outcome="success"` won't carry; hand-craft:

Find:
```python
def test_format_cycle_output_outcome_failed():
    from src.cli.display import format_cycle_output
    tool_calls = [
        {"tool_name": "get_market_data", "content": "Connection error", "outcome": "failed"},
    ]
    result = format_cycle_output(
        cycle_id="d1e2f3a4",
        trigger_type="scheduled",
        tool_calls=tool_calls,
        agent_output="Could not fetch data.",
        tokens_used=300,
        budget_remaining=49700,
    )
    assert "✗" in result
```

Replace with:
```python
def test_format_cycle_output_outcome_failed():
    from src.cli.display import format_cycle_output
    from pydantic_ai.messages import (
        ModelRequest, ModelResponse, TextPart,
        ToolCallPart, ToolReturnPart,
    )
    tcp = ToolCallPart(tool_name="get_market_data", args={}, tool_call_id="c1")
    msgs = [
        ModelResponse(parts=[tcp, TextPart(content="Could not fetch data.")]),
        ModelRequest(parts=[
            ToolReturnPart(
                tool_name="get_market_data", tool_call_id="c1",
                content="Connection error", outcome="failed",
            ),
        ]),
    ]
    out = format_cycle_output(_make_ctx(
        cycle_id="d1e2f3a4", trigger_type="scheduled",
        trigger_context={"type": "scheduled_tick"},
        messages=msgs, final_text="Could not fetch data.", cycle_tokens=300,
    ))
    assert "✗" in out
```

- [ ] **Step 5.3: Run tests to verify they fail**

```bash
uv run pytest tests/test_display_cycle.py -v -k "format_cycle_output or test_int"
```

Expected: ~15+ FAIL — current `format_cycle_output` 6-arg signature mismatch.

- [ ] **Step 5.4: Implement format_cycle_output(ctx) reframe in src/cli/display.py**

Replace the existing `format_cycle_output` function (line 326-370) with the `ctx`-arg version (spec §5.2 algorithm):

```python
def format_cycle_output(ctx: CycleRenderContext) -> str:
    """Format a complete cycle's output for terminal/session log display.

    spec §5.2 algorithm: 时序遍历 ModelResponse 分组，每段 think→act→think→act→decision
    交织。Forensic / retry-exhausted (messages=None) 短路渲染 Header + Footer + 占位 Decision.
    """
    lines = [_render_header(
        cycle_id=ctx.cycle_id, trigger_type=ctx.trigger_type,
        trigger_context=ctx.trigger_context, state_snapshot=ctx.state_snapshot,
        cycle_started_at=ctx.cycle_started_at, stats=ctx.stats,
    )]

    # === Forensic / retry-exhausted 短路 ===
    if ctx.messages is None:
        if ctx.forensic_reason and ctx.forensic_reason.startswith("aborted"):
            err_part = ctx.forensic_reason[len("aborted: "):]
            placeholder = f"[cycle aborted — 3 attempts failed: {err_part}]"
        else:  # usage_limit_exceeded
            placeholder = "[no decision — usage limit exceeded; partial messages unavailable]"
        # 仅一次 escape (spec §5.2 round-7 校准 — 不 pre-escape err_part 避免双 escape
        # 显示反斜杠 \[red]boom\[/])
        lines.append(f"\n▾ Decision\n  {escape(placeholder)}")
        lines.append(_render_footer(ctx))
        return "\n".join(lines)

    # === Build tool_call_id → ToolReturnPart map ===
    tool_returns_lookup: dict = {}
    for msg in ctx.messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_returns_lookup[part.tool_call_id] = part

    # === ②③ 时序段 ===
    response_msgs = [m for m in ctx.messages if isinstance(m, ModelResponse)]

    # spec §4.2.3: 渲染层 thinking 提取 SoT 由 _extract_reasoning_per_response 集中
    # （同一函数也供 T-DG-1 drift guard 与 _extract_thinking_text DB 写入路径行为对齐）
    reasoning_per_response = _extract_reasoning_per_response(ctx.messages)

    for i, mr in enumerate(response_msgs):
        thinking = reasoning_per_response[i]
        tool_calls = [p for p in mr.parts if isinstance(p, ToolCallPart)]

        if thinking:
            lines.append(_render_reasoning(thinking))

        if tool_calls:
            lines.append(_render_action(tool_calls, tool_returns_lookup, ctx.cycle_id))

    # === Decision 段 ===
    # spec §4.4.2: 数据源 = ctx.final_text (= result.output, 由 caller 装填) — 单源真相，
    # 不从 messages 重新提取 TextPart (避免双源真相 + ctx.final_text 死字段)。
    # 边界 (spec §4.4.3):
    # - ctx.final_text 非空 → 渲染 markdown 内嵌
    # - ctx.final_text == "" → [empty decision text] 占位
    # - ctx.final_text is None → [no decision text] 占位 (理论极少 — 正常路径
    #   pydantic-ai result.output 总是 str；forensic 路径短路已 return 不进此段)
    if ctx.final_text:
        lines.append(_render_decision(ctx.final_text))
    elif ctx.final_text == "":
        lines.append("\n▾ Decision\n  [empty decision text]")
    else:  # None
        lines.append("\n▾ Decision\n  [no decision text]")

    lines.append(_render_footer(ctx))
    return "\n".join(lines)
```

**重要（P1 reviewer 校准）**: Decision 段改 `ctx.final_text` SoT 后，TextPart import 在 format_cycle_output 内不再使用。`from pydantic_ai.messages import ... TextPart ...` 顶部 import 仍保留（`_render_action` 不使用，但 fixture builder + 测试模块仍 import 这条 path——别 cleanup 这条 import）。如未来需要 TextPart 与 ctx.final_text drift guard，可独立 helper 加 `assert text_parts[0].content == ctx.final_text` 做 sanity check（非 R2-8a scope）。

- [ ] **Step 5.5: (helper 已在 T3 加到 display.py，本步骤跳过)**

`_extract_reasoning_per_response` helper 已在 Task 3 Step 3.3 (c) 加入 `src/cli/display.py`（消费者所在层 — 避免 display→app 循环 import）。本步骤不动 app.py 的 `_extract_thinking_text`（R2-7 行为不动 — AC22）。

- [ ] **Step 5.6: Update run_agent_cycle to build CycleRenderContext + call format_cycle_output(ctx) + call stats.record_cycle**

(a) Insert `cycle_started_at = datetime.now(timezone.utc)` at function entry, just after the `if budget.exhausted` early-return (currently line 130). Add at line 132 (right before `cycle_id = str(uuid.uuid4())[:8]`):

```python
    cycle_started_at = datetime.now(timezone.utc)
```

**Spec drift note**: spec §5.1 line 642 verbatim 是 "`run_agent_cycle` 函数入口时刻"。本 plan 实际放在 `if budget.exhausted: return None` 检查之后——避免 exhausted 路径浪费 datetime.now() 调用 + capture 后字段未消费即 return。与 spec "函数入口时刻" 语义等价（exhausted 直接 return，不进 cycle，无 cycle_started_at 消费方），仅放置位置毫秒级偏移。新会话起手对照 spec §5.1 不必紧张此偏离。

(b) Replace the existing display block at line 300-310 (`if console is not None: ...`).

**Note on tool_calls extraction at app.py:240-277**: 该循环原本同时做 (1) 累积 `tool_calls` list dict 给旧 `format_cycle_output(tool_calls=...)` 用 + (2) `logger.info` / `logger.debug` 写 system log。R2-8a 后 (1) 不再需要（ctx 携带 messages 由 display.py 直接遍历）；(2) **保留**（system log INFO/DEBUG 仍需 per-tool 记录）。建议简化：

Find (line 240-277, full block):
```python
    # === A2: Extract tool calls from message history ===
    tool_calls = []
    _call_args_by_id: dict[str, dict | None] = {}

    for msg in result.new_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    try:
                        args = part.args_as_dict()
                    except Exception:
                        args = None
                    _call_args_by_id[part.tool_call_id] = args
        elif isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    content_str = str(part.content)
                    outcome = part.outcome
                    if part.tool_call_id not in _call_args_by_id:
                        logger.warning(
                            f"tool_call_id mismatch for {part.tool_name}, using fallback"
                        )
                    args = _call_args_by_id.get(part.tool_call_id)
                    tool_calls.append({
                        "tool_name": part.tool_name,
                        "content": content_str,
                        "outcome": outcome,
                        "args": args,
                    })

                    # System log: INFO summary, DEBUG full content
                    icon, summary = resolve_tool_display(
                        part.tool_name, content_str, outcome, args,
                    )
                    logger.info(f"  {icon} {part.tool_name}: {summary}")
                    logger.debug(
                        f"  Tool {part.tool_name} args={args} "
                        f"return={content_str[:500]}"
                    )
```

Replace with（删除 `tool_calls = []` 累积 + `_call_args_by_id` dict + `tool_calls.append(...)`；保留 `_call_args_by_id` 仅用于 args lookup → system log）:
```python
    # === A2: System log per-tool INFO/DEBUG ===
    # (R2-8a: 不再累积 tool_calls list 给 display 用 — ctx 直接消费 messages；
    # _call_args_by_id 仅 lifetime 内 args lookup, 写完 logger 即弃)
    _call_args_by_id: dict[str, dict | None] = {}
    for msg in result.new_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    try:
                        args = part.args_as_dict()
                    except Exception:
                        args = None
                    _call_args_by_id[part.tool_call_id] = args
        elif isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    content_str = str(part.content)
                    outcome = part.outcome
                    if part.tool_call_id not in _call_args_by_id:
                        logger.warning(
                            f"tool_call_id mismatch for {part.tool_name}, using fallback"
                        )
                    args = _call_args_by_id.get(part.tool_call_id)
                    icon, summary = resolve_tool_display(
                        part.tool_name, content_str, outcome, args,
                    )
                    logger.info(f"  {icon} {part.tool_name}: {summary}")
                    logger.debug(
                        f"  Tool {part.tool_name} args={args} "
                        f"return={content_str[:500]}"
                    )
```

Then find (line ~298-310):
```python
    logger.info(f"Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)")

    # === A2: Display formatted cycle output ===
    if console is not None:
        output = format_cycle_output(
            cycle_id=cycle_id,
            trigger_type=trigger_type,
            tool_calls=tool_calls,
            agent_output=result.output,
            tokens_used=tokens,
            budget_remaining=budget.remaining,
        )
        console.print(output)

    return result
```

Replace with:
```python
    logger.info(f"Cycle {cycle_id}: {tokens} tokens ({budget.remaining} remaining)")

    # === R2-8a: Build CycleRenderContext + render + record stats ===
    cycle_ended_at = datetime.now(timezone.utc)
    if console is not None:
        from src.cli.display import CycleRenderContext
        ctx = CycleRenderContext(
            cycle_id=cycle_id, trigger_type=trigger_type,
            trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
            messages=result.new_messages(), final_text=result.output,
            cycle_tokens=tokens, stats=stats, cache_hit_rate=hit_rate,
            cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
            forensic_reason=None,
        )
        console.print(format_cycle_output(ctx))
    stats.record_cycle(tokens, cycle_ended_at)

    return result
```

(c) **forensic 路径** also needs ctx + render + record_cycle. Locate the `except UsageLimitExceeded as e:` block at line 190-209.

Find:
```python
        except UsageLimitExceeded as e:
            # 病理状态（LLM 死循环 / runaway tools），不重试，写 forensic trace。
            # 注：ToolCallRecorder capability 已在 agent.run 内部独立 session 写完
            # 任何已成功 tool 调用的 tool_calls 行（不需要本路径协调 rollback）。
            logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
            async with get_session(engine) as session:
                session.add(AgentCycle(
                    session_id=deps.session_id,
                    cycle_id=cycle_id,
                    triggered_by=trigger_type,
                    trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                    state_snapshot=json.dumps(state_snapshot_var),
                    reasoning=None,                              # R2-7 §6.5: forensic NULL
                    decision=None,
                    execution_status="usage_limit_exceeded",
                    model_id=model_id_var,
                    tokens_consumed=0,                            # spec §3.1 #3: UsageLimitExceeded 不携带 partial usage
                ))
                await session.commit()
            return None
```

Replace with:
```python
        except UsageLimitExceeded as e:
            # 病理状态（LLM 死循环 / runaway tools），不重试，写 forensic trace。
            # 注：ToolCallRecorder capability 已在 agent.run 内部独立 session 写完
            # 任何已成功 tool 调用的 tool_calls 行（不需要本路径协调 rollback）。
            logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
            async with get_session(engine) as session:
                session.add(AgentCycle(
                    session_id=deps.session_id,
                    cycle_id=cycle_id,
                    triggered_by=trigger_type,
                    trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                    state_snapshot=json.dumps(state_snapshot_var),
                    reasoning=None,                              # R2-7 §6.5: forensic NULL
                    decision=None,
                    execution_status="usage_limit_exceeded",
                    model_id=model_id_var,
                    tokens_consumed=0,                            # spec §3.1 #3: UsageLimitExceeded 不携带 partial usage
                ))
                await session.commit()
            # capture cycle_ended_at AFTER DB commit — 与正常路径 (Step 5.6(b)) 时序对齐：
            # Footer Duration 字段语义统一为 "实墙时间含 DB 写入"
            cycle_ended_at = datetime.now(timezone.utc)
            if console is not None:
                from src.cli.display import CycleRenderContext
                ctx = CycleRenderContext(
                    cycle_id=cycle_id, trigger_type=trigger_type,
                    trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
                    messages=None, final_text=None,
                    cycle_tokens=0, stats=stats, cache_hit_rate=None,
                    cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
                    forensic_reason="usage_limit_exceeded",
                )
                console.print(format_cycle_output(ctx))
            stats.record_cycle(0, cycle_ended_at)
            return None
```

(retry-exhausted path comes in Task 6; this Task 5 leaves it as-is `return None` without forensic write — Task 6 atomic delivers that capability.)

- [ ] **Step 5.7: Run all touched tests**

```bash
uv run pytest tests/test_display_cycle.py tests/test_cycle_fixtures.py tests/test_session_state.py tests/test_cycle_log.py tests/test_usage_limits.py -v
```

Expected: all PASS.

- [ ] **Step 5.8: Run full test suite to catch regressions**

```bash
uv run pytest -q
```

Expected: 988 baseline → ~1030 tests collected through T5 (T1: 6 + T2: 3 + T3: 18 + T4: 1 + T5: 14 new + 4 migrated counted only once = +42 net). All PASS. （T6 后再增 16：retry 4 + DG 3 + edge 8 (T-EH-3 / T-EH-3b) + T-FO-3 1 → 终值 ~1046，见 Step 6.9）.

- [ ] **Step 5.9: Commit**

```bash
git add src/cli/display.py src/cli/app.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8a): format_cycle_output(ctx) reframe + run_agent_cycle 装填 (T5)

format_cycle_output 6-arg → CycleRenderContext single-arg；时序遍历
ModelResponse 分组实现 think→act→think→act→decision 5 段架构 (spec §5.2 algorithm)。

run_agent_cycle 装填 ctx：
- 函数入口 capture cycle_started_at（实墙时间含 trigger/state capture IO）
- 正常 + forensic (usage_limit_exceeded) 路径都调 format_cycle_output(ctx)
- 三路径都调 stats.record_cycle(tokens, end_ts) — forensic 调 (0, ts)
- retry-exhausted 路径 Task 6 接管

简化 line 240-277 tool_calls 提取：删除 `tool_calls = []` list 累积 + `tool_calls.append(...)`
（ctx 直接消费 messages 不再需要）；保留 `_call_args_by_id` lookup + system log INFO/DEBUG
per-tool 输出（独立于 cycle log 的 system 层日志仍需）。

`_extract_reasoning_per_response` helper 已在 T3 加到 display.py（消费者所在层），
本 task 在 format_cycle_output 内调用 — drift guard T-DG-1 (T6) 兜底两 helper 行为一致。

测试：4 个 legacy format_cycle_output 测试迁移到 ctx 签名 + 14 集成测试
(T-INT-1a/1b/2/3/4/5/5b/6/7/8/9/10/11/12) 覆盖 spec §7.2 矩阵 + ctx.final_text
SoT (P1 reviewer 校准)：T-INT-11 messages 无 TextPart 但 final_text 非空 / T-INT-12
final_text == "" 占位渲染。

spec §5 + AC1-AC10 全覆盖 + AC22 (_extract_thinking_text 不动)."
```

---

## Task 6: Retry-exhausted forensic write + drift guards + edge tests

**Files:**
- Modify: `src/cli/app.py`（retry-exhausted 分支写 forensic AgentCycle 行 + 调 ctx 渲染 + record_cycle）
- Modify: `tests/test_usage_limits.py`（加 retry-exhausted T-EX-1/2/3/4 测试）
- Modify: `tests/test_display_cycle.py`（加 drift guard T-DG-1/2/3 + 边界 T-EH/T-ES/T-RE 细化测试）

**Spec ref:** §6.5 / §3.3 D16 / §7.3 / AC11-AC14

- [ ] **Step 6.1: Write failing tests for retry-exhausted forensic write**

Append to `tests/test_usage_limits.py`:

```python


# === R2-8a: T-EX retry-exhausted forensic write ===


async def test_usage_limit_exceeded_renders_session_log_placeholder():
    """T-FO-3 (R2-8a 加): UsageLimitExceeded 路径 session log 端到端渲染
    [no decision — usage limit exceeded; partial messages unavailable] 占位 + Cache N/A (forensic).

    与 retry-exhausted test_retry_exhausted_session_log_renders_aborted_placeholder 同型——
    避免 plan Step 5.6(c) 实施时漏写 if console is not None: 分支或 ctx 参数错位
    （只能靠 AC30a sim 兜底，但 sim 难触发 UsageLimitExceeded）。"""
    from pydantic_ai.exceptions import UsageLimitExceeded
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-fo3")
    budget = TokenBudget(daily_max=500_000)

    async def boom(prompt, **kwargs):
        raise UsageLimitExceeded("simulated runaway")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    captured_print = []
    mock_console = MagicMock()
    mock_console.print = lambda s: captured_print.append(s)

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine, console=mock_console,
    )
    assert result is None
    rendered = "\n".join(str(s) for s in captured_print)
    assert "no decision — usage limit exceeded" in rendered, \
        f"未渲染 forensic Decision 占位; rendered={rendered!r}"
    assert "partial messages unavailable" in rendered
    assert "N/A (forensic)" in rendered, "Footer Cache 行未走 forensic 分支"


async def test_retry_exhausted_writes_forensic_agent_cycle():
    """T-EX-1: 3 次重试都失败 → AgentCycle 行 execution_status='retry_exhausted'."""
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.storage.models import AgentCycle
    from sqlalchemy import select

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-tex1")
    budget = TokenBudget(daily_max=500_000)

    call_count = {"n": 0}

    async def boom(prompt, **kwargs):
        call_count["n"] += 1
        raise ConnectionError("simulated network failure")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )
    assert result is None
    assert call_count["n"] == 3, f"应重试 3 次，实际 {call_count['n']}"

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(AgentCycle).where(AgentCycle.execution_status == "retry_exhausted")
        )).scalars().all()
    assert len(rows) == 1, f"应写 1 行 retry_exhausted，实际 {len(rows)}"
    row = rows[0]
    assert row.session_id == "sess-tex1"
    assert row.execution_status == "retry_exhausted"
    assert row.reasoning is None
    assert row.decision is None
    assert row.tokens_consumed == 0


async def test_retry_exhausted_session_log_renders_aborted_placeholder():
    """T-EX-2 (集成版): retry-exhausted 路径 session log 渲染 [cycle aborted ...]."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-tex2")
    budget = TokenBudget(daily_max=500_000)

    async def boom(prompt, **kwargs):
        raise ConnectionError("timeout")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    captured_print = []
    mock_console = MagicMock()
    mock_console.print = lambda s: captured_print.append(s)

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine, console=mock_console,
    )
    assert result is None
    assert any("cycle aborted" in str(s) for s in captured_print), \
        f"未渲染 [cycle aborted] 占位; captured={captured_print!r}"
    assert any("ConnectionError" in str(s) for s in captured_print)
    assert any("timeout" in str(s) for s in captured_print)


async def test_retry_exhausted_records_session_stats():
    """T-EX-3: retry-exhausted 调 stats.record_cycle(0, end_ts) — cycle_count 计入但 total_tokens 不增."""
    from src.cli.app import TokenBudget, run_agent_cycle
    from src.cli.session_state import SessionStats

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-tex3")
    budget = TokenBudget(daily_max=500_000)
    stats = SessionStats()

    async def boom(prompt, **kwargs):
        raise ConnectionError("net err")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine, stats=stats,
    )
    assert stats.cycle_count == 1
    assert stats.total_tokens == 0
    assert stats.last_cycle_ended_at is not None


async def test_retry_exhausted_error_message_truncated_at_200():
    """spec §6.5: error message 超 200 chars → 截断到 200 (forensic placeholder)."""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_engine_with_capture_mocks(session_id="sess-tex4")
    budget = TokenBudget(daily_max=500_000)

    long_msg = "x" * 500
    async def boom(prompt, **kwargs):
        raise ConnectionError(long_msg)

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    captured = []
    mock_console = MagicMock()
    mock_console.print = lambda s: captured.append(s)

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine, console=mock_console,
    )
    rendered = "\n".join(str(s) for s in captured)
    assert "ConnectionError" in rendered
    # spec §6.5 T-EX-2: 200 'x' + ellipsis '...' should appear, but full 500 should not
    assert "x" * 200 in rendered
    assert "x" * 250 not in rendered  # confirms truncation
    assert "..." in rendered, "spec §6.5 T-EX-2 要求截断后追加省略号"
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_usage_limits.py -v -k "retry_exhausted"
```

Expected: 4 FAIL — current retry-exhausted path returns None with no forensic write / no console render / no stats record.

- [ ] **Step 6.3: Implement retry-exhausted forensic path in src/cli/app.py**

Locate the generic `except Exception as e:` block at line 210-217:

Find:
```python
        except Exception as e:
            if attempt < 2:
                delay = 2 ** attempt
                logger.warning(f"LLM call attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                logger.error(f"LLM call failed after 3 attempts: {e}")
                return None
```

Replace with:
```python
        except Exception as e:
            if attempt < 2:
                delay = 2 ** attempt
                logger.warning(f"LLM call attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                # spec §6.5 D16: retry-exhausted forensic write + session log render — 避免 W2 SQL 黑洞
                logger.error(f"LLM call failed after 3 attempts: {e}")
                err_class = type(e).__name__
                # spec §6.5 T-EX-2: > 200 chars 截断 + 省略号
                err_raw = str(e)
                err_msg = (err_raw[:200] + "...") if len(err_raw) > 200 else err_raw
                async with get_session(engine) as session:
                    session.add(AgentCycle(
                        session_id=deps.session_id,
                        cycle_id=cycle_id,
                        triggered_by=trigger_type,
                        trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
                        state_snapshot=json.dumps(state_snapshot_var),
                        reasoning=None,
                        decision=None,
                        execution_status="retry_exhausted",
                        model_id=model_id_var,
                        tokens_consumed=0,
                    ))
                    await session.commit()
                # capture cycle_ended_at AFTER DB commit — 与正常路径 + UsageLimitExceeded 路径
                # 时序对齐：Footer Duration 字段语义统一为 "实墙时间含 DB 写入"
                cycle_ended_at = datetime.now(timezone.utc)
                if console is not None:
                    from src.cli.display import CycleRenderContext
                    ctx = CycleRenderContext(
                        cycle_id=cycle_id, trigger_type=trigger_type,
                        trigger_context=trigger_context_var, state_snapshot=state_snapshot_var,
                        messages=None, final_text=None,
                        cycle_tokens=0, stats=stats, cache_hit_rate=None,
                        cycle_started_at=cycle_started_at, cycle_ended_at=cycle_ended_at,
                        forensic_reason=f"aborted: {err_class}: {err_msg}",
                    )
                    console.print(format_cycle_output(ctx))
                stats.record_cycle(0, cycle_ended_at)
                return None
```

- [ ] **Step 6.4: Run T-EX tests to verify they pass**

```bash
uv run pytest tests/test_usage_limits.py -v -k "retry_exhausted"
```

Expected: 4 PASS.

- [ ] **Step 6.5: Add drift guards (T-DG-1/2/3)**

Append to `tests/test_display_cycle.py`:

```python


# === R2-8a: Drift guards (T-DG-1/2/3) ===


def test_dg_1_extract_helpers_equivalent_at_smoke_baseline():
    """T-DG-1: smoke baseline 下 _extract_thinking_text(messages) 等价于
    "\\n\\n".join(_extract_reasoning_per_response 中非 None 项).

    Future drift signal: 多 ThinkingPart per Response 引入时此断言会 fail，
    提示 R2-8c / N12 议题接管。

    Helper placement: _extract_thinking_text 在 src.cli.app (R2-7 DB 写入路径)；
    _extract_reasoning_per_response 在 src.cli.display (R2-8a 渲染层 — 见 spec
    §4.2.3 + plan T3)。drift guard 跨模块比对两 helper 行为对齐。"""
    from src.cli.app import _extract_thinking_text
    from src.cli.display import _extract_reasoning_per_response
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["alpha", "beta", "gamma"],
        tool_call_segments=[[("get_market_data", {}, "x")], [], []],
        final_text="done",
    )
    full_text = _extract_thinking_text(msgs)
    per_resp = _extract_reasoning_per_response(msgs)
    rejoined = "\n\n".join(t for t in per_resp if t)
    assert full_text == rejoined, (
        f"helper drift detected:\n  _extract_thinking_text => {full_text!r}\n"
        f"  rejoin per-resp        => {rejoined!r}"
    )


def test_dg_2_thinking_part_precedes_toolcall_in_smoke_baseline():
    """T-DG-2: smoke baseline 下 ThinkingPart 在 ToolCallPart 之前 (parts[0])."""
    from pydantic_ai.messages import ModelResponse, ThinkingPart, ToolCallPart
    from tests.fixtures.cycle_fixtures import build_cycle_messages
    msgs = build_cycle_messages(
        thinking_segments=["a", "b"],
        tool_call_segments=[[("get_market_data", {}, "x")], []],
        final_text="d",
    )
    for mr in [m for m in msgs if isinstance(m, ModelResponse)]:
        kinds = [type(p).__name__ for p in mr.parts]
        if "ThinkingPart" in kinds and "ToolCallPart" in kinds:
            assert kinds.index("ThinkingPart") < kinds.index("ToolCallPart"), (
                f"ThinkingPart 应先于 ToolCallPart: {kinds}"
            )


async def test_dg_3_state_snapshot_field_set_unchanged():
    """T-DG-3: state_snapshot 7 字段集合 = R2-7 contract。
    新增字段触发本测试 fail，提示 R2-8a 是否需消费."""
    expected = {
        "position", "balance", "market", "pending_orders",
        "active_alerts", "_errors", "_cycle_id",
    }
    from unittest.mock import AsyncMock, MagicMock
    from src.integrations.exchange.base import Balance, Ticker
    from src.services.cycle_capture import _capture_state_snapshot

    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=100.0, free_usdt=100.0, used_usdt=0.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=100.0, bid=99.0, ask=101.0,
        high=110.0, low=90.0, base_volume=1.0, timestamp=0,
    ))
    snapshot = await _capture_state_snapshot("test-cycle", deps)
    assert set(snapshot.keys()) == expected, (
        f"state_snapshot 字段集合漂移: actual={set(snapshot.keys())} expected={expected}\n"
        "  新增字段 → 检查 R2-8a 是否需消费 (header / footer / 段渲染)；\n"
        "  字段移除 → 检查 R2-8a 渲染 fallback 是否需更新。"
    )
```

- [ ] **Step 6.6: Run drift guards**

```bash
uv run pytest tests/test_display_cycle.py -v -k "dg_"
```

Expected: 3 PASS.

- [ ] **Step 6.7: Add edge tests for §6.1/6.2/6.3 (T-EH/T-ES/T-RE selected cases)**

Append to `tests/test_display_cycle.py`:

```python


# === R2-8a: Edge case 细化 ===


def test_eh_1_trigger_context_none_renders_bare_type():
    """T-EH-1: trigger_context=None → Header 'Trigger    {TYPE_UPPER}' 不带详情."""
    from src.cli.display import _format_trigger_detail
    out = _format_trigger_detail("alert", None)
    assert out == "ALERT"
    out = _format_trigger_detail("conditional", None)
    assert out == "CONDITIONAL"


def test_eh_3_conditional_fill_missing_price_partial_degrade():
    """T-EH-3 (spec §6.1): conditional fill 缺 fill_price → 部分降级保留 trigger_reason。
    trigger_reason (TP/SL/liquidation/market_close) 是 conditional cycle 排查关键信息，
    不应在缺其他字段时连 trigger_reason 一起丢。"""
    from src.cli.display import _format_trigger_detail
    out = _format_trigger_detail("conditional", {
        "type": "fill", "trigger_reason": "TP_FILL",
        # fill_price / position_side / symbol / amount / pnl missing → 部分降级
    })
    assert out == "CONDITIONAL — TP_FILL", (
        f"spec §6.1 T-EH-3 要求保留 trigger_reason 部分降级；实际 {out!r}"
    )


def test_eh_3b_conditional_fill_no_trigger_reason_full_fallback():
    """T-EH-3b: conditional fill 连 trigger_reason 都缺 → 全 fallback 到 {TYPE_UPPER}."""
    from src.cli.display import _format_trigger_detail
    out = _format_trigger_detail("conditional", {"type": "fill"})
    assert out == "CONDITIONAL"


def test_es_1_state_snapshot_none_unavailable():
    """T-ES-1: state_snapshot=None → State 段 [snapshot unavailable]."""
    from src.cli.display import _format_state_line
    assert _format_state_line(None) == "[snapshot unavailable]"


def test_es_2_position_none_renders_flat():
    """T-ES-2: position=None → 'FLAT'."""
    from src.cli.display import _format_state_line
    out = _format_state_line(_make_state_snapshot(
        balance={"total_usdt": 10000.0, "free_usdt": 10000.0, "used_usdt": 0.0},
    ))
    assert "FLAT" in out
    assert "Balance $10,000" in out


def test_es_3_balance_none_omits_balance_segment():
    """T-ES-3: balance=None → 省略 Balance 字段."""
    from src.cli.display import _format_state_line
    out = _format_state_line(_make_state_snapshot(
        position={
            "symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.265,
            "entry_price": 75350.0, "leverage": 5, "unrealized_pnl": 75.0,
            "liquidation_price": 0.0, "pnl_pct": 0.10,
        },
    ))
    assert "Short 0.265" in out
    assert "Balance" not in out


def test_es_5_position_pnl_pct_none_omits_pnl_segment():
    """T-ES-5: pnl_pct=None (notional 0 / 计算失败) → 省略 PnL 字段."""
    from src.cli.display import _format_state_line
    out = _format_state_line(_make_state_snapshot(
        position={
            "symbol": "BTC/USDT:USDT", "side": "short", "contracts": 0.265,
            "entry_price": 75350.0, "leverage": 5, "unrealized_pnl": 0.0,
            "liquidation_price": 0.0, "pnl_pct": None,
        },
        balance={"total_usdt": 10000.0, "free_usdt": 10000.0, "used_usdt": 0.0},
    ))
    assert "Short 0.265" in out
    assert "PnL" not in out


def test_re_2_thinking_empty_string_skipped():
    """T-RE-2: ThinkingPart content == "" → Reasoning 段省略 (与 non-thinking 同型).

    依赖 spec §4.2.4 协议: 空 ThinkingPart 与 'no ThinkingPart' 行为同型 (段省略)。
    若未来协议变更允许 empty render '▾ Reasoning (0 chars total)' 段头（spec §4.2.4
    边界协议演进），此测试会 fail —— 提示同步 spec §4.2.4 + 决议落实 T-RE-2 期望。
    """
    from pydantic_ai.messages import ModelResponse, ThinkingPart, TextPart
    from src.cli.display import format_cycle_output
    msgs = [ModelResponse(parts=[ThinkingPart(content=""), TextPart(content="d")])]
    out = format_cycle_output(_make_ctx(messages=msgs, final_text="d"))
    assert "▾ Reasoning" not in out, "空 ThinkingPart 不应渲染 Reasoning 段"
```

- [ ] **Step 6.8: Run edge tests**

```bash
uv run pytest tests/test_display_cycle.py -v -k "eh_ or es_ or re_"
```

Expected: 7 PASS.

(There's one subtle detail: `format_cycle_output` line `if thinking:` (Step 5.4) — empty string `""` is falsy so it's skipped naturally. T-RE-2 should pass without further code changes.)

- [ ] **Step 6.9: Run full suite**

```bash
uv run pytest -q
```

Expected: ~1046 tests PASS (988 baseline + 58 net = T1: 6 + T2: 3 + T3: 18 + T4: 1 + T5: 14 + T6: 16). 实际 collected 数字以 pytest 报告为准；偏差 ±3 容忍（mock isolation / module-level test 注册顺序）。

- [ ] **Step 6.10: Commit**

```bash
git add src/cli/app.py tests/test_usage_limits.py tests/test_display_cycle.py
git commit -m "feat(iter-w2r2-8a): retry-exhausted forensic write + drift guards (T6)

generic Exception 路径 (3 attempts failed) 加：
- forensic AgentCycle 写入 (execution_status='retry_exhausted', sibling of
  R2-7 'usage_limit_exceeded') — 避免 W2 SQL 黑洞 (spec §6.5 D16 / AC11/AC12)
- session log 渲染 [cycle aborted — 3 attempts failed: <error>]
  (cache N/A (aborted))
- stats.record_cycle(0, ts) — cycle_count 计入但 total_tokens 不增
- error message 截断 200 chars

T-FO-3 (P1 reviewer 补): UsageLimitExceeded 路径 console.print 端到端测试，
对称 retry-exhausted 同型——避免实施时漏写 if console is not None: 分支或
ctx 参数错位。

Drift guards (3): T-DG-1 _extract_helper 等价 / T-DG-2 ThinkingPart 时序 /
T-DG-3 state_snapshot 字段集合不漂。

边界细化 (8): T-EH-1 trigger_context=None / T-EH-3 conditional fill 缺 fill_price
部分降级保留 trigger_reason (spec §6.1) / T-EH-3b 连 trigger_reason 都缺时全 fallback /
T-ES-1/2/3/5 state 缺字段 / T-RE-2 空 ThinkingPart 跳过。

reviewer 第三轮校准：retry-exhausted error message 截断加省略号 (spec §6.5
T-EX-2)；conditional fill partial degrade impl + 测试与 spec §6.1 T-EH-3 对齐；
_extract_reasoning_per_response 多 ThinkingPart per Response 时记 logger.warning
(spec §6.3 T-RE-6)。

spec §6.5 + §3.3 D16 + §7.3 + AC11-AC14 全覆盖."
```

---

## Task 7: Docs + storage/models.py 注释更新

**Files:**
- Modify: `docs/metrics/agent-cycles-schema.md:17`
- Modify: `src/storage/models.py:94`

**Spec ref:** §5.3 影响清单 + AC11/AC12

- [ ] **Step 7.1: Update docs/metrics/agent-cycles-schema.md**

Edit line 17:

Find:
```markdown
| execution_status | VARCHAR(30) DEFAULT 'ok' | ok / usage_limit_exceeded |
```

Replace with:
```markdown
| execution_status | VARCHAR(30) DEFAULT 'ok' | ok / usage_limit_exceeded / retry_exhausted |
```

- [ ] **Step 7.2: Update src/storage/models.py:94 comment**

Find:
```python
    execution_status: Mapped[str] = mapped_column(String(30), default="ok", server_default="ok")  # ok / usage_limit_exceeded
```

Replace with:
```python
    execution_status: Mapped[str] = mapped_column(String(30), default="ok", server_default="ok")  # ok / usage_limit_exceeded / retry_exhausted
```

- [ ] **Step 7.3: Run a quick smoke to verify nothing broke**

```bash
uv run pytest tests/test_storage.py tests/test_alembic_migration.py tests/test_usage_limits.py -q
```

Expected: all PASS.

- [ ] **Step 7.4: Commit**

```bash
git add docs/metrics/agent-cycles-schema.md src/storage/models.py
git commit -m "docs(iter-w2r2-8a): annotate retry_exhausted enum value (T7)

R2-8a §6.5 D16 引入 retry_exhausted execution_status enum 值 (sibling of
usage_limit_exceeded)。schema 文档表格 + AgentCycle 列注释 sync 更新，
W2 SQL pivot 'WHERE execution_status != ok' 涵盖两类 forensic.

不动 schema (String(30) 不是 strict enum，无需 Alembic migration)；spec
§5.3 影响清单 + AC24."
```

---

## Task 8: Final verification — full suite + AC30a sim cycle + AC30b mock smoke

**Files:** none (verification only)

**Spec ref:** AC15-AC20, AC30a, AC30b, AC31

- [ ] **Step 8.1: Run full test suite**

```bash
uv run pytest -q
```

Expected: total tests ~1046 (988 + 58 net additions：T1: 6 + T2: 3 + T3: 18 + T4: 1 + T5: 14 + T6: 16); all PASS. Record exact count for PR description AC19 cross-ref.

- [ ] **Step 8.2: AC20 — verify suite < 3s (no slow regressions)**

```bash
uv run pytest -q --durations=10
```

Expected: top 10 slowest tests < 1s each, total < 3s. If any new test crosses 1s, investigate (potentially mock leakage).

- [ ] **Step 8.3: AC30a — Real sim 1 cycle smoke**

User-driven smoke (per memory `feedback_long_walltime_experiments` — >10min experiments → user runs). Execute:

```bash
PYTHONPATH=src uv run python main.py --debug
```

Pick "simulated" exchange + tiny sample. Wait for at least 1 cycle to complete. Inspect `logs/session_<id>.log` for:

- [ ] Cycle Header `═══` border + `Cycle XXXX  •  HH:MM:SS UTC  •  (first cycle)`
- [ ] `Trigger    SCHEDULED` (or alert/conditional with detail)
- [ ] `State       ...` line
- [ ] At least 1 `▾ Reasoning ({chars} chars total)` (if thinking model active)
- [ ] At least 1 `▾ Action (N tools)`
- [ ] `▾ Decision` followed by 2-space indented body
- [ ] Footer `Tokens   {N} cycle  |  Session {N}k (avg {N}k/cycle, 1 cycles)`
- [ ] `Cache    {NN.N}% hit rate`
- [ ] `Duration {N.N}s  |  Ended HH:MM:SS UTC`

Record verbatim a 1-cycle sample in PR description.

If any structural element is missing, investigate before merging.

- [ ] **Step 8.4: AC30b — Mock-based forensic + retry-exhausted verification (already covered)**

These two paths are covered by:
- `test_usage_limit_exceeded_writes_forensic_agent_cycle` (R2-7 forensic — existing T2 in test_usage_limits.py, unchanged behavior)
- `test_int_4_forensic_usage_limit_exceeded` (T-INT-4 display — Task 5)
- `test_retry_exhausted_writes_forensic_agent_cycle` + `test_retry_exhausted_session_log_renders_aborted_placeholder` + `test_retry_exhausted_records_session_stats` + `test_retry_exhausted_error_message_truncated_at_200` (Task 6)

```bash
uv run pytest tests/test_usage_limits.py tests/test_display_cycle.py -v -k "forensic or retry_exhausted or int_4 or int_5"
```

Expected: all PASS — confirms forensic + retry-exhausted paths are mock-tested.

- [ ] **Step 8.5: AC31 — PR description cross-ref D1-D16 brainstorm decisions**

Draft PR body (paste into PR creation):

```markdown
## Summary

R2-8a — Cycle Log Narrative Architecture Redesign. 把 cycle log 从"工具流水账 +
agent 最终输出"重设计为"还原 think → act → think → act → decision 完整 cognition
flow"，对齐 R2-7 5 维度叙事 schema。

## Brainstorm 决议 (D1-D16)

- D1 ✅ 时序架构（按 ModelResponse 分组遍历 result.new_messages()）
- D2 ✅ thinking 全部截 800 chars + ... [+N chars] 标记
- D3 ✅ thinking 段头 ▾ Reasoning ({total} chars total) 不编号
- D4 ❌ → R2-8c (长尾 fallback 升级)
- D5 ❌ → R2-8c (8 工具 L1 multi-line)
- D6 ✅ tool calls 按 ModelResponse 分组合并
- D7 ✅ terminal/file 双 sink 共用 markup (color stripped via no_color=True)
- D8 ✅ forensic 渲染 Header + Footer + 占位 (partial messages 留 N12a candidate)
- D9 ✅ Session 末 panel 不做 (事后 SQL)
- D10 ✅ 时序信息仅 in-memory，不动 R2-7 schema
- D11 ❌ → R2-8c (mixed C 形态)
- D12 ✅ session log rotation 不加
- D13 ✅ retry-exhausted 渲染 Header + Footer + [cycle aborted ...]
- D14 ✅ 新建 SessionStats class，与 TokenBudget 解耦
- D15 ✅ Footer 用 Session 替代 Cumulative
- D16 ✅ retry-exhausted 写 forensic AgentCycle (execution_status="retry_exhausted")

## AC 状态

- [x] AC1-AC10 5 段架构 + Header + Footer + Decision + 边界
- [x] AC11/AC12 retry-exhausted 渲染 + DB forensic write
- [x] AC13/AC14 SessionStats 跨日不重置 + forensic cycle_count 计入
- [x] AC15 测试矩阵 ~54 cases 全 PASS
- [x] AC16/AC17 fixture builder + hand-crafted structural fragments (mockup illustrative per spec §3.2 注)
- [x] AC18 现有 test 回归全 PASS
- [x] AC19 总测试 988 → ~1046 (净 +58). spec §7.5 / AC19 line 1081 估算 ~1028-1035 (净 +40-47) 是保守区间；plan 净 +58 偏多 11-18 来自：边界 case 拆独立 test (T-EH/T-ES/T-RE 8 个，含 T-EH-3 partial degrade + T-EH-3b full fallback 拆) + escape attack-surface 单测加入 (T3 reasoning/decision escape 2 个) + T-EX-* 多 1 个 + T-FO-3 UsageLimitExceeded 端到端 console.print + T-INT-11/12 ctx.final_text SoT 验证 (P1 reviewer 补) + T3 helper 拆细 (T-RH-4/T-RR-5 等)
- [x] AC20 全 suite < 3s
- [x] AC21-AC26 schema/_extract/parser/Alembic/SessionConsole/TokenBudget 不动
- [x] AC27 单 PR
- [x] AC28 净改动 ~XXX 行 (待 git diff stats)
- [x] AC29 docs commit 先于 impl
- [x] AC30a/b 真实 sim cycle + mock 验证

## Test plan

- [x] uv run pytest -q (988 → ~1046 PASS, 净 +58)
- [x] uv run python main.py --debug (1 cycle 视觉 verify Header/Reasoning/Action/Decision/Footer 结构)
- [x] forensic + retry-exhausted mock 路径验证 (test_usage_limits.py 全部 PASS)
```

- [ ] **Step 8.6: Final commit (if any cleanup)**

If verification revealed minor issues, fix them with single cleanup commit. Otherwise no extra commit needed (Tasks 1-7 already committed).

---

## Self-Review

### Spec coverage

| Spec section | Covered by Task |
|---|---|
| §3.1 5 段架构 | Task 5 (T-INT-1a/1b) + Task 8 (AC30a sim) |
| §3.2 完整 mockup | Task 5 (T-INT-1b structural fragments) |
| §3.2.1 SCHEDULED 短 mockup | Task 3 (test_render_header_scheduled_no_metadata) |
| §3.3 D1-D16 决议 | Task 8 PR description cross-ref |
| §4.1 Header 段契约 | Task 3 (T-RH-1/2/3 + scheduled + flat) |
| §4.1.3 Trigger 详情 4 分支 + verbatim | Task 3 + Task 6 (T-EH-3 conditional fill 缺字段) |
| §4.1.4 State 5 分支 | Task 3 + Task 6 (T-ES-1/2/3/5) |
| §4.2 Reasoning 段 | Task 3 (T-RR-1/2/3/4 + escape) + Task 6 (T-RE-2 空) |
| §4.2.3 _extract 双 helper | Task 5 (helper add) + Task 6 (T-DG-1) |
| §4.3 Action 段 + ret/args fallback | Task 3 (T-RA-1/2 + missing return) |
| §4.4 Decision 段 + escape | Task 3 (T-RD-1 + escape) |
| §4.5.1 Footer 三行布局 | Task 3 (T-RF-1 + forensic + aborted) |
| §4.5.3 SessionStats class | Task 1 |
| §5.1 数据流 + CycleRenderContext | Task 3 (dataclass) + Task 5 (装填) |
| §5.2 时序遍历算法 | Task 5 (format_cycle_output reframe) |
| §5.3 文件影响清单 | Task 4 (build_services 5-tuple) + Task 7 (docs/comment) |
| §5.4 SessionStats 持久化 lifecycle | Task 4 (build_services 注入) + Task 5 (record_cycle 调用) |
| §6.1 Trigger context 边界 | Task 3 + Task 6 (T-EH) |
| §6.2 State snapshot 边界 | Task 3 + Task 6 (T-ES) |
| §6.3 Reasoning 边界 | Task 3 + Task 6 (T-RE) |
| §6.4 forensic 路径 | Task 5 (forensic ctx + render + record) + Task 5 test (T-INT-4) |
| §6.5 retry-exhausted 路径 + DB write | Task 6 |
| §6.6 时序边界 (跨日) | Task 5 (T-INT-8/9) |
| §6.7 Tool calls 边界 | Task 3 (T-RA missing return) |
| §7.1 Helper 单测 11 | Task 3 |
| §7.2 集成 11 | Task 5 |
| §7.3 Drift guards 3 | Task 6 |
| §7.4 Mock fidelity (builder) | Task 2 |
| §8 Out-of-scope | not implemented (correctly) |
| §9 Pre-impl smoke | Verified pre-spec, not redone |
| §10 AC1-31 | All tasks + Task 8 final smoke |

### Placeholder scan

Searched plan for red flags:
- ✅ No "TBD" / "TODO" / "implement later" placeholders
- ✅ No "add appropriate error handling" / "handle edge cases" without specifics
- ✅ Every Step shows full code (no "similar to Task N")
- ✅ Every test step has full test code
- ✅ Every implementation step has full impl code
- ✅ Every command has expected output described
- ✅ All file paths are exact

### Type consistency

Cross-checked type/method signatures across tasks:

- ✅ `SessionStats` Task 1 attrs (`cycle_count` / `total_tokens` / `avg_tokens_per_cycle` / `last_cycle_ended_at` / `record_cycle`) match Task 3 footer test usage + Task 4 dummy stub + Task 5 ctx field type + Task 6 retry-exhausted invocation
- ✅ `CycleRenderContext` Task 3 fields (12: cycle_id/trigger_type/trigger_context/state_snapshot/messages/final_text/cycle_tokens/stats/cache_hit_rate/cycle_started_at/cycle_ended_at/forensic_reason) match Task 5 装填 + Task 5 test `_make_ctx` helper
- ✅ `_render_header` signature (cycle_id, trigger_type, trigger_context, state_snapshot, cycle_started_at, stats) consistent across Task 3 impl + Task 3 tests + Task 5 caller
- ✅ `_render_reasoning(thinking_text, max_chars=800)` signature consistent
- ✅ `_render_action(tool_calls, returns_lookup, cycle_id)` 3-arg signature consistent across Task 3 + Task 5
- ✅ `_render_decision(text)` 1-arg signature consistent
- ✅ `_render_footer(ctx)` takes full ctx, not split fields
- ✅ `format_cycle_output(ctx)` single-arg new signature consistent across Task 5 impl + Task 5/6 tests
- ✅ `build_services` 5-tuple `(exchange, deps, agent, budget, stats)` order consistent across Task 4 impl + 3 test files (test_wizard.py / test_n3_wiring.py / test_okx_algo_normalization.py mock chain)
- ✅ `run_agent_cycle(stats=None)` default consistent (Task 4 add) — uses `_DUMMY_STATS` module-level singleton
- ✅ `_extract_reasoning_per_response(messages) -> list[str | None]` Task 5 add + Task 6 T-DG-1 usage
- ✅ `forensic_reason` string format `"usage_limit_exceeded"` | `"aborted: <class>: <msg>"` consistent across spec §5.2 / Task 5 forensic / Task 6 retry-exhausted
- ✅ `execution_status="retry_exhausted"` enum value consistent across Task 6 DB write + Task 7 doc + comment

### Commit ordering (matches feedback_plan_doc_commit_first)

Plan commit (this file) **先于** Tasks 1-8 各 impl commit。Iter 起手 user 审完本 plan 后：

1. 第 1 commit: spec docs (already done — `9e12460`)
2. 第 2 commit: plan docs (this file, pending)
3. 第 3 commit: T1 SessionStats
4. 第 4 commit: T2 cycle_fixtures
5. 第 5 commit: T3 render helpers + dataclass
6. 第 6 commit: T4 build_services 5-tuple
7. 第 7 commit: T5 format_cycle_output(ctx) reframe + 装填
8. 第 8 commit: T6 retry-exhausted forensic + drift guards
9. 第 9 commit: T7 docs + comment
10. (optional) 第 10 commit: T8 cleanup

### 议题 cross-refs (新会话起手参考)

- spec: `docs/superpowers/specs/2026-05-02-iter-w2r2-8a-cycle-log-narrative-redesign-design.md` (1261 行 v8 self-review)
- W2 prep memory: `project_w2_prep_progress` 起手指引段 8
- 后续议题: `project_r2_8c_tool_output_optimization` (R2-8a landed 后立即启)
- 影响 candidate: N12a (agent.iter() 重构) / N12b (R2-7 schema 升级保留时序) / N12c (thinking 截断 data-driven)
