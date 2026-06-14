# WebUI 观察台 Phase 1 打磨 — 设计 spec

## 1. 目标

观察台 Phase 1（PR#76/#77/#78 已落）的 6 项体验改进，核心是让决策时间线
**"扫读即定位关键事件"**：大多数 cycle 是"持仓不变"的噪声，少数开/平/加/减仓与
成交才是关键。把这些做成 feed 的主信息 + 视觉锚点；并补齐会话级 system prompt、
cycle 状态快照、推理折叠、数值友好化、标题通俗化。

6 项议题：

| # | 议题 | 处置 | 改动面 |
|---|------|------|--------|
| 1 | system prompt 不可见 | 会话级折叠区展示 | 后端暴露 + 前端 |
| 2 | 推理块全展开占屏 | 超长默认折叠 + 展开 | 纯前端 |
| 3 | feed 行显示决策首行文字 | head/end 双段：开始态持仓 + 本轮关键动作 | 后端派生 + 前端 |
| 4 | cycle 无状态快照 | 详情区新增完整 state_snapshot 折叠区 | 纯前端 |
| 5 | 数值不友好 | 千分位 / ms→s / args 形态 | 纯前端 |
| 6 | "ReAct 过程"是黑话 | 通俗标题 | 纯前端文案 |

## 2. 现状与约束（数据可达性结论）

代码锚点已对照 HEAD 复核；DB 实证标注了数据来源（fresh run #20/#21 vs 历史会话），
避免跨会话/旧实现数据错配（[[feedback_data_mismatch_old_impl_inference]]）：

- **代码锚点（已对照 HEAD 复核）**：feed 行 schema = `CycleRow`（schemas.py:44，**非
  CycleSummary**）；feed 查询 = `get_cycles`（queries.py:28，**非 get_session_cycles**）；
  单会话查询 = `get_session_detail`（queries.py:176，**非 get_session**——`get_session`
  是 `src.storage.database` 的 DB 会话上下文管理器，queries.py:11，勿混）。端点全部带
  `/api` 前缀（src/webui/app.py:25-58）。前端类型同名 `CycleRow`（client.ts:6 / CycleRowHeader.vue /
  stores/sessions.ts）。
- **system prompt**：`Session.system_prompt` 列**已落库**（建会话时渲染、session-fixed，
  models.py:54-55），`/api/sessions/{sid}`（`get_session_detail`）未暴露。
- **state_snapshot**：`/api/cycles/{pk}` detail **已含**（schemas.py:73 + queries.py:81），
  结构 `position / balance / market / pending_orders / active_alerts`（+ 内部键 `_errors` /
  `_cycle_id` 不展示）；但 `CycleDetailPanel.vue` 未渲染它，且 feed 的 `CycleRow` 不含它。
- **snapshot 时序（核心约束）**：`state_snapshot` 在 `agent.run` **之前**拍（src/cli/app.py:514
  早于 556）= **本轮开始态 / 操作前持仓**。被动 fill 撮合/派发早于整个 cycle 体（fill 是唤醒因——
  撮合在 `_process_tick`/`_dispatch_fill_event`、drain 在 scheduler，均先于 cycle 体；src/cli/app.py:508
  仅 capture 已 drain events 的 trigger 镜像，非 drain 本身），故 snapshot 反映 fill **之后**的持仓
  （DB 实证：cycle 1147 stop 全平 short 后 snapshot.position=null）。cycle **结束态未落库**。故 feed 行须用 head/end
  双段表达（§3.1），不能拿开始态当"当前持仓"。
- **fill 开/平区分**：FillEvent 字段含 `pnl` / `is_full_close` / `position_side` /
  `trigger_reason`。`trigger_reason` 全取值（base.py:382）：`market` / `limit` / `stop` /
  `take_profit` / **`liquidation`**（强平，simulated.py:594/603-634，DB 当前样本未出现但
  代码支持——[[feedback_empirical_sampling_design]]：无样本≠不存在）。**开/平看
  `pnl`+`is_full_close`，不是 `trigger_reason`**：`pnl is None` → 开仓型；`pnl≠None` 且
  `is_full_close` → 全平；`pnl≠None` 且非 full_close → 部分平。方向看 `position_side`。
- **market fill = 历史会话的旧派发产物，当前已无**：含 market fill echo 的 conditional
  cycle **仅来自 2026-06-03 及之前的旧会话**（sim #6~#15）——彼时市价成交会派发 fill
  event 唤醒下一轮（全库 echo 中 149/150 有可去重的主动孪生轮）。**当前代码市价单
  `create_order` 同步返回 fill、不派发**（simulated.py 市价路径直接 `return fill` + tool
  层只记 TradeAction；`_process_tick` 派发列表仅 liquidation/stop/take_profit/limit，
  simulated.py:680-682）——故 **fresh run 无 market echo**（实证：#20 有 5 open+1 close 却
  0 echo / #21 0 fill）。WebUI 仍要渲染历史 5 月会话（它们真含 echo），故 §3.3 保留
  "跳过 market"规则：**对历史 cycle 去重正确、对新数据 no-op**。
- **args 存储**：干净，无双重转义；`open_position` args=`{side, position_pct, leverage}`
  （有方向、无绝对量价；量价在 result 文本里，本 iter 不解析）。
- **DB 迁移**：本 iter **零 Alembic 迁移**——所有新字段从已落库列派生暴露。

## 3. 议题 3（核心）：feed 行 head/end 双段 + 关键事件高亮

### 3.1 head / end 双段（解决 snapshot 时序根因）

snapshot 是 cycle 开始态，直接当"持仓"会在主动开/平仓轮误导（开多轮显示空仓）。
feed 行显式分两段：

- **head（开始态）**：本轮 `state_snapshot.position`，UI 标注"开始:"——明确是本轮**起始**持仓。
- **end（本轮决策）**：本轮关键交易动作 `key_events`，UI 标注"本轮:"。

cycle N 的 end（开空）与 cycle N+1 的 head（持空）跨行承接，状态流转自然可读；head
标注"开始"消除"开仓轮显示空仓"的歧义。**end 内容 = 动作 + 方向**（不含绝对量价；
量价进 §4 状态快照详情区）。

### 3.2 后端 schema（`CycleRow`，schemas.py:44）

```python
class PositionBrief(BaseModel):
    side: str            # 'long' | 'short'
    contracts: float
    entry_price: float | None

class KeyEvent(BaseModel):
    # 主动: open|add|close|flip|limit_order  被动 fill: fill_open|fill_close|fill_partial
    kind: str
    label: str           # 开多 / 加仓 / 平多 / 反手 / 挂限价单·多 / 限价开多 / 止损平仓 / 强平 …
    direction: str | None  # 'long'|'short'，用于色条

class CycleRow(BaseModel):
    # ... 现有字段；删 decision_head（见下注）...
    position: PositionBrief | None   # head：state_snapshot.position（开始态）；flat → None
    key_events: list[KeyEvent]       # end：本轮关键动作；无 → [] （空列表，非 None）
```

`key_events` 用 **list**（非单值）——支持同轮多动作（被动 fill 唤醒 + 同轮主动动作）。
实证：**非-market 被动 fill + 同轮主动动作 = 3 轮**（cycle 301/1147/1476，如 1147
`stop fill + open_position`：止损全平 short 后 snapshot 已空仓（§2 时序），open(short) 从空仓
新开 → 派生 `[fill_close 止损平仓, open 开空]` 两事件，单值会丢掉主动动作）。

> **删 `decision_head`**：它是 `_head(c.decision)` = 决策正文**首行**（queries.py:21-25/50，
> 非 stance 字段，只是当前 persona 模板首行恰为 `(1) Stance —`）。按用户拍板"feed 去
> 决策预览、改状态/动作"移除；decision 全文仍在 `/api/cycles/{pk}` 详情「决策」段。

### 3.3 后端派生规则（queries.py `get_cycles`）

每 cycle 派生 `position`（state_snapshot.position 精简）+ `key_events`（列表）。事件两类，
**并集收集**（不再"被动 > 主动"互斥——解决同轮双事件）：

**A. 被动成交 fill**（`trigger_context` 的 fill）。`pnl`+`is_full_close` 定开/平，
`trigger_reason` 定原因，`position_side` 定方向：

| fill 形态 | kind | label |
|-----------|------|-------|
| `pnl is None`，reason=`limit` | fill_open | 限价开多 / 限价开空 |
| `pnl≠None` 且 `is_full_close`，reason=`stop` | fill_close | 止损平仓 |
| `pnl≠None` 且 `is_full_close`，reason=`take_profit` | fill_close | 止盈平仓 |
| `pnl≠None` 且 `is_full_close`，reason=`liquidation` | fill_close | 强平 / 爆仓平仓 |
| `pnl≠None` 且 `is_full_close`，reason=`limit` | fill_close | 限价平仓 |
| `pnl≠None` 且非 `is_full_close` | fill_partial | 部分平仓 |
| reason=`market` | —（**跳过**） | 仅历史会话（≤06-03）有的旧 echo，当前已无（§2）；跳过去重 |

**B. 主动动作**（本轮 `tool_calls`，与 A 并集）：

| 工具 + 操作前持仓 | kind | label |
|-------------------|------|-------|
| `open_position`，前 flat | open | 开多 / 开空（`args.side`）|
| `open_position`，前同向 | add | 加仓 |
| `open_position`，前反向 | flip | 反手（→ 新方向）|
| `close_position` | close | 平多 / 平空（前 side）|
| `place_limit_order`（不即时改持仓）| limit_order | 挂限价单·多 / 空（`args.side`）|

**排序**：list 内按发生序——被动 fill（唤醒因）在前、主动动作在后。空 list → end 段
"（无交易）"。多主动工具同轮（如 open + place_limit）按上表行序：交易动作（open/close/
flip/add）> 挂单（limit_order）。

派生 **fail-isolated**：单事件解析异常 → 跳过该事件，绝不阻断 feed（沿用 #78 `_safe_*`）。
`trigger_context` 派生前**形态归一**（webui 已放宽为 `dict|list|str|None`，schemas.py:72）：
list[dict] 直用、dict 包成单元素 list、str/None → 空。实现优先一次性批量 join
`tool_calls`（feed limit≤200）。

### 3.4 前端 feed 行（CycleRowHeader.vue）

现用 `cycle.decision_head`（CycleRowHeader.vue:13），改 head/end 双段（保留时间 +
triggered_by + status + token·ms）：

- **head**：`开始: 空仓` / `开始: 空 17.99张 @63896`（position）。
- **end**：`本轮:` + 每个 `key_event` 一枚 chip（`🟢开空` / `🔴平仓·止损` / `🔵挂限价单·多`），
  按 direction 着色（开=绿 / 平=红 / 挂单·反手=蓝/黄）；空 list → `本轮: （无交易）`。
- `key_events` 非空 → 整行**左色条**高亮（关键事件锚点）；噪声轮无色条弱化。

## 4. 议题 4：cycle 状态快照详情区（纯前端）

`CycleDetailPanel.vue` 新增折叠 section「状态快照」（默认折叠），渲染
`detail.state_snapshot` 完整内容：持仓（side/contracts/entry_price/unrealized_pnl 若有）/
余额（total/free/used）/ market（ticker_last + fetched_at）/ 挂单列表 / 活跃告警列表
（id/direction/price/reasoning）。用 NDescriptions + 小表格。数据已在 detail payload，
**零后端改动**。`_errors` / `_cycle_id` 内部键不展示。

## 5. 议题 1：会话级 system prompt（后端暴露 + 前端）

- 后端：`SessionDetail`（`/api/sessions/{sid}`，`get_session_detail`）schema 加
  `system_prompt: str | None`；queries 读 `Session.system_prompt`。
- 前端：会话详情头部（SessionMeta.vue 附近）加折叠区「System Prompt（persona，会话固定）」，
  **默认折叠**（内容长）。会话级、不随 cycle 变 —— 与 cycle 级「唤醒上下文」区分清楚。

## 6. 议题 2：推理过程折叠（纯前端）

`ReactTimeline.vue` 🧠 thinking 块：字符数 **> 阈值（默认 600）** 默认折叠，显示前 ~6 行
+「展开全文 ▾」；≤ 阈值全显示。逐块独立折叠态。阈值常量集中定义。

## 7. 议题 5：数值与展示友好化（纯前端）

新增 `format` util：
- token：千分位 `80733` → `80,733`（`toLocaleString`）。
- 耗时：`≥1000ms` → `49770ms` 显示 `49.8s`（1 位小数）；`<1000ms` 显示 `ms`；`0ms` → `<1ms`。
  覆盖 chips（wall/llm）+ ReactTimeline 工具卡 duration。
- args 形态：ReactTimeline 工具卡入参用紧凑 `key=value` 单行（`timeframe=1h, candle_count=30`），
  cycle 详情/扁平表仍用 JsonBlock。无参工具显示 `（无参）`。嵌套值（dict/list）回退 JSON 串。

## 8. 议题 6：标题通俗化（纯前端文案）

`CycleDetailPanel.vue:64`「ReAct 过程」→「**推理与行动过程**」。

## 9. 后端 schema / query 变更汇总

- `schemas.py`：`CycleRow` 删 `decision_head` + 加 `position` / `key_events`（+ `PositionBrief`
  / `KeyEvent`）；`SessionDetail` 加 `system_prompt`。
- `queries.py`：`get_cycles` 派生 `position` + `key_events`（join tool_calls + 解析
  state_snapshot/trigger_context，fail-isolated）；`get_session_detail` 加 `system_prompt`；
  **清理 dead code** `_head` + `_DECISION_HEAD_CHARS`（删 decision_head 后无引用）。
- **无 Alembic 迁移**。
- 类型重新生成：`openapi.json`（minified + trailing newline，沿用约定）→ `gen:types`。

## 10. 测试策略

- 后端（pytest，`tests/test_webui_queries.py` / `test_webui_api.py`）：`get_cycles` 的
  `key_events` 派生**逐分支**——主动（开多/开空/加仓/反手/主动平仓/挂限价单/噪声轮 `[]`）+
  被动 fill（限价开仓 fill_open / 止损 / 止盈 / **强平 liquidation** / 限价平 / 部分平
  fill_partial，逐一验 `pnl`+`is_full_close`）+ **market 回声去重**（market fill → 不计入）+
  **同轮双事件**（止损 fill + 主动反手 → list 含 2 项）+ `position` 摘要 + 派生异常 → 跳过
  不抛 + `get_session_detail.system_prompt` 暴露。fixture 造 tool_calls + state_snapshot +
  trigger_context（各 reason × is_full_close × pnl 组合）。
- **既有测试同步**：`test_get_cycles_orders_desc_and_paginates`（test_webui_queries.py:52）
  断言 `rows[0].decision_head`，删字段会破，须改为断言 `position` / `key_events`。前端两处
  `decision_head` 消费者同步：`DecisionStream.spec.ts`（cyc 工厂 + head3/head1 断言——删后
  mount CycleRowHeader 会因 undefined `key_events` 崩）+ `store.spec.ts`（cyc 工厂残留字段）。
- 前端（vitest）：feed 行 head/end 双段渲染 + 有/无 key_events 的色条与 chip + 同轮多 chip +
  状态快照详情区各子块 + system prompt 折叠 + 推理超长折叠/短不折叠 + 数值格式化。
- 全量 gate：`pytest -q` 全跑（#78 教训：per-file 漏 drift）+ `vue-tsc --noEmit` + `npm run build`。

## 11. 非目标（out of scope）

- 不改 agent loop / 不落新 DB 列 / 不跑迁移。
- 不做 cycle "结束态精确持仓"（需存结束 snapshot 或 join 下一 cycle；head/end 双段已用
  end=本轮动作弥补，§3.1）。
- end 段不显示绝对成交量价（在 result 文本里、解析脆弱；量价进状态快照详情区）。
- 不 join `TradeAction`（开/平及原因已可从 trigger_context.fill + tool_calls 派生）。
- 主动部分平/减仓不细分（`close_position` 全平语义；反向小量 `open_position` 归 flip）。
  被动部分成交由 fill `is_full_close=False` 走 `fill_partial`，已覆盖。
- `cancel_order`（撤挂单）本轮不单独标 key_event（pending 计数变化在状态快照详情区可见）；
  若 review 认为撤单也是关键决策，再补一类。
- `set_stop_loss` / `set_take_profit` / `adjust_leverage` 同样不标 key_event：前两者是保护性
  algo 挂单、不即时改持仓，其效果在触发时以被动 `fill_close`（止损/止盈平仓）体现；`adjust_leverage`
  仅改杠杆、不改持仓方向。三者的状态（algo 挂单 / 杠杆）均在状态快照详情区（§4）可见。key_events
  聚焦「持仓方向/规模变化」（开/平/加/反手 + 建仓性挂单），故与 cancel_order 同类排除。
- **同轮多个主动动作共享 cycle 起始 prev_side**（取自 state_snapshot.position）：主动平仓后
  同轮再开反向仓，open 仍按起始持仓算 → 标 `flip` 而非 `open`。被动 fill 后的主动开仓**不**受影响
  ——snapshot 已反映 fill 后状态（全平→空仓时 open 正确标 `open`，§2 时序已用 cycle 1147 验证）。
  实证全库仅 1 例命中此边缘（约 1/1770 cycle，alert 唤醒 close→open），后果是 label 冗余非错误，不重算。
- **`fill_open` 一律标「限价开」**：当前撮合只 limit/market 能开仓、market 已跳过去重（§3.3），
  故 `pnl is None` 的非-market fill 必为 limit 开仓，label 准确。该口径隐含「limit 开仓」假设；
  实盘 OKX 若现其他开仓路径需复核（实盘 backlog，sim-only 不触发）。
- 不做实时推送（仍 5s 轮询）。
