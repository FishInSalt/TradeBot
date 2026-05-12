# TradeBot

加密货币永续期货自动交易 agent，基于 pydantic-ai + 多交易所抽象（OKX / Simulated）。

## 关键约定

### 工具设计与优化原则（必读）

任何工具相关的 spec / brainstorm / 议题立项前必读 **`docs/superpowers/principles/tool-design-principles.md`**。

8 条核心原则 + 1 条元原则（基于 sim #8 W2 实证归纳）:

1. **Fact-provider 不是 guard** — 工具名 + 输出 + docstring 全文都 fact-only；执行类用 explicit reject 不 silent clamp
2. **工具服务 agent 心智路径** — 从 narrative 提取 agent mental model；docstring 用完整 call→output 示例，不用 "X for Y" 指导语；反复手算 ≥3 次的事实信号是工具化触发条件
3. **信号唯一权威来源** — 每个信号定义一个唯一来源工具，避免 agent 跨工具对账
4. **信号补齐优先于新工具** — 现有工具的 underlying data 没被丢弃才考虑新工具；工具数量是 agent 选择延迟的物理约束
5. **接口闭环常用 pattern** — 高频 multi-call 拼凑是设计缺陷，应通过 list / preset / batch 让单调用闭环；default 反映实测主流场景
6. **失败语义区分** — 操作类异常 reject + retry；状态不存在 idempotent + ok with note
7. **输出与命名的表达友好** — 字段必带标签 / 单位 / 窗口；同名字段不同语义显式区分；sectioning 优于纯 alignment
8. **信任 agent + 工具优先** — Agent 行为偏差是工具反馈（反思顺序：能力 / 描述 / 默认值 / 接口），不是 prompt 失败；prompt nudge 是 last-resort，不是 fix-all 兜底
**（元）实证优先于直觉** — 议题立项前必查 sim 数据（args 分布 / 频率 / 多调 / 失败 / narrative grep）

冲突优先级与议题立项 checklist 见详细主档。

### 工作流约定

- **brainstorm/spec 产出**：落 `docs/superpowers/specs/<date>-<iter>-design.md`（不动 source code，per memory `feedback_brainstorm_decision_location`）
- **plan 文档**：落 `docs/superpowers/plans/<date>-<iter>.md`，作为独立 commit 先于 impl commits（per memory `feedback_plan_doc_commit_first`）
- **`.working/`**：迭代决策**前**的数据分析层（sim inventory / ergonomics / roadmap），ephemeral 不进 git
- **PR 工作流**：feature 分支提交，文档/计划先于代码 commit，重要产出物提交前等用户审阅

### 关键参考文档

- `docs/superpowers/principles/tool-design-principles.md` — 工具设计原则（本档 anchor 的详细主档）
- `docs/superpowers/specs/` — 各迭代设计 spec（按日期 + iter 名）
- `docs/superpowers/plans/` — 各迭代实施计划
- `.working/` — 临时分析层文档（不进 git）

## 项目结构

- `src/agent/` — pydantic-ai agent + tools_perception / tools_execution / tools_memory / persona / trader
- `src/services/` — 业务服务层（technical / metrics / cycle_capture / model_manager 等）
- `src/integrations/` — 交易所 / 新闻 / 宏观 / ETF 等外部数据源
- `src/storage/` — SQLAlchemy models + database + views
- `src/scheduler/` — APScheduler 调度
- `src/cli/` — CLI 入口（Rich + wizard）
- `scripts/` — 开发期 / 观察期分析与诊断脚本（如 `analyze_sim.py` / `diff_sim.py` cross-sim analytics、`fetch_session_ohlcv.py` OHLCV helper、`tool_call_summary.py` 工具调用统计、各 iter 的 smoke/probe/capture 脚本）
- `tests/` — pytest 测试集（1487 tests collected as of 2026-05-11）
