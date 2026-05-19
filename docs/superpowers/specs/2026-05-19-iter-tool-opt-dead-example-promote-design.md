# iter-tool-opt-dead-example-promote — design spec

## 1. 议题缘起

PR #58（iter-tool-opt-pre-w4-labels）实施 ② R2-Next-G OI delta docstring promo 时实证发现 pydantic-ai 1.78 / griffe 把 google-style 含 section header / admonition 的 docstring 段落从 `tool.tool_def.description` 完全剥离。本项目当前 33 个工具中实证 **7 个工具**有显著 docstring 内容流失，agent / LLM 实际看到的 description 严重短于源 docstring。

### 1.1 上游依据

- pydantic-ai issue #1146（2025-03-22 closed by-design）：维护者 @alexmojaki 确认应 "discourage `Returns:` in the docstring, maybe even with a warning"；推荐 inline 散文形态
- pydantic-ai issue #3122（2026-04-08 closed）：return schema 处理走 opt-in flag 路径，未默认 landed
- PR #58 实施观察实证：griffe 把 `Examples:` / `Example call:` / `Example output:` / 任意 `<词>:` + 缩进内容当 section / admonition 剥离

### 1.2 项目内实证（2026-05-19 全工具 audit）

7 个工具受影响，按 description 损失率排：

| 工具 | 源 doc 长度 | desc 长度 | 损失率 | 主要丢失类型 |
|---|---|---|---|---|
| `set_next_wake_at` | 1298 | 60 | **95.4%** | `Examples:` 块 (16 行 / 4 outcome) + Returns 散文 + Alerts 合约 |
| `set_next_wake` | 726 | 69 | **90.5%** | `Examples:` 块 (8 行 / 3 outcome) + Returns 散文 + Alerts 合约 |
| `get_order_book` | 409 | 174 | **57.5%** | `Degradation:` trailer |
| `get_market_data` | 1676 | 790 | **52.9%** | `Example call:` + `Example output:` 块（Ticker / Candles / Period summary 多 section） |
| `get_higher_timeframe_view` | 2079 | 1106 | **46.8%** | `Example call:` + `Example output:` + `Degradation:` trailer |
| `get_multi_timeframe_snapshot` | 1860 | 1188 | **36.1%** | `Example call:` + `Example output:` + `Degradation:` trailer |
| `get_performance` | 1049 | 896 | **14.6%** | `Degradation:` trailer（注：`Returns:` 块已被 pydantic-ai 包成 `<returns>` XML 进 description） |

**累计丢失 ~3940 字符**高密度 LLM 不可见内容。

### 1.3 W3 attribution 关联

- **`set_next_wake_at`** description 60 chars 对应 W3 sim #10 adoption **2.0%** (3/147) — 是 ③ R2-Next-H W3 设计反转议题的强 attribution 候选
- **`set_next_wake`** description 69 chars 对应 W3 narrative HH:MM UTC 锚从 78% → 4% — 同上 attribution 候选
- **`get_multi_timeframe_snapshot`** description 损失 36% 多 tf 输出形态 — Gate 4 议题（⑤）相关

修法落地后 W4 sim 可观察这 3 处 adoption / narrative 心智指标是否改善，给 ③⑤ 提供干净 attribution 数据。

## 2. 设计决策

### 2.1 修法 path 双轨：A inline vs B description override

PoC（基于 pydantic-ai 1.78 实测）确认 path B 完全可行 — `Tool(fn, description=X)` / `Agent.tool(description=X)` decorator 双形态均接受 raw string passthrough，完全 bypass griffe 解析。Args 仍从 docstring 解析进 `parameters_json_schema`，`require_parameter_descriptions=True` drift guard 仍生效。

按工具丢失结构分配 path：

| Path | 适用 | 工具 |
|---|---|---|
| **A inline narrative**（PR #58 模式） | 单行 trailer 可融入散文 | `get_order_book` / `get_performance` |
| **B description override**（新增） | 多 outcome Examples 块 / 多 section Example output 块 / 多行结构化合约 | `set_next_wake` / `set_next_wake_at` / `get_market_data` / `get_higher_timeframe_view` / `get_multi_timeframe_snapshot` |

不混用同一工具内的 A+B（混用增加维护源 + drift 风险）。

**A 路径形态约束**（与 PR #58 一致）：
- 用 `e.g. \`...\`` / `for example` 把示例融入流式散文
- 反引号 `` `...` `` 标记代码段
- **绝对**不用 `<词>:` + 换行 + 缩进的形态（避免被 griffe 截断）

**B 路径形态约束**：
- DESC 常量是 raw passthrough，可保留 `Examples:` / `Example output:` / multi-line 任意形态
- 不写指导话 / directive verb（"use X for Y"）— 仍守 principle 1 fact-only / principle 8 trust agent
- 仅展示**事实**：input → output / 边界条件 → reject 文案 / 失败降级 → 文案

### 2.2 `tool` wrapper dual-mode 改造

`src/agent/trader.py:80-84` 现有 wrapper：

```python
def tool(func):
    return agent.tool(
        docstring_format="google",
        require_parameter_descriptions=True,
    )(func)
```

改为 dual-mode：

```python
def tool(func=None, *, description=None):
    """Dual-mode wrapper:
       @tool                       — no description override (docstring sniff)
       @tool(description=DESC_X)   — explicit description override (bypass griffe)
    """
    if func is not None and callable(func):
        return agent.tool(
            docstring_format="google",
            require_parameter_descriptions=True,
        )(func)

    def _wrap(f):
        return agent.tool(
            docstring_format="google",
            require_parameter_descriptions=True,
            description=description,
        )(f)
    return _wrap
```

**Backward-compat**：现有 33 处 `@tool`（无参形态）零改动；新增 `@tool(description=DESC_X)` 形态使用 path B。

### 2.3 DESC 常量定位

**选项评估**：

| 选项 | 优点 | 缺点 |
|---|---|---|
| A1: 各 DESC 常量散落在 trader.py 各工具定义上方 | 阅读邻近性高（DESC 紧贴 @tool 装饰器） | 文件膨胀（5 常量 × ~30-50 行 = ~150-250 行） |
| A2: 集中放 trader.py 顶部 module-level | 单一 anchor 位置 | DESC 与 @tool 使用点分离，阅读需跳转 |
| A3: 单独文件 `src/agent/tools_descriptions.py` | trader.py 不膨胀 + DESC 集中 | 增加 1 个文件 + import |

**决议 A3** — 单独文件 `src/agent/tools_descriptions.py`。理由：
- DESC 常量是 LLM-facing 文案，逻辑与代码解耦
- 集中位置便于未来 lint / spell check / drift 集中检测
- trader.py 不膨胀（保持 ~800 行级别可读性）
- 已有惯例参考：`src/agent/tools_perception.py` / `tools_execution.py` 分离

### 2.4 Docstring 清理策略

源 docstring 在 path B 工具中**继续保留 Args 块**（pydantic-ai 仍从这解析参数 schema），但需删除被 DESC override 替代的内容，避免 dead documentation 重复。

**保留**：
- main_desc 第一行（开发者可读的简介；不影响 LLM，但维持 docstring 完整性）
- `Args:` 块（必需 — `parameters_json_schema` 来源）

**删除**：
- `Examples:` 块 / `Example call:` / `Example output:` 块（已经迁到 DESC 常量）
- 后置 trailer 散文（已经迁到 DESC 或不需要）
- `Returns:` 块（path B 工具不需要 — 整段已在 DESC 中重写）

Path A 工具继续单源 docstring（trailer 已融入 description body），完全不写 Examples / Example call/output 块。

### 2.5 Drift guard 测试

每工具 1 个 drift guard test，集中在 `tests/test_trader_agent.py`（沿用 PR #58 模式）：

```python
def test_<tool_name>_description_includes_<key_content>():
    """W3 R2-Next-G / R2-Next-H attribution lever — drift guard ensures
    <tool> LLM-visible description carries <key fact list>. Inline narrative
    (path A) or description override (path B) per spec §2.1."""
    agent = create_trader_agent(...)
    tool = agent._function_toolset.tools["<name>"]
    desc = tool.tool_def.description
    assert "<literal 1>" in desc, f"<reason>: {desc!r}"
    assert "<literal 2>" in desc, f"<reason>: {desc!r}"
    # ...
```

断言粒度：
- Path A 工具：1-2 个关键散文 literal（e.g. `"insufficient data"` / `"temporarily unavailable"`）
- Path B 工具：3-5 个关键 literal（Examples 块标志 + 至少 2 个 outcome 文案 + Args 不退化）

外加 1 个 module-level audit test 锁住"无新增 dead block-style admonition 回归"。**检测策略：source-vs-desc 差分**（非 regex pattern-guessing）：

```python
def test_no_block_admonition_lost_to_griffe_stripping():
    """Drift guard: detects when a block-style `<Word>:\\n<indent>`
    admonition in a non-PATH_B wrapper's source docstring fails to
    reach `tool.tool_def.description`. Differential mechanism catches
    exactly what griffe actually strips on the current version."""
    # For each tool (non-PATH_B):
    #   - find block-style admonitions in source (line `<Word>:` + indented next line)
    #   - skip handled headers (Args/Returns/Yields)
    #   - if header literal absent from tool_def.description → offender
```

**为什么用差分检测而非 regex 黑名单**：实证（plan review 2026-05-19）发现 `cancel_price_level_alert` 源 docstring 含 `Note: alerts at SL/TP...` 但是 **inline same-line `<Word>: <prose>` 形态**（不是块结构），griffe **不**剥离，description 完整含 `Note:` 字面。Regex-only `^Note:|^Warning:|...` 黑名单会 false-positive 此类合法用法。差分检测只 flag 实际被剥离的 header — 与 griffe 行为同步，未来 griffe 修复 dead admonition 时测试也自动适应。

**griffe 实际剥离触发条件**：行尾 `<词>:` + 立即跟随缩进续行块。Inline `Note: <prose>` 同行连写 → griffe 当散文保留。

### 2.6 Args 描述完整性 — 副议题

`set_next_wake_at` Args 内的 `target_time` 描述含详细 resolution 语义（"Resolves to the nearest future time matching HH:MM (today if HH:MM is still ahead in UTC; otherwise tomorrow)"）— pydantic-ai 把 Args 进 `parameters_json_schema[properties][target_time][description]`，**未流失**。本 iter 不动 Args 描述。

### 2.7 Out-of-scope（明确拒绝）

- 改 description override 形式以"改善" agent 行为（principle 1 fact-provider 严格执行；DESC 只展示 fact，不嵌入 directive）
- 修 griffe 上游 bug（不在本项目可控范围）
- 重新评估 N7 PR #25 当时的 dry-run 决策（议题已闭，本 iter 是补漏不是重审）
- ④ trade discipline brainstorm 主线议题（独立 brainstorm 议题，本 iter 不动）
- 7 个工具之外的 26 个工具的描述形态优化（本 iter 仅修实证 dead documentation 的 7 处）

### 2.8 W4 验收回路

PR merge 后 W4 sim 跑完观察：

| 指标 | 基线（W3 sim #10） | W4 目标 | 解释 |
|---|---|---|---|
| `set_next_wake_at` adoption | 2.0% (3/147) | ≥ 15% (任何明显改善) | ③ R2-Next-H attribution 隔离 |
| `set_next_wake` reasoning 含 HH:MM UTC | 4% | ≥ 20% | narrative 心智回归测试 |
| `oi_delta_ref_rate`（PR #58 ②） | 39.1% | ≥ 60%（原 PR #58 ② 目标） | 复用 PR #58 验证（不耦合本 iter） |
| Gate 4 MTS structure terms 引用率 | 71.4% (W3) | 不退化 | MTS desc 改造副作用监控 |

W4 数据后续触发 ③⑤ brainstorm 决议路径选择（D1 wontfix-by-design / D2 W4 延期 / 其他）。

## 3. 议题外但已注意

### 3.1 现有 `Returns:` 块的处理

`get_performance` 的 `Returns:` 块被 pydantic-ai 包成 `<returns>` XML 进 description（PoC 实证）— 这是 pydantic-ai 的 design-by-intent 行为（issue #1146 后续）。本 iter 不动 `get_performance` 的 `Returns:` 块，仅修其 `Degradation:` trailer。

其他 6 个工具均无 `Returns:` 块（不需修 — pydantic-ai design 是希望开发者把 returns 信息融入 description 散文，参 @alexmojaki 路线）。

### 3.2 PR #58 已用 path A 的 `get_derivatives_data`

不重做。`get_derivatives_data` 已 ship inline narrative，drift guard 已落，本 iter 不动。

## 4. 风险与回退

| 风险 | 缓解 |
|---|---|
| DESC 常量与 docstring Args 描述出现 drift（开发者改 docstring 但忘改 DESC） | drift guard test + `tools_descriptions.py` 集中位置便于 review |
| 修法 B 5 个工具 description 字节增 → LLM token cost 增 | 每工具增 300-1200 chars × 5 ≈ 3-6K token；占 8K context window 的 ~5%；可接受 |
| W4 验证指标无改善 → 议题修法失败 | path A 工具（2 个）回退成本极低；path B 工具回退 = 删 description override；DESC 常量保留作开发者文档 |
| `tools_descriptions.py` 新文件破坏现有 module 导入约定 | 检查 `src/agent/__init__.py` 不需 re-export；trader.py 单点 `from src.agent.tools_descriptions import ...` |

## 5. 关联

- 主依据：PR #58 (squash `383cd0b`, 2026-05-19) 实施观察
- pydantic-ai issue #1146 closed by-design + #3122 closed opt-in path
- memory `[[griffe-example-section-stripped]]`（本 iter 后更新为 ✅ landed）
- memory `[[pre-w4-followups]]` ② 已 ✅ landed；本 iter 不在 7 项 backlog 内，是 PR #58 派生的独立 mini-iter
- 工具设计原则 `docs/superpowers/principles/tool-design-principles.md` principle 1 / 4 / 8
- N7 PR #25 当时假设 "工具描述迁 docstring 由 pydantic-ai/griffe sniff 自动传 LLM" — 本 iter 是该假设 dead-Example 漏网的补漏
