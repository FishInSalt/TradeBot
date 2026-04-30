# R2-1 set_price_alert Threshold Range Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 放宽 `set_price_alert.threshold_pct` 下限 `0.5 → 0.1`，强化 wrapper docstring 措辞为 `(min 0.1, max 50)` 显化下限，加 drift guard，修两处过时断言（`test_price_alert.py:141` + `test_tools.py:262-268`）。**故意不加 Pydantic Field constraint**（observation-period soft-constraint 纪律 §1 首个落地）。

**Architecture:** Spec §1.4 双层 validation 双修（tool layer `tools_execution.py:212` + service layer `price_alert.py:21`）。Spec §3.5 测试 5 项：T1 drift guard（验 LLM-visible schema description）/ T2a/T2b 过时断言修（service + tool 两层）/ T3 service 边界 ✓ x2 / T4 tool 边界 ✓ + T5 tool 边界 ✗。

**Tech Stack:** pydantic-ai 1.78（已 pin via Iter 5 `>=1.78,<2`）/ Google docstring sniffing / pytest 8.x / Python 3.13。

**Spec reference:** `docs/superpowers/specs/2026-04-30-iter-w2r2-1-set-price-alert-bound-design.md`

**Baseline (locked 2026-04-30):** 927 tests collected via `uv run pytest --collect-only -q`. Target after R2-1: **+5 net = 932 tests collected**, all passed, 0 failed, ±0 skipped/failed delta.

**Branch:** `feature/iter-w2r2-1-set-price-alert-bound`（已建，spec commit `c1cb6a3`）

---

## File Touch Summary

| File | Change | Where |
|---|---|---|
| `src/agent/trader.py` | wrapper docstring 措辞 | L494-497 |
| `src/agent/tools_execution.py` | impl docstring + tool layer validation | L206 + L212-213 |
| `src/services/price_alert.py` | service layer validation | L20-22 |
| `tests/test_trader_agent.py` | T1 drift guard（新增 1 测试）| 末尾 append |
| `tests/test_price_alert.py` | T2a 过时断言修 + T3 边界 ✓ x2（新增 2 测试）| L141 改 + 末尾 append |
| `tests/test_tools.py` | T2b 过时断言修 | L263, L266 |
| `tests/test_tool_enhancement.py` | T4 + T5 tool 边界 ± （新增 2 测试）| 紧邻 L709 `test_set_price_alert_enabled` |

---

## Task 1: T1 drift guard + wrapper docstring

**Files:**
- Modify: `src/agent/trader.py:494-497` (wrapper docstring)
- Test: `tests/test_trader_agent.py` (new test appended at end)

- [ ] **Step 1: Write the failing T1 drift guard test**

Append to `tests/test_trader_agent.py` end of file:

```python
def test_set_price_alert_schema_exposes_threshold_range():
    """R2-1 drift guard: set_price_alert tool schema must expose threshold_pct and
    window_minutes range to LLM via pydantic-ai docstring sniffing.

    First-of-kind drift guard走 .tool_def.<attr> 二级 attr 路径（Iter 5 既有 drift
    guard 仅用一级 attr）。Spec 阶段已实测 pydantic-ai 1.78 verify。
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["set_price_alert"]
    schema = tool.tool_def.parameters_json_schema

    threshold_desc = schema["properties"]["threshold_pct"]["description"]
    assert "min 0.1," in threshold_desc, f"lower bound 0.1 missing from LLM-visible schema: {threshold_desc!r}"
    assert "max 50)" in threshold_desc, f"upper bound 50 missing from LLM-visible schema: {threshold_desc!r}"

    window_desc = schema["properties"]["window_minutes"]["description"]
    assert "min 1," in window_desc, f"window lower bound missing: {window_desc!r}"
    assert "max 240)" in window_desc, f"window upper bound missing: {window_desc!r}"
```

- [ ] **Step 2: Run T1 to verify it fails**

Run: `uv run pytest tests/test_trader_agent.py::test_set_price_alert_schema_exposes_threshold_range -v`

Expected: **FAIL** with assertion error showing current `threshold_desc = "alert threshold percent (0.5-50%)."` (lacks `min 0.1,`).

- [ ] **Step 3: Change wrapper docstring**

Edit `src/agent/trader.py` lines 494-497 (within `set_price_alert` wrapper):

```python
# Before
        Args:
            threshold_pct: alert threshold percent (0.5-50%).
            window_minutes: time window in minutes (1-240).
            reasoning: brief description of your decision logic.

# After
        Args:
            threshold_pct: alert threshold percent (min 0.1, max 50).
            window_minutes: time window in minutes (min 1, max 240).
            reasoning: brief description of your decision logic.
```

- [ ] **Step 4: Run T1 to verify it passes**

Run: `uv run pytest tests/test_trader_agent.py::test_set_price_alert_schema_exposes_threshold_range -v`

Expected: **PASS**.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "feat(iter-w2r2-1): T1 wrapper docstring 措辞强化 + drift guard

trader.py:494-496 set_price_alert wrapper Args:
- threshold_pct: (0.5-50%) → (min 0.1, max 50)
- window_minutes: (1-240) → (min 1, max 240)

新增 drift guard test_set_price_alert_schema_exposes_threshold_range，
首次走 tool.tool_def.parameters_json_schema 二级 attr 路径验证 LLM-visible
schema。assert 用逗号/右括号锁尾防 partial-match。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Tool layer validation + impl docstring + T2b/T4/T5

**Files:**
- Modify: `src/agent/tools_execution.py:206` (impl docstring)
- Modify: `src/agent/tools_execution.py:212-213` (tool validation)
- Modify: `tests/test_tools.py:263, 266` (T2b 过时断言)
- Test: `tests/test_tool_enhancement.py` (T4 + T5 new tests appended after `test_set_price_alert_enabled` at L709)

- [ ] **Step 1: Write T4 + T5 failing tests in test_tool_enhancement.py**

Append after `test_set_price_alert_enabled` (after L717 / before L720, the `test_cancel_order_success` definition):

Mock pattern follows existing `test_set_price_alert_enabled` L712-714（显式 `get_alert_params` + `update_alert_params` mock，避免 root `AsyncMock` 子属性产生未 await coroutine 的 RuntimeWarning）：

```python
async def test_set_price_alert_accepts_threshold_0_1():
    """R2-1 T4: tool layer accepts threshold_pct=0.1 (new lower bound)."""
    from src.agent.tools_execution import set_price_alert
    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, threshold_pct=0.1, window_minutes=15, reasoning="test")
    assert "Price alert updated" in result
    assert "threshold=0.1%" in result  # `%` 锁尾防 0.15 子串误命中（spec P2-1）


async def test_set_price_alert_rejects_threshold_below_0_1():
    """R2-1 T5: tool layer rejects threshold_pct=0.05 with new error message."""
    from src.agent.tools_execution import set_price_alert
    deps = _make_deps()
    deps.exchange.get_alert_params = MagicMock(return_value=(5.0, 60))
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, threshold_pct=0.05, window_minutes=15, reasoning="test")
    assert "Invalid threshold_pct: must be 0.1-50.0" in result
```

- [ ] **Step 2: Run T4 + T5 to verify they fail**

Run: `uv run pytest tests/test_tool_enhancement.py::test_set_price_alert_accepts_threshold_0_1 tests/test_tool_enhancement.py::test_set_price_alert_rejects_threshold_below_0_1 -v`

Expected: **FAIL**.
- T4 fails: `0.1` currently rejected by L212 validation (`0.5 <= threshold_pct`), result is `"Invalid threshold_pct: must be 0.5-50.0, got 0.1"` — `"Price alert updated"` not in result.
- T5 fails: result has old error message `"must be 0.5-50.0"`, assertion expects `"must be 0.1-50.0"`.

- [ ] **Step 3: Change tools_execution.py impl docstring + tool validation**

Edit `src/agent/tools_execution.py` line 206:

```python
# Before
async def set_price_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    reasoning: str,
) -> str:
    """Adjust price alert parameters. threshold_pct: 0.5-50%, window_minutes: 1-240."""

# After
async def set_price_alert(
    deps: TradingDeps,
    threshold_pct: float,
    window_minutes: int,
    reasoning: str,
) -> str:
    """Adjust price alert parameters. threshold_pct: min 0.1, max 50, window_minutes: min 1, max 240."""
```

Edit `src/agent/tools_execution.py` lines 212-213:

```python
# Before
    if not (0.5 <= threshold_pct <= 50.0):
        return f"Invalid threshold_pct: must be 0.5-50.0, got {threshold_pct}"

# After
    if not (0.1 <= threshold_pct <= 50.0):
        return f"Invalid threshold_pct: must be 0.1-50.0, got {threshold_pct}"
```

`window_minutes` validation (L214-215) **不动**.

- [ ] **Step 4: Run T4 + T5 to verify they pass**

Run: `uv run pytest tests/test_tool_enhancement.py::test_set_price_alert_accepts_threshold_0_1 tests/test_tool_enhancement.py::test_set_price_alert_rejects_threshold_below_0_1 -v`

Expected: **PASS**.

- [ ] **Step 5: Run test_tools.py:262 to verify T2b stale assertion fails**

Run: `uv run pytest tests/test_tools.py::test_set_price_alert_threshold_too_low -v`

Expected: **FAIL** — currently asserts `0.1` returns error, but tool now accepts `0.1`. Result is `"Price alert updated: threshold=0.1%, window=5min"`, neither `"error"`/`"invalid"`/`"must be"` in lowercase result; also `update_alert_params` was called.

- [ ] **Step 6: Fix T2b stale assertion**

Edit `tests/test_tools.py` lines 262-268:

```python
# Before
async def test_set_price_alert_threshold_too_low(deps):
    """threshold_pct < 0.5 时应返回错误，不调用 update。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 0.1, 5, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()

# After
async def test_set_price_alert_threshold_too_low(deps):
    """threshold_pct < 0.1 时应返回错误，不调用 update。"""
    from src.agent.tools_execution import set_price_alert
    deps.exchange.update_alert_params = MagicMock()
    result = await set_price_alert(deps, 0.05, 5, reasoning="test")
    assert "error" in result.lower() or "invalid" in result.lower() or "must be" in result.lower()
    deps.exchange.update_alert_params.assert_not_called()
```

Only L263 docstring (`< 0.5` → `< 0.1`) and L266 (`0.1` → `0.05`) change. L267 / L268 assertions unchanged (越界仍 reject + not called，仍命中).

- [ ] **Step 7: Run test_tools.py:262 to verify T2b passes**

Run: `uv run pytest tests/test_tools.py::test_set_price_alert_threshold_too_low -v`

Expected: **PASS**.

- [ ] **Step 8: Commit Task 2**

```bash
git add src/agent/tools_execution.py tests/test_tools.py tests/test_tool_enhancement.py
git commit -m "feat(iter-w2r2-1): tool layer validation 放宽 0.5 → 0.1 + T4/T5/T2b

tools_execution.py:
- L206 impl docstring 同步措辞 (min 0.1, max 50, min 1, max 240)
- L212-213 validation threshold_pct 下限 0.5 → 0.1，错误信息跟随 0.1-50.0

tests/test_tool_enhancement.py 新增 2 测试：
- T4 test_set_price_alert_accepts_threshold_0_1（边界 ✓ + threshold=0.1% % 锁尾）
- T5 test_set_price_alert_rejects_threshold_below_0_1（0.05 越界 + 新错误信息）

tests/test_tools.py:262-268 修过时断言（spec §1.4 第二轮 grep 发现）：
- L263 docstring 旧下限 0.5 → 0.1
- L266 0.1 → 0.05（保留越界保护语义）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Service layer validation + T2a/T3

**Files:**
- Modify: `src/services/price_alert.py:20-22` (service validation)
- Modify: `tests/test_price_alert.py:141` (T2a 过时断言)
- Test: `tests/test_price_alert.py` (T3 new tests appended at end)

- [ ] **Step 1: Write T3 boundary ✓ tests in test_price_alert.py**

Append at end of `tests/test_price_alert.py`:

```python
def test_update_params_accepts_new_lower_bound():
    """R2-1 T3: 0.1 is the new lower bound, must be accepted by update_params."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=5.0)
    service.update_params(threshold_pct=0.1, window_minutes=60)
    assert service.get_params() == (0.1, 60)


def test_constructor_accepts_new_lower_bound():
    """R2-1 T3: PriceAlertService init also accepts threshold_pct=0.1."""
    from src.services.price_alert import PriceAlertService
    service = PriceAlertService(symbol="BTC/USDT:USDT", window_minutes=60, threshold_pct=0.1)
    assert service.get_params() == (0.1, 60)
```

- [ ] **Step 2: Run T3 to verify they fail**

Run: `uv run pytest tests/test_price_alert.py::test_update_params_accepts_new_lower_bound tests/test_price_alert.py::test_constructor_accepts_new_lower_bound -v`

Expected: **FAIL** — both raise `ValueError("threshold_pct must be 0.5-50.0, got 0.1")` because service `_validate_params` still has `0.5 <= threshold_pct`.

- [ ] **Step 3: Change service validation**

Edit `src/services/price_alert.py` lines 20-22:

```python
# Before
    @staticmethod
    def _validate_params(threshold_pct: float, window_minutes: int) -> None:
        if not (0.5 <= threshold_pct <= 50.0):
            raise ValueError(f"threshold_pct must be 0.5-50.0, got {threshold_pct}")

# After
    @staticmethod
    def _validate_params(threshold_pct: float, window_minutes: int) -> None:
        if not (0.1 <= threshold_pct <= 50.0):
            raise ValueError(f"threshold_pct must be 0.1-50.0, got {threshold_pct}")
```

`window_minutes` validation (L23-24) **不动**.

- [ ] **Step 4: Run T3 to verify they pass**

Run: `uv run pytest tests/test_price_alert.py::test_update_params_accepts_new_lower_bound tests/test_price_alert.py::test_constructor_accepts_new_lower_bound -v`

Expected: **PASS**.

- [ ] **Step 5: Run test_price_alert.py:136 to verify T2a stale assertion fails**

Run: `uv run pytest tests/test_price_alert.py::test_update_params_boundary_validation -v`

Expected: **FAIL** — first `pytest.raises(ValueError)` block expects `0.1` to raise, but `0.1` is now accepted by service.

- [ ] **Step 6: Fix T2a stale assertion**

Edit `tests/test_price_alert.py` line 141:

```python
# Before
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=0.1, window_minutes=60)

# After
    with pytest.raises(ValueError):
        service.update_params(threshold_pct=0.05, window_minutes=60)
```

Other 3 `pytest.raises` blocks (L142-147) unchanged — `55.0` / `window=0` / `window=250` still over bounds.

- [ ] **Step 7: Run test_price_alert.py:136 to verify T2a passes**

Run: `uv run pytest tests/test_price_alert.py::test_update_params_boundary_validation -v`

Expected: **PASS**.

- [ ] **Step 8: Commit Task 3**

```bash
git add src/services/price_alert.py tests/test_price_alert.py
git commit -m "feat(iter-w2r2-1): service layer validation 放宽 0.5 → 0.1 + T2a/T3

price_alert.py:20-22 _validate_params:
- threshold_pct 下限 0.5 → 0.1，错误信息跟随 0.1-50.0
- window_minutes validation L23-24 不动

tests/test_price_alert.py 新增 2 测试 + 修 1 过时断言：
- T3 test_update_params_accepts_new_lower_bound（0.1 通过 update_params）
- T3 test_constructor_accepts_new_lower_bound（0.1 通过 PriceAlertService init）
- T2a L141: pytest.raises(ValueError) on 0.1 → 0.05（新下限 0.1 时 0.05 仍越界）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Regression sanity + AC verification

**Files:** No code changes.

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -q 2>&1 | tail -5`

Expected: `932 passed, X skipped, 0 failed`（X = baseline skip count from pre-flight，应与 baseline 相同 ±0；spec AC9 "+5 net passed, ±0 skipped/failed"）。

- [ ] **Step 2: Verify all 5 new tests + 2 stale-assertion fixes recognized**

Run: `uv run pytest --collect-only -q | tail -1`

Expected: `932 tests collected` (baseline 927 + 5 new = 932).

- [ ] **Step 3: Manual AC verify**

Run these checks one by one:

```bash
# AC1: wrapper docstring 含新串
git diff main -- src/agent/trader.py | grep "min 0.1, max 50"
# Expected: + line containing `(min 0.1, max 50)` for threshold_pct
#           and `(min 1, max 240)` for window_minutes

# AC2: impl docstring 含新串
git diff main -- src/agent/tools_execution.py | grep "threshold_pct: min 0.1, max 50"
# Expected: + line `Adjust price alert parameters. threshold_pct: min 0.1, max 50, window_minutes: min 1, max 240.`

# AC3: tool validation 新阈值
git diff main -- src/agent/tools_execution.py | grep "0.1 <= threshold_pct"
# Expected: + line `if not (0.1 <= threshold_pct <= 50.0):`

# AC4: service validation 新阈值
git diff main -- src/services/price_alert.py | grep "0.1 <= threshold_pct"
# Expected: + line `if not (0.1 <= threshold_pct <= 50.0):`

# AC10 防御: scan for hardcoded 0.5 in test files referencing set_price_alert
# 用 \b word boundary 避免误命中 0.50 / 0.500 / 10.5 / 0.55 等
grep -rnE "\b0\.5\b" tests/test_price_alert.py tests/test_tools.py tests/test_tool_enhancement.py | grep -iE "threshold|set_price_alert"
# Expected: no output (所有 0.5 已改为 0.1 / 0.05)
```

- [ ] **Step 4: Verify no Field constraint / default added (AC10)**

```bash
git diff main -- src/agent/trader.py src/agent/tools_execution.py | grep -E "Field\(|Annotated\[|default\s*="
# Expected: no output (no schema constraints / no defaults added)
```

- [ ] **Step 5: Final smoke + commit summary check**

```bash
git log --oneline main..HEAD
# Expected: 4 commits (1 spec + 3 feature commits Task 1/2/3)
# c1cb6a3 docs(iter-w2r2-1): add ... design spec
# <hash> feat(iter-w2r2-1): T1 wrapper docstring ...
# <hash> feat(iter-w2r2-1): tool layer validation ...
# <hash> feat(iter-w2r2-1): service layer validation ...
```

If any step fails, stop and investigate. Do NOT proceed to merge until all 5 AC checks pass and 932 tests pass.

---

## Acceptance Verification Map

| AC | Spec ref | Verified by |
|---|---|---|
| AC1 | wrapper docstring `(min 0.1, max 50)` / `(min 1, max 240)` | Task 1 Step 3 + Task 4 Step 3 git diff |
| AC2 | impl docstring 同步 | Task 2 Step 3 + Task 4 Step 3 git diff |
| AC3 | tool validation `0.5 → 0.1` | Task 2 Step 3 + Task 4 Step 3 git diff |
| AC4 | service validation `0.5 → 0.1` | Task 3 Step 3 + Task 4 Step 3 git diff |
| AC5 | drift guard test passes | Task 1 Step 4 + Task 4 Step 1 |
| AC6a | service boundary `0.05` | Task 3 Step 7 + Task 4 Step 1 |
| AC6b | tool boundary docstring `< 0.1` + `0.05` | Task 2 Step 7 + Task 4 Step 1 |
| AC7 | service 边界 ✓ T3 x2 passes | Task 3 Step 4 + Task 4 Step 1 |
| AC8 | tool 越界拒绝 T5 passes | Task 2 Step 4 + Task 4 Step 1 |
| AC9 | baseline + 5 passed, ±0 skipped/failed | Task 4 Step 1 |
| AC10 | 未加 Field constraint / default / 漏改过时断言 | Task 4 Step 3 + Step 4 |
| AC11 | spec §2.2 Out-of-scope 表完整 | Spec self-review 已 verify (commit `c1cb6a3`) |

---

## Out-of-Plan Reminders

按 spec §3.7，brainstorm 阶段已就绪的私域产出**不在 plan 内**（不进 git）：

- ✅ memory `feedback_observation_period_soft_constraint.md` — 已创建
- ✅ `MEMORY.md` 索引 — 已加
- ✅ `.working/sim4-issues-inventory.md §P1-2` — 已校准
- ✅ `.working/all-pending-needs.md` Tier 2 / Tier 3 — N11 / N14 已加

实施过程中若发现这些私域文件需补充，单独处理（可在本 plan 完成后再补）。

---

## Post-Plan: Merge & PR

执行完 Task 1-4 后：

1. Push branch: `git push -u origin feature/iter-w2r2-1-set-price-alert-bound`
2. 自检 + reviewer review-before-commit（按 memory `feedback_review_before_commit`）
3. 创建 PR `gh pr create --title "Iter W2-R2-1: set_price_alert threshold range expansion (0.5 → 0.1) + soft-constraint policy 首个落地"`
4. Merge `--no-ff` after PR approved（与 Iter 1/5/7/8 同模式）
5. 更新 `MEMORY.md` 顶部 `project_w2_prep_progress` 进度（R2-1 ✅ landed）

---

## Self-Review Checklist Result

✅ **Spec coverage**: G1 (Task 1 step 3) / G2 (Task 2 step 3) / G3 (Task 2 step 3) / G4 (Task 3 step 3) / G5 (Task 1 step 1) / G6a (Task 3 step 6) / G6b (Task 2 step 6) / G7 (Task 3 step 1) / G8/G9/G10 (brainstorm 阶段已完成) — 全覆盖

✅ **Placeholder scan**: 无 TBD/TODO；所有测试代码完整；所有 git commands 含 expected output

✅ **Type consistency**: `set_price_alert` / `PriceAlertService` / `_validate_params` / `update_params` / `_make_deps` / `tool.tool_def.parameters_json_schema` / `_function_toolset.tools` 在所有 task 中名称一致

✅ **Numbers**: baseline 927 (实测) → target 932 (+5)；与 spec §3.5 / AC9 一致
