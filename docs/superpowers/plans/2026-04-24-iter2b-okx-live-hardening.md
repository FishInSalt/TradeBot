# Iter 2b — OKX Live Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 OKX Exchange 对 algo 订单（conditional SL / OCO）的读/写路径断裂，加账户模式 fail-fast 护栏 + sandbox 配置化，让 agent 在 OKX demo 实盘环境下能正确下 / 查 / 撤 SL/TP 单。

**Architecture:** 读路径在 `OKXExchange._parse_order` 归一化 + `fetch_open_orders` 三路 `asyncio.gather`；写路径 `create_order` 路由 + 手动构造 algo Order 绕开 `_parse_order`；`cancel_order` / `fetch_order` / `set_leverage` 加 algo 适配 + `BaseExchange.cancel_order` 抽象 signature 扩展；`start()` 加 posMode / acctLv / ws_client sandbox 三重校验；配置层 `OKX_SANDBOX` 分流 demo / live credentials。

**Tech Stack:** Python 3.13 async / ccxt.async_support.okx / pydantic-ai / pytest-asyncio / OKX v5 REST API

**Spec reference:** `docs/superpowers/specs/2026-04-24-iter2b-okx-live-hardening.md`（1075 行，6 scope items）

---

## 0. 上下文与依赖拓扑

### 0.1 预置条件

- 分支 `iter2b/okx-live-hardening` 已存在，2 个 commits（`3f9ac83` Pre-work，`6f53602` spec）。当前 HEAD 即本 Plan 的起点。
- Pre-work fixtures 已归档在 `tests/fixtures/okx_*.json`（5 个），**本 Plan 的所有 mock 测试必须 load unified fixture，不手写 dict**。
- `tests/fixtures/okx_fetch_open_orders_conditional_sl_unified.json` 含 `stopLossPrice=54405.3`、`takeProfitPrice=null`；`okx_fetch_open_orders_oco_unified.json` 含 `stopLossPrice=54405.3` + `takeProfitPrice=101038.3`，都嵌套 `info` 子字典（OKX raw 字段）。
- 730 测试 baseline 通过，本轮完成后预期 778 passed + 1 skipped（新增 48 passed + 1 advisory skip + 修改若干条现有 signature/assertion）。注：spec §6 acceptance 原写"约 762"基于起草期估算；plan 展开后实际 +48 更精确，spec 数字后续通过 Task 10.2 的 patch 草稿由 user 审阅后校准（不阻塞本 PR）。

### 0.2 任务依赖拓扑

```
Task 1  (配置 + sandbox 基础设施)
   ├── Task 2 (Order.is_algo + _parse_order 归一化 + Sim sig sync) 
   │       ├── Task 3 (fetch_open_orders 三路 gather)
   │       │      └── Task 4 (get_open_orders OCO 合并渲染)
   │       ├── Task 5 (create_order algo 路由 + 手动 Order 构造)
   │       └── Task 6 (cancel_order + fetch_order + set_leverage + base 抽象)
   │              └── Task 7 (tools_execution.py 三处 cancel 调用转发 is_algo)
   └── Task 8 (start() posMode + acctLv + ws sandbox + watch_orders 诊断 log)

Task 9 (persona.py Layer 1 + 端到端 demo 冒烟)
Task 10 (memory 更新 + PR)
```

**并行机会**：Task 1 完成后，Task 2 / Task 8 可在两个 subagent 并发（Task 2 改 _parse_order / Order dataclass；Task 8 改 start() 与 _watch_orders_loop，两者不冲突）。Task 5 / Task 6 必须等 Task 2 完成（依赖 Order.is_algo 字段），但彼此可并行。

### 0.3 分支与 commit 规则

- 每个 Task 完成后**提交一次**（test + impl 一起），commit message 前缀 `feat(iter2b-Tn):` / `test(iter2b-Tn):` / `refactor(iter2b-Tn):`。
- **不 push 到 remote 直到 Task 10**（user feedback `feedback_git_branch.md`：feature 分支完成前先本地 commit，最终整包 push）。
- 禁 `--amend`，每个 task 独立 commit。
- 若某 task 的实现需要跨多文件、多组测试，保持"单 commit" —— 用 HEREDOC 传多行 commit message 说明。

### 0.4 File structure

本轮需要修改/创建的文件（按 task 归组）：

| 文件 | 修改内容 | 首次动刀的 Task |
|------|----------|---------------|
| `.env.example` | 加 `OKX_DEMO_*` + `OKX_SANDBOX` + 注释 | Task 1 |
| `src/config.py` | `ExchangeConfig` 加 `sandbox` 字段；`load_settings` 分流 credentials | Task 1 |
| `src/cli/app.py` | 构造 `OKXExchange` 时透传 `sandbox` | Task 1 |
| `src/integrations/exchange/base.py` | `Order` 加 `is_algo` 字段；`cancel_order` 抽象签名加 `is_algo` 参数 | Task 2 / Task 6 |
| `src/integrations/exchange/okx.py` | `__init__` 加 `sandbox`；`_parse_order` → `list[Order]`；新增 `_extract_trigger_prices` / `_make_algo_order` / `_make_oco` / `_is_okx_error_code` helpers；`fetch_open_orders` 三路 gather；`create_order` algo 路由；`cancel_order` / `fetch_order` / `set_leverage` 适配；`start()` 账户校验 + ws_client sandbox；`_watch_orders_loop` 诊断 log | Task 1-8 |
| `src/integrations/exchange/simulated.py` | `cancel_order` signature 对齐抽象 | Task 6 |
| `src/agent/tools_execution.py` | 三处 `cancel_order` 调用转发 `is_algo` | Task 7 |
| `src/agent/tools_perception.py` | `get_open_orders` 抽 `_render_single_order` + OCO 合并 | Task 4 |
| `src/agent/persona.py` | Layer 1 加一行 OCO 原子性说明 | Task 9 |
| `tests/test_config.py` | 6 条新测试（sandbox 分流） | Task 1 |
| `tests/test_exchange.py` 或新 `tests/test_okx_algo_normalization.py` | 主体归一化测试（~20 条） | Task 2-8 |
| `tests/test_tool_enhancement.py` | OCO 合并渲染测试 + is_algo 三处转发测试（追加到现有文件，不新建） | Task 4 / Task 7 |

---

## Task 1: sandboxMode 配置化 + credentials 分流 + call-site 透传

**Scope item #1**（spec §2.1）— 基础设施层，后续所有 task 依赖 sandbox flag 正确分流。

**Files:**
- Modify: `.env.example`
- Modify: `src/config.py:12-18`（`ExchangeConfig`）, `src/config.py:113-144`（`load_settings`）
- Modify: `src/cli/app.py:261-264`（OKXExchange 构造）
- Modify: `src/integrations/exchange/okx.py:86-106`（`__init__` 加 `sandbox` 参数）
- Test: `tests/test_config.py`（新增 6 条 sandbox 分流）+ `tests/test_exchange.py` 或新建 `tests/test_okx_algo_normalization.py`（新增 3 条 OKX init + 1 条 `build_services` call-site wiring = 4 条）

### Step 1.1: 写失败测试 — config 分流 6 条

- [ ] 在 `tests/test_config.py` 末尾追加 6 条新测试：

```python
# tests/test_config.py 追加

import tempfile
from pathlib import Path
from src.config import load_settings


def _write_yaml_settings(content: str = "") -> Path:
    """Helper: write a minimal settings.yaml to a temp file and return path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8",
    )
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def test_load_settings_sandbox_true_reads_demo_credentials():
    path = _write_yaml_settings("")
    env = {
        "OKX_SANDBOX": "true",
        "OKX_DEMO_API_KEY": "demo_key",
        "OKX_DEMO_SECRET": "demo_secret",
        "OKX_DEMO_PASSWORD": "demo_pwd",
        "OKX_API_KEY": "live_key",
        "OKX_SECRET": "live_secret",
        "OKX_PASSWORD": "live_pwd",
    }
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.api_key == "demo_key"
    assert settings.exchange.secret == "demo_secret"
    assert settings.exchange.password == "demo_pwd"
    assert settings.exchange.sandbox is True


def test_load_settings_sandbox_false_reads_live_credentials():
    path = _write_yaml_settings("")
    env = {
        "OKX_SANDBOX": "false",
        "OKX_DEMO_API_KEY": "demo_key",
        "OKX_API_KEY": "live_key",
        "OKX_SECRET": "live_secret",
        "OKX_PASSWORD": "live_pwd",
    }
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.api_key == "live_key"
    assert settings.exchange.sandbox is False


def test_load_settings_missing_sandbox_defaults_live():
    path = _write_yaml_settings("")
    env = {"OKX_API_KEY": "live_key", "OKX_SECRET": "live_s", "OKX_PASSWORD": "live_p"}
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.sandbox is False
    assert settings.exchange.api_key == "live_key"


def test_load_settings_yaml_sandbox_true_wins_over_env_missing():
    path = _write_yaml_settings("exchange:\n  sandbox: true\n")
    env = {"OKX_DEMO_API_KEY": "demo_k", "OKX_DEMO_SECRET": "d_s", "OKX_DEMO_PASSWORD": "d_p"}
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.sandbox is True
    assert settings.exchange.api_key == "demo_k"


def test_load_settings_yaml_sandbox_false_overrides_env_true():
    """YAML 显式 sandbox=false 必须覆盖 OKX_SANDBOX=true env —— final_sandbox 单一 SoT 关键分支。

    若 final_sandbox 错用 env-derived sandbox_env（非 exchange["sandbox"]），
    此场景会错走 demo credentials 路径 → demo endpoint + 空 live credentials
    或 demo credentials 的 live 标签，auth 失败时 error message 误导。
    """
    path = _write_yaml_settings("exchange:\n  sandbox: false\n")
    env = {
        "OKX_SANDBOX": "true",
        "OKX_API_KEY": "live_key", "OKX_SECRET": "live_s", "OKX_PASSWORD": "live_p",
        "OKX_DEMO_API_KEY": "demo_k",
    }
    settings = load_settings(path, env_overrides=env)
    assert settings.exchange.sandbox is False
    assert settings.exchange.api_key == "live_key"


def test_load_settings_empty_env_dict_defaults_to_live_empty_credentials():
    path = _write_yaml_settings("")
    settings = load_settings(path, env_overrides={})
    assert settings.exchange.sandbox is False
    assert settings.exchange.api_key == ""
    assert settings.exchange.secret == ""
    assert settings.exchange.password == ""
```

### Step 1.2: 写失败测试 — OKXExchange sandbox init + build_services call-site wiring（4 条）

- [ ] 选择测试文件：如 `tests/test_exchange.py` 已存在相关 OKX 测试组就续写；否则新建 `tests/test_okx_algo_normalization.py`。追加：

```python
# tests/test_okx_algo_normalization.py（新建）或 tests/test_exchange.py 追加

from unittest.mock import MagicMock, patch

import pytest


def test_okx_init_sandbox_true_calls_set_sandbox_mode_on_rest_client():
    with patch("src.integrations.exchange.okx.ccxt") as mock_ccxt:
        fake_client = MagicMock()
        mock_ccxt.okx.return_value = fake_client
        from src.integrations.exchange.okx import OKXExchange
        OKXExchange(api_key="k", secret="s", password="p",
                    symbol="BTC/USDT:USDT", sandbox=True)
        fake_client.set_sandbox_mode.assert_called_once_with(True)


def test_okx_init_sandbox_false_does_not_call_set_sandbox_mode():
    with patch("src.integrations.exchange.okx.ccxt") as mock_ccxt:
        fake_client = MagicMock()
        mock_ccxt.okx.return_value = fake_client
        from src.integrations.exchange.okx import OKXExchange
        OKXExchange(api_key="k", secret="s", password="p",
                    symbol="BTC/USDT:USDT", sandbox=False)
        fake_client.set_sandbox_mode.assert_not_called()


def test_okx_init_stores_sandbox_as_instance_field():
    with patch("src.integrations.exchange.okx.ccxt"):
        from src.integrations.exchange.okx import OKXExchange
        ex = OKXExchange(api_key="k", secret="s", password="p",
                         symbol="BTC/USDT:USDT", sandbox=True)
        assert ex._sandbox is True


def test_build_services_passes_sandbox_from_settings_to_okx_exchange():
    """Call-site wiring 回归：app.build_services 必须从 settings.exchange.sandbox
    透传到 OKXExchange 构造；漏传 = demo credentials 打 live endpoint（spec §2.1.2 footgun）。

    稳健性设计（回应 plan review B3）：
      - patch 链覆盖 OKXExchange 之后的所有构造依赖，避免后续 IO 或依赖缺失
        raise 把真问题伪装成 mock_okx_cls.called=False（假阳性）。
      - 保守起见 try/except 仍兜底，但主断言前先显式断言 called=True，
        failure 消息明确指向 mock 不足 vs call-site wiring 缺失。
    """
    from unittest.mock import MagicMock, patch

    result = MagicMock()
    result.exchange_type = "okx"
    result.symbol = "BTC/USDT:USDT"
    result.api_credentials = {"api_key": "k", "secret": "s", "password": "p"}
    result.token_budget = 1_000_000
    result.approval_enabled = False
    result.initial_balance = 100.0
    result.model = "claude-sonnet"
    result.persona = MagicMock()
    result.alert_enabled = False
    result.fee_rate = None

    from src.config import Settings
    settings = Settings()
    settings.exchange.sandbox = True  # call-site 必须把这个透传出去

    sc = MagicMock()

    # 全链 patch：OKXExchange + 其后所有构造依赖，任何一处 raise 都可能让 called 误报
    with patch("src.cli.app.OKXExchange") as mock_okx_cls, \
         patch("src.cli.app.MarketDataService"), \
         patch("src.cli.app.TechnicalAnalysisService"), \
         patch("src.cli.app.MemoryService"), \
         patch("src.cli.app.TokenBudget"), \
         patch("src.cli.app.ApprovalGate"), \
         patch("src.cli.app.create_trader_agent"):
        mock_okx_cls.return_value = MagicMock()
        from src.cli.app import build_services
        try:
            build_services(result, engine=MagicMock(), session_id="s1",
                           sc=sc, settings=settings)
        except Exception:
            # MetricsService / NewsService 等后续构造可能因 Settings 空值 raise；
            # OKXExchange 是 build_services 里**第一个**真实构造调用（app.py:261），
            # 任何后续 raise 时它已被 call 过。若下面 called=False，是 call-site
            # 真漏传或 patch 链未完全覆盖。
            pass
        assert mock_okx_cls.called, (
            "OKXExchange 未被构造调用 — 可能是 call-site 漏传（本测试目标 bug），"
            "也可能是 patch 链未完全覆盖 build_services 在 OKXExchange 之前 raise。"
            "若后者，扩展上方 patch 链再验证。"
        )
        kwargs = mock_okx_cls.call_args.kwargs
        assert kwargs.get("sandbox") is True, \
            f"call-site 漏传 sandbox kwarg；实际 kwargs={kwargs}"
```

### Step 1.3: 运行测试确认失败

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_config.py::test_load_settings_sandbox_true_reads_demo_credentials tests/test_okx_algo_normalization.py -v 2>&1 | tail -20
```

Expected: 所有 10 条新测试 FAIL（`ExchangeConfig` 没有 sandbox 字段 / `OKXExchange.__init__` 不接受 sandbox 参数 / `build_services` 漏透传）。

### Step 1.4: 实现 — ExchangeConfig + load_settings

- [ ] 在 `src/config.py:12-18` 的 `ExchangeConfig` 加 `sandbox` 字段：

```python
class ExchangeConfig(BaseModel):
    name: str = "okx"
    api_key: str = ""
    secret: str = ""
    password: str = ""
    fee_rate: float | None = None
    precision: dict[str, int] | None = None
    sandbox: bool = False
```

- [ ] 在 `src/config.py:113-144` 改写 `load_settings` 的 exchange 区块（其他 env 区块原样保留）：

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
    # Env-derived sandbox flag — seed for setdefault
    sandbox_env = env_overrides.get("OKX_SANDBOX", "").lower() == "true"
    exchange.setdefault("sandbox", sandbox_env)
    # Final sandbox = YAML-set value (if any) else env-derived; single source of truth.
    final_sandbox = bool(exchange["sandbox"])

    if final_sandbox:
        exchange.setdefault("api_key", env_overrides.get("OKX_DEMO_API_KEY", ""))
        exchange.setdefault("secret", env_overrides.get("OKX_DEMO_SECRET", ""))
        exchange.setdefault("password", env_overrides.get("OKX_DEMO_PASSWORD", ""))
    else:
        exchange.setdefault("api_key", env_overrides.get("OKX_API_KEY", ""))
        exchange.setdefault("secret", env_overrides.get("OKX_SECRET", ""))
        exchange.setdefault("password", env_overrides.get("OKX_PASSWORD", ""))
    data["exchange"] = exchange

    # 保留 macro / crypto_etf 现有 env override 块不动
    macro = data.get("macro", {})
    macro.setdefault("fred_api_key", env_overrides.get("FRED_API_KEY", ""))
    macro.setdefault("alpha_vantage_api_key",
                     env_overrides.get("ALPHA_VANTAGE_API_KEY", ""))
    macro.setdefault("coingecko_demo_api_key",
                     env_overrides.get("COINGECKO_DEMO_API_KEY", ""))
    data["macro"] = macro

    crypto_etf = data.get("crypto_etf", {})
    crypto_etf.setdefault("sosovalue_api_key",
                          env_overrides.get("SOSOVALUE_API_KEY", ""))
    data["crypto_etf"] = crypto_etf

    return Settings(**data)
```

### Step 1.5: 实现 — OKXExchange sandbox 参数

- [ ] 修改 `src/integrations/exchange/okx.py:86-106`：

```python
class OKXExchange(BaseExchange):
    def __init__(self, api_key: str, secret: str, password: str, symbol: str,
                 sandbox: bool = False):
        super().__init__()
        self._client = ccxt.okx(
            {
                "apiKey": api_key,
                "secret": secret,
                "password": password,
                "options": {"defaultType": "swap"},
                "timeout": 30000,
            }
        )
        if sandbox:
            self._client.set_sandbox_mode(True)
        self._sandbox = sandbox
        self._symbol = symbol
        self._fill_callback: Callable[[FillEvent], Awaitable[None]] | None = None
        self._alert_callback: Callable[[Any], Awaitable[None]] | None = None
        self._running = False
        self._ws_client: Any | None = None
        self._ws_connected = False
        self._pnl_fetch_timeout: float = 5.0
        self._seen_order_ids: dict[str, None] = {}
        self._seen_order_ids_max = 10000
        logger.info(
            "OKX exchange initialized (%s account)",
            "demo" if sandbox else "live",
        )
        # spec §2.1.4 Live endpoint 守卫 — 警示 log（不 fail，只提高观察度）
        if not sandbox and api_key:
            logger.warning(
                "OKX live account initialized — ALL ORDERS WILL USE REAL FUNDS"
            )
```

### Step 1.6: 实现 — app.py 透传 sandbox

- [ ] 修改 `src/cli/app.py:261-264`：

```python
    else:
        creds = result.api_credentials
        exchange = OKXExchange(
            api_key=creds["api_key"], secret=creds["secret"],
            password=creds["password"], symbol=result.symbol,
            sandbox=settings.exchange.sandbox,
        )
        sc.print("Exchange: okx (REAL account)")
```

### Step 1.7: 更新 .env.example

- [ ] 读现有 `.env.example`，在 OKX 区块插入 demo credentials + sandbox flag。应保留所有现有 key 不变：

```bash
# OKX live credentials (留空直到实盘接入)
OKX_API_KEY=your_api_key_here
OKX_SECRET=your_secret_here
OKX_PASSWORD=your_password_here

# OKX demo (模拟盘) credentials — 新增
OKX_DEMO_API_KEY=your_demo_api_key_here
OKX_DEMO_SECRET=your_demo_secret_here
OKX_DEMO_PASSWORD=your_demo_password_here

# 对接引擎开关 — 新增
#   true  → demo (adds x-simulated-trading: 1 header + 读 OKX_DEMO_*)
#   false → live (读 OKX_*)
OKX_SANDBOX=false
```

### Step 1.8: 跑所有新测试 + 全套回归

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_config.py tests/test_okx_algo_normalization.py -v 2>&1 | tail -20
```

Expected: 10 条新测试 PASS。

- [ ] 全套回归：

```bash
cd /Users/z/Z/TradeBot && pytest 2>&1 | tail -10
```

Expected: 730 baseline + 10 新通过 = 740，无 regression。如有 failure，仔细阅读错误；最可能是 `test_cli_app` 一类测试 mock 了 `OKXExchange` 但没带新参数，调整 mock。

### Step 1.9: Commit

- [ ] Commit:

```bash
cd /Users/z/Z/TradeBot && git add .env.example src/config.py src/cli/app.py src/integrations/exchange/okx.py tests/test_config.py tests/test_okx_algo_normalization.py && git commit -m "$(cat <<'EOF'
feat(iter2b-T1): sandboxMode 配置化 + OKX_DEMO_* credentials 分流 + app.py call-site wiring

- ExchangeConfig 加 sandbox 字段；load_settings 按 OKX_SANDBOX flag 分流 live/demo credentials
- OKXExchange.__init__ 加 sandbox 参数，sandbox=True 时 REST client set_sandbox_mode
- app.py 构造 OKXExchange 时透传 settings.exchange.sandbox
- .env.example 加 OKX_DEMO_* + OKX_SANDBOX 注释

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `Order.is_algo` + `_parse_order` algo 归一化 + Sim signature 同步

**Scope item #2**（spec §2.2 + §3.1）— 读路径核心。

**Files:**
- Modify: `src/integrations/exchange/base.py:33-41`（`Order` 加 `is_algo` 字段）
- Modify: `src/integrations/exchange/okx.py:327-337`（`_parse_order` 改 → `list[Order]`，新增 `_extract_trigger_prices` / `_make_algo_order` / `_make_oco` / `_parse_plain` helpers）
- Modify: `src/integrations/exchange/okx.py:361-372`（`fetch_open_orders` / `fetch_closed_orders` flat merge 适配新 signature）
- Modify: `src/integrations/exchange/okx.py:340-358`（`create_order` / `fetch_order` 临时 `parsed[0]` 适配新 signature，待 Task 5/6 扩展）
- Test: `tests/test_okx_algo_normalization.py`（追加 ~8 条）

### Step 2.1: 写失败测试 — `_parse_order` 归一化 8 条

- [ ] 在 `tests/test_okx_algo_normalization.py` 追加：

```python
# tests/test_okx_algo_normalization.py 追加

import json
from pathlib import Path

import pytest

from src.integrations.exchange.base import Order

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_okx(sandbox: bool = False):
    from unittest.mock import patch
    with patch("src.integrations.exchange.okx.ccxt"):
        from src.integrations.exchange.okx import OKXExchange
        return OKXExchange(api_key="k", secret="s", password="p",
                           symbol="BTC/USDT:USDT", sandbox=sandbox)


def test_parse_order_plain_returns_single_order_list():
    ex = _make_okx()
    data = {
        "id": "plain_1", "symbol": "BTC/USDT:USDT", "side": "buy",
        "type": "limit", "amount": 0.5, "price": 65000.0,
        "status": "open", "fee": None,
    }
    out = ex._parse_order(data)
    assert len(out) == 1
    assert out[0].order_type == "limit"
    assert out[0].is_algo is False


def test_parse_order_conditional_sl_produces_stop_order_from_unified():
    ex = _make_okx()
    data = _load_fixture("okx_fetch_open_orders_conditional_sl_unified.json")
    out = ex._parse_order(data)
    assert len(out) == 1
    o = out[0]
    assert o.order_type == "stop"
    assert o.price == pytest.approx(54405.3)
    assert o.is_algo is True
    assert o.id == data["id"]


def test_parse_order_conditional_tp_override_produces_take_profit():
    ex = _make_okx()
    base = _load_fixture("okx_fetch_open_orders_conditional_sl_unified.json")
    data = {**base, "stopLossPrice": None, "takeProfitPrice": 60000.0}
    data["info"] = {**base["info"], "slTriggerPx": "", "tpTriggerPx": "60000"}
    out = ex._parse_order(data)
    assert len(out) == 1
    assert out[0].order_type == "take_profit"
    assert out[0].price == pytest.approx(60000.0)
    assert out[0].is_algo is True


def test_parse_order_conditional_falls_back_to_info_when_unified_none():
    ex = _make_okx()
    base = _load_fixture("okx_fetch_open_orders_conditional_sl_unified.json")
    data = {**base, "stopLossPrice": None}
    # info.slTriggerPx 保留原 fixture 的 "54405.3"
    out = ex._parse_order(data)
    assert len(out) == 1
    assert out[0].order_type == "stop"
    assert out[0].price == pytest.approx(54405.3)


def test_parse_order_conditional_both_empty_falls_back_to_plain_with_warning(caplog):
    ex = _make_okx()
    base = _load_fixture("okx_fetch_open_orders_conditional_sl_unified.json")
    data = {**base, "stopLossPrice": None, "takeProfitPrice": None,
            "type": "conditional"}
    data["info"] = {**base["info"], "slTriggerPx": "", "tpTriggerPx": ""}
    with caplog.at_level("WARNING"):
        out = ex._parse_order(data)
    assert len(out) == 1
    assert out[0].is_algo is False  # plain fallback
    assert any("conditional" in r.message.lower() for r in caplog.records)


def test_parse_order_oco_splits_to_two_orders_sharing_id():
    ex = _make_okx()
    data = _load_fixture("okx_fetch_open_orders_oco_unified.json")
    out = ex._parse_order(data)
    assert len(out) == 2
    ids = {o.id for o in out}
    assert len(ids) == 1  # 共享 id
    types = {o.order_type for o in out}
    assert types == {"stop", "take_profit"}
    prices = {o.order_type: o.price for o in out}
    assert prices["stop"] == pytest.approx(54405.3)
    assert prices["take_profit"] == pytest.approx(101038.3)
    assert all(o.is_algo for o in out)


def test_parse_order_oco_malformed_falls_back_with_warning(caplog):
    ex = _make_okx()
    base = _load_fixture("okx_fetch_open_orders_oco_unified.json")
    data = {**base, "takeProfitPrice": None}
    data["info"] = {**base["info"], "tpTriggerPx": ""}
    with caplog.at_level("WARNING"):
        out = ex._parse_order(data)
    assert len(out) == 1
    assert out[0].is_algo is False
    assert any("OCO" in r.message or "oco" in r.message.lower() for r in caplog.records)


def test_parse_order_unknown_algo_type_falls_back():
    ex = _make_okx()
    base = _load_fixture("okx_fetch_open_orders_conditional_sl_unified.json")
    data = {**base, "type": "trigger"}
    data["info"] = {**base["info"], "ordType": "trigger"}
    out = ex._parse_order(data)
    assert len(out) == 1
    assert out[0].is_algo is False
```

### Step 2.2: 运行测试确认失败

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py::test_parse_order_plain_returns_single_order_list tests/test_okx_algo_normalization.py::test_parse_order_conditional_sl_produces_stop_order_from_unified -v 2>&1 | tail -25
```

Expected: FAIL 因为 `_parse_order` 当前返 `Order`，新测试期望 `list[Order]` + `is_algo` 字段不存在。

### Step 2.3: 实现 — Order 加 is_algo 字段

- [ ] 修改 `src/integrations/exchange/base.py:33-41`，追加字段：

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
```

### Step 2.4: 实现 — `_parse_order` 归一化 + helpers

- [ ] 重写 `src/integrations/exchange/okx.py:327-337`。整段替换为：

```python
def _parse_order(self, data: dict) -> list[Order]:
    """CCXT-unified OKX order dict → 一条或多条逻辑 Order。

    - ordType="oco" + 两个 trigger 都存在   → 2 条共享 id 的 Order（stop + take_profit）
    - ordType="conditional" + 只有 sl_px    → [Order(stop)]
    - ordType="conditional" + 只有 tp_px    → [Order(take_profit)]
    - 其他情况（plain / 异常 algo shape）    → [单条 Order]（is_algo=False）
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
    """Two-layer trigger price extraction: unified top-level 主 + info fallback 防 CCXT 版本漂移。"""
    sl_px = data.get("stopLossPrice")
    tp_px = data.get("takeProfitPrice")
    info = data.get("info") or {}
    if sl_px is None:
        raw_sl = info.get("slTriggerPx")
        if raw_sl:
            sl_px = float(raw_sl)
    if tp_px is None:
        raw_tp = info.get("tpTriggerPx")
        if raw_tp:
            tp_px = float(raw_tp)
    return sl_px, tp_px

def _parse_plain(self, data: dict) -> Order:
    return Order(
        id=data["id"],
        symbol=data["symbol"],
        side=data["side"],
        order_type=data["type"],
        amount=float(data["amount"]),
        price=float(data["price"]) if data.get("price") else None,
        status=data["status"],
        fee=self._parse_fee(data),
        is_algo=False,
    )

def _make_algo_order(self, data: dict, order_type: str, price: float) -> Order:
    return Order(
        id=data["id"],
        symbol=data["symbol"],
        side=data["side"],
        order_type=order_type,
        amount=float(data["amount"]),
        price=price,
        status=data["status"],
        fee=None,
        is_algo=True,
    )

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
        Order(order_type="stop", price=sl_px, **common),
        Order(order_type="take_profit", price=tp_px, **common),
    ]
```

### Step 2.5: 实现 — `fetch_open_orders` / `fetch_closed_orders` flat merge

- [ ] 修改 `src/integrations/exchange/okx.py:361-372`。注意 fetch_open_orders 暂时**保留单路**调用（Task 3 再扩三路 gather），仅适配 list-of-list flatten：

```python
@_retry()
async def fetch_open_orders(self, symbol: str) -> list[Order]:
    raw = await self._client.fetch_open_orders(symbol)
    return [o for d in raw for o in self._parse_order(d)]

@_retry()
async def fetch_closed_orders(
    self, symbol: str, limit: int = 20
) -> list[Order]:
    raw = await self._client.fetch_orders(
        symbol, limit=limit, params={"state": "filled"}
    )
    return [o for d in raw for o in self._parse_order(d)]
```

### Step 2.6: 实现 — `create_order` / `fetch_order` 临时适配

Task 5 / Task 6 会进一步改 `create_order` 加 algo 路由、`fetch_order` 加 fallback。本 task 仅让它们不因 signature 变更 break：

- [ ] 修改 `src/integrations/exchange/okx.py:340-358`：

```python
@_retry()
async def create_order(  # type: ignore[override]
    self,
    symbol: str,
    side: str,
    order_type: str,
    amount: float,
    price: float | None = None,
) -> Order:
    data = await self._client.create_order(
        symbol, order_type, side, amount, price  # type: ignore[arg-type]
    )
    parsed = self._parse_order(data)
    return parsed[0]

@_retry()
async def fetch_order(  # type: ignore[override]
    self, order_id: str, symbol: str | None = None
) -> Order:
    data = await self._client.fetch_order(order_id, symbol)
    parsed = self._parse_order(data)
    return parsed[0]
```

### Step 2.7: 跑新测试 + 全套回归

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py -v 2>&1 | tail -30
```

Expected: 所有 12 条（包括 Task 1 的 4 条 init + wiring）PASS。

- [ ] Run 全套：

```bash
cd /Users/z/Z/TradeBot && pytest 2>&1 | tail -15
```

Expected: 730 baseline + 18 累计（Task 1: 10 + Task 2: 8）= 748。Sim 相关测试不应 break（Sim 的 Order 构造点不改；`is_algo` 默认 `False`）；若某些 OKX 相关测试 break，通常是旧测试 assert 了 `Order` 精确比较 —— 用 `order.id == "..."` 而非 `order == Order(...)` 即可修正。

### Step 2.8: Commit

- [ ] Commit:

```bash
cd /Users/z/Z/TradeBot && git add src/integrations/exchange/base.py src/integrations/exchange/okx.py tests/test_okx_algo_normalization.py && git commit -m "$(cat <<'EOF'
feat(iter2b-T2): _parse_order algo 归一化 + Order.is_algo 字段

- Order dataclass 加 is_algo: bool = False（默认零影响 Sim + plain 路径）
- _parse_order 改为 -> list[Order]；OCO 拆 2 条共享 id；conditional 单腿分 stop/take_profit
- 新增 _extract_trigger_prices / _make_algo_order / _make_oco / _parse_plain helpers
- fetch_open_orders / fetch_closed_orders / create_order / fetch_order 适配 list signature

NOTE: fetch_open_orders 此 commit 仍为单路 plain 调用，algo orders
not yet queried — Task 3 扩三路 asyncio.gather 合并 plain+conditional+oco.
bisect 时请勿误判为 regression.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `fetch_open_orders` 三路 `asyncio.gather` 合并

**Scope item #3**（spec §2.3）— 依赖 Task 2 的 `_parse_order -> list[Order]` 签名。

**Files:**
- Modify: `src/integrations/exchange/okx.py`（`fetch_open_orders` 单路 → 三路 gather）
- Test: `tests/test_okx_algo_normalization.py`（追加 2 条 + 1 条 advisory skip 占位 = 3 条，passed 计数 +2，skipped +1）

### Step 3.1: 写失败测试 — 三路 gather 合并 + 1 条 advisory skip 占位

- [ ] 追加到 `tests/test_okx_algo_normalization.py`：

```python
# tests/test_okx_algo_normalization.py 追加

from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_fetch_open_orders_merges_three_endpoints():
    ex = _make_okx()
    plain = {
        "id": "p1", "symbol": "BTC/USDT:USDT", "side": "buy",
        "type": "limit", "amount": 0.1, "price": 65000.0,
        "status": "open", "fee": None,
    }
    cond = _load_fixture("okx_fetch_open_orders_conditional_sl_unified.json")
    oco = _load_fixture("okx_fetch_open_orders_oco_unified.json")

    async def fake_fetch(symbol, params=None):
        params = params or {}
        if not params.get("stop"):
            return [plain]
        if params.get("ordType") == "conditional":
            return [cond]
        if params.get("ordType") == "oco":
            return [oco]
        return []

    ex._client.fetch_open_orders = AsyncMock(side_effect=fake_fetch)
    result = await ex.fetch_open_orders("BTC/USDT:USDT")
    # 1 plain + 1 conditional SL + 2 OCO 腿 = 4
    assert len(result) == 4
    types = [o.order_type for o in result]
    assert "limit" in types
    assert types.count("stop") == 2  # 1 conditional SL + 1 OCO SL
    assert types.count("take_profit") == 1  # OCO TP
    # 三路都被调用
    assert ex._client.fetch_open_orders.call_count == 3


@pytest.mark.asyncio
async def test_fetch_open_orders_passes_ordtype_params():
    """验证 params 字典里 ordType 正确分两路 conditional + oco。"""
    ex = _make_okx()
    ex._client.fetch_open_orders = AsyncMock(return_value=[])
    await ex.fetch_open_orders("BTC/USDT:USDT")
    calls = ex._client.fetch_open_orders.call_args_list
    params_list = [c.kwargs.get("params") or (c.args[1] if len(c.args) > 1 else None)
                   for c in calls]
    # plain 路径 params 为空/None；两条 algo 路径分别传 conditional / oco
    algo_ordtypes = sorted(
        p["ordType"] for p in params_list if p and p.get("stop") is True
    )
    assert algo_ordtypes == ["conditional", "oco"]


@pytest.mark.skip(reason=(
    "CCXT rate-limiter serializes concurrent requests in same client; "
    "timing assertion version-sensitive. Spec §5.2 / §6 advisory only, "
    "not merge gate — placeholder so spec acceptance is structurally complete."
))
@pytest.mark.asyncio
async def test_fetch_open_orders_concurrent_not_serial():
    """Advisory: verify gather 真并发而非串行（非本 PR merge gate）。

    实现骨架（skip 状态下不 run；未来若要启用，解除 skip + 对齐当前 CCXT 版本）：
      用 asyncio.Event 在每个 AsyncMock 里阻塞，断言三路同时进入而非串行。
    """
    import asyncio
    ex = _make_okx()
    entered = [asyncio.Event() for _ in range(3)]
    release = asyncio.Event()

    call_ix = {"i": 0}

    async def fake(symbol, params=None):
        i = call_ix["i"]
        call_ix["i"] += 1
        entered[i].set()
        await release.wait()
        return []

    ex._client.fetch_open_orders = fake
    task = asyncio.create_task(ex.fetch_open_orders("BTC/USDT:USDT"))
    await asyncio.wait_for(
        asyncio.gather(*[e.wait() for e in entered]), timeout=1.0,
    )
    release.set()
    await task
```

### Step 3.2: 运行测试确认失败

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py::test_fetch_open_orders_merges_three_endpoints -v 2>&1 | tail -15
```

Expected: FAIL，当前 `fetch_open_orders` 只调一次（1 plain，非三路）。

### Step 3.3: 实现 — fetch_open_orders 三路 gather

- [ ] 修改 `src/integrations/exchange/okx.py` 的 `fetch_open_orders`：

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

### Step 3.4: 跑测试 + 回归

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py -v 2>&1 | tail -20
```

Expected: 14 条 PASS + 1 条 SKIPPED（test_okx_algo_normalization 累计，不含 test_config；Task 1: 4 + Task 2: 8 + Task 3: 2 passed + 1 skip = 15 collected, 14 passed）。

- [ ] 全套回归：

```bash
cd /Users/z/Z/TradeBot && pytest 2>&1 | tail -10
```

Expected: 750 passed + 1 skipped（730 + 20 累计；Task 1-3 = 10+8+2 = 20 passed；Task 3 advisory skip 不计 passed）。

### Step 3.5: Commit

- [ ] Commit:

```bash
cd /Users/z/Z/TradeBot && git add src/integrations/exchange/okx.py tests/test_okx_algo_normalization.py && git commit -m "$(cat <<'EOF'
feat(iter2b-T3): fetch_open_orders 三路 gather 合并 plain + conditional + oco

asyncio.gather 并发三路 fetch_open_orders 以覆盖 OKX 的 plain +
algo(conditional / oco) 两个 endpoint 家族；合并后经 _parse_order flatten

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `get_open_orders` OCO 合并展示

**Scope item #4**（spec §2.4）— 依赖 Task 2 的 `Order.is_algo` 字段。

**⚠️ 合并分支的触发场景（架构 limitation）**：agent 主路径调 `set_stop_loss(X)` + `set_take_profit(Y)` 产生**两条独立 conditional**（各自 algoId 不同 → `by_id` 分组成两个 1-len group → 不触发 OCO 合并）。**OCO 合并分支仅在**用户通过 OKX web 手动下 OCO / 底层 `private_post_trade_order_algo(ordType="oco")` / CCXT attach 模式时命中。本 task 实现正确性与 agent 主路径无关；未来若 agent 需要独立 SL+TP 原子化，走 Iter 3+ 扩展（创建真 OCO 的 tool，spec 非目标列已明确不在本轮）。

**Files:**
- Modify: `src/agent/tools_perception.py:317-342`（`get_open_orders` 抽 helper + OCO 合并）
- Test: `tests/test_tool_enhancement.py`（追加 4 条到现有文件末尾；现有 `test_get_open_orders_with_distance:638` 是 plain 路径测试，本 task 不改它，确保 zero byte-level regression）

### Step 4.1: 写失败测试 — OCO 合并渲染 4 条

- [ ] 追加到 `tests/test_tool_enhancement.py` 文件末尾（不新建文件；现有文件已有 `test_get_open_orders_with_distance:638` 覆盖 plain 路径，本 task 4 条新测试专测 OCO 合并 + edge case）：

```python
# tests/test_tool_enhancement.py 追加到末尾

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.integrations.exchange.base import Order, Ticker


def _make_oco_deps(orders: list[Order], ticker_last: float = 70000.0):
    """OCO 合并渲染专用的 deps 工厂（避免与文件现有 helper 冲突）。"""
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.exchange = MagicMock()
    deps.exchange.fetch_open_orders = AsyncMock(return_value=orders)
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=ticker_last, bid=ticker_last - 1,
        ask=ticker_last + 1, high=ticker_last, low=ticker_last,
        base_volume=0.0, timestamp=0,
    ))
    return deps


@pytest.mark.asyncio
async def test_get_open_orders_merges_oco_into_single_line():
    from src.agent.tools_perception import get_open_orders
    oco_id = "algo_123"
    orders = [
        Order(id=oco_id, symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=1.0, price=60000.0,
              status="open", fee=None, is_algo=True),
        Order(id=oco_id, symbol="BTC/USDT:USDT", side="sell",
              order_type="take_profit", amount=1.0, price=80000.0,
              status="open", fee=None, is_algo=True),
    ]
    out = await get_open_orders(_make_oco_deps(orders))
    lines = out.splitlines()
    # Only one rendered row besides the header
    assert lines[0] == "Pending Orders:"
    data_lines = [l for l in lines[1:] if l.strip()]
    assert len(data_lines) == 1, f"expected 1 merged OCO line, got {data_lines}"
    row = data_lines[0]
    assert "[OCO]" in row
    assert "stop" in row.lower()
    assert "tp" in row.lower()
    assert "algoId:" in row
    assert "cancel removes both legs" in row
    assert "60000.00" in row
    assert "80000.00" in row


@pytest.mark.asyncio
async def test_get_open_orders_non_oco_single_orders_separate_lines():
    from src.agent.tools_perception import get_open_orders
    orders = [
        Order(id="p1", symbol="BTC/USDT:USDT", side="buy",
              order_type="limit", amount=0.5, price=65000.0,
              status="open", is_algo=False),
        Order(id="s1", symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=0.5, price=60000.0,
              status="open", is_algo=True),  # 单腿 conditional SL
    ]
    out = await get_open_orders(_make_oco_deps(orders))
    data_lines = [l for l in out.splitlines()[1:] if l.strip()]
    assert len(data_lines) == 2
    assert "[LIMIT]" in data_lines[0]
    assert "[STOP]" in data_lines[1]
    # 不要出现 OCO 合并标签
    assert "[OCO]" not in out


@pytest.mark.asyncio
async def test_get_open_orders_fact_only_no_banned_words():
    """N5 fact-only 合规回归 — OCO 合并行不得含 protective / tight / wide 等评价词。"""
    from src.agent.tools_perception import get_open_orders
    orders = [
        Order(id="oco_1", symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=1.0, price=60000.0, status="open",
              is_algo=True),
        Order(id="oco_1", symbol="BTC/USDT:USDT", side="sell",
              order_type="take_profit", amount=1.0, price=80000.0, status="open",
              is_algo=True),
    ]
    out = await get_open_orders(_make_oco_deps(orders))
    banned = ("protective", "tight", "wide", "safe", "aggressive")
    for word in banned:
        assert word not in out.lower(), f"banned word '{word}' in output:\n{out}"


@pytest.mark.asyncio
async def test_get_open_orders_oco_handles_zero_ticker_without_dist_suffix():
    """ticker.last == 0（异常 fallback）时 OCO 合并行不得 ZeroDivisionError；
    dist 后缀应省略，其余字段完整。spec §2.4.1 警告条目的回归。"""
    from src.agent.tools_perception import get_open_orders
    orders = [
        Order(id="oco_z", symbol="BTC/USDT:USDT", side="sell",
              order_type="stop", amount=1.0, price=60000.0, status="open",
              is_algo=True),
        Order(id="oco_z", symbol="BTC/USDT:USDT", side="sell",
              order_type="take_profit", amount=1.0, price=80000.0, status="open",
              is_algo=True),
    ]
    out = await get_open_orders(_make_oco_deps(orders, ticker_last=0.0))
    # 没崩溃 + OCO 结构保留
    data_lines = [l for l in out.splitlines()[1:] if l.strip()]
    assert len(data_lines) == 1
    row = data_lines[0]
    assert "[OCO]" in row
    assert "60000.00" in row and "80000.00" in row
    # dist 后缀不得出现（不该 % from current）
    assert "% from current" not in row
```

### Step 4.2: 运行测试确认失败

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_tool_enhancement.py::test_get_open_orders_merges_oco_into_single_line -v 2>&1 | tail -15
```

Expected: FAIL，当前 `get_open_orders` 不合并 OCO（两条同 id 各自一行）。

### Step 4.3: 实现 — get_open_orders 抽 helper + OCO 合并

- [ ] 重写 `src/agent/tools_perception.py:317-342`：

```python
def _render_single_order(o, current: float) -> str:
    """Render a single (non-OCO) order line — preserves pre-Iter-2b rendering exactly.

    保留 current > 0 分支：ticker 异常时不崩溃。label/distance/ID 尾巴格式与
    原 tools_perception.py:327-341 逐字一致，以满足 spec §6 "zero byte-level regression"。
    """
    if o.order_type == "market" or o.price is None:
        label = "[PENDING]" if o.order_type == "market" else f"[{o.order_type.upper()}]"
        price_str = "market price"
    else:
        if o.order_type == "limit":
            label = "[LIMIT]"
        else:
            label = f"[{o.order_type.upper()}]"
        if current > 0:
            dist = (o.price - current) / current * 100
            price_str = f"@ {o.price:.2f} ({dist:+.2f}% from current)"
        else:
            price_str = f"@ {o.price:.2f}"
    return f"  {label} {o.side} {o.amount} {price_str} | ID: {o.id}"


async def get_open_orders(deps: TradingDeps) -> str:
    """Get all pending orders with distance from current price."""
    orders = await deps.exchange.fetch_open_orders(deps.symbol)
    if not orders:
        return "No pending orders."

    ticker = await deps.market_data.get_ticker(deps.symbol)
    current = ticker.last

    # 按 id 分组：OCO 的两条同 id 且 is_algo=True
    by_id: dict[str, list] = {}
    for o in orders:
        by_id.setdefault(o.id, []).append(o)

    lines = ["Pending Orders:"]
    for order_id, group in by_id.items():
        is_oco = (
            len(group) == 2
            and {o.order_type for o in group} == {"stop", "take_profit"}
            and all(o.is_algo for o in group)
        )
        if is_oco:
            sl = next(o for o in group if o.order_type == "stop")
            tp = next(o for o in group if o.order_type == "take_profit")
            sl_dist = (
                f" ({(sl.price - current) / current * 100:+.2f}% from current)"
                if current > 0 else ""
            )
            tp_dist = (
                f" ({(tp.price - current) / current * 100:+.2f}% from current)"
                if current > 0 else ""
            )
            lines.append(
                f"  [OCO] {sl.side} {sl.amount} "
                f"stop {sl.price:.2f}{sl_dist} / tp {tp.price:.2f}{tp_dist} "
                f"| algoId: {order_id} (cancel removes both legs)"
            )
        else:
            for o in group:
                lines.append(_render_single_order(o, current))
    return "\n".join(lines)
```

### Step 4.4: 跑测试 + 回归

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_tool_enhancement.py -v 2>&1 | tail -30
```

Expected: 4 条新 PASS（merge / separate / fact-only / zero-ticker）。旧 `test_get_open_orders*` 测试（若有）必须 **zero byte-level regression** —— `_render_single_order` 的 plain 路径格式和原代码逐字一致。

- [ ] 全套：

```bash
cd /Users/z/Z/TradeBot && pytest 2>&1 | tail -10
```

Expected: 754 passed + 1 skipped（730 + 24 累计；Task 1-4 = 10+8+2+4 = 24）。

### Step 4.5: Commit

- [ ] Commit:

```bash
cd /Users/z/Z/TradeBot && git add src/agent/tools_perception.py tests/test_tool_enhancement.py && git commit -m "$(cat <<'EOF'
feat(iter2b-T4): get_open_orders OCO 合并渲染 + 抽 _render_single_order helper

同 id 两条 is_algo=True 的 stop+take_profit 合并为一行 [OCO]，显式
提示 "cancel removes both legs" 避免 agent 误以为可以独立 cancel 一腿

单腿 / plain 路径抽 helper 保留逐字输出（label / distance / ID 格式
全部保留，含 current > 0 分支的 ZeroDivisionError 防御）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: `create_order` algo 路由 + 手动构造 algo Order

**Scope item #5a**（spec §2.5.1）— 写路径核心，修复当前 51000 broken 状态。

**Files:**
- Modify: `src/integrations/exchange/okx.py:340-351`（`create_order` 加 algo 路由；行号精确到本方法，不含 :354-358 fetch_order）
- Test: `tests/test_okx_algo_normalization.py`（追加 4 条）

### Step 5.1: 写失败测试 — algo 路由 4 条

- [ ] 追加到 `tests/test_okx_algo_normalization.py`：

```python
# tests/test_okx_algo_normalization.py 追加

@pytest.mark.asyncio
async def test_create_order_stop_adds_stopLossPrice_param():
    ex = _make_okx()
    ex._client.create_order = AsyncMock(return_value={
        "id": "algo_1", "symbol": "BTC/USDT:USDT", "side": "sell",
        "type": "stop", "amount": 1.0, "price": None, "status": "open",
        "info": {"algoId": "algo_1", "clOrdId": "", "tag": ""},
    })
    await ex.create_order("BTC/USDT:USDT", "sell", "stop", 1.0, price=50000.0)
    call = ex._client.create_order.call_args
    params = call.kwargs.get("params") or (call.args[5] if len(call.args) > 5 else None)
    assert params is not None
    assert params.get("tdMode") == "isolated"
    assert params.get("stopLossPrice") == 50000.0


@pytest.mark.asyncio
async def test_create_order_take_profit_adds_takeProfitPrice_param():
    ex = _make_okx()
    ex._client.create_order = AsyncMock(return_value={
        "id": "algo_2", "symbol": "BTC/USDT:USDT", "side": "sell",
        "type": "take_profit", "amount": 1.0, "price": None, "status": "open",
        "info": {"algoId": "algo_2", "clOrdId": "", "tag": ""},
    })
    await ex.create_order("BTC/USDT:USDT", "sell", "take_profit", 1.0, price=80000.0)
    call = ex._client.create_order.call_args
    params = call.kwargs.get("params") or (call.args[5] if len(call.args) > 5 else None)
    assert params.get("takeProfitPrice") == 80000.0
    assert "stopLossPrice" not in params


@pytest.mark.asyncio
async def test_create_order_stop_returns_is_algo_true_with_input_price():
    """Algo create 响应字段稀疏（只含 id/clOrdId/tag），必须手动构造 Order 带 input price。"""
    ex = _make_okx()
    ex._client.create_order = AsyncMock(return_value={
        "id": "algo_3", "info": {"algoId": "algo_3", "clOrdId": "", "tag": ""},
    })
    order = await ex.create_order("BTC/USDT:USDT", "sell", "stop", 1.0, price=50000.0)
    assert order.is_algo is True
    assert order.price == pytest.approx(50000.0)
    assert order.order_type == "stop"
    assert order.status == "open"
    assert order.id == "algo_3"
    assert order.amount == pytest.approx(1.0)
    assert order.side == "sell"


@pytest.mark.asyncio
async def test_create_order_plain_limit_unchanged_regression():
    ex = _make_okx()
    ex._client.create_order = AsyncMock(return_value={
        "id": "plain_1", "symbol": "BTC/USDT:USDT", "side": "buy",
        "type": "limit", "amount": 0.5, "price": 65000.0,
        "status": "open", "fee": None,
    })
    order = await ex.create_order("BTC/USDT:USDT", "buy", "limit", 0.5, price=65000.0)
    call = ex._client.create_order.call_args
    params = call.kwargs.get("params") or (call.args[5] if len(call.args) > 5 else None)
    assert params.get("tdMode") == "isolated"
    assert "stopLossPrice" not in params
    assert "takeProfitPrice" not in params
    assert order.is_algo is False
    assert order.order_type == "limit"
```

### Step 5.2: 运行测试确认失败

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py::test_create_order_stop_adds_stopLossPrice_param tests/test_okx_algo_normalization.py::test_create_order_stop_returns_is_algo_true_with_input_price -v 2>&1 | tail -20
```

Expected: FAIL。当前 `create_order` 不传 params 也不手动构造 algo Order。

### Step 5.3: 实现 — create_order algo 路由

- [ ] 重写 `src/integrations/exchange/okx.py:340-351` 的 `create_order` 方法体（注意**仅**替换 create_order，不触碰其后 fetch_order 的 :354-358）：

```python
@_retry()
async def create_order(  # type: ignore[override]
    self,
    symbol: str,
    side: str,
    order_type: str,
    amount: float,
    price: float | None = None,
) -> Order:
    params = {"tdMode": "isolated"}
    is_algo = order_type in ("stop", "take_profit")
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
        # Algo create 响应仅含 algoId + clOrdId + tag（write-path probe Attempt B
        # dump 确认），缺 slTriggerPx / ordType / stopLossPrice。走 _parse_order
        # 会触发 "both empty → plain fallback"，返回错误的 is_algo=False Order。
        # → 手动构造 Order 绕开该路径。
        return Order(
            id=data["id"],
            symbol=symbol,
            side=side,
            order_type=order_type,
            amount=amount,
            price=price,
            status="open",
            fee=None,
            is_algo=True,
        )
    parsed = self._parse_order(data)
    return parsed[0]
```

### Step 5.4: 跑测试 + 回归

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py -v 2>&1 | tail -30
```

Expected: 18 条（test_okx_algo_normalization 累计；Task 1: 4 + Task 2: 8 + Task 3: 2 + Task 5: 4 = 18）PASS。

- [ ] 全套：

```bash
cd /Users/z/Z/TradeBot && pytest 2>&1 | tail -10
```

Expected: 758 passed + 1 skipped（730 + 28 累计；Task 1-5 = 10+8+2+4+4 = 28）。

### Step 5.5: Commit

- [ ] Commit:

```bash
cd /Users/z/Z/TradeBot && git add src/integrations/exchange/okx.py tests/test_okx_algo_normalization.py && git commit -m "$(cat <<'EOF'
fix(iter2b-T5): create_order algo 路由修复实盘写路径 broken 状态

- order_type in {stop, take_profit} 时自动添加 stopLossPrice/takeProfitPrice
  params（Pre-work Attempt B + E 实测均路由到 OKX algo endpoint）
- 对 algo 响应手动构造 Order 绕开 _parse_order 的 fetch-shape 假设
  （algo create 响应仅含 id/clOrdId/tag）
- Plain limit/market 路径 zero regression（走原 _parse_order[0]）

Fixes 51000 "Parameter ordType error" 阻塞 agent SL/TP 下单

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: `cancel_order` + `fetch_order` + `set_leverage` 适配 + `BaseExchange` 抽象签名

**Scope item #5b**（spec §2.5.2 / §2.5.4 / §2.5.5）— 依赖 Task 2。

**Files:**
- Modify: `src/integrations/exchange/base.py:115`（`cancel_order` 抽象加 `is_algo` 参数）
- Modify: `src/integrations/exchange/okx.py`（`cancel_order` / `fetch_order` / `set_leverage` + 新增 `_is_okx_error_code` helper + `json` import）
- Modify: `src/integrations/exchange/simulated.py:748`（`cancel_order` signature 对齐）
- Test: `tests/test_okx_algo_normalization.py`（追加 6 条）

### Step 6.1: 写失败测试 — cancel_order / fetch_order / set_leverage 6 条

- [ ] 追加到 `tests/test_okx_algo_normalization.py`：

```python
# tests/test_okx_algo_normalization.py 追加

import ccxt.async_support as ccxt_async


@pytest.mark.asyncio
async def test_cancel_order_is_algo_true_passes_stop_params():
    ex = _make_okx()
    ex._client.cancel_order = AsyncMock(return_value=None)
    await ex.cancel_order("algo_123", "BTC/USDT:USDT", is_algo=True)
    call = ex._client.cancel_order.call_args
    params = call.kwargs.get("params") or (call.args[2] if len(call.args) > 2 else None)
    assert params is not None
    assert params.get("stop") is True
    assert params.get("trigger") is True
    assert params.get("algoId") == "algo_123"


@pytest.mark.asyncio
async def test_cancel_order_is_algo_false_plain_call():
    ex = _make_okx()
    ex._client.cancel_order = AsyncMock(return_value=None)
    await ex.cancel_order("plain_123", "BTC/USDT:USDT", is_algo=False)
    call = ex._client.cancel_order.call_args
    assert call.args[:2] == ("plain_123", "BTC/USDT:USDT")
    # 不传 algo params（如果有 params kwargs，必须不含 algoId）
    params = call.kwargs.get("params")
    assert params is None or "algoId" not in params


@pytest.mark.asyncio
async def test_fetch_order_plain_endpoint_first():
    ex = _make_okx()
    ex._client.fetch_order = AsyncMock(return_value={
        "id": "p1", "symbol": "BTC/USDT:USDT", "side": "buy",
        "type": "limit", "amount": 0.5, "price": 65000.0,
        "status": "open", "fee": None,
    })
    await ex.fetch_order("p1", "BTC/USDT:USDT")
    call = ex._client.fetch_order.call_args
    params = call.kwargs.get("params")
    # 第一次调用不传 algo params
    assert params is None or not params.get("stop")


@pytest.mark.asyncio
async def test_fetch_order_falls_back_to_algo_on_50002():
    ex = _make_okx()
    algo_response = {
        "id": "algo_x", "symbol": "BTC/USDT:USDT", "side": "sell",
        "type": "conditional", "amount": 1.0, "price": None,
        "stopLossPrice": 60000.0, "takeProfitPrice": None,
        "status": "open", "fee": None,
        "info": {"ordType": "conditional", "algoId": "algo_x",
                 "slTriggerPx": "60000", "tpTriggerPx": "", "state": "live"},
    }
    err_msg = 'okx {"code":"1","data":[{"sCode":"50002","sMsg":"Incorrect json data format"}],"msg":""}'
    ex._client.fetch_order = AsyncMock(
        side_effect=[ccxt_async.BadRequest(err_msg), algo_response]
    )
    out = await ex.fetch_order("algo_x", "BTC/USDT:USDT")
    assert out.order_type == "stop"
    assert out.is_algo is True
    assert ex._client.fetch_order.call_count == 2
    # 第二次调用必须传 algo params
    second_call = ex._client.fetch_order.call_args_list[1]
    params = second_call.kwargs.get("params")
    assert params is not None
    assert params.get("stop") is True
    assert params.get("algoId") == "algo_x"


@pytest.mark.asyncio
async def test_fetch_order_non_50002_error_propagates():
    ex = _make_okx()
    err_msg = 'okx {"code":"1","data":[{"sCode":"51001","sMsg":"Order does not exist"}],"msg":""}'
    ex._client.fetch_order = AsyncMock(side_effect=ccxt_async.BadRequest(err_msg))
    with pytest.raises(ccxt_async.BadRequest):
        await ex.fetch_order("missing", "BTC/USDT:USDT")
    # 只调用一次，没有 fallback
    assert ex._client.fetch_order.call_count == 1


@pytest.mark.asyncio
async def test_set_leverage_passes_mgnMode_isolated():
    ex = _make_okx()
    ex._client.set_leverage = AsyncMock(return_value=None)
    await ex.set_leverage("BTC/USDT:USDT", 20)
    call = ex._client.set_leverage.call_args
    params = call.kwargs.get("params") or (call.args[2] if len(call.args) > 2 else None)
    assert params is not None
    assert params.get("mgnMode") == "isolated"
    # 单向 posMode 不传 posSide
    assert "posSide" not in params
```

### Step 6.2: 运行测试确认失败

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py::test_cancel_order_is_algo_true_passes_stop_params tests/test_okx_algo_normalization.py::test_fetch_order_falls_back_to_algo_on_50002 tests/test_okx_algo_normalization.py::test_set_leverage_passes_mgnMode_isolated -v 2>&1 | tail -15
```

Expected: FAIL。

### Step 6.3: 实现 — BaseExchange 抽象签名

- [ ] 修改 `src/integrations/exchange/base.py:115`：

```python
    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str, is_algo: bool = False) -> None: ...
```

### Step 6.4: 实现 — OKXExchange cancel_order / fetch_order / set_leverage

- [ ] 在 `src/integrations/exchange/okx.py` 文件顶部 imports 追加 `import json`（若已存在则跳过）。

- [ ] 在 `src/integrations/exchange/okx.py` 替换 `cancel_order` / `fetch_order` / `set_leverage`：

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

@_retry()
async def fetch_order(  # type: ignore[override]
    self, order_id: str, symbol: str | None = None
) -> Order:
    try:
        data = await self._client.fetch_order(order_id, symbol)
    except ccxt.BadRequest as e:
        # OKX 50002 对 algo id 调 plain endpoint 时出现 — fallback 到 algo
        if _is_okx_error_code(e, "50002"):
            data = await self._client.fetch_order(
                order_id, symbol,
                params={"stop": True, "trigger": True, "algoId": order_id},
            )
        else:
            raise
    parsed = self._parse_order(data)
    return parsed[0]

@_retry()
async def set_leverage(self, symbol: str, leverage: int) -> None:  # type: ignore[override]
    await self._client.set_leverage(
        leverage, symbol, params={"mgnMode": "isolated"},
    )
```

- [ ] 在 `src/integrations/exchange/okx.py` 模块级（`_TRIGGER_REASON_MAP` 附近，class 之外）添加 `_is_okx_error_code` helper：

```python
def _is_okx_error_code(err: Exception, code: str) -> bool:
    """Parse OKX sCode from ccxt.BadRequest message envelope.

    Pre-work observed envelope: 'okx {"code":"1","data":[{"sCode":"50002",...}],"msg":""}'
    优先 JSON 解析；降级到 substring 匹配带结构的 sCode field（比纯数字宽松匹配安全）。
    """
    msg = str(err)
    try:
        payload = json.loads(msg.split(None, 1)[1])
        data = payload.get("data") or []
        for item in data:
            if item.get("sCode") == code:
                return True
    except (IndexError, json.JSONDecodeError, AttributeError):
        pass
    return f'"sCode":"{code}"' in msg
```

### Step 6.5: 实现 — SimExchange cancel_order signature 对齐 + FakeExchange subclass 同步

- [ ] 修改 `src/integrations/exchange/simulated.py:748`：

```python
    async def cancel_order(self, order_id: str, symbol: str, is_algo: bool = False) -> None:
        self._validate_symbol(symbol)
        # Sim 不区分 algo 概念；is_algo 参数接收后忽略（满足 BaseExchange LSP）
        async with self._lock:
            # ... existing logic unchanged ...
```

只需在 signature 加参数（`is_algo: bool = False`）+ 末尾加 `# noqa: ARG002` 注释告知 linter 参数故意未用。方法体保持不变：

```python
async def cancel_order(  # noqa: ARG002
    self, order_id: str, symbol: str, is_algo: bool = False,
) -> None:
    self._validate_symbol(symbol)
    # ... existing logic unchanged ...
```

Sim 不区分 algo 概念，参数接收后忽略即可。（若项目未启用 ruff ARG 规则，`# noqa` 可省。）

### Step 6.6: 跑测试 + 回归

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py -v 2>&1 | tail -40
```

Expected: 24 条（test_okx_algo_normalization 累计；Task 1: 4 + 2: 8 + 3: 2 + 5: 4 + 6: 6 = 24）全部 PASS。

- [ ] 全套：

```bash
cd /Users/z/Z/TradeBot && pytest 2>&1 | tail -15
```

Expected: 764 passed + 1 skipped（730 + 34 累计；Task 1-6 = 10+8+2+4+4+6 = 34）。

**已识别的现有 signature 同步点**（`BaseExchange.cancel_order` 抽象加 `is_algo` 后，以下 FakeExchange subclass 需同步 signature 以保持 LSP + mypy 严格模式 clean）：

- `tests/test_tool_enhancement.py:54` FakeExchange subclass 的 `async def cancel_order(self, order_id, symbol): ...` → 改为 `async def cancel_order(self, order_id, symbol, is_algo: bool = False): ...`
- `tests/test_tool_enhancement.py:102` 同上

这两处不是 Task 6 的业务逻辑变更，但 signature 要对齐抽象。改法最小：在两处 cancel_order 方法体上方加 `async def cancel_order(self, order_id, symbol, is_algo: bool = False):  # noqa: ARG002`。实际 runtime behavior 不受影响（旧调用 `cancel_order(id, symbol)` 带默认值兼容），仅对静态检查友好。

如 simulated / OKX 其他相关测试因 signature 新参数 break，仔细查看 —— Sim 内部调用 `self.cancel_order(...)` 的地方不依赖位置参数冲突（`is_algo` 是带默认值的第 3 位置参数）。

### Step 6.7: Commit

- [ ] Commit:

```bash
cd /Users/z/Z/TradeBot && git add src/integrations/exchange/base.py src/integrations/exchange/okx.py src/integrations/exchange/simulated.py tests/test_okx_algo_normalization.py && git commit -m "$(cat <<'EOF'
feat(iter2b-T6): cancel_order / fetch_order / set_leverage + base 抽象签名对齐 algo

- BaseExchange.cancel_order 抽象加 is_algo: bool = False 参数
- OKX cancel_order: is_algo=True 走 /trade/cancel-algos 带 stop+trigger+algoId
- OKX fetch_order: plain-first + 50002 fallback 到 algo（结构化 JSON 解析 sCode）
- OKX set_leverage: 显式 params={mgnMode: isolated} 对齐 create_order 的 tdMode=isolated
- Sim cancel_order signature 对齐（参数接收后忽略）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `tools_execution.py` 三处 `cancel_order` 调用转发 `is_algo`

**Scope item #5c**（spec §2.5.3 — code-review P0-1）— 依赖 Task 2 (Order.is_algo) + Task 6 (OKXExchange.cancel_order is_algo 参数)。

**Files:**
- Modify: `src/agent/tools_execution.py:135-138`（`set_stop_loss` 内部撤旧 SL）
- Modify: `src/agent/tools_execution.py:165-168`（`set_take_profit` 内部撤旧 TP）
- Modify: `src/agent/tools_execution.py:327-354`（agent-facing `cancel_order` tool）
- Test: `tests/test_tool_enhancement.py`（追加 6 条到现有文件末尾；同时修现有 `test_cancel_order_success:720-730` 的 assert，详见 Step 7.4）

### Step 7.1: 写失败测试 — 三处 is_algo 转发 6 条

- [ ] 追加到 `tests/test_tool_enhancement.py` 文件末尾（复用 Task 4 已加入的 imports；helper 改名 `_make_exec_deps` 避免与 Task 4 `_make_oco_deps` / 文件现有 fixture 重名）：

```python
# tests/test_tool_enhancement.py 追加到 Task 4 OCO 测试之后

from src.integrations.exchange.base import Position


def _make_exec_deps(positions=None, open_orders=None, ticker_last=70000.0):
    deps = MagicMock()
    deps.symbol = "BTC/USDT:USDT"
    deps.session_id = "s1"
    deps.db_engine = None
    deps.approval_enabled = False
    deps.approval_gate = None
    deps.exchange = MagicMock()
    deps.exchange.fetch_positions = AsyncMock(return_value=positions or [])
    deps.exchange.fetch_open_orders = AsyncMock(return_value=open_orders or [])
    deps.exchange.cancel_order = AsyncMock(return_value=None)
    deps.exchange.create_order = AsyncMock(return_value=Order(
        id="new_order", symbol="BTC/USDT:USDT", side="sell",
        order_type="stop", amount=1.0, price=60000.0, status="open", is_algo=True,
    ))
    deps.market_data = MagicMock()
    deps.market_data.get_ticker = AsyncMock(return_value=Ticker(
        symbol="BTC/USDT:USDT", last=ticker_last, bid=ticker_last - 1,
        ask=ticker_last + 1, high=ticker_last, low=ticker_last,
        base_volume=0.0, timestamp=0,
    ))
    return deps


def _pos(side="long", contracts=1.0):
    return Position(symbol="BTC/USDT:USDT", side=side, contracts=contracts,
                    entry_price=70000.0, unrealized_pnl=0.0, leverage=10,
                    liquidation_price=None)


@pytest.mark.asyncio
async def test_set_stop_loss_forwards_is_algo_true_for_algo_sl():
    from src.agent.tools_execution import set_stop_loss
    old_sl = Order(id="algo_old", symbol="BTC/USDT:USDT", side="sell",
                   order_type="stop", amount=1.0, price=59000.0,
                   status="open", is_algo=True)
    deps = _make_exec_deps(positions=[_pos()], open_orders=[old_sl])
    await set_stop_loss(deps, price=60000.0, reasoning="tighten")
    call = deps.exchange.cancel_order.call_args
    # cancel_order 要么 is_algo=True via kwargs 要么 位置参数第 3
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is True


@pytest.mark.asyncio
async def test_set_stop_loss_forwards_is_algo_false_for_sim_sl():
    from src.agent.tools_execution import set_stop_loss
    old_sl = Order(id="sim_old", symbol="BTC/USDT:USDT", side="sell",
                   order_type="stop", amount=1.0, price=59000.0,
                   status="open", is_algo=False)
    deps = _make_exec_deps(positions=[_pos()], open_orders=[old_sl])
    await set_stop_loss(deps, price=60000.0, reasoning="tighten")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is False


@pytest.mark.asyncio
async def test_set_take_profit_forwards_is_algo_true_for_algo_tp():
    from src.agent.tools_execution import set_take_profit
    old_tp = Order(id="algo_tp", symbol="BTC/USDT:USDT", side="sell",
                   order_type="take_profit", amount=1.0, price=80000.0,
                   status="open", is_algo=True)
    deps = _make_exec_deps(positions=[_pos()], open_orders=[old_tp])
    await set_take_profit(deps, price=81000.0, reasoning="bump")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is True


@pytest.mark.asyncio
async def test_set_take_profit_forwards_is_algo_false_for_sim_tp():
    from src.agent.tools_execution import set_take_profit
    old_tp = Order(id="sim_tp", symbol="BTC/USDT:USDT", side="sell",
                   order_type="take_profit", amount=1.0, price=80000.0,
                   status="open", is_algo=False)
    deps = _make_exec_deps(positions=[_pos()], open_orders=[old_tp])
    await set_take_profit(deps, price=81000.0, reasoning="bump")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is False


@pytest.mark.asyncio
async def test_cancel_order_tool_routes_is_algo_true_for_algo_order():
    from src.agent.tools_execution import cancel_order
    target = Order(id="algo_xyz", symbol="BTC/USDT:USDT", side="sell",
                   order_type="stop", amount=1.0, price=60000.0,
                   status="open", is_algo=True)
    deps = _make_exec_deps(open_orders=[target])
    await cancel_order(deps, order_id="algo_xyz", reasoning="stale")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is True


@pytest.mark.asyncio
async def test_cancel_order_tool_routes_is_algo_false_for_plain_order():
    from src.agent.tools_execution import cancel_order
    target = Order(id="plain_abc", symbol="BTC/USDT:USDT", side="buy",
                   order_type="limit", amount=0.5, price=65000.0,
                   status="open", is_algo=False)
    deps = _make_exec_deps(open_orders=[target])
    await cancel_order(deps, order_id="plain_abc", reasoning="stale")
    call = deps.exchange.cancel_order.call_args
    is_algo_actual = (
        call.kwargs.get("is_algo") if "is_algo" in call.kwargs
        else (call.args[2] if len(call.args) > 2 else False)
    )
    assert is_algo_actual is False
```

### Step 7.2: 运行测试确认失败

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_tool_enhancement.py::test_set_stop_loss_forwards_is_algo_true_for_algo_sl tests/test_tool_enhancement.py::test_cancel_order_tool_routes_is_algo_true_for_algo_order -v 2>&1 | tail -20
```

Expected: FAIL。当前三处都不传 `is_algo`。

### Step 7.3: 实现 — tools_execution.py 三处转发

- [ ] 修改 `src/agent/tools_execution.py:135-138`（`set_stop_loss` 内部 for loop）：

```python
    # Cancel existing stop orders
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "stop":
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)
```

- [ ] 修改 `src/agent/tools_execution.py:165-168`（`set_take_profit` 内部 for loop）：

```python
    # Cancel existing take profit orders
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    for o in open_orders:
        if o.order_type == "take_profit":
            await deps.exchange.cancel_order(o.id, deps.symbol, is_algo=o.is_algo)
```

- [ ] 修改 `src/agent/tools_execution.py:327-354`（`cancel_order` agent-facing tool），替换：

```python
async def cancel_order(
    deps: TradingDeps,
    order_id: str,
    reasoning: str,
) -> str:
    """Cancel a pending order (limit, stop, take_profit)."""
    open_orders = await deps.exchange.fetch_open_orders(deps.symbol)
    target = None
    for o in open_orders:
        if o.id == order_id:
            target = o
            break

    if target is None:
        return f"Order not found or already filled: {order_id}"

    if target.order_type == "market":
        return "Cannot cancel market orders"

    await deps.exchange.cancel_order(order_id, deps.symbol, is_algo=target.is_algo)

    await _record_action(
        deps, action="cancel_order", order_id=order_id,
        side=target.side, price=target.price, reasoning=reasoning,
    )

    price_str = f" @ {target.price:.2f}" if target.price is not None else ""
    return f"Order cancelled: {target.order_type} {target.side} {target.amount:.6f}{price_str} | ID: {order_id}"
```

**⚠️ Plan vs spec 偏离（已评估接受）**：spec §2.5.3 "失败情况（stale id）" 段描述 `target=None → is_algo=False → 走 plain cancel → 50002 → 异常传播`。但**现有代码**（`tools_execution.py:340-341`）的实际行为是 `target=None → 早退返回 "Order not found or already filled"`，**不**调 `cancel_order`。Plan 选择**沿用现有代码早退行为**，理由：(1) 避免对 stale id 发无意义 API 调用 + (2) 避免 agent 拿到 50002 噪音异常，当前 "Order not found" 早退对 agent 更清晰。Spec §2.5.3 该段文字需后续修，但**不阻塞本 PR**。

### Step 7.4: 跑测试 + 回归

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_tool_enhancement.py tests/test_tools.py -v 2>&1 | tail -30
```

Expected: 6 条新 PASS。

**必改的现有 assertion（Task 7 内部实施的 cancel_order signature 添加 kwarg 后，以下测试原 assert 会 fail，必须同步更新）**：

- [ ] `tests/test_tools.py:142` — `test_set_stop_loss_cancels_existing`：

  原文：
  ```python
  deps.exchange.cancel_order.assert_called_once_with("old-sl", "BTC/USDT:USDT")
  ```
  改为（Task 7 实施后 cancel_order 多 `is_algo=False` kwarg，因为测试 mock 的旧 order 默认 `is_algo=False`）：
  ```python
  deps.exchange.cancel_order.assert_called_once_with("old-sl", "BTC/USDT:USDT", is_algo=False)
  ```

- [ ] `tests/test_tool_enhancement.py:730` — `test_cancel_order_success`：

  原文：
  ```python
  deps.exchange.cancel_order.assert_called_once_with("o1", "BTC/USDT:USDT")
  ```
  改为：
  ```python
  deps.exchange.cancel_order.assert_called_once_with("o1", "BTC/USDT:USDT", is_algo=False)
  ```
  （测试里的 Order fixture 应确保 `is_algo=False` 默认，即无 algo 场景；若 fixture 已带 `is_algo` 默认字段则 kwarg 值自动为 `False`）。

**可能附加的小修**：若 `test_set_take_profit*` 类测试也 mock 了 `cancel_order.assert_called_*`，同样加 `is_algo=False` kwarg。

- [ ] 全套：

```bash
cd /Users/z/Z/TradeBot && pytest 2>&1 | tail -10
```

Expected: 770 passed + 1 skipped（730 + 40 累计；Task 1-7 = 10+8+2+4+4+6+6 = 40）。

### Step 7.5: Commit

- [ ] Commit:

```bash
cd /Users/z/Z/TradeBot && git add src/agent/tools_execution.py tests/test_tool_enhancement.py tests/test_tools.py && git commit -m "$(cat <<'EOF'
fix(iter2b-T7): tools_execution 三处 cancel_order 调用转发 is_algo（P0-1）

- set_stop_loss / set_take_profit 内部撤旧单时从 Order.is_algo 读取
- agent-facing cancel_order tool 从 fetch_open_orders 查到的 target 读取

实盘归一化后 SL/TP 几乎总是 is_algo=True；漏任一处 cancel 都会 50002 BadRequest
让 SL/TP 替换主路径 broken

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: `start()` posMode + acctLv 校验 + ws_client sandbox + `_watch_orders_loop` 诊断 log

**Scope item #6**（spec §2.6 + §3.1.3 diagnostic log）— 依赖 Task 1（`self._sandbox` 已存）。与 Task 2-7 不冲突，可与 Task 2 并行。

**Files:**
- Modify: `src/integrations/exchange/okx.py:118-144`（`start()`）
- Modify: `src/integrations/exchange/okx.py:148-186`（`_watch_orders_loop` 循环顶部加诊断 log）
- Test: `tests/test_okx_algo_normalization.py`（追加 8 条：4 × acctLv/posMode 校验 + 2 × ws sandbox + 1 × REST-only 降级 + 1 × 诊断 log 双分支）+ `tests/test_okx_websocket.py`（修现有 1 条 `test_okx_start_fallback_to_rest_on_ws_failure` 加 `private_get_account_config` mock）

### Step 8.1: 写失败测试 — start() + ws sandbox + diagnostic log 8 条

- [ ] 追加到 `tests/test_okx_algo_normalization.py`：

```python
# tests/test_okx_algo_normalization.py 追加

@pytest.mark.asyncio
async def test_start_rejects_long_short_posMode():
    ex = _make_okx()
    ex._client.load_markets = AsyncMock(return_value={})
    ex._client.private_get_account_config = AsyncMock(return_value={
        "data": [{"posMode": "long_short_mode", "acctLv": "2"}],
    })
    with pytest.raises(RuntimeError, match="posMode"):
        await ex.start()


@pytest.mark.asyncio
async def test_start_rejects_multi_currency_acctLv():
    ex = _make_okx()
    ex._client.load_markets = AsyncMock(return_value={})
    ex._client.private_get_account_config = AsyncMock(return_value={
        "data": [{"posMode": "net_mode", "acctLv": "3"}],
    })
    with pytest.raises(RuntimeError, match="acctLv"):
        await ex.start()


@pytest.mark.asyncio
async def test_start_rejects_simple_acctLv():
    ex = _make_okx()
    ex._client.load_markets = AsyncMock(return_value={})
    ex._client.private_get_account_config = AsyncMock(return_value={
        "data": [{"posMode": "net_mode", "acctLv": "1"}],
    })
    with pytest.raises(RuntimeError, match="acctLv"):
        await ex.start()


@pytest.mark.asyncio
async def test_start_rejects_portfolio_margin_acctLv():
    """acctLv=4 (Portfolio Margin) — margin 语义与系统 isolated 假设不兼容（spec §0.2 强调）。"""
    ex = _make_okx()
    ex._client.load_markets = AsyncMock(return_value={})
    ex._client.private_get_account_config = AsyncMock(return_value={
        "data": [{"posMode": "net_mode", "acctLv": "4"}],
    })
    with pytest.raises(RuntimeError, match="acctLv"):
        await ex.start()


@pytest.mark.asyncio
async def test_start_accepts_net_mode_single_currency():
    """Pre-work 实测配置 posMode=net_mode + acctLv=2."""
    ex = _make_okx()
    ex._client.load_markets = AsyncMock(return_value={})
    ex._client.private_get_account_config = AsyncMock(return_value={
        "data": [{"posMode": "net_mode", "acctLv": "2"}],
    })
    # ccxtpro.okx 构造抛错 → 走 REST-only except 分支
    # 注意：patch("ccxt.pro.okx") 直接替换属性，不依赖 sys.modules 缓存
    with patch("ccxt.pro.okx", side_effect=ImportError("mocked absence")):
        await ex.start()  # 不应 raise
    assert ex._ws_connected is False


@pytest.mark.asyncio
async def test_start_with_sandbox_true_calls_set_sandbox_mode_on_ws_client():
    ex = _make_okx(sandbox=True)
    ex._client.load_markets = AsyncMock(return_value={})
    ex._client.private_get_account_config = AsyncMock(return_value={
        "data": [{"posMode": "net_mode", "acctLv": "2"}],
    })
    fake_ws = MagicMock()
    fake_ws.watch_orders = AsyncMock(side_effect=asyncio.CancelledError)
    fake_ws.watch_ticker = AsyncMock(side_effect=asyncio.CancelledError)
    with patch("ccxt.pro.okx", return_value=fake_ws):
        await ex.start()
        # watch tasks 被创建，即刻 cancel 避免挂起
        for attr in ("_orders_task", "_ticker_task"):
            t = getattr(ex, attr, None)
            if t is not None:
                t.cancel()
    fake_ws.set_sandbox_mode.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_start_with_sandbox_false_ws_client_stays_live():
    ex = _make_okx(sandbox=False)
    ex._client.load_markets = AsyncMock(return_value={})
    ex._client.private_get_account_config = AsyncMock(return_value={
        "data": [{"posMode": "net_mode", "acctLv": "2"}],
    })
    fake_ws = MagicMock()
    fake_ws.watch_orders = AsyncMock(side_effect=asyncio.CancelledError)
    fake_ws.watch_ticker = AsyncMock(side_effect=asyncio.CancelledError)
    with patch("ccxt.pro.okx", return_value=fake_ws):
        await ex.start()
        for attr in ("_orders_task", "_ticker_task"):
            t = getattr(ex, attr, None)
            if t is not None:
                t.cancel()
    fake_ws.set_sandbox_mode.assert_not_called()


@pytest.mark.asyncio
async def test_watch_orders_loop_emits_algo_lineage_log_for_both_guard_branches(caplog):
    """诊断 log guard 双分支覆盖：
      假设 A: info.ordType ∈ {conditional, oco}
      假设 B: info.algoId 非空（即使 ordType 是 market/limit）
    首次 OCO 触发前后都能从日志反推事件 shape。"""
    ex = _make_okx()
    ex._running = True
    ex._ws_client = MagicMock()
    # 两个事件：各触发一个 guard 分支
    event_a = {
        "id": "evt_a", "status": "open", "filled": 0,
        "info": {"ordType": "conditional", "state": "live", "algoId": "algo_a"},
    }
    event_b = {
        "id": "evt_b", "status": "open", "filled": 0,
        "info": {"ordType": "market", "state": "live", "algoId": "algo_b"},
    }
    calls = iter([[event_a, event_b], asyncio.CancelledError()])

    async def fake_watch(symbol):
        nxt = next(calls)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    ex._ws_client.watch_orders = fake_watch
    with caplog.at_level("INFO"):
        try:
            await ex._watch_orders_loop()
        except asyncio.CancelledError:
            pass
    log_lines = [r.message for r in caplog.records if "algo-lineage" in r.message]
    assert len(log_lines) >= 2  # 两个事件都 log
    combined = "\n".join(log_lines)
    assert "algo_a" in combined and "algo_b" in combined
    assert "conditional" in combined  # 假设 A 字段
    # 5 字段完整性抽查
    assert "raw_ordType" in combined and "raw_state" in combined and "unified_status" in combined
```

### Step 8.2: 运行测试确认失败

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py::test_start_rejects_long_short_posMode tests/test_okx_algo_normalization.py::test_start_with_sandbox_true_calls_set_sandbox_mode_on_ws_client -v 2>&1 | tail -20
```

Expected: FAIL。当前 `start()` 不做账户校验；ws_client 不调 `set_sandbox_mode`。

### Step 8.3: 实现 — start() 账户校验 + ws sandbox

- [ ] 重写 `src/integrations/exchange/okx.py:118-144` 的 `start()`：

```python
async def start(self) -> None:
    """Preload markets + validate account config + start WebSocket."""
    await self._client.load_markets()

    # 账户配置 fail-fast — 在 WebSocket 之前，失败不浪费连接
    config_resp = await self._client.private_get_account_config()
    config = (config_resp.get("data") or [{}])[0]

    pos_mode = config.get("posMode")
    if pos_mode != "net_mode":
        raise RuntimeError(
            f"OKX account posMode={pos_mode!r}, system expects 'net_mode' (one-way). "
            f"System 全栈假设单向仓位；改动代价指数级。"
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

    try:
        import ccxt.pro as ccxtpro
        self._ws_client = ccxtpro.okx({
            "apiKey": self._client.apiKey,
            "secret": self._client.secret,
            "password": self._client.password,
            "options": {"defaultType": "swap"},
        })
        # CRITICAL: sync sandbox to WS client — 漏调 = REST→demo / WS→live 跨账户污染
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

### Step 8.4: 实现 — _watch_orders_loop 诊断 log

- [ ] 修改 `src/integrations/exchange/okx.py:148-186`，在 `for order_data in orders:` 循环顶部添加诊断 log：

```python
async def _watch_orders_loop(self) -> None:
    error_count = 0
    while self._running:
        try:
            orders = await self._ws_client.watch_orders(self._symbol)
            error_count = 0
            for order_data in orders:
                info = order_data.get("info") or {}
                # Algo-lineage 诊断 log — guard 双分支覆盖假设 A/B
                if (info.get("ordType") in ("conditional", "oco")
                        or info.get("algoId") not in (None, "")):
                    logger.info(
                        "algo-lineage raw event: raw_ordType=%s raw_state=%s "
                        "unified_status=%s id=%s algoId=%s",
                        info.get("ordType"), info.get("state"),
                        order_data.get("status"), order_data.get("id"),
                        info.get("algoId"),
                    )

                status = order_data.get("status")
                filled = order_data.get("filled", 0) or 0

                if status == "closed":
                    order_id = order_data.get("id")
                    if order_id in self._seen_order_ids:
                        logger.debug("Skipping duplicate order %s", order_id)
                        continue
                    self._seen_order_ids[order_id] = None
                    if len(self._seen_order_ids) > self._seen_order_ids_max:
                        keys = list(self._seen_order_ids)
                        for k in keys[:len(keys) // 2]:
                            del self._seen_order_ids[k]
                    fill_event = await self._parse_fill_event(order_data)
                    if self._fill_callback:
                        try:
                            await self._fill_callback(fill_event)
                        except Exception:
                            logger.exception("Fill callback failed for order %s", order_data.get("id"))
                elif filled > 0 and status != "closed":
                    logger.warning(
                        "Partial fill detected: order %s filled=%s status=%s (not processing)",
                        order_data.get("id"), filled, status,
                    )
        except asyncio.CancelledError:
            break
        except Exception:
            error_count += 1
            delay = min(5 * (2 ** (error_count - 1)), 60)
            logger.error("watch_orders error (retry in %ds)", delay, exc_info=True)
            await asyncio.sleep(delay)
```

### Step 8.5: 跑测试 + 回归

- [ ] Run:

```bash
cd /Users/z/Z/TradeBot && pytest tests/test_okx_algo_normalization.py -v 2>&1 | tail -40
```

Expected: 32 条 PASS + 1 条 SKIPPED（test_okx_algo_normalization 累计；Task 1: 4 init+wiring + Task 2: 8 + Task 3: 2 passed + Task 5: 4 + Task 6: 6 + Task 8: 8 = 32 passed；Task 3 advisory skip 计 1 skipped）。

- [ ] 全套：

```bash
cd /Users/z/Z/TradeBot && pytest 2>&1 | tail -15
```

Expected: 778 passed + 1 skipped（730 + 48 累计；Task 1-8 = 10+8+2+4+4+6+6+8 = 48 passed；Task 3 advisory skip）。

**已量化的 `tests/test_okx_websocket.py` 影响**：该文件 24 个测试中仅 **1 条** 直接调 `exchange.start()` —— `test_okx_start_fallback_to_rest_on_ws_failure`（约 `tests/test_okx_websocket.py:8-21`）。预检命令（subagent 执行前运行确认）：

```bash
cd /Users/z/Z/TradeBot && grep -En "await.*\.start\(\)|\.start\(\)" tests/test_okx_websocket.py
```

Expected 输出：只有 1 行命中（line 21 的 `await exchange.start()` 在 `test_okx_start_fallback_to_rest_on_ws_failure` 内）。其他测试走 `_watch_orders_loop` / `_parse_fill_event` / `close` 等独立方法，**不经** `start()` — 不受影响。若此 grep 命中 ≠ 1，停下来重新量化再继续。

此测试 mock 了 `load_markets` 但 **没 mock** `private_get_account_config`，Task 8 后会因 account config 未 mock 而 fail。修法：

```python
# tests/test_okx_websocket.py:18 前后，在 load_markets mock 旁边追加
exchange._client.private_get_account_config = AsyncMock(return_value={
    "data": [{"posMode": "net_mode", "acctLv": "2"}],
})
```

其他 23 个测试（`_watch_orders_loop` / `_parse_fill_event` / `_watch_ticker_loop` / `close` / 别名 callback 等）都直接调独立方法，不经 `start()` — **不受影响**。Task 8 Step 8.5 回归时只需确认这 1 条修好，其余不动。

### Step 8.6: Commit

- [ ] Commit:

```bash
cd /Users/z/Z/TradeBot && git add src/integrations/exchange/okx.py tests/test_okx_algo_normalization.py tests/test_okx_websocket.py && git commit -m "$(cat <<'EOF'
feat(iter2b-T8): start() 账户模式 fail-fast + ws_client sandbox 同步 + 诊断 log

- start() 在 load_markets 后、WS 之前调 private_get_account_config
  校验 posMode=net_mode + acctLv=2；失败 RuntimeError 带人工操作指引
- ws_client 创建后立即 set_sandbox_mode(sandbox) 防 REST→demo/WS→live
  跨账户事件污染（demo 永远收不到 fill）
- _watch_orders_loop 循环顶部加 algo-lineage 诊断 log（guard 双分支
  覆盖假设 A/B），首次真实 OCO 触发时可反推事件 shape

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: persona.py Layer 1 OCO 原子性 + 端到端 demo 冒烟

**Scope item #4 收尾 + acceptance §5.4**。

**Files:**
- Modify: `src/agent/persona.py:48`（Layer 1 最后一个 bullet 之后追加 OCO 原子性说明）
- Create: `scripts/iter2b_smoke_test.py`（手工跑的端到端脚本；非 CI）

### Step 9.1: persona.py 追加 OCO bullet

- [ ] 修改 `src/agent/persona.py:48`，在"Position risk context" bullet 之后追加一行：

```python
- **Position risk context**: get_position now includes Risk exposure (notional / margin / liquidation in ATR(1h) multiples — 1h is the fixed baseline regardless of session trading style) and Exit orders section (SL/TP distances from both entry and current). Useful both when opening and during ongoing position management.
- **OCO atomicity on OKX**: stop and take_profit orders that share an algoId (rendered as `[OCO]` in get_open_orders) are atomic — cancelling or triggering one leg removes both. If you intend to replace only one leg, re-create the other leg immediately after."""
```

### Step 9.2: 创建 smoke test 脚本

- [ ] 新建 `scripts/iter2b_smoke_test.py`（~80 行），内容：

```python
"""Iter 2b end-to-end smoke test — 手跑，非 CI.

在 OKX demo 账户验收闭环：
  1. 查余额（§5.4 USDT gate）
  2. 下 conditional SL → fetch_open_orders 能看到 → cancel
  3. 下 OCO → fetch_open_orders 看到合并行 → cancel

Usage:
  cp .env.example .env  # 填 OKX_DEMO_* 后
  OKX_SANDBOX=true python scripts/iter2b_smoke_test.py

Exit codes:
  0 — full smoke passed
  1 — env guard failed (OKX_SANDBOX != 'true')
  2 — balance gate failed (USDT <= 0)
  3 — SKIP: no open position (open small demo position in web UI, then re-run)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure repo root on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.integrations.exchange.okx import OKXExchange


async def main() -> int:
    load_dotenv(".env")
    if os.environ.get("OKX_SANDBOX", "").lower() != "true":
        print("ABORT: OKX_SANDBOX must be 'true' for smoke test")
        return 1

    ex = OKXExchange(
        api_key=os.environ["OKX_DEMO_API_KEY"],
        secret=os.environ["OKX_DEMO_SECRET"],
        password=os.environ["OKX_DEMO_PASSWORD"],
        symbol="BTC/USDT:USDT",
        sandbox=True,
    )

    try:
        # 1. start + balance gate
        await ex.start()
        bal = await ex.fetch_balance()
        print(f"[OK] balance USDT total={bal.total_usdt:.2f} free={bal.free_usdt:.2f}")
        if bal.total_usdt <= 0:
            print("ABORT: USDT=0 (see spec §5.4 auto_transfers_ccy risk)")
            return 2

        # 1.1 Dump idle balance fixture for spec §7.1 USDT/USDC auto-conversion 观察期对比
        import json
        raw_bal = await ex._client.fetch_balance()
        fixture_path = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "okx_fetch_balance_idle.json"
        fixture_path.write_text(json.dumps(
            {"total": raw_bal.get("total", {}), "free": raw_bal.get("free", {}),
             "used": raw_bal.get("used", {})},
            indent=2,
        ))
        print(f"[OK] idle balance fixture dumped → {fixture_path.name}")

        # 2. conditional SL round-trip
        positions = await ex.fetch_positions("BTC/USDT:USDT")
        if not positions:
            print("[SKIP] no open position — smoke only verifies SL/TP when a position exists.")
            print("       Open a small demo position via OKX web, then re-run.")
            return 3  # skip distinct from 0 (success) to avoid misread in shell

        p = positions[0]
        side = "sell" if p.side == "long" else "buy"
        trigger_px = p.entry_price * (0.97 if p.side == "long" else 1.03)
        sl = await ex.create_order("BTC/USDT:USDT", side, "stop", p.contracts, price=trigger_px)
        print(f"[OK] SL created: id={sl.id} is_algo={sl.is_algo}")
        assert sl.is_algo, "SL must be is_algo=True on OKX live"
        opens = await ex.fetch_open_orders("BTC/USDT:USDT")
        assert any(o.id == sl.id for o in opens), "SL not in fetch_open_orders"
        print(f"[OK] SL visible in fetch_open_orders ({len(opens)} total)")
        await ex.cancel_order(sl.id, "BTC/USDT:USDT", is_algo=True)
        print(f"[OK] SL cancelled")

        # 3. OCO round-trip (use direct raw to place OCO, since create_order 本 task 不 支持 OCO；
        #    这一步验证 fetch/render/cancel 闭环，下单用底层 private API)
        tp_px = p.entry_price * (1.1 if p.side == "long" else 0.9)
        sl_px = p.entry_price * (0.9 if p.side == "long" else 1.1)
        oco_resp = await ex._client.private_post_trade_order_algo({
            "instId": "BTC-USDT-SWAP",
            "tdMode": "isolated",
            "side": side,
            "ordType": "oco",
            "sz": str(p.contracts),
            "slTriggerPx": str(sl_px),
            "slOrdPx": "-1",
            "tpTriggerPx": str(tp_px),
            "tpOrdPx": "-1",
        })
        algo_id = oco_resp["data"][0]["algoId"]
        print(f"[OK] OCO placed: algoId={algo_id}")
        opens2 = await ex.fetch_open_orders("BTC/USDT:USDT")
        oco_legs = [o for o in opens2 if o.id == algo_id]
        assert len(oco_legs) == 2, f"OCO should render as 2 legs, got {len(oco_legs)}"
        types = {o.order_type for o in oco_legs}
        assert types == {"stop", "take_profit"}, f"unexpected types: {types}"
        print(f"[OK] OCO renders as 2 Orders sharing id, types={types}")
        await ex.cancel_order(algo_id, "BTC/USDT:USDT", is_algo=True)
        print(f"[OK] OCO cancelled (atomic: both legs gone)")

        print("\n[SUCCESS] Iter 2b smoke test passed")
        return 0
    finally:
        await ex.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

### Step 9.3: 本地 lint / 格式检查

- [ ] 确保 smoke 脚本无语法错 & imports 工作：

```bash
cd /Users/z/Z/TradeBot && python -c "import ast; ast.parse(open('scripts/iter2b_smoke_test.py').read())" && echo "syntax OK"
```

Expected: `syntax OK`。

### Step 9.4: 手跑 smoke test（等 user 决定是否在 demo 上跑）

- [ ] 本步骤**不在 subagent 实施范围**；user 在 PR review 期间运行：

```bash
cd /Users/z/Z/TradeBot && python scripts/iter2b_smoke_test.py
```

Expected（exit code 意义）：
- `0 + [SUCCESS]`：完整 smoke 通过
- `3 + [SKIP]`：demo 无 position，基础设施 OK 但未实测（user 需开 demo 仓位重跑）
- `1`：OKX_SANDBOX guard 失败（环境变量问题）
- `2`：USDT 余额为 0（§5.4 auto_transfers_ccy 风险）
- 其他非 0 + stack trace：真失败，subagent 根据错误调整代码

**若无法在 demo 跑（退 3 SKIP）**，smoke 脚本入仓代表基础设施就绪，实跑留给 user。

### Step 9.5: 回归

- [ ] Run 全套：

```bash
cd /Users/z/Z/TradeBot && pytest 2>&1 | tail -10
```

Expected: 778 passed + 1 skipped（Task 9 不新增测试 —— persona.py 改动不应 break 任何测试；smoke script 非 pytest 测试）。

### Step 9.6: Commit

- [ ] Commit:

```bash
cd /Users/z/Z/TradeBot && git add src/agent/persona.py scripts/iter2b_smoke_test.py && git commit -m "$(cat <<'EOF'
feat(iter2b-T9): persona Layer 1 OCO 原子性说明 + 端到端 demo 冒烟脚本

- Layer 1 Tool Usage Notes 追加一行：OCO 同 algoId 两腿原子，cancel/trigger
  任一腿移除两腿；若仅替换一腿需紧接重建对腿
- scripts/iter2b_smoke_test.py 验证 SL 下单/查看/cancel + OCO 查看
  合并渲染/原子 cancel 闭环（手跑，非 CI）

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Memory 更新 + PR

**Files:**
- Modify: `/Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/project_tradebot_status.md`（标记 Iter 2b landed）
- Modify: `/Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/project_pre_observation_iterations.md`（进度更新）
- Modify: `/Users/z/.claude/projects/-Users-z-Z-TradeBot/memory/MEMORY.md`（index 同步）

### Step 10.1: 更新 memory（允许 subagent 直接写）

Memory 文件在 `~/.claude/projects/.../memory/` 不在 repo 内，subagent 可直接修改；这些不是"重要产出物（计划/设计文档）"范畴。

- [ ] 读 `project_tradebot_status.md` 当前内容，更新 merged PR count + Iter 2b 状态：合入 Iter 2b PR 后 merged PR count 22 → 23（实际 PR number 由 GitHub 分配，文字里用"merged 数"而非"PR #"以避免混淆）；Iter 2b 状态从 "Pre-work/Spec 已 commit" 改为 "landed"，附对应 commit hash 范围（Task 1-9 共 9 commits，加原来 2 个 Pre-work/spec = 11 commits）。

- [ ] 读 `project_pre_observation_iterations.md`，把 Iter 2b 标 ✅ landed，更新下一步指向 Iter 3。

- [ ] 读 `MEMORY.md`，如 `project_iter2b_okx_algo_normalization` 或 `project_iter2b_review_digest` 有任何 outdated 描述调整。

### Step 10.2: 草拟 spec 追加 patch —— **等 user 审阅，不要自动 commit**

Spec 是已 commit 的重要产出物，遵循 `feedback_review_before_commit.md`：subagent **只生成 patch 草稿**，由 user 审阅 + 自己粘贴到 spec 文件。

- [ ] 在 PR comment / session 输出里把下面 4 处 patch 文字完整贴出（**不要**直接修改 `docs/superpowers/specs/2026-04-24-iter2b-okx-live-hardening.md`）：

**Patch A — §7.1 观察期 follow-up 追加（P2-9 fetch_order OCO edge case）**：

```
在 §7.1 表格末尾追加一行：
| **`fetch_order` OCO 单条返回 vs 完整两腿** | `fetch_order(algo_id)` fallback 到 algo endpoint 后若 id 对应 OCO，`_parse_order(data)` 返 `[stop, take_profit]`；现有 `parsed[0]` 只返 stop 腿，take_profit 腿被丢弃（§2.5.4 当前 default）。若观察期 `get_trade_journal` 出现"OCO 触发后 journal 只显示一腿详情"模式，改 `fetch_order` 返 `list[Order]` 或 journal 查询侧处理两腿展示 |
```

**Patch B — §6 acceptance 测试数字校准**：

```
把 §6 acceptance 最后一条：
- [ ] 新增测试全部通过（…）；730 测试 baseline 无 regression；Iter 2b **新增约 32 条** + **修改约 5 条现有**（…），合计 pytest 测试数约 **762**

改为：
- [ ] 新增测试全部通过（…）；730 测试 baseline 无 regression；Iter 2b **新增 48 passed + 1 advisory skipped** + 修改若干条 signature（…），合计 pytest **778 passed + 1 skipped**（plan 展开后精确数，起草期"约 762"估算偏差；advisory skip = fetch_open_orders_concurrent_not_serial 占位，§5.2/§6 已注明 CCXT 版本敏感不作 merge gate）
```

**Patch C — §0.2 Exit orders 引用行号 stale（M1）**：

```
§0.2 第 2 点原文写 `tools_perception.py:245-251`，实际 Exit orders 块在
`tools_perception.py:253-293`（Iter 2 toolkit 展开后）。行号偏差不影响 Iter 2b
实施，但未来读 spec 索引不准。建议调整为 `tools_perception.py:253-293`。
```

**Patch D — §2.5.4 `_parse_fill_event` pnl fallback 行号 stale（M3）**：

```
§2.5.4 ⚠️ 说明原文写 "okx.py:264 的 pnl fallback 直接调 self._client.fetch_order"，
实际 pnl fallback 从 `okx.py:263` 起（含 try 行）。1 行偏差，非关键。
建议调整为 "okx.py:263-271"（完整 fallback 块）。
```

- [ ] 告诉 user："Spec 有 4 处建议 patch（A/B/C/D），已贴在上面，请你审阅后自己追加到 `docs/superpowers/specs/2026-04-24-iter2b-okx-live-hardening.md` 并另起一个 commit（符合 `feedback_review_before_commit`）。Iter 2b 主 PR 不含这 4 处 spec 修订。"

### Step 10.3: 推分支 + 创建 PR

- [ ] 汇总所有新 commits：

```bash
cd /Users/z/Z/TradeBot && git log --oneline main..HEAD
```

Expected: 约 11 条（2 个 Pre-work/spec + 9 个 Task 1-9；Task 10 memory 更新不提交到 repo，故不计）。

- [ ] Push branch（**先与 user 确认可以 push**；违反 `feedback_git_branch.md` 会导致意外公开）：

```bash
cd /Users/z/Z/TradeBot && git push -u origin iter2b/okx-live-hardening
```

- [ ] 创建 PR：

```bash
cd /Users/z/Z/TradeBot && gh pr create --title "Iter 2b: OKX live hardening — algo normalization + account guards" --body "$(cat <<'EOF'
## Summary

- 修复 OKX Exchange 对 algo 订单（conditional SL / OCO）的读/写路径断裂
- 加账户模式 fail-fast 护栏（posMode=net_mode + acctLv=2 + ws_client sandbox）
- sandbox 配置化 + OKX_DEMO_* credentials 分流

## 6 项 scope（spec §1.1）

1. sandboxMode 配置化 + OKX_DEMO_* credentials 分流 + app.py call-site wiring
2. `_parse_order` algo 归一化（conditional 单腿 + OCO 双腿拆分；is_algo 字段）
3. `fetch_open_orders` 三路 `asyncio.gather` 合并（plain + conditional + oco）
4. `get_open_orders` OCO 合并展示（同 algoId 两条 → 一行）
5. Execution layer 硬化（create_order algo 路由 + cancel_order is_algo + fetch_order 50002 fallback + set_leverage mgnMode=isolated + tools_execution 三处 is_algo 转发）
6. `OKXExchange.start()` posMode + acctLv + ws_client sandbox 三重校验

## Test plan

- [ ] `pytest` 全套通过（778 passed + 1 skipped，730 baseline + 48 新增 passed + 1 advisory skip）
- [ ] `scripts/iter2b_smoke_test.py` 在 demo 账户手跑 exit code = 0（或 exit 3 SKIP 代表无 position）
- [ ] 手工验证 `.env.example` 更新的 `OKX_DEMO_*` / `OKX_SANDBOX` 注释可读
- [ ] 手工确认 `src/cli/app.py:261-264` 透传 sandbox（grep `sandbox=settings.exchange.sandbox`）
- [ ] `OKXExchange.__init__` sandbox=False + api_key 非空时 log warning（spec §2.1.4）

## 参考文档

- Spec: `docs/superpowers/specs/2026-04-24-iter2b-okx-live-hardening.md`
- Plan: `docs/superpowers/plans/2026-04-24-iter2b-okx-live-hardening.md`
- Pre-work fixtures: `tests/fixtures/okx_*.json`
- Pre-work scripts: `scripts/iter2b_sample_okx_algo_orders.py` / `scripts/iter2b_write_path_probe.py`

## 观察期 follow-up（spec §7.1）

合入后需观察的 10+ 项（首次 OCO 触发事件 shape、USDT/USDC auto-conversion、rate limit 命中等），详见 spec §7.1。

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 10.4: 回报 PR URL + spec patch 草稿

- [ ] 把 `gh pr create` 输出的 PR URL 记录下来，交给 user。
- [ ] 再次提醒 user Step 10.2 的 4 处 spec patch 草稿（Patch A/B/C/D）需要审阅 + 单独 commit。
- [ ] Memory 文件不在 repo 内，已在 Step 10.1 直接写入，无需 git commit。

---

## Self-Review Checklist（起草后最终扫描）

- [x] **Spec coverage** — 对照 spec §1.1 的 6 项 + acceptance §6 的约 38 条 checkbox（M5 review 纠正；原写 34 低估了 call-site wiring + ws sandbox 等新增条目）：
  - Scope 1（sandbox 配置 / demo credentials / call-site wiring）→ Task 1 ✓
  - Scope 2（_parse_order 归一化 / Order.is_algo）→ Task 2 ✓
  - Scope 3（fetch_open_orders 三路 gather）→ Task 3 ✓
  - Scope 4（OCO 合并渲染）→ Task 4 ✓
  - Scope 5a/b/c（create_order algo / cancel/fetch/leverage / tools_execution 三处）→ Task 5/6/7 ✓
  - Scope 6（start() posMode/acctLv + ws sandbox + 诊断 log）→ Task 8 ✓
  - persona.py OCO 一行说明 → Task 9 ✓
  - Pre-work fixtures + scripts 入库 → 已在 Pre-work commit（`3f9ac83`）✓
  - § 5.4 USDT gate → Task 9 smoke 脚本覆盖 ✓
- [x] **Placeholder scan** — 全文 grep "TBD", "TODO", "Similar to Task": 无命中
- [x] **Type consistency**：
  - `Order.is_algo`（Task 2 加入）→ Task 4/5/6/7 都正确引用
  - `cancel_order(id, symbol, is_algo=bool)` signature（Task 6 加入）→ Task 7 正确 forward
  - `_parse_order -> list[Order]`（Task 2 加入）→ Task 3 flatten、Task 5/6 `parsed[0]` 一致引用
  - `self._sandbox`（Task 1 加入）→ Task 8 `start()` 正确使用
- [x] **文件路径**：所有 Modify/Create 路径都是绝对 or repo-relative，含行号或锚点
- [x] **代码块完整性**：每个"实现"步都含完整可粘代码，非 diff-only
- [x] **测试覆盖**：每个 scope item 都有先写失败测试 → 运行确认失败 → 实现 → 运行通过的完整循环
- [x] **测试数字核对**：Task 1 +10 / Task 2 +8 / Task 3 +2 passed + 1 skip / Task 4 +4 / Task 5 +4 / Task 6 +6 / Task 7 +6 / Task 8 +8 = 合计 +48 passed + 1 skip；730 baseline + 48 = 终值 778 passed + 1 skipped
- [x] **OCO 合并渲染 scope note**：agent 主路径（set_stop_loss + set_take_profit 两次调用）产生独立 conditional（不同 algoId），不触发合并分支；合并仅在外部 OKX web / attach-mode 下 OCO 时命中 —— 已在 Task 4 开头 flag，reviewer 不会误以为合并分支覆盖 agent 主路径
- [x] **stale id 偏离 spec §2.5.3**：Plan Task 7 Step 7.3 沿用现有代码早退（`target=None → return "Order not found"`），避免对 stale id 发无意义 API 调用；spec §2.5.3 相应段描述需后续修（不阻塞本 PR）
- [x] **P2-9 fetch_order OCO follow-up**：单条返 `parsed[0]` 丢 take_profit 腿的 edge case 已在 Task 10 Step 10.1 标注追加到 spec §7.1 观察期列

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-24-iter2b-okx-live-hardening.md`.

**两种执行方式：**

**1. Subagent-Driven（推荐）** — 每个 task 派一个 fresh subagent，task 间我做 code-review。适合 Iter 2b 这种 scope 大（10 tasks）、横跨多文件、有并行机会（Task 2 / Task 8 可并发）的场景。

**2. Inline Execution** — 本 session 里按 task 顺序执行，每 2-3 个 task 停下来 checkpoint。对话上下文占用更大，但失败时更快 debug。

**建议**：subagent-driven — 原因：
- Spec 里的"review-digest 拒绝列表"（`project_iter2b_review_digest.md`）已成熟，subagent 按既定设计走不太会反复
- 10 tasks 对单一 session 偏长，subagent 能保持上下文清爽
- Task 2 / Task 8 可并发跑，节省时间
- Pre-work fixtures 已入库，subagent 有确定性 ground truth 参考

**决定后告诉我，我启动对应 skill 开始执行。**
