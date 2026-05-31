# sim 执行保真 iter-1（contract_size + precision 真实化）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `SimulatedExchange` 的 `contract_size`（1.0→真实）与 `amount_to_precision`（config→ccxt 原生）真实化，使 sim 的 `amount`/`contracts` 语义从「base 数量」对齐真实 OKX 的「张数」，并同步所有从存储字段重算钱的下游消费者（两条下单路径 + 两套 FIFO + 状态快照）。

**Architecture:** cs 在 `start()` 一次性从 `_ccxt.market()` 缓存并持久化到 `sessions.contract_size` 新列。执行内核经 helper `_base_qty(amount)=amount×cs` 统一 ×cs（7 处）；钱的量纲在内核内 ×cs 抵消、数值不变，仅 `contracts` 存储尺度 base→张数。下游 runtime 消费者（cycle_capture）用 `deps.exchange.get_contract_size`，无 exchange 的消费者（metrics / 离线 _sim_metrics）从 `sessions.contract_size` 读（历史 NULL→1.0 fallback）。

**Tech Stack:** Python / pytest / SQLAlchemy 2.0 (async) / Alembic / ccxt 4.5.47 (ccxt.pro okx)。

**Spec:** `docs/superpowers/specs/2026-05-31-sim-exec-cs-precision-design.md`

**全局测试基线**：每个 commit 前跑 `uv run pytest -q` 应全绿（基线 ~2000 passed）。多数现有 sim 撮合测试用 `_make_exchange`（不 `start()`）→ `_contract_size` 取 `__init__` 默认 1.0 → 行为不变，不会被 Task 3 破坏。仅 cs≠1 新测试 + `amount_to_precision`（Task 5/6）需注入 mock `_ccxt`。

---

### Task 1: DB — `sessions.contract_size` nullable 列 + migration

**Files:**
- Modify: `src/storage/models.py:51`（`Session` 类，`fee_rate` 后）
- Create: `alembic/versions/<rev>_sim_exec_contract_size.py`
- Test: `tests/test_models_contract_size.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_models_contract_size.py
import pytest
from sqlalchemy import select
from src.storage.database import create_engine_and_init, get_session
from src.storage.models import Session as SessionModel


@pytest.mark.asyncio
async def test_session_contract_size_roundtrip(tmp_path):
    engine = await create_engine_and_init(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with get_session(engine) as s:
        s.add(SessionModel(id="sess-1", name="t", symbol="BTC/USDT:USDT",
                           initial_balance=100.0, contract_size=0.01))
        await s.commit()
    async with get_session(engine) as s:
        row = (await s.execute(
            select(SessionModel.contract_size).where(SessionModel.id == "sess-1")
        )).scalar_one()
    assert row == 0.01


@pytest.mark.asyncio
async def test_session_contract_size_defaults_null(tmp_path):
    engine = await create_engine_and_init(f"sqlite+aiosqlite:///{tmp_path}/t2.db")
    async with get_session(engine) as s:
        s.add(SessionModel(id="sess-2", name="t2", symbol="BTC/USDT:USDT", initial_balance=100.0))
        await s.commit()
    async with get_session(engine) as s:
        row = (await s.execute(
            select(SessionModel.contract_size).where(SessionModel.id == "sess-2")
        )).scalar_one()
    assert row is None   # 历史/未设置 → NULL（分析层 fallback 1.0）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_models_contract_size.py -v`
Expected: FAIL（`TypeError: 'contract_size' is an invalid keyword argument` 或 AttributeError）

- [ ] **Step 3: 加列（model）**

`src/storage/models.py`，在 `fee_rate`（:51）后插入：
```python
    contract_size: Mapped[float | None] = mapped_column(Float, nullable=True)  # per-session market contractSize; NULL=legacy (analysis fallback 1.0)
```

- [ ] **Step 4: 写 migration**

参照 `alembic/versions/af87432ee6dd_iter_net_pnl_metrics.py`，但 `sessions` 无 view 引用 → 无需 drop views。先取当前 head：`uv run alembic heads`（取 revision 填 `down_revision`）。

```python
# alembic/versions/<rev>_sim_exec_contract_size.py
"""iter-sim-exec-cs-precision: sessions.contract_size nullable column

Per-session market contractSize, cached at SimulatedExchange.start().
Legacy sessions keep NULL → analysis layer falls back to cs=1.0 (old base
semantics), new runs store real cs (contracts semantics). Mirrors the
existing nullable sessions.fee_rate column.
"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "<rev>"            # 由 `alembic revision` 生成或手填唯一 hash
down_revision: str | None = "af87432ee6dd"   # 用 `alembic heads` 实测值替换
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("contract_size", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_column("contract_size")
```

- [ ] **Step 5: 跑测试 + migration 验证**

Run: `uv run pytest tests/test_models_contract_size.py -v && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: PASS；alembic up/down/up 无错。

- [ ] **Step 6: Commit**

```bash
git add src/storage/models.py alembic/versions/ tests/test_models_contract_size.py
git commit -m "iter-sim-exec-cs-precision: add sessions.contract_size nullable column (D4)"
```

---

### Task 2: SimExchange — load_markets(retry) + cs 缓存 + 持久化 + get_contract_size 返真值

**Files:**
- Modify: `src/integrations/exchange/simulated.py`（`__init__:84` 后；新增 `_load_markets_with_retry` / `_init_contract_size` / `_persist_contract_size`；`start():1147` 区；`get_contract_size:1262`）
- Test: `tests/test_simulated_cs_cache.py`

- [ ] **Step 1: 写失败测试（含 start() 级 load_markets 重试 / fail-fast，spec §5）**

```python
# tests/test_simulated_cs_cache.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from src.integrations.exchange.simulated import SimulatedExchange


def _bare(symbol="BTC/USDT:USDT"):
    cfg = MagicMock(); cfg.fee_rate = 0.0005; cfg.precision = {}
    return SimulatedExchange(config=cfg, db_engine=None, session_id="s", symbol=symbol)


@pytest.mark.asyncio
async def test_get_contract_size_defaults_1_before_start():
    ex = _bare()
    assert await ex.get_contract_size("BTC/USDT:USDT") == 1.0   # __init__ 默认


@pytest.mark.asyncio
async def test_get_contract_size_returns_cached_real_cs():
    ex = _bare()
    ex._contract_size = 0.01           # start() 缓存后的状态
    assert await ex.get_contract_size("BTC/USDT:USDT") == 0.01


@pytest.mark.asyncio
async def test_get_contract_size_validates_symbol():
    ex = _bare()
    with pytest.raises(ValueError, match="Symbol mismatch"):
        await ex.get_contract_size("ETH/USDT:USDT")


@pytest.mark.asyncio
async def test_load_markets_failfast_after_3(monkeypatch):
    # spec §5: load 连续失败 → fail-fast RuntimeError（不静默回退 1.0）
    import src.integrations.exchange.simulated as simmod
    ex = _bare()
    ex._ccxt = MagicMock()
    ex._ccxt.load_markets = AsyncMock(side_effect=Exception("net down"))
    monkeypatch.setattr(simmod.asyncio, "sleep", AsyncMock())   # 跳过退避等待
    with pytest.raises(RuntimeError, match="Failed to load_markets after 3"):
        await ex._load_markets_with_retry()
    assert ex._ccxt.load_markets.await_count == 3


@pytest.mark.asyncio
async def test_init_contract_size_caches_real_cs():
    # spec §5: 成功 → 从 market() 缓存真实 cs（db_engine=None → 不 persist）
    ex = _bare()
    ex._ccxt = MagicMock()
    ex._ccxt.load_markets = AsyncMock()
    ex._ccxt.market = MagicMock(return_value={"contractSize": 0.01})
    await ex._init_contract_size()
    assert ex._contract_size == 0.01
    ex._ccxt.market.assert_called_once_with("BTC/USDT:USDT")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_simulated_cs_cache.py -v`
Expected: FAIL（`validates_symbol` FAIL — 无 _validate_symbol；`returns_cached` FAIL — 仍返 1.0；`_load_markets_with_retry` / `_init_contract_size` FAIL — AttributeError 方法不存在）

- [ ] **Step 3: 实现**

`__init__`（`:84` `self._latest_ticker = None` 附近）加：
```python
        self._contract_size: float = 1.0   # _init_contract_size() 覆写为真实 market contractSize
```

`get_contract_size`（`:1262`）改为：
```python
    async def get_contract_size(self, symbol: str) -> float:
        self._validate_symbol(symbol)
        return self._contract_size
```

新增三个 helper（spec §3.1：retry helper 与 cs 缓存分离，便于 fail-fast 单测）：
```python
    async def _load_markets_with_retry(self) -> None:
        """Spec §3.1: load_markets，指数退避重试 3 次，失败 fail-fast（不静默回退）。"""
        for attempt in range(3):
            try:
                await self._ccxt.load_markets()
                return
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise RuntimeError(f"Failed to load_markets after 3 attempts: {e}") from e

    async def _init_contract_size(self) -> None:
        """load_markets(retry) → 缓存真实 contractSize → 持久化（D4）。"""
        await self._load_markets_with_retry()
        self._contract_size = float(self._ccxt.market(self._symbol).get("contractSize") or 1.0)
        if self._db_engine:
            await self._persist_contract_size()

    async def _persist_contract_size(self) -> None:
        from sqlalchemy import update
        from src.storage.database import get_session
        from src.storage.models import Session as SessionModel
        async with get_session(self._db_engine) as session:
            await session.execute(
                update(SessionModel)
                .where(SessionModel.id == self._session_id)
                .values(contract_size=self._contract_size)
            )
            await session.commit()
```

`start()` 在 `self._ccxt = ccxtpro.okx()`（`:1147`）之后、seed `fetch_ticker` 循环之前插入一行：
```python
        self._ccxt = ccxtpro.okx()
        await self._init_contract_size()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_simulated_cs_cache.py -v`
Expected: PASS（5 个）

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_cs_cache.py
git commit -m "iter-sim-exec-cs-precision: cache+persist real contract_size at start() (retry/fail-fast), get_contract_size returns cached"
```

---

### Task 3: 执行内核 7 处 ×cs（helper `_base_qty`）

**Files:**
- Modify: `src/integrations/exchange/simulated.py`（新增 `_base_qty`；`:114/116`、`:234`、`:240/241`、`:279/280`、`:331/332`、`:424/425/428/430`、`:575/576`）
- Test: `tests/test_simulated_cs_kernel.py`

- [ ] **Step 1: 写失败测试（cs≠1 为主断言）**

```python
# tests/test_simulated_cs_kernel.py
import pytest
from src.integrations.exchange.base import Ticker
from tests.test_simulated_exchange import _make_exchange, _tick


def _ex_cs(cs):
    ex = _make_exchange(initial_balance=100000.0)
    ex._contract_size = cs
    return ex


@pytest.mark.asyncio
async def test_unrealized_pnl_scales_with_cs():
    # BTC cs=0.01: 10 张 = 0.1 base. entry 100000, bid 101000 → pnl = 1000 × 0.1 = 100
    ex = _ex_cs(0.01)
    await ex.create_order("BTC/USDT:USDT", "buy", "market", amount=10)
    await ex._process_tick(_tick(last=100000.0, bid=100000.0, ask=100000.0))  # fill open
    await ex._process_tick(_tick(last=101000.0, bid=101000.0, ask=101000.0))
    pos = (await ex.fetch_positions("BTC/USDT:USDT"))[0]
    assert pos.contracts == 10                      # 张数
    assert abs(pos.unrealized_pnl - 100.0) < 1e-6   # 1000 × (10×0.01)


@pytest.mark.asyncio
async def test_close_pnl_and_fee_scale_with_cs():
    # 直接构造持仓验 kernel（绕过 sizing/precision，per spec §5 A3）
    from src.integrations.exchange.simulated import _Position
    ex = _ex_cs(0.01)
    ex._positions["BTC/USDT:USDT"] = _Position(side="long", contracts=10, entry_price=100000.0, leverage=10)
    ex._used_usdt = 10000.0
    ex._latest_ticker = _tick(last=101000.0, bid=101000.0, ask=101000.0)
    pnl, fee, released = ex._close_position_core("BTC/USDT:USDT", "long", 10, 101000.0)
    assert abs(pnl - 100.0) < 1e-6                  # (101000-100000) × (10×0.01)
    assert abs(fee - 101000.0 * (10 * 0.01) * 0.0005) < 1e-6
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_simulated_cs_kernel.py -v`
Expected: FAIL（pnl/fee 仍按 cs=1 算，得 1000 而非 100）

- [ ] **Step 3: 实现 helper + 7 处替换**

新增（`_calc_unrealized_pnl` 附近，`:110` 前）：
```python
    def _base_qty(self, amount: float) -> float:
        """张数 → base 币当量（amount × contractSize）。计价统一入口。"""
        return amount * self._contract_size
```

7 处把裸 `amount`/`contracts`/`order.amount` 替换为 `self._base_qty(...)`：

| 位置 | 改前 → 改后 |
|---|---|
| `:114` | `(self._latest_ticker.bid - pos.entry_price) * pos.contracts` → `* self._base_qty(pos.contracts)` |
| `:116` | `(pos.entry_price - self._latest_ticker.ask) * pos.contracts` → `* self._base_qty(pos.contracts)` |
| `:234` | `estimated_fee = estimated_price * amount * self._fee_rate` → `* self._base_qty(amount) * self._fee_rate` |
| `:240` | `estimated_margin = (estimated_price * amount) / leverage` → `(estimated_price * self._base_qty(amount)) / leverage` |
| `:241` | `estimated_fee = estimated_price * amount * self._fee_rate` → `* self._base_qty(amount) *` |
| `:279` | `margin = (price * amount) / leverage` → `(price * self._base_qty(amount)) / leverage` |
| `:280` | `fee = price * amount * self._fee_rate` → `price * self._base_qty(amount) * self._fee_rate` |
| `:331` | `actual_margin = (fill_price * order.amount) / leverage` → `(fill_price * self._base_qty(order.amount)) / leverage` |
| `:332` | `actual_fee = fill_price * order.amount * self._fee_rate` → `fill_price * self._base_qty(order.amount) *` |
| `:424` | `released_margin = (pos.entry_price * amount) / pos.leverage` → `(pos.entry_price * self._base_qty(amount)) / pos.leverage` |
| `:425` | `fee = fill_price * amount * self._fee_rate` → `fill_price * self._base_qty(amount) *` |
| `:428` | `pnl = (fill_price - pos.entry_price) * amount` → `* self._base_qty(amount)` |
| `:430` | `pnl = (pos.entry_price - fill_price) * amount` → `* self._base_qty(amount)` |
| `:575` | `actual_margin = (fill_price * order.amount) / leverage` → `(fill_price * self._base_qty(order.amount)) / leverage` |
| `:576` | `actual_fee = fill_price * order.amount * self._fee_rate` → `fill_price * self._base_qty(order.amount) *` |

**不改（防机械误改）**：`_calc_liquidation_price:118`（纯价格比例）；加权均价 `:352`/`:589`（cs 分子分母约分）；`:351`/`:353`/`:588`/`:446`/`:449` 等纯张数加减。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `uv run pytest tests/test_simulated_cs_kernel.py tests/test_simulated_exchange.py -v`
Expected: 新测试 PASS；现有 `test_simulated_exchange`（`_make_exchange` cs=1.0 默认）全绿（×1.0 抵消）。

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_cs_kernel.py
git commit -m "iter-sim-exec-cs-precision: kernel 7 pricing points ×cs via _base_qty helper"
```

---

### Task 4: 两条下单路径张数化（open_position + place_limit_order）

**Files:**
- Modify: `src/agent/tools_execution.py`（`open_position:77/103`、`place_limit_order:623/654`）
- Test: `tests/test_order_sizing_cs.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_order_sizing_cs.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.agent.tools_execution import open_position, place_limit_order


def _deps(cs):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"; deps.fee_rate = 0.0005
    deps.approval_enabled = False; deps.approval_gate = None
    bal = MagicMock(); bal.free_usdt = 10000.0
    deps.exchange = MagicMock()
    deps.exchange.fetch_balance = AsyncMock(return_value=bal)
    deps.exchange.fetch_positions = AsyncMock(return_value=[])
    deps.exchange.set_leverage = AsyncMock()
    deps.exchange.has_pending_market_order = MagicMock(return_value=False)
    deps.exchange.get_contract_size = AsyncMock(return_value=cs)
    # amount_to_precision: identity (张数精度在 Task 5 测)
    deps.exchange.amount_to_precision = MagicMock(side_effect=lambda s, a: a)
    deps.exchange.create_order = AsyncMock(return_value=MagicMock(id="o1"))
    deps.market_data = MagicMock()
    tk = MagicMock(); tk.last = 100000.0
    deps.market_data.get_ticker = AsyncMock(return_value=tk)
    return deps


@pytest.mark.asyncio
async def test_open_position_quantity_is_contracts_not_base():
    # cs=0.01, 10% of 10000 × 10x = 10000 usdt notional. 张数 = 10000/(100000×0.01) = 10
    deps = _deps(0.01)
    await open_position(deps, "long", position_pct=10.0, leverage=10, reasoning="r")
    amount = deps.exchange.create_order.call_args.kwargs["amount"]
    assert abs(amount - 10.0) < 1e-9          # 张数（旧实现会得 0.1 base）


@pytest.mark.asyncio
async def test_place_limit_order_quantity_is_contracts():
    deps = _deps(0.01)
    await place_limit_order(deps, "long", price=100000.0, position_pct=10.0, leverage=10, reasoning="r")
    amount = deps.exchange.create_order.call_args.kwargs["amount"]
    assert abs(amount - 10.0) < 1e-9
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_order_sizing_cs.py -v`
Expected: FAIL（amount=0.1，base 数量）

- [ ] **Step 3: 实现**

`open_position`（`:76-78`）——把 cs 取数上移，张数化：
```python
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    contract_size = await deps.exchange.get_contract_size(deps.symbol)
    raw_quantity = (usdt_amount * leverage) / (ticker.last * contract_size)
    quantity = deps.exchange.amount_to_precision(deps.symbol, raw_quantity)
```
并删除末尾（`:103`）重复的 `contract_size = await deps.exchange.get_contract_size(...)`，复用上面的 `contract_size`（`:104` notional 表达式不变）。

`place_limit_order`（`:622-624`）同构：
```python
    usdt_amount = balance.free_usdt * (position_pct / 100.0)
    contract_size = await deps.exchange.get_contract_size(deps.symbol)
    raw_quantity = (usdt_amount * actual_leverage) / (price * contract_size)
    quantity = deps.exchange.amount_to_precision(deps.symbol, raw_quantity)
```
删除 `:654` 重复取数，复用（`:655` notional 不变）。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `uv run pytest tests/test_order_sizing_cs.py tests/test_tool_enhancement.py -v`
Expected: 新 PASS；现有 open/limit 工具测试（mock `get_contract_size`→1.0 或断 notional）核对，按需补 `get_contract_size` mock。

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_execution.py tests/test_order_sizing_cs.py
git commit -m "iter-sim-exec-cs-precision: open_position + place_limit_order size in contracts (raw_quantity ÷ cs)"
```

---

### Task 5: `amount_to_precision` 用 ccxt 原生 + InvalidOrder 守卫（含 `_make_exchange` _ccxt 注入 + 迁移旧测试）

**Files:**
- Modify: `src/integrations/exchange/simulated.py:198-201`（+ 顶部 `import ccxt`）
- Modify: `tests/test_simulated_exchange.py`（`_make_exchange:7-27` 注入 `_ccxt`；迁移 `test_amount_to_precision:217` + 删 `test_amount_to_precision_unknown_symbol:223`）
- Test: `tests/test_simulated_precision.py`

**为何把 fixture 注入并入本 task（I-1）**：`amount_to_precision` 改走 `self._ccxt` 后，`test_simulated_exchange.py:217/223` 现有两测试（直接在无 `_ccxt` 的 `_make_exchange()` 上调，断言旧 `math.floor==0.001` / `KeyError`）会在本 commit 必红。`_ccxt` 注入与这两测试的迁移必须与本 task 同 commit，否则 `uv run pytest -q` 不绿。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_simulated_precision.py
import pytest, ccxt
from unittest.mock import MagicMock
from tests.test_simulated_exchange import _make_exchange


def _ex_with_ccxt(precision_fn):
    ex = _make_exchange()
    ex._ccxt = MagicMock()
    ex._ccxt.amount_to_precision = MagicMock(side_effect=precision_fn)
    return ex


def test_amount_to_precision_delegates_to_ccxt():
    ex = _ex_with_ccxt(lambda s, a: "0.16")   # ccxt TRUNCATE 返字符串
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.1667) == 0.16


def test_amount_to_precision_sub_min_returns_zero():
    def _raise(s, a):
        raise ccxt.InvalidOrder("amount must be greater than minimum amount precision")
    ex = _ex_with_ccxt(_raise)
    assert ex.amount_to_precision("BTC/USDT:USDT", 1e-12) == 0.0   # 保 too-small 守卫
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_simulated_precision.py -v`
Expected: FAIL（旧实现用 `self._precision[symbol]` math.floor，且 sub-min 不返 0）

- [ ] **Step 3: 实现 amount_to_precision**

`simulated.py` 顶部确保 `import ccxt`（若仅 `import ccxt.pro`，补 `import ccxt`）。`amount_to_precision`（`:198`）改为：
```python
    def amount_to_precision(self, symbol: str, amount: float) -> float:
        try:
            return float(self._ccxt.amount_to_precision(symbol, amount))
        except ccxt.InvalidOrder:
            return 0.0   # sub-precision → 复原 open_position/place_limit_order 的 "Position too small" 守卫
```

- [ ] **Step 4: `_make_exchange` 注入 _ccxt（截断 mock）+ 迁移旧 precision 测试**

`tests/test_simulated_exchange.py` 的 `_make_exchange`（`:7-27`）末尾、`return exchange` 前加（mock 模拟 ccxt **TRUNCATE** 语义，per `project_iter2_mock_fidelity_lesson` + spec §3.4 base/exchange.py:6607，**不可用四舍五入**）：
```python
    import math
    def _trunc3(_symbol, amt):          # ccxt amount_to_precision 截断到 3 位（mock 保真）
        return f"{math.floor(float(amt) * 1000) / 1000:.3f}"
    exchange._ccxt = MagicMock()
    exchange._ccxt.amount_to_precision = MagicMock(side_effect=_trunc3)
    exchange._ccxt.market = MagicMock(return_value={"contractSize": 1.0})
    exchange._contract_size = 1.0
```
（`MagicMock` 已在该文件 `:2` 导入，无需补 import。）

迁移 `test_amount_to_precision`（`:217`）为 ccxt 截断语义断言：
```python
def test_amount_to_precision_truncates_via_ccxt():
    ex = _make_exchange()
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.001567) == 0.001
    assert ex.amount_to_precision("BTC/USDT:USDT", 0.0019999) == 0.001   # 截断非四舍五入
```
**删除** `test_amount_to_precision_unknown_symbol`（`:223`）：它断言旧 `self._precision[symbol]` 的 `KeyError`，该路径已不存在；unknown-symbol 现属 ccxt 域（真实 raise `ccxt.BadSymbol`，非 sim 行为），sim 侧唯一特有行为是 `InvalidOrder→0.0`，已由 Step 1 的 `test_amount_to_precision_sub_min_returns_zero` 覆盖。无 sim 特有语义可断 → YAGNI 删除。

- [ ] **Step 5: 跑测试确认通过 + 回归**

Run: `uv run pytest tests/test_simulated_precision.py tests/test_simulated_exchange.py -v`
Expected: PASS（precision 新 2 个 + 迁移后的 truncate 测试 + 现有 sim 撮合测试经截断 mock 全绿）

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_simulated_precision.py tests/test_simulated_exchange.py
git commit -m "iter-sim-exec-cs-precision: amount_to_precision via ccxt native + InvalidOrder→0.0 guard; inject truncating _ccxt into _make_exchange + migrate precision tests"
```

---

### Task 6: `config.precision` 退役 + 测试/fixture 迁移

**Files:**
- Modify: `src/config.py:18`（删 `precision` 字段）、`src/cli/app.py:777`（删 `_DEFAULT_PRECISION`）、`:844`（删 precision 填充）、`src/integrations/exchange/simulated.py:75`（删 `self._precision`）
- Modify (tests/fixtures): `tests/conftest.py`、`tests/_fixtures.py`、`tests/test_config.py:91/93/100`、`tests/test_exchange_order_book.py`、`tests/test_alert_age.py`、`tests/test_tool_enhancement.py`、`tests/test_derivatives_data.py`、`tests/test_iter_tool_opt_mark_vs_last.py`
- Modify (scripts): `scripts/smoke_sim_microstructure.py`、`scripts/verify_taker_flow_boundary.py`、`scripts/grounding_order_flow_tools.py`

**注**：`tests/test_simulated_exchange.py:13` 的 `_make_exchange` `_ccxt` 注入已在 Task 5 Step 4 完成（连带迁移 precision 测试），本 task 不再重复。

- [ ] **Step 1: 改 test_config 断言（先驱动删除）**

`tests/test_config.py:91-100` 删除 precision 相关断言，替换为「无 precision 字段」断言：
```python
def test_exchange_config_no_precision_field():
    cfg = ExchangeConfig(name="simulated", fee_rate=0.0005)
    assert not hasattr(cfg, "precision")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL（`precision` 字段仍存在 → `hasattr` True）

- [ ] **Step 3: 删字段 + 填充点 + sim 读取 + 清理残留 `precision=`**

- `src/config.py:18`：删 `precision: dict[str, int] | None = None`。
- `src/cli/app.py`：删 `_DEFAULT_PRECISION`（`:777`）整块 + `:844` 的 `precision = {...}` 行 + `ExchangeConfig(..., precision=precision)` 里的 `precision=` 实参。
- `src/integrations/exchange/simulated.py:75`：删 `self._precision = ...`。
- 全仓 grep `precision=` + `\.precision` 逐个清理残留实参 / 引用（`grep -rn "precision" src/ tests/ scripts/`）。
- 若 `tests/conftest.py` / `tests/_fixtures.py` 存在**会调用 `amount_to_precision` 的** sim 构造助手（非经 `_make_exchange`），对其按 Task 5 Step 4 的截断 `_ccxt` 注入同法补 mock；否则无需改动。

- [ ] **Step 4: 跑全量测试**

Run: `uv run pytest -q`
Expected: 全绿（修完所有 precision 引用点；若某测试断言旧 math.floor 精度行为，迁移为 ccxt 行为或删除）

- [ ] **Step 5: Commit**

```bash
git add src/config.py src/cli/app.py src/integrations/exchange/simulated.py tests/ scripts/
git commit -m "iter-sim-exec-cs-precision: retire config.precision (dead after ccxt) + fixture _ccxt injection"
```

---

### Task 7: B4 — `cycle_capture` notional ×cs（runtime get_contract_size）

**Files:**
- Modify: `src/services/cycle_capture.py:119-124`
- Modify: `tests/test_cycle_capture.py`（`deps_with_position` fixture `:24-56` 补 `get_contract_size→1.0` stub，保现有 `pnl_pct≈0.0618` 测试绿）
- Test: `tests/test_cycle_capture_cs.py`

**真实入口签名**（核实 `cycle_capture.py:84`）：`async def _capture_state_snapshot(cycle_id: str, deps: TradingDeps) -> dict`——函数名带下划线、`cycle_id` 在前 `deps` 在后。

**连带（I-2）**：现有 `test_state_snapshot_with_position` 断言 `pnl_pct_of_notional ≈ 0.0618 = 12.34/(75350×0.265)×100`（cs=1）。B4 改 notional ×cs 后，该测试要靠 `get_contract_size→1.0` 维持。Step 3 必须给 `deps_with_position` fixture 补 stub。

- [ ] **Step 1: 写失败测试（cs=0.01，自建 deps）**

```python
# tests/test_cycle_capture_cs.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.integrations.exchange.base import Position, Balance, Ticker
from src.services.cycle_capture import _capture_state_snapshot


@pytest.mark.asyncio
async def test_pnl_pct_uses_real_cs():
    deps = MagicMock(); deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=[Position(
        symbol="BTC/USDT:USDT", side="long", contracts=10, entry_price=100000.0,
        unrealized_pnl=100.0, leverage=10, liquidation_price=90000.0,
    )])
    deps.exchange.get_contract_size = AsyncMock(return_value=0.01)
    deps.exchange.fetch_balance = AsyncMock(return_value=Balance(
        total_usdt=10000.0, free_usdt=10000.0, used_usdt=0.0))
    deps.exchange.fetch_open_orders = AsyncMock(return_value=[])
    deps.exchange.get_price_level_alerts = MagicMock(return_value=[])
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=101000.0, bid=101000.0, ask=101000.0,
        high=102000.0, low=99000.0, base_volume=1000.0, timestamp=1746098096000))
    snap = await _capture_state_snapshot("c1", deps)
    # notional = 100000 × 10 × 0.01 = 10000; pnl_pct = 100/10000×100 = 1.0
    assert abs(snap["position"]["pnl_pct_of_notional"] - 1.0) < 1e-6
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_cycle_capture_cs.py -v`
Expected: FAIL（notional=100000×10=1e6 无 cs → pnl_pct=0.01，错 100×）

- [ ] **Step 3: 实现 + 补现有 fixture stub**

`cycle_capture.py:119-124`，取 cs 并乘入 notional：
```python
            cs = await deps.exchange.get_contract_size(deps.symbol)
            notional = (
                p.entry_price * p.contracts * cs
                if p.entry_price > 0 and p.contracts > 0
                else 0.0
            )
            pnl_pct_of_notional = (p.unrealized_pnl / notional * 100) if notional > 0 else None
```
`tests/test_cycle_capture.py` 的 `deps_with_position` fixture（`:30` `deps.exchange = MagicMock()` 后）加：
```python
    deps.exchange.get_contract_size = AsyncMock(return_value=1.0)
```
（确认该文件已 import `AsyncMock`，缺则补。`deps_flat` 无持仓不取 cs，无需改。）

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `uv run pytest tests/test_cycle_capture_cs.py tests/test_cycle_capture.py -v`
Expected: 新 PASS；现有 `test_state_snapshot_with_position`（0.0618）经 ×1.0 仍绿。

- [ ] **Step 5: Commit**

```bash
git add src/services/cycle_capture.py tests/test_cycle_capture_cs.py tests/test_cycle_capture.py
git commit -m "iter-sim-exec-cs-precision: cycle_capture notional ×cs (B4) via get_contract_size"
```

---

### Task 8: B2 — `metrics.py` FIFO `pnl_gross` ×cs（仅普通平仓项，从 session 读 cs）

**Files:**
- Modify: `src/services/metrics.py`（`compute:233` 加 select contract_size；`_collect_roundtrips_from_trade_actions` 签名加 cs 参数；`:195` pnl_gross ×cs）
- Test: `tests/test_metrics_cs.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_metrics_cs.py — 构造一组 open+close trade_actions(order_filled)，cs=0.01
# 断言 pnl_gross 按张数×cs；fee 摊分 + liquidation 路径不受 cs 重复乘影响。
# （沿用现有 metrics 测试的 fixture 构造方式；核心断言：）
@pytest.mark.asyncio
async def test_metrics_total_pnl_scales_with_cs(metrics_engine_cs_001):
    svc = MetricsService(engine=metrics_engine_cs_001, session_id="cs-001",
                         initial_balance=10000.0)
    metrics = await svc.compute()
    # 单 roundtrip：10 张 long @100000 → close @101000, cs=0.01
    # gross = (101000-100000) × (10×0.01) = 100 → total_pnl = sum(gross_pnls) = 100
    assert abs(metrics.total_pnl - 100.0) < 1e-6
```

(`metrics.py` 无 per-roundtrip 字段；`total_pnl`(:313)=`sum(gross_pnls)`，单笔即等于该笔 gross。fixture 需 seed `sessions.contract_size=0.01` + 对应 open/close `trade_actions`，amount 存张数 10。具体 fixture 仿照 `tests/test_metrics*.py` 既有构造 + `MetricsService` import。)

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_metrics_cs.py -v`
Expected: FAIL（gross=1000，未乘 cs）

- [ ] **Step 3: 实现**

`compute`（`:231-235`）把 cs 一并 select：
```python
            row = (await session.execute(
                select(SessionModel.fee_rate, SessionModel.contract_size)
                .where(SessionModel.id == self._session_id)
            )).first()
        fee_rate = row.fee_rate if row else None
        contract_size = (row.contract_size if row and row.contract_size is not None else 1.0)
```
把 `contract_size` 传入 `_collect_roundtrips_from_trade_actions(self._engine, self._session_id, contract_size)`（`:254`），在该函数签名加 `contract_size: float = 1.0`，**仅普通平仓** `pnl_gross`（`:195`）乘 cs：
```python
            else:
                pnl_gross = (fill.price - lot.entry_px) * consumed * sign * contract_size
```
**不改**：`:193` liquidation（`liq_pnl_per_unit` 已 money/张）；`:189-190` fee 摊分（无量纲比例）。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `uv run pytest tests/test_metrics_cs.py tests/test_metrics*.py -v`
Expected: 新 PASS；现有 metrics 测试（cs 缺省 1.0 fallback）全绿。

- [ ] **Step 5: Commit**

```bash
git add src/services/metrics.py tests/test_metrics_cs.py
git commit -m "iter-sim-exec-cs-precision: metrics FIFO pnl_gross ×cs (B2, normal-close only) from sessions.contract_size"
```

---

### Task 9: B3 + B3-bis — `_sim_metrics.py`（`_compute_pnl` ×cs + `_derive_close_amount` ÷cs）

**Files:**
- Modify: `scripts/_sim_metrics.py`（加 `_fetch_contract_size`；`collect_roundtrips:176` 读 cs；`_derive_close_amount:95` 签名加 cs，`:105` ÷cs；`_compute_pnl:88` 签名加 cs + 修 stale docstring，调用点 `:243` ×cs）
- Modify: `tests/test_metrics_src_scripts_parity.py`（`_setup_synthetic_sim_session:28` 加 `contract_size` 参数 + cs≠1 parity 用例，I-4）
- Test: `tests/test_sim_metrics_cs.py`

- [ ] **Step 1: 写失败测试（含 B3-bis 守卫恒放行专项）**

```python
# tests/test_sim_metrics_cs.py
from scripts._sim_metrics import _derive_close_amount, _compute_pnl


class _Fill:
    def __init__(self, fee, filled_price, amount):
        self.fee = fee; self.filled_price = filled_price; self.amount = amount


def test_derive_close_amount_divides_by_cs():
    # cs=0.01, 10 张 close @101000: 内核存 fee = 101000 × (10×0.01) × 0.0005 = 5.05
    fill = _Fill(fee=5.05, filled_price=101000.0, amount=10.0)
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005, contract_size=0.01)
    assert ok is True
    assert abs(derived - 10.0) < 1e-6        # 张数（旧实现得 0.1，cs<1 守卫恒放行静默）


def test_compute_pnl_scales_with_cs():
    # 10 张 long, cs=0.01: (101000-100000) × 10 × 0.01 = 100
    assert abs(_compute_pnl(100000.0, 101000.0, 10.0, "long", contract_size=0.01) - 100.0) < 1e-6


def test_derive_close_amount_cs1_fallback_unchanged():
    fill = _Fill(fee=5.05, filled_price=101000.0, amount=0.1)  # 旧 base 语义 run
    derived, ok = _derive_close_amount(fill, fee_rate=0.0005, contract_size=1.0)
    assert ok is True and abs(derived - 0.1) < 1e-6
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_sim_metrics_cs.py -v`
Expected: FAIL（函数签名无 `contract_size` 参数 → TypeError）

- [ ] **Step 3: 实现**

加 `_fetch_contract_size`（仿 `_fetch_fee_rate:158`）：
```python
async def _fetch_contract_size(engine, session_id: str) -> float:
    async with engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT contract_size FROM sessions WHERE id = :sid"),
            {"sid": session_id},
        )).first()
    return row.contract_size if row and row.contract_size is not None else 1.0   # 历史 NULL→1.0
```

`_derive_close_amount`（`:95`）签名加 `contract_size: float = 1.0`，`:105` ÷cs：
```python
def _derive_close_amount(fill, fee_rate, contract_size: float = 1.0):
    if fill.fee and fill.filled_price and fee_rate and fee_rate > 0:
        derived = fill.fee / (fill.filled_price * contract_size * fee_rate)   # ÷cs → 张数
        if derived <= fill.amount * 1.01:
            return derived, True
    return fill.amount, False
```

`_compute_pnl`（`:88`）签名加 `contract_size: float = 1.0`，乘入；顺手修 stale docstring（实际平仓 PnL 已移至 `simulated.py:427-430`，原写 `403-406`，I-8）：
```python
def _compute_pnl(entry_px, exit_px, amount, side, contract_size: float = 1.0):
    """Lot-level PnL (non-weighted). Mirrors simulated.py:427-430 (×contractSize)."""
    if side == "long":
        return (exit_px - entry_px) * amount * contract_size
    return (entry_px - exit_px) * amount * contract_size
```

`collect_roundtrips`（`:176`）读 cs 并传入两处调用点（`:205` `_derive_close_amount(fill, fee_rate, cs)`、`:243` `_compute_pnl(..., lot.side, cs)`；liquidation `:241` 不传/不乘）：
```python
    fee_rate = await _fetch_fee_rate(engine, session_id)
    contract_size = await _fetch_contract_size(engine, session_id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_sim_metrics_cs.py -v`
Expected: PASS（3 个，含 B3-bis 守卫专项 + NULL fallback）

- [ ] **Step 5: cs≠1 两套 FIFO parity 覆盖（I-4）**

现有 `test_metrics_src_scripts_parity.py` 仅在 cs=1.0 验证一致性（fixture 不 seed `contract_size` → NULL → 两侧都 fallback 1.0）。补 cs≠1 用例锁死 src(显式传 cs) ↔ script(读 DB cs) 一致。

先给 `_setup_synthetic_sim_session`（`:28`）加 `contract_size: float = 1.0` 参数，使存储侧与新内核（fee/pnl 含 cs）一致——否则 cs≠1 时 script 的 `_derive_close_amount` ÷cs 会令 `derived=amount/cs` 超 1.01 守卫、`stale_close_amount_count≠0`：
- 签名：`async def _setup_synthetic_sim_session(engine, sid, fee_rate, fills, contract_size: float = 1.0)`
- sessions INSERT 列清单加 `contract_size`、VALUES 加 `:cs`、参数 dict 加 `"cs": contract_size`
- `fee = price * amount * contract_size * fee_rate`（原 `:55`，无 cs）
- trade_actions pnl：`(price - entry_price) * amount * sign * contract_size`（原 `:81`，无 cs）

（默认 1.0 → 现有 parity 用例 fee/pnl ×1 不变、sessions 存 1.0，2-arg `_collect_roundtrips_from_trade_actions` 默认 cs=1.0、script 读 1.0，全部仍 byte-equal。）

新增用例：
```python
@pytest.mark.asyncio
async def test_src_scripts_fifo_parity_cs_nonunit(engine):
    """cs=0.01: src(显式传 cs) ↔ scripts(读 DB cs) 仍 byte-equal。"""
    from src.services.metrics import _collect_roundtrips_from_trade_actions
    from scripts._sim_metrics import collect_roundtrips
    sid = "parity-cs"
    await _setup_synthetic_sim_session(engine, sid, 0.0005, contract_size=0.01, fills=[
        ("open", "long", 100000.0, 10.0),
        ("close", "long", 101000.0, 10.0),
    ])
    src_rts, _ = await _collect_roundtrips_from_trade_actions(engine, sid, 0.01)
    script_rts, caveats = await collect_roundtrips(engine, sid)
    assert caveats["stale_close_amount_count"] == 0
    assert len(src_rts) == len(script_rts) == 1
    _assert_roundtrip_parity(src_rts[0], script_rts[0])
    # cs 真乘入：gross = (101000-100000) × 10 × 0.01 = 100
    assert abs(src_rts[0].pnl_gross - 100.0) < 1e-6
```

Run: `uv run pytest tests/test_metrics_src_scripts_parity.py -v`
Expected: PASS（现有 parity 用例 + 新 cs≠1 用例全绿）

- [ ] **Step 6: Commit**

```bash
git add scripts/_sim_metrics.py tests/test_sim_metrics_cs.py tests/test_metrics_src_scripts_parity.py
git commit -m "iter-sim-exec-cs-precision: _sim_metrics _compute_pnl ×cs + _derive_close_amount ÷cs (B3/B3-bis) from sessions.contract_size; cs≠1 src↔script FIFO parity"
```

---

### Task 10: 收尾 — 跨层 cs 同源断言 + base.py docstring + 全量回归

**Files:**
- Modify: `src/integrations/exchange/base.py:191`（docstring）
- Test: `tests/test_cs_cross_layer.py`

- [ ] **Step 1: 写跨层 cs 同源测试（A4）**

```python
# tests/test_cs_cross_layer.py
import pytest
from unittest.mock import MagicMock
from tests.test_simulated_exchange import _make_exchange


@pytest.mark.asyncio
async def test_exec_cs_matches_marketdata_cs():
    ex = _make_exchange()
    ex._ccxt = MagicMock()
    ex._ccxt.market = MagicMock(return_value={"contractSize": 0.01})
    ex._contract_size = float(ex._ccxt.market("BTC/USDT:USDT")["contractSize"])
    # 执行层缓存 cs 与 market-data 层 live 读取同源
    assert await ex.get_contract_size("BTC/USDT:USDT") == ex._ccxt.market("BTC/USDT:USDT")["contractSize"]
```

- [ ] **Step 2: 跑测试确认通过（应已满足）**

Run: `uv run pytest tests/test_cs_cross_layer.py -v`
Expected: PASS（锁死回归——防未来执行层缓存与 market-data live 读漂移）

- [ ] **Step 3: 更新 base.py docstring**

`base.py:191`：
```python
    async def get_contract_size(self, symbol: str) -> float:
        """Contract multiplier (base currency per contract). OKX BTC swap = 0.01;
        Sim caches the real market contractSize at start() (was hardcoded 1.0)."""
        ...
```

- [ ] **Step 4: 全量回归**

Run: `uv run pytest -q`
Expected: 全绿。逐一排查残留 `precision=` / cs=1 隐含断言。

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/base.py tests/test_cs_cross_layer.py
git commit -m "iter-sim-exec-cs-precision: cross-layer cs same-source assertion + base.py docstring (A4/§8)"
```

---

## 完成标准

- [ ] `sessions.contract_size` 列 + migration up/down 通过
- [ ] sim `start()` 缓存真实 cs + 持久化；`get_contract_size` 返缓存值 + `_validate_symbol`
- [ ] 内核 7 处 ×cs（helper `_base_qty`），液算/加权均价正确排除
- [ ] open_position + place_limit_order 张数化（raw_quantity ÷cs，cs 取数上移）
- [ ] `amount_to_precision` 走 ccxt + InvalidOrder→0.0 守卫
- [ ] `config.precision` 退役 + 9 测试文件 / 3 脚本迁移 + fixture 注入 `_ccxt`
- [ ] cycle_capture(B4) / metrics(B2) / _sim_metrics(B3+B3-bis) 全部从 cs 来源同步（cs≠1 主断言 + B3-bis 守卫专项 + NULL fallback）
- [ ] cs≠1 下 src↔script 两套 FIFO parity 用例（`test_metrics_src_scripts_parity.py`）
- [ ] 跨层 cs 同源断言 + base.py docstring
- [ ] `uv run pytest -q` 全绿
