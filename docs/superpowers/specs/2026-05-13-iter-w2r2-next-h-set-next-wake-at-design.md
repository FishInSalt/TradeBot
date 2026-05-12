# Iter W2-R2-Next-H — `set_next_wake_at` 绝对时间唤醒工具 + `set_next_wake` clamp→reject

**Date**: 2026-05-13
**Branch**: `feature/iter-w2r2-next-h-set-next-wake-at`
**Source**: `.working/sim8-w2-tool-optimization-roadmap.md` §6.1 Iter 3 (R2-Next-H) + sim #8 实证重做（本会话）
**前置依赖**: 无（独立 PR，与 alert-family iter 代码区域不重叠）
**预估工作量**: 4-6 小时（spec / plan / impl / TDD / review / merge）

---

## 1. 背景与动机

### 1.1 议题来源

承接 sim #8 W2 观察期数据分析（`.working/sim8-w2-tool-optimization-roadmap.md` §3.5.2 V3 `set_next_wake` 深 dive）。Roadmap 初判 §6.1 中本议题归入 Iter 3 (R2-Next-H)，前置条件 "起 spec 前先 brainstorm A/B/C 选型"。

本 spec session 重做 sim #8 ergonomic 分析（principle "实证优先于直觉"），结果与 roadmap §3.5.2 初判呈 14×-19× 偏差，故 brainstorm 由 A/B/C 三方向 pivot 到新方向 E。

### 1.2 Sim #8 实证（DB-verified）

数据源：
- DB: `data/tradebot.db` SID `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3`
- Session log: `logs/session_8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3.log` (72980 行)
- 19.2h / 178 cycles / 150 `set_next_wake` calls

**总览**：

| 维度 | 值 |
|---|---|
| Total calls | 150 / 100% ok / 0 fail |
| Same-cycle 多调 | 148 单调 + 1 双调 |
| Clamp 触发（minutes 越界） | 0 次（max 实际 40min < cap 60min）|
| duration_ms | avg 3.64 / max 24（in-memory schedule）|

**minutes 入参分布**：

| Bucket | calls | 累计 % | 典型语义 |
|---|---|---|---|
| 1-3 min | 28 | 19% | "next 1m close" / "5m close in ~2 min" |
| 5 min | 58 | **39%** | monitor default + "5m close in ~3 min" + "fill expected" |
| 8-15 min | 45 | 30% | "1h candle closes at XX UTC (~30 min). Wake at ~12:42..." |
| 20-40 min | 19 | 13% | 全部显式 UTC 时间锚（1h close / ADP 等 macro event）|

**`reasoning` 字段 narrative 模式**（trade_actions 表 grep）：

| Pattern | 命中 | % of 150 |
|---|---|---|
| 含具体 UTC 时间戳（"HH:MM UTC" / "HH:MM"）| 117 | **78%** |
| 含 "candle close" / "align" | 86 | **57%** |
| 含 macro event (ADP/CPI/FOMC) | 11 | 7% |
| 含 funding settlement（真对齐） | 0 | 0% |

实证 reasoning 样本（节选）：
- `"interval=12min | 1h candle closes at 11:00 UTC (~33 min). Wake at 10:37 to assess the candle's progress heading into close, and adjust if needed."`
- `"interval=25min | Align with 14:00 UTC 1h close — the structural confirmation candle for the double-bottom thesis."`
- `"interval=40min | ADP Non-Farm at 12:15 UTC (~48 min). Wake at ~11:56 to reassess position and risk ahead of macro event."`
- `"interval=15min | Catch the 10:00 1h candle close at 11:00 UTC and monitor price action..."`

### 1.3 Ergonomic 痛点 root cause

**Agent 心智锚 = 绝对未来时刻；工具锚 = 相对分钟。**

每次调用 agent 执行双向换算：
1. 看 perception 工具输出的 `fetch_ts UTC`（`tools_perception.py` line 97 / 289 / 869 / 984 / 1648 共 5 处 header 渲染 `@ HH:MM:SS UTC`，已透传 current UTC time）
2. 算 "target wake moment" (HH:MM UTC) → `target - now = delta minutes`
3. 调 `set_next_wake(minutes=delta)`
4. Reasoning 中再写一次 "Wake at HH:MM" 反推作 forensic context

这是 principle 2（工具服务 agent 心智路径）的典型触发信号：工具未表达 agent 原生心智，强迫 agent 在 reasoning 中显式做格式换算。

**前置依赖**：本议题假设 perception 工具继续暴露 `fetch_ts UTC`——agent 必须知道当前 UTC 时间才能选择 HH:MM target。若未来 perception 输出格式变化（删 `fetch_ts` 或改时区），本工具需要重新评估。

### 1.4 Roadmap §3.5.2 与实证偏差校准

| Roadmap 初判 | 本 spec 实证 | 偏差 |
|---|---|---|
| "至少 6 处手算痕迹" | 117 处 UTC 锚 / 86 处 candle close 锚 | 14×-19× 低估 |
| "funding settlement 对齐" | 0 实例（grep 命中均为 "candle settle" / "market settle"）| 推测错误 |
| "macro event ADP/FOMC" | 7-8 处 ADP 实证 | 量级符合 |
| 1h candle close 主因 | ✓ 验证 | 一致 |

### 1.5 Roadmap §3.5.2 A/B/C 方向重审

| 方向 | 重审结论 |
|---|---|
| A `set_next_wake(target: datetime)` | 哲学合理（匹配 agent 心智），但与 monitor pattern (38% 5min) 张力大；与"替换 minutes"是 breaking change 议题 |
| B 新工具 `set_next_wake_at_event(event_type, offset_minutes)` enum | **拒绝**。1h/5m/15m/1m candle close 全要枚举；macro event 需 enum + offset 复杂度；把 agent 决策"对齐哪个事件"硬编码进 enum，违反 principle 8 |
| C 维持现状 | **拒绝**。78% reasoning 含 UTC 时间锚，agent 手算心智负担 + 出错风险足以触发 |
| **E** 新增独立工具 `set_next_wake_at(target_time)` 与 `set_next_wake(minutes)` 并列 | **选定**。见 §1.6 |

### 1.6 brainstorm 决议

- **D1 (方向选 E)**：新增独立工具 `set_next_wake_at`，与 `set_next_wake` 并列。不混入双入参互斥到单工具。工具名做语义 routing；docstring 各自单一职责；与现有 `set_*` 单职责家族风格一致（set_stop_loss / set_take_profit / set_price_alert / set_next_wake / adjust_leverage）。
- **D2 (target_time 格式 = "HH:MM" UTC 简略)**：agent reasoning 原生表达 100% 用 HH:MM 形式（0 次自发写日期）。系统隐式 future inference："今天若 HH:MM 仍在未来则今天，否则明天"。

  Reproducible 验证（macOS 系统 sqlite3 默认无 REGEXP，用 GLOB）：

  ```bash
  # 0 次 reasoning 含 "YYYY-MM-DD" 日期格式（150 calls, sim #8）
  sqlite3 data/tradebot.db "SELECT COUNT(*) FROM trade_actions \
    WHERE session_id='8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3' \
    AND action='set_next_wake' \
    AND reasoning GLOB '*20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]*';"
  # → 0
  ```
- **D3 (输出消息回填完整日期)**：success / reject 消息显式写 `2026-05-12 10:37 UTC` 完整日期。让 agent **看到** 系统解释结果，下个 cycle 立即可见误解。
- **D4 (F2 clamp → reject 顺手统一)**：set_next_wake_at 走 explicit reject（principle 6 + `feedback_observation_period_soft_constraint` §2），且**顺手** align 老工具 set_next_wake，将 silent clamp 改为 explicit reject。两工具失败语义一致。
- **D5 (P1 reject 消息 fact-only)**：reject 不引导切回另一工具，只写 reject 理由 + 越界数字。Agent 看 docstring 自决。
- **D6 (L3 Layer 1 抽象化)**：persona.py Layer 1 "Wake interval control" bullet 改抽象表达，不点名具体工具签名。工具描述由 docstring (pydantic-ai/griffe sniff) 自承。与 Iter 4 (PR #25) "DRY 反转" pattern 一致。
- **D7 (delta 取整用 `math.ceil`)**：delta_seconds → delta_minutes 用 `math.ceil(delta_seconds / 60)`。论据：
  1. **Use case 语义**：candle-close 对齐宁愿"晚 30s"（candle 已 close 可 assess）而非"早 30s"（candle 还在 forming）—— ceil 保证不会早唤醒
  2. **边界 case 不反直觉**：agent 写 "target 30s 后" 用 ceil → 唤醒在 ~1 min，匹配 agent 心智；用 round → 0 min reject，反直觉
  3. **副作用**：default `wake_min_minutes=1` 下，below wake_min reject 几乎不可达（仅在 `wake_min>1` 配置下保留）；reject case set 更简单
  4. 早期方案选 `round()` 的论据 "Floor 早 / Ceil 延 / Round 中间" 在 wake_min 边界 case 失效（30s 被 round 成 0 → reject）—— supersede 早期决议
- **D8 (cross-day 单一规则)**：candidate ≤ now → +1 day。无 timezone offset / DST 复杂度（全 UTC）。
- **D9 (R2-W2-5 D8 supersede)**：本议题 D4 让 R2-W2-5 spec §D8 "clamp 反馈不增强" 决议 obsolete——reject 模式下 "clamp 消息" 概念本身消失（不只是消息内容变化，而是 failure-semantic 范式切换）。R2-W2-5 设计于 `feedback_observation_period_soft_constraint` §2 落地（PR #30, 2026-04-30）前夕，未对齐 explicit reject 原则；本议题 W2 数据驱动后校准。
- **D10 (不动 RuntimeConfig schema / cli/app.py)**：`wake_min_minutes` / `wake_max_minutes` 沿用现有定义；`cli/app.py` `max_wake = min(max(4*scheduler_interval_min, 60), 180)` 公式不动。
- **D11 (不动 perception 工具)**：`get_market_data` / `get_higher_timeframe_view` 输出 header 已带 `fetch_ts UTC`，agent 能 derive current time。无需额外补强 perception 层。
- **D12 (不注入 clock_fn 到 deps)**：内部直接 `datetime.now(timezone.utc)`，与既有 `tools_perception.py` 5 处 pattern 一致（line 97 / 289 / 869 / 984 / 1648）。Test 用 `monkeypatch.setattr(mod, "datetime", FakeDateTime)` 模式（与 `tests/test_av_time_of_day_cache.py:16` 一致；codebase 0 freezegun 引用，不引入新 dep）。

---

## 2. 工具签名 + docstring

### 2.1 `set_next_wake_at` (新工具)

```python
async def set_next_wake_at(
    deps: TradingDeps,
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
```

### 2.2 `set_next_wake` (docstring 修订)

```python
async def set_next_wake(
    deps: TradingDeps,
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
```

### 2.3 trader.py @tool 注册（紧邻 set_next_wake）

```python
@tool
async def set_next_wake_at(
    ctx: RunContext[TradingDeps],
    target_time: str,
    reasoning: str,
) -> str:
    """[docstring per §2.1]"""
    from src.agent.tools_execution import set_next_wake_at as _impl
    return await _impl(ctx.deps, target_time, reasoning=reasoning)
```

`REGISTERED_TOOL_NAMES` 新增 `"set_next_wake_at"` 字符串。

---

## 3. 解析算法 + 边界 case 矩阵

### 3.1 解析 + 验证算法

```python
# imports: import math, re; from datetime import datetime, timezone, timedelta

async def set_next_wake_at(
    deps: TradingDeps,
    target_time: str,
    reasoning: str,
) -> str:
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

### 3.2 边界 case 矩阵

| now (UTC) | target_time | resolved candidate | delta (`ceil(delta_sec/60)`) | 结果 |
|---|---|---|---|---|
| 10:23:00 | "10:37" | today 10:37:00 | 14 min (ceil 14.0) | ✓ ok |
| 10:23:15 | "10:37" | today 10:37:00 | 14 min (ceil 13.75) | ✓ ok |
| 10:23:35 | "10:37" | today 10:37:00 | 14 min (ceil 13.42) | ✓ ok |
| 10:23:59 | "10:37" | today 10:37:00 | 14 min (ceil 13.02) | ✓ ok |
| 23:50:00 | "00:37" | **tomorrow** 00:37:00 | 47 min | ✓ ok (跨日) |
| 10:23:00 | "10:24" | today 10:24:00 | 1 min (ceil 1.0) | ✓ ok |
| 10:23:30 | "10:24" | today 10:24:00 | 1 min (ceil 0.5) | ✓ ok（ceil 后不 reject）|
| 10:23:59 | "10:24" | today 10:24:00 | 1 min (ceil 0.017) | ✓ ok |
| 10:23:00 | "12:00" | today 12:00:00 | 97 min | ✗ reject "exceeds wake_max" |
| 10:23:00 | "10:23" | tomorrow 10:23 | 1440 min | ✗ reject "exceeds wake_max"（same-minute → +1 day → 超界）|
| 10:23:00 | "10:00" | tomorrow 10:00 | 1417 min | ✗ reject "exceeds wake_max" |
| 10:23:00 | "foo" | — | — | ✗ reject "invalid format" |
| 10:23:00 | "25:00" | — | — | ✗ reject "invalid format" (regex 拒绝 hour=25) |
| 10:23:00 | "10:60" | — | — | ✗ reject "invalid format" (regex 拒绝 minute=60) |
| 10:23:00 | "10" / "10:37:00" / "" | — | — | ✗ reject "invalid format" |

**below wake_min reject** (`wake_min=1` 配置下) 几乎不可达——`candidate > now`（algorithm step 2 保证）→ `delta_sec ≥ 1` → `ceil(1/60) = 1` → ≥ `wake_min`。仅在 `wake_min > 1` 自定义配置下保留 reject path。

### 3.3 老工具 `set_next_wake` clamp → reject 边界对照

| minutes 入参 | 现状 (clamp) | 新行为 (reject) |
|---|---|---|
| 0 | clamp 到 1 | reject "below wake_min=1 min" |
| -5 | clamp 到 1 | reject "below wake_min=1 min" |
| 1 ≤ x ≤ 60 | ok | ok (无变化) |
| 60 | ok | ok (边界) |
| 61 | clamp 到 60 | reject "exceeds wake_max=60 min" |
| 90 | clamp 到 60 | reject "exceeds wake_max=60 min" |
| 999 | clamp 到 60 | reject "exceeds wake_max=60 min" |

---

## 4. 输出消息 + trade_actions 落库

### 4.1 Success 消息样本

```
# set_next_wake
Next wake set to 15 min. Reason: consolidation phase, check in 15 min.

# set_next_wake_at
Next wake set for 2026-05-12 10:37 UTC (in 14 min). Reason: align with 1h candle close at 11:00 UTC.
```

### 4.2 Reject 消息样本（fact-only，无切工具引导）

```
# set_next_wake_at 格式无效
Invalid target_time format: 'foo'. Expected 'HH:MM' UTC with 2-digit hour and minute (e.g., '10:37' or '03:05').

# set_next_wake_at 超 wake_max
Cannot wake at 12:00 UTC: nearest future 2026-05-12 12:00 UTC (in 97 min) exceeds wake_max=60 min for this session.

# set_next_wake_at 低于 wake_min（仅 wake_min>1 配置下可达；e.g. wake_min_minutes=2, now=10:23:30, target="10:24" → delta_sec=30 → ceil=1）
Cannot wake at 10:24 UTC: nearest future 2026-05-12 10:24 UTC (in 1 min) below wake_min=2 min.

# set_next_wake clamp → reject 超 wake_max
Cannot set wake to 90 min: exceeds wake_max=60 min for this session.

# set_next_wake clamp → reject 低于 wake_min
Cannot set wake to 0 min: below wake_min=1 min.
```

### 4.3 trade_actions 表 reasoning 落库格式

| Action | reasoning prefix 格式 |
|---|---|
| `set_next_wake` (success) | `interval={minutes}min \| {agent_reasoning}` |
| `set_next_wake_at` (success) | `target={target_time} UTC resolves_to={YYYY-MM-DD HH:MM} UTC interval={delta_minutes}min \| {agent_reasoning}` |

落库示例：
```
target=10:37 UTC resolves_to=2026-05-12 10:37 UTC interval=14min | align with 1h candle close at 11:00 UTC.
```

Reject path **不写** `trade_actions`（与现状 `_record_action` 仅 success 调用一致）。

### 4.4 tool_calls.args 字段

`ToolCallRecorder` 已 strip `reasoning`（`src/services/tool_call_recorder.py:138`）。set_next_wake_at 调用记录到 `tool_calls.args` 仅含 `{"target_time": "10:37"}`，与 set_next_wake 的 `{"minutes": 15}` 风格一致。无需改动 recorder。

---

## 5. persona.py Layer 1 改造（L3 抽象）

### 5.1 Before / After

**Before** (`src/agent/persona.py:92`):
```
- **Wake interval control**: `set_next_wake(minutes)` requests the next scheduler
  wake-up when no external trigger fires. Valid range 1-{runtime.wake_max_minutes}
  min for this session. Alerts, fills, and conditional triggers always interrupt
  sleep regardless of this setting.
```

**After (L3)**:
```
- **Wake interval control**: scheduled wake-up applies only when no external
  trigger fires; alerts, fills, and conditional triggers always interrupt
  sleep. Allowed range: next 1-{runtime.wake_max_minutes} min from now for
  this session.
```

### 5.2 信息分工

| 信息 | 落点 | 理由 |
|---|---|---|
| 工具名 / 入参格式 / call→output 示例 | docstring | pydantic-ai/griffe 自动 sniff 传 LLM；DRY 反转 (Iter 4) |
| `wake_max_minutes` 具体数值 (e.g. 60) | Layer 1 | session-aware runtime value，docstring 是 static text 无法注入 |
| `wake_min_minutes` / `wake_max_minutes` **名称** | docstring | 用作 reject message 中的 bound name |
| Cross-tool behavior (alerts interrupt 等) | Layer 1 | 跨工具共享约束，单一权威来源 |

---

## 6. 测试矩阵

### 6.1 Group T1 — `set_next_wake_at` 新工具

| ID | Case | 验证 |
|---|---|---|
| T1.1 | happy path: now=10:23:00, target="10:37" | ok + delta=14 + `set_next_wake_fn(14)` called + `trade_actions` row written |
| T1.2 | cross-day: now=23:50, target="00:37" | resolves to tomorrow 00:37 + delta=47 + ok |
| T1.3a | format invalid: "foo" / "25:00" / "10:60" / "10" / "10:37:00" / "" | reject + 精确 message |
| T1.3b | format edge: "00:00" / "23:59" | regex 接受（边界）|
| T1.4 | exceeds wake_max: target=now+97min | reject "exceeds wake_max=60 min" |
| T1.5 | ceil 边界: now=10:23:30, target="10:24" (delta_sec=30) | ok + delta=1 min + `set_next_wake_fn(1)` called（验证 ceil，不再 reject）|
| T1.6 | past resolves to tomorrow > wake_max: target=now's same HH:MM | reject "nearest future ... (in 1440 min) exceeds wake_max" |
| T1.7 | `deps.set_next_wake_fn=None` | "Dynamic wake not available" (与 set_next_wake 一致) |
| T1.8 | ceil drift guard: now=10:23:01, target="10:24" (delta_sec=59) | ok + delta=1 min（验证 `math.ceil` 实现；防回归到 `round` / `int()`）|
| T1.8b | ceil drift guard: now=10:23:00, target="10:25" (delta_sec=120) | ok + delta=2 min（整分钟边界不漂）|
| T1.8c | below wake_min reject（仅 `wake_min=2` 配置触发）: now=10:23:30, target="10:24", `wake_min_minutes=2` | reject "below wake_min=2 min" |
| T1.9 | `trade_actions` row reasoning prefix | `"target=10:37 UTC resolves_to=2026-05-12 10:37 UTC interval=14min \| ..."` |
| T1.10 | reject path 不写 `trade_actions` | 验证 0 row |

### 6.2 Group T2 — `set_next_wake` clamp → reject 改造

| ID | Case | 验证 |
|---|---|---|
| T2.1 | minutes=90 | reject "exceeds wake_max=60 min for this session" |
| T2.2 | minutes=0 / -5 | reject "below wake_min=1 min" |
| T2.3 | minutes=15 in range | ok 不变（无回归）|
| T2.4 | minutes=61 边界 | reject (61 = wake_max+1) |
| T2.5 | minutes=60 边界 | ok |
| T2.6 | 移除 R2-W2-5 引入的 "clamped from X" 输出分支 | success path 仅一条消息 |

### 6.3 Group T3 — Layer 1 / persona

| ID | Case | 验证 |
|---|---|---|
| T3.1 | `generate_system_prompt` 含改造后 bullet | "scheduled wake-up applies only when..." 文本 |
| T3.2 | drift sentinel: not contains `set_next_wake(minutes)` literal **and** not contains `set_next_wake_at(target_time)` literal | 防 L3 抽象退化（两工具签名都不应渲染进 Layer 1）|
| T3.3 | `wake_max=60` 渲染到 bullet | "1-60 min" 文本 |

### 6.4 Group T4 — Integration

| ID | Case | 验证 |
|---|---|---|
| T4.1 | `REGISTERED_TOOL_NAMES` 含 `"set_next_wake_at"` | 既有 `test_registered_tool_names_matches_agent_tools` 自动覆盖 |
| T4.2 | trader.py `@tool` 注册可被 pydantic-ai 发现 | agent_tools schema 含新工具 |

### 6.5 既有测试清理（必改 8 处 + 1 加 helper + 兜底 grep）

下列 8 处既有测试将在本议题改造后失败，必须显式改造（不只 grep 兜底）：

| Test | 位置 | 失败原因 | 改造方向 |
|---|---|---|---|
| `test_layer1_contains_wake_interval_control_bullet` (R2-5 G8) | `tests/test_persona.py:312-321` | 断言 `"Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting"` 字面；§5.1 After 删除 "regardless of this setting" 尾缀 | 改断言为 "alerts, fills, and conditional triggers always interrupt sleep" (匹配新 wording) |
| `test_layer1_renders_dynamic_wake_max` (R2-5 G11) | `tests/test_persona.py:324-336` | 断言 `"1-120 min for this session"` / `"1-60 min for this session"` 字面；§5.1 After 改为 `"1-X min from now for this session"` | 更新两处字面到新 wording |
| `test_generate_system_prompt_default_runtime` (R2-5 G9) | `tests/test_persona.py:339-349` | 断言 `"1-60 min for this session"` 字面（line 348）| 同上更新 |
| `test_set_next_wake_no_decision_hints_in_description` (R2-5 G10) | `tests/test_persona.py:352-380` | line 379-380 断言 `"one-shot" in desc.lower()`；§2.2 新 docstring 删除了 "one-shot" 关键词 | 删除该 sanity 断言（R2-W2-5 G10 主体 N5 wordlist 验证保留） |
| `test_set_next_wake_wrapper_layer1_reference_intact` (R2-5 PR #34 I-1) | `tests/test_persona.py:383-409` | 断言 wrapper Args.minutes.description 含 `"Wake interval control"` literal 引 Layer 1 bullet 名；§2.2 新 docstring 不引 Layer 1 bullet（L3 抽象解耦）| 删除整个测试（SSOT 不变量在 L3 抽象后不再成立；改 docstring 各自承载 cross-tool fact） |
| `test_set_next_wake_clamps_to_max` | `tests/test_tools.py:344-354` | 断言 `"clamped" in result.lower()` + `set_next_wake_fn.assert_called_once_with(60)`；改 reject 后两断言均不成立 | 改为 `test_set_next_wake_rejects_above_max`：断言 reject message + `set_next_wake_fn` 不被 call |
| `test_set_next_wake_clamps_to_min` | `tests/test_tools.py:356-365` | 断言 `"clamped" in result.lower()` + `set_next_wake_fn.assert_called_once_with(1)`；改 reject 后两断言均不成立 | 改为 `test_set_next_wake_rejects_below_min`：断言 reject message + `set_next_wake_fn` 不被 call |
| `test_execution_tool_fact_only` parametrize 缺新工具 | `tests/test_fact_only_wordlist.py:621-633` | parametrize 列表覆盖全 execution tools，新增 `set_next_wake_at` 未列入 | 在 parametrize list 加 `"_invoke_set_next_wake_at"`；在文件中加 helper（早返回路径 `set_next_wake_fn=None` 即可）|

**兜底 grep**（impl 期）：

```bash
# Clamp 行为残留 + R2-W2-5 G10 "one-shot" 断言 + "regardless of this setting" 断言
grep -rn "clamped\|one-shot\|regardless of this setting" tests/ src/

# Layer 1 wording drift
grep -rn "min for this session" tests/ src/

# 文件名 (注意是 tests/test_tools.py 不是 test_tools_execution.py)
ls tests/test_tools.py tests/test_persona.py tests/test_fact_only_wordlist.py
```

### 6.6 `src/cli/display.py` 3 处改造（execution 工具契约）

`src/agent/tools_execution.py:14-16` 显式契约 NOTE:
> Return string prefixes are used by `src/cli/display.py` (`_EXECUTION_SUCCESS_PREFIXES`) to detect success vs business rejection. If you change a return string's prefix, update `_EXECUTION_SUCCESS_PREFIXES` in `display.py` accordingly.

新工具 `set_next_wake_at` success message 以 `"Next wake set for ..."` 起头（与 `set_next_wake` 的 `"Next wake set to ..."` 不同 prefix），不加 entry → `is_tool_error()` 会把成功调用判为 error。

| 位置 | 改造 |
|---|---|
| `src/cli/display.py:252` 区域 `_EXECUTION_PARSERS` dict | 新增 `_summarize_set_next_wake_at` summarizer + 注册条目 `"set_next_wake_at": _summarize_set_next_wake_at`（实现可复用 `_summarize_set_next_wake` 的 `\d+\s*min` regex；或单独 parse `"in N min"` 段）|
| `src/cli/display.py:266` 区域 `_EXECUTION_SUCCESS_PREFIXES` dict | 新增条目 `"set_next_wake_at": "Next wake set for"` |
| `src/cli/display.py:492` 区域 `_EXECUTION_TOOL_NAMES` frozenset | 加 `"set_next_wake_at"` |

测试覆盖（加入 §6.4 Integration）:
- T4.3 `is_tool_error()` for set_next_wake_at success message → False
- T4.4 `is_tool_error()` for set_next_wake_at reject message (3 类) → True
- T4.5 `_summarize_set_next_wake_at` 解析 `"Next wake set for 2026-05-12 10:37 UTC (in 14 min). Reason: ..."` → `"14min"`

### 6.7 `scripts/_sim_metrics.py:586` 分类改造

```python
# Before:
if actions == {"set_next_wake"}:
    dist["hold (wake-only)"] = dist.get("hold (wake-only)", 0) + 1
    continue

# After (set_next_wake_at 同样计入 wake-only):
if actions <= {"set_next_wake", "set_next_wake_at"} and actions:
    dist["hold (wake-only)"] = dist.get("hold (wake-only)", 0) + 1
    continue
```

理由：set_next_wake_at 与 set_next_wake 同属 wake 调度类工具，"agent 只 set 了 wake（无 open/close/SL/alert/etc）" 的语义两者等价。精确集合匹配改 subset 包含逻辑后，W3 cross-sim analytics 不漂。

测试覆盖：T4.6 验证 `actions = {"set_next_wake_at"}` 和 `actions = {"set_next_wake", "set_next_wake_at"}` 均落 `hold (wake-only)` 分类（需用 fixture session 或单元测试 `_classify_active_distribution` 等函数）。

---

## 7. 风险表

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Clamp → reject 让既有 sim session 行为变化（运行中 agent 调 wake=90 现在 reject） | 中 | 低（无 production sim runs；本地 sim 是新启动） | 本 spec §1.6 D9 / §10 显式 supersede R2-W2-5 D8；release note 标 behavior change |
| `wake_min_minutes>1` 自定义配置下 `ceil(delta_sec/60)` 仍可能 < wake_min → reject（default `wake_min=1` 不可达，本风险微小）| 极低 | 低 | 测试 T1.8c 覆盖（`wake_min=2` fixture）|
| Agent 不学新工具 / 仍用 `set_next_wake` 手算分钟 | 中 | 中 | docstring 完整 call→output 示例 + Layer 1 cross-tool 抽象（双管引导）；W3 sim 验证 adoption rate |
| `set_next_wake_at` adoption < 30% W3 → ROI 不及预期 | 中 | 中 | W3 数据触发 follow-up（docstring promo / Layer 1 第二轮强化）；本议题不预设兜底 nudge（principle 8）|
| Future inference 误解（agent 想 today 10:23 但被推到 tomorrow） | 低 | 低 | reject message "nearest future" 暗示已做 inference + 完整日期回填让 agent 看到结果 |
| 既有 clamp 测试遗漏改造 | 中 | 中 | §6.5 enumerate 8 处具体测试 + 1 处 helper 新增 + impl 期 grep `clamped` / `one-shot` / `regardless of this setting` / `min for this session` 兜底 |
| 与 R2-Next-E (alert-family) 并发 PR 在 `tests/test_persona.py` / `src/agent/persona.py` 文件 merge conflict | 低 | 低 | 两议题改 Layer 1 不同 bullet（本议题 line 92 wake interval control；alert-family 主要 line 86-91 alert response），测试函数也不同；先合并先到先得 + rebase 即可 |

---

## 8. Token / cycle 成本估算

- **新工具 schema 注入成本**: ~150 tokens / cycle (docstring + signature) × 178 cycles ≈ **~27k tokens / 19h**（聚合值；单次 cycle ~150 tokens）
- **Layer 1 改造净变**: ≈ 0（删工具签名 ~30 字符 ≈ 加 "applies only when no external trigger fires" ~40 字符）
- **节省（若 50% adoption）**: ~75 cycles 不再手算 → ~4-6k tokens / 19h
- **净 token ROI**: ≈ 持平（schema cost ≈ 节省）
- **真正 ROI**: 心智负担 + 出错率（78% reasoning 不再做 "target→minutes→target" 双向换算）

---

## 9. Scope checklist

| 项 | In-scope |
|---|---|
| 新增 `set_next_wake_at` (execution layer + trader.py @tool + REGISTERED_TOOL_NAMES) | ✅ |
| `set_next_wake` clamp → reject 行为改造 + docstring 修订 | ✅ |
| `persona.py` Layer 1 "Wake interval control" bullet L3 抽象 | ✅ |
| `cli/display.py` 3 处契约改造（_EXECUTION_PARSERS / _EXECUTION_SUCCESS_PREFIXES / _EXECUTION_TOOL_NAMES）| ✅ |
| `scripts/_sim_metrics.py:586` actions 集合改 subset 包含 | ✅ |
| 测试矩阵 T1-T4 ~18-22 cases（含 display.py / sim_metrics 覆盖） | ✅ |
| 既有 clamp 测试清理（**test_tools.py** + test_persona.py + test_fact_only_wordlist.py 共 8 处）| ✅ |

| 项 | Out-of-scope |
|---|---|
| RuntimeConfig schema 变更 | ❌ |
| `cli/app.py` `max_wake` 公式 | ❌ |
| Perception 工具变更（get_market_data fetch_ts 已透传 UTC） | ❌ |
| `ToolCallRecorder` 变更 | ❌ |
| `_record_action` reject-path 落库（沿用 success-only） | ❌ |
| 注入 `clock_fn` 到 `TradingDeps` | ❌ |
| 与 alert-family iter (R2-Next-E) 代码区域重叠 | ❌ |

---

## 10. R2-W2-5 D8 决议 supersede 声明

**R2-W2-5 spec §1.3 D8** (`docs/superpowers/specs/2026-05-01-iter-w2r2-5-set-next-wake-clarity-design.md:83`):
> D8 (clamp 反馈不增强): Layer 1 已暴露精确 bound，无需 clamp 消息再次显示 session range；保持现有简洁版。

**本 spec §1.6 D9 supersede**：本议题 D4 让 R2-W2-5 D8 obsolete——D8 议题前提是 "clamp 模式下 clamp 消息要不要再显示 session range"，而 D4 将 failure semantic 由 clamp 切到 reject，"clamp 消息"概念本身消失（不只是消息内容变化，而是 failure-semantic 范式切换）。

R2-W2-5 设计于 `feedback_observation_period_soft_constraint` §2 落地（PR #30, 2026-04-30）前夕，未对齐 "执行类优先 explicit reject 而非 silent clamp" 原则。本议题 W2 数据驱动 + principle 6 落地后校准。

R2-W2-5 D8 状态：**superseded by R2-Next-H D4**。

---

## 11. 与其他议题边界

| 议题 | 边界 |
|---|---|
| R2-W2-5 (set_next_wake clarity, landed PR) | 本议题 supersede 其 D8 clamp 决议；保留其 D5/D6 (session-aware bound / RuntimeConfig 抽象) |
| R2-Next-D Multi-TF (landed PR #46) | 无代码区域重叠 |
| R2-Next-E Alert family (其他会话 in-progress) | 无代码区域重叠（tools_execution.py 不同 function；persona.py Layer 1 改造区不与 alert bullets 重合）|
| R2-Next-G OI 变化率 (Iter 4) | 无重叠 |
| R2-Next-I evaluate_trade_setup (Iter 5 DEFER) | 无重叠 |
| Iter 4 (PR #25) DRY 反转 pattern | 本议题 D6 L3 抽象继承该 pattern |
| `feedback_observation_period_soft_constraint` (memory) | 本议题 D4 (F2) 落实其 §2 "执行类 explicit reject" 原则 |
| `project_tool_design_principles` (memory) | 本议题应用 principle 1/2/6/7/8 |

---

## 12. 后续 follow-up（不进本 PR）

- W3 sim 验证 `set_next_wake_at` adoption rate（启动条件：W3 ≥1 个 sim 完成）
- 若 W3 adoption < 30% → 启动 follow-up（docstring promo 强化 / Layer 1 第二轮 nudge）
- 若 W3 暴露 event-driven 真实场景（funding settlement 实际对齐 ≥3 处 / macro event 对齐扩展）→ 重启 roadmap §3.5.2 方向 B 讨论
- 若 agent 在 W3 中表现出 timezone 混淆（写 "13:00" 但其实想 local time）→ 启动 timezone 显式注入议题
