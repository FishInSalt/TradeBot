from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import String, Float, Integer, Text, DateTime, ForeignKey, MetaData, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# Spec §3.3: Alembic naming convention. Permanent constant — never changes.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


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
    # --- R2: Session management fields ---
    exchange_type: Mapped[str] = mapped_column(String(20), default="simulated")
    timeframe: Mapped[str] = mapped_column(String(10), default="15m")
    scheduler_interval_min: Mapped[int] = mapped_column(Integer, default=15)
    approval_enabled: Mapped[bool] = mapped_column(default=True)
    alert_config: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON: {enabled, window, threshold}
    fee_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    token_budget: Mapped[int] = mapped_column(Integer, default=500000)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TradeAction(Base):
    """Agent 的交易操作日志 — append-only 事件模型。"""

    __tablename__ = "trade_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(50), nullable=True)        # Iter 3: §G3 — cycle correlation; nullable per §4.5 (历史数据约束); positioned next to session_id (mirrors ToolCall.cycle_id at line 162)
    action: Mapped[str] = mapped_column(String(30))
    order_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str] = mapped_column(String(50))
    side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    trigger_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class DecisionLog(Base):
    """One agent decision cycle — records what the agent decided and why."""

    __tablename__ = "decision_logs"
    __table_args__ = (
        Index("ix_decision_logs_session_id_cycle_id", "session_id", "cycle_id"),   # Iter 3: §G7 (T3-1 merged)
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"), index=True)
    cycle_id: Mapped[str] = mapped_column(String(50))                              # Unique ID for this decision cycle
    trigger_type: Mapped[str] = mapped_column(String(20))                          # scheduled / conditional / alert
    market_summary: Mapped[str | None] = mapped_column(Text, nullable=True)        # DEPRECATED — see brainstorm §B2 (Python 源码注释，非 SQLAlchemy comment= 参数：SQLite 不支持 column COMMENT 子句，且 comment= 会引入 alembic check noise)
    decision: Mapped[str] = mapped_column(String(20))                              # String(50)→String(20) (spec §B1)
    status: Mapped[str] = mapped_column(String(30), default="ok", server_default="ok")  # 新增 (B1 双字段方案；String(30) per brainstorm 校准；server_default="ok" 与 DB schema 一致避免 alembic check noise，详见 §4.2 Step 4 "为什么保留 server_default")
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
    frozen_usdt: Mapped[float] = mapped_column(Float, default=0.0)
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
    __table_args__ = (Index("ix_sim_orders_session_id_status", "session_id", "status"),)   # rename: was ix_sim_orders_session_status

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
    frozen_margin: Mapped[float] = mapped_column(Float, default=0.0)
    leverage: Mapped[int] = mapped_column(Integer, default=1)


class ToolCall(Base):
    """每次 agent tool 调用一行（观察期埋点）。Append-only，无 UPDATE/DELETE 接口。"""

    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_session_id_tool_name_created_at", "session_id", "tool_name", "created_at"),   # rename: was ix_tool_calls_session_tool_time
        Index("ix_tool_calls_cycle_id", "cycle_id"),                                                        # rename: was ix_tool_calls_cycle
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("sessions.id"))
    # cycle_id: 应用层软关联 DecisionLog.cycle_id（不声明 DB FK —— 时序不允许）
    # NOT NULL: 运行时所有 tool 调用都在 run_agent_cycle 内，cycle_id 必有值
    cycle_id: Mapped[str] = mapped_column(String(50), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10))  # "ok" / "error"
    duration_ms: Mapped[int] = mapped_column(Integer)
    # error_type 存异常类名（非 message / traceback），避免敏感数据泄露
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    args: Mapped[str | None] = mapped_column(Text, nullable=True)                  # Iter 3: §G2 — JSON dict of tool args, 4000 char cap, reasoning key stripped
