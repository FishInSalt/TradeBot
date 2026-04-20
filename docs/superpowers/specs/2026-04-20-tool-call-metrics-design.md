# Tool Call Metrics Enabler — 设计文档（Iteration 1 / 4）

## 0. 背景

### 0.0 项目快照

**TradeBot** 是一个 LLM 驱动的加密货币自动交易系统。Agent（Claude）通过工具调用感知市场、管理仓位、做出交易决策，在 USDT 保证金永续合约上自主交易。

**运行循环**：每 15 分钟唤醒一次（也可被订单成交、价格警报等事件提前唤醒），进入 `run_agent_cycle()` → LLM 调用工具分析 → 返回交易决策 → 写入 DecisionLog。

**技术栈**：Python 3.13 / pydantic-ai 1.78.0（agent 框架）/ SQLAlchemy 2.0 async + SQLite(WAL)/ pytest + pytest-asyncio。

**工具库规模**：26 个（15 感知 + 10 执行 + 1 memory），统一通过 `src/agent/trader.py` 的 `@agent.tool` 装饰器注册。

**System Prompt 三层结构**（`src/agent/persona.py`）：身份 + 工具引导（Layer 1）/ 通用交易思维框架（Layer 2）/ 人格 + 策略（Layer 3）。

**当前状态（2026-04-20，20 PRs merged）**：664 测试通过；Agent 感知层达到 6 类广度（技术面 / 消息面 / 衍生品 / 宏观环境 / 机构 ETF 资金流 / 链上稳定币）；hardening batch 已闭环（PR #18/#19/#20）。

### 0.1 所处位置

本 spec 是"进观察期前 4-iteration 计划"的第一轮。4 轮主题依次为：

| # | 主题 | 本 spec |
|---|------|---------|
| **1** | 观察基础设施 — tool-call metrics enabler | ✅ 本文 |
| 2 | 工具补全（get_position 增强 + order_book + recent_trades + multi-timeframe）| 下一 session |
| 3 | 结构感知工具 `get_price_pivots` 朴素版 | 下下 session |
| 4 | N7 Layer 1 prompt 组织重构 | 最后一 session |

4 轮合并后正式进观察期，由 agent 在模拟交易所自主交易、采集实战决策数据以驱动下一轮改进。

### 0.2 为什么是这一轮

**观察 ≠ 眼睛闭上等结果**。观察期要回答的问题（以下非穷举）：

- 哪些工具 agent 从不调？（触发 N7 Layer 1 重组 / 删工具的依据）
- 哪些工具总报错？错误类型是什么？（工具健壮性 / 源降级诊断）
- 某工具慢到影响 cycle 质量吗？（p95 延迟观察）
- 多 session 并行（如 BTC / ETH session 同时跑）时，调用画像差异如何？
- agent 读完某个工具后做了什么决定？（cycle_id 关联 DecisionLog）

无埋点 = 盲跑。先 instrument 后 observe 是本轮的核心纪律。

### 0.3 硬约束

- **多 session 并发**：系统近期规划支持同时在多个交易对上运行独立 agent session。metrics 设计必须从一开始就把 `session_id` 作为一等维度，并在并发写入下稳定（已确认 SQLite WAL 已启用，见 `src/storage/database.py:25`）。
- **不改动 26 个现有 `@agent.tool` 注册**：tool 文件已 N5 fact-only 清理定稿，再次重写的成本/收益不划算。埋点必须用"对 tool 注册零侵入"的机制。

### 0.4 术语表

| 术语 | 含义 |
|------|------|
| **观察期** | 4-iteration 完成后的阶段 —— agent 在模拟交易所用完整工具集自主交易，采集实战决策数据以驱动下一轮改进。非 QA |
| **cycle** | 一次 agent 从唤醒到决策落库的完整周期；每次 cycle 由 `run_agent_cycle()` 生成唯一 `cycle_id`（uuid4 前 8 位） |
| **DecisionLog** | 现有表，记录每个 cycle 的最终决策、model、tokens（`src/storage/models.py:66`） |
| **TradingDeps** | Agent run-scoped 依赖容器（dataclass），pydantic_ai `ctx.deps` 的承载对象（`src/agent/trader.py:17`） |
| **capability** | pydantic_ai 1.x 的扩展机制。`AbstractCapability.wrap_tool_execute` 可对每次 tool 执行做 before/after 拦截 |
| **Layer 1 / 2 / 3** | System prompt 三层结构（见 §0.0） |
| **N5 / N6 / N7** | Next-Iteration 候选议题编号（按识别时间排序）。N5 已落地（PR #18 fact-only 清理）；N6 HTF 候选；N7 Layer 1 prompt 重组候选 |
| **B 档 / C 档** | 本 spec §1.3 定义的 metrics 字段档位：B = 基础（tool_name + status + duration + error_type）；C = B + args + result preview |
| **hardening batch** | 已完成的一轮打磨批次（PR #18/#19/#20），涵盖 N5 标签清理 + N3 follow-up 修复 |
| **4-iteration plan** | 观察期前的 4 轮迭代计划（见 §0.1 表格） |

---

## 1. 目标与非目标

### 1.1 目标

本轮产出 **B 档 metrics 基础设施**：为每次 tool 调用记录 `(session_id, cycle_id, tool_name, status, duration_ms, error_type, created_at)`，并提供聚合读取接口 + 一个薄命令行脚本。

### 1.2 非目标

- **C 档字段（args / result preview / traceback 详情）** — 待观察期出现具体决策归因需求后再加 nullable 列扩展。预设会猜偏。
- **Alembic 迁移体系** — 项目当前靠 `Base.metadata.create_all()` 自动建表，新表无需迁移脚本（详见 §5）。引入 Alembic 是独立议题。
- **CLI 子命令 / 正式观察面板** — 薄脚本 (`scripts/tool_call_summary.py`) 够用，产品化等观察期反复手查后再考虑。
- **Agent 可读自己的 metrics** — 会产生反馈循环污染观察数据；观察者是人，不是 agent。
- **B 档升级 C 档的 prompt 治理** — 等观察期数据告诉我们需要什么。
- **替代 / 合并 A2 tool-call 解析路径**（`src/cli/app.py:156-194`）— A2 走 `result.new_messages()` 事后抽取 ToolCallPart/ToolReturnPart，用途是 INFO 日志 + `format_cycle_output` 终端显示（cycle 执行完后用户一眼看到刚才调了什么）。recorder 是事中拦截、写 DB、测 duration，用途是观察期聚合分析。两条路径**正交互补**，互不影响，也不重复 —— A2 保留原样，recorder 新增。合并为一套是独立议题（非本轮）。
  - **status 口径分歧（观察期须知）**：A2 依赖 `ToolReturnPart.outcome` / `is_tool_error`（pydantic_ai 框架标注），recorder 依赖 `wrap_tool_execute` 内的 Python 异常分类。当 tool 内部 `catch` 后返回"⚠️ 降级内容"字符串但**不抛出**时，A2 可能标为 normal（或按 display 逻辑识别 ⚠️ 文本），recorder 一定标为 `ok`。观察期对照两列（DB `tool_calls.status` 与终端 INFO 日志）时若不一致，以 recorder 的异常分类为准；"⚠️ 返回但未抛错"的场景属 C 档增强议题，本轮 B 档不治理。

### 1.3 档位判定

| 档 | 字段 | 本轮 |
|----|------|------|
| A | tool_name + session_id + cycle_id + created_at | 子集 |
| **B** | A + status + duration_ms + error_type | ✅ **本轮** |
| C | B + args + result_preview | 观察期后 |

B 足够覆盖 §0.2 列出的全部观察问题；C 引入敏感数据脱敏规则（单独一轮的 scope），本轮不做。

### 1.4 改动清单

**新建文件**（4 个）：

| 文件 | 作用 | 规模估算 |
|------|------|----------|
| `src/services/tool_call_recorder.py` | ToolCallRecorder capability —— 写路径核心 | ~50 行 |
| `scripts/tool_call_summary.py` | 薄命令行查询脚本（新建 `scripts/` 目录） | ~30 行 |
| `tests/test_tool_call_recorder.py` | 单元测试（5 正常路径）—— 扁平布局，follow 现有 `tests/` 约定 | ~140 行 |
| `tests/test_tool_call_instrumentation.py` | 端到端集成测试（1 条）—— 同上扁平 | ~60 行 |

**目录约定**：`tests/` 当前扁平（~30 个 `test_*.py` 挂在根下），新测试文件**沿用扁平布局**，不新建 `tests/services/` / `tests/integration/` 子目录。新建 `scripts/` 是顶层新目录（项目当前无）。

**修改文件**（6 个）：

| 文件 | 改动 |
|------|------|
| `src/storage/models.py` | 新增 `ToolCall` model（§2.1） |
| `src/agent/trader.py` | `TradingDeps.cycle_id: str \| None` 新字段；**`TradingDeps.db_engine` 类型由 `object \| None` 收紧为 `AsyncEngine \| None`**（利用已启用的 `from __future__ import annotations`，详见 §3.2）；Agent 构造注入 `ToolCallRecorder()`；导出 `REGISTERED_TOOL_NAMES` 常量；`create_trader_agent()` 签名**不变** |
| `src/cli/app.py` | `run_agent_cycle` 内 `deps.cycle_id = cycle_id` mutate |
| `src/services/metrics.py` | 追加 `ToolCallStats` dataclass + `get_tool_call_summary()` 方法 |
| `tests/test_metrics.py` | 追加 6 测试 |
| `tests/test_trader_agent.py` | 追加 `test_registered_tool_names_matches_agent_tools` 漂移防护 |

**总规模估算**：~200 行源码（recorder 50 + script 30 + ToolCall model 20 + MetricsService 方法 60-80 + trader.py / app.py 散点 10-20）+ ~250 行测试。测试总数 **664 → 681**（新增 17）。

---

## 2. 数据模型

### 2.1 新表 `tool_calls`

```python
# src/storage/models.py
class ToolCall(Base):
    """每次 agent tool 调用一行（观察期埋点）。"""
    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_session_tool_time", "session_id", "tool_name", "created_at"),
        Index("ix_tool_calls_cycle", "cycle_id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    # 无 index=True：复合索引 (session_id, tool_name, created_at) 的 leftmost prefix 已覆盖 session_id 查询
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"))
    # cycle_id 是应用层相关 key — 匹配 DecisionLog.cycle_id（当 cycle 正常结束时）。
    # 不声明 DB FK：tool_call 在 cycle 执行中写入，DecisionLog 在 cycle 结束写入，
    # 时序不允许 FK；DecisionLog.cycle_id 也无 UNIQUE 约束。
    # 无 index=True：__table_args__ 的 ix_tool_calls_cycle 已显式建索引，不重复
    cycle_id: Mapped[str] = mapped_column(String(50), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10))        # "ok" / "error"
    duration_ms: Mapped[int] = mapped_column(Integer)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. "TimeoutError"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

### 2.2 字段决策依据

**`cycle_id` 为 NOT NULL**（与 DecisionLog.cycle_id 同语义）：
- 核查 `src/cli/app.py:102` 确认所有 tool 调用路径都在 `run_agent_cycle` 内，cycle_id 在 `agent.run()` 之前生成。
- NOT NULL 保证数据质量 + fail-fast（任何未来绕过 cycle 设置 cycle_id 的新代码路径会立即暴露）。
- 未来若真需要"系统触发 / 外部调用"场景，用 placeholder 字符串（如 `"system"`）而非 NULL，语义更清楚。

**`error_type` 存异常类名字符串、不存 message / traceback**：
- 避免无意泄露敏感数据（B 档原则）。
- 可用于聚合查询（`SELECT error_type, COUNT(*) FROM tool_calls GROUP BY error_type`）。

**复合索引 `(session_id, tool_name, created_at)`**：
- 覆盖主查询路径 "per-session, per-tool, time-window aggregation"（§4.1 的 `get_tool_call_summary` 主查询）。

**单列索引 `(cycle_id)`**：
- 覆盖"某次决策前调了哪些工具"的 cycle-scope 查询。

**不存 `tool_call_id`（pydantic_ai 的 call.tool_call_id）**：
- 观察期聚合用不到；只有按 LLM response 分组时才需要，C 档再加。

### 2.3 与现有表的关系

- `session_id FK → sessions.id`：DB 层硬约束（session 必须存在）。
- `cycle_id` soft reference → `decision_logs.cycle_id`：**应用层软关联**，查询时用 `LEFT JOIN decision_logs USING (session_id, cycle_id)`，允许孤儿行（agent 中途崩 / cycle 未完成时 tool_call 已落库而 DecisionLog 未写）—— 这些孤儿正是观察期有价值的异常信号。
- **不给 DecisionLog 加 UNIQUE(session_id, cycle_id)**：当前 scope 不需要，将来若启用 cycle-level 关联分析再补一次独立小 PR。
- **Append-only 约定**：recorder 和 MetricsService 均不暴露 UPDATE / DELETE 接口；`tool_calls` 表只在 recorder 写入、在读取端聚合查询。观察期严禁手工 UPDATE/DELETE（污染数据真实性）；若需批量清理（如 TTL），另开独立 PR 并仅按 `created_at < cutoff` 删除（不改行）。
- **FK ON DELETE 未声明**：`tool_calls.session_id` FK 指向 `sessions.id` 但**不声明** `ON DELETE` 行为 —— 与现有 `trade_actions` / `decision_logs` / `memory_entries` 等现存表**一致**（所有 FK 均未声明 ON DELETE）。SQLite 在 pragma `foreign_keys=OFF` 的默认下也不 enforce 级联。若未来启用级联删除策略，整个 schema 一起议（独立迁移 PR），本轮不单独处理。

---

## 3. 记录管线（写路径）

### 3.0 数据流图

一次 tool 调用的完整路径（从 trigger 到 DB 落库）：

```
trigger (scheduled / order-filled / price-alert)
    │
    ▼
run_agent_cycle()   [src/cli/app.py]
    │
    ├─ 1. cycle_id = uuid4()[:8]
    ├─ 2. deps.cycle_id = cycle_id              ← 新增：mutate TradingDeps
    ├─ 3. await agent.run(prompt, deps=deps)
    │       │
    │       ▼
    │     LLM → ToolCallPart (e.g. "get_market_data")
    │       │
    │       ▼
    │     pydantic_ai capability chain
    │       │
    │       ▼
    │     ToolCallRecorder.wrap_tool_execute()  ← 新增 capability
    │       │   start = time.monotonic()
    │       │   try:     return await handler(args)       ← 工具实际执行
    │       │   except:  status="error", error_type=...; raise
    │       │   finally: INSERT INTO tool_calls (...)     ← 落库
    │       ▼
    │     tool result → LLM → 更多 tool call / 最终输出
    │
    └─ 4. INSERT INTO decision_logs (cycle_id, decision, ...)
```

**关键时序**：
- `cycle_id` 生成**先于** `agent.run()` → deps.cycle_id 被 recorder 读到时必有值
- `tool_calls` 行在每次 handler 执行后**立即写入**
- `decision_logs` 行在 cycle **全部结束后**才写入
- 因此 `tool_calls.cycle_id` **不能**作为 DB 层 FK（引用目标此刻不存在），只能作应用层软关联（§2.3）

### 3.1 注入机制 — pydantic_ai capability

经 context7 调研确认，pydantic_ai 1.78.0 提供 `AbstractCapability.wrap_tool_execute`（v1.71 起稳定），对每次 tool 执行 before/after 全覆盖，含异常捕获。对 26 个 `@agent.tool` **零改动**。

```python
# src/services/tool_call_recorder.py （新文件）
from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    ValidatedToolArgs,
    WrapToolExecuteHandler,
)
from pydantic_ai.exceptions import (
    ApprovalRequired,
    CallDeferred,
    ModelRetry,
    SkipToolExecution,
    ToolRetryError,
)
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDefinition

from src.storage.database import get_session
from src.storage.models import ToolCall

if TYPE_CHECKING:
    # 避免 trader.py ↔ tool_call_recorder.py 循环 import（trader.py 的
    # create_trader_agent() 内部会懒加载本模块，见 §3.5）
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)

# pydantic_ai 的控制流信号 —— retry / approval / deferral，不是真错，也不是 ok。
# 这些异常直通不记 metrics 行，否则会在未来启用 approval / retry flow 时产生假阳性 error。
_CONTROL_FLOW_EXCEPTIONS = (
    ApprovalRequired,
    CallDeferred,
    ModelRetry,
    SkipToolExecution,
    ToolRetryError,
)


@dataclass
class ToolCallRecorder(AbstractCapability["TradingDeps"]):  # 字符串前向引用
    """从 ctx.deps.db_engine 读 engine（详见 §3.2）—— recorder 本身无字段。

    依赖的 pydantic_ai 契约（v1.78 已验证）：capability 收到的 `ctx.deps` 即是
    `agent.run(deps=...)` 传入的对象，不做任何转换/复制。集成测试
    `test_agent_run_writes_tool_call_rows` 隐式验证这个契约；若未来版本变更，
    测试会立即暴露。
    """

    async def wrap_tool_execute(
        self,
        ctx: RunContext[TradingDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,  # 官方别名（= dict[str, Any]），与 base class override 一致
        handler: WrapToolExecuteHandler,
    ) -> Any:
        start = time.monotonic()
        status, error_type = "ok", None
        skip_record = False
        try:
            return await handler(args)
        except _CONTROL_FLOW_EXCEPTIONS:
            skip_record = True  # 控制流信号直通，不记 metrics
            raise
        except Exception as e:
            status, error_type = "error", type(e).__name__
            raise
        finally:
            if not skip_record:
                try:
                    # duration_ms 计算也在 try 内：time.monotonic() 极端下若抛
                    # （InterruptedError 等），不会在 finally 里替换掉 outer
                    # except 已 re-raise 的真 tool error。
                    duration_ms = int((time.monotonic() - start) * 1000)
                    # 用显式 raise 而非 assert：这是运行时不变量，不是 debug 检查；
                    # `python -O` / `PYTHONOPTIMIZE=1` 会剥离 assert，丢失 fail-fast。
                    if ctx.deps.cycle_id is None:
                        raise RuntimeError(
                            "cycle_id must be set on TradingDeps before tool call"
                        )
                    if ctx.deps.db_engine is None:
                        raise RuntimeError(
                            "db_engine must be set on TradingDeps"
                        )
                    async with get_session(ctx.deps.db_engine) as session:
                        session.add(ToolCall(
                            session_id=ctx.deps.session_id,
                            cycle_id=ctx.deps.cycle_id,
                            tool_name=call.tool_name,
                            status=status,
                            duration_ms=duration_ms,
                            error_type=error_type,
                        ))
                        await session.commit()
                except Exception as rec_err:
                    logger.error(
                        f"tool_call_recorder failed for {call.tool_name}: {rec_err}"
                    )
```

**注**：`finally` 块用 `if not skip_record:` 而不是 `if skip_record: return` —— 后者会吞掉 except 块的 `raise`，Python 语义陷阱。条件 skip 写法保证控制流异常正常向外传播。

**循环 import 规避**（已实测复现 + 修法验证）：
- 原始风险：若两边都用顶层 import，Python 加载期会 `ImportError: cannot import name 'TradingDeps' from partially initialized module 'src.agent.trader'`（`from __future__ import annotations` 不延迟顶层 import 语句，也不延迟 base class 的泛型下标求值）
- **真正破环的措施**（技术必需）：`tool_call_recorder.py` 用 `if TYPE_CHECKING: from src.agent.trader import TradingDeps` + base class 写 `AbstractCapability["TradingDeps"]`（字符串前向引用，runtime 被 `typing.Generic.__class_getitem__` 接受为 ForwardRef）—— 实测有此一步后，trader.py 顶层 import recorder 也能成功
- **风格选择**（非技术必需）：`trader.py` 的 `create_trader_agent()` 用函数级懒加载 `from src.services.tool_call_recorder import ToolCallRecorder`。理由：与现有 26 个 tool 的 `from src.agent.tools_perception import ...` 懒加载 pattern 统一，代码风格一致性优先。顶层 import 也能工作（已实测），但会打破 trader.py 现有的"一切依赖进函数内"惯例

**runtime 不变量用 `raise` 不用 `assert`**：
- Python 的 `assert` 在 `python -O` / `PYTHONOPTIMIZE=1` 下被编译器剥离；若观察期或生产未来启用这两个开关，`assert ctx.deps.cycle_id is not None` 会静默失效，降级为"无兜底"的隐性错误
- `cycle_id` 和 `db_engine` 非空是运行时不变量（契约级别），不是 debug 检查，因此用显式 `if ... is None: raise RuntimeError(...)`
- recorder 外层的 `except Exception as rec_err` 会捕获这些 `RuntimeError` 转 `log.error`，行为与原 assert 方案一致 —— 只是 -O 下不会被剥离

### 3.2 设计要点

**从 `ctx.deps.db_engine` 读 engine（不构造注入）**：
- 项目现状：`TradingDeps.db_engine` 本已存在且广泛使用（`src/agent/tools_perception.py:208,214` / `src/agent/tools_execution.py:20,26` / 多处 test fixture）。recorder 从 deps 读顺势而为，不再造第二条路径。
- 类型收紧：`src/agent/trader.py:1` 已有 `from __future__ import annotations`，所有类型注解 deferred → 可把 `db_engine: object | None`（现状注释"typed as object to avoid circular import"实为历史债）收紧为 `db_engine: AsyncEngine | None`。实测 `sqlalchemy.ext.asyncio.AsyncEngine` 已被 `src.storage.database` 拉进，无真循环风险；加 module-level `from sqlalchemy.ext.asyncio import AsyncEngine` 即可。
- 收益：`create_trader_agent()` 签名**完全不变**，零破坏性；3 处调用点（`src/cli/app.py:276` + `tests/test_trader_agent.py:11,19`）无需改动；与现有 tool 层读 engine 的方式一致。
- 其他 6 个 `object | None` 字段（`approval_gate` / `metrics` / `news` / `macro` / `crypto_etf` / `onchain`）同属 typing 债，**不在本轮 scope 内修**，已记为 follow-up memory `tradingdeps-typing-cleanup`。

**控制流异常白名单**（已知限制 + 缓解）：
- pydantic_ai 的 `ModelRetry` / `CallDeferred` / `ApprovalRequired` / `SkipToolExecution` / `ToolRetryError`（均在 `pydantic_ai.exceptions`）是控制流信号（框架用于 retry / 审批 / 延迟），不是真错。
- `wrap_tool_execute` 里 `except _CONTROL_FLOW_EXCEPTIONS` 分支只 `raise`、不写 metrics 行（既不是 ok 也不是 error）。避免未来启用 approval flow / retry 时产生假阳性 error 污染观察数据。
- **已核实不需进白名单**：`SkipToolValidation` 在 `tool_manager.py:390` 的验证阶段被消费，**不进** `wrap_tool_execute`；`SkipModelRequest` 在 `_agent_graph.py:547` 的模型请求阶段被消费，同样不进。capability `abstract.py:381, 449` docstring 也明言这两类不调用对应的 error hook
- **已知限制**：若 pydantic_ai 未来版本新增控制流异常类型或改变现有 Skip* 的分发路径，白名单需手工更新。由于控制流扩展通常是大版本动作（v2.x），观察期风险低。

**Inline await（不 fire-and-forget）**：每次 tool 调用 finally 块内同步写入 DB。
- SQLite WAL 下单行 INSERT 通常 < 10ms（含 fsync 开销）。相对 tool 自身延迟（几十到几千 ms）占比仍低。
- fire-and-forget 需处理 task 生命周期 / 未完成任务 / 异常上报，复杂度不值得。
- 多 session 并发下 WAL 稳定（多 reader + 单 writer，writer 队列快）。
- **观察期阈值**：若实测 `tool_call_recorder` 写入 p95 > 30ms 持续出现，切批量缓冲（不改 schema，只改 recorder 实现）。
- **写入延迟可观测性**：tool_calls.duration_ms 记录的是 tool handler 耗时，**不是 recorder INSERT 自身耗时**。为使阈值判定可操作，recorder 在 commit 前后加一对 `time.monotonic()`，把 insert 时长以 `logger.debug("tool_call_insert_ms=%.1f tool=%s", insert_ms, call.tool_name)` 输出。观察期若怀疑写延迟影响，打开 DEBUG 日志采样统计 p95；默认 INFO 级别不打印，零正常运行开销。

**外层 try/except 兜底**：
- metrics 写失败（DB 锁 / precondition check / I/O 错）**不抛出**，只 `log.error`。
- 原则：metrics 是辅助设施，绝不能影响真交易决策工具的返回。
- `log.error` 保证观察期翻日志可发现埋点失败。

**不吞 tool 本身异常**：
- `try` 块内 `return await handler(args)` 出错时 `raise`，保留 agent 原错误处理路径。

**cycle_id precondition check**：
- 运行态兜底。未来若有代码路径忘 mutate `deps.cycle_id`，`RuntimeError` 触发 → 外层 except 捕获 → log.error 暴露，不炸 agent。

### 3.3 TradingDeps 字段

```python
# src/agent/trader.py
@dataclass
class TradingDeps:
    # ... 现有字段 ...
    cycle_id: str | None = None   # 由 run_agent_cycle 在 agent.run() 前 mutate
```

**类型分层**：
- Python 层 `str | None`（dataclass 构造时允许无值 —— session 启动早期 / 测试场景）。
- DB 层 `NOT NULL`（运行时 recorder 写入时必有值，recorder precondition check + 外层 except → log.error 兜底）。

**Why `TradingDeps` 字段而非 contextvars**：
- pydantic_ai `deps` 是官方设计的 run-scoped state 通道 —— 使用 ctx.deps 顺框架的势。
- 与现有 TradingDeps 风格一致（已有 19 字段，其中 `session_id` / `initial_balance` / `wake_min_minutes` 等都是运行态）。
- grep `cycle_id` 一次就找到所有读写点，显式可追。
- 测试直接 `TradingDeps(cycle_id="test-cycle", ...)`，比 contextvars 的 fixture 简单。

### 3.4 Scheduler 接入

`src/cli/app.py:run_agent_cycle` 内：

```python
# 现状（line 102）：
cycle_id = str(uuid.uuid4())[:8]
# ...
result = await agent.run(prompt, **run_kwargs)

# 改为：
cycle_id = str(uuid.uuid4())[:8]
deps.cycle_id = cycle_id   # ← 新增：mutate 到 deps，capability 才能读到
# ...
result = await agent.run(prompt, **run_kwargs)
```

**依赖的 scheduler 不变量（cycles 串行化）**：

`deps.cycle_id` mutate 方案假设**同一 session 的 cycles 永远不重叠**。已核查 `src/scheduler/scheduler.py`（86 行）：
- `start()` 是单 coroutine，主循环 `await _run_cycle()` 严格串行（line 45, 60, 62）—— **这是不重叠的全部 100% 保障**
- fill / alert 触发走 `trigger()` → `_pending_events.append()`（line 37-39）—— 不直接调 on_tick，只往队列追加
- `_interruptible_sleep` 被 wake_event 打断后回主循环 drain 队列 —— 仍串行
- `_cycle_running` 字段（line 28/70/76）**仅 set/clear、当前代码无读取点**，是观测态预留；不是互斥保障（grep 确认无 `if self._cycle_running:` 消费点）

结论：**当前设计下 deps.cycle_id mutation 无竞态**。

**注**：cycle 串行 ≠ "同 cycle_id 下只有一组 tool_calls 行"。`run_agent_cycle()` 内部有 `for attempt in range(3)` LLM retry（`cli/app.py:140-151`），若 attempt 0 调了若干 tool 后 LLM 超时，attempt 1 会重跑 agent.run()，recorder 实时写入 → 同一 cycle_id 下出现重复 tool_calls 行。本语义见 §8 风险表 "LLM retry 导致同 cycle_id 下 tool_calls 重复"条 + §4.4 p95 caveat。

**未来破坏场景**：若引入并发调度（多 cycle 同时跑、reentrant on_tick、worker pool），deps 字段 mutate 会产生 cycle_id 互相覆盖，recorder 落库错关联。届时需切 contextvars 或 RunContext 更深通道。本不变量已入 §8 风险表。

### 3.5 Agent 构造

`src/agent/trader.py:create_trader_agent`：

```python
def create_trader_agent(
    model: str, persona_config: PersonaConfig
) -> Agent[TradingDeps, str]:                  # ← 签名完全不变
    # 函数级懒加载 —— 与现有 26 个 tool 的 `from src.agent.tools_perception import ...` 风格一致
    # （技术上非必需：recorder 端的 TYPE_CHECKING + 字符串前向引用已足以破环；
    #  此处保持懒加载仅为风格统一，见 §3.2）
    from src.services.tool_call_recorder import ToolCallRecorder

    system_prompt = generate_system_prompt(persona_config)
    agent = Agent(
        model,
        deps_type=TradingDeps,
        output_type=str,
        instructions=system_prompt,
        capabilities=[ToolCallRecorder()],      # ← 唯一新增，recorder 内部从 ctx.deps.db_engine 读
    )
    # ... 现有 tool 注册不变 ...
```

**零破坏性**：`create_trader_agent` **函数签名不变**（调用方无需改）；现有 3 处调用（`src/cli/app.py:276` + `tests/test_trader_agent.py:11,19`）不需任何改动。capability 从 `ctx.deps.db_engine` 读 engine（见 §3.2）。

（注：§1.4 提到 `trader.py` "导出 `REGISTERED_TOOL_NAMES` 常量"是**模块级新增**，与 `create_trader_agent()` 签名不变是两件事 —— 常量独立于函数签名，现有 3 处调用不因常量导出受影响。）

---

## 4. 读取接口

### 4.1 扩展 `MetricsService`

在现有 `src/services/metrics.py` 追加：

```python
from datetime import timedelta

@dataclass
class ToolCallStats:
    count: int                            # count >= 1（零调用工具不入 dict，见 §4.2）
    ok_count: int
    error_count: int
    error_rate: float                     # error_count / count, 0..1（比值；脚本层 §4.3 乘 100 显示为 "%")
    p50_duration_ms: int
    p95_duration_ms: int
    error_breakdown: dict[str, int]       # {"TimeoutError": 3, "HTTPStatusError": 1}
    last_called_at: datetime              # 必有值（contract: 入 dict 的 tool 都至少被调用一次）


class MetricsService:
    # ... 现有 PnL 方法 ...

    async def get_tool_call_summary(
        self,
        session_id: str | None = None,     # None = 跨所有 session
        since: timedelta | None = None,    # None = 全部历史
        tool_name: str | None = None,      # None = 所有工具
    ) -> dict[str, ToolCallStats]:
        """按 tool_name 聚合 tool_calls。返回 {tool_name: ToolCallStats}。
        未被调用的工具**不在返回 dict 中**（调用方自判 "零调用"）。
        """
```

### 4.2 设计要点

**方法参数 `session_id` 覆盖实例绑定**：
- 现有 `MetricsService.__init__` 绑定 session_id（为 PnL 方法服务）。
- `get_tool_call_summary` 允许 `session_id=None` 表示跨 session 聚合（多 session 对比观察必需）。
- 不破坏现有 PnL 方法的实例语义。
- **接受的不一致**：同一个 `MetricsService` 实例里 PnL 方法是 "单 session 绑定"、tool-call 方法是 "可覆盖"。权衡是"不新增独立 `ToolCallMetricsService` 类以减改动面"。若未来 tool-call 聚合能力扩展（C 档 / cycle-level 关联）复杂度持续上升，再 split 到独立 service。

**`since` 用 `timedelta` 不用字符串**：
- 调用方 `get_tool_call_summary(since=timedelta(days=1))` 类型安全。
- 字符串（"1d" / "7d"）转换留给薄脚本层处理。

**p50 / p95 内存计算**：
- SQLite 无原生 percentile 函数。
- 观察期规模（单工具单日 ~100 call）下 `SELECT duration_ms WHERE ...` 全量拉进内存用 `statistics.quantiles()`（Python 3.13 原生）计算，性能无忧。
- **小 N 语义**：Python 3.13 的 `statistics.quantiles(data, n=100)` 在 `len(data)=1` 时返回 [单值 × 99]，不抛异常。因此 p50=p95=该单值，语义清晰、与直觉一致。无需在实施代码里为"单调用工具"加特殊分支。（pyproject 要求 `python>=3.12`；.venv 实测 3.13 通过。若生产部署在 3.12，plan 阶段需补一次 3.12 回归验证 —— 官方 What's New 显示 3.12 已接受 N=1，但以实测为准）
- **method 选择：`method='inclusive'`**（必写，不能用默认）。默认 `exclusive` 对小样本做线性外推，会产生 "p95 > 样本最大值" 的反直觉输出（实测 N=2 max=1 时 p95=1.85；N=5 max=4 时 p95=4.7）。观察期早期 tool 只被调 2-5 次时，这种外推会让观察者困惑"这个 p95 是实测还是外推"。`inclusive` 保证 **p50/p95 不会超出样本最大值**（实测 N=2 `[0,1]` 时 inclusive p95=0.95 ≤ max=1，严格有界；而 exclusive p95=1.85 超出 max）。
- **float → int 转换策略（已拍板：`int(x)` 截断）**：`ToolCallStats.p50_duration_ms: int` 接收 `quantiles()` 返回的 float，用 `int(x)` 截断而非 `round(x)` 四舍五入。理由：
  - 截断观察者倾向低估延迟，触发"是否过慢"判定更保守（reviewer 角度偏 alarm-safe）
  - `round()` 的 banker's rounding 在 .5 边界会产生"47.5 → 48 但 48.5 → 48"的跳动，数据表达不直观
  - 单次最多损失 <1ms 精度，对观察期延迟判断影响忽略不计
- 若将来单工具 call 量超 10K，切 SQL 近似（不改接口）。

**返回 dict（key=tool_name）**：
- REPL 下 `summary["get_market_data"]` 最直观。
- 未调用工具不在 dict 中 —— 语义最诚实（"从未调用" ≠ "空 stats"）。
- 若需要"所有工具含零调用"的视图，脚本层补：拉静态工具名单做 full outer join。

### 4.3 薄脚本 `scripts/tool_call_summary.py`

~30 行，不写单测（§5.2 说明）。

**功能**：
- argparse：`--session <name-or-uuid>`（可选；省略 = 全部 session）、`--since <1d|7d|all>`（默认 1d）、`--tool <name>`（可选，过滤单工具）
- 读 config 拿 sqlite 路径 → `init_db()` → `MetricsService`
- 按友好名字或 UUID 定位 `session_id`
- 调 `get_tool_call_summary(...)` 拿到 `dict[tool_name, ToolCallStats]`
- **cycle 计数单独查**：表头显示的 "12 cycles" **不**从 `get_tool_call_summary` 取（该方法返回只含 per-tool 聚合，无 cycle 计数字段）；脚本层对 `tool_calls` 额外跑一次 `SELECT COUNT(DISTINCT cycle_id) FROM tool_calls WHERE session_id=? AND created_at > ?`。保持 API 职责单一，不为表头显示扩 `ToolCallStats` 结构
- 拉静态 tool 名单补齐零调用行。**名单来源**：在 `src/agent/trader.py` 末尾导出一个模块级常量 `REGISTERED_TOOL_NAMES: list[str]`（手工维护 26 项 = 15 感知 + 10 执行 + 1 memory），供 scheduler 日志、脚本运行时统一引用。**runtime 不做反射**，理由：脚本常被观察期用户直接跑，对 pydantic_ai 版本稳定性敏感。漂移防护：§5.1 的 `test_registered_tool_names_matches_agent_tools` 测试用 `agent._function_toolset.tools`（复用 `tests/test_trader_agent.py:20` 既有 accessor）对照常量，脆弱路径唯一化
- Pretty print 对齐表格

**示例输出**：

```
$ uv run python scripts/tool_call_summary.py --session btc-trend --since 1d
Session: btc-trend-strategy (b3e2...)  |  Last 24h  |  12 cycles

Tool                           Calls   Err%    p50    p95    Last called
─────────────────────────────  ─────   ────   ────   ────   ──────────────
get_market_data                   48   0.0%   320ms   810ms  2m ago
get_position                      24   4.2%    45ms    92ms  2m ago   [TimeoutError×1]
get_macro_context                 12   0.0%  1200ms  2300ms  8m ago
get_derivatives_data               8   0.0%   180ms   290ms  2m ago
get_etf_flows                      1   0.0%  1800ms  1800ms  7h ago
get_stablecoin_supply              0   ─       ─       ─     never
set_next_wake                      0   ─       ─       ─     never
...
```

### 4.4 覆盖的观察问题

| 观察问题 | 查询方法 |
|----------|----------|
| 哪些工具 agent 从不调？ | 脚本输出 `Calls == 0` 行 |
| 哪些工具总报错？ | `Err%` 列 + `error_breakdown` |
| 某工具慢到影响 cycle？ | `p95` 列（注：LLM retry 会把同 cycle 的 tool 重复写入，Calls 虚高、p95 分布偏离单次真实；分析延迟瓶颈时用 `status=error` 排除明显 retry 干扰；详见 §8 retry 风险条） |
| 多 session 调用画像对比？ | 跑两次脚本 with 不同 `--session` |
| N7 Layer 1 重组触发判定 | "靠后工具"（ETF / stablecoin / set_next_wake）的 `Calls` 是否显著偏低（同受 retry 污染，但相对排序受影响小） |

### 4.5 不覆盖（留给未来）

- "某次决策前调了哪些工具" → 需按 cycle_id 展开；观察期若高频手查再写第二脚本
- 调用序列 / 时序分析 → 同上
- args / result 分析 → C 档不做

---

## 5. 测试与迁移

### 5.1 测试矩阵

**Storage 模型测试（`tests/test_storage.py` 扩展，2 测试）**：

| 测试 | 覆盖 |
|------|------|
| `test_tool_call_model_create` | 插入并回查，验证所有字段 round-trip（tool_name / status / duration_ms / cycle_id / session_id / error_type / created_at）|
| `test_tool_call_cycle_id_not_null` | `cycle_id=None` 插入必触发 `IntegrityError`（DB 层 NOT NULL 约束）|

**Recorder 单元测试（`tests/test_tool_call_recorder.py`，新文件，7 测试）**：

| 测试 | 覆盖 |
|------|------|
| `test_records_successful_tool_call` | 正常路径：tool 返回 → 写入一行 `status=ok`、`duration_ms>=0`、`error_type=None` |
| `test_records_failed_tool_call` | tool 抛异常 → 写入一行 `status=error`、`error_type="ValueError"`；**异常仍向外 raise**（验证不吞） |
| `test_control_flow_exception_not_recorded` | pydantic_ai 控制流异常（`ModelRetry` 等）直通不写 metrics 行；异常仍 raise |
| `test_recorder_does_not_break_tool_on_db_failure` | mock engine 让 commit 抛 → 外层 except 吞掉 → tool 返回结果正常给 agent；`log.error` 被触发 |
| `test_recorder_raises_runtime_error_when_cycle_id_missing` | `deps.cycle_id=None` 时 → `RuntimeError` 被外层 except 捕获 → `log.error` 暴露（不炸 agent）|
| `test_recorder_raises_runtime_error_when_db_engine_missing` | `deps.db_engine=None` 时同路径 |
| `test_duration_ms_monotonic` | duration_ms 从 `time.monotonic()` 计算，>=0；用小 sleep 验证量级 |

**MetricsService 单元测试（`tests/test_metrics.py` 扩展，6 测试）**：

| 测试 | 覆盖 |
|------|------|
| `test_tool_call_summary_empty` | 无数据返回空 dict |
| `test_tool_call_summary_aggregation` | 多 tool 多 call 聚合正确；**含 `last_called_at == MAX(created_at)` 断言**（`get_tool_call_summary` 内部 SQL 用 `MAX(created_at)` per tool_name） |
| `test_tool_call_summary_filter_session` | `session_id` 过滤 |
| `test_tool_call_summary_filter_since` | 时间窗过滤 |
| `test_tool_call_summary_error_breakdown` | error_type 计数正确 |
| `test_tool_call_summary_percentiles` | p50 / p95 计算正确（小数据集验证） |

**漂移防护断言（`tests/test_trader_agent.py` 扩展，1 测试）**：

| 测试 | 覆盖 |
|------|------|
| `test_registered_tool_names_matches_agent_tools` | 断言 `set(REGISTERED_TOOL_NAMES) == set(create_trader_agent(...)._function_toolset.tools)`；复用现有 `tests/test_trader_agent.py:20` 的同款 accessor，保持脆弱路径唯一（若 pydantic_ai 改内部结构，所有同款测试一起挂，预期一致）；防未来加 tool 忘更新常量导致脚本从"零调用"表静默丢工具。plan-stage grep (2026-04-20) 确认 pydantic_ai 1.78 `agent/` 无公开 tool accessor（`toolsets` property 返回 `Sequence[AbstractToolset]` 而非 tools 列表），`_function_toolset.tools` 仍是唯一路径，脆弱性 accepted |

**集成测试（`tests/test_tool_call_instrumentation.py`，新文件，1 测试）**：

| 测试 | 覆盖 |
|------|------|
| `test_agent_run_writes_tool_call_rows` | 跑一次 stub agent.run()，让它调 2 个工具；查 DB 有 2 行、`session_id` / `cycle_id` / `tool_name` 正确。**隐式验证**：(a) `ToolCallRecorder` 确实被注册到 agent；(b) `ctx.deps === agent.run.deps` 契约成立；(c) capability 执行路径未被未知层拦截。**显式断言 call-count**：用 `unittest.mock.patch.object(ToolCallRecorder, 'wrap_tool_execute', wraps=...)` 或 spy 方式断言 `wrap_tool_execute` 被调用 ≥ 2 次，防止 stub 若不 emit ToolCallPart 导致测试 false-green |

**删除的测试**：原计划的 `test_recorder_capability_registered` —— pydantic_ai 1.78 把 capabilities 存在私有 `agent._root_capability`（CombinedCapability 包装），无公有访问器。结构断言会 AttributeError；集成测试的写入验证已足够兜底"capability 被注册"这一契约。

**总新增测试：2 + 7 + 6 + 1 + 1 = 17**。664 → **681 passing**。

### 5.2 不写单测的部分

**薄脚本 `scripts/tool_call_summary.py`**：
- 薄壳：参数解析 + MetricsService 调用 + 格式化打印。
- MetricsService 单测已覆盖核心逻辑；脚本层手动跑一次验证。
- 未来若脚本膨胀出业务逻辑（如复杂过滤），再补 smoke test。

### 5.3 回归保护

**现有 664 测试必须全绿**。两处源码改动点的回归面：

1. **`TradingDeps` 新增 `cycle_id` 字段**：已核查所有 `TradingDeps(` 调用点全部用 kwargs（3 处：`src/cli/app.py:327` + `tests/test_trader_agent.py:55` + `tests/test_tool_enhancement.py:224`），末尾新增 `cycle_id: str | None = None` 默认值 `None`，对现有代码零影响。

2. **`TradingDeps.db_engine` 类型收紧**（`object | None` → `AsyncEngine | None`）：纯注解变更。`from __future__ import annotations` 已使注解 deferred（runtime 不 evaluate dataclass annotations），现有代码行为 100% 不变。已验证 `sqlalchemy.ext.asyncio.AsyncEngine` 作模块级 import 无循环风险（详见 §3.2）。

**`create_trader_agent` 签名**不变，3 处调用无需改动。另已确认 `src/services/model_manager.py:96` 的 `Agent(model, output_type=str)` 是 connectivity test 的一次性 agent，**不**经 `create_trader_agent`，不受影响。

### 5.4 迁移策略

**零迁移脚本 —— `Base.metadata.create_all()` 自动建表**。

已核查 `src/storage/database.py`：
- `init_db()` 第 21-22 行调 `await conn.run_sync(Base.metadata.create_all)`。
- SQLAlchemy 默认行为：只新建缺失表，不改现有表 → 新增 `tool_calls` 表无破坏性。
- Index 通过 `__table_args__` 声明，随 `create_all()` 同步建立。
- 第 25 行已启用 WAL pragma，多 session 并发写稳定。

**部署验证路径**：
1. 本地 dev 删除 / 新建 sqlite → `init_db()` → 建表含 `tool_calls`。
2. `uv run pytest` → 681 全绿。
3. 启动 sim session → 触发一次 cycle → `sqlite3 <db> "SELECT * FROM tool_calls LIMIT 5"` 确认写入。
4. 跑 `uv run python scripts/tool_call_summary.py --help` 确认入口可执行（不依赖 wheel 打包 —— `scripts/` 不在 `[tool.hatch.build.targets.wheel].packages` 内，也不需要在；直接文件系统路径调用）。
5. 跑 `uv run python scripts/tool_call_summary.py --session <name>` 确认读取 + 格式化。

---

## 6. Acceptance Criteria

实施完成需满足以下全部：

1. **Schema**：`tool_calls` 表新建，含 §2.1 所有字段和索引。SQLite 启动 `init_db()` 后表存在，索引生效。
2. **写路径**：`ToolCallRecorder` capability 被注入到 agent；每次 tool 调用（含异常）写入一行。
3. **cycle_id 完整性**：`run_agent_cycle` 在 `agent.run()` 之前 mutate `deps.cycle_id`；写入的 `tool_calls` 行全部有 `cycle_id`（非 NULL 约束兜底）。
4. **tool 异常不吞 + 控制流正确分类**：
   - 普通异常：recorder 写 `status=error` 行 + re-raise
   - 控制流异常（`ModelRetry` / `CallDeferred` / `ApprovalRequired` / `SkipToolExecution` / `ToolRetryError`）：**不写** metrics 行 + re-raise（框架控制流原行为不受影响）
5. **metrics 失败不影响 agent**：DB 写失败 / precondition check 失败（`RuntimeError`）→ `log.error` + 不影响 tool 返回给 agent。
6. **读路径**：`MetricsService.get_tool_call_summary(...)` 按 §4.1 签名返回；§5.1 的 6 个 MetricsService 单测全绿（与其他新增测试合计 17 项）。
7. **薄脚本**：`scripts/tool_call_summary.py` 能连 DB、查询、打印对齐表格；手动 smoke pass。
8. **回归**：现有 664 测试全绿；新增 17 测试全绿；总 681。
9. **B 档字段原则**：tool_calls 表无 args / result_preview / traceback 列。

---

## 7. 后续演进（观察期后）

**C 档扩展**（按需启动）：
- 加 nullable 列 `args` / `result_preview`（byte-limit，带脱敏规则）
- 加列后 migration 策略：`create_all()` 不改已有表 → 首次真 schema 演进触发 Alembic 引入议题
- 需同步更新 §4 读接口以支持 C 档字段聚合

**观察期候选脚本**（当某查询反复手写 ≥ 3 次才固化）：
- `scripts/cycle_tool_trace.py` —— 按 cycle_id 展开"此决策前调了什么"（启用前需补索引，见下）
- `scripts/tool_error_drill.py` —— 错误聚合 + 时间序列趋势

**索引 follow-up（启用 cycle-level 关联查询前置）**：
- 当前 `decision_logs.cycle_id` **无索引**。`scripts/cycle_tool_trace.py` 的 `LEFT JOIN decision_logs USING (session_id, cycle_id)` 查询会全表扫
- 若 cycle-level 分析成为观察期高频查询，补一次独立小 PR：`Index("ix_decision_logs_session_cycle", "session_id", "cycle_id")`；可与 `UNIQUE(session_id, cycle_id)` 约束合并处理（见 §2.3）
- 本轮 Iter 1 不做，防止 scope 蔓延到非 tool_calls 表

**retry 语义 follow-up（观察期首批 hardening 候选）**：
- §8 风险表已记："LLM retry 导致同 cycle_id 下 tool_calls 重复"；§4.4 已加 caveat
- 若观察期 Calls / p95 聚合确实因 retry 污染到干扰判断（如 "某工具是否慢"的结论反复翻车），启动独立小 PR 加 `attempt` 列（nullable `Integer`，零迁移）
- 同时 MetricsService 聚合可按 `(session_id, cycle_id, tool_name)` 去重后统计（函数级可选参数 `dedupe_retries: bool = False`）

**相关议题依赖本 metrics**：
- N7 Layer 1 重组触发判定（"靠后工具"调用率）
- 观察期第一轮 hardening PR 候选（N6 HTF volume / MA 斜率）的"是否真需要"判定
- CoinDesk 源是否保留的观察证据

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| pydantic_ai `wrap_tool_execute` 在未来版本 API 变动 | 只用官方导出的 `AbstractCapability` / `ValidatedToolArgs` / `WrapToolExecuteHandler` 等 public 类型，非 internal；集成测试会立即暴露；当前 1.78.0 的 API 自 v1.71 起稳定 |
| pydantic_ai `ctx.deps === agent.run.deps` 契约未显式保障 | 当前版本已实测一致；集成测试 `test_agent_run_writes_tool_call_rows` 隐式验证；若未来版本做转换/复制，测试会立即暴露并在 §3.1 注释警示 |
| pydantic_ai 新增控制流异常类（当前 5 类之外） | 白名单 `_CONTROL_FLOW_EXCEPTIONS` 硬编码；新增异常类需观察期发现 + 手工加入。风险低 —— 控制流扩展通常是大版本动作（v2.x） |
| LLM retry 导致同 cycle_id 下 tool_calls 重复 | `run_agent_cycle` 在 `agent.run()` 外层有 `for attempt in range(3)` 重试。recorder 实时写，attempt 0 调过的 tool 会连同 attempt 1 再次落库，聚合 Calls 列偏高。**本轮接受现状**：retry 本身是观察对象，保留真实痕迹反而有信息；若后续发现聚合受干扰严重，加 `attempt` 列做独立 PR（schema 加 nullable 列，零迁移） |
| 观察期数据库膨胀（每 session 每天 ~500 rows，长期 GB 级） | 每行 ~100 bytes，单 session 半年 ~10MB，可控；若真膨胀加 TTL 清理（独立 PR） |
| 写放大（DB 每 cycle 从 2-3 行 → 22-53 行，10x+ 量级跃变） | SQLite WAL 单 writer 串行 —— 多 session 写压力上升；若观察期发现 cycle 尾端 latency 明显变化 / writer queue 堆积，切批量缓冲（仍不改 schema）。作为观察期首批指标：监测 p95 tool_call insert latency 和 DecisionLog insert latency |
| DB 写入成为 cycle 延迟瓶颈 | inline await 每行 < 10ms（WAL 已启用）；观察期阈值 p95 > 30ms 触发批量缓冲评估 |
| cycle_id 未设置（未来新代码路径） | 3 层防护：Python 层 Optional / DB 层 NOT NULL / recorder 内 `raise RuntimeError`（不用 assert —— 避 `-O` 剥离）；fail-fast 即可被发现 |
| Scheduler 引入并发调度（reentrant on_tick / worker pool）破坏 cycle 串行不变量 | `deps.cycle_id` mutation 方案依赖 `src/scheduler/scheduler.py` 当前的串行 `_run_cycle()` 设计（§3.4 已记录）；若未来改并发，需切 contextvars 或 RunContext；观察期暴露为 "跨 cycle tool_calls 互串" |
| db_engine 未设置（新代码路径） | recorder 内 `raise RuntimeError` + 外层 except 转 `log.error`；metrics 降级，不阻塞 agent |
| 模块级 import 循环（trader ↔ tool_call_recorder） | 已实测触发 ImportError；规避：`tool_call_recorder.py` 用 `TYPE_CHECKING` + 字符串前向引用 `AbstractCapability["TradingDeps"]`；`create_trader_agent()` 函数级懒加载 `ToolCallRecorder`（§3.2 / §3.5 已落实） |
| 跨 session 聚合查询（`session_id=None`）全表扫 | `(session_id, tool_name, created_at)` 复合索引的 leftmost prefix 不覆盖 `session_id=None` 的跨 session GROUP BY tool_name 查询。SQLite 不做 skip scan → 全扫。观察期数据量小（单 session 每日 ~500 行 × N session）可接受；若跨 session 查询成为高频且数据量达百万级，补 `Index("ix_tool_calls_tool_time", "tool_name", "created_at")` 独立小 PR |

---

## 9. 约束重申

- **Session 规模**：本 iteration 单 session 一次 brainstorm → spec → plan → PR，后 3 轮分别独立 session 执行。
- **完成条件**：本 spec 被用户 approve → `writing-plans` skill 启动 → 实施 plan → PR → merge → 进入 Iteration 2。

---

## 10. Alternatives Considered

记录 brainstorm 阶段讨论过但被否决的方案，留审查和未来回溯依据。

### 10.1 数据存储（三选一）

| 方案 | 否决原因 |
|------|----------|
| JSONL 文件日志 | SQL 聚合查询用不了 → 观察期反复查询累；手工跨 session 对比困难；需独立解析逻辑 |
| 扩展 `DecisionLog` 加 `tool_calls` JSON 列 | SQLite JSON1 聚合慢；同 cycle 多次调 tool 需 append-update 同一行 → 并发写风险 |
| **新表 `tool_calls`（选中）** | per-call 一行，SQL 聚合自然；新表对现有数据零影响；并发写不竞争现有 `trade_actions` / `decision_logs` |

### 10.2 注入机制（三选一）

| 方案 | 否决原因 |
|------|----------|
| try/finally 手工包住 26 个 `@agent.tool` | 26 处改动，新增工具容易漏埋点（虽 lint/测试可兜） |
| 重写 `trader.py` 用统一 `_register_tool` 注册 | 300 行核心路径大重写，scope 炸开到 Iter 2 级别；风险回报比差 |
| **pydantic_ai `AbstractCapability.wrap_tool_execute`（选中）** | 对 26 个 tool **零改动**；单点注入；新增工具自动覆盖；API 自 v1.71 稳定（context7 查证） |

### 10.3 Runtime state 传递给 recorder

两个独立决策 —— cycle_id 怎么给 recorder 读到、engine 怎么给 recorder 读到。**共用同一原则：顺 pydantic_ai `deps` 官方通道**。

**10.3.a cycle_id 传递（二选一）**

| 方案 | 否决原因 |
|------|----------|
| `contextvars` 维护 cycle_id | 绕开 pydantic_ai 官方 `deps` 通道；隐式（读代码难追）；测试需 set contextvar fixture |
| **`TradingDeps.cycle_id` 字段 + scheduler mutate（选中）** | pydantic_ai `deps` 是官方设计的 run-scoped state 通道；与现有 19 字段风格一致；grep 可追；测试只需 `TradingDeps(cycle_id=...)` |

**10.3.b engine 传递给 recorder（二选一）**

| 方案 | 否决原因 |
|------|----------|
| 构造注入 `ToolCallRecorder(engine=db_engine)` | `create_trader_agent` 需新增必选参数 `db_engine`（破坏性）；与 `TradingDeps.db_engine` 既有字段形成双路径；"typing 妥协"的原因（`object \| None`）是历史债 —— 实测 `from __future__ import annotations` 已消除循环风险 |
| **从 `ctx.deps.db_engine` 读（选中）** | `TradingDeps.db_engine` 已在 tools 层广泛使用（`tools_perception.py:208` 等），非新增字段；收紧类型到 `AsyncEngine \| None` 即得类型安全；`create_trader_agent` 签名不变，零破坏性；recorder 更简（无 dataclass 字段） |

### 10.4 迁移策略（二选一）

| 方案 | 否决原因 |
|------|----------|
| 引入 Alembic 做 schema 迁移 | 当前改动只是加新表（无 alter），`Base.metadata.create_all()` 已覆盖；为加一张表开 Alembic 过度 |
| **`create_all()` 自动建表（选中）** | 已在 `src/storage/database.py:21-22` 使用；新表对现有数据零影响；未来若有 alter 需求再引入 Alembic（独立议题） |

### 10.5 字段档位（见 §1.3）

本轮选 B 档。否决 A（太单薄，无 error 信息答不了观察问题）和 C（args/result 敏感数据脱敏规则是独立 scope，且无具体观察问题先上容易猜偏）。详见 §1.3 + §7。

### 10.6 读取入口（三选一）

| 方案 | 否决原因 |
|------|----------|
| 观察期只手写 SQL，不提供方法 | 反复查询要写重复 SQL；跨 session 对比尤其累 |
| 一次性做正式 CLI 子命令（`tradebot metrics tools ...`） | 观察期前两周你还在摸"我想看什么"，预先浇筑 CLI 易错配；产品化 CLI 需要格式/参数/help 成熟 |
| **`MetricsService.get_tool_call_summary()` 接口 + 薄脚本（选中）** | 接口提供核心聚合逻辑 + 单测保障；脚本是 ad-hoc utility 可随时改；若某查询反复手写 ≥3 次再固化为 CLI（§7） |

### 10.7 为什么不直接用 pydantic_ai Logfire 集成

pydantic_ai 原生支持 Logfire（Pydantic 官方 SaaS 可观测性平台）。**不选**：
- 外部 SaaS 有成本和依赖；观察期需要**离线本地可查**的数据
- Logfire 的查询模型是 trace-centric，不便做"某工具一周调用画像"这种聚合
- 现有 DB 已在本地、已有完整 session/cycle 上下文，自己建表一致性更好
- 未来如要 tracing（而非 metrics），再叠加 Logfire 不冲突
