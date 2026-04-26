# Iter 5 — pydantic-ai 框架合规 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 进观察期前最后一棒，类型保证四件套闭环 —— UsageLimits 单 cycle 防爆裂、`docstring_format='google' + require_parameter_descriptions=True` CI 硬保护、TradingDeps 6 字段类型收紧、pydantic-ai 版本 pin。

**Architecture:** 4 项硬保护合 1 PR，文件耦合低（A 在 cli/app.py / D+D' 在 trader.py / E 在 pyproject.toml + uv.lock）。先 D' 让 import 风险最早暴露；后 D 利用 D' 的真实类型注解；A 与 E 文件不重叠。每 task TDD：写测试 → 红 → 实现 → 绿 → 提交。

**A 项原子性声明**：A 在 plan 中拆为 Task 4（usage_limits kwarg + T1）+ Task 5（UsageLimitExceeded forensic + T2/T3/T4）— 这是 TDD 红绿两半，**必须同一 PR 内连续完成**。仅合并 Task 4 时 AC#2 不成立（UsageLimitExceeded 仍会被通用 except 捕获走 3 次重试）。同理 D 项虽然 Task 2 完成后 AC#3+5 已成立，但 T7（Task 3）是 D 项配套 drift guard，建议同 PR 推。

**Tech Stack:** pydantic-ai 1.78 / pytest 8 / functools.partial / sqlalchemy 2 async / uv lock manager

**Spec:** `docs/superpowers/specs/2026-04-26-iter5-pydantic-ai-compliance-design.md`

---

## File Structure Map

| File | 改动类型 | 职责 |
|---|---|---|
| `src/agent/trader.py` | Modify | TradingDeps 6 字段类型收紧（D'）；create_trader_agent 内 partial helper + 31 处 `@agent.tool` → `@tool`（D）|
| `src/cli/app.py` | Modify | 顶部 imports + `USAGE_LIMITS_PER_CYCLE` 常量 + run_agent_cycle 改 try/except 顺序（A）|
| `pyproject.toml` | Modify | 1 行版本 pin（E）|
| `uv.lock` | Modify | `uv lock --upgrade-package pydantic-ai` 重生（E）|
| `tests/test_trader_agent.py` | Modify | 加 T5 / T6 / T7 / T8 共 4 测试 |
| `tests/test_usage_limits.py` | Create | T1-T4 共 4 测试 |
| `tests/test_pyproject.py` | Create | T9 共 1 测试 |

**测试规模**：848 baseline → **857 passed**（+9 新：T1-T9）。

---

## Task 1: D' — TradingDeps 6 字段类型收紧（含 T8 drift guard）

**Files:**
- Modify: `src/agent/trader.py:7-15` (imports), `src/agent/trader.py:18-39` (TradingDeps fields)
- Test: `tests/test_trader_agent.py` (新增 T8)

**Why first**: D' 改 import 链最早暴露循环 import 风险；后续 D/A/E 都能在干净 import 状态下展开。

**Spec ref**: §3.3 D' design / §4.2 T8 / §6.5 fallback

- [ ] **Step 1.1: 写失败测试 T8（drift guard）**

在 `tests/test_trader_agent.py` 文件末尾追加：

```python
def test_trading_deps_no_object_typed_service_fields():
    """T8 drift guard: TradingDeps 6 个 service 字段不能用 object | None。

    限定保护这 6 个特定字段（硬编码列表）；未来加新 deps 字段不会被本测试
    覆盖——是有意的窄化，避免误伤合法 Callable / object 用法。
    """
    from typing import get_args, get_type_hints
    from src.agent.trader import TradingDeps

    expected_typed_fields = {
        "approval_gate", "metrics", "news",
        "macro", "crypto_etf", "onchain",
    }
    hints = get_type_hints(TradingDeps)
    for field_name in expected_typed_fields:
        hint = hints[field_name]
        args = get_args(hint)
        assert object not in args, (
            f"{field_name} still typed with `object` in {args}; "
            f"should be tightened to real service class | None"
        )
```

- [ ] **Step 1.2: 运行测试，确认失败**

```bash
cd /Users/z/Z/TradeBot
uv run pytest tests/test_trader_agent.py::test_trading_deps_no_object_typed_service_fields -v
```

Expected: **FAIL** — `assert object not in (object, NoneType)` 因为现状 `metrics: object | None`。

- [ ] **Step 1.3: 修改 trader.py — 加 6 个 service imports**

`src/agent/trader.py:7-15` 现状（imports 块）：

```python
from src.agent.memory import MemoryService
from src.agent.persona import generate_system_prompt
from src.config import PersonaConfig
from src.integrations.exchange.base import BaseExchange
from src.integrations.market_data import MarketDataService
from src.services.technical import TechnicalAnalysisService
```

改为（6 个 NEW import 按字母序 + 模块路径分组插入；最终块整体替换）：

```python
from src.agent.memory import MemoryService
from src.agent.persona import generate_system_prompt
from src.cli.approval import ApprovalGate
from src.config import PersonaConfig
from src.integrations.crypto_etf.service import CryptoEtfService
from src.integrations.exchange.base import BaseExchange
from src.integrations.macro.service import MacroService
from src.integrations.market_data import MarketDataService
from src.integrations.news.service import NewsService
from src.integrations.onchain.service import OnchainService
from src.services.metrics import MetricsService
from src.services.technical import TechnicalAnalysisService
```

NEW 行（共 6）：`ApprovalGate / CryptoEtfService / MacroService / NewsService / OnchainService / MetricsService`。注意 `cli.approval` 排在 `agent.persona` 后、`config` 前（按 `src.<module>` 字母序：`agent < cli < config < integrations < services`）。

- [ ] **Step 1.4: 修改 trader.py — 收紧 6 个字段类型 + 删注释**

`src/agent/trader.py:28, 34-38`（TradingDeps 内 6 行）逐行改：

```python
# Before (L28)
    approval_gate: object | None = None  # ApprovalGate instance
# After (L28)
    approval_gate: ApprovalGate | None = None

# Before (L34)
    metrics: object | None = None  # MetricsService, typed as object to avoid circular import
# After (L34)
    metrics: MetricsService | None = None

# Before (L35)
    news: object | None = None  # NewsService, typed as object to avoid circular import
# After (L35)
    news: NewsService | None = None

# Before (L36)
    macro: object | None = None  # MacroService; typed as object to avoid circular import
# After (L36)
    macro: MacroService | None = None

# Before (L37)
    crypto_etf: object | None = None  # CryptoEtfService; typed as object to avoid circular import
# After (L37)
    crypto_etf: CryptoEtfService | None = None

# Before (L38)
    onchain: object | None = None  # OnchainService; typed as object to avoid circular import
# After (L38)
    onchain: OnchainService | None = None
```

注：行号在加 imports 后会偏移，按 grep 实际行号操作。注释（`# ApprovalGate instance` / `# *Service, typed as object to avoid circular import`）全部删除。

- [ ] **Step 1.5: 运行 import sanity（AC#13）**

```bash
uv run python -c "from src.agent.trader import TradingDeps; print('TradingDeps OK')"
```

Expected: 输出 `TradingDeps OK`，无 ImportError。

**如果失败**（极小概率，spec §6.5）：错误信息会指出循环 import 模块。Fallback 改用 `if TYPE_CHECKING` 守卫——参见 spec §6.5。

- [ ] **Step 1.6: 运行 collect-only 全模块图 sanity（AC#14）**

```bash
uv run pytest --collect-only 2>&1 | tail -5
```

Expected: 末尾显示 `collected 850 items`（baseline 849 = 848 passed + 1 skipped + 新 T8）；无 collection error。

- [ ] **Step 1.7: 运行 T8 + 现有测试，确认 T8 PASS + 0 regression**

```bash
uv run pytest tests/test_trader_agent.py -v
```

Expected: 所有测试 PASS（含新 T8）；现有测试无 regression。

- [ ] **Step 1.8: 提交**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "$(cat <<'EOF'
Iter 5 D': tighten TradingDeps 6 service fields from object | None

- approval_gate / metrics / news / macro / crypto_etf / onchain 收紧到真实类型
- 删 6 处 "typed as object to avoid circular import" 注释（理由失效 — from __future__ import annotations 已使所有注解 lazy 字符串化）
- T8 drift guard: 6 字段不能用 object 类型

Spec §3.3 / AC#12 / AC#13.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: D — Tool helper + docstring/require config（含 T5/T6）

**Files:**
- Modify: `src/agent/trader.py:42-585` (create_trader_agent body)
- Test: `tests/test_trader_agent.py` (新增 T5 + T6)

**Spec ref**: §3.2 D design / §4.2 T5+T6 / §6.1 fallback

- [ ] **Step 2.1: 写失败测试 T5（docstring_format='google'）**

在 `tests/test_trader_agent.py` 文件末尾追加：

```python
def test_all_tools_use_google_docstring_format():
    """T5: 31 个工具全部 docstring_format='google'。

    实测 1.78 toolset 私有 API 可读 Tool.docstring_format 字段。
    若 1.79+ 改名见 spec §6.3 fallback。
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    for name, tool in agent._function_toolset.tools.items():
        assert tool.docstring_format == "google", (
            f"Tool {name} docstring_format = {tool.docstring_format!r}, expected 'google'"
        )
```

- [ ] **Step 2.2: 写失败测试 T6（require_parameter_descriptions=True）**

```python
def test_all_tools_require_parameter_descriptions():
    """T6: 31 个工具全部 require_parameter_descriptions=True。"""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    for name, tool in agent._function_toolset.tools.items():
        assert tool.require_parameter_descriptions is True, (
            f"Tool {name} require_parameter_descriptions = "
            f"{tool.require_parameter_descriptions!r}, expected True"
        )
```

- [ ] **Step 2.3: 运行 T5 + T6，确认失败**

```bash
uv run pytest tests/test_trader_agent.py::test_all_tools_use_google_docstring_format tests/test_trader_agent.py::test_all_tools_require_parameter_descriptions -v
```

Expected: **FAIL** — 现状 docstring_format 默认 `'auto'`，require_parameter_descriptions 默认 `False`。

- [ ] **Step 2.4: 修改 trader.py — 加 partial helper**

`src/agent/trader.py:42-56`（`create_trader_agent` 函数体头部）：

```python
# Before (L46-56)
def create_trader_agent(
    model: str, persona_config: PersonaConfig
) -> Agent[TradingDeps, str]:
    # 函数级懒加载 — 与现有 26 个 tool 的懒加载风格一致（技术上非必需：
    # recorder 侧 TYPE_CHECKING + 字符串前向引用已足以破环）
    from src.services.tool_call_recorder import ToolCallRecorder

    system_prompt = generate_system_prompt(persona_config)
    agent = Agent(
        model,
        deps_type=TradingDeps,
        output_type=str,
        instructions=system_prompt,
        capabilities=[ToolCallRecorder()],
    )

# After (新增 helper line 紧跟 Agent(...) 之后)
def create_trader_agent(
    model: str, persona_config: PersonaConfig
) -> Agent[TradingDeps, str]:
    # 函数级懒加载 — 与现有 26 个 tool 的懒加载风格一致（技术上非必需：
    # recorder 侧 TYPE_CHECKING + 字符串前向引用已足以破环）
    from functools import partial
    from src.services.tool_call_recorder import ToolCallRecorder

    system_prompt = generate_system_prompt(persona_config)
    agent = Agent(
        model,
        deps_type=TradingDeps,
        output_type=str,
        instructions=system_prompt,
        capabilities=[ToolCallRecorder()],
    )

    # Iter 5 D: 启用 google docstring 显式声明 + 强制 Args 完整性。
    # require_parameter_descriptions=True 在 tool 加载时校验，缺 Args 立即 startup fail。
    tool = partial(agent.tool, docstring_format="google", require_parameter_descriptions=True)
```

- [ ] **Step 2.5: 修改 trader.py — 31 处 `@agent.tool` → `@tool`**

使用 Edit tool（`replace_all=True`），精准匹配 4 空格缩进的装饰器（避免误伤 L588 注释里的 `@agent.tool` 文本）：

- old_string: `    @agent.tool` （注意：4 个空格 + `@agent.tool`，**无尾随换行**）
- new_string: `    @tool`
- replace_all: `True`

替换后 `grep -c "    @tool" src/agent/trader.py` 应为 31，`grep -c "    @agent.tool" src/agent/trader.py` 应为 0。

L588 的 `# REGISTERED_TOOL_NAMES: 与 \`@agent.tool\` 装饰顺序保持一致` 注释**保留不动**（不带 4 空格前缀，不被匹配）。

- [ ] **Step 2.6: 运行启动 sanity（AC#5）**

```bash
uv run python -c "from src.agent.trader import create_trader_agent; from src.config import PersonaConfig; agent = create_trader_agent('test', PersonaConfig()); print(f'agent OK, {len(agent._function_toolset.tools)} tools')"
```

Expected: 输出 `agent OK, 31 tools`，无异常。

**如果失败**（spec §6.2）：错误信息会指出某工具 Args 段缺。立即先补 Args 段（同一文件，符合本 PR scope），再继续。

**如果出现 partial 装饰器异常**（极不可能，spec §6.1）：把所有 `@tool` 改为 `@tool()`（带空括号），再次运行。

- [ ] **Step 2.7: 运行 T5/T6 + 全 trader_agent 测试，确认 PASS**

```bash
uv run pytest tests/test_trader_agent.py -v
```

Expected: 所有测试 PASS（含 T5 / T6 / T8）；0 regression。

- [ ] **Step 2.8: 提交**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "$(cat <<'EOF'
Iter 5 D: enable docstring_format='google' + require_parameter_descriptions=True per tool

- functools.partial helper 集中配置 31 个 @agent.tool → @tool
- require_parameter_descriptions=True 是 CI 硬保护：加新工具忘写 Args → startup fail
- T5/T6 验证 31 工具配置写入 ToolDefinition

Spec §3.2 / AC#3 / AC#5.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: T7 — pydantic-ai 行为契约测试（不是 trader.py drift guard）

**Files:**
- Test: `tests/test_trader_agent.py` (新增 T7)

**T7 定位（重要）**：T7 自构 `partial(Agent.tool, ..., require_parameter_descriptions=True)` + 缺 Args 工具，断言抛异常 —— 这是**对 pydantic-ai 库行为的契约锁定**，不依赖 trader.py 的 helper。trader.py 的 drift guard 实际由 **T5/T6 覆盖**（验证 31 工具的 ToolDefinition 配置）。T7 价值：1.79+ 若静默放弃 require 校验（导致缺 Args 不再 fail-fast），T7 会 FAIL 提醒；但 T7 PASS **不证明**本 PR Task 2 实施正确。

**Spec ref**: §4.2 T7

- [ ] **Step 3.1: 写测试 T7（pydantic-ai 行为契约）**

在 `tests/test_trader_agent.py` 文件末尾追加：

```python
def test_missing_args_with_require_descriptions_triggers_fail():
    """T7: pydantic-ai 1.78 行为契约 — partial(Agent.tool,
    require_parameter_descriptions=True) 装饰缺 Args 段工具时抛异常。

    本测试**不验证 trader.py 实施**（T5/T6 才是 trader.py drift guard）；
    本测试锁定 pydantic-ai 版本行为：若 1.79+ 静默放弃 require 校验，本测试 FAIL 提醒。
    """
    from functools import partial
    import pytest as _pytest
    from pydantic_ai import Agent, RunContext

    agent = Agent("test", deps_type=type(None), output_type=str)
    tool = partial(agent.tool, docstring_format="google", require_parameter_descriptions=True)

    with _pytest.raises(Exception):
        @tool
        async def bad_tool(ctx: RunContext, x: int) -> str:
            """Missing Args section docstring."""
            return str(x)
```

- [ ] **Step 3.2: 运行 T7，确认 PASS**

```bash
uv run pytest tests/test_trader_agent.py::test_missing_args_with_require_descriptions_triggers_fail -v
```

Expected: **PASS** — pydantic-ai 1.78 在 tool 加载时校验，抛 `pydantic_ai.exceptions.UserError` 被捕获。

**FAIL 的含义**：1.78 版本下 partial 没把 `require_parameter_descriptions=True` 传给 `Agent.tool`（pydantic-ai 内部行为变化）—— 与 Task 2 的 trader.py 实施无关。本测试与 T5/T6 维度独立。

- [ ] **Step 3.3: 提交**

```bash
git add tests/test_trader_agent.py
git commit -m "$(cat <<'EOF'
test(iter5 T7): pin pydantic-ai 1.78 require_parameter_descriptions behavior

锁定 pydantic-ai 1.78 行为契约：partial(Agent.tool, require_parameter_descriptions=True)
装饰缺 Args 段工具时抛 UserError。若 1.79+ 静默放弃此校验，本测试 FAIL 提醒。
T5/T6 才是 trader.py 配置 drift guard，本测试与之维度独立。

Spec §4.2 T7.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: A — UsageLimits 常量 + agent.run kwarg（含 T1）

**Files:**
- Modify: `src/cli/app.py:1-35` (imports), `src/cli/app.py:37` (顶部加常量), `src/cli/app.py:143` (kwarg)
- Test: `tests/test_usage_limits.py` (Create + T1)

**Spec ref**: §3.1 A design / §4.1 T1

- [ ] **Step 4.1: 创建测试文件 + 写失败测试 T1**

新建 `tests/test_usage_limits.py`：

```python
"""Iter 5 §3.1 — UsageLimits + UsageLimitExceeded forensic path tests."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import models
from sqlalchemy import select

from src.storage.database import init_db, get_session
from src.storage.models import Session as SessionModel, DecisionLog

models.ALLOW_MODEL_REQUESTS = False


async def _make_deps_and_engine(session_id: str = "sess-iter5"):
    """Build minimal TradingDeps + real engine + session row (FK target)."""
    from src.agent.trader import TradingDeps

    engine = await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session(engine) as db:
        db.add(SessionModel(id=session_id, name="iter5"))
        await db.commit()

    deps = TradingDeps(
        symbol="BTC/USDT:USDT",
        timeframe="15m",
        market_data=AsyncMock(),
        exchange=AsyncMock(),
        technical=MagicMock(),
        memory=AsyncMock(format_for_prompt=AsyncMock(return_value="No relevant memories.")),
        session_id=session_id,
        db_engine=engine,
    )
    return deps, engine


async def test_usage_limits_passed_to_agent_run(monkeypatch):
    """T1: run_agent_cycle 调用 agent.run 时 kwargs 含 usage_limits 且 == USAGE_LIMITS_PER_CYCLE。"""
    from src.cli.app import USAGE_LIMITS_PER_CYCLE, TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine()
    budget = TokenBudget(daily_max=500_000)

    captured_kwargs = {}

    async def mock_run(prompt, **kwargs):
        captured_kwargs.update(kwargs)
        result = MagicMock()
        result.usage = lambda: MagicMock(total_tokens=100)
        result.new_messages = lambda: []
        result.output = "test output"
        return result

    mock_agent = MagicMock()
    mock_agent.run = mock_run
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent,
        deps=deps,
        trigger_type="scheduled",
        budget=budget,
        engine=engine,
    )

    assert "usage_limits" in captured_kwargs, (
        f"agent.run 未收到 usage_limits 参数, captured: {list(captured_kwargs.keys())}"
    )
    assert captured_kwargs["usage_limits"] is USAGE_LIMITS_PER_CYCLE, (
        f"usage_limits 不是 USAGE_LIMITS_PER_CYCLE 常量"
    )
```

- [ ] **Step 4.2: 运行 T1，确认失败**

```bash
uv run pytest tests/test_usage_limits.py::test_usage_limits_passed_to_agent_run -v
```

Expected: **FAIL** — `ImportError: cannot import name 'USAGE_LIMITS_PER_CYCLE' from 'src.cli.app'`。

- [ ] **Step 4.3: 修改 cli/app.py — 加 imports**

`src/cli/app.py` 顶部 import 区（约 L15 附近 pydantic_ai 相关 import 之后）：

```python
# 加这两行
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
```

具体插入位置：紧贴现有 `from pydantic_ai.messages import ...`（L15-18）之后。

- [ ] **Step 4.4: 修改 cli/app.py — 加 USAGE_LIMITS_PER_CYCLE 常量**

紧贴 `class TokenBudget:`（约 L37）之前加：

```python
# Iter 5 §3.1: 单 cycle 防爆裂兜底；非业务 throttle。
# 正常 cycle ~10 tool calls / ~5 LLM requests，阈值留 5x buffer。
# 观察期 W1 末校准（实测中位数 + safety buffer）。
USAGE_LIMITS_PER_CYCLE = UsageLimits(
    request_limit=50,            # = pydantic-ai default，显式传防 1.79+ 默认变化
    tool_calls_limit=50,
    total_tokens_limit=300_000,  # 单 cycle 上限；外层 daily TokenBudget 是日累积
)
```

- [ ] **Step 4.5: 修改 cli/app.py — agent.run 加 kwarg**

`src/cli/app.py:143`（run_agent_cycle 内部）：

```python
# Before
            result = await agent.run(prompt, **run_kwargs)

# After
            result = await agent.run(
                prompt,
                usage_limits=USAGE_LIMITS_PER_CYCLE,
                **run_kwargs,
            )
```

- [ ] **Step 4.6: 运行 T1，确认 PASS**

```bash
uv run pytest tests/test_usage_limits.py::test_usage_limits_passed_to_agent_run -v
```

Expected: **PASS**。

- [ ] **Step 4.7: 提交**

```bash
git add src/cli/app.py tests/test_usage_limits.py
git commit -m "$(cat <<'EOF'
Iter 5 A1: pass UsageLimits to agent.run

- USAGE_LIMITS_PER_CYCLE 常量 (50/50/300k)，单 cycle 防爆裂兜底
- run_agent_cycle 内 agent.run() 传 usage_limits kwarg
- T1 验证传参生效

阈值哲学：保守宽，正常 cycle 留 5x buffer；观察期 W1 末根据实测调紧。
Spec §3.1 / AC#1.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: A — UsageLimitExceeded forensic 路径（含 T2/T3/T4）

**Files:**
- Modify: `src/cli/app.py:140-152` (try/except 改造)
- Test: `tests/test_usage_limits.py` (追加 T2/T3/T4)

**Spec ref**: §3.1 forensic path / §4.1 T2-T4

- [ ] **Step 5.1: 追加测试 T2（forensic decision_log）**

在 `tests/test_usage_limits.py` 文件末尾追加：

```python
async def test_usage_limit_exceeded_writes_forensic_decision_log():
    """T2: UsageLimitExceeded 触发时写 decision_logs 1 行 + 函数返回 None。"""
    from pydantic_ai.exceptions import UsageLimitExceeded

    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine(session_id="sess-t2")
    budget = TokenBudget(daily_max=500_000)

    async def boom(prompt, **kwargs):
        raise UsageLimitExceeded("test reason")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    result = await run_agent_cycle(
        agent=mock_agent,
        deps=deps,
        trigger_type="scheduled",
        budget=budget,
        engine=engine,
    )

    assert result is None, "病理 cycle 应返回 None"

    async with get_session(engine) as db:
        rows = (await db.execute(
            select(DecisionLog).where(DecisionLog.decision == "usage_limit_exceeded")
        )).scalars().all()

    assert len(rows) == 1, f"应写 1 行 decision='usage_limit_exceeded'，实际 {len(rows)} 行"
    row = rows[0]
    assert row.session_id == "sess-t2"
    assert "test reason" in row.reasoning
    assert row.tokens_used == 0  # spec §3.1 #3 设计取舍
```

- [ ] **Step 5.2: 追加测试 T3（不进重试）**

```python
async def test_usage_limit_exceeded_does_not_retry():
    """T3: UsageLimitExceeded 不进 range(3) 重试，agent.run 仅被调 1 次。"""
    from pydantic_ai.exceptions import UsageLimitExceeded

    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine(session_id="sess-t3")
    budget = TokenBudget(daily_max=500_000)

    call_count = {"n": 0}

    async def boom(prompt, **kwargs):
        call_count["n"] += 1
        raise UsageLimitExceeded("test")

    mock_agent = MagicMock()
    mock_agent.run = boom
    mock_agent.model = "test-model"

    await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    assert call_count["n"] == 1, (
        f"agent.run 应仅被调 1 次（不重试），实际 {call_count['n']} 次"
    )
```

- [ ] **Step 5.3: 追加测试 T4（通用 Exception 仍重试 3 次）**

```python
async def test_generic_exception_still_retries_3_times(monkeypatch):
    """T4: 通用 Exception 不被 UsageLimitExceeded 路径误捕，仍走 3 次重试。"""
    from src.cli.app import TokenBudget, run_agent_cycle

    deps, engine = await _make_deps_and_engine(session_id="sess-t4")
    budget = TokenBudget(daily_max=500_000)

    # 跳过实际 sleep 加速测试
    async def fast_sleep(_):
        pass
    monkeypatch.setattr("asyncio.sleep", fast_sleep)

    call_count = {"n": 0}

    async def flaky(prompt, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise RuntimeError("transient network error")
        result = MagicMock()
        result.usage = lambda: MagicMock(total_tokens=100)
        result.new_messages = lambda: []
        result.output = "recovered"
        return result

    mock_agent = MagicMock()
    mock_agent.run = flaky
    mock_agent.model = "test-model"

    result = await run_agent_cycle(
        agent=mock_agent, deps=deps, trigger_type="scheduled",
        budget=budget, engine=engine,
    )

    assert call_count["n"] == 3, f"应重试 3 次，实际 {call_count['n']}"
    assert result is not None, "第 3 次成功应返回 result"
```

- [ ] **Step 5.4: 运行 T2/T3/T4，确认失败**

```bash
uv run pytest tests/test_usage_limits.py -v
```

Expected:
- T1 PASS（Task 4 已实现）
- **T2 FAIL** — 现状 UsageLimitExceeded 被通用 except 捕获，会重试 3 次然后 return None，但不写 decision_logs 行
- **T3 FAIL** — 同上，UsageLimitExceeded 现走重试路径
- T4 可能 PASS（现有重试逻辑仍然工作），但应在 forensic 改造后仍 PASS

- [ ] **Step 5.5: 修改 cli/app.py — 加 UsageLimitExceeded 优先捕获**

`src/cli/app.py:140-152`（现有重试块）：

```python
# Before
    result = None
    for attempt in range(3):
        try:
            result = await agent.run(
                prompt,
                usage_limits=USAGE_LIMITS_PER_CYCLE,
                **run_kwargs,
            )
            break
        except Exception as e:
            if attempt < 2:
                delay = 2 ** attempt
                logger.warning(f"LLM call attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                logger.error(f"LLM call failed after 3 attempts: {e}")
                return None

# After
    result = None
    for attempt in range(3):
        try:
            result = await agent.run(
                prompt,
                usage_limits=USAGE_LIMITS_PER_CYCLE,
                **run_kwargs,
            )
            break
        except UsageLimitExceeded as e:
            # 病理状态（LLM 死循环 / runaway tools），不重试，写 forensic trace。
            # 注：ToolCallRecorder capability 已在 agent.run 内部独立 session 写完
            # 任何已成功 tool 调用的 tool_calls 行（不需要本路径协调 rollback）。
            logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
            async with get_session(engine) as session:
                session.add(DecisionLog(
                    session_id=deps.session_id,
                    cycle_id=cycle_id,
                    trigger_type=trigger_type,
                    decision="usage_limit_exceeded",
                    reasoning=str(e)[:500],
                    model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                    tokens_used=0,  # spec §3.1 #3: UsageLimitExceeded 不携带 partial usage
                ))
                await session.commit()
            return None
        except Exception as e:
            if attempt < 2:
                delay = 2 ** attempt
                logger.warning(f"LLM call attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                logger.error(f"LLM call failed after 3 attempts: {e}")
                return None
```

注意：`UsageLimitExceeded` 必须在通用 `Exception` **之前**捕获（Python except 顺序保证）。

- [ ] **Step 5.6: 运行 T1-T4，确认全 PASS**

```bash
uv run pytest tests/test_usage_limits.py -v
```

Expected: 4 测试全 PASS。

- [ ] **Step 5.7: 运行整套现有测试，确认 0 regression**

```bash
uv run pytest -x
```

Expected: 全绿 — `856 passed + 1 skipped`（baseline 848 passed + 1 skipped + 4 新 T1-T4 + 4 新 T5/T6/T7/T8 from Task 1-3 = 856 passed + 1 skipped）。

- [ ] **Step 5.8: 提交**

```bash
git add src/cli/app.py tests/test_usage_limits.py
git commit -m "$(cat <<'EOF'
Iter 5 A2: UsageLimitExceeded forensic decision_log path

- run_agent_cycle 内 try/except 顺序：UsageLimitExceeded 优先于通用 Exception
- 病理状态（LLM 死循环 / runaway tools）不进 3 次重试，写 decision_logs 行
- decision='usage_limit_exceeded'（24 字符 < String(50) limit）
- tokens_used=0 是设计取舍（exception 不携带 partial usage，spec §3.1 #3）
- T2/T3/T4 验证 forensic 写入 / 不重试 / 通用 Exception 不被误捕

Spec §3.1 / AC#2.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: E — pyproject.toml 版本 pin + uv.lock + T9

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `uv.lock` (re-generate)
- Test: `tests/test_pyproject.py` (Create + T9)

**Spec ref**: §3.4 / §4.3 T9

- [ ] **Step 6.1: 创建测试文件 + 写失败测试 T9**

新建 `tests/test_pyproject.py`：

```python
"""Iter 5 §3.4 — pydantic-ai 版本 pin drift guard."""
from __future__ import annotations

import tomllib
from pathlib import Path


def test_pydantic_ai_pinned_to_minor_floor_below_v2():
    """T9: pyproject.toml 中 pydantic-ai constraint 同时含 >=1.78 和 <2。

    防 floor 解 pin（>=1.0 → minor 升级污染观察期）+ 防 ceiling 解 pin
    （<2 删除 → 2.0 major breaking change）。
    """
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    deps = data["project"]["dependencies"]
    pydantic_ai_constraint = next(
        (d for d in deps if d.startswith("pydantic-ai")), None
    )

    assert pydantic_ai_constraint is not None, "pydantic-ai 不在 dependencies 中"
    assert ">=1.78" in pydantic_ai_constraint, (
        f"floor pin >=1.78 缺失: {pydantic_ai_constraint!r}"
    )
    assert "<2" in pydantic_ai_constraint, (
        f"ceiling pin <2 缺失: {pydantic_ai_constraint!r}"
    )
```

- [ ] **Step 6.2: 运行 T9，确认失败**

```bash
uv run pytest tests/test_pyproject.py -v
```

Expected: **FAIL** — 现状 `pydantic-ai>=1.0`，缺 `>=1.78` 和 `<2`。

- [ ] **Step 6.3: 修改 pyproject.toml**

`pyproject.toml:7`：

```diff
-    "pydantic-ai>=1.0",
+    "pydantic-ai>=1.78,<2",
```

- [ ] **Step 6.4: 重生 uv.lock + 检查 diff 范围**

```bash
uv lock --upgrade-package pydantic-ai
git diff uv.lock | head -80
```

Expected: `uv.lock` 文件被更新（pydantic-ai 写死到 1.78.0）。

**Diff 范围检查**：
- ✅ 仅 pydantic-ai 自身（version + URL + hashes）变化 → 直接进 Step 6.5
- ⚠ 若伴随传递依赖 bump（如 `pydantic-graph` / `mcp` / `griffe` 等），列出变化的包名记入 Task 6 commit message body 和 PR 描述（让 reviewer 知道 lock 大量改动是 transitive 而非额外 scope）；不需 abort，传递升级是正常副作用

- [ ] **Step 6.5: 运行 T9 + 全套测试，确认全 PASS**

```bash
uv run pytest -x
```

Expected: 全绿 — `857 passed + 1 skipped`（含新 T9）。

- [ ] **Step 6.6: 提交**

```bash
git add pyproject.toml uv.lock tests/test_pyproject.py
git commit -m "$(cat <<'EOF'
Iter 5 E: pin pydantic-ai >=1.78,<2 + sync uv.lock

- pyproject.toml floor + ceiling 双 pin（防观察期内 minor / major 升级行为变化）
- uv.lock 同步 (uv lock --upgrade-package pydantic-ai) 写死 1.78.0 patch level
- 观察期 onboarding 用 uv sync --frozen 校验
- T9 双断言 >=1.78 AND <2，防未来任一被解开

Spec §3.4 / AC#4.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 最终验证 + observation candidate memory

**Files:**
- Create: `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_iter5_observation_candidates.md` （memory 目录，**不进 git**）
- Update: `~/.claude/projects/-Users-z-Z-TradeBot/memory/MEMORY.md`（追加索引行）

**为什么独立 task**：所有代码改动已落地，本 task 仅做 acceptance 验证 + memory 维护。零代码改动 = 零 git commit。

**Spec ref**: §5 Acceptance Criteria / §7 观察期 follow-up / AC#9

- [ ] **Step 7.1: 全套 acceptance 验证**

逐条跑 spec §5 AC：

```bash
# AC#5: create_trader_agent 启动通过
uv run python -c "from src.agent.trader import create_trader_agent; from src.config import PersonaConfig; agent = create_trader_agent('test', PersonaConfig()); assert len(agent._function_toolset.tools) == 31; print('AC#5 OK')"

# AC#13: TradingDeps import 不抛 ImportError
uv run python -c "from src.agent.trader import TradingDeps; print('AC#13 OK')"

# AC#14: pytest collect-only 无 collection error
uv run pytest --collect-only 2>&1 | tail -3

# AC#6 + AC#7: 9 个新测试全 pass + 0 regression
uv run pytest -x 2>&1 | tail -10

# AC#8: 无 banned-word
uv run pytest tests/test_fact_only_wordlist.py -v 2>&1 | tail -5
```

Expected:
- AC#5: `AC#5 OK`
- AC#13: `AC#13 OK`
- AC#14: `collected 858 items`（848 baseline + 1 skipped 也算 1 item + 9 新 = 858）；无 collection error
- AC#6+7: `857 passed + 1 skipped`（baseline 848 passed + 1 skipped + 9 新 T1-T9）
- AC#8: 全 PASS（fact-only wordlist 测试不动）

- [ ] **Step 7.2: 创建 observation candidate memory**

新建文件 `~/.claude/projects/-Users-z-Z-TradeBot/memory/project_iter5_observation_candidates.md`：

```markdown
---
name: iter5-observation-candidates
description: 候选议题 — Iter 5 推迟的 B (ModelRetry 试点) 和 F (logfire instrumentation)，观察期数据触发后启动
type: project
---

## 背景

Iter 5 (PR #?) ROI 评估推迟 B/F 两项到观察期数据驱动。spec §1.3 七项 ROI 评估总结 + §7.3 触发条件汇总在此，避免下次会话遗忘。

**Why:** 观察期前没数据证明 LLM 真的把"错误字符串"当事实推理（B），或 cross-cycle trace 分析需求（F）；提前做 = 盲打或 over-engineering。
**How to apply:** 观察期数据出现下面任一触发条件 → 启动对应议题。

## B — ModelRetry 试点

**触发条件**：观察期 W2+ SQL 查到"LLM final output 把 tool 错误字符串当事实推理 ≥3 例"。

**Pattern**：`tool_calls.status='error'` 同 cycle 内 decision_logs.reasoning 含 "unavailable so I'll wait" / "no data therefore" / "since X is not available, I will" 类语句。

**SQL hook**：
```sql
SELECT dl.cycle_id, dl.reasoning, tc.tool_name, tc.error_type
FROM decision_logs dl
JOIN tool_calls tc ON tc.cycle_id = dl.cycle_id
WHERE tc.status = 'error'
  AND (
    dl.reasoning LIKE '%unavailable%'
    OR dl.reasoning LIKE '%no data%'
    OR dl.reasoning LIKE '%not available%'
  )
ORDER BY dl.created_at DESC LIMIT 20;
```

**实施范围（候选）**：
- `get_market_data` / `get_order_book`：参数无效 → `ModelRetry("Symbol not found, did you mean ...")`
- 任一 CCXT 调用：捕获 `NetworkError` → `ModelRetry("Transient network error, retry")`
- 配套：Agent 级 `retries=2`（默认 1）；与 USAGE_LIMITS_PER_CYCLE 配合避免无限循环

**风险**：可能伤害"LLM 自主判断"产品立场（fact-only 错误 → 强制 retry）；试点 1-2 个工具，对比观察期前后的 LLM 行为变化。

## F — logfire instrumentation

**触发条件**：观察期 W1 结束决策时点，若 `tool_call_summary.py` + DB SQL 已够当前分析需求，不接；若需 cross-cycle trace（如"为何这一系列 cycle 都没 set SL"因果分析），启动 logfire。

**实施细节**：
- `logfire.instrument_pydantic_ai()` 一行 instrumentation
- 与 ToolCallRecorder 不冲突（ToolCallRecorder 写 DB 是产品需求，logfire 是工业级可视化）
- 配套：API key 管理 / project setup / 数据 retention 决策

## 不会变 candidate 的项

- **C. Agent.override 测试重构**：不做（现有 `TestModel` 端到端覆盖足够）
- **G. 删 cli/app.py 外层 3 次重试**：保留（与 ModelRetry 维度不同）
```

- [ ] **Step 7.3: 更新 MEMORY.md 索引**

在 `~/.claude/projects/-Users-z-Z-TradeBot/memory/MEMORY.md` 末尾追加：

```markdown
- [Iter 5 observation candidates](project_iter5_observation_candidates.md) — 候选议题：B (ModelRetry 试点) / F (logfire)，观察期数据触发后启动
```

- [ ] **Step 7.4: 不 commit memory（memory 在用户 home 目录，不属于 repo）**

无 git 操作。

---

## Post-Merge（PR squash 合并后再做，不在本 plan 内）

合并后下一会话起手做：

1. 更新 `project_pre_observation_iterations.md` Iter 5 行标 ✅ landed + squash hash + 测试数（848 → 857）
2. 更新 `project_tradebot_status.md` 加 PR #? 行 + Iter 5 完成总结段
3. 更新 `project_tradingdeps_typing_cleanup.md` 标 ✅ landed via Iter 5 PR
4. 更新 `MEMORY.md` 顶层索引行（tradebot_status / pre_observation_iterations / tradingdeps_typing_cleanup 三行）
5. 检查 candidate memory（`project_iter5_observation_candidates.md`）SQL 是否可用 → 进观察期

---

## Self-Review Checklist

逐项核验确保 plan 覆盖 spec：

- [x] **AC#1** UsageLimits 传 agent.run → Task 4 Step 4.5
- [x] **AC#2** UsageLimitExceeded forensic + 不重试 → Task 5 Step 5.5
- [x] **AC#3** 31 个 `@tool` → Task 2 Step 2.5
- [x] **AC#4** pyproject pin → Task 6 Step 6.3
- [x] **AC#5** create_trader_agent 启动 → Task 2 Step 2.6 + Task 7 Step 7.1
- [x] **AC#6** 9 新测试 PASS → Task 1-6 各自验证
- [x] **AC#7** 0 regression → Task 5 Step 5.7 + Task 7 Step 7.1
- [x] **AC#8** 无 banned-word → Task 7 Step 7.1
- [x] **AC#9** observation candidate memory → Task 7 Step 7.2
- [ ] **AC#10** PR 描述 ROI 表 — *handled outside plan*（PR creation 时由人写 PR body，引用 spec §1.3 表）
- [ ] **AC#11** post-merge memory updates — *handled outside plan*（参见 Post-Merge 段，下次会话起手做）
- [x] **AC#12** TradingDeps 6 字段收紧 → Task 1 Step 1.4
- [x] **AC#13** TradingDeps import 不抛 ImportError → Task 1 Step 1.5 + Task 7 Step 7.1
- [x] **AC#14** pytest --collect-only → Task 1 Step 1.6 + Task 7 Step 7.1

**Tests T1-T9 mapping**：
- T1: Task 4 Step 4.1
- T2-T4: Task 5 Step 5.1-5.3
- T5-T6: Task 2 Step 2.1-2.2
- T7: Task 3 Step 3.1
- T8: Task 1 Step 1.1
- T9: Task 6 Step 6.1

无 placeholder（grep TODO/TBD/FIXME 净）；类型一致（`USAGE_LIMITS_PER_CYCLE` 在 Task 4 定义，Task 5 引用）；步骤完整 + 命令显式。
