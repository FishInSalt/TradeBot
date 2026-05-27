# Readability Checks

判断 agent **一眼能不能读懂**这个工具。三条主线：docstring（agent 调用前看的）/ 渲染输出（agent 读到的）/ 命名（工具名 + 字段名）。

## 1. docstring 三通道（pydantic-ai + griffe 实测）

memory `project_griffe_example_stripped` 实证：pydantic-ai 1.78 + griffe 把 docstring **不同段落**喂到 LLM 的不同通道，会被强删的 block 等于**没给 LLM 看**。

- **Channel 1 — pre-Args 段（成为 `<summary>`）**：docstring 顶部、直到第一个 `Args:` 之前的散文 + inline `<word>: <prose>` 同行 admonition 都 survives。块状 admonition（`Example call:\n    <indent>`、`Example output:\n    <indent>`、`Degradation:\n    <indent>` 等）会被 griffe 整段剥掉。
- **Channel 2 — `Args:` 段（成为 `parameters_json_schema`）**：每个参数描述 survives 进 schema。
- **Channel 3 — `Returns:` 段（成为 `<returns>` XML）**：整段 raw verbatim 注入，**内部不被 sub-parse**——是放结构化输出说明 / Example output / 单位说明的安全区。

### Checklist

- [ ] docstring 顶部散文有没有"何时调用 / 应当用于 X"这种 anti-pattern？（违反原则 1）
- [ ] 有没有 `Example call:` / `Example output:` 这类 block？如果在 pre-Args 段 → 被 griffe 强删，**dead code**。该挪到 `Returns:` 块。
- [ ] `Args:` 里每个参数有没有：单位 / 范围 / 默认值的语义 / 越界行为？
- [ ] `Returns:` 块在不在？输出的字段语义 / 单位 / 窗口写清楚了吗？
- [ ] docstring 出现 `should` / `appropriate` / `good` / `best` / `X for Y` / `Use when ...` → 违反原则 1（fact-provider 不是 guard）

### grep 起步

```bash
# 从源码抓完整 docstring
sed -n '<def 行>,<def 行+60>p' src/agent/tools_perception.py

# 检查 dead admonition
grep -n "Example call:\|Example output:\|Degradation:\|Note:" <docstring 范围>
```

## 2. 渲染输出（agent 真正看到的）

session log 是渲染层 + agent context 的并集。看渲染样本即可。

### Checklist

- [ ] 顶部有没有 `=== <Section> (<context>) ===` 标题？memory `project_r2_8c_tool_output_optimization` 之后 19 工具都加了 sectioning，缺的就是 anti-pattern
- [ ] 数字字段是不是都带**标签 + 单位 + 窗口**？（原则 7）
  - 好：`ATR(14): 177.72 (0.23% of price, 15m candles)`（label / 周期 / 单位 / 含义）
  - 差：`ATR: 177.72`（孤值，agent 得猜窗口）
- [ ] **同名字段不同语义**有没有显式区分？（原则 7）—— 经典坑：`pnl`（gross vs net）/ `vol`（last-bar vs SMA20 avg）/ `range pos`（5m vs 4h）
- [ ] 长 list / multi-tf / candle 行有没有被 D4 row-clip 砍掉关键信息？看是否 `[... N rows omitted ...]` 出现在重要数据中间
- [ ] 错误状态：成功 / 失败 / "无变化" 的输出区分清楚吗？（原则 6 失败语义）

### grep 起步

```bash
# 抽 3 个不同 args 的样本
grep -n "⚙ <tool_name>(" <session.log> | shuf -n 3

# 看一次完整调用：从匹配行到下一个 ⚙ / ▾ Reasoning / ▾ Decision
awk '/⚙ <tool_name>\(/,/^(  ⚙|▾)/' <session.log> | head -80
```

## 3. 命名（工具名 + 参数名 + 字段名）

### Checklist

- [ ] 工具名包含评价词或暗示用途？（原则 1）—— `get_critical_alerts` / `get_best_X` / `get_recommended_Y` 都是 anti-pattern
- [ ] 工具名陈述 *what* 还是规定 *how* 用？陈述型 OK：`get_market_data` / `get_position`；规定型差：`check_if_should_enter`
- [ ] 参数名是否需要 agent **手动构造**才能 idiomatic 调用？（原则 5 接口闭环）—— 高频被默认 / 高频被相同非默认值组合都是设计缺口
- [ ] 输出字段名是不是 agent 的 native 词汇？（原则 2 心智路径）—— grep session log 看 agent 自用什么词，反观字段是否对齐

## 4. Adoption 反向信号

读完输出层后回看 reasoning：

- 如果 agent 经常在调用后**重复定义**某个字段（"the BB position 59% means price is in upper half"），说明字段语义不够自解释——可读性可改进
- 如果 agent **手动重算**了输出里已经给的派生量，说明渲染层埋得太深或字段名容易被忽略
- 如果 agent **完全不提**某些字段（n 次调用都没引用），说明字段对 agent 没价值——是 dead column

详细 grep 模板见 `adoption-checks.md`。
