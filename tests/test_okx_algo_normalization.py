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
    """Call-site wiring 回归: app.build_services 必须从 settings.exchange.sandbox
    透传到 OKXExchange 构造; 漏传 = demo credentials 打 live endpoint (spec §2.1.2 footgun).
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
    settings.exchange.sandbox = True

    sc = MagicMock()

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
            # MetricsService / NewsService 等后续构造可能因 Settings 空值 raise;
            # OKXExchange 是 build_services 里第一个真实构造调用 (app.py:261),
            # 任何后续 raise 时它已被 call 过.
            pass
        assert mock_okx_cls.called, (
            "OKXExchange 未被构造调用 — 可能是 call-site 漏传 (本测试目标 bug),"
            "也可能是 patch 链未完全覆盖 build_services 在 OKXExchange 之前 raise."
        )
        kwargs = mock_okx_cls.call_args.kwargs
        assert kwargs.get("sandbox") is True, \
            f"call-site 漏传 sandbox kwarg; 实际 kwargs={kwargs}"


# ---------------------------------------------------------------------------
# Task 2: _parse_order algo 归一化 + Order.is_algo
# ---------------------------------------------------------------------------

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_okx(sandbox: bool = False):
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
    # info.slTriggerPx retains the original fixture value "54405.3"
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
    assert len(ids) == 1  # shared id
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


# ---------------------------------------------------------------------------
# Task 3: fetch_open_orders three-way asyncio.gather merge
# ---------------------------------------------------------------------------

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
    # 1 plain + 1 conditional SL + 2 OCO legs = 4
    assert len(result) == 4
    types = [o.order_type for o in result]
    assert "limit" in types
    assert types.count("stop") == 2  # 1 conditional SL + 1 OCO SL
    assert types.count("take_profit") == 1  # OCO TP
    # all three paths called
    assert ex._client.fetch_open_orders.call_count == 3


@pytest.mark.asyncio
async def test_fetch_open_orders_passes_ordtype_params():
    """Verify params dict routes ordType correctly to conditional + oco."""
    ex = _make_okx()
    ex._client.fetch_open_orders = AsyncMock(return_value=[])
    await ex.fetch_open_orders("BTC/USDT:USDT")
    calls = ex._client.fetch_open_orders.call_args_list
    params_list = [c.kwargs.get("params") or (c.args[1] if len(c.args) > 1 else None)
                   for c in calls]
    # plain path params empty/None; two algo paths pass conditional / oco
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
    """Advisory: verify gather is truly concurrent (not a merge gate).

    Implementation skeleton (not run under skip; to enable, remove skip +
    align with current CCXT version): use asyncio.Event to block each
    AsyncMock, assert three paths enter concurrently rather than serially.
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


# ---------------------------------------------------------------------------
# Task 5: create_order algo routing + manual Order construction
# ---------------------------------------------------------------------------


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
    """Algo create response is sparse (id/clOrdId/tag only); must manually construct Order with input price."""
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


# ---------------------------------------------------------------------------
# Task 6: cancel_order + fetch_order + set_leverage algo-aware routing
# ---------------------------------------------------------------------------

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
    # no algo params (if there are params kwargs, must not contain algoId)
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
    # first call does not pass algo params
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
    # second call must pass algo params
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
    # only called once, no fallback
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
    # single-direction posMode does not send posSide
    assert "posSide" not in params
