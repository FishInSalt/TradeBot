# WebUI 收益分析观察台重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把观察台底部「收益分析」区重构为「默认折叠的底部抽屉 + 已实现指标分层 + A+ 交易历程表（按持仓周期分组、开/加/平触发细分 + 逐笔扣费的最终收益） + 未平仓时的当前持仓未实现收益条」，并暴露后端两处既有数据（`trigger_reason` + `open_position`）。

**Architecture:** 前端为主。后端仅在 `Performance` API 上暴露两处 DB 现有数据（`TradeRow.trigger_reason`、`Performance.open_position`），不动 `MetricsService`、无数据库迁移。前端新增纯函数模块 `utils/trades.ts`（持仓周期派生 + 周期级聚合，单一来源供两组件复用），重写 `TradesTable.vue`（A+ 表）与 `PerformanceBar.vue`（抽屉 + 分层 + 持仓条）。所有 per-trade 计数走 episode 口径（与表头、Tier 2 自洽）。

**Tech Stack:** 后端 FastAPI + SQLAlchemy async + pydantic v2 + pytest；前端 Vue 3 SPA + naive-ui 2.38.1（**pinned，勿 npm update**）+ Pinia + TypeScript + vitest + @vue/test-utils；类型由后端 OpenAPI 经 openapi-typescript 生成。

**Spec:** `docs/superpowers/specs/2026-06-15-webui-perf-panel-redesign-design.md`

---

## File Structure

后端：
- `src/webui/schemas.py` — `TradeRow` 增 `trigger_reason`；新增 `OpenPositionBrief`；`Performance` 增 `open_position` + `total_pnl`。
- `src/webui/queries.py` — `get_performance` 映 `trigger_reason` + `total_pnl`；新增 `_derive_open_position(sim_pos, snapshot)`：**`SimPosition` 权威**（存在性 + side/数量/入场价），未实现毛仅 snapshot 同向时借用。

类型同步（生成物）：
- `frontend/openapi.json` + `frontend/src/api/types.ts` — 重生成。
- `frontend/src/api/client.ts` — 增 `OpenPositionBrief` re-export。

前端纯函数 / 格式化：
- `frontend/src/utils/trades.ts` — **新增**：`DerivedFill` / `EpisodeSummary` 类型、`OPEN_LABEL` / `CLOSE_LABEL`、`deriveTradeFills`、`summarizeEpisodes`。
- `frontend/src/utils/format.ts` — 增 `fmtSignedPct`。

前端组件：
- `frontend/src/components/TradesTable.vue` — 重写为 A+ 交易历程表。
- `frontend/src/components/PerformanceBar.vue` — 重构为可折叠底部抽屉 + 指标分层 + 当前持仓条。
- `frontend/src/components/EquityChart.vue` — **不变**，复用。
- `frontend/src/views/DashboardView.vue` — **不变**（`.dashboard` flex 列 + `.stream-wrap` flex:1，PerformanceBar 折叠态约 40px、展开态自带 `max-height`，高度内部自管，flex 自适应；本计划末尾 Playwright 验证此前提）。

测试：
- `tests/test_webui_queries.py` — 增后端暴露测试 + drift-guard。
- `frontend/test/format.spec.ts` — 增 `fmtSignedPct` 用例。
- `frontend/test/trades.spec.ts` — **新增**：`deriveTradeFills` / `summarizeEpisodes` 单元测试。
- `frontend/test/TradesTable.spec.ts` — 重写为 A+ 表断言。
- `frontend/test/PerformanceBar.spec.ts` — 增抽屉 / 分层 / 持仓条断言。

---

## Task 1: 后端暴露 `trigger_reason` + `open_position`（SimPosition 权威）+ `total_pnl`

**Files:**
- Modify: `src/webui/schemas.py:112-139`（TradeRow + Performance + 新增 OpenPositionBrief）
- Modify: `src/webui/queries.py`（`_derive_open_position` 新增 + `get_performance` 映 trigger_reason / total_pnl / open_position）
- Test: `tests/test_webui_queries.py`

**数据源决策（review claim 2，实证 3/21 会话矛盾）：** `open_position` 的存在性 + side/contracts/entry_price 取自 **`SimPosition`（权威当前态，与 `get_live_status`/LiveStatusCard 同源）**，不取 `state_snapshot.position`（本轮开始态、会出现幻影/漏显并与同屏 LiveStatusCard 矛盾）。`SimPosition` 无 `unrealized_pnl`（已核列），故未实现毛 + % 仅当最新 cycle `state_snapshot.position` 与 `SimPosition` **同向**时借用，否则 None。`get_performance` 已查 `pos = SimPosition…first()`（queries.py:345），复用之。

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_webui_queries.py` 末尾）

```python
@pytest.mark.asyncio
async def test_get_performance_exposes_trigger_reason_and_total_pnl(engine):
    """get_performance 暴露 close fill 的 trigger_reason + total_pnl（毛额）。
    注：symbol 必填（TradeAction.symbol NOT NULL，models.py:78）；total_pnl 由 MetricsService
    按价格重算 gross =(close.price − open.entry_px)*amount*sign*contract_size（metrics.py:192-196），
    非记录的 pnl 字段——故断言用重算值。"""
    from src.storage.models import TradeAction
    await _seed_session(engine)
    async with get_session(engine) as s:
        s.add(TradeAction(session_id="s1", action="order_filled", symbol="BTC/USDT:USDT",
                          side="long", price=65000.0, amount=0.1, fee=3.0, pnl=None,
                          entry_price=None, trigger_reason="market"))
        s.add(TradeAction(session_id="s1", action="order_filled", symbol="BTC/USDT:USDT",
                          side="long", price=64900.0, amount=0.1, fee=3.0, pnl=-10.0,
                          entry_price=65000.0, trigger_reason="stop"))
        await s.commit()
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")
    assert perf.trades[1].trigger_reason == "stop"          # id ASC：开仓在前、平仓在后
    # gross =(64900−65000)*0.1*sign(long=+1)*cs(1.0, session 无 contract_size→fallback) = -10.0
    assert round(perf.total_pnl, 2) == -10.0


@pytest.mark.asyncio
async def test_open_position_sim_authoritative_with_snapshot_unrealized(engine):
    """(a) SimPosition 有仓 + snapshot 同向 → side/数量/入场价取 SimPosition、未实现借 snapshot。"""
    from src.storage.models import SimPosition
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="short",
                          contracts=10.82, entry_price=65542.1, leverage=10))
        await s.commit()
    await _add_cycle(engine, cycle_id="cz", created_at=base,
                     snapshot=json.dumps({"balance": {"total_usdt": 9440.89},
                       "position": {"side": "short", "contracts": 10.82, "entry_price": 65542.1,
                                    "unrealized_pnl": -13.97, "pnl_pct_of_notional": -0.2}}))
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")
    assert perf.open_position is not None
    assert perf.open_position.side == "short"
    assert round(perf.open_position.contracts, 2) == 10.82
    assert round(perf.open_position.unrealized_pnl, 2) == -13.97
    assert round(perf.open_position.pnl_pct_of_notional, 2) == -0.2


@pytest.mark.asyncio
async def test_open_position_sim_authoritative_snapshot_mismatch_unrealized_none(engine):
    """(b) SimPosition 有仓 + snapshot flat/异向（漏显反例）→ side/数量/入场价仍取 SimPosition、未实现 None。"""
    from src.storage.models import SimPosition
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    async with get_session(engine) as s:
        s.add(SimPosition(session_id="s1", symbol="BTC/USDT:USDT", side="short",
                          contracts=0.265, entry_price=65000.0, leverage=10))
        await s.commit()
    await _add_cycle(engine, cycle_id="cf", created_at=base,
                     snapshot=json.dumps({"balance": {"total_usdt": 9900.0}}))   # snapshot 无 position
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")
    assert perf.open_position is not None
    assert perf.open_position.side == "short"
    assert round(perf.open_position.contracts, 3) == 0.265
    assert perf.open_position.unrealized_pnl is None
    assert perf.open_position.pnl_pct_of_notional is None


@pytest.mark.asyncio
async def test_open_position_none_when_sim_flat_despite_snapshot(engine):
    """(c) SimPosition 平/空 + snapshot 有仓（幻影反例）→ open_position is None（权威）。"""
    await _seed_session(engine)
    base = datetime(2026, 6, 12, 10, 0, tzinfo=UTC)
    await _add_cycle(engine, cycle_id="cp", created_at=base,
                     snapshot=json.dumps({"balance": {"total_usdt": 10000.0},
                       "position": {"side": "long", "contracts": 0.121, "entry_price": 60000.0,
                                    "unrealized_pnl": -8.16, "pnl_pct_of_notional": -0.1}}))
    from src.webui.queries import get_performance
    perf = await get_performance(engine, "s1")     # 无 SimPosition
    assert perf.open_position is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_webui_queries.py -k "trigger_reason_and_total_pnl or open_position" -v`
Expected: FAIL — `TradeRow` 无 `trigger_reason` / `Performance` 无 `total_pnl` / 无 `open_position`（AttributeError 或 pydantic 校验缺字段）。

- [ ] **Step 3: 改 schema**（`src/webui/schemas.py`）

把 `TradeRow` 改为（在 `fee` 后加一行）：

```python
class TradeRow(BaseModel):
    at: UtcDatetime
    action: str
    side: str | None
    price: float | None
    amount: float | None
    pnl: float | None
    fee: float | None
    trigger_reason: str | None = None   # 平仓/开仓触发细分（trade_actions.trigger_reason）
```

在 `PositionBrief`（schemas.py:45-49）之后新增 `OpenPositionBrief`：

```python
class OpenPositionBrief(BaseModel):
    """当前未平仓持仓 + 未实现收益。存在性 + side/contracts/entry_price 取自 SimPosition（权威
    当前态，与 get_live_status 同源）；unrealized_pnl/pnl_pct_of_notional 仅当最新 cycle
    state_snapshot.position 与之同向时借用（SimPosition 不存未实现），否则 None。
    与 PositionBrief（feed-head 开始态、无未实现）语义不同。"""
    side: str                          # 'long' | 'short'（来自 SimPosition）
    contracts: float                   # 来自 SimPosition（权威）
    entry_price: float | None          # 来自 SimPosition
    unrealized_pnl: float | None       # 盯市 mark-vs-entry 毛额；snapshot 同向才有，否则 None
    pnl_pct_of_notional: float | None  # 未实现 / 名义本金 * 100；同上
```

在 `Performance` 的 `trades` 字段后加两行：

```python
    trades: list[TradeRow]
    open_position: OpenPositionBrief | None = None   # 未平仓时当前持仓（SimPosition 权威）；平尾 → None
    total_pnl: float = 0.0                            # 已实现毛额（MetricsService.total_pnl）；毛PnL 直取免反推
```

- [ ] **Step 4: 改 query**（`src/webui/queries.py`）

在 `_derive_position`（queries.py:226-237）之后新增辅助：

```python
def _derive_open_position(sim_pos, snapshot) -> schemas.OpenPositionBrief | None:
    """当前持仓 + 未实现毛。存在性与 side/contracts/entry_price 取自 SimPosition（权威当前态，
    与 get_live_status 同源）；unrealized_pnl/pnl_pct_of_notional 仅当最新 cycle
    state_snapshot.position 与 SimPosition 同向时借用（snapshot=本轮开始态、可能矛盾），否则 None。
    同向闸只比 side 不比 contracts（未实现本就是本轮开始态近似，可接受 size 漂移）。
    SimPosition 平/空（None 或 contracts 0）→ None。"""
    if sim_pos is None or not sim_pos.contracts:
        return None
    unrealized = pct = None
    if isinstance(snapshot, dict):
        sp = snapshot.get("position")
        if isinstance(sp, dict) and sp.get("side") == sim_pos.side:   # 同向闸
            unrealized = sp.get("unrealized_pnl")
            pct = sp.get("pnl_pct_of_notional")
    return schemas.OpenPositionBrief(
        side=sim_pos.side, contracts=sim_pos.contracts, entry_price=sim_pos.entry_price,
        unrealized_pnl=unrealized, pnl_pct_of_notional=pct,
    )
```

在 `get_performance` 里，`trades = ...` 查询块之后、`cur = _current_position_label(pos)` 之前，加一条查最新 cycle 快照（仍在 `async with get_session(engine) as s:` 块内；`pos` = 已查的 `SimPosition`，queries.py:345）：

```python
        latest_snapshot = (await s.execute(
            text("SELECT state_snapshot FROM agent_cycles WHERE session_id=:sid "
                 "ORDER BY id DESC LIMIT 1"),
            {"sid": session_id},
        )).scalar_one_or_none()
```

在 `return schemas.Performance(...)` 中：`trades=[...]` 列表推导里的 `TradeRow(...)` 加 `trigger_reason=t.trigger_reason`，并加 `open_position` + `total_pnl`：

```python
        trades=[schemas.TradeRow(at=t.created_at, action=t.action, side=t.side, price=t.price,
                                 amount=t.amount, pnl=t.pnl, fee=t.fee,
                                 trigger_reason=t.trigger_reason) for t in trades],
        open_position=_derive_open_position(pos, _loads(latest_snapshot)),
        total_pnl=m.total_pnl,
    )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `pytest tests/test_webui_queries.py -k "trigger_reason_and_total_pnl or open_position" -v`
Expected: PASS（4 passed：trigger_reason+total_pnl + open_position 三态 a/b/c）

- [ ] **Step 6: 跑 webui queries 全文件防回归**

Run: `pytest tests/test_webui_queries.py -q`
Expected: 全 PASS（含既有 `test_get_performance_equity_skips_none_balance`）。

- [ ] **Step 7: Commit**

```bash
git add src/webui/schemas.py src/webui/queries.py tests/test_webui_queries.py
git commit -m "feat(webui): Performance 暴露 trigger_reason + open_position（SimPosition 权威）+ total_pnl"
```

---

## Task 2: 重生成前端类型 + 导出 OpenPositionBrief

**Files:**
- Modify: `frontend/openapi.json`（重生成）
- Modify: `frontend/src/api/types.ts`（重生成）
- Modify: `frontend/src/api/client.ts:11`（增 export）

- [ ] **Step 1: dump 后端 OpenAPI**

Run: `python -c "import json; from src.webui.app import app; print(json.dumps(app.openapi()))" > frontend/openapi.json`
Expected: 写入成功；`grep -c OpenPositionBrief frontend/openapi.json` ≥ 1。

- [ ] **Step 2: 生成前端类型**

Run: `cd frontend && npm run gen:types && cd ..`
Expected: `frontend/src/api/types.ts` 更新；`grep -c "OpenPositionBrief\|trigger_reason" frontend/src/api/types.ts` ≥ 2。

- [ ] **Step 3: 增 client.ts 类型导出**

在 `frontend/src/api/client.ts` 的 `export type TradeRow = S["TradeRow"];`（约第 11 行）之后加一行：

```typescript
export type OpenPositionBrief = S["OpenPositionBrief"];
```

- [ ] **Step 4: 类型检查 + 前端测试防回归**

Run: `cd frontend && npx vue-tsc --noEmit && npm test && cd ..`
Expected: vue-tsc 0 错；vitest 全 PASS（新增可选字段 + 新模型向后兼容，既有组件不受影响）。

- [ ] **Step 5: Commit**

```bash
git add frontend/openapi.json frontend/src/api/types.ts frontend/src/api/client.ts
git commit -m "chore(webui): 重生成 openapi.json + 前端类型（trigger_reason / open_position）"
```

---

## Task 3: `utils/format.ts` — 带符号百分比 `fmtSignedPct`

**Files:**
- Modify: `frontend/src/utils/format.ts`（末尾追加）
- Test: `frontend/test/format.spec.ts`

- [ ] **Step 1: 写失败测试**（追加到 `frontend/test/format.spec.ts` 末尾；并把首行 import 加上 `fmtSignedPct`）

把第 2 行 import 改为含 `fmtSignedPct`：

```typescript
import { fmtTokens, fmtTokensCompact, fmtGap, fmtDuration, fmtArgs, clipArgs, fmtNum, fmtSigned, fmtSignedPct, HEAD_ARGS_MAX } from "@/utils/format";
```

追加：

```typescript
describe("fmtSignedPct", () => {
  it("正 → +N.NN%（固定两位）", () => {
    expect(fmtSignedPct(0.07)).toBe("+0.07%");
    expect(fmtSignedPct(2.5)).toBe("+2.50%");
  });
  it("负 → −N.NN%（U+2212）", () => {
    expect(fmtSignedPct(-0.95)).toBe("−0.95%");
  });
  it("null/undefined → 占位", () => {
    expect(fmtSignedPct(null)).toBe("—");
    expect(fmtSignedPct(undefined)).toBe("—");
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- format.spec && cd ..`
Expected: FAIL — `fmtSignedPct is not a function` / import 解析失败。

- [ ] **Step 3: 实现**（追加到 `frontend/src/utils/format.ts` 末尾）

```typescript
/** 带符号百分比（U+2212 负号、固定两位小数、带 %），null → —。用于 PnL%。 */
export function fmtSignedPct(n: number | null | undefined): string {
  if (n == null) return "—";
  const s = Math.abs(n).toFixed(2);
  return n < 0 ? `−${s}%` : `+${s}%`;
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- format.spec && cd ..`
Expected: PASS（含既有 format 用例）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/format.ts frontend/test/format.spec.ts
git commit -m "feat(webui): fmtSignedPct 带符号百分比格式化"
```

---

## Task 4: `utils/trades.ts` — 持仓周期派生 `deriveTradeFills` + 标签

**Files:**
- Create: `frontend/src/utils/trades.ts`
- Test: `frontend/test/trades.spec.ts`（新增）

- [ ] **Step 1: 写失败测试**（新建 `frontend/test/trades.spec.ts`）

```typescript
import { describe, it, expect } from "vitest";
import { deriveTradeFills, OPEN_LABEL, CLOSE_LABEL } from "@/utils/trades";
import type { TradeRow } from "@/api/client";

const f = (o: Partial<TradeRow>): TradeRow => ({
  at: "2026-06-12T10:00:00Z", action: "order_filled", side: "long",
  price: 50000, amount: 0.1, pnl: null, fee: 1, trigger_reason: "market", ...o,
});

describe("OPEN_LABEL / CLOSE_LABEL", () => {
  it("OPEN_LABEL：limit 区分加仓、market/未知泛标签", () => {
    expect(OPEN_LABEL("limit", false)).toBe("限价开仓");
    expect(OPEN_LABEL("limit", true)).toBe("限价加仓");
    expect(OPEN_LABEL("market", false)).toBe("开仓");
    expect(OPEN_LABEL("market", true)).toBe("加仓");
    expect(OPEN_LABEL(null, false)).toBe("开仓");
  });
  it("CLOSE_LABEL：五标签", () => {
    expect(CLOSE_LABEL("stop")).toBe("止损平仓");
    expect(CLOSE_LABEL("take_profit")).toBe("止盈平仓");
    expect(CLOSE_LABEL("liquidation")).toBe("强平");
    expect(CLOSE_LABEL("limit")).toBe("限价平仓");
    expect(CLOSE_LABEL("market")).toBe("平仓");
    expect(CLOSE_LABEL(null)).toBe("平仓");
  });
});

describe("deriveTradeFills", () => {
  it("单开单平（market）→ 2 行、类型开仓/平仓、最终收益扣两费、episodeIndex 0", () => {
    const out = deriveTradeFills([
      f({ pnl: null, fee: 1 }),
      f({ pnl: 100, fee: 1.5, trigger_reason: "market" }),
    ]);
    expect(out.map((r) => r.type)).toEqual(["开仓", "平仓"]);
    expect(out[0].finalPnl).toBeNull();
    expect(out[0].grossPnl).toBeNull();
    expect(out[1].grossPnl).toBe(100);
    expect(out[1].finalPnl).toBeCloseTo(100 - 1 - 1.5, 6);
    expect(out[1].feeBreakdown).toEqual([1, 1.5]);
    expect(out.every((r) => r.episodeIndex === 0)).toBe(true);
  });

  it("加仓周期（开+加+平）→ 中间行加仓、平仓扣三费、同 episodeIndex", () => {
    const out = deriveTradeFills([
      f({ pnl: null, fee: 1 }),
      f({ pnl: null, fee: 1 }),                 // 加仓
      f({ pnl: -50, fee: 1, trigger_reason: "stop" }),
    ]);
    expect(out.map((r) => r.type)).toEqual(["开仓", "加仓", "止损平仓"]);
    expect(out[1].isAdd).toBe(true);
    expect(out[2].finalPnl).toBeCloseTo(-50 - 3, 6);
    expect(out[2].feeBreakdown).toEqual([1, 1, 1]);
    expect(out.every((r) => r.episodeIndex === 0)).toBe(true);
  });

  it("两个连续周期 → episodeIndex 0/1", () => {
    const out = deriveTradeFills([
      f({ pnl: null }), f({ pnl: 10 }),
      f({ pnl: null }), f({ pnl: -5 }),
    ]);
    expect(out.map((r) => r.episodeIndex)).toEqual([0, 0, 1, 1]);
  });

  it("尾部未平仓 → 末行 finalPnl=null、不递增 episodeIndex", () => {
    const out = deriveTradeFills([
      f({ pnl: null }), f({ pnl: 10 }),
      f({ pnl: null }),                         // 尾部开仓未平
    ]);
    expect(out[2].finalPnl).toBeNull();
    expect(out[2].episodeIndex).toBe(1);
  });

  it("孤儿平仓（无前开仓）→ finalPnl = pnl − 平费", () => {
    const out = deriveTradeFills([f({ pnl: -9.62, fee: 3, trigger_reason: "stop" })]);
    expect(out[0].finalPnl).toBeCloseTo(-9.62 - 3, 6);
    expect(out[0].feeBreakdown).toEqual([3]);
  });

  it("缺失 fee → 按 0", () => {
    const out = deriveTradeFills([f({ pnl: null, fee: null }), f({ pnl: 10, fee: null })]);
    expect(out[1].finalPnl).toBe(10);
    expect(out[1].feeBreakdown).toEqual([0, 0]);
  });

  it("legacy null-amount fill 被跳过", () => {
    expect(deriveTradeFills([f({ amount: null, pnl: null }), f({ amount: null, pnl: 100 })])).toEqual([]);
    const mixed = deriveTradeFills([
      f({ amount: null, pnl: null }),           // legacy 跳过
      f({ pnl: null, fee: 1 }),                 // clean 开
      f({ pnl: 50, fee: 1 }),                   // clean 平
    ]);
    expect(mixed.map((r) => r.type)).toEqual(["开仓", "平仓"]);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- trades.spec && cd ..`
Expected: FAIL — 无法解析 `@/utils/trades`。

- [ ] **Step 3: 实现**（新建 `frontend/src/utils/trades.ts`）

```typescript
/** 收益分析 A+ 交易历程：持仓周期（episode, flat→flat）派生 + 类型标签 + 周期级聚合。
 *  纯函数，单一来源供 TradesTable 与 PerformanceBar 复用（spec §C）。 */
import type { TradeRow } from "@/api/client";

export interface DerivedFill extends TradeRow {
  type: string;                   // 开仓/加仓/限价开仓/限价加仓/平仓/止损平仓/止盈平仓/强平/限价平仓
  isAdd: boolean;                 // 加仓行（同周期内已有同向开仓）
  grossPnl: number | null;        // 平仓行 = trade.pnl（毛）；开/加行 = null
  finalPnl: number | null;        // 平仓行 = grossPnl − Σ周期手续费；开/加行 = null
  feeBreakdown: number[] | null;  // 平仓行 = 本周期各 fill 手续费列表（拼算式用）；开/加行 = null
  episodeIndex: number;           // 0-based 周期号（交替底色用）
}

/** 平仓触发细分标签（与 queries._classify_fill 平仓词汇逐字同源，drift-guard 锁同步，见 Task 6）。 */
export function CLOSE_LABEL(reason: string | null | undefined): string {
  switch (reason) {
    case "stop": return "止损平仓";
    case "take_profit": return "止盈平仓";
    case "liquidation": return "强平";
    case "limit": return "限价平仓";
    default: return "平仓";        // market / 未知
  }
}

/** 开仓/加仓标签（前端原创，有意不同于 _classify_fill：方向另列、市价开仓不返 None）。 */
export function OPEN_LABEL(reason: string | null | undefined, isAdd: boolean): string {
  if (reason === "limit") return isAdd ? "限价加仓" : "限价开仓";
  return isAdd ? "加仓" : "开仓";  // market / 未知
}

/** trades（id ASC 的 fill 列表）→ 逐行派生。平仓即结束周期、开仓后同向再开 = 加仓。
 *  跳过 legacy null-amount fill（镜像 MetricsService skip，使表 Σ最终收益 与 net_pnl 对齐）。 */
export function deriveTradeFills(trades: TradeRow[]): DerivedFill[] {
  let episodeIndex = 0;
  let cur: TradeRow[] = [];        // 当前周期 fill 累积（fee 合计 + 开/加判定）
  const out: DerivedFill[] = [];
  for (const fill of trades) {
    if (fill.amount == null) continue;          // legacy null-amount → 跳过
    const isClose = fill.pnl != null;           // 平仓 = pnl 非空
    if (!isClose) {
      const isAdd = cur.length > 0;
      out.push({ ...fill, type: OPEN_LABEL(fill.trigger_reason, isAdd), isAdd,
                 grossPnl: null, finalPnl: null, feeBreakdown: null, episodeIndex });
      cur.push(fill);
    } else {
      const fees = [...cur.map((x) => x.fee ?? 0), fill.fee ?? 0];
      const finalPnl = (fill.pnl as number) - fees.reduce((a, b) => a + b, 0);
      out.push({ ...fill, type: CLOSE_LABEL(fill.trigger_reason), isAdd: false,
                 grossPnl: fill.pnl, finalPnl, feeBreakdown: fees, episodeIndex });
      episodeIndex += 1;
      cur = [];
    }
  }
  return out;
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- trades.spec && cd ..`
Expected: PASS（OPEN/CLOSE_LABEL + deriveTradeFills 全用例）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/trades.ts frontend/test/trades.spec.ts
git commit -m "feat(webui): deriveTradeFills 持仓周期派生 + 开/平类型标签"
```

---

## Task 5: `utils/trades.ts` — 周期级聚合 `summarizeEpisodes`

**Files:**
- Modify: `frontend/src/utils/trades.ts`（追加）
- Test: `frontend/test/trades.spec.ts`（追加）

- [ ] **Step 1: 写失败测试**（追加到 `frontend/test/trades.spec.ts`；把首行 import 加上 `summarizeEpisodes`）

把首行 import 改为：

```typescript
import { deriveTradeFills, summarizeEpisodes, OPEN_LABEL, CLOSE_LABEL } from "@/utils/trades";
```

追加：

```typescript
describe("summarizeEpisodes", () => {
  it("1 胜 1 负 → 计数/胜率/盈亏比/最佳最差", () => {
    const fills = deriveTradeFills([
      f({ pnl: null, fee: 1 }), f({ pnl: 100, fee: 1 }),          // ep0 win: final 98
      f({ pnl: null, fee: 1 }), f({ pnl: -50, fee: 1 }),         // ep1 loss: final -52
    ]);
    const s = summarizeEpisodes(fills);
    expect(s.episodes).toBe(2);
    expect(s.wins).toBe(1);
    expect(s.losses).toBe(1);
    expect(s.winRate).toBeCloseTo(0.5, 6);
    expect(s.profitFactor).toBeCloseTo(98 / 52, 4);
    expect(s.best).toBeCloseTo(98, 6);
    expect(s.worst).toBeCloseTo(-52, 6);
  });

  it("全打平（胜+负=0）→ 净胜率 / 盈亏比 null，不抛", () => {
    const fills = deriveTradeFills([f({ pnl: null, fee: 1 }), f({ pnl: 2, fee: 1 })]);  // final 0
    const s = summarizeEpisodes(fills);
    expect(s.episodes).toBe(1);
    expect(s.winRate).toBeNull();
    expect(s.profitFactor).toBeNull();
  });

  it("无已平仓周期（空 / 仅未平仓开仓）→ 全 null/0", () => {
    expect(summarizeEpisodes([])).toEqual({
      episodes: 0, wins: 0, losses: 0, winRate: null, profitFactor: null, best: null, worst: null,
    });
    const onlyOpen = summarizeEpisodes(deriveTradeFills([f({ pnl: null })]));
    expect(onlyOpen.episodes).toBe(0);
    expect(onlyOpen.best).toBeNull();
  });

  it("有盈无亏 → 盈亏比 null（分母 0，避免 ∞）", () => {
    const s = summarizeEpisodes(deriveTradeFills([f({ pnl: null, fee: 1 }), f({ pnl: 100, fee: 1 })]));
    expect(s.profitFactor).toBeNull();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- trades.spec && cd ..`
Expected: FAIL — `summarizeEpisodes is not a function`。

- [ ] **Step 3: 实现**（追加到 `frontend/src/utils/trades.ts` 末尾）

```typescript
export interface EpisodeSummary {
  episodes: number;             // 已平仓周期数
  wins: number;
  losses: number;
  winRate: number | null;       // 胜 /(胜+负)；胜+负=0 → null
  profitFactor: number | null;  // Σ盈 / |Σ亏|；无盈利周期 或 无亏损周期 → null
  best: number | null;          // max(各周期 finalPnl)
  worst: number | null;         // min(各周期 finalPnl)
}

/** 从 deriveTradeFills 输出的已平仓行 finalPnl 聚合周期级指标（Tier 1/2 + 表头单一来源）。 */
export function summarizeEpisodes(fills: DerivedFill[]): EpisodeSummary {
  const finals = fills.filter((r) => r.finalPnl != null).map((r) => r.finalPnl as number);
  const wins = finals.filter((v) => v > 0);
  const losses = finals.filter((v) => v < 0);
  const grossWin = wins.reduce((a, b) => a + b, 0);
  const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));
  return {
    episodes: finals.length,
    wins: wins.length,
    losses: losses.length,
    winRate: wins.length + losses.length > 0 ? wins.length / (wins.length + losses.length) : null,
    profitFactor: grossWin > 0 && grossLoss > 0 ? grossWin / grossLoss : null,
    best: finals.length ? Math.max(...finals) : null,
    worst: finals.length ? Math.min(...finals) : null,
  };
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- trades.spec && cd ..`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/trades.ts frontend/test/trades.spec.ts
git commit -m "feat(webui): summarizeEpisodes 周期级聚合（除零守卫）"
```

---

## Task 6: drift-guard — CLOSE_LABEL ↔ `_classify_fill` 跨语言同源

**Files:**
- Test: `tests/test_webui_queries.py`（追加）

- [ ] **Step 1: 写 drift-guard 测试**

说明：这是回归锁（非功能测试），它的"红"在于**漂移发生时失败**——Task 4 已实现正确标签，故此测试初次运行即 PASS 是预期的；其真正的红验证在 Step 2（临时改坏 TS 标签使其失败，证明 guard 能抓漂移），随后改回。追加：

```python
def test_close_label_drift_guard_ts_matches_classify_fill():
    """CLOSE_LABEL（trades.ts）平仓词汇必须与 _classify_fill 逐字同源（spec §C drift-guard）。
    _classify_fill 是单一权威来源：改它的标签 → 本测试读 TS 源校验同字面，强制 TS 同步。"""
    from pathlib import Path
    from src.webui.queries import _classify_fill
    expected = set()
    for reason in ["stop", "take_profit", "liquidation", "limit", "other"]:
        ev = _classify_fill({"trigger_reason": reason, "position_side": "long",
                             "pnl": 1.0, "is_full_close": True})
        expected.add(ev.label)        # other → 泛标签「平仓」
    repo_root = Path(__file__).resolve().parents[1]   # tests/ 的上一级 = 仓库根（不依赖 pytest CWD）
    ts = (repo_root / "frontend/src/utils/trades.ts").read_text(encoding="utf-8")
    # 用带引号字面校验（如 '"止损平仓"'），比裸 substring 更严：避免「平仓」是「止损平仓」子串的松配。
    missing = [label for label in expected if f'"{label}"' not in ts]
    assert not missing, f"CLOSE_LABEL drift: {missing} 不在 trades.ts（与 _classify_fill 漂移）"
```

- [ ] **Step 2: 运行确认机制有效（临时见红）**

临时把 `frontend/src/utils/trades.ts` 里 `case "stop": return "止损平仓";` 改成 `return "止损";`，运行：
Run: `pytest tests/test_webui_queries.py::test_close_label_drift_guard_ts_matches_classify_fill -v`
Expected: FAIL — `missing: ['止损平仓']`。确认 guard 能抓漂移后，把 `止损` 改回 `止损平仓`。

- [ ] **Step 3: 运行确认通过（改回后）**

Run: `pytest tests/test_webui_queries.py::test_close_label_drift_guard_ts_matches_classify_fill -v`
Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add tests/test_webui_queries.py
git commit -m "test(webui): CLOSE_LABEL ↔ _classify_fill 跨语言 drift-guard"
```

---

## Task 7: 重写 `TradesTable.vue` 为 A+ 交易历程表

**Files:**
- Modify: `frontend/src/components/TradesTable.vue`（整体重写）
- Test: `frontend/test/TradesTable.spec.ts`（整体重写）

- [ ] **Step 1: 写失败测试**（整体替换 `frontend/test/TradesTable.spec.ts`）

```typescript
import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import TradesTable from "@/components/TradesTable.vue";
import type { TradeRow } from "@/api/client";

const f = (o: Partial<TradeRow>): TradeRow => ({
  at: "2026-06-12T10:00:00Z", action: "order_filled", side: "short",
  price: 65000, amount: 10, pnl: null, fee: 1, trigger_reason: "market", ...o,
});

describe("TradesTable (A+)", () => {
  it("单位标注在表区顶部（不在每格重复）", () => {
    const w = mount(TradesTable, { props: { trades: [f({})] } });
    expect(w.text()).toContain("USDT");
    expect(w.text()).toContain("UTC");
  });

  it("加仓周期：开/加/平 + 平仓行逐笔算式 + 开仓行最终收益占位", () => {
    const w = mount(TradesTable, {
      props: { trades: [
        f({ pnl: null, fee: 1 }),
        f({ pnl: null, fee: 1 }),                          // 加仓
        f({ pnl: -7.62, fee: 1, trigger_reason: "market" }),  // final -10.62（3×fee=1：开+加+平 全计入）
      ] },
    });
    const t = w.text();
    expect(t).toContain("加仓");
    expect(t).toContain("平仓");
    expect(t).toContain("−10.62");       // 最终收益（U+2212；毛 −7.62 − 3×fee 1 = −10.62）
    expect(t).toContain("=");            // 逐笔算式行前缀
  });

  it("止损平仓触发细分标签", () => {
    const w = mount(TradesTable, {
      props: { trades: [f({ pnl: null }), f({ pnl: -50, trigger_reason: "stop" })] },
    });
    expect(w.text()).toContain("止损平仓");
  });

  it("周期交替底色 class（两周期 → ep-even + ep-odd 行）", () => {
    const w = mount(TradesTable, {
      props: { trades: [
        f({ pnl: null }), f({ pnl: 10 }),
        f({ pnl: null }), f({ pnl: -5 }),
      ] },
    });
    expect(w.find(".ep-even").exists()).toBe(true);
    expect(w.find(".ep-odd").exists()).toBe(true);
  });

  it("§全局 成交时刻按 UTC 展示", () => {
    const w = mount(TradesTable, { props: { trades: [f({ pnl: 10 })] } });
    expect(w.text()).toContain("2026-06-12 10:00:00");
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- TradesTable.spec && cd ..`
Expected: FAIL —「加仓」「止损平仓」「ep-even」未渲染（当前组件是旧的裸表）。

- [ ] **Step 3: 实现**（整体替换 `frontend/src/components/TradesTable.vue`）

```vue
<script setup lang="ts">
import { computed, h } from "vue";
import { NDataTable } from "naive-ui";
import type { DataTableColumns } from "naive-ui";
import type { TradeRow } from "@/api/client";
import { fmtUtc } from "@/utils/time";
import { fmtNum, fmtSigned } from "@/utils/format";
import { deriveTradeFills, type DerivedFill } from "@/utils/trades";

const props = defineProps<{ trades: TradeRow[] }>();
const rows = computed(() => deriveTradeFills(props.trades));

const signClass = (n: number | null | undefined) =>
  n != null && n > 0 ? "pos" : n != null && n < 0 ? "neg" : "";

const columns: DataTableColumns<DerivedFill> = [
  { title: "时刻(UTC)", key: "at", render: (r) => fmtUtc(r.at) },
  {
    title: "类型", key: "type",
    render: (r) => h("span", { class: r.isAdd ? "tag-add" : r.grossPnl != null ? "tag-close" : "" }, r.type),
  },
  {
    title: "方向", key: "side",
    render: (r) => h("span", { class: r.side === "long" ? "pos" : r.side === "short" ? "neg" : "" }, r.side ?? "—"),
  },
  { title: "价格", key: "price", render: (r) => fmtNum(r.price) },
  { title: "数量", key: "amount", render: (r) => fmtNum(r.amount, 4) },
  { title: "手续费", key: "fee", render: (r) => h("span", { class: "fee" }, fmtNum(r.fee)) },
  {
    title: "毛利PnL", key: "grossPnl",
    render: (r) => (r.grossPnl == null ? "—" : h("span", { class: signClass(r.grossPnl) }, fmtSigned(r.grossPnl))),
  },
  {
    title: "最终收益", key: "finalPnl",
    render: (r) => {
      if (r.finalPnl == null) return "—";
      const formula = "= " + fmtSigned(r.grossPnl) + (r.feeBreakdown ?? []).map((x) => ` − ${fmtNum(x)}`).join("");
      return h("div", { class: "final-cell" }, [
        h("div", { class: `${signClass(r.finalPnl)} final-v` }, fmtSigned(r.finalPnl)),
        h("div", { class: "formula" }, formula),
      ]);
    },
  },
];

const rowClassName = (row: DerivedFill) => (row.episodeIndex % 2 === 0 ? "ep-even" : "ep-odd");
</script>

<template>
  <div class="trades-a">
    <div class="unit-caption">金额单位 USDT · 时刻为 UTC · 价格为成交价</div>
    <n-data-table
      :columns="columns" :data="rows" size="small" :bordered="false"
      :max-height="280" :row-class-name="rowClassName"
    />
  </div>
</template>

<style scoped>
.unit-caption { font-size: 11px; color: var(--ob-text-muted); margin-bottom: 4px; }
:deep(.pos) { color: var(--ob-pos); }
:deep(.neg) { color: var(--ob-neg); }
:deep(.fee) { color: var(--ob-warn); }
.final-v { font-weight: 600; }
.formula { font-size: 10px; color: var(--ob-text-muted); }
.tag-add { background: var(--ob-warn-soft); color: var(--ob-warn); padding: 0 5px; border-radius: 3px; }
.tag-close { color: var(--ob-text-muted); }
:deep(tr.ep-odd td) { background: rgba(0, 0, 0, 0.025); }
</style>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- TradesTable.spec && cd ..`
Expected: PASS。

- [ ] **Step 5: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit && cd ..`
Expected: 0 错。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/TradesTable.vue frontend/test/TradesTable.spec.ts
git commit -m "feat(webui): TradesTable 重写为 A+ 交易历程表（周期分组 + 逐笔扣费）"
```

---

## Task 8: 重构 `PerformanceBar.vue` 为底部抽屉 + 分层 + 持仓条

**Files:**
- Modify: `frontend/src/components/PerformanceBar.vue`（整体重写）
- Test: `frontend/test/PerformanceBar.spec.ts`（整体重写）

- [ ] **Step 1: 写失败测试**（整体替换 `frontend/test/PerformanceBar.spec.ts`）

```typescript
import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { useSessionsStore } from "@/stores/sessions";

vi.mock("lightweight-charts", () => ({
  createChart: vi.fn(() => ({
    addLineSeries: vi.fn(() => ({ setData: vi.fn() })),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    remove: vi.fn(),
  })),
}));

import PerformanceBar from "@/components/PerformanceBar.vue";

// 2 周期（1 胜 1 负）trades + 标量字段。
// 注：标量（net_pnl/total_pnl/total_fees）与 trades 是【独立测试输入】——组件 PnL/费/% 取 API 标量、
// 计数/胜率/盈亏比取 episode 派生（deriveTradeFills），二者不交叉校验（镜像真实双来源设计），
// 故此处标量值无需与 trades 的 Σfinals 对齐（如 net_pnl=-95.14 用于触发负色，与 trades 的 +46 无关）。
const PERF_FLAT = {
  initial_balance: 10000, current_position: "flat",
  total_return_pct: 0.07, total_pnl: 7, net_pnl: -95.14, net_win_rate: 0.14, max_drawdown_pct: 0.95,  // MDD 恒非负（metrics.py max_dd_ratio*100）
  net_profit_factor: 0.33, total_trades: 2, net_winning_trades: 1, net_losing_trades: 1,
  total_fees: 102.03,
  equity_curve: [{ at: "2026-06-12T10:00:00Z", equity: 10000 }],
  trades: [
    { at: "2026-06-12T10:00:00Z", action: "order_filled", side: "short", price: 65000, amount: 10, pnl: null, fee: 1, trigger_reason: "market" },
    { at: "2026-06-12T10:05:00Z", action: "order_filled", side: "short", price: 65100, amount: 10, pnl: 100, fee: 1, trigger_reason: "market" },
    { at: "2026-06-12T10:10:00Z", action: "order_filled", side: "long", price: 65000, amount: 10, pnl: null, fee: 1, trigger_reason: "market" },
    { at: "2026-06-12T10:15:00Z", action: "order_filled", side: "long", price: 64900, amount: 10, pnl: -50, fee: 1, trigger_reason: "stop" },
  ],
  open_position: null,
};

const mountBar = (perf: unknown) => {
  const w = mount(PerformanceBar, {
    global: { plugins: [createTestingPinia({ createSpy: vi.fn, stubActions: true })] },
  });
  (useSessionsStore() as any).performance = perf;
  return w;
};

describe("PerformanceBar 抽屉", () => {
  it("默认折叠：见细条、不见 Tier1 六格", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    expect(w.find(".collapsed-bar").exists()).toBe(true);
    expect(w.find(".tier1-grid").exists()).toBe(false);
  });

  it("折叠条四项 + 数值（净/毛 PnL 带符号、手续费、胜率）", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    const t = w.text();
    expect(t).toContain("净PnL");
    expect(t).toContain("−95.14");           // 净PnL（U+2212）
    expect(t).toContain("毛PnL");
    expect(t).toContain("手续费");
    expect(t).toContain("胜率");
  });

  it("点击展开 → 渲 Tier1 六格 + Tier2 + 双口径警示", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    const t = w.text();
    expect(w.find(".tier1-grid").exists()).toBe(true);
    expect(t).toContain("净胜率");
    expect(t).toContain("盈亏比");
    expect(t).toContain("持仓周期");
    expect(t).toContain("不可逐点对账");
  });

  it("Tier1 净在前毛在后 + 手续费下标 毛−费=净", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    const t = w.text();
    expect(t.indexOf("net已实现")).toBeLessThan(t.indexOf("gross已实现"));
    expect(t).toContain("毛−费=净");
  });

  it("sign 驱动着色：净PnL 负 .neg、毛PnL 正 .pos", async () => {
    const w = mountBar(PERF_FLAT);          // net_pnl=-95.14（负）、total_return_pct=0.07（正）
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    expect(w.findAll(".tier1-grid .neg").length).toBeGreaterThan(0);
    expect(w.findAll(".tier1-grid .pos").length).toBeGreaterThan(0);
  });

  it("未平仓：渲当前持仓条（未实现毛 + 未平仓入场费）+ 折叠条多一格", async () => {
    const perfOpen = {
      ...PERF_FLAT, total_fees: 109.12,     // 已实现费 = 毛−净 = 7 − (-95.14)=102.14 → 入场费 ≈ 6.98
      open_position: { side: "short", contracts: 10.82, entry_price: 65542.1, unrealized_pnl: -13.97, pnl_pct_of_notional: -0.2 },
    };
    const w = mountBar(perfOpen);
    await w.vm.$nextTick();
    expect(w.find(".held-box").exists()).toBe(true);    // 折叠条持仓格
    await w.find(".collapsed-bar").trigger("click");
    const t = w.text();
    expect(w.find(".held-bar").exists()).toBe(true);    // 展开态持仓条
    expect(t).toContain("当前持仓(未平仓)");
    expect(t).toContain("未实现收益(毛)");
    expect(t).toContain("−13.97");
    expect(t).toContain("未平仓入场费");
  });

  it("平尾：无当前持仓条 + 无折叠条持仓格", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    expect(w.find(".held-box").exists()).toBe(false);
    await w.find(".collapsed-bar").trigger("click");
    expect(w.find(".held-bar").exists()).toBe(false);
  });

  it("未平仓但 unrealized=null（snapshot 异向）：持仓条显方向/数量、不显未实现行、无折叠条持仓格", async () => {
    const perfNoUnreal = {
      ...PERF_FLAT, total_fees: 109.12,
      open_position: { side: "short", contracts: 0.265, entry_price: 65000, unrealized_pnl: null, pnl_pct_of_notional: null },
    };
    const w = mountBar(perfNoUnreal);
    await w.vm.$nextTick();
    expect(w.find(".held-box").exists()).toBe(false);    // 折叠条不加未实现格
    await w.find(".collapsed-bar").trigger("click");
    const t = w.text();
    expect(w.find(".held-bar").exists()).toBe(true);     // 持仓条仍渲
    expect(t).toContain("当前持仓(未平仓)");
    expect(t).toContain("未平仓入场费");                  // 入场费照常
    expect(t).not.toContain("未实现收益(毛)");            // 不编造未实现行（spec §F fact-only）
    expect(t).not.toContain("盯市,未扣平仓费");
  });

  it("交易历程默认折叠（不见 A+ 表单位标注）、展开后可见", async () => {
    const w = mountBar(PERF_FLAT);
    await w.vm.$nextTick();
    await w.find(".collapsed-bar").trigger("click");
    expect(w.text()).toContain("交易历程");
    expect(w.find(".trades-a").exists()).toBe(false);   // showTrades 默认 false
    await w.find(".trades-fold button").trigger("click");
    expect(w.find(".trades-a").exists()).toBe(true);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- PerformanceBar.spec && cd ..`
Expected: FAIL —「.collapsed-bar」「.tier1-grid」「.held-bar」未渲染（当前组件是旧的常驻布局）。

- [ ] **Step 3: 实现**（整体替换 `frontend/src/components/PerformanceBar.vue`）

```vue
<script setup lang="ts">
import { computed, ref } from "vue";
import { NTag, NButton } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";
import EquityChart from "@/components/EquityChart.vue";
import TradesTable from "@/components/TradesTable.vue";
import { fmtNum, fmtSigned, fmtSignedPct } from "@/utils/format";
import { deriveTradeFills, summarizeEpisodes } from "@/utils/trades";

const store = useSessionsStore();
const expanded = ref(false);
const showTrades = ref(false);

const perf = computed(() => store.performance);
const fills = computed(() => (perf.value ? deriveTradeFills(perf.value.trades) : []));
const summary = computed(() => summarizeEpisodes(fills.value));

const initial = computed(() => perf.value?.initial_balance ?? 0);
const netPnl = computed(() => perf.value?.net_pnl ?? 0);
const netPnlPct = computed(() => (initial.value > 0 ? (netPnl.value / initial.value) * 100 : null));
const grossPnl = computed(() => perf.value?.total_pnl ?? 0);   // 毛额直取（review minor 5），免反推
const grossPnlPct = computed(() => perf.value?.total_return_pct ?? 0);
const feesRealized = computed(() => grossPnl.value - netPnl.value);   // 毛−净 = 已实现手续费（精确）
const mdd = computed(() => perf.value?.max_drawdown_pct ?? 0);

const openPos = computed(() => perf.value?.open_position ?? null);
const unrealizedEntryFee = computed(() =>
  openPos.value && perf.value ? perf.value.total_fees - feesRealized.value : 0);

const decided = computed(() => summary.value.wins + summary.value.losses);
const winRateText = computed(() =>
  summary.value.winRate == null ? "—" : `${(summary.value.winRate * 100).toFixed(0)}%`);
const profitFactorText = computed(() =>
  summary.value.profitFactor == null ? "—" : summary.value.profitFactor.toFixed(2));

const signClass = (n: number | null | undefined) =>
  n != null && n > 0 ? "pos" : n != null && n < 0 ? "neg" : "";
</script>

<template>
  <div v-if="perf" class="perf-bar ob-card" :class="{ expanded }">
    <!-- 折叠态：细条 -->
    <div v-if="!expanded" class="collapsed-bar" @click="expanded = true">
      <span class="lead">收益 ▴</span>
      <span>净PnL <b :class="signClass(netPnl)">{{ fmtSigned(netPnl) }}</b>
        <span class="muted">{{ fmtSignedPct(netPnlPct) }}</span></span>
      <span class="dot">·</span>
      <span>毛PnL <b :class="signClass(grossPnl)">{{ fmtSigned(grossPnl) }}</b>
        <span class="muted">{{ fmtSignedPct(grossPnlPct) }}</span></span>
      <span class="dot">·</span>
      <span>手续费 <b class="fee">{{ fmtNum(feesRealized) }}</b></span>
      <span class="dot">·</span>
      <span>胜率 <b>{{ winRateText }}</b> <span class="muted">({{ summary.wins }}/{{ decided }})</span></span>
      <span v-if="openPos && openPos.unrealized_pnl != null" class="held-box">持仓 未实现(毛)
        <b :class="signClass(openPos.unrealized_pnl)">{{ fmtSigned(openPos.unrealized_pnl) }}</b></span>
      <span class="expand-hint">点击展开 ▴</span>
    </div>

    <!-- 展开态 -->
    <template v-else>
      <div class="exp-head" @click="expanded = false">
        <span class="lead">收益分析 ▾</span>
        <span class="caveat">已实现指标 vs 盯市曲线 不同口径、不可逐点对账</span>
      </div>

      <!-- 当前持仓条（仅未平仓） -->
      <div v-if="openPos" class="held-bar">
        <span class="held-title">当前持仓(未平仓)</span>
        <span class="side-tag" :class="openPos.side">{{ openPos.side === "long" ? "多" : "空" }}</span>
        <span>{{ fmtNum(openPos.contracts, 4) }} @ {{ fmtNum(openPos.entry_price) }}</span>
        <span v-if="openPos.unrealized_pnl != null">未实现收益(毛)
          <b :class="signClass(openPos.unrealized_pnl)">{{ fmtSigned(openPos.unrealized_pnl) }}</b>
          <span class="muted">{{ fmtSignedPct(openPos.pnl_pct_of_notional) }} · 盯市,未扣平仓费</span></span>
        <span class="held-fee">未平仓入场费 <b>{{ fmtNum(unrealizedEntryFee) }}</b>
          <span class="muted">已付,从净值扣</span></span>
      </div>

      <div class="exp-grid">
        <div class="curve">
          <div class="caliper">净值曲线 <n-tag size="tiny" :bordered="false">盯市·含未实现</n-tag></div>
          <EquityChart :points="perf.equity_curve" />
        </div>
        <div class="metrics">
          <div class="tier-label">Tier 1 · 最关心 <span class="muted">(已实现)</span></div>
          <div class="tier1-grid">
            <div class="cell"><div class="k">净PnL <span class="sub">net已实现</span></div>
              <div class="v" :class="signClass(netPnl)">{{ fmtSigned(netPnl) }}</div>
              <div class="pct" :class="signClass(netPnlPct)">{{ fmtSignedPct(netPnlPct) }}</div></div>
            <div class="cell"><div class="k">毛PnL <span class="sub">gross已实现</span></div>
              <div class="v" :class="signClass(grossPnl)">{{ fmtSigned(grossPnl) }}</div>
              <div class="pct" :class="signClass(grossPnlPct)">{{ fmtSignedPct(grossPnlPct) }}</div></div>
            <div class="cell"><div class="k">手续费 <span class="sub">毛−费=净</span></div>
              <div class="v fee">{{ fmtNum(feesRealized) }}</div></div>
            <div class="cell"><div class="k">净胜率</div><div class="v">{{ winRateText }}</div></div>
            <div class="cell"><div class="k">盈亏比</div><div class="v">{{ profitFactorText }}</div></div>
            <div class="cell"><div class="k">最大回撤 <span class="sub">net equity</span></div>
              <div class="v neg">{{ fmtNum(mdd) }}%</div></div>
          </div>
          <div class="tier2">
            持仓周期 <b>{{ summary.episodes }}</b>
            · 胜负 <b class="pos">{{ summary.wins }}</b>/<b class="neg">{{ summary.losses }}</b>
            · 最佳 <b :class="signClass(summary.best)">{{ fmtSigned(summary.best) }}</b>
            · 最差 <b :class="signClass(summary.worst)">{{ fmtSigned(summary.worst) }}</b>
            · 初始 {{ fmtNum(initial) }}
          </div>
        </div>
      </div>

      <div class="trades-fold">
        <n-button text size="small" @click="showTrades = !showTrades">
          {{ showTrades ? "交易历程 ▾" : `交易历程（${summary.episodes} 笔 · 净 ${fmtSigned(netPnl)}）▸` }}
        </n-button>
        <TradesTable v-if="showTrades" :trades="perf.trades" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.perf-bar { border-top: 1px solid var(--ob-border); }
.perf-bar.expanded { max-height: 55vh; overflow-y: auto; padding: 8px 16px; }

.collapsed-bar { display: flex; align-items: center; gap: 10px; padding: 9px 16px; font-size: 13px; cursor: pointer; flex-wrap: wrap; }
.lead { font-weight: 600; }   /* 文字色继承（--ob-text 未定义，与现有非 muted 文字一致，review minor 1）*/
.dot { color: var(--ob-border); }
.muted { color: var(--ob-text-muted); font-size: 11px; }
.expand-hint { margin-left: auto; color: var(--ob-text-muted); font-size: 12px; }
.held-box { border: 1px dashed var(--ob-warn); border-radius: 4px; padding: 0 6px; }

.exp-head { display: flex; align-items: center; gap: 12px; padding: 4px 0 8px; cursor: pointer; }
.caveat { color: var(--ob-text-muted); font-size: 11px; }

.held-bar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; font-size: 13px;
  margin-bottom: 10px; padding: 8px 12px; border-radius: 6px;
  background: var(--ob-warn-soft); border: 1px solid var(--ob-warn); color: var(--ob-warn); }
.held-title { font-weight: 600; }
.side-tag { padding: 1px 6px; border-radius: 3px; }
.side-tag.long { color: var(--ob-pos); }
.side-tag.short { color: var(--ob-neg); }

.exp-grid { display: grid; grid-template-columns: 1.15fr 1fr; gap: 16px; }
.curve { min-width: 0; }
.caliper { font-size: 12px; color: var(--ob-text-muted); margin-bottom: 4px; }
.tier-label { font-size: 11px; color: var(--ob-text-muted); margin-bottom: 6px; }
.tier1-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px 14px; }
.cell .k { font-size: 10px; color: var(--ob-text-muted); }
.cell .sub { font-size: 9px; }
.cell .v { font-size: 16px; font-weight: 700; }
.cell .pct { font-size: 10px; }
.tier2 { font-size: 12px; margin-top: 10px; border-top: 1px dashed var(--ob-border); padding-top: 8px; }

.trades-fold { margin-top: 8px; }
.pos { color: var(--ob-pos); }
.neg { color: var(--ob-neg); }
.fee { color: var(--ob-warn); }
</style>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- PerformanceBar.spec && cd ..`
Expected: PASS（全部抽屉 / 分层 / 持仓条用例）。

- [ ] **Step 5: 类型检查**

Run: `cd frontend && npx vue-tsc --noEmit && cd ..`
Expected: 0 错。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/PerformanceBar.vue frontend/test/PerformanceBar.spec.ts
git commit -m "feat(webui): PerformanceBar 重构为底部抽屉 + 指标分层 + 当前持仓条"
```

---

## Task 9: 全套件 gate + 真实数据 Playwright 验证

**Files:**
- 无代码改动（验证 + 必要时回修）

- [ ] **Step 1: 后端全套件**

Run: `pytest -q`
Expected: 全 PASS（无回归；含 Task 1/6 新增）。

- [ ] **Step 2: 前端全套件 + 类型**

Run: `cd frontend && npm test && npx vue-tsc --noEmit && cd ..`
Expected: vitest 全 PASS；vue-tsc 0 错。

- [ ] **Step 3: 启动 WebUI（真实 sim DB）**

确认已重生成类型（Task 2）。按现有惯例启动后端 + 前端 dev server（如 `python -m src.webui` + `cd frontend && npm run dev`）。记下前端 URL（如 `http://localhost:5173`）。

**会话选取（按 SimPosition 权威态，非 snapshot）：** 先用 SQL 选两个会话——一个 `SimPosition` 平/无仓（平尾态），一个 `SimPosition` 有仓（未平态）。命令：`sqlite3 data/tradebot.db "SELECT s.name, p.side, p.contracts FROM sessions s LEFT JOIN sim_positions p ON p.session_id=s.id;"`。实测平尾示例 sim#19、有仓示例 sim#21（与 §F 实证一致）。

- [ ] **Step 4: Playwright — 平尾会话（SimPosition 无仓，如 sim#19）**

用 Playwright MCP 导航到该会话看板：
- 折叠态可见细条，四项数值合理：净PnL `−95.14`（−0.95%）、毛PnL `+6.90`（+0.07%）、手续费 `102.03`、胜率 `14%`（1/7）。
- **无持仓格（`.held-box` 不存在）**，且与同屏 LiveStatusCard「无持仓」**一致**（数据源同为 SimPosition）。
- 点击展开 → Tier1 六格 + Tier2；展开「交易历程」→ A+ 表含加仓周期（开空 / 加空 / 平），平仓行最终收益带逐笔算式。
- `browser_console_messages`：0 error。

- [ ] **Step 5: Playwright — 未平仓会话（SimPosition 有仓，如 sim#21）**

导航到该会话：
- 折叠态末尾多一格 `持仓 未实现(毛)`（snapshot 同向时；如 sim#21 `−13.97`）。
- 展开 → 当前持仓条 side/数量/入场价 **与同屏 LiveStatusCard 完全一致**（如 `空 10.82 @ 65,542.1`）；snapshot 同向时显未实现收益(毛)（`−13.97`，−0.20% · 盯市,未扣平仓费）+ 未平仓入场费（`7.09`，已付,从净值扣）。
- 手续费(已实现) `402.77`。
- `browser_console_messages`：0 error。
- **关键回归（review claim 2）：** 确认 PerformanceBar 持仓条与 LiveStatusCard 持仓在任一会话都不矛盾（同源 SimPosition）。

- [ ] **Step 6:（条件）回修 + 重跑**

若 Playwright 暴露偏差（数值 / 渲染 / console error / 与 LiveStatusCard 矛盾）：定位到对应 Task 的组件 / 派生函数 / 后端 `_derive_open_position`，补一条 red 测试复现，green 修复，重跑 Step 1-2 全套件，再回到 Step 4-5 复验。

- [ ] **Step 7: 收尾**

全套件 + 类型 + 两态 Playwright 通过后，按 `superpowers:finishing-a-development-branch` 完成分支（合并 / PR 由用户选）。

---

## Self-Review

**1. Spec coverage（逐节核对）:**
- §A 底部抽屉（默认折叠）→ Task 8（`expanded` 默认 false、折叠条四项 + 持仓格、点击展开）。✓
- §B 指标分层（Tier1 六格 + Tier2）→ Task 8（tier1-grid 顺序固定净在前、手续费下标、Tier2 行）。✓
- §C A+ 交易历程表（deriveTradeFills + summarizeEpisodes + 标签 + 算式 + 交替底色 + 默认折叠）→ Task 4/5/7/8。✓
- §C 标签词汇（OPEN_LABEL / CLOSE_LABEL）+ drift-guard → Task 4 + Task 6。✓
- §D 布局（exp-grid 1.15fr 1fr + max-height 55vh）→ Task 8 样式。✓
- §E 着色（PnL sign 驱动 / 手续费琥珀例外 / 持仓条 warn-soft）→ Task 7/8（signClass + .fee + .held-bar）。✓
- §F 当前持仓与未实现毛 + 未平仓入场费 → Task 1（后端 open_position，**SimPosition 权威**）+ Task 8（held-bar + unrealizedEntryFee）。✓
- 数据来源派生（毛PnL=**total_pnl 直取**、手续费=毛−净、净%、未平仓入场费=total_fees−已实现费）→ Task 8 computeds。✓
- 边界与降级（initial=0 → null%、空 trades、null trigger_reason 兜底、legacy null-amount skip、除零守卫、**open_position snapshot 异向→未实现 None / SimPosition 平→None**）→ Task 1/3/4/5（fmtSignedPct null、deriveTradeFills continue + ?? 0、summarizeEpisodes null 守卫、_derive_open_position 同向闸）。✓
- 后端三处暴露（trigger_reason + open_position + total_pnl）+ 无迁移 + 重生成 types → Task 1 + Task 2。✓
- 测试策略 §1-§7 → Task 1（后端：trigger_reason+total_pnl + open_position 三态）/ Task 4-5（trades 单元）/ Task 3（format）/ Task 6（drift-guard）/ Task 7-8（组件）/ Task 9（Playwright + LiveStatusCard 一致性）。✓

**Review 闭环（2026-06-15）:** claim 2（open_position 数据源）已改为 SimPosition 权威 + snapshot 同向借用未实现，补三态测试；minor 1（--ob-text 去除）/ 2（MDD fixture 0.95）/ 3（Task 6 文案 + 引号字面校验）/ 4（drift-guard __file__ 路径）/ 5（暴露 total_pnl 免反推）全部落地。claim 1（部分平仓）实证为假（138=开仓误计、零部分平仓）→ 不改，spec §C 注明实证；claim 3（episode vs FIFO）为已决设计 → 维持。

**2. Placeholder scan:** 无 TBD / TODO；每个 code step 含完整代码块与确切命令、预期输出。✓

**3. Type consistency:**
- `OpenPositionBrief` 字段 `side/contracts/entry_price/unrealized_pnl/pnl_pct_of_notional` 在 Task 1 schema、Task 1 `_derive_open_position`、Task 8 模板（`openPos.pnl_pct_of_notional`）一致。✓
- `DerivedFill` 字段 `type/isAdd/grossPnl/finalPnl/feeBreakdown/episodeIndex` 在 Task 4 定义、Task 7 渲染、Task 5 `summarizeEpisodes`（读 `finalPnl`）一致。✓
- `EpisodeSummary` 字段 `episodes/wins/losses/winRate/profitFactor/best/worst` 在 Task 5 定义、Task 8 模板（`summary.episodes/wins/losses` + winRateText/profitFactorText）一致。✓
- `deriveTradeFills` / `summarizeEpisodes` / `OPEN_LABEL` / `CLOSE_LABEL` / `fmtSignedPct` 命名跨 Task 4/5/6/7/8 一致。✓
- `TradeRow.trigger_reason`（Task 1）被 Task 4 `f()` fixture 与 deriveTradeFills 读取，类型同步靠 Task 2 重生成。✓
