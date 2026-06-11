# 唤醒重设纪律 — persona 修法 — Design

## 背景与动机

sim #17（session `64b4ea1f`，1H，默认 scheduler 间隔 60 min）观察到：部分 cycle agent 未调用 `set_next_wake` / `set_next_wake_at`，且联系上下文非有意为之——agent 忘了调。取证（DB + narrative）定位如下。

### 现象量化

133 cycle = 122 `ok` + 11 `retry_exhausted`（后者是大写-timeframe 杀 cycle 崩溃，已在 `bf74000` 独立修复，与本议题无关）。122 个正常 cycle 中 **8 个未调用任何 wake 工具**。按触发类型遗忘率：

| 触发 | ok cycle | 遗忘 | 遗忘率 |
|---|---|---|---|
| scheduled | 32 | 1 | 3.1% |
| alert | 83 | 5 | 6.0% |
| conditional | 7 | 2 | 28.6% |

遗忘高度集中在「被提前唤醒」的事件触发型 cycle（8 个里 7 个为 alert/conditional）。

### 根因（两层）

**系统层** — 唤醒间隔是一次性值：`scheduler.py` 在每轮循环顶部无条件 `_next_interval = None`（consume-and-clear）；agent 不重设则回落 session 默认间隔。关键缺陷是 alert/fill/conditional **提前打断 sleep 时，待生效的 scheduled 唤醒目标随之被销毁**——原本待睡的目标剩余时间直接丢失，回落默认。scheduled 准点触发消费它合理，但事件打断不该顺带销毁与其无关的「等 1H 收盘」计划。

**心智层（实证）** — 8 个 forgot cycle **100%** 都在 summary 的「Active commitments」里写了一行 `Wake: HH:MM`，其中两个点破误解：`8f20fe82`（conditional）"wake **already set** for the 1H close"；`4cac6adf`（conditional）"**Duplicate** wake... **No time elapsed**"。而 `persona.py` 的「Active commitments」格式只定义 positions/orders/alerts 三类真持久状态——`Wake:` 是 agent 自发补进去的第四类。它把唤醒和 SL/TP/alert（确实跨 cycle 持久）归为一类，于是在「情况没变 / 已设过」的 cycle 顺理成章地跳过重设。persona 既未告知唤醒是一次性、被打断后需重设、不设则静默回落，也未把自身「别把未来意图写成文字、要执行它」的原则接到 wake 这个 case 上。

### 为何长期未被发现

8 个 forgot 的下一 cycle **全是 alert，间隔 1–19 min**，全部 < 60 min 默认。密集波动 alert 把静默回落全程掩盖——agent 许诺的 checkpoint 从未真武装，只是凑巧被 alert 覆盖。`40651793`（18:04 scheduled，许诺 18:32）是最干净证据：其后无 18:32 scheduled 唤醒，下一为 18:20 alert，真实下次 scheduled 应在 ~19:04。安静行情下这些自定 checkpoint 会被整段错过。

### 已排除的替代解释

- **token 截断**：forgot cycle 输出 token（均 4704，min 3273）≥ 正常 cycle（均 4986，min 2520），summary 完整。
- **调用被拒**：全 session 114 个 wake 调用全 `ok`，8 个 forgot cycle 零 wake 调用行——是根本没调，非拒绝。

## 目标 / 非目标

**目标**：消除「把 wake 当持久承诺」的心智症结，补齐「唤醒一次性、被打断后需重设」的事实，降低事件触发型 cycle 的遗忘率。

**非目标 / 明确不做**：

- **不改调度器代码**。系统层的结构性修复（事件打断时保留待生效唤醒目标，不静默回落）记为 follow-up「A1」，另立 brainstorm。本 iter 仅 prompt 侧缓解。
- **不改 `tools_descriptions.py`** 的 `set_next_wake` / `set_next_wake_at` 描述。工具描述保持 fact-only；「何时调用」属 persona 不属 fact-provider（工具设计原则 1）。
- **不加 `RuntimeConfig` 字段**。措辞用泛指「session 默认间隔」，不插入数字，保持纯 persona-string、零接线改动。

## 设计

### 为何 B（prompt）而非 A（system）

工具设计原则 8：agent 行为偏差是系统反馈，prompt nudge 是 last-resort。结构性正解是系统侧让唤醒目标在打断后存活（A1）。但 A1 触及刚 landed 的 scheduler wake-context 子系统，且与 `a018351`「preempted wake never leaks context」的子决策需调和，改动与风险更大。B 是纯 persona、低风险、可即时缓解的止血；A1 作结构性 follow-up。

### 改动 1 — Cross-Tool Behavior「Wake interval control」补事实

`src/agent/persona.py` `_build_layer1` 内（现「Wake interval control」bullet）追加一次性 + 重设事实：

> The interval you set is **one-shot** — it governs only the very next sleep, then resets to the session default. An interrupting alert/fill/conditional **cancels** the wake you set earlier, so after being woken early you must set it **again** to keep a non-default cadence.

措辞要点：用 `cancels` 而非 `consumes`——后者隐含「已兑现/被用掉」，恰是 agent 被提前唤醒时该建立的反向认知（wake 并未兑现）；`cancels` 准确传达「那个 wake 作废了、不会发生、必须重设」，直击 `8f20fe82` "already set" / `4cac6adf` "duplicate" 那类误判第 4 步（被提前唤醒后判断「已设过」而跳过重设）。

### 改动 2（defer，触发型 follow-up）— summary 叙述⌐动作耦合

候选：在 `persona.py` summary 纪律段（现「prefer setting an alert or limit order rather than writing it as text intent」）追加——只有本 cycle 真调了工具才准写 `Wake: HH:MM`，不许叙述仅意图的或假定从上轮延续的 wake。

**defer 理由**：改动 1 直击根因第 4 步——会重设的 agent，其 summary 叙述天然为真，改动 2 想禁的「写了没设」的谎言不会出现。且改动 2 不强制重设、只强制诚实输出（杠杆弱、可消解为省略而非行动）。**触发条件**：下个 sim 若仍 grep 到 agent 叙述 standing wake / "already set" 类推理，再加改动 2。

## 测试

drift-guard（沿用本仓 persona/docstring drift-guard pattern）：断言 `generate_system_prompt` 输出含改动 1 的关键短语（如 `one-shot` / `cancels`），并验证 `wake_max_minutes` 插值未被破坏。非行为测试——真实验证为下个 sim 的 narrative grep + ok-cycle 遗忘率（会话内口径，不跨 sim 裸比；cross-sim 行情不可比）。

## 影响面

- `src/agent/persona.py`：`_build_layer1` 一处 bullet 追加 ~2 句。系统 prompt 每 cycle 多读 ~2 句，token 成本可忽略。
- 测试：新增/更新 persona drift-guard。
- 无调度器 / 工具描述 / schema / 接线变动。
- follow-up：改动 2（触发型）、A1 系统侧持久化（结构性，另立 brainstorm）。
