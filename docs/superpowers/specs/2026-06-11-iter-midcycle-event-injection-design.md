# iter-midcycle-event-injection Design

（代码锚点基于 worktree HEAD `85fa217`，2026-06-11 同步 origin/main 后逐一复核；
per memory `spec-anchors-worktree-head`。）

## Context

sim #17（`64b4ea1f`，2026-06-09/10，133 cycles）forensic 暴露事件送达机制的结构性缺口：
事件只能在 cycle 边界送达（wake prompt 的 `trigger_context`），cycle 运行中触发的事件
入 `_pending_events` 堆等待，cycle 结束后由 back-to-back cycle 消费
（`scheduler.py:115-116` pending 非空立即返回，实测接力 gap 0.0-0.3s）。

**实测数据**（sim #17）：

- 110 个带时间戳事件中 **38 个（35%）在 cycle 运行中触发**，送达等待中位 82s / p90 140s /
  max 148s（= in-flight cycle 剩余 wall time）；9 次 SL fill 中 4 次 mid-cycle。
- **盲区 artifact**：cycle `7a928cba` 的 decision 以已被止损平掉 80s 的仓位为前提
  （"Short from 61563.30… only 25 pts from SL"）——真实交易员工作流不可能产出的脏决策记录，
  污染本阶段唯一产物（sim 决策数据）。
- **echo artifact**：4 次 mid-cycle fill 中 3 次 agent 已在 cycle 内通过 live 感知
  （`get_position` / `close_position` 幂等 no-op）自行推断出成交，排队事件仍再起一个
  重复 cycle（`8f20fe82` "No actions needed… unchanged from the prior cycle" /
  `4cac6adf` "Duplicate wake from the SL fill already processed"），~200k tokens（全场 ~2%）。
- **可达性回放**：4/4 mid-cycle fill 之后 agent 均有后续工具调用（fill 后 16-28s 内），
  工具边界注入对全部案例可达，含唯一全盲案例（延迟 86s → 28s）。

**Fidelity 依据**：还原度盘点 memory `design-fidelity-gaps` 的 Strong-match 条目
"不被噪音中断决策（cycle 内事件 buffer 入队）"基于 W3 sim #10 的 **"0 cycle 内事件"** 实测
——该前提已被 sim #17 推翻。真实交易员对自己成交的感知是即时、推送式、不可关闭的；
对自设关键位告警同理。本 iter 把"事件在 agent 下次抬眼（工具调用返回）时进入视野"作为
机制还原，同时保持"不打断推理流"（不 abort、不重启）。

**方案比选**（brainstorm 2026-06-11）：A 工具边界 capability 注入（采纳）/
B 专用轮询工具（否决：原则 4 工具数量约束 + `set_next_wake_at` adoption 2.1% 教训
——agent 不知道有事件时才最需要被告知，无法指望主动轮询 + echo 不消失）/
C conditional 到达即中止重启 cycle（否决：执行类工具可能已跑一半，重启产生
"无决策记录的孤儿动作"；丢弃 in-flight 推理 token；pydantic-ai 无 mid-run resume）。

**Scope 演化**（brainstorm 决策链）：fill-only → fill+price_level → **全部三类事件**。
最终依据：① 感知调用集中在 cycle 前段，后段长推理期间的波动爆发是真实盲区
（`7a928cba` 即波动告警唤醒后波动继续、SL 被打穿）；② 送达语义全局唯一
——"任何事件在下次工具边界到达"，不产生按类型分裂的双轨心智；③ scheduler API
退化为无过滤全弹，实现更简；④ sim #17 的 13 个 mid-cycle percentage 延迟消费
cycle 一并被吸收。

## Goals / Non-goals

**Goals**：① cycle 运行中触发的事件（fill / price_level_alert / percentage_alert）在
agent 下一次工具调用返回时以事实块注入，注入即消费；② **echo 归零、back-to-back 大幅下降**
——echo 定义为"agent 已收到的事件再次触发唤醒"，注入即消费下结构性归零（事件在末次工具
调用后到达时 agent 无任何通道已知它，兜底唤醒是首次送达、非 echo）；back-to-back 唤醒
数量预期大幅下降但非零（兜底通道存在）；③ 注入块与 wake prompt 事件块同源渲染
（信号唯一权威来源）；④ persona 送达契约同步更新；⑤ 注入有完整取证
（DB 列 + session log 时序内联）；⑥ `v_alert_lifecycle` 接入注入送达通道。

**Non-goals**：不打断/重启 in-flight 推理（方案 C 否决）；不新增 agent 工具（方案 B 否决）；
不改 wake prompt 现有格式（N==1/N>1 字节不变）；不改 drain cap 20 / 优先级堆语义
（主循环唤醒时 drain 退化为兜底通道但逻辑不动）；不动 OKX live 路径（事件源 `on_fill`/`on_alert`
回调在 exchange 抽象层，sim/live 同接口，注入机制天然两侧可用，但本 iter 仅以 sim 验证）。

## §1 Scheduler：`drain_pending_events()`

`src/scheduler/scheduler.py` 新增同步方法：

```python
def drain_pending_events(self) -> list[tuple[str, Any]]:
    """Pop ALL pending events in heap priority order. Used by mid-cycle injection."""
```

- 全弹（无类型过滤），堆优先级序（conditional > alert > scheduled——scheduled 实际不会
  在堆中出现，`trigger()` 只被 fill/alert 回调调用）。
- 一次弹出 >5 个时 `logger.warning`（信号、不丢弃；阈值 = 略高于 sim #17 实测
  mid-cycle 批峰值 3，纯观测信号、非 tuning knob。注：与主循环 cap 注释的
  "observed max 4" 属不同测量窗口——前者 = 单 cycle 运行中累积的 pending 峰值，
  后者 = 历史 wake 批大小峰值）。
- **不碰 `_wake_event`**：注入清空堆后，cycle 结束回主循环，`_interruptible_sleep`
  入口 `if self._pending_events: return`（scheduler.py:115-116）不命中 →
  `_wake_event.clear()` 后正常睡眠。堆空 + event set 残留不产生虚假唤醒
  （clear 在 wait 之前）。
- 与主循环唤醒时 drain（`scheduler.py:85-96`，喂给下一 cycle）的关系：后者保持原样，
  作为"末次工具调用之后才到达的事件"的兜底通道，语义、cap、warning 均不变。
- 同一 asyncio loop 内同步堆操作，无并发竞争面。

配对新增同步方法 `requeue_events(events: list[tuple[str, Any]]) -> None`（§2 失败
语义的回滚句柄）：逐个 heappush（type+context 原样、sequence 重新分配——同批相对序
保持，跨批次序不保证，堆消费本就按 priority 而非全局 FIFO）+ `_wake_event.set()`。
不绕 async `trigger()`（scheduler.py:61）——堆 push 本无 await，sync 接口让
capability 失败路径免起协程。

接线（`src/cli/app.py`，`set_next_wake_fn` 同模式，app.py:1195）：

```python
deps.drain_pending_events_fn = scheduler.drain_pending_events
deps.requeue_events_fn = scheduler.requeue_events
```

`TradingDeps`（trader.py:32）新增字段
`drain_pending_events_fn: Callable[[], list[tuple[str, Any]]] | None = None` 与
`requeue_events_fn: Callable[[list[tuple[str, Any]]], None] | None = None`
（None = 注入不可用，capability 直通——单测/旧调用路径零侵入；两字段须同时非 None
才启用注入，防止"可弹不可回滚"半态）。

## §2 `MidCycleEventInjector` capability

新文件 `src/services/midcycle_injector.py`，骨架同 `ToolCallRecorder`
（`tool_call_recorder.py:94` 的 `AbstractCapability.wrap_tool_execute`），注册于
`trader.py:110` `capabilities=[...]`。

执行流（在 `result = await handler(args)` **成功返回后**）：

1. `deps.drain_pending_events_fn` / `deps.requeue_events_fn` 任一为 None 或
   `result` 非 `str` → 原样返回（不弹堆）。
2. `events = drain_pending_events_fn()`；空 → 原样返回。
3. 渲染注入块（§3 共享渲染器 + §4 格式）。
4. 注入记录 append 到 `deps.injected_events_log`（§6 取证累积器）。
5. 返回 `result` + 注入块（记录先于交付——保证 §6 不变量"注入成功 ⇔ 有取证记录"）。

**失败语义**（送达保证不降级）：

- handler 抛异常 / 控制流信号（`_CONTROL_FLOW_EXCEPTIONS` 同集合）→ 直通不弹堆
  ——事件留在堆中走兜底通道。
- 步骤 3/4 任何异常 → `requeue_events_fn(events)` 整批回滚（§1），返回**原始**
  `result`。事件退化为兜底送达（现状行为）——送达保证降速不降级。
- 整个注入路径 try/except 包裹，绝不污染工具返回（与 ToolCallRecorder swallow 契约一致）。
- **被丢弃的 run ⇒ 注入回滚**：`run_agent_cycle` 的 retry 循环（app.py:654，
  `except Exception` :711 退避后**全新重跑** `agent.run`）与两条终态 forensic 路径
  （usage_limit :671 / retry_exhausted :724）会丢弃 run 的推理输出——被该 run 消费的
  注入事件若不回滚，将永远到不了任何存活决策（送达盲区换处藏身），且取证记录与最终
  decision 错位（污染 §9 验收④）。规则：retry 重试前、两条终态 forensic 写库前，
  对 accumulator 中本 run 的注入记录逐条 `requeue_events_fn(raw)`（§6 raw 字段）+
  清空 accumulator——事件经兜底通道重新送达（retry 场景下通常被下一 attempt 的首次
  工具调用重新注入），被丢弃 run 的 `injected_events` 落 NULL。

**框架交互（源码级已核，测试锁定防版本回归）**：

1. **存在性前提（源码级已验证，集成断言保留为 gate）**：`wrap_tool_execute` 返回的
   修改后字符串进入 `ToolReturnPart.content` 且被 LLM 看到——已核
   pydantic-ai 1.78 `tool_manager.py:311-334`：capability 返回值即 `tool_result`
   直接向下游 return（项目内 `ToolCallRecorder` 只原样 return 过，
   tool_call_recorder.py:121，此前从未依赖修改路径）。集成测试仍断言
   ToolReturnPart.content 含注入块，防版本升级回归。附备选注入点：同链路的
   `after_tool_execute` hook（返回值同样替换 result），plan 时与 wrap_tool_execute
   二选一定型。
2. **组合顺序（已定型）**：pydantic-ai `capabilities/combined.py` 用
   `reversed(self.capabilities)` 链式包裹——注册
   `[MidCycleEventInjector(), ToolCallRecorder()]` 时 Injector 在最外层，注入发生在
   Recorder 计时闭合之后，`tool_calls.duration_ms` 不含注入耗时（duration 语义 =
   工具本体执行时长，保持）。单测锁定该注册顺序（共存断言见 §9）。

## §3 事件渲染器提取（共享模块）

搬移清单（`src/cli/app.py` → 新模块 `src/services/event_render.py`；app.py
wake prompt 路径与 `midcycle_injector.py` 注入路径均从该模块 import）：

| 成员 | 现位置 | 说明 |
|---|---|---|
| `_render_event_block` | app.py:417 | 含 scheduled-wake-context echo 分支（:430-431，spec 2026-06-11）——一并搬移；注入路径不会命中它（堆中只有 fill/alert），回归覆盖该分支 |
| `_wake_time_suffix` | app.py:138 | |
| `_format_price_level_alert_trigger` | app.py:374 | |
| `_format_event_age` / `_format_relative_time` | app.py:123 / :100 | `_wake_time_suffix` 的传递依赖 |
| `_format_event_breakdown(events)`（**新提取**） | 自 `_wake_header_line` N>1 分支（app.py:405-414）抽出 | breakdown 拼接（`1 fill, 2 alerts`，fill 在前）唯一权威来源；`_wake_header_line` 改为调用它（行为保持），§4 注入 header 复用它 |
| `PriceLevelAlertInfo` import | — | `_render_event_block` / `_format_price_level_alert_trigger` 的 isinstance 依赖 |

提取时 `_render_event_block` / `_wake_time_suffix` 的时间基准形参 `cycle_started_at`
改中性名 `now`——wake 路径传 cycle_started_at、注入路径传注入时刻（§4），避免把
"注入时刻"塞进名为 cycle_started_at 的参数造成语义重载。

- **行为保持搬移**：wake prompt 输出做 byte-identical 回归（既有 wake prompt 测试
  若锚定 app.py 私有名则改 import 路径，断言不变）；`_wake_header_line` 本体留在
  app.py（wake header 与注入 header 语义不同，只共享 breakdown 拼接）。
- fee/PnL/equiv-round-trip 计算只存在一份——注入块与 wake 块数字永不打架。
- `_render_event_block` 含 `await deps.exchange.get_contract_size()`（full-close 分支）
  ：sim 路径为内存读，注入点调用无 IO 顾虑；该 await 失败由 §2 失败语义兜底。

## §4 注入块格式

追加在工具返回文本之后（空行分隔）：

```
=== NEW EVENTS TRIGGERED (1 fill, 1 alert) ===
IMPORTANT EVENT: stop triggered — BTC/USDT:USDT 59.67 @ 61800.0, Fee: -36.88 USDT, PnL: -65.70 USDT (gross) / -103.21 USDT (this fill, equiv-round-trip) — filled 2026-06-09 22:14 UTC (23s ago)
PRICE LEVEL ALERT: BTC/USDT:USDT reached 61630.50 (alert id=f3fd8021 below 61634.00 — 22:00 1H bar low break revives breakdown thesis) — fired 2026-06-09 22:14 UTC (8s ago)
```

（示例行为 §3 渲染器逐字输出：fill 块 `IMPORTANT EVENT:` / price_level 块
`PRICE LEVEL ALERT:`（app.py:380）/ percentage 块 `PRICE VOLATILITY ALERT:`
（app.py:473）——三前缀经 `7f979c4` 唤醒标签消歧后的现行字面量，display.py:941
`_EVENT_PREFIXES` 为同步锚；时间后缀 ` — {verb} {绝对UTC} ({age})` 复用
`_wake_time_suffix`；渲染块自带的 `\n\n` 前缀在注入装配时归一为单行分隔。）

- **header**：`=== NEW EVENTS TRIGGERED ({breakdown}) ===`，breakdown 复用 §3
  提取的 `_format_event_breakdown`（`1 fill` / `2 alerts` / `1 fill, 2 alerts`，
  fill 在前——与 wake header 同一来源，零漂移面）。
  常量前缀 `NEW EVENTS TRIGGERED` 是 narrative forensic 的 grep 锚点。
- **不带送达语义从句**（brainstorm 决策：逐片段可推断性检验——"occurred during this
  cycle" 可由块出现位置 + 相对时间后缀推断；"no separate wake follows" 不可推断但属
  接口契约，家在 persona（§5）而非每次注入重复；header 与 persona 中的
  `NEW EVENTS TRIGGERED` 逐字一致互为锚点）。
- **事件正文零新格式**：逐条复用 §3 渲染器，与 wake prompt 块逐字同构
  （`IMPORTANT EVENT:` / `PRICE LEVEL ALERT:` / `PRICE VOLATILITY ALERT:` 三前缀
  + 相对时间后缀），agent 无新表达学习成本。
  相对时间基准用注入时刻（`datetime.now(timezone.utc)`）而非 cycle_started_at
  ——"23s ago" 指距注入此刻，语义更准。
- 排序：堆优先级序（fill 在前），与 wake prompt 全局一致。
- fact-only 审计：块内无任何指令性措辞（原则 1）。

## §5 Persona 送达契约更新

LLM 可见文本三通道审计结果与处置：

| 位置 | 原文 | 处置 |
|---|---|---|
| persona.py:126 | "…always interrupt sleep. The interval you set is **one-shot** … An interrupting alert/fill/conditional **cancels** the wake you set earlier…" | **改**：追加 mid-cycle 送达分支 + 注入与 one-shot wake 的边界（下文） |
| persona.py:122/123/124 | "When woken by a limit-order fill / a fill that closed / a price alert" | **改**：通道中性化，指引内容不变 |
| persona.py:121 | "you will be notified when they fill" | 不动（通道中性，仍真） |
| tools_descriptions.py:15/:31 | "Alerts, fills, and conditional triggers always interrupt scheduled wake" | 不动（描述睡眠抢占，注入只发生在非睡眠期，仍真） |
| tools_execution.py:155/:248 | "You will be notified when filled." | 不动（仍真；OKX 异步分支文本——sim 市价走同步分支返回 "Filled:"，agent 在 sim 期不可见） |

persona.py:126 bullet **末尾**插入（"…set it **again** to keep a non-default
cadence." 之后、"Allowed range:" 之前——cancel 规则先于新段陈述，"as above" 与
"unlike an interrupting wake" 的指代对象均已在上文，无前向引用）：

> If an event fires while a cycle is already running, it is delivered in your next tool
> result under a `NEW EVENTS TRIGGERED` header and consumed there — no separate wake
> follows, and unlike an interrupting wake it does **not** cancel the next-wake interval
> you set. An event that fires after your last tool call of the cycle still arrives as
> a normal wake — cancelling the interval as above.

persona.py:122-124 的 "When woken by X" → 通道中性（如
"When a fill notification arrives (wake trigger or NEW EVENTS block), …"）。

**与 wake-rearm 纪律（`7c6e264`，persona:126 one-shot 语义）的交互——正向**：
旧行为下 mid-cycle 事件在主循环顶端吃掉 agent 设的 one-shot interval（back-to-back
cycle 消费它，agent 须重设——wake-forget 根因的 manifestation 之一）；注入即消费后
堆空，agent 设的 wake 得以兑现。契约新增句的 "does not cancel" 即此边界：cancel
语义只属于"睡眠期被事件打断"，注入不触发它。

定性：persona:126 本就是 wake 机制的接口文档，机制变更后维持文档准确属契约维护，
不是 behavioral nudge（原则 8 合规）。

## §6 取证：`agent_cycles.injected_events`

- `TradingDeps` 新增 `injected_events_log: list[dict] = field(default_factory=list)`；
  `run_agent_cycle` 在设置 `deps.cycle_id` 处（app.py:603）同步 `clear()`（per-cycle 复位）。
  前置：trader.py:4 `from dataclasses import dataclass` 需补 `field`
  （TradingDeps 现无 default_factory 字段先例）。
- 每条注入记录：

```json
{"event": <_capture_trigger_context 单事件 capture，cycle_capture.py:24 复用>,
 "raw": <原始 (trigger_type, context) 元组——§2 被丢弃 run 回滚时 requeue 用，落库时剥离>,
 "after_tool": "get_taker_flow",
 "offset_ms": 73000}
```

  `event` 复用 wake 通道的同一单事件 capture 函数（批处理版 `_capture_trigger_contexts`
  在 :84，落 `trigger_context` 列）——取证 schema 唯一来源；`offset_ms` =
  注入时刻相对 `cycle_started_at`。
- `agent_cycles` 新列 `injected_events TEXT NULL`（JSON 数组；无注入 = NULL，与
  `trigger_context` 同形态）；alembic migration 一支。
- `run_agent_cycle` 的**全部 3 个 `AgentCycle` 写入点**均带 `injected_events` 列：
  成功（app.py:828）正常落 accumulator 序列化；usage_limit_exceeded（:671）/
  retry_exhausted（:724）按 §2 "被丢弃的 run ⇒ 注入回滚" 规则在写库前已
  requeue + 清空 → 落 NULL。
- 不变量精化为"注入对**存活** run 成立 ⇔ 有取证记录"，且是**内存累积器级**保证
  （§2 步骤 4 先于交付）；DB 序列化在 `agent.run` 之后，写库失败面与
  `trigger_context` 同构（注入块此时早已交付给 LLM，不可回滚）。§2 步骤 4 的
  回滚路径属 defensive（`list.append` 实际仅 MemoryError 级才抛），保留以维持
  不变量的形式完整。

## §7 `v_alert_lifecycle` 接入注入通道

现状（HEAD `85fa217`）：triggers CTE 已是 `json_each` + `json_type()` array/object
双分支形态（views.py:106-128，`cb7d7db` #71 落地，配套测试
`tests/test_v_alert_lifecycle.py:126/149/175`）——数组格式解析**无既有 bug**。
（注：sim #17 DB 文件内的 view DDL 是建库时快照，曾据此误判"view 失明"；
per memory `spec-anchors-worktree-head`，view 现状以 `src/storage/views.py` 为准。）

本 iter 唯一新增：price_level alert 经注入通道消费时不再出现在任何
`trigger_context` 中，view 须接入第二通道，否则注入的 alert 在 view 中
永远 `active`：

- triggers CTE UNION `json_each(agent_cycles.injected_events)` 分支（取
  `'$.event.alert_id'` / `'$.event.current_price'`，过滤
  `'$.event.type'='price_level_alert'`），形态与现有 array 分支一致。
- 新增 `delivery` 列（`'wake'` / `'injected'`）。
- 现有列语义不变（`triggered_at` / `triggered_price` 两通道同名同义；
  injected 分支的 `triggered_at` 用 cycle `created_at` —— 与 wake 分支同粒度）。

## §8 Session log 渲染

注入文本随工具返回字符串进入 `ToolReturnPart.content`，`▾ Action` 节按调用顺序渲染
（display.py `_render_action`），`_render_tool_body`（display.py:589）按 `=== … ===`
内容 dispatch——注入块**自动**以独立小节渲染在其所属工具调用名下，时序内联零新增渲染逻辑。

唯一改动：`_FULL_KEEP_SECTION_PREFIXES`（display.py:576）加 `"NEW EVENTS TRIGGERED"`，
事件行免于 `_clip_body` 裁剪。

不在 Header / Context 节重复渲染注入信息（Trigger 行语义 = 唤醒原因，注入非唤醒）。

## §9 测试与验收

**单测**：

- scheduler：`drain_pending_events` 优先级序 / 空堆返回 `[]` / >5 warning /
  drain 后 `_interruptible_sleep` 正常睡眠（无虚假唤醒）；`requeue_events`
  回滚后兜底通道可送达（drain→requeue→主循环 drain 同批等价）。
- capability：成功返回才注入；非 str 返回直通；`drain_pending_events_fn` 或
  `requeue_events_fn` 为 None 直通；handler 异常直通且不弹堆；渲染/记录失败
  `requeue_events_fn` 回滚 + 返回原始 result；累积器记录 `after_tool`/`offset_ms`；
  与 ToolCallRecorder 共存（两 capability 注册顺序下行为一致）。
- 渲染器提取：wake prompt byte-identical 回归（N==1 / N>1 / fill 三分支 /
  两类 alert / scheduled-wake-context echo 分支）；`_format_event_breakdown`
  提取后 `_wake_header_line` 输出不变。
- 注入块格式：header breakdown 计数 / fill 在前排序 / 相对时间基准 = 注入时刻。
- persona：§5 改动后的 drift guard（断言 `NEW EVENTS TRIGGERED` 在 persona 文本与
  injector 常量逐字一致——防两处漂移）。
- migration：升级後新列可写可读 NULL/数组两态。
- view：injected 通道 `delivery='injected'` 可见 / 双通道 UNION 不重不漏 /
  既有 wake 通道行为不回归（数组 + legacy 兼容测试已存在于
  `tests/test_v_alert_lifecycle.py`，仅需补 delivery 列后的断言适配）。
- display：`NEW EVENTS TRIGGERED` 小节 full-keep 不裁剪；无 section 标记的
  plain 工具返回 + 注入块的渲染回归（`_parse_sections` 归一化路径下原文本
  渲染不变，仅追加小节——防未来渲染管道改动引入模式分叉）。

**集成**（仿 sim exchange 路径）：

- happy path：cycle 运行中 `on_fill` 触发 → 下一工具返回含注入块 → cycle 结束无
  back-to-back conditional cycle → `injected_events` 列有记录 → session log
  Action 节对应工具下可见注入小节。
- retry 交互（§2 被丢弃 run 回滚）：attempt 1 注入后人为抛瞬时异常 → 断言事件被
  requeue 且 attempt 2 经重新注入或兜底送达、最终 `injected_events` 仅含存活
  attempt 的记录；usage_limit / retry_exhausted 终态 → `injected_events` 为
  NULL 且事件由下一 cycle 的 trigger_context 送达。
- 反向断言：同步市价 open/close 不产生自注入（同步路径不经 `trigger()`，
  simulated.py 仅 :682 matching-loop dispatch——lock 防回归）。

**验收信号**（下次 sim run）：① echo 归零（decision 含 "duplicate/already
processed" 类表述归零——结构性保证，见 Goal②）+ back-to-back 唤醒数量
vs sim #17 基线（38 处 mid-cycle 事件中预期仅"末次工具调用后到达"残量）大幅下降；
② mid-cycle 事件感知延迟中位 82s → 下一工具边界（预期 <30s）；③ 无"以已平仓位
为前提"的脏 decision；④ adoption forensic：`grep "NEW EVENTS TRIGGERED"`
（session log）× decision 文本交叉验证 agent 是否 ground 注入内容；
⑤ per-cycle token 增量监控（预期 echo 消失的节省 > 注入文本成本）；
⑥ wake-rearm 侧效应观察：agent 设的 one-shot wake 因不再被 mid-cycle 事件
吃掉，wake-forget manifestation 预期下降（与 memory `wake-rearm-discipline` 关联）。

## §10 Follow-ups（不在本 iter scope）

- memory `design-fidelity-gaps` 更新：追加 G11（execution/event-awareness latency，
  resolved by 本 iter）+ Strong-match "不被噪音中断决策" 条目修订（buffer 入队前提已变，
  改述为"注入不打断推理流"）——merge 后做。
- 观察项：若 sim 数据显示 agent 对注入块消费语义零困惑且 persona 契约线冗余，
  可裁 persona 分支从句（反向亦然：若出现"等待不存在的 wake"narrative，在 wake
  契约段加固）。
- `v_alert_lifecycle` 的 `cancel_attempts` CTE 等其余部分不在本 iter 触碰范围
  （PR #42 I-3 已知坑维持现状）。
