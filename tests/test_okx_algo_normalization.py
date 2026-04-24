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
