# Iter W2-R2-5 — RuntimeConfig 抽象 + set_next_wake usage clarity + P0-5 wontfix justification

**Date**: 2026-05-01
**Branch**: `feature/iter-w2r2-5-set-next-wake-clarity`
**Source**: `.working/sim4-issues-inventory.md §P0-5` / `.working/all-pending-needs.md` Tier 1 R2-5
**前置依赖**: 无（独立 PR，不依赖其他 R2 议题）
**预估工作量**: 1.5 小时（含 spec / plan / impl / TDD / review-before-commit / merge）

---

## 1. 背景与动机

### 1.1 P0-5 现象（sim #4 实证）

`sim #4` cycle `09353c07` (16:46:42) agent 调用 `set_next_wake(120)` →
下一 cycle `2e4fba15` (18:02:08) 由 **alert** 唤醒，间隔 75 分钟 49 秒。
中间 17:16 / 17:46 / 18:16 三个 30min scheduler 兜底点全部跳过。

**inventory 原始定级 🔴 P0**，列三大严重性：
1. 风控（agent 持仓时设长 wake → 期间无人盯盘）
2. 观察期数据空洞（30min 监控密度无法保证）
3. agent prompt 引导失效（agent 完全控制系统时间感知）

### 1.2 brainstorm 阶段（2026-05-01）的根因校准 + 哲学审视

经多轮 brainstorm 重新审视后，**P0-5 实际是机制按设计工作，不是机制失败**：

**证据链（DB-verified）**:
- agent 16:46 判断"FOMC 已过、市场安静" → set_next_wake(120) 是**合理决策**
- 期间无 fill / 无重大事件 / SL 在岗
- 75min 处 alert **自然**触发提前唤醒（系统按设计响应市场变化）
- 没有任何坏后果发生

**inventory 列的"严重性"全是 hypothetical**（"如果...就会..."），不是 empirical（"已经出了什么"）。
这与 P0-1（10.7% biz error 被吞）/ P0-3（decision 三义混合）完全不同——后者是**已发生的数据污染**。

**设计哲学一致性检验**:

> 系统提供丰富的工具库，希望 agent 能模拟真实世界的交易员工作流（项目终态目标）

| 现实交易员 | TradeBot 系统的对照 |
|---|---|
| 设 SL/TP，让经纪商保护 | `set_stop_loss` / `set_take_profit` |
| 设 price alert，手机响铃才看 | `set_price_alert` / `add_price_level_alert` |
| 设 conditional order（fill 通知） | `place_limit_order` + `_dispatch_fill_event` |
| **不需要老板每 30min 强制叫醒** | **scheduler 30min 强制兜底** ← 与哲学冲突 |

强制 30min 兜底**违背设计哲学**。alert + SL/TP + fill 通知就是 agent 的"手机响铃"——
真实交易员工作流。

**已有断路器核对**（`src/cli/app.py:615`）:

```python
max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
#                                                      ↑
#                                          绝对硬天花板
```

| `scheduler_interval_min` | `max_wake` |
|---|---|
| 15（默认） | 60 |
| 30（sim #4 配置） | 120 |
| 45+ | 180 |

agent `set_next_wake(9999)` → tool 层 clamp 到 `wake_max_minutes` →
**任何配置下不超过 180 min**。极端失控防线已在岗。

3 小时硬上限对夜间 / 周末 / FOMC 后等安静行情合理；agent 真要 set_next_wake(180+)
大概率 prompt 故障，被 cap 到 180 后下 cycle 异常 reasoning 可观测，alert 系统兜底。

**真实风险源转给 P1-4**（R:R 漂移 + SL 哲学）——长 wake 期间无人盯盘的真实风险源是 trailing stop
设计议题，不是 scheduler 议题。**用 scheduler 兜底掩盖 P1-4 是错位修补**。

### 1.3 brainstorm 决议

- **D1 (P0-5 重新评估为 wontfix - by design)**: scheduler 不改，180min 硬天花板已是合理断路器。
- **D2 (P0-5 inventory 状态校准为 wontfix justification)**: 在 `.working/sim4-issues-inventory.md §P0-5` 加补注，链向 P1-4 作真实风险源，不进 git（运行文档）。
- **D3 (R2-5 scope pivot 至 set_next_wake fact-only refresh)**: 借此议题清理一处 N5 漏网（"Shorten when... lengthen when..."）+ 让 agent 看到精确 session bound。
- **D4 (Layer 1 加 cross-tool bullet)**: set_next_wake 与 alert/fill/conditional 触发器的关系是真实 cross-tool behavior，落 Layer 1 不破 Iter 4 DRY 纪律。
- **D5 (动态 session-aware bound)**: bullet 渲染 `1-{wake_max}`（实际 session max），不用 `1-180` envelope（含义对默认配置 misleading）。
- **D6 (RuntimeConfig dataclass 抽象)**: 不直接给 `generate_system_prompt` 加 kwarg，引入 `RuntimeConfig` 作为"session-fixed prompt 输入"语义边界，未来加字段（`scheduler_interval_min` / `exchange_type` / etc）零签名 churn。
- **D7 (per-cycle 动态注入留 R2-8)**: N10 reasoning 注入是 per-cycle 通道，与 RuntimeConfig（session-start）正交，不在本 PR scope。
- **D8 (clamp 反馈不增强)**: Layer 1 已暴露精确 bound，无需 clamp 消息再次显示 session range；保持现有简洁版。
- **D9 (fact-only 漏网清理仅限 set_next_wake)**: Open/Close fill response 含轻微 prescription 但争议小，留未来"N5 第二轮 audit"议题。

### 1.4 token 经济成本论证（2b 路径胜出 2c 的关键依据）

cycle 间 pydantic-ai 无消息历史传递（pydantic-ai 每次 `agent.run` 独立，message history 不跨 cycle 传递；
N10 R2-8 才补此洞）。所以"agent 首次试错被 clamp 后学到 session range"的承诺，**仅在单 cycle 内成立**；
下个 cycle agent 重新无知，可能再次试错。

参考 sim #4 P1-2 set_price_alert 实证（详 `.working/sim4-issues-inventory.md §P1-2`）：
**8 次 round-trip × ~6k token = ~48k token 浪费**（4 次失败提交 × 2 calls/失败：fail 调用 +
retry 调用），跨 4 cycle 重复触发（每 cycle 都犯同样错），印证 cycle 间无记忆。

W2 24-48h（~150 cycles）下，假设 agent 启发式不稳定 10% cycle 越界（每越界一次产生 1 次额外
retry call）；按 Iter 8 audit 5 sample 实测 per-call ~9k token（包含 prompt + tool defs +
message history，cache miss 计费）：
150 × 10% × ~9k token/extra retry call = **~135k tokens 浪费**（最坏可达百万级）。

> **基数说明**：sim #4 引证用 ~6k/call（inventory 估算），W2 估算用 ~9k/call（Iter 8 audit
> 实测）；两者范围一致量级，9k 是更保守 / 包含完整 prompt 结构的估计。结论数量级不变。

vs Option β `RuntimeConfig` plumbing 一次性 ~40 行——ROI 完全反转，β 胜出。

**论证闭环**：β 之所以能解决跨 cycle 无记忆的浪费，**关键不是它"跨 cycle 持久化"**——
而是它把 bound 信息放进 **system_prompt（Layer 1）**，而 system_prompt **每 cycle 都重新注入**。
agent 每 cycle 起手就看到精确 session range，无需依赖前 cycle 的 message history 记忆。
这与 R2-8 N10（per-cycle 注入前 cycle reasoning）正交——R2-5 解决 session-fixed 信息的渲染，
N10 解决 per-cycle dynamic 信息的注入。

---

## 2. 设计目标

### 2.1 In-scope

| # | 改动 | 文件 |
|---|---|---|
| **G1** | 新增 `RuntimeConfig` frozen dataclass（含 `wake_max_minutes: int = 60` 默认值）+ docstring 明确语义边界（session-fixed vs persona vs per-cycle） | `src/agent/persona.py` |
| **G2** | `_build_layer1` 签名改 `_build_layer1(runtime: RuntimeConfig)`；新增 `Wake interval control` cross-tool bullet（fact-only + 动态 `1-{wake_max}`） | `src/agent/persona.py` |
| **G3** | `generate_system_prompt` 签名改 `(persona, runtime: RuntimeConfig \| None = None)`；`runtime = runtime or RuntimeConfig()` 默认值兜底 | `src/agent/persona.py` |
| **G4** | `create_trader_agent` 签名改 `(model, persona_config, runtime: RuntimeConfig \| None = None)`，转传至 `generate_system_prompt` | `src/agent/trader.py` |
| **G5** | `cli/app.py` **路径 A 跨函数装配**：`build_services` 内（line 438 收尾后、439 前）算 `max_wake` + 构造 `RuntimeConfig` 传入 `create_trader_agent`；`build_services` 内（line 507 后、509 前）装 `deps.wake_min_minutes=1` + `deps.wake_max_minutes=max_wake`；`run` 内删 line 615-617 重复装配 + 改写 line 614 注释，仅保留 line 618 `deps.set_next_wake_fn`（依赖 scheduler 实例必须留 run） | `src/cli/app.py` (build_services 397-524 + run 611-618) |
| **G7** | `trader.py` `set_next_wake` wrapper docstring 改写（删 `Shorten when... lengthen when...` 决策暗示 + 引用 Layer 1 "Wake interval control"） | `src/agent/trader.py:567-585` |
| **G8** | drift guard：`_build_layer1` 含 `Wake interval control` 标题 + `1-{wake_max}` 渲染 + `Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting` 关键句 | `tests/test_persona.py` 新增 |
| **G9** | drift guard：`generate_system_prompt(persona)` 单参（无 runtime）等价于 `generate_system_prompt(persona, RuntimeConfig())`，渲染默认 `1-60` | `tests/test_persona.py` 新增 |
| **G10** | drift guard：N5 wordlist verification — `agent._function_toolset.tools["set_next_wake"].tool_def.description` 不含 `\bshorten when\b` / `\blengthen when\b`。**参考路径风格** `tests/test_trader_agent.py:210-211`（同源访问 `agent._function_toolset.tools[name].tool_def.<attr>`，但 line 210-211 用 `.parameters_json_schema` 校验 Args 段，本 G10 用 `.description` 校验首段——理由见 §3.6.1） | `tests/test_persona.py` 新增 |
| **G11** | drift guard：`_build_layer1(runtime)` 渲染含动态值——传入 `RuntimeConfig(wake_max_minutes=120)` 时 bullet 必须显 `1-120` 而非 `1-60`（120 = sim #4 实际 30min 配置下的 wake_max，比极端值 180 更贴运维场景） | `tests/test_persona.py` 新增 |
| **G12** | **现有测试更新**：`test_layer1_bullet_count_5` → `test_layer1_cross_tool_bullet_count`，`assert bullet_count == 5` → `assert bullet_count == 6`（加第 6 bullet 后必修，否则 deterministic fail） | `tests/test_persona.py:246-258` |
| **G13** | `.working/sim4-issues-inventory.md §P0-5` 加 wontfix 补注（含 brainstorm 路径浓缩 + 链向 P1-4） | inventory（不进 git） |
| **G14** | `.working/all-pending-needs.md` 更新 R2-5 状态（wontfix scheduler + docstring polish landed） | inventory（不进 git） |

**G6 已剔除**（原"verification-only"项移到 §3.4 段内说明，避免 in-scope 表语义混淆——in-scope 表只列**有 diff 的改动**，verification 不算改动）。

### 2.2 Out-of-scope（不做项 + 何时做）

| 议题 | 不做理由 | 何时做 |
|---|---|---|
| **scheduler 30min 硬兜底**（A/B/D 任一） | brainstorm 决议 D1：现机制按设计工作 + 180min 断路器已在岗 + 违反"模拟真实交易员"哲学 | 永不（除非项目哲学被推翻） |
| **降低 wake_max 公式上限**（A-strict 派生议题） | 同上 + 限制 agent 合法长 wake 表达（如夜间安静） | 永不 |
| **clamp 消息暴露 session range** | Layer 1 已暴露 1-{wake_max}，clamp 二次显示冗余；保留简洁版降低反馈 token | 永不（除非 Layer 1 信息被剥离） |
| **scheduler_interval_min 注入 RuntimeConfig** | 当前无具体 prompt 使用场景；本 PR 仅落地一个最小 RuntimeConfig 验证抽象 | 当 agent prompt 需感知监控节拍时（W2 数据驱动） |
| **exchange_type / symbol / timeframe 注入 RuntimeConfig** | 同上 — 当前由工具调用返回，无 prompt 静态需求 | 实盘准备 / W2 数据驱动 |
| **per-cycle 动态注入（N10 reasoning）** | 不同通道（per-cycle vs session-start）；与 R2-8 N10 议题正交 | R2-8 |
| **Open/Close fill response bullet N5 audit** | 含轻微 prescription 但争议小；属"N5 第二轮 audit"议题，不在本 PR | 观察期数据驱动 |
| **clamp 边界值修改**（提高 wake_min / 降低 wake_max） | 与本议题正交；`set_next_wake(0)` clamp 至 1 是合理 fact，不是 bug | 永不（除非边界本身错） |
| **set_next_wake schema constraint**（Pydantic Field） | 沿用 R2-1 soft-constraint 纪律 — 工具是 fact-provider 不是 guard，agent 学习信号优先 | 永不（除非纪律推翻） |
| **取消 wake_max 公式（固定 180）** | 与本议题正交；公式 `4 * scheduler_interval_min` 仍合理（短间隔配置不该容许长 wake） | 永不（除非配置语义重设） |

### 2.3 设计哲学（沿袭项目纪律）

- **soft-constraint 纪律（memory `feedback_observation_period_soft_constraint`）**：
  - §1（**首要**）: 不加 schema constraint —— set_next_wake 不引入 Pydantic Field
  - §2: 执行类工具优先 explicit reject 而非 silent clamp —— set_next_wake 现状 silent clamp 是历史，不在本 PR 翻案
- **fact-only 哲学（memory `project_n5_label_cleanup`）**：工具输出 + Layer 1 bullet 仅含事实，不含决策暗示
- **Layer 1 DRY 纪律（Iter 4 PR #25）**：cross-tool behavior 落 Layer 1，单工具描述落 docstring；本 PR 加的 bullet 是 cross-tool（与 alert/fill/conditional 关系），合规
- **plan/spec 文档先于代码（memory `feedback_plan_doc_commit_first`）**：spec 先独立 commit，再 impl

---

## 3. 实施细节

### 3.1 `src/agent/persona.py` — RuntimeConfig + signature 改造

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
    Layer 3: Strategy Preferences — what style to trade
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

**设计要点**:
- `RuntimeConfig` `frozen=True` 防意外突变（与 PersonaConfig 风格不同——PersonaConfig 是 BaseModel，但 frozen dataclass 更适合 prompt 输入这种"装配后只读"语义）
- 默认值 `wake_max_minutes=60` 对应**默认 15min scheduler 配置的 wake_max**（符合现有逻辑），让无显式 RuntimeConfig 的测试 / 临时调用渲染合理默认
- Layer 1 第 6 个 bullet（新增），保持现有 5 个 bullet 顺序与措辞不变
- `runtime.wake_max_minutes` f-string 嵌入，渲染 session 实际值
- **字段 docstring 走 PEP 257-外约定**（属性赋值后跟三引号字符串字面量）：pyright / Sphinx / griffe 静态工具识别，但 Python runtime 不绑定到 `__doc__`——`inspect.getdoc(RuntimeConfig.wake_max_minutes)` 返回 `None`。这是 dataclass 字段文档的**常规处理**，spec 文档参考 [PEP 257 § Other Conventions](https://peps.python.org/pep-0257/#what-is-a-docstring)。如未来需 runtime 反射，改 `field(metadata={"doc": "..."})` 形式

### 3.2 `src/agent/trader.py:48-69` — create_trader_agent 签名

```python
def create_trader_agent(
    model: str,
    persona_config: PersonaConfig,
    runtime: RuntimeConfig | None = None,
) -> Agent[TradingDeps, str]:
    from src.services.tool_call_recorder import ToolCallRecorder
    from src.services.model_manager import get_optimal_settings

    system_prompt = generate_system_prompt(persona_config, runtime)
    # ... 余不变
```

`from src.agent.persona import RuntimeConfig` 顶部加 import。

### 3.3 `src/cli/app.py` — RuntimeConfig 装配（路径 A：跨函数迁移到 build_services）

**现状的跨函数顺序冲突**：

| 函数 | 行号 | 当前代码 |
|---|---|---|
| `build_services` (397-524) | 439 | `agent = create_trader_agent(model=result.model, persona_config=result.persona)` |
| `build_services` | 490-507 | `deps = TradingDeps(...)` |
| `build_services` | 524 | `return exchange, deps, agent, budget` |
| `run` (527+) | 611-612 | `scheduler = Scheduler(interval_seconds=interval, callback=on_tick)` |
| `run` | 615 | `max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)` |
| `run` | 616-617 | `deps.wake_min_minutes = 1` / `deps.wake_max_minutes = max_wake` |
| `run` | 618 | `deps.set_next_wake_fn = lambda minutes: scheduler.set_next_interval(minutes * 60)` |

**关键事实**: `max_wake` 在 `run` 局部，`deps` / `agent` 在 `build_services` 创建并 return；
两者跨函数，**`build_services` 的局部变量无法被 `run` 直接引用**。

**两条候选路径**：

- **路径 A**（推荐）：把 `max_wake` 计算 + `RuntimeConfig` 构造 + `deps.wake_min/max_minutes` 赋值全部上移到 `build_services` 内（agent 创建之前）；`run` 内仅保留 `deps.set_next_wake_fn`（必须依赖 `scheduler` 实例，scheduler 在 `run` 内创建）。
- **路径 B**：把 `agent` 创建下移到 `run`。会改 `build_services` 返回签名（`return exchange, deps, agent, budget` → `return exchange, deps, budget`），调用点 line 573 也要改；增加跨函数耦合面。

**G5 选定路径 A**。具体改动：

```python
# build_services — line 438 ()收尾后、line 439 agent 创建前新增（approval_gate 装配后）：
max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)
runtime_config = RuntimeConfig(wake_max_minutes=max_wake)

# build_services — line 439 改造：
agent = create_trader_agent(
    model=result.model,
    persona_config=result.persona,
    runtime=runtime_config,
)

# build_services — line 490-507 deps = TradingDeps(...) 不动
# build_services — line 507 之后、line 509 alert service 之前新增：
deps.wake_min_minutes = 1   # 显式赋值与原 run() 装配模式一致 + 防 TradingDeps 默认值未来漂移
deps.wake_max_minutes = max_wake

# build_services — line 524 return 签名不变 (exchange, deps, agent, budget)

# run — line 614 注释 `# R4: dynamic wake interval` 改写为 `# R4: dynamic wake fn binds scheduler`
#       （原注释统辖 615-618 四行；615-617 删除后语境窄化为 line 618 一行）

# run — line 615-617 全部删除：
#   - max_wake = min(max(4 * result.scheduler_interval_min, 60), 180)  # 删
#   - deps.wake_min_minutes = 1                                          # 删（已在 build_services 装配）
#   - deps.wake_max_minutes = max_wake                                   # 删（已在 build_services 装配）

# run — line 618 保留（必须依赖 scheduler 实例）：
deps.set_next_wake_fn = lambda minutes: scheduler.set_next_interval(minutes * 60)
```

**为何 `set_next_wake_fn` 必须留在 run**: `scheduler` 在 line 612（run 内）创建，是 lambda
闭包依赖；scheduler 不能上移到 build_services（callback `on_tick` 内调 `run_agent_cycle` 等需要
agent / deps / engine / model，全在 run 上下文）。

**导入**: `src/agent/persona.py` 暴露 `RuntimeConfig`；`src/cli/app.py` 顶部加
`from src.agent.persona import RuntimeConfig`。

**DoD 校验**: 改动后 `git grep -n "max_wake\b" src/cli/app.py` → **3 hits 全在 build_services 内**
（1 def + 2 use），run 内 0 hits（防漏删，参 §6.1）。

### 3.4 `src/agent/tools_execution.py:292-310` — set_next_wake impl docstring 验证（无改动）

**当前**:
```python
async def set_next_wake(
    deps: TradingDeps,
    minutes: int,
    reasoning: str,
) -> str:
    """Set the next wake interval (one-shot). Clamped to configured min/max."""
    ...
```

impl docstring 已经简洁（"Clamped to configured min/max"），**无决策暗示**——
N5 漏网仅在 wrapper docstring（`trader.py:567-585`，§3.5 处理）。

**本节零改动**：函数实现不变（clamp 逻辑、return 字符串、`_record_action` 调用全保留）。
此段仅作 §3.5 wrapper 改写的对照参考，**不进 in-scope 表**（in-scope 表只列有 diff 的项）。

### 3.5 `src/agent/trader.py:567-585` — set_next_wake wrapper docstring

**当前（含 N5 漏网）**:
```python
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
```

**改后**:
```python
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

**变更点**:
- 删 `Shorten when... lengthen when...` 整段（N5 决策暗示清理）
- 第一行从 "Set how soon you want to check the market again" 改为 "Set the next scheduler wake-up interval (one-shot; reverts to default after use)"——更精确事实描述
- `Args.minutes` 引用 Layer 1 `Wake interval control` bullet（单 SoT 跨引用）

### 3.6 测试更新

#### 3.6.1 `tests/test_persona.py` — 新增 4 个 drift guard 测试

| 测试名 | 验证 |
|---|---|
| `test_layer1_contains_wake_interval_control_bullet` | `_build_layer1(RuntimeConfig())` 含 `## Cross-Tool Behavior` + `**Wake interval control**` 标题 + 关键句 `Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting` |
| `test_layer1_renders_dynamic_wake_max` | `_build_layer1(RuntimeConfig(wake_max_minutes=120))` 渲染含 `1-120 min for this session`（120 = sim #4 实证值）；对比 `_build_layer1(RuntimeConfig(wake_max_minutes=60))` 含 `1-60 min for this session`（默认值），证明模板渲染了正确的字段值 |
| `test_generate_system_prompt_default_runtime` | `generate_system_prompt(PersonaConfig())`（无 runtime 参数）等价于显式 `RuntimeConfig()`，渲染含 `1-60` |
| `test_set_next_wake_no_decision_hints_in_description` | 走 pydantic-ai 1.78 实测路径（参考 `tests/test_trader_agent.py:210-211`）：`agent._function_toolset.tools["set_next_wake"].tool_def.description` 不含 `shorten when` / `lengthen when`（regex case-insensitive）。**API 路径为何选 `description`**：N5 决策暗示出现在 docstring 首段（被 griffe sniff 进 `tool_def.description`），不在 `parameters_json_schema`（仅含 Args 段 per-param 描述）。 |

#### 3.6.2 `tests/test_persona.py` — 现有测试更新（G12，必修）

`tests/test_persona.py:246-258` `test_layer1_bullet_count_5`:

```python
def test_layer1_bullet_count_5():
    """Layer 1 bullet count drift guard (Iter 4: 25 → 5 — cross-tool behavior only)."""
    ...
    assert bullet_count == 5, f"Expected 5 Layer 1 bullets, got {bullet_count}"
```

加第 6 个 Wake interval control bullet 后**该断言 deterministic fail**。修法：

| 项 | from | to |
|---|---|---|
| 函数名 | `test_layer1_bullet_count_5` | `test_layer1_cross_tool_bullet_count`（与具体数字解耦，命名稳健） |
| docstring | `(Iter 4: 25 → 5 — cross-tool behavior only)` | `(Iter 4: 25 → 5; R2-5: +1 Wake interval control = 6 — cross-tool behavior only)` |
| assert | `== 5` | `== 6` |

#### 3.6.3 `tests/test_tools.py` — 现有测试保持（默认值兜底）

`test_set_next_wake_clamps_to_max(deps)` L343-352 现状传 120 期望 clamp 到 60（因 `MockDeps.wake_max_minutes=60`）——
本 PR 不动 clamp 逻辑，测试值保持。

#### 3.6.4 `tests/test_persona.py` 其他 28 个现有测试 — 保持

28 个现有测试（`grep -c "^def test_\|^async def test_" tests/test_persona.py` = **29**，
减去 G12 修的 1 个 = 28 不变）用 `generate_system_prompt(PersonaConfig())` 单参——
本 PR 后等价于 `generate_system_prompt(PersonaConfig(), RuntimeConfig())`（默认值兜底）。
**唯一例外是 G12 修的 `test_layer1_bullet_count_5`**。

#### 3.6.5 `create_trader_agent` 现有调用点 — 保持

实测 grep `tests/`：

| 文件 | 真实调用 | mock 桩 |
|---|---|---|
| `tests/test_trader_agent.py` | 8 | 0 |
| `tests/test_tool_call_instrumentation.py` | 1 | 0 |
| `tests/test_wizard.py` | 0 | 3（`patch("src.cli.app.create_trader_agent")`） |
| `tests/test_okx_algo_normalization.py` | 0 | 1（patch） |

**合计 9 个真实调用 + 4 个 mock 桩**。本 PR 后真实调用等价于 `runtime=None`（默认值兜底），mock 桩不受签名影响——**全部无需更新**。

### 3.7 inventory + memory 更新（不进 git）

#### 3.7.1 `.working/sim4-issues-inventory.md §P0-5` 状态注记

在原 `### P0-5. scheduler 30min 兜底失效：set_next_wake 完全覆盖配置` 段开头加：

```
### P0-5. scheduler 30min 兜底失效：set_next_wake 完全覆盖配置  ✅ wontfix - by design (R2-5 brainstorm 决议, 2026-05-01)

**重新评估结论**：现机制按设计工作，180min 硬天花板已是合理断路器。强制 30min 兜底
违背"模拟真实交易员"哲学（real traders 设 alert/SL/TP，不需要老板叫醒）。
sim #4 中 set_next_wake(120) 实际工作良好（75min alert 自然唤醒，期间无坏后果）。

**真实风险源**：长 wake 期间无人盯盘 → P1-4（R:R 漂移 + SL 哲学）才是修法位置，
不是 scheduler 兜底。

**R2-5 实际产出**：set_next_wake fact-only refresh + RuntimeConfig 抽象 + Layer 1 cross-tool bullet。
详见 `docs/superpowers/specs/2026-05-01-iter-w2r2-5-set-next-wake-clarity-design.md`。

---

[原 P0-5 内容保留作历史 traceability]
```

#### 3.7.2 `.working/all-pending-needs.md` Tier 1 R2-5 状态

R2-5 状态 pending → ✅ landed（PR 编号 + merge commit 待 PR 合后回填，与 R2-1~R2-4 风格一致）。

#### 3.7.3 `MEMORY.md` + `project_w2_prep_progress.md` + `project_tradebot_status.md` 更新

- `project_w2_prep_progress` round 2 表 R2-5 行：状态从 `pending` → `✅ landed (PR # ?)`，描述含
  "wontfix scheduler + RuntimeConfig 抽象 + Layer 1 cross-tool bullet + N5 漏网清理"。
- `project_tradebot_status` 头部 description 字段从 "R2-1~R2-4 ✅ landed" 扩展含 R2-5；PR 表新增 R2-5 行。
- `MEMORY.md` **两条索引 description 行都需更新**（实测含显式 R2-x 列举，R2-5 landed 后过期）：
  - `[TradeBot project status]` 行：`R2-1~R2-4 ✅ PR #30/#31/#32/#33 / R2-5~R2-9 pending` → `R2-1~R2-5 ✅ PR #30/#31/#32/#33/#? / R2-6~R2-9 pending`，PR 数量 `34 PRs` → `35 PRs`
  - `[W2 prep progress]` 行：`R2-1~R2-4 ✅ PR #30/#31/#32/#33 / R2-5~R2-9 pending` → `R2-1~R2-5 ✅ PR #30/#31/#32/#33/#? / R2-6~R2-9 pending`

---

## 4. 测试策略

### 4.1 测试金字塔

| 层 | 数量 | 验证内容 |
|---|---|---|
| **Drift guard（unit）** | 4 新增（§3.6.1）| Layer 1 bullet 渲染 + 默认值兜底 + N5 漏网检测 |
| **现有测试更新** | 1 修（§3.6.2，G12）| `test_layer1_bullet_count_5` → `test_layer1_cross_tool_bullet_count`，5 → 6 |
| **回归（unit/integration）** | 0 新增 | 28 个现有 test_persona（29 - G12 修的 1）/ 9 真实 + 4 mock create_trader_agent 调用点 / `test_set_next_wake_*` 全部通过（默认参数兜底） |
| **手工冒烟（非 CI）** | 1 | 启动 sim 跑 1 cycle，prompt dump 含 `Wake interval control` + 实际 wake_max；agent 调 `set_next_wake(180)` clamp 反馈正常 |

### 4.2 关键 invariant

1. `_build_layer1(RuntimeConfig())` 与现有 5-bullet Cross-Tool Behavior 完全一致 + 第 6 bullet 新增（diff 仅追加）
2. `generate_system_prompt(persona)` 与 `generate_system_prompt(persona, RuntimeConfig())` 输出**完全相同**字符串（默认值兜底语义）
3. `set_next_wake` wrapper docstring 不含 `shorten when` / `lengthen when`（任何 case）
4. `RuntimeConfig` frozen → 任何 `runtime.wake_max_minutes = X` 突变抛 `dataclasses.FrozenInstanceError`

### 4.3 covered by 现有测试（不重复 enumerate）

- `test_set_next_wake_clamps_to_max/min` — clamp 行为不变
- `test_set_next_wake_success` — 成功路径不变
- `test_set_next_wake_not_available` — `set_next_wake_fn=None` 早返回不变
- `test_persona_*` — 三层结构 + 关键内容断言全部通过

---

## 5. 风险与权衡

### 5.1 已知 trade-off

| Trade-off | 选择 | 代价 |
|---|---|---|
| `RuntimeConfig` 单字段 vs 直接 kwarg | dataclass 抽象 | +5 LOC，未来扩展零 churn 抵消 |
| `wake_max_minutes` 默认 60 vs 无默认 | 默认 60（对应默认 scheduler 配置） | 测试 / 临时调用免改；非默认配置下显式传值 |
| `frozen=True` vs 普通 dataclass | frozen | 防意外突变；call site 装配后只读 |
| Layer 1 第 6 bullet 还是 docstring | Layer 1 | session-aware 信息无法静态 docstring；cross-tool 关系是 Layer 1 真正价值；**Layer 1 即 system_prompt，每 cycle 都注入 → agent 每 cycle 都被告知 session range，不依赖跨 cycle 记忆**（闭合 §1.4 token 经济论证） |
| 新 bullet 末句"Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting" | 保留 | 这是**真正的 cross-tool 价值**，比 bound 更重要——agent 知道 sleep 不是不可中断 |
| D8 不增强 clamp 反馈（暴露 session range） | 不做 | 主防线已在 Layer 1（每 cycle 注入），clamp 反馈附加 session range 是 **nice-to-have** ——保持当前 PR 最小 + 减少反馈 token；未来若数据显示 agent 漏看 Layer 1 可独立 follow-up |

### 5.2 已知盲区

| 盲区 | 缓解 |
|---|---|
| `RuntimeConfig` 仅 1 字段，"抽象不饱和" | 接受 — 论据见 §1.2 D6（N10 / scheduler_interval / exchange 等近期候选场景） |
| 默认 `wake_max_minutes=60` 与 cli/app.py 公式 `min(max(4N, 60), 180)` 在 N=15 时巧合相等，但语义不同 | 接受 — 默认值是"无 cli 装配时的合理兜底"，非"复制 cli 公式"；docstring 注明 |
| Layer 1 token 增量（新增 ~200 tokens / call） | 接受 — Iter 8 audit 显示 Layer 1 是 cache_hit 区间（91% hit rate），增量在已 cache 部分 |
| pydantic-ai 1.78 实际访问 tool description 的 API 路径 | 实测路径（`tests/test_trader_agent.py:210-211` 已用）：`agent._function_toolset.tools[name].tool_def.description`（首段 docstring）/ `.parameters_json_schema` (Args 段 per-param)。N5 决策暗示在 `description` 不在 `parameters_json_schema`，故 G10 走 `description` 路径 |

### 5.3 W2 之后议题预期

观察期数据可能驱动以下议题（不在本 PR scope）:
- agent 是否仍持续 set_next_wake 越界（Layer 1 暴露后理论上 0%；如非 0% → prompt 引导 / N10 议题）
- `scheduler_interval_min` 是否需注入 Layer 1（agent 是否表现出"不知道监控节拍"困惑）
- Open/Close fill response bullet N5 第二轮 audit 时机
- N10 reasoning 注入 + RuntimeConfig 是否融合（per-cycle vs session-start 两通道是否需要统一抽象）

---

## 6. 验收标准

### 6.1 Definition of Done

- [ ] G1-G14（in-scope §2.1，G6 已剔除）全部 commit，每项独立 commit message 标注 task 编号
- [ ] `pytest tests/` baseline 962 pass / 965 collected → after R2-5 **966 pass / 969 collected**（+4 新 drift guard，0 移除，G12 是修改而非新增/移除）
- [ ] `git grep -i "shorten when\|lengthen when" src/agent/` → **0 hits**
- [ ] `git grep "Wake interval control" src/agent/persona.py` → **1 hit**（Layer 1 bullet 主体）
- [ ] `git grep "Wake interval control" src/agent/trader.py` → **1 hit**（wrapper docstring 引用 Layer 1 标签）
- [ ] `git grep -n "max_wake\b" src/cli/app.py` → **3 hits 全在 `build_services` 内**（1 处 `max_wake = ...` 定义 + 2 处使用：`RuntimeConfig(wake_max_minutes=max_wake)` / `deps.wake_max_minutes = max_wake`）
- [ ] **动态行号 robust 校验**（path A migration 后 build_services 长度漂移仍稳定）：
  ```bash
  RUN_LINE=$(grep -n "^async def run" src/cli/app.py | head -1 | cut -d: -f1)
  git grep -n "max_wake\b" src/cli/app.py | awk -F: -v r="$RUN_LINE" '$2+0 >= r' | wc -l
  ```
  → **0 hits**（所有 `max_wake` 出现在 `async def run` 之前——即 build_services 内，防 run 内 NameError）
- [ ] 手工冒烟：sim 1 cycle prompt dump 含 `1-60 min for this session`（默认配置）或 `1-120 min for this session`（30min 配置）
- [ ] PR description 含 brainstorm 决议浓缩（D1-D9）+ token 经济成本论证摘要 + sim #4 wontfix justification 链接
- [ ] inventory `§P0-5` 状态注记落 `.working/sim4-issues-inventory.md`（不进 git）

### 6.2 PR review 关注点（写入 PR description）

1. **D6 RuntimeConfig 抽象是否适合**：单字段是否过度工程？答：N10 / scheduler_interval / exchange 等近期场景驱动 + 5 LOC 增量
2. **D5 默认 60 是否合理**：与 cli/app.py 公式 N=15 时巧合相等是否暗示耦合？答：默认是"无 cli 装配时兜底"，文档注明语义独立
3. **D9 N5 仅清 set_next_wake 是否双标**：Open/Close fill response 也含轻微 prescription，为何不一并清？答：争议小 + scope 控制 + 留 N5 第二轮 audit
4. **bullet 末句价值**：`Alerts, fills, and conditional triggers always interrupt sleep regardless of this setting` 是否冗余？答：**真正的 cross-tool 价值**，比 bound 重要（agent 知道 sleep 可中断）

---

## 7. 实施顺序（与 plan 文档对应）

| Task | 类型 | 内容 | 覆盖 G* |
|---|---|---|---|
| **1** | docs | spec 独立 commit（本文档） | — |
| **2** | test red | `tests/test_persona.py` 新增 3 个 drift guard：`test_layer1_contains_wake_interval_control_bullet` / `test_layer1_renders_dynamic_wake_max` / `test_generate_system_prompt_default_runtime`（此时 RuntimeConfig 不存在 → ImportError 红） | G8 / G9 / G11 |
| **3** | feat green | `RuntimeConfig` dataclass + `_build_layer1(runtime)` 改造 + `generate_system_prompt` 签名 + **G12 sync**：`test_layer1_bullet_count_5` → `test_layer1_cross_tool_bullet_count` + `assert == 6`（与第 6 bullet 同 commit 避免中间状态 fail） | G1 / G2 / G3 / G12 |
| **4** | feat | `create_trader_agent` 签名（加 `runtime: RuntimeConfig \| None = None`）+ `cli/app.py` 路径 A 跨函数装配：`build_services` 内算 `max_wake` + 构造 `RuntimeConfig` + 装 `deps.wake_min/max_minutes`；`run` 内仅留 `deps.set_next_wake_fn` | G4 / G5 |
| **5** | refactor red→green | `trader.py` set_next_wake wrapper docstring fact-only 改写（删 `Shorten when... lengthen when...` + 第一行精确化 + Args 引用 Layer 1）；**与 G10 N5 drift guard 同 commit**：新增 `test_set_next_wake_no_decision_hints_in_description` 走 `agent._function_toolset.tools["set_next_wake"].tool_def.description` 路径 | G7 / G10 |
| **6** | docs/memory | inventory `§P0-5` 状态注记 + `all-pending-needs.md` Tier 1 R2-5 状态更新 + `project_w2_prep_progress.md` R2-5 行更新（**全部不进 git**） | G13 / G14 |

**TDD 节奏说明**：
- Task 2 三个测试在 Task 3 后转绿（RuntimeConfig + bullet 实施）
- Task 3 G12 sync 必须与第 6 bullet 同 commit，否则中间状态 `test_layer1_bullet_count_5` deterministic fail
- Task 5 N5 drift guard 测试与 wrapper docstring 改写同 commit（先加测试再删文字会瞬时 fail）

---

## 8. 决议溯源（brainstorm session 2026-05-01）

| 决议 | 备选 | 拒绝理由 |
|---|---|---|
| D1 wontfix scheduler | A 硬 cap / B 软 cap+记账 / D 双轨 | A 限制 agent 表达力；B silent rollover 在无 N10 时反让 agent 偏离原始决策；D 工程量爆炸不闭环 P0；现机制 + 180min 断路器 + alert/SL/TP 系统已合理 |
| D5 动态 1-{wake_max} | 静态 envelope `1-180` | 默认配置（max=60）下 misleading；agent 试 90/120/180 全 clamp |
| D5 sub-recommended over clamp 反馈增强 | clamp 反馈带 session range | cycle 间无记忆 → 每 cycle 重新踩边界 → ~135k-1.35M token 浪费（vs β plumbing 一次性 ~40 行） |
| D6 RuntimeConfig dataclass | α 扁平 kwarg / γ 复用 TradingDeps | α 未来 N10 / scheduler_interval / exchange 加字段时签名 churn；γ TradingDeps 含 services / db_engine / cycle_id 不该泄入 prompt |
| D8 不增强 clamp 反馈 | 暴露 session range | Layer 1 即 system_prompt 每 cycle 都注入；agent 每 cycle 都看到精确 session range，不依赖跨 cycle 记忆，clamp 反馈追加 session range 是 nice-to-have，留未来数据驱动决定 |
| D9 N5 仅清 set_next_wake | 一并清 Open/Close fill response | 后者争议小 + 控 scope + 留第二轮 audit |
| 默认 `wake_max_minutes=60` | 默认 None（强制传值） | 测试 / 临时调用免大批改；与默认 15min 配置 wake_max 巧合相等是合理 ergonomic |
| 不动 `set_next_wake` impl docstring | 一并清理 | impl docstring "Clamped to configured min/max" 已简洁；N5 漏网仅在 wrapper（trader.py）|

---

**Spec 版本**: v1
**最近更新**: 2026-05-01
