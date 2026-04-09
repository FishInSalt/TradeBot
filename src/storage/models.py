from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Text, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    import uuid
    return str(uuid.uuid4())


class Session(Base):
    """A trading session — one AI trader agent instance with its own config and history."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True)                    # User-friendly name (e.g. "BTC trend strategy")
    symbol: Mapped[str] = mapped_column(String(50), default="BTC/USDT:USDT")       # Trading pair for this session
    persona_config: Mapped[str | None] = mapped_column(Text, nullable=True)        # JSON snapshot of PersonaConfig at creation time
    model_config: Mapped[str | None] = mapped_column(Text, nullable=True)          # JSON snapshot of ModelsConfig at creation time
    initial_balance: Mapped[float] = mapped_column(Float, default=100.0)           # Starting capital in USDT (for metrics calculation)
    status: Mapped[str] = mapped_column(String(20), default="active")              # active / paused / stopped
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class TradeRecord(Base):
    """A single trade lifecycle — created on open, updated on close."""

    __tablename__ = "trade_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(50))                                # Trading pair (e.g. BTC/USDT:USDT)
    side: Mapped[str] = mapped_column(String(10))                                  # long / short
    entry_price: Mapped[float] = mapped_column(Float)                              # Price at position open
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)         # Price at position close (null while open)
    quantity: Mapped[float] = mapped_column(Float)                                 # Position size in contracts
    leverage: Mapped[int] = mapped_column(Integer, default=1)                      # Leverage multiplier
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)          # Stop loss price level
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)        # Take profit price level
    status: Mapped[str] = mapped_column(String(20))                                # open / closed / cancelled
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)                # Realized PnL in USDT (set on close)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)       # Agent's reasoning for this trade
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Timestamp when position was closed


class DecisionLog(Base):
    """One agent decision cycle — records what the agent decided and why."""

    __tablename__ = "decision_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(String(50))                              # Unique ID for this decision cycle
    trigger_type: Mapped[str] = mapped_column(String(20))                          # scheduled / conditional
    market_summary: Mapped[str | None] = mapped_column(Text, nullable=True)        # Condensed market state at decision time
    decision: Mapped[str] = mapped_column(String(50))                              # open_long / open_short / close / hold / completed
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)             # Agent's reasoning (truncated to 500 chars)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)     # LLM model ID used for this cycle
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)                   # Total tokens consumed in this cycle
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class MemoryEntry(Base):
    """Agent memory — short-term context or long-term learned knowledge."""

    __tablename__ = "memory_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    memory_type: Mapped[str] = mapped_column(String(20))                           # short_term / long_term
    category: Mapped[str] = mapped_column(String(50))                              # context / trade_review / market_pattern / lesson
    content: Mapped[str] = mapped_column(Text)                                     # The memory content
    relevance_score: Mapped[float] = mapped_column(Float, default=0.5)             # 0-1, higher = more important, used for top-N retrieval
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # Optional TTL for short-term memories


class SimBalance(Base):
    """Simulated exchange balance — one row per session (PK enforces single balance)."""

    __tablename__ = "sim_balances"

    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), primary_key=True)
    free_usdt: Mapped[float] = mapped_column(Float)
    used_usdt: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SimPosition(Base):
    """Simulated open position — unique per (session, symbol) pair."""

    __tablename__ = "sim_positions"
    __table_args__ = (UniqueConstraint("session_id", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str] = mapped_column(String(10))
    contracts: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    leverage: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class SimOrder(Base):
    """Simulated order — one row per submitted order in the simulated exchange."""

    __tablename__ = "sim_orders"
    __table_args__ = (Index("ix_sim_orders_session_status", "session_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"))
    order_id: Mapped[str] = mapped_column(String(36), unique=True)
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str] = mapped_column(String(10))
    position_side: Mapped[str] = mapped_column(String(10))
    order_type: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Float)
    trigger_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    filled_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
