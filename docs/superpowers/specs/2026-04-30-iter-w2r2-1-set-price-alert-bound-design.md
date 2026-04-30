# Iter W2-R2-1 — set_price_alert 阈值范围放宽 + docstring 措辞强化

**Date**: 2026-04-30
**Branch**: `feature/iter-w2r2-1-set-price-alert-bound`
**Source**: `.working/sim4-issues-inventory.md §P1-2` / `.working/all-pending-needs.md` Tier 1 R2-1
**前置依赖**: 无（独立 PR，不依赖其他 R2 议题）
**预估工作量**: 1 小时（含 spec / plan / impl / TDD / review-before-commit / merge）

---

## 1. 背景与动机

### 1.1 现象

`sim #4` 中 agent 4 次提交 `threshold_pct=0.3 / 0.4 / 0.4 / 0.4`，全部失败：

```
Invalid threshold_pct: must be 0.5-50.0, got 0.3
```

每次失败后 agent 重试 0.5 成功。累积 8 次 LLM round-trip × ~6k token = **~48k token 浪费在重试**。

W1 baseline 26 ✗ 中也含此类——**这是持续问题，不是新引入**。

### 1.2 brainstorm 阶段的根因校准（2026-04-30）

初版 inventory 写"工具 docstring 没暴露 0.5 下限给 LLM"。**核对代码后发现该诊断错**：

```python
# src/agent/trader.py:494-496（现状）
Args:
    threshold_pct: alert threshold percent (0.5-50%).
    window_minutes: time window in minutes (1-240).
```

下限 `(0.5-50%)` **已经暴露**给 LLM；错误信息 `must be 0.5-50.0, got 0.3` 也清晰。问题不在 fact 缺失，而在 fact 本身错。

**校准后的根因**（双因）：

1. **真因（design-time guess 错）**: `0.5` 下限本身设严了。sim #4 中 agent 4 次试 `0.3 / 0.4` 是**真实需求信号**——agent 在 1h window 里想要更敏感的早警报，不是行为偏差。BTC $75K 价位 0.5% = $375，对早警报场景偏粗；0.1% = $75 才接近 1min 噪音边缘。**下限放到 0.1 而非 0.3 不是仅响应 sim #4 实证（0.3-0.4），而是预留 0.1-0.3 未来需求空间，避免下次 W2 又出现 ≥ 0.1 但 < 0.3 的越界仍被卡。**
2. **次因（fact 可读性）**: docstring 暴露了范围但 `(0.5-50%)` 括号注解 LLM 感知优先级低；`(min 0.1, max 50)` 把下限关键词显化

### 1.3 brainstorm 决议（沿用，不重做）

- **C1 (根因双因)**: 主因放宽下限 + 次因强化措辞
- **C2 (放宽到 0.1-50)**: 0.1 仍防 alert 系统雪崩的技术下界；不放到 `0-100`（0 = 任何 tick 都触发 = streaming 雪崩；100% 上限无场景）
- **C3 (不加 schema constraint)**: 故意不加 Pydantic `Field(ge=0.1)`，保留观察窗口——失败信号进 message history 让 agent 学习 / 让 SQL 暴露 prompt 引导 / N10 / 模型行为问题；详见新 memory `feedback_observation_period_soft_constraint`
- **C4 (不加 default)**: alert 配置是行为决策，default 屏蔽 agent 主动选值的观察价值
- **C5 (错误信息保持现状)**: 现 `Invalid threshold_pct: must be 0.5-50.0, got X` 已含范围 + 实际值；放宽下限后改为 `must be 0.1-50.0, got X` 跟随
- **C6 (不扩展同类工具)**: `add_price_level_alert.price` / `adjust_leverage.leverage` 范围审查留独立 follow-up，议题边界清晰

### 1.4 实施前发现的双层 validation + 双处过时断言

`grep` + spec review 第二轮回查后发现：

**(1) service 层也有 0.5 下限**：

```python
# src/services/price_alert.py:20-22
def _validate_params(threshold_pct: float, window_minutes: int) -> None:
    if not (0.5 <= threshold_pct <= 50.0):
        raise ValueError(f"threshold_pct must be 0.5-50.0, got {threshold_pct}")
```

**两层都必须放宽**，否则 tool 层放过 `0.1` 但 service 层抛 `ValueError`。

**(2) 两处过时断言**（spec review 第二轮发现，第一轮 grep 用了 `| head -30` 截断遗漏 test_tools.py，必须修）：

```python
# tests/test_price_alert.py:141  断言 0.1 应抛 ValueError——R2-1 改后 0.1 合法 → 测试 fail
service.update_params(threshold_pct=0.1, window_minutes=60)

# tests/test_tools.py:262-268  test_set_price_alert_threshold_too_low
async def test_set_price_alert_threshold_too_low(deps):
    """threshold_pct < 0.5 时应返回错误，不调用 update。"""  # ← docstring 旧下限
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 0.1, 5, reasoning="test")    # ← 0.1 R2-1 后合法
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()              # ← R2-1 后会被调用
```

R2-1 改下限到 0.1 后：
- L266 `0.1` 进入合法路径 → 走到 `update_alert_params` 实调用
- L267 错误信息断言 fail（结果会是 "Price alert updated"）
- L268 `assert_not_called()` fail

**两处必须同步改 0.1 → 0.05**（保留越界保护语义）+ `test_tools.py` docstring `< 0.5` → `< 0.1`。

**同期已 verify 不影响的测试**（55.0 / 0 / 250 / 2.0 仍越界或本就合法）：

- `test_tools.py:271` `test_set_price_alert_threshold_too_high` 用 55.0 ✓ 不动
- `test_tools.py:280` `test_set_price_alert_window_out_of_range` 用 0/250 ✓ 不动
- `test_tools.py:253` `test_set_price_alert_valid` 用 2.0 ✓ 不动
- `test_fact_only_wordlist.py:690-694` `_invoke_set_price_alert` 用 1.5 ✓ 不动（**真实原因是 alerts disabled 早返回，根本到不了 validation**——即使传 0.05 也不 fail；spec §3.6 论证）

---

## 2. 设计目标

### 2.1 In-scope

| # | 改动 |
|---|---|
| **G1** | `src/agent/trader.py` `set_price_alert` wrapper docstring 措辞从 `(0.5-50%)` 改为 `(min 0.1, max 50)`；`window_minutes` 同步改为 `(min 1, max 240)`（措辞统一，下限值未变）|
| **G2** | `src/agent/tools_execution.py` impl docstring 一致性同步 |
| **G3** | `src/agent/tools_execution.py:212-213` tool 层 validation 阈值 `0.5 → 0.1` + 错误信息跟随 |
| **G4** | `src/services/price_alert.py:20-22` service 层 validation 阈值 `0.5 → 0.1` + 错误信息跟随 |
| **G5** | drift guard 测试：assert wrapper docstring 含 `min 0.1` / `max 50` / `min 1` / `max 240` |
| **G6a** | `tests/test_price_alert.py:141` 现断言 `0.1 抛 ValueError` → 改断言 `0.05 抛 ValueError` |
| **G6b** | `tests/test_tools.py:262-268` `test_set_price_alert_threshold_too_low`：L263 docstring `< 0.5` → `< 0.1`；L266 `set_price_alert(deps, 0.1, ...)` → `(deps, 0.05, ...)`；L267-268 错误信息断言 + assert_not_called 保持（值改后仍命中）|
| **G7** | 边界 ✓ 新增测试：`threshold_pct=0.1` service / tool 层均接受 |
| **G8** | `.working/sim4-issues-inventory.md §P1-2` 根因段校准（不进 git，brainstorm 阶段已完成）|
| **G9** | 新 memory: `feedback_observation_period_soft_constraint.md` + `MEMORY.md` 索引（不进 git，brainstorm 阶段已完成）|
| **G10** | `.working/all-pending-needs.md` Tier 2 加 N11 + Tier 3 加 N14（不进 git，brainstorm 阶段已完成）|

### 2.2 Out-of-scope（不做项 + 何时做）

| 议题 | 不做理由 | 何时做 |
|---|---|---|
| 加 Pydantic Field constraint | 违反 soft-constraint 纪律（C3）| 永不（除非纪律本身被推翻）|
| 加参数 default | 违反 fact-provider 哲学（C4）| 同上 |
| 改 `add_price_level_alert.price` 范围 | sim #4 未暴露 price 越界 | 长 wake 期 agent 设奇异 price 实例 ≥ 1 |
| `window_minutes` 下限放宽（1 → 0.5）| sim #4 未暴露 window 越界 | 永不（保持现状）|
| 升级到 ModelRetry | Iter 5 observation candidate B 项 | error-as-fact ≥ 3 例触发后启动 |
| 改错误信息措辞（除范围跟随外）| 现已清晰 | R2-9 重跑后仍越界且诊断错方向时考虑 |
| **N11**（合并）`get_kline_data.candle_count` 80 + `add_price_level_alert` 容量 max 20 审查；同 PR 应用 silent-clamp policy 改造 | sim #4 未实证触发 + 是 W2 数据驱动的工具参数审查议题（与 R2-1 同哲学，分议题保持单 PR 边界清晰）；详见全工具参数审计 inventory（2026-04-30 brainstorm 产出）| W2 期 agent 触及任一边界 ≥ 1 实例后启动单 brainstorm + 单 PR |
| **N14** `place_limit_order.leverage` / `adjust_leverage.leverage` 上限校验 | 方向相反（加防护非放宽限制），属 Tier 3 实盘前 batch；与 R2-6 (P0-2 max_position_pct) 同源（资金/杠杆硬风控） | 实盘前 batch / R2-6 联动 |
| **set_next_wake silent clamp(1, 60) → explicit reject** | 是 R2-5 (P0-5) scope（与 scheduler 30min 兜底议题联合 brainstorm）| R2-5 spec 阶段纳入 |

---

## 3. 设计详情

### 3.1 改动 A — wrapper docstring（trader.py L494-497）

```python
# 改前
Args:
    threshold_pct: alert threshold percent (0.5-50%).
    window_minutes: time window in minutes (1-240).
    reasoning: brief description of your decision logic.

# 改后
Args:
    threshold_pct: alert threshold percent (min 0.1, max 50).
    window_minutes: time window in minutes (min 1, max 240).
    reasoning: brief description of your decision logic.
```

**为什么 `(min X, max Y)` 而非 `(X-Y)`**：项目内现行范式是 `(X-Y)` / `(X-Y, default Z)` / `(default Z)`（如 `(1-14, default 7)` / `(default 50)`），均无 `(min X, max Y)`。R2-1 主动**引入新范式**（不是"统一"），目的是显化下限关键词以提升 LLM 感知（§1.2 次因诊断）。C6 已声明不扩展同类工具，故 R2-1 是 set_price_alert 单点引入；未来其他工具是否切换由 N11 议题数据驱动决定。

### 3.2 改动 B — impl docstring（tools_execution.py L206）

```python
# 改前
"""Adjust price alert parameters. threshold_pct: 0.5-50%, window_minutes: 1-240."""

# 改后
"""Adjust price alert parameters. threshold_pct: min 0.1, max 50, window_minutes: min 1, max 240."""
```

**理由**：impl docstring 不被 pydantic-ai sniff（不影响 LLM 工具描述），但保持单文件内措辞一致防未来读代码者疑惑。

### 3.3 改动 C — tool 层 validation（tools_execution.py L212-213）

```python
# 改前
if not (0.5 <= threshold_pct <= 50.0):
    return f"Invalid threshold_pct: must be 0.5-50.0, got {threshold_pct}"

# 改后
if not (0.1 <= threshold_pct <= 50.0):
    return f"Invalid threshold_pct: must be 0.1-50.0, got {threshold_pct}"
```

`window_minutes` validation 不动（`1 <= window_minutes <= 240` 保持）。

### 3.4 改动 D — service 层 validation（price_alert.py L20-22）

```python
# 改前
def _validate_params(threshold_pct: float, window_minutes: int) -> None:
    if not (0.5 <= threshold_pct <= 50.0):
        raise ValueError(f"threshold_pct must be 0.5-50.0, got {threshold_pct}")

# 改后
def _validate_params(threshold_pct: float, window_minutes: int) -> None:
    if not (0.1 <= threshold_pct <= 50.0):
        raise ValueError(f"threshold_pct must be 0.1-50.0, got {threshold_pct}")
```

`window_minutes` 部分（line 23-24）不动。

### 3.5 测试改动

#### T1 — drift guard（新增 1 测试）

文件：`tests/test_trader_agent.py`（与 Iter 5 T5/T6 docstring 相关 drift guard 同文件，共用 `_function_toolset.tools` 模式）

**验证什么**：实际传给 LLM 的工具 schema 中 `threshold_pct` / `window_minutes` 参数的 description——即 pydantic-ai 通过 Google docstring sniffing 提取后注入到 JSON schema 的内容（非 raw docstring 字符串）。raw docstring 改了但 sniff 失败 = LLM 看不到 = 必须按 schema 验。

```python
def test_set_price_alert_schema_exposes_threshold_range():
    """R2-1 drift guard: set_price_alert tool schema must expose threshold_pct and
    window_minutes range to LLM via pydantic-ai docstring sniffing.
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["set_price_alert"]
    schema = tool.tool_def.parameters_json_schema  # pydantic-ai 1.78 实测 API（spec 阶段已 verify）

    threshold_desc = schema["properties"]["threshold_pct"]["description"]
    assert "min 0.1," in threshold_desc, f"lower bound 0.1 missing from LLM-visible schema: {threshold_desc!r}"
    assert "max 50)" in threshold_desc, f"upper bound 50 missing from LLM-visible schema: {threshold_desc!r}"

    window_desc = schema["properties"]["window_minutes"]["description"]
    assert "min 1," in window_desc, f"window lower bound missing: {window_desc!r}"
    assert "max 240)" in window_desc, f"window upper bound missing: {window_desc!r}"
```

**API 一致性**：与 Iter 5 既有 drift guard（`tools.docstring_format` / `tools.require_parameter_descriptions`，`test_trader_agent.py:159, 171`）共用 `_function_toolset.tools[name]` 入口，但 **schema 字段是首次走 `.tool_def.<attr>` 二级 attr 路径**（Iter 5 既有 drift guard 仅用一级 attr）。Spec 阶段已实测 pydantic-ai 1.78 verify。

**实测 description 完整样本**（Spec 阶段 REPL）：

```python
# 当前（spec 实测）
schema["properties"]["threshold_pct"]["description"]
# → "alert threshold percent (0.5-50%)."
schema["properties"]["window_minutes"]["description"]
# → "time window in minutes (1-240)."

# R2-1 改后（期望值）
# threshold_pct → "alert threshold percent (min 0.1, max 50)."
# window_minutes → "time window in minutes (min 1, max 240)."
```

Google docstring sniff 提取的是 Args 行尾文本（不含参数名前缀），`min 0.1` / `max 50` / `min 1` / `max 240` 子串断言均可命中。

#### T2a — service 层 boundary validation 测试更新（修改 1 测试）

文件：`tests/test_price_alert.py:136-147`

```python
# 改前
def test_update_params_boundary_validation():
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=0.1, window_minutes=60)  # ← 现下限 0.5 时 0.1 越界
    ...

# 改后
def test_update_params_boundary_validation():
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=0.05, window_minutes=60)  # ← 新下限 0.1 时 0.05 越界
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=55.0, window_minutes=60)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=5.0, window_minutes=0)
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=5.0, window_minutes=250)
```

#### T2b — tool 层 over-threshold-low 测试更新（修改 1 测试）

文件：`tests/test_tools.py:262-268`

```python
# 改前
async def test_set_price_alert_threshold_too_low(deps):
    """threshold_pct < 0.5 时应返回错误，不调用 update。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 0.1, 5, reasoning="test")    # ← 0.1 R2-1 后合法
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()

# 改后
async def test_set_price_alert_threshold_too_low(deps):
    """threshold_pct < 0.1 时应返回错误，不调用 update。"""  # ← docstring 旧下限 0.5 → 新下限 0.1
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 0.05, 5, reasoning="test")   # ← 0.05 仍越下限
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()
```

L267-268 错误信息断言（"error/invalid/must be"）+ `assert_not_called()` 保持——值改后仍命中（错误信息含 `must be 0.1-50.0`，且越下限不会调 `update_alert_params`）。

#### T3 — boundary ✓ 新增测试（新增 2 测试）

文件：`tests/test_price_alert.py`（同文件）

```python
def test_update_params_accepts_new_lower_bound():
    """R2-1: 0.1 is the new lower bound, must be accepted."""
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    service.update_params(threshold_pct=0.1, window_minutes=60)
    assert service.get_params() == (0.1, 60)


def test_constructor_accepts_new_lower_bound():
    """R2-1: PriceAlertService init also accepts threshold_pct=0.1."""
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=0.1)
    assert service.get_params() == (0.1, 60)
```

#### T4 — tool 层 validation 边界测试（新增 1 测试）

文件：`tests/test_tool_enhancement.py`（紧邻现有 `test_set_price_alert_disabled` L699 / `test_set_price_alert_enabled` L709，与 `_make_deps()` 模式 L285 共享）

```python
async def test_set_price_alert_accepts_threshold_0_1():
    """R2-1: tool layer accepts threshold_pct=0.1 (new lower bound)."""
    deps = _make_deps()  # alerts enabled by default in test_tool_enhancement.py:285+
    result = await set_price_alert(deps, threshold_pct=0.1, window_minutes=15, reasoning="test")
    assert "Price alert updated" in result
    assert "threshold=0.1%" in result  # `%` 锁尾防 0.15 子串误命中（P2-1）
```

#### T5 — tool 层 validation 越界测试（新增 1 测试，可选合并 T4）

文件：同 T4（`tests/test_tool_enhancement.py`）

```python
async def test_set_price_alert_rejects_threshold_below_0_1():
    """R2-1: tool layer rejects threshold_pct=0.05 with new error message."""
    deps = _make_deps()
    result = await set_price_alert(deps, threshold_pct=0.05, window_minutes=15, reasoning="test")
    assert "Invalid threshold_pct: must be 0.1-50.0" in result
```

**测试总计**：T1 (+1) + T2a/T2b (修改不计，2 处过时断言) + T3 (+2) + T4/T5 (+1 ~ +2，视是否合并) = **+4 ~ +5 net**。

预期：baseline + 4 ~ 5 passed，±0 skipped/failed。Plan 阶段先跑 `pytest --collect-only -q | tail -1` 锁实际 baseline，避免 spec hardcoded 数字 drift。

### 3.6 fact-only 兼容性

`tests/test_fact_only_wordlist.py:690-694` `_invoke_set_price_alert` fixture 调 `set_price_alert(deps, 1.5, 30, ...)`——**不受 R2-1 影响**。

精确论证：fixture 设 `deps.exchange.get_alert_params = mocker.Mock(return_value=None)`（line 693），触发 impl line 208 `if deps.exchange.get_alert_params() is None: return "Alerts are disabled..."` **早返回**，根本不到 line 212 validation。fixture docstring 自己也写明 `"Early return: alerts disabled."` 即使 1.5 / 0.05 / 任意越界值也不 fail——validation 不被执行。

### 3.7 Brainstorm 阶段已就绪的私域产出（spec 仅引用，不进 git）

R2-1 brainstorm 阶段（2026-04-30）已直接落到本地 memory + `.working/` 文档（均在 .gitignore 内不进 PR），实施阶段 plan / impl 这些已就位无需再改：

1. **memory `feedback_observation_period_soft_constraint.md`**——观察期工具设计哲学（fact-provider 不是 guard），双条 policy + §3 例外。R2-1 是 §1 首个落地。`MEMORY.md` 索引行同步加。完整内容见 memory 文件本身。
2. **`.working/sim4-issues-inventory.md §P1-2`**——根因段校准（design-time guess 错为主因 + fact 可读性次因）。完整校准内容见 inventory 文件本身。
3. **`.working/all-pending-needs.md`**——Tier 2 加 N11（合并 `candle_count` + alert 容量，W2 期数据驱动启动）；Tier 3 加 N14（leverage 上限实盘前防护，与 P3 / R2-6 同源）。

> N11 / N14 命名说明：N11 = observation-period 工具参数审查同家族合并；N14 = 实盘前 batch（方向相反，加防护非放宽）；与 R2-5 `set_next_wake` silent clamp 区分（那是 R2-5 P0-5 scope）。

---

## 4. Acceptance Criteria

| # | 验收项 | 验证方式 |
|---|---|---|
| AC1 | wrapper docstring 改为 `(min 0.1, max 50)` 和 `(min 1, max 240)` | `git diff src/agent/trader.py` |
| AC2 | impl docstring 同步 `min 0.1, max 50` / `min 1, max 240` | `git diff src/agent/tools_execution.py`（impl docstring 不入 LLM schema，仅人工 verify）|
| AC3 | tool 层 validation 阈值 `0.5 → 0.1` + 错误信息跟随 | `git diff src/agent/tools_execution.py` L212-213 |
| AC4 | service 层 validation 阈值 `0.5 → 0.1` + 错误信息跟随 | `git diff src/services/price_alert.py` L20-22 |
| AC5 | drift guard 测试通过（T1）| `pytest tests/test_trader_agent.py -v -k schema_exposes_threshold` |
| AC6a | service 层 boundary 测试改为 `0.05 抛错`（T2a）| `pytest tests/test_price_alert.py::test_update_params_boundary_validation` |
| AC6b | tool 层 over-threshold-low 测试改为 `0.05 + docstring < 0.1`（T2b）| `pytest tests/test_tools.py::test_set_price_alert_threshold_too_low` |
| AC7 | 新增边界 ✓ 测试通过（T3, T4）| `pytest tests/test_price_alert.py -k accepts_new_lower_bound` 等 |
| AC8 | 新增越界拒绝测试通过（T5）| `pytest -k test_set_price_alert_rejects_threshold` |
| AC9 | 全套 regression 0 回归 | `pytest`：baseline + 4 ~ 5 passed, ±0 skipped/failed（plan 阶段 `pytest --collect-only -q | tail -1` 锁实际 baseline 避免 hardcoded drift）|
| AC10 | **未**加 Pydantic Field constraint / 默认值 / 改 window 下限 / 漏改 `test_tools.py:262-268` 与 `test_price_alert.py:141` 任一处过时断言 | `git diff` 整体扫描 + `pytest` 全绿 |
| AC11 | spec §2.2 Out-of-scope 表完整列出 N11 / N14 / set_next_wake silent clamp（R2-5 scope）| spec self-review 时已 verify |

> brainstorm 阶段已就绪的私域产出（`feedback_observation_period_soft_constraint.md` / `MEMORY.md` 索引 / `sim4-issues-inventory.md §P1-2` / `all-pending-needs.md` Tier 2/3）不进 git 故不列 AC，brainstorm 阶段已 verify 完成。

---

## 5. 风险与回滚

### 5.1 风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| **alert 频繁触发**：放宽下限到 0.1 后，agent 真用 0.1 在快速行情下 alert 每分钟触发 | 中（W2 期 agent 可能试）| cycle 预算消耗增加 | 已有保护：`PriceAlertService._ticks.clear()` 触发后清窗口 reset 语义（`src/services/price_alert.py:57/65`，单次触发清窗口防连发）+ scheduler priority queue（Iter 7，cycle 调度层独立保护）。注：`max 20` 上限属另一子系统（`add_price_level_alert` 点位告警，`src/integrations/exchange/base.py:182`），与本议题波动告警无关。 |
| **agent 选 0.05 越界**：agent 试比 0.1 还小的值 | 低（行为模式连续，不会突然跳到 0.05）| 错误信息返回，agent 学习；本身就是观察 fact | 不缓解（保留作 observation point）|
| **service 层和 tool 层 validation 不一致** | 低（spec 已明确双层一起改）| service 抛 ValueError 上溯不可控 | code review checklist 显式 verify 双层 + plan 阶段拆 task 强制双修 |
| **现有测试还有 hardcoded 0.5 漏改** | 低 | 测试 fail | spec §1.4 第二轮 grep 已覆盖（`tests/test_tools.py:262-268` + `tests/test_price_alert.py:141` 两处过时断言已纳入 G6a/G6b）；plan 阶段 `pytest -v` 收尾 |

### 5.2 回滚

R2-1 是纯参数 + docstring 改动，无 schema / 数据 / 协议改变。回滚 = `git revert <merge-commit>` 单步完成，无 data fix。

---

## 6. 与未来 R2 议题的关系

| 关联议题 | 关系 |
|---|---|
| **R2-2** (cancel_alert / get_active_alerts 协议) | 同 alert 工具家族，但改不同函数；spec / plan / impl 物理零冲突 |
| **R2-3** (system.log 轮转) | 完全独立模块（cli vs agent）|
| **R2-4** (P0-1 业务失败 metrics) | 若 R2-4 加 `tool_calls.error_type` 写入业务失败，R2-1 措辞改后仍越界的 agent 调用会被记入；R2-9 SQL 查询时由此辨识"软约束失效"信号 |
| **R2-9** (Iter 10 重跑 smoke) | 观察 R2-1 + R2-2 + R2-3 + ... 联合修复后 sim 表现；R2-1 验证维度：(a) `Invalid threshold_pct` 出现频率 (b) agent 是否选 0.1（频繁触发）/ 仍试 < 0.1（说明软约束无效）|

---

## 7. 估算

- **Spec**: 已完成（本文档）
- **Plan**: ~15 min（writing-plans skill）
- **Impl**: ~30 min（4 处 source 改 + 5 测试改/新增）
- **Review (self + user) + commit + merge**: ~15 min
- **总计**: ~1 小时

改动量预估：

| 类型 | 行数 |
|---|---|
| Source（trader.py / tools_execution.py / price_alert.py）| ~10 行 |
| Tests（drift guard + boundary 改 + boundary ✓ + tool 边界 ± 测试）| ~40 行 |
| Docs / Memory / Inventory 校准 | ~30 行 |
| **总计** | **~80 行** |

属于"极小 PR"档位。
