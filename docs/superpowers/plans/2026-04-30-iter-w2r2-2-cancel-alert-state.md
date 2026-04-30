# R2-2 cancel_price_level_alert State Machine + ID Display Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修 sim #4 暴露的 cancel_price_level_alert 跨 cycle 100% 失败问题。两层修复：(A) `get_active_alerts` 输出主显真实 uuid，agent 跨 cycle 能看见可用 ID；(C) `cancel_price_level_alert` 错误信息分两类（协议错 vs 状态错），agent 误传 `#N` 或 `"N"` 时不再被"已触发"误导。加 wrapper docstring 引导 + drift guard。

**Architecture:** 双层显示 + 双层错误信息：
- **显示层** (`tools_perception.py:471`)：保留位置索引 `#N` 同时主显 `id=<uuid>`，跨 cycle 暴露真实 id。
- **执行层** (`tools_execution.py:268-276`)：先 8-char hex 格式校验 → 不通过返回 `Invalid alert_id format` 协议错；通过则进 sim_exchange `remove_price_level_alert`，仍 `False` 返回 `already triggered or expired` 状态错。
- **wrapper 引导** (`trader.py:558`)：docstring 明示 id 来源（`add_price_level_alert` 返回 + `get_active_alerts` 输出 `id=...`）。
- **drift guard**：通过 pydantic-ai `.tool_def.parameters_json_schema` 验证 wrapper docstring 暴露格式约束 + id 来源关键词。

**Tech Stack:** pydantic-ai 1.78（已 pin via Iter 5）/ Google docstring sniffing / pytest 8.x / Python 3.13 / `re` stdlib（hex 格式校验）。

**Spec reference:** `.working/sim4-issues-inventory.md §P1-1`（DB-verified 根因 + 修法候选 (A)+(C) 已闭环，无独立 long spec — 用户决议精简版跳 spec）。

**Baseline (locked 2026-04-30):** 932 tests collected via `uv run pytest --collect-only -q`（929 pass + 3 skip）。Target after R2-2: **+4 net = 936 tests collected**, 933 pass + 3 skip, 0 failed。

**净增准确计数**（4 新增 + 2 替换不计）:
| 测试名 | 状态 |
|---|---|
| `test_get_active_alerts_displays_real_uuid` | 新增 (Task 1) |
| `test_cancel_price_level_alert_tool_invalid_format` | 新增 (Task 2) |
| `test_cancel_price_level_alert_tool_state_not_found` | 新增（替换 `_not_found`，名字变 → 计新增）|
| `test_is_tool_error_cancel_alert_invalid_format_returns_true` | 新增 (Task 2) |
| `test_is_tool_error_cancel_alert_state_not_found_returns_true` | 新增（替换 `_not_found`）|
| `test_cancel_price_level_alert_schema_exposes_id_format_and_source` | 新增 (Task 3) |
| `test_cancel_price_level_alert_tool_not_found` | **删除**（替换为 invalid_format + state_not_found）|
| `test_is_tool_error_cancel_alert_not_found_returns_true` | **删除**（同上）|
| **净增** | **+4** |

**Branch:** `feature/iter-w2r2-2-cancel-alert-state`（已建于 main `535d92f`）

---

## File Touch Summary

| File | Change | Where |
|---|---|---|
| `src/agent/tools_perception.py` | get_active_alerts 输出主显真实 id | L471 |
| `src/agent/tools_execution.py` | cancel 8-char hex 格式校验 + 错误信息分两类 | L263-276 |
| `src/agent/trader.py` | cancel wrapper docstring 引导 id 来源 | L558 |
| `tests/test_alert_lifecycle.py` | T2 协议错（新增）+ T3 状态错（修现有）+ T4 display 协议错（新增）+ T5 display 状态错（修现有） | L593-606 修 + 末尾 append |
| `tests/test_price_level_alert.py` | T1a get_active_alerts 显示 id（新增） | 末尾 append |
| `tests/test_trader_agent.py` | T1b drift guard（新增） | 末尾 append |

**净增测试**：6 新增 - 2 删除 = **+4 净增 → 936 collected**（详见 Baseline 段下表）。

---

## Task 1: T1a get_active_alerts 输出主显真实 uuid

**Files:**
- Modify: `src/agent/tools_perception.py:471`
- Test: `tests/test_price_level_alert.py` (new test appended at end)

- [ ] **Step 1: Write the failing T1a test**

Append to `tests/test_price_level_alert.py` end of file:

```python
@pytest.mark.asyncio
async def test_get_active_alerts_displays_real_uuid():
    """R2-2 T1a: get_active_alerts 输出必须主显真实 uuid 且与位置索引同行，
    保证 agent 跨 cycle 能复制 id=<uuid> 真值给 cancel_price_level_alert。

    sim #4 根因：原输出仅有位置索引 `#N`，跨 cycle agent 无法获取 uuid → 100% cancel 失败。
    """
    from unittest.mock import MagicMock
    from src.agent.tools_perception import get_active_alerts

    exchange = _make_exchange()
    aid_a = exchange.add_price_level_alert(58000.0, "below", "BTC/USDT:USDT", "support")
    aid_b = exchange.add_price_level_alert(62000.0, "above", "BTC/USDT:USDT", "resistance")
    assert aid_a is not None and aid_b is not None

    deps = MagicMock()
    deps.exchange = exchange

    result = await get_active_alerts(deps)

    # 主显真实 uuid（agent 跨 cycle 复制 id 用）
    assert f"id={aid_a}" in result, f"uuid {aid_a!r} not in output: {result!r}"
    assert f"id={aid_b}" in result, f"uuid {aid_b!r} not in output: {result!r}"
    # 保留位置索引（向后兼容现有显示习惯）
    assert "#1" in result and "#2" in result
    # 锁定显示格式：#N 与 id= 必须同行（防未来 reformatter 把 id 拆下一行 / 删位置索引）
    assert any(f"#1 (id={aid_a})" in line for line in result.splitlines()), \
        f"expected '#1 (id={aid_a})' on a single line, got: {result!r}"
    assert any(f"#2 (id={aid_b})" in line for line in result.splitlines()), \
        f"expected '#2 (id={aid_b})' on a single line, got: {result!r}"
```

**Imports note**: 不需要新增 import — `pytest` 已在 file top L4，`_make_exchange()` helper 在 file L10-40 同文件内可直接调用。

- [ ] **Step 2: Run T1a to verify it fails**

Run: `uv run pytest tests/test_price_level_alert.py::test_get_active_alerts_displays_real_uuid -v`

Expected: **FAIL** with `AssertionError` — current output 仅 `#1 below 58000.00 — "support"`，缺 `id=<uuid>` 关键词。**不应**有 TypeError / setup error（_make_exchange 是同文件 helper，已 verified at L10-40）。

- [ ] **Step 3: Modify get_active_alerts display**

Edit `src/agent/tools_perception.py` line 471:

```python
# Before
        for i, a in enumerate(alerts, 1):
            lines.append(f'  #{i} {a["direction"]} {a["price"]:.2f} — "{a["reasoning"]}"')

# After
        for i, a in enumerate(alerts, 1):
            lines.append(f'  #{i} (id={a["id"]}) {a["direction"]} {a["price"]:.2f} — "{a["reasoning"]}"')
```

- [ ] **Step 4: Run T1a to verify it passes**

Run: `uv run pytest tests/test_price_level_alert.py::test_get_active_alerts_displays_real_uuid -v`

Expected: **PASS**.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/agent/tools_perception.py tests/test_price_level_alert.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-2): T1a get_active_alerts 主显真实 uuid

sim #4 根因：原输出仅有位置索引 `#N`，跨 cycle agent 无法获取
uuid → 100% cancel 失败 (008c/383d/8988 三个 cycle 全部 ✗)。

修法 (A)：保留位置索引 `#N` 同时主显 `(id=<uuid>)`，agent 跨
cycle 从 get_active_alerts 输出复制 id 即可。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: T2 + T3 cancel 错误信息分两类（协议错 vs 状态错）

**Files:**
- Modify: `src/agent/tools_execution.py:263-276` (cancel impl)
- Test: `tests/test_alert_lifecycle.py:593-606` (修原 not_found test) + 末尾 append T2 协议错

**设计要点**:
- UUID format = `str(uuid.uuid4())[:8]` → 8 个 hex 字符 `[0-9a-f]{8}`
- 校验用 `re.fullmatch(r"[0-9a-f]{8}", alert_id)` — `fullmatch` 防尾部空格 / 多字符
- 协议错信息：`"Invalid alert_id format: {alert_id!r}. Expected 8-char hex (e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids."`
- 状态错信息：`"Alert {alert_id} already triggered or expired"` — 去除原 "never existed" 误导（格式校验已分流"格式错"路径）

- [ ] **Step 1: 修原 not_found 测试（T3 状态错）+ 写 T2 协议错新测试**

Edit `tests/test_alert_lifecycle.py:593-606`. **Replace** the existing `test_cancel_price_level_alert_tool_not_found` with two distinct tests (协议错 + 状态错):

```python
@pytest.mark.asyncio
async def test_cancel_price_level_alert_tool_invalid_format():
    """R2-2 T2: 协议错（agent 传非 8-char hex）→ format 错误信息引导查看 get_active_alerts。

    sim #4 实证：agent 100% 传 `#1` / `"1"` / `"11"` 等 enumerate 索引误读，
    永远匹配不到 uuid。原统一错误信息把"格式错"和"已触发"合并 → agent 诊断错方向。
    """
    from src.agent.tools_execution import cancel_price_level_alert

    sim = make_sim_exchange()
    deps = MagicMock()
    deps.exchange = sim
    deps.db_engine = None
    deps.session_id = "test-session"

    # 三种典型协议错输入（含 # / 含 dash / 长度错）
    for bad_id in ["#1", "nonexistent-id", "1"]:
        result = await cancel_price_level_alert(deps, bad_id, "test")
        assert "Invalid alert_id format" in result, f"协议错信息缺失 for {bad_id!r}: {result!r}"
        assert "8-char hex" in result, f"格式提示缺失 for {bad_id!r}: {result!r}"
        assert "get_active_alerts" in result, f"id 来源引导缺失 for {bad_id!r}: {result!r}"
        assert repr(bad_id) in result or bad_id in result, f"用户输入回显缺失 for {bad_id!r}: {result!r}"


@pytest.mark.asyncio
async def test_cancel_price_level_alert_tool_state_not_found():
    """R2-2 T3: 状态错（合法 8-char hex 但 sim 中不存在）→ already triggered or expired。"""
    from src.agent.tools_execution import cancel_price_level_alert

    sim = make_sim_exchange()
    deps = MagicMock()
    deps.exchange = sim
    deps.db_engine = None
    deps.session_id = "test-session"

    # 合法 8-char hex 格式（防真碰撞，不与任何活跃 uuid 重合 — sim 中无 alerts）
    fake_id = "deadbeef"
    result = await cancel_price_level_alert(deps, fake_id, "test")

    assert "already triggered or expired" in result, f"状态错信息缺失: {result!r}"
    assert fake_id in result, f"alert_id 回显缺失: {result!r}"
    # 状态错不应混入"格式错"提示
    assert "Invalid alert_id format" not in result
    assert "8-char hex" not in result
```

- [ ] **Step 2: Run T2/T3 to verify they fail**

Run: `uv run pytest tests/test_alert_lifecycle.py::test_cancel_price_level_alert_tool_invalid_format tests/test_alert_lifecycle.py::test_cancel_price_level_alert_tool_state_not_found -v`

Expected: **BOTH FAIL**:
- T2 fails: current code 不区分 → 返回原 `Alert #1 not found (already triggered or never existed)`，缺 `Invalid alert_id format` 关键词。
- T3 fails: current code 返回 `Alert deadbeef not found (already triggered or never existed)`，缺 `already triggered or expired` 关键词。

- [ ] **Step 3: Modify cancel_price_level_alert impl**

Edit `src/agent/tools_execution.py`. Add `import re` at top (verify if missing, near other stdlib imports). Replace lines 263-276:

```python
# Before
async def cancel_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    reasoning: str,
) -> str:
    """Remove a price level alert by ID."""
    ok = deps.exchange.remove_price_level_alert(alert_id)
    if ok:
        await _record_action(
            deps, action="cancel_price_level_alert",
            reasoning=f"id={alert_id} | {reasoning}",
        )
        return f"Price level alert cancelled (id={alert_id})"
    return f"Alert {alert_id} not found (already triggered or never existed)"

# After
async def cancel_price_level_alert(
    deps: TradingDeps,
    alert_id: str,
    reasoning: str,
) -> str:
    """Remove a price level alert by ID."""
    # 协议层：8-char hex 格式校验（uuid.uuid4()[:8] 生成，[0-9a-f]{8}）
    if not re.fullmatch(r"[0-9a-f]{8}", alert_id):
        return (
            f"Invalid alert_id format: {alert_id!r}. Expected 8-char hex "
            f"(e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids."
        )
    # 状态层：格式合法但 sim 中不存在
    ok = deps.exchange.remove_price_level_alert(alert_id)
    if ok:
        await _record_action(
            deps, action="cancel_price_level_alert",
            reasoning=f"id={alert_id} | {reasoning}",
        )
        return f"Price level alert cancelled (id={alert_id})"
    return f"Alert {alert_id} already triggered or expired"
```

Verify `import re` present at top of `tools_execution.py`. If absent, add to stdlib imports section.

- [ ] **Step 4: Run T2/T3 to verify they pass**

Run: `uv run pytest tests/test_alert_lifecycle.py::test_cancel_price_level_alert_tool_invalid_format tests/test_alert_lifecycle.py::test_cancel_price_level_alert_tool_state_not_found -v`

Expected: **BOTH PASS**.

- [ ] **Step 5: 修 display is_tool_error 测试 (T4 协议错 + T5 状态错)**

Edit `tests/test_alert_lifecycle.py:623-632`. **Replace** the existing `test_is_tool_error_cancel_alert_not_found_returns_true` with two distinct tests:

```python
def test_is_tool_error_cancel_alert_invalid_format_returns_true():
    """R2-2 T4: 协议错信息不命中 success prefix → is_tool_error=True (business rejection)。"""
    from src.cli.display import is_tool_error

    result = is_tool_error(
        tool_name="cancel_price_level_alert",
        content="Invalid alert_id format: '#1'. Expected 8-char hex (e.g. 'a3f2b8c1'). Use get_active_alerts to see current ids.",
        outcome="success",
    )
    assert result is True


def test_is_tool_error_cancel_alert_state_not_found_returns_true():
    """R2-2 T5: 状态错（已触发/过期）信息不命中 success prefix → is_tool_error=True。"""
    from src.cli.display import is_tool_error

    result = is_tool_error(
        tool_name="cancel_price_level_alert",
        content="Alert deadbeef already triggered or expired",
        outcome="success",
    )
    assert result is True
```

- [ ] **Step 6: Run T4/T5 to verify they pass**

Run: `uv run pytest tests/test_alert_lifecycle.py::test_is_tool_error_cancel_alert_invalid_format_returns_true tests/test_alert_lifecycle.py::test_is_tool_error_cancel_alert_state_not_found_returns_true -v`

Expected: **BOTH PASS**（is_tool_error 用 prefix 白名单匹配，新错误信息均不以 `Price level alert cancelled` 开头 → 仍 True）。

- [ ] **Step 7: Commit Task 2**

```bash
git add src/agent/tools_execution.py tests/test_alert_lifecycle.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-2): T2/T3 cancel 错误信息分两类 + 8-char hex 格式校验

sim #4 实证：agent 100% 从 get_active_alerts 输出复制 `#N` / `"N"`
传给 cancel_price_level_alert，永远不匹配 uuid。原错误信息
"already triggered or never existed" 把协议错和状态错合并 →
agent 误以为 alert 已触发（状态问题）→ 下 cycle 不再尝试。

修法 (C)：
- 协议层：re.fullmatch(r"[0-9a-f]{8}", alert_id) 校验 → 不通过返回
  "Invalid alert_id format: ... Use get_active_alerts to see current ids."
- 状态层：合法 hex 但 sim 中不存在 → "already triggered or expired"
- display.is_tool_error 双类信息均不命中 success prefix → True (业务拒绝)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: T1b cancel wrapper docstring 引导 + drift guard

**Files:**
- Modify: `src/agent/trader.py:558`
- Test: `tests/test_trader_agent.py` (new test appended at end)

- [ ] **Step 1: Write the failing T1b drift guard test**

Append to `tests/test_trader_agent.py` end of file:

```python
def test_cancel_price_level_alert_schema_exposes_id_format_and_source():
    """R2-2 T1b drift guard: cancel_price_level_alert wrapper docstring 必须
    暴露 alert_id 格式约束 (8-char hex) + id 来源引导 (get_active_alerts)
    给 LLM via pydantic-ai docstring sniffing。

    防 R2-2 修复回退：未来若 docstring 措辞被改弱，drift guard 立即失败。
    """
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["cancel_price_level_alert"]
    schema = tool.tool_def.parameters_json_schema

    alert_id_desc = schema["properties"]["alert_id"]["description"]
    assert "8-char hex" in alert_id_desc, \
        f"id format constraint missing from LLM-visible schema: {alert_id_desc!r}"
    assert "get_active_alerts" in alert_id_desc, \
        f"id source guidance missing from LLM-visible schema: {alert_id_desc!r}"
```

- [ ] **Step 2: Run T1b to verify it fails**

Run: `uv run pytest tests/test_trader_agent.py::test_cancel_price_level_alert_schema_exposes_id_format_and_source -v`

Expected: **FAIL** with assertion error — current docstring `alert_id: the alert ID returned by add_price_level_alert.` 不含 `8-char hex` 也不含 `get_active_alerts`。

- [ ] **Step 3: Modify wrapper docstring**

Edit `src/agent/trader.py` line 558 (within `cancel_price_level_alert` wrapper):

```python
# Before
        Args:
            alert_id: the alert ID returned by add_price_level_alert.
            reasoning: brief description of why this alert is being cancelled.

# After
        Args:
            alert_id: 8-char hex id returned by add_price_level_alert (also visible
                in get_active_alerts output as 'id=...'). Do not use the position
                index '#N' from get_active_alerts — that is for display only.
            reasoning: brief description of why this alert is being cancelled.
```

- [ ] **Step 4: Run T1b to verify it passes**

Run: `uv run pytest tests/test_trader_agent.py::test_cancel_price_level_alert_schema_exposes_id_format_and_source -v`

Expected: **PASS**.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "$(cat <<'EOF'
feat(iter-w2r2-2): T1b cancel wrapper docstring 引导 + drift guard

trader.py:558 wrapper docstring 强化：
- 显式标 8-char hex 格式约束
- 引导 id 来源（add_price_level_alert 返回 / get_active_alerts 输出 'id=...'）
- 明示 '#N' 位置索引仅显示用，不可作 cancel 入参

drift guard 走 .tool_def.parameters_json_schema（与 R2-1 同模式），
锁住 LLM-visible schema description 防回退。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Regression sanity + AC verification

**Files:** （只跑测试，不改代码）

- [ ] **Step 1: Full pytest sweep**

Run: `uv run pytest -q 2>&1 | tail -10`

Expected: `933 passed, 3 skipped` (= 936 collected, +4 net vs baseline 932)。

净增计数详见 plan 头部 Baseline 段下表（6 新增 - 2 删除 = +4）。

- [ ] **Step 2: 协议层覆盖验证（AC1）**

Run: `uv run pytest tests/test_alert_lifecycle.py -v -k "cancel_price_level_alert" 2>&1 | tail -15`

Expected:
- `test_cancel_price_level_alert_tool_success PASSED`
- `test_cancel_price_level_alert_tool_invalid_format PASSED`
- `test_cancel_price_level_alert_tool_state_not_found PASSED`
- `test_is_tool_error_cancel_alert_success_returns_false PASSED`
- `test_is_tool_error_cancel_alert_invalid_format_returns_true PASSED`
- `test_is_tool_error_cancel_alert_state_not_found_returns_true PASSED`

- [ ] **Step 3: 显示层覆盖验证（AC2）**

Run: `uv run pytest tests/test_price_level_alert.py -v -k "displays_real_uuid" 2>&1 | tail -5`

Expected: `test_get_active_alerts_displays_real_uuid PASSED`.

- [ ] **Step 4: drift guard 验证（AC3）**

Run: `uv run pytest tests/test_trader_agent.py -v -k "exposes_id_format_and_source" 2>&1 | tail -5`

Expected: `test_cancel_price_level_alert_schema_exposes_id_format_and_source PASSED`.

- [ ] **Step 5: 与 sim #4 baseline 对照（AC4）**

手工核对：
- sim #4 中 008c/383d/8988 三个 cycle 共 19 次 cancel 全失败 → 修复后预期 agent 用 `id=<uuid>` 主显格式，cancel 成功率应回归 (W2 R2-9 重跑 smoke 验证)
- 错误信息分类后，agent 收到 `Invalid alert_id format` 应改用 `get_active_alerts` 输出的真 id 重试（不是放弃以为 alert 已触发）

记录 R2-9 smoke 验证项到 `.working/sim4-issues-inventory.md §P1-1 状态` (在 commit message / PR description 中提示，不在本 task 内动 inventory)。

- [ ] **Step 6: Commit verification 不需要单独 commit**

Task 4 仅验证，无代码改动。如需文档化 verification log，在 PR description 中列。

---

## Self-Review

**1. Spec coverage**:

| Inventory §P1-1 修法 | Plan 覆盖 |
|---|---|
| (A) 主路径：get_active_alerts 显示真实 uuid | Task 1 ✅ |
| (C) 必做：错误信息分两类（协议错 vs 状态错）| Task 2 ✅ |
| (B) 兼容路径：cancel 也接受位置索引 | **不做** — inventory 已否决（位置语义跨 cycle 漂移）|
| docstring 引导（额外加强）| Task 3 ✅ + drift guard |
| 测试覆盖（30-50 行）| Task 1/2/3 共 ~60 行 ✅ |

**2. Placeholder scan**: 无 TBD / TODO / "implement later"。所有代码块完整，所有命令含 expected output。

**3. Type consistency**:
- `alert_id: str` 三处一致（base.py:184 `str(uuid.uuid4())[:8]` / tools_execution.py:265 `alert_id: str` / trader.py:543 `alert_id: str`）
- 错误信息字符串：`"Invalid alert_id format"` / `"already triggered or expired"` / `"8-char hex"` / `"get_active_alerts"` 在 impl + 测试 + drift guard 三处文字保持一致 ✅
- `re.fullmatch(r"[0-9a-f]{8}", alert_id)` pattern 与 `str(uuid.uuid4())[:8]` 字符集严格匹配（uuid hex 都是小写）✅

**4. Net test count 校验**:
- Task 1: +1 测试（T1a get_active_alerts）
- Task 2: +2 新增（invalid_format + state_not_found）+ 2 替换（display 两类）= +2 净增（替换不算）
- Task 3: +1 测试（drift guard T1b）
- **总净增 = +4 → 932 + 4 = 936 collected**（plan 头部 / File Touch Summary / Task 4 Step 1 三处一致）✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-30-iter-w2r2-2-cancel-alert-state.md`.

**两个执行选项**:

**1. Subagent-Driven (recommended)** — 每 Task 派一个 fresh subagent，task 间 review，快速迭代

**2. Inline Execution** — 本 session 内执行 Tasks，checkpoint review

**纪律**: 用户审阅 plan 后才进 execution（memory `feedback_review_before_commit`）。

