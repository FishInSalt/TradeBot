# Sim 微结构行情真实化（B 类）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `SimulatedExchange.fetch_order_book` / `fetch_trades` 从合成数据改为调真实 `_ccxt`（ccxtpro.okx），并验证换源后首次被激活的工具渲染路径（concentration / 非均衡 bid share / partial-coverage / 失败降级）。

**Architecture:** 单一 src 文件 `src/integrations/exchange/simulated.py` 两方法换源 + 死代码 `_prev_ticker` 移除。映射逻辑独立对照 CCXT 库契约（`ccxt 4.5.47`）推导，不照抄未执行的 `okx.py`，含两处稳健改进（盘口显式排序 / trades None-safe）。测试分两层：sim 层（mock `_ccxt`）+ 工具层（mock `deps.market_data`，验证 `tools_perception.py` 在真实形态上的渲染）。

**Tech Stack:** Python 3.13 / pytest / pytest-asyncio / unittest.mock（`MagicMock` + `AsyncMock`）/ ccxt.async_support。

**Spec:** `docs/superpowers/specs/2026-05-29-sim-microstructure-real-data-design.md`

---

## 文件结构

- **Modify** `src/integrations/exchange/simulated.py` — `fetch_order_book`（现 1182-1203）/ `fetch_trades`（现 1205-1239）换源 + `_prev_ticker`（82 / 631 / 1218-1219）移除。
- **Modify** `tests/test_exchange_order_book.py` — 重写 3 个合成结构测试为 mock `_ccxt`，删除 2 个 direction-bias 测试，新增映射/排序/None-safe/限频测试。
- **Create** `tests/test_sim_microstructure_real_data.py` — 工具层耦合验证（concentration / 非均衡 bid share / partial-coverage / 失败降级）。

CCXT 契约参考（已从库源码确认，写测试 mock 数据时遵循）：
- `_ccxt.fetch_order_book` 返回 `{"bids": [[price, amount, count?], ...], "asks": [...], "timestamp": int|None, ...}`（CCXT 已 parse，bids 价降序 / asks 价升序；OKX raw 4 元素 `["px","sz","0","1"]` parse 后为 3 元素）。
- `_ccxt.fetch_trades` 返回 unified trade dict 列表，每项 `{"timestamp": int|None, "side": str|None, "price": float|None, "amount": float|None, "id": str|None, ...}`（`safe_*` 缺字段返 None）。

---

## Task 1: sim `fetch_order_book` 换真实 `_ccxt`

**Files:**
- Modify: `src/integrations/exchange/simulated.py`（`fetch_order_book`，现 1182-1203）
- Test: `tests/test_exchange_order_book.py`

- [ ] **Step 1: 重写 + 新增 order_book 的 sim 层测试（先让其失败）**

替换 `tests/test_exchange_order_book.py` 中的 `test_sim_fetch_order_book_structure` 和 `test_sim_fetch_order_book_custom_depth` 两个函数为下列**七个**函数（删旧增新）。文件顶部已 `from unittest.mock import MagicMock`；补充 `AsyncMock`：把第 5 行改为 `from unittest.mock import AsyncMock, MagicMock`；并在 import 区加 `import ccxt.async_support as ccxt`。

```python
def _sim_with_ccxt(symbol: str = "BTC/USDT:USDT") -> SimulatedExchange:
    """_make_sim + 挂一个 MagicMock _ccxt（换源后两方法依赖 self._ccxt）。"""
    ex = _make_sim(symbol)
    ex._ccxt = MagicMock()
    return ex


@pytest.mark.asyncio
async def test_sim_fetch_order_book_maps_ccxt():
    """映射 CCXT-parsed bids/asks → OrderBookLevel，保留 symbol + timestamp。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[100.0, 1.5, 1], [99.0, 2.0, 1]],
        "asks": [[101.0, 1.0, 1], [102.0, 3.0, 1]],
        "timestamp": 1700000000000,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert isinstance(ob, OrderBook)
    assert ob.symbol == "BTC/USDT:USDT"
    assert ob.timestamp == 1700000000000
    assert [(l.price, l.amount) for l in ob.bids] == [(100.0, 1.5), (99.0, 2.0)]
    assert [(l.price, l.amount) for l in ob.asks] == [(101.0, 1.0), (102.0, 3.0)]


@pytest.mark.asyncio
async def test_sim_fetch_order_book_explicit_sort():
    """乱序输入 → 输出 bids 价降序 / asks 价升序（不依赖 CCXT 内部 sort）。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[98.0, 1.0], [100.0, 1.0], [99.0, 1.0]],
        "asks": [[103.0, 1.0], [101.0, 1.0], [102.0, 1.0]],
        "timestamp": 0,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert [l.price for l in ob.bids] == [100.0, 99.0, 98.0]
    assert [l.price for l in ob.asks] == [101.0, 102.0, 103.0]


@pytest.mark.asyncio
async def test_sim_fetch_order_book_skips_none_fields():
    """price/amount 为 None 的档位被跳过，不崩。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[100.0, 1.0], [None, 1.0], [99.0, None]],
        "asks": [[101.0, 1.0]],
        "timestamp": 0,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert [l.price for l in ob.bids] == [100.0]
    assert [l.price for l in ob.asks] == [101.0]


@pytest.mark.asyncio
async def test_sim_fetch_order_book_handles_2_and_3_element():
    """2 元素 [p,a] 与 3 元素 [p,a,count] 都能映射（*_ 解包）。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[100.0, 1.0], [99.0, 2.0, 5]],
        "asks": [[101.0, 1.0, 5], [102.0, 2.0]],
        "timestamp": 0,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert len(ob.bids) == 2 and len(ob.asks) == 2


@pytest.mark.asyncio
async def test_sim_fetch_order_book_depth_forwarded():
    """depth 透传给 _ccxt.fetch_order_book(limit=depth)。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(return_value={"bids": [[100.0, 1.0]], "asks": [[101.0, 1.0]], "timestamp": 0})
    await ex.fetch_order_book("BTC/USDT:USDT", depth=5)
    ex._ccxt.fetch_order_book.assert_awaited_once_with("BTC/USDT:USDT", limit=5)


@pytest.mark.asyncio
async def test_sim_fetch_order_book_not_started():
    """未 start（无 _ccxt）→ RuntimeError。"""
    ex = _make_sim()  # 不挂 _ccxt
    with pytest.raises(RuntimeError, match="not started"):
        await ex.fetch_order_book("BTC/USDT:USDT", depth=20)


@pytest.mark.asyncio
async def test_sim_fetch_order_book_ratelimit():
    """_ccxt 抛 RateLimitExceeded → 转 RateLimitHit。"""
    from src.utils.cache import RateLimitHit
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_order_book = AsyncMock(side_effect=ccxt.RateLimitExceeded("429"))
    with pytest.raises(RateLimitHit):
        await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_exchange_order_book.py -k "fetch_order_book" -v`
Expected: FAIL（旧合成实现仍读 ticker、不调 `_ccxt.fetch_order_book`；`test_sim_fetch_order_book_maps_ccxt` 等断言不符 / mock 未被调用）。

- [ ] **Step 3: 替换 `fetch_order_book` 实现**

把 `simulated.py` 的 `fetch_order_book`（现 1182-1203，含 docstring "Synthesize order book..."）整体替换为：

```python
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        """Fetch real order book via _ccxt (ccxtpro.okx public /market/books)."""
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            data = await self._ccxt.fetch_order_book(symbol, limit=depth)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim order book: {e}") from e
        import time
        # CCXT-parsed entries are [price, amount, count?]; *_ swallows count.
        # None-safe: skip malformed levels rather than crash on float(None).
        bids = [OrderBookLevel(price=float(p), amount=float(a))
                for p, a, *_ in data.get("bids", []) if p is not None and a is not None]
        asks = [OrderBookLevel(price=float(p), amount=float(a))
                for p, a, *_ in data.get("asks", []) if p is not None and a is not None]
        # Explicit sort — self-enforce best-first instead of depending on CCXT's
        # internal parse_order_book sort_by (untested-in-prod assumption otherwise).
        bids.sort(key=lambda l: l.price, reverse=True)
        asks.sort(key=lambda l: l.price)
        ts = data.get("timestamp") or int(time.time() * 1000)
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ts)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_exchange_order_book.py -k "fetch_order_book" -v`
Expected: PASS（7 个 fetch_order_book 测试全过）。

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_exchange_order_book.py
git commit -m "feat(sim): fetch_order_book reads real _ccxt instead of synthesizing"
```

---

## Task 2: sim `fetch_trades` 换真实 `_ccxt`

**Files:**
- Modify: `src/integrations/exchange/simulated.py`（`fetch_trades`，现 1205-1239）
- Test: `tests/test_exchange_order_book.py`

- [ ] **Step 1: 重写 + 新增 trades 的 sim 层测试（先让其失败）**

在 `tests/test_exchange_order_book.py` 中**删除** `test_sim_fetch_trades_structure`、`test_sim_fetch_trades_direction_bias_rising`、`test_sim_fetch_trades_direction_bias_falling` 三个函数（合成行为已不存在），替换为下列**六个**函数：

```python
@pytest.mark.asyncio
async def test_sim_fetch_trades_maps_ccxt():
    """映射 CCXT unified trade dict → Trade。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1700000000000, "side": "buy", "price": 70000.0, "amount": 0.01, "id": "t1"},
        {"timestamp": 1700000001000, "side": "sell", "price": 70010.0, "amount": 0.02, "id": "t2"},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert [(t.side, t.price, t.amount, t.trade_id) for t in trades] == [
        ("buy", 70000.0, 0.01, "t1"), ("sell", 70010.0, 0.02, "t2"),
    ]


@pytest.mark.asyncio
async def test_sim_fetch_trades_sorted_ascending():
    """乱序输入 → 按 timestamp 升序。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 3000, "side": "buy", "price": 1.0, "amount": 0.01, "id": "c"},
        {"timestamp": 1000, "side": "buy", "price": 1.0, "amount": 0.01, "id": "a"},
        {"timestamp": 2000, "side": "buy", "price": 1.0, "amount": 0.01, "id": "b"},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert [t.timestamp for t in trades] == [1000, 2000, 3000]


@pytest.mark.asyncio
async def test_sim_fetch_trades_skips_none_fields():
    """ts/side/price/amount 任一为 None 的成交被跳过。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1000, "side": "buy", "price": 1.0, "amount": 0.01, "id": "ok"},
        {"timestamp": None, "side": "buy", "price": 1.0, "amount": 0.01, "id": "bad_ts"},
        {"timestamp": 2000, "side": None, "price": 1.0, "amount": 0.01, "id": "bad_side"},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert [t.trade_id for t in trades] == ["ok"]


@pytest.mark.asyncio
async def test_sim_fetch_trades_none_id_ok():
    """id 为 None → trade_id=None，不跳过。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[
        {"timestamp": 1000, "side": "buy", "price": 1.0, "amount": 0.01, "id": None},
    ])
    trades = await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    assert len(trades) == 1 and trades[0].trade_id is None


@pytest.mark.asyncio
async def test_sim_fetch_trades_limit_forwarded_and_not_started():
    """limit 透传；无 _ccxt → RuntimeError。"""
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(return_value=[])
    await ex.fetch_trades("BTC/USDT:USDT", limit=500)
    ex._ccxt.fetch_trades.assert_awaited_once_with("BTC/USDT:USDT", limit=500)
    ex2 = _make_sim()  # 无 _ccxt
    with pytest.raises(RuntimeError, match="not started"):
        await ex2.fetch_trades("BTC/USDT:USDT", limit=500)


@pytest.mark.asyncio
async def test_sim_fetch_trades_ratelimit():
    """_ccxt 抛 RateLimitExceeded → 转 RateLimitHit。"""
    from src.utils.cache import RateLimitHit
    ex = _sim_with_ccxt()
    ex._ccxt.fetch_trades = AsyncMock(side_effect=ccxt.RateLimitExceeded("429"))
    with pytest.raises(RateLimitHit):
        await ex.fetch_trades("BTC/USDT:USDT", limit=500)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_exchange_order_book.py -k "fetch_trades" -v`
Expected: FAIL（旧合成实现不调 `_ccxt.fetch_trades`）。

- [ ] **Step 3: 替换 `fetch_trades` 实现**

把 `simulated.py` 的 `fetch_trades`（现 1205-1239，含 docstring "Synthesize ~20-50 trades..." 与全部 random 合成代码）整体替换为：

```python
    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
        """Fetch real recent trades via _ccxt (ccxtpro.okx public /market/trades)."""
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            data = await self._ccxt.fetch_trades(symbol, limit=limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim recent trades: {e}") from e
        trades: list[Trade] = []
        for r in data:
            ts, side, px, amt = r.get("timestamp"), r.get("side"), r.get("price"), r.get("amount")
            if ts is None or side is None or px is None or amt is None:
                continue  # None-safe: CCXT safe_* may return None on malformed rows
            tid = r.get("id")
            trades.append(Trade(timestamp=int(ts), side=str(side), price=float(px),
                                amount=float(amt), trade_id=str(tid) if tid is not None else None))
        trades.sort(key=lambda t: t.timestamp)
        return trades
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_exchange_order_book.py -k "fetch_trades" -v`
Expected: PASS（6 个 fetch_trades 测试全过）。

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_exchange_order_book.py
git commit -m "feat(sim): fetch_trades reads real _ccxt instead of synthesizing"
```

---

## Task 3: 移除 `_prev_ticker` 死代码

**Files:**
- Modify: `src/integrations/exchange/simulated.py`（`:82` 声明 / `:631` 维护；`:1218-1219` 已随 Task 2 删除）

- [ ] **Step 1: 确认无残留 consumer**

Run: `grep -n "_prev_ticker" src/integrations/exchange/simulated.py`
Expected: 只剩 2 处 —— 声明（`self._prev_ticker: Ticker | None = None`，约 :82）与维护（`self._prev_ticker = self._latest_ticker  # ... (for fetch_trades bias)`，约 :631）。Task 2 已移除 1218-1219 的使用。

- [ ] **Step 2: 删除两处**

删除声明行 `self._prev_ticker: Ticker | None = None`（约 :82）。
删除维护行 `self._prev_ticker = self._latest_ticker  # save previous before overwrite (for fetch_trades bias)`（约 :631）。

- [ ] **Step 3: 确认全代码库无引用**

Run: `grep -rn "_prev_ticker" src/ tests/`
Expected: 无输出（零引用）。

- [ ] **Step 4: 运行 exchange 全量测试确认未回归**

Run: `pytest tests/test_exchange_order_book.py tests/test_exchange.py -v`
Expected: PASS（无 `_prev_ticker` 相关失败；direction-bias 测试已在 Task 2 删除）。

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py
git commit -m "refactor(sim): remove _prev_ticker dead code (only served trades bias)"
```

---

## Task 4: 工具层耦合验证 — get_order_book（concentration / 非均衡 / 降级）

**Files:**
- Create: `tests/test_sim_microstructure_real_data.py`

这些测试 mock `deps.market_data.get_order_book` 喂真实形态 `OrderBook`，验证 `tools_perception.get_order_book` 渲染——覆盖 sim 合成期 0/98 从未跑过的路径。工具代码不改；测试应直接 PASS（若 FAIL 则暴露潜伏 bug）。

- [ ] **Step 1: 写工具层测试**

创建 `tests/test_sim_microstructure_real_data.py`：

```python
"""工具层耦合验证：换真实数据后 get_order_book / get_recent_trades 才会遇到的形态。"""
from __future__ import annotations
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.integrations.exchange.base import OrderBook, OrderBookLevel, Trade
from src.agent.tools_perception import get_order_book, get_recent_trades


def _deps_with_order_book(ob: OrderBook):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_order_book = AsyncMock(return_value=ob)
    return deps


def _deps_with_trades(trades):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_recent_trades = AsyncMock(return_value=trades)
    return deps


@pytest.mark.asyncio
async def test_get_order_book_renders_concentrated_levels():
    """某档 amount > 3× 同侧 median → 渲染 Concentrated Levels 段。"""
    bids = [OrderBookLevel(100.0 - i, 1.0) for i in range(15)]
    bids[5] = OrderBookLevel(95.0, 10.0)  # 10 > 3× median(1.0)=3.0 → wall
    asks = [OrderBookLevel(101.0 + i, 1.0) for i in range(15)]
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "Concentrated Levels" in out
    assert "95.00" in out  # 该 bid wall 的价格出现


@pytest.mark.asyncio
async def test_get_order_book_non_balanced_bid_share():
    """total_bid >> total_ask → 渲染 'bid : ask = N:1' 非均衡分支。"""
    bids = [OrderBookLevel(100.0 - i * 0.1, 10.0) for i in range(15)]  # total 150
    asks = [OrderBookLevel(101.0 + i * 0.1, 1.0) for i in range(15)]   # total 15
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "bid : ask =" in out
    assert "~50%" not in out  # 不是 balanced 分支


@pytest.mark.asyncio
async def test_get_order_book_degrades_on_failure():
    """market_data 抛异常 → 工具返 temporarily unavailable。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_order_book = AsyncMock(side_effect=Exception("boom"))
    out = await get_order_book(deps, depth=15)
    assert "Temporarily unavailable" in out
```

- [ ] **Step 2: 运行测试**

Run: `pytest tests/test_sim_microstructure_real_data.py -k "order_book" -v`
Expected: PASS（3 个）。若 concentration 测试 FAIL，停下来核对 `tools_perception.py:1773-1804` 的集中度计算（这正是 audit 议题 2 要验证的潜伏 bug）。

- [ ] **Step 3: Commit**

```bash
git add tests/test_sim_microstructure_real_data.py
git commit -m "test: verify get_order_book concentration / non-balanced / degradation paths"
```

---

## Task 5: 工具层耦合验证 — get_recent_trades（partial-coverage / 降级）

**Files:**
- Modify: `tests/test_sim_microstructure_real_data.py`

- [ ] **Step 1: 追加 get_recent_trades 测试**

在 `tests/test_sim_microstructure_real_data.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_get_recent_trades_partial_coverage():
    """500 笔成交全落窗口末 <120s（fetch_ratio=1.0 且 oldest_age_ratio<0.95）→ partial coverage 注记。"""
    now_ms = int(time.time() * 1000)
    trades = [Trade(timestamp=now_ms - (i % 120) * 1000,
                    side="buy" if i % 2 else "sell",
                    price=70000.0, amount=0.01, trade_id=str(i))
              for i in range(500)]  # RECENT_TRADES_MAX_FETCH = 500 → fetch_ratio=1.0
    deps = _deps_with_trades(trades)
    out = await get_recent_trades(deps, window_seconds=300)
    assert "partial coverage" in out


@pytest.mark.asyncio
async def test_get_recent_trades_degrades_on_failure():
    """market_data 抛异常 → 工具返 temporarily unavailable。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.market_data = MagicMock()
    deps.market_data.get_recent_trades = AsyncMock(side_effect=Exception("boom"))
    out = await get_recent_trades(deps, window_seconds=300)
    assert "Temporarily unavailable" in out
```

- [ ] **Step 2: 运行测试**

Run: `pytest tests/test_sim_microstructure_real_data.py -k "recent_trades" -v`
Expected: PASS（2 个）。

- [ ] **Step 3: 运行全量回归**

Run: `pytest -q`
Expected: PASS（无回归；删除的 5 个合成测试不再计入，新增 sim 层 13 + 工具层 5 测试通过）。

- [ ] **Step 4: Commit**

```bash
git add tests/test_sim_microstructure_real_data.py
git commit -m "test: verify get_recent_trades partial-coverage / degradation paths"
```

---

## 收尾说明（非任务）

- **延后项**（不在本计划）：`get_recent_trades` 低样本 caveat —— 待真实数据跑一轮后按低样本实际频率数据驱动决定（spec OUT）。
- **另立 mini-iter**：`get_recent_trades` docstring 双源 / `get_order_book` wrapper 降级消息格式 —— 纯 doc，独立 direct-merge。
- **另开会话**：`load_markets` 元数据层（contract_size / precision / min order size）。
- **可选硬化**：一次性 live-capture fixture（录真实 OKX book+trades 响应回放）—— 需联网，用户执行；本计划用 CCXT-parsed 形态手工构造已覆盖映射边界。
- **okx.py 同类隐患**（盘口未显式排序 / trades 裸取无 None-safe）—— 实盘路径 out-of-scope，留实盘准备期。
