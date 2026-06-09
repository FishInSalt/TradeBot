# taker-flow audit remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收口 `get_taker_flow` 两轮 tool-audit（`.working/tool-audits/2026-06-07` + `2026-06-09`，sim #15）浮出的 7 条 docstring / 接口 / render 打磨议题，把 period 集补成 15m 连贯阶梯、消歧时点语义、并把"枚举漂移"病灶收口到单一来源。

**Architecture:** 全部改动围绕 `get_taker_flow` 工具链：`_TAKER_FLOW_ANCHOR`（tools_perception.py）成为 tool period 集的**单一权威来源**——reject 消息、`fetch_taker_flow` 的三处 `Literal`、code 注释一律派生/对齐它（不再各自硬编码 `{5m,1h,4h,1d}`）。render 改动只动 `_render_taker_flow` 的两个分支（closed row1 标注 / in-progress Now 行 partial），不碰核心计算（审计已验证 0 计算议题）。docstring 改 LLM-facing wrapper（`trader.py` PATH A，griffe channel）。无算法改动，src 净改动 ~28 行。

**Tech Stack:** Python 3 / pydantic-ai（@tool wrapper + griffe docstring channel）/ pytest（`tests/test_taker_flow.py` + `tests/test_display_cycle.py` round-trip）/ OKX rubik `taker-volume-contract` 端点（period enum 含 15m）。`from __future__ import annotations` 全开（PEP 563）→ Literal 漂移测试需 `eval(annotation_str, module_globals)` 取成员。

---

## File Structure

改动文件与职责：

- `src/integrations/exchange/base.py` — `_TAKER_VOLUME_PERIOD`（lowercase→OKX 端点 period 映射）+ `fetch_taker_flow` 抽象契约 `Literal`。+15m。
- `src/integrations/exchange/simulated.py` — sim 活跃路径 `fetch_taker_flow` `Literal`。+15m。
- `src/integrations/exchange/okx.py` — live 路径 `fetch_taker_flow` `Literal`。+15m（一致性，实盘暂不接入）。
- `src/agent/tools_perception.py` — `_TAKER_FLOW_PERIOD_MS` / `_TAKER_FLOW_ANCHOR`（单一来源）/ reject 派生 / 两处注释去枚举（I-9/F1）/ `_render_taker_flow` 的 I-6（closed row1）+ I-8（in-progress Now 行）/ impl 签名 default。
- `src/agent/trader.py` — `get_taker_flow` @tool wrapper docstring（LLM channel：period 枚举 +15m / anchor 去枚举 / Example closed 化）+ 签名 default 6→12。
- `tests/test_taker_flow.py` — 主测试集（period/anchor/reject/accept/Literal-drift/docstring/I-6/I-8/I-7）。
- `tests/test_display_cycle.py` — render round-trip（仅一处改名）。

**不改**（已核验）：`src/integrations/market_data.py:36` `get_taker_flow(period: str = "5m")` 用 `str` 非 `Literal`、且默认值是内部直传（agent 永远显式传 limit）→ 无需改；exchange 层 `fetch_taker_flow` 的 `limit` default 保持 6（始终被显式 `n` 调用，agent 不经过它）。

---

## Task 1: 15m 成为合法 tool period（端到端常量层 + reject 派生 + 注释去枚举）

**议题:** I-5（period 补 15m 成连贯阶梯）+ I-9/F1（reject + 注释去枚举，收口到单一来源）。

**Files:**
- Modify: `src/integrations/exchange/base.py:19-23`
- Modify: `src/agent/tools_perception.py:1124-1130`（常量 + 注释）
- Modify: `src/agent/tools_perception.py:1283-1284`（reject）
- Test: `tests/test_taker_flow.py`（更新 2 个既有 + 新增 3 个）

**背景约束（必读）:** 加 15m 进 `_TAKER_FLOW_ANCHOR` 后，reject 检查 `period not in _TAKER_FLOW_ANCHOR` 立即接受 15m。既有 `test_get_taker_flow_rejects_bad_period`（断言 15m 被拒）会因此失败，故**必须在本 task 同步替换**为用 `30m`（仍非法）+ 派生消息断言。reject 消息派生自 `_TAKER_FLOW_ANCHOR` keys（Python dict 保插入序 → 输出 `5m, 15m, 1h, 4h, 1d`）。

- [ ] **Step 1: 更新既有测试 `test_taker_volume_period_map_is_complete`（断言新 period map 含 15m）**

`tests/test_taker_flow.py:20-27` 整体替换为：

```python
def test_taker_volume_period_map_is_complete():
    """§3.1/§3.3/③ + iter-taker-flow-audit-remediation I-5: distinct from
    _OKX_OI_PERIOD; covers tool periods {5m,15m,1h,4h,1d} PLUS the 1w anchor up-tier.
    Reusing _OKX_OI_PERIOD would KeyError on 15m/4h/1w."""
    from src.integrations.exchange.base import _TAKER_VOLUME_PERIOD, _OKX_OI_PERIOD
    assert _TAKER_VOLUME_PERIOD == {
        "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    assert _TAKER_VOLUME_PERIOD is not _OKX_OI_PERIOD
    for p in ("5m", "15m", "1h", "4h", "1d", "1w"):
        assert p in _TAKER_VOLUME_PERIOD
```

- [ ] **Step 2: 替换既有测试 `test_get_taker_flow_rejects_bad_period`（15m 现合法 → 改 30m + 派生消息）**

`tests/test_taker_flow.py:313-317` 整体替换为：

```python
@pytest.mark.asyncio
async def test_get_taker_flow_rejects_bad_period_derived_message():
    """I-9/F1: an out-of-set period is rejected fact-only, and the message is DERIVED
    from the single source of truth (_TAKER_FLOW_ANCHOR keys), not a hardcoded enum.
    15m is now a valid tool period (see test_get_taker_flow_accepts_15m_period); 30m
    stays invalid (deliberate ladder subset — 30m would compress the bottom step to
    ×2, principle 4)."""
    from src.agent.tools_perception import get_taker_flow, _TAKER_FLOW_ANCHOR
    out = await get_taker_flow(_deps_with_taker({}), period="30m")
    assert "Invalid period '30m'" in out
    assert f"period must be one of: {', '.join(_TAKER_FLOW_ANCHOR)}" in out
    assert "5m, 15m, 1h, 4h, 1d" in out   # insertion-ordered dict -> natural ladder order
```

- [ ] **Step 3: 新增测试 — 15m 端到端被接受 + period_ms/anchor 常量**

追加到 `tests/test_taker_flow.py`（紧跟 Step 2 的测试之后）：

```python
def test_taker_flow_15m_period_ms_and_anchor():
    """I-5: 15m added as a first-class tool period. period_ms = 15min; the wide-error
    anchor routes 15m to the SAME 1h context as the 5m main frame (every fine-grained
    frame reads the hour-scale context); 5m->1h is unchanged (no regression). Anchor
    keys ARE the valid tool periods."""
    from src.agent.tools_perception import _TAKER_FLOW_PERIOD_MS, _TAKER_FLOW_ANCHOR
    assert _TAKER_FLOW_PERIOD_MS["15m"] == 900_000
    assert _TAKER_FLOW_ANCHOR["15m"] == "1h"
    assert _TAKER_FLOW_ANCHOR["5m"] == "1h"
    assert set(_TAKER_FLOW_ANCHOR) == {"5m", "15m", "1h", "4h", "1d"}


@pytest.mark.asyncio
async def test_get_taker_flow_accepts_15m_period():
    """I-5: period=15m no longer rejected; renders the normal report (header names the
    15m bar size; anchor fetches the 1h up-tier)."""
    from src.agent.tools_perception import get_taker_flow
    deps = _deps_with_taker({"15m": _live_bars(21, 900_000), "1h": _live_bars(2, 3_600_000)})
    out = await get_taker_flow(deps, "15m", 6)
    assert "Invalid period" not in out
    assert "=== Taker Flow (BTC/USDT:USDT · 15m bars · @" in out
    assert "Per-bar" in out
```

- [ ] **Step 4: 跑测试确认失败（红）**

Run: `python -m pytest tests/test_taker_flow.py::test_taker_volume_period_map_is_complete tests/test_taker_flow.py::test_get_taker_flow_rejects_bad_period_derived_message tests/test_taker_flow.py::test_taker_flow_15m_period_ms_and_anchor tests/test_taker_flow.py::test_get_taker_flow_accepts_15m_period -v`

Expected: FAIL —`_TAKER_VOLUME_PERIOD` 缺 15m / `_TAKER_FLOW_PERIOD_MS` KeyError `'15m'` / `_TAKER_FLOW_ANCHOR` 缺 15m / reject 消息不含 `15m`。

- [ ] **Step 5: 实现 — `base.py` period map + 注释 +15m**

`src/integrations/exchange/base.py:19-23`，把：

```python
# taker-volume rubik endpoint period map. DELIBERATELY distinct from
# _OKX_OI_PERIOD: the legal period set differs (taker flow exposes 4h + 1w; OI
# does not), so reusing _OKX_OI_PERIOD would KeyError on 4h/1w. 1w is included
# only as the 1d-period anchor up-tier (§3.3), not as a standalone tool period.
_TAKER_VOLUME_PERIOD = {"5m": "5m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
```

替换为：

```python
# taker-volume rubik endpoint period map. DELIBERATELY distinct from
# _OKX_OI_PERIOD: the legal period set differs (taker flow exposes 15m + 4h + 1w;
# OI does not), so reusing _OKX_OI_PERIOD would KeyError on 15m/4h/1w. 1w is
# included only as the 1d-period anchor up-tier (§3.3), not a standalone tool period.
_TAKER_VOLUME_PERIOD = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
```

（OKX `taker-volume-contract` 端点 15m 用小写，与 5m 一致。）

- [ ] **Step 6: 实现 — `tools_perception.py` period_ms + anchor + 注释去枚举（I-9/F1）**

`src/agent/tools_perception.py:1123-1130`，把：

```python
# --- taker_flow (get_taker_flow) constants + helpers (spec §3.1-3.3) ---
_TAKER_FLOW_PERIOD_MS = {
    "5m": 5 * 60_000, "1h": 60 * 60_000, "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000, "1w": 7 * 24 * 60 * 60_000,
}
# context-anchor up-tier on the 5m->1h->4h->1d->1w ladder (§3.3). Keys are also
# the exact set of valid *tool* periods ({5m,1h,4h,1d}); 1w is anchor-only.
_TAKER_FLOW_ANCHOR = {"5m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}
```

替换为：

```python
# --- taker_flow (get_taker_flow) constants + helpers (spec §3.1-3.3) ---
_TAKER_FLOW_PERIOD_MS = {
    "5m": 5 * 60_000, "15m": 15 * 60_000, "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000, "1d": 24 * 60 * 60_000, "1w": 7 * 24 * 60 * 60_000,
}
# context-anchor up-tier on the 5m->15m->1h->4h->1d->1w ladder (§3.3). Keys are the
# SINGLE SOURCE OF TRUTH for the valid *tool* periods — the reject message and the
# fetch_taker_flow Literals derive from them, so adding a period here can't leave a
# stale enum behind. 1w is anchor-only (an up-tier, not a standalone tool period).
_TAKER_FLOW_ANCHOR = {"5m": "1h", "15m": "1h", "1h": "4h", "4h": "1d", "1d": "1w"}
```

- [ ] **Step 7: 实现 — reject 派生（去硬编码枚举，I-9/F1 病根）**

`src/agent/tools_perception.py:1282-1284`，把：

```python
    # Fact-only explicit reject (no clamp, no Literal narrowing — soft-constraint §1/§2)
    if period not in _TAKER_FLOW_ANCHOR:  # valid tool periods == {5m,1h,4h,1d}
        return f"Invalid period '{period}'. period must be one of: 5m, 1h, 4h, 1d"
```

替换为：

```python
    # Fact-only explicit reject (no clamp, no Literal narrowing — soft-constraint §1/§2)
    if period not in _TAKER_FLOW_ANCHOR:  # anchor keys ARE the valid tool periods
        return (f"Invalid period '{period}'. period must be one of: "
                f"{', '.join(_TAKER_FLOW_ANCHOR)}")
```

- [ ] **Step 8: 跑测试确认通过（绿）**

Run: `python -m pytest tests/test_taker_flow.py -v`
Expected: PASS（全部，含新增 4 个 + 既有未触及的）。

- [ ] **Step 9: Commit**

```bash
git add src/integrations/exchange/base.py src/agent/tools_perception.py tests/test_taker_flow.py
git commit -m "$(cat <<'EOF'
feat(taker-flow): 15m 成合法 period + reject/注释去枚举到单一来源 (I-5/I-9)

period 集 {5m,1h,4h,1d} -> {5m,15m,1h,4h,1d}（1w 仍 anchor-only），填掉 5m->1h
的 ×12 空洞成连贯阶梯。reject 消息与注释不再硬编码 {5m,1h,4h,1d}，一律派生/对齐
_TAKER_FLOW_ANCHOR keys（单一权威来源，根治枚举漂移）。15m anchor 接 1h（宽错 anchor）。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `fetch_taker_flow` Literal +15m（三处）+ drift 守护 pin

**议题:** I-5/I-9（Literal 层与单一来源同步）+ F2（Literal drift pin）。

**Files:**
- Modify: `src/integrations/exchange/base.py:181`
- Modify: `src/integrations/exchange/simulated.py:1042`
- Modify: `src/integrations/exchange/okx.py:856`
- Test: `tests/test_taker_flow.py`（新增 drift 测试）

**背景:** `Literal` 是纯类型注解，项目无 mypy/CI 类型 gate（已核 `pyproject.toml`）→ 不加 15m 也不破运行/CI；但留旧 `Literal` = stale enum，与 I-9 去枚举自相矛盾。drift 测试把这三处手工维护的 Literal 纳入与单一来源同步的守护网。

- [ ] **Step 1: 新增 drift 守护测试**

追加到 `tests/test_taker_flow.py`（紧跟 `test_base_exchange_has_fetch_taker_flow_abstractmethod` 之后，与其它结构性测试同组）：

```python
def test_fetch_taker_flow_period_literal_matches_anchor_single_source():
    """F2/I-9: the hand-maintained `period: Literal[...]` on all three fetch_taker_flow
    signatures (base contract + sim active path + okx live) must stay in lockstep with
    the single source of truth. The fetchable set = tool periods (anchor keys) PLUS the
    anchor up-tiers (anchor values) = {5m,15m,1h,4h,1d} ∪ {1h,4h,1d,1w}. Pins the
    Literals into the same drift-guard net as the reject message — the exact stale-enum
    bug this iter fixes can't recur."""
    import inspect, sys, typing
    from src.agent.tools_perception import _TAKER_FLOW_ANCHOR
    from src.integrations.exchange.base import BaseExchange
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.okx import OKXExchange
    expected = set(_TAKER_FLOW_ANCHOR) | set(_TAKER_FLOW_ANCHOR.values())
    assert expected == {"5m", "15m", "1h", "4h", "1d", "1w"}  # explicit for the reader
    for cls in (BaseExchange, SimulatedExchange, OKXExchange):
        ann = inspect.signature(cls.fetch_taker_flow).parameters["period"].annotation
        # PEP 563 (from __future__ import annotations) makes `ann` a string. eval ONLY
        # the period annotation (not the whole signature -> avoids resolving the
        # list["TakerFlowBar"] return forward-ref). Inject Literal defensively so this
        # works regardless of whether the module imports it at top level.
        ns = {**vars(sys.modules[cls.__module__]), "Literal": typing.Literal}
        period_type = eval(ann, ns)
        assert set(typing.get_args(period_type)) == expected, \
            f"{cls.__name__}.fetch_taker_flow period Literal drift: {ann!r}"
```

- [ ] **Step 2: 跑测试确认失败（红）**

Run: `python -m pytest tests/test_taker_flow.py::test_fetch_taker_flow_period_literal_matches_anchor_single_source -v`
Expected: FAIL — Literal 现为 `{5m,1h,4h,1d,1w}`（缺 15m），`expected` 含 15m → 断言不等。

- [ ] **Step 3: 实现 — 三处 Literal +15m**

`src/integrations/exchange/base.py:181`，把 `period: Literal["5m", "1h", "4h", "1d", "1w"] = "5m",` 改为：

```python
        period: Literal["5m", "15m", "1h", "4h", "1d", "1w"] = "5m",
```

`src/integrations/exchange/simulated.py:1042`，同样改为：

```python
        period: Literal["5m", "15m", "1h", "4h", "1d", "1w"] = "5m",
```

`src/integrations/exchange/okx.py:856`，同样改为：

```python
        period: Literal["5m", "15m", "1h", "4h", "1d", "1w"] = "5m",
```

- [ ] **Step 4: 跑测试确认通过（绿）**

Run: `python -m pytest tests/test_taker_flow.py -v`
Expected: PASS（drift 测试通过 + 既有 `test_base_exchange_has_fetch_taker_flow_abstractmethod` 仍绿——它只断 default，未断 Literal 成员）。

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/base.py src/integrations/exchange/simulated.py src/integrations/exchange/okx.py tests/test_taker_flow.py
git commit -m "$(cat <<'EOF'
feat(taker-flow): fetch_taker_flow Literal +15m 三处 + drift 守护 pin (F2/I-9)

base 契约 / sim 活跃路径 / okx live 三处 period Literal 加 15m，与单一来源
_TAKER_FLOW_ANCHOR 同步。新增 drift 测试断言三处 Literal == anchor keys ∪ values
（{5m,15m,1h,4h,1d,1w}），把手工 Literal 纳入防漂移网。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: I-6 render — closed row1 标注 rubik publish-lag

**议题:** I-6（P2 · agent-facing）。rubik 发布滞后使 row1（"latest closed bar"）常比 GMD OHLCV 最新已收盘 bar 晚 ~1 根；Close 列邀请 agent 跨源比对 → 误判 timing/join bug（sim #15: 9/1802 blocks 误报）。

**Files:**
- Modify: `src/agent/tools_perception.py:1232`（`row1_state` closed 分支）
- Test: `tests/test_taker_flow.py`（新增 I-6 专测）

**落点决策（plan 定）:** 采用 spec §3 option (a)——直接改 `row1_state` 的 closed 值，内联到既有 `Per-bar (...; row 1 = {row1_state}):` 行，用 em-dash 无嵌套括号（满足 spec 约束②）。仅 closed 分支；in-progress 分支与 Close 列均不动。

- [ ] **Step 1: 新增 I-6 专测**

追加到 `tests/test_taker_flow.py`（紧跟既有 `test_render_taker_flow_closed_newest_header_not_in_progress` 之后）：

```python
def test_render_taker_flow_closed_row1_notes_publish_lag():
    """I-6: when the newest returned bar is already closed (rubik publish-lag), the
    per-bar row-1 label flags that rubik can lag the candle/ticker by ~1 bar — so the
    agent does NOT cross-check the Close column against GMD's newer closed bar and
    misreport a 'timestamp/join bug' (sim #15: 9/1802 false reports, e.g. L37769 /
    L72185). In-progress renders keep the plain 'current in-progress' label and gain
    no lag note."""
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    # closed newest: opened 7min before now (period 5min) -> closed in publish-lag window
    bars_cl = _bars(21, period_ms, base_open=now - 120_000 - 21 * period_ms)
    out_cl = _render_taker_flow(bars_cl, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "row 1 = latest closed bar — rubik may lag candle/ticker by ~1 bar" in out_cl
    # in-progress branch unchanged
    bars_ip = _bars(21, period_ms, base_open=now - 120_000 - 20 * period_ms)
    out_ip = _render_taker_flow(bars_ip, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "row 1 = current in-progress" in out_ip
    assert "rubik may lag" not in out_ip
```

- [ ] **Step 2: 跑测试确认失败（红）**

Run: `python -m pytest tests/test_taker_flow.py::test_render_taker_flow_closed_row1_notes_publish_lag -v`
Expected: FAIL — closed `row1_state` 现为 `"latest closed bar"`，无 publish-lag 标注。

- [ ] **Step 3: 实现 — closed `row1_state` 加 publish-lag 标注**

`src/agent/tools_perception.py:1232`，把：

```python
    row1_state = "current in-progress" if is_in_progress else "latest closed bar"
```

替换为：

```python
    row1_state = ("current in-progress" if is_in_progress
                  else "latest closed bar — rubik may lag candle/ticker by ~1 bar")
```

- [ ] **Step 4: 跑测试确认通过（绿）+ 既有 closed 测试不回归**

Run: `python -m pytest tests/test_taker_flow.py::test_render_taker_flow_closed_row1_notes_publish_lag tests/test_taker_flow.py::test_render_taker_flow_closed_newest_header_not_in_progress -v`
Expected: PASS 两者。既有测试断 `"row 1 = latest closed bar" in out`（子串，仍命中）+ `"*" not in out.split("Per-bar")[1]`（标注无 `*`，仍成立）。

- [ ] **Step 5: 跑 display_cycle round-trip 不回归**

Run: `python -m pytest tests/test_display_cycle.py -k taker_flow -v`
Expected: PASS（全部 render round-trip 均为 in-progress 场景，I-6 只动 closed 分支，不受影响）。

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/test_taker_flow.py
git commit -m "$(cat <<'EOF'
feat(taker-flow): closed row1 标注 rubik publish-lag (I-6)

newest 已收盘时（rubik 发布滞后），per-bar row1 标签提示 rubik 可能比
candle/ticker 晚 ~1 根，消除 agent 跨 Close 列误判 timing/join bug（sim #15
9/1802 误报）。em-dash 无嵌套括号；仅 closed 分支，in-progress/Close 列不动。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: I-8 render — in-progress Now 行标注 partial bar

**议题:** I-8（P3）。in-progress bar 的 RVol = 部分成交量 ÷ 20 根整 bar 均值，bar 早期机械偏低（非真缩量）；现仅 `*` 脚注说 "still forming"，未点明 RVol 是部分量。

**Files:**
- Modify: `src/agent/tools_perception.py:1197-1198`（`rvol_now` 构造）
- Test: `tests/test_taker_flow.py`（新增 I-8 专测）

**改动:** 复用 GMD 的 "partial bar" 术语（`tools_perception.py:212` 用了该词；只借术语，不照搬 GMD "excluded from all indicators" 整句——taker_flow 仍展示在制 bar）。仅 `is_in_progress` 为真时在 Now 行 RVol 括号内追加 `; partial bar`；closed 不变。

- [ ] **Step 1: 新增 I-8 专测**

追加到 `tests/test_taker_flow.py`（紧跟 Task 3 的 I-6 测试之后）：

```python
def test_render_taker_flow_in_progress_now_line_flags_partial_bar():
    """I-8: an in-progress bar's RVol is partial volume / a full 20-bar average, so it
    reads mechanically low early in the bar (not real contraction). The Now line's RVol
    says 'partial bar' so the agent does not misread it as a volume drop. Closed bars
    (91-98% mainstream) are unaffected."""
    from src.agent.tools_perception import _render_taker_flow
    period_ms = 300_000
    now = 1_000_000_000_000
    # in-progress: newest bar opened 1min before now (period 5min)
    bars_ip = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    out_ip = _render_taker_flow(bars_ip, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "(vs 20-bar avg; partial bar)" in out_ip
    # closed newest: opened 6min before now -> closed, plain RVol label
    bars_cl = _bars(21, period_ms, base_open=now - 60_000 - 21 * period_ms)
    out_cl = _render_taker_flow(bars_cl, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00")
    assert "partial bar" not in out_cl
    assert "(vs 20-bar avg)" in out_cl
```

- [ ] **Step 2: 跑测试确认失败（红）**

Run: `python -m pytest tests/test_taker_flow.py::test_render_taker_flow_in_progress_now_line_flags_partial_bar -v`
Expected: FAIL — in-progress Now 行现为 `(vs 20-bar avg)`，无 `; partial bar`。

- [ ] **Step 3: 实现 — `rvol_now` 构造按 in-progress 加 partial 后缀**

`src/agent/tools_perception.py:1197-1198`，把：

```python
    now_rvol = (_total(newest) / baseline_avg) if baseline_avg else None
    rvol_now = f"{now_rvol:.1f}× (vs {_TAKER_FLOW_RVOL_BARS}-bar avg)" if now_rvol is not None else "—"
```

替换为：

```python
    now_rvol = (_total(newest) / baseline_avg) if baseline_avg else None
    # in-progress RVol is partial volume / a full 20-bar avg -> mechanically low early;
    # flag it so the agent does not misread it as real contraction (I-8).
    rvol_partial = "; partial bar" if is_in_progress else ""
    rvol_now = (f"{now_rvol:.1f}× (vs {_TAKER_FLOW_RVOL_BARS}-bar avg{rvol_partial})"
                if now_rvol is not None else "—")
```

（`is_in_progress` 已在 `:1168` 计算，早于此处。当 `now_rvol is None`（<20 closed bars 降级）时仍输出 `—`，无 partial 后缀——与既有 `test_render_taker_flow_rvol_degrades_below_20_closed` 一致。）

- [ ] **Step 4: 跑测试确认通过（绿）+ 既有 in-progress 测试不回归**

Run: `python -m pytest tests/test_taker_flow.py -v`
Expected: PASS（既有 `test_render_taker_flow_now_line_and_in_progress` 不断 Now 行 vol 串、`test_render_taker_flow_rvol_*` 不受影响）。

- [ ] **Step 5: 跑 display_cycle in-progress round-trip 不回归**

Run: `python -m pytest tests/test_display_cycle.py -k taker_flow -v`
Expected: PASS（`test_taker_flow_full_kept_in_progress_footnote_and_star` 断 `"row 1 = current in-progress"` + `"still forming"`，不断 Now 行 vol 串 → 不受 partial 后缀影响）。

- [ ] **Step 6: Commit**

```bash
git add src/agent/tools_perception.py tests/test_taker_flow.py
git commit -m "$(cat <<'EOF'
feat(taker-flow): in-progress Now 行 RVol 标注 partial bar (I-8)

在制 bar 的 RVol = 部分量 ÷ 20 根整 bar 均值，bar 早期机械偏低（非真缩量）。
Now 行 RVol 括号内仅 in-progress 时追加 '; partial bar'（复用 GMD partial 术语），
消除潜在误读；closed 主流（91-98%）不变。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: docstring I-2/I-9 + default limit 6→12（I-3）+ display 测试改名

**议题:** I-2（Example closed 主流化）+ I-3（default limit 6→12，对齐实测主流，token 影响=零）+ I-9（anchor 描述去枚举）。

**Files:**
- Modify: `src/agent/trader.py:431`（wrapper 签名 default）+ `:432-459`（wrapper docstring）
- Modify: `src/agent/tools_perception.py:1271`（impl 签名 default）
- Modify: `tests/test_display_cycle.py:3683`（改名，去 stale "default"）
- Test: `tests/test_taker_flow.py`（新增 2 个 docstring/default 测试）

**LLM channel 事实（已实测）:** griffe 把 Args 解析进 `parameters_json_schema`（每参数 `description` + `default`），不进 `tool_def.description`；`tool_def.description` = summary（pre-Args）+ Returns 块。故 period 枚举的 LLM-facing 断言：summary 串打 `tool_def.description`、Args 串打 `schema["properties"][...]["description"]`、default 打 `schema[...]["default"]`。Returns 首行须保持**无冒号**（否则 griffe google-style 劈成 `<type>`）。

- [ ] **Step 1: 新增 docstring 内容 + default 测试**

追加到 `tests/test_taker_flow.py`（紧跟既有 `test_get_taker_flow_docstring_row1_state_is_fact_only` 之后）：

```python
def test_get_taker_flow_default_limit_is_12_both_signatures():
    """I-3: tool default limit 6 -> 12 (sim #15: limit=12 = 65.1% of calls vs 6 = 10.4%;
    principle 5 'default 反映实测主流'). The wrapper (LLM-facing, drives the JSON-schema
    default) and the impl signature must agree. Exchange-layer fetch_taker_flow default
    stays 6 (always called with an explicit n, never the agent's default)."""
    import inspect
    from src.agent.tools_perception import get_taker_flow as impl
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig
    assert inspect.signature(impl).parameters["limit"].default == 12
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    sch = agent._function_toolset.tools["get_taker_flow"].tool_def.parameters_json_schema
    assert sch["properties"]["limit"]["default"] == 12


def test_get_taker_flow_docstring_reflects_15m_default12_and_closed_example():
    """I-2/I-3/I-9 LLM channel: the description must (a) list 15m in the summary period
    enum and the Args (JSON-schema) enum, (b) state default limit 12 in Args, (c) show a
    closed-form Example (closed = 91-98% mainstream; no in-progress star/footnote, row1
    carries the I-6 publish-lag note), (d) drop the stale 'same-period 1h/4h/1d' anchor
    enumeration. Assert tool_def.description / parameters_json_schema (LLM channels), not
    the impl __doc__ (memory project_tool_docstring_llm_channel)."""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    td = agent._function_toolset.tools["get_taker_flow"].tool_def
    desc = td.description or ""
    norm = " ".join(desc.split())                       # collapse line-wraps
    sch = td.parameters_json_schema
    # (a) summary period enum +15m; (d) stale anchor enumeration gone
    assert "period one of 5m/15m/1h/4h/1d" in norm
    assert "same-period 1h/4h/1d" not in norm
    assert "coarser-tier context-anchor" in norm
    # (a)/(b) Args (per-param JSON-schema description) reflects 15m + default 12
    assert sch["properties"]["period"]["description"] == \
        'bar size, one of "5m", "15m", "1h", "4h", "1d" (default "5m").'
    assert "(default 12)" in sch["properties"]["limit"]["description"]
    # (c) Returns Example is closed-form mainstream
    assert "Now (current 5m, closed):" in desc
    assert "Window (12 bars = 60min):" in desc
    assert "row 1 = latest closed bar — rubik may lag candle/ticker by ~1 bar" in desc
    assert "still forming" not in desc                  # no in-progress footnote
    assert "4.0/5min formed" not in desc                # old in-progress Now value gone
```

- [ ] **Step 2: 跑测试确认失败（红）**

Run: `python -m pytest tests/test_taker_flow.py::test_get_taker_flow_default_limit_is_12_both_signatures tests/test_taker_flow.py::test_get_taker_flow_docstring_reflects_15m_default12_and_closed_example -v`
Expected: FAIL — default 现 6 / 描述现 `period one of 5m/1h/4h/1d` + `same-period 1h/4h/1d` + in-progress Example。

- [ ] **Step 3: 实现 — `trader.py` wrapper 签名 default + docstring 整体重写**

`src/agent/trader.py:431`，把 `async def get_taker_flow(ctx: RunContext[TradingDeps], period: str = "5m", limit: int = 6) -> str:` 改为：

```python
    async def get_taker_flow(ctx: RunContext[TradingDeps], period: str = "5m", limit: int = 12) -> str:
```

然后把 `:432-459` 的整段 docstring 替换为（summary closed-first + anchor 去枚举 + period +15m；Args +15m + default 12；Returns Example closed 化、首行保持无冒号）：

```python
        """Minute-level taker buy/sell flow: who is hitting the book over recent bars.

        Server-aggregated taker volume (USD) per bar — a minute-to-hours flow
        trend. Row 1 is the newest bar — the latest closed bar; or the current
        in-progress bar, labeled with how far it has formed, when still open. CVD is
        cumulative net taker volume across the shown window only, so do NOT compare
        CVD across separate calls (the window's oldest bar — its zero point — rolls
        forward each call). RVol is the bar's taker total vs a fixed 20-closed-bar
        average. A coarser-tier context-anchor line shows the larger bar's
        current direction. period one of 5m/15m/1h/4h/1d; limit 1..36 bars.

        Args:
            period: bar size, one of "5m", "15m", "1h", "4h", "1d" (default "5m").
            limit: number of bars to show, 1..36 (default 12).

        Returns:
            A taker-flow report (fact-only text) for the given period and limit. Example output follows.
            === Taker Flow (BTC-USDT-SWAP · 5m bars · @ 04:34:07 UTC) ===
            Now (current 5m, closed):  52% taker buy · net +1.2$M · vol 1.0× (vs 20-bar avg)
            Window (12 bars = 60min):  CVD +8.4$M · 5/12 bars net-sell
            Per-bar (bar open UTC, newest first; row 1 = latest closed bar — rubik may lag candle/ticker by ~1 bar):
              Time     Buy%   Net($M)   RVol(×20-bar)   CVD($M)   Close
              04:30     52%     +1.2    1.0×     +8.4    73531
              ... (older bars) ...
            1h-scale anchor (current 1h, 34min formed):  53% buy · net +62$M
        """
```

（F3：Example 用代表性 closed 值——RVol ~1.0×、buy 52%/net +1.2$M 内部自洽，row1 CVD +8.4 = window CVD；anchor 故意保留 in-progress 形态以示两态并存。）

- [ ] **Step 4: 实现 — impl 签名 default 6→12**

`src/agent/tools_perception.py:1271`，把 `async def get_taker_flow(deps: TradingDeps, period: str = "5m", limit: int = 6) -> str:` 改为：

```python
async def get_taker_flow(deps: TradingDeps, period: str = "5m", limit: int = 12) -> str:
```

- [ ] **Step 5: 实现 — `test_display_cycle.py` 去 stale "default" 改名**

`src/agent/...` 不变。`tests/test_display_cycle.py:3683-3694`，把：

```python
def test_taker_flow_section_full_kept_default_limit_6():
    """附带受益：默认 limit=6（body ≥10 行）当前也被 Branch 2 折叠，新设计下全保留。"""
```

替换为（去掉 "default" —— tool 默认已改 12，此用例仅守 6-bar 渲染整段保留）：

```python
def test_taker_flow_section_full_kept_limit_6():
    """limit=6（body ≥10 行）的渲染整段保留、无折叠（旧 Branch 2 会折叠）。
    注：tool 默认 limit 现为 12（iter-taker-flow-audit-remediation I-3），此用例守
    更短 6-bar 输出的同等行为。"""
```

（函数体不变——它显式传 `_render_taker_flow(bars, "5m", 6, ...)`，渲染层默认无关。）

- [ ] **Step 6: 跑测试确认通过（绿）+ 既有 docstring 测试不回归**

Run: `python -m pytest tests/test_taker_flow.py tests/test_display_cycle.py -k "taker_flow or taker" -v`
Expected: PASS。重点核既有不回归：
- `test_get_taker_flow_returns_example_not_mangled_into_pseudo_type`：Returns 首行仍无冒号、header `=== Taker Flow (BTC-USDT-SWAP · 5m bars · @` 仍在 → 绿。
- `test_get_taker_flow_docstring_row1_state_is_fact_only`：`"Row 1 is the newest bar"` + `"latest closed bar"` 在、`"Row 1 is the current in-progress bar"` 不在 → 绿。
- `test_order_flow_wrappers_fact_only_no_imperative_cross_routing`：无 imperative / cross-routing → 绿。

- [ ] **Step 7: Commit**

```bash
git add src/agent/trader.py src/agent/tools_perception.py tests/test_taker_flow.py tests/test_display_cycle.py
git commit -m "$(cat <<'EOF'
feat(taker-flow): docstring Example closed 化 + default limit 6->12 + anchor 去枚举 (I-2/I-3/I-9)

I-3: tool default limit 6->12（sim #15 实测 12 占 65.1% / 6 占 10.4%，原则 5；agent
100% 显式传 limit 故 token 影响=零，纯 cosmetic 对齐声明默认）。I-2: wrapper docstring
Example 改 closed 主流形态（91-98% 实测），代表性 ~1.0× 值 + row1 带 I-6 publish-lag 注。
I-9: anchor 描述去枚举（'same-period 1h/4h/1d' -> 'coarser-tier'）。display 测试去 stale 名。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: I-7 anchor $K/$M scale 守护测试（0 渲染改动）

**议题:** I-7（P3）。anchor 行用独立 `_pick_usd_scale([up_net])`，同一 render 内可能 $M 主表 + $K anchor 混排。**决策（brainstorm）：不硬修**——anchor 是 1h/4h bar 量级常比 5m 大 ~10×，强制共享主表 scale 会渲染成难读形态；独立 scale 恰为各行可读，显式 `$K`/`$M` 后缀已是缓解。本 task 只加守护测试把"隐患"转成"已审定 + 守护"。

**Files:**
- Test: `tests/test_taker_flow.py`（新增 I-7 守护测试，无 src 改动）

- [ ] **Step 1: 新增 I-7 守护测试**

追加到 `tests/test_taker_flow.py`（紧跟既有 `test_render_taker_flow_anchor_line_when_provided_and_absent_when_none` 之后）：

```python
def test_render_taker_flow_anchor_always_carries_usd_scale_suffix():
    """I-7 guard (audit-accepted, not fixed): the up-tier anchor line uses an
    independent $K/$M scale (decoupled from the main column so a ~10x-larger 1h/4h bar
    stays readable), so it MUST always carry an explicit $K or $M suffix — that suffix
    is the only thing preventing a 1000x misread when the anchor renders at a different
    scale than the main table. Pin it so the suffix can never be dropped."""
    import re
    from src.agent.tools_perception import _render_taker_flow
    from src.integrations.exchange.base import TakerFlowBar
    period_ms = 300_000
    now = 1_000_000_000_000
    bars = _bars(21, period_ms, base_open=now - 60_000 - 20 * period_ms)
    cases = (
        (TakerFlowBar(ts=now - 34 * 60_000, sell_usd=120_000.0, buy_usd=80_000.0), "$K"),    # |net|=40K
        (TakerFlowBar(ts=now - 34 * 60_000, sell_usd=8_000_000.0, buy_usd=2_000_000.0), "$M"),  # |net|=6M
    )
    for anchor_bar, expect in cases:
        out = _render_taker_flow(bars, "5m", 6, now_ms=now, symbol="X", fetch_ts="00:00",
                                 anchor=("1h", anchor_bar))
        anchor_line = [ln for ln in out.splitlines() if "-scale anchor" in ln][0]
        assert re.search(r"net [+-]\d+(?:\.\d+)?\$[KM]", anchor_line), anchor_line
        assert expect in anchor_line
```

- [ ] **Step 2: 跑测试确认通过（绿，无需改 src）**

Run: `python -m pytest tests/test_taker_flow.py::test_render_taker_flow_anchor_always_carries_usd_scale_suffix -v`
Expected: PASS（现行 `_render_taker_flow` 已为 anchor 行带 `$K`/`$M` 后缀；本测试 pin 住该不变量）。

- [ ] **Step 3: Commit**

```bash
git add tests/test_taker_flow.py
git commit -m "$(cat <<'EOF'
test(taker-flow): pin anchor 行始终带 $K/$M 单位后缀 (I-7)

I-7 审定不硬修（独立 scale 为各行可读，后缀已缓解 1000x 误读）。新增守护测试
断言 anchor 行无论 $K/$M 量级都带显式单位后缀，把隐患转成已审定+守护。0 渲染改动。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: 全量回归 + pre-merge 15m 真实端点 smoke gate + direct-merge

**议题:** 收尾——全量 pytest 0 回归 + spec §7 的 pre-merge 15m 真实端点 smoke gate（外部假设验证）+ mini-iter direct-merge to main。

**Files:** 无代码改动（验证 + 合并）。

- [ ] **Step 1: 全量 pytest 0 回归**

Run: `python -m pytest -q`
Expected: PASS（仅允许预存的 alembic-CLI-not-on-PATH 无关失败，per memory `project_tradebot_status`；taker-flow 相关全绿）。若出现新失败，回到对应 task 排查，**不得**跳过。

- [ ] **Step 2: 【Pre-merge gate · 必跑、非 unit】15m 真实端点 smoke —— 由用户跑**

> **指派给用户**（理由：碰 live OKX / env 侧凭证 —— 是只读 rubik 端点的 ~2s 快调，**非** >10min 长实验，故不援引 `feedback_long_walltime_experiments`；指派给用户是因 live/env 侧而非耗时）。

请用户在 merge 前对真实 OKX 跑一次并贴回结果：

```bash
caffeinate -is python -c "
import asyncio
from src.integrations.exchange.okx import OKXExchange
from src.config import load_config

async def main():
    cfg = load_config()
    ex = OKXExchange(cfg.exchange)   # 按本地实际构造方式调整
    await ex.start()
    try:
        bars = await ex.fetch_taker_flow('BTC/USDT:USDT', '15m', 12)
        print('rows:', len(bars))
        for b in bars[-3:]:
            print(b.ts, 'sell', b.sell_usd, 'buy', b.buy_usd)
        # ts 对齐 15m 边界（900000ms）检查
        if len(bars) >= 2:
            diffs = {bars[i+1].ts - bars[i].ts for i in range(len(bars)-1)}
            print('ts deltas (ms):', diffs, '-> 15m-aligned:', diffs == {900000})
    finally:
        await ex.close()

asyncio.run(main())
"
```

验收标准（spec §7）：① 返回非空、② 行形状 `[ts, sellVol, buyVol]` 正确（sell/buy 量级合理、非交换）、③ ts 按 15m 边界对齐（相邻差恒 900000ms）。**任一不过 → §1（Task 1/2）整组回炉，不 merge。** 理由：§1 load-bearing 在"OKX rubik 真对该 instId 返回 15m 数据"这一外部假设上；context7 文档枚举 ≠ 端点实际行为，unit 全绿但端点不返回 → 下一轮 sim 静默失败。

- [ ] **Step 3: direct-merge to main（mini-iter）**

src 净改动 ~28 行 < 100 且议题简单，符合 mini-iter direct-merge（per `feedback_docs_only_direct_merge`：feature 分支 → 直 merge main，不创建 GitHub PR）。守三纪律：review-before-commit ✅（spec 已两轮审查）、tests pass ✅（Step 1/2）、memory anchor（见 Step 4）。

```bash
git checkout main
git merge --ff-only iter-taker-flow-audit-remediation
```

（若 `--ff-only` 失败说明 main 有新提交 → 先 `git rebase main` 分支再 ff-merge，per `feedback_parallel_subagent_cross_iter_tests` rebase 后 worktree 重跑 pytest。）

- [ ] **Step 4: 更新 memory anchor**

更新 `project_sim_market_data_fidelity` 的 recent_trades/taker_flow 线条目，记本 iter 收口（I-5 15m / I-2/I-3 docstring+default / I-6 publish-lag / I-7 pin / I-8 partial / I-9 去枚举单一来源），并把 audit 报告路径与 7 议题分流写入。在 `MEMORY.md` 同步一行指针（若已有相关行则更新而非新增）。
