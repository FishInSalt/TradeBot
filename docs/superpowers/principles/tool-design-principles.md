# 工具设计与优化原则

**Status**: 持久化参考文档（进 git，可迭代）
**Origin**: 基于 sim #8 W2 观察期数据（178 cycles / 19.2h / 1818 tool 调用）+ 三高频工具深度分析（GMD / HTF / MTS）实证归纳
**Scope**: 项目所有 agent-facing 工具的设计、优化、新增、删除决策的根本约定
**应用时机**: 工具相关 spec session 起手必读；议题立项前 checklist；brainstorm 时冲突解决依据

---

## 0. 一分钟摘要

工具不是"功能集合"，是 **agent 心智路径的延伸**。设计错了不会让 agent 卡住——agent 会自适应——但会暴露在 token 浪费、决策摩擦、心智路径错位上。

8 条核心原则 + 1 条元原则:

1. **Fact-provider 不是 guard** — 工具名 + 输出 + docstring 全文都 fact-only；执行类 explicit reject 不 silent clamp
2. **工具服务 agent 心智路径** — 工具对齐 agent 已有心智；docstring 是 call→output 示例不是"X for Y"指导
3. **信号唯一权威来源** — 每个信号一个来源，避免 agent 对账
4. **信号补齐优先于新工具** — 现有工具的 underlying data 没被丢弃才考虑新工具
5. **接口闭环常用 pattern** — 高频 multi-call 是设计缺陷
6. **失败语义区分** — 操作异常 reject / 状态不存在 idempotent
7. **输出与命名的表达友好** — 字段带标签/单位/窗口；同名字段不同语义显式区分
8. **信任 agent + 工具优先** — Agent 行为偏差是工具反馈，不是 prompt 失败；prompt nudge 是 last-resort
**（元）实证优先于直觉** — 议题立项前必查 sim 数据

---

## 1. 核心信念

### 1.1 工具是产品，agent 是用户

工具设计的成败不是"功能完整不完整"，而是 agent 在真实 cycle 中**用得起来吗、用得好吗、信任它吗**。

### 1.2 agent 的反馈不在 issue 里，在 narrative 里

agent 不会提 PR 抱怨工具，但会在 reasoning 中暴露所有痛点：手算、对账、困惑、绕路、试错。**Session log 是工具最权威的反馈来源**。

### 1.3 设计错了不会卡住，会被掩盖

agent 自适应力强 → 即使工具设计很差也能完成任务。但代价是 token 浪费 / 决策摩擦 / 心智路径错位。"agent 现在 OK 啊"不是不优化的理由。

### 1.4 数值正确性是底线，不是设计议题

工具的计算过程和结果必须正确——这是任何工具的前提，不在原则讨论范围内。设计原则讨论的是"在计算正确前提下如何更好服务 agent"。实施时通过单元测试 / drift guards 保证计算 invariant；偏差暴露后作 bugfix 而非设计议题。

---

## 2. 八条核心原则 + 一条元原则

### 原则 1：Fact-provider 不是 guard

**定义**：工具提供事实数值，不提供评价/判断；执行类工具用 explicit reject 不 silent clamp。**fact-only 范围覆盖工具名 + 输出 + docstring 全文**，不只输出层。

**已 landed**：memory `feedback_observation_period_soft_constraint`（R2-1 PR #30）

**应用规则（按范围分）**:

**输出层**:
- ✅ 事实数值 / 事实分类 / 事实标签
  - `"Volume: +5.0× avg"` (比率事实)
  - `"MA50 above MA200"` (状态事实)
  - `"Alignment: MIXED"` / `"ALIGNED-UP"` (分类标签，无价值取向)
- ❌ 评价词 / 解读 / 情绪倾向
  - `"Volume: heavy"` / `"strong"` / `"good"` (评价)
  - `"This indicates a bullish breakout"` (解读)
  - `"appropriate"` / `"reasonable"` (主观判断)

**工具名层**:
- ✅ 陈述输出内容: `get_macro_calendar` / `get_market_data` / `get_position`
- ❌ 评价词或暗示用途: `get_critical_alerts`（critical 是评价词）/ `get_best_entry`（best 是评价）/ `get_recommended_pairs`（recommended 是判断）

**docstring 全文**:
- ✅ 描述工具做什么（事实陈述）: `"Returns ticker, indicators, market context, and recent candles for the given timeframe."`
- ✅ 参数事实定义: `"candle_count: int = 50. Values above 50 may be capped by exchange API limits."`
- ❌ "何时调用"指导语: `"Use this when you need a multi-timeframe view"`
- ❌ "X for Y" 用途评价: `"candle_count=20 for quick check, 50 for detailed analysis"`
- ❌ 价值倾向: `"Best for short-term entry decisions"`

**执行类层**:
- ❌ schema constraint 强制约束 — 让事实流通，不让工具替 agent 决策
- 执行类失败显式 reject + 错误原因，不要静默 clamp 到合法范围

**证据**:
- `feedback_observation_period_soft_constraint` 已固化设计哲学
- N6 G1/G2 设计明确禁用 "rising/falling/heavy/light" 等解读
- 当前 GMD docstring `"candle_count=20 for quick check or secondary timeframes, 50 for detailed analysis"` 是 anti-pattern 实例（议题级清理候选）

**Red flag**:
- docstring 出现 `"should"` / `"appropriate"` / `"good"` / `"best"` / `"X for Y"` / `"use when"` 类语气
- 输出标签或工具名包含价值倾向（"strong" / "weak" / "critical" / "healthy"）
- 工具替 agent 做决策（自动选择"合适"参数）

### 原则 2：工具服务 agent 心智路径

**定义**：从 narrative 提取 agent mental model，工具的 docstring / schema / 输出与 agent **自用词汇**和**思维角色分工**对齐；不要倒逼 agent 走工具的设计路径。

**应用规则**:
- 设计/优化前先 grep session log 提取 agent 自用词汇（如 "context" / "confirmation" / "broader picture" / "alignment"）
- docstring 用 agent 自己的话写，不用开发者技术语言
- 工具角色与 agent 心智角色 1:1 对应（避免一个工具承担多角色）
- **反复手算 = 工具化候选**：narrative 中显式手算同一信号 ≥3 次，是工具化优先级提升的硬指标
- agent 自适应力 OK 不是不工具化的理由——token 浪费和决策摩擦仍是优化目标
- **docstring few-shot**：提供**完整调用 + 输出片段**示例，让 agent 看真实行为；**禁止**"何时调用"的指导语和"X for Y"的用途评价（属原则 1 fact-only 范畴）
  - ✅ 事实示例（工具实际行为演示）:
    ```
    Example:
        get_market_data(timeframe="5m", candle_count=20)
        →
        === Ticker (BTC/USDT:USDT) ===
        Price: 81870.50 | ...
        === Recent Candles (5m, last 20) ===
        ...
    ```
  - ❌ 反例: `"Use when..."` / `"Best for..."` / `"X for quick check, Y for detailed analysis"`

**证据**:
- 100% (1m, 5m, 1h) 三件套 cycle / 仅顺序不同 → agent 心智 "1m primary / 5m confirmation / 1h context" 早已成熟
- `multi-TF alignment` 6+ 次 narrative 引用作 conviction 决策核心 → mts 应是这个心智的工具载体
- R:R 手算 9 次 / volume ratio / ATR multiples / OI 变化率手算反复出现
- 当前 GMD docstring `"Use multiple timeframes to build conviction"` 反向倒逼 multi-call
- 当前 GMD docstring "X for Y" 子句让开发者预设使用模式，sim #8 实证 agent 没按引导走（自己心智决定 100% 三件套）

**Red flag**:
- 议题描述说"agent 应该这样用工具"——如果不是从 log 实证出发，大概率倒逼
- docstring 用纯技术术语（"OHLCV dataframe with technical indicators"）而非 agent 决策意图（"Quick regime check across timeframes"）
- 工具签名要求 agent 提供工具实现细节（如内部 cache key）

### 原则 3：信号唯一权威来源

**定义**：每个信号定义"哪个工具是唯一来源"，其他工具引用而非重新计算；同一信号多源表达不一致是设计缺陷。

**应用规则**:
- 设计/优化时画"信号矩阵"（每行一个信号，标注唯一来源）
- 重叠信号必须二选一：去重 OR 显式标注语义区分（如 "per-tf last close" vs "global ticker"）
- 新工具加信号前查"是否其他工具已是该信号的唯一来源"
- 信号矩阵示例参考 `.working/sim8-w2-multi-tf-deep-dive.md` §4 / §6

**证据**:
- mts current_price vs ticker ≥8 cycles 显式困惑（agent 推断 `"must have stale data"`，实际是设计 intentional）
- MA50 距离 GMD/MTS/HTF 都给（不同 tf 不同表达） → agent 自己对账
- Range pos 三处不同表达（raw / width% / pos%）

**Red flag**:
- 两个工具计算同一指标但用不同 tf / lookback / 公式
- agent narrative 反复出现"X tool says Y, but Z tool says different"
- 文档说"as a convenience, this tool also includes..."（信号搬家而非分工）

### 原则 4：信号补齐优先于新工具

**定义**：先看现有工具的 underlying data 有没有"被丢弃的信号"再考虑新工具；新工具增加 agent 选择延迟和 LLM context 占用，门槛要高。

**应用规则**:
- 新工具 wishlist 出现时，先查"现有工具的 fetch_X 函数 return 了什么数据，输出展示了什么"
- "已 fetch 但未展示"的数据补齐成本 < 新工具
- 新工具门槛三选一：① 解决 ≥3 cycles 明确缺口 ② 现有工具拆分（不是叠加）③ 提供现有工具确实拿不到的独有信号
- 长期 0-call 工具 = 设计反馈：① 删除 ② 合并 ③ docstring promo ④ 留观察（按价值降序）
- 工具集精简 = optimization，不是 over-engineering

**证据**:
- N6 G1: HTF 当前完全丢弃 volume 列（DataFrame 里有但没输出）→ 50 行修复 vs 新工具几百行
- N6 G2: MA 距离已给但 MA 斜率没给（同一份 OHLCV 数据）
- sim #8 4 工具 0-call（macro_context / etf_flows / stablecoin_supply / adjust_leverage）→ 不一定是 agent 错，可能是工具不该存在

**Red flag**:
- 议题描述是"加 X 工具会很有用"但没量化"现有方式失败了几次"
- 新工具 fetch 的数据现有工具已经 fetch 了
- "用不到也不亏"思维（错的：每加一工具增加选择延迟 + context tokens）

### 原则 5：接口闭环常用 pattern

**定义**：高频被 multi-call 拼凑的工具，应通过接口扩展（list / preset / batch）让单调用完成；default 值反映实测主流场景。

**应用规则**:
- 看 cycle 内同工具调用 ≥2 次的频率；高频 = 接口表面缺闭环
- 加 list / batch 入参 OR 预设组合 OR 升级到更高级别工具
- default 值 = 实测 mode（最高频组合），不是开发者直觉
- 输出 token 总量与使用频率成反比（高频工具更小、低频工具可大）
- 输出 sectioning + 主要信号在前 + 详细数据可截断（fold）
- 但避免 over-engineering：罕见组合不值得加 batch

**证据**:
- HTF 单 tf form → 4h+1d 必双调（5/5 双调 cycles）
- GMD 单 tf form → (1m, 5m, 1h) 三件套必三调（100% 三调 cycles）
- add_price_level_alert 单值 form → 上下双边界必双调（41 cycles）
- GMD `candle_count=50` default 实际只 8.8% 使用，主流是 20/30/10
- K-line table 占 75% GMD token 但 agent 实际重点是 indicators + 最近 5-10 candles pattern

**Red flag**:
- DB 查询显示某工具同 cycle 多调 ≥10% cycles
- default 与实测主流偏离 >2×
- 输出大块 fixed-format（如 50 行 K-line table 全程没 fold/截断选项）

### 原则 6：失败语义区分

**定义**：工具失败要区分"操作类异常"（reject + retry）与"状态不存在"（idempotent + ok with note）。

**应用规则**:
- 失败分类:
  - **真异常**（network / permission / format invalid）→ reject + 明确错误原因
  - **状态不存在 / 已完成**（如 alert 已 trigger / order 已成交）→ idempotent ok + note "Already X"
- 但避免 idempotent 滥用导致执行类工具丢失 reject 语义（参见原则 1）
- agent narrative `"that's fine, it already X"` 反复出现是 idempotent 候选信号

**证据**:
- cancel_alert 40% 失败 / 全 `alert_not_found`：alert 已 trigger 后从 active 集合移除是事实状态
- agent narrative: `"The alert already triggered or expired. That's fine"` — agent 已自适应但 token 浪费

**Red flag**:
- 工具失败率 >20% 且全部相同 error_type
- agent narrative 反复出现"that's fine" / "expected" 之后接重新计划
- 失败原因混在一个 enum 里（事实状态 + 真异常）

### 原则 7：输出与命名的表达友好

**定义**：工具的输出字段、单位、窗口、工具名都必须 agent 一眼读懂；语义与展示数据一致。

**应用规则**:
- **字段必带语义标签**（不只数值）: `BB: U 81960 / M 81727 / L 81494` 而非 `BB: 81960 / 81727 / 81494`
- **数值必带单位 / 窗口 / 时点**: `Last bar vol: 123.6 (0.90× avg)` 而非 `Volume: 123.6`（看不出是 last bar / 24h / current）
- **同行不混合方向相反语义**: 参考 F-C3 教训——multi-tf 单行 `+12.1% vs MA50 | MA50 below MA200` momentum + structure 同行方向相反
- **同名字段不同语义必须显式区分**: 如 mts current_price vs ticker.last 应分别标 "global ticker" / "per-tf last close"，而非都叫 `current_price`
- **sectioning 优于纯 alignment**: 用 `=== Section ===` 分块比长 K-line table 空格对齐对 LLM 更友好（R2-8c PR #37 已证）
- **工具名陈述输出**（与原则 1 命名规则联动）

**证据**:
- F-O2: BB labels 缺 U/M/L → agent 要从位置推断
- F-O3 / F-C1: Volume ratio 含义陷阱（不显示窗口看不出是 last bar / 24h / current）
- F-C3: Multi-TF 单行 `+12.1% vs MA50 | MA50 below MA200` 同行方向相反
- L4 (deep-dive): mts current_price vs ticker ≥8 cycles 困惑（同名字段不同语义未显式区分）
- R2-8c sectioning landed PR #37（19 工具输出统一 sectioning，agent reasoning 阅读体验改善）

**Red flag**:
- 字段是裸数字无标签
- 输出未显式标注窗口 / 单位 / 时点
- 同 cycle 多个工具给同字段名但语义不同
- 输出依赖纯空格对齐展示数据（LLM 不需要视觉对齐，需要语义标签）

### 原则 8：信任 agent + 工具优先（agent 行为偏差是工具反馈）

**定义**：工具是 agent 路径的第一性来源。Agent 行为不符预期时，反思顺序是：① 工具能力是否够？② 工具描述是否准确？③ 工具默认值是否对齐主流？④ 接口是否闭环？**仅在四层都验证后，才考虑 persona / system prompt nudge**。Prompt nudge 是 last-resort，不是 fix-all 兜底。

**应用规则**:
- 工具优化议题立项时，agent 行为偏差先做"工具反思" checklist（①-④），不直接提 prompt nudge 议题
- W3+ 实测如果验证目标不达标（如 mts 频率 <60%），**第一反应是继续升级工具**（K-line snippet 长度 / alignment 标签 / 默认 tfs / docstring 词汇）而非补 persona nudge
- `persona.py` Layer-1 应保持极简（per memory `project_n7_layer1_organization` — PR #25 已做过 25→5 bullets 的 DRY 反转，工具描述迁 docstring 由 pydantic-ai/griffe sniff 自动传 LLM）
- 工具间 cross-reference 在 wrapper docstring 末尾**等同 nudge**，同样属 last-resort
- 工具 docstring 自身的强度（fact-only / 完整 example / 准确字段标签）> prompt nudge 强度

**证据**:
- memory `project_n7_layer1_organization`（PR #25）已 land 工具描述迁 docstring 的 DRY 反转 — 项目方向一致
- sim #8 实测 agent 心智已成熟（"multi-TF alignment" ≥6 次 / "1m primary / 5m confirmation / 1h context"）— 不需要 nudge 教概念，只需路径反转通过工具自身能力实现
- R2-Next-D 实施反思（2026-05-11）：Cross-ref 嵌入 wrapper docstring 末尾的"Related perception tools"段被识别为 token 冗余 + 心智负担 + 方向存疑（"调 X 时提醒 Y" ≠ "起手时知道用 X"），决定回归"工具能力优先，无 nudge fallback"

**Red flag**:
- 议题描述"agent 应该 X，加 prompt nudge 让它做"——先检查工具是否能让 X 自然发生
- 把 prompt nudge 当工具改造的"廉价替代品"
- 工具能力未充分升级前提 prompt nudge 议题
- 多个 prompt nudge 累积膨胀 system prompt（违反 N7 DRY 反转方向）
- wrapper docstring 末尾出现 "Related tools" / "See also" / "Use this when not X" 类 cross-routing 段

### 元原则：实证优先于直觉

**定义**：设计/优化决策必须看 sim 数据；不要凭"应该这样用"。

**应用规则（议题立项前必查）**:
- args 分布（DB tool_calls.args JSON 提取）
- 调用频率 / 时间分布（per cycle / per session）
- 同 cycle 多调比例
- 失败率 / 失败类型分布
- session log narrative grep（agent 自用词汇 / 显式手算 / 困惑表达）
- 跨 cycle 时序模式

> 实证数据来源：sim DB（tool_calls 表 / cycle 表 / args / status / created_at）+ session log（agent reasoning + tool 输出原文）。两者本地运行生成、gitignored；路径按当前项目惯例查找。

**证据**:
- candle_count=50 default 看似合理，实测主流 20/30
- mts 100% gmd-first 不是直觉能预测的
- 三件套 100% (1m, 5m, 1h) 而非随机组合 → 心智模式存在

**Red flag**:
- 议题描述只有"我觉得 X 应该 Y"，没有 sim 数据引用
- spec 决策依赖"我们认为 agent 应该这样"而非"sim N 实测"
- 优先级判定凭主观感觉

---

## 3. 原则间冲突解决（优先级）

冲突时排序（高 → 低）：

```
1 fact-provider           ← 哲学层不可破（覆盖工具名 + 输出 + docstring 全文）
↓
2 心智对齐                 ← agent 是用户，工具是产品
↓
3 信号唯一来源 / 4 补齐优先 ← 信号设计
↓
5 接口闭环                 ← 接口设计
↓
6 失败语义                 ← 错误处理
↓
7 表达友好                 ← 显示层（字段 / 单位 / 窗口 / 命名）
↓
8 信任 agent + 工具优先     ← 反思方向（行为偏差是工具反馈，非 prompt 失败）
↓
元 实证优先               ← 贯穿所有
```

**典型冲突场景**:
- 心智对齐 vs fact-only：agent 用 "alignment" 但要求加分类标签 → 用 fact-only 边界内分类（MIXED / ALIGNED-UP）通过原则 1
- 接口闭环 vs 信号唯一来源：multi-tf 入参可能造成同信号多源 → 优先信号唯一来源，接口闭环用其他方式（preset / 升级 mts）
- 心智对齐 vs 实证优先：agent 表达想要 X 但实证使用频率低 → 实证优先（narrative 频次必须>3 cycles，否则不是稳定需求）

---

## 4. 议题立项前 checklist

每个工具优化议题在写 spec 前必过此 checklist:

```markdown
- [ ] 原则 1 fact-only：**工具名 + 输出 + docstring 全文**无评价词；执行类无 silent clamp？
- [ ] 原则 2 心智对齐：grep session log，agent 自用词汇与 docstring 对齐？docstring 是完整 call→output 示例非 "X for Y" 指导？反复手算 ≥3 次的事实信号？
- [ ] 原则 3 唯一来源：信号矩阵无重叠？重叠信号有去重方案？
- [ ] 原则 4 补齐优先：现有工具的 underlying data 没被丢弃？新工具有量化依据？
- [ ] 原则 5 接口闭环：DB 查同 cycle 多调比例 < 10%？default 与实测主流偏离 < 2×？
- [ ] 原则 6 失败语义：失败分类清晰，事实状态 idempotent，真异常 reject？
- [ ] 原则 7 表达友好：字段带标签 + 单位 + 窗口；同名字段不同语义显式区分；sectioning？
- [ ] 原则 8 信任 agent + 工具优先：议题反思顺序"工具能力 → 描述 → 默认值 → 接口" 走完才考虑 prompt nudge？无 wrapper 末尾 cross-routing 段？
- [ ] 元 实证优先：sim 数据引用充分（args 分布 + 频率 + 多调 + 失败 + narrative grep）？
```

---

## 5. 维护

- 本文档随 sim 数据迭代演进（每个观察期 sim 后评估是否新增/调整原则）
- 重大调整通过 PR + commit 留 audit trail（不在文档内嵌 changelog）
- 引用本文档的位置：
  - 项目根 `CLAUDE.md`（顶层 anchor，列原则名 + 指向本档）
  - memory `project_tool_design_principles`（自动加载备用索引）
  - 工具相关 spec / brainstorm session 起手必读
- 与 `.working/sim8-w2-multi-tf-deep-dive.md` 配对：本档 = 长期原则；deep-dive = 议题级深度分析
