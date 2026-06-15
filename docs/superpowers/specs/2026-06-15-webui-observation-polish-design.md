# WebUI 观察台打磨设计（cycle 模块 / 状态面板 / header / 注入卡 / 全局 UTC）

## 背景与动机

sim #21 运行期实际使用观察台后，归纳出 11 项 UI 打磨 + 1 项全局时区统一 + 1 项详情区结构调整。全部聚焦**观测可读性与语义准确性**，不改 agent 决策路径——唯一触及后端的是 B3（快照多采一个已存在的字段）与 D/C1（webui 序列化层派生展示字段），均不影响撮合 / 决策 / 持久化主链。

## Scope

**In scope**
- 前端：`ReactTimeline.vue`（思考块、注入卡）、`CycleRowHeader.vue`（header）、`CycleDetailPanel.vue`（状态面板、唤醒上下文、chips、结构重排）、`LiveStatusCard.vue` / `TradesTable.vue`（时间）、`utils/time.ts`（UTC 格式器）、会话头 UTC 标注。
- 后端：`cycle_capture.py`（B3 快照采集波动告警单例）、`webui/queries.py` + `webui/schemas.py`（C1 会话内序号 `seq`、D 注入事件 `triggered_ago` 富化）、复用 `event_render._format_event_age`。

**Out of scope**
- agent 决策 / 工具 / persona / prompt。
- 撮合（simulated.py）、view（`v_alert_lifecycle` 等）、analytics 脚本。
- `v_alert_lifecycle` full-close over-count 修法（独立 deferred 议题，见 memory）。
- 既有落盘数据的追溯修正——B3 仅对**未来 cycle** 生效（见「风险与注意」）。

## 决策清单

| # | 决策 | 层 |
|---|------|----|
| A1 | 思考块统一整块折叠（点标题行 ▾/▸，折叠态单行预览，展开态全文滚动框，取消 600 字两段式），**默认折叠** | 前端 |
| A2 | 图标 🧠 → 💭 + 补「思考」文字标签 | 前端 |
| A3 | 唤醒上下文默认折叠 | 前端 |
| B1 | 余额三标签格（总额 / 可用 / 占用，标签+值同行，段间留白，USDT 收行尾） | 前端 |
| B2 | 现价时间格式化（并入全局 UTC） | 前端 |
| B3 | 后端快照加采集波动告警 + 前端「价格 / 波动」两类展示 | 前后端 |
| C1 | cycle header 加**会话内序号** `#N` | 前后端 |
| C2 | header 时间 = 开始 → 结束 区间（**created_at 是结束时刻**；开始 = created_at − wall_time_ms） | 前端 |
| C3 | token / 耗时只留 header；详情 chips 去掉重复的 `tokens` / `wall`，保留拆解（输入/输出·cache·llm·status·model） | 前端 |
| D | 注入卡：人读摘要 + 原始 JSON 折叠（保留）+ `触发于 {HH:MM:SS UTC}` + age `{X ago}`（英文，复用后端 ladder）；标题用后端下发 `kind_label`（复用 `_classify_fill` 词汇）；**去掉** `开始后 +Xs` offset | 前后端 |
| 全局 | 看板时间统一 **UTC**（`fmtLocal` → UTC 格式器，4 处调用 + 测试）；会话头一次性标注「时间均为 UTC」 | 前端 |
| 结构 | 详情区顺序：chips → **唤醒时状态** → 唤醒上下文 → 推理时间线 → 决策；快照标题「本轮开始时的状态」→「唤醒时状态」 | 前端 |

代码锚点均在 `iter-webui-observation-polish` HEAD 复核。

---

## 详细设计

### 1. 全局时区 → UTC

**动机**：看板存在跨屏割裂风险（cycle header 本地、现价裸 ISO `+00:00`），且观测/取证口径是 UTC（DB 存 UTC，sim 分析按 UTC）。统一 UTC 与 DB 对齐、零心算对账。

**`frontend/src/utils/time.ts`**
- 新增 `fmtUtc(iso)` → `YYYY-MM-DD HH:MM:SS`（UTC，去微秒、去 `+00:00`）。
- 新增 `fmtUtcTime(iso)` → `HH:MM:SS`（UTC，给区间结束 / 紧凑场景）。
- 新增 `fmtUtcEpoch(ms)` → `HH:MM:SS`（UTC，给注入事件 `event.timestamp` 这类 epoch-ms 源）。
- 实现用 `Date` 的 `getUTCFullYear/getUTCMonth/...` 拼装，**不依赖** `toLocaleString`（locale 会引入本地时区）。
- `fmtLocal` 删除（替换全部 3 个调用点后无消费者）；文件头注释「本地展示用 toLocaleString」同步改为 UTC 口径（防过时）。

**调用点替换**
- `CycleRowHeader.vue:30` `fmtLocal(created_at)` → 见 §2（区间）。
- `LiveStatusCard.vue:16` `fmtLocal(last_active_at)` → `fmtUtc(...)`。
- `TradesTable.vue:11` `fmtLocal(r.at)` → `fmtUtc(...)`。
- `CycleDetailPanel.vue:91` 现价裸 `{{ snapshot.market.fetched_at }}` → `fmtUtc(snapshot.market.fetched_at)`（B2）。

**全局标注**：`DashboardView.vue` 的 `.session-header.ob-card` 内加一行 muted 小字「时间均为 UTC」（去歧义、不逐戳加噪）。

### 2. cycle header（C1 / C2 / C3）

**`src/webui/schemas.py`**：`CycleRow` + `CycleDetail` 各加 `seq: int`（会话内 1-based 序号）。

**`src/webui/queries.py`**
- `get_cycles`：`seq` 须在 **before_id/after_id 游标过滤之前**对全量 session 子集开窗——子查询 `func.row_number().over(order_by=AgentCycle.id.asc())`（partition 隐含于 session 过滤），外层再套游标 + 方向排序 + limit；否则 `after_id` 翻页会从游标处重启序号。desc 列表与翻页用同一绝对 `seq`。⚠ impl 注意：外层 `after_id` 分支须保留现有 `order_by(id.asc()).limit().reverse()`（queries.py:28-33 注释「avoid 静默跳过紧邻批」），仅在其外包一层带 `seq` 的子查询，不改其游标语义。
- `get_cycle_detail`：`seq = SELECT COUNT(*) FROM agent_cycles WHERE session_id=:sid AND id <= :id`（单标量子查询）。

**`frontend/src/components/CycleRowHeader.vue`**
- 行首加序号片：`#{{ cycle.seq }}`（样式见可视化稿，`.seq` 灰底圆角）。
- 时间改区间：`{{ fmtUtc(startAt) }} → {{ fmtUtcTime(cycle.created_at) }}`，其中 **`created_at` 是 cycle 结束时刻**（见「数据可用性核实」），`startAt` = `created_at − wall_time_ms`（computed，≈ cycle_started_at）。`wall_time_ms == null`（forensic cycle）无法推开始 → 只渲 `fmtUtc(created_at)`（结束/落库时刻）单点、不显示 `→`。
- 末尾遥测段保留 `{{ fmtTokens }} tok · {{ fmtDuration(wall_time_ms) }}`（token + 耗时只此一处）。

**`frontend/src/components/CycleDetailPanel.vue`（C3 chips 去重）**
- 删除 `:55` `tokens {{ fmtTokens(detail.tokens_consumed) }}` 片。
- 删除 `:58` `wall {{ fmtDuration(detail.wall_time_ms) }}` 片。
- 保留：`:56` 输入/输出、`:57` cache、`:59` llm、`:60` status、`:61` model。

### 3. 思考块（A1 / A2）—— `frontend/src/components/ReactTimeline.vue`

**A2 图标 + 标签**：`:107` `🧠` → `💭`，并在其后加 `<span class="tk-lbl">思考</span>`。

**A1 统一整块折叠**（替换现 `:62-76` / `:106-114` 的 600 字两段式）
- 折叠态键改为 step 级（沿用 `openThinking: Set<number>`，但语义从"展开全文"变为"整块展开"）。
- 默认折叠：`openThinking` 初始空集即默认折叠（无需额外状态）。
- **短 thinking 免折叠豁免**：当 thinking 单行即可完整显示（无截断，即不长于预览行容量）时，不渲染折叠 affordance（无 `▸`/`▾`），直接 `💭 思考 {全文}` 常显——折叠态预览=全文时点开无新信息，省一次无谓交互。判定 `needsFold(text) = text.includes("\n") || text.length > THINKING_INLINE_MAX`（`THINKING_INLINE_MAX` 取单行容量小值 ~100，**非**旧的 600）——有换行 **或** 超单行容量即给折叠 affordance 并默认折叠，只有真·单行短句才豁免常显；豁免不破坏"默认折叠"语义（长/多行 thinking 仍默认折叠）。
- 折叠态渲染（需折叠时）：标题行 `💭 思考 ▸` + 单行预览（`text-overflow: ellipsis; white-space: nowrap`，取首行）。
- 展开态渲染：标题行 `💭 思考 ▾` + 正文 `pre`（`max-height` + `overflow:auto` 滚动框，超长靠滚动而非截断）。
- 删除常量 `THINKING_HEAD_CHARS` / `THINKING_FOLD_CHARS`（=600，旧"超此截断"阈值，新语义下不再适用）与 `thinkingShown`；新增 `THINKING_INLINE_MAX`（~100，单行容量）作 `needsFold` 阈值；`toggleThinking` 保留（语义改为整块开合）。

### 4. 状态面板（B1 / B2 / B3）+ 重命名 —— `frontend/src/components/CycleDetailPanel.vue`

**重命名**：`:73` 标题「本轮开始时的状态」→「唤醒时状态」。
- 依据：快照在 `cli/app.py:498`（`cycle_started_at = now()`）后 `:514` 立即 capture、早于 agent 运行 → 即唤醒瞬间状态，命名准确且与 wake 词汇一致。注意与 `created_at`（= cycle 结束时刻，见数据核实）相区别——快照的 `market.fetched_at` 才是开始时刻。

**B1 余额三标签格**（`:85-88`）：value 单元改为三段 `seg`（标签+值同行），段间 `gap` 留白，`USDT` 收行尾。
- `总额 {total_usdt}` `可用 {free_usdt}` `占用 {used_usdt}` + 行尾 `USDT`；数值走 `fmtNum`。

**B2 现价时间**（`:91`）：见 §1，裸 `fetched_at` → `fmtUtc(...)`。

**B3 波动告警**
- 后端 `src/services/cycle_capture.py`：在 active alerts（`:199-215`）后加第 6 段，采集波动告警单例：
  ```python
  # 6. volatility alert (singleton) — get_alert_params 返回 (threshold_pct, window_minutes)|None
  try:
      vol = deps.exchange.get_alert_params()
      # 形状守卫：仅 2 元 tuple/list 才解构；非契约返回 fail-safe 成 None，守住本函数
      # 「永不返非 json-serializable 值」契约（裸 truthy 判 `if vol` 会让非 tuple 返回
      # —— 如测试裸 MagicMock —— 解构出非序列化值污染 snapshot 致下游 json.dumps 500，勿改回）。
      snapshot["volatility_alert"] = (
          {"threshold_pct": vol[0], "window_minutes": vol[1]}
          if isinstance(vol, (tuple, list)) and len(vol) == 2 else None
      )
  except Exception as e:
      msg = f"volatility_alert_read_failed: {type(e).__name__}"
      snapshot["_errors"].append(msg)
      logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)
  ```
  `snapshot` 初始化（`:120` 附近）补 `"volatility_alert": None` 默认键。
- 前端「告警」段（`:97-100`）拆两类：
  - 价格：现状 `active_alerts` 渲染（`↑@.. ↓@..`），冠以 muted「价格」标签。
  - 波动：`snapshot.volatility_alert` 非空时渲 `±{threshold_pct}% / {window_minutes}min`，冠以 muted「波动」标签；为空则该子段不渲。

**默认展开**：`snapshotOpen` 维持 `ref(true)`（结构化状态作 at-a-glance 主体）。

### 5. 唤醒上下文（A3）—— `frontend/src/components/CycleDetailPanel.vue`

`:17` `contextOpen = ref(true)` → `ref(false)`（默认折叠，一键展开）。

**折叠默认层级**（最终态）：唤醒时状态**展开** / 唤醒上下文**折叠** / 思考块**折叠** —— 结构化状态可见，原始 prompt 与推理按需展开。

### 6. 注入卡（D）—— `frontend/src/components/ReactTimeline.vue` + 后端富化

**后端 `src/webui/queries.py`（`get_cycle_detail`，`:178`）**
- `injected_events` 不再裸 `_loads`，而是富化：当形态为 `list[{event, offset_ms, ...}]` 时，逐条（循环变量 `rec`，避免与内层 `rec["event"]` 同名混淆）计算并附字段。**逐条包 try/except：任一事件富化异常 → 该条降级为裸 `event`（不附富化字段）；get_cycle_detail 无外层 try/except，绝不让异常冒泡成 500。**
  - `triggered_ago: str | None` = `event_render._format_event_age(injection_moment, event_ts)`
    - **`injection_moment` 必须是 aware UTC**：`base = c.created_at if c.wall_time_ms is None else c.created_at − timedelta(ms=c.wall_time_ms)`；`injection_moment = base.replace(tzinfo=timezone.utc) + timedelta(ms=rec["offset_ms"] or 0)`。**⚠ P0：`c.created_at` 经 ORM 从 SQLite 读回是 naive（`_ensure_utc` docstring 所述、已实证 `tzinfo is None`）；不补 tz 则 `_format_event_age` 首行 `then > now`（aware `event_ts` vs naive `now`）抛 `TypeError: can't compare offset-naive and offset-aware`（已实证复现）→ 500，且只在有注入事件的 cycle（D 全部目标场景）触发。补 tz 与 `_ensure_utc` 同模式。** `created_at` 是结束时刻（数据核实），`created_at − wall_time_ms ≈ cycle_started_at`；`offset_ms` 自 cycle 开始计（`src/services/midcycle_injector.py:95-99`）；`wall_time_ms` 由 CycleDetail 现成下发（schemas.py:96），null 时退回 `created_at`（forensic，age 仅近似）。
    - `event_ts = datetime.fromtimestamp(rec["event"]["timestamp"]/1000, tz=timezone.utc)`（aware）。
    - None-guard：`rec["event"] is None`（`_capture_trigger_context` best-effort 可返 None，落库 `"event": null`）→ `triggered_ago=None`；缺 `timestamp` → None；未来时点（skew）→ None（`_format_event_age` 既有语义）。
  - `kind_label: str` —— **复用既有分类器作单一权威来源、消除前端 fill 词汇漂移（P1 / 单源原则）**：`fill` → `kl = _classify_fill(rec["event"]); kind_label = kl.label if kl else "成交"`（**禁直接 `.label`**：`_classify_fill` 对 `trigger_reason=="market"` 回 None，直接 `.label` 会 AttributeError 被 per-event try/except 兜成整条降级、连 `triggered_ago` 一起丢；注入 fill 一般非 market，None 回退泛标题「成交」。label 与 feed chip 同词：限价开多 / 止损平仓 / 止盈平仓 / 强平 / 限价平仓 / 部分平仓）；`percentage_alert` → `"波动告警触发"`；`price_level_alert` → `"价格告警触发"`（后两者静态标题在此一处定义）。
  - 透传原 `event` / `offset_ms` / `after_tool*` 不变（`offset_ms` 仍下发，仅前端不再显示）。
- `webui/schemas.py`：注入事件无独立 model（`injected_events: dict|list|str|None` 宽形），富化在序列化函数内完成；保持宽形。

**前端 `ReactTimeline.vue` 注入卡（`:140-145` / `:150-156`）**
- 头部：`⚡ {rec.kind_label}`（标题用**后端下发的 `kind_label`**，前端不再自行按 type/trigger_reason 映射 → fill 词汇与 feed chip 单一权威来源）+ 右侧 age 片 `{triggered_ago}`（英文，如 `1 min ago`；`triggered_ago == null` 时不渲 age 片）。
- 摘要行（**第二套更轻的数值渲染器**：仅做展示格式化、**不做事件分类**——开/平及类型已由后端 `kind_label` 决定；与后端 `event_render._render_event_block` 是不同 surface：英文 prompt vs 中文轻 UI + 保留 raw JSON 逃生口，故重渲可接受）：
  - 波动：`{symbol 基名，如 BTC} {window_minutes}min 窗口 {change_pct:+.2f}% · {reference_price} → {current_price}`
  - 成交：侧向标签 `{position_side→多/空}` + `{amount} 张 @{fill_price}` + （`pnl != null` 时）` · 盈亏 {fmtSigned(pnl) 红绿}` + ` · 手续费 {fmtNum(fee)} USDT`。`pnl` 缺省即不渲盈亏（仅 field-presence 判定，非分类，避免"盈亏 None"）；开/平不在摘要重复（已在 `kind_label`）。
  - 价格告警：`{上破/下破 by direction} @{target_price}（现价 {current_price}）` + 次行 muted italic `{reasoning}`
- meta 行（muted）：`触发于 {fmtUtcEpoch(event.timestamp)}`（时分秒 UTC；日期同 cycle 已在 header）。
- 折叠：`原始 JSON ▸`（点击展开 `JsonBlock`，**保留**——取证逃生口：`order_id` / `alert_id` / 精确 epoch / 全精度 `reference_price`）。
- **去掉** `+{offset_ms}ms` 显示（数据仍在 payload，仅不渲染）。
- orphan 注入组（`:150-156`，未能锚定）同样改用 `kind_label` + 摘要 + age + 折叠原文。
- 前端 `InjectedEvent` 接口（ReactTimeline.vue:10-15）加 `triggered_ago?: string | null` 与 `kind_label?: string`。
- **flat-fallback 路径**（`CycleDetailPanel.vue:121-124`，`react_steps=null` 的 legacy/forensic 分支）**保留裸 `JsonBlock` 渲原文**——该路径无骨架锚点、属取证回退，不做摘要化（明示，不静默遗漏）。

### 7. 详情区结构重排 —— `frontend/src/components/CycleDetailPanel.vue`

template section 顺序由「chips → 唤醒上下文(`:65`) → 状态快照(`:71`)」改为：

```
chips(:54) → 唤醒时状态(:71 块上移) → 唤醒上下文(:65 块下移) → 推理时间线(:105) → 决策(:130)
```

---

## 后端改动汇总

| 文件 | 改动 | 关联 |
|------|------|------|
| `src/services/cycle_capture.py` | 快照加 `volatility_alert` 采集 + 默认键 | B3 |
| `src/webui/schemas.py` | `CycleRow` / `CycleDetail` 加 `seq: int` | C1 |
| `src/webui/queries.py` | `get_cycles`/`get_cycle_detail` 计 `seq`；`get_cycle_detail` 富化 `injected_events`（`triggered_ago` + `kind_label`，逐条 try/except 降级、`injection_moment` 补 aware UTC）；`kind_label` 复用 `_classify_fill` | C1 / D / P0 / P1 |
| `src/services/event_render.py` | 仅**复用** `_format_event_age`（不改） | D |
| `frontend/openapi.json` + `npm run gen:types` | `CycleRow`/`CycleDetail` 加 `seq` 后**重导 OpenAPI + 再生 `src/api/types.ts`**（否则前端 `cycle.seq` TS 类型不存在、编译不过）；`triggered_ago` 走 `injected_events` 宽形 blob 不受影响 | C1 |

## 数据可用性核实（支撑设计、无占位）

- **`AgentCycle.created_at` 是 cycle 结束时刻（非开始）**：三个 `AgentCycle(...)`（app.py:571/627/733）均不传 `created_at` → `default=_utcnow`（models.py:44）在 flush/commit 时求值，而构造在 `agent.run` 跑完之后（`app.py:730`「Record to database」）。DB 实测 8 行（取证 SQL：`SELECT created_at, wall_time_ms, state_snapshot FROM agent_cycles ORDER BY id DESC LIMIT 8`，比对 `created_at` 与快照 `market.fetched_at`）`created_at − market.fetched_at = wall_time_ms` **逐行精确成立**（差 ≤1ms）。故 cycle 开始 ≈ `created_at − wall_time_ms`；快照内 `market.fetched_at` = 真实开始时刻。C2 区间与 D 的 `triggered_ago` 基准均据此修正。⚠ 归因精度：该 ≤1ms = (commit−construct DB 写延迟) + (cycle_started_at→ticker fetch 前导)，sim 内存撮合下二者均亚毫秒故重合；live（网络 fetch 数百 ms）下 `created_at − wall ≈ cycle_started_at` 而 `fetched_at = cycle_started_at + fetch 延迟`，二者会分离——当前 sim-only 不影响。
- **ORM 从 SQLite 读回 `datetime` 为 naive（无 tz）**：`schemas._ensure_utc` docstring 明载、实证 `c.created_at.tzinfo is None`。故 D 富化把 DB 派生时刻当 `now` 传 `_format_event_age` 前必须补 aware UTC（§6 P0），否则 `then > now` 抛 `TypeError`。
- `CycleRow.id` 是全局自增 int PK，`cycle_label` 是字符串 cycle_id；**无会话内序号** → `seq` 后端派生（schemas.py:59-68 / 81-101）。
- `CycleRow` 含 `created_at`（= 结束）+ `wall_time_ms` → C2 开始时间前端可算（`created_at − wall_time_ms`，schemas.py:63-65）。
- `get_alert_params()` 返回 `(threshold_pct, window_minutes) | None` 的单例（base.py:254-258）；sim 单 symbol → 映射当前 cycle symbol。
- `injected_events` 当前裸 `_loads`（queries.py:178）；事件含 `type` / `timestamp`(epoch ms) / `offset_ms`（实测样本：`percentage_alert` / `fill` / `price_level_alert` 三类）。
- `_format_event_age(now, then)`（event_render.py:46-58）：future→None、<2s→"just now"、否则秒/分/时+分/天 ladder（跨小时保留分钟）。
- B3 adoption 实证：`set_price_volatility_alert` 全库 31 次、sim #21 内 2 次——真实在用，非 YAGNI。

## 测试策略

**前端（vitest）**
- `time.spec`（**改写**，文件已存在且含 fmtLocal 用例 14-20 行）：删 fmtLocal 用例，加 `fmtUtc` / `fmtUtcTime` / `fmtUtcEpoch` 对已知 UTC instant 的输出断言；不随本地时区漂移。
- `CycleRowHeader.spec`：序号 `#N` 渲染；区间 `(created_at − wall) → created_at`；`wall_time_ms=null` 退化为仅 `created_at` 单点；UTC 断言。
- `LiveStatusCard.spec` / `TradesTable.spec`：UTC 输出断言更新（原本地断言失效，需改）。
- `CycleDetailPanel.spec`：chips 不含 `tokens`/`wall` 重复片、仍含 输入/输出·cache·llm；标题为「唤醒时状态」；section 顺序（唤醒时状态先于唤醒上下文）；`contextOpen` 默认折叠；余额三段；波动告警渲染；现价 UTC。
- `ReactTimeline.spec`：思考块默认折叠 + 单行预览 + 整块展开；💭+「思考」标签；注入卡摘要（三类映射）+ age 片 + `触发于 UTC` + 原始 JSON 折叠存在 + 不含 `+..ms` offset。

**后端（pytest）**
- `cycle_capture`：设波动告警后快照 `volatility_alert` 非空（threshold/window）；未设 → None；getter 异常 → `_errors` 记录且不抛。
- `webui/queries`：`get_cycle_detail` 注入事件富化——**测试须经真实 SQLite 往返**（插入 cycle → 读回得 naive `created_at` → 再富化），**严禁手构造 aware `created_at` fixture**，否则 P0 的 tz-naive→500 假绿（cf. fresh-DB 假绿陷阱 memory `feedback_views_need_rebuild_migration`）。断言：`triggered_ago` ladder（固定 `created_at − wall + offset` vs `event.timestamp`，**基准为 created_at − wall 而非 created_at**）；`kind_label` = `_classify_fill` 词汇（如 fill stop full-close → 「止损平仓」）；`rec["event"]` 为 None / 缺 timestamp / future-ts → `triggered_ago=None`；某条富化抛错 → 该条降级裸 event、其余正常、整体不 500。`seq` 在 `get_cycles`（含 `after_id`/`before_id` 翻页）与 `get_cycle_detail` 一致、为会话内绝对序号且翻页不重启。

## 风险与注意

- **🔴 注入富化 tz-naive 陷阱（P0，已实证复现）**：`injection_moment` 由 naive `created_at`（SQLite 读回）派生，传 `_format_event_age` 前必须 `.replace(tzinfo=timezone.utc)`，否则**有注入事件的 cycle（D 全部目标场景）detail 接口 500**。后端测试必须走真实 SQLite 往返取 naive `created_at`，手构造 aware fixture 会假绿。
- **B3 仅未来 cycle 生效**：sim #21 已落盘快照无 `volatility_alert` 键，前端须容忍缺键（`undefined` → 不渲波动子段）。回看历史 cycle 不会有波动告警，属预期。
- **UTC 改动牵动测试**：`time.spec`（删 fmtLocal 用例）、`LiveStatusCard` / `TradesTable` / 既有 header 测试中以本地格式书写的断言会失效，需同批改为 UTC。
- **运行中的 webui 进程不热更**：sim #21 的 `python -m src.webui` 进程与 dist 需重启 + rebuild 才反映改动；不影响 main.py 采集进程与数据有效性。
- **`seq` 与游标翻页**：desc 列表与 `after_id`/`before_id` 翻页都须显示绝对会话内序号；窗口函数须在**游标过滤之前**对全量 session 子集开窗（子查询），否则翻页序号从游标重启。
- **age 英文文案**：`triggered_ago` 直接下发 `_format_event_age` 的英文串（`1 min ago` 等），单一权威来源，不在 TS 重写 ladder；中文化为后续可选项（需 zh 格式器，暂不做）。

## 落地顺序建议

1. 后端：`schemas.py` + `queries.py`（seq、injected_events 富化）+ `cycle_capture.py`（B3）+ 后端测试 → **重导 `frontend/openapi.json` + `npm run gen:types`**（seq 进 TS 类型，前端方可引用 `cycle.seq`）。
2. 前端 util：`time.ts`（fmtUtc 系列，删 fmtLocal）+ 全部调用点替换 + time 测试。
3. 前端组件：header（C1/C2/C3）→ 状态面板（B1/B2/B3、重命名、重排、A3）→ 思考块（A1/A2）→ 注入卡（D）+ 各组件测试。
4. dist rebuild + Playwright 三路径走查（交错时间线 / 折叠默认态 / 注入卡摘要）。
