# WebUI Cycle ReAct 时间线 — 设计 spec

## 1. 目标

把 WebUI 的 cycle 详情面板从「原始数据堆叠」改造成 **ReAct 过程的忠实回放**:一个 cycle 内
`唤醒上下文 → 思考 → 调工具(看入参) → 看结果 →〔中途事件注入〕→ 再思考 → …… → 最终决策`
按真实时序交错呈现,对标 CLI session log 每个 cycle 的渲染范本,并在 web 端做得更直观。

核心难点:**ReAct 的交错顺序当前没有持久化**——它只活在 cycle 运行期的内存 message 时间线里,
落库时被拆平成「`reasoning` 一坨 + `tool_calls` 扁平行」。本 spec 补齐这个信号。

> 对标范围说明:"对标 CLI"指**交错叙事结构**对齐,非逐项等价——web v1 在被拒调用(ModelRetry /
> 非法 call)的可解释性上**低于 CLI**(CLI 能显示拒绝原因文本,web v1 不存 RetryPromptPart;见 §10 / §12)。

## 2. 现状与约束(数据可达性结论)

- CLI session log(`format_cycle_output`)能渲交错,是因为它持有运行期 `result.new_messages()`
  ——pydantic-ai 的 ModelResponse(含 ThinkingPart / ToolCallPart)/ ModelRequest(含
  ToolReturnPart)时间线。这条时间线**临时**,不落库。
- DB 落的是派生量:`agent_cycles.reasoning`=全部 thinking 拼接成单列;`tool_calls`=逐调用扁平行
  (无「属于第几轮 response」的关联键);`decision`=最终输出。
- **关键事实**:工具的 `duration_ms` / `status`(尤其 biz_error,是 ContextVar 边路信号)/
  截断后的 `result` / `error_type` 都**不在 messages 里**,是 `ToolCallRecorder` 运行时
  量出来 / 分类出来的。token/usage 来自 `result.usage()`,timing 来自运行器,trigger_context
  / state_snapshot / injected_events 来自 scheduler / cycle_capture。→ 这些列**无法**从 messages
  反推,无论怎么设计都必须独立存在。
- `user_prompt_snapshot`(唤醒上下文原文)**已落库**(agent_cycles 列),但 WebUI API 未暴露。

结论:交错只能在 **cycle 收尾、从 `new_messages()` 重建后落库**,缺的就两样——thinking 按
response 切段、thinking 步骤与工具调用的顺序关联键。

## 3. 方案总览:骨架 + 指针(A)

存一个**叙事骨架**(顺序 + 每步思考 + 该步发起的 tool_call_id 列表),工具遥测仍**只在
`tool_calls` 一份**(保留其 30000 截断 / error_type 脱敏 / 索引 / analytics 可查),WebUI
渲染时按骨架顺序、用 tool_call_id JOIN `tool_calls` 取 args/result。

选 A 而非「自包含 transcript」的理由:遵循「信号唯一权威来源」原则——工具遥测不双存、无漂移面;
后端收尾只需走一遍 messages(无需回读 tool_calls);存储省;analytics 零影响;未来工具字段演进
只动 `tool_calls`,JOIN 自动带出。代价是 WebUI 多一次轻量 map 查表 + 处理「骨架里的 id 在
tool_calls 无对应行」的兜底——两者都小且有现成解法(见 §10)。

> 注:A 的 react_steps thinking 与 `reasoning` 列**双存了 thinking**,看似与上面"不双存"矛盾,实为该
> 原则的**可控例外**——thinking 不可变、字段单一、**无 analytics(SQL)消费**,与工具遥测(有截断 / 脱敏 /
> 聚合查询,双存会漂且查询口径分裂)性质不同,双存风险可控。消除此重复(react_steps 当 thinking 单源)列
> §12 非目标。

## 4. 数据模型变更

两个新列,沿用现有「JSON-as-Text」模式(与 `trigger_context` / `state_snapshot` 一致:Text 列存
`json.dumps`,读侧 `_loads`)。

### 4.1 `agent_cycles.react_steps`(Text, nullable)

一轮一条记录,**按 ModelResponse 顺序**排列的数组,每个元素对应一个 ModelResponse:

```json
[
  { "thinking": "<该 response 的思考全文,可为 null>",
    "tools": [ {"tool_call_id": "call_abc", "tool_name": "get_market_data"},
               {"tool_call_id": "call_def", "tool_name": "get_position"} ] },
  { "thinking": "...", "tools": [ {"tool_call_id": "call_ghi", "tool_name": "open_position"} ] },
  { "thinking": "<末轮思考>", "tools": [] }
]
```

- `thinking`:该 response 的**全部 ThinkingPart content 拼接**(无 → null)。比 session log
  「每 response 只取首个 ThinkingPart」更详细;实测 smoke baseline 每 response 通常仅 1 个,
  多数情况两者等价,>1 时 web 全展示(更忠实)。
- `tools`:该 response 的 ToolCallParts,**保留发起顺序**;每项带 `tool_name`——这是 §10
  orphan 兜底所需(被拒调用无 tool_calls 行时仍能渲染名字),不存裸 id。
- 既无 thinking 又无 tools 的空 response 跳过,不产生空元素。
- 决策**不进** react_steps:`decision` 仍由 `agent_cycles.decision`(= `result.output`)单源持有;
  末轮 response 的 TextPart 即决策,被骨架忽略,只取其 thinking(若有)作末步思考。

### 4.2 `tool_calls.tool_call_id`(String, nullable)

pydantic-ai `ToolCallPart.tool_call_id`,由 `ToolCallRecorder` 写行时顺手带上(它本就持有
`call.tool_call_id`)。这是 react_steps 指针的落点。nullable:历史行为 NULL。

### 4.3 `injected_events[].after_tool_call_id`(JSON 内新字段,无迁移)

`injected_events` 落库态每条为 `{event, after_tool(名), offset_ms}`,新增 `after_tool_call_id`
(= 注入发生时那次 `call.tool_call_id`)。`after_tool` 名保留作 forensic。
注:injector **内存态**另含 `raw`(requeue 句柄),落库经 app.py:742-745 `{k: v ... if k != "raw"}`
**只剥 raw、保留其余**——故新字段加进记录 dict 即**自动落库,无需改序列化**。JSON 列加字段不需迁移。

## 5. 采集与落库

### 5.1 recorder(`src/services/tool_call_recorder.py`)

`ToolCall(...)` insert 增加 `tool_call_id=call.tool_call_id`。其余不变。

### 5.2 injector(`src/services/midcycle_injector.py`)

注入取证记录的 dict 增加 `after_tool_call_id=call.tool_call_id`。其余不变。注入只在工具**成功
返回**时 drain,故**正常路径下**该 id 对应一条已写入的 `tool_calls` 行;唯 recorder 自身 insert
失败被 swallow(tool_call_recorder.py:173)的极罕见情形会无行,兜底见 §10。

### 5.3 cycle 收尾构建(`cli/app.py` happy path)

在已有 `thinking_text = _extract_thinking_text(...)` 附近,新增**纯函数** `build_react_steps(messages)`
(置于 `display.py`,供 `app.py` import——方向与现有 `app→display` import 一致,无循环):
遍历 `result.new_messages()` 的 ModelResponse,产出 §4.1 结构。

落库:`AgentCycle(...)` insert 增加 `react_steps=json.dumps(steps) if steps else None`,**且整个
构建 + 序列化包在 try/except 内**,任何异常 → `react_steps=None` + `logger.warning`,**绝不阻断**
关键的 AgentCycle 写入(fail-isolated,与现有 render 失败降级一致)。

**两条** forensic 写库路径(`messages=None`)均 `react_steps=None`:`usage_limit_exceeded`
(app.py:557)与 `retry_exhausted`(app.py:612——同一 `except Exception` 块/同一 insert,
**同时覆盖**"重试 3 次耗尽"与"其它异常 abort",render 时 `forensic_reason` 分 aborted/usage)。

> 实现说明(防双遍历漂移):`format_cycle_output`(display.py:1459-1476)主循环**已在做**同样的"按序
> 遍历 ModelResponse → 每步取 (ThinkingParts, ToolCallParts)"提取。若 `build_react_steps` 另起一套独立
> 遍历,两者一旦漂移(如 pydantic-ai 升级改 parts 结构),web 回放序会与 CLI session log 交错序不一致
> ——而 §1 核心目标正是对标 CLI。故**抽一个共享遍历提取器**(返回按序的
> `[(per_response_thinking_parts, tool_call_parts), …]`),`format_cycle_output` 渲染与 `build_react_steps`
> 落库**同消费它**,仅在 thinking 聚合上分叉(CLI 取首个、web 取全部拼接)。重构 `format_cycle_output`
> 时须保持其渲染行为逐字不变(iter-session-log-render-fidelity 既有测试守护)。

## 6. 中途事件注入整合

机制(查证 `midcycle_injector.py`):capability 注册序 `[MidCycleEventInjector(), ToolCallRecorder()]`,
pydantic-ai `reversed()` 包裹 → **Injector 最外、Recorder 最内**。故:

- Recorder 先抓 `result` 并闭合计时 → **`tool_calls.result` 是干净工具返回,不含注入块**
  (`duration_ms` 也不含注入耗时)。
- Injector 随后 `return result + block`,注入块只进 **message 时间线的 ToolReturnPart** +
  **`injected_events` 列**(结构化 capture)。

这对 A 有利:工具卡直接展示干净 result,注入事件**单独**渲成时间线元素(与 session log 把注入块
split 出来单独渲一致),无需从 result 里切。

时间线放置:每渲完一个工具卡,若其 `tool_call_id` 命中某条 `injected_events` 记录的
`after_tool_call_id` → 紧随其后插一张「⚡ 触发事件注入」卡(正文取自 `injected_events[].event`
结构化 capture,**不**从 result 抠)。随后的下一 response 思考即 agent 对该事件的反应。批量注入
(一次多事件,共享同一 `after_tool_call_id`)并排多张。

## 7. 迁移

Alembic,`down_revision = 'b43e33764d90'`(`alembic heads` **实查**的当前 head——`b43e33764d90`
接在 `8c48305247c3` 之后;勿凭"最近做的 feature 迁移"想当然认 head,见 memory
`feedback_spec_anchors_worktree_head`):

- **upgrade**:两个 `op.add_column`(`agent_cycles.react_steps` Text null / `tool_calls.tool_call_id`
  String null)。plain ADD COLUMN,SQLite 原生、不触 view,无需 drop view。
- **downgrade**:`agent_cycles` / `tool_calls` 均被 `v_cycle_metrics` 等 view 引用,batch
  `drop_column` 的 temp-table rename 会重解析 view → 须先 `DROP VIEW`(`reversed(ALL_VIEW_NAMES)`)、
  再 batch drop 两列、再用 `ALL_VIEW_SQLS` 重建(沿用 `8c48305247c3` 既有写法)。
- **不回填**历史 cycle:旧行 `react_steps=NULL` / `tool_call_id=NULL` / 注入记录无
  `after_tool_call_id`。WebUI 走 §10 兜底。

## 8. WebUI schema / query

- `schemas.CycleDetail` 增:
  - `react_steps: list | dict | str | None`(`_loads` 解析;放宽形态同 trigger_context,防损坏行整类 500)。
  - `user_prompt_snapshot: str | None`(原文直传,暴露 #1 唤醒上下文)。
  - `execution_status: str`(**`CycleRow` 已有(schemas.py:52)、`CycleDetail` 此前缺**;forensic
    兜底视图据此说明"为何无时间线":usage_limit_exceeded / retry_exhausted)。
- `schemas.ToolCallRow` 增 `tool_call_id: str | None`。
- `injected_events` 已在 `CycleDetail`(schemas.py:73)且 `get_cycle_detail` 已传——**无需改动**。
- `queries.get_cycle_detail`:`react_steps=_loads(c.react_steps)`、
  `user_prompt_snapshot=c.user_prompt_snapshot`、`execution_status=c.execution_status`、
  `ToolCallRow(..., tool_call_id=t.tool_call_id)`。
- 前端 `types.ts` 由 OpenAPI 重生成。

## 9. 前端:ReAct 时间线

`CycleDetailPanel.vue` 重做(时间线主体可抽 `ReactTimeline.vue` 子组件)。自上而下:

1. **顶部遥测条**(紧凑 chips):**沿用现有** tokens(in/out)· cache%(口径已修,commit 6dcc606)
   · wall · model;**新增** llm(`llm_call_ms`,已在 CycleDetail)· execution_status(需 §8 给
   CycleDetail 补字段)。
2. **唤醒上下文(Context)**:可折叠块,展示 raw `user_prompt_snapshot`(原文版,不解析)。
   null(legacy 行)→ 不渲该块。
3. **ReAct 时间线**(主角):竖向,按 `react_steps` 顺序,每步:
   - 〔思考块〕:`thinking`(null 则跳过该块)。
   - 〔工具卡〕逐 `tools` 项:`⚙ tool_name(args 摘要)` 头,可展开看完整 args + 干净 result;
     带 status / 耗时 / 错误徽标(失败红、biz_error 区分)。args/result 由 `tool_call_id` 在
     `detail.tool_calls` 建的 `{id: row}` map 里查。
   - 〔注入卡〕:见 §6,按 `after_tool_call_id` 锚在对应工具卡后。
   - 视觉上呈现 think → act → 看结果 →〔注入〕→ think 的流。
4. **决策(最终输出)**:末尾醒目块,渲 `decision`。v1 渲干净格式化文本(保留换行/结构);
   把 `(1) Stance / (2) …` 拆成带标签字段是后续增强(§12)。

`{tool_call_id: ToolCallRow}` map 由 `detail.tool_calls` 构建一次;react_steps 工具项据此解析。

## 10. 边界情形

- **`react_steps` 为 null**(legacy / 两条 forensic 路径):时间线区回退到**当前的扁平视图**
  (reasoning 整块 + 工具表)+ 一行说明「该 cycle 无交错时间线(历史/取证记录)」。其余区(遥测/
  上下文/决策)正常渲。
- **骨架里的 tool_call_id 在 `tool_calls` 无对应行**:两因——① 被 pydantic-ai 拒绝的调用(ModelRetry
  / 非法 call,recorder 跳过控制流异常不记行);② recorder 自身 insert 失败被 swallow(极罕见,工具其实
  **已成功执行**)。react_steps 单独**无法区分**两者,故用 `tool_name` 渲 + **因因中性**标注
  「无遥测记录(被拒或记录失败)」,不硬断"被拒"。CLI 借 `retry_lookup`(display.py:1456)能区分
  retry-reject 并显示 RetryPromptPart 文本,web v1 不存 RetryPromptPart、可解释性低于 CLI(捕获拒绝
  原因属 §12 非目标)。
- **某 response 有 thinking 无 tools / 有 tools 无 thinking**:正常,对应块缺省项跳过。
- **批量注入**:多条 `injected_events` 共享同一 `after_tool_call_id` → 并排多张注入卡。
- **注入锚点无法解析**:两种情形合流处理——① legacy 行无 `after_tool_call_id`;② `after_tool_call_id`
  在 map 缺失(极罕见:recorder 自身 insert 失败被 swallow,tool_call_recorder.py:173,致该工具无行)。
  均退化为按 `after_tool` 名 best-effort 锚定 / 该步末尾归组,无需新逻辑。
- **result 命中 30000 截断**:`tool_calls.result` 已带 `…[truncated]` 标记,工具卡原样展示。
- **同名工具一轮多调**:`tool_call_id` 唯一,map 查表与注入锚定均精确,不靠名字。

## 11. 测试策略

后端:
- recorder 写入 `tool_call_id`。
- injector 记录含 `after_tool_call_id`。
- `build_react_steps`:多 response 交错顺序;thinking 多 part 拼接;末轮无 tools;某 response 无
  thinking;空 response 跳过;构建异常 → 调用方落 None(cycle 写入不受影响)。
- 共享提取器一致性:同一 messages 下 `build_react_steps` 步骤顺序 == `format_cycle_output` 渲染顺序
  (锁 CLI/web 交错一致、防双遍历漂移);`format_cycle_output` 既有 render fidelity 测试全绿。
- 两条 forensic 路径(usage_limit_exceeded / retry_exhausted) → `react_steps=None`。
- webui query/schema 含 `react_steps` / `user_prompt_snapshot` / `tool_call_id`;`_loads` 放宽形态。
- 迁移 smoke:两列存在且 nullable;downgrade 重建 view 后 view 仍可查。

前端:
- 时间线按 react_steps 顺序渲交错步骤。
- 工具卡按 `tool_call_id` 解析出 args/result。
- 注入卡按 `after_tool_call_id` 锚在正确工具后;批量并排。
- orphan tool_call_id → 渲 tool_name + 因因中性标注「无遥测记录(被拒或记录失败)」。
- `react_steps=null` → 回退扁平视图 + 说明。

## 12. 非目标(out of scope)

- 决策字段化(把 `(1)/(2)…` 拆成带标签行)——v1 渲格式化块。
- Context 精炼版(解析 Woke-by / Carried-thesis)——本轮用原文版。
- 捕获控制流被拒调用的拒绝原因文本。
- 回填历史 cycle 的 react_steps / tool_call_id。
- 让 react_steps 当 thinking 单源、消除与 `reasoning` 一坨的重复——本轮保留 `reasoning`,接受
  thinking 文本重复。
- 运行中 cycle 的实时流式时间线——时间线为 cycle 收尾后的回放。
