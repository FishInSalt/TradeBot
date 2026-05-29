# Sim 微结构行情真实化（B 类）设计

## 背景与目标

SimulatedExchange 已用真实 `_ccxt = ccxtpro.okx()` 取 OHLCV / funding / OI / L/S ratio / ticker（全真实，逐时变化），但 **`fetch_order_book` 与 `fetch_trades` 仍是合成 / 硬编码**，是 sim 里唯二喂给 agent 感知层的"合成微结构"离群者（per memory `project_sim_market_data_fidelity`）。

两次 tool-audit（`.working/tool-audits/2026-05-29-get_recent_trades.md` / `2026-05-29-get_order_book.md`）实证：

- `fetch_trades`：`random.randint(20,50)` 笔随机方向/大小/时间 → taker buy% 中位恰 50.0、41% 落 [45,55] 抛硬币带；agent 却 56% 带 conviction、48% 构建 absorption/divergence 论点（cycle d6e1 决策级误读 9% 假信号）。
- `fetch_order_book`：从 ticker ±0.01% 确定性对称合成 → bid share 98/98 = 100% "~50% (balanced)"、Concentrated Levels 0/98 触发、累计深度仅 2 个值。

两个工具的感知层（`tools_perception.py`）本身计算正确、渲染清晰、adoption 高且 0 fabrication；问题纯在上游合成数据。

**目标**：把 `simulated.py` 的 `fetch_order_book` / `fetch_trades` 从合成改为调真实 `_ccxt`，使 `get_order_book` / `get_recent_trades` 在 sim 中喂真实订单流，并验证换源后首次被激活的代码路径。

## 关键约束

- **不以 `okx.py` 为参考实现**：okx.py 的实盘路径从未真实执行过（项目 sim-only，实盘"暂不接入"，测试仅 mock），其映射逻辑可能藏未触发 bug。本设计独立对照 **CCXT 库源码契约**（`ccxt 4.5.47` 的 `base/exchange.py::parse_order_book` / `okx.py::parse_trade`）推导。
- 沿用 sim 既有真实方法的 pattern（`RateLimitExceeded → RateLimitHit` 包装、`_validate_symbol`、`hasattr(self, "_ccxt")` guard），与 `fetch_ohlcv` / `fetch_funding_rate` 等同构。

### 独立确立的 CCXT 契约（不依赖 okx.py）

- **`parse_order_book` 保证排序**：`'bids': sort_by(bids, 0, True)`（价降序）、`'asks': sort_by(asks, 0)`（价升序）。`data["bids"][0]` 为最优买价、`[-1]` 最深 —— `get_order_book` 工具的 `bids[0]=best / bids[depth-1]=deepest` 假设安全。
- **OKX 盘口 raw 为 `["px","sz","0","1"]`（4 元素）**，parse 后为 `[price, amount, count]`（3 元素）→ 映射需 `[p, a, *_]` 解包。
- **`parse_trade` 用 `safe_integer` / `safe_string`**，缺字段返回 `None` → 映射需 None-safe，不能裸取（okx.py 的 `int(raw["timestamp"])` / `float(raw["price"])` 在 None 时会崩）。OKX 公开成交 raw 为 `{"side","sz","px","tradeId","ts"}`，文档上恒填，但稳健起见仍跳过畸形行。

## Scope

### IN
1. `simulated.py::fetch_order_book` / `fetch_trades` 合成体替换为真实 `_ccxt` 调用。
2. `_prev_ticker` 死代码移除（`:82` 声明 / `:631` 维护 / `:1218-1219` 使用 —— 已核实仅服务 trades bias）。
3. Sim 层测试改造（5 个合成测试）。
4. 工具层耦合验证测试：concentration 路径、非均衡 bid share、partial-coverage 星号、失败降级。
5. Mock 保真 fixture（按 OKX 文档响应形态）。

### OUT（延后 / 另立）
- `get_recent_trades` 低样本 caveat（audit 议题 2）—— 延后，真实数据跑一轮后按低样本实际频率数据驱动决定。
- 纯 docstring mini-iter：`get_recent_trades` docstring 双源（impl `Degradation:` 对 LLM 是 dead doc）/ `get_order_book` wrapper docstring 降级消息格式与实际输出不符 —— 独立 direct-merge。
- `load_markets` 元数据层（contract_size / precision / min order size）—— 另开会话深入（per memory `project_sim_market_data_fidelity`）。
- `okx.py` 同类稳健隐患（盘口未显式排序 / trades 裸取无 None-safe）—— 实盘路径 out-of-scope；本 spec 记录，留实盘准备期处理。
- `get_mark_price = last` / `_fee_rate` config —— 不动（判定见 `project_sim_market_data_fidelity`）。

## 详细设计

### 改动面
单一 src 文件 `src/integrations/exchange/simulated.py`。

### `fetch_order_book`

```python
async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
    self._validate_symbol(symbol)
    if not hasattr(self, "_ccxt"):
        raise RuntimeError("Exchange not started — call start() first")
    try:
        data = await self._ccxt.fetch_order_book(symbol, limit=depth)
    except ccxt.RateLimitExceeded as e:
        raise RateLimitHit(f"Sim order book: {e}") from e
    import time
    # CCXT 已 parse 为 [price, amount, count?]；count 用 *_ 吞掉；None 字段跳过
    bids = [OrderBookLevel(price=float(p), amount=float(a))
            for p, a, *_ in data.get("bids", []) if p is not None and a is not None]
    asks = [OrderBookLevel(price=float(p), amount=float(a))
            for p, a, *_ in data.get("asks", []) if p is not None and a is not None]
    # 显式排序：自保证 bids[0]=best(价高) / asks[0]=best(价低)，不依赖 CCXT 内部 sort
    bids.sort(key=lambda l: l.price, reverse=True)
    asks.sort(key=lambda l: l.price)
    ts = data.get("timestamp") or int(time.time() * 1000)
    return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ts)
```

### `fetch_trades`

```python
async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
    self._validate_symbol(symbol)
    if not hasattr(self, "_ccxt"):
        raise RuntimeError("Exchange not started — call start() first")
    try:
        data = await self._ccxt.fetch_trades(symbol, limit=limit)
    except ccxt.RateLimitExceeded as e:
        raise RateLimitHit(f"Sim recent trades: {e}") from e
    trades = []
    for r in data:
        ts, side, px, amt = r.get("timestamp"), r.get("side"), r.get("price"), r.get("amount")
        if ts is None or side is None or px is None or amt is None:
            continue  # None-safe 跳过畸形行（CCXT safe_* 可能返 None）
        tid = r.get("id")
        trades.append(Trade(timestamp=int(ts), side=str(side), price=float(px),
                            amount=float(amt), trade_id=str(tid) if tid is not None else None))
    trades.sort(key=lambda t: t.timestamp)
    return trades
```

### vs okx.py 的两处独立改进
1. **order book 显式排序**：okx.py 不排序，靠 CCXT `parse_order_book` 内部 sort 的隐式依赖。本实现显式排序，让工具假设自我保证。
2. **trades None-safe 跳过**：okx.py 裸取 `int(raw["timestamp"])` 在 None 时崩；本实现跳过畸形行。

## 错误与降级语义

- Sim 层：`RateLimitExceeded → RateLimitHit`；其他异常上抛；保留 `RuntimeError("not started")`。
- 工具层既有降级（`get_order_book` / `get_recent_trades` 的 try/except → "temporarily unavailable"）在 sim 中 0 触发，换真实数据后 CCXT 失败时首次可达 —— 加测试验证 catch 生效，工具代码不改。
- 无合成兜底（一致；sim 本就依赖网络取 ticker/OHLCV）。

## 测试策略

### A. Sim 层测试改造（`tests/test_exchange_order_book.py`）
- **删除**：`test_sim_fetch_trades_direction_bias_rising` / `_falling`（合成 bias 已不存在）。
- **重写**：`test_sim_fetch_order_book_structure` / `_custom_depth` / `test_sim_fetch_trades_structure` → mock `_ccxt.fetch_order_book` / `fetch_trades` 返回 CCXT-parsed 结构，断言映射正确。
- **新增（验证两处稳健改进）**：
  - 乱序输入 → 断言显式排序后 `bids[0]` 最高价 / `asks[0]` 最低价 / trades 按 ts 升序；
  - None 字段行 → 断言被跳过（book None price、trade None ts/side）；
  - book entry 2 元素 vs 3 元素 → 断言 `*_` 解包都 work；
  - mock `_ccxt.fetch_*` 抛 `RateLimitExceeded` → 断言转 `RateLimitHit`。

### B. 工具层耦合验证（新增；喂构造 `OrderBook`/`Trade` 给 mock deps，测 `tools_perception.py` 渲染）
- **concentration**：不对称盘口，某档 amount > 3× 同侧 median → 断言 `=== Concentrated Levels ===` 段正确（top-10 排序 / mid 距离 / below-above mid）。sim 0/98 从未跑过的承载语义路径。
- **非均衡 bid share**：`total_bid >> total_ask` → 断言 `Bid share: X% (bid : ask = N:1)` 分支。
- **partial-coverage**：mock 500 笔成交全落窗口末 ~2min（`fetch_ratio=1.0` 且 `oldest_age_ratio<0.95`）→ 断言星号 `*` + `[* partial coverage: ...]` 注记。
- **失败降级**：mock `_ccxt` 抛异常 → 断言工具返 "temporarily unavailable"。

### C. Mock 保真（per memory `project_iter2_mock_fidelity_lesson`）
至少一个 fixture 按 OKX 真实文档响应形态构造（盘口 `["px","sz","0","1"]`、成交 `{"side","sz","px","tradeId","ts"}`），避免理想化 mock 盲区（2-vs-3 元素解包 / safe_* 的 None）。
- 可选硬化：一次性 live-capture helper 录真实 OKX book+trades 响应存 fixture 回放（需联网，用户跑）—— 列为 optional follow-up，本批先用文档形态手工构造。

### 不变
`get_order_book` / `get_recent_trades` 现有工具测试（喂 mock `OrderBook`/`Trade`）逻辑不变。

## 风险

1. **可复现性**：order_book/trades 变 live 非确定 —— ticker/OHLCV 本就如此，analyze_sim/diff_sim 可比性性质不变。
2. **延迟**：0ms 合成 → 真实网络往返（几十~几百 ms）；cycle 间隔分钟级，每 cycle 增 <1s，可接受。
3. **限频**：每 cycle 各 1 调，OKX 公开限额宽松（books 20req/2s、trades 100req/2s），`RateLimitHit` 兜突发。
4. **死路径首次激活**：partial-coverage / 降级 / concentration / 非均衡分支换源后才跑（正是目的）；残留潜伏 bug 风险靠 §B 测试 + spot-check 缓解。
5. **跨会话分析混入**：历史 sim session 为合成 order_book/trades，新 session 为真实；`diff_sim` 比对新旧会混合成/真微结构 —— 分析时需知悉。
