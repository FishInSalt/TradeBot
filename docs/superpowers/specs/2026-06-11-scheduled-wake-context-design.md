# Scheduled 唤醒携带唤醒上下文 — Design

## 背景与动机

当前 agent 被唤醒时,不同 trigger 类型携带的"唤醒上下文"不一致:

| Trigger | 触发 prompt 是否带"为何唤醒"的 context | 来源 |
|---|---|---|
| conditional（fill） | ✅ 成交事件详情 | `_render_event_block` |
| alert（price-level） | ✅ 警报设置时的 reasoning | `_format_price_level_alert_trigger` |
| alert（volatility） | ✅ % 位移 | `_render_event_block` |
| **scheduled（dynamic wake）** | ❌ body 为 `""` | `_render_event_block` |

`set_next_wake` / `set_next_wake_at` 接收 `reasoning` 参数,但它仅落 `trade_actions` 表（audit），**不进入下一轮 prompt**——对 agent 实质是 write-only。scheduled 唤醒因此成为唯一"不告诉 agent 为何醒来"的 trigger。

**立项依据是架构一致性**，非实测痛点。已有实证（`.working/tool-audits/2026-06-10-set_next_wake-carry-reasoning.md`，session 64b4ea1f）：

- 趋势市下 29 个条件型 wake 以 scheduled 触发，scheduled cycle 100% honor 前轮 wake 意图，升级触发命中 0/29，无 thesis-drift。
- 该实证有两条功率边界：(1) 现有"agent 自抄进 decision + N=3 注入"通道已生效，混淆了"去掉 wake-context 是否仍一致"的反事实；(2) 趋势市是 inconsistency failure mode 的低功率 regime。

结论：当前 regime 无痛点，但 wake reasoning 对 agent write-only 是真实的机制缺口。本特性按"**所有触发唤醒的事件统一携带唤醒上下文**"的一致性目标立项，让 agent 每次被唤醒都能回答"我为何醒着"。

## 目标 / 非目标

**目标**：dynamic wake 真以 scheduled 身份触发时，把设置该 wake 时 agent 写的 `reasoning` 回显到该 scheduled cycle 的 prompt。

**非目标（明确排除）**：

1. **不覆盖被抢占的 wake**。69% 的条件型 wake 在定时器到点前被 alert/conditional 抢占——那些 cycle 真正的触发者是 alert，alert context 已携带。语义严格取"实际触发本轮者的 context"，被抢占的 wake 意图不视为 gap。
2. **不改 persona**。当前 agent 会自觉把 wake 意图重抄进 decision（已被 N=3 注入），加 echo 后存在轻微叠加——这正踩工具设计原则 3（信号唯一权威来源）的边：同一前向意图可能同时出现在 WAKE CONTEXT echo 与 N=3 recent-decision 注入里。但 `persona.py:132` 本就劝退 decision 写前向 intent，故这不是新建双源、而是激活当前 write-only 的 wake 通道。是否为消叠加而收紧 persona，是独立的、更高风险的数据驱动决定，不在本 scope。**上线后观察项（实现完成后跟进）**：用 session log grep 比对 WAKE CONTEXT echo 与 decision 复述的冗余 / token 抬升，作为是否收紧 persona 的触发条件。
3. **不新增持久化机制**。reasoning 仍照旧落 `trade_actions`；echo 走内存 context，无 schema 变更。注意精确表述：echo 文本会随渲染后的 prompt 一并落入既有列 `AgentCycle.user_prompt_snapshot`（`app.py:636`），故 reasoning 会因此多出现在 prompt 快照里——这是既有快照通道的顺带结果，非新增持久化机制。（另：`cycle_capture._capture_trigger_context` 对 scheduled 恒返回 `{"type":"scheduled_tick"}` 并丢弃 context（`cycle_capture.py:33-34`），非空 `str` context 不会污染 trigger-context 镜像。）

## 设计

### 数据来源：context 随 scheduler 流动（方案 A）

设 wake 时把 reasoning 一并塞进 scheduler，定时器到点时作为 `("scheduled", context)` 带出。相对"渲染时回查 DB"（方案 B）的优势：

- 与 alert/conditional 的 context 流动对称。
- `_next_interval` 本就是"每轮消费即清"语义，context 跟随同一生命周期 → 抢占场景天然正确（被事件打断时 context 随之丢弃，不泄漏到后续 scheduled fire），无需方案 B 那段"判断查到的 wake 是否对应本次 fire"的 staleness 消歧。
- 无 scheduled 路径 DB 读。

代价：触及 `set_next_wake_fn` 的定义/调用点（trader 定义 1 + tools_execution 调用 2 + app wiring 1，共 4 个点——注：此"4"是签名相关的调用/定义点，与下文"§数据流（4 个触点）"按**文件**计数的"4"含义不同，勿混）；进程重启丢失 in-memory context（可接受——`_next_interval` override 本就同样丢失）。

### 数据流（4 个触点）

1. **`src/agent/trader.py` `TradingDeps.set_next_wake_fn`**：类型 `Callable[[int], None]` → `Callable[[int, str], None]`，第二参为 wake-context 文本。

2. **`src/agent/tools_execution.py`**：两个调用点加第二参 `reasoning`——`:617` `set_next_wake_fn(minutes)` → `set_next_wake_fn(minutes, reasoning)`；`:669` `set_next_wake_fn(delta_minutes)` → `set_next_wake_fn(delta_minutes, reasoning)`。传 agent 写的**干净 `reasoning`**（函数入参原文），非落库用的 `interval=..min | ...` 前缀版本。**`set_next_wake_at` 取舍（显式声明）**：落库前缀 `target=.. resolves_to=.. interval=..min` 是在 `tools_execution.py:672-675` 为 audit 构造的，不在 `reasoning` 入参内；故 `set_next_wake_at` 的 echo 同样**只回显 free-text reasoning、不回显 resolved 目标时刻**（除非 agent 在 reasoning 里自带，如样例）。属可接受的一致性取舍。

3. **`src/cli/app.py:1166`**：`lambda minutes: scheduler.set_next_interval(minutes * 60)` → `lambda minutes, ctx: scheduler.set_next_interval(minutes * 60, ctx)`。

4. **`src/scheduler/scheduler.py`**：
   - `set_next_interval(seconds)` → `set_next_interval(seconds, context=None)`，存 `self._next_wake_context = context`（与 `self._next_interval` 并列，`__init__` 初始化为 `None`）。`context` 为纯 `str`（agent reasoning 文本），区别于 alert/conditional 携带的 dataclass context；`_render_event_block` 的 scheduled 分支按 `str` 直接渲染。
   - 主循环（`start()`）：在读取并清空 `_next_interval` 的同一处，对称读取并清空 `_next_wake_context` 到本地 `wake_ctx`。
   - 定时器到点（无 pending events）分支：`_run_cycle([("scheduled", wake_ctx)])`（原为 `("scheduled", None)`）。
   - 首 cycle（`start()` 开头的 `_run_cycle([("scheduled", None)])`）保持 `None`——启动时无 dynamic wake。
   - 被事件抢占分支（`if self._pending_events`）：照旧走 `events`，`wake_ctx` 不使用、随本地变量消亡。

### 渲染（`src/cli/app.py` `_render_event_block`）

新增 scheduled 分支（当前 scheduled 落到末尾 `return ""`）：

```
if trigger_type == "scheduled" and context is not None:
    return f"\n\nWAKE CONTEXT (set last cycle): {context}"
```

渲染样例（与 header 合并后）：

```
You have been woken up by a scheduled trigger (fired just now)

WAKE CONTEXT (set last cycle): 12:00 1H candle closes at 13:00 UTC. This is a
decision-grade candle — MA50 rejection at 62880 high + support test at 62400 low.
The close location determines whether breakdown continues.
```

- 仅 `trigger_type=="scheduled" and context is not None` 出。默认 interval tick（agent 未设 dynamic wake）→ `context is None` → 返回 `""`，行为不变。
- fact-only（原则 1）：label `WAKE CONTEXT (set last cycle):` 陈述事实"你上轮设的"；正文是 agent 自己的原话，逐字回显，不加 imperative。
- multi-event（N>1）路径：本设计只处理单一 `("scheduled", ctx)`；scheduled 不与其他 trigger 在同一 drain 内混合产生（scheduled 仅在 `_pending_events` 为空时 fire），故无需改 `_wake_header_line` 的 N>1 分支。
- echo 对 agent 自由文本 **verbatim、不截断**（`reasoning` 无长度 cap）；docstring 已引导 `brief`，实务足够，长文本会逐字回显——已知行为，本设计不引入截断。

### 边界

| 场景 | 行为 |
|---|---|
| 一轮多次调 set_next_wake | 每次覆盖 `_next_interval` + `_next_wake_context`，最后一次 win（与现有 interval 覆盖语义一致） |
| 进程重启后首个 scheduled | in-memory context 丢 → 无 echo（与 `_next_interval` override 丢失一致） |
| 默认 interval fire（未设 wake） | `context is None` → 无 echo |
| dynamic wake 被 alert/conditional 抢占 | 走 events 分支，wake context 丢弃；本轮以 alert/conditional 身份携带其自身 context |

## 测试

1. **`scheduler` 单测**：
   - `set_next_interval(s, ctx)` 后，定时器到点 fire 的 events == `[("scheduled", ctx)]`。
   - `set_next_interval(s)`（无 ctx）/ 默认 interval fire → `[("scheduled", None)]`。
   - 设 wake 后被 `trigger("alert", ...)` 抢占 → 本 cycle events 为 alert（不含 scheduled context）；后续默认 fire 不泄漏前次 wake context（== `None`）。
   - 一轮多调 `set_next_interval` → 末次 context win。
2. **`_render_event_block` 单测**：scheduled + 非空 context → 渲染 `WAKE CONTEXT (set last cycle): ...` 行；scheduled + `None` → `""`；不影响 conditional / alert 分支既有断言。
3. **集成**：dynamic wake（带 reasoning）→ 下一个 scheduled cycle 的 prompt snapshot 含该 reasoning 原文。
4. **既有断言更新（签名涟漪，必做）**：`tests/test_tools.py` 共 **8 处** `set_next_wake_fn.assert_called_once_with(N)` 因 mock 多收一个 `reasoning` 实参而失败——行 `496/551/585/598/626/710/721` + `699` 的 `assert_called_once_with(expected_delta)`；改为带第二参（如 `assert_called_once_with(10, "<reasoning>")` 或 `unittest.mock.ANY`，需补 `ANY` import）。
5. **回归**：现有 scheduler / event-render / N>1 multi-event 测试全绿。已核验其余 `("scheduled", None)` 测试引用（`test_scheduler` 的 bootstrap 断言与 `b != [("scheduled", None)]` filter、`test_cycle_log`、`test_p4_cycle_capture`、`test_wake_event_timestamp`）**均不破**——默认/bootstrap fire 仍为 `None`，仅"已设 context"路径变非空 `str`。

## 影响面

- 改动文件（src）：`src/agent/trader.py`（类型）、`src/agent/tools_execution.py`（2 调用点）、`src/cli/app.py`（wiring + render）、`src/scheduler/scheduler.py`（context 存取与 fire）。
- 改动文件（tests）：`tests/test_tools.py`（8 处 set_next_wake 断言更新，签名涟漪，见测试 §4）；新增 scheduler context / `_render_event_block` / 集成 用例。
- 无 DB schema 变更、无 persona 变更、无新工具。
- 实施在独立 worktree `feat/scheduled-wake-context`；spec 作独立 commit 先于代码（本迭代改动小、设计已定，跳过独立 plan 文档，spec 兼作 implementation guide）。
