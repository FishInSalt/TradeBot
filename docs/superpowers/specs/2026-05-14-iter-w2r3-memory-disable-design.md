# Iter w2r3-memory-disable — disable memory tool + prompt injection for W3 clean baseline

**Date**: 2026-05-14
**Iteration**: iter-w2r3-memory-disable (W2 → W3 过渡，建立 W3 单注入 baseline)
**Type**: Design spec (wiring removal — 0 storage change / 0 schema change)
**Source brainstorm**: 2026-05-14 session （sim #8 实证数据驱动）
**Upstream memory refs**:
- `project_agent_reflection_tools_candidate` — 议题源头，断言 reframe 应数据驱动
- `project_w2_observation_inventory_kickoff` — W2 inventory 已记录写读不对称 (39:1)
- `project_iter_w2r2_next_e_followups` — cross-period attribution shift caveat
- `feedback_observation_period_soft_constraint` — 观察期工具改动哲学
**Related principles**: `docs/superpowers/principles/tool-design-principles.md` — 元原则（实证优先）+ 4（信号补齐 vs 新工具）+ 8（信任 agent + 工具优先）

---

## 0. One-minute summary

W2 sim #8 实证数据显示 memory 工具对 agent 决策的净正向影响**未达 ROI 门槛**：178 cycles 内 39 次 `save_memory` 写入 + 仅 1 次 `get_memories` 主动读取（39:1 不对称），而 prompt 自动注入 top-10 memories（每 cycle ~10K 字符）在 113/178 cycles (64%) 持续传递 "spring zone" 单一 pattern 进入 reasoning，并在 7 个 cycles 持续传递错误事实（"4h MA50 at 81,147"，实际为 78,621）。同期净 PnL −0.81% / 6W-9L。R2-8b Recent Cycle Summaries 注入（PR #38, 2026-05-06）已在 sim #8 期间 active，覆盖"近期决策上下文"用途。

本 iter **不重构 memory，不实现 3-tier playbook**，而是**暂时禁用 memory wiring**，建立 W3 sim 的"仅 Recent Cycle Summaries 单注入" baseline，用于隔离判定 memory 通道的净 ROI。改动局限于 wiring 层（tool register / prompt inject / persona 引用句），**storage 层 0 改动**（`MemoryService` 类 + `memory_entries` 表 + 历史 entries + 索引全部保留），保证可逆。

W3 sim 跑出来后基于双 baseline A/B 数据决定：(a) 永久退役 memory / (b) 按 `agent_reflection_tools_candidate` reframe 启动 3-tier 设计 / (c) 再观察一轮。

`REGISTERED_TOOL_NAMES` 从 34 → 32（移除 `save_memory` + `get_memories`）。Recent Cycle Summaries 注入不动。`memory_entries` 表与历史数据保留为 forensic 资源。

---

## 1. Empirical foundations

### 1.1 sim #8 实测数据（W2 observation baseline）

| 维度 | 值 |
|---|---|
| Session | `8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3` |
| 时间范围 | 2026-05-06 08:59 → 2026-05-07 04:09 UTC（19.2h） |
| 总 cycles | 178 |
| `save_memory` 调用 | 39（36 cycles，20.2% cycles 命中） |
| `get_memories` 主动调用 | **1**（仅 cycle `0b62273e`，09:32 UTC 开局阶段） |
| memory 自动注入 cycles | 178（100%）|
| 写读比 | **39:1**（不计自动注入） |
| memory_entries 分类 | 1 lesson / 18 market_pattern / 20 trade_review |
| importance 分布 | 0.6: 2 / 0.7: 8 / 0.8: 18 / 0.85: 2 / 0.9: 9（87% 在 0.7–0.9 区间，字段事实退化为按写入顺序）<br>raw query: `SELECT relevance_score, COUNT(*) FROM memory_entries WHERE session_id='8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3' AND memory_type='long_term' GROUP BY relevance_score` |
| 内容长度 | min 470 / avg 1058 / max 1717 字符 |
| 每 cycle 注入开销 | top-10 × ~1058 字符 ≈ **10K 字符 / cycle** |
| 累计渲染 | 178 × 10K ≈ 1.78M 字符 |
| 净 PnL | **−81.10 USDT (−0.81%)** / 15 closed trades / 6W-9L |
| Reasoning 长度增长（quarter 0→3） | 11.3K → 13.2K 字符（+17%，慢增长） |

### 1.2 Confirmation bias / memory poisoning 证据

| 信号 | 命中 cycles | 占比 / 备注 |
|---|---|---|
| reasoning 含 "memor / lesson / prior / previously" 词根 | 139 | 78% — memory 确被 reasoning 引用 |
| reasoning 显式 "my memory" / "memory shows" | 31 | 17% — self-correction 用法存在 |
| "spring" 词根 | 113 | 64% — 单一 pattern 反复自我强化（7 条 spring memory 注入，5 次 spring-related long 全失败） |
| "81,147"（错误的 4h MA50 数字） | 7 | 错误事实跨 7 cycles 持续传递；后写 1 条 lesson 修正 (`memory_entries.id=65`, importance=0.8, content 含 "4h MA50 correction: across multiple cycles (6d7f6439, 76b1dded, db234f44), I cited '4h MA50 at 81,147' as a near-term support..."); 该 lesson entry 是 agent **自己** 在 importance 排序 top-10 中再次注入回 prompt 的纠错条目，但未提供证据本 entry 在 81,147 错误流行的 7 cycles **之前** 即在 top-10 注入 — 因此 confirmation bias 归因链 = "agent 在 trade_review 写入时引用错误数字 → top-10 注入持续放大 → 末期 agent 自纠"。原始错误源头需进一步 forensic 分析（含早期 trade_review entries 是否含 81,147，本 spec 不深入）|
| "78,621"（实际 4h MA50） | 4 | self-correction 仅覆盖 4 cycles |

**归因谨慎性**: "spring" 113 cycles 引用**不完全归因于 memory 通道** — Recent Cycle Summaries（N=3 prior summaries，每条 cap `CYCLE_DECISION_WORD_CAP=700` words / `CYCLE_DECISION_CHAR_HARD_FLOOR=8000` chars 在 closing summary 写入时执行，注入时不再 cap，见 `src/agent/persona.py:11,22` + `src/cli/app.py:118-119`；原 R2-8b 提交描述的"600/800/1200 三层 cap"在 R2-Next-A / PR #40 F1 length feedback loop closure 后已校准为当前单 word + 单 char 形态）也可能传递。"81,147" 错误事实更可能来自 memory（importance 排序持续注入，N=3 summaries 自然滚出）。本 iter 目的是**通过禁用建立干净 A/B 基线**，不是已证伪 memory 是 confirmation bias 唯一来源。

### 1.3 sim #8 期间 wiring 现状（核实）

| commit | 时间 | 状态 |
|---|---|---|
| R2-8b PR #38 (`28f7265`) — Recent Cycle Summaries 注入 | 2026-05-06 05:21 UTC | sim #8 开始前 ~3.5h |
| P4 user_prompt_snapshot (`0d35051`) | 2026-05-10 10:42 UTC | sim #8 之后 |
| sim #8 session | 2026-05-06 08:59 → 2026-05-07 04:09 UTC | — |

→ sim #8 baseline = **memory 注入 + Recent Cycle Summaries 注入**（双注入）。W3 禁用 memory 后 baseline = **仅 Recent Cycle Summaries**（单注入）。

### 1.4 Memory `project_agent_reflection_tools_candidate` 断言验证

议题（2026-04-26 写）核心断言与 sim #8 数据吻合度：

| 断言 | sim #8 验证 |
|---|---|
| 写读不对称 | ✅ 实测 39:1 |
| LLM 不真正"学习"，"memory" 只是反复 prompt 内文本 | ✅ 100% cycles 自动注入推动引用，agent 主动调用近 0 |
| importance 字段无信号 | ✅ 84% 堆挤 0.7–0.9 |
| "lesson" 抽象成本高 | ✅ 仅 1/39 写入 lesson |
| 经验工具按直觉做是陷阱 | ✅ confirmation bias 实测证据（spring zone 5-for-5 / 81,147 错误持续 7 cycles） |

该议题 §6 明确"不实现 reframe，等观察期数据触发"。sim #8 数据现在提供决策依据 — 但依据**指向先证伪当前 memory 是否有正 ROI**，而**非直接 reframe**。

---

## 2. Industry-standard reference

LLM agent 系统中 "memory" 设计的主流取向：

| 系统 | 处理方式 | 启示 |
|---|---|---|
| ChatGPT memory | 用户可见 + 显式 opt-in/out + 用户可编辑/删 | 用户主导，非 agent 自治 |
| Claude Projects | 项目级 stable instructions（系统级，非 agent 写） | 静态规则注入，不是动态经验积累 |
| Anthropic Computer Use | 无持续 memory，每次 task 独立 | 极简，单 task 内 reasoning chain 即上下文 |
| 学术 reflection agents (Reflexion, Voyager) | 多用 episode buffer + skill library，**离线 distill** | 在线"自由形式 save"是反例，主流是结构化 + 异步抽象 |

**本 iter 的对齐**: 朝"单 task / 内 reasoning chain 即上下文"方向收敛（Recent Cycle Summaries 即扮演此角色）。如 W3 数据显示需要持续经验沉淀，再按 reflection agents 主流（episode buffer + 异步 distill）重设计。

---

## 3. Design

### 3.1 改动清单（精确到 file:line）

**Tool register 层**:

| 文件 | 改动 | 位置参考 |
|---|---|---|
| `src/agent/trader.py` | 移除 `@tool async def get_memories` 整个装饰函数 | line 178–186 |
| `src/agent/trader.py` | 移除 `@tool async def save_memory` 整个装饰函数 + 注释 `# === Memory Tools ===` 段头 | line 726–746 |
| `src/agent/trader.py` | `REGISTERED_TOOL_NAMES` 列表移除 `"get_memories"` 与 `"save_memory"`，更新段头注释 `--- 感知 (20) ---` → `--- 感知 (19) ---`，删除 `--- memory (1) ---` 段头 | line 755–793 |
| `src/agent/trader.py` | `REGISTERED_TOOL_NAMES` 上方注释 "（感知 → 执行 → memory）" 同步删除 "→ memory" | line 751 |
| `src/agent/trader.py` | `from src.agent.memory import MemoryService` import | line 10 — **不删**（`MemoryService` typing 字段保留，所有持有 `MemoryService` 引用必须能 import） |
| `src/agent/trader.py` | `TradingDeps.memory: MemoryService` typing 字段 | line 31 — **不删**（同上）|

**Prompt injection 层**:

| 文件 | 改动 | 位置参考 |
|---|---|---|
| `src/cli/app.py` | 移除 `memory_context = await deps.memory.format_for_prompt()` 与紧随的 `if memory_context != "No relevant memories.": prompt += f"\n\nYour memories:\n{memory_context}"` 块（整块删，3 行）| line 504–506 |
| `src/cli/app.py` | 上方注释 "(D-D-E injection position: trigger context → recent → memory)" 同步删除尾部 "→ memory" | line 495 |

**Persona 层**:

| 文件 | 改动 | 位置参考 |
|---|---|---|
| `src/agent/persona.py` | "Close fill response" 句末 "Save actionable lessons to memory." 删除（保留前半句） | 约 89 行 |
| `src/agent/persona.py` | "Self-Review" 段 "Are there relevant lessons in your memory?" 整句删除（保留前后句） | 约 135 行（在 `_build_layer2()` 内 — 见 §5 关于 Layer 2 改动边界的澄清） |

**display.py 集合层**（T-DG-2 一致性要求 — dispatch 集合必须与 `REGISTERED_TOOL_NAMES` 等值；dead summarizer 函数与 dict key 仍保留为 dead refs，归 §5 follow-up cleanup）:

| 文件 | 改动 | 位置参考 |
|---|---|---|
| `src/cli/display.py` | `_PERCEPTION_TOOL_NAMES` 集合移除 `"get_memories"` | 约 502 行 |
| `src/cli/display.py` | `_SECTIONED_PERCEPTION_TOOL_NAMES` 派生表达式（`_PERCEPTION_TOOL_NAMES - frozenset({"get_memories"})`）调整为不再 `- frozenset({"get_memories"})`（因为已从父集合中移除） | 约 514 行 |
| `src/cli/display.py` | 注释 "get_memories 是 backend-dependent format 例外（spec §4.2.13 / §8.8）" 同步删除或改为"已下线（iter-w2r3-memory-disable）" | 约 516 行 |
| `src/cli/display.py` | `save_memory` dispatch branch (line 549-551 + 802 docstring + 831-832 branch 3 + 855 注释 + 863 异常字符串 + 502 dispatch loop 上下文) — **保留 branch 但不再有调用路径**；dispatch 入口处加 `# Retired tool: iter-w2r3-memory-disable — branch kept for revert path` 注释 | 多处 |
| `src/cli/display.py` | `_SYSTEM_LOG_PERCEPTION_PARSERS` dict (line 349-358) 含 `"get_memories": _summarize_get_memories` key — **不删**。实际有运行时 get 路径（display.py:996 `_SYSTEM_LOG_PERCEPTION_PARSERS.get(tool_name)`），但 `tool_name='get_memories'` 移除后永不触发，vacuously not-find；与 dead summarizer 函数同等待遇，列入 §5 follow-up cleanup | line 355 |
| `src/cli/display.py` | `_summarize_get_memories` / `summarize_save_memory` 函数 | **不删**（dead summarizer，cleanup 归入 §5 follow-up） |

**File-level**:

| 文件 | 处置 |
|---|---|
| `src/agent/tools_memory.py` | **不删**，保留全文件 |
| `src/agent/memory.py` (`MemoryService` 类) | **不删**，保留全文件（`deps.memory` 仍存在，只是无人调用） |
| `src/agent/tools_perception.py` 中的 `get_memories` 函数 | **不删**，保留（也无人调用）|
| `src/cli/app.py:15` import `from src.agent.memory import MemoryService` + `:825` `memory = MemoryService(engine, session_id=session_id)` 创建 + `:891` `deps = TradingDeps(` 调用入口 + `:897` `memory=memory` keyword arg | **不删**，保留 — **保留理由**: 单一可逆 commit revert 路径；`deps.memory` 字段为已注入未读对象，运行时 0 cost；若 None 化需要修改 `TradingDeps` typing 与所有 `deps.memory.X` 残留引用，反 revert 成本远高于保留 |
| `src/cli/display.py` 中 `_summarize_get_memories` / `summarize_save_memory` 函数体 | **不删**，无调用路径但保留便于 revert（集合移除已在上一组完成，函数为 dead code）|

**保留即可逆**: 如 W3 决定重启 memory，本 iter 的全部移除可通过 git revert 单 commit 完整还原。display.py 三层集合的同步移除是 T-DG-2 一致性要求（dispatch sets 与 REGISTERED_TOOL_NAMES 必须等值，否则 drift guard 必失败）— 也在 revert 一并恢复。

### 3.2 测试改动

按以下清单**逐文件处置**:

| 测试文件 | 改动 | 必要性 |
|---|---|---|
| `tests/test_trader_agent.py::test_trader_agent_has_all_tools` (line 27 + line 35) | **必改** — 删除 `assert "get_memories" in tool_names`（line 27）和 `assert "save_memory" in tool_names`（line 35）+ 删除 line 34 注释 "# 记忆类" | 不改必硬失败 |
| `tests/test_trader_agent.py::test_registered_tool_names_matches_agent_tools` | 已有 drift guard，自动随 `REGISTERED_TOOL_NAMES` 常量同步通过 | 无需手改 |
| `tests/test_trader_agent.py` 硬编码 count 断言 (line 85-86) | **必改** — `assert len(REGISTERED_TOOL_NAMES) == 34` → `== 32` + `f"Expected 34 tools (20+13+1), got ..."` → `f"Expected 32 tools (19+13), got ..."` | 不改必硬失败 |
| `tests/test_display_cycle.py::test_dg_2_dispatch_sets_partition_all_registered_tools` (function body line 1471–1511) | **必改** — 综合处置:<br>(1) docstring 改 "34 registered tools" → "32 registered tools" + "Spec §4.4: ... ∪ {save_memory} 必须等于 ... (34)" → "_PERCEPTION_TOOL_NAMES (19) ∪ _EXECUTION_TOOL_NAMES (13) = REGISTERED_TOOL_NAMES (32)" + 删 "_SECTIONED_PERCEPTION_TOOL_NAMES (19) ⊂ _PERCEPTION_TOOL_NAMES（仅 get_memories 例外）" 整行<br>(2) line 1492 `assert perception - sectioned == frozenset({"get_memories"})` → 删除（移除 get_memories 后 perception 与 sectioned 相等，该断言无意义）<br>(3) 局部变量 `save = frozenset({"save_memory"})` 删除 + 互斥断言相关 `perception.isdisjoint(save)` / `execution.isdisjoint(save)` 删除 + `union = perception \| execution \| save` → `union = perception \| execution`<br>(4) line 1509 `assert len(perception) == 20` → `== 19`<br>(5) line 1510 `assert len(sectioned) == 19` 数值不变但语义脱钩（sectioned 现 = perception），docstring 同步删该断言注释 | 不改必硬失败 |
| `tests/test_agent_cycle_injection.py::test_injection_appears_before_memory_context` (line 168-192) + module docstring (line 7) | **必改** — 该用例改为 `assert "Your memories:" not in prompt`（即 spec §3.2 drift guard 的反向断言），不再 `prompt.index("Your memories:")`；module docstring "BEFORE memory_context" 同步删除/改写。或彻底删除该用例（迁移到新 drift guard 文件） | 不改 `str.index` 必抛 ValueError |
| `tests/test_memory.py` | **不动** — 测 `MemoryService` 单元行为，spec 保留 service 类，自然通过 | — |
| `tests/test_tools.py::test_get_memories` (line 126) | **不动** — 测 `tools_perception.get_memories` 函数，spec 保留函数，自然通过 | — |
| `tests/test_fact_only_wordlist.py` (line 447, 764) | **不动** — fact-only 词表测试，保留导入路径，自然通过 | — |
| `tests/test_persona.py::test_prompt_contains_memory_quality_guidance` (line 48–53) | **必改 (语义)** — 删完 L28 bullet "Save actionable lessons to memory." 后断言 `assert "actionable" in prompt_lower` 仍会通过（碰撞到 persona.py:112 "nothing actionable happened" 的 hard-truncate 文案），但 docstring 守护对象已不在 prompt 中，语义脱钩。**处置**: 删除整个 `test_prompt_contains_memory_quality_guidance` 用例 + 把其语义反转后合并到新增的 `tests/test_iter_w2r3_memory_disabled.py` 的 §3.2 (c) drift guard 中（已覆盖 `Save actionable lessons` 不含断言）— 避免函数名与新语义脱钩 | 守护对象脱钩 |
| `tests/test_persona.py::test_prompt_contains_fill_response_guidance` (line 33 注释) | **可改** — 注释 `# Close fill: review outcome, save memory` 同步改为 `# Close fill: review outcome`（注释类，不影响通过）| 注释同步 |

**新增** `tests/test_iter_w2r3_memory_disabled.py`（drift guard）:

(a) 断言 `save_memory` / `get_memories` 不在 `agent._function_toolset.tools` 中
(b) 断言 `run_agent_cycle` 拼出的 user_prompt 不含 `"Your memories:"` **且** 不含 `"=== Long-term Memory ==="` **且** 不含 `"=== Recent Context ==="` — 三字符串联合断言：第一个抓 app.py wiring 回归，后两个抓 `MemoryService.format_for_prompt` 输出回归（`src/agent/memory.py:91,97`）
(c) 断言 `generate_system_prompt(PersonaConfig())` 完整输出不含 `Save actionable lessons to memory` 与 `lessons in your memory` 正则（范围 = 整 system prompt，跨 Layer 1/2/3 一并覆盖；`generate_system_prompt` 定义于 `src/agent/persona.py:56`）
(d) 断言 `MemoryService` 类与 `memory_entries` 表仍存在（防止下游误删 storage 层）

**Dead fixture mocks**（归入 §5 follow-up，本 iter 不动；当前实测 13 处分布 11 个文件，含两种形态 `memory=AsyncMock(format_for_prompt=...)` 与 `deps.memory.format_for_prompt = AsyncMock(...)`）:
- `tests/conftest.py:155` — `memory=MagicMock(format_for_prompt=...)`
- `tests/test_agent_cycle_injection.py:55` — `memory=AsyncMock(format_for_prompt=...)`
- `tests/test_agent_cycle_injection.py:174` — `deps.memory = AsyncMock(format_for_prompt=...)` (T4.3 用例，§3.2 上方已列必改)
- `tests/test_cycle_log.py:58` — `memory=AsyncMock(format_for_prompt=...)`
- `tests/test_cycle_summary_injection.py:811` — `deps.memory.format_for_prompt = AsyncMock(...)`
- `tests/test_cycle_summary_injection.py:891` — `deps.memory.format_for_prompt = AsyncMock(...)`
- `tests/test_p4_cycle_capture.py:67` — `memory=AsyncMock(format_for_prompt=...)`
- `tests/test_tool_call_instrumentation.py:66` — `memory=AsyncMock()`
- `tests/test_tool_call_recorder.py:27` — `memory=AsyncMock()`
- `tests/test_tool_enhancement.py:256, 299` — `memory=AsyncMock()` (两处)
- `tests/test_tools.py:59` — `memory=AsyncMock()`
- `tests/test_trader_agent.py:62` — `memory=AsyncMock()`
- `tests/test_usage_limits.py:74` — `memory=AsyncMock(format_for_prompt=...)`

注入移除后这些 mock 永不被调用（除 §3.2 上方已列入必改的 `test_agent_cycle_injection.py:174` T4.3 用例），AsyncMock 包容性允许它们继续通过 — dead code。follow-up cleanup 时按两种形态 grep 重盘点。

注：`tests/test_memory.py:40` **不是 dead** — 它测 `MemoryService.format_for_prompt` 单元行为，spec 保留 service 类，自然通过。

### 3.3 行为可观察的变化（定性预期）

**口径声明**: 本节表是**定性预期**（W3 sim 前的方向性 hypothesis），与 §4.2 / §4.3 的**定量硬阈值**（W3 sim 完成后的判定门槛）分属不同层。本节用于实施期 sanity check，§4.2 / §4.3 用于 W3 sim 完成后判定。

| 维度 | 预期变化 |
|---|---|
| 每 cycle prompt 字符数 | ~10K 减少（top-10 memory 注入消失） |
| input tokens / cycle | ~2.5K 减少（10K char ≈ 2.5K tokens） |
| agent 工具数 | 34 → 32（感知 20 → 19 + 执行 13 + memory 1 → 0） |
| `REGISTERED_TOOL_NAMES` 长度 | 34 → 32 |
| cycle reasoning 中 "memor / lesson / my memory" 引用 | 预期**显著降**（不归零 — persona.py:110 "Watch list" field 5 仍保留 "lessons from this cycle" 字串引导，agent 写 Closing Summary 时仍可能命中 "lesson" 词根；W3 评估时此噪声需扣除） |
| 新 `memory_entries` rows | 不再产生 |
| `tool_calls` 表中 `tool_name='save_memory' / 'get_memories'` | W3 期间 0 条新增 |

---

## 4. W3 评估方法

### 4.1 双 baseline A/B 框架

| baseline | 注入通道 | 数据来源 |
|---|---|---|
| W2（双注入） | memory + Recent Cycle Summaries | sim #8（已存） |
| W3（单注入） | 仅 Recent Cycle Summaries | W3 sim（待跑） |

W3 sim 期望条件：
- 体量 ≥ sim #8（≥ 150 cycles + ≥ 15h），保证统计可比
- 模型 + 启动资金 + 交易对 与 sim #8 一致
- prompt template 除 memory 注入外其他不变

### 4.2 评估指标

| 指标 | sim #8 (W2) | W3 目标 |
|---|---|---|
| 净 PnL | −0.81% / 19h | 同等或更优（弱主指标，sample 小 noisy）|
| Reasoning length avg | 11.3K → 13.2K 字符（quarter 0→3） | 整体降 + 增长斜率降 |
| Input tokens / cycle avg | (P4 字段 sim #8 缺失，W3 可用) | 预期降 ~2K-3K |
| "重复错误事实"持续 cycles | "81,147" 7 cycles | 显著降（W3 不应出现 ≥ 5 cycles 同错误数字持续） |
| "单一 pattern" reasoning 占比 | "spring" 113 / 178 = 64% | 看 Recent Cycle Summaries 单独是否仍产生类似自回归（如仍 ≥ 50% 则证明 memory 不是主因） |
| trade W/L | 6W-9L | 不显著退化（≥ 5W-10L 或更优） |
| 重复 reasoning 模式 grep | 多场景手算 | 不显著新增（Recent Cycle Summaries 应覆盖近期上下文需求） |
| **W3 跨期 grep anchor**（baseline 复现）| W2 sim #8 实测：`SELECT COUNT(*) FROM agent_cycles WHERE session_id='8f2ca0cb-1c2a-4dc7-8927-198e6c0d98d3' AND reasoning LIKE '%spring%'` = **113** / `LIKE '%81,147%'` = **7** / `LIKE '%my memory%'` = **31** / `LIKE '%memor%' OR LIKE '%lesson%' OR LIKE '%prior%' OR LIKE '%previously%'` = **139** | W3 期望: spring < 70 (60%降) / 81,147 类持续错误事实 = 0 / "my memory" = 0 / 总词根降 ≥ 30%（绝对值因 Recent Cycle Summaries 仍会带 "lessons from this cycle" 等噪声不归零）|

### 4.3 判定准则（草案，W3 sim 完成后可基于实测调整）

| 情形 | 判定 |
|---|---|
| W3 同等/更好 PnL + token 下降 ≥ 2K + 重复错误事实显著降 | **永久退役 memory**（移除保留的 `MemoryService` + `tools_memory.py` + persona dead 注释 + display.py dead summarizer） |
| W3 显著退化（净 PnL 差 ≥ 2% 或重复决策错误 ≥ W2 × 1.5 或**绝对净胜数差距** ≥ 3 — 即 W2 net = wins − losses = −3，W3 net ≤ −6 算"显著退化"）| **按 `agent_reflection_tools_candidate` 启动 3-tier reframe 设计**（playbook + bias mirror + 异步 distill） |
| W3 持平模糊（无显著差异） | **再观察一轮（W4）**，或并行启动 forensic 分析（grep "spring" / 重复 pattern 占比是否由 Recent Cycle Summaries 主导） |

**阈值是草案**，W3 数据出来后可依据 sample 体量与 noise 修订；本 iter 不订死。

### 4.4 cross-period attribution caveat

W2 baseline = "memory + Recent Cycle Summaries"，W3 baseline = "仅 Recent Cycle Summaries"。除 memory 通道外其他变量：
- W2 → W3 期间 G-calc audit (PR #53) 修正 ATR / pivots / MDD / BB / OI 6 处计算 → 影响 reasoning 质量但与 memory 通道独立
- W2 → W3 期间 tool-opt 系列（PR #50–52）调整 alert family / mark-vs-last → 影响 tool 使用模式但与 memory 通道独立
- W3 sim 应记录所有跨期改动清单作为 attribution caveat（已有 git log 即可）

判定时 caveat 标注："W3 vs W2 差异**包含** memory 通道 + G-calc + tool-opt 联合效应；纯 memory 通道贡献需 forensic 分析。"

---

## 5. Out of scope

明确**不做**:

- ❌ 删除 `MemoryService` 类 / `tools_memory.py` / `tools_perception.py:get_memories` 函数 — 留作 revert 路径
- ❌ 删除 `memory_entries` DB 表 / 索引 / 历史 entries — forensic 数据保留
- ❌ 改 Recent Cycle Summaries 注入逻辑（R2-8b） — W3 单注入 baseline 依赖此
- ❌ 启动 3-tier reframe（playbook / journal / reflections） — 数据驱动决策，W3 后再说
- ❌ 加 reflection 工具（articulate_thesis / surface_my_recent_pattern / register_hypothesis） — 同上
- ❌ 修改 persona Layer 2 / Layer 3 的 **reasoning steering 内容**（"如何思考"维度、市场结构 / 风险 / 仓位管理 / 信号确认等段落）— principle 8（信任 agent + 工具优先，不靠 prompt nudge）。**例外**：删除 Layer 2 中**指向已下线工具的 dead pointer**（如 `_build_layer2()` line 135 "Are there relevant lessons in your memory?"）属 wiring cleanup，与 reasoning steering 改动不同 — 保留该句会让 agent 在 W3 仍寻找已下线的 memory 工具。
- ❌ 给 `MemoryService` 加 "deprecated" 警告或 noop — `deps.memory` 仍在 deps 装配中，无人调用即可
- ❌ display.py 中 dead summarizer **函数** 清理（`_summarize_get_memories` / `summarize_save_memory`） — follow-up 候选（注：display.py **三层集合** 同步移除已在 §3.1 落，因 T-DG-2 一致性要求；函数体保留）
- ❌ 测试 dead fixture mocks 清理（§3.2 Dead fixture mocks 清单所列 `memory=AsyncMock(format_for_prompt=...)` 与 `deps.memory.format_for_prompt = AsyncMock(...)` 两形态）— 同上 follow-up 候选

---

## 6. 工作流 + 提交计划

按 `feedback_plan_doc_commit_first`: docs commit 先于 impl commits。

| Commit | 内容 |
|---|---|
| 1 | docs(iter-w2r3-memory-disable): design spec |
| 2 | docs(iter-w2r3-memory-disable): plan doc |
| 3 | refactor(iter-w2r3-memory-disable): remove memory tool register + prompt injection + persona refs |
| 4 | test(iter-w2r3-memory-disable): add drift guard + skip existing memory tests |

Feature 分支: `iter-w2r3-memory-disable`（per `feedback_git_branch`）
最终 PR 标题: `iter-w2r3-memory-disable: disable memory tool for W3 clean baseline`

---

## 7. 触发型 follow-up candidates（不在本 iter）

W3 数据出来后视情况启动：

| Candidate | 触发条件 |
|---|---|
| Permanent retirement cleanup | W3 同等/更好 → 移除 MemoryService / tools_memory.py / dead summarizer / memory_entries 表 |
| 3-tier playbook reframe | W3 显著退化 + sim 数据指向"需要持续抽象通道" |
| Articulate-thesis pre-mortem tool | W3 显示 plan-vs-actual delta 大（独立信号） |
| Surface-my-recent-pattern bias mirror | W3 显示连续同向 / FOMO 关键词聚集 |
| `MemoryService` 类 deprecation | 永久退役决策后 |
