# get_order_book 重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 B 类换源后 get_order_book 的单位错标（张数标成 base 币）与 % 字段塌陷，输出改 USD notional + price points/bp + 精简。

**Architecture:** 单位修复下沉到 `fetch_order_book` 内（用 `_ccxt.market()` 真 contractSize 做张→base 归一化，绕过执行层 `get_contract_size=1.0`，解耦）；渲染层因此只管展示——规模量 → USD notional，距离 % → pts(主)+bp(辅)，Concentrated 段去 best 重复/去距离列，bid share 三态 fact 化。只碰 `fetch_order_book` + `get_order_book`，不碰 `fetch_trades`（taker-flow iter）与执行层。

**Tech Stack:** Python 3.13 / pytest / ccxt(async okx) / dataclass OrderBook(Level)。设计 spec：`docs/superpowers/specs/2026-05-30-order-book-depth-redesign-design.md`。

---

## Task 1: sim `fetch_order_book` 张→base 归一化

**Files:**
- Modify: `src/integrations/exchange/simulated.py:1181-1204`
- Test: `tests/test_exchange_order_book.py`（更新 helper `_sim_with_ccxt` + 新增 1 测试）

- [ ] **Step 1: 更新测试 helper，让 MagicMock `_ccxt.market()` 返回带 contractSize 的 dict**

`tests/test_exchange_order_book.py` 的 `_sim_with_ccxt`（约 line 27-33）在 `ex._ccxt = MagicMock()` 之后加一行（默认 cs=1.0，保证现有 7 个测试 amount 不变）：

```python
def _sim_with_ccxt(symbol: str = "BTC/USDT:USDT") -> SimulatedExchange:
    """_make_sim + 挂一个 MagicMock _ccxt（换源后两方法依赖 self._ccxt）。"""
    ex = _make_sim(symbol)
    ex._ccxt = MagicMock()
    ex._ccxt.market = MagicMock(return_value={"contractSize": 1.0})  # 归一化默认无变化
    return ex
```

- [ ] **Step 2: 写失败测试（cs≠1 归一化）**

在 `tests/test_exchange_order_book.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_sim_fetch_order_book_normalizes_contracts_to_base():
    """amount(张数) × contractSize → base 币；用 _ccxt.market 真 cs（非执行层 get_contract_size）。"""
    ex = _sim_with_ccxt()
    ex._ccxt.market = MagicMock(return_value={"contractSize": 0.01})
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[73509.9, 2000.0]], "asks": [[73510.0, 300.0]], "timestamp": 0,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert ob.bids[0].amount == pytest.approx(20.0)   # 2000 张 × 0.01
    assert ob.asks[0].amount == pytest.approx(3.0)    # 300 张 × 0.01
    assert ob.bids[0].price == pytest.approx(73509.9)  # 价格不变


@pytest.mark.asyncio
async def test_sim_fetch_order_book_missing_contract_size_defaults_1():
    """market() 无 contractSize → 默认 1.0（不崩，amount 不变）。"""
    ex = _sim_with_ccxt()
    ex._ccxt.market = MagicMock(return_value={})
    ex._ccxt.fetch_order_book = AsyncMock(return_value={
        "bids": [[100.0, 5.0]], "asks": [[101.0, 5.0]], "timestamp": 0,
    })
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert ob.bids[0].amount == pytest.approx(5.0)
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_exchange_order_book.py::test_sim_fetch_order_book_normalizes_contracts_to_base -v`
Expected: FAIL（当前 amount=2000.0，断言 20.0 不符）

- [ ] **Step 4: 实现归一化**

`src/integrations/exchange/simulated.py` 的 `fetch_order_book`，将 `try:` 到 `return OrderBook(...)` 整段（约 line 1186-1204）替换为下块——**仅插入 `cs` + 两处列表推导改 `× cs`，sort / timestamp guard 原样保留**：

```python
        try:
            data = await self._ccxt.fetch_order_book(symbol, limit=depth)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim order book: {e}") from e
        # OKX swap size 是合约张数；× contractSize 归一化为 base 币（解耦执行层 get_contract_size）。
        # ccxt fetch_order_book 首行已 load_markets（okx.py），market() 必可用。
        cs = float(self._ccxt.market(symbol).get("contractSize") or 1.0)
        # CCXT-parsed entries are [price, amount, count?]; *_ swallows count.
        # None-safe: skip malformed levels rather than crash on float(None).
        bids = [OrderBookLevel(price=float(p), amount=float(a) * cs)
                for p, a, *_ in data.get("bids", []) if p is not None and a is not None]
        asks = [OrderBookLevel(price=float(p), amount=float(a) * cs)
                for p, a, *_ in data.get("asks", []) if p is not None and a is not None]
        # Explicit sort — self-enforce best-first instead of depending on CCXT's
        # internal parse_order_book sort_by.
        bids.sort(key=lambda l: l.price, reverse=True)
        asks.sort(key=lambda l: l.price)
        # is None (not falsy) — a legitimate timestamp of 0 must not fall to wall-clock.
        raw_ts = data.get("timestamp")
        ts = raw_ts if raw_ts is not None else int(time.time() * 1000)
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ts)
```

- [ ] **Step 5: 运行确认通过（含现有 7 测试不回归）**

Run: `python -m pytest tests/test_exchange_order_book.py tests/test_exchange.py tests/test_market_data.py -q`
Expected: PASS（现有测试 cs=1.0 amount 不变 + 2 个新测试通过）

- [ ] **Step 6: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_exchange_order_book.py
git commit -m "iter-order-book-depth-redesign: sim fetch_order_book 张→base 归一化

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: okx `fetch_order_book` 同款归一化（base 契约一致）

**Files:**
- Modify: `src/integrations/exchange/okx.py:879-891`
- Test: `tests/test_exchange_order_book.py`（补 3 个现有测试的 market mock + 新增 1 测试）

> **为何落 `test_exchange_order_book.py` 而非 `test_okx_exchange.py`**：okx 的 fetch_order_book 兄弟测试都在此文件；且实现加 `self._client.market()` 后，`test_okx_fetch_order_book_parses_ccxt_response`(:201) / `_timestamp_none_fallback`(:224) / `_handles_three_element_bidask_entries`(:321) 这 3 个（用真实 ccxt client、只 mock `fetch_order_book`、未 mock `market()`）会因 `ExchangeError: okx markets not loaded` 而 break。新测试与 mock 修复落同一文件，Step 4 校验才不 false-green。

- [ ] **Step 1a: 给 3 个现有 okx 测试补 `market()` mock（防归一化引入 break）**

`tests/test_exchange_order_book.py` 的 `test_okx_fetch_order_book_parses_ccxt_response`(:201) / `test_okx_fetch_order_book_timestamp_none_fallback`(:224) / `test_okx_fetch_order_book_handles_three_element_bidask_entries`(:321)，各在 `mocker.patch.object(ex._client, "fetch_order_book", ...)` 之后补一行（cs=1.0 → amount 不变 → 现有断言全部仍成立）：

```python
    mocker.patch.object(ex._client, "market", return_value={"contractSize": 1.0})
```

（`test_okx_fetch_order_book_retry_params`:239 先抛 `NetworkError`、不触达 `market()`，无需改。）

- [ ] **Step 1b: 写失败测试（落同一文件，沿用现有 mocker 风格）**

在 `tests/test_exchange_order_book.py` 末尾追加（顶部已 `import pytest`）：

```python
@pytest.mark.asyncio
async def test_okx_fetch_order_book_normalizes_contracts_to_base(mocker):
    """okx fetch_order_book 同款张→base 归一化（与 sim 对称，base 抽象契约一致）。"""
    from src.integrations.exchange.okx import OKXExchange
    ex = OKXExchange(api_key="k", secret="s", password="p", symbol="BTC/USDT:USDT")
    mocker.patch.object(ex._client, "fetch_order_book", return_value={
        "bids": [[73509.9, 2000.0]], "asks": [[73510.0, 300.0]], "timestamp": 0,
    })
    mocker.patch.object(ex._client, "market", return_value={"contractSize": 0.01})
    ob = await ex.fetch_order_book("BTC/USDT:USDT", depth=20)
    assert ob.bids[0].amount == pytest.approx(20.0)   # 2000 张 × 0.01
    assert ob.asks[0].amount == pytest.approx(3.0)    # 300 张 × 0.01
```

> `mocker.patch.object(..., "fetch_order_book", return_value=dict)`：pytest-mock 对 async 方法自动用 AsyncMock，await 返回该 dict（与现有 :201 测试同款）；`market` 是同步方法 → MagicMock 返回 dict。

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_exchange_order_book.py::test_okx_fetch_order_book_normalizes_contracts_to_base -v`
Expected: FAIL（当前 amount=2000.0，断言 20.0 不符）

- [ ] **Step 3: 实现归一化**

`src/integrations/exchange/okx.py` 的 `fetch_order_book`（line 879-891）改为：

```python
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        import time
        data = await self._client.fetch_order_book(symbol, limit=depth)
        # OKX swap size 是合约张数；× contractSize 归一化为 base 币（与 sim 对称）。
        cs = float(self._client.market(symbol).get("contractSize") or 1.0)
        # CCXT parse_bid_ask appends `countOrId`; `*_` swallows trailing fields.
        bids = [OrderBookLevel(price=float(p), amount=float(a) * cs) for p, a, *_ in data.get("bids", [])]
        asks = [OrderBookLevel(price=float(p), amount=float(a) * cs) for p, a, *_ in data.get("asks", [])]
        ts = data.get("timestamp")
        if ts is None:
            ts = int(time.time() * 1000)
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ts)
```

- [ ] **Step 4: 运行确认通过（含 3 个补 mock 的现有测试不回归）**

Run: `python -m pytest tests/test_exchange_order_book.py tests/test_okx_exchange.py -q`
Expected: PASS（3 个补 mock 的 okx 测试 + 新归一化测试 + 其余全过；两文件都跑以避免 false-green）

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/okx.py tests/test_exchange_order_book.py
git commit -m "iter-order-book-depth-redesign: okx fetch_order_book 同款张→base 归一化

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `get_order_book` 渲染重做（notional / pts·bp / 精简 / bid share fact 化）

**Files:**
- Modify: `src/agent/tools_perception.py:1690-1806`（加 module-level helper + 重写函数体）
- Test: `tests/test_sim_microstructure_real_data.py`（加强现有 1 测试 + 新增 4 测试）
- Test（卫生，Step 7）: `tests/test_display_cycle.py`（happy-path 快照 fixture 换新格式）+ `tests/test_toolkit_iter2.py`（typical 测试 docstring 去 stale）

- [ ] **Step 1: 加强现有测试 + 写新失败测试**

`tests/test_sim_microstructure_real_data.py`：在 `test_get_order_book_renders_concentrated_levels` 末尾加一行断言新格式无距离列：

```python
    assert "Concentrated Levels" in out
    assert "95.00" in out  # 该 bid wall 的价格出现
    assert "below mid" not in out  # 新格式去距离列
```

然后在文件末尾追加 4 个新测试：

```python
@pytest.mark.asyncio
async def test_get_order_book_renders_usd_notional():
    """规模量 = amount × price → USD notional（best 行不再标 base 币）。"""
    bids = [OrderBookLevel(100.0 - i * 0.1, 50.0) for i in range(15)]
    asks = [OrderBookLevel(101.0 + i * 0.1, 50.0) for i in range(15)]
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "$5.0K" in out          # best bid notional 50 × 100 = 5000
    assert "BTC  |" not in out     # 旧 "× N BTC  |  Best ask" 格式消失（notional 取代 base 币标签）


@pytest.mark.asyncio
async def test_get_order_book_distance_in_pts_and_bp():
    """spread / depth span 用 pts + bp，不用 %。"""
    bids = [OrderBookLevel(100.0 - i * 0.1, 10.0) for i in range(15)]
    asks = [OrderBookLevel(101.0 + i * 0.1, 10.0) for i in range(15)]
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "pts" in out
    assert "bp" in out
    assert "% deep" not in out
    assert "0.00%" not in out


@pytest.mark.asyncio
async def test_get_order_book_bid_share_factonly_near_50():
    """接近 50% 时显实际值 + 比值，无 'balanced' 评价词。"""
    bids = [OrderBookLevel(100.0 - i * 0.1, 10.0) for i in range(15)]   # total 150
    asks = [OrderBookLevel(101.0 + i * 0.1, 10.5) for i in range(15)]   # total 157.5
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    assert "balanced" not in out
    assert "~50%" not in out
    assert "bid : ask =" in out


@pytest.mark.asyncio
async def test_get_order_book_concentrated_excludes_best_no_distance():
    """Concentrated 段排除 best 档（已在 Best 行）+ 无距离列。"""
    bids = [OrderBookLevel(100.0 - i, 1.0) for i in range(15)]
    bids[0] = OrderBookLevel(100.0, 100.0)   # best 是最大单
    bids[5] = OrderBookLevel(95.0, 10.0)     # 纵深 wall（>3× median 1.0）
    asks = [OrderBookLevel(101.0 + i, 1.0) for i in range(15)]
    deps = _deps_with_order_book(OrderBook("BTC/USDT:USDT", bids, asks, 0))
    out = await get_order_book(deps, depth=15)
    conc = out.split("Concentrated Levels")[1]
    assert "95.00" in conc          # 纵深 wall 在
    assert "100.00" not in conc     # best 被排除（best 行另算）
    assert "below mid" not in conc  # 无距离列
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_sim_microstructure_real_data.py -v -k order_book`
Expected: FAIL（旧实现输出 base 币 / `%` / `below mid`，新断言 `$5.0K`/`pts`/排除 best 不符）

- [ ] **Step 3: 加 module-level helper**

`src/agent/tools_perception.py`，在 `async def get_order_book` 定义前加：

```python
def _fmt_ob_notional(usd: float) -> str:
    """Order book 规模量 USD notional 自适应 $K/$M（逐值）。"""
    if abs(usd) >= 1e6:
        return f"${usd/1e6:.2f}M"
    if abs(usd) >= 1e3:
        return f"${usd/1e3:.1f}K"
    return f"${usd:.0f}"
```

- [ ] **Step 4: 重写 get_order_book 函数体**

把 `src/agent/tools_perception.py:1690-1806` 整个函数替换为：

```python
async def get_order_book(deps: TradingDeps, depth: int = ORDER_BOOK_DEPTH_DEFAULT) -> str:
    """Order book snapshot: best bid/ask, depth, bid/ask share, concentrated levels.

    Args:
        depth: Levels per side to fetch. Default 15.

    Returns:
        str: Multi-line fact-only text. Sizes are USD notional; distances are
        price points + bp. See spec docs/superpowers/specs/2026-05-30-order-book-depth-redesign-design.md.
    """
    from datetime import datetime, timezone
    fetch_ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    symbol = deps.symbol
    try:
        ob = await deps.market_data.get_order_book(symbol, depth=depth)
    except Exception as e:
        logger.exception("get_order_book failed for %s", symbol)
        return (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Error: Temporarily unavailable ({e.__class__.__name__})."
        )

    actual = min(len(ob.bids), len(ob.asks))
    if not ob.bids or not ob.asks or actual < depth:
        return (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Error: Insufficient data (requested depth {depth}, got {actual})."
        )

    best_bid = ob.bids[0]
    best_ask = ob.asks[0]
    mid = (best_bid.price + best_ask.price) / 2
    spread = best_ask.price - best_bid.price
    spread_bp = spread / mid * 10000

    # notional = amount(base) × price; bid share uses base-amount ratio (unit-invariant)
    bid_notional = sum(l.amount * l.price for l in ob.bids[:depth])
    ask_notional = sum(l.amount * l.price for l in ob.asks[:depth])
    total_bid = sum(l.amount for l in ob.bids[:depth])
    total_ask = sum(l.amount for l in ob.asks[:depth])
    total_sum = total_bid + total_ask
    if total_sum == 0:
        return (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Error: Insufficient data (requested depth {depth}, got {actual})."
        )

    bid_lo = ob.bids[depth - 1].price
    ask_hi = ob.asks[depth - 1].price
    bid_span = best_bid.price - bid_lo
    ask_span = ask_hi - best_ask.price
    bid_span_bp = bid_span / best_bid.price * 10000
    ask_span_bp = ask_span / best_ask.price * 10000

    # Bid share three-state, fact-only (no "balanced" label)
    if total_bid == 0 and total_ask > 0:
        share_line = f"Bid share: 0% (asks only, no bids in top {depth})"
    elif total_ask == 0 and total_bid > 0:
        share_line = f"Bid share: 100% (bids only, no asks in top {depth})"
    else:
        bid_share = total_bid / total_sum * 100
        bid_ratio = total_bid / total_ask
        share_line = f"Bid share: {bid_share:.1f}% (bid : ask = {bid_ratio:.2f} : 1)"

    sections = [
        (
            f"=== Order Book ({symbol} @ {fetch_ts} UTC) ===\n"
            f"Best bid: {best_bid.price:.2f} × {_fmt_ob_notional(best_bid.amount * best_bid.price)}  |  "
            f"Best ask: {best_ask.price:.2f} × {_fmt_ob_notional(best_ask.amount * best_ask.price)}\n"
            f"Spread: {spread:.2f} pts ({spread_bp:.2f} bp)"
        ),
        (
            f"=== Depth (top {depth} each side) ===\n"
            f"  Bids: {_fmt_ob_notional(bid_notional)} over {best_bid.price:.2f} - {bid_lo:.2f}  "
            f"(span {bid_span:.2f} pts / {bid_span_bp:.1f} bp)\n"
            f"  Asks: {_fmt_ob_notional(ask_notional)} over {best_ask.price:.2f} - {ask_hi:.2f}  "
            f"(span {ask_span:.2f} pts / {ask_span_bp:.1f} bp)\n"
            f"  {share_line}"
        ),
    ]

    # Concentrated levels (per-side median on 张数维度), excluding best[0]
    import statistics
    bid_median = statistics.median([l.amount for l in ob.bids[:depth]])
    ask_median = statistics.median([l.amount for l in ob.asks[:depth]])
    threshold_bid = bid_median * ORDER_BOOK_CONCENTRATION_MULTIPLIER
    threshold_ask = ask_median * ORDER_BOOK_CONCENTRATION_MULTIPLIER

    concentrated = []
    for l in ob.bids[1:depth]:  # exclude best bid (already in Best line)
        if l.amount > threshold_bid:
            concentrated.append(("Bid", l.price, l.amount))
    for l in ob.asks[1:depth]:  # exclude best ask
        if l.amount > threshold_ask:
            concentrated.append(("Ask", l.price, l.amount))

    if concentrated:
        concentrated.sort(key=lambda c: c[2], reverse=True)
        concentrated = concentrated[:ORDER_BOOK_MAX_CONCENTRATED_LEVELS]
        bids_conc = sorted([c for c in concentrated if c[0] == "Bid"], key=lambda c: -c[1])
        asks_conc = sorted([c for c in concentrated if c[0] == "Ask"], key=lambda c: c[1])
        conc_header = (
            f"=== Concentrated Levels (beyond best bid/ask, "
            f"size > {ORDER_BOOK_CONCENTRATION_MULTIPLIER:.0f}× median of top {depth}) ==="
        )
        conc_rows = [
            f"  {side}  {price:.2f}  {_fmt_ob_notional(amount * price)}"
            for side, price, amount in bids_conc + asks_conc
        ]
        sections.append(conc_header + "\n" + "\n".join(conc_rows))

    return "\n\n".join(sections)
```

- [ ] **Step 5: 运行确认通过（含所有真调用 get_order_book 的文件）**

Run: `python -m pytest tests/test_sim_microstructure_real_data.py tests/test_toolkit_iter2.py tests/test_fact_only_wordlist.py tests/test_iter_tool_opt_error_metadata.py -q`
Expected: PASS（1 加强 + 4 新 + degrade；后 3 个文件也真调用 get_order_book，其断言为格式无关子串/结构——`Best bid:` / `Bid share:` / `Spread:` / `=== Depth` / `Error: Insufficient data` / `=== Concentrated Levels` 存在性 / 行数≤10 / 无禁词——渲染重写应全部存活；一并跑以闭掉 per-task false-green）

- [ ] **Step 6: 清理废弃常量**

`ORDER_BOOK_BALANCED_THRESHOLD_PCT`（`tools_perception.py:19`）现已无引用，删除该行。Run: `grep -rn "ORDER_BOOK_BALANCED_THRESHOLD_PCT" src/ tests/`，Expected: 无结果。

- [ ] **Step 7: 同步 stale fixture / docstring（卫生，非 break）**

(a) `tests/test_display_cycle.py` 的 `test_snapshot_get_order_book_happy_path`(:2268) —— 该快照喂硬编码旧格式 content 给 display 层（不调真实 get_order_book，机械通过但已失真）。把 `content` / `expected` 替换为新格式（display 缩进规则：每行 +4 空格前缀，空行保持空，content 内 2 空格行 → 6 空格）：

```python
    content = (
        "=== Order Book (BTC/USDT:USDT @ 05:28:25 UTC) ===\n"
        "Best bid: 75200.00 × $37.6K  |  Best ask: 75205.00 × $33.8K\n"
        "Spread: 5.00 pts (0.66 bp)\n"
        "\n"
        "=== Depth (top 15 each side) ===\n"
        "  Bids: $410.0K over 75200.00 - 75150.00  (span 50.00 pts / 6.6 bp)\n"
        "  Asks: $466.5K over 75205.00 - 75260.00  (span 55.00 pts / 7.3 bp)\n"
        "  Bid share: 46.8% (bid : ask = 0.88 : 1)"
    )
    expected = (
        "  ⚙ get_order_book()\n"
        "    === Order Book (BTC/USDT:USDT @ 05:28:25 UTC) ===\n"
        "    Best bid: 75200.00 × $37.6K  |  Best ask: 75205.00 × $33.8K\n"
        "    Spread: 5.00 pts (0.66 bp)\n"
        "\n"
        "    === Depth (top 15 each side) ===\n"
        "      Bids: $410.0K over 75200.00 - 75150.00  (span 50.00 pts / 6.6 bp)\n"
        "      Asks: $466.5K over 75205.00 - 75260.00  (span 55.00 pts / 7.3 bp)\n"
        "      Bid share: 46.8% (bid : ask = 0.88 : 1)"
    )
```

（同文件 `test_snapshot_get_order_book_l2_unavailable`:2294 走 error 路径、rewrite 逐字保留，无需改。）

(b) `tests/test_toolkit_iter2.py` 的 `test_order_book_typical_output_format`(:28) docstring 仍写"cumulative depth"（旧格式），改为反映新格式（断言体不变，仅 docstring）：

```python
    """Typical order book renders best bid/ask, USD-notional depth, bid share, concentrated levels."""
```

Run: `python -m pytest "tests/test_display_cycle.py::test_snapshot_get_order_book_happy_path" "tests/test_toolkit_iter2.py::test_order_book_typical_output_format" -q`
Expected: PASS。

- [ ] **Step 8: Commit**

```bash
git add src/agent/tools_perception.py tests/test_sim_microstructure_real_data.py \
        tests/test_display_cycle.py tests/test_toolkit_iter2.py
git commit -m "iter-order-book-depth-redesign: get_order_book 渲染重做(notional/pts·bp/精简)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `trader.py` wrapper docstring 更新 + 退役旧 drift-guard（LLM 通道）

**Files:**
- Modify: `src/agent/trader.py:377-388`
- Test: `tests/test_trader_agent.py`（更新现有 `:424` drift-guard 到新契约）

> **为何改 `:424` 而非新写源文本测试**：`test_get_order_book_description_carries_degradation`(:424) 断的就是 `tool.tool_def.description`（griffe 解析后、LLM 真见通道，per `project_tool_docstring_llm_channel`），与本 Task 改的同一对象；其 3 条旧断言（`"Reports best bid/ask"` / `"insufficient data"` / `"temporarily unavailable"`）在新 docstring 下全 break，必须随契约更新——非 mock 缺失。实测：当前 wrapper 旧文案 `Order book (symbol): temporarily unavailable` 从未与 impl 输出（`Error: Temporarily unavailable (Exception).`）匹配，本 Task 顺带退役这条既存 drift。断源文本会绕过 griffe、漏掉 block-strip 陷阱（per `project_griffe_example_section_stripped`），故不取。

- [ ] **Step 1: 更新 `:424` drift-guard 断言到新契约**

实测 `tool_def.description` 对 `Returns:` 块的处理：summary 包成 `<summary>…</summary>`、`Returns:` 块包成 `<returns><type>str</type><description>…块原文…</description></returns>` 一并进 desc（用 `get_performance` 实证）。故新格式样例放 `Returns:` 块 LLM 看得到，断言可覆盖 summary + Returns 两区。

把 `tests/test_trader_agent.py` 的 `test_get_order_book_description_carries_degradation`（约 :424）整个函数替换为：

```python
def test_get_order_book_description_carries_degradation():
    """get_order_book 新契约（USD notional / pts / 单行 Error 降级）经 tool_def.description
    （summary + Returns 块）到达 LLM。退役旧 'Reports…/temporarily unavailable' literal
    （该旧文案从未与 impl 输出匹配）。"""
    from src.agent.trader import create_trader_agent
    from src.config import PersonaConfig

    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    tool = agent._function_toolset.tools["get_order_book"]
    desc = tool.tool_def.description

    # 新格式事实内容到达 LLM（summary + Returns 块）
    assert "best bid/ask" in desc, f"summary fact content lost: {desc!r}"
    assert "USD notional" in desc, f"notional contract missing: {desc!r}"
    assert "pts" in desc, f"distance-unit (pts) contract missing: {desc!r}"
    # 降级契约收敛为单行 Error
    assert "Insufficient data" in desc, f"degradation contract missing: {desc!r}"
    assert "Error:" in desc, f"error-line contract missing: {desc!r}"
    # 旧 drift 契约已退役
    assert "temporarily unavailable" not in desc, f"retired old literal still present: {desc!r}"
    assert "Reports best bid/ask" not in desc, f"retired old summary still present: {desc!r}"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_trader_agent.py::test_get_order_book_description_carries_degradation -v`
Expected: FAIL（旧 docstring 无 "USD notional"/"pts"，且仍含 "temporarily unavailable" / "Reports best bid/ask"）

- [ ] **Step 3: 更新 wrapper docstring**

`src/agent/trader.py:377-388` 的 wrapper 改为（`Returns:` 块整段进 LLM，per project_griffe_example_section_stripped；summary 去掉已废弃的 "cumulative" 标签）：

```python
    @tool
    async def get_order_book(ctx: RunContext[TradingDeps], depth: int = 15) -> str:
        """Order book snapshot: best bid/ask, depth, bid/ask share, concentrated levels.

        Args:
            depth: levels per side (default 15).

        Returns:
            === Order Book (BTC/USDT:USDT @ 05:28:25 UTC) ===
            Best bid: 73509.90 × $1.49M  |  Best ask: 73510.00 × $241K
            Spread: 0.10 pts (0.01 bp)

            === Depth (top 15 each side) ===
              Bids: $1.54M over 73509.90 - 73506.70  (span 3.2 pts / 0.4 bp)
              Asks: $0.29M over 73510.00 - 73512.00  (span 2.0 pts / 0.3 bp)
              Bid share: 84.1% (bid : ask = 5.30 : 1)

            === Concentrated Levels (beyond best bid/ask, size > 3× median of top 15) ===
              Bid  73509.80  $20.8K
              Ask  73511.60  $36.7K

            Sizes are USD notional; distances are price points + bp. Insufficient
            data or service failure returns a single "Error: ..." line.
        """
        from src.agent.tools_perception import get_order_book as _impl

        return await _impl(ctx.deps, depth=depth)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_trader_agent.py::test_get_order_book_description_carries_degradation -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "iter-order-book-depth-redesign: wrapper docstring call→output 新格式 + 退役旧 drift-guard 契约

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: 全量回归 + 真实数据 smoke 验证

**Files:** 无（验证 only）

- [ ] **Step 1: 全量 pytest**

Run: `python -m pytest -q`
Expected: PASS（baseline 1957 collected @ `a2580e6` + 本 iter 净增测试；0 failures）。已知会动的测试已在前序 Task 显式处理，**勿用"补 mock"兜底误判**：① 交易所层（Task 1/2）真未 mock `market()` → 补 mock；② docstring drift-guard（`test_trader_agent.py::test_get_order_book_description_carries_degradation`，Task 4）→ 改测试反映新契约、非 mock 问题。

- [ ] **Step 2: 真实 smoke 确认新渲染**

Run: `PYTHONPATH=. python .working/tool-optimization/diag_order_book_real.py`（需联网 OKX 公开行情）
Expected: BTC/ETH/SOL **渲染段**（`---- get_order_book 当前渲染 ----`）显示 `$` notional + `pts`/`bp` + Concentrated 无 `below mid` + 无 `0.00%` 塌陷；best bid notional 量级合理（BTC ~$1M 级）。
> 注：脚本 `[symptom A/B]` 诊断行写于归一化前（假设 `amount` 是张数）；Task 1 后 `fetch_order_book` 已 ×cs，这些行会二次乘 cs / 失真——**只看渲染段，忽略 symptom 行**（或执行时删 diag 脚本 :59-66 诊断块）。

- [ ] **Step 3: 确认 git 历史**

Run: `git log --oneline main..HEAD`
Expected: spec commit + 4 个 impl commit（Task 1-4），顺序正确。

---

## 范围确认（不在本 plan）

- `fetch_trades` 单位 → taker-flow iter
- 执行层 `get_contract_size=1.0` 真值 → sim 执行保真 iter
- depth 默认值调整 / load_markets 完整元数据层 → 触发型候选

### ⚠ 跨层 notional 口径不一致（过渡期风险，需用户签字接受）

本 iter 后 `get_order_book` 用**真 cs** 显示 USD notional（BTC best bid ≈ $1.49M），而 `get_position` / 执行层 PnL/notional 仍用 **cs=1.0**（BTC 约 100× 偏小）。两者同一 cycle 都标 `$` → agent 跨工具对账可能错 100×（违反原则 7「同名字段不同语义」）。这是 spec §6 有意识的取舍（fact-only、不加 caveat、defer 到 sim 执行保真 iter），是本改动**最大行为风险**而非代码缺陷：

- **本 plan 不修**——修它属执行层保真 iter（牵动 PnL/撮合，per `feedback_sim_real_data_except_order_mgmt` F1）。
- **grounding 必查项**：下个 sim session 跑完后专门 grep narrative，看 agent 是否把 get_order_book 的 `$`（真 notional）与 get_position 的 `$`（cs=1.0 偏小）混用对账；若实证混用 → 提前启动执行层保真 iter。
