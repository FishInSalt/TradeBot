"""WebUI JSON API 响应契约（pydantic v2）。前端 types.ts 由本模块的 OpenAPI 自动生成。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import AfterValidator, BaseModel


def _ensure_utc(v: datetime) -> datetime:
    """所有出站 datetime 归一化为 aware UTC。ORM 在 SQLite 读回 naive（无 tz）、pydantic
    序列化 naive 无 `Z`、aware 有 `Z`；混用会让前端 `new Date()` 对无 Z 串按本地时区解析、
    +0800 用户错位 8h。统一补 UTC → 全部带 Z、前端全按 UTC 解析。"""
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(_ensure_utc)]


class SessionSummary(BaseModel):
    id: str
    name: str
    symbol: str
    status: str               # active / paused（原始字段，非 liveness 断言）
    created_at: UtcDatetime
    last_active_at: UtcDatetime | None
    cycle_count: int
    total_return_pct: float


class SessionDetail(BaseModel):
    id: str
    name: str
    symbol: str
    status: str
    timeframe: str
    scheduler_interval_min: int
    initial_balance: float
    token_budget: int
    created_at: UtcDatetime
    last_active_at: UtcDatetime | None


class CycleRow(BaseModel):
    id: int                   # int PK — 详情跳转/游标用这个
    cycle_label: str          # agent_cycles.cycle_id 字符串，仅显示
    triggered_by: str
    created_at: UtcDatetime
    decision_head: str | None # decision 首段（截断）
    tokens_consumed: int
    wall_time_ms: int | None
    execution_status: str


class ToolCallRow(BaseModel):
    tool_name: str
    status: str
    duration_ms: int
    error_type: str | None
    args: dict | str | None   # 解析后的 JSON；截断 outlier 行解析失败时回退原始 str


class CycleDetail(BaseModel):
    id: int
    cycle_label: str
    triggered_by: str
    created_at: UtcDatetime
    reasoning: str | None
    decision: str | None
    trigger_context: dict | None
    state_snapshot: dict | None
    injected_events: dict | list | None
    tool_calls: list[ToolCallRow]
    tokens_consumed: int
    input_tokens: int | None
    output_tokens: int | None
    cache_hit_rate: float | None
    wall_time_ms: int | None
    llm_call_ms: int | None
    model_id: str | None


class EquityPoint(BaseModel):
    at: UtcDatetime
    equity: float             # 账户盯市净值 state_snapshot.balance.total_usdt


class TradeRow(BaseModel):
    at: UtcDatetime
    action: str
    side: str | None
    price: float | None
    amount: float | None
    pnl: float | None
    fee: float | None


class Performance(BaseModel):
    initial_balance: float
    current_position: str
    total_return_pct: float
    net_pnl: float
    net_win_rate: float
    max_drawdown_pct: float
    net_profit_factor: float | None
    total_trades: int
    net_winning_trades: int
    net_losing_trades: int
    total_fees: float
    equity_curve: list[EquityPoint]    # 盯市，每 cycle
    trades: list[TradeRow]


class PositionInfo(BaseModel):
    symbol: str
    side: str
    contracts: float
    entry_price: float
    leverage: int


class OrderInfo(BaseModel):
    order_id: str
    side: str
    order_type: str
    amount: float
    trigger_price: float | None


class AlertInfo(BaseModel):
    alert_id: str
    target_price: float | None
    registered_at: UtcDatetime
    register_reasoning: str | None


class LiveStatus(BaseModel):
    status: str                       # 会话状态字段（active/paused），非"运行中"断言
    last_active_at: UtcDatetime | None   # 原始戳——让陈旧的 active 自证（spec §5.2）
    position: PositionInfo | None
    open_orders: list[OrderInfo]
    active_alerts: list[AlertInfo]
