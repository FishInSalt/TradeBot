# Iter W2-R2-5 — set_next_wake usage clarity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 P0-5 (scheduler 30min 兜底失效) 重新评估为 wontfix - by design，并借此清理 set_next_wake 工具的 N5 决策暗示漏网 + 给 agent 暴露精确 session-aware wake bound。引入 RuntimeConfig dataclass 作为 system prompt 的"session-fixed runtime 输入"语义边界，未来 N10 / scheduler_interval / exchange 等扩展时签名稳定。

**Architecture:**
- 新增 `RuntimeConfig` frozen dataclass（`src/agent/persona.py`），sibling to `PersonaConfig`
- `generate_system_prompt(persona, runtime=None)` + `_build_layer1(runtime)` 渲染动态 1-{wake_max} 范围
- `cli/app.py` **路径 A 跨函数迁移**：`max_wake` 计算 / `RuntimeConfig` 装配 / `deps.wake_min/max_minutes` 全部上移到 `build_services`；`run` 内仅留 `set_next_wake_fn`（依赖 scheduler 实例）
- `set_next_wake` wrapper docstring 删 "Shorten when... lengthen when..." 决策暗示，引用 Layer 1 标签

**Tech Stack:** Python 3.13, pydantic-ai 1.78（pinned, Iter 5）, frozen dataclass, pytest, asyncio

**Spec:** `docs/superpowers/specs/2026-05-01-iter-w2r2-5-set-next-wake-clarity-design.md`

---

## File Structure

| 文件 | 职责 | 改动类型 |
|---|---|---|
| `src/agent/persona.py` | 新增 `RuntimeConfig` dataclass；`_build_layer1(runtime)` + `generate_system_prompt(persona, runtime=None)` 签名升级；Layer 1 加第 6 个 cross-tool bullet "Wake interval control" | 修改 |
| `src/agent/trader.py` | `create_trader_agent(model, persona_config, runtime=None)` 签名升级 + 转传；`set_next_wake` wrapper docstring fact-only 改写 | 修改 |
| `src/cli/app.py` | 路径 A 跨函数装配：`build_services` 内（438 后 / 507 后两处）算 `max_wake` + 构造 `RuntimeConfig` + 装 `deps.wake_min/max_minutes`；`run` 内（614-617）删除重复装配 + 改写 `# R4` 注释 | 修改 |
| `tests/test_persona.py` | 新增 4 个 drift guard 测试（bullet 渲染 / 动态 wake_max / 默认 runtime / N5 漏网检测）；`test_layer1_bullet_count_5` 重命名 + assert 5→6 | 修改 |
| `.working/sim4-issues-inventory.md` | §P0-5 加 wontfix justification 注记（不进 git） | 文档（不进 git） |
| `.working/all-pending-needs.md` | Tier 1 R2-5 状态更新（不进 git） | 文档（不进 git） |
| `~/.claude/.../memory/project_w2_prep_progress.md` | round 2 表 R2-5 行更新（不进 git） | memory（不进 git） |

---

## Task 1: Plan doc commit

**Files:**
- Create: `docs/superpowers/plans/2026-05-01-iter-w2r2-5-set-next-wake-clarity.md` (本文档)

- [ ] **Step 1: User review of plan**

等用户审阅本 plan 文档（feedback_review_before_commit）。如有修改建议则 inline 修复。

- [ ] **Step 2: Commit plan as second R2-5 commit**

```bash
git add docs/superpowers/plans/2026-05-01-iter-w2r2-5-set-next-wake-clarity.md
git commit -m "$(cat <<'EOF'
docs(iter-w2r2-5): add set_next_wake usage clarity implementation plan

Implementation plan for spec d7e6f19. Sequence:
- T2: 3 drift guard tests (TDD red)
- T3: RuntimeConfig + Layer 1 bullet + G12 sync (TDD green)
- T4: create_trader_agent signature + cli/app.py path A migration
- T5: wrapper docstring N5 cleanup + N5 drift guard
- T6: inventory + memory updates (no git)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Verify commit**

```bash
git log --oneline -2
```
Expected: 顶部 2 个 commit 是 `docs(iter-w2r2-5): plan` 和 `docs(iter-w2r2-5): spec`

---

## Task 2: 3 drift guard tests (TDD red)

**Files:**
- Modify: `tests/test_persona.py` (在文件末尾追加 3 个新测试)

**目的**: 在 `RuntimeConfig` 与 Layer 1 第 6 bullet 实现前先写测试，确保 TDD 红→绿节奏。

- [ ] **Step 1: Add `test_layer1_contains_wake_interval_control_bullet`**

在 `tests/test_persona.py` 末尾追加：

```python
def test_layer1_contains_wake_interval_control_bullet():
    """R2-5 G8: Layer 1 含 Wake interval control bullet (cross-tool with alert/fill/conditional)."""
    from src.agent.persona import _build_layer1, RuntimeConfig
    layer1 = _build_layer1(RuntimeConfig())
    # bullet 标题
    assert "**Wake interval control**" in layer1, \
        "Layer 1 missing Wake interval control bullet header"
    # cross-tool 关系断言（真正的 Layer 1 价值，比 bound 重要）
    assert "Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting" in layer1, \
        "Layer 1 Wake interval control bullet missing cross-tool interrupt clause"
```

- [ ] **Step 2: Add `test_layer1_renders_dynamic_wake_max`**

```python
def test_layer1_renders_dynamic_wake_max():
    """R2-5 G11: _build_layer1 渲染 RuntimeConfig.wake_max_minutes 实际值（非 envelope 1-180）。"""
    from src.agent.persona import _build_layer1, RuntimeConfig
    # sim #4 实证值（30min scheduler 配置下的 wake_max）
    layer1_120 = _build_layer1(RuntimeConfig(wake_max_minutes=120))
    assert "1-120 min for this session" in layer1_120, \
        "wake_max=120 not rendered in bullet"
    assert "1-60 min for this session" not in layer1_120, \
        "default 60 leaked when explicit 120 passed"
    # 默认 60（默认 15min scheduler 配置）
    layer1_60 = _build_layer1(RuntimeConfig(wake_max_minutes=60))
    assert "1-60 min for this session" in layer1_60, \
        "wake_max=60 not rendered in bullet"
```

- [ ] **Step 3: Add `test_generate_system_prompt_default_runtime`**

```python
def test_generate_system_prompt_default_runtime():
    """R2-5 G9: generate_system_prompt(persona) 单参等价于显式 RuntimeConfig() 默认值。"""
    from src.agent.persona import generate_system_prompt, RuntimeConfig
    from src.config import PersonaConfig
    prompt_default = generate_system_prompt(PersonaConfig())
    prompt_explicit = generate_system_prompt(PersonaConfig(), RuntimeConfig())
    assert prompt_default == prompt_explicit, \
        "Single-arg call must equal explicit RuntimeConfig() — backwards compat broken"
    # 渲染默认 wake_max=60
    assert "1-60 min for this session" in prompt_default, \
        "Default RuntimeConfig() should render 1-60 min"
```

- [ ] **Step 4: Run new tests to verify they fail**

```bash
uv run pytest tests/test_persona.py::test_layer1_contains_wake_interval_control_bullet tests/test_persona.py::test_layer1_renders_dynamic_wake_max tests/test_persona.py::test_generate_system_prompt_default_runtime -v
```

Expected: 3 tests **FAIL** with `ImportError: cannot import name 'RuntimeConfig' from 'src.agent.persona'`

- [ ] **Step 5: Run full test_persona.py to confirm only the 3 new tests fail**

注：`pytest --ignore-glob` 匹配文件路径而非测试函数名，无法用来排除单个测试。直接跑全套
看 fail 计数即可：

```bash
uv run pytest tests/test_persona.py 2>&1 | tail -5
```

Expected: **29 passed, 3 failed**（3 fails 即新加的 3 个，ImportError on `RuntimeConfig`；
其他 29 个原有测试全 PASS，含 `test_layer1_bullet_count_5` 仍 == 5）

- [ ] **Step 6: Commit (TDD red)**

```bash
git add tests/test_persona.py
git commit -m "$(cat <<'EOF'
test(iter-w2r2-5): T2 add 3 drift guard tests (TDD red)

3 new tests fail with ImportError until T3 lands RuntimeConfig + Layer 1 bullet:
- test_layer1_contains_wake_interval_control_bullet (G8)
- test_layer1_renders_dynamic_wake_max (G11, uses sim #4 实证值 wake_max=120)
- test_generate_system_prompt_default_runtime (G9, backwards-compat for ~28 call sites in test_persona.py per spec §3.6.4)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: RuntimeConfig + Layer 1 bullet + G12 sync (TDD green)

**Files:**
- Modify: `src/agent/persona.py` (整个文件改造 — 加 import + RuntimeConfig + 改 _build_layer1 / generate_system_prompt 签名 + 加第 6 bullet)
- Modify: `tests/test_persona.py:246-258` (G12 sync: rename + assert 5→6)

**目的**: 让 Task 2 的 3 个 red test 转绿，同 commit 修复 G12（test_layer1_bullet_count_5 加第 6 bullet 后必失败）避免中间 fail。

- [ ] **Step 1: Read current persona.py**

```bash
head -32 src/agent/persona.py
```

确认现状：
- `from src.config import PersonaConfig`
- `generate_system_prompt(config: PersonaConfig) -> str:` 单参签名
- `_build_layer1() -> str:` 无参
- Layer 1 含 5 个 cross-tool bullets（Fill timing / Open fill response / Close fill response / Alert response / OCO atomicity on OKX）

- [ ] **Step 2: Rewrite `src/agent/persona.py` (header + RuntimeConfig + signature 升级)**

将 `src/agent/persona.py:1-31` 替换为：

```python
from __future__ import annotations
from dataclasses import dataclass

from src.config import PersonaConfig


@dataclass(frozen=True)
class RuntimeConfig:
    """Session-fixed runtime values injected into the system prompt.

    Sibling to PersonaConfig:
    - PersonaConfig: who I am as a trader (personality, trading style)
    - RuntimeConfig: operational facts about this trading session
      (tool bounds, exchange context, monitoring rhythm, etc.)

    Per-cycle dynamic context (e.g., previous-cycle reasoning, current
    position) is NOT here — that channel is reserved for separate
    mechanisms (R2-8 N10 reasoning injection).

    Field docstrings (PEP 257-extra convention): pyright/Sphinx/griffe
    static tools recognize them, but Python runtime does not bind them
    to __doc__ — inspect.getdoc(RuntimeConfig.wake_max_minutes) returns
    None. If runtime reflection is needed, switch to
    field(metadata={"doc": "..."}).
    """
    wake_max_minutes: int = 60
    """Default 60 matches the cli/app.py formula at scheduler_interval_min=15
    (min(max(4*15, 60), 180) = 60), but the value is **independent** — adjust
    if the formula changes.

    **Production paths MUST set this explicitly** via cli wiring
    (`RuntimeConfig(wake_max_minutes=cli.app.max_wake)`); this default is
    **for tests / temporary call sites only, NOT for production**. If a
    production code path silently relies on the default 60, that is a bug —
    flag and route through cli wiring instead."""


def generate_system_prompt(
    persona: PersonaConfig,
    runtime: RuntimeConfig | None = None,
) -> str:
    """Generate a three-layer system prompt.

    Layer 1: Identity & Tools — who you are, key cross-tool behavior
    Layer 2: Trader Thinking Framework — how to think (generic)
    Layer 3: Strategy Preferences — what style to trade (injection point)

    Args:
        persona: trader identity / style config.
        runtime: session-fixed runtime values (tool bounds, etc).
            Defaults to RuntimeConfig() — only for tests / temp call sites.
    """
    runtime = runtime or RuntimeConfig()
    layer1 = _build_layer1(runtime)
    layer2 = _build_layer2()
    layer3 = _build_layer3(persona)
    return f"{layer1}\n\n{layer2}\n\n{layer3}"


def _build_layer1(runtime: RuntimeConfig) -> str:
    return f"""You are a cryptocurrency trader operating autonomously. You analyze markets, manage positions, and make trading decisions using the tools available to you.

## Market Context

You trade USDT-margined perpetual futures (no expiry date). The exchange uses one-way position mode — you cannot hold long and short positions on the same symbol simultaneously. To reverse direction, close your current position first. Leverage cannot be changed while holding a position. Every trade incurs fees on both entry and exit — frequent small trades can erode capital through friction costs alone.

## Cross-Tool Behavior

- **Fill timing**: After submitting a market order, you will be notified when it fills via a separate trigger. Set stop loss and take profit only after receiving fill confirmation — do not attempt in the same cycle as order submission.
- **Open fill response**: When woken by an order fill notification (conditional trigger) that opened a position, identify your stop loss and take profit levels and set them. Use market data to inform these levels.
- **Close fill response**: When woken by a fill that closed a position (stop loss, take profit, or manual close), review the trade outcome: what worked, what didn't, and what you would do differently. Save actionable lessons to memory.
- **Alert response**: When woken by a price alert, assess whether the price move changes your thesis. For a price level alert, evaluate whether the level held or broke and what that implies. For a volatility alert, determine if the move is the start of a trend or just noise before acting.
- **OCO atomicity on OKX**: stop and take_profit orders that share an algoId (rendered as `[OCO]` in get_open_orders) are atomic — cancelling or triggering one leg removes both. If you intend to replace only one leg, re-create the other leg immediately after.
- **Wake interval control**: `set_next_wake(minutes)` requests the next scheduler wake-up when no external trigger fires. Valid range 1-{runtime.wake_max_minutes} min for this session. Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting."""
```

注：`_build_layer2` / `_build_layer3` / `_STYLE_DESCRIPTIONS` / `_PERSONA_DESCRIPTIONS` 部分**保持不变**（line 33-145）。

- [ ] **Step 3: Update `tests/test_persona.py:246-258` (G12 sync — 同 commit 防中间 fail)**

将 `tests/test_persona.py:246-258` 整段替换为：

```python
def test_layer1_cross_tool_bullet_count():
    """Layer 1 bullet count drift guard.

    Iter 4 PR #25 reduced Layer 1 from 25 to 5 cross-tool bullets.
    R2-5 PR # added 6th bullet "Wake interval control" (set_next_wake
    × alert/fill/conditional triggers). Bullets are markdown rows
    starting with '\\n- **' — matches `_build_layer1`'s format.
    """
    from src.agent.persona import generate_system_prompt
    from src.config import PersonaConfig
    prompt = generate_system_prompt(PersonaConfig())
    layer1 = prompt.split("## How to Think")[0]
    bullet_count = layer1.count("\n- **")
    assert bullet_count == 6, f"Expected 6 Layer 1 bullets, got {bullet_count}"
```

- [ ] **Step 4: Run all 4 affected tests (3 new + 1 G12 sync)**

```bash
uv run pytest tests/test_persona.py::test_layer1_contains_wake_interval_control_bullet tests/test_persona.py::test_layer1_renders_dynamic_wake_max tests/test_persona.py::test_generate_system_prompt_default_runtime tests/test_persona.py::test_layer1_cross_tool_bullet_count -v
```

Expected: 4 PASS

- [ ] **Step 5: Run full test_persona.py suite for regression**

```bash
uv run pytest tests/test_persona.py -v 2>&1 | tail -10
```

Expected: **32 passed**（29 原有 + 3 新 — `test_layer1_bullet_count_5` 已被 G12 sync 替换为 `test_layer1_cross_tool_bullet_count`，总数仍 29 + 3 = 32）

- [ ] **Step 6: Run full project tests for regression**

```bash
uv run pytest 2>&1 | tail -5
```

Expected: baseline 962 passed + 3 skipped = 965 collected → **965 passed + 3 skipped = 968 collected**（+3 from T2 new tests now turning green，+0 from G12 rename — count unchanged，G12 仅 rename + 改 assert 值；T5 +1 后达最终 DoD 的 966 + 3 = 969）

- [ ] **Step 7: Commit (TDD green + G12 sync)**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-5): T3 add RuntimeConfig + Layer 1 Wake interval control bullet

- New RuntimeConfig frozen dataclass: sibling to PersonaConfig,
  session-fixed prompt input boundary (vs per-cycle N10 channel).
  wake_max_minutes default 60 matches default 15-min scheduler config;
  production MUST set explicitly via cli wiring (T4).
- _build_layer1(runtime) and generate_system_prompt(persona, runtime=None):
  signatures upgraded with backwards-compat default. Single-arg calls
  (~28 test sites in test_persona.py per spec §3.6.4) equivalent to
  explicit RuntimeConfig().
- Layer 1 6th cross-tool bullet "Wake interval control":
  Valid range 1-{wake_max} (dynamic per session); cross-tool clause
  "Alerts, fills, and conditional triggers always interrupt sleep
  regardless of this setting" — agents know sleep is interruptible.
- G12 sync: test_layer1_bullet_count_5 → test_layer1_cross_tool_bullet_count,
  assert == 6 (same commit avoids intermediate-state deterministic fail).

Tests: 32 in test_persona.py (was 29 + 3 new), 0 regression.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: create_trader_agent signature + cli/app.py path A migration

**Files:**
- Modify: `src/agent/trader.py:48-69` (create_trader_agent 签名 + 转传)
- Modify: `src/cli/app.py` (顶部 import + build_services 内两处装配 + run 内 4 行删改)

**目的**: 让生产路径在 `build_services` 内显式装配 `RuntimeConfig`，传入 `create_trader_agent`；`deps.wake_min/max_minutes` 同步上移；`run` 内仅留 `set_next_wake_fn`（依赖 scheduler 实例）。

- [ ] **Step 1: Update `src/agent/trader.py:48-69` (create_trader_agent 签名)**

将 `src/agent/trader.py:48-69`（`def create_trader_agent` 函数体）改造：

```python
def create_trader_agent(
    model: str,
    persona_config: PersonaConfig,
    runtime: RuntimeConfig | None = None,
) -> Agent[TradingDeps, str]:
    # 函数级懒加载 — 与现有 26 个 tool 的懒加载风格一致（技术上非必需：
    # recorder 侧 TYPE_CHECKING + 字符串前向引用已足以破环）
    from src.services.tool_call_recorder import ToolCallRecorder
    from src.services.model_manager import get_optimal_settings

    system_prompt = generate_system_prompt(persona_config, runtime)
    # ... 余下 line 56+ 不变（model_settings / Agent 创建等）
```

同时确认 `src/agent/trader.py` 顶部已 import `RuntimeConfig`：

```bash
grep -n "from src.agent.persona" src/agent/trader.py
```

如未 import，加：

```python
# trader.py 顶部 import 区
from src.agent.persona import generate_system_prompt, RuntimeConfig
```

（如已有 `from src.agent.persona import generate_system_prompt` 行，扩展为含 `RuntimeConfig`）

- [ ] **Step 2: Add `RuntimeConfig` import to `src/cli/app.py`**

```bash
grep -n "from src.agent.persona\|from src.agent.trader" src/cli/app.py | head -5
```

在 `src/cli/app.py` 顶部 import 区适当位置加：

```python
from src.agent.persona import RuntimeConfig
```

（位置：与 `from src.agent.trader import ...` 邻近）

- [ ] **Step 3: build_services 内 — agent 创建前插入 max_wake + RuntimeConfig 装配**

**重要**: T4 Step 3 / 4 / 5 是顺序执行——Step 3 插入 ~7 行后，原 line 505 / 615 等行号会漂移。
所以以下 Step 都用 **anchor 字符串**定位（grep 而非 sed），不依赖具体行号。

定位现状（用 anchor 而非行号）：
```bash
grep -n "agent = create_trader_agent" src/cli/app.py
```

应显示 **1 hit**（在 build_services 内单行调用）。anchor 周围 5 行：

```
        timeout_seconds=settings.approval.timeout_seconds,
        console=sc,
    )

    agent = create_trader_agent(model=result.model, persona_config=result.persona)
```

**改造**: 把 anchor 行替换为多行调用，并在前插入 max_wake / runtime_config 装配。具体使用 Edit:

```
old_string:
    )

    agent = create_trader_agent(model=result.model, persona_config=result.persona)

new_string:
    )

    # R2-5: session-fixed runtime config injected into system prompt
    max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
    runtime_config = RuntimeConfig(wake_max_minutes=max_wake)
    agent = create_trader_agent(
        model=result.model,
        persona_config=result.persona,
        runtime=runtime_config,
    )
```

- [ ] **Step 4: build_services 内 — deps 装配后插入 wake_min/max_minutes**

定位现状（anchor 而非行号；Step 3 已改 line 漂移）：
```bash
grep -n "# Alert service" src/cli/app.py
```

应显示 **1 hit**（在 build_services 内）。anchor 上方 5 行 + 下方 1 行：

```
        crypto_etf=crypto_etf_service,
        onchain=onchain_service,
    )

    # Alert service
    if result.alert_enabled:
```

**改造**: 在 `deps = TradingDeps(...)` 收尾 `)` 之后、`# Alert service` 之前插入：

```
old_string:
        crypto_etf=crypto_etf_service,
        onchain=onchain_service,
    )

    # Alert service

new_string:
        crypto_etf=crypto_etf_service,
        onchain=onchain_service,
    )

    # R2-5: wake bounds — explicit assignment matches original run() pattern
    # + defends against TradingDeps default value drift in the future
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = max_wake

    # Alert service
```

- [ ] **Step 5: run 内 — 删除重复装配 + 改写 R4 注释 + 保留 set_next_wake_fn**

定位现状（anchor；Step 3+4 改后 line 漂移更大）：
```bash
grep -n "R4: dynamic wake interval" src/cli/app.py
```

应显示 **1 hit**（在 run 函数内）。anchor 上下文 6 行：

```
    # R4: dynamic wake interval
    max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = max_wake
    deps.set_next_wake_fn = lambda minutes: scheduler.set_next_interval(minutes * 60)
```

**改造**: 删除 max_wake / deps.wake_min/max_minutes 三行（已在 build_services 装配），改写注释：

```
old_string:
    # R4: dynamic wake interval
    max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
    deps.wake_min_minutes = 1
    deps.wake_max_minutes = max_wake
    deps.set_next_wake_fn = lambda minutes: scheduler.set_next_interval(minutes * 60)

new_string:
    # R4: dynamic wake fn binds scheduler (max_wake / wake_min/max装配 in build_services)
    deps.set_next_wake_fn = lambda minutes: scheduler.set_next_interval(minutes * 60)
```

- [ ] **Step 6: Verify max_wake grep counts (DoD §6.1)**

```bash
git grep -n "max_wake\b" src/cli/app.py
```

Expected: 3 hits, 全部在 `build_services` 函数内：
- 1 处 `max_wake = min(max(...))` 定义
- 1 处 `RuntimeConfig(wake_max_minutes=max_wake)` 使用
- 1 处 `deps.wake_max_minutes = max_wake` 使用

**二次校验 run() 内无残留**（动态行号，path A migration 后 build_services 长度漂移也仍 robust）：
```bash
RUN_LINE=$(grep -n "^async def run" src/cli/app.py | head -1 | cut -d: -f1)
git grep -n "max_wake\b" src/cli/app.py | awk -F: -v r="$RUN_LINE" '$2+0 >= r' | wc -l
```
Expected: **0**（所有 max_wake 必须出现在 `async def run` 之前——即 build_services 内）

- [ ] **Step 7: Run full project tests for regression**

```bash
uv run pytest 2>&1 | tail -5
```

Expected: **965 passed + 3 skipped = 968 collected**（与 T3 后基线一致；create_trader_agent 签名兼容默认参数兜底，cli/app.py 路径 A 仅迁移装配位置无新增测试）

- [ ] **Step 8: Manual smoke (optional — confirm prompt rendering)**

Python REPL 快速验证（不进 CI）：
```bash
uv run python -c "
from src.agent.persona import generate_system_prompt, RuntimeConfig
from src.config import PersonaConfig
prompt = generate_system_prompt(PersonaConfig(), RuntimeConfig(wake_max_minutes=120))
assert '1-120 min for this session' in prompt
assert '**Wake interval control**' in prompt
print('OK: dynamic wake_max=120 rendered')
"
```

Expected: `OK: dynamic wake_max=120 rendered`

- [ ] **Step 9: Commit**

```bash
git add src/agent/trader.py src/cli/app.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-5): T4 wire RuntimeConfig through cli/app.py path A

create_trader_agent: add runtime: RuntimeConfig | None = None param,
forwards to generate_system_prompt. Backwards-compat (None → default 60).

cli/app.py path A cross-function migration:
- build_services (line 438 後、439 前): compute max_wake + construct
  RuntimeConfig + pass to create_trader_agent
- build_services (line 507 後、509 前): assign deps.wake_min_minutes=1 +
  deps.wake_max_minutes=max_wake (explicit assignment matches original
  run() pattern + defends against TradingDeps default drift)
- run (line 614-617): delete duplicate max_wake calc + deps assignments,
  rewrite # R4 comment to reflect narrowed scope (set_next_wake_fn only)
- run (line 618): preserved — set_next_wake_fn lambda needs scheduler
  instance which is created in run

DoD verified: git grep "max_wake\b" src/cli/app.py → 3 hits all in
build_services; run() has 0 max_wake refs (no NameError risk).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: wrapper docstring N5 cleanup + N5 drift guard (TDD red→green same commit)

**Files:**
- Modify: `src/agent/trader.py:567-585` (set_next_wake wrapper docstring fact-only 改写)
- Modify: `tests/test_persona.py` (新增 `test_set_next_wake_no_decision_hints_in_description`)

**目的**: 删 wrapper docstring 中 N5 漏网决策暗示 ("Shorten when... lengthen when...")，改用 fact-only 措辞 + 引用 Layer 1 标签；同 commit 加 drift guard 防回归。

- [ ] **Step 1: Read current wrapper docstring**

```bash
sed -n '567,586p' src/agent/trader.py
```

确认现状（含 "Shorten when... lengthen when..." 决策暗示段）。

- [ ] **Step 2: Rewrite `src/agent/trader.py:567-585` (wrapper docstring fact-only)**

将 set_next_wake wrapper 函数体替换为：

```
old_string:
    @tool
    async def set_next_wake(
        ctx: RunContext[TradingDeps],
        minutes: int,
        reasoning: str,
    ) -> str:
        """Set how soon you want to check the market again.

        One-shot: only affects the next wake, then reverts to the default
        interval. Shorten when you have an open position or expect volatility;
        lengthen when the market is quiet and you have no exposure.

        Args:
            minutes: minutes until next wake.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_next_wake as _impl

        return await _impl(ctx.deps, minutes, reasoning=reasoning)

new_string:
    @tool
    async def set_next_wake(
        ctx: RunContext[TradingDeps],
        minutes: int,
        reasoning: str,
    ) -> str:
        """Set the next scheduler wake-up interval (one-shot; reverts to default after use).

        Args:
            minutes: target minutes until next wake. See "Wake interval control"
                in the system prompt for valid range and trigger behavior.
            reasoning: brief description of your decision logic.
        """
        from src.agent.tools_execution import set_next_wake as _impl

        return await _impl(ctx.deps, minutes, reasoning=reasoning)
```

注：删除"Shorten when... lengthen when..."整段（决策暗示）；首行从 "Set how soon you want..."（用户语气）改为 "Set the next scheduler wake-up interval (one-shot; reverts to default after use)"（精确事实）；Args.minutes 引用 Layer 1 "Wake interval control" 标签。

- [ ] **Step 3: Add `test_set_next_wake_no_decision_hints_in_description`**

在 `tests/test_persona.py` 末尾追加：

```python
def test_set_next_wake_no_decision_hints_in_description():
    """R2-5 G10: set_next_wake wrapper docstring fact-only verification.

    Decision hints "shorten when X" / "lengthen when Y" are N5 banned —
    they prescribe agent behavior based on conditions, violating fact-only
    philosophy. This drift guard ensures wrapper docstring (rendered into
    tool_def.description by pydantic-ai 1.78 griffe sniff) stays clean.

    API path: agent._function_toolset.tools[name].tool_def.<attr>
    (matches tests/test_trader_agent.py:210-211 access style; we use
    .description for first-paragraph text vs .parameters_json_schema
    for per-arg Args descriptions — see spec §3.6.1).
    """
    import re
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["set_next_wake"]
    desc = tool.tool_def.description or ""

    # N5 wordlist verification
    assert not re.search(r"\bshorten when\b", desc, re.IGNORECASE), \
        f"set_next_wake description contains banned 'shorten when': {desc!r}"
    assert not re.search(r"\blengthen when\b", desc, re.IGNORECASE), \
        f"set_next_wake description contains banned 'lengthen when': {desc!r}"
    # Sanity: factual content preserved
    assert "one-shot" in desc.lower(), \
        f"set_next_wake description should preserve 'one-shot' fact: {desc!r}"
```

- [ ] **Step 4: Run new test**

```bash
uv run pytest tests/test_persona.py::test_set_next_wake_no_decision_hints_in_description -v
```

Expected: **PASS** (Step 2 已删除决策暗示，docstring 同 commit 改写完毕)

- [ ] **Step 5: Run full test_persona.py suite + N5 grep DoD**

```bash
uv run pytest tests/test_persona.py -v 2>&1 | tail -5
git grep -i "shorten when\|lengthen when" src/agent/
```

Expected:
- pytest: **33 passed**（32 from T3 + 1 new G10 drift guard）
- git grep: **0 hits in src/agent/**（spec §6.1 DoD 项 1）

- [ ] **Step 6: Run full project tests + 双层 grep DoD**

```bash
uv run pytest 2>&1 | tail -5
git grep "Wake interval control" src/agent/persona.py
git grep "Wake interval control" src/agent/trader.py
```

Expected:
- pytest: **966 passed + 3 skipped = 969 collected**（965 from T4 + 1 new；与 spec §6.1 DoD 终点一致）
- persona.py: 1 hit (Layer 1 bullet 主体)
- trader.py: 1 hit (wrapper Args.minutes 引用)

- [ ] **Step 7: Commit (red→green same commit per spec §7 TDD note)**

```bash
git add src/agent/trader.py tests/test_persona.py
git commit -m "$(cat <<'EOF'
refactor(iter-w2r2-5): T5 fact-only set_next_wake wrapper docstring + N5 drift guard

Wrapper docstring (trader.py:567-585) cleanup:
- Delete "Shorten when X / lengthen when Y" decision hints (N5 banned —
  prescribes agent behavior based on conditions; same Iter 4 漏网类型
  caught after Open/Close fill response was deferred to N5 round 2)
- First line from "Set how soon you want..." (user-voice) → "Set the
  next scheduler wake-up interval (one-shot; reverts to default after
  use)" (precise fact)
- Args.minutes refers to Layer 1 "Wake interval control" bullet for
  range + trigger behavior (Single Source of Truth: Layer 1)

N5 drift guard (test_set_next_wake_no_decision_hints_in_description):
- Walks pydantic-ai 1.78 path agent._function_toolset.tools[name].tool_def.description
- Asserts \bshorten when\b / \blengthen when\b absent + factual "one-shot" preserved
- Same commit as docstring change (red→green) avoids intermediate fail

DoD verified:
- git grep "shorten when|lengthen when" src/agent/ → 0 hits
- git grep "Wake interval control" src/agent/persona.py → 1 hit (bullet)
- git grep "Wake interval control" src/agent/trader.py → 1 hit (Args ref)
- pytest: 966 passed + 3 skipped (was 962 + 3 baseline; +4 = 3 new in T2 + 1 new in T5; G12 is rename, not a new test)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Inventory + memory updates (no git)

**Files:**
- Modify: `.working/sim4-issues-inventory.md` (§P0-5 段加 wontfix 注记)
- Modify: `.working/all-pending-needs.md` (Tier 1 R2-5 状态)
- Modify: `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_w2_prep_progress.md` (round 2 表 R2-5 行)

**目的**: 文档/memory 状态校准。这些文件**不进 git**（运行文档 + memory），但仍是 R2-5 收尾必做。

- [ ] **Step 1: Update `.working/sim4-issues-inventory.md` §P0-5**

定位 P0-5 段：
```bash
grep -n "^### P0-5" .working/sim4-issues-inventory.md
```

在该段标题行后插入 wontfix 注记。具体使用 Edit:

```
old_string:
### P0-5. scheduler 30min 兜底失效：set_next_wake 完全覆盖配置

**现象**: cycle `09353c07` (16:46:42)

new_string:
### P0-5. scheduler 30min 兜底失效：set_next_wake 完全覆盖配置  ✅ wontfix - by design (R2-5 brainstorm 决议, 2026-05-01)

**重新评估结论**：现机制按设计工作，180min 硬天花板已是合理断路器（`cli/app.py:615`
公式 `min(max(4 * scheduler_interval_min, 60), 180)` 硬上限 180）。强制 30min 兜底
违背"模拟真实交易员"哲学（real traders 设 alert/SL/TP，不需要老板叫醒）。
sim #4 中 set_next_wake(120) 实际工作良好（75min alert 自然唤醒，期间无坏后果）。

**真实风险源**：长 wake 期间无人盯盘 → P1-4（R:R 漂移 + SL 哲学）才是修法位置，
不是 scheduler 兜底。

**R2-5 实际产出**：set_next_wake fact-only refresh + RuntimeConfig 抽象 + Layer 1
cross-tool bullet（PR # / merge `<sha>`, 2026-05-01）。详见
`docs/superpowers/specs/2026-05-01-iter-w2r2-5-set-next-wake-clarity-design.md`。

---

[原 P0-5 内容保留作历史 traceability]

**现象**: cycle `09353c07` (16:46:42)
```

注：`PR #` 与 `<sha>` 待 PR 合后回填（与 R2-1~R2-4 风格一致）。

- [ ] **Step 2: Update `.working/all-pending-needs.md` Tier 1 R2-5 行**

定位 R2-5 行：
```bash
grep -n "R2-5" .working/all-pending-needs.md | head -5
```

将 R2-5 状态从 `pending` → `✅ landed (PR # / `<sha>`, 2026-05-01)`。具体格式参考 R2-1~R2-4 的 landed 行写法。

- [ ] **Step 3: Update memory `project_w2_prep_progress.md`**

```bash
ls /Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/project_w2_prep_progress.md
```

定位 round 2 表 R2-5 行，从 pending 改为 landed，加 PR # / merge sha / 改动量摘要（与 R2-1~R2-4 行风格一致）。

具体改动：

```
old_string:
| **R2-5** | P0-5 scheduler 30min 兜底失效（set_next_wake 完全覆盖）| W2 阻塞 P0 | 中（brainstorm A/B/C/D）| pending |

new_string:
| **R2-5** | P0-5 scheduler wontfix - by design + set_next_wake fact-only refresh + RuntimeConfig 抽象 | W2 准备 | 小（~40 行）| ✅ landed PR # / merge `<sha>` (2026-05-01，brainstorm 决议 D1-D9：scheduler 不改 + Layer 1 加第 6 cross-tool bullet "Wake interval control" + wrapper docstring 删 "Shorten when..." N5 漏网 + cli/app.py 路径 A 跨函数装配) |
```

注：`PR #` 与 `<sha>` 在 PR 合后回填。

- [ ] **Step 4: 同步 memory `project_tradebot_status.md` (PR list + 顶部 description)**

定位"已完成的 PR"表，新增 PR # 行（与 PR #30/#31/#32/#33 风格一致）。同时更新顶部 description 中"R2-1~R2-4 ✅ landed (PR #30/#31/#32/#33, 2026-04-30)"扩展为含 R2-5。

具体在 `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_tradebot_status.md` 头部 description 字段 + PR 表新增一行。

注：本步骤在 PR 合后 final 回填；PR 进 review 期间可先用占位（`PR #?`）。

- [ ] **Step 5: 同步 MEMORY.md 索引行（spec §3.7.3 校准）**

spec §3.7.3 写"MEMORY.md 索引行不动"——但实测两条索引 description 显式列举 R2-x 状态，
R2-5 landed 后必过期（与 R2-1~R2-4 已写法一致需保持）：

```bash
grep -n "R2-1~R2-4\|R2-5~R2-9" /Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/MEMORY.md
```

应显示 2 hits：
- `[TradeBot project status]` 行：`R2-1~R2-4 ✅ PR #30/#31/#32/#33 / R2-5~R2-9 pending`
- `[W2 prep progress]` 行：`R2-1~R2-4 ✅ PR #30/#31/#32/#33 / R2-5~R2-9 pending`

更新两处为：

```
[TradeBot project status]: R2-1~R2-5 ✅ PR #30/#31/#32/#33/#? / R2-6~R2-9 pending
[W2 prep progress]:        R2-1~R2-5 ✅ PR #30/#31/#32/#33/#? / R2-6~R2-9 pending
```

PR # 在 PR 合后回填；同步 PR 数量从 "34 PRs" → "35 PRs"。

- [ ] **Step 6: Verification — no git changes from this task**

```bash
git status
```

Expected: working tree clean（Task 6 改的文件全部不进 git）。

---

## Self-Review

(待 plan 写完后执行 — checklist：1) Spec coverage / 2) Placeholder scan / 3) Type consistency / 4) Numbers consistency)

### Spec coverage check

| Spec G* | Plan task | Step |
|---|---|---|
| G1 (RuntimeConfig dataclass) | T3 | Step 2 |
| G2 (_build_layer1 签名 + bullet) | T3 | Step 2 |
| G3 (generate_system_prompt 签名 + 默认值) | T3 | Step 2 |
| G4 (create_trader_agent 签名) | T4 | Step 1 |
| G5 (cli/app.py 路径 A) | T4 | Steps 2-5 |
| G7 (wrapper docstring 改写) | T5 | Step 2 |
| G8 (Wake interval control bullet drift guard) | T2 | Step 1 |
| G9 (默认 RuntimeConfig 兜底 drift guard) | T2 | Step 3 |
| G10 (N5 wordlist drift guard) | T5 | Step 3 |
| G11 (动态 wake_max 渲染 drift guard) | T2 | Step 2 |
| G12 (test_layer1_bullet_count_5 → 6 sync) | T3 | Step 3 |
| G13 (inventory §P0-5 注记) | T6 | Step 1 |
| G14 (all-pending-needs.md R2-5 状态) | T6 | Step 2 |

✅ G1-G14 全覆盖（G6 已剔除，spec §2.1）。

### Placeholder scan

- "TBD" / "TODO" 全文搜索: **0 hits**
- "implement later" / "fill in details": **0 hits**
- "Add appropriate error handling" / "validation": **0 hits**
- "Similar to Task N": **0 hits**
- 所有代码块均含完整 source / shell command / expected output

✅ 无 placeholder。

### Type consistency

- `RuntimeConfig` 字段名 `wake_max_minutes`: T2 / T3 / T4 / T6 全部一致
- `generate_system_prompt(persona, runtime=None)` 签名: T2 测试 / T3 实现 / T6 memory 描述一致
- `create_trader_agent(model, persona_config, runtime=None)`: T4 实现 / T5 测试调用一致
- `_function_toolset.tools["set_next_wake"].tool_def.description`: T5 测试访问路径与 spec §3.6.1 一致

✅ Type / signature / property 全 consistent。

### Numbers consistency

- 测试基线 962 pass / 3 skip / 965 collected
- T2 (red): adds 3 new tests，全部 fail (ImportError) — 中间态 962 pass + 3 fail + 3 skip = 968 collected
- T3 (green + G12 rename): 3 红测试转绿；G12 是 rename + 改 assert 值（5 → 6），**count 不变** → 965 pass + 3 skip = 968 collected
- T4 (path A migration): 仅迁移装配位置，无新增/移除测试 → 965 pass + 3 skip = 968 collected
- T5 (wrapper docstring + N5 drift guard): adds 1 new test → **966 pass + 3 skip = 969 collected**

✅ 终点与 spec §6.1 DoD `966 pass / 969 collected` 一致。
✅ test_persona.py 单文件: 29 baseline → 32 after T3 → 33 after T5。

---

## Execution Notes

- 每 task 一个独立 commit（与 R2-1~R2-4 风格一致）
- TDD 节奏：T2 (red) → T3 (green + G12 sync) / T5 (red→green same commit per spec §7)
- T6 不进 git（运行文档 + memory）
- 总改动量估算：~40 LOC source + ~50 LOC tests + ~10 LOC docstring = ~100 LOC
