# Iter 2b — OKX Live Hardening 设计文档

## 0. 背景

### 0.0 项目快照

**TradeBot** 是 LLM 驱动的加密货币自动交易系统，在 USDT 保证金永续合约上自主交易。Agent（Claude）通过 29 个工具感知市场、管理仓位、执行订单。

**运行循环**：每 15 分钟唤醒一次（也可被订单成交 / 价格警报提前唤醒），进入 `run_agent_cycle()` → LLM 调用工具分析 → 返回交易决策 → 写 DecisionLog。

**技术栈**：Python 3.13 / pydantic-ai 1.78+ / SQLAlchemy 2.0 async + SQLite(WAL) / pytest + pytest-asyncio / ccxt (OKX, defaultType=swap)。

**当前状态（2026-04-24，22 PRs merged）**：730 测试通过；Iter 1（metrics enabler PR #21）+ Iter 2（toolkit expansion PR #22）已 landed。

### 0.1 所处位置

本 spec 是"进观察期前 5-iteration 计划"的第 2.5 轮（插队 Iter 2b）：

| # | 主题 | 状态 |
|---|------|------|
| 1 | 观察基础设施 — tool-call metrics enabler | ✅ PR #21 landed |
| 2 | 工具补全 — 3 感知工具 + get_position 增强 | ✅ PR #22 landed |
| **2b** | **OKX Live Hardening — algo 归一化 + 实盘接入门槛** | **本 spec** |
| 3 | 结构感知工具 `get_price_pivots` 朴素版 | next |
| 4 | N7 Layer 1 prompt 重组（基于完整工具集）| last |

Iter 2b 原本在 Iter 3 之后，因 2026-04-21 OKX demo 账户 ready、Iter 2 上下文新鲜、scope 小且确定性高，提到 Iter 3 前做（外部依赖窗口期 + 上下文连续性）。

### 0.2 为什么是这一轮

**驱动动因（三重）**：

1. **实盘写路径 broken — agent 当前无法在 OKX 设 SL/TP**（最严重）：Pre-work write-path probe（`scripts/iter2b_write_path_probe.py` Attempt A）实测 —— `tools_execution.py:141-143` → `exchange.create_order(order_type="stop", price=X, params={"tdMode":"isolated"})` 在 OKX demo 上直接报 `51000 "Parameter ordType error"`；`set_take_profit` 对称推理同样 broken。CCXT unified 的 `type="stop"` 路由到 OKX algo endpoint **需要显式传 `stopLossPrice` / `takeProfitPrice` params**（Attempt B 实测成功，返回 `info.algoId` 非空）。Iter 2b 若只修读路径不修写路径，实盘归一化完美但 agent 根本**下不进** SL/TP 单 —— 整个价值只达成一半。

2. **实盘读路径 — `get_position` 裸仓误报**：Iter 2 刚加的 Exit orders 区块（`tools_perception.py:245-251`）按 `order_type ∈ {"stop", "take_profit"}` 匹配 SL/TP 展示。OKX 实盘的 SL/TP 是**独立的 algo order**（`ordType="conditional"` / `"oco"`），现有 `_parse_order`（`okx.py:327-337`）仅读 `data["type"]` 透传 `"conditional"` / `"oco"` 字符串 → `get_position` 永远匹配不到 stop/take_profit → 所有实盘仓位显示 "Stop loss: not set"（裸仓误报）。

3. **账户配置漂移风险**：系统全栈硬编码"单向 posMode + 逐仓 marginMode"假设（`persona.py:22` Agent prompt 明文 one-way；`_POSITION_SIDE_INFER` `okx.py:44-53` 单向推断表；SimExchange 单 `_Position` 模型；`_calc_liquidation_price` 按隔离语义），但代码层未显式锁定 —— 账户被手动改为双向或升级到 multi-currency/portfolio margin 账户时，`liquidation_price` 语义错位、`posSide` 推断失败，**agent 不自知**。

4. **Demo 彩排能力缺失**：`OKXExchange.__init__`（`okx.py:86`）无 sandbox 配置，CCXT 默认打 live endpoint；从 Sim 跳实盘前无低风险彩排途径。

**非目标**：Iter 2b 不触碰 agent 策略、观察期计划、SimExchange 算法、其他感知工具。只动 OKX adapter + config + 少量 tool 渲染层（`get_open_orders`）+ `Order` dataclass。

### 0.3 硬约束

- **不改单向 / 逐仓假设**：改造代价指数级（参 memory `project_iter2b_okx_algo_normalization` 评估），本轮反而要**显式锁定**两个假设，防漂移。
- **Fact-only 延续**：`get_open_orders` OCO 合并行不得引入评价词（`"protective"` / `"tight stop"` / `"wide TP"` 禁）；文字形如 `"[OCO] {side} {amount} stop {sl_px} (±X%) / tp {tp_px} (±Y%) | algoId: {id} (cancel removes both legs)"`（与现有 `[LIMIT]/[MARKET]` 标签格式对齐；完整渲染见 §2.4.1）。
- **Pre-work ground truth 优先**：算法设计依据 `tests/fixtures/okx_fetch_open_orders_*.json` 真实采样（Iter 2 Round-4 mock-fidelity lesson），不凭 OKX 文档 / CCXT 声明臆测。
- **Fixture duality**：Pre-work 归档两套 fixture：
  - `*_raw.json`（OKX raw，来自 `private_get_trade_orders_algo_pending` 顶层字段 `algoId` / `slTriggerPx` / `state` ...）—— schema 归档，不用于单元测试输入
  - `*_unified.json`（CCXT unified，来自 `fetch_open_orders(symbol, params={stop, ordType})`，含 `id` / `symbol` / `amount` / `status` 顶层 + OKX raw 嵌套在 `info` 下）—— **`_parse_order` 测试输入**，因为生产代码就消费这个形态
- **Mock 形态**：所有涉及 OKX algo response shape 的新测试 **从 `*_unified.json` load**；若需特殊 case mock（slTriggerPx 为空 / state=canceled 等），**在 unified fixture 上 copy+override**，不整个重写。raw fixture 仅用作 OKX 接口层参照。
- **SimExchange 跟随 signature，不改算法**：`_parse_order` 改 `-> list[Order]` 后 Sim 端同步 signature；Sim 的 SL/TP 合成（现有 `_PendingOrder.trigger_price` 映射到 `Order.price`）保持，`order_type` 已原生是 `"stop"` / `"take_profit"` → 无算法改动。
- **Rate limit 初步判断**：`fetch_open_orders` 从 1 路变 3 路，必须 `asyncio.gather` 并发；OKX rate limit 按 endpoint path 计，plain pending 和 algo-pending 是**两个独立 bucket**（`/trade/orders-pending` vs `/trade/orders-algo-pending`），但后者被 conditional + oco **调两次**（都打 `/trade/orders-algo-pending`，只是 params ordType 不同），对 algo-pending bucket 压力加倍。Agent cycle 频次：~每 15min 一次主循环；一轮常态调用 `fetch_open_orders` 3-4 次（agent 主动查 + `cancel_order`/`set_stop_loss`/`set_take_profit` 内部各调一次），算下来每分钟 algo-pending 打 ~1-2 次，plain-pending 打 ~0.5-1 次 —— 仍远低于 OKX 限额（20 次 / 2s per endpoint）。实际并发度 + rate limit 命中需**观察期验证**（§7.1）。

### 0.4 术语表

| 术语 | 含义 |
|------|------|
| **Algo order** | OKX 独立于普通订单的"条件/算法单"：`conditional`（单腿 trigger）/ `oco`（SL+TP 原子对）/ `trigger` / `move_order_stop` / `iceberg` / `twap`。本轮只归一化前两类。 |
| **posMode** | OKX 账户级仓位模式：`net_mode`（单向）/ `long_short_mode`（双向）。系统仅支持前者。 |
| **acctLv** | OKX 账户类型：`1` Simple / Spot Mode（**不支持 swap/margin/option**）/ `2` Single-currency margin（支持 swap + isolated 下单）/ `3` Multi-currency margin / `4` Portfolio margin（后两者 margin 语义与系统 isolated 假设不兼容）。**本轮仅接受 `acctLv="2"`**，`1` 因无 swap 能力被拒、`3/4` 因 margin 语义不兼容被拒（见 §2.6 错误消息）。 |
| **tdMode** | OKX 订单级保证金模式：`isolated` / `cross` / `cash`。系统 create_order 源头显式传 `isolated`。 |
| **algoId** | OKX algo 订单的主 id；CCXT 已映射到 unified `Order.id`（Pre-work 确认）。OCO 拆成两条逻辑 Order 时两条共享此 id。 |
| **algoClOrdId** | OKX algo 订单的客户端 clOrdId；**CCXT 未映射**到 unified `clientOrderId`（Pre-work 发现 `clientOrderId=None`），读取需走 `data["info"]["algoClOrdId"]`。本轮不依赖此字段，记为观察期 follow-up。 |
| **OCO 原子性** | OKX 的 OCO 单在交易所层是**一个 order**，cancel 任一条即两腿一起消失，触发任一腿另一腿自动 cancel。归一化拆两条 Order 仅是逻辑展示，不改变交易所语义。 |

---

## 1. 目标与非目标

### 1.1 目标（6 项）

1. `OKXExchange` sandboxMode 配置化 + `.env` 层 `OKX_DEMO_*` credentials 分流
2. `_parse_order` algo 归一化（conditional 单腿 + oco 双腿拆分）
3. `fetch_open_orders` 三路 `asyncio.gather` 合并（plain + conditional + oco）
4. `get_open_orders` OCO 合并展示（同 algoId 两条 → 一行）
5. `create_order` 显式 `tdMode="isolated"` + `cancel_order` algo 分流（`is_algo` 参数）
6. `OKXExchange.start()` posMode + acctLv 双校验 fail-fast

### 1.2 非目标

| 项 | 原因 |
|---|------|
| 双向 posMode / cross / multi-currency / portfolio margin 账户支持 | 全栈假设改动代价指数级，`0.2` 第 2 点；本轮反其道显式锁定单向+逐仓 |
| Credentials 存储统一到 `config/exchange.json` | 本次会话明确否决（scope 蔓延；只搬 OKX 造成不对称），independent 议题留观察期 |
| Algo order 其他类型：`trigger` / `move_order_stop` / `iceberg` / `twap` | 未验证用例；观察期如发现 agent 需要再扩 |
| OCO 触发通知机制变更 | 现有 fill callback 已 cover，`_seen_order_ids` 只需微调去重 |
| 非 BTC/USDT:USDT 合约 | 系统现有单 symbol 假设一致，本轮不扩 |
| Live endpoint 跑通 | Pre-work 脚本硬拦 `OKX_SANDBOX=false`，实盘首跑独立里程碑 |
| `Order.algoClOrdId` 字段添加 | 当前业务路径不依赖；留观察期 |

---

## 2. 功能设计

### 2.1 sandboxMode 配置化 + credentials 分流

#### 2.1.1 `.env.example` 更新

**增量修改**（保留现有 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 行不动），在 OKX 区块追加 / 重组为：

```bash
# OKX 实盘 credentials（留空直到实盘接入）
OKX_API_KEY=your_api_key_here
OKX_SECRET=your_secret_here
OKX_PASSWORD=your_password_here

# OKX demo（模拟盘）credentials — 新增
OKX_DEMO_API_KEY=your_demo_api_key_here
OKX_DEMO_SECRET=your_demo_secret_here
OKX_DEMO_PASSWORD=your_demo_password_here

# 对接引擎开关 — 新增
#   true  → demo (adds x-simulated-trading: 1 header + reads OKX_DEMO_*)
#   false → live (reads OKX_*)
OKX_SANDBOX=false

# 其他 API keys（保留现有行不变）
ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here
```

#### 2.1.2 `OKXExchange.__init__` signature 扩展

```python
class OKXExchange(BaseExchange):
    def __init__(
        self,
        api_key: str,
        secret: str,
        password: str,
        symbol: str,
        sandbox: bool = False,
    ):
        super().__init__()
        self._client = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": password,
            "options": {"defaultType": "swap"},
            "timeout": 30000,
        })
        if sandbox:
            self._client.set_sandbox_mode(True)
        self._sandbox = sandbox              # ← 新增：存为实例字段，start() 里给 ws_client 用
        # ... existing fields ...
        logger.info("OKX exchange initialized (%s account)", "demo" if sandbox else "live")
```

**⚠️ 关键：ws_client 必须同步 sandbox**（否则 REST→demo 但 WS→live，跨账户事件污染 + demo 永远收不到 fill）。`start()` 里创建 ws_client 后**立即**调 `set_sandbox_mode`，见 §2.6 `start()` 代码。

**⚠️ 同等关键：call-site 必须透传 sandbox**。系统唯一的 `OKXExchange` 构造位 `src/cli/app.py:261-264`：

```python
# 修改前（sandbox 参数丢失 → demo credentials 打 live endpoint → 401 auth fail）
exchange = OKXExchange(
    api_key=creds["api_key"], secret=creds["secret"],
    password=creds["password"], symbol=result.symbol,
)

# 修改后
exchange = OKXExchange(
    api_key=creds["api_key"], secret=creds["secret"],
    password=creds["password"], symbol=result.symbol,
    sandbox=settings.exchange.sandbox,   # ← 必须传
)
```

即使 `load_settings` 正确分流 demo credentials，call-site 丢失 sandbox flag 会让 OKXExchange 按默认 `sandbox=False` 打 live endpoint —— 这是和"YAML 设 sandbox 但 env 派生 False"（§2.1.3 `final_sandbox` 修复）对称的 footgun。

#### 2.1.3 `config.py` env 分流逻辑

```python
def load_settings(
    path: Path = Path("config/settings.yaml"),
    env_overrides: dict[str, str] | None = None,
) -> Settings:
    if env_overrides is None:
        load_dotenv()
        env_overrides = dict(os.environ)

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    exchange = data.get("exchange", {})
    # Env-derived sandbox flag — only the seed for setdefault
    sandbox_env = env_overrides.get("OKX_SANDBOX", "").lower() == "true"
    exchange.setdefault("sandbox", sandbox_env)
    # Final sandbox = YAML-set value (if any) else env-derived. This is the
    # single source of truth for both ExchangeConfig.sandbox AND credentials
    # dispatch — using the env-derived `sandbox_env` here would break the
    # YAML=true + OKX_SANDBOX=unset case (demo endpoint + live credentials,
    # auth failure with misleading "invalid key" error).
    final_sandbox = bool(exchange["sandbox"])

    # 根据 final sandbox flag 选择 credentials 来源
    if final_sandbox:
        exchange.setdefault("api_key", env_overrides.get("OKX_DEMO_API_KEY", ""))
        exchange.setdefault("secret", env_overrides.get("OKX_DEMO_SECRET", ""))
        exchange.setdefault("password", env_overrides.get("OKX_DEMO_PASSWORD", ""))
    else:
        exchange.setdefault("api_key", env_overrides.get("OKX_API_KEY", ""))
        exchange.setdefault("secret", env_overrides.get("OKX_SECRET", ""))
        exchange.setdefault("password", env_overrides.get("OKX_PASSWORD", ""))
    data["exchange"] = exchange

    # ══════════════════════════════════════════════════════════════════
    # ⚠️ 以下现有代码块必须原样保留，不在 Iter 2b scope 内：
    #   - macro (FRED_API_KEY / ALPHA_VANTAGE_API_KEY / COINGECKO_DEMO_API_KEY)
    #   - crypto_etf (SOSOVALUE_API_KEY)
    #   (现 config.py:130-142，13 行 setdefault logic)
    # Iter 2b 只改 exchange 区块 + sandbox 分流；其他 env override 逻辑不动
    # ══════════════════════════════════════════════════════════════════
    return Settings(**data)
```

`ExchangeConfig` pydantic model 加 `sandbox: bool = False` 字段，值透传到 `OKXExchange.__init__` 的 `sandbox` 参数。

**空 env 场景**：`load_settings(..., env_overrides={})` 时 `get("OKX_SANDBOX", "")` 返 `""`，`sandbox=False`，走 live 分支，3 组 credentials 都是空字符串。这是**向后兼容的默认行为**（现有测试依赖），`OKXExchange` 接受空 key / secret 即可构造（CCXT 不会在构造时 validate），只在第一次 REST call 时才抛 auth error —— 不影响 `load_settings` 测试。新增对应测试见 §5.2。

#### 2.1.4 Live endpoint 守卫（scope 外的 early-guard）

`OKXExchange.__init__` 里加**警示 log**：`sandbox=False` 且 api_key 非空时 log 一行 warning `"OKX live account initialized — ALL ORDERS WILL USE REAL FUNDS"`。不 fail（sandbox=False + 实盘跑是合法场景），只提高观察度。

### 2.2 `_parse_order` algo 归一化

#### 2.2.1 算法（伪代码）

```python
def _parse_order(self, data: dict) -> list[Order]:
    """CCXT-unified OKX order dict → one or more logical Order records.

    Trigger-price source strategy (two-layer):
      1. Primary: CCXT unified top-level `stopLossPrice` / `takeProfitPrice`
         (CCXT OKX adapter promotes OKX `info.slTriggerPx` / `info.tpTriggerPx`
         into these unified-spec fields; Pre-work fixture confirms both present
         as float or None on the unified dict).
      2. Fallback: OKX raw `info.slTriggerPx` / `info.tpTriggerPx` (string)
         when unified value is None — guards against CCXT version drift that
         might alter the promotion behavior.

    Dispatch:
      - ordType="conditional" + only sl_px → [Order(order_type="stop", ...)]
      - ordType="conditional" + only tp_px → [Order(order_type="take_profit", ...)]
      - ordType="conditional" + both empty / both set → warn + plain fallback
      - ordType="oco" + both present      → 2 Orders sharing id (stop + take_profit)
      - ordType="oco" + missing any trigger → warn + plain fallback
      - Plain order (type != conditional/oco) → [Order] existing path, is_algo=False
    """
    ord_type = data.get("type") or ""
    sl_px, tp_px = self._extract_trigger_prices(data)

    if ord_type == "oco":
        if sl_px is not None and tp_px is not None:
            return self._make_oco(data, sl_px, tp_px)
        logger.warning(
            "Malformed OCO (missing trigger): sl=%r tp=%r id=%s",
            sl_px, tp_px, data.get("id"),
        )
        return [self._parse_plain(data)]

    if ord_type == "conditional":
        if sl_px is not None and tp_px is None:
            return [self._make_algo_order(data, "stop", sl_px)]
        if tp_px is not None and sl_px is None:
            return [self._make_algo_order(data, "take_profit", tp_px)]
        logger.warning(
            "Unexpected conditional algo shape: sl=%r tp=%r id=%s",
            sl_px, tp_px, data.get("id"),
        )
        return [self._parse_plain(data)]

    return [self._parse_plain(data)]


def _extract_trigger_prices(self, data: dict) -> tuple[float | None, float | None]:
    """Two-layer trigger price extraction: CCXT unified primary, OKX raw fallback."""
    sl_px = data.get("stopLossPrice")
    tp_px = data.get("takeProfitPrice")
    info = data.get("info") or {}
    if sl_px is None:
        raw_sl = info.get("slTriggerPx")
        if raw_sl:      # non-empty string
            sl_px = float(raw_sl)
    if tp_px is None:
        raw_tp = info.get("tpTriggerPx")
        if raw_tp:
            tp_px = float(raw_tp)
    return sl_px, tp_px
```

`_parse_plain` 即现有单条 Order 构造逻辑（`data["type"]` 作 order_type、`data["price"]` 作 price），`is_algo=False`。

`_make_algo_order` / `_make_oco` 构造带 `is_algo=True` 的 Order，细节见 §3.1。

#### 2.2.2 `Order` dataclass 扩展

对 `base.py:33-41` 现有 `Order` 追加一个字段（**增量 diff，禁 copy-paste 整个 dataclass** —— 防意外漏字段）：

```diff
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
+    is_algo: bool = False      # 新增，归一化产物标记
```

新字段默认值 `False` 保证 Sim 和 CCXT plain 路径零改动。

#### 2.2.3 SimExchange 同步

**澄清**：Sim (`simulated.py`) **没有** `_parse_order` 内部方法 — 它直接用 `_Order` / `_PendingOrder` 数据类型构造 `Order` 返回。本轮 Sim 端需要的改动仅：

1. **`cancel_order` signature 对齐**（§2.5.2 接收新的 `is_algo: bool = False` 参数）：Sim 不区分 algo 概念，参数接收后忽略即可（但必须接收以满足 `BaseExchange.cancel_order` 抽象签名；见 §4）。
2. **确认 Order 构造位点对新字段默认值 `is_algo=False` 不敏感**：`simulated.py:686` `fetch_order` / `:710` `fetch_open_orders` / `:721` `fetch_closed_orders` / `create_order:182` 内部 —— 这些位点构造 Order 时不显式传 `is_algo`，默认 `False`，Sim 行为零变化。

Sim 的 SL/TP 工作方式不变（`_PendingOrder.trigger_price` 映射到 `Order.price`，`order_type` 原生 `"stop"` / `"take_profit"`）。SimExchange 返回的 Order 永远 `is_algo=False` —— 意味着 Sim 环境下 `get_open_orders` 的 OCO 合并渲染（§2.4.1 守卫 `all(is_algo)`）不会生效，Sim 的 SL 单和 TP 单按单腿各自一行渲染；这与 Sim 当前不支持 OCO 语义一致。

### 2.3 `fetch_open_orders` 三路 gather 合并

```python
@_retry()
async def fetch_open_orders(self, symbol: str) -> list[Order]:
    plain_task = self._client.fetch_open_orders(symbol)
    cond_task = self._client.fetch_open_orders(
        symbol, params={"stop": True, "ordType": "conditional"}
    )
    oco_task = self._client.fetch_open_orders(
        symbol, params={"stop": True, "ordType": "oco"}
    )
    plain, cond, oco = await asyncio.gather(plain_task, cond_task, oco_task)
    raw_all = list(plain) + list(cond) + list(oco)
    return [o for d in raw_all for o in self._parse_order(d)]
```

**延迟（理论上限，Pre-work 未实测）**：三路并发**理论上**约 100-150ms（单路 ~80-100ms 网络 RTT），vs 串行 ~300ms。实际并发度受 CCXT 客户端 `enableRateLimit=True`（默认开启）的 token-bucket 影响 —— 同 client 的多请求在 rate limiter 内有串行化风险。若观察期发现 `fetch_open_orders` 延迟超过 250ms，可考虑（a）关闭 `enableRateLimit`（慎用，可能触发 OKX 风控）或（b）用三个独立 client 实例。本轮不基于延迟数字做 gate。

**错误处理**：`@_retry()` 装饰器作用于整个 fetch_open_orders，若任一 gather 任务抛 NetworkError 整个重试。这会浪费另外两路的成功结果 —— 可接受（open orders 读取天然幂等，重试无副作用）。

**Gather + @_retry 取消语义**：第一路抛 NetworkError 时，`asyncio.gather` 默认（`return_exceptions=False`）立即抛并尝试 cancel 另两路未完成 task。重试时新一轮 gather 从头启动，使用**同一个 `self._client`** —— CCXT 内部连接池按请求粒度管理 connection（HTTP keep-alive 池），被 cancel 的请求如有未 close 的 response，会在 Python GC 清理；rate limit token 是进程内 counter，已消耗 token 不回退。重试有轻微 rate-limit 紧度风险（连续两轮 3 次请求 = 6 次 token）但无状态污染（每个请求独立 URL + body + signature），OKX 不会因 client-side cancel 记为异常。低风险，不加特殊处理。

**gather return_exceptions**：**不用**。三路中任一 legit API error（如 51000 Parameter error / 51001 Order doesn't exist）是契约级异常，不应静默降级。`@_retry` 只捕 `NetworkError / ExchangeNotAvailable / TimeoutError`；`ExchangeError` 子类（`BadRequest` / `OrderNotFound` / ...）默认传播到调用方，调用方（`tools_perception.get_open_orders`）应该感知并在 prompt 里以 "temporarily unavailable" 降级呈现。

### 2.4 `get_open_orders` OCO 合并展示

#### 2.4.1 渲染逻辑（伪代码）

**复用现有渲染元素**（`tools_perception.py:326-341`）：
- `"Pending Orders:"` header（必须保留）
- label 映射：`market` → `[PENDING]` / `limit` → `[LIMIT]` / `stop` → `[STOP]` / `take_profit` → `[TAKE_PROFIT]` / ...（按 `o.order_type.upper()`）
- Price 行：`f"@ {o.price:.2f} ({dist:+.2f}% from current)"`（distance 计算 `(o.price - current) / current * 100`）
- 尾巴：`f"| ID: {o.id}"`

OCO 合并分支在此结构上新增，伪代码：

```python
async def get_open_orders(deps: TradingDeps) -> str:
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return "No pending orders."
    ticker = await deps.market_data.get_ticker(deps.symbol)
    current = ticker.last

    # 按 id 分组：OCO 的两条同 id 且 is_algo=True，其他单条各自唯一 id
    by_id: dict[str, list[Order]] = {}
    for o in orders:
        by_id.setdefault(o.id, []).append(o)

    lines = ["Pending Orders:"]
    for order_id, group in by_id.items():
        is_oco = (
            len(group) == 2
            and {o.order_type for o in group} == {"stop", "take_profit"}
            and all(o.is_algo for o in group)   # 契约守卫：plain 共享 id 不该触发 OCO 合并
        )
        if is_oco:
            sl = next(o for o in group if o.order_type == "stop")
            tp = next(o for o in group if o.order_type == "take_profit")
            # distance 对 OCO 两腿各自标注
            sl_dist = f" ({(sl.price - current) / current * 100:+.2f}% from current)" if current > 0 else ""
            tp_dist = f" ({(tp.price - current) / current * 100:+.2f}% from current)" if current > 0 else ""
            lines.append(
                f"  [OCO] {sl.side} {sl.amount} "
                f"stop {sl.price:.2f}{sl_dist} / tp {tp.price:.2f}{tp_dist} "
                f"| algoId: {order_id} (cancel removes both legs)"
            )
        else:
            # 单条（含 plain / 单腿 conditional）—— 复用现有 render 逻辑
            for o in group:
                lines.append(_render_single_order(o, current))   # 现有逻辑抽函数
    return "\n".join(lines)
```

其中 `_render_single_order(o, current)` 是把现有 `tools_perception.py:327-341` 的 loop body 抽成函数，维持 `"  [LIMIT] sell 1.0 @ 65000.00 (+5.23% from current) | ID: 3506..."` 这一行形态不变 —— refactoring，不改输出。

**⚠️ 必须保留的分支**：`tools_perception.py:336-340` 存在 `current > 0` 边界分支 —— false 路径走 `price_str = f"@ {o.price:.2f}"`（不带 distance）。`_render_single_order` 抽函数时**必须完整保留此分支**，否则 `current <= 0`（ticker 异常 / fetch_ticker 失败 fallback）时会 `ZeroDivisionError`。§6 acceptance "zero byte-level regression" 的成立依赖此点。

**关键不变式**：同 id 且 {stop, take_profit} 两条 **且 `is_algo=True`** ⟺ OCO。这是归一化契约（§3.1.3）；`is_algo` 守卫是加固 —— 虽然 plain order 交易所 id 天然唯一不会共享，显式守卫让契约更清晰、防未来同 id 场景误触发。

**"cancel removes both legs" 措辞理由**：OCO 在 OKX 交易所层是**一个 algo order**，cancel 任一腿即原子取消两腿；agent 若把合并行解读为"两个可独立 cancel 的订单"，想"仅挪 TP 保留 SL"时会一并清掉 SL → 裸仓。Prompt 级认知断层，靠渲染提示 + persona.py 一行说明（§4 附加）一并修复，成本极低。

#### 2.4.2 单腿 conditional 的渲染

`order_type="stop"` 或 `"take_profit"` 的单条（非 OCO 拆分出的）保持现有单行渲染 —— 用户一般通过 Sim 的"止损单"也走这个路径。

### 2.5 `create_order` tdMode 显式 + `cancel_order` algo 分流

#### 2.5.1 `create_order` — algo 路由 + 手动构造 algo Order

**Pre-work write-path probe 结论**（`scripts/iter2b_write_path_probe.py`）：

| Attempt | 调用 | 结果 |
|---------|------|------|
| A | `create_order("stop", price=X, params={tdMode:isolated})` | **51000 ordType error**（当前系统调用） |
| B | A + `params={stopLossPrice: X}` | ✅ ALGO（`info.algoId` 非空） |
| C | `create_order("market", params={stopLossPrice: X, reduceOnly: True})` | ✅ ALGO（attach 模式） |
| D | `create_order("market", params={stopLoss: {triggerPrice, price}})` | 51053 方向错（需开仓语义） |
| **E** | `create_order("take_profit", price=X, params={takeProfitPrice: X})` | ✅ **ALGO**（`info.algoId` 非空，2026-04-24 实测）|

**胜出路径：Attempt B 和 E 对称** —— agent 当前 `set_stop_loss` / `set_take_profit` 的 call shape 兼容（仍传 `type="stop"/"take_profit", price=X`），只需在 `OKXExchange.create_order` 里**自动添加** `stopLossPrice` / `takeProfitPrice` params。take_profit 对称性**已实测验证**（非推测）。

```python
@_retry()
async def create_order(
    self, symbol: str, side: str, order_type: str, amount: float,
    price: float | None = None,
) -> Order:
    params = {"tdMode": "isolated"}
    is_algo = order_type in ("stop", "take_profit")
    # Algo routing: OKX rejects type="stop" without trigger params (51000).
    # CCXT unified takes stopLossPrice / takeProfitPrice to route to algo endpoint.
    # Verified via scripts/iter2b_write_path_probe.py: Attempt B (stop)
    # + Attempt E (take_profit) both route to OKX algo endpoint with
    # info.algoId non-empty.
    if is_algo and price is not None:
        if order_type == "stop":
            params["stopLossPrice"] = price
        else:  # take_profit
            params["takeProfitPrice"] = price

    data = await self._client.create_order(
        symbol, order_type, side, amount, price, params=params,
    )

    if is_algo:
        # ⚠️ Algo create_order 响应形态与 fetch_open_orders 不同：
        # OKX /trade/order-algo 返回仅含 algoId + clOrdId + tag（见 write-path probe
        # Attempt B dump），无 slTriggerPx / ordType / stopLossPrice 字段。
        # 走 _parse_order(data) 会因字段空触发 "both empty → warning + plain fallback"
        # 路径，返回错误的 is_algo=False + price=None Order。
        # → 对 algo 响应手动构造 Order，绕开 _parse_order 的 fetch shape 假设。
        return Order(
            id=data["id"],           # CCXT 已映射自 info.algoId
            symbol=symbol,
            side=side,
            order_type=order_type,   # 保留 "stop" / "take_profit"
            amount=amount,
            price=price,             # 来自调用方输入，CCXT 响应不含
            status="open",           # 新建的 algo 未触发 = "open"
            fee=None,
            is_algo=True,
        )
    # Plain order — CCXT create_order 响应与 fetch_open_orders 同形态，_parse_order 可用
    parsed = self._parse_order(data)
    return parsed[0]
```

**`fetch_order` 同理归一化**（§2.5.4 里 plain-first fallback）返 `parsed[0]`（conditional 单腿总是一条；OCO 场景 fetch_order 调用方少见，返回第一条 stop 是合理 default）。

#### 2.5.2 `cancel_order` signature 扩展

```python
@_retry()
async def cancel_order(
    self, order_id: str, symbol: str, is_algo: bool = False,
) -> None:
    if is_algo:
        await self._client.cancel_order(
            order_id, symbol,
            params={"stop": True, "trigger": True, "algoId": order_id},
        )
    else:
        await self._client.cancel_order(order_id, symbol)
```

Pre-work 验证：`cancel_order(algoId, symbol, params={...})` 可成功 cancel algo；plain cancel 对 algo id 报 50002 `"Incorrect json data format"`。

#### 2.5.3 调用方改动（`tools_execution.py` 三处）

Iter 2 归一化后 OKX 实盘的 `o.order_type == "stop"` / `"take_profit"` 订单几乎必然 `is_algo=True`。`tools_execution.py` 里**三处**调用 `exchange.cancel_order` —— agent-facing + 两处内部自动撤旧单 —— 都必须传 `is_algo` 才能打到 OKX 正确 endpoint。漏改任一处实盘都会 50002 BadRequest、主路径 broken。

##### A. Agent-facing `cancel_order` tool

```python
async def cancel_order(deps: TradingDeps, order_id: str, reasoning: str) -> str:
    # 查当前 open orders 找到该 order 的 is_algo 标记
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    target = next((o for o in orders if o.id == order_id), None)
    is_algo = target.is_algo if target else False
    await deps.exchange.cancel_order(order_id, deps.symbol, is_algo=is_algo)
    # ... existing decision log ...
    return f"Order {order_id} cancelled"
```

##### B. `set_stop_loss` 内部撤旧 SL（`tools_execution.py:134-138`）

```python
async def set_stop_loss(deps: TradingDeps, price: float, reasoning: str) -> str:
    positions = await deps.exchange.fetch_positions(deps.symbol)
    if not positions: ...
    p = positions[0]

    # Cancel existing stop orders — MUST forward is_algo to reach correct OKX endpoint
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "stop":
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)
    # ... existing create_order logic ...
```

##### C. `set_take_profit` 内部撤旧 TP（`tools_execution.py:164-168`）

```python
async def set_take_profit(deps: TradingDeps, price: float, reasoning: str) -> str:
    # ... positions fetch ...
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "take_profit":
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)
    # ... existing create_order logic ...
```

##### OCO 场景注意（set_stop_loss 和 set_take_profit 对称）

在 OCO 合约下，**B 段和 C 段都有对称副作用**：

- **`set_stop_loss` 替换 SL**：B 段 `for o if o.order_type == "stop"` 匹配到 OCO 的 SL 腿 → `cancel_order(algoId, is_algo=True)` → OKX 原子撤整个 OCO → **TP 腿同时消失**
- **`set_take_profit` 替换 TP**（对称）：C 段 `for o if o.order_type == "take_profit"` 匹配到 OCO 的 TP 腿 → 同样原子撤整个 OCO → **SL 腿同时消失**

两种情况下，后续 `create_order(stop_or_take_profit, ...)` 只新建独立单腿，原对腿丢失。

**这是 agent 的语义承担**：Iter 2b persona.py 已加一行 "OCO cancel removes both legs"（§4），agent 在选择走 `set_stop_loss` / `set_take_profit` 之前应自行判断是否需要同时重建对腿。Iter 2b 不在 tool 层做自动补偿（避免暗行为）。观察期若发现 agent 频繁"set_X 后对腿丢失且未重建"（§7.1 follow-up 条目），启动 tool 层自动补偿。

##### 失败情况（stale id）

如果 `order_id` 已被 OCO 触发/手动取消而 not in current open_orders（A 段），`target=None` → **早退返回 `"Order not found or already filled: {order_id}"` 字符串**（现有代码路径保留，Iter 2b 未改），不向 exchange 发 cancel 调用。Agent 下一 cycle 可再调 `fetch_open_orders` 确认订单已消失。

**Plan 阶段对 spec 的校准（post-impl 更新，2026-04-25）**：本段原文（2026-04-24 spec 起草时）描述 "target=None → is_algo=False → plain cancel → 50002（`BadRequest`，不被 `@_retry` 重试） → 异常传播给 agent" 为"合理行为"。Plan Task 7 Step 7.3 实施权衡后保留现有早退，未按原文改，理由：(1) 避免对 stale id 发无意义 API 调用；(2) `"Order not found or already filled"` 字符串比 50002 异常对 agent 更清晰（后者需要额外错误处理）。spec 此段按实施实际行为校准；50002 异常路径的判断仍保留给"in-flight id 但 exchange 侧已消失"的竞态场景（target 查到但 exchange cancel 时已 stale）——此路径仍走 plain cancel → 50002 → 异常传播，与原文描述一致。

#### 2.5.4 `fetch_order` algo 路由（plain-first fallback）

`fetch_order` 在两处被调：
- `src/agent/tools_perception.py:387` `get_trade_journal` 循环拿订单详情（from decision_logs 存的 order_id，**无 is_algo 上下文**）
- `src/integrations/exchange/okx.py:264` `_parse_fill_event` 的 pnl fallback 查询（有 `order_data["info"]` 上下文但为了简化不走显式参数路径）

归一化后 `get_trade_journal` 的 decision_logs 里可能存 algo id（来自 agent set_stop_loss 的 `Order.id` = OKX algoId）。现有 `fetch_order` 只走 plain endpoint，对 algo id 报 50002 被 except 吞掉 → journal 缺 algo 订单详情、`_parse_fill_event` pnl 恒 None。

**设计选择**：不加显式 `is_algo` 参数（对比 `cancel_order` 加参数的对称性）。理由：
- `get_trade_journal` 调用方无 `is_algo` 上下文（decision_logs schema 只存 order_id），改成显式参数 = 还得扩 schema = scope 蔓延
- `_parse_fill_event` 有 context 但 pnl fallback 是辅助路径，不值得专门分流
- OKX 只有两个 endpoint 且 50002 错误明确，adapter 层 try-plain-then-algo 清晰自洽

**实现（adapter 层内部 fallback）**：

```python
@_retry()
async def fetch_order(self, order_id: str, symbol: str | None = None) -> Order:
    try:
        data = await self._client.fetch_order(order_id, symbol)
    except ccxt.BadRequest as e:
        # OKX errCode 50002 对 algo id 调 plain endpoint 时出现。
        # 实际格式（Pre-work 观察）：okx {"code":"1","data":[{"sCode":"50002","sMsg":
        # "Incorrect json data format",...}],"msg":""}
        # → 用 JSON parse 从 sCode 精确识别，避免 "50002 / algo" 字符串宽松匹配误触
        if _is_okx_error_code(e, "50002"):
            data = await self._client.fetch_order(
                order_id, symbol,
                params={"stop": True, "trigger": True, "algoId": order_id},
            )
        else:
            raise
    parsed = self._parse_order(data)
    return parsed[0]   # 单条语义，OCO 场景返第一条 stop


def _is_okx_error_code(err: Exception, code: str) -> bool:
    """Parse OKX sCode from ccxt.BadRequest message envelope."""
    msg = str(err)
    try:
        # Strip "okx " prefix and parse JSON body
        payload = json.loads(msg.split(None, 1)[1])
        data = payload.get("data") or []
        for item in data:
            if item.get("sCode") == code:
                return True
    except (IndexError, json.JSONDecodeError, AttributeError):
        pass
    # Fallback: top-level message match (defensive)
    return f'"sCode":"{code}"' in msg
```

**字符串匹配脆弱性**：上述 `_is_okx_error_code` 依赖 OKX 错误 envelope 稳定。若 CCXT 升级换 error message 格式，fallback 到 top-level `"sCode":"50002"` 子串匹配 —— 仍然精确（完整 `"sCode":"XXXXX"` 字段比纯 `"50002"` 数字窄得多，避免误触 e.g. price "50002" 等无关 50002）。Pre-work 观察到的实际错误格式见上例。观察期若出现"fallback 被无关 50002 触发"告警，升级 matcher（§7.1）。

**成本**：仅 algo id 查询时多一次 50002 往返；journal 查询频率低，性能无影响。

**⚠️ `_parse_fill_event` pnl fallback 不受益**：`okx.py:264` 的 pnl fallback 直接调 `self._client.fetch_order(...)`（CCXT 原生 client），**不走** `self.fetch_order`（OKXExchange 方法）；本轮 §2.5.4 的修改不影响 pnl fallback 路径。对 algo id 的 pnl fallback 仍会 silent skip。影响面小：`info.pnl` 主路径（`_parse_fill_event:254-260`）对 algo fill 事件通常能直接拿到 pnl，fallback 仅在 info.pnl 缺失时兜底。留观察期验证 —— 若首次 algo 触发后出现"pnl 恒 None"模式，再决定是否改 `_parse_fill_event` 调用方式（两层 @_retry 取舍）。

**SimExchange 无需改**（`simulated.py:686` `fetch_order` 已原生返单条且 Sim 无 algo 概念）。

#### 2.5.5 `set_leverage` 显式传 `mgnMode="isolated"`

现有 `okx.py:405-407`：

```python
@_retry()
async def set_leverage(self, symbol: str, leverage: int) -> None:
    await self._client.set_leverage(leverage, symbol)    # ← 默认 CCXT 走 cross bucket
```

**问题**：结合 §2.5.1 `create_order(params={"tdMode": "isolated"})`，杠杆和保证金模式割裂：
- Agent 调 `adjust_leverage(20)` → CCXT 默认改 **cross** bucket 20x
- Agent 调 `open_position` → `create_order` 走 **isolated** bucket → 读 isolated bucket leverage（账户 default，通常 1x）
- **实际仓位 1x，系统按 20x 算风控** → 仓位规模错位 / liquidation 距离错算

**修复**（显式传 mgnMode 对齐）：

```python
@_retry()
async def set_leverage(self, symbol: str, leverage: int) -> None:
    await self._client.set_leverage(
        leverage, symbol,
        params={"mgnMode": "isolated"},    # 对齐 create_order tdMode
    )
```

单向 posMode 不传 `posSide`（OKX 在 net_mode 下拒绝 posSide 字段，与 §2.5.1 / §2.6 posMode 校验保持一致）。

**Pre-work 确认**：`scripts/iter2b_sample_okx_algo_orders.py:388-392` 调 `private_post_account_set_leverage({"instId": ..., "lever": ..., "mgnMode": "isolated"})` 成功 —— 底层 OKX endpoint 接受 isolated 杠杆设置，CCXT unified `set_leverage(..., params={"mgnMode": ...})` 会正确翻译到同一 raw API。

**SimExchange 无需改**（`simulated.py` 的 `set_leverage` 只存内部 _leverage 字段，无保证金模式语义）。

### 2.6 `start()` posMode + acctLv 双校验

```python
async def start(self) -> None:
    """Preload markets + validate account config + start WebSocket."""
    await self._client.load_markets()

    # 账户配置 fail-fast — 在 WebSocket 连接前做，失败不浪费连接资源
    config_resp = await self._client.private_get_account_config()
    config = (config_resp.get("data") or [{}])[0]

    pos_mode = config.get("posMode")
    if pos_mode != "net_mode":
        raise RuntimeError(
            f"OKX account posMode={pos_mode!r}, system expects 'net_mode' (one-way). "
            f"System全栈假设单向仓位；改动代价指数级。"
            f"Change in OKX web → Account → Settings → Position mode → One-way."
        )

    acct_lv = config.get("acctLv")
    if acct_lv != "2":
        raise RuntimeError(
            f"OKX account acctLv={acct_lv!r}, system expects '2' (Single-currency margin). "
            f"acctLv=1 (Simple) does not support swap contracts. "
            f"acctLv=3 (multi-currency) / 4 (portfolio margin) use different margin semantics "
            f"incompatible with isolated-margin model. "
            f"Change via OKX web → Trading mode → Single-currency margin."
        )

    # ─── existing WebSocket initialization with sandbox sync ───
    try:
        import ccxt.pro as ccxtpro
        self._ws_client = ccxtpro.okx({
            "apiKey": self._client.apiKey,
            "secret": self._client.secret,
            "password": self._client.password,
            "options": {"defaultType": "swap"},
        })
        # ⚠️ CRITICAL — sync sandbox to WebSocket client, otherwise
        # REST→demo but WS→live: fill events never arrive, live ticker
        # drives demo alerts, cross-account pollution.
        if self._sandbox:
            self._ws_client.set_sandbox_mode(True)
        self._running = True
        self._ws_connected = True
        self._orders_task = asyncio.create_task(self._watch_orders_loop())
        self._ticker_task = asyncio.create_task(self._watch_ticker_loop())
        loops = "watch_orders + watch_ticker"
        logger.info("OKX WebSocket started (%s, sandbox=%s)", loops, self._sandbox)
    except Exception:
        self._ws_connected = False
        logger.error("WebSocket connection failed, running in REST-only mode", exc_info=True)
```

校验放在 `load_markets` 之后、WebSocket 之前：
- `load_markets` 失败是真正的 infra issue，先解决
- account-config 校验挡在 WebSocket 之前，失败时不浪费连接开销
- WebSocket 建立在账户校验通过后，代码信任后续所有 call 的 posMode/acctLv 假设
- **`set_sandbox_mode(True)` 必须在 ws_client 创建后立即调用**，任何后续 `watch_orders` / `watch_ticker` 都会基于该 flag 决定 endpoint 路由；漏调这一行 = Iter 2b 在 demo 上完全不可用（agent 看不到任何自家 fill 事件）

---

## 3. 关键算法

### 3.1 OKX algo 字段 → `Order` 映射（基于 Pre-work ground truth）

**输入形态**：`_parse_order(data)` 的 `data` 参数是 **CCXT unified dict**（从 `self._client.fetch_open_orders(symbol, params={stop, ordType})` 返回），含 top-level `id` / `symbol` / `side` / `amount` / `status` + 嵌套 `info={OKX raw fields}`。测试时 load `*_unified.json` fixture（参见 §0.3 Fixture duality）。

#### 3.1.1 conditional（单腿）

Pre-work fixture（`okx_fetch_open_orders_conditional_sl_unified.json`）的 unified top-level（重要字段）：

```
id="3506775915768614912",      ← CCXT 把 OKX algoId 映射到这里
symbol="BTC/USDT:USDT",
type="conditional",             ← dispatch 依据
side="sell",
status="open",                  ← CCXT 把 OKX state="live" 映射到这里
amount=1.0,                     ← CCXT 从 sz=1 映射（单位=张）
price=null,
stopLossPrice=54405.3,          ← CCXT 从 info.slTriggerPx 提升的主字段
takeProfitPrice=null,           ← 单腿 SL 时 TP 自然为 null
clientOrderId=null              ← CCXT 未映射 info.algoClOrdId；§7.1 follow-up
```

嵌套 `info` 字段（OKX raw，给 fallback 用）：

```
ordType=conditional, sz=1, slTriggerPx=54405.3, slOrdPx=-1,
tpTriggerPx=(empty), algoId=<同顶层 id>, posSide=net, tdMode=isolated, state=live
```

构造（`_make_algo_order` 被 §2.2.1 主 `_parse_order` 调）：

```python
def _make_algo_order(self, data: dict, order_type: str, price: float) -> Order:
    """Build one algo Order from a CCXT-unified dict (conditional single-leg
    or one leg of OCO). Trigger price已由 §2.2.1 _extract_trigger_prices 解析。"""
    return Order(
        id=data["id"],                   # CCXT 已把 algoId 映射到这里
        symbol=data["symbol"],
        side=data["side"],
        order_type=order_type,            # "stop" or "take_profit"
        amount=float(data["amount"]),
        price=price,                      # 已是 float（unified 主字段 or info fallback float 化）
        status=data["status"],            # "open" for live algo
        fee=None,                         # algo 未触发无 fee
        is_algo=True,
    )
```

#### 3.1.2 oco（双腿）

Pre-work fixture（`okx_fetch_open_orders_oco_unified.json`）unified top-level 比 conditional 多两处差异：

```
type="oco",
stopLossPrice=54405.3,
takeProfitPrice=101038.3,       ← 两个都非 null，这是 OCO 契约标志
```

嵌套 `info` 字段（OKX raw，给 fallback 用）：

```
ordType=oco, sz=1, slTriggerPx=54405.3, slOrdPx=-1,
tpTriggerPx=101038.3, tpOrdPx=-1, algoId=<同顶层 id>, ...
```

构造（拆两条共享 id 的 Order，供 §2.4 OCO 合并渲染识别）：

```python
def _make_oco(self, data: dict, sl_px: float, tp_px: float) -> list[Order]:
    """Split an OCO algo into 2 logical Orders sharing algoId (= data['id'])."""
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
        Order(order_type="stop", price=sl_px, **common),
        Order(order_type="take_profit", price=tp_px, **common),
    ]
```

**两条共享 `id`** 是渲染层 OCO 合并的关键（§2.4）。

#### 3.1.3 归一化契约

- 同一 `id` 且 `order_type ∈ {"stop", "take_profit"}` 各一条 **且 `is_algo=True`** ⟺ 来自同一 OCO algo
- `is_algo=True` 表示该 Order 来自 OKX algo endpoint，cancel 必须走 algo 路径（§2.5.2）
- **触发价数据源优先级**：CCXT unified top-level `stopLossPrice` / `takeProfitPrice`（主，已是 `float | None`）→ OKX raw `info.slTriggerPx` / `info.tpTriggerPx`（fallback，`str` → `float`）。Pre-work fixture 实测 CCXT 已做字段提升（两字段都在 unified top-level），fallback 仅防 CCXT 版本升级可能改映射行为。若未来 CCXT 仍然提升该字段但加入其他（如 `trailingStopPrice`），再按同机制扩展。
- `Order.status` 对 algo 是 CCXT 映射自 `info["state"]`：**Pre-work 仅实测 `"live" → "open"`**。其他状态（`"effective"` 已触发 / `"canceled"` / 其他）的 CCXT 映射**未经采样**，留**观察期验证**（§7.1）。
- **诊断 log 位置**：放在 `_watch_orders_loop` 循环顶部（**所有 status 分支之前**），而非 `_parse_fill_event` 内部。原因：`_parse_fill_event` 只在 `_watch_orders_loop:169` `status == "closed"` 分支被调用；若 CCXT 把 algo `state="effective"` 映射成 **非 `"closed"`** 的字符串（如 `"triggered"` / 原样 `"effective"`），`_parse_fill_event` **不会被调用**，log 永不触发 → 观察期 follow-up 自我屏蔽。
  ```python
  # src/integrations/exchange/okx.py _watch_orders_loop — 新增在 line 154 for 循环顶部
  for order_data in orders:
      info = order_data.get("info") or {}
      # algo-lineage 诊断 log — 覆盖所有 status 分支前
      if info.get("ordType") in ("conditional", "oco") or info.get("algoId") not in (None, ""):
          logger.info(
              "algo-lineage raw event: raw_ordType=%s raw_state=%s "
              "unified_status=%s id=%s algoId=%s",
              info.get("ordType"), info.get("state"),
              order_data.get("status"), order_data.get("id"), info.get("algoId"),
          )
      status = order_data.get("status")
      # ... existing status dispatch ...
  ```
  **Guard 双分支设计**：OCO / conditional 触发时 OKX watch_orders 事件形态未经实测，两种假设并存：
    - 假设 A：OKX 推 algo state 变更事件 → `info.ordType ∈ {"conditional", "oco"}`
    - 假设 B（更可能）：OKX 以底层订单形式推 fill 事件 → `info.ordType` 是 `"market"` / `"limit"`，但 `info.algoId` 非空指向原 algo
  
  `or info.get("algoId")` 分支覆盖假设 B —— 任一假设成立都能捕捉事件。日志 5 字段同时记录 `raw_ordType` / `raw_state` / `unified_status` / `id` / `algoId`，两种形态下都能从日志反推真实语义，首次 OCO 触发后即可决定是否扩 `_watch_orders_loop` 的 status 判断逻辑。

### 3.2 `_seen_order_ids` OCO 触发事件去重

memory `project_iter2b_okx_algo_normalization` §4 提到：OCO 触发时 OKX 推两条 watch_orders 事件 —— 一腿 fill + 另一腿 `oco_auto_cancel`。

现有 `_watch_orders_loop`（`okx.py:148-186`）只对 `status == "closed"` 的 fill 事件处理 `_seen_order_ids`；cancel 事件（status != "closed" or filled == 0）不走 fill_callback，也不加 `_seen_order_ids`。

**本轮不改 `_watch_orders_loop` 的去重逻辑** —— 现有逻辑在**两种假设下均不产生问题**：

**假设 A（§3.1.3 假设 A：OKX 推 algo state 变更事件，共享 algoId）**：
- Fill 事件：push 到 `_seen_order_ids`，触发 fill_callback
- Cancel 事件（对腿）：`status != "closed"`，走不到 `seen_order_ids` 分支，也不触发 fill_callback
- 同 algoId 的 fill + cancel 共享 id，但 cancel 被 status 过滤不进 dedup，最终 fill_callback 触发 1 次 ✓

**假设 B（§3.1.3 假设 B，更可能：OKX 推底层订单 fill 事件，id 是底层 market ord_id，非 algoId）**：
- Fill 事件：用底层 market ord_id 作为 dedup key，触发 fill_callback
- 对腿 cancel 事件：用**另一个**底层 ord_id（独立 id），即使走 `status="canceled"` 分支也是不同 id 不冲突
- 天然 dedup 无问题 ✓

**事件顺序 order-agnostic**：无论"先 fill 后 cancel"还是"先 cancel 后 fill"，两种假设下最终 fill_callback 都只触发 1 次。假设 A 下靠 status 过滤，假设 B 下靠独立 id —— **两条路径都正确**，但原因不同。

真实触发事件 shape 在 Pre-work 无法观察（需要价格穿越 trigger）—— 标记为 **观察期 follow-up**（§7）：首次真实 OCO 触发后 log raw event，验证逻辑。

### 3.3 多路 `fetch_open_orders` 的排序

三路 gather 合并结果**不保排序**（OKX 各 endpoint 按 create time 各自排）。渲染层（`tools_perception.get_open_orders`）当前不依赖顺序；Agent prompt 里 exit orders 也按"匹配出场单"逻辑显示，不是时间线。

**不引入显式 sort** —— YAGNI。如未来某 tool 依赖 open orders 时序，在那个 tool 层 sort 即可。

---

## 4. Schema / signature 变更

| 文件 | 变更 | 破坏性? |
|------|------|--------|
| `src/integrations/exchange/base.py` | (1) `Order` 加 `is_algo: bool = False` 字段；(2) **抽象 `cancel_order` signature 加 `is_algo: bool = False` 参数**（`base.py:115`；子类 override 需同步，否则违反 LSP + mypy 报错）| 否（新字段默认值；新参数带默认值）|
| `src/integrations/exchange/okx.py` | `__init__` 加 `sandbox: bool = False` + 存为 `self._sandbox`；**`create_order` 加 algo 路由**（`order_type ∈ {stop, take_profit}` 时自动添加 `stopLossPrice`/`takeProfitPrice` params + 手动构造 algo Order 绕开 `_parse_order`，§2.5.1）；`_parse_order` → `-> list[Order]`；`fetch_open_orders` 三路 gather；`fetch_closed_orders` **仅 signature flattening**（`[o for d in raw for o in self._parse_order(d)]`，不做 algo endpoint gather，algo closed 查询不在本轮 scope）；**`fetch_order` 加 plain-first + 50002 fallback algo 路由**（§2.5.4）；`cancel_order` 加 `is_algo: bool = False` 参数；**`set_leverage` 显式传 `params={"mgnMode": "isolated"}`**（§2.5.5）；`start()` 加账户校验 + **`ws_client` 创建后立即 `set_sandbox_mode(True)`**（§2.6，P0 critical）；`_watch_orders_loop` 循环顶部加 algo-lineage 诊断 log（§3.1.3）| 内部（signature 改动须同步 Sim + 调用方）|
| `src/integrations/exchange/simulated.py` | `cancel_order` signature 对齐 `BaseExchange`（加 `is_algo: bool = False`，Sim 忽略参数）；**无需改 Order 构造**（Sim 返回的 Order 永远默认 `is_algo=False`）；`fetch_closed_orders` 无需改（Sim 无 algo endpoint 分拆）| 内部 |
| `src/config.py` | `ExchangeConfig` 加 `sandbox: bool = False`；`load_settings` 分流 credentials（用 `exchange["sandbox"]` 最终值，§2.1.3）| 否（新字段默认值）|
| `src/agent/tools_execution.py` | **三处**调 `exchange.cancel_order` 的位置都要传 `is_algo`（§2.5.3）：(1) agent-facing `cancel_order` tool（读 fetch_open_orders 结果的 `Order.is_algo`）；(2) `set_stop_loss` 内部撤旧 SL（`o.is_algo` 转发）；(3) `set_take_profit` 内部撤旧 TP（`o.is_algo` 转发）；漏改任一处实盘 broken | 否 |
| `src/agent/tools_perception.py` | `get_open_orders` 渲染重构：抽 `_render_single_order` helper + 加 OCO 合并分组 + `"(OCO — cancel removes both legs)"` 后缀（§2.4.1）| 否（单行渲染形态保留）|
| `src/agent/persona.py` | Layer 1 `_build_layer1()` 的 **"Tool Usage Notes" bullet 列表**中、靠近现有 SL/TP 相关 bullet（若有）或 bullet 列表末尾，追加一行："**OCO stop/take-profit orders are atomic on OKX: cancelling or triggering one leg removes both** —— 替换 SL/TP 时若需保留对腿，应同步 re-create" | 否（prompt 增量一行）|
| `src/cli/app.py` | **构造 OKXExchange 时透传 `sandbox=settings.exchange.sandbox`**（`app.py:261-264`，P0 call-site wiring；否则 OKXExchange 默认 `sandbox=False` 让 REST 打 live endpoint，使 §2.1.3 的 credentials 分流失效）| 否（新参数带默认值）|
| `.env.example` | 增量：加 3 条 `OKX_DEMO_*` + `OKX_SANDBOX` + 注释；保留现有 `OKX_API_KEY` / `OKX_SECRET` / `OKX_PASSWORD` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` 不变 | 否 |

---

## 5. 测试策略

### 5.1 Fixture-loaded mocks（强约束）

所有涉及 OKX algo response shape 的测试**必须 load `tests/fixtures/okx_fetch_open_orders_*_unified.json`**（Pre-work 归档的 CCXT unified 形态），禁从零手写 dict。若需特殊场景（slTriggerPx 为空、state=canceled、ordType=trigger 等），**在 unified fixture 上 copy+override**，不整个重写。

`*_raw.json` 仅用作 OKX 接口层 schema 归档，**不用于单元测试输入** —— 生产代码 `_parse_order` 消费的是 CCXT unified wrapper（`fetch_open_orders(...)` 经过 CCXT parse_order 后的形态），含 top-level `id`/`symbol`/`amount`/`status` + 嵌套 `info` = OKX raw。混用会立即 KeyError。

**动机**：Iter 2 Round-4 两个生产 bug（`parse_bid_ask` 3 元素 / 负 age_ms IndexError）都是手写 mock 凭 CCXT 文档写的结果；Iter 2b 的 `_parse_order` 归一化正是 shape-敏感代码，mock fidelity 直接决定测试有效性。额外地，CCXT 对 OKX swap `amount` 的映射（sz 张数 vs sz × contractSize）未在 Pre-work 确认（probe 显示 `amount=1.0 == sz=1` 但非契约），手写 wrapper 极易错 —— 强制走 fixture 规避。

### 5.2 测试清单

**`tests/test_okx_exchange.py` 或新 `tests/test_okx_algo_normalization.py`**：

- `_parse_order_plain_returns_single_order` — 现有行为回归
- `_parse_order_conditional_sl_produces_stop_order` — `conditional_sl_unified.json` loaded 原样，断言 order_type="stop"、price=54405.3（来自 unified `stopLossPrice`）、is_algo=True
- `_parse_order_conditional_tp_produces_take_profit_order` — 在 unified fixture 上 override `stopLossPrice=None, takeProfitPrice=60000.0`（+ `info.slTriggerPx="", info.tpTriggerPx="60000"` 同步），断言 order_type="take_profit", price=60000.0
- `_parse_order_conditional_falls_back_to_info_when_unified_none` — override unified `stopLossPrice=None`, 保留 `info.slTriggerPx="54405.3"` → 断言 fallback 生效，order_type="stop", price=54405.3
- `_parse_order_conditional_both_empty_falls_back_to_plain_with_warning` — override unified 两个 price 为 None + info 两字段为空，断言 warning log + 走 plain 路径
- `_parse_order_oco_splits_to_two_orders_sharing_id` — `oco_unified.json` loaded 原样，断言 len=2, ids equal, types={stop, take_profit}, prices={54405.3, 101038.3}, 两条都 is_algo=True
- `_parse_order_oco_malformed_falls_back_with_warning` — override `takeProfitPrice=None` + `info.tpTriggerPx=""` → 断言 warning + fallback
- `_parse_order_unknown_algo_type_falls_back` — override `type="trigger"` + `info.ordType="trigger"`，走 plain
- `fetch_open_orders_merges_three_endpoints` — mock 三路各返一条，断言合并后 list 总数 + 各路条目都在
- `fetch_open_orders_concurrent_not_serial` —（**可选，CCXT 版本敏感，不作 acceptance gate**）用 `asyncio.Event` 断言 gather 并发
- **`fetch_order_plain_endpoint_first`**（P1-3）— mock ccxt.fetch_order 返正常，断言不传 algo params
- **`fetch_order_falls_back_to_algo_on_50002`**（P1-3）— mock ccxt.fetch_order 先 raise `BadRequest("50002 Incorrect json data format")`，第二次正常返；断言第二次调用传 `params={"stop": True, "trigger": True, "algoId": order_id}`
- **`fetch_order_non_50002_error_propagates`**（P1-3）— mock 抛其他 BadRequest（如 51001 Order not found），断言不 fallback、异常传播
- **`set_leverage_passes_mgnMode_isolated`**（P1-4）— mock ccxt.set_leverage，断言 params 含 `{"mgnMode": "isolated"}`，且不含 `posSide`（单向模式不能传）
- **`create_order_stop_adds_stopLossPrice_param`**（write-path P0）— mock ccxt.create_order，`create_order(side, "stop", amount, price=50000)` 调用时 params 含 `{"tdMode":"isolated", "stopLossPrice":50000}`
- **`create_order_take_profit_adds_takeProfitPrice_param`**（write-path P0）— 同上对称，`order_type="take_profit"` → params 含 `{"takeProfitPrice": X}`
- **`create_order_stop_returns_is_algo_true_with_input_price`**（write-path P0）— mock ccxt 返最小 algo 响应（仅 `id` + `info.algoId`），断言返回 Order 的 `is_algo=True` + `price=<输入值>` + `order_type="stop"` + `status="open"`
- **`create_order_plain_limit_unchanged_regression`**（write-path P0）— `order_type="limit"` 走原 `_parse_order` 路径，params 不含 stopLossPrice，行为零回归
- **`app_constructs_okx_exchange_with_sandbox_flag`**（#1 call-site wiring）— mock `settings.exchange.sandbox=True`，启动 CLI 流程，断言 `OKXExchange.__init__` 被传 `sandbox=True`（e.g. 通过 patch `OKXExchange` 构造器捕获 kwargs）
- `cancel_order_is_algo_true_passes_stop_params` — mock ccxt.cancel_order，断言调用参数
- `cancel_order_is_algo_false_plain_call` — 同上，plain 路径
- `create_order_passes_tdMode_isolated` — mock 断言 create_order call args
- `start_rejects_long_short_posMode` — mock private_get_account_config 返 `{posMode: "long_short_mode"}`，断言 RuntimeError
- `start_rejects_multi_currency_acctLv` — mock 返 `{acctLv: "3"}`
- `start_rejects_portfolio_margin_acctLv` — mock 返 `{acctLv: "4"}`
- `start_rejects_simple_acctLv` — mock 返 `{acctLv: "1"}`（Simple/Spot 不支持 swap）
- `start_accepts_net_mode_single_currency` — Pre-work 实测配置 posMode=net_mode + acctLv=2
- `okx_init_sandbox_true_calls_set_sandbox_mode_on_rest_client` — mock ccxt.okx，断言 REST client `set_sandbox_mode(True)` 被调
- `okx_init_sandbox_false_does_not_call_set_sandbox_mode` — 同上反向
- **`start_with_sandbox_true_calls_set_sandbox_mode_on_ws_client`**（P0-1）— mock ccxtpro.okx，断言 ws_client 创建后 `set_sandbox_mode(True)` 被调（而非只有 REST client 调到）
- **`start_with_sandbox_false_ws_client_stays_live`**（P0-1）— 同上反向，sandbox=False 时 ws_client 不调 set_sandbox_mode

**`tests/test_config.py`**：

- `load_settings_sandbox_true_reads_demo_credentials` — env 提供 `OKX_SANDBOX=true + OKX_DEMO_*`，断言 ExchangeConfig.api_key == demo_key
- `load_settings_sandbox_false_reads_live_credentials` — env 提供 `OKX_SANDBOX=false + OKX_*`，断言 api_key == live_key
- `load_settings_missing_sandbox_defaults_live` — env 无 OKX_SANDBOX，走 live 路径
- `load_settings_sandbox_flag_propagates_to_exchange_config` — 断言 ExchangeConfig.sandbox == bool flag
- `load_settings_empty_env_dict_defaults_to_live_path_empty_credentials` — `env_overrides={}` → sandbox=False + 3 组 credentials 为空字符串（向后兼容验证）

**`tests/test_tools_perception.py`**：

- `get_open_orders_merges_oco_into_single_line` — 注入两条同 id、`is_algo=True`、`order_type ∈ {stop, take_profit}` 的 Order，断言渲染输出**一行**含 `"[OCO]"` + `"stop"` + `"tp"` + `"| algoId:"` + `"cancel removes both legs"` 几个片段（避开 `±X%` 浮点数值断言）
- `get_open_orders_non_oco_single_orders_separate_lines` — 单腿 stop / plain limit 等保持多行
- `get_open_orders_fact_only_no_banned_words` — regression for Iter 2 N5 约束

**`tests/test_tools_execution.py`**：

- `cancel_order_tool_routes_is_algo_correctly` — 模拟 fetch_open_orders 返含 `is_algo=True` 的 Order，断言 agent-facing `cancel_order` tool 把 `is_algo=True` 传给 exchange
- `cancel_order_tool_plain_order_passes_is_algo_false` — 同上反向
- **`set_stop_loss_forwards_is_algo_true_for_algo_sl`**（P0-1）— 模拟 fetch_open_orders 返一条 `order_type="stop"` + `is_algo=True` 的 Order，调 `set_stop_loss(price=X)`，断言内部 `cancel_order` 被传 `is_algo=True`（而非默认 False）
- **`set_stop_loss_forwards_is_algo_false_for_sim_sl`**（P0-1）— Sim 场景，旧 SL `is_algo=False` → 断言传 False
- **`set_take_profit_forwards_is_algo_true_for_algo_tp`**（P0-1）— 同上 stop/tp 对称
- **`set_take_profit_forwards_is_algo_false_for_sim_tp`**（P0-1）— 同上 Sim 对称

**`tests/test_simulated_exchange.py`** / **`tests/test_okx_websocket.py`**：

- 现有测试若因 signature 改动（list[Order] 返回 / Order 加 is_algo）需要调整：**最小修改原则**，只包 `[order]` 或忽略新字段

### 5.3 Pre-work 脚本 + fixtures 入库

- `scripts/iter2b_sample_okx_algo_orders.py` —— 读路径 Pre-work 工具（fixture 采样）
- `scripts/iter2b_write_path_probe.py` —— 写路径 Pre-work 工具（CCXT unified create_order routing 探测），决定 §2.5.1 algo 路由设计
- fixture 5 个（读路径实测归档）：
  - `tests/fixtures/okx_account_config.json` —— 账户模式校验参考
  - `tests/fixtures/okx_fetch_open_orders_conditional_sl_raw.json` —— OKX 接口层 schema 归档
  - `tests/fixtures/okx_fetch_open_orders_oco_raw.json` —— 同上
  - `tests/fixtures/okx_fetch_open_orders_conditional_sl_unified.json` —— `_parse_order` 单测输入
  - `tests/fixtures/okx_fetch_open_orders_oco_unified.json` —— 同上
- 可选 fixture（若实施阶段跑 §5.4 REPL 时一并归档）：
  - `tests/fixtures/okx_fetch_balance_idle.json` —— 开仓前余额币种基线，用于观察期对比 `auto_transfers_ccy` 效果

### 5.4 Pre-work 重跑手动验证

PR review 期间 / 实施阶段，Pre-work 脚本至少再重跑一次以确保可复现：脚本自动清理所有自身创建的订单 + 在 demo 上无副作用。重跑后额外手动验证（不自动化）：

1. **fetch_balance USDT 余额 gate（阻塞性）**：在 Python REPL 里
   ```python
   import asyncio, os, ccxt.async_support as ccxt
   from dotenv import load_dotenv
   load_dotenv(".env")
   client = ccxt.okx({
       "apiKey": os.environ["OKX_DEMO_API_KEY"],
       "secret": os.environ["OKX_DEMO_SECRET"],
       "password": os.environ["OKX_DEMO_PASSWORD"],
       "options": {"defaultType": "swap", "fetchMarkets": ["swap"]},
   })
   client.set_sandbox_mode(True)
   bal = asyncio.run(client.fetch_balance())
   assert bal["total"].get("USDT", 0) > 0, \
       "DEMO USDT balance is 0 — fetch_balance hardcoded to read USDT (okx.py:375-381)"
   print(bal["total"])
   asyncio.run(client.close())
   ```
   **断言必须通过**，否则本轮 Iter 2b 阻塞（余额币种失配会让 `get_position` Risk exposure / kill-switch 全局失准）。

   **关于 `settleCcy=USDC` vs `total.USDT` 的澄清**：Pre-work fixture `okx_account_config.json` 显示 `settleCcy=USDC` + `settleCcyList=["USDC", "USDG"]` + `mgnIsoMode=auto_transfers_ccy`；前两项是账户层的"主结算币偏好"配置字段，**不约束 `balance["total"]` 里任何币种的具体余额**（类比：默认银行账户币种可以是美元，但账户里仍可同时持有人民币余额）。2026-04-24 demo 账户实测 `total.USDT=85058.91` 即使 `settleCcy=USDC` 也成立。

   **⚠️ `mgnIsoMode=auto_transfers_ccy` 风险**：该 OKX 模式可能在开仓时**自动把 USDT 余额换成 settleCcy（USDC）作为保证金** —— 这意味着 §5.4 首跑 assert 通过（静态 USDT 余额 > 0）但**开仓后再查 `fetch_balance`**，`total.USDT` 可能被自动换走变 0 —— 下一轮 `get_position` Risk exposure 里 "margin from balance.used_usdt" 会错算。

   **建议新增 Pre-work 归档**：上面 REPL 跑完后，**把 `bal` dict 序列化归档**到 `tests/fixtures/okx_fetch_balance_idle.json`（或在 write-path probe 脚本里顺手 dump），作为"开仓前余额币种分布"基线。首次 demo 彩排开仓后手动再查 fetch_balance 对比 —— 若变化显著（USDT 骤降），立即记录到 §7.1 follow-up "USDT/USDC auto-conversion" 条目。

   若开仓后 `total.USDT=0` 成为稳定模式（非偶发），启动独立议题"支持 USDC 结算账户"，观察期决策。
   
   若 demo 账户余额切到 USDC（assert 失败），两个处置路径：
   - **推荐**：demo 网页手动把余额换成 USDT，重做验证（最快）
   - **Scope 外**：若 USDC 不可换回 USDT，升级为独立议题 "支持 USDC 结算账户"，Iter 2b 暂停等决策
   
   当前 demo 账户（uid=785178828971991265）2026-04-24 实测 `total.USDT=85058.91`，assert 通过。

2. **fetch_positions 币种**：同理检查 position 的 marginMode / marginCcy 字段与系统假设（USDT / isolated）一致性。

---

## 6. Acceptance criteria

完成 Iter 2b 必须满足：

- [ ] `.env.example` 包含 `OKX_SANDBOX` + 3 条 `OKX_DEMO_*` + 注释
- [ ] `config.py` 按 `OKX_SANDBOX` flag 分流 live/demo credentials；`env_overrides={}` 走 live 分支空字符串（向后兼容）；新增测试通过
- [ ] `OKXExchange.__init__` 接受 `sandbox: bool = False` 参数；sandbox=True 时 **REST client** `set_sandbox_mode(True)` 被调用；`self._sandbox` 存为实例字段
- [ ] **`OKXExchange.start()` 创建 `ws_client` 后，若 `self._sandbox == True` 则调 `self._ws_client.set_sandbox_mode(True)`**（P0 — 否则 REST→demo / WS→live 跨账户污染）
- [ ] `OKXExchange.start()` 在 load_markets 之后调 `private_get_account_config`；`posMode != "net_mode"` 或 `acctLv != "2"` → RuntimeError 明确指引
- [ ] `_parse_order` signature `-> list[Order]`；Sim 和 OKX 同步；`fetch_closed_orders` 同步 flat merge
- [ ] `_parse_order` 对 `okx_fetch_open_orders_conditional_sl_unified.json` 返 `[Order(order_type="stop", price=54405.3, is_algo=True)]`，price 主路径取 `stopLossPrice` float 字段
- [ ] `_parse_order` 对 `okx_fetch_open_orders_oco_unified.json` 返 2 条共享 id 的 `[Order(stop, price=54405.3), Order(take_profit, price=101038.3)]`，两条都 `is_algo=True`
- [ ] `_parse_order` 在 unified `stopLossPrice=None` 但 `info.slTriggerPx` 非空时 fallback 到 info 层（CCXT 版本兼容防御）
- [ ] `_watch_orders_loop` 循环顶部（所有 status 分支前）加 algo-lineage 诊断 log，guard `ordType ∈ {conditional, oco} or algoId 非空`，log 5 字段（`raw_ordType` / `raw_state` / `unified_status` / `id` / `algoId`）
- [ ] `fetch_open_orders` 三路 gather 合并；mock 测试验证三路合并（并发 timing 断言可选，CCXT 版本敏感不作 gate）
- [ ] `create_order` 调 ccxt 时 params 含 `tdMode="isolated"`
- [ ] **`create_order(order_type="stop", price=X)` 在 ccxt 层 params 含 `stopLossPrice=X`**（write-path P0，Attempt B 胜出路径）
- [ ] **`create_order(order_type="take_profit", price=X)` 在 ccxt 层 params 含 `takeProfitPrice=X`**（对称，Pre-work `write_path_probe.py` Attempt E 2026-04-24 实测 ALGO 返回，非推测）
- [ ] **`create_order` 对 algo 响应手动构造 `Order`**（`is_algo=True` + `price=input_value`），不走 `_parse_order`（绕开 algo create 响应字段稀疏问题）
- [ ] **`src/cli/app.py:261-264` 透传 `sandbox=settings.exchange.sandbox`**（call-site wiring P0；否则 OKXExchange 默认 sandbox=False 让 demo credentials 打 live endpoint）
- [ ] **`set_leverage` 调 ccxt 时 params 含 `mgnMode="isolated"`**（P1-4，否则 isolated 仓位 leverage 始终是账户 default）
- [ ] **`fetch_order` 实现 plain-first + 50002 fallback algo 路由**（P1-3）；`get_trade_journal` 查 algo 订单详情不再 silent skip；`_parse_fill_event` pnl fallback 路径**不在本轮 scope**（`okx.py:264` 直调 `self._client.fetch_order`，不走本 OKXExchange 方法；见 §2.5.4 ⚠️  说明，留观察期）
- [ ] `cancel_order(id, symbol, is_algo=True)` 调 ccxt 时 params 含 `stop + trigger + algoId`；`is_algo=False` 走 plain 路径
- [ ] `Order` dataclass 加 `is_algo: bool = False`
- [ ] **`BaseExchange.cancel_order` 抽象 signature 加 `is_algo: bool = False`（`base.py:115`），Sim + OKX 子类 signature 对齐** — mypy / pyright 通过
- [ ] `tools_perception.get_open_orders` 同 id 且 `all(is_algo)` 的 {stop, take_profit} 渲染为 `"[OCO] ... stop X / tp Y | algoId: ... (cancel removes both legs)"`；单腿和 plain 走 `_render_single_order` 保持 header + label + distance + ID 格式不变
- [ ] `persona.py` Layer 1 一行说明 OCO 原子性语义
- [ ] **`tools_execution.cancel_order`（agent-facing tool）、`set_stop_loss`、`set_take_profit` 三处 `exchange.cancel_order` 调用都转发 `is_algo`**（漏任一处实盘 SL/TP 替换主路径 broken）
- [ ] **现有 `tests/test_tools_perception.py` 关于 `get_open_orders` plain 路径（非 OCO）的字符串断言测试 zero byte-level regression** —— `_render_single_order` helper refactor 必须保留 (a) `current > 0` 分支（否则 ticker 异常时 `ZeroDivisionError`）、(b) label 格式（`[LIMIT]` / `[MARKET]` / `[STOP]` 等）、(c) distance 渲染、(d) `| ID: {o.id}` 尾巴 完全不变
- [ ] 新增测试全部通过（含 WS sandbox、fetch_order fallback、set_leverage mgnMode、create_order algo 路由 4 条、set_stop_loss/set_take_profit forwards_is_algo 4 条、app.py wiring 等）；730 测试 baseline 无 regression；Iter 2b **新增约 32 条** + **修改约 5 条现有**（signature 调整，非新增），合计 pytest 测试数约 **762**
- [ ] `fetch_open_orders_concurrent_not_serial` 测试存在但**标 `pytest.mark.skip` 或 `skipif(ccxt_version)`**（CCXT 版本敏感，不作 merge gate，仅 advisory）
- [ ] `scripts/iter2b_sample_okx_algo_orders.py` + `scripts/iter2b_write_path_probe.py` + 5 个读路径 fixture 入库
- [ ] Pre-work 脚本重跑能正常完成（cleanup 成功，产生相同 fixture shape）
- [ ] **§5.4 USDT 余额 gate assert 通过**（`bal["total"].get("USDT", 0) > 0`），否则 Iter 2b 阻塞

---

## 7. 已知 follow-up / 观察期 candidates

### 7.1 观察期 follow-up

| 项 | 触发条件 |
|---|---------|
| **OCO 触发真实事件 shape 验证** | 首次真实 OCO 被 price 穿越触发时 log raw watch_orders 事件，验证三点同时落地：(1) `_seen_order_ids` 去重逻辑正确处理"一腿 fill + 另一腿 cancel"双事件；(2) 假设 A/B 哪个成立（`info.ordType` 是 `conditional/oco` 还是 `market/limit`）；(3) 触发腿如何从事件字段区分（SL 腿 vs TP 腿）——用于后续 `_TRIGGER_REASON_MAP` 扩展的触发判据（见同 §7.1 `_TRIGGER_REASON_MAP` 条目）|
| **Algo-lineage order 事件形态 + state 映射校验**（新增）| `_watch_orders_loop` 循环顶部的 `"algo-lineage raw event: raw_ordType=... raw_state=... unified_status=... algoId=..."` log 首次出现时，两种证据都可能拿到：(1) `raw_ordType ∈ {"conditional", "oco"}` → OKX 确实推 algo state 变更事件；(2) `raw_ordType ∈ {"market", "limit"}` 且 `algoId` 非空 → OKX 推底层订单 fill 事件 + algoId 血缘。任一形态下核对 `raw_state` / `unified_status` 的 CCXT 映射（如 `state="effective"` 是否映射到 `"closed"`）；若映射非 `"closed"`，`_watch_orders_loop` 的 `status == "closed"` 分支可能漏 fill，需扩 status 判断 |
| **set_stop_loss / set_take_profit 在 OCO 场景下孤腿失落（双向对称）**（P0-1 副产品）| Agent 在 OCO 合约下调 `set_stop_loss` 替换 SL → OKX OCO 原子 cancel 连带撤 TP 腿 → `create_order(stop)` 只新建独立 SL，TP 丢失。**对称地**：agent 调 `set_take_profit` 替换 TP → 原子撤 SL 腿 → 只新建独立 TP，SL 丢失（裸仓风险）。Iter 2b 在 persona.py prompt 提示 OCO 原子性，不在 tool 层自动补偿。若日志里观察到 agent 频繁"`set_stop_loss`/`set_take_profit` 后 `fetch_open_orders` 无对腿且未紧跟重建"模式 ≥ N 次 → 启动 tool 层补偿逻辑（两个方向对称实现：`set_stop_loss` 自动 re-create 原 TP；`set_take_profit` 自动 re-create 原 SL）|
| **Unknown algo ordType 出现** | 日志里出现 `"Unexpected conditional algo shape"` / `"Malformed OCO"` / `"unknown ordType"` warning ≥ N 次 → 扩展归一化支持 |
| **`marginMode=cross` 残留持仓渲染偏差** | 实盘接入后若用户已有 cross 持仓，`get_position` 的 liquidation_price ATR 倍数会偏；记录首次观察到的场景判断是否需要 tool 层警示 |
| **`_TRIGGER_REASON_MAP` 对 algo 事件扩展**（假设 A 下）| 现有 `okx.py:35` `_TRIGGER_REASON_MAP` 只含 `stop/take_profit/market`，不含 `conditional/oco`。若 §3.1.3 假设 A 成立（OKX 推 algo state 变更事件，`info.ordType ∈ {conditional, oco}`），`_parse_fill_event:244` 会把 `trigger_reason` 映射到 `"unknown"`。首次 OCO 触发 log 出现 `raw_ordType=conditional/oco` → 扩 map：conditional 依据 `info.slTriggerPx` / `info.tpTriggerPx` 哪个非空推导 stop/take_profit；OCO 根据 trigger 价 vs 当前价推断哪一腿触发（SL 腿价低 / TP 腿价高）|
| ~~**FillEvent.order_id vs decision_logs.order_id 对齐**（假设 B 下）~~ | ~~若 §3.1.3 假设 B 成立（OKX 推底层 market/limit fill 事件，`order_data["id"]` 是底层 `ordId` 非 `algoId`），则 `FillEvent.order_id`（底层）和 `decision_logs.order_id`（agent `create_order` 拿到的 algoId）**不对齐** → `get_trade_journal` 按 decision_logs.order_id 关联 FillEvent 查不到。修复方向：`_parse_fill_event` 里若 `info.algoId` 非空，`FillEvent.order_id` 存 algoId 而非底层 ordId；或 journal 查询端做双 id fallback~~ | **已 fix（2026-04-25 PR #23 round-3 review II-1）**：`_parse_fill_event` 改 `order_id = info.get("algoId") or order_data["id"]`，假设 A/B 双 shape 下都正确（A: `info.algoId` 可能空 → fallback 到 `order_data["id"]` = algoId；B: `info.algoId` 非空 → 用 algoId 与 decision_logs 对齐）。观察期纯 confirm 行为符合预期，不再是修复 candidate |
| **Demo/live 账户 USDT/USDC auto-conversion 开仓后失配**（升级）| `okx_account_config.json` 实测 `mgnIsoMode=auto_transfers_ccy`；该模式**可能在开仓时自动把 USDT → settleCcy (USDC) 做保证金**。§5.4 首跑 assert 只检查**静态** `total.USDT > 0`；若开仓后 `total.USDT` 骤降 → `fetch_balance`（`okx.py:375-381` 读 USDT）给 `get_position` Risk exposure 错误数据。首次 demo 开仓后手动对比归档的 `okx_fetch_balance_idle.json`，若 USDT 骤变升级为独立议题"USDC 结算支持" |
| **`fetch_order` fallback 50002 误触**（fallback 字符串匹配）| `OKXExchange.fetch_order` 用 `_is_okx_error_code(e, "50002")` 做 plain-first fallback 判断（§2.5.4）。若日志里出现"fallback 路径触发但最终 algo fetch 也失败"的非预期模式（表明 50002 并非"algo id 走 plain 路径"语义），收紧 matcher 或改用 CCXT 结构化异常（若 CCXT 未来提供 `errCode` 属性）|
| **`fetch_open_orders` 三路 rate limit 命中**| `/trade/orders-algo-pending` 被 conditional + oco 调两次，算上 plain `/trade/orders-pending` 共 3 路 —— 初步估算见 §0.3（algo-pending ~1-2/min，plain-pending ~0.5-1/min，远低于 OKX 限额 20/2s per endpoint）。若观察期监控到 429 Too Many Requests 或 CCXT `RateLimitExceeded` 对这些 endpoint 累积，优化为"单路 `ordType=conditional,oco`"（若 OKX 未来支持多 ordType 一次调）或加 per-endpoint TTLCache |
| **CCXT `clientOrderId` / `stopPrice` 未映射** | 若归一化链路发现需要 clOrdId 做订单追踪或 stopPrice 做更严格校验，考虑 upstream PR / fallback 到 info 层读 |
| **`fetch_order` OCO 单条返回仅首腿**（P2-9，post-impl 新增 2026-04-25）| `fetch_order(algo_id)` 50002 fallback 到 algo endpoint（§2.5.4）后，若 id 对应真实 OCO，`_parse_order(data)` 返 `[stop, take_profit]` 两条 Order；但 `fetch_order` 以 `parsed[0]` 收尾（§2.5.4 当前 default 决定），silently 丢 `take_profit` 腿。Iter 2b 范围内 `create_order` 不支持 OCO（OCO 仅通过 OKX web / 底层 `private_post_trade_order_algo` 创建），此路径命中窄；但 `get_trade_journal` 按 decision_logs.order_id 查询若遇到 OCO id（observation-period 内可能出现），只显 stop 腿。若日志出现"OCO 触发后 journal 只显一腿详情"模式 → 改 `fetch_order` 返 `list[Order]` 或 journal 查询侧处理两腿展示 |

### 7.2 Scope 外候选

| 项 | 为什么留待 |
|---|-----------|
| Credentials 存储统一到 `config/*.json` profile | 本次 session 已否决，独立 PR 议题；观察期期间若出现切换疲劳再启动 |
| 支持 `trigger` / `move_order_stop` / `iceberg` / `twap` | YAGNI；观察期证据驱动 |
| 双向 posMode / multi-currency 账户支持 | 改动代价指数级；目前无需求 |
| `Order.algoClOrdId` 字段 | 当前路径不依赖；如将来做订单幂等性追踪再加 |
| **实盘首接入 double-gate 设计**（新增）| §2.1.4 `OKXExchange(sandbox=False)` 仅 log warning（不 hard-fail）。考虑实盘首次接入时加 `OKX_ALLOW_LIVE_ORDERS=true` 显式开关 / 或 approval gate default 严格化（`ApprovalConfig.enabled=True` + timeout 调短）。目前 `ApprovalConfig.enabled=True` 默认已提供 human-in-loop，本轮不加新 flag 避免功能重叠；留作实盘前体检独立议题 |
| **`fetch_closed_orders` 加 algo closed 查询**（新增）| 本轮 `fetch_closed_orders` 仅 signature flattening，不查 OKX algo closed endpoint（`/trade/orders-algo-history`）。OCO/conditional 触发后 `fetch_closed_orders` 列表查询拿不到它们 —— `get_trade_journal` 当前通过 `fetch_order(algoId)` 单条 fallback（§2.5.4）弥补；若未来有工具按"列出所有 closed orders"维度用，或 `get_trade_journal` 改为批量拉取，需扩 `fetch_closed_orders` 为第二路 gather（plain-history + algo-history），仿照 `fetch_open_orders` 三路合并模式 |
| AV counter UTC reset 验证 / DefiLlama drift 真实出现 | Iter 1/2 遗留观察期项；与 Iter 2b 正交 |

---

## 8. 参考

- Pre-work 归档（5 个）：`tests/fixtures/okx_fetch_open_orders_conditional_sl_{raw,unified}.json` / `okx_fetch_open_orders_oco_{raw,unified}.json` / `okx_account_config.json`
- Pre-work 脚本：`scripts/iter2b_sample_okx_algo_orders.py`
- 代码现状：`src/integrations/exchange/okx.py:86` `__init__` / `:327` `_parse_order` / `:118` `start` / `:412` `cancel_order`
- Iter 2 toolkit（`get_position` Risk exposure + Exit orders）：`docs/superpowers/specs/2026-04-20-toolkit-iter2-design.md`
- Iter 2 Round-4 mock fidelity lesson：memory `project_iter2_mock_fidelity_lesson`
- Iter 2b 原始 brainstorm：memory `project_iter2b_okx_algo_normalization`
- OKX v5 API: `/trade/orders-algo-pending`、`/trade/cancel-algos`、`/account/config`（posMode / acctLv 字段）
