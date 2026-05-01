# R2-7 Agent Cycle Schema Reframe — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `decision_logs` 表重构为 `agent_cycles` (5 维度叙事 framing：前因 / 触发上下文 / 决策时现状 / agent 推理 / agent 决策)；删除 R2-4 派生 enum 路线；新加 `state_snapshot` JSON 字段；`reasoning` 改保 thinking content；`decision` 改保 message；`BaseExchange.Order` 扩 `trigger_price` 字段；spec §8 P1-7 展示设计接口契约保留为 R2-8 议题。

**Architecture:** 纯存储路径 + schema 改造，不动 prompt / agent 行为 / output_type。新增 `src/services/cycle_capture.py` 双 helper（`_capture_state_snapshot` + `_capture_trigger_context`）在 `agent.run()` 之前一次性 capture，success / forensic 两路径都复用同一对 `*_var`。Migration 走 Alembic batch_alter（rename table + 5 columns + decision Text/nullable + add state_snapshot + drop index recreate）。R2-4 派生函数 + 5 ACTIONS 常量 + DERIVE_DECISION_VALUES + 27 测试 + decision-enum-timeline.md 整套删除。

**Tech Stack:** SQLAlchemy 2.x async + Alembic batch_alter + pydantic-ai 1.78 (ThinkingPart) + pytest async + DeepSeek v4-pro thinking model

---

## Pre-impl 验证（已完成 2026-05-01）

### Pre-smoke 1: pydantic-ai DeepSeek ThinkingPart 实测 ✅

User 跑 `uv run python scripts/debug_agent_call.py --model deepseek-v4-pro` 验证结果：

| 验收项 | 结果 |
|---|---|
| `thinking_active` | **True** ✓ |
| `[thinking]` content 长度 | ~1500 chars 中文推理链 ✓ |
| `result.usage().details["reasoning_tokens"]` | **426** ✓ |
| reasoning_tokens vs thinking content 互证 | 426 tokens × 3.5 chars/token ≈ 1500 chars (中文) ✓ |
| ThinkingPart vs TextPart 内容职责分离 | ✓ thinking = 推理过程 / output = 最终 markdown 结论 |

**结论**: spec §6.3 ThinkingPart 提取路径（`isinstance(part, ThinkingPart) → part.content`）实证有效，pydantic-ai 1.78 + DeepSeek v4-pro 完整支持。

### Pre-smoke 2: UsageLimitExceeded partial usage ✅ (audit-only)

由 spec reviewer source-code audit 已确认（不 invoke LLM）：
- `pydantic_ai/exceptions.py:183`: `class UsageLimitExceeded(AgentRunError).__init__(self, message: str)` 单参数构造，无 usage 字段
- `pydantic_ai/usage.py:382/386/392/400/404/410/417`: 7 处 raise 均仅传 message string

**结论**: 沿用 spec §6.5 `tokens_consumed=0` 假设，无需 impl 时 smoke 实测。

---

## File Structure

### 新建（5 个文件，Issue 10 校准）

| 路径 | 责任 |
|---|---|
| `src/services/cycle_capture.py` | `_capture_state_snapshot` + `_capture_trigger_context` 两 helper |
| `alembic/versions/<rev>_r2_7_agent_cycle_schema_reframe.py` | Alembic migration |
| `tests/test_cycle_capture.py` | helpers 单元测试 + G1 SoT 测试 |
| `tests/test_drift_no_legacy_decision_refs.py` | G7 drift guard |
| `docs/metrics/agent-cycles-schema.md` | 取代 decision-enum-timeline.md |

### 修改 src（4 个文件）

| 路径 | 改动 |
|---|---|
| `src/storage/models.py:77-95` | `DecisionLog` → `AgentCycle` + 5 字段 rename + decision Text/nullable + state_snapshot 列 |
| `src/cli/app.py:35,51-149,217-282,355-369,378-383` | import rename + 删派生路线整套 + 写入路径改造（capture + ThinkingPart 提取 + AgentCycle 写入）|
| `src/integrations/exchange/base.py:35-45` | `Order` dataclass + `trigger_price: float \| None = None` |
| `src/integrations/exchange/okx.py` | `_parse_order` 填充 trigger_price (algo orders 走 stopLossPrice/takeProfitPrice) |
| `src/integrations/exchange/simulated.py` | `_make_*_order` 转换路径填 trigger_price |

### 修改 tests（5 个文件）

| 路径 | 改动 |
|---|---|
| `tests/test_storage.py:60-69` | DecisionLog → AgentCycle import + 字段名改 |
| `tests/test_usage_limits.py:11/103/110/210-227/268-274` | DecisionLog → AgentCycle import + 字段名改 + 删 `assert row.decision == "hold"/"open_long"` 类断言 |
| `tests/test_alembic_migration.py` | 保留 Iter 3 + R2-4 段 + 新增 R2-7 段（T-MIG-1~8）|
| `tests/test_okx_algo_normalization.py` | 加 trigger_price 填充测试 (T-ORD-2~5) |
| `tests/test_okx_websocket.py:208` | stale 注释更新（顺手清理）|

### 删除（3 个文件）

| 路径 | 理由 |
|---|---|
| `tests/test_derive_decision.py` | R2-4 派生函数测试整套（27 项）|
| `tests/test_decision_log_e2e.py` | e2e 派生测试 |
| `docs/metrics/decision-enum-timeline.md` | 派生路线文档作废，由 agent-cycles-schema.md 取代 |

---

## Task 1: BaseExchange.Order 扩 trigger_price 字段

**Files:**
- Modify: `src/integrations/exchange/base.py:35-45` (Order dataclass)
- Modify: `src/integrations/exchange/okx.py` (_parse_order 填充)
- Modify: `src/integrations/exchange/simulated.py` (_make_*_order 转换)
- Test: `tests/test_okx_algo_normalization.py` (加 6 项 T-ORD)
- Test: `tests/test_exchange.py` (Order 构造 fixture 默认 None 兼容)

**Spec ref:** §4.7 + §10.2 (Order 扩展测试 T-ORD-1~6) + AC13

- [ ] **Step 1.1: Read existing Order dataclass + _parse_order context**

```bash
sed -n '35,45p' src/integrations/exchange/base.py
sed -n '370,420p' src/integrations/exchange/okx.py | grep -A 30 "_parse_order"
```

读取目标：当前 Order 结构 + OKX `_parse_order` 实现位置（Iter 2b 加 is_algo 后的现状）。

- [ ] **Step 1.2: Write T-ORD-1 (Order dataclass field exists)**

加到 `tests/test_okx_algo_normalization.py` 末尾：

```python
def test_order_dataclass_has_trigger_price_field():
    """T-ORD-1 (R2-7 §4.7): BaseExchange.Order 含 trigger_price 字段，默认 None。"""
    from src.integrations.exchange.base import Order
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(Order)}
    assert "trigger_price" in field_names, \
        f"Order dataclass 缺 trigger_price 字段；现有字段: {field_names}"

    # 验证默认值（不传也能构造）
    o = Order(
        id="test-1", symbol="BTC/USDT:USDT", side="buy",
        order_type="limit", amount=0.01, price=65000.0, status="open",
    )
    assert o.trigger_price is None, f"trigger_price 默认应为 None，实际 {o.trigger_price!r}"
```

- [ ] **Step 1.3: Run T-ORD-1 expecting failure**

```bash
uv run pytest tests/test_okx_algo_normalization.py::test_order_dataclass_has_trigger_price_field -v
```

Expected: FAIL with `'trigger_price' in field_names` 失败 / 或 `Order(...)` 构造 AttributeError。

- [ ] **Step 1.4: Add trigger_price field to Order dataclass**

`src/integrations/exchange/base.py:35-45` 改为：

```python
@dataclass
class Order:
    id: str
    symbol: str
    side: str
    order_type: str
    amount: float
    price: float | None
    status: str
    fee: float | None = None
    is_algo: bool = False
    trigger_price: float | None = None   # R2-7 §4.7: SL/TP/conditional 触发阈值
```

- [ ] **Step 1.5: Run T-ORD-1 expecting pass**

```bash
uv run pytest tests/test_okx_algo_normalization.py::test_order_dataclass_has_trigger_price_field -v
```

Expected: PASS。

- [ ] **Step 1.6: Write T-ORD-2 ~ T-ORD-5 (OKX _parse_order trigger_price filling)**

**P2 校准**: `_parse_order` (okx.py:460) 是 `OKXExchange` **实例方法** (`def _parse_order(self, data: dict)`)，不能直接 import。测试需 OKXExchange 实例化或调用 `_parse_plain` / `_make_algo_order` / `_make_oco`（也都是实例方法）。

加到 `tests/test_okx_algo_normalization.py`（read existing fixtures 看 OCO/SL/TP/limit 真实样本，已知 fixtures 在 `tests/fixtures/`，由 Iter 6 task 0 抓取）：

```python
def test_t_ord_2_oco_fills_trigger_price(okx_exchange_factory):
    """T-ORD-2: OKX algoType=oco → trigger_price 填充 (P3 校准: trigger 价已在 _make_oco 的 sl_px/tp_px 参数,
    R2-7 实施在 _make_oco 内 Order 构造追加 trigger_price=price 即可)."""
    okx = okx_exchange_factory()  # 现有 fixture 模式 (按 test_okx_algo_normalization 现有 setup)
    parsed = okx._parse_order(_OCO_FIXTURE)
    # OCO returns list[Order] (stop + take_profit legs)
    assert all(o.trigger_price is not None for o in parsed), \
        f"OCO legs 应都有 trigger_price，实际 {[o.trigger_price for o in parsed]}"
    # P1 校准: order_type literals 是 "stop" / "take_profit", 不是 "stop_loss"
    types = {o.order_type for o in parsed}
    assert types == {"stop", "take_profit"}


def test_t_ord_3_stop_algo_fills_trigger_price(okx_exchange_factory):
    """T-ORD-3: OKX ordType=conditional + slTriggerPx → trigger_price 填充 (走 _make_algo_order 路径)."""
    okx = okx_exchange_factory()
    parsed = okx._parse_order(_SL_ALGO_FIXTURE)
    assert parsed[0].trigger_price is not None
    assert parsed[0].order_type == "stop"   # P1 校准: 字面 "stop", 不是 "stop_loss"


def test_t_ord_4_take_profit_algo_fills_trigger_price(okx_exchange_factory):
    """T-ORD-4: OKX ordType=conditional + tpTriggerPx → trigger_price 填充。"""
    okx = okx_exchange_factory()
    parsed = okx._parse_order(_TP_ALGO_FIXTURE)
    assert parsed[0].trigger_price is not None
    assert parsed[0].order_type == "take_profit"


def test_t_ord_5_plain_limit_no_trigger_price(okx_exchange_factory):
    """T-ORD-5: OKX plain limit (无 trigger) → trigger_price = None (走 _parse_plain 路径)."""
    okx = okx_exchange_factory()
    parsed = okx._parse_order(_PLAIN_LIMIT_FIXTURE)
    assert parsed[0].trigger_price is None
    assert parsed[0].order_type == "limit"
```

注意：
- `okx_exchange_factory` 是测试 fixture，构造一个最小可用 OKXExchange 实例（无需真实 API key — 仅调用同步 _parse_* 方法）。如 `tests/test_okx_algo_normalization.py` 现有 fixture 不存在，按现有 OKXExchange.__init__ 签名 inline 构造一个 minimal 实例。
- `_OCO_FIXTURE / _SL_ALGO_FIXTURE / _TP_ALGO_FIXTURE / _PLAIN_LIMIT_FIXTURE` 走 `tests/fixtures/` 真实 OKX 样本（Iter 6 task 0 抓取的 _OCO_RAW_DICT / etc 类）。具体 fixture 名 by `grep -rn "OCO" tests/fixtures/ tests/_fixtures.py` 定位。

- [ ] **Step 1.7: Run T-ORD-2~5 expecting failure**

```bash
uv run pytest tests/test_okx_algo_normalization.py -v -k "trigger_price"
```

Expected: 4 项 FAIL（trigger_price 全是 None / 未填充）。

- [ ] **Step 1.8: Modify okx.py to fill trigger_price (P3 校准: 改 3 个底层 helper, 不重复抠 info)**

**P3 校准**: 实测 `okx.py:460-491` 的 `_parse_order` 已经委托 `_extract_trigger_prices` (line 493-506) 抽离 sl_px / tp_px，然后传入 `_make_algo_order(data, "stop", sl_px)` (line 482) / `_make_oco(data, sl_px, tp_px)` (line 534) 作 `price=` 参数 — **trigger 价已存在 `Order.price` 字段**（line 528 / 545-546）。Plan 早期版本在 `_parse_order` 内重复抠 `info.slTriggerPx` 是错的。

**正确实施**: 在 3 个底层 Order 构造 helper 各加一行 `trigger_price=` 参数：

1. `_make_algo_order` (okx.py:521-532) — algo 类填 `trigger_price=price` (与 price 同值，algo 路径 sl_px/tp_px 即触发价)：

```python
def _make_algo_order(self, data: dict, order_type: str, price: float) -> Order:
    return Order(
        id=data["id"],
        symbol=data["symbol"],
        side=data["side"],
        order_type=order_type,
        amount=float(data["amount"]),
        price=price,
        trigger_price=price,    # ← R2-7 §4.7: algo 类 trigger_price = price 同值
        status=data["status"],
        fee=None,
        is_algo=True,
    )
```

2. `_make_oco` (okx.py:534-548) — OCO 各腿填同样的 sl_px / tp_px：

```python
def _make_oco(self, data: dict, sl_px: float, tp_px: float) -> list[Order]:
    common = {
        "id": data["id"],
        "symbol": data["symbol"],
        "side": data["side"],
        "amount": float(data["amount"]),
        "status": data["status"],
        "fee": None,
        "is_algo": True,
    }
    return [
        Order(order_type="stop", price=sl_px, trigger_price=sl_px, **common),       # ← R2-7
        Order(order_type="take_profit", price=tp_px, trigger_price=tp_px, **common), # ← R2-7
    ]
```

3. `_parse_plain` (okx.py:508-519) — plain orders **不需改**（`Order.trigger_price` 默认 None，§4.7 dataclass 默认值天然兼容）。

改动量 ~3-5 行。比早期 plan 模板（在 _parse_order 内重复抠 info）简洁很多 + 与现有 _extract_trigger_prices 抽离 不冗余。

- [ ] **Step 1.9: Write T-ORD-6 (Simulated end-to-end via fetch_open_orders, Issue 5 校准)**

`SimulatedExchange` 内**没有独立 `_make_order_from_db` helper**（实测 — 转换全部 inline 在 `fetch_order` / `fetch_open_orders` / etc 函数体内）。改用端到端测试：setup engine + 插入 SimOrder 行 + `await fetch_open_orders` → 验证 Order.trigger_price 透传。

加到 `tests/test_simulated.py` 或新建 `tests/test_simulated_order_trigger_price.py`（按现有 simulated 测试 fixture 风格）：

```python
async def test_t_ord_6_simulated_fetch_open_orders_propagates_trigger_price(
    sim_exchange_factory,  # 现有 conftest fixture, see tests/test_simulated.py
):
    """T-ORD-6: SimulatedExchange.fetch_open_orders 返回的 Order 含 trigger_price (透传 SimOrder.trigger_price)。

    P1 校准: order_type 字面量是 "stop" / "take_profit", 不是 "stop_loss" (verify simulated.py:51/196).
    """
    from src.storage.models import SimOrder
    from src.storage.database import get_session

    sim_exchange, engine, session_id = await sim_exchange_factory()
    # Insert a stop SimOrder directly (P1: order_type="stop", 不是 "stop_loss")
    async with get_session(engine) as session:
        session.add(SimOrder(
            session_id=session_id, order_id="ord-sl-1",
            symbol="BTC/USDT:USDT", side="sell", position_side="short",
            order_type="stop", amount=0.01,
            trigger_price=66000.0, status="open", leverage=5,
        ))
        await session.commit()

    orders = await sim_exchange.fetch_open_orders("BTC/USDT:USDT")
    sl = next(o for o in orders if o.order_type == "stop")
    assert sl.trigger_price == 66000.0, \
        f"Simulated SL Order.trigger_price 应透传 66000.0，实际 {sl.trigger_price!r}"
```

注：`sim_exchange_factory` 是 `tests/conftest.py` / `tests/test_simulated.py` 现有 setup fixture；如不存在就用现有 setup 模式（`asyncio` engine + `SimulatedExchange(engine, ...)` 实例化）inline 构造。

- [ ] **Step 1.10: Modify simulated.py to propagate trigger_price (Issue 4 字段映射决议)**

**字段映射决议（Issue 4 校准）**: simulated.py 现状把 `SimOrder.trigger_price` 映射到 `Order.price`（line 708/720/731/758），是因为 Order dataclass R2-7 之前没 trigger_price 字段。R2-7 加字段后，**采用方案 (a) 双填 with backward compat**:
- `Order.price`: 保持现状映射（不破坏现有依赖 Order.price 的 callers，如 cancel_order / `_render_*_order`）
- `Order.trigger_price`: **新加同样的值**（SL/TP 的 trigger_price 与 simulated 内部 price 字段实质同义；OKX side 走 §4.7 实施代码 separately 填）

理由：(b) 拆开方案需全 simulated 内部 reader audit 改造 (Order.price 在 SL/TP 类多处被读)，超出 R2-7 scope；(c) 不动 price 只加 trigger_price 等同 (b) 的下游影响。**(a) 双填**保持向后兼容 + 让 state_snapshot.pending_orders 含 trigger_price 字段供 forensic 使用即可（state_snapshot 分析者优先读 trigger_price，price 字段对 SL/TP 等同）。

实施：`src/integrations/exchange/simulated.py` 各 `Order(...)` 构造点，按 SL/TP/conditional 类**保留** `price=row.trigger_price` 现状 + **新增** `trigger_price=row.trigger_price` 字段；plain limit / market 类 `trigger_price=None`。

**重要事实校准（Issue 11）**: `SimOrder` dataclass (storage/models.py:148-163) **没有 `.price` 字段** — 仅有 `.trigger_price` (Float|None) 和 `.filled_price` (Float|None)。Simulated 现状（simulated.py:270）对 plain limit 也用 `trigger_price=price` 存下单价。所以**所有 order_type 类的 Order.price 都从 `o.trigger_price` 读**（不是不存在的 `o.price`）。

精简实施模板（按 order_type 条件分支决定 trigger_price 字段语义；P1 校准：字面 `"stop"` / `"take_profit"`，不是 `"stop_loss"`）：

```python
# 通用模式：按 order_type 决定 Order.trigger_price 字段含义
trigger_price = o.trigger_price if o.order_type in ("stop", "take_profit") else None
Order(
    id=o.order_id,
    symbol=o.symbol,
    side=o.side,
    order_type=o.order_type,
    amount=o.amount,
    price=o.trigger_price,    # SimOrder.trigger_price 对所有 order_type 都是价位字段
                              # (limit: 下单价 / "stop"/"take_profit": 触发价；现状不动)
    trigger_price=trigger_price,  # R2-7 §4.7: stop/take_profit 类填同值；limit/market 类 None
    status=o.status,
    is_algo=...,  # 现有逻辑
)
```

具体行号 by `grep -n "Order(" src/integrations/exchange/simulated.py` 定位 + 按上面模板替换（共 ~5-7 处构造点，集中在 fetch_open_orders / fetch_order / fetch_closed_orders / `_create_conditional_order`）。

**未来 cleanup 候选**（不在 R2-7 scope）：将 simulated.py 切到 (b) 拆开方案与 OKX CCXT 语义对齐（SL/TP `Order.price=None`），需配套改 cancel_order / `_render_*_order` 等下游 reader。

- [ ] **Step 1.11: Run all T-ORD tests expecting pass**

```bash
uv run pytest tests/test_okx_algo_normalization.py -v -k "trigger_price"
uv run pytest tests/ -v -k "order" 2>&1 | tail -30   # 全 order 相关测试不 regression
```

Expected: 6 T-ORD 全 PASS + 现有 order 类测试不 regression。

- [ ] **Step 1.12: Commit Task 1**

```bash
git add src/integrations/exchange/base.py src/integrations/exchange/okx.py src/integrations/exchange/simulated.py tests/test_okx_algo_normalization.py
git commit -m "feat(iter-w2r2-7): T1 BaseExchange.Order +trigger_price field (E3 a)

- Add trigger_price: float | None = None to Order dataclass (default
  preserves existing 11 callsites); OKX _parse_order fills from algo
  info fields (slTriggerPx / tpTriggerPx / stopLossPrice /
  takeProfitPrice); Simulated _make_*_order transparent passthrough
  from SimOrder.trigger_price.

- Tests +6 (T-ORD-1~6): dataclass field + 4 OKX algo type fillings +
  Simulated transparent passthrough.

R2-7 §4.7 BaseExchange.Order extension. Sets up state_snapshot.pending_orders
detail forensic (Task 2 dependency)."
```

---

## Task 2: cycle_capture.py — 双 helper

**Files:**
- Create: `src/services/cycle_capture.py`
- Test: `tests/test_cycle_capture.py`

**Spec ref:** §6.1 + §6.2 + §10.2 (T-SS-1~10 + T-TC-1~4) + AC3 + AC11

### Step 2.A: state_snapshot helper (TDD)

- [ ] **Step 2.A.1: Create test file skeleton**

`tests/test_cycle_capture.py`:

```python
"""R2-7 §10.2 — _capture_state_snapshot + _capture_trigger_context 单元测。"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.exchange.base import (
    Balance, Order, PriceLevelAlertInfo, Position, Ticker,
)
from src.services.price_alert import AlertInfo  # Issue 3 校准: AlertInfo 仅在 price_alert.py, base.py 不 re-export
from src.services.cycle_capture import (
    _capture_state_snapshot,
    _capture_trigger_context,
)


@pytest.fixture
def deps_with_position():
    """Mocked TradingDeps with one short position + balance + ticker + 1 pending limit + 0 alerts."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"

    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC/USDT:USDT", side="short", contracts=0.265,
            entry_price=75350.0, unrealized_pnl=12.34, leverage=5,
            liquidation_price=79500.0,
        )
    ])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10134.5, free_usdt=10047.3, used_usdt=87.2,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[
        Order(
            id="ord-abc", symbol="BTC/USDT:USDT", side="buy",
            order_type="limit", amount=0.013, price=75550.0, status="open",
            is_algo=False, trigger_price=None,
        )
    ])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])

    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=75123.5, bid=75123.0, ask=75124.0,
        high=76200.0, low=74900.0, base_volume=1234.56,
        timestamp=1746098096000,
    ))
    return deps


@pytest.fixture
def deps_flat():
    """Mocked TradingDeps with flat position (no position)."""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=10000.0, used_usdt=0.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=75000.0, bid=74999.0, ask=75001.0,
        high=75500.0, low=74500.0, base_volume=1000.0, timestamp=1746098096000,
    ))
    return deps
```

- [ ] **Step 2.A.2: Write T-SS-1 (no position)**

```python
async def test_state_snapshot_no_position(deps_flat):
    """T-SS-1: 无持仓 cycle → snapshot.position = None, balance/market 有值。"""
    snap = await _capture_state_snapshot("cyc-001", deps_flat)
    assert snap["position"] is None
    assert snap["balance"]["total_usdt"] == 10000.0
    assert snap["market"]["ticker_last"] == 75000.0
    assert snap["pending_orders"] == []
    assert snap["active_alerts"] == []
    assert snap["_errors"] == []
    assert snap["_cycle_id"] == "cyc-001"
```

- [ ] **Step 2.A.3: Write T-SS-2 ~ T-SS-10 (rest of state_snapshot)**

```python
async def test_state_snapshot_with_position(deps_with_position):
    """T-SS-2: 有持仓 cycle → position 含 8 字段（含 pnl_pct 衍生计算）。"""
    snap = await _capture_state_snapshot("cyc-002", deps_with_position)
    p = snap["position"]
    assert p["symbol"] == "BTC/USDT:USDT"
    assert p["side"] == "short"
    assert p["contracts"] == 0.265
    assert p["entry_price"] == 75350.0
    assert p["unrealized_pnl"] == 12.34
    assert p["leverage"] == 5
    assert p["liquidation_price"] == 79500.0
    # pnl_pct = 12.34 / (75350 * 0.265) * 100 ≈ 0.0618
    assert p["pnl_pct"] == pytest.approx(0.0618, rel=1e-3)


async def test_state_snapshot_pending_orders_detail(deps_with_position):
    """T-SS-3: pending_orders detail 完整（含 8 字段）。"""
    snap = await _capture_state_snapshot("cyc-003", deps_with_position)
    assert len(snap["pending_orders"]) == 1
    o = snap["pending_orders"][0]
    assert set(o.keys()) == {
        "id", "order_type", "side", "price", "trigger_price",
        "amount", "status", "is_algo",
    }


async def test_state_snapshot_active_alerts_detail():
    """T-SS-4: active_alerts detail 完整 + 单 symbol filter。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=1.0, free_usdt=1.0, used_usdt=0.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[
        {"id": "a1", "symbol": "BTC/USDT:USDT", "direction": "above", "price": 76000.0, "reasoning": "test"},
        {"id": "a2", "symbol": "ETH/USDT:USDT", "direction": "below", "price": 3000.0, "reasoning": "other"},
    ])
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=1.0, bid=1.0, ask=1.0, high=1.0, low=1.0,
        base_volume=1.0, timestamp=0,
    ))
    snap = await _capture_state_snapshot("cyc-004", deps)
    assert len(snap["active_alerts"]) == 1, "应只含 BTC 的 alert"
    assert snap["active_alerts"][0]["id"] == "a1"
    assert snap["active_alerts"][0]["price"] == 76000.0


async def test_state_snapshot_ticker_fetch_failed(deps_flat):
    """T-SS-5: ticker fetch 失败 → market = None + _errors 含 ticker_fetch_failed。"""
    deps_flat.market_data.get_ticker = AsyncMock(side_effect=RuntimeError("network"))
    snap = await _capture_state_snapshot("cyc-005", deps_flat)
    assert snap["market"] is None
    assert any("ticker_fetch_failed" in e for e in snap["_errors"])


async def test_state_snapshot_position_fetch_failed(deps_with_position):
    """T-SS-6: position fetch 失败 → position = None + _errors 标记。"""
    deps_with_position.exchange.fetch_positions = AsyncMock(side_effect=RuntimeError("api"))
    snap = await _capture_state_snapshot("cyc-006", deps_with_position)
    assert snap["position"] is None
    assert any("position_fetch_failed" in e for e in snap["_errors"])


async def test_state_snapshot_all_failed():
    """T-SS-7: 全部 fetch 失败 → 所有字段 None + _errors 5 项 + 不抛异常。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(side_effect=RuntimeError())
    deps.exchange.fetch_balance = AsyncMock(side_effect=RuntimeError())
    deps.exchange.fetch_open_orders = AsyncMock(side_effect=RuntimeError())
    deps.exchange.get_price_level_alerts = MagicMock(side_effect=RuntimeError())
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(side_effect=RuntimeError())

    snap = await _capture_state_snapshot("cyc-007", deps)
    assert snap["position"] is None
    assert snap["balance"] is None
    assert snap["market"] is None
    assert snap["pending_orders"] == []
    assert snap["active_alerts"] == []
    assert len(snap["_errors"]) == 5


async def test_state_snapshot_json_round_trip(deps_with_position):
    """T-SS-8: snapshot json.dumps + json.loads round-trip 不丢字段。"""
    snap = await _capture_state_snapshot("cyc-008", deps_with_position)
    serialized = json.dumps(snap)
    restored = json.loads(serialized)
    assert restored == snap


async def test_state_snapshot_balance_field_name(deps_flat):
    """T-SS-9 (E2 校准): balance 字段名是 total_usdt 不是 equity_usdt。"""
    snap = await _capture_state_snapshot("cyc-009", deps_flat)
    assert "total_usdt" in snap["balance"]
    assert "equity_usdt" not in snap["balance"]


async def test_state_snapshot_pnl_pct_zero_position():
    """T-SS-10: entry_price=0 或 contracts=0 → pnl_pct = None（不除 0）。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC/USDT:USDT", side="long", contracts=0.0,
            entry_price=0.0, unrealized_pnl=0.0, leverage=1,
            liquidation_price=None,
        )
    ])
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=1.0, free_usdt=1.0, used_usdt=0.0,
    ))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=1.0, bid=1.0, ask=1.0, high=1.0, low=1.0,
        base_volume=1.0, timestamp=0,
    ))
    snap = await _capture_state_snapshot("cyc-010", deps)
    assert snap["position"]["pnl_pct"] is None
```

注：测试用 `pytest.mark.asyncio` 还是 `pytest_asyncio` 按现有 test suite 风格选（看 `conftest.py`）。

- [ ] **Step 2.A.4: Run T-SS-1~10 expecting failure (helper not yet created)**

```bash
uv run pytest tests/test_cycle_capture.py -v
```

Expected: 全 FAIL with `ModuleNotFoundError: src.services.cycle_capture`。

- [ ] **Step 2.A.5: Create cycle_capture.py with state_snapshot helper**

`src/services/cycle_capture.py`:

```python
"""R2-7 §6.1 + §6.2: cycle 决策时刻 capture helpers.

两 helper:
- _capture_trigger_context: trigger metadata DB 端镜像 (dataclass → JSON dict)
- _capture_state_snapshot: 决策时系统层面客观快照 (持仓 / 余额 / 现价 / pending / alerts)

Best-effort 容错: 异常 → 字段 None + _errors 标记 + log warning + cycle 继续.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.integrations.exchange.base import FillEvent, PriceLevelAlertInfo
from src.services.price_alert import AlertInfo

if TYPE_CHECKING:
    from src.agent.trader import TradingDeps

logger = logging.getLogger(__name__)


def _capture_trigger_context(cycle_id: str, trigger_type: str, context) -> dict | None:
    """Capture trigger metadata for DB. Best-effort: any exception → None.

    Args:
        cycle_id: 当前 cycle_id (用于日志反查)
        trigger_type: scheduled / conditional / alert
        context: trigger 携带 metadata (FillEvent / PriceLevelAlertInfo / AlertInfo / None)
    """
    try:
        if trigger_type == "scheduled":
            return {"type": "scheduled_tick"}
        if trigger_type == "conditional" and context is not None:
            # FillEvent (base.py:269-281): 11 字段全保留
            return {
                "type": "fill",
                "trigger_reason": context.trigger_reason,
                "symbol": context.symbol,
                "side": context.side,
                "position_side": context.position_side,
                "amount": context.amount,
                "fill_price": context.fill_price,
                "fee": context.fee,
                "pnl": context.pnl,
                "order_id": context.order_id,
                "timestamp": context.timestamp,
                "is_full_close": context.is_full_close,
            }
        if trigger_type == "alert" and context is not None:
            if isinstance(context, PriceLevelAlertInfo):
                # base.py:284-291
                return {
                    "type": "price_level_alert",
                    "symbol": context.symbol,
                    "current_price": context.current_price,
                    "target_price": context.target_price,
                    "direction": context.direction,
                    "reasoning": context.reasoning,
                    "timestamp": context.timestamp,
                }
            if isinstance(context, AlertInfo):
                # src/services/price_alert.py:9-15
                return {
                    "type": "percentage_alert",
                    "symbol": context.symbol,
                    "current_price": context.current_price,
                    "reference_price": context.reference_price,
                    "change_pct": context.change_pct,
                    "window_minutes": context.window_minutes,
                    "timestamp": context.timestamp,
                }
        return None
    except Exception as e:
        logger.warning(
            "trigger_context capture failed (cycle_id=%s, trigger_type=%s, context_type=%s): %s",
            cycle_id, trigger_type, type(context).__name__, e,
        )
        return None


async def _capture_state_snapshot(cycle_id: str, deps: TradingDeps) -> dict:
    """Capture system-side objective state at decision time. Best-effort per-field."""
    snapshot: dict = {
        "position": None,
        "balance": None,
        "market": None,
        "pending_orders": [],
        "active_alerts": [],
        "_errors": [],
        "_cycle_id": cycle_id,
    }

    # 1. position (best-effort) — Position dataclass (base.py:78-87)
    try:
        positions = await deps.exchange.fetch_positions(deps.symbol)
        if positions:
            p = positions[0]
            notional = p.entry_price * p.contracts if p.entry_price > 0 and p.contracts > 0 else 0.0
            pnl_pct = (p.unrealized_pnl / notional * 100) if notional > 0 else None
            snapshot["position"] = {
                "symbol": p.symbol,
                "side": p.side,
                "contracts": p.contracts,
                "entry_price": p.entry_price,
                "unrealized_pnl": p.unrealized_pnl,
                "leverage": p.leverage,
                "liquidation_price": p.liquidation_price,
                "pnl_pct": pnl_pct,
            }
    except Exception as e:
        msg = f"position_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 2. balance (best-effort) — Balance dataclass (base.py:48-52)
    try:
        balance = await deps.exchange.fetch_balance()
        snapshot["balance"] = {
            "total_usdt": balance.total_usdt,
            "free_usdt": balance.free_usdt,
            "used_usdt": balance.used_usdt,
        }
    except Exception as e:
        msg = f"balance_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 3. market (best-effort) — Ticker dataclass (base.py:13-22)
    try:
        ticker = await deps.market_data.get_ticker(deps.symbol)
        snapshot["market"] = {
            "ticker_last": ticker.last,
            "ticker_timestamp": ticker.timestamp,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        msg = f"ticker_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 4. pending orders (best-effort) — Order dataclass + R2-7 §4.7 trigger_price
    try:
        orders = await deps.exchange.fetch_open_orders(deps.symbol)
        snapshot["pending_orders"] = [
            {
                "id": o.id,
                "order_type": o.order_type,
                "side": o.side,
                "price": o.price,
                "trigger_price": o.trigger_price,
                "amount": o.amount,
                "status": o.status,
                "is_algo": o.is_algo,
            }
            for o in orders
        ]
    except Exception as e:
        msg = f"open_orders_fetch_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    # 5. active alerts (no IO) — single-symbol filter (Issue 6: cycle 是单 symbol 上下文)
    try:
        all_alerts = deps.exchange.get_price_level_alerts()
        snapshot["active_alerts"] = [
            {
                "id": a["id"],
                "direction": a["direction"],
                "price": a["price"],
                "reasoning": a.get("reasoning", ""),
            }
            for a in all_alerts
            if a["symbol"] == deps.symbol
        ]
    except Exception as e:
        msg = f"alerts_read_failed: {type(e).__name__}"
        snapshot["_errors"].append(msg)
        logger.warning("state_snapshot capture: cycle_id=%s %s", cycle_id, msg)

    return snapshot
```

- [ ] **Step 2.A.6: Run T-SS-1~10 expecting pass**

```bash
uv run pytest tests/test_cycle_capture.py -v -k "state_snapshot or T_SS"
```

Expected: 10 项全 PASS。

### Step 2.B: trigger_context helper (TDD)

- [ ] **Step 2.B.1: Write T-TC-1~4**

加到 `tests/test_cycle_capture.py`：

```python
def test_trigger_context_scheduled():
    """T-TC-1: scheduled trigger → {type: scheduled_tick}。"""
    result = _capture_trigger_context("cyc-tc1", "scheduled", None)
    assert result == {"type": "scheduled_tick"}


def test_trigger_context_fill_event():
    """T-TC-2: conditional FillEvent → 12 字段全保留 (P1-2)。"""
    fe = FillEvent(
        order_id="ord-1", symbol="BTC/USDT:USDT", side="sell",
        position_side="short", trigger_reason="stop_loss",
        fill_price=75600.0, amount=0.265, fee=1.5, pnl=-125.0,
        timestamp=1746098000000, is_full_close=True,
    )
    result = _capture_trigger_context("cyc-tc2", "conditional", fe)
    assert result["type"] == "fill"
    assert result["trigger_reason"] == "stop_loss"
    assert result["fee"] == 1.5
    assert result["position_side"] == "short"
    assert result["timestamp"] == 1746098000000
    assert result["is_full_close"] is True
    assert set(result.keys()) == {
        "type", "trigger_reason", "symbol", "side", "position_side",
        "amount", "fill_price", "fee", "pnl", "order_id", "timestamp",
        "is_full_close",
    }


def test_trigger_context_price_level_alert():
    """T-TC-3: PriceLevelAlertInfo → 7 字段含 timestamp (P1-1)。"""
    pla = PriceLevelAlertInfo(
        symbol="BTC/USDT:USDT", target_price=75600.0, direction="above",
        current_price=75623.0, reasoning="FOMC reaction watch",
        timestamp=1746098000000,
    )
    result = _capture_trigger_context("cyc-tc3", "alert", pla)
    assert result["type"] == "price_level_alert"
    assert result["target_price"] == 75600.0
    assert result["timestamp"] == 1746098000000
    assert set(result.keys()) == {
        "type", "symbol", "current_price", "target_price",
        "direction", "reasoning", "timestamp",
    }


def test_trigger_context_percentage_alert():
    """T-TC-4: AlertInfo (percentage) → 7 字段 reference_price/change_pct/window_minutes (E4 校准)。"""
    ai = AlertInfo(
        symbol="BTC/USDT:USDT", current_price=76847.0, reference_price=75123.0,
        change_pct=2.3, window_minutes=60, timestamp=1746098000000,
    )
    result = _capture_trigger_context("cyc-tc4", "alert", ai)
    assert result["type"] == "percentage_alert"
    assert result["reference_price"] == 75123.0
    assert result["change_pct"] == 2.3
    assert result["window_minutes"] == 60
    assert set(result.keys()) == {
        "type", "symbol", "current_price", "reference_price",
        "change_pct", "window_minutes", "timestamp",
    }


def test_trigger_context_attribute_error_fallback():
    """T-TC-5 (Issue 2): context 类型不符 → AttributeError → return None + log warning。"""
    bad_ctx = MagicMock(spec=[])  # 无任何属性
    result = _capture_trigger_context("cyc-tc5", "conditional", bad_ctx)
    assert result is None
```

- [ ] **Step 2.B.2: Run T-TC-1~5 expecting pass**

```bash
uv run pytest tests/test_cycle_capture.py -v -k "trigger_context"
```

Expected: 5 项全 PASS。

- [ ] **Step 2.B.3: Commit Task 2**

```bash
git add src/services/cycle_capture.py tests/test_cycle_capture.py
git commit -m "feat(iter-w2r2-7): T2 cycle_capture helpers (state_snapshot + trigger_context)

- src/services/cycle_capture.py: 双 helper for R2-7 §6.1/§6.2
  - _capture_trigger_context: trigger metadata DB 镜像
    (FillEvent 11+1 / PriceLevelAlertInfo 6+1 / AlertInfo 6+1 fields)
  - _capture_state_snapshot: 系统客观快照
    (Position 8 / Balance 3 / market 3 / pending Order 8 / alerts 4 fields)

- Best-effort 容错: 异常 → 字段 None + _errors 标记 + cycle_id 日志
- Single-symbol alert filter (Issue 6: cycle 单 symbol 上下文)
- pnl_pct 衍生计算 (entry_price * contracts > 0; 否则 None)

- Tests +15 (T-SS-1~10 + T-TC-1~5).

R2-7 §6.1 + §6.2 implementation. Sets up cli/app.py write path
rewrite (Task 4 dependency)."
```

---

## Task 3: storage/models.py + Alembic migration

**Files:**
- Modify: `src/storage/models.py:77-95` (DecisionLog → AgentCycle)
- Create: `alembic/versions/<rev>_r2_7_agent_cycle_schema_reframe.py`
- Test: `tests/test_alembic_migration.py` (加 R2-7 段 T-MIG-1~8)
- Modify: imports in `src/cli/app.py:35` + 间接 import sites

**Spec ref:** §4 + §7 (migration) + §10.2 (T-MIG-1~8) + AC1 + AC2 + AC10 + AC14

- [ ] **Step 3.1: Read existing alembic head + storage/models.py current state**

```bash
ls alembic/versions/
sed -n '77,95p' src/storage/models.py
uv run alembic current
```

确认 head 是 `e7b2bd73c131_r2_4_decision_subtypes_and_biz_error_`。

- [ ] **Step 3.2: Generate new alembic revision**

```bash
uv run alembic revision -m "r2_7 agent_cycle schema reframe"
```

记录 new revision id，如 `<NEW_REV>`。文件位于 `alembic/versions/<NEW_REV>_r2_7_agent_cycle_schema_reframe.py`。

- [ ] **Step 3.3: Write migration upgrade + downgrade**

替换 stub 内容：

```python
"""r2_7 agent_cycle schema reframe

Revision ID: <NEW_REV>
Revises: e7b2bd73c131
Create Date: 2026-05-01 ...

R2-7 spec §7.1: rename table + 5 columns + decision Text/nullable + state_snapshot.
"""
from __future__ import annotations
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "<NEW_REV>"
down_revision: str | None = "e7b2bd73c131"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: rename table
    op.rename_table("decision_logs", "agent_cycles")

    # Step 2: batch_alter (SQLite 限制)
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.alter_column("trigger_type", new_column_name="triggered_by")
        batch_op.alter_column("market_summary", new_column_name="trigger_context")
        batch_op.alter_column("status", new_column_name="execution_status")
        batch_op.alter_column("model_used", new_column_name="model_id")
        batch_op.alter_column("tokens_used", new_column_name="tokens_consumed")

        batch_op.alter_column(
            "decision",
            existing_type=sa.String(30),
            type_=sa.Text(),
            existing_nullable=False,
            nullable=True,
        )

        batch_op.add_column(sa.Column("state_snapshot", sa.Text(), nullable=True))

    # Step 3: rename index (SQLite drop + recreate)
    op.drop_index("ix_decision_logs_session_id_cycle_id", table_name="agent_cycles")
    op.create_index(
        "ix_agent_cycles_session_id_cycle_id",
        "agent_cycles",
        ["session_id", "cycle_id"],
    )


def downgrade() -> None:
    # Escape hatch (Issue 4): 若 R2-7 后有 forensic NULL 行，先手动清理:
    # DELETE FROM agent_cycles WHERE execution_status='usage_limit_exceeded' AND decision IS NULL;
    op.drop_index("ix_agent_cycles_session_id_cycle_id", table_name="decision_logs")
    op.create_index(
        "ix_decision_logs_session_id_cycle_id",
        "decision_logs",
        ["session_id", "cycle_id"],
    )
    with op.batch_alter_table("agent_cycles", schema=None) as batch_op:
        batch_op.drop_column("state_snapshot")
        batch_op.alter_column(
            "decision",
            existing_type=sa.Text(),
            type_=sa.String(30),
            existing_nullable=True,
            nullable=False,
        )
        batch_op.alter_column("tokens_consumed", new_column_name="tokens_used")
        batch_op.alter_column("model_id", new_column_name="model_used")
        batch_op.alter_column("execution_status", new_column_name="status")
        batch_op.alter_column("trigger_context", new_column_name="market_summary")
        batch_op.alter_column("triggered_by", new_column_name="trigger_type")
    op.rename_table("agent_cycles", "decision_logs")
```

- [ ] **Step 3.4: Modify storage/models.py — DecisionLog → AgentCycle**

`src/storage/models.py:77-95` 整段替换：

```python
class AgentCycle(Base):
    """One agent cycle record — captures前因 (triggered_by/trigger_context) →
    决策时现状 (state_snapshot) → agent 推理 (reasoning=thinking) →
    agent 决策 (decision=message). R2-7 五维度叙事 framing."""

    __tablename__ = "agent_cycles"
    __table_args__ = (
        Index("ix_agent_cycles_session_id_cycle_id", "session_id", "cycle_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(String(50))
    triggered_by: Mapped[str] = mapped_column(String(20))                  # scheduled / conditional / alert
    trigger_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: trigger 瞬间客观快照
    state_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON: 决策时系统层面客观状态 (R2-7 §4.4)
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)         # message content (R2-7: was String(30) enum, 改 Text+nullable)
    execution_status: Mapped[str] = mapped_column(String(30), default="ok", server_default="ok")  # ok / usage_limit_exceeded
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)        # thinking content (R2-7: was result.output message)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_consumed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
```

- [ ] **Step 3.5: Update import sites**

```bash
grep -rn "from src.storage.models import.*DecisionLog\|import DecisionLog" src/ tests/ --include="*.py"
```

每个 hit 改 `DecisionLog` → `AgentCycle`。已知关键位点：
- `src/cli/app.py:35`: `from src.storage.models import DecisionLog, Session, TradeAction` → `AgentCycle, Session, TradeAction`

注：cli/app.py 内的 `DecisionLog(...)` 构造调用 + `decision=` 等字段名暂时保留为旧名，Task 4 一并改写。**本 Task 仅改 import 名**让 Task 4 可以基于新名字工作。

- [ ] **Step 3.6: Write T-MIG-1~8 (P4 校准: 用现有 alembic_cfg_factory + tmp_path + sqlite3 PRAGMA 模式)**

**P4 校准**: `tests/test_alembic_migration.py:14-25` 现有 fixture 是 `alembic_cfg_factory(db_path: Path) -> Config`（接 monkeypatch + TRADEBOT_DB_URL env）。R2-4 段（line 295-373）模式：`tmp_path` + `_create_pre_alembic_schema(db_path)` + `command.upgrade(cfg, "head")` + `sqlite3.connect(db_path)` + `cur.execute("PRAGMA table_info(...)")`。**Plan 早期版本用 `in_memory_engine_factory` / `upgrade_to_head` 是错的**（这些 helper 不存在）。

加到 `tests/test_alembic_migration.py` 末尾（保留 Iter 3 + R2-4 段不动），按 R2-4 现有测试模式：

```python
def test_t_mig_1_table_renamed_to_agent_cycles(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-1: R2-7 upgrade 后 agent_cycles 存在 + decision_logs 不存在。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    names = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "agent_cycles" in names
    assert "decision_logs" not in names
    conn.close()


def test_t_mig_2_columns_renamed(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-2: 5 列重命名生效."""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(agent_cycles)")}
    expected = {
        "id", "session_id", "cycle_id", "triggered_by", "trigger_context",
        "state_snapshot", "decision", "execution_status", "reasoning",
        "model_id", "tokens_consumed", "created_at",
    }
    assert cols == expected, f"agent_cycles 列集 {cols} ≠ {expected}"
    conn.close()


def test_t_mig_3_decision_type_text_nullable(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-3: decision = TEXT + nullable=1（v8 spec §4.5）。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(agent_cycles)")}
    # PRAGMA columns: (cid, name, type, notnull, dflt_value, pk)
    assert cols["decision"][2] == "TEXT", f"decision type {cols['decision'][2]} ≠ TEXT"
    assert cols["decision"][3] == 0, f"decision notnull {cols['decision'][3]} ≠ 0 (期望 nullable)"
    conn.close()


def test_t_mig_4_state_snapshot_column_exists(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-4: state_snapshot TEXT nullable 新加列存在。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(agent_cycles)")}
    assert "state_snapshot" in cols
    assert cols["state_snapshot"][2] == "TEXT"
    assert cols["state_snapshot"][3] == 0  # nullable
    conn.close()


def test_t_mig_5_index_renamed(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-5: 索引 ix_agent_cycles_session_id_cycle_id 存在 + 旧索引不存在."""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    indexes = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='agent_cycles'"
    )}
    assert "ix_agent_cycles_session_id_cycle_id" in indexes
    # 旧索引应不在 agent_cycles 上
    old_indexes = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_decision_logs_session_id_cycle_id'"
    )}
    assert old_indexes == set()
    conn.close()


def test_t_mig_6_historical_data_compat(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-6: 历史 decision_logs 行 schema 兼容 (旧 enum 短串保留 + 行数不变)."""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    # 跑到 R2-4 head 后插入旧数据
    command.upgrade(cfg, "e7b2bd73c131")  # R2-4 revision
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO decision_logs (session_id, cycle_id, trigger_type, decision, status, tokens_used, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        [
            ("s1", "cyc-1", "scheduled", "open_long", "ok", 1000),
            ("s1", "cyc-2", "scheduled", "adjust_protect", "ok", 1500),
            ("s1", "cyc-3", "scheduled", "hold", "usage_limit_exceeded", 0),
        ],
    )
    conn.commit()
    conn.close()
    # 跑 R2-7 upgrade
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = list(cur.execute(
        "SELECT cycle_id, decision, execution_status FROM agent_cycles ORDER BY cycle_id"
    ))
    assert len(rows) == 3
    decisions = [r[1] for r in rows]
    assert decisions == ["open_long", "adjust_protect", "hold"], \
        f"历史 enum 短串保留, 实际 {decisions}"
    conn.close()


def test_t_mig_7_downgrade_succeeds_when_no_null_decision(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-7: 无 NULL decision 行时 downgrade 回滚成功。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")
    # 不插任何 R2-7 后写入 (avoid forensic NULL decision)
    command.downgrade(cfg, "-1")
    # 现在 head 应回到 R2-4 (e7b2bd73c131)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    names = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "decision_logs" in names
    assert "agent_cycles" not in names
    conn.close()


def test_t_mig_8_execution_status_server_default_preserved(tmp_path: Path, alembic_cfg_factory):
    """T-MIG-8 (M5/AC14): batch_alter rename 后 execution_status 仍有 server_default='ok'。"""
    db_path = tmp_path / "test.db"
    _create_pre_alembic_schema(db_path)
    cfg = alembic_cfg_factory(db_path)
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cols = {r[1]: r for r in cur.execute("PRAGMA table_info(agent_cycles)")}
    # PRAGMA dflt_value (index 4) — SQLite 返回带引号的字符串字面量 "'ok'"
    assert cols["execution_status"][4] in ("'ok'", "ok"), \
        f"execution_status server_default 期望 'ok', 实际 {cols['execution_status'][4]!r}"
    conn.close()
```

注意：`alembic_cfg_factory` / `_create_pre_alembic_schema` / `sqlite3` / `command` 全部是 `tests/test_alembic_migration.py` 现有 imports + fixtures，与 R2-4 段（line 295-373）完全同模式。implementer 不需要新建 helper。

- [ ] **Step 3.7: Run T-MIG-1~8**

```bash
uv run pytest tests/test_alembic_migration.py -v -k "t_mig"
```

Expected: 8 项全 PASS。

- [ ] **Step 3.8: Run alembic up/down sanity check**

```bash
# 用临时 DB 验证 chain 完整性
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
```

Expected: 双向无错。

- [ ] **Step 3.9: Run full test suite (期望 cli/app.py 仍有断点 — 由 Task 4 修复)**

```bash
uv run pytest 2>&1 | tail -50
```

Expected: 部分测试 FAIL（cli/app.py 内 `DecisionLog(...)` 构造、`decision=` 写入仍是旧名 → import 后构造旧表会爆）。本 Task 暂留这些 failure 给 Task 4 修复（不引入 broken-builds-go-to-CI 风险，因为 R2-7 是 feature branch）。

- [ ] **Step 3.10: Commit Task 3**

```bash
git add alembic/versions/<NEW_REV>_r2_7_agent_cycle_schema_reframe.py src/storage/models.py src/cli/app.py tests/test_alembic_migration.py
git commit -m "feat(iter-w2r2-7): T3 schema rename + Alembic migration

- src/storage/models.py: DecisionLog → AgentCycle, 5 fields rename
  (trigger_type/market_summary/status/model_used/tokens_used →
  triggered_by/trigger_context/execution_status/model_id/tokens_consumed),
  decision String(30)→Text+nullable, +state_snapshot Text|None.

- alembic/versions/<NEW_REV>_r2_7_*.py: batch_alter migration
  (rename table + 5 cols + decision type/null + add state_snapshot
  + drop_index/create_index recreate). downgrade with escape hatch
  comment for forensic NULL rows.

- src/cli/app.py:35: import rename DecisionLog → AgentCycle (Task 4
  will rewrite write path body).

- Tests +8 (T-MIG-1~8): table rename, columns rename, decision type
  TEXT/nullable, state_snapshot column, index rename, historical data
  compat, downgrade no-null, server_default preserved (M5).

R2-7 §4 + §7 implementation. Sets up cli/app.py write path rewrite
(Task 4 dependency)."
```

---

## Task 4: cli/app.py — 删派生 + 写入路径改造

**Files:**
- Modify: `src/cli/app.py:51-149` (删派生路线整套) + `:54` (stale comment) + `:217-282/355-369/378-383` (写入路径改造)
- Delete: `tests/test_derive_decision.py`
- Delete: `tests/test_decision_log_e2e.py`
- Delete: `docs/metrics/decision-enum-timeline.md`
- Test: `tests/test_cycle_capture.py` (加 ThinkingPart 提取测试 T-TH-1~4)
- Test: `tests/test_usage_limits.py` (T-WP-1~3 改写)

**Spec ref:** §5 (deprecation) + §6.3 (ThinkingPart) + §6.4-§6.7 (写入) + AC4 + AC5 + AC6

### Step 4.A: 删派生路线整套

- [ ] **Step 4.A.1: Delete deprecation files**

```bash
rm tests/test_derive_decision.py
rm tests/test_decision_log_e2e.py
rm docs/metrics/decision-enum-timeline.md
```

- [ ] **Step 4.A.2: Strip src/cli/app.py 派生段**

`src/cli/app.py` 删除：
- Line 51-76: 5 ACTIONS frozenset 常量段（含 `# Iter 4 §3.2 + R2-4 spec §5.3` 注释）
- Line 78-85: `DERIVE_DECISION_VALUES` SoT
- Line 88-149: `async def _derive_decision_from_actions(...)` 函数体
- ~Line 54 stale comment: `# trade_actions 留底，未来若数据反证可仅重派生历史 decision_logs.decision，无需 schema 演进`

具体行号在改 storage/models.py 后会偏移，按 grep 定位：

```bash
grep -n "_derive_decision_from_actions\|PROTECT_ACTIONS\|ENTRY_ORDER_ACTIONS\|LEVERAGE_ACTIONS\|ALERT_ACTIONS\|ADJUST_ACTIONS\|DERIVE_DECISION_VALUES\|trade_actions 留底" src/cli/app.py
```

按 grep 结果整段删除。

- [ ] **Step 4.A.3: Run tests confirming派生测试已删 + 派生引用已无**

```bash
uv run pytest tests/test_derive_decision.py tests/test_decision_log_e2e.py 2>&1 | tail -5
```

Expected: `ERROR: file or directory not found` (期望文件已删)。

```bash
grep -rn "_derive_decision_from_actions\|PROTECT_ACTIONS\|DERIVE_DECISION_VALUES" src/ tests/
```

Expected: 0 hit。

### Step 4.B: ThinkingPart 提取 + 写入路径改造

- [ ] **Step 4.B.1: Read current run_agent_cycle for context**

```bash
sed -n '203,387p' src/cli/app.py
```

读取目标：当前 `run_agent_cycle` 完整 try/except 流程 + tool_calls 提取段 + DecisionLog 写入段。

- [ ] **Step 4.B.2: Write T-TH-1~4 (ThinkingPart 提取测试)**

加到 `tests/test_cycle_capture.py`（与 helper 同 module 测试方便）：

```python
def test_extract_thinking_from_messages_with_thinking_part():
    """T-TH-1: thinking model (mock ThinkingPart) → reasoning = 拼接 content。"""
    from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
    from src.cli.app import _extract_thinking_text   # Task 4 内新建 helper

    msgs = [
        ModelResponse(parts=[
            ThinkingPart(content="reasoning step 1"),
            TextPart(content="visible output"),
        ])
    ]
    text = _extract_thinking_text(msgs)
    assert text == "reasoning step 1"


def test_extract_thinking_no_thinking_part_returns_none():
    """T-TH-2: 非 thinking model (无 ThinkingPart) → reasoning = None。"""
    from pydantic_ai.messages import ModelResponse, TextPart
    from src.cli.app import _extract_thinking_text

    msgs = [ModelResponse(parts=[TextPart(content="output only")])]
    assert _extract_thinking_text(msgs) is None


def test_extract_thinking_multiple_parts_joined():
    """T-TH-3: 多个 ThinkingPart → 用 \\n\\n 拼接。"""
    from pydantic_ai.messages import ModelResponse, ThinkingPart
    from src.cli.app import _extract_thinking_text

    msgs = [
        ModelResponse(parts=[ThinkingPart(content="part 1")]),
        ModelResponse(parts=[ThinkingPart(content="part 2")]),
    ]
    text = _extract_thinking_text(msgs)
    assert text == "part 1\n\npart 2"


def test_extract_thinking_no_truncation():
    """T-TH-4: thinking content 长度 > 4000 → 不截断。"""
    from pydantic_ai.messages import ModelResponse, ThinkingPart
    from src.cli.app import _extract_thinking_text

    long_text = "x" * 5000
    msgs = [ModelResponse(parts=[ThinkingPart(content=long_text)])]
    text = _extract_thinking_text(msgs)
    assert len(text) == 5000
```

- [ ] **Step 4.B.3: Run T-TH-1~4 expecting failure**

```bash
uv run pytest tests/test_cycle_capture.py -v -k "extract_thinking"
```

Expected: 全 FAIL with `ImportError: cannot import name '_extract_thinking_text'`。

- [ ] **Step 4.B.4: Add _extract_thinking_text helper to cli/app.py**

`src/cli/app.py` (在 imports 之后、`run_agent_cycle` 之前合适位置)：

```python
from pydantic_ai.messages import ThinkingPart


def _extract_thinking_text(messages) -> str | None:
    """R2-7 §6.3: 遍历 result.new_messages() 找所有 ThinkingPart 拼接 content."""
    parts: list[str] = []
    for msg in messages:
        for part in getattr(msg, "parts", []):
            if isinstance(part, ThinkingPart):
                parts.append(part.content)
    return "\n\n".join(parts) if parts else None
```

- [ ] **Step 4.B.5: Run T-TH-1~4 expecting pass**

```bash
uv run pytest tests/test_cycle_capture.py -v -k "extract_thinking"
```

Expected: 4 项全 PASS。

- [ ] **Step 4.B.6: Rewrite run_agent_cycle write path**

`src/cli/app.py` `run_agent_cycle` 函数内：

1. **Top-level imports 加 (P7 校准)**：

`src/cli/app.py` 文件顶部 import 段（与现有 `from src.storage.models import AgentCycle, Session, TradeAction` 同处）：

```python
from pydantic_ai.messages import ThinkingPart   # 仅 ThinkingPart class 已 import via pydantic_ai 现有 import
from src.services.cycle_capture import _capture_trigger_context, _capture_state_snapshot
import json   # if not already imported
```

避免函数内 `from ... import ...`（违反 PEP 8，ruff/flake8 会 lint）。

2. **cycle_id 生成后立即 capture (retry loop 之前一次性, P8 校准)**：

```python
# After cycle_id = str(uuid.uuid4())[:8] (~line 217)
deps.cycle_id = cycle_id

# R2-7 §6.7: capture trigger_context + state_snapshot 在 retry loop 之前 (一次, 复用 *_var)
# P8 校准: 必须在 `for attempt in range(3):` retry loop **之前**, 不能在 loop 内
# (capture 应只发生一次; 重复 capture 会让 IO 4× retry + state_snapshot 时刻漂移 + 违反 §6.7 不变量)
trigger_context_var = _capture_trigger_context(cycle_id, trigger_type, context)
state_snapshot_var = await _capture_state_snapshot(cycle_id, deps)

prompt = (
    f"You have been woken up by a {trigger_type} trigger.\n"
    ...
)
# (prompt 拼接逻辑保持现状，agent 看到的内容不变)

# 现有 retry loop (cli/app.py:256-292) 在此 capture 段之后开始:
# for attempt in range(3):
#     try: result = await agent.run(...) ...
```

3. **Forensic 路径 (UsageLimitExceeded)**：

```python
except UsageLimitExceeded as e:
    logger.error(f"Cycle {cycle_id} hit usage limit: {e}")
    async with get_session(engine) as session:
        session.add(AgentCycle(
            session_id=deps.session_id,
            cycle_id=cycle_id,
            triggered_by=trigger_type,
            trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
            state_snapshot=json.dumps(state_snapshot_var),
            reasoning=None,                              # R2-7 §6.5: forensic NULL
            decision=None,
            execution_status="usage_limit_exceeded",
            model_id=getattr(model, 'model_name', str(model)) if model else str(agent.model),
            tokens_consumed=0,
        ))
        await session.commit()
    return None
```

4. **Success 路径**：

```python
# After tool_calls extraction loop completes
thinking_text = _extract_thinking_text(result.new_messages())
async with get_session(engine) as session:
    session.add(AgentCycle(
        session_id=deps.session_id,
        cycle_id=cycle_id,
        triggered_by=trigger_type,
        trigger_context=json.dumps(trigger_context_var) if trigger_context_var else None,
        state_snapshot=json.dumps(state_snapshot_var),
        reasoning=thinking_text,
        decision=result.output,
        execution_status="ok",
        model_id=getattr(model, 'model_name', str(model)) if model else str(agent.model),
        tokens_consumed=tokens,
    ))
    await session.commit()
```

5. **Retry-failure 路径（Issue 8 校准 — 明牌不动）**：

`cli/app.py:285-292` 的 "3 次 LLM 重试都失败 → return None" 路径既不是 success 也不是 forensic（不抛 UsageLimitExceeded）。**R2-7 沿用现状：retry-failure 路径不写 DB 行**，与现有 cli/app.py 行为一致。

理由：
- spec §6 仅覆盖 success / forensic 二分；retry-failure 不在 R2-7 范围
- 引入新 `execution_status='retry_failed'` 是新行为改造，违反 R2-7 "纯存储路径 + schema 改造"纪律
- 即使 `*_var` 已 in scope（capture 在 try 之前），也**不应**自作主张写入 DB；是否记录 retry-failure 是独立 ops 议题，留 W2 期间数据驱动

implementer 不要在 retry-failure 路径添加 AgentCycle.add() 调用。

注：`json` 模块需要 import (top of file)。display.py 调用 `format_cycle_output(...)` 的 param 名不改（D 决议：trigger_type / agent_output / tokens_used 保留为渲染层标签，agent_output 仍传 `result.output`）。

- [ ] **Step 4.B.7: Write T-WP-1~3 (写入路径)**

改写 `tests/test_usage_limits.py` 现有 t1/t9 类测试或新增：

```python
async def test_wp_1_success_path_writes_thinking_message_state(...):
    """T-WP-1: success 路径 → reasoning=thinking, decision=result.output, state_snapshot 不为 NULL。"""
    # mock ThinkingPart in result.new_messages(), result.output = "decision text"
    # 跑 run_agent_cycle, query AgentCycle
    # assert row.reasoning == thinking text
    # assert row.decision == "decision text"
    # assert row.state_snapshot is not None (json valid)


async def test_wp_2_forensic_path_writes_null_decision_reasoning(...):
    """T-WP-2: UsageLimitExceeded → reasoning/decision=NULL, status=usage_limit_exceeded, state_snapshot 不 NULL."""
    # mock agent.run raise UsageLimitExceeded
    # query AgentCycle row
    # assert row.reasoning is None
    # assert row.decision is None
    # assert row.execution_status == "usage_limit_exceeded"
    # assert row.state_snapshot is not None
    # assert row.tokens_consumed == 0


async def test_wp_3_forensic_path_writes_trigger_context(...):
    """T-WP-3: forensic 路径 trigger_context 仍写入。"""
    # mock 同 T-WP-2
    # assert row.trigger_context is not None (json valid)
```

详细实施按 `tests/test_usage_limits.py` 现有 t2/t9 mock 模式（`mock_agent.run = AsyncMock(side_effect=UsageLimitExceeded(...))`）扩展。

- [ ] **Step 4.B.8: Run T-WP-1~3**

```bash
uv run pytest tests/test_usage_limits.py -v -k "wp_"
```

Expected: 3 项全 PASS。

- [ ] **Step 4.B.9: Commit Task 4**

```bash
git add src/cli/app.py tests/test_cycle_capture.py tests/test_usage_limits.py
git rm tests/test_derive_decision.py tests/test_decision_log_e2e.py docs/metrics/decision-enum-timeline.md
git commit -m "feat(iter-w2r2-7): T4 cli/app.py rewrite (delete derive + new write path)

- Delete R2-4 derive enum infrastructure整套:
  - src/cli/app.py: 5 ACTIONS frozenset constants + DERIVE_DECISION_VALUES SoT
    + _derive_decision_from_actions function (~80 lines stripped)
    + L54 stale comment 'trade_actions 留底...重派生历史 decision_logs.decision'
  - tests/test_derive_decision.py (27 tests deleted)
  - tests/test_decision_log_e2e.py (e2e derive test deleted)
  - docs/metrics/decision-enum-timeline.md (deprecated, replaced by
    docs/metrics/agent-cycles-schema.md in T6)

- Rewrite run_agent_cycle write path:
  - capture trigger_context_var / state_snapshot_var ONCE before try
    block (§6.7 invariant: success/forensic both reuse vars)
  - _extract_thinking_text helper: traverse result.new_messages() find
    ThinkingPart, join with \\n\\n, return None if no thinking parts
  - Success path: AgentCycle(reasoning=thinking_text, decision=result.output,
    execution_status='ok', ...)
  - Forensic path (UsageLimitExceeded): AgentCycle(reasoning=None,
    decision=None, execution_status='usage_limit_exceeded',
    tokens_consumed=0, state_snapshot 仍写入 — capture 在 try 之前)

- N9 limit-order derive blindspot wontfix-by-design (派生整套删除).

- Tests +7 (T-TH-1~4 ThinkingPart extraction + T-WP-1~3 write path).

R2-7 §5 + §6 implementation. Closes derive route deprecation."
```

---

## Task 5: 现有测试迁移

**Files:**
- Modify: `tests/test_storage.py:60-69`
- Modify: `tests/test_usage_limits.py:11/103/110/210-227/268-274` (字段名 + 删 enum 断言)
- Modify: `tests/test_display_cycle.py` (无需改 — D 决议: display.py param 名不变)
- Modify: `tests/test_cycle_log.py:75/106/132` (run_agent_cycle 入参 trigger_type 不改)

**Spec ref:** §10.3 + AC8 + AC9

- [ ] **Step 5.1: Update tests/test_storage.py**

```bash
sed -n '60,70p' tests/test_storage.py
```

替换：

```python
from src.storage.models import AgentCycle  # was DecisionLog
log = AgentCycle(
    session_id=..., cycle_id="c1", triggered_by="scheduled",  # was trigger_type
    decision="open_long",  # 仍可用为 message text (但语义改了, 测试旧 enum 短串作 placeholder)
    reasoning="RSI oversold",
    model_id="claude-opus",  # was model_used
    tokens_consumed=1500,    # was tokens_used
)
assert log.tokens_consumed == 1500
```

- [ ] **Step 5.1.x: Add G1 EXPECTED_AGENT_CYCLE_FIELDS SoT test (Issue 7 / spec §14 G1)**

加到 `tests/test_storage.py` 末尾：

```python
def test_g1_agent_cycle_field_sot_drift_guard():
    """G1 (R2-7 spec §14): AgentCycle 字段集合 SoT vs 实际 dataclass 比对。

    加新字段忘改 SoT 集合 → CI fail。同款纪律见 Iter 4 ADJUST_ACTIONS drift guard.
    """
    from src.storage.models import AgentCycle
    from sqlalchemy import inspect

    EXPECTED_AGENT_CYCLE_FIELDS = {
        "id", "session_id", "cycle_id",
        "triggered_by", "trigger_context",
        "state_snapshot",
        "decision", "execution_status", "reasoning",
        "model_id", "tokens_consumed",
        "created_at",
    }

    actual = {col.name for col in inspect(AgentCycle).columns}
    drift = actual ^ EXPECTED_AGENT_CYCLE_FIELDS  # symmetric diff
    assert not drift, (
        f"AgentCycle 字段集漂移: actual={actual}, expected={EXPECTED_AGENT_CYCLE_FIELDS}, "
        f"diff={drift}. 加新字段需更新 EXPECTED_AGENT_CYCLE_FIELDS."
    )
```

- [ ] **Step 5.2: Update tests/test_usage_limits.py**

按 grep 结果改：
- `from src.storage.models import Session as SessionModel, DecisionLog` → `from src.storage.models import Session as SessionModel, AgentCycle`
- `select(DecisionLog).where(DecisionLog.status == ...)` → `select(AgentCycle).where(AgentCycle.execution_status == ...)`
- `select(DecisionLog).where(DecisionLog.session_id == "sess-t9")` → `select(AgentCycle).where(AgentCycle.session_id == "sess-t9")`
- 删除 `assert row.decision == "hold"` (line 110), `assert row.decision == "hold"` (line 216) — R2-7 后 decision 是 message free-form，旧 enum 断言无意义；改为 `assert row.decision is None` (forensic 路径) 或 `assert isinstance(row.decision, str)` (success 路径)
- `assert len(row.reasoning) == 4000` (line 218-219) → R2-7 后 reasoning 是 thinking content 不 cap，改为 `assert row.reasoning is None` (forensic 路径) 或 `assert row.reasoning is None or len(row.reasoning) > 0` (success path with thinking model)

**T10 整体删除（Issue 6 校准）**: `test_t10_forensic_path_derives_from_committed_trade_actions` (line 227-274 区域) 整个 test 验证派生函数从 trade_actions 反查 → forensic 路径写 'open_long'。**R2-7 派生函数已删 + forensic 路径 decision=NULL，T10 整个测试失去意义**。

修法：直接删除 T10 函数体（`def test_t10_...` 整个函数 + 相关 helper 调用）。重构成本高（如改为 "forensic decision=NULL but trade_actions 仍写入" 几乎是 T-WP-2 + 现有 trade_actions 写入测试组合），收益低。

```bash
# 在 tests/test_usage_limits.py 中删除 test_t10_* 函数及其 docstring/setup
grep -n "def test_t10\|def test_t10_forensic" tests/test_usage_limits.py
# 删除从 def test_t10_... 行到下一个 def test_ 行 (或文件末尾)
```

- [ ] **Step 5.3: Verify test_display_cycle.py unchanged**

D 决议：display.py param 名 (trigger_type / agent_output / tokens_used) R2-7 不改。检查 test_display_cycle.py 是否有 DB 字段名引用：

```bash
grep -n "DecisionLog\|decision_logs\|\.tokens_used\b\|\.trigger_type\b" tests/test_display_cycle.py
```

Expected: 0 hit on DB-side names. param 名引用 (trigger_type / tokens_used / agent_output) 是 `format_cycle_output(...)` 入参，不动。

- [ ] **Step 5.4: Update test_cycle_log.py**

```bash
grep -n "trigger_type\|DecisionLog" tests/test_cycle_log.py
```

`run_agent_cycle(agent=, deps=, trigger_type="scheduled", ...)` 入参 `trigger_type` **不改**（与 D 决议同源：cycle handler 入参是 string，不直接对应 DB 字段）。仅改 DecisionLog → AgentCycle import 如有。

- [ ] **Step 5.5: Run all migrated tests**

```bash
uv run pytest tests/test_storage.py tests/test_usage_limits.py tests/test_display_cycle.py tests/test_cycle_log.py -v
```

Expected: 全 PASS（包括之前 Task 3 留下的 broken 测试）。

- [ ] **Step 5.6: Run full test suite**

```bash
uv run pytest 2>&1 | tail -30
```

Expected: 970 baseline - 27 (deleted: 25 in test_derive_decision + 1 test_decision_log_e2e + 1 T10) - any t2/t9 enum 断言 cleanup 等 ≈ 943 baseline; +39 (new: T-ORD 6 + T-SS 10 + T-TC 5 + T-TH 4 + T-WP 3 + T-MIG 8 + G1 1 + G7 2) ≈ **982 passed + 3 skipped**。

**P9 校准**: 净 +12 测试（删 27 + 增 39）。具体落点应在 980-985 之间；如显著偏离需检查 grep / drift。

- [ ] **Step 5.7: Commit Task 5**

```bash
git add tests/test_storage.py tests/test_usage_limits.py tests/test_cycle_log.py
git commit -m "test(iter-w2r2-7): T5 migrate existing tests to AgentCycle schema

- tests/test_storage.py: DecisionLog → AgentCycle import + field rename
  (trigger_type→triggered_by, model_used→model_id, tokens_used→tokens_consumed)
- tests/test_usage_limits.py: same import + field rename, drop legacy enum
  decision assertions ('hold' / 'open_long') — replaced with semantic
  assertions for R2-7 message/thinking fields (None for forensic, valid
  string for success)
- tests/test_cycle_log.py: only DecisionLog import rename if any;
  run_agent_cycle() trigger_type param unchanged (D-decision: param name
  is rendering-layer label, not DB field)
- tests/test_display_cycle.py: unchanged (D-decision: display.py params
  trigger_type/agent_output/tokens_used preserved)

R2-7 §10.3 + AC8 + AC9 implementation."
```

---

## Task 6: 顺手清理 + 新 docs

**Files:**
- Modify: `tests/test_okx_websocket.py:208` (stale 注释更新)
- Modify: `src/storage/models.py:177` (stale 注释 — Issue 1+2)
- Modify: `src/storage/database.py:112` (stale 注释 — Issue 1+2)
- Modify: `src/integrations/exchange/okx.py:320,326` (stale 注释 — Issue 1+2)
- Create: `docs/metrics/agent-cycles-schema.md`

**Spec ref:** §5.1 stale 注释清理 + AC7 + Issue 1+2 联动

- [ ] **Step 6.1: Update test_okx_websocket.py stale comment**

`tests/test_okx_websocket.py:208`：

```bash
sed -n '206,212p' tests/test_okx_websocket.py
```

旧注释：`FillEvent.order_id 必须用 algoId, 否则与 decision_logs.order_id (= algoId from...`

改为：`FillEvent.order_id 必须用 algoId, 否则与 trade_actions.order_id (= algoId from agent create_order) 不一致 → get_trade_journal 关联失败`

(decision_logs 表本无 order_id 字段，正确指代是 trade_actions.order_id；R2-7 后 decision_logs 已 rename agent_cycles，注释更新为 trade_actions 修正本质 stale。)

- [ ] **Step 6.2: Update src/storage/models.py:177 stale comment (Issue 1+2)**

旧注释（在 ToolCall 类注释中，line 177 附近）：
```python
# cycle_id: 应用层软关联 DecisionLog.cycle_id（不声明 DB FK —— 时序不允许）
```

改为：
```python
# cycle_id: 应用层软关联 AgentCycle.cycle_id（不声明 DB FK —— 时序不允许）
```

- [ ] **Step 6.3: Update src/storage/database.py:112 stale comment (Issue 1+2)**

旧注释：
```
后续 _alembic_upgrade_head 会从 base 跑全部 migration，包括 batch_alter 重建 decision_logs。
```

改为：
```
后续 _alembic_upgrade_head 会从 base 跑全部 migration，包括 batch_alter 重建 decision_logs (Iter 3) → agent_cycles (R2-7 rename)。
```

(database.py:112 描述 Iter 3 migration 行为，仍指代 decision_logs 是事实准确 — 在 Iter 3 chain 视角下；R2-7 加注 rename 指针即可，不破坏历史描述准确性。)

- [ ] **Step 6.4: Update src/integrations/exchange/okx.py:320,326 stale comments (Issue 1+2)**

旧注释（在 `_parse_fill_event` 周边）：
```python
# manual construction) in decision_logs / TradeAction.order_id. OKX algo fill
# Under B: info.algoId non-empty → use algoId, matches decision_logs ✓
```

改为（统一指代 `trade_actions.order_id`，因为 decision_logs 本无 order_id 字段，原注释 stale 指错）：
```python
# manual construction) in trade_actions.order_id. OKX algo fill
# Under B: info.algoId non-empty → use algoId, matches trade_actions.order_id ✓
```

- [ ] **Step 6.5: Create agent-cycles-schema.md (Issue 12 编号校准)**

`docs/metrics/agent-cycles-schema.md`:

```markdown
# Agent Cycle Schema (R2-7 起)

本文档承载 `agent_cycles` 表 schema 演进 audit + 跨期 SQL 兼容性指南。
取代 `decision-enum-timeline.md`（已删除，R2-7 派生 enum 路线 deprecated）。

## 表结构（R2-7 起）

| 字段 | 类型 | 含义 |
|---|---|---|
| id | INTEGER PK | — |
| session_id | VARCHAR(36) FK | sessions.id |
| cycle_id | VARCHAR(50) | unique cycle id within session |
| triggered_by | VARCHAR(20) | scheduled / conditional / alert |
| trigger_context | TEXT NULL | JSON: trigger 瞬间客观快照 (FillEvent / PriceLevelAlertInfo / AlertInfo metadata) |
| state_snapshot | TEXT NULL | JSON: 决策时系统层面客观快照 (position / balance / market / pending_orders / active_alerts + _errors) |
| decision | TEXT NULL | agent 最终对外文本 (`result.output` message)；NULL for forensic path |
| execution_status | VARCHAR(30) DEFAULT 'ok' | ok / usage_limit_exceeded |
| reasoning | TEXT NULL | agent thinking content (LLM ThinkingPart 拼接)；NULL if non-thinking model 或 forensic |
| model_id | VARCHAR(100) NULL | LLM 模型 id |
| tokens_consumed | INTEGER DEFAULT 0 | LLM token 计数 (cycle 总, forensic 路径 = 0) |
| created_at | DATETIME | timestamp |

Index: `ix_agent_cycles_session_id_cycle_id` (session_id, cycle_id)

## trigger_context JSON schema

详见 spec `docs/superpowers/specs/2026-05-01-iter-w2r2-7-agent-cycle-schema-reframe-design.md` §4.3.

| trigger_type | content type tag | dataclass 来源 | 字段数 |
|---|---|---|---|
| scheduled | scheduled_tick | (无) | 1 |
| conditional | fill | FillEvent (base.py:269-281) | 12 (11 + 1 type) |
| alert | price_level_alert | PriceLevelAlertInfo (base.py:284-291) | 7 (6 + 1 type) |
| alert | percentage_alert | AlertInfo (price_alert.py:9-15) | 7 (6 + 1 type) |

## state_snapshot JSON schema

详见 spec §4.4。结构：
- `position`: Position dataclass 7 字段 + 衍生 pnl_pct (8 keys 或 null)
- `balance`: 3 字段 (total_usdt / free_usdt / used_usdt)
- `market`: ticker_last + ticker_timestamp (exchange ms epoch) + fetched_at (本机 ISO8601)
- `pending_orders`: list of Order dict (含 R2-7 §4.7 trigger_price 字段)
- `active_alerts`: list, single-symbol filter (cycle 单 symbol 上下文)
- `_errors`: list of "{type}_fetch_failed: {ExceptionType}" 字符串
- `_cycle_id`: 字段冗余便于 grep system.log

## 历史 enum 时点（W2 SQL 跨期分析参考）

| 时点 | decision 含义 | reasoning 含义 |
|---|---|---|
| 2026-04-08 ~ 2026-04-26 | 'completed' (硬编码) | message |
| 2026-04-26 (Iter 5) | 'usage_limit_exceeded' / 'completed' | message + str(e) |
| 2026-04-29 (Iter 4 PR #29) | enum 9 类（open_*/close/adjust/hold/derive_error/legacy）| message cap 4000 |
| 2026-04-30 (R2-4 PR #33) | enum 12 类（拆 4 子集 + 上述）| message cap 4000 |
| **2026-05-01 起 (R2-7)** | **message 自由文本** \| NULL | **thinking content** \| NULL |

## SQL 跨期分析建议

```sql
-- 旧数据 GROUP BY decision (W1 / sim #4 期)
SELECT decision, COUNT(*) FROM agent_cycles
WHERE created_at < '2026-05-01' GROUP BY decision;

-- 新数据 LIKE 检索 (W2 期)
SELECT cycle_id, decision FROM agent_cycles
WHERE created_at >= '2026-05-01' AND decision LIKE '%open%';
-- ⚠️ 自由文本，pivot 仅作粗筛，结果需人工 review
```

## R2-8 接口契约

R2-7 spec §8 已为 R2-8 (P1-7 展示 MVP + N10 reasoning 注入) 提供完整 display 设计契约：
- §8.1 cycle header (7-1 + 7-4)
- §8.2 cycle 末小结 + 累计统计 (7-3)
- §8.3 trigger_context 渲染 (7-5)
- §8.4 session 终结报告 (7-8)
- §8.5 字段消费契约 (display.py 接口签名)

R2-7 PR 内 display.py 不改 param 名（保留 trigger_type / agent_output / tokens_used 为渲染层标签）；
param rename 由 R2-8 PR 决定。
```

- [ ] **Step 6.6: Commit Task 6**

```bash
git add tests/test_okx_websocket.py src/storage/models.py src/storage/database.py src/integrations/exchange/okx.py docs/metrics/agent-cycles-schema.md
git commit -m "docs(iter-w2r2-7): T6 stale comment cleanup + new agent-cycles-schema.md

- 4 stale comments cleanup (Issue 1+2 review feedback):
  - tests/test_okx_websocket.py:208: 'decision_logs.order_id' →
    'trade_actions.order_id' (decision_logs has no order_id column;
    correct ref is trade_actions)
  - src/storage/models.py:177: ToolCall.cycle_id soft-link comment
    'DecisionLog.cycle_id' → 'AgentCycle.cycle_id'
  - src/storage/database.py:112: alembic upgrade comment add R2-7
    rename pointer 'decision_logs (Iter 3) → agent_cycles (R2-7)'
  - src/integrations/exchange/okx.py:320/326: _parse_fill_event
    comment 'decision_logs' → 'trade_actions.order_id' (本质 stale,
    decision_logs 无 order_id 列)

- docs/metrics/agent-cycles-schema.md: new schema documentation
  (replaces decision-enum-timeline.md):
  - 12-column AgentCycle table reference
  - trigger_context / state_snapshot JSON schemas
  - Historical enum timeline (decision/reasoning semantics across
    2026-04-08 → 2026-05-01)
  - SQL cross-period analysis guidance (W1/sim#4 vs W2 不可比)
  - R2-8 display interface contract pointer (spec §8)

R2-7 AC7 + Issue 1/2 review implementation."
```

---

## Task 7: G7 drift guard 测试

**Files:**
- Create: `tests/test_drift_no_legacy_decision_refs.py`

**Spec ref:** §14 G7 (M3 + K extension) + AC9

- [ ] **Step 7.1: Write drift guard test**

`tests/test_drift_no_legacy_decision_refs.py`:

```python
"""R2-7 §14 G7: drift guard — 派生路线 + DecisionLog 残留扫描."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent

DERIVE_ROUTE_SYMBOLS = [
    "_derive_decision_from_actions",
    "PROTECT_ACTIONS",
    "ENTRY_ORDER_ACTIONS",
    "LEVERAGE_ACTIONS",
    "ALERT_ACTIONS",
    "ADJUST_ACTIONS",
    "DERIVE_DECISION_VALUES",
]

LEGACY_NAMES = ["DecisionLog", "decision_logs"]


def _grep(pattern: str, paths: list[Path]) -> list[str]:
    """Grep pattern across paths, return list of "file:line: match"."""
    cmd = ["grep", "-rn", pattern] + [str(p) for p in paths]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.stdout.splitlines()


def test_g7_derive_route_symbols_no_residual():
    """G7: 派生路线 7 个符号在 src/ tests/ 内 0 hit (排除已删除文件)。"""
    paths = [REPO_ROOT / "src", REPO_ROOT / "tests"]
    pattern = "|".join(DERIVE_ROUTE_SYMBOLS)
    hits = _grep(f"({pattern})", paths)
    # 过滤本测试文件自身（含定义）
    hits = [h for h in hits if "test_drift_no_legacy_decision_refs" not in h]
    assert len(hits) == 0, \
        f"派生路线符号残留 (R2-7 §5 应全删):\n" + "\n".join(hits[:20])


def test_g7_legacy_names_no_residual():
    """G7 (K): DecisionLog / decision_logs 在 src/ tests/ 内 0 hit, 仅 alembic 历史 migration files
    + test_alembic_migration.py（历史 migration 行为测试）允许保留. Issue 1+2 校准."""
    paths = [REPO_ROOT / "src", REPO_ROOT / "tests"]
    pattern = "|".join(LEGACY_NAMES)
    hits = _grep(f"({pattern})", paths)

    # Whitelist (Issue 1 校准):
    # 1. 本 drift guard 测试本身（含 LEGACY_NAMES 定义）
    # 2. tests/test_alembic_migration.py — 历史 Iter 3 + R2-4 migration 行为测试（PRAGMA / INSERT
    #    decision_logs 是 by design — 验证旧 schema chain 状态，不应改名）
    # 3. R2-7 后期 src/storage/database.py:112 alembic 注释保留 "decision_logs (Iter 3) → agent_cycles (R2-7)"
    #    描述 chain 演进，本质不是 stale (T6.3 已处理)
    WHITELIST_FILES = {
        "test_drift_no_legacy_decision_refs",
        "tests/test_alembic_migration.py",
    }
    WHITELIST_PATTERNS = [
        # database.py:112 含 chain 演进描述, 保留 decision_logs (Iter 3) 字面是合理
        re.compile(r"src/storage/database\.py.*decision_logs \(Iter 3\)"),
    ]

    filtered = []
    for h in hits:
        if any(w in h for w in WHITELIST_FILES):
            continue
        if any(p.search(h) for p in WHITELIST_PATTERNS):
            continue
        filtered.append(h)

    assert len(filtered) == 0, \
        f"DecisionLog/decision_logs 残留 (R2-7 应已 rename agent_cycles/AgentCycle):\n" + "\n".join(filtered[:20])
```

- [ ] **Step 7.2: Run drift guard test**

```bash
uv run pytest tests/test_drift_no_legacy_decision_refs.py -v
```

Expected: 2 项全 PASS。

如果 FAIL：按 hits 输出回去修对应文件的残留引用，直到 PASS。

- [ ] **Step 7.3: Commit Task 7**

```bash
git add tests/test_drift_no_legacy_decision_refs.py
git commit -m "test(iter-w2r2-7): T7 G7 drift guard for derive route + DecisionLog residuals

- tests/test_drift_no_legacy_decision_refs.py: 2 grep-based drift tests
  - G7-1: 7 derive-route symbols (5 ACTIONS + DERIVE_DECISION_VALUES
    + _derive_decision_from_actions) zero in src/ tests/
  - G7-2 (M3+K): DecisionLog / decision_logs zero in src/ tests/
    (alembic/versions/ excluded — historical chain preserved)

Catches future regressions where derive route符号 or legacy table/class
names sneak back into source via merge / refactor / accidental copy.

R2-7 §14 G7 implementation."
```

---

## Task 8: Final smoke + AC self-check

**Files:** No file changes — 验证步骤。

**Spec ref:** §11 AC1-15 self-check

- [ ] **Step 8.1: Run full test suite**

```bash
uv run pytest 2>&1 | tail -20
```

Expected: 总测试数落在 970-980 区间，0 fail，3 skip 不变。

- [ ] **Step 8.2: AC self-check checklist**

逐项验证（grep / sql / 手测）：

```
AC1  agent_cycles 表 12 列                       → uv run alembic upgrade head + PRAGMA table_info(agent_cycles)
AC2  历史 decision_logs 行兼容保留               → T-MIG-6 通过即可
AC3  success 路径写入完整                        → T-WP-1 通过即可
AC4  forensic 路径 NULL + status                 → T-WP-2 通过即可
AC5  ThinkingPart 提取                           → T-TH-1~4 + Pre-smoke 1 已验证
AC6  R2-4 派生路线整套删除                       → T-G7-1 通过即可
AC7  agent-cycles-schema.md 已建                 → ls docs/metrics/
AC8  display.py param 名不改                     → grep "trigger_type / agent_output / tokens_used" 现状保留
AC9  测试数 970→978~980                          → 总测试 count
AC10 alembic upgrade + downgrade 通                → uv run alembic upgrade head && uv run alembic downgrade base
AC11 OKX state_snapshot 4 次逻辑 fetch           → simulated 模式 0 IO；实盘 smoke 由用户触发 (W2 启动后)
AC12 不引入 disable toggle / 连续失败检测        → grep "state_snapshot.enabled" = 0 hit
AC13 BaseExchange.Order +trigger_price           → T-ORD-1~6 通过即可
AC14 server_default 'ok' 保留                     → T-MIG-8 通过即可
AC15 Pre-impl smoke 验证完成                     → 已在 plan §pre-impl 段记录
```

逐项打勾。如有未通过，回到对应 task 修复。

- [ ] **Step 8.3: Spec drift sanity check**

```bash
# 确认 spec 引用的关键 ground truth 仍准确
grep -n "base.py:78-87\|base.py:48-52\|base.py:13-22\|base.py:35-45\|base.py:269-281\|base.py:284-291" docs/superpowers/specs/2026-05-01-iter-w2r2-7-agent-cycle-schema-reframe-design.md | head -10
```

如代码行号有偏移，记录在 PR description 或留观察期议题。

- [ ] **Step 8.4: Optional sim cycle smoke (recommended before merge)**

由用户跑（涉网络 + 计费）：

```
! uv run python main.py
```

跑一个 simulated cycle，确认：
- system.log 中 cycle handler 顺利完成
- DB 中 `SELECT * FROM agent_cycles ORDER BY id DESC LIMIT 1;` 显示新字段全填
- state_snapshot JSON 内含 5 类（position/balance/market/pending_orders/active_alerts）
- reasoning 字段非空（含 thinking content）
- decision 字段非空（含 message）
- execution_status = 'ok'

如有偏差，记录到 PR description。

- [ ] **Step 8.5: PR self-check grep (K)**

按 spec §14 G7 K 扩展：

```bash
grep -rn "DecisionLog\|decision_logs" src/ tests/ scripts/ 2>&1 | head -20
```

Expected: 0 hit（alembic/versions/ 不在 grep 范围 — 历史 migration 保留）。

输出贴 PR description 与 G7 自动测试互验。

- [ ] **Step 8.6: Final commit (if any sanity fixes)**

如 AC self-check 暴露遗漏，做最后 sanity commit；否则跳过。

```bash
# 仅在有遗漏修复时
git add ...
git commit -m "fix(iter-w2r2-7): T8 sanity check fixes

- ..."
```

---

## Post-merge 议题归档（手动，不在代码 PR 内）

按 spec §15 完成：

### Memory 更新

```bash
# 编辑下列 memory files (用户手动操作 / 在 chat 中 ask claude 帮)
~/.claude/projects/-Users-z-Z-TradeBot/memory/project_n9_derive_decision_limit_order_blindspot.md  # wontfix-by-design
~/.claude/projects/-Users-z-Z-TradeBot/memory/project_w2_prep_progress.md                          # R2-7 ✅ landed
~/.claude/projects/-Users-z-Z-TradeBot/memory/project_tradebot_status.md                           # +PR # 行
~/.claude/projects/-Users-z-Z-TradeBot/memory/project_iter4_sql_caveats.md                         # derive_error 失效, status 仍可统计
~/.claude/projects/-Users-z-Z-TradeBot/memory/project_observation_period_metrics_review_checklist.md  # C 档字段独立议题更新
```

### Inventory 更新

```bash
.working/sim4-issues-inventory.md     # §P0-4 ✅ wontfix-by-design (R2-7)
.working/all-pending-needs.md         # Tier 1 R2-7 升级为 schema reframe ✅; Tier 2 C 档字段更新
```

### 新 memory（R2-7 落地后）

新建 `project_agent_cycle_schema_reframe.md` 含：
- R2-7 schema reframe 决议历史（5 维度叙事 framing）
- 历史 enum timeline + W1/sim #4 与 W2 数据断层
- forensic 路径 NULL + status 决议
- C 档字段独立议题归属

---

## Self-Review

(本节由 plan 写完后自查)

### Spec 覆盖
- §1 议题背景 → Pre-impl 段 + Goal/Architecture
- §2 当前状态盘点 → 上下文，无 task
- §3 设计哲学 → Architecture
- §4 schema reframe → Task 1 (Order) + Task 3 (storage/models + alembic)
- §5 R2-4 deprecation → Task 4.A
- §6 写入路径 → Task 2 (helpers) + Task 4.B (cli/app)
- §7 migration → Task 3
- §8 P1-7 接口契约 → Out-of-scope, ref'd in Task 6 docs
- §9 容错 → Task 2 (per-field try) + Task 4 (forensic NULL)
- §10 测试 → Task 1-4 + Task 5 (migration) + Task 7 (drift)
- §11 AC1-15 → Task 8 self-check
- §12 out-of-scope → Plan 不涉及
- §13 改动量 → 跨 task 总和
- §14 drift guards → Task 7
- §15 议题归档 → Post-merge 段
- §16 self-review → 不入 plan

✅ 所有 spec 段对应 task.

### Placeholder scan
- ✅ 无 TBD / TODO / "fill in details"
- ✅ 每 step 含完整 code 或 exact bash 命令
- ✅ 每 commit 有完整 message draft

### Type consistency
- `_capture_state_snapshot(cycle_id: str, deps: TradingDeps) -> dict` — 在 Task 2 / Task 4 一致
- `_capture_trigger_context(cycle_id, trigger_type, context) -> dict | None` — 一致
- `_extract_thinking_text(messages) -> str | None` — Task 4 内一致
- `AgentCycle` (was `DecisionLog`) — Task 3 起统一改名
- `triggered_by / trigger_context / execution_status / model_id / tokens_consumed / state_snapshot` — Task 3 字段定义 / Task 4 写入 / Task 5 测试一致

✅ 类型 / 名字一致.

---

## Plan Self-Review v2 (2026-05-01, reviewer 第一轮 audit 后修订)

### v2 触发原因

v1 plan 第一轮审阅发现 10 项问题（2🔴 + 6🟡 + 2🟢）。

### v2 修复范围

| Issue | 严重度 | 修复内容 |
|---|---|---|
| **1** G7 drift guard `len(hits) == 0` 与代码现状冲突 | 🔴 | Task 7 G7-2 加 whitelist (test_drift 本身 + test_alembic_migration.py 整文件 + database.py:112 chain 描述 regex) |
| **2** stale 注释清理范围漏 3 处 | 🔴 | Task 6 加 3 处清理 step (6.2 models.py:177 / 6.3 database.py:112 / 6.4 okx.py:320,326)；commit message + Files 段同步更新 |
| **3** Task 2.A.1 fixture import `AlertInfo` from base.py 错误 | 🟡 | 删第一处 AlertInfo import + 第二个改回 `from src.services.price_alert import AlertInfo` (与 helper 一致) |
| **4** Task 1.10 Simulated Order.price vs trigger_price 字段映射策略未明 | 🟡 | 明牌方案 (a) 双填 with backward compat：保持 price=trigger_price 现状 + 新增 trigger_price 同值；附 future cleanup 候选 (b) 拆开方向 |
| **5** T-ORD-6 假设的 `_make_order_from_db` helper 不存在 | 🟡 | Task 1.9 改为端到端 setup engine + 插 SimOrder + `await fetch_open_orders` 路径，提供完整 fixture 模板 |
| **6** T10 整体处置未明牌 | 🟡 | Task 5.2 明牌"删除 T10 整个测试函数"（理由：派生函数已删，T10 失去意义；重构成本高收益低）|
| **7** Spec §14 G1 EXPECTED_AGENT_CYCLE_FIELDS SoT 测试未在 plan 实施 | 🟡 | Task 5 加 Step 5.1.x: G1 SoT 测试完整 code（用 `inspect(AgentCycle).columns` + symmetric diff）|
| **8** Retry-failure 路径写入策略未明牌 | 🟡 | Task 4.B.6 加第 4 段"Retry-failure 路径（明牌不动）"+ 3 条理由（spec §6 仅二分 / 不引入新 status / W2 数据驱动）|
| **9** Plan 部分 test 仅 skeleton | 🟢 | Reviewer 接受作为 reference-driven plan，不阻塞 |
| **10** File Structure "新建 4 个文件" 实际 5 | 🟢 | 改为 "新建（5 个文件，Issue 10 校准）" |

### v2 review 状态

✅ Issue 1 + 2 (🔴 联动) 修复 — Task 6 stale 注释清理 4 处全列；Task 7 G7-2 whitelist 完整覆盖
✅ Issue 3-8 (🟡) 全部修复 — fixture import / 字段映射决议 / 端到端测试 / T10 删除 / G1 SoT / retry-failure 明牌
✅ Issue 9-10 (🟢) — 9 reference-driven 接受 + 10 计数校准

---

## Plan Self-Review v3 (2026-05-01, reviewer 第二轮 audit 后修订)

### v3 触发原因

v2 plan 第二轮审阅发现 2 项遗漏：

| Issue | 严重度 | 内容 |
|---|---|---|
| **11** | 🟡 | Task 1.10 plain limit 例子用 `o.price` 字段，但 `SimOrder` (storage/models.py:148-163) **无 .price 字段** — 仅 trigger_price + filled_price。simulated.py:270 实证 limit 类用 `trigger_price=price` 存下单价 → 例子按字面 copy 会 `AttributeError: 'SimOrder' object has no attribute 'price'` |
| **12** | 🟢 | Task 6 Step 编号重复：6.1/6.2/6.3/6.4/**6.2**/6.5（schema md create 那个 step 误编 6.2，与 models.py:177 stale 清理 step 6.2 重复）|

### v3 修复范围

| 项 | 修复内容 |
|---|---|
| **11** | Task 1.10 例子改为单一 order_type 条件分支模板：`price=o.trigger_price` (所有 order_type 都从 trigger_price 读) + `trigger_price = o.trigger_price if o.order_type in ('stop_loss', 'take_profit') else None`。同时显式标注 SimOrder 字段事实校准（仅 trigger_price + filled_price，无 .price） |
| **12** | Task 6 step 重新编号: 6.1 (test_okx_websocket) / 6.2 (models.py:177) / 6.3 (database.py:112) / 6.4 (okx.py:320,326) / 6.5 (schema md create) / 6.6 (commit) — 严格递增 |

### v3 review 状态

✅ Issue 11 (🟡) 修复 — 字段名校准 + 简化 order_type 条件分支模板
✅ Issue 12 (🟢) 修复 — Step 编号递增 6.1→6.6

---

## Plan Self-Review v4 (2026-05-01, reviewer 第三轮 audit 后修订 + 触发 spec v8)

### v4 触发原因

plan v3 第三轮审阅发现 **代码层面错误 4 项 🔴 + 应修 4 项 🟡 + 信息项 4 项 🟢**。其中部分错误（P1）与 spec v7 同源（spec 也写 "stop_loss"），触发 spec v8 同步修订。

| Issue | 严重度 | 内容 |
|---|---|---|
| **P1** | 🔴 | order_type 命名错（spec + plan 用 "stop_loss"，但代码全用 "stop"，无 "stop_loss" 字面）|
| **P2** | 🔴 | OKX `_parse_order` 是 OKXExchange **实例方法** (def _parse_order(self, ...))，plan 测试当独立函数 import |
| **P3** | 🔴 | trigger_price 实施位置错 — `_extract_trigger_prices` (okx.py:493-506) 已抽离 sl_px/tp_px 传入 `_make_algo_order`/`_make_oco`；plan 早期版本在 `_parse_order` 内重复抠 info 字段是错的 |
| **P4** | 🔴 | alembic test fixture 用 `in_memory_engine_factory` / `upgrade_to_head` 不存在；现有是 `alembic_cfg_factory(db_path) -> Config` + `tmp_path` + `sqlite3.connect` PRAGMA 模式 |
| **P5** | 🟡 | spec §4.4 与 plan 字段命名同步（与 P1 联动 → spec v8）|
| **P6** | 🟡 | plan stale 注释清理超 spec 授权（spec §5.1 仅 1 处, plan 4 处）— 选 (a) spec §5.1 加 3 处对齐 |
| **P7** | 🟡 | plan Task 4.B.6 import 位置在函数体内（违反 PEP 8）|
| **P8** | 🟡 | retry loop 嵌套提醒不足，implementer 可能误把 capture 写到 for loop 内 |
| **P9** | 🟢 | 测试数实际净 +12（v3 估算 +5~+10 微对不齐），落点应是 982 不是 978 |
| **P10** | 🟢 | plan T10 删除决议 spec §10.3 没显式列 → spec v8 增补 |
| **P11** | 🟢 | retry-failure 路径 spec §6 二分未覆盖 → spec v8 §6.8 增补 |
| **P12** | 🟢 | plan Step 5.2 grep 列表是按 v2 抓的，impl 时建议再 grep |

#### v4 修复范围

| 项 | 修复内容 |
|---|---|
| **P1 + P5** (🔴) | spec v8 §4.4 JSON example "stop_loss" → "stop"；plan Task 1.6/1.9/1.10 全部 "stop_loss" → "stop" 字面对齐 simulated.py:51/196 + okx.py:482/484/545 |
| **P2** (🔴) | plan Task 1.6 测试改用 `okx_exchange_factory()` 实例化 + 调用 `okx._parse_order(data)` 实例方法路径，不再 `import _parse_order` |
| **P3** (🔴) | plan Task 1.8 实施代码完全重写：在 `_make_algo_order` (okx.py:521-532) / `_make_oco` (okx.py:534-548) 各 Order 构造**追加 `trigger_price=` 参数**（algo 类填同 price 值；OCO 各腿 sl_px/tp_px 各自）；`_parse_plain` 不需改（默认 None 兼容）。改动 ~3-5 行 |
| **P4** (🔴) | plan Task 3.6 测试模板完全重写：用 `tmp_path + alembic_cfg_factory(db_path) + _create_pre_alembic_schema(db_path) + command.upgrade(cfg, "head") + sqlite3.connect(db_path) + cur.execute("PRAGMA ...")` 模式，与 R2-4 现有测试段（line 295-373）完全一致 |
| **P6** (🟡) | spec v8 §5.1 stale 注释清理表加 3 处（models.py:177 / database.py:112 / okx.py:320,326），plan Task 6 范围与 spec 一致 |
| **P7** (🟡) | plan Task 4.B.6 import 移到 src/cli/app.py 文件顶部 imports 段（与 AgentCycle import 同处），避免 PEP 8 违规 |
| **P8** (🟡) | plan Task 4.B.6 capture 段加显式注释 "必须在 `for attempt in range(3):` retry loop **之前**, 不能在 loop 内"；spec v8 §6.7 写入顺序图标注 retry loop 嵌套层级 |
| **P9** (🟢) | plan Step 5.6 expected 改为 "≈ 982 passed + 3 skipped"，列具体加合（27 删 + 39 增 = 净 +12） |
| **P10** (🟢) | spec v8 §10.3 test_usage_limits.py 行加 "**删除 test_t10_*** 整个测试函数" 显式条目 + 删除理由 |
| **P11** (🟢) | spec v8 §6 加 §6.8 "Retry-failure 路径处理" 段，明牌不写 DB + 3 条理由 |
| **P12** (🟢) | (info-only) plan Step 5.2 已隐含 "按 grep 结果改"，impl 时实际跑一次 grep 是默认动作，不入 plan |

### v4 review 状态

✅ P1-P4 (🔴 必修) 全部修复 — order_type / 实例方法 / trigger_price 实施位置 / alembic fixture 全对齐代码现状
✅ P5/P6/P7/P8 (🟡 应修) 全部修复 — spec / plan 字段命名同步 / stale 注释 spec 授权 / import 位置 / retry loop 嵌套
✅ P9/P10/P11/P12 (🟢) 全部修复 — 测试数估算 / T10 删除 spec / retry-failure spec / grep 提醒
✅ Spec v8 同步增订（§4.4 P1 / §5.1 P6 / §6.7 P8 / §6.8 P11 / §10.3 P10）

plan v4 + spec v8 准备好让用户做最终 review。
