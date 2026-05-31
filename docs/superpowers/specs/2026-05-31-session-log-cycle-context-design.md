# Session log — per-cycle Context section

iter: `session-log-cycle-context` · 2026-05-31

## 1. 背景与动机

当前 session log 每个 cycle 的渲染顺序是 `Header(Cycle/Trigger/State 框)` → 紧接 `▾ Reasoning`(thinking 正文)→ `▾ Action` → `▾ Decision` → `▾ Footer`。问题:**Header 太薄,reasoning 一上来就铺开,读 log 的人(用户 / 开发者)没有"agent 为什么这么推理"的锚点** —— 要判断本轮推理是否合理,得往上翻到上一个 cycle 的 Decision 才能拼出 thesis。

本 iter 在 `Header` 与第一段 `Reasoning` 之间插入一个 `▾ Context (carried into this cycle)` 段,把 agent **本轮唤醒所携带的关键上下文**先摆出来,使读者能就地评估"在这个上下文下,本轮推理是否合理"。

**NEED 性质(如实标注)**:本 iter 的动机是**高频读 sim log 做 tool-opt 的工作流痛点**(reasoning 缺锚、需回翻上一个 Decision 拼 thesis)—— 这是**定性的工作流判断,非 DB 可量化指标**(回翻频率属人类阅读行为,不落库)。§2 的实证火力集中在论证 HOW(可解析 / 尺寸可控 / 降级安全),不冒充对 NEED 的量化。

## 2. 实证基础(sim #12, session `f0f7b24f`, 248 cycle 全量)

设计决策由最新交易会话数据驱动,而非直觉。下列数字可由 `scripts/ground_cycle_context_render.py` 复跑核验(参数 `[DB_PATH] [SESSION_ID]`,默认 sim #12;注:`data/tradebot.db` 是运行产物、gitignored、在主工作目录,本 worktree 内不存在 → 复跑须显式传主仓绝对路径):

- **格式合规与解析器口径收敛**:用"行锚定 `(N)` marker 提取 ①④"判据,**成功 231 (93.1%) / 兜底 17 (6.9%) / 部分 0** —— 与"按字段名松散检测全 5 字段"同口径一致(放宽 marker 正则容忍第 4 种写法后,两法收敛,见下)。
- **兜底的 17 条全是短文(≤374c),不是格式损坏**,而是两类合法形态:① persona 明确允许的"无事发生"一句话(`Done.` / `Next wake in 30 min`,最长 374c);② 当某 prior cycle 本身是 forensic(retry_exhausted / usage_limit_exceeded)时,注入的是 `_render_empty_decision_body` 系统文本(非 agent 摘要)。sim #12 恰好 0 条 forensic,故本批统计只覆盖 ok-cycle 摘要 —— 但解析层必须容忍第三种 body 形态(§3.4 兜底)。
- **字段标记有 4 种 cosmetic 写法**(均行首、含 `(N)+字段名`):`**(N) Field`(110)/ `(N) **Field`(83)/ `(N) Field`(32)/ **`### (N) Field`(markdown 标题,6)**。最后一种是早先窄正则漏掉、会把 6 条 ~2791–3451c 的完整 summary 误打到兜底的根源 → marker 正则须放宽到 `^(?:#{1,6}\s*)?\**\s*\(([1-5])\)`(含标题前缀),放宽后这 6 条全部收回,成功率 90.7%→93.1%,且剩余兜底全为短文。
- **字段长度(放宽正则口径)**:① median **108c** / p90 168 / max 339;④ median **685c** / p90 897 / max **1185**;①+④ median **810c** / max 1339;整条结构化 summary median **2425c** / max 5621。
- **log 不解释 markdown**:plain-text 渲染下 summary 的 `**` 全部以字面星号显示(实测 Decision 段出现 `**(1) Stance**`、`**Invalidation triggers**`)→ verbatim 提取需剥离。
- **唤醒 prompt 半数是纯样板**:124/248(50%)scheduled cycle 的唤醒 prompt 切片是三行常量,零增量;有价值的是 conditional/alert 才有的变量事件行。

## 3. 设计

### 3.1 渲染位置与形态

新增顶层 `▾ Context (carried into this cycle)` 段,复用现有 `▾`-section 视觉惯例(`▾ Name` 标题 + `escape()`),排在 `Header` 框之后、第一段 `▾ Reasoning` 之前。该段内部引入**轻度嵌套**(prior-cycle sub-header 缩进一层、字段再缩一层)—— 这是其它段没有的结构,故并非"零新视觉方言",而是受控的一处嵌套。

下例为 alert-触发 cycle 的**理想化排版示意**(真实终端:字段内容经 §3.5 collapse 后靠软换行,无手工悬挂对齐;实际更扁平):

```
═══════════════════════════════════════════════════════════════════════════
  Cycle 06e9  •  07:35:46 UTC  •  +6 min from prev
───────────────────────────────────────────────────────────────────────────
  Trigger    ALERT — BTC reached 73,384 (above $73,384 alert)     ← Header 不动(§4 scope)
  State      FLAT | Balance $9,632
═══════════════════════════════════════════════════════════════════════════

▾ Context (carried into this cycle)
  Woke by — PRICE LEVEL: BTC/USDT:USDT reached 73384.00 (alert id=934c9cfd above
            73384.00 — MA20 reclaim: bounce momentum / bearish structure weakening)
  Carried thesis — last 3 cycles (newest first):
    00f7 · 8 min ago
      Stance — Flat. MA20 reclaim confirmed by 07:30 close; bearish bias tempered.
      Thesis — Bearish macro intact (higher TFs below MAs). Invalidation: break > 74,200
               or < 73,100. Conviction: low near-term, moderate range-bound.
      (+3 more)
    47d5 · 34 min ago
      Stance — Watching; MA20 reclaim is the battleground, no position.
      (+4 more)
    824e · 35 min ago
      Stance — Flat; cascade compressing into 73,000, taker flow turning balanced.
      (+4 more)

▾ Reasoning (899 chars total)
  …
```

(`06e9` 是当前 cycle;carried thesis 是它**之前**的 `00f7 / 47d5 / 824e` —— 当前 cycle 绝不出现在自己的 carried list 里,因为注入块在本 cycle 行写库之前就已组装。**若当前 cycle 是 scheduled,`Woke by` 行整体省略**,Context 段直接从 `Carried thesis` 起,见 §3.3。)

### 3.2 数据源:单一字段 `user_prompt_snapshot`(仅 live 一条路径)

整个 Context 段从已存的 `agent_cycles.user_prompt_snapshot` 派生 —— **零新 DB 字段、渲染层零 DB 查询、不回查上一个 cycle 的 decision**。当前 `format_cycle_output` 仅有 `app.py` 的 3 处 live 调用方(grep 全仓 + scripts + tests 确认),**不存在从 DB 行重建 `CycleRenderContext` 的回放渲染器**,故只有 live 一条数据源(`run_agent_cycle` 内的 `user_prompt_snapshot_var`);本 iter 不引入 replay 工具。

工程上把 `user_prompt_snapshot` 透传进 `CycleRenderContext`(新增字段,**默认 `None`** —— `CycleRenderContext` 是 `frozen` dataclass 且现有 12 字段全无默认值,新字段须加在末尾带默认值,否则现有全部 kw 构造点 `app.py ×3` + tests 会 `TypeError`)。以注入块标记 `"Your prior cycle summaries (most recent N=3, from this session):"` 切成两半:前半 → §3.3 Woke by;后半 → agent 实际看到的注入 summary 块(已含 `[cycle …]` 头 + 经 `_truncate_decision` 的正文)→ §3.4 Carried thesis。此举使 Context 段 = "agent 当时读到的那份",保真度最高。

### 3.3 Woke by(只在 conditional/alert 渲染,verbatim 事件行)

丢弃每 cycle 重复的常量 scaffold(`You have been woken up by a … trigger` / `Trading pair … | Timeframe …` / `Assess the situation and decide what to do.`)。byte-exact 全文本就存于 DB `user_prompt_snapshot`,优化 prompt 时查那里;log 只为人读。

- **`trigger_type == "scheduled"` → 不渲 `Woke by` 行**。scheduled 的唤醒切片是零增量纯样板(与 Header `Trigger SCHEDULED` 完全重叠,占 ~50% cycle),与本 iter 去冗余主旨冲突;省略之,Context 段直接从 `Carried thesis` 起。(纯 scheduled 且无 prior 的首 cycle → 整个 Context 段省略,与 §5 None→省略哲学一致。)
- **`trigger_type in ("conditional", "alert")` → `Woke by — <verbatim 事件行>`**:取前半段去掉常量 scaffold 后剩余的事件文本,以已知前缀 `IMPORTANT EVENT` / `PRICE ALERT` / `PRICE LEVEL` 锚定,**原样保留**(含 `alert id` / reasoning / fee / PnL / round-trip)。这正是它比 Header `Trigger` 行更丰富、从而有独立价值的形态。识别不到事件文本 → 不渲 `Woke by` 行。

### 3.4 Carried thesis(Stance 全渲 + Thesis 仅最近 + `(+N more)` 省略指示)

对后半段注入块按 `[cycle …]` 头切成最多 3 个 per-cycle block。**注入块本身按 `(created_at, id)` ASC = 最旧在前(`_render_recent_summaries`),Context 要 newest-first,故解析出的 block 列表需反转。**

每个 block:

- **sub-header**:`<id4> · <ago>`。block 头有两变体 —— valid decision `[cycle <id8> · <trigger> · <utc> (<ago>) · <N> words]`,NULL/forensic decision `[cycle <id8> · <trigger> · <utc> (<ago>)]`(无 `· N words`);解析 id+ago 须**容忍两者**(ago 取 `(...)` 内文本,位于可选 `· N words` 之前)。id 由块头 `cycle_id[:8]` **再切到 4 字符**对齐 Header `Cycle` 行;ago **去括号**(`(8 min ago)` → `8 min ago`)。
- **字段提取**:marker 正则 `^(?:#{1,6}\s*)?\**\s*\(([1-5])\)\s*`(容忍 4 种写法),按相邻 marker 位置切片。
- **渲染规则(优先级:兜底 > 字段渲染)**:
  - block 中 ①(Stance)与 ④(Thesis)均可定位 → **结构化渲染**:
    - **每条** carried cycle → 渲染 `Stance —`(字段 ①),给跨 cycle 的 stance 轨迹(每条 ~108c)。
    - **仅最近一条**(反转后第一条)→ 额外渲染 `Thesis —`(字段 ④),给当前活 thesis 详情。
    - **省略指示**:每条在其最后一个已渲字段之下、独占一行渲 **`(+N more)`**,`N = (该 summary 实际存在的字段数) − (已渲染字段数)`(**动态算**:⑤Watch 是 persona optional 字段、agent 常省,N 必须反映真实存在的字段、不可写死;故较早槽 N 通常为 4、最近槽为 3,且 Watch 缺席时各减 1)。`(+N more)` 是 fact-only 的"上方完整 Decision 里还有 N 个字段"提示,不搬正文(保 §6 去重)。
  - block 无 ① 或无 ④(terse 一句话 / forensic-prior `_render_empty_decision_body` 系统文本,合计实测 6.9%)→ **整条 block 兜底**:正文原样(经 §3.5 清洗 + §3.6 截断),**不渲字段标签、不渲 `(+N more)`**。最近一条若自身落兜底,同样走整条兜底。
- **边界定界(严格化)**:Stance 内容以"其后下一个 marker 或 block 末"定界;最近一条的 Thesis(④)以"**⑤ marker 或 block 末**"兜底定界(不依赖 ②③⑤ 全在 —— 真实数据为全 5 或全无的二元,但判据须对"仅 ①④ 在"的退化情形也正确)。

### 3.5 渲染清洗(prev summary,全做)

1. **剥离 `**` markdown**(log 不解释,否则字面星号噪声)。
2. **去 marker 前缀**(`### ` / `(N)` / `**`),归一为干净标签 `Stance —` / `Thesis —`(④ 原文标签 `Thesis & invalidation`,统一缩写 `Thesis —`)。
3. **collapse 内部空白**(`\s+` → 单空格):多行散排压成单段,随 `▾` 段缩进**靠终端软换行**自然折行(§3.1 mockup 的手工悬挂对齐是理想化示意,真实输出更扁平 —— label 分隔仍在,可读性由 `Label —` 前缀 + 独占行的 `(+N more)` 保证)。
4. **截断后缀复用** `_render_reasoning` 的 ASCII ` ... [+N chars]` 风格(ASCII `...` 非 Unicode `…`,与 `display.py:917` 一致)。

### 3.6 长度安全网

采 Stance-默认 + Thesis-仅最近后,Context 段 carried 部分的**典型量(median 量级)**≈ `3 × Stance(108) + 1 × Thesis(685)` ≈ **1000c/cycle**(对比"3 条各 ①④"的 median ~2400c,降 ~2.5×);真正上界 ≈ `3 × max(339) + max(1185)` ≈ **2200c**。

- **最近一条的 Thesis** 设单一字符上限(默认 ~1500c,覆盖实测 ④ max 1185 + 余量,实测不触发,仅防病态长文)。
- **Woke by 事件行**设较小上限(~500c —— 实测最长事件行为 full-close fill 串约 150c + price-level reasoning,500c 充裕)。
- **兜底 whole-block** 设显式 char cap(~500c,**尤其 earlier-slot**):注入体虽已被 `_truncate_decision` 预截到 8000c,但那对"本应 1 行 Stance"的 carry 是过大天花板;显式小 cap 防 §3.4 兜底分支(如未来出现新 marker 写法)在 earlier-slot 渲出整条长文、跨 cycle 重复。
- 整体与 `persona.py` 现有 `CYCLE_DECISION_CHAR_HARD_FLOOR = 8000` 的"安全网而非主约束"哲学一致。

## 4. Scope 边界(本 iter 不做)

- **Header `Trigger` / `State` 行保留不动**(`Trigger` 与 conditional/alert 的 `Woke by` 语义重叠是已知项;先保留,后续若确证为痛点再删 —— 不提前优化)。
- **不改 agent 实际 prompt / 注入逻辑**(N=3 注入保持)。
- **不新增 DB 字段 / migration**(复用 `user_prompt_snapshot`)。
- **不引入 DB-replay 渲染器**(只有 live 一条源)。
- **不改 `state_snapshot` 语义**:`State` 行数据来自唤醒快照,而该快照**并不传给 agent**(agent 靠工具自取)。本 iter 不在 Context 段呈现它,以免读者误把"系统旁路快照"当成"agent 推理依据"。`State` 行留在 Header 不变。

## 5. 边界与降级

- **`user_prompt_snapshot is None`** → **整个 Context 段省略**(最安全)。该列 nullable(Phase 3 才加,旧行 NULL);live 路径恒非 None,故此规则只影响传 None 的现有测试 / 假想旧行,对 live 零冲击。
- **scheduled 且无 prior 的首 cycle** → Context 段无 `Woke by`(§3.3)且无 `Carried thesis` → 整段省略。
- **conditional/alert 首 cycle**(有 Woke by、无 prior)→ 只渲 `Woke by` 行。
- **N<3 prior**:展示实际存在的 1 或 2 条。
- **terse / forensic-prior block**:走 §3.4 整条兜底(无字段标签、无 `(+N more)`)。
- **forensic 短路路径**(`messages is None`,usage_limit_exceeded / retry-exhausted):该路径 `user_prompt_snapshot` 同样已落库(`app.py:591 / 643`)且 `user_prompt_snapshot_var` 在 retry loop 前定义(`app.py:545`)→ 两个 except 块都能取到 → Context 段照常渲染。
- **任何解析 / 提取异常**:fail-isolated —— Context 段降级为空或仅 `Woke by`,绝不阻断整 cycle 渲染。

## 6. 风险与缓解

- **渲染层解析耦合注入格式**:Context 段解析 `_render_recent_summaries` 产出的注入块字串(块头 `[cycle …]` 两变体 + body)。若该格式漂移,解析失配。**缓解**:round-trip drift-guard 测试 —— 用 `_render_recent_summaries` 真实产出(含 valid + forensic-prior 两种 body)喂解析器,断言能正确切块 + 反转 + 提字段 + 算 `(+N more)`;漂移时该测试先红。
- **字段提取脆弱性**:agent 偶发新写法(如 `### (N)` 曾漏)→ 由放宽正则(4 写法)+ §3.4 整条兜底 + §3.6 兜底 cap 三层兜住,实测兜底率降到 6.9% 且全为短文。
- **为何 selective(提 ①④ / 反转 / Thesis-仅最近)而非 verbatim-clean(近 verbatim 渲整块)**:本 iter 的核心收益是 §3.4 的差异化裁剪(较早 2 条仅 1 行 Stance、最近 1 条加 Thesis)以压重复;**verbatim 渲整块做不到这种裁剪**(它就是 3 条全文 ~7200c,丢掉去重)。故 selective 是兑现去重目标的必要手段,其解析耦合代价由上面三层缓解 + drift-guard 控制,值得。
- **重复信息(已正面处理)**:reviewer 量化每条 thesis 在连续 log 中约出现 **2×**(自身 Decision + 它作为某 cycle newest-prior 被 carry 的那一次;较早槽位只 carry 单行 Stance,不重复其 thesis 正文)。本 iter 采 **Stance-默认 + Thesis-仅最近 + `(+N more)` 省略指示** 即为压此重复:carried 体量 median 量级 ~1000c/cycle(上界 ~2200c),且 `(+N more)` 让"还有哪些字段在上方"对读者透明、不必把正文搬下来。

## 7. 实现影响面

(以下行号为**当前快照,以符号为准**,易随他 iter 漂移。)

- `src/cli/display.py`:
  - `CycleRenderContext` 增 `user_prompt_snapshot: str | None = None`(末尾、带默认,保现有构造点)。
  - 新增 `_render_context(...)` + helper(`_split_wake_prompt` 切前/后半 + 取事件行、`_parse_injected_summaries` 切块+反转+解析两变体块头、`_extract_summary_fields` 提 ①④ + 算 `(+N more)`、清洗 / 截断)。
  - `format_cycle_output` 在 header append 之后、`messages is None` 短路之前(`display.py:1079`→`1086` 之间)插入一处 → 同时覆盖 success + forensic 两路径。
- `src/cli/app.py`:在 success 与 forensic 两处构造 `CycleRenderContext` 时传入 `user_prompt_snapshot_var`(共 3 处)。
- `scripts/ground_cycle_context_render.py`:§2 实证复跑脚本(已建,marker 正则含 4 写法)。

## 8. 测试策略

TDD:每个 helper 先写失败测试再实现,断言锚定**行为**(渲染输出文本结构)而非内部正则。

- **解析器(4 写法)**:`**(N)` / `(N) **` / `(N)` / **`### (N)`** 均干净提 ①④;terse 一句话 → 兜底;forensic-prior `_render_empty_decision_body` body → 兜底;"仅 ①④ 在(缺 ②③⑤)"退化 → ④ 以 block 末兜底定界。
- **切半**:有/无注入块标记;`user_prompt_snapshot is None` → 整段省略。
- **块切分**:0/1/2/3 条;**源 ASC → 反转为 newest-first** 顺序断言;块头**两变体**(有/无 `· N words`)均能取 id+ago。
- **渲染规则**:Stance 全渲 + Thesis 仅最近;`(+N more)` **独占行、N=实存−已渲、动态**(构造缺 Watch 的 summary 断 N 减 1、不误报);newest 与 earlier 的 N 差(3 vs 4);最近一条自身落兜底 → 整条兜底、无标签无 `(+N more)`(优先级 兜底 > 字段)。
- **Woke by**:scheduled → **不渲 Woke by**(整段从 Carried thesis 起 / 无 prior 则整段省略);conditional → 事件行(含 fee/PnL);alert(volatility / price-level)→ 事件行(price-level 保 `alert id` + reasoning)。
- **清洗**:`**` 剥离 / 4 写法 marker 前缀去除 / 空白 collapse / ASCII `...` 截断后缀。
- **长度**:Thesis cap ~1500 / Woke-by 事件行 cap ~500 / 兜底 whole-block cap ~500。
- **边界**:首 cycle(scheduled vs conditional/alert)/ forensic 短路路径。
- **drift-guard**:`CycleRenderContext` 新字段透传(对照 `app.py` 构造点);§6 round-trip(`_render_recent_summaries` 两种 body 产出 → 解析器)。
- **向后兼容审计**:现有 `test_display_cycle.py` 结构断言(`▾ Reasoning`/`▾ Action` 计数、Decision 先于 Footer 等)与 `test_iter_alert_trigger_id_unknown_tool_render.py` 在新字段默认 None 下应全绿 —— 显式声明此前提:None → 不渲 Context,故现有断言不受影响。
- **真实数据 fixture**:取若干 sim #12 真实 `user_prompt_snapshot` 入 fixture,覆盖 4 写法 + 兜底 + markdown 星号 + 两块头变体。
