# src/integrations/exchange/okx.py
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar

import ccxt.async_support as ccxt

from src.integrations.exchange.base import (
    Balance,
    BaseExchange,
    Candle,
    FillEvent,
    FundingRate,
    LongShortRatio,
    OpenInterest,
    Order,
    Position,
    Ticker,
)
from src.utils.cache import RateLimitHit

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

# order_type → trigger_reason 映射
_TRIGGER_REASON_MAP = {
    "stop": "stop",
    "stop_market": "stop",
    "take_profit": "take_profit",
    "take_profit_market": "take_profit",
    "market": "market",
}

# (side, order_type) → position_side 推断表
_POSITION_SIDE_INFER = {
    ("sell", "stop"): "long",
    ("buy", "stop"): "short",
    ("sell", "stop_market"): "long",
    ("buy", "stop_market"): "short",
    ("sell", "take_profit"): "long",
    ("buy", "take_profit"): "short",
    ("sell", "take_profit_market"): "long",
    ("buy", "take_profit_market"): "short",
}


def _retry(max_retries: int = 3, base_delay: float = 1.0):
    """Exponential backoff retry decorator for async exchange methods."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            last_error: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (
                    ccxt.NetworkError,
                    ccxt.ExchangeNotAvailable,
                    asyncio.TimeoutError,
                ) as e:
                    last_error = e
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"{func.__name__} attempt {attempt + 1}/{max_retries} "
                        f"failed: {e}, retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)
            raise last_error  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


class OKXExchange(BaseExchange):
    def __init__(self, api_key: str, secret: str, password: str, symbol: str):
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
        self._symbol = symbol
        self._fill_callback: Callable[[FillEvent], Awaitable[None]] | None = None
        self._alert_callback: Callable[[Any], Awaitable[None]] | None = None
        self._running = False
        self._ws_client: Any | None = None
        self._ws_connected = False
        self._pnl_fetch_timeout: float = 5.0
        self._seen_order_ids: dict[str, None] = {}
        self._seen_order_ids_max = 10000
        logger.info("OKX exchange initialized (real account)")

    # --- Fill / Alert callback registration ---

    def on_fill(self, callback: Callable[[FillEvent], Awaitable[None]]) -> None:
        self._fill_callback = callback

    def on_alert(self, callback: Callable[[Any], Awaitable[None]]) -> None:
        self._alert_callback = callback

    # --- WebSocket lifecycle ---

    async def start(self) -> None:
        """启动 WebSocket 监听循环。失败时降级为 REST-only 模式。"""
        try:
            import ccxt.pro as ccxtpro
            self._ws_client = ccxtpro.okx({
                "apiKey": self._client.apiKey,
                "secret": self._client.secret,
                "password": self._client.password,
                "options": {"defaultType": "swap"},
            })
            self._running = True
            self._ws_connected = True
            self._orders_task = asyncio.create_task(self._watch_orders_loop())
            self._ticker_task = asyncio.create_task(self._watch_ticker_loop())
            loops = "watch_orders + watch_ticker"
            logger.info("OKX WebSocket started (%s)", loops)
        except Exception:
            self._ws_connected = False
            logger.error("WebSocket connection failed, running in REST-only mode", exc_info=True)

    # --- watch_orders loop ---

    async def _watch_orders_loop(self) -> None:
        error_count = 0
        while self._running:
            try:
                orders = await self._ws_client.watch_orders(self._symbol)
                error_count = 0
                for order_data in orders:
                    status = order_data.get("status")
                    filled = order_data.get("filled", 0) or 0

                    if status == "closed":
                        order_id = order_data.get("id")
                        if order_id in self._seen_order_ids:
                            logger.debug("Skipping duplicate order %s", order_id)
                            continue
                        self._seen_order_ids[order_id] = None
                        if len(self._seen_order_ids) > self._seen_order_ids_max:
                            # FIFO 淘汰最旧的一半（dict 保持插入顺序）
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

    # --- watch_ticker loop ---

    async def _watch_ticker_loop(self) -> None:
        error_count = 0
        while self._running:
            try:
                raw = await self._ws_client.watch_ticker(self._symbol)
                error_count = 0
                if any(raw.get(k) is None for k in ("timestamp", "last", "bid", "ask", "high", "low", "baseVolume")):
                    continue
                try:
                    ticker = Ticker(
                        symbol=raw["symbol"],
                        last=float(raw["last"]),
                        bid=float(raw["bid"]),
                        ask=float(raw["ask"]),
                        high=float(raw["high"]),
                        low=float(raw["low"]),
                        base_volume=float(raw["baseVolume"]),
                        timestamp=raw["timestamp"],
                    )
                except (ValueError, TypeError):
                    logger.warning("Invalid ticker data, skipping: %s", raw.get("symbol"))
                    continue
                if self._alert_service:
                    alert = self._alert_service.check(ticker.last, ticker.timestamp)
                    if alert and self._alert_callback:
                        await self._alert_callback(alert)
                self._latest_price = ticker.last
                level_alerts = self._check_price_levels(ticker.last, ticker.timestamp)
                for la in level_alerts:
                    if self._alert_callback:
                        await self._alert_callback(la)
            except asyncio.CancelledError:
                break
            except Exception:
                error_count += 1
                delay = min(5 * (2 ** (error_count - 1)), 60)
                logger.warning("watch_ticker error (retry in %ds)", delay, exc_info=True)
                await asyncio.sleep(delay)

    # --- FillEvent 解析 ---

    async def _parse_fill_event(self, order_data: dict) -> FillEvent:
        order_id = order_data["id"]
        symbol = order_data["symbol"]
        side = order_data["side"]
        order_type = order_data.get("type", "")
        info = order_data.get("info", {})

        pos_side_raw = info.get("posSide")
        if pos_side_raw and pos_side_raw not in ("", "net"):
            position_side = pos_side_raw
        else:
            position_side = _POSITION_SIDE_INFER.get((side, order_type), side)

        trigger_reason = _TRIGGER_REASON_MAP.get(order_type, "unknown")

        fill_price = order_data.get("average") or order_data.get("price") or 0.0
        fill_price = float(fill_price)

        amount = float(order_data.get("filled", 0) or 0)

        fee_info = order_data.get("fee", {})
        fee = float(fee_info.get("cost", 0) or 0) if fee_info else 0.0

        pnl_raw = info.get("pnl")
        pnl: float | None = None
        if pnl_raw is not None and pnl_raw != "":
            try:
                pnl = float(pnl_raw)
            except (ValueError, TypeError):
                pnl = None
        if pnl is None:
            try:
                fetched = await asyncio.wait_for(
                    self._client.fetch_order(order_id, symbol),
                    timeout=self._pnl_fetch_timeout,
                )
                pnl_fetched = fetched.get("info", {}).get("pnl")
                if pnl_fetched is not None:
                    pnl = float(pnl_fetched)
            except Exception:
                logger.warning("pnl fetch failed for order %s, setting pnl=None", order_id)

        timestamp = order_data.get("timestamp", 0) or 0

        return FillEvent(
            order_id=order_id,
            symbol=symbol,
            side=side,
            position_side=position_side,
            trigger_reason=trigger_reason,
            fill_price=fill_price,
            amount=amount,
            fee=fee,
            pnl=pnl,
            timestamp=timestamp,
        )

    # --- REST interface ---

    @_retry()
    async def fetch_ticker(self, symbol: str) -> Ticker:  # type: ignore[override]
        data = await self._client.fetch_ticker(symbol)
        return Ticker(
            symbol=data["symbol"],  # type: ignore[arg-type]
            last=float(data["last"]),  # type: ignore[arg-type]
            bid=float(data["bid"]),  # type: ignore[arg-type]
            ask=float(data["ask"]),  # type: ignore[arg-type]
            high=float(data["high"]),  # type: ignore[arg-type]
            low=float(data["low"]),  # type: ignore[arg-type]
            base_volume=float(data["baseVolume"]),  # type: ignore[arg-type]
            timestamp=data["timestamp"],  # type: ignore[arg-type]
        )

    @_retry()
    async def fetch_ohlcv(  # type: ignore[override]
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> list[Candle]:
        data = await self._client.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [
            Candle(
                timestamp=int(r[0]),
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in data
        ]

    def _parse_fee(self, data: dict) -> float | None:
        fee_info = data.get("fee")
        if fee_info and fee_info.get("cost") is not None:
            return float(fee_info["cost"])
        return None

    def _parse_order(self, data: dict) -> Order:
        return Order(
            id=data["id"],  # type: ignore[arg-type]
            symbol=data["symbol"],  # type: ignore[arg-type]
            side=data["side"],  # type: ignore[arg-type]
            order_type=data["type"],  # type: ignore[arg-type]
            amount=float(data["amount"]),  # type: ignore[arg-type]
            price=float(data["price"]) if data.get("price") else None,  # type: ignore[arg-type]
            status=data["status"],  # type: ignore[arg-type]
            fee=self._parse_fee(data),
        )

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
        return self._parse_order(data)

    @_retry()
    async def fetch_order(  # type: ignore[override]
        self, order_id: str, symbol: str | None = None
    ) -> Order:
        data = await self._client.fetch_order(order_id, symbol)
        return self._parse_order(data)

    @_retry()
    async def fetch_open_orders(self, symbol: str) -> list[Order]:  # type: ignore[override]
        raw = await self._client.fetch_open_orders(symbol)
        return [self._parse_order(d) for d in raw]

    @_retry()
    async def fetch_closed_orders(  # type: ignore[override]
        self, symbol: str, limit: int = 20
    ) -> list[Order]:
        raw = await self._client.fetch_orders(
            symbol, limit=limit, params={"state": "filled"}
        )
        return [self._parse_order(d) for d in raw]

    @_retry()
    async def fetch_balance(self) -> Balance:  # type: ignore[override]
        data = await self._client.fetch_balance()
        return Balance(
            total_usdt=float(data["total"].get("USDT", 0)),
            free_usdt=float(data["free"].get("USDT", 0)),
            used_usdt=float(data["used"].get("USDT", 0)),
        )

    @_retry()
    async def fetch_positions(self, symbol: str) -> list[Position]:  # type: ignore[override]
        data = await self._client.fetch_positions([symbol])
        return [
            Position(
                symbol=p["symbol"],  # type: ignore[arg-type]
                side=p["side"],  # type: ignore[arg-type]
                contracts=float(p["contracts"]),  # type: ignore[arg-type]
                entry_price=float(p["entryPrice"]),  # type: ignore[arg-type]
                unrealized_pnl=float(p["unrealizedPnl"]),  # type: ignore[arg-type]
                leverage=int(p["leverage"]),  # type: ignore[arg-type]
                liquidation_price=(
                    float(p["liquidationPrice"])  # type: ignore[arg-type]
                    if p.get("liquidationPrice")
                    else None
                ),
            )
            for p in data
            if float(p["contracts"]) > 0  # type: ignore[arg-type]
        ]

    @_retry()
    async def set_leverage(self, symbol: str, leverage: int) -> None:  # type: ignore[override]
        await self._client.set_leverage(leverage, symbol)

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self._client.amount_to_precision(symbol, amount))  # type: ignore[arg-type]

    @_retry()
    async def cancel_order(self, order_id: str, symbol: str) -> None:  # type: ignore[override]
        await self._client.cancel_order(order_id, symbol)

    @_retry()
    async def fetch_funding_rate(self, symbol: str) -> FundingRate:
        # Must convert RateLimitExceeded → RateLimitHit INSIDE the function
        # body; see note above about the ccxt subclass hierarchy.
        try:
            data = await self._client.fetch_funding_rate(symbol)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"OKX funding rate: {e}") from e
        return FundingRate(
            symbol=data["symbol"],
            rate=float(data["fundingRate"]),
            next_funding_time=int(data.get("fundingTimestamp") or 0),
            timestamp=int(data.get("timestamp") or 0),
        )

    @_retry()
    async def fetch_open_interest(self, symbol: str) -> OpenInterest:
        try:
            data = await self._client.fetch_open_interest(symbol)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"OKX open interest: {e}") from e
        return OpenInterest(
            symbol=data["symbol"],
            open_interest=float(data.get("openInterestAmount") or 0),
            open_interest_value=float(data.get("openInterestValue") or 0),
            timestamp=int(data.get("timestamp") or 0),
        )

    @_retry()
    async def fetch_long_short_ratio(self, symbol: str) -> LongShortRatio:
        try:
            history = await self._client.fetch_long_short_ratio_history(symbol, "5m", limit=1)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"OKX long/short ratio: {e}") from e
        if not history:
            raise ValueError(f"No long/short ratio data for {symbol}")
        entry = history[0]
        ratio = float(entry["longShortRatio"])
        return LongShortRatio(
            symbol=symbol,
            long_short_ratio=ratio,
            long_ratio=ratio / (1 + ratio),
            short_ratio=1.0 / (1 + ratio),
            timestamp=int(entry.get("timestamp") or 0),
        )

    async def close(self) -> None:
        logger.info("OKX exchange closing")
        self._running = False
        for attr in ("_orders_task", "_ticker_task"):
            task = getattr(self, attr, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        try:
            await self._client.close()
        except Exception:
            logger.warning("REST client close failed", exc_info=True)
        finally:
            if self._ws_client:
                try:
                    await self._ws_client.close()
                except Exception:
                    logger.warning("WebSocket client close failed", exc_info=True)
