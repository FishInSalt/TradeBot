from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from src.integrations.exchange.base import (
    Balance,
    BaseExchange,
    Candle,
    FillEvent,
    Order,
    Position,
    Ticker,
)

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
    order_type: str
    amount: float
    trigger_price: float


class SimulatedExchange(BaseExchange):
    def __init__(self, config: Any, db_engine: Any, session_id: str, symbol: str):
        self._config = config
        self._db_engine = db_engine
        self._session_id = session_id
        self._symbol = symbol
        self._fee_rate: float = config.fee_rate if config.fee_rate is not None else 0.0005
        self._precision: dict[str, int] = config.precision if config.precision is not None else {}

        # Internal state (initialized in start() or directly for tests)
        self._free_usdt: float = 0.0
        self._used_usdt: float = 0.0
        self._positions: dict[str, _Position] = {}
        self._pending_orders: list[_PendingOrder] = []
        self._leverage: dict[str, int] = {}
        self._latest_ticker: Ticker | None = None
        self._running = False
        self._lock = asyncio.Lock()
        self._fill_callback: Callable[[FillEvent], Awaitable[None]] | None = None
        self._pending_fills: list[FillEvent] = []
        self._error_count = 0

    def _validate_symbol(self, symbol: str) -> None:
        if symbol != self._symbol:
            raise ValueError(f"Symbol mismatch: expected {self._symbol}, got {symbol}")

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
            total_usdt=self._free_usdt + self._used_usdt + unrealized,
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

    async def create_order(
        self, symbol: str, side: str, order_type: str, amount: float, price: float | None = None,
    ) -> Order:
        self._validate_symbol(symbol)
        if amount <= 0:
            raise ValueError(f"amount must be > 0, got {amount}")
        if order_type not in ("market", "stop", "take_profit"):
            raise ValueError(f"Unknown order_type: {order_type}")
        if order_type in ("stop", "take_profit") and price is None:
            raise ValueError(f"price is required for {order_type} orders")

        async with self._lock:
            if order_type == "market":
                order, position_side, pnl = self._execute_market_order(symbol, side, amount)
                if self._db_engine:
                    await self._persist_state(new_orders=[(order, position_side)])
                self._pending_fills.append(FillEvent(
                    order_id=order.id, symbol=symbol, side=order.side,
                    position_side=position_side,
                    trigger_reason="market",
                    fill_price=order.price, amount=order.amount,
                    fee=order.fee, pnl=pnl,
                    timestamp=int(datetime.now(timezone.utc).timestamp() * 1000),
                ))
                return order
            else:
                order = self._create_conditional_order(symbol, side, order_type, amount, price)  # type: ignore[arg-type]
                if self._db_engine:
                    await self._persist_state()  # persist new pending order
                return order

    def _execute_market_order(self, symbol: str, side: str, amount: float) -> tuple[Order, str, float | None]:
        """Returns (Order, position_side, pnl) tuple."""
        if self._latest_ticker is None:
            raise RuntimeError("No ticker data available")

        pos = self._positions.get(symbol)
        is_close = (
            (pos is not None and pos.side == "long" and side == "sell") or
            (pos is not None and pos.side == "short" and side == "buy")
        )

        if is_close:
            return self._close_market_order(symbol, side, amount, pos)  # type: ignore[arg-type]
        else:
            return self._open_market_order(symbol, side, amount)

    def _open_market_order(self, symbol: str, side: str, amount: float) -> tuple[Order, str, None]:
        ticker = self._latest_ticker
        if ticker is None:
            raise RuntimeError("No ticker data available")
        fill_price = ticker.ask if side == "buy" else ticker.bid
        leverage = self._leverage.get(symbol, 1)
        margin = (fill_price * amount) / leverage
        fee = fill_price * amount * self._fee_rate
        required = margin + fee

        if self._free_usdt < required:
            raise ValueError(
                f"Insufficient balance: need {required:.2f}, have {self._free_usdt:.2f}"
            )

        # Check leverage consistency for add-to-position
        pos = self._positions.get(symbol)
        position_side = "long" if side == "buy" else "short"
        if pos is not None:
            if pos.leverage != leverage:
                raise ValueError(
                    f"Leverage mismatch: position has {pos.leverage}x, "
                    f"current is {leverage}x. Close position first."
                )
            position_side = pos.side  # add-to-position: same direction
            # Merge position
            new_contracts = pos.contracts + amount
            new_entry = (pos.entry_price * pos.contracts + fill_price * amount) / new_contracts
            pos.contracts = new_contracts
            pos.entry_price = new_entry
            pos.updated_at = datetime.now(timezone.utc)
        else:
            self._positions[symbol] = _Position(
                side=position_side,
                contracts=amount,
                entry_price=fill_price,
                leverage=leverage,
            )

        self._free_usdt -= required
        self._used_usdt += margin
        self._free_usdt = round(self._free_usdt, 8)
        self._used_usdt = round(self._used_usdt, 8)

        order_id = str(uuid.uuid4())
        order = Order(
            id=order_id, symbol=symbol, side=side, order_type="market",
            amount=amount, price=fill_price, status="closed", fee=fee,
        )
        logger.info(f"Market order filled: {side} {amount} {symbol} @ {fill_price:.2f}, fee={fee:.4f}")
        return order, position_side, None

    def _close_market_order(self, symbol: str, side: str, amount: float, pos: _Position) -> tuple[Order, str, float]:
        ticker = self._latest_ticker
        if ticker is None:
            raise RuntimeError("No ticker data available")
        # Clamp amount
        actual_amount = min(amount, pos.contracts)
        position_side = pos.side  # record BEFORE close (pos may be deleted)
        fill_price = ticker.bid if pos.side == "long" else ticker.ask
        pnl, fee, released_margin = self._close_position_core(
            symbol, pos.side, actual_amount, fill_price,
        )

        # Cancel orphaned orders if position fully closed
        if symbol not in self._positions:
            self._cancel_orphaned_orders()

        order_id = str(uuid.uuid4())
        order = Order(
            id=order_id, symbol=symbol, side=side, order_type="market",
            amount=actual_amount, price=fill_price, status="closed", fee=fee,
        )
        logger.info(
            f"Market close filled: {side} {actual_amount} {symbol} @ {fill_price:.2f}, "
            f"pnl={pnl:.4f}, fee={fee:.4f}"
        )
        return order, position_side, pnl

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
        )

    def _cancel_orphaned_orders(self) -> None:
        """Remove pending orders for symbols that no longer have positions."""
        self._pending_orders = [
            o for o in self._pending_orders
            if o.symbol in self._positions
        ]

    def _remove_order_by_id(self, order_id: str) -> None:
        self._pending_orders = [o for o in self._pending_orders if o.id != order_id]

    def _has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    # --- Matching engine ---

    def _should_trigger(self, order: _PendingOrder, ticker: Ticker) -> bool:
        if order.order_type == "stop":
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
        pnl, fee, _ = self._close_position_core(
            order.symbol, pos.side, actual_amount, fill_price,
        )
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            position_side=order.position_side, trigger_reason=order.order_type,
            fill_price=fill_price, amount=actual_amount, fee=fee,
            pnl=pnl,
            timestamp=now_ms,
        )

    def _force_liquidate(self, pos: _Position, symbol: str, price: float) -> FillEvent:
        contracts = pos.contracts  # capture before close deletes pos
        pnl, fee, _ = self._close_position_core(
            symbol, pos.side, contracts, price, pnl_cap=True,
        )
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
        )

    async def _process_tick(self, ticker: Ticker) -> None:
        """Process a single tick -- check liquidations and conditional orders."""
        self._latest_ticker = ticker

        triggered: list[FillEvent] = []
        filled_order_ids: list[str] = []
        new_orders: list[tuple[Order, str]] = []

        async with self._lock:
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

            # 2. Conditional order check
            for order in list(self._pending_orders):
                if self._should_trigger(order, ticker):
                    if not self._has_position(order.symbol):
                        continue
                    fill = self._execute_fill(order, ticker)
                    triggered.append(fill)
                    filled_order_ids.append(order.id)

            if triggered:
                for fill in triggered:
                    self._remove_order_by_id(fill.order_id)
                self._cancel_orphaned_orders()
                if self._db_engine:
                    await self._persist_state(
                        new_orders=new_orders,
                        filled_order_ids=filled_order_ids,
                        fill_events=triggered,
                    )

        # Notify outside lock
        for fill in triggered:
            if self._fill_callback:
                await self._fill_callback(fill)

    # --- Order query methods ---

    async def fetch_order(self, order_id: str, symbol: str | None = None) -> Order:
        # Check in-memory pending orders first
        for o in self._pending_orders:
            if o.id == order_id:
                return Order(id=o.id, symbol=o.symbol, side=o.side,
                             order_type=o.order_type, amount=o.amount,
                             price=o.trigger_price, status="open")
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
                    return Order(id=row.order_id, symbol=row.symbol, side=row.side,
                                 order_type=row.order_type, amount=row.amount,
                                 price=price, status=row.status, fee=row.fee)
        raise ValueError(f"Order not found: {order_id}")

    async def fetch_open_orders(self, symbol: str) -> list[Order]:
        self._validate_symbol(symbol)
        return [
            Order(
                id=o.id, symbol=o.symbol, side=o.side, order_type=o.order_type,
                amount=o.amount, price=o.trigger_price, status="open",
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
                )
                for row in rows
            ]

    async def cancel_order(self, order_id: str, symbol: str) -> None:
        self._validate_symbol(symbol)
        async with self._lock:
            found = any(o.id == order_id for o in self._pending_orders)
            if not found:
                raise ValueError(f"Order not found: {order_id}")
            self._remove_order_by_id(order_id)
            if self._db_engine:
                from sqlalchemy import update
                from src.storage.database import get_session
                from src.storage.models import SimOrder
                async with get_session(self._db_engine) as session:
                    await session.execute(
                        update(SimOrder)
                        .where(SimOrder.order_id == order_id)
                        .where(SimOrder.status == "open")
                        .values(status="cancelled")
                    )
                    await session.commit()
        logger.info(f"Order cancelled: {order_id}")

    # --- Fill callback ---

    def on_fill(self, callback: Callable[[FillEvent], Awaitable[None]]) -> None:
        self._fill_callback = callback

    def drain_pending_fills(self) -> list[FillEvent]:
        fills = self._pending_fills.copy()
        self._pending_fills.clear()
        return fills

    # --- Persistence ---

    async def _init_state(self, initial_balance: float) -> None:
        self._free_usdt = initial_balance
        self._used_usdt = 0.0
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
                ))

        logger.info(
            f"Restored state: balance={self._free_usdt:.2f}/{self._used_usdt:.2f}, "
            f"positions={len(self._positions)}, pending_orders={len(self._pending_orders)}"
        )

    async def _persist_state(
        self,
        new_orders: list[tuple[Order, str]] | None = None,
        filled_order_ids: list[str] | None = None,
        fill_events: list[FillEvent] | None = None,
    ) -> None:
        from sqlalchemy import delete, update
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from src.storage.database import get_session
        from src.storage.models import SimBalance, SimPosition, SimOrder

        now = datetime.now(timezone.utc)

        async with get_session(self._db_engine) as session:
            # 1. Upsert balance
            stmt = sqlite_insert(SimBalance).values(
                session_id=self._session_id,
                free_usdt=self._free_usdt,
                used_usdt=self._used_usdt,
                updated_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["session_id"],
                set_={
                    "free_usdt": stmt.excluded.free_usdt,
                    "used_usdt": stmt.excluded.used_usdt,
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

            # 3b. Cancel orphaned pending orders in DB
            pending_ids = [o.id for o in self._pending_orders]
            filled_ids = filled_order_ids or []
            exclude_ids = pending_ids + filled_ids
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

            # 3d. Upsert pending conditional orders
            for pending in self._pending_orders:
                stmt = sqlite_insert(SimOrder).values(
                    session_id=self._session_id, order_id=pending.id,
                    symbol=pending.symbol, side=pending.side,
                    position_side=pending.position_side,
                    order_type=pending.order_type, amount=pending.amount,
                    trigger_price=pending.trigger_price,
                    status="open", created_at=now,
                )
                stmt = stmt.on_conflict_do_nothing(index_elements=["order_id"])
                await session.execute(stmt)

            await session.commit()

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
        seed_ticker = await self._ccxt.fetch_ticker(self._symbol)
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
