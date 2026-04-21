# tests/test_okx_websocket.py

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


async def test_okx_start_fallback_to_rest_on_ws_failure():
    """WebSocket 启动失败时应降级为 REST-only 模式。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        # start() now preloads markets before the WebSocket try-block; make
        # load_markets awaitable so the preload doesn't hide the WS-failure path.
        exchange._client.load_markets = AsyncMock()
        with patch.dict("sys.modules", {"ccxt.pro": None}):
            await exchange.start()
        assert exchange._ws_connected is False
        assert exchange._running is False


def test_okx_constructor_accepts_symbol():
    """OKXExchange 构造函数应接受 symbol 参数。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        assert exchange._symbol == "BTC/USDT:USDT"


def test_okx_on_fill_registers_callback():
    """on_fill 应注册回调函数。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        callback = AsyncMock()
        exchange.on_fill(callback)
        assert exchange._fill_callback is callback


async def test_parse_fill_event_stop_loss():
    """_parse_fill_event 应正确解析止损成交数据。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        order_data = {
            "id": "order-123",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 59000.0,
            "price": 59000.0,
            "filled": 0.01,
            "fee": {"cost": 0.295, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {
                "posSide": "long",
                "pnl": "-12.50",
            },
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.order_id == "order-123"
        assert fill.symbol == "BTC/USDT:USDT"
        assert fill.side == "sell"
        assert fill.position_side == "long"
        assert fill.trigger_reason == "stop"
        assert fill.fill_price == 59000.0
        assert fill.amount == 0.01
        assert fill.fee == 0.295
        assert fill.pnl == -12.50
        assert fill.timestamp == 1712534400000


async def test_parse_fill_event_take_profit():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        order_data = {
            "id": "order-456",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "take_profit",
            "status": "closed",
            "average": 65000.0,
            "price": 65000.0,
            "filled": 0.01,
            "fee": {"cost": 0.325, "currency": "USDT"},
            "timestamp": 1712534500000,
            "info": {"posSide": "long", "pnl": "25.00"},
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.position_side == "long"
        assert fill.trigger_reason == "take_profit"
        assert fill.pnl == 25.00


async def test_parse_fill_event_infer_position_side():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        order_data = {
            "id": "order-789",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {},
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.position_side == "long"

        order_data["side"] = "buy"
        order_data["id"] = "order-790"
        fill2 = await exchange._parse_fill_event(order_data)
        assert fill2.position_side == "short"


async def test_parse_fill_event_pnl_missing():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        exchange._client.fetch_order = AsyncMock(return_value={"info": {}})
        order_data = {
            "id": "order-no-pnl",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {"posSide": "long"},
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.pnl is None
        exchange._client.fetch_order.assert_called_once()


async def test_parse_fill_event_pnl_rest_fallback():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        order_data = {
            "id": "order-rest-pnl",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {"posSide": "long"},
        }
        exchange._client.fetch_order = AsyncMock(return_value={
            "info": {"pnl": "-3.50"},
        })
        fill = await exchange._parse_fill_event(order_data)
        assert fill.pnl == -3.5
        exchange._client.fetch_order.assert_called_once_with("order-rest-pnl", "BTC/USDT:USDT")


async def test_parse_fill_event_pnl_rest_fallback_timeout():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        exchange._pnl_fetch_timeout = 0.1
        order_data = {
            "id": "order-timeout",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {"posSide": "long"},
        }
        async def slow_fetch(*args):
            await asyncio.sleep(10)
        exchange._client.fetch_order = slow_fetch
        fill = await exchange._parse_fill_event(order_data)
        assert fill.pnl is None


async def test_parse_fill_event_unknown_order_type():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        order_data = {
            "id": "order-unknown",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "trailingStop",
            "status": "closed",
            "average": 58000.0,
            "price": 58000.0,
            "filled": 0.01,
            "fee": {"cost": 0.29, "currency": "USDT"},
            "timestamp": 1712534600000,
            "info": {"posSide": "long"},
        }
        fill = await exchange._parse_fill_event(order_data)
        assert fill.trigger_reason == "unknown"


async def test_watch_orders_loop_calls_callback():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        callback = AsyncMock()
        exchange.on_fill(callback)
        exchange._running = True
        order_data = {
            "id": "order-ws",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 59000.0,
            "price": 59000.0,
            "filled": 0.01,
            "fee": {"cost": 0.295, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {"posSide": "long", "pnl": "-5.00"},
        }
        mock_ws = AsyncMock()
        call_count = 0
        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [order_data]
            exchange._running = False
            return []
        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws
        await exchange._watch_orders_loop()
        callback.assert_called_once()
        fill_event = callback.call_args[0][0]
        assert fill_event.order_id == "order-ws"
        assert fill_event.pnl == -5.00


async def test_watch_orders_loop_skips_open_orders():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        callback = AsyncMock()
        exchange.on_fill(callback)
        exchange._running = True
        open_order = {
            "id": "order-open",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "open",
            "average": None,
            "price": 59000.0,
            "filled": 0,
            "fee": {"cost": 0, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {"posSide": "long"},
        }
        mock_ws = AsyncMock()
        call_count = 0
        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [open_order]
            exchange._running = False
            return []
        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws
        await exchange._watch_orders_loop()
        callback.assert_not_called()


async def test_watch_orders_loop_logs_partial_fill():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        import logging
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        callback = AsyncMock()
        exchange.on_fill(callback)
        exchange._running = True
        partial_order = {
            "id": "order-partial",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "open",
            "average": 59000.0,
            "price": 59000.0,
            "filled": 0.005,
            "fee": {"cost": 0.15, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {"posSide": "long"},
        }
        mock_ws = AsyncMock()
        call_count = 0
        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [partial_order]
            exchange._running = False
            return []
        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws
        with patch("src.integrations.exchange.okx.logger") as mock_logger:
            await exchange._watch_orders_loop()
            mock_logger.warning.assert_called()


async def test_watch_orders_loop_error_recovery():
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        exchange._running = True
        mock_ws = AsyncMock()
        call_count = 0
        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("network error")
            exchange._running = False
            return []
        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await exchange._watch_orders_loop()
        assert call_count == 2


async def test_watch_orders_loop_callback_error_no_backoff():
    """fill callback 异常不应触发 WebSocket 指数退避。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        callback = AsyncMock(side_effect=Exception("DB write failed"))
        exchange.on_fill(callback)
        exchange._running = True

        order_data = {
            "id": "order-cb-err",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 59000.0,
            "price": 59000.0,
            "filled": 0.01,
            "fee": {"cost": 0.295, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {"posSide": "long", "pnl": "-5.00"},
        }

        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [order_data]
            exchange._running = False
            return []

        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws

        # 不应触发 asyncio.sleep（退避）
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await exchange._watch_orders_loop()
            mock_sleep.assert_not_called()

        # callback 被调用了（即使失败）
        callback.assert_called_once()


async def test_watch_orders_loop_dedup_orders():
    """重连后重复的 closed order 不应重复触发 fill callback。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        callback = AsyncMock()
        exchange.on_fill(callback)
        exchange._running = True

        order_data = {
            "id": "order-dup",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "type": "stop",
            "status": "closed",
            "average": 59000.0,
            "price": 59000.0,
            "filled": 0.01,
            "fee": {"cost": 0.295, "currency": "USDT"},
            "timestamp": 1712534400000,
            "info": {"posSide": "long", "pnl": "-5.00"},
        }

        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_orders(symbol):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return [order_data]  # 同一 order 返回两次
            exchange._running = False
            return []

        mock_ws.watch_orders = mock_watch_orders
        exchange._ws_client = mock_ws

        await exchange._watch_orders_loop()

        # 只应触发一次
        callback.assert_called_once()


async def test_watch_ticker_loop_skips_none_timestamp():
    """timestamp 为 None 的 ticker 应被跳过，不触发退避。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        mock_service = MagicMock()
        mock_service.check.return_value = None
        exchange.set_alert_service(mock_service)

        exchange._running = True
        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_ticker(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "symbol": "BTC/USDT:USDT",
                    "last": "60000",
                    "bid": "59999",
                    "ask": "60001",
                    "high": "60500",
                    "low": "59500",
                    "baseVolume": "12345",
                    "timestamp": None,  # None timestamp
                }
            exchange._running = False
            return {
                "symbol": "BTC/USDT:USDT",
                "last": "60000",
                "bid": "59999",
                "ask": "60001",
                "high": "60500",
                "low": "59500",
                "baseVolume": "12345",
                "timestamp": 1712534401000,
            }

        mock_ws.watch_ticker = mock_watch_ticker
        exchange._ws_client = mock_ws

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await exchange._watch_ticker_loop()
            mock_sleep.assert_not_called()

        # 只有第二个 tick（有 timestamp）才喂入 service
        mock_service.check.assert_called_once()


async def test_watch_ticker_loop_skips_none_bid():
    """bid 为 None 的 ticker 应被跳过。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        mock_service = MagicMock()
        mock_service.check.return_value = None
        exchange.set_alert_service(mock_service)

        exchange._running = True
        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_ticker(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "symbol": "BTC/USDT:USDT",
                    "last": "60000",
                    "bid": None,
                    "ask": "60001",
                    "high": "60500",
                    "low": "59500",
                    "baseVolume": "12345",
                    "timestamp": 1712534400000,
                }
            exchange._running = False
            return {
                "symbol": "BTC/USDT:USDT",
                "last": "60000",
                "bid": "59999",
                "ask": "60001",
                "high": "60500",
                "low": "59500",
                "baseVolume": "12345",
                "timestamp": 1712534401000,
            }

        mock_ws.watch_ticker = mock_watch_ticker
        exchange._ws_client = mock_ws

        await exchange._watch_ticker_loop()
        mock_service.check.assert_called_once()


async def test_close_closes_both_clients():
    """close() 应关闭 REST 和 WS 客户端，即使 REST 关闭失败。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        mock_rest = AsyncMock()
        mock_rest.close = AsyncMock(side_effect=Exception("REST close failed"))
        exchange._client = mock_rest

        mock_ws = AsyncMock()
        mock_ws.close = AsyncMock()
        exchange._ws_client = mock_ws

        await exchange.close()

        mock_rest.close.assert_called_once()
        mock_ws.close.assert_called_once()


async def test_close_without_ws_client():
    """没有 WS 客户端时 close() 不应崩溃。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        mock_rest = AsyncMock()
        exchange._client = mock_rest
        exchange._ws_client = None

        await exchange.close()
        mock_rest.close.assert_called_once()


async def test_okx_set_alert_service():
    """set_alert_service 应注入 PriceAlertService。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        mock_service = MagicMock()
        exchange.set_alert_service(mock_service)
        assert exchange._alert_service is mock_service


async def test_okx_update_alert_params_delegates():
    """update_alert_params 应委托给 PriceAlertService.update_params。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )
        mock_service = MagicMock()
        exchange.set_alert_service(mock_service)
        exchange.update_alert_params(2.0, 10)
        mock_service.update_params.assert_called_once_with(2.0, 10)


async def test_watch_ticker_loop_triggers_alert():
    """_watch_ticker_loop 应在 PriceAlertService 返回 AlertInfo 时调用 alert callback。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        from src.services.price_alert import AlertInfo
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        alert_callback = AsyncMock()
        exchange.on_alert(alert_callback)

        mock_alert = AlertInfo(
            symbol="BTC/USDT:USDT",
            current_price=57900.0,
            reference_price=60000.0,
            change_pct=-3.5,
            window_minutes=5,
            timestamp=1712534400000,
        )
        mock_service = MagicMock()
        mock_service.check.side_effect = [mock_alert, None]
        exchange.set_alert_service(mock_service)

        exchange._running = True
        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_ticker(symbol):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "symbol": "BTC/USDT:USDT",
                    "last": "57900",
                    "bid": "57899",
                    "ask": "57901",
                    "high": "60000",
                    "low": "57800",
                    "baseVolume": "12345",
                    "timestamp": 1712534400000,
                }
            exchange._running = False
            return {
                "symbol": "BTC/USDT:USDT",
                "last": "57900",
                "bid": "57899",
                "ask": "57901",
                "high": "60000",
                "low": "57800",
                "baseVolume": "12345",
                "timestamp": 1712534401000,
            }

        mock_ws.watch_ticker = mock_watch_ticker
        exchange._ws_client = mock_ws

        await exchange._watch_ticker_loop()

        alert_callback.assert_called_once()
        alert_info = alert_callback.call_args[0][0]
        assert alert_info.change_pct == -3.5


async def test_watch_ticker_loop_no_alert_when_service_returns_none():
    """当 PriceAlertService.check 返回 None 时不应调用 alert callback。"""
    with patch("ccxt.async_support.okx") as mock_okx:
        mock_okx.return_value = MagicMock()
        from src.integrations.exchange.okx import OKXExchange
        exchange = OKXExchange(
            api_key="test", secret="test", password="test",
            symbol="BTC/USDT:USDT",
        )

        alert_callback = AsyncMock()
        exchange.on_alert(alert_callback)

        mock_service = MagicMock()
        mock_service.check.return_value = None
        exchange.set_alert_service(mock_service)

        exchange._running = True
        mock_ws = AsyncMock()
        call_count = 0

        async def mock_watch_ticker(symbol):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                exchange._running = False
            return {
                "symbol": "BTC/USDT:USDT",
                "last": "60000",
                "bid": "59999",
                "ask": "60001",
                "high": "60500",
                "low": "59500",
                "baseVolume": "12345",
                "timestamp": 1712534400000 + call_count * 1000,
            }

        mock_ws.watch_ticker = mock_watch_ticker
        exchange._ws_client = mock_ws

        await exchange._watch_ticker_loop()
        alert_callback.assert_not_called()
