# Iter 5 — pydantic-ai 框架合规

**日期**：2026-04-26
**作者**：brainstorming session（Iter 5 起手）
**状态**：spec — 待用户 review 后转 writing-plans

---

## §0 一句话

进观察期前最后一棒，类型保证四件套闭环 —— 在 `agent.run` 调用层补 `UsageLimits`（单 cycle 防爆裂兜底），在 Agent 工厂内启用 `docstring_format='google' + require_parameter_descriptions=True`（CI 硬保护），把 `TradingDeps` 6 个 `object | None` 字段收紧为真实 service 类型（恢复 `RunContext[TradingDeps]` 类型保证），在 `pyproject.toml` 把 `pydantic-ai>=1.0` pin 到 `>=1.78,<2`（防观察期数周内被 minor 升级污染）。

---

## §1 决策背景

### 1.1 来源

- `project_pydantic_ai_compliance.md`（2026-04-22 写）列了 P0 UsageLimits / P1 ModelRetry / P1 Agent.override / P2 logfire+版本固定 全清单
- `project_pre_observation_iterations.md` Iter 5 行写"UsageLimits / ModelRetry+Agent.override / docstring_format='google' Agent 配置启用 + require_parameter_descriptions=True + pydantic-ai 版本固定 + logfire instrumentation"
- `project_tradingdeps_typing_cleanup.md`（2026-04-21）记 TradingDeps 6 个 `object | None` 字段是历史债，原计划独立 PR；用户在 brainstorm 末追加要求一并纳入
- 2026-04-26 brainstorm 对 7 个候选项做 ROI 评估后，scope 缩到 3 项（A + D + E），随后追加 D'（TradingDeps 类型收紧）合 4 项

### 1.2 Memory 校准（重要）

> Memory 写：「`docstring_format='google'` **Agent 配置**启用 + `require_parameter_descriptions=True`」

**实测 pydantic-ai 1.78.0 校准**：这两个参数**只在 `@agent.tool` 装饰器和 `Tool()` 上**——`Agent.__init__` 没有这两个 kwarg。`docstring_format` 默认 `'auto'`（自动识别 google/sphinx/numpy），`require_parameter_descriptions` 默认 `False`。

→ 实施时必须**逐工具**启用，不能 Agent 级一次性开。本 spec §3.2 用 `partial(agent.tool, ...)` helper 集中配置。

### 1.3 七个候选 ROI 评估总结

| 项 | ROI | 决策 | 理由 |
|---|---|---|---|
| **A. UsageLimits** | 🟡 中 | **进 Iter 5** | pydantic-ai default request_limit=50 已 cap 死循环；真正增量是显式声明 + 加 `total_tokens_limit`（外层 daily TokenBudget 是日累积，单 cycle 不阻断）|
| **B. ModelRetry 试点** | 🔴 低 | **推迟** | 观察期前没数据证明 LLM 真的把"错误字符串"当事实推理；试点 = 盲打。改 fact-only 错误 → ModelRetry 还可能伤害"LLM 自主判断"产品立场 |
| **C. Agent.override 测试** | 🔴 低 | **不做** | `TestModel` + `ALLOW_MODEL_REQUESTS=False` 已端到端覆盖；override 仅 fixture API 风格升级，边际价值低 |
| **D. docstring_format + require_parameter_descriptions** | 🟢 中-高 | **进 Iter 5** | `require_parameter_descriptions=True` 是真 CI 硬保护——加新工具忘写 Args 立刻 startup fail，比 `REGISTERED_TOOL_NAMES` drift guard 早一层 |
| **E. 版本 pin `>=1.78,<2`** | 🟢 高 | **进 Iter 5** | 1 行工时；防观察期数周内 minor 升级带行为变化（capability API / UsageLimits 字段 / tool schema 渲染都可能微调）|
| **F. logfire instrumentation** | 🔴 低 | **推迟** | 现有 `ToolCallRecorder` + `tool_call_summary.py` 提供 p50/p95/error_rate；logfire UI 价值取决于观察期实际分析需求（cross-cycle trace）|
| **G. 删 `cli/app.py:141-152` 外层 3 次重试** | ⚪ 不动 | **保留** | 与 ModelRetry 维度不同——外层防"网络瞬时失败 → 整 cycle 跳过"；保留 |
| **D'. TradingDeps 6 个 `object \| None` 字段类型收紧**（追加项）| 🟢 中-高 | **进 Iter 5** | 历史债（注释"avoid circular import"理由失效——`from __future__ import annotations` 已使所有注解 lazy）；`require_parameter_descriptions=True` 启用后，借机把 deps 类型也收齐——参数 schema / deps 类型 / 错误处理三个维度齐闭环 |

**ROI 哲学**：YAGNI + "先 instrument 后 observe" —— B/F 在没观察数据前是盲打，应等观察期数据触发后再启动；E 几乎零工时换最大稳定性，必做；A/D/D' 是真正的"硬保护"层，进 Iter 5 收益密度最高。

### 1.4 总规模

| 文件 | 改动类型 | LOC |
|---|---|---|
| `src/cli/app.py` | A 实施 | ~25 |
| `src/agent/trader.py` | D 实施（partial helper + 31 处装饰器）+ D' 实施（6 imports + 6 注解 + 6 注释删）| ~50 |
| `pyproject.toml` | E pin | 1 |
| `tests/test_usage_limits.py`（新）| T1-T4 | ~60 |
| `tests/test_trader_agent.py` | T5-T7 增强 + T9 drift guard | ~30 |
| `tests/test_pyproject.py`（新）| T8 | ~10 |
| **总** | | **~175** |

预估工时 0.5-1 天 / 单 PR / 单会话 implementation。

---

## §2 Scope

### 2.1 进 Iter 5 的 4 项

- **A**：在 `cli/app.py:run_agent_cycle` 给 `agent.run(...)` 调用传 `usage_limits` + 捕获 `UsageLimitExceeded` 走 forensic 路径
- **D**：在 `trader.py:create_trader_agent` 内部用 `partial(agent.tool, docstring_format='google', require_parameter_descriptions=True)` 抽 helper，31 个 `@agent.tool` 改 `@tool`
- **D'**：把 `trader.py:TradingDeps` 6 个 `object | None` 字段收紧为真实 service 类型（恢复 `RunContext[TradingDeps]` 类型保证）
- **E**：`pyproject.toml:7` `pydantic-ai>=1.0` → `pydantic-ai>=1.78,<2`

### 2.2 不做 / 推迟项

- **B（ModelRetry 试点）+ F（logfire）**：合并写入 1 条新 memory `project_iter5_observation_candidates.md`，记触发条件，spec 提交后顺手新建
- **C（Agent.override 测试）+ G（删外层重试）**：明确不做，无 candidate memory

### 2.3 与现有 memory 关系

- `project_pydantic_ai_compliance.md`（2026-04-22）覆盖 P0/P1/P2 全清单——不动；本 spec 引用它做 audit trail
- `project_tradingdeps_typing_cleanup.md`（2026-04-21）原写"独立 PR，不 bundle"——本 spec 推翻该决策（理由：与 D 协同收益高于隔离收益）；闭环后该 memory 标 ✅ landed
- `project_pre_observation_iterations.md` Iter 5 行——闭环后由 implementation session 更新（ToDoNote: 实施完成后改为 ✅ landed PR #X）

---

## §3 设计

### 3.1 A — UsageLimits + UsageLimitExceeded 异常路径

#### 阈值常量（cli/app.py 顶层）

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

**阈值哲学**：保守宽，"防爆炸不 throttle 业务"。50 calls 留 5x buffer，正常 cycle 几乎不触发；token 300k 防 LLM 单 cycle 输出无限自言自语。具体值观察期 W1 数据出来后单 PR 调紧（1 行常量）。

#### `agent.run` 调用站点改造

`cli/app.py:run_agent_cycle` 现有循环：

```python
# Before
for attempt in range(3):
    try:
        result = await agent.run(prompt, **run_kwargs)
        break
    except Exception as e:
        if attempt < 2: ... retry ...
        else: ... error + return None ...
```

改造为（关键：`UsageLimitExceeded` 在 `Exception` 之前捕获）：

```python
# After
for attempt in range(3):
    try:
        result = await agent.run(
            prompt,
            usage_limits=USAGE_LIMITS_PER_CYCLE,  # 新增
            **run_kwargs,
        )
        break
    except UsageLimitExceeded as e:
        # 病理状态（LLM 死循环 / runaway tools），不重试，写 forensic trace
        logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
        async with get_session(engine) as session:
            session.add(DecisionLog(
                session_id=deps.session_id,
                cycle_id=cycle_id,
                trigger_type=trigger_type,
                decision="usage_limit_exceeded",
                reasoning=str(e)[:500],
                model_used=getattr(model, 'model_name', str(model)) if model else str(agent.model),
                tokens_used=0,  # UsageLimitExceeded raise 时无 result，记 0
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

**关键设计点**：

1. `UsageLimitExceeded` 优先级 > 通用 `Exception`——Python except 顺序保证
2. `decision="usage_limit_exceeded"` 是 decision_logs 新值——观察期可 SQL 直接查命中频次
3. `tokens_used=0` 因为 pydantic-ai 1.78 `UsageLimitExceeded.__init__(self, message: str)` 仅接受 message 参数（实测 inspect 验证），异常对象**不携带 partial usage**——无法从 exception 反查触发时点的累计 token；同时 raise 时无 RunResult 对象可读 usage。**记 0 是设计取舍而非疏漏**：观察期 SQL 用 `WHERE decision='usage_limit_exceeded'` 而非 `WHERE tokens_used=0` 过滤（schema 默认就是 0，二者无法区分）；单 cycle token 实际值需配合 `tool_calls` 表 cycle_id 关联近似估算（每行 duration_ms 隐含调用规模）
4. `model_used` 字段沿用现有 fallback 链（`getattr(model, 'model_name', str(model))`）
5. **`market_summary` 字段故意不填**（schema nullable 允许）——病理 cycle forensic 不应再触发外层 service 调用（否则可能再次触发同源问题，如 LLM 卡 retry 循环时 market_data 也可能受影响）；事后调查的市场状态可通过 `tool_calls.cycle_id` 关联同 cycle 内已成功调用的 `get_market_data` 输出反查

#### import 影响

`cli/app.py` 顶部新增：
```python
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
```

#### 与现有 budget 路径的关系

```
budget.exhausted (daily) → return None     (现有，不动)
  ↓ 否
agent.run(usage_limits=PER_CYCLE)
  ├─ 正常 → budget.record(tokens) → ...    (现有，不动)
  ├─ UsageLimitExceeded → forensic + return None    (新)
  └─ 其它 Exception → 3 次重试 → return None    (现有，不动)
```

两层独立：daily budget 是用户配的成本上限，per-cycle limit 是病理保护。

### 3.2 D — Tool helper + docstring config

#### partial helper

`trader.py:create_trader_agent` 内 Agent 创建后 + tool 定义前：

```python
def create_trader_agent(model, persona_config) -> Agent[TradingDeps, str]:
    from src.services.tool_call_recorder import ToolCallRecorder
    from functools import partial

    system_prompt = generate_system_prompt(persona_config)
    agent = Agent(
        model,
        deps_type=TradingDeps,
        output_type=str,
        instructions=system_prompt,
        capabilities=[ToolCallRecorder()],
    )

    # Iter 5 §3.2: 启用 google docstring 显式声明 + 强制 Args 完整性。
    # require_parameter_descriptions=True 在 tool 加载时校验，缺 Args 立即 startup fail。
    tool = partial(agent.tool, docstring_format="google", require_parameter_descriptions=True)

    @tool
    async def get_market_data(...): ...
    # ...剩余 30 处全部 @agent.tool → @tool
```

#### 兼容性 sanity check（不需 probe）

`Agent.tool` 签名 `(self, func: ... | None = None, /, *, ...)` 是标准双模式装饰器——`partial` 绑定 keyword 参数后必然同时支持 `@tool` 和 `@tool(name=...)` 两种调用形式。Implementation Step 1 直接按下面写代码即可，无需独立 probe：

```python
tool = partial(agent.tool, docstring_format="google", require_parameter_descriptions=True)

@tool                       # 裸形式（推荐，少 31 对括号）
async def get_market_data(...): ...
```

若意外不工作（极不可能），见 §6.1 fallback。

#### Require validation 启动校验

`require_parameter_descriptions=True` 启用后，pydantic-ai 在 tool 加载时校验。若**任一**有参工具的 Args 不齐 → `Agent` 构造时 raise → 应用启动失败。

**Iter 4 已对全部有参工具 audit 完 Google docstring + Args 段**，理论上启动通过。但**不预设审计结果**——让 startup fail-fast 自己来报哪个工具缺 Args：

```bash
python -c "from src.agent.trader import create_trader_agent; from src.config import PersonaConfig; create_trader_agent('test', PersonaConfig())"
```

启动正常返回 = OK；抛 → 错误信息会指出缺 Args 的工具名 → **立即先补 Args** 再继续后续 step。

### 3.3 D' — TradingDeps 6 字段类型收紧

#### 现状（trader.py:28, 34-38）

```python
@dataclass
class TradingDeps:
    ...
    approval_gate: object | None = None    # L28: ApprovalGate instance
    ...
    metrics: object | None = None          # L34: MetricsService, typed as object to avoid circular import
    news: object | None = None             # L35: NewsService, typed as object to avoid circular import
    macro: object | None = None            # L36: MacroService; typed as object to avoid circular import
    crypto_etf: object | None = None       # L37: CryptoEtfService; typed as object to avoid circular import
    onchain: object | None = None          # L38: OnchainService; typed as object to avoid circular import
```

注释里"avoid circular import"理由失效——`trader.py:1` 已有 `from __future__ import annotations`，所有注解 lazy 字符串化。

#### 真实类路径（实测 grep 验证）

| 字段 | 真实类 | 模块 |
|---|---|---|
| `approval_gate` | `ApprovalGate` | `src.cli.approval` |
| `metrics` | `MetricsService` | `src.services.metrics` |
| `news` | `NewsService` | `src.integrations.news.service` |
| `macro` | `MacroService` | `src.integrations.macro.service` |
| `crypto_etf` | `CryptoEtfService` | `src.integrations.crypto_etf.service` |
| `onchain` | `OnchainService` | `src.integrations.onchain.service` |

#### 循环风险预检（spec 写时已 grep 完成）

```bash
grep -rln "from src.agent.trader\|import src.agent.trader" src/
# → tools_execution.py / tools_perception.py / tools_memory.py / cli/app.py / services/tool_call_recorder.py
```

**6 个 service 模块均不在反向引用列表**——无循环风险，**0 个 TYPE_CHECKING 守卫需要**。memory 写"可能 2-3 个字段需 TYPE_CHECKING 守卫"是过度防御，spec 实测推翻。

#### 改造（仅 trader.py）

```python
# 顶部 imports 新增
from src.cli.approval import ApprovalGate
from src.integrations.crypto_etf.service import CryptoEtfService
from src.integrations.macro.service import MacroService
from src.integrations.news.service import NewsService
from src.integrations.onchain.service import OnchainService
from src.services.metrics import MetricsService

# TradingDeps 6 行修改（删注释 + 收紧类型）
@dataclass
class TradingDeps:
    ...
    approval_gate: ApprovalGate | None = None
    ...
    metrics: MetricsService | None = None
    news: NewsService | None = None
    macro: MacroService | None = None
    crypto_etf: CryptoEtfService | None = None
    onchain: OnchainService | None = None
```

**改动范围**：6 imports + 6 type 注解 + 6 注释删 = 共 ~15 行 trader.py 改动，**0 行其它文件改动**。

#### 行为不变性保证

- 所有 `ctx.deps.<obj>` 调用点已经在运行时 duck typing 工作——收紧类型只是补静态契约，不改运行时
- `None = None` 默认值不变——既存 None 路径（如 `if deps.metrics is not None:` 守卫）仍合法
- 现有 848 测试全绿是 acceptance（参见 §4 T9 + AC#7）

### 3.4 E — pyproject.toml 版本 pin

```diff
-    "pydantic-ai>=1.0",
+    "pydantic-ai>=1.78,<2",
```

**理由**：
- 当前实测 1.78.0 → floor `>=1.78` 接受所有 1.78.x patches（小修小补 OK）
- ceiling `<2` 防 2.0 major 升级带 breaking change
- minor 升级 `1.79+` 在观察期内**也被屏蔽**——避免 capability API / UsageLimits 字段 / tool schema 渲染微调污染观察数据

**uv.lock 同步（必做，非可选）**：
- **PR 一并推 uv.lock**：实施时 `uv lock --upgrade-package pydantic-ai` 强制重解该包到当前实测 1.78.0 patch level 写死在 lock 文件里（裸 `uv lock` 在 lock 已是 1.78.0 时可能不触发重写，`--upgrade-package` 显式强制）；单纯改 `pyproject.toml` constraint 而不更新 lock，新 clone 的环境会按 constraint 解 floor 从而装到任意 1.78.x（实际可能拿到 1.78.5+ 行为微变）
- **观察期 onboarding 用 `uv sync --frozen`**：强制按 lock 文件装版本，拒绝 floor 解析；防止"团队成员新机器跑 `pip install -U` 绕过 constraint 装到 1.79+"

---

## §4 测试

### 4.1 新建 `tests/test_usage_limits.py`

| # | 测试 | 输入 | 断言 |
|---|---|---|---|
| **T1** | `test_usage_limits_passed_to_agent_run` | mocker spy 包 `agent.run`，跑 stub cycle | `agent.run` 被调用时 kwargs 含 `usage_limits` 且 == `USAGE_LIMITS_PER_CYCLE` |
| **T2** | `test_usage_limit_exceeded_writes_forensic_decision_log` | mock agent.run 第 1 次 raise `UsageLimitExceeded("test reason")` | log error 命中 / `decision_logs` 表多 1 行 `decision="usage_limit_exceeded"` & `reasoning="test reason"` & `tokens_used=0` / `run_agent_cycle` 返回 None |
| **T3** | `test_usage_limit_exceeded_does_not_retry` | 同 T2 + spy 数 agent.run 调用次数 | agent.run 仅被调 1 次（不进 `range(3)` 重试） |
| **T4** | `test_generic_exception_still_retries_3_times` | mock 前 2 次 raise `NetworkError`，第 3 次成功 | agent.run 被调 3 次 / 函数正常返回 result（不被 UsageLimitExceeded 路径误捕） |

### 4.2 增强 `tests/test_trader_agent.py`

| # | 测试 | 输入 | 断言 |
|---|---|---|---|
| **T5** | `test_all_tools_use_google_docstring_format` | 加载 agent，遍历 `agent._function_toolset.tools` | 每个工具的 ToolDefinition `docstring_format == 'google'`（pydantic-ai 1.78 公开 API：tool registration 后能从 toolset 反查 docstring_format）|
| **T6** | `test_all_tools_require_parameter_descriptions` | 同上 | 每个工具 `require_parameter_descriptions == True` |
| **T7** | `test_missing_args_triggers_startup_fail` | mocker patch 1 个工具的 docstring 删掉 Args 段 → 重建 agent | raise（具体异常类型由 pydantic-ai 决定，断言 raise 即可）|
| **T8** | `test_trading_deps_no_object_typed_service_fields` | `typing.get_type_hints(TradingDeps)` | `approval_gate / metrics / news / macro / crypto_etf / onchain` 6 字段的 `__args__` 不含 `object`（drift guard 限定保护**这 6 个特定字段**；未来加新 deps 字段不会被本测试覆盖——是有意的窄化，避免误伤合法 `Callable` / `object` 用法 ）|

**T5/T6 注意**：实测 1.78 私有 API `agent._function_toolset.tools[name].docstring_format` / `.require_parameter_descriptions` 可读（`Tool` 对象暴露这两字段）；测试直接用此路径。备注"私有 API 1.79+ 改名风险"——届时改写为 `partial.keywords` 间接验证。

**T8 实现细节**：用 `typing.get_type_hints` 强制解析 `from __future__ import annotations` 的字符串注解；遍历 6 个 expected typed 字段名（硬编码列表），对每个字段断言 `object not in get_args(hint)`。

### 4.3 新建 `tests/test_pyproject.py`

| # | 测试 | 输入 | 断言 |
|---|---|---|---|
| **T9** | `test_pydantic_ai_pinned_to_minor_floor_below_v2` | parse `pyproject.toml`（`tomllib` stdlib）| `dependencies` 中 pydantic-ai constraint **同时含 `>=1.78` 且含 `<2`**（双断言：未来若有人改回 `>=1.0,<2` 解 floor pin，本测试仍能捕获）|

**ROI 边缘但纳入**：单测层防止未来 floor / ceiling 任一被解开（drift guard 与 `REGISTERED_TOOL_NAMES` 同维度）。

### 4.4 测试规模预期

848 baseline → **857 passed**（+9 新：T1-T9，0 regression）。

### 4.5 不写的测试（YAGNI）

- ❌ UsageLimits 各阈值边界（49 vs 50）——pydantic-ai 自有测试，重复
- ❌ partial helper 单测——属 implementation detail，由 T5/T6 间接覆盖
- ❌ E 的"实际安装版本测试"——不可在单测层验证 dep resolution；T9 只验声明的 constraint

---

## §5 验收标准（Acceptance Criteria）

### 5.1 功能 AC

- **AC#1**: `cli/app.py:run_agent_cycle` 内 `agent.run(...)` 调用传 `usage_limits=USAGE_LIMITS_PER_CYCLE`
- **AC#2**: `UsageLimitExceeded` 路径写 `decision_logs` 1 行 `decision="usage_limit_exceeded"`，函数返回 None，**不进** 3 次重试
- **AC#3**: `trader.py:create_trader_agent` 内 31 个 `@agent.tool` 全部改用 helper `@tool` 形式（注：`grep -c "@agent.tool" src/agent/trader.py` 返回 32，因 L588 的 `REGISTERED_TOOL_NAMES` 头注释包含 `@agent.tool` 文本；真实装饰器数 31 与 `REGISTERED_TOOL_NAMES` 长度一致——AC 验证用 `agent._function_toolset.tools` 长度 == 31）
- **AC#4**: `pyproject.toml` 含 `pydantic-ai>=1.78,<2`
- **AC#5**: `python -c "from src.agent.trader import create_trader_agent; from src.config import PersonaConfig; create_trader_agent('test', PersonaConfig())"` 启动不抛异常（即所有有参工具 Args 段全过 require validation；如启动失败，错误信息会指出具体缺 Args 的工具，先补 Args 再继续）
- **AC#12**: `trader.py:TradingDeps` 6 个 service 字段（`approval_gate / metrics / news / macro / crypto_etf / onchain`）类型从 `object | None` 收紧为对应真实类型 `| None`；6 个误导性注释（"typed as object to avoid circular import"）全部删除
- **AC#13**: `python -c "from src.agent.trader import TradingDeps"` 启动不抛 ImportError（验证 6 个 service 模块 import 链无循环）
- **AC#14**: `pytest --collect-only` 不报 collection error（最广 import 链 sanity——所有测试文件 import 全过 = 整个项目模块图无破坏）

### 5.2 质量 AC

- **AC#6**: 9 个新测试全 pass（T1-T9）
- **AC#7**: 现有 848 测试 0 regression
- **AC#8**: 无 banned-word（fact-only regression test 不动也应通过——本 PR 不动 tool 输出）

### 5.3 文档 AC

- **AC#9**: 顺手新建 `project_iter5_observation_candidates.md` 记 B/F 触发条件
- **AC#10**: PR 描述列出"7+1 候选 ROI 评估总结表"（含 D' 追加项；让 reviewer 看到决策路径）
- **AC#11**: PR merge 后更新 `project_pre_observation_iterations.md` Iter 5 行标 ✅ landed + squash hash + 同步 `project_tradingdeps_typing_cleanup.md` 标 ✅ landed

---

## §6 风险 / Fallback

### 6.1 partial 不兼容 `@agent.tool` 裸形式（极不可能）

**前提**：`Agent.tool` 是标准双模式装饰器（`func: ... | None = None` positional-only），`partial` + keyword 绑定理论上必然兼容两种形式。本节仅作 paranoia fallback。

**症状**：实施 Step 2b 写完 `@tool` 形式跑测试报 `TypeError` / 装饰器异常
**Fallback**：所有 31 处改用 `@tool()` 形式（带空括号）——确定可工作
**判定**：跑测试 pass / fail = binary，无需独立 probe

### 6.2 require_parameter_descriptions 启用导致启动 fail

**症状**：Step 2b 完成后启动检查抛异常，错误信息指出某工具 Args 段缺
**Fallback**：先补 Args 段（不脱离本 PR scope，因为这正是 require 设计意图），再继续后续 task。Iter 4 已 audit 全部有参工具，理论概率低；**不预设审计结果**——让 startup fail-fast 自报具体工具名

### 6.3 T5/T6 私有 API 在 1.79+ 改名（极小概率）

**前提**：实测 1.78 `agent._function_toolset.tools[name].docstring_format` / `.require_parameter_descriptions` 均可读（`Tool` 对象暴露这两字段）；T5/T6 直接走此私有路径。
**症状**：未来 1.79+ patch 改 `_function_toolset` 内部命名 → T5/T6 失败
**Fallback**：T5/T6 改写为间接验证 `partial.keywords`（断言 helper 注册时配置含 `docstring_format='google'` + `require_parameter_descriptions=True`），等同覆盖意图。
**判定**：仅在 1.79+ 升级时再考虑，不阻塞当前 PR。

### 6.4 Memory 校准的二次失误

Memory 已被本 spec §1.2 校准（per-tool not Agent-level）。implementation 不再走"先试 Agent-level 再 fallback per-tool"路径——直接按 §3.2 partial helper 实施。

### 6.5 D' 实施时发现循环 import

**症状**：spec 写时 grep 验证 6 个 service 模块均不反向引用 trader，但若实施时某 service **新增** import 链（如 `MetricsService` 内部又 import 了 `tools_perception` 间接拉到 trader），会触发 ImportError。
**Fallback**：对该字段改用 `TYPE_CHECKING` 守卫——把 `from xxx import YyyService` 移到 `if TYPE_CHECKING:` 块内，注解仍直接写 `metrics: MetricsService | None = None`（`from __future__ import annotations` 已使所有注解 lazy 字符串化，运行时不解析；TYPE_CHECKING 块仅供 mypy/pyright 静态分析读类型）。代价：mypy 仍可正常检查，仅运行时 `isinstance(deps.metrics, MetricsService)` 类调用不可用——但项目内无此用法。
**判定**：Step 实施时 `python -c "from src.agent.trader import TradingDeps"` 失败 = 切此 fallback。

---

## §7 观察期 follow-up

### 7.1 阈值校准（W1 末）

观察期第一周末跑：
```sql
SELECT cycle_id, COUNT(*) as tool_calls FROM tool_calls
WHERE created_at >= datetime('now', '-7 days')
GROUP BY cycle_id ORDER BY tool_calls DESC LIMIT 20;
```

中位数 + p95 + max 对比 `USAGE_LIMITS_PER_CYCLE` 阈值——若 max 远小于 50（如 max=15），单 PR 调紧到 `tool_calls_limit=25` 等。

### 7.2 病理 cycle 调查

观察期任意时刻：
```sql
SELECT * FROM decision_logs WHERE decision='usage_limit_exceeded';
```

每出现 1 行 = 1 次病理 cycle，需读 `cycle_id` 对应的 tool_calls + decision_log.reasoning 调查根因（哪个 tool 卡循环 / 哪类 LLM 状态出现 runaway）。

### 7.3 B/F 触发条件（见 candidate memory）

- **B（ModelRetry 试点）触发**：观察期 W2+ SQL 查到"LLM final output 把 tool 错误字符串当事实推理 ≥3 例"——典型 pattern：`tool_calls.status='error'` + 同 cycle decision_logs.reasoning 含 "unavailable so I'll wait" / "no data therefore" 类语句
- **F（logfire）触发**：观察期 W1 结束决策——若 `tool_call_summary.py` + DB SQL 已够，不接；若需 cross-cycle trace（"为何这一系列 cycle 都没 set SL"因果分析），启动 logfire

---

## §8 不在本 spec 内的议题（明确不做）

- **C. Agent.override 测试重构**：现 `TestModel` 已端到端覆盖，边际低
- **G. 删 cli/app.py 外层 3 次 exponential backoff**：维度不同，保留
- **Iter 4 6 项 minor follow-up**（见 `project_iter4_followups`）：观察期数据驱动

---

## §9 实施顺序提示（给 writing-plans）

1. **Step 1**：D' 实施（TradingDeps 6 imports + 6 注解 + 6 注释删）→ `python -c "from src.agent.trader import TradingDeps"` 验证无循环 import → `pytest --collect-only` 验证整个项目模块图无破坏（AC#13 + AC#14）
2. **Step 2**：D 实施（trader.py 31 处 `@agent.tool` → `@tool`，写入 `partial` helper）→ `python -c create_trader_agent('test', PersonaConfig())` 启动检查（AC#5）；若启动 fail，错误信息会指出缺 Args 的工具，立即先补 Args 再继续
3. **Step 3**：A 实施（cli/app.py 顶部 imports + `USAGE_LIMITS_PER_CYCLE` 常量 + run_agent_cycle try/except 顺序：UsageLimitExceeded 优先于通用 Exception）
4. **Step 4**：E 实施（pyproject.toml 1 行 + `uv lock` 重生 lock 文件 + 把 lock 改动一并入 PR）
5. **Step 5**：测试（T1-T9）
6. **Step 6**：candidate memory 新建（`project_iter5_observation_candidates.md`）
7. **Step 7**：MEMORY.md index + tradebot_status / pre_observation_iterations / tradingdeps_typing_cleanup 更新（merge 后）

**为什么 D' 在 D 之前**：D 启用 `require_parameter_descriptions=True` 时若有参工具 Args 段全 OK 则启动通过——独立于 D'。但 D' 改 TradingDeps 注解后若意外引发循环 import，问题表现是 `from src.agent.trader import ...` 失败——会**同时阻塞**所有 D/D'/T5-T9 的 acceptance 验证。先做 D' 让 import 风险最早暴露。

每 step 一次 review pair（spec compliance + code quality）。预估 7 step × ~10 分钟 / step = 1-1.5 小时净工时（含 review）。
