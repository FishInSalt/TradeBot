# src/integrations/exchange/okx.py
from __future__ import annotations

import asyncio
import json
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
    OrderBook,
    OrderBookLevel,
    Position,
    Ticker,
    Trade,
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

def _is_okx_error_code(err: Exception, code: str) -> bool:
    """Parse OKX sCode from ccxt.BadRequest message envelope.

    Pre-work observed envelope: 'okx {"code":"1","data":[{"sCode":"50002",...}],"msg":""}'
    Prefer JSON parse; fall back to structured sCode substring match (safer than raw digit match).
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
        # spec §2.1.4 Live endpoint 守卫 — 警示 log
        if not sandbox and api_key:
            logger.warning(
                "OKX live account initialized — ALL ORDERS WILL USE REAL FUNDS"
            )

    # --- Fill / Alert callback registration ---

    def on_alert(self, callback: Callable[[Any], Awaitable[None]]) -> None:
        self._alert_callback = callback

    # --- WebSocket lifecycle ---

    @_retry()
    async def _preload_markets(self) -> None:
        """load_markets wrapper — @_retry 保护启动时的 transient network glitch."""
        await self._client.load_markets()

    @_retry()
    async def _fetch_account_config(self) -> dict:
        """private_get_account_config wrapper — @_retry 保护启动时的 transient network glitch."""
        return await self._client.private_get_account_config()

    async def start(self) -> None:
        """预加载 markets + 校验账户模式（fail-fast）+ 启动 WebSocket（失败时降级 REST-only）。

        预加载 markets 用于 get_contract_size 的 contractSize 查询 —— 放在 WebSocket
        try 之外：markets 加载失败意味着所有依赖合约面值的工具都会坏掉，fail-fast
        比延迟到每次调用时静默 fallback 更好（spec §8.5）。

        账户模式校验（posMode + acctLv）放在 WebSocket try 之外：系统全栈假设单向仓位
        （net_mode）+ 单币种保证金（acctLv=2），错配会导致 fill-event 关联断裂或
        保证金语义不一致，fail-fast 避免上线后静默坏掉。
        """
        # Preload markets for get_contract_size — fail-fast outside WebSocket try
        await self._preload_markets()

        # Account config fail-fast — before WebSocket so failures don't waste connections
        config_resp = await self._fetch_account_config()
        data = config_resp.get("data") or []
        if not data:
            raise RuntimeError(
                "OKX account_config returned empty data — check API credentials/connectivity."
            )
        config = data[0]

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
            # CRITICAL: sync sandbox to WS client — missing this = REST→demo / WS→live
            # cross-account pollution (demo orders never emit fill events)
            if self._sandbox:
                self._ws_client.set_sandbox_mode(True)
            # ws_client is a separate ccxt.pro instance with its own markets cache;
            # watch_orders/watch_ticker raise "markets not loaded" without this.
            await self._ws_client.load_markets()
            self._running = True
            self._ws_connected = True
            self._orders_task = asyncio.create_task(self._watch_orders_loop())
            self._ticker_task = asyncio.create_task(self._watch_ticker_loop())
            loops = "watch_orders + watch_ticker"
            logger.info("OKX WebSocket started (%s, sandbox=%s)", loops, self._sandbox)
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
                    info = order_data.get("info") or {}
                    # Algo-lineage diagnostic log — dual-branch guard covers hypothesis A/B
                    # (A: info.ordType in {conditional, oco}; B: info.algoId non-empty)
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
        symbol = order_data["symbol"]
        side = order_data["side"]
        order_type = order_data.get("type", "")
        info = order_data.get("info", {})
        # algoId-aware order_id: agent stores algoId (= Order.id from create_order T5
        # manual construction) in decision_logs / TradeAction.order_id. OKX algo fill
        # events shape unverified pre-observation, two hypotheses (spec §3.1.3):
        #   A: info.ordType ∈ {conditional, oco}, order_data["id"] IS algoId
        #   B: info.ordType ∈ {market, limit}, order_data["id"] is underlying ordId,
        #      info.algoId is the algoId
        # Under A: info.algoId may be empty/None → fallback to order_data["id"] = algoId ✓
        # Under B: info.algoId non-empty → use algoId, matches decision_logs ✓
        # Plain (non-algo) fills: info.algoId absent → fallback to order_data["id"] ✓
        order_id = info.get("algoId") or order_data["id"]

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
                    self._fetch_order_with_algo_fallback(order_id, symbol),
                    timeout=self._pnl_fetch_timeout,
                )
                pnl_fetched = fetched.get("info", {}).get("pnl")
                if pnl_fetched is not None:
                    pnl = float(pnl_fetched)
            except Exception:
                logger.warning("pnl fetch failed for order %s, setting pnl=None", order_id)

        timestamp = order_data.get("timestamp", 0) or 0

        is_full_close = self._infer_is_full_close(info, side, trigger_reason)

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
            is_full_close=is_full_close,
        )

    def _infer_is_full_close(self, info: dict, side: str, trigger_reason: str) -> bool:
        """OKX 平仓判定：四源融合，任一命中即认 close.

        NOTE: 当前项目 convention 下 ALL CLOSE FILLS ARE FULL CLOSE
        (close_position / set_stop_loss / set_take_profit 都传 amount=pos.contracts)。
        所以本判定实质是 "is close direction", 等价于 is_full_close.

        若未来加 partial close 工具 (reduce_position(percent) 等), 此判定会
        static-false-positive partial close, 届时需改为基于 fetch_positions /
        in-memory position cache 的精确判定 (见 spec §6.3).
        """
        # 信号 1: reduceOnly 显式 (OKX 强信号).
        # Task 0 实测: market close 路径下, 仅当 caller 显式传 params={"reduceOnly": True}
        # 时 OKX 才回填 'true' (Task 5b 实施 Remediation A).
        if info.get("reduceOnly") in (True, "true"):
            return True
        # 信号 2: trigger_reason 派生 close 类型.
        # 注意 "liquidation" 当前不可达 — _TRIGGER_REASON_MAP (okx.py:36-42) 没有该 key.
        # Task 0 实测: algo (SL/TP) 触发后 OKX 推送 fill event 的 ordType="limit"
        # → trigger_reason="unknown" → 信号 2 漏. algo 路径靠新增的信号 4 (algoId) 兜底.
        if trigger_reason in ("stop", "take_profit", "liquidation"):
            return True
        # 信号 3: posSide + side 反向 (hedge mode 强信号).
        # 项目强制 net_mode (okx.py:183), posSide 永远是 "net", 此分支当前不命中.
        pos_side = info.get("posSide")
        if pos_side == "long" and side == "sell":
            return True
        if pos_side == "short" and side == "buy":
            return True
        # 信号 4 (Task 0 实测后新增): info.algoId 非空 → algo-triggered fill.
        # algo 单 (SL/TP/conditional/OCO) 本质都是 reduce-only 语义, 触发后的 fill event
        # 一定带 algoId. Task 0 1B/1C 实测确认: SL/TP 触发的 fill event 中 info.algoId
        # 均非空; 普通用户下单 (1A/1D) algoId 为空. OKX 显式标识, 比信号 1/2/3 都强.
        algo_id = info.get("algoId")
        if algo_id and algo_id != "":
            return True
        return False

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

    def _parse_order(self, data: dict) -> list[Order]:
        """CCXT-unified OKX order dict → one or more logical Orders.

        - ordType="oco" + both triggers present  → 2 Orders sharing id (stop + take_profit)
        - ordType="conditional" + only sl_px     → [Order(stop)]
        - ordType="conditional" + only tp_px     → [Order(take_profit)]
        - other (plain / malformed algo)         → [single Order] (is_algo=False)
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
        """Two-layer trigger price extraction: unified top-level primary + info fallback for CCXT version drift."""
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

    @_retry()
    async def create_order(  # type: ignore[override]
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> Order:
        merged_params: dict[str, Any] = {"tdMode": "isolated"}
        if params:
            merged_params.update(params)  # caller wins on conflict
        is_algo = order_type in ("stop", "take_profit")
        # Verified via scripts/iter2b_write_path_probe.py: Attempt B (stop)
        # + Attempt E (take_profit) both route to OKX algo endpoint with
        # info.algoId non-empty.
        if is_algo and price is not None:
            if order_type == "stop":
                merged_params["stopLossPrice"] = price
            else:  # take_profit
                merged_params["takeProfitPrice"] = price

        data = await self._client.create_order(
            symbol, order_type, side, amount, price, params=merged_params,  # type: ignore[arg-type]
        )

        if is_algo:
            # Algo create response contains only algoId + clOrdId + tag (verified
            # via write-path probe Attempt B dump); missing slTriggerPx / ordType /
            # stopLossPrice. Routing through _parse_order would hit the "both empty
            # → plain fallback" branch and return is_algo=False wrongly.
            # → Manually construct Order to bypass _parse_order.
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

    async def _fetch_order_with_algo_fallback(
        self, order_id: str, symbol: str | None,
    ) -> dict:
        """Raw CCXT fetch_order with plain-first + 50002 algo fallback.

        Shared by public `fetch_order` (wraps to Order via _parse_order) and
        `_parse_fill_event` pnl fallback (reads raw info.pnl). Same 50002 fallback
        semantics. No @_retry: caller decides retry vs timeout semantics.
        """
        try:
            return await self._client.fetch_order(order_id, symbol)
        except ccxt.BadRequest as e:
            # OKX 50002 appears when calling plain endpoint on an algo id — fall back to algo
            if _is_okx_error_code(e, "50002"):
                return await self._client.fetch_order(
                    order_id, symbol,
                    params={"stop": True, "trigger": True, "algoId": order_id},
                )
            raise

    @_retry()
    async def fetch_order(  # type: ignore[override]
        self, order_id: str, symbol: str | None = None
    ) -> Order:
        data = await self._fetch_order_with_algo_fallback(order_id, symbol)
        parsed = self._parse_order(data)
        return parsed[0]

    @_retry()
    async def fetch_open_orders(self, symbol: str) -> list[Order]:  # type: ignore[override]
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

    @_retry()
    async def fetch_closed_orders(  # type: ignore[override]
        self, symbol: str, limit: int = 20
    ) -> list[Order]:
        # Spec §7.2: 当前仅查 plain history endpoint, 不查 algo-history. 设计选择,
        # 非疏漏 — journal 走单条 fetch_order + 50002 fallback (§2.5.4) 已 cover
        # OCO/conditional 触发后的详情查询。若未来出现"列所有 closed orders"用例,
        # 扩为 plain-history + algo-history 两路 gather (类似 fetch_open_orders 三路)。
        raw = await self._client.fetch_orders(
            symbol, limit=limit, params={"state": "filled"}
        )
        return [o for d in raw for o in self._parse_order(d)]

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
        await self._client.set_leverage(
            leverage, symbol, params={"mgnMode": "isolated"},
        )

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self._client.amount_to_precision(symbol, amount))  # type: ignore[arg-type]

    @_retry()
    async def cancel_order(  # type: ignore[override]
        self, order_id: str, symbol: str, is_algo: bool = False,
    ) -> None:
        if is_algo:
            await self._client.cancel_order(
                order_id, symbol,
                params={"stop": True, "trigger": True, "algoId": order_id},
            )
        else:
            await self._client.cancel_order(order_id, symbol)

    # ── Derivatives market-structure fetches ──
    # ccxt.RateLimitExceeded is a subclass of ccxt.NetworkError, and @_retry()
    # catches NetworkError for up to 3 retries. If we converted 429 to
    # RateLimitHit outside the decorated body, the decorator would swallow
    # the 429 and retry silently — defeating TTLCache's stale-cache
    # fallback (spec §3.5). Keeping the try/except inside the function body
    # ensures RateLimitHit (not a ccxt type) escapes the decorator untouched
    # and propagates up to TTLCache.get_or_fetch.

    @_retry()
    async def fetch_funding_rate(self, symbol: str) -> FundingRate:
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
        except ccxt.NotSupported as e:
            # Pre-work P5 confirmed `has['fetchLongShortRatioHistory']=True`
            # at time of implementation, but a future ccxt upgrade could
            # withdraw capability. Surface a precise error so the tool-layer
            # "temporarily unavailable" message is not misread as a 429 / network
            # blip. Spec §9.5 calls for a REST /rubik/stat/... fallback if
            # this ever fires in production; tracked as follow-up.
            raise NotImplementedError(
                f"ccxt no longer exposes long/short ratio history for {symbol}: {e}"
            ) from e
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

    @_retry(max_retries=2, base_delay=0.5)
    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        import time
        data = await self._client.fetch_order_book(symbol, limit=depth)
        # CCXT parse_bid_ask appends `countOrId` (e.g. OKX numOrders) when the
        # raw exchange response carries it, so each entry can be `[price, amount]`
        # OR `[price, amount, num_orders, ...]`. `*_` swallows any trailing fields
        # so the unpack never raises ValueError on real OKX responses.
        bids = [OrderBookLevel(price=float(p), amount=float(a)) for p, a, *_ in data.get("bids", [])]
        asks = [OrderBookLevel(price=float(p), amount=float(a)) for p, a, *_ in data.get("asks", [])]
        ts = data.get("timestamp")
        if ts is None:
            ts = int(time.time() * 1000)
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ts)

    @_retry(max_retries=2, base_delay=0.5)
    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
        data = await self._client.fetch_trades(symbol, limit=limit)
        trades: list[Trade] = []
        for raw in data:
            raw_id = raw.get("id")
            trades.append(Trade(
                timestamp=int(raw["timestamp"]),
                side=str(raw["side"]),
                price=float(raw["price"]),
                amount=float(raw["amount"]),
                trade_id=str(raw_id) if raw_id is not None else None,
            ))
        # Explicit sort — don't rely on CCXT default (unified spec is ascending but not guaranteed)
        trades.sort(key=lambda t: t.timestamp)
        return trades

    async def get_contract_size(self, symbol: str) -> float:
        if not self._client.markets:
            await self._client.load_markets()
        market = self._client.markets.get(symbol)
        if market is None:
            logger.warning("Market %s not loaded, defaulting contract_size=1.0", symbol)
            return 1.0
        return float(market.get("contractSize", 1.0))

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
