# 工具调用结果持久化设计（WebUI 观察台 result seam 点亮）

## 目标

给 `tool_calls` 表增加 `result` 列、在执行层捕获每次工具调用的返回值，让 WebUI 观察台能展示「agent 每次调用工具看到了什么」。这是当前最大的可观测性硬伤：`tool_calls` 表记录了工具名 / 入参 / 状态 / 耗时，唯独没有返回值，使得查看者无法判断 agent 基于什么事实做的决策。

Phase 1b 前端已预埋契约 seam（`ToolCallRow.result`）+ 渲染槽（`CycleDetailPanel.vue` 工具表 `result` 列，空时显示「结果未持久化（待后端补全）」）。本迭代是纯后端改动，落地后整条链零改前端点亮。

## 背景 / 当前状态

- **数据模型** `src/storage/models.py:198-219`：`ToolCall` 当前列 = `id / session_id / cycle_id / tool_name / status / duration_ms / error_type / args(Text, 4000 char cap) / created_at`。**无 result 列**。append-only，无 UPDATE/DELETE 接口。
- **捕获点** `src/services/tool_call_recorder.py`：`ToolCallRecorder.wrap_tool_execute`（pydantic-ai capability，PR#21 引入的观察期埋点）。`result = await handler(args)` 在 try 体内获得返回值；`finally` 块同步构造 `ToolCall(...)` 并 `session.commit()`（每次调用一次写库）。返回值在 finally 作用域可达，当前未被捕获。
- **返回值形态**：所有工具返回 `str`（人类可读的格式化文本 / 表格，非 JSON）。最大输出工具 `get_market_data` 80 根 K 线约 15.3K char；其余工具（news / HTF / trade_journal / taker_flow / order_book / derivatives）均由显式 limit / depth 参数封顶，结构性远低于该量级。
- **WebUI seam** `src/webui/schemas.py:55-61`：`ToolCallRow.result: dict | list | str | None = None`（预留，恒 None）。`src/webui/queries.py:73-86`：`get_cycle_detail` 用 `select(ToolCall)` 全列查，构造 `ToolCallRow` 时未取用 `result`。
- **schema 初始化** `src/storage/database.py`：`init_db` 三路径——① 已在 Alembic 链 → `alembic upgrade head`（agent 每次 session 启动跑）；② legacy DB → stamp base + upgrade head；③ 空库 / 测试 fixture → `create_all` + stamp head。

**关键约束**：`tool_calls` 是只被 metrics / analytics / WebUI 读、**从不回喂 agent context** 的观察期埋点。result 的大小不影响 agent token 成本，只影响本地 sim DB 文件大小。

## 改动面（5 处，纯后端）

| # | 文件 | 改动 |
|---|------|------|
| 1 | `src/storage/models.py` | `ToolCall` 加 `result: Mapped[str \| None] = mapped_column(Text, nullable=True)` |
| 2 | `alembic/versions/<new>.py` | 新迁移，`down_revision = "7244c7b7185d"`（当前 head）。upgrade / downgrade 语义见下节「Migration 语义」（SQLite view 重建陷阱） |
| 3 | `src/services/tool_call_recorder.py` | `wrap_tool_execute` 在 finally 块序列化 result 并传入 `ToolCall(...)` |
| 4 | `src/webui/queries.py` | `get_cycle_detail` 构造 `ToolCallRow` 时补 `result=t.result`（直传 raw str） |
| 5 | `tests/` | recorder / queries / migration / webui api 回归 |

## Migration 语义（SQLite view 重建陷阱）

`tool_calls` 被两个 view 引用——`v_cycle_metrics` 的 `SUM(tc.duration_ms)`（`src/storage/views.py:47`）、`v_alert_lifecycle` 的 cancel_attempts CTE（`views.py:166`）。SQLite 的 `batch_alter_table`（temp-table copy-rename）在 rename 瞬间原表不存在，会触发全部 view 重解析、对引用该表的 view 报 `no such table` 而失败。这是本仓库反复踩过的地雷（见 `7244c7b7185d` / `4ee6c95d0430` 的 downgrade 注释）。故本迁移：

- **upgrade**：用 plain `op.add_column("tool_calls", sa.Column("result", sa.Text(), nullable=True))`。SQLite 原生 ADD COLUMN 不重建表、不触碰 view，绝对安全，**无需 drop view**。
- **downgrade**：必须沿用本仓库既定 pattern——先 `DROP VIEW` 全部 → batch `drop_column("result")` → 重建 view（从 `src/storage/views.py` import `ALL_VIEW_NAMES` + `ALL_VIEW_SQLS`）：

  ```python
  def downgrade() -> None:
      for view in reversed(ALL_VIEW_NAMES):
          op.execute(f"DROP VIEW IF EXISTS {view}")
      with op.batch_alter_table("tool_calls", schema=None) as b:
          b.drop_column("result")
      for sql in ALL_VIEW_SQLS:
          op.execute(sql)
  ```

- **用当前单源 `ALL_VIEW_SQLS` 重建，不需要 `_PRE_ITER` 冻结快照**：与 `7244c7b7185d`（删 `injected_events`，而当前 v_alert_lifecycle SQL 仍引用该列，故 downgrade 必须用冻结快照）本质不同——本迁移删的是 `result`，**无任何 view 引用 result**；且 downgrade 落在 `7244c7b7185d`，`injected_events` 列仍在，当前单源 view SQL 全部有效。照抄 `_PRE_ITER` 冻结快照是过度工程。

## 捕获语义（recorder finally 块）

与 args 序列化对称，紧邻其后：

- try 前初始化 `result = None`；`result = await handler(args)` 成功才覆盖该绑定。
- **status = ok / biz_error**（handler 正常返回，biz_error 是返回后由 `note_biz_error` 标记）→ 捕获 result。
- **status = error**（handler 抛异常，`result` 未被重新绑定 → 保持 None）→ result 列写 NULL。异常无返回值是自然语义，且与 `models.py:215` 既有 redaction 策略一致（异常路径只存 `error_type` 异常类名、不存 message/traceback 防敏感泄露）。
- **control-flow 异常**（`_CONTROL_FLOW_EXCEPTIONS`，`skip_record=True`）→ 整行不记录，行为不变。
- **截断**：cap = **30000 char**。`len(result_str) > 30000` → `result_str[:30000] + "\n…[truncated]"`（标记让查看者知道被截）。全部工具返回 str；非 str 输入做防御性 `json.dumps(result, ensure_ascii=False)` 再判长。

cap 选 30000 的依据：result 不进 agent context、不耗 token，cap 是防病态巨行的安全上界而非预算。30000 ≈ 已知最大工具 `get_market_data`（~15.3K）的 2× 头部空间，能保证所有现实工具的正常输出**全量记录**，截断仅在异常情况触发。`…[truncated]` 标记使「是否有工具撞到 cap」在第一个落地 sim run 即可被实测发现 —— 30000 是自校正的起始上界，必要时按实测数据再调。

## Query 接线

`src/webui/queries.py` `get_cycle_detail`：`select(ToolCall)` 已全列查（自动带新列），仅需在构造 `ToolCallRow` 时补 `result=t.result`。

**直传 raw str，不走 `_loads`**：工具返回是格式化文本表格不是 JSON。关键理由——**截断行（带 `…[truncated]` 标记）永远不是合法 JSON**：若走 `_loads`，未截断的短结果可能被解析成 dict/int、截断行回退成 str，使 result 类型在 dict/str 间漂移；直传 raw 给一致的 str 类型。`ToolCallRow.result` 类型 `dict | list | str | None` 接受 str。`args` 走 `_loads` 是因为 args 确为 JSON dict —— 两者数据性质不同，处理方式不同。

## 向后兼容（已查清，非新增风险类）

- agent `init_db` 每次 session 启动跑 `alembic upgrade head` → 下一个 / 恢复的 sim 库自动获得 result 列，此后工具调用即捕获。
- WebUI mode=ro 只读不迁移，依赖 DB 已在 head schema。这是 Phase 1a 既有约束（`select(AgentCycle)` 已依赖 `injected_events` 等 head 列），本迭代延续同一特性，不引入新问题类。
- **所有 `select(ToolCall)` 全列 ORM 读者隐式携带 result**：除 WebUI `get_cycle_detail` 外，还有 `metrics.py:367` `get_tool_call_summary`（仅被 dev 脚本 `scripts/tool_call_summary.py` + 测试调用，**不在 WebUI `compute()` / performance 路径**）。head schema 库上无害；未迁移旧库上与 cycle-detail 同样缺列报错。
- **未迁移的旧归档库**在 cycle-detail 端点会因缺 result 列报错（list / live / performance 端点不受影响 —— 它们不 select ToolCall 全列）；解法是 agent 重开一次该 session（触发 init_db upgrade）或手动 `alembic upgrade head`。文档记录此约束，不建运行时 schema 探测（YAGNI）。

## 不做（YAGNI）

- 不回填旧行：append-only，旧行 result = NULL，前端已有诚实空态。
- 不给捕获加开关：observability 恒有用，且不影响 agent token。
- 不动 `args` 的 4000 cap / 不给 args 补截断标记：out of scope。
- 不做跨 cycle result 去重 / 压缩。
- 不做运行时 schema 探测兜底旧库。

## 测试

1. **recorder — 成功捕获**：工具正常返回字符串 → DB `tool_calls.result` = 该字符串。
2. **recorder — 截断**：result > 30000 char → 存储值 = `[:30000] + "\n…[truncated]"`，长度符合预期。
3. **recorder — 异常**：handler 抛 Exception → status=error 且 result = NULL。
4. **recorder — biz_error**：handler 正常返回 + `note_biz_error` 标记 → status=biz_error 且 result 被捕获。
5. **queries**：`get_cycle_detail` 返回的 `ToolCallRow.result` = DB 中的值（含 None 行）。
6. **migration**：upgrade 后表有 result 列、downgrade 后无；revision 链接 head 正确。**既有 `tests/test_alembic_roundtrip_phase1.py::test_upgrade_idempotent_after_downgrade` 会直接跑新迁移的 down+up（`head_db` → `downgrade -1` → `upgrade head`，`check=True`），是 downgrade view 重建正确性的现成关卡，必须保持绿**；并断言 `tool_calls.result` 列的 add/drop + downgrade 后 3 个 view 全部恢复。
7. **webui api**：cycle-detail 端点响应 JSON 含 `result` 字段。

## 落地形态

三件套（spec + plan + impl）+ schema migration + 多文件改动 → 走 **PR**（非 mini-iter direct-merge）。

**时机协同**：若本迭代先落地再起下个 sim run（如 #73/#74 验证 sim），该 sim 的工具结果即可在观察台直接查看，收益叠加。
