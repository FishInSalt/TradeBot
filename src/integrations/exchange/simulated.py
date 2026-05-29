from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable, Literal

import ccxt.async_support as ccxt  # new top-level import (for RateLimitExceeded)

from src.integrations.exchange.base import (
    Balance,
    BaseExchange,
    Candle,
    FillEvent,
    FundingRate,
    LongShortRatio,
    OpenInterestHistoryPoint,
    Order,
    OrderBook,
    OrderBookLevel,
    Position,
    Ticker,
    Trade,
    _OKX_OI_PERIOD,
)
from src.utils.cache import RateLimitHit

logger = logging.getLogger(__name__)


@dataclass
class _Position:
    """Internal position representation."""
    side: str
    contracts: float
    entry_price: float
    leverage: int
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _PendingOrder:
    """Internal pending order representation."""
    id: str
    symbol: str
    side: str
    position_side: str
    order_type: str          # "market" | "limit" | "stop" | "take_profit"
    amount: float
    trigger_price: float | None   # stop/TP: trigger price; limit: fill price; market: None
    frozen_margin: float = 0.0    # market/limit: frozen margin+fee
    leverage: int = 1             # leverage at order time (needed at fill time)


class SimulatedExchange(BaseExchange):
    def __init__(self, config: Any, db_engine: Any, session_id: str, symbol: str):
        super().__init__()
        self._config = config
        self._db_engine = db_engine
        self._session_id = session_id
        self._symbol = symbol
        if config.fee_rate is None:
            raise ValueError(
                "SimulatedExchange requires fee_rate in config "
                "(wizard-enforced; legacy NULL session detected)"
            )
        self._fee_rate: float = config.fee_rate
        self._precision: dict[str, int] = config.precision if config.precision is not None else {}

        # Internal state (initialized in start() or directly for tests)
        self._free_usdt: float = 0.0
        self._used_usdt: float = 0.0
        self._frozen_usdt: float = 0.0
        self._positions: dict[str, _Position] = {}
        self._pending_orders: list[_PendingOrder] = []
        self._leverage: dict[str, int] = {}
        self._latest_ticker: Ticker | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._error_count = 0
        self._alert_callback: Callable[[Any], Awaitable[None]] | None = None

    def _validate_symbol(self, symbol: str) -> None:
        if symbol != self._symbol:
            raise ValueError(f"Symbol mismatch: expected {self._symbol}, got {symbol}")

    def _is_close_order(self, symbol: str, side: str) -> bool:
        """Is this a close order? Uses current position state (call from create_order)."""
        pos = self._positions.get(symbol)
        return (
            (pos is not None and pos.side == "long" and side == "sell") or
            (pos is not None and pos.side == "short" and side == "buy")
        )

    @staticmethod
    def _is_close_order_static(o: _PendingOrder) -> bool:
        """Is this a close-direction order? Uses order fields only (no position state)."""
        return (
            (o.position_side == "long" and o.side == "sell") or
            (o.position_side == "short" and o.side == "buy")
        )

    def _calc_unrealized_pnl(self, pos: _Position) -> float:
        if self._latest_ticker is None:
            return 0.0
        if pos.side == "long":
            return (self._latest_ticker.bid - pos.entry_price) * pos.contracts
        else:
            return (pos.entry_price - self._latest_ticker.ask) * pos.contracts

    def _calc_liquidation_price(self, pos: _Position) -> float:
        if pos.side == "long":
            return pos.entry_price * (1 - 1 / pos.leverage) / (1 - self._fee_rate)
        else:
            return pos.entry_price * (1 + 1 / pos.leverage) / (1 + self._fee_rate)

    # --- BaseExchange interface ---

    async def fetch_ticker(self, symbol: str) -> Ticker:
        self._validate_symbol(symbol)
        if self._latest_ticker is None:
            raise RuntimeError("No ticker data available yet")
        return self._latest_ticker

    async def get_mark_price(self, symbol: str) -> float:
        """Sim has a single price source — mark = last. Note: under the new
        get_position flow this is called inside a 6-tuple gather that already
        fetches ticker; fetch_ticker is observation-only (reads cached
        _latest_ticker) so back-to-back invocation is safe. If a future
        SimulatedExchange mutates state in fetch_ticker (e.g., synthetic tick
        advancement for replay scenarios), revisit and read self._latest_ticker
        directly.
        """
        self._validate_symbol(symbol)
        if self._latest_ticker is None:
            raise RuntimeError("No ticker data available yet")
        return self._latest_ticker.last

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list[Candle]:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        data = await self._ccxt.fetch_ohlcv(symbol, timeframe, limit=limit)
        return [
            Candle(timestamp=int(r[0]), open=float(r[1]), high=float(r[2]),
                   low=float(r[3]), close=float(r[4]), volume=float(r[5]))
            for r in data
        ]

    async def fetch_balance(self) -> Balance:
        unrealized = sum(
            self._calc_unrealized_pnl(pos)
            for pos in self._positions.values()
        )
        return Balance(
            total_usdt=self._free_usdt + self._used_usdt + self._frozen_usdt + unrealized,
            free_usdt=max(0.0, self._free_usdt + unrealized),
            used_usdt=self._used_usdt,
        )

    async def fetch_positions(self, symbol: str) -> list[Position]:
        self._validate_symbol(symbol)
        pos = self._positions.get(symbol)
        if pos is None:
            return []
        return [Position(
            symbol=symbol,
            side=pos.side,
            contracts=pos.contracts,
            entry_price=pos.entry_price,
            unrealized_pnl=self._calc_unrealized_pnl(pos),
            leverage=pos.leverage,
            liquidation_price=self._calc_liquidation_price(pos),
            created_at=pos.created_at,
        )]

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        self._validate_symbol(symbol)
        if not isinstance(leverage, int):
            raise TypeError(f"leverage must be int, got {type(leverage).__name__}")
        if not 1 <= leverage <= 125:
            raise ValueError(f"leverage must be 1-125, got {leverage}")
        pos = self._positions.get(symbol)
        if pos is not None and pos.leverage != leverage:
            raise ValueError(
                f"Cannot change leverage from {pos.leverage} to {leverage} while holding position. "
                f"Close position first."
            )
        self._leverage[symbol] = leverage

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        decimals = self._precision[symbol]  # KeyError if missing = fail fast
        factor = 10 ** decimals
        return math.floor(amount * factor) / factor

    async def create_order(  # noqa: ARG002
        self,
        symbol: str,
        side: str,
        order_type: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> Order:
        # Sim doesn't need reduceOnly: its _is_close_order_static + position
        # state logic handles full-close inference natively. Accept params for
        # API parity with OKX (Remediation A); ignored at the sim layer.
        self._validate_symbol(symbol)
        if amount <= 0:
            raise ValueError(f"amount must be > 0, got {amount}")
        if order_type not in ("market", "limit", "stop", "take_profit"):
            raise ValueError(f"Unknown order_type: {order_type}")
        if order_type in ("stop", "take_profit", "limit") and price is None:
            raise ValueError(f"price is required for {order_type} orders")

        async with self._lock:
            if order_type == "market":
                if self._latest_ticker is None:
                    raise RuntimeError("No ticker data available")
                ticker = self._latest_ticker
                is_close = self._is_close_order(symbol, side)

                if is_close:
                    pos = self._positions[symbol]
                    position_side = pos.side
                    estimated_price = ticker.bid if pos.side == "long" else ticker.ask
                    estimated_fee = estimated_price * amount * self._fee_rate
                    frozen = min(estimated_fee, self._free_usdt)
                else:
                    position_side = "long" if side == "buy" else "short"
                    estimated_price = ticker.ask if side == "buy" else ticker.bid
                    leverage = self._leverage.get(symbol, 1)
                    estimated_margin = (estimated_price * amount) / leverage
                    estimated_fee = estimated_price * amount * self._fee_rate
                    frozen = (estimated_margin + estimated_fee) * 1.002
                    if self._free_usdt < frozen:
                        raise ValueError(
                            f"Insufficient balance: need {frozen:.2f}, have {self._free_usdt:.2f}"
                        )

                self._free_usdt -= frozen
                self._frozen_usdt += frozen

                order_id = str(uuid.uuid4())
                leverage_val = self._leverage.get(symbol, 1)
                self._pending_orders.append(_PendingOrder(
                    id=order_id, symbol=symbol, side=side,
                    position_side=position_side, order_type="market",
                    amount=amount, trigger_price=None,
                    frozen_margin=frozen, leverage=leverage_val,
                ))
                if self._db_engine:
                    await self._persist_state()
                return Order(
                    id=order_id, symbol=symbol, side=side, order_type="market",
                    amount=amount, price=None, status="open",
                )
            elif order_type == "limit":
                # Limit orders are open-only (first version — D4)
                pos = self._positions.get(symbol)
                position_side = "long" if side == "buy" else "short"
                if pos is not None and pos.side != position_side:
                    raise ValueError(
                        f"Cannot open {position_side} limit order: "
                        f"existing {pos.side} position. Close position first."
                    )
                # Use position leverage if position exists, else current setting
                if pos is not None:
                    leverage = pos.leverage
                else:
                    leverage = self._leverage.get(symbol, 1)
                margin = (price * amount) / leverage
                fee = price * amount * self._fee_rate
                frozen = margin + fee
                if self._free_usdt < frozen:
                    raise ValueError(
                        f"Insufficient balance: need {frozen:.2f}, have {self._free_usdt:.2f}"
                    )
                self._free_usdt -= frozen
                self._frozen_usdt += frozen
                order_id = str(uuid.uuid4())
                self._pending_orders.append(_PendingOrder(
                    id=order_id, symbol=symbol, side=side,
                    position_side=position_side, order_type="limit",
                    amount=amount, trigger_price=price,
                    frozen_margin=frozen, leverage=leverage,
                ))
                if self._db_engine:
                    await self._persist_state()
                return Order(
                    id=order_id, symbol=symbol, side=side,
                    order_type="limit", amount=amount, price=price, status="open",
                )
            else:
                order = self._create_conditional_order(symbol, side, order_type, amount, price)  # type: ignore[arg-type]
                if self._db_engine:
                    await self._persist_state()
                return order

    def _fill_market_open(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
        """Fill a pending market open order. Returns None if cancelled due to conflict."""
        pos = self._positions.get(order.symbol)
        position_side = "long" if order.side == "buy" else "short"

        # Defensive: reverse position conflict
        if pos is not None and pos.side != position_side:
            logger.warning(f"Market open {order.id} cancelled: conflicts with {pos.side} position")
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        # Defensive: leverage mismatch
        if pos is not None and pos.leverage != order.leverage:
            logger.warning(
                f"Market open {order.id} cancelled: leverage mismatch "
                f"(order={order.leverage}x, position={pos.leverage}x)"
            )
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        fill_price = ticker.ask if order.side == "buy" else ticker.bid
        leverage = order.leverage
        actual_margin = (fill_price * order.amount) / leverage
        actual_fee = fill_price * order.amount * self._fee_rate
        actual_cost = actual_margin + actual_fee

        # Unfreeze → occupy
        diff = order.frozen_margin - actual_cost
        self._frozen_usdt -= order.frozen_margin
        self._used_usdt += actual_margin
        self._free_usdt += diff
        if self._free_usdt < 0:
            logger.warning(
                f"Market open {order.id}: free_usdt shortfall {-self._free_usdt:.4f} clamped to 0"
            )
            self._free_usdt = 0.0

        self._free_usdt = round(self._free_usdt, 8)
        self._used_usdt = round(self._used_usdt, 8)

        # Create or merge position
        if pos is not None and pos.side == position_side:
            new_contracts = pos.contracts + order.amount
            new_entry = (pos.entry_price * pos.contracts + fill_price * order.amount) / new_contracts
            pos.contracts = new_contracts
            pos.entry_price = new_entry
            pos.updated_at = datetime.now(timezone.utc)
        else:
            self._positions[order.symbol] = _Position(
                side=position_side, contracts=order.amount,
                entry_price=fill_price, leverage=leverage,
            )
        self._leverage[order.symbol] = leverage

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.info(f"Market open filled: {order.side} {order.amount} {order.symbol} @ {fill_price:.2f}")
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            position_side=position_side, trigger_reason="market",
            fill_price=fill_price, amount=order.amount, fee=actual_fee,
            pnl=None, timestamp=now_ms,
            is_full_close=False,  # market open
        )

    def _fill_market_close(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
        """Fill a pending market close order. Returns None if position already gone."""
        pos = self._positions.get(order.symbol)
        if pos is None:
            logger.warning(f"Market close {order.id} cancelled: position already closed")
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        actual_amount = min(order.amount, pos.contracts)
        fill_price = ticker.bid if pos.side == "long" else ticker.ask
        position_side = pos.side
        captured_entry = pos.entry_price  # capture BEFORE close (pos may be popped from self._positions in _close_position_core)
        pnl, fee, _ = self._close_position_core(
            order.symbol, pos.side, actual_amount, fill_price, pnl_cap=True,
        )

        # Unfreeze (close doesn't occupy new margin — it's released by _close_position_core)
        self._frozen_usdt -= order.frozen_margin
        self._free_usdt += order.frozen_margin

        is_full_close = order.symbol not in self._positions  # rely on _close_position_core having popped fully-closed symbols

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.info(
            f"Market close filled: {order.side} {actual_amount} {order.symbol} @ {fill_price:.2f}, "
            f"pnl={pnl:.4f}, fee={fee:.4f}"
        )
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            position_side=position_side, trigger_reason="market",
            fill_price=fill_price, amount=actual_amount, fee=fee,
            pnl=pnl, timestamp=now_ms,
            is_full_close=is_full_close,
            entry_price=captured_entry,
        )

    def _execute_market_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
        """Route pending market order to open or close fill. Uses static direction check."""
        if self._is_close_order_static(order):
            return self._fill_market_close(order, ticker)
        else:
            return self._fill_market_open(order, ticker)

    def _close_position_core(
        self, symbol: str, position_side: str, amount: float, fill_price: float,
        *, pnl_cap: bool = False,
    ) -> tuple[float, float, float]:
        """Core close logic shared by market close, conditional fill, and liquidation.
        Returns (pnl, fee, released_margin). Does NOT cancel orders."""
        pos = self._positions[symbol]
        released_margin = (pos.entry_price * amount) / pos.leverage
        fee = fill_price * amount * self._fee_rate

        if position_side == "long":
            pnl = (fill_price - pos.entry_price) * amount
        else:
            pnl = (pos.entry_price - fill_price) * amount

        if pnl_cap:
            pnl = max(pnl, -(released_margin - fee))

        self._used_usdt -= released_margin
        self._free_usdt += released_margin + pnl - fee
        self._free_usdt = round(self._free_usdt, 8)
        self._used_usdt = round(self._used_usdt, 8)

        if self._free_usdt < -0.01:
            raise RuntimeError(
                f"CRITICAL: free_usdt went negative ({self._free_usdt:.4f}). "
                f"This should never happen."
            )

        if amount >= pos.contracts:
            del self._positions[symbol]
        else:
            pos.contracts -= amount
            pos.contracts = round(pos.contracts, 8)
            pos.updated_at = datetime.now(timezone.utc)

        return pnl, fee, released_margin

    def _create_conditional_order(
        self, symbol: str, side: str, order_type: str, amount: float, price: float,
    ) -> Order:
        pos = self._positions.get(symbol)
        if pos is None:
            raise ValueError("Cannot create conditional order without a position")

        # Force amount = position contracts
        actual_amount = pos.contracts
        position_side = pos.side
        order_id = str(uuid.uuid4())

        self._pending_orders.append(_PendingOrder(
            id=order_id, symbol=symbol, side=side,
            position_side=position_side, order_type=order_type,
            amount=actual_amount, trigger_price=price,
        ))

        logger.info(f"Conditional order created: {order_type} {side} {actual_amount} {symbol} @ {price:.2f}")
        return Order(
            id=order_id, symbol=symbol, side=side, order_type=order_type,
            amount=actual_amount, price=price, status="open",
            trigger_price=price if order_type in ("stop", "take_profit") else None,
        )

    def _cancel_orphaned_orders(self) -> None:
        """Remove pending orders that lost their target.
        - stop/take_profit: remove if no position (close orders need a target)
        - market close direction: remove if no position + unfreeze margin
        - market open / limit: always keep (they create positions)
        """
        remaining = []
        for o in self._pending_orders:
            if o.order_type in ("stop", "take_profit"):
                if o.symbol in self._positions:
                    remaining.append(o)
                # else: conditional orders have no frozen margin, just drop
            elif o.order_type == "market" and self._is_close_order_static(o):
                if o.symbol in self._positions:
                    remaining.append(o)
                else:
                    # Close target gone (liquidated), unfreeze
                    if o.frozen_margin > 0:
                        self._frozen_usdt -= o.frozen_margin
                        self._free_usdt += o.frozen_margin
            else:
                # market open / limit: keep
                remaining.append(o)
        self._pending_orders = remaining

    def _remove_order_by_id(self, order_id: str) -> None:
        self._pending_orders = [o for o in self._pending_orders if o.id != order_id]

    def _has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    # --- Matching engine ---

    def _should_trigger(self, order: _PendingOrder, ticker: Ticker) -> bool:
        if order.order_type == "limit":
            if order.side == "buy":
                return ticker.ask <= order.trigger_price
            else:
                return ticker.bid >= order.trigger_price
        elif order.order_type == "stop":
            if order.position_side == "long":
                return ticker.bid <= order.trigger_price
            else:
                return ticker.ask >= order.trigger_price
        elif order.order_type == "take_profit":
            if order.position_side == "long":
                return ticker.bid >= order.trigger_price
            else:
                return ticker.ask <= order.trigger_price
        return False

    def _execute_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent:
        pos = self._positions[order.symbol]
        actual_amount = min(order.amount, pos.contracts)
        fill_price = ticker.bid if pos.side == "long" else ticker.ask
        captured_entry = pos.entry_price  # capture BEFORE close (pos may be popped from self._positions in _close_position_core)
        pnl, fee, _ = self._close_position_core(
            order.symbol, pos.side, actual_amount, fill_price,
        )
        is_full_close = order.symbol not in self._positions
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            position_side=order.position_side, trigger_reason=order.order_type,
            fill_price=fill_price, amount=actual_amount, fee=fee,
            pnl=pnl,
            timestamp=now_ms,
            is_full_close=is_full_close,
            entry_price=captured_entry,
        )

    def _execute_limit_fill(self, order: _PendingOrder, ticker: Ticker) -> FillEvent | None:
        """Fill a limit order. Returns None if cancelled due to conflict (unfreezes margin)."""
        pos = self._positions.get(order.symbol)
        position_side = "long" if order.side == "buy" else "short"

        # Check 1: reverse position conflict
        if pos is not None and pos.side != position_side:
            logger.warning(f"Limit order {order.id} cancelled: conflicts with {pos.side} position")
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        # Check 2: leverage mismatch
        if pos is not None and pos.leverage != order.leverage:
            logger.warning(
                f"Limit order {order.id} cancelled: leverage mismatch "
                f"(order={order.leverage}x, position={pos.leverage}x)"
            )
            self._frozen_usdt -= order.frozen_margin
            self._free_usdt += order.frozen_margin
            return None

        fill_price = order.trigger_price  # limit fills at specified price
        leverage = order.leverage
        actual_margin = (fill_price * order.amount) / leverage
        actual_fee = fill_price * order.amount * self._fee_rate
        actual_cost = actual_margin + actual_fee

        # Unfreeze → occupy
        self._frozen_usdt -= order.frozen_margin
        self._used_usdt += actual_margin
        self._free_usdt += (order.frozen_margin - actual_cost)
        self._free_usdt = round(self._free_usdt, 8)
        self._used_usdt = round(self._used_usdt, 8)

        # Create or merge position
        if pos is not None and pos.side == position_side:
            new_contracts = pos.contracts + order.amount
            new_entry = (pos.entry_price * pos.contracts + fill_price * order.amount) / new_contracts
            pos.contracts = new_contracts
            pos.entry_price = new_entry
            pos.updated_at = datetime.now(timezone.utc)
        else:
            self._positions[order.symbol] = _Position(
                side=position_side, contracts=order.amount,
                entry_price=fill_price, leverage=leverage,
            )
        self._leverage[order.symbol] = leverage

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.info(f"Limit order filled: {order.side} {order.amount} {order.symbol} @ {fill_price:.2f}")
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            position_side=position_side, trigger_reason="limit",
            fill_price=fill_price, amount=order.amount, fee=actual_fee,
            pnl=None, timestamp=now_ms,
            is_full_close=False,  # limit open (no limit-close tool exists)
        )

    def _force_liquidate(self, pos: _Position, symbol: str, price: float) -> FillEvent:
        contracts = pos.contracts  # capture before close deletes pos
        captured_entry = pos.entry_price  # capture BEFORE close (pos may be popped from self._positions in _close_position_core)
        pnl, fee, _ = self._close_position_core(
            symbol, pos.side, contracts, price, pnl_cap=True,
        )
        is_full_close = symbol not in self._positions  # always True for liquidation
        order_id = str(uuid.uuid4())
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        logger.warning(f"LIQUIDATION: {pos.side} {contracts} {symbol} @ {price:.2f}")
        return FillEvent(
            order_id=order_id, symbol=symbol,
            side="sell" if pos.side == "long" else "buy",
            position_side=pos.side, trigger_reason="liquidation",
            fill_price=price, amount=contracts, fee=fee,
            pnl=pnl,
            timestamp=now_ms,
            is_full_close=is_full_close,
            entry_price=captured_entry,
        )

    async def _process_tick(self, ticker: Ticker) -> None:
        """Process a single tick -- match market orders, check liquidations, conditional orders, alerts."""
        self._latest_ticker = ticker
        self._latest_price = ticker.last

        triggered: list[FillEvent] = []
        filled_order_ids: list[str] = []
        cancelled_order_ids: list[str] = []
        new_orders: list[tuple[Order, str]] = []
        alert_info = None
        level_alerts = []

        async with self._lock:
            # 0. Match pending market orders (new — before liquidation)
            market_orders = [o for o in self._pending_orders if o.order_type == "market"]
            for order in market_orders:
                fill = self._execute_market_fill(order, ticker)
                if fill is None:
                    cancelled_order_ids.append(order.id)
                    continue
                filled_order_ids.append(order.id)
                triggered.append(fill)

            # 1. Liquidation check (must be before conditional orders)
            for symbol, pos in list(self._positions.items()):
                liq = self._calc_liquidation_price(pos)
                if pos.side == "long" and ticker.bid <= liq:
                    fill = self._force_liquidate(pos, symbol, ticker.bid)
                    triggered.append(fill)
                    new_orders.append((Order(
                        id=fill.order_id, symbol=symbol,
                        side="sell", order_type="liquidation",
                        amount=fill.amount, price=fill.fill_price,
                        status="closed", fee=fill.fee,
                    ), fill.position_side))
                elif pos.side == "short" and ticker.ask >= liq:
                    fill = self._force_liquidate(pos, symbol, ticker.ask)
                    triggered.append(fill)
                    new_orders.append((Order(
                        id=fill.order_id, symbol=symbol,
                        side="buy", order_type="liquidation",
                        amount=fill.amount, price=fill.fill_price,
                        status="closed", fee=fill.fee,
                    ), fill.position_side))

            # 2. Conditional + limit order check
            processed = set(filled_order_ids + cancelled_order_ids)
            for order in list(self._pending_orders):
                if order.id in processed:
                    continue
                if self._should_trigger(order, ticker):
                    if order.order_type in ("stop", "take_profit"):
                        if not self._has_position(order.symbol):
                            continue
                        fill = self._execute_fill(order, ticker)
                    elif order.order_type == "limit":
                        fill = self._execute_limit_fill(order, ticker)
                        if fill is None:
                            cancelled_order_ids.append(order.id)
                            continue
                    else:
                        continue
                    triggered.append(fill)
                    filled_order_ids.append(order.id)

            # 3. Unified cleanup
            all_resolved = filled_order_ids + cancelled_order_ids
            if triggered or all_resolved:
                for oid in all_resolved:
                    self._remove_order_by_id(oid)
                self._cancel_orphaned_orders()
                if self._db_engine:
                    await self._persist_state(
                        new_orders=new_orders,
                        filled_order_ids=filled_order_ids,
                        cancelled_order_ids=cancelled_order_ids,
                        fill_events=triggered,
                    )

            # 4. Price alert check
            if self._alert_service:
                alert_info = self._alert_service.check(ticker.last, ticker.timestamp)

            # 5. Price level alert check (R7)
            level_alerts = self._check_price_levels(ticker.last, ticker.timestamp)

        # Notify outside lock
        for fill in triggered:
            await self._dispatch_fill_event(fill)

        if alert_info and self._alert_callback:
            await self._alert_callback(alert_info)

        for la in level_alerts:
            if self._alert_callback:
                await self._alert_callback(la)

    # --- Order query methods ---

    async def fetch_order(self, order_id: str, symbol: str | None = None) -> Order:
        # Check in-memory pending orders first
        for o in self._pending_orders:
            if o.id == order_id:
                return Order(
                    id=o.id, symbol=o.symbol, side=o.side,
                    order_type=o.order_type, amount=o.amount,
                    price=o.trigger_price, status="open",
                    trigger_price=o.trigger_price if o.order_type in ("stop", "take_profit") else None,
                )
        # Query DB for filled/cancelled orders
        if self._db_engine:
            from sqlalchemy import select
            from src.storage.database import get_session
            from src.storage.models import SimOrder
            async with get_session(self._db_engine) as session:
                result = await session.execute(
                    select(SimOrder).where(SimOrder.order_id == order_id)
                )
                row = result.scalar_one_or_none()
                if row:
                    price = row.filled_price if row.status == "closed" else row.trigger_price
                    return Order(
                        id=row.order_id, symbol=row.symbol, side=row.side,
                        order_type=row.order_type, amount=row.amount,
                        price=price, status=row.status, fee=row.fee,
                        trigger_price=row.trigger_price if row.order_type in ("stop", "take_profit") else None,
                    )
        raise ValueError(f"Order not found: {order_id}")

    async def fetch_open_orders(self, symbol: str) -> list[Order]:
        self._validate_symbol(symbol)
        return [
            Order(
                id=o.id, symbol=o.symbol, side=o.side, order_type=o.order_type,
                amount=o.amount, price=o.trigger_price, status="open",
                trigger_price=o.trigger_price if o.order_type in ("stop", "take_profit") else None,
            )
            for o in self._pending_orders
            if o.symbol == symbol
        ]

    async def fetch_closed_orders(self, symbol: str, limit: int = 20) -> list[Order]:
        self._validate_symbol(symbol)
        if not self._db_engine:
            return []
        from sqlalchemy import select, func
        from src.storage.database import get_session
        from src.storage.models import SimOrder
        async with get_session(self._db_engine) as session:
            result = await session.execute(
                select(SimOrder)
                .where(SimOrder.session_id == self._session_id)
                .where(SimOrder.symbol == symbol)
                .where(SimOrder.status.in_(["closed", "cancelled"]))
                .order_by(func.coalesce(SimOrder.filled_at, SimOrder.created_at).desc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                Order(
                    id=row.order_id, symbol=row.symbol, side=row.side,
                    order_type=row.order_type, amount=row.amount,
                    price=row.filled_price if row.status == "closed" else row.trigger_price,
                    status=row.status, fee=row.fee,
                    trigger_price=row.trigger_price if row.order_type in ("stop", "take_profit") else None,
                )
                for row in rows
            ]

    async def cancel_order(  # noqa: ARG002
        self, order_id: str, symbol: str, is_algo: bool = False,
    ) -> None:
        self._validate_symbol(symbol)
        async with self._lock:
            order = None
            for o in self._pending_orders:
                if o.id == order_id:
                    order = o
                    break
            if order is None:
                raise ValueError(f"Order not found: {order_id}")

            if order.order_type == "market":
                raise ValueError("Cannot cancel market orders")

            # Unfreeze margin (limit orders have frozen margin)
            if order.frozen_margin > 0:
                self._frozen_usdt -= order.frozen_margin
                self._free_usdt += order.frozen_margin

            self._remove_order_by_id(order_id)
            if self._db_engine:
                await self._persist_state()
        logger.info(f"Order cancelled: {order_id}")

    # --- Alert callback ---

    def on_alert(self, callback: Callable[[Any], Awaitable[None]]) -> None:
        self._alert_callback = callback

    def has_pending_market_order(self, symbol: str, side: str | None = None) -> bool:
        """Check for pending market orders matching symbol and optional side."""
        for o in self._pending_orders:
            if o.order_type == "market" and o.symbol == symbol:
                if side is None or o.side == side:
                    return True
        return False

    # --- Persistence ---

    async def _init_state(self, initial_balance: float) -> None:
        self._free_usdt = initial_balance
        self._used_usdt = 0.0
        self._frozen_usdt = 0.0
        self._positions = {}
        self._pending_orders = []
        self._leverage = {}

    async def _restore_state(self) -> None:
        from sqlalchemy import select
        from src.storage.database import get_session
        from src.storage.models import SimBalance, SimPosition, SimOrder

        async with get_session(self._db_engine) as session:
            result = await session.execute(
                select(SimBalance).where(SimBalance.session_id == self._session_id)
            )
            bal = result.scalar_one_or_none()
            if bal:
                self._free_usdt = bal.free_usdt
                self._used_usdt = bal.used_usdt
                self._frozen_usdt = bal.frozen_usdt
            else:
                return

            result = await session.execute(
                select(SimPosition).where(SimPosition.session_id == self._session_id)
            )
            for pos in result.scalars().all():
                self._positions[pos.symbol] = _Position(
                    side=pos.side, contracts=pos.contracts,
                    entry_price=pos.entry_price, leverage=pos.leverage,
                    created_at=pos.created_at, updated_at=pos.updated_at,
                )
                self._leverage[pos.symbol] = pos.leverage

            result = await session.execute(
                select(SimOrder)
                .where(SimOrder.session_id == self._session_id)
                .where(SimOrder.status == "open")
            )
            for o in result.scalars().all():
                self._pending_orders.append(_PendingOrder(
                    id=o.order_id, symbol=o.symbol, side=o.side,
                    position_side=o.position_side, order_type=o.order_type,
                    amount=o.amount, trigger_price=o.trigger_price,
                    frozen_margin=o.frozen_margin,
                    leverage=o.leverage,
                ))

        logger.info(
            f"Restored state: balance={self._free_usdt:.2f}/{self._used_usdt:.2f}/{self._frozen_usdt:.2f}, "
            f"positions={len(self._positions)}, pending_orders={len(self._pending_orders)}"
        )

    async def _persist_state(
        self,
        new_orders: list[tuple[Order, str]] | None = None,
        filled_order_ids: list[str] | None = None,
        cancelled_order_ids: list[str] | None = None,  # NEW
        fill_events: list[FillEvent] | None = None,
    ) -> None:
        from sqlalchemy import delete, update
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from src.storage.database import get_session
        from src.storage.models import SimBalance, SimPosition, SimOrder

        now = datetime.now(timezone.utc)

        async with get_session(self._db_engine) as session:
            # 1. Upsert balance (includes frozen_usdt)
            stmt = sqlite_insert(SimBalance).values(
                session_id=self._session_id,
                free_usdt=self._free_usdt,
                used_usdt=self._used_usdt,
                frozen_usdt=self._frozen_usdt,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["session_id"],
                set_={
                    "free_usdt": stmt.excluded.free_usdt,
                    "used_usdt": stmt.excluded.used_usdt,
                    "frozen_usdt": stmt.excluded.frozen_usdt,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)

            # 2. Positions: delete + insert (preserve created_at)
            await session.execute(
                delete(SimPosition).where(SimPosition.session_id == self._session_id)
            )
            for symbol, pos in self._positions.items():
                session.add(SimPosition(
                    session_id=self._session_id, symbol=symbol,
                    side=pos.side, contracts=pos.contracts,
                    entry_price=pos.entry_price, leverage=pos.leverage,
                    created_at=pos.created_at, updated_at=pos.updated_at,
                ))

            # 3a. Update filled conditional orders
            if filled_order_ids and fill_events:
                fill_map = {f.order_id: f for f in fill_events}
                for oid in filled_order_ids:
                    fill = fill_map.get(oid)
                    if fill:
                        await session.execute(
                            update(SimOrder)
                            .where(SimOrder.order_id == oid)
                            .values(
                                status="closed",
                                filled_price=fill.fill_price,
                                fee=fill.fee,
                                filled_at=now,
                            )
                        )

            # 3a-bis. Update cancelled orders → "cancelled"
            if cancelled_order_ids:
                for oid in cancelled_order_ids:
                    await session.execute(
                        update(SimOrder)
                        .where(SimOrder.order_id == oid)
                        .values(status="cancelled")
                    )

            # 3b. Cancel orphaned pending orders in DB
            pending_ids = [o.id for o in self._pending_orders]
            filled_ids = filled_order_ids or []
            cancelled_ids = cancelled_order_ids or []
            exclude_ids = pending_ids + filled_ids + cancelled_ids
            if exclude_ids:
                await session.execute(
                    update(SimOrder)
                    .where(SimOrder.session_id == self._session_id)
                    .where(SimOrder.status == "open")
                    .where(SimOrder.order_id.notin_(exclude_ids))
                    .values(status="cancelled")
                )
            else:
                await session.execute(
                    update(SimOrder)
                    .where(SimOrder.session_id == self._session_id)
                    .where(SimOrder.status == "open")
                    .values(status="cancelled")
                )

            # 3c. INSERT new orders (market, liquidation)
            if new_orders:
                for order, position_side in new_orders:
                    session.add(SimOrder(
                        session_id=self._session_id, order_id=order.id,
                        symbol=order.symbol, side=order.side,
                        position_side=position_side,
                        order_type=order.order_type, amount=order.amount,
                        trigger_price=order.price if order.status == "open" else None,
                        status=order.status,
                        filled_price=order.price if order.status == "closed" else None,
                        fee=order.fee,
                        filled_at=now if order.status == "closed" else None,
                    ))

            # 3d. Upsert pending orders (includes frozen_margin/leverage)
            for pending in self._pending_orders:
                stmt = sqlite_insert(SimOrder).values(
                    session_id=self._session_id, order_id=pending.id,
                    symbol=pending.symbol, side=pending.side,
                    position_side=pending.position_side,
                    order_type=pending.order_type, amount=pending.amount,
                    trigger_price=pending.trigger_price,
                    frozen_margin=pending.frozen_margin,
                    leverage=pending.leverage,
                    status="open", created_at=now,
                )
                stmt = stmt.on_conflict_do_nothing(index_elements=["order_id"])
                await session.execute(stmt)

            await session.commit()

    # --- Derivatives ---

    async def fetch_funding_rate(self, symbol: str) -> FundingRate:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            data = await self._ccxt.fetch_funding_rate(symbol)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim funding rate: {e}") from e
        return FundingRate(
            symbol=data["symbol"],
            rate=float(data["fundingRate"]),
            next_funding_time=int(data.get("fundingTimestamp") or 0),
            timestamp=int(data.get("timestamp") or 0),
        )

    async def fetch_open_interest_history(
        self,
        symbol: str,
        period: Literal["5m", "1h", "1d"] = "1h",
        limit: int = 26,
    ) -> list[OpenInterestHistoryPoint]:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            raw = await self._ccxt.public_get_rubik_stat_contracts_open_interest_history({
                "instId": self._ccxt.market(symbol)["id"],
                "period": _OKX_OI_PERIOD[period],
                "limit": str(limit),
            })
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim open interest history: {e}") from e
        rows = raw.get("data") or []
        points = [
            OpenInterestHistoryPoint(
                timestamp=int(r[0]),
                open_interest=float(r[2]),
                open_interest_value=float(r[3]),
            )
            for r in rows
        ]
        points.reverse()
        return points

    async def fetch_long_short_ratio(self, symbol: str) -> LongShortRatio:
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            history = await self._ccxt.fetch_long_short_ratio_history(symbol, "5m", limit=1)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim long/short ratio: {e}") from e
        except ccxt.NotSupported as e:
            # Mirrors okx.py: surface a precise error if a future ccxt
            # upgrade withdraws the fetch_long_short_ratio_history
            # capability, rather than leaking ccxt.NotSupported to the
            # tool layer where it would flatten into "temporarily
            # unavailable".
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

    # --- Lifecycle ---

    async def start(self) -> None:
        import ccxt.pro as ccxtpro
        from sqlalchemy import select
        from src.storage.database import get_session
        from src.storage.models import SimBalance, Session as SessionModel

        async with get_session(self._db_engine) as session:
            result = await session.execute(
                select(SimBalance).where(SimBalance.session_id == self._session_id)
            )
            has_state = result.scalar_one_or_none() is not None

        if has_state:
            await self._restore_state()
        else:
            async with get_session(self._db_engine) as session:
                result = await session.execute(
                    select(SessionModel).where(SessionModel.id == self._session_id)
                )
                trading_session = result.scalar_one()
            await self._init_state(trading_session.initial_balance)
            await self._persist_state()

        self._ccxt = ccxtpro.okx()
        seed_ticker = None
        for attempt in range(3):
            try:
                seed_ticker = await self._ccxt.fetch_ticker(self._symbol)
                break
            except Exception as e:
                if attempt < 2:
                    delay = 2 ** attempt
                    logger.warning(f"fetch_ticker attempt {attempt + 1}/3 failed: {e}, retrying in {delay}s")
                    await asyncio.sleep(delay)
                else:
                    raise RuntimeError(f"Failed to fetch initial ticker after 3 attempts: {e}") from e
        self._latest_ticker = Ticker(
            symbol=seed_ticker["symbol"],
            last=float(seed_ticker["last"]),
            bid=float(seed_ticker["bid"]),
            ask=float(seed_ticker["ask"]),
            high=float(seed_ticker["high"]),
            low=float(seed_ticker["low"]),
            base_volume=float(seed_ticker["baseVolume"]),
            timestamp=seed_ticker["timestamp"],
        )
        self._latest_price = self._latest_ticker.last

        self._running = True
        self._matching_task = asyncio.create_task(self._matching_loop())
        logger.info(f"SimulatedExchange started: {self._symbol}, seed ticker @ {self._latest_ticker.last}")

    async def _matching_loop(self) -> None:
        while self._running:
            try:
                raw = await self._ccxt.watch_ticker(self._symbol)
                ticker = Ticker(
                    symbol=raw["symbol"], last=float(raw["last"]),
                    bid=float(raw["bid"]), ask=float(raw["ask"]),
                    high=float(raw["high"]), low=float(raw["low"]),
                    base_volume=float(raw["baseVolume"]), timestamp=raw["timestamp"],
                )
                await self._process_tick(ticker)
            except asyncio.CancelledError:
                break
            except Exception:
                self._error_count += 1
                logger.error("Matching loop error (count=%d)", self._error_count, exc_info=True)
                if self._error_count >= 3:
                    await asyncio.sleep(min(5 * self._error_count, 60))
            else:
                self._error_count = 0

    async def close(self) -> None:
        self._running = False
        if hasattr(self, "_matching_task"):
            self._matching_task.cancel()
            try:
                await self._matching_task
            except asyncio.CancelledError:
                pass
        if hasattr(self, "_ccxt"):
            await self._ccxt.close()
        logger.info("SimulatedExchange closed")

    async def fetch_order_book(self, symbol: str, depth: int = 20) -> OrderBook:
        """Fetch real order book via _ccxt (ccxtpro.okx public /market/books)."""
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            data = await self._ccxt.fetch_order_book(symbol, limit=depth)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim order book: {e}") from e
        # CCXT-parsed entries are [price, amount, count?]; *_ swallows count.
        # None-safe: skip malformed levels rather than crash on float(None).
        bids = [OrderBookLevel(price=float(p), amount=float(a))
                for p, a, *_ in data.get("bids", []) if p is not None and a is not None]
        asks = [OrderBookLevel(price=float(p), amount=float(a))
                for p, a, *_ in data.get("asks", []) if p is not None and a is not None]
        # Explicit sort — self-enforce best-first instead of depending on CCXT's
        # internal parse_order_book sort_by (untested-in-prod assumption otherwise).
        bids.sort(key=lambda l: l.price, reverse=True)
        asks.sort(key=lambda l: l.price)
        # is None (not falsy) — a legitimate timestamp of 0 must not fall to wall-clock,
        # mirroring fetch_trades' None-guard for cross-method consistency.
        raw_ts = data.get("timestamp")
        ts = raw_ts if raw_ts is not None else int(time.time() * 1000)
        return OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ts)

    async def fetch_trades(self, symbol: str, limit: int = 500) -> list[Trade]:
        """Fetch real recent trades via _ccxt (ccxtpro.okx public /market/trades)."""
        self._validate_symbol(symbol)
        if not hasattr(self, "_ccxt"):
            raise RuntimeError("Exchange not started — call start() first")
        try:
            data = await self._ccxt.fetch_trades(symbol, limit=limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitHit(f"Sim recent trades: {e}") from e
        trades: list[Trade] = []
        for r in data:
            ts, side, px, amt = r.get("timestamp"), r.get("side"), r.get("price"), r.get("amount")
            if ts is None or side is None or px is None or amt is None:
                continue  # None-safe: CCXT safe_* may return None on malformed rows
            tid = r.get("id")
            trades.append(Trade(timestamp=int(ts), side=str(side), price=float(px),
                                amount=float(amt), trade_id=str(tid) if tid is not None else None))
        trades.sort(key=lambda t: t.timestamp)
        return trades

    async def get_contract_size(self, symbol: str) -> float:
        return 1.0
