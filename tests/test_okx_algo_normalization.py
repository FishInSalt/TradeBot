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
