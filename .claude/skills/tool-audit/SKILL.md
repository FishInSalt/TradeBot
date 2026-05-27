---
name: tool-audit
description: Audit a single agent-facing tool in this TradeBot codebase for three things — whether the agent can read it at a glance (clarity / ambiguity), whether its numbers are right (calculation correctness), and whether the agent actually grounds reasoning in its output (adoption). Use whenever the user wants to inspect, refine, polish, or evaluate a specific tool — phrasings like "审一下 get_X", "看看 get_Y 有没有问题", "打磨 get_Z", "tool-audit get_W", "get_X 输出格式合理吗", "agent 是不是真在用 get_X 的数据". Grounds findings in the actual session log (what the agent actually saw) plus DB quantitative slice, never in speculation.
---

# Tool Audit

打磨**单个**工具的诊断器。把"工具好不好"这个模糊问题拆成三条可被 session log **全量数据**回答的子问题，并把发现整理成议题清单。

**核心承诺：不偷懒。** 报告里每条议题都要有 (a) 量化分母 (n/N pct%) (b) 完整 read 抽样作质性 backup (c) 涉数值类 必有 spot-check。grep 总数不当结论 backup；样本读 < 10 个不当 quality backup；纸面读源码不当 correctness backup。skill 的可靠性建立在 **三重独立证据** 上，缺一项议题就退到 [低信度] 标注。

不做的事：spec / plan 写作（那是后续 iter 流程）、跨工具横向对比、prompt 调整。这个 skill 的边界就是**单个工具的实证体检 + 议题清单**。

## 何时启动

User 的请求落在以下任何一种语境，就该用这个 skill：

- "审一下 / 看看 / 打磨 / 体检 `get_X`"（明确点名某个工具）
- "`get_X` 的输出格式 / docstring / 字段标签合理吗"
- "agent 是不是真的在用 `get_X` 的数据 / 输出"
- "`get_X` 这个计算对不对"
- 用户贴出一段 session log 输出问"这个工具有什么问题"

**不该启动**：用户问的是工具 *设计* 层的横向决策（"要不要新增工具 X"、"两个工具职责怎么切"），那应该走 brainstorm + spec，不是这个 skill。

## 锚

工具设计原则（8+1 条）在 `docs/superpowers/principles/tool-design-principles.md`。本 skill **不复述**原则，只在出报告时按编号引用。读这个 skill 之前如果你还没读过那个主档，先去读。

## 三个诊断维度

| 维度 | 子问题 | 主要数据源 |
|---|---|---|
| **可读性** | agent 一眼能理解输出吗？docstring 有歧义吗？标签 / 单位 / 窗口齐吗？docstring 中有部分被 griffe 剥落丢给 LLM 吗？ | docstring 源码 + session log 中的渲染输出 |
| **计算正确性** | 数值算得对吗？closed-bar 还是含未收盘？NaN / 边界 / 时间对齐处理对吗？跨字段一致吗？ | 工具源码 + 关联 service 源码 + session log 中的具体数值 |
| **Adoption** | agent 真的拿这个工具的输出做推理了吗？还是只调不用、跨工具自己手算、或者 fabricate 信号？ | session log 中工具调用周围的 `▾ Reasoning` 块 |

对应你 CLAUDE.md 里说的"三大痛点"。每条议题在最终报告里**必须**能映射回三维度之一。

## 工作流

四个阶段。每个阶段给 framework，不是脚本——你按工具具体情况灵活组合。

### Phase 1 — 定位

确定要查什么、用什么数据查。

0. **工具类型分类（必）**：按下表判定，决定 Phase 2/3 adoption 维度的判读标准。判错类型 → 整轮 audit 议题倾向跑偏（对 surveillance 工具误把"安静期低 adoption"立为议题）。

   | 类型 | 示例 | Adoption 维度判读 | 重点检查 |
   |---|---|---|---|
   | **Information** | get_market_data / get_multi_timeframe_snapshot / get_higher_timeframe_view / get_position / get_market_news / get_derivatives_data | 低 adoption = 议题信号；正常调用应有 reasoning 引用 | 渲染清晰度 / 字段 adoption / 算法正确 |
   | **Surveillance** | get_active_alerts / get_active_alert / 价格警报 fetch 类 | 安静期低 adoption 是 by-design；判读看**事件期**（warrant 触发时）agent 是否消化 | 事件期消化 / 静期 token 浪费 / 状态变化语义 |
   | **Execution** | open_position / close_position / set_stop_loss / set_take_profit / cancel_order / place_limit_order / set_next_wake | adoption 不是核心；测 reject 语义 / 参数 validation / 错误回报清晰度 | 失败语义（原则 6）/ args validation / state-delta 渲染 |
   | **Memory / journal** | save_memory / get_trade_journal | adoption 看跨 cycle 引用 + 写入触发条件 | 写入语义 / 召回触发 / 跨 session 持久性 |

1. **找工具源码**：grep `def <tool_name>\|name="<tool_name>"` 在 `src/agent/tools_perception.py` / `tools_execution.py` / `tools_memory.py`。读完整函数 + docstring + 关联的 service 调用（往往 `src/services/` 下）。同时**逐字 diff** path A 源 docstring vs path B `tools_descriptions.py:<TOOL>_DESCRIPTION` 看是否一致（不一致 → 议题候选）。
2. **确认最新 session log**：默认用 `ls -t logs/session_*.log | head -1`；用户指定就用指定的。记下 session UUID + DB 路径（默认 `data/tradebot.db`）。
3. **确认工具确实有调用**：`grep -c "⚙ <tool_name>(" <session.log>`。如果 0 调用，直接告诉用户没有实证数据，让用户跑一轮 sim 或选个有调用的 session。

读完这步你应该知道：工具类型、源码位置、path A vs B 是否漂移、本次审查用哪份 session log + DB、有多少调用样本。

### Phase 2 — 取证

每个维度取一组证据。**不出结论**，只收料。**严禁偷懒**——grep 统计 / 样本 read / 数值手算三种证据都要有，缺一项都可能在 phase 3 立错议题。

**Phase 2 的不可省纪律**（按本 skill 历史教训固化，违反任一项就回炉重做）：

| 纪律 | 操作 | 违反症状 |
|---|---|---|
| **D1. Schema 侦察先做** | `PRAGMA table_info(tool_calls/agent_cycles)` + `sqlite_master` 表清单 | 用过时脚本默认查，漏新列 |
| **D2. DB raw vs script 对账** | 跑 `scripts/tool_call_summary.py` **必须**配一句 raw `SELECT COUNT/SUM/AVG` 对账 | 默认 `--since 1d` 截断长 sim 不自知 |
| **D3. Attribution-by-pairing + self-validation** | (a) 跨工具共享字段（MA / ATR / RSI 等）adoption 必须 split 成五桶 (target_only / mts_only / htf_only / multi_TA / other) 分别统计；(b) 配对完成后**必做自检**：选 1-2 个**已知 tool-only 的标签**（如 GMD 工具时选 MTS-only 的 "Range pos" / HTF-only 的 "100-period high"），验证它在对应桶占绝对多数 / 其他桶仅噪声水平。通不过 → 配对脚本有 bug，回炉重写 | 单纯 grep "MA20" 算 1518 次然后含糊说"归因稀释"；或配对脚本有 bug 但用了它输出立议题 |
| **D8. 共享字段枚举（attribution 前置）** | D3 之前**必做**：读目标工具 + 同类工具源码 / 渲染样本，列清单"目标工具哪些字段是独占、哪些与谁共享"。无清单做配对等于盲猜哪些字段需要分桶归因 | 漏识别共享字段 → 把"实为别工具贡献的 adoption"算到目标工具头上 |
| **D4. 完整 read 配额 ≥ 11 — stratified** | 两层抽：**Layer 1** 随机基线 5 个（目标工具 only 桶）+ **Layer 2** 疑似有问题子集 ≥ 6 个（拆三 stream：元 pattern 命中 / 频次 outlier / DB 错误行）。详见下方"D4 抽样细则" | 纯随机抽 10 个会偏向 modal case，漏掉 outlier 承载的高密度议题信号 |
| **D5. 数值 spot-check 必做** | 选 ≥ 1 个 sample，公式手算 ≥ 3 个 metric 对账渲染值（差异 ≤ 显示精度）；公式无法手算的用 invariant 检查（min ≤ avg ≤ max / 范围 0-100% 不越界 / p50 ≤ p95） | 纸面 reading 源码就说"算法可能错" |
| **D6. 元 pattern 扫描** | 全量 reasoning 上 regex 扫几类 ambient 现象（candle-timing / cross-tf 拼接 / multi-call / 跨 cycle delta / hand-compute）；不为某次 audit 临时加新 regex 时复用 `references/meta-patterns.md` 里的库 | 单点 outlier 误以为单点，但其实占 reasoning 24% |
| **D7. 每议题带 n/N 分母** | 任何议题 phrasing "agent 经常 X" 必须替换为 "X 出现 n/N = pct%"；分母明确 | "agent 似乎不用这个字段" |

**通用工具**（细节模板见 `references/empirical-queries.md`）：

1. **Schema 侦察** (D1): `PRAGMA table_info(tool_calls)` + `PRAGMA table_info(agent_cycles)` + `SELECT name FROM sqlite_master WHERE type='table'`
2. **Locate**：`grep -n "⚙ <tool_name>(" <session.log>` + DB `SELECT COUNT, GROUP BY args` 全量枚举调用
3. **Quantitative** (D2): `scripts/tool_call_summary.py --session <id> --since all` + raw sqlite 对账。脚本默认 `--since=1d` 静默截断已知坑，**必加 `--since all` 或对账**
4. **共享字段枚举** (D8)：读目标工具 + 同类工具源码 / 一个渲染样本，输出字段独占性表（哪些字段是目标工具独占、哪些与 MTS / HTF / Ticker 共享）。这表是下一步 attribution 分桶的前置依据
5. **Pairing dump** (D3a)：用 `scripts/parse_session_log.py <session.log> <out.jsonl> <target_tool>` 把 session log 切成 (Action tools, Reasoning text) 记录
6. **Adoption attribution** (D3a)：对每个待量化标签（来自步骤 4 的字段独占性表），在五桶下分别统计 mentions，得出 per-bucket adoption rate
7. **Attribution 模型自检** (D3b)：选 1-2 个已知 tool-only 的对照标签（不是目标工具的，是**邻居工具独占的**，如 MTS-only 的 `Range pos` / HTF-only 的 `100-period high`），跑同一 attribution 流程。期望：该标签 ≥80% 命中在对应桶、其他桶仅噪声（<10%）。通不过 → 配对脚本 bug，回炉
8. **Co-call 模式分析**：从配对 JSONL 统计目标工具同 Action 共调的工具频次分布。`alone %`、`+ MTS`、`+ HTF`、`+ get_position`、`+ get_active_alerts` 等。揭示工具实际使用上下文 + adoption 上限边界 + 接口闭环候选议题（同 Action 内 ≥2 次目标工具 = multi-call 议题候选）
9. **Full read** (D4)：按下方"D4 抽样细则"做 stratified 抽样（≥11），肉眼分类为：复述 / 推理依据 / 误读 / fabricate / 跨工具手算 / 在线自纠正
10. **Numeric spot-check** (D5)：用 `scripts/fetch_session_ohlcv.py --session <id>` 拉 OHLCV ground truth；选 sample 验 ≥3 metric
11. **Meta-pattern scan** (D6)：跑 `references/meta-patterns.md` 列的全量 regex 命中率
12. **Args distribution** + **per-cycle call count**（高 multi-call 指向接口闭环议题）

**可读性证据**：取 docstring 源码 + 3+ 个不同 args 的输出样本。看 docstring 是否有 `Example call:` / `Example output:` 这类 block——这些会被 griffe 从 `<summary>` 剥掉（详见 [memory](/Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/project_griffe_example_stripped.md)），需要确认是否进了 `Returns:` 块（survives）或路径 B `tools_descriptions.py` 通过 `@tool(description=...)` override（bypasses griffe 整体）。两个 path 都没救到的就是 dead doc。

**计算正确性证据**：D5 强制。任何 P0/P1 计算类议题没有 spot-check 不接受。

**Adoption 证据**：D3 + D4 联动。grep 数字给量化、full read 给质性分类。两者矛盾时（grep 高 / read 显示是机械复述）→ adoption 实质偏低，议题在 quality 不在 frequency。

每个维度的更详细 checklist 见 `references/<dimension>-checks.md`。

#### D4 抽样细则

纯随机抽样会偏向 modal case（"看市 → 没动作 → 设 wake" 类平淡 reasoning），漏掉 outlier 承载的高密度议题信号。两层 stratified：

**Layer 1 — 随机基线 (5 个，必)**

从"目标工具 only" 桶随机抽 5 个 reasoning block 完整 read。目的：
- 估 typical reasoning 形态，看 grep 量化是否 misleading
- 校准 adoption rate（"agent 真的把字段用进推理了" vs "只是字段名机械复述"）
- 避免完全只看 outlier 导致议题清单全是 edge case

**Layer 2 — 疑似有问题的子集 (≥6 个，必，三 stream)**

| Stream | 来源 | 配额 |
|---|---|---|
| **L2a. 元 pattern 命中** | 从命中率 ≥10% 的 pattern 子集中随机抽（每个 ≥10% 命中的 pattern 抽 ≥2，命中率越高配额越大） | ≥ 2 / pattern |
| **L2b. 频次 outlier** | per-cycle 调用次数 top 2 cycle / 单次 reasoning 字符数 top 2 / 单 Action 内同工具 ≥2 次的 actions 全部 | ≥ 2 + 全 multi-call actions |
| **L2c. DB 错误行** | `SELECT * FROM tool_calls WHERE status != 'ok'` 全部 read | 全部（通常 0–几个） |

当某 stream 集合极小（如 L2c 错误 0 行 / L2b outlier 全集 < 2），不强求填满；用实际可得。

**抽样后必做的分类**

每个 read 完的 block 标注分类，写进 phase 3 议题证据时反查（不强求落档于报告，但 reasoning 链要有）：

| 分类 | 含义 | 议题含义 |
|---|---|---|
| 复述 | agent 把工具输出原样复述，无后续推理 | adoption 量化数字虚高，质性 adoption 低 |
| 推理依据 | agent 用工具输出导出新结论 / 决策 | true adoption，工具发挥设计价值 |
| 误读 | agent 读错 / 误解输出 | 可读性议题（P0/P1） |
| Fabricate | agent 引用了工具**没给**的精确数 | 信号源混乱（违反原则 3） |
| 跨工具手算 | agent 用本工具 + 别工具数字手算派生量 | 接口闭环议题（原则 5） |
| 在线自纠正 | agent 在 reasoning 中自纠先前读错 | 可读性议题（强信号） |

### Phase 3 — 排议题

把证据归纳成议题，每条带：

- **维度**（可读性 / 计算正确性 / Adoption）+ **原则编号**（1–8 or 元，来自主档）
- **优先级**（P0 = 计算错误或 agent 因此做错决策 / P1 = 明显歧义或 adoption 接近 0 / P2 = 表达可改进 / P3 = 微优化）
- **证据**（session log 行号 + 量化数据 + 源码行）
- **建议方向**（不必到 patch 粒度；下游 iter 才出 spec）
- **预估改动范围**（docstring N 行 / output renderer N 行 / service 计算 N 行）

**议题立项前必做的反向 systematic 验证**：

每条候选议题在写入报告前，**必须**反向回 session log 找 2-3 个**不同 cycle** 的同类样本：

- 元 pattern grep 命中率高（如 24%）但只来自 ≤3 个 cycle → 是 cycle-locality 偏差，不是 systematic 议题，需重审分母
- 频次 outlier 单点议题（如 cycle 2c09 多调） → 必须在元 pattern 数据中找到该 outlier 类型的群体证据（如 candle-timing 24% 验证 cycle 2c09 是 systematic 的 instance），否则降级 P3 / 标 [低信度]
- 数值 spot-check 只验证 1 个 sample → 同一 metric 至少在 2 个不同 sample 复算一致

议题数没有上限也没有下限。**如果你只找到 1 条 P3 议题，就如实写**——别为了显得"有产出"凑数。原则 8 的"信任 agent + 工具优先"反过来也适用于 audit：如果工具实证看起来挺好，就这么说。

### Phase 4 — 出报告

落盘到 `.working/tool-audits/<YYYY-MM-DD>-<tool_name>.md`（per CLAUDE.md `.working/` 为迭代决策前数据分析层，不进 git）。

报告**模板**：

```markdown
# Tool Audit: <tool_name>

- **Audited**: <YYYY-MM-DD>
- **Tool source**: `src/agent/<file>.py:L<n>` (+ 关联 service `src/services/<file>.py`)
- **Data sources**:
  - Session log: `logs/session_<uuid>.log` (cycles <n>–<m>, <total> calls)
  - DB: `data/tradebot.db` session_id=<uuid>
- **Principles anchor**: `docs/superpowers/principles/tool-design-principles.md`

## 1. 调用画像

- 总调用数 / 涉及 cycle 数 / 错误率 / p50–p95 时延
- args 分布（默认值占比、被改写的字段、稀有组合）
- 在 reasoning 中被命名引用的次数（adoption 量化）

## 2. 议题清单

### 议题 1: <one-line title>  [P0/P1/P2/P3 · 可读性/计算/Adoption · 原则 X]

**现象**: <一句话>

**证据**:
- session log L<n>: <quote>
- 源码 `<file>.py:L<n>`: <relevant slice>
- DB / scripts/: <quantitative>

**建议方向**: <非 patch；只到"改 docstring Returns: 加单位"或"换算法用 closed-bar"这种粒度>

**改动范围估计**: docstring N 行 / output ~N 行 / 算法 ~N 行

### 议题 2: ...

## 3. 结论

- 议题计数：P0 × N / P1 × N / P2 × N / P3 × N
- 建议优先：<前 1–3 条>
- 暂缓 / wontfix：<理由>
- 是否触发新立 iter：<yes/no + 理由>

## 附录：out-of-scope findings（如有）

audit 过程中顺带浮出的非目标工具议题，按"建议立 backlog 候选"列出：

- 例：`scripts/<X>.py` 默认 `--since 1d` 长 sim 静默截断 (DB raw vs script default 数据偏差 N%)
- 例：DB 表 `<Y>` 与源码 `models.py` 字段长度不一致（schema drift）
- 例：邻居工具 `<Z>` 在 attribution self-validation 中暴露的可疑现象

仅列**有量化证据**的副产物 findings。空段 OK，不强求。
```

报告写完，**口头**告诉用户：报告路径 + 议题计数 + 最重要的 1–2 条 + 附录有无 out-of-scope findings。不要 echo 整篇——用户自己会打开看。

**Skill 自身改进点**：audit 过程中若发现 skill 指引不清 / 缺项 / 临场需 improvise 的地方，直接在对话中告诉用户"这次 audit 觉得 skill 需要改 X"，让用户决定是否改 skill / 写 memory。**不要把这种 meta 反馈塞进报告**——双 channel 会混乱，对话即时反馈机制已覆盖。

## 输出原则

- **证据先于结论**。每条议题都要能定位到 session log 行号 + 源码行 + 量化数字。"我感觉 docstring 不清晰"不是议题，"agent 在 N=27 次调用后只有 3 次 reasoning 提到 `Range pos`，说明这个标签 adoption 低"是议题。
- **引用主档**，不复述。比如 "违反原则 1 — fact-provider 不是 guard，docstring 出现 `Use this when ...` 类指导语"。读者要知道细节会自己点过去。
- **agent 行为是工具反馈**（原则 8）。不要写"建议在 system prompt 加一句让 agent 用这个工具"。如果 agent 不用，先反思工具描述 / 默认值 / 输出可读性。
- **可疑要标 [低信度]**。比如 "[低信度] 数值范围疑似越界，但 sample 太小（n=3）无法确认"——不要假装一切都板上钉钉。
- **不主动改代码**。这个 skill 只出诊断报告。议题落 spec / 改 code 是用户决定后走另一个流程（brainstorm → spec → iter）。

## 反模式速查

如果你发现自己在做下面任何一件事，停下来重新看上面：

| Anti-pattern | 重做方向 |
|---|---|
| 报告里出现没有证据的"应该" / "建议" | 找 session log / 源码 / DB 行号补证据，找不到就删 |
| 议题主要靠"我读 docstring 觉得拗口" | 那就用 session log adoption 量化它——agent 真的拗口吗？ |
| 议题全是 docstring 文字游戏（P2/P3 堆一堆） | 是不是漏看了正确性 / adoption？至少各维度过一遍 |
| 复述原则原文 | 引用原则编号即可，不要 paste |
| 想顺手 commit 改 docstring | 不在 scope；写报告，让用户决定走不走 iter |
| 没看 session log 就开始下结论 | 回 Phase 1 |
| session log 里 0 调用还硬出报告 | 告诉用户没有实证数据，停 |
