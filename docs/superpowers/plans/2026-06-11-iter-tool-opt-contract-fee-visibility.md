# iter-tool-opt-contract-fee-visibility 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 agent 可见 contract_size（persona 注入 + get_position 数值实例化）并把 fee 前移进开仓决策（docstring 示例 + persona Layer 2 流程句），按 spec `docs/superpowers/specs/2026-06-11-iter-tool-opt-contract-fee-visibility-design.md`。

**Architecture:** 新增 `BaseExchange.init_market_meta()`（DB-cache 优先 / 缺 contractSize 即 raise / 幂等），build_services async 化后在 agent 创建前调用并注入 `RuntimeConfig.contract_size/base_ccy`；撮合循环启动位置不动（spec §3.6 回调窗口约束）。其余为 persona / get_position 渲染 / trader.py docstring 文案级改动。

**Tech Stack:** Python 3.12 / pydantic-ai / SQLAlchemy async / pytest（`asyncio_mode = "auto"`，async 测试无需装饰器）。

**前置事实（执行者必读）：**
- spec 全文先读一遍，特别是 §3.6 的三条硬约束。
- `tests/` 的 pytest 已配 `asyncio_mode = "auto"`（pyproject.toml:36）——把 sync 测试改 `async def` 即可 `await`，无需 `@pytest.mark.asyncio`（写了也不冲突）。
- sim 的 `_ccxt` 属性当前**不在 `__init__` 里**（只在 `start()` 赋值，`close()` 用 `hasattr` 探测）。
- `SessionModel` 的导入名：`from src.storage.models import Session as SessionModel`；DB 会话：`get_session(self._db_engine)`（simulated.py 内已有使用）。
- 既有测试中 `tests/test_get_position.py::test_fee_breakeven_section_does_not_render_fee_rate_number` 与本 iter 方向**有意冲突**（旧决定：费率数字只在 system prompt 单源）——本 iter 的"规则层（persona/docstring）+ 实例层（输出数值）"架构取代它，Task 6 反转重写该测试，不是误删。
- spec §5 test 6 写 "补 `contract_size=`/`symbol=` 断言"，其中 symbol 是 §3.6 的开放项（"symbol（或解析后 base_ccy）"）——本 plan **收敛为 `base_ccy=`**（RuntimeConfig 字段、注入点、AST 守卫三处一致），交叉对照 spec 时以此为准。

---

### Task 1: SimulatedExchange.init_market_meta()（DB-cache 优先 + fail-loud + 幂等）

**Files:**
- Modify: `src/integrations/exchange/simulated.py`（`__init__` ~:84 / `start()` ~:1119-1120 / `close()` ~:1218 / `_init_contract_size` ~:1287-1292 删除并替换）
- Test: `tests/test_init_market_meta.py`（新建）

- [ ] **Step 1: 写失败测试**

```python
"""init_market_meta: DB-cache 优先 / fetch 路径 fail-loud / 幂等（spec §3.6 硬约束 1）。"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import ExchangeConfig
from src.integrations.exchange.simulated import SimulatedExchange
from src.storage.models import Base, Session as SessionModel
from src.storage.database import get_session


def _make_exchange(db_engine=None) -> SimulatedExchange:
    config = ExchangeConfig(name="simulated", fee_rate=0.001)  # 注意：ExchangeConfig 无 precision 字段（config.py:14），勿传死参
    return SimulatedExchange(
        config=config, db_engine=db_engine,
        session_id="imm-test", symbol="BTC/USDT:USDT",
    )


class _FakeCcxt:
    def __init__(self, contract_size):
        self._cs = contract_size
        self.closed = False

    async def load_markets(self):
        return {}

    def market(self, symbol):
        return {"contractSize": self._cs}

    async def close(self):
        self.closed = True


async def test_idempotent_second_call_returns_cached_without_network():
    ex = _make_exchange()
    ex._ccxt = _FakeCcxt(0.01)
    first = await ex.init_market_meta()
    ex._ccxt = object()  # 毒丸：二次调用若再走网络路径 → load_markets AttributeError（不会真触网）
    second = await ex.init_market_meta()
    assert first == second == 0.01


async def test_fetch_path_raises_on_missing_contract_size():
    """contractSize 缺失必须 raise，不允许 or 1.0 静默兜底（spec §3.6 硬约束 1）。"""
    ex = _make_exchange()
    ex._ccxt = _FakeCcxt(None)
    with pytest.raises(RuntimeError, match="contractSize"):
        await ex.init_market_meta()
    assert ex._market_meta_ready is False


async def test_db_cache_hit_skips_network_entirely():
    """sessions.contract_size 命中 → 不创建 ccxt client、不走网络。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with get_session(engine) as session:
        session.add(SessionModel(
            id="imm-test", name="imm", symbol="BTC/USDT:USDT",
            initial_balance=10_000.0, status="active",
            exchange_type="simulated", timeframe="15m",
            scheduler_interval_min=15, approval_enabled=False,
            token_budget=1_000_000, contract_size=0.01,
        ))
        await session.commit()
    ex = _make_exchange(db_engine=engine)
    cs = await ex.init_market_meta()
    assert cs == 0.01
    assert getattr(ex, "_ccxt", None) is None  # 没碰网络客户端（_ccxt 属性 pre-start 不存在，勿直接访问）
    await engine.dispose()


async def test_init_market_meta_does_not_start_matching_loops():
    """spec §3.6 硬约束 3 前提：init_market_meta 不拉起撮合循环（回调窗口不存在）。"""
    ex = _make_exchange()
    ex._ccxt = _FakeCcxt(0.01)
    await ex.init_market_meta()
    assert ex._running is False
    assert getattr(ex, "_matching_task", None) is None


async def test_close_tolerates_pre_start_state():
    """__init__ 后立即 close 不抛（spec §3.6 硬约束 2 的清理路径前提）。"""
    ex = _make_exchange()
    await ex.close()  # _ccxt is None → 不应 await None.close()
```

注意：`SessionModel(...)` 必填列以 `src/storage/models.py:32` 的 `Session` 定义为准——若上面缺列（NOT NULL 报错），按模型定义补齐，不要给模型加默认值。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_init_market_meta.py -v`
Expected: FAIL（`init_market_meta` 不存在 / `_market_meta_ready` 不存在 / close 抛 AttributeError 等）

- [ ] **Step 3: 实现**

`src/integrations/exchange/simulated.py`：

(a) `__init__`（~:84，`self._contract_size: float = 1.0` 行后）：

```python
        self._contract_size: float = 1.0   # internal default; real value via init_market_meta()
        self._market_meta_ready: bool = False  # sentinel: distinguishes "uninitialized" from a legit 1.0
```

⚠️ **禁止在 `__init__` 加 `self._ccxt = None`**：simulated.py 有 7 处 `if not hasattr(self, "_ccxt"): raise RuntimeError("... call start() first")` pre-start 守卫（:152/:997/:1017/:1046/:1067/:1225/:1253）——属性一旦存在，守卫恒失效，`RuntimeError` 退化为 `AttributeError`，`tests/test_derivatives_data.py:164` 等现存测试必挂。`_ccxt` 的"可能不存在"由下方 `getattr(self, "_ccxt", None)` 处理。

(b) 新方法（放 `_persist_contract_size` 旁，~:1294 前）：

```python
    async def init_market_meta(self) -> float:
        """Resolve and cache contractSize for the bound symbol. Idempotent.

        Resolution order: in-memory cache → sessions.contract_size (DB, no
        network) → ccxt load_markets + market lookup (persists to DB).
        Raises RuntimeError when contractSize cannot be resolved — never
        silently falls back to 1.0 (spec §3.6 hard constraint 1).
        """
        if self._market_meta_ready:
            return self._contract_size
        if self._db_engine is not None:
            from sqlalchemy import select
            from src.storage.database import get_session
            from src.storage.models import Session as SessionModel
            async with get_session(self._db_engine) as session:
                result = await session.execute(
                    select(SessionModel.contract_size).where(SessionModel.id == self._session_id)
                )
                cached = result.scalar_one_or_none()
            if cached is not None:
                self._contract_size = float(cached)
                self._market_meta_ready = True
                return self._contract_size
        if getattr(self, "_ccxt", None) is None:
            import ccxt.pro as ccxtpro
            self._ccxt = ccxtpro.okx()
        await self._load_markets_with_retry()
        raw = self._ccxt.market(self._symbol).get("contractSize")
        if raw is None:
            raise RuntimeError(
                f"contractSize missing for {self._symbol} — cannot initialize market metadata"
            )
        self._contract_size = float(raw)
        self._market_meta_ready = True
        if self._db_engine:
            await self._persist_contract_size()
        return self._contract_size
```

⚠️ **import 事实**：simulated.py 顶层（:1-32）**没有** `select` / `get_session` / `ccxtpro`——它们在该文件全部是函数内局部 import（:704/738/822/1097 等）。上面代码块已自带三处局部 import，照写。特别注意 `ccxt.pro as ccxtpro` 一行：**Task 1 的单测全部绕开网络分支**（FakeCcxt 预置 / DB cache 命中），该 import 遗漏不会被测试暴露、会潜伏到生产"新 session + cache miss"才 NameError——实现后人工检查网络分支三行。

(c) `start()`（~:1119-1120）：

```python
改前：
        self._ccxt = ccxtpro.okx()
        await self._init_contract_size()
改后：
        await self.init_market_meta()   # idempotent — no-op when build_services already ran it
        if getattr(self, "_ccxt", None) is None:
            self._ccxt = ccxtpro.okx()  # DB-cache path skips client creation; ticker seeding needs it
            await self._load_markets_with_retry()  # 还原旧 start() 的显式 load_markets 保证（resume/DB-cache 路径下 init_market_meta 未触网）
```

（`ccxtpro` 在 start() 内 :1097 已有局部 import，此处沿用其作用域；DB-cache 路径下 `_ccxt` 属性可能尚不存在，必须用 `getattr` 而非 `self._ccxt is None`。补 `_load_markets_with_retry` 的原因：旧流程 start() 恒经 `_init_contract_size → load_markets`，`_seed_mark_price` 依赖 markets 已加载才能解析 instId——不补则 resume 路径退化为依赖 fetch_ticker 的 lazy-load，显式不变量丢失。）

(d) 删除 `_init_contract_size`（~:1287-1292）。`_load_markets_with_retry` / `_persist_contract_size` 保留。

(f) 更新 `_seed_mark_price` docstring 悬空引用（:1175）：

```
改前：MUST be called after _init_contract_size() (load_markets) so ccxt can
      resolve instId. Mirrors seed_ticker retry semantics.
改后：MUST be called after markets are loaded (init_market_meta network path,
      or the explicit _load_markets_with_retry in start()'s cache path) so
      ccxt can resolve instId. Mirrors seed_ticker retry semantics.
```

(e) `close()`（:1218）**不动**——`hasattr` 语义在"不往 `__init__` 塞 `_ccxt`"的前提下本就正确（属性存在 ⇔ client 已创建）。

- [ ] **Step 3b: 迁移 cs 缓存 / 跨层同源测试（删除 `_init_contract_size` 的直接受害者）**

`tests/test_simulated_cs_cache.py`（:54/:85）与 `tests/test_cs_cross_layer.py`（:29/:51）共 4 处直接调用 `_init_contract_size()`——它们守卫的"cs 缓存 / 跨层同源"不变量正是本 iter 迁移的职责，**迁移到 `init_market_meta()`，不是删测试**：

1. 4 处调用 `await ex._init_contract_size()` → `await ex.init_market_meta()`
2. ⚠️ `init_market_meta` 有幂等短路（`_market_meta_ready`）与 DB-cache 优先：
   - 若测试在**同一实例**上二次调用以验证重读（核对 `:51` 上下文），二次调用前需 `ex._market_meta_ready = False` 复位，或改用新实例
   - 若测试的 engine 中 sessions 行已带 contract_size，DB-cache 会先命中、不走 mocked `_ccxt`——按各测试意图选择：测缓存语义 → 保持；测 ccxt 解析路径 → 确保 sessions.contract_size 为 NULL 或 db_engine=None
3. 断言的不变量（缓存值 / persist / 执行层与 market-data 层同源）全部保留，不放宽

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_init_market_meta.py tests/test_simulated_exchange.py tests/test_simulated_cs_cache.py tests/test_cs_cross_layer.py -v`
Expected: 全 PASS（后三个是邻接回归面）

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/simulated.py tests/test_init_market_meta.py tests/test_simulated_cs_cache.py tests/test_cs_cross_layer.py
git commit -m "feat(exchange): SimulatedExchange.init_market_meta — DB-cache 优先 + fail-loud + 幂等"
```

---

### Task 2: OKXExchange.init_market_meta + BaseExchange 接口声明

**Files:**
- Modify: `src/integrations/exchange/okx.py`（`_preload_markets` ~:188 附近加方法）
- Modify: `src/integrations/exchange/base.py`（`BaseExchange` 类内加 abstractmethod）
- Test: `tests/test_init_market_meta.py`（追加）

- [ ] **Step 1: 写失败测试（追加到 tests/test_init_market_meta.py）**

```python
def _make_okx() -> "OKXExchange":
    from unittest.mock import patch
    from src.integrations.exchange.okx import OKXExchange
    with patch("src.integrations.exchange.okx.ccxt"):  # 既有 idiom（test_okx_algo_normalization.py:8），构造期不碰真实 ccxt
        return OKXExchange(api_key="k", secret="s", password="p",
                           symbol="BTC/USDT:USDT", sandbox=True)


async def test_okx_init_market_meta_returns_contract_size():
    ex = _make_okx()
    ex._preload_markets = AsyncMock()
    ex._client = MagicMock()
    ex._client.markets = {"BTC/USDT:USDT": {"contractSize": 0.01}}
    assert await ex.init_market_meta() == 0.01


async def test_okx_init_market_meta_raises_on_missing_market():
    """覆盖 okx.py 惰性 get_contract_size 的 1.0 兜底盲区（spec §3.6 硬约束 1 细则）。"""
    ex = _make_okx()
    ex._preload_markets = AsyncMock()
    ex._client = MagicMock()
    ex._client.markets = {}
    with pytest.raises(RuntimeError, match="contractSize"):
        await ex.init_market_meta()
```

（patch 为无条件采用——既有 OKX 测试一律如此，不留"出问题再调"的条件分支。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_init_market_meta.py -k okx -v`
Expected: FAIL with AttributeError（init_market_meta 不存在）

- [ ] **Step 3: 实现**

`src/integrations/exchange/okx.py`（`_preload_markets` 后）：

```python
    async def init_market_meta(self) -> float:
        """Resolve contractSize for the bound symbol via load_markets.

        Raises RuntimeError when the market or contractSize is unavailable —
        no silent 1.0 fallback (spec §3.6 hard constraint 1; the lazy
        get_contract_size() fallback below is NOT used on this init path —
        its own removal is an OKX-runtime change deferred to Tier 3).
        """
        await self._preload_markets()
        market = self._client.markets.get(self._symbol)
        cs = market.get("contractSize") if market else None
        if cs is None:
            raise RuntimeError(
                f"contractSize unavailable for {self._symbol} on OKX — cannot initialize market metadata"
            )
        return float(cs)
```

`src/integrations/exchange/base.py`（`BaseExchange` 类内，与 `fetch_ticker` 等同级）：

```python
    async def init_market_meta(self) -> float:
        """Resolve and cache market metadata (contract size) for the bound
        symbol. Idempotent where applicable. MUST raise on failure — never
        return a silent 1.0 fallback.

        Concrete default (deliberately NOT @abstractmethod): tests/ has 10+
        concrete BaseExchange doubles (DummyExchange / _TestExchange) that
        would fail instantiation under a new abstractmethod. Default stays
        loud if ever invoked on an exchange without a real implementation.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement init_market_meta"
        )
```

⚠️ **有意偏离 spec §3.6 的"接口声明"字面**：不用 `@abstractmethod`——tests/ 中 `DummyExchange`（test_exchange.py :236/:275/:315）、`_Stub`（:354）、`_TestExchange`（test_price_level_alert.py :13、test_tool_enhancement.py :42 等 ×6）共 10+ 个具体替身会因新增抽象方法集体 `TypeError`。具体默认实现 + `NotImplementedError` 保留 fail-loud 精神，sim/okx override 满足"两端实现"的实质要求。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_init_market_meta.py tests/test_okx_algo_normalization.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/integrations/exchange/okx.py src/integrations/exchange/base.py tests/test_init_market_meta.py
git commit -m "feat(exchange): OKX init_market_meta + BaseExchange 接口声明（两端 fail-loud）"
```

---

### Task 3: RuntimeConfig 新字段 + persona Layer 1/Layer 2

**Files:**
- Modify: `src/agent/persona.py`（常量区 ~:24 / RuntimeConfig ~:60 / `_build_layer1` ~:100-101 / `_build_layer2` ~:146-147）
- Test: `tests/test_persona.py`（追加 + 既有断言检查）

- [ ] **Step 1: 写失败测试（追加到 tests/test_persona.py，import 沿用文件头部既有）**

```python
def test_layer1_contract_size_line_renders_runtime_values():
    """Layer 1 渲染 contract size 行 + notional 换算规则（spec §3.1）。"""
    runtime = RuntimeConfig(taker_fee_rate=0.001, contract_size=0.01, base_ccy="BTC")
    text = generate_system_prompt(PersonaConfig(), runtime)
    assert ("Contract size: 1 contract = 0.01 BTC. "
            "Notional (USDT) = contracts × contract_size × price.") in text


def test_layer1_contract_size_line_follows_fee_lines():
    """contract size 行紧随 Fee 两行之后（Market Context 段内聚）。"""
    runtime = RuntimeConfig(taker_fee_rate=0.001, contract_size=0.01, base_ccy="BTC")
    text = generate_system_prompt(PersonaConfig(), runtime)
    fee_idx = text.index("Fee: taker")
    cs_idx = text.index("Contract size: 1 contract")
    assert 0 < cs_idx - fee_idx < 250


def test_layer2_risk_reward_breakeven_question():
    """Layer 2 Risk-Reward 维度含 breakeven 疑问句（spec §3.4）。"""
    text = generate_system_prompt(PersonaConfig())
    assert ("Does the expected move clear the round-trip fee cost — "
            "where is breakeven (entry ± 2 × fee_rate) relative to your stop and target?") in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_persona.py -k "contract_size_line or breakeven_question" -v`
Expected: FAIL（RuntimeConfig 无 contract_size 字段 → TypeError）

- [ ] **Step 3: 实现**

`src/agent/persona.py`：

(a) 常量区（`DEFAULT_TAKER_FEE_RATE` 后）：

```python
DEFAULT_CONTRACT_SIZE = 0.01
"""OKX BTC/USDT:USDT perp contractSize (ctVal) in base currency. Test-only
default — production paths MUST inject via build_services
(exchange.init_market_meta()), mirroring DEFAULT_TAKER_FEE_RATE discipline."""
```

(b) RuntimeConfig（`taker_fee_rate` 字段后）：

```python
    contract_size: float = DEFAULT_CONTRACT_SIZE
    """Base-currency amount per contract. Injected from
    exchange.init_market_meta() via build_services. Default is for tests /
    temp call sites only — production paths MUST set explicitly."""

    base_ccy: str = "BTC"
    """Base currency label for the contract-size line (parsed from the
    session symbol via extract_base_currency in build_services). Default is
    for tests only."""
```

(c) `_build_layer1` f-string（Fee 两行后插一行）：

```
Fee: taker {fee_pct:.3f}% per side (set at session start).
Round-trip cost on a position = entry_fee + exit_fee ≈ 2 × fee_rate × notional.
Contract size: 1 contract = {runtime.contract_size:g} {runtime.base_ccy}. Notional (USDT) = contracts × contract_size × price.
```

(d) `_build_layer2` Risk-Reward 段（第四个问句后同段追加）：

```
**Risk-Reward**
What is the risk-to-reward ratio of this potential trade? Where is the logical stop loss? Is the potential reward worth the risk? Would a better entry improve the ratio? Does the expected move clear the round-trip fee cost — where is breakeven (entry ± 2 × fee_rate) relative to your stop and target?
```

- [ ] **Step 4: 跑 persona 全量测试确认通过**

Run: `pytest tests/test_persona.py -v`
Expected: 全 PASS（既有 39 处调用点因默认值不 raise；若有对 Layer 1 全文/行数做 exact 断言的测试失败，按新增行更新断言——只允许适配，不允许删断言）

- [ ] **Step 5: Commit**

```bash
git add src/agent/persona.py tests/test_persona.py
git commit -m "feat(persona): Layer 1 contract size 行 + Layer 2 breakeven 疑问句（spec §3.1/§3.4）"
```

---

### Task 4: build_services async 化 + 注入 + 既有测试位点适配

**Files:**
- Modify: `src/cli/app.py`（`build_services` :921 起 / caller :1123 / Phase 5b :1128-1131）
- Modify: `tests/conftest.py`（加 stub fixture）
- Modify: `tests/test_wizard.py` / `tests/test_n3_wiring.py` / `tests/test_okx_algo_normalization.py`（call sites async 化 + stub）
- Test: `tests/test_drift_p4_capture_paths.py`（扩展既有 AST 守卫，**不新建**）

- [ ] **Step 1: 扩展 AST 守卫（先写失败断言）**

`tests/test_drift_p4_capture_paths.py::test_p4_runtime_config_matches_build_services_fee_rate`（:239 起）——在该测试既有断言后追加（沿用其 `found_sites` 结构）：

```python
    # iter-tool-opt-contract-fee-visibility: 两处构造点必须同时注入新字段
    required = {"taker_fee_rate", "contract_size", "base_ccy"}
    for name, calls in found_sites.items():
        for call in calls:
            kwarg_names = {kw.arg for kw in call.keywords}
            missing = required - kwarg_names
            assert not missing, (
                f"RuntimeConfig in {name} missing kwargs {missing} — "
                f"persona would render test-only defaults in production"
            )
```

Run: `pytest tests/test_drift_p4_capture_paths.py -v` → Expected: FAIL（missing contract_size/base_ccy）

- [ ] **Step 2: conftest 加 stub fixture**

`tests/conftest.py` 追加：

```python
@pytest.fixture
def stub_market_meta(monkeypatch):
    """绕开 init_market_meta 的 DB/网络路径——build_services 单测专用。"""
    from src.integrations.exchange.simulated import SimulatedExchange
    from src.integrations.exchange.okx import OKXExchange

    async def _fake(self):
        return 0.01

    monkeypatch.setattr(SimulatedExchange, "init_market_meta", _fake)
    monkeypatch.setattr(OKXExchange, "init_market_meta", _fake)
    return 0.01
```

- [ ] **Step 3: 实现 build_services 改动**

`src/cli/app.py`：

(a) `def build_services(` → `async def build_services(`。

(b) exchange 创建分支结束后（`sc.print(f"Exchange: okx — {account_label}")` 之后、`market_data = ...` 之前）：

```python
    # iter-tool-opt-contract-fee-visibility: 市场元数据前置（spec §3.6）。
    # init_market_meta 失败发生在 run() 的 try/finally 之前 → 此处自行清理（硬约束 2）。
    try:
        contract_size = await exchange.init_market_meta()
    except Exception:
        await exchange.close()
        raise
```

(c) RuntimeConfig 构造（R2-5 注释处）：

```python
    from src.integrations.news.models import extract_base_currency

    max_wake = _compute_max_wake(result.scheduler_interval_min)
    runtime_config = RuntimeConfig(
        wake_max_minutes=max_wake,
        taker_fee_rate=result.fee_rate,
        contract_size=contract_size,
        base_ccy=extract_base_currency(result.symbol),
    )
```

（`extract_base_currency` 复用 news/models.py:64 的现成实现——单源，per spec §3.6 配套决定；函数级 import 与文件内既有懒加载风格一致。）

(d) caller（:1123）：

```python
    exchange, deps, agent, budget, stats = await build_services(
        result, engine, session_id, sc, settings,
    )
```

(e) Phase 5b（:1128-1131）：

```python
    runtime_config_for_capture = RuntimeConfig(
        wake_max_minutes=_compute_max_wake(result.scheduler_interval_min),
        taker_fee_rate=result.fee_rate,
        contract_size=await exchange.init_market_meta(),  # 幂等返回已校验值；不走 get_contract_size（其含 1.0 静默兜底，与硬约束 1 口径相左）
        base_ccy=extract_base_currency(result.symbol),
    )
```

（`extract_base_currency` 在 run() 作用域需同样可用——模块顶部或函数内 import 一次，两处共用。）

- [ ] **Step 4: 适配既有 build_services 测试位点**

对每个文件执行 `grep -n "build_services(" <file>` 找全部调用点（行号可能漂移，以 grep 为准），统一施加同一变换：

1. 测试函数 `def` → `async def`（asyncio_mode=auto 自动收集）
2. `build_services(...)` → `await build_services(...)`
3. 测试函数签名加 `stub_market_meta` fixture 参数

文件清单与已知行号（2026-06-11 时点）：
- `tests/test_wizard.py`：457 / 501 / 527
- `tests/test_n3_wiring.py`：88 / 107 / 124 / 137 / 154（及该文件其余调用点，以 grep 为准）
- `tests/test_okx_algo_normalization.py`：69（OKX 分支——stub fixture 已同时 patch OKXExchange）
- `tests/test_drift_p4_capture_paths.py`：**该文件不只是 AST 守卫，还有两处运行时调用**，需特殊处理：
  - `:202` `test_build_services_raises_on_none_fee_rate`：改 `async def` + `with pytest.raises(ValueError, match="fee_rate"): await build_services(...)`（ValueError 在 await 时抛出，语义不变；不加 stub fixture 也可——fee_rate 校验先于 init_market_meta）
  - `:226` `test_build_services_drift_guard_runtime_vs_deps_fee_rate`：改 `async def` + `await`；**`stub_market_meta` 对它无效**（该测试用 `_build_services_patches()` 整类 patch `SimulatedExchange`，monkeypatch 真实类不影响 MagicMock 实例）——在 `MockSim.return_value = MagicMock()` 后显式补：

    ```python
        MockSim.return_value.init_market_meta = AsyncMock(return_value=0.01)
        MockSim.return_value.close = AsyncMock()
    ```

    （`AsyncMock` 若未导入：`from unittest.mock import AsyncMock` 并入文件头部既有 mock import 行。）

变换示例（test_n3_wiring.py:88 处，其余位点同型）：

```python
改前：
def test_news_service_wired(...):
    exchange, deps, agent, budget, _stats = build_services(
        result, engine, session_id, sc, settings)
改后：
async def test_news_service_wired(..., stub_market_meta):
    exchange, deps, agent, budget, _stats = await build_services(
        result, engine, session_id, sc, settings)
```

- [ ] **Step 5: 补硬约束 2 的清理测试（追加到 tests/test_wizard.py，复用其 :457 测试的 result/engine/sc/settings 构造）**

```python
async def test_build_services_closes_exchange_on_market_meta_failure(monkeypatch):
    """init_market_meta 失败发生在 run() try/finally 之前——build_services 必须自行清理（spec §3.6 硬约束 2）。"""
    from src.integrations.exchange.simulated import SimulatedExchange

    closed = []

    async def _boom(self):
        raise RuntimeError("contractSize missing")

    async def _close(self):
        closed.append(True)

    monkeypatch.setattr(SimulatedExchange, "init_market_meta", _boom)
    monkeypatch.setattr(SimulatedExchange, "close", _close)
    with pytest.raises(RuntimeError, match="contractSize"):
        await build_services(result, engine, session_id, sc, settings)  # 入参按 :457 模式构造
    assert closed, "exchange.close() not called on init_market_meta failure"
```

- [ ] **Step 6: 跑相关测试确认通过**

Run: `pytest tests/test_drift_p4_capture_paths.py tests/test_wizard.py tests/test_n3_wiring.py tests/test_okx_algo_normalization.py -v`
Expected: 全 PASS

- [ ] **Step 7: Commit**

```bash
git add src/cli/app.py tests/conftest.py tests/test_wizard.py tests/test_n3_wiring.py tests/test_okx_algo_normalization.py tests/test_drift_p4_capture_paths.py
git commit -m "feat(cli): build_services async 化 — init_market_meta 前置 + RuntimeConfig contract_size/base_ccy 注入"
```

---

### Task 5: 回调时序回归测试（spec §5 test 7）

**Files:**
- Test: `tests/test_init_market_meta.py`（追加）

- [ ] **Step 1: 写测试（守卫 §3.6 硬约束 3 的不变量）**

```python
async def test_fill_dispatch_reaches_callback_registered_before_start():
    """回调注册先于循环启动的相对序下，fill 事件必达回调（spec §3.6 硬约束 3）。

    不真启动撮合循环（需网络）——直接驱动 _dispatch_fill_event 验证
    '注册后派发必达'；窗口不存在性由 test_init_market_meta_does_not_start_matching_loops 守卫。
    """
    from src.integrations.exchange.base import FillEvent

    ex = _make_exchange()
    received = []

    async def _cb(fill):
        received.append(fill)

    ex.on_fill(_cb)
    fill = FillEvent(
        order_id="f1", symbol="BTC/USDT:USDT", side="long", position_side="long",
        amount=1.0, fill_price=60_000.0, fee=0.6, pnl=0.0,
        trigger_reason="manual", timestamp=1_715_040_000_000, is_full_close=False,
    )
    await ex._dispatch_fill_event(fill)
    assert received and received[0].order_id == "f1"
```

注意：`FillEvent` 必填字段含 position_side / trigger_reason / pnl / timestamp / is_full_close（均无默认值）——上例已按此构造，仍以 `src/integrations/exchange/base.py` 实际定义为准（先 `grep -n "class FillEvent" -A 15` 核对类型/取值）；若已有等价测试（grep `_dispatch_fill_event` tests/），改为在该测试处加引用注释并跳过本步新建。

- [ ] **Step 1b: 回调序结构守卫（追加同文件）**

spec §3.6 硬约束 3 的完整不变量（"resume session 循环启动后首个 tick 的 fill/alert 必达"）依赖真实撮合循环（需网络），unit 层拆为上面两个测试 + 此结构守卫；**此前该序仅靠 code structure 保证、无任何 guard**，本测试拦住日后对 run() 的重排：

```python
def test_app_registers_callbacks_before_exchange_start():
    """结构守卫：run() 内 on_fill/on_alert 注册必须先于 await exchange.start()（spec §3.6 硬约束 3）。"""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "src" / "cli" / "app.py").read_text()
    run_body = src[src.index("async def run("):]
    start_idx = run_body.index("await exchange.start()")
    assert run_body.index("exchange.on_fill(") < start_idx
    assert run_body.index("exchange.on_alert(") < start_idx
```

（若 run() 的实际函数名/签名不同，以 `grep -n "def run" src/cli/app.py` 为准调整切片锚点。）

- [ ] **Step 2: 跑测试确认通过 + Commit**

Run: `pytest tests/test_init_market_meta.py -v` → Expected: 全 PASS

```bash
git add tests/test_init_market_meta.py
git commit -m "test(exchange): 回调时序回归 — 注册先于循环启动时 fill 必达（spec §3.6 硬约束 3）"
```

---

### Task 6: get_position 渲染数值实例化

**Files:**
- Modify: `src/agent/tools_perception.py`（:348-395）
- Test: `tests/test_get_position.py`（更新断言 + 反转 1 个旧 drift guard）

- [ ] **Step 1: 更新测试断言（先改测试 → 红）**

`tests/test_get_position.py`：

```python
# test_renders_fee_breakeven_section_long（:88 一带）：
改前：assert "Entry fee paid: ~-40.00 USDT (= entry × contracts × contract_size × rate)" in out
改后：assert "Entry fee paid: ~-40.00 USDT (= notional 40,000.00 × 0.100%)" in out
改前：assert "= 80,000.00 × (1 + 2 × fee_rate) [long round-trip taker]" in out
改后：assert "= 80,000.00 × (1 + 2 × 0.100%) [long round-trip taker]" in out

# test_renders_fee_breakeven_section_short（:109 一带）：
改前：assert "= 80,000.00 × (1 − 2 × fee_rate) [short round-trip taker]" in out
改后：assert "= 80,000.00 × (1 − 2 × 0.100%) [short round-trip taker]" in out
# （U+2212 保持）

# test_fee_breakeven_section_uses_contract_size_factor（:181 一带）：
# entry=80000, contracts=10, cs=0.01 → notional=8,000.00, fee=8.00
追加：assert "Entry fee paid: ~-8.00 USDT (= notional 8,000.00 × 0.100%)" in out

# test_fee_breakeven_section_does_not_render_fee_rate_number —— 反转重写：
# 旧设计（费率数字只在 system prompt 单源）被本 iter "规则层+实例层" 架构有意取代（spec §3.2）。
改名为 test_fee_breakeven_section_renders_session_fee_rate，断言：
    assert "0.100%" in out  # 数值实例层：输出渲染 session 费率
```

并更新 Notional 行断言（`grep -rn "Notional value:" tests/` 找到全部位点）。新格式（cs=1.0、contracts=0.5、entry=80,000 的 fixture 下）：

```python
assert "Notional value: 40,000.00 USDT = 0.5 contracts × 1 BTC × entry 80,000.00" in out
```

（各位点数值按其 fixture 换算：`notional = contracts × cs × entry`；equity 部分若原断言覆盖，按 `{:,.2f}` 加千分位。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_get_position.py -v`
Expected: 上述断言 FAIL（输出仍是符号公式）

- [ ] **Step 3: 实现渲染改动**

`src/agent/tools_perception.py`（:348-395 区域）：

```python
    # === Fee & Breakeven ===
    # （保留现有注释块）notional (at entry) 同时供本节与 Risk Exposure 使用；
    # contracts × contract_size × price 分解只在 Notional 行渲染一次（规则权威源在 persona Layer 1）。
    notional = p.contracts * contract_size * p.entry_price
    entry_fee = notional * deps.fee_rate
    fee_pct_str = f"{deps.fee_rate * 100:.3f}%"
    if p.side == "long":
        breakeven = p.entry_price * (1 + 2 * deps.fee_rate)
        sign_str = "+"
        side_label = "long"
    else:
        breakeven = p.entry_price * (1 - 2 * deps.fee_rate)
        sign_str = "−"  # Unicode minus U+2212, matches test assertion exactly
        side_label = "short"

    fb_lines = ["=== Fee & Breakeven ==="]
    fb_lines.append(
        f"Entry fee paid: ~-{entry_fee:.2f} USDT "
        f"(= notional {notional:,.2f} × {fee_pct_str})"
    )
    # Breakeven 行（:369-379）保持不变；公式行改为：
    fb_lines.append(
        f"  = {p.entry_price:,.2f} × (1 {sign_str} 2 × {fee_pct_str}) "
        f"[{side_label} round-trip taker]"
    )
```

Risk Exposure 段（:386-395）：删除重复的 `notional = p.contracts * p.entry_price * contract_size`（复用上方变量），Notional 行改为：

```python
    base_ccy = extract_base_currency(symbol)
    risk_lines.append(
        f"Notional value: {notional:,.2f} USDT = {p.contracts:g} contracts × "
        f"{contract_size:g} {base_ccy} × entry {p.entry_price:,.2f} "
        f"({exp_pct:.1f}% of equity {equity:,.2f})"
    )
```

文件顶部加 import（单源复用，per spec §3.6）：

```python
from src.integrations.news.models import extract_base_currency
```

顺手清理：`tools_perception.py:899` 已有同函数的局部 import（另一工具内）——顶层 import 落地后删除该局部 import 行，统一走顶层。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_get_position.py tests/test_tool_enhancement.py -v`
Expected: 全 PASS（test_tool_enhancement 若有 Notional/Fee 行断言一并按新格式适配）

- [ ] **Step 5: Commit**

```bash
git add src/agent/tools_perception.py tests/test_get_position.py tests/test_tool_enhancement.py
git commit -m "feat(perception): get_position 公式数值实例化 — notional × rate 因式 + 分解单点于 Notional 行"
```

---

### Task 7: trader.py docstring（改动 3 + 收尾 a/b）+ drift guards

**Files:**
- Modify: `src/agent/trader.py`（get_position :152-154 / open_position :504-510 / place_limit_order :766）
- Test: `tests/test_trader_agent.py`（追加 drift guards，沿用文件内 `tool_def.description` 既有模式）

- [ ] **Step 1: 写失败 drift guards（追加到 tests/test_trader_agent.py，agent 构造沿用文件内既有 helper）**

```python
def test_open_position_docstring_inline_fee_example():
    """spec §3.3-1：fee 事实数值化 inline 示例必达 LLM 通道（tool_def.description）。"""
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    desc = agent._function_toolset.tools["open_position"].tool_def.description
    assert "Entry fee: -7.50 USDT (notional 7,498.52)" in desc
    assert "rate is session-specific" in desc


def test_position_pct_margin_semantics_both_tools():
    """spec §3.3-2：position_pct margin 语义两工具同款（log 19872/37672 消歧）。"""
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    for tool_name in ("open_position", "place_limit_order"):
        desc = agent._function_toolset.tools[tool_name].tool_def.description
        assert "use as margin" in desc, tool_name
        assert "notional = margin × leverage" in desc, tool_name


def test_get_position_docstring_fee_formula_unified():
    """spec §3.5 收尾 a：fee 公式统一 notional × fee_rate，旧四因子式不得残留。"""
    agent = create_trader_agent(model="test", persona_config=PersonaConfig())
    desc = agent._function_toolset.tools["get_position"].tool_def.description
    assert "notional × fee_rate" in desc
    assert "entry × contracts × contract_size × rate" not in desc
```

（`create_trader_agent(model="test", persona_config=PersonaConfig())` 是该文件既有 idiom；import 沿用文件头部既有行，`tools` dict 访问方式以 :217 一带既有测试为准。）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_trader_agent.py -k "inline_fee_example or margin_semantics or formula_unified" -v`
Expected: 3 个 FAIL

- [ ] **Step 3: 实现 docstring 改动**

`src/agent/trader.py`：

(a) open_position（:504 后）：

```python
        Entry incurs taker fee = notional × fee_rate; the return reports the
        actual fee. For example, a fill of 12.02 contracts @ 62,383.70 returns
        'Entry fee: -7.50 USDT (notional 7,498.52)' (rate is session-specific;
        this example uses 0.1%).
```

（inline 散文，禁用块状 `Example:` section header——griffe 会剥离，per memory `griffe_example_section_stripped`。）

(b) open_position 与 place_limit_order 的 Args（:508 / :766 同款）：

```
改前：position_pct: percent of free balance to allocate (0-100).
改后：position_pct: percent of free balance to use as margin (0-100);
      resulting notional = margin × leverage.
```

(c) get_position（:152-154）：

```python
改前：
        Output also includes Fee & Breakeven section: entry_fee paid (= entry × contracts × contract_size × rate)
        and breakeven price = entry × (1 ± 2 × fee_rate) — the fill price at which the
        position is exactly flat on a taker round-trip.
改后：
        Output also includes Fee & Breakeven section: entry_fee paid
        (= notional × fee_rate, rendered with the session's actual values)
        and breakeven price = entry × (1 ± 2 × fee_rate) — the fill price at
        which the position is exactly flat on a taker round-trip.
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_trader_agent.py -v`
Expected: 全 PASS（若既有 docstring 断言因措辞改动失败，按新文案适配）

- [ ] **Step 5: Commit**

```bash
git add src/agent/trader.py tests/test_trader_agent.py
git commit -m "feat(trader): open/limit docstring fee 示例 + position_pct margin 语义 + get_position 公式统一"
```

---

### Task 8: 全量回归 + 收尾

- [ ] **Step 1: 全量 pytest**

Run: `pytest -q`
Expected: 全 PASS（基线 1756+；新增 ~12）。失败逐个修：典型残留是其它测试对 Layer 1 / get_position 输出的字符串断言——按新格式适配，禁止反向放宽实现。

- [ ] **Step 2: spec 验证口径自查**

对照 spec §5 测试表 1-8 逐项打勾（1↔Task 3 / 2↔Task 1+4 / 3↔Task 6 / 4↔Task 7 / 5↔Task 3 / 6↔Task 4 / 7↔Task 5 / 8↔本 Task）。

- [ ] **Step 3: Commit（如有收尾改动）+ 汇报**

```bash
git add -A && git commit -m "test: 全量回归收尾 — iter-tool-opt-contract-fee-visibility"
```

汇报时附：改动文件清单、测试增量、spec §6 的 W4/sim #18 验证口径提醒（baseline：contract_size 反推 ≥4 段 → 目标 0；开仓前 fee 提及 1/11 → gate ≥50%/20-50%/<20%）。
